#!/usr/bin/env python3
"""Verify that CI produced a complete, traceable bitstream build."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "awg" / "build_low_memory.log"
BIT = ROOT / "awg" / "awg.runs" / "impl_1" / "host_bd_wrapper.bit"
HDF = ROOT / "awg" / "host_bd.hdf"
MANIFEST = ROOT / "build_manifest.json"
TIMING = ROOT / "awg" / "awg.runs" / "impl_1" / "host_bd_wrapper_timing_summary_routed.rpt"
REQUIRED_MARKERS = (
    "BD-REGENERATED",
    "OOC-OK",
    "HDF-EXPORTED",
    "SYNTH-OK",
    "GATE2-OK",
    "BITSTREAM-OK",
    "OOC-RESTORED",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fail(message: str) -> int:
    print(f"CI verification failed: {message}", file=sys.stderr)
    return 1


def main() -> int:
    if not LOG.is_file():
        return fail(f"missing build log: {LOG}")
    log_text = LOG.read_text(encoding="utf-8", errors="replace")
    missing = [marker for marker in REQUIRED_MARKERS if marker not in log_text]
    if missing:
        return fail("missing build markers: " + ", ".join(missing))

    for artifact in (BIT, HDF, MANIFEST, TIMING):
        if not artifact.is_file() or artifact.stat().st_size == 0:
            return fail(f"missing or empty artifact: {artifact}")

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return fail(f"invalid build manifest: {exc}")

    expected_path = manifest.get("bitstream")
    if expected_path != BIT.relative_to(ROOT).as_posix():
        return fail(f"manifest bitstream path is {expected_path!r}")

    expected_hash = manifest.get("bitstream_sha256")
    actual_hash = sha256(BIT)
    if expected_hash != actual_hash:
        return fail("manifest bitstream SHA-256 does not match the artifact")

    print("CI verification passed")
    print(f"bitstream: {BIT.relative_to(ROOT)}")
    print(f"sha256: {actual_hash}")
    print("markers: " + ", ".join(REQUIRED_MARKERS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
