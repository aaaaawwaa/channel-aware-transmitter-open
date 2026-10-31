"""Acceptance checks for the frozen round-2 transfer test on the 2026-10-21 new real frames.

Read-only: recomputes the frozen pipeline on the 4 new physical frames and compares against
the raw results that were saved before any report/improvement.  Also verifies data-format,
truth, hash and development-use audit claims.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.iq import read_frame  # noqa: E402
from ml_training.model.numpy_runtime import load_model  # noqa: E402
from ml_training.round2.predictor import Round2Predictor  # noqa: E402

ML = ROOT / "ml_training"
EVIDENCE = ROOT / "evidence" / "rf-teaching" / "s2-46db-80mhz-narrowband-2026-10-21"
RAW = EVIDENCE / "raw"
REPORTS = ML / "round2" / "evaluation" / "reports"
RAW_RESULTS = REPORTS / "real_transfer_2026-10-21_raw.json"
REPORT_MD = REPORTS / "real_transfer_2026-10-21.md"
CHECKLIST_MD = ML / "round2" / "reports" / "arm-compat-checklist.md"
TAGS = ("nf-00-normal", "nf-01-narrowband", "nf-02-narrowband", "nf-03-narrowband")
FROZEN = {
    ML / "artifacts" / "cnn" / "model.json":
        "25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc",
    ML / "artifacts" / "baseline" / "model.json":
        "caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014",
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def raw_document():
    return json.loads(RAW_RESULTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def frozen_cnn():
    return load_model(ML / "artifacts" / "cnn" / "model.json")


@pytest.fixture(scope="module")
def predictor():
    return Round2Predictor()


def test_frozen_artifacts_and_rule_identity(raw_document):
    for path, expected in FROZEN.items():
        assert sha256(path) == expected
    frozen = raw_document["frozen_pipeline"]
    assert frozen["cnn_model_json"]["sha256"] == sha256(ML / "artifacts" / "cnn" / "model.json")
    assert frozen["baseline_model_json"]["sha256"] == sha256(
        ML / "artifacts" / "baseline" / "model.json")
    assert frozen["rule_config"]["sha256"] == sha256(
        ML / "round2" / "artifacts" / "rule_config.json")
    assert frozen["rule_config"]["arbitration_threshold"] == 0.30
    assert abs(frozen["rule_config"]["tone_rule"]["threshold"] - 0.527328260750662) < 1e-12
    assert abs(frozen["rule_config"]["noise_rule"]["threshold"] - 0.25161) < 1e-12
    assert raw_document["discipline"]["retrained"] is False
    assert raw_document["discipline"]["recalibrated"] is False
    assert raw_document["discipline"]["device_accessed"] is False
    assert raw_document["discipline"]["product_runtime_modified"] is False


def test_data_format_and_hash_checks(raw_document):
    frames = raw_document["frames"]
    assert [item["tag"] for item in frames] == list(TAGS)
    for item in frames:
        assert item["format"]["format_ok"] is True
        assert item["format"]["bytes"] == 16384
        assert item["format"]["shape"] == [4096, 2]
        assert -2048 <= item["format"]["min_code"] <= item["format"]["max_code"] <= 2047
        assert item["format"]["all_zero"] is False
        assert item["format"]["rail_count"] == 0
        assert item["hashes"]["metadata_match"] is True
        assert item["hashes"]["summary_match"] is True
        assert item["hashes"]["iq_sha256"] == sha256(RAW / (item["tag"] + ".iq"))
    assert raw_document["data"]["sha256sums"]["all_ok"] is True
    assert raw_document["data"]["sha256sums"]["entries"] >= 4


def test_config_truth_is_not_environment_label(raw_document):
    for item in raw_document["frames"]:
        assert item["truth"]["tx_config_ok"] is True
        assert item["truth"]["tx_config"]["mode"] == "BPSK"
        assert item["truth"]["tx_config"]["symbol_rate_hz"] == 3_000_000
        assert item["truth"]["tx_config"]["shaping"] == "RECT"
        assert item["truth"]["environment_label_is_not_truth"] is True
    normal = raw_document["frames"][0]
    assert normal["truth"]["demod_ber"] == 0.0
    assert normal["truth"]["demod_reference_sync"] is True
    assert normal["identity"]["environment_label"] == "UNKNOWN"
    for item in raw_document["frames"][1:]:
        assert item["truth"]["demod_reason"] == "CARRIER_NOT_SYNCED"
        assert item["identity"]["environment_label"] == "NARROWBAND"


def test_data_never_used_in_development(raw_document):
    audit = raw_document["development_use_audit"]
    assert audit["overlap_count"] == 0
    assert audit["pre_existing_reference_count"] == 0
    assert "no hash overlap" in audit["conclusion"]
    assert raw_document["discipline"]["results_saved_before_any_improvement"] is True
    assert "development replay" in raw_document["discipline"]["dev_replay_note"]


def test_frozen_pipeline_recomputation_matches_saved_results(raw_document, frozen_cnn,
                                                             predictor):
    by_tag = {item["tag"]: item for item in raw_document["frames"]}
    for tag in TAGS:
        iq = read_frame(RAW / (tag + ".iq"))
        assert iq.shape == (4096, 2) and iq.dtype == np.int16
        expected = by_tag[tag]
        before = frozen_cnn.predict_iq(iq)
        assert before["label"] == expected["before"]["label"]
        assert (before.get("reason") or "") == (expected["before"]["reason"] or "")
        after = predictor.decide(iq)
        assert after["label"] == expected["after"]["label"]
        assert (after["reason"] or "") == (expected["after"]["reason"] or "")
        assert after["arbitration_applied"] == expected["after"]["arbitration_applied"]
        classification = predictor.classify(iq)
        assert classification["class_label"] == expected["after"]["pure_class_label"]


def test_normal_frame_result(raw_document):
    normal = raw_document["summary"]["normal"]
    assert normal["frames"] == 1
    assert normal["before_labels"] == ["QPSK"]
    assert normal["after_labels"] == ["BPSK"]
    assert normal["after_reasons"] == [""]
    assert normal["clean_4class_accuracy"] == 1.0
    assert "single normal frame" in normal["note"]


def test_narrowband_stress_is_separate(raw_document):
    stress = raw_document["summary"]["narrowband_stress"]
    assert stress["frames"] == 3
    assert stress["counted_as_clean_4class"] is False
    assert stress["before_labels"] == ["QPSK", "QPSK", "QPSK"]
    assert stress["after_labels"] == ["BPSK", "BPSK", "BPSK"]
    assert stress["after_reasons"] == ["", "", ""]
    for item in raw_document["frames"][1:]:
        assert item["after"]["spectral_trigger"] is None
        assert item["after"]["spectral"]["top5_frac"] < 0.527328
        assert item["after"]["spectral"]["band_frac_3m"] > 0.251610


def test_report_and_checklist_exist():
    assert REPORT_MD.is_file()
    text = REPORT_MD.read_text(encoding="utf-8")
    for needle in ("冻结", "调制识别真值", "不计入干净四类", "dev replay", "n=1"):
        assert needle in text, needle
    assert CHECKLIST_MD.is_file()
    checklist = CHECKLIST_MD.read_text(encoding="utf-8")
    for needle in ("4 类", "6 类", "ARM", "NOT_RUN", "时延"):
        assert needle in checklist, needle
