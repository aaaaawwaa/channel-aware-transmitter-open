"""Offline fake-worker tests for the existing BPSK UI board runner.

Covers the additive `modrec4_result` persistence and the optional one-frame
campaign.  These tests never create a client, open an Overlay, or emit RF.
"""
import dataclasses
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rf_bpsk_ui_acceptance as acceptance  # noqa: E402
import rf_bpsk_ui_board_session as session_mod  # noqa: E402


class _Readback:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return {"value": self.value}


class _Frame:
    payload = b"\x00\x00" * 8

    def __init__(self):
        self.capture_id = "fake-capture"
        self.pre_readback = _Readback("pre")
        self.post_readback = _Readback("post")

    def to_dict(self):
        return {"capture_id": self.capture_id, "samples": 8}


class _Client:
    def __init__(self):
        self.frame = _Frame()


class _App:
    def rx_demod_summary(self):
        return {"display": "BPSK"}


def _session():
    obj = object.__new__(session_mod.LiveUiSession)
    obj.output = pathlib.Path(".")
    obj.client = _Client()
    obj.app = _App()
    obj._teaching = False
    obj.ui_events = []
    obj._capture_diagnostic = None
    obj._last_payload = {
        "frame": obj.client.frame.to_dict(),
        "analysis": {"base": "kept"},
        "model_result": {"label": "BPSK", "scores": [1.0]},
        "demod_result": {"algorithm_version": "v1", "label": "BPSK"},
        "environment_result": {"label": "NORMAL", "confidence": 0.5},
        "modrec4_result": {
            "status": "OK",
            "frame_identity": {"capture_id": "fake-capture", "request_id": 7},
            "input_domain": {"status": "VALID", "format": "s16_iq_le"},
            "modulation": {"final_label": "QPSK", "accepted": True},
            "passthrough": {"six_class": {"label": "BPSK"}},
        },
    }
    return obj


def test_save_keeps_complete_modrec4_result_and_old_fields(tmp_path):
    session = _session()
    session.output = tmp_path
    session._session_index = 2
    session.run_id = "run-1"
    session._active_load_evidence = {"session_index": 2, "payload_sha256": "a"*64,
                                     "payload_length": 8, "load_ok": True}
    session._capture_command_seconds = 0.0123
    session._capture_tx_state = {"pre_tx_source_selected": False,
                                 "post_tx_source_selected": False,
                                 "off_tx_ok": None}
    meta = session._save("one", {"state": "done"}, {"state": "done"})
    written = json.loads((tmp_path / "one.json").read_text(encoding="utf-8"))
    assert written == meta
    assert written["capture_command_seconds"] == 0.0123
    assert written["capture_command_timing_note"] == "command closure time, not TX-on time"
    assert written["tx_on_measurement"] == "NOT_MEASURED"
    assert written["capture_tx_state"] == session._capture_tx_state
    assert written["session_index"] == 2
    assert written["tx_load_evidence"] == session._active_load_evidence
    assert written["analysis"] == {"base": "kept"}
    assert written["model_result"] == {"label": "BPSK", "scores": [1.0]}
    assert written["demod_result"] == {"algorithm_version": "v1", "label": "BPSK"}
    assert written["environment_result"] == {"label": "NORMAL", "confidence": 0.5}
    assert written["modrec4_result"] == session._last_payload["modrec4_result"]
    assert written["handoff_frame"] == session._last_payload["frame"]
    assert (tmp_path / "one.iq").read_bytes() == _Frame.payload


class _FakeSession:
    def __init__(self, calls):
        self.calls = calls

    def initial(self):
        self.calls.append("initial")
        return {"safe": True, "release_id": "release"}

    def setup(self, seed):
        self.calls.append(("setup", seed))

    def capture(self, tag, physical):
        self.calls.append(("capture", tag, physical))
        if not physical:
            return {"egress_safe": True, "result": {"ber": None}}
        return {"egress_safe": True, "result": {
            "reference_sync": True, "bit_errors": 0, "compared_bits": 100,
            "ber": 0.0}}

    def cleanup(self):
        self.calls.append("cleanup")
        return {"safe": True, "disarm_ack": True}

    def close(self):
        self.calls.append("close")


def test_single_frame_runs_one_session_init_tx_and_capture(tmp_path):
    calls = []
    result = acceptance.campaign(lambda: _FakeSession(calls), tmp_path, "release",
                                 single=True)
    assert result["single_frame"] is True
    assert result["passed"] is True
    assert result["stop_cause"] is None
    assert result["sessions"] == [dict(index=0, seed=acceptance.SEEDS[0], closed=True,
                                       disarm_ack=True, cause=None, error=None)]
    assert len(result["records"]) == 1
    assert calls == ["initial", ("setup", acceptance.SEEDS[0]),
                     ("capture", "s00-on-00", True), "cleanup", "close"]


def test_default_campaign_still_runs_33_frames(tmp_path):
    calls = []
    result = acceptance.campaign(lambda: _FakeSession(calls), tmp_path, "release")
    assert result["single_frame"] is False
    assert result["passed"] is True
    assert len(result["records"]) == acceptance.EXPECTED_RECORDS == 33
    assert len(result["sessions"]) == len(acceptance.SEEDS) == 3
    assert calls.count("initial") == 3
    assert len([call for call in calls if isinstance(call, tuple)
                and call[0] == "capture"]) == 33

def test_tx_load_recorder_uses_actual_payload_and_propagates_failure():
    session = object.__new__(session_mod.LiveUiSession)
    session._session_index = 2
    session.run_id = "run-2"
    session._tx_mode = "QAM16"
    session._tx_seed = 73
    session._tx_amplitude = 60
    session._tx_load_records = []
    payload = bytes(range(16))
    calls = []

    def control_op(op, args=None, payload=b""):
        calls.append((op, dict(args or {}), bytes(payload)))
        return {"ok": True}

    session._tx_control_op = control_op
    response = session._record_control_op("tx.load", {"samples": 4, "bytes": 16}, payload)
    assert response == {"ok": True}
    assert calls == [("tx.load", {"samples": 4, "bytes": 16}, payload)]
    record = session._tx_load_records[-1]
    assert record["payload_sha256"] == __import__("hashlib").sha256(payload).hexdigest()
    assert record["payload_length"] == 16
    assert record["session_index"] == 2 and record["tx_mode"] == "QAM16"
    assert record["tx_seed"] == 73 and record["tx_amplitude"] == 60
    assert record["load_ok"] is True

    def failing(op, args=None, payload=b""):
        raise OSError("daemon rejected load")

    session._tx_load_records = []
    session._tx_control_op = failing
    try:
        session._record_control_op("tx.load", {"samples": 4}, payload)
    except OSError:
        pass
    else:
        raise AssertionError("load failure must be propagated")
    failed = session._tx_load_records[-1]
    assert failed["load_ok"] is False
    assert failed["error"]["type"] == "OSError"
