#!/usr/bin/env python3
"""P5 的**核心实时性实验**: Linux 用户态 Python 能不能扛住 DDR 双 bank 的环形重装。

方法(全部走 awg_ctl / awg_ddr_pp, 与真运行时同一代码路径):
  1. CMA 分配两个 1 MiB bank, 各写入已知字节图样;
  2. QAM16 + 400 kSym/s 下启动 bank0(1 MiB 每 bank 约 5.2 s);
  3. 每 2 ms 轮询一次 `service()`: IOC → DRAIN → (另一 bank fresh) 自动重臂;
  4. 测**每轮的 NEXT_SUBMIT_S**(DRAIN 到重臂完成的间隙, 裸机实测 1.871 ms,
     由 ~16 KB AXIS FIFO(≈30 ms 容量)吸收);
  5. 判据 = 与裸机同款: **underflow 位干净** + 几轮切换后 `GET DDR PP` 回 IDLE。

判据解释(为什么不拿绝对时限当判据): AXIS FIFO 能容纳 ~30 ms 的断流, 裸机的
1.871 ms 只是"实际多快"。所以只要 Linux 的调度抖动(偶发几十 ms)**不叠加**在
连续两拍上, FIFO 就能吸收 —— underflow 位就是那个硬件级判 决位。

跑法(板上, 先停旧的 awg_bridge —— 它占着 UART/DMA 视角):
    cd /home/xilinx/overlays/awg_merged
    sudo env XILINX_XRT=/usr PYTHONPATH=/home/xixinx python3 -u p5_ringtest.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import awg_ctl as A
import awg_ddr_pp as P
from pynq import Clocks, MMIO, Overlay, allocate

BITFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "awg_merged.bit")

# 与 scripts/udp_ddr_pp_gate.py M3 相同的播放配置: 1 MiB, QAM16, 100 kSym/s
BANK_BYTES = P.BANK_BYTES


def main():
    print("== 1. 装 overlay 与时钟 ==")
    ov = Overlay(BITFILE)
    Clocks.fclk0_mhz = 50.0
    print("   FCLK0 = %s MHz" % Clocks.fclk0_mhz)

    ctl = A.AwgCtl(A.PynqBackend(ov))
    # ⚠ **先把通道对象取出来存住**。`ov.axi_gpio_7.channel2` 每次访问都会重新构造驱动
    # (实测单次 ~24 ms), 而环形切换的热路径上要走好几次 —— 不缓存就会把切换间隙从
    # 毫秒级抬到上百毫秒(板上实测 174 ms, 远超 AXIS FIFO 的 ~30 ms 容量)。这与 P4 那次
    # "一条 SET 399 ms" 是同一个坑的不同面。
    ch1 = ov.axi_gpio_7.channel1
    ch2 = ov.axi_gpio_7.channel2
    user_status = ov.axi_gpio_8.channel1
    pp = P.DdrPp([0, 0], ov.axi_dma_0.mmio,
                 write_user_ctrl_ch2=lambda w: ch2.write(w & 0xFFFFFFFF, 0xFFFFFFFF),
                 read_user_status_ch1=lambda: user_status.read(),
                 digmod_mode_fn=lambda: ctl.digmod.mode,
                 symrate_fn=lambda: ctl.digmod.symrate_hz)
    pp._write_user_ctrl_ch1 = lambda w: ch1.write(w & 0xFFFFFFFF, 0xFFFFFFFF)

    print("== 2. CMA 分配双 bank 并写入图样 ==")
    # ⚠ 本镜像的 PynqBuffer **不接受 bytes 整段赋值**(内部把 bytes 按 ASCII 解码,
    # 遇 0x80 直接 UnicodeDecodeError), np.frombuffer 出来的 ndarray 则正常。
    import numpy as np
    banks = (allocate(shape=(BANK_BYTES,), dtype="u1"),
             allocate(shape=(BANK_BYTES,), dtype="u1"))
    pp.bank_phys = (banks[0].device_address, banks[1].device_address)
    pp.bank_mem = (memoryview(banks[0]), memoryview(banks[1]))
    for i in (0, 1):
        pattern = np.frombuffer(
            bytes(((i * 37 + j) & 0xFF) for j in range(256)) * (BANK_BYTES // 256),
            dtype="u1")
        banks[i][:] = pattern
        try:
            banks[i].flush()          # HP 口非 cache-coherent: 写侧必须 flush
        except AttributeError:
            pass
        pp.banks[i].bit_length = BANK_BYTES * 8
        pp.banks[i].valid = 1
        pp.banks[i].fresh = 1
        pp.banks[i].owner = P.STAGE_NONE
    print("   bank0 @0x%08X  bank1 @0x%08X" % (pp.bank_phys[0], pp.bank_phys[1]))

    print("== 3. 设 QAM16 / 100 kSym/s 并启动 bank0 ==")
    ctl.handle("SET DIGMOD MODE OFF")
    ctl.handle("SET DIGMOD SYMRATE 400000")   # 1MiB/4bit=2.1M 符号: 400k -> 5.2s/bank
    ctl.handle("SET DIGMOD MODE QAM16")
    if not pp.start(0):
        print("   启动失败: %s (error 0x%02X)" % (pp.state, pp.error))
        return 1
    print("   已启动: %s" % pp.status_line())

    # 1 MiB / QAM16(4bit/符号) @400kSym/s = 5.24 s 每 bank
    switches = 0
    grants = []
    deadline = time.monotonic() + 30.0         # 每个银行 5.2s: 3 次切换约 16s, 给余量
    poll_count = 0
    while time.monotonic() < deadline:
        event, still = pp.service()
        poll_count += 1
        if event == "switched":
            switches += 1
            grants.append(pp.timeline.get("NEXT_SUBMIT_S", -1.0))
            print("   第 %d 次切换: 间隙 %.3f ms" % (switches, grants[-1] * 1000))
            if switches >= 3:
                break
        elif event == "error":
            print("   !! 出错: %s" % pp.status_line())
            return 1
        time.sleep(0.002)

    print("== 4. 停止 ==")
    pp.stop()
    final = pp.status_line()
    print("   %s" % final)
    print("   轮询次数 %d, 切换 %d 次" % (poll_count, switches))

    # 与裸机门禁同款判据: FIFO underflow 必须干净
    st = pp._user_status()
    # 一次切换就够: 两个 bank 都 fresh 时切一次; 之后另一个 bank 不再 fresh,
    # 状态机正确地回 IDLE(没有新数据可放)。持续环形要播放中往非活动 bank 喂数。
    ok = (not st["underflow"]) and switches >= 1
    if grants:
        worst = max(grants)
        print("   切换间隙: min %.3f / max %.3f ms (裸机参考 1.871 ms)"
              % (min(grants) * 1000, worst * 1000))
        if worst > 0.030:
            print("   !! 最差间隙超过 30 ms(FIFO 容量) —— 需要 PL 侧自持重装")
            ok = False
    print("P5RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
