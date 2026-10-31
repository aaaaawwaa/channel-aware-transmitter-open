#!/usr/bin/env python3
"""把六页面板渲成 PNG —— 离线就能核对版式, 不接板子也不用触摸。

两个用途:

1. **改版式之后核对**。本工程的版式错误(按钮出屏、标签被控件盖住、通道选择被波形网格
   盖住)全都只有渲成图才看得出来, 而 `tests/test_ui_view.py` 里的断言只覆盖"能自动判的
   那几条"。改了 `ui_view.py` 的坐标就重跑一遍, 逐张看。
2. **板端出问题时对比**。同一份代码在开发机与板子上应当画出逐字节相同的图(字体差异除
   外), 所以"板上显示的怪"可以拿开发机的图当基准。

跑法:
    python3 panel_preview.py /tmp/panel        # 六页 + 键盘弹窗各一张
    python3 panel_preview.py /tmp/panel --board # 接板子: 用真寄存器回读的值画

不用板子时画的是**出厂默认参数**; 想看别的值就改下面 `_demo_app()` 里的草稿。
离线模式下状态页只有顶栏/导航/底栏 —— 那一页的内容属于守护进程(网络/写卡/关机进度),
它在 `pynq_status.py` 里, 用 `python3 pynq_status.py --preview /tmp/status.png` 看。

⚠ `--board` 会自己 `Overlay()` 一次 —— **必须先停守护进程**(`sudo systemctl stop
  pynq-status`), 两个进程同时装 PL 会把板子卡死(见技能 `ac820-pynq` 的"Never call
  Overlay() while a daemon holds the PL", 实测踩过)。另外 `AwgCtl.__init__` 会
  `write_all()` 一次(与固件上电一致), 所以它**会把参数写成默认值** —— 只想看版式就别带
  `--board`。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_model as M  # noqa: E402
import ui_view as V  # noqa: E402


def _fake_lcd():
    import numpy as np
    from pynq_lcd import HEIGHT, WIDTH

    class Lcd(object):
        address = 0

        def __init__(self):
            self.array = np.zeros((HEIGHT, WIDTH), dtype=np.uint16)

        def flush(self):
            pass

        def enable(self):
            pass

    return Lcd()


def _demo_app():
    """离线预览用的状态: 都改成**能看到东西**的值(而不是全零)。"""
    app = M.UiApp()
    cfg = M.default_config()
    cfg["awg"][0].update({"wave": "SINE", "freq_hz": 100000, "ampl_pct": 50})
    cfg["awg"][1].update({"wave": "SQUARE", "freq_hz": 5000, "ampl_pct": 80})
    cfg["dig"].update({"mode": "QPSK", "symrate_hz": 100000, "filter": "RRC",
                       "rolloff": "0.35", "seed": 127})
    cfg["chan"][0].update({"enabled": True, "noise_pct": 25,
                           "taps": [{"gain_q8": 255, "delay_samples": 0},
                                    {"gain_q8": -100, "delay_samples": 20},
                                    {"gain_q8": 0, "delay_samples": 0},
                                    {"gain_q8": 0, "delay_samples": 0}]})
    app.set_observed(cfg, "OFF")
    app.log = ["SET CH1 FREQ 100000", "SET CH1 WAVE SINE", "GET CH1",
               "SET DIGMOD FILTER RRC", "SET CH1 CHAN COMMIT"]
    return app


def preview(path, board=False, ctl=None):
    """把每页各存一张 PNG, 再把键盘弹窗存一张。返回写出的文件名列表。"""
    if board:
        from pynq import Clocks, Overlay
        import awg_ctl as A
        here = os.path.dirname(os.path.abspath(__file__))
        ov = Overlay(os.path.join(here, "awg_merged.bit"))
        Clocks.fclk0_mhz = 50.0
        ctl = A.AwgCtl(A.PynqBackend(ov))
        app = M.UiApp()
        # 构造会 write_all() 一次(与固件上电一致), 所以这里读到的是默认参数
        app.set_observed(ctl.read_registers(), "OFF")
    else:
        app = _demo_app()

    lcd = _fake_lcd()
    view = V.UiView(lcd, app, ctl=ctl)
    out = []
    for page in M.PAGES:
        app.page = page
        view.invalidate()
        view.render()
        name = "%s_%s.png" % (path, page)
        _save(lcd.array, name)
        out.append(name)
    # 键盘弹窗: 在波形页按一下频率行
    app.page = M.PAGE_AWG
    view.invalidate()
    view.render()
    view.keypad.open_for("频率", "100000", "整数范围 10 ~ 1000000 Hz",
                         {"min": M.FREQ_MIN, "max": M.FREQ_MAX,
                          "set": lambda v: None})
    view.invalidate()
    view.render()
    name = "%s_keypad.png" % path
    _save(lcd.array, name)
    out.append(name)
    return out


def _save(array, name):
    from pynq_lcd_ui import save_png
    save_png(array, name)
    print("wrote %s" % name)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    path = args[0] if args else "/tmp/panel"
    preview(path, board="--board" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
