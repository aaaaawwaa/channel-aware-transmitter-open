# Simulated per-SNR metrics (4-class test split)

Generated: 2026-10-20T12:26:31Z  
Dataset: `ml_training/datasets/sim-smoke`  
CNN: artifact `ml_training/artifacts/cnn`, model.json sha256 `02687b5e5f6560ea0b372747106689736614650fba25c7ae6218592dd6ba1f7f`, status `experimental`, thresholds max_softmax=0.5 margin=0.0 min_complex_rms=28.52203968514979  
Baseline: artifact `ml_training/artifacts/baseline`, model.json sha256 `946f0dc8c19b78d2c56a7eb1543cb1da4de8103dbe6512cfe93b62fa712991bc`, status `experimental`, thresholds max_softmax=0.0 margin=0.0 min_complex_rms=28.52203968514979  

Population: test split only, clipped=False; `n` is the number of frames at that SNR. Per-SNR macro-F1 is the mean F1 over classes with support>0 at that SNR; a recall of `-` means no frame of that class at that SNR.

## CNN

| SNR (dB) | n | accuracy | macro-F1 | recall BPSK | recall QPSK | recall QAM16 | recall QAM64 |
|---|---|---|---|---|---|---|---|
| 0.0000 | 40 | 0.2500 | 0.1000 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| 10.0000 | 30 | 0.2667 | 0.1053 | 0.0000 | 0.0000 | 0.0000 | 1.0000 |

## Feature baseline

| SNR (dB) | n | accuracy | macro-F1 | recall BPSK | recall QPSK | recall QAM16 | recall QAM64 |
|---|---|---|---|---|---|---|---|
| 0.0000 | 40 | 0.3750 | 0.3083 | 0.7000 | 0.1000 | 0.7000 | 0.0000 |
| 10.0000 | 30 | 0.8000 | 0.7272 | 1.0000 | 1.0000 | 0.1667 | 0.8750 |

Quality-gate rejection rates are not part of these tables (see `gate.md`). Host CPU numpy runtime, NOT ARM/board.

## NOT_RUN

- arm_inference: NOT_RUN - all inference in this report ran on the host CPU with the numpy runtime; these numbers are NOT ARM numbers and NOT board numbers.
- board_hardware_accuracy: NOT_RUN - no board/hardware execution was performed by the evaluation tools.
- azure_vm: NOT_RUN - no Azure connection information is available in this round.
- real_data_transfer: NOT_RUN here - real (physical) data is evaluated separately and read-only by ml_training/evaluation/real_transfer.py; it is not mixed into simulated metrics.
- val_split_metrics: NOT_RUN - the val split is reserved for checkpoint/threshold selection (CONTRACTS §6/§13); this script reports the independent test split only.
- train_split_metrics: NOT_RUN - training split accuracy is not an evaluation metric.
- baseline_gate_metrics: NOT_RUN - the feature baseline has no rejection gate in CONTRACTS §9; only the CNN quality gate is characterised on gate_test.
- arm_latency: NOT_RUN - no latency measurement was performed by this script.
