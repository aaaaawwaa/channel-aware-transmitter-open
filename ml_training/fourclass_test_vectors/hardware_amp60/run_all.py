#!/usr/bin/env python3
"""One-command offline build/check for the amplitude-60 actual-TX payloads."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
DEFAULT_OUT = PACKAGE / "outputs"
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

import evaluate_frozen  # noqa: E402
import generate  # noqa: E402
import verify  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=PACKAGE / "config" / "hardware_amp60.json")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    generated = generate.generate(args.config, args.out_dir)
    verified = verify.verify(args.out_dir)
    evaluated = evaluate_frozen.evaluate(args.out_dir)
    summary = {
        "schema": "ml-fourclass-hardware-amp60-summary-v1",
        "offline_generated_tx_payload": True,
        "real_capture": False,
        "training_use": False,
        "byte_equality_with_amp1800_vectors_claimed": False,
        "real_rf_iq_byte_equality_required": False,
        "generated": generated,
        "verification": verified,
        "frozen_model": evaluated,
        "status": "COMPLETED" if verified["status"] == "PASS" else "FAIL",
        "note": ("Amplitude fixed at 60; frozen-model rejections/misclassifications are reported "
                 "as-is and were not tuned. Not a real-RF accuracy or BER result."),
    }
    out = Path(args.out_dir) / "summary.json"
    out.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "summary": str(out),
                      "manifest": generated["manifest"],
                      "verification": verified["status"],
                      "frozen_model": evaluated["summary"]}, indent=1))
    return 0 if summary["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
