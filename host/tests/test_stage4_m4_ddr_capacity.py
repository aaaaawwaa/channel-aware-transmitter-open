from pathlib import Path


ROOT = Path(__file__).parents[2]
SDK_MAIN = ROOT / "sdk" / "src" / "main.c"
CAPACITY_GATE = ROOT / "scripts" / "ddr_capacity_gate.py"
SKILL_DOCS = (
    ROOT / "fpga-agent-skills" / "skills" / "documentation" / "SKILL.md",
    ROOT.parent / "fpga-agent-skills" / "skills" / "documentation" / "SKILL.md",
)


def _sdk_text():
    return SDK_MAIN.read_text(encoding="utf-8")


def test_sdk_exposes_ddr_capacity_boundary_command():
    text = _sdk_text()
    assert 'strcmp(tok[2], "CAPACITY")' in text
    assert 'strcmp(tok[3], "TEST")' in text
    assert 'DDR CAPACITY PASS SIZE' in text


def test_sdk_capacity_test_checks_half_gib_alias_and_window_boundary():
    text = _sdk_text()
    start = text.index("static int ddr_capacity_test")
    end = text.index("static int ddr_run_test", start)
    capacity = text[start:end]
    assert "1u << 29" in capacity
    assert "DDR_HIGHADDR - 3u" in capacity
    assert "ddr_sync_range" in capacity
    assert "ALIAS512" in text


def test_capacity_gate_requires_detected_size_and_boundary_result():
    text = CAPACITY_GATE.read_text(encoding="utf-8")
    assert '"SET DDR CAPACITY TEST"' in text
    assert "DDR CAPACITY PASS SIZE" in text
    assert "ALIAS512" in text
    assert "BOUNDARY" in text


def test_capacity_command_does_not_run_while_upload_or_playback_is_active():
    text = _sdk_text()
    start = text.index('strcmp(tok[2], "CAPACITY")')
    end = text.index('strcmp(tok[2], "TEST")', start)
    command = text[start:end]
    assert "ddr_active" in command
    assert "ddr_uploading" in command




def test_ddr_test_refuses_while_upload_or_playback_is_active():
    text = _sdk_text()
    start = text.index('if (n == 3 && strcmp(tok[1], "DDR") == 0 && strcmp(tok[2], "TEST") == 0)')
    end = text.index("#endif", start)
    command = text[start:end]
    assert "ddr_active || ddr_uploading" in command
    assert 'uart_send("ERR DDR BUSY\\r\\n")' in command
