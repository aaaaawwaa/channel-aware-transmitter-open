import pytest
from PySide6.QtWidgets import QApplication

from siggen.controller import Controller
from siggen.protocol import crc16_ccitt


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
    controller = Controller()
    controller.worker = FakeWorker()
    return controller


def _pp_method(ctrl, name):
    return getattr(ctrl, name)


def _status_line(crc16="0000", active="0", active_bank="NONE", next_bank="NONE",
                 state="IDLE", error="00"):
    return (
        f"DDR PP ACTIVE {active} ACTIVE_BANK {active_bank} NEXT_BANK {next_bank} "
        f"STATE {state} ERROR {error} "
        f"BANK0 VALID 0 LENGTH 0 CRC 0000 BANK1 VALID 1 LENGTH 192 CRC {crc16}"
    )


def test_ddr_pp_upload_serializes_bank_chunks_and_confirmed_status(ctrl):
    progress = []
    statuses = []
    ctrl.ddr_pp_upload_progress.connect(lambda sent, total: progress.append((sent, total)))
    ctrl.ddr_pp_changed.connect(statuses.append)
    payload = bytes(range(24))

    _pp_method(ctrl, "upload_ddr_pp_data_serial")(1, payload)
    assert ctrl.worker.sent == ["SET DDR PP CLEAR\r\n"]
    assert progress == [(0, 24)]

    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR PP CHUNK 1 0 ")
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR PP CHUNK 1 20 ")
    assert progress == [(0, 24), (20, 24)]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR PP COMMIT 1 192 ")
    assert progress == [(0, 24), (20, 24), (24, 24)]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DDR PP\r\n"
    ctrl._on_line(_status_line(f"{crc16_ccitt(payload):04X}"))

    assert statuses[-1]["bank1"]["bit_length"] == 192
    assert statuses[-1]["bank1"]["valid"] is True
    assert ctrl._ddr_pp_upload is None
    assert ctrl._pending is None


def test_ddr_pp_status_request_parses_lifecycle_and_banks(ctrl):
    _pp_method(ctrl, "request_ddr_pp_status")()
    assert ctrl.worker.sent == ["GET DDR PP\r\n"]

    ctrl._on_line(_status_line("1234", active="1", active_bank="0", next_bank="1",
                               state="DRAIN", error="23"))

    assert ctrl.ddr_pp["active"] is True
    assert ctrl.ddr_pp["active_bank"] == 0
    assert ctrl.ddr_pp["next_bank"] == 1
    assert ctrl.ddr_pp["state"] == "DRAIN"
    assert ctrl.ddr_pp["error_code"] == 0x23


def test_ddr_pp_start_stop_use_serialized_status_confirmation(ctrl):
    _pp_method(ctrl, "ddr_pp_start")()
    assert ctrl.worker.sent == ["SET DDR PP START\r\n"]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DDR PP\r\n"
    ctrl._on_line(_status_line("1234", active="1", active_bank="0",
                               next_bank="1", state="RUNNING"))
    assert ctrl.ddr_pp["active"] is True

    _pp_method(ctrl, "ddr_pp_stop")()
    assert ctrl.worker.sent[-1] == "SET DDR PP STOP\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DDR PP\r\n"
    ctrl._on_line(_status_line("1234"))


@pytest.mark.parametrize("bank", [-1, 2, True, "0"])
def test_ddr_pp_upload_rejects_invalid_bank_without_send(ctrl, bank):
    _pp_method(ctrl, "upload_ddr_pp_data_serial")(bank, b"\x00\x01\x02\x03")
    assert ctrl.worker.sent == []
    assert ctrl._ddr_pp_upload is None


def test_ddr_pp_upload_rejects_unaligned_payload_without_send(ctrl):
    _pp_method(ctrl, "upload_ddr_pp_data_serial")(0, b"\x00\x01\x02")
    assert ctrl.worker.sent == []
    assert ctrl._ddr_pp_upload is None


def test_ddr_pp_upload_error_clears_batch_without_mutating_status(ctrl):
    _pp_method(ctrl, "upload_ddr_pp_data_serial")(0, b"payload")
    ctrl._on_line("ERR DDR PP CHUNK CRC")
    assert ctrl._ddr_pp_upload is None
    assert ctrl.ddr_pp is None
    assert ctrl._pending is None
