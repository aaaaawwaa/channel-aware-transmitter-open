# Codex final review entry — QAM64 trial stage + four-frame runner

Status: **READY_FOR_ONE_CODEX_CLOSEOUT_REVIEW / NOT_DEPLOYED / NOT_RF**. Review only. Candidate `4ea1c0a…` is already covered by `docs/reviews/2026-10-25-codex-qam64-candidate-review.md` (APPROVED_FOR_TRIAL_PREPARATION); this entry covers only the **new trial stage/acceptance/unit/preflight** and **new QAM64 four-frame runner**.

## 1 New trial stage

Root: `<LOCAL_PATH>`

| item | value |
|---|---|
| candidate | `4ea1c0a68f03773fe1a77d1876ab1548` |
| candidate.tgz | `5dd1b2eeb15befbffaf0e181fbf53ad19b374245ddb2457e3270501ba44ddb91` |
| parent | `cd76818afc3bee7c610678574e892b26` |
| parent archive | `440ba377a84be4ac2ee6e6cd85980106271cff4a26863235cb84718bc8273c17` |
| contract | `cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1` (64) |
| trial id / unit | `rf-qam64-4ea1c0a` / `rf-qam64-trial-4ea1c0a.service` |
| stage SHA | `ebd075be0b7d50c692e2a52efb247c47e2614942bf34cf69e1cfcce303d183e5` (17 files) |
| trial-stage.tgz | **`7263172bd5632976d9015dce7fc19a323a0002d3281e79d7d5dde3b0fa7d5588`** (4,111,623 B) |
| bundle receipt | `7dd8ec0dcba7b3eb71e8804f954ab620a448d5ad3d1e1774e9c739f870077e95` |
| stage path | `/var/lib/rf-deployment-trials/rf-qam64-4ea1c0a/stage` |
| link / durable | `/tmp/rf-teaching-install-rf-qam64-4ea1c0a` / `/var/lib/rf-deployment-trials/rf-qam64-4ea1c0a` |
| acceptance budget | 120 s; install/rollback outer timing separate |

Stage files: `candidate.tgz`, `rollback-parent.tgz`, `install.sh`, `install_state.py`, `trial-install.sh`, `dispatch_preflight.py`, `rf-qam64-trial-4ea1c0a.service.in`, `stage-sha256.json`, `TRIAL-STAGE.json`, `trial/acceptance.py`, `trial/frames/*` (BPSK/QPSK/QAM16/QAM64 clean + QAM64 mismatch), `trial/host-acceptance.json`. No placeholders.

## 2 Host results

- `host-preflight.json`: **9/9 PASS** — candidate identity/64, installer layout/entry, parent identity/archive, new release absent, digest, unique trial, bounded acceptance.
- `host-acceptance.json`: **PASS 1.469 s** (≤120), 22/22 checks:
  - actual candidate identity/release/64 software; QAM64 algorithm version and receiver/pipeline SHA; modules imported from release;
  - QAM64 0/384, valid127, align378, compare_start63, EVM unavailable;
  - no-signal `LOW_ENERGY`; mismatch `REFERENCE_NOT_FOUND`; no BER;
  - worker/UI same frame 0/384; config change clears old; BPSK 0/138/QPSK 0/128/QAM16 0/256;
  - host skips board RF/service/boot; board mode runs them.
- `offline-tests.json`: **PASS** success wrapper `TRIAL_ACCEPTED`; acceptance failure `TRIAL_ROLLED_BACK`; timeout rollback; missing `pynq_overlay/sw/install.sh` preflight refusal before stop; wrong software count failed.
- stage archive 17 members equals loose tree; all files real.

## 3 New QAM64 runner

File: `scripts/rf_qam64_real_validation.py` SHA `9873a7bf6dc327f6dc0c0dd16af3744edf6f5ac71a0da7296ee85d2cf8e0353e`
Session file: `scripts/rf_bpsk_ui_board_session.py` SHA `7299123bb0485a6eb1ba0410da8bd5881bf052cdba61c89a74ac971db40bbd6f` (QAM64 RX only + existing capture_deadline; default unchanged 15 s)
Tests: `scripts/tests/test_rf_qam64_real_session.py` SHA `d2a1d7f18a7fc8cbad92036e7f958c6ce656570889a47390a8e9bece8ab91faa`; **24 passed**; QAM64 receiver/product **131 passed**.
Frames/budget: OFF-first → QAM64/RECT39 A → 39 B → OFF-last; **4/4/4/2/4/0**; ≤180 s; capture_deadline=10 s; QAM64 product capability; ON 0/384/127/378/63; OFF false/null; legal BER retained, structural stops; same-frame metadata/observation binding; replay entry RESERVED.

## 4 Exact next commands

See `evidence/rf-teaching/qam64-trial-layout-2026-10-25/NEXT-STEP.md`. Board commands (not executed): mkdir stage, extract trial-stage.tgz, symlink, install unit 0644 root:root (full name, no enable/alias), daemon-reload, preflight with parent manifest, exactly one `systemctl start`. 120 s acceptance; success `TRIAL_ACCEPTED`+candidate current/active+independent readback; failure existing transaction parent + local rollback JSON independent readback; SSH loss observe only.
Four-frame command: `python -B scripts/rf_qam64_real_validation.py --expected-release 4ea1c0a… --output <new-empty> --cable-confirmed`.

## 5 Review questions / limitations

1. Any P1 in new stage/preflight/acceptance/unit or runner before a user-authorized trial?
2. Host acceptance skipped board RF/service/boot; board acceptance is expected to fill them. Is that split acceptable?
3. Runner not executed; trial stage not uploaded. No install/RF/current change.
4. Candidate approved for trial preparation only; no new candidate changes.
