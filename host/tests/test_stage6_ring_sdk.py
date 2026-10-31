from pathlib import Path

ROOT = Path(__file__).parents[2]
SDK_MAIN = ROOT / "sdk" / "src" / "main.c"


def _text():
    return SDK_MAIN.read_text(encoding="utf-8")


def _block(text, start_marker, end_marker):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_firmware_declares_per_bank_fresh_flag():
    assert "static u8 ddr_pp_bank_fresh[2];" in _text()


def test_clear_and_start_and_commit_manage_fresh():
    text = _text()
    clear = _block(text, "static int ddr_pp_clear_transfer",
                   "static int ddr_pp_stop_transfer")
    assert "ddr_pp_bank_fresh[0] = (u8)ddr_pp_valid[0]" in clear
    assert "ddr_pp_bank_fresh[1] = (u8)ddr_pp_valid[1]" in clear
    start = _block(text, "static int ddr_pp_start_transfer",
                   "static void ddr_pp_service")
    assert "ddr_pp_bank_fresh[bank] = 0" in start
    udp_commit = _block(text, "static int ddr_pp_udp_commit",
                        "static void ddr_pp_udp_abort")
    assert "ddr_pp_bank_fresh[bank] = 1" in udp_commit


def test_uart_commit_sets_fresh():
    text = _text()
    block = _block(text, 'strcmp(tok[3], "COMMIT") == 0',
                   'strcmp(tok[3], "START") == 0')
    assert "ddr_pp_bank_fresh[bank] = 1" in block


def test_begin_allows_non_active_bank_while_playing():
    text = _text()
    begin = _block(text, "static int ddr_pp_udp_begin",
                   "static int ddr_pp_udp_write")
    # while playing: reject only the active bank, digmod/play no longer block
    assert "if (ddr_pp_active)" in begin
    assert "if (bank == ddr_pp_active_bank)" in begin
    assert "else if (digmod_mode != 0u || play_blocks_config())" in begin
    # stopped-state rule must still reject digital-mod-on
    assert "ddr_pp_bank_fresh[bank] = 0" in begin


def test_drain_switches_on_fresh_else_underrun_else_idle():
    text = _text()
    service = _block(text, "static void ddr_pp_service",
                     "static void ddr_pp_flush_cpu_write_range")
    drain = service[service.index('strcmp(ddr_pp_state, "DRAIN") == 0'):
                    service.index('strcmp(ddr_pp_state, "RUNNING") == 0')]
    assert "ddr_pp_bank_fresh[other]" in drain
    assert "ddr_pp_stage_owner[other] == DDR_PP_STAGE_UDP" in drain
    assert "0x26u" in drain
    assert 'ddr_pp_state = "IDLE"' in drain


def test_reload_one_shot_flag_is_removed():
    text = _text()
    assert "ddr_pp_reload_used" not in text
    assert "ddr_pp_next_bank" not in text


def test_get_ddr_pp_next_bank_is_computed_from_fresh():
    text = _text()
    getter = _block(text, 'strcmp(tok[1], "DDR") == 0 && strcmp(tok[2], "PP") == 0',
                    'if (n == 2 && strcmp(tok[1], "DDR") == 0)')
    assert "ddr_pp_active_bank ^ 1u" in getter
    assert "ddr_pp_bank_fresh[" in getter


def test_udp_commit_and_abort_preserve_running_state_while_playing():
    # A ring refill COMMIT/ABORT happens while the other bank is RUNNING; the
    # service state machine must not be knocked out of RUNNING/DRAIN.
    text = _text()
    commit = _block(text, "static int ddr_pp_udp_commit",
                    "static void ddr_pp_udp_abort")
    abort = _block(text, "static void ddr_pp_udp_abort",
                   "static void ddr_pp_udp_status")
    assert "if (!ddr_pp_active)" in commit
    assert "if (!ddr_pp_active)" in abort
