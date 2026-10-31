from pathlib import Path


ROOT = Path(__file__).parents[2]
GATE = ROOT / "scripts" / "stage5_fault_gate.py"


FAULTS = {
    "DMA_ERROR": "23",
    "RESET_TIMEOUT": "21",
    "STREAM_UNDERFLOW": "25",
    "TIMEOUT": "24",
}


def _text():
    return GATE.read_text(encoding="utf-8")


def test_m1_gate_declares_all_compile_time_fault_modes_and_codes():
    text = _text()
    for mode, error in FAULTS.items():
        assert mode in text
        assert error in text
    assert "--fault" in text
    assert "FAULTS" in text


def test_m1_gate_uses_only_frozen_pp_commands_without_public_test_command():
    text = _text()
    for command in (
        "SET DDR PP CLEAR",
        "SET DDR PP CHUNK",
        "SET DDR PP COMMIT",
        "SET DDR PP START",
        "SET DDR PP STOP",
        "GET DDR PP",
    ):
        assert command in text
    assert "SET DDR PP TEST" not in text


def test_m1_gate_requires_error_then_stop_and_restart_recovery():
    text = _text()
    assert "FAULT INJECTED" in text
    assert "FAULT RECOVERED" in text
    assert "SET DDR PP STOP" in text
    assert "SET DDR PP START" in text
    assert 'stopped["state"] != "IDLE"' in text
    assert 'stopped["error"]' in text


def test_m1_gate_records_active_bram_metadata_before_and_after_fault():
    text = _text()
    assert "GET USERBUF" in text
    assert "VALID" in text
    assert "CRC" in text
    assert "before_user" in text


def test_m1_gate_probes_bridge_when_startup_banner_was_already_consumed():
    text = _text()
    wait_ready = text[text.index("def wait_ready"):text.index("def user_metadata")]
    assert 'ser.write(b"GET USERBUF\\r\\n")' in wait_ready
    assert 'line.startswith("USERBUF ")' in wait_ready
