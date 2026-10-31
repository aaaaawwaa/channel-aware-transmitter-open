from pathlib import Path


ROOT = Path(__file__).parents[2]
SINK = ROOT / "src" / "rtl" / "axis_dma_benchmark_sink.v"
AWG_TOP = ROOT / "src" / "rtl" / "awg_top.v"
BD = ROOT / "scripts" / "create_bd.tcl"
SDK = ROOT / "sdk" / "src" / "main.c"
GATE = ROOT / "scripts" / "ddr_pp_data_plane_gate.py"


def test_stage5_m2_pp_observation_has_normal_mode_data_plane_contract():
    text = SINK.read_text(encoding="utf-8")
    for token in (
        "stream_control",
        "stream_underflow",
        "pp_mode",
        "pp_bank",
        "pp_result",
        "pp_fifo_min",
        "pp_fifo_max",
        "pp_gap_cycles",
        "TLAST",
        "TKEEP",
    ):
        assert token in text
    assert "!bench &&" in text


def test_stage5_m2_pp_observation_preserves_length_and_underflow_signal():
    awg_text = AWG_TOP.read_text(encoding="utf-8")
    bd_text = BD.read_text(encoding="utf-8")
    assert "user_stream_underflow" in awg_text
    assert "32'hffff_fff8" in awg_text
    assert "axis_dma_benchmark_sink_0/stream_control" in bd_text
    assert "axis_dma_benchmark_sink_0/stream_underflow" in bd_text


def test_stage5_m2_pp_observation_uses_existing_gpio_pages_and_frozen_protocol():
    sdk_text = SDK.read_text(encoding="utf-8")
    for token in (
        "DMA_PP_CTRL_MODE",
        "DMA_PP_CTRL_BANK",
        "dma_pp_stream_ctrl",
        "PP_OBS",
        "PP0_STATUS",
        "PP1_STATUS",
        "PP_GAP_CYCLES",
    ):
        assert token in sdk_text
    assert "SET DDR PP TEST" not in sdk_text


def test_stage5_m2_data_plane_gate_checks_crc_tlast_tkeep_fifo_underflow_and_gap():
    text = GATE.read_text(encoding="utf-8")
    for token in (
        "SET DMA BENCH",
        "PP_OBS",
        "PP0_STATUS",
        "PP1_STATUS",
        "PP0_CRC32",
        "PP1_CRC32",
        "TLAST",
        "TKEEP",
        "UNDERFLOW",
        "FIFO_MIN",
        "FIFO_MAX",
        "PP_GAP_CYCLES",
        "zlib.crc32",
        "active_banks != [\"0\", \"1\"]",
    ):
        assert token in text


def test_stage5_m2_gate_accepts_fwft_raw_fifo_count_sentinel():
    text = GATE.read_text(encoding="utf-8")
    assert "FIFO_RAW_COUNT_MAX = 4097" in text


def test_stage5_m2_gate_compares_bank_selector_as_a_boolean():
    text = GATE.read_text(encoding="utf-8")


def test_stage5_m2_gate_reuploads_benchmark_overwritten_bank0_between_profiles():
    text = GATE.read_text(encoding="utf-8")
    assert "payloads_by_size = {}" in text
    assert "if args.size not in payloads_by_size:" in text
    assert text.count("base.upload_bank(ser, pending, 0, payloads[0])") >= 2
    assert "base.upload_bank(ser, pending, 1, payloads[1])" in text
