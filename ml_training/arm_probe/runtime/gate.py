"""Quality-gate / decision policy shared by training-time threshold selection and inference.

Gate semantics (same as the board runtime):
1. ``clipping`` / ``no_energy`` from ``preprocess_iq``;
2. energy floor: raw complex RMS below ``min_complex_rms`` -> ``below_energy_floor``;
3. softmax confidence below ``max_softmax`` -> ``confidence``;
4. top-1 minus top-2 probability below ``margin`` -> ``margin``;
5. otherwise the top-1 known class.

``UNKNOWN`` is never a training class; it is the rejection label.
"""
from __future__ import annotations

import numpy as np

UNKNOWN = "UNKNOWN"


def softmax(logits):
    logits = np.asarray(logits, dtype=np.float64)
    shifted = logits - logits.max()
    exp = np.exp(shifted)
    return exp / exp.sum()


def top_scores(scores):
    scores = np.asarray(scores, dtype=np.float64)
    order = np.argsort(scores)[::-1]
    return int(order[0]), float(scores[order[0]]), float(scores[order[1]])


def decide(scores, thresholds):
    """Return dict(label, reason, score, margin, accepted) for one probability vector."""
    thresholds = dict(thresholds)
    index, score, second = top_scores(scores)
    margin = score - second
    result = {"index": index, "score": score, "margin": margin,
              "label": UNKNOWN, "reason": ""}
    if score < thresholds["max_softmax"]:
        result["reason"] = "confidence"
    elif margin < thresholds["margin"]:
        result["reason"] = "margin"
    else:
        result["label"] = None  # caller maps the class index
        result["accepted"] = True
    return result


def apply_gate(scores, classes, thresholds):
    """Full decision: returns label in classes, or UNKNOWN with a rejection reason."""
    result = decide(scores, thresholds)
    if result["label"] is None:
        result["label"] = str(classes[result["index"]])
    return result


def energy_floor_from_off(off_rms, quantile=0.95, headroom_db=3.0):
    """Energy floor from OFF (background) raw complex RMS samples.

    Returns ``p95(off_rms) * 10**(headroom_db/20)``; the +3 dB headroom mirrors the
    existing product energy-floor rule.
    """
    values = np.asarray(list(off_rms), dtype=np.float64)
    if values.size == 0:
        raise ValueError("OFF population is empty")
    return float(np.percentile(values, 100.0 * quantile) * (10.0 ** (headroom_db / 20.0)))
