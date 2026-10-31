"""`awg_page` 的离线测试(P6)。

判据的核心是**唯一真源**: 面板上的每次操作都要走 `AwgCtl.handle()`, 于是
"面板改的"与"串口改的"在语义上完全等价。这里用假的 LCD/触摸验证:
  1. 点按钮 → 参数真的变了(读 `AwgCtl`, 不是读控件);
  2. 面板改完 → 串口侧的 `GET` 回读一致(这是 P6 的验收判据);
  3. 越界不会被点出去(频率/幅度被夹在协议范围内);
  4. 需要 STOPPED 的 DIGMOD 参数, 面板会先 MODE OFF(与串口侧同一约束)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_pynq  # noqa: E402

fake_pynq.install()

import awg_ctl as A  # noqa: E402
import awg_page as P  # noqa: E402
import pynq_lcd_ui as ui  # noqa: E402


def _page():
    ctl = A.AwgCtl()
    page = P.AwgPage(fake_pynq.FakeLcd(), ctl)
    return ctl, page


def _tap(btn):
    """模拟一次点击(按下+抬起都在控件内)。

    ⚠ 这是**直接调回调**的捷径: 它绕过了 `Ui._hit` 的命中判定。板上"按钮全点不动"那类 bug
    (准星整屏压在最上层把点击全吃掉) 正是这条捷径测不出来的 —— 所以每个页面还必须有一条
    走真实路径的测试, 见 `test_a_real_tap_reaches_the_button_through_ui_hit_test`。
    """
    x0, y0, x1, y1 = btn.box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    btn.on_press(cx, cy)
    btn.on_release(cx, cy, True)


def _real_tap(page, widget):
    """走 `render()` → `ui.step()` 的真实路径点一下: 命中判定、按下/抬起沿全都过一遍。"""
    import pynq_touch

    x0, y0, x1, y1 = widget.box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2

    class FakeTouch(object):
        def __init__(self):
            self.frames = [[(cx, cy, 1)], [], []]     # 按下 -> 抬手

        def read_points(self):
            return self.frames.pop(0) if self.frames else []

        @staticmethod
        def to_screen(x, y):
            return (x, y)

    cursor = pynq_touch.TouchCursor(FakeTouch())
    page.cursor = cursor
    page.ui.source = cursor.source()
    page.render()          # 按下
    page.render()          # 抬手 -> on_tap


def test_a_real_tap_reaches_the_button_through_ui_hit_test():
    """**按钮必须真的点得到** —— 走真实命中路径(准星那类装饰控件不许把点击吃掉)。

    板上报"点控制页没跳转"就是这条挂了: 准星的框是整屏又画在最上层, `_hit` 从顶往下找
    第一个 `contains`, 于是每次点击都落到准星上。`_tap()` 直接调回调, 测不出这个。
    """
    ctl, page = _page()
    assert A.WAVE_NAME[ctl.ch[0].wave] == "SINE"
    _real_tap(page, page.wave_btn[0])
    assert A.WAVE_NAME[ctl.ch[0].wave] == "SQUARE", "真实点击必须走到波形按钮的回调"
    assert page.ui._hit(400, 200) is None, "空白处不该命中任何控件"


def test_the_crosshair_never_swallows_a_tap():
    ctl, page = _page()
    assert page.cross.tappable is False
    x0, y0, x1, y1 = page.wave_btn[0].box
    assert page.ui._hit((x0 + x1) // 2, (y0 + y1) // 2) is page.wave_btn[0]


# ---------------- 面板操作 = 串口命令 ----------------

def test_wave_button_cycles_through_the_protocol_wave_names():
    ctl, page = _page()
    assert ctl.ch[0].wave == 0                       # 默认 SINE
    _tap(page.wave_btn[0])
    assert ctl.ch[0].wave == 1, "点一下应当切到 SQUARE"
    assert A.WAVE_NAME[ctl.ch[0].wave] == "SQUARE"
    for _ in range(len(A.WAVE_NAME) - 1):
        _tap(page.wave_btn[0])
    assert ctl.ch[0].wave == 0, "转一圈回到 SINE"


def test_channel_buttons_are_independent():
    ctl, page = _page()
    _tap(page.wave_btn[1])
    assert ctl.ch[1].wave == 1 and ctl.ch[0].wave == 0, "只该动 CH2"


def test_frequency_steps_by_magnitude_and_stays_in_range():
    ctl, page = _page()
    ctl.handle("SET CH1 FREQ 1000")
    # 直接调回调(标签不可点)
    page._freq_tap(0, +1)(None)
    assert ctl.ch[0].freq_hz == 2000, "1 kHz 档步进 1000"
    ctl.handle("SET CH1 FREQ 999999")
    page._freq_tap(0, +1)(None)
    assert ctl.ch[0].freq_hz == A.FREQ_MAX, "上界要夹住"
    ctl.handle("SET CH1 FREQ 10")
    page._freq_tap(0, -1)(None)
    assert ctl.ch[0].freq_hz == A.FREQ_MIN, "下界要夹住"


def test_amplitude_clamps_to_the_protocol_range():
    ctl, page = _page()
    ctl.handle("SET CH1 AMPL 2")
    page._ampl_tap(0, -5)(None)
    assert ctl.ch[0].ampl_pct == 0
    ctl.handle("SET CH1 AMPL 99")
    page._ampl_tap(0, +5)(None)
    assert ctl.ch[0].ampl_pct == A.AMPL_MAX


def test_mode_button_cycles_off_am_fm_pm_sweep():
    ctl, page = _page()
    seen = []
    for _ in range(len(A.MODE_NAME) + 1):
        seen.append(A.MODE_NAME[ctl.ch[0].mode])
        _tap(page.mode_btn[0])
    assert seen[:5] == ["OFF", "AM", "FM", "PM", "SWEEP"]
    assert seen[5] == "OFF", "转一圈回到 OFF"


def test_reset_button_restores_the_firmware_defaults():
    """面板不造协议里没有的语义: 没有"通道开关"命令, 所以给的是"复位默认"这种
    确实存在的组合操作。"""
    ctl, page = _page()
    ctl.handle("SET CH1 WAVE SQUARE")
    ctl.handle("SET CH1 FREQ 250000")
    ctl.handle("SET CH1 AMPL 90")
    ctl.handle("SET CH1 MODE FM")
    ctl.handle("SET CH2 WAVE NOISE")
    page._reset_tap()(None)
    assert A.WAVE_NAME[ctl.ch[0].wave] == "SINE"
    assert ctl.ch[0].freq_hz == 1000
    assert ctl.ch[0].ampl_pct == 50
    assert ctl.ch[0].mode == 0
    assert A.WAVE_NAME[ctl.ch[1].wave] == "SINE"
    assert ctl.digmod.mode == 0


# ---------------- 需要 STOPPED 的 DIGMOD 参数 ----------------

def test_filter_button_stops_digmod_first_then_flips_it():
    ctl, page = _page()
    ctl.handle("SET DIGMOD MODE QAM16")
    assert ctl.digmod.mode == 4
    page._filter_tap()(None)
    assert ctl.digmod.filter == 1, "RECT -> RRC"
    assert ctl.digmod.mode == 0, "面板替用户先 MODE OFF(与串口侧同一约束)"
    page._filter_tap()(None)
    assert ctl.digmod.filter == 0, "RRC -> RECT"


def test_rolloff_button_cycles_through_the_three_defined_values():
    ctl, page = _page()
    assert ctl.digmod.rolloff == 0                   # 默认 0.35
    page._rolloff_tap()(None)
    assert A.DIGMOD_ROLLOFF_NAME[ctl.digmod.rolloff] == "0.2"
    page._rolloff_tap()(None)
    assert A.DIGMOD_ROLLOFF_NAME[ctl.digmod.rolloff] == "0.5"
    page._rolloff_tap()(None)
    assert A.DIGMOD_ROLLOFF_NAME[ctl.digmod.rolloff] == "0.35"


def test_symrate_button_respects_the_stopped_requirement():
    ctl, page = _page()
    ctl.handle("SET DIGMOD MODE OFF")
    ctl.handle("SET DIGMOD SYMRATE 100000")
    page._symrate_tap(+1)(None)
    assert ctl.digmod.symrate_hz == 200000
    assert ctl.digmod.mode == 0, "改符号率前必须先 STOPPED"


def test_digmod_button_walks_through_every_mode():
    """面板点一次 = MODE OFF -> 进到下一个模式。

    这里钉的是一个真 bug: 早先按"当前模式"推下一个, 但每次都要先 MODE OFF, 于是当前值
    恒为 0, 永远在 OFF<->BPSK 之间跳, 够不到 QPSK/QAM。现在用面板自己的目标游标推进。
    """
    ctl, page = _page()
    reached = []
    for _ in range(len(A.DIGMOD_NAME) - 1):
        page._digmod_tap()(None)
        reached.append(A.DIGMOD_NAME[ctl.digmod.mode])
    assert reached == ["BPSK", "QPSK", "QAM4", "QAM16", "QAM64"], reached
    page._digmod_tap()(None)
    assert A.DIGMOD_NAME[ctl.digmod.mode] == "OFF", "转一圈回到 OFF"


# ---------------- 唯一真源: 面板改完串口回读一致 ----------------

def test_panel_changes_are_visible_to_the_uart_readback():
    """P6 的验收判据: 面板上改参数, 从串口 `GET` 回读必须一致。

    这里用**同一个 AwgCtl** 的 `handle("GET ...")` 来代表串口侧 —— 真板上桥接的就是它。
    """
    ctl, page = _page()
    _tap(page.wave_btn[0])
    page._freq_tap(0, +1)(None)
    page._ampl_tap(0, +5)(None)

    line = ctl.handle("GET CH1")
    assert "WAVE SQUARE" in line, line
    assert "FREQ 2000" in line, line
    assert "AMPL 55" in line, line


def test_errors_are_shown_not_swallowed():
    """越界/非法命令在面板上要看得见 —— 与串口侧回 `ERR` 是同一件事。"""
    ctl, page = _page()
    # 1000001..2000000 之间才走 FREQ OUT OF RANGE; 超过 2000000 会被 parse_u32 的
    # 硬上限先挡成 BAD VALUE(那是固件的真实行为, 面板照实显示)
    reply = page._cmd("SET CH1 FREQ 1500000")
    assert reply == "ERR FREQ OUT OF RANGE"
    assert page._notice == reply, "错误要挂到提示行上"
    # 超过 parse_u32 的 2000000 硬上限时先被挡成 BAD VALUE(固件的真实行为)
    assert page._cmd("SET CH1 FREQ 3000000") == "ERR BAD VALUE"


def test_render_reads_from_awg_ctl_not_from_the_widgets():
    """`render()` 必须从 `AwgCtl` 取显示值 —— 这样串口侧改了参数, 面板下一帧就跟着变。"""
    ctl, page = _page()
    ctl.handle("SET CH1 WAVE TRIANGLE")
    ctl.handle("SET CH1 AMPL 77")
    page.render()
    assert page.wave_btn[0].text == "TRIANGLE"
    assert page.ampl_lbl[0].text == "77%"
    # 反过来: 串口改完之后面板一帧就追上(不需要面板自己也知道)
    ctl.handle("SET CH1 WAVE SAW")
    page.render()
    assert page.wave_btn[0].text == "SAW"


def test_render_is_idempotent_when_nothing_changed():
    """一帧内容没变就不重画(与状态页同一个省法, 免得面板闪)。"""
    ctl, page = _page()
    page.render()
    first = page.ui.frames
    page.render()
    assert page.ui.frames == first, "内容没变不该再推一帧"


def test_switch_callback_fires():
    called = []
    ctl = A.AwgCtl()
    page = P.AwgPage(fake_pynq.FakeLcd(), ctl, on_switch=lambda: called.append(1))
    _tap(page.ui.widgets[1])                 # 第二个控件就是"状态页"按钮
    assert called == [1]