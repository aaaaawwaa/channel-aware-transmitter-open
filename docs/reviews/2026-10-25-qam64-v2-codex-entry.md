# Codex incremental review entry — QAM64 v2 alignment-window correction

Status: **ONE INCREMENTAL REVIEW ONLY / NOT INSTALLED / NOT RF**. No Codex CLI invocation is requested by this file; provide a verdict in the current Codex conversation if desired. This is not an approval.

## Candidate bytes

- candidate: **`024fb7b130dc756f648a655560ef740f`**
- parent/fallback: **`4ea1c0a68f03773fe1a77d1876ab1548`** (current installed)
- contract: `cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1` (64 software)
- candidate.tgz: **`3725382f98c37a3cb2e914f97d5d45575579efc1deb7092996ad0573dd06dd5e`**, 2,053,444 B, 138 full-layout members
- release: `evidence/rf-teaching/qam64-rect-product-v2-2026-10-25/candidate/release`
- parent archive: current `qam64-rect-product-2026-10-25/candidate.tgz` SHA `5dd1b2…` (full layout); no old cd76818a/layout2 builder used.

## Delta only (parent → candidate)

- `rf_qam64_receiver.py` SHA `5824cf303fe25337eaa551750aca6829f6f7a3f800a31fad5573e2196820ef54`; `VERSION = qam64-rect-snapshot-v2`.
- `rf_modem_result.py` SHA `f9fb9ef5bb15f6b79a43dba386e00a7b83813f7ead60d59a91f5374ce419f0c9`; adds named `ILL_CONDITIONED`.
- `ui_model.py` accepts qam64 v2 and remains strict on version/SHA/release.
- bit/HWH/daemon/six-class/modrec4 unchanged; old v1 maps/artifacts retained.

## Algorithm behavior

- only alignment symbols `[0,62)` fit model; `[63,127)` is only counted (384 bits);
- 2×2 real I/Q affine + DC LS, condition ≤2, linear ≤8, DC norm ≤1024, finite; otherwise scalar;
- affine requires ≥1% alignment RMSE improvement over scalar; fine CFO ±2 kHz/25 Hz + 0, adopted only on ≥1% alignment RMSE improvement; no comparison/BER selection and no fixed A/B compensation;
- comparison-region damage leaves selected kind/CFO/RMSE/condition/linear/DC unchanged (A probe and B tests);
- named failures stay no-BER; EVM unavailable; no RRC/training/RTL.

## Results

- A/B historical IQ v1 2/384, 5/384 → v2 **0/384, 0/384** (offline re-decode, not fresh VAL).
- B independent synthetic: old 40/5 red → v2 **45/45 green**; coverage: axis gain/cross, DC, non-grid CFO 777/1234/4321/−2345 Hz, phase, timing 0..19, noise, combined, pathological, comparison damage (128/114 exact).
- Integrated: **213 passed**; A focused 175; B 45; runner 25.
- ARM actual v2 bytes: import v2, fixed input 0/384, 18 host/ARM fields equal; 5-run timing min 3.013794 / median 3.097749 / max 3.471878 s; temp cleaned; current remains 4ea; no RF/install/service/Overlay/commit.

## Trial stage

- trial `rf-qam64-024fb7b`; unit `rf-qam64-trial-024fb7b.service`; stage `/var/lib/rf-deployment-trials/rf-qam64-024fb7b/stage`.
- stage index `bdc2c218c39e448341e6548e4ab534c4b07e9555ac9dbb7c2f8d6056d343405c` (self-excluded 16 files); trial-stage.tgz `f320e53d73c04ea260fd3ea97f89dfdc7010ef97fea23496b14b1c5b49d65f03` (17 members).
- host preflight **9/9 PASS**; host acceptance **22/22 PASS**; parent is current 4ea; no install/board/RF.

## Review questions

1. Any P1 in the v2 model selection/fit or version/UI gating before a separately authorized no-RF trial?
2. Is the parent/current 4ea and self-excluded stage index correct for a future rollback?
3. Confirm A/B are historical regression not fresh validation; no retry/recapture claim.
4. Confirm EVM/RRC/model/RTL remain out of scope.

Evidence: `evidence/rf-teaching/qam64-rect-product-v2-2026-10-25/REPORT.md`, `ab-replay.json`, `minimal-diff.json`, `candidate-receipt.json`, `host-acceptance.json`, `trial-stage.tgz`; A `qam64-alignment-v2-2026-10-25`; B `qam64-impairments-2026-10-25`; ARM `qam64-arm-v2-2026-10-25`.
