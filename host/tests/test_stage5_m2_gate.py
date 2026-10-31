from pathlib import Path


ROOT = Path(__file__).parents[2]
GATE_SOURCE = ROOT / "scripts" / "dma_benchmark_gate.py"


def test_stage5_m2_gate_checks_benchmark_measurements_without_new_protocol():
    text = GATE_SOURCE.read_text(encoding="utf-8")
    for token in (
        "SET DMA BENCH",
        "DMA BENCH PASS",
        "FIRST_VALID_CYCLE",
        "FIFO_MIN",
        "FIFO_MAX",
        "DMA_BENCH_BYTES",
        "DMA_BENCH_STATUS_ERROR",
        "DMA_BENCH_STATUS_TLAST",
    ):
        assert token in text
    assert "SET DDR PP TEST" not in text


def test_stage5_m2_gate_rejects_missing_first_beat_or_invalid_fifo_range():
    text = GATE_SOURCE.read_text(encoding="utf-8")
    assert "first_valid_cycle <= 0" in text
    assert "fifo_min < 0" in text
    assert "fifo_min > fifo_max" in text
    assert "fifo_max > FIFO_DEPTH" in text
