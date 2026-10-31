#!/usr/bin/env python3
"""One-command CPU-only reproduction: generate -> independent check -> frozen eval -> preview.

Usage:
    python ml_training/fourclass_test_vectors/run_all.py

No device, Pluto, board, network, installation or RF operation is performed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

import evaluate_vectors  # noqa: E402
import generator  # noqa: E402
import independent_check  # noqa: E402
import preview_svg  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PACKAGE / "config" / "vectors.json")
    parser.add_argument("--data-dir", type=Path, default=PACKAGE / "data")
    parser.add_argument("--results-dir", type=Path, default=PACKAGE / "results")
    parser.add_argument("--skip-evaluation", action="store_true",
                        help="only generate/check/preview; no model import")
    args = parser.parse_args(argv)
    data_dir = args.data_dir.resolve()
    results_dir = args.results_dir.resolve()
    results_dir.mkdir(parents=True, exist_ok=True)

    generated = generator.generate(args.config, data_dir)
    checked = independent_check.check(data_dir, results_dir)
    preview = preview_svg.render(data_dir)
    evaluation = None
    if not args.skip_evaluation:
        evaluation = evaluate_vectors.evaluate(data_dir, results_dir)
    summary = {
        "schema": "ml-fourclass-test-vectors-summary-v1",
        "synthetic": True,
        "real_capture": False,
        "counts_as_real_capture": False,
        "real_accuracy_applicable": False,
        "product_qualification": False,
        "generator": generated,
        "independent_check": {
            "status": checked["status"],
            "out": checked["out"],
            "checks": checked["checks"],
        },
        "preview": preview,
        "evaluation": None if evaluation is None else {
            "status": evaluation["status"],
            "out": evaluation["out"],
            "summary": evaluation["summary"],
        },
        "status": "PASS",
        "note": ("SYNTHETIC offline vectors only; no real capture/accuracy/product "
                 "qualification; model output is not truth."),
    }
    if checked["status"] != "PASS":
        summary["status"] = "FAIL"
    if evaluation is not None and evaluation["status"] != "PASS":
        summary["status"] = "FAIL"
    summary_path = results_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
    print(json.dumps({
        "status": summary["status"],
        "summary": str(summary_path),
        "manifest": generated["manifest"],
        "independent_check": checked["status"],
        "evaluation": None if evaluation is None else evaluation["status"],
        "preview": preview["svg"],
    }, indent=1))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
