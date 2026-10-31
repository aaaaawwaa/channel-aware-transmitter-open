"""Dual-backend routing tests: ACM keeps command plans, AD9361 uses RF load/start."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui_model as M
import pynq_status as S

OPS = tuple(M.RF_CONTROL_OP.values()) + ("tx.load", "tx.start")
PARAMS = {
    "lo_hz": 80000000,
    "sample_rate_hz": 60000000,
    "attenuation_mdB": 0,
    "phy_mode_1r1t": 0,
    "rx_bandwidth_hz": 30000000,
    "tx_bandwidth_hz": 30000000,
    "idelay_en": 1,
    "idelay_tap": 7,
}


def snapshot(state="READY", ready=True, selected=False, stale=False):
    return {
        "online": True, "mode": "status_only", "armed": True, "core": True,
        "control_ops": OPS, "daemon_state": state, "tx_session_ready": ready,
        "stale": stale,
        "pl_status": {"io_ok": True,
                      "tx": {"source_selected": selected, "length_words": 1024,
                             "position_words": 100}},
    }


def configured():
    app = M.UiApp()
    app.selected_device = "AD9361"
    app.set_rf_status(snapshot(ready=False))
    for k, v in PARAMS.items():
        assert app.set_rf_parameter(k, v) is None
    app.set_rf_control_result("configure", {"ok": True, "args": PARAMS})
    return app


class FakeWorker:
    def __init__(self):
        self.calls = []

    def submit_control(self, action, token_path, args):
        self.calls.append((action, args))
        return {"queued": True}

    def control(self, action, token_path=None, args=None):
        self.calls.append((action, args))
        return {"ok": True}


def test_acm_digital_begin_keeps_original_set_digmod_command_plan():
    app = M.UiApp()
    app.set_observed(M.default_config(), "OFF")
    app.connected = True
    app.fresh = True
    app.selected_device = "ACM2108"
    result, plan = app.begin(M.APPLY_DIGITAL)
    assert result == M.OK
    assert plan.device == "ACM2108"
    assert any(c.startswith("SET DIGMOD") for c in plan.commands)
    assert not any("tx.load" in c for c in plan.commands)


def test_ad9361_digital_load_routes_to_rf_worker_and_not_awg_set():
    app = configured()
    app.set_rf_signal_kind("digital")
    assert app.set_rf_digital_parameter("symbol_rate_hz", 2000000) is None
    worker = FakeWorker()
    S._rf_control_action(app, worker, "load")
    assert len(worker.calls) == 1
    action, args = worker.calls[0]
    assert action == "load"
    assert args["waveform"] == "digital"
    assert args["mode"] == "QPSK"
    assert args["sample_rate_hz"] == 60000000
    assert args["symbol_rate_hz"] == 2000000
    # No ACM command plan was created.
    assert app.in_flight is None
    assert app.pending is False


def test_acm_selected_never_sends_rf_load_to_worker():
    app = configured()
    app.selected_device = "ACM2108"
    worker = FakeWorker()
    S._rf_control_action(app, worker, "load")
    assert worker.calls == []
    assert app.rf_control["last_code"] == "TARGET_MISMATCH"


def test_editing_digital_draft_invalidates_start_and_keeps_acm_draft_untouched():
    app = configured()
    before_acm_dig = dict(app.draft["dig"])
    app.set_rf_signal_kind("digital")
    app.set_rf_digital_parameter("mode", "QAM16")
    # A loaded digital signal should be invalidated by any RF digital edit.
    assert "start" not in app.rf_control["actions"]
    assert app._rf_tx_loaded is False
    assert app.draft["dig"] == before_acm_dig, "AD9361 edits must not touch ACM draft"


def test_digital_load_success_sets_loaded_kind_and_start_available():
    app = configured()
    app.set_rf_signal_kind("digital")
    assert app.set_rf_digital_parameter("symbol_rate_hz", 2000000) is None
    worker = FakeWorker()
    S._rf_control_action(app, worker, "load")
    app.set_rf_status(snapshot())
    app.set_rf_control_result("load", {"ok": True, "args": worker.calls[0][1]})
    assert app._rf_loaded_kind == "digital"
    assert "start" in app.rf_control["actions"]


def test_rf_digital_symbol_rate_setter_accepts_positive_and_rejects_nonpositive():
    app = configured()
    app.set_rf_signal_kind("digital")
    assert app.set_rf_digital_parameter("symbol_rate_hz", 2000000) is None
    assert app.set_rf_digital_parameter("symbol_rate_hz", 0) is not None
    assert app.set_rf_digital_parameter("symbol_rate_hz", -1) is not None
    assert app.set_rf_digital_parameter("symbol_rate_hz", 2000000.0) is not None


def test_channel_apply_reloads_loaded_kind_with_impairment():
    app = configured()
    app.set_rf_parameter("tx_waveform", 1)
    app.set_rf_parameter("tx_cycles", 32)
    app.set_rf_parameter("tx_amplitude", 1500)
    app.set_rf_signal_kind("waveform")
    worker = FakeWorker()
    S._rf_control_action(app, worker, "load")
    app.set_rf_status(snapshot())
    app.set_rf_control_result("load", {"ok": True, "args": worker.calls[0][1]})
    assert app._rf_loaded_kind == "waveform"

    app.set_rf_channel_parameter("noise_pct", 10)
    app.set_rf_channel_tap(0, "gain_q8", 128)
    assert app.set_rf_channel_apply() is None
    S._rf_control_action(app, worker, "load")
    assert len(worker.calls) == 2
    action, args = worker.calls[-1]
    assert action == "load"
    assert args["waveform"] == "sine"
    assert args["impairment"]["noise_pct"] == 10
    assert args["impairment"]["taps"][0]["gain_q8"] == 128


def test_channel_apply_without_loaded_signal_is_refused():
    app = configured()
    assert app.set_rf_channel_apply() is not None
    worker = FakeWorker()
    S._rf_control_action(app, worker, "load")
    assert worker.calls == []
    assert app.rf_control["last_code"] == "INVALID_ARGUMENT"


def test_rf_digital_amplitude_setter_carries_into_next_load_plan():
    app = configured()
    app.set_rf_signal_kind("digital")
    assert M.rf_digital_parameter_error("amplitude", 1000) is None
    assert M.rf_digital_parameter_error("amplitude", 2048) is not None
    assert app.set_rf_digital_parameter("amplitude", 1000) is None
    args, why = app.rf_parameter_plan("load")
    assert why is None, why
    assert args["amplitude"] == 1000
    assert app.set_rf_digital_parameter("amplitude", 2048) is not None


def test_rf_digital_symbol_rate_uses_fs_dependent_range_and_rejects_invalid():
    app = configured()
    app.set_rf_signal_kind("digital")
    assert M.rf_digital_symrate_range(60_000_000) == (1_875_000, 30_000_000)
    assert app.rf_digital_symrate_bounds() == (1_875_000, 30_000_000)
    for rate in (1_875_000, 2_000_000, 30_000_000):
        assert app.set_rf_digital_parameter("symbol_rate_hz", rate) is None, rate
    for rate in (1_000_000, 31_000_000, 2_000_001):
        assert app.set_rf_digital_parameter("symbol_rate_hz", rate) is not None, rate
