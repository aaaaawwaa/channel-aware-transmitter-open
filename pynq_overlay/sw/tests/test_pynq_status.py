"""pynq_status.py 的离线测试。

守护进程的本职是"长按 S0 优雅关机"(防断电写坏根文件系统), 状态屏只是锦上添花。
所以这里最要紧的两条断言是:
  (1) 屏幕建不起来时, 守护进程照常跑(attach_screen 返回 None, 不抛);
  (2) 屏幕运行中出问题时, 只降级成无界面(render_status 返回 None, 不抛)。
其余是状态屏本身的画法: LED 指示点要跟着 8 位 LED 走, 写卡时那行要变红, 长按进度条要动。

跑法: python -m pytest pynq_overlay/sw/tests/
"""
import os
import struct
import sys
from pathlib import Path

import pytest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_pynq  # noqa: E402

fake_pynq.install()

import pynq_lcd  # noqa: E402
import pynq_lcd_ui as ui  # noqa: E402
import pynq_status as daemon  # noqa: E402


def screen():
    lcd = fake_pynq.FakeLcd()
    return daemon.StatusScreen(lcd), lcd


# ---------------- 降级路径(最重要) ----------------

def test_attach_screen_degrades_when_the_ui_layer_is_missing():
    old = daemon._UI_IMPORT_ERROR
    daemon._UI_IMPORT_ERROR = ImportError("No module named 'PIL'")
    try:
        assert daemon.attach_screen(fake_pynq.FakeOverlay()) is None
    finally:
        daemon._UI_IMPORT_ERROR = old


def test_attach_screen_degrades_when_the_bitstream_has_no_framebuffer():
    # 旧位流没有 axi_gpio_fb —— 这是真实会遇到的: 板上位流落后于脚本
    class OldOverlay:
        axi_gpio_led = fake_pynq.FakeGpio()
        axi_gpio_btn = fake_pynq.FakeGpio()

    assert daemon.attach_screen(OldOverlay()) is None


def test_attach_screen_builds_a_screen_from_a_good_overlay():
    overlay = fake_pynq.FakeOverlay()
    scr = daemon.attach_screen(overlay)
    assert scr is not None
    # 屏建起来的同时会**放开触摸复位**(TP_RST = 通道2 的 bit1): 位流复位值是 0,
    # 不放开的话 GT911 一直待在复位里, 谁都读不到它。
    assert overlay.axi_gpio_fb.channel2.value == 0b10
    daemon.render_status(scr, daemon.Status())
    # 还没画第一帧: 源仍然是 PL 内置彩条(先画好内容再切, 免得闪一下未初始化的内存)
    assert overlay.axi_gpio_fb.channel1.value == fake_pynq.FAKE_ADDRESS
    assert overlay.axi_gpio_fb.channel2.value == 0b01


def test_release_touch_reset_only_touches_the_second_bit():
    # 只写 bit1(掩码 0b10), 不去动 bit0(帧缓冲使能)
    overlay = fake_pynq.FakeOverlay()
    daemon.release_touch_reset(overlay)
    assert overlay.axi_gpio_fb.channel2.value == 0b10
    assert overlay.axi_gpio_fb.channel2.mask == 0b10


def test_release_touch_reset_never_raises_on_an_old_bitstream():
    # 没有 axi_gpio_fb 的旧位流上, 触摸放不开也不该影响守护进程
    class OldOverlay:
        axi_gpio_led = fake_pynq.FakeGpio()

    assert daemon.release_touch_reset(OldOverlay()) is None


def test_render_status_swallows_a_broken_screen():
    class Boom:
        def render(self, state):
            raise RuntimeError("framebuffer gone")

    assert daemon.render_status(Boom(), daemon.Status()) is None


def test_status_screen_tracks_the_finger_with_a_crosshair():
    class FakeTouch:
        """第一次读有新点, 之后就没有 —— 与 GT911"读走才更新"一致。"""

        def __init__(self):
            self.reads = 0

        def read_points(self):
            self.reads += 1
            return [(100, 200, 1)] if self.reads == 1 else []

        @staticmethod
        def to_screen(x, y):
            return (x, y)

    screen = daemon.StatusScreen(fake_pynq.FakeLcd(), touch=FakeTouch())
    daemon.render_status(screen, daemon.Status())
    assert screen.cross.point == (100, 200), "手指在哪, 准星就在哪"
    assert "100" in screen.touch_text.text
    daemon.render_status(screen, daemon.Status())
    assert screen.cross.point is None, "抬手后准星要收起来"


def test_status_screen_without_touch_leaves_the_crosshair_off():
    screen = daemon.StatusScreen(fake_pynq.FakeLcd())
    daemon.render_status(screen, daemon.Status())
    assert screen.cross.point is None


def test_open_touch_returns_none_when_the_device_does_not_answer(monkeypatch):
    import pynq_touch

    def boom(*a, **kw):
        raise pynq_touch.I2CError("0x5D/0x14 都没应答")

    monkeypatch.setattr(pynq_touch.Touch, "open", staticmethod(boom))
    assert daemon.open_touch(fake_pynq.FakeOverlay()) is None


def test_render_status_keeps_a_working_screen():
    scr, _ = screen()
    assert daemon.render_status(scr, daemon.Status()) is scr


# ---------------- 状态屏画法 ----------------

def test_led_dots_mirror_the_led_register():
    scr, lcd = screen()
    state = daemon.Status()
    state.leds = 0b0000_0101          # LED0(心跳) + LED2(服务)
    scr.render(state)
    pitch, x0, y = scr.leds.pitch, scr.leds.x, scr.leds.y
    dots = [lcd.array[y + 12, x0 + i * pitch + pitch // 2] for i in range(8)]
    assert dots[0] == ui.GREEN and dots[2] == ui.GREEN
    assert dots[1] == ui.DIM and dots[7] == ui.DIM


def test_disk_row_turns_red_and_shows_a_banner_only_while_writing():
    scr, lcd = screen()
    state = daemon.Status()
    scr.render(state)
    idle_values = lcd.array[66 + 2 * daemon.StatusBoard.ROW_H:][:20, 174:400].copy()
    assert (idle_values == ui.RED).sum() == 0
    assert scr.banner.text == ""

    state.disk_active = True
    state.disk_loud = True
    scr.render(state)
    banner = lcd.array[300:330, 24:700]
    assert (banner == ui.RED).sum() > 0, "大量写卡时应当有红色告警"
    row = lcd.array[66 + 2 * daemon.StatusBoard.ROW_H:][:26, 174:400]
    assert (row == ui.RED).sum() > 0, "写卡那一行的值应当变红"


def test_a_quiet_write_does_not_raise_the_big_banner():
    # 后台零散写(板上实测每 2~4 秒几 KB~几百 KB)只该让那行字动, 不该弹整屏告警
    scr, _ = screen()
    state = daemon.Status()
    state.disk_active = True
    state.disk_loud = False
    state.disk_rate = 180 * 1024
    scr.render(state)
    assert scr.banner.text == ""
    assert "180 KB/s" in scr.board.values[2]


def test_rate_text_switches_units():
    assert daemon._rate_text(0) == "0 KB"
    assert daemon._rate_text(320 * 1024) == "320 KB"
    assert daemon._rate_text(3.2 * 1024 * 1024) == "3.2 MB"


def test_hold_progress_bar_follows_the_button_hold_time():
    scr, lcd = screen()
    state = daemon.Status()
    scr.render(state)
    assert scr.hold.value == 0.0
    state.held = daemon.HOLD_SECONDS / 2
    scr.render(state)
    assert abs(scr.hold.value - 0.5) < 1e-6
    state.held = daemon.HOLD_SECONDS * 10
    scr.render(state)
    assert scr.hold.value == 1.0, "长按进度条必须夹在 1.0, 不能溢出"


def test_shutting_down_shows_the_banner_and_keeps_the_led_mirror():
    scr, _ = screen()
    state = daemon.Status()
    state.shutting_down = True
    scr.render(state)
    assert "关机" in scr.banner.text
    assert scr.banner.fg == ui.YELLOW


def test_screen_enables_the_framebuffer_exactly_once():
    scr, lcd = screen()
    st = daemon.Status()
    scr.render(st)
    assert lcd.enables == 1, "只在第一帧切一次源, 免得每帧都重来"
    st.load = 1.0
    scr.render(st)
    st.load = 2.0
    scr.render(st)
    assert lcd.enables == 1, "后面几帧不该再切源"
    assert lcd.flushes == 3, "状态变了才画, 一次变化一次 flush"


def test_ip_and_jupyter_rows_reflect_the_state():
    scr, _ = screen()
    state = daemon.Status()
    state.ip = "192.0.2.10/24"
    state.jupyter = True
    scr.render(state)
    assert scr.board.values[0] == "192.0.2.10/24"
    assert scr.board.on[0] is True and scr.board.on[1] is True
    state.ip = None
    state.jupyter = False
    scr.render(state)
    assert scr.board.values[0] == "无 IPv4 地址"
    assert scr.board.on[0] is False and scr.board.on[1] is False


def test_load_row_flags_a_high_load():
    scr, _ = screen()
    state = daemon.Status()
    state.load = 2.5
    scr.render(state)
    assert "2.50" in scr.board.values[3]
    assert scr.board.on[3] is True


# ---------------- 不闪: 影子缓冲 + 无变化不重画 ----------------

def test_shadow_framebuffer_copies_the_finished_frame_in_one_go():
    real = fake_pynq.FakeLcd()
    shadow = ui.ShadowFramebuffer(real)
    assert shadow.array is not real.array
    assert (real.array == 0).all(), "还没 flush 之前不该动真帧缓冲"

    shadow.array[:, :] = 0x1234
    shadow.flush()
    assert (real.array == 0x1234).all(), "flush 要把整帧一次拷过去"
    assert real.flushes == 1
    assert shadow.address == real.address


def test_status_screen_draws_through_the_shadow_so_the_panel_never_sees_a_half_frame():
    scr, lcd = screen()
    assert isinstance(scr.ui.lcd, ui.ShadowFramebuffer)
    before = lcd.array.copy()
    scr.render(daemon.Status())
    changed = (lcd.array != before) | (before != 0)
    assert changed.any(), "第一帧应当真的画上屏了"
    # 再渲染同一份状态: 没有任何变化 -> 一次 flush 都不该有(面板纹丝不动)
    n = lcd.flushes
    scr.render(daemon.Status())
    assert lcd.flushes == n, "状态没变就不该重画"


def test_a_changed_state_does_redraw():
    scr, lcd = screen()
    st = daemon.Status()
    scr.render(st)
    n = lcd.flushes
    st.load = 9.9
    scr.render(st)
    assert lcd.flushes == n + 1


# ---------------- 写卡: 速率与迟滞 ----------------

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_disk(monkeypatch, counts, clock):
    seq = list(counts)
    monkeypatch.setattr(daemon.DiskActivity, "_read",
                        staticmethod(lambda: seq.pop(0) if seq else None))
    return daemon.DiskActivity(clock=clock)


def test_disk_rate_is_computed_from_the_sector_delta(monkeypatch):
    clock = FakeClock()
    disk = make_disk(monkeypatch, [0, 2048], clock)     # 基线 0, 一秒钟写了 2048 扇区
    clock.t = 1.0
    assert disk.poll() is True
    assert abs(disk.rate - 2048 * 512.0) < 1.0


def test_loud_needs_a_real_burst_and_then_holds(monkeypatch):
    clock = FakeClock()
    disk = make_disk(monkeypatch, [0, 200, 400, 400 + 4096], clock)

    clock.t = 1.0
    disk.poll()                                          # ~100 KB/s: 后台零散写
    disk.loud()
    assert disk.loud() is False, "常规后台写不该弹告警"

    clock.t = 2.0
    disk.poll()                                          # ~100 KB/s
    assert disk.loud() is False

    clock.t = 3.0
    disk.poll()                                          # 4096 扇区/s = 2 MB/s: 真在大量写
    assert disk.loud() is True

    clock.t = 4.0
    clock.t = 3.0 + daemon.LOUD_HOLD - 0.1
    assert disk.loud() is True, "速率降下来之后也要保持一会儿(迟滞, 防闪)"
    clock.t = 3.0 + daemon.LOUD_HOLD + 0.1
    assert disk.loud() is False

def test_hms_formatting():
    assert daemon._hms(0) == "00:00:00"
    assert daemon._hms(61) == "00:01:01"
    assert daemon._hms(3661) == "01:01:01"
    assert daemon._hms(90000) == "25:00:00"


def test_leds_value_mirrors_the_register():
    leds = daemon.Leds(fake_pynq.FakeGpio())
    leds.set(daemon.LED_NET, True)
    leds.set(daemon.LED_DISK, True)
    assert leds.value == 0b0000_1010
    assert leds._gpio is not None          # 不是重点, 只是确认没被写坏
    leds.write()
    assert leds.value == 0b0000_1010


# ---------------- P6/P7: 六页面板 ----------------

import awg_ctl as A          # noqa: E402
import ui_model              # noqa: E402


def _panel(lcd=None, cursor=None, ctl=None):
    """一套离线可跑的面板: 假 lcd + 假 ctl + 状态屏 + `UiView`。"""
    import pynq_lcd_ui
    import ui_view

    lcd = lcd if lcd is not None else fake_pynq.FakeLcd()
    scr = daemon.StatusScreen(lcd, cursor=cursor, background=pynq_lcd_ui.BG)
    app = ui_model.UiApp()
    app.page = ui_model.PAGE_STATUS
    ctl = ctl if ctl is not None else A.AwgCtl(A.RecordingBackend())
    view = ui_view.UiView(lcd, app, ctl=ctl, status_screen=scr, touch_cursor=cursor)
    return view, app, scr, ctl


def _frame(view, state=None):
    view.render(state if state is not None else daemon.Status())


def test_panel_renders_every_page_without_raising():
    view, app, _scr, _ctl = _panel()
    for page in ui_model.PAGES:
        app.page = page
        view.invalidate()
        _frame(view)


def test_the_status_page_still_lets_the_nav_bar_switch_away():
    """**状态页上导航必须点得动** —— 否则切进去就出不来。

    状态页由守护进程那套 `StatusScreen` 画, 它有自己的 `Ui`; 导航栏属于面板的 `Ui`。
    面板在状态页这一帧不收输入的话, 导航栏就只是"画着好看", 一个也点不动。
    """
    import pynq_touch
    import ui_view

    class FakeTouch:
        """第一帧按在"波形"导航项上, 第二帧抬手。"""

        def __init__(self, points):
            self.frames = [points, []]

        def read_points(self):
            return self.frames.pop(0) if self.frames else []

        @staticmethod
        def to_screen(x, y):
            return (x, y)

    # ⚠ 坐标从**真实控件**取, 不写死 —— 导航节距改过一次(60→51, 为了塞下第七项),
    #   写死的 y 会在下次改版式时静默点到隔壁那一项。
    view, app, _scr, _ctl = _panel()
    nav = view.nav[[i for i, (p, _ic, _n) in enumerate(ui_view.NAV_ITEMS)
                    if p == ui_model.PAGE_AWG][0]]
    nav_x, nav_y = (nav.box[0] + nav.box[2]) // 2, (nav.box[1] + nav.box[3]) // 2
    cursor = pynq_touch.TouchCursor(FakeTouch([(nav_x, nav_y, 1)]))
    view, app, _scr, _ctl = _panel(cursor=cursor)
    assert app.page == ui_model.PAGE_STATUS
    _frame(view)                                    # 按下
    _frame(view)                                    # 抬起 -> 一次点击
    assert app.page == ui_model.PAGE_AWG, "状态页上点导航必须真的换页"


def test_the_panel_reads_the_touch_once_per_frame():
    """状态页那一帧**只能读一次** GT911 —— 状态寄存器"读走才更新", 读第二次就是空的。

    面板与状态屏是两个 `Ui`, 但共用一个 `TouchCursor`: 面板读一次, 把点**传给**状态屏,
    两边看到同一时刻的手指。谁要是自己再读一次, 另一边的按钮就永远收不到点 —— 这正是
    "切到某页按钮全点不动"的机制。
    """
    import pynq_touch

    class CountingTouch:
        def __init__(self):
            self.reads = 0

        def read_points(self):
            self.reads += 1
            return [(100, 200, 1)]

        @staticmethod
        def to_screen(x, y):
            return (x, y)

    dev = CountingTouch()
    view, _app, _scr, _ctl = _panel(cursor=pynq_touch.TouchCursor(dev))
    _frame(view)
    assert dev.reads == 1, "一帧里器件被读了 %d 次" % dev.reads


def test_the_status_screen_still_reads_for_itself_when_used_alone():
    """单独用状态屏(旧路径)时它仍自己读 —— 只有嵌进面板才由面板读。"""
    import pynq_touch

    class CountingTouch:
        def __init__(self):
            self.reads = 0

        def read_points(self):
            self.reads += 1
            return []

        @staticmethod
        def to_screen(x, y):
            return (x, y)

    dev = CountingTouch()
    scr = daemon.StatusScreen(fake_pynq.FakeLcd(), cursor=pynq_touch.TouchCursor(dev))
    scr.invalidate()
    scr.render(daemon.Status())
    assert dev.reads == 1


def test_shutdown_forces_the_status_page():
    """关机时无论停在哪一页都要切到状态页 —— 那几秒是"别拔电"的提示。"""
    view, app, scr, ctl = _panel()
    panel = daemon._PanelScreen(view, daemon.PanelSession(app, ctl), None, scr)
    app.page = ui_model.PAGE_AWG
    st = daemon.Status()
    st.shutting_down = True
    panel.render(st)
    assert app.page == ui_model.PAGE_STATUS


def test_the_panel_stops_the_bridge_on_shutdown():
    """关机时要停掉桥线程(它占着串口), 否则关机流程可能卡在串口上。"""
    class FakeBridge:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    view, app, scr, ctl = _panel()
    bridge = FakeBridge()
    panel = daemon._PanelScreen(view, daemon.PanelSession(app, ctl), bridge, scr)
    panel.stop()
    assert bridge.stopped


def test_the_panel_falls_back_to_the_status_page_without_a_backend():
    """控制面建不起来(旧位流/缺模块)时只剩状态页, 但**长按关机那条路必须还在**。"""
    scr = daemon.StatusScreen(fake_pynq.FakeLcd())
    panel = daemon.attach_panel(fake_pynq.FakeOverlay(), scr.lcd, None, scr)
    assert panel is not None
    scr.invalidate()
    assert daemon.render_status(panel, daemon.Status()) is panel, "降级后仍要能画状态页"
    assert panel.session is None


def test_the_panel_and_the_status_screen_share_one_touch_cursor():
    """面板与状态屏必须拿到**同一个** `TouchCursor` 对象。"""
    import pynq_touch

    class FakeTouch:
        def read_points(self):
            return []

        @staticmethod
        def to_screen(x, y):
            return (x, y)

    cursor = pynq_touch.TouchCursor(FakeTouch())
    view, _app, scr, _ctl = _panel(cursor=cursor)
    assert scr.cursor is cursor and view.cursor is cursor


def test_the_status_screen_draws_no_missing_glyphs():
    """状态屏画的字也要有字形 —— 它那批字(写卡告警/关机提示/准星读数)是按状态分支画的,
    只渲一个默认状态会漏(实测漏过"正在关机 — 请等待")。

    ⚠ 平台相关: 开发机通过不等于板上通过, 板端要跑 `python3 glyph_check.py`。
    """
    import glyph_check
    import pynq_lcd_ui as ui

    drawn = [x for x in glyph_check.collect() if x[2] == "status-screen"]
    assert len(drawn) > 15, "只收到 %d 串状态屏文字" % len(drawn)
    bad = [(s, size) for s, size, _src in drawn if not ui.has_glyphs(s, size)]
    assert not bad, "状态屏上有豆腐块: %r" % (bad,)
    # 字号同样只能在板上判(见 `MIN_CJK_SIZE` 的注释), 这里先钉住开发机上也成立的那部分。
    small = [(s, size) for s, size, _src in drawn
             if glyph_check._has_cjk(s) and size < ui.MIN_CJK_SIZE]
    assert not small, "状态屏上有画得太小的中文: %r" % (small,)


def test_the_whole_daemon_path_works_end_to_end():
    """**上板之前最后一道网**: 用带整条控制面的假 overlay 走一遍守护进程真正的路径。

    覆盖 `attach_panel` 的建面 -> 开机回读 -> 走真实命中路径切页 -> 改草稿 -> 应用 ->
    事务线程下发 -> **从真寄存器回读**核对 -> 串口 `GET` 读回一致(这就是验收判据)。
    前面那些测试各测一段, 这条测的是"接起来还对不对" —— 接线错误只在这里才能暴露。
    """
    import time

    import ui_model
    import ui_view

    lcd = fake_pynq.FakeLcd()
    scr = daemon.StatusScreen(lcd)
    overlay = fake_pynq.AwgOverlay()
    panel = daemon.attach_panel(overlay, lcd, None, scr)
    assert panel.session is not None, "带完整控制面的 overlay 不该降级"

    app = panel.view.app
    panel.render(daemon.Status())
    deadline = time.time() + 5.0
    while not app.connected and time.time() < deadline:
        panel.render(daemon.Status())
        time.sleep(0.01)
    assert app.connected and app.fresh, "开机回读没到"
    assert not app.dirty, "回读进来之后草稿应当与设备一致"

    # 走真实命中路径点一下"波形"导航(从状态页切过去 —— 那正是"切进去出不来"的坑)
    # ⚠ 坐标从**真实控件**取, 不写死 —— 导航节距改过一次(60→51, 为了塞下第七项),
    #   写死的 y 会在下次改版式时静默点到隔壁那一项。
    nav = panel.view.nav[[i for i, (p, _ic, _n) in enumerate(ui_view.NAV_ITEMS)
                          if p == ui_model.PAGE_AWG][0]]
    nav_x, nav_y = (nav.box[0] + nav.box[2]) // 2, (nav.box[1] + nav.box[3]) // 2
    panel.view.ui.source = ui.ScriptedInput([(nav_x, nav_y, True), (nav_x, nav_y, False)])
    panel.render(daemon.Status())
    panel.render(daemon.Status())
    assert app.page == ui_model.PAGE_AWG, "状态页上点导航没换页"

    # 改草稿 -> 应用设置 -> 事务线程 -> 真寄存器回读
    app.draft["awg"][0]["freq_hz"] = 250000
    app.draft["awg"][0]["wave"] = "TRIANGLE"
    daemon._submit(panel.session, app, panel.view, ui_model.APPLY_AWG)
    deadline = time.time() + 5.0
    while app.pending and time.time() < deadline:
        panel.render(daemon.Status())
        time.sleep(0.01)
    assert not app.pending and app.last_result == ui_model.OK, app.last_message
    assert not app.dirty, "应用成功后草稿与设备应当一致"

    regs = panel.session.ctl.read_registers()["awg"][0]
    assert regs["freq_hz"] == 250000 and regs["wave"] == "TRIANGLE"
    # **验收判据**: 面板改的参数, 串口侧 GET 必须读得到(同一个 AwgCtl 实例)
    line = panel.session.ctl.handle("GET CH1")
    assert "FREQ 250000" in line and "WAVE TRIANGLE" in line, line


def test_the_serial_bridge_thread_and_the_panel_do_not_interleave_register_access():
    """串口桥线程与面板的事务线程**共用同一个 `AwgCtl`**, 所以一次寄存器访问不能被打断。

    一条 SET 要写十几个 GPIO 通道, 一次回读也要连着读十几个 —— 两条 SET 交错执行会让硬件
    落到"两条命令各写了一半"的状态。面板正好拿这份硬件状态与**自己的计划**核对, 于是会把
    别人的半条命令读成"我这条没生效"(MISMATCH) —— 看着像写没生效, 其实是自己的两个线程踩了。
    这里直接盯住那个不变量: 写与读**从不重叠**。
    """
    import threading
    import time

    class OverlapDetecting(A.RecordingBackend):
        """在**临界区内的第一次**寄存器访问拉一个小窗口, 并记下"写与读同时进行"的次数。

        只在每个临界区的开头拉窗口就够了: 窗口在锁里面, 锁要是没生效, 另一个线程必然
        撞进来。每次访问都拉的话在 Windows 上会被 `time.sleep` 的 15ms 粒度拖慢十倍。
        """

        FIRST = (A.GPIO_WORD, 1)         # `write_all` 的第一个通道

        def __init__(self):
            A.RecordingBackend.__init__(self)
            self.writing = 0
            self.reading = 0
            self.overlaps = 0

        def write_gpio(self, instance, channel, value):
            self.writing += 1
            if (instance, channel) == self.FIRST:
                time.sleep(0.002)
                if self.reading:
                    self.overlaps += 1
            A.RecordingBackend.write_gpio(self, instance, channel, value)
            self.writing -= 1

        def read_gpio(self, instance, channel):
            self.reading += 1
            if (instance, channel) == self.FIRST:
                time.sleep(0.002)
                if self.writing:
                    self.overlaps += 1
            value = A.RecordingBackend.read_gpio(self, instance, channel)
            self.reading -= 1
            return value

    backend = OverlapDetecting()
    ctl = A.AwgCtl(backend)
    done = []

    def serial():
        for _ in range(20):
            ctl.handle("SET CH1 FREQ 250000")
        done.append("serial")

    def panel():
        for _ in range(20):
            ctl.read_registers()
        done.append("panel")

    t1 = threading.Thread(target=serial)
    t2 = threading.Thread(target=panel)
    t1.start()
    t2.start()
    t1.join(30)
    t2.join(30)
    assert sorted(done) == ["panel", "serial"], "两个线程应当都跑完(没有死锁)"
    assert backend.overlaps == 0, "写与读重叠了 %d 次" % backend.overlaps


def test_status_screen_still_accepts_a_raw_touch_device():
    """不给 cursor 时仍旧自己建一个 —— 旧调用方式不能坏。"""
    import pynq_touch

    class FakeTouch:
        def read_points(self):
            return []

        @staticmethod
        def to_screen(x, y):
            return (x, y)

    scr = daemon.StatusScreen(fake_pynq.FakeLcd(), touch=FakeTouch())
    assert isinstance(scr.cursor, pynq_touch.TouchCursor)
    scr2 = daemon.StatusScreen(fake_pynq.FakeLcd(), touch=None)
    assert scr2.cursor is None


# ---------------- 事务线程 ----------------

def _session(ctl=None, **kw):
    app = ui_model.UiApp()
    ctl = ctl if ctl is not None else A.AwgCtl(A.RecordingBackend())
    return app, ctl, daemon.PanelSession(app, ctl, **kw)


def _pump_until(session, cond, tries=600):
    """线程是真的, 所以要等: 交替 pump 与检查, 直到条件成立或超时。"""
    import time
    for _ in range(tries):
        session.pump()
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_a_sync_job_fills_the_observed_state():
    app, _ctl, s = _session()
    assert not app.connected
    s.sync()
    assert _pump_until(s, lambda: app.connected), "回读没有到达"
    assert app.fresh and not app.pending
    # 干净的草稿会被回读同步 —— **除了 `dig.mode`**: 那是"下次跑哪个模式"的选择, 设备在
    # MODE OFF 时恒是 OFF, 同步进来会把用户的选择抹掉, 于是 START 计划发 `MODE OFF`。
    # 所以同步后应当**没有实质差异**(`dirty` 为假), 而 mode 仍保留草稿里的选择。
    assert not app.dirty, "回读同步之后不该还有待应用参数"
    assert app.draft["dig"]["mode"] == "QPSK", \
        "回读不该抹掉用户选定的模式(设备那侧对应的是 actual_mode)"
    assert app.observed["dig"]["mode"] == "OFF"


def test_a_sync_job_does_not_clobber_the_users_edits():
    """用户手上有没应用的改动时, 回读只更新 `observed`, **不许**动草稿。"""
    app, _ctl, s = _session()
    s.sync()
    assert _pump_until(s, lambda: app.connected)
    app.draft["awg"][0]["freq_hz"] = 432100        # 用户改了还没应用
    assert app.dirty
    s.sync()
    assert _pump_until(s, lambda: app.fresh and not app.pending)
    assert app.draft["awg"][0]["freq_hz"] == 432100, "回读把用户的输入冲掉了"
    assert app.observed["awg"][0]["freq_hz"] != 432100


def test_a_transaction_lands_in_the_device_and_clears_dirty():
    """端到端: 改草稿 -> `begin` -> 事务线程下发 -> 从**真寄存器**回读 -> `finish`。"""
    app, ctl, s = _session()
    s.sync()
    assert _pump_until(s, lambda: app.connected)
    app.draft["awg"][0]["freq_hz"] = 250000
    app.draft["awg"][0]["wave"] = "TRIANGLE"
    res, plan = app.begin(ui_model.APPLY_AWG)
    assert plan is not None and res == ui_model.OK
    s.submit(plan)
    assert _pump_until(s, lambda: not app.pending)
    assert app.last_result == ui_model.OK, app.last_message
    assert not app.dirty, "应用成功后草稿与设备应当一致"
    assert ctl.read_registers()["awg"][0]["freq_hz"] == 250000


def test_a_write_that_never_lands_is_reported_as_a_mismatch():
    """**这是真寄存器回读的价值**: 写静默失效时 `GET` 照常回正确值(影子状态), 只有从
    PL 寄存器解出来的回读能抓到 —— 必须报 MISMATCH, 不许说"已生效"。

    后端要**构造完之后**才装聋: `AwgCtl.__init__` 会写一遍默认值, 从开机起就读到 0
    的话草稿本身就不可用(这跟"写到一半失效"是两件事)。
    """
    class DeafBackend(A.RecordingBackend):
        deaf = False

        def write_gpio(self, instance, channel, value):
            if self.deaf:
                return                             # 写不进去
            A.RecordingBackend.write_gpio(self, instance, channel, value)

    backend = DeafBackend()
    app, _ctl, s = _session(ctl=A.AwgCtl(backend))
    s.sync()
    assert _pump_until(s, lambda: app.connected)
    backend.deaf = True
    app.draft["awg"][0]["freq_hz"] = 250000
    _res, plan = app.begin(ui_model.APPLY_AWG)
    assert plan is not None, _res
    s.submit(plan)
    assert _pump_until(s, lambda: not app.pending)
    assert app.last_result == ui_model.MISMATCH, app.last_message
    # 用户的值必须留着(设备没接受, 他还想重试)
    assert app.draft["awg"][0]["freq_hz"] == 250000


def test_a_backend_that_raises_fails_the_transaction_instead_of_hanging():
    """后端抛异常时事务要报失败并**标成过期**, 而不是永远挂着 pending。"""
    class Broken(A.RecordingBackend):
        broken = False

        def write_gpio(self, instance, channel, value):
            if self.broken:
                raise RuntimeError("no device")
            A.RecordingBackend.write_gpio(self, instance, channel, value)

    backend = Broken()
    app, _ctl, s = _session(ctl=A.AwgCtl(backend))
    app.set_observed(app.draft, "OFF")
    backend.broken = True
    _res, plan = app.begin(ui_model.APPLY_AWG)
    assert plan is not None, _res
    s.submit(plan)
    assert _pump_until(s, lambda: not app.pending)
    assert app.last_result == ui_model.IO_ERROR
    assert not app.fresh, "失败后要标成过期, 而不是继续显示一份可能不对的回读"


def test_a_transaction_that_never_answers_times_out():
    """事务线程卡住(后端阻塞)时必须超时失效, 不能永远挂着 pending。"""
    import threading
    import time

    release = threading.Event()

    class Stuck(A.RecordingBackend):
        stuck = False

        def write_gpio(self, instance, channel, value):
            if self.stuck:
                release.wait(5.0)
            A.RecordingBackend.write_gpio(self, instance, channel, value)

    backend = Stuck()
    app, _ctl, s = _session(ctl=A.AwgCtl(backend))
    app.set_observed(app.draft, "OFF")
    backend.stuck = True
    _res, plan = app.begin(ui_model.APPLY_AWG)
    s.submit(plan)
    assert app.pending
    s._deadline = time.monotonic() - 1.0           # 把期限拨到过去
    s.pump()
    assert not app.pending and app.last_result == ui_model.IO_ERROR
    assert not app.fresh
    release.set()


def test_the_session_periodically_rereads_the_device():
    """串口侧会改参数, 面板要**自己**周期性重读 —— 否则界面永远停在旧值。"""
    now = [1000.0]
    _app, _ctl, s = _session(clock=lambda: now[0], sync_period=5.0)
    assert s._jobs.qsize() == 0, "刚起来不该立刻排一次重读"
    now[0] += 6.0
    s.pump()
    assert s._jobs.qsize() == 1, "到点必须排一次重读"


def test_pump_never_raises_even_when_the_backend_is_broken():
    """`pump` 跑在画帧那条路上: 抛出会被当成"屏幕坏了"而丢掉整个屏幕(含关机提示)。"""
    import time

    class Broken(object):
        def read_registers(self):
            raise RuntimeError("boom")

        def handle(self, line):
            raise RuntimeError("boom")

    app = ui_model.UiApp()
    s = daemon.PanelSession(app, Broken())
    s.sync()
    for _ in range(400):
        s.pump()
        if s.errors:
            break
        time.sleep(0.005)
    assert s.errors >= 1, "回读失败要被记下来"
    assert app.connected is False, "读不到设备就得显示未连接"


# ---------------- 数据页的后台作业 + 环形线程快照 ----------------

class FakeRing(object):
    """`RingThread` 的替身: 只提供 `snapshot()` 与命令队列的接口。"""

    def __init__(self, snap=None):
        self._snap = snap or {"present": 1, "active": 0, "state": "IDLE",
                              "error_code": 0, "active_bank": "NONE",
                              "next_bank": "NONE", "bank0_len": 0, "bank1_len": 0,
                              "src_bank": None, "fed": 0, "ring_on": 0,
                              "last_error": ""}
        self.submitted = []
        self.queues = []
        self.ring_calls = []

    def snapshot(self):
        return dict(self._snap)

    def submit(self, cmd, payload=None):
        self.submitted.append((cmd, payload))

    def set_queue(self, paths):
        self.queues.append(list(paths))

    def set_ring(self, on):
        self.ring_calls.append(bool(on))


def test_list_files_job_fills_the_app_with_sorted_name_size_pairs(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\x00" * 32)
    (tmp_path / "a.dat").write_bytes(b"\x00" * 8)
    (tmp_path / "notes.txt").write_bytes(b"ignore me")     # 后缀不在白名单里
    app = ui_model.UiApp()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ddr_dir=str(tmp_path))
    s.list_files()
    assert _pump_until(s, lambda: bool(app.ddr_files)), "目录列举没落地"
    assert app.ddr_files == [("a.dat", 8), ("b.bin", 32)]
    assert "2" in app.ddr_status


def test_list_files_reports_a_missing_directory_instead_of_raising():
    app = ui_model.UiApp()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()),
                            ddr_dir="/definitely/not/here")
    s.list_files()
    assert _pump_until(s, lambda: app.ddr_status == "目录不可读")
    assert app.ddr_files == []


def test_list_files_clamps_a_now_out_of_range_page(tmp_path):
    """删了文件之后页码可能越界 —— 夹回去比显示空白好。"""
    (tmp_path / "a.bin").write_bytes(b"\x00" * 4)
    app = ui_model.UiApp()
    app.ddr_page = 5
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ddr_dir=str(tmp_path))
    s.list_files()
    assert _pump_until(s, lambda: bool(app.ddr_files))
    assert app.ddr_page == 0


def test_pump_copies_the_ring_snapshot_into_the_app():
    app = ui_model.UiApp()
    ring = FakeRing()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=ring)
    assert s.pump() is True or app.pp, "第一拍就要把快照拷进来"
    assert app.pp["present"] == 1
    # 快照没动 -> 不算变化(不击穿"内容没变就不重画")
    assert s.pump() is False


def test_pump_reports_a_change_when_the_ring_snapshot_moves():
    app = ui_model.UiApp()
    ring = FakeRing()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=ring)
    s.pump()
    ring._snap["active"] = 1
    ring._snap["fed"] = 4
    assert s.pump() is True, "快照变了要报告重画"


def test_without_a_ring_thread_the_app_stays_at_no_ddr():
    """环形线程缺席(位流/驱动没就绪)时, 界面必须看到 `{}` —— 不是一份全 0 的假状态。"""
    app = ui_model.UiApp()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=None)
    s.pump()
    assert app.pp == {}
    assert ui_model.ddr_start_block(app) == "DDR 不可用"


def test_a_ring_snapshot_that_raises_does_not_break_pump():
    """快照取不到只记一笔 —— 绝不抛(`pump` 跑在画帧那条路上)。"""
    class BadRing(object):
        def snapshot(self):
            raise RuntimeError("boom")

    app = ui_model.UiApp()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=BadRing())
    s.pump()
    assert s.errors >= 1
    assert app.pp == {}


def test_the_ddr_command_path_enqueues_and_pushes_the_queue():
    """入队 -> 队列里出现 -> 环形线程收到**绝对路径**。"""
    app = ui_model.UiApp()
    ring = FakeRing()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=ring,
                            ddr_dir="/home/xilinx/waveforms")
    app.ddr_files = [("a.bin", 16), ("b.bin", 32)]
    app.pp = {"present": 1}

    class FakeView(object):
        def __init__(self):
            self.invalidated = 0

        def invalidate(self):
            self.invalidated += 1

    v = FakeView()
    daemon._ddr_command(s, app, v, "enqueue", 1)
    assert app.ddr_queue == ["b.bin"]
    assert ring.queues[-1] == [os.path.join("/home/xilinx/waveforms", "b.bin")]
    assert v.invalidated, "改了状态要重画"


def test_the_enqueue_chip_uses_the_selected_row_when_no_arg_is_given():
    """入队 chip 的回调**不带行号** —— 那就要用面板选中的那行。

    板上实测: chip 是 `self._ddr("enqueue")`(无 arg), 而处理函数原来写的是
    `if arg is not None and ...` —— 于是 `arg is None` 直接静默跳过, 表现就是
    "点了入队, 队列还是空的"(验收脚本抓到)。所以这条走**真实命中路径**点入队 chip,
    断言队列真的进了东西。
    """
    app = ui_model.UiApp()
    ring = FakeRing()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=ring,
                            ddr_dir="/home/xilinx/waveforms")
    app.ddr_files = [("a.bin", 16), ("b.bin", 32)]
    app.pp = {"present": 1}
    app.ddr_selected = 1                            # 用户点了第 2 行

    class FakeView(object):
        def invalidate(self):
            pass

    daemon._ddr_command(s, app, FakeView(), "enqueue")     # 不带 arg
    assert app.ddr_queue == ["b.bin"], "选了行却没入队"
    assert ring.queues[-1] == [os.path.join("/home/xilinx/waveforms", "b.bin")]

    # 没选任何行时要**说清楚**, 不能静默什么都不做
    app.ddr_selected = None
    daemon._ddr_command(s, app, FakeView(), "enqueue")
    assert app.ddr_status == "请先在文件表里点一行", app.ddr_status


def test_the_ddr_command_path_refuses_cleanly_without_a_ring_thread():
    app = ui_model.UiApp()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=None)

    class FakeView(object):
        def invalidate(self):
            pass

    daemon._ddr_command(s, app, FakeView(), "ring")
    assert app.ddr_status == "DDR 不可用"
    assert app.ddr_ring is False


def test_refresh_works_without_a_ring_thread(tmp_path):
    """刷新只列本地目录 —— 环形线程缺席时**不该**被"设备未连接"那条路挡掉。

    它和入队/移除/清空不同: 那三个要往 RingThread 的命令队列里塞东西, 这个只是
    `listdir`。挡住了就等于"面板能显示文件但永远刷不出新文件"。
    """
    (tmp_path / "a.bin").write_bytes(b"\x00" * 8)
    app = ui_model.UiApp()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=None,
                            ddr_dir=str(tmp_path))

    class FakeView(object):
        def invalidate(self):
            pass

    daemon._ddr_command(s, app, FakeView(), "refresh")
    assert app.ddr_status != "DDR 不可用", "刷新被环形线程的缺席挡住了"
    assert _pump_until(s, lambda: bool(app.ddr_files)), "刷新没落地"
    assert app.ddr_files == [("a.bin", 8)]


def test_the_ddr_start_command_obeys_the_gate():
    """门禁不过时**不下发** START —— 只把原因显示出来。"""
    import ring_thread as RT
    app = ui_model.UiApp()
    ring = FakeRing()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=ring)

    class FakeView(object):
        def invalidate(self):
            pass

    app.pp = {"present": 1, "active": 0, "bank0_len": 0, "bank1_len": 0}
    app.connected = True                    # 门禁的第一条就是"设备未连接"
    app.actual_mode = "OFF"                 # 起播还要求数字调制开着(见下)
    daemon._ddr_command(s, app, FakeView(), "start")
    assert ring.submitted == [], "没有可播的东西却下发了 START"
    assert app.ddr_status == "请先打开数字调制", \
        "MODE OFF 时起播会被 DdrPp.start() 拒成 ERROR, 面板必须先挡住"

    # MODE 开着但没装填 —— 队列里有文件**仍然不能起播**(装填要 MODE OFF、起播要非 OFF)
    app.actual_mode = "QPSK"
    app.ddr_queue = ["a.bin"]
    daemon._ddr_command(s, app, FakeView(), "start")
    assert ring.submitted == [], "队列非空被当成了可播"
    assert app.ddr_status == "请先装填一个文件"

    # 真装好了一块, 才放行
    app.pp["bank0_len"] = 4096
    daemon._ddr_command(s, app, FakeView(), "start")
    assert ring.submitted == [(RT.CMD_START, 2)]


def test_the_ddr_load_command_obeys_the_mode_gate():
    """装填要求 MODE OFF —— 门禁不过时**不下发**, 只把原因显示出来。"""
    import ring_thread as RT
    app = ui_model.UiApp()
    app.connected = True
    app.actual_mode = "QPSK"                # 数据源还在 AWG 手里
    app.ddr_files = [("a.bin", 64)]
    app.pp = {"present": 1, "active": 0, "bank0_len": 0, "bank1_len": 0}
    ring = FakeRing()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=ring,
                            ddr_dir="/home/xilinx/waveforms")

    class FakeView(object):
        def invalidate(self):
            pass

    daemon._ddr_command(s, app, FakeView(), "load")
    assert ring.submitted == [], "MODE 非 OFF 时不该下发装填"
    assert app.ddr_status == "请先关闭数字调制"

    app.actual_mode = "OFF"
    app.ddr_selected = 0
    daemon._ddr_command(s, app, FakeView(), "load")
    assert ring.submitted == [(RT.CMD_LOAD,
                               (os.path.join("/home/xilinx/waveforms", "a.bin"), None))]


def test_the_ddr_stop_command_turns_the_ring_off_too():
    """停止要**同时**关环形 —— 否则停完立刻又被自动喂起来。"""
    import ring_thread as RT
    app = ui_model.UiApp()
    app.ddr_ring = True
    ring = FakeRing()
    s = daemon.PanelSession(app, A.AwgCtl(A.RecordingBackend()), ring=ring)

    class FakeView(object):
        def invalidate(self):
            pass

    daemon._ddr_command(s, app, FakeView(), "stop")
    assert ring.submitted == [(RT.CMD_STOP, None)]
    assert app.ddr_ring is False
    assert ring.ring_calls[-1] is False


def test_the_data_page_degrades_through_the_real_attach_panel_path():
    """**上板之前最后一道网(数据页)**: 环形线程建不起来时, 整条守护进程路径仍要活着。

    假 overlay 有完整的 GPIO 控制面但**没有 `axi_dma_0`** —— 这正是"位流里没有 DDR PP"
    的形态。此时 `_start_ring_thread` 必须**降级返回 None**, 而不是把异常抛穿 `attach_panel`
    (抛了就是守护进程起不来, 连状态灯和串口桥一起没了 —— 一个可选功能不该带走整个守护进程)。

    然后走真实命中路径切到数据页: 它要能画出来、显示"DDR 不可用", 且 `app.pp` 停在 `{}`
    (不是一份全 0 的假状态)。前面那些测试各测一段, 这条测的是"接起来还对不对"。
    """
    import ui_model
    import ui_view

    lcd = fake_pynq.FakeLcd()
    scr = daemon.StatusScreen(lcd)
    overlay = fake_pynq.AwgOverlay()
    assert not hasattr(overlay, "axi_dma_0"), "这条测试的前提: 假 overlay 里没有 DDR PP"

    logs = []
    real_log = daemon.log
    daemon.log = logs.append
    try:
        panel = daemon.attach_panel(overlay, lcd, None, scr)
    finally:
        daemon.log = real_log
    assert panel.session is not None, "带完整控制面的 overlay 不该整体降级"
    assert panel.session.ring is None, "假 overlay 没有 axi_dma_0, 环形线程应当降级"
    # 降级的**原因**也要对: 少了 `axi_dma_0` 才是"位流里没有 DDR PP"那条路。
    # (若这里改成 ImportError, 说明是测试桩缺了 pynq 的某个名字, 不是被测行为 —— 别放过。)
    assert any("axi_dma_0" in m for m in logs), logs

    app = panel.view.app
    panel.render(daemon.Status())
    assert app.pp == {}, "环形线程缺席时 pp 必须是空字典"

    # 真实命中路径切到数据页 —— 坐标从**真实控件**取, 不写死(节距改过一次)
    nav = panel.view.nav[[i for i, (p, _ic, _n) in enumerate(ui_view.NAV_ITEMS)
                          if p == ui_model.PAGE_DDR][0]]
    nav_x, nav_y = (nav.box[0] + nav.box[2]) // 2, (nav.box[1] + nav.box[3]) // 2
    panel.view.ui.source = ui.ScriptedInput([(nav_x, nav_y, True), (nav_x, nav_y, False)])
    panel.render(daemon.Status())
    panel.render(daemon.Status())
    assert app.page == ui_model.PAGE_DDR, "点导航没切到数据页"

    # 数据页要能画, 且操作被禁用(点了也没反应 —— 比"点了不生效"好)
    panel.render(daemon.Status())
    assert ui_model.ddr_start_block(app) == "DDR 不可用"
    daemon._ddr_command(panel.session, app, panel.view, "start")
    assert app.ddr_status == "DDR 不可用"
    assert panel.view.ddr_start_btn.enabled is False


# ---------------- configure_ps_clocks: FCLK 节流门必须被清掉 ----------------
#
# 板上根因(2026-09-23 上板实测): Linux 会把**没有内核客户端认领**的 FPGA 输出时钟
# 一律写 FPGAx_THR_CNT=1 停掉, 而 FPGAx_CLK_CTRL 里的分频/源选择**照旧读出来是好的**
# —— 所以光看 CLK_CTRL 发现不了门被关了。UG585 5.6.2: PS-PL AXI 接口时钟没在跑时
# 发起 GPV 访问可能**挂死整个 PS**。实测现象正是"第一次 CPU 读 PL 就从机, 串口、
# 网口全哑, JTAG 下两个核停在同一个内核 PC"。
#
# A/B 证据(同一 U-Boot 起点、同一位流, 只有内核参数不同):
#   bootargs 无附加参数  -> THR_CNT=1/1/1/1, THR_STA RUNNING=0 -> 读 PL 挂死
#   + clk_ignore_unused  -> THR_CNT=0/0/0/0, THR_STA RUNNING=1 -> 0x41200000=00000003
# 也就是门是**内核 common clock 框架**关的。用户态清了之后 10 秒不回来(不会自己
# 重新关门), 所以在 configure_ps_clocks 里清是稳定的修法。


class FakeSlcr(bytearray):
    """按 SLCR 绝对地址做 4 字节读写的假 SLCR(偏移 0xF8000000)。

    `configure_ps_clocks` 走的是 buffer 协议(`struct.pack_into`), 所以这个假映射
    必须**是**字节数组本身, 不能用方法拦截。普通 `rd`/`wr` 按硬件规则同步
    `CNT -> STA`; 生产函数绕过方法直接写时, `run_configure` 在校验前同步一次。
    """

    def __init__(self):
        bytearray.__init__(self, 0x1000)

    def rd(self, addr):
        self._sync_sta()
        return struct.unpack_from("<I", self, addr - daemon.SLCR_OFFSET)[0]

    def wr(self, addr, val):
        struct.pack_into("<I", self, addr - daemon.SLCR_OFFSET, val)
        self._sync_sta()

    def _sync_sta(self):
        """STA 按计数器现算: 这是硬件行为, 不是测试的发明。

        板上实测写 FPGAx_THR_CNT=0 之后 RUNNING 立刻置 1、写非 0 则清 0
        (Test F/G/H 都量到)。`configure_ps_clocks` 也正是靠这一点才能在清完
        计数器后直接断言 RUNNING。
        """
        for cnt, sta in zip(daemon.FPGA_THR_CNT, daemon.FPGA_THR_STA):
            cnt_now = struct.unpack_from("<I", self, cnt - daemon.SLCR_OFFSET)[0]
            running = daemon.FPGA_THR_RUNNING if cnt_now == 0 else 0
            struct.pack_into("<I", self, sta - daemon.SLCR_OFFSET, running)

    def close(self):
        pass


def throttled(slcr):
    """把四个 FPGA 时钟设成 Linux 留下的样子: 门全关, RUNNING 全 0。"""
    for cnt in daemon.FPGA_THR_CNT:
        slcr.wr(cnt, 1)


def cleared(slcr):
    """把四个门清掉; STA 由 _sync_sta 跟着变成 RUNNING(同硬件)。"""
    for cnt in daemon.FPGA_THR_CNT:
        slcr.wr(cnt, 0)


def running(slcr):
    """读出来的四路 RUNNING 位。"""
    return [bool(slcr.rd(a) & daemon.FPGA_THR_RUNNING)
            for a in daemon.FPGA_THR_STA]


class FakeMmapModule(object):
    """替身 mmap 模块: 只实现 configure_ps_clocks 用到的那几个属性。

    直接把真 mmap.mmap 换掉会连带把 MAP_SHARED 等常量也换没, 所以整个换模块。
    """

    MAP_SHARED = 1
    PROT_READ = 1
    PROT_WRITE = 2

    def __init__(self, mapping):
        self._mapping = mapping

    def mmap(self, *args, **kwargs):
        return self._mapping


def run_configure(monkeypatch, slcr):
    """用假 SLCR 跑一遍 configure_ps_clocks, 返回假映射给测试检查。

    mmap 是在函数内部 import 的, 所以要 patch **mmap 模块本身**而不是
    daemon.mmap。
    """
    import mmap as real_mmap
    import sys as real_sys
    monkeypatch.setattr(daemon.os, "open", lambda *a, **k: 0)
    monkeypatch.setattr(daemon.os, "close", lambda fd: None)
    orig_verify = daemon._verify_fclk_running
    real_sys.modules["mmap"] = FakeMmapModule(slcr)
    try:
        # 函数内部用 buffer 协议直接写/读 THR_CNT, STA 是硬件自动跟着变的,
        # 假映射只在 Python 方法里同步 —— 所以在断言之前补一次 sync_sta。
        def verify_after_sync(rd):
            slcr._sync_sta()
            return orig_verify(rd)

        monkeypatch.setattr(daemon, "_verify_fclk_running", verify_after_sync)
        daemon.configure_ps_clocks()
    finally:
        monkeypatch.setattr(daemon, "_verify_fclk_running", orig_verify)
        real_sys.modules["mmap"] = real_mmap
    return slcr


def test_configure_ps_clocks_clears_every_throttle_gate(monkeypatch):
    slcr = FakeSlcr()
    # 前置 = 板上 Linux 留下的真实状态: 门全关、RUNNING 全 0, 但分频寄存器
    # 读出来一切正常 —— 这正是"只看 CLK_CTRL 永远发现不了问题"的原因。
    throttled(slcr)
    assert [slcr.rd(c) for c in daemon.FPGA_THR_CNT] == [1] * 4
    assert running(slcr) == [False] * 4
    slcr.wr(daemon.FPGA0_CLK_CTRL, daemon.FPGA0_CLK_CTRL_VALUE)
    slcr.wr(daemon.FPGA1_CLK_CTRL, daemon.FPGA1_CLK_CTRL_VALUE)

    run_configure(monkeypatch, slcr)

    assert [slcr.rd(c) for c in daemon.FPGA_THR_CNT] == [0] * 4, \
        "四个 FPGAx_THR_CNT 都必须清 0 —— FCLK0 之外的门同样会挂死板子"


def test_configure_ps_clocks_verifies_the_clocks_are_running(monkeypatch):
    """只清计数器不校验是不够的: 四路 RUNNING 必须真的置起来。

    `_verify_fclk_running` 是被单独抽出来的那半步, 所以这里可以直接喂读数函数,
    不用去伪装整个 SLCR。
    """
    ok = FakeSlcr()
    cleared(ok)
    daemon._verify_fclk_running(ok.rd)

    bad = FakeSlcr()
    throttled(bad)
    with pytest.raises(RuntimeError):
        daemon._verify_fclk_running(bad.rd)


def test_configure_ps_clocks_raises_when_a_gate_stays_stopped(monkeypatch):
    """门若清不掉, 必须**大声失败**, 不能静默继续去做 GPV 访问。

    静默返回正是线上症状: 调用方以为时钟好了, 第一次 GPV 访问就把整个 PS 挂死
    (串口/网口全哑, 只能 JTAG 救), 那比在这里直接报错难查得多。
    """
    slcr = FakeSlcr()
    throttled(slcr)

    def rd_gate_stuck(addr):
        if addr in daemon.FPGA_THR_STA:
            return 0x00000000
        return slcr.rd(addr)

    with pytest.raises(RuntimeError):
        daemon._verify_fclk_running(rd_gate_stuck)
