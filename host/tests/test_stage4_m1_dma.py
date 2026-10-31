from pathlib import Path


ROOT = Path(__file__).parents[2]
SDK_MAIN = ROOT / "sdk" / "src" / "main.c"
DMA_GATE = ROOT / "scripts" / "dma_gate.py"


def test_dma_firmware_declares_mm2s_simple_transfer_and_recovery():
    text = SDK_MAIN.read_text(encoding="utf-8")
    assert '#include "xaxidma.h"' in text
    assert "XAxiDma_SimpleTransfer" in text
    assert "XAXIDMA_IRQ_IOC_MASK" in text
    assert "XAxiDma_Reset" in text
    assert "SET DMA TEST" in text


def test_dma_smoke_restores_digmod_state_after_stream_test():
    text = SDK_MAIN.read_text(encoding="utf-8")
    assert "u32 saved_digmod_mode = digmod_mode;" in text
    assert "u32 saved_digmod_source = digmod_source;" in text
    assert "digmod_mode = saved_digmod_mode;" in text
    assert "digmod_source = saved_digmod_source;" in text


def test_dma_smoke_arms_transfer_before_enabling_stream():
    text = SDK_MAIN.read_text(encoding="utf-8")
    start = text.index("transfer_bytes = DMA_TEST_BYTES")
    end = text.index("dma_cleanup:", start)
    smoke = text[start:end]
    assert smoke.index("XAxiDma_SimpleTransfer") < smoke.index("dma_stream_ctrl(1")


def test_dma_gate_expects_pattern_backpressure_and_recovery_passes():
    text = DMA_GATE.read_text(encoding="utf-8")
    assert '"SET DMA TEST"' in text
    assert "DMA TEST PASS" in text
    assert "BACKPRESSURE PASS" in text
    assert "RECOVERY PASS" in text
    assert "GET DMA" in text
