"""RRC worker handoff: product pipeline path, contract gate and classifier refusal."""
import dataclasses
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rf_status_worker as W
import rf_rrc_pipeline as P
import test_rf_rx_pipeline as H
from test_rf_rrc_receiver import _frame


def rrc_frame(capture_id="rrc-1", *, mode="QPSK"):
    return dataclasses.replace(
        _frame(mode=mode, offset=7, phase=0.2, iq_order="QI"),
        capture_id=capture_id, release_id=H.RELEASE,
        completed_at_monotonic=time.monotonic())


def prepared(frame):
    client = H.FakeCaptureClient(blocked=False, samples=4096)
    client.capture = lambda args, physical=False: frame
    worker = W.RfStatusWorker(client, poll_interval=100)
    worker._demod_checked = True
    worker._demod_pipeline = None
    worker._qpsk_pipeline = None
    worker._rrc_pipeline = P
    worker._demod_info = dict(
        available=False, qpsk_available=False, rrc_available=True,
        release_id=H.RELEASE, reason="RRC ready",
        algorithm_version=None, algorithm_sha256=None,
        qpsk_algorithm_version=None, qpsk_algorithm_sha256=None,
        rrc_algorithm_version=P.VERSION, rrc_algorithm_sha256="c" * 64)
    draft = dict(expected_mode="QPSK", shaping="RRC", symrate_hz=3_000_000,
                 reference_enabled=True, reference_seed=39, decim=1,
                 rx_len=4096, input_fs_hz=60_000_000)
    submitted = P.freeze_rrc_demod_config(draft, 7, iq_order="QI")
    return client, worker, submitted


def test_rrc_submission_runs_product_pipeline_and_intervals():
    client, worker, submitted = prepared(rrc_frame())
    assert worker.demod_status()["rrc_available"] is True
    assert worker.demod_status()["rrc_algorithm_version"] == P.VERSION
    reply = worker.submit_capture(dict(samples=4096, ratio=0), demod_config=submitted,
                                  demod_revision=7)
    assert reply["queued"]
    payload, status = H.drain(worker)
    env = payload["demod_result"]
    assert isinstance(env, P.RrcEnvelope)
    assert env.submitted == submitted
    assert env.result.capture_id == "rrc-1"
    assert (env.result.bit_errors, env.result.compared_bits,
            env.result.valid_symbols) == (0, 112, 119)
    assert env.result.ber == 0.0
    assert (env.comparison_start_symbol, env.comparison_end_symbol) == (67, 123)


def test_rrc_config_rejected_without_rrc_pipeline():
    client, worker, submitted = prepared(rrc_frame())
    worker._rrc_pipeline = None
    reply = worker.submit_capture(dict(samples=4096, ratio=0), demod_config=submitted)
    assert reply.get("queued") is None and reply["code"] == "DEMOD_UNAVAILABLE"


def test_rrc_modrec4_classification_is_explicitly_unavailable_not_blocking():
    client, worker, submitted = prepared(rrc_frame())
    frame = rrc_frame()
    _, _, env = None, None, P.analyze_rrc(frame, submitted)
    result = worker._modrec4_result(frame, {}, None, env, None)
    assert result["status"] == "INVALID"
    assert result["reason"] == "RRC_CLASSIFICATION_UNAVAILABLE"
    assert result["validity"] == "current"
    # The RRC demod envelope itself remains a successful measured result.
    assert env.result.reason == "" and env.result.ber == 0.0


def _fake_demod_release(tmp_path, monkeypatch, contract):
    import merged_release as R
    tmp_path.mkdir(parents=True, exist_ok=True)
    names = ("rf_bpsk_pipeline.py", "rf_bpsk_receiver.py",
             "rf_qpsk_pipeline.py", "rf_qpsk_receiver.py",
             "rf_rrc_pipeline.py", "rf_rrc_receiver.py", "rf_modem_result.py")
    for name in names:
        (tmp_path / name).write_text("# fake\n", encoding="utf-8")
    modules = {}
    for name in names:
        stem = name[:-3]
        version = {"rf_bpsk_receiver.py": "bpsk-rect-snapshot-v2",
                   "rf_qpsk_receiver.py": "qpsk-rect-snapshot-v1",
                   "rf_rrc_receiver.py": "rrc-alpha035-sps20-v3"}.get(name, "v1")
        modules[stem] = types.SimpleNamespace(
            __name__=stem, __file__=str(tmp_path / name), VERSION=version)
    manifest = dict(release_id="fake-rrc-release", software_contract=contract,
                    software={"rf_bpsk_receiver.py": "a" * 64,
                              "rf_qpsk_receiver.py": "b" * 64,
                              "rf_rrc_receiver.py": "c" * 64})
    monkeypatch.setattr(R, "verify_release",
                        lambda directory, require_software=True: manifest)
    monkeypatch.setattr(W.importlib, "import_module", lambda name: modules[name])
    worker = W.RfStatusWorker(H.FakeCaptureClient(blocked=False), poll_interval=100)
    worker._release_directory = tmp_path
    worker._ensure_demod()
    return worker


def test_worker_release_gate_loads_rrc_only_for_new_contract(tmp_path, monkeypatch):
    old = _fake_demod_release(tmp_path / "qpsk", monkeypatch,
                              "cnn-bpsk-modrec4-qpsk-v1")
    assert old.demod_status()["rrc_available"] is False
    new = _fake_demod_release(tmp_path / "rrc", monkeypatch,
                              "cnn-bpsk-modrec4-qpsk-rrc-v1")
    assert new.demod_status()["available"] is True
    assert new.demod_status()["qpsk_available"] is True
    assert new.demod_status()["rrc_available"] is True
    assert new.demod_status()["rrc_algorithm_version"] == "rrc-alpha035-sps20-v3"
