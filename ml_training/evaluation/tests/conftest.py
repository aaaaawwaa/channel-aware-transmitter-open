"""pytest bootstrap for the evaluation tests.

Run from the project root, e.g.:

    .\\ml_training\\.venv\\Scripts\\python.exe -m pytest ml_training\\evaluation\\tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SIM_SMALL = FIXTURES / "sim-small"
FAKE_MODEL = FIXTURES / "fake_model"
FAKE_EVIDENCE = FIXTURES / "real-evidence"
FAKE_CANONICAL = FAKE_EVIDENCE / "campaign5-val-followup" / "download"
FAKE_CAMPAIGN4 = FAKE_EVIDENCE / "campaign4" / "download"
REAL_EVIDENCE = (ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18" /
                 "campaign5-val-followup" / "download" / "train-01" / "normal-01.json")


@pytest.fixture(scope="session")
def sim_small():
    assert (SIM_SMALL / "manifest.json").is_file(), (
        "evaluation fixtures are missing; run "
        "ml_training/evaluation/tests/fixtures/_build_fixtures.py")
    return SIM_SMALL


@pytest.fixture(scope="session")
def fake_model():
    assert (FAKE_MODEL / "model.json").is_file()
    return FAKE_MODEL


@pytest.fixture(scope="session")
def fake_canonical():
    assert (FAKE_CANONICAL / "train-01" / "session-manifest.json").is_file()
    return FAKE_CANONICAL


@pytest.fixture(scope="session")
def fake_campaign4():
    return FAKE_CAMPAIGN4
