# Round-2 candidate experiment: phase-symmetry TTA

Dev-replay only: real round-1 physical frames + round-1 **validation** split.
Round-1 test split not used. Round-2 test set is generated separately with new seeds.

## Validation split (1200 frames, dev)

| variant | accuracy (gate counted) | rejected known | accepted macro-F1 |
|---|---|---|---|
| plain | 0.8400 | 36 | 0.8652 |
| tta | 0.8567 | 37 | 0.8834 |

Per phase octant (dev validation): plain vs TTA accuracy

| phase deg | n | plain | TTA |
|---|---|---|---|
| 0-15 | 184 | 0.8859 | 0.8967 |
| 15-30 | 208 | 0.8558 | 0.8798 |
| 30-45 | 210 | 0.7762 | 0.7857 |
| 45-60 | 173 | 0.8382 | 0.8555 |
| 60-75 | 207 | 0.8116 | 0.8406 |
| 75-90 | 218 | 0.8761 | 0.8853 |

## Real dev replay (physical BPSK frames)

| session | normal | plain BPSK | TTA BPSK |
|---|---|---|---|
| train-01 | 10 | 10 | 10 |
| train-02 | 10 | 0 | 0 |
| val-01 | 10 | 10 | 10 |

## train-02 per-frame (the failure session)

| frame | phase | plain label (score) | TTA label (score) |
|---|---|---|---|
| normal-01 | 62.83 | QPSK (0.825) | QPSK (0.886) |
| normal-02 | 63.07 | QPSK (0.679) | QPSK (0.801) |
| normal-03 | 62.82 | QPSK (0.607) | QPSK (0.806) |
| normal-04 | 63.02 | QPSK (0.814) | QPSK (0.894) |
| normal-05 | 63.05 | QPSK (0.773) | QPSK (0.850) |
| normal-06 | 63.08 | QPSK (0.769) | QPSK (0.853) |
| normal-07 | 63.30 | QPSK (0.876) | QPSK (0.929) |
| normal-08 | 62.91 | QPSK (0.716) | QPSK (0.812) |
| normal-09 | 62.72 | QPSK (0.758) | QPSK (0.861) |
| normal-10 | 63.17 | QPSK (0.528) | QPSK (0.766) |
