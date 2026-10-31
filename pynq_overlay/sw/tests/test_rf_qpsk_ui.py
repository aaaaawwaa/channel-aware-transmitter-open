"""QPSK product UI handoff: explicit QI config, no stale BER, EVM unavailable."""
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_model as M
import rf_qpsk_pipeline as P
from rf_rx_analysis import summarize
from test_rf_qpsk_receiver import _frame

QPSK_VERSION = "qpsk-rect-snapshot-v1"
QPSK_HASH = "b" * 64


def app():
    a = M.UiApp()
    a.set_rf_status(dict(online=True, stale=False, mode="status_only", armed=True,
                         daemon_state="READY", release_id="release",
                         control_ops=["rx.capture"], rx_session_ready=True))
    a.set_rx_demod_status(dict(available=False, algorithm_version=None,
                               algorithm_sha256=None, release_id="release",
                               reason="BPSK未接入", qpsk_available=True,
                               qpsk_algorithm_version=QPSK_VERSION,
                               qpsk_algorithm_sha256=QPSK_HASH))
    assert a.set_rx_demod_setting("expected_mode", "QPSK") is None
    assert a.set_rx_demod_setting("reference_enabled", True) is None
    assert a.set_rx_demod_setting("reference_seed", 39) is None
    return a


def payload(a):
    frame = dataclasses.replace(
        _frame(seed=39, offset=4, phase=0.2, iq_order="QI"),
        capture_id="qpsk-ui-frame", release_id="release",
        completed_at_monotonic=time.monotonic())
    submitted = a.rx_demod_submission()
    assert isinstance(submitted, P.QpskDemodConfig)
    assert submitted.iq_order == "QI"
    a.rx_capture_submitted()
    return dict(frame=frame.to_dict(), analysis=summarize(frame),
                demod_result=P.analyze_qpsk(frame, submitted))


def test_qpsk_submission_uses_explicit_qi_and_lands_result():
    a = app()
    data = payload(a)
    assert a.set_rx_capture_result(data)[0]
    summary = a.rx_demod_summary()
    assert summary["current"] is True
    assert summary["ber"] == 0.0
    assert (summary["bit_errors"], summary["compared_bits"]) == (0, 128)
    assert summary["carrier_sync"] and summary["timing_sync"]
    assert summary["reference_sync"] is True
    assert summary["valid_symbols"] == 127
    assert summary["alignment_bits"] == 126
    assert summary["comparison_start_symbol"] == 63
    assert len(summary["symbol_preview"]) <= 64
    assert len(summary["bit_preview"]) <= 128
    assert summary["evm_available"] is False and summary["evm_rms"] is None
    assert summary["qpsk_available"] is True


def test_qpsk_config_edit_clears_old_ber():
    a = app()
    data = payload(a)
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_demod_summary()["ber"] == 0.0
    assert a.set_rx_demod_setting("reference_seed", 38) is None
    assert a.rx_demod_result is None
    assert a.rx_demod_summary()["ber"] is None


def test_qpsq_identity_mismatch_is_refused():
    a = app()
    data = payload(a)
    envelope = data["demod_result"]
    data["demod_result"] = dataclasses.replace(
        envelope, result=dataclasses.replace(envelope.result, release_id="other"))
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_demod_result is None
    assert a.rx_demod_summary()["ber"] is None


def test_qpsk_view_shows_previews_and_evm_unavailable(monkeypatch):
    import test_rf_rx_view as H
    a = H.rx_app()
    a.rx_subpage = "demod"
    monkeypatch.setattr(a, "rx_demod_summary", lambda: dict(
        available=True, current=True, reason="", ber=0.0, bit_errors=0,
        compared_bits=128, carrier_sync=True, timing_sync=True,
        reference_sync=True, cfo_hz=0.0, capture_id="qpsk-ui-frame",
        polarity_ambiguous=False, phase_ambiguity=0,
        symbol_preview=((1.0, 1.0),), bit_preview=(0, 1),
        evm_available=False, evm_rms=None))
    _lcd, boxes = H.render_logged(a, "demod")
    text = "\n".join(s for s, size, x, y in boxes)
    assert "EVM不可用" in text and "0.000000" in text
    assert "符号预览" in text and "比特预览" in text
    assert "0/128" in text
