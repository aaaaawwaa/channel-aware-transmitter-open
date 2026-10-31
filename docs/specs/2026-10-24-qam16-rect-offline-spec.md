# QAM16/RECT offline snapshot receiver — specification

Status: **offline only**. No worker/UI/ARM/real-board integration; no RF, install
or existing-algorithm change. The receiver is a finite snapshot analysis module;
it neither selects TX mode nor infers mode from the CNN classifier.

## 1 Frozen TX mapping and domain
Strictly equal to `rf_iq_signal.map_bits_to_symbol("QAM16", bits)`:

| I bits b0b1 | I level | Q bits b2b3 | Q level |
|---|---|---|---|
| 00 | +3 | 00 | +3 |
| 01 | +1 | 01 | +1 |
| 10 | −3 | 10 | −3 |
| 11 | −1 | 11 | −1 |

MSB-first PRBS7 (`bit6` output, `bit6^bit5` feedback), 127 symbols. The first
serial bit is b0, then b1, b2, b3. RECT, 20 samples/symbol, 2540-sample cyclic
payload, 4096-sample reference by repeating the payload. Component peak
normalized to amplitude 60 and signed-12 ties-to-even quantized by the TX side.

Supported snapshot domain only:
- QAM16, RECT;
- 60 MSPS, 3 MSym/s, SPS20;
- 4096 signed-12 I/Q samples (16384 bytes), ratio 0;
- explicit wire order `IQ` or `QI` (default `IQ`; real captures use `QI`);
- reference PRBS7 seed 1..127, or `None` for a named no-BER refusal.

Empty/weak references, clipping, invalid domain, unsupported mode/shaping/rate/
wire order and failed reference search return named failures with no BER and no
fabricated denominator. The receiver does not use TX mode or CNN class to decide
synchronization.

## 2 Synchronization / normalization / alignment
- Input is de-meaned; a fixed ±100 kHz CFO grid (10 kHz coarse, 500 Hz fine
  around the winner) and all 20 timing phases are searched using **alignment
  symbols only**.
- Cyclic reference shift is searched over all 127 offsets. Score is normalized
  complex correlation; **alignment correlation must be ≥ 0.80**.
- The winning alignment estimates a complex gain (amplitude + phase):
  `gain = vdot(reference_alignment, soft_alignment) / vdot(reference,reference)`;
  symbols are divided by it. `phase_ambiguity = round(angle(gain)/(π/2)) mod 4`
  records the four-quadrant phase context; the reference resolves the quadrant.
- BER never participates in timing/CFO/reference/gain selection.

## 3 Decision and intervals
- Each normalized axis is quantized to the nearest level ±1/±3; decision
  boundaries are at 0 and ±2 level units.
- **valid_symbols = 127**; `alignment_bits = 63×4 = 252`;
  `comparison_start_symbol = 63`; comparison covers symbols **63..126**:
  `compared_bits = 64×4 = 256`.
- `bit_errors` counts all four decision-bit mismatches in that comparison only.
  Alignment symbols are never reused or re-aligned. BER is
  `bit_errors / compared_bits`; no denominator is filled on failure.
- The first 64 symbols and first 128 decision bits are bounded previews.

## 4 Output contract
Returns the shared `ModemResult` with:
- `mode='QAM16'`, `shaping='RECT'`, receiver identity/version
  `qam16-rect-snapshot-v1`;
- carrier/timing/reference sync, `reason`, CFO, timing offset, phase ambiguity;
- valid/alignment/comparison fields, bit preview, bit errors and denominator;
- `evm_available=False`, `evm_rms=None` (EVM is not implemented).

Named failure reasons: `UNSUPPORTED`, `INPUT_DOMAIN`, `CLIPPED`, `LOW_ENERGY`,
`NO_REFERENCE`, `REFERENCE_NOT_FOUND`, `TIMING_NOT_SYNCED`. All failure paths
return no `bit_errors`/`compared_bits`/BER.

## 5 Independent reference and tests
`pynq_overlay/sw/tests/qam16_reference.py` independently implements the mapping,
PRBS, RECT payload/reference, quantization, phase/CFO/timing/noise/clipping
fixtures and all 16 constellation points. It does not import the receiver under
test or use receiver internals as its only source.

`test_rf_qam16_receiver.py` covers:
- all 16 map points vs `rf_iq_signal.map_bits_to_symbol`; bit inverse;
- clean data, seeds, amplitudes [10,30,60,200], phases [0,37,91,180,271]°,
  timing offsets 0..19, CFO ±50 kHz, QI/IQ;
- nonzero BER retained with exact denominator and no bit deletion;
- wrong reference (random QAM data), no reference, no signal, clipping,
  out-of-domain, unsupported config, QI-not-guessed;
- preview bounds/EVM-unavailable/identity.

**Red→green:** initial collection failed `ModuleNotFoundError: rf_qam16_receiver`
before implementation; final host run: **88 passed** (the red was the expected
missing-module state, not a product change).

## 6 Host performance smoke (not a threshold)
Measurements are recorded in the evidence file; no ARM/board/RF claim and no
performance acceptance threshold is substituted for a network deadline.

## 7 Explicitly not executed
No RF, real capture, board/ARM deployment, worker/UI integration, CNN/class
decision, EVM, QAM64, RRC QAM16, candidate/release modification or commit.
