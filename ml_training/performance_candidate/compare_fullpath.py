#!/usr/bin/env python3
"""Compare baseline vs optimized full-path measurement outputs (same frames)."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

NUMBER = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
TOL = {"six_score": 1e-6, "modrec4_logit": 1e-3, "modrec4_score": 1e-6,
       "spectral": 1e-6, "environment": 1e-9}


def _numbers(text):
    return [float(token) for token in NUMBER.findall(str(text))]


def _env_compare(left, right):
    if str(left) == str(right):
        return {"pass": True, "max_numeric_delta": 0.0, "exact": True}
    left_numbers, right_numbers = _numbers(left), _numbers(right)
    left_shape = NUMBER.sub("#", str(left))
    right_shape = NUMBER.sub("#", str(right))
    if len(left_numbers) != len(right_numbers) or left_shape != right_shape:
        return {"pass": False, "max_numeric_delta": None, "exact": False}
    delta = max((abs(a - b) for a, b in zip(left_numbers, right_numbers)), default=0.0)
    return {"pass": delta <= TOL["environment"], "max_numeric_delta": delta,
            "exact": False}


def _delta(left, right):
    if left is None and right is None:
        return 0.0
    if left is None or right is None:
        return float("inf")
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return float("inf")
        return max((_delta(left[key], right[key]) for key in left), default=0.0)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return float("inf")
        return max((_delta(a, b) for a, b in zip(left, right)), default=0.0)
    try:
        return abs(float(left) - float(right))
    except (TypeError, ValueError):
        return 0.0 if left == right else float("inf")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    problems = []
    rows = []
    before_results = before.get("results") or {}
    after_results = after.get("results") or {}
    for tag in sorted(set(before_results) | set(after_results)):
        a, b = before_results.get(tag), after_results.get(tag)
        row = {"frame": tag, "problems": []}
        if a is None or b is None:
            row["problems"].append("missing side")
        else:
            if a.get("release_id") != b.get("release_id"):
                row["problems"].append("release_id differs")
            if a.get("frame") != b.get("frame"):
                row["problems"].append("frame identity differs")
            sa, sb = a.get("six_class") or {}, b.get("six_class") or {}
            if (sa.get("label") != sb.get("label")
                    or (sa.get("reason") or "") != (sb.get("reason") or "")
                    or sa.get("model_sha256") != sb.get("model_sha256")):
                row["problems"].append("six_class label/reason/model_sha differs")
            row["six_score_delta"] = _delta(sa.get("scores"), sb.get("scores"))
            if row["six_score_delta"] > TOL["six_score"]:
                row["problems"].append("six_class scores delta")
            env = _env_compare(a.get("environment"), b.get("environment"))
            row["environment"] = env
            if not env["pass"]:
                row["problems"].append("environment differs")
            ma, mb = a.get("modrec4") or {}, b.get("modrec4") or {}
            if ma.get("status") != mb.get("status"):
                row["problems"].append("modrec4 status differs")
            ma_mod, mb_mod = ma.get("modulation") or {}, mb.get("modulation") or {}
            for field in ("class", "final_label", "final_label_source", "accepted",
                          "reason", "cnn_raw_top1", "arbitration_applied",
                          "arbitration_source", "decision_stage"):
                if ma_mod.get(field) != mb_mod.get(field):
                    row["problems"].append("modrec4.%s differs" % field)
            if ma_mod.get("arbitration") != mb_mod.get("arbitration"):
                row["problems"].append("modrec4 arbitration differs")
            row["scores_delta"] = _delta(ma_mod.get("scores4"), mb_mod.get("scores4"))
            row["logits_delta"] = _delta(ma_mod.get("cnn_logits4"),
                                         mb_mod.get("cnn_logits4"))
            row["spectral_delta"] = _delta(ma_mod.get("spectral"), mb_mod.get("spectral"))
            if row["scores_delta"] > TOL["modrec4_score"]:
                row["problems"].append("modrec4 scores delta")
            if row["logits_delta"] > TOL["modrec4_logit"]:
                row["problems"].append("modrec4 logits delta")
            if row["spectral_delta"] > TOL["spectral"]:
                row["problems"].append("modrec4 spectral delta")
            if (ma.get("contract") or {}).get("six_class_probabilities") is not None \
                    or (mb.get("contract") or {}).get("six_class_probabilities") is not None:
                row["problems"].append("six_class_probabilities not null")
            if ma.get("passthrough") != mb.get("passthrough"):
                row["problems"].append("modrec4 passthrough differs")
        row["status"] = "PASS" if not row["problems"] else "FAIL"
        if row["problems"]:
            problems.append(row)
        rows.append(row)
    document = {
        "schema": "ml-performance-fullpath-parity-v1",
        "before": str(args.before),
        "after": str(args.after),
        "frames": rows,
        "mismatch_count": len(problems),
        "mismatches": problems,
        "timing": {"before": before.get("timing_ms"), "after": after.get("timing_ms"),
                   "before_stages": before.get("stages_ms"),
                   "after_stages": after.get("stages_ms"),
                   "before_wall": before.get("wall_invocation_ms"),
                   "after_wall": after.get("wall_invocation_ms")},
        "tolerances": TOL,
        "status": "PASS" if not problems else "FAIL",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(json.dumps({"status": document["status"],
                      "frames": len(rows),
                      "mismatch_count": document["mismatch_count"],
                      "timing": document["timing"]}, indent=1))
    return 0 if document["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
