import pytest

from host.siggen.protocol import crc16_ccitt
from host.siggen.udp_ddr_pp import (
    MessageType,
    ReplyError,
    ReplyState,
    ReplyStatus,
    UploadResult,
    crc32,
)
from scripts.udp_ddr_pp_gate import (
    build_m1_data_packets,
    build_m1_positive_sequence,
    make_m1_payload,
    make_m2_report,
    non_target_metadata,
    parse_net_status,
    run_m0,
)


def _fast_crc16(data):
    """Table CCITT-FALSE CRC-16; must equal protocol.crc16_ccitt but is O(1)/byte
    so the M3 1 MiB metadata fixtures stay fast. Pinned by a test below."""
    table = getattr(_fast_crc16, "_table", None)
    if table is None:
        table = []
        for n in range(256):
            r = n << 8
            for _ in range(8):
                r = ((r << 1) ^ 0x1021) if (r & 0x8000) else (r << 1)
            table.append(r & 0xffff)
        _fast_crc16._table = table
    crc = 0xffff
    for byte in bytes(data):
        crc = ((crc << 8) ^ table[((crc >> 8) ^ byte) & 0xff]) & 0xffff
    return crc


def test_fast_crc16_matches_protocol_reference():
    from scripts.udp_ddr_pp_gate import _crc16_ccitt_fast

    samples = [b"", b"a", b"hello", bytes(range(256)) * 3, bytes((i * 7) & 0xFF for i in range(8192))]
    for data in samples:
        assert _fast_crc16(data) == crc16_ccitt(data)
        assert _crc16_ccitt_fast(data) == crc16_ccitt(data)


def test_m0_requires_arp_echo_and_uart_net_status():
    status = ReplyStatus(ReplyState.IDLE, ReplyError.NONE, 0, 0, 0)

    class FakeClient:
        def echo(self):
            return status

    result = run_m0(
        FakeClient(),
        lambda host: "02-ac-82-00-00-02",
        lambda: "NET GEM0 INIT 1 LINK UP IP 192.0.2.10 UDP 40100",
    )

    assert result["arp"] == "192.0.2.10"
    assert result["echo"].error is ReplyError.NONE
    assert result["net"]["init"] == 1
    assert result["net"]["link"] == "UP"


def test_m0_sends_echo_before_reading_arp_neighbor():
    events = []
    status = ReplyStatus(ReplyState.IDLE, ReplyError.NONE, 0, 0, 0)

    class FakeClient:
        def echo(self):
            events.append("echo")
            return status

    run_m0(
        FakeClient(),
        lambda _host: events.append("arp") or "02-ac-82-00-00-02",
        None,
    )

    assert events == ["echo", "arp"]


def test_m0_rejects_missing_arp_neighbor():
    status = ReplyStatus(ReplyState.IDLE, ReplyError.NONE, 0, 0, 0)

    class FakeClient:
        def echo(self):
            return status

    with pytest.raises(RuntimeError, match="ARP"):
        run_m0(FakeClient(), lambda _host: "", None)


def test_m2_reports_effective_rate_and_retransmits():
    report = make_m2_report(
        bytes_sent=1 << 20,
        elapsed_s=0.2,
        retransmits=3,
        payload_crc32=0x12345678,
        device_crc32=0x12345678,
        device_error=0,
    )

    assert report["effective_mb_s"] == pytest.approx(5.0)
    assert report["passes_rate_gate"] is True
    assert report["retransmits"] == 3


def test_m1_positive_sequence_is_out_of_order_with_duplicate_and_gap_status():
    payload = make_m1_payload()
    packets = build_m1_data_packets(0, 0x10203040, payload)
    sequence = build_m1_positive_sequence(packets, withheld_index=2)

    assert [packet.sequence for packet in sequence[:3]] == [1, 0, 0]
    assert sequence[3].type is MessageType.STATUS
    assert sequence[3].sequence == 0xFFFFFFFE
    assert [packet.sequence for packet in sequence[4:]] == list(range(3, len(packets))) + [2]


def test_m1_payload_and_chunks_are_aligned_and_fragment_safe():
    payload = make_m1_payload()
    packets = build_m1_data_packets(1, 7, payload)

    assert len(payload) == 64 * 1024
    assert payload == bytes((index * 29 + 17) & 0xFF for index in range(64 * 1024))
    assert all(packet.type is MessageType.DATA for packet in packets)
    assert all(len(packet.payload) <= 1400 and len(packet.payload) % 4 == 0 for packet in packets)
    assert packets[-1].offset + len(packets[-1].payload) == len(payload)


def test_m1_negative_snapshot_only_checks_non_target_bank():
    before = {
        "b0_valid": 1, "b0_length": 100, "b0_crc": 0x1111,
        "b1_valid": 1, "b1_length": 200, "b1_crc": 0x2222,
    }
    after_target_changed = dict(before, b0_valid=0, b0_length=0, b0_crc=0)

    assert non_target_metadata(before, bank=0) == (1, 200, 0x2222)
    assert non_target_metadata(after_target_changed, bank=0) == (1, 200, 0x2222)


def test_net_status_parser_exposes_fixed_endpoint():
    parsed = parse_net_status(
        "NET GEM0 INIT 1 LINK UP IP 192.0.2.10 UDP 40100"
    )
    assert parsed == {"init": 1, "link": "UP", "ip": "192.0.2.10", "port": 40100}
    # Documentation-range IP, deliberately distinct from the redacted endpoint
    # (192.0.2.10) so the negative case survives the public export.
    with pytest.raises(ValueError):
        parse_net_status("NET GEM0 INIT 0 LINK DOWN IP 192.0.2.11 UDP 40100")


# ---- M3 dual-bank upload -> playback gate (Task 8) -------------------------


def _pp_line(active=0, abank="NONE", nbank="NONE", state="IDLE", error=0,
             b0v=0, b0len=0, b0crc=0, b1v=0, b1len=0, b1crc=0):
    return (
        "DDR PP ACTIVE %d ACTIVE_BANK %s NEXT_BANK %s STATE %s ERROR %02X "
        "BANK0 VALID %d LENGTH %d CRC %04X BANK1 VALID %d LENGTH %d CRC %04X" %
        (active, abank, nbank, state, error,
         b0v, b0len, b0crc, b1v, b1len, b1crc)
    )


def _m3_ready_lines(gate, p0, p1):
    """Idle statuses used by the fake board: settle + post-upload metadata."""
    bits = gate.BANK_BYTES * 8
    return [
        _pp_line(),  # settle: board idle
        _pp_line(b0v=1, b0len=bits, b0crc=_fast_crc16(p0),
                 b1v=1, b1len=bits, b1crc=_fast_crc16(p1)),  # metadata verify
    ]


def _m3_metadata_line(gate, p0, p1):
    bits = gate.BANK_BYTES * 8
    return _pp_line(b0v=1, b0len=bits, b0crc=_fast_crc16(p0),
                    b1v=1, b1len=bits, b1crc=_fast_crc16(p1))


def _bench_line(next_submit_ticks=1050195, pp0_underflow=0, pp1_underflow=0):
    """DMA BENCH readback line mirroring the board's SET DMA BENCH response."""
    return (
        "DMA BENCH PASS BYTES 1048576 CRC 262A423D STATUS E0100000 TICKS 1748343 "
        "RATE_MBPS_X1000 199918 FIRST_VALID_CYCLE 65164 FIFO_MIN 1 FIFO_MAX 3 "
        "DDR_PP_TIMELINE IOC_TICKS 689151858 DRAIN_TICKS 10317633 "
        "NEXT_SUBMIT_TICKS %d FIRST_BEAT_TICKS 0 GAP_TICKS %d FIFO_MIN 0 FIFO_MAX 0 "
        "PP_OBS PP0_STATUS CF100000 PP1_STATUS CF900000 PP0_CRC32 17C8C627 "
        "PP1_CRC32 F2EE4773 PP0_TLAST 1 PP1_TLAST 1 PP0_TKEEP F PP1_TKEEP F "
        "PP0_UNDERFLOW %d PP1_UNDERFLOW %d PP0_BYTES 1048576 PP1_BYTES 1048576 "
        "PP0_FIFO_MIN 0 PP0_FIFO_MAX 4097 PP1_FIFO_MIN 0 PP1_FIFO_MAX 4097 "
        "PP_GAP_CYCLES 159055"
        % (next_submit_ticks, next_submit_ticks, pp0_underflow, pp1_underflow)
    )


class FakeUdpClient:
    def __init__(self):
        self.uploads = []
        self.cancelled = False

    def upload(self, bank, payload):
        payload = bytes(payload)
        self.uploads.append((bank, payload))
        full = crc32(payload)
        return UploadResult(
            bytes_sent=len(payload), elapsed_s=0.1, effective_mb_s=10.0,
            retransmits=0, payload_crc32=full, device_crc32=full,
            device_error=ReplyError.NONE,
        )


class FakeUart:
    def __init__(self):
        self.sent = []
        self.ddrpp = []
        self.default_ddrpp = _pp_line()
        self.start_reply = "OK"
        self.reject_substrings = []
        self.dma_bench = _bench_line()

    def cmd(self, command, timeout=3.0):
        self.sent.append(command)
        if any(sub in command for sub in self.reject_substrings):
            return "ERR BAD VALUE"
        if command == "GET DDR PP":
            return self.ddrpp.pop(0) if self.ddrpp else self.default_ddrpp
        if command == "SET DDR PP START":
            return self.start_reply
        if command == "SET DMA BENCH":
            return self.dma_bench
        return "OK"


def test_m3_uploads_two_banks_then_drives_dual_bank_playback():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_m3_payloads()
    uart = FakeUart()
    uart.ddrpp = _m3_ready_lines(gate, p0, p1) + [
        _pp_line(active=1, abank="0", nbank="1", state="RUNNING",
                 b0v=1, b1v=1),
        _pp_line(active=1, abank="1", nbank="NONE", state="DRAIN",
                 b0v=1, b1v=1),
        _pp_line(),  # idle after both banks
    ]
    client = FakeUdpClient()

    result = gate.run_m3(client, uart)

    assert client.uploads == [(0, p0), (1, p1)]
    # M3 must not turn into a payload upload over the UART control plane.
    assert not any("CHUNK" in line or "COMMIT" in line for line in uart.sent)
    assert "SET DDR PP START" in uart.sent
    assert "SET DIGMOD MODE QAM16" in uart.sent
    assert "SET DDR PP STOP" not in uart.sent
    assert result["bank_order"] == ["0", "1"]
    assert result["final_state"] == "IDLE"
    assert result["final_error"] == 0


def test_m3_rejects_upload_metadata_mismatch_before_start():
    from scripts import udp_ddr_pp_gate as gate
    import pytest as _pytest

    p0, p1 = gate.make_m3_payloads()
    uart = FakeUart()
    bits = gate.BANK_BYTES * 8
    uart.ddrpp = [
        _pp_line(),  # settle
        _pp_line(b0v=1, b0len=bits // 2, b0crc=_fast_crc16(p0),  # wrong length
                 b1v=1, b1len=bits, b1crc=_fast_crc16(p1)),
    ]
    with _pytest.raises(gate.GateError, match="metadata"):
        gate.run_m3(FakeUdpClient(), uart)
    assert "SET DDR PP START" not in uart.sent


def test_m3_does_not_send_payload_chunks_before_metadata_check():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_m3_payloads()
    uart = FakeUart()
    uart.ddrpp = _m3_ready_lines(gate, p0, p1) + [
        _pp_line(active=1, abank="0", nbank="1", state="RUNNING",
                 b0v=1, b1v=1),
        _pp_line(active=1, abank="1", nbank="NONE", state="DRAIN",
                 b0v=1, b1v=1),
        _pp_line(),
    ]
    gate.run_m3(FakeUdpClient(), uart)
    assert all(line.startswith(("GET DDR PP", "SET DIGMOD", "SET DDR PP START"))
               for line in uart.sent)


def test_m3_stops_and_fails_when_playback_enters_error():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_m3_payloads()
    uart = FakeUart()
    uart.ddrpp = _m3_ready_lines(gate, p0, p1) + [
        _pp_line(active=1, abank="0", state="RUNNING", error=0x21,
                 b0v=1, b1v=1),
    ]
    with pytest.raises(gate.GateError, match="ERROR"):
        gate.run_m3(FakeUdpClient(), uart)
    assert uart.sent.count("SET DDR PP STOP") == 1


def test_m3_rejects_missing_bank_transition():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_m3_payloads()
    uart = FakeUart()
    uart.ddrpp = _m3_ready_lines(gate, p0, p1) + [
        _pp_line(active=1, abank="0", nbank="1", state="RUNNING",
                 b0v=1, b1v=1),
        _pp_line(),  # skips bank 1 entirely
    ]
    with pytest.raises(gate.GateError, match="bank order"):
        gate.run_m3(FakeUdpClient(), uart)


def test_m3_stops_when_playback_times_out_never_idle():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_m3_payloads()
    uart = FakeUart()
    uart.ddrpp = _m3_ready_lines(gate, p0, p1)
    uart.default_ddrpp = _pp_line(active=1, abank="1", nbank="NONE",
                                  state="RUNNING", b0v=1, b1v=1)
    with pytest.raises(gate.GateError, match="timed out"):
        gate.run_m3(FakeUdpClient(), uart, poll_timeout_s=0.05)
    assert "SET DDR PP STOP" in uart.sent


def test_m3_rejects_digmod_configure_failure():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_m3_payloads()
    uart = FakeUart()
    uart.ddrpp = _m3_ready_lines(gate, p0, p1)
    uart.reject_substrings = ["SET DIGMOD FILTER"]
    with pytest.raises(gate.GateError, match="DIGMOD"):
        gate.run_m3(FakeUdpClient(), uart)
    assert "SET DDR PP START" not in uart.sent


def test_m3_rejects_start_failure():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_m3_payloads()
    uart = FakeUart()
    uart.ddrpp = _m3_ready_lines(gate, p0, p1)
    uart.start_reply = "ERR DDR PP START FAILED"
    with pytest.raises(gate.GateError, match="START"):
        gate.run_m3(FakeUdpClient(), uart)


# ---- M4 ring-refill playback gate (Task 3) ---------------------------------


def test_m4_refills_non_active_bank_and_switches():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_ring_payloads(0)
    crc = {0: _fast_crc16(p0), 1: _fast_crc16(p1)}
    bits = gate.BANK_BYTES * 8
    refill = gate.make_ring_payloads(2)[1]  # rounds=1 -> seed 2, bank 1
    uart = FakeUart()
    # settle(metadata) -> START -> active 0 -> (gate uploads bank1) -> active 1 -> IDLE
    uart.ddrpp = [
        _pp_line(b0v=1, b0len=bits, b0crc=crc[0], b1v=1, b1len=bits, b1crc=crc[1]),
        _pp_line(active=1, abank="0", state="RUNNING", b0v=1, b1v=1),
        _pp_line(active=1, abank="1", state="RUNNING", b0v=1, b1v=1,
                 b1len=bits, b1crc=_fast_crc16(refill)),
        _pp_line(),
    ]
    client = FakeUdpClient()

    result = gate.run_m4(client, uart, rounds=1)

    assert result["switch_order"] == ["1"]
    assert result["final_state"] == "IDLE"
    assert client.uploads[0][0] == 0 and client.uploads[1][0] == 1  # preload
    assert client.uploads[2][0] == 1                                # refill non-active
    assert "SET DDR PP START" in uart.sent
    assert result["next_submit_ticks"] == 1050195
    assert result["pp0_underflow"] == 0 and result["pp1_underflow"] == 0


def test_m4_rejects_per_switch_metadata_mismatch():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_ring_payloads(0)
    bits = gate.BANK_BYTES * 8
    refill = gate.make_ring_payloads(2)[1]
    uart = FakeUart()
    uart.ddrpp = [
        _pp_line(b0v=1, b0len=bits, b0crc=_fast_crc16(p0),
                 b1v=1, b1len=bits, b1crc=_fast_crc16(p1)),
        _pp_line(active=1, abank="0", state="RUNNING", b0v=1, b1v=1),
        # switched to bank 1 but the played content CRC is not the refill
        _pp_line(active=1, abank="1", state="RUNNING", b0v=1, b1v=1,
                 b1len=bits, b1crc=_fast_crc16(refill) ^ 0xFFFF),
        _pp_line(),
    ]
    with pytest.raises(gate.GateError, match="metadata"):
        gate.run_m4(FakeUdpClient(), uart, rounds=1)
    assert "SET DDR PP STOP" in uart.sent


def test_m4_rejects_nonzero_underflow():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_ring_payloads(0)
    bits = gate.BANK_BYTES * 8
    refill = gate.make_ring_payloads(2)[1]
    uart = FakeUart()
    uart.ddrpp = [
        _pp_line(b0v=1, b0len=bits, b0crc=_fast_crc16(p0),
                 b1v=1, b1len=bits, b1crc=_fast_crc16(p1)),
        _pp_line(active=1, abank="0", state="RUNNING", b0v=1, b1v=1),
        _pp_line(active=1, abank="1", state="RUNNING", b0v=1, b1v=1,
                 b1len=bits, b1crc=_fast_crc16(refill)),
        _pp_line(),
    ]
    uart.dma_bench = _bench_line(pp0_underflow=1)
    with pytest.raises(gate.GateError, match="underflow"):
        gate.run_m4(FakeUdpClient(), uart, rounds=1)
    assert "SET DDR PP STOP" in uart.sent


def test_m4_rejects_non_alternating_switch_order():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_ring_payloads(0)
    bits = gate.BANK_BYTES * 8
    r0 = gate.make_ring_payloads(3)[1]  # rounds=2, r=0 -> seed 3, bank 1
    r1 = gate.make_ring_payloads(4)[1]  # rounds=2, r=1 -> seed 4, bank 1
    uart = FakeUart()
    # Both rounds report the active bank back on bank 0 -> switches 1,1 (not 1,0)
    uart.ddrpp = [
        _pp_line(b0v=1, b0len=bits, b0crc=_fast_crc16(p0),
                 b1v=1, b1len=bits, b1crc=_fast_crc16(p1)),
        _pp_line(active=1, abank="0", state="RUNNING", b0v=1, b1v=1),
        _pp_line(active=1, abank="1", state="RUNNING", b0v=1, b1v=1,
                 b1len=bits, b1crc=_fast_crc16(r0)),
        _pp_line(active=1, abank="0", state="RUNNING", b0v=1, b1v=1),
        _pp_line(active=1, abank="1", state="RUNNING", b0v=1, b1v=1,
                 b1len=bits, b1crc=_fast_crc16(r1)),
        _pp_line(),
    ]
    with pytest.raises(gate.GateError, match="switch order"):
        gate.run_m4(FakeUdpClient(), uart, rounds=2)
    assert "SET DDR PP STOP" in uart.sent


def test_m4_uses_distinct_payload_per_round():
    from scripts import udp_ddr_pp_gate as gate

    a = gate.make_ring_payloads(1)[0]
    b = gate.make_ring_payloads(2)[0]
    assert a != b


def test_m4_fails_on_playback_error():
    import pytest as _pytest
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_ring_payloads(0)
    crc = {0: _fast_crc16(p0), 1: _fast_crc16(p1)}
    bits = gate.BANK_BYTES * 8
    uart = FakeUart()
    uart.ddrpp = [
        _pp_line(b0v=1, b0len=bits, b0crc=crc[0], b1v=1, b1len=bits, b1crc=crc[1]),
        _pp_line(active=1, abank="0", state="ERROR", error=0x26, b0v=1, b1v=1),
    ]
    with _pytest.raises(gate.GateError, match="ERROR"):
        gate.run_m4(FakeUdpClient(), uart, rounds=1)


# ---- M5 host-ring E2E gate (GUI RingFeeder + panel status parser) ----------


def _m5_preload_line(gate):
    p0, p1 = gate.make_ring_payloads(0)
    bits = gate.BANK_BYTES * 8
    return _pp_line(b0v=1, b0len=bits, b0crc=_fast_crc16(p0),
                    b1v=1, b1len=bits, b1crc=_fast_crc16(p1))


def test_m5_drives_ring_refills_through_host_feeder():
    from scripts import udp_ddr_pp_gate as gate

    queue = gate._ring_payload_queue(3)
    bits = gate.BANK_BYTES * 8
    uart = FakeUart()
    uart.ddrpp = [
        _m5_preload_line(gate),
        # both banks fresh: active 0, next 1 -> feeder does not refill
        _pp_line(active=1, abank="0", nbank="1", state="RUNNING", b0v=1, b1v=1),
        # natural switch to bank 1; next NONE -> feeder refills queue[0] to bank 0
        _pp_line(active=1, abank="1", nbank="NONE", state="RUNNING", b0v=1, b1v=1),
        # bank 0 now fresh -> next 0, still no refill
        _pp_line(active=1, abank="1", nbank="0", state="RUNNING", b0v=1, b1v=1),
        # switch to bank 0: played content must equal queue[0]; refill queue[1] to bank 1
        _pp_line(active=1, abank="0", nbank="NONE", state="RUNNING",
                 b0v=1, b0len=bits, b0crc=_fast_crc16(queue[0]), b1v=1),
        # switch to bank 1: content must equal queue[1]; rounds reached
        _pp_line(active=1, abank="1", nbank="NONE", state="RUNNING",
                 b0v=1, b1v=1, b1len=bits, b1crc=_fast_crc16(queue[1])),
        _pp_line(),  # drain to IDLE
    ]
    client = FakeUdpClient()

    result = gate.run_m5(client, uart, rounds=3)

    assert result["switch_order"] == [1, 0, 1]
    assert result["final_state"] == "IDLE"
    assert [bank for bank, _ in client.uploads] == [0, 1, 0, 1]
    assert client.uploads[2][1] == queue[0] and client.uploads[3][1] == queue[1]
    assert result["pp0_underflow"] == 0 and result["pp1_underflow"] == 0


def test_m5_rejects_refill_metadata_mismatch():
    from scripts import udp_ddr_pp_gate as gate

    queue = gate._ring_payload_queue(2)
    bits = gate.BANK_BYTES * 8
    uart = FakeUart()
    uart.ddrpp = [
        _m5_preload_line(gate),
        _pp_line(active=1, abank="0", nbank="1", state="RUNNING", b0v=1, b1v=1),
        _pp_line(active=1, abank="1", nbank="NONE", state="RUNNING", b0v=1, b1v=1),
        # switched to bank 0 but its CRC is not the queue[0] we just committed
        _pp_line(active=1, abank="0", nbank="NONE", state="RUNNING",
                 b0v=1, b0len=bits, b0crc=_fast_crc16(queue[0]) ^ 0xFFFF, b1v=1),
    ]
    with pytest.raises(gate.GateError, match="metadata"):
        gate.run_m5(FakeUdpClient(), uart, rounds=2)
    assert "SET DDR PP STOP" in uart.sent


def test_m5_stops_on_playback_error():
    from scripts import udp_ddr_pp_gate as gate

    uart = FakeUart()
    uart.ddrpp = [
        _m5_preload_line(gate),
        _pp_line(active=1, abank="0", state="ERROR", error=0x26, b0v=1, b1v=1),
    ]
    with pytest.raises(gate.GateError, match="ERROR"):
        gate.run_m5(FakeUdpClient(), uart, rounds=2)
    assert "SET DDR PP STOP" in uart.sent


def test_m5_status_parser_retries_past_stray_lines():
    from scripts import udp_ddr_pp_gate as gate

    uart = FakeUart()
    uart.ddrpp = ["", _pp_line()]

    status = gate._pp_status_gui(uart)

    assert status["state"] == "IDLE" and status["active"] is False

