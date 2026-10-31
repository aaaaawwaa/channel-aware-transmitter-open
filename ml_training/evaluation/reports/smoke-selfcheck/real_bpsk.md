# Real BPSK transfer evaluation (read-only physical evidence)

Generated: 2026-10-20T12:32:07Z

> **Scope warning.** This report evaluates the single real BPSK configuration (3 MSym/s RECT, 60 MSPS) with zero-shot transfer of a model trained on the **simulated** dataset. It does **NOT** compute a four-class real accuracy, and its numbers are **host CPU numpy inference - NOT ARM and NOT board numbers**.

## Contract statements

- **read_only_evidence**: evidence/ was opened read-only; no file under evidence/ was created, modified, moved or deleted by this script.
- **label_source**: truth = trainval.truth (normal/off); transmission config = demod_result.submitted.config (BPSK/3Msym/s/RECT). No demod/model/environment output was used as a label.
- **four_class_real_accuracy**: NOT_RUN / prohibited: a four-class real overall accuracy cannot be computed from BPSK-only real truth and is not reported.
- **single_real_config**: The only real BPSK configuration is 3 MSym/s RECT at 60 MSPS with a 4096-sample snapshot; it is a single configuration, not a coverage sweep.
- **interference_scenarios**: Physical interference/impairment scenarios, if any exist elsewhere, are NOT included: no canonical per-frame manifest/evidence supports them in the scanned roots, so interference transfer is NOT_RUN.
- **host_vs_arm**: All inference is host CPU numpy; these results are NOT ARM and NOT board measurements and must not be reported as such.
- **dedupe_rule**: Frames are deduplicated by iq_sha256; equal hashes across campaign4/campaign5 are counted once at the first canonical path.
- **independence**: val-01 is the independent real session that participated in no threshold selection or tuning; it is reported first.

## Inventory / dedupe

- canonical root: `evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download`
- canonical sessions: train-01, train-02, val-01
- canonical frames: 33 (counted 33, rejected 0)
- duplicates mapped by iq_sha256: 22 frame(s) over 22 unique hash(es); each counted once
- non-canonical unique frames excluded: 1
- clipped frames excluded and listed separately: 0

Per-session counts:

| session | canonical frames | counted | rejected | normal | off | clipped |
|---|---|---|---|---|---|---|
| train-01 | 11 | 11 | 0 | 10 | 1 | 0 |
| train-02 | 11 | 11 | 0 | 10 | 1 | 0 |
| val-01 | 11 | 11 | 0 | 10 | 1 | 0 |

## val-01 - independent untouched real session (primary result)

val-01 participated in no threshold selection or tuning; it is the first reported transfer session.

| session | normal frames | off frames | BPSK recognized | recognition rate | normal rejection rate | misclassified/rejected destinations | OFF rejected | OFF rejection reasons |
|---|---|---|---|---|---|---|---|---|
| val-01 (independent) | 10 | 1 | 0/10 | 0.0000 | 1.0000 | UNKNOWN:10 | 1/1 | confidence:1 |

val-01 CNN failures: 10. val-01 baseline failures: 0.

## train-01 and train-02

These sessions are reported separately from val-01 (they are not the independent session, even though the model never trained on any real frame).

### CNN

| session | normal | off | BPSK recognized | recognition rate | normal rejection rate | destinations | OFF rejected | OFF reasons |
|---|---|---|---|---|---|---|---|---|
| train-01 | 10 | 1 | 0/10 | 0.0000 | 1.0000 | UNKNOWN:10 | 1/1 | confidence:1 |
| train-02 | 10 | 1 | 0/10 | 0.0000 | 1.0000 | UNKNOWN:10 | 1/1 | confidence:1 |

### Feature baseline

| session | normal | off | BPSK recognized | recognition rate | normal rejection rate | destinations | OFF rejected | OFF reasons |
|---|---|---|---|---|---|---|---|---|
| train-01 | 10 | 1 | 10/10 | 1.0000 | 0.0000 | - | 0/1 | - |
| train-02 | 10 | 1 | 10/10 | 1.0000 | 0.0000 | - | 0/1 | - |

## OFF gate behaviour (all sessions)

| session | normal | off | BPSK recognized | recognition rate | normal rejection rate | OFF rejected | OFF rejection reasons |
|---|---|---|---|---|---|---|---|
| train-01 | 10 | 1 | 0/10 | 0.0000 | 1.0000 | UNKNOWN:10 | 1/1 | confidence:1 |
| train-02 | 10 | 1 | 0/10 | 0.0000 | 1.0000 | UNKNOWN:10 | 1/1 | confidence:1 |
| val-01 (independent) | 10 | 1 | 0/10 | 0.0000 | 1.0000 | UNKNOWN:10 | 1/1 | confidence:1 |

OFF false accepts by CNN: none.
OFF false accepts by baseline (descriptive; the baseline has no gate): evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/off-01.iq, evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/off-01.iq, evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/val-01/off-01.iq.

## CNN vs feature baseline summary

| session | normal frames | CNN BPSK recognition | baseline BPSK recognition | CNN normal rejection | CNN OFF rejected | baseline OFF rejected |
|---|---|---|---|---|---|---|
| train-01 | 10 | 0.0000 | 1.0000 | 1.0000 | 1/1 | 0/1 |
| train-02 | 10 | 0.0000 | 1.0000 | 1.0000 | 1/1 | 0/1 |
| val-01 (independent) | 10 | 0.0000 | 1.0000 | 1.0000 | 1/1 | 0/1 |
| ALL sessions | 30 | 0.0000 | 1.0000 | 1.0000 | 3/3 | 0/3 |

## Failure cases (CNN)

| session | tag | truth | predicted | score | margin | reason | kind |
|---|---|---|---|---|---|---|---|
| train-01 | normal-01 | normal | UNKNOWN | 0.2735 | 0.0007 | confidence | rejected_unknown |
| train-01 | normal-02 | normal | UNKNOWN | 0.2735 | 0.0006 | confidence | rejected_unknown |
| train-01 | normal-03 | normal | UNKNOWN | 0.2735 | 0.0006 | confidence | rejected_unknown |
| train-01 | normal-04 | normal | UNKNOWN | 0.2734 | 0.0005 | confidence | rejected_unknown |
| train-01 | normal-05 | normal | UNKNOWN | 0.2734 | 0.0005 | confidence | rejected_unknown |
| train-01 | normal-06 | normal | UNKNOWN | 0.2735 | 0.0006 | confidence | rejected_unknown |
| train-01 | normal-07 | normal | UNKNOWN | 0.2735 | 0.0007 | confidence | rejected_unknown |
| train-01 | normal-08 | normal | UNKNOWN | 0.2735 | 0.0007 | confidence | rejected_unknown |
| train-01 | normal-09 | normal | UNKNOWN | 0.2737 | 0.0008 | confidence | rejected_unknown |
| train-01 | normal-10 | normal | UNKNOWN | 0.2735 | 0.0006 | confidence | rejected_unknown |
| train-02 | normal-01 | normal | UNKNOWN | 0.2753 | 0.0051 | confidence | rejected_unknown |
| train-02 | normal-02 | normal | UNKNOWN | 0.2754 | 0.0052 | confidence | rejected_unknown |
| train-02 | normal-03 | normal | UNKNOWN | 0.2755 | 0.0055 | confidence | rejected_unknown |
| train-02 | normal-04 | normal | UNKNOWN | 0.2755 | 0.0053 | confidence | rejected_unknown |
| train-02 | normal-05 | normal | UNKNOWN | 0.2754 | 0.0052 | confidence | rejected_unknown |
| train-02 | normal-06 | normal | UNKNOWN | 0.2754 | 0.0052 | confidence | rejected_unknown |
| train-02 | normal-07 | normal | UNKNOWN | 0.2754 | 0.0051 | confidence | rejected_unknown |
| train-02 | normal-08 | normal | UNKNOWN | 0.2753 | 0.0052 | confidence | rejected_unknown |
| train-02 | normal-09 | normal | UNKNOWN | 0.2751 | 0.0050 | confidence | rejected_unknown |
| train-02 | normal-10 | normal | UNKNOWN | 0.2758 | 0.0055 | confidence | rejected_unknown |
| val-01 | normal-01 | normal | UNKNOWN | 0.2728 | 0.0001 | confidence | rejected_unknown |
| val-01 | normal-02 | normal | UNKNOWN | 0.2728 | 0.0001 | confidence | rejected_unknown |
| val-01 | normal-03 | normal | UNKNOWN | 0.2728 | 0.0000 | confidence | rejected_unknown |
| val-01 | normal-04 | normal | UNKNOWN | 0.2727 | 0.0000 | confidence | rejected_unknown |
| val-01 | normal-05 | normal | UNKNOWN | 0.2728 | 0.0001 | confidence | rejected_unknown |
| val-01 | normal-06 | normal | UNKNOWN | 0.2728 | 0.0002 | confidence | rejected_unknown |
| val-01 | normal-07 | normal | UNKNOWN | 0.2729 | 0.0002 | confidence | rejected_unknown |
| val-01 | normal-08 | normal | UNKNOWN | 0.2728 | 0.0001 | confidence | rejected_unknown |
| val-01 | normal-09 | normal | UNKNOWN | 0.2727 | 0.0000 | confidence | rejected_unknown |
| val-01 | normal-10 | normal | UNKNOWN | 0.2728 | 0.0001 | confidence | rejected_unknown |

Baseline failures (descriptive): 0.

## Duplicate mapping (campaign4 -> campaign5)

| iq_sha256 | counted at canonical path | duplicate copies (paths) |
|---|---|---|
| da7989ab63cd0f2cf4bfaf821406a48c992086e84c5c9bd5ef040d5fd3716826 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-01.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-01.iq |
| 63903735de176ce42c81886298b61019959e8042fa412e87fae9dcf2525b3dc0 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-02.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-02.iq |
| 9ebaba8c4c86da08c1f4a741fc5a4aebce41924174b84b76605a8ba43c571c84 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-03.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-03.iq |
| 6a53871c06e3193d65ebec7a3dbfdd19fee47fe04451d84ec8e81985b1d84d60 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-04.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-04.iq |
| aa0194e6f30b46e6420831b81835e58f1edc4317d01ece4b9f6cf4b47db4eb64 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-05.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-05.iq |
| 81dd54d690a29d5b49a0c3996178529e3c8c485cbc4acb5411a972e82c5ee11a | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-06.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-06.iq |
| 0bcc24ef1652346be62d6d4d90d63752d26e5b1c75ce2624648a3d1059848f8b | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-07.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-07.iq |
| d6171f8191ff9647be729e390de921b4899ef3b2545ed8c5416aeacde78959cf | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-08.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-08.iq |
| cd7d2fd93c4b79dec962a58b6de5620a56bf46413cf4f907cd7da57d05a28bf6 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-09.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-09.iq |
| 698a2e04d021cc9add35c7f6ea2b7ac6629a7a14ff3abeb3519c7423cbdcdc94 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/normal-10.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/normal-10.iq |
| 366091c71b9a66bceda7b644c9ba6a7a7f4832cda34bcd0fe8959e2cfffe65c9 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-01/off-01.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-01/off-01.iq |
| 13a61a64b4b04e273479a06ce69a4a71232e225b2066ea0a826bb42be5a3f75b | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-01.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-01.iq |
| e8d169ea82eddee194b99843fe0fc59d432869322d987d73ab2ed64e69d6206d | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-02.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-02.iq |
| 13e216876e3b89fae6e7f6ce9a96584352fd2360d5dbde5ea1a6be5f90e1d49b | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-03.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-03.iq |
| 2e5cb894e82b33606bb9a7dfef7f359d430e90aa545078d526f03ffa35f93c17 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-04.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-04.iq |
| 4ea00fd2d3d12cb1fc95e67f5ada14fd79cc557750ff1c8102881fb6bbb96be8 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-05.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-05.iq |
| 38b66e07a2f67ce5baad33b00d363bfe40e3302b56ac735d3ba4e8d22170d0e3 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-06.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-06.iq |
| 86cc270d1af336c93932b34db617fa47e123e7906d34b2398d94c5c576763fd8 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-07.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-07.iq |
| b8e69fa19329c3bdb4d521989ff50743ad81513b89faa752bc2c43f44b4f9562 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-08.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-08.iq |
| dc6bc7cb3a51117585f74d7106197e4fdac3721178f3469a52ca88580583a772 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-09.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-09.iq |
| 810fd2bbf981237b14e470f31f48e30f9fee9fbc37c8036de6ad2340e05ba260 | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/normal-10.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/normal-10.iq |
| b5314840fd8fdbedf606ca134dac6e90f5b3a335f1168eeca9444aba0385635d | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/train-02/off-01.iq | evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/train-02/off-01.iq |

Duplicate copies are NOT counted a second time; the first canonical path is counted.

Non-canonical unique frames excluded:

| path | session | tag | reason | truth (read-only) | clipped |
|---|---|---|---|---|---|
| evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/val-01/normal-01.iq | val-01 | normal-01 | unique frame outside the canonical campaign5 session set; not counted (CONTRACTS §11 canonical roots) | normal | false |

## NOT_RUN

- arm_inference: NOT_RUN - all inference in this report ran on the host CPU with the numpy runtime; these numbers are NOT ARM numbers and NOT board numbers.
- board_hardware_accuracy: NOT_RUN - no board/hardware execution was performed by the evaluation tools.
- azure_vm: NOT_RUN - no Azure connection information is available in this round.
- four_class_real_overall_accuracy: NOT_RUN - not computed and prohibited by CONTRACTS §11; real truth exists only for the single BPSK-normal configuration and OFF scenes.
- real_qpsk_qam16_qam64_frames: NOT_RUN - the canonical real sessions contain no QPSK/QAM16/QAM64 ground-truth frames.
- real_quality_gate_population: NOT_RUN - no real gate_test population with OFF/NOISE/TONE/CLIP was collected; only OFF frames exist and are reported as OFF gate behaviour.
- interference_physical_scenarios: NOT_RUN - no canonical per-frame manifest/receipt covering an interference physical scenario was included; such scenarios are absent from the canonical session set and are NOT_RUN, not silently folded into the BPSK transfer numbers.
- gate_threshold_selection_on_real_data: NOT_RUN - forbidden by CONTRACTS §11; thresholds are selected on simulation val + gate_cal only, and val-01 is untouched by any selection.
- demod_ber_derived_accuracy: NOT_RUN - demod BER exists on the board path but is not a model metric and is not used as a label or accuracy here.
- arm_inference: NOT_RUN - see host-vs-ARM statement.
- board_hardware_accuracy: NOT_RUN - see host-vs-ARM statement.

## Limitations

- Small real sample: 3 sessions x (10 normal BPSK + 1 off) = 30 normal + 3 OFF counted frames; one frame per listed file, no augmentation and no reuse.
- Real BPSK has exactly ONE configuration in scope: fs=60 MSPS, BPSK 3 MSym/s RECT, LO=80 MHz, attenuation 46000 mdB, 4096-sample snapshot. Other symbol rates/shapings are NOT_RUN (no data).
- OFF frames carry trainval.physical=false (no-signal scene); they are real board captures used only for OFF gate behaviour, never as class truth and never with a fake class label.
- Board algorithm labels differ from the contract truth (several normal frames are labelled UNKNOWN/PULSE/CLEAR by the board algorithm); those outputs are ignored here, not converted into labels.
- No clipped real frames were present in the canonical sessions, so real clipping rejection is NOT_RUN (simulation covers the CLIP gate kind).
- Zero-shot transfer only: no real frame was used for training, validation or threshold selection (CONTRACTS §11); val-01 is the untouched independent real session.
- Inference ran on the host CPU with the numpy runtime; these are NOT ARM numbers and NOT board numbers.
- campaign4 and campaign5 duplicate frames were deduplicated by iq_sha256; the one unique campaign4 val-01 frame lies outside the canonical campaign5 session set and is excluded (listed with its reason), not counted.
