# Codex entry — QAM16 bundle-v2 layout fix

Status: **READY_FOR_CODEX_INCREMENTAL_REVIEW / NOT_DEPLOYED / NOT_RF**. Review only; do not install, connect board, init RF or modify files.

## 1 Absolute paths and new digests

Workspace: `<LOCAL_PATH>`

- bundle-v2 root: `<LOCAL_PATH>`
- outer archive: `...\bundle-v2\candidate.tgz`
- stage archive: `...\bundle-v2\trial-stage.tgz`
- stage tree: `...\bundle-v2\trial`
- report: `...\bundle-v2\REPORT.md`
- receipt: `...\bundle-v2\bundle-receipt.json`
- old failed package (preserved): `...\bundle\trial\candidate.tgz` (72 files all release/)
- A raw recovery: `...\trial-execution-2026-10-25\recovery-v2-2026-10-25\`
- Lead review: `docs/reviews/2026-10-25-qam16-bundle-v2-layout-review.md`

| item | value |
|---|---|
| candidate release id | `cd76818afc3bee7c610678574e892b26` (release bytes unchanged) |
| parent | `bedf98c77283002c632dd7f345abaff9` |
| contract | `cnn-bpsk-modrec4-qpsk-rrc-qam16-v1` (62 software) |
| new outer candidate.tgz | **`2437b44acd4d285ca5d9c451c0baeceea998011478fff7abd4675962f23412e9`**, 2,047,691 B |
| outer layout | 138 files: release/72 + pynq_overlay/sdk/rf_overlay 66 |
| new trial-stage.tgz | **`9a6fb48040b86a564f1374f61782a5bd29ab1d9dfb950eea55f56fc6797ea55b`**, 4,105,290 B |
| parent archive | `f17b53d8b0f8e16fbeb04f6714f941d4fcc3e043b703eb41bab78502ce4f95ac` |
| old outer (blocked) | `181bf027a321da6d4ceadd664a7ea24fdaa1660918a72a908deb35e8fb2a4816` |

## 2 Corrected failure fact

Raw journal confirms:
```text
INSTALL BEGIN: stopping existing PL owner and sidecar
sh: 0: Can't open /tmp/rf-teaching-install-rf-qam16-cd76818a/unpacked/pynq_overlay/sw/install.sh
INSTALL FAILED: restoring previous release and service units
```
- installation entry failure **CONFIRMED**; old outer archive lacked `pynq_overlay/sw/install.sh` and dependencies (72 files all release/).
- current after cross-reboot = parent `bedf98c7`; candidate release dir absent; unit inactive/dead, no terminal state.
- rollback terminal **UNKNOWN/NOT_PROVEN**; acceptance **NOT RUN/UNKNOWN**; board hang/restart mechanism **UNKNOWN**.

## 3 Fix delta

- `build_bundle_v2.py` constructs full parent layout from frozen candidate/parent bytes; candidate-owned files only from release, external files only from frozen parent (no worktree).
- `pynq_overlay/sw/install.sh` is actual entry; QAM modules under `pynq_overlay/sw/`.
- preflight-v2 checks entry/layout/deps/digest before any service stop; old 72 package rejected 8/8 false for layout/entry/digest.
- stage wrapper runs preflight before reviewed inner transaction; `trial-phase.log`/tee/set -x persists phases; `host_tail_phase_log.py` for incremental host capture.
- Isolated WSL sandbox: actual installer executed; fake `systemctl` only; installed 72 release files; current points candidate; verify release/panel passes; fake-systemctl log has no stop.

## 4 Tests

- `PYTHONPATH=scripts;pynq_overlay/sw python -B -m pytest .../bundle-v2/test_bundle_v2.py -q` → **5 passed** (Lead rerun).
- New preflight JSON 8/8 true; old preflight JSON failure as above.
- Prior QAM 111/product tests and ARM evidence unchanged; release bytes unchanged; no rerun.
- No board/RF/install.

## 5 Exact next board command (only after Codex review + new user authorization)

**Do not run now.** Preconditions: new fresh parent pre (current/active=bedf, boot/services/storage/RF idle, no residual jobs), upload **new** stage `9a6fb480…`, no old package.

```sh
TRIAL_ID=rf-qam16-cd76818a
STAGE=/var/lib/rf-deployment-trials/$TRIAL_ID/stage
sudo install -d -m 700 "$STAGE"
sudo tar -xzf trial-stage.tgz -C "$STAGE"
sudo ln -sfn "$STAGE" /tmp/rf-teaching-install-$TRIAL_ID
sudo install -m 644 "$STAGE/rf-qam16-cd76818a.service.in" \
  /etc/systemd/system/rf-qam16-trial-cd76818a.service
sudo systemctl daemon-reload
sudo python3 -B /tmp/rf-teaching-install-$TRIAL_ID/dispatch_preflight.py \
  --stage /tmp/rf-teaching-install-$TRIAL_ID \
  --parent-manifest /home/xilinx/overlays/awg_merged/releases/bedf98c77283002c632dd7f345abaff9/merged_release_manifest.json
# must be 8/8 installed, then exactly once:
sudo systemctl start rf-qam16-trial-cd76818a.service
```
One start only; no retry. On SSH loss, observe same unit; do not reinstall/parallel rollback; download `trial-phase.log`/journal/state first. Success: TRIAL_ACCEPTED + acceptance + current/active candidate + independent post safe. Failure: existing transaction parent recovery + independent rollback readback; UNKNOWN/RECOVERY observe only.

## 6 Open questions for Codex

1. Does full outer layout + preflight + isolated actual installer adequately close the 72-file packaging defect?
2. Any P1 in new `candidate.tgz` mapping (candidate-owned vs external parent bytes) or stage/wrapper hashes?
3. Is persistent phase logging sufficient to identify where the previous install/rollback stopped without guessing hardware?
4. Confirm old package must remain blocked and old approval is not reusable; new trial needs fresh authorization.
