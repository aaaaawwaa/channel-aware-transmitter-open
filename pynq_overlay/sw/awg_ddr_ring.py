#!/usr/bin/env python3
"""板端 UDP 环形喂数(P5b): 收 ADPP 包 -> `DdrPp.udp_feed` -> 回包, 并在 COMMIT 时锁存。

为什么需要它: 裸机固件里 lwIP 的 raw-GEM 回调直接调 `udp_rx`, 而 `ddr_pp_service()` 在
主循环里轮询 DMA 完成。Linux 用户态没有那个回调, 所以要自己起一个**收包 + 轮询**的循环:
  * 一个 UDP socket 绑在 40100(与固件同口), 收 `ADPP` 报文;
  * 每个报文交给 `DdrPp.udp_feed`(纯逻辑, 与固件逐字对齐), 回包原样发回;
  * `udp_feed` 遇到 COMMIT 时返回哨兵 `("commit", bank, session)` —— 这里替它调
    `udp_commit` 完成锁存, 并在锁存前 **flush** 那个 bank(HP 口非相干);
  * 同一循环里每拍调一次 `DdrPp.service()`: IOC -> DRAIN -> (另一 bank fresh) 自动重臂。
    环形的"喂"与"播"因此并行 —— 播放中收进来的数据落在非活动 bank, 下一拍就被切过去。

**与固件的根本差异**: 固件用固定物理地址, 这里用 CMA(`pynq.allocate`), 所以 `bank_mem`
是 `PynqBuffer` 的 memoryview 而不是裸指针。`flush_fn` 就是补上 HP 口那个"写完要刷 cache"
的动作(见 `pynq_lcd.py` 同款前科: 非相干口写侧必须 flush)。

⚠ `DdrPp` 的 DMA/GPIO 写集中在**这个循环的线程**里。若同时还有 `awg_bridge` 在跑,
它只碰 `ctl.handle` 的非 PP 命令, 两边目前没有共享可变状态 —— 但别在别处再调 `service()`。
"""
import errno
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import awg_ddr_pp as P  # noqa: E402

RECV_SIZE = P.HEADER_SIZE + P.MAX_PAYLOAD + 256


class RingServer(object):
    """收 ADPP 报文 + 轮询播放状态机的循环。**纯逻辑部分不碰真 socket**, 离线可测。"""

    def __init__(self, pp, host="0.0.0.0", port=40100, flush_fn=None,
                 on_event=None, poll_interval=0.002, socket_factory=socket.socket,
                 clock=time.monotonic):
        self.pp = pp
        self.host = host
        self.port = port
        self._flush_fn = flush_fn
        self._on_event = on_event
        self.poll_interval = poll_interval
        self._socket_factory = socket_factory
        self.clock = clock
        self.running = False
        self.stats = {"datagrams": 0, "commits": 0, "switches": 0, "errors": 0}

    # ---- 纯逻辑: 一个数据报进, 一个回包出 ----

    def handle_datagram(self, wire):
        """吃一个数据报, 返回要回的报文(或 None)。COMMIT 在这里完成锁存。"""
        reply = self.pp.udp_feed(wire)
        # `udp_feed` 对 COMMIT 返回哨兵而不是报文 —— 锁存要落盘 + flush, 属于驱动层。
        if isinstance(reply, tuple) and reply and reply[0] == "commit":
            _, bank, session = reply
            self._flush(bank)
            reply = self.pp.udp_commit(bank, memory_view=self.pp.bank_mem[bank])
            self.stats["commits"] += 1
            self._event("commit bank=%d" % bank)
        if reply is not None:
            self.stats["datagrams"] += 1
        return reply

    def _flush(self, bank):
        if self._flush_fn is not None:
            self._flush_fn(bank)

    def _event(self, text):
        if self._on_event is not None:
            self._on_event(text)

    def service_once(self):
        """推进一拍播放状态机; 返回事件名(或 None)。"""
        event, _active = self.pp.service()
        if event == "switched":
            self.stats["switches"] += 1
        elif event == "error":
            self.stats["errors"] += 1
        if event is not None:
            self._event("pp %s" % event)
        return event

    # ---- 真循环 ----

    def open_socket(self):
        sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.settimeout(self.poll_interval)
        return sock

    def serve_forever(self, sock=None):
        """收包 + 轮询。`sock` 可注入(测试用假 socket); 缺省自己开。"""
        own = sock is None
        if own:
            sock = self.open_socket()
        self.running = True
        try:
            while self.running:
                self._pump(sock)
                self.service_once()
        finally:
            if own:
                sock.close()

    def _pump(self, sock):
        """把当前可读的数据报全部处理掉(非阻塞语义: 超时即返回)。"""
        while True:
            try:
                wire, addr = sock.recvfrom(RECV_SIZE)
            except socket.timeout:
                return
            except OSError as exc:
                if getattr(exc, "errno", None) in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                raise
            reply = self.handle_datagram(wire)
            if reply is not None:
                sock.sendto(reply, addr)

    def stop(self):
        self.running = False


def build_board_server(bitfile=None, host="0.0.0.0", port=40100, on_event=None,
                       overlay=None, ctl=None, allocate_fn=None):
    """板上组装: 装 overlay、CMA 双 bank、把 GPIO 通道缓存住。

    ⚠ `ov.axi_gpio_7.channel2` 每次访问都重新构造驱动(实测单次 ~24 ms), 切换热路径上会走
    好几次 —— 不缓存就把切换间隙从毫秒级抬到上百毫秒(远超 AXIS FIFO 的 ~30 ms 容量)。

    ⚠ **必须带一个真的 `AwgCtl`**: 播放的前提是 PL 的消费链开着, 而那是 `digmod_source`
    位(`SET DIGMOD MODE <非 OFF>` 才写)。只把 `DdrPp._digmod_mode` 改成 `lambda: 5` 只是
    改了 Python 侧的判断依据, PL 侧没开 —— DMA 排出的数据没人取, 状态机按超时判死(ERROR 0x24)。
    返回的 `ctl` 供调用方下发 `SET DIGMOD ...`。

    ⚠ **`overlay` 与 `ctl` 可注入** —— 守护进程(它自己已经持了 overlay)必须传, 见下。
    """
    from pynq import Clocks, Overlay, allocate as _allocate
    import awg_ctl as A
    # ⚠ **绝不能调第二次 `Overlay()`**。`Overlay()` 会抢 FPGA manager, 而守护进程
    #   (pynq-status)从开机起就持着同一个 overlay —— 第二次调用会把板子卡死(本项目
    #   2026-09-12 与 09-14 各一次, 连"只想读个 ip_dict"的只读探针也一样)。
    #   所以板端组装一律走 `overlay=`/`ctl=` 注入; 不给参数时(独立脚本如 p5b_ringtest)
    #   才自己装一次。
    ov = overlay if overlay is not None else (Overlay(bitfile) if bitfile else Overlay())
    Clocks.fclk0_mhz = 50.0
    ctl = ctl if ctl is not None else A.AwgCtl(A.PynqBackend(ov))
    allocate = allocate_fn if allocate_fn is not None else _allocate
    ch2 = ov.axi_gpio_7.channel2
    user_status = ov.axi_gpio_8.channel1
    banks = (allocate(shape=(P.BANK_BYTES,), dtype="u1"),
             allocate(shape=(P.BANK_BYTES,), dtype="u1"))
    pp = P.DdrPp([banks[0].device_address, banks[1].device_address],
                 ov.axi_dma_0.mmio,
                 write_user_ctrl_ch2=lambda w: ch2.write(w & 0xFFFFFFFF, 0xFFFFFFFF),
                 read_user_status_ch1=lambda: user_status.read(),
                 digmod_mode_fn=lambda: ctl.digmod.mode,
                 symrate_fn=lambda: ctl.digmod.symrate_hz)
    # ⚠ ch1(用户源控制字)**归 `AwgCtl`**, 不在这里直接写 GPIO。`DdrPp.start()` 只知道自己
    #   要切到 USER 源, 而"切源"这个动作在固件里是 `digmod_source=1; write_all()`
    #   (`main.c:1614-1615`)—— 走 ctl 才有同一把锁、影子状态也跟着动。绕过它就会出现
    #   "GET DIGMOD 说 PRBS7、PL 其实读 USER" 这种两处真源不一致。
    pp._write_user_ctrl_ch1 = None
    pp._write_user_ctrl_for_pp = ctl.write_user_ctrl_for_pp
    pp.bank_mem = (memoryview(banks[0]), memoryview(banks[1]))

    def flush(bank):
        try:
            banks[bank].flush()          # HP 口非相干: 写完必须刷 cache
        except AttributeError:
            pass

    return ov, pp, banks, RingServer(pp, host=host, port=port, flush_fn=flush,
                                     on_event=on_event), ctl


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="板端 UDP 环形喂数服务")
    ap.add_argument("--bitfile", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "awg_merged.bit"))
    ap.add_argument("--port", type=int, default=40100)
    args = ap.parse_args()
    _ov, _pp, _banks, server, _ctl = build_board_server(args.bitfile, port=args.port,
                                                        on_event=lambda t: print(t, flush=True))
    print("ring: 监听 %s:%d, 等 ADPP 报文" % (server.host, server.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()