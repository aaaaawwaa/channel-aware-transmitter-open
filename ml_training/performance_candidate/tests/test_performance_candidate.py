"""Acceptance tests for the performance optimization prototype (host-only)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "ml_training" / "performance_candidate"
RESULTS = PACKAGE / "results"
CANDIDATE = (ROOT / "evidence" / "rf-teaching" /
             "s2-46db-modrec4-product-2026-10-21" / "candidate" / "releases" / "v3" /
             "out" / "releases" / "099174be0cb38a139ea95926394fcf0e")
FRAMES = (ROOT / "evidence" / "rf-teaching" /
          "s2-46db-80mhz-narrowband-2026-10-21" / "raw")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.performance_candidate import compare_cases  # noqa: E402


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def summary():
    return load_json(RESULTS / "summary.json")


def test_reference_snapshot_is_byte_identical_to_candidate():
    for path in sorted((PACKAGE / "reference").iterdir()):
        if not path.is_file():
            continue
        source = CANDIDATE / path.name
        assert source.is_file(), path.name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == \
            hashlib.sha256(source.read_bytes()).hexdigest(), path.name


def test_parity_has_no_mismatch_and_exact_numeric_values():
    parity = load_json(RESULTS / "parity.json")
    assert parity["status"] == "PASS"
    assert parity["cases"] == 70
    assert parity["mismatch_count"] == 0
    assert all(value == 0.0 for value in parity["max_deltas"].values())


def test_case_categories_cover_clean_rejection_stress_and_boundaries():
    baseline = load_json(RESULTS / "cases_baseline.json")
    counts = baseline["counts"]["by_category"]
    assert counts == {"arm_clean": 28, "arm_rejection": 12, "arm_stress": 3,
                      "boundary": 17, "candidate": 4, "fixture_invalid": 2,
                      "fixture_valid": 4}
    by_id = {case["case_id"]: case for case in baseline["cases"]}
    assert by_id["boundary/missing_context"]["input_domain_reason"] == \
        "missing_input_context"
    assert by_id["boundary/sample_rate_30m"]["input_domain_reason"] == \
        "unsupported_sampling_domain"
    assert by_id["boundary/shaping_rrc"]["input_domain_reason"] == \
        "unsupported_symbol_domain"
    assert by_id["boundary/range_3000"]["input_domain_reason"] == "invalid_iq_format"
    assert by_id["boundary/rails"]["modulation"]["reason"] == "clipping"
    # The 43-frame arm set includes clean, gate rejection and stress.
    assert len([case for case in baseline["cases"]
                if case["category"] == "arm_clean"]) == 28
    assert len([case for case in baseline["cases"]
                if case["category"] == "arm_rejection"]) == 12
    assert len([case for case in baseline["cases"]
                if case["category"] == "arm_stress"]) == 3


def test_fullpath_parity_is_exact():
    parity = load_json(RESULTS / "fullpath_parity.json")
    assert parity["status"] == "PASS"
    assert parity["mismatch_count"] == 0
    assert len(parity["frames"]) == 4
    for row in parity["frames"]:
        assert row["status"] == "PASS"
        assert row["six_score_delta"] == 0.0
        assert row["scores_delta"] == 0.0
        assert row["logits_delta"] == 0.0
        assert row["spectral_delta"] == 0.0


def test_host_timing_improves_without_claiming_arm():
    timing = load_json(RESULTS / "summary.json")["timing_comparison"]
    assert timing["modrec4_stage"]["after_median_ms"] < timing["modrec4_stage"]["before_median_ms"]
    assert timing["fullpath_total"]["after_median_ms"] < timing["fullpath_total"]["before_median_ms"]
    assert timing["adapter_only"]["after_median_ms"] < timing["adapter_only"]["before_median_ms"]
    for item in timing["by_category"].values():
        assert item["after_median_ms"] < item["before_median_ms"]
    report = (PACKAGE / "reports" / "performance_report.md").read_text(encoding="utf-8")
    assert "不宣称 ARM 已加速" in report
    assert "arm_compare.py --dry-run" in report
    assert "arm_compare.py --execute" in report


def test_fresh_optimized_case_run_matches_baseline():
    """Exercise the optimized code, not only the committed JSON."""
    with tempfile.TemporaryDirectory(prefix="perf-parity-") as temp:
        out = Path(temp) / "optimized.json"
        completed = subprocess.run(
            [sys.executable, str(PACKAGE / "eval_cases.py"), "--mode", "optimized",
             "--candidate", str(CANDIDATE), "--frames-dir", str(FRAMES),
             "--out", str(out)],
            cwd=str(ROOT), capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        fresh = load_json(out)
        baseline = load_json(RESULTS / "cases_baseline.json")
        comparison = compare_cases.compare(baseline, fresh)
        assert comparison["status"] == "PASS"
        assert comparison["mismatch_count"] == 0
        assert all(value == 0.0 for value in comparison["max_deltas"].values())


def test_optimization_evidence_and_docs_exist():
    profile = load_json(RESULTS / "profile_before.json")
    calls = profile["adapter_calls_per_frame"]
    assert all(row["preprocess_iq"] == 4 for row in calls.values())
    assert all(row["cnn.forward"] == 3 for row in calls.values())
    assert all(row["spectral_features"] == 2 for row in calls.values())
    assert all(row["baseline.scores"] == 2 for row in calls.values())
    for relative in ("README.md", "MINIMAL_DIFF.md", "optimized_adapter.py",
                     "optimized_fullpath.py", "reports/performance_report.md"):
        assert (PACKAGE / relative).is_file(), relative
    diff = (PACKAGE / "MINIMAL_DIFF.md").read_text(encoding="utf-8")
    assert "不宣称 ARM 已加速" in diff
    assert "70 个 case" in diff
