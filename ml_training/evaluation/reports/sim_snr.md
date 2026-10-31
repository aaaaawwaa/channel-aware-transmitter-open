# Simulated per-SNR metrics (4-class test split)

Generated: 2026-10-20T13:19:25Z  
Dataset: `ml_training/datasets/sim-v1`  
CNN: artifact `ml_training/artifacts/cnn`, model.json sha256 `25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc`, status `experimental`, thresholds max_softmax=0.45 margin=0.0 min_complex_rms=57.05962767972466  
Baseline: artifact `ml_training/artifacts/baseline`, model.json sha256 `caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014`, status `experimental`, thresholds max_softmax=0.0 margin=0.0 min_complex_rms=57.05962767972466  

Population: test split only, clipped=False; `n` is the number of frames at that SNR. Per-SNR macro-F1 is the mean F1 over classes with support>0 at that SNR; a recall of `-` means no frame of that class at that SNR.

## CNN

| SNR (dB) | n | accuracy | macro-F1 | recall BPSK | recall QPSK | recall QAM16 | recall QAM64 |
|---|---|---|---|---|---|---|---|
| -6.0000 | 200 | 0.4300 | 0.3967 | 0.5200 | 0.4400 | 0.0800 | 0.6800 |
| -3.0000 | 200 | 0.6400 | 0.6379 | 0.7200 | 0.7400 | 0.4800 | 0.6200 |
| 0.0000 | 200 | 0.7950 | 0.7946 | 0.9200 | 0.9200 | 0.7200 | 0.6200 |
| 2.0000 | 200 | 0.7500 | 0.7464 | 0.9600 | 0.8200 | 0.7400 | 0.4800 |
| 4.0000 | 200 | 0.8750 | 0.8742 | 0.9800 | 0.9000 | 0.9000 | 0.7200 |
| 6.0000 | 200 | 0.9400 | 0.9399 | 0.9800 | 1.0000 | 0.9400 | 0.8400 |
| 8.0000 | 200 | 0.9450 | 0.9450 | 0.9400 | 1.0000 | 0.9000 | 0.9400 |
| 10.0000 | 200 | 0.9500 | 0.9500 | 1.0000 | 1.0000 | 0.9200 | 0.8800 |
| 12.0000 | 200 | 0.9650 | 0.9650 | 0.9800 | 1.0000 | 0.9600 | 0.9200 |
| 15.0000 | 200 | 0.9750 | 0.9750 | 0.9600 | 1.0000 | 0.9400 | 1.0000 |
| 18.0000 | 200 | 0.9600 | 0.9600 | 0.9800 | 1.0000 | 0.9000 | 0.9600 |
| 20.0000 | 200 | 0.9350 | 0.9346 | 0.9800 | 1.0000 | 0.8000 | 0.9600 |

## Feature baseline

| SNR (dB) | n | accuracy | macro-F1 | recall BPSK | recall QPSK | recall QAM16 | recall QAM64 |
|---|---|---|---|---|---|---|---|
| -6.0000 | 200 | 0.3200 | 0.3073 | 0.4200 | 0.5200 | 0.2000 | 0.1400 |
| -3.0000 | 200 | 0.3850 | 0.3892 | 0.6800 | 0.3200 | 0.3400 | 0.2000 |
| 0.0000 | 200 | 0.4550 | 0.4457 | 0.8200 | 0.1400 | 0.4000 | 0.4600 |
| 2.0000 | 200 | 0.5800 | 0.5826 | 1.0000 | 0.5000 | 0.2400 | 0.5800 |
| 4.0000 | 200 | 0.6950 | 0.6519 | 1.0000 | 0.9400 | 0.0600 | 0.7800 |
| 6.0000 | 200 | 0.7700 | 0.7374 | 1.0000 | 1.0000 | 0.2000 | 0.8800 |
| 8.0000 | 200 | 0.7550 | 0.7146 | 1.0000 | 1.0000 | 0.1400 | 0.8800 |
| 10.0000 | 200 | 0.8000 | 0.7945 | 1.0000 | 1.0000 | 0.4800 | 0.7200 |
| 12.0000 | 200 | 0.9150 | 0.9141 | 1.0000 | 1.0000 | 0.8600 | 0.8000 |
| 15.0000 | 200 | 0.9050 | 0.9007 | 1.0000 | 1.0000 | 1.0000 | 0.6200 |
| 18.0000 | 200 | 0.8500 | 0.8342 | 1.0000 | 1.0000 | 1.0000 | 0.4000 |
| 20.0000 | 200 | 0.8500 | 0.8324 | 1.0000 | 1.0000 | 1.0000 | 0.4000 |

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
