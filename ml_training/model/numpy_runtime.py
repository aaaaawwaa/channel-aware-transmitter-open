"""Pure-NumPy offline inference for the exported 4-class model JSON.

Numerically mirrors the board runtime's forward pass: stride-2 cross-correlation
convolutions with BLAS accumulation, ReLU, global average pooling, and the ordered
float32 head accumulation used by ``rf_cnn_runtime.TinyIQCNNModel``.  No product
code, ctypes or torch is imported here, so offline inference works in a plain
numpy environment.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from ml_training.common.gate import apply_gate, softmax
from ml_training.common.preprocess import PREPROCESSING, preprocess_iq

SCHEMA_VERSION = 1
NETWORK = "tiny-iq-cnn-v1"
VARIANT = "modrec4"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
INPUT_SHAPE = (2, 4096)
MAX_MODEL_BYTES = 2 * 1024 * 1024
WEIGHT_SHAPES = {
    "conv1.weight": (8, 2, 7), "conv1.bias": (8,),
    "conv2.weight": (16, 8, 5), "conv2.bias": (16,),
    "conv3.weight": (16, 16, 3), "conv3.bias": (16,),
    "head.weight": (4, 16), "head.bias": (4,),
}


def _is_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_model(document):
    if (not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION
            or type(document.get("schema_version")) is not int
            or document.get("network") != NETWORK
            or document.get("variant") != VARIANT
            or document.get("preprocessing") != PREPROCESSING
            or document.get("classes") != list(CLASSES)
            or document.get("input_shape") != list(INPUT_SHAPE)):
        raise ValueError("invalid model schema/network/variant/preprocessing/classes/input")
    thresholds = document.get("thresholds")
    if (not isinstance(thresholds, dict)
            or set(thresholds) != {"max_softmax", "margin", "min_complex_rms"}
            or any(not _is_number(v) for v in thresholds.values())
            or not 0.0 <= thresholds["max_softmax"] <= 1.0
            or not 0.0 <= thresholds["margin"] <= 1.0
            or thresholds["min_complex_rms"] <= 0.0):
        raise ValueError("invalid model thresholds")
    weights = document.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(WEIGHT_SHAPES):
        raise ValueError("invalid model weight keys")
    for name, shape in WEIGHT_SHAPES.items():
        tensor = weights[name]
        if (not isinstance(tensor, dict) or set(tensor) != {"shape", "values"}
                or tensor["shape"] != list(shape)
                or not isinstance(tensor["values"], list)
                or len(tensor["values"]) != math.prod(shape)
                or any(not _is_number(v) for v in tensor["values"])):
            raise ValueError("invalid model weight tensor: " + name)
    return document


def load_model(path):
    """Read a bounded JSON model, validate it, and return a :class:`ModRecModel`."""
    raw = Path(path).read_bytes()
    if len(raw) > MAX_MODEL_BYTES:
        raise ValueError("model JSON exceeds %d bytes" % (MAX_MODEL_BYTES,))
    document = json.loads(raw.decode("utf-8"))
    return ModRecModel(validate_model(document))


def _conv_stride2(x, weight, bias, padding):
    """Stride-2 cross-correlation, vectorised over output windows (float32 output)."""
    padded = np.pad(x, ((0, 0), (padding, padding)), mode="constant")
    kernel = weight.shape[2]
    length = (padded.shape[1] - kernel) // 2 + 1
    windows = np.lib.stride_tricks.as_strided(
        padded, shape=(x.shape[0], length, kernel),
        strides=(padded.strides[0], padded.strides[1] * 2, padded.strides[1]))
    matrix = windows.transpose(1, 0, 2).reshape(length, -1)
    result = np.dot(matrix, weight.reshape(weight.shape[0], -1).T) + bias
    return np.ascontiguousarray(result.T, dtype=np.float32)


def _conv_stride2_ordered(x, weight, bias, padding):
    """Same convolution with the product runtime's ordered float32 FMA rounding."""
    padded = np.pad(x, ((0, 0), (padding, padding)), mode="constant")
    kernel = weight.shape[2]
    length = (padded.shape[1] - kernel) // 2 + 1
    windows = np.lib.stride_tricks.as_strided(
        padded, shape=(x.shape[0], length, kernel),
        strides=(padded.strides[0], padded.strides[1] * 2, padded.strides[1]))
    matrix = windows.transpose(1, 0, 2).reshape(length, -1).astype(np.float64)
    weights = weight.reshape(weight.shape[0], -1).T.astype(np.float64)
    result = np.zeros((length, weight.shape[0]), dtype=np.float32)
    for index in range(matrix.shape[1]):
        product = matrix[:, index, None] * weights[index, None, :]
        product += result
        result = product.astype(np.float32)
    result += bias
    return np.ascontiguousarray(result.T, dtype=np.float32)


class ModRecModel:
    def __init__(self, document):
        validate_model(document)
        self.document = document
        self.classes = tuple(document["classes"])
        self.thresholds = dict(document["thresholds"])
        self.status = document.get("status", "experimental")
        self.domain = dict(document.get("supported_domain", {}))
        self.weights = {}
        for name, tensor in document["weights"].items():
            value = np.asarray(tensor["values"], dtype=np.float32).reshape(tensor["shape"])
            value.setflags(write=False)
            self.weights[name] = value

    def forward(self, x):
        """Return logits for one pre-processed ``(2, 4096)`` float32 frame."""
        x = np.asarray(x, dtype=np.float32)
        if x.shape != INPUT_SHAPE or not np.isfinite(x).all():
            raise ValueError("invalid network input")
        for index, padding in ((1, 3), (2, 2), (3, 1)):
            name = "conv%d" % index
            x = _conv_stride2(x, self.weights[name + ".weight"],
                              self.weights[name + ".bias"], padding)
            x = np.maximum(x, np.float32(0))
        gap = x.mean(axis=1)
        # Ordered head accumulation: float64 products with per-term float32 rounding.
        weight = self.weights["head.weight"].astype(np.float64)
        gap64 = gap.astype(np.float64)
        acc = np.zeros((weight.shape[0],), dtype=np.float32)
        for index in range(weight.shape[1]):
            product = weight[:, index] * gap64[index] + acc.astype(np.float64)
            acc = product.astype(np.float32)
        logits = acc + self.weights["head.bias"]
        if not np.isfinite(logits).all():
            raise ValueError("nonfinite network output")
        return logits

    def forward_ordered(self, x):
        """Same forward pass using the product's ordered FMA convolution policy."""
        x = np.asarray(x, dtype=np.float32)
        if x.shape != INPUT_SHAPE or not np.isfinite(x).all():
            raise ValueError("invalid network input")
        for index, padding in ((1, 3), (2, 2), (3, 1)):
            name = "conv%d" % index
            x = _conv_stride2_ordered(x, self.weights[name + ".weight"],
                                      self.weights[name + ".bias"], padding)
            x = np.maximum(x, np.float32(0))
        gap = x.mean(axis=1)
        weight = self.weights["head.weight"].astype(np.float64)
        gap64 = gap.astype(np.float64)
        acc = np.zeros((weight.shape[0],), dtype=np.float32)
        for index in range(weight.shape[1]):
            product = weight[:, index] * gap64[index] + acc.astype(np.float64)
            acc = product.astype(np.float32)
        return acc + self.weights["head.bias"]

    def predict_scores(self, iq):
        """Return (scores, gate, rms) for one int16 ``(4096, 2)`` frame."""
        x, gate = preprocess_iq(iq)
        if not gate["ok"]:
            return None, gate, gate["rms"]
        if gate["rms"] < self.thresholds["min_complex_rms"]:
            gate = dict(gate)
            gate["ok"] = False
            gate["reason"] = "below_energy_floor"
            return None, gate, gate["rms"]
        return softmax(self.forward(x)), gate, gate["rms"]

    def predict_iq(self, iq):
        """Full offline decision dict (label in classes or UNKNOWN + reason)."""
        scores, gate, rms = self.predict_scores(iq)
        if scores is None:
            return {"label": "UNKNOWN", "reason": gate["reason"], "score": 0.0,
                    "margin": 0.0, "scores": None, "gate": gate, "rms": rms,
                    "status": self.status}
        decision = apply_gate(scores, self.classes, self.thresholds)
        return {"label": decision["label"], "reason": decision["reason"],
                "score": decision["score"], "margin": decision["margin"],
                "scores": [float(value) for value in scores], "gate": gate,
                "rms": rms, "status": self.status}


def predict_file(model, iq_path):
    """Convenience: read a 16384-byte ``.iq`` frame and run :meth:`ModRecModel.predict_iq`."""
    from ml_training.common.iq import read_frame

    return model.predict_iq(read_frame(iq_path))
