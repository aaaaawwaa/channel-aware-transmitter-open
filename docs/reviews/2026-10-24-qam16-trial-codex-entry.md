# Codex key-stage review entry — QAM16 trial bundle

Status: **READY_FOR_CODEX_REVIEW / NOT_APPROVED / NOT_DEPLOYED**. Review only; do not install, connect board, init RF, or modify files.

## 1 Absolute paths and digests

- Workspace: `<LOCAL_PATH>`
- Candidate release: `<LOCAL_PATH>`
- Candidate archive: `...\qam16-rect-product-2026-10-24\candidate\candidate.tgz`
- Bundle: `...\qam16-rect-product-2026-10-24\bundle`
- Executable stage: `...\bundle\trial\`
- Stage archive: `...\bundle\trial-stage.tgz`
- Acceptance summary: `...\bundle\acceptance-summary.json`
- Bundle report: `...\bundle\BUNDLE-REPORT.md`
- Bundle receipt: `...\bundle\bundle-receipt.json`
- ARM: `...\qam16-rect-product-2026-10-24\arm\` (`compare.json`, `arm-result.json`, `host-result.json`, `arm-performance.json`, `logs-*`)
- Product report: `...\qam16-rect-product-2026-10-24\PRODUCT-REPORT.md`
- Lead review: `docs/reviews/2026-10-24-qam16-trial-bundle-rrc-gate.md`
- Spec: `docs/specs/2026-10-24-qam16-rect-product-spec.md`

| identity | value |
|---|---|
| candidate | `cd76818afc3bee7c610678574e892b26` |
| parent/rollback | `bedf98c77283002c632dd7f345abaff9` |
| contract | `cnn-bpsk-modrec4-qpsk-rrc-qam16-v1` |
| software members | 62 (not archive file count) |
| candidate.tgz | 1,695,506 B, SHA-256 `181bf027a321da6d4ceadd664a7ea24fdaa1660918a72a908deb35e8fb2a4816` |
| parent archive | `f17b53d8b0f8e16fbeb04f6714f941d4fcc3e043b703eb41bab78502ce4f95ac` |
| stage files | 16, SHA-256 `345461624b16212d0b8c011b389c2059b3cee331b23e8c9e13f781286cdd199c` |
| trial-stage.tgz | 3,751,266 B, SHA-256 `0a847d19b2c247ff09f9777c0c8d30ebd7e459c0c2f60779fba7d4271dd114dd` |
| unit | `rf-qam16-trial-cd76818a.service` (start by full name; no enable) |
| budget | 120 s hard; no retry |

## 2 Candidate delta vs parent

`minimal-diff.json`: changed = `install.sh`, `merged_release.py`, `rf_status_worker.py`, `ui_model.py`, `rf_qam16_receiver.py`, `rf_qam16_pipeline.py`; `bit_unchanged=true`, `hwh_unchanged=true`, `daemon_unchanged=true`, `modrec4_unchanged=true`, `six_class_unchanged=true`; old 55/58/60 maps strict subsets.
- QAM16/RECT frozen intervals: valid127, alignment63 symbols/252 bits, comparison start63, 64×4=**256 bits**; EVM unavailable; failure named/no BER.
- Worker/UI: capability version+sha, explicit QAM16/RECT/QI, same-frame landing, stale/config change clear, stop/cancel, no CNN/TX-mode substitution.

## 3 Existing ARM result (reused, not rerun)

`arm/compare.json`: status PASS; input SHA `1e12d23afcdcfe4d69369ecc45853427081a6898d8e24cd708a9b9a2203dd0b3`; host/ARM **0/256**, valid127, align252, start63, sync true, CFO12000, timing7; measurement field diffs empty; `bit_preview_exact=true`; symbol preview max abs diff **8.8818e-16**; only local `capture_id` label differs.
- ARM actual candidate tgz SHA `181bf027…`; 62/62 software SHA; `verify_release` → `cd76818a…`.
- ARM perf (5 runs, 2 warmup): median **655.36 ms**, min634.52/max719.81; host median129.52/P95185.82 (separate; not ARM). No worker/UI process on ARM; cancel is covered by offline integration tests.
- Post readback `VERIFIED_SAFE_EXPECTED`; current/active remains bedf98c7; no install/Overlay/init.

## 4 Executable acceptance command (host; actual product entry)

```powershell
python -B evidence/rf-teaching/qam16-rect-product-2026-10-24/bundle/trial/trial/acceptance.py `
  --release evidence/rf-teaching/qam16-rect-product-2026-10-24/candidate/release `
  --candidate-tgz evidence/rf-teaching/qam16-rect-product-2026-10-24/bundle/trial/candidate.tgz `
  --candidate-sha 181bf027a321da6d4ceadd664a7ea24fdaa1660918a72a908deb35e8fb2a4816 `
  --expected-release cd76818afc3bee7c610678574e892b26 `
  --contract cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 --expected-software 62 `
  --frame-dir evidence/rf-teaching/qam16-rect-product-2026-10-24/bundle/trial/trial/frames `
  --output <new-host-acceptance.json> --tag lead-host
```
Actual last run: `TRIAL_ACCEPTANCE_PASSED`, passed=true, 19 boolean checks true; QAM0/256, mismatch/domain/capability/worker/UI/stale/EVM/compat/modrec4. Bundle tests 6; QAM tests 105.

## 5 Planned board trial (NOT executed; needs user authorization)

Variables:
```sh
TRIAL_ID=rf-qam16-cd76818a
STAGE=/var/lib/rf-deployment-trials/$TRIAL_ID/stage
BUNDLE=<LOCAL_PATH>
```
1. Fresh parent read-only pre: current/active=`bedf98c7…`, boot, services/NRestarts, storage, TX/RX idle, no residual jobs.
2. Upload/extract `trial-stage.tgz` to `$STAGE`; `ln -sfn "$STAGE" /tmp/rf-teaching-install-$TRIAL_ID`; install unit 0644 root:root, `daemon-reload`.
3. Preflight:
```sh
sudo python3 -B /tmp/rf-teaching-install-$TRIAL_ID/dispatch_preflight.py \
  --stage /tmp/rf-teaching-install-$TRIAL_ID \
  --parent-manifest /home/xilinx/overlays/awg_merged/releases/bedf98c77283002c632dd7f345abaff9/merged_release_manifest.json
```
4. One dispatch only: `sudo systemctl start rf-qam16-trial-cd76818a.service`; no retry; lost SSH only observe same unit.
5. Success: `TRIAL_ACCEPTED release=cd76818…`; current/active candidate; trial-acceptance passed; both services active/NRestarts0; no storage error; independent post `rf_environment_readonly_check.py --phase post --candidate <local candidate> --expect-boot-id <preflight boot_id>`; boot same; TX/RX idle.
6. Failure: existing transaction restores parent; `TRIAL_ROLLED_BACK`; independent `rf_environment_readonly_check.py --candidate <local parent> --rollback-state "$STAGE/trial-rollback-state.json" --rollback-candidate <local parent>`; must be `VERIFIED_SAFE_ROLLBACK`. UNKNOWN/RECOVERY: observe same unit, no reinstall/redeploy.
7. Cleanup after terminal readback; do not delete evidence.

## 6 Tests

- QAM bundle tests **6 passed**; QAM product/pipeline/receiver **105 passed**; B total relevant **129 passed**; Lead acceptance **18/18** checks.
- RRC gate: `test_rf_rrc_real_session.py` **41 passed**; observation/QPSK-rootcause **138 passed**; no board/RF.
- Red: expected-software 61 exit1; gate old behavior red reproduced.

## 7 Open questions for Codex

1. Is the additive 62-member contract + stage/ARM evidence sufficient for a limited QAM16/RECT no-RF trial?
2. Any P1 in worker/UI same-frame/stale clearing, failure/EVM semantics, or bundle acceptance path?
3. Verify stage/archive/member counts (62 software vs 72 tar files), parent rollback, unit/120s transaction.
4. Should the QAM trial acceptance include a modrec4/RRC compatibility smoke in the actual board path as currently staged? (It does host; board unit runs same acceptance.)
5. RRC rootcause remains blocked: OFF has no observation; next gate fix is outside this QAM bundle. Confirm this does not block QAM trial.

**Do not report APPROVED unless a real Codex verdict is returned.**
