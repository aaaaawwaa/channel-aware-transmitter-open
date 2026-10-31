# Round-2 independent physical session (read-only, NOT used in development)

Session: `evidence/rf-teaching/s2/46db-trainval-campaign3-2026-10-18/download/train-01` (release `1a30f4b7b9aebe27965fcdd6dcf7634e`), 10 normal BPSK + 1 OFF frame, TX config BPSK/3 MSym/s/RECT. IQ hashes are disjoint from the three development-replay sessions; the rules were frozen before this session was evaluated.

| session | normal | BEFORE BPSK | AFTER BPSK | arbitrated | OFF BEFORE rejected | OFF AFTER rejected |
|---|---|---|---|---|---|---|
| campaign3 train-01 (independent) | 10 | 0/10 | 10/10 | 10 | 0/1 | 1/1 |

Per-frame results:

- `normal-01` truth=normal rms=245.573319746046 phase=55.27 | before=QPSK () | after=BPSK ()
- `normal-02` truth=normal rms=248.01248235975947 phase=54.91 | before=QPSK () | after=BPSK ()
- `normal-03` truth=normal rms=245.86258885331915 phase=55.02 | before=QPSK () | after=BPSK ()
- `normal-04` truth=normal rms=242.1175952541257 phase=55.28 | before=QPSK () | after=BPSK ()
- `normal-05` truth=normal rms=245.29519146740574 phase=55.7 | before=QPSK () | after=BPSK ()
- `normal-06` truth=normal rms=244.5304512766508 phase=55.92 | before=QPSK () | after=BPSK ()
- `normal-07` truth=normal rms=246.5272273565843 phase=54.87 | before=QPSK () | after=BPSK ()
- `normal-08` truth=normal rms=247.69557505210025 phase=55.09 | before=QPSK () | after=BPSK ()
- `normal-09` truth=normal rms=244.23635972979636 phase=55.35 | before=QPSK () | after=BPSK ()
- `normal-10` truth=normal rms=243.94001507959135 phase=55.3 | before=QPSK () | after=BPSK ()
- `off-01` truth=off rms=112.18721709574358 phase=25.58 | before=QAM64 () | after=UNKNOWN (noise_like)

Scope: independent physical BPSK session only (single configuration; not a four-class real accuracy and not ARM/board inference).
