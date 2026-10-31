"""Worker QPSK handoff: explicit config, no stale BER, legacy path intact."""
import dataclasses
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rf_status_worker as W
import rf_qpsk_pipeline as P
import test_rf_rx_pipeline as H
from test_rf_qpsk_receiver import _frame


def qpsk_frame(capture_id, *, random_payload=False, seed=39):
    return dataclasses.replace(
        _frame(seed=seed, offset=4, phase=0.2, iq_order="QI",
               random_payload=random_payload),
        capture_id=capture_id, release_id=H.RELEASE,
        completed_at_monotonic=time.monotonic())


def prepared(frames):
    client = H.FakeCaptureClient(blocked=False, samples=4096)
    queue = list(frames)

    def capture(args, physical=False):
        client.calls.append(("capture", dict(args), bool(physical)))
        return queue.pop(0)
    client.capture = capture
    worker = W.RfStatusWorker(client, poll_interval=100)
    worker._demod_checked = True
    worker._demod_pipeline = None
    worker._qpsk_pipeline = P
    worker._demod_info = dict(available=False, qpsk_available=True,
                              release_id=H.RELEASE, reason="QPSK ready",
                              algorithm_version=None, algorithm_sha256=None,
                              qpsk_algorithm_version=P.VERSION,
                              qpsk_algorithm_sha256="b" * 64)
    draft = dict(expected_mode="QPSK", symrate_hz=3_000_000, shaping="RECT",
                 reference_enabled=True, reference_seed=39, decim=1,
                 rx_len=4096, input_fs_hz=60_000_000)
    submitted = P.freeze_qpsk_demod_config(draft, 7, iq_order="QI")
    return client, worker, submitted


def test_qpsk_submission_runs_real_pipeline_and_binds_frame():
    client, worker, submitted = prepared([qpsk_frame("qpsk-1")])
    assert worker.demod_status()["qpsk_available"] is True
    assert worker.demod_status()["qpsk_algorithm_version"] == P.VERSION
    reply = worker.submit_capture(dict(samples=4096, ratio=0),
                                  demod_config=submitted, demod_revision=7)
    assert reply["queued"]
    payload, status = H.drain(worker)
    assert status["state"] == W.RX_CAPTURE_DONE
    envelope = payload["demod_result"]
    assert isinstance(envelope, P.QpskEnvelope)
    assert envelope.submitted == submitted
    assert envelope.result.capture_id == payload["frame"]["capture_id"] == "qpsk-1"
    assert envelope.result.reason == ""
    assert (envelope.result.bit_errors, envelope.result.compared_bits) == (0, 128)
    assert envelope.result.ber == 0.0
    assert envelope.result.valid_symbols == 127
    assert client.calls[0][1] == dict(samples=4096, ratio=0)


def test_second_qpsk_frame_does_not_keep_previous_ber():
    client, worker, submitted = prepared([
        qpsk_frame("qpsk-good"),
        qpsk_frame("qpsk-bad", random_payload=True),
    ])
    assert worker.submit_capture(dict(samples=4096, ratio=0), demod_config=submitted)["queued"]
    first, _ = H.drain(worker)
    assert first["demod_result"].result.ber == 0.0

    assert worker.submit_capture(dict(samples=4096, ratio=0), demod_config=submitted)["queued"]
    second, status = H.drain(worker)
    second_result = second["demod_result"].result
    assert status["state"] == W.RX_CAPTURE_DONE
    assert second_result.capture_id == "qpsk-bad"
    assert second_result.reference_sync is not True
    assert second_result.bit_errors is None
    assert second_result.compared_bits is None
    assert second_result.ber is None


def test_qpsk_config_is_rejected_when_qpsk_pipeline_is_absent():
    client, worker, submitted = prepared([qpsk_frame("x")])
    worker._qpsk_pipeline = None
    reply = worker.submit_capture(dict(samples=4096, ratio=0),
                                  demod_config=submitted)
    assert reply.get("queued") is None and reply["code"] == "DEMOD_UNAVAILABLE"
    assert worker.capture_status()["state"] == W.RX_CAPTURE_IDLE


def test_old_bpsk_demod_status_shape_stays_accepted():
    client = H.FakeCaptureClient(blocked=False, samples=4096)
    worker = W.RfStatusWorker(client, poll_interval=100)
    worker._demod_checked = True
    worker._demod_pipeline = H  # existing legacy tests inject this module
    worker._demod_info = dict(available=True, release_id=H.RELEASE,
                              algorithm_version="bpsk-rect-snapshot-v2",
                              algorithm_sha256="a" * 64, reason="ready")
    assert worker.demod_status()["available"] is True



def _fake_demod_release(tmp_path, monkeypatch, contract):
    import merged_release as R
    tmp_path.mkdir(parents=True, exist_ok=True)
    names = ("rf_bpsk_pipeline.py", "rf_bpsk_receiver.py",
             "rf_qpsk_pipeline.py", "rf_qpsk_receiver.py", "rf_modem_result.py")
    for name in names:
        (tmp_path / name).write_text("# fake release module\n", encoding="utf-8")
    modules = {}
    for name in names:
        modules[name[:-3]] = types.SimpleNamespace(
            __name__=name[:-3], __file__=str(tmp_path / name),
            VERSION=("qpsk-rect-snapshot-v1" if name == "rf_qpsk_receiver.py"
                     else "bpsk-rect-snapshot-v2"))
    manifest = dict(release_id="fake-release", software_contract=contract,
                    software={"rf_bpsk_receiver.py": "a" * 64,
                              "rf_qpsk_receiver.py": "b" * 64})
    monkeypatch.setattr(R, "verify_release",
                        lambda directory, require_software=True: manifest)
    monkeypatch.setattr(W.importlib, "import_module", lambda name: modules[name])
    worker = W.RfStatusWorker(H.FakeCaptureClient(blocked=False), poll_interval=100)
    worker._release_directory = tmp_path
    worker._ensure_demod()
    return worker


def test_demod_contract_gate_old_has_no_qpsk_new_has_qpsk(tmp_path, monkeypatch):
    old = _fake_demod_release(tmp_path / "old", monkeypatch,
                              "cnn-bpsk-modrec4-v1")
    assert old.demod_status()["available"] is True
    assert old.demod_status()["qpsk_available"] is False
    new = _fake_demod_release(tmp_path / "new", monkeypatch,
                              "cnn-bpsk-modrec4-qpsk-v1")
    assert new.demod_status()["available"] is True
    assert new.demod_status()["qpsk_available"] is True
    assert new.demod_status()["qpsk_algorithm_version"] == "qpsk-rect-snapshot-v1"
