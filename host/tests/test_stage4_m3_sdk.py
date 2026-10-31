import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
SDK_MAIN = ROOT / "sdk" / "src" / "main.c"


def _text():
    return SDK_MAIN.read_text(encoding="utf-8")


def _fixed_ddr_offset(name):
    """Offset of a `#define <name> (DDR_BASEADDR + DDR_RESERVED_BYTES + 0x...u)`."""
    text = _text()
    match = re.search(
        r"#define\s+%s\s+\(DDR_BASEADDR\s*\+\s*DDR_RESERVED_BYTES\s*\+\s*(0x[0-9A-Fa-f]+)u\)"
        % re.escape(name),
        text,
    )
    assert match, "%s must be DDR_BASEADDR + DDR_RESERVED_BYTES + <offset>" % name
    return int(match.group(1), 16)


def test_sdk_declares_fixed_ddr_payload_contract():
    text = _text()
    assert "DDR_BUFFER_BYTES      0x00100000u" in text
    assert "DDR_MAX_BITS" in text
    assert "DMA_BUFFER_ADDR" in text
    assert "DDR_UPLOAD_MAX_BYTES" in text


def test_fixed_ddr_scratch_buffers_sit_above_the_program_image():
    # .text is in OCM, but .rodata/.data/.bss/.heap/.stack (including the lwIP
    # pbuf pool) fill DDR from 0x100000 past 0x500000. A fixed DMA/diag buffer
    # placed only 1 MiB above DDR_RESERVED (0x300000) overlapped the lwIP
    # rx_pbufs_storage table, so SET DMA BENCH overwrote the GEM RX pbuf
    # pointers and the next RX interrupt hung the CPU inside pbuf_realloc().
    # Keep every fixed scratch region well clear of the linked image.
    min_offset = 0x01000000  # 16 MiB above DDR_RESERVED
    assert _fixed_ddr_offset("DMA_BUFFER_ADDR") >= min_offset
    assert _fixed_ddr_offset("DDR_DIAG_BASE") >= min_offset


def test_sdk_exposes_ddr_payload_commands_and_status_fields():
    text = _text()
    for command in ("SET DDR CLEAR", "SET DDR CHUNK", "SET DDR COMMIT",
                    "SET DDR ABORT", 'strcmp(tok[2], "START")',
                    'strcmp(tok[2], "STOP")'):
        assert command in text
    status = "DDR VALID %lu BUFFER %08lX MAX %lu LENGTH %lu CRC %04lX UPLOAD %s ACTIVE %lu DMA %s ERROR %02X"
    assert status in text


def test_sdk_ddr_upload_validates_payload_before_mutating_metadata():
    text = _text()
    assert "ddr_stage_seen" in text
    assert "crc8_atm(bytes, byte_count)" in text
    assert "crc16_ccitt" in text
    assert "ddr_all_received" in text
    assert "ddr_uploading" in text
    commit = text.index('strcmp(tok[2], "COMMIT")')
    metadata = text.index("ddr_bit_length = bit_length", commit)
    assert text.index("expected_crc = crc16_ccitt", commit) < metadata
    assert text.index("ddr_all_received", commit) < metadata
    clear = text.index('strcmp(tok[2], "CLEAR")')
    clear_end = text.index('strcmp(tok[2], "ABORT")', clear)
    clear_path = text[clear:clear_end]
    assert "ddr_valid = 0" not in clear_path
    assert "ddr_bit_length = 0" not in clear_path


def test_sdk_ddr_chunks_form_one_contiguous_upload_range():
    text = _text()
    start = text.index("static int ddr_range_valid")
    end = text.index("static void ddr_abort_upload", start)
    assert "offset == ddr_stage_bytes" in text[start:end]
    command = text.index('strcmp(tok[2], "CHUNK")')
    command_end = text.index('strcmp(tok[2], "COMMIT")', command)
    assert "parse_u32_limit(tok[3], &offset, DDR_BUFFER_BYTES - 1u)" in text[command:command_end]

def test_sdk_ddr_start_enables_stream_only_after_dma_transfer_is_armed():
    text = _text()
    start = text.index("static int ddr_start_transfer")
    end = text.index("static void ddr_service", start)
    transfer = text[start:end]
    assert transfer.index("XAxiDma_SimpleTransfer") < transfer.index("dma_stream_ctrl(1")
    assert transfer.index("DDR_BUFFER_ADDR") >= 0


def test_sdk_ddr_start_does_not_use_frame_done_as_dma_completion():
    text = _text()
    start = text.index("static int ddr_start_transfer")
    stop = text.index("static void ddr_service", start)
    transfer = text[start:stop]
    assert "play_frame_done" not in transfer
    assert "DMA" in transfer


def test_sdk_ddr_stop_disables_stream_resets_dma_and_clears_error():
    text = _text()
    start = text.index("static int ddr_stop_transfer")
    end = text.index("static int ddr_start_transfer", start)
    stop = text[start:end]
    assert stop.index("dma_stream_ctrl(0") < stop.index("XAxiDma_Reset")
    assert 'ddr_error_code = 0;' in stop


def test_sdk_ddr_ioc_enters_drain_without_stopping_stream():
    text = _text()
    start = text.index("static void ddr_service")
    service = text[start:text.index("#endif", start)]
    ioc = service.index("XAXIDMA_IRQ_IOC_MASK")
    timeout = service.index("DMA_TIMEOUT_TICKS", ioc)
    ioc_path = service[ioc:timeout]
    assert 'ddr_dma_state = "DRAIN"' in ioc_path
    assert "ddr_active = 0" not in ioc_path
    assert "dma_stream_ctrl(0, 0)" not in ioc_path


def test_sdk_ddr_completion_waits_for_pl_stream_done():
    text = _text()
    assert "USER_STATUS_STREAM_DONE" in text
    assert "user_stream_done" in text
    start = text.index("static void ddr_service")
    service = text[start:text.index("#endif", start)]
    assert "read_user_status()" in service
    completion = service.index('strcmp(ddr_dma_state, "DRAIN") == 0')
    ioc = service.index('strcmp(ddr_dma_state, "RUNNING") == 0', completion)
    completion_path = service[completion:ioc]
    assert "user_stream_done" in completion_path
    assert "dma_stream_ctrl(0, 0)" in completion_path
    assert "ddr_active = 0" in completion_path
    assert 'ddr_dma_state = "READY"' in completion_path


def test_sdk_ddr_start_rejects_payload_above_dma_simple_limit():
    text = _text()
    start = text.index("static int ddr_start_transfer")
    end = text.index("static void ddr_service", start)
    transfer = text[start:end]
    assert "bytes > DMA_MAX_TRANSFER_BYTES" in transfer


def test_sdk_ddr_service_times_out_stalled_dma():
    text = _text()
    assert "ddr_started_at" in text
    start = text.index("static void ddr_service")
    service = text[start:text.index("#endif", start)]
    assert "DMA_TIMEOUT_TICKS" in service
    assert "ddr_stop_transfer()" in service


def test_sdk_rx_buffer_accepts_maximum_ascii_ddr_chunk_line():
    text = _text()
    marker = "#define RX_BUF_MAX"
    value = text[text.index(marker):].splitlines()[0].split()[-1]
    assert int(value) >= 68


def test_bd_fifo_reset_gate_holds_fifo_in_reset_when_stream_disabled():
    text = (ROOT / "scripts" / "create_bd.tcl").read_text(encoding="utf-8")
    gate_start = text.index("set fifo_run_enable_gate")
    gate_end = text.index("# ---- wrap up", gate_start)
    gate = text[gate_start:gate_end]
    assert "CONFIG.C_OPERATION {or}" in gate
    assert "CONFIG.C_OPERATION {and}" in gate
    assert "CONFIG.C_SIZE {1}" in gate
    assert "rst_ps7/peripheral_aresetn" in gate
    assert "stream_enable_slice/dout" in gate
    assert "dma_benchmark_enable_slice/dout" in gate
    assert "fifo_reset_gate/Res" in gate
    assert "axis_data_fifo_0/s_axis_aresetn" in gate
    assert "leaving the awg_top stream disabled" in text
    assert (
        "connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] "
        "[get_bd_pins axis_data_fifo_0/s_axis_aresetn]"
    ) not in gate


def test_ddr_stream_done_is_synchronized_into_status_clock_domain():
    text = (ROOT / "src" / "rtl" / "awg_top.v").read_text(encoding="utf-8")
    assert "user_stream_done_status_meta" in text
    assert "user_stream_done_status" in text
    status_block = text[text.index("always @(posedge status_clk"):text.index("assign play_status_out", text.index("always @(posedge status_clk"))]
    assert "user_stream_done_status_meta <= user_stream_done_r" in status_block
    assert "user_stream_done_status <= user_stream_done_status_meta" in status_block
    gpio = text[text.index("assign gpio14_out"):text.index("function signed", text.index("assign gpio14_out"))]
    assert "user_stream_done_status" in gpio
    assert "user_stream_done_r" not in gpio
