"""`awg_ddr_ring` 的离线测试: 收包/回包/COMMIT 锁存/与 service() 并行, 不碰硬件。

判据与 `test_awg_ddr_pp.py` 同源 —— 状态机与线协议已经逐字对齐固件, 这里只验**驱动层**
(谁收 socket、何时锁存、何时 flush、环形怎么并行)。
"""
import socket
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import awg_ddr_pp as P  # noqa: E402
import awg_ddr_ring as R  # noqa: E402
from test_awg_ddr_pp import FakeMmio, Ram, _pkt, _reply_fields  # noqa: E402


def _server(mode=5, ram=None):
    ram = ram or (Ram(), Ram())
    ctl = P.DdrPp([0x1000, 0x2000], FakeMmio(),
                  write_user_ctrl_ch2=lambda w: None,
                  read_user_status_ch1=lambda: 0,
                  digmod_mode_fn=lambda: mode,
                  symrate_fn=lambda: 100000)
    ctl.bank_mem = ram
    flushes = []
    events = []
    srv = R.RingServer(ctl, flush_fn=lambda b: flushes.append(b),
                       on_event=events.append)
    return srv, ctl, ram, flushes, events


def _upload(srv, bank, payload, session=7):
    """把一次完整上传喂给 `handle_datagram`(不经过 socket)。"""
    replies = [srv.handle_datagram(_pkt(P.TYPE_BEGIN, bank=bank, session=session,
                                        total=len(payload), crc=zlib.crc32(payload)))]
    for off in range(0, len(payload), 500):
        chunk = payload[off:off + 500]
        replies.append(srv.handle_datagram(
            _pkt(P.TYPE_DATA, bank=bank, session=session, seq=off // 500 + 1,
                 offset=off, total=len(payload), payload=chunk,
                 crc=zlib.crc32(chunk))))
    replies.append(srv.handle_datagram(
        _pkt(P.TYPE_COMMIT, bank=bank, session=session, seq=P.SEQ_COMMIT,
             total=len(payload), crc=zlib.crc32(payload))))
    return replies


# ---------------- 收包 -> 回包 ----------------

def test_a_datagram_that_needs_no_reply_returns_none():
    srv, _ctl, _ram, _fl, _ev = _server()
    assert srv.handle_datagram(b"\x00" * 4) is None      # 太短: 静默丢弃
    assert srv.stats["datagrams"] == 0


def test_commit_latches_the_bank_and_flushes_it():
    """COMMIT 必须在驱动层落盘并 flush —— HP 口非相干, 不刷 cache 板子读到旧内容。"""
    srv, ctl, ram, flushes, events = _server(mode=0)
    payload = bytes(range(256)) * 4
    replies = _upload(srv, 0, payload)
    _, state, error, _ = _reply_fields(replies[-1])
    assert state == 2 and error == P.UDP_ERR_NONE        # COMMITTED
    assert flushes == [0], "锁存前必须 flush 那个 bank"
    assert ctl.banks[0].valid and ctl.banks[0].fresh
    assert ctl.banks[0].bit_length == len(payload) * 8
    assert bytes(ram[0][:len(payload)]) == payload, "载荷没落进 bank 内存"
    assert srv.stats["commits"] == 1
    assert any("commit" in e for e in events)


def test_replies_are_echoed_with_the_request_sequence():
    """回包的 sequence 必须是请求的那一个 —— 主机侧客户端按它过滤。"""
    srv, _ctl, _ram, _fl, _ev = _server(mode=0)
    reply = srv.handle_datagram(_pkt(P.TYPE_DATA, bank=0, session=0xABCD, seq=9,
                                     offset=0, total=64, payload=b"\x11" * 8,
                                     crc=zlib.crc32(b"\x11" * 8)))
    head = P.HEADER.unpack(reply[:P.HEADER_SIZE])
    assert head[4] == 0xABCD and head[5] == 9


# ---------------- 环形: 喂数与播放并行 ----------------

def test_a_ring_round_trip_switches_to_the_fed_bank():
    """一个完整回合: 起播 bank0 -> 播放中喂 bank1 -> 切过去。"""
    srv, ctl, ram, flushes, events = _server(mode=5)
    for i in (0, 1):
        ctl.banks[i].valid = ctl.banks[i].fresh = 1
        ctl.banks[i].bit_length = 4096 * 8
    assert ctl.start(0)

    payload = bytes((i * 7) & 0xFF for i in range(2048))
    _upload(srv, 1, payload)
    assert ctl.banks[1].fresh == 1
    assert flushes == [1]

    # 当前 bank 排空 -> service_once 自动切到刚喂好的 bank1
    ctl.dma.mmio.regs[P.DMA_SR] = P.DMA_IRQ_IOC_MASK | P.DMA_SR_IDLE
    assert srv.service_once() == "ioc"
    ctl._read_status = lambda: P.USER_STATUS_STREAM_DONE
    assert srv.service_once() == "switched"
    assert ctl.active_bank == 1
    assert srv.stats["switches"] == 1
    assert any("switched" in e for e in events)


def test_a_refill_is_rejected_for_the_active_bank_but_accepted_for_the_other():
    """驱动层不该掩盖状态机的门禁: 活动 bank 报 BUSY, 非活动 bank 收下。"""
    srv, ctl, _ram, _fl, _ev = _server(mode=5)
    for i in (0, 1):
        ctl.banks[i].valid = ctl.banks[i].fresh = 1
        ctl.banks[i].bit_length = 4096 * 8
    assert ctl.start(1)
    _, _, err_active, _ = _reply_fields(
        srv.handle_datagram(_pkt(P.TYPE_BEGIN, bank=1, session=7, total=64)))
    assert err_active == P.UDP_ERR_BUSY
    _, state_other, err_other, _ = _reply_fields(
        srv.handle_datagram(_pkt(P.TYPE_BEGIN, bank=0, session=7, total=64)))
    assert err_other == P.UDP_ERR_NONE and state_other == 1


def test_service_errors_are_counted_and_reported():
    srv, ctl, _ram, _fl, events = _server(mode=5)
    ctl.banks[0].valid = ctl.banks[0].fresh = 1
    ctl.banks[0].bit_length = 4096 * 8
    assert ctl.start(0)
    ctl._read_status = lambda: P.USER_STATUS_UNDERFLOW
    assert srv.service_once() == "error"
    assert srv.stats["errors"] == 1 and ctl.error == P.PP_UNDERFLOW
    assert any("error" in e for e in events)


# ---------------- 真循环(假 socket) ----------------

class FakeSock(object):
    """喂一串预设数据报, 记录发出去的回复。`recvfrom` 收完就抛 timeout。"""

    def __init__(self, wires):
        self._pending = list(wires)
        self.sent = []

    def recvfrom(self, _size):
        if not self._pending:
            raise socket.timeout()
        return self._pending.pop(0), ("127.0.0.1", 40100)

    def sendto(self, data, _addr):
        self.sent.append(data)


def test_pump_drains_all_pending_datagrams_then_stops_on_timeout():
    """一拍要把积压的数据报全处理掉(否则 1400B 分片会越积越多), 然后靠超时收手。"""
    srv, ctl, _ram, _fl, _ev = _server(mode=0)
    payload = bytes(range(256)) * 4
    wires = [_pkt(P.TYPE_BEGIN, bank=0, session=7, total=len(payload),
                  crc=zlib.crc32(payload))]
    for off in range(0, len(payload), 500):
        chunk = payload[off:off + 500]
        wires.append(_pkt(P.TYPE_DATA, bank=0, session=7, seq=off // 500 + 1,
                          offset=off, total=len(payload), payload=chunk,
                          crc=zlib.crc32(chunk)))
    wires.append(_pkt(P.TYPE_COMMIT, bank=0, session=7, seq=P.SEQ_COMMIT,
                      total=len(payload), crc=zlib.crc32(payload)))
    sock = FakeSock(wires)
    srv._pump(sock)
    assert len(sock.sent) == len(wires), "每个报文都要有回包"
    assert ctl.banks[0].valid and ctl.banks[0].fresh
    assert sock.sent and P.HEADER.unpack(sock.sent[-1][:P.HEADER_SIZE])[2] == P.TYPE_REPLY