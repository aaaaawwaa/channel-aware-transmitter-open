#!/usr/bin/env python3
"""AC820 5" TFT (800x480 RGB565) 上的最小绘图/UI 层。

在 `pynq_lcd.Lcd`(帧缓冲 + 像素 API)之上再叠一层: 文字、形状、控件、以及一个收触摸的
帧循环。`Lcd` 只管"往 DDR 里写像素", 这里管"一屏长什么样、谁被按到了"。

**为什么是纯 Python + PIL, 不是 LVGL**: 板上已有 Python 3.8 + PIL 7.0, 这一层要的只是
画字/画控件/收触摸; 引 LVGL 意味着在板上交叉编译一套 C 库并写显示/输入驱动, 收益不值。
代价是每帧整屏重画, 而 480P 一帧就是一次 numpy 赋值 + 一次 flush。

**性能(2026-08-13 板端实测, 一屏标题+副标题+进度条+两个圆角按钮)**: 整屏一帧 **7.5ms
(134 fps)**。这条数不是白来的, 第一版是 92.9ms(10.8fps), 分两步才降下来, 改代码前先看:

- 字形掩膜按 (文字, 字号) **缓存** —— 92.9 -> 50.0ms。每帧重渲同一批字是纯浪费。
- 形状**不做逐行切片赋值** —— 50.0 -> 7.5ms。实测在帧缓冲上逐行切片每次约 **100us 且与
  宽度无关**(69 行 × 220 宽 = 7.4ms, 69 行 × 1 宽 = 6.4ms), 圆角矩形这样画一次 23ms;
  而**标量写 2us、整块 2-D 赋值 0.15ms**。所以形状一律先用 numpy 在连续掩膜上算好
  (`_rounded_mask`/`_circle_mask`, 也带缓存), 再 `region[mask] = color` 盖一次。
  顺带: `array[:, :] = c`(整屏清屏) 0.75ms, `flush()` 0.11ms, 都不值一提。

**立即模式**: 每帧从背景色开始重画全部控件, 不保留绘制状态。控件对象只持有"我长什么样"
和"我被按到时做什么", 不持有位图。

输入是可插拔的(`Input.poll()`): 触摸(GT911)、脚本化输入(演示/测试)、无输入(只显示)
实现同一接口。帧循环每帧问一次输入, 自己合成"按下/抬起"沿, 再按 Z 序从上往下做命中测试。

用法(守护进程 pynq-status 在跑时它占着 PL, 先 systemctl stop pynq-status):
    from pynq_lcd import Lcd
    from pynq_lcd_ui import Button, Label, ProgressBar, Ui

    ui = Ui(Lcd.from_bitfile("/home/xilinx/overlays/ledbtn/ledbtn.bit"))
    ui.add(Label(24, 20, "AC820", size=32, fg=0xFFFF))
    bar = ui.add(ProgressBar(24, 100, 400, 24))
    ui.add(Button(24, 160, 200, 64, "点击", on_tap=lambda b: setattr(bar, "value", 0.5)))
    ui.run(frames=600)          # 或 ui.run() 一直跑, Ctrl-C / ui.stop() 退出

查看某块区域画成了什么(调 UI 时最有用):
    python3 pynq_lcd_ui.py --demo frame.png
"""
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pynq_lcd import HEIGHT, WIDTH, rgb565

# PIL 的字体在各平台上位置不同; 板子是 PYNQ 镜像, 其余是开发机。都找不到时退回 PIL 自带
# 的点阵字体(够看, 但没有无极缩放, 也没有中文)。
LATIN_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "<LOCAL_PATH>",
    "<LOCAL_PATH>",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
# 中文要单独一套字体: DejaVu/Arial 之流没有 CJK 字形, 画出来是一排"豆腐块"。
CJK_FONTS = (
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",      # PYNQ 镜像
    "<LOCAL_PATH>",
    "<LOCAL_PATH>",
    "<LOCAL_PATH>",
    "/System/Library/Fonts/PingFang.ttc",
)

# 一屏配色(RGB565)
BLACK, WHITE, GREY, DARK, ACCENT, DIM = 0x0000, 0xFFFF, 0x8410, 0x2124, 0x07FF, 0x4208
GREEN, RED, YELLOW = 0x07E0, 0xF800, 0xFFE0

# 参考设计(`docs/reference/index.html` 的 CSS 变量)的配色, 供新的五页界面使用。
# **必须走 `rgb565()` 而不是直接写 #RRGGBB** —— 面板是 16 位色, 分量按位截断(不四舍五入),
# 手算容易差 1 个 LSB 而让同一种颜色在两处看起来不一样。
BG    = rgb565(0x0E, 0x14, 0x1C)   # 页面底色
PANEL = rgb565(0x15, 0x1E, 0x2A)   # 卡片
LINE  = rgb565(0x2A, 0x35, 0x43)   # 分隔线 / 边框
TEXT  = rgb565(0xEE, 0xF3, 0xF8)   # 正文
MUTED = rgb565(0x96, 0xA5, 0xB8)   # 辅助文字
CYAN  = rgb565(0x5A, 0xE0, 0xC4)   # 选中 / 强调
ORANGE = rgb565(0xFF, 0xB0, 0x66)  # 主操作按钮
BTN   = rgb565(0x20, 0x2B, 0x39)   # 普通按钮底色
BTN_ON_BG = rgb565(0x16, 0x3B, 0x38)   # 选中按钮底色
STOP_BG   = rgb565(0x60, 0x33, 0x36)   # 停止按钮底色
STOP_LINE = rgb565(0x9E, 0x55, 0x5B)

# **中文的字号下限(板上实测)**: 同一串字按 11/12/13/14/15/16/18/20 各画一行存成 PNG,
# 板上亲眼看的结果是 11px 糊成一团、12px 仍差、13px 勉强、**14px 才清楚**。
# ⚠ 这与"缺字形"是两件事: 字形是在的(`has_glyphs` 照样通过), 只是没有足够的像素去画这些
#   笔画。开发机的 msyh 在小字号下比板子的 `DroidSansFallbackFull.ttf` 清楚得多, 所以在开发
#   机上量会量出一个偏小的值 —— 这个下限**只能按板子定**。量法见 `font_probe.py`。
# ASCII 不受这条约束(西文字体在小字号下清楚), 所以它只用来卡带中文的那几处。
MIN_CJK_SIZE = 14

# **字形掩膜的二值化阈值(覆盖率 0..255 的一半)**。PIL 渲出来的是带抗锯齿的灰度掩膜,
# 边缘像素的灰度 = 该像素被字形盖住的面积比例。老代码按"非零即实心"(`> 0`)盖色, 于是
# **每一个抗锯齿边缘像素都被点成整格** —— 每笔两侧各胖 ~1px、字内的窄缝(日/月 的框、
# 信道/损伤 的笔画间隙)被填死, 14px 的中文整片糊在一起(实测非零像素里 37% 是 <50% 覆盖
# 的边缘像素)。按 50% 覆盖率二值化后笔画回到真实粗细、间隙重新露出来。
GLYPH_COVERAGE = 128


def _text_size(font, s):
    """PIL 各版本量文字尺寸的接口不同(9.2 起弃用 getsize)。"""
    try:
        return font.getsize(s)
    except AttributeError:
        left, top, right, bottom = font.getbbox(s)
        return right - left, bottom - top


_font_cache = {}
_MASK_CACHE = {}        # (文字, 字号) -> (灰度掩膜, ascent)
_SHAPE_CACHE = {}       # 形状参数 -> 布尔掩膜
_CACHE_MAX = 256        # 两个缓存各自的上限, 免得动态内容把内存撑起来


def _cache_put(cache, key, value):
    if len(cache) >= _CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


def load_font(size, cjk=False):
    """按字号取字体(带缓存)。cjk=True 时用带中文字形的字体。"""
    key = (size, bool(cjk))
    if key in _font_cache:
        return _font_cache[key]
    font = None
    for path in (CJK_FONTS if cjk else LATIN_FONTS):
        try:
            font = ImageFont.truetype(path, size)
            break
        except (OSError, IOError):
            continue
    if font is None:
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def font_for(text, size):
    """纯 ASCII 用常规西文字体, 含非 ASCII(中文等)时换成 CJK 字体。

    **不能只挑一套字体**: 板子上唯一带中文的是 `DroidSansFallbackFull.ttf`, 而它是
    "CJK 补充"字体 —— 里面**没有 ASCII 字形**(画英文/数字会得到一排豆腐块); 反过来
    DejaVu/Arial 没有中文。所以混排必须按段换字体, 见 `_runs`/`Canvas.text`。
    """
    try:
        text.encode("ascii")
        cjk = False
    except UnicodeEncodeError:
        cjk = True
    return load_font(size, cjk)


def _runs(s):
    """把一行文字切成连续的"纯 ASCII"段与"非 ASCII"段, 每段各用合适的字体。"""
    runs = []
    for ch in s:
        ascii_ = ord(ch) < 128
        if runs and runs[-1][1] == ascii_:
            runs[-1][0] += ch
        else:
            runs.append([ch, ascii_])
    return runs


def _ascent(font):
    try:
        return font.getmetrics()[0]
    except AttributeError:
        return 0


_MISSING = object()


def _glyph_mask(s, size):
    """一行文字的灰度字形掩膜, 按 (文字, 字号) 缓存。

    缓存是这一层的性能关键: 掩膜只跟文字/字号有关(颜色是blit时才上的), 而 UI 每帧要
    重画一遍。不做缓存时实测整屏一帧 92.9ms(10.8fps) —— 几乎全花在每帧重渲同一批字上。
    缓存有上限, 免得动态变化的数字把内存撑起来。

    返回 (mask, ascent) 或 (None, 0)(这串字没有可画字形)。
    """
    key = (s, size)
    hit = _MASK_CACHE.get(key, _MISSING)
    if hit is not _MISSING:
        return hit
    font = font_for(s, size)
    w, h = _text_size(font, s)
    if w <= 0 or h <= 0:
        entry = (None, 0)
    else:
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text((-1, -1), s, font=font, fill=255)
        entry = (np.asarray(img), _ascent(font))
    return _cache_put(_MASK_CACHE, key, entry)


def text_size(s, size):
    """一行文字画出来占多大: 宽度按段累加(换字体宽度就变), 高度取各段最大。"""
    width, height = 0, 0
    for run, _ascii in _runs(s):
        mask, _ascent_ = _glyph_mask(run, size)
        if mask is None:
            continue
        width += mask.shape[1]
        height = max(height, mask.shape[0])
    return width, height


_TOFU_CHAR = ""      # 私用区码位: 正常字体都不给它字形, 渲出来就是"缺字形"的标准样子


def _tofu_mask(size):
    """这台机器上"缺字形"渲出来的样子(私用区码位), 用于比对。带缓存。"""
    key = ("tofu", size)
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    font = font_for(_TOFU_CHAR, size)
    w, h = _text_size(font, _TOFU_CHAR)
    if w <= 0 or h <= 0:
        entry = None
    else:
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text((-1, -1), _TOFU_CHAR, font=font, fill=255)
        entry = np.asarray(img)
    return _cache_put(_SHAPE_CACHE, key, entry)


def has_glyphs(s, size=16):
    """这串字在当前字体下**真有字形**吗(而不是画成一排豆腐块)。

    **为什么需要它**: 字体缺字形时 PIL 会画一个"豆腐块"方框, 那个方框**是非空的** ——
    所以"掩膜非空"根本判不出缺字形。本工程踩过三次(按钮上的 U+2212 减号、"α"、参考设计
    的导航图标 `∿ ⋈ ≋ ⌁ ⚙ ◉`), 每次都是上屏才发现。

    判法: 与一个**私用区**码位(U+E000)渲出的豆腐块逐位比对 —— 任何正常字体都不会给私用区
    分配字形, 所以它渲出来的就是这台机器上"缺字形"的标准样子。

    ⚠ 开发机与板子的字体集**不同**(板子上唯一带中文的是 `DroidSansFallbackFull.ttf`),
      所以这条判断的结果是**平台相关**的: 在开发机通过不代表板子上也通过。要在板上确认,
      得在板上跑同一条断言。
    """
    tofu = _tofu_mask(size)
    for run, _ascii in _runs(s):
        if not run.strip():
            # 空白渲成空白**是对的**, 不是缺字形 —— 但它的掩膜是空的, 会被下面判成 False。
            # 所以先跳过: 否则"合成示意 · 非测量"这种带空格的串一律报缺字形。
            continue
        mask, _a = _glyph_mask(run, size)
        if mask is None or not mask.any():
            return False
        if tofu is not None and mask.shape == tofu.shape and (mask == tofu).all():
            return False
    return True


# 校验钩子: 不为 None 时 `Canvas.text` 把画过的每串字记进来。
#
# 用途是"把所有**真的会画到屏上**的字过一遍 `has_glyphs`"。靠人肉枚举字面量必漏 ——
# 实测漏过 `·`(U+00B7)与 `—`(U+2014): 开发机的 msyh 有字形, 板子上的
# `DroidSansFallbackFull.ttf` 没有, 于是屏上是豆腐块。屏上的字只有 `Canvas.text` 一个
# 出口, 挂在出口上就不会漏。
_TEXT_LOG = None


def set_text_log(sink):
    """开始/停止记录画过的文字(`sink=None` 关闭记录)。"""
    global _TEXT_LOG
    _TEXT_LOG = sink


def drawn_text():
    """自上次 `set_text_log()` 起画过的文字, 形如 `[(字符串, 字号)]`(按出现顺序)。"""
    return [(s, size) for s, size, _x, _y in _TEXT_LOG] if _TEXT_LOG is not None else []


def drawn_text_boxes():
    """同上, 但带上落笔点: `[(字符串, 字号, x, y)]` —— 用来判"文字有没有跑出屏幕"。"""
    return list(_TEXT_LOG) if _TEXT_LOG is not None else []


class ShadowFramebuffer:
    """画在 CPU 内存里, 画完一次性拷进 DDR 帧缓冲。

    **为什么要它**: 直接在帧缓冲上"先清屏、再逐个画控件"会留下一段"清完了还没画完"的
    窗口(实测约 7ms), 面板是连续扫描的, 正好扫到那一帧就会整屏闪一下 —— 板上实测用户能
    明确看到闪。画完再整块拷贝, 面板看到的永远是完整的一帧; 而且整块 `np.copyto`(实测
    1.45ms) 比零散写(7.5ms)还快。

    接口与 `Lcd` 相同(`array` / `flush` / `enable` / `disable` / `address`), 控件不需要
    知道自己在画影子还是画板子。`Ui(shadow=True)` 会自动套一层。
    """

    def __init__(self, lcd):
        self._lcd = lcd
        # 必须显式给 shape/dtype: empty_like 会照着非连续视图的布局走, 可能拿到非连续数组
        self.array = np.zeros(lcd.array.shape, dtype=lcd.array.dtype)

    @property
    def address(self):
        return self._lcd.address

    def flush(self):
        """把整帧拷进真正的帧缓冲, 再交给底层的 flush。"""
        np.copyto(self._lcd.array, self.array)
        self._lcd.flush()

    def enable(self):
        self._lcd.enable()

    def disable(self):
        self._lcd.disable()


class Canvas:
    """往 `Lcd` 的像素数组上画。所有方法都自己裁剪, 越界不会写坏相邻行。

    **为什么形状先做成掩膜再一次性盖章**: 实测(2026-08-13 板端)在帧缓冲上做**逐行切片
    赋值**每次约 100us —— 与宽度无关(69 行 220 宽 = 7.4ms, 69 行 1 宽 = 6.4ms), 圆角矩形
    这样画一次要 23ms。而标量写只要 2us、整块 2-D 赋值只要 0.15ms。所以这里所有形状都在
    **连续的 CPU 掩膜**上用 numpy 一次算出来, 再 `region[mask] = color` 盖一次。
    """

    def __init__(self, lcd):
        self.lcd = lcd
        self.array = lcd.array          # (HEIGHT, WIDTH) uint16 视图, 直接写它

    # ---- 基本形状 ----

    def clear(self, color=BLACK):
        self.array[:, :] = color

    def _stamp(self, x, y, mask, color):
        """把布尔掩膜(True 处上色)盖到 (x,y), 越界自动裁掉。整块一次赋值。"""
        h, w = mask.shape
        x0, y0, x1, y1 = _clip_box(x, y, x + w - 1, y + h - 1)
        if x0 > x1 or y0 > y1:
            return
        sub = mask[y0 - y:y1 - y + 1, x0 - x:x1 - x + 1]
        if not sub.any():
            return
        target = self.array[y0:y1 + 1, x0:x1 + 1]
        target[sub] = color

    def rect(self, x0, y0, x1, y1, color, fill=True):
        """(x0,y0)-(x1,y1) 闭区间; fill=False 只画一像素宽的边框。"""
        x0, y0, x1, y1 = _clip_box(x0, y0, x1, y1)
        if x0 > x1 or y0 > y1:
            return
        if fill:
            self.array[y0:y1 + 1, x0:x1 + 1] = color
            return
        # 四条边各是一次整块赋值; 退化成一像素宽时用标量写(比切片快得多)
        self.array[y0, x0:x1 + 1] = color
        self.array[y1, x0:x1 + 1] = color
        if x1 > x0:
            self.array[y0:y1 + 1, x0] = color
            self.array[y0:y1 + 1, x1] = color
        else:
            self.array[y0:y1 + 1, x0] = color

    def rounded_rect(self, x0, y0, x1, y1, color, radius=8, fill=True, width=1):
        """圆角矩形。没套圆角时退化成普通矩形(它已经很快)。"""
        x0, y0, x1, y1 = _clip_box(x0, y0, x1, y1)
        if x0 > x1 or y0 > y1:
            return
        w, h = x1 - x0 + 1, y1 - y0 + 1
        radius = int(min(radius, w // 2, h // 2))
        if radius <= 0:
            self.rect(x0, y0, x1, y1, color, fill)
            return
        mask = (_rounded_mask(w, h, radius) if fill
                else _rounded_outline_mask(w, h, radius, width))
        self._stamp(x0, y0, mask, color)

    def circle(self, cx, cy, radius, color, fill=True):
        self._stamp(cx - radius, cy - radius, _circle_mask(radius, fill), color)

    def polyline(self, points, color=WHITE, width=1):
        """把一串点连成折线。`points` 是 [(x, y), ...], 少于两点不画。

        **不能逐段画小矩形** —— 那样每段都是一次切片写, 按本模块开头的实测(每次约 100us),
        一条 200 点的折线会退化到几十毫秒。做法与 `_glyph_mask` 一致: 先用 PIL 在**连续的
        CPU 掩膜**上把整条线渲出来, 再 `_stamp` 整块盖一次。

        掩膜按 (点序列, 线宽) 缓存 —— 波形/星座预览在参数不变时每帧都是同一条线。
        """
        if len(points) < 2:
            return
        mask = _polyline_mask(points, width)
        if mask is None:
            return
        ox, oy, sub = mask
        self._stamp(ox, oy, sub, color)

    def bar(self, x0, y0, x1, y1, value, color=ACCENT, bg=DIM, border=GREY):
        """进度条: value 0..1 决定从左边填多少列。"""
        value = 0.0 if value < 0 else (1.0 if value > 1 else float(value))
        self.rect(x0, y0, x1, y1, bg)
        span = int(round((x1 - x0 + 1) * value))
        if span > 0:
            self.rect(x0, y0, x0 + span - 1, y1, color)
        self.rect(x0, y0, x1, y1, border, fill=False)

    # ---- 文字 ----

    def text(self, x, y, s, color=WHITE, size=16):
        """在 (x,y)(文字块左上角) 画一行文字, 背景透明 —— 只写字形覆盖到的像素。

        透明很关键: 控件是"背景 + 文字"叠出来的, 若文字带自己的底色, 圆角和边框就被
        盖掉一块。做法是让 PIL 渲一张灰度字形掩膜, 再按掩膜把颜色写进帧缓冲。

        混排(中英数字)按段换字体, 各段以**基线**对齐 —— 否则中文字体与西文字体行高
        不同, 同一行里会上下跳。所以 y 大致是这一行最高那段文字的顶端。
        """
        parts = []
        for run, _ascii in _runs(s):
            mask, ascent = _glyph_mask(run, size)
            if mask is not None:
                parts.append((mask, ascent))
        if _TEXT_LOG is not None:
            _TEXT_LOG.append((s, size, x, y))
        if not parts:
            return
        baseline = y + max(ascent for _mask, ascent in parts)
        pen = x
        for mask, ascent in parts:
            _blit_mask(self.array, mask, pen, baseline - ascent, color)
            pen += mask.shape[1]

    def text_center(self, x0, y0, x1, y1, s, color=WHITE, size=16):
        """在矩形里居中画一行文字。"""
        w, h = text_size(s, size)
        self.text(x0 + max((x1 - x0 + 1 - w) // 2, 0), y0 + max((y1 - y0 + 1 - h) // 2, 0),
                  s, color, size)


def _rounded_mask(w, h, radius):
    """w×h 实心圆角矩形的布尔掩膜(带缓存, 一次算完, 不做 Python 逐行循环)。

    做法是"离内矩形的距离": 四个角各自到圆心的距离不超过 radius 才算在内。中间那两条
    直边因为被 max(...,0) 夹住, 距离恒为 0, 自动算作在内。

    缓存同样关键: 板子(Cortex-A9)上算一次 220×70 的掩膜要好几毫秒, 而按钮每帧都要画。
    """
    key = ("fill", w, h, radius)
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    ys = np.arange(h, dtype=np.int32)[:, None]
    xs = np.arange(w, dtype=np.int32)[None, :]
    dx = np.maximum(np.maximum(radius - xs, xs - (w - 1 - radius)), 0)
    dy = np.maximum(np.maximum(radius - ys, ys - (h - 1 - radius)), 0)
    return _cache_put(_SHAPE_CACHE, key, (dx * dx + dy * dy) <= radius * radius)


def _rounded_outline_mask(w, h, radius, width):
    """圆角矩形边框的布尔掩膜 = 实心减去内缩 width 的实心。"""
    key = ("outline", w, h, radius, width)
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    inner_w, inner_h = max(w - 2 * width, 0), max(h - 2 * width, 0)
    mask = _rounded_mask(w, h, radius).copy()
    if inner_w > 0 and inner_h > 0:
        inner = _rounded_mask(inner_w, inner_h, max(radius - width, 0))
        mask[width:width + inner_h, width:width + inner_w] &= ~inner
    return _cache_put(_SHAPE_CACHE, key, mask)


def _circle_mask(radius, fill):
    key = ("circle", radius, bool(fill))
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    side = 2 * radius + 1
    ys = np.arange(side, dtype=np.int32) - radius
    dist2 = ys[:, None] ** 2 + ys[None, :] ** 2
    mask = dist2 <= radius * radius
    if not fill:
        mask &= dist2 >= (radius - 1) ** 2
    return _cache_put(_SHAPE_CACHE, key, mask)


def _polyline_mask(points, width):
    """折线的布尔掩膜。返回 (ox, oy, mask) —— 掩膜左上角在原图上的位置, 或 None。

    **先把点夹到屏内再算包围盒**: 调用方可能送来越界的点(比如幅度算错的波形), 不夹的话
    包围盒能开出几万像素见方的 PIL 图, 一次就把内存和耗时吃掉。

    缓存键是点序列本身 —— 波形/星座预览只要参数没变, 每帧都是同一条线, 直接命中。
    """
    pts = [(int(round(x)), int(round(y))) for x, y in points]
    pts = [(min(max(x, 0), WIDTH - 1), min(max(y, 0), HEIGHT - 1)) for x, y in pts]
    key = ("poly", tuple(pts), int(width))
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    pad = max(int(width), 1)
    ox, oy = min(xs) - pad, min(ys) - pad
    w, h = max(xs) - ox + pad + 1, max(ys) - oy + pad + 1
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).line([(x - ox, y - oy) for x, y in pts], fill=255, width=width)
    entry = (ox, oy, np.asarray(img) > 0)
    return _cache_put(_SHAPE_CACHE, key, entry)


def _clip_box(x0, y0, x1, y1):
    return (max(int(x0), 0), max(int(y0), 0),
            min(int(x1), WIDTH - 1), min(int(y1), HEIGHT - 1))


def _blit_mask(array, mask, x, y, color):
    """把灰度掩膜按颜色写进帧缓冲, 越界自动裁掉。

    掩膜是带抗锯齿的覆盖率图(见 `GLYPH_COVERAGE`): 只在覆盖率过半处盖色。老代码写的是
    `> 0`, 把每个边缘像素都点成实心 —— 笔画胖一圈、字内窄缝填死, 中文糊成一片。
    """
    h, w = mask.shape
    x0, y0, x1, y1 = _clip_box(x, y, x + w - 1, y + h - 1)
    if x0 > x1 or y0 > y1:
        return
    sub = mask[y0 - y:y1 - y + 1, x0 - x:x1 - x + 1]
    target = array[y0:y1 + 1, x0:x1 + 1]
    target[sub >= GLYPH_COVERAGE] = color


# ---------------- 输入 ----------------

class NullInput:
    """没有输入设备(只显示)。"""

    def poll(self):
        return None

    def close(self):
        pass


class ScriptedInput:
    """按脚本给出输入, 供演示与测试: 每项是 (x, y, pressed) 或 None。"""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0
        self.closed = False

    def poll(self):
        if self.i >= len(self.script):
            return None
        item = self.script[self.i]
        self.i += 1
        return item

    def close(self):
        self.closed = True


# ---------------- 控件 ----------------

class Widget:
    """控件的共同部分: 一块矩形 + 画自己 + 收触摸。

    触摸只做"命中/不命中"两态。按下与抬起都落在同一个控件上才算一次点击(点完拖出去
    再松开不算) —— 这是触摸屏上最不容易误触的语义。
    """

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)
        self.pressed = False
        # `tappable=False` 的控件只是**画**在某处(准星、进度条、标签), 不参与触摸命中。
        # ⚠ 这不是可有可无的开关: `_hit` 是从 Z 序顶往下找第一个 `contains` 的控件, 而准星
        #   的框就是整屏、又总是最后加(画在最上层) —— 于是它会把**每一次点击**都吃掉,
        #   板上的表现就是"按钮全都点不动"。诊断: 用 `Ui._hit(x, y)` 看返回的是谁。
        self.tappable = True
        # `visible=False` 的控件既不画也不收触摸 —— 用来做"同一个控件按状态显隐",
        # 比如弹窗里的键盘。`Ui.step()` 会跳过它, `_hit` 也不会命中它。
        self.visible = True
        # `enabled=False` = 变灰且**不可点**(锁定态)。放在基类而不是某个控件里:
        # 只要"能不能点"由 `contains` 决定, 就必须一起判, 否则"锁定"只是视觉上的,
        # 底下照样收触摸(数字调制页运行中锁模式/滤波按钮时就差点这样)。
        self.enabled = True

    @property
    def box(self):
        return self.x, self.y, self.x + self.w - 1, self.y + self.h - 1

    def contains(self, x, y):
        if not self.tappable or not self.visible or not self.enabled:
            return False
        return (self.x <= x <= self.x + self.w - 1
                and self.y <= y <= self.y + self.h - 1)

    def draw(self, canvas):
        raise NotImplementedError

    def on_press(self, x, y):
        self.pressed = True

    def on_release(self, x, y, inside):
        self.pressed = False
        if inside:
            self.on_tap(x, y)

    def on_tap(self, x, y):
        pass


class Label(Widget):
    def __init__(self, x, y, text, size=16, fg=WHITE, bg=None, pad=2, align="left"):
        self.text = text
        self.size = size
        self.fg = fg
        self.bg = bg
        self.pad = pad
        self.align = align
        w, h = text_size(text, size)
        super().__init__(x, y, w + 2 * pad, h + 2 * pad)
        self.tappable = False

    def draw(self, canvas):
        if self.bg is not None:
            canvas.rect(*self.box, self.bg)
        x0, y0, x1, y1 = self.box
        if self.align == "center":
            canvas.text_center(x0, y0, x1, y1, self.text, self.fg, self.size)
        elif self.align == "right":
            w, _ = text_size(self.text, self.size)
            canvas.text(x1 - w - self.pad, y0 + self.pad, self.text, self.fg, self.size)
        else:
            canvas.text(x0 + self.pad, y0 + self.pad, self.text, self.fg, self.size)


class Button(Widget):
    def __init__(self, x, y, w, h, text, on_tap=None, size=18,
                 fill=DARK, edge=GREY, fg=WHITE, pressed_fill=ACCENT, radius=8):
        super().__init__(x, y, w, h)
        self.text = text
        self.callback = on_tap
        self.size = size
        self.fill = fill
        self.edge = edge
        self.fg = fg
        self.pressed_fill = pressed_fill
        self.radius = radius

    def draw(self, canvas):
        x0, y0, x1, y1 = self.box
        edge = self.edge if self.enabled else LINE
        fg = self.fg if self.enabled else MUTED
        canvas.rounded_rect(x0, y0, x1, y1, self.pressed_fill if self.pressed else self.fill,
                            self.radius)
        canvas.rounded_rect(x0, y0, x1, y1, edge, self.radius, fill=False)
        canvas.text_center(x0, y0, x1, y1, self.text, fg, self.size)

    def on_tap(self, x, y):
        if self.callback is not None:
            self.callback(self)


class ProgressBar(Widget):
    def __init__(self, x, y, w, h, value=0.0, color=ACCENT, bg=DIM, border=GREY):
        super().__init__(x, y, w, h)
        self.value = value
        self.color = color
        self.bg = bg
        self.border = border
        self.tappable = False

    def draw(self, canvas):
        x0, y0, x1, y1 = self.box
        canvas.bar(x0, y0, x1, y1, self.value, self.color, self.bg, self.border)


class Crosshair(Widget):
    """在触摸点画一个十字 —— 手指位置与屏幕读数是不是一致, 一眼就能看出来。

    `point=(x, y)` 或 None(没按)。它是标定/调试的"眼睛": 定量的那套在
    `pynq_touch.fit_calibration`, 这里负责让人当场看出对不对。
    """

    def __init__(self, width, height, color=YELLOW, arm=16):
        super().__init__(0, 0, width, height)
        self.point = None
        self.color = color
        self.arm = arm
        self.tappable = False           # 整屏大小又画在最上层, 可点就吃掉所有按钮

    def draw(self, canvas):
        if self.point is None:
            return
        x, y = self.point
        canvas.rect(x - self.arm, y, x + self.arm, y, self.color)
        canvas.rect(x, y - self.arm, x, y + self.arm, self.color)


class Panel(Widget):
    """一张卡片: 圆角实底 + 边框。参考设计的工作区就是几张这样的卡片拼的。

    纯装饰 —— 卡片**不能**吃掉点在自己身上的按钮, 所以 `tappable=False`。
    """

    def __init__(self, x, y, w, h, fill=PANEL, edge=LINE, radius=10):
        super().__init__(x, y, w, h)
        self.fill = fill
        self.edge = edge
        self.radius = radius
        self.tappable = False

    def draw(self, canvas):
        x0, y0, x1, y1 = self.box
        canvas.rounded_rect(x0, y0, x1, y1, self.fill, self.radius)
        canvas.rounded_rect(x0, y0, x1, y1, self.edge, self.radius, fill=False)


class ValueRow(Widget):
    """一行"标签 + 值 + 单位", 值是**会变**的。

    **为什么不用 `Label`**: `Label.__init__` 在构造时按文字算好尺寸, 之后改 `.text`
    不会重算 `box` —— 值一变长变短, 对齐和底色就跟着错。这里照 `StatusBoard` 的路子:
    不持有 Label, 每帧直接 `canvas.text()` 按当前文字画。`on_tap` 给"点这行弹键盘"用。
    """

    def __init__(self, x, y, w, h, label, value="", unit="", on_tap=None,
                 size=20, label_size=MIN_CJK_SIZE, fill=BTN, edge=LINE, radius=8,
                 fg=TEXT, label_fg=MUTED, unit_fg=MUTED):
        super().__init__(x, y, w, h)
        self.label = label
        self.value = value
        self.unit = unit
        self.callback = on_tap
        self.size = size
        self.label_size = label_size
        self.fill = fill
        self.edge = edge
        self.fg = fg
        self.label_fg = label_fg
        self.unit_fg = unit_fg
        self.radius = radius

    def draw(self, canvas):
        x0, y0, x1, y1 = self.box
        fg = self.fg if self.enabled else MUTED
        canvas.rounded_rect(x0, y0, x1, y1, self.pressed_fill() if self.pressed else self.fill,
                            self.radius)
        canvas.rounded_rect(x0, y0, x1, y1, self.edge, self.radius, fill=False)
        pad = 10
        canvas.text(x0 + pad, y0 + (self.h - text_size(self.label, self.label_size)[1]) // 2,
                    self.label, self.label_fg, self.label_size)
        # 值 + 单位右对齐
        unit_w = 0
        if self.unit:
            unit_w = text_size(self.unit, self.label_size)[0] + 5
            canvas.text(x1 - pad - text_size(self.unit, self.label_size)[0],
                        y0 + (self.h - text_size(self.unit, self.label_size)[1]) // 2,
                        self.unit, self.unit_fg, self.label_size)
        vw, vh = text_size(self.value, self.size)
        canvas.text(x1 - pad - unit_w - vw, y0 + (self.h - vh) // 2,
                    self.value, fg, self.size)

    def pressed_fill(self):
        return BTN_ON_BG

    def on_tap(self, x, y):
        if self.callback is not None:
            self.callback(self)


class Table(Widget):
    """只读表格: 表头 + 若干行, 每格是一个可点按钮(点开键盘编辑)。

    行数固定(信道页是 4 个抽头), 但**格子里的数字每帧都可能变**, 所以照 `ValueRow` 的
    路子直接画, 不用 Label。`cells` 是 [[str, ...], ...]; `on_cell` 收到 (row, col)。
    """

    ROW_H = 34
    HEAD_H = 22

    def __init__(self, x, y, w, headers, cells=None, on_cell=None,
                 col_w=None, size=16, fill=BTN, edge=LINE, head_fg=MUTED, fg=TEXT):
        rows = len(cells) if cells else 0
        super().__init__(x, y, w, self.HEAD_H + self.ROW_H * max(rows, 1))
        self.headers = list(headers)
        self.cells = [list(r) for r in (cells or [])]
        self.callback = on_cell
        self.col_w = col_w or [w // len(self.headers)] * len(self.headers)
        self.size = size
        self.fill = fill
        self.edge = edge
        self.head_fg = head_fg
        self.fg = fg
        self.tappable = True
        self._pressed_cell = None

    def _cell_box(self, row, col):
        x0 = self.x + sum(self.col_w[:col])
        y0 = self.y + self.HEAD_H + row * self.ROW_H
        return x0, y0, x0 + self.col_w[col] - 3, y0 + self.ROW_H - 3

    def draw(self, canvas):
        x0, y0, x1, y1 = self.box
        for c, head in enumerate(self.headers):
            hx0 = self.x + sum(self.col_w[:c])
            canvas.text(hx0 + 4, y0 + 3, head, self.head_fg, self.size - 2)
        for r, row in enumerate(self.cells):
            for c, val in enumerate(row):
                bx0, by0, bx1, by1 = self._cell_box(r, c)
                pressed = self._pressed_cell == (r, c)
                canvas.rounded_rect(bx0, by0, bx1, by1,
                                    BTN_ON_BG if pressed else self.fill, 6)
                canvas.rounded_rect(bx0, by0, bx1, by1, self.edge, 6, fill=False)
                canvas.text_center(bx0, by0, bx1, by1, val, self.fg, self.size)

    def _hit_cell(self, x, y):
        for r in range(len(self.cells)):
            for c in range(len(self.headers)):
                bx0, by0, bx1, by1 = self._cell_box(r, c)
                if bx0 <= x <= bx1 and by0 <= y <= by1:
                    return r, c
        return None

    def on_press(self, x, y):
        self.pressed = True
        self._pressed_cell = self._hit_cell(x, y)

    def on_release(self, x, y, inside):
        cell, self._pressed_cell = self._pressed_cell, None
        self.pressed = False
        if inside and cell is not None and self.callback is not None:
            self.callback(cell[0], cell[1])


class Plot(Widget):
    """预览区: 深色底 + 网格 + 折线(可多条)。

    `series` 是 [([(x, y), ...], color), ...], **坐标归一化, 且两轴都以 0 为中心**:
    x 与 y 都是 -1..1(左→右、下→上)。两轴同号意味着"1 个单位"在横竖上是同一个尺度,
    星座图不会被拉成椭圆。

    ⚠ 早先 x 用的是 0..1, 而星座的点是 -1..1 —— 它们全被映射到框外裁掉了, 画出来是空白,
      偏偏"框里有网格"让人以为已经画对。两个轴统一成 -1..1 就没这个歧义。

    **内容只依赖参数, 不依赖时间** —— 参数不变时每帧都是同一条线, `polyline` 的掩膜缓存
    直接命中, 所以它不会击穿各页的"内容没变就不重画"优化(前提是调用方把它纳入
    `_last_sig` 签名, 见 `ui_view._signature`)。
    """

    def __init__(self, x, y, w, h, series=None, bg=None, grid=LINE, badge="",
                 inset=0.16, square=False, badge_size=11):
        super().__init__(x, y, w, h)
        self.series = series or []
        self.bg = bg if bg is not None else rgb565(0x0D, 0x15, 0x20)
        self.grid = grid
        self.badge = badge
        # 角标常带中文("合成示意 | 非测量"), 所以字号要能被调用方抬到板上可读的下限。
        self.badge_size = badge_size
        self.tappable = False
        self.div_x = 8
        self.div_y = 6
        self.inset = inset
        # `square=True` 时数据区退到框内**居中的正方形**(见 `_frame`) —— 星座图必须等比例。
        self.square = square

    def _frame(self):
        """数据区(也是网格区)的外框与两轴格数。

        ⚠ `square=True` 是给星座图用的: 两个轴各自映射到框的**宽**与**高**, 于是一个 ±1 的
          方阵会被框的宽高比拉成矩形, 看着像"星座被横着抻开", 读不出正确的相对位置。
          方形区保证 I/Q 两轴的像素尺度一致。
        """
        x0, y0, x1, y1 = self.box
        if not self.square:
            return x0, y0, x1, y1, self.div_x, self.div_y
        side = min(x1 - x0, y1 - y0)
        fx0 = int(round((x0 + x1 - side) / 2.0))
        fy0 = int(round((y0 + y1 - side) / 2.0))
        return fx0, fy0, fx0 + side, fy0 + side, 4, 4

    def to_pixels(self, pts):
        """归一化点 -> 这个框里的像素点。两轴都是 -1..1, 映射到数据区内 `inset` 比例的边距处。

        `inset` 存在是因为满量程数据(±1 的星座点、100% 幅度的正弦)**正好压在边框与网格
        端点上**, 看起来像框的装饰而不是数据。留一点边距就分得开了。
        """
        fx0, fy0, fx1, fy1, _nx, _ny = self._frame()
        w, h = fx1 - fx0, fy1 - fy0
        k = 1.0 / (1.0 + self.inset)
        out = []
        for nx, ny in pts:
            px = fx0 + int(round((nx * k + 1.0) / 2.0 * w))
            py = fy0 + int(round((1.0 - (ny * k + 1.0) / 2.0) * h))
            out.append((px, py))
        return out

    def draw(self, canvas):
        x0, y0, x1, y1 = self.box
        canvas.rect(x0, y0, x1, y1, self.bg)
        fx0, fy0, fx1, fy1, nx, ny = self._frame()
        for i in range(1, nx):
            gx = fx0 + (fx1 - fx0) * i // nx
            canvas.rect(gx, fy0, gx, fy1, self.grid)
        for i in range(1, ny):
            gy = fy0 + (fy1 - fy0) * i // ny
            canvas.rect(fx0, gy, fx1, gy, self.grid)
        for pts, color in self.series:
            if len(pts) >= 2:
                canvas.polyline(self.to_pixels(pts), color, 1)
        if self.badge:
            canvas.text(x0 + 8, y0 + 6, self.badge, MUTED, self.badge_size)


class Ui:
    """一屏控件 + 一个帧循环。

    `step()` 做一帧: 收输入 → 合成按下/抬起沿 → 派发给命中的控件 → 重画 → 把像素推给 PL。
    `run(frames=N)` 反复调 `step()`; 传 None 表示一直跑(靠 `stop()` 或 Ctrl-C 退出)。

    `shadow=True` 时画在 CPU 影子缓冲里、每帧整块拷进帧缓冲(见 `ShadowFramebuffer`)——
    真上屏时都该开着, 否则"清屏到画完"那 ~7ms 的中间态会被面板扫到, 看着就是整屏闪。
    """

    def __init__(self, lcd, source=None, background=BLACK, shadow=False,
                 framebuffer=None):
        # `framebuffer`: 直接指定要画进的缓冲(通常是**别人**已经建好的
        # `ShadowFramebuffer`)。给"两块内容要合成同一帧"的场景用 —— 各自建影子的话,
        # 两边各推各的整帧, 后推的把先推的整块盖掉(见 `ui_view.UiView` 的状态页分支)。
        if framebuffer is not None:
            self.lcd = framebuffer
        else:
            self.lcd = ShadowFramebuffer(lcd) if shadow else lcd
        self.canvas = Canvas(self.lcd)
        self.background = background
        self.widgets = []
        self.source = source or NullInput()
        self.active = None              # 目前按着的控件
        self._touching = False
        self.running = False
        self.frames = 0

    def add(self, widget):
        self.widgets.append(widget)
        return widget

    def step(self, flush=True):
        """画一帧并处理这一帧的输入。返回这一帧是否有触摸按下。

        `flush=False` 只画进影子缓冲、**不推给帧缓冲**。给"两块内容要合成同一帧"的场景用:
        面板的状态页要把状态屏与自己的顶栏/导航/底栏叠成**一张**图, 若两边各推各的,
        后推的那整块会把先推的整块盖掉 —— 而且一帧里两次整块拷贝, 面板扫到的中间态
        看着就是**整屏闪**(实测踩到, 见 `ui_view.UiView.render` 的状态页分支)。
        """
        touched = self._poll_input()
        self.canvas.clear(self.background)
        for w in self.widgets:
            if w.visible:
                w.draw(self.canvas)
        if flush:
            self.lcd.flush()
        self.frames += 1
        return touched

    def _poll_input(self):
        report = self.source.poll()
        if report is None:
            return False
        x, y, down = report
        if down and not self._touching:
            self.active = self._hit(x, y)
            if self.active is not None:
                self.active.on_press(x, y)
        elif down and self.active is not None:
            self.active.on_press(x, y)          # 拖动: 让控件能跟上手指
        elif not down and self._touching and self.active is not None:
            self.active.on_release(x, y, self.active.contains(x, y))
            self.active = None
        self._touching = bool(down)
        return bool(down)

    def _hit(self, x, y):
        """Z 序从上往下找命中的控件: 后加的画在上面, 所以先问它。"""
        for w in reversed(self.widgets):
            if w.contains(x, y):
                return w
        return None

    def run(self, frames=None, interval=0.0):
        """跑帧循环。frames=None 表示一直跑, 直到 `stop()` 或 KeyboardInterrupt。"""
        self.running = True
        try:
            n = 0
            while self.running and (frames is None or n < frames):
                self.step()
                n += 1
                if interval:
                    time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
        return n

    def stop(self):
        self.running = False


# ---------------- 演示 ----------------

def demo_ui(lcd, source=None, shadow=False):
    """一屏简单的演示界面: 标题 + 两个按钮 + 一个进度条。返回 Ui(方便测试驱动)。"""
    ui = Ui(lcd, source=source, background=0x0861, shadow=shadow)
    ui.add(Label(24, 18, "AC820 PYNQ 显示", size=28, fg=WHITE))
    ui.add(Label(24, 60, "800x480 RGB565  /  DDR 帧缓冲", size=15, fg=GREY))
    bar = ui.add(ProgressBar(24, 100, 500, 26, value=0.25))

    def bump(_btn, delta):
        bar.value = min(1.0, max(0.0, bar.value + delta))

    ui.add(Button(24, 170, 220, 70, "加 25%", on_tap=lambda b: bump(b, 0.25)))
    ui.add(Button(264, 170, 220, 70, "清零", on_tap=lambda b: setattr(bar, "value", 0.0)))
    ui.add(Label(24, 270, "点按钮可改进度条(需要触摸或脚本输入)", size=15, fg=GREY))
    return ui


def frame_to_image(array):
    """RGB565 帧缓冲 -> PIL 图像(给预览/存档用)。"""
    rgb = np.zeros(array.shape + (3,), dtype=np.uint8)
    rgb[..., 0] = ((array >> 11) & 0x1F) << 3
    rgb[..., 1] = ((array >> 5) & 0x3F) << 2
    rgb[..., 2] = (array & 0x1F) << 3
    return Image.fromarray(rgb)


def save_png(array, path):
    """把一整帧存成 PNG —— 不必接板子就能看画出来长什么样。"""
    frame_to_image(array).save(path)
    return path


def _main():
    import argparse
    ap = argparse.ArgumentParser(description="AC820 TFT 绘图/UI 演示")
    ap.add_argument("--bitfile", default="/home/xilinx/overlays/ledbtn/ledbtn.bit")
    ap.add_argument("--frames", type=int, default=300,
                    help="跑多少帧后退出(默认 300; 只显示的话 1 帧就够)")
    ap.add_argument("--demo", metavar="PNG",
                    help="不出板卡: 只把演示界面渲成 PNG 看效果(开发机上即可)")
    args = ap.parse_args()

    if args.demo:
        _render_demo_png(args.demo)
        return 0

    from pynq_lcd import Lcd
    lcd = Lcd.from_bitfile(args.bitfile)
    ui = demo_ui(lcd, shadow=True)          # 真上屏: 走影子缓冲, 免得面板扫到半屏
    ui.run(frames=args.frames, interval=1.0 / 30)
    print("draw %d frames, framebuffer at 0x%08X" % (ui.frames, lcd.address))
    return 0


def _render_demo_png(path):
    """把演示界面渲成 PNG —— 不必接板子就能看/改版式。"""
    frame = np.zeros((HEIGHT, WIDTH), dtype=np.uint16)

    class _FakeLcd:
        array = frame

    ui = demo_ui(_FakeLcd())
    ui.canvas.clear(ui.background)
    for w in ui.widgets:
        w.draw(ui.canvas)
    save_png(frame, path)
    print("wrote %s" % path)
    return path


if __name__ == "__main__":
    import sys
    sys.exit(_main())
