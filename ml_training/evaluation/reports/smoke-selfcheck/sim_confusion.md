# Simulated 4-class test confusion matrix

Generated: 2026-10-20T12:26:31Z  
Dataset: `ml_training/datasets/sim-smoke` (manifest sha256 `3e5a008d0394aba0933bc814a31f3fe2d6a44351e9a619b5a36a882a80e994af`)  
CNN: artifact `ml_training/artifacts/cnn`, model.json sha256 `02687b5e5f6560ea0b372747106689736614650fba25c7ae6218592dd6ba1f7f`, status `experimental`, thresholds max_softmax=0.5 margin=0.0 min_complex_rms=28.52203968514979  
Baseline: artifact `ml_training/artifacts/baseline`, model.json sha256 `946f0dc8c19b78d2c56a7eb1543cb1da4de8103dbe6512cfe93b62fa712991bc`, status `experimental`, thresholds max_softmax=0.0 margin=0.0 min_complex_rms=28.52203968514979  

## Protocol (frozen separation)

- 4-class population: **test split only, clipped=False frames only** (70 frames used, 10 dropped as clipped).
- Prediction: argmax over the four class scores returned by the predictor; a quality-gate rejection does not change the 4-class prediction.
- Quality-gate results are reported separately in `gate.md` and never enter the 4x4 matrix.
- Host CPU numpy runtime: these are **NOT ARM and NOT board numbers**.

## CNN confusion matrix

| true \ predicted | BPSK | QPSK | QAM16 | QAM64 | row total |
|---|---|---|---|---|---|
| BPSK | 0 | 10 | 0 | 10 | 20 |
| QPSK | 0 | 10 | 0 | 6 | 16 |
| QAM16 | 0 | 10 | 0 | 6 | 16 |
| QAM64 | 0 | 10 | 0 | 8 | 18 |

## Feature-baseline confusion matrix

| true \ predicted | BPSK | QPSK | QAM16 | QAM64 | row total |
|---|---|---|---|---|---|
| BPSK | 17 | 1 | 2 | 0 | 20 |
| QPSK | 0 | 7 | 8 | 1 | 16 |
| QAM16 | 2 | 0 | 8 | 6 | 16 |
| QAM64 | 2 | 1 | 8 | 7 | 18 |

## Per-class precision / recall / F1

| class | support | CNN P | CNN R | CNN F1 | baseline P | baseline R | baseline F1 |
|---|---|---|---|---|---|---|---|
| BPSK | 20 | 0.0000 | 0.0000 | 0.0000 | 0.8095 | 0.8500 | 0.8293 |
| QPSK | 16 | 0.2500 | 0.6250 | 0.3571 | 0.7778 | 0.4375 | 0.5600 |
| QAM16 | 16 | 0.0000 | 0.0000 | 0.0000 | 0.3077 | 0.5000 | 0.3810 |
| QAM64 | 18 | 0.2667 | 0.4444 | 0.3333 | 0.5000 | 0.3889 | 0.4375 |

## Summary

| metric | CNN | baseline | delta (CNN - baseline) |
|---|---|---|---|
| accuracy | 0.2571 | 0.5571 | -0.3000 |
| macro-F1 | 0.1726 | 0.5519 | -0.3793 |
| gate-rejected known frames (count) | 70 | 0 | 70 |
| accuracy with gate rejections counted as errors | 0.0000 | 0.5571 | -0.5571 |

`accuracy with gate rejections` is informational: the frozen 4-class metric is the pure argmax accuracy above.

## NOT_RUN

- arm_inference: NOT_RUN - all inference in this report ran on the host CPU with the numpy runtime; these numbers are NOT ARM numbers and NOT board numbers.
- board_hardware_accuracy: NOT_RUN - no board/hardware execution was performed by the evaluation tools.
- azure_vm: NOT_RUN - no Azure connection information is available in this round.
- real_data_transfer: NOT_RUN here - real (physical) data is evaluated separately and read-only by ml_training/evaluation/real_transfer.py; it is not mixed into simulated metrics.
- val_split_metrics: NOT_RUN - the val split is reserved for checkpoint/threshold selection (CONTRACTS §6/§13); this script reports the independent test split only.
- train_split_metrics: NOT_RUN - training split accuracy is not an evaluation metric.
- baseline_gate_metrics: NOT_RUN - the feature baseline has no rejection gate in CONTRACTS §9; only the CNN quality gate is characterised on gate_test.
- arm_latency: NOT_RUN - no latency measurement was performed by this script.

## Limitations

- Metrics are single-run point estimates on the frozen test split; no confidence intervals are computed.
- The synthetic impairment model explicitly excludes IQ imbalance, DC offset, receive filter/RRC distortion, multipath, phase noise, AGC transients, hardware frequency response and RF-chain nonlinearity (CONTRACTS §5); these are unmodeled, not calibrated.
- gate_test CLIP frames are kept even when their quantization overflow sets clipped=True; they are used only for the gate rejection rates, never for the 4x4 matrix.
- Per-SNR macro-F1 is the mean F1 over classes with support>0 at that SNR; recall is null for a class with no support at that SNR.
