"""Round-2 predictor: frozen round-1 CNN + interpretable feature guard + spectral gate.

The round-1 artefacts are used **read-only** and are not modified:

* ``ml_training/artifacts/cnn/model.json``       (1,628 parameters, 4-class TinyIQCNN)
* ``ml_training/artifacts/baseline/model.json``  (36-parameter interpretable baseline)

Round-2 adds no parameters and does not retrain anything:

* **arbitration**: when the CNN top-1 is BPSK or QPSK, the interpretable feature
  baseline arbitrates between BPSK and QPSK if its BPSK-vs-QPSK probability margin
  is larger than a threshold calibrated on the round-2 validation split;
* **spectral gate**: two interpretable rejection rules (tone-like / noise-like) on
  top of the frozen round-1 gate, thresholds calibrated on round-2 gate_cal +
  validation frames with an explicit known-false-rejection constraint.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ml_training.common.gate import apply_gate, top_scores
from ml_training.common.preprocess import preprocess_iq
from ml_training.model.baseline import load_baseline
from ml_training.model.features import extract_features
from ml_training.model.numpy_runtime import load_model
from ml_training.round2.features import spectral_features

CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
BPSK = CLASSES.index("BPSK")
QPSK = CLASSES.index("QPSK")
UNKNOWN = "UNKNOWN"
RULE_SCHEMA = "ml-modrec-round2-rule-config-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES = Path(__file__).resolve().parent / "artifacts" / "rule_config.json"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def load_rules(path=None):
    """Load and validate the frozen round-2 rule configuration."""
    path = Path(path) if path is not None else DEFAULT_RULES
    document = json.loads(path.read_text(encoding="utf-8"))
    _require(document.get("schema") == RULE_SCHEMA, "invalid rule config schema")
    frozen = document.get("frozen_round1") or {}
    thresholds = frozen.get("gate") or {}
    _require(set(thresholds) == {"max_softmax", "margin", "min_complex_rms"},
             "invalid frozen gate thresholds")
    arbitration = document.get("arbitration") or {}
    _require(type(arbitration.get("threshold")) is float
             and 0.0 <= arbitration["threshold"] <= 1.0,
             "invalid arbitration threshold")
    spectral = document.get("spectral_gate") or {}
    for name in ("tone", "noise"):
        rule = spectral.get(name) or {}
        _require(rule.get("feature") in ("top5_frac", "band_frac_3m"),
                 "invalid spectral feature for " + name)
        _require(type(rule.get("threshold")) is float, "invalid spectral threshold for " + name)
        _require(rule.get("op") in (">", "<"), "invalid spectral operator for " + name)
    return document, path


def softmax(logits):
    logits = np.asarray(logits, dtype=np.float64)
    shifted = logits - logits.max()
    exp = np.exp(shifted)
    return exp / exp.sum()


class Round2Predictor:
    def __init__(self, rules_path=None, cnn_path=None, baseline_path=None):
        document, path = load_rules(rules_path)
        self.rules_path = Path(path)
        frozen = document["frozen_round1"]
        cnn_path = Path(cnn_path) if cnn_path is not None else PROJECT_ROOT / frozen["cnn"]["path"]
        baseline_path = (Path(baseline_path) if baseline_path is not None
                         else PROJECT_ROOT / frozen["baseline"]["path"])
        for label, expected, actual in (
                ("cnn", frozen["cnn"]["sha256"], _sha256(cnn_path)),
                ("baseline", frozen["baseline"]["sha256"], _sha256(baseline_path))):
            _require(expected == actual,
                     "frozen round-1 %s artifact hash mismatch (expected %s, got %s)"
                     % (label, expected, actual))
        self.cnn = load_model(cnn_path)
        self.baseline = load_baseline(baseline_path)
        self.gate = dict(frozen["gate"])
        self.arbitration_threshold = float(document["arbitration"]["threshold"])
        self.spectral_gate = document["spectral_gate"]
        self.document = document

    # -- pieces -----------------------------------------------------------
    def cnn_probabilities(self, iq):
        x, gate = preprocess_iq(iq)
        if not gate["ok"]:
            return None, None, gate
        return softmax(self.cnn.forward(x)), x, gate

    def baseline_probabilities(self, x):
        return self.baseline.scores(extract_features(x))

    def _arbitrate(self, cnn_probs, baseline_probs):
        """Return (class_index, applied, detail) after the BPSK/QPSK feature guard."""
        cnn_label = int(np.argmax(cnn_probs))
        baseline_label = int(np.argmax(baseline_probs))
        margin = float(baseline_probs[BPSK] - baseline_probs[QPSK])
        applied = False
        chosen = cnn_label
        if cnn_label in (BPSK, QPSK) and abs(margin) > self.arbitration_threshold:
            chosen = BPSK if margin > 0 else QPSK
            applied = chosen != cnn_label
        return chosen, applied, {"cnn_label": CLASSES[cnn_label],
                                 "baseline_label": CLASSES[baseline_label],
                                 "bpsk_minus_qpsk": margin}

    def _spectral_rule(self, x):
        features = spectral_features(x)
        for name in ("tone", "noise"):
            rule = self.spectral_gate[name]
            value = features[rule["feature"]]
            if (rule["op"] == ">" and value > rule["threshold"]) or \
                    (rule["op"] == "<" and value < rule["threshold"]):
                return name + "_like", value, features
        return "", None, features

    # -- public API -------------------------------------------------------
    def classify(self, iq):
        """Pure 4-class decision (arbitration only; gate rejection ignored)."""
        probs, x, gate = self.cnn_probabilities(iq)
        if probs is None:
            return {"class_label": UNKNOWN, "reason": gate["reason"], "gate": gate}
        baseline_probs = self.baseline_probabilities(x)
        chosen, applied, detail = self._arbitrate(probs, baseline_probs)
        return {"class_label": CLASSES[chosen], "class_index": chosen,
                "arbitration_applied": applied, "arbitration": detail,
                "cnn_scores": [float(v) for v in probs],
                "baseline_scores": [float(v) for v in baseline_probs],
                "gate": gate}

    def decide(self, iq):
        """Full round-2 decision including all quality gates."""
        probs, x, gate = self.cnn_probabilities(iq)
        result = {"label": UNKNOWN, "reason": "", "score": 0.0, "margin": 0.0,
                  "scores": None, "gate": gate, "rms": gate.get("rms", 0.0),
                  "arbitration_applied": False, "spectral": None}
        if probs is None:
            result["reason"] = gate["reason"]
            return result
        result["cnn_scores"] = [float(v) for v in probs]
        if gate["rms"] < self.gate["min_complex_rms"]:
            result["reason"] = "below_energy_floor"
            return result
        spectral_reason, spectral_value, spectral = self._spectral_rule(x)
        result["spectral"] = {key: float(value) for key, value in spectral.items()}
        if spectral_reason:
            result["reason"] = spectral_reason
            result["spectral_trigger"] = float(spectral_value)
            return result
        index, score, second = top_scores(probs)
        margin = score - second
        if score < self.gate["max_softmax"]:
            result.update(scores=[float(v) for v in probs], score=score, margin=margin)
            result["reason"] = "confidence"
            return result
        if margin < self.gate["margin"]:
            result.update(scores=[float(v) for v in probs], score=score, margin=margin)
            result["reason"] = "margin"
            return result
        baseline_probs = self.baseline_probabilities(x)
        chosen, applied, detail = self._arbitrate(probs, baseline_probs)
        result.update(label=CLASSES[chosen], scores=[float(v) for v in probs],
                      score=score, margin=margin, arbitration_applied=applied,
                      arbitration=detail,
                      baseline_scores=[float(v) for v in baseline_probs])
        return result

    def predict_iq(self, iq):
        """Compatibility shape close to the frozen round-1 runtime's predict_iq."""
        decision = self.decide(iq)
        return {"label": decision["label"], "reason": decision["reason"],
                "score": decision["score"], "margin": decision["margin"],
                "scores": decision["scores"], "gate": decision["gate"],
                "rms": decision["rms"], "status": "experimental-round2",
                "arbitration_applied": decision["arbitration_applied"]}
