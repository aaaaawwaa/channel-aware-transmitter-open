"""Fake-chain tests against the real QAM64 stability runner interface.

The runner is the Lead-provided ``scripts/rf_qam64_stability_runner.py``
(current SHA 39f5293...), executed with a fake session so no board/RF/product is
used.  The test checks the planned 2-session, 10-capture, 6-ON, 0-retry contract,
the real EvidenceClient-style precheck (capture/observe_capture_timing/
capture_extra), and the settle_wait_us timing/cleanup/identity stop boundaries.
"""
from __future__ import annotations

import time

import pytest

from rf_qam64_stability_runner import (CAPTURE_SECONDS, QAM64_VERSION,
                                       SESSIONS, run_stability)

EXPECTED_WAITS = [0, 5000, 0, 5000, 0, 5000]


class FakeClient:
    def __init__(self):
        self.capture_extra = {}
        self.frame = None
        self.last_wait = None

    def capture(self, args, physical=False):
        # Precheck only: the runner calls FakeSession.capture directly.
        return None

    def observe_capture_timing(self, frame):
        wait = self.last_wait
        trace = dict(available=True, settle_wait_requested_us=wait,
                     settle_begin_ns=100 if wait else None,
                     settle_end_ns=(100 + wait * 1000) if wait else None)
        return dict(reason=None, last_capture_timing=trace)


class FakeSession:
    instances = []
    fail = None

    def __init__(self, expected, output, **kwargs):
        self.expected = expected
        self.output = output
        self.kwargs = kwargs
        self.client = FakeClient()
        self.calls = []
        type(self).instances.append(self)

    def initial(self):
        self.calls.append("initial")
        return {"safe": True, "release_id": self.expected}

    def setup(self, seed):
        self.calls.append(("setup", seed))

    def capture(self, tag, physical):
        self.calls.append(("capture", tag, physical))
        if type(self).fail == "timeout" and tag == "s1-on-5000-a":
            raise TimeoutError("capture deadline")
        wait = None
        if physical:
            extra = self.client.capture_extra
            assert extra.get("observe_timing") is True
            wait = extra.get("settle_wait_us")
            assert wait in (0, 5000)
            self.client.last_wait = wait
            result = dict(
                algorithm_version=QAM64_VERSION, capture_id="cap-" + tag,
                release_id=self.expected, evm_available=False, evm_rms=None,
                carrier_sync=True, timing_sync=True, reference_sync=True,
                valid_symbols=127, alignment_bits=378,
                comparison_start_symbol=63, bit_errors=0, compared_bits=384,
                ber=0.0)
            observation = {"usable": True, "stop": False,
                           "capture_id": "cap-" + tag,
                           "request_id": 1, "daemon_timestamp_ns": 1,
                           "release_id": self.expected,
                           "observation": {
                               "observation_present": True,
                               "identity_ok": True,
                               "in_window_rx_gain_index": {
                                   "available": True, "value": 76},
                               "chip_tx_attenuation_mdb": {
                                   "available": True,
                                   "value": [46000, 46000]}}}
        else:
            assert self.client.capture_extra == {}
            result = dict(
                algorithm_version=QAM64_VERSION, capture_id="cap-" + tag,
                release_id=self.expected, evm_available=False, evm_rms=None,
                carrier_sync=False, timing_sync=False, reference_sync=False,
                valid_symbols=0, alignment_bits=None,
                comparison_start_symbol=None, bit_errors=None,
                compared_bits=None, ber=None)
            observation = None
        if type(self).fail == "identity" and tag == "s1-on-5000-a":
            result["capture_id"] = "old-capture"
        record = dict(
            capture_id="cap-" + tag, release_id=self.expected,
            iq_sha256="a" * 64, iq_file=tag + ".iq",
            metadata_file=tag + ".json",
            capture_observation_file=tag + ".capture-observation.json",
            capture_observation=observation, egress_safe=True,
            result=result)
        self.client.frame = record
        return record

    def cleanup(self):
        self.calls.append("cleanup")
        if type(self).fail == "cleanup":
            raise RuntimeError("cleanup refused")
        return {"safe": True, "disarm_ack": True}

    def close(self):
        self.calls.append("close")


@pytest.fixture(autouse=True)
def _reset():
    FakeSession.instances = []
    FakeSession.fail = None
    yield
    FakeSession.fail = None


def _run(tmp_path):
    return run_stability("release", tmp_path / "out",
                         session_factory=FakeSession, cable_confirmed=True)


def test_plan_budget_order_and_real_runner_output(tmp_path):
    summary = _run(tmp_path)
    assert summary["execution_complete"] is True
    assert summary["stop_cause"] is None
    assert summary["counts"] == dict(sessions=2, initializations=2, loads=2,
                                     on_tx=6, captures=10)
    assert summary["attempts"] == summary["counts"]
    assert [r["tag"] for r in summary["rows"]] == [
        f["tag"] for s in SESSIONS for f in s["frames"]]
    assert summary["elapsed_s"] <= 180.0
    assert summary["qam64_on_frames"] == 6
    assert (tmp_path / "out" / "summary.json").is_file()


def test_settle_wait_is_passed_not_host_sleep(tmp_path, monkeypatch):
    def forbidden_sleep(*args, **kwargs):
        raise AssertionError("runner must not sleep on the host")
    monkeypatch.setattr(time, "sleep", forbidden_sleep)
    summary = _run(tmp_path)
    ons = [r for r in summary["rows"] if r["physical"]]
    assert [r["requested_wait_us"] for r in ons] == EXPECTED_WAITS
    assert [r["timing"]["actual_wait_us"] for r in ons] == EXPECTED_WAITS
    assert [r["timing"]["timing_available"] for r in ons] == [True] * 6
    assert all(r["timing"]["actual_wait_us"] != 5_000_000 for r in ons)
    for row in ons:
        if row["requested_wait_us"] == 5000:
            assert 4_900_000 <= row["timing"]["settle_duration_ns"] <= 5_300_000
            assert row["timing"]["settle_duration_ns"] != 5_000_000_000
    assert summary["execution_complete"] is True


def test_one_initialization_setup_load_and_capture_deadline(tmp_path):
    _run(tmp_path)
    assert len(FakeSession.instances) == 2
    for session in FakeSession.instances:
        assert session.calls.count("initial") == 1
        assert sum(1 for c in session.calls if isinstance(c, tuple)
                   and c[0] == "setup") == 1
        assert session.calls.count("cleanup") == 1
        assert session.calls.count("close") == 1
        assert session.kwargs["capture_deadline"] == CAPTURE_SECONDS == 10.0


def test_on_observation_and_off_not_fabricated(tmp_path):
    summary = _run(tmp_path)
    for row in summary["rows"]:
        if row["physical"]:
            obs = row["capture_observation"]
            assert obs["usable"] is True and obs["stop"] is False
            assert obs["observation"]["observation_present"] is True
            assert obs["observation"]["identity_ok"] is True
            assert obs["observation"]["in_window_rx_gain_index"]["value"] == 76
            assert obs["observation"]["chip_tx_attenuation_mdb"]["value"] == [46000, 46000]
        else:
            assert row["capture_observation"] is None
            assert row["timing"]["timing_available"] is False
            assert row["timing"]["actual_wait_us"] is None


def test_timeout_stops_without_retry_or_later_frames(tmp_path):
    FakeSession.fail = "timeout"
    summary = _run(tmp_path)
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("TimeoutError")
    assert [r["tag"] for r in summary["rows"]] == [
        "s1-off-first", "s1-on-0-a"]
    assert summary["counts"]["captures"] == 2
    assert summary["attempts"]["captures"] == 3
    assert summary["attempts"]["sessions"] == 1
    assert summary["counts"]["sessions"] == 0


def test_cleanup_failure_stops(tmp_path):
    FakeSession.fail = "cleanup"
    summary = _run(tmp_path)
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("cleanup:")
    assert len(summary["rows"]) == 5
    assert summary["attempts"]["sessions"] == 1
    assert summary["counts"]["sessions"] == 0


def test_identity_failure_stops(tmp_path):
    FakeSession.fail = "identity"
    summary = _run(tmp_path)
    assert summary["execution_complete"] is False
    assert "stale_capture_id" in str(summary["stop_cause"])
    assert [r["tag"] for r in summary["rows"]] == [
        "s1-off-first", "s1-on-0-a", "s1-on-5000-a"]
    assert summary["counts"]["captures"] == 3
    assert summary["attempts"]["captures"] == 3


def test_runner_is_the_real_interface_not_a_local_skeleton():
    import rf_qam64_stability_runner as R
    from pathlib import Path
    assert Path(R.__file__).name == "rf_qam64_stability_runner.py"
    assert R.QAM64_VERSION == "qam64-rect-snapshot-v2"
    assert R.CAPTURE_SECONDS == 10.0
