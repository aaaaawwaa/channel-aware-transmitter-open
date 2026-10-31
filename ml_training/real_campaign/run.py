#!/usr/bin/env python3
"""One-command real four-class data acceptance tool.

Usage (from the project root):

    python ml_training/real_campaign/run.py
    python ml_training/real_campaign/run.py --rebuild-fixtures

It imports existing real evidence (read-only), groups sessions, checks duplicate IQ and
TRAIN/VAL/TEST leakage, evaluates the frozen round-2 predictor separately for clean/quality/
stress, validates synthetic fixtures, and writes indexes + a report under
``ml_training/real_campaign/``.

This tool does not acquire, transmit, connect to a board or change product code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.real_campaign import dedupe, evaluate, report  # noqa: E402
from ml_training.real_campaign.fixtures import build_fixtures  # noqa: E402
from ml_training.real_campaign.importer import import_all  # noqa: E402
from ml_training.real_campaign.sources import load_sources  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="sources JSON (default: config/sources.json)")
    parser.add_argument("--package-dir", default=None,
                        help="output package dir (default: ml_training/real_campaign)")
    parser.add_argument("--rebuild-fixtures", action="store_true",
                        help="rebuild synthetic fixtures from the frozen generator output")
    args = parser.parse_args(argv)
    package = Path(args.package_dir) if args.package_dir else PACKAGE
    fixtures_root = package / "fixtures" / "synthetic"
    if args.rebuild_fixtures or not (fixtures_root / "manifest.json").is_file():
        build_fixtures.main()
    fixtures = json.loads((fixtures_root / "manifest.json").read_text(encoding="utf-8"))
    config = load_sources(args.config)
    imported = import_all(config)
    grouping = dedupe.analyze(imported["records"])
    real_eval = evaluate.evaluate_real(imported)
    synthetic_eval = evaluate.evaluate_synthetic(imported)
    report.write_indexes(package, imported, grouping)
    fixture_checks = report._fixture_checks(imported)
    fixture_checks["synthetic_duplicate_group_count"] = len(
        [group for group in grouping["duplicate_groups"]
         if all(record["synthetic"] for record in group["records"])])
    fixture_checks["synthetic_leakage_group_count"] = len(
        [group for group in grouping["leakage_groups"]
         if all(record["synthetic"] for record in group["records"])])
    real_session_count = len([session for session in grouping["sessions"]
                              if not session["synthetic"]])
    synthetic_session_count = len([session for session in grouping["sessions"]
                                   if session["synthetic"]])
    summary, _ = report.write_report(package, config, imported, grouping, real_eval,
                                     synthetic_eval, fixtures, fixture_checks)
    payload = {
        "status": summary["status"],
        "import_counts": summary["import_counts"],
        "sessions": real_session_count,
        "synthetic_sessions": synthetic_session_count,
        "real_clean_frames": real_eval["clean"]["frames"],
        "real_clean_classes": real_eval["clean"]["counts_by_class"],
        "missing_real_classes": real_eval["clean"]["missing_classes"],
        "four_class_overall_accuracy": real_eval["clean"]["four_class_overall_accuracy"],
        "quality_rejection": {
            "total": real_eval["quality"].get("total"),
            "rejected": real_eval["quality"].get("rejected"),
            "false_accept": real_eval["quality"].get("false_accept"),
        },
        "stress": {"total": real_eval["stress"].get("total"),
                   "accepted": real_eval["stress"].get("accepted")},
        "duplicates": {"groups": grouping["duplicate_group_count"],
                       "real_groups": grouping["real_duplicate_group_count"],
                       "leakage": grouping["leakage_group_count"],
                       "real_leakage": grouping["real_leakage_group_count"]},
        "fixture_checks": {"status": fixture_checks["status"],
                           "passed": fixture_checks["passed"],
                           "total": fixture_checks["total"]},
        "report": {
            "markdown": str((package / "reports" / "real_campaign_report.md").relative_to(ROOT)).replace("\\", "/"),
            "json": str((package / "reports" / "real_campaign_report.json").relative_to(ROOT)).replace("\\", "/"),
            "index": str((package / "index" / "records.json").relative_to(ROOT)).replace("\\", "/"),
        },
    }
    print(json.dumps(payload, indent=1))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
