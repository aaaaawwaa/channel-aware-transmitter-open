#!/usr/bin/env python3
"""Run the GEM0 UDP DDR PP M0-M5 board validation gates."""
import argparse
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import serial

from host.siggen.udp_ddr_pp import (
    BANK_BYTES,
    HEADER_SIZE,
    MAX_PAYLOAD_BYTES,
    MessageType,
    Packet,
    ReplyError,
    ReplyState,
    UdpDdrPpClient,
    crc32,
    decode_packet,
    decode_reply_status,
    encode_packet,
)

DEFAULT_HOST = "192.0.2.10"
DEFAULT_PORT = 40100
BOARD_MAC = "02-ac-82-00-00-02"
M1_BYTES = 64 * 1024
M2_MIN_MB_S = 3.0
SESSION_TIMEOUT_S = 5.0
BAUD = 115200
SEQ_STATUS = 0xFFFFFFFE
SEQ_COMMIT = 0xFFFFFFFF
SEQ_ABORT = 0xFFFFFFFD
M3_BITS = BANK_BYTES * 8
M3_PROFILE = ("QAM16", "RECT", "0.35", 1000000)
M3_POLL_TIMEOUT_S = 40.0

NET_RE = re.compile(
    r"^NET GEM0 INIT (?P<init>[01]) LINK (?P<link>UP|DOWN) "
    r"IP (?P<ip>\d+\.\d+\.\d+\.\d+) UDP (?P<port>\d+)$"
)
MAC_RE = re.compile(r"(?i)([0-9a-f]{2}[-:]){5}[0-9a-f]{2}")
PP_RE = re.compile(
    r"^DDR PP ACTIVE (?P<active>[01]) ACTIVE_BANK (?P<active_bank>NONE|[01]) "
    r"NEXT_BANK (?P<next_bank>NONE|[01]) STATE (?P<state>\w+) "
    r"ERROR (?P<error>[0-9A-Fa-f]{2}) "
    r"BANK0 VALID (?P<b0_valid>[01]) LENGTH (?P<b0_length>\d+) CRC (?P<b0_crc>[0-9A-Fa-f]+) "
    r"BANK1 VALID (?P<b1_valid>[01]) LENGTH (?P<b1_length>\d+) CRC (?P<b1_crc>[0-9A-Fa-f]+)$"
)


class GateError(RuntimeError):
    pass


def parse_net_status(line):
    match = NET_RE.fullmatch((line or "").strip())
    if not match:
        raise ValueError("invalid NET status: %r" % line)
    parsed = {
        "init": int(match.group("init")),
        "link": match.group("link"),
        "ip": match.group("ip"),
        "port": int(match.group("port")),
    }
    if parsed["ip"] != DEFAULT_HOST or parsed["port"] != DEFAULT_PORT:
        raise ValueError("unexpected GEM0 endpoint: %r" % parsed)
    return parsed


def run_m0(client, arp_lookup, serial_query, host=DEFAULT_HOST, port=DEFAULT_PORT):
    status = client.echo()
    if status.state is not ReplyState.IDLE or status.error is not ReplyError.NONE:
        raise GateError("UDP ECHO failed: %s/%s" % (status.state.name, status.error.name))
    mac = arp_lookup(host)
    if not mac:
        raise GateError("ARP neighbor missing for %s" % host)
    net = None
    if serial_query is not None:
        net = parse_net_status(serial_query())
        if net["init"] != 1 or net["link"] != "UP":
            raise GateError("UART NET status is not initialized/up: %r" % net)
        if net["ip"] != host or net["port"] != port:
            raise GateError("UART NET endpoint mismatch: %r" % net)
    return {"arp": host, "mac": mac, "echo": status, "net": net}


def lookup_arp(host):
    command = ["arp", "-a", host] if os.name == "nt" else ["ip", "neigh", "show", host]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    match = MAC_RE.search((result.stdout or "") + "\n" + (result.stderr or ""))
    return match.group(0).lower().replace(":", "-") if match else ""


def _serial_query(port, command, timeout=3.0):
    with serial.Serial(port, BAUD, timeout=0.1) as ser:
        ser.reset_input_buffer()
        ser.write((command + "\r\n").encode())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = ser.readline().decode(errors="replace").strip()
            if command == "GET NET" and line.startswith("NET GEM0 "):
                return line
            if command == "GET DDR PP" and line.startswith("DDR PP ACTIVE "):
                return line
    raise GateError("UART response timeout for %s" % command)


class M3Uart:
    """One persistent line-buffered UART session for the m3 playback poll loop.

    GET DDR PP needs many sub-second round trips; opening a fresh serial port
    per query would miss playback state transitions."""

    def __init__(self, port):
        self._ser = serial.Serial(port, BAUD, timeout=0.1)
        self._pending = bytearray()

    def cmd(self, command, timeout=3.0):
        self._ser.write((command + "\r\n").encode())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._read_line(min(0.2, deadline - time.monotonic()))
            if line is not None:
                return line
        raise GateError("UART response timeout for %s" % command)

    def _read_line(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            newline = self._pending.find(b"\n")
            if newline >= 0:
                line = bytes(self._pending[:newline])
                del self._pending[:newline + 1]
                return line.decode(errors="replace").strip()
            chunk = self._ser.read(self._ser.in_waiting or 1)
            if chunk:
                self._pending.extend(chunk)
        return None

    def close(self):
        self._ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def parse_pp_status(line):
    match = PP_RE.fullmatch((line or "").strip())
    if not match:
        raise GateError("invalid DDR PP status: %r" % line)
    values = match.groupdict()
    for key in ("active", "b0_valid", "b1_valid"):
        values[key] = int(values[key])
    for key in ("b0_length", "b1_length"):
        values[key] = int(values[key])
    values["error"] = int(values["error"], 16)
    values["b0_crc"] = int(values["b0_crc"], 16)
    values["b1_crc"] = int(values["b1_crc"], 16)
    return values


def make_m1_payload(size=M1_BYTES):
    if size < 4 or size > BANK_BYTES or size % 4:
        raise ValueError("M1 payload size must be aligned and within one bank")
    return bytes((index * 29 + 17) & 0xFF for index in range(size))


def build_m1_data_packets(bank, session, payload):
    payload = bytes(payload)
    if not 0 <= bank <= 1 or session == 0 or not payload or len(payload) % 4:
        raise ValueError("invalid M1 packet inputs")
    if len(payload) > BANK_BYTES:
        raise ValueError("M1 payload exceeds one bank")
    packets = []
    offset = 0
    while offset < len(payload):
        length = min(MAX_PAYLOAD_BYTES, len(payload) - offset)
        chunk = payload[offset:offset + length]
        packets.append(Packet(
            MessageType.DATA, bank, session, len(packets), offset, len(payload),
            crc32(chunk), chunk,
        ))
        offset += length
    return packets


def build_m1_positive_sequence(packets, withheld_index=2):
    packets = list(packets)
    if len(packets) < 3 or not 0 <= withheld_index < len(packets):
        raise ValueError("M1 sequence requires at least three packets")
    if withheld_index in (0, 1):
        raise ValueError("withheld packet must follow the out-of-order pair")
    status = Packet(
        MessageType.STATUS, packets[0].bank, packets[0].session_id, SEQ_STATUS,
        0, packets[0].total_bytes, 0, b"",
    )
    return (
        [packets[1], packets[0], packets[0], status] +
        [packet for index, packet in enumerate(packets) if index not in (0, 1, withheld_index)] +
        [packets[withheld_index]]
    )


def make_m2_report(bytes_sent, elapsed_s, retransmits, payload_crc32,
                   device_crc32, device_error):
    effective = bytes_sent / elapsed_s / (1 << 20) if elapsed_s > 0 else 0.0
    error = int(device_error)
    return {
        "bytes_sent": bytes_sent,
        "elapsed_s": elapsed_s,
        "effective_mb_s": effective,
        "retransmits": retransmits,
        "payload_crc32": payload_crc32,
        "device_crc32": device_crc32,
        "device_error": error,
        "passes_rate_gate": (
            bytes_sent == BANK_BYTES and effective >= M2_MIN_MB_S and
            error == int(ReplyError.NONE) and payload_crc32 == device_crc32
        ),
    }


def _recv_reply(sock, host, port, session, bank, sequence, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        wire, _addr = sock.recvfrom(HEADER_SIZE + MAX_PAYLOAD_BYTES + 256)
        try:
            reply = decode_packet(wire)
        except ValueError:
            continue
        if (
            reply.type is MessageType.REPLY and reply.session_id == session and
            reply.bank == bank and reply.sequence == sequence
        ):
            return reply
    raise socket.timeout("matching UDP reply not received")


def _exchange(sock, packet, host, port, attempts=5):
    wire = encode_packet(packet)
    for _ in range(attempts):
        sock.sendto(wire, (host, port))
        try:
            return _recv_reply(sock, host, port, packet.session_id, packet.bank,
                               packet.sequence)
        except socket.timeout:
            continue
    raise GateError("no reply for %s sequence %08X" % (packet.type.name, packet.sequence))


def _exchange_raw(sock, wire, host, port, session, bank, sequence, attempts=5):
    for _ in range(attempts):
        sock.sendto(wire, (host, port))
        try:
            return _recv_reply(sock, host, port, session, bank, sequence)
        except socket.timeout:
            continue
    raise GateError("no reply for raw sequence %08X" % sequence)


def _status(reply):
    status = decode_reply_status(reply.payload)
    if status.error is not ReplyError.NONE:
        raise GateError("device rejected request: %s" % status.error.name)
    return status


def _begin(sock, host, port, bank, session, payload):
    packet = Packet(
        MessageType.BEGIN, bank, session, 0, 0, len(payload), crc32(payload), b"",
    )
    reply = _exchange(sock, packet, host, port)
    status = _status(reply)
    if status.state is not ReplyState.RECEIVING:
        raise GateError("BEGIN did not enter RECEIVING: %s" % status.state.name)
    return packet


def _abort(sock, host, port, bank, session, total):
    packet = Packet(MessageType.ABORT, bank, session, SEQ_ABORT, 0, total, 0, b"")
    reply = _exchange(sock, packet, host, port)
    status = decode_reply_status(reply.payload)
    if status.state is not ReplyState.ABORTED or status.error is not ReplyError.NONE:
        raise GateError("ABORT did not close session: %s/%s" %
                        (status.state.name, status.error.name))
    return status


def _snapshot(serial_port):
    if serial_port is None:
        return None
    return parse_pp_status(_serial_query(serial_port, "GET DDR PP"))


def non_target_metadata(status, bank):
    if bank not in (0, 1):
        raise ValueError("bank must be 0 or 1")
    other = 1 - bank
    return (
        status["b%d_valid" % other],
        status["b%d_length" % other],
        status["b%d_crc" % other],
    )


def _assert_snapshot_unchanged(before, serial_port, bank):
    if before is None:
        return
    after = _snapshot(serial_port)
    if non_target_metadata(after, bank) != non_target_metadata(before, bank):
        raise GateError("non-target DDR PP metadata changed: before=%r after=%r" %
                        (non_target_metadata(before, bank),
                         non_target_metadata(after, bank)))


def _negative_crc(host, port, bank, payload, serial_port):
    before = _snapshot(serial_port)
    session = secrets.randbits(32) or 1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.25)
        _begin(sock, host, port, bank, session, payload)
        packet = build_m1_data_packets(bank, session, payload)[0]
        bad = Packet(packet.type, packet.bank, packet.session_id, packet.sequence,
                     packet.offset, packet.total_bytes, packet.crc32 ^ 1, packet.payload)
        reply = _exchange(sock, bad, host, port)
        status = decode_reply_status(reply.payload)
        if status.error is not ReplyError.CRC:
            raise GateError("bad DATA CRC was not rejected: %s" % status.error.name)
        _abort(sock, host, port, bank, session, len(payload))
    _assert_snapshot_unchanged(before, serial_port, bank)
    return "BAD_CRC"


def _negative_range(host, port, bank, payload, serial_port):
    before = _snapshot(serial_port)
    session = secrets.randbits(32) or 1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.25)
        _begin(sock, host, port, bank, session, payload)
        packet = build_m1_data_packets(bank, session, payload)[0]
        wire = bytearray(encode_packet(packet))
        wire[16:20] = len(payload).to_bytes(4, "big")
        reply = _exchange_raw(sock, bytes(wire), host, port, session, bank,
                              packet.sequence)
        status = decode_reply_status(reply.payload)
        if status.error is not ReplyError.RANGE:
            raise GateError("out-of-range DATA was not rejected: %s" % status.error.name)
        _abort(sock, host, port, bank, session, len(payload))
    _assert_snapshot_unchanged(before, serial_port, bank)
    return "RANGE"


def _negative_abort(host, port, bank, payload, serial_port):
    before = _snapshot(serial_port)
    session = secrets.randbits(32) or 1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.25)
        _begin(sock, host, port, bank, session, payload)
        _abort(sock, host, port, bank, session, len(payload))
    _assert_snapshot_unchanged(before, serial_port, bank)
    return "ABORT"


def _negative_timeout(host, port, bank, payload, serial_port):
    before = _snapshot(serial_port)
    session = secrets.randbits(32) or 1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.25)
        _begin(sock, host, port, bank, session, payload)
        time.sleep(SESSION_TIMEOUT_S + 0.25)
        status_packet = Packet(MessageType.STATUS, bank, session, SEQ_STATUS, 0,
                               len(payload), 0, b"")
        reply = _exchange(sock, status_packet, host, port)
        status = decode_reply_status(reply.payload)
        if status.state is not ReplyState.ABORTED or status.error is not ReplyError.TIMEOUT:
            raise GateError("session timeout was not reported: %s/%s" %
                            (status.state.name, status.error.name))
    _assert_snapshot_unchanged(before, serial_port, bank)
    return "TIMEOUT"


def run_m1(host, port, bank, serial_port=None):
    payload = make_m1_payload()
    session = secrets.randbits(32) or 1
    packets = build_m1_data_packets(bank, session, payload)
    sequence = build_m1_positive_sequence(packets)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.25)
        _begin(sock, host, port, bank, session, payload)
        gap_checked = False
        for packet in sequence:
            reply = _exchange(sock, packet, host, port)
            status = decode_reply_status(reply.payload)
            if packet.type is MessageType.STATUS:
                if status.state is not ReplyState.RECEIVING or status.first_missing_length == 0:
                    raise GateError("withheld DATA was not visible in STATUS: %r" % status)
                gap_checked = True
            elif status.error is not ReplyError.NONE:
                raise GateError("positive DATA rejected: %s" % status.error.name)
        if not gap_checked:
            raise GateError("M1 positive sequence omitted gap STATUS")
        status_packet = Packet(MessageType.STATUS, bank, session, SEQ_STATUS, 0,
                               len(payload), 0, b"")
        final_status = _status(_exchange(sock, status_packet, host, port))
        if final_status.received_bytes != len(payload) or final_status.first_missing_length:
            raise GateError("M1 final STATUS incomplete: %r" % final_status)
        commit = Packet(MessageType.COMMIT, bank, session, SEQ_COMMIT, 0,
                        len(payload), crc32(payload), b"")
        reply = _exchange(sock, commit, host, port)
        committed = decode_reply_status(reply.payload)
        if (
            committed.state is not ReplyState.COMMITTED or
            committed.error is not ReplyError.NONE or
            committed.received_bytes != len(payload) or
            reply.crc32 != crc32(payload)
        ):
            raise GateError("M1 COMMIT verification failed: %r crc=%08X" %
                            (committed, reply.crc32))
    negatives = [
        _negative_crc(host, port, bank, payload, serial_port),
        _negative_range(host, port, bank, payload, serial_port),
        _negative_abort(host, port, bank, payload, serial_port),
        _negative_timeout(host, port, bank, payload, serial_port),
    ]
    return {
        "bank": bank,
        "bytes": len(payload),
        "payload_crc32": crc32(payload),
        "device_crc32": crc32(payload),
        "retransmits": 0,
        "negative_cases": negatives,
    }


def make_m2_payload():
    return bytes((index * 37 + 91) & 0xFF for index in range(BANK_BYTES))


def run_m2(host, port, bank):
    payload = make_m2_payload()
    client = UdpDdrPpClient(host, port)
    started = time.monotonic()
    result = client.upload(bank, payload)
    elapsed = time.monotonic() - started
    report = make_m2_report(
        result.bytes_sent, elapsed, result.retransmits,
        result.payload_crc32, result.device_crc32, result.device_error,
    )
    if not report["passes_rate_gate"]:
        raise GateError("M2 rate/integrity gate failed: %r" % report)
    return report


def make_m3_payloads():
    return (
        bytes((index * 37 + 91) & 0xFF for index in range(BANK_BYTES)),
        bytes((index * 53 + 17) & 0xFF for index in range(BANK_BYTES)),
    )


def _crc16_ccitt_fast(data):
    """Byte-wise CCITT-FALSE CRC-16 matching protocol.crc16_ccitt and the
    device COMMIT metadata. Avoids the bit-loop reference's ~7 s cost over a
    1 MiB bank so the M3 metadata check stays interactive."""
    table = getattr(_crc16_ccitt_fast, "_table", None)
    if table is None:
        table = []
        for n in range(256):
            r = n << 8
            for _ in range(8):
                r = ((r << 1) ^ 0x1021) if (r & 0x8000) else (r << 1)
            table.append(r & 0xffff)
        _crc16_ccitt_fast._table = table
    crc = 0xffff
    for byte in bytes(data):
        crc = ((crc << 8) ^ table[((crc >> 8) ^ byte) & 0xff]) & 0xffff
    return crc


def _require_m3_banks(status, payload_crc16):
    problems = []
    for bank in (0, 1):
        valid = status["b%d_valid" % bank]
        length = status["b%d_length" % bank]
        crc = status["b%d_crc" % bank]
        if not valid:
            problems.append("bank %d not valid" % bank)
        if length != M3_BITS:
            problems.append("bank %d length %d != %d bits" %
                            (bank, length, M3_BITS))
        if crc != payload_crc16[bank]:
            problems.append("bank %d crc %04X != %04X" %
                            (bank, crc, payload_crc16[bank]))
    if status["active"]:
        problems.append("DDR PP active during pre-playback check")
    if status["error"]:
        problems.append("DDR PP error %02X" % status["error"])
    if status["state"] != "IDLE":
        problems.append("state %s != IDLE" % status["state"])
    return problems


def _pp_status(uart):
    """Read one GET DDR PP status, retrying past stray/unsolicited lines."""
    last = None
    for _ in range(6):
        try:
            return parse_pp_status(uart.cmd("GET DDR PP"))
        except GateError as exc:
            last = exc
            continue
    raise GateError("GET DDR PP gave no parseable status: %s" % last)


def _expect_ok(uart, command):
    reply = uart.cmd(command)
    if reply != "OK":
        raise GateError("DIGMOD %s rejected: %r" % (command, reply))


def run_m3(client, uart, poll_timeout_s=M3_POLL_TIMEOUT_S,
           bank_order=("0", "1")):
    """UDP-upload both banks, then play them back through the UART control
    plane. Playback uses only the existing SET DDR PP START/STOP commands; the
    payload is carried over UDP and never re-chunked over UART."""
    settled = _pp_status(uart)
    if settled["active"] or settled["error"] or settled["state"] == "ERROR":
        uart.cmd("SET DDR PP STOP")
    _expect_ok(uart, "SET DIGMOD MODE OFF")

    payloads = make_m3_payloads()
    results = []
    for bank in (0, 1):
        result = client.upload(bank, payloads[bank])
        if (result.bytes_sent != BANK_BYTES or
                result.device_error is not ReplyError.NONE or
                result.payload_crc32 != result.device_crc32):
            raise GateError(
                "M3 UDP upload bank %d failed: bytes=%d error=%s crc32=%08X/%08X" %
                (bank, result.bytes_sent, result.device_error.name,
                 result.payload_crc32, result.device_crc32))
        results.append(result)

    expected_crc16 = [_crc16_ccitt_fast(payloads[b]) for b in (0, 1)]
    status = _pp_status(uart)
    problems = _require_m3_banks(status, expected_crc16)
    if problems:
        raise GateError("M3 bank metadata mismatch: %s" % "; ".join(problems))

    mode, filt, rolloff, symrate = M3_PROFILE
    for command in (
        "SET DIGMOD FILTER %s" % filt,
        "SET DIGMOD ROLLOFF %s" % rolloff,
        "SET DIGMOD SYMRATE %d" % symrate,
        "SET DIGMOD MODE %s" % mode,
    ):
        _expect_ok(uart, command)

    if uart.cmd("SET DDR PP START") != "OK":
        raise GateError("SET DDR PP START rejected")

    started = time.monotonic()
    transitions = []
    previous = None
    saw_active = False
    order_seen = []
    final = None
    while time.monotonic() - started < poll_timeout_s:
        status = _pp_status(uart)
        final = status
        key = (status["active"], status["active_bank"], status["next_bank"],
               status["state"], status["error"])
        if key != previous:
            transitions.append((time.monotonic() - started, status))
            previous = key
        if status["error"]:
            uart.cmd("SET DDR PP STOP")
            raise GateError("M3 playback ERROR %02X state %s" %
                            (status["error"], status["state"]))
        if status["active"]:
            saw_active = True
            bank = status["active_bank"]
            if bank != "NONE" and bank not in order_seen:
                order_seen.append(bank)
        if saw_active and not status["active"] and status["state"] == "IDLE":
            break
        time.sleep(0.01)
    else:
        uart.cmd("SET DDR PP STOP")
        state = final["state"] if final else "?"
        raise GateError("M3 playback timed out after %.1fs; last state=%s" %
                        (poll_timeout_s, state))
    if not saw_active:
        raise GateError("M3 playback ended without an active bank")
    if tuple(order_seen) != tuple(bank_order):
        raise GateError("M3 playback bank order %s != expected %s" %
                        (order_seen, list(bank_order)))
    if final["active"] or final["state"] != "IDLE" or final["error"]:
        raise GateError("M3 playback final not clean IDLE: %r" % final)

    _expect_ok(uart, "SET DIGMOD MODE OFF")
    return {
        "bytes": BANK_BYTES,
        "payload_crc32": [r.payload_crc32 for r in results],
        "device_crc32": [r.device_crc32 for r in results],
        "retransmits": [r.retransmits for r in results],
        "bank_order": order_seen,
        "final_state": final["state"],
        "final_error": final["error"],
        "transitions": transitions,
    }


M4_ROUNDS = 3
M4_BENCH_TIMEOUT_S = 15.0
M4_BENCH_RE = re.compile(
    r"NEXT_SUBMIT_TICKS (?P<next_submit_ticks>\d+)"
    r".*PP0_UNDERFLOW (?P<pp0_underflow>\d+)"
    r" PP1_UNDERFLOW (?P<pp1_underflow>\d+)"
)


def make_ring_payloads(seed):
    """Distinct deterministic 1 MiB payloads per round (CRC differs)."""
    return (
        bytes((i * 41 + 7 + seed) & 0xFF for i in range(BANK_BYTES)),
        bytes((i * 59 + 13 + seed) & 0xFF for i in range(BANK_BYTES)),
    )


def _ring_metadata_ok(status, payload_crc16):
    bits = BANK_BYTES * 8
    for bank in (0, 1):
        if not status["b%d_valid" % bank]:
            return False
        if status["b%d_length" % bank] != bits:
            return False
        if status["b%d_crc" % bank] != payload_crc16[bank]:
            return False
    return status["active"] == 0 and status["error"] == 0


def _wait_active_bank(uart, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = _pp_status(uart)
        if st["error"]:
            raise GateError("M4 playback ERROR %02X" % st["error"])
        if st["active"] and st["active_bank"] in ("0", "1"):
            return int(st["active_bank"])
        time.sleep(0.01)
    raise GateError("M4 timed out waiting for active bank")


def _wait_bank(uart, bank, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = _pp_status(uart)
        if st["error"]:
            raise GateError("M4 playback ERROR %02X" % st["error"])
        if st["active"] and st["active_bank"] == str(bank):
            return st
        time.sleep(0.01)
    raise GateError("M4 timed out waiting for bank %d" % bank)


def _wait_idle(uart, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = _pp_status(uart)
        if st["error"]:
            raise GateError("M4 playback ERROR %02X" % st["error"])
        if not st["active"] and st["state"] == "IDLE":
            return st
        time.sleep(0.01)
    raise GateError("M4 timed out waiting for IDLE")


def _m4_bench(uart, label="M4"):
    """Read back the DMA BENCH line: PP underflow counters and the last bank
    reload gap.  SET DMA BENCH uses a separate DDR buffer and does not reset
    DDR_PP_TIMELINE, so the just-finished PP playback is reflected here."""
    line = uart.cmd("SET DMA BENCH", M4_BENCH_TIMEOUT_S)
    if not line or not line.startswith("DMA BENCH PASS"):
        raise GateError("%s DMA BENCH readback failed: %r" % (label, line))
    match = M4_BENCH_RE.search(line)
    if not match:
        raise GateError("%s DMA BENCH underflow/timeline fields missing: %r" %
                        (label, line))
    return (int(match.group("next_submit_ticks")),
            int(match.group("pp0_underflow")),
            int(match.group("pp1_underflow")))


def run_m4(client, uart, rounds=M4_ROUNDS, poll_timeout_s=M3_POLL_TIMEOUT_S):
    uart.cmd("SET DDR PP STOP")
    _expect_ok(uart, "SET DIGMOD MODE OFF")
    preload = {}
    base = make_ring_payloads(0)
    for bank in (0, 1):
        payload = base[bank]
        res = client.upload(bank, payload)
        if res.device_error is not ReplyError.NONE:
            raise GateError("M4 preload bank %d device error" % bank)
        if res.payload_crc32 != res.device_crc32 or res.bytes_sent != BANK_BYTES:
            raise GateError("M4 preload bank %d CRC mismatch" % bank)
        preload[bank] = payload
    status = _pp_status(uart)
    if not _ring_metadata_ok(status, {b: _crc16_ccitt_fast(preload[b]) for b in (0, 1)}):
        raise GateError("M4 preload metadata mismatch")
    _expect_ok(uart, "SET DIGMOD FILTER RECT")
    _expect_ok(uart, "SET DIGMOD ROLLOFF 0.35")
    _expect_ok(uart, "SET DIGMOD SYMRATE 1000000")
    _expect_ok(uart, "SET DIGMOD MODE QAM16")
    if uart.cmd("SET DDR PP START") != "OK":
        raise GateError("M4 SET DDR PP START rejected")
    switch_order = []
    try:
        for r in range(rounds):
            # wait until a bank is active and known
            active = _wait_active_bank(uart, poll_timeout_s)
            other = 1 - active
            payload = make_ring_payloads(rounds + 1 + r)[other]
            res = client.upload(other, payload)
            if res.device_error is not ReplyError.NONE:
                raise GateError("M4 refill bank %d device error" % other)
            if res.payload_crc32 != res.device_crc32:
                raise GateError("M4 refill bank %d CRC mismatch" % other)
            switched = _wait_bank(uart, other, poll_timeout_s)
            # The just-refilled bank must be the one playing its new content.
            expected_crc16 = _crc16_ccitt_fast(payload)
            if (switched["b%d_length" % other] != BANK_BYTES * 8 or
                    switched["b%d_crc" % other] != expected_crc16):
                raise GateError(
                    "M4 switched bank %d metadata mismatch: LENGTH %d CRC %04X "
                    "!= %d/%04X" %
                    (other, switched["b%d_length" % other],
                     switched["b%d_crc" % other], BANK_BYTES * 8, expected_crc16))
            switch_order.append(str(other))
        # stop refilling -> natural end
        final = _wait_idle(uart, poll_timeout_s)
        expected_order = [str(1 - (r % 2)) for r in range(rounds)]
        if switch_order != expected_order:
            raise GateError("M4 switch order %s != expected %s" %
                            (switch_order, expected_order))
        next_submit, pp0_underflow, pp1_underflow = _m4_bench(uart)
        if pp0_underflow or pp1_underflow:
            raise GateError("M4 playback underflow PP0=%d PP1=%d" %
                            (pp0_underflow, pp1_underflow))
    except GateError:
        uart.cmd("SET DDR PP STOP")
        raise
    _expect_ok(uart, "SET DIGMOD MODE OFF")
    return {"switch_order": switch_order, "final_state": final["state"],
            "final_error": final["error"], "rounds": rounds,
            "next_submit_ticks": next_submit,
            "pp0_underflow": pp0_underflow, "pp1_underflow": pp1_underflow}


M5_ROUNDS = 3


def _ring_payload_queue(rounds):
    """Distinct 1 MiB payloads the host RingFeeder cycles through."""
    return [make_ring_payloads(1 + index)[index % 2]
            for index in range(max(1, rounds))]


def _pp_status_gui(uart):
    """GET DDR PP parsed with the panel's parser, retrying past stray lines."""
    from host.siggen.protocol import parse_ddr_pp_status

    last = None
    for _ in range(6):
        try:
            return parse_ddr_pp_status(uart.cmd("GET DDR PP"))
        except ValueError as exc:
            last = exc
            continue
    raise GateError("GET DDR PP gave no panel-parseable status: %s" % last)


def run_m5(client, uart, rounds=M5_ROUNDS, poll_timeout_s=M3_POLL_TIMEOUT_S):
    """Ring refill driven by the host GUI logic instead of the gate's own loop.

    GET DDR PP is parsed with the panel's parser and fed to the real RingFeeder,
    so this gate guards the GUI's active/next-bank decisions against the board:
    a refill must target the non-active bank, the played bank's LENGTH/CRC16 must
    match the content just committed, and the switch order must alternate."""
    from host.siggen.ring_feeder import RingFeeder

    uart.cmd("SET DDR PP STOP")
    _expect_ok(uart, "SET DIGMOD MODE OFF")
    preload = make_ring_payloads(0)
    for bank in (0, 1):
        result = client.upload(bank, preload[bank])
        if (result.device_error is not ReplyError.NONE or
                result.payload_crc32 != result.device_crc32 or
                result.bytes_sent != BANK_BYTES):
            raise GateError("M5 preload bank %d upload failed" % bank)
    status = _pp_status(uart)
    if not _ring_metadata_ok(
            status, {bank: _crc16_ccitt_fast(preload[bank]) for bank in (0, 1)}):
        raise GateError("M5 preload metadata mismatch")

    mode, filt, rolloff, symrate = M3_PROFILE
    for command in ("SET DIGMOD FILTER %s" % filt,
                    "SET DIGMOD ROLLOFF %s" % rolloff,
                    "SET DIGMOD SYMRATE %d" % symrate,
                    "SET DIGMOD MODE %s" % mode):
        _expect_ok(uart, command)
    if uart.cmd("SET DDR PP START") != "OK":
        raise GateError("M5 SET DDR PP START rejected")

    queue = _ring_payload_queue(rounds)
    feeder = RingFeeder()
    expected = {}
    switch_order = []
    last_active = None
    deadline = time.monotonic() + poll_timeout_s
    try:
        while len(switch_order) < rounds and time.monotonic() < deadline:
            status = _pp_status_gui(uart)
            if status["error_code"]:
                raise GateError("M5 playback ERROR %02X" % status["error_code"])
            active = status["active_bank"] if status["active"] else None
            if active is not None and active != last_active:
                if last_active is not None:
                    switch_order.append(active)
                    if active in expected:
                        info = status["bank%d" % active]
                        if (info["bit_length"] != BANK_BYTES * 8 or
                                info["crc16"] != expected.pop(active)):
                            raise GateError(
                                "M5 bank %d metadata mismatch after switch" % active)
                last_active = active
                if len(switch_order) >= rounds:
                    break
            feed = feeder.next_feed(status, len(queue), False)
            if feed is not None:
                bank, index = feed
                result = client.upload(bank, queue[index])
                if (result.device_error is not ReplyError.NONE or
                        result.payload_crc32 != result.device_crc32):
                    raise GateError("M5 refill bank %d upload failed" % bank)
                expected[bank] = _crc16_ccitt_fast(queue[index])
            time.sleep(0.01)
        if len(switch_order) < rounds:
            raise GateError("M5 timed out after %.1fs" % poll_timeout_s)
        expected_order = [1 - (index % 2) for index in range(rounds)]
        if switch_order != expected_order:
            raise GateError("M5 switch order %s != expected %s" %
                            (switch_order, expected_order))
        final = _wait_idle(uart, poll_timeout_s)
        next_submit, pp0_underflow, pp1_underflow = _m4_bench(uart, "M5")
        if pp0_underflow or pp1_underflow:
            raise GateError("M5 playback underflow PP0=%d PP1=%d" %
                            (pp0_underflow, pp1_underflow))
    except GateError:
        uart.cmd("SET DDR PP STOP")
        raise
    _expect_ok(uart, "SET DIGMOD MODE OFF")
    return {"switch_order": switch_order, "final_state": final["state"],
            "final_error": final["error"], "rounds": rounds,
            "next_submit_ticks": next_submit,
            "pp0_underflow": pp0_underflow, "pp1_underflow": pp1_underflow}


def _print_m0(result):
    print("M0 ARP %s MAC %s" % (result["arp"], result["mac"]))
    print("M0 ECHO STATE %s ERROR %s" %
          (result["echo"].state.name, result["echo"].error.name))
    if result["net"] is not None:
        print("M0 NET INIT %d LINK %s IP %s UDP %d" %
              (result["net"]["init"], result["net"]["link"],
               result["net"]["ip"], result["net"]["port"]))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("m0", "m1", "m2", "m3", "m4", "m5"))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--serial", dest="serial_port")
    parser.add_argument("--bank", type=int, choices=(0, 1), default=None)
    args = parser.parse_args(argv)
    bank = args.bank if args.bank is not None else (0 if args.gate == "m1" else 1)
    try:
        if args.gate == "m0":
            client = UdpDdrPpClient(args.host, args.port)
            serial_query = (
                (lambda: _serial_query(args.serial_port, "GET NET"))
                if args.serial_port else None
            )
            result = run_m0(client, lookup_arp, serial_query,
                            args.host, args.port)
            _print_m0(result)
            print("M0 PASS")
        elif args.gate == "m1":
            result = run_m1(args.host, args.port, bank, args.serial_port)
            print("M1 BANK %d BYTES %d HOST_CRC32 %08X DEVICE_CRC32 %08X RETRANSMITS %d" %
                  (result["bank"], result["bytes"], result["payload_crc32"],
                   result["device_crc32"], result["retransmits"]))
            print("M1 NEGATIVE_CASES %s" % ",".join(result["negative_cases"]))
            print("M1 PASS")
        elif args.gate == "m3":
            if not args.serial_port:
                raise SystemExit("m3 requires --serial COM..")
            client = UdpDdrPpClient(args.host, args.port)
            with M3Uart(args.serial_port) as uart:
                result = run_m3(client, uart)
            print("M3 BANKS 2 BYTES %d" % result["bytes"])
            for bank in (0, 1):
                print("M3 BANK%d CRC32 %08X DEVICE %08X RETX %d" %
                      (bank, result["payload_crc32"][bank],
                       result["device_crc32"][bank],
                       result["retransmits"][bank]))
            print("M3 PLAYBACK ORDER %s STATE %s ERROR %d" %
                  (",".join(result["bank_order"]), result["final_state"],
                   result["final_error"]))
            print("M3 PASS")
        elif args.gate == "m4":
            if not args.serial_port:
                raise SystemExit("m4 requires --serial COM..")
            client = UdpDdrPpClient(args.host, args.port)
            with M3Uart(args.serial_port) as uart:
                result = run_m4(client, uart)
            print("M4 SWITCH ORDER %s STATE %s ERROR %02X" %
                  (",".join(result["switch_order"]), result["final_state"],
                   result["final_error"]))
            print("M4 NEXT_SUBMIT_TICKS %d UNDERFLOW PP0 %d PP1 %d" %
                  (result["next_submit_ticks"], result["pp0_underflow"],
                   result["pp1_underflow"]))
            print("M4 PASS")
        elif args.gate == "m5":
            if not args.serial_port:
                raise SystemExit("m5 requires --serial COM..")
            client = UdpDdrPpClient(args.host, args.port)
            with M3Uart(args.serial_port) as uart:
                result = run_m5(client, uart)
            print("M5 SWITCH ORDER %s STATE %s ERROR %02X" %
                  (",".join(str(bank) for bank in result["switch_order"]),
                   result["final_state"], result["final_error"]))
            print("M5 NEXT_SUBMIT_TICKS %d UNDERFLOW PP0 %d PP1 %d" %
                  (result["next_submit_ticks"], result["pp0_underflow"],
                   result["pp1_underflow"]))
            print("M5 PASS")
        else:
            result = run_m2(args.host, args.port, bank)
            print("M2 BYTES %d ELAPSED_S %.6f EFFECTIVE_MB_S %.3f" %
                  (result["bytes_sent"], result["elapsed_s"],
                   result["effective_mb_s"]))
            print("M2 RETRANSMITS %d HOST_CRC32 %08X DEVICE_CRC32 %08X ERROR %d" %
                  (result["retransmits"], result["payload_crc32"],
                   result["device_crc32"], result["device_error"]))
            print("M2 PASS")
    except (GateError, OSError, serial.SerialException) as exc:
        print("%s: %s" % (args.gate.upper(), exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
