"""Shared (non-fixture) helpers for the ``ml_training/data/tests`` suite.

This module deliberately has a **unique file/module name**
(``data_test_support``) instead of living inside ``conftest.py``: when pytest is
invoked on several test directories at once (e.g.
``pytest ml_training/tests ml_training/data/tests ml_training/evaluation/tests``)
each directory may provide its own ``conftest.py``, and a bare
``from conftest import ...`` in a test module can resolve to the wrong
directory's conftest module (observed cross-directory collection ImportError).

Path bootstrapping (repo root + ``ml_training/data`` on ``sys.path``) also lives
here so the test modules only need to import this one helper module.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(DATA_DIR), str(REPO_ROOT), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import generate  # noqa: E402  (path bootstrap above)
import reference_check  # noqa: E402

BASE_CONFIG_PATH = REPO_ROOT / "ml_training" / "config" / "first_round.json"


def tiny_config() -> dict:
    """A reduced copy of the frozen first-round config (same schema/waveform)."""
    with open(BASE_CONFIG_PATH, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    config = copy.deepcopy(config)
    splits = config["dataset"]["smoke"]["splits"]
    splits["train"] = {
        "sessions_per_label": 1,
        "frames_per_session": 6,
        "seed_families": ["train-a"],
        "snr": {"kind": "uniform", "low": -6.0, "high": 18.0},
    }
    splits["val"] = {
        "sessions_per_label": 1,
        "frames_per_session": 4,
        "seed_families": ["val-a"],
        "snr": {"kind": "uniform", "low": -6.0, "high": 18.0},
    }
    splits["test"] = {
        "frames_per_class_per_snr": 3,
        "snr_db_grid": [0.0, 10.0],
        "seed_families": ["test-a"],
    }
    splits["gate_cal"] = {"families": ["gate-cal-a"], "frames_per_kind": 4}
    splits["gate_test"] = {"families": ["gate-test-a"], "frames_per_kind": 4}
    return config


def copy_dataset(source: Path, tmp_path: Path) -> Path:
    """Copy a dataset to a fresh directory with the same directory name."""
    target = tmp_path / source.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target


def read_manifest(dataset_dir: Path) -> dict:
    with open(dataset_dir / "manifest.json", "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_manifest(dataset_dir: Path, manifest: dict) -> None:
    with open(dataset_dir / "manifest.json", "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")


def read_npz(dataset_dir: Path, rel_path: str) -> dict:
    import numpy as np

    with np.load(dataset_dir / rel_path, allow_pickle=False) as archive:
        return {name: np.asarray(archive[name]) for name in archive.files}


def rewrite_npz(dataset_dir: Path, rel_path: str, arrays: dict, update_manifest: bool = False) -> None:
    generate.write_npz_deterministic(dataset_dir / rel_path, arrays)
    if update_manifest:
        manifest = read_manifest(dataset_dir)
        digest = generate.sha256_file(dataset_dir / rel_path)
        for entry in manifest["files"]:
            if entry["path"] == rel_path:
                entry["sha256"] = digest
        write_manifest(dataset_dir, manifest)


def failing_check_names(report: dict) -> list:
    return [check["name"] for check in report["checks"] if not check["passed"]]
