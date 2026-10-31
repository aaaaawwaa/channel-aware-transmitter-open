# 2026-10-01 Independent Offline Spec/Quality Review

Reviewer: ui-final-reviewer
Date: 2026-10-01
Candidate HEAD reviewed: d7e4f48 (codex/dual-peripheral-ui)
Main project HEAD: f676cf6 + uncommitted integrated changes (files match candidate HEAD hashes for reviewed software files)

## Verdict

**Needs fixes.** The A/B/C implementation has good pure-logic coverage and the offline regression is green, but there are unresolved UI/spec violations that prevent the final `final独立规格与质量审查` checkbox from being checked.

## What was reviewed

- Plan: `docs/superpowers/plans/2026-10-01-dual-peripheral-ui.md`
- Specs: `docs/superpowers/specs/2026-10-01-rf-awg-ui-continuation-design.md`, `docs/superpowers/specs/2026-10-01-rf-modulation-impairment-design.md`
- Source: `ui_units.py`, `rf_iq_signal.py`, `rf_tx_waveform.py`, `ui_model.py`, `ui_view.py`, `pynq_status.py`, `rf_status_worker.py`
- Targeted tests: `test_ui_units.py`, `test_dual_peripheral_ui.py`, `test_touch_latency.py`, `test_rf_awg_alignment.py`, `test_rf_waveform_quality.py`, `test_rf_iq_signal.py`, `test_dual_backend_routing.py`
- Evidence: 12 screenshots, manifests, test report, main integration report

Re-run of the focused A/B/C test set on this review:

```
python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_ui_units.py pynq_overlay/sw/tests/test_dual_peripheral_ui.py pynq_overlay/sw/tests/test_touch_latency.py pynq_overlay/sw/tests/test_rf_awg_alignment.py pynq_overlay/sw/tests/test_rf_waveform_quality.py pynq_overlay/sw/tests/test_rf_iq_signal.py pynq_overlay/sw/tests/test_dual_backend_routing.py -q
69 passed in 1.11s
```

## Findings

### F1 (High) AD9361 digital amplitude cannot be edited

- Location: `pynq_overlay/sw/ui_model.py::rf_digital_parameter_error`, `ui_view.py::_build_digital_page` (dig_amp_row).
- Problem: `rf_digital_parameter_error()` has no `"amplitude"` case, so `set_rf_digital_parameter("amplitude", 1000)` returns `"unknown RF digital parameter 'amplitude'"`. The visible `"峰值码"` row on the AD9361 digital page therefore rejects every edit. The default remains 2047 forever.
- Suggested fix: add `"amplitude"` validation (`type(value) is int and 0 <= value <= 2047`) in `rf_digital_parameter_error`, and add a UI/model test that edits amplitude through the keypad and verifies the next `load` plan carries it.
- Severity: High (visible control is non-functional).

### F2 (High) AD9361 digital symbol-rate UI uses the legacy ACM 10..1e6 range

- Location: `pynq_overlay/sw/ui_view.py::_build_digital_page` (symrate row), `ui_model.py::rf_digital_symrate_range` (unused).
- Problem: The AD9361 digital symbol-rate editor is built with `M.SYMRATE_MIN`/`M.SYMRATE_MAX` (10..1,000,000 Sym/s), but the C contract requires finite-frame support `Fs/32..Fs/2` with integer `sps=2..32`. At the documented 60 MSPS, the default draft value is 2,000,000 Sym/s, and opening/committing that field fails with `数值超出范围`. The function `rf_digital_symrate_range(fs)` exists but is not used.
- Suggested fix: when `selected_device == "AD9361"` use a keypad/field range based on applied `sample_rate_hz`: `(fs // 32, fs // 2)` and enforce integer divisibility/`sps` in the model/editor. Show the finite-frame range and actual frame length in the UI.
- Severity: High (the main AD9361 digital symbol-rate path is not operable through the UI for its valid/default range).

### F3 (Medium) AD9361 digital/channel pages display a contradictory placeholder

- Location: ui_view.py::_draw_page AD9361 branch.
- Problem: When AD9361 is selected on digital/channel pages the view draws "AD9361 软件 IQ 工具尚未接入" even though the controls are wired and enabled.
- Suggested fix: remove or replace the placeholder with real page labels/status.
- Severity: Medium.

### F4 (Medium) RF waveform selection is not the required readable-name button set

- Location: ui_view.py::_build_awg_page (rf_wave_row), ui_model.py::rf_waveform_fields.
- Problem: The spec calls for readable waveform name buttons, but the implementation uses one ValueRow that displays the Chinese name and opens a numeric 1..9 keypad when tapped.
- Suggested fix: add a row/set of buttons for all 9 RF waveform names in the transmit subpage, and add touch tests that select each named waveform and verify the correct field set is shown.
- Severity: Medium.

### F5 (Medium) Evidence/docs source-commit inaccuracies

- Location: evidence screenshots-manifest.json/txt, docs/reports/2026-10-01-main-integration-report.md, docs/reports/2026-10-01-dual-peripheral-ui-software.md.
- Problem: Screenshot manifests still say commit_source 61aff43 while final test-report says eace8e8 and HEAD is d7e4f48; main integration report also references 61aff43 and 667 passed vs candidate 684 passed.
- Suggested fix: regenerate/update manifests and reports to final reviewed commit, and clarify the 667 vs 684 difference is due to intentionally removed install-contract test.
- Severity: Medium.

### F6 (Low/Medium) C-contract test gaps

- Location: pynq_overlay/sw/tests/test_rf_iq_signal.py, rf_iq_signal.py.
- Problem: The C spec asks for direct verification of RRC frequency-domain suppression/periodic convolution, RECT symbol-period behavior, and little-endian payload contents, but the current tests do not explicitly verify those.
- Suggested fix: add targeted tests for RRC output spectrum/periodic convolution reference, RECT pulse shape in a non-zero frame, byte-level little-endian packing, and UI Fs-dependent symbol-rate range (overlaps F2).
- Severity: Low/Medium.

### F7 (Low) Legacy advanced field list shows tx_cycles for noise

- Location: ui_model.py::rf_waveform_fields, rf_tx_waveform.py::build_noise.
- Problem: Advanced Noise fields include tx_cycles although build_noise does not use cycles.
- Suggested fix: special-case Noise so advanced fields are amplitude+seed only.
- Severity: Low.

### F8 (Low) AD9361 channel impairment seed is not exposed in the UI

- Location: ui_view.py::_build_channel_page, ui_model.py::rf_channel_parameter_error.
- Problem: The channel draft supports noise_seed but the AD9361 channel page does not let the operator set it.
- Suggested fix: add a noise-seed field or explicitly document the fixed/default seed in the UI.
- Severity: Low.

## Overall assessment

- Chunk A: core model/routing/touch-latency behavior is substantially implemented and covered by focused tests.
- Chunk B: waveform generation fixes and model/UI field mapping are substantially implemented; the missing named-selector interaction is a spec gap.
- Chunk C: pure IQ backend and routing are implemented and tested at model/worker level; the AD9361 digital UI has high-impact editor/routing/range defects (F1, F2, F3).
- Evidence: screenshots exist and layout checks pass, but the AD9361 digital page visually contains a contradictory placeholder and the manifests/report commit references are stale (F3, F5).

Therefore the final review checkbox must remain unchecked until F1/F2 (and ideally F3/F4) are fixed and re-reviewed.

## Re-review after F1-F8 fix (HEAD 4358715 / source 5e9d813)

Re-ran focused tests and full suite on the fix branch:
`python -m pytest -p no:cacheprovider pynq_overlay/sw/tests -q` -> 697 passed, 1 skipped.
Re-ran `generate_screenshots.py` -> all 12 screenshots pass layout checks.

### Resolved

- F1: AD9361 digital amplitude validation and keypad editing are now implemented and tested.
- F2: AD9361 symbol-rate UI now uses Fs-dependent bounds and divisibility validation; default 2 MSym/s can be committed.
- F3: The placeholder on AD9361 digital/channel pages has been removed.
- F5: Evidence/manifests now point to the fix commit and record 697 passed; candidate evidence is updated.
- F6: Added RRC periodic-convolution reference, RECT symbol-repeat, little-endian payload, and Fs-range tests.
- F7: Advanced Noise no longer exposes tx_cycles.
- F8: AD9361 channel noise_seed is now exposed and editable.

### Remaining blocker

- F4/R1: The new readable RF waveform buttons are created with height 26 and font size 12 (ui_view.py rf_wave_btns). The approved UI spec requires main touch targets to be at least 40px high and Chinese text at least 14px. The named-selector requirement is functionally implemented, but the buttons do not yet meet the approved touch/font sizing requirement.

Suggested fix: increase rf_wave_btns height to >=40 and font size to >=14, using pagination or a more compact grid/paging if needed to avoid overlap.

## Updated verdict

**Still needs a minor UI fix before final approval.** F1-F3, F5-F8 are resolved; F4 is partially resolved but the waveform selector buttons remain below the approved minimum touch/font spec. The final plan checkbox should remain unchecked until this is addressed.

## Final re-review after F4/R1 fix (HEAD d7e1fd0 / source 29cc4e8)

The RF waveform name buttons are now 5 columns x 2 rows, 50x40px, font size 14. This resolves F4/R1.

Re-ran full suite: `697 passed, 1 skipped` in 19.72s.
Re-ran `generate_screenshots.py`: all 12 screenshots pass layout checks.

## Final verdict

**Approved for offline spec/quality review.** All prior findings F1-F8 and F4/R1 are resolved. The plan checkbox for final independent spec/quality review is now marked [x]. Hardware/deploy/RTL acceptance remains NOT RUN and is not claimed.
