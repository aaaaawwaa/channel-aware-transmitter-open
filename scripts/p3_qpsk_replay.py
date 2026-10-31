#!/usr/bin/env python3
"""P3 QPSK offline replay / independent-reference CLI.

The reference answer is produced only by ``pynq_overlay/sw/tests/qpsk_reference.py``.
The optional A receiver is imported solely to compare an A result; it is never
used to construct the expected reference or the BER.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "pynq_overlay" / "sw" / "tests"
SW = ROOT / "pynq_overlay" / "sw"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(SW))
import qpsk_reference as Q  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _load_iq(path: Path):
    raw = np.frombuffer(path.read_bytes(), dtype="<i2").reshape(-1, 2)
    return raw


def _load_metadata(path):
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _seed_from(metadata, explicit=None):
    if explicit is not None:
        return explicit
    if metadata:
        ev = metadata.get("tx_load_evidence") or {}
        seed = ev.get("tx_seed")
        if seed is not None:
            return int(seed)
        # Some older task71 metadata carries the seed only in result.json.
        for key in ("tx_seed", "seed"):
            if metadata.get(key) is not None:
                return int(metadata[key])
    return None


def _reference_result(raw, seed, metadata, order):
    arr = raw if order == "IQ" else raw[:, ::-1]
    iq = arr.astype(float)
    r = Q.analyze_iq(iq, seed, metadata)
    return dict(r, iq_order=order)


def _a_result(raw, seed, order):
    """Optional A comparison only; returns a JSON-safe dict or an error string."""
    try:
        from rf_qpsk_receiver import QpskConfig, receive_qpsk
        from rf_rx_types import RxIqFrame, RxReadback
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    # A's config describes the *file* order; feed the original payload, not a
    # pre-swapped copy (pre-swapping plus QI config would double-swap).
    frame = RxIqFrame(
        capture_id="p3-replay", payload=np.asarray(raw, dtype="<i2").tobytes(),
        samples=4096, sample_rate_hz=60_000_000, ratio=0,
        daemon_timestamp_ns=1, connection_generation=1, config_revision=1,
        request_id=1, release_id="da97347ab29d0b31513460e40bcce99f",
        op="rx", physical=True, pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    try:
        r = receive_qpsk(frame, QpskConfig(reference_prbs7_seed=seed, iq_order=order))
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "error": "%s: %s" % (type(exc).__name__, exc)}
    return {
        "available": True, "iq_order": order, "version": r.algorithm_version,
        "reason": r.reason, "carrier_sync": r.carrier_sync,
        "timing_sync": r.timing_sync, "reference_sync": r.reference_sync,
        "cfo_hz": r.cfo_hz, "timing_offset_samples": r.timing_offset_samples,
        "phase_ambiguity": r.phase_ambiguity, "valid_symbols": r.valid_symbols,
        "alignment_bits": r.alignment_bits,
        "comparison_start_symbol": r.comparison_start_symbol,
        "bit_errors": r.bit_errors, "compared_bits": r.compared_bits,
        "ber": r.ber,
    }


def replay_one(iq_path: Path, metadata_path=None, seed=None, label=None,
               compare_a=True):
    raw = _load_iq(iq_path)
    metadata = _load_metadata(metadata_path)
    resolved_seed = _seed_from(metadata, seed)
    attempts = {order: _reference_result(raw, resolved_seed, metadata, order)
                for order in ("IQ", "QI")}
    good = [r for r in attempts.values() if r.get("reference_sync")]
    # Prefer a synced result; otherwise keep the higher alignment correlation for
    # diagnosis.  No frame is dropped.
    chosen = (min(good, key=lambda r: (r.get("ber") if r.get("ber") is not None else 9))
              if good else max(attempts.values(),
                               key=lambda r: float(r.get("alignment_correlation") or 0.0)))
    out = {
        "schema": "p3-qpsk-replay-v1",
        "label": label or iq_path.parent.name,
        "iq_path": str(iq_path),
        "iq_sha256": _sha(iq_path),
        "seed": resolved_seed,
        "iq_order": chosen.get("iq_order"),
        "reference": chosen,
        "attempts": attempts,
        "truth_or_label": label or iq_path.parent.name,
    }
    if compare_a:
        out["a"] = {
            order: _a_result(raw, resolved_seed, order) for order in ("IQ", "QI")
        }
    return out


def write_fixtures(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    manifest = []
    rng = np.random.default_rng(20261023)
    for spec in Q.fixture_catalog():
        kwargs = {k: v for k, v in spec.items()
                  if k not in ("name", "kind", "random_reference")}
        iq, meta, seed = Q.make_fixture(**kwargs)
        reference_symbols = None
        if spec.get("random_reference"):
            reference_symbols = rng.choice([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j], size=127)
        result = Q.analyze_iq(iq, seed, meta, reference_symbols=reference_symbols)
        npz = outdir / (spec["name"] + ".npz")
        save = {"iq": iq, "metadata": np.array(json.dumps(meta)), "seed": np.array(seed)}
        if reference_symbols is not None:
            save["reference_symbols"] = reference_symbols
        np.savez_compressed(npz, **save)
        manifest.append({
            "name": spec["name"], "kind": spec.get("kind"),
            "file": npz.name, "sha256": _sha(npz),
            "expected_reason": result.get("reason"),
            "reference_sync": result.get("reference_sync"),
            "ber": result.get("ber"),
            "bit_errors": result.get("bit_errors"),
            "compared_bits": result.get("compared_bits"),
            "alignment_bits": result.get("alignment_bits"),
            "comparison_start_symbol": result.get("comparison_start_symbol"),
            "spec": spec,
        })
    (outdir / "manifest.json").write_text(
        json.dumps({"schema": "p3-qpsk-fixtures-v1", "fixtures": manifest},
                   indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq", type=Path)
    p.add_argument("--metadata", type=Path)
    p.add_argument("--seed", type=int)
    p.add_argument("--label")
    p.add_argument("--out", type=Path)
    p.add_argument("--iq-order", choices=("auto", "IQ", "QI"), default="auto")
    p.add_argument("--no-a", action="store_true", help="skip optional A comparison")
    p.add_argument("--write-fixtures", type=Path, metavar="DIR",
                   help="write synthetic fixtures and manifest, then exit")
    a = p.parse_args(argv)
    if a.write_fixtures:
        items = write_fixtures(a.write_fixtures)
        print(json.dumps({"fixtures": len(items), "manifest": str(a.write_fixtures / "manifest.json")}))
        return 0
    if not a.iq:
        p.error("--iq is required unless --write-fixtures is used")
    result = replay_one(a.iq, a.metadata, a.seed, a.label, not a.no_a)
    if a.iq_order != "auto":
        result["iq_order"] = a.iq_order
        result["reference"] = result["attempts"][a.iq_order]
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(result, indent=2, default=float) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
