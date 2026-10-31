"""Alignment-window affine v2 tests (implementation side, no B files changed)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rf_qam64_receiver as R
import rf_qam64_pipeline as P
import test_rf_qam64_receiver as H


def test_version_is_v2_and_pipeline_follows():
    assert R.VERSION == "qam64-rect-snapshot-v2"
    assert R.QAM64_VERSION == R.VERSION
    assert P.VERSION == R.VERSION


def test_clean_and_impairment_free_remain_zero():
    r = R.receive_qam64(H.frame(39), H.config())
    assert (r.reason, r.bit_errors, r.compared_bits, r.valid_symbols,
            r.alignment_bits, r.comparison_start_symbol, r.evm_available) == \
        ("", 0, 384, 127, 378, 63, False)


def test_affine_2x2_dc_recovers_axis_gain_cross_and_dc():
    # Independent synthetic channel: I/Q axis gain, cross terms and DC.
    base = H.symbols(39)
    base = np.resize(np.repeat(base, 20), 4096)
    z = base * 180.0
    i, q = z.real.copy(), z.imag.copy()
    i2 = 1.18 * i + 0.10 * q + 8.0
    q2 = 0.08 * i + 0.82 * q - 6.0
    n = np.arange(4096)
    z2 = (i2 + 1j * q2) * np.exp(1j * (0.37 + 2 * np.pi * 1234.0 *
                                     n / 60_000_000))
    wire = np.empty((4096, 2), dtype="<i2")
    wire[:, 0] = np.rint(z2.real)
    wire[:, 1] = np.rint(z2.imag)
    f = H.frame()
    f = type(f)(**{**f.__dict__, "payload": wire[:, ::-1].copy().tobytes()})
    r = R.receive_qam64(f, H.config(iq_order="QI"))
    assert r.reason == "" and r.bit_errors == 0 and r.compared_bits == 384


def test_comparison_damage_does_not_change_alignment_model():
    clean = H.frame(39)
    damaged = H.frame(39, corrupt_comparison=True)
    a = R.receive_qam64(clean, H.config())
    b = R.receive_qam64(damaged, H.config())
    # Timing, CFO and phase are alignment-derived; comparison damage may only
    # change the measured BER, never the alignment branch.
    assert a.reason == "" and b.reason == ""
    assert (a.timing_offset_samples, a.cfo_hz, a.phase_ambiguity) == \
        (b.timing_offset_samples, b.cfo_hz, b.phase_ambiguity)
    assert a.carrier_sync and b.carrier_sync and a.reference_sync and b.reference_sync
    assert a.bit_errors == 0
    assert b.bit_errors is not None and b.bit_errors > 0 and b.compared_bits == 384


def test_ill_conditioned_model_is_named_without_ber():
    good = R._fit_alignment_affine(
        R._reference_symbols(39)[:63], R._reference_symbols(39)[:63])
    bad = dict(good)
    bad["condition"] = 3.0
    bad["scalar_rmse"] = 1.0
    bad["affine_rmse"] = 0.0
    with mock.patch.object(R, "_fit_alignment_affine", return_value=bad):
        r = R.receive_qam64(H.frame(39), H.config())
    assert r.reason == "ILL_CONDITIONED"
    assert r.bit_errors is None and r.compared_bits is None and r.ber is None


def test_original_a_b_negative_is_still_reproducible_from_v1_data():
    # Preserve the legacy replay values as an explicit guard: the v2 change does
    # not rewrite the observed 2/5 negative, it only changes the new receiver.
    replay = Path(r"<LOCAL_PATH>"
                  r"qam64-real-rx-2026-10-25/replay-analysis/replay-analysis.json")
    if not replay.exists():
        pytest.skip("legacy replay evidence unavailable")
    import json
    data = json.loads(replay.read_text(encoding="utf-8"))
    rows = data.get("frames") or data.get("rows") or {}
    if isinstance(rows, list):
        rows = {r.get("tag"): r for r in rows}
    def errors(tag):
        value = rows[tag]["compare"]["bit_errors"]
        return value["live"] if isinstance(value, dict) else value
    assert errors("qam64-rect-39-a") == 2
    assert errors("qam64-rect-39-b") == 5
