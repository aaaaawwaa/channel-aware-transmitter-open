#!/usr/bin/env python3
"""Build a small fixed replay package for candidate A engineering handoff.

Uses only existing frames.  Synthetic frames come from sim-v1 VAL/known test; the
real frame is task80 OFF-last.  No training, no new simulation, no threshold change,
no modification of A bytes.  Errors are retained exactly as observed.

Package purpose: host/ARM computation consistency for original vs A; not performance
acceptance and not a request to turn every known negative into a correct prediction.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
PACKAGE = BASE
FRAMES = PACKAGE / "frames"
SIM = ROOT / "ml_training" / "datasets" / "sim-v1"
A_DIR = ROOT / "ml_training" / "qam16_domain_repair" / "results" / "candidate_A"
ORIGINAL_MODEL = ROOT / "ml_training" / "artifacts" / "cnn" / "model.json"
BASELINE_MODEL = ROOT / "ml_training" / "artifacts" / "baseline" / "model.json"
TASK80 = (ROOT / "evidence" / "rf-teaching" /
          "s2-46db-fourclass-seed-validation-2026-10-22")
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split  # noqa: E402
from ml_training.common.gate import top_scores  # noqa: E402
from ml_training.round2.predictor import Round2Predictor  # noqa: E402


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    return sha_bytes(Path(path).read_bytes())


def jwrite(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def path_of(predictor, iq):
    probs, x, gate = predictor.cnn_probabilities(iq)
    if probs is None:
        return {"stage": "preprocess", "reason": gate.get("reason"), "gate": gate}
    if gate["rms"] < predictor.gate["min_complex_rms"]:
        return {"stage": "energy_floor", "reason": "below_energy_floor",
                "gate": gate, "scores": probs.tolist()}
    spectral_reason, spectral_value, _ = predictor._spectral_rule(x)
    if spectral_reason:
        return {"stage": "spectral", "reason": spectral_reason, "gate": gate,
                "scores": probs.tolist(), "spectral_value": spectral_value}
    index, score, second = top_scores(probs)
    margin = float(score - second)
    if score < predictor.gate["max_softmax"]:
        return {"stage": "confidence", "reason": "confidence", "gate": gate,
                "scores": probs.tolist(), "score": float(score), "margin": margin}
    if margin < predictor.gate["margin"]:
        return {"stage": "margin", "reason": "margin", "gate": gate,
                "scores": probs.tolist(), "score": float(score), "margin": margin}
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


def output_of(predictor, iq):
    decision = predictor.decide(iq)
    return {
        "label": decision.get("label"),
        "reason": decision.get("reason"),
        "accepted": decision.get("label") in CLASSES,
        "scores": decision.get("scores"),
        "path": path_of(predictor, iq),
    }


def write_synthetic_frame(frame_id, category, split, index, data, original, candidate):
    iq = np.asarray(data["X"][index], dtype="<i2")
    raw = iq.tobytes()
    digest = sha_bytes(raw)
    label = str(data["label"][index])
    condition = {
        "snr_db": float(data["snr_db"][index]),
        "cfo_hz": float(data["cfo_hz"][index]),
        "amplitude_codes": int(data["amplitude_codes"][index]),
        "window_offset": int(data["window_offset"][index]),
        "phase_rad": float(data["phase_rad"][index]),
        "session_id": str(data["session_id"][index]),
        "seed_family": str(data["seed_family"][index]),
    }
    frame_dir = FRAMES / frame_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    (frame_dir / "frame.iq").write_bytes(raw)
    document = {
        "package": "candidate_a_replay_package",
        "frame_id": frame_id,
        "category": category,
        "synthetic": True,
        "real_capture": False,
        "source": {"dataset": "sim-v1", "split": split, "index": int(index),
                   "session_id": condition["session_id"],
                   "seed_family": condition["seed_family"]},
        "conditions": condition,
        "truth_class": label,
        "iq_sha256": digest,
        "iq_bytes": len(raw),
        "iq_file": "frame.iq",
        "original": output_of(original, iq),
        "A": output_of(candidate, iq),
        "error_outputs_preserved": True,
        "note": "existing synthetic frame; not generated here; not performance acceptance",
    }
    jwrite(frame_dir / "frame.json", document)
    return document


def write_real_task80_off(original, candidate):
    label = "10-off-last"
    frame_dir_src = TASK80 / "raw" / label
    iq_files = sorted(frame_dir_src.glob("*.iq"))
    if len(iq_files) != 1:
        raise RuntimeError("expected exactly one .iq in %s" % frame_dir_src)
    iq_path = iq_files[0]
    meta_path = iq_path.with_suffix(".json")
    plan = json.loads((TASK80 / "plan.json").read_text(encoding="utf-8"))
    plan_row = next(row for row in plan["order"] if row["label"] == label)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    raw = iq_path.read_bytes()
    digest = sha_bytes(raw)
    iq = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).copy()
    demod = (meta.get("demod_result") or {}).get("result") or {}
    off_tx = meta.get("off_tx") or {}
    capture_tx = meta.get("capture_tx_state") or {}
    off_ok = off_tx.get("ok", capture_tx.get("off_tx_ok"))
    pre_sel = off_tx.get("pre_tx_source_selected",
                         capture_tx.get("pre_tx_source_selected",
                                       meta.get("pre_tx_source_selected")))
    post_sel = off_tx.get("post_tx_source_selected",
                          capture_tx.get("post_tx_source_selected",
                                        meta.get("post_tx_source_selected")))
    frame_id = "06_real_task80_off_last"
    frame_dir = FRAMES / frame_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    (frame_dir / "frame.iq").write_bytes(raw)
    document = {
        "package": "candidate_a_replay_package",
        "frame_id": frame_id,
        "category": "task80_OFF_last",
        "synthetic": False,
        "real_capture": True,
        "source": {"real_path": str(iq_path.relative_to(ROOT)).replace("\\", "/"),
                   "metadata_path": str(meta_path.relative_to(ROOT)).replace("\\", "/"),
                   "label": label,
                   "capture_id": meta.get("capture_id"),
                   "request_id": meta.get("request_id")},
        "conditions": {
            "tx_amplitude": 60,
            "off_tx_ok": off_ok,
            "pre_tx_source_selected": pre_sel,
            "post_tx_source_selected": post_sel,
            "demod_cfo_hz": demod.get("cfo_hz"),
            "demod_carrier_sync": demod.get("carrier_sync"),
            "demod_carrier_coherence": demod.get("carrier_coherence"),
            "environment": (meta.get("environment_result") or {}).get("label"),
            "environment_reason": (meta.get("environment_result") or {}).get("reason"),
            "snr_db": None,
            "missing_fields": ["snr_db"],
        },
        "truth_class": plan_row["truth_class"],
        "iq_sha256": digest,
        "iq_bytes": len(raw),
        "iq_file": "frame.iq",
        "original": output_of(original, iq),
        "A": output_of(candidate, iq),
        "error_outputs_preserved": True,
        "note": "existing task80 real OFF frame; independent diagnostic, not training/acceptance",
    }
    jwrite(frame_dir / "frame.json", document)
    return document


def task80_bpsk_correction():
    iq_path = TASK80 / "raw" / "02-bpsk-2" / "s00-on-00.iq"
    raw = iq_path.read_bytes()
    iq = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.float64)
    centered = iq - iq.mean(axis=0)
    rms = float(np.sqrt(np.mean(np.sum(centered ** 2, axis=1))))
    return {
        "tx_amplitude_code": 60,
        "note": "TX amp60 is a transmit amplitude code, NOT receive IQ amplitude",
        "actual_adc_peak": int(np.abs(iq).max()),
        "dc_removed_complex_rms": rms,
        "source": str(iq_path.relative_to(ROOT)).replace("\\", "/"),
        "iq_sha256": sha_bytes(raw),
    }


def main():
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(SIM)
    val = load_split(SIM, manifest, "val", labels=list(CLASSES))
    test = load_split(SIM, manifest, "test", labels=list(CLASSES))
    original = Round2Predictor()
    candidate = Round2Predictor(rules_path=A_DIR / "rules.json")
    frames = [
        write_synthetic_frame("01_syn_val_BPSK_to_QPSK", "A_BPSK_to_QPSK",
                              "val", 24, val, original, candidate),
        write_synthetic_frame("02_syn_test_BPSK_to_QAM64", "A_BPSK_to_QAM64",
                              "test", 439, test, original, candidate),
        write_synthetic_frame("03_syn_val_BPSK_confidence", "A_confidence_reject",
                              "val", 158, val, original, candidate),
        write_synthetic_frame("04_syn_test_high_snr_BPSK", "high_snr_correct_BPSK",
                              "test", 500, test, original, candidate),
        write_synthetic_frame("05_syn_test_correct_QPSK", "correct_QPSK",
                              "test", 1100, test, original, candidate),
        write_real_task80_off(original, candidate),
    ]
    correction = task80_bpsk_correction()
    package = {
        "schema": "ml-candidate-a-replay-package-v1",
        "package_id": "candidate_a_replay_package",
        "purpose": ("host/ARM computation-consistency replay for original vs candidate A; "
                    "NOT performance acceptance"),
        "frame_count": len(frames),
        "synthetic_frames": sum(1 for frame in frames if frame["synthetic"]),
        "real_frames": sum(1 for frame in frames if frame["real_capture"]),
        "frames": frames,
        "models": {
            "original": {
                "model_path": str(ORIGINAL_MODEL.relative_to(ROOT)).replace("\\", "/"),
                "model_sha256": sha_file(ORIGINAL_MODEL),
                "rules_path": "ml_training/round2/artifacts/rule_config.json",
                "rules_sha256": sha_file(ROOT / "ml_training" / "round2" / "artifacts" /
                                         "rule_config.json"),
            },
            "A": {
                "model_path": str((A_DIR / "model.json").relative_to(ROOT)).replace("\\", "/"),
                "model_sha256": sha_file(A_DIR / "model.json"),
                "rules_path": str((A_DIR / "rules.json").relative_to(ROOT)).replace("\\", "/"),
                "rules_sha256": sha_file(A_DIR / "rules.json"),
                "metadata_path": str((A_DIR / "metadata.json").relative_to(ROOT)).replace("\\", "/"),
                "metadata_sha256": sha_file(A_DIR / "metadata.json"),
            },
            "baseline_path": str(BASELINE_MODEL.relative_to(ROOT)).replace("\\", "/"),
            "baseline_sha256": sha_file(BASELINE_MODEL),
            "preprocessing": "dc-complex-rms-v1",
            "class_order": list(CLASSES),
        },
        "corrections": {
            "tx_amplitude": correction,
            "high_snr_statistics": ("high-SNR stratified numbers are NEW A-vs-original "
                                    "error rates, NOT A's overall error rate"),
            "task80_scope": "task80 10 frames is independent diagnostic, not overall accuracy",
        },
        "boundaries": {
            "training": False,
            "new_simulation_generated": False,
            "A_bytes_modified": False,
            "thresholds_changed": False,
            "device_access": False,
            "performance_acceptance": False,
            "errors_preserved_unchanged": True,
        },
    }
    jwrite(PACKAGE / "manifest.json", package)
    print(json.dumps({
        "package": str(PACKAGE),
        "frames": len(frames),
        "synthetic": package["synthetic_frames"],
        "real": package["real_frames"],
        "categories": [frame["category"] for frame in frames],
        "correction": correction,
        "manifest_sha256": sha_file(PACKAGE / "manifest.json"),
    }, indent=1))


if __name__ == "__main__":
    main()
