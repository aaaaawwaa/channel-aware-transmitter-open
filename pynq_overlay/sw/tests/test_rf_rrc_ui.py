"""RRC product UI handoff: explicit RRC/QI submission, intervals and invalidation."""
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rf_rrc_pipeline as P
import ui_model as M
from rf_rx_analysis import summarize
from test_rf_rrc_receiver import _frame

VERSION = "rrc-alpha035-sps20-v3"
HASH = "c" * 64


def app():
    a = M.UiApp()
    a.set_rf_status(dict(online=True, stale=False, mode="status_only", armed=True,
                         daemon_state="READY", release_id="release",
                         control_ops=["rx.capture"], rx_session_ready=True))
    a.set_rx_demod_status(dict(available=False, release_id="release",
                               qpsk_available=False, rrc_available=True,
                               algorithm_version=None, algorithm_sha256=None,
                               qpsk_algorithm_version=None,
                               qpsk_algorithm_sha256=None,
                               rrc_algorithm_version=VERSION,
                               rrc_algorithm_sha256=HASH))
    assert a.set_rx_demod_setting("expected_mode", "QPSK") is None
    assert a.set_rx_demod_setting("shaping", "RRC") is None
    assert a.set_rx_demod_setting("reference_enabled", True) is None
    assert a.set_rx_demod_setting("reference_seed", 39) is None
    return a


def payload(a):
    frame = dataclasses.replace(
        _frame(mode="QPSK", offset=7, phase=0.2, iq_order="QI"),
        capture_id="rrc-ui-frame", release_id="release",
        completed_at_monotonic=time.monotonic())
    submitted = a.rx_demod_submission()
    assert isinstance(submitted, P.RrcDemodConfig)
    assert submitted.iq_order == "QI"
    assert submitted.config.mode == "QPSK"
    assert submitted.config.rolloff == 0.35
    a.rx_capture_submitted()
    return dict(frame=frame.to_dict(), analysis=summarize(frame),
                demod_result=P.analyze_rrc(frame, submitted))


def test_rrc_submission_and_landing_show_actual_intervals():
    a = app()
    data = payload(a)
    assert a.set_rx_capture_result(data)[0]
    s = a.rx_demod_summary()
    assert s["current"] is True
    assert s["engine"] == "RrcEnvelope"
    assert s["algorithm_version"] == VERSION
    assert s["rrc_available"] is True
    assert s["ber"] == 0.0 and s["compared_bits"] == 112
    assert s["valid_symbols"] == 119
    assert (s["valid_symbol_start"], s["valid_symbol_end"]) == (4, 123)
    assert (s["comparison_start_symbol"], s["comparison_end_symbol"]) == (67, 123)
    assert s["group_delay_samples"] == 0 and s["transition_symbols"] == 4
    assert s["evm_available"] is False and s["evm_rms"] is None
    assert len(s["symbol_preview"]) <= 64 and len(s["bit_preview"]) <= 128


def test_rrc_config_change_clears_old_ber():
    a = app()
    data = payload(a)
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_demod_summary()["ber"] == 0.0
    assert a.set_rx_demod_setting("reference_seed", 40) is None
    assert a.rx_demod_result is None
    assert a.rx_demod_summary()["ber"] is None


def test_rrc_identity_mismatch_is_refused():
    a = app()
    data = payload(a)
    envelope = data["demod_result"]
    data["demod_result"] = dataclasses.replace(
        envelope, result=dataclasses.replace(envelope.result, capture_id="old"))
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_demod_result is None
    assert a.rx_demod_summary()["ber"] is None


def test_rrc_view_shows_intervals_and_evm_unavailable(monkeypatch):
    import test_rf_rx_view as H
    a = H.rx_app()
    a.rx_subpage = "demod"
    monkeypatch.setattr(a, "rx_demod_summary", lambda: dict(
        available=True, current=True, engine="RrcEnvelope",
        algorithm_version=VERSION, reason="", ber=0.0, bit_errors=0,
        compared_bits=112, valid_symbols=119, valid_symbol_start=4,
        valid_symbol_end=123, comparison_start_symbol=67,
        comparison_end_symbol=123, group_delay_samples=0,
        transition_symbols=4, carrier_sync=True, timing_sync=True,
        reference_sync=True, cfo_hz=0.0, capture_id="rrc-ui-frame",
        polarity_ambiguous=False, symbol_preview=((1.0, 0.0),),
        bit_preview=(0, 1), evm_available=False, evm_rms=None))
    _lcd, boxes = H.render_logged(a, "demod")
    text = "\n".join(s for s, size, x, y in boxes)
    assert "EVM不可用" in text and "有效119" in text
    assert "67-123" in text and "0/112" in text
    assert "预览" in text and "B:01" in text



def test_rrc_bpsk_submission_uses_56_bit_comparison():
    a = app()
    assert a.set_rx_demod_setting("expected_mode", "BPSK") is None
    frame = dataclasses.replace(
        _frame(mode="BPSK", offset=0, phase=0.1, iq_order="QI"),
        capture_id="rrc-bpsk-ui", release_id="release",
        completed_at_monotonic=time.monotonic())
    submitted = a.rx_demod_submission()
    assert isinstance(submitted, P.RrcDemodConfig)
    assert submitted.config.mode == "BPSK" and submitted.iq_order == "QI"
    a.rx_capture_submitted()
    data = dict(frame=frame.to_dict(), analysis=summarize(frame),
                demod_result=P.analyze_rrc(frame, submitted))
    assert a.set_rx_capture_result(data)[0]
    s = a.rx_demod_summary()
    assert s["engine"] == "RrcEnvelope" and s["ber"] == 0.0
    assert (s["bit_errors"], s["compared_bits"]) == (0, 56)
    assert s["valid_symbols"] == 119
