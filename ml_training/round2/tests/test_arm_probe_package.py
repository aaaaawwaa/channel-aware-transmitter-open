"""Acceptance checks for the standalone ARM replay package ``ml_training/arm_probe``.

Verifies the package manifest/artefact hashes, the frozen input manifest, host expected
outputs (recomputed with the package's own runtime), the clean-directory replay and the
bounded ARM timing script on the host.  No board access and no package rebuild.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "ml_training" / "arm_probe"
FROZEN = {
    "cnn_model": ("ml_training/artifacts/cnn/model.json",
                  "25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc"),
    "baseline_model": ("ml_training/artifacts/baseline/model.json",
                       "caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014"),
    "rule_config": ("ml_training/round2/artifacts/rule_config.json",
                    "9aceebbdb6b9b9a54ad0dd09ebd61935ae8c8fd276ba18d8b9db69e7949619e0"),
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def package_manifest():
    return json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def input_manifest():
    return json.loads((PKG / "input_manifest.json").read_text(encoding="utf-8"))


def test_manifest_frozen_identity(package_manifest):
    assert package_manifest["schema"] == "ml-modrec-arm-probe-manifest-v1"
    assert package_manifest["frames"]["total"] == 43
    assert package_manifest["frames"]["by_group"] == {
        "clean_failure_bpsk": 10, "clean_real_bpsk": 10, "clean_synthetic_4class": 8,
        "gate_off": 3, "gate_tone": 3, "gate_noise": 3, "gate_clip": 3,
        "stress_narrowband": 3,
    }
    for key, (relative, expected) in FROZEN.items():
        assert package_manifest["frozen"][key]["sha256"] == expected
        assert sha256(ROOT / relative) == expected
    for model_name, source_relative in (("cnn_model.json", FROZEN["cnn_model"][0]),
                                        ("baseline_model.json", FROZEN["baseline_model"][0]),
                                        ("rule_config.json", FROZEN["rule_config"][0])):
        assert sha256(PKG / "model" / model_name) == sha256(ROOT / source_relative)


def test_manifest_file_hashes_are_current(package_manifest):
    for item in package_manifest["files"]:
        path = ROOT / item["path"]
        assert path.is_file(), item["path"]
        assert path.stat().st_size == item["bytes"], item["path"]
        assert sha256(path) == item["sha256"], item["path"]
    assert package_manifest["no_device_note"]


def test_input_manifest_frames(input_manifest):
    frames = input_manifest["frames"]
    assert len(frames) == 43
    ids = [frame["id"] for frame in frames]
    assert len(set(ids)) == len(ids)
    for frame in frames:
        path = PKG / frame["path"]
        assert path.is_file()
        assert path.stat().st_size == 16384
        assert sha256(path) == frame["iq_sha256"]
        assert frame["kind"] in ("clean", "gate", "stress")
    for group, item in input_manifest["groups"].items():
        assert item["n"] == len(item["ids"])


def test_package_runtime_reproduces_expected_outputs(input_manifest):
    if str(PKG) not in sys.path:
        sys.path.insert(0, str(PKG))
    import run_replay  # noqa: WPS433

    predictor = run_replay.load_predictor()
    actual = run_replay.evaluate(predictor, input_manifest)
    expected = json.loads((PKG / "expected" / "host_expected.json").read_text(encoding="utf-8"))
    problems = run_replay.compare(expected, actual)
    assert problems == []
    summary = actual["summary"]
    assert summary["clean"]["__total__"]["n"] == 28
    assert summary["clean"]["__total__"]["after_correct"] == 28
    assert summary["clean"]["clean_real_bpsk"]["after_accuracy"] == 1.0
    assert summary["clean"]["clean_failure_bpsk"]["before_correct"] == 0
    assert summary["clean"]["clean_failure_bpsk"]["after_correct"] == 10
    for name in ("gate_off", "gate_tone", "gate_noise", "gate_clip"):
        assert summary["gate"][name]["rejected"] == summary["gate"][name]["n"], name
    assert summary["stress"]["stress_narrowband"]["after_labels"] == ["BPSK", "BPSK", "BPSK"]
    assert "not counted" in summary["stress"]["stress_narrowband"]["note"]


def test_host_checks_are_green(package_manifest):
    checks = package_manifest["host_checks"]
    assert checks["project_cross_check"]["status"] == "PASS"
    assert checks["project_cross_check"]["mismatch_count"] == 0
    assert checks["compatibility_check"]["python_syntax_3_8_ok"] is True
    assert checks["compatibility_check"]["pure_numpy_only"] is True
    assert checks["compatibility_check"]["banned_imports_found"] == []
    assert checks["clean_directory_run"]["status"] == "PASS"
    assert checks["clean_directory_run"]["check_passed"] is True
    compatibility = json.loads((PKG / "host" / "compatibility_check.json")
                               .read_text(encoding="utf-8"))
    assert compatibility["python_syntax_3_8_ok"] is True
    assert compatibility["pure_numpy_only"] is True
    clean_run = json.loads((PKG / "host" / "host_clean_run.json").read_text(encoding="utf-8"))
    assert clean_run["returncode"] == 0
    assert clean_run["problem_count"] == 0


def test_arm_timing_script_runs_on_host():
    with tempfile.TemporaryDirectory(prefix="arm-probe-test-") as temp:
        out = Path(temp) / "arm_results.json"
        completed = subprocess.run(
            [sys.executable, str(PKG / "arm" / "run_arm_probe.py"), "--repeats", "1",
             "--warmup", "0", "--budget-seconds", "10", "--out", str(out)],
            cwd=str(PKG), capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        assert out.is_file()
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["schema"] == "ml-modrec-arm-probe-timing-v1"
        assert result["expected_label_checks"]["mismatch_count"] == 0
        assert result["timing_ms"]["samples"] > 0
        assert result["config"]["frames_total"] == 43
