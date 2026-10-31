"""Independent offline counterexample tests for the QAM64 path-compare runner.

The real runner and the real ``EvidenceClient.digital_capture`` adapter are used;
fake sessions/underlying clients keep this entirely off-board.  No product,
runner, candidate or installed file is modified.
"""
from __future__ import annotations

import json
import threading

import pytest

from rf_bpsk_ui_board_session import EvidenceClient
from rf_qam64_path_compare_runner import FRAMES, run_path_compare
from rf_rx_types import RxIqFrame, RxReadback


def _rb(rx_armed=False, tx=False):
    return RxReadback(io_ok=True, rx_armed=rx_armed,
                      tx_source_selected=tx)


def _frame(**overrides):
    data = dict(
        capture_id="digital-cap", payload=b"\0" * (4096 * 4), samples=4096,
        sample_rate_hz=60_000_000, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=3, request_id=1,
        release_id="release", op="loopback.digital.capture", physical=False,
        pre_readback=_rb(), post_readback=_rb(False, False),
        completed_at_monotonic=1.0)
    data.update(overrides)
    return RxIqFrame(**data)


class Underlying:
    _tx_session_ready = True

    def __init__(self, frame):
        self._io_lock = threading.Lock()
        self.frame = frame
        self.calls = []

    def _ordinary_owner(self):
        self.calls.append("owner")

    def _capture_unlocked(self, op, wire, physical):
        self.calls.append(("capture_unlocked", op, physical))
        if self.frame == "timeout":
            raise TimeoutError("digital capture timeout")
        return self.frame


def test_digital_adapter_accepts_real_binary_frame_and_keeps_identity():
    frame = _frame()
    adapter = EvidenceClient(Underlying(frame))
    got = adapter.digital_capture(dict(samples=4096, ratio=0))
    assert got is frame
    assert got.op == "loopback.digital.capture"
    assert got.physical is False
    assert got.post_readback.rx_armed is False
    assert got.post_readback.tx_source_selected is False
    assert got.capture_id == "digital-cap"
    assert got.payload == b"\0" * (4096 * 4)


@pytest.mark.parametrize("frame,message", [
    (_frame(op="rx.capture"), "wrong op"),
    (_frame(physical=True), "mislabeled physical"),
    (_frame(post_readback=_rb(True, False)), "did not release RX"),
    (_frame(post_readback=_rb(False, True)), "did not close TX source"),
])
def test_digital_adapter_rejects_wrong_op_physical_release_or_tx(frame, message):
    adapter = EvidenceClient(Underlying(frame))
    with pytest.raises(RuntimeError, match=message):
        adapter.digital_capture({})


def test_digital_adapter_requires_loaded_tx_session():
    underlying = Underlying(_frame())
    underlying._tx_session_ready = False
    adapter = EvidenceClient(underlying)
    with pytest.raises(RuntimeError, match="loaded TX session"):
        adapter.digital_capture({})


def test_digital_adapter_timeout_propagates():
    adapter = EvidenceClient(Underlying("timeout"))
    with pytest.raises(TimeoutError):
        adapter.digital_capture({})


def test_binary_iq_header_rejects_bad_payload_length_before_runner():
    with pytest.raises(ValueError):
        _frame(payload=b"\0" * (4096 * 4 - 2))


# ---------------------------------------------------------------- path runner


class FakeClient:
    def __init__(self, session):
        self.session = session
        self.capture_extra = {}
        self.frame = None
        self.last_wait = None
        self.control_calls = []
        self.client = type("Inner", (), {"capture_timeout": 0})()

    def capture(self, args, physical=False):
        return None  # precheck only

    def control_op(self, op, args=None, payload=b""):
        self.control_calls.append(op)
        return {"ok": True}

    def observe_capture_timing(self, frame):
        wait = self.last_wait
        return dict(reason=None, last_capture_timing=dict(
            available=True, settle_wait_requested_us=wait,
            settle_begin_ns=100 if wait else None,
            settle_end_ns=(100 + wait * 1000) if wait else None))

    def digital_capture(self, args):
        self.session.calls.append("digital_capture")
        if self.session.fail == "digital_timeout":
            raise TimeoutError("digital capture timeout")
        return _frame(capture_id="digital-" + str(len(self.session.calls)))


class FakeSession:
    instances = []
    fail = None

    def __init__(self, expected, output, **kwargs):
        self.expected = expected
        self.output = output
        self.kwargs = kwargs
        self.client = FakeClient(self)
        self.calls = []
        type(self).instances.append(self)

    def initial(self):
        self.calls.append("initial")
        return {"safe": True, "release_id": self.expected}

    def setup(self, seed):
        self.calls.append(("setup", seed))
        self.client.control_op("rf.init", {})
        self.client.control_op("tx.load", {})

    def capture(self, tag, physical):
        self.calls.append(("capture", tag, physical))
        capture_id = "cap-" + tag
        if physical:
            assert self.client.capture_extra.get("observe_timing") is True
            wait = self.client.capture_extra.get("settle_wait_us")
            assert wait == 0
            self.client.last_wait = wait
            result = dict(
                algorithm_version="qam64-rect-snapshot-v2",
                capture_id=capture_id, release_id=self.expected,
                evm_available=False, evm_rms=None, carrier_sync=True,
                timing_sync=True, reference_sync=True, valid_symbols=127,
                alignment_bits=378, comparison_start_symbol=63,
                bit_errors=0, compared_bits=384, ber=0.0)
            observation = dict(
                usable=True, stop=False, capture_id=capture_id,
                request_id=1, daemon_timestamp_ns=1, release_id=self.expected,
                observation=dict(
                    observation_present=True, identity_ok=True,
                    in_window_rx_gain_index=dict(available=True, value=76),
                    chip_tx_attenuation_mdb=dict(available=True,
                                                 value=[46000, 46000])))
        else:
            assert self.client.capture_extra == {}
            result = dict(
                algorithm_version="qam64-rect-snapshot-v2",
                capture_id=capture_id, release_id=self.expected,
                evm_available=False, evm_rms=None, carrier_sync=False,
                timing_sync=False, reference_sync=False, valid_symbols=0,
                alignment_bits=None, comparison_start_symbol=None,
                bit_errors=None, compared_bits=None, ber=None)
            observation = None
            self.client.last_wait = None
        record = dict(
            capture_id=capture_id, release_id=self.expected,
            iq_sha256="a" * 64, iq_file=tag + ".iq",
            metadata_file=tag + ".json",
            capture_observation_file=tag + ".capture-observation.json",
            capture_observation=observation, egress_safe=True, result=result)
        self.client.frame = record
        return record

    def cleanup(self):
        self.calls.append("cleanup")
        if self.fail == "cleanup":
            raise RuntimeError("cleanup refused")
        return {"safe": True, "disarm_ack": True}

    def close(self):
        self.calls.append("close")


class NoDigitalClient(FakeClient):
    digital_capture = None


class NoDigitalSession(FakeSession):
    def __init__(self, expected, output, **kwargs):
        super().__init__(expected, output, **kwargs)
        self.client = NoDigitalClient(self)


@pytest.fixture(autouse=True)
def _reset():
    FakeSession.instances = []
    FakeSession.fail = None
    yield
    FakeSession.fail = None


def _run(tmp_path, factory=FakeSession):
    return run_path_compare("release", tmp_path / "out",
                            session_factory=factory, cable_confirmed=True)


def test_fixed_plan_budget_order_and_attempted_completed(tmp_path):
    summary = _run(tmp_path)
    assert summary["execution_complete"] is True
    assert summary["stop_cause"] is None
    assert [r["tag"] for r in summary["rows"]] == [f["tag"] for f in FRAMES]
    assert [r["path"] for r in summary["rows"]] == [
        "off", "physical", "digital", "digital", "physical", "off"]
    assert summary["digital_frames"] == ["path-digital-a", "path-digital-b"]
    c = summary["counts"]
    assert c["attempted"]["sessions"] == 1
    assert c["attempted"]["rf_init"] == 1
    assert c["attempted"]["tx_load"] == 1
    assert c["attempted"]["captures"] == 6
    assert c["attempted"]["tx_source_activations"] == 4
    assert c["completed"] == c["attempted"]
    assert c["unknown"] == dict(sessions=0, rf_init=0, tx_load=0,
                                captures=0, tx_source_activations=0)
    assert c["precheck_reads"] == 1
    assert [r["tag"] for r in summary["rows"]] == [
        "path-off-first", "path-physical-a", "path-digital-a",
        "path-digital-b", "path-physical-b", "path-off-last"]


def test_read_only_initial_is_not_counted_as_init(tmp_path):
    summary = _run(tmp_path)
    assert summary["counts"]["attempted"]["rf_init"] == 1
    assert summary["counts"]["completed"]["rf_init"] == 1
    assert len(FakeSession.instances) == 1
    assert FakeSession.instances[0].calls[0] == "initial"


def test_digital_frames_are_real_false_path_and_unique(tmp_path):
    summary = _run(tmp_path)
    digital = [r for r in summary["rows"] if r["path"] == "digital"]
    assert len(digital) == 2
    for row in digital:
        assert row["physical"] is False
        assert row["op"] == "loopback.digital.capture"
        assert row["post_rx_armed"] is False
        assert row["post_tx_source_selected"] is False
        assert (tmp_path / "out" / "session-1" / row["iq_file"]).is_file()
        assert (tmp_path / "out" / "session-1" / row["metadata_file"]).is_file()
        meta = json.loads((tmp_path / "out" / "session-1" /
                           row["metadata_file"]).read_text(encoding="utf-8"))
        assert meta["path"] == "digital"
        assert meta["physical"] is False
        assert meta["op"] == "loopback.digital.capture"
    names = [r["metadata_file"] for r in summary["rows"]]
    assert len(names) == len(set(names)) == 6


def test_digital_timeout_unknown_counts_and_no_later_frames(tmp_path):
    FakeSession.fail = "digital_timeout"
    summary = _run(tmp_path)
    assert summary["execution_complete"] is False
    assert "TimeoutError" in str(summary["stop_cause"])
    assert [r["tag"] for r in summary["rows"]] == [
        "path-off-first", "path-physical-a"]
    c = summary["counts"]
    assert c["attempted"]["captures"] == 3
    assert c["completed"]["captures"] == 2
    assert c["unknown"]["captures"] == 1
    assert c["attempted"]["tx_source_activations"] == 2
    assert c["completed"]["tx_source_activations"] == 1
    assert c["unknown"]["tx_source_activations"] == 1
    assert len(FakeSession.instances) == 1


def test_cleanup_failure_stops_and_no_restart(tmp_path):
    FakeSession.fail = "cleanup"
    summary = _run(tmp_path)
    assert summary["execution_complete"] is False
    assert "cleanup:" in str(summary["stop_cause"])
    assert summary["counts"]["attempted"]["sessions"] == 1
    assert summary["counts"]["completed"]["sessions"] == 0
    assert len(FakeSession.instances) == 1


def test_precheck_missing_digital_capture_rejects_before_initial(tmp_path):
    summary = _run(tmp_path, NoDigitalSession)
    assert summary["execution_complete"] is False
    assert "missing_client_method:digital_capture" in str(summary["stop_cause"])
    assert len(FakeSession.instances) == 1
    assert "initial" not in FakeSession.instances[0].calls
    assert summary["counts"]["attempted"]["sessions"] == 1
    assert summary["counts"]["completed"]["sessions"] == 0


def test_output_directory_refuses_overwrite(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    keep = out / "keep"
    keep.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_path_compare("release", out, session_factory=FakeSession,
                         cable_confirmed=True)
    assert keep.read_text(encoding="utf-8") == "keep"
    assert not (out / "plan.json").exists()


def test_token_is_not_written_anywhere(tmp_path):
    summary = _run(tmp_path)
    text = json.dumps(summary)
    assert "token" not in text.lower()
    for path in (tmp_path / "out").rglob("*"):
        if path.is_file():
            assert b"token" not in path.read_bytes().lower()
