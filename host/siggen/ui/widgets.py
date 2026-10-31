"""界面组件:连接栏 / 通道面板 / 状态面板。"""
import os

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QProgressBar, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ..protocol import WAVES, MODES, MODWAVES, parse_freq_input, parse_modfreq_input
from ..ring_feeder import RingFeeder

PORTS = [f"COM{i}" for i in range(1, 17)]
BAUDS = ["9600", "115200", "460800"]


class ConnectionBar(QWidget):
    connect_requested = Signal(str, int)
    disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.port = QComboBox()
        self.port.addItems(PORTS)
        self.baud = QComboBox()
        self.baud.addItems(BAUDS)
        self.baud.setCurrentText("115200")
        self.btn = QPushButton("连接")
        self.status = QLabel("未连接")
        lay = QHBoxLayout(self)
        lay.addWidget(QLabel("串口"))
        lay.addWidget(self.port)
        lay.addWidget(QLabel("波特率"))
        lay.addWidget(self.baud)
        lay.addWidget(self.btn)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.btn.clicked.connect(self._on_click)

    def _on_click(self):
        if self.btn.text() == "连接":
            self.connect_requested.emit(self.port.currentText(), int(self.baud.currentText()))
        else:
            self.disconnect_requested.emit()

    def set_connected(self, on: bool):
        self.btn.setEnabled(True)
        self.port.setEnabled(True)
        self.baud.setEnabled(True)
        self.btn.setText("断开" if on else "连接")
        self.status.setText("已连接" if on else "未连接")
        self.status.setStyleSheet("color: green;" if on else "color: gray;")

    def set_connecting(self, on: bool):
        if on:
            self.btn.setEnabled(False)
            self.port.setEnabled(False)
            self.baud.setEnabled(False)
            self.btn.setText("连接中")
            self.status.setText("连接中")
            self.status.setStyleSheet("color: orange;")
        else:
            self.set_connected(False)


class ChannelPanel(QWidget):
    """单通道控制:波形按钮 + 频率步进/输入 + 幅度滑块。"""
    param_changed = Signal(str, object)   # (field, value)

    def __init__(self, title: str, parent=None, phase_link_enabled=False):
        super().__init__(parent)
        self._freq_hz = 1000
        self._phase_link_enabled = phase_link_enabled
        self._updating = True   # 构建期间屏蔽控件信号,末尾复位
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel(title))

        # 波形按钮
        wave_lay = QHBoxLayout()
        self.wave_btns = {}
        for w in WAVES:
            b = QPushButton(w.capitalize())
            b.setCheckable(True)
            b.setAutoExclusive(True)
            # checked 兜底参数:兼容 PySide6 各版本 clicked(0 参)与 clicked(bool) 两种签名
            b.clicked.connect(lambda checked=False, ww=w: self._on_wave_selected(ww))
            self.wave_btns[w] = b
            wave_lay.addWidget(b)
        lay.addLayout(wave_lay)

        # 频率
        self.freq_label = QLabel("1000 Hz")
        self.freq_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        lay.addWidget(self.freq_label)
        freq_lay = QHBoxLayout()
        self.freq_buttons = []
        for text, op in (("-10", -10), ("+10", 10), ("÷10", "div"), ("×10", "mul")):
            b = QPushButton(text)
            b.clicked.connect(lambda checked=False, o=op: self._step(o))
            self.freq_buttons.append(b)
            freq_lay.addWidget(b)
        self.freq_edit = QLineEdit()
        self.freq_edit.setPlaceholderText("Hz")
        self.freq_edit.returnPressed.connect(self._typed)
        freq_lay.addWidget(self.freq_edit)
        lay.addLayout(freq_lay)

        if phase_link_enabled:
            self.phase_link = QCheckBox("跟随 CH1（相位联动）")
            self.phase_link.toggled.connect(self._on_phase_link_changed)
            lay.addWidget(self.phase_link)

            phase_lay = QHBoxLayout()
            phase_lay.addWidget(QLabel("相位"))
            self.phase_slider = QSlider(Qt.Horizontal)
            self.phase_slider.setRange(0, 36000)
            self.phase_slider.setValue(0)
            self.phase_slider.valueChanged.connect(self._on_phase_changed)
            self.phase_label = QLabel("0.00°")
            phase_lay.addWidget(self.phase_slider, 1)
            phase_lay.addWidget(self.phase_label)
            lay.addLayout(phase_lay)

        # 幅度
        self.ampl = QSlider(Qt.Horizontal)
        self.ampl.setRange(0, 100)
        self.ampl.setValue(50)
        self.ampl.valueChanged.connect(lambda v: self.param_changed.emit("ampl", v))
        self.ampl_label = QLabel("50%")
        ampl_lay = QHBoxLayout()
        ampl_lay.addWidget(QLabel("幅度"))
        ampl_lay.addWidget(self.ampl)
        ampl_lay.addWidget(self.ampl_label)
        lay.addLayout(ampl_lay)

        pulse_lay = QHBoxLayout()
        pulse_lay.addWidget(QLabel("脉冲占空比"))
        self.pulse_duty = QSlider(Qt.Horizontal)
        self.pulse_duty.setRange(0, 100)
        self.pulse_duty.setValue(50)
        self.pulse_duty.valueChanged.connect(
            lambda v: self._emit_pulse_duty(v))
        self.pulse_duty_label = QLabel("50%")
        pulse_lay.addWidget(self.pulse_duty, 1)
        pulse_lay.addWidget(self.pulse_duty_label)
        lay.addLayout(pulse_lay)

        # ---- 谐波区 ----
        harm_box = QVBoxLayout()
        self.norm_hint = QLabel("")          # 归一化提示
        self.norm_hint.setStyleSheet("color: orange;")
        harm_box.addWidget(self.norm_hint)

        base_lay = QHBoxLayout()
        base_lay.addWidget(QLabel("基波"))
        self.base_slider = QSlider(Qt.Horizontal)
        self.base_slider.setRange(0, 100)
        self.base_slider.setValue(100)
        self.base_slider.valueChanged.connect(
            lambda v: self._emit_harm_if_user("base", 0, v))
        self.base_label = QLabel("100%")
        base_lay.addWidget(self.base_slider, 1)
        base_lay.addWidget(self.base_label)
        harm_box.addLayout(base_lay)

        self.harm_ord = []
        self.harm_ampl = []
        self.harm_ampl_labels = []
        for i in range(4):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"谐波{i + 1}"))
            combo = QComboBox()
            combo.addItems(["关"] + [str(o) for o in range(2, 9)])
            combo.currentIndexChanged.connect(
                lambda idx, hh=i: self._emit_harm_if_user("order", hh, idx))
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setEnabled(False)
            slider.valueChanged.connect(
                lambda v, hh=i: self._emit_harm_if_user("ampl", hh, v))
            lab = QLabel("0%")
            row.addWidget(combo)
            row.addWidget(slider, 1)
            row.addWidget(lab)
            self.harm_ord.append(combo)
            self.harm_ampl.append(slider)
            self.harm_ampl_labels.append(lab)
            harm_box.addLayout(row)
        lay.addLayout(harm_box)

        # ---- 调制区 ----
        mod_box = QVBoxLayout()
        mod_row1 = QHBoxLayout()
        mod_row1.addWidget(QLabel("调制"))
        self.mod_mode = QComboBox()
        self.mod_mode.addItems(["关", "AM", "FM", "PM", "扫频"])
        self.mod_mode.currentIndexChanged.connect(
            lambda idx: self._emit_mod("mode", MODES[idx]))
        mod_row1.addWidget(self.mod_mode)
        self.mod_wave = QComboBox()
        self.mod_wave.addItems(["正弦", "方波", "三角", "锯齿"])
        self.mod_wave.currentIndexChanged.connect(
            lambda idx: self._emit_mod("mwave", MODWAVES[idx]))
        mod_row1.addWidget(self.mod_wave)
        mod_box.addLayout(mod_row1)

        mod_row2 = QHBoxLayout()
        mod_row2.addWidget(QLabel("频率"))
        self.mod_freq = QLineEdit()
        self.mod_freq.setPlaceholderText("Hz (0.1~50k)")
        self.mod_freq.returnPressed.connect(self._mod_freq_typed)
        self.mod_freq_label = QLabel("1 Hz")
        mod_row2.addWidget(self.mod_freq)
        mod_row2.addWidget(self.mod_freq_label)
        mod_row2.addWidget(QLabel("AM深度"))
        self.am_depth = QSlider(Qt.Horizontal)
        self.am_depth.setRange(0, 100)
        self.am_depth.setValue(100)
        self.am_depth.valueChanged.connect(
            lambda v: self._emit_mod("amdepth", v))
        self.am_depth_label = QLabel("100%")
        mod_row2.addWidget(self.am_depth, 1)
        mod_row2.addWidget(self.am_depth_label)
        mod_box.addLayout(mod_row2)

        mod_row3 = QHBoxLayout()
        self.fm_dev_caption = QLabel("FM频偏")
        mod_row3.addWidget(self.fm_dev_caption)
        self.fm_dev = QSlider(Qt.Horizontal)
        self.fm_dev.setRange(0, 500000)
        self.fm_dev.valueChanged.connect(
            lambda v: self._emit_mod("fmdev", v))
        self.fm_dev_label = QLabel("0 Hz")
        mod_row3.addWidget(self.fm_dev, 1)
        mod_row3.addWidget(self.fm_dev_label)
        mod_row3.addWidget(QLabel("PM相位"))
        self.pm_dev = QSlider(Qt.Horizontal)
        self.pm_dev.setRange(0, 128)
        self.pm_dev.valueChanged.connect(
            lambda v: self._emit_mod("pmdev", v))
        self.pm_dev_label = QLabel("0°")
        mod_row3.addWidget(self.pm_dev, 1)
        mod_row3.addWidget(self.pm_dev_label)
        mod_box.addLayout(mod_row3)
        self.mod_hint = QLabel("")
        self.mod_hint.setStyleSheet("color: orange;")
        mod_box.addWidget(self.mod_hint)
        lay.addLayout(mod_box)

        # ---- Vpp 显示与校准 ----
        vpp_lay = QHBoxLayout()
        self.vpp_label = QLabel("≈ -- Vpp")
        self.vpp_label.setStyleSheet("color: blue; font-weight: bold;")
        vpp_lay.addWidget(self.vpp_label)
        vpp_lay.addStretch(1)
        lay.addLayout(vpp_lay)

        set_lay = QHBoxLayout()
        set_lay.addWidget(QLabel("按 Vpp 设置"))
        self.vpp_edit = QLineEdit()
        self.vpp_edit.setPlaceholderText("V")
        self.vpp_edit.returnPressed.connect(self._on_vpp_typed)
        self.cal_edit = QDoubleSpinBox()
        self.cal_edit.setRange(0.1, 20.0)
        self.cal_edit.setDecimals(2)
        self.cal_edit.setValue(9.50)
        self.cal_edit.setSuffix(" V")
        self.cal_edit.setToolTip(
            "满摆幅(100%)Vpp 校准常数\n"
            "用示波器实测 100% 方波 10kHz 的 Vpp 填入(出厂默认 9.50V(示波器实测))\n"
            "各百分比控件旁的电压值均按此常数换算")
        self.cal_edit.valueChanged.connect(lambda _=None: self._update_vpp())
        set_lay.addWidget(self.vpp_edit)
        set_lay.addWidget(QLabel("校准"))
        set_lay.addWidget(self.cal_edit)
        lay.addLayout(set_lay)
        # 校准换算提示: 满摆幅电压 + 1% 对应电压(随校准/幅度联动)
        self.cal_hint = QLabel("")
        self.cal_hint.setStyleSheet("color: gray; font-size: 10px;")
        lay.addWidget(self.cal_hint)

        self._mod_freq_01 = 10
        self._updating = False
        self._update_enabled()

    def _is_phase_linked(self):
        return self._phase_link_enabled and self.phase_link.isChecked()

    def _on_wave_selected(self, wave):
        if not self._updating:
            self.param_changed.emit("wave", wave)
        self._update_enabled()

    def _emit_pulse_duty(self, value):
        self.pulse_duty_label.setText(f"{value}%")
        if not self._updating:
            self.param_changed.emit("duty", value)

    def _update_pulse_enabled(self):
        enabled = self.wave_btns["PULSE"].isChecked()
        self.pulse_duty.setEnabled(enabled)
        self.pulse_duty_label.setEnabled(enabled)

    def _on_phase_changed(self, value):
        self.phase_label.setText(f"{value / 100.0:.2f}°")
        if not self._updating:
            self.param_changed.emit("phase", value)

    def _on_phase_link_changed(self, checked):
        if self._updating:
            return
        self._update_enabled()
        self.param_changed.emit("phaselink", checked)

    def _update_enabled(self):
        linked = self._is_phase_linked()
        for button in self.freq_buttons:
            button.setEnabled(not linked)
        self.freq_edit.setEnabled(not linked)
        self._update_pulse_enabled()
        self._update_mod_enabled()

    def _step(self, op):
        if op == "mul":
            self.param_changed.emit("freq", self._freq_hz * 10)
        elif op == "div":
            self.param_changed.emit("freq", self._freq_hz // 10)
        else:
            self.param_changed.emit("freq", self._freq_hz + op)

    def _typed(self):
        try:
            v = parse_freq_input(self.freq_edit.text())
        except ValueError:
            return
        self.param_changed.emit("freq", v)
        self.freq_edit.clear()

    def _emit_harm_if_user(self, kind, idx, value):
        """谐波控件用户操作 -> param_changed;程序化更新(_updating)时忽略。"""
        if self._updating:
            return
        if kind == "base":
            self.param_changed.emit("base", value)
        elif kind == "order":
            # value 为下拉 index:0=关,1..7 = 次数 2..8
            order = 0 if value == 0 else value + 1
            self.param_changed.emit(f"h{idx + 1}_order", order)
        else:
            self.param_changed.emit(f"h{idx + 1}_ampl", value)
        self._update_norm_hint()
        self._update_volt_labels()

    def _update_norm_hint(self):
        base = self.base_slider.value()
        total = base + sum(self.harm_ampl[i].value() for i in range(4))
        if total > 100:
            self.norm_hint.setText(f"已自动归一化(总幅 {total}% → 100%)")
        else:
            self.norm_hint.setText("")

    def _emit_mod(self, field, value):
        if self._updating:
            return
        if field == "mode" and value == "SWEEP":
            # 扫频预设三角波(仅当调制波形还是默认正弦时); 显式改过则尊重用户选择
            if self.mod_wave.currentIndex() == 0:
                self.mod_wave.blockSignals(True)
                self.mod_wave.setCurrentIndex(2)     # 三角
                self.mod_wave.blockSignals(False)
                self.param_changed.emit("mwave", "TRIANGLE")
        self.param_changed.emit(field, value)
        self._update_mod_enabled()
        self._update_mod_labels()

    def _update_mod_enabled(self):
        """OFF 模式下调制参数置灰(与硬件一致: 调制被抑制)。"""
        linked = self._is_phase_linked()
        on = not linked and self.mod_mode.currentIndex() != 0
        self.mod_mode.setEnabled(not linked)
        for w in (self.mod_wave, self.mod_freq, self.am_depth,
                  self.fm_dev, self.pm_dev):
            w.setEnabled(on)
        self.fm_dev_caption.setEnabled(on)
        self.fm_dev_label.setEnabled(on)
        self.am_depth_label.setEnabled(on)
        self.pm_dev_label.setEnabled(on)

    def _mod_freq_typed(self):
        try:
            v = parse_modfreq_input(self.mod_freq.text())
        except ValueError:
            return
        self._mod_freq_01 = v
        self._emit_mod("modfreq", v)
        self.mod_freq.clear()

    def _update_mod_labels(self):
        self.mod_freq_label.setText(f"{self._mod_freq_01 / 10.0:g} Hz")
        sweep = self.mod_mode.currentIndex() == 4
        self.fm_dev_caption.setText("扫频跨度" if sweep else "FM频偏")
        self.fm_dev_label.setText(f"{self.fm_dev.value()} Hz")
        self.pm_dev_label.setText(f"{int(round(self.pm_dev.value() * 1.40625))}°")
        self._update_mod_hint()
        self._update_volt_labels()

    def _update_mod_hint(self):
        # AM 深度与幅度乘积超过满摆幅会削波(am_stage 在 ampl_scale 之前饱和)
        if self.mod_mode.currentIndex() == 1 and (100 + self.am_depth.value()) * self.ampl.value() > 10000:
            self.mod_hint.setText("AM 深度 + 幅度超过 100% 会削波, 请降低 AMPL")
        else:
            self.mod_hint.setText("")

    def _component_volt(self, pct):
        """分量百分比 -> 输出 Vpp(硬件截断模型):
        满摆幅 127 LSB, ampl_scale 整数截断 trunc(127×pct/100) 与 RTL 一致。
        电压 = 截断 LSB / 127 × 校准常数 × AMPL%。"""
        lsb = 127 * pct // 100
        return lsb / 127.0 * self.cal_edit.value() * self.ampl.value() / 100.0

    def _update_volt_labels(self):
        """各百分比控件旁显示换算电压(基于校准常数 × AMPL, 随任一联动更新)。"""
        self.base_label.setText(
            f"{self.base_slider.value()}% = {self._component_volt(self.base_slider.value()):.2f}V")
        for i in range(4):
            a = self.harm_ampl[i].value()
            self.harm_ampl_labels[i].setText(f"{a}% = {self._component_volt(a):.2f}V")
        # AM 深度: 包络峰值电压 = (1+d/100)×AMPL×校准; 超过满摆幅提示削波
        d = self.am_depth.value()
        fs_lsb = 127 * self.ampl.value() // 100      # 硬件截断模型满摆幅
        fs = fs_lsb / 127.0 * self.cal_edit.value()
        peak = (1 + d / 100.0) * fs
        if peak > fs * 1.01:
            self.am_depth_label.setText(f"{d}% (峰 {peak:.2f}V 削波)")
        else:
            self.am_depth_label.setText(f"{d}% (峰 {peak:.2f}V)")
        self.cal_hint.setText(
            f"满摆幅 {fs:.2f} V · 1% = {fs / 100.0:.3f} V")

    def _update_vpp(self):
        pct = self.ampl.value()
        lsb = 127 * pct // 100
        vpp = lsb / 127.0 * self.cal_edit.value()
        self.vpp_label.setText(f"≈ {vpp:.2f} Vpp")
        self._update_volt_labels()

    def _on_vpp_typed(self):
        try:
            v = float(self.vpp_edit.text())
        except ValueError:
            return
        cal = self.cal_edit.value()
        v = min(max(v, 0.0), cal)          # 钳位到 [0, cal]
        self.ampl.setValue(int(round(v / cal * 100)))
        self.vpp_edit.clear()

    def set_hardware_enabled(self, enabled: bool) -> None:
        widgets = list(self.wave_btns.values()) + self.freq_buttons + [
            self.freq_edit, self.ampl, self.pulse_duty, self.base_slider,
            self.mod_mode, self.mod_wave, self.mod_freq, self.am_depth,
            self.fm_dev, self.pm_dev, self.vpp_edit,
        ]
        widgets += self.harm_ord + self.harm_ampl
        if self._phase_link_enabled:
            widgets += [self.phase_link, self.phase_slider]
        for widget in widgets:
            widget.setEnabled(enabled)
        if enabled:
            for combo, slider in zip(self.harm_ord, self.harm_ampl):
                slider.setEnabled(combo.currentIndex() != 0)
            self._update_enabled()

    def set_state(self, wave: str, freq_hz: int, ampl_pct: int,
                  base_pct: int = 100, harms=None,
                  mode: str = "OFF", mwave: str = "SINE", mfreq_01: int = 10,
                  am_depth: int = 100, fm_dev: int = 0, pm_dev: int = 0,
                  phase_0_01: int = 0, phaselink: bool = False,
                  pulse_duty: int = 50):
        self._updating = True
        self._freq_hz = freq_hz
        for w, b in self.wave_btns.items():
            b.setChecked(w == wave)
        self.freq_label.setText(f"{freq_hz} Hz")
        self.pulse_duty.blockSignals(True)
        self.pulse_duty.setValue(pulse_duty)
        self.pulse_duty.blockSignals(False)
        self.pulse_duty_label.setText(f"{pulse_duty}%")
        if self._phase_link_enabled:
            self.phase_slider.blockSignals(True)
            self.phase_slider.setValue(phase_0_01)
            self.phase_slider.blockSignals(False)
            self.phase_label.setText(f"{phase_0_01 / 100.0:.2f}°")
            self.phase_link.blockSignals(True)
            self.phase_link.setChecked(phaselink)
            self.phase_link.blockSignals(False)
        # 屏蔽信号:setValue 会触发 valueChanged -> 避免 ack 后回灌一条重复 SET
        self.ampl.blockSignals(True)
        self.ampl.setValue(ampl_pct)
        self.ampl.blockSignals(False)
        self.ampl_label.setText(f"{ampl_pct}%")
        self.base_slider.setValue(base_pct)
        # 板桥可能回读少于 4 条谐波,补齐避免索引越界
        harms = list(harms or []) + [(0, 0)] * 4
        for i in range(4):
            order, a = harms[i]
            self.harm_ord[i].setCurrentIndex(0 if order == 0 else order - 1)
            self.harm_ampl[i].setValue(a)
            self.harm_ampl[i].setEnabled(order != 0)
        self._mod_freq_01 = mfreq_01
        for combo, name, names in ((self.mod_mode, mode, MODES),
                                   (self.mod_wave, mwave, MODWAVES)):
            combo.blockSignals(True)
            combo.setCurrentIndex(names.index(name) if name in names else 0)
            combo.blockSignals(False)
        for slider, val in ((self.am_depth, am_depth),
                            (self.fm_dev, fm_dev),
                            (self.pm_dev, pm_dev)):
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
        self._update_enabled()
        self._update_mod_labels()
        self._updating = False
        self._update_norm_hint()
        self._update_vpp()


class StatusPanel(QWidget):
    """右栏:回读状态 + 波形预览 + 日志。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        from ..preview import WavePreview
        lay = QVBoxLayout(self)
        self.ch1_label = QLabel("CH1: --")
        self.ch2_label = QLabel("CH2: --")
        self.preview = WavePreview()
        self.log = QListWidget()
        lay.addWidget(QLabel("状态回读"))
        lay.addWidget(self.ch1_label)
        lay.addWidget(self.ch2_label)
        self.dig_view = QComboBox()
        self.dig_view.addItems(["时域", "星座", "对比"])
        self.dig_view.setVisible(False)
        lay.addWidget(self.dig_view)
        lay.addWidget(QLabel("波形预览"))
        lay.addWidget(self.preview, 1)
        lay.addWidget(QLabel("日志"))
        lay.addWidget(self.log, 1)

    def set_channel(self, ch: int, st):
        text = f"CH{ch + 1}: {st.wave} {st.freq_hz} Hz {st.ampl_pct}%"
        if getattr(st, "base_pct", None) is not None:
            text += f" B{st.base_pct}%"
        if ch == 1:
            text += f" 相位 {st.phase_0_01 / 100.0:.2f}°"
            text += " 联动开启" if st.phaselink else " 联动关闭"
        (self.ch1_label if ch == 0 else self.ch2_label).setText(text)

    def add_log(self, text: str, level: str = "info"):
        color = {"error": "red", "ok": "green"}.get(level, "black")
        self.log.addItem(text)
        item = self.log.item(self.log.count() - 1)
        item.setForeground(QColor(color))   # PySide6 需 QColor,字符串会 TypeError
        self.log.scrollToBottom()

    def set_dig_view_visible(self, on: bool) -> None:
        self.dig_view.setVisible(on)


class DigitalModPanel(QWidget):
    """共享数字调制面板: 模式 + PRBS7 数据源 + 可配置符号率。"""
    mode_requested = Signal(str)       # 模式名,下拉框变更即下发
    filter_requested = Signal(str)     # RECT/RRC,下拉框变更即下发
    rolloff_requested = Signal(str)    # RRC 滚降因子,下拉框变更即下发
    symrate_requested = Signal(int)   # 符号率 Sym/s
    seed_requested = Signal(int)      # PRBS7 seed
    source_requested = Signal(str)   # PRBS7/USER 数据源
    upload_requested = Signal(str)   # 用户原始比特流路径
    loop_requested = Signal(bool)    # 用户数据循环播放
    play_mode_requested = Signal(str)
    play_repeat_requested = Signal(int)
    play_trigger_requested = Signal(str)
    play_arm_requested = Signal()
    play_start_requested = Signal()
    play_stop_requested = Signal()
    play_clear_requested = Signal()
    ddr_stream_upload_requested = Signal(str, int)   # 手动上传 (文件路径, bank 0/1)
    ddr_stream_feed_requested = Signal(str, int)     # 环形自动喂数 (文件路径, bank)
    ddr_stream_start_requested = Signal()
    ddr_stream_stop_requested = Signal()
    ddr_stream_poll_requested = Signal()             # 请求刷新 GET DDR PP 状态

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._hardware_enabled = True
        self._mode = "OFF"
        self._ddr_status = None
        self._ring_upload_active = False
        self._ring_fed = 0
        self._ring_feeder = RingFeeder()
        self._ring_timer = QTimer(self)
        self._ring_timer.setInterval(200)
        self._ring_timer.timeout.connect(self._on_ring_tick)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignTop)
        lay.addWidget(QLabel("数字调制"))
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel("调制模式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["BPSK", "QPSK", "QAM4", "QAM16", "QAM64", "OFF"])
        self.mode_combo.currentTextChanged.connect(self.mode_requested.emit)
        row1.addWidget(self.mode_combo)
        lay.addLayout(row1)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("成形滤波"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["RECT", "RRC"])
        self.filter_combo.currentTextChanged.connect(self.filter_requested.emit)
        self.filter_combo.currentTextChanged.connect(self._update_rolloff_enabled)
        filter_row.addWidget(self.filter_combo)
        lay.addLayout(filter_row)
        rolloff_row = QHBoxLayout()
        rolloff_row.setSpacing(8)
        rolloff_row.addWidget(QLabel("滚降因子"))
        self.rolloff_combo = QComboBox()
        self.rolloff_combo.addItems(["0.2", "0.35", "0.5"])
        self.rolloff_combo.setCurrentText("0.35")
        self.rolloff_combo.currentTextChanged.connect(self.rolloff_requested.emit)
        rolloff_row.addWidget(self.rolloff_combo)
        lay.addLayout(rolloff_row)
        self.filter_hint = QLabel("RRC 支持全部模式；运行中需先关闭数字调制")
        self.filter_hint.setStyleSheet("color: orange;")
        lay.addWidget(self.filter_hint)
        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_row.addWidget(QLabel("数据源"))
        self.source_combo = QComboBox()
        self.source_combo.addItems(["PRBS7", "USER"])
        self.source_combo.currentTextChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_combo)
        lay.addLayout(source_row)
        self.source_label = QLabel("数据源：内部固定 PRBS7")
        lay.addWidget(self.source_label)

        user_file_row = QHBoxLayout()
        user_file_row.setSpacing(8)
        user_file_row.addWidget(QLabel("用户比特流"))
        self.user_file_edit = QLineEdit()
        self.user_file_edit.setReadOnly(True)
        self.user_file_edit.setPlaceholderText("选择 .bin/.dat 原始比特流")
        user_file_row.addWidget(self.user_file_edit, 1)
        self.user_browse_btn = QPushButton("选择文件")
        self.user_browse_btn.clicked.connect(self._browse_user_file)
        user_file_row.addWidget(self.user_browse_btn)
        self.user_upload_btn = QPushButton("上传")
        self.user_upload_btn.clicked.connect(self._on_upload_clicked)
        user_file_row.addWidget(self.user_upload_btn)
        lay.addLayout(user_file_row)

        user_status_row = QHBoxLayout()
        user_status_row.setSpacing(8)
        self.user_file_size = QLabel("文件大小：--")
        self.user_progress = QProgressBar()
        self.user_progress.setRange(0, 100)
        self.user_progress.setValue(0)
        self.user_progress.setTextVisible(True)
        self.user_progress.setFormat("%p%")
        self.user_status = QLabel("未选择文件")
        user_status_row.addWidget(self.user_file_size)
        user_status_row.addWidget(self.user_progress, 1)
        user_status_row.addWidget(self.user_status)
        lay.addLayout(user_status_row)

        self.user_loop = QCheckBox("循环播放用户数据")
        self.user_loop.toggled.connect(self._on_user_loop_changed)
        lay.addWidget(self.user_loop)

        ddr_box = QVBoxLayout()
        ddr_box.addWidget(QLabel("大文件数据流 (DDR ≤1 MiB)"))
        ddr_file_row = QHBoxLayout()
        ddr_file_row.setSpacing(8)
        ddr_file_row.addWidget(QLabel("数据文件队列"))
        self.ddr_stream_queue_label = QLabel("0 个文件")
        ddr_file_row.addWidget(self.ddr_stream_queue_label, 1)
        self.ddr_stream_add_btn = QPushButton("添加文件")
        self.ddr_stream_remove_btn = QPushButton("移除")
        self.ddr_stream_clear_btn = QPushButton("清空")
        self.ddr_stream_add_btn.clicked.connect(self._browse_ddr_stream_files)
        self.ddr_stream_remove_btn.clicked.connect(self._remove_ddr_stream_selected)
        self.ddr_stream_clear_btn.clicked.connect(self._clear_ddr_stream_queue)
        for button in (self.ddr_stream_add_btn, self.ddr_stream_remove_btn,
                       self.ddr_stream_clear_btn):
            ddr_file_row.addWidget(button)
        ddr_box.addLayout(ddr_file_row)

        self.ddr_stream_list = QListWidget()
        self.ddr_stream_list.setMaximumHeight(90)
        ddr_box.addWidget(self.ddr_stream_list)

        ddr_row = QHBoxLayout()
        ddr_row.setSpacing(8)
        ddr_row.addWidget(QLabel("手动上传到 bank"))
        self.ddr_stream_bank_combo = QComboBox()
        self.ddr_stream_bank_combo.addItems(["0", "1"])
        ddr_row.addWidget(self.ddr_stream_bank_combo)
        self.ddr_stream_upload_btn = QPushButton("上传所选")
        self.ddr_stream_upload_btn.clicked.connect(self._on_ddr_stream_upload_clicked)
        ddr_row.addWidget(self.ddr_stream_upload_btn)
        self.ddr_stream_ring_check = QCheckBox("环形模式")
        self.ddr_stream_ring_check.toggled.connect(self._on_ddr_stream_ring_toggled)
        ddr_row.addWidget(self.ddr_stream_ring_check)
        ddr_box.addLayout(ddr_row)

        self.ddr_stream_progress = QProgressBar()
        self.ddr_stream_progress.setRange(0, 100)
        self.ddr_stream_progress.setValue(0)
        self.ddr_stream_progress.setTextVisible(True)
        self.ddr_stream_progress.setFormat("%p%")
        ddr_box.addWidget(self.ddr_stream_progress)

        ddr_ctrl_row = QHBoxLayout()
        self.ddr_stream_start_btn = QPushButton("START")
        self.ddr_stream_stop_btn = QPushButton("STOP")
        self.ddr_stream_start_btn.clicked.connect(self.ddr_stream_start_requested.emit)
        self.ddr_stream_stop_btn.clicked.connect(self.ddr_stream_stop_requested.emit)
        ddr_ctrl_row.addWidget(self.ddr_stream_start_btn)
        ddr_ctrl_row.addWidget(self.ddr_stream_stop_btn)
        ddr_box.addLayout(ddr_ctrl_row)
        self.ddr_stream_status = QLabel("DDR 数据流：--")
        ddr_box.addWidget(self.ddr_stream_status)
        self.ddr_stream_ring_status = QLabel("环形：关")
        ddr_box.addWidget(self.ddr_stream_ring_status)
        self.ddr_stream_hint = QLabel(
            "上传需 MODE OFF；播放需先选数字调制模式；环形模式播放中自动把队列下一块喂给非活动 bank")
        ddr_box.addWidget(self.ddr_stream_hint)
        lay.addLayout(ddr_box)

        play_box = QVBoxLayout()
        play_box.addWidget(QLabel("确定性播放"))
        play_row1 = QHBoxLayout()
        play_row1.addWidget(QLabel("模式"))
        self.play_mode_combo = QComboBox()
        self.play_mode_combo.addItems(["LEGACY", "MANUAL"])
        self.play_mode_combo.currentTextChanged.connect(self.play_mode_requested.emit)
        play_row1.addWidget(self.play_mode_combo)
        play_row1.addWidget(QLabel("重复次数"))
        self.play_repeat_spin = QSpinBox()
        self.play_repeat_spin.setRange(0, 65535)
        self.play_repeat_spin.setValue(0)
        self.play_repeat_spin.setSpecialValueText("无限")
        self.play_repeat_spin.setKeyboardTracking(False)
        self.play_repeat_spin.valueChanged.connect(self.play_repeat_requested.emit)
        play_row1.addWidget(self.play_repeat_spin)
        play_row1.addWidget(QLabel("触发"))
        self.play_trigger_combo = QComboBox()
        self.play_trigger_combo.addItems(["SOFTWARE", "EXTERNAL"])
        self.play_trigger_combo.currentTextChanged.connect(self.play_trigger_requested.emit)
        play_row1.addWidget(self.play_trigger_combo)
        play_box.addLayout(play_row1)

        play_row2 = QHBoxLayout()
        self.play_arm_btn = QPushButton("ARM")
        self.play_start_btn = QPushButton("START")
        self.play_stop_btn = QPushButton("STOP")
        self.play_clear_btn = QPushButton("CLEAR")
        self.play_arm_btn.clicked.connect(self.play_arm_requested.emit)
        self.play_start_btn.clicked.connect(self.play_start_requested.emit)
        self.play_stop_btn.clicked.connect(self.play_stop_requested.emit)
        self.play_clear_btn.clicked.connect(self.play_clear_requested.emit)
        for button in (self.play_arm_btn, self.play_start_btn,
                       self.play_stop_btn, self.play_clear_btn):
            play_row2.addWidget(button)
        play_box.addLayout(play_row2)
        self.play_status = QLabel("PLAY: IDLE · 剩余 0")
        play_box.addWidget(self.play_status)
        lay.addLayout(play_box)
        self._play_state = "IDLE"
        self._play_supported = True

        seed_row = QHBoxLayout()
        seed_row.setSpacing(8)
        seed_row.addWidget(QLabel("PRBS7 seed"))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(1, 127)
        self.seed_spin.setValue(127)
        self.seed_spin.setKeyboardTracking(False)
        seed_row.addWidget(self.seed_spin, 1)
        self.seed_btn = QPushButton("设置")
        self.seed_btn.clicked.connect(lambda: self.seed_requested.emit(self.seed_spin.value()))
        seed_row.addWidget(self.seed_btn)
        lay.addLayout(seed_row)
        self.seed_hint = QLabel("范围：1~127；运行中需先关闭数字调制")
        lay.addWidget(self.seed_hint)
        rate_row = QHBoxLayout()
        rate_row.setSpacing(8)
        rate_row.addWidget(QLabel("符号率"))
        self.symrate_spin = QSpinBox()
        self.symrate_spin.setRange(10, 1_000_000)
        self.symrate_spin.setValue(10_000)
        self.symrate_spin.setSingleStep(1000)
        self.symrate_spin.setSuffix(" Sym/s")
        self.symrate_spin.setKeyboardTracking(False)
        rate_row.addWidget(self.symrate_spin, 1)
        self.symrate_btn = QPushButton("设置")
        self.symrate_btn.clicked.connect(
            lambda: self.symrate_requested.emit(self.symrate_spin.value()))
        rate_row.addWidget(self.symrate_btn)
        lay.addLayout(rate_row)
        self.symrate_hint = QLabel("范围：10~1,000,000 Sym/s；运行中需先关闭数字调制")
        lay.addWidget(self.symrate_hint)
        self.symrate_label = QLabel("已确认符号率：10 kSym/s")
        lay.addWidget(self.symrate_label)
        lay.addWidget(QLabel("DAC0 = I · DAC1 = Q（固定满量程）"))
        self.note = QLabel("当前：标准 AWG 输出")
        lay.addWidget(self.note)
        self._updating = False
        self._update_rolloff_enabled()
        self._update_user_controls()
        self._update_play_controls()
        self._update_ddr_stream_controls()

    def _update_play_controls(self) -> None:
        play_enabled = self._hardware_enabled and self._play_supported
        config_enabled = play_enabled and not self._running and self._mode == "OFF"
        self.play_mode_combo.setEnabled(config_enabled)
        self.play_repeat_spin.setEnabled(config_enabled)
        self.play_trigger_combo.setEnabled(config_enabled)
        self.play_arm_btn.setEnabled(
            play_enabled and self._mode != "OFF" and
            self._play_state in ("IDLE", "ERROR"))
        self.play_start_btn.setEnabled(
            play_enabled and self._play_state == "ARMED")
        self.play_stop_btn.setEnabled(
            play_enabled and self._play_state in
            ("ARMED", "STARTING", "RUNNING", "DRAIN"))
        self.play_clear_btn.setEnabled(play_enabled)

    def set_play_supported(self, enabled: bool) -> None:
        self._play_supported = bool(enabled)
        if not self._play_supported:
            self.play_status.setText("PLAY: 不支持（旧硬件）")
        self._update_play_controls()

    def set_play_status(self, status: dict) -> None:
        self._play_state = str(status.get("state", "IDLE"))
        remaining = int(status.get("repeat_remaining", 0))
        flags = []
        if status.get("frame_done"):
            flags.append("完成")
        if status.get("underflow"):
            flags.append("欠载")
        if status.get("trigger_seen"):
            flags.append("已触发")
        if status.get("error_valid"):
            flags.append(f"错误 {int(status.get('error_code', 0)):02X}")
        suffix = " · " + "、".join(flags) if flags else ""
        self.play_status.setText(f"PLAY: {self._play_state} · 剩余 {remaining}{suffix}")
        self._update_play_controls()

    def _on_source_changed(self, source: str) -> None:
        source = str(source).upper()
        self.source_label.setText(
            "数据源：内部固定 PRBS7" if source == "PRBS7" else "数据源：用户上传 USER")
        if source == "PRBS7":
            self.user_loop.blockSignals(True)
            self.user_loop.setChecked(False)
            self.user_loop.blockSignals(False)
        self._update_user_controls()
        if not self._updating:
            self.source_requested.emit(source)

    def _on_user_loop_changed(self, checked: bool) -> None:
        if not self._updating:
            self.loop_requested.emit(bool(checked))

    def _browse_user_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择用户原始比特流", "", "Bitstream (*.bin *.dat);;所有文件 (*)")
        if path:
            self.set_user_file(path)

    def set_user_file(self, path: str) -> None:
        path = os.fspath(path)
        self.user_file_edit.setText(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            self.user_file_size.setText("文件大小：不可读")
            self.user_upload_btn.setEnabled(False)
            self.user_status.setText("文件不可读")
            return
        self.user_file_size.setText(f"文件大小：{size} B")
        self.user_status.setText("待上传")
        self._update_user_controls()

    def _on_upload_clicked(self) -> None:
        path = self.user_file_edit.text().strip()
        if path and os.path.isfile(path):
            self.upload_requested.emit(path)

    def set_user_progress(self, *values) -> None:
        if not values:
            return
        if len(values) >= 2:
            try:
                done, total = int(values[0]), int(values[1])
                percent = 0 if total <= 0 else round(done * 100 / total)
            except (TypeError, ValueError):
                return
        else:
            try:
                percent = round(float(values[0]))
            except (TypeError, ValueError):
                return
        self.user_progress.setValue(max(0, min(100, percent)))

    def set_user_status(self, status, *_args) -> None:
        self.user_status.setText(str(status))

    def _update_user_controls(self) -> None:
        editable = self._hardware_enabled and not self._running and self._mode == "OFF"
        source = self.source_combo.currentText().upper()
        user_source = editable and source == "USER"
        self.source_combo.setEnabled(editable)
        self.user_browse_btn.setEnabled(user_source)
        self.user_upload_btn.setEnabled(user_source and bool(self.user_file_edit.text().strip()))
        self.user_loop.setEnabled(user_source)

    def _browse_ddr_stream_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 DDR 数据流文件(可多选)", "", "数据流 (*.bin *.dat);;所有文件 (*)")
        if paths:
            self.add_ddr_stream_files(paths)

    def add_ddr_stream_files(self, paths) -> None:
        added = False
        for path in paths:
            path = os.fspath(path)
            if not os.path.isfile(path):
                continue
            self.ddr_stream_list.addItem(path)
            added = True
        if added and self.ddr_stream_list.currentRow() < 0:
            self.ddr_stream_list.setCurrentRow(0)
        self._update_ddr_stream_controls()

    def ddr_stream_paths(self):
        return [self.ddr_stream_list.item(i).text()
                for i in range(self.ddr_stream_list.count())]

    def _remove_ddr_stream_selected(self) -> None:
        row = self.ddr_stream_list.currentRow()
        if row >= 0:
            self.ddr_stream_list.takeItem(row)
        self._update_ddr_stream_controls()

    def _clear_ddr_stream_queue(self) -> None:
        self.ddr_stream_list.clear()
        self._ring_feeder.reset()
        self._update_ddr_stream_controls()

    def _on_ddr_stream_upload_clicked(self) -> None:
        row = self.ddr_stream_list.currentRow()
        if row < 0:
            return
        path = self.ddr_stream_list.item(row).text()
        if path and os.path.isfile(path):
            bank = int(self.ddr_stream_bank_combo.currentText())
            self._ring_feeder.skip_past(row, self.ddr_stream_list.count())
            self.ddr_stream_upload_requested.emit(path, bank)

    def _on_ddr_stream_ring_toggled(self, checked: bool) -> None:
        self._ring_fed = 0
        self._ring_upload_active = False
        self._update_ddr_stream_controls()

    def set_ddr_stream_progress(self, *values) -> None:
        if not values:
            return
        if len(values) >= 2:
            try:
                done, total = int(values[0]), int(values[1])
                percent = 0 if total <= 0 else round(done * 100 / total)
            except (TypeError, ValueError):
                return
        else:
            try:
                percent = round(float(values[0]))
            except (TypeError, ValueError):
                return
        self.ddr_stream_progress.setValue(max(0, min(100, percent)))

    def set_ddr_stream_upload_done(self) -> None:
        self._ring_upload_active = False

    def set_ddr_stream_status(self, status) -> None:
        self._ddr_status = dict(status) if status else None
        if not status:
            self.ddr_stream_status.setText("DDR 数据流：--")
        else:
            banks = []
            for bank in (0, 1):
                info = status.get(f"bank{bank}") or {}
                if info.get("valid"):
                    banks.append(f"B{bank} {int(info.get('bit_length', 0)) // 8} B")
            active_bank = status.get("active_bank")
            text = (f"DDR 数据流：{status.get('state', '?')} · "
                    f"活动 {'NONE' if active_bank is None else active_bank}")
            if banks:
                text += " · " + "、".join(banks)
            error = int(status.get("error_code", 0))
            if error:
                text += f" · 错误 {error:02X}"
            self.ddr_stream_status.setText(text)
        self._maybe_ring_feed()
        self._update_ddr_stream_controls()

    def _maybe_ring_feed(self) -> None:
        if not self.ddr_stream_ring_check.isChecked():
            return
        paths = self.ddr_stream_paths()
        feed = self._ring_feeder.next_feed(
            self._ddr_status, len(paths), self._ring_upload_active)
        if feed is None:
            return
        bank, index = feed
        self._ring_upload_active = True
        self._ring_fed += 1
        self.ddr_stream_feed_requested.emit(paths[index], bank)

    def _on_ring_tick(self) -> None:
        if self.ddr_stream_ring_check.isChecked():
            self.ddr_stream_poll_requested.emit()

    def _update_ddr_stream_controls(self) -> None:
        upload_ok = (self._hardware_enabled and not self._running and
                     self._mode == "OFF")
        queue_len = self.ddr_stream_list.count()
        self.ddr_stream_add_btn.setEnabled(upload_ok)
        self.ddr_stream_bank_combo.setEnabled(upload_ok)
        self.ddr_stream_upload_btn.setEnabled(upload_ok and queue_len > 0)
        self.ddr_stream_remove_btn.setEnabled(upload_ok and queue_len > 0)
        self.ddr_stream_clear_btn.setEnabled(upload_ok and queue_len > 0)
        self.ddr_stream_ring_check.setEnabled(upload_ok and queue_len > 0)
        self.ddr_stream_queue_label.setText(f"{queue_len} 个文件")
        active = bool(self._ddr_status and self._ddr_status.get("active"))
        any_valid = False
        if self._ddr_status is not None:
            any_valid = any(
                (self._ddr_status.get(f"bank{bank}") or {}).get("valid")
                for bank in (0, 1))
        self.ddr_stream_start_btn.setEnabled(
            self._hardware_enabled and self._mode != "OFF" and any_valid and not active)
        self.ddr_stream_stop_btn.setEnabled(self._hardware_enabled and active)
        if self.ddr_stream_ring_check.isChecked() and active:
            if not self._ring_timer.isActive():
                self._ring_timer.start()
        elif self._ring_timer.isActive():
            self._ring_timer.stop()
        self._update_ddr_stream_ring_label(active)

    def _update_ddr_stream_ring_label(self, active: bool) -> None:
        if not self.ddr_stream_ring_check.isChecked():
            self.ddr_stream_ring_status.setText("环形：关")
            return
        status = self._ddr_status or {}
        active_bank = status.get("active_bank")
        if active and active_bank is not None:
            text = f"环形：开 · 活动 B{active_bank} · 已喂 {self._ring_fed} 块"
        else:
            text = "环形：开（等待播放）"
        error = int(status.get("error_code", 0))
        if error:
            text += f" · 错误 {error:02X}"
        self.ddr_stream_ring_status.setText(text)

    def set_mode(self, mode: str) -> None:
        if mode in ("BPSK", "QPSK", "QAM4", "QAM16", "QAM64", "OFF"):
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentText(mode)
            self.mode_combo.blockSignals(False)

    def _update_rolloff_enabled(self, *_args) -> None:
        self.rolloff_combo.setEnabled(
            self._hardware_enabled and not self._running and self.filter_combo.currentText() == "RRC")

    def set_hardware_enabled(self, enabled: bool) -> None:
        self._hardware_enabled = enabled
        if not enabled:
            # A disconnect ends the session: stop polling and drop the last
            # status so the panel does not keep showing a stale RUNNING bank.
            self._ring_timer.stop()
            self._ring_upload_active = False
            self._ddr_status = None
            self.ddr_stream_status.setText("DDR 数据流：--")
        self.mode_combo.setEnabled(enabled)
        self.filter_combo.setEnabled(enabled and not self._running)
        self.seed_spin.setEnabled(enabled and not self._running)
        self.seed_btn.setEnabled(enabled and not self._running)
        self.symrate_spin.setEnabled(enabled and not self._running)
        self.symrate_btn.setEnabled(enabled and not self._running)
        self._update_rolloff_enabled()
        self._update_user_controls()
        self._update_play_controls()
        self._update_ddr_stream_controls()

    def set_confirmed(self, mode: str, source: str = "PRBS7",
                      symrate_hz: int = 10_000, running: bool = False,
                      *, filter_name: str = "RECT", rolloff: str = "0.35",
                      seed: int = 127, user_loop: bool = False) -> None:
        self.set_mode(mode)
        filter_name = str(filter_name).upper()
        if filter_name not in ("RECT", "RRC"):
            filter_name = "RECT"
        self._running = running
        self._mode = str(mode).upper()
        source_name = str(source).upper()
        if source_name not in ("PRBS7", "USER"):
            source_name = "PRBS7"
        self.filter_combo.blockSignals(True)
        self.filter_combo.setCurrentText(filter_name)
        self.filter_combo.blockSignals(False)
        self.filter_combo.setEnabled(self._hardware_enabled and not running)
        rolloff_name = str(rolloff)
        if rolloff_name not in ("0.2", "0.35", "0.5"):
            rolloff_name = "0.35"
        self.rolloff_combo.blockSignals(True)
        self.rolloff_combo.setCurrentText(rolloff_name)
        self.rolloff_combo.blockSignals(False)
        self._update_rolloff_enabled()
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentText(source_name)
        self.source_combo.blockSignals(False)
        self.source_label.setText(
            "数据源：内部固定 PRBS7" if source_name == "PRBS7" else "数据源：用户上传 USER")
        self.filter_hint.setText("RRC 支持全部模式；运行中需先关闭数字调制")
        self.user_loop.blockSignals(True)
        self.user_loop.setChecked(bool(user_loop) if source_name == "USER" else False)
        self.user_loop.blockSignals(False)
        self._update_user_controls()
        self.seed_spin.blockSignals(True)
        self.seed_spin.setValue(seed)
        self.seed_spin.blockSignals(False)
        self.seed_spin.setEnabled(self._hardware_enabled and not running)
        self.seed_btn.setEnabled(self._hardware_enabled and not running)
        self.symrate_spin.blockSignals(True)
        self.symrate_spin.setValue(symrate_hz)
        self.symrate_spin.blockSignals(False)
        self.symrate_spin.setEnabled(self._hardware_enabled and not running)
        self.symrate_btn.setEnabled(self._hardware_enabled and not running)
        if symrate_hz % 1000 == 0:
            symrate_text = f"{symrate_hz // 1000} kSym/s"
        else:
            symrate_text = f"{symrate_hz} Sym/s"
        self.symrate_label.setText(f"已确认符号率：{symrate_text}")
        if mode == "OFF":
            self.note.setText("当前：标准 AWG 输出")
        else:
            state = "运行中" if running else "已停止"
            filter_text = f"，{filter_name}，" if filter_name != "RECT" else "，"
            self.note.setText(f"当前：{mode}（{source}{filter_text}{symrate_text}，{state}）")
        self._update_play_controls()
        self._update_ddr_stream_controls()


class ChannelImpairPanel(QWidget):
    """信道损伤面板: CH1/CH2 使能 + 噪声 + 4 抽头 (增益/延迟) + 应用。"""
    apply_requested = Signal(int, bool, int, list)   # (channel 1/2, enabled, noise, taps)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hardware_enabled = True
        self._controls = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignTop)
        lay.addWidget(QLabel("信道损伤 (增益 Q8 定点, 延迟采样)"))
        for ch in (1, 2):
            lay.addWidget(QLabel(f"CH{ch}"))
            row = QHBoxLayout()
            row.setSpacing(6)
            enable = QCheckBox("启用")
            row.addWidget(enable)
            row.addWidget(QLabel("噪声"))
            noise = QSpinBox()
            noise.setRange(0, 100)
            row.addWidget(noise)
            row.addStretch(1)
            lay.addLayout(row)
            taps = []
            for k in range(4):
                trow = QHBoxLayout()
                trow.setSpacing(6)
                trow.addWidget(QLabel(f"抽头{k}"))
                trow.addWidget(QLabel("增益"))
                gain = QSpinBox()
                gain.setRange(-256, 255)
                gain.setValue(255 if k == 0 else 0)   # unity tap (9-bit signed Q8)
                trow.addWidget(gain)
                trow.addWidget(QLabel("延迟"))
                delay = QSpinBox()
                delay.setRange(0, 511)
                trow.addWidget(delay)
                trow.addStretch(1)
                lay.addLayout(trow)
                taps.append((gain, delay))
            btn = QPushButton("应用")
            btn.clicked.connect(lambda _=False, c=ch: self.apply(c))
            lay.addWidget(btn)
            self._controls[ch] = {"enable": enable, "noise": noise,
                                  "taps": taps, "btn": btn}
        self.apply_btns = {ch: self._controls[ch]["btn"] for ch in (1, 2)}
        self.apply_btn = self.apply_btns[1]

    def get_taps(self, channel: int) -> list:
        return [(g.value(), d.value()) for g, d in self._controls[channel]["taps"]]

    def apply(self, channel: int) -> None:
        c = self._controls[channel]
        self.apply_requested.emit(
            int(channel), c["enable"].isChecked(), c["noise"].value(),
            [(g.value(), d.value()) for g, d in c["taps"]])

    def set_confirmed(self, channel, enabled, noise, taps) -> None:
        if channel not in self._controls:
            return
        c = self._controls[channel]
        c["enable"].blockSignals(True)
        c["enable"].setChecked(bool(enabled))
        c["enable"].blockSignals(False)
        c["noise"].blockSignals(True)
        c["noise"].setValue(int(noise))
        c["noise"].blockSignals(False)
        for (gain, delay), (gs, ds) in zip(taps, c["taps"]):
            gs.blockSignals(True)
            gs.setValue(int(gain))
            gs.blockSignals(False)
            ds.blockSignals(True)
            ds.setValue(int(delay))
            ds.blockSignals(False)

    def set_hardware_enabled(self, enabled: bool) -> None:
        self._hardware_enabled = bool(enabled)
        for c in self._controls.values():
            c["enable"].setEnabled(self._hardware_enabled)
            c["noise"].setEnabled(self._hardware_enabled)
            for gain, delay in c["taps"]:
                gain.setEnabled(self._hardware_enabled)
                delay.setEnabled(self._hardware_enabled)
            c["btn"].setEnabled(self._hardware_enabled)
