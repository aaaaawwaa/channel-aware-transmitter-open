"""Frozen round-2 evaluation on imported real frames.

Separation is mandatory:
* clean-signal classification (normal frames with an explicit four-class truth);
* quality rejection (OFF/TONE/NOISE/CLIP-like frames, truth is a quality label);
* interference stress (narrowband etc.; never mixed into clean accuracy).

A four-class overall accuracy is emitted only if all four real classes are present.  Missing
classes are reported as ``NOT_RUN``; no zero is fabricated to stand in for absent data.
"""
from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.round2.predictor import CLASSES, Round2Predictor  # noqa: E402

from . import domain  # noqa: E402

FROZEN_RULES = ROOT / "ml_training" / "round2" / "artifacts" / "rule_config.json"
FROZEN_CNN = ROOT / "ml_training" / "artifacts" / "cnn" / "model.json"
FROZEN_BASELINE = ROOT / "ml_training" / "artifacts" / "baseline" / "model.json"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _predictor_metadata():
    return {
        "frozen_round2_predictor": True,
        "rule_config_path": str(FROZEN_RULES.relative_to(ROOT)).replace("\\", "/"),
        "rule_config_sha256": _sha256(FROZEN_RULES),
        "cnn_path": str(FROZEN_CNN.relative_to(ROOT)).replace("\\", "/"),
        "cnn_sha256": _sha256(FROZEN_CNN),
        "baseline_path": str(FROZEN_BASELINE.relative_to(ROOT)).replace("\\", "/"),
        "baseline_sha256": _sha256(FROZEN_BASELINE),
        "classes": list(CLASSES),
    }


def _empty_clean():
    return {
        "status": "NOT_RUN",
        "frames": 0,
        "counts_by_class": {label: 0 for label in CLASSES},
        "present_classes": [],
        "missing_classes": list(CLASSES),
        "correct": 0,
        "wrong": 0,
        "accepted": 0,
        "rejected": 0,
        "known_false_rejection": {"count": 0, "total": 0, "rate": None},
        "per_class": {},
        "four_class_overall_accuracy": {
            "status": "NOT_RUN",
            "reason": "no clean real frames",
            "missing_classes": list(CLASSES),
        },
    }


def _clean_metrics(records):
    metrics = _empty_clean()
    if not records:
        return metrics
    counts = {label: 0 for label in CLASSES}
    per_class = {label: {"total": 0, "correct": 0, "wrong": 0, "rejected": 0,
                         "accuracy": None} for label in CLASSES}
    correct = wrong = rejected = accepted = 0
    for record in records:
        label = record["truth_class"]
        if label not in CLASSES:
            continue
        final = record["evaluation"]["final_label"]
        counts[label] += 1
        per_class[label]["total"] += 1
        if final == "UNKNOWN":
            rejected += 1
            per_class[label]["rejected"] += 1
        elif final == label:
            correct += 1
            accepted += 1
            per_class[label]["correct"] += 1
        else:
            wrong += 1
            accepted += 1
            per_class[label]["wrong"] += 1
    for label, item in per_class.items():
        if item["total"]:
            item["accuracy"] = (item["correct"] / item["total"])
    present = [label for label in CLASSES if counts[label] > 0]
    missing = [label for label in CLASSES if counts[label] == 0]
    metrics.update({
        "status": "PASS" if present else "NOT_RUN",
        "frames": len(records),
        "counts_by_class": counts,
        "present_classes": present,
        "missing_classes": missing,
        "correct": correct,
        "wrong": wrong,
        "accepted": accepted,
        "rejected": rejected,
        "known_false_rejection": {
            "count": rejected,
            "total": len(records),
            "rate": (rejected / len(records)) if records else None,
        },
        "per_class": per_class,
        "four_class_overall_accuracy": (
            {"status": "PASS", "value": correct / len(records)}
            if not missing and records else
            {"status": "NOT_RUN",
             "reason": "missing real class data: " + ", ".join(missing),
             "missing_classes": missing,
             "note": "per-class metrics are reported; no four-class overall accuracy is fabricated"}
        ),
    })
    return metrics


def _quality_metrics(records):
    if not records:
        return {"status": "NOT_RUN", "reason": "no quality/off real frames",
                "total": 0, "rejected": 0, "false_accept": 0,
                "rejection_rate": None, "reasons": {}}
    rejected = false_accept = 0
    reasons = defaultdict(int)
    for record in records:
        final = record["evaluation"]["final_label"]
        reason = record["evaluation"]["reason"]
        reasons[reason or ""] += 1
        if final == "UNKNOWN":
            rejected += 1
        elif final in CLASSES:
            false_accept += 1
    return {
        "status": "PASS",
        "total": len(records),
        "rejected": rejected,
        "false_accept": false_accept,
        "rejection_rate": rejected / len(records),
        "reasons": dict(sorted(reasons.items())),
        "labels": [record["truth_class"] for record in records],
    }


def _stress_metrics(records):
    if not records:
        return {"status": "NOT_RUN", "reason": "no real interference-stress frames",
                "total": 0, "results": []}
    results = []
    for record in records:
        result = record["evaluation"]
        results.append({
            "frame_id": record["frame_id"],
            "session_id": record["session_id"],
            "truth_class": record["truth_class"],
            "final_label": result["final_label"],
            "accepted": result["accepted"],
            "reason": result["reason"],
            "arbitration_applied": result["arbitration_applied"],
        })
    accepted = sum(1 for item in results if item["accepted"])
    return {
        "status": "PASS",
        "total": len(records),
        "accepted": accepted,
        "rejected": len(records) - accepted,
        "results": results,
        "note": "interference stress only; not counted in clean four-class accuracy",
    }


def _session_metrics(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[record["session_key"]].append(record)
    sessions = []
    for session_key in sorted(grouped):
        items = grouped[session_key]
        first = items[0]
        clean = [record for record in items
                 if record["kind"] == "clean" and record["status"] == "OK"]
        quality = [record for record in items
                   if record["kind"] == "quality" and record["status"] == "OK"]
        stress = [record for record in items
                  if record["kind"] == "stress" and record["status"] == "OK"]
        clean_metrics = _clean_metrics(clean) if clean else _empty_clean()
        sessions.append({
            "session_key": session_key,
            "source_id": first["source_id"],
            "session_id": first["session_id"],
            "split": first["split"],
            "synthetic": first["synthetic"],
            "frames": len(items),
            "invalid": len([record for record in items if record["status"] != "OK"]),
            "release_ids": sorted({record["identity"].get("release_id")
                                   for record in items
                                   if record["identity"].get("release_id")}),
            "boot_id": next((record["identity"].get("boot_id") for record in items
                             if record["identity"].get("boot_id")), None),
            "clean": {
                "frames": clean_metrics["frames"],
                "counts_by_class": clean_metrics["counts_by_class"],
                "present_classes": clean_metrics["present_classes"],
                "missing_classes": clean_metrics["missing_classes"],
                "accuracy": (clean_metrics["correct"] / clean_metrics["frames"]
                             if clean_metrics["frames"] else None),
                "four_class_overall_accuracy":
                    clean_metrics["four_class_overall_accuracy"],
                "known_false_rejection": clean_metrics["known_false_rejection"],
            },
            "quality": _quality_metrics(quality) if quality else {
                "status": "NOT_RUN", "reason": "no quality frames in session",
                "total": 0},
            "stress": _stress_metrics(stress) if stress else {
                "status": "NOT_RUN", "reason": "no stress frames in session",
                "total": 0},
        })
    return sessions


def _unique_records(records):
    """Keep one deterministic record per IQ digest for metrics; retain all in the index.

    Campaign5's TRAIN copies duplicate campaign4's TRAIN frames.  They are real evidence of a
    repeated acquisition, but they must not double-count the same IQ in classification rates.
    """
    seen = {}
    unique, duplicates = [], []
    for record in records:
        digest = record.get("iq_sha256")
        if not digest:
            unique.append(record)
            continue
        if digest in seen:
            duplicates.append({"frame_id": record["frame_id"],
                               "kept_frame_id": seen[digest]["frame_id"],
                               "session_key": record["session_key"],
                               "split": record["split"]})
            continue
        seen[digest] = record
        unique.append(record)
    return unique, duplicates


def evaluate(records, predictor=None, synthetic=False):
    predictor = predictor or Round2Predictor()
    valid, skipped = [], []
    for record in records:
        if record["status"] != "OK":
            skipped.append({"frame_id": record["frame_id"], "reason": "import_invalid",
                            "details": record["invalid_reasons"]})
            continue
        if record["domain_status"] != "OK":
            skipped.append({"frame_id": record["frame_id"],
                            "reason": record["domain_reason"] or "domain_unsupported"})
            continue
        iq_check = domain.load_iq(ROOT / record["iq_path"])
        if not iq_check["ok"]:
            skipped.append({"frame_id": record["frame_id"],
                            "reason": iq_check["reason"]})
            continue
        decision = predictor.decide(iq_check["iq"])
        record["evaluation"] = {
            "final_label": decision.get("label", "UNKNOWN"),
            "accepted": decision.get("label") in CLASSES,
            "reason": decision.get("reason", ""),
            "arbitration_applied": bool(decision.get("arbitration_applied", False)),
            "score": decision.get("score"),
            "cnn_scores": decision.get("cnn_scores"),
        }
        valid.append(record)
    valid, duplicate_records = _unique_records(valid)
    clean = [record for record in valid if record["kind"] == "clean"]
    quality = [record for record in valid if record["kind"] == "quality"]
    stress = [record for record in valid if record["kind"] == "stress"]
    other = [record for record in valid if record["kind"] not in
             ("clean", "quality", "stress")]
    result = {
        "schema": "ml-real-campaign-evaluation-v1",
        "synthetic": synthetic,
        "predictor": _predictor_metadata(),
        "frames_evaluated": len(valid),
        "frames_deduplicated": len(duplicate_records),
        "duplicate_records": duplicate_records,
        "frames_skipped": skipped,
        "clean": _clean_metrics(clean),
        "quality": _quality_metrics(quality),
        "stress": _stress_metrics(stress),
        "other": {"frames": len(other), "records": [
            {"frame_id": record["frame_id"], "scenario": record["scenario"],
             "final_label": record["evaluation"]["final_label"]}
            for record in other]},
        "per_session": _session_metrics(valid),
        "status": "PASS" if valid else "NOT_RUN",
        "note": ("frozen round-2 decision; clean/quality/stress are reported separately; "
                 "missing real classes are NOT_RUN"),
    }
    return result


def evaluate_real(imported):
    return evaluate(imported["real_records"], synthetic=False)


def evaluate_synthetic(imported):
    return evaluate(imported["synthetic_records"], synthetic=True)
