"""Independent synthetic QAM64 impairment fixtures for receiver regression.

This module reuses only the B-line TX-contract reference (``qam64_reference``);
it does not import the receiver under test.  Only the first 63 symbols are the
fit/alignment region; the last 64 symbols (384 bits) are the comparison region.
"""
from __future__ import annotations

import numpy as np

import qam64_reference as R

SPS = R.SPS
SYMBOLS = R.SYMBOLS
REFERENCE_SAMPLES = R.REFERENCE_SAMPLES
ALIGNMENT_SYMBOLS = 63
COMPARISON_SYMBOLS = 64
ALIGNMENT_BITS = 6 * ALIGNMENT_SYMBOLS       # 378
COMPARED_BITS = 6 * COMPARISON_SYMBOLS        # 384
LEVELS = R.LEVELS


def _axis_levels(symbols):
    return np.array([int(round(v.real)) for v in symbols]), \
        np.array([int(round(v.imag)) for v in symbols])


def make_impaired_frame(seed=39, *, amplitude=60, gain_i=1.0, gain_q=1.0,
                        cross_iq=0.0, cross_qi=0.0, dc_i=0.0, dc_q=0.0,
                        phase_deg=0.0, cfo_hz=0.0, timing_samples=0,
                        noise_rms=0.0, clip=False, no_signal=False,
                        wrong_reference=False, corrupt_from=None,
                        iq_order="QI", samples=REFERENCE_SAMPLES,
                        noise_seed=20261025, wrong_seed=20261025):
    """Build one impaired 4096-sample QAM64/RECT frame and its truth metadata.

    Impairments are applied to the TX symbol sequence before quantization:
    axis gain, I/Q cross terms, DC, phase, non-500-Hz-grid CFO, timing roll and
    complex noise.  ``corrupt_from`` flips comparison symbols only when >=63;
    the fit region then remains identical to the clean frame.
    """
    truth = R.reference_symbols(seed)
    tx = truth.copy()
    if corrupt_from is not None:
        tx[int(corrupt_from):] *= -1.0
    if wrong_reference:
        rng = np.random.default_rng(wrong_seed)
        choices = np.array(list(R.all_64_symbols().values()), dtype=complex)
        tx = rng.choice(choices, size=SYMBOLS)

    iq = np.resize(np.repeat(np.column_stack((tx.real, tx.imag)), SPS, axis=0),
                   (samples, 2)).astype(float)
    if no_signal:
        iq[:] = 0.0
    else:
        if amplitude:
            peak = float(np.max(np.abs(iq)))
            iq *= (float(amplitude) / peak)
        else:
            iq[:] = 0.0
    i = iq[:, 0] * gain_i + iq[:, 1] * cross_iq + dc_i
    q = iq[:, 1] * gain_q + iq[:, 0] * cross_qi + dc_q
    n = np.arange(samples, dtype=float)
    z = (i + 1j * q) * np.exp(1j * (np.deg2rad(phase_deg) +
                                   2.0 * np.pi * cfo_hz * n / R.SAMPLE_RATE_HZ))
    if timing_samples:
        z = np.roll(z, int(timing_samples))
    if noise_rms:
        rng = np.random.default_rng(noise_seed)
        z = z + noise_rms * (rng.normal(size=samples) +
                             1j * rng.normal(size=samples))
    wire = np.empty((samples, 2), dtype="<i2")
    wire[:, 0] = np.rint(z.real)
    wire[:, 1] = np.rint(z.imag)
    if clip:
        wire[0, 0] = 2047
    if iq_order == "QI":
        wire = wire[:, ::-1].copy()
    original_bits = R._bits_and_symbols(truth)
    sent_bits = R._bits_and_symbols(tx)
    expected_errors = sum(
        1 for a, b in zip(original_bits[6 * ALIGNMENT_SYMBOLS:],
                          sent_bits[6 * ALIGNMENT_SYMBOLS:]) if a != b)
    if wrong_reference:
        expected_errors = None
    return wire.astype("<i2").tobytes(), {
        "seed": seed,
        "iq_order": iq_order,
        "samples": samples,
        "amplitude": amplitude,
        "gain_i": gain_i,
        "gain_q": gain_q,
        "cross_iq": cross_iq,
        "cross_qi": cross_qi,
        "dc_i": dc_i,
        "dc_q": dc_q,
        "phase_deg": phase_deg,
        "cfo_hz": cfo_hz,
        "timing_samples": timing_samples,
        "noise_rms": noise_rms,
        "clip": bool(clip),
        "no_signal": bool(no_signal),
        "wrong_reference": bool(wrong_reference),
        "corrupt_from": corrupt_from,
        "alignment_symbols": ALIGNMENT_SYMBOLS,
        "comparison_symbols": COMPARISON_SYMBOLS,
        "alignment_bits": ALIGNMENT_BITS,
        "compared_bits": COMPARED_BITS,
        "comparison_start_symbol": ALIGNMENT_SYMBOLS,
        "expected_errors": expected_errors,
        "bits": original_bits,
        "tx_bits": sent_bits,
        "symbols": truth,
        "tx_symbols": tx,
    }


def scenario_matrix():
    """Return the declarative coverage matrix used by the regression test."""
    return {
        "axis_gain": [
            dict(gain_i=1.10, gain_q=0.90),
            dict(gain_i=1.30, gain_q=0.70),
        ],
        "axis_cross": [
            dict(cross_iq=0.08, cross_qi=0.05),
            dict(cross_iq=0.15, cross_qi=0.10),
        ],
        "dc": [dict(dc_i=8.0, dc_q=-6.0), dict(dc_i=20.0, dc_q=-15.0)],
        "non_grid_cfo": [777.0, 1234.0, 4321.0, -2345.0],
        "phase": [37.0, 91.0, 180.0, 271.0],
        "timing": list(range(20)),
        "noise": [1.0, 2.0],
        "combined": [dict(gain_i=1.10, gain_q=0.90, cross_iq=0.08,
                          cross_qi=0.05, dc_i=8.0, dc_q=-6.0,
                          cfo_hz=1234.0, phase_deg=37.0, timing_samples=7,
                          noise_rms=1.0)],
        "pathological": [
            dict(no_signal=True),
            dict(dc_i=50.0, dc_q=50.0),
            dict(wrong_reference=True),
            dict(clip=True),
        ],
        "comparison_damage": [63, 100],
    }
