#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
ROLLOFFS = (0.2, 0.35, 0.5)
ROLLOFF_SUFFIXES = ("A20", "A35", "A50")
sys.path.insert(0, str(ROOT / "host"))

from siggen.digital_mod import generate_symbols
from siggen.rrc import rrc_coefficients, rrc_filter_symbols


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="QPSK",
                        choices=["BPSK", "QPSK", "QAM4", "QAM16", "QAM64"])
    parser.add_argument("--symbols", type=int, default=16)
    parser.add_argument("--seed", type=int, default=0x7F)
    parser.add_argument("--alpha", type=float, choices=ROLLOFFS, default=0.35)
    parser.add_argument("--coeff", type=Path, default=ROOT / "src/rtl/rrc_coeffs.vh")
    parser.add_argument("--vectors", type=Path, default=ROOT / "src/tb/rrc_expected.mem")
    args = parser.parse_args()

    coeffs = [rrc_coefficients(alpha) for alpha in ROLLOFFS]
    i_sym, q_sym = generate_symbols(args.mode, args.symbols, seed=args.seed)
    selected_coeff = coeffs[ROLLOFFS.index(args.alpha)]
    i_out, q_out = rrc_filter_symbols(i_sym, q_sym, selected_coeff)

    args.coeff.write_text(
        "`ifndef RRC_COEFFS_VH\n`define RRC_COEFFS_VH\n"
        + "".join(
            f"localparam signed [15:0] RRC_COEFF_{n:02d}_{suffix} = "
            f"16'sh{int(value) & 0xffff:04x};\n"
            for suffix, coeff in zip(ROLLOFF_SUFFIXES, coeffs)
            for n, value in enumerate(coeff)
        )
        + "`endif\n",
        encoding="ascii",
    )
    args.vectors.write_text(
        "\n".join(f"{int(i) & 0xffff:04x}{int(q) & 0xffff:04x}" for i, q in zip(i_out, q_out))
        + "\n",
        encoding="ascii",
    )


if __name__ == "__main__":
    main()
