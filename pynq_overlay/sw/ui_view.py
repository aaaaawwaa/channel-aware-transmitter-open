#!/usr/bin/env python3
"""面板界面: 顶栏 + 左导航 + 工作区 + 底栏, 六个页面。

版式照 `docs/reference/index.html`(800×480 横屏):

    ┌──────────────────────────────────────────────┐ y=0
    │ 顶栏 48px   项目名 · 后端 · 数字调制实际状态   │
    ├────────┬─────────────────────────────────────┤ y=48
    │ 导航    │ 工作区 368px                        │
    │ 91px   │  标题 26 + 两栏卡片 302              │
    │        │                                     │
    ├────────┴─────────────────────────────────────┤ y=416
    │ 底栏 64px  待应用提示 · 应用设置 · 启动/停止    │
    └──────────────────────────────────────────────┘ y=480

**这一层只画和收事件, 不做判断**: 校验/命令计划/回读核对全在 `ui_model`(纯逻辑, 离线可测)。
它持有 `UiApp` 的**引用**并直接读写(同一线程), 照 `AGENT_GUIDE.md` 的线程约定。

三条从既有代码继承的纪律, 改这一层时必须继续遵守:

1. **值会变的显示行不能用 `Label`** —— `Label` 的 box 在构造期固化, 改 `.text` 不重算。
   用 `ValueRow`/`Table`/直接 `canvas.text()`。
2. **装饰件必须 `tappable=False`** —— 否则整屏大小的卡片会吃掉所有点击(准星那个坑)。
3. **弹窗必须靠 `visible` 显隐, 遮罩不可点** —— 打开时底层按钮点不动。

`render()` 里用一个**签名**判断"这一帧内容有没有变"; 没变就直接返回, 不重画也不推帧。
签名覆盖所有可见量, 包括预览折线的点序列 —— 预览只依赖参数(不依赖时间), 所以参数不变时
签名不变, 不会击穿这个优化。
"""
import awg_ctl as A
import ui_model as M
import ui_units as U
from pynq_lcd import rgb565
from pynq_lcd_ui import (BG, BTN, BTN_ON_BG, Canvas, CYAN, HEIGHT, LINE, MIN_CJK_SIZE,
                         MUTED, ORANGE, PANEL, STOP_BG, STOP_LINE, TEXT, WIDTH, Button,
                         Label, Panel, Plot, Table, ValueRow, Widget, text_size)

# ---- 版式常量(与参考设计的 CSS 逐值对应) ----
HDR_H = 48
NAV_W = 91
WORK_H = 368
FOOT_H = 64
FOOT_Y = HDR_H + WORK_H                    # 416
MAIN_X = NAV_W                             # 91
PAD = 14
TITLE_H = 26
COLS_H = 302

# RF 控制面的动作标签(Task 5/6)。默认只读页面一个都不画; 只有可控制模式
# 才出现, 所以既有的 glyph/版式判据看不到它们。
RF_CONTROL_LABEL = {
    "arm": "布防", "init": "初始化", "configure": "配置",
    "stop": "停止", "disarm": "解除", "load": "加载", "start": "发射",
}
RF_CONTROL_SLOTS = 3      # armed/initialized/configured/stopped 最多同时 3 个动作

# 工作区矩形 (x0, y0, x1, y1) —— 顶栏/左导航/底栏之外的那块。
# 面板把守护进程的 `StatusScreen` 嵌进状态页时要传这个: 状态屏默认按**整屏**绝对坐标画,
# 直接叠上来会被顶栏、左导航、底栏压住(板上看到的就是"栏与内容重叠")。
WORK_BOX = (NAV_W, HDR_H, WIDTH - 1, FOOT_Y - 1)

# 导航项: (页面 id, 图标, 名称)。
#
# ⚠ **图标只能用确认有字形的字符**。参考设计用的 `∿ ⋈ ≋ ⌁ ⚙ ◉`(U+223F/U+22C8/U+224B/
#   U+2301/U+2699/U+25C9)在开发机和板子上**都没有字形** —— 画出来是一排豆腐块。这个坑本工程
#   踩过两次(按钮上的 U+2212 减号、"α"), 所以这里退回 ASCII 与最基本的几何符号, 并且有
#   `test_nav_icons_have_glyphs` 钉住(它按"与私用区码位渲出的豆腐块是否逐位相同"判断,
#   光看掩膜非空是测不出来的 —— 豆腐块本身非空)。
#
# 状态页是本工程多出来的(守护进程的网络/写卡/关机), 参考设计没有它 —— 放在最前面,
# 因为它是"停机时唯一能看的东西"。数据页同样是自己加的(本地文件队列 + DDR 环形播放)。
#
# ⚠ **节距 51 与高度 46 是 7 项时唯一装得下的组合**: `y = 54 + i*51`, 末项 i=6 止于
#   54+306+46 = 406, 底栏在 416 —— 留 10px 余量。6 项时用的节距 60/高 54 装到第 7 项会
#   到 54+360+54 = 468, 直接压进底栏。`test_nav_buttons_do_not_overlap_the_footer`
#   与 `test_nav_buttons_are_all_inside_the_screen` 钉住这件事。
# 七个一级页面与 reference v2 对齐。旧 digital/channel 页面从 tools 进入，不占导航槽位。
NAV_ITEMS = (
    (M.PAGE_OVERVIEW, "@", "总览"),
    (M.PAGE_TX, "~", "发射"),
    (M.PAGE_RX, "+", "接收"),
    (M.PAGE_DATA, "=", "数据"),
    (M.PAGE_AI, "?", "智能"),
    (M.PAGE_TOOLS, "#", "工具"),
    (M.PAGE_SYSTEM, "*", "系统"),
)
NAV_Y0 = 54
NAV_PITCH = 51
NAV_BTN_H = 46

WAVE_CN = ("正弦", "方波", "三角", "锯齿", "脉冲", "噪声")

# 中文字号下限 `MIN_CJK_SIZE` **从 `pynq_lcd_ui` 引进来**(那边贴着颜色常量定义), 免得
# 两处各写一个、日后只改一处。板上实测的根据见那边的注释与 `font_probe.py`。

# 每个页面"应用设置"对应的动作。不在表里的页面(状态/扩展/系统)没有可下发的参数,
# 底栏的"应用设置"在那里就该是灰的 —— 点一个什么都不做的按钮比按钮是灰的更糟。
APPLY_FOR_PAGE = {
    M.PAGE_AWG: M.APPLY_AWG,
    M.PAGE_DIGITAL: M.APPLY_DIGITAL,
    M.PAGE_CHANNEL: M.APPLY_CHANNEL,
}


def strings_drawn(view, state=None):
    """把**这一整套界面会画到屏上**的文字都收出来 `[(字符串, 字号, 哪一页)]`。

    做法是挂在 `Canvas.text` 的出口上逐页真渲一遍 —— 手写字面量清单必漏(实测漏过 `·`
    与 `—`, 两者在开发机的字体里有字形、板子上没有)。`glyph_check.py` 与离线测试都用它。
    """
    from pynq_lcd_ui import drawn_text, set_text_log
    out = []
    page_before = view.app.page
    for page in M.PAGES:
        view.app.page = page
        view.invalidate()
        sink = []
        set_text_log(sink)
        try:
            view.render(state)
        finally:
            set_text_log(None)
        out.extend((s, size, page) for s, size, _x, _y in sink)
    # 弹窗: 键盘上的字只在打开时才画
    view.app.page = M.PAGE_AWG
    view.invalidate()
    view.keypad.open_for("频率", "100000", "整数范围 10 ~ 1000000 Hz",
                         {"min": M.FREQ_MIN, "max": M.FREQ_MAX, "set": lambda v: None})
    sink = []
    set_text_log(sink)
    try:
        view.invalidate()
        view.render(state)
    finally:
        set_text_log(None)
    view.keypad.close()
    out.extend((s, size, "keypad") for s, size, _x, _y in sink)
    view.app.page = page_before
    view.invalidate()
    return out


def rx_spectrum_series(analysis, points=96):
    """One magnitude spectrum onto a Plot box: x = frequency, y = magnitude.

    The x axis is the TWO-SIDED analysis band, ascending, so the DC bin sits in the middle
    (the analyser returns the FFT order, so it is shifted here for display only).  The y
    axis is the magnitude normalised to its own largest bin.  This is a relative view of
    the ANALYSIS result: the panel never turns it into an SNR, Vpp or dBm claim.
    """
    spectrum = getattr(analysis, "spectrum", None) or ()
    if len(spectrum) < 3:
        return []
    half = len(spectrum) // 2
    ordered = list(spectrum[half:]) + list(spectrum[:half])
    peak = max(ordered)
    if peak <= 0.0:
        return []
    # Bucket MAX, not every n-th bin: a single-tone spectrum is one bin wide, so plain
    # decimation would drop the very peak the page exists to show.
    buckets = max(2, int(points))
    size = max(1, len(ordered) // buckets)
    span = float(len(ordered) - 1) or 1.0
    pts = []
    for start in range(0, len(ordered), size):
        chunk = ordered[start:start + size]
        index = start + chunk.index(max(chunk))
        pts.append(((index / span) * 2.0 - 1.0, max(chunk) / peak))
    return [(pts, CYAN)]


def rx_iq_series(analysis, points=64):
    """The bounded I/Q preview as two series (I and Q) on the same Plot box."""
    preview = getattr(analysis, "preview", None) or ()
    if len(preview) < 4:
        return []
    pairs = [(preview[index], preview[index + 1])
             for index in range(0, len(preview) - 1, 2)][:points]
    span = float(max(1, len(pairs) - 1))
    ivec, qvec = [], []
    for index, (i_value, q_value) in enumerate(pairs):
        x = (index / span) * 2.0 - 1.0
        ivec.append((x, i_value / 2048.0))
        qvec.append((x, q_value / 2048.0))
    return [(ivec, CYAN), (qvec, ORANGE)]


def _rf_control_line(control):
    # RF 控制面的一行文字与颜色(任务 3 的状态 3: 控制结果 / 门禁原因)。
    # 优先级: 在途 > 最近一次失败或本地门禁原因 > 等确认 > 当前控制状态 + 可用性原因。
    # 成功只由 UiApp.set_rf_control_result(ok=True) 推进控制状态; 这里只转写状态,
    # 没有 daemon 回复时画的就是阻塞原因, 从不发明"成功 / 已生效"。
    control = control if isinstance(control, dict) else {}
    inflight = control.get("inflight_action")
    if inflight:
        return "控制: 等待 %s" % inflight, CYAN
    last_error = control.get("last_error")
    if last_error:
        return "控制: %s" % last_error, ORANGE
    pending = control.get("pending_action")
    if pending:
        return ("控制: 待确认 %s %s"
                % (pending, control.get("token_path_hint") or ""), CYAN)
    state = control.get("state") or "read_only"
    reason = control.get("reason") or "控制面未接入"
    return "控制: %s (%s)" % (state, reason), MUTED


def _hz(v):
    """频率写成人看的样子(触摸屏上宽度有限)。"""
    if v >= 1000000:
        return "%.2fM" % (v / 1e6)
    if v >= 1000:
        return "%.3gk" % (v / 1e3)
    return "%d" % v


class NavButton(Button):
    """导航栏的一项: 图标在上、名称在下, 选中时高亮。"""

    def __init__(self, x, y, w, h, icon, name, on_tap=None, selected=False):
        super().__init__(x, y, w, h, "", on_tap=on_tap, size=12,
                         fill=BTN_ON_BG if selected else PANEL,
                         edge=CYAN if selected else PANEL,
                         fg=CYAN if selected else MUTED, radius=8)
        self.icon = icon
        self.name = name
        self.selected = selected

    def draw(self, canvas):
        x0, y0, x1, y1 = self.box
        fill = BTN_ON_BG if (self.selected or self.pressed) else PANEL
        edge = CYAN if self.selected else PANEL
        fg = CYAN if self.selected else MUTED
        canvas.rounded_rect(x0, y0, x1, y1, fill, self.radius)
        canvas.rounded_rect(x0, y0, x1, y1, edge, self.radius, fill=False)
        canvas.text_center(x0, y0 + 2, x1, y0 + 26, self.icon, fg, 19)
        canvas.text_center(x0, y1 - 21, x1, y1 - 5, self.name, fg, MIN_CJK_SIZE)


class Header(Widget):
    """顶栏: 项目名 + 后端类型 + **数字调制的实际状态**。

    运行状态取自 `app.actual_mode`(设备回读), 不是草稿 —— 参考设计特意强调这点。
    """

    def __init__(self, app):
        super().__init__(0, 0, WIDTH, HDR_H)
        self.app = app
        self.tappable = False

    def draw(self, canvas):
        canvas.rect(0, 0, WIDTH - 1, HDR_H - 1, BG)
        canvas.rect(0, HDR_H - 1, WIDTH - 1, HDR_H - 1, LINE)
        canvas.text(16, 14, "智链通测", TEXT, 17)
        canvas.text(16 + text_size("智链通测", 17)[0] + 10, 19, "AC820 / PYNQ", MUTED, 11)
        # 顶栏只画 daemon 链路(状态 1: 在线/过期/离线), 不再拿 RX 采集能力冒充
        # 顶栏的 RF 接入状态。真机上 RF 控制可用而采集能力缺席时, 旧文案会被读成
        # RF 整个不可用(任务 3)。
        rf = self.app.rf_status if isinstance(getattr(self.app, "rf_status", None), dict) else {}
        if rf.get("online") is not True:
            link, link_color = "链路 离线", MUTED
        elif rf.get("stale") is True:
            link, link_color = "链路 过期", ORANGE
        else:
            link, link_color = "链路 在线", CYAN
        link_w = text_size(link, MIN_CJK_SIZE)[0]
        canvas.text(WIDTH - 16 - link_w, 16, link, link_color, MIN_CJK_SIZE)


class Footer(Widget):
    """底栏: 左侧"待应用/已同步"提示, 右侧"应用设置"与"启动/关闭数字调制"。"""

    def __init__(self, app, on_apply, on_toggle):
        super().__init__(0, FOOT_Y, WIDTH, FOOT_H)
        self.app = app
        self.tappable = False
        # ⚠ 两个按钮的 x **必须按各自的真实宽度算**: 早先"应用设置"的 x 是按"右侧那个按钮
        #   只有 105 宽"推的, 而它其实是 130 —— 于是两个按钮**重叠了 18px**, 右边那个
        #   (后画、且命中优先级更高)把"应用设置"的右边缘盖掉了。`test_no_two_controls_overlap`
        #   抓到。(这是同一个坑的第三次: 通道选择撞波形网格、键盘压住确认按钮。)
        btn_w = 130
        self.toggle_btn = Button(WIDTH - 16 - btn_w, FOOT_Y + 10, btn_w, 44,
                                 "启动数字调制", on_tap=lambda b: on_toggle(), size=16)
        self.apply_btn = Button(WIDTH - 16 - btn_w - 8 - btn_w, FOOT_Y + 10, btn_w, 44,
                                "应用设置", on_tap=lambda b: on_apply(), size=16)

    def draw(self, canvas):
        """只画底栏底色、分隔线、"待应用/已同步"提示与上次结果。

        ⚠ 那两个按钮**不在这里画** —— 它们是注册在 `Ui` 顶层的独立控件, 由帧循环统一画
        (照键盘那套)。在这里再画一遍会画两次, 而且第二次不带按下态, 按下去看不出反馈。
        """
        canvas.rect(0, FOOT_Y, WIDTH - 1, HEIGHT - 1, BG)
        canvas.rect(0, FOOT_Y, WIDTH - 1, FOOT_Y, LINE)
        app = self.app
        if not app.connected:
            text, color = "设备未连接", MUTED
        elif not app.fresh:
            text, color = "状态已过期, 请重新读取", ORANGE
        elif app.pending:
            text, color = "正在应用...", CYAN
        elif app.dirty:
            text, color = "有待应用参数", ORANGE
        else:
            text, color = "已与设备同步", CYAN
        canvas.circle(22, FOOT_Y + 32, 4, color)
        canvas.text(32, FOOT_Y + 22, text, color, MIN_CJK_SIZE)
        # 上次结果(错误/不符要看得见)
        if app.last_message and app.last_result != M.OK:
            canvas.text(32, FOOT_Y + 40, app.last_message, ORANGE, MIN_CJK_SIZE)
        # 按钮的可用态/文字在这里定(帧循环随后才画它们, 所以这里改完立刻生效)
        self.apply_btn.enabled = (app.selected_device == "ACM2108" and app.connected and app.fresh and not app.pending
                                  and app.page in APPLY_FOR_PAGE)
        self.toggle_btn.visible = app.running or (app.selected_device == "ACM2108" and not ((app.page == M.PAGE_RX and app.rx_subpage == "transmit") or (app.page == M.PAGE_TX and app.tx_subpage in ("parameters", "transmit"))))
        self.toggle_btn.enabled = app.connected and not app.pending
        self.toggle_btn.text = "关闭数字调制" if app.running else "启动数字调制"
        self.toggle_btn.fill = STOP_BG if app.running else ORANGE
        self.toggle_btn.edge = STOP_LINE if app.running else ORANGE
        self.toggle_btn.fg = TEXT if app.running else BG


class _ModalMask(Widget):
    """弹窗的**命中遮罩**: 盖住工作区与底栏, 不画像素、可点但什么都不做。

    **为什么不能只靠"遮罩不可点"**: `_hit` 找的是第一个 `contains` 为真的控件 —— 遮罩不参与
    命中, 落在弹窗外(键盘旁边、底栏上)的手指就会**穿透到底层控件**, 于是改数值时随手一点
    可能把"应用设置"甚至"启动数字调制"按下去。让遮罩把这些点吃掉才算真模态。

    **导航栏那一列故意留在遮罩外**: 留着它, 用户在被挡住时还能切页逃出去(切页会顺带收掉
    键盘, 见 `_nav_tap`)。弹窗中心在 x=218, 不会与导航栏(0..90)重叠。

    它只在弹窗开着时 `visible`(= 参与命中), 所以平时不影响任何点击 —— 这与"整屏准星吃掉
    所有点击"是同一个机制、相反方向的应用: 那个是**无条件**盖着, 这个**有条件**。
    """

    def __init__(self):
        super().__init__(NAV_W, 0, WIDTH - NAV_W, HEIGHT)
        self.tappable = True
        self.visible = False

    def draw(self, canvas):
        pass                    # 遮罩的像素由 `Keypad.draw` 画, 这里只需占住命中


class Keypad(Widget):
    """触摸数字键盘弹窗: 遮罩 + 输入框 + 3×4 键盘 + 确认/取消。

    **打开时底层点不动** —— 靠三件事, 缺一不可:
      1. 遮罩是整屏的, 由 `_ModalMask` **吃掉**弹窗外的所有点击(它不可见时不参与命中);
      2. 键盘/确认/取消注册在 `Ui` 顶层、且排在**最后**(`_hit` 从顶往下找第一个命中),
         所以落在弹窗内的点会先被它们接走;
      3. 容器自身 `tappable=False` —— 否则 `_hit` 会先命中容器, 里面的按钮就全都点不到。

    ⚠ 早先这里只有"遮罩不可点", 于是弹窗外的点会**穿透到底层按钮**上; `test_keypad_opens_
      and_blocks_the_buttons_underneath` 当初之所以通过, 是因为底栏那个按钮压根没注册到
      `Ui`(永远点不到) —— 两条 bug 互相掩盖。修掉漏注册之后这条测试才真的开始起作用。
    """

    # ⚠ `⌫`(U+232B)在开发机与板子上**都没有字形**(画出来是豆腐块), 所以退格键写 "DEL"。
    #   键盘上的 `±` 有字形, 保留。
    KEYS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "+/-", "0", "DEL")
    # 尺寸按内容算出来, 不要手填: 3 行键盘 + 标题/输入框/范围提示 + 底部确认行。
    # 手填过一次 268, 结果键盘压到了确认按钮上(第 4 行键 y=368..409 而弹窗只到 373)。
    KEY_W, KEY_H = 108, 42
    GAP = 6
    PAD = 14
    # 标题(12..28) + 输入框(40..88) + 范围提示行(94..106) —— 键盘从 HEAD_H 开始。
    # 手填过 96, 结果提示行被第一排键压住(渲成 PNG 才看出来)。
    HEAD_H = 154
    FOOT_H = 56          # 确认/取消那一行
    W = PAD * 2 + KEY_W * 3 + GAP * 2
    H = HEAD_H + KEY_H * 4 + GAP * 3 + FOOT_H + PAD

    def __init__(self, on_commit=None):
        x = (WIDTH - self.W) // 2
        y = (HEIGHT - self.H) // 2
        super().__init__(x, y, self.W, self.H)
        self.on_commit = on_commit
        self.title = ""
        self.value = ""
        self.range_text = ""
        self.error = ""
        self.field = None              # 当前在编辑的字段描述(调用方给的)
        self.unit = ""
        self.units = ()
        self.visible = False
        # ⚠ 弹窗**自己不可点**: 它是个容器, 真正接收点击的是里面的按钮。若这里为 True,
        #   `_hit` 会先命中容器本身(它覆盖整块弹窗), 里面的键盘/确认/取消就全都点不到了 ——
        #   与"整屏准星吃掉所有点击"是同一个坑。
        self.tappable = False
        # 整屏的**命中遮罩**: 弹窗开着时吃掉所有落在外面的点击(见 `_ModalMask`)。
        self.mask = _ModalMask()
        self.buttons = []
        for i, key in enumerate(self.KEYS):
            r, c = divmod(i, 3)
            bx = x + self.PAD + c * (self.KEY_W + self.GAP)
            by = y + self.HEAD_H + r * (self.KEY_H + self.GAP)
            self.buttons.append(Button(bx, by, self.KEY_W, self.KEY_H, key,
                                       on_tap=self._key_tap(key), size=20,
                                       fill=BTN, edge=LINE))
        foot_y = y + self.H - self.PAD - 44
        self.ok = Button(x + self.W - self.PAD - 150, foot_y, 150, 44,
                         "确认参数", on_tap=lambda b: self._commit(), size=16,
                         fill=ORANGE, edge=ORANGE, fg=BG)
        self.cancel = Button(x + self.PAD, foot_y, 110, 44, "取消",
                             on_tap=lambda b: self.close(), size=16,
                             fill=BTN, edge=LINE, fg=MUTED)
        self.decimal = Button(x + self.PAD, y + 116, 38, 32, ".",
                              on_tap=self._key_tap("."), size=20)
        self.unit_buttons = [Button(x + self.PAD + 44 + i * 72, y + 116,
                                    68, 32, "", size=12,
                                    on_tap=lambda _b, slot=i: self.select_unit(self.units[slot]))
                             for i in range(4)]

    def all_buttons(self):
        """弹窗里所有可点控件 —— 它们要注册到 `Ui` 顶层才能被命中(见 UiView._build)。"""
        return list(self.buttons) + [self.decimal] + self.unit_buttons + [self.ok, self.cancel]

    def open_for(self, title, value, range_text, field):
        self.title = title
        self.value = str(value)
        self.range_text = range_text
        self.field = field
        self.unit = field.get("unit", "")
        self.units = U.FREQUENCY_UNITS if self.unit == "Hz" else (U.SYMBOL_UNITS if self.unit == "Sym/s" else ())
        self.error = ""
        self.visible = True
        self._sync_children()

    def close(self):
        self.visible = False
        self.field = None
        self._sync_children()

    def _sync_children(self):
        """子控件的可见性跟着弹窗走。

        **必须显式同步**: 子控件是注册在 `Ui` 顶层的独立控件, `Ui` 只看它们自己的
        `visible` —— 弹窗关了但子控件还 visible 的话, 它们会在屏上"幽灵般"可点。遮罩尤其
        要同步: 它铺满全屏, 忘了关就是"整屏点不动"。
        """
        self.mask.visible = self.visible
        for b in self.all_buttons():
            b.visible = self.visible
        self.decimal.visible = self.visible and bool(self.units)
        for i, b in enumerate(self.unit_buttons):
            b.visible = self.visible and i < len(self.units)
            b.text = self.units[i] if i < len(self.units) else ""
            b.fill = BTN_ON_BG if b.text == self.unit else BTN

    def select_unit(self, unit):
        if self.field is None or unit not in self.units:
            return
        try:
            if self.value:
                value = U.parse_frequency(self.value, self.unit, maximum=self.field["max"])
                self.value = U.format_frequency(value, unit)
        except ValueError as exc:
            self.error = str(exc)
            return
        self.unit = unit
        self.error = ""
        self._sync_children()

    def _key_tap(self, key):
        def tap(_b):
            if key == "DEL":
                self.value = self.value[:-1]
            elif key == "+/-":
                self.value = self.value[1:] if self.value.startswith("-") else "-" + self.value
            else:
                self.value += key
            self.error = ""
        return tap

    def _commit(self):
        if self.field is None:
            return
        try:
            v = U.parse_frequency(self.value, self.unit, self.field["min"], self.field["max"]) if self.units else int(self.value)
        except ValueError as exc:
            self.error = str(exc) if self.units else "请输入整数"
            return
        lo, hi = self.field["min"], self.field["max"]
        if not (lo <= v <= hi):
            self.error = "范围 %d ~ %d" % (lo, hi)
            return
        setter = self.field["set"]
        setter(v)
        if self.on_commit is not None:
            self.on_commit()
        self.close()

    def draw(self, canvas):
        """只画遮罩与弹窗底板/文字。

        ⚠ 键盘/确认/取消**不在这里画** —— 它们是注册在 `Ui` 顶层的独立控件, 由帧循环统一
        画。在这里再画一遍会画两次(且第二次不带按下态)。
        """
        if not self.visible:
            return
        canvas.rect(0, 0, WIDTH - 1, HEIGHT - 1, BG)          # 遮罩
        x0, y0, x1, y1 = self.box
        canvas.rounded_rect(x0, y0, x1, y1, PANEL, 12)
        canvas.rounded_rect(x0, y0, x1, y1, LINE, 12, fill=False)
        canvas.text(x0 + self.PAD, y0 + 12, self.title, TEXT, 16)
        canvas.rounded_rect(x0 + self.PAD, y0 + 40, x1 - self.PAD, y0 + 88, BTN, 7)
        canvas.text(x0 + self.PAD + 12, y0 + 54, self.value or "未输入", TEXT, 24)
        canvas.text(x0 + self.PAD, y0 + 94, self.error or self.range_text,
                    ORANGE if self.error else MUTED, MIN_CJK_SIZE)

    def in_bounds(self, x, y):
        """这个点落在弹窗矩形里吗 —— **只给测试/调用方用, 不参与命中**。

        `_hit` 用的是 `contains`, 而弹窗容器**故意不可命中**(见 `__init__`), 所以那
        两个方法必须分开: 合在一起就会让容器把里面按钮的点击全吃掉。
        """
        return (self.visible and self.x <= x <= self.x + self.w - 1
                and self.y <= y <= self.y + self.h - 1)


class UiView(object):
    """六页界面。对外只有 `render()` / `invalidate()` / `.ui` 三个接口 ——
    与旧的 `StatusScreen`/`AwgPage` 一致, 所以 `_DualScreen` 那类装配代码能直接换成它。

    `status_screen` 是守护进程的**现有状态页**(网络/写卡/长按关机那套), 它不归本文件管 ——
    这里只在导航切到"状态"时把它渲染进来, 让面板功能一字不丢。
    """

    def __init__(self, lcd, app, ctl=None, status_screen=None, on_status=None,
                 on_apply=None, on_toggle=None, touch_cursor=None, log=None,
                 framebuffer=None, on_ddr=None, on_rx=None, on_rf_control=None):
        from pynq_lcd_ui import Ui
        self.lcd = lcd
        self.app = app
        self.ctl = ctl
        self.status_screen = status_screen
        self.on_status = on_status
        self.cursor = touch_cursor
        self.log = log or (lambda s: None)
        # ⚠ 与状态屏**共用同一个影子缓冲**(`framebuffer`)。各自建一个的话, 状态页上两边
        #   会各推一帧整屏到同一个真帧缓冲 —— 后推的那整块把先推的整块**盖掉**, 而且一帧
        #   两次整块拷贝的中间态会被面板扫到, 看着就是**整屏闪**(板上实测; 板端与离线都
        #   复现过: 状态页一帧 flush 两次, 状态屏画的内容立刻被面板的影子覆盖)。
        #   共用之后两边画的是同一张图, 由本类在帧末统一推一次。
        if framebuffer is None and status_screen is not None:
            framebuffer = getattr(getattr(status_screen, "ui", None), "lcd", None)
        # ⚠ 状态屏默认按**整屏**绝对坐标画(它也能独立显示, 见 `_StatusOnlyView`), 而面板的
        #   顶栏/左导航/底栏都压在那套坐标上 —— 不重新摆进工作区就会"栏与内容重叠"
        #   (板上看到的状态页)。重建控件后由本类统一在帧末推一次, 所以放在这里而不是
        #   构造状态屏时(那时还不知道有没有导航栏)。
        if status_screen is not None:
            relayout = getattr(status_screen, "relayout", None)
            if relayout is not None:
                relayout(WORK_BOX)
        self.ui = Ui(lcd, background=BG, shadow=True, framebuffer=framebuffer,
                     source=touch_cursor.source() if touch_cursor else None)
        self._last_sig = None
        self._enabled = False
        self._input_ready = False
        self._input_point = None
        # 本帧画过的**静态文字**的矩形, 见 `_t()`
        self._static_labels = []
        self._on_apply = on_apply
        self._on_toggle = on_toggle
        # 数据页的动作出口(`on_ddr(cmd, arg)`)。**本层不做 DDR 判断** —— 判据在
        # `ui_model.ddr_*`, 执行在 `RingThread`, 这里只转发一次点击。
        self._on_ddr = on_ddr
        # RX 事件也只转发给唯一控制服务；本层不直接访问 AD9361 或寄存器。
        self._on_rx = on_rx
        # RF 控制动作只转发给唯一服务(与 `_on_rx` 同一原则): 本层不碰 socket。
        self._on_rf_control = on_rf_control
        self.fields = {}          # 字段名 -> 编辑描述(给键盘弹窗用)
        self._titles = {
            M.PAGE_OVERVIEW: ("总览", "TX / RX / TRX STATUS"),
            M.PAGE_TX: ("发射", "DUAL CHANNEL / AWG"),
            M.PAGE_DIGITAL: ("工具 / 数字调制", "BASEBAND / I + Q"),
            M.PAGE_CHANNEL: ("工具 / 信道损伤", "STATIC MULTIPATH + APPROX. NOISE"),
            M.PAGE_RX: ("接收", "AD9361 / RX CAPABILITY"),
            M.PAGE_DATA: ("数据", "DDR / RX FRAMES"),
            M.PAGE_AI: ("智能", "OBSERVE / RECOMMEND ONLY"),
            M.PAGE_TOOLS: ("工具", "LEGACY AWG / BASEBAND / CHANNEL"),
            M.PAGE_SYSTEM: ("系统", "CAPABILITIES / OPERATION LOG"),
        }
        self._build()

    # ---- 构建 ----

    def _build(self):
        u = self.ui
        u.add(Header(self.app))
        self.nav = []
        for i, (page, icon, name) in enumerate(NAV_ITEMS):
            y = NAV_Y0 + i * NAV_PITCH
            self.nav.append(u.add(NavButton(
                7, y, NAV_W - 14, NAV_BTN_H, icon, name,
                on_tap=self._nav_tap(page), selected=(page == self.app.page))))
        self.footer = u.add(Footer(self.app, self._apply, self._toggle))
        # 底栏的两个按钮也要**注册成顶层控件** —— 与键盘同一个理由(见下面): `_hit` 只在
        # `ui.widgets` 里找、不递归进容器。早先只注册了 `Footer` 容器本身, 于是"应用设置"
        # 画得出来却**点不到**(板上/P7 e2e 抓到); 而"能点到的控件"那条测试是遍历
        # `ui.widgets` 的, 天生看不见这类漏注册, 所以另有一条从构造路径上查的测试。
        u.add(self.footer.apply_btn)
        u.add(self.footer.toggle_btn)
        self.page_widgets = []
        self.keypad = Keypad(on_commit=self._after_edit)
        self._build_pages()
        self.device_buttons = {}
        for i, device in enumerate(M.DEVICES):
            b = u.add(Button(498 + i * 144, 55, 136, 34, device,
                             on_tap=lambda _b, name=device: self._select_device(name), size=16))
            self.device_buttons[device] = b
        self.rf_stop_btn = u.add(Button(302, FOOT_Y + 10, 174, 44, "停止 AD9361",
                                       on_tap=lambda _b: self._rf_control_tap("stop"),
                                       size=15, fill=STOP_BG, edge=STOP_LINE))
        # 弹窗**最后加**(画在最上层), 且它内部的按钮也要注册成顶层控件 —— 否则 `_hit`
        # 找不到它们(`_hit` 只在 `ui.widgets` 里找, 不递归进容器)。
        # 顺序要紧: 先是整屏遮罩(弹窗外吃掉), 再是键盘/确认/取消(弹窗内接走)。
        u.add(self.keypad.mask)
        u.add(self.keypad)
        for b in self.keypad.all_buttons():
            u.add(b)
            b.visible = False

    def _nav_tap(self, page):
        def tap(_b):
            if page == self.app.page:
                return
            # 切页要把键盘收掉: 状态页只叠顶栏/导航/底栏(见 `_draw_chrome_only`), 弹出的
            # 键盘不会被画出来, 但它的按钮仍注册在 `ui.widgets` 里 —— 不收掉就会留下
            # 一块**看不见却点得到**的区域。
            self.keypad.close()
            # ⚠ 状态页是**另一个** `Ui`(守护进程那套), 它有自己的"上一帧签名": 不清掉的话
            #   切回去时若状态恰好没变, 它会直接早退 —— 面板上还留着刚才那页的画面。
            if self.status_screen is not None:
                self.status_screen.invalidate()
            self.app.page = page
            self.invalidate()
        return tap

    def _apply(self):
        """底栏"应用设置": 按当前页决定下发哪一组动作。"""
        if self.app.selected_device != "ACM2108":
            return
        act = APPLY_FOR_PAGE.get(self.app.page)
        if act is not None and self._on_apply is not None:
            self._on_apply(act)

    def _toggle(self):
        """底栏"启动/关闭数字调制": **以设备实际模式**为准(不是草稿里的模式)。"""
        if not self.app.running and (self.app.selected_device != "ACM2108" or (self.app.page == M.PAGE_RX and self.app.rx_subpage == "transmit") or (self.app.page == M.PAGE_TX and self.app.tx_subpage in ("parameters", "transmit"))):
            return
        if self._on_toggle is not None:
            self._on_toggle(M.STOP_DIGITAL if self.app.running else M.START_DIGITAL)

    def _select_device(self, device):
        why = self.app.select_device(device)
        if why is not None:
            self.app.last_message = why
            return
        self.keypad.close()
        self.invalidate()

    def _after_edit(self):
        """键盘确认后: 草稿变了, 界面要重画。"""
        self.invalidate()

    # ---- 页面(子类/后续阶段填充; 这里给出骨架与"空页"占位) ----

    def _build_pages(self):
        """把每个页面的控件建好并放进 `self.page_widgets[page]`。

        控件在构造时就全部建好、按 `visible` 显隐 —— 而不是切页时重建。理由: 重建会
        让掩膜缓存(字形/形状/折线)失效, 而切页本来就会强制重画一次。
        """
        self.page_widgets = {p: [] for p in tuple(M.PAGES) + tuple(M.LEGACY_PAGES)}
        self._build_overview_page()
        self._build_awg_page()
        self._build_future_page()
        self._build_ddr_page()
        self._build_ai_page()
        self._build_tools_page()
        self._build_digital_page()
        self._build_channel_page()
        self._build_system_page()
        for page, widgets in self.page_widgets.items():
            for w in widgets:
                self.ui.add(w)
                w.visible = False

    def _work_area(self, title, sub=""):
        """画工作区的标题行, 返回两栏卡片的可用区域 (x0, y0, w, h)。"""
        c = Canvas(self.ui.lcd)
        x0 = MAIN_X + PAD
        y0 = HDR_H + 12
        c.text(x0, y0 + 4, title, TEXT, 18)
        if sub and self.app.page not in (M.PAGE_TX, M.PAGE_TOOLS, M.PAGE_DIGITAL, M.PAGE_CHANNEL):
            w = text_size(sub, 11)[0]
            c.text(WIDTH - PAD - w, y0 + 8, sub, MUTED, 11)
        cy = y0 + TITLE_H + 10
        return x0, cy, WIDTH - PAD - x0, COLS_H

    def _cols(self, page=None):
        """两栏卡片的区域: (左栏 x,y,w,h), (右栏 x,y,w,h)。参考设计是 286 + 其余。

        `page` 必须**显式传**: 构建期各页按自己的 id 算坐标, 而 `app.page` 那时是别的页
        (通常是状态页)。读 `app.page` 会 KeyError。
        """
        page = page if page is not None else self.app.page
        x0, cy, w, h = self._work_area(*self._titles[page])
        left_w = 286
        gap = 12
        right_x = x0 + left_w + gap
        right_w = x0 + w - right_x
        return (x0, cy, left_w, h), (right_x, cy, right_w, h)

    # ---- 各页构建 ----

    def _edit_field(self, label, key, lo, hi, unit, getter, setter, page, size=20):
        """建一行"点开键盘编辑"的数值行, 并登记它的字段描述(给弹窗用)。"""
        row = ValueRow(0, 0, 10, 48, label, "", unit, size=size,
                       on_tap=lambda w: self._open_editor(label, key, lo, hi, unit,
                                                           getter, setter))
        self.page_widgets[page].append(row)
        self.fields[key] = {"min": lo, "max": hi, "unit": unit,
                            "get": getter, "set": setter, "row": row, "label": label}
        return row

    def _open_editor(self, label, key, lo, hi, unit, getter, setter):
        if key == "dig_symrate_hz" and self.app.selected_device == "AD9361":
            lo, hi = self.app.rf_digital_symrate_bounds()
        unit_txt = (" " + unit) if unit else ""
        self.keypad.open_for(label, getter(), "整数范围 %d ~ %d%s" % (lo, hi, unit_txt),
                             {"min": lo, "max": hi, "unit": unit, "set": setter})
        self.invalidate()

    def _chip(self, page, x, y, w, h, text, on_tap, size=16):
        """一个小按钮(波形/模式/滤波选择那种)。"""
        b = Button(x, y, w, h, text, on_tap=on_tap, size=size,
                   fill=BTN, edge=LINE, fg=TEXT, pressed_fill=BTN_ON_BG, radius=8)
        self.page_widgets[page].append(b)
        return b

    def _select_channel(self, ci):
        def tap(_b):
            self.app.selected_channel = ci
            self.invalidate()
        return tap

    def _build_overview_page(self):
        """总览没有可编辑控件；状态屏和本视图共同提供只读运行概览。"""
        return

    def _build_tools_page(self):
        """工具容器页: 旧的数字调制/信道损伤事务页从这里进入。"""
        page = M.PAGE_TOOLS
        (lx, ly, lw, lh), (_rx, _ry, _rw, _rh) = self._cols(page)
        self.tools_digital_btn = self._chip(
            page, lx + 14, ly + 48, lw - 28, 48, "数字调制工具",
            lambda _b: self._open_legacy_page(M.PAGE_DIGITAL), size=16)
        self.tools_channel_btn = self._chip(
            page, lx + 14, ly + 112, lw - 28, 48, "信道损伤工具",
            lambda _b: self._open_legacy_page(M.PAGE_CHANNEL), size=16)

    def _build_ai_page(self):
        """智能页只显示后端发布的真实建议，不生成或执行模型结果。"""
        page = M.PAGE_AI
        (lx, ly, lw, _lh), (_rx, _ry, _rw, _rh) = self._cols(page)
        self.ai_fill_btn = self._chip(
            page, lx + 14, ly + 214, lw - 28, 42, "建议填入接收草稿",
            lambda _b: self._rx_event("fill_recommendation"), size=15)

    def _open_legacy_page(self, page):
        self.keypad.close()
        self.app.page = page
        self.invalidate()

    def _rf_wave_mode_tap(self, _b):
        self.app.set_rf_waveform_mode(not self.app._rf_wave_advanced)
        self.invalidate()

    def _rf_tx_entry(self, _b):
        self.app.page = M.PAGE_RX
        self.app.rx_subpage = "transmit"
        self.invalidate()

    def _build_awg_page(self):
        """发射页: 波形/AWG 与 AD9361 RF 参数/RF 发射共用此页。"""
        page = M.PAGE_AWG
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(page)
        self.tx_tab_btns = [
            self._chip(page, rx + 14 + i * 122, ry + 5, 114, 30, name,
                       self._tx_tab_tap(key), size=14)
            for i, (key, name) in enumerate(
                (("awg", "波形"), ("parameters", "RF参数"), ("transmit", "RF发射")))
        ]
        # ---- AWG 波形 ----
        self.awg_ch_btns = [
            self._chip(page, lx + 98 + i * 90, ly + 10, 84, 32, "CH%d" % (i + 1),
                       self._select_channel(i), size=15)
            for i in range(2)]
        self.awg_wave_btns = []
        for i, name in enumerate(M.WAVES):
            r, c = divmod(i, 3)
            self.awg_wave_btns.append(self._chip(
                page, lx + 14 + c * 86, ly + 52 + r * 46, 80, 40, WAVE_CN[i],
                self._wave_tap(name), size=15))
        self.awg_rows = {}
        specs = (("freq_hz", "频率", A.FREQ_MIN, A.FREQ_MAX, "Hz"),
                 ("ampl_pct", "幅度", 0, 100, "%"),
                 ("duty_pct", "占空比", 0, 100, "%"))
        for i, (key, label, lo, hi, unit) in enumerate(specs):
            row = self._edit_field(label, "awg_" + key, lo, hi, unit,
                                   (lambda k=key: self._awg()[k]),
                                   (lambda v, k=key: self._set_awg(k, v)), page)
            row.x, row.y, row.w, row.h = lx + 14, ly + 148 + i * 50, lw - 28, 46
            self.awg_rows[key] = row
        self.awg_plot = Plot(rx + 14, ry + 44, rw - 28, rh - 130,
                             badge="合成示意 | 非测量", badge_size=MIN_CJK_SIZE)
        self.page_widgets[page].append(self.awg_plot)
        # ---- RF 参数 ----
        self.rf_parameter_rows = {}
        columns = (
            (("lo_hz", "本振", 1, M.RF_LO_MAX_HZ, "Hz"),
             ("sample_rate_hz", "采样率", 1, M.RF_PROTOCOL_INT_MAX, "Hz"),
             ("attenuation_mdB", "衰减", 0, M.RF_SAFE_ATTENUATION_MDB, "mdB"),
             ("phy_mode_1r1t", "PHY模式", 0, 1, "")),
            (("rx_bandwidth_hz", "RX带宽", 1, M.RF_PROTOCOL_INT_MAX, "Hz"),
             ("tx_bandwidth_hz", "TX带宽", 1, M.RF_PROTOCOL_INT_MAX, "Hz"),
             ("idelay_en", "延迟开关", 0, 1, ""),
             ("idelay_tap", "延迟档位", 0, 31, "")))
        for (x, y, width, _height), fields in zip(((lx, ly, lw, lh), (rx, ry, rw, rh)), columns):
            for i, (name, label, lo, hi, unit) in enumerate(fields):
                row = self._edit_field(
                    label, "rf_" + name, lo, hi, unit,
                    lambda name=name: self.app.rf_parameter_values().get(name, ""),
                    lambda value, name=name: self.app.set_rf_parameter(name, value),
                    page, size=16)
                row.x, row.y, row.w, row.h = x + 14, y + 54 + i * 44, width - 28, 40
                self.rf_parameter_rows[name] = row
        self.rf_clear_parameters_btn = self._chip(
            page, lx + 14, ly + 248, 124, 32, "清空参数",
            self._rf_parameters_clear_tap, size=14)
        # ---- RF 发射参数 ----
        self.rf_wave_row = self._edit_field(
            "波形类型", "tx_waveform", 1, len(M.TX_WAVEFORM_TYPES), "",
            lambda: self.app.rf_parameter_values().get("tx_waveform", ""),
            lambda v: self.app.set_rf_parameter("tx_waveform", v), page, size=16)
        self.rf_wave_row.x, self.rf_wave_row.y, self.rf_wave_row.w, self.rf_wave_row.h = lx + 14, ly + 54, lw - 28, 40
        # Named readable waveform buttons replace the numeric ValueRow on the RF transmit page.
        self.rf_wave_btns = []
        for i, name in enumerate(M.TX_WAVEFORM_TYPES):
            r, c = divmod(i, 5)
            self.rf_wave_btns.append(self._chip(
                page, lx + 14 + c * 52, ly + 42 + r * 46, 50, 40,
                M.TX_WAVEFORM_NAMES[i], self._rf_wave_tap(name), size=14))
        self.rf_tone_rows = {}
        for i, (name, label, hi) in enumerate((("tx_cycles", "每帧周期", 511), ("tx_amplitude", "峰值码", 2047))):
            row = self._edit_field(label, name, 1, hi, "",
                lambda name=name: self.app.rf_parameter_values().get(name, ""),
                lambda v, name=name: self.app.set_rf_parameter(name, v), page, size=16)
            row.x, row.y, row.w, row.h = lx + 14, ly + 130 + i * 44, lw - 28, 40
            self.rf_tone_rows[name] = row
        self.rf_extra_rows = {}
        for i, (name, label, hi) in enumerate((("tone2_cycles", "第二音周期", 511),
                                               ("tone2_amplitude", "第二音峰值", 2047),
                                               ("chirp_f1_cycles", "Chirp终止周期", 511),
                                               ("seed", "噪声种子", 65535))):
            row = self._edit_field(label, name, 0 if name == "seed" else 1, hi, "",
                lambda name=name: self.app.rf_parameter_values().get(name, ""),
                lambda v, name=name: self.app.set_rf_parameter(name, v), page, size=15)
            row.x, row.y, row.w, row.h = lx + 14, ly + 218 + (i % 2) * 44, lw - 28, 40
            self.rf_extra_rows[name] = row
        self.rf_normal_rows = {}
        normal_specs = (
            ("tx_frequency_hz", "基带频率", M.RF_SAMPLE_RATE_MAX_HZ, "Hz", 0),
            ("tx_amplitude_pct", "幅度", 100, "%", 1),
            ("tone2_frequency_hz", "第二音频率", M.RF_SAMPLE_RATE_MAX_HZ, "Hz", 2),
            ("tone2_amplitude_pct", "第二音幅度", 100, "%", 3),
            ("chirp_f1_hz", "Chirp终止频率", M.RF_SAMPLE_RATE_MAX_HZ, "Hz", 2),
            ("seed", "噪声种子", 65535, "", 2),
            ("duty_pct", "占空比", 100, "%", 2),
        )
        for i, (name, label, hi, unit, pos) in enumerate(normal_specs):
            lo = 0 if name in ("tx_amplitude_pct", "tone2_amplitude_pct", "duty_pct", "seed") else 1
            row = self._edit_field(label, name, lo, hi, unit,
                lambda name=name: self.app.rf_parameter_values().get(name, ""),
                lambda v, name=name: self.app.set_rf_parameter(name, v), page, size=15)
            row.x, row.y, row.w, row.h = lx + 14, ly + 130 + pos * 44, lw - 28, 40
            self.rf_normal_rows[name] = row
        self.rf_advanced_btn = self._chip(
            page, lx + 14, ly + 8, 124, 28, "普通/高级",
            self._rf_wave_mode_tap, size=13)
        # ---- RF 控制动作 ----
        chip_y = ry + 248
        chip_h = 32
        gap = 8
        x0 = rx + 14
        usable = rw - 28
        slot_w = (usable - (RF_CONTROL_SLOTS - 1) * gap) // RF_CONTROL_SLOTS
        self.rf_action_slots = []
        self.rf_action_btns = []
        for i in range(RF_CONTROL_SLOTS):
            slot = {"index": i, "action": None}
            btn = self._chip(page, x0 + i * (slot_w + gap), chip_y, slot_w, chip_h,
                             "", self._rf_action_tap(slot), size=13)
            self.rf_action_slots.append(slot)
            self.rf_action_btns.append(btn)
        confirm_w = slot_w
        self.rf_confirm_btn = self._chip(
            page, x0, chip_y, confirm_w, chip_h, "确认", self._rf_confirm_tap, size=13)
        self.rf_cancel_btn = self._chip(
            page, x0 + confirm_w + gap, chip_y, confirm_w, chip_h, "取消",
            self._rf_cancel_tap, size=13)

    def _build_digital_page(self):
        page = M.PAGE_DIGITAL
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(page)
        # 三种交付模式(不含 OFF —— OFF 是运行态, 由底栏的启动/关闭管)；
        # QAM64 已移出本次交付支持范围，不再生成按钮/不可选中。
        self.dig_mode_btns = []
        for i, mode in enumerate(("BPSK", "QPSK", "QAM16")):
            self.dig_mode_btns.append(self._chip(
                page, lx + 14 + i * 84, ly + 26, 78, 40, mode,
                self._mode_tap(mode), size=13))
        # 符号率 / 滤波 / 滚降 / 种子
        self.dig_rows = {}
        self.dig_rows["symrate_hz"] = self._edit_field(
            "符号率", "dig_symrate_hz", M.SYMRATE_MIN, M.SYMRATE_MAX, "Sym/s",
            lambda: (self.app.rf_digital_values().get("symbol_rate_hz")
                     if self.app.selected_device == "AD9361"
                     else self.app.draft["dig"]["symrate_hz"]),
            lambda v: self._set_dig("symrate_hz", v), page, size=18)
        self.dig_rows["symrate_hz"].x, self.dig_rows["symrate_hz"].y = lx + 14, ly + 74
        self.dig_rows["symrate_hz"].w, self.dig_rows["symrate_hz"].h = lw - 28, 46
        self.dig_filter_btns = [
            self._chip(page, lx + 14 + i * 82, ly + 142, 76, 40, name,
                       self._filter_tap(name), size=15)
            for i, name in enumerate(M.FILTERS)]
        self.dig_roll_btns = [
            self._chip(page, lx + 14 + i * 78, ly + 188, 72, 40, "r " + r,
                       self._rolloff_tap(r), size=15)
            for i, r in enumerate(M.ROLLOFFS)]
        self.dig_rows["seed"] = self._edit_field(
            "PRBS7 种子", "dig_seed", M.SEED_MIN, M.SEED_MAX, "",
            lambda: (self.app.rf_digital_values().get("seed")
                     if self.app.selected_device == "AD9361"
                     else self.app.draft["dig"]["seed"]),
            lambda v: self._set_dig("seed", v), page, size=18)
        self.dig_rows["seed"].x, self.dig_rows["seed"].y = lx + 14, ly + 236
        self.dig_rows["seed"].w, self.dig_rows["seed"].h = lw - 28, 46
        self.dig_amp_row = self._edit_field(
            "峰值码", "dig_amplitude", M.RF_DIGITAL_AMPLITUDE_MIN,
            M.RF_DIGITAL_AMPLITUDE_MAX, "",
            lambda: self.app.rf_digital_values().get("amplitude", ""),
            lambda v: self.app.set_rf_digital_parameter("amplitude", v),
            page, size=16)
        self.dig_amp_row.x = rx + 14
        self.dig_amp_row.y = ry + rh - 60
        self.dig_amp_row.w = rw - 28
        self.dig_amp_row.h = 40
        self.dig_load_btn = self._chip(
            page, rx + 14, ry + rh - 18, rw - 28, 32, "加载调制",
            self._digital_load_tap, size=14)
        self.dig_plot = Plot(rx + 14, ry + 14, rw - 28, rh - 100, square=True,
                             badge="理想发射示意 | 非测量", badge_size=MIN_CJK_SIZE)
        self.page_widgets[page].append(self.dig_plot)

    def _build_channel_page(self):
        page = M.PAGE_CHANNEL
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(page)
        # 通道选择与 AWG 页同一种摆法: 与"通道"标签同处一行(见 `_build_awg_page`)
        self.chan_ch_btns = [
            self._chip(page, lx + 98 + i * 90, ly + 10, 84, 32, "CH%d" % (i + 1),
                       self._select_channel(i), size=15)
            for i in range(2)]
        self.chan_en_btns = [
            self._chip(page, lx + 14 + i * 118, ly + 52, 110, 40, name,
                       self._chan_en_tap(on), size=15)
            for i, (name, on) in enumerate((("逐位旁路", False), ("损伤开启", True)))]
        self.chan_rows = {}
        self.chan_rows["noise_pct"] = self._edit_field(
            "噪声等级", "chan_noise", 0, 100, "%",
            lambda: (self.app.rf_channel_values().get("noise_pct")
                     if self.app.selected_device == "AD9361"
                     else self.app.draft["chan"][self.app.selected_channel]["noise_pct"]),
            lambda v: self._set_chan("noise_pct", v), page, size=18)
        self.chan_rows["noise_pct"].x, self.chan_rows["noise_pct"].y = lx + 14, ly + 108
        self.chan_rows["noise_pct"].w, self.chan_rows["noise_pct"].h = lw - 28, 46
        # AD9361 channel impairment exposes the deterministic noise seed.
        self.chan_rows["noise_seed"] = self._edit_field(
            "噪声种子", "chan_noise_seed", 0, 2147483647, "",
            lambda: (self.app.rf_channel_values().get("noise_seed")
                     if self.app.selected_device == "AD9361"
                     else 0),
            lambda v: self._set_chan("noise_seed", v), page, size=18)
        self.chan_rows["noise_seed"].x, self.chan_rows["noise_seed"].y = lx + 14, ly + 158
        self.chan_rows["noise_seed"].w, self.chan_rows["noise_seed"].h = lw - 28, 44
        # 4 抽头表(右栏)
        # 表头画在 `size - 2`, 所以这里给 16 让表头落在中文下限上(15 会让表头只有 13px)
        self.chan_table = Table(rx + 14, ry + 30, rw - 28, ["抽头", "增益 Q8", "延迟"],
                                [[str(k), "", ""] for k in range(M.TAPS)],
                                on_cell=self._tap_cell, size=16)
        self.page_widgets[page].append(self.chan_table)
        self.chan_apply_btn = self._chip(
            page, rx + 14, ry + rh - 48, rw - 28, 36, "应用损伤",
            self._channel_apply_tap, size=14)

    def _build_future_page(self):
        """接收页: 只保留接收/采样/解调/智能相关控件。"""
        page = M.PAGE_FUTURE
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(page)
        self.rx_tab_btns = [
            self._chip(page, lx + 14 + i * 43, ly + 7, 40, 34, name,
                       self._rx_tab_tap(key), size=14)
            for i, (key, name) in enumerate(
                (("overview", "概览"), ("sampling", "采样"), ("demod", "解调"),
                 ("smart", "智能"), ("constellation", "星座"), ('environment','环境')))
        ]
        # 在途时**同一个控件**变成"取消": 普通捕获按钮不会变成发射按钮, 也不会让第二次
        # 采集溜出去(它读的是模型发布的在途状态, 不是本地猜的)。
        self.rx_capture_btn = self._chip(
            page, lx + 14, ly + lh - 50, 80, 38, "捕获快照",
            lambda _b: self._rx_event("cancel_capture"
                                      if M.rx_capture_inflight(self.app) else "capture"),
            size=MIN_CJK_SIZE)
        self.rx_clear_btn = self._chip(
            page, lx + 103, ly + lh - 50, 80, 38, "清除快照",
            lambda _b: self._rx_event("clear"), size=MIN_CJK_SIZE)
        # 回环是**独立**的动作, 并且必须显式确认; 它的确认/取消**顶掉**普通动作那一行
        # (与 '_sync_rf_control' 里待确认时的规则一致), 所以不新增几何位置。
        self.rx_loopback_btn = self._chip(
            page, lx + 192, ly + lh - 50, 80, 38, "回环捕获",
            lambda _b: self._rx_event("loopback"), size=MIN_CJK_SIZE)
        self.rx_loopback_confirm_btn = self._chip(
            page, lx + 14, ly + lh - 50, 124, 38, "确认回环",
            lambda _b: self._rx_event("loopback_confirm"), size=14)
        self.rx_loopback_cancel_btn = self._chip(
            page, lx + 146, ly + lh - 50, 124, 38, "取消回环",
            lambda _b: self._rx_event("loopback_cancel"), size=14)
        # 先显示频谱/IQ/质量: 两个 Plot 只画**分析结果**, 不画任何帧载荷。
        self.rx_spectrum_plot = Plot(lx + 14, ly + 150, lw - 28, 58,
                                     badge="幅度谱 (窗系数归一)", badge_size=MIN_CJK_SIZE)
        self.rx_iq_plot = Plot(rx + 14, ry + 200, rw - 28, 68,
                               badge="IQ 预览 (码值/2048)", badge_size=MIN_CJK_SIZE)
        for widget in (self.rx_iq_plot, self.rx_spectrum_plot):
            self.page_widgets[page].append(widget)
        self.rx_decim_btns = [
            self._chip(page, lx + 14 + i * 52, ly + 58, 46, 36, "D%d" % d,
                       self._decim_tap(d), size=14)
            for i, d in enumerate(M.RX_DECIM_OPTIONS)]
        self.rx_len_row = self._edit_field(
            "抽取后点数", "rx_len", 1, 4096, "点",
            lambda: self.app.rx_draft["rx_len"],
            lambda v: self._set_rx("rx_len", v), page, size=18)
        self.rx_len_row.x, self.rx_len_row.y = lx + 14, ly + 102
        self.rx_len_row.w, self.rx_len_row.h = lw - 28, 44
        self.rx_mode_btns = [
            self._chip(page, lx + 14 + i * 80, ly + 58, 74, 36, mode,
                       self._rx_mode_tap(mode), size=13)
            for i, mode in enumerate(("BPSK", "QPSK", "QAM16"))]
        self.rx_rate_row = self._edit_field(
            "预期符号率", "rx_symrate", M.SYMRATE_MIN, M.RX_INPUT_FS_HZ // 2, "Sym/s",
            lambda: self.app.rx_draft["symrate_hz"],
            lambda v: self._set_rx("symrate_hz", v), page, size=16)
        self.rx_rate_row.x, self.rx_rate_row.y = lx + 14, ly + 98
        self.rx_rate_row.w, self.rx_rate_row.h = lw - 28, 40
        self.rx_shape_btns = [
            self._chip(page, lx + 14 + i * 90, ly + 144, 84, 32, shape,
                       lambda _b, s=shape: self._set_rx('shaping', s), size=14)
            for i, shape in enumerate(('RECT', 'RRC'))]
        self.rx_reference_btn = self._chip(
            page, lx + 14, ly + 182, lw - 28, 34, 'PRBS7参考: 关闭',
            lambda _b: self._set_rx('reference_enabled',
                                   not self.app.rx_draft['reference_enabled']), size=14)
        self.rx_seed_row = self._edit_field(
            '参考种子', 'rx_reference_seed', 1, 127, '',
            lambda: self.app.rx_draft['reference_seed'],
            lambda v: self._set_rx('reference_seed', v), page, size=16)
        self.rx_seed_row.x, self.rx_seed_row.y = lx + 14, ly + 222
        self.rx_seed_row.w, self.rx_seed_row.h = lw - 28, 40
        self.rx_fill_btn = self._chip(
            page, lx + 14, ly + 218, lw - 28, 38, "建议填入接收草稿",
            lambda _b: self._rx_event("fill_recommendation"), size=14)
        self.teaching_profile_btn = self._chip(page, lx+14, ly+54, lw-28, 32,
            '填入教学配置', self._teaching_profile_tap, size=14)
        self.teaching_advanced_btn = self._chip(page, lx+14, ly+94, lw-28, 32,
            '高级发射工具', self._teaching_advanced_tap, size=14)
        self.rx_save_btn = self._chip(page,lx+14,ly+134,lw-28,32,'保存当前实验',
            lambda _b:self._rx_event('save_experiment'),size=14)
        self.rx_save_history_btn = self._chip(page,lx+14,ly+174,lw-28,32,'另存历史实验',
            lambda _b:self._rx_event('save_history'),size=14)
        self.rx_constellation_plot = Plot(rx+14,ry+36,rw-28,166,
            badge='真实接收 / 归一化同步符号',badge_size=MIN_CJK_SIZE,square=True,inset=.3)
        self.page_widgets[page].append(self.rx_constellation_plot)
        # 场景按钮 = 模型声明的真实支持范围(M.ENVIRONMENT_SCENES, 与 rf_experiment_scenarios
        # .KINDS 相等); 这里只负责它的中文名, 不再维护第二份清单。 5 个按钮排成 3 列 2 行,
        # 行底 ly+120 仍低于"填入教学配置"(ly+126), 所以不遮挡环境页其它控件。
        self.environment_scene_kinds = tuple(M.ENVIRONMENT_SCENES)
        _scene_names = {'normal':'正常基线','narrowband':'窄带实验','pulse':'脉冲实验',
                        'compound':'复合负控','off':'OFF负控'}
        self.environment_scene_btns = [self._chip(page,lx+14+(i%3)*86,
            ly+54+(i//3)*36,84,30,_scene_names[kind],
            lambda _b,k=kind:self._environment_scene_tap(k),size=14)
            for i,kind in enumerate(self.environment_scene_kinds)]
        self.environment_load_btn = self._chip(page,lx+14,ly+162,128,32,'加载实验',
            self._environment_load_tap,size=14)
        self.environment_cancel_btn = self._chip(page,lx+150,ly+162,128,32,'取消加载',
            self._rf_cancel_tap,size=14)
        # 余量研究是**显式两下**的硬件动作, 与"加载实验/取消加载"共用同一行(可见性互斥,
        # 与回环的确认/取消顶掉普通动作那一行同一种做法): 第一下只登记意图, 第二下才发。
        # 研究要消耗 daemon 唯一的一次初始化且本进程不可重跑, 所以一下绝不够。
        self.headroom_study_btn = self._chip(page,lx+150,ly+162,128,32,'余量研究',
            lambda _b:self._rx_event('headroom_study'),size=14)
        self.headroom_confirm_btn = self._chip(page,lx+14,ly+162,128,32,'确认研究',
            lambda _b:self._rx_event('headroom_confirm'),size=14)
        self.headroom_cancel_btn = self._chip(page,lx+150,ly+162,128,32,'取消研究',
            lambda _b:self._rx_event('headroom_cancel'),size=14)

    def _environment_scene_tap(self, kind):
        self.app.teaching_message = self.app.set_environment_scene(kind) or '场景草稿已选择；请确认加载'
        self._sync_rx()
        self.invalidate()

    def _environment_load_tap(self, _b):
        if self.app.rf_control.get('pending_action') == 'load':
            self._rf_confirm_tap(_b)
        else:
            self._rf_control_tap('load')
        self._sync_rx()

    def _teaching_profile_tap(self, _b):
        self.app.apply_teaching_profile()
        self._sync_rx()
        self.invalidate()

    def _teaching_advanced_tap(self, _b):
        self.app.page=M.PAGE_DIGITAL
        self.invalidate()

    def _rf_parameters_clear_tap(self, _b):
        self.app.clear_rf_parameters()
        self._sync_rx()
        self.invalidate()

    def _tx_tab_tap(self, subpage):
        def tap(_b):
            self.app.tx_subpage = "transmit" if self.app.selected_device == "AD9361" and subpage == "awg" else subpage
            self._sync_tx()
            self.invalidate()
        return tap

    def _rx_tab_tap(self, subpage):
        def tap(_b):
            self.app.rx_subpage = subpage
            self._sync_rx()
            self.invalidate()
        return tap

    def _decim_tap(self, decim):
        def tap(_b):
            self._set_rx('decim', decim)
        return tap

    def _rx_mode_tap(self, mode):
        def tap(_b):
            self._set_rx('expected_mode', mode)
        return tap

    def _set_rx(self, key, value):
        error = self.app.set_rx_demod_setting(key, value)
        if error:
            self.app.rx_status_message = error
        self._sync_rx()
        self.invalidate()

    def _rx_event(self, event):
        """RX 事件只上送唯一控制服务; 本层不碰 socket、不碰寄存器、不做门禁判断。

        'capture' 只接收, 'clear' 只清本地快照, 'loopback' 是**显式确认**的独立动作。
        门禁全在 'ui_model' 里(见 'rx_capture_block_reason'), 本层连判断都不重复一遍。
        """
        if self._on_rx is not None:
            self._on_rx(event)
        else:
            self.app.rx_status_message = "RF 服务未接入"
        self._sync_rx()
        self.invalidate()

    def _build_system_page(self):
        # 系统页只有"导出日志/恢复演示"两个按钮, 其余是静态说明与日志区。
        page = M.PAGE_SYSTEM
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(page)
        self.sys_btns = [
            self._chip(page, lx + 14 + i * 132, ly + lh - 56, 124, 42, name,
                       cb, size=15)
            for i, (name, cb) in enumerate((("导出配置", self._export),
                                            ("恢复演示", self._reset_demo)))]

    def _build_ddr_page(self):
        """数据页: 左栏状态/播放控制, 右栏本地文件表 + 队列操作。

        本页**没有键盘** —— 唯一的数值是文件尺寸(只读)。所以整页只有 chips 与一个
        `Table`, 命中判定简单。布局按右栏宽算过: 4 个动作 chip 各 82 宽、间隔 8, 末个
        止于 `rx+366`, 卡片内容区右界 `rx+369` —— 不越界也不互相重叠(有测试钉住)。
        """
        page = M.PAGE_DDR
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(page)
        # 左栏: 环形开关 + 装填 + 播放控制。**尺寸按卡片真实宽度算**, 不按"右边那个大概多宽"推
        # (本项目在底栏上踩过: 按错误宽度推 x, 两个按钮重叠 18px)。
        # 状态行止于 `ly+14+6*21+14 = ly+154`, 所以控制从 ly+168 起。
        #
        # ⚠ 「装填」**必须和环形挤在同一行** —— 左栏卡片只有 302px 高, 再开一行会溢出到
        #   底栏上(实测: 单独一行时按钮底边到 411、卡片底边才 398, 提示行落在 418 —— 正好
        #   压在底栏里, 被底栏背景盖掉)。120+126+8 = 254, 内容区 258, 刚好装下。
        self.ddr_ring_btn = self._chip(
            page, lx + 14, ly + 168, 120, 38, "环形: 关",
            lambda _b: self._ddr("ring"), size=15)
        # 「装填」= 把队列里的一块放进 bank。它是数据页**唯一**会被 `digmod_mode` 挡住的
        # 按钮: 装填要求 MODE OFF, 起播要求非 OFF —— 两者不可能同时成立。所以流程是
        # 装填 -> 打开调制 -> 启动(与主机 GUI 的手动上传同款)。
        self.ddr_load_btn = self._chip(
            page, lx + 142, ly + 168, 126, 38, "装填",
            lambda _b: self._ddr("load"), size=15)
        self.ddr_start_btn = self._chip(
            page, lx + 14, ly + 218, 124, 42, "启动",
            lambda _b: self._ddr("start"), size=16)
        self.ddr_stop_btn = self._chip(
            page, lx + 146, ly + 218, 124, 42, "停止",
            lambda _b: self._ddr("stop"), size=16)
        # 右栏: 文件表。**行数固定** `DDR_PAGE_ROWS`(没有滚动, 靠翻页), 所以 Table 的
        # 高度在构造期就是对的 —— 每帧只换 `cells`(空行补空串), 不改行数。
        #
        # ⚠ y 从 `ry+26` 起: "本地文件"那行标签画在 `ry+6`、**高 19px**(14px 中文的实测
        #   行高, 不是字号)—— 到 `ry+25`, 表放 `ry+24` 会被压 1px。这 1px 只有比矩形
        #   才看得见(`test_static_labels_are_not_covered_by_controls`)。
        self.ddr_table = Table(
            rx + 14, ry + 48, rw - 28, ["文件", "字节"],
            [["", ""] for _ in range(M.DDR_PAGE_ROWS)],
            on_cell=self._ddr_cell, size=16)
        self.page_widgets[page].append(self.ddr_table)
        # 表底 = 26 + 22 + 34*4 = 184。翻页与动作两行紧跟在它下面。
        self.ddr_prev_btn = self._chip(
            page, rx + 14, ry + 210, 60, 34, "<",
            lambda _b: self._ddr("page", -1), size=16)
        self.ddr_page_btn = self._chip(
            page, rx + 82, ry + 210, 90, 34, "1/1",
            lambda _b: self._ddr("refresh"), size=14)
        self.ddr_next_btn = self._chip(
            page, rx + 180, ry + 210, 60, 34, ">",
            lambda _b: self._ddr("page", 1), size=16)
        # 队列操作。末个止于 rx+14+3*90+82 = rx+366, 卡片内容区右界 rx+369 —— 不越界。
        specs = (("入队", "enqueue"), ("移除", "remove"),
                 ("清空", "clear"), ("刷新", "refresh"))
        self.ddr_act_btns = [
            self._chip(page, rx + 14 + i * 90, ry + 250, 82, 40, name,
                       (lambda c=cmd: (lambda _b: self._ddr(c)))(cmd), size=15)
            for i, (name, cmd) in enumerate(specs)]

    def _ddr(self, cmd, arg=None):
        """数据页的动作出口。**本层不判断可行性** —— 交给 `ui_model`/`RingThread`。"""
        if self._on_ddr is not None:
            self._on_ddr(cmd, arg)

    def _ddr_cell(self, row, col):
        """点文件表的一行 → 选中它(再按"入队"/"移除"操作)。

        **不预建控件**: 格子本身就是可点区域(与信道页的抽头表同一种做法)。
        """
        if col != 0:
            return
        self.app.ddr_selected = row
        self.invalidate()

    # ---- 草稿写入(全部只改 draft, 不下发) ----

    def _awg(self):
        return self.app.draft["awg"][self.app.selected_channel]

    def _set_awg(self, key, value):
        self._awg()[key] = value
        self.invalidate()

    def _set_dig(self, key, value):
        if key == "mode" and value == "QAM64":
            # 旧选中态/外部调用显式拒绝；不自动回落，也不触发发射。
            self.app.last_message = "QAM64超出本次交付支持范围，请重新选择 BPSK/QPSK/QAM16"
            self.invalidate()
            return
        if self.app.selected_device == "AD9361":
            why = self.app.set_rf_digital_parameter(key, value)
            if why is not None:
                self.app.last_message = why
        else:
            self.app.draft["dig"][key] = value
        self.invalidate()

    def _set_chan(self, key, value):
        if self.app.selected_device == "AD9361":
            why = self.app.set_rf_channel_parameter(key, value)
            if why is not None:
                self.app.last_message = why
        else:
            self.app.draft["chan"][self.app.selected_channel][key] = value
        self.invalidate()

    def _digital_load_tap(self, _b):
        why = self.app.set_rf_signal_kind("digital")
        if why is not None:
            self.app.last_message = why
            self.invalidate()
            return
        self._rf_control_tap("load")

    def _channel_apply_tap(self, _b):
        why = self.app.set_rf_channel_apply()
        if why is not None:
            self.app.set_rf_control_result(
                "load", {"ok": False, "code": "INVALID_ARGUMENT", "message": why})
            self.invalidate()
            return
        self._rf_control_tap("load")

    def _wave_tap(self, name):
        return lambda _b: self._set_awg("wave", name)

    def _rf_wave_tap(self, name):
        def tap(_b):
            why = self.app.set_rf_parameter("tx_waveform", name)
            if why is not None:
                self.app.last_message = why
            self.invalidate()
        return tap

    def _mode_tap(self, mode):
        return lambda _b: self._set_dig("mode", mode)

    def _filter_tap(self, name):
        return lambda _b: self._set_dig("filter", name)

    def _rolloff_tap(self, name):
        return lambda _b: self._set_dig("rolloff", name)

    def _chan_en_tap(self, on):
        return lambda _b: self._set_chan("enabled", on)

    def _tap_cell(self, row, col):
        """点表格里的格子 → 开键盘编辑那个抽头字段。

        **不预建控件**: 格子本身就是可点区域, 这里只按当前通道/行列算出该编辑哪个字段,
        然后直接开弹窗。预建 8 个看不见的 `ValueRow` 只会多出一批幽灵控件。

        ⚠ 字段名是 `gain_q8` / `delay_samples`(与 `ui_model` 的配置结构一致), 别写成
          `_samples` —— 那种拼错会在点格子时才炸, 而界面看起来完全正常。
        """
        if col == 0:
            return
        gain = (col == 1)
        lo, hi = (M.GAIN_MIN, M.GAIN_MAX) if gain else (M.DELAY_MIN, M.DELAY_MAX)
        unit = "Q8" if gain else "采样"
        field = "gain_q8" if gain else "delay_samples"
        label = "TAP%d %s" % (row, "增益" if gain else "延迟")
        ci = self.app.selected_channel
        if self.app.selected_device == "AD9361":
            getter = lambda: self.app.rf_channel_values()["taps"][row][field]
            setter = lambda v: self.app.set_rf_channel_tap(row, field, v)
        else:
            getter = lambda: self.app.draft["chan"][ci]["taps"][row][field]
            setter = lambda v: self._set_tap(row, field, v)
        self._open_editor(label, "tap%d_%s" % (row, field), lo, hi, unit, getter, setter)

    def _set_tap(self, row, field, value):
        if self.app.selected_device == "AD9361":
            why = self.app.set_rf_channel_tap(row, field, value)
            if why is not None:
                self.app.last_message = why
        else:
            self.app.draft["chan"][self.app.selected_channel]["taps"][row][field] = value
        self.invalidate()

    def _export(self):
        """导出配置与命令日志 —— 板端写文件(与参考设计的"导出配置"对应)。"""
        import json
        try:
            with open("/tmp/linklab-config.json", "w") as fh:
                json.dump({"draft": self.app.draft, "observed": self.app.observed,
                           "actual_mode": self.app.actual_mode}, fh,
                          ensure_ascii=False, indent=2)
            with open("/tmp/linklab-commands.txt", "w") as fh:
                fh.write("\n".join(self.app.log))
            self.app.last_message = "已导出到 /tmp"
        except OSError as exc:
            self.app.last_message = "导出失败: %s" % exc
        self.invalidate()

    def _reset_demo(self):
        self.app.draft = M.default_config()
        self.invalidate()

    # ---- 渲染 ----

    def invalidate(self):
        self._last_sig = None

    def _prepare_input(self):
        page = self.app.page
        if page == M.PAGE_STATUS and self.status_screen is not None:
            self.keypad.close()
        for p, widgets in self.page_widgets.items():
            for w in widgets:
                w.visible = p == page
        for i, (p, _icon, _name) in enumerate(NAV_ITEMS):
            nav_page = M.PAGE_TOOLS if page in M.LEGACY_PAGES else page
            self.nav[i].selected = p == nav_page
        self._sync_page(page)
        target_page = page in (M.PAGE_TX, M.PAGE_TOOLS, M.PAGE_DIGITAL, M.PAGE_CHANNEL)
        for device, button in self.device_buttons.items():
            button.visible = target_page
            button.enabled = not self.app.pending and self.app._rf_inflight_action is None
            self._mark(button, device == self.app.selected_device)
        self.rf_stop_btn.visible = bool(self.app.rf_control.get("stop_reachable"))
        self.rf_stop_btn.enabled = self.rf_stop_btn.visible
        self.footer.apply_btn.enabled = (self.app.selected_device == "ACM2108" and
                                        self.app.connected and self.app.fresh and
                                        not self.app.pending and page in APPLY_FOR_PAGE)

    def poll_input(self):
        """Sample once before replay; render consumes this same input tick."""
        self._prepare_input()
        self._input_point = self.cursor.read() if self.cursor is not None else None
        touched = self.ui._poll_input()
        # Release callbacks may switch pages: hit visibility and drawing use that page now.
        self._prepare_input()
        self._input_ready = True
        return touched

    def render(self, state=None):
        """Process current input, then redraw only when visible content changes."""
        if not self._input_ready:
            self.poll_input()
        self._input_ready = False
        page = self.app.page
        point = self._input_point
        if page == M.PAGE_STATUS and self.status_screen is not None:
            drew = self.status_screen.render(state, point=point, flush=False)
            sig = ("status", getattr(state, "uptime", None), self.app.page,
                   self.app.connected, self.app.fresh, self.app.pending,
                   self.app.dirty, self.app.last_result, self.app.last_message,
                   repr(self.app.rf_control), self.app.running)
            if not drew and sig == self._last_sig:
                return
            self._last_sig = sig
            self._draw_chrome_only()
        else:
            sig = self._signature(state) + (point,)
            if sig == self._last_sig:
                return
            self._last_sig = sig
            self.ui.canvas.clear(BG)
            self._draw_page(page)
            for w in self.ui.widgets:
                if w.visible:
                    w.draw(self.ui.canvas)
        self.ui.lcd.flush()
        self.ui.frames += 1
        if not self._enabled:
            self.ui.lcd.enable()
            self._enabled = True

    def _draw_chrome_only(self):
        """状态页模式下只叠顶栏、导航与底栏(让用户切得回去)。

        ⚠ 底栏的按钮**是独立的顶层控件**(见 `Footer.draw`), 所以这里要把它们单独列进来 ——
        漏掉的话它们在状态页上就是"看不见但点得到"的幽灵区。这也是本工程踩过的同一类坑。

        **不 flush**: 本方法与状态屏画的是**同一块影子缓冲**(见 `__init__`), 由 `render()`
        在帧末统一推一次。这里再推一次就等于把状态屏画的内容盖掉、并制造屏闪。
        """
        chrome = [self.footer.apply_btn, self.footer.toggle_btn, self.rf_stop_btn]
        for w in self.ui.widgets:
            if w.visible and (isinstance(w, (Header, NavButton, Footer)) or w in chrome):
                w.draw(self.ui.canvas)

    def _sync_page(self, page):
        """把 `app.draft` 刷进这一页的控件(选中态/数值/表格/预览)。

        **控件不持有参数** —— 参数只存在 `app.draft` 里, 每帧刷一遍。这样"草稿改了界面
        就跟着变"是自动的, 不需要到处通知。
        """
        app = self.app
        ci = app.selected_channel
        d = app.draft
        if page == M.PAGE_AWG:
            self._sync_tx()
        elif page == M.PAGE_DIGITAL:
            dig = app.rf_digital_values() if app.selected_device == "AD9361" else d["dig"]
            for i, b in enumerate(self.dig_mode_btns):
                self._mark(b, ("BPSK", "QPSK", "QAM16")[i] == dig["mode"])
            for i, b in enumerate(self.dig_filter_btns):
                self._mark(b, M.FILTERS[i] == dig["filter"])
            for i, b in enumerate(self.dig_roll_btns):
                self._mark(b, M.ROLLOFFS[i] == dig["rolloff"])
            self.dig_rows["symrate_hz"].value = _hz(dig.get("symbol_rate_hz", dig.get("symrate_hz")))
            self.dig_rows["seed"].value = "%d" % dig["seed"]
            self.dig_plot.series = self._constellation_series(dig["mode"])
            if app.selected_device == "AD9361":
                self.dig_amp_row.value = str(dig.get("amplitude", ""))
                self.dig_amp_row.visible = True
                self.dig_load_btn.visible = True
            else:
                self.dig_amp_row.visible = False
                self.dig_load_btn.visible = False
            # 运行中锁定这些: ACM uses the legacy run state; AD9361 uses RF TX state.
            if app.selected_device == "AD9361":
                running = app.rf_transmitting() or app._rf_inflight_action is not None
            else:
                running = app.running
            for b in (self.dig_mode_btns + self.dig_filter_btns + self.dig_roll_btns):
                b.enabled = not running
            for row in self.dig_rows.values():
                row.enabled = not running
        elif page == M.PAGE_CHANNEL:
            ch = app.rf_channel_values() if app.selected_device == "AD9361" else d["chan"][ci]
            for i, b in enumerate(self.chan_ch_btns):
                self._mark(b, i == ci)
                b.visible = app.selected_device != "AD9361"
            for i, b in enumerate(self.chan_en_btns):
                self._mark(b, (i == 1) == ch["enabled"])
                b.visible = True
            self.chan_rows["noise_pct"].value = "%d" % ch["noise_pct"]
            self.chan_rows["noise_pct"].visible = True
            if app.selected_device == "AD9361":
                self.chan_rows["noise_seed"].value = "%d" % ch["noise_seed"]
                self.chan_rows["noise_seed"].visible = True
            else:
                self.chan_rows["noise_seed"].visible = False
            self.chan_table.cells = [[str(k), str(t["gain_q8"]), str(t["delay_samples"])]
                                     for k, t in enumerate(ch["taps"])]
            self.chan_table.visible = True
            self.chan_apply_btn.visible = app.selected_device == "AD9361"
        elif page == M.PAGE_FUTURE:
            self._sync_rx()
        elif page == M.PAGE_DDR:
            self._sync_ddr()
        elif page == M.PAGE_AI:
            self.ai_fill_btn.enabled = self.app.rx_fill_block() is None
        elif page == M.PAGE_TOOLS:
            self.tools_digital_btn.enabled = True
            self.tools_channel_btn.enabled = True

    def _sync_tx(self):
        app = self.app
        sub = app.tx_subpage if app.tx_subpage in M.TX_SUBPAGES else "awg"
        if app.selected_device == "ACM2108":
            sub = "awg"
        elif sub == "awg":
            sub = "parameters"
        app.tx_subpage = sub
        for i, b in enumerate(self.tx_tab_btns):
            self._mark(b, M.TX_SUBPAGES[i] == sub)
            b.visible = app.selected_device == "AD9361"
        awg_visible = sub == "awg" and app.selected_device == "ACM2108"
        for b in self.awg_ch_btns + self.awg_wave_btns:
            b.visible = awg_visible
        for row in self.awg_rows.values():
            row.visible = awg_visible
        self.awg_plot.visible = awg_visible
        parameters = app.rf_parameter_values()
        for name, row in self.rf_parameter_rows.items():
            row.visible = (sub == "parameters")
            row.enabled = app.rf_control.get("inflight_action") is None and not app.rf_transmitting()
            row.value = str(parameters[name]) if name in parameters else "未填"
        self.rf_clear_parameters_btn.visible = (sub == "parameters")
        self.rf_clear_parameters_btn.enabled = app.rf_control.get("inflight_action") is None and not app.rf_transmitting()
        # The RF transmit page now uses readable named buttons, not the numeric ValueRow.
        self.rf_wave_row.visible = False
        for i, name in enumerate(M.TX_WAVEFORM_TYPES):
            b = self.rf_wave_btns[i]
            b.visible = (sub == "transmit")
            b.enabled = app.rf_control.get("inflight_action") is None and not app.rf_transmitting()
        wf = M.rf_waveform_name(parameters.get("tx_waveform", 1))
        self.rf_wave_row.value = M.TX_WAVEFORM_NAMES[M.TX_WAVEFORM_TYPES.index(wf)] if wf else str(parameters.get("tx_waveform", ""))
        for i, name in enumerate(M.TX_WAVEFORM_TYPES):
            self._mark(self.rf_wave_btns[i], wf == name)
        self.rf_advanced_btn.visible = (sub == "transmit")
        self.rf_advanced_btn.enabled = app.rf_control.get("inflight_action") is None and not app.rf_transmitting()
        if wf is not None:
            self.rf_advanced_btn.text = "高级" if app._rf_wave_advanced else "普通"
        use_legacy = app._rf_wave_advanced or not any(
            name in parameters for name in ("tx_frequency_hz", "tx_amplitude_pct",
                                            "tone2_frequency_hz", "tone2_amplitude_pct",
                                            "chirp_f1_hz"))
        for name, row in self.rf_tone_rows.items():
            visible = (sub == "transmit" and use_legacy and wf is not None
                       and name in M.rf_waveform_fields(wf, advanced=True))
            row.visible = visible
            row.enabled = visible and app.rf_control.get("inflight_action") is None and not app.rf_transmitting()
            row.value = str(parameters[name]) if name in parameters else "未填"
        for name, row in self.rf_extra_rows.items():
            visible = (sub == "transmit" and use_legacy and wf is not None
                       and name in M.rf_waveform_fields(wf, advanced=True))
            row.visible = visible
            row.enabled = visible and app.rf_control.get("inflight_action") is None and not app.rf_transmitting()
            row.value = str(parameters[name]) if name in parameters else "未填"
        for name, row in self.rf_normal_rows.items():
            visible = (sub == "transmit" and wf is not None
                       and ((not use_legacy and name in M.rf_waveform_fields(wf, advanced=False))
                            or (use_legacy and name == "duty_pct" and wf == "pulse")))
            row.visible = visible
            row.enabled = visible and app.rf_control.get("inflight_action") is None and not app.rf_transmitting()
            row.value = str(parameters[name]) if name in parameters else "未填"
        if awg_visible:
            ci = app.selected_channel
            d = app.draft
            for i, b in enumerate(self.awg_ch_btns):
                self._mark(b, i == ci)
            for i, b in enumerate(self.awg_wave_btns):
                self._mark(b, M.WAVES[i] == d["awg"][ci]["wave"])
            a = d["awg"][ci]
            self.awg_rows["freq_hz"].value = _hz(a["freq_hz"])
            self.awg_rows["ampl_pct"].value = "%d" % a["ampl_pct"]
            self.awg_rows["duty_pct"].value = "%d" % a["duty_pct"]
            self.awg_rows["duty_pct"].enabled = (a["wave"] == "PULSE")
            self.awg_plot.series = self._wave_series(a)
        self._sync_rf_control()

    def _sync_rx(self):
        app = self.app
        sub = app.rx_subpage if app.rx_subpage in M.RX_SUBPAGES else "overview"
        app.rx_subpage = sub
        for i, b in enumerate(self.rx_tab_btns):
            self._mark(b, M.RX_SUBPAGES[i] == sub)
        capture = app.rx_capture if isinstance(getattr(app, "rx_capture", None), dict) else {}
        pending = capture.get("pending_action") == "loopback"
        inflight = M.rx_capture_inflight(app)
        environment = sub == 'environment'
        headroom_pending = bool(getattr(app, 'headroom_pending', False))
        overview = sub in ('overview','constellation','environment')
        self.teaching_profile_btn.visible = sub in ('constellation','environment')
        self.teaching_profile_btn.y = self._cols(M.PAGE_FUTURE)[0][1] + (126 if environment else 54)
        self.teaching_profile_btn.enabled = bool(app.teaching_status['available']
            and not inflight and not app.rf_transmitting() and app._rf_inflight_action is None)
        self.teaching_advanced_btn.visible = sub == 'constellation'
        symbols=app.rx_constellation_summary()
        self.rx_constellation_plot.visible=sub=='constellation'
        points=app.rx_constellation.points if app.rx_constellation is not None else ()
        # Draw separate measured dots; never connect symbols or substitute ideal points.
        self.rx_constellation_plot.series=[([(x-.012,y),(x+.012,y)],CYAN)
            for x,y in points]
        self.rx_save_btn.visible=self.rx_save_history_btn.visible=sub in ('constellation','environment')
        lx,ly,lw,_ = self._cols(M.PAGE_FUTURE)[0]
        self.rx_save_btn.y = ly + (202 if environment else 134)
        self.rx_save_history_btn.y = ly + (202 if environment else 174)
        self.rx_save_btn.w = self.rx_save_history_btn.w = 128 if environment else lw-28
        self.rx_save_btn.h = self.rx_save_history_btn.h = 28 if environment else 32
        self.rx_save_history_btn.x = lx + (150 if environment else 14)
        can_select = bool(app.environment_status['available'] and not inflight
            and not app.rf_transmitting() and app._rf_inflight_action is None)
        for b,kind in zip(self.environment_scene_btns,self.environment_scene_kinds):
            b.visible = environment
            b.enabled = can_select
            self._mark(b,kind==app.environment_scene and app._rf_signal_draft_kind=='environment')
        self.environment_load_btn.visible = environment and not headroom_pending
        self.environment_load_btn.enabled = bool(can_select and app._rf_signal_draft_kind=='environment'
            and 'load' in app.rf_control['actions'])
        load_pending = app.rf_control.get('pending_action')=='load'
        self.environment_load_btn.text = '确认加载' if load_pending else '加载实验'
        self.environment_cancel_btn.visible = (environment and load_pending
                                               and not headroom_pending)
        self.environment_cancel_btn.enabled = load_pending
        # 余量研究: 与"加载实验/取消加载"共用同一行的互斥可见性。研究只在这一页出现,
        # 且门禁完全来自模型(headroom_run_block), 这里不重复写一遍规则。
        self.headroom_study_btn.visible = (environment and not headroom_pending
                                           and not load_pending)
        self.headroom_study_btn.text = ('研究进行中' if app.headroom_running
                                        else '余量研究')
        self.headroom_study_btn.enabled = bool(not app.headroom_running
                                               and app.headroom_run_block() is None)
        self.headroom_confirm_btn.visible = environment and headroom_pending
        self.headroom_confirm_btn.enabled = self.headroom_confirm_btn.visible
        self.headroom_cancel_btn.visible = environment and (headroom_pending or app.headroom_running)
        self.headroom_cancel_btn.enabled = self.headroom_cancel_btn.visible
        self.rx_save_btn.enabled=bool(symbols['current'] and app.rx_save_status.get('state')!='saving')
        self.rx_save_history_btn.enabled=bool(app.rx_record_history is not None
            and app.rx_save_status.get('state')!='saving')
        # 动态解锁: 可用性由 daemon 广告 + 当前状态 + 已实现的 client 共同决定(判据在
        # ui_model.rx_capture_block_reason), 这里既**不写死为开**也不重复一遍规则。
        self.rx_capture_btn.visible = overview and not pending
        self.rx_clear_btn.visible = overview and not pending
        self.rx_loopback_btn.visible = overview and not pending
        self.rx_loopback_confirm_btn.visible = overview and pending
        self.rx_loopback_cancel_btn.visible = overview and pending
        self.rx_capture_btn.text = "取消捕获" if inflight else "捕获快照"
        self.rx_capture_btn.enabled = bool(inflight or app.rx_capture_block_reason() is None)
        self.rx_clear_btn.enabled = bool(inflight or app.rx_analysis is not None
                                         or app.rx_history)
        self.rx_loopback_btn.enabled = bool(not inflight
                                            and app.rx_loopback_block() is None)
        self.rx_loopback_confirm_btn.enabled = self.rx_loopback_confirm_btn.visible
        self.rx_loopback_cancel_btn.enabled = self.rx_loopback_cancel_btn.visible
        self.rx_decim_btns[0].visible = (sub == "sampling")
        for b in self.rx_decim_btns[1:]:
            b.visible = (sub == "sampling")
        self.rx_len_row.visible = (sub == "sampling")
        self.rx_mode_btns[0].visible = (sub == "demod")
        for b in self.rx_mode_btns[1:]:
            b.visible = (sub == "demod")
        self.rx_rate_row.visible = (sub == "demod")
        for b in self.rx_shape_btns:
            b.visible = (sub == 'demod')
            self._mark(b, b.text == app.rx_draft['shaping'])
        self.rx_reference_btn.visible = (sub == 'demod')
        self.rx_reference_btn.text = 'PRBS7参考: ' + ('开启' if app.rx_draft['reference_enabled'] else '关闭')
        self._mark(self.rx_reference_btn, app.rx_draft['reference_enabled'])
        self.rx_seed_row.visible = (sub == 'demod')
        self.rx_seed_row.value = str(app.rx_draft['reference_seed'])
        self.rx_fill_btn.visible = (sub == "smart")
        for i, b in enumerate(self.rx_decim_btns):
            self._mark(b, M.RX_DECIM_OPTIONS[i] == app.rx_draft["decim"])
        for b in self.rx_mode_btns:
            self._mark(b, b.text == app.rx_draft["expected_mode"])
        self.rx_len_row.value = "%d" % app.rx_draft["rx_len"]
        self.rx_rate_row.value = _hz(app.rx_draft["symrate_hz"])
        self.rx_fill_btn.enabled = app.rx_fill_block() is None
        self.rx_iq_plot.visible = (sub == "sampling")
        self.rx_spectrum_plot.visible = (sub == "sampling")
        self.rx_iq_plot.series = rx_iq_series(app.rx_analysis)
        self.rx_spectrum_plot.series = rx_spectrum_series(app.rx_analysis)

    def _sync_rf_control(self):
        app = self.app
        control = app.rf_control if isinstance(getattr(app, "rf_control", None), dict) else {}
        actions = tuple(control.get("actions") or ())
        pending = control.get("pending_action")
        sub = app.tx_subpage if app.tx_subpage in M.TX_SUBPAGES else "awg"
        overview = app.selected_device == "AD9361" and sub in ("parameters", "transmit")
        if sub == "parameters":
            actions = tuple(a for a in actions if a not in ("load", "start"))
        else:
            actions = tuple(a for a in actions if not (a == "configure" and ("load" in actions or "start" in actions)))
        shown = actions[:RF_CONTROL_SLOTS] if pending is None else (
            None, None, "stop" if control.get("stop_reachable") else None)
        for slot, btn in zip(self.rf_action_slots, self.rf_action_btns):
            action = shown[slot["index"]] if slot["index"] < len(shown) else None
            slot["action"] = action
            btn.visible = bool(overview and action)
            btn.enabled = bool(action)
            btn.text = RF_CONTROL_LABEL.get(action, "") if action else ""
        self.rf_confirm_btn.visible = bool(overview and pending)
        self.rf_cancel_btn.visible = bool(overview and pending)

    def _rf_action_tap(self, slot):
        def tap(_b):
            action = slot.get("action")
            if action:
                self._rf_control_tap(action)
        return tap

    def _rf_control_tap(self, action):
        accepted, needs_confirm, _message = self.app.rf_control_prompt(action)
        if not accepted:
            self.app.set_rf_control_result(
                action, {"ok": False, "code": "INVALID_ARGUMENT", "message": _message})
        elif not needs_confirm:
            self._rf_control_execute(action)
        # 按钮在抬起沿才回调, 而 `_sync_rf_control` 在收输入**之前**已经跑过 ——
        # 不在这里重同步的话, "确认/取消" 要等下一帧才出现(一帧的选择空窗)。
        self._sync_rf_control()
        self.invalidate()

    def _rf_confirm_tap(self, _b):
        action = self.app.rf_control.get("pending_action")
        if action:
            self._rf_control_execute(action)
        self._sync_rf_control()
        self.invalidate()

    def _rf_cancel_tap(self, _b):
        self.app.rf_control_cancel()
        self._sync_rf_control()
        self.invalidate()

    def _rf_control_execute(self, action):
        """模型门禁通过后, 只把动作上送一次 —— 本层不碰 socket。"""
        if action not in ("stop", "disarm") and self.app.selected_device != "AD9361":
            return
        if self.app.rf_control_block_reason(action) is not None:
            self.invalidate()
            return
        if self._on_rf_control is not None:
            self._on_rf_control(action)
        else:
            self.app.set_rf_control_result(
                action, {"ok": False, "code": "NOT_WIRED",
                         "message": "the panel has no RF control call path wired"})
        self.invalidate()

    def _sync_ddr(self):
        """把 `app.ddr_*` 与环形线程快照刷进本页控件。

        `app.pp` 是 RingThread 的**快照 dict**(空字典 = 线程缺席)。这里**只读它** ——
        与环形线程没有任何共享可变状态, 所以不需要锁。
        """
        app = self.app
        pp = app.pp or {}
        present = bool(pp.get("present"))
        active = bool(pp.get("active"))
        # 文件表: 当前窗口, 不足一屏补空行(行数必须固定 —— Table 的尺寸在构造期定死)
        start, win = M.ddr_window(app.ddr_files, app.ddr_page)
        rows = []
        for i in range(M.DDR_PAGE_ROWS):
            if i < len(win):
                name, size = win[i]
                mark = "*" if name in app.ddr_queue else " "
                rows.append(["%s %s" % (mark, name), "%d" % size])
            else:
                rows.append(["", ""])
        self.ddr_table.cells = rows
        # 页码
        self.ddr_page_btn.text = "%d/%d" % (app.ddr_page + 1,
                                            M.ddr_pages(len(app.ddr_files)))
        # 环形开关: 关闭时**也禁用 START** —— 单块播完就停, 那不是用户点"启动"想要的
        self.ddr_ring_btn.text = "环形: %s" % ("开" if app.ddr_ring else "关")
        self._mark(self.ddr_ring_btn, app.ddr_ring)
        # 启动门禁: 不可行时置灰(点一个什么都不做的按钮比灰按钮糟)
        why = M.ddr_start_block(app)
        self.ddr_start_btn.enabled = (why is None)
        self.ddr_stop_btn.enabled = (present and active)
        # 装填: 数据页唯一被 MODE 挡住的按钮 —— 原因由 `_draw_ddr_static` 写在左栏底部
        # (`app.ddr_status` 只在**点过之后**才有值, 而灰按钮点不出提示, 所以静态那行是
        # 必要的: 用户得看得出"为什么装填是灰的")。
        self.ddr_load_btn.enabled = (M.ddr_load_block(app) is None)
        for b in (self.ddr_prev_btn, self.ddr_next_btn):
            b.enabled = (len(app.ddr_files) > M.DDR_PAGE_ROWS)
        # 前三个 chip 要环形线程; **刷新不用**(只列本地目录) —— 降级时它仍可用,
        # 否则界面上"本地文件仍可浏览"就成了空话。
        for b in self.ddr_act_btns:
            b.enabled = present
        self.ddr_act_btns[3].enabled = True

    @staticmethod
    def _mark(btn, on):
        """选中态: 高亮边框与文字。Button 没有 selected 概念, 用颜色表达。"""
        btn.fill = BTN_ON_BG if on else BTN
        btn.edge = CYAN if on else LINE
        btn.fg = CYAN if on else TEXT

    # ---- 预览曲线的点(只依赖参数, 不依赖时间) ----

    @staticmethod
    def _wave_series(a):
        """一个周期的基础波形, **归一化坐标**(x, y 都是 -1..1)。静态函数图形, 不是采样流。

        点数按屏幕宽度取(700 点 ≈ 一个像素一点), 再多也画不出来。
        """
        import math
        n = 700
        pts = []
        for i in range(n):
            t = i / float(n - 1)
            ph = t % 1.0
            if a["wave"] == "SQUARE":
                v = 1.0 if ph < 0.5 else -1.0
            elif a["wave"] == "TRIANGLE":
                v = 1 - 4 * abs(ph - 0.5)
            elif a["wave"] == "SAW":
                v = 2 * ph - 1
            elif a["wave"] == "PULSE":
                v = 1.0 if ph < a["duty_pct"] / 100.0 else -1.0
            elif a["wave"] == "NOISE":
                # 确定性伪随机: 同一参数每次画出来一样。若用真随机, 签名每帧都变,
                # 会击穿"内容没变就不重画"。
                s = (i * 2654435761) & 0xFFFFFFFF
                v = (s / 4294967296.0) * 2 - 1
            else:
                v = math.sin(ph * 2 * math.pi)
            pts.append((t * 2 - 1, v * (a["ampl_pct"] / 100.0)))
        return [(pts, CYAN)]

    @staticmethod
    def _constellation_series(mode):
        """星座点(理想位置)。**是映射器层的参考星座, 不是 RRC 之后的接收星座。**

        每个点画成一个小方块(两条横线夹出的短线段)—— `polyline` 画不了单个点,
        而零长度线段太小看不见。
        """
        levels = {"BPSK": 1, "QPSK": 2, "QAM16": 4, "QAM64": 8}.get(mode, 2)
        if mode == "BPSK":
            pts = [(-1.0, 0.0), (1.0, 0.0)]
        else:
            pts = [(2.0 * i / (levels - 1) - 1.0, 2.0 * j / (levels - 1) - 1.0)
                   for i in range(levels) for j in range(levels)]
        d = 0.045            # 点的大小(归一化单位) —— 太小看不见, 太大挤在一起
        out = []
        for x, y in pts:
            out.append(([(x - d, y - d), (x + d, y - d), (x + d, y + d),
                         (x - d, y + d), (x - d, y - d)], CYAN))
        return out
        """状态页模式下只叠顶栏与导航(让用户切得回去)。"""
        for w in self.ui.widgets:
            if isinstance(w, (Header, NavButton, Footer)):
                w.draw(self.ui.canvas)
        self.ui.lcd.flush()
        self.ui.frames += 1

    def _draw_page(self, page):
        """画工作区: 标题 + 两栏卡片 + 该页的静态内容。

        动态内容(数值行/表格/预览/按钮选中态)由**控件自己**在 `draw()` 里按 `app.draft`
        画 —— 所以这里先 `_sync_page()` 把控件状态从草稿刷一遍, 再交给帧循环统一绘制。
        """
        if page == M.PAGE_OVERVIEW and self.status_screen is not None:
            return
        self._static_labels = []
        title, sub = self._titles.get(page, ("", ""))
        x0, cy, w, h = self._work_area(title, sub)
        c = self.ui.canvas
        if page == M.PAGE_OVERVIEW:
            self._draw_overview(x0, cy, w, h)
        elif page == M.PAGE_AI:
            self._draw_ai(x0, cy, w, h)
        elif page == M.PAGE_TOOLS:
            self._draw_tools(x0, cy, w, h)
        elif page == M.PAGE_AWG:
            self._draw_tx(x0, cy, w, h)
        elif page == M.PAGE_FUTURE:
            self._draw_future(x0, cy, w, h)
        elif page == M.PAGE_SYSTEM:
            self._draw_system(x0, cy, w, h)
        elif page == M.PAGE_DDR:
            (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(page)
            Panel(lx, ly, lw, lh, fill=PANEL, edge=LINE).draw(c)
            Panel(rx, ry, rw, rh, fill=PANEL, edge=LINE).draw(c)
            self._draw_ddr_static(c, lx, ly, lw, lh, rx, ry, rw, rh)
        else:
            (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(page)
            Panel(lx, ly, lw, lh, fill=PANEL, edge=LINE).draw(c)
            Panel(rx, ry, rw, rh, fill=PANEL, edge=LINE).draw(c)
            self._draw_page_static(page, c, lx, ly, lw, lh, rx, ry, rw, rh)

    def _t(self, c, x, y, text, color, size):
        """画一行静态文字, 并记下它的矩形(给"标签别压在控件上"那条测试用)。

        ⚠ 静态文字是**先于控件**画的, 控件后画就把压在它下面的字盖掉一半。本项目有三次
          版式错误只在渲成 PNG 时才看出来 —— 记下矩形就能用断言钉住, 不必靠眼睛。
        """
        w, h = text_size(text, size)
        self._static_labels.append((x, y, w, h, text))
        c.text(x, y, text, color, size)

    def _draw_page_static(self, page, c, lx, ly, lw, lh, rx, ry, rw, rh):
        """每页固定不变的文字/说明(值会变的部分都由控件画)。

        ⚠ 标签要放在**控件之间**的空隙里。第一版把"波形"画在按钮上、"参考星座"画在预览框
          的角上, 都叠在一起了 —— 靠渲 PNG 才看出来。
        """
        if page == M.PAGE_AWG:
            self._sync_tx()
        elif page == M.PAGE_DIGITAL:
            self._t(c, lx + 14, ly + 5, "模式", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 122, "成形滤波 / 滚降", MUTED, MIN_CJK_SIZE)
            if self.app.selected_device == "AD9361":
                dig = self.app.rf_digital_values()
                filt_desc = "4 SPS" if dig["filter"] == "RRC" else "RECT"
                lo, hi = self.app.rf_digital_symrate_bounds()
                fs = self.app._rf_applied_parameters.get("sample_rate_hz")
                sym = dig.get("symbol_rate_hz", dig.get("symrate_hz"))
                frame = ""
                if fs and sym and fs % sym == 0:
                    frame = "帧%d" % (127 * (fs // sym))
                info = "%s %s" % (dig["mode"], filt_desc)
                if frame:
                    info += " | " + frame
                info += " | %s~%s Sym/s" % (_hz(lo), _hz(hi))
                self._t(c, rx + 14, ry + rh - 80, info, MUTED, 13)
            else:
                bits = {"BPSK": 1, "QPSK": 2, "QAM16": 4, "QAM64": 6}.get(
                    self.app.draft["dig"]["mode"], 2)
                self._t(c, rx + 14, ry + rh - 62, "每符号比特数  %d" % bits, TEXT, 15)
                self._t(c, rx + 14, ry + rh - 40,
                        "成形  %s" % ("4 SPS" if self.app.draft["dig"]["filter"] == "RRC"
                                      else "RECT"), TEXT, 15)
        elif page == M.PAGE_CHANNEL:
            self._t(c, lx + 14, ly + 17, "通道", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 208, "标准差 = 等级 / 100 x 127", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 228, "噪声等级不等于 SNR", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 248, "延迟步进 20 ns, 最大 10.22 us", MUTED, MIN_CJK_SIZE)
            self._t(c, rx + 14, ry + 6, "4 抽头 | 点格子编辑", MUTED, MIN_CJK_SIZE)
            self._t(c, rx + 14, ry + rh - 36, "实数 FIR | 应用时执行 COMMIT", MUTED, MIN_CJK_SIZE)

    def _draw_overview(self, x0, cy, w, h):
        """总览页的只读 TX/RX 摘要；没有真实 RX 服务时只显示能力门禁。"""
        c = self.ui.canvas
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(M.PAGE_OVERVIEW)
        Panel(lx, ly, lw, lh, fill=PANEL, edge=LINE).draw(c)
        Panel(rx, ry, rw, rh, fill=PANEL, edge=LINE).draw(c)
        app = self.app
        snap = app.rx_snapshot
        caps = app.capabilities
        self._t(c, lx + 14, ly + 10, "发射状态", MUTED, MIN_CJK_SIZE)
        tx_rows = (("数字调制", app.actual_mode),
                   ("发射草稿", app.draft["dig"]["mode"]),
                   ("连接", "已连接" if app.connected else "未连接"),
                   ("状态", "有效" if app.fresh else "已过期"),
                   ("事务", "进行中" if app.pending else "空闲"))
        for i, (label, value) in enumerate(tx_rows):
            y = ly + 42 + i * 34
            self._t(c, lx + 14, y, label, MUTED, MIN_CJK_SIZE)
            tw = text_size(str(value), MIN_CJK_SIZE)[0]
            self._t(c, lx + lw - 14 - tw, y, str(value),
                    CYAN if value in ("已连接", "有效") else TEXT, MIN_CJK_SIZE)
        self._t(c, lx + 14, ly + lh - 34,
                "参数必须经 AwgCtl 应用并回读确认", MUTED, MIN_CJK_SIZE)

        self._t(c, rx + 14, ry + 10, "接收状态", MUTED, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + 42, snap.state, CYAN if snap.state == "DONE" else MUTED, 20)
        rx_rows = (("采集", "已接入" if caps.supports("capture") else "未接入"),
                   ("同步", "已接入" if caps.supports("sync") else "未接入"),
                   ("帧数据", "已接入" if caps.supports("frames") else "未接入"),
                   ("指标", "已接入" if caps.supports("ber") else "未接入"))
        for i, (label, value) in enumerate(rx_rows):
            y = ry + 84 + i * 34
            self._t(c, rx + 14, y, label, MUTED, MIN_CJK_SIZE)
            tw = text_size(value, MIN_CJK_SIZE)[0]
            self._t(c, rx + rw - 14 - tw, y, value,
                    CYAN if value == "已接入" else MUTED, MIN_CJK_SIZE)
        cap_note = snap.error or caps.reason or "采集能力未接入"
        self._t(c, rx + 14, ry + rh - 34,
                ("采集: %s" % cap_note)[:28],
                ORANGE if snap.error else MUTED, MIN_CJK_SIZE)

    def _draw_tools(self, x0, cy, w, h):
        """工具容器页只提供进入既有事务页的入口。"""
        c = self.ui.canvas
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(M.PAGE_TOOLS)
        Panel(lx, ly, lw, lh, fill=PANEL, edge=LINE).draw(c)
        Panel(rx, ry, rw, rh, fill=PANEL, edge=LINE).draw(c)
        self._t(c, lx + 14, ly + 10, "既有控制工具", MUTED, MIN_CJK_SIZE)
        self._t(c, lx + 14, ly + 174, "所有下发仍走原有事务模型", MUTED, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + 10, "工具说明", MUTED, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + 48, "数字调制", TEXT, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + 76, "模式、符号率、RRC 和 PRBS7", MUTED, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + 122, "信道损伤", TEXT, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + 150, "抽头、噪声、单音与冲激", MUTED, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + rh - 30, "不绕过 AwgCtl / RingThread / DdrPp", MUTED, MIN_CJK_SIZE)

    def _draw_ai(self, x0, cy, w, h):
        """智能页只读显示真实 recommendation/capabilities。"""
        c = self.ui.canvas
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(M.PAGE_AI)
        Panel(lx, ly, lw, lh, fill=PANEL, edge=LINE).draw(c)
        Panel(rx, ry, rw, rh, fill=PANEL, edge=LINE).draw(c)
        app = self.app
        rec = app.recommendation
        caps = app.capabilities
        self._draw_model_prediction(c, lx, ly, lw)

        self._t(c, rx + 14, ry + 10, "能力门禁", MUTED, MIN_CJK_SIZE)
        for i, (label, key) in enumerate((("采集", "capture"), ("生产建议", "model"),
                                           ("同步", "sync"), ("帧数据", "frames"),
                                           ("导出", "export"))):
            value = "已接入" if caps.supports(key) else "未接入"
            y = ry + 42 + i * 34
            self._t(c, rx + 14, y, label, MUTED, MIN_CJK_SIZE)
            tw = text_size(value, MIN_CJK_SIZE)[0]
            self._t(c, rx + rw - 14 - tw, y, value,
                    CYAN if value == "已接入" else MUTED, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + rh - 30, (caps.reason or "N/A")[:28], MUTED, MIN_CJK_SIZE)

    def _draw_model_prediction(self, c, x, y, width, below_tabs=False):
        """Scalar display of a frozen handoff; no inference or array conversion."""
        display = self.app.rx_model_display()
        self._t(c, x + 14, y + (42 if below_tabs else 10), "实测 IQ 分类", MUTED, MIN_CJK_SIZE)
        self._t(c, x + 14, y + (66 if below_tabs else 36), display["label"], TEXT, 20)
        score = "N/A" if display["score"] is None else "%.3f" % display["score"]
        offsets = (96, 120, 144, 168, 194) if below_tabs else (70, 96, 122, 148, 178)
        for offset, text in zip(offsets, ("模型得分  " + score,
                                         "来源  " + display["source"],
                                         "版本  " + display["version"],
                                         display["state"],
                                         "仅填本地草稿; AUTO 永久关闭")):
            self._t(c, x + 14, y + offset,
                    self._clip(text, width - 28, MIN_CJK_SIZE), MUTED, MIN_CJK_SIZE)

    @staticmethod
    def _clip(text, width, size):
        text = str(text)
        while text and text_size(text, size)[0] > width:
            text = text[:-1]
        return text

    def _draw_tx(self, x0, cy, w, h):
        c = self.ui.canvas
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(M.PAGE_AWG)
        Panel(lx, ly, lw, lh, fill=PANEL, edge=LINE).draw(c)
        Panel(rx, ry, rw, rh, fill=PANEL, edge=LINE).draw(c)
        app = self.app
        sub = app.tx_subpage if app.tx_subpage in M.TX_SUBPAGES else "awg"
        if sub == "awg":
            self._draw_page_static(M.PAGE_AWG, c, lx, ly, lw, lh, rx, ry, rw, rh)
        elif sub == "parameters":
            self._t(c, lx + 14, ly + 232, "PHY: 0=2R2T / 1=1R1T", MUTED, 14)
            self._t(c, lx + 14, ly + 284, "草稿不自动应用到硬件", MUTED, MIN_CJK_SIZE)
            self._t(c, rx + 14, ry + 10, "初始化 / 配置", TEXT, 16)
            self._t(c, rx + 14, ry + 232, "模式切换需重新初始化", MUTED, MIN_CJK_SIZE)
            message, message_color = _rf_control_line(app.rf_control)
            self._t(c, rx + 14, ry + 284, self._clip(message, rw - 28, MIN_CJK_SIZE),
                    message_color, MIN_CJK_SIZE)
        elif sub == "transmit":
            self._t(c, lx + 14, ly + 330, "普通=频率/幅度  高级=周期/码值", MUTED, 14)
            if app.rf_transmitting():
                status = "正在发射"
            elif (app.rf_control.get("tx_loaded") or app.rf_control.get("state") == "loaded"
                  or "start" in app.rf_control.get("actions", ())):
                status = "已加载 / 待发射"
            else:
                status = "未发射 / 待加载"
            self._t(c, rx + 14, ry + 52, status, CYAN if app.rf_transmitting() else TEXT, 20)
            tone = app.rf_tone_frequency_hz()
            self._t(c, rx + 14, ry + 90, "基带: " + (_hz(tone) + "Hz" if tone is not None else "未配置"), TEXT, 16)
            applied = app._rf_applied_parameters
            self._t(c, rx + 14, ry + 122, "已应用 LO: " + (_hz(applied["lo_hz"]) + "Hz" if "lo_hz" in applied else "未配置"), MUTED, 15)
            self._t(c, rx + 14, ry + 156, "30秒无控制操作后自动停机", ORANGE, 15)
            self._t(c, rx + 14, ry + 184, "状态读取不延长发射期限", MUTED, 15)
            self._t(c, rx + 14, ry + 214, "停止无需再次确认", MUTED, 15)
            message, color = _rf_control_line(app.rf_control)
            self._t(c, rx + 14, ry + 284, self._clip(message, rw - 28, MIN_CJK_SIZE), color, MIN_CJK_SIZE)

    def _draw_future(self, x0, cy, w, h):
        c = self.ui.canvas
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(M.PAGE_FUTURE)
        Panel(lx, ly, lw, lh, fill=PANEL, edge=LINE).draw(c)
        Panel(rx, ry, rw, rh, fill=PANEL, edge=LINE).draw(c)
        app = self.app
        snap = app.rx_snapshot
        caps = app.capabilities
        capture = app.rx_capture if isinstance(getattr(app, "rx_capture", None), dict) else {}
        # One terminal-capture predicate for BOTH subpages: a cancelled / failed / refused
        # capture has no landed result, so its reason is the only thing there is to show.
        terminal = capture.get("state") in (M.RX_CAPTURE_CANCELLED, M.RX_CAPTURE_ERROR,
                                            M.RX_CAPTURE_REFUSED)

        def value(value, fallback="N/A"):
            return fallback if value is None or value == "" else str(value)

        def row(x, y, width, label, text, color=TEXT):
            self._t(c, x, y, label, MUTED, MIN_CJK_SIZE)
            tw = text_size(text, MIN_CJK_SIZE)[0]
            self._t(c, x + width - tw, y, text, color, MIN_CJK_SIZE)

        sub = app.rx_subpage
        if sub == "overview":
            # 阶段 1 这一页画的是**只读链路**: daemon 可达性 + 逻辑生命周期 + PL 寄存器快照。
            # 数值全部来自 `rf_status`(主循环从后台 worker 取的最新不可变快照), 界面自己
            # 不碰 socket、不碰 RF 寄存器。
            rf = app.rf_status if isinstance(app.rf_status, dict) else {}
            pl = rf.get("pl_status") if isinstance(rf.get("pl_status"), dict) else {}
            tx = pl.get("tx") if isinstance(pl.get("tx"), dict) else {}
            rx_reg = pl.get("rx") if isinstance(pl.get("rx"), dict) else {}
            # 在线只证明"收到了 daemon 回复"; 过期 = 数值已被 `ui_model` 清成未知。
            online = rf.get("online") is True
            stale = rf.get("stale") is True
            known = online and not stale
            readable = known and pl.get("io_ok") is True
            if not online:
                link, link_color = "离线", ORANGE
            elif stale:
                link, link_color = "在线 / 过期", ORANGE
            else:
                link, link_color = "在线 / 新鲜", CYAN

            def flag(value):
                return "N/A" if value is None else ("是" if value else "否")

            def count(value):
                return "N/A" if value is None else str(value)

            # ---- 左栏: 采集状态 + daemon 链路(状态 1) + 武装(状态 2) ----
            self._t(c, lx + 14, ly + 44, "采集状态", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 64, value(snap.state),
                    CYAN if snap.state == "DONE" else ORANGE if snap.state == "ERROR" else TEXT, 20)
            # 能力缺席时这行**明确说没有真实采集**: daemon 在线 / hardware_verified 都
            # 不能画成"测量通过"(设计 §4/§6)。
            if terminal and capture.get("reason"):
                # 取消/失败/被拒**照实画原因**: 没有落地的采集不该被画成"等待真实采集".
                capture_note, capture_color = (capture["reason"], ORANGE)
            elif not caps.capture:
                capture_note, capture_color = ("未接入真实采集, 不报告测量结果", ORANGE)
            elif snap.state in ("DONE", "ERROR"):
                capture_note, capture_color = (snap.error or "",
                                               ORANGE if snap.state == "ERROR" else MUTED)
            elif snap.state == "UNAVAILABLE":
                capture_note, capture_color = ("等待真实采集", MUTED)
            else:
                capture_note, capture_color = (app.rx_status_message, MUTED)
            self._t(c, lx + 14, ly + 92,
                    self._clip(capture_note, lw - 28, MIN_CJK_SIZE),
                    capture_color, MIN_CJK_SIZE)

            self._t(c, lx + 14, ly + 114, "RF 链路", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 136, link, link_color, 20)
            row(lx + 14, ly + 164, lw - 28, "生命周期", value(rf.get("daemon_state")))
            # 状态 2: daemon 还武装着没有。链路不新鲜时它无法证实, 所以画 N/A, 不猜。
            armed = rf.get("armed") if known else None
            row(lx + 14, ly + 184, lw - 28, "RF 武装",
                "N/A" if armed is None else ("已武装" if armed else "未武装"),
                CYAN if armed is True else MUTED)
            row(lx + 14, ly + 204, lw - 28, "PL 寄存器",
                "可读" if readable else "不可读", CYAN if readable else ORANGE)
            # ⚠ 阶段 1 不读 AD9361 SPI, 所以芯片状态**恒为未知** —— 不能因为 daemon 在线
            #   就写"就绪"(设计 §4/§6: 读 PL 寄存器 != AD9361 初始化通过)。
            row(lx + 14, ly + 224, lw - 28, "AD9361", "未知", MUTED)

            # ---- 右栏: 有实测 IQ 时先画**接收分析**(质量), 没有才画 PL 寄存器快照 ----
            ana = app.rx_analysis_summary if isinstance(app.rx_analysis_summary, dict) else None
            if ana is not None:
                def dbfs(key):
                    level = ana.get(key)
                    return "N/A" if level is None else "%.1f dBFS" % level

                def tone(key):
                    hertz = ana.get(key)
                    return "N/A" if hertz is None else "%sHz" % _hz(abs(int(hertz)))

                self._t(c, rx + 14, ry + 10, "接收分析", MUTED, MIN_CJK_SIZE)
                row(rx + 14, ry + 36, rw - 28, "I / Q RMS",
                    "%s / %s" % (dbfs("i_dbfs"), dbfs("q_dbfs")))
                row(rx + 14, ry + 60, rw - 28, "复数 RMS", dbfs("complex_dbfs"))
                row(rx + 14, ry + 84, rw - 28, "输出削顶",
                    "是 (%d)" % ana.get("clipping_count", 0)
                    if ana.get("output_clipped") else "否（不代表前端未压缩）",
                    ORANGE if ana.get("output_clipped") else MUTED)
                row(rx + 14, ry + 108, rw - 28, "主峰", tone("peak_hz"))
                # 口径必须写清楚: 它是**分析带内的 99% 功率包含度**, 不是信号带宽。
                row(rx + 14, ry + 132, rw - 28, "99% 占用带宽",
                    tone("occupied_bandwidth_hz"))
                row(rx + 14, ry + 156, rw - 28, "分析采样率",
                    "N/A" if ana.get("analysis_fs_hz") is None
                    else "%sHz (D=%d)" % (_hz(int(ana["analysis_fs_hz"])),
                                          ana.get("decimation", 1)))
                row(rx + 14, ry + 180, rw - 28, "谱窗",
                    value(ana.get("spectrum_window"), "无频谱"))
                row(rx + 14, ry + 204, rw - 28, "口径",
                    "99% 占用带宽=分析带内包含度")
            else:
                self._t(c, rx + 14, ry + 10, "PL 快照", MUTED, MIN_CJK_SIZE)
                row(rx + 14, ry + 36, rw - 28, "TX 选择", flag(tx.get("source_selected")))
                row(rx + 14, ry + 60, rw - 28, "TX 长度", count(tx.get("length_words")))
                row(rx + 14, ry + 84, rw - 28, "TX 位置", count(tx.get("position_words")))
                row(rx + 14, ry + 108, rw - 28, "RX 布防", flag(rx_reg.get("armed")))
                row(rx + 14, ry + 132, rw - 28, "RX 完成", flag(rx_reg.get("done")))
                row(rx + 14, ry + 156, rw - 28, "RX 位置", count(rx_reg.get("position_words")))
                row(rx + 14, ry + 180, rw - 28, "采样时刻",
                    count(pl.get("sampled_at_monotonic_ns")))
                row(rx + 14, ry + 204, rw - 28, "错误",
                    value(rf.get("error"), "无错误")[:28],
                    ORANGE if rf.get("error") else MUTED)
            # 状态 4: RX 采集是**能力**(AD9361 采集路径), 不是控制失败。已接入/未接入
            #   和原因只来自 `capabilities`, 与链路/武装/控制结果互不冒充。
            cap_line = "采集能力: %s" % ("已接入" if caps.capture else "未接入")
            if not caps.capture and caps.reason:
                cap_line += " (%s)" % caps.reason
            self._t(c, rx + 14, ry + 226,
                    self._clip(cap_line, rw - 28, MIN_CJK_SIZE),
                    CYAN if caps.capture else MUTED, MIN_CJK_SIZE)
            # 状态 3: 控制结果 / 门禁原因。`_rf_control_line` 只转写 daemon 的码、本地
            #   门禁原因或当前控制状态, 从不发明"成功"。
            # 质量原因**必须看得见**: 过载(码值到轨)与过期不是被悄悄丢掉的记录。
            for index, reason in enumerate(M.rx_quality_reasons(app)[:2]):
                self._t(c, rx + 14, ry + 246 + index * 16,
                        self._clip(reason["text"], rw - 28, MIN_CJK_SIZE),
                        ORANGE if reason["severe"] else MUTED, MIN_CJK_SIZE)
            control = app.rf_control if isinstance(getattr(app, "rf_control", None), dict) else {}
            ctl_line, ctl_color = _rf_control_line(control)
            # 中英混排的**行盒是两套字体里高的那个**(19px,而非纯中文的 14px)。
            # 写死 ry+284 时它的行盒超出面板下沿(需要 399, 面板到 397); 而两行质量原因
            # 各自 18px 间距时, 面板底部已经没有位置放得下它。所以质量行收到 16px 间距,
            # 控制行放在 ry+278: 四行都在面板内, 且各自的墨迹不重叠。
            self._t(c, rx + 14, ry + 278, self._clip(ctl_line, rw - 28, MIN_CJK_SIZE),
                    ctl_color, MIN_CJK_SIZE)
        elif sub == "sampling":
            self._t(c, lx + 14, ly + 10, "采样配置", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 34, "输入 60 MSPS", TEXT, 16)
            self._t(c, lx + 14, ly + 218, "D 为输入到输出的抽取比", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 236, "rx_len 是抽取后的复样点数", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 254, "格式: %s" % caps.sample_format,
                    MUTED, MIN_CJK_SIZE)
            # 口径必须写在页面上: 它是**分析带内的 99% 功率包含度**, 不是信号带宽。
            self._t(c, lx + 14, ly + 274, "99% 占用带宽是分析带内的包含度",
                    MUTED, MIN_CJK_SIZE)
            self._t(c, rx + 14, ry + 10, "最近快照", MUTED, MIN_CJK_SIZE)
            row(rx + 14, ry + 38, rw - 28, "输入采样率", "%d Hz" % snap.input_fs_hz)
            row(rx + 14, ry + 62, rw - 28, "抽取比", "D=%d" % snap.decim)
            row(rx + 14, ry + 86, rw - 28, "输出采样率", "%d Hz" % snap.output_fs_hz)
            row(rx + 14, ry + 110, rw - 28, "请求点数", str(snap.rx_len))
            row(rx + 14, ry + 134, rw - 28, "实际点数", str(snap.actual_len))
            row(rx + 14, ry + 158, rw - 28, "滤波配置", value(snap.filter_profile))
            row(rx + 14, ry + 182, rw - 28, "溢出", "是" if snap.overflow else "否",
                ORANGE if snap.overflow else TEXT)
            # 口径与状态分开说: 99% 占用带宽是**分析带内的功率包含度**, 不是信号带宽;
            # 也**不**从码值声称信噪比或绝对电平。
            note = snap.error or ("快照 %s" % snap.capture_id if snap.capture_id
                                  else "未收到真实快照")
            if snap.state != "DONE" and capture.get("reason"):
                note = capture["reason"]
            self._t(c, rx + 14, ry + 274,
                    self._clip(note, rw - 28, MIN_CJK_SIZE),
                    ORANGE if (snap.error or terminal) else MUTED, MIN_CJK_SIZE)
        elif sub == "demod":
            demod = app.rx_demod_summary()
            current = demod.get('current', False)
            self._t(c, lx + 14, ly + 10, '解调设置', MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 270, '3M RECT / D1 | 概览捕获', MUTED, MIN_CJK_SIZE)
            self._t(c, rx + 14, ry + 10, '本帧解调', MUTED, MIN_CJK_SIZE)
            def synced(key):
                if not current:
                    return '--'
                v = demod.get(key)
                return '未提供' if v is None else ('已同步' if v else '未同步')
            capture_id = demod.get('capture_id')
            ber = demod.get('ber') if current else None
            errors, bits = demod.get('bit_errors'), demod.get('compared_bits')
            cfo = demod.get('cfo_hz') if current else None
            is_rrc = (demod.get('engine') == 'RrcEnvelope'
                      or demod.get('valid_symbol_start') is not None)
            rows = (
                ('引擎', '已接入' if demod.get('available') else '未接入'),
                ('快照', str(capture_id)[-16:] if current and capture_id else '--'),
                ('载波', synced('carrier_sync')),
                ('定时', synced('timing_sync')),
                ('参考', synced('reference_sync')),
                ('频偏', ('%.1f Hz' % cfo) if cfo is not None else '--'),
                ('错误/比特', '%d/%d' % (errors, bits) if ber is not None else '--'),
                ('BER', '%.6f' % ber if ber is not None else '--'),
            )
            for i, (label, val) in enumerate(rows):
                row(rx + 14, ry + 36 + i * 24, rw - 28, label, val,
                    CYAN if val not in ('--', '未接入', '未同步', '未提供') else MUTED)
            if current and is_rrc:
                preview = demod.get('symbol_preview') or ()
                bits = demod.get('bit_preview') or ()
                sample = ('%.0f,%.0f' % preview[0]) if preview else '--'
                bit_text = ''.join(str(b) for b in bits[:6]) or '--'
                row(rx + 14, ry + 228, rw - 28, '预览', '%s | B:%s' % (sample, bit_text))
            reasons = {'UNSUPPORTED': '不支持此配置', 'CLIPPED': 'IQ削顶',
                'NO_ENERGY': '能量不足', 'LOW_ENERGY': '能量不足',
                'REFERENCE_NOT_PROVIDED': '无参考: 比特极性未确定',
                'REFERENCE_NOT_SYNCED': '参考未同步', 'NO_REFERENCE': '无参考: 未给BER',
                'REFERENCE_NOT_FOUND': '参考未找到', 'CARRIER_NOT_SYNCED': '载波未同步',
                'TIMING_NOT_SYNCED': '定时未同步', 'CFO_OUT_OF_RANGE': '频偏超出支持域',
                'INPUT_DOMAIN': '输入域不支持', 'NOT_RUN': '本帧未运行',
                'RRC_CLASSIFICATION_UNAVAILABLE': 'RRC分类不可用',
                'DEMOD_FAILED': '本次解调失败', 'RELEASE_MISMATCH': '发布身份不一致'}
            reason = demod.get('reason') or ('本帧有效' if current else '等待新快照')
            reason_text = reasons.get(reason, reason)
            if current and is_rrc:
                reason_text = reason_text + ' / EVM不可用'
            reason_y = ry + (250 if is_rrc else 238)
            self._t(c, rx + 14, reason_y,
                    self._clip(reason_text, rw - 28, MIN_CJK_SIZE),
                    CYAN if ber is not None else ORANGE, MIN_CJK_SIZE)
            if current:
                preview = demod.get('symbol_preview') or ()
                bits = demod.get('bit_preview') or ()
                if preview:
                    i, q = preview[0]
                    sample = '%.0f,%.0f' % (i, q)
                else:
                    sample = '--'
                bits_text = ''.join(str(b) for b in bits[:12]) or '--'
                if is_rrc:
                    footer = ('有效%s[%s,%s) 比较%s-%s d%s t%s'
                              % (demod.get('valid_symbols', '--'),
                                 demod.get('valid_symbol_start', '--'),
                                 demod.get('valid_symbol_end', '--'),
                                 demod.get('comparison_start_symbol', '--'),
                                 demod.get('comparison_end_symbol', '--'),
                                 demod.get('group_delay_samples', '--'),
                                 demod.get('transition_symbols', '--')))
                else:
                    footer = ('符号预览 [%s] / 比特预览 %s / EVM不可用'
                              % (sample, bits_text))
            else:
                footer = '快照估计 / CRC、帧、EVM不可用'
            footer_y = ry + (274 if is_rrc else 266)
            self._t(c, rx + 14, footer_y, self._clip(footer, rw - 28, MIN_CJK_SIZE),
                    MUTED, MIN_CJK_SIZE)
        elif sub == 'constellation':
            measured=app.rx_constellation_summary(); demod=app.rx_demod_summary()
            self._t(c,lx+14,ly+216,self._clip(app.teaching_message or
                app.teaching_status['reason'],lw-28,MIN_CJK_SIZE),MUTED,MIN_CJK_SIZE)
            saved=app.rx_save_status
            text={'saving':'正在保存','saved':'历史记录已保存','error':'保存失败'}.get(saved.get('state'),'本地保存 / 不上传')
            self._t(c,lx+14,ly+238,self._clip(text+(' '+saved.get('reason','') if saved.get('reason') else ''),
                lw-28,MIN_CJK_SIZE),ORANGE if saved.get('state')=='error' else MUTED,MIN_CJK_SIZE)
            self._t(c,rx+14,ry+10,'当前快照 / 符号RMS归一化',MUTED,MIN_CJK_SIZE)
            row(rx+14,ry+210,rw-28,'快照',str(measured.get('capture_id') or '--')[-23:])
            row(rx+14,ry+232,rw-28,'显示点 / 有效符号','%d / %d' %
                (measured['point_count'],measured['symbol_count']))
            errors,bits=demod.get('bit_errors'),demod.get('compared_bits')
            row(rx+14,ry+254,rw-28,'错误 / 比特','%d / %d' % (errors,bits)
                if demod.get('ber') is not None else '-- / --')
            reason=measured['reason'] or ('载波/定时已同步 | 参考'+
                ('关闭' if demod.get('reference_sync') is None else
                 '已同步' if demod.get('reference_sync') else '未同步')
                if measured['current'] else '等待真实快照')
            self._t(c,rx+14,ry+278,self._clip(reason,rw-28,MIN_CJK_SIZE),MUTED,MIN_CJK_SIZE)
        elif sub == 'environment':
            measured=app.rx_environment_summary(); features=measured['features']
            saved=app.rx_save_status
            save_text={'saving':'正在保存','saved':'实验已保存','error':'保存失败'}.get(saved.get('state'),'')
            feedback=(save_text+' '+str(saved.get('reason') or '') if saved.get('state')=='error'
                else app.rf_control.get('last_error') or save_text or app.teaching_message)
            self._t(c,lx+14,ly+232,self._clip(feedback or '请选择场景并确认加载',lw-28,MIN_CJK_SIZE),
                ORANGE if saved.get('state')=='error' or app.rf_control.get('last_error') else MUTED,
                MIN_CJK_SIZE)
            labels={'CLEAR':'窗口未检出','NARROWBAND':'窄带异常','PULSE':'脉冲异常',
                    'UNKNOWN':'不确定','INVALID':'无效窗口'}
            self._t(c,rx+14,ry+10,'接收证据 / 有限窗口诊断',MUTED,MIN_CJK_SIZE)
            self._t(c,rx+14,ry+38,labels.get(measured['label'],'等待新快照'),
                CYAN if measured['label']=='CLEAR' else ORANGE,20)
            row(rx+14,ry+70,rw-28,'快照',str(measured['capture_id'] or '--')[-23:])
            for i,(label,key,fmt) in enumerate((('观察窗','window_us','%.2f us'),
                ('峰值比','crest_ratio','%.2f'),('窄带集中度','tone_concentration','%.3f'),
                ('基线超额','tone_excess','%.3f'),('异常点比例','pulse_sample_fraction','%.3f'))):
                value=features.get(key)
                row(rx+14,ry+94+i*23,rw-28,label,fmt%value if value is not None else '--')
            self._t(c,rx+14,ry+216,self._clip(measured['reason'],rw-28,MIN_CJK_SIZE),ORANGE,MIN_CJK_SIZE)
            truth=app._rx_record_scenario or {}
            text='TX实验声明: '+str(truth.get('truth','未记录'))
            self._t(c,rx+14,ry+241,self._clip(text,rw-28,MIN_CJK_SIZE),MUTED,MIN_CJK_SIZE)
            self._t(c,rx+14,ry+269,'仅本次窗口 / 不代表持续状态',MUTED,MIN_CJK_SIZE)
        else:
            rec = app.recommendation
            self._draw_model_prediction(c, lx, ly, lw, below_tabs=True)
            self._t(c, rx + 14, ry + 10, "能力", MUTED, MIN_CJK_SIZE)
            for i, (label, key) in enumerate((("采集", "capture"), ("同步", "sync"),
                                               ("解调", "demod"), ("帧数据", "frames"),
                                               ("生产建议", "model"))):
                row(rx + 14, ry + 36 + i * 24, rw - 28, label,
                    "已接入" if caps.supports(key) else "未接入",
                    CYAN if caps.supports(key) else MUTED)
            self._t(c, rx + 14, ry + rh - 30,
                    "建议" if rec.actionable else "真实数据和能力未满足",
                    CYAN if rec.actionable else MUTED, MIN_CJK_SIZE)

    def _draw_ddr_static(self, c, lx, ly, lw, lh, rx, ry, rw, rh):
        """数据页固定不变的文字。**所有会变的值都由控件画**(见 `_sync_ddr`)。

        左栏的状态行是静态文字 —— 值会变, 所以用 `_t` 直接画而不是 `Label`(Label 的
        box 在构造期固化, 改 `.text` 不重算)。它们在 `_draw_page` 里每帧随签名重画。
        """
        app = self.app
        pp = app.pp or {}
        # 右栏那两行提示与 DDR 无关 —— 本地文件表在环形线程缺席时**照样能用**(列目录是
        # 纯本地 IO)。所以要画在降级分支的 `return` 之前, 否则降级时右栏是光秃秃的表,
        # 看不出"点行选中""带 * 是队列里的"这两条用法。
        rx_frame_count = len(app.rx_frames)
        rx_export_count = sum(1 for frame in app.rx_frames if frame.exportable)
        self._t(c, rx + 14, ry + 6,
                "本地文件 | RX 帧 %d | 可导出载荷 %d" % (rx_frame_count, rx_export_count),
                MUTED, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + 26,
                "队列中的文件带 * | 环形按序喂非活动 bank", MUTED, MIN_CJK_SIZE)
        if not pp.get("present"):
            self._t(c, lx + 14, ly + 14, "DDR 不可用", ORANGE, 16)
            self._t(c, lx + 14, ly + 42, "位流或驱动未就绪", MUTED, MIN_CJK_SIZE)
            self._t(c, lx + 14, ly + 62, "本地文件仍可浏览", MUTED, MIN_CJK_SIZE)
            return
        rows = (
            ("状态", pp.get("state", "IDLE")),
            ("活动 bank", str(pp.get("active_bank", "NONE"))),
            ("下一 bank", str(pp.get("next_bank", "NONE"))),
            ("B0 / B1 字节", "%d / %d" % (pp.get("bank0_len", 0), pp.get("bank1_len", 0))),
            # `src_bank` 是 PL 侧从 DMA 源地址解出来的 —— 没在播时是 None, 显示成 "-"
            # 比显示 Python 的 `None` 像样(它是给人看的, 不是调试输出)。
            ("硬件源 bank", "-" if pp.get("src_bank") is None else str(pp["src_bank"])),
            ("错误码", "0x%02X" % pp.get("error_code", 0)),
            ("已喂块数", "%d" % pp.get("fed", 0)),
        )
        for i, (k, v) in enumerate(rows):
            y = ly + 14 + i * 21
            self._t(c, lx + 14, y, k, MUTED, MIN_CJK_SIZE)
            vw = text_size(v, MIN_CJK_SIZE)[0]
            self._t(c, lx + lw - 14 - vw, y, v, TEXT, MIN_CJK_SIZE)
        # 左栏底部**只有一行**提示 —— 三件事抢这一行, 按重要性排:
        #   1. 环形线程报的 `last_error`(为什么没播起来);
        #   2. 面板刚做过的动作结果(`app.ddr_status`);
        #   3. 都没消息时, 说明装填为什么是灰的(灰按钮点不出提示, 不写这行用户就只看到
        #      "装填是灰的")。
        # ⚠ 三行**必须共用这一个位置**: 左栏卡片只有 302px 高, 状态行到 ly+154、控制到
        #   ly+260 —— 再开一行就溢出到底栏里了(实测过)。
        err = pp.get("last_error") or app.ddr_status
        if not err:
            err = M.ddr_load_block(app) or "装填后打开调制即可启动"
        self._t(c, lx + 14, ly + lh - 26, err[:30], ORANGE, MIN_CJK_SIZE)

    def _draw_system(self, x0, cy, w, h):
        """系统页: 设备概览 + 命令日志(日志是**本页唯一会变的内容**)。"""
        c = self.ui.canvas
        (lx, ly, lw, lh), (rx, ry, rw, rh) = self._cols(M.PAGE_SYSTEM)
        Panel(lx, ly, lw, lh, fill=PANEL, edge=LINE).draw(c)
        Panel(rx, ry, rw, rh, fill=PANEL, edge=LINE).draw(c)
        app = self.app
        rows = (("显示基准", "800 x 480 / 横屏"),
                ("连接状态", "已连接" if app.connected else "未连接"),
                ("状态新鲜度", "有效" if app.fresh else "已过期"),
                ("数字调制", app.actual_mode),
                ("USER / DDR", "需硬件就绪状态"))
        self._t(c, lx + 14, ly + 8, "设备概览", MUTED, MIN_CJK_SIZE)
        for i, (k, v) in enumerate(rows):
            y = ly + 34 + i * 30
            self._t(c, lx + 14, y, k, MUTED, MIN_CJK_SIZE)
            vw = text_size(v, MIN_CJK_SIZE)[0]
            self._t(c, lx + lw - 14 - vw, y, v, TEXT, MIN_CJK_SIZE)
        self._t(c, rx + 14, ry + 8, "命令预览 | 仅本地记录", MUTED, MIN_CJK_SIZE)
        # 日志: 只画最近若干行(屏上放不下全部), 从底部往上排最新的
        log = app.log[-9:]
        for i, line in enumerate(log):
            self._t(c, rx + 14, ry + 32 + i * 16, line[:52], rgb565(0x86, 0xCA, 0xBC), 11)
        self._t(c, rx + 14, ry + rh - 34,
                "应用只改草稿; 下发后须等应答与回读, 才显示真实生效", MUTED,
                MIN_CJK_SIZE)

    def _signature(self, state):
        app = self.app
        return (app.page, app.selected_device, app.tx_subpage, app.selected_channel, app.connected, app.fresh,
                app.pending, app.dirty, app.actual_mode, app.last_result,
                app.last_message,
                # 按下态也要进签名 —— 否则按下时按钮不变色(内容"没变", 直接早退)
                self.ui.active is not None,
                tuple((c["wave"], c["freq_hz"], c["ampl_pct"], c["duty_pct"])
                      for c in app.draft["awg"]),
                tuple(sorted(app.draft["dig"].items())),
                tuple((c["enabled"], c["noise_pct"],
                       tuple(sorted((t["gain_q8"], t["delay_samples"]) for t in c["taps"])))
                      for c in app.draft["chan"]),
                # 数据页: 文件表/队列/页码/选中/环形开关/状态提示, 以及环形线程的快照。
                # ⚠ 快照必须**整个**进签名 —— 它带着 state/active_bank/error_code 这些
                #   每拍都可能变的值, 漏掉任何一个都会让界面停在旧状态上(而且"内容没变
                #   就不重画"的优化会把它藏起来, 看起来像硬件没动)。
                tuple(app.ddr_files), tuple(app.ddr_queue), app.ddr_page,
                app.ddr_selected, app.ddr_ring, app.ddr_status,
                tuple(sorted((app.pp or {}).items())),
                # RX 服务发布的是不可变契约对象；把所有可见字段纳入签名，避免快照或能力更新
                # 被"内容未变"优化吞掉。
                repr(app.capabilities.to_dict()),
                repr(app.rx_snapshot.to_dict()),
                # RF 链路的只读快照同理: 它带着在线/过期/生命周期这些每拍都可能变的值,
                # 漏掉任何一个都会让面板停在旧读数上。
                repr(app.rf_status), repr(app.rf_control),
                tuple(sorted(app.rf_parameter_values().items())),
                tuple(repr(frame.to_dict()) for frame in app.rx_frames),
                repr(app.rx_metrics.to_dict()),
                repr(app.recommendation.to_dict()),
                repr(app.rx_model_display()),
                repr(app.rx_demod_summary()),
                repr(app.rx_constellation_summary()),repr(app.teaching_status),app.teaching_message,
                repr(app.rx_environment_summary()),repr(app.environment_status),
                app.environment_scene,repr(app._rf_loaded_scenario),
                repr(app.rx_save_status),tuple(repr(x) for x in self.rx_constellation_plot.series),
                # RX 采集控制面与冻结分析: 状态/原因每拍都可能变, 漏掉任何一个都会让
                # 面板停在旧读数或旧按钮上("内容没变就不重画"的优化会把它藏起来)。
                repr(app.rx_capture), repr(app.rx_analysis_summary),
                app.rx_snapshot_is_current(),
                tuple(repr(item) for item in app.rx_history),
                tuple(self.rx_iq_plot.series), tuple(self.rx_spectrum_plot.series),
                app.rx_subpage, tuple(sorted(app.rx_draft.items())),
                app.rx_status_message,
                self.keypad.visible, self.keypad.value, self.keypad.unit, self.keypad.error)
