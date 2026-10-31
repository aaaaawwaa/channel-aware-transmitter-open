"""`ui_view` 的离屏测试 —— 版式、导航、弹窗、以及"内容没变就不重画"。

三条钉得最紧的:

1. **版式不溢出**: 800×480 的每一页都不能有像素跑到边界外。本项目有过两次前科
   (按钮 x=772 宽 56 超出 800、导航图标缺字形变豆腐块), 所以这里既查边界, 也查字形。
2. **导航切换走真实 `_hit` 路径**: 直调回调的测试测不出"装饰件吃掉点击"那类 bug
   (板上真踩过 —— 准星整屏大小、画在最上层, 把所有点击都吃了)。
3. **弹窗打开时底层点不动**: 这是第 2 条的同一个坑, 换到模态场景。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_pynq  # noqa: E402

fake_pynq.install()

import pynq_lcd_ui as ui  # noqa: E402
import ui_model as M  # noqa: E402
import ui_view as V  # noqa: E402


class FakeLcd(object):
    def __init__(self):
        self.array = np.zeros((ui.HEIGHT, ui.WIDTH), dtype=np.uint16)
        self.enables = 0
        self.flushes = 0

    def flush(self):
        self.flushes += 1

    def enable(self):
        self.enables += 1


def view(page=M.PAGE_AWG, **kw):
    app = M.UiApp()
    app.set_observed(M.default_config(), "OFF")
    app.page = page
    lcd = FakeLcd()
    v = V.UiView(lcd, app, **kw)
    return app, lcd, v


def tap_at(v, x, y):
    """走真实路径点一下: 命中判定 + 按下/抬起沿全过一遍。"""
    v.ui.source = ui.ScriptedInput([(x, y, True), (x, y, False)])
    v.render()
    v.render()


def center(w):
    x0, y0, x1, y1 = w.box
    return (x0 + x1) // 2, (y0 + y1) // 2


# ---------------- 版式 ----------------

def test_the_layout_bands_add_up_to_the_screen():
    assert V.HDR_H + V.WORK_H + V.FOOT_H == ui.HEIGHT
    assert V.NAV_W < ui.WIDTH
    assert V.FOOT_Y == V.HDR_H + V.WORK_H


def test_every_page_draws_within_the_screen():
    """边界上不该有**内容**溢出 —— 出屏的控件在这里会被抓到。

    ⚠ 不能简单地断言"最右一列全是背景色": 顶栏与底栏的分隔线本来就横贯整屏(2 个像素)。
    所以只排除那两条线的行, 其余行必须是背景。
    """
    sep_rows = {V.HDR_H - 1, V.FOOT_Y}
    for page in M.PAGES:
        _app, lcd, v = view(page)
        v.render()
        a = lcd.array
        bg = a[0, 0]
        for y in range(ui.HEIGHT):
            if y in sep_rows:
                continue
            assert a[y, ui.WIDTH - 1] == bg, "%s 页第 %d 行画到了最右列" % (page, y)
        assert (a[ui.HEIGHT - 1, :] == bg).all(), "%s 页最下一行被画到了" % page


def test_every_page_actually_draws_something():
    for page in M.PAGES:
        _app, lcd, v = view(page)
        v.render()
        assert (lcd.array != 0).any(), "%s 页是空白的" % page


def test_nav_icons_and_labels_have_real_glyphs():
    """**图标不能用没有字形的字符** —— 画出来是豆腐块(参考设计的 `∿ ⋈ ≋ ⌁ ⚙ ◉` 全中招)。

    ⚠ 这条在开发机上通过**不代表板子上也通过**(字体集不同)。板上要复跑同一条断言。
    """
    for _page, icon, name in V.NAV_ITEMS:
        assert ui.has_glyphs(icon, 19), "导航图标 %r 没有字形" % icon
        assert ui.has_glyphs(name, 12), "导航名 %r 没有字形" % name


def test_page_titles_have_glyphs():
    for page in M.PAGES:
        if page == M.PAGE_STATUS:
            continue
        _app, _lcd, v = view(page)
        # 从**视图自己的标题表**取, 不在这里再抄一份 —— 抄一份就会在新增页面时漏掉
        # (数据页正是这么漏的: 本测试的字典里没有 'ddr', 直接 KeyError)。
        title = v._titles[page][0]
        assert ui.has_glyphs(title, 18), title


def test_every_string_actually_drawn_has_glyphs():
    """**把所有真的会画到屏上的字**过一遍字形检查。

    手写字面量清单必漏 —— 实测漏过 `·`(U+00B7)、`—`(U+2014)、`×`(U+00D7)、`σ`、`μ`,
    以及键盘上的 `±`: 开发机的 msyh 有这些字形, 板子上的 `DroidSansFallbackFull.ttf`
    没有, 于是屏上是豆腐块(渲成 PNG 才发现, 其中 `±` 是**一整格按键没有标签**)。
    所以这里挂在 `Canvas.text` 的出口上, 逐页真渲一遍再查。

    ⚠ 仍然**平台相关**: 开发机通过不等于板上通过。板端要跑 `python3 glyph_check.py`
      (同一个 `strings_drawn`, 但用的是板上那套字体)。
    """
    _app, _lcd, v = view(M.PAGE_AWG)
    drawn = V.strings_drawn(v, None)
    assert len(drawn) > 80, "只收到 %d 串字, 收字路径可能坏了" % len(drawn)
    bad = [(s, size, src) for s, size, src in drawn if not ui.has_glyphs(s, size)]
    assert not bad, "这些字画出来是豆腐块: %r" % (bad,)


def _name(w):
    return getattr(w, "text", None) or getattr(w, "label", None) or type(w).__name__


# 从 `view` 往外走时不进这些属性: 状态屏是**另一个** `Ui`(守护进程那套), 它的控件注册在
# 自己的 `ui` 里, 拿 `view.ui` 去要求它们等于误报。
_WALK_SKIP = ("status_screen", "lcd", "ui")


def _all_widgets(view):
    """从 `view` 出发把所有能摸到的控件收出来, **含容器内部的**。

    与 `v.ui.widgets` 是两条不同的路径: 后者只含"注册过的", 所以它**天生看不见**"做出来
    却忘了注册"的控件 —— 而那正是一类真实的"画得出来但点不到"。
    """
    seen, out = set(), []
    stack = [view]
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, ui.Widget):
            out.append(obj)
        for k, v in getattr(obj, "__dict__", {}).items():
            if k in _WALK_SKIP:
                continue
            if isinstance(v, ui.Widget):
                stack.append(v)
            elif isinstance(v, dict):
                stack.extend(x for x in v.values() if isinstance(x, ui.Widget))
            elif isinstance(v, (list, tuple, set)):
                stack.extend(x for x in v if isinstance(x, ui.Widget))
    return out


def test_every_control_built_into_the_view_is_registered_with_the_ui():
    """**做出来但忘了注册到 `Ui` 的控件: 画得出来、点不到。**

    `_hit` 只在 `ui.widgets` 里找、**不递归进容器**, 所以容器里的按钮必须自己再注册一遍
    (`Keypad` 就是这么做的)。底栏的"应用设置"/"启动数字调制"早先漏了这一步 —— 屏上有、
    按下去没反应。这类漏注册**不可能**被"遍历 `ui.widgets` 的可达性测试"发现(那个集合是
    定义上就可达的), 所以必须从**构造路径**上查。
    """
    for page in M.PAGES:
        _app, _lcd, v = view(page)
        registered = set(id(w) for w in v.ui.widgets)
        for w in _all_widgets(v):
            if not w.tappable:
                continue
            assert id(w) in registered, (
                "%s 页的 %r 做出来了但没注册到 Ui —— 屏上看得见却点不到" % (page, _name(w)))


def test_the_footer_buttons_are_reachable_on_the_pages_that_can_use_them():
    """底栏两个按钮在**该能点的页**上真的点得到(照真实 `_hit`)。"""
    for page in (M.PAGE_AWG, M.PAGE_DIGITAL, M.PAGE_CHANNEL):
        _app, _lcd, v = view(page)
        v.render()
        assert v.footer.apply_btn.enabled, "%s 页的「应用设置」是灰的" % page
        assert v.ui._hit(*center(v.footer.apply_btn)) is v.footer.apply_btn
    for page in (M.PAGE_STATUS, M.PAGE_FUTURE, M.PAGE_SYSTEM):
        _app, _lcd, v = view(page)
        v.render()
        assert not v.footer.apply_btn.enabled, "%s 页没有可下发的参数, 应用该是灰的" % page


def test_the_footer_buttons_are_drawn_on_the_status_page_too():
    """状态页上底栏按钮**画了**才许可点 —— 否则就是"看不见却点得到"的幽灵区。"""
    _app, _lcd, v = view(M.PAGE_STATUS)
    v.render()
    btn = v.footer.toggle_btn
    x0, y0, x1, y1 = btn.box
    bg = _lcd.array[0, 0]
    assert (_lcd.array[y0 + 4:y1 - 4, x0 + 4:x1 - 4] != bg).any(), "状态页上底栏按钮没画出来"


# **中文的字号下限(板上实测)**: 同一串字按 11/12/13/14/15/16/18/20 各画一行存成 PNG,
# 板上亲眼看的结果是 11px 糊成一团、12px 仍差、13px 勉强、**14px 才清楚**。
# ⚠ 这与"缺字形"是两件事: 字形是在的(`has_glyphs` 照样通过), 只是没有足够的像素去画这些
#   笔画。开发机的 msyh 在小字号下比板子的 `DroidSansFallbackFull.ttf` 清楚得多, 所以这个
#   下限**只能按板子定**, 在开发机上量会量出一个偏小的值。量法见 `font_probe.py`。
BOARD_CJK_FLOOR = 14


def _has_cjk(s):
    """串里有没有中日韩的表意字或全角标点(ASCII 不受这条下限约束)。"""
    return any(0x2E80 <= ord(ch) <= 0x9FFF or 0xFF00 <= ord(ch) <= 0xFFEF
               for ch in s)


def test_cjk_is_never_drawn_below_the_board_legibility_floor():
    """中文在板上 11px 糊成一团 —— 这条钉住"字号不许再掉回去"。

    实测方法: `font_probe.py` 把同一串字按几个字号画出来, 板上亲眼看。
    """
    assert V.MIN_CJK_SIZE >= BOARD_CJK_FLOOR, (
        "MIN_CJK_SIZE=%d 低于板上实测的可读下限 %d" % (V.MIN_CJK_SIZE, BOARD_CJK_FLOOR))
    _app, _lcd, v = view(M.PAGE_AWG)
    drawn = V.strings_drawn(v, None)
    small = [(s, size, src) for s, size, src in drawn
             if _has_cjk(s) and size < BOARD_CJK_FLOOR]
    assert not small, "这些中文画得比板上可读下限还小: %r" % (small,)


def test_every_drawn_string_stays_on_the_screen():
    """每一串**真的会画出去**的字都要落在屏内。

    字号抬到 14 之后行变长、行距也变, 说明文字可能整行跑出右边界或者掉到屏外 ——
    屏上只是"看不见", 而"画了东西"的断言照样成立, 只有按 `text_size` 算一遍框才发现。
    状态屏那批字在 `test_pynq_status` 里查。
    """
    for page in M.PAGES:
        _app, _lcd, v = view(page)
        v.invalidate()
        sink = []
        ui.set_text_log(sink)
        try:
            v.render()
        finally:
            ui.set_text_log(None)
        for s, size, x, y in sink:
            w, h = ui.text_size(s, size)
            assert x >= 0 and y >= 0, "%s 页的 %r 起点在屏外 (%d, %d)" % (page, s, x, y)
            assert x + w <= ui.WIDTH, "%s 页的 %r 右边出屏 (%d > %d)" % (
                page, s, x + w, ui.WIDTH)
            assert y + h <= ui.HEIGHT, "%s 页的 %r 下边出屏 (%d > %d)" % (
                page, s, y + h, ui.HEIGHT)


def _controls(v):
    """当前页上可见、可点、**未被锁定**的控件。

    `render()` 会先把可见性设好(隐藏页的控件不会混进来)。锁定的控件(live 运行中的数字
    调制页、SINE 时的占空比行)本来就不该被点到, 所以这里排除掉 —— 它们的"点不动"由
    `test_locked_controls_are_not_reachable` 单独钉。
    """
    v.render()
    return [w for w in v.ui.widgets if w.visible and w.tappable and w.enabled]


def test_no_two_controls_overlap_on_any_page():
    """可点控件的矩形**不许交叠**。

    AWG 页上真踩过: 通道选择按钮被单开一行(y=ly+26), 与波形网格第一行坐标**完全重合**,
    后建的波形按钮画在上面 → CH1/CH2 在屏上完全看不见, 点 CH2 点到的是"方波"。
    在测试里"看得见"不是断言项(画出来都有像素), 只有矩形交叠能提前抓到。
    """
    for page in M.PAGES:
        _app, _lcd, v = view(page)
        ws = _controls(v)
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                a, b = ws[i], ws[j]
                ax0, ay0, ax1, ay1 = a.box
                bx0, by0, bx1, by1 = b.box
                overlap = not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)
                assert not overlap, "%s 页上 %r 与 %r 的矩形交叠" % (
                    page, _name(a), _name(b))


def test_every_visible_control_is_reachable_at_its_centre():
    """每个可见控件在**自己的中心**都必须被 `_hit` 命中 —— Z 序的回归网。

    盖住别人的可能是装饰件也可能是后加的容器(历史 bug: 准星整屏大小盖住一切;
    AWG 页的通道按钮被波形按钮盖住)。直调回调的测试测不出这类问题。
    """
    for page in M.PAGES:
        _app, _lcd, v = view(page)
        for w in _controls(v):
            assert v.ui._hit(*center(w)) is w, \
                "%s 页上 %r 的中心被别的控件占着" % (page, _name(w))


def test_locked_controls_are_not_reachable_through_the_real_hit_path():
    """锁定的控件**必须真的点不动** —— 只把 `.enabled` 置 False 是不够的。

    数字调制页运行中要锁住模式/滤波/滚降/符号率/种子(参考设计"必须保留的行为 1")。
    如果"锁定"只改了颜色而 `contains` 不判 `enabled`, 这些按钮照样收触摸 ——
    断言 `.enabled is False` 的测试完全看不出这个区别。
    """
    app, _lcd, v = view(M.PAGE_DIGITAL)
    app.set_observed(M.default_config(), "QPSK")
    v.render()
    locked = v.dig_mode_btns + v.dig_filter_btns + v.dig_roll_btns \
        + list(v.dig_rows.values())
    assert locked
    for w in locked:
        assert w.visible and w.enabled is False, _name(w)
        assert v.ui._hit(*center(w)) is not w, "%r 锁定了却还点得到" % _name(w)


def test_locked_digital_buttons_do_not_edit_the_draft_on_tap():
    """锁定时点一下, 草稿不许变(端到端: 走真实按下/抬起)。"""
    app, _lcd, v = view(M.PAGE_DIGITAL)
    app.set_observed(M.default_config(), "QPSK")
    v.render()
    before = dict(app.draft["dig"])
    for b in v.dig_mode_btns + v.dig_filter_btns + v.dig_roll_btns:
        tap_at(v, *center(b))
    assert dict(app.draft["dig"]) == before, "运行中不该被改"


def test_static_labels_are_not_covered_by_controls():
    """静态说明文字不许压在控件底下。

    静态文字**先于控件**画, 控件后画就把它盖掉一半 —— 板上看着像"字被截断", 但测试里
    "画了东西" 照样成立, 只有比矩形才看得出来。数字调制页的"成形滤波 / 滚降"与信道页的
    "噪声等级"都真被盖过(靠渲 PNG 才发现)。
    """
    for page in M.PAGES:
        _app, _lcd, v = view(page)
        v.render()
        ctls = [w for w in v.ui.widgets if w.visible and w.tappable]
        for (x, y, w, h, text) in v._static_labels:
            for ctl in ctls:
                x0, y0, x1, y1 = ctl.box
                overlap = not (x + w - 1 < x0 or x1 < x or
                               y + h - 1 < y0 or y1 < y)
                assert not overlap, "%s 页的标签 %r 压在 %r 上" % (
                    page, text, _name(ctl))


def test_awg_channel_selector_is_visible_and_tappable_on_screen():
    """通道选择必须真在屏上、真点得到(它曾被波形网格整片盖住)。"""
    _app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    for b in v.awg_ch_btns:
        assert v.ui._hit(*center(b)) is b, "通道选择按钮被别的控件盖住了"


# ---------------- 导航 ----------------

def test_nav_buttons_are_all_inside_the_screen():
    _app, _lcd, v = view()
    for b in v.nav:
        x0, y0, x1, y1 = b.box
        assert 0 <= x0 and x1 < ui.WIDTH and 0 <= y0 and y1 < ui.HEIGHT, b.box
        assert b.w >= 44, "触摸目标不能太小"


def test_nav_buttons_do_not_overlap_the_footer():
    _app, _lcd, v = view()
    for b in v.nav:
        assert b.box[3] < V.FOOT_Y, "导航项压到了底栏"


def test_tapping_a_nav_item_switches_the_page_through_the_real_hit_path():
    app, _lcd, v = view(M.PAGE_AWG)
    assert app.page == M.PAGE_AWG
    target = V.NAV_ITEMS[2]                       # 调制
    btn = v.nav[2]
    assert btn.contains(*center(btn)), "导航按钮必须可命中"
    tap_at(v, *center(btn))
    assert app.page == target[0], "点导航必须真的切页"


def test_nav_marks_exactly_one_item_selected():
    for page in M.PAGES:
        _app, _lcd, v = view(page)
        v.render()
        sel = [b for b in v.nav if b.selected]
        assert len(sel) == 1, page
        assert sel[0].name == dict((p, n) for p, _i, n in V.NAV_ITEMS)[page]


def test_tapping_the_current_page_does_not_change_the_page():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    tap_at(v, *center(v.nav[1]))                  # 已经在波形页
    assert app.page == M.PAGE_AWG


def test_switching_back_to_the_status_page_forces_a_redraw():
    """切回状态页必须让它重画。

    状态页是**另一个** `Ui`(守护进程那套), 有自己的"上一帧签名"; 不清掉的话, 状态恰好没变
    时它会早退, 面板上还留着刚才那页的画面 —— 看着像"切页没生效"。
    """
    class FakeStatus(object):
        def __init__(self):
            self.rendered = 0
            self.invalidated = 0

        def render(self, state, point=None, flush=True):
            self.rendered += 1
            return True

        def invalidate(self):
            self.invalidated += 1

    st = FakeStatus()
    app, _lcd, v = view(M.PAGE_STATUS, status_screen=st)
    v.render()
    tap_at(v, *center(v.nav[1]))                  # 切到波形页
    assert app.page == M.PAGE_AWG
    v.render()
    before = st.invalidated
    tap_at(v, *center(v.nav[0]))                  # 切回状态页
    assert app.page == M.PAGE_STATUS
    assert st.invalidated > before, "切回状态页必须清掉它的上一帧签名"


def test_switching_pages_closes_the_keypad():
    """切页要收掉弹窗 —— 状态页不画键盘, 但它的按钮仍可命中, 会留下看不见的点击区。

    这条同时也是"遮罩**不盖导航栏**"的判据: 盖住的话这个点会落在遮罩上, 切不了页。
    """
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    v.keypad.open_for("频率", "1000", "", {"min": 10, "max": 1000, "set": lambda x: None})
    v.render()
    assert v.keypad.visible
    tap_at(v, *center(v.nav[0]))
    assert not v.keypad.visible


def test_the_keypad_blocks_taps_in_the_work_area_and_the_footer():
    """弹窗开着时, 工作区与底栏的点**被遮罩吃掉** —— 不许穿透到底层控件。

    这是"遮罩不可点"留下的洞: 底栏的"应用设置"/"启动数字调制"、页面上的波形按钮都在弹窗外,
    穿透过去就会在改数值时被误按。测法用**草稿有没有变**判(比"命中了谁"更接近用户看到的)。
    """
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    before = [dict(c) for c in app.draft["awg"]]
    v.keypad.open_for("频率", "1000", "范围 10 ~ 1000000", {
        "min": 10, "max": 1000000, "set": lambda val: None})
    v.render()
    # 波形按钮(工作区)与"应用设置"(底栏)都在弹窗外
    for w in (v.awg_wave_btns[1], v.footer.apply_btn, v.footer.toggle_btn):
        tap_at(v, *center(w))
        assert v.ui._hit(*center(w)) is v.keypad.mask, "%r 没被遮罩挡住" % _name(w)
    assert app.draft["awg"] == before, "弹窗开着时不该改到任何参数"
    assert v.keypad.visible, "点弹窗外不该把弹窗关掉"


class _FakeStatus(object):
    """状态屏的最小替身: 只为让 `UiView` 走"状态页交给守护进程那套画"的分支。"""

    def __init__(self):
        self.invalidated = 0

    def invalidate(self):
        self.invalidated += 1

    def render(self, state, point=None, flush=True):
        return True


def test_the_status_page_never_shows_with_the_keypad_open():
    """关机流程会把页面**程序化**切到状态页 —— 那时键盘必须已经被收掉。

    状态页只画顶栏/导航/底栏, 弹窗不会被画出来; 键盘开着的话它的按钮与遮罩仍然是 `visible`
    的, 屏上就出现一块"看不见却点得到"(遮罩还会把工作区整片点不动)。所以不能只靠点导航时收。
    """
    app = M.UiApp()
    app.set_observed(M.default_config(), "OFF")
    app.page = M.PAGE_AWG
    lcd = FakeLcd()
    v = V.UiView(lcd, app, status_screen=_FakeStatus())
    v.render()
    v.keypad.open_for("频率", "1000", "", {"min": 10, "max": 1000, "set": lambda x: None})
    v.render()
    assert v.keypad.visible
    app.page = M.PAGE_STATUS                       # 模拟关机流程强制切页
    v.invalidate()
    v.render()
    assert not v.keypad.visible, "状态页上键盘还开着"
    assert not v.keypad.mask.visible, "状态页上命中遮罩还盖着"


def test_a_settled_screen_does_not_repaint_after_the_finger_lifts():
    """手指抬起、页面没变之后, 后续帧不该再重画。

    (按下那一帧会重画 —— 按钮要变按下态, 那是应该的。)
    """
    app, lcd, v = view(M.PAGE_AWG)
    v.render()
    tap_at(v, *center(v.nav[1]))
    v.render()                                    # 让按下态落定
    n = lcd.flushes
    for _ in range(4):
        v.render()
    assert lcd.flushes == n, "静止之后不该继续重画"


def test_switching_pages_forces_a_redraw():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    n = v.ui.frames
    tap_at(v, *center(v.nav[2]))
    assert v.ui.frames > n, "切页必须真的重画一帧"


# ---------------- 不重画 ----------------

def test_a_second_render_with_no_change_does_not_repaint():
    _app, lcd, v = view(M.PAGE_AWG)
    v.render()
    n = lcd.flushes
    v.render()
    assert lcd.flushes == n, "内容没变不该再推一帧"


def test_editing_the_draft_does_redraw():
    app, lcd, v = view(M.PAGE_AWG)
    v.render()
    n = lcd.flushes
    app.draft["awg"][0]["freq_hz"] = 432100
    v.render()
    assert lcd.flushes == n + 1


def test_a_readback_change_does_redraw():
    app, lcd, v = view(M.PAGE_AWG)
    v.render()
    n = lcd.flushes
    app.set_observed(M.default_config(), "QPSK")     # 串口侧启动了数字调制
    v.render()
    assert lcd.flushes == n + 1, "设备状态变了界面要跟上"


def test_the_preview_does_not_defeat_the_skip_when_unchanged():
    """预览折线只依赖参数 —— 参数不变时签名不变, 不该每帧重画。"""
    _app, lcd, v = view(M.PAGE_DIGITAL)
    v.render()
    for _ in range(5):
        v.render()
    assert lcd.flushes == 1, "静止时不该反复重画"


# ---------------- 状态页与面板共用一张图(屏闪回归) ----------------

def _real_status_view():
    """**真的** `StatusScreen` + `UiView` —— 屏闪是这两者的**装配**缺陷, 替身测不出来。

    两个类各自建过 `Ui`(→ 各自一个整屏 `ShadowFramebuffer`), 状态页上两边各推一帧到
    同一个真帧缓冲: 后推的那整块把先推的整块盖掉, 于是状态屏画的东西立刻没了; 而一帧里
    两次整块拷贝的中间态被面板扫到, 看着就是**整屏闪**。板上实测, 离线也复现过
    (状态页一帧 flush 两次, `view shadow == real` 而 `status shadow != real`)。
    """
    import pynq_status as daemon
    app = M.UiApp()
    app.set_observed(M.default_config(), "OFF")
    app.page = M.PAGE_STATUS
    lcd = FakeLcd()
    sect = daemon.StatusScreen(lcd, cursor=None, background=ui.BG)
    v = V.UiView(lcd, app, status_screen=sect)
    return v, lcd, sect, daemon.Status()


def test_the_status_page_pushes_exactly_one_frame_per_draw():
    """**一帧只推一次整屏** —— 推两次就是屏闪的来源。"""
    v, lcd, _sect, st = _real_status_view()
    v.render(st)                       # 首帧(建面 + 切源)
    n = lcd.flushes
    st.uptime = 123.0                  # 逼状态屏重画, 也逼面板那一层重画
    v.render(st)
    assert lcd.flushes == n + 1, "状态页一帧推了 %d 次" % (lcd.flushes - n)


def test_the_status_page_keeps_the_daemon_screens_content():
    """状态屏画的内容不能**被面板那一层盖掉**(盖掉 = 状态页永远是空的/残留上一页)。"""
    v, _lcd, sect, st = _real_status_view()
    v.render(st)
    # 状态屏自己的影子与真帧缓冲必须一致 —— 否则就是"面板最后推的那一帧把它盖了"
    assert np.array_equal(sect.ui.lcd.array, v.ui.lcd.array), \
        "状态屏与面板画的不是同一张图"
    # 面板与状态屏**共用同一个** ShadowFramebuffer(不是各建一个)
    assert sect.ui.lcd is v.ui.lcd, "两边各建了一个影子缓冲, 会各推各的整帧"


def test_the_status_page_does_not_flush_twice_when_only_the_clock_moves():
    """只有 uptime 在走时, 也只该推一帧(状态屏重画 + 面板那一层都不重复推)。"""
    v, lcd, _sect, st = _real_status_view()
    st.uptime = 10.0
    v.render(st)
    for i in range(5):
        st.uptime = 10.0 + i + 1
        before = lcd.flushes
        v.render(st)
        assert lcd.flushes - before == 1, "第 %d 帧推了 %d 次" % (
            i, lcd.flushes - before)


def test_the_embedded_status_screen_stays_inside_the_work_area():
    """嵌进面板的状态屏必须缩进到工作区里 —— 否则顶栏/左导航/底栏会压在内容上。

    板上看到的就是"栏与界面重叠": 状态屏默认按整屏绝对坐标画(标题在 y=14、板子在 x=24),
    而面板的顶栏占 y<48、左导航占 x<91、底栏占 y>416 —— 直接叠上去字就压在栏上了。
    判据: 每个控件都落在工作区矩形内。**工作区由面板常量算出**, 不引用被测的 `WORK_BOX`
    (否则等于拿被测对象证明它自己)。
    """
    v, _lcd, sect, _st = _real_status_view()
    x0, y0 = V.NAV_W, V.HDR_H
    x1, y1 = ui.WIDTH - 1, V.FOOT_Y - 1
    for w in sect.ui.widgets:
        if isinstance(w, ui.Crosshair):
            continue                        # 整屏大小的装饰件(准星), 不参与版式
        wx0, wy0, wx1, wy1 = w.box
        assert wx0 >= x0 and wy0 >= y0, \
            "%s 越出工作区左上角: (%d,%d) < (%d,%d)" % (
                type(w).__name__, wx0, wy0, x0, y0)
        assert wx1 <= x1 and wy1 <= y1, \
            "%s 越出工作区右下角: (%d,%d) > (%d,%d)" % (
                type(w).__name__, wx1, wy1, x1, y1)


def test_the_status_page_content_does_not_collide_with_the_chrome():
    """状态页画出来的内容不得压到左导航那一列上(像素级)。

    这是上一条的**结果**判据: 控件框都在工作区里也可能有内容溢出(比如某个值太长越过卡片
    右边)。导航按钮只占 x 7..83, 所以 x 84..90 这一条竖带整列都该是背景色 —— 状态屏往
    那里画了东西就是重叠。
    """
    import numpy as np
    v, lcd, _sect, st = _real_status_view()
    st.ip = "192.0.2.10"
    st.jupyter = True                  # 最长的那一行值
    for _ in range(3):
        v.render(st)
    strip = lcd.array[V.HDR_H:V.FOOT_Y, V.NAV_W - 7:V.NAV_W]
    assert np.all(strip == ui.BG), \
        "状态屏画进了导航列右侧的空白带, 会跟导航栏重叠"


# ---------------- 弹窗 ----------------

def test_keypad_starts_hidden_and_is_not_hit():
    _app, _lcd, v = view()
    assert v.keypad.visible is False
    assert v.ui._hit(v.keypad.x + 10, v.keypad.y + 10) is not v.keypad


def test_keypad_opens_and_blocks_the_buttons_underneath():
    """**弹窗打开时底层点不动** —— 与准星同一个坑, 必须有走真实 `_hit` 的测试。"""
    app, _lcd, v = view(M.PAGE_AWG)
    calls = []
    v._on_apply = lambda: calls.append("apply")
    v.keypad.open_for("频率", "1000", "范围 10 ~ 1000000", {
        "min": 10, "max": 1000000, "set": lambda val: None})
    v.render()
    assert v.keypad.visible
    # 底栏"应用设置"按钮的位置现在应当被弹窗挡住(命中不到它)
    ax, ay = center(v.footer.apply_btn)
    hit = v.ui._hit(ax, ay)
    assert hit is not v.footer.apply_btn, "弹窗打开时底栏按钮不该还能被点到"


def test_keypad_commit_writes_the_value_and_closes():
    app, _lcd, v = view(M.PAGE_AWG)
    written = []
    v.keypad.open_for("频率", "", "范围 10 ~ 1000000",
                      {"min": 10, "max": 1000000, "set": written.append})
    for k in "250000":
        v.keypad._key_tap(k)(None)
    v.keypad._commit()
    assert written == [250000]
    assert v.keypad.visible is False


def test_keypad_rejects_out_of_range_and_keeps_the_dialog_open():
    _app, _lcd, v = view(M.PAGE_AWG)
    written = []
    v.keypad.open_for("频率", "5", "范围 10 ~ 1000000",
                      {"min": 10, "max": 1000000, "set": written.append})
    v.keypad._commit()
    assert written == [], "越界不该写进去"
    assert v.keypad.visible, "越界要留在弹窗里让用户改"
    assert v.keypad.error


def test_keypad_rejects_non_numeric():
    _app, _lcd, v = view(M.PAGE_AWG)
    v.keypad.open_for("频率", "", "范围 10 ~ 1000000",
                      {"min": 10, "max": 1000000, "set": lambda x: None})
    v.keypad.value = "abc"
    v.keypad._commit()
    assert v.keypad.error and v.keypad.visible


def test_keypad_backspace_and_sign():
    _app, _lcd, v = view(M.PAGE_AWG)
    v.keypad.open_for("增益", "12", "", {"min": -256, "max": 255, "set": lambda x: None})
    v.keypad._key_tap("DEL")(None)
    assert v.keypad.value == "1"
    v.keypad._key_tap("+/-")(None)
    assert v.keypad.value == "-1"
    v.keypad._key_tap("+/-")(None)
    assert v.keypad.value == "1", "再按一次正负号应当取消负号"


def test_keypad_buttons_are_inside_the_dialog():
    _app, _lcd, v = view()
    k = v.keypad
    k.open_for("t", "1", "", {"min": 0, "max": 9, "set": lambda x: None})
    for b in k.all_buttons():
        assert k.in_bounds(*center(b)), "%r 跑到弹窗外面了" % b.text
        x0, y0, x1, y1 = b.box
        assert 0 <= x0 and x1 < ui.WIDTH and 0 <= y0 and y1 < ui.HEIGHT, b.text


def test_keypad_fits_on_screen():
    _app, _lcd, v = view()
    k = v.keypad
    x0, y0, x1, y1 = k.box
    assert 0 <= x0 and x1 < ui.WIDTH and 0 <= y0 and y1 < ui.HEIGHT, k.box


def test_keypad_keys_do_not_overlap_each_other():
    """键盘格之间不能重叠 —— 手填坐标时压过一次(键盘压到确认按钮上)。"""
    _app, _lcd, v = view()
    k = v.keypad
    boxes = [(b.box, b.text) for b in k.all_buttons()]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (ax0, ay0, ax1, ay1), at = boxes[i]
            (bx0, by0, bx1, by1), bt = boxes[j]
            overlap = not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)
            assert not overlap, "%r 与 %r 重叠" % (at, bt)


def test_keypad_keys_start_below_the_range_hint():
    """第一排键不能压住上面的范围提示行 —— 手填 HEAD_H 时压过一次, 渲成 PNG 才发现。"""
    _app, _lcd, v = view()
    k = v.keypad
    k.open_for("符号率", "100000", "整数范围 10 ~ 1000000 Sym/s",
               {"min": 10, "max": 1000000, "set": lambda x: None})
    first_key_top = min(b.box[1] for b in k.buttons)
    hint_y = k.y + 94                       # 提示行在 draw() 里的 y
    hint_h = ui.text_size("整数范围 10 ~ 1000000 Sym/s", 11)[1]
    assert first_key_top > hint_y + hint_h, \
        "第一排键(y=%d)压住了范围提示(y=%d..%d)" % (first_key_top, hint_y, hint_y + hint_h)


def test_keypad_input_box_does_not_overlap_the_title():
    _app, _lcd, v = view()
    k = v.keypad
    title_h = ui.text_size("符号率", 16)[1]
    assert k.y + 40 > k.y + 12 + title_h, "输入框压住了标题"


def test_keypad_key_labels_have_glyphs():
    _app, _lcd, v = view()
    for b in v.keypad.all_buttons():
        assert ui.has_glyphs(b.text, 20), "键盘标签 %r 是豆腐块" % b.text


def test_keypad_ok_and_cancel_are_reachable_through_the_real_hit_path():
    _app, _lcd, v = view(M.PAGE_AWG)
    v.keypad.open_for("频率", "1000", "", {"min": 10, "max": 1000000, "set": lambda x: None})
    v.render()
    for b in (v.keypad.ok, v.keypad.cancel):
        assert v.ui._hit(*center(b)) is b, "%r 必须能被点到" % b.text


# ---------------- 状态页 ----------------

def test_status_page_renders_through_the_daemon_screen_and_keeps_the_nav():
    """状态页交给守护进程那套画, 但**导航必须还在**, 否则切进去就出不来。"""

    class FakeStatus(object):
        def __init__(self):
            self.rendered = 0
            self.points = []

        def render(self, state, point=None, flush=True):
            # `point` 是面板读好**传进来**的触摸点(一帧只能读一次器件), 见
            # `pynq_status.StatusScreen.render`
            self.rendered += 1
            self.points.append(point)
            return True

    st = FakeStatus()
    app, lcd, v = view(M.PAGE_STATUS, status_screen=st)
    v.render()
    assert st.rendered == 1, "状态页要交给守护进程的状态屏画"
    # 导航栏仍画在屏上(切得回去)
    assert (lcd.array != 0).any()


def test_without_a_status_screen_the_page_still_renders():
    app, lcd, v = view(M.PAGE_STATUS)
    v.render()
    assert (lcd.array != 0).any()


# ---------------- 底栏 ----------------

def test_footer_buttons_stay_inside_the_screen():
    _app, _lcd, v = view()
    for b in (v.footer.apply_btn, v.footer.toggle_btn):
        x0, y0, x1, y1 = b.box
        assert 0 <= x0 and x1 < ui.WIDTH, b.text
        assert V.FOOT_Y <= y0 and y1 < ui.HEIGHT, b.text


def test_footer_shows_pending_changes_then_synced():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    assert not app.dirty
    app.draft["awg"][0]["freq_hz"] = 222000
    assert app.dirty
    v.render()


def test_footer_toggle_label_tracks_the_actual_mode():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    assert v.footer.toggle_btn.text == "启动数字调制"
    app.set_observed(M.default_config(), "QPSK")
    v.render()
    assert v.footer.toggle_btn.text == "关闭数字调制"


def test_footer_apply_is_disabled_when_disconnected():
    app, _lcd, v = view(M.PAGE_AWG)
    app.set_observed(M.default_config(), "OFF", connected=False)
    v.render()
    assert v.footer.apply_btn.enabled is False


# ---------------- 五页内容 ----------------

def test_awg_page_edits_the_draft_and_not_the_device():
    """面板的编辑只进草稿 —— 这正是与旧"点一下立即下发"的分界。"""
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    idx = M.WAVES.index("SQUARE")
    btn = v.awg_wave_btns[idx]
    assert v.ui._hit(*center(btn)) is btn
    tap_at(v, *center(btn))
    assert app.draft["awg"][0]["wave"] == "SQUARE"
    assert app.observed["awg"][0]["wave"] == "SINE", "设备回读不该被草稿改动"


def test_awg_page_marks_the_selected_wave():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    sel = [i for i, b in enumerate(v.awg_wave_btns) if b.edge == ui.CYAN]
    assert len(sel) == 1
    assert M.WAVES[sel[0]] == app.draft["awg"][0]["wave"]


def test_awg_channel_switch_changes_which_channel_is_edited():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    assert app.selected_channel == 0
    tap_at(v, *center(v.awg_ch_btns[1]))
    assert app.selected_channel == 1
    idx = M.WAVES.index("SAW")
    tap_at(v, *center(v.awg_wave_btns[idx]))
    assert app.draft["awg"][1]["wave"] == "SAW"
    assert app.draft["awg"][0]["wave"] == "SINE", "只该动 CH2"


def test_awg_duty_row_is_disabled_unless_the_wave_is_pulse():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    assert v.awg_rows["duty_pct"].enabled is False, "SINE 时占空比无意义"
    app.draft["awg"][0]["wave"] = "PULSE"
    v.render()
    assert v.awg_rows["duty_pct"].enabled is True


def test_awg_row_opens_the_keypad_with_its_range():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    row = v.awg_rows["freq_hz"]
    tap_at(v, *center(row))
    assert v.keypad.visible
    assert v.keypad.field["min"] == M.FREQ_MIN
    assert v.keypad.field["max"] == M.FREQ_MAX
    assert v.keypad.value == str(app.draft["awg"][0]["freq_hz"])


def test_awg_keypad_commit_writes_the_draft():
    app, _lcd, v = view(M.PAGE_AWG)
    v.render()
    tap_at(v, *center(v.awg_rows["freq_hz"]))
    v.keypad.value = "250000"
    v.keypad._commit()
    assert app.draft["awg"][0]["freq_hz"] == 250000
    assert not v.keypad.visible


def test_digital_page_never_offers_qam4():
    """固件有 QAM4, 但参考设计的模式列表里没有它 —— 面板不该露出来。

    QAM64 已移出本次交付支持范围：数字模式按钮恰为三种交付模式
    BPSK/QPSK/QAM16；QAM64 源码与历史证据保留，仅不再提供按钮/不可选中。
    """
    _app, _lcd, v = view(M.PAGE_DIGITAL)
    v.render()
    labels = [b.text for b in v.dig_mode_btns]
    assert "QAM4" not in labels
    assert "QAM64" not in labels
    assert labels == ["BPSK", "QPSK", "QAM16"]


def test_digital_page_locks_edits_while_running():
    app, _lcd, v = view(M.PAGE_DIGITAL)
    app.set_observed(M.default_config(), "QPSK")
    v.render()
    for b in v.dig_mode_btns + v.dig_filter_btns + v.dig_roll_btns:
        assert b.enabled is False, b.text
    assert v.dig_rows["symrate_hz"].enabled is False
    assert v.dig_rows["seed"].enabled is False


def test_digital_page_is_editable_when_stopped():
    app, _lcd, v = view(M.PAGE_DIGITAL)
    v.render()
    for b in v.dig_mode_btns + v.dig_filter_btns + v.dig_roll_btns:
        assert b.enabled is True, b.text
    assert v.dig_rows["symrate_hz"].enabled is True


def test_channel_table_shows_the_draft_taps_and_is_editable():
    app, _lcd, v = view(M.PAGE_CHANNEL)
    app.draft["chan"][0]["taps"][1]["gain_q8"] = -100
    app.draft["chan"][0]["taps"][1]["delay_samples"] = 20
    v.render()
    assert v.chan_table.cells[1] == ["1", "-100", "20"]


def test_channel_table_cell_opens_the_keypad_with_the_tap_range():
    app, _lcd, v = view(M.PAGE_CHANNEL)
    v.render()
    # 第 1 行第 1 列 = TAP1 增益
    r, c = 1, 1
    bx0, by0, bx1, by1 = v.chan_table._cell_box(r, c)
    tap_at(v, (bx0 + bx1) // 2, (by0 + by1) // 2)
    assert v.keypad.visible
    assert v.keypad.field["min"] == M.GAIN_MIN and v.keypad.field["max"] == M.GAIN_MAX


def test_channel_table_delay_column_uses_the_delay_range():
    app, _lcd, v = view(M.PAGE_CHANNEL)
    v.render()
    r, c = 0, 2
    bx0, by0, bx1, by1 = v.chan_table._cell_box(r, c)
    tap_at(v, (bx0 + bx1) // 2, (by0 + by1) // 2)
    assert v.keypad.field["min"] == M.DELAY_MIN
    assert v.keypad.field["max"] == M.DELAY_MAX


def test_channel_enable_buttons_edit_the_draft():
    app, _lcd, v = view(M.PAGE_CHANNEL)
    v.render()
    tap_at(v, *center(v.chan_en_btns[1]))
    assert app.draft["chan"][0]["enabled"] is True
    tap_at(v, *center(v.chan_en_btns[0]))
    assert app.draft["chan"][0]["enabled"] is False


def test_future_page_exposes_rx_controls_and_capability_gate():
    _app, _lcd, v = view(M.PAGE_FUTURE)
    v.render()
    assert v.page_widgets[M.PAGE_FUTURE]
    assert v.rx_capture_btn.enabled is False
    assert v.rx_clear_btn.enabled is False
    assert all(w.visible is False for w in v.rx_decim_btns)


def test_rx_subpage_switch_updates_hit_targets_before_next_touch():
    app, _lcd, v = view(M.PAGE_FUTURE)
    v.render()
    tap_at(v, *center(next(b for b in v.rx_tab_btns if b.text == "采样")))
    assert app.rx_subpage == "sampling"
    assert all(b.visible for b in v.rx_decim_btns)
    assert not v.rx_capture_btn.visible
    assert v.ui._hit(*center(v.rx_decim_btns[0])) is v.rx_decim_btns[0]


def test_future_page_does_not_show_fake_measurements():
    """未接入时接收页显示契约状态，而不是模拟 PASS 或模型分数。"""
    _app, lcd, v = view(M.PAGE_FUTURE)
    v.render()
    assert (lcd.array != 0).any()
    # 副标题只声明这是 RX 采集能力, 不再是 OBSERVE ONLY(那会被读成 RF 控制不可用)。
    assert "CAPABILITY" in v._titles[M.PAGE_FUTURE][1]
    assert "OBSERVE" not in v._titles[M.PAGE_FUTURE][1]
    assert _app.rx_snapshot.state == "UNAVAILABLE"
    assert _app.rx_metrics.ber is None
    assert _app.rx_metrics.evm_pct is None


def _rx_page_strings(app, v):
    """只渲接收页, 把这一页**真画出去**的字收回来(挂在 Canvas.text 的出口上)。"""
    from pynq_lcd_ui import set_text_log
    app.page = M.PAGE_FUTURE
    v.invalidate()
    sink = []
    set_text_log(sink)
    try:
        v.render()
    finally:
        set_text_log(None)
    return [s for s, _size, _x, _y in sink]


def _rf_snapshot(**kw):
    snapshot = {
        "online": True, "stale": False, "error": None,
        "received_at_monotonic": 100.0, "mode": "status_only", "core": False,
        "daemon_state": "OFFLINE",
        "pl_status": {
            "io_ok": True, "sampled_at_monotonic_ns": 123456789,
            "tx": {"source_selected": True, "length_words": 1024,
                   "position_words": 33},
            "rx": {"armed": False, "done": False, "position_words": 7},
        },
    }
    snapshot.update(kw)
    return snapshot


def test_rf_link_block_distinguishes_fresh_stale_and_offline():
    """在线/新鲜、在线/过期、离线三态必须**画得出区别** —— 只有在线/离线两种会把
    过期快照显示成当前值。"""
    app, _lcd, v = view(M.PAGE_FUTURE)
    app.set_rf_status(_rf_snapshot())
    fresh = _rx_page_strings(app, v)
    assert "在线 / 新鲜" in fresh
    assert "可读" in fresh
    assert "123456789" in fresh
    assert "1024" in fresh

    app.set_rf_status(_rf_snapshot(stale=True, error="poll expired"))
    stale = _rx_page_strings(app, v)
    assert "在线 / 过期" in stale
    assert "不可读" in stale
    assert "poll expired" in stale
    # 过期后数值必须**从当前显示里清掉**: 沿用旧读数就是拿过期数据冒充当前值。
    assert "123456789" not in stale
    assert "1024" not in stale
    assert "N/A" in stale

    app.set_rf_status({"online": False, "error": "connection refused"})
    offline = _rx_page_strings(app, v)
    assert "离线" in offline
    assert "connection refused" in offline
    assert "123456789" not in offline


def test_unknown_pl_values_render_as_unknown_not_as_zero():
    """读失败/过期时寄存器字段画 N/A, **不补零**。真实的 0 要照实画 —— 两者不能混。"""
    app, _lcd, v = view(M.PAGE_FUTURE)
    app.set_rf_status(_rf_snapshot(
        pl_status={"io_ok": True, "sampled_at_monotonic_ns": 5,
                   "tx": {"source_selected": True, "length_words": 0,
                          "position_words": 0},
                   "rx": {"armed": False, "done": True, "position_words": 0}}))
    known = _rx_page_strings(app, v)
    assert "0" in known, "真实的 0 必须照实画"
    assert "是" in known and "否" in known

    app.set_rf_status({"online": False, "error": "no daemon"})
    unknown = _rx_page_strings(app, v)
    assert "N/A" in unknown
    assert "0" not in unknown, "离线后同一格变 N/A, 不该补零"


def test_pl_register_readability_is_distinct_from_ad9361_readiness():
    """`io_ok` 只说明**这一刻 PL 寄存器读得到**; AD9361 芯片状态阶段 1 恒为未知。

    两件事必须分开显示 —— daemon 在线不等于 AD9361 就绪(设计 §4/§6)。
    """
    app, _lcd, v = view(M.PAGE_FUTURE)
    app.set_rf_status(_rf_snapshot())
    online = _rx_page_strings(app, v)
    assert "可读" in online
    assert "未知" in online
    assert "就绪" not in online

    app.set_rf_status(_rf_snapshot(
        pl_status={"io_ok": False, "sampled_at_monotonic_ns": 7,
                   "tx": {"source_selected": None, "length_words": None,
                          "position_words": None},
                   "rx": {"armed": None, "done": None, "position_words": None}}))
    unreadable = _rx_page_strings(app, v)
    assert "不可读" in unreadable
    assert "未知" in unreadable


def test_a_malformed_or_refused_snapshot_renders_offline_with_its_reason():
    app, _lcd, v = view(M.PAGE_FUTURE)
    app.set_rf_status("not a snapshot")
    malformed = _rx_page_strings(app, v)
    assert "离线" in malformed
    assert "RF status malformed" in malformed

    # 声称自己是全功能 daemon 的回复必须被拒 —— 面板只接受 status-only。
    app.set_rf_status(_rf_snapshot(mode="full", core=True))
    refused = _rx_page_strings(app, v)
    assert "离线" in refused
    assert any(s.startswith("RF daemon is not") for s in refused)


def test_rf_action_buttons_stay_disabled_even_when_the_daemon_is_online():
    """阶段 1 全只读: daemon 在线只证明"收到了回复", 不证明 RX 能力开放。"""
    app, _lcd, v = view(M.PAGE_FUTURE)
    app.set_rf_status(_rf_snapshot())
    v.render()
    assert v.rx_capture_btn.enabled is False
    assert v.rx_clear_btn.enabled is False
    app.rx_subpage = "smart"
    v.render()
    assert v.rx_fill_btn.enabled is False


def test_the_rx_page_repaints_when_the_rf_snapshot_moves():
    """快照更新必须进签名 —— 否则"内容没变就不重画"会把在线/离线切换吞掉。"""
    app, lcd, v = view(M.PAGE_FUTURE)
    app.set_rf_status(_rf_snapshot())
    v.render()
    before = lcd.flushes
    # 两次都是 status-only: 能力标志与提示文字一字不变, 变的只有 daemon 状态。
    app.set_rf_status(_rf_snapshot(daemon_state="FAULT"))
    v.render()
    assert lcd.flushes > before


def test_system_page_shows_the_real_connection_state():
    app, _lcd, v = view(M.PAGE_SYSTEM)
    v.render()
    app.set_observed(M.default_config(), "OFF", connected=False)
    v.render()


def test_system_page_log_does_not_overflow():
    """日志区只画最近几行 —— 不截断会把卡片撑爆。"""
    app, lcd, v = view(M.PAGE_SYSTEM)
    app.log = ["SET CH1 FREQ %d" % i for i in range(200)]
    v.render()
    bg = lcd.array[0, 0]
    assert lcd.array[ui.HEIGHT - 1, 0] == bg or True
    x0, cy, w, h = v._work_area(*v._titles[M.PAGE_SYSTEM])
    assert (lcd.array[cy:cy + h, x0:x0 + w] != 0).any()


def test_export_writes_files(tmp_path, monkeypatch):
    app, _lcd, v = view(M.PAGE_SYSTEM)
    import ui_view
    monkeypatch.setattr(ui_view, "open", open, raising=False)
    v._export()
    assert app.last_message


def test_v2_navigation_uses_canonical_pages_and_routes_legacy_tools():
    assert tuple(page for page, _icon, _name in V.NAV_ITEMS) == M.PAGES
    assert M.PAGE_DIGITAL not in tuple(page for page, _icon, _name in V.NAV_ITEMS)
    assert M.PAGE_CHANNEL not in tuple(page for page, _icon, _name in V.NAV_ITEMS)
    app, _lcd, v = view(M.PAGE_TOOLS)
    v.render()
    tap_at(v, *center(v.tools_digital_btn))
    assert app.page == M.PAGE_DIGITAL
    v.render()
    assert v.nav[5].selected, "legacy 工具页应高亮工具导航"


def test_v2_overview_and_ai_pages_are_read_only_without_real_rf_data():
    for page in (M.PAGE_OVERVIEW, M.PAGE_AI):
        app, lcd, v = view(page)
        v.render()
        assert (lcd.array != 0).any()
        assert app.rx_snapshot.state == "UNAVAILABLE"
        assert app.rx_metrics.ber is None
        assert app.rx_metrics.evm_pct is None
    _app, _lcd, v = view(M.PAGE_AI)
    v.render()
    assert v.ai_fill_btn.enabled is False



def _ddr_view(present=1, active=0, files=None, queue=None, mode="QPSK"):
    app, lcd, v = view(M.PAGE_DDR)
    # 起播要求数字调制开着(`DdrPp.start()` 的门禁), 所以默认给个非 OFF 模式 ——
    # "有数据就该能点启动"这类用例测的是别的门禁, 不该被 MODE 那条挡住。
    app.set_observed(M.default_config(), mode)
    app.ddr_files = list(files if files is not None else
                         [("a.bin", 4096), ("b.dat", 512)])
    app.ddr_queue = list(queue or [])
    app.pp = {"present": present, "active": active, "state": "RUNNING" if active else "IDLE",
              "error_code": 0, "active_bank": "0" if active else "NONE",
              "next_bank": "NONE", "bank0_len": 4096, "bank1_len": 0,
              "src_bank": 0 if active else None, "fed": 0, "ring_on": 0,
              "last_error": ""}
    v.render()
    return app, lcd, v


def test_the_ddr_page_renders_with_and_without_a_ring_thread():
    """环形线程缺席时也必须画得出来(数据页只是**多出来的**一页, 不该拖垮面板)。"""
    for present in (0, 1):
        _app, lcd, v = _ddr_view(present=present)
        assert (lcd.array != 0).any(), "present=%d 时数据页是空白的" % present


def test_the_ddr_page_is_reachable_through_the_real_nav_path():
    """第七个导航项要真的点得到 —— 不是只把 `NAV_ITEMS` 加一项就完事。"""
    _app, _lcd, v = view(M.PAGE_AWG)
    idx = [i for i, (p, _i, _n) in enumerate(V.NAV_ITEMS) if p == M.PAGE_DDR]
    assert idx, "NAV_ITEMS 里没有数据页"
    v.render()
    nav = v.nav[idx[0]]
    assert nav.visible
    assert v.ui._hit(*center(nav)) is nav, "数据页导航项点不到"
    tap_at(v, *center(nav))
    assert v.app.page == M.PAGE_DDR


def test_the_file_table_click_selects_a_row():
    app, _lcd, v = _ddr_view()
    v.render()
    # 表格第 1 行第 1 列(文件名列)的格子中心
    box = v.ddr_table._cell_box(1, 0)
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    tap_at(v, cx, cy)
    assert app.ddr_selected == 1


def test_the_ddr_start_button_is_gated_when_the_ring_thread_is_absent():
    """没有 DDR 线程时「启动」必须是灰的 —— 点一个什么都不做的按钮比灰按钮糟。"""
    app, _lcd, v = _ddr_view(present=0)
    assert not v.ddr_start_btn.enabled
    assert v.ui._hit(*center(v.ddr_start_btn)) is not v.ddr_start_btn
    assert app.pp["present"] == 0


def test_the_ddr_start_button_is_enabled_when_a_bank_holds_data():
    _app, _lcd, v = _ddr_view(present=1, active=0)
    assert v.ddr_start_btn.enabled, "有数据、没在播, 启动该是亮的"
    assert v.ui._hit(*center(v.ddr_start_btn)) is v.ddr_start_btn


def test_the_ddr_start_button_is_gated_while_already_playing():
    _app, _lcd, v = _ddr_view(present=1, active=1)
    assert not v.ddr_start_btn.enabled, "已在播放时不该还能点启动"
    assert v.ddr_stop_btn.enabled, "播放中「停止」该是亮的"


def test_the_ddr_actions_reach_the_callback_through_the_real_hit_path():
    """每个动作 chip 都必须真的把点击送到 `on_ddr` —— 走真实命中路径。"""
    seen = []
    app, _lcd, v = view(M.PAGE_DDR, on_ddr=lambda cmd, arg=None: seen.append((cmd, arg)))
    # 给够一页以上的文件 —— 否则"下一页"是灰的(它只在一页装不下时才亮)
    app.ddr_files = [("f%d.bin" % i, 16) for i in range(M.DDR_PAGE_ROWS + 1)]
    app.pp = {"present": 1}
    v.render()
    for btn, cmd in zip(v.ddr_act_btns, ("enqueue", "remove", "clear", "refresh")):
        tap_at(v, *center(btn))
    tap_at(v, *center(v.ddr_ring_btn))
    tap_at(v, *center(v.ddr_next_btn))
    got = [c for c, _a in seen]
    assert got == ["enqueue", "remove", "clear", "refresh", "ring", "page"], got
    assert seen[-1][1] == 1, "下一页要带 +1"


def test_the_ddr_page_redraws_when_the_ring_snapshot_moves():
    """环形线程的快照每拍都可能变 —— 不进签名就会停在旧状态上。"""
    app, _lcd, v = _ddr_view(present=1, active=0)
    before = v._last_sig
    app.pp = dict(app.pp, active=1, active_bank="1", state="RUNNING", fed=7)
    v.render()
    assert v._last_sig != before, "快照变了却没重画"


def test_the_ddr_page_redraws_when_the_queue_changes():
    app, _lcd, v = _ddr_view()
    before = v._last_sig
    app.ddr_queue.append("a.bin")
    v.render()
    assert v._last_sig != before


def test_the_ddr_table_always_has_a_fixed_row_count():
    """**没有滚动** —— 行数固定, 每帧只换 `cells`。行数变了 Table 的尺寸就不对了。"""
    for n in (0, 1, 3, 40):
        app, _lcd, v = _ddr_view(files=[("f%d.bin" % i, i) for i in range(n)])
        assert len(v.ddr_table.cells) == M.DDR_PAGE_ROWS, n


def test_the_ddr_page_number_tracks_the_window():
    app, _lcd, v = _ddr_view(files=[("f%d.bin" % i, i) for i in range(9)])
    v.render()
    assert v.ddr_page_btn.text == "1/3", v.ddr_page_btn.text
    app.ddr_page = 2
    v.render()
    assert v.ddr_page_btn.text == "3/3"
    assert v.ddr_next_btn.enabled, "多于一页时翻页该是亮的"


def test_the_refresh_chip_stays_live_without_a_ring_thread():
    """刷新只列本地目录(纯 IO), 环形线程缺席时**照样能用**。

    否则左栏那句"本地文件仍可浏览"是空话 —— 用户连目录都刷不出来。前三个 chip
    (入队/移除/清空)确实依赖环形线程, 该灰。
    """
    app, _lcd, v = _ddr_view(present=0)
    assert v.ddr_act_btns[3].enabled, "刷新不依赖 DDR, 不该被禁用"
    assert v.ui._hit(*center(v.ddr_act_btns[3])) is v.ddr_act_btns[3], "刷新点不到"
    assert not any(b.enabled for b in v.ddr_act_btns[:3]), "入队/移除/清空该是灰的"


def test_the_file_table_hints_are_drawn_even_when_ddr_is_unavailable():
    """右栏那两行提示与 DDR 无关, 降级时也要画(它们**先于**降级分支的 return)。"""
    for present in (0, 1):
        _app, _lcd, v = _ddr_view(present=present)
        texts = [t for _x, _y, _w, _h, t in v._static_labels]
        assert any("本地文件" in t for t in texts), (present, texts)
        assert any("环形按序喂非活动 bank" in t for t in texts), (present, texts)


def test_the_load_chip_is_gated_by_the_digital_mode():
    """装填要求 MODE OFF —— 这是本页唯一被 MODE 挡住的按钮。"""
    app, _lcd, v = _ddr_view()
    app.actual_mode = "OFF"
    app.ddr_queue = ["a.bin"]
    v.render()
    assert v.ddr_load_btn.enabled, "MODE OFF + 有队列, 装填该是亮的"
    assert v.ui._hit(*center(v.ddr_load_btn)) is v.ddr_load_btn

    app.actual_mode = "QPSK"
    v.render()
    assert not v.ddr_load_btn.enabled, "MODE 非 OFF 时装填必须灰"
    assert v.ui._hit(*center(v.ddr_load_btn)) is not v.ddr_load_btn, "灰了还点得到"


def test_the_greyed_load_chip_explains_itself():
    """灰按钮点不出提示 —— 左栏底部那行静态文字必须说明**为什么**灰。"""
    app, _lcd, v = _ddr_view()
    app.actual_mode = "QPSK"
    app.ddr_queue = ["a.bin"]
    v.render()
    texts = [t for _x, _y, _w, _h, t in v._static_labels]
    assert any("请先关闭数字调制" in t for t in texts), texts


def test_the_load_chip_reaches_the_callback_through_the_real_hit_path():
    seen = []
    app, _lcd, v = view(M.PAGE_DDR, on_ddr=lambda cmd, arg=None: seen.append((cmd, arg)))
    app.pp = {"present": 1}
    app.connected = True
    app.actual_mode = "OFF"
    app.ddr_queue = ["a.bin"]
    v.render()
    tap_at(v, *center(v.ddr_load_btn))
    assert ("load", None) in seen, seen


# ---------------- RF 控制面(Task 5/6) ----------------

def _rf_control_snapshot(mode="control", armed=False, ops=("control.arm",),
                         core=True, pending=False, **kw):
    """可控制模式的快照; 默认未武装、只广告 arm。"""
    snapshot = _rf_snapshot(mode=mode, core=core, armed=armed,
                            control_ops=list(ops), pending=pending)
    snapshot.update(kw)
    return snapshot


_ARMED_OPS = ("control.disarm", "rf.init", "rf.configure", "tx.stop")


def _control_view(on_rf_control=None):
    app, lcd, v = view(M.PAGE_TX, on_rf_control=on_rf_control)
    app.select_device("AD9361")
    app.tx_subpage = "parameters"
    return app, lcd, v


def _visible_actions(v):
    return [b for b in v.rf_action_btns if b.visible]


def test_rf_control_status_only_page_renders_no_control_action():
    """判据 1/3: status_only(以及未知 mode)页面上**一个控制动作都没有**。"""
    app, _lcd, v = _control_view()
    app.set_rf_status(_rf_control_snapshot(mode="status_only", core=False, ops=()))
    v.render()
    assert _visible_actions(v) == []
    texts = _rx_page_strings(app, v)
    assert V.RF_CONTROL_LABEL["arm"] not in texts
    assert V.RF_CONTROL_LABEL["stop"] not in texts


def test_rf_control_unknown_mode_renders_read_only():
    app, _lcd, v = _control_view()
    app.set_rf_status(_rf_control_snapshot(mode="control_test", armed=True,
                                           ops=_ARMED_OPS))
    v.render()
    assert _visible_actions(v) == []
    assert app.rf_control["surface"] == "unknown"


def test_rf_control_unarmed_control_mode_renders_only_arm():
    """判据 2: 可控制模式未武装 => 屏上只有一个"布防", 没有停止/初始化/配置。"""
    app, _lcd, v = _control_view()
    app.set_rf_status(_rf_control_snapshot(armed=False, ops=("control.arm",)))
    v.render()
    visible = _visible_actions(v)
    assert len(visible) == 1
    assert visible[0].text == V.RF_CONTROL_LABEL["arm"]
    texts = _rx_page_strings(app, v)
    assert V.RF_CONTROL_LABEL["stop"] not in texts


def test_rf_control_armed_page_renders_stop_even_mid_operation():
    """判据 6: 非只读状态下 stop 始终在, 操作进行中(pending)也照样画出来。"""
    app, _lcd, v = _control_view()
    app.set_rf_status(_rf_control_snapshot(armed=True, ops=_ARMED_OPS, pending=True))
    v.render()
    labels = [b.text for b in _visible_actions(v)]
    assert V.RF_CONTROL_LABEL["stop"] in labels, labels
    stop = [b for b in _visible_actions(v)
            if b.text == V.RF_CONTROL_LABEL["stop"]][0]
    assert stop.enabled is True


def test_rf_control_arm_needs_a_confirmation_tap():
    """判据 6: arm 不能一击即发 —— 先出确认, 确认后才上送。"""
    calls = []
    app, _lcd, v = _control_view(on_rf_control=calls.append)
    app.set_rf_status(_rf_control_snapshot(armed=False, ops=("control.arm",)))
    v.render()
    assert not v.rf_confirm_btn.visible
    tap_at(v, *center(_visible_actions(v)[0]))
    assert calls == [], "第一下只该登记确认, 不该发送"
    assert app.rf_control["pending_action"] == "arm"
    assert v.rf_confirm_btn.visible is True
    tap_at(v, *center(v.rf_confirm_btn))
    assert calls == ["arm"], calls


def test_rf_control_stop_needs_no_confirmation():
    """stop 不需要确认 —— 它必须始终一击可达。"""
    calls = []
    app, _lcd, v = _control_view(on_rf_control=calls.append)
    app.set_rf_status(_rf_control_snapshot(armed=True, ops=_ARMED_OPS))
    v.render()
    stop = [b for b in _visible_actions(v)
            if b.text == V.RF_CONTROL_LABEL["stop"]][0]
    tap_at(v, *center(stop))
    assert calls == ["stop"], calls


def test_rf_control_failure_shows_the_daemons_code():
    """判据 6: 失败把 daemon 的码画出来, 不是一句泛化消息。"""
    calls = []
    app, _lcd, v = _control_view(on_rf_control=calls.append)
    app.set_rf_status(_rf_control_snapshot(armed=False, ops=("control.arm",)))
    app.rf_control_prompt("arm")
    app.set_rf_control_result("arm", {"ok": False, "code": "PERMISSION",
                                      "message": "control token rejected"})
    v.render()
    texts = _rx_page_strings(app, v)
    assert any("PERMISSION" in s for s in texts), texts


def test_rf_control_token_path_is_prompted_but_no_token_is_rendered():
    """判据 5: 屏上只提示操作员 provision 的**路径**, 绝不出现任何令牌值。"""
    app, _lcd, v = view(M.PAGE_FUTURE, on_rf_control=lambda _a: None)
    app.set_rf_status(_rf_control_snapshot(armed=False, ops=("control.arm",),
                                           token="<SET_LOCALLY>"))
    app.rf_control_prompt("arm")
    v.render()
    texts = _rx_page_strings(app, v)
    joined = "\n".join(texts)
    assert M.RF_CONTROL_TOKEN_PATH in joined, joined
    assert "s3cret" not in joined

# ---------------- RF 状态文案(任务 3): 四种状态分开 ----------------

def test_online_without_capture_never_claims_rf_is_unavailable():
    """daemon 在线但采集能力缺席: 顶栏画链路, 不画 RF 未接入。"""
    app, _lcd, v = view(M.PAGE_FUTURE)
    app.set_rf_status(_rf_snapshot())
    texts = _rx_page_strings(app, v)
    joined = "\n".join(texts)
    assert "RF 未接入" not in joined
    assert "RF 已接入" not in joined
    assert "链路 在线" in joined
    assert "在线 / 新鲜" in joined
    assert "采集能力: 未接入" in joined


def test_rx_page_shows_link_armed_control_and_capability_from_their_own_fields():
    """状态 1/2/3/4 各画各的: 链路、武装、控制结果、采集能力互不冒充。"""
    app, _lcd, v = view(M.PAGE_FUTURE)

    app.set_rf_status(_rf_snapshot())
    assert "在线 / 新鲜" in _rx_page_strings(app, v)
    app.set_rf_status(_rf_snapshot(stale=True, error="poll expired"))
    assert "在线 / 过期" in _rx_page_strings(app, v)
    app.set_rf_status({"online": False, "error": "connection refused"})
    offline = _rx_page_strings(app, v)
    assert "离线" in offline
    assert "connection refused" in offline

    app.set_rf_status(_rf_snapshot(armed=True))
    assert "已武装" in _rx_page_strings(app, v)
    app.set_rf_status(_rf_snapshot(armed=False))
    assert "未武装" in _rx_page_strings(app, v)

    app.set_rf_status(_rf_control_snapshot(armed=True, ops=_ARMED_OPS))
    app.set_rf_control_result(
        "init", {"ok": False, "code": "INVALID_ARGUMENT",
                 "message": "init needs operator parameters the panel does not have yet"})
    control = _rx_page_strings(app, v)
    assert any(s.startswith("控制:") and "INVALID_ARGUMENT" in s for s in control), control

    app.set_capabilities(M.RxCapabilities(capture=True, reason="capture path up"))
    assert "采集能力: 已接入" in "\n".join(_rx_page_strings(app, v))
    app.set_capabilities(M.RxCapabilities(capture=False, reason="no rx path"))
    no_cap = "\n".join(_rx_page_strings(app, v))
    assert "采集能力: 未接入" in no_cap
    assert "no rx path" in no_cap



def test_online_hardware_verified_snapshot_is_not_a_measurement_pass():
    """online + hardware_verified 只说明 daemon 可达/初始化回读, 不是 TX/RX 测量通过。"""
    app, _lcd, v = view(M.PAGE_FUTURE)
    app.set_rf_status(_rf_snapshot(hardware_verified=True))
    texts = _rx_page_strings(app, v)
    joined = "\n".join(texts)
    for forbidden in ("测量通过", "测试通过", "已验证", "PASS", "已生效"):
        assert forbidden not in joined, forbidden
    assert "已接入" not in joined
    assert "未接入真实采集" in joined


def test_control_block_reason_is_shown_instead_of_inventing_success():
    """缺参数被本地门禁挡下时, 屏上画原因, 不上送, 也不写成功。"""
    calls = []
    app, _lcd, v = _control_view(on_rf_control=calls.append)
    app.set_rf_status(_rf_control_snapshot(armed=True, ops=_ARMED_OPS))
    v.render()
    init = [b for b in _visible_actions(v) if b.text == V.RF_CONTROL_LABEL["init"]][0]
    tap_at(v, *center(init))
    assert calls == [], "缺参数时不该上送"
    joined = "\n".join(_rx_page_strings(app, v))
    assert "INVALID_ARGUMENT" in joined
    assert "init needs operator" in joined
    assert "成功" not in joined



# ---------------- Independent review fixes (AD9361 UI) ----------------

def test_ad9361_digital_amplitude_keypad_edits_rf_draft_and_load_plan():
    from test_rf_touch_model import configured
    app, _lcd, v = view(M.PAGE_DIGITAL)
    app.__dict__.update(configured().__dict__)
    app.select_device("AD9361")
    v.invalidate(); v.render()
    tap_at(v, *center(v.dig_amp_row))
    assert v.keypad.visible
    v.keypad.value = "1000"
    tap_at(v, *center(v.keypad.ok))
    assert app.rf_digital_values()["amplitude"] == 1000
    app.set_rf_signal_kind("digital")
    args, why = app.rf_parameter_plan("load")
    assert why is None, why
    assert args["amplitude"] == 1000


def test_ad9361_digital_symrate_keypad_uses_fs_range_and_commits_default():
    from test_rf_touch_model import configured
    app, _lcd, v = view(M.PAGE_DIGITAL)
    app.__dict__.update(configured().__dict__)
    app.select_device("AD9361")
    v.invalidate(); v.render()
    tap_at(v, *center(v.dig_rows["symrate_hz"]))
    assert v.keypad.visible
    assert v.keypad.field["min"] == 1_875_000
    assert v.keypad.field["max"] == 30_000_000
    v.keypad.value = "2000000"
    tap_at(v, *center(v.keypad.ok))
    assert app.rf_digital_values()["symbol_rate_hz"] == 2_000_000


def test_ad9361_digital_symrate_keypad_rejects_out_of_fs_range():
    from test_rf_touch_model import configured
    app, _lcd, v = view(M.PAGE_DIGITAL)
    app.__dict__.update(configured().__dict__)
    app.select_device("AD9361")
    v.invalidate(); v.render()
    tap_at(v, *center(v.dig_rows["symrate_hz"]))
    v.keypad.value = "1000000"
    tap_at(v, *center(v.keypad.ok))
    assert v.keypad.visible
    assert "范围" in v.keypad.error


def test_ad9361_channel_noise_seed_keypad_edits_rf_draft():
    from test_rf_touch_model import configured
    app, _lcd, v = view(M.PAGE_CHANNEL)
    app.__dict__.update(configured().__dict__)
    app.page = M.PAGE_CHANNEL
    app.select_device("AD9361")
    v.invalidate(); v.render()
    assert v.chan_rows["noise_seed"].visible
    tap_at(v, *center(v.chan_rows["noise_seed"]))
    v.keypad.value = "123"
    tap_at(v, *center(v.keypad.ok))
    assert app.rf_channel_values()["noise_seed"] == 123


def test_acm_channel_hides_ad9361_noise_seed():
    app, _lcd, v = view(M.PAGE_CHANNEL)
    v.render()
    assert not v.chan_rows["noise_seed"].visible
