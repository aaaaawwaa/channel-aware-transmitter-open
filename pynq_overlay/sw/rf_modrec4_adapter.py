"""Vendored frozen 4-class modulation adapter for cnn-bpsk-modrec4-v1.

This is the frozen ``ml_training/integration_candidate`` adapter vendored into the
release: relative ``ml_training`` runtime imports became flat ``rf_modrec4_*``
imports and ``from_release`` loads the three attested model JSONs.  The inference /
arbitration / passthrough logic is unchanged; no retraining, no threshold change,
no six-class probability synthesis, no environment/demod overwrite.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from rf_modrec4_gate import apply_gate, softmax, top_scores
from rf_modrec4_iq import read_frame
from rf_modrec4_predictor import CLASSES as MOD4_CLASSES
from rf_modrec4_predictor import Round2Predictor
from rf_modrec4_preprocess import preprocess_iq
from rf_modrec4_spectral import spectral_features

from rf_modrec4_input_domain import (FROZEN_INPUT_CONTEXT, REASON_INVALID_IQ_FORMAT,
                                     REQUIRED_DOMAIN, decode_raw_frame, validate_context,
                                     validate_iq)

SCHEMA = "ml-modrec-integration-candidate-v1"
BOARD_CONTRACT = "cnn-bpsk-environment-v1"
BOARD_CLASSES = ("TONE", "BPSK", "QPSK", "QAM16", "QAM64", "OTHER")
KNOWN_CLASS_SUBSET = {label: label for label in MOD4_CLASSES}
UNMAPPED_BOARD_CLASSES = ("TONE", "OTHER")
UNKNOWN = "UNKNOWN"
CONTRACT_SCHEMA = "modrec4-board-contract-map-v1"
STATUS_OK = "OK"
STATUS_UNAVAILABLE = "UNAVAILABLE"


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _before_decision(cnn, iq):
    """Frozen round-1 CNN + round-1 gate decision (raw CNN scores before round-2 rules)."""
    x, gate = preprocess_iq(iq)
    result = {"label": "UNKNOWN", "reason": gate.get("reason", ""), "score": 0.0,
              "margin": 0.0, "scores": None, "logits": None,
              "rms": float(gate.get("rms", 0.0))}
    if not gate.get("ok"):
        return result
    if gate["rms"] < cnn.thresholds["min_complex_rms"]:
        result["reason"] = "below_energy_floor"
        return result
    logits = cnn.forward(x)
    scores = softmax(logits)
    decision = apply_gate(scores, MOD4_CLASSES, cnn.thresholds)
    result.update(label=decision["label"], reason=decision["reason"],
                  score=float(decision["score"]), margin=float(decision["margin"]),
                  scores=[float(value) for value in scores],
                  logits=[float(value) for value in logits])
    return result


class ModRec4Adapter:
    """Additive 4-class modulation adapter over the frozen round-2 replay runtime."""

    def __init__(self, package_dir=None, artifact=None):
        if artifact is not None:
            self._init_from_artifact(Path(package_dir), artifact)
            return
        self.package_dir = Path(package_dir) if package_dir is not None else Path(__file__).resolve().parent
        model_dir = self.package_dir
        self.predictor = Round2Predictor(
            rules_path=model_dir / "modrec4_rule_config.json",
            cnn_path=model_dir / "modrec4_cnn_model.json",
            baseline_path=model_dir / "modrec4_baseline_model.json")
        self.rule_path = model_dir / "modrec4_rule_config.json"
        self.package_manifest_sha256 = None
        self.expected_sha256 = None
        self.model_sha256 = {
            "cnn_model.json": _sha256_file(model_dir / "modrec4_cnn_model.json"),
            "baseline_model.json": _sha256_file(model_dir / "modrec4_baseline_model.json"),
            "rule_config.json": _sha256_file(model_dir / "modrec4_rule_config.json"),
        }
        self.rule_config = self.predictor.document
        self.input_context = dict(FROZEN_INPUT_CONTEXT)

    def _init_from_artifact(self, directory, artifact):
        self.package_dir = directory
        self.predictor = Round2Predictor(
            rules_path=directory / artifact["rule_file"],
            cnn_path=directory / artifact["cnn_file"],
            baseline_path=directory / artifact["baseline_file"])
        self.rule_path = directory / artifact["rule_file"]
        self.package_manifest_sha256 = None
        self.expected_sha256 = None
        self.model_sha256 = {
            "cnn_model.json": _sha256_file(directory / artifact["cnn_file"]),
            "baseline_model.json": _sha256_file(directory / artifact["baseline_file"]),
            "rule_config.json": _sha256_file(directory / artifact["rule_file"]),
        }
        self.rule_config = self.predictor.document
        self.input_context = dict(FROZEN_INPUT_CONTEXT)

    @classmethod
    def from_release(cls, directory, artifact):
        return cls(package_dir=directory, artifact=artifact)

    # -- metadata ---------------------------------------------------------
    def contract_map(self):
        rule_path = getattr(self, 'rule_path', self.package_dir / "modrec4_rule_config.json")
        return {
            "schema": CONTRACT_SCHEMA,
            "board_contract": BOARD_CONTRACT,
            "board_classes": list(BOARD_CLASSES),
            "board_head_probabilities": 6,
            "modulation_head_probabilities": 4,
            "modulation_classes": list(MOD4_CLASSES),
            "six_class_probabilities": None,
            "six_class_probabilities_note": (
                "not produced: this adapter has a 4-class head and does not fabricate "
                "TONE/OTHER probabilities or a 6-class distribution"),
            "known_class_subset_mapping": dict(KNOWN_CLASS_SUBSET),
            "environment_side_classes": list(UNMAPPED_BOARD_CLASSES),
            "extension_required": True,
            "output_rule": (
                "modulation.final_label is one of BPSK/QPSK/QAM16/QAM64 or UNKNOWN; it is "
                "never TONE/OTHER. Environmental labels remain the responsibility of the "
                "existing environment result; demod results are not interpreted here."),
            "frozen_rule_config_sha256": _sha256_file(rule_path),
            "input_domain": dict(REQUIRED_DOMAIN),
        }

    def _provenance(self):
        return {
            "schema": SCHEMA,
            "adapter": "ModRec4Adapter",
            "arm_probe_package": str(self.package_dir.name),
            "arm_probe_manifest_sha256": self.package_manifest_sha256,
            "arm_probe_expected_sha256": self.expected_sha256,
            "model_sha256": dict(self.model_sha256),
            "contract": self.contract_map(),
            "input_context_source": (
                "caller-provided capture metadata; never inferred from IQ or prediction"),
        }

    def _passthrough(self, environment, demod, six_class):
        return {
            "environment": copy.deepcopy(environment),
            "demod": copy.deepcopy(demod),
            "six_class": copy.deepcopy(six_class),
            "provided": {"environment": environment is not None,
                         "demod": demod is not None,
                         "six_class": six_class is not None},
            "note": ("existing product results are passed through unchanged; they are not "
                     "read for the modulation decision and are never overwritten"),
        }

    @staticmethod
    def _unavailable_modulation(reason, final_label_source="unavailable"):
        return {
            "class": "UNKNOWN",
            "final_label": "UNKNOWN",
            "final_label_source": final_label_source,
            "accepted": False,
            "reason": reason,
            "scores4": None,
            "decision_scores4": None,
            "cnn_raw_scores4": None,
            "cnn_scores4": None,
            "cnn_raw_top1": None,
            "cnn_logits4": None,
            "logits4": None,
            "arbitration_applied": False,
            "arbitration_source": "none",
            "arbitration_detail": None,
            "arbitration": None,
            "baseline_scores4": None,
            "spectral": None,
            "spectral_trigger": None,
            "decision_stage": "input_domain_unavailable",
            "cnn_raw_scores_basis": (
                "none: input domain unavailable, model was not run and no scores were fabricated"),
        }

    def _unavailable_result(self, frame_id, domain, environment, demod, six_class,
                            input_info=None):
        return {
            "schema": SCHEMA,
            "status": STATUS_UNAVAILABLE,
            "frame_id": frame_id,
            "input_domain": domain,
            "input": input_info or {"bytes": None, "shape": None, "dtype": None,
                                    "rms": None, "gate_ok": None, "gate_reason": ""},
            "modulation": self._unavailable_modulation(domain.get("reason", "unavailable")),
            "contract": self.contract_map(),
            "passthrough": self._passthrough(environment, demod, six_class),
            "provenance": self._provenance(),
        }

    # -- evaluation -------------------------------------------------------
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

    def evaluate_bytes(self, raw_bytes, context=None, environment=None, demod=None,
                       six_class=None, frame_id=None):
        """Validate/decode an explicit ``s16_iq_le`` byte frame and evaluate it."""
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
                                            six_class,
                                            {"bytes": len(raw_bytes)
                                             if isinstance(raw_bytes, (bytes, bytearray))
                                             else None,
                                             "shape": None, "dtype": None,
                                             "rms": None, "gate_ok": None,
                                             "gate_reason": ""})
        values, frame_check = decode_raw_frame(raw_bytes)
        if not frame_check["ok"]:
            domain = {"required": dict(REQUIRED_DOMAIN), "status": STATUS_UNAVAILABLE,
                      "reason": frame_check["reason"],
                      "missing_fields": [],
                      "observed": frame_check["details"].get("observed"),
                      "required_values": frame_check["details"],
                      "context_source": ("caller-provided capture metadata; never inferred "
                                         "from IQ or prediction")}
            return self._unavailable_result(frame_id, domain, environment, demod,
                                            six_class,
                                            {"bytes": frame_check["details"].get(
                                                "observed", {}).get("bytes"),
                                             "shape": None, "dtype": None,
                                             "rms": None, "gate_ok": None,
                                             "gate_reason": ""})
        return self.evaluate_iq(values, context=context, environment=environment,
                                demod=demod, six_class=six_class, frame_id=frame_id)

    def evaluate_frame(self, path, context=None, environment=None, demod=None,
                       six_class=None, frame_id=None):
        raw = Path(path).read_bytes()
        return self.evaluate_bytes(raw, context=context, environment=environment,
                                   demod=demod, six_class=six_class,
                                   frame_id=frame_id if frame_id is not None
                                   else Path(path).stem)

    def to_product_fields(self, result):
        """Minimal additive fields a product payload would carry (see PRODUCT_INTEGRATION_DIFF)."""
        modulation = result["modulation"]
        contract = result["contract"]
        return {
            "modrec4": {
                "schema": result["schema"],
                "status": result["status"],
                "final_label": modulation["final_label"],
                "final_label_source": modulation["final_label_source"],
                "accepted": modulation["accepted"],
                "reason": modulation["reason"],
                "cnn_raw_top1": modulation["cnn_raw_top1"],
                "cnn_raw_scores4": modulation["cnn_raw_scores4"],
                "cnn_logits4": modulation["cnn_logits4"],
                "arbitration_source": modulation["arbitration_source"],
                "arbitration_applied": modulation["arbitration_applied"],
                "six_class_probabilities": contract["six_class_probabilities"],
                "input_domain_status": result["input_domain"]["status"],
                "input_domain_reason": result["input_domain"]["reason"],
                "contract": {
                    "board_contract": contract["board_contract"],
                    "modulation_head_probabilities":
                        contract["modulation_head_probabilities"],
                    "six_class_probabilities": contract["six_class_probabilities"],
                    "known_class_subset_mapping":
                        contract["known_class_subset_mapping"],
                    "extension_required": contract["extension_required"],
                },
            }
        }
