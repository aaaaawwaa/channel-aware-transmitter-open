"""End-to-end ``evaluate_sim.py`` tests using deterministic stub predictors."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ml_training.evaluation import _common as C
from ml_training.evaluation import evaluate_sim

from support import (StubPredictor, accept_spec, digest_of_iq, frame_digests,
                     hash_tree, reject_spec, wrong_spec)


def _fixture_stubs(sim_small):
    manifest = C.load_manifest(sim_small)
    test = C.load_split(sim_small, manifest, "test", labels=C.CLASS_ORDER)
    gate = C.load_split(sim_small, manifest, "gate_test", kinds=C.GATE_KINDS,
                        include_clipped=True)
    test_digests = frame_digests(test)
    gate_digests = frame_digests(gate)

    cnn_mapping = {}
    for digest, label in zip(test_digests, test["label"]):
        cnn_mapping[digest] = accept_spec(str(label))
    labels = [str(label) for label in test["label"]]
    snrs = [float(value) for value in test["snr_db"]]
    bpsk_zero = next(index for index, (label, snr) in enumerate(zip(labels, snrs))
                     if label == "BPSK" and snr == 0.0)
    cnn_mapping[test_digests[bpsk_zero]] = wrong_spec("QPSK")
    qam64_ten = next(index for index, (label, snr) in enumerate(zip(labels, snrs))
                     if label == "QAM64" and snr == 10.0)
    correct_qam64_scores = accept_spec("QAM64")["scores"]
    cnn_mapping[test_digests[qam64_ten]] = reject_spec("margin",
                                                       scores=correct_qam64_scores)

    for index, (digest, kind) in enumerate(zip(gate_digests, gate["label"])):
        kind = str(kind)
        if kind == "CLIP":
            continue  # out-of-range codes: the stub raises and the clipping fallback fires
        if kind == "OFF":
            cnn_mapping[digest] = reject_spec("below_energy_floor")
        elif kind == "NOISE":
            cnn_mapping[digest] = reject_spec("margin")
        else:  # TONE: one false accept, two rejects
            cnn_mapping[digest] = (accept_spec("BPSK") if index % 3 == 0
                                   else reject_spec("confidence"))

    baseline_mapping = {}
    qpsk_ten = next(index for index, (label, snr) in enumerate(zip(labels, snrs))
                    if label == "QPSK" and snr == 10.0)
    for index, (digest, label) in enumerate(zip(test_digests, labels)):
        baseline_mapping[digest] = accept_spec(label)
    baseline_mapping[test_digests[qpsk_ten]] = wrong_spec("QAM16")

    return (StubPredictor(cnn_mapping, kind="cnn"), StubPredictor(baseline_mapping,
                                                                  kind="baseline"),
            test, gate)


def test_evaluate_sim_end_to_end(sim_small, tmp_path):
    cnn, baseline, test, gate = _fixture_stubs(sim_small)
    before = hash_tree(sim_small)
    document = evaluate_sim.evaluate(sim_small, cnn, baseline, tmp_path,
                                     verify_hashes=True)
    after = hash_tree(sim_small)
    assert before == after, "evaluation must never modify the dataset"

    for name in ("sim_metrics.json", "sim_confusion.md", "sim_snr.md", "gate.md"):
        assert (tmp_path / name).is_file()
    loaded = json.loads((tmp_path / "sim_metrics.json").read_text(encoding="utf-8"))
    assert loaded["schema"] == C.EVAL_SCHEMA_SIM
    assert loaded["not_run"] and isinstance(loaded["not_run"], list)
    assert loaded["protocol"]["four_class_population"].startswith("test split only")
    assert loaded["protocol"]["n_test_frames"] == 8
    assert loaded["protocol"]["n_test_frames_dropped_clipped"] == 4
    assert loaded["protocol"]["n_gate_test_frames"] == 12
    assert loaded["protocol"]["test_evaluated_once"] is True
    assert loaded["protocol"]["host_only"] is True

    cnn_report = loaded["cnn"]
    assert cnn_report["n_frames"] == 8
    assert sum(sum(row) for row in cnn_report["confusion_matrix"]) == 8
    assert cnn_report["accuracy"] == pytest.approx(7.0 / 8.0)
    assert cnn_report["per_class"]["BPSK"]["recall"] == pytest.approx(0.5)
    assert cnn_report["per_class"]["QAM64"]["recall"] == pytest.approx(1.0)
    assert cnn_report["gate_rejected_known_frames"] == 1
    assert cnn_report["accuracy_with_gate"] == pytest.approx(6.0 / 8.0)
    assert [row["snr_db"] for row in cnn_report["per_snr"]] == [0.0, 10.0]
    assert cnn_report["per_snr"][0]["accuracy"] == pytest.approx(0.75)

    baseline_report = loaded["baseline_report"]
    assert baseline_report["n_frames"] == 8
    assert baseline_report["accuracy"] == pytest.approx(7.0 / 8.0)
    assert baseline_report["per_class"]["QPSK"]["recall"] == pytest.approx(0.5)
    assert loaded["comparison"]["accuracy"]["delta_cnn_minus_baseline"] == pytest.approx(0.0)

    gate_report = loaded["gate"]
    assert gate_report["per_kind"]["OFF"]["n"] == 3
    assert gate_report["per_kind"]["OFF"]["rejection_rate"] == pytest.approx(1.0)
    assert gate_report["per_kind"]["NOISE"]["rejection_rate"] == pytest.approx(1.0)
    assert gate_report["per_kind"]["TONE"]["rejected"] == 2
    assert gate_report["per_kind"]["TONE"]["accepted_labels"] == {"BPSK": 1}
    assert gate_report["per_kind"]["CLIP"]["rejection_rate"] == pytest.approx(1.0)
    assert gate_report["per_kind"]["CLIP"]["reject_reasons"] == {"clipping": 3}
    assert gate_report["known_class_false_rejection"]["n_frames"] == 8
    assert gate_report["known_class_false_rejection"]["rejected"] == 1
    assert gate_report["separation"]["gate_frames_never_enter_four_class_matrix"] is True
    assert gate_report["baseline"]["status"] == "NOT_RUN"

    confusion_md = (tmp_path / "sim_confusion.md").read_text(encoding="utf-8")
    assert "## CNN confusion matrix" in confusion_md
    assert "NOT ARM" in confusion_md
    snr_md = (tmp_path / "sim_snr.md").read_text(encoding="utf-8")
    assert "## CNN" in snr_md and "## Feature baseline" in snr_md
    gate_md = (tmp_path / "gate.md").read_text(encoding="utf-8")
    assert "gate_test rejection rates per kind" in gate_md
    assert "Strict separation" in gate_md


def test_cli_returns_nonzero_when_artifacts_are_missing(sim_small, tmp_path):
    code = evaluate_sim.main([
        "--dataset", str(sim_small),
        "--model", str(tmp_path / "missing-model"),
        "--baseline", str(tmp_path / "missing-baseline"),
        "--out", str(tmp_path / "out"),
    ])
    assert code == 2
    assert not (tmp_path / "out").exists()


def test_parse_args_requires_contract_flags():
    with pytest.raises(SystemExit):
        evaluate_sim.parse_args([])
