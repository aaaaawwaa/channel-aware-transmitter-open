#!/usr/bin/env python3
"""One-command reproduction: truth side -> frozen task68 eval -> diagnostics."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RUN = ROOT / "evidence" / "rf-teaching" / "s2-46db-fourclass-real-rx-2026-10-22" / "run"
RESULTS = BASE / "results"

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.fourclass_qam16_diagnosis import build_truth_side, diagnose  # noqa: E402
from ml_training.real_campaign import task68  # noqa: E402


def main():
    truth = build_truth_side.build()
    eval_doc = task68.run(
        frames_root=RUN,
        truth_file=truth["truth_file"],
        out_dir=RESULTS / "task68_eval",
        source_id="task68-real-rx",
        split="TEST",
    )
    diagnose.main()
    summary = {
        "truth": truth,
        "task68_status": eval_doc.get("status"),
        "task68_out": str(RESULTS / "task68_eval" / "task68_result.json"),
        "diagnosis_out": str(RESULTS / "diagnosis.json"),
        "report": str(BASE / "report.md"),
        "note": "real raw results are authoritative; transforms are diagnostics only",
    }
    (RESULTS / "run_summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
