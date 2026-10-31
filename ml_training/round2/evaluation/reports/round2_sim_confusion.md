# Round-2 simulated test (NEW seeds, not used for tuning)

Dataset `sim-r2` manifest `83afaf8a9fe604114d3d1f260a4e1e445aaf7575ae9f26bff3d7bfbdd62e88e9`; test split 2,400 frames, 0 clipped.

4-class metric = pure class decision; gate rejection reported separately.

## Confusion matrices

### BEFORE (frozen round-1 CNN)

| true \ predicted | BPSK | QPSK | QAM16 | QAM64 |
|---|---|---|---|---|
| BPSK | 547 | 46 | 0 | 7 |
| QPSK | 40 | 546 | 1 | 13 |
| QAM16 | 3 | 16 | 473 | 108 |
| QAM64 | 0 | 11 | 122 | 467 |

### AFTER (round-2: CNN + BPSK/QPSK feature arbitration)

| true \ predicted | BPSK | QPSK | QAM16 | QAM64 |
|---|---|---|---|---|
| BPSK | 574 | 19 | 0 | 7 |
| QPSK | 23 | 563 | 1 | 13 |
| QAM16 | 2 | 17 | 473 | 108 |
| QAM64 | 0 | 11 | 122 | 467 |

## Per-class and summary

| class | support | before P/R/F1 | after P/R/F1 |
|---|---|---|---|
| BPSK | 600 | 0.9271 / 0.9117 / 0.9193 | 0.9583 / 0.9567 / 0.9575 |
| QPSK | 600 | 0.8821 / 0.9100 / 0.8958 | 0.9230 / 0.9383 / 0.9306 |
| QAM16 | 600 | 0.7936 / 0.7883 / 0.7910 | 0.7936 / 0.7883 / 0.7910 |
| QAM64 | 600 | 0.7849 / 0.7783 / 0.7816 | 0.7849 / 0.7783 / 0.7816 |

| metric | before | after |
|---|---|---|
| accuracy | 0.8471 | 0.8654 |
| macro-F1 | 0.8469 | 0.8652 |
| arbitrated decisions changed | - | 53 (+47 correct / -3 wrong) |

## Known-class false rejection (NEW test split)

| gate | rejected known frames | rate | accuracy (rejections as errors) |
|---|---|---|---|
| BEFORE (frozen round-1 gate) | 84 | 0.0350 | 0.8337 |
| AFTER (round-2 gate) | 98 | 0.0408 | 0.8475 |
