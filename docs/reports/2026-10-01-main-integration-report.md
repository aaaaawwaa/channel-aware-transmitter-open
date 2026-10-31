# 2026-10-01 Main Integration Report

> 后续板端核查已发现本记录的融合并未覆盖完整交付链：release 45e756 的 UI 因缺少 rf_iq_signal/ui_units 降级为状态页。2026-10-01 本轮已修复清单、安装/启动验证并部署 622838c506a8359f8d91423d9169a92f，712 项新鲜回归通过。详见 [session 修复与融合审核](2026-10-01-session-repair.md)。以下为历史整合记录，不作为当前 UI 或物理验收证据。

## Source and target
- Candidate repo: `ui_candidate`, branch `codex/dual-peripheral-ui`, commit `61aff43`
- Candidate baseline used for three-way comparison: `acadbdc`
- Main project: `<LOCAL_PATH>`, branch `master`, HEAD `f676cf6`
- Existing uncommitted main-project changes were left in place except where intentionally merged below.

## Approach
- Inventoried candidate files new/modified relative to `acadbdc` and relative to the main working tree.
- Did **not** copy the candidate tree wholesale.
- For overlapping software files, compared baseline `acadbdc`, main working tree, and candidate HEAD.
- Candidate HEAD was found to be a superset of the main working tree for the merged software files; the only main-only lines were older versions superseded by candidate implementations, so no main fix was lost.
- Missing files required by the A/B/C feature set and targeted tests were copied from candidate.
- Main deploy files (`install.sh`, `pynq-status.service`) and unrelated dirty files were intentionally left untouched.

## Files updated/added in main

### Overwritten from candidate (tracked files)
- `pynq_overlay/sw/pynq_status.py`
- `pynq_overlay/sw/pynq_touch.py`
- `pynq_overlay/sw/ui_model.py`
- `pynq_overlay/sw/ui_view.py`
- `pynq_overlay/sw/tests/test_pynq_touch.py`
- `pynq_overlay/sw/tests/test_ui_model.py`
- `pynq_overlay/sw/tests/test_ui_view.py`

### Added from candidate (new files)
- `pynq_overlay/sw/ui_units.py`
- `pynq_overlay/sw/rf_iq_signal.py`
- `pynq_overlay/sw/rf_tx_waveform.py`
- `pynq_overlay/sw/rf_status_worker.py`
- `pynq_overlay/sw/rf_status_client.py`
- `pynq_overlay/sw/merged_release.py`
- `pynq_overlay/sw/_prove_install_contract.py`
- Tests:
  - `pynq_overlay/sw/tests/test_ui_units.py`
  - `pynq_overlay/sw/tests/test_dual_peripheral_ui.py`
  - `pynq_overlay/sw/tests/test_touch_latency.py`
  - `pynq_overlay/sw/tests/test_rf_awg_alignment.py`
  - `pynq_overlay/sw/tests/test_rf_waveform_quality.py`
  - `pynq_overlay/sw/tests/test_rf_touch_model.py`
  - `pynq_overlay/sw/tests/test_rf_tx_transport.py`
  - `pynq_overlay/sw/tests/test_rf_iq_signal.py`
  - `pynq_overlay/sw/tests/test_dual_backend_routing.py`
  - `pynq_overlay/sw/tests/test_rf_control_pipeline.py`
  - `pynq_overlay/sw/tests/test_rf_touch_parameters.py`
  - `pynq_overlay/sw/tests/test_rf_touch_view.py`
  - `pynq_overlay/sw/tests/test_rf_control_background.py`
  - `pynq_overlay/sw/tests/test_rf_status_client.py`
  - `pynq_overlay/sw/tests/test_rf_status_worker.py`

### Docs
- `docs/superpowers/plans/2026-10-01-dual-peripheral-ui.md` updated to candidate progress.
- `docs/superpowers/specs/2026-10-01-rf-awg-ui-continuation-design.md`, `docs/superpowers/specs/2026-10-01-rf-modulation-impairment-design.md`, and `docs/reference/dual-peripheral-ui-capabilities.md` were already present and identical to candidate; no write needed.

## Three-way merge decisions
- `pynq_status.py`, `pynq_touch.py`, `ui_model.py`, `ui_view.py`, `test_pynq_touch.py`, `test_ui_model.py`, `test_ui_view.py`: candidate HEAD was used as merged result after verifying it contains the main working tree's relevant fixes. Main-only removed lines were obsolete versions replaced by candidate implementations.
- New files absent from main: copied from candidate.
- Existing main modifications to `install.sh`, `pynq-status.service`, `test_pynq_status.py`, and all unrelated dirty files were **not** overwritten.

## Out-of-scope / conflict risk
- `pynq_overlay/sw/install.sh` and `pynq_overlay/sw/pynq-status.service` remain at their previous main working-tree state. Candidate has newer release/install semantics that `merged_release.py` and `pynq_status.py` expect on the board, but those deployment changes were not part of the requested A/B/C source integration. This is the main item needing human review before a board deployment.
- `test_merged_install_contract.py` was initially copied from candidate but was removed again because it tests the unmerged `install.sh`/`pynq-status.service` contract. It is not part of the requested targeted tests.

## Test results in main project
Targeted commands (all passed):
1. `python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_ui_units.py pynq_overlay/sw/tests/test_dual_peripheral_ui.py pynq_overlay/sw/tests/test_touch_latency.py -q`
   - **31 passed**
2. `python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_rf_awg_alignment.py pynq_overlay/sw/tests/test_rf_waveform_quality.py pynq_overlay/sw/tests/test_rf_touch_model.py pynq_overlay/sw/tests/test_rf_tx_transport.py -q`
   - **48 passed**
3. `python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_rf_iq_signal.py pynq_overlay/sw/tests/test_dual_backend_routing.py -q`
   - **19 passed**

Additional offline checks:
- Modified overlapping tests plus RF touch/control tests: **305 passed**
- Full `pynq_overlay/sw/tests` after removing out-of-scope install-contract test: **667 passed**
- No board/PYNQ live tests were run.

## Main project status summary
- Git repo: yes, branch `master`, HEAD `f676cf6`.
- Changes are left **uncommitted**.
- New files from this integration are untracked as expected in the dirty main tree.
- Modified tracked files in the software area now include the candidate A/B/C versions.
- `install.sh`, `pynq-status.service`, and `test_pynq_status.py` remain dirty from previous main work and were intentionally not changed by this integration.
