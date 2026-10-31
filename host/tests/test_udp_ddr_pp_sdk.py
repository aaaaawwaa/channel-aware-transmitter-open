"""Task 4 M0 source gate: firmware wires a Raw lwIP UDP echo service on GEM0
into the existing non-blocking service loop without replacing UART or the DDR
PP playback machinery."""
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
MAIN = (ROOT / "sdk" / "src" / "main.c").read_text(encoding="utf-8")
UDP_C = (ROOT / "sdk" / "src" / "udp_ddr_pp.c").read_text(encoding="utf-8")
UDP_H = (ROOT / "sdk" / "src" / "udp_ddr_pp.h").read_text(encoding="utf-8")


def test_firmware_uses_ps_gem0_lwip_raw_udp_and_fixed_m0_endpoint():
    for token in (
        '#include "lwip/udp.h"', '#include "netif/xadapter.h"',
        "XPAR_XEMACPS_0_BASEADDR", "xemac_add", "xemacif_input",
        "sys_check_timeouts", "0x02, 0xac, 0x82, 0x00, 0x00, 0x02",
        "192, 168, 2, 2", "40100", "UDP_DDR_PP_TYPE_ECHO",
    ):
        assert token in UDP_C


def test_main_services_network_without_replacing_uart_or_pp_service():
    loop = MAIN[MAIN.index("while (1)"):]
    assert "ddr_pp_service();" in loop
    assert "ddr_service();" in loop
    assert "udp_ddr_pp_poll();" in loop
    assert loop.index("ddr_pp_service();") < loop.index("udp_ddr_pp_poll();")
    assert "XUartPs_IsReceiveData" in loop
    assert "udp_ddr_pp_init" in MAIN
    assert "udp_ddr_pp_ops_t" in UDP_H


def test_udp_staging_is_owned_per_bank_and_never_uses_uart_cpu_sync_helper():
    for token in (
        "DDR_PP_STAGE_UART", "DDR_PP_STAGE_UDP", "ddr_pp_stage_owner[2]",
        "ddr_pp_stage_expected_bytes[2]", "ddr_pp_stage_received_bytes[2]",
        "ddr_pp_payload_crc32[2]", "ddr_pp_udp_begin", "ddr_pp_udp_write",
        "ddr_pp_udp_commit", "ddr_pp_udp_abort", "ddr_pp_flush_cpu_write_range",
    ):
        assert token in MAIN
    udp_commit = MAIN[MAIN.index("static int ddr_pp_udp_commit"):]
    assert "Xil_DCacheFlushRange" in udp_commit
    assert "Xil_DCacheInvalidateRange" not in udp_commit


def test_udp_decoder_rejects_bad_session_range_crc_and_timeout_before_commit():
    for token in (
        "UDP_DDR_PP_ERROR_SESSION", "UDP_DDR_PP_ERROR_RANGE",
        "UDP_DDR_PP_ERROR_CRC", "UDP_DDR_PP_ERROR_TIMEOUT",
        "udp_ddr_pp_crc32", "UDP_DDR_PP_SESSION_TIMEOUT",
        "UDP_DDR_PP_TYPE_BEGIN", "UDP_DDR_PP_TYPE_DATA",
        "UDP_DDR_PP_TYPE_COMMIT", "UDP_DDR_PP_TYPE_ABORT",
    ):
        assert token in UDP_C


def test_main_installs_real_udp_staging_ops_and_exposes_net_status():
    net = MAIN[MAIN.index('strcmp(tok[1], "NET")'):]
    assert "NET GEM0 INIT" in net
    assert "udp_ddr_pp_is_initialized()" in net
    assert "udp_ddr_pp_link_is_up()" in net
    assert "udp_ddr_pp_ipv4_string()" in net
    assert "ddr_pp_udp_ops" in MAIN
    assert "udp_ddr_pp_init(&ddr_pp_udp_ops)" in MAIN


def test_generated_gem_workspace_contains_lwip_and_split_memory_layout():
    workspace = ROOT / "sdk" / "ws2025_gem_fresh"
    app = workspace / "awg_bridge"
    if not app.is_dir():
        pytest.skip("fresh generated workspace is not present")

    xparameters = [
        path for path in workspace.rglob("xparameters.h")
        if path.as_posix().endswith(
            "standalone_ps7_cortexa9_0/bsp/include/xparameters.h"
        )
    ]
    assert len(xparameters) == 1
    xparameters_text = xparameters[0].read_text(encoding="utf-8")
    cmake_text = (app / "src" / "CMakeLists.txt").read_text(encoding="utf-8")
    linker_text = (app / "src" / "lscript.ld").read_text(encoding="utf-8")

    assert "#define XPAR_XEMACPS_0_BASEADDR 0xe000b000" in xparameters_text
    assert "#define XPAR_XUARTPS_0_BASEADDR 0xe0001000" in xparameters_text
    assert "lwip220" in cmake_text
    assert ".text" in linker_text and "> ps7_ram_0_memory_0" in linker_text
    assert ".data" in linker_text and "> ps7_ddr_0_memory_0" in linker_text
    assert ".bss" in linker_text and "> ps7_ddr_0_memory_0" in linker_text
    assert ".heap" in linker_text and "> ps7_ddr_0_memory_0" in linker_text
    assert ".stack" in linker_text and "> ps7_ddr_0_memory_0" in linker_text
