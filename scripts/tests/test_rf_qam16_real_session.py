"""Fake-chain and real-product-entry tests for the fixed QAM16/RECT runner."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "pynq_overlay" / "sw"))

import rf_qam16_real_validation as V  # noqa: E402
from rf_bpsk_ui_board_session import _rx_engine_ready  # noqa: E402


def qam_config(seed=39):
    return dict(config=dict(mode="QAM16", shaping="RECT", iq_order="QI",
                            symbol_rate_hz=3_000_000,
                            reference_prbs7_seed=seed),
                revision=3, decim=1, rx_len=4096, input_fs_hz=60_000_000)


def record(tag, physical, *, seed=39, bad=None, wrong_ref=False):
    if physical:
        result = dict(
            algorithm_version="qam16-rect-snapshot-v1", iq_order="QI",
            demod_config=qam_config(seed), envelope_present=True, reason="",
            carrier_sync=True, timing_sync=True, reference_sync=True,
            bit_errors=0, compared_bits=256, ber=0.0, valid_symbols=127,
            alignment_bits=252, comparison_start_symbol=63,
            evm_available=False, evm_rms=None)
        if bad == "one_error":
            result.update(bit_errors=1, ber=1.0 / 256.0)
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
            result.update(compared_bits=255)
        elif bad == "bad_count":
            result.update(bit_errors=257, ber=257.0 / 256.0)
        elif bad == "bad_ber":
            result.update(bit_errors=1, ber=0.5)
        elif bad == "incomplete_metrics":
            result.update(ber=None)
        elif bad == "domain":
            result.update(envelope_present=False, reason="INPUT_DOMAIN",
                          reference_sync=False, bit_errors=None,
                          compared_bits=None, ber=None)
        elif bad == "no_envelope":
            result.update(envelope_present=False, reason="QAM16 unavailable",
                          reference_sync=False, bit_errors=None,
                          compared_bits=None, ber=None)
        elif bad == "wrong_identity":
            result.update(capture_id="old-capture")
        elif bad == "seed_mismatch":
            result.update(demod_config=qam_config(seed + 1))
    else:
        result = dict(
            algorithm_version="qam16-rect-snapshot-v1", iq_order="QI",
            demod_config=qam_config(seed), envelope_present=False,
            reason="REFERENCE_NOT_FOUND", carrier_sync=False,
            timing_sync=False, reference_sync=False, bit_errors=None,
            compared_bits=None, ber=None, valid_symbols=None,
            alignment_bits=None, comparison_start_symbol=None,
            evm_available=False, evm_rms=None)
        if bad == "off_residual_sync":
            result.update(reference_sync=True)
        elif bad == "off_residual_ber":
            result.update(bit_errors=0, compared_bits=256, ber=0.0)
    return dict(capture_id=("other" if bad == "wrong_identity" else tag),
                iq_sha256="a" * 64, release_id="cd76818a",
                result=result, egress_safe=True, post_tx_source_selected=False,
                pre_tx_source_selected=False,
                off_tx_ok=(True if not physical else None), cleanup=None)


class FakeQamSession:
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
        type(self).calls.append(("setup", self.tag, seed, self.kwargs))
        if type(self).bad == "capability":
            raise RuntimeError("QAM16 engine not available: qam16_available=False")

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
                          "wrong_identity", "seed_mismatch") else None))

    def cleanup(self):
        type(self).calls.append(("cleanup", self.tag))
        if type(self).bad == "cleanup":
            return {"safe": False, "disarm_ack": False}
        return {"safe": True, "disarm_ack": True}

    def close(self):
        type(self).calls.append(("close", self.tag))


def _run(tmp_path, bad=None):
    FakeQamSession.calls = []
    FakeQamSession.bad = bad
    return V.run_validation("cd76818a", tmp_path / "out",
                            session_factory=FakeQamSession, cable_confirmed=True)


def test_four_frame_plan_order_budget_and_prewritten_metrics(tmp_path):
    summary = _run(tmp_path)
    assert summary["plan_name"] == "qam16"
    assert [r["tag"] for r in summary["rows"]] == [
        "qam16-off-first", "qam16-rect-39-a", "qam16-rect-39-b",
        "qam16-off-last"]
    assert summary["counts"] == dict(sessions=4, initializations=4, loads=4,
                                     on_tx=2, captures=4)
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is True
    assert summary["stop_cause"] is None
    assert summary["qam16_on_frames"] == 2
    assert summary["qam16_clean_sync"] == 2
    plan = json.loads((tmp_path / "out" / "plan.json").read_text())
    assert plan["plan_name"] == "qam16" and len(plan["frames"]) == 4
    assert plan["budget"] == V.BUDGET
    assert plan["frame_metrics"]["qam16-off-first"]["kind"] == "off"
    assert plan["frame_metrics"]["qam16-rect-39-a"]["compared_bits"] == 256
    for call in [c for c in FakeQamSession.calls if c[0] == "new"]:
        kwargs = call[2]
        assert kwargs["rx_mode"] == "QAM16"
        assert kwargs["rx_shaping"] == "RECT"
        assert kwargs["tx_mode"] == "QAM16"
        assert kwargs["tx_shaping"] == "RECT"
        assert kwargs["rx_rolloff"] == "0.35"
        assert kwargs["capture_deadline"] == 10.0
    on_rows = [r for r in summary["rows"] if r["physical"]]
    for row in on_rows:
        assert row["result"]["demod_config"]["config"]["mode"] == "QAM16"
        assert row["result"]["demod_config"]["config"]["shaping"] == "RECT"
        assert row["result"]["demod_config"]["config"]["iq_order"] == "QI"
        assert (row["result"]["bit_errors"], row["result"]["compared_bits"],
                row["result"]["ber"]) == (0, 256, 0.0)
        assert row["result"]["valid_symbols"] == 127
        assert row["result"]["alignment_bits"] == 252
        assert row["result"]["comparison_start_symbol"] == 63
        assert row["result"]["evm_available"] is False


def test_nonzero_ber_one_of_256_completes_batch_but_metrics_fail(tmp_path):
    summary = _run(tmp_path, bad="one_error")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is False
    assert summary["stop_cause"] is None
    assert len(summary["rows"]) == 4
    assert summary["metric_failures"] == ["qam16-rect-39-a", "qam16-rect-39-b"]
    assert all(r["metric_problem"] == "metric_negative_nonzero_ber"
               for r in summary["rows"] if r["physical"])


def test_legal_desync_completes_batch_as_legal_negative(tmp_path):
    summary = _run(tmp_path, bad="desync")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is False
    assert summary["stop_cause"] is None
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
def test_metric_container_or_structural_corruption_stops(tmp_path, bad, prefix):
    summary = _run(tmp_path / bad, bad=bad)
    assert summary["execution_complete"] is False
    assert summary["structural_valid"] is False
    assert str(summary["stop_cause"]).startswith(prefix)
    assert len(summary["rows"]) == 2


def test_timeout_stops(tmp_path):
    summary = _run(tmp_path, bad="timeout")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("runtime_error:TimeoutError")


def test_config_seed_mismatch_stops(tmp_path):
    summary = _run(tmp_path, bad="seed_mismatch")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("submission_seed_mismatch")
    assert len(summary["rows"]) == 2


def test_wrong_identity_stops(tmp_path):
    summary = _run(tmp_path, bad="wrong_identity")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("stale_demod_capture_id")
    assert len(summary["rows"]) == 2


@pytest.mark.parametrize("bad,prefix", [
    ("off_residual_sync", "off_reference_sync_residual"),
    ("off_residual_ber", "off_ber_residual"),
])
def test_off_residual_sync_or_ber_stops(tmp_path, bad, prefix):
    summary = _run(tmp_path / bad, bad=bad)
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith(prefix)
    assert len(summary["rows"]) == 1


def test_cleanup_unsafe_stops_after_frame(tmp_path):
    summary = _run(tmp_path, bad="cleanup")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("cleanup_not_safe")
    assert len(summary["rows"]) == 1


def test_capability_absent_stops(tmp_path):
    summary = _run(tmp_path, bad="capability")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("runtime_error:RuntimeError")


def test_out_of_domain_or_missing_envelope_stops(tmp_path):
    for bad in ("domain", "no_envelope"):
        summary = _run(tmp_path / bad, bad=bad)
        assert summary["execution_complete"] is False
        assert str(summary["stop_cause"]).startswith("missing_product_demod")
        assert len(summary["rows"]) == 2


def test_old_modes_capability_helper_is_explicit():
    assert _rx_engine_ready(dict(available=True), "BPSK", "RECT") is True
    assert _rx_engine_ready(dict(qpsk_available=True), "QPSK", "RECT") is True
    assert _rx_engine_ready(dict(rrc_available=True), "QPSK", "RRC") is True
    assert _rx_engine_ready(dict(qam16_available=True), "QAM16", "RECT") is True
    assert _rx_engine_ready(dict(qam16_available=False), "QAM16", "RECT") is False
    assert _rx_engine_ready(dict(available=True), "QAM16", "RECT") is False


def test_submission_does_not_reject_different_seed():
    result = dict(demod_config=qam_config(40), envelope_present=True)
    assert V.qam16_submission_problem(result) is None
    assert V.qam16_evidence_problem({"rx_seed": 40}, {
        **result, "algorithm_version": "qam16-rect-snapshot-v1",
        "iq_order": "QI", "reason": "", "carrier_sync": True,
        "timing_sync": True, "reference_sync": True, "valid_symbols": 127,
        "alignment_bits": 252, "comparison_start_symbol": 63,
        "bit_errors": 0, "compared_bits": 256, "ber": 0.0,
        "evm_available": False, "evm_rms": None}) is None


def test_real_product_entry_seed39_and_seed40_align_to_zero_of_256(tmp_path):
    """Run the actual candidate product entry, not a mocked ModemResult."""
    qam = pathlib.Path(__file__).resolve().parents[2] / (
        "evidence/rf-teaching/qam16-rect-product-2026-10-24")
    release = qam / "candidate/release"
    clean = qam / "bundle/trial/trial/frames/qam16-clean-qi.iq"
    mismatch = qam / "bundle/trial/trial/frames/qam16-mismatch-qi.iq"
    script = r"""
import dataclasses, json, pathlib, sys
release, clean, mismatch, workspace = map(pathlib.Path, sys.argv[1:5])
sys.path.insert(0, str(release))
sys.path.insert(0, str(workspace / "pynq_overlay/sw/tests"))
import qam16_reference as R
from rf_rx_types import RxIqFrame, RxReadback
from rf_qam16_pipeline import freeze_qam16_demod_config, analyze_qam16
sys.path.insert(0, str(workspace / "scripts"))
from rf_bpsk_ui_board_session import _demod_fields
import rf_qam16_real_validation as V

def make(payload, cid):
    return RxIqFrame(capture_id=cid, payload=payload, samples=4096,
        sample_rate_hz=60000000, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=3, request_id=3,
        release_id="cd76818afc3bee7c610678574e892b26", op="rx.capture",
        physical=False, pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)

def run(payload, seed, cid):
    draft = dict(expected_mode="QAM16", shaping="RECT", symrate_hz=3000000,
                 reference_enabled=True, reference_seed=seed, decim=1,
                 rx_len=4096, input_fs_hz=60000000)
    cfg = freeze_qam16_demod_config(draft, 3, iq_order="QI")
    env = analyze_qam16(make(payload, cid), cfg)
    r = _demod_fields(env)
    r["envelope_present"] = True
    r["demod_config"] = dataclasses.asdict(env.submitted)
    return r

seed39 = run(clean.read_bytes(), 39, "qam16-seed39")
wire40, _ = R.make_frame_bytes(40, phase_deg=17.0, iq_order="QI")
seed40 = run(wire40, 40, "qam16-seed40")
mism = run(mismatch.read_bytes(), 39, "qam16-mismatch")
out = {
 "seed39": {"problem": V.qam16_evidence_problem({"rx_seed": 39}, seed39),
            **{k: seed39.get(k) for k in ("bit_errors", "compared_bits", "ber",
              "valid_symbols", "alignment_bits", "comparison_start_symbol",
              "reference_sync", "evm_available")}},
 "seed40": {"problem": V.qam16_evidence_problem({"rx_seed": 40}, seed40),
            **{k: seed40.get(k) for k in ("bit_errors", "compared_bits", "ber",
              "valid_symbols", "alignment_bits", "comparison_start_symbol",
              "reference_sync", "evm_available")}},
 "mismatch": {"problem": V.qam16_evidence_problem({"rx_seed": 39}, mism),
              "reason": mism.get("reason"),
              "reference_sync": mism.get("reference_sync"),
              "bit_errors": mism.get("bit_errors"),
              "compared_bits": mism.get("compared_bits"), "ber": mism.get("ber"),
              "metric": V._metric_problem({"physical": True}, {"result": mism})},
}
print(json.dumps(out, sort_keys=True))
raise SystemExit(0 if (out["seed39"]["problem"] is None
                       and out["seed40"]["problem"] is None
                       and out["mismatch"]["problem"] is None
                       and out["mismatch"]["reason"]
                       and out["mismatch"]["bit_errors"] is None
                       and out["mismatch"]["metric"] == "legal_desync_negative")
                 else 1)
"""
    proc = __import__("subprocess").run(
        [sys.executable, "-B", "-c", script, str(release), str(clean),
         str(mismatch), str(pathlib.Path(__file__).resolve().parents[2])],
        capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    for key in ("seed39", "seed40"):
        assert data[key]["problem"] is None
        assert (data[key]["bit_errors"], data[key]["compared_bits"],
                data[key]["ber"]) == (0, 256, 0.0)
        assert data[key]["valid_symbols"] == 127
        assert data[key]["alignment_bits"] == 252
        assert data[key]["comparison_start_symbol"] == 63
        assert data[key]["reference_sync"] is True
        assert data[key]["evm_available"] is False
    assert data["mismatch"]["problem"] is None
    assert data["mismatch"]["reason"]
    assert data["mismatch"]["reference_sync"] is False
    assert data["mismatch"]["bit_errors"] is None
    assert data["mismatch"]["compared_bits"] is None
    assert data["mismatch"]["ber"] is None
    assert data["mismatch"]["metric"] == "legal_desync_negative"
