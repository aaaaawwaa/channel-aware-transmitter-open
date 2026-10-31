#!/usr/bin/env python3
"""Compare baseline vs optimized adapter parity cases with frozen tolerances."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

LOGIT_TOL = 1e-3
SCORE_TOL = 1e-6
SPECTRAL_TOL = 1e-6
EXACT_FIELDS = ("case_id", "category", "status", "input_domain_status",
                "input_domain_reason", "pure_class_hint", "contract_sha256",
                "provenance_sha256", "passthrough_sha256")
MOD_EXACT_FIELDS = ("class", "final_label", "final_label_source", "accepted", "reason",
                    "cnn_raw_top1", "arbitration_applied", "arbitration_source",
                    "decision_stage")
MOD_NUMERIC_FIELDS = (("scores4", SCORE_TOL), ("cnn_raw_scores4", SCORE_TOL),
                      ("cnn_logits4", LOGIT_TOL), ("baseline_scores4", SCORE_TOL),
                      ("spectral", SPECTRAL_TOL), ("spectral_trigger", SPECTRAL_TOL))


def _numbers(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_numbers(item) for item in value]
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _max_delta(left, right):
    if left is None and right is None:
        return 0.0
    if left is None or right is None:
        return float("inf")
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return float("inf")
        return max((_max_delta(left[key], right[key]) for key in left), default=0.0)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return float("inf")
        return max((_max_delta(a, b) for a, b in zip(left, right)), default=0.0)
    try:
        return abs(float(left) - float(right))
    except (TypeError, ValueError):
        return 0.0 if left == right else float("inf")


def compare(baseline, optimized):
    left = {case["case_id"]: case for case in baseline["cases"]}
    right = {case["case_id"]: case for case in optimized["cases"]}
    mismatches = []
    max_deltas = {field: 0.0 for field, _ in MOD_NUMERIC_FIELDS}
    for case_id in sorted(set(left) | set(right)):
        a, b = left.get(case_id), right.get(case_id)
        if a is None or b is None:
            mismatches.append({"case_id": case_id, "issue": "missing side"})
            continue
        for field in EXACT_FIELDS:
            if a.get(field) != b.get(field):
                mismatches.append({"case_id": case_id, "field": field,
                                   "baseline": a.get(field), "optimized": b.get(field)})
        for field in MOD_EXACT_FIELDS:
            if a["modulation"].get(field) != b["modulation"].get(field):
                mismatches.append({"case_id": case_id, "field": "modulation." + field,
                                   "baseline": a["modulation"].get(field),
                                   "optimized": b["modulation"].get(field)})
        if a["modulation"].get("arbitration") != b["modulation"].get("arbitration"):
            mismatches.append({"case_id": case_id, "field": "modulation.arbitration"})
        for field, tolerance in MOD_NUMERIC_FIELDS:
            delta = _max_delta(_numbers(a["modulation"].get(field)),
                               _numbers(b["modulation"].get(field)))
            max_deltas[field] = max(max_deltas[field], delta)
            if delta > tolerance:
                mismatches.append({"case_id": case_id,
                                   "field": "modulation." + field,
                                   "delta": delta, "tolerance": tolerance})
    return {
        "schema": "ml-performance-parity-compare-v1",
        "cases": len(left),
        "baseline_cases": len(left),
        "optimized_cases": len(right),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:30],
        "max_deltas": max_deltas,
        "tolerances": {"cnn_logits4": LOGIT_TOL, "scores4": SCORE_TOL,
                       "cnn_raw_scores4": SCORE_TOL,
                       "baseline_scores4": SCORE_TOL, "spectral": SPECTRAL_TOL,
                       "spectral_trigger": SPECTRAL_TOL},
        "status": "PASS" if not mismatches else "FAIL",
        "note": ("labels/reasons/arbitration/passthrough/contract/provenance must be exact; "
                 "numeric fields use the existing full-path tolerances"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--optimized", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    optimized = json.loads(args.optimized.read_text(encoding="utf-8"))
    document = compare(baseline, optimized)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(json.dumps({key: document[key] for key in
                      ("status", "cases", "mismatch_count", "max_deltas")}, indent=1))
    return 0 if document["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
