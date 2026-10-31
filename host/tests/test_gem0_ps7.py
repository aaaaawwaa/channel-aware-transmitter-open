import re
from pathlib import Path
from typing import List


ROOT = Path(__file__).parents[2]
PS7 = (ROOT / "scripts" / "ps7_props.tcl").read_text(encoding="utf-8")
CREATE_BD = (ROOT / "scripts" / "create_bd.tcl").read_text(encoding="utf-8")


def _mio_tree(name: str) -> List[str]:
    match = re.search(rf"CONFIG\.{name} \{{([^}}]+)\}}", PS7)
    assert match, name
    return match.group(1).split("#")


def test_gem0_uses_board_rgmii_and_mdio_mios_without_ps_reset() -> None:
    required = (
        "CONFIG.PCW_ENET0_PERIPHERAL_ENABLE {1}",
        "CONFIG.PCW_EN_ENET0 {1}",
        "CONFIG.PCW_ENET0_ENET0_IO {MIO 16 .. 27}",
        "CONFIG.PCW_ENET0_GRP_MDIO_ENABLE {1}",
        "CONFIG.PCW_ENET0_GRP_MDIO_IO {MIO 52 .. 53}",
        "CONFIG.PCW_ENET0_RESET_ENABLE {0}",
        "CONFIG.PCW_ENET_RESET_ENABLE {0}",
        "CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1}",
        "CONFIG.PCW_UART1_UART1_IO {MIO 48 .. 49}",
    )

    for setting in required:
        assert setting in PS7


def test_gem0_mio_tree_preserves_board_rgmii_mdio_and_uart1_ownership() -> None:
    peripherals = _mio_tree("PCW_MIO_TREE_PERIPHERALS")
    signals = _mio_tree("PCW_MIO_TREE_SIGNALS")

    assert peripherals[16:28] == ["Ethernet 0"] * 12
    assert signals[16:28] == [
        "tx_clk",
        "txd[0]",
        "txd[1]",
        "txd[2]",
        "txd[3]",
        "tx_en",
        "rx_clk",
        "rxd[0]",
        "rxd[1]",
        "rxd[2]",
        "rxd[3]",
        "rx_dv",
    ]
    assert peripherals[52:54] == ["Ethernet 0", "Ethernet 0"]
    assert signals[52:54] == ["mdc", "mdio"]
    assert peripherals[48:50] == ["UART 1", "UART 1"]
    assert signals[48:50] == ["tx", "rx"]


def test_ps7_reset_controller_releases_unused_active_low_aux_reset() -> None:
    assert "get_bd_pins rst_aux_inactive/dout" in CREATE_BD
    assert "get_bd_pins rst_ps7/aux_reset_in" in CREATE_BD
    assert "connect_bd_net [get_bd_pins rst_aux_inactive/dout] [get_bd_pins rst_ps7/aux_reset_in]" in CREATE_BD
