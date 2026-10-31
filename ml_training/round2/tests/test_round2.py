"""Round-2 acceptance tests: frozen round-1 preservation + improvement behaviour.

These tests use the frozen round-2 rule configuration and read-only evidence; they do not
retrain or tune anything.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split  # noqa: E402
from ml_training.common.iq import read_frame  # noqa: E402
from ml_training.model.numpy_runtime import load_model  # noqa: E402
from ml_training.round2.predictor import Round2Predictor, load_rules  # noqa: E402

ML = ROOT / "ml_training"
DEV = ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18" / \
    "campaign5-val-followup" / "download"
INDEPENDENT = ROOT / "evidence" / "rf-teaching" / "s2" / \
    "46db-trainval-campaign3-2026-10-18" / "download" / "train-01"
ROUND2_DATASET = ML / "round2" / "datasets" / "sim-r2"
ROUND1_HASHES = {
    ML / "artifacts" / "cnn" / "model.json":
        "25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc",
    ML / "artifacts" / "baseline" / "model.json":
        "caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014",
    ML / "datasets" / "sim-v1" / "manifest.json":
        "9f70a0c8c5e3b5a1395617d71f860fd60c2bd3462511efa5330dea6f310a3ecd",
    ML / "reports" / "first-round-report.md":
        "31b25fbb3dd5ebcd440db1c654dd0b6e380d6fa1eda29f86fbd30b21ef023964",
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def predictor():
    return Round2Predictor()


@pytest.fixture(scope="module")
def frozen_cnn():
    return load_model(ML / "artifacts" / "cnn" / "model.json")


def test_frozen_round1_artifacts_unchanged():
    for path, expected in ROUND1_HASHES.items():
        assert path.is_file(), path
        assert sha256(path) == expected, path


def test_rule_config_contract(predictor):
    document, path = load_rules()
    assert path.is_file()
    frozen = document["frozen_round1"]
    assert frozen["cnn"]["sha256"] == sha256(ML / "artifacts" / "cnn" / "model.json")
    assert frozen["baseline"]["sha256"] == sha256(ML / "artifacts" / "baseline" / "model.json")
    assert 0.0 <= document["arbitration"]["threshold"] <= 1.0
    for name in ("tone", "noise"):
        rule = document["spectral_gate"][name]
        assert rule["feature"] in ("top5_frac", "band_frac_3m")
        assert rule["op"] in (">", "<")
        assert isinstance(rule["threshold"], float)
    assert predictor.arbitration_threshold == document["arbitration"]["threshold"]


def test_arbitration_unit_behaviour(predictor):
    bpsk, qpsk = 0, 1
    # CNN says QPSK but the baseline strongly prefers BPSK -> override to BPSK
    cnn = np.asarray([0.15, 0.80, 0.04, 0.01])
    baseline = np.asarray([0.90, 0.05, 0.03, 0.02])
    chosen, applied, _ = predictor._arbitrate(cnn, baseline)
    assert chosen == bpsk and applied
    # Baseline margin too small -> keep the CNN decision
    cnn = np.asarray([0.10, 0.85, 0.03, 0.02])
    baseline = np.asarray([0.40, 0.38, 0.12, 0.10])
    chosen, applied, _ = predictor._arbitrate(cnn, baseline)
    assert chosen == qpsk and not applied
    # CNN top-1 is QAM -> arbitration never touches QAM decisions
    cnn = np.asarray([0.02, 0.03, 0.90, 0.05])
    baseline = np.asarray([0.95, 0.02, 0.02, 0.01])
    chosen, applied, _ = predictor._arbitrate(cnn, baseline)
    assert chosen == 2 and not applied


def test_real_failure_session_is_fixed(predictor, frozen_cnn):
    iq = read_frame(DEV / "train-02" / "normal-01.iq")
    assert frozen_cnn.predict_iq(iq)["label"] == "QPSK"  # round-1 failure reproduced
    decision = predictor.decide(iq)
    assert decision["label"] == "BPSK"
    assert decision["arbitration_applied"] is True
    assert decision["reason"] == ""


def test_other_dev_sessions_unchanged(predictor, frozen_cnn):
    for session in ("train-01", "val-01"):
        iq = read_frame(DEV / session / "normal-01.iq")
        assert frozen_cnn.predict_iq(iq)["label"] == "BPSK"
        assert predictor.decide(iq)["label"] == "BPSK"


def test_real_off_frames_rejected(predictor):
    for session in ("train-01", "train-02", "val-01"):
        iq = read_frame(DEV / session / "off-01.iq")
        decision = predictor.decide(iq)
        assert decision["label"] == "UNKNOWN", session
        assert decision["reason"] in ("noise_like", "confidence", "below_energy_floor"), session


def test_independent_physical_session_generalises(predictor, frozen_cnn):
    normal = sorted(INDEPENDENT.glob("normal-*.iq"))
    assert len(normal) == 10
    plain = 0
    for path in normal:
        iq = read_frame(path)
        plain += int(frozen_cnn.predict_iq(iq)["label"] == "BPSK")
        decision = predictor.decide(iq)
        assert decision["label"] == "BPSK", path.name
    assert plain == 0  # the frozen round-1 CNN fails on this independent session
    off = read_frame(INDEPENDENT / "off-01.iq")
    assert predictor.decide(off)["label"] == "UNKNOWN"


def test_gate_does_not_reject_everything(predictor):
    manifest = load_manifest(ROUND2_DATASET)
    val = load_split(ROUND2_DATASET, manifest, "val", labels=["BPSK", "QPSK", "QAM16", "QAM64"])
    positions = list(range(0, len(val["X"]), 6))  # deterministic 200-frame sample
    accepted = 0
    spectral_rejected = 0
    for position in positions:
        decision = predictor.decide(val["X"][position])
        if decision["label"] != "UNKNOWN":
            accepted += 1
        if decision["reason"] in ("tone_like", "noise_like"):
            spectral_rejected += 1
    acceptance = accepted / len(positions)
    assert acceptance >= 0.95, acceptance
    assert spectral_rejected / len(positions) <= 0.02, spectral_rejected / len(positions)


def test_gate_test_rejection_rules(predictor):
    manifest = load_manifest(ROUND2_DATASET)
    gate = load_split(ROUND2_DATASET, manifest, "gate_test",
                      kinds=["TONE", "NOISE", "OFF"], include_clipped=True)
    kinds = np.asarray([str(kind) for kind in gate["label"]])
    reason_by_kind = {}
    for index in range(len(gate["X"])):
        kind = str(gate["label"][index])
        if kind not in ("TONE", "NOISE", "OFF"):
            continue
        if kind not in reason_by_kind:
            reason_by_kind[kind] = []
        decision = predictor.decide(gate["X"][index])
        reason_by_kind[kind].append(decision["reason"] if decision["label"] == "UNKNOWN" else "")
    for kind in ("TONE", "NOISE", "OFF"):
        reasons = reason_by_kind[kind]
        assert reasons, kind
        rejected = sum(1 for reason in reasons if reason)
        assert rejected / len(reasons) >= 0.99, (kind, rejected, len(reasons))
    assert all(reason == "tone_like" for reason in reason_by_kind["TONE"])
    assert all(reason in ("noise_like", "clipping") for reason in reason_by_kind["NOISE"])


def test_round2_dataset_seed_isolation():
    round1 = json.loads((ML / "datasets" / "sim-v1" / "manifest.json")
                        .read_text(encoding="utf-8"))
    round2 = json.loads((ROUND2_DATASET / "manifest.json").read_text(encoding="utf-8"))

    def families(manifest):
        result = {}
        for entry in manifest["files"]:
            with np.load(ROUND2_DATASET / entry["path"], allow_pickle=False) if \
                    manifest is round2 else np.load(ML / "datasets" / "sim-v1" / entry["path"],
                                                    allow_pickle=False) as archive:
                for value in np.asarray(archive["seed_family"]).tolist():
                    result.setdefault(entry["split"], set()).add(value)
        return result

    round2_families = families(round2)
    round1_families = families(round1)
    for split in ("train", "val", "test", "gate_cal", "gate_test"):
        for family in round2_families.get(split, set()):
            assert family not in round1_families.get(split, set()), (split, family)
            for other in ("train", "val", "test", "gate_cal", "gate_test"):
                if other != split:
                    assert family not in round2_families.get(other, set())


def test_final_evaluation_report_contract():
    path = ML / "round2" / "evaluation" / "reports" / "round2_sim_metrics.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["sim_test"]["protocol"]["test_used_for_tuning"] is False
    assert document["real_dev_replay"]["scope"].startswith("development replay")
    assert document["real_independent_session"]["summary"]["not_used_in_development"] is True
    assert document["model"]["total_parameters"] == 1664
    before = document["sim_test"]["gate_test"]["known_test"]["before_rejected"]
    after = document["sim_test"]["gate_test"]["known_test"]["after_rejected"]
    assert 0 <= before < after < 0.10 * document["sim_test"]["protocol"]["test_frames"]
