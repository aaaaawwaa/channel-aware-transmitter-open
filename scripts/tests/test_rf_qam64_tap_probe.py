import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rf_qam64_digital_tap_probe_runner import RawProtocolRecorder, _configure


def test_raw_refusal_then_same_connection_status_is_recorded(tmp_path):
    class Client:
        def __init__(self):
            self.calls = []
            self._session_id = 27

        def _send_and_read_json(self, sock, buffer, deadline, request_id, op,
                                args, payload=b"", validate_id=True):
            self.calls.append((request_id, op))
            if op == "loopback.digital.capture":
                return ({"id": request_id, "ok": False,
                         "error": {"code": "TIMEOUT",
                                   "message": "the hardware did not respond in time"}},
                        b"")
            assert op == "pl.status.get"
            assert request_id in (28, 29, 30)
            return ({"id": request_id, "ok": True, "result": {
                "io_ok": True, "sampled_at_monotonic_ns": 123,
                "rx": {"armed": False, "done": False, "position_words": 0},
                "tx": {"source_selected": False, "length_words": 2540,
                       "position_words": 0}}}, b"")

    recorder = RawProtocolRecorder(tmp_path / "raw.jsonl")
    c = Client()
    recorder.install(c)
    got = c._send_and_read_json(
        None, b"", time.monotonic() + 1.0, 27,
        "loopback.digital.capture", {})
    assert got[0]["error"]["code"] == "TIMEOUT"
    assert c.calls[0] == (27, "loopback.digital.capture")
    assert [call[1] for call in c.calls[1:]] == ["pl.status.get"] * 3
    assert len(recorder.events) == 2
    raw, status = recorder.events
    assert raw["error_code"] == "TIMEOUT"
    assert status["stage"] == "post_refusal_pl_status"
    assert status["available"] is True
    assert status["fields"]["rx_armed"] is False
    assert status["fields"]["rx_done"] is False
    assert status["fields"]["rx_position_words"] == 0
    assert status["fields"]["tx_source_selected"] is False
    assert status["fields"]["tx_length_words"] == 2540
    assert status["fields"]["sampled_at_monotonic_ns"] == 123
    assert len(status["reads"]) == 3
    assert all(read["available"] for read in status["reads"])


def test_iq_frame_does_not_trigger_status_request(tmp_path):
    class Client:
        def __init__(self):
            self.calls = 0

        def _send_and_read_json(self, *args, **kwargs):
            self.calls += 1
            return ({"id": 17, "type": "iq_frame", "samples": 4096}, b"")

    recorder = RawProtocolRecorder(tmp_path / "raw.jsonl")
    c = Client()
    recorder.install(c)
    c._send_and_read_json(None, b"", time.monotonic() + 1.0, 17,
                          "loopback.digital.capture", {})
    assert c.calls == 1
    assert recorder.events == []


def test_status_timeout_preserves_original_refusal(tmp_path):
    class Client:
        def __init__(self):
            self.calls = 0

        def _send_and_read_json(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ({"id": 27, "ok": False,
                         "error": {"code": "TIMEOUT",
                                   "message": "the hardware did not respond in time"}},
                        b"")
            raise TimeoutError("status read deadline")

    recorder = RawProtocolRecorder(tmp_path / "raw.jsonl")
    c = Client()
    recorder.install(c)
    got = c._send_and_read_json(None, b"", time.monotonic() + 1.0, 27,
                                "loopback.digital.capture", {})
    assert got[0]["error"]["code"] == "TIMEOUT"
    assert len(recorder.events) == 2
    assert recorder.events[0]["error_code"] == "TIMEOUT"
    assert recorder.events[1]["available"] is False
    assert recorder.events[1]["error"]["type"] == "TimeoutError"
    assert "TIMEOUT" not in str(recorder.events[1]["error"])


def test_normal_iq_does_not_add_requests(tmp_path):
    class Client:
        def __init__(self):
            self.calls = 0

        def _send_and_read_json(self, *args, **kwargs):
            self.calls += 1
            return ({"id": 17, "type": "iq_frame", "samples": 4096}, b"")

    recorder = RawProtocolRecorder(tmp_path / "raw.jsonl")
    c = Client()
    recorder.install(c)
    c._send_and_read_json(None, b"", time.monotonic() + 1.0, 17,
                          "loopback.digital.capture", {})
    assert c.calls == 1
    assert recorder.events == []


def test_configure_count_is_one_per_actual_call(tmp_path):
    class Client:
        def __init__(self):
            self.control_op = lambda op, args=None, payload=b"": {"ok": True}

    class Worker:
        def submit_control(self, action, token, args):
            return {"queued": True}

    class App:
        def set_rf_parameter(self, name, value):
            return None

        def rf_parameter_plan(self, action):
            return ({"idelay_tap": 25}, None)

    session = SimpleNamespace(
        client=Client(), app=App(), worker=Worker(),
        _wait_control=lambda action: {"ok": True})
    counts = dict(attempted=dict(rf_configure=0), completed=dict(rf_configure=0),
                  unknown=dict(rf_configure=0))
    records = []
    _configure(session, 25, records, counts, "tap25")
    assert counts["attempted"]["rf_configure"] == 0
    assert counts["completed"]["rf_configure"] == 0
    assert len(records) == 1
