#!/usr/bin/env python3
"""Summarize baseline/A/B evidence, regressions, hashes and training budget."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
DATASET = ROOT / "ml_training" / "datasets" / "sim-v1"

# Observed from the two training logs (A: initial 279.2646 s + resume/export 4.0089 s;
# B: 326.0808 s).  Kept separate from candidate metadata because A was re-exported
# from its checkpoint without retraining from scratch.
TRAIN_SECONDS = {"candidate_A": 279.26464419999684 + 4.008883299997251,
                 "candidate_B": 326.0807980000027}
TOTAL_BUDGET_SECONDS = 1800.0


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    baseline = load(RESULTS / "baseline_frozen_eval.json")
    candidates = {name: load(RESULTS / (name + "_eval.json"))
                  for name in ("candidate_A", "candidate_B")}
    manifest = load(DATASET / "manifest.json")
    configs = {name: load(RESULTS / name / "metadata.json")
               for name in ("candidate_A", "candidate_B")}
    comparison = {
        "schema": "ml-qam16-domain-repair-comparison-v1",
        "experiment": "qam16_domain_repair",
        "status": "TWO_CANDIDATES_COMPLETED",
        "budget_seconds_total": TOTAL_BUDGET_SECONDS,
        "training_seconds_observed": TRAIN_SECONDS,
        "training_seconds_total_observed": sum(TRAIN_SECONDS.values()),
        "within_budget": sum(TRAIN_SECONDS.values()) <= TOTAL_BUDGET_SECONDS,
        "data": {
            "synthetic_test": "sim-v1/test",
            "synthetic_test_seed_families": manifest["splits"]["test"]["seed_families"],
            "synthetic_gate_test_seed_families":
                manifest["splits"]["gate_test"]["seed_families"],
            "real_dev": "14 real frames (development diagnosis only; not acceptance)",
            "real_data_used_for_training": False,
            "conjugate_test": "diagnostic transform of sim-v1/test; not a new dataset",
        },
        "candidates": {},
        "baseline": {
            "eval_sha256": sha(RESULTS / "baseline_frozen_eval.json"),
            "normal": baseline["synthetic_normal"],
            "conjugate_diagnostic": baseline["synthetic_conjugate_diagnostic"],
            "gate_test": baseline["gate_test"],
            "real_dev": baseline["real_dev"],
        },
        "per_class_normal": {},
        "per_class_conjugate_diagnostic": {},
        "real_qam16": {},
        "gate_test": {},
        "verdict": {
            "candidate_A": {
                "status": "CONTINUE_FORMAL_TRAINING_BUT_DO_NOT_FREEZE_YET",
                "reason": ("Fixes all 3 real dev QAM16 (0/3 -> 3/3), improves independent "
                           "synthetic normal macro-F1 0.8669 -> 0.8988 and QAM16 recall "
                           "0.765 -> 0.857, keeps real BPSK/QPSK/QAM64/OFF and all gate_test "
                           "rejection unchanged; but synthetic BPSK recall regresses "
                           "0.930 -> 0.880, so not safe to freeze without a BPSK-preserving "
                           "formal control."),
            },
            "candidate_B": {
                "status": "REJECT",
                "reason": ("A plus IQ gain imbalance improves some synthetic classes but "
                           "real dev QAM16 falls to 1/3 and synthetic QAM16 recall is "
                           "0.755; does not beat A on the target. Do not continue stacking "
                           "augmentations."),
            },
            "next_hypothesis": ("A's BPSK regression is caused by applying conjugate "
                                "augmentation to BPSK equally; a class-balanced/"
                                "QAM-only conjugate schedule should preserve BPSK while "
                                "keeping A's QAM16 gain. This is a hypothesis for a future "
                                "authorized formal run, not another candidate here."),
        },
    }
    for name, doc in candidates.items():
        cfg = configs[name]
        comparison["candidates"][name] = {
            "eval_sha256": sha(RESULTS / (name + "_eval.json")),
            "config": {
                "candidate": cfg["candidate"],
                "augmentation": cfg["augmentation"],
                "augmentation_scope": cfg["augmentation_scope"],
                "architecture": cfg["architecture"],
                "preprocessing": cfg["preprocessing"],
                "seed": cfg["seed"],
                "settings": cfg["settings"],
                "thresholds": cfg["thresholds"],
                "thresholds_recalibrated": cfg["thresholds_recalibrated"],
                "best_epoch": cfg["best_epoch"],
                "best_val_macro_f1": cfg["best_val_macro_f1"],
                "epochs_run": cfg["epochs_run"],
                "params": cfg["params"],
                "macs": cfg["macs"],
                "real_data_used_for_training": cfg["real_data_used_for_training"],
                "dev_real_frames_used_for_training": cfg["dev_real_frames_used_for_training"],
                "resume_command": cfg["resume_command"],
            },
            "hashes": {
                "model_json": cfg["artefacts"]["model_json_sha256"],
                "model_pt": cfg["artefacts"]["model_pt_sha256"],
                "rules": cfg["artefacts"]["rules_sha256"],
                "checkpoint_best": cfg["artefacts"]["checkpoint_best_sha256"],
                "checkpoint_last": cfg["artefacts"]["checkpoint_last_sha256"],
            },
            "training_seconds_observed": TRAIN_SECONDS[name],
            "normal": candidates[name]["synthetic_normal"],
            "conjugate_diagnostic": candidates[name]["synthetic_conjugate_diagnostic"],
            "gate_test": candidates[name]["gate_test"],
            "real_dev": candidates[name]["real_dev"],
        }
    for key in ("normal", "conjugate_diagnostic"):
        source_key = ("synthetic_normal" if key == "normal"
                      else "synthetic_conjugate_diagnostic")
        comparison["per_class_" + key] = {}
        for label in ("BPSK", "QPSK", "QAM16", "QAM64"):
            comparison["per_class_" + key][label] = {
                "baseline_recall": baseline[source_key]["per_class"][label]["accuracy"],
                "A_recall": candidates["candidate_A"][source_key]["per_class"][label]["accuracy"],
                "B_recall": candidates["candidate_B"][source_key]["per_class"][label]["accuracy"],
                "baseline_accepted_rate": baseline[source_key]["per_class"][label]["accepted_rate"],
                "A_accepted_rate": candidates["candidate_A"][source_key]["per_class"][label]["accepted_rate"],
                "B_accepted_rate": candidates["candidate_B"][source_key]["per_class"][label]["accepted_rate"],
            }
    comparison["real_qam16"] = {
        name: [{"frame": r["frame"], "truth": r["truth"], "pred": r["pred"],
                "accepted": r["accepted"], "qam16_score": r["qam16_score"],
                "qam64_score": r["qam64_score"]}
               for r in candidates[name]["real_dev"]["qam16"]]
        for name in ("candidate_A", "candidate_B")
    }
    comparison["gate_test"] = {
        name: {kind: candidates[name]["gate_test"][kind]["rejection_rate"]
               for kind in ("OFF", "NOISE", "TONE", "CLIP", "all")}
        for name in ("candidate_A", "candidate_B")
    }
    path = RESULTS / "comparison.json"
    raw = json.dumps(comparison, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    print(json.dumps({
        "comparison": str(path),
        "comparison_sha256": hashlib.sha256(raw).hexdigest(),
        "training_seconds": TRAIN_SECONDS,
        "total_seconds": sum(TRAIN_SECONDS.values()),
        "verdict_A": comparison["verdict"]["candidate_A"]["status"],
        "verdict_B": comparison["verdict"]["candidate_B"]["status"],
    }, indent=1))


if __name__ == "__main__":
    main()
