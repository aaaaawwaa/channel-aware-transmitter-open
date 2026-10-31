"""Quality-gate metric tests (CONTRACTS §2, §10): gate_test only, strictly separate."""
from __future__ import annotations

import pytest

from ml_training.evaluation import _common as C
from support import accept_spec, reject_spec


def _fixture_rows():
    gate_rows = [
        {"kind": "OFF", "prediction": reject_spec("below_energy_floor")},
        {"kind": "OFF", "prediction": reject_spec("below_energy_floor")},
        {"kind": "OFF", "prediction": reject_spec("no_energy")},
        {"kind": "NOISE", "prediction": reject_spec("margin")},
        {"kind": "NOISE", "prediction": reject_spec("margin")},
        {"kind": "NOISE", "prediction": reject_spec("confidence")},
        {"kind": "TONE", "prediction": reject_spec("confidence")},
        {"kind": "TONE", "prediction": reject_spec("confidence")},
        {"kind": "TONE", "prediction": accept_spec("BPSK", confidence=0.95)},
        {"kind": "CLIP", "prediction": reject_spec("clipping")},
        {"kind": "CLIP", "prediction": reject_spec("clipping")},
        {"kind": "CLIP", "prediction": reject_spec("clipping")},
    ]
    known_rows = []
    for label in C.CLASS_ORDER:
        known_rows.append({"label": label, "prediction": accept_spec(label)})
        known_rows.append({"label": label, "prediction": accept_spec(label)})
    # one known QAM64 frame is falsely rejected by the gate
    known_rows[-1] = {"label": "QAM64", "prediction": reject_spec("margin")}
    return gate_rows, known_rows


def test_per_kind_rejection_rates_and_false_accepts():
    gate_rows, known_rows = _fixture_rows()
    report = C.gate_metrics(gate_rows, known_rows)
    per_kind = report["per_kind"]
    assert per_kind["OFF"]["n"] == 3
    assert per_kind["OFF"]["rejected"] == 3
    assert per_kind["OFF"]["rejection_rate"] == pytest.approx(1.0)
    assert per_kind["OFF"]["reject_reasons"] == {"below_energy_floor": 2, "no_energy": 1}
    assert per_kind["NOISE"]["rejection_rate"] == pytest.approx(1.0)
    assert per_kind["NOISE"]["reject_reasons"] == {"margin": 2, "confidence": 1}
    assert per_kind["TONE"]["rejected"] == 2
    assert per_kind["TONE"]["accepted_labels"] == {"BPSK": 1}
    assert per_kind["CLIP"]["rejection_rate"] == pytest.approx(1.0)
    assert per_kind["CLIP"]["reject_reasons"] == {"clipping": 3}
    assert report["n_gate_frames"] == 12


def test_known_class_false_rejection_is_gate_metrics_only():
    gate_rows, known_rows = _fixture_rows()
    report = C.gate_metrics(gate_rows, known_rows)
    known = report["known_class_false_rejection"]
    assert known["n_frames"] == 8
    assert known["rejected"] == 1
    assert known["rate"] == pytest.approx(0.125)
    assert known["per_class"]["QAM64"]["rejected"] == 1
    assert known["per_class"]["BPSK"]["rejected"] == 0
    assert known["reject_reasons"] == {"margin": 1}
    assert report["separation"]["gate_frames_never_enter_four_class_matrix"] is True
    assert report["separation"]["four_class_labels_never_enter_gate_rates"] is True


def test_gate_kind_in_known_rows_is_label_leak():
    gate_rows, known_rows = _fixture_rows()
    bad = known_rows + [{"label": "OFF", "prediction": reject_spec("no_energy")}]
    with pytest.raises(C.LabelLeakError):
        C.gate_metrics(gate_rows, bad)


def test_four_class_label_in_gate_rows_is_label_leak():
    gate_rows, known_rows = _fixture_rows()
    bad = gate_rows + [{"kind": "BPSK", "prediction": accept_spec("BPSK")}]
    with pytest.raises(C.LabelLeakError):
        C.gate_metrics(bad, known_rows)


def test_unknown_gate_kind_is_rejected():
    gate_rows, known_rows = _fixture_rows()
    bad = gate_rows + [{"kind": "UNKNOWN", "prediction": reject_spec("confidence")}]
    with pytest.raises(C.LabelLeakError):
        C.gate_metrics(bad, known_rows)


def test_empty_populations_do_not_divide_by_zero():
    report = C.gate_metrics([], [])
    assert report["per_kind"]["OFF"]["n"] == 0
    assert report["per_kind"]["OFF"]["rejection_rate"] == 0.0
    assert report["known_class_false_rejection"]["rate"] == 0.0
