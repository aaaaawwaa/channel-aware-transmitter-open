"""Pytest fixtures for the synthetic data generator / reference check tests.

The fixtures build a *tiny* smoke dataset from a reduced copy of the frozen config
so the whole generate -> reference-check loop runs in well under a second, while
still exercising every frozen code path (4 classes, 12 session kinds, gate kinds,
clipping, duplicate scans).

Non-fixture helpers live in ``data_test_support.py`` (unique module name) to
avoid cross-directory ``conftest`` import collisions when several ml_training
test directories are collected in one pytest run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from data_test_support import (  # noqa: E402
    BASE_CONFIG_PATH, REPO_ROOT, copy_dataset, failing_check_names, read_manifest,
    read_npz, rewrite_npz, tiny_config, write_manifest,
)

import generate  # noqa: E402
import reference_check  # noqa: E402

__all__ = ["copy_dataset", "read_manifest", "write_manifest", "read_npz", "rewrite_npz",
           "failing_check_names"]


@pytest.fixture(scope="session")
def tiny_config_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("data-gen-config") / "first_round_tiny.json"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(tiny_config(), handle, indent=2)
        handle.write("\n")
    return path


@pytest.fixture(scope="session")
def tiny_dataset(tmp_path_factory, tiny_config_path) -> dict:
    out_dir = tmp_path_factory.mktemp("data-gen-dataset") / "sim-smoke"
    result = generate.generate_dataset(tiny_config_path, "smoke", out_dir)
    return {
        "dir": out_dir,
        "config_path": tiny_config_path,
        "manifest": result["manifest"],
        "manifest_sha256": result["manifest_sha256"],
        "elapsed_sec": result["elapsed_sec"],
    }


@pytest.fixture(scope="session")
def tiny_report(tiny_dataset) -> dict:
    return reference_check.run_check(
        tiny_dataset["dir"], config_path=tiny_dataset["config_path"],
        out_json=None, out_md=None,
    )
