# Codex key-stage review entry — QAM64/RECT candidate

Status: **READY_FOR_ONE_CODEX_REVIEW / NOT_INSTALLED / NOT_RF**. Review only; no install/board/RF/file changes.

## Absolute paths

- Candidate root: `<LOCAL_PATH>`
- Release: `...\qam64-rect-product-2026-10-25\candidate\release`
- Archive: `...\qam64-rect-product-2026-10-25\candidate.tgz`
- Receipt/diff/report: `candidate-receipt.json`, `minimal-diff.json`, `REPORT.md`
- ARM: `<LOCAL_PATH>`
- Spec: `docs/specs/2026-10-25-qam64-rect-offline-spec.md`
- QAM16 baseline: current installed `cd76818a`; real report `evidence/rf-teaching/qam16-rect-real-rx-2026-10-25/`.

| identity | value |
|---|---|
| candidate | **`4ea1c0a68f03773fe1a77d1876ab1548`** |
| parent/fallback | `cd76818afc3bee7c610678574e892b26` |
| contract | **`cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1`** |
| software | **64** |
| candidate.tgz | **`5dd1b2eeb15befbffaf0e181fbf53ad19b374245ddb2457e3270501ba44ddb91`**, 2,050,991 B |
| archive | **138** members: release/74 + pynq_overlay/sdk/rf_overlay |
| parent archive | layout2 `440ba377…` full layout (actual cd76818a) |

## Delta

`minimal-diff.json`: changed = install.sh, merged_release.py, rf_status_worker.py, ui_model.py, rf_qam64_receiver.py, rf_qam64_pipeline.py; bit/HWH/daemon/six-class/modrec4 unchanged; old 55/58/60/62 maps unchanged.
QAM64: TX map I b0b1b2 / Q b3b4b5 -> `(7,5,1,3,-7,-5,-1,-3)`; valid127/align378/compare_start63/compared384; sync from alignment only; EVM unavailable; full-frame mean removed after B found partial-comparison red; errors now exact.

## Tests

- QAM64 receiver/reference: 124 passed; QAM64 product seam: 13 passed; Lead QAM64/QAM16/runner combined: **163 passed in 28.50 s**.
- QAM16 plan `capture_deadline=10.0` now passed explicitly; default session remains 15.0; timeout/cleanup tests pass.
- ARM actual candidate bytes: 0/384, valid127/align378/start63, EVM unavailable; 18 fields host==ARM; median 2.554862 s (recorded only).
- No board install/RF.

## Next trial command (not executed; requires user authorization and parent pre-readback)

Use only this candidate archive; **not** bundle-v2/old layout packages.

```sh
TRIAL_ID=rf-qam64-4ea1c0a
STAGE=/var/lib/rf-deployment-trials/$TRIAL_ID/stage
# upload actual candidate.tgz (5dd1b2…), parent full archive (440ba377…), installer/wrapper, QAM64 fixture acceptance
sudo install -d -m 700 "$STAGE"
sudo tar -xzf trial-stage.tgz -C "$STAGE"
sudo ln -sfn "$STAGE" /tmp/rf-teaching-install-$TRIAL_ID
sudo install -m 644 "$STAGE/<unit>.service.in" /etc/systemd/system/<unit>.service
sudo systemctl daemon-reload
sudo python3 -B "$STAGE/dispatch_preflight.py" --stage "$STAGE" \
  --parent-manifest /home/xilinx/overlays/awg_merged/releases/cd76818afc3bee7c610678574e892b26/merged_release_manifest.json
# only after full preflight, exactly once:
sudo systemctl start <unit>.service
```

Trial stage/unit is **not yet built** (QAM64 fixture acceptance and unit must be prepared after Codex review); do not claim ready. Success: TRIAL_ACCEPTED, QAM64 acceptance passed, current/active=4ea1c0a, services/RF safe, independent post. Failure: existing transaction restores cd76818a; independent rollback readback with downloaded local JSON. SSH loss: observe only.

## Open questions

1. Is 64-member additive contract + full-layout archive + ARM parity sufficient to proceed to a limited no-RF trial after the remaining trial-stage/QAM64 fixture work?
2. Any P1 in worker/UI QAM64 capability/landing/invalidation or archive full-layout dependencies?
3. Confirm no reuse of old QAM16/layout2 approval for this new candidate.
4. Confirm QAM64 real-RX runner remains a separate subsequent task; no physical claim here.
