#!/usr/bin/env python3
"""Post-freeze comparison for the negative BPSK repair experiment.

No candidate R1 checkpoint was eligible, so this evaluates only frozen original and A.
The test split is shown as a known regression comparison, not a new independent
acceptance set; it was not used for training or checkpoint selection.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
DATASET = ROOT / "ml_training" / "datasets" / "sim-v1"
A_RULES = ROOT / "ml_training" / "qam16_domain_repair" / "results" / "candidate_A" / "rules.json"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split  # noqa: E402
from ml_training.round2.predictor import Round2Predictor  # noqa: E402


def evaluate(predictor, iq_list, labels):
    records = []
    for iq, label in zip(iq_list, labels):
        decision = predictor.decide(iq)
        pred = decision.get("label")
        records.append({"truth": str(label), "pred": pred,
                        "accepted": pred in CLASSES,
                        "reason": decision.get("reason"),
                        "scores": decision.get("scores")})
    per_class = {}
    for label in CLASSES:
        subset = [r for r in records if r["truth"] == label]
        correct = sum(1 for r in subset if r["pred"] == label)
        rejected = sum(1 for r in subset if r["pred"] == "UNKNOWN")
        accepted_wrong = sum(1 for r in subset
                             if r["pred"] in CLASSES and r["pred"] != label)
        per_class[label] = {
            "n": len(subset), "correct": correct,
            "recall": correct / max(len(subset), 1),
            "rejected": rejected,
            "rejection_rate": rejected / max(len(subset), 1),
            "accepted_wrong": accepted_wrong,
            "false_accept_rate": accepted_wrong / max(len(subset), 1),
            "accepted": sum(1 for r in subset if r["accepted"]),
        }
    f1s = []
    for label in CLASSES:
        tp = sum(1 for r in records if r["truth"] == label and r["pred"] == label)
        fp = sum(1 for r in records if r["truth"] != label and r["pred"] == label)
        fn = sum(1 for r in records if r["truth"] == label and r["pred"] != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall)
                   if precision + recall else 0.0)
    return {
        "n": len(records),
        "macro_f1_all": float(np.mean(f1s)),
        "accuracy_all": sum(1 for r in records if r["truth"] == r["pred"]) / max(len(records), 1),
        "accepted": sum(1 for r in records if r["accepted"]),
        "accept_rate": sum(1 for r in records if r["accepted"]) / max(len(records), 1),
        "rejected": sum(1 for r in records if r["pred"] == "UNKNOWN"),
        "per_class": per_class,
        "pred_counts": {name: sum(1 for r in records if r["pred"] == name)
                        for name in list(CLASSES) + ["UNKNOWN"]},
    }


def gate_off(predictor, manifest):
    data = load_split(DATASET, manifest, "gate_test", kinds=["OFF"],
                      include_clipped=True)
    accepted = sum(1 for iq in data["X"]
                   if predictor.decide(iq).get("label") in CLASSES)
    return {"n": len(data["X"]), "false_accept": accepted,
            "false_accept_rate": accepted / max(len(data["X"]), 1)}


def main():
    manifest = load_manifest(DATASET)
    val = load_split(DATASET, manifest, "val", labels=list(CLASSES))
    test = load_split(DATASET, manifest, "test", labels=list(CLASSES))
    original = Round2Predictor()
    candidate = Round2Predictor(rules_path=A_RULES)
    doc = {
        "schema": "ml-candidate-a-bpsk-repair-comparison-v1",
        "experiment": "candidate_a_bpsk_repair",
        "candidate_R1_status": {
            "status": "NEGATIVE_NO_ELIGIBLE_CHECKPOINT",
            "reason": "no epoch met the pre-registered VAL guard",
            "new_model_exported": False,
        },
        "decision_thresholds": "frozen, unchanged from round-2 rule config",
        "val": {
            "original": evaluate(original, val["X"], val["label"]),
            "A": evaluate(candidate, val["X"], val["label"]),
        },
        "known_test_comparison": {
            "label": "known regression comparison only; NOT independent acceptance",
            "used_for_training_or_selection": False,
            "original": evaluate(original, test["X"], test["label"]),
            "A": evaluate(candidate, test["X"], test["label"]),
        },
        "gate_off_false_accept": {
            "original": gate_off(original, manifest),
            "A": gate_off(candidate, manifest),
        },
        "boundaries": {
            "test_used_for_training_or_selection": False,
            "task80_used": False,
            "new_capture_used": False,
            "thresholds_changed": False,
            "A_overwritten": False,
            "deployed": False,
        },
    }
    path = BASE / "results" / "comparison_original_A.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(json.dumps({
        "out": str(path),
        "val_original": {k: doc["val"]["original"]["per_class"][k]["recall"]
                         for k in CLASSES},
        "val_A": {k: doc["val"]["A"]["per_class"][k]["recall"]
                  for k in CLASSES},
        "val_A_BPSK_rejection": doc["val"]["A"]["per_class"]["BPSK"]["rejection_rate"],
        "val_A_BPSK_false_accept": doc["val"]["A"]["per_class"]["BPSK"]["false_accept_rate"],
        "test_BPSK": {"original": doc["known_test_comparison"]["original"]["per_class"]["BPSK"],
                      "A": doc["known_test_comparison"]["A"]["per_class"]["BPSK"]},
        "candidate_R1_status": doc["candidate_R1_status"],
    }, indent=1))


if __name__ == "__main__":
    main()
