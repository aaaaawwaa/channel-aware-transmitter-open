import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
SDK_MAIN = ROOT / "sdk" / "src" / "main.c"


FAULTS = {
    "STAGE5_FAULT_DMA_ERROR": "0x23u",
    "STAGE5_FAULT_RESET_TIMEOUT": "0x21u",
    "STAGE5_FAULT_STREAM_UNDERFLOW": "0x25u",
    "STAGE5_FAULT_TIMEOUT": "0x24u",
}


def _text():
    return SDK_MAIN.read_text(encoding="utf-8")


def _section(text, start, end):
    return text[text.index(start) : text.index(end, text.index(start))]


def test_sdk_has_private_compile_time_fault_injection_seam():
    text = _text()
    assert "#if defined(STAGE5_FAULT_INJECT)" in text
    assert "#define STAGE5_FAULT_MODE" in text
    for fault in FAULTS:
        assert fault in text
    assert "STAGE5_FAULT_INJECT" not in _section(text, "static void do_line", "int main")


def test_fault_seam_drives_production_observations_not_parser_commands():
    text = _text()
    service = _section(text, "static void ddr_pp_service", "#endif\n#endif")
    reset_wait = _section(text, "static int dma_wait_reset", "static int dma_init")
    assert "stage5_fault_active(STAGE5_FAULT_DMA_ERROR)" in service
    assert "stage5_fault_active(STAGE5_FAULT_STREAM_UNDERFLOW)" in service
    assert "stage5_fault_active(STAGE5_FAULT_TIMEOUT)" in service
    assert "stage5_fault_active(STAGE5_FAULT_RESET_TIMEOUT)" in reset_wait
    assert "SET DDR PP TEST" not in text


def test_each_injected_fault_uses_frozen_error_and_stop_recovery_path():
    text = _text()
    service = _section(text, "static void ddr_pp_service", "#endif\n#endif")
    for fault, error in FAULTS.items():
        assert fault in service or fault == "STAGE5_FAULT_RESET_TIMEOUT"
        assert error in service
    assert service.count("ddr_pp_stop_transfer()") >= 2
    assert "ddr_pp_state = \"ERROR\"" in service


def test_pp_clear_preserves_committed_bank_metadata_after_fault_recovery():
    text = _text()
    clear = _section(text, "static int ddr_pp_clear_transfer", "static int ddr_pp_stop_transfer")
    assert "ddr_pp_stage_seen" in clear
    assert "ddr_pp_stage_bytes[0] = 0" in clear
    assert "ddr_pp_stage_bytes[1] = 0" in clear
    for field in ("ddr_pp_valid", "ddr_pp_bit_length", "ddr_pp_crc16"):
        # CLEAR must not mutate committed metadata.  Reading ddr_pp_valid to
        # re-arm the per-bank fresh flag is allowed; writing it is not.
        assert not re.search(rf"{field}\s*\[[^\]]*\]\s*=(?!=)", clear)


def test_repeated_pp_start_is_rejected_without_mutating_running_state():
    text = _text()
    parser = _section(text, "if (n == 4 && strcmp(tok[3], \"START\") == 0)", "if (n == 4 && strcmp(tok[3], \"STOP\") == 0)")
    assert "ddr_pp_active" in parser
    assert "ERR DDR PP START FAILED" in parser
    assert "ddr_pp_start_transfer(2u)" in parser


def test_timeout_fault_is_prioritized_before_completion_transitions():
    text = _text()
    service = _section(text, "static void ddr_pp_service", "#endif\n#endif")
    timeout_branch = service.index("} else if ((now - ddr_pp_started_at) > ddr_pp_timeout_ticks)")
    drain_branch = service.index('} else if (strcmp(ddr_pp_state, "DRAIN") == 0')
    running_branch = service.index('} else if (strcmp(ddr_pp_state, "RUNNING") == 0')
    assert timeout_branch < drain_branch
    assert timeout_branch < running_branch


def test_reset_timeout_seam_covers_reload_reset_after_active_flag_cleared():
    text = _text()
    reset_wait = _section(text, "static int dma_wait_reset", "static int dma_init")
    reload_service = _section(text, "} else if (strcmp(ddr_pp_state, \"DRAIN\")", "} else if (strcmp(ddr_pp_state, \"RUNNING\")")
    assert 'strcmp(ddr_pp_state, "DRAIN") == 0' in reset_wait
    assert reload_service.index("ddr_pp_active = 0") < reload_service.index("dma_wait_reset()")
