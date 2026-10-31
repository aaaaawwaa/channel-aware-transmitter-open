"""Task 3 gate: default DDR PP upload runs on the UDP worker, UART keeps the
control plane only (GET status / recovery), and the serial stop-and-wait path
survives as an explicit fallback."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from siggen.controller import Controller
from siggen.protocol import crc16_ccitt
from siggen.udp_ddr_pp import UploadResult


app = QApplication.instance() or QApplication([])


class FakeSerialWorker:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)

    def stop(self):
        pass


class FakeUdpClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port


class FakeUdpWorker(QObject):
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, client, bank, payload, parent=None):
        super().__init__(parent)
        self.client = client
        self.bank = bank
        self.payload = bytes(payload)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture
def ctrl():
    controller = Controller()
    controller.worker = FakeSerialWorker()
    created = SimpleNamespace(workers=[], clients=[])

    def client_factory(host, port):
        client = FakeUdpClient(host, port)
        created.clients.append(client)
        return client

    def worker_factory(client, bank, payload):
        worker = FakeUdpWorker(client, bank, payload)
        created.workers.append(worker)
        return worker

    controller.udp_pp_client_factory = client_factory
    controller.udp_pp_worker_factory = worker_factory
    controller.created = created
    return controller


def _result(total=32):
    return UploadResult(bytes_sent=total, elapsed_s=0.1,
                        effective_mb_s=total / 1e5, retransmits=0,
                        payload_crc32=1, device_crc32=1)


def _status_line(crc16="0000", length=32):
    return (
        "DDR PP ACTIVE 0 ACTIVE_BANK NONE NEXT_BANK NONE STATE IDLE ERROR 00 "
        f"BANK0 VALID 0 LENGTH 0 CRC 0000 BANK1 VALID 1 LENGTH {length} CRC {crc16}"
    )


def test_default_upload_starts_udp_worker_and_sends_no_uart_payload(ctrl):
    progress = []
    ctrl.ddr_pp_upload_progress.connect(lambda sent, total: progress.append((sent, total)))
    payload = bytes(range(64))

    ctrl.upload_ddr_pp_data(1, payload)

    assert len(ctrl.created.workers) == 1
    worker = ctrl.created.workers[0]
    assert worker.started is True
    assert worker.bank == 1
    assert worker.payload == payload
    assert ctrl.created.clients[0].host == "192.0.2.10"
    assert ctrl.created.clients[0].port == 40100
    assert ctrl.worker.sent == []
    assert progress == [(0, len(payload))]

    worker.progress.emit(32, len(payload))
    assert progress == [(0, len(payload)), (32, len(payload))]


def test_udp_completion_refreshes_ddr_pp_status_once_over_uart(ctrl):
    payload = b"\x01\x02\x03\x04" * 8
    ctrl.upload_ddr_pp_data(0, payload)
    worker = ctrl.created.workers[0]

    worker.completed.emit(_result(len(payload)))

    assert ctrl.worker.sent == ["GET DDR PP\r\n"]
    assert ctrl._ddr_pp_udp_worker is None
    assert worker.stopped is True
    ctrl._on_line(_status_line(f"{crc16_ccitt(payload):04X}"))
    assert ctrl.ddr_pp is not None


def test_udp_failure_reports_error_without_uart_payload_commands(ctrl):
    logs = []
    ctrl.log.connect(lambda message, level: logs.append((message, level)))
    ctrl.upload_ddr_pp_data(0, b"\x00" * 8)
    worker = ctrl.created.workers[0]

    worker.failed.emit("device rejected transaction: BUSY")

    assert ctrl._ddr_pp_udp_worker is None
    assert worker.stopped is True
    assert ctrl.worker.sent == []
    assert any("BUSY" in message and level == "error" for message, level in logs)


def test_second_udp_upload_is_rejected_while_first_worker_runs(ctrl):
    ctrl.upload_ddr_pp_data(0, b"\x00" * 8)
    logs = []
    ctrl.log.connect(lambda message, level: logs.append((message, level)))

    ctrl.upload_ddr_pp_data(1, b"\x00" * 8)

    assert len(ctrl.created.workers) == 1
    assert any("busy" in message and level == "error" for message, level in logs)


def test_udp_upload_is_rejected_while_serial_upload_is_active(ctrl):
    ctrl.upload_ddr_pp_data_serial(0, b"\x00" * 8)

    ctrl.upload_ddr_pp_data(0, b"\x00" * 8)

    assert ctrl.created.workers == []
    assert ctrl.worker.sent == ["SET DDR PP CLEAR\r\n"]


def test_udp_upload_emits_finished_signal(ctrl):
    finished = []
    ctrl.ddr_pp_upload_finished.connect(finished.append)
    payload = b"\x01\x02\x03\x04" * 8

    ctrl.upload_ddr_pp_data(0, payload)
    ctrl.created.workers[0].completed.emit(_result(len(payload)))
    assert finished == [True]

    ctrl.upload_ddr_pp_data(1, payload)
    ctrl.created.workers[1].failed.emit("boom")
    assert finished == [True, False]


def test_udp_upload_requires_a_serial_connection(ctrl):
    ctrl.worker = None

    ctrl.upload_ddr_pp_data(0, b"\x00" * 8)

    assert ctrl.created.workers == []


@pytest.mark.parametrize("bank", [-1, 2, True, "0"])
def test_udp_upload_rejects_invalid_bank_without_worker(ctrl, bank):
    ctrl.upload_ddr_pp_data(bank, b"\x00\x01\x02\x03")
    assert ctrl.created.workers == []
    assert ctrl.worker.sent == []


def test_udp_upload_rejects_unaligned_payload_without_worker(ctrl):
    ctrl.upload_ddr_pp_data(0, b"\x00\x01\x02")
    assert ctrl.created.workers == []
    assert ctrl.worker.sent == []


def test_disconnect_stops_a_running_udp_worker(ctrl):
    ctrl.upload_ddr_pp_data(0, b"\x00" * 8)
    worker = ctrl.created.workers[0]

    ctrl.disconnect()

    assert worker.stopped is True
    assert ctrl._ddr_pp_udp_worker is None


def test_serial_fallback_retains_clear_chunk_commit_sequence(ctrl):
    progress = []
    statuses = []
    ctrl.ddr_pp_upload_progress.connect(lambda sent, total: progress.append((sent, total)))
    ctrl.ddr_pp_changed.connect(statuses.append)
    payload = bytes(range(24))

    ctrl.upload_ddr_pp_data_serial(1, payload)

    assert ctrl.created.workers == []
    assert ctrl.worker.sent == ["SET DDR PP CLEAR\r\n"]
    assert progress == [(0, 24)]

    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR PP CHUNK 1 0 ")
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR PP CHUNK 1 20 ")
    assert progress == [(0, 24), (20, 24)]
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1].startswith("SET DDR PP COMMIT 1 192 ")
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DDR PP\r\n"

    ctrl._on_line(_status_line(f"{crc16_ccitt(payload):04X}", length=192))
    assert statuses[-1]["bank1"]["bit_length"] == 192
    assert ctrl._ddr_pp_upload is None
