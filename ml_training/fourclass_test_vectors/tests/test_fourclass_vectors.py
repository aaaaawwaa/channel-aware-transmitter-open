"""Targeted acceptance tests for the SYNTHETIC four-class test vectors.

These tests are self-contained to this directory; they do not rerun the
historical suites and do not touch a board, Pluto, network or RF hardware.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(__file__).resolve().parents[1]
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

import generator  # noqa: E402
import independent_check  # noqa: E402

DATA = PACKAGE / "data"
RESULTS = PACKAGE / "results"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_manifest_marks_everything_synthetic_and_records_both_rates():
    manifest = load_json(DATA / "manifest.json")
    assert manifest["synthetic"] is True
    assert manifest["real_capture"] is False
    assert manifest["counts_as_real_capture"] is False
    assert manifest["product_qualification"] is False
    assert [v["truth_class"] for v in manifest["vectors"]] == \
        ["BPSK", "QPSK", "QAM16", "QAM64"]
    assert manifest["rates"]["six_msps"]["sample_rate_hz"] == 6_000_000
    assert manifest["rates"]["sixty_msps"]["sample_rate_hz"] == 60_000_000
    assert manifest["rates"]["rates_must_not_mix"] is True
    for vector in manifest["vectors"]:
        assert vector["synthetic"] is True
        assert vector["real_capture"] is False
        assert 1 <= vector["prbs_seed"] <= 127
        assert vector["rates"]["six_msps"]["samples"] == 254
        assert vector["rates"]["sixty_msps"]["samples"] == 4096
        assert vector["rates"]["six_msps"]["bytes"] == 1016
        assert vector["rates"]["sixty_msps"]["bytes"] == 16384
        assert vector["rates"]["six_msps"]["sha256"] != \
            vector["rates"]["sixty_msps"]["sha256"]


def test_files_match_manifest_hashes_and_are_s16_iq_le_little_endian():
    manifest = load_json(DATA / "manifest.json")
    for vector in manifest["vectors"]:
        for rate in ("six_msps", "sixty_msps"):
            record = vector["rates"][rate]
            path = DATA / record["path"]
            raw = path.read_bytes()
            assert len(raw) == record["bytes"]
            assert generator.sha256_bytes(raw) == record["sha256"]
            values = np.frombuffer(raw, dtype="<i2")
            assert values.dtype == np.dtype("<i2")
            assert values.size == record["samples"] * 2
            assert int(values.min()) > -2048
            assert int(values.max()) < 2047


def test_independent_prbs_and_constellation_check_passes_for_both_rates():
    result = independent_check.check(DATA, RESULTS)
    assert result["status"] == "PASS"
    assert len(result["checks"]) == 4
    for row in result["checks"]:
        assert row["six_msps_exact"] is True
        assert row["sixty_msps_exact"] is True
        assert row["six_msps_sha256_matches_manifest"] is True
        assert row["sixty_msps_sha256_matches_manifest"] is True
        assert row["rates_are_distinct"] is True
        assert row["no_clipping_code"] is True


def test_generation_is_deterministic_in_a_temp_directory():
    with tempfile.TemporaryDirectory(prefix="fourclass-vectors-") as temp:
        out = Path(temp) / "data"
        result = generator.generate(PACKAGE / "config" / "vectors.json", out)
        first = load_json(out / "manifest.json")
        second_result = generator.generate(PACKAGE / "config" / "vectors.json", out)
        second = load_json(out / "manifest.json")
        assert first["vectors"] == second["vectors"]
        assert result["manifest_sha256"] == second_result["manifest_sha256"]


def test_frozen_evaluation_is_attached_and_reports_predictions_and_domains():
    evaluation = load_json(RESULTS / "evaluation.json")
    assert evaluation["synthetic"] is True
    assert evaluation["real_capture"] is False
    assert evaluation["real_accuracy_applicable"] is False
    assert evaluation["product_qualification"] is False
    assert evaluation["model"]["frozen_round2_predictor"] is True
    assert evaluation["model"]["prediction_used_as_truth"] is False
    assert evaluation["model"]["classes"] == ["BPSK", "QPSK", "QAM16", "QAM64"]
    assert len(evaluation["predictions"]) == 4
    for row in evaluation["predictions"]:
        assert row["truth_class"] in ("BPSK", "QPSK", "QAM16", "QAM64")
        assert row["input_context_check"]["ok"] is True
        assert row["input_iq_check"]["ok"] is True
        assert row["model_run"] is True
        assert "final_label" in row
        assert "reason" in row
    assert evaluation["summary"]["synthetic_frames"] == 4
    assert evaluation["summary"]["six_msps_all_fed_to_model"] is False
    assert len(evaluation["six_msps_domain_checks"]) == 4


def test_six_msps_is_out_of_model_domain_and_not_silently_coerced():
    evaluation = load_json(RESULTS / "evaluation.json")
    for row in evaluation["six_msps_domain_checks"]:
        assert row["fed_to_model"] is False
        assert row["context_check"]["ok"] is False
        assert row["context_check"]["reason"] == "unsupported_sampling_domain"
        assert row["file_check"]["ok"] is False
        assert row["file_check"]["reason"] == "invalid_iq_length"


def test_sixty_msps_is_exact_ten_times_hold_of_six_msps_period():
    manifest = load_json(DATA / "manifest.json")
    for vector in manifest["vectors"]:
        six = np.frombuffer((DATA / vector["rates"]["six_msps"]["path"]).read_bytes(),
                            dtype="<i2").reshape(-1, 2)
        sixty = np.frombuffer((DATA / vector["rates"]["sixty_msps"]["path"]).read_bytes(),
                              dtype="<i2").reshape(-1, 2)
        period = np.repeat(six, 10, axis=0)
        expected = np.tile(period, (2, 1))[:4096]
        assert np.array_equal(sixty, expected)
        assert vector["rates"]["sixty_msps"]["derived_from_6msps_sha256"] == \
            vector["rates"]["six_msps"]["sha256"]
