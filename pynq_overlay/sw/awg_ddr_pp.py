#!/usr/bin/env python3
"""DDR 双 bank 数据面在 Linux 上的重建(P5)。

裸机固件里这块由 lwIP raw-GEM 收 UDP、`ddr_pp_service()` 在主循环里轮询 DMA 完成、
DRAIN 后自动重臂另一 bank —— 那是**硬实时**的(实测切换间隙 3.15 ms, 由 16 KB AXIS FIFO
吸收)。Linux 用户态给不出那种确定性, 所以移植时把职责重新切了一刀:

  * **环形重装交给 DMA 的直接寄存器操作**, 但**换一种更省的方式收 UDP**: 同一
    `ADPP` 线格式、同一 32 字节头、同一 CRC —— 主机侧 `host/siggen/udp_ddr_pp.py`
    **一行不用改**。
  * **时间线/超时不再靠 XTime tick**(667 MHz 全局定时器在 Linux 用户态读不到),
    用 `time.monotonic_ns()` 换算; 超时语义与固件一致(播放时长 + 2 s 余量)。

与固件的**逐字对应关系**(移植判据, 别"顺手改"):
  * 32B 头: `!4sBBHIIIIII` 大端, magic 'ADPP', ver 1, type/bank/session/sequence/
    offset/total/payload_len/crc32 —— 见 `sdk/src/udp_ddr_pp.c:500-506`。
  * 回复: 头 type=0x80 + 20B 状态 {state,error,received,first_missing_offset,
    first_missing_length} —— `send_reply :153-183`。
  * 类型: ECHO=1 BEGIN=2 DATA=3 STATUS=4 COMMIT=5 ABORT=6 —— `udp_ddr_pp.h:14-24`。
  * UART 动词: `SET DDR PP CLEAR|CHUNK|COMMIT|START|STOP`, 回包与错误码(0x20..0x26)
    见 `main.c:2158-2263` 与 `ddr_pp_service`。
  * DMA: simple-mode MM2S, 寄存器偏移照抄 `xaxidma_hw.h`(CR=0x00 SR=0x04
    RESET=0x04 位, SRCADDR=0x18, BUFFLEN=0x28; IOC=SR.b12, ERR=0x770)。
  * 流控制: `dma_pp_stream_ctrl` 写 axi_gpio_7 **通道2**: enable|bank<<1|mode<<2|
    length<<3 —— `main.c:1021-1034`。

**内存策略(与裸机的根本差异, 见 BASELINE 与技能里的前科)**: 固件用固定物理地址
(DDR+0x100000+0x01000000/0x01200000), 那在 Linux 下会被页分配器拿去用。这里改用
`pynq.allocate()`(CMA)拿连续物理内存, 两个 bank 各 1 MiB —— 地址不再固定, 但 bank 语义
(交替/新鲜位/owner)完全保留。
"""
import socket
import struct
import time
import zlib

# ---- ADPP 线格式(与固件逐字一致) ----
HEADER = struct.Struct("!4sBBHIIIIII")          # magic, ver, type, bank, session,
                                                 # sequence, offset, total, len, crc32
HEADER_SIZE = 32
STATUS_SIZE = 20
MAX_PAYLOAD = 1400
BANK_BYTES = 0x00100000                          # 1 MiB per bank
SESSION_TIMEOUT_S = 5.0

TYPE_ECHO, TYPE_BEGIN, TYPE_DATA, TYPE_STATUS, TYPE_COMMIT, TYPE_ABORT = 1, 2, 3, 4, 5, 6
TYPE_REPLY = 0x80
ECHO_BANK = 0xFFFF
SEQ_STATUS, SEQ_COMMIT, SEQ_ABORT = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD

# udp_ddr_pp.h:14-24
UDP_STATE = ("IDLE", "RECEIVING", "COMMITTED", "ABORTED", "REJECTED")
UDP_ERR_NONE, UDP_ERR_HEADER, UDP_ERR_SESSION, UDP_ERR_BUSY, UDP_ERR_RANGE = 0, 1, 2, 3, 4
UDP_ERR_CRC, UDP_ERR_INCOMPLETE, UDP_ERR_TIMEOUT, UDP_ERR_CONFLICT = 5, 6, 7, 8

# main.c 的 PP 错误码(GET DDR PP 的 ERROR %02X)
PP_START_FAILED, PP_RESET_FAILED, PP_SUBMIT_FAILED = 0x20, 0x21, 0x22
PP_DMA_ERROR, PP_TIMEOUT, PP_UNDERFLOW, PP_RING_BLOCKED = 0x23, 0x24, 0x25, 0x26

# xaxidma_hw.h (AXI DMA v7.1, simple mode MM2S)
DMA_TX = 0x00
DMA_CR, DMA_SR, DMA_SRCADDR, DMA_BUFFLEN = DMA_TX + 0x00, DMA_TX + 0x04, DMA_TX + 0x18, DMA_TX + 0x28
DMA_CR_RESET, DMA_CR_RUNSTOP = 1 << 2, 1 << 0
DMA_SR_HALTED, DMA_SR_IDLE = 1 << 0, 1 << 1
DMA_ERR_ALL_MASK, DMA_IRQ_IOC_MASK = 0x770, 1 << 12

# main.c:209-225 的位定义
USER_STATUS_VALID, USER_STATUS_DONE = 1 << 0, 1 << 1
USER_STATUS_UNDERFLOW, USER_STATUS_READY = 1 << 2, 1 << 3
USER_STATUS_BANK, USER_STATUS_STREAM_DONE = 1 << 4, 1 << 6
PP_CTRL_ENABLE, PP_CTRL_BANK, PP_CTRL_MODE = 1 << 0, 1 << 1, 1 << 2

STAGE_NONE, STAGE_UART, STAGE_UDP = 0, 1, 2


def crc32_ieee(data):
    """`zlib.crc32` 即固件的 IEEE CRC-32(反射 0xEDB88320), 与 host 侧一致。"""
    return zlib.crc32(data) & 0xFFFFFFFF


def crc16_ccitt(data):
    """`main.c:140` 的 CCITT(0x1021, 初值 0xFFFF)。COMMIT 校验用。"""
    crc = 0xFFFF
    for byte in bytearray(data):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


class DmaEngine:
    """AXI DMA simple-mode MM2S 的直接寄存器驱动(照抄 XAxiDma 的裸寄存器序列)。

    `XAxiDma_Reset` = 写 CR.RESET 后等 SR.RESET 清零(固件 `dma_wait_reset` 的
    100 万次循环在 Python 里换算成超时秒数)。
    """

    def __init__(self, mmio, reset_timeout=1.0):
        self.mmio = mmio
        self.reset_timeout = reset_timeout

    def _rd(self, off):
        return self.mmio.read(off)

    def _wr(self, off, value):
        self.mmio.write(off, value & 0xFFFFFFFF)

    def reset(self):
        """`XAxiDma_Reset` + simple 模式的通道启动: 复位完成后**置 CR.RUNSTOP**。

        ⚠ 漏了它 DMA 就永远 halted(实测 SR=0x1, BUFFLEN 写了也不动, 状态机只能按
        超时判死): 固件里这一步藏在 `XAxiDma_CfgInitialize`(simple 分支写
        CR|=RUNSTOP, axidma_v9_18/xaxidma.c:425-444), 而 `SimpleTransfer` 只写
        SRCADDR+BUFFLEN —— "照抄 SimpleTransfer"不够, CfgInitialize 的这步也要搬。
        """
        self._wr(DMA_CR, DMA_CR_RESET)
        end = time.monotonic() + self.reset_timeout
        while time.monotonic() < end:
            if not (self._rd(DMA_SR) & DMA_CR_RESET):
                self._wr(DMA_CR, self._rd(DMA_CR) | DMA_CR_RUNSTOP)
                return True
            time.sleep(0.0005)
        return False

    def busy(self):
        return not (self._rd(DMA_SR) & DMA_SR_IDLE)

    def submit(self, phys_addr, length):
        """`XAxiDma_SimpleTransfer` 的寄存器序列: 源地址 + 长度。"""
        self._wr(DMA_SRCADDR, phys_addr)
        self._wr(DMA_BUFFLEN, length)

    def status(self):
        return self._rd(DMA_SR)


class BankState(object):
    """一个 bank 的暂存状态(`main.c:381-421` 的 Python 镜像)。"""

    def __init__(self):
        self.owner = STAGE_NONE
        self.open = 0
        self.seen_words = set()      # 位图换集合(离线可测, 语义同 contig/hwm)
        self.stage_bytes = 0
        self.expected_bytes = 0
        self.received_bytes = 0
        self.payload_crc32 = 0
        self.bit_length = 0
        self.crc16 = 0
        self.valid = 0
        self.fresh = 0

    def clear_stage(self):
        self.owner = STAGE_NONE
        self.open = 0
        self.seen_words = set()
        self.stage_bytes = 0
        self.expected_bytes = 0
        self.received_bytes = 0
        self.payload_crc32 = 0


class DdrPp:
    """双 bank 环形播放 + UART/UDP 两个暂存入口, 状态机与 `main.c` 对齐。

    构造参数 `ddr_phys(0|1)` 给两个 bank 的**物理地址**(由 CMA 分配后传入);
    `mmio_dma`/`gpio_user_ctrl`/`gpio_user_status` 是 PL 侧访问对象。
    `digmod_mode_fn` 回调在 start 时查数字调制模式(0x20 的前置条件之一)。
    """

    def __init__(self, ddr_phys, mmio_dma, write_user_ctrl_ch2, read_user_status_ch1,
                 digmod_mode_fn=lambda: 0, symrate_fn=lambda: 10000, clock=time.monotonic,
                 play_blocks_fn=lambda: False):
        self.bank_phys = tuple(ddr_phys)
        self.bank_mem = (None, None)     # 每个 bank 的内存视图(UDP 落盘用)
        self.dma = DmaEngine(mmio_dma)
        self._write_stream = write_user_ctrl_ch2
        self._read_status = read_user_status_ch1
        self._digmod_mode = digmod_mode_fn
        self._symrate = symrate_fn
        # `play_blocks_config()` 的板端镜像: PLAY 命令占住配置时, UDP BEGIN 必须报 BUSY
        # (固件 main.c:1781)。默认 False —— 面板/环形喂数这条路径不碰 PLAY。
        self._play_blocks = play_blocks_fn
        self._write_user_ctrl_ch1 = None   # axi_gpio_7 通道1 写接口(user 控制字)
        # 板端优先用这个(见 `start()`): `AwgCtl` 的方法, 带锁且同步影子 source。
        self._write_user_ctrl_for_pp = None
        self.clock = clock
        self.banks = [BankState(), BankState()]
        self.active = 0
        self.active_bank = 0xFF
        self.state = "IDLE"
        self.error = 0
        self.started_at = 0.0
        self.timeout_s = 0.0
        self.ioc_at = 0.0
        self.drain_at = 0.0
        self.timeline = {}          # 观测值(NEXT_SUBMIT_S 等), 供诊断
        self._udp_session = {}      # bank -> session id
        self._socket = None

    # ---- 底层小工具 ----

    def _bank_name(self, bank):
        return "NONE" if bank > 1 else str(bank)

    def _stream_ctrl(self, enable, length, bank):
        """`dma_pp_stream_ctrl` 的逐位镜像(main.c:1021-1034)。

        ⚠ **length 不要加掩码**。固件是 `length_bytes << 3` 直接左移, 我一开始多加了
        `& 0xFFFFF`(20 位) —— 而 **1 MiB = 0x100000 正好被掩成 0**, PL 收到的长度是 0,
        消费链永远排不完(DMA 一直 busy, 状态机按超时判死 ERROR 24)。这个 bug 在
        4 KiB 的 buffer 上**不会暴露**(0x1000 在 20 位内), 所以"小 buffer 能过"并不能
        排除它 —— 板上是靠"读回每个寄存器都与裸机成功时逐位一致、却仍不排空"才收敛到
        length 这一个字段的。
        """
        word = (PP_CTRL_MODE | ((length << 3) & 0xFFFFFFFF)
                | (PP_CTRL_BANK if bank else 0)
                | (PP_CTRL_ENABLE if enable else 0))
        self._write_stream(word)

    def _user_status(self):
        raw = self._read_status()
        return {
            "valid": bool(raw & USER_STATUS_VALID),
            "done": bool(raw & USER_STATUS_DONE),
            "underflow": bool(raw & USER_STATUS_UNDERFLOW),
            "ready": bool(raw & USER_STATUS_READY),
            "bank": bool(raw & USER_STATUS_BANK),
            "stream_done": bool(raw & USER_STATUS_STREAM_DONE),
        }

    def pl_view(self):
        """**从 PL 侧**读当前消费的是什么 —— 与 `self.active_bank`(软件账面)对照用。

        为什么需要它: `active_bank` 是软件自己记的, 它只反映"我们以为切到哪了"。真正
        决定输出内容的是两个硬件状态:
          * `DMA_SRCADDR` —— DMA 通道里**实际被 arm 的源地址**。若 `submit()` 的写没生效
            (BD 修订落后、外设被按在复位里 —— 本项目两样都出过), DMA 会照旧读上一块 bank,
            而账面 `active_bank` 照样更新, `underflow` 照样干净。这类"静默读到旧内容"只有
            把寄存器读回来才抓得到。
          * `axi_gpio_8` bit4 —— PL 自己的 `user_active_bank`(见 `awg_top.v:510` 的
            `gpio14_out` 位域), 即消费链认的那个 bank。

        返回 `{"src_addr": int 或 None, "bank": 0/1 或 None}`; `bank` 由源地址与
        `bank_phys` 比对得出(地址是运行期 CMA 分配的, 不能硬编码)。
        """
        src = None
        try:
            src = self.dma._rd(DMA_SRCADDR) & 0xFFFFFFFF
        except Exception:
            pass
        bank = None
        if src is not None:
            for i, phys in enumerate(self.bank_phys):
                if phys is not None and src == (phys & 0xFFFFFFFF):
                    bank = i
                    break
        return {"src_addr": src, "bank": bank}

    def _timeout_for(self, bytes_):
        """`ddr_pp_timeout_for` 的秒制镜像: 播放时长 + 2 s 余量。"""
        mode = self._digmod_mode()
        bits = {2: 2, 3: 2, 4: 4, 5: 6}.get(mode, 1)
        symbols = (bytes_ * 8 + bits - 1) // bits
        rate = self._symrate() or 10000
        return symbols / float(rate) + 2.0

    # ---- UART 侧动词(main.c:2158-2263) ----

    def clear(self, blocked_fn=lambda: False):
        if self.active or blocked_fn() or self.banks[0].owner == STAGE_UDP \
                or self.banks[1].owner == STAGE_UDP:
            return "ERR DDR PP BUSY"
        for b in self.banks:
            b.clear_stage()
            b.fresh = b.valid
        self.timeline = {}
        return "OK"

    def chunk(self, bank, offset, data, crc8):
        b = self.banks[bank]
        if b.owner != STAGE_UART:
            return "ERR DDR PP CHUNK BUSY"
        # (crc8 校验在调用方做 —— awg_ctl 已有 crc8_atm 的实现路径)
        return "OK"

    def commit(self, bank, bit_length, crc16, memory=None):
        b = self.banks[bank]
        if b.owner != STAGE_UART:
            return "ERR DDR PP COMMIT BUSY"
        if bit_length < 32 or bit_length % 32:
            return "ERR BAD VALUE"
        bytes_ = bit_length // 8
        if b.stage_bytes != bytes_:
            return "ERR DDR PP COMMIT FAILED"
        if memory is not None and crc16_ccitt(memory[:bytes_]) != crc16:
            return "ERR DDR PP COMMIT FAILED"
        b.bit_length = bit_length
        b.crc16 = crc16
        b.valid = 1
        b.fresh = 1
        b.owner = STAGE_NONE
        return "OK"

    def start(self, bank=2, blocked_fn=lambda: False):
        if self.active or blocked_fn():
            self.state, self.error = "ERROR", PP_START_FAILED
            return False
        if bank > 1:
            bank = 0 if self.banks[0].valid else (1 if self.banks[1].valid else 2)
        if (bank > 1 or not self.banks[bank].valid or self._digmod_mode() == 0
                or (self.banks[0].owner == STAGE_UART and self.banks[0].stage_bytes)
                or (self.banks[1].owner == STAGE_UART and self.banks[1].stage_bytes)):
            self.state, self.error = "ERROR", PP_START_FAILED
            return False
        bytes_ = self.banks[bank].bit_length // 8
        if bytes_ == 0 or bytes_ > BANK_BYTES or self.banks[bank].bit_length % 32:
            self.state, self.error = "ERROR", PP_START_FAILED
            return False
        if not self.dma.reset():
            self.state, self.error = "ERROR", PP_RESET_FAILED
            return False
        # 固件在 start 时会 `digmod_source=1; write_all()`(main.c:1614-1615) —— 不把数据源
        # 切到 USER, PL 的消费路径不开, DMA 排出的数据没人取, 状态机只能按超时判死。
        #
        # ⚠ **只写 SOURCE(+BANK), 绝不能写 length**。ch1 的 length 字段是 **BRAM
        #   USERBUF 那一路**的长度(固件里就是 `user_bit_length`, 512 B 量级), 而 PP 的
        #   长度走 ch2(`dma_pp_stream_ctrl` 的 length<<3)。而 awg_top 的 `user_length`
        #   恰恰取自 ch1 —— 把 1 MiB 塞进 ch1 会让 BRAM 路径的长度变成 1 MiB, 消费链按
        #   错误的长度节流, 于是 DMA 永远排不完(板上实测: 30 s 不动, user_status 恒
        #   0x42/READY 不置位)。裸机固件之所以没这问题, 就是因为它写的是 USERBUF 的值。
        #
        # ⚠ **优先走 `_write_user_ctrl_for_pp`**(板端注入的 `AwgCtl` 方法): 它带同一把锁,
        #   且会把 `digmod.source` 影子一起置 1 —— 否则播放中来一条 `SET`(`write_all()` 按
        #   影子重写 ch1)就会把 USER 源悄悄关掉, 正在播的流当场断。裸 GPIO 注入只留给离线测试。
        USER_CTRL_SOURCE, USER_CTRL_BANK = 1, 1 << 12
        if self._write_user_ctrl_for_pp is not None:
            self._write_user_ctrl_for_pp(bank)
        elif self._write_user_ctrl_ch1 is not None:
            self._write_user_ctrl_ch1(USER_CTRL_SOURCE | (USER_CTRL_BANK if bank else 0))
        self._stream_ctrl(0, 0, bank)
        self.dma.submit(self.bank_phys[bank], bytes_)
        self.banks[bank].fresh = 0
        self._stream_ctrl(1, bytes_, bank)
        self.active = 1
        self.active_bank = bank
        self.started_at = self.clock()
        self.timeout_s = self._timeout_for(bytes_)
        self.state = "RUNNING"
        self.error = 0
        return True

    def stop(self):
        ok = self.dma.reset()
        self._stream_ctrl(0, 0, self.active_bank if self.active_bank < 2 else 0)
        self.active = 0
        self.active_bank = 0xFF
        self.state = "IDLE" if ok else "ERROR"
        if not ok:
            self.error = PP_RESET_FAILED
        else:
            self.error = 0
        return ok

    # ---- 轮询(替代裸机主循环里的 ddr_pp_service) ----

    def service(self):
        """推进状态机; 返回 (事件名或 None, 是否仍 active)。**只读状态, 不抛。"""
        if not self.active:
            return None, False
        now = self.clock()
        sr = self.dma.status()
        st = self._user_status()
        if sr & DMA_ERR_ALL_MASK:
            self.stop()
            self.error = PP_DMA_ERROR
            self.state = "ERROR"
            return "error", False
        if st["underflow"]:
            self.stop()
            self.error = PP_UNDERFLOW
            self.state = "ERROR"
            return "error", False
        if now - self.started_at > self.timeout_s:
            self.stop()
            self.error = PP_TIMEOUT
            self.state = "ERROR"
            return "error", False
        if self.state == "DRAIN" and st["stream_done"]:
            current = self.active_bank
            other = current ^ 1
            self._stream_ctrl(0, 0, current)
            self.drain_at = now
            if other < 2 and self.banks[other].fresh:
                self.active = 0
                self.active_bank = 0xFF
                if not self.dma.reset():
                    self.state, self.error = "ERROR", PP_RESET_FAILED
                    return "error", False
                if not self.start(other, blocked_fn=lambda: False):
                    if self.error == 0:
                        self.error = PP_SUBMIT_FAILED
                    return "error", False
                self.timeline["NEXT_SUBMIT_S"] = self.clock() - self.drain_at
                return "switched", True
            if other < 2 and self.banks[other].owner == STAGE_UDP:
                self.stop()
                self.error = PP_RING_BLOCKED
                self.state = "ERROR"
                return "error", False
            self.active = 0
            self.active_bank = 0xFF
            self.state = "IDLE"
            self.error = 0
            return "drained", False
        if self.state == "RUNNING" and (sr & DMA_IRQ_IOC_MASK) and not self.dma.busy():
            self.ioc_at = now
            self.state = "DRAIN"
            return "ioc", True
        return None, True

    def status_line(self):
        """`GET DDR PP` 的回包(`main.c:1968-1989` 的格式串逐字镜像)。"""
        nxt = "NONE"
        if self.active and self.active_bank < 2 and self.banks[self.active_bank ^ 1].fresh:
            nxt = str(self.active_bank ^ 1)
        parts = ["DDR PP ACTIVE %d" % self.active,
                 "ACTIVE_BANK %s" % self._bank_name(self.active_bank),
                 "NEXT_BANK %s" % nxt,
                 "STATE %s" % self.state,
                 "ERROR %02X" % self.error]
        for i in (0, 1):
            b = self.banks[i]
            parts.append("BANK%d VALID %d LENGTH %d CRC %04X"
                          % (i, b.valid, b.bit_length, b.crc16))
        return " ".join(parts)

    # ---- UDP 侧(同一 ADPP 线格式; 由 awg_bridge 或独立线程喂数据包进来) ----

    def udp_feed(self, pkt):
        """处理一个 UDP 包, 返回**要回的报文**(或 None)。校验与 `udp_rx` 对齐。"""
        if len(pkt) < HEADER_SIZE or len(pkt) > HEADER_SIZE + MAX_PAYLOAD:
            return None                          # magic/len 不符: 静默丢弃
        (magic, ver, ptype, bank, session, seq, offset,
         total, plen, crc) = HEADER.unpack(pkt[:HEADER_SIZE])
        if magic != b"ADPP" or ver != 1:
            return None
        if len(pkt) != HEADER_SIZE + plen:
            return None

        # ⚠ 回包必须**逐字回显**请求头里的 bank/session/sequence/total/crc(固件
        #   `send_reply` 就是这么填的)。主机侧客户端按 (session, bank, sequence) 过滤回包
        #   (`host/siggen/udp_ddr_pp.py:_recv_reply`), 全填 0 的话它**一个回包都匹配不上**,
        #   每个事务都要等满重传次数才超时。离线测试只读 state/error/received, 所以这条
        #   在假 socket 上永远测不出来 —— 只有真客户端接进来才暴露。
        def rep(state, error, received=0, miss_off=0, miss_len=0):
            return self._reply(state, error, received, miss_off, miss_len,
                               bank, session, seq, total, crc)

        if ptype == TYPE_ECHO:
            if bank != ECHO_BANK or session or seq or offset or total or plen or crc:
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_HEADER)
            return rep(UDP_STATE.index("IDLE"), UDP_ERR_NONE)
        if bank > 1 or session == 0:
            return rep(UDP_STATE.index("IDLE"), UDP_ERR_HEADER)
        b = self.banks[bank]
        sess = self._udp_session.get(bank, 0)
        if ptype == TYPE_BEGIN:
            if not (total and total % 4 == 0 and total <= BANK_BYTES):
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_RANGE,
                           b.received_bytes)
            if sess == session and b.owner == STAGE_UDP:
                return rep(UDP_STATE.index("RECEIVING"), UDP_ERR_NONE,
                           b.received_bytes)
            # ⚠ 播放中**必须允许**往非活动 bank 喂数 —— 这就是环形喂数的全部前提。
            #   固件 main.c:1774-1785 的门禁是"播放中只拒**活动** bank"(它正被 DMA 读),
            #   而老代码写的是 `if b.owner == STAGE_UDP or self.active:` —— 播放中一律拒,
            #   于是环形永远起不来(离线测不出: 老测试都在停播状态下喂)。
            #   停播那一半仍照固件: 要 `digmod_mode == 0`(数据源已让给 AWG), 否则 BUSY。
            if b.owner == STAGE_UDP:
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_BUSY,
                           b.received_bytes)
            if self.active:
                if bank == self.active_bank:
                    return rep(UDP_STATE.index("REJECTED"), UDP_ERR_BUSY,
                               b.received_bytes)
            elif self._digmod_mode() != 0 or self._play_blocks():
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_BUSY,
                           b.received_bytes)
            b.clear_stage()
            b.owner = STAGE_UDP
            b.open = 1
            b.expected_bytes = total
            b.payload_crc32 = crc
            b.received_bytes = 0
            b.seen_words = set()
            # ⚠ BEGIN 就把 `fresh` 清掉: 否则 DMA 排空当前 bank 时, 状态机看到这个
            #   "还是 fresh、其实正在被重写"的 bank 会立刻切过去读半成品(固件
            #   ddr_pp_udp_begin 同样在开头清 fresh, main.c:1801)。COMMIT 才重新置起。
            b.fresh = 0
            self._udp_session[bank] = session
            return rep(UDP_STATE.index("RECEIVING"), UDP_ERR_NONE)
        if ptype == TYPE_DATA:
            if sess != session or b.owner != STAGE_UDP:
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_SESSION,
                           b.received_bytes)
            if (plen < 4 or plen % 4 or offset % 4 or offset + plen > b.expected_bytes):
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_RANGE,
                           b.received_bytes)
            if crc32_ieee(pkt[HEADER_SIZE:]) != crc:
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_CRC,
                           b.received_bytes)
            # 通过校验: 直接落盘并回状态(调用方不必再二次分发 —— 那个设计让
            # "received_bytes"在普通路径上丢失, 实测 1024 字节全收完却回 0)
            return self.udp_accept_data(bank, offset, pkt[HEADER_SIZE:],
                                        seq=seq, total=total, crc=crc)
        if ptype == TYPE_STATUS:
            if sess != session or b.owner != STAGE_UDP:
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_SESSION,
                           b.received_bytes)
            return rep(UDP_STATE.index("RECEIVING"), UDP_ERR_NONE,
                       b.received_bytes)
        if ptype == TYPE_COMMIT:
            if sess != session or b.owner != STAGE_UDP:
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_SESSION,
                           b.received_bytes)
            if b.received_bytes != b.expected_bytes:
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_INCOMPLETE,
                           b.received_bytes)
            return ("commit", bank, session)
        if ptype == TYPE_ABORT:
            if sess != session:
                return rep(UDP_STATE.index("REJECTED"), UDP_ERR_SESSION,
                           b.received_bytes)
            b.clear_stage()
            self._udp_session.pop(bank, None)
            return rep(UDP_STATE.index("ABORTED"), UDP_ERR_NONE,
                       b.received_bytes)
        return rep(UDP_STATE.index("REJECTED"), UDP_ERR_HEADER, b.received_bytes)

    def _reply(self, state, error, received, miss_off, miss_len,
               bank=0, session=0, seq=0, total=0, crc=0):
        """回包 = 32B 头 + 20B 状态。头里回显请求的 bank/session/sequence/total/crc
        (固件 `send_reply` 的字段顺序逐字对应, 见 `sdk/src/udp_ddr_pp.c:155-183`)。"""
        body = struct.pack("!IIIII", state, error, received, miss_off, miss_len)
        head = HEADER.pack(b"ADPP", 1, TYPE_REPLY, bank, session, seq, 0,
                           total, STATUS_SIZE, crc)
        return head + body

    def udp_accept_data(self, bank, offset, data, memory_view=None,
                        seq=0, total=0, crc=0):
        """DATA 已通过校验: 写进 bank 内存(由调用方提供物理内存写接口)。"""
        b = self.banks[bank]
        if memory_view is None:
            memory_view = self.bank_mem[bank]
        if memory_view is not None:
            memory_view[offset:offset + len(data)] = data
        b.received_bytes += len(data)
        base_word = offset // 4
        for i in range(len(data) // 4):
            b.seen_words.add(base_word + i)
        return self._reply(UDP_STATE.index("RECEIVING"), UDP_ERR_NONE,
                           b.received_bytes, 0, 0, bank,
                           self._udp_session.get(bank, 0), seq, total, crc)

    def latch_bank(self, bank, byte_len, crc16=None):
        """把一个 bank 锁存成"可播放"(COMMIT 的公共尾巴)。

        `udp_commit` 与 `feed_file` 共用 —— 两者只差数据是怎么进来的(分包 vs 整块),
        锁存语义必须**逐字相同**: 长度/valid/fresh 一起置, owner 让回 NONE。
        `crc16=None` 表示不动 CRC 字段(UDP 那条路不带 CRC16, 保持原值)。
        """
        b = self.banks[bank]
        b.bit_length = byte_len * 8
        if crc16 is not None:
            b.crc16 = crc16
        b.valid = 1
        b.fresh = 1
        b.owner = STAGE_NONE
        return b

    def feed_file(self, bank, data, memory_view=None, crc16=0):
        """**本地文件直喂**(不经 UDP): 把整块拷进 bank 内存并锁存。

        板端自洽路径用这条 —— 文件已经在板子上(`/home/xilinx/waveforms/*.bin`), 没有理由
        再拆成 1400 B 的包从 loopback 走一遍 UDP。分包那条路(`udp_feed`/`udp_commit`)保持
        原样给主机 GUI 用。

        门禁与 `udp_feed` 的 BEGIN **逐条对齐**, 否则两条入口会在同一块 bank 上打架:
          * 活动 bank 拒(DMA 正读它, 当场改写 = 播放出垃圾);
          * 长度必须是 4..BANK_BYTES 且 4 字节对齐(`bit_length % 32 == 0`);
          * 停播时还要求 `digmod_mode == 0`(数据源还在 AWG 手里时必须 BUSY)。

        返回 `(ok, reason)`; `reason` 是给面板/日志看的短词, 不是协议回包。
        """
        n = len(data)
        if bank > 1:
            return False, "BAD BANK"
        if n == 0 or n % 4 or n > BANK_BYTES:
            return False, "RANGE"
        if self.active and bank == self.active_bank:
            return False, "BUSY"                 # 活动 bank 正被 DMA 读
        if not self.active and (self._digmod_mode() != 0 or self._play_blocks()):
            return False, "BUSY"
        if memory_view is None:
            memory_view = self.bank_mem[bank]
        if memory_view is not None:
            memory_view[0:n] = data
        b = self.banks[bank]
        b.clear_stage()
        b.expected_bytes = n
        b.received_bytes = n
        b.payload_crc32 = crc32_ieee(data)
        # 与 BEGIN 一样**先清 fresh**: 状态机看到"还是 fresh、其实正在被重写"的 bank 会立刻
        # 切过去读半成品(`udp_feed` 的 BEGIN 分支同款)。锁存时再置起。
        b.fresh = 0
        self.latch_bank(bank, n, crc16)
        return True, "OK"

    def udp_commit(self, bank, memory_view=None):
        b = self.banks[bank]
        session = self._udp_session.get(bank, 0)
        if b.received_bytes != b.expected_bytes:
            return self._reply(UDP_STATE.index("REJECTED"), UDP_ERR_INCOMPLETE,
                               b.received_bytes, 0, 0, bank, session,
                               SEQ_COMMIT, b.expected_bytes, b.payload_crc32)
        if memory_view is not None and crc32_ieee(memory_view[:b.expected_bytes]) != b.payload_crc32:
            return self._reply(UDP_STATE.index("REJECTED"), UDP_ERR_CRC,
                               b.received_bytes, 0, 0, bank, session,
                               SEQ_COMMIT, b.expected_bytes, b.payload_crc32)
        self.latch_bank(bank, b.expected_bytes)
        self._udp_session.pop(bank, None)
        return self._reply(UDP_STATE.index("COMMITTED"), UDP_ERR_NONE,
                           b.received_bytes, 0, 0, bank, session,
                           SEQ_COMMIT, b.expected_bytes, b.payload_crc32)
