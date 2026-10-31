#!/usr/bin/env python3
"""One-command, per-release isolated live-worker contract smoke.

Every release is checked in its own child Python process so modules from one
release can never be reused for another.  No device, daemon, socket, arm, TX,
capture or RF operation is performed; see ``worker_smoke.py`` for the test client
that forbids hardware interaction.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
DEFAULT_AD37 = (ROOT / "evidence" / "rf-teaching" /
                "s2-46db-modrec4-product-2026-10-21" / "candidate" / "releases" /
                "v11" / "out" / "releases" /
                "ad37bad4f6b0a6e3ba707a95be7fc196")
DEFAULT_OUT = PACKAGE / "results"


def run_release(release, out_root):
    release = Path(release).resolve()
    child_out = Path(out_root).resolve() / release.name
    child_out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-B", str(PACKAGE / "worker_smoke.py"),
           "--release", str(release), "--out-dir", str(child_out)]
    completed = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                               env=dict(os.environ,
                                        PYTHONDONTWRITEBYTECODE="1",
                                        PYTHONUNBUFFERED="1"))
    row = {
        "release": str(release),
        "release_name": release.name,
        "out_dir": str(child_out),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "result": None,
    }
    result_path = child_out / "worker_smoke.json"
    if result_path.is_file():
        row["result"] = json.loads(result_path.read_text(encoding="utf-8"))
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, action="append", default=None,
                        help="release directory (repeat for multiple; default ad37bad4)")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    releases = args.release or [DEFAULT_AD37]
    rows = []
    for release in releases:
        rows.append(run_release(release, args.out_root))
    summary = {
        "schema": "ml-live-worker-contract-smoke-batch-v1",
        "no_hardware": True,
        "device_connection": False,
        "rf_operation": False,
        "process_isolation": "one child process per release",
        "releases": rows,
        "status": "COMPLETED" if all(
            row["returncode"] == 0 and row.get("result") is not None
            for row in rows) else "FAIL",
    }
    out_root = Path(args.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / "batch.json"
    out_path.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({
        "status": summary["status"],
        "batch": str(out_path),
        "releases": [{"release": row["release_name"],
                      "returncode": row["returncode"],
                      "status": (row.get("result") or {}).get("status"),
                      "unavailable": (row.get("result") or {}).get(
                          "unavailable_contracts"),
                      "all_available": (row.get("result") or {}).get(
                          "all_contracts_available")}
                     for row in rows],
    }, indent=1))
    return 0 if summary["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
