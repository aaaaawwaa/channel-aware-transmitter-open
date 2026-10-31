"""Offline regressions for the comparison path used by the parity driver."""
import copy
import json
from pathlib import Path

import pytest

from scripts.rf_receiver_parity_run import STAGES, compare_results
from scripts import merged_release as R


@pytest.fixture
def payloads():
    retained = (Path(__file__).resolve().parents[2] / "evidence/rf-teaching/s2"
                / "g1-qualification-2026-10-09/parity")
    host, arm = [json.loads((retained / name).read_text(encoding="utf-8"))
                 for name in ("host.json", "arm.json")]
    manifest = {key: copy.deepcopy(host[key]) for key in ("release_id", "software")}
    return host, arm, manifest


def test_retained_exact_four_input_five_stage_parity_passes(payloads):
    result = compare_results(*payloads)
    assert result["passed"] is True
    assert result["compared_inputs"] == 4
    assert result["parity_mismatches"] == []
    assert result["mismatches_per_stage"] == dict.fromkeys(STAGES, 0)


def test_actual_verified_candidate_uses_canonical_baseline_not_file_sha(payloads):
    root = Path(__file__).resolve().parents[2]
    candidate = (root / "evidence/rf-teaching/s2/g1-qualification-2026-10-08"
                 / "candidate/releases" / payloads[0]["release_id"])
    manifest = R.verify_release(candidate, require_software=True)
    canonical = json.loads((candidate / "rf_environment_baseline.json").read_text(
        encoding="utf-8"))["sha256"]
    assert manifest["software"]["rf_environment_baseline.json"] != canonical
    result = compare_results(payloads[0], payloads[1], manifest, expected_baseline=canonical)
    assert result["passed"] is True
    assert result["compared_inputs"] == 4


@pytest.mark.parametrize("side", (0, 1))
@pytest.mark.parametrize("change", ("missing", "extra", "empty"))
def test_record_count_cannot_silently_zip_to_the_shorter_side(payloads, side, change):
    records = payloads[side]["records"]
    if change == "missing":
        records.pop()
    elif change == "extra":
        records.append(copy.deepcopy(records[-1]))
    else:
        records.clear()
    assert compare_results(*payloads)["passed"] is False


@pytest.mark.parametrize("sides", ((0,), (1,), (0, 1)))
@pytest.mark.parametrize("change", ("duplicate", "unknown"))
def test_exact_tag_set_is_required_even_when_both_sides_agree(payloads, sides, change):
    for side in sides:
        records = payloads[side]["records"]
        records[-1]["tag"] = records[0]["tag"] if change == "duplicate" else "other"
    assert compare_results(*payloads)["passed"] is False


@pytest.mark.parametrize("field", ("iq_sha256", "source_release"))
def test_whole_record_metadata_is_compared(payloads, field):
    payloads[1]["records"][0][field] = "different-source"
    result = compare_results(*payloads)
    assert result["passed"] is False
    assert any(field in mismatch for mismatch in result["parity_mismatches"])


@pytest.mark.parametrize("sides", ((0,), (1,), (0, 1)))
@pytest.mark.parametrize("change", ("duplicate", "unlisted", "missing", "input-list"))
def test_per_record_iq_hashes_bind_to_the_declared_unique_inputs(payloads, sides, change):
    for side in sides:
        document = payloads[side]
        if change == "duplicate":
            document["records"][1]["iq_sha256"] = document["records"][0]["iq_sha256"]
            document["input_iq_sha256"] = sorted(row["iq_sha256"] for row in document["records"])
        elif change == "unlisted":
            document["records"][0]["iq_sha256"] = "0" * 64
        elif change == "missing":
            document["records"][0].pop("iq_sha256")
        else:
            document["input_iq_sha256"].pop()
    assert compare_results(*payloads)["passed"] is False


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("change", ("missing", "null", "empty", "declaration"))
def test_all_five_stages_are_required_even_if_both_sides_omit_them(payloads, stage, change):
    for document in payloads[:2]:
        if change == "declaration":
            document["stages"].remove(stage)
        elif change == "missing":
            document["records"][0].pop(stage)
        else:
            document["records"][0][stage] = None if change == "null" else {}
    assert compare_results(*payloads)["passed"] is False


@pytest.mark.parametrize("field", ("release_id", "software"))
@pytest.mark.parametrize("sides", ((0,), (1,), (0, 1)))
def test_both_sides_must_use_the_verified_candidate(payloads, field, sides):
    for side in sides:
        if field == "release_id":
            payloads[side][field] = "different-candidate"
        else:
            name = next(iter(payloads[side][field]))
            payloads[side][field][name] = "0" * 64
    assert compare_results(*payloads)["passed"] is False


def test_baseline_mismatch_is_rejected(payloads):
    payloads[1]["baseline_sha256"] = "0" * 64
    assert compare_results(*payloads)["passed"] is False


def test_matching_baselines_must_still_belong_to_the_verified_candidate(payloads):
    canonical = payloads[0]["baseline_sha256"]
    for document in payloads[:2]:
        document["baseline_sha256"] = "0" * 64
        for row in document["records"]:
            row["environment"]["baseline_sha256"] = "0" * 64
    assert compare_results(*payloads, expected_baseline=canonical)["passed"] is False


def test_missing_baseline_cannot_pass_when_both_sides_omit_it(payloads):
    for document in payloads[:2]:
        document.pop("baseline_sha256")
    assert compare_results(*payloads)["passed"] is False


@pytest.mark.parametrize("side", (0, 1))
def test_missing_record_source_metadata_is_rejected(payloads, side):
    payloads[side]["records"][0].pop("source_release")
    assert compare_results(*payloads)["passed"] is False


@pytest.mark.parametrize("side", (0, 1))
def test_runtime_backend_declarations_remain_host_python_arm_native(payloads, side):
    payloads[side]["native_cnn"] = side == 0
    assert compare_results(*payloads)["passed"] is False


@pytest.mark.parametrize("delta, passed", ((5e-7, True), (2e-6, False), (float("nan"), False)))
def test_existing_finite_measured_float_tolerance_is_preserved(payloads, delta, passed):
    payloads[1]["records"][0]["base"]["complex_dbfs"] = (
        payloads[0]["records"][0]["base"]["complex_dbfs"] + delta)
    result = compare_results(*payloads)
    assert result["passed"] is passed
    assert result["mismatches_per_stage"]["base"] == (0 if passed else 1)


def test_refusal_result_is_compared_as_a_complete_stage(payloads):
    refused = next(row for row in payloads[1]["records"] if row["tag"] == "narrowband")
    assert refused["bpsk"]["reason"]
    refused["bpsk"]["reason"] = "different-refusal"
    result = compare_results(*payloads)
    assert result["passed"] is False
    assert result["mismatches_per_stage"]["bpsk"] == 1
