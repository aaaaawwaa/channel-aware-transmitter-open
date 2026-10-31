#!/usr/bin/env python3
"""把**真的会画到屏上**的每一串字过一遍"有没有字形", 没有就报出来。

为什么需要这个: 字体缺字形时 PIL 画的是**豆腐块**, 而豆腐块是非空的 —— 所以"画出来了"
根本看不出问题。本工程踩过四次(按钮上的减号 U+2212、`α`、参考设计的导航图标
`∿ ⋈ ≋ ⌁ ⚙ ◉`、以及 `·`/`—`), **每次都是上屏才发现**。

为什么不能只靠离线测试: 开发机与板子的字体集不同 —— 板子上唯一带中文的是
`DroidSansFallbackFull.ttf`, 它**没有 ASCII/Latin-1** 字形, 于是 `·`(U+00B7)、
`—`(U+2014)、`×`(U+00D7)在这台机器上有、在板子上没有。所以**必须在板上也跑一遍**。

跑法(离线与板上都可以, 板上不需要 root, 也不碰 overlay):
    python3 glyph_check.py

顺带查**中文字号**: 字形有着落、但小到糊成一团也是不能看的(板上实测 11px 的中文就是
一坨), 判据是 `pynq_lcd_ui.MIN_CJK_SIZE`。这条**只能按板子定** —— 开发机的 msyh 在
小字号下清楚得多, 所以它在板上跑才算数(量法见 `font_probe.py`)。

退出码非 0 表示有字会画成豆腐块, 或有中文画得比可读下限还小。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pynq_lcd_ui as ui  # noqa: E402
import ui_model as M  # noqa: E402
import ui_view as V  # noqa: E402


class _Lcd(object):
    """只要一块数组 —— 这个检查不碰帧缓冲、不碰 overlay, 所以板上不用 sudo。"""

    address = 0

    def __init__(self):
        import numpy as np
        from pynq_lcd import HEIGHT, WIDTH
        self.array = np.zeros((HEIGHT, WIDTH), dtype=np.uint16)

    def flush(self):
        pass

    def enable(self):
        pass


def _status_screen(lcd):
    """守护进程那套状态屏(它自己画一批字: LED 图例、写卡告警、长按提示...)。

    开发机上 import 不了(要假 pynq), 那就跳过并明说 —— 不要静默少查一批字。
    """
    try:
        import pynq_status
    except Exception as exc:                         # noqa: BLE001
        print("跳过状态屏(%s: %s)" % (type(exc).__name__, exc))
        return None
    try:
        return pynq_status.StatusScreen(lcd)
    except Exception as exc:                         # noqa: BLE001
        print("状态屏建不起来(%s: %s)" % (type(exc).__name__, exc))
        return None


def collect(state=None):
    """收出所有会画到屏上的文字 `[(字符串, 字号, 来自)]`(去重)。"""
    from pynq_lcd_ui import drawn_text, set_text_log

    lcd = _Lcd()
    app = M.UiApp()
    app.set_observed(M.default_config(), "OFF")
    app.log = ["SET CH1 FREQ 100000", "GET DIGMOD"]     # 系统页的日志行也要查
    view = V.UiView(lcd, app)
    out = list(V.strings_drawn(view, state))

    scr = _status_screen(lcd)
    if scr is not None:
        # 状态屏的文字分好几种状态才出现(写卡告警、关机提示、准星读数),
        # 只渲一个默认状态会漏 —— 实测"正在关机 — 请等待"就漏过去了。
        for st, cursor in _status_states():
            sink = []
            set_text_log(sink)
            try:
                scr.cursor = cursor
                scr.invalidate()
                scr.render(st)
            finally:
                set_text_log(None)
            out.extend((s, size, "status-screen") for s, size, _x, _y in sink)

    seen = set()
    unique = []
    for s, size, src in out:
        if (s, size) in seen:
            continue
        seen.add((s, size))
        unique.append((s, size, src))
    return unique


class _FakeCursor(object):
    """让状态屏走"有触摸"那条分支(它画"触摸 N, M"与读失败计数)。"""

    errors = 0

    def read(self):
        return (100, 200, True)


def _status_states():
    """状态屏的几种状态各配一个读得到的准星 —— 文字是按状态分支画的。"""
    import pynq_status

    def base(**kw):
        st = pynq_status.Status()
        st.ip = "192.0.2.10/24"
        st.jupyter = True
        for k, v in kw.items():
            setattr(st, k, v)
        return st, _FakeCursor()

    return [
        base(),
        base(disk_active=True, disk_rate=2.5 * 1024 * 1024, disk_loud=True),
        base(disk_active=True, disk_rate=200 * 1024),
        base(held=1.2),
        base(shutting_down=True),
        base(load=3.5),
    ]


def _has_cjk(s):
    """串里有没有中日韩的表意字或全角标点(ASCII 不受字号下限约束)。"""
    return any(0x2E80 <= ord(ch) <= 0x9FFF or 0xFF00 <= ord(ch) <= 0xFFEF
               for ch in s)


def main():
    items = collect()
    bad = [(s, size, src) for s, size, src in items if not ui.has_glyphs(s, size)]
    # **字号**是另一回事: 字形在, 但没有足够的像素画这些笔画 —— 板上 11px 的中文糊成一团。
    # 这条同样**只能在板上判**(开发机的 msyh 小字号下清楚得多), 所以并到这个板上工具里。
    small = [(s, size, src) for s, size, src in items
             if _has_cjk(s) and size < ui.MIN_CJK_SIZE]
    print("查了 %d 串字(去重), 中文字号下限 = %d" % (len(items), ui.MIN_CJK_SIZE))
    if not bad and not small:
        print("全部有字形、且不小于可读下限")
        return 0
    if bad:
        print("**没有字形的 %d 串**:" % len(bad))
        for s, size, src in bad:
            print("  [%s] size=%d %r" % (src, size, s))
    if small:
        print("**中文字号小于 %d 的 %d 串**:" % (ui.MIN_CJK_SIZE, len(small)))
        for s, size, src in small:
            print("  [%s] size=%d %r" % (src, size, s))
    return 1


if __name__ == "__main__":
    sys.exit(main())
