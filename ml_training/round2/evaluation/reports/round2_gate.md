# Round-2 quality gate on NEW gate_test data

Known-class false rejection is measured on the NEW test split.

| kind | n | BEFORE rejected | BEFORE rate | AFTER rejected | AFTER rate |
|---|---|---|---|---|---|
| OFF | 100 | 100 | 1.0000 | 100 | 1.0000 |
| NOISE | 100 | 20 | 0.2000 | 100 | 1.0000 |
| TONE | 100 | 0 | 0.0000 | 100 | 1.0000 |
| CLIP | 100 | 100 | 1.0000 | 100 | 1.0000 |

Rejection reasons (AFTER):
- OFF: {"below_energy_floor": 100}
- NOISE: {"noise_like": 87, "clipping": 13}
- TONE: {"tone_like": 100}
- CLIP: {"clipping": 100}

Known false rejection: BEFORE 84/2400, AFTER 98/2400.

Known rejection reasons (AFTER): {"confidence": 79, "noise_like": 19}

Accepted known frames (AFTER): 2302/2400, accuracy among accepted 0.8836.

Known false rejection per SNR (AFTER):

| SNR dB | n | before rejected | after rejected |
|---|---|---|---|
| -6.0 | 200 | 74 | 88 |
| -3.0 | 200 | 9 | 9 |
| 0.0 | 200 | 1 | 1 |
| 2.0 | 200 | 0 | 0 |
| 4.0 | 200 | 0 | 0 |
| 6.0 | 200 | 0 | 0 |
| 8.0 | 200 | 0 | 0 |
| 10.0 | 200 | 0 | 0 |
| 12.0 | 200 | 0 | 0 |
| 15.0 | 200 | 0 | 0 |
| 18.0 | 200 | 0 | 0 |
| 20.0 | 200 | 0 | 0 |

Known false rejection per class (AFTER):

| class | n | before rejected | after rejected |
|---|---|---|---|
| BPSK | 600 | 19 | 21 |
| QPSK | 600 | 27 | 30 |
| QAM16 | 600 | 25 | 29 |
| QAM64 | 600 | 13 | 18 |
