from test_ui_view import view, center
from pynq_touch import TouchCursor, Touch, REG_STATUS, REG_POINT1
from test_pynq_touch import FakeBus
import ui_model as M
import pynq_status as S
import threading


class Reports:
    def __init__(self, reports):
        self.reports = iter(reports)
        self.reads = 0
    def poll(self):
        self.reads += 1
        return next(self.reports, None)


def test_cursor_release_dispatches_in_second_frame_and_reads_once_per_tick():
    cursor = TouchCursor(None)
    app, _, v = view(touch_cursor=cursor)
    button = v.nav[5]  # Tools
    x, y = center(button)
    cursor.inner = Reports([(x,y,True), (x,y,False)])
    v.render()
    v.render()
    assert app.page == M.PAGE_TOOLS, 'release was delayed by stale replay'
    assert cursor.inner.reads == 2
    assert v.tools_digital_btn.visible
    assert not v.awg_rows['freq_hz'].visible


def test_poll_then_render_does_not_read_touch_twice():
    cursor = TouchCursor(None)
    _, _, v = view(touch_cursor=cursor)
    cursor.inner = Reports([None])
    v.poll_input()
    v.render()
    assert cursor.inner.reads == 1


def test_idle_ticks_preserve_no_redraw_optimization():
    cursor = TouchCursor(None)
    _, _, v = view(touch_cursor=cursor)
    cursor.inner = Reports([None, None])
    v.render()
    frames = v.ui.frames
    v.render()
    assert v.ui.frames == frames
    assert cursor.inner.reads == 2


def test_input_cadence_is_independent_of_slow_status_period():
    assert getattr(S, 'INPUT_PERIOD', 1) <= .02
    assert S.UI_PERIOD >= .1
    assert S.POLL <= .02


def test_blocked_status_sampling_does_not_block_local_touch():
    assert hasattr(S, '_SlowStatusCollector'), 'slow system probes still run on the UI thread'
    entered, release = threading.Event(), threading.Event()
    def slow_probe():
        entered.set()
        release.wait(2)
        return {'ip':'192.0.2.1'}
    collector = S._SlowStatusCollector(sample=slow_probe)
    try:
        collector.start()
        assert entered.wait(1)
        assert collector.latest() == {}
        cursor = TouchCursor(None)
        app, _, v = view(touch_cursor=cursor)
        x, y = center(v.nav[5])
        cursor.inner = Reports([(x,y,True), (x,y,False)])
        v.render()
        v.render()
        assert app.page == M.PAGE_TOOLS
    finally:
        release.set()
        collector.stop()
        collector.join(2)


def test_not_ready_gt911_report_does_not_manufacture_release_or_click():
    bus = FakeBus({REG_STATUS: b'\x81', REG_POINT1: bytes(8)})
    cursor = TouchCursor(Touch(bus, 0x5D, raw_x_min=0, raw_x_max=799, raw_y_min=0, raw_y_max=479))
    app, _, v = view(touch_cursor=cursor)
    x, y = center(v.nav[5])
    bus.regs[REG_POINT1] = bytes((0, x & 255, x >> 8, y & 255, y >> 8, 0, 0, 0))
    v.render()  # new down report 0x81; acknowledging clears READY
    assert app.page == M.PAGE_TX
    assert bus.regs[REG_STATUS] == b'\x00'
    v.render()  # no new report 0x00: the finger is still down
    assert app.page == M.PAGE_TX, 'no-new-report was incorrectly treated as physical release'
    assert v.ui.active is v.nav[5]
    bus.regs[REG_STATUS] = b'\x80'
    v.render()  # actual fresh release
    assert app.page == M.PAGE_TOOLS
    assert v.ui.active is None
    assert sum(reg == REG_STATUS for _, reg, _ in bus.read_log) == 3
    assert len(bus.write_log) == 2, 'only fresh reports may be acknowledged'
