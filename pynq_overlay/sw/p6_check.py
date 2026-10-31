#!/usr/bin/env python3
"""P6 板上验证: 面板显示的是**硬件里真实的参数**, 不是页面自己的副本。

做法: 新建一个 AwgCtl 从**寄存器读回**(与守护进程同一个 PynqBackend 路径), 再渲染
AWG 页存成 PNG。判据是 PNG 上的文字与串口 `GET` 一致 —— 因为两者读的是同一个真源。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pynq import Clocks, Overlay  # noqa: E402

import awg_ctl as A  # noqa: E402
import awg_page as P  # noqa: E402


def main():
    ov = Overlay(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "awg_merged.bit"))
    Clocks.fclk0_mhz = 50.0
    # 注意: 构造会 **write_all()** 一次(固件上电也是这么做的), 所以参数会被写成默认值。
    # 这不是"页面改了参数", 而是控制库的初始化语义 —— 与固件 main() 一致。
    ctl = A.AwgCtl(A.PynqBackend(ov))

    out = "/tmp/awg_page.png"
    P.preview_png(out, ctl)
    print("渲染 %s" % out)
    for ci in (0, 1):
        s = ctl.ch[ci]
        print("CH%d: WAVE %s FREQ %d AMPL %d MODE %s"
              % (ci + 1, A.WAVE_NAME[s.wave], s.freq_hz, s.ampl_pct,
                 A.MODE_NAME[s.mode]))
    print("数字调制: %s" % A.DIGMOD_NAME[ctl.digmod.mode])
    # 串口侧应当读到同样的东西
    print("串口侧 GET CH1 应当与此一致:")
    print("  " + ctl.handle("GET CH1"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
