"""Minimal offline seam tests for QAM16/RECT product-style config handoff."""
from __future__ import annotations

import qam16_reference as R
from rf_qam16_pipeline import analyze_qam16, freeze_qam16_demod_config
from rf_rx_types import RxIqFrame, RxReadback

FS = 60_000_000


def _frame(seed=39, iq_order="IQ", samples=4096):
    wire, _ = R.make_frame_bytes(seed, iq_order=iq_order)
    if samples != 4096:
        wire = wire[:samples * 4]
    return RxIqFrame(
        capture_id="qam16-pipeline", payload=wire, samples=samples,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
        connection_generation=2, config_revision=3, request_id=4,
        release_id="release-qam16", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=2.0)


def _draft(**overrides):
    draft = {"expected_mode": "QAM16", "shaping": "RECT",
             "symrate_hz": 3_000_000, "reference_enabled": True,
             "reference_seed": 39, "decim": 1, "rx_len": 4096,
             "input_fs_hz": FS}
    draft.update(overrides)
    return draft


def test_qam16_pipeline_freezes_and_runs_explicit_rect_config():
    submitted = freeze_qam16_demod_config(_draft(), 3, iq_order="IQ")
    env = analyze_qam16(_frame(), submitted)
    r = env.result
    assert r.mode == "QAM16" and r.shaping == "RECT"
    assert r.carrier_sync and r.timing_sync and r.reference_sync
    assert r.valid_symbols == 127
    assert r.comparison_start_symbol == 63
    assert r.bit_errors == 0 and r.compared_bits == 256
    assert r.evm_available is False and r.evm_rms is None
    assert env.submitted.iq_order == "IQ"


def test_qam16_pipeline_qi_is_explicit_and_not_guessed():
    submitted = freeze_qam16_demod_config(_draft(), 3, iq_order="QI")
    r = analyze_qam16(_frame(iq_order="QI"), submitted).result
    assert r.reference_sync is True and r.bit_errors == 0


def test_qam16_pipeline_rejects_wrong_shaping_or_mode():
    for bad in ({"shaping": "RRC"}, {"expected_mode": "QPSK"}):
        try:
            freeze_qam16_demod_config(_draft(**bad), 1)
        except ValueError:
            continue
        raise AssertionError("invalid QAM16 config accepted: %r" % (bad,))


def test_qam16_pipeline_domain_mismatch_has_no_ber():
    submitted = freeze_qam16_demod_config(_draft(), 1)
    r = analyze_qam16(_frame(samples=2048), submitted).result
    assert r.reason == "INPUT_DOMAIN"
    assert r.bit_errors is None and r.compared_bits is None and r.ber is None
