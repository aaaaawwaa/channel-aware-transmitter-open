"""Single-pass modrec4 adapter prototype (one optimization only).

The reference adapter evaluates one frame with four preprocess calls, three CNN forwards,
two baseline feature passes and two spectral FFTs: ``_before_decision``, ``decide``,
``classify`` and a final spectral recomputation each repeat the same work.  This prototype
computes those common intermediates once and derives the identical ``before``/``after``/
``pure_class_hint``/``spectral`` outputs from them.

Frozen weights, thresholds, input domain, final label, rejection reasons, arbitration and
frame identity semantics are unchanged.  The prototype is an independent copy; no product
file, candidate release, model, evidence or frozen package is modified.
"""
from __future__ import annotations

import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REFERENCE = PACKAGE / "reference"
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

from rf_modrec4_adapter import ModRec4Adapter as _ReferenceAdapter  # noqa: E402
from rf_modrec4_gate import apply_gate, softmax, top_scores  # noqa: E402
from rf_modrec4_input_domain import (FROZEN_INPUT_CONTEXT, REQUIRED_DOMAIN,  # noqa: E402
                                     validate_context, validate_iq)
from rf_modrec4_predictor import CLASSES as MOD4_CLASSES  # noqa: E402
from rf_modrec4_predictor import Round2Predictor  # noqa: E402
from rf_modrec4_preprocess import preprocess_iq  # noqa: E402
from rf_modrec4_spectral import spectral_features  # noqa: E402
from rf_modrec4_adapter import (STATUS_OK, STATUS_UNAVAILABLE, SCHEMA)  # noqa: E402

UNKNOWN = "UNKNOWN"


class ModRec4Adapter(_ReferenceAdapter):
    """Reference output semantics with a single shared preprocess/CNN/features pass."""

    def _spectral_decision(self, features):
        for name in ("tone", "noise"):
            rule = self.predictor.spectral_gate[name]
            value = features[rule["feature"]]
            if (rule["op"] == ">" and value > rule["threshold"]) or \
                    (rule["op"] == "<" and value < rule["threshold"]):
                return name + "_like", float(value)
        return "", None

    def evaluate_iq(self, iq, context=None, environment=None, demod=None, six_class=None,
                    frame_id=None):
        import numpy as np

        values = np.asarray(iq)
        input_info = {"bytes": int(values.size * values.dtype.itemsize)
                      if values.dtype.kind in "iuf" else None,
                      "shape": list(values.shape), "dtype": str(values.dtype),
                      "rms": None, "gate_ok": None, "gate_reason": ""}
        context_check = validate_context(context)
        if not context_check["ok"]:
            domain = {"required": dict(REQUIRED_DOMAIN), "status": STATUS_UNAVAILABLE,
                      "reason": context_check["reason"],
                      "missing_fields": context_check["details"].get("missing_fields", []),
                      "observed": context_check["details"].get("observed"),
                      "required_values": context_check["details"].get("required"),
                      "context_source": ("caller-provided capture metadata; never inferred "
                                         "from IQ or prediction")}
            return self._unavailable_result(frame_id, domain, environment, demod,
                                            six_class, input_info)
        iq_check = validate_iq(values)
        if not iq_check["ok"]:
            domain = {"required": dict(REQUIRED_DOMAIN), "status": STATUS_UNAVAILABLE,
                      "reason": iq_check["reason"], "missing_fields": [],
                      "observed": iq_check["details"].get("observed"),
                      "required_values": iq_check["details"],
                      "context_source": ("caller-provided capture metadata; never inferred "
                                         "from IQ or prediction")}
            return self._unavailable_result(frame_id, domain, environment, demod,
                                            six_class, input_info)

        # ---- the single shared inference pass ---------------------------------
        x, gate = preprocess_iq(values)
        gate_ok = bool(gate.get("ok"))
        rms = float(gate.get("rms", 0.0))
        logits = None
        probs = None
        spectral = None
        before = {"label": UNKNOWN, "reason": gate.get("reason", ""), "score": 0.0,
                  "margin": 0.0, "scores": None, "logits": None, "rms": rms}
        after = {"label": UNKNOWN, "reason": "", "score": 0.0, "margin": 0.0,
                 "scores": None, "gate": gate, "rms": rms,
                 "arbitration_applied": False, "spectral": None}
        pure_class_hint = UNKNOWN
        if gate_ok:
            logits = self.predictor.cnn.forward(x)
            probs = softmax(logits)
            # Raw CNN scores only for the before-decision field once above the energy floor.
            if rms < self.predictor.cnn.thresholds["min_complex_rms"]:
                before["reason"] = "below_energy_floor"
            else:
                raw_decision = apply_gate(probs, MOD4_CLASSES,
                                          self.predictor.cnn.thresholds)
                before.update(label=raw_decision["label"],
                              reason=raw_decision["reason"],
                              score=float(raw_decision["score"]),
                              margin=float(raw_decision["margin"]),
                              scores=[float(value) for value in probs],
                              logits=[float(value) for value in logits])
            # classify() always uses the CNN+baseline arbitration for gate-ok input, even
            # below the energy floor; only its class label is exposed (pure_class_hint).
            baseline_probs = self.predictor.baseline_probabilities(x)
            chosen, applied, detail = self.predictor._arbitrate(probs, baseline_probs)
            pure_class_hint = MOD4_CLASSES[chosen]
            features = spectral_features(x)
            spectral = {key: float(value) for key, value in features.items()}
            after["spectral"] = dict(spectral)
            spectral_reason, spectral_trigger = self._spectral_decision(features)
            if rms < self.predictor.cnn.thresholds["min_complex_rms"]:
                after["reason"] = "below_energy_floor"
            elif spectral_reason:
                after["reason"] = spectral_reason
                after["spectral_trigger"] = spectral_trigger
            else:
                index, score, second = top_scores(probs)
                margin = score - second
                if score < self.predictor.gate["max_softmax"]:
                    after.update(scores=[float(value) for value in probs],
                                 score=score, margin=margin, reason="confidence")
                elif margin < self.predictor.gate["margin"]:
                    after.update(scores=[float(value) for value in probs],
                                 score=score, margin=margin, reason="margin")
                else:
                    after.update(label=MOD4_CLASSES[chosen],
                                 scores=[float(value) for value in probs],
                                 score=score, margin=margin,
                                 arbitration_applied=bool(applied),
                                 arbitration=detail,
                                 baseline_scores=[float(value) for value in baseline_probs])
        else:
            # preprocess failed: decide()/classify() both return UNKNOWN with gate reason.
            after["reason"] = gate.get("reason", "")
            after["spectral"] = None

        final_label = after["label"]
        accepted = final_label in MOD4_CLASSES
        raw_top1 = None
        if before.get("logits") is not None:
            raw_top1 = MOD4_CLASSES[int(np.argmax(before["logits"]))]
        if not accepted:
            final_label_source = "gate_rejection"
        elif after["arbitration_applied"]:
            final_label_source = "baseline_arbitration"
        else:
            final_label_source = "cnn_top1"
        arbitration_source = ("baseline_feature_model"
                              if after["arbitration_applied"] else "none")
        modulation = {
            "class": final_label,
            "final_label": final_label,
            "final_label_source": final_label_source,
            "accepted": accepted,
            "reason": after["reason"],
            "scores4": after.get("scores"),
            "decision_scores4": after.get("scores"),
            "cnn_raw_scores4": before.get("scores"),
            "cnn_scores4": before.get("scores"),
            "cnn_raw_top1": raw_top1,
            "cnn_logits4": before.get("logits"),
            "logits4": before.get("logits"),
            "arbitration_applied": bool(after["arbitration_applied"]),
            "arbitration_source": arbitration_source,
            "arbitration_detail": after.get("arbitration"),
            "arbitration": after.get("arbitration"),
            "baseline_scores4": after.get("baseline_scores"),
            "spectral": spectral,
            "spectral_trigger": after.get("spectral_trigger"),
            "decision_stage": (
                "round2_feature_arbitration" if after["arbitration_applied"]
                else ("gate_rejection" if not accepted else "round1_cnn_gate")),
            "cnn_raw_scores_basis": (
                "round-1 CNN softmax before round-2 arbitration/gate labels"),
        }
        return {
            "schema": SCHEMA,
            "status": STATUS_OK,
            "frame_id": frame_id,
            "input_domain": {
                "required": dict(REQUIRED_DOMAIN),
                "status": "VALID",
                "reason": "",
                "observed": context_check["details"].get("observed"),
                "context_source": ("caller-provided capture metadata; never inferred from "
                                   "IQ or prediction"),
            },
            "input": {
                "shape": list(values.shape), "dtype": str(values.dtype),
                "bytes": int(values.size * values.dtype.itemsize),
                "rms": rms,
                "gate_ok": gate_ok,
                "gate_reason": gate.get("reason", ""),
            },
            "modulation": modulation,
            "contract": self.contract_map(),
            "passthrough": self._passthrough(environment, demod, six_class),
            "provenance": self._provenance(),
            "pure_class_hint": pure_class_hint,
        }
