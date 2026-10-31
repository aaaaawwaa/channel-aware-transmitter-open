#!/usr/bin/env python3
"""证明 `GET PLAY` 的读路径**真的连着硬件** —— 不是又一个恒真判据。

上一版 `check_play_userbuf.py` 在静止态下 ALL PASS, 但那时 `axi_gpio_10` 恰好读 0,
**0 解出来就等于基线桩** —— 所以它分不出"读了硬件"和"回硬编码"。这正是本项目反复踩的
"从未失败过的断言要先证明它**能**失败"。

办法: 直接写 PLAY 的控制字(`axi_gpio_9` ch1, 输出), 让 FSM 离开 IDLE, 再看 `GET PLAY`
是否跟着变。CMD_ARM 无论成功(→ARMED)还是失败(→ERROR + error_code)都会改变状态字, 所以
只要它变了就证明读路径是真的。

⚠ 只 ARM / CLEAR, 不 START —— 不动 DAC 输出。跑完 CLEAR 回 IDLE。

    sudo systemctl stop pynq-status
    cd /home/xilinx/overlays/awg_merged && sudo env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 prove_play_read.py
    sudo systemctl restart pynq-status
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED = []


def check(ok, what, detail=""):
    print("%s  %s%s" % ("PASS" if ok else "FAIL", what, ("  | " + detail) if detail else ""))
    if not ok:
        FAILED.append(what)
    return ok


# `main.c:583-590` play_ctrl_word 的位域
CMD_ARM, CMD_CLEAR = 1, 4


def ctrl_word(manual, cmd, epoch):
    return ((1 if manual else 0) | ((cmd & 7) << 1) | ((epoch & 1) << 4))


def main():
    from pynq import Clocks, Overlay
    import awg_ctl as A
    import awg_bridge

    ov = Overlay(awg_bridge.DEFAULT_BITFILE)
    Clocks.fclk0_mhz = 50.0
    ctl = A.AwgCtl(A.PynqBackend(ov))
    play_ctrl = ov.axi_gpio_9.channel1

    def write_ctrl(w):
        play_ctrl.write(w & 0xFFFFFFFF, 0xFFFFFFFF)

    def play_state():
        line = ctl.handle("GET PLAY")
        return line, (line.split()[2] if line.startswith("PLAY") else line)

    # ---- 基线 ----
    line0, st0 = play_state()
    print("   基线        -> %s" % line0)

    # ---- ARM: 让 FSM 离开 IDLE ----
    write_ctrl(ctrl_word(manual=1, cmd=CMD_ARM, epoch=0))
    time.sleep(0.3)
    line1, st1 = play_state()
    print("   ARM 之后    -> %s" % line1)

    # 命令 epoch 是"拨动才认"的, 所以再来一次(翻 epoch)确保 FSM 收到
    write_ctrl(ctrl_word(manual=1, cmd=CMD_ARM, epoch=1))
    time.sleep(0.3)
    line2, st2 = play_state()
    print("   ARM(翻epoch) -> %s" % line2)

    changed = (line1 != line0) or (line2 != line0)
    check(changed, "ARM 之后 GET PLAY **变了** —— 读路径真的连着硬件",
          "%s -> %s" % (st0, st2))
    check(st1 in A.PLAY_STATE_NAME or st2 in A.PLAY_STATE_NAME,
          "状态仍是合法名(没回 ERR PLAY STATUS)", "%s / %s" % (st1, st2))
    # ARM 要么进 ARMED, 要么因前置条件不满足进 ERROR(带 error_code) —— 两者都算"硬件动了"
    check(st1 in ("ARMED", "ERROR") or st2 in ("ARMED", "ERROR"),
          "ARM 把 FSM 推到了 ARMED 或 ERROR", "%s / %s" % (st1, st2))

    # ---- CLEAR: 收回 IDLE ----
    write_ctrl(ctrl_word(manual=1, cmd=CMD_CLEAR, epoch=0))
    time.sleep(0.3)
    write_ctrl(ctrl_word(manual=1, cmd=CMD_CLEAR, epoch=1))
    time.sleep(0.3)
    line3, st3 = play_state()
    print("   CLEAR 之后  -> %s" % line3)
    check(st3 == "IDLE", "CLEAR 把 FSM 收回 IDLE", st3)

    # 收尾: 控制字清 0, 别把 manual_mode 留着
    write_ctrl(0)
    time.sleep(0.1)

    print("")
    if FAILED:
        print("**%d 项 FAIL**: %s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("ALL PASS —— GET PLAY 读的是真寄存器")
    return 0


if __name__ == "__main__":
    sys.exit(main())