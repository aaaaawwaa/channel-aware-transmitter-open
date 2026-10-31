"""Task 10: real IQ -> optional verified CNN -> frozen local-only suggestion."""
import copy
import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_status_worker as W
import ui_model as M
import test_rf_model_release as R
import test_rf_rx_pipeline as P
import test_rf_rx_view as V


def release(tree, status="experimental", label="TONE", energy_floor=1.):
    manifest, receipt = R._release(tree, contract="cnn-v1", artifact=True)
    path = tree / "rf_model.json"
    doc = json.loads(path.read_text())
    doc["status"] = status
    doc["weights"]["head.bias"]["values"] = [10. if name == label else 0.
                                                 for name in R.CLASSES]
    doc["thresholds"]["min_complex_rms"] = energy_floor
    R._write(path, doc)
    evaluation = json.loads((tree / "rf_model_evaluation.json").read_text())
    evaluation["model_sha256"] = R.R.sha256_file(path)
    R._write(tree / "rf_model_evaluation.json", evaluation)
    manifest["model_artifact"]["status"] = status
    R._rehash_artifact(tree, manifest, receipt)
    return manifest


class Client(P.FakeCaptureClient):
    def __init__(self, release_id, age=0., **kwargs):
        super().__init__(samples=4096, blocked=False, **kwargs)
        self.release_id = release_id
        self._connection_generation = 1
        self._config_revision = 0
        self.age = age

    def capture(self, args, physical=False):
        frame = super().capture(args, physical)
        return replace(frame, release_id=self.release_id,
                       completed_at_monotonic=frame.completed_at_monotonic - self.age)


def captured(tree, status="experimental", model_label="TONE", energy_floor=1., **kwargs):
    manifest = release(tree, status, label=model_label, energy_floor=energy_floor)
    client = Client(manifest["release_id"], **kwargs)
    worker = W.RfStatusWorker(client, release_directory=tree)
    worker.submit_capture({"samples": 4096, "ratio": 0})
    payload, state = P.drain(worker)
    assert state["state"] == "done", state
    return worker, payload


def land(worker, payload):
    app = V.rx_app()
    app.rf_status["release_id"] = payload["frame"]["release_id"]
    app.set_rx_model_status(worker.model_status())
    app.rx_capture_submitted()
    assert app.set_rx_capture_result(payload)[0]
    return app


def test_experimental_predictions_are_frozen_but_never_actionable(tmp_path):
    worker, payload = captured(tmp_path)
    result = payload["model_result"]
    assert result.capture_id == payload["frame"]["capture_id"]
    assert result.label == "TONE" and result.experimental and result.scores
    assert result.model_sha256 and result.preprocessing_version == "dc-complex-rms-v1"
    with pytest.raises(AttributeError):
        result.label = "QPSK"
    app = land(worker, payload)
    assert not app.capabilities.model and app.rx_model_result is result
    assert app.rx_fill_block() is not None and app.rx_fill_draft() is not None
    assert M.rx_auto_block(app) == M.UNSUPPORTED
    worker.stop()


def test_production_fill_rechecks_freshness_and_only_edits_allowed_draft(tmp_path, monkeypatch):
    worker, payload = captured(tmp_path, "production")
    app = land(worker, payload)
    assert app.capabilities.model and app.rx_fill_block() is None
    app.recommendation.action.update(loader="/bin/sh", symrate_hz=5000000)
    calls = copy.deepcopy(worker.client.calls)
    before = app.actual_mode
    before_rate = app.rx_draft['symrate_hz']
    assert app.rx_fill_draft() is None
    assert app.actual_mode == before and worker.client.calls == calls
    assert "loader" not in app.rx_draft
    assert app.rx_draft["symrate_hz"] == before_rate, "classifier cannot estimate symbol rate"
    clock = time.monotonic()
    monkeypatch.setattr(M.time, "monotonic", lambda: clock + 31)
    assert app.rx_fill_block() == M.STALE and app.rx_fill_draft() is not None
    assert "过期" in app.rx_model_display()["state"]
    assert M.rx_auto_block(app) == M.UNSUPPORTED
    monkeypatch.undo()
    worker.stop()


@pytest.mark.parametrize("kind,reason", [("zero", "no_energy"), ("clipped", "clipping"),
                                          ("sampling", "unsupported_domain")])
def test_quality_gates_preserve_base_rx_and_show_unknown(tmp_path, kind, reason):
    kwargs = {"zero": {"amplitude": 0}, "clipped": {"clipped": True},
              "sampling": {"rate": 30000000}}[kind]
    worker, payload = captured(tmp_path, **kwargs)
    assert payload["analysis"] is not None
    assert payload["model_result"].label == "UNKNOWN"
    assert payload["model_result"].reason == reason
    worker.stop()


@pytest.mark.parametrize("kind", ["absent", "bad_hash", "rx-v1"])
def test_no_or_bad_model_keeps_base_rx(tmp_path, kind):
    if kind == "absent":
        directory, release_id = None, P.RELEASE
    elif kind == "rx-v1":
        manifest, _ = R._release(tmp_path, contract="rx-v1")
        directory, release_id = tmp_path, manifest["release_id"]
    else:
        manifest = release(tmp_path)
        with (tmp_path / "rf_model.json").open("ab") as handle:
            handle.write(b" ")
        directory, release_id = tmp_path, manifest["release_id"]
    worker = W.RfStatusWorker(Client(release_id), release_directory=directory)
    worker.submit_capture({"samples": 4096, "ratio": 0})
    payload, state = P.drain(worker)
    assert state["state"] == "done" and payload["analysis"]
    assert payload.get("model_result") is None
    assert worker.model_status()["production"] is False
    worker.stop()


@pytest.mark.parametrize("invalidate", ["stop", "generation", "config"])
def test_stop_or_context_change_during_inference_discards_prediction_and_capture(
        tmp_path, monkeypatch, invalidate):
    import rf_cnn_runtime as CNN
    entered, unblock = threading.Event(), threading.Event()
    original = CNN.TinyIQCNNModel.predict

    def blocked(self, *args, **kwargs):
        assert threading.current_thread().name == "rf-rx-capture"
        entered.set()
        assert unblock.wait(5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CNN.TinyIQCNNModel, "predict", blocked)
    manifest = release(tmp_path)
    client = Client(manifest["release_id"])
    worker = W.RfStatusWorker(client, release_directory=tmp_path)
    worker.submit_capture({"samples": 4096, "ratio": 0})
    assert entered.wait(5)
    if invalidate == "stop":
        assert worker.cancel_capture()["state"] == "cancelling"
    elif invalidate == "generation":
        client._connection_generation += 1
    else:
        client._config_revision += 1
    unblock.set()
    payload, state = P.drain(worker)
    assert payload is None and state["state"] == "cancelled"
    assert state["close_confirmed"] and client.closed == 1
    worker.stop()


@pytest.mark.parametrize("page", [M.PAGE_AI, M.PAGE_RX])
def test_smart_render_uses_frozen_scores_source_and_version_without_inference(
        tmp_path, monkeypatch, page):
    worker, payload = captured(tmp_path)
    app = land(worker, payload)
    app.rx_subpage = "smart"

    def forbidden(*args, **kwargs):
        pytest.fail("model IO/inference ran on the input/draw path")

    monkeypatch.setattr(worker, "_predict_frame", forbidden)
    monkeypatch.setattr(worker, "_ensure_model", forbidden)
    _lcd, view = V.rx_view(app, page=page, on_rx=V.callback(app, worker))
    texts = []
    original = view._t

    def collect(canvas, x, y, text, *args):
        texts.append(str(text))
        return original(canvas, x, y, text, *args)

    monkeypatch.setattr(view, "_t", collect)
    view.invalidate()
    view.render()
    text = "\n".join(texts)
    assert "模型得分" in text and "可信度" not in text and "概率" not in text
    assert "rx.capture" in text and "tiny-iq-cnn-v1" in text and "实验" in text
    assert "生产建议" in text
    assert (view.ai_fill_btn if page == M.PAGE_AI else view.rx_fill_btn).enabled is False
    worker.stop()


def test_generic_recommendation_cannot_replace_frozen_accepted_evidence(tmp_path):
    worker, payload = captured(tmp_path, "production")
    app = land(worker, payload)
    with pytest.raises(AttributeError):
        app.recommendation.mode = "QPSK"
    before = copy.deepcopy(app.rx_draft)
    app.set_recommendation({"mode": "QPSK", "stale": False,
                            "action": {"decim": 4, "symrate_hz": 3000000}})
    assert app.rx_fill_draft() is not None and app.rx_draft == before
    worker.stop()


def test_rx_only_release_does_not_import_optional_runtime(tmp_path, monkeypatch):
    manifest, _ = R._release(tmp_path, contract="rx-v1")
    original = W.importlib.import_module

    def unavailable(name, *args, **kwargs):
        if name == "rf_cnn_runtime":
            pytest.fail("rx-v1 attempted to import the optional CNN runtime")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(W.importlib, "import_module", unavailable)
    worker = W.RfStatusWorker(Client(manifest["release_id"]), release_directory=tmp_path)
    worker.submit_capture({"samples": 4096, "ratio": 0})
    payload, status = P.drain(worker)
    assert status["state"] == "done" and payload["model_result"] is None
    worker.stop()


@pytest.mark.parametrize("label,floor,reason", [("OTHER", 1., "other"),
                                                ("TONE", 5000., "below_energy_floor")])
def test_other_and_low_energy_are_unknown_without_recommendation(tmp_path, label, floor, reason):
    worker, payload = captured(tmp_path, "production", model_label=label, energy_floor=floor)
    result = payload["model_result"]
    assert result.label == "UNKNOWN" and result.rejected and result.reason == reason
    app = land(worker, payload)
    assert app.rx_fill_block() is not None and not app.recommendation.actionable
    worker.stop()


def test_long_inference_does_not_restart_freshness_at_publication(tmp_path):
    worker, payload = captured(tmp_path, "production", age=31.)
    result = payload["model_result"]
    assert result.completed_at_monotonic == payload["frame"]["completed_at_monotonic"]
    app = land(worker, payload)
    assert app.rx_fill_block() == M.STALE and not app.recommendation.actionable
    assert "过期" in app.rx_model_display()["state"]
    worker.stop()


def test_model_result_cannot_land_on_a_different_capture(tmp_path):
    worker, payload = captured(tmp_path, "production")
    payload["model_result"] = replace(payload["model_result"], capture_id="old-capture")
    app = land(worker, payload)
    assert app.rx_model_result is None and app.rx_fill_block() is not None
    worker.stop()


@pytest.mark.parametrize("field,value", [("model_sha256", "0" * 64),
                                         ("model_version", "wrong-network"),
                                         ("preprocessing_version", "wrong-preprocessing")])
def test_prediction_identity_must_match_attested_worker_model(tmp_path, field, value):
    worker, payload = captured(tmp_path, "production")
    payload["model_result"] = replace(payload["model_result"], **{field: value})
    app = land(worker, payload)
    assert app.rx_model_result is None
    assert app.rx_fill_block() is not None and not app.recommendation.actionable
    worker.stop()


@pytest.mark.parametrize("field", ["model_sha256", "model_version", "preprocessing_version"])
def test_missing_attested_identity_invalidates_prediction_and_recommendation(tmp_path, field):
    worker, payload = captured(tmp_path, "production")
    app = land(worker, payload)
    status = worker.model_status()
    status.pop(field, None)
    app.set_rx_model_status(status)
    assert app.rx_model_result is None and not app.capabilities.model
    assert app.rx_fill_block() is not None and not app.recommendation.actionable
    worker.stop()


@pytest.mark.parametrize("kind", ["config", "session"])
def test_prediction_and_recommendation_invalidate_with_existing_rx_lifecycle(tmp_path, kind):
    worker, payload = captured(tmp_path, "production")
    app = land(worker, payload)
    if kind == "config":
        app._rx_config_revision += 1
        app._invalidate_rx_analysis_if_config_changed()
    else:
        app._invalidate_rx_analysis("会话已断开")
    assert app.rx_model_result is None and not app.recommendation.actionable
    assert app.rx_fill_block() is not None
    worker.stop()
