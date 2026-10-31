"""Direct negative cases for the adapter input-domain contract.

Each case supplies an invalid format/length/sampling-domain/symbol-domain IQ or context and
checks the adapter returns an explicit ``UNAVAILABLE`` result with the expected reason, no
fabricated final label, no CNN scores/logits and no arbitration, while the model is not run.

Writes ``ml_training/integration_candidate/results/negative_cases.json``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.integration_candidate.input_domain import (  # noqa: E402
    FROZEN_INPUT_CONTEXT, REASON_INVALID_IQ_FORMAT, REASON_INVALID_IQ_LENGTH,
    REASON_MISSING_CONTEXT, REASON_UNSUPPORTED_FORMAT, REASON_UNSUPPORTED_LENGTH,
    REASON_UNSUPPORTED_SAMPLING, REASON_UNSUPPORTED_SYMBOL)
from ml_training.integration_candidate.modrec4_adapter import (  # noqa: E402
    STATUS_UNAVAILABLE, ModRec4Adapter)

ARM_PROBE = ROOT / "ml_training" / "arm_probe"
if str(ARM_PROBE) not in sys.path:
    sys.path.insert(0, str(ARM_PROBE))
from runtime.iq import read_frame  # noqa: E402

BASE_FRAME = ARM_PROBE / "frames" / "clean_real_bpsk" / "clean-real-new-normal.iq"
RESULTS = ROOT / "ml_training" / "integration_candidate" / "results" / "negative_cases.json"


class _NoModelCall:
    def __getattr__(self, name):
        raise AssertionError("model must not be called for invalid input (touched %r)" % name)


def _context(**overrides):
    context = dict(FROZEN_INPUT_CONTEXT)
    context.update(overrides)
    return context


def build_cases(adapter):
    iq = read_frame(BASE_FRAME)
    raw = BASE_FRAME.read_bytes()
    cases = [
        {"id": "missing_context", "kind": "context", "reason": REASON_MISSING_CONTEXT,
         "call": lambda: adapter.evaluate_iq(iq, context=None)},
        {"id": "partial_context_missing_decimation", "kind": "context",
         "reason": REASON_MISSING_CONTEXT,
         "call": lambda: adapter.evaluate_iq(iq, context={key: value for key, value in
                                                          FROZEN_INPUT_CONTEXT.items()
                                                          if key != "decimation"})},
        {"id": "unsupported_format", "kind": "format", "reason": REASON_UNSUPPORTED_FORMAT,
         "call": lambda: adapter.evaluate_iq(iq, context=_context(format="u16_iq_be"))},
        {"id": "unsupported_samples_length", "kind": "length",
         "reason": REASON_UNSUPPORTED_LENGTH,
         "call": lambda: adapter.evaluate_iq(iq, context=_context(samples=2048))},
        {"id": "unsupported_sample_rate", "kind": "sampling",
         "reason": REASON_UNSUPPORTED_SAMPLING,
         "call": lambda: adapter.evaluate_iq(iq, context=_context(sample_rate_hz=30_000_000))},
        {"id": "unsupported_decimation", "kind": "sampling",
         "reason": REASON_UNSUPPORTED_SAMPLING,
         "call": lambda: adapter.evaluate_iq(iq, context=_context(decimation=2))},
        {"id": "unsupported_symbol_rate", "kind": "symbol",
         "reason": REASON_UNSUPPORTED_SYMBOL,
         "call": lambda: adapter.evaluate_iq(iq, context=_context(symbol_rate_hz=2_000_000))},
        {"id": "unsupported_shaping", "kind": "symbol",
         "reason": REASON_UNSUPPORTED_SYMBOL,
         "call": lambda: adapter.evaluate_iq(iq, context=_context(shaping="RRC"))},
        {"id": "invalid_iq_shape", "kind": "iq", "reason": REASON_INVALID_IQ_FORMAT,
         "call": lambda: adapter.evaluate_iq(iq[:-1], context=FROZEN_INPUT_CONTEXT)},
        {"id": "invalid_iq_dtype", "kind": "iq", "reason": REASON_INVALID_IQ_FORMAT,
         "call": lambda: adapter.evaluate_iq(iq.astype(np.float32),
                                             context=FROZEN_INPUT_CONTEXT)},
        {"id": "invalid_iq_range", "kind": "iq", "reason": REASON_INVALID_IQ_FORMAT,
         "call": lambda: adapter.evaluate_iq(np.where(
             np.arange(iq.size).reshape(iq.shape) == 0, 3000, iq).astype(np.int16),
             context=FROZEN_INPUT_CONTEXT)},
        {"id": "invalid_iq_big_endian", "kind": "iq", "reason": REASON_INVALID_IQ_FORMAT,
         "call": lambda: adapter.evaluate_iq(iq.astype(">i2"),
                                             context=FROZEN_INPUT_CONTEXT)},
        {"id": "invalid_raw_length", "kind": "iq_length", "reason": REASON_INVALID_IQ_LENGTH,
         "call": lambda: adapter.evaluate_bytes(raw[:-2], context=FROZEN_INPUT_CONTEXT)},
        {"id": "invalid_raw_not_bytes", "kind": "iq", "reason": REASON_INVALID_IQ_FORMAT,
         "call": lambda: adapter.evaluate_bytes("not-bytes", context=FROZEN_INPUT_CONTEXT)},
    ]
    return iq, raw, cases


def run_cases(adapter=None):
    if adapter is None:
        adapter = ModRec4Adapter()
    iq, raw, cases = build_cases(adapter)
    original_predictor = adapter.predictor
    rows = []
    passed = 0
    try:
        adapter.predictor = _NoModelCall()  # proves invalid inputs never run the model
        for case in cases:
            result = case["call"]()
            modulation = result["modulation"]
            checks = {
                "status_unavailable": result["status"] == STATUS_UNAVAILABLE,
                "reason_matches": modulation["reason"] == case["reason"],
                "input_domain_unavailable": result["input_domain"]["status"] == "UNAVAILABLE",
                "final_label_unknown": modulation["final_label"] == "UNKNOWN",
                "no_cnn_scores": modulation["cnn_raw_scores4"] is None,
                "no_cnn_logits": modulation["cnn_logits4"] is None,
                "no_arbitration": modulation["arbitration_source"] == "none",
            }
            ok = all(checks.values())
            passed += int(ok)
            rows.append({"id": case["id"], "kind": case["kind"],
                         "expected_reason": case["reason"],
                         "actual_reason": modulation["reason"],
                         "status": result["status"], "checks": checks,
                         "pass": ok})
    finally:
        adapter.predictor = original_predictor
    # positive control with the declared context (model is allowed here)
    control = adapter.evaluate_iq(iq, context=FROZEN_INPUT_CONTEXT)
    control_ok = control["status"] == "OK" and control["modulation"]["final_label"] == "BPSK"
    summary = {
        "schema": "ml-modrec-integration-candidate-negative-cases-v1",
        "base_frame": str(BASE_FRAME.relative_to(ROOT)).replace("\\", "/"),
        "cases": rows,
        "cases_total": len(rows),
        "cases_passed": passed,
        "valid_context_control": {
            "status": control["status"],
            "final_label": control["modulation"]["final_label"],
            "accepted": control["modulation"]["accepted"],
            "pass": control_ok,
        },
        "model_not_called_for_invalid_inputs": True,
        "status": "PASS" if passed == len(rows) and control_ok else "FAIL",
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def main():
    summary = run_cases()
    print(json.dumps({"cases_total": summary["cases_total"],
                      "cases_passed": summary["cases_passed"],
                      "control": summary["valid_context_control"],
                      "status": summary["status"]}, indent=1))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
