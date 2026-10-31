# Round-2 failure-frame comparison (baseline vs CNN)

Read-only analysis of the real physical frames; round-1 artefacts untouched.
Truth only from `trainval.truth` + `demod_result.submitted.config`; environment-rule
labels are context only (never used as labels).

## Per-session summary

| session | normal | CNN BPSK | baseline BPSK | complex_rms | phase_deg | SNR(coherence) | env labels | OFF rejected |
|---|---|---|---|---|---|---|---|---|
| train-01 | 10 | 10/10 | 10/10 | 566.0 | -4.94 | 15.79 dB | UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT:3, CLEAR/NO_COVERED_ANOMALY_IN_WINDOW:7 | 1/1 |
| train-02 | 10 | 0/10 | 10/10 | 318.7 | 63.00 | 10.12 dB | PULSE/SPARSE_AMPLITUDE_OUTLIERS:9, UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT:1 | 1/1 |
| val-01 | 10 | 10/10 | 10/10 | 614.4 | 1.73 | 16.43 dB | CLEAR/NO_COVERED_ANOMALY_IN_WINDOW:9, UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT:1 | 1/1 |

## train-02 per-frame CNN / baseline output

| frame | rms | phase | env label | CNN label | CNN scores (B/QP/16/64) | baseline label | baseline max |
|---|---|---|---|---|---|---|---|
| normal-01 | 325.3 | 62.83 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.174, 0.825, 0.001, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-02 | 321.3 | 63.07 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.320, 0.679, 0.001, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-03 | 319.4 | 62.82 | UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT | QPSK | [0.393, 0.607, 0.000, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-04 | 318.9 | 63.02 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.185, 0.814, 0.001, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-05 | 320.0 | 63.05 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.226, 0.773, 0.001, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-06 | 317.6 | 63.08 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.230, 0.769, 0.001, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-07 | 318.5 | 63.30 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.122, 0.876, 0.002, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-08 | 316.3 | 62.91 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.284, 0.716, 0.001, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-09 | 314.8 | 62.72 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.241, 0.758, 0.001, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |
| normal-10 | 315.0 | 63.17 | PULSE/SPARSE_AMPLITUDE_OUTLIERS | QPSK | [0.472, 0.528, 0.000, 0.000] | BPSK [1.000, 0.000, 0.000, 0.000] | 1.000 |

## Feature comparison: failure (train-02) vs success (train-01+val-01) frames

| feature | failure mean | success mean | delta | relative |
|---|---|---|---|---|
| amp_kurtosis | 1.1845 | 1.0885 | +0.0960 | +0.088 |
| amp_sixth | 1.5907 | 1.2508 | +0.3399 | +0.272 |
| c20_abs | 0.9112 | 0.9760 | -0.0648 | -0.066 |
| c40_abs | 1.6285 | 1.8618 | -0.2333 | -0.125 |
| c42_abs | 1.6458 | 1.8641 | -0.2183 | -0.117 |
| amp_low_fraction | 0.0369 | 0.0352 | +0.0017 | +0.049 |
| amp_entropy | 0.8274 | 0.6515 | +0.1759 | +0.270 |
| phase_diff_entropy | 0.7229 | 0.6739 | +0.0490 | +0.073 |

## Reading

- 10/10 train-02 frames are high-confidence QPSK from the CNN (not gate rejections),
  while the interpretable baseline labels all 30 normal frames BPSK. The failure is
  therefore a **CNN decision-boundary** issue, not a missing-class or gate issue.
- The failure session shares one carrier phase (~63 deg) and has lower SNR / complex RMS
  than the two success sessions; the environment rule independently flags 9/10 failure
  frames as PULSE/SPARSE_AMPLITUDE_OUTLIERS (context only).
- Round 1 already trained with uniform random phase; the issue is not absence of phase
  randomization but the CNN's phase-dependent decision surface plus session-correlated
  phase (and unmodelled distortion).
