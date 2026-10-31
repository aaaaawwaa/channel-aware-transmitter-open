"""Acceptance tests for the integration adapter prototype and input-domain contract.

Host-only, read-only w.r.t. product code and board. Uses the frozen arm_probe package and the
already-captured ARM replay evidence.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "ml_training" / "integration_candidate"
ARM = ROOT / "ml_training" / "arm_probe"
EVIDENCE = ROOT / "evidence" / "rf-teaching" / "s2-46db-arm-replay-2026-10-21"
NEW_RAW = ROOT / "evidence" / "rf-teaching" / "s2-46db-80mhz-narrowband-2026-10-21" / "raw"
BASE_FRAME = ARM / "frames" / "clean_real_bpsk" / "clean-real-new-normal.iq"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ARM) not in sys.path:
    sys.path.insert(0, str(ARM))

from ml_training.integration_candidate import consistency_check, negative_cases  # noqa: E402
from ml_training.integration_candidate.input_domain import (  # noqa: E402
    FROZEN_INPUT_CONTEXT, REASON_INVALID_IQ_FORMAT, REASON_INVALID_IQ_LENGTH,
    REASON_MISSING_CONTEXT, REASON_UNSUPPORTED_FORMAT, REASON_UNSUPPORTED_LENGTH,
    REASON_UNSUPPORTED_SAMPLING, REASON_UNSUPPORTED_SYMBOL, validate_context,
    validate_iq)
from ml_training.integration_candidate.modrec4_adapter import (  # noqa: E402
    BOARD_CLASSES, MOD4_CLASSES, ModRec4Adapter)
from runtime.iq import read_frame  # noqa: E402


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class _NoModelCall:
    def __getattr__(self, name):
        raise AssertionError("model must not be called for invalid input (touched %r)" % name)


@pytest.fixture(scope="module")
def adapter():
    return ModRec4Adapter()


@pytest.fixture(scope="module")
def manifest():
    return load_json(ARM / "input_manifest.json")


@pytest.fixture(scope="module")
def expected():
    return load_json(ARM / "expected" / "host_expected.json")


def _context(**overrides):
    context = dict(FROZEN_INPUT_CONTEXT)
    context.update(overrides)
    return context


def test_four_vs_six_class_contract(adapter):
    contract = adapter.contract_map()
    assert contract["board_contract"] == "cnn-bpsk-environment-v1"
    assert contract["board_classes"] == list(BOARD_CLASSES)
    assert contract["board_head_probabilities"] == 6
    assert contract["modulation_head_probabilities"] == 4
    assert contract["modulation_classes"] == list(MOD4_CLASSES)
    assert contract["six_class_probabilities"] is None
    assert contract["known_class_subset_mapping"] == {
        "BPSK": "BPSK", "QPSK": "QPSK", "QAM16": "QAM16", "QAM64": "QAM64"}
    assert contract["environment_side_classes"] == ["TONE", "OTHER"]
    assert contract["extension_required"] is True
    assert contract["input_domain"]["sample_rate_hz"] == 60_000_000
    assert contract["input_domain"]["decimation"] == 1
    assert contract["input_domain"]["symbol_rate_hz"] == 3_000_000
    assert contract["input_domain"]["shaping"] == "RECT"


def test_input_domain_validator():
    assert validate_context(FROZEN_INPUT_CONTEXT)["ok"] is True
    assert validate_context(None)["reason"] == REASON_MISSING_CONTEXT
    assert validate_context(_context(format="u16_iq_be"))["reason"] == REASON_UNSUPPORTED_FORMAT
    assert validate_context(_context(samples=2048))["reason"] == REASON_UNSUPPORTED_LENGTH
    assert validate_context(_context(sample_rate_hz=30_000_000))["reason"] == \
        REASON_UNSUPPORTED_SAMPLING
    assert validate_context(_context(decimation=2))["reason"] == REASON_UNSUPPORTED_SAMPLING
    assert validate_context(_context(symbol_rate_hz=2_000_000))["reason"] == \
        REASON_UNSUPPORTED_SYMBOL
    assert validate_context(_context(shaping="RRC"))["reason"] == REASON_UNSUPPORTED_SYMBOL
    iq = read_frame(BASE_FRAME)
    assert validate_iq(iq)["ok"] is True
    assert validate_iq(iq[:-1])["reason"] == REASON_INVALID_IQ_FORMAT
    assert validate_iq(iq.astype(np.float32))["reason"] == REASON_INVALID_IQ_FORMAT


def test_missing_context_is_unavailable_without_model_call(adapter):
    iq = read_frame(BASE_FRAME)
    original = adapter.predictor
    try:
        adapter.predictor = _NoModelCall()
        result = adapter.evaluate_iq(iq, context=None)
    finally:
        adapter.predictor = original
    assert result["status"] == "UNAVAILABLE"
    assert result["input_domain"]["reason"] == REASON_MISSING_CONTEXT
    assert "decimation" in result["input_domain"]["missing_fields"]
    modulation = result["modulation"]
    assert modulation["final_label"] == "UNKNOWN"
    assert modulation["accepted"] is False
    assert modulation["cnn_raw_scores4"] is None
    assert modulation["cnn_logits4"] is None
    assert modulation["arbitration_source"] == "none"
    assert modulation["final_label_source"] == "unavailable"
    control = adapter.evaluate_iq(iq, context=FROZEN_INPUT_CONTEXT)
    assert control["status"] == "OK"
    assert control["modulation"]["final_label"] == "BPSK"


def test_unsupported_domain_is_unavailable_and_does_not_infer(adapter):
    iq = read_frame(BASE_FRAME)
    original = adapter.predictor
    try:
        adapter.predictor = _NoModelCall()
        for context, reason in (
                (_context(sample_rate_hz=30_000_000), REASON_UNSUPPORTED_SAMPLING),
                (_context(decimation=2), REASON_UNSUPPORTED_SAMPLING),
                (_context(symbol_rate_hz=2_000_000), REASON_UNSUPPORTED_SYMBOL),
                (_context(shaping="RRC"), REASON_UNSUPPORTED_SYMBOL),
                (_context(format="u16_iq_be"), REASON_UNSUPPORTED_FORMAT)):
            result = adapter.evaluate_iq(iq, context=context)
            assert result["status"] == "UNAVAILABLE"
            assert result["modulation"]["reason"] == reason
            assert result["modulation"]["final_label"] == "UNKNOWN"
            assert result["modulation"]["cnn_raw_scores4"] is None
    finally:
        adapter.predictor = original


def test_raw_bytes_and_length_validation(adapter):
    raw = BASE_FRAME.read_bytes()
    ok = adapter.evaluate_bytes(raw, context=FROZEN_INPUT_CONTEXT)
    assert ok["status"] == "OK"
    too_short = adapter.evaluate_bytes(raw[:-2], context=FROZEN_INPUT_CONTEXT)
    assert too_short["status"] == "UNAVAILABLE"
    assert too_short["modulation"]["reason"] == REASON_INVALID_IQ_LENGTH
    not_bytes = adapter.evaluate_bytes("not-bytes", context=FROZEN_INPUT_CONTEXT)
    assert not_bytes["status"] == "UNAVAILABLE"
    assert not_bytes["modulation"]["reason"] == REASON_INVALID_IQ_FORMAT


def test_negative_cases_are_all_rejected_with_reasons():
    summary = negative_cases.run_cases()
    assert summary["status"] == "PASS"
    assert summary["cases_total"] == 14
    assert summary["cases_passed"] == 14
    expected = {
        "missing_context": REASON_MISSING_CONTEXT,
        "partial_context_missing_decimation": REASON_MISSING_CONTEXT,
        "unsupported_format": REASON_UNSUPPORTED_FORMAT,
        "unsupported_samples_length": REASON_UNSUPPORTED_LENGTH,
        "unsupported_sample_rate": REASON_UNSUPPORTED_SAMPLING,
        "unsupported_decimation": REASON_UNSUPPORTED_SAMPLING,
        "unsupported_symbol_rate": REASON_UNSUPPORTED_SYMBOL,
        "unsupported_shaping": REASON_UNSUPPORTED_SYMBOL,
        "invalid_iq_shape": REASON_INVALID_IQ_FORMAT,
        "invalid_iq_dtype": REASON_INVALID_IQ_FORMAT,
        "invalid_iq_range": REASON_INVALID_IQ_FORMAT,
        "invalid_iq_big_endian": REASON_INVALID_IQ_FORMAT,
        "invalid_raw_length": REASON_INVALID_IQ_LENGTH,
        "invalid_raw_not_bytes": REASON_INVALID_IQ_FORMAT,
    }
    for row in summary["cases"]:
        assert row["expected_reason"] == expected[row["id"]]
        assert row["pass"] is True, row
    assert summary["valid_context_control"]["pass"] is True


def test_adapter_matches_frozen_replay_for_all_frames(adapter, manifest, expected):
    result = consistency_check.compare_adapter(expected, manifest)
    assert result["frames"] == 43
    assert result["input_domain_valid_frames"] == 43
    assert result["mismatch_count"] == 0, result["mismatches"][:5]
    assert result["max_logit_delta"] <= 1e-3
    assert result["max_spectral_delta"] <= 1e-6
    assert result["status"] == "PASS"


def test_modulation_output_is_separate_and_never_six_class(adapter, manifest):
    entry = next(frame for frame in manifest["frames"]
                 if frame["id"] == "clean-real-new-normal")
    environment = load_json(NEW_RAW / "nf-00-normal.environment.json")["environment"]
    demod = load_json(NEW_RAW / "nf-00-normal.json")["demod_result"]
    six_class = {"label": "QPSK", "scores": [0.01, 0.02, 0.9, 0.03, 0.01, 0.03]}
    env_before = json.loads(json.dumps(environment))
    demod_before = json.loads(json.dumps(demod))
    six_before = json.loads(json.dumps(six_class))
    result = adapter.evaluate_frame(ARM / entry["path"], context=FROZEN_INPUT_CONTEXT,
                                    environment=environment, demod=demod,
                                    six_class=six_class)
    assert result["status"] == "OK"
    assert result["input_domain"]["status"] == "VALID"
    modulation = result["modulation"]
    assert modulation["final_label"] == "BPSK"
    assert modulation["accepted"] is True
    assert modulation["reason"] == ""
    assert modulation["final_label_source"] == "baseline_arbitration"
    assert modulation["arbitration_source"] == "baseline_feature_model"
    assert modulation["cnn_raw_top1"] == "QPSK"
    assert modulation["cnn_raw_scores4"] is not None
    assert int(np.argmax(modulation["cnn_raw_scores4"])) == MOD4_CLASSES.index("QPSK")
    assert modulation["final_label"] not in ("TONE", "OTHER")
    assert result["contract"]["six_class_probabilities"] is None
    assert result["passthrough"]["environment"] == env_before
    assert result["passthrough"]["demod"] == demod_before
    assert result["passthrough"]["six_class"] == six_before
    assert environment == env_before and demod == demod_before and six_class == six_before
    product = adapter.to_product_fields(result)
    assert product["modrec4"]["final_label"] == "BPSK"
    assert product["modrec4"]["final_label_source"] == "baseline_arbitration"
    assert product["modrec4"]["cnn_raw_top1"] == "QPSK"
    assert product["modrec4"]["cnn_raw_scores4"] is not None
    assert product["modrec4"]["arbitration_source"] == "baseline_feature_model"
    assert product["modrec4"]["six_class_probabilities"] is None
    assert "environment" not in product and "demod" not in product


def test_rejected_frames_are_unknown_with_reason(adapter, manifest, expected):
    by_id = {frame["id"]: frame for frame in expected["frames"]}
    for entry in manifest["frames"]:
        if not entry["group"].startswith("gate_"):
            continue
        result = adapter.evaluate_frame(ARM / entry["path"],
                                        context=FROZEN_INPUT_CONTEXT,
                                        frame_id=entry["id"])
        want = by_id[entry["id"]]["after"]
        modulation = result["modulation"]
        assert modulation["final_label"] == "UNKNOWN"
        assert modulation["accepted"] is False
        assert modulation["final_label_source"] == "gate_rejection"
        assert modulation["arbitration_source"] == "none"
        assert modulation["reason"] == want["reason"]


def test_final_label_raw_cnn_and_arbitration_are_distinguished(adapter, manifest):
    failure = next(frame for frame in manifest["frames"]
                   if frame["id"] == "clean-fail-train02-normal-01")
    result = adapter.evaluate_frame(ARM / failure["path"], context=FROZEN_INPUT_CONTEXT)
    modulation = result["modulation"]
    assert modulation["final_label"] == "BPSK"
    assert modulation["final_label_source"] == "baseline_arbitration"
    assert modulation["arbitration_source"] == "baseline_feature_model"
    assert modulation["cnn_raw_top1"] == "QPSK"
    assert int(np.argmax(modulation["cnn_raw_scores4"])) == MOD4_CLASSES.index("QPSK")
    clean = next(frame for frame in manifest["frames"]
                 if frame["id"] == "clean-real-bpsk-train-01-normal-01")
    clean_result = adapter.evaluate_frame(ARM / clean["path"], context=FROZEN_INPUT_CONTEXT)
    assert clean_result["modulation"]["final_label"] == "BPSK"
    assert clean_result["modulation"]["final_label_source"] == "cnn_top1"
    assert clean_result["modulation"]["arbitration_source"] == "none"


def test_narrowband_stress_is_stress_only(adapter, manifest, expected):
    by_id = {frame["id"]: frame for frame in expected["frames"]}
    stress = [entry for entry in manifest["frames"]
              if entry["group"] == "stress_narrowband"]
    assert len(stress) == 3
    for entry in stress:
        result = adapter.evaluate_frame(ARM / entry["path"],
                                        context=FROZEN_INPUT_CONTEXT,
                                        frame_id=entry["id"])
        assert result["modulation"]["final_label"] == "BPSK"
        assert result["modulation"]["arbitration_source"] == "baseline_feature_model"
        assert by_id[entry["id"]]["kind"] == "stress"


def test_arm_evidence_sync():
    facts = consistency_check.arm_evidence_sync()
    assert facts["status"] == "PASS"
    assert facts["frames"] == 43
    assert facts["label_mismatch_count"] == 0
    assert facts["board_environment"]["python"] == "3.8.2"
    assert facts["board_environment"]["numpy"] == "1.17.4"
    assert facts["board_environment"]["machine"] == "armv7l"
    assert abs(facts["timing_ms"]["p95_ms"] - 63.239008900382025) < 1e-9
    assert facts["rss_vmhwm_kb"] == 23144
    assert abs(facts["peak_mib"] - 22.6015625) < 1e-9
    assert facts["package_manifest_sha256_top_level"] == facts["package_manifest_sha256_local"]
    assert facts["board_results_vs_expected"]["mismatch_count"] == 0
    assert facts["not_product_runtime"] is True
    assert facts["product_end_to_end_qualified"] is False


def test_passthrough_and_example_output(adapter):
    check = consistency_check.passthrough_check()
    assert check["status"] == "PASS"
    assert all(check["checks"].values())
    assert (PKG / "results" / "example_output.json").is_file()
    example = load_json(PKG / "results" / "example_output.json")
    assert example["status"] == "OK"
    assert example["modulation"]["final_label"] == "BPSK"
    assert example["passthrough"]["environment"] is not None
    assert example["contract"]["six_class_probabilities"] is None
    assert example["input_domain"]["status"] == "VALID"


def test_consistency_and_negative_results_are_green():
    document = load_json(PKG / "results" / "consistency.json")
    assert document["status"] == "PASS"
    assert document["adapter_vs_replay"]["mismatch_count"] == 0
    assert document["adapter_vs_replay"]["input_domain_valid_frames"] == 43
    assert document["negative_cases"]["status"] == "PASS"
    assert document["negative_cases"]["cases_passed"] == 14
    assert document["arm_evidence"]["label_mismatch_count"] == 0
    assert document["product_end_to_end_qualified"] is False
    negative = load_json(PKG / "results" / "negative_cases.json")
    assert negative["status"] == "PASS"
    assert negative["cases_passed"] == 14


def test_cli_single_frame_and_no_context():
    manifest = load_json(ARM / "input_manifest.json")
    entry = next(frame for frame in manifest["frames"]
                 if frame["id"] == "clean-fail-train02-normal-01")
    with tempfile.TemporaryDirectory(prefix="adapter-cli-") as temp:
        out = Path(temp) / "result.json"
        completed = subprocess.run(
            [sys.executable, str(PKG / "run_adapter.py"), "--frame",
             str(ARM / entry["path"]), "--out", str(out)],
            cwd=str(ROOT), capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        document = load_json(out)
        assert document["result"]["status"] == "OK"
        assert document["result"]["modulation"]["final_label"] == "BPSK"
        assert document["product_fields"]["modrec4"]["six_class_probabilities"] is None
        no_context = Path(temp) / "no_context.json"
        completed = subprocess.run(
            [sys.executable, str(PKG / "run_adapter.py"), "--frame",
             str(ARM / entry["path"]), "--no-context", "--out", str(no_context)],
            cwd=str(ROOT), capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        unavailable = load_json(no_context)["result"]
        assert unavailable["status"] == "UNAVAILABLE"
        assert unavailable["modulation"]["reason"] == REASON_MISSING_CONTEXT
        assert unavailable["modulation"]["cnn_raw_scores4"] is None
