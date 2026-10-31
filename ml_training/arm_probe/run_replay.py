#!/usr/bin/env python3
"""Standalone replay entrypoint for the frozen round-2 ARM probe.

Runs the frozen CNN + baseline arbitration + spectral gate on the bundled frames using only
the package's own ``runtime/`` modules and ``model/`` artefacts, and compares the result with
``expected/host_expected.json``.

Usage (from the package directory or any copied clean directory):

    python3 run_replay.py --out results.json
    python3 run_replay.py --check

Requirements: CPython 3.8+ and NumPy only (board target: CPython 3.8.x / NumPy 1.17.4).
No torch/scipy/product module is imported, and nothing is written outside ``--out``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

import numpy  # noqa: E402

from runtime.gate import apply_gate, softmax  # noqa: E402
from runtime.iq import read_frame  # noqa: E402
from runtime.predictor import CLASSES, Round2Predictor  # noqa: E402
from runtime.preprocess import preprocess_iq  # noqa: E402
from runtime.spectral import spectral_features  # noqa: E402

SCHEMA = "ml-modrec-arm-probe-replay-v1"
UNKNOWN = "UNKNOWN"
CLEAN_PREFIX = "clean_"
GATE_PREFIX = "gate_"
STRESS_PREFIX = "stress_"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_manifest():
    return load_json(PACKAGE / "input_manifest.json")


def load_predictor():
    return Round2Predictor()


def _before_decision(cnn, iq):
    """Frozen round-1 CNN + round-1 gate decision (before the round-2 rules)."""
    x, gate = preprocess_iq(iq)
    result = {"label": UNKNOWN, "reason": gate["reason"], "score": 0.0, "margin": 0.0,
              "scores": None, "logits": None, "rms": float(gate.get("rms", 0.0))}
    if not gate["ok"]:
        return result
    if gate["rms"] < cnn.thresholds["min_complex_rms"]:
        result["reason"] = "below_energy_floor"
        return result
    logits = cnn.forward(x)
    scores = softmax(logits)
    decision = apply_gate(scores, CLASSES, cnn.thresholds)
    result.update(label=decision["label"], reason=decision["reason"],
                  score=float(decision["score"]), margin=float(decision["margin"]),
                  scores=[float(value) for value in scores],
                  logits=[float(value) for value in logits])
    return result


def evaluate_one(predictor, entry):
    frame_path = PACKAGE / entry["path"]
    raw = frame_path.read_bytes()
    digest = sha256_bytes(raw)
    if digest != entry["iq_sha256"]:
        raise SystemExit("frame hash mismatch: %s (%s != %s)"
                         % (entry["id"], digest, entry["iq_sha256"]))
    iq = read_frame(frame_path)
    before = _before_decision(predictor.cnn, iq)
    after = predictor.decide(iq)
    classification = predictor.classify(iq)
    x, gate = preprocess_iq(iq)
    spectral = ({key: float(value) for key, value in spectral_features(x).items()}
                if gate.get("ok") else None)
    return {
        "id": entry["id"],
        "group": entry["group"],
        "kind": entry["kind"],
        "truth": entry.get("truth"),
        "source": entry.get("source"),
        "iq_sha256": digest,
        "rms": float(gate.get("rms", 0.0)),
        "gate_ok": bool(gate.get("ok")),
        "before": before,
        "after": {
            "label": after["label"], "reason": after["reason"],
            "score": float(after["score"]), "margin": float(after["margin"]),
            "scores": after.get("scores"),
            "arbitration_applied": bool(after["arbitration_applied"]),
            "arbitration": after.get("arbitration"),
            "baseline_scores": after.get("baseline_scores"),
            "spectral_trigger": after.get("spectral_trigger"),
            "pure_class_label": classification.get("class_label"),
        },
        "spectral": spectral,
    }


def summarise(frames):
    groups = {}
    for frame in frames:
        item = groups.setdefault(frame["group"], {
            "n": 0, "kind": frame["kind"], "truth_classes": [],
            "before_labels": [], "after_labels": [], "after_reasons": [],
            "before_correct": 0, "after_correct": 0,
        })
        item["n"] += 1
        truth = frame.get("truth") or {}
        truth_class = truth.get("class")
        if truth_class:
            item["truth_classes"].append(truth_class)
        item["before_labels"].append(frame["before"]["label"])
        item["after_labels"].append(frame["after"]["label"])
        item["after_reasons"].append(frame["after"]["reason"])
        if truth_class:
            item["before_correct"] += int(frame["before"]["label"] == truth_class)
            item["after_correct"] += int(frame["after"]["label"] == truth_class)
    summary = {"groups": groups, "clean": {}, "gate": {}, "stress": {}}
    clean_n = clean_correct = clean_before = 0
    for name, item in groups.items():
        if name.startswith(CLEAN_PREFIX):
            n = len(item["truth_classes"])
            entry = {"n": item["n"], "truth_classes": sorted(set(item["truth_classes"])),
                     "before_labels": item["before_labels"],
                     "after_labels": item["after_labels"],
                     "after_reasons": item["after_reasons"],
                     "before_correct": item["before_correct"],
                     "after_correct": item["after_correct"],
                     "before_accuracy": (item["before_correct"] / n) if n else None,
                     "after_accuracy": (item["after_correct"] / n) if n else None}
            summary["clean"][name] = entry
            clean_n += n
            clean_correct += item["after_correct"]
            clean_before += item["before_correct"]
        elif name.startswith(GATE_PREFIX):
            rejected = sum(1 for reason in item["after_reasons"] if reason)
            reasons = {}
            for reason in item["after_reasons"]:
                reasons[reason or "accepted"] = reasons.get(reason or "accepted", 0) + 1
            summary["gate"][name] = {"n": item["n"], "rejected": rejected,
                                     "accepted": item["n"] - rejected,
                                     "reasons": reasons,
                                     "before_labels": item["before_labels"],
                                     "after_labels": item["after_labels"]}
        elif name.startswith(STRESS_PREFIX):
            summary["stress"][name] = {"n": item["n"],
                                       "before_labels": item["before_labels"],
                                       "after_labels": item["after_labels"],
                                       "after_reasons": item["after_reasons"],
                                       "note": "interference stress only; not counted in clean 4-class accuracy"}
    summary["clean"]["__total__"] = {
        "n": clean_n, "before_correct": clean_before, "after_correct": clean_correct,
        "before_accuracy": (clean_before / clean_n) if clean_n else None,
        "after_accuracy": (clean_correct / clean_n) if clean_n else None,
        "note": "mixed real BPSK frames and synthetic 4-class frames; not a real-world accuracy estimate",
    }
    return summary


def evaluate(predictor, manifest):
    frames = [evaluate_one(predictor, entry) for entry in manifest["frames"]]
    result = {
        "schema": SCHEMA,
        "package": {"name": "ml_training/arm_probe",
                    "manifest_sha256": (sha256_bytes((PACKAGE / "MANIFEST.json").read_bytes())
                                        if (PACKAGE / "MANIFEST.json").is_file() else None),
                    "frames": len(frames)},
        "host": {"python": sys.version.split()[0], "numpy": numpy.__version__,
                 "platform": platform.platform(), "machine": platform.machine()},
        "frames": frames,
        "summary": summarise(frames),
    }
    return result


def compare(expected, actual, logit_tolerance=1e-3, score_tolerance=1e-6,
            spectral_tolerance=1e-6):
    problems = []
    expected_frames = {frame["id"]: frame for frame in expected["frames"]}
    actual_frames = {frame["id"]: frame for frame in actual["frames"]}
    if set(expected_frames) != set(actual_frames):
        problems.append("frame id set differs")
    for frame_id in sorted(set(expected_frames) & set(actual_frames)):
        want = expected_frames[frame_id]
        got = actual_frames[frame_id]
        for section in ("before", "after"):
            for key in ("label", "reason"):
                if want[section][key] != got[section][key]:
                    problems.append("%s.%s.%s: %r != %r"
                                    % (frame_id, section, key, want[section][key],
                                       got[section][key]))
        if want["after"]["arbitration_applied"] != got["after"]["arbitration_applied"]:
            problems.append("%s.after.arbitration_applied differs" % frame_id)
        want_logits = want["before"].get("logits")
        got_logits = got["before"].get("logits")
        if (want_logits is None) != (got_logits is None):
            problems.append("%s logits presence differs" % frame_id)
        elif want_logits is not None:
            delta = max(abs(a - b) for a, b in zip(want_logits, got_logits))
            if delta > logit_tolerance:
                problems.append("%s logit delta %.6g > %.6g"
                                % (frame_id, delta, logit_tolerance))
        for section in ("before", "after"):
            want_scores = want[section].get("scores")
            got_scores = got[section].get("scores")
            if (want_scores is None) != (got_scores is None):
                problems.append("%s.%s scores presence differs" % (frame_id, section))
            elif want_scores is not None:
                delta = max(abs(a - b) for a, b in zip(want_scores, got_scores))
                if delta > score_tolerance:
                    problems.append("%s.%s score delta %.6g > %.6g"
                                    % (frame_id, section, delta, score_tolerance))
        want_spectral = want.get("spectral")
        got_spectral = got.get("spectral")
        if (want_spectral is None) != (got_spectral is None):
            problems.append("%s spectral presence differs" % frame_id)
        elif want_spectral is not None:
            delta = max(abs(want_spectral[key] - got_spectral[key])
                        for key in want_spectral)
            if delta > spectral_tolerance:
                problems.append("%s spectral delta %.6g > %.6g"
                                % (frame_id, delta, spectral_tolerance))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="results.json", help="result JSON path")
    parser.add_argument("--check", action="store_true",
                        help="compare with expected/host_expected.json and exit non-zero on mismatch")
    parser.add_argument("--logit-tolerance", type=float, default=1e-3)
    parser.add_argument("--score-tolerance", type=float, default=1e-6)
    parser.add_argument("--spectral-tolerance", type=float, default=1e-6)
    args = parser.parse_args(argv)
    predictor = load_predictor()
    manifest = load_manifest()
    result = evaluate(predictor, manifest)
    out_path = Path(args.out)
    out_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    summary = result["summary"]
    clean = summary["clean"]["__total__"]
    print("[replay] frames=%d clean_n=%d clean_before_acc=%s clean_after_acc=%s"
          % (len(result["frames"]), clean["n"], clean["before_accuracy"],
             clean["after_accuracy"]))
    for name in sorted(summary["gate"]):
        item = summary["gate"][name]
        print("[replay] %-18s n=%d rejected=%d accepted=%d reasons=%s"
              % (name, item["n"], item["rejected"], item["accepted"],
                 json.dumps(item["reasons"], sort_keys=True)))
    for name in sorted(summary["stress"]):
        item = summary["stress"][name]
        print("[replay] %-18s n=%d after=%s reasons=%s (stress only)"
              % (name, item["n"], item["after_labels"], item["after_reasons"]))
    if args.check:
        expected = load_json(PACKAGE / "expected" / "host_expected.json")
        problems = compare(expected, result, args.logit_tolerance,
                           args.score_tolerance, args.spectral_tolerance)
        if problems:
            print("[replay] CHECK FAILED (%d problems)" % len(problems))
            for problem in problems[:20]:
                print("  -", problem)
            return 1
        print("[replay] CHECK PASSED: labels/reasons/arbitration exact; logits/scores/"
              "spectral within frozen tolerances")
    print("[replay] wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
