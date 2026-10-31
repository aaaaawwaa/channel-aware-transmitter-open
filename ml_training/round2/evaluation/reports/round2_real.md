# Round-2 real dev replay (read-only physical frames)

**Scope**: the three real sessions were used in round-1 failure analysis and round-2 design; this is development replay, not an independent generalisation test. Truth only from `trainval.truth` + `demod_result.submitted.config`.

| session | normal | BEFORE BPSK | AFTER BPSK | arbitrated | OFF BEFORE rejected | OFF AFTER rejected |
|---|---|---|---|---|---|---|
| train-01 | 10 | 10/10 | 10/10 | 0 | 1/1 | 1/1 |
| train-02 | 10 | 0/10 | 10/10 | 10 | 1/1 | 1/1 |
| val-01 | 10 | 10/10 | 10/10 | 0 | 1/1 | 1/1 |

OFF rejection reasons (AFTER): {"train-01": ["noise_like"], "train-02": ["noise_like"], "val-01": ["noise_like"]}

Failure frames (AFTER):
- none
