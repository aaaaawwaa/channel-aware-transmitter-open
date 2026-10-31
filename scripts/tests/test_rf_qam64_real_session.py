"""Fake-chain tests for the QAM64/RECT real-RX runner (offline only).

No board, RF, install, candidate or production-receiver change is performed.
The fake record carries the same-frame IQ/metadata/observation identity that the
real board session returns; the runner must bind it and reserve a replay entry.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "pynq_overlay" / "sw"))

import rf_qam64_real_validation as V  # noqa: E402
from rf_bpsk_ui_board_session import _rx_engine_ready  # noqa: E402


def qam64_config(seed=39):
    return dict(config=dict(mode="QAM64", shaping="RECT", iq_order="QI",
                            symbol_rate_hz=3_000_000,
                            reference_prbs7_seed=seed),
                revision=3, decim=1, rx_len=4096, input_fs_hz=60_000_000)


def record(tag, physical, *, seed=39, bad=None):
    if physical:
        result = dict(
            algorithm_version="qam64-rect-snapshot-v2", iq_order="QI",
            demod_config=qam64_config(seed), envelope_present=True, reason="",
            carrier_sync=True, timing_sync=True, reference_sync=True,
            bit_errors=0, compared_bits=384, ber=0.0, valid_symbols=127,
            alignment_bits=378, comparison_start_symbol=63,
            evm_available=False, evm_rms=None, capture_id=tag)
        if bad == "one_error":
            result.update(bit_errors=1, ber=1.0 / 384.0)
        elif bad == "desync":
            result.update(reason="REFERENCE_NOT_FOUND", carrier_sync=False,
                          timing_sync=False, reference_sync=False,
                          bit_errors=None, compared_bits=None, ber=None,
                          valid_symbols=None, alignment_bits=None,
                          comparison_start_symbol=None)
        elif bad == "desync_metrics":
            result.update(reason="REFERENCE_NOT_FOUND", reference_sync=False,
                          carrier_sync=False, timing_sync=False)
        elif bad == "bad_denominator":
            result.update(compared_bits=383)
        elif bad == "bad_count":
            result.update(bit_errors=385, ber=385.0 / 384.0)
        elif bad == "bad_ber":
            result.update(bit_errors=1, ber=0.5)
        elif bad == "incomplete_metrics":
            result.update(ber=None)
        elif bad == "domain":
            result.update(envelope_present=False, reason="INPUT_DOMAIN",
                          reference_sync=False, bit_errors=None,
                          compared_bits=None, ber=None)
        elif bad == "no_envelope":
            result.update(envelope_present=False, reason="QAM64 unavailable",
                          reference_sync=False, bit_errors=None,
                          compared_bits=None, ber=None)
        elif bad == "wrong_identity":
            result.update(capture_id="old-capture")
        elif bad == "seed_mismatch":
            result.update(demod_config=qam64_config(seed + 1))
        elif bad == "qam16_submission":
            result.update(demod_config=dict(config=dict(
                mode="QAM16", shaping="RECT", iq_order="QI",
                symbol_rate_hz=3_000_000, reference_prbs7_seed=seed),
                revision=3, decim=1, rx_len=4096, input_fs_hz=60_000_000))
        elif bad == "qam16_version":
            result.update(algorithm_version="qam16-rect-snapshot-v1")
    else:
        result = dict(
            algorithm_version="qam64-rect-snapshot-v2", iq_order="QI",
            demod_config=qam64_config(seed), envelope_present=False,
            reason="REFERENCE_NOT_FOUND", carrier_sync=False,
            timing_sync=False, reference_sync=False, bit_errors=None,
            compared_bits=None, ber=None, valid_symbols=None,
            alignment_bits=None, comparison_start_symbol=None,
            evm_available=False, evm_rms=None, capture_id=tag)
        if bad == "off_residual_sync":
            result.update(reference_sync=True)
        elif bad == "off_residual_ber":
            result.update(bit_errors=0, compared_bits=384, ber=0.0)
    out = dict(
        capture_id=("other" if bad == "wrong_identity" else tag),
        iq_sha256="a" * 64, iq_file=tag + ".iq",
        metadata_file=tag + ".json",
        capture_observation_file=tag + ".capture-observation.json",
        capture_observation={"status": "PRESENT"}, release_id="4ea1c0a6",
        result=result, egress_safe=True, post_tx_source_selected=False,
        pre_tx_source_selected=False,
        off_tx_ok=(True if not physical else None), cleanup=None)
    if bad == "off_no_observation" and not physical:
        out.pop("capture_observation_file")
        out.pop("capture_observation")
    if bad == "missing_artifact":
        out.pop("metadata_file")
    if bad == "missing_observation":
        out.pop("capture_observation_file")
    if bad == "observation_error":
        out["capture_observation_error"] = "SIDECAR_LOST"
    return out


class FakeQam64Session:
    calls = []
    bad = None

    def __init__(self, expected, output, **kwargs):
        self.expected = expected
        self.output = pathlib.Path(output)
        self.kwargs = kwargs
        self.frame = V.FRAMES[kwargs["session_index"]]
        self.tag = kwargs["run_id"]
        type(self).calls.append(("new", self.tag, kwargs))

    def initial(self):
        type(self).calls.append(("initial", self.tag))
        return {"safe": True, "release_id": self.expected}

    def setup(self, seed):
        type(self).calls.append(("setup", self.tag, seed))
        if type(self).bad == "capability":
            raise RuntimeError("QAM64 engine not available: qam64_available=False")

    def capture(self, tag, physical):
        type(self).calls.append(("capture", tag, physical))
        if type(self).bad == "timeout":
            raise TimeoutError("capture deadline")
        return record(tag, physical, seed=self.frame["rx_seed"],
                      bad=(type(self).bad if type(self).bad in (
                          "one_error", "desync", "desync_metrics",
                          "bad_denominator", "bad_count", "bad_ber",
                          "incomplete_metrics", "domain", "no_envelope",
                          "off_residual_sync", "off_residual_ber",
                          "wrong_identity", "seed_mismatch",
                          "qam16_submission", "qam16_version",
                          "missing_artifact", "missing_observation",
                          "observation_error", "off_no_observation") else None))

    def cleanup(self):
        type(self).calls.append(("cleanup", self.tag))
        if type(self).bad == "cleanup":
            return {"safe": False, "disarm_ack": False}
        return {"safe": True, "disarm_ack": True}

    def close(self):
        type(self).calls.append(("close", self.tag))


def _run(tmp_path, bad=None):
    FakeQam64Session.calls = []
    FakeQam64Session.bad = bad
    return V.run_validation("4ea1c0a6", tmp_path / "out",
                            session_factory=FakeQam64Session,
                            cable_confirmed=True)


def test_off_frames_without_capture_observation_continue(tmp_path):
    summary = _run(tmp_path, bad="off_no_observation")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is True
    rows = {r["tag"]: r for r in summary["rows"]}
    assert rows["qam64-off-first"]["capture_observation"] is None
    assert rows["qam64-off-last"]["capture_observation"] is None
    assert rows["qam64-rect-39-a"]["capture_observation"] == {"status": "PRESENT"}


def test_four_frame_order_budget_capture_deadline_and_artifacts(tmp_path):
    summary = _run(tmp_path)
    assert [r["tag"] for r in summary["rows"]] == [
        "qam64-off-first", "qam64-rect-39-a", "qam64-rect-39-b",
        "qam64-off-last"]
    assert summary["counts"] == dict(sessions=4, initializations=4, loads=4,
                                     on_tx=2, captures=4)
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is True
    assert summary["stop_cause"] is None
    assert summary["qam64_on_frames"] == 2
    assert summary["qam64_clean_sync"] == 2
    plan = json.loads((tmp_path / "out" / "plan.json").read_text())
    assert plan["capture_seconds"] == 10.0
    assert plan["budget"] == V.BUDGET
    assert "replay_policy" in plan
    for call in [c for c in FakeQam64Session.calls if c[0] == "new"]:
        kwargs = call[2]
        assert kwargs["capture_deadline"] == 10.0
        assert kwargs["rx_mode"] == "QAM64"
        assert kwargs["tx_mode"] == "QAM64"
        assert kwargs["rx_shaping"] == kwargs["tx_shaping"] == "RECT"
        assert kwargs["rx_rolloff"] == kwargs["tx_rolloff"] == "0.35"
    for row in summary["rows"]:
        assert row["iq_file"].endswith(".iq")
        assert row["metadata_file"].endswith(".json")
        assert row["capture_observation_file"].endswith(".capture-observation.json")
        assert row["capture_observation"] == {"status": "PRESENT"}
        assert row["replay_entry"]["status"] == "RESERVED"
        assert row["replay_entry"]["capture_id"] == row["capture_id"]
        assert row["replay_entry"]["iq_sha256"] == row["iq_sha256"]


def test_on_clean_exact_384_and_evm_unavailable(tmp_path):
    summary = _run(tmp_path)
    for row in [r for r in summary["rows"] if r["physical"]]:
        r = row["result"]
        assert (r["carrier_sync"], r["timing_sync"], r["reference_sync"]) == \
            (True, True, True)
        assert r["reason"] == ""
        assert (r["valid_symbols"], r["alignment_bits"],
                r["comparison_start_symbol"]) == (127, 378, 63)
        assert (r["bit_errors"], r["compared_bits"], r["ber"]) == (0, 384, 0.0)
        assert r["evm_available"] is False and r["evm_rms"] is None


def test_nonzero_ber_full_batch_but_metrics_false(tmp_path):
    summary = _run(tmp_path, "one_error")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is False
    assert summary["stop_cause"] is None
    assert len(summary["rows"]) == 4
    assert summary["metric_failures"] == ["qam64-rect-39-a", "qam64-rect-39-b"]
    assert all(r["metric_problem"] == "metric_negative_nonzero_ber"
               for r in summary["rows"] if r["physical"])


def test_legal_desync_full_batch_metrics_false(tmp_path):
    summary = _run(tmp_path, "desync")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is False
    assert len(summary["rows"]) == 4
    assert all(r["metric_problem"] == "legal_desync_negative"
               for r in summary["rows"] if r["physical"])


@pytest.mark.parametrize("bad,prefix", [
    ("bad_denominator", "metric_denominator_mismatch"),
    ("bad_count", "metric_count_out_of_range"),
    ("bad_ber", "metric_ber_inconsistent"),
    ("incomplete_metrics", "incomplete_metric_triplet"),
    ("desync_metrics", "desync_metrics_present"),
])
def test_metric_corruption_stops(tmp_path, bad, prefix):
    summary = _run(tmp_path / bad, bad=bad)
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith(prefix)
    assert len(summary["rows"]) == 2


@pytest.mark.parametrize("bad,prefix,rows", [
    ("wrong_identity", "stale_demod_capture_id", 1),
    ("seed_mismatch", "submission_seed_mismatch", 2),
    ("qam16_submission", "submission_mode_not_qam64", 2),
    ("qam16_version", "qam64_algorithm_version_mismatch", 2),
    ("missing_artifact", "missing_metadata_file", 1),
    ("missing_observation", "missing_capture_observation_file", 2),
    ("observation_error", "capture_observation_error", 1),
])
def test_identity_config_and_artifact_failures_stop(tmp_path, bad, prefix, rows):
    summary = _run(tmp_path / bad, bad=bad)
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith(prefix)
    assert len(summary["rows"]) == rows


@pytest.mark.parametrize("bad,prefix", [
    ("off_residual_sync", "off_reference_sync_residual"),
    ("off_residual_ber", "off_ber_residual"),
])
def test_off_residual_stops(tmp_path, bad, prefix):
    summary = _run(tmp_path / bad, bad=bad)
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith(prefix)
    assert len(summary["rows"]) == 1


def test_cleanup_unsafe_stops_after_frame(tmp_path):
    summary = _run(tmp_path, "cleanup")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("cleanup_not_safe")
    assert len(summary["rows"]) == 1


def test_capability_absent_stops(tmp_path):
    summary = _run(tmp_path, "capability")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("runtime_error:RuntimeError")


def test_timeout_stops(tmp_path):
    summary = _run(tmp_path, "timeout")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("runtime_error:TimeoutError")


def test_qam64_submission_is_not_qam64_by_tx_or_label():
    good = {"demod_config": qam64_config(39)}
    bad = {"demod_config": dict(
        config=dict(mode="QAM16", shaping="RECT", iq_order="QI",
                    symbol_rate_hz=3_000_000, reference_prbs7_seed=39),
        revision=3, decim=1, rx_len=4096, input_fs_hz=60_000_000)}
    assert V.qam64_submission_problem(good) is None
    assert V.qam64_submission_problem(bad) == "submission_mode_not_qam64"


def test_capability_helper_explicit_for_all_modes():
    assert _rx_engine_ready(dict(available=True), "BPSK", "RECT") is True
    assert _rx_engine_ready(dict(qpsk_available=True), "QPSK", "RECT") is True
    assert _rx_engine_ready(dict(rrc_available=True), "QPSK", "RRC") is True
    assert _rx_engine_ready(dict(qam16_available=True), "QAM16", "RECT") is True
    assert _rx_engine_ready(dict(qam64_available=True), "QAM64", "RECT") is True
    assert _rx_engine_ready(dict(qam64_available=False), "QAM64", "RECT") is False
    assert _rx_engine_ready(dict(qam16_available=True), "QAM64", "RECT") is False


def test_old_result_is_bound_to_its_own_frame():
    good = record("qam64-a", True)
    assert V._structural_problem(V.FRAMES[1], good, "4ea1c0a6") is None
    stale = record("qam64-a", True)
    stale["result"]["capture_id"] = "qam64-old"
    assert V._structural_problem(V.FRAMES[1], stale, "4ea1c0a6") == \
        "stale_demod_capture_id"
