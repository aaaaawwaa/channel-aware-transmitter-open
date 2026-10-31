# QAM16/RECT product integration specification

Status: **offline/product-prepared candidate only; not installed or hot-swapped**.
The installed/current release remains `bedf98c77283002c632dd7f345abaff9`.

## Additive contract
New contract: `cnn-bpsk-modrec4-qpsk-rrc-qam16-v1`.

It inherits the installed 60-member `cnn-bpsk-modrec4-qpsk-rrc-v1` software map
and adds only:
- `rf_qam16_receiver.py`
- `rf_qam16_pipeline.py`

New map = **62 members**. The old 55/58/60-member contracts remain byte-verifiable
and unchanged. Candidate parent/fallback = installed **bedf**; the candidate is
never copied into the board `current` release and no service is switched.

## Worker/UI behavior
- Worker demod status adds `qam16_available`, `qam16_algorithm_version`,
  `qam16_algorithm_sha256`, `qam16_reason`; all three QAM16 modules must resolve
  inside the same verified release.
- UI accepts only an explicit QAM16/RECT submission with explicit QI/IQ and
  reference seed; no TX mode, CNN class, prior BER or sample inference selects it.
- The same frozen `Qam16Envelope` result is landed only for the matching frame
  identity (capture/release/connection/config revision), current snapshot and
  current config revision. Wrong/old frames, config/mode changes and capability
  withdrawal clear the previous BER/preview.
- Stop/cancel retain the existing cooperative semantics; a cancelled or failed
  capture never lands a result and no old BER survives.
- EVM is explicitly unavailable (`False/None`); no QAM64/RRC/EVM/training is
  added and BPSK/QPSK/RRC algorithms are untouched.

## Frozen intervals
QAM16/RECT, 60 MSPS, 3 MSym/s, SPS20, 127 symbols, 4096 capture:
- valid 127 symbols;
- alignment 63 symbols / 252 bits;
- comparison starts at symbol 63, covers 64 symbols × 4 = **256 bits**;
- failures carry no BER/denominator; nonzero BER is retained.

## Candidate and ARM gate
- Candidate contract/release/list and actual `candidate.tgz` are built under
  `evidence/rf-teaching/qam16-rect-product-2026-10-24/`; actual source/software
  SHA is recorded. No install/current/release replacement.
- After explicit line-A release, a **temporary directory, no-RF, no-Overlay,
  no-init, no-install, no-service-switch** probe imported the actual candidate
  bytes (`candidate.tgz` SHA `181bf027…`, 62/62 software SHA, verify_release OK)
  and compared the same IQ input on host/ARM. Measured fields matched exactly;
  bit preview exact; symbol preview max abs diff 8.88e-16; local capture-id label
  differed only. ARM timing (median 655.36 ms) is recorded separately from host
  timing and is **not** a host result.
- This is an ARM parity record, not a service/deadline claim and not an approval.
  Current/active remains **bedf98c7**; candidate remains uninstalled. Temp dir was
  removed and post readback was VERIFIED_SAFE_EXPECTED.

## Non-goals
No RF capture, no real-board QAM16 signal, no daemon/worker deployment, no
candidate installation, no rollback, no training, no CNN mode substitution, no
QAM64/RRC QAM16/EVM, no commit.
