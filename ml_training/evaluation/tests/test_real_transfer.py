"""Read-only real-data inventory / transfer tests (CONTRACTS §11).

The fake-evidence tree under ``tests/fixtures/real-evidence`` mirrors the shape of
the real campaign evidence (session manifests, per-frame JSON, .iq frames) but all
IQ bytes are synthetic fixtures.  A separate test parses one REAL frame JSON
read-only (no inference) to prove format compatibility.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ml_training.evaluation import _common as C
from ml_training.evaluation import real_transfer

from conftest import REAL_EVIDENCE
from support import StubPredictor, accept_spec, hash_tree, reject_spec, wrong_spec


def _by_tag(inventory):
    return {(record["session"], record["tag"]): record for record in inventory["frames"]}


def test_collect_inventory_dedupes_and_extracts_truth(fake_canonical, fake_campaign4):
    inventory = real_transfer.collect_inventory(fake_canonical, fake_campaign4)
    summary = inventory["summary"]
    assert summary["n_canonical_frames"] == 10
    assert summary["n_counted_frames"] == 5
    assert summary["n_rejected_frames"] == 5
    assert summary["n_duplicates_mapped"] == 2
    assert summary["n_duplicate_unique_hashes"] == 1
    assert summary["n_excluded_non_canonical"] == 1
    assert summary["per_session"]["train-01"]["n_counted"] == 2
    assert summary["per_session"]["train-02"]["n_counted"] == 1
    assert summary["per_session"]["val-01"]["n_counted"] == 2

    frames = _by_tag(inventory)
    train_normal = frames[("train-01", "normal-01")]
    assert train_normal["status"] == "counted"
    assert train_normal["truth"] == "normal"
    assert train_normal["config"]["mode"] == "BPSK"
    assert train_normal["config"]["symbol_rate_hz"] == 3000000
    assert train_normal["config"]["shaping"] == "RECT"
    assert train_normal["clipped"] is False
    # algorithm outputs are recorded but never promoted to labels
    ignored = train_normal["board_algorithm_labels_ignored"]
    assert ignored["trainval.label"] == "UNKNOWN"
    assert ignored["model_result.label"] == "BPSK"
    assert train_normal["truth"] == "normal"

    assert frames[("train-01", "normal-02")]["status"] == "rejected"
    assert frames[("train-01", "normal-02")]["rejection_reasons"] == ["clipping"]

    duplicate = frames[("train-02", "normal-01")]
    assert duplicate["status"] == "rejected"
    assert duplicate["rejection_reasons"] == ["duplicate_iq_sha256"]
    assert duplicate["duplicate_of"].endswith("train-01/normal-01.iq")

    truth_missing = frames[("train-02", "normal-02")]
    assert truth_missing["status"] == "rejected"
    assert truth_missing["truth"] is None
    assert truth_missing["rejection_reasons"] == ["truth_missing"]
    assert truth_missing["board_algorithm_labels_ignored"]["model_result.label"] == "BPSK"

    config_bad = frames[("train-02", "normal-03")]
    assert config_bad["status"] == "rejected"
    assert any(reason.startswith("config_mismatch") for reason in config_bad["rejection_reasons"])

    hash_bad = frames[("val-01", "normal-02")]
    assert hash_bad["status"] == "rejected"
    assert hash_bad["rejection_reasons"] == ["json_iq_sha256_mismatch"]

    assert len(inventory["duplicates"]) == 1
    duplicate_table = inventory["duplicates"][0]
    assert duplicate_table["canonical_path"].endswith("train-01/normal-01.iq")
    assert duplicate_table["counted_copies"] == 1
    duplicate_paths = [item["path"] for item in duplicate_table["duplicate_paths"]]
    assert len(duplicate_paths) == 2
    assert any("campaign4" in path and "train-01" in path for path in duplicate_paths)
    assert any("train-02/normal-01.iq" in path for path in duplicate_paths)

    assert len(inventory["excluded"]) == 1
    excluded = inventory["excluded"][0]
    assert "campaign4/download/val-01/normal-01.iq" in excluded["path"]
    assert "outside the canonical campaign5 session set" in excluded["reason"]
    assert excluded["truth"] == "normal"


def test_assert_unique_counted_rejects_double_counting():
    with pytest.raises(real_transfer.RealEvidenceError):
        real_transfer.assert_unique_counted([
            {"path": "a.iq", "iq_sha256": "0" * 64},
            {"path": "b.iq", "iq_sha256": "0" * 64},
        ])


def test_algorithm_outputs_are_never_labels():
    document = {
        "model_result": {"label": "BPSK"},
        "demod_result": {"result": {"label": "BPSK"}},
        "analysis": {"clipping_count": 0, "output_clipped": False},
    }
    with pytest.raises(C.TruthUnavailable):
        real_transfer.extract_frame_truth(document)

    document = {
        "trainval": {"truth": "normal", "scenario": "normal", "label": "QPSK",
                     "reason": "SPARSE_AMPLITUDE_OUTLIERS"},
        "model_result": {"label": "QAM16"},
        "demod_result": {"submitted": {"config": {"mode": "BPSK", "symbol_rate_hz": 3000000,
                                                  "shaping": "RECT"}},
                         "result": {"label": "QAM64"}},
        "analysis": {"clipping_count": 0, "output_clipped": False},
    }
    metadata = real_transfer.extract_frame_truth(document)
    assert metadata["truth"] == "normal"
    assert metadata["config_ok"] is True
    ignored = metadata["board_algorithm_labels_ignored"]
    assert ignored["trainval.label"] == "QPSK"
    assert ignored["model_result.label"] == "QAM16"
    assert ignored["demod_result.result.label"] == "QAM64"


def test_truth_outside_canonical_values_is_rejected():
    document = {"trainval": {"truth": "unknown"},
                "analysis": {"clipping_count": 0, "output_clipped": False}}
    with pytest.raises(C.TruthUnavailable):
        real_transfer.extract_frame_truth(document)


def _stub_predictors(inventory):
    counted = [record for record in inventory["frames"] if record["status"] == "counted"]
    cnn_mapping = {}
    baseline_mapping = {}
    for record in counted:
        digest = record["iq_sha256"]
        if record["truth"] == "normal":
            cnn_mapping[digest] = accept_spec("BPSK")
            baseline_mapping[digest] = accept_spec("BPSK")
        else:
            cnn_mapping[digest] = reject_spec("below_energy_floor")
            baseline_mapping[digest] = accept_spec("BPSK")  # baseline has no gate
    independent_normal = next(record for record in counted
                              if record["session"] == "val-01" and record["truth"] == "normal")
    baseline_mapping[independent_normal["iq_sha256"]] = wrong_spec("QPSK")
    return (StubPredictor(cnn_mapping, kind="cnn"),
            StubPredictor(baseline_mapping, kind="baseline"))


def test_run_transfer_end_to_end_and_read_only(fake_canonical, fake_campaign4, tmp_path):
    evidence_root = fake_canonical.parents[2]
    before = hash_tree(evidence_root)
    inventory = real_transfer.collect_inventory(fake_canonical, fake_campaign4)
    cnn, baseline = _stub_predictors(inventory)
    document, failures = real_transfer.run_transfer(
        cnn, baseline, tmp_path, canonical_root=fake_canonical,
        campaign4_root=fake_campaign4)
    after = hash_tree(evidence_root)
    assert before == after, "real transfer evaluation must never modify evidence"

    for name in ("real_inventory.json", "real_bpsk.md", "real_failures.json"):
        assert (tmp_path / name).is_file()
    loaded = json.loads((tmp_path / "real_inventory.json").read_text(encoding="utf-8"))
    assert loaded["schema"] == C.EVAL_SCHEMA_REAL
    assert loaded["scope"]["read_only"] is True
    assert loaded["scope"]["canonical_sessions"] == list(real_transfer.CANONICAL_SESSIONS)
    assert loaded["summary"]["n_canonical_frames"] == 10
    assert loaded["summary"]["n_counted_frames"] == 5
    assert loaded["not_run"] and isinstance(loaded["not_run"], list)
    assert any("four_class_real_overall_accuracy" in item for item in loaded["not_run"])
    assert any("interference_physical_scenarios" in item for item in loaded["not_run"])
    assert "single_real_config" in loaded["statements"]
    assert "host_vs_arm" in loaded["statements"]
    assert "session_diagnostics" in loaded
    assert loaded["session_diagnostics"]["train-01"]["n_normal"] == 1
    assert "lead_diagnostics" in loaded
    assert loaded["lead_diagnostics"]["attribution"]

    cnn_sessions = loaded["sessions"]["cnn"]["sessions"]
    assert cnn_sessions["train-01"]["n_normal"] == 1
    assert cnn_sessions["train-01"]["bpsk_recognition_rate"] == pytest.approx(1.0)
    assert cnn_sessions["train-01"]["normal_rejection_rate"] == pytest.approx(0.0)
    assert cnn_sessions["train-01"]["off_rejected"] == 1
    assert cnn_sessions["train-02"]["n_normal"] == 0
    assert cnn_sessions["train-02"]["bpsk_recognition_rate"] is None
    assert cnn_sessions["val-01"]["bpsk_recognition_rate"] == pytest.approx(1.0)
    overall_cnn = loaded["sessions"]["cnn"]["overall"]
    assert overall_cnn["n_normal"] == 2
    assert overall_cnn["bpsk_recognized"] == 2
    assert overall_cnn["bpsk_recognition_rate"] == pytest.approx(1.0)
    assert overall_cnn["off_rejected"] == 3
    assert overall_cnn["off_rejection_rate"] == pytest.approx(1.0)
    assert overall_cnn["failure_count"] == 0

    overall_baseline = loaded["sessions"]["baseline"]["overall"]
    assert overall_baseline["bpsk_recognition_rate"] == pytest.approx(0.5)
    assert overall_baseline["failure_count"] == 1
    assert overall_baseline["off_false_accept_labels"] == {"BPSK": 3}
    # per-session baseline metrics must use baseline predictions, not CNN predictions
    baseline_sessions = loaded["sessions"]["baseline"]["sessions"]
    assert baseline_sessions["train-01"]["bpsk_recognition_rate"] == pytest.approx(1.0)
    assert baseline_sessions["val-01"]["bpsk_recognition_rate"] == pytest.approx(0.0)
    assert baseline_sessions["val-01"]["failure_count"] == 1
    cnn_sessions = loaded["sessions"]["cnn"]["sessions"]
    assert cnn_sessions["val-01"]["failure_count"] == 0

    # CNN and baseline predictions are stored separately per frame (no overwrite bug)
    frames = {(record["session"], record["tag"]): record for record in loaded["frames"]}
    independent = frames[("val-01", "normal-01")]
    assert independent["predicted_label"] == "BPSK"
    assert independent["baseline_predicted_label"] == "QPSK"
    # every canonical frame exposes the contract-required inventory fields
    for record in loaded["frames"]:
        for key in ("path", "session", "truth", "config", "iq_sha256", "clipped"):
            assert key in record
        if record["status"] == "counted":
            for key in ("predicted_label", "score", "margin", "reason", "gate_reason"):
                assert key in record
            assert record["gate_reason"] == record["reason"]
            for key in ("baseline_predicted_label", "baseline_score",
                        "baseline_margin", "baseline_gate_reason"):
                assert key in record

    failures_loaded = json.loads((tmp_path / "real_failures.json").read_text(encoding="utf-8"))
    assert failures_loaded["schema"] == C.EVAL_SCHEMA_REAL_FAILURES
    assert failures_loaded["cnn"]["failure_count"] == 0
    assert failures_loaded["baseline"]["failure_count"] == 1
    assert failures_loaded["not_run"] and isinstance(failures_loaded["not_run"], list)
    assert failures_loaded["session_diagnostics"]["train-01"]["n_normal"] == 1
    assert failures_loaded["lead_diagnostics"]["attribution"]
    baseline_failure = failures_loaded["baseline"]["failures"][0]
    for key in ("frame_id", "scores", "score", "margin", "phase_deg", "complex_rms",
                "env_label", "env_reason", "config", "demod_diagnostics", "analysis"):
        assert key in baseline_failure

    markdown = (tmp_path / "real_bpsk.md").read_text(encoding="utf-8")
    assert markdown.index("## val-01") < markdown.index("## train-01 and train-02")
    assert "## Failure mode analysis - train-02 (CNN)" in markdown
    assert "Lead diagnostic" in markdown
    assert "NOT_ESTABLISHED" in markdown
    assert "NOT_RUN" in markdown
    assert "NOT ARM" in markdown or "NOT ARM and NOT board" in markdown


def test_real_frame_json_truth_extraction_is_read_only():
    if not REAL_EVIDENCE.is_file():
        pytest.skip("real campaign evidence is not present in this workspace")
    before = C.sha256_file(REAL_EVIDENCE)
    document = C.load_json_file(REAL_EVIDENCE)
    metadata = real_transfer.extract_frame_truth(document)
    assert metadata["truth"] == "normal"
    assert metadata["config_ok"] is True
    assert metadata["clipping_count"] == 0
    assert metadata["clipped"] is False
    ignored = metadata["board_algorithm_labels_ignored"]
    # the board algorithm calls this physical normal frame UNKNOWN; truth is normal
    assert ignored["trainval.label"] == "UNKNOWN"
    assert ignored["model_result.label"] == "BPSK"
    assert metadata["truth"] == "normal"
    demod = metadata["demod_diagnostics"]
    assert demod["phase_deg"] == pytest.approx(-4.89, abs=0.1)  # radians -> degrees
    assert demod["carrier_coherence"] == pytest.approx(0.9761, abs=1e-4)
    assert demod["snr_est_db_from_coherence"] == pytest.approx(16.2, abs=0.3)
    assert demod["ber"] == 0.0
    assert C.sha256_file(REAL_EVIDENCE) == before


def test_real_environment_json_is_parsed_read_only():
    environment_path = REAL_EVIDENCE.parent / "normal-01.environment.json"
    if not environment_path.is_file():
        pytest.skip("real campaign evidence is not present in this workspace")
    before = C.sha256_file(environment_path)
    diagnostics = real_transfer._environment_diagnostics(environment_path)
    assert diagnostics is not None
    assert diagnostics["label"] == "UNKNOWN"
    assert diagnostics["reason"] == "AMPLITUDE_OUTSIDE_RULE_SUPPORT"
    assert diagnostics["features"]["rms_codes"] == pytest.approx(569.54, abs=0.1)
    assert C.sha256_file(environment_path) == before


def test_real_session_manifest_is_parsed_read_only():
    session_dir = REAL_EVIDENCE.parent
    if not (session_dir / "session-manifest.json").is_file():
        pytest.skip("real campaign evidence is not present in this workspace")
    before = C.sha256_file(session_dir / "session-manifest.json")
    manifest = real_transfer.read_session_manifest(session_dir, "train-01")
    assert manifest["fixture"] is False
    assert manifest["physical"] is True
    entries = real_transfer.canonical_session_entries("train-01", session_dir, manifest)
    assert len(entries) == 11
    assert [entry["tag"] for entry in entries][0] == "normal-01"
    assert C.sha256_file(session_dir / "session-manifest.json") == before
