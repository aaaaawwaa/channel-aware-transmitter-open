"""`awg_ddr_pp` 的离线测试: 状态机与 UDP 线协议, 全部不碰硬件。

判据是**与固件语义逐字对齐**: 错误码、状态名、回包格式。硬件那半(DMA 寄存器时序、
切换间隙)由 P5 的上板门禁量, 不在离线范围。
"""
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import awg_ddr_pp as pp  # noqa: E402


def _pkt(ptype, bank=0, session=1, seq=0, offset=0, total=0, payload=b"", crc=0):
    return pp.HEADER.pack(b"ADPP", 1, ptype, bank, session, seq, offset,
                          total, len(payload), crc) + payload


def _echo_pkt(bank=pp.ECHO_BANK, session=0):
    """ECHO 要求 bank=0xFFFF 且其余字段全零(见 `valid_transaction_header`)。"""
    return pp.HEADER.pack(b"ADPP", 1, pp.TYPE_ECHO, bank, session, 0, 0, 0, 0, 0)


def _reply_fields(reply):
    head = pp.HEADER.unpack(reply[:pp.HEADER_SIZE])
    state, error, received, miss_off, miss_len = struct.unpack(
        "!IIIII", reply[pp.HEADER_SIZE:pp.HEADER_SIZE + pp.STATUS_SIZE])
    return head, state, error, received


class FakeMmio:
    def __init__(self):
        self.regs = {}

    def read(self, off):
        return self.regs.get(off, 0)

    def write(self, off, v):
        self.regs[off] = v & 0xFFFFFFFF


class Ram:
    """1 MiB 的假 bank 内存(UDP 载荷落点)。"""

    def __init__(self, size=pp.BANK_BYTES):
        self.buf = bytearray(size)

    def __setitem__(self, key, value):
        self.buf[key] = value

    def __getitem__(self, key):
        return self.buf[key]


def _pp(mode=5, symrate=100000):
    mmio = FakeMmio()
    ram0, ram1 = Ram(), Ram()
    ctl = pp.DdrPp([0x1000, 0x2000], mmio,
                   write_user_ctrl_ch2=lambda w: None,
                   read_user_status_ch1=lambda: 0,
                   digmod_mode_fn=lambda: mode,
                   symrate_fn=lambda: symrate)
    ctl.bank_mem = (ram0, ram1)
    return ctl, (ram0, ram1)


# ---------------- UDP 线协议 ----------------

def test_echo_replies_idle_and_rejects_nonzero_fields():
    ctl, _ = _pp()
    head, state, error, _ = _reply_fields(ctl.udp_feed(_echo_pkt()))
    assert head[2] == pp.TYPE_REPLY and state == 0 and error == pp.UDP_ERR_NONE
    _, _, error, _ = _reply_fields(ctl.udp_feed(_echo_pkt(bank=0)))
    assert error == pp.UDP_ERR_HEADER, "ECHO 的 bank 必须是 0xFFFF"


def test_begin_then_data_then_commit_round_trip():
    ctl, (ram0, _) = _pp(mode=0)
    payload = bytes(range(256)) * 4                     # 1 KiB, 4 字节对齐
    r = ctl.udp_feed(_pkt(pp.TYPE_BEGIN, session=7, total=len(payload),
                          crc=zlib.crc32(payload)))
    _, state, error, _ = _reply_fields(r)
    assert state == 1 and error == pp.UDP_ERR_NONE       # RECEIVING
    for off in range(0, len(payload), 500):
        chunk = payload[off:off + 500]
        r = ctl.udp_feed(_pkt(pp.TYPE_DATA, bank=0, session=7, seq=off // 500 + 1,
                              offset=off, total=len(payload), payload=chunk,
                              crc=zlib.crc32(chunk)))
    _, state, error, received = _reply_fields(r)
    assert state == 1 and received == len(payload)
    r = ctl.udp_commit(0, memory_view=ram0)
    _, state, error, _ = _reply_fields(r)
    assert state == 2 and error == pp.UDP_ERR_NONE       # COMMITTED
    assert ctl.banks[0].valid and ctl.banks[0].fresh
    assert ctl.banks[0].bit_length == len(payload) * 8


def test_data_with_bad_crc_is_rejected_with_crc_error():
    ctl, (ram0, _) = _pp(mode=0)
    ctl.udp_feed(_pkt(pp.TYPE_BEGIN, session=7, total=8, crc=0))
    r = ctl.udp_feed(_pkt(pp.TYPE_DATA, bank=0, session=7, offset=0, total=8,
                          payload=b"\x00" * 8, crc=0x12345678))
    _, _, error, _ = _reply_fields(r)
    assert error == pp.UDP_ERR_CRC


def test_commit_with_missing_bytes_is_incomplete():
    ctl, (ram0, _) = _pp(mode=0)
    ctl.udp_feed(_pkt(pp.TYPE_BEGIN, session=7, total=64, crc=0))
    ctl.udp_feed(_pkt(pp.TYPE_DATA, bank=0, session=7, offset=0, total=64,
                      payload=b"\xAA" * 32, crc=zlib.crc32(b"\xAA" * 32)))
    r = ctl.udp_commit(0, memory_view=ram0)
    _, _, error, received = _reply_fields(r)
    assert error == pp.UDP_ERR_INCOMPLETE and received == 32


def test_abort_clears_the_stage_and_reports_aborted():
    ctl, _ = _pp(mode=0)
    ctl.udp_feed(_pkt(pp.TYPE_BEGIN, session=7, total=64))
    r = ctl.udp_feed(_pkt(pp.TYPE_ABORT, bank=0, session=7, seq=pp.SEQ_ABORT))
    _, state, _, _ = _reply_fields(r)
    assert state == 3                                    # ABORTED
    assert ctl.banks[0].owner == pp.STAGE_NONE


def test_mismatched_session_is_rejected():
    ctl, _ = _pp(mode=0)
    ctl.udp_feed(_pkt(pp.TYPE_BEGIN, session=7, total=64))
    r = ctl.udp_feed(_pkt(pp.TYPE_DATA, bank=0, session=9, offset=0, total=64,
                          payload=b"\x00" * 8, crc=0))
    _, _, error, _ = _reply_fields(r)
    assert error == pp.UDP_ERR_SESSION


# ---------------- 环形喂数: 播放中往非活动 bank 喂数 ----------------

def _running(bank=0, bit_length=4096 * 8):
    """一个正在播放 `bank` 的 DdrPp。"""
    ctl, rams = _pp()
    for i in (0, 1):
        ctl.banks[i].valid = ctl.banks[i].fresh = 1
        ctl.banks[i].bit_length = bit_length
    assert ctl.start(bank)
    assert ctl.state == "RUNNING" and ctl.active_bank == bank
    return ctl, rams


def test_replies_echo_the_request_header_fields():
    """回包必须回显请求的 bank/session/sequence/total/crc。

    主机侧客户端按 (session, bank, sequence) 过滤回包(见 host/siggen/udp_ddr_pp.py
    的 `_recv_reply`), 全填 0 的话它**一个回包都匹配不上**, 每个事务都要等满重传次数。
    离线测试只读 state/error/received, 所以这条在假 socket 上测不出来 —— 只能直接断言头。
    """
    ctl, _ = _pp(mode=0)
    total = 256
    begin = _pkt(pp.TYPE_BEGIN, bank=1, session=0xDEADBEEF, total=total, crc=0x12345678)
    reply = ctl.udp_feed(begin)
    head = pp.HEADER.unpack(reply[:pp.HEADER_SIZE])
    assert head[2] == pp.TYPE_REPLY
    assert head[3] == 1, "回包的 bank 必须是请求的 bank"
    assert head[4] == 0xDEADBEEF, "回包的 session 必须是请求的 session"
    assert head[7] == total, "回包的 total_bytes 要回显"
    assert head[9] == 0x12345678, "回包的 crc32 要回显"
    # 每个阶段的回包都带各自的 sequence(STATUS/COMMIT 用固定哨兵值)
    data = _pkt(pp.TYPE_DATA, bank=1, session=0xDEADBEEF, seq=5, offset=0,
                total=total, payload=b"\xAA" * 8, crc=zlib.crc32(b"\xAA" * 8))
    head = pp.HEADER.unpack(ctl.udp_feed(data)[:pp.HEADER_SIZE])
    assert head[4] == 0xDEADBEEF and head[5] == 5, "DATA 的回包要带原 sequence"
    status = _pkt(pp.TYPE_STATUS, bank=1, session=0xDEADBEEF,
                  seq=pp.SEQ_STATUS, total=total)
    head = pp.HEADER.unpack(ctl.udp_feed(status)[:pp.HEADER_SIZE])
    assert head[5] == pp.SEQ_STATUS


def test_begin_is_allowed_on_the_inactive_bank_while_playing():
    """环形喂数的**全部前提**: 播放中往非活动 bank 喂数必须被接受。

    固件 main.c:1774-1785 的门禁是"播放中只拒活动 bank"。老代码写的是 `or self.active`
    —— 播放中一律拒, 于是环形永远起不来(而所有老测试都在停播状态下喂, 测不出)。
    """
    ctl, _ = _running(bank=0)
    r = ctl.udp_feed(_pkt(pp.TYPE_BEGIN, bank=1, session=7, total=64))
    _, state, error, _ = _reply_fields(r)
    assert error == pp.UDP_ERR_NONE, "播放中往非活动 bank 喂数被拒了, 环形起不来"
    assert state == 1                                    # RECEIVING
    assert ctl.banks[1].owner == pp.STAGE_UDP
    assert ctl.state == "RUNNING", "喂数不该打断播放"


def test_begin_on_the_active_bank_while_playing_is_busy():
    """活动 bank 正被 DMA 读, 喂它会当场改写正在播放的内容 —— 必须报 BUSY。"""
    ctl, _ = _running(bank=1)
    r = ctl.udp_feed(_pkt(pp.TYPE_BEGIN, bank=1, session=7, total=64))
    _, _, error, _ = _reply_fields(r)
    assert error == pp.UDP_ERR_BUSY
    assert ctl.banks[1].owner == pp.STAGE_NONE


def test_begin_while_stopped_still_requires_digmod_off():
    """停播那一半照固件: 数据源还在 AWG 手里时必须 BUSY(不能偷偷抢走 BRAM 路径)。"""
    ctl, _ = _pp(mode=5)                                 # 数字调制开着, 未播放
    r = ctl.udp_feed(_pkt(pp.TYPE_BEGIN, bank=0, session=7, total=64))
    _, _, error, _ = _reply_fields(r)
    assert error == pp.UDP_ERR_BUSY


def test_begin_clears_fresh_so_a_half_written_bank_is_never_switched_to():
    """BEGIN 必须清 `fresh`。

    否则 DMA 排空当前 bank 时, 状态机看到"还是 fresh、其实正在被重写"的 bank 会立刻切过去
    读半成品。固件 `ddr_pp_udp_begin` 同样在开头清 fresh(main.c:1801)。
    """
    ctl, _ = _running(bank=0)
    assert ctl.banks[1].fresh == 1
    ctl.udp_feed(_pkt(pp.TYPE_BEGIN, bank=1, session=7, total=64))
    assert ctl.banks[1].fresh == 0, "BEGIN 没清 fresh —— 会切到写了一半的 bank"


def test_a_ring_commit_makes_the_bank_switchable_again():
    """环形一个完整回合: 播放中喂非活动 bank → COMMIT → 下一拍自动切过去。"""
    ctl, rams = _running(bank=0)
    payload = bytes(range(256)) * 4
    ctl.udp_feed(_pkt(pp.TYPE_BEGIN, bank=1, session=7, total=len(payload),
                      crc=zlib.crc32(payload)))
    for off in range(0, len(payload), 500):
        chunk = payload[off:off + 500]
        ctl.udp_feed(_pkt(pp.TYPE_DATA, bank=1, session=7, seq=off // 500 + 1,
                          offset=off, total=len(payload), payload=chunk,
                          crc=zlib.crc32(chunk)))
    reply = ctl.udp_commit(1, memory_view=rams[1])
    _, state, error, _ = _reply_fields(reply)
    assert state == 2 and error == pp.UDP_ERR_NONE       # COMMITTED
    assert ctl.banks[1].fresh == 1
    assert ctl.banks[1].bit_length == len(payload) * 8
    # 当前 bank 排空 -> 自动切到刚喂好的 bank1
    ctl.dma.mmio.regs[pp.DMA_SR] = pp.DMA_IRQ_IOC_MASK | pp.DMA_SR_IDLE
    assert ctl.service()[0] == "ioc"
    ctl._read_status = lambda: pp.USER_STATUS_STREAM_DONE
    event, active = ctl.service()
    assert event == "switched" and ctl.active_bank == 1
    assert ctl.banks[1].fresh == 0                       # 切过去就消费掉了


def test_a_slow_refill_blocks_the_ring_instead_of_playing_garbage():
    """喂数太慢时另一 bank 还是 STAGE_UDP 且未 COMMIT —— 必须停成 RING_BLOCKED(26),
    不能切过去读半成品。

    固件判定顺序是 `fresh` 在前、`STAGE_UDP` 在后(main.c:1668-1686): fresh 置起的
    bank 一律切; fresh 未置起而 owner 是 UDP, 说明"正在收但没喂完", 才报 26。
    BEGIN 清 fresh(见上)正好造出这个状态 —— 这一条就是那个修复的回归网。
    """
    ctl, _ = _running(bank=0)
    ctl.udp_feed(_pkt(pp.TYPE_BEGIN, bank=1, session=7, total=64))   # 开了但没喂完
    assert ctl.banks[1].fresh == 0 and ctl.banks[1].owner == pp.STAGE_UDP
    ctl.dma.mmio.regs[pp.DMA_SR] = pp.DMA_IRQ_IOC_MASK | pp.DMA_SR_IDLE
    ctl.service()
    ctl._read_status = lambda: pp.USER_STATUS_STREAM_DONE
    event, active = ctl.service()
    assert event == "error" and not active
    assert ctl.error == pp.PP_RING_BLOCKED
    assert "ERROR 26" in ctl.status_line()


def test_play_blocks_udp_begin_when_stopped():
    """`play_blocks_config()` 占着配置时停播也不能喂(固件 main.c:1781)。"""
    ctl = pp.DdrPp([0, 0], FakeMmio(), lambda w: None, lambda: 0,
                   digmod_mode_fn=lambda: 0, play_blocks_fn=lambda: True)
    r = ctl.udp_feed(_pkt(pp.TYPE_BEGIN, bank=0, session=7, total=64))
    _, _, error, _ = _reply_fields(r)
    assert error == pp.UDP_ERR_BUSY


# ---------------- UART 侧动词 ----------------

def test_uart_commit_requires_uart_owner():
    ctl, _ = _pp()
    assert ctl.commit(0, 8 * 32, 0) == "ERR DDR PP COMMIT BUSY"


def test_start_requires_digmod_mode_and_valid_bank():
    ctl, _ = _pp()
    ctl_off, _ = _pp(mode=0)                              # 数字调制关着
    assert not ctl_off.start()
    assert ctl_off.error == pp.PP_START_FAILED
    ctl.banks[0].valid = 1
    ctl.banks[0].bit_length = 4096 * 8
    assert ctl.start()
    assert ctl.state == "RUNNING" and ctl.active_bank == 0


def test_status_line_matches_the_firmware_format():
    ctl, _ = _pp()
    line = ctl.status_line()
    assert line.startswith("DDR PP ACTIVE 0 ACTIVE_BANK NONE NEXT_BANK NONE STATE IDLE ERROR 00")
    assert "BANK0 VALID 0 LENGTH 0 CRC 0000" in line
    assert "BANK1 VALID 0 LENGTH 0 CRC 0000" in line


def test_service_detects_ioc_then_drain_then_switch():
    """RUNNING --(IOC)--> DRAIN --(stream_done + fresh other)--> 切到另一 bank。"""
    ctl, _ = _pp()
    ctl.banks[0].valid = ctl.banks[0].fresh = 1
    ctl.banks[0].bit_length = 4096 * 8
    ctl.banks[1].valid = ctl.banks[1].fresh = 1
    ctl.banks[1].bit_length = 4096 * 8
    assert ctl.start(0)
    # IOC: SR 里 IOC 位置起, 且通道空闲
    ctl.dma.mmio.regs[pp.DMA_SR] = pp.DMA_IRQ_IOC_MASK | pp.DMA_SR_IDLE
    event, active = ctl.service()
    assert event == "ioc" and ctl.state == "DRAIN" and active
    # 另一 bank fresh -> 自动切换
    ctl._read_status = lambda: pp.USER_STATUS_STREAM_DONE     # stream_done 置起
    event, active = ctl.service()
    assert event == "switched" and ctl.active_bank == 1, (event, ctl.status_line())
    assert ctl.banks[0].fresh == 0 and ctl.banks[1].fresh == 0
    assert "NEXT_SUBMIT_S" in ctl.timeline


def test_service_reports_underflow_as_error_25():
    ctl, _ = _pp()
    ctl.banks[0].valid = ctl.banks[0].fresh = 1
    ctl.banks[0].bit_length = 4096 * 8
    assert ctl.start(0)
    ctl._read_status = lambda: pp.USER_STATUS_UNDERFLOW
    event, active = ctl.service()
    assert event == "error" and not active
    assert ctl.error == pp.PP_UNDERFLOW and ctl.state == "ERROR"
    assert "ERROR 25" in ctl.status_line()


def test_dma_error_is_error_23():
    ctl, _ = _pp()
    ctl.banks[0].valid = ctl.banks[0].fresh = 1
    ctl.banks[0].bit_length = 4096 * 8
    assert ctl.start(0)
    ctl.dma.mmio.regs[pp.DMA_SR] = pp.DMA_ERR_ALL_MASK
    ctl.service()
    assert ctl.error == pp.PP_DMA_ERROR


def test_stream_control_word_carries_a_full_megabyte():
    """1 MiB 的长度**不能**被掩码吃掉 —— 曾经的 `& 0xFFFFF` 正好把 0x100000 掩成 0,
    于是 PL 收到的长度是 0, DMA 永远排不完(板上表现为 ERROR 24)。

    这个 bug 在 4 KiB 的 buffer 上不会暴露, 所以必须专门拿满 bank 尺寸来钉。
    """
    words = []
    ctl = pp.DdrPp([0, 0], FakeMmio(), lambda w: words.append(w), lambda: 0)
    ctl._stream_ctrl(1, pp.BANK_BYTES, 0)
    word = words[-1]
    assert word & 0xFFFFFFF8 == (pp.BANK_BYTES << 3), hex(word)
    assert word & pp.PP_CTRL_ENABLE, "enable 位要置起"
    assert not word & pp.PP_CTRL_BANK, "bank0 不该带 BANK 位"
    ctl._stream_ctrl(1, pp.BANK_BYTES, 1)
    assert words[-1] & pp.PP_CTRL_BANK, "bank1 要带 BANK 位"


def test_start_writes_only_source_and_bank_to_ch1():
    """ch1 的 length 字段属于 **BRAM USERBUF** 那一路, PP 的长度只走 ch2。

    把 1 MiB 塞进 ch1 会让 `awg_top` 的 user_length 变成 1 MiB(BRAM 路径的量程),
    消费链按错误的长度节流 —— 板上实测: DMA 30 s 不动、user_status 恒 0x42。
    """
    ch1_writes, ch2_writes = [], []
    ctl = pp.DdrPp([0x1000, 0x2000], FakeMmio(),
                   write_user_ctrl_ch2=lambda w: ch2_writes.append(w),
                   read_user_status_ch1=lambda: 0,
                   digmod_mode_fn=lambda: 5)
    ctl._write_user_ctrl_ch1 = lambda w: ch1_writes.append(w)
    ctl.banks[0].valid = ctl.banks[0].fresh = 1
    ctl.banks[0].bit_length = pp.BANK_BYTES * 8
    assert ctl.start(0)
    assert ch1_writes, "必须写 ch1(SOURCE)"
    assert ch1_writes[-1] & 1, "SOURCE 位要置起"
    assert not (ch1_writes[-1] >> 3) & 0x1FF, "ch1 的 length 字段必须保持 0"
    assert ch2_writes[-1] & 0xFFFFFFF8 == (pp.BANK_BYTES << 3), "ch2 才带 PP 长度"


def test_start_prefers_the_awgctl_owned_ch1_writer():
    """`start()` 必须**优先**走 `_write_user_ctrl_for_pp`(`AwgCtl` 的方法)。

    裸 GPIO 注入只置硬件位; `AwgCtl` 那条还会同步影子 `digmod.source` —— 播放中任何一条
    `SET` 都会按影子重写 ch1, 影子不对就会把 USER 源悄悄关掉(板上表现: 播放中突然 ERROR 24)。
    """
    calls = []
    ctl = pp.DdrPp([0x1000, 0x2000], FakeMmio(),
                   write_user_ctrl_ch2=lambda w: None, read_user_status_ch1=lambda: 0,
                   digmod_mode_fn=lambda: 5)
    ctl._write_user_ctrl_ch1 = lambda w: calls.append(("raw", w))
    ctl._write_user_ctrl_for_pp = lambda bank: calls.append(("ctl", bank))
    ctl.banks[0].valid = ctl.banks[0].fresh = 1
    ctl.banks[0].bit_length = 4096 * 8
    assert ctl.start(0)
    assert calls == [("ctl", 0)], "必须走 AwgCtl 那条, 而不是裸 GPIO"


def test_feed_file_latches_a_local_file_into_the_bank():
    """本地文件直喂(板端自洽路径): 拷进 bank + 锁存, 语义与 `udp_commit` 逐字相同。"""
    ctl, (ram0, _) = _pp(mode=0)
    data = bytes(range(256)) * 4                          # 1 KiB, 4 字节对齐
    ok, reason = ctl.feed_file(0, data, memory_view=ram0)
    assert ok and reason == "OK"
    b = ctl.banks[0]
    assert b.valid == 1 and b.fresh == 1 and b.owner == pp.STAGE_NONE
    assert b.bit_length == len(data) * 8
    assert bytes(ram0[0:len(data)]) == data
    assert b.payload_crc32 == zlib.crc32(data) & 0xFFFFFFFF


def test_feed_file_refuses_the_active_bank():
    """活动 bank 正被 DMA 读 —— 喂它就是当场改写正在播的内容。"""
    ctl, _ = _running(bank=0)
    ok, reason = ctl.feed_file(0, b"\x00" * 64)
    assert not ok and reason == "BUSY"
    assert ctl.banks[0].bit_length == 4096 * 8, "被拒时不该动长度"


def test_feed_file_is_allowed_on_the_inactive_bank_while_playing():
    """环形的全部前提: 播放中往非活动 bank 喂本地文件必须被接受。"""
    ctl, rams = _running(bank=0)
    ok, reason = ctl.feed_file(1, b"\xAA" * 64, memory_view=rams[1])
    assert ok and reason == "OK"
    assert ctl.banks[1].fresh == 1 and ctl.state == "RUNNING", "喂数不该打断播放"


def test_feed_file_requires_digmod_off_when_stopped():
    """停播时照固件: 数据源还在 AWG 手里就必须 BUSY。"""
    ctl, _ = _pp(mode=5)
    ok, reason = ctl.feed_file(0, b"\x00" * 64)
    assert not ok and reason == "BUSY"


def test_feed_file_rejects_bad_lengths_and_alignment():
    """4 字节对齐是硬要求(上传字节被当作符号比特, `bit_length % 32 == 0`)。"""
    ctl, _ = _pp(mode=0)
    assert ctl.feed_file(0, b"")[0] is False
    assert ctl.feed_file(0, b"\x00" * 6)[1] == "RANGE", "非 4 字节对齐"
    assert ctl.feed_file(0, b"\x00" * (pp.BANK_BYTES + 4))[1] == "RANGE", "超过一个 bank"
    assert ctl.feed_file(2, b"\x00" * 64)[1] == "BAD BANK"


def test_feed_file_clears_fresh_before_latching_so_a_half_write_is_never_switched_to():
    """与 BEGIN 同款: 先清 fresh, 锁存时再置起 —— 否则状态机会切到写了一半的 bank。"""
    ctl, rams = _running(bank=0)
    ctl.banks[1].fresh = 1                                # 假装它"就绪"
    ok, _ = ctl.feed_file(1, b"\x11" * 64, memory_view=rams[1])
    assert ok and ctl.banks[1].fresh == 1, "锁存后必须是 fresh(可被切)"
    assert ctl.banks[1].owner == pp.STAGE_NONE


def test_latch_bank_and_udp_commit_produce_the_same_state():
    """两条入口(本地文件 / UDP 分包)的锁存结果必须逐字段相同。"""
    data = bytes(range(256)) * 2
    a, (ram_a, _) = _pp(mode=0)
    b, (ram_b, _) = _pp(mode=0)
    a.feed_file(0, data, memory_view=ram_a)
    b.udp_feed(_pkt(pp.TYPE_BEGIN, session=7, total=len(data), crc=zlib.crc32(data)))
    for off in range(0, len(data), 500):
        chunk = data[off:off + 500]
        b.udp_feed(_pkt(pp.TYPE_DATA, bank=0, session=7, seq=off // 500 + 1,
                        offset=off, total=len(data), payload=chunk,
                        crc=zlib.crc32(chunk)))
    b.udp_commit(0, memory_view=ram_b)
    for attr in ("valid", "fresh", "bit_length", "owner"):
        assert getattr(a.banks[0], attr) == getattr(b.banks[0], attr), attr


def test_crc16_matches_the_firmware_algorithm():
    assert pp.crc16_ccitt(b"123456789") == 0x29B1       # CCITT-FALSE 标准校验值
    assert pp.crc32_ieee(b"123456789") == zlib.crc32(b"123456789") & 0xFFFFFFFF


def test_timeout_scales_with_payload_and_symrate():
    ctl = _pp(mode=5, symrate=100000)[0]
    # 1 MiB, QAM64(6 bit/符号): 8*1048576/6 ≈ 1398101 符号 / 100k ≈ 14 s + 2 s 余量
    t = ctl._timeout_for(1048576)
    assert 15.5 < t < 16.5, t
    ctl2 = _pp(mode=1, symrate=1000000)[0]              # BPSK, 1 Mbaud
    t2 = ctl2._timeout_for(4096)
    assert t2 < 0.1 + 2.0 + 0.1


# ---------------- PL 侧回读: 硬件实际读的是哪块 bank ----------------

def test_pl_view_decodes_the_bank_from_the_dma_source_address():
    """`pl_view` 必须把 DMA 里被 arm 的源地址解回 bank 号。

    这是"真切换"判据的核心: `active_bank` 是软件账面, 而 `DMA_SRCADDR` 是硬件实际
    会去读的地址。两者一致才说明输出内容真的换了。
    """
    ctl, _ = _pp()
    ctl.dma.mmio.regs[pp.DMA_SRCADDR] = 0x2000
    assert ctl.pl_view()["bank"] == 1
    ctl.dma.mmio.regs[pp.DMA_SRCADDR] = 0x1000
    assert ctl.pl_view()["bank"] == 0


def test_pl_view_reports_none_for_an_address_that_is_not_a_bank():
    """地址对不上任何一个 bank 时必须回 None, **不能**猜一个近似的。

    若 `submit()` 的写没生效, 寄存器里会是上一块的地址或 0 —— 那种情况下"解不出 bank"
    正是我们要的信号, 猜一个出来就把这个信号抹掉了。
    """
    ctl, _ = _pp()
    ctl.dma.mmio.regs[pp.DMA_SRCADDR] = 0xDEADBEEF
    view = ctl.pl_view()
    assert view["src_addr"] == 0xDEADBEEF and view["bank"] is None
    ctl.dma.mmio.regs[pp.DMA_SRCADDR] = 0
    assert ctl.pl_view()["bank"] is None


def test_start_arms_the_dma_at_the_banks_physical_address():
    """`start(bank)` 之后, 硬件里被 arm 的源地址必须就是**那块 bank** 的物理地址。

    这条把 `start()` 的 submit 与 `pl_view()` 的解码对起来 —— 中间任一环写错(掩码、
    取反、写错寄存器)都会让两者对不上, 而 underflow 照样干净。
    """
    ctl, _ = _pp()
    for i in (0, 1):
        ctl.banks[i].valid = ctl.banks[i].fresh = 1
        ctl.banks[i].bit_length = 4096 * 8
    assert ctl.start(1)
    view = ctl.pl_view()
    assert view["src_addr"] == 0x2000, hex(view["src_addr"])
    assert view["bank"] == 1


def test_pl_view_catches_a_source_address_write_that_never_took():
    """**判据不是空的**: 若 `SRCADDR` 的写没生效(BD 修订落后 / 外设被按在复位里 ——
    本项目两样都出过), 硬件会照旧指着上一块 bank, 而 `active_bank` 照样更新、
    `underflow` 照样干净。`pl_view()` 必须把这个分歧报出来。

    没有这条, `p5b_ringtest` 的 PL 侧判据只是一句永远不会失败的话。
    """
    ctl, _ = _pp()
    for i in (0, 1):
        ctl.banks[i].valid = ctl.banks[i].fresh = 1
        ctl.banks[i].bit_length = 4096 * 8
    assert ctl.start(0)                                  # 硬件先指着 bank0
    ctl.active, ctl.active_bank, ctl.state = 0, 0xFF, "IDLE"

    real_wr = ctl.dma._wr
    def swallow_srcaddr(off, value):
        if off == pp.DMA_SRCADDR:
            return                                       # 写被吞: 寄存器保持旧值
        return real_wr(off, value)
    ctl.dma._wr = swallow_srcaddr

    assert ctl.start(1)
    assert ctl.active_bank == 1, "账面以为切到了 bank1"
    view = ctl.pl_view()
    assert view["bank"] == 0, "硬件其实还指着 bank0 —— 判据必须看见这个分歧"
    assert view["src_addr"] == 0x1000
