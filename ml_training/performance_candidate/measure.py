#!/usr/bin/env python3
"""Host full-path timing runner for the performance candidate (offline only).

Run baseline and optimized as separate processes so module names cannot collide:

  python measure.py --mode baseline  --candidate <release> ... --out results/host_before.json
  python measure.py --mode optimized --candidate <release> ... --out results/host_after.json
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


def rss_kb():
    try:
        text = Path("/proc/self/status").read_text()
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        if line.startswith(("VmSize:", "VmHWM:", "VmRSS:")):
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])
    return values or None


def load_average():
    getter = getattr(__import__("os"), "getloadavg", None)
    try:
        return getter() if getter else None
    except OSError:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "optimized"), required=True)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--frames-dir", required=True, type=Path)
    parser.add_argument("--tags", nargs="+", required=True)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    release = args.candidate.resolve()
    sys.path.insert(0, str(release))
    sys.path.insert(0, str(PACKAGE))
    sys.dont_write_bytecode = True
    if args.mode == "baseline":
        import rf_modrec4_fullpath as runner
    else:
        import optimized_fullpath as runner

    frames_dir = args.frames_dir.resolve()
    tags = list(args.tags)

    def run_one(tag):
        return runner.run(frames_dir, tag, release)

    started = time.perf_counter()
    for _ in range(max(0, args.warmup)):
        for tag in tags:
            run_one(tag)
    rss_before = rss_kb()
    load_before = load_average()
    samples, wall_samples, per_frame = [], [], {tag: [] for tag in tags}
    stage_samples = {}
    latest = {}
    budget_exceeded = False
    measured = 0
    for _repeat in range(max(1, args.repeats)):
        for tag in tags:
            if time.perf_counter() - started > 240.0:
                budget_exceeded = True
                break
            begin = time.perf_counter()
            out = run_one(tag)
            elapsed = time.perf_counter() - begin
            measured += 1
            wall_samples.append(elapsed)
            path_seconds = out.get("total_seconds")
            if path_seconds is None:
                path_seconds = sum(float(row["seconds"])
                                   for row in (out.get("stages") or {}).values())
            samples.append(float(path_seconds))
            per_frame[tag].append(float(path_seconds))
            for stage, row in (out.get("stages") or {}).items():
                stage_samples.setdefault(stage, []).append(float(row["seconds"]))
            latest[tag] = out
        if budget_exceeded:
            break
    rss_after = rss_kb()
    result = {
        "schema": "ml-performance-fullpath-measure-v1",
        "mode": args.mode,
        "candidate": str(release.relative_to(PACKAGE.parents[1])).replace("\\", "/"),
        "release_id": (latest.get(tags[0]) or {}).get("release_id") if tags else None,
        "protocol": {"warmup": args.warmup, "repeats": args.repeats,
                     "batch": 1, "frames": tags},
        "host": {"python": sys.version.split()[0],
                 "numpy": __import__("numpy").__version__,
                 "platform": platform.platform(), "machine": platform.machine(),
                 "cpu_count": __import__("os").cpu_count()},
        "rss_kb": {"before": rss_before, "after": rss_after},
        "loadavg": {"before": load_before, "after": load_average()},
        "budget_exceeded": budget_exceeded,
        "measured_runs": measured,
        "timing_ms": {
            "samples": len(samples),
            "median_ms": percentile(samples, 0.5) * 1000.0 if samples else None,
            "p95_ms": percentile(samples, 0.95) * 1000.0 if samples else None,
            "min_ms": min(samples) * 1000.0 if samples else None,
            "max_ms": max(samples) * 1000.0 if samples else None,
            "mean_ms": (sum(samples) / len(samples) * 1000.0) if samples else None,
        },
        "wall_invocation_ms": {
            "samples": len(wall_samples),
            "median_ms": percentile(wall_samples, 0.5) * 1000.0 if wall_samples else None,
            "p95_ms": percentile(wall_samples, 0.95) * 1000.0 if wall_samples else None,
            "min_ms": min(wall_samples) * 1000.0 if wall_samples else None,
            "max_ms": max(wall_samples) * 1000.0 if wall_samples else None,
        },
        "per_frame": {tag: {
            "n": len(values),
            "median_ms": percentile(values, 0.5) * 1000.0,
            "p95_ms": percentile(values, 0.95) * 1000.0,
            "min_ms": min(values) * 1000.0,
            "max_ms": max(values) * 1000.0,
        } for tag, values in per_frame.items()},
        "stages_ms": {stage: {
            "median_ms": percentile(values, 0.5) * 1000.0,
            "p95_ms": percentile(values, 0.95) * 1000.0,
        } for stage, values in stage_samples.items()},
        "results": latest,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(json.dumps({
        "mode": args.mode, "measured_runs": measured,
        "timing_ms": result["timing_ms"],
        "wall_invocation_ms": result["wall_invocation_ms"],
        "stages_ms": result["stages_ms"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
