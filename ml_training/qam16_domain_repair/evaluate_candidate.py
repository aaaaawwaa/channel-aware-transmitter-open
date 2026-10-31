#!/usr/bin/env python3
"""Evaluate candidates A/B against the frozen baseline on independent synthetic splits
and on the 14 dev real frames.  Candidate decision thresholds are the frozen ones.

All synthetic test/gate_test splits are independent seed families never used for
training.  The 14 real frames are development diagnosis only, not an independent
acceptance set; no real frame is used for training.
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
DEFAULT_DATASET = ROOT / "ml_training" / "datasets" / "sim-v1"
FROZEN_RULES = ROOT / "ml_training" / "round2" / "artifacts" / "rule_config.json"
DATA_RUN = ROOT / "evidence" / "rf-teaching" / "s2-46db-fourclass-real-rx-2026-10-22" / "run"
DEV_TRUTH_SOURCE = (ROOT / "ml_training" / "fourclass_qam16_diagnosis" /
                    "results" / "tx_truth_side.json")

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split, sha256_file  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.round2.predictor import Round2Predictor  # noqa: E402

CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
KINDS = ("OFF", "NOISE", "TONE", "CLIP")


def _json_write(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _eval_split(predictor, X, labels):
    truth = np.asarray([CLASSES.index(str(label)) for label in labels], dtype=np.int64)
    records = []
    for index in range(len(X)):
        decision = predictor.decide(X[index])
        pred = decision.get("label")
        pred_index = CLASSES.index(pred) if pred in CLASSES else None
        records.append({
            "truth": CLASSES[int(truth[index])],
            "pred": pred,
            "pred_index": pred_index,
            "accepted": pred in CLASSES,
            "reason": decision.get("reason"),
            "scores": decision.get("scores"),
            "qam16_score": (decision.get("scores") or [None] * 4)[2],
            "qam64_score": (decision.get("scores") or [None] * 4)[3],
        })
    per_class = {}
    for index, label in enumerate(CLASSES):
        subset = [r for r in records if r["truth"] == label]
        correct = [r for r in subset if r["pred"] == label]
        accepted = [r for r in subset if r["accepted"]]
        correct_accepted = [r for r in accepted if r["pred"] == label]
        per_class[label] = {
            "n": len(subset),
            "correct": len(correct),
            "accuracy": len(correct) / max(len(subset), 1),
            "accepted": len(accepted),
            "accepted_rate": len(accepted) / max(len(subset), 1),
            "accepted_correct": len(correct_accepted),
            "accepted_accuracy": len(correct_accepted) / max(len(accepted), 1),
            "pred_counts": {name: sum(1 for r in subset if r["pred"] == name)
                            for name in list(CLASSES) + ["UNKNOWN"]},
        }
    # Macro F1 with UNKNOWN treated as a wrong prediction (not a class).
    f1s = []
    for index, label in enumerate(CLASSES):
        tp = sum(1 for r in records if r["truth"] == label and r["pred"] == label)
        fp = sum(1 for r in records if r["truth"] != label and r["pred"] == label)
        fn = sum(1 for r in records if r["truth"] == label and r["pred"] != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1s.append(2 * precision * recall / (precision + recall)
                   if (precision + recall) else 0.0)
    accepted = [r for r in records if r["accepted"]]
    return {
        "n": len(records),
        "macro_f1_all": float(np.mean(f1s)),
        "accuracy_all": sum(1 for r in records if r["pred"] == r["truth"]) / max(len(records), 1),
        "accepted": len(accepted),
        "accept_rate": len(accepted) / max(len(records), 1),
        "accepted_correct": sum(1 for r in accepted if r["pred"] == r["truth"]),
        "accepted_accuracy": (sum(1 for r in accepted if r["pred"] == r["truth"])
                              / max(len(accepted), 1)),
        "rejected": len(records) - len(accepted),
        "per_class": per_class,
        "qam16_pred_counts": {name: sum(1 for r in records
                                       if r["truth"] == "QAM16" and r["pred"] == name)
                              for name in list(CLASSES) + ["UNKNOWN"]},
        "records": records if len(records) <= 3000 else None,
    }


def _eval_gate(predictor, manifest, dataset_dir):
    result = {}
    for kind in KINDS:
        data = load_split(dataset_dir, manifest, "gate_test", kinds=[kind],
                          include_clipped=True)
        rejected = 0
        reasons = {}
        for iq in data["X"]:
            decision = predictor.decide(iq)
            if decision.get("label") == "UNKNOWN":
                rejected += 1
                reason = decision.get("reason") or "unknown"
                reasons[reason] = reasons.get(reason, 0) + 1
        result[kind] = {
            "n": len(data["X"]),
            "rejected": rejected,
            "rejection_rate": rejected / max(len(data["X"]), 1),
            "reasons": reasons,
        }
    result["all"] = {
        "n": sum(row["n"] for row in result.values()),
        "rejected": sum(row["rejected"] for row in result.values()),
        "rejection_rate": (sum(row["rejected"] for row in result.values())
                           / max(sum(row["n"] for row in result.values()), 1)),
    }
    return result


def _eval_real_dev(predictor, truth, truth_sha):
    by_key = {}
    for entry in truth["frames"]:
        identity = entry["frame_identity"]
        by_key[(identity["capture_id"], identity["iq_sha256"])] = entry
    frame_rows = []
    for meta_path in sorted(DATA_RUN.glob("s*.json")):
        if not meta_path.name.startswith("s0"):
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        iq_path = meta_path.with_suffix(".iq")
        if not iq_path.is_file():
            continue
        digest = sha256_file(iq_path)
        key = (meta.get("capture_id"), digest)
        entry = by_key.get(key)
        if entry is None:
            continue
        iq = np.frombuffer(iq_path.read_bytes(), dtype="<i2").reshape(-1, 2).copy()
        decision = predictor.decide(iq)
        tx = entry["tx"]
        truth_class = tx["mode"] if tx["mode"] != "OFF" else "OFF"
        pred = decision.get("label")
        frame_rows.append({
            "frame": meta_path.stem,
            "truth": truth_class,
            "tx_mode": tx["mode"],
            "tx_seed": tx.get("seed"),
            "capture_id": meta.get("capture_id"),
            "iq_sha256": digest,
            "pred": pred,
            "accepted": pred in CLASSES,
            "reason": decision.get("reason"),
            "scores": decision.get("scores"),
            "correct": pred == truth_class,
            "qam16_score": (decision.get("scores") or [None] * 4)[2],
            "qam64_score": (decision.get("scores") or [None] * 4)[3],
        })
    qam16 = [r for r in frame_rows if r["truth"] == "QAM16"]
    return {
        "truth_source": str(DEV_TRUTH_SOURCE),
        "truth_sha256": truth_sha,
        "frames": frame_rows,
        "qam16": qam16,
        "qam16_correct": sum(1 for r in qam16 if r["correct"]),
        "qam16_n": len(qam16),
        "on_correct": sum(1 for r in frame_rows if r["truth"] in CLASSES and r["correct"]),
        "on_n": sum(1 for r in frame_rows if r["truth"] in CLASSES),
        "off_rejected": sum(1 for r in frame_rows if r["truth"] == "OFF"
                            and r["pred"] == "UNKNOWN"),
        "off_n": sum(1 for r in frame_rows if r["truth"] == "OFF"),
        "note": "14 dev frames only; not independent acceptance; no training use",
    }


def _conj_iq_array(X):
    out = np.asarray(X).copy()
    out[..., 1] = -out[..., 1]
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=("A", "B", "baseline"), required=True)
    args = parser.parse_args()
    manifest = load_manifest(DEFAULT_DATASET)
    test = load_split(DEFAULT_DATASET, manifest, "test", labels=list(CLASSES))
    if args.candidate == "baseline":
        predictor = Round2Predictor()
        candidate = "baseline_frozen"
        candidate_dir = None
    else:
        candidate_dir = RESULTS / ("candidate_" + args.candidate)
        rules_path = candidate_dir / "rules.json"
        if not rules_path.is_file():
            raise SystemExit("candidate rules missing: " + str(rules_path))
        predictor = Round2Predictor(rules_path=rules_path)
        candidate = "candidate_" + args.candidate
    normal = _eval_split(predictor, test["X"], test["label"])
    conjugate = _eval_split(predictor, _conj_iq_array(test["X"]), test["label"])
    gate = _eval_gate(predictor, manifest, DEFAULT_DATASET)
    truth = json.loads(DEV_TRUTH_SOURCE.read_text(encoding="utf-8"))
    truth_sha = sha256_file(DEV_TRUTH_SOURCE)
    real = _eval_real_dev(predictor, truth, truth_sha)
    out = {
        "schema": "ml-qam16-domain-repair-eval-v1",
        "candidate": candidate,
        "data": {
            "synthetic_test": "sim-v1/test (seed families test-a/b/c; independent)",
            "synthetic_test_conjugate_diagnostic": "Q->-Q transform of sim-v1/test; diagnostic only",
            "gate_test": "sim-v1/gate_test (independent gate seed families)",
            "real_dev": "14 real frames; development diagnosis only, not acceptance",
        },
        "decision_thresholds": {
            "source": str(FROZEN_RULES.relative_to(ROOT)).replace("\\", "/"),
            "source_sha256": sha256_file(FROZEN_RULES),
            "values": dict(predictor.gate),
            "recalibrated": False,
        },
        "candidate_artifacts": ({
            "dir": str(candidate_dir),
            "model_sha256": sha256_file(candidate_dir / "model.json"),
            "rules_sha256": sha256_file(candidate_dir / "rules.json"),
        } if candidate_dir is not None else None),
        "synthetic_normal": normal,
        "synthetic_conjugate_diagnostic": conjugate,
        "gate_test": gate,
        "real_dev": real,
    }
    out_path = RESULTS / (candidate + "_eval.json")
    sha = _json_write(out_path, out)
    print(json.dumps({
        "candidate": candidate,
        "eval_file": str(out_path),
        "eval_sha256": sha,
        "normal": {key: normal[key] for key in
                   ("macro_f1_all", "accuracy_all", "accept_rate", "accepted_accuracy")},
        "conjugate_diagnostic": {key: conjugate[key] for key in
                                 ("macro_f1_all", "accuracy_all", "accept_rate", "accepted_accuracy")},
        "gate_rejection": {kind: gate[kind]["rejection_rate"] for kind in KINDS},
        "real_qam16": [{"frame": r["frame"], "pred": r["pred"],
                        "qam16_score": r["qam16_score"], "qam64_score": r["qam64_score"]}
                       for r in real["qam16"]],
        "real_qam16_correct": "%d/%d" % (real["qam16_correct"], real["qam16_n"]),
        "real_off_rejected": "%d/%d" % (real["off_rejected"], real["off_n"]),
    }, indent=1))


if __name__ == "__main__":
    main()
