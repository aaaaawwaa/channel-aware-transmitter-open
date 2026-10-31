"""Interpretable feature baseline: 8 features + multinomial logistic regression.

This is the "simple, explainable" reference model.  It is trained with plain
NumPy (no sklearn/torch dependency) on standardised features; the L2 strength is
selected on the validation split only.  Its output contract mirrors the CNN's
offline decision dict so evaluation code can treat both models identically.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from .gate import apply_gate, softmax
from .preprocess import preprocess_iq
from .features import FEATURE_NAMES, FEATURE_SET, extract_features

SCHEMA_VERSION = 1
KIND = "feature-baseline"
MAX_MODEL_BYTES = 128 * 1024


def _softmax_rows(logits):
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def train_logistic(features, targets, num_classes, l2=1e-2, epochs=300, lr=0.5,
                   seed=0, batch_size=None):
    """Full-batch gradient descent with Adam on standardised features."""
    features = np.asarray(features, dtype=np.float64)
    targets = np.asarray(targets, dtype=np.int64)
    mean = features.mean(axis=0)
    std = features.std(axis=0)
    std[std < 1e-12] = 1.0
    normalised = (features - mean) / std
    weights = np.zeros((normalised.shape[1], num_classes), dtype=np.float64)
    bias = np.zeros((num_classes,), dtype=np.float64)
    one_hot = np.zeros((targets.size, num_classes), dtype=np.float64)
    one_hot[np.arange(targets.size), targets] = 1.0
    rng = np.random.default_rng(seed)
    m_w = np.zeros_like(weights)
    v_w = np.zeros_like(weights)
    m_b = np.zeros_like(bias)
    v_b = np.zeros_like(bias)
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    count = normalised.shape[0]
    for step in range(1, int(epochs) + 1):
        logits = normalised @ weights + bias
        probabilities = _softmax_rows(logits)
        error = probabilities - one_hot
        grad_w = normalised.T @ error / count + l2 * weights
        grad_b = error.mean(axis=0)
        m_w = beta1 * m_w + (1.0 - beta1) * grad_w
        v_w = beta2 * v_w + (1.0 - beta2) * (grad_w ** 2)
        m_b = beta1 * m_b + (1.0 - beta1) * grad_b
        v_b = beta2 * v_b + (1.0 - beta2) * (grad_b ** 2)
        correction1 = 1.0 - beta1 ** step
        correction2 = 1.0 - beta2 ** step
        weights -= lr * (m_w / correction1) / (np.sqrt(v_w / correction2) + eps)
        bias -= lr * (m_b / correction1) / (np.sqrt(v_b / correction2) + eps)
    return {"mean": mean.tolist(), "std": std.tolist(),
            "W": weights.tolist(), "b": bias.tolist()}


def _predict_matrix(parameters, features):
    mean = np.asarray(parameters["mean"], dtype=np.float64)
    std = np.asarray(parameters["std"], dtype=np.float64)
    weights = np.asarray(parameters["W"], dtype=np.float64)
    bias = np.asarray(parameters["b"], dtype=np.float64)
    return _softmax_rows(((np.asarray(features, dtype=np.float64) - mean) / std) @ weights + bias)


def save_baseline(path, parameters, classes, thresholds, training):
    document = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "feature_set": FEATURE_SET,
        "features": list(FEATURE_NAMES),
        "classes": list(classes),
        "thresholds": {key: float(value) for key, value in thresholds.items()},
        "parameters": parameters,
        "training": training,
        "status": "experimental",
    }
    raw = json.dumps(document, indent=1).encode("utf-8")
    if len(raw) > MAX_MODEL_BYTES:
        raise ValueError("baseline model exceeds size limit")
    Path(path).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def validate_baseline(document):
    if (not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION
            or document.get("kind") != KIND
            or document.get("feature_set") != FEATURE_SET
            or document.get("features") != list(FEATURE_NAMES)
            or not isinstance(document.get("classes"), list) or len(document["classes"]) != 4):
        raise ValueError("invalid baseline document")
    parameters = document.get("parameters")
    if (not isinstance(parameters, dict)
            or not all(key in parameters for key in ("mean", "std", "W", "b"))
            or len(parameters["mean"]) != len(FEATURE_NAMES)
            or len(parameters["std"]) != len(FEATURE_NAMES)
            or np.asarray(parameters["W"]).shape != (len(FEATURE_NAMES), 4)
            or len(parameters["b"]) != 4):
        raise ValueError("invalid baseline parameters")
    return document


def load_baseline(path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return FeatureBaseline(validate_baseline(document))


class FeatureBaseline:
    def __init__(self, document):
        validate_baseline(document)
        self.document = document
        self.classes = tuple(document["classes"])
        self.thresholds = dict(document["thresholds"])
        self.parameters = document["parameters"]

    def scores(self, features):
        return _predict_matrix(self.parameters, np.atleast_2d(features))[0]

    def predict_iq(self, iq):
        x, gate = preprocess_iq(iq)
        if not gate["ok"]:
            return {"label": "UNKNOWN", "reason": gate["reason"], "score": 0.0,
                    "margin": 0.0, "scores": None, "gate": gate, "rms": gate["rms"],
                    "status": "experimental"}
        if gate["rms"] < self.thresholds["min_complex_rms"]:
            gate = dict(gate)
            gate["ok"] = False
            gate["reason"] = "below_energy_floor"
            return {"label": "UNKNOWN", "reason": gate["reason"], "score": 0.0,
                    "margin": 0.0, "scores": None, "gate": gate, "rms": gate["rms"],
                    "status": "experimental"}
        vector = extract_features(x)
        probabilities = self.scores(vector)
        decision = apply_gate(probabilities, self.classes, self.thresholds)
        return {"label": decision["label"], "reason": decision["reason"],
                "score": decision["score"], "margin": decision["margin"],
                "scores": [float(value) for value in probabilities],
                "gate": gate, "rms": gate["rms"], "status": "experimental"}
