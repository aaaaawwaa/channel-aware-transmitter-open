"""Acceptance tests for the offline real-campaign import/evaluation tool."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "ml_training" / "real_campaign"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.real_campaign import dedupe, evaluate  # noqa: E402
from ml_training.real_campaign import domain  # noqa: E402
from ml_training.real_campaign.importer import import_all  # noqa: E402
from ml_training.real_campaign.sources import load_sources  # noqa: E402


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def imported():
    return import_all(load_sources())


@pytest.fixture(scope="module")
def grouping(imported):
    return dedupe.analyze(imported["records"])


@pytest.fixture(scope="module")
def real_eval(imported):
    return evaluate.evaluate_real(imported)


@pytest.fixture(scope="module")
def synthetic_eval(imported):
    return evaluate.evaluate_synthetic(imported)


def test_real_bpsk_data_is_imported_with_explicit_truth(imported):
    counts = imported["counts"]
    assert counts["real"] == 71
    assert counts["real_invalid"] == 0
    real = imported["real_records"]
    assert len([record for record in real if record["kind"] == "clean"]) == 62
    assert len([record for record in real if record["kind"] == "quality"]) == 6
    assert len([record for record in real if record["kind"] == "stress"]) == 3
    assert all(record["status"] == "OK" for record in real)
    assert all(record["domain_status"] == "OK" for record in real)
    classes = {record["truth_class"] for record in real}
    assert "QPSK" not in classes and "QAM16" not in classes and "QAM64" not in classes
    # A real BPSK frame whose old six-class model said QPSK: model label must not be truth.
    mismatch = [record for record in real
                if record["non_truth_labels"].get("model_result_label") == "QPSK"
                and record["truth_class"] == "BPSK"]
    assert mismatch
    assert all(record["truth_source"] in ("signal_config", "experiment_record")
               for record in real)


def test_identity_fields_are_recorded_without_fabrication(imported):
    real = imported["real_records"]
    campaign3 = [record for record in real
                 if record["source_id"].startswith("campaign3")]
    assert campaign3
    assert campaign3[0]["identity"]["boot_declared"] is True
    assert campaign3[0]["identity"]["boot_id"]
    undeclared = [record for record in real
                  if record["source_id"].startswith("teaching")]
    assert undeclared
    assert all(record["identity"]["boot_declared"] is False
               for record in undeclared)
    assert all(record["identity"]["release_id"] for record in real)


def test_invalid_fixtures_are_rejected_for_the_right_reason(imported):
    synthetic = {record["frame_id"]: record for record in imported["synthetic_records"]}
    by_suffix = {frame_id.split("/")[-1]: record for frame_id, record in synthetic.items()}
    assert by_suffix["wrong_length.json"]["invalid_reasons"] == ["invalid_iq_length"]
    assert "unsupported_sampling_domain" in \
        by_suffix["wrong_sample_rate.json"]["invalid_reasons"]
    assert "truth_source_not_independent" in \
        by_suffix["truth_from_model.json"]["invalid_reasons"]
    assert "truth_source_not_independent" in \
        by_suffix["truth_from_environment.json"]["invalid_reasons"]
    assert by_suffix["wrong_length.json"]["status"] == "INVALID"
    assert by_suffix["truth_from_model.json"]["truth_class"] is None
    assert by_suffix["truth_from_environment.json"]["truth_class"] != "BPSK"


def test_valid_fixtures_are_explicitly_synthetic():
    manifest = load_json(PKG / "fixtures" / "synthetic" / "manifest.json")
    assert manifest["synthetic"] is True
    assert manifest["not_a_real_capture"] is True
    assert manifest["source_dataset"]["reference_check_passed"] is True
    assert manifest["source_dataset"]["reference_check_checks"] == "22/22"
    valid = list((PKG / "fixtures" / "synthetic" / "valid").glob("*.json"))
    assert len(valid) == 4
    for path in valid:
        document = load_json(path)
        assert document["synthetic"] is True
        assert document["truth_source"] == "synthetic_generator"
        assert document["truth_class"] in ("BPSK", "QPSK", "QAM16", "QAM64")


def test_duplicate_and_cross_split_leakage_detection(imported, grouping):
    assert grouping["real_leakage_group_count"] == 0
    assert grouping["leakage_group_count"] >= 1
    leak = [group for group in grouping["leakage_groups"]
            if all(record["synthetic"] for record in group["records"])]
    assert leak
    assert "TRAIN" in leak[0]["splits"]
    assert "VAL" in leak[0]["splits"]
    assert len(leak[0]["splits"]) > 1
    assert grouping["duplicate_group_count"] >= 2
    # campaign5 TRAIN is a real duplicate re-download of campaign4 TRAIN.
    real_duplicates = [group for group in grouping["duplicate_groups"]
                       if not all(record["synthetic"] for record in group["records"])]
    assert real_duplicates
    assert all(record["status"] == "OK" for group in real_duplicates
               for record in group["records"])


def test_evaluation_separates_clean_quality_stress_and_missing_classes(real_eval):
    assert real_eval["frames_deduplicated"] == 22
    clean = real_eval["clean"]
    assert clean["frames"] == 42
    assert clean["counts_by_class"] == {"BPSK": 42, "QPSK": 0, "QAM16": 0, "QAM64": 0}
    assert clean["missing_classes"] == ["QPSK", "QAM16", "QAM64"]
    assert clean["four_class_overall_accuracy"]["status"] == "NOT_RUN"
    assert "value" not in clean["four_class_overall_accuracy"]
    assert clean["per_class"]["BPSK"]["accuracy"] == 1.0
    assert clean["known_false_rejection"] == {"count": 0, "total": 42, "rate": 0.0}
    quality = real_eval["quality"]
    assert quality["total"] == 4
    assert quality["rejected"] == 4
    assert quality["false_accept"] == 0
    assert quality["rejection_rate"] == 1.0
    stress = real_eval["stress"]
    assert stress["total"] == 3
    assert stress["accepted"] == 3
    assert "not counted in clean four-class accuracy" in stress["note"]
    assert real_eval["per_session"]
    assert all("clean" in session and "quality" in session and "stress" in session
               for session in real_eval["per_session"])


def test_synthetic_evaluation_is_separate(imported, real_eval, synthetic_eval):
    assert synthetic_eval["synthetic"] is True
    assert real_eval["synthetic"] is False
    assert synthetic_eval["clean"]["frames"] == 4
    assert synthetic_eval["clean"]["counts_by_class"] == {
        "BPSK": 1, "QPSK": 1, "QAM16": 1, "QAM64": 1}
    assert synthetic_eval["clean"]["four_class_overall_accuracy"]["status"] == "PASS"
    # Synthetic metrics must never appear in the real missing-class list.
    assert real_eval["clean"]["counts_by_class"]["QPSK"] == 0


def test_domain_validator_rejects_bad_context_and_length(tmp_path):
    iq = (PKG / "fixtures" / "synthetic" / "valid" / "BPSK.iq").read_bytes()
    assert domain.load_iq(PKG / "fixtures" / "synthetic" / "valid" / "BPSK.iq")["ok"]
    short = tmp_path / "short.iq"
    short.write_bytes(iq[:-2])
    assert domain.load_iq(short)["reason"] == "invalid_iq_length"
    context = dict(domain.FROZEN_CONTEXT)
    context["sample_rate_hz"] = 30_000_000
    assert domain.validate_context(context)["reason"] == "unsupported_sampling_domain"
    assert domain.validate_context(None)["reason"] == "missing_input_context"


def test_one_command_writes_report_and_indexes():
    completed = subprocess.run(
        [sys.executable, str(PKG / "run.py")],
        cwd=str(ROOT), capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["real_clean_frames"] == 42
    assert payload["missing_real_classes"] == ["QPSK", "QAM16", "QAM64"]
    assert payload["fixture_checks"]["status"] == "PASS"
    assert payload["duplicates"]["real_leakage"] == 0
    for relative in ("index/records.json", "index/sessions.json",
                     "index/duplicates.json", "reports/real_campaign_report.md",
                     "reports/real_campaign_report.json"):
        assert (PKG / relative).is_file(), relative
    report = (PKG / "reports" / "real_campaign_report.md").read_text(encoding="utf-8")
    assert "QPSK" in report and "NOT_RUN" in report
    assert "42/42" in report
    report_json = load_json(PKG / "reports" / "real_campaign_report.json")
    assert report_json["status"] == "PASS"
    assert report_json["missing_real_classes"] == ["QPSK", "QAM16", "QAM64"]
    assert report_json["real_evaluation"]["clean"]["four_class_overall_accuracy"]["status"] == "NOT_RUN"


def test_delivery_docs_exist():
    for relative in ("README.md", "docs/collection_prep.md", "docs/next_batch_fields.md"):
        assert (PKG / relative).is_file(), relative
    prep = (PKG / "docs" / "collection_prep.md").read_text(encoding="utf-8")
    assert "不执行采集" in prep
    assert "板卡 RX-only" in prep or "RX-only" in prep
    fields = (PKG / "docs" / "next_batch_fields.md").read_text(encoding="utf-8")
    assert "truth_source" in fields and "iq_sha256" in fields
