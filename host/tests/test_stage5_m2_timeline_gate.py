from pathlib import Path


ROOT = Path(__file__).parents[2]
GATE_SOURCE = ROOT / "scripts" / "ddr_pp_timeline_gate.py"


def test_stage5_m2_timeline_gate_uses_existing_readback_without_new_protocol():
    text = GATE_SOURCE.read_text(encoding="utf-8")
    for token in (
        "SET DMA BENCH",
        "DMA BENCH PASS",
        "DDR_PP_TIMELINE",
        "NEXT_SUBMIT_TICKS",
        "IOC_TICKS",
        "FIRST_BEAT_TICKS",
        "base.ddr_pp_start_cmd()",
        "base.reset_profile_state",
    ):
        assert token in text
    assert "SET DDR PP TEST" not in text


def test_stage5_m2_timeline_gate_keeps_honest_measurement_boundaries():
    text = GATE_SOURCE.read_text(encoding="utf-8")
    # The gate must not dress the PS-tick readback up as a hardware gap.
    assert "not re-verified at playback" in text
    assert "do NOT read it as a hardware" in text
    assert "timing/order evidence only" in text
    # The two reload samples used on the board were the 1 MSym/s 1 MiB profiles.
    assert '"QAM16", "RECT"' in text
    assert '"QAM64", "RRC"' in text
    assert "1_000_000" in text


def test_stage5_m2_timeline_gate_asserts_bank_order_and_population():
    text = GATE_SOURCE.read_text(encoding="utf-8")
    assert 'active_banks != ["0", "1"]' in text
    assert 'tl["next_submit_ticks"] and tl["ioc_ticks"]' in text
    assert "both committed banks required" in text
