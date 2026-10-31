#!/usr/bin/env python3
"""Summarize C0/C1 controlled experiment, selection result and post-selection dev diagnosis."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
PRIOR_A = ROOT / "ml_training" / "qam16_domain_repair" / "results" / "candidate_A"
TRAIN_SECONDS = {"C0": 250.6810175999999, "C1": 342.2512010999999}
BUDGET = 1800.0


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    selection = load(RESULTS / "selection.json")
    dev = load(RESULTS / "dev_diagnosis.json")
    metrics = selection["metrics"]
    comparison = {
        "schema": "ml-qam16-bpsk-control-comparison-v1",
        "experiment": "qam16_bpsk_control",
        "status": "TWO_CONTROLS_COMPLETED",
        "selection_rule": {
            "path": str(BASE / "selection_rule.json"),
            "sha256": sha(BASE / "selection_rule.json"),
            "written_before_training": True,
            "selection_split": "sim-v1/val",
            "test_or_real_used_for_selection": False,
        },
        "training_seconds": TRAIN_SECONDS,
        "training_seconds_total": sum(TRAIN_SECONDS.values()),
        "within_budget": sum(TRAIN_SECONDS.values()) <= BUDGET,
        "selection": selection["selection"],
        "selected": selection.get("selected"),
        "data_boundaries": {
            "same_total_frames": True,
            "same_per_class_frames": True,
            "same_seed_and_optimizer": True,
            "same_frozen_thresholds": True,
            "test_known_comparison_only": True,
            "real_14_dev_only": True,
            "real_used_for_selection": False,
        },
        "candidates": {},
        "per_class_val": {},
        "per_class_test_known": {},
        "dev_qam16": {},
        "gate_test": {},
        "verdict": {
            "selected": None,
            "status": "NO_QUALIFIED_CANDIDATE",
            "reason": ("Both C0 and C1 fail the pre-written BPSK preservation and/or other-class "
                       "safety checks on VAL with the final Round2 pipeline. C0 (no augmentation) "
                       "also regresses BPSK and QPSK, so the BPSK drop is not supported as solely "
                       "caused by conjugate augmentation. C1 does not preserve BPSK better than C0."),
            "A": "remains experimental baseline only; not deployed/frozen",
            "B": "rejected in previous experiment; not reused",
            "next_validation": ("Multi-seed C0/C1 (and a same-duration/no-duplication control) "
                                "to separate seed/training variance from augmentation; then a new "
                                "independent real acceptance set, not the 14 dev frames."),
        },
    }
    paths = {
        "frozen_original": ROOT / "ml_training" / "artifacts" / "cnn" / "model.json",
        "A_experimental_baseline": PRIOR_A / "model.json",
        "C0": RESULTS / "C0" / "model.json",
        "C1": RESULTS / "C1" / "model.json",
    }
    for name, data in metrics.items():
        comparison["candidates"][name] = {
            "model_json_sha256": sha(paths[name]),
            "val": data["val"],
            "test_known_comparison_only": data["test_known_comparison_only"],
            "gate_test": data["gate_test"],
        }
        comparison["per_class_val"][name] = {
            label: data["val"]["per_class"][label] for label in
            ("BPSK", "QPSK", "QAM16", "QAM64")}
        comparison["per_class_test_known"][name] = {
            label: data["test_known_comparison_only"]["per_class"][label] for label in
            ("BPSK", "QPSK", "QAM16", "QAM64")}
    for name in ("C0", "C1"):
        comparison["candidates"][name]["rules_sha256"] = sha(RESULTS / name / "rules.json")
        comparison["candidates"][name]["model_pt_sha256"] = sha(RESULTS / name / "model.pt")
        comparison["candidates"][name]["checkpoint_best_sha256"] = sha(
            RESULTS / name / "checkpoint_best.pt")
        comparison["candidates"][name]["checkpoint_last_sha256"] = sha(
            RESULTS / name / "checkpoint_last.pt")
        comparison["candidates"][name]["metadata_sha256"] = sha(
            RESULTS / name / "metadata.json")
        comparison["candidates"][name]["train_seconds"] = TRAIN_SECONDS[name]
    comparison["dev_qam16"] = {
        name: [
            {"frame": r["frame"], "truth": r["truth"],
             "pred": r[name]["pred"], "reason": r[name]["reason"],
             "qam16_score": (r[name]["scores"] or [None] * 4)[2],
             "qam64_score": (r[name]["scores"] or [None] * 4)[3]}
            for r in dev["frames"] if r["truth"] == "QAM16"
        ] for name in ("frozen_original", "A_experimental_baseline", "C0", "C1")
    }
    comparison["gate_test"] = {
        name: {kind: data["gate_test"][kind]["rejection_rate"]
               for kind in ("OFF", "NOISE", "TONE", "CLIP")}
        for name, data in metrics.items()
    }
    comparison["hashes"] = {
        "selection_json": sha(RESULTS / "selection.json"),
        "dev_diagnosis_json": sha(RESULTS / "dev_diagnosis.json"),
        "selection_rule": sha(BASE / "selection_rule.json"),
    }
    raw = json.dumps(comparison, indent=2, ensure_ascii=False).encode("utf-8")
    path = RESULTS / "comparison.json"
    path.write_bytes(raw)
    print(json.dumps({
        "comparison": str(path),
        "comparison_sha256": hashlib.sha256(raw).hexdigest(),
        "selected": comparison["selected"],
        "training_seconds": TRAIN_SECONDS,
        "total_seconds": sum(TRAIN_SECONDS.values()),
        "verdict": comparison["verdict"]["status"],
    }, indent=1))


if __name__ == "__main__":
    main()
