# QAM64/RECT offline reference and verifier — 2026-10-25

Status: **B-line offline complete; no board/RF/install/commit; no production file changed by B.**
A-line fixed the partial-comparison production red after the B verifier exposed it;
the verifier is now green with no xfail (see §7).

## 1. Frozen TX contract implemented independently
`pynq_overlay/sw/tests/qam64_reference.py` does **not** import the receiver,
pipeline, worker, UI, or their decision/mapping functions. It implements:

- MSB-first serial PRBS7 (`x^7 + x^6 + 1`);
- I bits b0 b1 b2 and Q bits b3 b4 b5, each axis through
  `(7, 5, 1, 3, -7, -5, -1, -3)`;
- RECT, 60 MSPS, 3 MSym/s, sps20, 127 symbols, 2540-sample cyclic payload,
  4096-sample frame;
- component-peak amplitude 60, signed-12 ties-to-even quantization;
- explicit IQ or QI wire order;
- first 63 symbols = 378 alignment bits; last 64 symbols = 384 comparison bits;
- fixtures for phase, CFO, timing, amplitude, noise, no signal, clipping,
  arbitrary comparison-area corruption, and a **true mismatching waveform**
  (random 64-QAM payload with the original seed still supplied), never a
  seed-change-only negative.

`rf_iq_signal.map_bits_to_symbol` is imported **only in tests** to cross-check
the frozen TX table; it is not the sole expected sequence.

## 2. API
Reference:
`prbs7_bits`, `axis_level`, `axis_bits`, `symbol_for_bits`,
`bits_for_symbol`, `reference_symbols`, `all_64_symbols`, `make_frame_bytes`.

Production under test (A-line, not modified by B):
`rf_qam64_receiver.Qam64Config/receive_qam64/QAM64_VERSION`;
`rf_qam64_pipeline.freeze_qam64_demod_config/analyze_qam64/Qam64Envelope`.

## 3. Fixture SHA (seed 39, default phase unless stated)
| fixture | wire SHA-256 | payload SHA-256 | reference SHA-256 | expected comparison errors |
|---|---|---|---|---|
| clean QI, phase 23° | `a70ba21ee6bbc597cabaae014e1ac5b89fc1d098481edf8e1966d38092a6666b` | `8a118d01f0f277ad49cf2abb9bd72b0e7408dda5fd07594a391851e0c58ecb61` | `63faffbba5f7fe9966ad59c1d93b48ef2a71791e0079180e371e527db2b21dd6` | 0 |
| clean IQ, phase 23° | `c15b2b9077306d108eefbce498f6c0756e6905be97f5ae3ab9638c43f5f1011e` | `8a118d…` | `63faff…` | 0 |
| corrupt_from=63 | `d112a4400c996e27876182955664e4d1e1c8b9a6cbfa692b7d3d71567b351ef2` | `a97c92…` | `d112a4…` | 128 |
| corrupt_from=70 | `7de63e72682e87e60b38f8e7787b6208af652441197f0f702ce5470d72ad42e9` | `d38b0d…` | `7de63e…` | 114 |
| corrupt_from=100 | `a2a3790e075ab17cebe76e97bba90b98c08848e7544aa89f3053473b09225ea0` | `d47014…` | `a2a379…` | 54 |
| noise_rms=1.0 | `1f73aa89a1083bdd7a10fdb846d41da90ff0c46ba37e90e44610b0d2dbf81e47` | `8a118d…` | `63faff…` | 0 |
| true mismatch | `021ed33caf991d96d77ba2b106e6369aa39d474bcc61bfa4a82b587cf19da8a8` | `5320ba…` | `021ed3…` | n/a |

All generated in memory by `make_frame_bytes`; no production/receiver decision
was used to compute them.

## 4. Verifier coverage
`pynq_overlay/sw/tests/test_rf_qam64_receiver.py` (B section) checks:
- all 64 points and inverse bit mapping; cross-check to frozen TX table;
- clean IQ and QI: `reason=""`, three sync true, valid 127, align 378,
  compare start 63, **0/384**, BER 0, EVM unavailable;
- seeds 3/39/127 × phase 0/37/91/180/271° × timing 0/1/7/19;
- CFO 0/±20k/±50k;
- noise RMS 1.0/2.0; amplitude 20/60/120;
- **full comparison corruption (start 63/70):** sync true, exact independent
  `expected_errors` preserved, denominator 384, BER = errors/384;
- comparison-area full corruption does not change valid/alignment/start;
- true mismatching waveform: `REFERENCE_NOT_FOUND`, no BER;
- no reference: `NO_REFERENCE`, no BER;
- no signal: `LOW_ENERGY`; clipping: `CLIPPED`;
- domain errors (6 MSPS/ratio1/2048 samples) and unsupported configs: named, no BER;
- pipeline clean QI/envelope identity and pipeline domain failure;
- distinct clean/corrupt fixture SHAs.
A-line implementation-side tests remain in the same file above the B section.

## 5. Red → green
- **GREEN:** `pynq_overlay/sw/tests/test_rf_qam64_receiver.py` +
  `test_rf_qam64_pipeline.py`: **124 passed, 0 failed, 0 xfailed** (run time ~24 s).
- **RED:** a true mismatch is tested by a random 64-QAM waveform, not by
  changing seed: PRBS7 reference search is cyclic and seed 40/127 can align;
  implementation-side tests that expected a seed change to fail were corrected.
- **RED → GREEN (A fixed):** partial comparison-area corruption at symbol 100
  previously gave independent 54 errors but production 62 (CFO −500 Hz) because
  the receiver removed the full-frame mean. A-line changed the receiver to avoid
  the full-frame mean and to use the first-63-symbol alignment window for the
  energy floor. The B test now passes without `xfail`.

## 6. Commands
```sh
PYTHONPATH=pynq_overlay/sw;pynq_overlay/sw/tests \
  python -B -m pytest pynq_overlay/sw/tests/test_rf_qam64_receiver.py \
  pynq_overlay/sw/tests/test_rf_qam64_pipeline.py -q
```

## 7. Resolved production red (fixed by A, verified by B)
The earlier receiver did `z = z - np.mean(z)` over the entire 4096-sample frame;
partial comparison-area sign corruption changed that mean and moved the CFO
search. A-line (SHA `a069e15dbf35791f95ad7fe800815479669f33abc39ab99702be73be68ce788f`)
removed that full-frame mean and uses the first 63 symbols for the energy floor;
sync/CFO/reference/gain still use only the alignment region. B removed the
`xfail` and reran: **124 passed**, with `corrupt_from=100` now returning the
independent 54 errors.

## 8. Not executed / not changed by B
No board, RF, install, candidate, bundle, worker, UI, production receiver,
pipeline, or B-owned algorithm change; no commit. A-line QAM64 production files
are used as-is for verification (A separately fixed the partial-comparison red);
their hashes are recorded in the delivery report.
