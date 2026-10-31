#!/usr/bin/env python3
"""Offline per-frame wire/12-bit/sign-extension analysis for the RX delay run."""
import argparse
import json
import pathlib
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from rf_qam64_frame29_lane1_scan_runner import _compare_all


def _load(path):
    return np.frombuffer(pathlib.Path(path).read_bytes(), dtype="<i2").reshape(-1, 2).astype(np.int16)


def _aligned(tx_path, iq_path, alg):
    tx = _load(tx_path)
    iq = _load(iq_path)
    sample = iq[:, ::-1] if alg["iq_swap"] else iq
    ref = tx[(np.arange(len(sample)) + alg["offset"]) % len(tx)]
    return sample, ref


def _component(name):
    return name


def frame(tx_path, iq_path):
    full = _compare_all(tx_path, iq_path)
    alg = full["alignment"]
    sample, ref = _aligned(tx_path, iq_path, alg)
    # Raw 16-bit wire and 12-bit valid-data differences.
    wire = sample.astype(np.uint16) ^ ref.astype(np.uint16)
    valid = (sample.astype(np.int32) & 0x0FFF) ^ (ref.astype(np.int32) & 0x0FFF)
    wire_bad = np.any(wire != 0, axis=1)
    valid_bad = np.any(valid != 0, axis=1)

    def sign_ok(a):
        u = a.astype(np.int32) & 0xFFFF
        bit11 = (u >> 11) & 1
        upper = (u >> 12) & 0xF
        expected = np.where(bit11 == 1, 0xF, 0x0)
        return upper == expected

    got_sign = sign_ok(sample)
    ref_sign = sign_ok(ref)
    # Classify every raw wire mismatch: lower 12 bits, upper sign-extension bits,
    # or both; keep I/Q separate.
    pairs = []
    for i in np.flatnonzero(wire_bad):
        for q in (0, 1):
            if wire[i, q] == 0:
                continue
            w = int(wire[i, q])
            low = int((sample[i, q].astype(np.int32) & 0x0FFF) ^ (ref[i, q].astype(np.int32) & 0x0FFF))
            high = w & 0xF000
            pairs.append(dict(sample=int(i), axis="I" if q == 0 else "Q",
                              got=int(sample[i, q]), want=int(ref[i, q]), wire_xor=w,
                              valid12_xor=low, upper_xor=high,
                              got_sign_ext_ok=bool(got_sign[i, q]),
                              want_sign_ext_ok=bool(ref_sign[i, q])))
    bits = {}
    for b in range(16):
        n = int(np.count_nonzero((wire >> b) & 1))
        if n:
            bits[str(b)] = n
    by_axis = {}
    for axis, col in (("I", 0), ("Q", 1)):
        by_axis[axis] = dict(
            wire_mismatch=int(np.count_nonzero(wire[:, col] != 0)),
            valid12_mismatch=int(np.count_nonzero(valid[:, col] != 0)),
            got_sign_ext_violations=int(np.count_nonzero(~got_sign[:, col])),
            want_sign_ext_violations=int(np.count_nonzero(~ref_sign[:, col])),
        )
    # Rows where the two sides differ only in upper sign-extension bits vs rows
    # that also differ in the 12-bit value.
    upper_only = sum(1 for p in pairs if p["valid12_xor"] == 0 and p["upper_xor"] != 0)
    low_and_upper = sum(1 for p in pairs if p["valid12_xor"] != 0 and p["upper_xor"] != 0)
    low_only = sum(1 for p in pairs if p["valid12_xor"] != 0 and p["upper_xor"] == 0)
    halfword = {str(b): int(np.count_nonzero((wire >> b) & 1)) for b in (6, 7, 8, 9, 10, 11) if np.any((wire >> b) & 1)}
    lane1 = {str(b): int(np.count_nonzero((wire >> b) & 1)) for b in (1, 7) if np.any((wire >> b) & 1)}
    return dict(
        alignment=alg,
        exact_pairs=full["exact_pairs"], mismatch_pairs=full["mismatch_pairs"],
        wire16=dict(
            bad_pairs=int(np.count_nonzero(wire_bad)),
            xor_histogram={str(k): int(v) for k, v in sorted(Counter(int(x) for x in wire.flatten() if x).items())},
            bit_counts=bits,
            by_axis=by_axis,
            upper_only_rows=upper_only, low_only_rows=low_only,
            low_and_upper_rows=low_and_upper,
            examples=pairs[:12],
        ),
        valid12=dict(
            bad_pairs=int(np.count_nonzero(valid_bad)),
            xor_histogram={str(k): int(v) for k, v in sorted(Counter(int(x) for x in valid.flatten() if x).items())},
            by_axis={axis: int(np.count_nonzero(valid[:, 0 if axis == "I" else 1] != 0)) for axis in ("I", "Q")},
        ),
        sign_extension=dict(
            got_violations=int(np.count_nonzero(~got_sign)),
            ref_violations=int(np.count_nonzero(~ref_sign)),
            mismatches_with_got_bad=sum(1 for p in pairs if not p["got_sign_ext_ok"]),
            mismatches_with_ref_bad=sum(1 for p in pairs if not p["want_sign_ext_ok"]),
            note="bits12..15 are sign extension of bit11; upper-only differences are not four independent LVDS bits",
        ),
        bits=bits,
        halfword_bits_6_11=halfword,
        lane1_bits_1_7=lane1,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tx", required=True)
    p.add_argument("--frame", action="append", required=True,
                   help="tag=path")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    out = {}
    for item in a.frame:
        tag, path = item.split("=", 1)
        out[tag] = frame(a.tx, path)
    pathlib.Path(a.output).write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
