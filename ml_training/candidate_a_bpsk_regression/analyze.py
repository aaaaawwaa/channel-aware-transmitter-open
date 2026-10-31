#!/usr/bin/env python3
"""Locate candidate A's BPSK regression relative to the frozen original model.

Read-only: original and A models/rules, sim-v1 VAL and known test split, plus the
task80 new-seed independent diagnostic result.  No training, threshold changes,
device access or modification of A bytes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
DATASET = ROOT / "ml_training" / "datasets" / "sim-v1"
A_DIR = ROOT / "ml_training" / "qam16_domain_repair" / "results" / "candidate_A"
TASK80_EVAL = (ROOT / "evidence" / "rf-teaching" /
               "s2-46db-fourclass-seed-validation-2026-10-22" /
               "eval-task80" / "seed_validation_result.json")
TASK80_BPSK = (ROOT / "evidence" / "rf-teaching" /
               "s2-46db-fourclass-seed-validation-2026-10-22" /
               "raw" / "02-bpsk-2" / "s00-on-00")
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
BINS_SNR = [(-99.0, -6.0), (-6.0, 0.0), (0.0, 6.0), (6.0, 12.0), (12.0, 99.0)]
BINS_CFO = [(0.0, 2000.0), (2000.0, 10000.0), (10000.0, 20001.0)]
BINS_AMP = [(0, 150), (150, 300), (300, 600), (600, 1201)]

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split  # noqa: E402
from ml_training.common.gate import top_scores  # noqa: E402
from ml_training.round2.predictor import Round2Predictor  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def path_of(predictor, iq):
    probs, x, gate = predictor.cnn_probabilities(iq)
    if probs is None:
        return {"stage": "preprocess", "reason": gate.get("reason"), "gate": gate}
    if gate["rms"] < predictor.gate["min_complex_rms"]:
        return {"stage": "energy_floor", "reason": "below_energy_floor",
                "gate": gate, "scores": probs.tolist()}
    spectral_reason, spectral_value, spectral_features = predictor._spectral_rule(x)
    if spectral_reason:
        return {"stage": "spectral", "reason": spectral_reason, "gate": gate,
                "scores": probs.tolist(), "spectral_value": spectral_value}
    index, score, second = top_scores(probs)
    margin = float(score - second)
    if score < predictor.gate["max_softmax"]:
        return {"stage": "confidence", "reason": "confidence", "gate": gate,
                "scores": probs.tolist(), "score": float(score),
                "margin": margin}
    if margin < predictor.gate["margin"]:
        return {"stage": "margin", "reason": "margin", "gate": gate,
                "scores": probs.tolist(), "score": float(score),
                "margin": margin}
    baseline_probs = predictor.baseline_probabilities(x)
    chosen, applied, detail = predictor._arbitrate(probs, baseline_probs)
    return {
        "stage": "arbitration" if applied else "cnn_top1",
        "reason": "",
        "gate": gate,
        "scores": probs.tolist(),
        "baseline_scores": baseline_probs.tolist(),
        "cnn_label": detail["cnn_label"],
        "baseline_label": detail["baseline_label"],
        "arbitration_margin_bpsk_minus_qpsk": float(detail["bpsk_minus_qpsk"]),
        "arbitration_applied": bool(applied),
        "final": CLASSES[int(chosen)],
    }


def rate_for(values, errors, bins):
    out = []
    for low, high in bins:
        total = sum(1 for value in values if low <= value < high)
        bad = sum(1 for value in errors if low <= value < high)
        out.append({"low": low, "high": high, "total": total, "errors": bad,
                    "rate": (bad / total) if total else None})
    return out


def evaluate_split(original, candidate, split):
    manifest = load_manifest(DATASET)
    data = load_split(DATASET, manifest, split, labels=list(CLASSES))
    bpsk_indices = [index for index, label in enumerate(data["label"])
                    if str(label) == "BPSK"]
    bpsk = []
    new_errors = []
    fixed = []
    original_paths = {}
    candidate_paths = {}
    for index in bpsk_indices:
        iq = data["X"][index]
        original_decision = original.decide(iq)
        candidate_decision = candidate.decide(iq)
        original_path = path_of(original, iq)
        candidate_path = path_of(candidate, iq)
        record = {
            "index": int(index),
            "snr_db": float(data["snr_db"][index]),
            "cfo_hz": float(data["cfo_hz"][index]),
            "abs_cfo_hz": abs(float(data["cfo_hz"][index])),
            "amplitude_codes": int(data["amplitude_codes"][index]),
            "window_offset": int(data["window_offset"][index]),
            "phase_rad": float(data["phase_rad"][index]),
            "session_id": str(data["session_id"][index]),
            "seed_family": str(data["seed_family"][index]),
            "original": {
                "label": original_decision.get("label"),
                "reason": original_decision.get("reason"),
                "scores": original_decision.get("scores"),
                "path": original_path,
            },
            "candidate_A": {
                "label": candidate_decision.get("label"),
                "reason": candidate_decision.get("reason"),
                "scores": candidate_decision.get("scores"),
                "path": candidate_path,
            },
        }
        bpsk.append(record)
        original_paths[original_path["stage"]] = original_paths.get(
            original_path["stage"], 0) + 1
        candidate_paths[candidate_path["stage"]] = candidate_paths.get(
            candidate_path["stage"], 0) + 1
        if original_decision.get("label") == "BPSK" and candidate_decision.get(
                "label") != "BPSK":
            new_errors.append(record)
        if original_decision.get("label") != "BPSK" and candidate_decision.get(
                "label") == "BPSK":
            fixed.append(record)
    errors = new_errors
    summary = {
        "split": split,
        "bpsk_frames": len(bpsk),
        "new_A_BPSK_errors": len(errors),
        "A_fixed_BPSK_errors": len(fixed),
        "new_error_final": {
            label: sum(1 for row in errors
                       if row["candidate_A"]["label"] == label)
            for label in list(CLASSES) + ["UNKNOWN"]},
        "new_error_stage": {
            stage: sum(1 for row in errors
                       if row["candidate_A"]["path"]["stage"] == stage)
            for stage in ("preprocess", "energy_floor", "spectral", "confidence",
                          "margin", "arbitration", "cnn_top1")},
        "new_error_reason": {
            reason: sum(1 for row in errors
                        if (row["candidate_A"]["reason"] or "accepted") == reason)
            for reason in ("", "confidence", "noise_like", "tone_like",
                           "below_energy_floor", "margin", "preprocess")},
        "new_error_cnn_top1": {
            label: sum(1 for row in errors
                       if row["candidate_A"]["path"].get("cnn_label") == label)
            for label in CLASSES},
        "new_error_baseline_top1": {
            label: sum(1 for row in errors
                       if row["candidate_A"]["path"].get("baseline_label") == label)
            for label in CLASSES},
        "new_error_arbitration_applied": sum(
            1 for row in errors
            if row["candidate_A"]["path"].get("arbitration_applied") is True),
        "new_error_arbitration_margin": [
            row["candidate_A"]["path"].get("arbitration_margin_bpsk_minus_qpsk")
            for row in errors],
        "original_paths": original_paths,
        "A_paths": candidate_paths,
        "strata": {
            "snr_db": rate_for([row["snr_db"] for row in bpsk],
                               [row["snr_db"] for row in errors], BINS_SNR),
            "abs_cfo_hz": rate_for([row["abs_cfo_hz"] for row in bpsk],
                                   [row["abs_cfo_hz"] for row in errors], BINS_CFO),
            "amplitude_codes": rate_for([row["amplitude_codes"] for row in bpsk],
                                        [row["amplitude_codes"] for row in errors],
                                        BINS_AMP),
        },
        "records": bpsk,
        "new_errors": errors,
        "fixed_errors": fixed,
    }
    return summary


def task80_evidence(original, candidate):
    eval_doc = load_json(TASK80_EVAL)
    meta = load_json(TASK80_BPSK.with_suffix(".json"))
    iq = np.frombuffer(TASK80_BPSK.with_suffix(".iq").read_bytes(),
                       dtype="<i2").reshape(-1, 2).copy()
    original_decision = original.decide(iq)
    candidate_decision = candidate.decide(iq)
    original_path = path_of(original, iq)
    candidate_path = path_of(candidate, iq)
    demod = (meta.get("demod_result") or {}).get("result") or {}
    ev = meta.get("tx_load_evidence") or {}
    worker_args = ev.get("worker_args") or {}
    return {
        "batch": "task80 new-seed independent diagnostic",
        "eval_summary": eval_doc.get("summary"),
        "eval_status": eval_doc.get("status"),
        "BPSK_frame": {
            "label": "02-bpsk-2",
            "iq_sha256": sha(TASK80_BPSK.with_suffix(".iq")),
            "truth": "BPSK",
            "tx_mode": worker_args.get("mode"),
            "tx_seed": worker_args.get("seed"),
            "tx_amplitude": worker_args.get("amplitude"),
            "demod_cfo_hz": demod.get("cfo_hz"),
            "demod_carrier_sync": demod.get("carrier_sync"),
            "demod_carrier_coherence": demod.get("carrier_coherence"),
            "environment": (meta.get("environment_result") or {}).get("label"),
            "original": {"label": original_decision.get("label"),
                         "reason": original_decision.get("reason"),
                         "scores": original_decision.get("scores"),
                         "path": original_path},
            "candidate_A": {"label": candidate_decision.get("label"),
                            "reason": candidate_decision.get("reason"),
                            "scores": candidate_decision.get("scores"),
                            "path": candidate_path},
            "note": ("current-condition BPSK is correct for A via arbitration; "
                     "CNN top1 is QPSK, baseline margin is high"),
        },
        "batch_scope_note": ("task80 is an independent diagnostic batch of 10 frames; "
                             "10/10 does not establish overall accuracy or release approval"),
    }


def main():
    original = Round2Predictor()
    candidate = Round2Predictor(rules_path=A_DIR / "rules.json")
    val = evaluate_split(original, candidate, "val")
    test = evaluate_split(original, candidate, "test")
    task80 = task80_evidence(original, candidate)
    a_metadata = load_json(A_DIR / "metadata.json")
    model = load_json(A_DIR / "model.json")
    rules = load_json(A_DIR / "rules.json")
    baseline = load_json(ROOT / "ml_training" / "artifacts" / "baseline" / "model.json")
    doc = {
        "schema": "ml-candidate-a-bpsk-regression-v1",
        "experiment": "candidate_a_bpsk_regression",
        "A_model": {
            "dir": str(A_DIR),
            "model_path": str(A_DIR / "model.json"),
            "model_sha256": sha(A_DIR / "model.json"),
            "rules_path": str(A_DIR / "rules.json"),
            "rules_sha256": sha(A_DIR / "rules.json"),
            "metadata_path": str(A_DIR / "metadata.json"),
            "metadata_sha256": sha(A_DIR / "metadata.json"),
            "baseline_path": str(ROOT / "ml_training" / "artifacts" / "baseline" / "model.json"),
            "baseline_sha256": sha(ROOT / "ml_training" / "artifacts" / "baseline" / "model.json"),
            "preprocessing": model.get("preprocessing"),
            "classes": model.get("classes"),
            "network": model.get("network"),
            "params": a_metadata.get("params"),
            "macs": a_metadata.get("macs"),
            "thresholds": model.get("thresholds"),
            "arbitration_threshold": rules.get("arbitration", {}).get("threshold"),
            "spectral_gate": rules.get("spectral_gate"),
            "augmentation": a_metadata.get("augmentation"),
            "best_epoch": a_metadata.get("best_epoch"),
            "best_val_macro_f1": a_metadata.get("best_val_macro_f1"),
            "training_note": "experimental candidate A; not approved/deployed",
        },
        "val": val,
        "known_test": test,
        "task80": task80,
        "data_boundaries": {
            "training": False, "device_access": False, "threshold_changes": False,
            "A_bytes_modified": False, "new_real_frames_collected": False,
            "sim_v1_test_used_as_known_comparison": True,
            "task80_used_as_independent_diagnostic_only": True,
            "task80_not_training": True,
            "no_input_domain_narrowing": True,
            "no_bad_example_deletion": True,
        },
        "missing_metadata": [
            "task80 BPSK has no measured SNR ground truth (not present; not fabricated)",
            "task80 has only one BPSK frame; no current-condition SNR/CFO distribution",
            "sim-v1 amplitude range is 150..1200; current amp60 is not represented in sim-v1",
        ],
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
    path = RESULTS / "bpsk_regression.json"
    path.write_bytes(raw)
    print(json.dumps({
        "out": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "val_new_errors": val["new_A_BPSK_errors"],
        "val_fixed": val["A_fixed_BPSK_errors"],
        "test_new_errors": test["new_A_BPSK_errors"],
        "test_fixed": test["A_fixed_BPSK_errors"],
        "val_error_final": val["new_error_final"],
        "test_error_final": test["new_error_final"],
        "val_stage": val["new_error_stage"],
        "test_stage": test["new_error_stage"],
        "task80_BPSK": task80["BPSK_frame"]["candidate_A"],
        "task80_BPSK_margin": task80["BPSK_frame"]["candidate_A"]["path"].get(
            "arbitration_margin_bpsk_minus_qpsk"),
        "missing_metadata": doc["missing_metadata"],
    }, indent=1))


if __name__ == "__main__":
    main()
