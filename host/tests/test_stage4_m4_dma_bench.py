import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
BD_SCRIPT = ROOT / "scripts" / "create_bd.tcl"
SDK_SOURCE = ROOT / "sdk" / "src" / "main.c"
BENCH_SOURCE = ROOT / "src" / "rtl" / "axis_dma_benchmark_sink.v"
TB_SOURCE = ROOT / "src" / "tb" / "axis_dma_benchmark_sink_tb.v"


def _num_mi(text):
    """(declared NUM_MI, number of Mxx_AXI entries in the slave table).

    The literal ``CONFIG.NUM_MI {16}`` this replaced stayed 16 across several
    additions, so it could not fail.  The two numbers below can disagree, and
    that is the failure worth catching.
    """
    line = next(l for l in text.splitlines()
                if "CONFIG.NUM_MI" in l and "$axiint" in l)
    declared = int(re.search(r"CONFIG\.NUM_MI\s+\{(\d+)\}", line).group(1))
    table = re.search(r"foreach \{mi slave\} \{(.*?)\}\s*\{", text, re.S).group(1)
    return declared, len(re.findall(r"\bM\d\d_AXI\b", table))


def test_dma_benchmark_sink_has_no_backpressure_bypass_and_measurement_outputs():
    text = BENCH_SOURCE.read_text(encoding="utf-8")
    assert "module axis_dma_benchmark_sink" in text
    assert "status_clk" in text
    assert "benchmark_enable_meta_r" in text
    assert "bench_status_sync_r" in text
    assert "bench_checksum_sync_r" in text
    assert "bench_result_status_r" in text
    assert "bench_result_checksum_r" in text
    assert "bench_result_req_r" in text
    assert "bench_result_req_meta_r" in text
    assert "bench_result_req_sync_r" in text
    assert "bench_result_req_seen_r" in text
    assert "benchmark_enable" in text
    assert "s_axis_tready" in text
    assert "bench_status" in text
    assert "bench_checksum" in text
    assert "crc32_byte" in text


def test_dma_benchmark_bd_keeps_legacy_path_and_adds_status_gpio():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    assert "axis_dma_benchmark_sink" in text
    assert "dma_benchmark_enable_slice" in text
    assert "M13_AXI axi_gpio_11/S_AXI" in text
    assert _num_mi(text)[0] == _num_mi(text)[1]
    assert "axi_gpio_11/gpio_io_i" in text
    assert "axis_dma_benchmark_sink_0/status_clk" in text
    assert "axis_dma_benchmark_sink_0/m_axis_tdata" in text
    assert "axis_dma_benchmark_sink_0/bench_status" in text
    assert "axis_dma_benchmark_sink_0/bench_checksum" in text


def test_sdk_exposes_benchmark_command_and_verifies_sink_measurements():
    text = SDK_SOURCE.read_text(encoding="utf-8")
    assert "SET DMA BENCH" in text
    assert "DMA BENCH PASS" in text
    assert "XPAR_AXI_GPIO_11_BASEADDR" in text
    assert "XGpio_DiscreteRead(&DmaBenchStatusGpio, 1)" in text
    assert "XGpio_DiscreteRead(&DmaBenchStatusGpio, 2)" in text
    assert "DMA_BENCH_STATUS_COUNT_MASK" in text


def test_dma_benchmark_bd_exposes_m2_fifo_read_count_to_sink():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    assert "CONFIG.HAS_RD_DATA_COUNT {1}" in text
    assert "fifo_rd_count_slice" in text
    assert "CONFIG.DIN_FROM {12}" in text
    assert "axis_data_fifo_0/axis_rd_data_count" in text
    assert "axis_dma_benchmark_sink_0/fifo_rd_count" in text
    assert _num_mi(text)[0] == _num_mi(text)[1]


def test_dma_benchmark_sink_tracks_first_valid_beat_and_fifo_watermarks():
    text = BENCH_SOURCE.read_text(encoding="utf-8")
    for token in (
        "fifo_rd_count",
        "first_valid_cycle",
        "fifo_min_count",
        "fifo_max_count",
    ):
        assert token in text


def test_dma_benchmark_tb_asserts_first_beat_and_watermark_observations():
    text = TB_SOURCE.read_text(encoding="utf-8")
    for token in (
        "first_valid_cycle",
        "fifo_min_count",
        "fifo_max_count",
        "reload_gap_cycles",
    ):
        assert token in text


def test_sdk_declares_stage5_m2_reload_timeline_without_changing_pp_status():
    text = SDK_SOURCE.read_text(encoding="utf-8")
    for token in (
        "DDR_PP_TIMELINE",
        "IOC_TICKS",
        "DRAIN_TICKS",
        "NEXT_SUBMIT_TICKS",
        "FIRST_BEAT_TICKS",
        "GAP_TICKS",
        "FIFO_MIN",
        "FIFO_MAX",
    ):
        assert token in text


def test_dma_benchmark_measurements_reuse_existing_status_gpio_channels():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    assert "CONFIG.C_IS_DUAL {1}" in text
    assert "CONFIG.C_GPIO2_WIDTH {32}" in text
    assert "CONFIG.C_ALL_INPUTS_2 {1}" in text
    assert "axi_gpio_8/gpio2_io_i" in text
    assert "axis_dma_benchmark_sink_0/bench_first_valid_cycle" in text
    assert "axi_gpio_10/gpio2_io_i" in text
    assert "axis_dma_benchmark_sink_0/bench_fifo_watermark" in text
    assert _num_mi(text)[0] == _num_mi(text)[1]


def test_dma_benchmark_bd_refreshes_module_ports_before_creation():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    assert "update_compile_order -fileset sources_1" in text


def test_dma_benchmark_observation_connections_follow_sink_creation():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    sink_creation = text.index(
        "create_bd_cell -type module -reference axis_dma_benchmark_sink"
    )
    first_cycle_connection = text.index(
        "axis_dma_benchmark_sink_0/bench_first_valid_cycle"
    )
    fifo_watermark_connection = text.index(
        "axis_dma_benchmark_sink_0/bench_fifo_watermark"
    )
    assert sink_creation < first_cycle_connection < fifo_watermark_connection


def test_dma_benchmark_sink_latches_measurements_with_result_handshake():
    text = BENCH_SOURCE.read_text(encoding="utf-8")
    for token in (
        "bench_result_first_valid_cycle_r",
        "bench_result_fifo_min_count_r",
        "bench_result_fifo_max_count_r",
        "bench_first_valid_cycle_sync_r",
        "bench_fifo_min_count_sync_r",
        "bench_fifo_max_count_sync_r",
        "bench_fifo_watermark",
    ):
        assert token in text


def test_sdk_reads_dma_benchmark_first_beat_and_fifo_watermarks():
    text = SDK_SOURCE.read_text(encoding="utf-8")
    for token in (
        "XGpio_DiscreteRead(&UserStatusGpio, 2)",
        "XGpio_DiscreteRead(&PlayStatusGpio, 2)",
        "FIRST_VALID_CYCLE",
        "FIFO_MIN",
        "FIFO_MAX",
    ):
        assert token in text
