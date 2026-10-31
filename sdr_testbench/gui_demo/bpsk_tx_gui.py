"""GNU Radio QtGUI baseband preview + explicitly requested bounded Pluto TX.

Opening the window constructs only software source/throttle/GUI sinks. Actual
hardware is owned solely by the existing radio_test child after a button click.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

from profile_contract import HERE, load_profile, preview_samples, tx_command, validate_profile

# GNU Radio's FFTW cache must be writable; the default Windows profile path can
# also expose ANSI/UTF-8 conversion issues on this machine's Chinese username.
os.environ.setdefault('XDG_CACHE_HOME', str(HERE / 'generated/cache'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=HERE / 'pluto_bpsk.json')
    parser.add_argument('--dry-run', action='store_true', help='Validate configuration; no GUI or hardware')
    parser.add_argument('--screenshot', type=Path, help='Save this local preview window; never starts RF')
    parser.add_argument('--quit-after', type=float, default=0, help='Preview-only QA automatic close, seconds')
    args = parser.parse_args()
    config = load_profile(args.profile)
    if args.dry_run:
        print(json.dumps(dict(mode='PREVIEW_ONLY', hardware_accessed=False, profile=config,
                              samples=len(preview_samples(config)), actual_tx_requires_button=True),
                         indent=2, ensure_ascii=False))
        return 0

    from PyQt5 import QtCore, QtGui, QtWidgets
    import sip
    from gnuradio import blocks, gr, qtgui
    from gnuradio.fft import window

    class Demo(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.config = dict(config)
            self.process = None
            self.run_dir = None
            self.close_after_tx = False
            self.setWindowTitle('GNU Radio | BPSK external source | PlutoSDR')
            self.resize(1450, 960)
            self.setStyleSheet('QWidget {font-family:"Microsoft YaHei","Segoe UI"; font-size:13px;} '
                              'QGroupBox {font-weight:bold; margin-top:12px; padding-top:12px;} '
                              'QPushButton {padding:6px;} QLineEdit,QComboBox,QDoubleSpinBox {padding:3px;}')
            layout = QtWidgets.QVBoxLayout(self)
            title = QtWidgets.QLabel('GNU Radio · BPSK 外部信号源')
            title.setStyleSheet('font-size:27px; font-weight:bold; color:#153b65;')
            layout.addWidget(title)
            layout.addWidget(QtWidgets.QLabel('Pluto TX → 板卡 RX1  |  板卡仅接收、TX关闭  |  不使用自回环'))
            self.status = QtWidgets.QLabel('本地基带预览 · 未访问Pluto · 未发射')
            self.status.setStyleSheet('background:#e7f1ff; color:#163f70; padding:10px; font-size:17px;')
            layout.addWidget(self.status)
            top = QtWidgets.QHBoxLayout()
            layout.addLayout(top)
            settings = QtWidgets.QGroupBox('信号与设备参数（应用到预览不发射）')
            form = QtWidgets.QFormLayout(settings)
            self.lo = QtWidgets.QDoubleSpinBox()
            self.lo.setRange(325, 3800)
            self.lo.setDecimals(3)
            self.lo.setSuffix(' MHz')
            self.lo.setValue(config['center_hz'] / 1e6)
            self.seed = QtWidgets.QComboBox()
            self.seed.addItems(['1', '37', '73'])
            self.seed.setCurrentText(str(config['prbs7_seed']))
            self.duration = QtWidgets.QDoubleSpinBox()
            self.duration.setRange(0.2, 2.0)
            self.duration.setSingleStep(0.1)
            self.duration.setSuffix(' s')
            self.duration.setValue(config['duration_s'])
            self.serial = QtWidgets.QLineEdit(config['serial'])
            form.addRow('Pluto LO', self.lo)
            form.addRow('调制 / 成形', QtWidgets.QLabel('BPSK / RECT · 无差分编码'))
            form.addRow('采样率 / 符号率', QtWidgets.QLabel('6 MSPS / 3 MSym/s · 2 samples/symbol'))
            form.addRow('数字幅度 / TX衰减', QtWidgets.QLabel('0.1 / 60 dB（板卡匹配配置）'))
            form.addRow('TX RF带宽', QtWidgets.QLabel('1.5 MHz · 沿用已有发射配置，非校准测量'))
            form.addRow('PRBS7参考种子', self.seed)
            form.addRow('单次流窗口', self.duration)
            form.addRow('Pluto序列号', self.serial)
            self.apply_btn = QtWidgets.QPushButton('应用到本地预览 / 保存拍摄参数')
            self.apply_btn.clicked.connect(self.apply_preview)
            form.addRow(self.apply_btn)
            top.addWidget(settings, 1)

            action_box = QtWidgets.QGroupBox('外部源发射（独立显式操作）')
            actions = QtWidgets.QVBoxLayout(action_box)
            actions.addWidget(QtWidgets.QLabel('PRBS7 → BPSK映射 → RECT ×2 → Pluto Sink\n'
                                               '预览图只显示发送基带；实测接收结果请看板卡屏幕。'))
            self.wiring = QtWidgets.QLineEdit()
            self.wiring.setPlaceholderText('填写本次实际接线，例如 Pluto TX -> board RX1')
            actions.addWidget(self.wiring)
            self.board_off = QtWidgets.QCheckBox('已确认板卡TX关闭，且实际接线为Pluto TX → 板卡RX1')
            actions.addWidget(self.board_off)
            self.tx_btn = QtWidgets.QPushButton('启动一次限时Pluto发射')
            self.tx_btn.setStyleSheet('background:#c05b13; color:white; font-weight:bold;')
            self.tx_btn.clicked.connect(self.transmit)
            actions.addWidget(self.tx_btn)
            self.stop_btn = QtWidgets.QPushButton('请求停止并关闭TX')
            self.stop_btn.setEnabled(False)
            self.stop_btn.clicked.connect(self.request_stop)
            actions.addWidget(self.stop_btn)
            self.saved = QtWidgets.QLabel('尚未启动射频。启动本窗口和GRC预览均不会发射。')
            self.saved.setWordWrap(True)
            actions.addWidget(self.saved)
            top.addWidget(action_box, 1)
            self.settings_widget = settings

            plots = QtWidgets.QGridLayout()
            layout.addLayout(plots, 1)
            self.tb = gr.top_block('BPSK software preview - no hardware', catch_exceptions=True)
            self.source = blocks.vector_source_c(preview_samples(self.config), True)
            self.throttle = blocks.throttle(gr.sizeof_gr_complex, config['sample_rate'], True)
            self.time_sink = qtgui.time_sink_c(128, config['sample_rate'], 'TX baseband I/Q - local preview', 1)
            self.time_sink.set_update_time(0.15)
            self.time_sink.set_y_axis(-0.14, 0.14)
            self.time_sink.enable_grid(True)
            self.freq_sink = qtgui.freq_sink_c(4096, window.WIN_HANN, 0, config['sample_rate'],
                                             'TX baseband spectrum - relative, not measured RF', 1)
            self.freq_sink.set_update_time(0.15)
            self.freq_sink.set_y_axis(-110, 0)
            self.const_sink = qtgui.const_sink_c(1024, 'BPSK constellation - generated IQ', 1)
            self.const_sink.set_update_time(0.15)
            self.const_sink.set_x_axis(-0.14, 0.14)
            self.const_sink.set_y_axis(-0.14, 0.14)
            self.const_sink.enable_grid(True)
            for sink in (self.time_sink, self.freq_sink, self.const_sink):
                self.tb.connect(self.throttle, sink)
            self.tb.connect(self.source, self.throttle)
            for row, col, sink in ((0, 0, self.time_sink), (0, 1, self.freq_sink), (1, 0, self.const_sink)):
                widget = sip.wrapinstance(sink.qwidget(), QtWidgets.QWidget)
                widget.setMinimumSize(350, 180)
                plots.addWidget(widget, row, col)
            self.log = QtWidgets.QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumBlockCount(800)
            self.log.setPlainText('本地预览启动：无IIO上下文、无Pluto硬件连接。\n'
                                  '有效RF/接收判定必须来自板卡实际IQ与同步/BER结果。\n'
                                  '默认LO 433.9 MHz；板卡必须配置相同LO，不能沿用80 MHz模板。')
            plots.addWidget(self.log, 1, 1)
            footer = QtWidgets.QLabel('预览保持运行不等于RF持续发射。实际发射按独立按钮及收据判断；一次最多2秒，不自动循环。')
            layout.addWidget(footer)
            self.tb.start()

        def selected_config(self):
            c = dict(self.config, center_hz=round(self.lo.value() * 1e6),
                     duration_s=round(self.duration.value(), 3),
                     prbs7_seed=int(self.seed.currentText()), serial=self.serial.text().strip())
            return validate_profile(c)

        def apply_preview(self):
            try:
                self.config = self.selected_config()
                self.source.set_data(preview_samples(self.config), [])
                path = HERE / 'last_preview_profile.json'
                path.write_text(json.dumps(self.config, indent=2), encoding='utf-8')
                self.log.appendPlainText('仅更新本地预览参数：LO %.3f MHz，seed %d；未发射。'
                                         % (self.config['center_hz'] / 1e6, self.config['prbs7_seed']))
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, '参数错误', str(exc))

        def transmit(self):
            if self.process is not None:
                return
            try:
                c = self.selected_config()
                # Guard before creating the run directory or starting a child.
                tx_command(c, self.wiring.text(), self.board_off.isChecked())
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, '尚未启动发射', str(exc))
                return
            answer = QtWidgets.QMessageBox.question(self, '确认这一次发射',
                'Pluto TX → 板卡RX1；板卡TX已关闭。\n'
                'LO %.3f MHz，BPSK 3 MSym/s，60 dB TX衰减，窗口≤%.1f秒。\n'
                '板卡接收已准备好时，确认启动一次？'
                % (c['center_hz'] / 1e6, c['duration_s']),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
            self.config = c
            self.source.set_data(preview_samples(c), [])
            self.run_dir = HERE / 'runs' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            self.run_dir.mkdir(parents=True, exist_ok=False)
            config_path = self.run_dir / 'profile.json'
            config_path.write_text(json.dumps(c, indent=2), encoding='utf-8')
            (self.run_dir / 'operator.json').write_text(json.dumps(dict(
                wiring=self.wiring.text(), board_tx_off=True, preview_is_not_rf_measurement=True),
                ensure_ascii=False, indent=2), encoding='utf-8')
            cmd = tx_command(c, self.wiring.text(), True, config_path,
                             self.run_dir / 'stop.request', self.run_dir / 'worker-summary.json')
            self.process = QtCore.QProcess(self)
            self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.read_output)
            self.process.finished.connect(self.tx_finished)
            self.process.errorOccurred.connect(self.tx_error)
            self.settings_widget.setEnabled(False)
            self.tx_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.status.setText('发射任务启动中 · 状态以实际回执为准 · 本地图仍是基带预览')
            self.process.start(cmd[0], cmd[1:])
            QtCore.QTimer.singleShot(15000, lambda run=self.run_dir: self.watchdog(run))

        def read_output(self):
            if self.process is None:
                return
            text = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
            self.log.appendPlainText(text)
            if self.run_dir:
                with (self.run_dir / 'worker-console.log').open('a', encoding='utf-8') as f:
                    f.write(text)

        def request_stop(self):
            if self.process is not None:
                (self.run_dir / 'stop.request').write_text('GUI graceful stop requested\n', encoding='utf-8')
                self.status.setText('已请求停止 · 等待stop/wait及TX关闭读回，不强杀进程')

        def watchdog(self, run):
            if self.process is not None and self.run_dir == run:
                self.request_stop()
                self.status.setText('任务超时，关闭状态尚未证实 · 禁止再次发射；检查设备与收尾日志')

        def tx_error(self, error):
            if self.process and error == QtCore.QProcess.FailedToStart:
                self.tx_finished(-1, QtCore.QProcess.CrashExit)

        def tx_finished(self, code, exit_status):
            if self.process is None:
                return
            self.read_output()
            path = self.run_dir / 'worker-summary.json'
            try:
                result = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            except (ValueError, OSError):
                result = {}
            state = result.get('state', 'CLEANUP_UNVERIFIED')
            messages = {
                'TX_FINISHED_RF_UNVERIFIED': '发射流已结束、TX LO已关闭 · RF质量待板卡验证',
                'STOPPED_WITH_ERROR': '任务已停止且TX LO已关 · 有错误，请查看收据',
                'CLEANUP_UNVERIFIED': 'TX关闭尚未证实 · 请检查设备和日志，勿再次发射',
            }
            self.status.setText(messages.get(state, state))
            self.saved.setText('本次记录：' + str(self.run_dir) + '\n原始TX回执：' + str(result.get('result_path')))
            self.process.deleteLater()
            self.process = None
            self.stop_btn.setEnabled(False)
            self.settings_widget.setEnabled(True)
            self.tx_btn.setEnabled(state != 'CLEANUP_UNVERIFIED')
            self.board_off.setChecked(False)
            if self.close_after_tx:
                self.close()

        def closeEvent(self, event):
            if self.process is not None:
                self.close_after_tx = True
                self.request_stop()
                event.ignore()
                return
            self.tb.stop()
            self.tb.wait()
            event.accept()

    app = QtWidgets.QApplication(sys.argv[:1])
    font_file = Path('<LOCAL_PATH>')
    if font_file.exists():
        QtGui.QFontDatabase.addApplicationFont(str(font_file))
    app.setFont(QtGui.QFont('Microsoft YaHei', 10))
    demo = Demo()
    demo.show()
    if args.screenshot:
        def save_screenshot():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not demo.grab().save(str(args.screenshot)):
                raise RuntimeError('Screenshot save failed')
        QtCore.QTimer.singleShot(1800, save_screenshot)
    if args.quit_after:
        QtCore.QTimer.singleShot(int(max(2.5, args.quit_after) * 1000), demo.close)
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
