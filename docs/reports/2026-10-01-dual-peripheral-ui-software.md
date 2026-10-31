# 2026-10-01 Dual Peripheral UI Software Release Note (Candidate)

This is an offline software inventory/release note for the dual-peripheral UI implementation.
It is NOT a hardware acceptance or deployment record.

## Source

- Source commit under evaluation: 29cc4e8 (29cc4e8)
- Branch: codex/dual-peripheral-ui
- Baseline for A/B/C diff: acadbdc5c877852153860e23537fcef17d9d5f75

## Offline regression

Command: python -m pytest -p no:cacheprovider pynq_overlay/sw/tests -q
Result: 697 passed, 1 skipped in 21.30s
Exit code: 0
Log: evidence/2026-10-01-ui-implementation/test-run.txt
Report: evidence/2026-10-01-ui-implementation/test-report.md

## NOT RUN hardware items

- Physical PYNQ boot / PL overlay load
- RF daemon binary execution on target
- ACM2108 or AD9361 hardware transmit
- TX-to-RX loopback
- On-device touch latency / visual acceptance
- Vivado/RTL rebuild or bitstream/HWH identity change

## New/modified implementation files (acadbdc..29cc4e8)

A docs/reference/dual-peripheral-ui-capabilities.md
A docs/superpowers/plans/2026-10-01-dual-peripheral-ui.md
M docs/superpowers/specs/2026-10-01-rf-awg-ui-continuation-design.md
A docs/superpowers/specs/2026-10-01-rf-modulation-impairment-design.md
M pynq_overlay/sw/pynq_status.py
M pynq_overlay/sw/pynq_touch.py
A pynq_overlay/sw/rf_iq_signal.py
M pynq_overlay/sw/rf_tx_waveform.py
A pynq_overlay/sw/tests/test_dual_backend_routing.py
A pynq_overlay/sw/tests/test_dual_peripheral_ui.py
M pynq_overlay/sw/tests/test_pynq_touch.py
A pynq_overlay/sw/tests/test_rf_awg_alignment.py
M pynq_overlay/sw/tests/test_rf_control_pipeline.py
A pynq_overlay/sw/tests/test_rf_iq_signal.py
M pynq_overlay/sw/tests/test_rf_touch_parameters.py
M pynq_overlay/sw/tests/test_rf_touch_view.py
M pynq_overlay/sw/tests/test_rf_tx_transport.py
A pynq_overlay/sw/tests/test_rf_waveform_quality.py
A pynq_overlay/sw/tests/test_touch_latency.py
M pynq_overlay/sw/tests/test_ui_model.py
A pynq_overlay/sw/tests/test_ui_units.py
M pynq_overlay/sw/tests/test_ui_view.py
M pynq_overlay/sw/ui_model.py
A pynq_overlay/sw/ui_units.py
M pynq_overlay/sw/ui_view.py

## Chunk D offline delivery artifacts (added after 61aff43; regenerated at 29cc4e8)

evidence/2026-10-01-ui-implementation/generate_screenshots.py
evidence/2026-10-01-ui-implementation/acm2108_tx_awg.png
evidence/2026-10-01-ui-implementation/ad9361_rf_parameters.png
evidence/2026-10-01-ui-implementation/ad9361_rf_transmit_pulse.png
evidence/2026-10-01-ui-implementation/ad9361_rf_transmit_dual_tone.png
evidence/2026-10-01-ui-implementation/ad9361_rf_transmit_chirp.png
evidence/2026-10-01-ui-implementation/digital_modulation_ad9361.png
evidence/2026-10-01-ui-implementation/channel_impairment_ad9361.png
evidence/2026-10-01-ui-implementation/units_keyboard.png
evidence/2026-10-01-ui-implementation/running_digital_modulation_acm.png
evidence/2026-10-01-ui-implementation/running_ad9361_tx.png
evidence/2026-10-01-ui-implementation/error_ad9361_control.png
evidence/2026-10-01-ui-implementation/confirm_ad9361_start.png
evidence/2026-10-01-ui-implementation/screenshots-manifest.json
evidence/2026-10-01-ui-implementation/screenshots-manifest.txt
evidence/2026-10-01-ui-implementation/test-run.txt
evidence/2026-10-01-ui-implementation/test-report.md
docs/reference/dual-peripheral-ui-capabilities.md
docs/reports/2026-10-01-dual-peripheral-ui-software.md
docs/superpowers/plans/2026-10-01-dual-peripheral-ui.md

## Known offline issue

The AD9361 digital modulation screenshot check initially found a 5 pixel overlap between the peak-code row and the load-modulation button. That was fixed by moving dig_amp_row from ry+rh-52 to ry+rh-60; after rerunning the generator, all 12 programmatic screenshot checks pass (see screenshots-manifest.json). On-device touch acceptance is still a separate hardware item.

## Commit

This release note is prepared for source commit 5e9d813. The independent-review fix commit is 5e9d813689b06306d1e15df82578c29ce1509ef5; this documentation/evidence update is committed on top of it on codex/dual-peripheral-ui.

Note: `docs/reports/2026-10-01-main-integration-report.md` is not present in the candidate tree, so it was not updated here; the main-project integration report update is left to the lead after syncing candidate changes.

## Deployment record (background subagent report)

> 本段为历史报告。新鲜板端日志证实该 release 缺少 rf_iq_signal.py，虽服务 active 但完整 UI 未启动。已重建并部署 622838c506a8359f8d91423d9169a92f，实际 panel 启动与帧缓冲核验通过；详见 [本轮修复报告](2026-10-01-session-repair.md)。
- VM: <REMOTE_USER>@<REMOTE_HOST> (fpga-runner)
- Board: 192.0.2.10 (pynq/xilinx)
- Release ID: 45e75640323a61ed4df15545ad92b1b0
- Board services after deploy: pynq-status active, rf-daemon active
- Software hashes on board match local candidate: ui_model.py 8158EBC5..., ui_view.py 078042EF..., install.sh 886EF55D...
- rf_deploy result: PASS
- Physical loopback/spectrum/touch acceptance: still NOT RUN
