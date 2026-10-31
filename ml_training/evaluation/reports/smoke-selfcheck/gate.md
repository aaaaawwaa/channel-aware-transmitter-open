# Quality-gate / rejection report (gate_test)

Generated: 2026-10-20T12:26:31Z  
CNN: artifact `ml_training/artifacts/cnn`, model.json sha256 `02687b5e5f6560ea0b372747106689736614650fba25c7ae6218592dd6ba1f7f`, status `experimental`, thresholds max_softmax=0.5 margin=0.0 min_complex_rms=28.52203968514979  

## Strict separation from the 4-class matrix

- Gate population: **gate_test split only** (OFF / NOISE / TONE / CLIP).
- Known-class false-rejection population: **test split known frames only** (clipped=False).
- Neither population is added to, or subtracted from, the 4x4 test confusion matrix (`sim_confusion.md`). Mixing the label spaces raises `LabelLeakError`.

## gate_test rejection rates per kind

| kind | n | clipped flag | rejected | rejection rate | accepted labels (false accepts) | rejection reasons |
|---|---|---|---|---|---|---|
| OFF | 10 | 0 | 10 | 1.0000 | - | below_energy_floor:6, confidence:4 |
| NOISE | 10 | 3 | 10 | 1.0000 | - | clipping:3, confidence:7 |
| TONE | 10 | 0 | 10 | 1.0000 | - | confidence:10 |
| CLIP | 10 | 10 | 10 | 1.0000 | - | clipping:10 |

A rejection is any predictor output whose label is not one of the four known classes (i.e. `UNKNOWN`); the reason column shows the gate reason.

## Known-class false rejection (known test frames)

| class | n | rejected | rejection rate |
|---|---|---|---|
| BPSK | 20 | 20 | 1.0000 |
| QPSK | 16 | 16 | 1.0000 |
| QAM16 | 16 | 16 | 1.0000 |
| QAM64 | 18 | 18 | 1.0000 |

Overall: 70 / 70 known frames rejected (rate 1.0000); reasons: confidence:70.

## Baseline quality gate

Status: **NOT_RUN** - feature baseline has no quality gate in CONTRACTS §9; only the CNN quality gate is characterised. The counts below are informational.

## NOT_RUN

- arm_inference: NOT_RUN - all inference in this report ran on the host CPU with the numpy runtime; these numbers are NOT ARM numbers and NOT board numbers.
- board_hardware_accuracy: NOT_RUN - no board/hardware execution was performed by the evaluation tools.
- azure_vm: NOT_RUN - no Azure connection information is available in this round.
- real_data_transfer: NOT_RUN here - real (physical) data is evaluated separately and read-only by ml_training/evaluation/real_transfer.py; it is not mixed into simulated metrics.
- val_split_metrics: NOT_RUN - the val split is reserved for checkpoint/threshold selection (CONTRACTS §6/§13); this script reports the independent test split only.
- train_split_metrics: NOT_RUN - training split accuracy is not an evaluation metric.
- baseline_gate_metrics: NOT_RUN - the feature baseline has no rejection gate in CONTRACTS §9; only the CNN quality gate is characterised on gate_test.
- arm_latency: NOT_RUN - no latency measurement was performed by this script.
