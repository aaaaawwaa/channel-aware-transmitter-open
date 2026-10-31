# Quality-gate / rejection report (gate_test)

Generated: 2026-10-20T13:19:25Z  
CNN: artifact `ml_training/artifacts/cnn`, model.json sha256 `25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc`, status `experimental`, thresholds max_softmax=0.45 margin=0.0 min_complex_rms=57.05962767972466  

## Strict separation from the 4-class matrix

- Gate population: **gate_test split only** (OFF / NOISE / TONE / CLIP).
- Known-class false-rejection population: **test split known frames only** (clipped=False).
- Neither population is added to, or subtracted from, the 4x4 test confusion matrix (`sim_confusion.md`). Mixing the label spaces raises `LabelLeakError`.

## gate_test rejection rates per kind

| kind | n | clipped flag | rejected | rejection rate | accepted labels (false accepts) | rejection reasons |
|---|---|---|---|---|---|---|
| OFF | 100 | 0 | 100 | 1.0000 | - | below_energy_floor:100 |
| NOISE | 100 | 12 | 23 | 0.2300 | QAM64:77 | clipping:13, confidence:10 |
| TONE | 100 | 0 | 0 | 0.0000 | BPSK:7, QAM16:37, QAM64:52, QPSK:4 | - |
| CLIP | 100 | 100 | 100 | 1.0000 | - | clipping:100 |

A rejection is any predictor output whose label is not one of the four known classes (i.e. `UNKNOWN`); the reason column shows the gate reason.

## Known-class false rejection (known test frames)

| class | n | rejected | rejection rate |
|---|---|---|---|
| BPSK | 600 | 11 | 0.0183 |
| QPSK | 600 | 22 | 0.0367 |
| QAM16 | 600 | 30 | 0.0500 |
| QAM64 | 600 | 18 | 0.0300 |

Overall: 81 / 2400 known frames rejected (rate 0.0338); reasons: confidence:81.

## Baseline quality gate

Status: **NOT_RUN** - feature baseline has no quality gate in CONTRACTS §9; only the CNN quality gate is characterised. Informational baseline label-rejection counts are recorded in sim_metrics.json.

Informational: the feature baseline rejected 0 known test frames by label only (it has no gate).

## NOT_RUN

- arm_inference: NOT_RUN - all inference in this report ran on the host CPU with the numpy runtime; these numbers are NOT ARM numbers and NOT board numbers.
- board_hardware_accuracy: NOT_RUN - no board/hardware execution was performed by the evaluation tools.
- azure_vm: NOT_RUN - no Azure connection information is available in this round.
- real_data_transfer: NOT_RUN here - real (physical) data is evaluated separately and read-only by ml_training/evaluation/real_transfer.py; it is not mixed into simulated metrics.
- val_split_metrics: NOT_RUN - the val split is reserved for checkpoint/threshold selection (CONTRACTS §6/§13); this script reports the independent test split only.
- train_split_metrics: NOT_RUN - training split accuracy is not an evaluation metric.
- baseline_gate_metrics: NOT_RUN - the feature baseline has no rejection gate in CONTRACTS §9; only the CNN quality gate is characterised on gate_test.
- arm_latency: NOT_RUN - no latency measurement was performed by this script.
