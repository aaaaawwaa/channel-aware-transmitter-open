# RF Touch Parameters Implementation Plan

> Execute in the existing integration worktree using executing-plans. Ownership: root edits the listed production and test files; reviewer is read-only.

**Goal:** Complete the selected touch-keypad parameter path through the production RF worker.

**Architecture:** Reuse ValueRow/Keypad and the model's parameter plan. Add a parameters subpage and forward the validated plan through the existing callback. No default parameters, extra hardware owner or direct UI socket access.

**Tech Stack:** Python 3.8-compatible PYNQ UI, numpy/Pillow offscreen rendering, pytest.

## Task 1: Failing regressions

- [x] Add `test_rf_touch_parameters.py` for touch editing, cancellation, invalid values, explicit mode/delay, clearing and confirmation/stop behavior.
- [x] Add `test_rf_control_background.py` and `test_rf_control_pipeline.py` for submission, exact arguments, deadlines, complete UI callback and races; update existing contract/navigation fixtures.
- [x] Run targeted tests and save their expected failures before production edits.

## Task 2: Minimal implementation

- [x] `ui_model.py`: protocol limits, safe parameter snapshot/clear, accepted edit cancels pending confirmation.
- [x] `ui_view.py`: RF parameters tab/rows, blank keypad draft, refresh signature, shared controls and local error feedback; disable editing while control runs.
- [x] `pynq_status.py`: forward complete init/configure args; missing args are locally rejected; consume an atomic worker frame.
- [x] `rf_status_worker.py`: background control, copied parameters, at most one queued STOP, ordered poll publication, completion-only state advancement and shutdown joins.
- [x] `rf_status_client.py`: independent 20s init/configure deadline and serialized network I/O, retain 0.75s poll/short-control deadline.
- [x] Run new tests and related suites: PYNQ579/1 skip, RF+host845/1 skip.

## Task 3: Render and production verification

- [x] Render blank/filled/keypad/error/confirmation views and inspect them; inspect board fonts in downloaded memory renders.
- [x] Read-only review and re-review; resolve three concrete findings with red/green regressions.
- [x] Reproduce baseline init timeout0.7517s; new release init0.892s/configure0.324s, finally disarmed.
- [x] Repackage software only; bit/HWH/daemon/installer unchanged; atomic deployment with sole Overlay owner.
- [x] Release `ff96b755880df100c932d753398d3c00` UI/model/client path PASS, complete software audit PASS, final unarmed OFFLINE.

## Task 4: Handoff

- [x] Save evidence and `docs/reports/2026-09-30-rf-touch-parameters.md` with exact release/source/test boundaries.
- [x] Append §47 to integration release record, ledger and board handover, preserving prior records.
- [x] Keep unrelated working-tree changes intact; no batch commit or cleanup of historical backlog.
