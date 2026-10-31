import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
SDK_MAIN = ROOT / "sdk" / "src" / "main.c"


def _text():
    return SDK_MAIN.read_text(encoding="utf-8")


def test_sdk_declares_bounded_ddr_pp_bank_layout():
    text = _text()
    assert re.search(r"#define\s+DDR_PP_BANK_BYTES\s+0x00100000u", text)
    assert re.search(r"#define\s+DDR_PP_BANK0_ADDR\s+DDR_BUFFER_ADDR", text)
    assert re.search(
        r"#define\s+DDR_PP_BANK1_ADDR\s+\(DDR_PP_BANK0_ADDR\s*\+\s*DDR_PP_BANK_BYTES\)",
        text,
    )
    assert re.search(
        r"#define\s+DDR_PP_PAIR_END\s+\(DDR_PP_BANK1_ADDR\s*\+\s*DDR_PP_BANK_BYTES\)",
        text,
    )


def test_sdk_places_dma_pp_buffer_above_lwip_image_floor():
    text = _text()
    assert re.search(
        r"#define\s+DDR_BUFFER_ADDR\s+\(DDR_BASEADDR\s*\+\s*DDR_RESERVED_BYTES\s*\+\s*0x01000000u\)",
        text,
    )


    text = _text()
    for name in (
        "ddr_pp_stage_seen",
        "ddr_pp_stage_bytes",
        "ddr_pp_bit_length",
        "ddr_pp_crc16",
        "ddr_pp_valid",
    ):
        assert re.search(rf"{name}\s*\[\s*2\s*\]", text)


def test_sdk_parses_ddr_pp_commands_in_a_separate_namespace():
    text = _text()
    assert 'strcmp(tok[2], "PP")' in text
    for command in ("CLEAR", "CHUNK", "COMMIT", "START", "STOP"):
        assert f'strcmp(tok[3], "{command}")' in text
    for command in (
        "SET DDR PP CLEAR",
        "SET DDR PP CHUNK",
        "SET DDR PP COMMIT",
        "SET DDR PP START",
        "SET DDR PP STOP",
    ):
        assert command in text


def test_sdk_uart_pp_commands_respect_udp_stage_ownership():
    text = _text()
    pp = text[
        text.index('strcmp(tok[1], "DDR") == 0 && strcmp(tok[2], "PP") == 0') :
    ]
    clear = pp[: pp.index('strcmp(tok[3], "CHUNK")')]
    chunk = pp[pp.index('strcmp(tok[3], "CHUNK")') : pp.index('strcmp(tok[3], "COMMIT")')]
    commit = pp[pp.index('strcmp(tok[3], "COMMIT")') : pp.index('strcmp(tok[3], "START")')]
    assert "DDR_PP_STAGE_UDP" in clear
    assert "ddr_pp_stage_owner" in chunk
    assert "DDR_PP_STAGE_UART" in chunk
    assert "DDR_PP_STAGE_UART" in commit
    assert "DDR_PP_STAGE_UDP" in commit


def test_sdk_formats_ddr_pp_status_with_banks_and_active_next_fields():
    text = _text()
    marker = '"DDR PP ACTIVE'
    assert marker in text
    status = text[text.index(marker) :]
    bank1_offset = status.index("BANK1")
    bank0 = status[:bank1_offset]
    bank1 = status[bank1_offset:]
    bank0_fields = (
        "ACTIVE",
        "ACTIVE_BANK",
        "NEXT_BANK",
        "STATE",
        "ERROR",
        "BANK0",
        "VALID",
        "LENGTH",
        "CRC",
    )
    bank1_fields = ("BANK1", "VALID", "LENGTH", "CRC")
    assert [bank0.index(field) for field in bank0_fields] == sorted(
        bank0.index(field) for field in bank0_fields
    )
    assert [bank1.index(field) for field in bank1_fields] == sorted(
        bank1.index(field) for field in bank1_fields
    )


def test_sdk_enforces_four_byte_alignment_for_ddr_pp_chunks_and_commits():
    text = _text()
    chunk = text.index('strcmp(tok[3], "CHUNK")')
    commit = text.index('strcmp(tok[3], "COMMIT")', chunk)
    assert "offset & 3u" in text[chunk:commit]
    assert "byte_count & 3u" in text[chunk:commit]
    assert "bit_length & 31u" in text[commit:]
    assert "DDR_PP_PAIR_END" in text[chunk:commit]


def test_sdk_ddr_pp_lifecycle_exposes_running_drain_and_error():
    text = _text()
    start = text.index("static int ddr_pp_start_transfer")
    service = text.index("static void ddr_pp_service")
    stop = text.index("static int ddr_pp_stop_transfer")
    start_body = text[start:service]
    service_body = text[service:]
    stop_body = text[stop:start]
    assert 'ddr_pp_state = "RUNNING"' in start_body
    assert 'ddr_pp_state = "DRAIN"' in service_body
    assert 'ddr_pp_state = "ERROR"' in service_body
    assert "ddr_pp_active_bank" in start_body
    assert "ddr_pp_bank_fresh[bank] = 0" in start_body
    assert start_body.index("XAxiDma_SimpleTransfer") < start_body.index("dma_pp_stream_ctrl(1")
    assert "ddr_pp_state = \"IDLE\"" in stop_body


def test_sdk_ddr_pp_stops_and_recovers_dma_after_error_or_reset():
    text = _text()
    service = text[text.index("static void ddr_pp_service") :]
    stop = text[
        text.index("static int ddr_pp_stop_transfer") :
        text.index("static int ddr_pp_start_transfer")
    ]
    assert "XAXIDMA_ERR_ALL_MASK" in service
    assert "ddr_pp_stop_transfer()" in service
    assert "XAxiDma_Reset" in stop
    assert "dma_wait_reset" in stop
    assert stop.index("dma_stream_ctrl(0") < stop.index("XAxiDma_Reset")
    assert "ddr_pp_error_code" in stop


def test_single_buffer_ddr_codec_and_status_remain_unchanged():
    text = _text()
    for command in (
        "SET DDR CLEAR",
        "SET DDR CHUNK",
        "SET DDR COMMIT",
        "SET DDR ABORT",
        "SET DDR START|STOP",
    ):
        assert command in text
    assert 'strcmp(tok[2], "START")' in text
    assert 'strcmp(tok[2], "STOP")' in text
    assert (
        "DDR VALID %lu BUFFER %08lX MAX %lu LENGTH %lu CRC %04lX "
        "UPLOAD %s ACTIVE %lu DMA %s ERROR %02X"
    ) in text


def test_sdk_initializes_gic_before_lwip_configures_the_gem_handler():
    text = (ROOT / "sdk" / "src" / "udp_ddr_pp.c").read_text(encoding="utf-8")

    assert '#include "xscugic.h"' in text
    assert "XScuGic_LookupConfig(XPAR_SCUGIC_SINGLE_DEVICE_ID)" in text
    assert "XScuGic_CfgInitialize" in text
    assert "Xil_ExceptionInit();" in text
    assert "Xil_ExceptionRegisterHandler" in text
    assert "XScuGic_InterruptHandler" in text
    assert "Xil_ExceptionEnable();" in text
    assert "XScuGic_Connect" not in text

    gic_init = text.index("gem0_irq_init()", text.index("udp_ddr_pp_init"))
    add = text.index("xemac_add(&gem0_netif")
    enable = text.index("Xil_ExceptionEnable();")
    assert gic_init < add < enable
