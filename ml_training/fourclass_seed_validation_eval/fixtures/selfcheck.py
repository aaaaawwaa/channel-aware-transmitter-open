#!/usr/bin/env python3
"""Self-check the new-seed evaluator against the clearly marked synthetic fixture."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixture_run"
OUT = BASE / "results" / "fixture_eval"
sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.fourclass_seed_validation_eval.seed_validation_eval import run  # noqa: E402


def main():
    doc = run(FIXTURE / "raw", FIXTURE / "fixture_truth.json",
              FIXTURE / "fixture_plan.json", OUT)
    assert doc["status"] == "PARTIAL", doc["status"]
    assert len(doc["frames"]) == 3, len(doc["frames"])
    assert len(doc["invalid_frames"]) == 1, doc["invalid_frames"]
    assert len(doc["valid_frames"]) == 2, len(doc["valid_frames"])
    assert "SYNTHETIC_FIXTURE" in json.dumps(doc, default=str)
    for frame in doc["valid_frames"]:
        for name, model in frame["models"].items():
            assert model["label"] in ("BPSK", "UNKNOWN"), (frame["frame"], name)
    for name, summary in doc["summary"].items():
        assert summary["per_class"]["BPSK"]["correct"] == 1, (name, summary)
        assert summary["off"]["correct_rejection"] == 1, (name, summary)
    print(json.dumps({
        "selfcheck": "PASS",
        "status": doc["status"],
        "frames": len(doc["frames"]),
        "valid": len(doc["valid_frames"]),
        "invalid": len(doc["invalid_frames"]),
        "models": list(doc["summary"]),
        "out": str(OUT / "seed_validation_result.json"),
        "fixture_is_synthetic": True,
    }, indent=1))


if __name__ == "__main__":
    main()
