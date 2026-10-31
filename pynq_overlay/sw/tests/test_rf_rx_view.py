"""Task 4: the on-board receive page driven by REAL hit/press/release.

RED FIRST.  Before the implementation the capture chip is hard-wired disabled and the
page has no RX action path at all.  What is pinned here:

  * the capture control is unlocked at RUNTIME from the daemon's advertisement + current
    state + the implemented client -- never hard-coded on;
  * every refusal the brief names (not READY, missing capability, busy, stale session,
    cancelled, configuration changed) has its own negative path;
  * a real touch on the chip reaches the production callback and the background worker,
    and an ordinary capture NEVER transmits;
  * the loopback is a separate, explicitly confirmed action;
  * 'clear' clears the LOCAL snapshot only, and history stays separate from current state;
  * the model's "fill draft" edits the draft only;
  * overload and expiry stay VISIBLE as quality reasons;
  * the page shows spectrum / I/Q / quality instead of a fabricated frame payload.
"""
import dataclasses
import inspect
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_view as H
import test_rf_rx_pipeline as P
import pynq_status as daemon
from rf_status_worker import RfStatusWorker

M, V, ui = H.M, H.V, H.ui

PARAMS = dict(lo_hz=80000000, sample_rate_hz=60000000, attenuation_mdB=40000,
              phy_mode_1r1t=0, rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000,
              idelay_en=1, idelay_tap=7)
OPS = tuple(P.CONTROLS)
CAPTURE_OP = "rx.capture"


def rx_status(state="READY", armed=True, ops=OPS, stale=False, online=True,
              source=False, rx_armed=False, tx_ready=False, **kw):
    snapshot = {
        "online": online, "stale": stale, "error": None,
        "received_at_monotonic": time.monotonic(), "mode": "control", "core": True,
        "armed": armed, "daemon_state": state, "control_ops": list(ops),
        "tx_session_ready": tx_ready, "rx_session_ready": False,
        "pl_status": {"io_ok": True, "sampled_at_monotonic_ns": 1000,
                      "tx": {"source_selected": source, "length_words": 0,
                             "position_words": 0},
                      "rx": {"armed": rx_armed, "done": False, "position_words": 0}},
    }
    snapshot.update(kw)
    return snapshot


def rx_app(status=None, params=None):
    """The state an operator reaches before an ordinary capture: AD9361 selected, a live
    armed control surface that advertises rx.capture, READY, and one applied config."""
    a = M.UiApp()
    a.set_observed(M.default_config(), "OFF")
    a.select_device("AD9361")
    a.set_rf_status(rx_status() if status is None else status)
    for name, value in (params or PARAMS).items():
        assert a.set_rf_parameter(name, value) is None, name
    a.set_rf_control_result("configure", {"ok": True, "args": dict(params or PARAMS)})
    return a


def rx_view(app, on_rx=None, page=M.PAGE_RX, **kw):
    lcd = H.FakeLcd()
    v = V.UiView(lcd, app, on_rx=on_rx, **kw)
    app.page = page
    v.invalidate()
    v.render()
    return lcd, v


def callback(app, worker):
    return lambda event: daemon._rx_action(app, worker, event)


def land(app, worker, timeout=5.0):
    payload, status = P.drain(worker, timeout)
    app.set_rx_capture_status(status)
    if payload is not None:
        app.set_rx_capture_result(payload)
    return payload, status


# ------------------------------------------------- dynamic capability, real negatives

def test_the_capture_chip_is_unlocked_only_by_a_live_ready_advertisement():
    # 1. no daemon reply at all: nothing is advertised, so nothing is enabled.
    blind = M.UiApp()
    blind.set_observed(M.default_config(), "OFF")
    _lcd, v = rx_view(blind)
    assert v.rx_capture_btn.enabled is False
    assert blind.capabilities.capture is False
    # 2. a recognised control surface that does NOT advertise rx.capture.
    app = rx_app(status=rx_status(ops=("control.arm", "control.disarm", "rf.configure")))
    _lcd, v = rx_view(app)
    assert app.capabilities.capture is False
    assert v.rx_capture_btn.enabled is False
    # 3. advertised and armed and READY: now it is enabled -- derived, not hard-coded.
    app = rx_app()
    _lcd, v = rx_view(app)
    assert app.capabilities.capture is True
    assert v.rx_capture_btn.enabled is True
    assert app.rx_capture_block_reason() is None
    # 4. the same advertisement and the same arm, but the state left READY.
    for state in ("FAULT", "OFFLINE", "TX_ACTIVE", "RX_CAPTURING"):
        app = rx_app(status=rx_status(state=state))
        _lcd, v = rx_view(app)
        assert app.capabilities.capture is False, state
        assert v.rx_capture_btn.enabled is False, state
    # 5. disarmed: the daemon would refuse the capture, so the chip is off.
    app = rx_app(status=rx_status(armed=False))
    _lcd, v = rx_view(app)
    assert app.capabilities.capture is False and v.rx_capture_btn.enabled is False


def test_the_unlock_is_recomputed_from_every_new_snapshot():
    app = rx_app()
    _lcd, v = rx_view(app)
    assert v.rx_capture_btn.enabled is True
    app.set_rf_status(rx_status(state="FAULT"))
    v.render()
    assert app.capabilities.capture is False and v.rx_capture_btn.enabled is False
    app.set_rf_status(rx_status())
    v.render()
    assert app.capabilities.capture is True and v.rx_capture_btn.enabled is True


NEGATIVES = (
    ("not_ready", M.NOT_READY, lambda a: None, dict(status=rx_status(state="FAULT"))),
    ("missing_capability", M.UNSUPPORTED, lambda a: None,
     dict(status=rx_status(ops=("control.arm", "rf.configure")))),
    ("not_armed", M.UNSUPPORTED, lambda a: None, dict(status=rx_status(armed=False))),
    ("stale_session", M.STALE, lambda a: None, dict(status=rx_status(stale=True))),
    ("offline", M.DISCONNECTED, lambda a: None, dict(status=rx_status(online=False))),
    ("control_busy", M.BUSY, lambda a: a.set_rf_control_pending("configure"), {}),
    ("config_changed", M.CONFIG_CHANGED,
     lambda a: a.set_rf_parameter("lo_hz", 81000000), {}),
)


@pytest.mark.parametrize("name,code,break_it,kwargs",
                         NEGATIVES, ids=[n[0] for n in NEGATIVES])
def test_each_refusal_has_its_own_negative_path(name, code, break_it, kwargs):
    app = rx_app(**kwargs)
    break_it(app)
    assert app.rx_capture_block_reason() == code, name
    _lcd, v = rx_view(app)
    assert v.rx_capture_btn.enabled is False, name


def test_a_capture_before_any_applied_configuration_is_refused():
    app = M.UiApp()
    app.set_observed(M.default_config(), "OFF")
    app.select_device("AD9361")
    app.set_rf_status(rx_status())
    # The daemon is armed and READY, but no configuration was ever applied: the IQ would
    # belong to parameters the panel is not running.
    assert app.capabilities.capture is True
    assert app.rx_capture_block_reason() == M.CONFIG_CHANGED
    _lcd, v = rx_view(app)
    assert v.rx_capture_btn.enabled is False


def test_a_reconfiguration_mid_capture_discards_the_old_result():
    app = rx_app()
    client = P.FakeCaptureClient(blocked=True)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        H.tap_at(v, *H.center(v.rx_capture_btn))
        assert client.entered.wait(2)
        # The operator re-applies the configuration while the capture is in flight, so the
        # frame the worker is about to deliver describes a session that no longer exists.
        app.set_rf_control_result("configure", {"ok": True, "args": dict(PARAMS)})
        client.release.set()
        payload, status = land(app, worker)
    finally:
        client.release.set()
        worker.stop()
    assert payload is not None, "the worker still validates and hands off the frame"
    assert status["state"] == "done"
    assert app.rx_analysis is None, "a frame from the old configuration must not land"
    assert app.rx_snapshot.state != "DONE"
    assert app.rx_capture["state"] == "cancelled"


def test_a_capture_already_in_flight_refuses_a_second_one_and_offers_cancel():
    app = rx_app()
    client = P.FakeCaptureClient(blocked=True)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        H.tap_at(v, *H.center(v.rx_capture_btn))
        assert client.entered.wait(2)
        assert M.rx_capture_inflight(app) is True
        assert app.rx_capture_block_reason() == M.BUSY
        # In flight is VISIBLE, and nothing has landed yet.
        assert "采集中" in app.rx_quality_text()
        assert app.rx_analysis is None
        # The SAME chip now cancels -- a second capture is never submitted.
        assert v.rx_capture_btn.text == "取消捕获"
        assert v.rx_capture_btn.enabled is True
        H.tap_at(v, *H.center(v.rx_capture_btn))
        assert app.rx_capture["state"] == "cancelling"
        assert "取消中" in app.rx_quality_text()
        client.release.set()
        _payload, status = land(app, worker)
    finally:
        client.release.set()
        worker.stop()
    submits = [c for c in client.calls if c[0] == "capture"]
    assert len(submits) == 1, "the panel submitted a second capture"
    assert status["state"] == "cancelled"
    assert app.rx_analysis is None and app.rx_snapshot.state != "DONE"


# ---------------------------------------------------------- the production touch path

def test_a_real_tap_reaches_the_worker_and_an_ordinary_capture_never_transmits():
    app = rx_app()
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        assert v.ui._hit(*H.center(v.rx_capture_btn)) is v.rx_capture_btn
        state_before = app.rf_control["state"]
        H.tap_at(v, *H.center(v.rx_capture_btn))
        payload, status = land(app, worker)
    finally:
        worker.stop()
    assert payload is not None and status["state"] == "done"
    assert client.calls == [("capture", {"samples": M.RX_DEFAULT_LEN, "ratio": 0}, False)]
    ops = [call[0] for call in client.calls]
    assert "tx.start" not in ops and "rf.init" not in ops and "tx.load" not in ops
    assert app.rf_control["state"] == state_before
    assert app.selected_device == "AD9361"
    assert app.rx_snapshot.state == "DONE"
    assert app.rx_snapshot.capture_id == "rx-test-0-0-1"
    assert app.rx_snapshot.input_fs_hz == P.FS and app.rx_snapshot.decim == 1
    assert app.rx_snapshot.actual_len == M.RX_DEFAULT_LEN
    assert app.rx_analysis is not None
    assert app.rx_analysis.spectrum and app.rx_analysis.preview


def test_the_touch_path_does_not_block_on_the_socket_or_the_fft():
    app = rx_app()
    client = P.FakeCaptureClient(blocked=True)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        started = time.monotonic()
        H.tap_at(v, *H.center(v.rx_capture_btn))
        elapsed = time.monotonic() - started
    finally:
        client.release.set()
        worker.stop()
    assert elapsed < 0.3, elapsed
    assert M.rx_capture_inflight(app) is True
    # Nothing is drawn until the worker publishes a fully frozen result.
    assert app.rx_analysis is None and app.rx_snapshot.state != "DONE"


def test_the_main_loop_lands_the_frozen_result_and_only_then_draws_it():
    app = rx_app()
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    worker._poll_once()  # The main loop must have live telemetry for the delivered frame.
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        screen = daemon._PanelScreen(v, None, None, None, worker)
        H.tap_at(v, *H.center(v.rx_capture_btn))
        assert client.entered.wait(2)
        # One frame of the production main loop lands the result.
        for _ in range(200):
            screen.render(daemon.Status())
            if app.rx_analysis is not None:
                break
            time.sleep(0.01)
    finally:
        worker.stop()
    assert app.rx_analysis is not None
    assert app.rx_snapshot.state == "DONE"
    assert worker.take_rx_result() == (None, worker.capture_status())


def test_a_refused_submission_lands_a_visible_reason_and_sends_nothing():
    app = rx_app()
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        app.set_rf_status(rx_status(state="FAULT"))
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        assert v.rx_capture_btn.enabled is False
        # Even if the chip were somehow tapped, the model still refuses.
        daemon._rx_action(app, worker, "capture")
    finally:
        worker.stop()
    assert client.calls == []
    assert app.rx_capture["state"] == "refused"
    assert M.NOT_READY in app.rx_capture["reason"]


# ------------------------------------------------------------------------ loopback

def test_the_loopback_is_a_separate_action_that_needs_explicit_confirmation():
    app = rx_app(status=rx_status(source=False, tx_ready=True))
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        assert v.rx_loopback_btn.enabled is True
        H.tap_at(v, *H.center(v.rx_loopback_btn))
        # The first tap only asks; nothing was sent and nothing was transmitted.
        assert client.calls == []
        assert app.rx_capture["pending_action"] == "loopback"
        assert v.rx_loopback_confirm_btn.visible is True
        assert v.rx_capture_btn.visible is False
        H.tap_at(v, *H.center(v.rx_loopback_confirm_btn))
        payload, status = land(app, worker)
    finally:
        worker.stop()
    assert payload is not None and status["state"] == "done"
    assert client.calls == [("capture",
                             {"samples": M.RX_DEFAULT_LEN, "ratio": 0,
                              "cable_confirmed": True, "attenuation_mdB": 46000},
                             True)]
    assert payload["frame"]["op"] == "loopback.physical.capture"


def test_the_loopback_can_be_cancelled_and_needs_a_loaded_tx():
    app = rx_app(status=rx_status(tx_ready=False))
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        # No loaded TX on the retained session: the physical path is refused locally.
        assert app.rx_loopback_block() == M.NOT_LOADED
        assert v.rx_loopback_btn.enabled is False
        daemon._rx_action(app, worker, "loopback")
        assert app.rx_capture["state"] == "refused"
        assert client.calls == []
    finally:
        worker.stop()
    app = rx_app(status=rx_status(tx_ready=True))
    _lcd, v = rx_view(app, on_rx=callback(app, worker))
    H.tap_at(v, *H.center(v.rx_loopback_btn))
    assert app.rx_capture["pending_action"] == "loopback"
    H.tap_at(v, *H.center(v.rx_loopback_cancel_btn))
    assert app.rx_capture["pending_action"] is None
    assert v.rx_capture_btn.visible is True
    assert client.calls == []


def test_an_unprompted_loopback_confirmation_is_refused():
    app = rx_app(status=rx_status(tx_ready=True))
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        daemon._rx_action(app, worker, "loopback_confirm")   # never prompted
        assert app.rx_capture["state"] == "refused"
        assert app.rx_capture["code"] == "NOT_WIRED"
        assert client.calls == [], "an unprompted confirmation sends nothing"
        daemon._rx_action(app, worker, "capture")            # an ordinary capture, still fine
        _payload, status = P.drain(worker)
    finally:
        worker.stop()
    assert status["state"] == "done"
    assert client.calls == [("capture",
                             {"samples": M.RX_DEFAULT_LEN, "ratio": 0}, False)]


def test_an_ordinary_capture_is_never_a_loopback():
    app = rx_app(status=rx_status(tx_ready=True))
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        H.tap_at(v, *H.center(v.rx_capture_btn))
        land(app, worker)
    finally:
        worker.stop()
    _op, args, physical = client.calls[0]
    assert physical is False
    assert "cable_confirmed" not in args and "attenuation_mdB" not in args
    assert app.rx_snapshot.source == "rx.capture"


# ------------------------------------------------------------- clear / fill / history

def test_clear_clears_the_local_snapshot_only_and_keeps_history_separate():
    app = rx_app()
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        H.tap_at(v, *H.center(v.rx_capture_btn))
        land(app, worker)
        assert app.rx_analysis is not None and app.rx_history == []
        calls = list(client.calls)
        assert v.rx_clear_btn.enabled is True
        H.tap_at(v, *H.center(v.rx_clear_btn))
    finally:
        worker.stop()
    assert app.rx_analysis is None, "clear must clear the local current snapshot"
    assert len(app.rx_history) == 1 and app.rx_analysis is None
    assert app.rx_history[0]["capture_id"] == "rx-test-0-0-1"
    assert client.calls == calls, "clear must not talk to the daemon"
    assert app.rf_control["state"] == "configured"


def test_fill_draft_only_edits_the_draft(tmp_path):
    # Task 10 fills only from current measured IQ and an accepted release model.
    from test_rf_smart_pipeline import captured, land
    worker, payload = captured(tmp_path, "production")
    app = land(worker, payload)
    client = worker.client
    calls = list(client.calls)
    app.rx_subpage = "smart"
    before_mode = app.actual_mode
    app.rx_draft.update(decim=4, rx_len=64)
    app.recommendation.action.update(loader="/bin/sh")  # returned copy is immutable evidence
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        assert app.rx_subpage == "smart"
        assert v.rx_fill_btn.enabled is True
        H.tap_at(v, *H.center(v.rx_fill_btn))
    finally:
        worker.stop()
    assert app.rx_draft["decim"] == 1 and app.rx_draft["rx_len"] == 4096
    assert app.actual_mode == before_mode
    assert client.calls == calls
    assert not hasattr(app, "loader")
    assert "loader" not in app.rx_draft


def test_fill_draft_refuses_a_recommendation_that_is_not_actionable():
    app = rx_app()
    app.rx_subpage = "smart"
    app.set_recommendation({"mode": "UNKNOWN", "stale": True})
    assert app.rx_fill_block() is not None
    assert app.rx_fill_draft() is not None


# ------------------------------------------------------------------ visible quality

def clipped_payload():
    frame = P.make_frame(amplitude=2047, clipped=True)
    return {"frame": frame.to_dict(), "analysis": P.ANA.summarize(frame)}


def test_overload_stays_visible_as_a_quality_reason():
    app = rx_app()
    app.rx_subpage = "overview"
    app.set_rx_capture_result(clipped_payload())
    codes = [q["code"] for q in M.rx_quality_reasons(app)]
    assert "overload" in codes, codes
    assert app.rx_snapshot.overflow is True
    assert app.rx_snapshot.valid is True
    assert app.rx_snapshot.usable is False, "a clipped record is never usable"
    _lcd, v = rx_view(app)
    text = "\n".join(H._rx_page_strings(app, v))
    assert "削顶" in text, text


def test_an_expired_snapshot_stays_visible_and_never_claims_current_reception():
    app = rx_app()
    app.rx_subpage = "overview"
    app.set_rx_capture_result(clipped_payload())
    landed = app.rx_analysis
    moment = landed.completed_at_monotonic
    fresh = [q["code"] for q in M.rx_quality_reasons(app, now=moment + 1.0)]
    assert "expired" not in fresh, fresh
    aged = [q["code"] for q in M.rx_quality_reasons(app, now=moment + 31.0)]
    assert "expired" in aged, aged
    assert app.rx_snapshot_is_current(now=moment + 31.0) is False
    assert app.rx_snapshot_is_current(now=moment + 1.0) is True
    # Age the FROZEN result 31 s and render the real page: the reason has to be drawn, and
    # the repaint has to happen (the freshness state is part of the frame signature).
    import dataclasses
    lcd, v = rx_view(app)
    app.rx_analysis = dataclasses.replace(landed, completed_at_monotonic=moment - 31.0)
    app.rx_analysis_summary = dict(app.rx_analysis_summary,
                                   completed_at_monotonic=moment - 31.0)
    v.render()
    text = "\n".join(H._rx_page_strings(app, v))
    assert "过期" in text, text
    assert app.rx_snapshot_is_current() is False


def test_a_cancelled_capture_lands_a_visible_reason_and_no_analysis():
    app = rx_app()
    app.set_rx_capture_status({"state": "cancelled", "cancelled": True,
                               "close_confirmed": False,
                               "reason": "关闭未确认: readback could not confirm"})
    codes = [q["code"] for q in M.rx_quality_reasons(app)]
    assert "close_unconfirmed" in codes, codes
    assert app.rx_analysis is None
    assert app.rx_snapshot.state != "DONE"
    assert app.rx_quality_text().find("关闭未确认") >= 0


# ------------------------------------------------- spectrum / IQ / no fake payload

def test_the_page_shows_spectrum_iq_and_quality_instead_of_a_frame_payload():
    app = rx_app()
    app.set_rx_capture_result(clipped_payload())
    app.rx_subpage = "sampling"
    _lcd, v = rx_view(app)
    text = "\n".join(H._rx_page_strings(app, v))
    assert "幅度谱" in text, text
    assert "IQ" in text, text
    assert app.rx_frames == (), "there is no frame payload in this version"
    assert app.rx_metrics.ber is None and app.rx_metrics.evm_pct is None
    for banned in ("SNR", "snr", "Vpp", "dBm", "EVM"):
        assert banned not in text, banned
    # The containment figure must be labelled as containment, never as a signal bandwidth.
    assert "99%" in text and "占用带宽" in text


def test_the_unconnected_measurements_stay_unavailable_not_faked():
    app = rx_app()
    app.rx_subpage = "demod"
    _lcd, v = rx_view(app)
    text = "\n".join(H._rx_page_strings(app, v))
    assert "未接入" in text
    assert app.capabilities.sync is False and app.capabilities.crc is False
    assert app.capabilities.ber is False and app.capabilities.evm is False
    assert app.rx_status("crc") is None and app.rx_status("carrier_sync") is None


# ------------------------------------------------- payload shapes (one place)

def apply_new_configuration(app, lo_hz=85000000):
    """One SUCCESSFUL, applied configuration change -- exactly what the panel sends.

    The operator edits the RF draft and then re-applies it; the daemon confirms.  Nothing
    here edits only the draft: the draft edit and the applied result are separate steps on
    purpose (see the negative criterion below).
    """
    assert app.set_rf_parameter("lo_hz", lo_hz) is None
    app.set_rf_control_result("configure", {"ok": True, "args": dict(PARAMS, lo_hz=lo_hz)})
    return app


def payload_app(make):
    """One receive page in the shape a real capture leaves behind.

    "session_lost" is the shape the review found missing: a capture DID land, and then the
    daemon session went away -- the page must stop presenting that analysis as current
    reception.  "config_changed" is the same shape reached the other way: the session is
    alive and retained, but the operator applied a NEW configuration, so the landed record
    was taken under parameters that are no longer the ones in force.
    """
    app = rx_app()
    if make == "clean":
        frame = P.make_frame(samples=4096, amplitude=1000)
        app.set_rx_capture_result({"frame": frame.to_dict(), "analysis": P.ANA.summarize(frame)})
    elif make == "clipped":
        app.set_rx_capture_result(clipped_payload())
    elif make == "expired":
        app.set_rx_capture_result(clipped_payload())
        landed = app.rx_analysis
        moment = landed.completed_at_monotonic
        app.rx_analysis = dataclasses.replace(landed, completed_at_monotonic=moment - 31.0)
        app.rx_analysis_summary = dict(app.rx_analysis_summary,
                                       completed_at_monotonic=moment - 31.0)
    elif make == "cancelled":
        app.set_rx_capture_status({"state": "cancelled", "cancelled": True,
                                   "close_confirmed": False, "reason": "关闭未确认"})
    elif make == "session_lost":
        frame = P.make_frame(samples=4096, amplitude=1000)
        app.set_rx_capture_result({"frame": frame.to_dict(), "analysis": P.ANA.summarize(frame)})
        app.set_rf_status(rx_status(online=False))
    elif make == "config_changed":
        frame = P.make_frame(samples=4096, amplitude=1000)
        app.set_rx_capture_result({"frame": frame.to_dict(), "analysis": P.ANA.summarize(frame)})
        apply_new_configuration(app)
    else:
        raise AssertionError(make)
    return app


def future_panels():
    """The two Panel rects the receive page draws with -- read from the view itself."""
    _lcd, v = rx_view(rx_app())
    return v._cols(M.PAGE_FUTURE)


def render_logged(app, subpage):
    """Render the receive page with the canvas text sink on; returns (lcd, boxes)."""
    app.rx_subpage = subpage
    lcd = H.FakeLcd()
    v = V.UiView(lcd, app)
    app.page = M.PAGE_FUTURE
    v.invalidate()
    sink = []
    ui.set_text_log(sink)
    try:
        v.render()
    finally:
        ui.set_text_log(None)
    return lcd, list(sink)


# --------------------------------------------------------------- visible geometry

@pytest.mark.parametrize("make", ["clean", "clipped", "expired", "cancelled", "session_lost",
                                  "config_changed"])
def test_the_receive_page_never_draws_outside_the_panel_it_belongs_to(make):
    """Two Panel rects on a fixed 800x480 screen; nothing may leave the card it is in.

    The criterion this replaces asserted that any pixel was drawn plus ONE corner pixel,
    so it could not fail on an overflowing layout at all.  This one measures every glyph
    run the canvas actually emitted against the panel bounds, and the bands outside the two
    panels -- which is what makes a label bleeding into the other column, or a chip poking
    into the margin, fail.

    Bounds alone would also be satisfied by a BLANK render, so the non-emptiness guard the
    replaced criterion did have is kept and strengthened here: each of the 6 x 4 renders
    must put ink in both cards and emit text that belongs to each of them.
    """
    app = payload_app(make)
    (lx, ly, lw, lh), (rx, ry, rw, rh) = future_panels()
    for subpage in M.RX_SUBPAGES:
        lcd, boxes = render_logged(app, subpage)
        a = lcd.array
        bg = a[0, 0]
        assert (a != bg).any(), (make, subpage, "the whole render is background")
        assert (a[ly:ly + lh, lx:lx + lw] != bg).any(), (make, subpage, "left card blank")
        assert (a[ry:ry + rh, rx:rx + rw] != bg).any(), (make, subpage, "right card blank")
        owners = set()
        for s, size, x, y in boxes:
            w, h = ui.text_size(s, size)
            if y + h <= ly or y >= ly + lh or x + w <= lx or x >= rx + rw:
                continue                    # header / footer / nav column: not this card
            owners.add("left" if x < rx else "right")
            assert x >= lx and x + w <= rx + rw, (make, subpage, s, x, x + w)
            assert y >= ly and y + h <= ly + lh, (make, subpage, s, y, y + h)
            owner_x, owner_w = (lx, lw) if x < rx else (rx, rw)
            assert x >= owner_x and x + w <= owner_x + owner_w, (
                "%s/%s: %r leaves its own panel (x %d..%d, panel %d..%d)"
                % (make, subpage, s, x, x + w, owner_x, owner_x + owner_w))
        assert owners == {"left", "right"}, (make, subpage,
                                             "a card got no text at all", owners)
        # Everything outside the two Panel rects, inside the work area, stays background.
        assert (a[ly:ly + lh, lx + lw:rx] == bg).all(), (make, subpage, "gap band")
        assert (a[ly + lh:V.FOOT_Y, lx:rx + rw] == bg).all(), (make, subpage, "below band")
        assert (a[ly:ly + lh, rx + rw:] == bg).all(), (make, subpage, "right band")
        # The screen own outer row/column, minus the two full-width separator lines.
        for y in range(ui.HEIGHT):
            if y in (V.HDR_H - 1, V.FOOT_Y):
                continue
            assert a[y, ui.WIDTH - 1] == bg, (make, subpage, "right column", y)
        assert (a[ui.HEIGHT - 1, :] == bg).all(), (make, subpage, "bottom row")

# ---------------------------------------- IMPORTANT-1: a lost session stops being current

def land_clean(app):
    """Land one good capture: the result only, exactly what the main loop hands over."""
    frame = P.make_frame(samples=4096, amplitude=1000)
    app.set_rx_capture_result({"frame": frame.to_dict(), "analysis": P.ANA.summarize(frame)})
    return frame


@pytest.mark.parametrize("name,status", [
    ("offline", rx_status(online=False)),
    ("stale", rx_status(stale=True)),
    ("fault", rx_status(state="FAULT")),
    ("disarmed", rx_status(armed=False)),
])
def test_a_lost_session_invalidates_the_current_analysis_and_draws_the_reason(name, status):
    """A landed analysis is CURRENT only while the session that produced it is still there.

    The screen used to contradict itself after a disconnect: the left column said
    "未接入真实采集, 不报告测量结果" while the right one still drew the whole
    receive-analysis block as DONE and the quality line said "无异常".
    """
    app = rx_app()
    land_clean(app)
    assert app.rx_analysis is not None and app.rx_snapshot.state == "DONE"
    app.set_rf_status(status)
    assert app.rx_analysis is None, name
    assert app.rx_analysis_summary is None, name
    assert app.rx_snapshot.state != "DONE", name
    assert app.rx_history and app.rx_history[0]["capture_id"] == "rx-test-0-0-1", name
    codes = [q["code"] for q in M.rx_quality_reasons(app)]
    assert "session_lost" in codes, (name, codes)
    _lcd, v = rx_view(app)
    text = "\n".join(H._rx_page_strings(app, v))
    assert "接收分析" not in text, (name, text)
    assert "质量: 无异常" not in text, (name, text)
    assert "质量: " in text, (name, text)          # the reason is DRAWN, not dropped


def test_the_retained_capture_generation_going_away_invalidates_the_landed_result():
    """rx_session_ready is the generation-scoped evidence the client publishes.

    It is true exactly while the connection generation that produced the frame is still the
    one the client retains, and it is cleared when that generation is dropped.  A status
    that still says READY/armed but no longer retains the capture session is a DIFFERENT
    session, so the frozen analysis stops being current reception.
    """
    app = rx_app()
    land_clean(app)
    app.set_rf_status(rx_status(rx_session_ready=True))
    assert app.rx_analysis is not None, "the retained session still proves it"
    app.set_rf_status(rx_status(rx_session_ready=False))
    assert app.rx_analysis is None, "the capture session was dropped"
    assert app.rx_snapshot.state != "DONE"
    assert "session_lost" in [q["code"] for q in M.rx_quality_reasons(app)]


def test_a_daemon_that_restarted_does_not_leave_its_old_measurement_on_screen():
    app = rx_app()
    land_clean(app)
    app.set_rf_status(rx_status(release_id="rx-release-someone-else"))
    assert app.rx_analysis is None
    assert "session_lost" in [q["code"] for q in M.rx_quality_reasons(app)]


@pytest.mark.parametrize("status", [
    rx_status(online=False), rx_status(stale=True), rx_status(state="FAULT"),
    rx_status(armed=False), rx_status(release_id="another-release"),
])
def test_a_late_capture_result_cannot_revive_a_failed_session(status):
    app = rx_app()
    app.rx_capture_submitted()
    app.set_rf_status(status)
    frame = P.make_frame()
    landed, reason = app.set_rx_capture_result(
        {"frame": frame.to_dict(), "analysis": P.ANA.summarize(frame)})
    assert landed is False and reason is not None
    assert app.rx_analysis is None and not app.rx_snapshot_is_current()
    assert "session_lost" in [q["code"] for q in M.rx_quality_reasons(app)]


def test_a_capture_on_a_previously_confirmed_session_cannot_survive_its_loss():
    app = rx_app()
    land_clean(app)
    app.set_rf_status(rx_status(rx_session_ready=True))
    app.rx_capture_submitted()
    app.set_rf_status(rx_status(rx_session_ready=False))
    # A later healthy status is a different retained session, not proof for the
    # capture that crossed the disconnect.
    app.set_rf_status(rx_status(rx_session_ready=True))
    frame = P.make_frame(capture_id="late-frame")
    landed, reason = app.set_rx_capture_result(
        {"frame": frame.to_dict(), "analysis": P.ANA.summarize(frame)})
    assert landed is False and reason is not None
    assert app.rx_analysis is None


def test_a_first_capture_can_land_before_rx_ready_telemetry_arrives():
    app = rx_app()
    app.rx_capture_submitted()
    frame = P.make_frame()
    landed, reason = app.set_rx_capture_result(
        {"frame": frame.to_dict(), "analysis": P.ANA.summarize(frame)})
    assert landed is True and reason is None
    assert app.rx_snapshot_is_current()


def test_a_new_landed_capture_clears_the_session_loss_reason():
    app = rx_app()
    land_clean(app)
    app.set_rf_status(rx_status(state="FAULT"))
    assert "session_lost" in [q["code"] for q in M.rx_quality_reasons(app)]
    app.set_rf_status(rx_status())
    land_clean(app)
    assert app.rx_analysis is not None
    assert "session_lost" not in [q["code"] for q in M.rx_quality_reasons(app)]
    assert "质量: 无异常" in app.rx_quality_text()


def test_an_applied_configuration_change_invalidates_the_landed_analysis():
    """A NEW applied configuration makes the landed analysis stop being current reception.

    The 30-second clock, the session and the daemon are all still fine here, so nothing
    else in the model can rescue the screen: the record was captured under the OLD LO /
    sample rate, and the operator has since applied a new one.  Showing it as the current
    receive result would be exactly the misleading state the session-loss finding was about
    (spec: 改配置...使当前分析失效).
    """
    app = rx_app()
    land_clean(app)
    # The session is alive AND its capture generation is still retained: the only thing
    # that changes below is the applied configuration.
    app.set_rf_status(rx_status(rx_session_ready=True))
    stamped = app.rx_analysis_summary.get("config_revision")
    assert stamped is not None, "the snapshot records the config generation it was taken under"

    apply_new_configuration(app)

    assert app.rx_analysis is None, "the old-configuration analysis is not current"
    assert app.rx_analysis_summary is None
    assert app.rx_snapshot.state != "DONE"
    assert app.rx_history and app.rx_history[0]["capture_id"] == "rx-test-0-0-1", \
        "it is history, not deleted"
    codes = [q["code"] for q in M.rx_quality_reasons(app)]
    assert "config_changed" in codes, codes
    assert "session_lost" not in codes, "an applied configuration change is not a lost session"
    assert app.rf_status["daemon_state"] == "READY", "and it is certainly not a hardware FAULT"
    _lcd, v = rx_view(app)
    text = "\n".join(H._rx_page_strings(app, v))
    assert "接收分析" not in text, text
    assert "质量: 无异常" not in text, text
    assert "配置已变更" in text, "the cause reaches the panel, not just the model"
    # It must not be dressed up as any of the other invalidation reasons.
    assert "关闭未确认" not in text, text
    assert "会话已断开" not in text, text
    assert "已停" not in text, text


def test_editing_an_unapplied_draft_does_not_invalidate_the_landed_analysis():
    """Negative criterion: only a configuration that was APPLIED invalidates.

    An operator editing the RF draft field changes nothing on the daemon, so the landed
    analysis still describes the configuration that is actually in force.  It stays
    current; the draft mismatch is carried by the NEXT capture being refused with
    CONFIG_CHANGED instead.  Without this, "invalidate on any config edit" would pass the
    criterion above while quietly throwing away a perfectly current measurement.
    """
    app = rx_app()
    land_clean(app)
    app.set_rf_status(rx_status(rx_session_ready=True))
    landed = app.rx_analysis

    assert app.set_rf_parameter("lo_hz", 85000000) is None      # draft only, never applied
    assert app.rx_capture_block_reason() == M.CONFIG_CHANGED, "the next capture IS gated"

    assert app.rx_analysis is landed, "an unapplied draft edit must not invalidate it"
    assert app.rx_analysis_summary is not None
    assert app.rx_snapshot.state == "DONE"
    assert "config_changed" not in [q["code"] for q in M.rx_quality_reasons(app)]
    _lcd, v = rx_view(app)
    text = "\n".join(H._rx_page_strings(app, v))
    assert "接收分析" in text, text
    assert "质量: 无异常" in text, text


# ------------------------------------- the panel-side terminal state must not be undone

def test_a_panel_refusal_is_not_undone_by_the_workers_last_status():
    """The main loop lands the worker status every frame -- including an OLD one.

    A refusal decided by the model (the daemon left READY) was being overwritten by the
    worker status the panel had already seen, so the page could read DONE with nothing
    landed at all.
    """
    app = rx_app()
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    worker._poll_once()
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        screen = daemon._PanelScreen(v, None, None, None, worker)
        H.tap_at(v, *H.center(v.rx_capture_btn))
        for _ in range(200):
            screen.render(daemon.Status())
            if app.rx_analysis is not None:
                break
            time.sleep(0.01)
        assert app.rx_capture["state"] == "done", app.rx_capture
        # The daemon leaves READY and the model refuses the next capture locally: the
        # worker still holds its previous DONE, and no new status has been published.
        app.set_rf_status(rx_status(state="FAULT"))
        daemon._rx_action(app, worker, "capture")
        assert app.rx_capture["state"] == "refused"
        screen.render(daemon.Status())
        assert app.rx_capture["state"] == "refused", "the worker stale DONE came back"
        assert app.rx_capture["code"] == M.NOT_READY
        submits = [c for c in client.calls if c[0] == "capture"]
        assert len(submits) == 1, "the refused tap submitted a second capture"
    finally:
        worker.stop()


def test_a_config_change_cancellation_is_not_undone_by_the_workers_last_status():
    app = rx_app()
    client = P.FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        _lcd, v = rx_view(app, on_rx=callback(app, worker))
        screen = daemon._PanelScreen(v, None, None, None, worker)
        H.tap_at(v, *H.center(v.rx_capture_btn))
        assert client.entered.wait(2)
        # The operator re-applies the configuration while the capture is in flight.
        app.set_rf_control_result("configure", {"ok": True, "args": dict(PARAMS)})
        client.release.set()
        for _ in range(200):
            screen.render(daemon.Status())
            if app.rx_capture["state"] == "cancelled":
                break
            time.sleep(0.01)
        assert app.rx_capture["state"] == "cancelled", app.rx_capture
        assert app.rx_analysis is None
        # The worker keeps reporting DONE: it must not resurrect the discarded result.
        for _ in range(3):
            screen.render(daemon.Status())
        assert app.rx_capture["state"] == "cancelled", "the worker stale DONE came back"
        assert app.rx_analysis is None and app.rx_snapshot.state != "DONE"
    finally:
        client.release.set()
        worker.stop()


# ------------------------------------------- a recommendation cannot poison the draft

def test_fill_draft_refuses_a_recommendation_that_would_break_the_next_capture():
    app = rx_app()
    app.rx_subpage = "smart"
    app.set_recommendation({"mode": "QPSK", "confidence": 0.9, "source": "test",
                            "stale": False,
                            "action": {"decim": 3, "rx_len": 99999, "symrate_hz": 10 ** 9}})
    assert app.rx_fill_draft() is not None, "nothing usable was offered"
    assert app.rx_draft["decim"] == 1
    assert app.rx_draft["rx_len"] == M.RX_DEFAULT_LEN
    assert app.rx_draft["symrate_hz"] == 3000000
    assert app.rx_capture_block_reason() is None, "the draft must stay capturable"


def test_fill_draft_still_takes_recommendations_inside_the_capture_contract(tmp_path):
    from test_rf_smart_pipeline import captured, land
    worker, payload = captured(tmp_path, "production")
    app = land(worker, payload)
    app.rx_subpage = "smart"
    app.rx_draft.update(decim=8, rx_len=2048)
    before_mode = app.rx_draft['expected_mode']
    assert app.rx_fill_draft() is None
    assert (app.rx_draft["decim"], app.rx_draft["rx_len"], app.rx_draft["symrate_hz"]) \
        == (1, 4096, 3000000)
    assert app.rx_draft["expected_mode"] == before_mode
    assert app.rx_capture_block_reason() is None
    worker.stop()


def test_the_snapshot_output_rate_reuses_the_one_decimation_helper():
    frame = P.make_frame(samples=64, ratio=0).to_dict()
    del frame["output_fs_hz"]
    frame["decimation"] = 3                     # outside the 1/2/4/8/16 contract
    snap = M.rx_snapshot_from_capture(frame, P.ANA.summarize(P.make_frame(samples=64)))
    assert M.rx_output_fs(M.RX_INPUT_FS_HZ, 3) is None
    assert snap.output_fs_hz == 0, "an out-of-contract decimation must not invent an Fs"


# ------------------------------------------------- the capture vocabulary has one owner

def test_the_capture_state_vocabulary_is_declared_in_exactly_one_module():
    import ast

    import rf_status_worker as W
    for name in ("RX_CAPTURE_IDLE", "RX_CAPTURE_QUEUED", "RX_CAPTURE_RUNNING",
                 "RX_CAPTURE_CANCELLING", "RX_CAPTURE_CANCELLED", "RX_CAPTURE_DONE",
                 "RX_CAPTURE_ERROR"):
        assert getattr(W, name) is getattr(M, name), name
    states = set(M.rx_capture_state_vocabulary())
    tree = ast.parse(inspect.getsource(W))
    redeclared = [node.targets[0].id for node in ast.walk(tree)
                  if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                  and node.value.value in states]
    assert redeclared == [], "the worker re-declares the panel capture vocabulary"
