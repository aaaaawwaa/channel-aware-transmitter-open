"""Source-text assertions for the channel-impairment serial protocol (Task 9).

The HDF/BSP for the new AXI GPIO 12 / BRAM Ctrl 1 peripherals does not exist
yet (deferred, ruling R9), so the PS application cannot be compiled here.
These tests pin the firmware source contract instead: the exact command
tokens, the dual AXI GPIO control-word writes, the tap-table BRAM writes and
the 24-token ``GET CHx CHAN`` wire format the host parser depends on.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
SDK_MAIN = ROOT / "sdk" / "src" / "main.c"


def _text():
    return SDK_MAIN.read_text(encoding="utf-8")


def test_channel_command_tokens_are_parsed():
    text = _text()
    for token in ('"CHAN"', '"NOISE"', '"TAP"', '"COMMIT"', '"CLEAR"'):
        assert token in text, f"missing command token {token}"
    # command branch ordering: CHAN / NOISE / TAP must all appear in SET CHx
    set_idx = text.index('strcmp(tok[1], "CH1") != 0 && strcmp(tok[1], "CH2") != 0', text.index('strcmp(tok[0], "SET") == 0'))
    chan_idx = text.index('strcmp(tok[2], "CHAN") == 0')
    assert chan_idx < set_idx, "channel commands must precede the generic CHx guard"
    assert text.index('strcmp(tok[2], "NOISE") == 0') < set_idx
    assert text.index('strcmp(tok[2], "TAP") == 0') < set_idx


def test_channel_state_is_kept_for_get_readback():
    text = _text()
    assert "chan_word[2]" in text
    assert "chan_tap[2][4]" in text
    # control word layout documented: bit0 en, [7:1] noise, bit8 commit epoch
    assert "bit0" in text and "commit" in text
    # noise packing / commit epoch toggle
    assert "(v & 0x7Fu) << 1" in text
    assert "(1u << 8)" in text


def test_dual_axi_gpio_control_word_writes():
    text = _text()
    assert "static XGpio    Gpio12;" in text
    assert "CHAN_CTRL_BASEADDR" in text
    assert "XGpio_Initialize(&Gpio12, CHAN_CTRL_BASEADDR)" in text
    # R3/R4: two separate 32-bit words, one per channel
    assert "XGpio_DiscreteWrite(&Gpio12, 1, chan_word[0]" in text
    assert "XGpio_DiscreteWrite(&Gpio12, 2, chan_word[1]" in text


def test_tap_table_written_to_separate_bram():
    text = _text()
    assert "CHAN_TAP_BRAM_BASEADDR" in text
    assert "XPAR_AXI_BRAM_CTRL_1_BASEADDR" in text
    # fall back to 0 so the file still builds against an older BSP
    assert "#ifndef XPAR_AXI_GPIO_12_BASEADDR" in text
    assert "Xil_Out32(base + k * 4u, chan_tap[ci][k])" in text
    assert "static void write_chan_tap(int ci)" in text
    # CH1 taps at word 0..3, CH2 at 4..7
    assert "CHAN_TAP_BRAM_BASEADDR + (u32)ci * 4u * 4u" in text


def test_channel_commands_report_the_required_errors():
    text = _text()
    assert "ERR NOISE OUT OF RANGE" in text
    assert "ERR TAP OUT OF RANGE" in text
    # bad tap index and bad ON|OFF reuse the generic bad-value error
    assert "ERR BAD VALUE" in text
    assert 'g < -256 || g > 255 || d < 0 || d > 511' in text
    assert "k > 3u" in text


def test_tap_clear_zeroes_all_taps_not_passthrough():
    # R6 deviation from the brief: TAP CLEAR == "clear all taps" (silent FIR),
    # never passthrough (that is SET CHx CHAN OFF).
    text = _text()
    tap_idx = text.index('strcmp(tok[2], "TAP") == 0', text.index('strcmp(tok[0], "SET") == 0'))
    clear_idx = text.index('strcmp(tok[3], "CLEAR") == 0)', tap_idx)
    block = text[clear_idx : clear_idx + 420]
    assert "chan_tap[cc][k] = 0" in block
    assert "9u << 9" not in block, "brief's passthrough seed is a spec bug"


def test_get_chx_chan_emits_twenty_four_tokens():
    text = _text()
    # the format is split over two adjacent C literals; join them before matching
    joined = re.sub(r'"\s*\n\s*"', "", text)
    fmt = ("CH%d CHAN %s NOISE %lu TONE %lu %lu IMPULSE %lu %lu %ld "
           "TAP0 %ld %lu TAP1 %ld %lu TAP2 %ld %lu TAP3 %ld %lu\\r\\n")
    assert fmt in joined, "GET CHx CHAN format string must be exactly 24 tokens"
    assert "static long sign9(u32 v)" in text
    # must be handled before the plain GET CHx branch so it never changes it
    get_idx = text.index("CH%d WAVE %s FREQ")
    chan_get_idx = text.index('strcmp(tok[2], "CHAN") == 0', text.index('strcmp(tok[0], "GET") == 0'))
    assert chan_get_idx < get_idx


def test_channel_state_is_initialized_and_written_once():
    text = _text()
    assert "static u32 chan_word[2] = {0, 0};" in text
    assert "static u32 chan_tap[2][4] = {{0}};" in text
    main_body = text[text.index("int main(void)") :]
    assert "write_chan_ctrl();" in main_body
