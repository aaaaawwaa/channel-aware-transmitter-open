# data-gen NOTES — contract interpretation log (ml_training/data)

Scope: `ml_training/data/**` + `ml_training/datasets/**` (data-gen). This file
records how ambiguous or under-specified points of `ml_training/docs/CONTRACTS.md`
were resolved, what was escalated to the Lead, and the exact implementation
choices that the independent reference checker relies on. Nothing here changes
the contract; the contract is owned by the Lead.

## 1. QPSK mean-power coefficient `c` (escalated 2026-10-20, resolved)

The first contract draft gave `c = E|x|²/A²` for a **component-peak** normalisation
`max(|I|,|Q|) = A` as `BPSK 1, QPSK 1, QAM16 10/9, QAM64 6/7`. Under that
normalisation QPSK is `(±A, ±A)` so `E|x|² = 2A²` → `c = 2`, i.e. the draft value
`1.0` was inconsistent and the §5 clipping guarantee was violated (smoke data
showed QPSK systematically clipped).

Resolution (Lead decision, CONTRACTS §4 updated): the receive simulation uses
**complex-envelope peak normalisation**, `max|I + jQ| = amplitude_codes`, with the
analytic coefficients

| mode | c |
|---|---|
| BPSK | 1.0 |
| QPSK | 1.0 |
| QAM16 | 5/9 |
| QAM64 | 3/7 |

This is a deliberate **receive-simulation-domain** choice: the random initial
phase / CFO rotation is applied *after* the amplitude normalisation, and under
component-peak normalisation that rotation could push a two-axis constellation to
`sqrt(2)·A` and cause artificial ADC clipping. The frozen product TX
`pynq_overlay/sw/rf_iq_signal.py:generate_digital_frame` keeps its own
component-peak semantics (`peak = max|iq|` of the complex waveform) and is **not
modified**. The generator echoes `peak_normalisation: "complex"` in
`manifest.json.generator` so the data is self-describing.

Measured consequence (see `tools/clip_probe.py` and the formal reference check):
`clipped` frames almost vanish for the 4-class data; gate CLIP remains 100 %
clipped by construction.

## 2. Clipping headroom sigma (config-driven, not hard-coded)

`dataset.impairment.clipping_headroom_sigma` is frozen in
`ml_training/config/first_round.json` (initially 4.5, changed to **5.5** by the
Lead on 2026-10-20 because the 4.5σ Gaussian tail over 8192 components gave a
~5 % frame clipping rate). Both `generate.py` and `reference_check.py` **read it
from the config**; neither hard-codes the number. The manifest echoes it under
`generator.clipping_headroom_sigma`, and the reference checker verifies
manifest ↔ config consistency.

## 3. Quantisation / saturation semantics

`np.rint` (ties-to-even) is applied to the float IQ; if any rounded value falls
outside signed 12-bit range `[-2048, 2047]` the frame is flagged
`clipped=True`, its values are **saturated** to the 12-bit range (rail codes
`±2047/-2048`) and the frame is kept (CONTRACTS §3). No frame is silently dropped
or truncated: the flag plus the rail code make the clipping observable, and the
product preprocessing gate rejects frames whose components touch the rails.

`clipped` is an **overflow** flag: `clipped=True` always implies the frame
retains rail codes, but a rail code does **not** necessarily imply overflow — a
genuine in-range value can round exactly onto `±2047/-2048` at the boundary. The
formal dataset contains exactly one such boundary frame (`gate_test-NOISE`
index 21: `max=2047`, `clipped=False`); all main-split frames have
`clipped == (rail present)` (measured 0 rail-without-overflow frames). The
evaluation pipeline still rejects that frame through the preprocess rail
detection, so rejection logic is unaffected. The reference checker derives the
flag independently from the pre-saturation rounded values (literal contract
definition) and verifies it per frame.

## 4. Gate kinds (OFF / NOISE / TONE / CLIP)

The contract freezes the four gate kinds and the config fixes their parameter
ranges but not the exact construction. Implementation (documented in
`generate.py:build_gate_frame`):

| kind | construction | recorded metadata |
|---|---|---|
| `OFF` | AWGN only, per-component sigma ~ U[0.5, 30] codes | `snr_db = NaN`, `amplitude_codes = 0`, phase/CFO/offset 0 |
| `NOISE` | AWGN only, per-component sigma ~ U[50, 600] codes | as above |
| `TONE` | single complex tone `A·exp(j(2πfn/fs + φ))`, `A ~ U[200,1200]`, `f ~ U[-5e6,5e6]`, `φ ~ U[0,2π)`, no AWGN | `cfo_hz` metadata field carries the tone frequency; `noise_sigma_codes = 0` |
| `CLIP` | BPSK symbols (independent bits from `symbol_seed`) peak-normalised to `A ~ U[2200,3500]`, zero phase/CFO/AWGN | guaranteed saturation → `clipped=True`, rail codes present |

`snr_db = NaN` is the explicit "not applicable" sentinel for gate frames; it is
never used for 4-class maths. `symbol_seed` is recorded for every frame (for
OFF/NOISE/TONE it is drawn but only the relevant stream is consumed), and
`noise_seed` is recorded for every frame.

## 5. Deterministic seeding and reproducible parameters

* Per-frame seeds are derived with SHA-256 over a canonical material string
  (`schema | variant | dataset_id | split | session_id | frame_index | stream`),
  truncated to uint64 — never Python's salted `hash()`. `symbol_seed` and
  `noise_seed` collision-free in practice and verified unique by the reference
  checker.
* Bits per frame: `np.random.default_rng(symbol_seed).integers(0, 2, size=207*bps)`
  — **independent i.i.d. bits per frame**, no PRBS period shifting/slicing.
* Per-frame damage parameters are drawn from a separate `default_rng` stream in a
  frozen order: (train/val) `snr_db`, `amplitude_draw`, `phase_rad`, `cfo_hz`,
  `window_offset`; (test) same without the SNR draw (fixed grid value). Gate kinds
  draw their own parameter sets. Draw order is fixed in `derive_frame`.
* All synthesis uses the **stored float32-rounded** `snr_db`, `phase_rad`,
  `cfo_hz` and `noise_sigma_codes` values, so the independent checker can rebuild
  every frame byte-for-byte from the npz metadata alone.
* `.npz` files are written by a deterministic writer (fixed member order, fixed
  zip timestamps, fixed deflate level). The same config therefore reproduces the
  same npz sha256 values on the same interpreter; `created_utc`/`elapsed_sec` are
  the only non-deterministic manifest fields.

## 6. Amplitude cap

`amplitude_codes = min(drawn, floor(2046 / (1 + headroom·sqrt(c/(2·snr_lin)))))`,
with `drawn ~ U[150,1200]`, `c` from §1, headroom from §2. A cap below 64 raises a
configuration error. The reference checker recomputes the cap from the recorded
`snr_db` + mode and verifies every 4-class frame respects it.

## 7. Split / session layout

* `train`: 4 labels × 4 sessions (`train-a..d`) × 300 = 4800 frames.
* `val`: 4 labels × 2 sessions (`val-a,b`) × 150 = 1200 frames.
* `test`: 4 labels × 12 SNR grid points × 50 = 2400 frames; `session_id` is
  `test-<label>-snr<±NN>-<family>` with families `test-a..c` assigned round-robin
  over the 48 sessions.
* `gate_cal` / `gate_test`: 4 kinds × 100 frames = 400 each, split over the two
  declared families per kind (2 sessions × 50 frames; smoke/1-family configs get
  all frames in one session — the generator always distributes exactly
  `frames_per_kind` frames over the declared families).
* `frame_id = <session_id>-<0000..>`; families are pairwise disjoint across
  splits; no duplicate IQ is allowed across or within splits (checked).

## 8. Other implementation details

* `P_sig` is computed as `mean(Re(clean)² + Im(clean)²)` on the rotated 4096-sample
  clean window (`clean` = windowed, phase-rotated, CFO-rotated waveform).
* The AWGN realisation is `default_rng(noise_seed).standard_normal((4096, 2)) * sigma`
  with `sigma` = stored (float32) value.
* `tools/clip_probe.py` is a documented diagnostic (not part of the contract) that
  samples the clipping rate per class/SNR under the frozen config; it exists to
  sanity-check headroom/normalisation changes.

## 9. Config re-freezes (traceability)

* 2026-10-20: `impairment.clipping_headroom_sigma` 4.5 → **5.5** (Lead decision;
  see §2). Both datasets regenerated.
* 2026-10-20 (later): only `model.max_epochs` 40 → 80 and
  `gate.max_softmax_grid` extended (training/threshold fields; waveform,
  impairment and splits untouched). Both datasets regenerated with
  `config_sha256 = c34ba443065ffc6a8ba2bd0ed6285eac68f5a5d81030b0482d934e518848cc78`;
  **all 20 npz files are byte-identical to the previous generation** (verified
  per-file sha256); only manifest `config_sha256`/`created_utc`/timing and the
  `generation.log` changed. No mixed-config data is retained.

## 10. Deviation summary (nothing silent)

No field names, dtypes, units, array names or split sizes were changed. The two
contract-level decisions above (complex-envelope peak normalisation + revised c
table, and the config headroom change 4.5→5.5) were made by the Lead and are
reflected in `ml_training/docs/CONTRACTS.md`; the dataset manifest and the
reference-check report record the exact values actually used.
