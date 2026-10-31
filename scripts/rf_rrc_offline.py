#!/usr/bin/env python3
"""Reproducible offline entry for the BPSK/QPSK RRC snapshot receiver.

Two explicit input modes:

* ``--fixture`` reads a frozen independent fixture JSON plus its raw ``.iq``.
* ``--product-tx`` runs the actual product RRC TX generator, extends its
  2540-sample cyclic frame to the 4096-sample RX domain, and feeds that to the
  receiver (a TX-generator cross-check, not independent truth).

The receiver config (mode, reference seed, rolloff, sps, rates, wire I/Q order)
is always explicit.  The output records the input SHA-256, config, the unified
``ModemResult`` and the RRC-only group-delay/transition/useful-symbol ranges.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pynq_overlay/sw"))

from rf_iq_signal import generate_digital_frame  # noqa: E402
from rf_rrc_receiver import RrcConfig, receive_rrc  # noqa: E402
from rf_rx_types import RxIqFrame, RxReadback  # noqa: E402

CAPTURE_SAMPLES = 4096
SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_frame(raw: bytes, *, capture_id: str) -> RxIqFrame:
    if len(raw) != CAPTURE_SAMPLES * 4:
        raise SystemExit("REFUSE: expected %d bytes of 4096 complex i16 IQ, got %d"
                         % (CAPTURE_SAMPLES * 4, len(raw)))
    return RxIqFrame(
        capture_id=capture_id, payload=raw, samples=CAPTURE_SAMPLES,
        sample_rate_hz=SAMPLE_RATE_HZ, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id=None, op="offline", physical=False,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)


def fixture_input(path: Path, order_override):
    meta = json.loads(path.read_text(encoding="utf-8"))
    iq_path = path.with_name(meta["iq_file"])
    raw = iq_path.read_bytes()
    order = order_override or meta["iq_order"]
    if order not in ("IQ", "QI"):
        raise SystemExit("REFUSE: fixture needs explicit IQ/QI")
    return meta, raw, order


def product_tx_input(mode, seed, amplitude, capture_offset, order):
    if order != "IQ":
        raise SystemExit("REFUSE: product TX emits I,Q wire order; --iq-order must be IQ")
    args = {
        "mode": mode, "filter": "RRC", "rolloff": "0.35", "seed": seed,
        "sample_rate_hz": SAMPLE_RATE_HZ, "symbol_rate_hz": SYMBOL_RATE_HZ,
        "amplitude": amplitude,
    }
    tx_meta, payload = generate_digital_frame(args)
    samples = len(payload) // 4
    iq = np.frombuffer(payload, dtype="<i2").reshape(-1, 2)
    # The product TX frame is a 2540-sample cyclic frame (`wrap=True`); the RX
    # snapshot is 4096 samples.  Extend cyclically and allow an explicit phase.
    repeated = np.resize(iq, (CAPTURE_SAMPLES, 2))
    if capture_offset:
        repeated = np.roll(repeated, -int(capture_offset), axis=0)
    raw = repeated.astype("<i2").tobytes()
    tx = {
        "source": "product_rf_iq_signal.generate_digital_frame",
        "args": args, "metadata": tx_meta, "payload_samples": samples,
        "payload_sha256": sha256(payload),
        "capture_offset_samples": int(capture_offset),
    }
    return tx, raw, "IQ"


def run_one(source, raw, order, mode, seed, capture_id):
    config = RrcConfig(mode=mode, reference_prbs7_seed=seed, iq_order=order)
    frame = make_frame(raw, capture_id=capture_id)
    env = receive_rrc(frame, config)
    return {
        "schema": "p3-rrc-offline-run-v1",
        "input": {
            "source": source,
            "samples": CAPTURE_SAMPLES,
            "bytes": len(raw),
            "iq_sha256": sha256(raw),
            "iq_order": order,
            "note": "raw wire order is explicit; receiver never guesses or globally swaps I/Q",
        },
        "config": asdict(config),
        "envelope": {
            "algorithm_version": env.algorithm_version,
            "group_delay_samples": env.group_delay_samples,
            "transition_symbols": env.transition_symbols,
            "valid_symbol_start": env.valid_symbol_start,
            "valid_symbol_end": env.valid_symbol_end,
            "comparison_start_symbol": env.comparison_start_symbol,
            "comparison_end_symbol": env.comparison_end_symbol,
        },
        "result": env.result.to_dict(),
        "policy": {
            "alignment_region": "symbol 4..66 (63 symbols); used for synchronization/phase only",
            "ber_region": "symbol 67..122 (56 symbols); never used to pick sync/phase",
            "comparison_bits": "BPSK 56 / QPSK 112; RECT's 127/128 is not reused",
            "evm": "unavailable (False/None), never 0",
            "no_fake_denominator": "unavailable results carry no BER/counts",
        },
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--fixture", type=Path,
                   help="frozen independent fixture JSON (its .iq is read next to it)")
    g.add_argument("--product-tx", action="store_true",
                   help="use the actual product RRC TX generator as input")
    p.add_argument("--mode", choices=("BPSK", "QPSK"))
    p.add_argument("--seed", type=int)
    p.add_argument("--iq-order", choices=("IQ", "QI"))
    p.add_argument("--amplitude", type=int, default=60)
    p.add_argument("--capture-offset", type=int, default=0,
                   help="cyclic sample phase for --product-tx only")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)

    if args.fixture is not None:
        meta, raw, order = fixture_input(args.fixture, args.iq_order)
        mode, seed = meta["mode"], meta["seed"]
        if args.mode is not None and args.mode != mode:
            raise SystemExit("REFUSE: --mode disagrees with fixture")
        if args.seed is not None and args.seed != seed:
            raise SystemExit("REFUSE: --seed disagrees with fixture")
        tx = {
            "source": "independent_fixture",
            "fixture": str(args.fixture),
            "payload_sha256": meta.get("payload_sha256"),
            "reference_sha256": meta.get("reference_sha256"),
            "taps_sha256": meta.get("taps_sha256"),
            "kind": meta.get("kind"),
        }
        capture_id = "rrc-offline-" + meta.get("id", args.fixture.stem)
    else:
        if args.mode is None or args.seed is None:
            raise SystemExit("REFUSE: --product-tx needs explicit --mode and --seed")
        if not 1 <= args.seed <= 127:
            raise SystemExit("REFUSE: --seed must be 1..127")
        if not 0 <= args.amplitude <= 2047:
            raise SystemExit("REFUSE: --amplitude must be 0..2047")
        mode, seed = args.mode, args.seed
        tx, raw, order = product_tx_input(mode, seed, args.amplitude,
                                          args.capture_offset, args.iq_order or "IQ")
        capture_id = "rrc-product-tx-%s-%d" % (mode.lower(), seed)

    out = run_one("fixture" if args.fixture is not None else "product_tx",
                  raw, order, mode, seed, capture_id)
    out["tx"] = tx
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, allow_nan=False) + "\n",
                        encoding="utf-8")
    r = out["result"]
    print(json.dumps({
        "out": str(args.out), "mode": mode, "seed": seed, "iq_order": order,
        "carrier_sync": r["carrier_sync"], "timing_sync": r["timing_sync"],
        "reference_sync": r["reference_sync"], "reason": r["reason"],
        "valid_symbols": r["valid_symbols"],
        "bit_errors": r["bit_errors"], "compared_bits": r["compared_bits"],
        "ber": r["ber"], "evm_available": r["evm_available"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
