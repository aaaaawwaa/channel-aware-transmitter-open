#!/usr/bin/env python3
"""Adapter-only timing by sample class on the 43 frozen arm_probe frames.

  python measure_groups.py --mode baseline  --candidate <release> --out results/groups_before.json
  python measure_groups.py --mode optimized --candidate <release> --out results/groups_after.json

Categories: clean (normal/known), rejection (gate OFF/tone/noise/clip), stress (narrowband).
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
ARM = ROOT / "ml_training" / "arm_probe"
FROZEN_CONTEXT = {"format": "s16_iq_le", "samples": 4096, "sample_rate_hz": 60_000_000,
                  "decimation": 1, "symbol_rate_hz": 3_000_000, "shaping": "RECT"}


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * q
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    frac = index - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "optimized"), required=True)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--arm-dir", type=Path, default=ARM)
    args = parser.parse_args(argv)
    release = args.candidate.resolve()
    sys.path.insert(0, str(release))
    sys.path.insert(0, str(PACKAGE))
    sys.dont_write_bytecode = True
    if args.mode == "baseline":
        import rf_modrec4_adapter as adapter_mod
    else:
        import optimized_adapter as adapter_mod
    import merged_release
    manifest = merged_release.verify_release(release, require_software=True)
    artifact = manifest[merged_release.MODREC4_MODEL_KEY]
    adapter = adapter_mod.ModRec4Adapter.from_release(release, artifact)
    arm_manifest = json.loads((Path(args.arm_dir) / "input_manifest.json").read_text(
        encoding="utf-8"))
    cases = []
    for entry in arm_manifest["frames"]:
        group = entry["group"]
        category = ("clean" if group.startswith("clean_") else
                    "rejection" if group.startswith("gate_") else
                    "stress" if group.startswith("stress_") else "other")
        iq = np.fromfile(Path(args.arm_dir) / entry["path"], dtype="<i2").reshape(4096, 2)
        cases.append({"id": entry["id"], "category": category, "iq": iq})
    samples = {category: [] for category in {case["category"] for case in cases}}
    per_frame = {}
    for _ in range(max(0, args.warmup)):
        for case in cases:
            adapter.evaluate_iq(case["iq"], context=dict(FROZEN_CONTEXT),
                                frame_id=case["id"])
    for _ in range(max(1, args.repeats)):
        for case in cases:
            begin = time.perf_counter()
            adapter.evaluate_iq(case["iq"], context=dict(FROZEN_CONTEXT),
                                frame_id=case["id"])
            elapsed = (time.perf_counter() - begin) * 1000.0
            samples[case["category"]].append(elapsed)
            per_frame.setdefault(case["id"], []).append(elapsed)
    by_category = {category: {
        "samples": len(values),
        "median_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "min_ms": min(values), "max_ms": max(values),
    } for category, values in samples.items()}
    all_values = [value for values in samples.values() for value in values]
    result = {
        "schema": "ml-performance-group-measure-v1",
        "mode": args.mode,
        "candidate": str(release.relative_to(ROOT)).replace("\\", "/"),
        "host": {"python": sys.version.split()[0], "numpy": np.__version__,
                 "platform": platform.platform(), "machine": platform.machine()},
        "repeats": args.repeats,
        "frames": len(cases),
        "by_category": by_category,
        "overall": {"samples": len(all_values),
                    "median_ms": percentile(all_values, 0.5),
                    "p95_ms": percentile(all_values, 0.95)},
        "per_frame": {frame: {"samples": len(values),
                              "median_ms": percentile(values, 0.5),
                              "p95_ms": percentile(values, 0.95)}
                      for frame, values in per_frame.items()},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({"mode": args.mode, "frames": len(cases),
                      "by_category": by_category, "overall": result["overall"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
