import pytest
from PySide6.QtWidgets import QApplication

from siggen.controller import Controller

# 与 test_widgets.py 共享单例(同进程内 QApplication/QCoreApplication 只能建一次)
app = QApplication.instance() or QApplication([])


class FakeWorker:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)

    def stop(self):
        pass


@pytest.fixture
def ctrl():
    c = Controller()
    c.worker = FakeWorker()
    return c


def test_set_field_sends_command(ctrl):
    ctrl.set_field(0, "freq", 100000)
    assert ctrl.worker.sent[-1] == "SET CH1 FREQ 100000\r\n"
    assert ctrl._pending == (0, "freq", 100000)


def test_set_field_invalid_no_send(ctrl):
    ctrl.set_field(0, "freq", 5)
    assert ctrl.worker.sent == []
    assert ctrl._pending is None


def test_phase_fields_send_command(ctrl):
    ctrl.set_field(1, "phase", 0)
    assert ctrl.worker.sent[-1] == "SET CH2 PHASE 0\r\n"
    ctrl._on_line("OK")
    ctrl.set_field(1, "phaselink", True)
    assert ctrl.worker.sent[-1] == "SET CH2 PHASELINK ON\r\n"
    ctrl._on_line("OK")
    ctrl.set_field(1, "phaselink", False)
    assert ctrl.worker.sent[-1] == "SET CH2 PHASELINK OFF\r\n"


@pytest.mark.parametrize("field, value", [("phase", 0), ("phaselink", True)])
def test_ch1_phase_fields_log_error_without_send(ctrl, field, value):
    logs = []
    ctrl.log.connect(lambda text, level: logs.append((text, level)))

    ctrl.set_field(0, field, value)

    assert ctrl.worker.sent == []
    assert ctrl._pending is None
    assert logs and logs[-1][1] == "error"


def test_phase_invalid_rejected(ctrl):
    ctrl.set_field(1, "phase", 36001)
    assert ctrl.worker.sent == []
    assert ctrl._pending is None
    ctrl.set_field(1, "phase", -1)
    assert ctrl.worker.sent == []


@pytest.mark.parametrize("value", [None, 1.5, True, "1"])
def test_phase_non_integer_rejected_without_exception(ctrl, value):
    ctrl.set_field(1, "phase", value)
    assert ctrl.worker.sent == []
    assert ctrl._pending is None


def test_phase_ack_applies_state(ctrl):
    ctrl.set_field(1, "phase", 36000)
    ctrl._on_line("OK")
    assert ctrl.state[1].phase_0_01 == 36000
    ctrl.set_field(1, "phaselink", True)
    ctrl._on_line("OK")
    assert ctrl.state[1].phaselink is True


def test_not_connected_logs(ctrl):
    ctrl.worker = None
    logs = []
    ctrl.log.connect(lambda text, level: logs.append((text, level)))
    ctrl.set_field(0, "freq", 1000)
    assert logs and logs[0][1] == "error"


def test_pulse_fields_send_and_ack(ctrl):
    ctrl.set_field(0, "wave", "PULSE")
    assert ctrl.worker.sent[-1] == "SET CH1 WAVE PULSE\r\n"
    ctrl._on_line("OK")
    ctrl.set_field(0, "duty", 25)
    assert ctrl.worker.sent[-1] == "SET CH1 DUTY 25\r\n"
    ctrl._on_line("OK")
    assert ctrl.state[0].pulse_duty == 25


def test_err_ack_keeps_state(ctrl):
    ctrl.set_field(0, "freq", 100000)
    ctrl._on_line("ERR FREQ OUT OF RANGE")
    assert ctrl.state[0].freq_hz == 1000   # default unchanged
    assert ctrl._pending is None


def test_status_line_updates_state(ctrl):
    ctrl._on_line("CH1 WAVE SINE FREQ 100000 AMPL 80")
    assert ctrl.state[0].freq_hz == 100000
    assert ctrl.state[0].ampl_pct == 80


def test_phase_status_updates_off(ctrl):
    ctrl.state[0].phaselink = True
    ctrl._on_line("CH1 WAVE SINE FREQ 100000 AMPL 80 PHASE 27000 PHASELINK OFF")
    st = ctrl.state[0]
    assert st.phase_0_01 == 27000
    assert st.phaselink is False


def test_timeout_clears_pending(ctrl):
    ctrl.set_field(0, "freq", 1000)
    assert ctrl._pending is not None
    ctrl._on_timeout()
    assert ctrl._pending is None


def test_base_harm_send_and_resync(ctrl):
    # 谐波字段:发 SET;OK 后应自动 GET 该通道回读归一化值
    ctrl.set_field(0, "base", 80)
    assert ctrl.worker.sent[-1] == "SET CH1 BASE 80\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET CH1\r\n"      # ack 后自动回读
    assert ctrl._pending == (0, "status", None)


def test_harm_order_field(ctrl):
    ctrl.set_field(0, "h1_order", 3)
    assert ctrl.worker.sent[-1] == "SET CH1 HARM1 ORDER 3\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET CH1\r\n"


def test_harm_invalid_rejected(ctrl):
    ctrl.set_field(0, "h1_order", 9)
    assert ctrl.worker.sent == []     # 校验前置,不发命令


def test_extended_status_updates_harms(ctrl):
    ctrl._on_line("CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 H2 5 8 H3 0 0 H4 0 0")
    st = ctrl.state[0]
    assert st.base_pct == 68
    assert st.harms == [(3, 20), (5, 8), (0, 0), (0, 0)]


def test_mod_field_sends_command(ctrl):
    ctrl.set_field(0, "mode", "FM")
    assert ctrl.worker.sent[-1] == "SET CH1 MODE FM\r\n"
    ctrl._on_line("OK")
    ctrl.set_field(1, "fmdev", 10000)
    assert ctrl.worker.sent[-1] == "SET CH2 FMDEV 10000\r\n"
    ctrl._on_line("OK")
    ctrl.set_field(0, "pmdev", 128)
    assert ctrl.worker.sent[-1] == "SET CH1 PMDEV 128\r\n"
    ctrl._on_line("OK")
    ctrl.set_field(0, "mwave", "SQUARE")
    assert ctrl.worker.sent[-1] == "SET CH1 MODWAVE SQUARE\r\n"
    ctrl._on_line("OK")
    ctrl.set_field(0, "modfreq", 50)
    assert ctrl.worker.sent[-1] == "SET CH1 MODFREQ 50\r\n"
    ctrl._on_line("OK")
    ctrl.set_field(0, "amdepth", 60)
    assert ctrl.worker.sent[-1] == "SET CH1 AMDEPTH 60\r\n"


def test_mod_invalid_rejected(ctrl):
    ctrl.set_field(0, "mode", "FOO")
    assert ctrl.worker.sent == []


def test_mod_ack_applies_state(ctrl):
    ctrl.set_field(0, "mode", "FM")
    ctrl._on_line("OK")
    assert ctrl.state[0].mode == "FM"
    ctrl.set_field(0, "fmdev", 10000)
    ctrl._on_line("OK")
    assert ctrl.state[0].fm_dev == 10000
    ctrl.set_field(0, "modfreq", 50)
    ctrl._on_line("OK")
    assert ctrl.state[0].mfreq_01 == 50
    ctrl.set_field(0, "amdepth", 60)
    ctrl._on_line("OK")
    assert ctrl.state[0].am_depth == 60
    ctrl.set_field(0, "pmdev", 32)
    ctrl._on_line("OK")
    assert ctrl.state[0].pm_dev == 32
    ctrl.set_field(0, "mwave", "SAW")
    ctrl._on_line("OK")
    assert ctrl.state[0].mwave == "SAW"


def test_mod_status_updates_state(ctrl):
    ctrl._on_line("CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 H2 0 0 "
                  "H3 0 0 H4 0 0 MODE PM MODWAVE SAW MODFREQ 50 AMDEPTH 60 "
                  "FMDEV 5000 PMDEV 32")
    st = ctrl.state[0]
    assert st.mode == "PM" and st.mwave == "SAW"
    assert st.mfreq_01 == 50 and st.am_depth == 60
    assert st.fm_dev == 5000 and st.pm_dev == 32


def test_digmod_default_off(ctrl):
    assert ctrl.digmod.mode == "OFF"
    assert ctrl.digmod.source == "PRBS7"
    assert ctrl.digmod.filter == "RECT"
    assert ctrl.digmod.rolloff == "0.35"
    assert ctrl.digmod.symrate_hz == 10000
    assert ctrl.digmod.running is False


def test_set_digmod_seed_waits_for_get_confirmation(ctrl):
    ctrl.set_digmod_seed(5)
    assert ctrl.worker.sent[-1] == "SET DIGMOD SEED 5\r\n"
    assert ctrl._pending == (None, "digmod_seed", 5)
    assert ctrl.digmod.seed == 127
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"
    ctrl._on_line("DIGMOD MODE OFF SOURCE PRBS7 FILTER RECT ROLLOFF 0.35 SEED 5 SYMRATE 10000 STATE STOPPED")
    assert ctrl.digmod.seed == 5


def test_set_digmod_reset_sends_command(ctrl):
    ctrl.set_digmod_reset()
    assert ctrl.worker.sent[-1] == "SET DIGMOD RESET\r\n"
    assert ctrl._pending == (None, "digmod_reset", None)


def test_set_digmod_filter_waits_for_get_confirmation(ctrl):
    ctrl.set_digmod_filter("RRC")
    assert ctrl.worker.sent[-1] == "SET DIGMOD FILTER RRC\r\n"
    assert ctrl.digmod.filter == "RECT"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"
    ctrl._on_line("DIGMOD MODE OFF SOURCE PRBS7 FILTER RRC ROLLOFF 0.35 SYMRATE 10000 STATE STOPPED")
    assert ctrl.digmod.filter == "RRC"


def test_filter_error_and_timeout_keep_confirmed_state(ctrl):
    ctrl.set_digmod_filter("RRC")
    ctrl._on_line("ERR DIGMOD RUNNING")
    assert ctrl.digmod.filter == "RECT"
    ctrl.set_digmod_filter("RRC")
    ctrl._on_timeout()
    assert ctrl.digmod.filter == "RECT"


    ctrl.set_digmod_symrate(100000)
    assert ctrl.worker.sent[-1] == "SET DIGMOD SYMRATE 100000\r\n"
    assert ctrl._pending == (None, "digmod_symrate", 100000)


def test_set_digmod_rolloff_waits_for_get_confirmation(ctrl):
    ctrl.set_digmod_rolloff("0.2")
    assert ctrl.worker.sent[-1] == "SET DIGMOD ROLLOFF 0.2\r\n"
    assert ctrl._pending == (None, "digmod_rolloff", "0.2")
    assert ctrl.digmod.rolloff == "0.35"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"
    assert ctrl.digmod.rolloff == "0.35"
    ctrl._on_line("DIGMOD MODE OFF SOURCE PRBS7 FILTER RRC ROLLOFF 0.2 SYMRATE 10000 STATE STOPPED")
    assert ctrl.digmod.rolloff == "0.2"


def test_set_digmod_rolloff_invalid_no_send(ctrl):
    ctrl.set_digmod_rolloff("0.9")
    assert ctrl.worker.sent == []
    assert ctrl._pending is None
    assert ctrl.digmod.rolloff == "0.35"


def test_digmod_rolloff_error_keeps_confirmed_state(ctrl):
    ctrl.set_digmod_rolloff("0.2")
    ctrl._on_line("ERR DIGMOD RUNNING")
    assert ctrl.digmod.rolloff == "0.35"
    assert ctrl._pending is None


def test_digmod_symrate_ack_requests_confirmed_status(ctrl):
    ctrl.set_digmod_symrate(100000)
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"
    assert ctrl.digmod.symrate_hz == 10000
    ctrl._on_line("DIGMOD MODE OFF SOURCE PRBS7 FILTER RECT ROLLOFF 0.35 SYMRATE 100000 STATE STOPPED")
    assert ctrl.digmod.symrate_hz == 100000
    ctrl.set_digmod_symrate(250000)
    ctrl._on_line("ERR DIGMOD RUNNING")
    assert ctrl.digmod.symrate_hz == 100000


def test_set_digmod_sends_command(ctrl):
    ctrl.set_digmod("QAM16")
    assert ctrl.worker.sent[-1] == "SET DIGMOD MODE QAM16\r\n"
    assert ctrl._pending == (None, "digmod", "QAM16")


def test_set_digmod_invalid_no_send(ctrl):
    ctrl.set_digmod("QAM8")
    assert ctrl.worker.sent == []
    assert ctrl._pending is None


def test_digmod_ack_requests_confirmed_status(ctrl):
    ctrl.set_digmod("QAM16")
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"
    assert ctrl.digmod.mode == "OFF"
    ctrl._on_line("DIGMOD MODE QAM16 SOURCE PRBS7 FILTER RECT ROLLOFF 0.35 SYMRATE 10000 STATE RUNNING")
    assert ctrl.digmod.mode == "QAM16"


def test_digmod_status_line_updates_state(ctrl):
    ctrl._on_line("DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.35 SYMRATE 100000 STATE RUNNING")
    assert ctrl.digmod.mode == "QPSK"
    assert ctrl.digmod.source == "PRBS7"
    assert ctrl.digmod.filter == "RRC"
    assert ctrl.digmod.symrate_hz == 100000
    assert ctrl.digmod.running is True


def test_digmod_ack_error_keeps_state(ctrl):
    ctrl.set_digmod("QAM16")
    ctrl._on_line("ERR BAD VALUE")
    assert ctrl.digmod.mode == "OFF"


def test_digmod_ack_error_emits_confirmed_mode_without_mutating_state(ctrl):
    got = []
    ctrl.digmod.mode = "BPSK"
    ctrl.digmod_changed.connect(lambda mode: got.append(mode))

    ctrl.set_digmod("QAM16")
    ctrl._on_line("ERR BAD VALUE")

    assert ctrl.digmod.mode == "BPSK"
    assert got == ["BPSK"]


def test_digmod_timeout_emits_confirmed_mode(ctrl):
    got = []
    ctrl.digmod.mode = "QPSK"
    ctrl.digmod_changed.connect(lambda mode: got.append(mode))

    ctrl.set_digmod("QAM16")
    ctrl._on_timeout()

    assert ctrl.digmod.mode == "QPSK"
    assert got == ["QPSK"]


def test_request_digmod_status_sends_get(ctrl):
    ctrl.request_digmod_status()
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"


def test_commands_are_serialized_without_overwriting_active(ctrl):
    ctrl.set_field(0, "freq", 2000)
    ctrl.set_field(0, "ampl", 60)

    assert ctrl.worker.sent == ["SET CH1 FREQ 2000\r\n"]
    assert ctrl._pending == (0, "freq", 2000)

    ctrl._on_line("OK")
    assert ctrl.worker.sent == [
        "SET CH1 FREQ 2000\r\n",
        "SET CH1 AMPL 60\r\n",
    ]
    assert ctrl._pending == (0, "ampl", 60)


def test_status_request_is_strictly_ordered(ctrl):
    ctrl.request_status()
    assert ctrl.worker.sent == ["GET CH1\r\n"]

    ctrl._on_line("CH2 WAVE SINE FREQ 2200 AMPL 40")
    assert ctrl.worker.sent == ["GET CH1\r\n"]

    ctrl._on_line("CH1 WAVE SINE FREQ 1100 AMPL 30")
    assert ctrl.worker.sent == ["GET CH1\r\n", "GET CH2\r\n"]


def test_welcome_and_unknown_lines_do_not_consume_active_command(ctrl):
    ctrl.set_field(0, "freq", 2000)
    ctrl._on_line("AC820-AWG bridge ready")
    ctrl._on_line("UNEXPECTED INPUT")

    assert ctrl._pending == (0, "freq", 2000)
    ctrl._on_line("OK")
    assert ctrl._pending is None


def test_harmonic_confirmation_get_is_queued_ahead_of_later_commands(ctrl):
    ctrl.set_field(0, "base", 80)
    ctrl.set_field(0, "freq", 2000)
    ctrl._on_line("OK")

    assert ctrl.worker.sent == [
        "SET CH1 BASE 80\r\n",
        "GET CH1\r\n",
    ]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "SET CH1 FREQ 2000\r\n"


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)


class _InitWorker(FakeWorker):
    def __init__(self, port, baud, parent=None):
        super().__init__()
        self.line_received = _FakeSignal()
        self.connection_lost = _FakeSignal()

    def open(self):
        return None

    def start(self):
        pass


def test_connect_waits_for_complete_hardware_state_before_ready(monkeypatch):
    monkeypatch.setattr("siggen.transport.SerialWorker", _InitWorker)
    ctrl = Controller()
    ready = []
    ctrl.connected.connect(lambda: ready.append(True))

    assert ctrl.connect_serial("COM1", 115200) is None
    assert ctrl.worker.sent == ["GET CH1\r\n"]
    assert ready == []

    ch1 = ("CH1 WAVE SINE FREQ 1000 AMPL 50 DUTY 50 BASE 100 "
           "H1 0 0 H2 0 0 H3 0 0 H4 0 0 MODE OFF MODWAVE SINE "
           "MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
    ch2 = ch1.replace("CH1", "CH2", 1)
    dig = ("DIGMOD MODE OFF SOURCE PRBS7 FILTER RECT ROLLOFF 0.35 "
           "SEED 127 SYMRATE 10000 STATE STOPPED")
    ctrl._on_line(ch1)
    assert ctrl.worker.sent == ["GET CH1\r\n", "GET CH2\r\n"]
    ctrl._on_line(ch2)
    assert ctrl.worker.sent == ["GET CH1\r\n", "GET CH2\r\n", "GET DIGMOD\r\n"]
    assert ready == []
    ctrl._on_line(dig)
    assert ctrl.worker.sent[-1] == "GET PLAY\r\n"
    assert ready == []
    ctrl._on_line("PLAY STATE IDLE FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 0 "
                   "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 0 ERROR 00")
    assert ready == [True]


def test_connect_requests_ddr_pp_status_after_ready(monkeypatch):
    monkeypatch.setattr("siggen.transport.SerialWorker", _InitWorker)
    ctrl = Controller()
    changed = []
    ctrl.ddr_pp_changed.connect(changed.append)

    ctrl.connect_serial("COM1", 115200)
    ch1 = ("CH1 WAVE SINE FREQ 1000 AMPL 50 DUTY 50 BASE 100 "
           "H1 0 0 H2 0 0 H3 0 0 H4 0 0 MODE OFF MODWAVE SINE "
           "MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
    ctrl._on_line(ch1)
    ctrl._on_line(ch1.replace("CH1", "CH2", 1))
    ctrl._on_line("DIGMOD MODE OFF SOURCE PRBS7 FILTER RECT ROLLOFF 0.35 "
                  "SEED 127 SYMRATE 10000 STATE STOPPED")
    ctrl._on_line("PLAY STATE IDLE FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 0 "
                  "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 0 ERROR 00")

    # The DDR user-stream panel needs the initial bank metadata to enable START.
    assert ctrl.worker.sent[-1] == "GET DDR PP\r\n"
    ctrl._on_line("DDR PP ACTIVE 0 ACTIVE_BANK NONE NEXT_BANK NONE STATE IDLE "
                  "ERROR 00 BANK0 VALID 0 LENGTH 0 CRC 0000 "
                  "BANK1 VALID 0 LENGTH 0 CRC 0000")
    assert changed and changed[-1]["state"] == "IDLE"


def test_connection_lost_clears_active_and_queued_commands(ctrl):
    lost = []
    ctrl.disconnected.connect(lost.append)
    ctrl.set_field(0, "freq", 2000)
    ctrl.set_field(0, "ampl", 60)

    ctrl._on_lost("USB unplugged")

    assert ctrl.worker is None
    assert ctrl._pending is None
    assert lost == ["USB unplugged"]
    sent = list(ctrl.worker.sent) if ctrl.worker is not None else []
    ctrl.set_field(0, "freq", 3000)
    assert sent == []


def test_expected_status_parse_error_aborts_initialization(monkeypatch):
    monkeypatch.setattr("siggen.transport.SerialWorker", _InitWorker)
    ctrl = Controller()
    disconnected = []
    ctrl.disconnected.connect(disconnected.append)
    ctrl.connect_serial("COM1", 115200)

    ctrl._on_line("CH1 malformed")

    assert ctrl.worker is None
    assert ctrl.ready is False
    assert disconnected == ["protocol parse error"]





def test_user_upload_is_serialized_and_waits_for_confirmations(ctrl):
    progress = []
    statuses = []
    ctrl.user_upload_progress.connect(lambda sent, total: progress.append((sent, total)))
    ctrl.userbuf_changed.connect(statuses.append)

    ctrl.upload_user_data(bytes(range(21)), loop=True)
    assert ctrl.worker.sent == ["SET USER CLEAR\r\n"]
    assert progress == [(0, 21)]

    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET USER CHUNK 0 ")
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET USER CHUNK 20 ")
    assert progress == [(0, 21), (20, 21)]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET USER COMMIT 168 ")
    assert progress == [(0, 21), (20, 21), (21, 21)]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "SET USER LOOP ON\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET USERBUF\r\n"
    ctrl._on_line(
        "USERBUF VALID 1 LENGTH 168 CRC 4A8B BANK 1 UPLOAD IDLE LOOP ON "
        "COMMIT_SEEN 1 READY 1 DONE 0 UNDERFLOW 0"
    )
    assert statuses and statuses[-1]["bit_length"] == 168
    assert ctrl._pending is None


def test_user_upload_accepts_file_path(ctrl, tmp_path):
    path = tmp_path / "payload.bin"
    path.write_bytes(b"\xaa\x55")

    ctrl.upload_user_data(str(path), loop=True)

    assert ctrl.worker.sent == ["SET USER CLEAR\r\n"]
    assert ctrl._user_upload.data == b"\xaa\x55"
    assert ctrl._user_upload.loop is True


def test_user_upload_failure_clears_batch_without_mutating_digmod(ctrl):
    ctrl.digmod.source = "PRBS7"
    ctrl.upload_user_data(b"payload")
    ctrl._on_line("ERR USER CHUNK CRC")

    assert ctrl._user_upload is None
    assert ctrl.digmod.source == "PRBS7"
    sent = list(ctrl.worker.sent)
    ctrl._on_line("OK")
    assert ctrl.worker.sent == sent


def test_user_source_command_is_confirmed_by_digmod_status(ctrl):
    ctrl.set_digmod_source("USER")
    assert ctrl.worker.sent[-1] == "SET DIGMOD SOURCE USER\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"
    ctrl._on_line(
        "DIGMOD MODE OFF SOURCE USER FILTER RECT ROLLOFF 0.35 "
        "SEED 127 SYMRATE 10000 STATE STOPPED"
    )
    assert ctrl.digmod.source == "USER"


def test_play_commands_are_serialized_and_status_is_confirmed(ctrl):
    changes = []
    ctrl.play_changed.connect(changes.append)
    ctrl.set_play_mode("MANUAL")
    ctrl.set_play_repeat(2)
    ctrl.set_play_trigger("SOFTWARE")
    assert ctrl.worker.sent == ["SET PLAY MODE MANUAL\r\n"]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET PLAY\r\n"
    ctrl._on_line("PLAY STATE IDLE FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 0 "
                   "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 0 ERROR 00")
    assert ctrl.worker.sent[-1] == "SET PLAY REPEAT 2\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET PLAY\r\n"
    ctrl._on_line("PLAY STATE IDLE FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 0 "
                   "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 0 ERROR 00")
    assert ctrl.worker.sent[-1] == "SET PLAY TRIGGER SOFTWARE\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET PLAY\r\n"
    ctrl._on_line("PLAY STATE IDLE FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 0 "
                   "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 0 ERROR 00")
    ctrl.request_play_status()
    assert ctrl.worker.sent[-1] == "GET PLAY\r\n"
    ctrl._on_line("PLAY STATE ARMED FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 0 "
                   "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 2 ERROR 00")
    assert ctrl.play["state"] == "ARMED"
    assert changes[-1]["repeat_remaining"] == 2


def test_initialization_tolerates_legacy_play_unsupported(monkeypatch):
    monkeypatch.setattr("siggen.transport.SerialWorker", _InitWorker)
    ctrl = Controller()
    ready = []
    ctrl.connected.connect(lambda: ready.append(True))

    assert ctrl.connect_serial("COM1", 115200) is None
    ch1 = ("CH1 WAVE SINE FREQ 1000 AMPL 50 DUTY 50 BASE 100 "
           "H1 0 0 H2 0 0 H3 0 0 H4 0 0 MODE OFF MODWAVE SINE "
           "MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
    ch2 = ch1.replace("CH1", "CH2", 1)
    dig = ("DIGMOD MODE OFF SOURCE PRBS7 FILTER RECT ROLLOFF 0.35 "
           "SEED 127 SYMRATE 10000 STATE STOPPED")
    ctrl._on_line(ch1)
    ctrl._on_line(ch2)
    ctrl._on_line(dig)

    assert ctrl.worker.sent[-1] == "GET PLAY\r\n"
    ctrl._on_line("ERR PLAY UNSUPPORTED")

    assert ready == [True]
    assert ctrl.worker is not None
    assert ctrl.ready is True


def test_ddr_upload_is_serialized_and_committed(ctrl):
    progress = []
    statuses = []
    ctrl.ddr_upload_progress.connect(lambda sent, total: progress.append((sent, total)))
    ctrl.ddr_changed.connect(statuses.append)

    ctrl.upload_ddr_data(bytes(range(21)))
    assert ctrl.worker.sent == ["SET DDR CLEAR\r\n"]
    assert progress == [(0, 21)]

    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR CHUNK 0 ")
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR CHUNK 20 ")
    assert progress == [(0, 21), (20, 21)]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR COMMIT 168 ")
    assert progress == [(0, 21), (20, 21), (21, 21)]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DDR\r\n"
    ctrl._on_line(
        "DDR VALID 1 BUFFER 00300000 MAX 1048576 LENGTH 168 CRC 4A8B "
        "UPLOAD IDLE ACTIVE 0 DMA READY ERROR 00"
    )
    assert statuses and statuses[-1]["bit_length"] == 168
    assert ctrl._ddr_upload is None
    assert ctrl._pending is None


def test_ddr_upload_failure_clears_batch_without_mutating_state(ctrl):
    ctrl.upload_ddr_data(b"payload")
    ctrl._on_line("ERR DDR CHUNK CRC")

    assert ctrl._ddr_upload is None
    assert ctrl.ddr is None
    sent = list(ctrl.worker.sent)
    ctrl._on_line("OK")
    assert ctrl.worker.sent == sent


def test_ddr_start_stop_are_confirmed_by_status(ctrl):
    ctrl.ddr_start()
    assert ctrl.worker.sent == ["SET DDR START\r\n"]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DDR\r\n"
    ctrl._on_line(
        "DDR VALID 1 BUFFER 00300000 MAX 1048576 LENGTH 168 CRC 4A8B "
        "UPLOAD IDLE ACTIVE 1 DMA RUNNING ERROR 00"
    )
    assert ctrl.ddr["active"] is True
    assert ctrl.ddr["dma_state"] == "RUNNING"

    ctrl.ddr_stop()
    assert ctrl.worker.sent[-1] == "SET DDR STOP\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DDR\r\n"
    ctrl._on_line(
        "DDR VALID 1 BUFFER 00300000 MAX 1048576 LENGTH 168 CRC 4A8B "
        "UPLOAD IDLE ACTIVE 0 DMA READY ERROR 00"
    )
    assert ctrl.ddr["active"] is False


def test_ddr_invalid_upload_is_rejected_without_send(ctrl):
    ctrl.upload_ddr_data(b"", bit_length=0)
    assert ctrl.worker.sent == []
    assert ctrl._pending is None


def test_ddr_status_request_sends_get(ctrl):
    ctrl.request_ddr_status()
    assert ctrl.worker.sent[-1] == "GET DDR\r\n"


def test_play_start_and_stop_use_existing_transaction_queue(ctrl):
    ctrl.play_start()
    ctrl.play_stop()
    assert ctrl.worker.sent == ["SET PLAY START\r\n"]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET PLAY\r\n"
    ctrl._on_line("PLAY STATE STARTING FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 0 "
                   "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 1 ERROR 00")
    assert ctrl.worker.sent[-1] == "SET PLAY STOP\r\n"


def test_apply_channel_impairment_sends_taps_then_commit(ctrl):
    ctrl.apply_channel_impairment(1, True, 40, [(255, 0), (0, 0), (0, 0), (0, 0)])
    # _pump sends exactly one command and waits for the ACK before the next.
    assert ctrl.worker.sent == ["SET CH1 CHAN ON\r\n"]
    for _ in range(6):
        ctrl._on_line("OK")
    assert ctrl.worker.sent == [
        "SET CH1 CHAN ON\r\n",
        "SET CH1 NOISE 40\r\n",
        "SET CH1 TAP 0 255 0\r\n",
        "SET CH1 TAP 1 0 0\r\n",
        "SET CH1 TAP 2 0 0\r\n",
        "SET CH1 TAP 3 0 0\r\n",
        "SET CH1 CHAN COMMIT\r\n",
    ]
    ctrl._on_line("OK")
    assert ctrl._pending is None


def test_apply_channel_impairment_not_connected_logs(ctrl):
    ctrl.worker = None
    logs = []
    ctrl.log.connect(lambda text, level: logs.append((text, level)))
    ctrl.apply_channel_impairment(1, True, 40, [(255, 0)] * 4)
    assert logs == [("not connected", "error")]


def test_apply_channel_impairment_invalid_tap_is_atomic(ctrl):
    ctrl.apply_channel_impairment(1, True, 40, [(256, 0), (0, 0), (0, 0), (0, 0)])
    assert ctrl.worker.sent == []


def test_request_channel_status_parses_and_emits(ctrl):
    got = []
    ctrl.chan_status_changed.connect(lambda ch, data: got.append((ch, data)))
    ctrl.request_channel_status(2)
    assert ctrl.worker.sent[-1] == "GET CH2 CHAN\r\n"
    ctrl._on_line("CH2 CHAN ON NOISE 20 TAP0 255 0 TAP1 0 2 TAP2 0 0 TAP3 0 0")
    assert got == [(1, {"enabled": True, "noise": 20, "tone": (0, 0),
                        "impulse": None,
                        "taps": [(255, 0), (0, 2), (0, 0), (0, 0)]})]
    assert ctrl._pending is None


def test_request_channel_status_accepts_fresh_board_idle_line(ctrl):
    # Power-up/reset emits IMPULSE 0 0 0 (chan_imp is zero-initialised in the
    # firmware).  The parser must treat that as "never configured" and still
    # emit chan_status_changed, otherwise the GUI shows "--" for every channel.
    got = []
    logs = []
    ctrl.chan_status_changed.connect(lambda ch, data: got.append((ch, data)))
    ctrl.log.connect(lambda text, level: logs.append((text, level)))
    ctrl.request_channel_status(1)
    assert ctrl.worker.sent[-1] == "GET CH1 CHAN\r\n"
    ctrl._on_line("CH1 CHAN OFF NOISE 0 TONE 0 0 IMPULSE 0 0 0 "
                  "TAP0 0 0 TAP1 0 0 TAP2 0 0 TAP3 0 0")
    assert got == [(0, {"enabled": False, "noise": 0, "tone": (0, 0),
                        "impulse": None, "taps": [(0, 0)] * 4})]
    assert not any("protocol parse error" in text for text, _ in logs)
    assert ctrl._pending is None
