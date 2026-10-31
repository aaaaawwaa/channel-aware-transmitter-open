"""Generate the round-2 dataset through the frozen round-1 generator, safely.

The frozen generator writes ``ml_training/data/reports/generation-report.md`` for *any*
formal run, so this wrapper backs up the round-1 report first and restores it afterwards.
It also runs the independent reference check against the new dataset.

Usage (from the project root):
    ml_training\\.venv\\Scripts\\python.exe ml_training/round2/scripts/generate_dataset.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ML = ROOT / "ml_training"
CONFIG = ML / "round2" / "config" / "round2.json"
OUT = ML / "round2" / "datasets" / "sim-r2"
ROUND1_REPORT = ML / "data" / "reports" / "generation-report.md"
BACKUP = ML / "round2" / "analysis" / "round1-generation-report.backup.md"
REFERENCE_JSON = ML / "round2" / "analysis" / "reference-check-r2.json"
REFERENCE_MD = ML / "round2" / "analysis" / "reference-check-r2.md"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in argv
    if (OUT / "manifest.json").is_file() and not force:
        print("[round2] dataset already exists, skipping generation: %s" % OUT)
    else:
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        if ROUND1_REPORT.is_file() and not BACKUP.is_file():
            BACKUP.write_bytes(ROUND1_REPORT.read_bytes())
        python = sys.executable
        completed = subprocess.run(
            [python, str(ML / "data" / "generate.py"), "--config", str(CONFIG),
             "--variant", "formal", "--out", str(OUT)], cwd=str(ROOT))
        if ROUND1_REPORT.is_file() and BACKUP.is_file():
            ROUND1_REPORT.write_bytes(BACKUP.read_bytes())
        if completed.returncode != 0:
            raise SystemExit("dataset generation failed with exit code %d" % completed.returncode)
    completed = subprocess.run(
        [sys.executable, str(ML / "data" / "reference_check.py"),
         "--dataset", str(OUT), "--out", str(REFERENCE_JSON), "--md", str(REFERENCE_MD)],
        cwd=str(ROOT))
    if completed.returncode != 0:
        raise SystemExit("reference check failed with exit code %d" % completed.returncode)
    print("[round2] dataset ready: %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
