"""pynq_lcd_ui.py 的离屏测试。

绘图与 UI 最容易错的三处 —— 越界写坏邻行、控件命中判定、按下/抬起沿的合成 —— 都能
在开发机上验, 不必占板子。文字渲染用真 PIL(开发机与板上都有), 但只断言"字形覆盖到的
像素被改了、范围外的没被改", 不断言具体字形, 免得换字体就红。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_pynq  # noqa: E402

fake_pynq.install()

import pynq_lcd  # noqa: E402
import pynq_lcd_ui as ui  # noqa: E402


class FakeLcd:
    """只当画布用: 真的 Lcd 需要板子上的 allocate(), 这里给一块同样几何的数组。"""

    def __init__(self):
        self.array = np.zeros((pynq_lcd.HEIGHT, pynq_lcd.WIDTH), dtype=np.uint16)
        self.flushes = 0

    def flush(self):
        self.flushes += 1


def make_canvas():
    lcd = FakeLcd()
    return ui.Canvas(lcd), lcd


# ---------------- 形状 ----------------

def test_rect_clips_instead_of_writing_the_next_row():
    c, lcd = make_canvas()
    c.rect(-50, -50, 10, 10, 0x1234)
    assert lcd.array[0, 0] == 0x1234
    assert lcd.array[10, 10] == 0x1234
    assert lcd.array[11, 11] == 0            # 边界外一个像素都没动
    assert lcd.array[pynq_lcd.HEIGHT - 1, pynq_lcd.WIDTH - 1] == 0


def test_rect_over_the_far_corner_stays_inside():
    c, lcd = make_canvas()
    c.rect(pynq_lcd.WIDTH - 5, pynq_lcd.HEIGHT - 5, pynq_lcd.WIDTH + 100,
           pynq_lcd.HEIGHT + 100, 0x2222)
    assert lcd.array[pynq_lcd.HEIGHT - 1, pynq_lcd.WIDTH - 1] == 0x2222
    assert lcd.array[pynq_lcd.HEIGHT - 5, pynq_lcd.WIDTH - 6] == 0


def test_rect_border_leaves_the_interior_alone():
    c, lcd = make_canvas()
    c.rect(20, 20, 60, 40, 0x0000)
    c.rect(20, 20, 60, 40, 0xFFFF, fill=False)
    assert lcd.array[20, 20] == 0xFFFF and lcd.array[40, 60] == 0xFFFF
    assert lcd.array[30, 40] == 0x0000          # 内部还是底色


def test_rounded_rect_corners_are_cut_and_edges_are_solid():
    c, lcd = make_canvas()
    c.clear(0x0000)
    c.rounded_rect(100, 100, 199, 159, 0x07FF, radius=16)
    assert lcd.array[100, 100] == 0x0000        # 直角处被切掉
    assert lcd.array[100, 150] == 0x07FF        # 上边中间是实的
    assert lcd.array[130, 100] == 0x07FF        # 左边中间是实的
    assert lcd.array[130, 150] == 0x07FF        # 内部是实的
    assert lcd.array[159, 199] == 0x0000        # 右下直角被切掉


def test_rounded_rect_border_is_not_filled():
    c, lcd = make_canvas()
    c.clear(0x0000)
    c.rounded_rect(50, 50, 149, 109, 0xFFFF, radius=10, fill=False)
    assert lcd.array[50, 100] == 0xFFFF         # 上边
    assert lcd.array[109, 100] == 0xFFFF        # 下边
    assert lcd.array[80, 50] == 0xFFFF          # 左边
    assert lcd.array[80, 99] == 0x0000          # 内部没被填


def test_rounded_rect_clamps_a_radius_that_does_not_fit():
    c, lcd = make_canvas()
    c.clear(0x0000)
    c.rounded_rect(10, 10, 29, 19, 0xF800, radius=999)
    assert lcd.array[10, 10] == 0x0000          # 半径夹到 box 容得下的 5, 直角仍被切
    assert lcd.array[10, 15] == 0xF800          # 上边中间是实的
    assert lcd.array[14, 19] == 0xF800


def test_rounded_rect_with_zero_radius_is_a_plain_rect():
    c, lcd = make_canvas()
    c.rounded_rect(10, 10, 29, 19, 0xF800, radius=0)
    assert lcd.array[10, 10] == 0xF800
    assert lcd.array[19, 29] == 0xF800


def test_circle_is_round_and_clipped():
    c, lcd = make_canvas()
    c.clear(0)
    c.circle(200, 200, 20, 0xFFFF)
    assert lcd.array[200, 220] == 0xFFFF
    assert lcd.array[200, 221] == 0
    assert lcd.array[200 - 20, 200] == 0xFFFF
    assert lcd.array[200 - 21, 200] == 0
    assert lcd.array[200 - 15, 200 + 15] == 0    # 圆角内不算: 15√2 > 20
    c.circle(-10, -10, 30, 0x001F)               # 越界的圆不该炸
    assert lcd.array[0, 0] == 0x001F


def test_bar_fills_proportionally():
    c, lcd = make_canvas()
    c.bar(0, 0, 99, 9, 0.5)
    assert lcd.array[5, 0] == ui.GREY            # 左边框压在填充上
    assert lcd.array[5, 1] == ui.ACCENT
    assert lcd.array[5, 49] == ui.ACCENT
    assert lcd.array[5, 50] == ui.DIM
    assert lcd.array[5, 99] == ui.GREY           # 右边框
    c.bar(0, 0, 99, 9, 0.0)
    assert lcd.array[5, 50] == ui.DIM            # 0 一点都不填
    c.bar(0, 0, 99, 9, 5.0)                      # 大于 1 按 1 处理
    assert lcd.array[5, 98] == ui.ACCENT
    c.bar(0, 0, 99, 9, -1.0)                     # 小于 0 按 0 处理
    assert lcd.array[5, 98] == ui.DIM


# ---------------- 文字 ----------------

def test_text_paints_something_inside_its_box_and_nothing_outside():
    c, lcd = make_canvas()
    c.clear(0)
    c.text(100, 100, "AC820", 0xFFFF, size=24)
    block = lcd.array[100:140, 100:220]
    assert (block == 0xFFFF).any(), "字形一个像素都没画"
    assert lcd.array[99, :].sum() == 0           # 上一行没被碰到
    assert lcd.array[:, 99].sum() == 0           # 左边一列没被碰到


def test_text_clipped_at_the_screen_edge_does_not_wrap():
    c, lcd = make_canvas()
    c.clear(0)
    c.text(pynq_lcd.WIDTH - 30, pynq_lcd.HEIGHT - 12, "WWWW", 0xFFFF, size=20)
    assert (lcd.array[:, :] == 0xFFFF).any()     # 画进去了一部分
    assert lcd.array[0, :].sum() == 0            # 但没有绕回第一行


def test_text_center_puts_the_text_inside_the_box():
    c, lcd = make_canvas()
    c.clear(0)
    c.text_center(0, 0, 399, 99, "OK", 0xFFFF, size=28)
    rows = np.where((lcd.array[:, :200] == 0xFFFF).any(axis=1))[0]
    assert 0 < rows.min() and rows.max() < 99


def test_blit_mask_drops_antialiased_edge_pixels():
    """抗锯齿边缘像素不得被点成实心 —— 否则笔画胖一圈、字内窄缝填死(中文糊成一片)。"""
    array = np.zeros((4, 4), dtype=np.uint16)
    mask = np.array([[0, 1, 127, 128],
                     [200, 255, 0, 64]], dtype=np.uint8)
    ui._blit_mask(array, mask, 0, 0, 0xFFFF)
    # 过半覆盖率的才算字; 1/64/127 这些边缘像素必须留白
    assert array[0, 0] == 0 and array[0, 1] == 0 and array[0, 2] == 0
    assert array[0, 3] == 0xFFFF and array[1, 0] == 0xFFFF and array[1, 1] == 0xFFFF
    assert array[1, 2] == 0 and array[1, 3] == 0


def test_cjk_strokes_are_not_fattened_by_edge_pixels():
    """真字形回归: 画出来的墨量必须**少于**掩膜非零像素数(说明边缘像素被丢掉了)。

    老代码 `> 0` 时两者相等 —— 这条在旧实现上必失败。
    """
    s = "信道损伤"
    mask, _ascent = ui._glyph_mask(s, ui.MIN_CJK_SIZE)
    nonzero = int((mask > 0).sum())
    c, lcd = make_canvas()
    c.clear(0)
    c.text(0, 0, s, 0xFFFF, size=ui.MIN_CJK_SIZE)
    painted = int((lcd.array == 0xFFFF).sum())
    assert painted < nonzero, "边缘像素没被丢掉, 笔画会被撑粗"
    assert painted > 0


# ---------------- 控件与事件 ----------------

def test_button_hit_test_edges():
    b = ui.Button(100, 100, 80, 40, "x")
    assert b.contains(100, 100) and b.contains(179, 139)
    assert not b.contains(99, 100) and not b.contains(100, 140)


def test_tap_inside_calls_back_and_paints_pressed_state():
    lcd = FakeLcd()
    taps = []
    ui_ = ui.Ui(lcd, source=ui.ScriptedInput([(120, 120, True), (120, 120, False), None]))
    btn = ui_.add(ui.Button(100, 100, 80, 40, "点我", on_tap=taps.append))

    ui_.step()                                   # 按下
    assert btn.pressed and taps == []
    assert lcd.array[120, 120] == ui.ACCENT      # 按下态的颜色
    ui_.step()                                   # 抬起
    assert not btn.pressed and taps == [btn]


def test_release_outside_does_not_count_as_a_tap():
    taps = []
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput([(120, 120, True), (500, 400, False)]))
    btn = ui_.add(ui.Button(100, 100, 80, 40, "点我", on_tap=taps.append))
    ui_.step()
    ui_.step()
    assert taps == []
    assert not btn.pressed


def test_touch_outside_every_widget_is_ignored():
    taps = []
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput([(10, 10, True), (10, 10, False)]))
    ui_.add(ui.Button(100, 100, 80, 40, "点我", on_tap=taps.append))
    ui_.step()
    ui_.step()
    assert taps == []


def test_topmost_widget_wins():
    hits = []
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput([(120, 120, True), (120, 120, False)]))
    ui_.add(ui.Button(100, 100, 80, 40, "底", on_tap=lambda b: hits.append("底")))
    ui_.add(ui.Button(110, 110, 80, 40, "上", on_tap=lambda b: hits.append("上")))
    ui_.step()
    ui_.step()
    assert hits == ["上"]


def test_a_held_finger_keeps_dragging_the_same_widget():
    # 按下后手指移出控件再移回, 只要最后在控件内抬起就算点击 —— 拖动时控件要能跟上
    taps = []
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput(
        [(120, 120, True), (500, 500, True), (130, 130, True), (130, 130, False)]))
    btn = ui_.add(ui.Button(100, 100, 80, 40, "点我", on_tap=taps.append))
    for _ in range(4):
        ui_.step()
    assert taps == [btn]
    assert not btn.pressed


def test_a_full_screen_crosshair_does_not_swallow_every_tap():
    """**整屏大小的准星不能吃掉所有点击** —— 板上踩过的真 bug。

    准星的框就是整屏, 而它总是最后加(画在最上层), `_hit` 从顶往下找第一个 `contains`,
    于是每一次点击都落到准星上, 按钮永远收不到按下沿 —— 现象就是"点了没反应"(面板切换
    不跳、控制页按钮全不动)。测试必须走 `step()` 的真实命中路径: 直接调 `on_press` /
    `on_release` 会绕过 `_hit`, 当年就是这么漏掉的。
    """
    taps = []
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput(
        [(690, 32, True), (690, 32, False)]))
    btn = ui_.add(ui.Button(600, 12, 180, 40, "控制页", on_tap=taps.append))
    ui_.add(ui.Crosshair(800, 480))              # 整屏, 且在最上层
    ui_.step()
    ui_.step()
    assert taps == [btn], "按钮必须收到这次点击"
    assert ui_._hit(690, 32) is btn


def test_decoration_widgets_are_not_tappable():
    """标签/进度条/准星只负责画, 不该参与命中 —— 否则它们会挡住底下的按钮。"""
    assert ui.Crosshair(800, 480).tappable is False
    assert ui.Label(0, 0, "x").tappable is False
    assert ui.ProgressBar(0, 0, 10, 10).tappable is False
    assert ui.Button(0, 0, 10, 10, "x").tappable is True


def test_a_label_on_top_of_a_button_still_lets_the_button_be_tapped():
    taps = []
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput([(120, 120, True), (120, 120, False)]))
    btn = ui_.add(ui.Button(100, 100, 80, 40, "点我", on_tap=taps.append))
    ui_.add(ui.Label(110, 110, "盖住按钮的字", size=18))
    ui_.step()
    ui_.step()
    assert taps == [btn]


def test_progress_bar_widget_draws_its_value():
    lcd = FakeLcd()
    ui_ = ui.Ui(lcd)
    ui_.add(ui.ProgressBar(0, 0, 99, 9, value=0.5))
    ui_.step()
    assert lcd.array[5, 10] == ui.ACCENT
    assert lcd.array[5, 90] == ui.DIM


def test_label_alignments_stay_inside_their_box():
    for align in ("left", "center", "right"):
        lcd = FakeLcd()
        ui_ = ui.Ui(lcd)
        lbl = ui_.add(ui.Label(100, 100, "ABC", size=20, fg=0xFFFF, align=align))
        ui_.step()
        x0, y0, x1, y1 = lbl.box
        assert (lcd.array[y0:y1 + 1, x0:x1 + 1] == 0xFFFF).any()
        assert lcd.array[:, :x0].sum() == 0
        assert lcd.array[:, x1 + 1:].sum() == 0


def test_step_flushes_once_per_frame_and_counts():
    lcd = FakeLcd()
    ui_ = ui.Ui(lcd)
    ui_.run(frames=5)
    assert lcd.flushes == 5 and ui_.frames == 5


def test_run_returns_immediately_after_stop():
    lcd = FakeLcd()
    ui_ = ui.Ui(lcd)

    class Stopper(ui.NullInput):
        def poll(self):
            ui_.stop()
            return None

    ui_.source = Stopper()
    assert ui_.run(frames=None) == 1        # stop() 之后不再跑下一帧


def test_scripted_input_reports_its_script_then_none():
    src = ui.ScriptedInput([(1, 2, True)])
    assert src.poll() == (1, 2, True)
    assert src.poll() is None
    assert src.poll() is None


def test_demo_screen_draws_without_touching_anything_outside_the_array():
    lcd = FakeLcd()
    ui_ = ui.demo_ui(lcd)
    ui_.step()
    assert ui_.frames == 1
    assert lcd.array.shape == (pynq_lcd.HEIGHT, pynq_lcd.WIDTH)


# ---------------- 参考设计配色(六页界面用) ----------------

def test_reference_palette_is_rgb565_truncated_not_rounded():
    """新配色必须走 rgb565(): 分量按位截断。手写 #RRGGBB 常量容易差 1 个 LSB,
    让"同一种颜色"在两处看起来不一样。"""
    assert ui.BG == pynq_lcd.rgb565(0x0E, 0x14, 0x1C)
    assert ui.PANEL == pynq_lcd.rgb565(0x15, 0x1E, 0x2A)
    assert ui.LINE == pynq_lcd.rgb565(0x2A, 0x35, 0x43)
    assert ui.TEXT == pynq_lcd.rgb565(0xEE, 0xF3, 0xF8)
    assert ui.MUTED == pynq_lcd.rgb565(0x96, 0xA5, 0xB8)
    assert ui.CYAN == pynq_lcd.rgb565(0x5A, 0xE0, 0xC4)
    assert ui.ORANGE == pynq_lcd.rgb565(0xFF, 0xB0, 0x66)
    # 截断而非四舍五入: 0x1C 的低 3 位被丢掉, 不是进位
    assert ui.BG == ((0x0E & 0xF8) << 8) | ((0x14 & 0xFC) << 3) | (0x1C >> 3)


# ---------------- 折线(波形预览 / 星座连线) ----------------

def test_polyline_draws_a_connected_line():
    lcd = FakeLcd()
    c = ui.Canvas(lcd)
    c.polyline([(10, 10), (60, 10)], ui.CYAN)
    assert (lcd.array[10, 10:61] == ui.CYAN).all(), "水平折线应当连成一条"
    assert (lcd.array == ui.CYAN).sum() >= 50


def test_polyline_needs_at_least_two_points():
    lcd = FakeLcd()
    c = ui.Canvas(lcd)
    c.polyline([(10, 10)], ui.CYAN)
    c.polyline([], ui.CYAN)
    assert (lcd.array == ui.CYAN).sum() == 0


def test_polyline_clips_wild_points_instead_of_allocating_a_huge_mask():
    """越界的点要先夹到屏内再算包围盒 —— 否则能开出几万像素见方的 PIL 图。"""
    lcd = FakeLcd()
    c = ui.Canvas(lcd)
    c.polyline([(-100000, -100000), (100000, 100000)], ui.CYAN)
    assert lcd.array.shape == (pynq_lcd.HEIGHT, pynq_lcd.WIDTH)


def test_polyline_mask_is_cached_so_repainting_is_cheap():
    """折线掩膜必须缓存: 波形/星座预览参数不变时每帧都是同一条线。
    不缓存的话每帧都要 PIL 重渲, 会击穿各页"内容没变就不重画"的优化。"""
    pts = [(i, 100 + (i * 7) % 50) for i in range(0, 200, 3)]
    ui._polyline_mask(pts, 1)
    n_before = len(ui._SHAPE_CACHE)
    for _ in range(20):
        ui._polyline_mask(pts, 1)
    assert len(ui._SHAPE_CACHE) == n_before, "同样的点序列不该重复建掩膜"


def test_polyline_through_the_plot_widget():
    lcd = FakeLcd()
    ui_ = ui.Ui(lcd)
    ui_.add(ui.Plot(0, 0, 200, 100, series=[([(0, 50), (199, 50)], ui.CYAN)]))
    ui_.step()
    assert (lcd.array == ui.CYAN).sum() > 0


# ---------------- visible ----------------

def test_invisible_widget_is_neither_drawn_nor_hit():
    lcd = FakeLcd()
    taps = []
    ui_ = ui.Ui(lcd, source=ui.ScriptedInput([(120, 120, True), (120, 120, False)]))
    btn = ui_.add(ui.Button(100, 100, 80, 40, "点我", on_tap=taps.append))
    btn.visible = False
    ui_.step()
    ui_.step()
    assert taps == [], "不可见的控件不该被点到"
    assert ui_._hit(120, 120) is None
    assert (lcd.array == btn.fill).sum() == 0, "不可见的控件不该被画出来"


def test_a_hidden_overlay_does_not_eat_clicks_meant_for_the_button_below():
    """隐藏的遮罩不能吃掉点击 —— 与准星那个坑同一类。"""
    taps = []
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput([(120, 120, True), (120, 120, False)]))
    btn = ui_.add(ui.Button(100, 100, 80, 40, "点我", on_tap=taps.append))
    shade = ui_.add(ui.Panel(0, 0, 800, 480))       # 整屏, 画在按钮上面
    ui_.step()
    ui_.step()
    assert taps == [btn], "装饰卡片不该挡住按钮(它 tappable=False)"

    shade.visible = True
    assert shade.tappable is False


# ---------------- 新控件 ----------------

def test_panel_is_decoration_only():
    p = ui.Panel(0, 0, 800, 480)
    assert p.tappable is False and p.visible is True
    assert not p.contains(400, 240)


def test_value_row_shows_label_value_and_unit():
    lcd = FakeLcd()
    ui_ = ui.Ui(lcd)
    row = ui_.add(ui.ValueRow(10, 10, 300, 48, "频率", "1,000", "Hz", size=20))
    ui_.step()
    assert (lcd.array[10:58, 10:310] == ui.TEXT).any() or \
           (lcd.array[10:58, 10:310] == ui.MUTED).any()
    # 改值之后必须跟着变(这正是不能用 Label 的原因)
    row.value = "250,000"
    ui_.step()
    assert ui_.frames == 2


def test_value_row_disabled_is_not_tappable():
    hit = []
    row = ui.ValueRow(0, 0, 100, 40, "x", "1", on_tap=lambda w: hit.append(1))
    assert row.contains(50, 20)
    row.enabled = False
    assert not row.contains(50, 20), "锁定时不该能点开键盘"


def test_value_row_tap_opens_its_callback():
    hits = []
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput([(50, 20, True), (50, 20, False)]))
    row = ui_.add(ui.ValueRow(0, 0, 100, 40, "x", "1", on_tap=lambda w: hits.append(w)))
    ui_.step()
    ui_.step()
    assert hits == [row]


def test_table_draws_headers_and_cells_and_reports_the_tapped_cell():
    cells = [["255", "0"], ["0", "0"], ["0", "0"], ["0", "0"]]
    got = []
    # 表头占 HEAD_H(22), 第 0 行从 y=32 开始; 点第 1 行要落在 y=66 附近
    ui_ = ui.Ui(FakeLcd(), source=ui.ScriptedInput([(40, 70, True), (40, 70, False)]))
    tbl = ui_.add(ui.Table(10, 10, 200, ["增益", "延迟"], cells,
                           on_cell=lambda r, c: got.append((r, c))))
    ui_.step()
    ui_.step()
    assert got and got[0][0] == 1, "第 1 行(row index 1)应当被点到"


def test_table_is_tappable_but_only_inside_cells():
    tbl = ui.Table(10, 10, 200, ["a", "b"], [["1", "2"]], on_cell=lambda r, c: None)
    # 第 0 行的格子从 y = 10 + HEAD_H 开始
    y0 = 10 + ui.Table.HEAD_H + 4
    assert tbl.contains(20, y0)
    assert tbl._hit_cell(20, y0) == (0, 0)
    assert tbl._hit_cell(20, 15) is None, "表头不是格子"
    assert tbl._hit_cell(20, 10 + ui.Table.HEAD_H - 2) is None, "表头下方缝隙也不是格子"
