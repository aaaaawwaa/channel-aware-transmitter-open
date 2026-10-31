# Synthetic dataset generation report — ml_training data-gen (task-1)

Owner: data-gen. Scope: `ml_training/data/**`, `ml_training/datasets/**`.
Contract: `ml_training/docs/CONTRACTS.md` §1–§7, §13 (frozen; Lead-owned). All
numbers below are actual values from the produced artefacts on disk
(2026-10-20 UTC), not estimates. Interpretation log: `ml_training/data/NOTES.md`.

Note: `generate.py` auto-writes a per-run version of this file **only for
`--variant formal`** (so smoke/pytest runs can never clobber the deliverable);
this document is the consolidated final report with the reference check and
pytest results appended.

## 1. Deliverables (actual)

| artefact | path | status |
|---|---|---|
| generator | `ml_training/data/generate.py` | done |
| independent reference checker | `ml_training/data/reference_check.py` | done |
| pytest suite | `ml_training/data/tests/` (`conftest.py`, `test_generate.py`, `test_reference_check.py`) | **36 passed**, 0 failed |
| diagnostic tool | `ml_training/data/tools/clip_probe.py` | done (documented aid, not contract) |
| contract interpretation log | `ml_training/data/NOTES.md` | done |
| smoke dataset | `ml_training/datasets/sim-smoke/` | done |
| formal dataset | `ml_training/datasets/sim-v1/` | done (frozen; not modified after generation) |
| reference-check outputs | `ml_training/data/reports/reference-check.json`, `reference-check.md`, `reference-check-smoke.json/.md` | done |

Environment: `ml_training\.venv\Scripts\python.exe` (Python 3.12.10, numpy 2.5.4,
pytest 9.1.1). No network installs, no product-code/`evidence/`/Git/board actions.

## 2. Formal dataset `ml_training/datasets/sim-v1`

### 2.1 Identity / integrity (final re-freeze)

| field | value |
|---|---|
| schema | `ml-modrec-synthetic-v1` |
| dataset_id / dirname | `sim-v1` |
| created_utc | `2026-10-20T12:58:44Z` |
| config | `ml_training/config/first_round.json` |
| config_sha256 | `c34ba443065ffc6a8ba2bd0ed6285eac68f5a5d81030b0482d934e518848cc78` |
| manifest_sha256 | `9f70a0c8c5e3b5a1395617d71f860fd60c2bd3462511efa5330dea6f310a3ecd` |
| total files on disk | 20 npz + `manifest.json` + `generation.log` |
| **total bytes** | **114,886,349 B = 109.56 MiB** (limit < 200 MB) |
| generation elapsed (manifest) | **24.036 s** (wall clock 24.64 s) |
| numpy version | 2.5.4 |
| peak normalisation | `complex` — `max(|I+jQ|) = amplitude_codes` (接收仿真域, CONTRACTS §4) |
| clipping headroom sigma | 5.5 (read from config; not hard-coded) |
| c coefficients | BPSK 1.0, QPSK 1.0, QAM16 5/9, QAM64 3/7 |
| waveform | fs=60,000,000 Hz, Rs=3,000,000, sps=20, RECT, 4096 samples, 207 symbols |

### 2.2 Frames per split / label (exact counts)

| split | per label / kind | split total | clipped |
|---|---|---|---|
| train | 4 labels × (4 sessions × 300) → **1200 per label** | **4800** | 0 |
| val | 4 labels × (2 sessions × 150) → **300 per label** | **1200** | 0 |
| test | 4 labels × (12 SNR cells × 50) → **600 per label** | **2400** | 0 |
| gate_cal | 4 kinds × 100 | **400** | 112 (CLIP 100 + NOISE 12) |
| gate_test | 4 kinds × 100 | **400** | 112 (CLIP 100 + NOISE 12) |
| **total** | | **9200** | **224** |

* 4-class clipped frames: **0 / 8400** (BPSK 0, QPSK 0, QAM16 0, QAM64 0).
  The 5.5σ headroom + complex-envelope normalisation removes the systematic
  clipping seen in the first smoke draft; remaining clipping appears only in
  gate data (NOISE noise-only frames with large sigma; CLIP is deliberate).
* test SNR grid: `[-6, -3, 0, 2, 4, 6, 8, 10, 12, 15, 18, 20]` dB, exactly
  50 frames per (label, SNR) cell, one session per cell (48 test sessions).
* Duplicate IQ: **cross-split 0, intra-split 0** (all 9200 `iq_sha256` unique;
  checked independently from the stored bytes).
* Seed families: `train-a..d`, `val-a..b`, `test-a..c`, `gate-cal-a..b`,
  `gate-test-a..b` — pairwise disjoint across splits (verified).
* `generation.log` (in the dataset dir) records per-file sha256/seconds and the
  totals; `manifest.json` carries every frozen field (`splits`, `files`,
  `summary`) plus generator metadata.

### 2.3 Config re-freeze 2026-10-20 (training/gate fields only — npz byte-identical)

After the first formal generation, the Lead re-froze
`ml_training/config/first_round.json` changing **only** `model.max_epochs`
(40 → 80) and `gate.max_softmax_grid` (added 0.30/0.35/0.40/0.45). The waveform,
impairment and split blocks were unchanged. Both datasets were regenerated with
the new config; verified against a pre-regeneration snapshot of all 20 npz
`sha256` values:

* **all 20 npz files are byte-identical** for both `sim-v1` and `sim-smoke`
  (same per-file sha256; deterministic writer + unchanged seed/impairment blocks);
* only `manifest.config_path/config_sha256`, `created_utc`, `manifest_sha256` and
  timing/log content changed (`config_sha256` `f1a677ce…` → `c34ba443…`).
* No mixed-config data was retained (both datasets now carry
  `config_sha256 = c34ba443…`).

### 2.4 Per-file counts and npz sha256 (formal, actual; unchanged by the re-freeze)

| path | frames | clipped | sha256 |
|---|---|---|---|
| `frames/train-BPSK.npz` | 1200 | 0 | `bfc086619ad5fff8b7656493e01ad517a1e1fee2ba004fea0ec2a67b676afac0` |
| `frames/train-QPSK.npz` | 1200 | 0 | `1e302bf0a7309c03afd9e6e666ea58bcd9527178e2ad4f8fc141d96aa97c05ad` |
| `frames/train-QAM16.npz` | 1200 | 0 | `ad7ffa17cf5f8d36f5f9758b3ce37ce6fdc6e2edd94d07bb18c32ef5ad0ac386` |
| `frames/train-QAM64.npz` | 1200 | 0 | `862231beb8eb178d2ee4829677764530109f3968856b390929abd8a8b5eb5196` |
| `frames/val-BPSK.npz` | 300 | 0 | `5b01c55f715fdf0d3e855dde960c4d90b2ac30188f016885b4f856aca34bc15e` |
| `frames/val-QPSK.npz` | 300 | 0 | `930600ab34c569276892af97657af48372df2e9faea553f58ea6ba36385e583d` |
| `frames/val-QAM16.npz` | 300 | 0 | `30aea447017756d95b1a3a040d73354b13684f83de2a168a427c16b0716c43aa` |
| `frames/val-QAM64.npz` | 300 | 0 | `b69de98d530873d6518332ddbabfd62dd9d635f93c193ec74f8dbd5048a451d3` |
| `frames/test-BPSK.npz` | 600 | 0 | `9e1ee63dd99fb27a0fcc3726d2e896cecbc6b6ac62158786716e32862cfcbb00` |
| `frames/test-QPSK.npz` | 600 | 0 | `de3086f37687fcf1ecee55f7ba14cbaec1a48c63882f6b2bca7c3c6195a1a21b` |
| `frames/test-QAM16.npz` | 600 | 0 | `c7991df190d94f2505f15904d0ac6285f4f59c69246845a1b58684b3e6ae6ee9` |
| `frames/test-QAM64.npz` | 600 | 0 | `53a25b7440ff9a6c0eec441fc01b044eb59621d6576ff1c18730d28fc258ac66` |
| `gate/gate_cal-OFF.npz` | 100 | 0 | `9f8d8c35ea2d23d691bc1a5a37d2cc579aa67a70049cf10f729769caaa04350f` |
| `gate/gate_cal-NOISE.npz` | 100 | 12 | `bb799f9f9eae95a1d5da48132cd284c5295979013eb4a8334293513c3371c128` |
| `gate/gate_cal-TONE.npz` | 100 | 0 | `8832d9c236f8a4bcfafcdcc50b7bbd0c324acf728c558c67403e46527ca75236` |
| `gate/gate_cal-CLIP.npz` | 100 | 100 | `1e2adc65f9d6ca53a7ede377e140c033275b27e3fd3dd457db1566ba6402ceee` |
| `gate/gate_test-OFF.npz` | 100 | 0 | `63a233c093607c28502ce2edb6337e90918a0b78a12c0f956ef7f7a26d81ed0e` |
| `gate/gate_test-NOISE.npz` | 100 | 12 | `dc9177299472a21eb2130dbe8c48879ac7c7bdc48a6fd542769f2007fe5ed184` |
| `gate/gate_test-TONE.npz` | 100 | 0 | `9d8cca53abfef8fb539298d682e1fefac45d8ee576404d95d185ec8d9468d1ca` |
| `gate/gate_test-CLIP.npz` | 100 | 100 | `452bff2542cb88fac3c65625c67f557beab782ceba52430d9e3ad34a41f18681` |

### 2.5 Smoke dataset `ml_training/datasets/sim-smoke` (regenerated with final config)

| field | value |
|---|---|
| frames | 280 (train 80 / val 40 / test 80 / gate_cal 40 / gate_test 40) |
| bytes | 3,290,965 B = 3.14 MiB |
| generation elapsed | 1.111 s (wall 1.87 s) |
| manifest_sha256 | `d932e887e18a8c68e2f58d4231b54785588350cdb675d20b83fc60a7ee88a6b7` |
| config_sha256 | `c34ba443065ffc6a8ba2bd0ed6285eac68f5a5d81030b0482d934e518848cc78` |
| clipped | 24 total: main splits **0/200**; gate CLIP 20/20 = 100 %, gate NOISE 4 |
| reference check | **22/22 PASS** (0.783 s), exact reconstruction 280/280 |
| pytest | covered by the 36-test suite (tiny fixture exercises the same code paths) |
| npz | byte-identical to the pre-re-freeze smoke generation (same 20 sha256) |

## 3. Independent reference check (formal) — `ml_training/data/reports/reference-check.json`

Command: `python ml_training/data/reference_check.py --dataset ml_training/datasets/sim-v1 --out ml_training/data/reports/reference-check.json --md ml_training/data/reports/reference-check.md`

**Result: PASS — 22/22 checks, 11.64 s.** The checker does **not** import the
generator; it re-implements the contract, rebuilds every frame from the recorded
seeds/params and cross-checks the bit mapping against the frozen product
`pynq_overlay/sw/rf_iq_signal.py:map_bits_to_symbol` (read-only import).

| check | result / actual |
|---|---|
| manifest schema/fields | PASS (`9f70a0c8…`) |
| config identity | PASS (`config_sha256 = c34ba443…`) |
| waveform frozen + headroom/peak-normalisation echo | PASS (5.5, `complex`) |
| split/session/file plan vs frozen config, families disjoint | PASS (expected total 9200) |
| npz sha256 vs manifest | PASS (all 20 files) |
| required arrays/dtypes/shapes | PASS |
| frame/session/label/family metadata, ordering | PASS |
| **exact per-frame reconstruction (quantised bytes)** | **PASS — 9200/9200** |
| per-frame `iq_sha256` | PASS (9200/9200) |
| clipping flag vs pre-saturation out-of-range | PASS |
| per-frame parameter ranges + sigma formula exact | PASS (`sigma` formula max abs diff 0.0) |
| amplitude clipping-safety cap | PASS |
| SNR residual variance (noise + quantisation noise) | PASS — 8400/8400 non-clipped 4-class frames; var ratio 0.9336–1.0596; max rel dev 6.64 % |
| test SNR grid / train-val SNR range | PASS |
| seed uniqueness + duplicate IQ | PASS — cross-split 0, intra-split 0, 9200 unique |
| gate CLIP saturation/rail codes/flag | PASS — 200/200 CLIP frames saturated + flagged |
| rotated component bound (`max(|I|,|Q|) ≤ amplitude+1`) | PASS |
| clipped statistics vs manifest | PASS (224 total: train 0 / val 0 / test 0 / gate_cal 112 / gate_test 112) |
| frame-count summary vs frozen plan | PASS (4800/1200/2400/400/400) |
| bit mapping vs contract + product | PASS — all 2+4+16+64 = 86 patterns match |
| noiseless quantisation (ties-to-even, saturation, envelope peak) | PASS (48 peak probes + 12 offset probes) |
| analytic constellation power coefficients | PASS — synthetic mean / dataset mean vs c: BPSK 1.0/1.0, QPSK 1.0/1.0, QAM16 0.55520/0.55533 (5/9=0.55556), QAM64 0.42967/0.42881 (3/7=0.42857) |

Readable summary: `ml_training/data/reports/reference-check.md`.

**`clipped` semantics note (as requested by the Lead):** `clipped=True` means the
`np.rint` result left the signed 12-bit range (overflow) — such frames always
retain rail codes. The converse does not hold in discrete boundary cases: a
genuine in-range value can round exactly onto a rail code (one formal gate NOISE
frame: `gate_test-NOISE` index 21, `max=2047`, `clipped=False`); main splits have
**0** rail-without-overflow frames. The evaluation pipeline still rejects such a
frame through the preprocess rail detection, so the rejection logic is unaffected.
The generator/reference check follow the literal contract definition (clipped =
out-of-range); the Lead-side contract test was adjusted accordingly
(`clipped=True ⇒ rail code present`; main splits additionally forbid
rail-without-flag, measured 0).

## 4. pytest (actual run, final tree)

Command:
`ml_training\.venv\Scripts\python.exe -m pytest ml_training\data\tests -q`

Result: **36 passed, 0 failed, 0 skipped** in 14.58 s (final run after the config
re-freeze). The suite covers:

* generator: manifest fields/counts, npz array names/dtypes/shapes, frame ids,
  families/split isolation, seed-family disjointness, gate construction,
  quantization ties-to-even + saturation, amplitude cap, complex-envelope peak,
  SNR residual, rail statistics, deterministic byte-identical regeneration
  (two runs), CLI exit codes.
* bit mapping: every pattern (2/4/16/64) vs the frozen **product** mapper
  (`pynq_overlay/sw/rf_iq_signal.py`) and vs the scalar contract table.
* independent-bits requirement: per-frame bits are i.i.d. draws (no PRBS
  shift/slice relation between frames; 50 distinct seeds → 50 distinct frames).
* reference checker: PASS on the generated dataset; **negative cases must fail**:
  tampered frame bytes, changed frame label, cross-split IQ copy, clipped-flag
  tamper, `iq_sha256` field tamper, manifest clipped-count tamper, config-sha
  tamper, missing npz, duplicate IQ inside a split.
* frozen formal plan: 9200 frames (4800/1200/2400/400/400), 48 test sessions
  covering the full 12-point SNR grid, gate 100/kind, disjoint families.

Lead-side `ml_training/tests/test_dataset_contract.py` (Lead-owned, not modified
by data-gen): Lead reported **passing for both `sim-smoke` and `sim-v1`** after
the rail/clipped assertion was relaxed for gate frames (see note above).

## 5. Design choices / deviations (no silent deviations)

Full log in `ml_training/data/NOTES.md`; summary:

1. **Complex-envelope peak normalisation** (`max(|I+jQ|)=amplitude_codes`) plus
   revised analytic `c` (1, 1, 5/9, 3/7), decided by the Lead on 2026-10-20 and
   reflected in CONTRACTS §4. This is a *receive-simulation domain* choice: the
   random phase/CFO rotation is applied after the amplitude normalisation and
   would otherwise cause artificial ADC clipping of two-axis constellations
   (measured: QPSK 13 % clipped at 15 dB under the component-peak reading vs 0 %
   with the complex-envelope reading). The product TX
   `generate_digital_frame` component-peak semantics is **unchanged/not touched**.
2. **Clipping headroom sigma = 5.5** (was 4.5) read from
   `dataset.impairment.clipping_headroom_sigma`; both generator and reference
   checker read it from config (no hard-coded value). Manifest echoes it.
3. **Quantisation saturation**: `np.rint` ties-to-even; out-of-12-bit values are
   saturated to `[-2048,2047]`, frames kept and flagged `clipped=True`; gate CLIP
   frames are deliberately built to saturate (100 %).
4. **Gate kind definitions** (contract leaves the construction open): OFF/NOISE =
   AWGN-only with the configured sigma ranges; TONE = single complex tone (the
   `cfo_hz` metadata field carries the tone frequency); CLIP = BPSK waveform
   peak-normalised to 2200–3500 codes with zero phase/CFO/AWGN. `snr_db = NaN` is
   the explicit not-applicable sentinel for gate frames.
5. No field names, dtypes, units, array names, split sizes or manifest keys were
   changed relative to CONTRACTS §3–§7. Smoke/formal directories were overwritten
   when the contract/config changed; **no mixed-config data is retained** (both
   current datasets carry `config_sha256 = c34ba443…`).

## 6. Diagnostics (design aid): `ml_training/data/tools/clip_probe.py`

Actual output with the frozen config (300 trials per class/SNR, amplitude drawn
`U[150,1200]` capped by the frozen formula, random phase/CFO/offset):
all cells **0.00 %** clipped except one BPSK −6 dB probe frame (0.33 %, i.e.
1/300 — consistent with the ~3e-4/frame theoretical 5.5σ tail). This matches the
formal dataset result (0 clipped 4-class frames).

## 7. Unmodeled impairments (declared, not simulated)

From CONTRACTS §5 / config `impairment.unmodeled`: IQ imbalance, DC offset,
receive filter/RRC distortion, multipath, phase noise, AGC transient, hardware
frequency response, RF chain non-linearity. No calibrated values are claimed.

## 8. NOT_RUN (data-gen scope)

* Training / evaluation / threshold selection / export — Lead / eval-tools scope
  (Lead's 80-epoch training run is separate from this task).
* ARM / board / RF / real-IQ capture — **NOT_RUN**, zero board contact from this
  task; no `evidence/` files were read, moved or modified; no Git operations.
* Real-data transfer evaluation — eval-tools scope (real BPSK frames remain
  read-only there).

## 9. Reproduction

```
ml_training\.venv\Scripts\python.exe ml_training\data\generate.py \
    --config ml_training\config\first_round.json --variant smoke \
    --out ml_training\datasets\sim-smoke
ml_training\.venv\Scripts\python.exe ml_training\data\generate.py \
    --config ml_training\config\first_round.json --variant formal \
    --out ml_training\datasets\sim-v1
ml_training\.venv\Scripts\python.exe ml_training\data\reference_check.py \
    --dataset ml_training\datasets\sim-v1 \
    --out ml_training\data\reports\reference-check.json \
    --md ml_training\data\reports\reference-check.md
ml_training\.venv\Scripts\python.exe -m pytest ml_training\data\tests -q
```

The npz files are written by a deterministic writer (fixed zip timestamps/order),
so the same config reproduces the same npz sha256 values (verified by a two-run
determinism test and by the 2026-10-20 re-freeze regeneration where all 20 npz
sha256 stayed identical); `created_utc`/`elapsed_sec` are the only
non-deterministic manifest fields.
