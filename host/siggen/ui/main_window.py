"""三栏主窗口:连接栏 | 通道控制 | 状态+预览+日志。"""
from PySide6.QtWidgets import (
    QMainWindow, QScrollArea, QSplitter, QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from ..controller import Controller
from .widgets import (
    ChannelImpairPanel, ChannelPanel, ConnectionBar, DigitalModPanel, StatusPanel,
)


def _refresh_standard_preview(window):
    window.status_panel.preview.set_calibration(window.ch1.cal_edit.value())

    ch1 = window.ctrl.state[0]
    ch2 = window.ctrl.state[1]
    ch1_mod = {
        "mode": ch1.mode, "mwave": ch1.mwave,
        "mfreq": ch1.mfreq_01 / 10.0, "am_depth": ch1.am_depth,
        "fm_dev": ch1.fm_dev, "pm_dev": ch1.pm_dev,
    }
    linked_ch2 = None
    if ch2.phaselink:
        linked_ch2 = {
            "wave": ch2.wave,
            "freq_hz": ch1.freq_hz,
            "ampl_pct": ch2.ampl_pct,
            "base_pct": ch2.base_pct,
            "harms": ch2.harms,
            "pulse_duty": getattr(ch2, "pulse_duty", 50),
            "mod": ch1_mod if ch1.mode in ("FM", "SWEEP") else {"mode": "OFF"},
            "phase_off": ch2.phase_0_01 / 36000.0,
        }
    window.status_panel.preview.set_params(
        ch1.wave, ch1.freq_hz, ch1.ampl_pct, ch1.base_pct, ch1.harms,
        mod=ch1_mod,
        ch2=linked_ch2,
        pulse_duty=getattr(ch1, "pulse_duty", 50))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AC820 任意波形发生器 — 上位机")
        self.resize(1080, 720)

        self.ctrl = Controller(self)
        self.conn = ConnectionBar()
        self.ch1 = ChannelPanel("CH1 — DAC0")
        self.ch2 = ChannelPanel("CH2 — DAC1", phase_link_enabled=True)
        self.status_panel = StatusPanel()

        self.dig = DigitalModPanel()
        self.dig_scroll = QScrollArea()
        self.dig_scroll.setWidget(self.dig)
        self.dig_scroll.setWidgetResizable(True)
        self.dig_scroll.setMinimumWidth(420)

        self.imp = ChannelImpairPanel()
        self.imp_scroll = QScrollArea()
        self.imp_scroll.setWidget(self.imp)
        self.imp_scroll.setWidgetResizable(True)
        self.imp_scroll.setMinimumWidth(420)

        mid = QWidget()
        mid_lay = QVBoxLayout(mid)
        mid_lay.setContentsMargins(0, 0, 0, 0)
        mid_lay.addWidget(self.ch1)
        mid_lay.addWidget(self.ch2)
        mid_scroll = QScrollArea()
        mid_scroll.setWidget(mid)
        mid_scroll.setWidgetResizable(True)
        mid_scroll.setMinimumWidth(420)
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(mid_scroll)        # 0: 标准波形
        self.left_stack.addWidget(self.dig_scroll)   # 1: 数字调制
        self.left_stack.addWidget(self.imp_scroll)   # 2: 信道损伤
        splitter = QSplitter()
        splitter.addWidget(self.left_stack)
        splitter.addWidget(self.status_panel)
        splitter.setSizes([420, 600])
        self.tabs = QTabBar()
        self.tabs.addTab("标准波形")
        self.tabs.addTab("数字调制")
        self.tabs.addTab("信道损伤")
        self.tabs.currentChanged.connect(self._on_tab)

        root = QWidget()
        root_lay = QVBoxLayout(root)
        root_lay.addWidget(self.conn)
        root_lay.addWidget(self.tabs)
        root_lay.addWidget(splitter, 1)
        self.setCentralWidget(root)

        # UI -> controller
        self.conn.connect_requested.connect(self._on_connect)
        self.conn.disconnect_requested.connect(self.ctrl.disconnect)
        for panel, ch in ((self.ch1, 0), (self.ch2, 1)):
            panel.param_changed.connect(
                lambda field, value, c=ch: self.ctrl.set_field(c, field, value))
            # 校准常数变化 -> 预览 Vpp/RMS 测量值刷新
            panel.cal_edit.valueChanged.connect(
                lambda _=None, c=ch: self._on_cal_changed(c))

        # controller -> UI
        self.ctrl.connected.connect(self._on_connected)
        self.ctrl.disconnected.connect(self._on_disconnected)
        self.ctrl.state_changed.connect(self._on_state)
        self.ctrl.log.connect(self.status_panel.add_log)
        self.dig.mode_requested.connect(self._on_digmod_apply)
        self.dig.filter_requested.connect(self.ctrl.set_digmod_filter)
        self.dig.rolloff_requested.connect(self.ctrl.set_digmod_rolloff)
        self.dig.symrate_requested.connect(self.ctrl.set_digmod_symrate)
        self.dig.seed_requested.connect(self.ctrl.set_digmod_seed)
        self.dig.source_requested.connect(self._on_digmod_source)
        self.dig.upload_requested.connect(self._on_user_upload)
        self.dig.loop_requested.connect(self._on_user_loop)
        self.dig.play_mode_requested.connect(self.ctrl.set_play_mode)
        self.dig.play_repeat_requested.connect(self.ctrl.set_play_repeat)
        self.dig.play_trigger_requested.connect(self.ctrl.set_play_trigger)
        self.dig.play_arm_requested.connect(self.ctrl.play_arm)
        self.dig.play_start_requested.connect(self.ctrl.play_start)
        self.dig.play_stop_requested.connect(self.ctrl.play_stop)
        self.dig.play_clear_requested.connect(self.ctrl.play_clear)
        self.ctrl.play_changed.connect(self._on_play_state)
        self.ctrl.play_capability_changed.connect(self.dig.set_play_supported)
        self.dig.ddr_stream_upload_requested.connect(self._on_ddr_stream_upload)
        self.dig.ddr_stream_feed_requested.connect(self._on_ddr_stream_upload)
        self.dig.ddr_stream_poll_requested.connect(self.ctrl.request_ddr_pp_status)
        self.dig.ddr_stream_start_requested.connect(self.ctrl.ddr_pp_start)
        self.dig.ddr_stream_stop_requested.connect(self.ctrl.ddr_pp_stop)
        self.ctrl.ddr_pp_upload_progress.connect(self.dig.set_ddr_stream_progress)
        self.ctrl.ddr_pp_upload_finished.connect(self._on_ddr_pp_upload_finished)
        self.ctrl.ddr_pp_changed.connect(self.dig.set_ddr_stream_status)
        self.ctrl.chan_status_changed.connect(self._on_chan_status)
        self.imp.apply_requested.connect(self.ctrl.apply_channel_impairment)
        self.status_panel.dig_view.currentIndexChanged.connect(self._on_dig_view)
        self.ctrl.digmod_changed.connect(self._on_digmod_state)
        self._wire_user_data_signals()
        self._set_hardware_controls_enabled(False)

    def _on_digmod_source(self, source: str):
        method = getattr(self.ctrl, "set_digmod_source", None)
        if method is not None:
            method(source)

    def _on_user_upload(self, path: str):
        method = getattr(self.ctrl, "upload_user_data", None)
        if method is not None:
            method(path)

    def _on_ddr_stream_upload(self, path: str, bank: int):
        method = getattr(self.ctrl, "upload_ddr_pp_data", None)
        if method is not None:
            method(int(bank), path)

    def _on_ddr_pp_upload_finished(self, _ok: bool):
        self.dig.set_ddr_stream_upload_done()

    def _on_user_loop(self, enabled: bool):
        method = getattr(self.ctrl, "set_user_loop", None)
        if method is not None:
            method(bool(enabled))

    def _on_play_state(self, status: dict):
        self.dig.set_play_status(status)

    def _wire_user_data_signals(self):
        for name in ("user_progress", "user_upload_progress"):
            signal = getattr(self.ctrl, name, None)
            if signal is not None and hasattr(signal, "connect"):
                signal.connect(self.dig.set_user_progress)
                break
        for name in ("user_status", "user_upload_status"):
            signal = getattr(self.ctrl, name, None)
            if signal is not None and hasattr(signal, "connect"):
                signal.connect(self.dig.set_user_status)
                break

    def _set_hardware_controls_enabled(self, enabled: bool):
        self.ch1.set_hardware_enabled(enabled)
        self.ch2.set_hardware_enabled(enabled)
        self.dig.set_hardware_enabled(enabled)
        self.imp.set_hardware_enabled(enabled)

    def _on_chan_status(self, channel: int, data: dict):
        self.imp.set_confirmed(
            int(channel) + 1, data.get("enabled", False), data.get("noise", 0),
            data.get("taps", [(255, 0), (0, 0), (0, 0), (0, 0)]))

    def _on_connected(self):
        self.conn.set_connected(True)
        self._set_hardware_controls_enabled(True)
        self.ctrl.request_channel_status(1)
        self.ctrl.request_channel_status(2)

    def _on_disconnected(self, _msg: str):
        self.conn.set_connected(False)
        self._set_hardware_controls_enabled(False)

    def _on_connect(self, port: str, baud: int):
        self.conn.set_connecting(True)
        err = self.ctrl.connect_serial(port, baud)
        if err:
            self.status_panel.add_log(f"connect failed: {err}", "error")
            self.conn.set_connecting(False)

    def _on_state(self, ch: int):
        st = self.ctrl.state[ch]
        panel = self.ch1 if ch == 0 else self.ch2
        state_kwargs = {"pulse_duty": getattr(st, "pulse_duty", 50)}
        if ch == 1:
            state_kwargs.update({
                "phase_0_01": st.phase_0_01,
                "phaselink": st.phaselink,
            })
        panel.set_state(st.wave, st.freq_hz, st.ampl_pct, st.base_pct, st.harms,
                        st.mode, st.mwave, st.mfreq_01, st.am_depth,
                        st.fm_dev, st.pm_dev, **state_kwargs)
        self.status_panel.set_channel(ch, st)

        in_digital_tab = getattr(self, "tabs", None)
        digmod = getattr(self.ctrl, "digmod", None)
        if (in_digital_tab is not None and in_digital_tab.currentIndex() == 1
                and getattr(digmod, "mode", "OFF") != "OFF"):
            return
        _refresh_standard_preview(self)

    def _on_cal_changed(self, _ch: int):
        self.status_panel.preview.set_calibration(self.ch1.cal_edit.value())

    def _on_tab(self, index: int):
        self.left_stack.setCurrentIndex(index)
        if index == 0:
            self.status_panel.set_dig_view_visible(False)
            self.status_panel.preview.clear_digital()
            self._on_state(0)
        else:
            self._on_digmod_state(self.ctrl.digmod.mode)

    def _on_digmod_apply(self, mode: str):
        self.ctrl.set_digmod(mode)

    def _on_dig_view(self, _index: int):
        if self.tabs.currentIndex() == 1:
            self._on_digmod_state(self.ctrl.digmod.mode)

    def _on_digmod_state(self, mode: str):
        state = self.ctrl.digmod
        self.dig.set_confirmed(
            mode, state.source, state.symrate_hz,
            state.running if mode == state.mode else mode != "OFF",
            filter_name=getattr(state, "filter", "RECT"),
            rolloff=getattr(state, "rolloff", "0.35"),
            seed=getattr(state, "seed", 127),
            user_loop=getattr(state, "user_loop", getattr(state, "loop", False)),
        )
        active = mode != "OFF"
        in_digital_tab = self.tabs.currentIndex() == 1
        self.status_panel.set_dig_view_visible(in_digital_tab and active)
        if in_digital_tab and active:
            view = ["time", "constellation", "compare"][
                self.status_panel.dig_view.currentIndex()]
            self.status_panel.preview.set_digital(
                mode, view, symrate_hz=state.symrate_hz,
                filter_name=getattr(state, "filter", "RECT"),
                rolloff=getattr(state, "rolloff", "0.35"))
        else:
            self.status_panel.preview.clear_digital()
            _refresh_standard_preview(self)

    def closeEvent(self, event):
        self.ctrl.disconnect()
        super().closeEvent(event)
