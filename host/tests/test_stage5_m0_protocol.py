import pytest

import siggen.protocol as protocol
from siggen.protocol import (
    DDR_BUFFER_BYTES,
    DDR_CHUNK_MAX_BYTES,
    crc16_ccitt,
    crc8_atm,
    ddr_chunk_cmd,
    ddr_commit_cmd,
    ddr_clear_cmd,
    ddr_start_cmd,
    ddr_stop_cmd,
    get_ddr_cmd,
    parse_ddr_status,
)


PP_BANK_BYTES = 1 << 20
PP_MAX_BITS = PP_BANK_BYTES * 8
PP_CHUNK_MAX_BYTES = 20


def _pp(name):
    return getattr(protocol, name)


def test_ddr_pp_commands_use_independent_two_bank_namespace():
    payload = bytes(range(PP_CHUNK_MAX_BYTES))
    assert _pp("ddr_pp_clear_cmd")() == "SET DDR PP CLEAR"
    assert _pp("ddr_pp_chunk_cmd")(0, 4, payload) == (
        "SET DDR PP CHUNK 0 4 000102030405060708090A0B0C0D0E0F10111213 27"
    )
    assert _pp("ddr_pp_chunk_cmd")(1, 8, bytes((0xAA, 0x55, 0x00, 0xFF))) == (
        "SET DDR PP CHUNK 1 8 AA5500FF 74"
    )
    assert _pp("ddr_pp_commit_cmd")(0, 160, 0x5A74) == "SET DDR PP COMMIT 0 160 5A74"
    assert _pp("ddr_pp_commit_cmd")(1, 32, 0x1234) == "SET DDR PP COMMIT 1 32 1234"
    assert _pp("ddr_pp_start_cmd")() == "SET DDR PP START"
    assert _pp("ddr_pp_stop_cmd")() == "SET DDR PP STOP"
    assert _pp("get_ddr_pp_cmd")() == "GET DDR PP"


@pytest.mark.parametrize("bank", [-1, 2, True, "0"])
def test_ddr_pp_chunk_rejects_invalid_bank(bank):
    with pytest.raises(ValueError):
        _pp("ddr_pp_chunk_cmd")(bank, 0, b"x")


@pytest.mark.parametrize("offset", [-4, 2, PP_BANK_BYTES])
def test_ddr_pp_chunk_requires_aligned_in_range_offset(offset):
    with pytest.raises(ValueError):
        _pp("ddr_pp_chunk_cmd")(0, offset, b"x")




@pytest.mark.parametrize("payload", [b"x", bytes(5)])
def test_ddr_pp_chunk_requires_four_byte_payload(payload):
    with pytest.raises(ValueError):
        _pp("ddr_pp_chunk_cmd")(0, 0, payload)


def test_ddr_pp_chunk_rejects_empty_oversized_and_crossing_payloads():
    with pytest.raises(ValueError):
        _pp("ddr_pp_chunk_cmd")(0, 0, b"")
    with pytest.raises(ValueError):
        _pp("ddr_pp_chunk_cmd")(0, 0, bytes(PP_CHUNK_MAX_BYTES + 1))
    with pytest.raises(ValueError):
        _pp("ddr_pp_chunk_cmd")(1, PP_BANK_BYTES - 4, bytes(8))


@pytest.mark.parametrize("bit_length", [0, 8, 31, PP_MAX_BITS + 32])
def test_ddr_pp_commit_requires_dma_word_aligned_bit_length(bit_length):
    with pytest.raises(ValueError):
        _pp("ddr_pp_commit_cmd")(0, bit_length, 0)


@pytest.mark.parametrize("crc16", [-1, 0x10000, True])
def test_ddr_pp_commit_rejects_invalid_crc(crc16):
    with pytest.raises(ValueError):
        _pp("ddr_pp_commit_cmd")(1, 32, crc16)


def _pp_status_line(state="RUNNING", error="00", active="1", active_bank="0", next_bank="1"):
    return (
        f"DDR PP ACTIVE {active} ACTIVE_BANK {active_bank} NEXT_BANK {next_bank} "
        f"STATE {state} ERROR {error} "
        "BANK0 VALID 1 LENGTH 160 CRC 5A74 "
        "BANK1 VALID 1 LENGTH 32 CRC 1234"
    )


@pytest.mark.parametrize("state,error", [("RUNNING", "00"), ("DRAIN", "00"), ("ERROR", "23")])
def test_parse_ddr_pp_status_exposes_active_next_banks_and_lifecycle(state, error):
    status = _pp("parse_ddr_pp_status")(_pp_status_line(state=state, error=error))
    assert status == {
        "active": True,
        "active_bank": 0,
        "next_bank": 1,
        "state": state,
        "error_code": int(error, 16),
        "bank0": {"valid": True, "bit_length": 160, "crc16": 0x5A74},
        "bank1": {"valid": True, "bit_length": 32, "crc16": 0x1234},
    }


def test_parse_ddr_pp_status_accepts_idle_without_active_or_next_bank():
    status = _pp("parse_ddr_pp_status")(
        _pp_status_line(state="IDLE", active="0", active_bank="NONE", next_bank="NONE")
    )
    assert status["active"] is False
    assert status["active_bank"] is None
    assert status["next_bank"] is None
    assert status["state"] == "IDLE"


@pytest.mark.parametrize(
    "replacement",
    [
        ("ACTIVE_BANK 0", "ACTIVE_BANK 2"),
        ("STATE RUNNING", "STATE BROKEN"),
        ("BANK0 VALID 1", "BANK0 VALID 2"),
        ("BANK1 VALID 1 LENGTH 32", "BANK1 VALID 1 LENGTH 8"),
        ("ERROR 00", "ERROR GG"),
    ],
)
def test_parse_ddr_pp_status_rejects_invalid_fields(replacement):
    line = _pp_status_line().replace(*replacement)
    with pytest.raises(ValueError):
        _pp("parse_ddr_pp_status")(line)


def test_single_buffer_ddr_codec_and_status_remain_unchanged():
    payload = bytes(range(DDR_CHUNK_MAX_BYTES))
    assert DDR_BUFFER_BYTES == 1 << 20
    assert ddr_clear_cmd() == "SET DDR CLEAR"
    assert ddr_chunk_cmd(32, payload) == (
        "SET DDR CHUNK 32 000102030405060708090A0B0C0D0E0F10111213 27"
    )
    assert ddr_commit_cmd(160, 0x29B1) == "SET DDR COMMIT 160 29B1"
    assert ddr_start_cmd() == "SET DDR START"
    assert ddr_stop_cmd() == "SET DDR STOP"
    assert get_ddr_cmd() == "GET DDR"
    assert crc8_atm(payload) == 0x27
    assert crc16_ccitt(payload) == 0x5A74
    assert parse_ddr_status(
        "DDR VALID 1 BUFFER 00300000 MAX 1048576 LENGTH 160 CRC 29B1 "
        "UPLOAD IDLE ACTIVE 0 DMA READY ERROR 00"
    )["dma_state"] == "READY"
