"""ChannelPanel 控件级测试(offscreen):信号发射 / 置灰联动 / 扫频预设。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from siggen.ui.main_window import MainWindow
from siggen.ui.widgets import (
    ChannelImpairPanel, ChannelPanel, DigitalModPanel, StatusPanel,
)

# 与 test_controller.py 共享单例:另一模块可能已建 QApplication
app = QApplication.instance() or QApplication([])


@pytest.fixture
def panel():
    p = ChannelPanel("CH1")
    p._updating = False          # 模拟用户可交互状态
    return p


@pytest.fixture
def ch2_panel():
    p = ChannelPanel("CH2", phase_link_enabled=True)
    p._updating = False          # 模拟用户可交互状态
    return p


def test_ch2_phase_controls_emit_and_show_endpoints(ch2_panel):
    got = []
    ch2_panel.param_changed.connect(lambda f, v: got.append((f, v)))

    ch2_panel.phase_slider.setValue(0)
    assert ch2_panel.phase_label.text() == "0.00°"
    ch2_panel.phase_slider.setValue(36000)
    ch2_panel.phase_link.setChecked(True)

    assert ch2_panel.phase_label.text() == "360.00°"
    assert ("phase", 36000) in got
    assert ("phaselink", True) in got


def test_default_ch1_has_no_phase_link_controls(panel):
    assert not hasattr(panel, "phase_slider")
    assert not hasattr(panel, "phase_label")
    assert not hasattr(panel, "phase_link")


def test_phase_link_disables_frequency_and_modulation_but_not_other_controls(ch2_panel):
    ch2_panel.set_state("SINE", 1000, 50, 100, [(2, 20)] + [(0, 0)] * 3,
                        "AM", "SINE", 10, 100, 0, 0)
    ch2_panel.phase_link.setChecked(True)

    assert all(not button.isEnabled() for button in ch2_panel.freq_buttons)
    assert not ch2_panel.freq_edit.isEnabled()
    assert not ch2_panel.mod_mode.isEnabled()
    assert not ch2_panel.mod_wave.isEnabled()
    assert not ch2_panel.mod_freq.isEnabled()
    assert not ch2_panel.am_depth.isEnabled()
    assert not ch2_panel.fm_dev.isEnabled()
    assert not ch2_panel.pm_dev.isEnabled()
    assert all(button.isEnabled() for button in ch2_panel.wave_btns.values())
    assert ch2_panel.ampl.isEnabled()
    assert ch2_panel.base_slider.isEnabled()
    assert all(combo.isEnabled() for combo in ch2_panel.harm_ord)
    assert ch2_panel.phase_slider.isEnabled()
    assert ch2_panel.phase_link.isEnabled()


def test_unlink_restores_frequency_and_mode_based_modulation(ch2_panel):
    ch2_panel.set_state("SINE", 1000, 50, 100, [(0, 0)] * 4,
                        "AM", "SINE", 10, 100, 0, 0)
    ch2_panel.phase_link.setChecked(True)
    ch2_panel.phase_link.setChecked(False)

    assert all(button.isEnabled() for button in ch2_panel.freq_buttons)
    assert ch2_panel.freq_edit.isEnabled()
    assert ch2_panel.mod_mode.isEnabled()
    assert ch2_panel.mod_wave.isEnabled()
    assert ch2_panel.mod_freq.isEnabled()
    assert ch2_panel.am_depth.isEnabled()
    assert ch2_panel.fm_dev.isEnabled()
    assert ch2_panel.pm_dev.isEnabled()

    ch2_panel.mod_mode.setCurrentIndex(0)
    ch2_panel.phase_link.setChecked(True)
    ch2_panel.phase_link.setChecked(False)
    assert ch2_panel.mod_mode.isEnabled()
    assert not ch2_panel.mod_wave.isEnabled()
    assert not ch2_panel.mod_freq.isEnabled()
    assert not ch2_panel.am_depth.isEnabled()
    assert not ch2_panel.fm_dev.isEnabled()
    assert not ch2_panel.pm_dev.isEnabled()


def test_set_state_phase_link_has_no_signal_echo(ch2_panel):
    got = []
    ch2_panel.param_changed.connect(lambda f, v: got.append((f, v)))

    ch2_panel.set_state("SINE", 1000, 50, 100, [(0, 0)] * 4,
                        "AM", "SINE", 10, 100, 0, 0,
                        phase_0_01=1234, phaselink=True)

    assert got == []
    assert ch2_panel.phase_slider.value() == 1234
    assert ch2_panel.phase_label.text() == "12.34°"
    assert ch2_panel.phase_link.isChecked()
    assert not ch2_panel.freq_edit.isEnabled()
    assert not ch2_panel.mod_mode.isEnabled()


def test_status_panel_shows_ch2_phase_and_link_state():
    status = StatusPanel()
    state = SimpleNamespace(wave="SINE", freq_hz=1000, ampl_pct=50,
                            base_pct=100, phase_0_01=9000, phaselink=True)

    status.set_channel(1, state)

    assert "90.00°" in status.ch2_label.text()
    assert "联动开启" in status.ch2_label.text()

    state.phaselink = False
    status.set_channel(1, state)

    assert "联动关闭" in status.ch2_label.text()


def test_noise_control_is_available_without_pulse_duty(panel):
    assert "NOISE" in panel.wave_btns
    panel.wave_btns["NOISE"].click()
    assert not panel.pulse_duty.isEnabled()


def test_pulse_control_emits_and_is_waveform_specific(panel):
    got = []
    panel.param_changed.connect(lambda f, v: got.append((f, v)))
    assert not panel.pulse_duty.isEnabled()
    panel.wave_btns["PULSE"].click()
    assert panel.pulse_duty.isEnabled()
    panel.pulse_duty.setValue(25)
    assert ("wave", "PULSE") in got and ("duty", 25) in got
    panel.wave_btns["SINE"].click()
    assert not panel.pulse_duty.isEnabled()

    got = []
    panel.param_changed.connect(lambda f, v: got.append((f, v)))
    panel.mod_mode.setCurrentIndex(1)
    panel.am_depth.setValue(50)
    panel.pm_dev.setValue(64)
    assert ("mode", "AM") in got and ("amdepth", 50) in got and ("pmdev", 64) in got


def test_mod_freq_typed(panel):
    got = []
    panel.param_changed.connect(lambda f, v: got.append((f, v)))
    panel.mod_freq.setText("2.5k")
    panel._mod_freq_typed()
    assert got == [("modfreq", 25000)]
    assert panel._mod_freq_01 == 25000


def test_set_state_no_echo(panel):
    got = []
    panel.param_changed.connect(lambda f, v: got.append((f, v)))
    panel.set_state("SINE", 1000, 50, 100, [(0, 0)] * 4,
                    "PM", "SAW", 25, 60, 5000, 32)
    assert got == []                       # 程序化更新零回灌
    assert panel.mod_mode.currentIndex() == 3
    assert panel.mod_wave.currentText() == "锯齿"
    assert panel.am_depth.value() == 60
    assert panel.fm_dev.value() == 5000
    assert panel.pm_dev.value() == 32
    assert panel.pm_dev_label.text() == "45°"


def test_off_disables_mod_controls(panel):
    panel.set_state("SINE", 1000, 50, 100, [(0, 0)] * 4, "OFF", "SINE", 10, 100, 0, 0)
    assert not panel.mod_wave.isEnabled()
    assert not panel.fm_dev.isEnabled()
    panel.set_state("SINE", 1000, 50, 100, [(0, 0)] * 4, "AM", "SINE", 10, 100, 0, 0)
    assert panel.mod_wave.isEnabled()
    assert panel.fm_dev.isEnabled()


def test_sweep_presets_triangle_and_label(panel):
    got = []
    panel.param_changed.connect(lambda f, v: got.append((f, v)))
    panel.mod_mode.setCurrentIndex(4)      # 扫频
    assert ("mode", "SWEEP") in got and ("mwave", "TRIANGLE") in got
    assert panel.mod_wave.currentIndex() == 2
    assert panel.fm_dev_caption.text() == "扫频跨度"


def test_preview_zoom_out_extends_data_window():
    """缩放缩小超出数据窗口时自动扩展(左右不缺失空白); 放大/参数变化不扩展。"""
    import numpy as np
    from siggen.preview import WavePreview

    w = WavePreview()
    mod = {"mode": "AM", "mwave": "SINE", "mfreq": 1000, "am_depth": 100}
    w.set_params("SINE", 10000, 100, 100, [(0, 0)] * 4, mod=mod)
    pi = w.getPlotItem()
    assert abs(w._data_win - 1e-3) < 1e-9          # 自适应窗口 1ms, 不扩展

    # 缩小到 2.5ms(超出 1ms): 数据扩展覆盖视图, 左右两端都有数据
    pi.vb.scaleBy((2.5, 1), center=(0, 0))
    xr = pi.viewRange()[0]
    view_w = xr[1] - xr[0]
    ty, _ = w._curve.getData()
    assert w._data_win >= view_w * 1.5
    assert np.sum(ty <= xr[0] + view_w * 0.1) > 100     # 左端有数据
    assert np.sum(ty >= xr[1] - view_w * 0.1) > 100     # 右端有数据
    tm, _ = w._mod_curve.getData()
    assert abs(tm[-1] - ty[-1]) < 1e-9                  # 调制源同步扩展

    # 视图含负时间(缩放中心在左端): 数据起点跟随视图起点, 负时间也有数据
    pi.vb.setRange(xRange=(-1e-3, 2e-3), padding=0)
    xr = pi.viewRange()[0]
    assert xr[0] < 0
    ty, _ = w._curve.getData()
    assert w._data_start <= xr[0] + 1e-12
    assert np.sum(ty < 0) > 100

    # 平移超出数据范围(不缩放)也扩展
    d1 = w._data_start + w._data_win
    pi.vb.setRange(xRange=(d1 - 0.2e-3, d1 + 1.0e-3), padding=0)
    xr = pi.viewRange()[0]
    ty2, _ = w._curve.getData()
    assert w._data_start <= xr[0] + 1e-9 and ty2[-1] >= xr[1] - 1e-9

    # 放大不触发扩展; 参数更新恢复自适应窗口
    ds, dw = w._data_start, w._data_win
    pi.vb.setRange(xRange=(ds + 0.1e-3, ds + 0.4e-3), padding=0)
    assert abs(w._data_start - ds) < 1e-12 and abs(w._data_win - dw) < 1e-12
    w.set_params("SINE", 10000, 100, 100, [(0, 0)] * 4, mod=mod)
    assert w._data_start == 0.0 and abs(w._data_win - 1e-3) < 1e-9


def test_preview_zoom_back_resets_home():
    """缩小后放大回初始窗口附近: 自动复位到初始数据(相位基准 t=0),
    波形与刚开始完全一致; 双击复位; 中间窗口不误触发。"""
    import numpy as np
    from siggen.preview import WavePreview

    w = WavePreview()
    mod = {"mode": "AM", "mwave": "SINE", "mfreq": 1000, "am_depth": 100,
           "fm_dev": 0, "pm_dev": 0}
    w.set_params("SINE", 10000, 100, 100, [(0, 0)] * 4, mod=mod)
    pi = w.getPlotItem()
    t0 = np.array(w._curve.getData()[0])
    y0 = np.array(w._curve.getData()[1])
    assert abs(w._home_win - 1e-3) < 1e-9

    # 缩小(数据扩展) -> 放大回 [0.05, 1.05]ms(滚轮中心偏移的典型场景)
    pi.vb.setRange(xRange=(0, 3e-3), padding=0)
    pi.vb.setRange(xRange=(0.05e-3, 1.05e-3), padding=0)
    t2, y2 = w._curve.getData()
    assert w._data_start == 0.0 and abs(w._data_win - 1e-3) < 1e-9
    assert len(t2) == len(t0) and np.allclose(np.array(y2), y0)

    # 放大到中间窗口(起点偏移 > 0.1×home): 不复位, 保持绝对时间轴
    pi.vb.setRange(xRange=(0.3e-3, 1.3e-3), padding=0)
    assert w._data_start > 0
    w._reset_home()
    assert w._data_start == 0.0 and abs(w._data_win - 1e-3) < 1e-9
    t3, y3 = w._curve.getData()
    assert np.allclose(np.array(y3), y0)


def test_volt_labels_calibration_linked(panel):
    """百分比控件旁显示换算电压(校准常数 × AMPL 联动, 硬件截断模型), 超满摆幅提示削波。"""
    panel.cal_edit.setValue(9.60)
    panel.ampl.setValue(50)
    panel.base_slider.setValue(100)
    panel._update_vpp()                                # 显式刷新(不依赖 valueChanged 是否触发), 保证标签同步
    assert "4.80V" in panel.base_label.text()          # 127 LSB × 9.6 × 50%
    panel.harm_ampl[0].setValue(30)
    assert "1.44V" in panel.harm_ampl_labels[0].text()  # 38/127 × 9.6 × 50%
    # AM 深度 100% + AMPL 50: 满摆幅 63 LSB = 4.76V, 包络峰 2×4.76 = 9.52V -> 削波提示
    panel.am_depth.setValue(100)
    assert "削波" in panel.am_depth_label.text()
    # 校准常数联动更新
    panel.cal_edit.setValue(10.0)
    assert "5.00V" in panel.base_label.text()
    assert "满摆幅 4.96 V" in panel.cal_hint.text()     # 63/127 × 10.0(截断模型)
    # AMPL 100 + 深度 0(校准 10.0): 峰 = 满摆幅 10.0V, 无削波
    panel.ampl.setValue(100)
    panel.am_depth.setValue(0)
    assert "10.00V" in panel.am_depth_label.text()
    assert "削波" not in panel.am_depth_label.text()


def test_preview_vpp_rms_measurement():
    """预览标题显示视图内波形 Vpp/RMS(按校准常数换算电压), 校准/缩放联动。"""
    import numpy as np
    from siggen.preview import WavePreview

    w = WavePreview()
    w.set_calibration(9.60)
    pi = w.getPlotItem()
    # 正弦 1k 100%: Vpp = 9.60, RMS = 9.6/(2√2) = 3.39
    w.set_params("SINE", 1000, 100, 100, [(0, 0)] * 4)
    vpp, rms = w._measure()
    assert abs(vpp - 9.60) < 0.02 and abs(rms - 3.39) < 0.01
    t = pi.titleLabel.text
    assert "窗口" in t and "Vpp 9.60 V" in t and "RMS 3.39 V" in t
    # 50% 幅度联动
    w.set_params("SINE", 1000, 50, 100, [(0, 0)] * 4)
    vpp, rms = w._measure()
    assert abs(vpp - 4.80) < 0.02 and abs(rms - 1.70) < 0.01
    # 校准常数联动
    w.set_calibration(10.0)
    assert abs(w._measure()[0] - 5.00) < 0.02
    # 方波 100%: RMS = cal/2
    w.set_calibration(9.60)
    w.set_params("SQUARE", 1000, 100, 100, [(0, 0)] * 4)
    assert abs(w._measure()[0] - 9.60) < 0.02
    assert abs(w._measure()[1] - 4.80) < 0.05
    # 视图内测量(放大到 1/4 周期, Vpp 变小)
    w.set_params("SINE", 1000, 100, 100, [(0, 0)] * 4)
    pi.vb.setRange(xRange=(0, 0.25e-3), padding=0)
    assert w._measure()[0] < 8.0


@pytest.fixture
def dig_panel():
    return DigitalModPanel()


def test_digital_panel_defaults(dig_panel):
    assert dig_panel.mode_combo.currentText() == "BPSK"
    assert dig_panel.symrate_spin.value() == 10000
    assert dig_panel.symrate_spin.isEnabled() is True
    assert not hasattr(dig_panel, "apply_btn")
    assert not hasattr(dig_panel, "off_btn")
    assert dig_panel.note.text() == "当前：标准 AWG 输出"


def test_digital_panel_seed_defaults_and_emits(dig_panel):
    assert dig_panel.seed_spin.value() == 127
    assert dig_panel.seed_spin.isEnabled() is True
    got = []
    dig_panel.seed_requested.connect(got.append)
    dig_panel.seed_spin.setValue(5)
    dig_panel.seed_btn.click()
    assert got == [5]


@pytest.fixture
def imp_panel():
    return ChannelImpairPanel()


def test_channel_impair_panel_default_unity_tap(imp_panel):
    # 9-bit signed Q8 cannot hold 256, so unity gain is 255.
    assert imp_panel.get_taps(1) == [(255, 0), (0, 0), (0, 0), (0, 0)]
    assert imp_panel.get_taps(2) == [(255, 0), (0, 0), (0, 0), (0, 0)]


def test_channel_impair_panel_emits_apply(imp_panel):
    got = []
    imp_panel.apply_requested.connect(lambda c, en, n, taps: got.append((c, en, n, taps)))
    imp_panel.set_hardware_enabled(True)
    imp_panel.set_confirmed(1, True, 40, [(255, 0), (-128, 2), (0, 0), (0, 3)])
    imp_panel.apply(1)
    assert got == [(1, True, 40, [(255, 0), (-128, 2), (0, 0), (0, 3)])]


def test_channel_impair_panel_applies_selected_channel(imp_panel):
    got = []
    imp_panel.apply_requested.connect(lambda c, en, n, taps: got.append((c, en, n)))
    imp_panel.set_confirmed(2, False, 7, [(1, 1), (2, 2), (3, 3), (4, 4)])
    imp_panel.apply_btns[2].click()
    assert got == [(2, False, 7)]


def test_channel_impair_panel_disconnect_locks_apply(imp_panel):
    imp_panel.set_hardware_enabled(False)
    assert imp_panel.apply_btn.isEnabled() is False
    assert imp_panel.apply_btns[2].isEnabled() is False
    imp_panel.set_hardware_enabled(True)
    assert imp_panel.apply_btn.isEnabled() is True
    assert imp_panel.apply_btns[2].isEnabled() is True


def test_digital_panel_confirmed_seed_locks_when_running(dig_panel):
    dig_panel.set_confirmed("QPSK", "PRBS7", 100000, True, seed=5)
    assert dig_panel.seed_spin.value() == 5
    assert dig_panel.seed_spin.isEnabled() is False
    assert dig_panel.seed_btn.isEnabled() is False
    dig_panel.set_confirmed("OFF", "PRBS7", 100000, False, seed=127)
    assert dig_panel.seed_spin.isEnabled() is True


def test_digital_panel_user_source_controls_and_upload(tmp_path, dig_panel):
    assert dig_panel.source_combo.currentText() == "PRBS7"
    assert not dig_panel.user_browse_btn.isEnabled()
    assert not dig_panel.user_upload_btn.isEnabled()
    assert not dig_panel.user_loop.isEnabled()

    source_calls = []
    upload_calls = []
    loop_calls = []
    dig_panel.source_requested.connect(source_calls.append)
    dig_panel.upload_requested.connect(upload_calls.append)
    dig_panel.loop_requested.connect(loop_calls.append)

    path = tmp_path / "bits.bin"
    path.write_bytes(b"101")
    dig_panel.source_combo.setCurrentText("USER")
    dig_panel.set_user_file(str(path))
    dig_panel.user_loop.setChecked(True)
    dig_panel.user_upload_btn.click()

    assert source_calls == ["USER"]
    assert loop_calls == [True]
    assert upload_calls == [str(path)]
    assert dig_panel.user_file_edit.text() == str(path)
    assert "3 B" in dig_panel.user_file_size.text()


def test_digital_panel_user_controls_lock_for_running_and_disconnect(dig_panel):
    dig_panel.set_hardware_enabled(True)
    dig_panel.source_combo.setCurrentText("USER")
    assert dig_panel.user_browse_btn.isEnabled()
    assert dig_panel.user_loop.isEnabled()

    dig_panel.set_confirmed("QPSK", "USER", 10000, True)
    assert not dig_panel.source_combo.isEnabled()
    assert not dig_panel.user_browse_btn.isEnabled()
    assert not dig_panel.user_upload_btn.isEnabled()
    assert not dig_panel.user_loop.isEnabled()

    dig_panel.set_confirmed("OFF", "USER", 10000, False)
    assert dig_panel.source_combo.isEnabled()
    assert dig_panel.user_browse_btn.isEnabled()
    assert dig_panel.user_loop.isEnabled()

    dig_panel.set_hardware_enabled(False)
    assert not dig_panel.source_combo.isEnabled()
    assert not dig_panel.user_browse_btn.isEnabled()
    assert not dig_panel.user_upload_btn.isEnabled()
    assert not dig_panel.user_loop.isEnabled()


def test_digital_panel_user_upload_progress_and_status(dig_panel):
    dig_panel.set_user_progress(25, 100)
    assert dig_panel.user_progress.value() == 25
    dig_panel.set_user_progress(80)
    assert dig_panel.user_progress.value() == 80
    dig_panel.set_user_status("上传完成")
    assert dig_panel.user_status.text() == "上传完成"


def test_main_window_wires_user_data_controller(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "siggen.controller.Controller.set_digmod_source",
        lambda _self, value: calls.append(("source", value)),
        raising=False,
    )
    monkeypatch.setattr(
        "siggen.controller.Controller.upload_user_data",
        lambda _self, value: calls.append(("upload", value)),
        raising=False,
    )
    monkeypatch.setattr(
        "siggen.controller.Controller.set_user_loop",
        lambda _self, value: calls.append(("loop", value)),
        raising=False,
    )
    w = MainWindow()
    w.ctrl.connected.emit()
    path = tmp_path / "bits.bin"
    path.write_bytes(b"0011")
    w.dig.source_combo.setCurrentText("USER")
    w.dig.set_user_file(str(path))
    w.dig.user_loop.setChecked(True)
    w.dig.user_upload_btn.click()

    assert calls == [("source", "USER"), ("loop", True), ("upload", str(path))]
    w.close()


def test_main_window_routes_user_progress_and_status(monkeypatch):
    from PySide6.QtCore import Signal
    from PySide6.QtCore import QObject

    class ControllerWithUserSignals(QObject):
        user_progress = Signal(int, int)
        user_status = Signal(str)

    w = MainWindow()
    signals = ControllerWithUserSignals()
    w.ctrl.user_progress = signals.user_progress
    w.ctrl.user_status = signals.user_status
    w._wire_user_data_signals()
    signals.user_progress.emit(3, 4)
    signals.user_status.emit("就绪")

    assert w.dig.user_progress.value() == 75
    assert w.dig.user_status.text() == "就绪"
    w.close()


def test_digital_panel_mode_selection_emits_request(dig_panel):
    got = []
    dig_panel.mode_requested.connect(got.append)
    dig_panel.mode_combo.setCurrentText("QAM16")
    dig_panel.mode_combo.setCurrentText("OFF")
    assert got == ["QAM16", "OFF"]


def test_digital_panel_symrate_emits_request(dig_panel):
    got = []
    dig_panel.symrate_requested.connect(got.append)
    dig_panel.symrate_spin.setValue(100000)
    dig_panel.symrate_btn.click()
    assert got == [100000]


def test_digital_panel_confirmed_symrate_updates_and_locks_when_running(dig_panel):
    dig_panel.symrate_spin.setValue(250000)
    dig_panel.set_confirmed("QPSK", "PRBS7", 100000, True)
    assert dig_panel.symrate_spin.value() == 100000
    assert dig_panel.symrate_spin.isEnabled() is False
    assert dig_panel.symrate_btn.isEnabled() is False
    assert dig_panel.mode_combo.isEnabled() is True
    dig_panel.set_confirmed("OFF", "PRBS7", 100000, False)
    assert dig_panel.symrate_spin.isEnabled() is True
    assert dig_panel.symrate_btn.isEnabled() is True


def test_digital_panel_set_confirmed(dig_panel):
    dig_panel.set_confirmed("QAM64", "PRBS7", 100000, True)
    assert dig_panel.mode_combo.currentText() == "QAM64"
    assert dig_panel.note.text() == "当前：QAM64（PRBS7，100 kSym/s，运行中）"
    dig_panel.set_confirmed("OFF", "PRBS7", 100000, False)
    assert dig_panel.mode_combo.currentText() == "OFF"
    assert dig_panel.note.text() == "当前：标准 AWG 输出"


def test_status_panel_dig_view_hidden_by_default():
    sp = StatusPanel()
    # 未显示父窗口时 isVisible 恒 False, 先 show() 使可见性断言有效
    sp.show()
    assert sp.dig_view.isVisible() is False
    sp.set_dig_view_visible(True)
    assert sp.dig_view.isVisible() is True


def test_main_window_digital_tab_owns_active_preview(monkeypatch):
    w = MainWindow()
    w.ctrl.digmod.mode = "QPSK"
    w.tabs.setCurrentIndex(1)

    params_calls = []
    monkeypatch.setattr(
        w.status_panel.preview, "set_params",
        lambda *args, **kwargs: params_calls.append((args, kwargs)),
    )

    w._on_state(0)

    assert params_calls == []


def test_main_window_standard_tab_ignores_digital_preview(monkeypatch):
    w = MainWindow()
    w.status_panel.show()
    digital_calls = []
    monkeypatch.setattr(
        w.status_panel.preview, "set_digital",
        lambda *args, **kwargs: digital_calls.append((args, kwargs)),
    )

    w._on_digmod_state("QPSK")

    assert digital_calls == []
    assert w.status_panel.dig_view.isVisible() is False


def test_main_window_renders_confirmed_digmod_status_line(monkeypatch):
    w = MainWindow()
    w.show()
    w.tabs.setCurrentIndex(1)
    digital_calls = []
    monkeypatch.setattr(
        w.status_panel.preview, "set_digital",
        lambda *args, **kwargs: digital_calls.append((args, kwargs)),
    )

    w.ctrl._on_line(
        "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RECT SYMRATE 10000 STATE RUNNING"
    )
    app.processEvents()

    assert w.dig.note.text() == "当前：QPSK（PRBS7，10 kSym/s，运行中）"
    assert w.status_panel.dig_view.isVisible() is True
    assert digital_calls[-1] == (
        ("QPSK", "time"),
        {"symrate_hz": 10000, "filter_name": "RECT", "rolloff": "0.35"},
    )
    w.close()


def test_main_window_off_restores_standard_preview(monkeypatch):
    w = MainWindow()
    w.tabs.setCurrentIndex(1)
    digital_calls = []
    clear_calls = []
    params_calls = []
    monkeypatch.setattr(
        w.status_panel.preview, "set_digital",
        lambda *args, **kwargs: digital_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        w.status_panel.preview, "clear_digital",
        lambda *args, **kwargs: clear_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        w.status_panel.preview, "set_params",
        lambda *args, **kwargs: params_calls.append((args, kwargs)),
    )

    w._on_digmod_state("QPSK")
    w._on_digmod_state("OFF")

    assert digital_calls
    assert clear_calls
    assert params_calls


def test_main_window_hardware_controls_locked_until_ready():
    w = MainWindow()

    assert not w.ch1.wave_btns["SINE"].isEnabled()
    assert not w.ch2.ampl.isEnabled()
    assert not w.dig.mode_combo.isEnabled()

    w.ctrl.connected.emit()
    assert w.ch1.wave_btns["SINE"].isEnabled()
    assert w.ch2.ampl.isEnabled()
    assert w.dig.mode_combo.isEnabled()

    w.ctrl.disconnected.emit("lost")
    assert not w.ch1.wave_btns["SINE"].isEnabled()
    assert not w.ch2.ampl.isEnabled()
    assert not w.dig.mode_combo.isEnabled()
    w.close()


def test_connection_bar_shows_connecting_state():
    from siggen.ui.widgets import ConnectionBar

    bar = ConnectionBar()
    bar.set_connecting(True)
    assert bar.btn.isEnabled() is False
    assert bar.status.text() == "连接中"
    bar.set_connected(True)
    assert bar.btn.isEnabled() is True
    assert bar.status.text() == "已连接"


def test_main_window_wires_mode_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "siggen.controller.Controller.set_digmod",
        lambda _self, mode: calls.append(mode),
    )
    w = MainWindow()
    w.dig.mode_combo.setCurrentText("QAM16")
    w.dig.mode_combo.setCurrentText("OFF")
    assert calls == ["QAM16", "OFF"]


def test_main_window_wires_symrate_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "siggen.controller.Controller.set_digmod_symrate",
        lambda _self, value: calls.append(value),
    )
    w = MainWindow()
    w.ctrl.connected.emit()
    w.dig.symrate_spin.setValue(250000)
    w.dig.symrate_btn.click()
    assert calls == [250000]


def test_digital_panel_filter_defaults_and_emits(dig_panel):
    assert dig_panel.filter_combo.currentText() == "RECT"
    got = []
    dig_panel.filter_requested.connect(got.append)
    dig_panel.filter_combo.setCurrentText("RRC")
    assert got == ["RRC"]


def test_digital_panel_confirmed_filter_locks_while_running(dig_panel):
    dig_panel.set_confirmed("QPSK", "PRBS7", 100000, True, filter_name="RRC")
    assert dig_panel.filter_combo.currentText() == "RRC"
    assert dig_panel.filter_combo.isEnabled() is False
    assert "RRC" in dig_panel.note.text()
    assert "全部模式" in dig_panel.filter_hint.text()
    dig_panel.set_confirmed("OFF", "PRBS7", 100000, False, filter_name="RRC")
    assert dig_panel.filter_combo.isEnabled() is True


def test_digital_panel_rolloff_defaults_and_emits(dig_panel):
    assert dig_panel.rolloff_combo.currentText() == "0.35"
    got = []
    dig_panel.rolloff_requested.connect(got.append)
    dig_panel.rolloff_combo.setCurrentText("0.5")
    assert got == ["0.5"]


def test_digital_panel_rolloff_only_enabled_for_stopped_rrc(dig_panel):
    dig_panel.set_confirmed("QPSK", "PRBS7", 100000, False,
                            filter_name="RECT", rolloff="0.5")
    assert dig_panel.rolloff_combo.currentText() == "0.5"
    assert dig_panel.rolloff_combo.isEnabled() is False

    dig_panel.set_confirmed("QPSK", "PRBS7", 100000, False,
                            filter_name="RRC", rolloff="0.2")
    assert dig_panel.rolloff_combo.isEnabled() is True

    dig_panel.set_confirmed("QPSK", "PRBS7", 100000, True,
                            filter_name="RRC", rolloff="0.2")
    assert dig_panel.rolloff_combo.isEnabled() is False


def test_digital_panel_filter_hint_supports_all_modes(dig_panel):
    assert "全部模式" in dig_panel.filter_hint.text()


def test_main_window_wires_filter_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "siggen.controller.Controller.set_digmod_filter",
        lambda _self, value: calls.append(value),
    )
    w = MainWindow()
    w.dig.filter_combo.setCurrentText("RRC")
    assert calls == ["RRC"]
    w.close()


def test_main_window_wires_rolloff_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "siggen.controller.Controller.set_digmod_rolloff",
        lambda _self, value: calls.append(value),
    )
    w = MainWindow()
    w.dig.rolloff_combo.setCurrentText("0.5")
    assert calls == ["0.5"]
    w.close()




def test_digital_panel_play_defaults_and_actions(dig_panel):
    assert dig_panel.play_mode_combo.currentText() == "LEGACY"
    assert dig_panel.play_repeat_spin.value() == 0
    assert dig_panel.play_trigger_combo.currentText() == "SOFTWARE"

    got = []
    dig_panel.play_mode_requested.connect(lambda value: got.append(("mode", value)))
    dig_panel.play_repeat_requested.connect(lambda value: got.append(("repeat", value)))
    dig_panel.play_trigger_requested.connect(lambda value: got.append(("trigger", value)))
    dig_panel.play_arm_requested.connect(lambda: got.append(("arm", None)))
    dig_panel.play_start_requested.connect(lambda: got.append(("start", None)))
    dig_panel.play_stop_requested.connect(lambda: got.append(("stop", None)))
    dig_panel.play_clear_requested.connect(lambda: got.append(("clear", None)))

    dig_panel.set_confirmed("QPSK", "PRBS7", 10000, False)
    dig_panel.set_play_status({"state": "IDLE", "repeat_remaining": 0})
    dig_panel.play_mode_combo.setCurrentText("MANUAL")
    dig_panel.play_repeat_spin.setValue(2)
    dig_panel.play_trigger_combo.setCurrentText("EXTERNAL")
    dig_panel.play_arm_btn.click()
    dig_panel.set_play_status({"state": "ARMED", "repeat_remaining": 2})
    dig_panel.play_start_btn.click()
    dig_panel.set_play_status({"state": "RUNNING", "repeat_remaining": 2})
    dig_panel.play_stop_btn.click()
    dig_panel.play_clear_btn.click()

    assert got == [("mode", "MANUAL"), ("repeat", 2), ("trigger", "EXTERNAL"),
                   ("arm", None), ("start", None), ("stop", None), ("clear", None)]




def test_digital_panel_play_status_and_disconnect_lock(dig_panel):
    status = {
        "state": "RUNNING", "frame_done": False, "underflow": False,
        "trigger_seen": True, "stop_pending": False, "error_valid": False,
        "repeat_remaining": 2, "error_code": 0,
    }
    dig_panel.set_play_status(status)
    assert "RUNNING" in dig_panel.play_status.text()
    assert "2" in dig_panel.play_status.text()
    assert dig_panel.play_start_btn.isEnabled() is False

    dig_panel.set_hardware_enabled(False)
    assert not dig_panel.play_mode_combo.isEnabled()
    assert not dig_panel.play_arm_btn.isEnabled()


def test_main_window_wires_play_controls_and_status(monkeypatch):
    calls = []
    for name in ("set_play_mode", "set_play_repeat", "set_play_trigger"):
        monkeypatch.setattr(
            getattr(__import__("siggen.controller", fromlist=["Controller"]), "Controller"),
            name, lambda _self, value, n=name: calls.append((n, value)))
    for name in ("play_arm", "play_start", "play_stop", "play_clear"):
        monkeypatch.setattr(
            "siggen.controller.Controller." + name,
            lambda _self, n=name: calls.append((n, None)))

    w = MainWindow()
    w.ctrl.connected.emit()
    w.dig.set_confirmed("QPSK", "PRBS7", 10000, False)
    w.dig.set_play_status({"state": "IDLE", "repeat_remaining": 0})
    w.dig.play_mode_combo.setCurrentText("MANUAL")
    w.dig.play_repeat_spin.setValue(3)
    w.dig.play_trigger_combo.setCurrentText("SOFTWARE")
    w.dig.play_arm_btn.click()
    w.ctrl.play_changed.emit({"state": "ARMED", "repeat_remaining": 3})
    w.dig.play_start_btn.click()
    w.ctrl.play_changed.emit({"state": "RUNNING", "repeat_remaining": 3})
    w.dig.play_stop_btn.click()
    w.dig.play_clear_btn.click()

    assert calls == [("set_play_mode", "MANUAL"), ("set_play_repeat", 3),
                     ("play_arm", None), ("play_start", None),
                     ("play_stop", None), ("play_clear", None)]
    assert "RUNNING" in w.dig.play_status.text()
    w.close()


def _ddr_status(active=False, active_bank=None, next_bank=None, state="IDLE",
                error=0, valid=(False, False), lengths=(0, 0)):
    return {
        "active": active, "active_bank": active_bank, "next_bank": next_bank,
        "state": state, "error_code": error,
        "bank0": {"valid": bool(valid[0]), "bit_length": lengths[0], "crc16": 1},
        "bank1": {"valid": bool(valid[1]), "bit_length": lengths[1], "crc16": 2},
    }


def _make_files(tmp_path, count):
    paths = []
    for i in range(count):
        path = tmp_path / f"w{i}.bin"
        path.write_bytes(bytes([i]) * (16 * (i + 1)))
        paths.append(str(path))
    return paths


def test_digital_panel_ddr_stream_defaults(dig_panel):
    assert dig_panel.ddr_stream_bank_combo.currentText() == "0"
    assert dig_panel.ddr_stream_paths() == []
    assert dig_panel.ddr_stream_ring_check.isChecked() is False
    assert dig_panel.ddr_stream_add_btn.isEnabled() is True      # MODE OFF + hw on
    assert dig_panel.ddr_stream_upload_btn.isEnabled() is False  # empty queue
    assert dig_panel.ddr_stream_remove_btn.isEnabled() is False
    assert dig_panel.ddr_stream_clear_btn.isEnabled() is False
    assert dig_panel.ddr_stream_start_btn.isEnabled() is False   # MODE OFF
    assert dig_panel.ddr_stream_stop_btn.isEnabled() is False


def test_digital_panel_ddr_stream_queue_add_remove_clear(tmp_path, dig_panel):
    paths = _make_files(tmp_path, 2)
    dig_panel.add_ddr_stream_files(paths)
    assert dig_panel.ddr_stream_paths() == paths
    assert dig_panel.ddr_stream_list.count() == 2
    assert "2" in dig_panel.ddr_stream_queue_label.text()
    assert dig_panel.ddr_stream_upload_btn.isEnabled() is True
    assert dig_panel.ddr_stream_remove_btn.isEnabled() is True

    dig_panel.ddr_stream_list.setCurrentRow(0)
    dig_panel.ddr_stream_remove_btn.click()
    assert dig_panel.ddr_stream_paths() == [paths[1]]

    dig_panel.ddr_stream_clear_btn.click()
    assert dig_panel.ddr_stream_paths() == []
    assert dig_panel.ddr_stream_upload_btn.isEnabled() is False


def test_digital_panel_ddr_stream_upload_emits_path_and_bank(tmp_path, dig_panel):
    got = []
    dig_panel.ddr_stream_upload_requested.connect(lambda p, b: got.append((p, b)))
    paths = _make_files(tmp_path, 2)
    dig_panel.add_ddr_stream_files(paths)
    dig_panel.ddr_stream_list.setCurrentRow(1)
    dig_panel.ddr_stream_bank_combo.setCurrentText("1")
    dig_panel.ddr_stream_upload_btn.click()
    assert got == [(paths[1], 1)]


def test_digital_panel_ddr_stream_start_stop_emit(dig_panel):
    got = []
    dig_panel.ddr_stream_start_requested.connect(lambda: got.append("start"))
    dig_panel.ddr_stream_stop_requested.connect(lambda: got.append("stop"))
    dig_panel.set_confirmed("QAM16", "PRBS7", 100000, False)
    dig_panel.set_ddr_stream_status(_ddr_status(valid=(True, False), lengths=(128, 0)))
    dig_panel.ddr_stream_start_btn.click()
    dig_panel.set_ddr_stream_status(
        _ddr_status(active=True, active_bank=0, state="RUNNING",
                    valid=(True, False), lengths=(128, 0)))
    dig_panel.ddr_stream_stop_btn.click()
    assert got == ["start", "stop"]


def test_digital_panel_ddr_stream_controls_follow_mode_and_validity(tmp_path, dig_panel):
    dig_panel.add_ddr_stream_files(_make_files(tmp_path, 1))
    assert dig_panel.ddr_stream_start_btn.isEnabled() is False   # MODE OFF
    dig_panel.set_confirmed("QAM16", "PRBS7", 100000, False)
    dig_panel.set_ddr_stream_status(_ddr_status(valid=(True, False), lengths=(128, 0)))
    assert dig_panel.ddr_stream_start_btn.isEnabled() is True
    assert dig_panel.ddr_stream_add_btn.isEnabled() is False     # queue edit needs MODE OFF
    dig_panel.set_ddr_stream_status(_ddr_status(valid=(False, False)))
    assert dig_panel.ddr_stream_start_btn.isEnabled() is False    # no valid bank
    dig_panel.set_hardware_enabled(False)
    assert not dig_panel.ddr_stream_add_btn.isEnabled()
    assert not dig_panel.ddr_stream_start_btn.isEnabled()
    assert not dig_panel.ddr_stream_stop_btn.isEnabled()


def test_digital_panel_ddr_stream_progress_and_status(dig_panel):
    dig_panel.set_ddr_stream_progress(40, 100)
    assert dig_panel.ddr_stream_progress.value() == 40
    dig_panel.set_ddr_stream_progress(90)
    assert dig_panel.ddr_stream_progress.value() == 90
    dig_panel.set_ddr_stream_status(
        _ddr_status(active=True, active_bank=1, state="RUNNING",
                    valid=(True, True), lengths=(128, 256)))
    text = dig_panel.ddr_stream_status.text()
    assert "RUNNING" in text and "1" in text and "32 B" in text


def test_digital_panel_ddr_stream_ring_feeds_non_active_bank(tmp_path, dig_panel):
    paths = _make_files(tmp_path, 2)
    dig_panel.add_ddr_stream_files(paths)
    feeds = []
    dig_panel.ddr_stream_feed_requested.connect(lambda p, b: feeds.append((p, b)))
    dig_panel.set_confirmed("QAM16", "PRBS7", 100000, False)
    dig_panel.ddr_stream_ring_check.setChecked(True)

    running0 = _ddr_status(active=True, active_bank=0, state="RUNNING",
                           valid=(True, True), lengths=(128, 128))
    dig_panel.set_ddr_stream_status(running0)
    assert feeds == [(paths[0], 1)]           # feed queue[0] to the idle bank

    dig_panel.set_ddr_stream_status(running0)  # upload still in flight
    assert feeds == [(paths[0], 1)]

    dig_panel.set_ddr_stream_upload_done()
    dig_panel.set_ddr_stream_status(running0)
    assert feeds == [(paths[0], 1), (paths[1], 1)]

    dig_panel.set_ddr_stream_upload_done()
    dig_panel.set_ddr_stream_status(
        _ddr_status(active=True, active_bank=1, state="RUNNING",
                    valid=(True, True), lengths=(128, 128)))
    assert feeds == [(paths[0], 1), (paths[1], 1), (paths[0], 0)]


def test_digital_panel_ddr_stream_manual_preload_skips_ring_cursor(tmp_path, dig_panel):
    paths = _make_files(tmp_path, 3)
    dig_panel.add_ddr_stream_files(paths)
    feeds = []
    dig_panel.ddr_stream_feed_requested.connect(lambda p, b: feeds.append((p, b)))
    dig_panel.ddr_stream_list.setCurrentRow(0)
    dig_panel.ddr_stream_bank_combo.setCurrentText("0")
    dig_panel.ddr_stream_upload_btn.click()          # 手动预装 queue[0] 到 bank0
    dig_panel.set_confirmed("QAM16", "PRBS7", 100000, False)
    dig_panel.ddr_stream_ring_check.setChecked(True)  # 之后才开环形

    dig_panel.set_ddr_stream_status(
        _ddr_status(active=True, active_bank=0, state="RUNNING",
                    valid=(True, True), lengths=(128, 128)))
    assert feeds == [(paths[1], 1)]   # 不重复喂已预装的 queue[0]


def test_digital_panel_ddr_stream_ring_off_never_feeds(tmp_path, dig_panel):
    dig_panel.add_ddr_stream_files(_make_files(tmp_path, 1))
    feeds = []
    dig_panel.ddr_stream_feed_requested.connect(lambda p, b: feeds.append((p, b)))
    dig_panel.set_confirmed("QAM16", "PRBS7", 100000, False)
    dig_panel.set_ddr_stream_status(
        _ddr_status(active=True, active_bank=0, state="RUNNING",
                    valid=(True, True), lengths=(128, 128)))
    assert feeds == []


def test_digital_panel_ddr_stream_ring_timer_polls_while_playing(tmp_path, dig_panel):
    dig_panel.add_ddr_stream_files(_make_files(tmp_path, 1))
    polls = []
    dig_panel.ddr_stream_poll_requested.connect(lambda: polls.append(True))
    dig_panel.set_confirmed("QAM16", "PRBS7", 100000, False)
    dig_panel.ddr_stream_ring_check.setChecked(True)
    assert dig_panel._ring_timer.isActive() is False   # not playing yet

    dig_panel.set_ddr_stream_status(
        _ddr_status(active=True, active_bank=0, state="RUNNING",
                    valid=(True, True), lengths=(128, 128)))
    assert dig_panel._ring_timer.isActive() is True
    dig_panel._on_ring_tick()
    assert polls == [True]

    dig_panel.set_ddr_stream_status(_ddr_status(valid=(True, True), lengths=(128, 128)))
    assert dig_panel._ring_timer.isActive() is False
    dig_panel.ddr_stream_ring_check.setChecked(False)


def test_digital_panel_ddr_stream_disconnect_stops_ring_and_clears_status(tmp_path, dig_panel):
    dig_panel.add_ddr_stream_files(_make_files(tmp_path, 1))
    dig_panel.set_confirmed("QAM16", "PRBS7", 100000, False)
    dig_panel.ddr_stream_ring_check.setChecked(True)
    dig_panel.set_ddr_stream_status(
        _ddr_status(active=True, active_bank=0, state="RUNNING",
                    valid=(True, True), lengths=(128, 128)))
    assert dig_panel._ring_timer.isActive() is True

    dig_panel.set_hardware_enabled(False)

    assert dig_panel._ring_timer.isActive() is False
    assert dig_panel._ddr_status is None
    assert "RUNNING" not in dig_panel.ddr_stream_status.text()


def test_main_window_wires_ddr_stream_controls(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "siggen.controller.Controller.upload_ddr_pp_data",
        lambda _self, bank, data, *a, **k: calls.append(("upload", bank, data)),
        raising=False)
    monkeypatch.setattr(
        "siggen.controller.Controller.ddr_pp_start",
        lambda _self: calls.append(("start", None, None)), raising=False)
    monkeypatch.setattr(
        "siggen.controller.Controller.ddr_pp_stop",
        lambda _self: calls.append(("stop", None, None)), raising=False)
    w = MainWindow()
    w.ctrl.connected.emit()
    paths = _make_files(tmp_path, 1)
    w.dig.add_ddr_stream_files(paths)
    w.dig.ddr_stream_bank_combo.setCurrentText("1")
    w.dig.ddr_stream_upload_btn.click()
    w.ctrl.ddr_pp_upload_progress.emit(10, 16)
    assert w.dig.ddr_stream_progress.value() == 62
    w.ctrl.ddr_pp_changed.emit(_ddr_status(valid=(False, True), lengths=(0, 128)))
    assert "IDLE" in w.dig.ddr_stream_status.text()
    w.dig.set_confirmed("QAM16", "PRBS7", 100000, False)
    w.dig.ddr_stream_start_btn.click()
    w.ctrl.ddr_pp_changed.emit(
        _ddr_status(active=True, active_bank=1, state="RUNNING",
                    valid=(False, True), lengths=(0, 128)))
    w.dig.ddr_stream_stop_btn.click()
    assert calls == [("upload", 1, paths[0]), ("start", None, None),
                     ("stop", None, None)]
    w.close()
