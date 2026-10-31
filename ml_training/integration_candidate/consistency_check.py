"""Host consistency check: integration adapter vs frozen replay package + ARM evidence sync.

What it does (all host-side, no board access, no replay on hardware):
1. evaluates the adapter on all 43 frames of ``ml_training/arm_probe`` and compares the
   modulation class / acceptance / reason / arbitration / logits / scores / spectral features
   with the frozen ``expected/host_expected.json``;
2. checks the passthrough fields (environment / demod / six-class) are unchanged when supplied;
3. reads the ARM replay result evidence (already executed by the board operator), verifies the
   recorded facts (43 frames / 0 mismatch / P95 63.24 ms / peak 22.6 MiB) and compares the
   fetched board replay results with the same expected outputs;
4. writes ``evidence/arm_replay_evidence.json``, ``results/consistency.json`` and
   ``results/example_output.json``.

This is not a product end-to-end qualification and does not re-run the ARM probe.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ML = ROOT / "ml_training"
ARM_PROBE = ML / "arm_probe"
IC = ML / "integration_candidate"
EVIDENCE = ROOT / "evidence" / "rf-teaching" / "s2-46db-arm-replay-2026-10-21"
ARM_RESULT = EVIDENCE / "result" / "arm-replay-result.json"
NEW_RAW = ROOT / "evidence" / "rf-teaching" / "s2-46db-80mhz-narrowband-2026-10-21" / "raw"
MOD4_CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
LOGIT_TOLERANCE = 1e-3
SCORE_TOLERANCE = 1e-6
SPECTRAL_TOLERANCE = 1e-6

from ml_training.integration_candidate.input_domain import FROZEN_INPUT_CONTEXT  # noqa: E402


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _max_delta(left, right):
    if left is None and right is None:
        return None
    if left is None or right is None:
        return float("inf")
    return max(abs(float(a) - float(b)) for a, b in zip(left, right))


def compare_adapter(expected_doc, manifest):
    from ml_training.integration_candidate.modrec4_adapter import ModRec4Adapter

    adapter = ModRec4Adapter()
    manifest_by_id = {frame["id"]: frame for frame in manifest["frames"]}
    mismatches = []
    max_logit = 0.0
    max_cnn_score = 0.0
    max_after_score = 0.0
    max_spectral = 0.0
    valid_domain = 0
    for expected in expected_doc["frames"]:
        frame_id = expected["id"]
        entry = manifest_by_id[frame_id]
        result = adapter.evaluate_frame(ARM_PROBE / entry["path"],
                                        context=FROZEN_INPUT_CONTEXT, frame_id=frame_id)
        if result["status"] != "OK":
            mismatches.append({"id": frame_id, "field": "status",
                               "adapter": result["status"], "expected": "OK"})
            continue
        if result["input_domain"]["status"] == "VALID":
            valid_domain += 1
        else:
            mismatches.append({"id": frame_id, "field": "input_domain",
                               "adapter": result["input_domain"]})
        modulation = result["modulation"]
        expected_after = expected["after"]
        expected_before = expected["before"]
        expected_class = expected_after["label"]
        expected_accepted = expected_class in MOD4_CLASSES
        if modulation["class"] != expected_class:
            mismatches.append({"id": frame_id, "field": "class",
                               "adapter": modulation["class"],
                               "expected": expected_class})
        if modulation["final_label"] != expected_class:
            mismatches.append({"id": frame_id, "field": "final_label",
                               "adapter": modulation["final_label"],
                               "expected": expected_class})
        expected_source = ("gate_rejection" if not expected_accepted else
                           ("baseline_arbitration"
                            if expected_after["arbitration_applied"] else "cnn_top1"))
        if modulation["final_label_source"] != expected_source:
            mismatches.append({"id": frame_id, "field": "final_label_source",
                               "adapter": modulation["final_label_source"],
                               "expected": expected_source})
        expected_arbitration_source = ("baseline_feature_model"
                                       if expected_after["arbitration_applied"]
                                       else "none")
        if modulation["arbitration_source"] != expected_arbitration_source:
            mismatches.append({"id": frame_id, "field": "arbitration_source",
                               "adapter": modulation["arbitration_source"],
                               "expected": expected_arbitration_source})
        if modulation["accepted"] != expected_accepted:
            mismatches.append({"id": frame_id, "field": "accepted",
                               "adapter": modulation["accepted"],
                               "expected": expected_accepted})
        if (modulation["reason"] or "") != (expected_after["reason"] or ""):
            mismatches.append({"id": frame_id, "field": "reason",
                               "adapter": modulation["reason"],
                               "expected": expected_after["reason"]})
        if bool(modulation["arbitration_applied"]) != bool(
                expected_after["arbitration_applied"]):
            mismatches.append({"id": frame_id, "field": "arbitration_applied"})
        logit_delta = _max_delta(modulation["logits4"], expected_before["logits"])
        if logit_delta is not None:
            max_logit = max(max_logit, logit_delta)
            if logit_delta > LOGIT_TOLERANCE:
                mismatches.append({"id": frame_id, "field": "logits4",
                                   "delta": logit_delta})
        cnn_score_delta = _max_delta(modulation["cnn_scores4"], expected_before["scores"])
        if cnn_score_delta is not None:
            max_cnn_score = max(max_cnn_score, cnn_score_delta)
            if cnn_score_delta > SCORE_TOLERANCE:
                mismatches.append({"id": frame_id, "field": "cnn_scores4",
                                   "delta": cnn_score_delta})
        after_score_delta = _max_delta(modulation["scores4"], expected_after["scores"])
        if after_score_delta is not None:
            max_after_score = max(max_after_score, after_score_delta)
            if after_score_delta > SCORE_TOLERANCE:
                mismatches.append({"id": frame_id, "field": "scores4",
                                   "delta": after_score_delta})
        spectral_delta = _max_delta(
            list(modulation["spectral"].values()) if modulation["spectral"] else None,
            list(expected["spectral"].values()) if expected["spectral"] else None)
        if spectral_delta is not None:
            max_spectral = max(max_spectral, spectral_delta)
            if spectral_delta > SPECTRAL_TOLERANCE:
                mismatches.append({"id": frame_id, "field": "spectral",
                                   "delta": spectral_delta})
    return {
        "frames": len(expected_doc["frames"]),
        "mismatches": mismatches,
        "mismatch_count": len(mismatches),
        "input_domain_valid_frames": valid_domain,
        "max_logit_delta": max_logit,
        "max_cnn_score_delta": max_cnn_score,
        "max_after_score_delta": max_after_score,
        "max_spectral_delta": max_spectral,
        "tolerances": {"logit": LOGIT_TOLERANCE, "score": SCORE_TOLERANCE,
                       "spectral": SPECTRAL_TOLERANCE},
        "status": "PASS" if not mismatches else "FAIL",
    }


def passthrough_check():
    from ml_training.integration_candidate.modrec4_adapter import ModRec4Adapter

    adapter = ModRec4Adapter()
    environment_doc = load_json(NEW_RAW / "nf-00-normal.environment.json")
    environment = environment_doc.get("environment", environment_doc)
    demod = load_json(NEW_RAW / "nf-00-normal.json").get("demod_result")
    six_class = {"label": "QPSK", "scores": [0.01, 0.02, 0.90, 0.03, 0.01, 0.03],
                 "model_sha256": "f76f2fc0" + "0" * 56, "capture_id": "context-only"}
    environment_before = json.loads(json.dumps(environment))
    demod_before = json.loads(json.dumps(demod))
    six_before = json.loads(json.dumps(six_class))
    entry = None
    manifest = load_json(ARM_PROBE / "input_manifest.json")
    for frame in manifest["frames"]:
        if frame["id"] == "clean-real-new-normal":
            entry = frame
            break
    if entry is None:
        raise SystemExit("example frame missing from arm_probe input manifest")
    result = adapter.evaluate_frame(ARM_PROBE / entry["path"],
                                    context=FROZEN_INPUT_CONTEXT,
                                    environment=environment,
                                    demod=demod, six_class=six_class)
    passthrough = result["passthrough"]
    checks = {
        "status_ok": result["status"] == "OK",
        "input_domain_valid": result["input_domain"]["status"] == "VALID",
        "environment_equal": passthrough["environment"] == environment_before,
        "demod_equal": passthrough["demod"] == demod_before,
        "six_class_equal": passthrough["six_class"] == six_before,
        "inputs_not_mutated": (environment == environment_before
                               and demod == demod_before and six_class == six_before),
        "modulation_separate": "modulation" in result and "passthrough" in result,
        "six_class_probabilities_none": (
            result["contract"]["six_class_probabilities"] is None),
        "modulation_class_allowed": result["modulation"]["final_label"] in
                                    MOD4_CLASSES + ("UNKNOWN",),
        "final_label_source_allowed": result["modulation"]["final_label_source"] in
                                      ("cnn_top1", "baseline_arbitration",
                                       "gate_rejection"),
    }
    return {"frame_id": entry["id"], "checks": checks,
            "status": "PASS" if all(checks.values()) else "FAIL",
            "modulation_class": result["modulation"]["class"],
            "modulation_accepted": result["modulation"]["accepted"]}


def write_example(example_result):
    EXAMPLE = IC / "results" / "example_output.json"
    EXAMPLE.parent.mkdir(parents=True, exist_ok=True)
    EXAMPLE.write_text(json.dumps(example_result, indent=1), encoding="utf-8")


def arm_evidence_sync():
    document = load_json(ARM_RESULT)
    arm = document.get("arm", {})
    board_results_path = EVIDENCE / document["fetched"]["results.json"].replace("\\", "/")
    board_doc = load_json(board_results_path)
    expected = load_json(ARM_PROBE / "expected" / "host_expected.json")
    board_by_id = {frame["id"]: frame for frame in board_doc["frames"]}
    expected_by_id = {frame["id"]: frame for frame in expected["frames"]}
    mismatches = []
    for frame_id, expected_frame in expected_by_id.items():
        actual = board_by_id.get(frame_id)
        if actual is None:
            mismatches.append({"id": frame_id, "issue": "missing in board results"})
            continue
        for section in ("before", "after"):
            for field in ("label", "reason"):
                if (actual[section].get(field) or "") != (expected_frame[section].get(field) or ""):
                    mismatches.append({"id": frame_id, "field": section + "." + field})
            if bool(actual[section].get("arbitration_applied")) != bool(
                    expected_frame[section].get("arbitration_applied")):
                mismatches.append({"id": frame_id, "field": section + ".arbitration_applied"})
    hwm_kb = arm.get("rss_kb", {}).get("after", {}).get("VmHWM")
    facts = {
        "schema": "ml-modrec-arm-replay-evidence-sync-v1",
        "source": str(ARM_RESULT.relative_to(ROOT)).replace("\\", "/"),
        "source_files": {
            "arm-replay-result.json": sha256_file(ARM_RESULT),
            "board-results.json": sha256_file(board_results_path),
            "latency-rss.json": sha256_file(EVIDENCE / "result" / "latency-rss.json"),
            "ARM-REPLAY-RESULT.md": sha256_file(EVIDENCE / "result" / "ARM-REPLAY-RESULT.md"),
        },
        "run_id": document.get("run_id"),
        "status": document.get("status"),
        "scope": document.get("scope"),
        "board_environment": arm.get("host"),
        "board_replay": document.get("board_replay"),
        "frames": arm.get("expected_label_checks", {}).get("checked"),
        "label_mismatch_count": arm.get("expected_label_checks", {}).get("mismatch_count"),
        "timing_ms": arm.get("timing_ms"),
        "rss_vmhwm_kb": hwm_kb,
        "peak_mib": (hwm_kb / 1024.0) if hwm_kb else None,
        "package_manifest_sha256_top_level": document.get("package_manifest_sha256"),
        "package_manifest_sha256_local": sha256_file(ARM_PROBE / "MANIFEST.json"),
        "expected_outputs_sha256_local": sha256_file(
            ARM_PROBE / "expected" / "host_expected.json"),
        "arm_field_package_manifest_sha256": arm.get("package_manifest_sha256"),
        "field_label_note": ("the nested arm.package_manifest_sha256 field equals the local "
                             "expected-outputs JSON hash; the top-level package_manifest_sha256 "
                             "equals the local MANIFEST.json hash"),
        "cleanup": document.get("cleanup"),
        "post_clean": document.get("post_clean"),
        "board_results_vs_expected": {
            "frames": len(expected_by_id),
            "mismatch_count": len(mismatches),
            "mismatches": mismatches[:20],
            "status": "PASS" if not mismatches else "FAIL",
        },
        "not_product_runtime": True,
        "product_end_to_end_qualified": False,
        "no_replay_rerun_by_this_check": True,
    }
    evidence_dir = IC / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "arm_replay_evidence.json").write_text(json.dumps(facts, indent=1),
                                                           encoding="utf-8")
    return facts


def main():
    from ml_training.integration_candidate import negative_cases
    from ml_training.integration_candidate.modrec4_adapter import ModRec4Adapter

    manifest = load_json(ARM_PROBE / "input_manifest.json")
    expected = load_json(ARM_PROBE / "expected" / "host_expected.json")
    adapter_result = compare_adapter(expected, manifest)
    example = passthrough_check()
    arm = arm_evidence_sync()
    adapter = ModRec4Adapter()
    negative = negative_cases.run_cases(adapter)
    # example output for the new normal frame with passthrough context
    environment = load_json(NEW_RAW / "nf-00-normal.environment.json").get("environment")
    demod = load_json(NEW_RAW / "nf-00-normal.json").get("demod_result")
    entry = next(frame for frame in manifest["frames"]
                 if frame["id"] == "clean-real-new-normal")
    example_result = adapter.evaluate_frame(ARM_PROBE / entry["path"],
                                            context=FROZEN_INPUT_CONTEXT,
                                            environment=environment, demod=demod,
                                            six_class={"label": "QPSK",
                                                       "scores": [0.01, 0.02, 0.9, 0.03, 0.01, 0.03]})
    write_example(example_result)
    product_fields = adapter.to_product_fields(example_result)
    checks = {
        "adapter_vs_replay": adapter_result["status"],
        "passthrough": example["status"],
        "negative_input_domain_cases": negative["status"],
        "arm_board_results_vs_expected": arm["board_results_vs_expected"]["status"],
        "contract_no_six_class_probabilities":
            "PASS" if example["checks"]["six_class_probabilities_none"] else "FAIL",
        "frozen_package_manifest_matches_arm_evidence":
            "PASS" if (arm["package_manifest_sha256_top_level"]
                       == arm["package_manifest_sha256_local"]) else "FAIL",
    }
    document = {
        "schema": "ml-modrec-integration-candidate-consistency-v1",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checks": checks,
        "adapter_vs_replay": adapter_result,
        "negative_cases": negative,
        "passthrough_check": example,
        "arm_evidence": arm,
        "product_fields_example": product_fields,
        "product_end_to_end_qualified": False,
        "note": ("host-only consistency check; ARM execution facts are read from the board "
                 "operator's evidence directory and are not re-run here; the probe is not the "
                 "product C runtime or the product 6-class contract"),
        "status": "PASS" if all(value == "PASS" for value in checks.values()) else "FAIL",
    }
    RESULTS = IC / "results"
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "consistency.json").write_text(json.dumps(document, indent=1),
                                               encoding="utf-8")
    print(json.dumps({"checks": checks, "adapter_vs_replay":
                      {key: adapter_result[key] for key in
                       ("frames", "mismatch_count", "input_domain_valid_frames",
                        "max_logit_delta", "max_spectral_delta")},
                      "negative_cases": {"total": negative["cases_total"],
                                         "passed": negative["cases_passed"]},
                      "arm": {"frames": arm["frames"],
                              "label_mismatch_count": arm["label_mismatch_count"],
                              "p95_ms": arm["timing_ms"]["p95_ms"],
                              "peak_mib": arm["peak_mib"],
                              "board_results_vs_expected":
                                  arm["board_results_vs_expected"]["mismatch_count"]},
                      "status": document["status"]}, indent=1))
    return 0 if document["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
