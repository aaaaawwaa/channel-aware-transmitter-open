#!/usr/bin/env python3
"""AWG 控制页: 在面板上改信号发生器的参数(P6)。

设计约束(来自这条线前几步的教训):

* **状态只有一个真源**: 本页与 UART 协议桥读写**同一个** `awg_ctl.AwgCtl` 实例。
  页面上点一下 = 走一遍命令解析 = 写一遍寄存器, 所以串口那边 `GET` 回读必然一致
  (这是 P6 的验收判据)。
* **不要各存一份参数**: 页面上的每个显示值都从 `AwgCtl` 读, 不自己缓存。
* **一帧只读一次触摸**: 触摸由守护进程统一读(`TouchCursor`), 本页只消费。

页面布局(800x480)按"手指能点准"来: 按钮最小 64x44, 参数行用 −/+ 步进而不是输入框
(触摸屏上没有键盘, 数字键盘又要占半屏)。
"""
import pynq_lcd_ui as ui

import awg_ctl as A

# 参数行定义: (标签, 取值, 设值, 步进)。取值/设值都走 AwgCtl, 不缓存。
WAVE_CYCLE = A.WAVE_NAME
MODE_CYCLE = A.MODE_NAME


class AwgPage(object):
    """AWG 控制页 —— 与 `StatusScreen` 同接口(`render(state)` + `.ui`), 可互换。

    参数直接落在 `AwgCtl` 上: `_set()` 走的就是协议解析那条路(`handle`), 所以
    "面板改的"与"串口改的"在语义上完全等价, 没有第二条写入路径。
    """

    def __init__(self, lcd, ctl, on_switch=None, touch_cursor=None):
        self.lcd = lcd
        self.ctl = ctl
        self.on_switch = on_switch          # 点"状态页"时回调
        self.cursor = touch_cursor
        self.ui = ui.Ui(lcd, background=0x0861, shadow=True,
                        source=self.cursor.source() if self.cursor else None)
        self._enabled = False
        self._last_sig = None
        self._notice = ""
        self._notice_until = 0.0
        # 数字调制按钮的目标模式游标(UI 状态, 不是参数副本 —— 见 _digmod_tap)
        self._dig_target = 0
        self._build()

    # ---- 布局 ----

    def _build(self):
        u = self.ui
        u.add(ui.Label(20, 10, "信号发生器控制", size=26, fg=ui.WHITE))
        u.add(ui.Button(600, 12, 180, 40, "状态页", on_tap=self._to_status, size=18))

        # 两个通道各一组: 波形 / 调制 / 频率 / 幅度。
        # ⚠ 坐标是**按 800 宽算过**的(20..780 是可用区): 早先最后一列 "+" 放在 772,
        #   加上 56 宽就出了屏 —— 渲成 PNG 才看出来。改版式后请重跑 preview_png 核对。
        # ⚠ 按钮文字用 **ASCII 的 "-"**, 不用 U+2212 减号: 板上字体没有那个字形, 会变豆腐块
        #   (同一个坑在"α"上也踩到, 所以滚降那格写 "r 0.35")。
        self.wave_btn = {}
        self.freq_lbl = {}
        self.ampl_lbl = {}
        self.mode_btn = {}
        for ci, name in ((0, "CH1"), (1, "CH2")):
            y = 62 + ci * 116
            u.add(ui.Label(20, y, name, size=24, fg=ui.ACCENT))
            self.wave_btn[ci] = u.add(ui.Button(
                78, y - 4, 120, 44, "—", on_tap=self._wave_tap(ci), size=18))
            self.mode_btn[ci] = u.add(ui.Button(
                206, y - 4, 120, 44, "—", on_tap=self._mode_tap(ci), size=18))
            # 频率组(336..534) 与 幅度组(572..760) 之间留 38 px 空隙, 免得两组看着像一组
            u.add(ui.Button(336, y - 4, 48, 44, "-", on_tap=self._freq_tap(ci, -1), size=24))
            self.freq_lbl[ci] = u.add(ui.Label(388, y + 4, "", size=20, fg=ui.WHITE))
            u.add(ui.Button(486, y - 4, 48, 44, "+", on_tap=self._freq_tap(ci, +1), size=24))
            u.add(ui.Button(572, y - 4, 48, 44, "-", on_tap=self._ampl_tap(ci, -5), size=24))
            self.ampl_lbl[ci] = u.add(ui.Label(624, y + 4, "", size=20, fg=ui.WHITE))
            u.add(ui.Button(712, y - 4, 48, 44, "+", on_tap=self._ampl_tap(ci, +5), size=24))

        # 数字调制一行
        y = 296
        u.add(ui.Label(20, y, "数字调制", size=24, fg=ui.ACCENT))
        self.dig_btn = u.add(ui.Button(148, y - 4, 140, 44, "—",
                                       on_tap=self._digmod_tap(), size=18))
        u.add(ui.Button(296, y - 4, 140, 44, "RRC/RECT",
                        on_tap=self._filter_tap(), size=16))
        self.roll_btn = u.add(ui.Button(444, y - 4, 120, 44, "r 0.35",
                                        on_tap=self._rolloff_tap(), size=16))
        self.sym_lbl = u.add(ui.Label(572, y + 4, "", size=18, fg=ui.WHITE))
        u.add(ui.Button(700, y - 4, 48, 44, "+", on_tap=self._symrate_tap(+1), size=24))

        # 全局: 复位成默认 + 提示行
        # ⚠ 这里**没有"两路开/关"按钮**: 协议里没有"通道开关"这条命令 —— `SET CHx MODE`
        # 是**调制模式**(OFF/AM/FM/PM/SWEEP), 不是通断。面板不能凭空造出协议里没有的语义,
        # 所以给的是"复位到默认态"这种确实存在、也确实常用的组合操作。
        y = 360
        u.add(ui.Button(20, y, 170, 46, "复位默认", on_tap=self._reset_tap(), size=18))
        self.notice = u.add(ui.Label(210, y + 10, "", size=16, fg=ui.GREY))
        self.cross = u.add(ui.Crosshair(self.lcd.array.shape[1],
                                        self.lcd.array.shape[0]))

    # ---- 回调: 全部经 ctl.handle(), 没有第二条写入路径 ----

    def _cmd(self, text):
        """走一遍命令解析。回包是 `ERR ...` 就挂到提示行上 —— 与串口侧看到的一致。"""
        reply = self.ctl.handle(text) or ""
        if reply.startswith("ERR"):
            self._notice = reply
            import time
            self._notice_until = time.monotonic() + 3.0
        return reply

    def _wave_tap(self, ci):
        def tap(_b):
            cur = self.ctl.ch[ci].wave
            self._cmd("SET CH%d WAVE %s" % (ci + 1, WAVE_CYCLE[(cur + 1) % len(WAVE_CYCLE)]))
        return tap

    def _mode_tap(self, ci):
        def tap(_b):
            cur = self.ctl.ch[ci].mode
            self._cmd("SET CH%d MODE %s" % (ci + 1, MODE_CYCLE[(cur + 1) % len(MODE_CYCLE)]))
        return tap

    def _freq_tap(self, ci, sign):
        def tap(_b):
            cur = self.ctl.ch[ci].freq_hz
            # 按数量级步进: 低频细调, 高频粗调(触摸屏上按一次跨十个数量级不现实)
            step = 100 if cur < 1000 else (1000 if cur < 100000 else 10000)
            self._cmd("SET CH%d FREQ %d" % (ci + 1, max(A.FREQ_MIN,
                                                      min(A.FREQ_MAX, cur + sign * step))))
        return tap

    def _ampl_tap(self, ci, delta):
        def tap(_b):
            cur = self.ctl.ch[ci].ampl_pct
            self._cmd("SET CH%d AMPL %d" % (ci + 1, max(0, min(A.AMPL_MAX, cur + delta))))
        return tap

    def _digmod_tap(self):
        def tap(_b):
            # ⚠ 不能只看"当前模式"来推下一个: 改 FILTER/SYMRATE 前必须 MODE OFF, 而 OFF
            # 之后"当前模式"就是 0, 于是永远在 OFF<->BPSK 之间跳, 够不到 QPSK/QAM。
            # 所以用面板自己的**目标模式**做游标 —— 它是 UI 状态, 不是参数的副本。
            self._dig_target = (self._dig_target + 1) % len(A.DIGMOD_NAME)
            self._cmd("SET DIGMOD MODE OFF")
            self._cmd("SET DIGMOD MODE %s" % A.DIGMOD_NAME[self._dig_target])
        return tap

    def _filter_tap(self):
        def tap(_b):
            self._cmd("SET DIGMOD MODE OFF")
            self._cmd("SET DIGMOD FILTER %s"
                      % A.DIGMOD_FILTER_NAME[1 - self.ctl.digmod.filter])
        return tap

    def _rolloff_tap(self):
        def tap(_b):
            self._cmd("SET DIGMOD MODE OFF")
            cur = self.ctl.digmod.rolloff
            # 0.35 -> 0.2 -> 0.5 -> 0.35(索引序, 与固件的编码一致)
            order = [0, 1, 2]
            self._cmd("SET DIGMOD ROLLOFF %s"
                      % A.DIGMOD_ROLLOFF_NAME[order[(order.index(cur) + 1) % 3]])
        return tap

    def _symrate_tap(self, sign):
        def tap(_b):
            cur = self.ctl.digmod.symrate_hz
            step = 10000 if cur < 100000 else 100000
            self._cmd("SET DIGMOD MODE OFF")
            self._cmd("SET DIGMOD SYMRATE %d" % max(10, min(1000000, cur + sign * step)))
        return tap

    def _reset_tap(self):
        """复位到默认态 —— 用的都是协议里真实存在的命令(面板不造语义)。"""
        def tap(_b):
            for text in ("SET CH1 MODE OFF", "SET CH2 MODE OFF",
                         "SET CH1 WAVE SINE", "SET CH2 WAVE SINE",
                         "SET CH1 FREQ 1000", "SET CH2 FREQ 1000",
                         "SET CH1 AMPL 50", "SET CH2 AMPL 50",
                         "SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT",
                         "SET DIGMOD ROLLOFF 0.35", "SET DIGMOD SEED 127"):
                self._cmd(text)
        return tap

    def _to_status(self, _b):
        if self.on_switch is not None:
            self.on_switch()

    def invalidate(self):
        """切回本页时调用: 清掉"上一帧签名", 强制重画一次。

        与状态页同一个理由: 不清的话, 切回来时若内容恰好没变, `render` 会以为已经画过而
        直接返回, 面板上还留着另一页的画面。
        """
        self._last_sig = None

    # ---- 渲染 ----

    def _readback(self):
        """**从 AwgCtl 读**(不是从控件读) —— 这是"唯一真源"的具体含义。"""
        return {
            "wave": [self.ctl.ch[i].wave for i in (0, 1)],
            "mode": [self.ctl.ch[i].mode for i in (0, 1)],
            "freq": [self.ctl.ch[i].freq_hz for i in (0, 1)],
            "ampl": [self.ctl.ch[i].ampl_pct for i in (0, 1)],
            "dig": self.ctl.digmod.mode,
            "filter": self.ctl.digmod.filter,
            "rolloff": self.ctl.digmod.rolloff,
            "symrate": self.ctl.digmod.symrate_hz,
            "notice": self._notice,
        }

    def render(self, state=None):
        import time
        r = self._readback()
        if self._notice and time.monotonic() > self._notice_until:
            self._notice = ""
            r["notice"] = ""
        for ci in (0, 1):
            self.wave_btn[ci].text = A.WAVE_NAME[r["wave"][ci]]
            self.mode_btn[ci].text = "MOD " + A.MODE_NAME[r["mode"][ci]]
            self.freq_lbl[ci].text = _hz(r["freq"][ci])
            self.ampl_lbl[ci].text = "%d%%" % r["ampl"][ci]
        self.dig_btn.text = A.DIGMOD_NAME[r["dig"]]
        self.roll_btn.text = "r " + A.DIGMOD_ROLLOFF_NAME[r["rolloff"]]
        self.notice.text = r["notice"] or ("r %s  符号率 %s"
                                          % (A.DIGMOD_ROLLOFF_NAME[r["rolloff"]],
                                             _hz(r["symrate"])))
        self.notice.fg = ui.RED if r["notice"] else ui.GREY
        self.sym_lbl.text = _hz(r["symrate"])

        if self.cursor is not None:
            point = self.cursor.read()
            # 抬手那一帧是 (x, y, False): 准星收起, 但这一帧必须照常走 ui.step() ——
            # 控件靠 down=False 合成抬起沿才会触发 on_tap(见 pynq_touch.TouchInput.poll)
            self.cross.point = point[:2] if point and point[2] else None

        sig = (tuple(r["wave"]), tuple(r["mode"]), tuple(r["freq"]), tuple(r["ampl"]),
               r["dig"], r["filter"], r["rolloff"], r["symrate"], r["notice"],
               self.cross.point)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        self.ui.step()
        if not self._enabled:
            self.ui.lcd.enable()
            self._enabled = True


def _hz(v):
    """频率写成人看的样子(触摸屏上宽度有限)。"""
    if v >= 1000000:
        return "%.2f MHz" % (v / 1e6)
    if v >= 1000:
        return "%.1f kHz" % (v / 1e3)
    return "%d Hz" % v

def preview_png(path, ctl=None):
    """把 AWG 页渲成一张 PNG —— 不接板子就能看版式(与状态页的 `preview_png` 同一个用法)。

    开发机上也能跑: 用假 LCD 铺一屏 800x480, 画完存成 PNG 看排版对不对。
    """
    import numpy as np
    from PIL import Image

    class _Buf(object):
        def __init__(self, shape):
            self.array = np.zeros(shape, dtype=np.uint16)

        def flush(self):
            pass

        def enable(self):
            pass

    class _Lcd(object):
        def __init__(self):
            self.array = _Buf((480, 800)).array

        def flush(self):
            pass

        def enable(self):
            pass

    lcd = _Lcd()
    ctl = ctl if ctl is not None else A.AwgCtl()
    page = AwgPage(lcd, ctl)
    page.render()
    # 影子缓冲在 UI 里; 这里把影子拷出来存图
    shadow = page.ui.lcd
    arr = getattr(shadow, "array", None)
    if arr is None:
        arr = lcd.array
    arr = np.asarray(arr, dtype=np.uint16)
    # RGB565 -> RGB888: 用 numpy 位运算直接算(别用 PIL 的 point(lambda) —— 它传进来的是
    # `_E` 表达式对象, 不是整数, 位运算会 TypeError)
    r = ((arr >> 11) & 0x1F) * 255 // 31
    g = ((arr >> 5) & 0x3F) * 255 // 63
    b = (arr & 0x1F) * 255 // 31
    rgb = np.dstack([r, g, b]).astype(np.uint8)
    Image.fromarray(rgb, "RGB").save(path)
    return path
