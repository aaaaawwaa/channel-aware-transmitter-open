# LICENSES

This directory contains the license texts and provenance notes for
third-party material included in this repository. The project's own code is
licensed under the MIT License (see the root `LICENSE`).

| Path / component | License | License text or note |
|---|---|---|
| Project-owned code and documentation (everything not listed below) | MIT | [`LICENSE`](../LICENSE) |
| `rf_overlay/sw/board_app/vendor/` (10 files) | `LicenseRef-ADI-AD9361` (Analog Devices BSD-style, with patent and field-of-use conditions) | [`Analog-Devices-AD9361-BSD-style.txt`](Analog-Devices-AD9361-BSD-style.txt) |
| `rf_overlay/sw/ref/main.c`, `platform.c`, `platform.h` | `LicenseRef-ADI-AD9361` | [`Analog-Devices-AD9361-BSD-style.txt`](Analog-Devices-AD9361-BSD-style.txt) |
| `rf_overlay/rtl/ad9361_phy.v`, `phy_rx.v`, `phy_tx.v`, `axi_ctrl_v1_0.v`, `axi_ctrl_v1_0_S00_AXI.v` | UNKNOWN (no license granted) | [`UNKNOWN-PROVENANCE.md`](UNKNOWN-PROVENANCE.md) |
| `rf_overlay/sw/ref/platform_config.h` | UNKNOWN (Vitis-generated stub, no statement) | [`UNKNOWN-PROVENANCE.md`](UNKNOWN-PROVENANCE.md) |
| `sdr_testbench/radio_test.py`, `sdr_testbench/board_integration/stage0.py`, `sdr_testbench/gui_demo/bpsk_tx_gui.py`, `sdr_testbench/gui_demo/verify_offline.py`, `skills/pluto-gnuradio-testbench/assets/testbench/radio_test.py` | GPL-3.0 | [`GPL-3.0.txt`](GPL-3.0.txt) |

Notes:

- The ADI license is not standard BSD-3-Clause: it adds a patent-rights
  condition and a field-of-use condition ("use, in source or binary form, must
  be run on or directly connected to an Analog Devices Inc. component"). Do not
  map it to `BSD-3-Clause` in tooling.
- The five `rf_overlay/rtl` files listed as UNKNOWN are required by the
  `rf_overlay` PL build. They are retained in this private preparation copy,
  but they must not be publicly redistributed until their author's permission
  is obtained or they are replaced. See `UNKNOWN-PROVENANCE.md`.
- Generated GNU Radio Companion output (`sdr_testbench/generated/`,
  `sdr_testbench/gui_demo/generated/`) and reference-only third-party files
  (`rf_overlay/rtl_ref/`) are intentionally not included in this repository.
- External dependencies (Vivado/Vitis, PYNQ, GNU Radio, NumPy, PyTorch,
  PySide6, PyQt5, PyVISA, etc.) are not distributed here; use them under their
  own licenses.
