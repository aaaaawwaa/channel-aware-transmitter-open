#!/usr/bin/env python3
"""Adapter-only host timing runner (separate process per mode).

  python measure_adapter.py --mode baseline  --candidate <release> ... --out ...
  python measure_adapter.py --mode optimized --candidate <release> ... --out ...
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


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
    parser.add_argument("--frames-dir", required=True, type=Path)
    parser.add_argument("--tags", nargs="+", required=True)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    release = args.candidate.resolve()
    sys.path.insert(0, str(release))
    sys.path.insert(0, str(PACKAGE))
    sys.dont_write_bytecode = True
    if args.mode == "baseline":
        import rf_modrec4_adapter as adapter_mod
        import rf_modrec4_fullpath as loader
    else:
        import optimized_adapter as adapter_mod
        import rf_modrec4_fullpath as loader
    import numpy as np
    import merged_release

    manifest = merged_release.verify_release(release, require_software=True)
    artifact = manifest[merged_release.MODREC4_MODEL_KEY]
    frames = {}
    for tag in args.tags:
        frame, envelope, environment = loader._load_frame(args.frames_dir.resolve(), tag)
        iq = np.frombuffer(frame.payload, dtype="<i2").reshape(frame.samples, 2)
        context = {"format": "s16_iq_le", "samples": frame.samples,
                   "sample_rate_hz": frame.sample_rate_hz,
                   "decimation": frame.decimation,
                   "symbol_rate_hz": envelope.submitted.config.symbol_rate_hz,
                   "shaping": envelope.submitted.config.shaping}
        frames[tag] = (frame, iq, context, environment)
    construct_ms = []
    started = time.perf_counter()
    adapter = adapter_mod.ModRec4Adapter.from_release(release, artifact)
    construct_ms.append((time.perf_counter() - started) * 1000.0)
    per_tag = {tag: [] for tag in args.tags}
    for _ in range(max(0, args.warmup)):
        for tag in args.tags:
            frame, iq, context, environment = frames[tag]
            adapter.evaluate_iq(iq, context=context, environment=environment,
                                frame_id=frame.capture_id)
    for _ in range(max(1, args.repeats)):
        for tag in args.tags:
            frame, iq, context, environment = frames[tag]
            begin = time.perf_counter()
            adapter.evaluate_iq(iq, context=context, environment=environment,
                                frame_id=frame.capture_id)
            per_tag[tag].append((time.perf_counter() - begin) * 1000.0)
    all_values = [value for values in per_tag.values() for value in values]
    result = {
        "schema": "ml-performance-adapter-measure-v1",
        "mode": args.mode,
        "candidate": str(release.relative_to(PACKAGE.parents[1])).replace("\\", "/"),
        "host": {"python": sys.version.split()[0],
                 "numpy": np.__version__, "platform": platform.platform(),
                 "machine": platform.machine()},
        "adapter_construct_ms": construct_ms[0],
        "repeats": args.repeats,
        "per_frame": {tag: {
            "n": len(values),
            "median_ms": percentile(values, 0.5),
            "p95_ms": percentile(values, 0.95),
            "min_ms": min(values), "max_ms": max(values),
        } for tag, values in per_tag.items()},
        "overall": {
            "samples": len(all_values),
            "median_ms": percentile(all_values, 0.5),
            "p95_ms": percentile(all_values, 0.95),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({"mode": args.mode, "construct_ms": construct_ms[0],
                      "overall": result["overall"],
                      "per_frame": result["per_frame"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
