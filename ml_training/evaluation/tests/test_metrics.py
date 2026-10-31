"""4-class metric unit tests (CONTRACTS §2, §10)."""
from __future__ import annotations

import math

import pytest

from ml_training.evaluation import _common as C


def test_confusion_matrix_shape_and_counts():
    truth = ["BPSK", "BPSK", "QPSK", "QAM16"]
    predicted = ["BPSK", "QPSK", "QPSK", "QAM16"]
    matrix = C.confusion_matrix(truth, predicted)
    assert len(matrix) == 4 and all(len(row) == 4 for row in matrix)
    assert matrix == [
        [1, 1, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 0],
    ]
    assert sum(sum(row) for row in matrix) == len(truth)


def test_per_class_precision_recall_f1_macro_and_accuracy():
    truth = ["BPSK", "BPSK", "QPSK", "QAM16"]
    predicted = ["BPSK", "QPSK", "QPSK", "QAM16"]
    metrics = C.classification_metrics(truth, predicted)
    per_class = metrics["per_class"]
    assert per_class["BPSK"]["precision"] == pytest.approx(1.0)
    assert per_class["BPSK"]["recall"] == pytest.approx(0.5)
    assert per_class["BPSK"]["f1"] == pytest.approx(2.0 / 3.0)
    assert per_class["BPSK"]["support"] == 2
    assert per_class["QPSK"]["precision"] == pytest.approx(0.5)
    assert per_class["QPSK"]["recall"] == pytest.approx(1.0)
    assert per_class["QPSK"]["f1"] == pytest.approx(2.0 / 3.0)
    assert per_class["QAM16"]["f1"] == pytest.approx(1.0)
    assert per_class["QAM64"]["support"] == 0
    assert per_class["QAM64"]["f1"] == 0.0
    assert metrics["macro_f1"] == pytest.approx((2.0 / 3.0 + 2.0 / 3.0 + 1.0 + 0.0) / 4.0)
    assert metrics["accuracy"] == pytest.approx(0.75)
    assert metrics["n_frames"] == 4
    assert metrics["correct"] == 3


def test_all_correct_accuracy_and_macro_one():
    truth = list(C.CLASS_ORDER) * 2
    metrics = C.classification_metrics(truth, list(truth))
    assert metrics["accuracy"] == 1.0
    assert metrics["macro_f1"] == 1.0


def test_gate_kinds_can_never_enter_four_class_metrics():
    with pytest.raises(C.LabelLeakError):
        C.classification_metrics(["BPSK", "OFF"], ["BPSK", "BPSK"])
    with pytest.raises(C.LabelLeakError):
        C.classification_metrics(["BPSK"], ["TONE"])
    with pytest.raises(C.LabelLeakError):
        C.confusion_matrix(["CLIP"], ["BPSK"])


def test_per_snr_metrics_groups_and_null_recall():
    truth = ["BPSK", "BPSK", "QPSK", "QAM16"]
    predicted = ["BPSK", "QPSK", "QPSK", "QAM16"]
    rows = C.per_snr_metrics(truth, predicted, [0.0, 0.0, 10.0, 10.0])
    assert [row["snr_db"] for row in rows] == [0.0, 10.0]
    low = rows[0]
    assert low["n"] == 2
    assert low["accuracy"] == pytest.approx(0.5)
    assert low["recall"]["BPSK"] == pytest.approx(0.5)
    assert low["recall"]["QPSK"] is None  # no QPSK support at 0 dB
    assert low["macro_f1"] == pytest.approx(2.0 / 3.0)  # mean over present classes only
    assert low["macro_f1_all_classes"] == pytest.approx((2.0 / 3.0) / 4.0)
    high = rows[1]
    assert high["accuracy"] == pytest.approx(1.0)
    assert high["macro_f1"] == pytest.approx(1.0)
    assert high["macro_f1_all_classes"] == pytest.approx(0.5)


def test_four_class_report_uses_argmax_and_separates_gate_rejection():
    truth = ["BPSK", "QPSK", "QAM16", "QAM64"]
    predictions = [
        {"label": "BPSK", "scores": [0.9, 0.05, 0.03, 0.02]},
        {"label": "QPSK", "scores": [0.05, 0.9, 0.03, 0.02]},
        {"label": C.UNKNOWN, "reason": "margin", "scores": [0.05, 0.03, 0.9, 0.02]},
        {"label": "QAM64", "scores": [0.02, 0.03, 0.05, 0.9]},
    ]
    report = C.four_class_report(truth, predictions, [0.0] * 4)
    # the gate rejection does NOT alter the 4-class argmax matrix
    assert report["accuracy"] == pytest.approx(1.0)
    assert report["gate_rejected_known_frames"] == 1
    assert report["gate_rejection_rate"] == pytest.approx(0.25)
    # ... but it does count as an error in the (informational) gated accuracy
    assert report["accuracy_with_gate"] == pytest.approx(0.75)
    assert report["per_snr"][0]["n"] == 4


def test_class_from_scores_requires_four_scores():
    with pytest.raises(C.EvaluationError):
        C.class_from_scores({"label": "BPSK", "scores": None})
    with pytest.raises(C.EvaluationError):
        C.class_from_scores({"scores": [0.5, 0.5]})


def test_round_snr_is_stable_for_float32_values():
    assert C.round_snr(0.0) == 0.0
    assert C.round_snr(10.0000001) == 10.0
    assert C.round_snr(-6.0) == -6.0
    assert math.isclose(C.round_snr(2.0004), 2.0)
