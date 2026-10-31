# HEADROOM-1 native contract and candidate qualification

**Final status, 2026-10-11:** Development and new-candidate G1 completed. Final candidate `b67a8aaab0c5904822580c0647b96fd0`, native58/software46, ARM full computation P95 270.793ms/RSS31524KiB, host/ARM full results and eight fake-chain cases passed. The first 314.963ms failure is retained; a mathematically exact 127×63 batched alignment fixed the budget, without changing thresholds. Below checklists preserve implementation history; final evidence/status supersedes intermediate candidate IDs and review schedules. See `docs/reports/2026-10-11-headroom-contract-final.md` and `evidence/rf-teaching/s2/headroom-contract-2026-10-11/stage-checkpoint.json`. Installation and real HEADROOM remain NOT_RUN.

> **For agentic workers:** REQUIRED: Use $subagent-driven-development. Preserve the dirty shared checkout and frozen evidence. No blanket commit, reset or cleanup.

**Goal:** Implement an independently named, bounded 46 dB research transaction without changing the exact 40 dB teaching contract, then qualify a new candidate with fake hardware and saved IQ.

**Architecture:** One existing daemon/core owner enforces a once-only research session, one initialization and five attempts in the order OFF-before, normal, narrowband, pulse, OFF-after. Dedicated research operations and frame metadata keep actual RF readback, source identity and attempt identity together. The Python plan/controller/worker/client chain validates the same contract and saves each original frame before analysis or acknowledgment. Old environment classification remains INVALID/RF_DOMAIN_MISMATCH for 46 dB.

**Tech Stack:** Portable C, injected fake PL/vendor, Python 3.8, existing NDJSON/IQ framing and release tooling, ARM gcc in an isolated board /tmp directory.

**Authorization / isolation:** This executes the already approved future-contract section of `2026-10-10-s2-headroom-research-design.md`, following the user's instruction to continue until a large stage is complete. Use the current codex branch: a new Git worktree omits the untracked production modules and frozen candidate on which this work depends. Freeze the starting relevant inputs before edits. Historical HEADROOM preparation, G2, old candidate and baseline are immutable. This stage includes offline/native ARM fake tests, candidate packaging and reviews; it does not install, load Overlay, initialize or transmit real RF. A qualified candidate is a complete development stage; finite physical research is the next distinct stage.

**2026-10-11 review amendment:** The user explicitly requested fewer reviews, limited to critical parts. Keep the already completed native spec/quality verdicts; combine the remaining Python/control/evidence and qualification-tool checks into one independent `python/critical-review.md/.json` verdict. Its binding must match the frozen software map, report, fake-chain harness and runtime qualifier. Do not add repeated spec/quality rounds. Current frozen candidate is `ebad0de99120685cdc14a167b9b49911`; final host/ARM qualification and receipt determine completion, not the intermediate checklist below. Historical failures remain preserved.

## Chunk 1: Native transaction

### Task 1: Native contract, framing and evidence

Files: new `rf_overlay/sw/board_app/rf_headroom.c/.h`, optional narrowly scoped SHA256 module; modify `rf_daemon.c/.h`, `rf_core.c/.h`, `rf_version.h`, `Makefile`; tests under `board_app/test/`.

- [ ] Freeze relevant starting input hashes and run existing C regression.
- [ ] Add failing tests for the pure budget/order machine and daemon framing integration. Reject absent/incorrect research ID, cable, fixed RF, nonzero wait, arbitrary scenario/payload/declaration, prior initialized core, repeats and mixed ordinary controls. A failed accepted attempt consumes its slot and seals the study.
- [ ] Implement `headroom.begin`, `headroom.load`, `headroom.capture`, `headroom.accept`, `headroom.abort`. Exact contract `HEADROOM-1`, daemon 1.0.4 / protocol 1.6. Begin consumes its one initialization before touching hardware; fixed 80M/60M/30M/2R2T/tap29/TX46000. No restart in the process, including disarm/rearm or reconnect. Start from a safely idle OFFLINE core. Ordinary control requests are blocked during an active study except stop/disarm; stop/disarm/disconnect terminates it.
- [ ] Load validates the exact existing generator's full scenario declaration and actual payload SHA before BRAM writes. Embed reviewed fixed hashes, not caller-provided expected hashes. Keep the existing flat NDJSON parser and 1024-byte args limit: use a flat set of typed fixed source/declaration fields, with version/truth/seed/amplitude/tone/pulse/shape/formula/quantization/rates/periods checked and reconstruct the canonical declaration from those verified values. A digest by itself is not full declaration validation. OFF has no TX payload and uses the ordinary RX transaction with SRC_SEL confirmed off. Normal/interference captures use a dedicated atomic transaction, never a series of externally callable tx.start/rx.capture operations or a forged teaching approval.
- [ ] Capture consumes its attempt before hardware, confirms fixed RF and both TX attenuation getters before source/RX arm and after capture; verify PL fields and hardware_verified. Return research ID, sequence, scenario, source/declaration digests, actual RF/dual attenuation readback, request/timestamp, zero-wait timing and rail count in the same IQ header. Detect rails, stop and preserve the original IQ. Native capture/config/clock/cleanup errors terminally close both paths. Frame send failure also seals the study. Next attempt requires explicit matching acceptance of this frame; false/invalid acceptance stops. Fifth acceptance completes; no sixth attempt.
- [ ] Tests cover full five captures, no TX assertion in OFF, both getter mismatch/read failures, failed initialization/attempts, bad input/payload framing, budget/reset bypass, rails retaining IQ, cleanup while connection remains open, disconnect/stop/FAULT and old exact40000 gate. Do not replace existing assertions. Host fake hardware must be explicitly labeled simulation, never real attestation.
- [ ] Run complete C make targets and isolated effective mutations for order/budget, dual readback and payload identity. Obtain independent spec then quality review; repair before moving to Task 2.

## Chunk 2: Python integration and offline campaign

### Task 2: Research plan/control/evidence path

Files: new `pynq_overlay/sw/rf_headroom.py` (immutable plan, controller and evidence checks), targeted additions to `ui_model.py`, `rf_status_worker.py`, `rf_status_client.py`; packaging map `pynq_overlay/sw/merged_release.py`; separate runner/preparer under `scripts/` if needed. Existing capture and teaching load retain40000 unchanged.

- [ ] Add failing tests for live capability/version gate before initialization, exact typed plan, prepared declaration/payload identity, native response instead of sent args, corrupted same-frame metadata, raw IQ persistence before decision, cancellation, failure no retry, and OFF sync/points/BER stopping.
- [ ] Implement the plan from the existing generator with fixed parameters; wire UiApp plan -> controller -> worker -> client -> dedicated daemon actions on one retained connection. Never automatically arm or run research from rendering/status polling. Capability must be read from a live hello/device.info, not a hand-written manifest. No source-load against production tx.load and no second hardware owner.
- [ ] Client reads the research IQ header and payload once; validates same-frame identities/full actual RF, unique request IDs, monotonic timing, fixed metadata and hashes. Preserve failed raw IQ and native error before abort, with explicit incomplete evidence if bytes are unavailable. Do not publish research frames as current teaching captures.
- [ ] Controller serializes five attempts, persists attempt intent before sending, raw frame/header before analysis, full saved-IQ BPSK/constellation/environment replay, and terminal receipt. Native failures, rails, timeout, invalid metadata, OFF synchronization/points/BER, cancellation or closure failure stop immediately, never retry/top up. 46dB environment must remain INVALID/RF_DOMAIN_MISMATCH. Disarm in finally and record close acknowledgment; independent final safety check is required only for future real runs.
- [ ] Introduce NEW software contract `cnn-bpsk-headroom-v1`, inheriting the unchanged ENVIRONMENT map and adding `rf_headroom.py`; do not alter old contract membership. Extend runtime acceptance where the new contract inherits environment/teaching/model support. Version pair verifier explicitly adds1.0.4/minor6. Historical candidates remain valid; no baseline/model changes or automatic RF authorization. Tests for candidate version/source/map integrity and old client/UI paths.
- [ ] Run relevant Python plus C regression and independent spec then quality reviews.

## Chunk 3: G1 candidate qualification and closeout

### Task 3: Frozen artifact and ARM fake qualification

Files: evidence under `evidence/rf-teaching/s2/headroom-contract-2026-10-11/`, report `docs/reports/2026-10-11-headroom-contract.md`, updated current summary in `CLAUDE.md` and roadmap.

- [ ] Freeze all native/software inputs and relevant tests/tools, build only those sources in an isolated board /tmp directory through the approved paramiko helper. No install, Overlay/MMIO/SPI/core initialization or RF. Build ARM ELF, full fake C targets/vendor integrity; save real exit codes/logs/hashes and executable artifacts.
- [ ] Qualify exact source/ELF/version receipt, package a new candidate from the verified S1 parent, verify all candidate members/imports; old installed candidate and physical totals unchanged.
- [ ] Run candidate Python full path with fake research client and retained16KB IQ, compare host/ARM results and 46dB domain refusal; run bounded100 full calculations for resource/parity evidence and preserve input/output/log bindings. Any failed qualification is reported as incomplete, not old G1 inheritance.
- [ ] Independent final integrated review. Seal stage receipt with all evidence hashes and explicit NOT_RUN for installation/physical HEADROOM/S2formal/S3. Verify receipt once after sealing. Update stage diagram and remaining work, report the completed development/qualification stage to the user.
