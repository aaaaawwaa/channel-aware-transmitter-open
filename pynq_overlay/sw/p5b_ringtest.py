#!/usr/bin/env python3
"""P5b 的板上验收: **持续环形喂数**(UDP 收包线程 + 播放状态机并行)。

与 P5 的差别: P5 只验证"两个 bank 都 fresh 时切一次"; 这里验证**播放中持续往非活动 bank
喂新内容** —— 真正的环形。判据对齐裸机侧 `scripts/udp_ddr_pp_gate.py` 的 M4/M5:
  * 切换顺序必须交替(每次切到**刚喂好**的那个 bank);
  * 收尾必须干净: IDLE / ERROR 00;
  * **underflow 必须干净**(PL 的消费 FIFO 没断过流 —— 这是硬件级的裁决位, 不是我们算的)。

方法(全部走真代码路径, 不用桩):
  1. 真 PL 上 CMA 双 bank, 起播 bank0(QAM16 / 400 kSym/s, 1 MiB ≈ 5.2 s/bank);
  2. 主线程跑 `RingServer.serve_forever()` —— **真 UDP socket** 绑 40100, 收 ADPP 报文,
     喂 `DdrPp.udp_feed`, 回包, COMMIT 时落盘 + flush;
  3. 驱动线程用**真 UDP socket** 把下一块发到 127.0.0.1:40100(非活动 bank), 按板上状态
     决定何时喂(照 `host/siggen/ring_feeder.py` 的决策: 非活动 bank 不 fresh 就喂);
  4. 数切换次数, 收尾读 underflow。

线程约定: `DdrPp` 的**全部写**(udp_feed/udp_commit/service)都在主线程(server 循环里);
驱动线程只**读** `pp` 的状态做决策。GIL 下单个属性读是原子的, 且驱动只读不写。

跑法(板上, 先停 pynq-status —— overlay 只能有一个持有者):
    cd /home/xilinx/overlays/awg_merged
    sudo env XILINX_XRT=/usr PYTHONPATH=/home/xilinx/overlays/awg_merged:/home/xilinx \\
        python3 -u p5b_ringtest.py
"""
import os
import socket
import struct
import sys
import threading
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import awg_ddr_pp as P
import awg_ddr_ring as R

BANK_BYTES = P.BANK_BYTES
PORT = 40100
ROUNDS = 3                      # 要观测到的切换次数
SYMRATE = 400000                # QAM16 4bit/符号 -> 1 MiB ≈ 5.2 s/bank
HOST_IP = "192.0.2.10"        # 板子自己的地址: 用真网口发, 而不是 lo


def make_payload(seed):
    """确定性的 1 MiB 图样(每轮不同 -> CRC16 不同, 才能证明"切到的是刚喂的那块")。"""
    return bytes((i * 41 + 7 + seed) & 0xFF for i in range(BANK_BYTES))


class LoopClient(object):
    """最小 UDP 上传器: 用真 socket 把一块发到板子的 40100。

    刻意不复用主机侧的 `UdpDdrPpClient` —— 那个要 Qt, 板上没有。这里只做 BEGIN/DATA/COMMIT
    这一条主路径(不回传重传窗口), 因为环回网络不丢包; 但**回包字段是要校验的**
    (session/bank/sequence 必须对上, 否则说明回包没回显请求头)。
    """

    def __init__(self, target, timeout=1.0):
        self.target = target
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self.session = 0

    def _exchange(self, wire, session, bank, seq):
        self.sock.sendto(wire, self.target)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            reply, _addr = self.sock.recvfrom(P.HEADER_SIZE + P.MAX_PAYLOAD + 256)
            head = P.HEADER.unpack(reply[:P.HEADER_SIZE])
            if head[2] != P.TYPE_REPLY:
                continue
            if head[3] != bank or head[4] != session or head[5] != seq:
                raise AssertionError(
                    "回包头没回显请求: bank %d/%d session %08X/%08X seq %d/%d"
                    % (head[3], bank, head[4], session, head[5], seq))
            state, error = struct.unpack("!II", reply[P.HEADER_SIZE:P.HEADER_SIZE + 8])
            return state, error
        raise AssertionError("等回包超时")

    def upload(self, bank, payload):
        session = (self.session + 1) & 0xFFFFFFFF or 1
        self.session = session
        total = len(payload)
        wire = P.HEADER.pack(b"ADPP", 1, P.TYPE_BEGIN, bank, session, 0, 0,
                             total, 0, zlib.crc32(payload))
        state, error = self._exchange(wire, session, bank, 0)
        assert error == P.UDP_ERR_NONE, "BEGIN 被拒: state %d error %d" % (state, error)
        seq = 0
        for off in range(0, total, P.MAX_PAYLOAD):
            chunk = payload[off:off + P.MAX_PAYLOAD]
            seq += 1
            wire = P.HEADER.pack(b"ADPP", 1, P.TYPE_DATA, bank, session, seq, off,
                                 total, len(chunk), zlib.crc32(chunk)) + chunk
            state, error = self._exchange(wire, session, bank, seq)
            assert error == P.UDP_ERR_NONE, "DATA 被拒: state %d error %d" % (state, error)
        wire = P.HEADER.pack(b"ADPP", 1, P.TYPE_COMMIT, bank, session,
                             P.SEQ_COMMIT, 0, total, 0, zlib.crc32(payload))
        state, error = self._exchange(wire, session, bank, P.SEQ_COMMIT)
        assert error == P.UDP_ERR_NONE, "COMMIT 被拒: state %d error %d" % (state, error)
        assert state == P.UDP_STATE.index("COMMITTED"), "COMMIT 没落成 COMMITTED: %d" % state


def driver(pp, client, rounds, log, stop_evt, order):
    """驱动线程: 非活动 bank 不 fresh 就喂下一块(照 RingFeeder 的决策)。

    `order` 由调用方持有: 每次切换把**新活动 bank** 追加进去。判据是**必须交替**
    (每次都切到刚喂好的那个) —— 连续切到同一个 bank 说明回读的 fresh 与实际不符。
    """
    fed = 0
    seed = 1
    last_active = None
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline and not stop_evt.is_set():
        if pp.error:
            log.append("DRIVER: 播放出错 0x%02X" % pp.error)
            break
        if pp.active and pp.active_bank < 2:
            active = pp.active_bank
            if active != last_active:
                if last_active is not None:
                    order.append(active)
                    log.append("DRIVER: 切到 bank%d(第 %d 次)" % (active, len(order)))
                    if len(order) >= rounds:
                        break
                last_active = active
            other = active ^ 1
            if not pp.banks[other].fresh and pp.banks[other].owner == P.STAGE_NONE:
                payload = make_payload(seed)
                seed += 1
                client.upload(other, payload)
                fed += 1
                log.append("DRIVER: 喂 bank%d(第 %d 块, %d 字节)" % (other, fed, len(payload)))
        time.sleep(0.003)
    stop_evt.set()


def main():
    print("== 1. 装 overlay + CMA 双 bank + 起 server ==")
    bitfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "awg_merged.bit")
    _ov, pp, banks, server, ctl = R.build_board_server(bitfile, host="0.0.0.0",
                                                       port=PORT)

    import numpy as np
    for i in (0, 1):
        pattern = np.frombuffer(make_payload(i * 1000), dtype="u1")
        banks[i][:] = pattern
        try:
            banks[i].flush()
        except AttributeError:
            pass
        pp.banks[i].bit_length = BANK_BYTES * 8
        pp.banks[i].valid = 1
        pp.banks[i].fresh = 1
    print("   bank0 @0x%08X  bank1 @0x%08X" % (pp.bank_phys[0], pp.bank_phys[1]))

    # QAM16 + 400kSym/s: 1 MiB/4bit = 2.1M 符号 -> 5.2 s/bank
    # ⚠ 必须**真的下发到 PL**: `SET DIGMOD MODE <非 OFF>` 才写 `digmod_source`, 消费链才开。
    #   只改 `pp._digmod_mode` 是改 Python 侧的判断依据, PL 侧没开 -> DMA 排出的数据没人取,
    #   状态机按超时判死(ERROR 0x24)。
    print("== 2. 配置数字调制并起播 bank0 ==")
    ctl.handle("SET DIGMOD MODE OFF")
    ctl.handle("SET DIGMOD SYMRATE %d" % SYMRATE)
    ctl.handle("SET DIGMOD MODE QAM16")
    print("   digmod: %s" % ctl.handle("GET DIGMOD"))
    if not pp.start(0):
        print("   启动失败: %s (error 0x%02X)" % (pp.state, pp.error))
        return 1
    print("   %s" % pp.status_line())

    print("== 3. 环形喂数(%d 次切换) ==" % ROUNDS)
    log = []
    order = []
    stop_evt = threading.Event()
    client = LoopClient((HOST_IP, PORT))
    sock = server.open_socket()
    th = threading.Thread(target=driver, args=(pp, client, ROUNDS, log, stop_evt, order),
                          daemon=True)
    th.start()
    grants = []
    pl = []                     # 每次切换后的 (账面 bank, DMA 源地址解出的 bank, 源地址)
    try:
        while not stop_evt.is_set():
            server._pump(sock)
            if server.service_once() == "switched":
                # DRAIN 到重臂完成的间隙 —— 由 PL 侧 ~16 KB AXIS FIFO(≈30 ms 容量)吸收。
                # 与 P5 同款判据: 只要不叠加到连续两拍上, FIFO 就接得住。
                grants.append(pp.timeline.get("NEXT_SUBMIT_S", -1.0))
                # ⚠ **PL 侧判据**(不只是账面): 把 DMA 里实际被 arm 的源地址读回来, 与
                #   软件记的 active_bank 对照。若 `submit()` 的写没生效(BD 修订落后 / 外设被
                #   按在复位里 —— 本项目两样都出过), DMA 会照旧读上一块 bank, 而账面照样更新、
                #   underflow 照样干净 —— 只有读寄存器才抓得到这种"静默读到旧内容"。
                view = pp.pl_view()
                pl.append((pp.active_bank, view["bank"], view["src_addr"]))
    finally:
        sock.close()
        th.join(timeout=5.0)

    print("== 4. 收尾 ==")
    pp.stop()
    for line in log:
        print("   %s" % line)
    final = pp.status_line()
    print("   %s" % final)
    print("   server 统计: %r" % server.stats)
    if grants:
        print("   切换间隙: min %.3f / max %.3f ms (裸机参考 1.871 ms, FIFO 容量 ~30 ms)"
              % (min(grants) * 1000, max(grants) * 1000))
    print("   PL 视角(账面 bank / DMA 源地址解出的 bank / 源地址):")
    for sw, pl_bank, src in pl:
        print("     切到 bank%d -> DMA 源 0x%08X => bank%s"
              % (sw, src, "?" if pl_bank is None else str(pl_bank)))

    st = pp._user_status()
    ok = (not st["underflow"]) and server.stats["switches"] >= ROUNDS
    if st["underflow"]:
        print("   !! FIFO underflow 置起 —— 消费链断过流")
    # ⚠ 每次切换后, DMA 实际被 arm 的源地址必须就是**软件以为的那个 bank**。不成立
    #   说明我们切的是账面上的 bank, 硬件读的是别处 —— 输出内容与预期无关。
    for sw, pl_bank, src in pl:
        if pl_bank != sw:
            print("   !! 切到 bank%d 但 DMA 源地址 0x%08X 解出 bank%s —— 硬件没按账面切"
                  % (sw, src, pl_bank))
            ok = False
            break
    # 切换顺序必须**交替**(每次都切到刚喂好的那个)。连续切到同一 bank 说明状态机
    # 看错了 fresh —— 那是"读到半成品"的形态, 与裸机门禁 M4 的 SWITCH ORDER 判据同源。
    print("   切换顺序: %s" % (order,))
    if len(order) >= 2:
        for a, b in zip(order, order[1:]):
            if a == b:
                print("   !! 切换顺序没有交替: %s" % (order,))
                ok = False
                break
    if server.stats["switches"] < ROUNDS:
        print("   !! 只观测到 %d 次切换(期望 %d)" % (server.stats["switches"], ROUNDS))
        ok = False
    if pp.error:
        print("   !! 收尾有错 0x%02X" % pp.error)
        ok = False
    print("P5BRESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())