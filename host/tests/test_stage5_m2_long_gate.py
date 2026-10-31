from pathlib import Path


ROOT = Path(__file__).parents[2]
GATE_SOURCE = ROOT / "scripts" / "ddr_pp_long_gate.py"


def test_stage5_m2_long_gate_uses_frozen_pp_commands_and_full_bank_limit():
    text = GATE_SOURCE.read_text(encoding="utf-8")
    for token in (
        "SET DIGMOD MODE OFF",
        "ddr_pp_clear_cmd",
        "ddr_pp_chunk_cmd",
        "ddr_pp_commit_cmd",
        "ddr_pp_start_cmd",
        "GET DDR PP",
        "DDR_PP_BANK_BYTES",
        "crc16_ccitt",
        "PP PROFILE PASS",
        "LOW_PAYLOAD_BYTES",
        "profile_payload_size",
        "--group",
        "--upload-bank",
        "--reuse-upload",
    ):
        assert token in text
    assert "SET DDR PP TEST" not in text


def test_stage5_m2_long_gate_covers_modes_filters_and_rates():
    text = GATE_SOURCE.read_text(encoding="utf-8")
    for token in (
        '("BPSK", "RECT"',
        '("QPSK", "RRC"',
        '("QAM16", "RECT"',
        '("QAM64", "RRC"',
        "800_000",
        "1_000_000",
        "state_changes",
        "saw_active",
    ):
        assert token in text


def test_stage5_m2_long_gate_validates_committed_metadata_and_errors():
    text = GATE_SOURCE.read_text(encoding="utf-8")
    for token in (
        "b0_valid",
        "b1_valid",
        "b0_length",
        "b1_length",
        "b0_crc",
        "b1_crc",
        "status[\"error\"]",
        "profile %s timed out",
        "active_banks",
        "bank order mismatch",
    ):
        assert token in text
