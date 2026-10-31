# Simulated 4-class test confusion matrix

Generated: 2026-10-20T13:19:25Z  
Dataset: `ml_training/datasets/sim-v1` (manifest sha256 `9f70a0c8c5e3b5a1395617d71f860fd60c2bd3462511efa5330dea6f310a3ecd`)  
CNN: artifact `ml_training/artifacts/cnn`, model.json sha256 `25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc`, status `experimental`, thresholds max_softmax=0.45 margin=0.0 min_complex_rms=57.05962767972466  
Baseline: artifact `ml_training/artifacts/baseline`, model.json sha256 `caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014`, status `experimental`, thresholds max_softmax=0.0 margin=0.0 min_complex_rms=57.05962767972466  

## Protocol (frozen separation)

- 4-class population: **test split only, clipped=False frames only** (2400 frames used, 0 dropped as clipped).
- Prediction: argmax over the four class scores returned by the predictor; a quality-gate rejection does not change the 4-class prediction.
- Quality-gate results are reported separately in `gate.md` and never enter the 4x4 matrix.
- Host CPU numpy runtime: these are **NOT ARM and NOT board numbers**.

## CNN confusion matrix

| true \ predicted | BPSK | QPSK | QAM16 | QAM64 | row total |
|---|---|---|---|---|---|
| BPSK | 546 | 46 | 0 | 8 | 600 |
| QPSK | 45 | 541 | 2 | 12 | 600 |
| QAM16 | 6 | 14 | 464 | 116 | 600 |
| QAM64 | 2 | 5 | 112 | 481 | 600 |

## Feature-baseline confusion matrix

| true \ predicted | BPSK | QPSK | QAM16 | QAM64 | row total |
|---|---|---|---|---|---|
| BPSK | 546 | 18 | 15 | 21 | 600 |
| QPSK | 7 | 471 | 40 | 82 | 600 |
| QAM16 | 15 | 46 | 296 | 243 | 600 |
| QAM64 | 21 | 63 | 173 | 343 | 600 |

## Per-class precision / recall / F1

| class | support | CNN P | CNN R | CNN F1 | baseline P | baseline R | baseline F1 |
|---|---|---|---|---|---|---|---|
| BPSK | 600 | 0.9115 | 0.9100 | 0.9108 | 0.9270 | 0.9100 | 0.9184 |
| QPSK | 600 | 0.8927 | 0.9017 | 0.8972 | 0.7876 | 0.7850 | 0.7863 |
| QAM16 | 600 | 0.8028 | 0.7733 | 0.7878 | 0.5649 | 0.4933 | 0.5267 |
| QAM64 | 600 | 0.7796 | 0.8017 | 0.7905 | 0.4978 | 0.5717 | 0.5322 |

## Summary

| metric | CNN | baseline | delta (CNN - baseline) |
|---|---|---|---|
| accuracy | 0.8467 | 0.6900 | 0.1567 |
| macro-F1 | 0.8465 | 0.6909 | 0.1556 |
| gate-rejected known frames (count) | 81 | 0 | 81 |
| accuracy with gate rejections counted as errors | 0.8358 | 0.6900 | 0.1458 |

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
