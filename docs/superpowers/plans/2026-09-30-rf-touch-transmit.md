# RF Touch Transmit Implementation Plan

> **For agentic workers:** REQUIRED: Use $subagent-driven-development or $executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Implement the user-approved RF dual TX tone load/start/stop flow on the existing touch panel, with actual telemetry and retained connection.

**Architecture:** Existing single daemon remains unchanged. Client holds a socket from load until stop/disarm/expiry/error; worker synthesizes explicit 1024-point I-only test tone and uses binary load. Pure model gates actions and derives transmitting status from fresh TX_ACTIVE and PL source status. View exposes tone settings separately from RF hardware parameters.

**Tech Stack:** Python3/PYNQ/Paramiko/PyVISA, existing C daemon protocol. Spec docs/superpowers/specs/2026-09-30-rf-touch-transmit-design.md approved by user 2026-09-30, commit423f850.

## Chunk 1: Transport and waveform

- [x] Add failing real framing/fake-server tests for tx.load binary and load→poll→start on one connection, stop and expiry releasing connection, new explicit arm after expiry. Validate waveform types/ranges and metadata.
- [x] Implement pynq_overlay/sw/rf_tx_waveform.py: explicit cycles1..511 and amplitude1..2047 → 4096 bytes of little-endian I/Q, N1024, Q0, wrap=true.
- [x] Extend rf_status_client.py payload framing and retained session, ID/buffer ownership under existing _io_lock, no blind retry start. Maintain short connections until load. Close on stop/disarm/expiry/error/worker exit.
- [x] Extend rf_status_worker.py with load/start actions; load waveform in background. Return native result as evidence and retain atomic publication ordering.
- [x] Publish immutable local `tx_session_ready` evidence in each snapshot (only successful load with still-valid held socket). Close/error/expiry makes false, even if reconnect immediately returns fresh READY+armed. Model clears loaded on false; load result cannot restore loaded against false/unknown telemetry. Test disconnect→fresh READY still requires new load before start.
- [x] Run targeted and existing client/worker RF tests; investigate failures rather than weakening gates.

## Chunk 2: Pure state and touch interface

- [x] Add failing model tests for loaded gating, edits invalidate loaded, confirmation required, actual TX state/freshness, STOP in flight and expiry reset.
- [x] Extend ui_model.py actions load/start, explicit tx_cycles/tx_amplitude draft fields, freeze RF applied sampling rate on configure success, invalidate loaded on RF/tone edits, stop/disarm/offline. Running derives from actual telemetry only.
- [x] Extend ui_view.py with tone subpage/rows plus load/start controls; keep STOP reachable and no network on touch thread. pynq_status.py continues generic model-plan→worker submit path.
- [x] Render800×480 overview/tone/confirmation/transmitting/stopped states, inspect overlaps and glyphs, run UI regression.

## Chunk 3: Product-path and board validation

- [x] Run all pynq_overlay/sw/tests, rf_overlay/sw/tests and relevant native C contract suites; obtain independent code review.
- [x] Upload Python source to reviewed staging location, verify hashes/backup, install through existing release-safe mechanism and restart only the authorized panel owner when needed. Never construct a second Overlay().
- [x] Probe actual product client/worker/model actions on board: arm/init/configure/load→poll→start, repeated fresh TX_ACTIVE with source_selected=true, scope ON and OFF, explicit STOP/disarm; 32+sec read-only poll expiry; explicit re-arm after released session. Distinguish callback automation from human physical touch.
- [x] Update report and progress record, retain raw waveforms/screenshots/logs and final OFFLINE status. If deployment limitation occurs, report it without claiming installed code.

Ownership: transport worker owns client, worker, waveform module and its new tests only. Parent owns model/view/callback and its tests, report and hardware. Shared integration worktree; no agent reverts edits by others. Board/scope operations belong exclusively to parent.

Completed: implementation d204f05; release67865a3e41b50c048b62d52b85011901; 1457 Python passes / 2 skips, native C host-test exit0, product scope12/12, product watchdog expiry/rearm passed. Physical human touch remains NOT RUN.
