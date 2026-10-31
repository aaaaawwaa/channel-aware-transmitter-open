#!/usr/bin/env python3
"""板上验收: `GET PLAY` / `GET USERBUF` 是否回**真值**(不再是硬编码桩)。

⚠ 先停守护进程(两个进程同时 Overlay() 会把板子卡死):
    sudo systemctl stop pynq-status
    cd /home/xilinx/overlays/awg_merged && sudo env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 check_play_userbuf.py
    sudo systemctl restart pynq-status

判据:
  1. 两个回包的**形状**与固件格式串逐字一致(host 侧解析器认得)。
  2. `GET PLAY` 的 STATE 是 7 个合法名之一; 若是 `ERR PLAY STATUS` 说明状态字越界(真问题)。
  3. `GET USERBUF` 的 READY/DONE/UNDERFLOW/BANK 来自 `axi_gpio_8` —— 与**独立**从
     `/dev/mem` mmap 读同一个寄存器解出来的位**逐个相同**(不靠自己的解码自证)。
  4. `GET PLAY` 的八个字段与独立读 `axi_gpio_10` 的解码一致。

第 3/4 条是关键: 它们把"我们的 Python 解码"与"寄存器里的原始值"分开取, 于是回包说错话
(桩/取错寄存器/位域错位)都会被抓住。
"""
import os
import sys
import mmap
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED = []


def check(ok, what, detail=""):
    print("%s  %s%s" % ("PASS" if ok else "FAIL", what, ("  | " + detail) if detail else ""))
    if not ok:
        FAILED.append(what)
    return ok


def mmap_gpio(base):
    """只读 mmap 一个 AXI GPIO 的寄存器页 —— **不抢 PL**, 可与守护进程共存。

    AXI GPIO 寄存器: ch1 DATA = 0x00(只读通道读它), ch2 DATA = 0x08。
    """
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=base)
    return fd, mm


def read_word(mm, off=0x00):
    return struct.unpack("<I", mm[off:off + 4])[0]


def main():
    import awg_ctl as A
    import awg_bridge

    ov = None
    from pynq import Clocks, Overlay
    ov = Overlay(awg_bridge.DEFAULT_BITFILE)
    Clocks.fclk0_mhz = 50.0
    ctl = A.AwgCtl(A.PynqBackend(ov))

    # ---- 从 .hwh 里取两个状态 GPIO 的物理基址(不靠猜) ----
    # ⚠ `MEMRANGE` 在 `<MEMORYMAP>` 里, **不在** `<MODULE>` 块内 —— 按 MODULE 去搜会
    #   一无所获(第一版就是这么错的), 必须全局搜 `INSTANCE=...`.
    import re
    hwh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "awg_merged.hwh")
    text = open(hwh, encoding="utf-8", errors="replace").read()

    def base_of(instance):
        m = re.search(r'<MEMRANGE[^>]*INSTANCE="%s"[^>]*>' % instance, text)
        if not m:
            return None
        mm = re.search(r'BASEVALUE="([^"]*)"', m.group(0))
        return int(mm.group(1), 16) if mm else None

    b8, b10 = base_of("axi_gpio_8"), base_of("axi_gpio_10")
    check(b8 is not None and b10 is not None, "从 .hwh 取到两个状态 GPIO 的基址",
          "gpio8=0x%X gpio10=0x%X" % (b8 or 0, b10 or 0))

    # ---- 1. 形状 ----
    line_play = ctl.handle("GET PLAY")
    line_ub = ctl.handle("GET USERBUF")
    print("   GET PLAY    -> %s" % line_play)
    print("   GET USERBUF -> %s" % line_ub)

    if line_play == "ERR PLAY STATUS":
        check(False, "GET PLAY 没回 ERR PLAY STATUS(状态字越界)", line_play)
        return 1
    p = line_play.split()
    check(len(p) == 17 and p[0] == "PLAY", "GET PLAY 是 17 个 token 的 PLAY 行",
          "%d tokens" % len(p))
    check(p[2] in A.PLAY_STATE_NAME, "PLAY STATE 是合法状态名", p[2])
    check(p[1::2] == ["STATE", "FRAME_DONE", "UNDERFLOW", "TRIGGER_SEEN",
                      "STOP_PENDING", "ERROR_VALID", "REPEAT_REMAINING", "ERROR"],
          "GET PLAY 的字段名与固件顺序一致", repr(p[1::2]))

    u = line_ub.split()
    check(len(u) == 21 and u[0] == "USERBUF", "GET USERBUF 是 21 个 token 的 USERBUF 行",
          "%d tokens" % len(u))
    check(u[1::2] == ["VALID", "LENGTH", "CRC", "BANK", "UPLOAD", "LOOP",
                      "COMMIT_SEEN", "READY", "DONE", "UNDERFLOW"],
          "GET USERBUF 的字段名与固件顺序一致", repr(u[1::2]))

    # ⚠ host 侧解析器(`host/siggen/protocol.py`)的回程验证**不在这里** —— 板上没有
    #   `siggen` 包(它在上位机仓库那一半)。那条判据在开发机上跑:
    #       python -c "from siggen.protocol import parse_play_status; ..."
    #   已经过了: 7 个 PLAY 状态名全部能被 host 的 `PLAY_STATES` 解析, 两行都能解析成
    #   与固件同构的 dict。这里只判**板端自己的**形状(上面那两条 token 检查)。

    # ---- 2. 与独立 mmap 的寄存器值逐个对比(不信自己的解码) ----
    if b8 is not None and b10 is not None:
        fd8, mm8 = mmap_gpio(b8)
        fd10, mm10 = mmap_gpio(b10)
        try:
            raw8, raw10 = read_word(mm8), read_word(mm10)
        finally:
            mm8.close(); os.close(fd8)
            mm10.close(); os.close(fd10)
        print("   raw axi_gpio_8  ch1 = 0x%08X" % raw8)
        print("   raw axi_gpio_10 ch1 = 0x%08X" % raw10)

        check(int(p[2] == A.PLAY_STATE_NAME[raw10 & 0x7]),
              "PLAY STATE 与 raw 状态字低 3 位一致",
              "回包 %s / raw %d" % (p[2], raw10 & 0x7))
        check(int(p[4]) == ((raw10 >> 3) & 1), "FRAME_DONE 与 raw bit3 一致", p[4])
        check(int(p[6]) == ((raw10 >> 4) & 1), "UNDERFLOW 与 raw bit4 一致", p[6])
        check(int(p[8]) == ((raw10 >> 5) & 1), "TRIGGER_SEEN 与 raw bit5 一致", p[8])
        check(int(p[10]) == ((raw10 >> 6) & 1), "STOP_PENDING 与 raw bit6 一致", p[10])
        check(int(p[12]) == ((raw10 >> 7) & 1), "ERROR_VALID 与 raw bit7 一致", p[12])
        check(int(p[14]) == ((raw10 >> 8) & 0xFFFF), "REPEAT_REMAINING 与 raw 一致", p[14])
        check(int(p[16], 16) == ((raw10 >> 24) & 0xFF), "ERROR 与 raw 一致", p[16])

        check(int(u[8]) == ((raw8 >> 4) & 1), "USERBUF BANK 与 raw bit4 一致",
              "回包 %s / raw %d" % (u[8], (raw8 >> 4) & 1))
        check(int(u[16]) == ((raw8 >> 3) & 1), "USERBUF READY 与 raw bit3 一致", u[16])
        check(int(u[18]) == ((raw8 >> 1) & 1), "USERBUF DONE 与 raw bit1 一致", u[18])
        check(int(u[20]) == ((raw8 >> 2) & 1), "USERBUF UNDERFLOW 与 raw bit2 一致", u[20])

    # ---- 3. 两次连读必须一致(静止态, 不是随机值) ----
    again = ctl.handle("GET PLAY")
    check(again == line_play, "PLAY 静止态连读一致", repr(again))

    print("")
    if FAILED:
        print("**%d 项 FAIL**: %s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())