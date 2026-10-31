#!/usr/bin/env python3
"""Bounded ARM timing / RSS probe for the frozen round-2 replay package.

Run this only on a machine where the package was copied (normally /tmp on the board Linux,
executed by the main project session -- this task does not contact the board).  It is a
pure-Python/NumPy probe, not the C-accelerated product runtime and not the product 6-class
contract.

Bounds (all configurable):
* ``--repeats`` frames per sample (default 20);
* ``--warmup`` warm-up iterations before timing (default 5);
* ``--budget-seconds`` hard wall-clock budget for the timed loop (default 120 s); the loop
  stops early and reports ``budget_exceeded`` instead of overrunning;
* ``--max-frames`` optionally limits the number of distinct frames used for timing.

Writes one JSON file to ``--out`` and prints a short summary.  No files are written outside
that path (recommended: a file under the same /tmp copy, removed during cleanup).
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

import numpy  # noqa: E402

from runtime.iq import read_frame  # noqa: E402
from runtime.predictor import Round2Predictor  # noqa: E402

SCHEMA = "ml-modrec-arm-probe-timing-v1"


def read_rss_kb():
    """Read VmRSS/VmHWM (KiB) from /proc if available; return None on non-Linux systems."""
    values = {}
    path = Path("/proc/self/status")
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("VmRSS:", "VmHWM:", "VmSize:"):
            values[parts[0].rstrip(":")] = int(parts[1])
    return values or None


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def load_frames(manifest):
    frames = []
    for entry in manifest["frames"]:
        iq = read_frame(PACKAGE / entry["path"])
        frames.append((entry, iq))
    return frames


def expected_map():
    path = PACKAGE / "expected" / "host_expected.json"
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    return {frame["id"]: frame for frame in document["frames"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--budget-seconds", type=float, default=120.0)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--out", default="arm_results.json")
    args = parser.parse_args(argv)

    manifest = json.loads((PACKAGE / "input_manifest.json").read_text(encoding="utf-8"))
    manifest_doc = json.loads((PACKAGE / "MANIFEST.json").read_text(encoding="utf-8")) \
        if (PACKAGE / "MANIFEST.json").is_file() else {}
    predictor = Round2Predictor()
    frames = load_frames(manifest)
    if args.max_frames and args.max_frames > 0:
        frames = frames[:args.max_frames]
    expected = expected_map()

    for _ in range(max(0, args.warmup)):
        for _entry, iq in frames:
            predictor.decide(iq)

    rss_before = read_rss_kb()
    started = time.perf_counter()
    samples = []
    per_frame = {entry["id"]: [] for entry, _iq in frames}
    budget_exceeded = False
    for _repeat in range(max(1, args.repeats)):
        for entry, iq in frames:
            if time.perf_counter() - started > args.budget_seconds:
                budget_exceeded = True
                break
            timestamp = time.perf_counter()
            predictor.decide(iq)
            elapsed = (time.perf_counter() - timestamp) * 1000.0
            samples.append(elapsed)
            per_frame[entry["id"]].append(elapsed)
        if budget_exceeded:
            break
    elapsed_seconds = time.perf_counter() - started
    rss_after = read_rss_kb()

    after = {}
    mismatches = []
    for entry, iq in frames:
        decision = predictor.decide(iq)
        after[entry["id"]] = {"label": decision["label"], "reason": decision["reason"],
                              "arbitration_applied": bool(decision["arbitration_applied"])}
        want = expected.get(entry["id"])
        if want is not None:
            want_after = want["after"]
            if (decision["label"] != want_after["label"]
                    or (decision["reason"] or "") != (want_after["reason"] or "")
                    or bool(decision["arbitration_applied"])
                    != bool(want_after["arbitration_applied"])):
                mismatches.append({"id": entry["id"], "expected": want_after,
                                   "actual": after[entry["id"]]})
    timing = {
        "samples": len(samples),
        "median_ms": percentile(samples, 0.5),
        "p95_ms": percentile(samples, 0.95),
        "min_ms": min(samples) if samples else None,
        "max_ms": max(samples) if samples else None,
        "mean_ms": (sum(samples) / len(samples)) if samples else None,
    }
    per_frame_out = []
    for entry, _iq in frames:
        values = per_frame[entry["id"]]
        per_frame_out.append({
            "id": entry["id"], "group": entry["group"], "kind": entry["kind"],
            "n": len(values),
            "median_ms": percentile(values, 0.5),
            "p95_ms": percentile(values, 0.95),
            "min_ms": min(values) if values else None,
            "max_ms": max(values) if values else None,
        })
    result = {
        "schema": SCHEMA,
        "package_manifest_sha256": manifest_doc.get("host_expected_sha256"),
        "probe_note": ("pure Python/NumPy probe of the frozen replay pipeline; not the "
                       "C-accelerated product runtime, not the product 6-class contract, "
                       "and not an ARM product deployment"),
        "host": {
            "python": sys.version.split()[0], "numpy": numpy.__version__,
            "platform": platform.platform(), "machine": platform.machine(),
            "cpu_count": __import__("os").cpu_count(),
        },
        "config": {"repeats": max(1, args.repeats), "warmup": max(0, args.warmup),
                   "budget_seconds": args.budget_seconds,
                   "max_frames": args.max_frames, "frames_total": len(frames)},
        "rss_kb": {"before": rss_before, "after": rss_after},
        "elapsed_seconds": elapsed_seconds,
        "budget_exceeded": budget_exceeded,
        "timing_ms": timing,
        "per_frame": per_frame_out,
        "expected_label_checks": {"checked": len(frames), "mismatch_count": len(mismatches),
                                  "mismatches": mismatches},
        "labels_after": after,
    }
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("[arm-probe] frames=%d samples=%d median_ms=%s p95_ms=%s budget_exceeded=%s"
          % (len(frames), timing["samples"], timing["median_ms"], timing["p95_ms"],
             budget_exceeded))
    print("[arm-probe] rss before=%s after=%s expected_mismatches=%d"
          % (rss_before, rss_after, len(mismatches)))
    print("[arm-probe] wrote", args.out)
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
