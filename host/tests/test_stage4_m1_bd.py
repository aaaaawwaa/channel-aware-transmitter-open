import re
from pathlib import Path


BD_SCRIPT = Path(__file__).parents[2] / "scripts" / "create_bd.tcl"


def _num_mi(text):
    """(declared NUM_MI, number of Mxx_AXI entries in the slave table).

    The assertion used to be the literal ``CONFIG.NUM_MI {16}``, which stayed 16
    across several additions and so could not fail.  Deriving it from the same
    file is not circular: the two can disagree, and that is the failure worth
    catching -- too few MIs and the ``connect_bd_intf_net`` calls name pins that
    do not exist, too many and the extra slaves are never connected.
    """
    line = next(l for l in text.splitlines()
                if "CONFIG.NUM_MI" in l and "$axiint" in l)
    declared = int(re.search(r"CONFIG\.NUM_MI\s+\{(\d+)\}", line).group(1))
    table = re.search(r"foreach \{mi slave\} \{(.*?)\}\s*\{", text, re.S).group(1)
    return declared, len(re.findall(r"\bM\d\d_AXI\b", table))


def _has_net(text, source, sink):
    return re.search(
        rf"connect_bd_net\s+\[get_bd_(?:pins|ports)\s+{re.escape(source)}\]"
        rf"\s+\[get_bd_(?:pins|ports)\s+{re.escape(sink)}\]",
        text,
    ) is not None


def test_m1_bd_declares_mm2s_dma_and_async_fifo():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    assert "xilinx.com:ip:axi_dma:7.1" in text
    assert "CONFIG.c_include_sg {0}" in text
    assert "CONFIG.c_include_s2mm {0}" in text
    assert "CONFIG.c_include_mm2s {1}" in text
    assert "CONFIG.c_sg_length_width {23}" in text
    assert "xilinx.com:ip:axis_data_fifo:2.0" in text
    assert "CONFIG.IS_ACLK_ASYNC {1}" in text


def test_m1_bd_expands_gp_interconnect_and_connects_stream_path():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    declared, wired = _num_mi(text)
    assert declared == wired
    assert "M12_AXI axi_dma_0/S_AXI_LITE" in text
    assert "M14_AXI axi_gpio_12/S_AXI" in text
    assert "M15_AXI axi_bram_ctrl_1/S_AXI" in text
    # Pinned explicitly in bd_addr_map rather than left to assign_bd_address, which
    # is heuristic (measured 2026-09-21: adding the RF BRAMs relocated
    # axi_bram_ctrl_0 by 32 MiB, with no warning).
    assert re.search(r"^\s*axi_bram_ctrl_1\s+0x[0-9A-Fa-f]+\s+\S+\s*$", text, re.M)
    assert "axi_dma_0/M_AXIS_MM2S" in text
    assert "axis_data_fifo_0/S_AXIS" in text
    assert "axis_dma_benchmark_sink_0/s_axis_tdata" in text
    assert "axis_dma_benchmark_sink_0/m_axis_tdata" in text
    assert "awg_top_0/user_stream_tvalid" in text
    assert "awg_top_0/user_stream_tlast" in text
    assert "ps7_0/S_AXI_HP0" in text


def test_m1_stream_control_uses_existing_user_gpio_second_channel():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    assert "CONFIG.C_IS_DUAL {1}" in text
    assert "CONFIG.C_GPIO2_WIDTH {32}" in text
    assert "stream_enable_slice" in text
    assert "axi_gpio_7/gpio2_io_o" in text


def test_m1_fifo_reset_is_gated_by_stream_enable():
    text = BD_SCRIPT.read_text(encoding="utf-8")
    gate_start = text.index("set fifo_run_enable_gate")
    gate_end = text.index("# ---- wrap up", gate_start)
    gate = text[gate_start:gate_end]
    assert "xilinx.com:ip:util_vector_logic:2.0" in gate
    assert "fifo_reset_gate" in gate
    assert re.search(r"CONFIG\.C_OPERATION\s+\{and\}", gate)
    assert re.search(r"CONFIG\.C_SIZE\s+\{1\}", gate)
    for source, sink in (
        ("rst_ps7/peripheral_aresetn", "fifo_reset_gate/Op1"),
        ("fifo_run_enable_gate/Res", "fifo_reset_gate/Op2"),
        ("fifo_reset_gate/Res", "axis_data_fifo_0/s_axis_aresetn"),
    ):
        assert _has_net(gate, source, sink)
    assert _has_net(gate, "stream_enable_slice/dout", "fifo_run_enable_gate/Op1")
    assert _has_net(gate, "dma_benchmark_enable_slice/dout", "fifo_run_enable_gate/Op2")

    assert _has_net(text, "ps7_0/FCLK_CLK0", "axis_data_fifo_0/s_axis_aclk")
    assert _has_net(text, "sys_clk", "axis_data_fifo_0/m_axis_aclk")
    for source, sink in (
        ("rst_ps7/peripheral_aresetn", "axi_dma_0/axi_resetn"),
        ("rst_ps7/peripheral_aresetn", "sc_dma_hp0/ARESETN"),
        ("rst_ps7/interconnect_aresetn", "axi_interconnect_0/ARESETN"),
    ):
        assert _has_net(text, source, sink)
    assert not _has_net(
        text,
        "rst_ps7/peripheral_aresetn",
        "axis_data_fifo_0/s_axis_aresetn",
    )


def test_every_awg_top_port_is_connected_or_deliberately_open():
    """awg_top 的每个 BD 端口要么连了, 要么是**有意**留空的。

    2026-09-23 排查"DAC 恒在中点"时, 一度怀疑过 `rst_n` 悬空被 Vivado 当成常量 0,
    于是永久驻在复位。**该假设已被证伪**: `awg_top.v` 根本没有 `rst_n` 端口 ——
    它的复位是模块内部的上电计数器(`wire rst_n = rst_cnt[19]`, 2^19 @ 50MHz
    ≈ 10.5ms 后恒释放), 打算接外部复位反而是引用不存在的引脚、会让
    `create_bd.tcl` 直接报错。

    所以这里不断言任何一条具体连接(那会随设计演进而过期), 而是断言那个真正值得
    守住的结论: 没有输入端口被漏接。`led` 是唯一有意留空的输出 —— 见脚本里
    "LED: 合流后由 PS 侧驱动" 那段。
    """
    text = BD_SCRIPT.read_text(encoding="utf-8")
    source = (BD_SCRIPT.parents[1] / "src" / "rtl" / "awg_top.v").read_text(
        encoding="utf-8")
    connected = set(re.findall(r"awg_top_0/(\w+)", text))

    exports = source[source.index("module awg_top"):]
    exports = exports[:exports.index(");")]
    for direction, name in re.findall(
            r"^\s*(input|output)\s+(?:wire\s+)?(?:\[[^\]]*\]\s*)?(\w+)",
            exports, re.M):
        if direction == "output" and name == "led":
            continue                  # 有意留给 PS 侧的 axi_gpio_led
        assert name in connected, (
            "awg_top 的 %s 端口 %s 没有在 create_bd.tcl 里连接" % (direction, name))
