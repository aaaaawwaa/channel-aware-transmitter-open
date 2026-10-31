import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from siggen.preview import (_mod_int, _mod_wave, WavePreview, display_periods,
                            make_constellation_data, make_digital_mod_data,
                            make_mod_source_data, make_plot_data)
from siggen.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_triangle_modulation_matches_hardware_direction():
    points = np.array([0.0, 0.25, 0.5, 0.75])

    assert np.allclose(_mod_wave("TRIANGLE", points), [-1.0, 0.0, 1.0, 0.0])
    integral = _mod_int("TRIANGLE", points)
    assert np.allclose(integral, [0.0, -0.125, 0.0, 0.125])
    assert integral[1] < 0.0


def test_pulse_duty_and_phase_offset():
    _, pulse = make_plot_data("PULSE", 1, 100, n=8, window_s=1,
                              pulse_duty=25)
    _, shifted = make_plot_data("PULSE", 1, 100, n=8, window_s=1,
                                pulse_duty=25, phase_off=0.25)
    _, square = make_plot_data("SQUARE", 1, 100, n=8, window_s=1)
    assert np.allclose(pulse, [1.0, 1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0])
    assert np.allclose(shifted, [-1.0, -1.0, -1.0, -1.0,
                                  -1.0, -1.0, 1.0, 1.0])
    _, pulse50 = make_plot_data("PULSE", 1, 100, n=8, window_s=1,
                                pulse_duty=50)
    assert np.array_equal(pulse50, square)


def test_noise_is_deterministic_bipolar_and_nonperiodic_at_carrier_period():
    _, y = make_plot_data("NOISE", 1000, 100, n=4096, window_s=0.01)
    _, y2 = make_plot_data("NOISE", 1000, 100, n=4096, window_s=0.01)
    assert np.array_equal(y, y2)
    assert y[0] == -1.0
    assert len(np.unique(y)) > 100
    assert y.min() < -0.9 and y.max() > 0.9
    assert not np.array_equal(y[:1024], y[1024:2048])


def test_noise_preview_preserves_absolute_sequence_when_window_changes():
    _, y_big = make_plot_data("NOISE", 1000, 100, n=10000,
                              window_s=0.01, t_start=-0.002)
    _, y_small = make_plot_data("NOISE", 1000, 100, n=4000,
                                window_s=0.004, t_start=0.0)
    assert np.allclose(y_big[2000:6000], y_small)


    t, y = make_plot_data("SINE", 1000, 80, base_pct=100, harms=[(2, 50)],
                          mod={"mode": "OFF"})
    t2, y2 = make_plot_data("SINE", 1000, 80, base_pct=100, harms=[(2, 50)])
    assert np.allclose(y, y2)


def test_make_plot_data_phase_offset_off_and_default():
    t, y = make_plot_data("SINE", 1, 100, n=4)
    t0, y0 = make_plot_data("SINE", 1, 100, n=4, phase_off=0.0)
    t90, y90 = make_plot_data("SINE", 1, 100, n=4, phase_off=0.25)
    assert np.array_equal(t, t0)
    assert np.array_equal(t, t90)
    assert np.allclose(y, y0)
    assert np.allclose(y0, [0.0, -1.0, 0.0, 1.0])
    assert np.allclose(y90, [1.0, 0.0, -1.0, 0.0])


@pytest.mark.parametrize("mode", ["FM", "SWEEP"])
def test_make_plot_data_phase_offset_injects_initial_fm_phase(mode):
    freq_hz = 1000
    total_s = 3 / freq_hz
    grid_points = round(total_s * 50e6)
    sample_dt = total_s / grid_points
    mod = {"mode": mode, "mwave": "SQUARE", "mfreq": 500, "fm_dev": 0}
    t, shifted = make_plot_data("SINE", freq_hz, 100, n=6000, mod=mod,
                                phase_off=0.25)
    expected = np.cos(2 * np.pi * freq_hz * (t + sample_dt))
    assert np.allclose(shifted, expected, atol=1e-5)


def test_am_envelope_depth100():
    # 载波 1kHz 3 周期窗口, 调制同频 1kHz 100%(调制与载波相位锁定, m=s):
    # y = clip(s·(1+s), ±1) = clip(s+s², ±1); 包络 1+s ∈ [0,2]
    t, y = make_plot_data("SINE", 1000, 100,
                          mod={"mode": "AM", "mwave": "SINE",
                               "mfreq": 1000, "am_depth": 100})
    assert abs(y.max() - 1.0) < 0.05      # 包络峰 2 被 ±1 饱和(硬件 ±127 饱和)
    assert abs(y.min() + 0.25) < 0.05     # s=-0.5 处 s+s²=-0.25
    assert np.min(np.abs(y)) < 0.05       # 包络谷(gain=0)与 s=0 过零

    # ampl_pct=50: 先钳位再乘 50% => max=0.5(错误顺序 scale->clip 会得 1.0)
    t4, y4 = make_plot_data("SINE", 1000, 50,
                            mod={"mode": "AM", "mwave": "SINE",
                                 "mfreq": 1000, "am_depth": 100})
    assert abs(y4.max() - 0.5) < 0.01
    assert abs(y4.min() + 0.125) < 0.01   # 包络谷 -0.25 × 50%


def test_fm_square_dev():
    # 方波调制 ±1: 瞬时频率 1200/800Hz 交替 -> 上升过零间隔 1667/2333 点。
    # 相位连续: 800Hz 段仅 0.8 圈不足整周期, 最长间隔 2333 点跨 m 跳变
    # (1200Hz 0.2 圈 + 800Hz 0.8 圈); fm_dev 增益错误(2×: 1429/2571,
    # 0.5×: 1819/2181)两断言均失败。
    t, y = make_plot_data("SINE", 1000, 100, n=6000,
                          mod={"mode": "FM", "mwave": "SQUARE",
                               "mfreq": 500, "fm_dev": 200})
    up = np.where((y[:-1] < 0) & (y[1:] >= 0))[0]
    gaps = np.diff(up)
    assert abs(gaps.min() - 1666.7) < 70      # 1200Hz 段纯周期
    assert abs(gaps.max() - 2333.3) < 70      # 跨 m 跳变的混合间隔


def test_pm_square_shift():
    # 方波调制 90°: m=+1 区间相位 +90°(=cos), m=-1 区间 -90°(=-cos)
    t, y = make_plot_data("SINE", 1000, 100, n=6000,
                          mod={"mode": "PM", "mwave": "SQUARE",
                               "mfreq": 500, "pm_dev": 64})
    cos = np.cos(2 * np.pi * 1000 * t)
    m = np.where((t % 2e-3) < 1e-3, 1.0, -1.0)   # 调制 500Hz 方波(周期 2ms): 前半 +1
    assert np.allclose(y, cos * m, atol=0.05)


def test_sweep_equals_fm():
    kw = dict(mode="FM", mwave="TRIANGLE", mfreq=100, fm_dev=10000)
    t1, y1 = make_plot_data("SINE", 1000, 100, mod=kw)
    kw2 = dict(kw, mode="SWEEP")
    t2, y2 = make_plot_data("SINE", 1000, 100, mod=kw2)
    assert np.allclose(y1, y2)


def test_display_periods_adaptive_window():
    # OFF/无调制 -> 固定 3 载波周期
    assert display_periods(10000, None) == 3
    assert display_periods(10000, {"mode": "OFF"}) == 3
    # 调制比 <= 3: 3 载波周期已覆盖调制周期
    assert display_periods(1000, {"mode": "AM", "mfreq": 1000}) == 3
    # 调制比 3..50: 窗口 = 1 个完整调制周期(载波周期数 = 比值)
    assert display_periods(10000, {"mode": "AM", "mfreq": 1000}) == 10
    assert display_periods(10000, {"mode": "FM", "mfreq": 100}) == 50
    # 调制比 > 50: 封顶 50 载波周期(防载波混叠)
    assert display_periods(10000, {"mode": "AM", "mfreq": 10}) == 50
    assert display_periods(100000, {"mode": "AM", "mfreq": 100}) == 50


def test_mod_plot_window_covers_mod_period():
    # 10k 载波 + 1k 调制: 预览窗口 = 10 载波周期 = 1ms = 1 个调制周期
    t, y = make_plot_data("SINE", 10000, 100,
                          mod={"mode": "AM", "mwave": "SINE",
                               "mfreq": 1000, "am_depth": 100})
    assert abs(t[-1] + t[1] - t[0] - 1e-3) < 1e-6     # 窗口 = 1ms
    # 调制源曲线同窗口, 覆盖完整调制周期: 峰谷均到达 + 半开窗口内 1 次下降过零
    # (正弦从相位 0 起, 上升过零在 ph=1 处被 endpoint=False 排除)
    tm, m = make_mod_source_data(10000, {"mode": "AM", "mwave": "SINE", "mfreq": 1000})
    assert abs(m.max() - 1.0) < 1e-6 and abs(m.min() + 1.0) < 1e-6
    down = np.where((m[:-1] > 0) & (m[1:] <= 0))[0]
    assert len(down) == 1
    # 包络谷可达 0(深度 100%): 窗口内存在 |y| 接近 0 的样本
    assert np.min(np.abs(y)) < 0.02
    # 包络峰被饱和削平: 窗口内 |y| 到满幅
    assert np.max(np.abs(y)) > 0.99


def test_make_mod_source_data_off():
    # OFF/无 mod -> 全零(曲线隐藏用)
    t, m = make_mod_source_data(1000, {"mode": "OFF"})
    assert np.all(m == 0.0)
    t, m = make_mod_source_data(1000, None)
    assert np.all(m == 0.0)


def test_make_mod_source_data_window_matches_plot():
    # 窗口与 make_plot_data 一致(3 个载波周期), 调制源是纯波形函数
    t, m = make_mod_source_data(1000, {"mode": "FM", "mwave": "SINE", "mfreq": 1000})
    t2, y2 = make_plot_data("SINE", 1000, 100, mod={"mode": "FM", "mwave": "SINE",
                                                    "mfreq": 1000, "fm_dev": 200})
    assert len(t) == len(t2) and np.allclose(t, t2)
    assert abs(m.max() - 1.0) < 1e-6 and abs(m.min() + 1.0) < 1e-6
    # 半开窗口 [0, 3ms): 正弦过零在 t=1/2ms, t=3ms 的过零被排除 -> 2 次
    up = np.where((m[:-1] < 0) & (m[1:] >= 0))[0]
    assert len(up) == 2


def test_make_mod_source_data_waves():
    t, m = make_mod_source_data(1000, {"mode": "FM", "mwave": "SQUARE", "mfreq": 500})
    assert set(np.unique(np.round(m))) <= {1.0, -1.0}
    assert np.any(m > 0) and np.any(m < 0)
    # 锯齿 1.5 个周期(周期 2ms): 窗口在上升段末尾截止, 峰值略低于 1
    t, m = make_mod_source_data(1000, {"mode": "FM", "mwave": "SAW", "mfreq": 500})
    assert abs(m.max() - 1.0) < 0.01 and abs(m.min() + 1.0) < 1e-6
    assert np.sum(np.diff(m) < -1e-6) == 1   # 仅 1 处回绕(周期边界)


@pytest.mark.parametrize("mode", ["FM", "SWEEP"])
def test_triangle_window_regeneration_preserves_absolute_phase(mode):
    mod = {"mode": mode, "mwave": "TRIANGLE", "mfreq": 137, "fm_dev": 321}
    t_big, y_big = make_plot_data("SINE", 1000, 100, n=12000,
                                  mod=mod, window_s=0.012, t_start=-0.004)
    t_sub, y_sub = make_plot_data("SINE", 1000, 100, n=6000,
                                  mod=mod, window_s=0.006, t_start=-0.001)
    start = 3000
    assert np.allclose(t_big[start:start + len(t_sub)], t_sub)
    assert np.allclose(y_big[start:start + len(y_sub)], y_sub, atol=5e-5, rtol=0)


@pytest.mark.parametrize("mode", ["FM", "SWEEP", "AM", "PM"])
def test_linked_ch2_preserves_hardware_phase_path(mode):
    ch1 = SimpleNamespace(
        wave="SINE", freq_hz=1000, ampl_pct=80, base_pct=70, harms=[(2, 20)],
        mode=mode, mwave="TRIANGLE", mfreq_01=1370, am_depth=33,
        fm_dev=321, pm_dev=27, phase_0_01=0, phaselink=False,
    )
    ch2 = SimpleNamespace(
        wave="SINE", freq_hz=2500, ampl_pct=55, base_pct=80, harms=[(3, 10)],
        mode="AM", mwave="SINE", mfreq_01=999, am_depth=100,
        fm_dev=999, pm_dev=999, phase_0_01=9000, phaselink=True,
    )

    class Panel:
        def __init__(self):
            self.cal_edit = SimpleNamespace(value=lambda: 9.5)

        def set_state(self, *args, **kwargs):
            pass

    class Preview:
        def set_calibration(self, value):
            self.calibration = value

        def set_params(self, *args, **kwargs):
            self.primary = args
            self.params = kwargs

    preview = Preview()
    window = SimpleNamespace(
        ctrl=SimpleNamespace(state=[ch1, ch2]),
        ch1=Panel(), ch2=Panel(),
        status_panel=SimpleNamespace(
            preview=preview, set_channel=lambda ch, state: None,
        ),
    )

    MainWindow._on_state(window, 1)

    expected_ch1_mod = {
        "mode": mode,
        "mwave": "TRIANGLE",
        "mfreq": 137.0,
        "am_depth": 33,
        "fm_dev": 321,
        "pm_dev": 27,
    }
    assert preview.params["mod"] == expected_ch1_mod
    linked = preview.params["ch2"]
    assert linked["freq_hz"] == ch1.freq_hz
    assert linked["wave"] == ch2.wave
    assert linked["ampl_pct"] == ch2.ampl_pct
    assert linked["base_pct"] == ch2.base_pct
    assert linked["harms"] == ch2.harms
    assert linked["phase_off"] == ch2.phase_0_01 / 36000.0
    expected_mod = expected_ch1_mod if mode in ("FM", "SWEEP") else {"mode": "OFF"}
    assert linked["mod"] == expected_mod
    if mode in ("FM", "SWEEP"):
        assert linked["mod"] is preview.params["mod"]

    _, linked_y = make_plot_data(
        linked["wave"], linked["freq_hz"], linked["ampl_pct"],
        linked["base_pct"], linked["harms"], mod=linked["mod"],
        phase_off=linked["phase_off"],
    )
    _, expected_y = make_plot_data(
        ch2.wave, ch1.freq_hz, ch2.ampl_pct, ch2.base_pct, ch2.harms,
        mod=expected_mod, phase_off=ch2.phase_0_01 / 36000.0,
    )
    assert np.allclose(linked_y, expected_y)


def test_wave_preview_ch2_uses_ch1_time_window(qapp):
    preview = WavePreview()
    preview.set_params(
        "SINE", 1000, 80,
        mod={"mode": "AM", "mwave": "SINE", "mfreq": 100, "am_depth": 50},
        ch2={
            "wave": "SQUARE", "freq_hz": 2500, "ampl_pct": 40,
            "base_pct": 100, "harms": [], "mod": {"mode": "FM", "mwave": "SINE",
                                                        "mfreq": 700, "fm_dev": 300},
            "phase_off": 0.25,
        },
    )

    t1, _ = preview._curve.getData()
    t2, _ = preview._ch2_curve.getData()
    assert np.array_equal(t1, t2)

    preview._regenerate(-1e-3, 3e-3)
    t1, _ = preview._curve.getData()
    t2, _ = preview._ch2_curve.getData()
    assert np.array_equal(t1, t2)

    preview._reset_home()
    t1, _ = preview._curve.getData()
    t2, _ = preview._ch2_curve.getData()
    assert np.array_equal(t1, t2)

    preview.set_params("SINE", 1000, 80)
    t2, y2 = preview._ch2_curve.getData()
    assert t2 is None or len(t2) == 0
    assert y2 is None or len(y2) == 0


@pytest.mark.parametrize("mode", ["FM", "SWEEP"])
def test_real_ch2_render_uses_inherited_frequency_modulation(mode, qapp):
    mod = {"mode": mode, "mwave": "SQUARE", "mfreq": 500, "fm_dev": 200}
    ch2 = {
        "wave": "SINE",
        "freq_hz": 1000,
        "ampl_pct": 100,
        "base_pct": 100,
        "harms": [],
        "mod": mod,
        "phase_off": 0.25,
    }
    preview = WavePreview()
    preview.set_params(
        "SINE", 1000, 100,
        base_pct=100,
        harms=[],
        mod=mod,
        ch2=ch2,
    )

    t1, _ = preview._curve.getData()
    t2, y2 = preview._ch2_curve.getData()
    r = t2 % (1 / 500)
    integral_s = np.where(r < 1 / (2 * 500), r, 1 / 500 - r)
    inst = 1000 + 200 * np.where(r < 1 / (2 * 500), 1, -1)
    expected_y = np.sin(2 * np.pi * (
        0.25 + 1000 * t2 + 200 * integral_s + inst / 50e6
    ))

    assert np.array_equal(t2, t1)
    assert np.allclose(y2, expected_y, atol=1e-4, rtol=0)
    preview.close()


def test_main_preview_calibration_always_uses_ch1(qapp):
    window = MainWindow()
    window.ch1.cal_edit.setValue(9.5)
    window.ch2.cal_edit.setValue(12.0)

    window._on_state(1)
    assert window.status_panel.preview._cal_vpp == 9.5

    window.ch2.cal_edit.setValue(13.0)
    assert window.status_panel.preview._cal_vpp == 9.5

    window.ch1.cal_edit.setValue(10.0)
    assert window.status_panel.preview._cal_vpp == 10.0
    window.close()


def test_digital_mod_data_shape_and_bounds():
    t, i, q = make_digital_mod_data("QAM16", symbols=4, sym_ticks=5)
    assert len(t) == len(i) == len(q) == 20
    assert np.max(np.abs(i)) <= 1.0
    assert np.max(np.abs(q)) <= 1.0


def test_digital_mod_data_holds_symbols():
    t, i, q = make_digital_mod_data("QAM16", symbols=4, sym_ticks=5)
    assert np.array_equal(i[:5], np.full(5, i[0]))
    assert np.array_equal(q[:5], np.full(5, q[0]))


def test_digital_mod_data_sym_rate():
    t, i, q = make_digital_mod_data("BPSK", symbols=2, sym_ticks=10000)
    assert abs(t[10000] - t[0] - 0.0001) < 1e-12  # 10 kSym/s (100 us/符号)
    assert len(i) == 20000


@pytest.mark.parametrize("mode, count", [("BPSK", 2), ("QPSK", 4), ("QAM4", 4),
                                         ("QAM16", 16), ("QAM64", 64)])
def test_constellation_data_count(mode, count):
    i, q = make_constellation_data(mode)
    assert len(i) == len(q) == count


def test_constellation_data_bounds():
    i, q = make_constellation_data("QAM64")
    assert np.max(np.abs(i)) <= 1.0 and np.max(np.abs(q)) <= 1.0


@pytest.mark.parametrize("fn", [make_digital_mod_data, make_constellation_data])
def test_digital_mod_rejects_bad_mode(fn):
    with pytest.raises(ValueError):
        fn("QAM8")


def test_rrc_digital_mod_data_uses_four_samples_per_symbol():
    t, i, q = make_digital_mod_data(
        "QPSK", symbols=4, sym_ticks=99, symrate_hz=100000, filter_name="RRC"
    )
    assert len(t) == len(i) == len(q) == 4 * 4 + 32
    assert abs(t[1] - t[0] - 1 / 400000) < 1e-15
    assert np.max(np.abs(i)) <= 1.0 and np.max(np.abs(q)) <= 1.0


def test_rrc_digital_mod_data_uses_selected_rolloff():
    t20, i20, q20 = make_digital_mod_data(
        "QPSK", symbols=8, symrate_hz=100000, filter_name="RRC", rolloff="0.2"
    )
    t50, i50, q50 = make_digital_mod_data(
        "QPSK", symbols=8, symrate_hz=100000, filter_name="RRC", rolloff="0.5"
    )

    assert np.array_equal(t20, t50)
    assert len(i20) == len(q20) == len(i50) == len(q50)
    assert not np.array_equal(i20, i50)
    assert not np.array_equal(q20, q50)


@pytest.mark.parametrize("mode", ["BPSK", "QAM4", "QAM16", "QAM64"])
def test_rrc_digital_mod_data_supports_all_modes(mode):
    t, i, q = make_digital_mod_data(mode, filter_name="RRC")
    assert len(t) == len(i) == len(q) and len(i) > 0


def test_make_digital_mod_data_qam_rrc_not_rejected():
    from siggen.preview import make_digital_mod_data
    for mode in ("QAM16", "QAM64"):
        t, i, q = make_digital_mod_data(
            mode, 64, 5000, symrate_hz=100000, filter_name="RRC")
        assert len(t) == len(i) == len(q) and len(i) > 0


def test_wave_preview_rrc_title_identifies_delay_and_theory(qapp):
    preview = WavePreview()
    preview.set_digital("QPSK", symrate_hz=100000, filter_name="RRC")
    title = preview.getPlotItem().titleLabel.text
    assert "RRC" in title
    assert "16" in title
    assert "理论仿真" in title
    preview.close()
