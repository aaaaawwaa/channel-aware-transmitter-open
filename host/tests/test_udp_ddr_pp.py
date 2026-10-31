import socket
from collections import deque

import pytest

from host.siggen.udp_ddr_pp import (
    BANK_BYTES,
    HEADER_SIZE,
    MAX_PAYLOAD_BYTES,
    MessageType,
    Packet,
    ReplyError,
    ReplyState,
    ReplyStatus,
    UdpDdrPpClient,
    UdpDdrPpUploadError,
    UploadResult,
    crc32,
    decode_packet,
    decode_reply_status,
    encode_packet,
    encode_reply,
)


def _data_packet(**overrides):
    fields = dict(
        type=MessageType.DATA,
        bank=1,
        session_id=0x10203040,
        sequence=7,
        offset=0x00001000,
        total_bytes=0x00010000,
        crc32=0xE08AB900,
        payload=b"\x10\x20\x30\x40",
    )
    fields.update(overrides)
    return Packet(**fields)


def test_data_packet_is_a_32_byte_network_order_header_followed_by_payload():
    packet = _data_packet()
    wire = encode_packet(packet)
    assert HEADER_SIZE == 32
    assert wire.hex() == (
        "414450500103000110203040000000070000100000010000"
        "00000004e08ab90010203040"
    )
    assert decode_packet(wire) == packet


def test_codec_rejects_non_fragment_safe_or_malformed_data_packets():
    packet = Packet(MessageType.DATA, 0, 1, 0, 0, 4, 0x2144DF1C, b"\0\0\0\0")
    assert len(encode_packet(packet)) == HEADER_SIZE + 4
    for bad in (b"", b"ADPP", encode_packet(packet)[:-1]):
        with pytest.raises(ValueError):
            decode_packet(bad)
    assert MAX_PAYLOAD_BYTES == 1400


def test_reply_status_is_network_order_and_exposes_missing_range():
    status = decode_reply_status(bytes.fromhex(
        "00000001000000000000057800000af000000578"
    ))
    assert status.state is ReplyState.RECEIVING
    assert status.error is ReplyError.NONE
    assert status.received_bytes == 1400
    assert status.first_missing_offset == 2800
    assert status.first_missing_length == 1400


def test_wrong_magic_or_version_is_rejected():
    good = encode_packet(_data_packet())
    with pytest.raises(ValueError):
        decode_packet(b"XDPP" + good[4:])
    with pytest.raises(ValueError):
        decode_packet(good[:4] + bytes([2]) + good[5:])


def test_unknown_type_is_rejected():
    good = encode_packet(_data_packet())
    with pytest.raises(ValueError):
        decode_packet(good[:5] + bytes([9]) + good[6:])


def test_payload_len_mismatch_is_rejected():
    wire = encode_packet(_data_packet())
    longer = wire + b"\x00\x00\x00\x00"
    with pytest.raises(ValueError):
        decode_packet(longer)


def test_invalid_bank_is_rejected():
    with pytest.raises(ValueError):
        encode_packet(_data_packet(bank=2))
    with pytest.raises(ValueError):
        encode_packet(_data_packet(bank=0xFFFE))


def test_echo_bank_and_session_are_forced():
    echo = Packet(MessageType.ECHO, 0xFFFF, 0, 0, 0, 0, 0, b"")
    wire = encode_packet(echo)
    assert decode_packet(wire) == echo
    with pytest.raises(ValueError):
        encode_packet(Packet(MessageType.ECHO, 0, 0, 0, 0, 0, 0, b""))
    with pytest.raises(ValueError):
        encode_packet(Packet(MessageType.ECHO, 0xFFFF, 1, 0, 0, 0, 0, b""))


def test_zero_session_for_a_transaction_is_rejected():
    with pytest.raises(ValueError):
        encode_packet(_data_packet(session_id=0))
    with pytest.raises(ValueError):
        encode_packet(Packet(
            MessageType.BEGIN, 0, 0, 0, 0, 4096, crc32(b"\0" * 4096), b"",
        ))


def test_unaligned_data_offset_or_length_is_rejected():
    with pytest.raises(ValueError):
        encode_packet(_data_packet(offset=2))
    with pytest.raises(ValueError):
        encode_packet(_data_packet(payload=b"\x01\x02\x03"))


def test_data_payload_over_1400_bytes_is_rejected():
    with pytest.raises(ValueError):
        encode_packet(_data_packet(payload=b"\0" * 1404))
    with pytest.raises(ValueError):
        encode_packet(_data_packet(payload=b""))


def test_transaction_control_packets_must_be_empty_and_aligned():
    payload_crc = crc32(b"\0" * 4096)
    begin = Packet(MessageType.BEGIN, 0, 5, 0, 0, 4096, payload_crc, b"")
    assert decode_packet(encode_packet(begin)) == begin
    with pytest.raises(ValueError):
        encode_packet(Packet(MessageType.BEGIN, 0, 5, 0, 0, 4096, payload_crc, b"\1\2\3\4"))
    with pytest.raises(ValueError):
        encode_packet(Packet(MessageType.BEGIN, 0, 5, 0, 0, 0, 0, b""))
    with pytest.raises(ValueError):
        encode_packet(Packet(MessageType.BEGIN, 0, 5, 0, 0, 4094, payload_crc, b""))
    with pytest.raises(ValueError):
        encode_packet(Packet(
            MessageType.COMMIT, 0, 5, 0xFFFFFFFF, 0, 4097, payload_crc, b"",
        ))
    with pytest.raises(ValueError):
        encode_packet(Packet(
            MessageType.ABORT, 1, 5, 0xFFFFFFFD, 0, 0, 0, b"\0\0\0\0",
        ))
    with pytest.raises(ValueError):
        encode_packet(Packet(MessageType.BEGIN, 0, 5, 0, 0, BANK_BYTES + 4, payload_crc, b""))


def test_status_packet_is_empty_and_session_bound():
    status = Packet(MessageType.STATUS, 1, 9, 0xFFFFFFFE, 0, 0, 0, b"")
    assert decode_packet(encode_packet(status)) == status


def test_crc32_matches_ieee_reflected_polynomial():
    assert crc32(b"123456789") == 0xCBF43926
    assert crc32(b"") == 0


TARGET = ("192.0.2.10", 40100)
_TIMEOUT = object()


class FakeUdpSocket:
    def __init__(self):
        self.sent = []
        self.replies = deque()
        self.events = []
        self.closed = False

    def settimeout(self, seconds):
        pass

    def sendto(self, data, addr):
        self.sent.append((bytes(data), addr))
        self.events.append(("send", decode_packet(data)))

    def recvfrom(self, bufsize):
        if self.replies:
            item = self.replies.popleft()
            if item is _TIMEOUT:
                self.events.append(("timeout", None))
                raise socket.timeout("fake timeout")
            self.events.append(("recv", decode_packet(item[0])))
            return item
        self.events.append(("timeout", None))
        raise socket.timeout("no replies queued")

    def close(self):
        self.closed = True


class FakeDevice:
    """Word-accurate simulation of the board UDP staging receiver."""

    def __init__(self, sock, total, full_crc):
        self.sock = sock
        self.total = total
        self.full_crc = full_crc
        self.words = set()
        self.drop_rule = lambda request: False
        self.duplicate_rule = lambda request: 0
        self.rejected_error = None
        sock.sendto = self.sendto

    def sendto(self, data, addr):
        self.sock.sent.append((bytes(data), addr))
        self.sock.events.append(("send", decode_packet(data)))
        request = decode_packet(data)
        if self.drop_rule(request):
            return
        if request.type is MessageType.BEGIN:
            if self.rejected_error is not None:
                status = _status(state=ReplyState.REJECTED, error=self.rejected_error)
            else:
                status = _status(received=0, first_missing=0, missing_len=self.total)
            self._reply(request, status, addr)
        elif request.type is MessageType.DATA:
            for off in range(request.offset, request.offset + len(request.payload), 4):
                self.words.add(off)
            status = self._compute_status()
            for _ in range(1 + self.duplicate_rule(request)):
                self._reply(request, status, addr)
        elif request.type is MessageType.STATUS:
            self._reply(request, self._compute_status(), addr)
        elif request.type is MessageType.COMMIT:
            if len(self.words) * 4 == self.total:
                self._reply(request, _status(
                    state=ReplyState.COMMITTED, received=self.total,
                    first_missing=self.total), addr, crc32_value=self.full_crc)
            else:
                self._reply(request, _status(
                    state=ReplyState.REJECTED, error=ReplyError.INCOMPLETE), addr)
        elif request.type is MessageType.ECHO:
            self._reply(request, _status(state=ReplyState.IDLE), addr)

    def _compute_status(self):
        received = len(self.words) * 4
        for off in range(0, self.total, 4):
            if off not in self.words:
                run = 0
                while off + run < self.total and (off + run) not in self.words:
                    run += 4
                return _status(received=received, first_missing=off, missing_len=run)
        return _status(received=received, first_missing=self.total, missing_len=0)

    def _reply(self, request, status, addr, crc32_value=0):
        self.sock.replies.append(
            (encode_reply(request, status, self.total, crc32_value), addr))


def _status(state=ReplyState.RECEIVING, error=ReplyError.NONE, received=0,
            first_missing=0, missing_len=0):
    return ReplyStatus(state, error, received, first_missing, missing_len)


def _make_client(sock, window_size=8, max_idle_rounds=20):
    def factory(*_args, **_kwargs):
        return sock
    return UdpDdrPpClient(*TARGET, timeout_s=0.01, window_size=window_size,
                          max_idle_rounds=max_idle_rounds,
                          socket_factory=factory)


def test_upload_opens_session_then_retransmits_only_reported_missing_packet():
    sock = FakeUdpSocket()
    payload = bytes(range(256)) * 12  # 3072 bytes -> 1400/1400/272
    total = len(payload)
    full_crc = crc32(payload)
    device = FakeDevice(sock, total, full_crc)
    dropped = set()

    def drop_first_d1(request):
        if request.type is MessageType.DATA and request.sequence == 1:
            if 1 not in dropped:
                dropped.add(1)
                return True
        return False

    device.drop_rule = drop_first_d1
    client = _make_client(sock, window_size=3)

    progress = []
    result = client.upload(0, payload, progress=lambda s, t: progress.append((s, t)))
    sent = [decode_packet(data) for data, _ in sock.sent]
    data = [packet for packet in sent if packet.type is MessageType.DATA]
    assert [packet.sequence for packet in data[:3]] == [0, 1, 2]
    assert [packet.sequence for packet in data[3:]] == [1]
    assert result.bytes_sent == len(payload)
    assert result.payload_crc32 == crc32(payload)
    assert result.retransmits == 1
    assert result.device_crc32 == full_crc
    assert result.device_error is ReplyError.NONE
    assert progress[-1] == (total, total)
    assert all(addr == TARGET for _, addr in sock.sent)
    assert sock.closed


def test_upload_aborts_session_when_begin_is_rejected():
    sock = FakeUdpSocket()
    device = FakeDevice(sock, 4, crc32(b"\x00\x01\x02\x03"))
    device.rejected_error = ReplyError.BUSY
    client = _make_client(sock)
    with pytest.raises(UdpDdrPpUploadError, match="BUSY"):
        client.upload(1, b"\x00\x01\x02\x03")
    assert decode_packet(sock.sent[-1][0]).type is MessageType.ABORT
    assert sock.closed


def test_upload_rejects_unaligned_or_oversized_payload_without_sending():
    sock = FakeUdpSocket()
    client = _make_client(sock)
    with pytest.raises(ValueError):
        client.upload(0, b"\x00\x01\x02")
    with pytest.raises(ValueError):
        client.upload(0, b"\x00" * (BANK_BYTES + 4))
    with pytest.raises(ValueError):
        client.upload(2, b"\x00\x01\x02\x03")
    assert sock.sent == []


def test_upload_waits_for_an_ack_before_filling_the_next_window_slot():
    sock = FakeUdpSocket()
    payload = bytes(range(256)) * 12  # three chunks
    device = FakeDevice(sock, len(payload), crc32(payload))
    client = _make_client(sock, window_size=2)
    client.upload(0, payload)

    kinds = [(kind, pkt.type if pkt is not None else None, pkt.sequence if pkt is not None else None)
             for kind, pkt in sock.events]
    data_events = [(index, pkt.sequence) for index, (kind, pkt) in enumerate(sock.events)
                   if kind == "send" and pkt.type is MessageType.DATA]
    first_two = [seq for _index, seq in data_events[:2]]
    third_index = data_events[2][0]
    assert first_two == [0, 1]
    # the third DATA packet must wait until a DATA reply freed a window slot
    acks_before_third = [event for event in sock.events[:third_index]
                         if event[0] == "recv" and event[1].type is MessageType.REPLY]
    assert acks_before_third
    assert data_events[2][1] == 2


def test_upload_resolves_out_of_order_gaps_and_duplicate_replies():
    sock = FakeUdpSocket()
    payload = b"\x5a" * 8
    full_crc = crc32(payload)
    device = FakeDevice(sock, len(payload), full_crc)
    device.duplicate_rule = lambda request: 1 if request.offset == 0 else 0
    client = _make_client(sock)
    result = client.upload(1, payload)
    assert result.bytes_sent == len(payload)
    assert result.device_crc32 == full_crc
    # the duplicate reply for DATA 0 must not be mistaken for STATUS/COMMIT
    statuses = [decode_packet(d) for d, _ in sock.sent
                if decode_packet(d).type is MessageType.STATUS]
    commits = [decode_packet(d) for d, _ in sock.sent
               if decode_packet(d).type is MessageType.COMMIT]
    assert len(statuses) == 1
    assert len(commits) == 1


def test_upload_aborts_when_commit_is_rejected():
    sock = FakeUdpSocket()
    payload = bytes(range(256)) * 12
    device = FakeDevice(sock, len(payload), crc32(payload))
    original_sendto = device.sendto

    def hostile_sendto(data, addr):
        request = decode_packet(data)
        if request.type is MessageType.COMMIT:
            sock.sent.append((bytes(data), addr))
            sock.events.append(("send", request))
            sock.replies.append((encode_reply(
                request, _status(state=ReplyState.REJECTED,
                                 error=ReplyError.INCOMPLETE),
                len(payload)), addr))
            return
        original_sendto(data, addr)

    sock.sendto = hostile_sendto
    client = _make_client(sock)
    with pytest.raises(UdpDdrPpUploadError, match="INCOMPLETE"):
        client.upload(0, payload)
    assert decode_packet(sock.sent[-1][0]).type is MessageType.ABORT


def test_upload_gives_up_after_bounded_idle_timeouts():
    sock = FakeUdpSocket()
    payload = bytes(range(256)) * 12
    device = FakeDevice(sock, len(payload), crc32(payload))
    original_sendto = device.sendto
    swallowed = []

    def swallowing_sendto(data, addr):
        request = decode_packet(data)
        if request.type is MessageType.DATA:
            sock.sent.append((bytes(data), addr))
            sock.events.append(("send", request))
            swallowed.append(request.sequence)
            return
        original_sendto(data, addr)

    sock.sendto = swallowing_sendto
    client = _make_client(sock, window_size=3, max_idle_rounds=2)
    with pytest.raises(UdpDdrPpUploadError, match="stopped responding"):
        client.upload(0, payload)
    # DATA was retransmitted for a bounded number of rounds, then aborted
    assert len(swallowed) == 3 + 2 * 3
    assert decode_packet(sock.sent[-1][0]).type is MessageType.ABORT
    assert sock.closed


def test_echo_returns_matching_status():
    sock = FakeUdpSocket()
    FakeDevice(sock, 0, 0)
    client = _make_client(sock)
    status = client.echo()
    assert status.state is ReplyState.IDLE
    assert status.error is ReplyError.NONE
    assert sock.closed


def test_echo_ignores_foreign_replies_and_times_out():
    sock = FakeUdpSocket()

    def reactive_sendto(data, addr):
        sock.sent.append((bytes(data), addr))
        sock.events.append(("send", decode_packet(data)))
        request = decode_packet(data)
        foreign = Packet(request.type, 0, request.session_id, 0, 0, 0, 0, b"")
        sock.replies.append((encode_reply(foreign, _status(state=ReplyState.IDLE), 0), addr))
        sock.replies.append(_TIMEOUT)

    sock.sendto = reactive_sendto
    client = _make_client(sock)
    with pytest.raises(socket.timeout):
        client.echo()
    assert sock.closed


def test_upload_result_reports_metrics_for_successful_run():
    sock = FakeUdpSocket()
    payload = bytes(range(256)) * 12
    full_crc = crc32(payload)
    FakeDevice(sock, len(payload), full_crc)
    client = _make_client(sock)
    result = client.upload(0, payload)
    assert isinstance(result, UploadResult)
    assert result.bytes_sent == 3072
    assert result.retransmits == 0
    assert result.elapsed_s >= 0
    assert result.payload_crc32 == full_crc == result.device_crc32
