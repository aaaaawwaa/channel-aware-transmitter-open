"""Shared helpers for the evaluation test-suite (stub predictors, hashes)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from ml_training.evaluation import _common as C


def digest_of_iq(iq):
    return C.sha256_bytes(np.ascontiguousarray(np.asarray(iq)).astype("<i2").tobytes())


def frame_digests(frames):
    return [digest_of_iq(row) for row in frames["X"]]


def accept_spec(label, confidence=0.9):
    index = C.CLASS_ORDER.index(label)
    other = (1.0 - confidence) / 3.0
    scores = [other] * 4
    scores[index] = confidence
    ordered = sorted(scores, reverse=True)
    return {
        "label": label, "scores": scores, "score": scores[index],
        "margin": scores[index] - ordered[1], "reason": "",
        "gate": {"ok": True, "reason": ""},
    }


def wrong_spec(predicted_label, confidence=0.85):
    index = C.CLASS_ORDER.index(predicted_label)
    other = (1.0 - confidence) / 3.0
    scores = [other] * 4
    scores[index] = confidence
    ordered = sorted(scores, reverse=True)
    return {
        "label": predicted_label, "scores": scores, "score": confidence,
        "margin": confidence - ordered[1], "reason": "",
        "gate": {"ok": True, "reason": ""},
    }


def reject_spec(reason, scores=None):
    scores = list(scores or [0.45, 0.30, 0.15, 0.10])
    ordered = sorted(scores, reverse=True)
    return {
        "label": C.UNKNOWN, "scores": scores, "score": ordered[0],
        "margin": ordered[0] - ordered[1], "reason": reason,
        "gate": {"ok": False, "reason": reason},
    }


class StubPredictor(object):
    """Deterministic prediction stub: maps a frame's iq_sha256 to a fixed result."""

    def __init__(self, mapping, kind="fixture-stub", classes=None):
        self.mapping = dict(mapping)
        self.info = {
            "kind": kind,
            "artifact_path": "<test-fixture-stub>",
            "model_json_path": "<test-fixture-stub>",
            "model_json_sha256": "0" * 64,
            "network": "stub", "variant": "stub", "preprocessing": "dc-complex-rms-v1",
            "classes": list(classes or C.CLASS_ORDER), "thresholds": {},
            "status": "fixture-stub", "params": 0, "board_compat": "test stub only",
            "training": {}, "supported_domain": {},
        }

    def predict_iq(self, iq):
        array = np.asarray(iq)
        if array.shape != (C.SAMPLES, 2):
            raise ValueError("bad stub IQ shape %r" % (array.shape,))
        if C.out_of_signed12(array):
            raise ValueError("invalid signed-12-bit IQ values")
        digest = digest_of_iq(array)
        if digest not in self.mapping:
            raise KeyError("stub has no prediction for iq_sha256 %s" % digest)
        return dict(self.mapping[digest])


def hash_tree(root):
    """sha256 of every regular file under ``root`` keyed by relative POSIX path."""
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = C.sha256_file(path)
    return result
