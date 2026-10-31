#!/usr/bin/env python3
"""Evaluate C0/C1 against frozen baseline and A on the same VAL + final Round2 pipeline.

Candidate selection uses the pre-written selection_rule.json and VAL/gate_test only.
sim-v1/test is reported as a known comparison set, never used for selection.
The 14 real frames are not read in this script; they are handled by diagnose_dev.py
only after selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
DATASET = ROOT / "ml_training" / "datasets" / "sim-v1"
RULE_SOURCE = ROOT / "ml_training" / "round2" / "artifacts" / "rule_config.json"
PRIOR_A = ROOT / "ml_training" / "qam16_domain_repair" / "results" / "candidate_A"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
KINDS = ("OFF", "NOISE", "TONE", "CLIP")

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split, sha256_file  # noqa: E402
from ml_training.round2.predictor import Round2Predictor  # noqa: E402


def jwrite(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def evaluate_split(predictor, X, labels):
    truth = np.asarray([CLASSES.index(str(label)) for label in labels], dtype=np.int64)
    decisions = [predictor.decide(iq) for iq in X]
    preds = [d.get("label") for d in decisions]
    acc = sum(1 for i, pred in enumerate(preds) if pred == CLASSES[int(truth[i])])
    accepted = sum(1 for pred in preds if pred in CLASSES)
    correct_accepted = sum(1 for i, pred in enumerate(preds)
                           if pred == CLASSES[int(truth[i])])
    per_class = {}
    for index, label in enumerate(CLASSES):
        positions = [i for i in range(len(truth)) if truth[i] == index]
        n = len(positions)
        correct = sum(1 for i in positions if preds[i] == label)
        accepted_class = sum(1 for i in positions if preds[i] in CLASSES)
        correct_acc_class = sum(1 for i in positions if preds[i] == label)
        per_class[label] = {
            "n": n,
            "recall": correct / max(n, 1),
            "accepted_rate": accepted_class / max(n, 1),
            "accepted_accuracy": correct_acc_class / max(accepted_class, 1),
        }
    f1s = []
    for index, label in enumerate(CLASSES):
        tp = sum(1 for i, pred in enumerate(preds)
                 if truth[i] == index and pred == label)
        fp = sum(1 for i, pred in enumerate(preds)
                 if truth[i] != index and pred == label)
        fn = sum(1 for i, pred in enumerate(preds)
                 if truth[i] == index and pred != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1s.append(2 * precision * recall / (precision + recall)
                   if (precision + recall) else 0.0)
    return {
        "n": len(X),
        "accuracy": acc / max(len(X), 1),
        "macro_f1": float(np.mean(f1s)),
        "accepted": accepted,
        "accept_rate": accepted / max(len(X), 1),
        "accepted_accuracy": correct_accepted / max(accepted, 1),
        "per_class": per_class,
    }


def evaluate_gate(predictor, manifest):
    kinds = {}
    for kind in KINDS:
        data = load_split(DATASET, manifest, "gate_test", kinds=[kind],
                          include_clipped=True)
        rejected = 0
        reasons = {}
        for iq in data["X"]:
            decision = predictor.decide(iq)
            if decision.get("label") == "UNKNOWN":
                rejected += 1
                reason = decision.get("reason") or "unknown"
                reasons[reason] = reasons.get(reason, 0) + 1
        kinds[kind] = {
            "n": len(data["X"]),
            "rejected": rejected,
            "rejection_rate": rejected / max(len(data["X"]), 1),
            "reasons": reasons,
        }
    return kinds


def load_predictors():
    predictors = {"frozen_original": Round2Predictor()}
    if (PRIOR_A / "rules.json").is_file():
        predictors["A_experimental_baseline"] = Round2Predictor(
            rules_path=PRIOR_A / "rules.json")
    for name in ("C0", "C1"):
        path = RESULTS / name / "rules.json"
        if not path.is_file():
            raise SystemExit("missing candidate rules: " + str(path))
        predictors[name] = Round2Predictor(rules_path=path)
    return predictors


def apply_selection_rule(rule_doc, baseline, candidates):
    eligibility = rule_doc["eligibility"]
    accepted = []
    diagnostics = {}
    for name, metrics in candidates.items():
        checks = {}
        checks["gate_quality"] = (
            metrics["gate_test"]["OFF"]["rejection_rate"] >=
            baseline["gate_test"]["OFF"]["rejection_rate"] - 0.01 and
            metrics["gate_test"]["NOISE"]["rejection_rate"] >=
            baseline["gate_test"]["NOISE"]["rejection_rate"] - 0.01 and
            metrics["gate_test"]["TONE"]["rejection_rate"] >=
            baseline["gate_test"]["TONE"]["rejection_rate"] - 0.01 and
            metrics["gate_test"]["CLIP"]["rejection_rate"] >=
            baseline["gate_test"]["CLIP"]["rejection_rate"] - 0.01 and
            metrics["val"]["accepted_accuracy"] >=
            baseline["val"]["accepted_accuracy"] - 0.01)
        checks["bpsk_preservation"] = (
            metrics["val"]["per_class"]["BPSK"]["recall"] >=
            baseline["val"]["per_class"]["BPSK"]["recall"] - 0.01)
        checks["qam16_improvement"] = (
            metrics["val"]["per_class"]["QAM16"]["recall"] >=
            baseline["val"]["per_class"]["QAM16"]["recall"] + 0.02)
        checks["other_class_safety"] = (
            metrics["val"]["per_class"]["QPSK"]["recall"] >=
            baseline["val"]["per_class"]["QPSK"]["recall"] - 0.02 and
            metrics["val"]["per_class"]["QAM64"]["recall"] >=
            baseline["val"]["per_class"]["QAM64"]["recall"] - 0.02)
        checks["eligible"] = all(checks.values())
        if checks["eligible"]:
            accepted.append(name)
        diagnostics[name] = checks
    if not accepted:
        return {"selected": None, "status": "NO_QUALIFIED_CANDIDATE",
                "checks": diagnostics}
    accepted.sort(key=lambda name: (
        candidates[name]["val"]["macro_f1"],
        candidates[name]["val"]["per_class"]["QAM16"]["recall"]), reverse=True)
    selected = accepted[0]
    return {"selected": selected, "status": "SELECTED",
            "checks": diagnostics, "ranking": accepted,
            "rule": rule_doc["selection"]["rule"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-rule", type=Path, default=BASE / "selection_rule.json")
    args = parser.parse_args()
    rule_doc = json.loads(Path(args.selection_rule).read_text(encoding="utf-8"))
    manifest = load_manifest(DATASET)
    val = load_split(DATASET, manifest, "val", labels=list(CLASSES))
    test = load_split(DATASET, manifest, "test", labels=list(CLASSES))
    predictors = load_predictors()
    metrics = {}
    model_paths = {
        "frozen_original": ROOT / "ml_training" / "artifacts" / "cnn" / "model.json",
        "A_experimental_baseline": PRIOR_A / "model.json",
        "C0": RESULTS / "C0" / "model.json",
        "C1": RESULTS / "C1" / "model.json",
    }
    for name, predictor in predictors.items():
        print("evaluating", name, flush=True)
        metrics[name] = {
            "val": evaluate_split(predictor, val["X"], val["label"]),
            "test_known_comparison_only": evaluate_split(predictor, test["X"],
                                                         test["label"]),
            "gate_test": evaluate_gate(predictor, manifest),
            "model_json_sha256": sha256_file(model_paths[name]),
        }
    # Stable artifact hashes without relying on private predictor attributes.
    for name in ("C0", "C1"):
        metrics[name]["candidate_model_json_sha256"] = sha256_file(
            RESULTS / name / "model.json")
        metrics[name]["candidate_rules_sha256"] = sha256_file(
            RESULTS / name / "rules.json")
    if "A_experimental_baseline" in metrics:
        metrics["A_experimental_baseline"]["candidate_model_json_sha256"] = sha256_file(
            PRIOR_A / "model.json")
        metrics["A_experimental_baseline"]["candidate_rules_sha256"] = sha256_file(
            PRIOR_A / "rules.json")
    baseline = {"val": metrics["frozen_original"]["val"],
                "gate_test": metrics["frozen_original"]["gate_test"]}
    candidates = {name: metrics[name] for name in ("C0", "C1")}
    selection = apply_selection_rule(rule_doc, baseline, candidates)
    output = {
        "schema": "ml-qam16-bpsk-control-selection-v1",
        "selection_rule_path": str(Path(args.selection_rule)),
        "selection_rule_sha256": sha256_file(args.selection_rule),
        "selection_split": "sim-v1/val",
        "selection_pipeline": "frozen Round2 decision pipeline with frozen thresholds",
        "test_known_comparison_only": True,
        "test_used_for_selection": False,
        "real_14_used_for_selection": False,
        "metrics": metrics,
        "selection": selection,
        "selected": selection.get("selected"),
    }
    sha = jwrite(RESULTS / "selection.json", output)
    print(json.dumps({
        "selection_sha256": sha,
        "selected": selection.get("selected"),
        "status": selection.get("status"),
        "checks": selection.get("checks"),
        "val": {name: {
            "macro_f1": metrics[name]["val"]["macro_f1"],
            "accuracy": metrics[name]["val"]["accuracy"],
            "BPSK": metrics[name]["val"]["per_class"]["BPSK"]["recall"],
            "QAM16": metrics[name]["val"]["per_class"]["QAM16"]["recall"],
            "QPSK": metrics[name]["val"]["per_class"]["QPSK"]["recall"],
            "QAM64": metrics[name]["val"]["per_class"]["QAM64"]["recall"],
        } for name in metrics},
    }, indent=1))


if __name__ == "__main__":
    main()
