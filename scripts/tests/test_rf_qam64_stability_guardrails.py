"""Independent counterexample/guardrail tests for the QAM64 stability runner.

Exercises the real ``scripts/rf_qam64_stability_runner.py`` with fake sessions;
the real ``EvidenceClient`` wrapper is used for the pre-init interface check.
No board, RF, product, runner change or commit.
"""
from __future__ import annotations

import json
import math

import pytest

from rf_bpsk_ui_board_session import EvidenceClient
from rf_qam64_stability_runner import (QAM64_VERSION, SESSIONS,
                                       run_stability)

RELEASE = "release"
ON_TAG = "s1-on-0-a"


class InnerClient:
    def __init__(self, session):
        self.session = session
        self.last_wait = None

    def capture(self, args, physical=False):
        wait = (args or {}).get("settle_wait_us")
        self.last_wait = wait
        return self.session._capture(wait, physical,
                                     (args or {}).get("tag"))

    def observe_capture_timing(self, frame):
        wait = self.last_wait
        return dict(reason=None, last_capture_timing=dict(
            available=True, settle_wait_requested_us=wait,
            settle_begin_ns=100 if wait else None,
            settle_end_ns=(100 + wait * 1000) if wait else None))


class NoObserveInner(InnerClient):
    observe_capture_timing = None


def _result(physical, capture_id, mode=None):
    if physical:
        result = dict(
            algorithm_version=QAM64_VERSION, capture_id=capture_id,
            release_id=RELEASE, evm_available=False, evm_rms=None,
            carrier_sync=True, timing_sync=True, reference_sync=True,
            valid_symbols=127, alignment_bits=378,
            comparison_start_symbol=63, bit_errors=0, compared_bits=384,
            ber=0.0)
        if mode == "legal_nonzero":
            result.update(bit_errors=1, ber=1 / 384)
        elif mode == "legal_desync":
            result.update(reason="REFERENCE_NOT_FOUND", carrier_sync=False,
                          timing_sync=False, reference_sync=False,
                          bit_errors=None, compared_bits=None, ber=None,
                          valid_symbols=0, alignment_bits=None,
                          comparison_start_symbol=None)
        elif mode == "desync_bad_flag":
            result.update(reason="REFERENCE_NOT_FOUND", carrier_sync=True,
                          timing_sync=False, reference_sync=False,
                          bit_errors=None, compared_bits=None, ber=None,
                          valid_symbols=0)
        elif mode == "ber_negative":
            result.update(bit_errors=-1, ber=-1 / 384)
        elif mode == "ber_big":
            result.update(bit_errors=385, ber=385 / 384)
        elif mode == "ber_nan":
            result.update(bit_errors=1, ber=float("nan"))
        elif mode == "ber_inconsistent":
            result.update(bit_errors=1, ber=0.5)
    else:
        result = dict(
            algorithm_version=QAM64_VERSION, capture_id=capture_id,
            release_id=RELEASE, evm_available=False, evm_rms=None,
            carrier_sync=False, timing_sync=False, reference_sync=False,
            valid_symbols=0, alignment_bits=None,
            comparison_start_symbol=None, bit_errors=None,
            compared_bits=None, ber=None)
    return result


def _observation(physical, capture_id, mode=None):
    if not physical:
        return None
    obs = dict(usable=True, stop=False, capture_id=capture_id,
               request_id=1, daemon_timestamp_ns=1, release_id=RELEASE,
               observation=dict(
                   observation_present=True, identity_ok=True,
                   in_window_rx_gain_index=dict(available=True, value=76),
                   chip_tx_attenuation_mdb=dict(available=True,
                                                value=[46000, 46000])))
    if mode == "obs_unusable":
        obs["usable"] = False
    elif mode == "obs_stop":
        obs["stop"] = True
    elif mode == "obs_capture":
        obs["capture_id"] = "old-capture"
    elif mode == "obs_inner":
        obs["observation"] = None
    elif mode == "obs_present":
        obs["observation"]["observation_present"] = False
    elif mode == "obs_identity":
        obs["observation"]["identity_ok"] = False
    elif mode == "obs_gain_missing":
        obs["observation"]["in_window_rx_gain_index"] = dict(
            available=False, value=None)
    elif mode == "obs_gain_type":
        obs["observation"]["in_window_rx_gain_index"] = dict(
            available=True, value="76")
    elif mode == "obs_att_missing":
        obs["observation"]["chip_tx_attenuation_mdb"] = dict(
            available=False, value=None)
    elif mode == "obs_att_value":
        obs["observation"]["chip_tx_attenuation_mdb"] = dict(
            available=True, value=[46000])
    return obs


class FakeSession:
    instances = []
    mode = None
    cleanup_mode = None
    fail = None

    def __init__(self, expected, output, *, inner=None, **kwargs):
        self.expected = expected
        self.output = output
        self.kwargs = kwargs
        self.client = EvidenceClient(inner(self) if inner else InnerClient(self))
        self.client.capture_extra = {}
        self.calls = []
        self.capture_n = 0
        type(self).instances.append(self)

    def initial(self):
        self.calls.append("initial")
        return {"safe": True, "release_id": self.expected}

    def setup(self, seed):
        self.calls.append(("setup", seed))

    def _capture(self, wait, physical, tag=None):
        self.capture_n += 1
        self.calls.append(("capture", wait, physical, tag))
        capture_id = "cap-" + str(tag)
        mode = type(self).mode
        if type(self).fail == "timeout" and self.capture_n == 3:
            raise TimeoutError("capture deadline")
        use_mode = None
        if physical:
            if mode in ("legal_nonzero", "legal_desync"):
                use_mode = mode
            elif self.capture_n == 2:
                use_mode = mode
        result = _result(physical, capture_id, use_mode)
        observation = _observation(physical, capture_id, use_mode)
        return dict(
            capture_id=capture_id, release_id=self.expected,
            iq_sha256="a" * 64, iq_file="f.iq", metadata_file="f.json",
            capture_observation_file="f.capture-observation.json",
            capture_observation=observation,
            egress_safe=True, result=result)

    def capture(self, tag, physical):
        # Go through the real EvidenceClient wrapper so capture_extra is injected
        # exactly as the production session does.
        return self.client.capture({"tag": tag}, physical=physical)

    def cleanup(self):
        self.calls.append("cleanup")
        if type(self).fail == "cleanup_raise":
            raise RuntimeError("cleanup refused")
        if type(self).cleanup_mode == "unsafe":
            return {"safe": False, "disarm_ack": False}
        if type(self).cleanup_mode == "no_disarm":
            return {"safe": True, "disarm_ack": False}
        return {"safe": True, "disarm_ack": True}

    def close(self):
        self.calls.append("close")


@pytest.fixture(autouse=True)
def _reset():
    FakeSession.instances = []
    FakeSession.mode = None
    FakeSession.cleanup_mode = None
    FakeSession.fail = None
    yield
    FakeSession.mode = None
    FakeSession.cleanup_mode = None
    FakeSession.fail = None


def _run(tmp_path, factory=FakeSession):
    return run_stability(RELEASE, tmp_path / "out", session_factory=factory,
                         cable_confirmed=True)


def _assert_stopped(summary, prefix):
    assert summary["execution_complete"] is False
    assert prefix in str(summary["stop_cause"]), summary["stop_cause"]


def test_preinit_real_evidenceclient_wrapper_missing_method_stops():
    class Missing(FakeSession):
        def __init__(self, expected, output, **kwargs):
            super().__init__(expected, output,
                             inner=NoObserveInner, **kwargs)

    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        summary = run_stability(RELEASE, pathlib.Path(td) / "out",
                                session_factory=Missing,
                                cable_confirmed=True)
    _assert_stopped(summary, "precheck:missing_client_method:observe_capture_timing")
    assert len(FakeSession.instances) == 1
    assert "initial" not in FakeSession.instances[0].calls
    assert summary["attempts"]["sessions"] == 1
    assert summary["counts"]["initializations"] == 0


def test_cleanup_unsafe_stops_and_second_session_never_starts(tmp_path):
    FakeSession.cleanup_mode = "unsafe"
    summary = _run(tmp_path)
    _assert_stopped(summary, "cleanup_unsafe")
    assert len(FakeSession.instances) == 1
    assert summary["attempts"]["sessions"] == 1
    assert summary["counts"]["sessions"] == 0
    assert len(summary["rows"]) == 5


def test_cleanup_missing_disarm_stops(tmp_path):
    FakeSession.cleanup_mode = "no_disarm"
    summary = _run(tmp_path)
    _assert_stopped(summary, "cleanup_unsafe")
    assert len(FakeSession.instances) == 1


@pytest.mark.parametrize("mode,prefix", [
    ("obs_unusable", "on_observation_not_usable"),
    ("obs_stop", "on_observation_not_usable"),
    ("obs_capture", "on_observation_capture_id_mismatch"),
    ("obs_inner", "on_observation_inner_missing"),
    ("obs_present", "on_observation_not_present"),
    ("obs_identity", "on_observation_identity_bad"),
    ("obs_gain_missing", "on_in_window_gain_missing"),
    ("obs_gain_type", "on_in_window_gain_value_invalid"),
    ("obs_att_missing", "on_chip_attenuation_missing"),
    ("obs_att_value", "on_chip_attenuation_value_invalid"),
])
def test_on_observation_content_identity_gain_attenuation_stops(tmp_path, mode, prefix):
    FakeSession.mode = mode
    summary = _run(tmp_path)
    _assert_stopped(summary, prefix)
    assert summary["rows"][-1]["problem"] == prefix
    assert len(summary["rows"]) == 2  # first OFF + first ON


def test_off_named_missing_observation_is_allowed(tmp_path):
    summary = _run(tmp_path)
    off = [r for r in summary["rows"] if not r["physical"]]
    assert len(off) == 4
    assert all(r["capture_observation"] is None for r in off)
    assert summary["execution_complete"] is True


@pytest.mark.parametrize("mode,prefix", [
    ("ber_negative", "metric_count_out_of_range"),
    ("ber_big", "metric_count_out_of_range"),
    ("ber_nan", "metric_ber_not_finite"),
    ("ber_inconsistent", "metric_ber_inconsistent"),
])
def test_bad_ber_stops(tmp_path, mode, prefix):
    FakeSession.mode = mode
    summary = _run(tmp_path)
    _assert_stopped(summary, prefix)
    assert len(summary["rows"]) == 2


def test_legal_nonzero_ber_is_retained_and_later_frames_continue(tmp_path):
    FakeSession.mode = "legal_nonzero"
    summary = _run(tmp_path)
    assert summary["execution_complete"] is True
    assert len(summary["rows"]) == 10
    assert len(summary["metric_failures"]) == 6
    assert all(r["result"]["bit_errors"] == 1 for r in summary["rows"]
               if r["physical"])


def test_legal_three_sync_false_empty_ber_continues(tmp_path):
    FakeSession.mode = "legal_desync"
    summary = _run(tmp_path)
    assert summary["execution_complete"] is True
    assert len(summary["rows"]) == 10
    assert summary["qam64_clean_sync"] == 0
    assert all(r["result"]["bit_errors"] is None for r in summary["rows"]
               if r["physical"])


def test_damaged_desync_flags_stop(tmp_path):
    FakeSession.mode = "desync_bad_flag"
    summary = _run(tmp_path)
    _assert_stopped(summary, "on_desync_flags_not_all_false")
    assert len(summary["rows"]) == 2


def test_exception_attempts_vs_counts_and_no_second_session(tmp_path):
    FakeSession.fail = "timeout"
    summary = _run(tmp_path)
    _assert_stopped(summary, "TimeoutError")
    assert summary["attempts"]["sessions"] == 1
    assert summary["counts"]["sessions"] == 0
    assert summary["attempts"]["captures"] == 3
    assert summary["counts"]["captures"] == 2
    assert summary["attempts"]["on_tx"] == 2
    assert summary["counts"]["on_tx"] == 1
    assert len(summary["rows"]) == 2


def test_cleanup_exception_counts_are_not_leaked(tmp_path):
    FakeSession.fail = "cleanup_raise"
    summary = _run(tmp_path)
    _assert_stopped(summary, "cleanup:")
    assert summary["attempts"]["sessions"] == 1
    assert summary["counts"]["sessions"] == 0
    assert summary["counts"]["captures"] == 5
    assert len(summary["rows"]) == 5


def test_output_directory_refuses_overwrite(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    marker = out / "existing"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_stability(RELEASE, out, session_factory=FakeSession,
                      cable_confirmed=True)
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (out / "plan.json").exists()


def test_per_frame_unique_result_files(tmp_path):
    summary = _run(tmp_path)
    assert summary["execution_complete"] is True
    files = sorted((tmp_path / "out").glob("session-*/frame-result-*.json"))
    assert len(files) == 10
    assert len({p.name for p in files}) == 10
    for p in files:
        json.loads(p.read_text(encoding="utf-8"))
