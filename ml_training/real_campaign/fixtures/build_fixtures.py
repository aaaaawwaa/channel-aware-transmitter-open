"""Build small, explicitly synthetic fixtures for the real-campaign import/eval tool.

The fixtures reuse the frozen four-class generator output ``ml_training/datasets/sim-smoke`` and
the existing mapping/reference check report.  They are marked ``synthetic=true`` everywhere and
are never counted as real captures.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "ml_training" / "datasets" / "sim-smoke"
FIRTURES = ROOT / "ml_training" / "real_campaign" / "fixtures" / "synthetic"
REFERENCE = ROOT / "ml_training" / "data" / "reports" / "reference-check-smoke.json"
FROZEN_CONTEXT = {
    "format": "s16_iq_le", "samples": 4096, "sample_rate_hz": 60_000_000,
    "decimation": 1, "symbol_rate_hz": 3_000_000, "shaping": "RECT",
}


def _sha256(path):
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_iq(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(np.asarray(values, dtype="<i2").tobytes())


def _write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1), encoding="utf-8")


def _frame_bytes(values):
    return np.asarray(values, dtype="<i2").tobytes()


def _fixture_document(purpose, kind, truth_class, iq_file, raw, split="TEST",
                      context=None, expected_status="OK", expected_reason=None,
                      non_truth_labels=None, session_id="synthetic-sim-smoke-test",
                      scenario="synthetic", extra=None):
    document = {
        "schema": "ml-real-campaign-fixture-v1",
        "synthetic": True,
        "purpose": purpose,
        "kind": kind,
        "scenario": scenario,
        "session_id": session_id,
        "split": split,
        "iq_file": iq_file,
        "iq_sha256": __import__("hashlib").sha256(raw).hexdigest(),
        "context": dict(context or FROZEN_CONTEXT),
        "truth_source": "synthetic_generator",
        "truth_class": truth_class,
        "truth_detail": "ml_training/datasets/sim-smoke (frozen generator output)",
        "expected_import_status": expected_status,
        "expected_reason": expected_reason,
        "non_truth_labels": non_truth_labels or {},
        "dataset": {
            "dataset_id": "sim-smoke",
            "manifest_path": "ml_training/datasets/sim-smoke/manifest.json",
            "manifest_sha256": _sha256(DATASET / "manifest.json"),
            "reference_check_path": "ml_training/data/reports/reference-check-smoke.json",
            "reference_check_sha256": _sha256(REFERENCE),
        },
        "not_a_real_capture": True,
    }
    if extra:
        document.update(extra)
    return document


def build():
    if not (DATASET / "manifest.json").is_file():
        raise SystemExit("missing synthetic generator dataset: %s" % DATASET)
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    if not reference.get("passed"):
        raise SystemExit("reference-check-smoke report is not PASS")
    mapping_check = next((check for check in reference.get("checks", [])
                          if check.get("name") == "bit_mapping_vs_contract_and_product"),
                         None)
    if not mapping_check or not mapping_check.get("passed"):
        raise SystemExit("reference-check-smoke mapping check is not PASS")
    selected = {}
    for class_name in ("BPSK", "QPSK", "QAM16", "QAM64"):
        npz_path = DATASET / "frames" / ("test-%s.npz" % class_name)
        data = np.load(npz_path)
        index = int(np.argmax(data["snr_db"]))
        values = data["X"][index]
        selected[class_name] = {
            "values": values,
            "npz": npz_path,
            "frame_id": str(data["frame_id"][index]),
            "session_id": str(data["session_id"][index]),
            "snr_db": float(data["snr_db"][index]),
            "symbol_seed": int(data["symbol_seed"][index]),
            "noise_seed": int(data["noise_seed"][index]),
            "phase_rad": float(data["phase_rad"][index]),
            "cfo_hz": float(data["cfo_hz"][index]),
        }
    # Clean valid frames, one per class.
    for class_name, selection in selected.items():
        raw = _frame_bytes(selection["values"])
        iq_path = FIRTURES / "valid" / ("%s.iq" % class_name)
        _write_iq(iq_path, selection["values"])
        document = _fixture_document(
            "valid_synthetic_%s" % class_name.lower(), "clean", class_name,
            iq_path.name, raw, split="TEST", session_id="synthetic-sim-smoke-%s" % class_name,
            extra={"generator": {
                "dataset_id": "sim-smoke", "frame_id": selection["frame_id"],
                "npz_path": str(selection["npz"].relative_to(ROOT)).replace("\\", "/"),
                "npz_sha256": _sha256(selection["npz"]),
                "snr_db": selection["snr_db"],
                "symbol_seed": selection["symbol_seed"],
                "noise_seed": selection["noise_seed"],
                "phase_rad": selection["phase_rad"], "cfo_hz": selection["cfo_hz"],
            }})
        _write_json(FIRTURES / "valid" / ("%s.json" % class_name), document)

    base = selected["BPSK"]
    base_raw = _frame_bytes(base["values"])
    # Invalid length.
    wrong_length = base_raw[:-2]
    (FIRTURES / "invalid").mkdir(parents=True, exist_ok=True)
    (FIRTURES / "invalid" / "wrong_length.iq").write_bytes(wrong_length)
    _write_json(FIRTURES / "invalid" / "wrong_length.json", _fixture_document(
        "invalid_length", "clean", "BPSK", "wrong_length.iq", wrong_length,
        split="TEST", expected_status="INVALID", expected_reason="invalid_iq_length",
        extra={"source_frame_id": base["frame_id"]}))
    # Invalid sampling domain while bytes/truth are otherwise valid.
    sample_rate_context = dict(FROZEN_CONTEXT)
    sample_rate_context["sample_rate_hz"] = 30_000_000
    _write_iq(FIRTURES / "invalid" / "wrong_sample_rate.iq", base["values"])
    _write_json(FIRTURES / "invalid" / "wrong_sample_rate.json", _fixture_document(
        "invalid_sample_rate", "clean", "BPSK", "wrong_sample_rate.iq", base_raw,
        split="TEST", context=sample_rate_context, expected_status="INVALID",
        expected_reason="unsupported_sampling_domain",
        extra={"source_frame_id": base["frame_id"]}))
    # Truth from model prediction only -- must be refused.
    _write_iq(FIRTURES / "invalid" / "truth_from_model.iq", base["values"])
    model_doc = _fixture_document(
        "invalid_truth_from_model", "clean", None, "truth_from_model.iq", base_raw,
        split="TEST", expected_status="INVALID",
        expected_reason="truth_source_not_independent",
        non_truth_labels={"model_result_label": "QPSK"},
        extra={"source_frame_id": base["frame_id"]})
    model_doc["truth_source"] = "model_prediction"
    _write_json(FIRTURES / "invalid" / "truth_from_model.json", model_doc)
    # Truth from environment label only -- must be refused.
    _write_iq(FIRTURES / "invalid" / "truth_from_environment.iq", base["values"])
    env_doc = _fixture_document(
        "invalid_truth_from_environment", "quality", "OFF", "truth_from_environment.iq",
        base_raw, split="TEST", expected_status="INVALID",
        expected_reason="truth_source_not_independent",
        non_truth_labels={"environment_label": "NARROWBAND"},
        extra={"source_frame_id": base["frame_id"]})
    env_doc["truth_source"] = "environment_label"
    _write_json(FIRTURES / "invalid" / "truth_from_environment.json", env_doc)
    # Duplicate IQ spanning TRAIN and VAL (leakage fixture).
    _write_iq(FIRTURES / "duplicates" / "leak-train.iq", base["values"])
    _write_iq(FIRTURES / "duplicates" / "leak-val.iq", base["values"])
    _write_json(FIRTURES / "duplicates" / "leak-train.json", _fixture_document(
        "duplicate_cross_split_train", "clean", "BPSK", "leak-train.iq", base_raw,
        split="TRAIN", session_id="synthetic-leak-train",
        expected_status="OK", extra={"source_frame_id": base["frame_id"]}))
    _write_json(FIRTURES / "duplicates" / "leak-val.json", _fixture_document(
        "duplicate_cross_split_val", "clean", "BPSK", "leak-val.iq", base_raw,
        split="VAL", session_id="synthetic-leak-val",
        expected_status="OK", extra={"source_frame_id": base["frame_id"]}))
    # Duplicate IQ inside one session/split.
    _write_iq(FIRTURES / "duplicates" / "dup-session-b.iq", base["values"])
    _write_json(FIRTURES / "duplicates" / "dup-session-b.json", _fixture_document(
        "duplicate_same_session", "clean", "BPSK", "dup-session-b.iq", base_raw,
        split="TEST", session_id="synthetic-sim-smoke-BPSK",
        expected_status="OK", extra={"source_frame_id": base["frame_id"]}))

    manifest_doc = {
        "schema": "ml-real-campaign-fixtures-v1",
        "synthetic": True,
        "not_a_real_capture": True,
        "source_dataset": {
            "dataset_id": "sim-smoke",
            "manifest_path": "ml_training/datasets/sim-smoke/manifest.json",
            "manifest_sha256": _sha256(DATASET / "manifest.json"),
            "generator": manifest.get("generator"),
            "config_sha256": manifest.get("config_sha256"),
            "reference_check_path": "ml_training/data/reports/reference-check-smoke.json",
            "reference_check_sha256": _sha256(REFERENCE),
            "reference_check_passed": reference.get("passed"),
            "reference_check_checks": "%s/%s" % (reference.get("checks_passed"),
                                                  reference.get("checks_total")),
            "bit_mapping_vs_contract_and_product_passed": True,
        },
        "valid": [str(path.relative_to(FIRTURES)).replace("\\", "/")
                  for path in sorted((FIRTURES / "valid").glob("*.json"))],
        "invalid": [str(path.relative_to(FIRTURES)).replace("\\", "/")
                    for path in sorted((FIRTURES / "invalid").glob("*.json"))],
        "duplicates": [str(path.relative_to(FIRTURES)).replace("\\", "/")
                       for path in sorted((FIRTURES / "duplicates").glob("*.json"))],
        "selected_frames": {
            class_name: {"frame_id": selection["frame_id"],
                         "snr_db": selection["snr_db"],
                         "npz_sha256": _sha256(selection["npz"])}
            for class_name, selection in selected.items()
        },
    }
    _write_json(FIRTURES / "manifest.json", manifest_doc)
    return manifest_doc


def main():
    manifest = build()
    print(json.dumps({"valid": len(manifest["valid"]),
                      "invalid": len(manifest["invalid"]),
                      "duplicates": len(manifest["duplicates"]),
                      "reference_check": manifest["source_dataset"]["reference_check_checks"],
                      "status": "PASS"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
