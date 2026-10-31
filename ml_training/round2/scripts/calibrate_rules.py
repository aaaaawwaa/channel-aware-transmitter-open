"""Calibrate the round-2 rule thresholds on fresh dev data (round-2 val + gate_cal).

Inputs (all read-only):
* frozen round-1 CNN and feature baseline,
* round-2 dataset ``sim-r2``: ``val`` (known, dev) and ``gate_cal`` (dev quality-gate
  calibration).  ``test`` and ``gate_test`` are NOT touched by this script.

Outputs:
* ``ml_training/round2/artifacts/rule_config.json``  (frozen runtime thresholds)
* ``ml_training/round2/analysis/calibration.json``   (full selection trace)

The arbitration threshold is selected by 4-class accuracy on ``val`` (dev).
The spectral-gate thresholds are selected on ``gate_cal`` + ``val`` with an explicit
cap on the **incremental** known-false-rejection relative to the frozen round-1 gate.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split  # noqa: E402
from ml_training.common.gate import top_scores  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.baseline import load_baseline  # noqa: E402
from ml_training.model.features import extract_features  # noqa: E402
from ml_training.model.numpy_runtime import load_model  # noqa: E402
from ml_training.round2.features import spectral_features  # noqa: E402

ML = ROOT / "ml_training"
DATASET = ML / "round2" / "datasets" / "sim-r2"
CNN_PATH = ML / "artifacts" / "cnn" / "model.json"
BASELINE_PATH = ML / "artifacts" / "baseline" / "model.json"
THRESHOLDS_PATH = ML / "artifacts" / "cnn" / "thresholds.json"
RULES_OUT = ML / "round2" / "artifacts" / "rule_config.json"
ANALYSIS_OUT = ML / "round2" / "analysis" / "calibration.json"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
GATE_KINDS = ("OFF", "NOISE", "TONE", "CLIP")
BPSK, QPSK = 0, 1


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def softmax(logits):
    logits = np.asarray(logits, dtype=np.float64)
    shifted = logits - logits.max()
    exp = np.exp(shifted)
    return exp / exp.sum()


def load_frames(split, labels=None, kinds=None, include_clipped=False):
    manifest = load_manifest(DATASET)
    data = load_split(DATASET, manifest, split, labels=labels, kinds=kinds,
                      include_clipped=include_clipped)
    return data


def frame_state(cnn, baseline, iq):
    x, gate = preprocess_iq(iq)
    if not gate["ok"]:
        return {"ok": False, "gate": gate, "cnn": None, "baseline": None, "spectral": None}
    probs = softmax(cnn.forward(x))
    return {"ok": True, "gate": gate, "cnn": probs,
            "baseline": baseline.scores(extract_features(x)),
            "spectral": spectral_features(x)}


def frozen_gate_accepts(state, frozen):
    if not state["ok"]:
        return False
    if state["gate"]["rms"] < frozen["min_complex_rms"]:
        return False
    index, score, second = top_scores(state["cnn"])
    if score < frozen["max_softmax"]:
        return False
    if score - second < frozen["margin"]:
        return False
    return True


def arbitrate(cnn_probs, baseline_probs, threshold, variant):
    cnn_label = int(np.argmax(cnn_probs))
    baseline_label = int(np.argmax(baseline_probs))
    margin = float(baseline_probs[BPSK] - baseline_probs[QPSK])
    if cnn_label not in (BPSK, QPSK):
        return cnn_label
    if variant == "v2" and baseline_label not in (BPSK, QPSK):
        return cnn_label
    if abs(margin) > threshold:
        return BPSK if margin > 0 else QPSK
    return cnn_label


def main():
    frozen_doc = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    frozen_gate = dict(frozen_doc["selected"])
    cnn = load_model(CNN_PATH)
    baseline = load_baseline(BASELINE_PATH)
    val = load_frames("val", labels=list(CLASSES))
    gate = load_frames("gate_cal", kinds=list(GATE_KINDS), include_clipped=True)
    truth = np.asarray([CLASSES.index(str(label)) for label in val["label"]])
    val_states = [frame_state(cnn, baseline, iq) for iq in val["X"]]
    gate_states = [frame_state(cnn, baseline, iq) for iq in gate["X"]]
    gate_kind = np.asarray([str(kind) for kind in gate["label"]])
    before_accept = np.asarray([frozen_gate_accepts(state, frozen_gate) for state in val_states])

    # ---- A. arbitration threshold on val (4-class accuracy, gate ignored) ----
    plain_pred = np.asarray([int(np.argmax(state["cnn"])) for state in val_states])
    plain_accuracy = float(np.mean(plain_pred == truth))
    grid = [round(0.05 * index, 2) for index in range(0, 19)]
    variants = {}
    for variant in ("v1", "v2"):
        rows = []
        for threshold in grid:
            predicted = np.asarray([arbitrate(state["cnn"], state["baseline"], threshold, variant)
                                    for state in val_states])
            accuracy = float(np.mean(predicted == truth))
            changed_to_correct = int(np.sum((predicted != plain_pred) & (predicted == truth)))
            changed_from_correct = int(np.sum((predicted != plain_pred) & (plain_pred == truth)))
            rows.append({"threshold": threshold, "val_accuracy": accuracy,
                         "changed_to_correct": changed_to_correct,
                         "changed_from_correct": changed_from_correct})
        best = max(rows, key=lambda row: (row["val_accuracy"], row["threshold"]))
        variants[variant] = {"rows": rows, "selected": best}
    best_variant = max(("v1", "v2"),
                       key=lambda name: (variants[name]["selected"]["val_accuracy"],
                                         variants[name]["selected"]["threshold"]))
    selected_threshold = variants[best_variant]["selected"]["threshold"]
    arbitrated_pred = np.asarray([arbitrate(state["cnn"], state["baseline"],
                                            selected_threshold, best_variant)
                                  for state in val_states])
    per_class = {}
    for index, label in enumerate(CLASSES):
        mask = truth == index
        per_class[label] = {
            "n": int(mask.sum()),
            "plain_recall": float(np.mean(plain_pred[mask] == truth[mask])),
            "arbitrated_recall": float(np.mean(arbitrated_pred[mask] == truth[mask])),
        }
    before_accept_accuracy = float(np.mean(plain_pred[before_accept] == truth[before_accept]))

    # ---- B. spectral gate calibration on gate_cal + val ----
    gate_spectral = {
        kind: [state["spectral"] for state, label in zip(gate_states, gate_kind)
               if label == kind and state["spectral"] is not None]
        for kind in GATE_KINDS}
    val_spectral = [state["spectral"] for state in val_states if state["spectral"] is not None]
    tone_values = np.asarray([frame["top5_frac"] for frame in gate_spectral["TONE"]])
    noise_values = np.asarray([frame["band_frac_3m"] for frame in gate_spectral["NOISE"]])
    known_top5 = np.asarray([frame["top5_frac"] for frame in val_spectral])
    known_band = np.asarray([frame["band_frac_3m"] for frame in val_spectral])
    # tone rule: top5_frac > threshold; want tone rejection 1.0 with zero incremental FR
    max_known_top5 = float(known_top5.max())
    min_tone_top5 = float(tone_values.min())
    if min_tone_top5 > max_known_top5:
        tone_threshold = 0.5 * (max_known_top5 + min_tone_top5)
        tone_selection = {"method": "midpoint between max known and min tone",
                          "max_known_valid": max_known_top5,
                          "min_tone_valid": min_tone_top5,
                          "known_false_rejection": 0.0,
                          "tone_rejection": 1.0}
    else:
        candidates = []
        for threshold in np.unique(np.round(np.append(known_top5, tone_values), 6)):
            known_rej = float(np.mean(known_top5 > threshold))
            tone_rej = float(np.mean(tone_values > threshold))
            if known_rej <= 0.005:
                candidates.append((tone_rej, -known_rej, threshold))
        tone_rej, neg_known, tone_threshold = max(candidates)
        tone_selection = {"method": "grid with known FR <= 0.5%",
                          "known_false_rejection": float(-neg_known),
                          "tone_rejection": float(tone_rej)}
    # noise rule: band_frac_3m < threshold; maximise noise rejection subject to
    # *incremental* known false rejection <= 1% on val (frames the frozen gate accepted)
    incremental_candidates = []
    for threshold in np.unique(np.round(np.percentile(known_band, np.arange(0, 60, 0.5)), 6)):
        newly_rejected = float(np.mean((known_band < threshold) & before_accept))
        noise_rej = float(np.mean(noise_values < threshold))
        incremental_candidates.append({"threshold": float(threshold),
                                       "noise_rejection": noise_rej,
                                       "incremental_known_false_rejection": newly_rejected})
    feasible = [row for row in incremental_candidates
                if row["incremental_known_false_rejection"] <= 0.01]
    if feasible:
        noise_selection = max(feasible, key=lambda row: row["noise_rejection"])
    else:
        noise_selection = min(incremental_candidates,
                              key=lambda row: row["incremental_known_false_rejection"])
    noise_threshold = noise_selection["threshold"]

    # trade-off table for the report
    tradeoff = []
    for target in (0.005, 0.01, 0.02):
        feasible_target = [row for row in incremental_candidates
                           if row["incremental_known_false_rejection"] <= target]
        if feasible_target:
            best = max(feasible_target, key=lambda row: row["noise_rejection"])
            tradeoff.append({"target": target, **best})

    # ---- combined gate behaviour on dev data ----
    def spectral_reject(state):
        if not state["ok"]:
            return False
        values = state["spectral"]
        if values["top5_frac"] > tone_threshold:
            return True
        if values["band_frac_3m"] < noise_threshold:
            return True
        return False

    val_after_accept = np.asarray([
        before_accept[index] and not spectral_reject(val_states[index])
        for index in range(len(val_states))])
    val_incremental_rejected = int(np.sum(before_accept & ~val_after_accept))

    def rejection_summary(states):
        summary = {}
        for kind in GATE_KINDS:
            indices = [index for index, label in enumerate(gate_kind) if label == kind]
            before = sum(1 for index in indices
                         if not frozen_gate_accepts(states[index], frozen_gate))
            after = sum(1 for index in indices
                        if (not frozen_gate_accepts(states[index], frozen_gate))
                        or spectral_reject(states[index]))
            summary[kind] = {"n": len(indices), "before_rejected": before,
                             "after_rejected": after}
        return summary

    gate_summary = rejection_summary(gate_states)

    document = {
        "schema": "ml-modrec-round2-rule-config-v1",
        "created_utc": utc_now(),
        "purpose": ("round-2 offline improvement: frozen round-1 CNN + interpretable feature "
                    "arbitration for BPSK/QPSK + interpretable spectral rejection rules"),
        "frozen_round1": {
            "cnn": {"path": "ml_training/artifacts/cnn/model.json", "sha256": sha256(CNN_PATH)},
            "baseline": {"path": "ml_training/artifacts/baseline/model.json",
                         "sha256": sha256(BASELINE_PATH)},
            "gate": {"max_softmax": float(frozen_gate["max_softmax"]),
                     "margin": float(frozen_gate["margin"]),
                     "min_complex_rms": float(frozen_gate["min_complex_rms"])},
            "thresholds_json_sha256": sha256(THRESHOLDS_PATH),
        },
        "arbitration": {
            "variant": best_variant,
            "threshold": float(selected_threshold),
            "classes": ["BPSK", "QPSK"],
            "description": ("when the frozen CNN top-1 is BPSK or QPSK, the frozen 36-parameter "
                            "interpretable baseline arbitrates between them if "
                            "|p_BPSK - p_QPSK| > threshold"),
            "calibration": {
                "split": "sim-r2/val (dev, new seed families)",
                "plain_val_accuracy": plain_accuracy,
                "arbitrated_val_accuracy": float(np.mean(arbitrated_pred == truth)),
                "changed_to_correct": variants[best_variant]["selected"]["changed_to_correct"],
                "changed_from_correct": variants[best_variant]["selected"]["changed_from_correct"],
                "per_class_recall": per_class,
                "grid": variants[best_variant]["rows"],
                "variant_comparison": {name: data["selected"] for name, data in variants.items()},
            },
        },
        "spectral_gate": {
            "tone": {"feature": "top5_frac", "op": ">", "threshold": float(tone_threshold),
                     "selection": tone_selection},
            "noise": {"feature": "band_frac_3m", "op": "<", "threshold": float(noise_threshold),
                      "selection": {**noise_selection,
                                    "incremental_known_false_rejection_cap": 0.01}},
            "feature_definitions": {
                "top5_frac": "fraction of FFT power in the five strongest bins",
                "band_frac_3m": "fraction of FFT power inside |f| <= 3 MHz",
            },
            "calibration": {
                "split": "sim-r2/gate_cal + sim-r2/val (dev)",
                "known_frames": int(len(val_states)),
                "before_gate_accepted": int(before_accept.sum()),
                "before_gate_known_false_rejection": 1.0 - float(before_accept.mean()),
                "after_gate_accepted": int(val_after_accept.sum()),
                "after_gate_known_false_rejection": 1.0 - float(val_after_accept.mean()),
                "incremental_known_false_rejection": val_incremental_rejected / len(val_states),
                "incremental_known_false_rejection_count": val_incremental_rejected,
                "gate_cal_rejection_before_after": gate_summary,
                "noise_rejection_tradeoff": tradeoff,
                "tone_rejection_on_gate_cal": 1.0,
                "noise_rejection_on_gate_cal": noise_selection["noise_rejection"],
            },
        },
        "dataset": {"path": "ml_training/round2/datasets/sim-r2",
                    "manifest_sha256": sha256(DATASET / "manifest.json"),
                    "config_sha256": sha256(ML / "round2" / "config" / "round2.json"),
                    "data_use": "test/gate_test not used by this calibration"},
        "notes": {"no_retraining": True, "parameter_count_added": 0,
                  "round1_artifacts_modified": False},
    }
    RULES_OUT.parent.mkdir(parents=True, exist_ok=True)
    RULES_OUT.write_text(json.dumps(document, indent=1), encoding="utf-8")
    ANALYSIS_OUT.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_OUT.write_text(json.dumps(document, indent=1), encoding="utf-8")

    print("=== arbitration ===")
    print("plain val accuracy %.4f -> arbitrated %.4f (variant %s, threshold %.2f, +%d/-%d)" % (
        plain_accuracy, document["arbitration"]["calibration"]["arbitrated_val_accuracy"],
        best_variant, selected_threshold,
        variants[best_variant]["selected"]["changed_to_correct"],
        variants[best_variant]["selected"]["changed_from_correct"]))
    print("per-class recall:", json.dumps(per_class, indent=1))
    print("=== spectral gate ===")
    print("tone threshold %.6f (selection %s)" % (tone_threshold, tone_selection))
    print("noise threshold %.6f (selection %s)" % (noise_threshold, noise_selection))
    print("trade-off:", json.dumps(tradeoff, indent=1))
    print("gate_cal before/after:", json.dumps(gate_summary, indent=1))
    print("val known false rejection: before %.4f after %.4f (incremental %d frames)" % (
        document["spectral_gate"]["calibration"]["before_gate_known_false_rejection"],
        document["spectral_gate"]["calibration"]["after_gate_known_false_rejection"],
        val_incremental_rejected))
    print("wrote", RULES_OUT)


if __name__ == "__main__":
    main()
