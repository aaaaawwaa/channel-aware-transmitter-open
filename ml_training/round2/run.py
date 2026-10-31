"""One-command round-2 pipeline (dataset -> rule calibration -> final evaluation).

Usage (from the project root):
    ml_training\\.venv\\Scripts\\python.exe ml_training/round2/run.py all
    ml_training\\.venv\\Scripts\\python.exe ml_training/round2/run.py calibrate
    ml_training\\.venv\\Scripts\\python.exe ml_training/round2/run.py evaluate

``all`` skips dataset generation when ``ml_training/round2/datasets/sim-r2/manifest.json``
already exists (use ``generate --force`` to rebuild).  Calibration uses only the round-2
``val``/``gate_cal`` splits; evaluation uses the untouched round-2 ``test``/``gate_test``
splits plus the read-only real evidence.  No retraining happens in this round.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ML = ROOT / "ml_training"
ROUND2 = ML / "round2"


def _run(command):
    print("[round2] " + " ".join(str(part) for part in command))
    completed = subprocess.run(command, cwd=str(ROOT))
    if completed.returncode != 0:
        raise SystemExit("command failed with exit code %d" % completed.returncode)


def cmd_generate(args):
    command = [sys.executable, str(ROUND2 / "scripts" / "generate_dataset.py")]
    if args.force:
        command.append("--force")
    _run(command)


def cmd_calibrate(_args):
    _run([sys.executable, str(ROUND2 / "scripts" / "calibrate_rules.py")])


def cmd_evaluate(_args):
    _run([sys.executable, str(ROUND2 / "scripts" / "evaluate_round2.py")])
    summary_path = ROUND2 / "evaluation" / "reports" / "round2_sim_metrics.json"
    if summary_path.is_file():
        document = json.loads(summary_path.read_text(encoding="utf-8"))
        sim = document["sim_test"]
        print(json.dumps({
            "sim_before_accuracy": sim["before_accuracy"],
            "sim_after_accuracy": sim["after_accuracy"],
            "gate_after": {kind: value["rejection_rate"]
                           for kind, value in sim["gate_test"]["after"].items()},
            "known_false_rejection_after": sim["gate_test"]["known_test"]["after_rejected"],
            "independent_real_after": document["real_independent_session"]["summary"].get(
                "proposed_bpsk"),
        }, indent=1))


def cmd_all(args):
    if not (ROUND2 / "datasets" / "sim-r2" / "manifest.json").is_file() or args.force:
        cmd_generate(args)
    else:
        print("[round2] dataset exists, skipping generation")
    cmd_calibrate(args)
    cmd_evaluate(args)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "calibrate", "evaluate", "all"):
        child = sub.add_parser(name)
        child.add_argument("--force", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {"generate": cmd_generate, "calibrate": cmd_calibrate,
                "evaluate": cmd_evaluate, "all": cmd_all}
    handlers[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
