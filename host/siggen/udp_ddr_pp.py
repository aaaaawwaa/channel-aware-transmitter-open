"""UDP DDR PP wire codec (host side).

Implements the 32-byte network-byte-order header shared with the board
firmware, the reply-status payload decoder, and the bounded-window UDP
uploader used to preload stopped DDR PP banks. Codec functions consume and
produce byte strings only; they have no Qt, serial, controller, or socket
dependency.
"""
import secrets
import socket
import struct
import time
import zlib
from dataclasses import dataclass
from enum import IntEnum

from PySide6.QtCore import QThread, Signal


UDP_DDR_PP_MAGIC = b"ADPP"
UDP_DDR_PP_VERSION = 1
HEADER = struct.Struct("!4sBBHIIIIII")
HEADER_SIZE = HEADER.size
MAX_PAYLOAD_BYTES = 1400
BANK_COUNT = 2
BANK_BYTES = 1 << 20
ECHO_BANK = 0xFFFF
STATUS_PAYLOAD_SIZE = 20
DEFAULT_HOST = "192.0.2.10"
DEFAULT_PORT = 40100


class MessageType(IntEnum):
    ECHO = 1
    BEGIN = 2
    DATA = 3
    STATUS = 4
    COMMIT = 5
    ABORT = 6
    REPLY = 0x80


class ReplyState(IntEnum):
    IDLE = 0
    RECEIVING = 1
    COMMITTED = 2
    ABORTED = 3
    REJECTED = 4


class ReplyError(IntEnum):
    NONE = 0
    HEADER = 1
    SESSION = 2
    BUSY = 3
    RANGE = 4
    CRC = 5
    INCOMPLETE = 6
    TIMEOUT = 7
    CONFLICT = 8


@dataclass(frozen=True)
class Packet:
    type: MessageType
    bank: int
    session_id: int
    sequence: int
    offset: int
    total_bytes: int
    crc32: int
    payload: bytes


@dataclass(frozen=True)
class ReplyStatus:
    state: ReplyState
    error: ReplyError
    received_bytes: int
    first_missing_offset: int
    first_missing_length: int


STATUS = struct.Struct("!IIIII")


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _check_common(type_value: int) -> MessageType:
    try:
        return MessageType(type_value)
    except ValueError:
        raise ValueError("unknown message type %d" % type_value) from None


def _validate(packet: Packet, is_reply: bool) -> None:
    if len(packet.payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds %d bytes" % MAX_PAYLOAD_BYTES)
    if not 0 <= packet.bank <= 1 and packet.bank != ECHO_BANK:
        raise ValueError("invalid bank %d" % packet.bank)
    if not 0 <= packet.total_bytes <= BANK_BYTES:
        raise ValueError("total_bytes out of range")
    if not 0 <= packet.offset <= 0xFFFFFFFF:
        raise ValueError("offset out of range")
    if not 0 <= packet.crc32 <= 0xFFFFFFFF:
        raise ValueError("crc32 out of range")
    if not 0 <= packet.sequence <= 0xFFFFFFFF:
        raise ValueError("sequence out of range")
    if is_reply:
        if len(packet.payload) != STATUS_PAYLOAD_SIZE:
            raise ValueError("reply payload must be %d bytes" % STATUS_PAYLOAD_SIZE)
        if packet.bank == ECHO_BANK and packet.session_id != 0:
            raise ValueError("echo reply must use session 0")
        return
    if packet.type is MessageType.ECHO:
        if packet.bank != ECHO_BANK or packet.session_id != 0:
            raise ValueError("ECHO must use bank 0xffff and session 0")
        if packet.payload or packet.sequence or packet.offset or packet.total_bytes or packet.crc32:
            raise ValueError("ECHO must carry no transaction fields")
    elif packet.type is MessageType.REPLY:
        raise ValueError("cannot encode a REPLY on the wire")
    else:
        if packet.bank == ECHO_BANK:
            raise ValueError("bank 0xffff is reserved for ECHO")
        if packet.session_id == 0:
            raise ValueError("transaction messages need a non-zero session")
        if packet.type is MessageType.DATA:
            if not 4 <= len(packet.payload) <= MAX_PAYLOAD_BYTES:
                raise ValueError("DATA payload must be 4..%d bytes" % MAX_PAYLOAD_BYTES)
            if len(packet.payload) % 4 or packet.offset % 4:
                raise ValueError("DATA offset/length must be four-byte aligned")
            if packet.total_bytes == 0 or packet.total_bytes % 4:
                raise ValueError("DATA total_bytes must be non-zero and aligned")
            if packet.offset + len(packet.payload) > packet.total_bytes:
                raise ValueError("DATA range exceeds total_bytes")
        else:
            if packet.payload:
                raise ValueError("%s must carry no payload" % packet.type.name)
            if packet.type in (MessageType.BEGIN, MessageType.COMMIT):
                if packet.total_bytes == 0 or packet.total_bytes % 4:
                    raise ValueError("%s total_bytes must be non-zero and aligned" % packet.type.name)


def encode_packet(packet: Packet) -> bytes:
    if isinstance(packet.type, MessageType):
        if packet.type is MessageType.REPLY:
            raise ValueError("cannot encode a REPLY request")
        kind = packet.type
    else:
        raise ValueError("packet type must be a MessageType")
    _validate(packet, is_reply=False)
    header = HEADER.pack(
        UDP_DDR_PP_MAGIC,
        UDP_DDR_PP_VERSION,
        int(kind),
        packet.bank,
        packet.session_id,
        packet.sequence,
        packet.offset,
        packet.total_bytes,
        len(packet.payload),
        packet.crc32,
    )
    return header + packet.payload


def decode_packet(wire: bytes) -> Packet:
    if len(wire) < HEADER_SIZE:
        raise ValueError("datagram shorter than header")
    (magic, version, type_value, bank, session_id, sequence,
     offset, total_bytes, payload_len, crc32_value) = HEADER.unpack(wire[:HEADER_SIZE])
    if magic != UDP_DDR_PP_MAGIC:
        raise ValueError("bad magic")
    if version != UDP_DDR_PP_VERSION:
        raise ValueError("bad version %d" % version)
    if len(wire) != HEADER_SIZE + payload_len:
        raise ValueError("datagram length does not match payload_len")
    kind = _check_common(type_value)
    payload = bytes(wire[HEADER_SIZE:])
    packet = Packet(kind, bank, session_id, sequence, offset, total_bytes, crc32_value, payload)
    _validate(packet, is_reply=(kind is MessageType.REPLY))
    return packet


def decode_reply_status(payload: bytes) -> ReplyStatus:
    if len(payload) != STATUS_PAYLOAD_SIZE:
        raise ValueError("reply status payload must be %d bytes" % STATUS_PAYLOAD_SIZE)
    state_value, error_value, received_bytes, first_missing_offset, first_missing_length = STATUS.unpack(payload)
    try:
        state = ReplyState(state_value)
    except ValueError:
        raise ValueError("unknown reply state %d" % state_value) from None
    try:
        error = ReplyError(error_value)
    except ValueError:
        raise ValueError("unknown reply error %d" % error_value) from None
    return ReplyStatus(state, error, received_bytes, first_missing_offset, first_missing_length)


class UdpDdrPpUploadError(Exception):
    """Terminal failure of a UDP DDR PP upload transaction."""

    def __init__(self, message: str, error: ReplyError = ReplyError.NONE):
        super().__init__(message)
        self.error = error


@dataclass
class UploadResult:
    bytes_sent: int
    elapsed_s: float
    effective_mb_s: float
    retransmits: int
    payload_crc32: int
    device_crc32: int
    device_error: ReplyError = ReplyError.NONE


def encode_reply(request: Packet, status: ReplyStatus, total_bytes: int = 0,
                 crc32_value: int = 0) -> bytes:
    """Build the REPLY datagram a device sends for ``request``.

    Used by host-side tests and loopback tooling; the upload client itself
    only ever decodes replies.
    """
    if request.type is MessageType.REPLY:
        raise ValueError("cannot reply to a REPLY")
    payload = STATUS.pack(
        int(status.state), int(status.error), status.received_bytes,
        status.first_missing_offset, status.first_missing_length,
    )
    header = HEADER.pack(
        UDP_DDR_PP_MAGIC,
        UDP_DDR_PP_VERSION,
        int(MessageType.REPLY),
        request.bank,
        request.session_id,
        request.sequence,
        0,
        total_bytes,
        len(payload),
        crc32_value,
    )
    return header + payload


_SEQ_STATUS = 0xFFFFFFFE
_SEQ_COMMIT = 0xFFFFFFFF
_SEQ_ABORT = 0xFFFFFFFD


class UdpDdrPpClient:
    """Bounded sliding-window UDP uploader for one board endpoint.

    Transaction sequence: BEGIN -> DATA window -> STATUS -> COMMIT, with a
    best-effort ABORT on any terminal device error. The client is
    thread-compatible but not thread-safe; run one transaction at a time.
    """

    def __init__(self, host: str, port: int, *, timeout_s: float = 0.15,
                 window_size: int = 8, socket_factory=socket.socket,
                 max_idle_rounds: int = 20):
        self._host = host
        self._port = port
        self._timeout_s = timeout_s
        self._window_size = window_size
        self._socket_factory = socket_factory
        self._max_idle_rounds = max_idle_rounds
        self.cancelled = False

    # ---- echo ----
    def echo(self) -> ReplyStatus:
        request = Packet(MessageType.ECHO, ECHO_BANK, 0, 0, 0, 0, 0, b"")
        sock = self._open_socket()
        try:
            sock.sendto(encode_packet(request), self._target())
            reply = self._recv_reply(sock, session=0, bank=ECHO_BANK, sequences={0})
            return decode_reply_status(reply.payload)
        finally:
            sock.close()

    # ---- upload ----
    def upload(self, bank: int, payload, progress=None) -> UploadResult:
        if isinstance(bank, bool) or bank not in (0, 1):
            raise ValueError("bank must be 0 or 1")
        payload = bytes(payload)
        total = len(payload)
        if total % 4 or not 4 <= total <= BANK_BYTES:
            raise ValueError(
                "payload length must be 4..%d bytes and four-byte aligned" % BANK_BYTES)
        session = secrets.randbits(32) or 1
        full_crc = crc32(payload)
        chunks = []
        offset = 0
        while offset < total:
            length = min(MAX_PAYLOAD_BYTES, total - offset)
            chunks.append(Packet(MessageType.DATA, bank, session, len(chunks),
                                 offset, total, crc32(payload[offset:offset + length]),
                                 payload[offset:offset + length]))
            offset += length
        sock = self._open_socket()
        started = time.monotonic()
        retransmits = 0
        try:
            begin = Packet(MessageType.BEGIN, bank, session, 0, 0, total, full_crc, b"")
            reply = self._exchange(sock, begin, {0})
            self._raise_for_error(reply, begin)
            received, miss_start, miss_len = self._progress_of(reply)
            if progress is not None:
                progress(received, total)
            in_flight = {}
            next_seq = 0
            sent_seqs = set()
            idle_rounds = 0
            while True:
                if self.cancelled:
                    raise UdpDdrPpUploadError("upload cancelled")
                for seq in [s for s, pkt in in_flight.items()
                            if pkt.offset + len(pkt.payload) <= miss_start]:
                    del in_flight[seq]
                if (next_seq >= len(chunks) and not in_flight and
                        miss_len == 0 and received >= total):
                    break
                while len(in_flight) < self._window_size and next_seq < len(chunks):
                    packet = chunks[next_seq]
                    sock.sendto(encode_packet(packet), self._target())
                    in_flight[next_seq] = packet
                    sent_seqs.add(next_seq)
                    next_seq += 1
                if in_flight:
                    try:
                        reply = self._recv_reply(sock, session, bank, sent_seqs)
                        idle_rounds = 0
                    except socket.timeout:
                        idle_rounds += 1
                        if idle_rounds > self._max_idle_rounds:
                            self._abort(bank, session, total)
                            raise UdpDdrPpUploadError(
                                "device stopped responding after %d timeouts" %
                                idle_rounds)
                        victims = [pkt for pkt in in_flight.values()
                                   if self._intersects(pkt, miss_start, miss_len)]
                        if not victims:
                            victims = list(in_flight.values())
                        for packet in victims:
                            sock.sendto(encode_packet(packet), self._target())
                            retransmits += 1
                        continue
                    self._raise_for_error(reply, None)
                    received, miss_start, miss_len = self._progress_of(reply)
                    if progress is not None:
                        progress(received, total)
                elif miss_len:
                    status = Packet(MessageType.STATUS, bank, session, _SEQ_STATUS,
                                    0, total, 0, b"")
                    reply = self._exchange(sock, status, {_SEQ_STATUS})
                    self._raise_for_error(reply, status)
                    received, miss_start, miss_len = self._progress_of(reply)
                    if progress is not None:
                        progress(received, total)
            status = Packet(MessageType.STATUS, bank, session, _SEQ_STATUS,
                            0, total, 0, b"")
            reply = self._exchange(sock, status, {_SEQ_STATUS})
            self._raise_for_error(reply, status)
            final_received, final_miss_start, final_miss_len = self._progress_of(reply)
            if final_received != total or final_miss_len:
                raise UdpDdrPpUploadError(
                    "device reports incomplete transfer: %d/%d bytes" %
                    (final_received, total))
            commit = Packet(MessageType.COMMIT, bank, session, _SEQ_COMMIT,
                            0, total, full_crc, b"")
            reply = self._exchange(sock, commit, {_SEQ_COMMIT})
            self._raise_for_error(reply, commit)
            result_status = decode_reply_status(reply.payload)
            if result_status.state is not ReplyState.COMMITTED:
                raise UdpDdrPpUploadError(
                    "device did not commit: state %s" % result_status.state.name)
            elapsed = time.monotonic() - started
            return UploadResult(
                bytes_sent=total,
                elapsed_s=elapsed,
                effective_mb_s=(total / elapsed / 1e6) if elapsed > 0 else 0.0,
                retransmits=retransmits,
                payload_crc32=full_crc,
                device_crc32=reply.crc32,
                device_error=ReplyError.NONE,
            )
        except UdpDdrPpUploadError:
            raise
        except OSError as exc:
            raise UdpDdrPpUploadError(str(exc)) from exc
        finally:
            sock.close()

    # ---- internals ----
    def _target(self):
        return (self._host, self._port)

    def _open_socket(self):
        sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self._timeout_s)
        return sock

    def _exchange(self, sock, packet, sequences):
        """Send a control packet and await its reply, resending on timeout.

        Gives up after ``max_idle_rounds`` unanswered retransmissions and
        raises ``UdpDdrPpUploadError`` with a best-effort ABORT sent first.
        """
        wire = encode_packet(packet)
        idle_rounds = 0
        while True:
            if self.cancelled:
                raise UdpDdrPpUploadError("upload cancelled")
            sock.sendto(wire, self._target())
            try:
                return self._recv_reply(sock, packet.session_id, packet.bank, sequences)
            except socket.timeout:
                idle_rounds += 1
                if idle_rounds > self._max_idle_rounds:
                    self._abort(packet.bank, packet.session_id, packet.total_bytes)
                    raise UdpDdrPpUploadError(
                        "device stopped responding after %d timeouts" % idle_rounds)

    def _recv_reply(self, sock, session, bank, sequences):
        while True:
            if self.cancelled:
                raise UdpDdrPpUploadError("upload cancelled")
            wire, _addr = sock.recvfrom(HEADER_SIZE + MAX_PAYLOAD_BYTES + 256)
            try:
                packet = decode_packet(wire)
            except ValueError:
                continue
            if packet.type is not MessageType.REPLY:
                continue
            if packet.session_id != session or packet.bank != bank:
                continue
            if sequences is not None and packet.sequence not in sequences:
                continue
            return packet

    @staticmethod
    def _progress_of(reply):
        status = decode_reply_status(reply.payload)
        return (status.received_bytes, status.first_missing_offset,
                status.first_missing_length)

    def _raise_for_error(self, reply, request):
        status = decode_reply_status(reply.payload)
        if status.error is not ReplyError.NONE:
            self._abort(reply.bank, reply.session_id, reply.total_bytes)
            raise UdpDdrPpUploadError(
                "device rejected %s: %s" %
                (request.type.name if request is not None else "transaction",
                 status.error.name),
                status.error)

    def _abort(self, bank, session, total):
        sock = self._open_socket()
        try:
            packet = Packet(MessageType.ABORT, bank, session, _SEQ_ABORT,
                            0, total, 0, b"")
            sock.sendto(encode_packet(packet), self._target())
        except OSError:
            pass
        finally:
            sock.close()

    @staticmethod
    def _intersects(packet, miss_start, miss_len):
        if miss_len <= 0:
            return False
        start = packet.offset
        end = packet.offset + len(packet.payload)
        return start < miss_start + miss_len and end > miss_start


class UdpDdrPpWorker(QThread):
    """Runs one UDP DDR PP upload off the GUI thread."""

    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, client, bank, payload, parent=None):
        super().__init__(parent)
        self._client = client
        self._bank = bank
        self._payload = bytes(payload)

    def run(self):
        try:
            result = self._client.upload(
                self._bank, self._payload,
                progress=lambda sent, total: self.progress.emit(sent, total),
            )
        except UdpDdrPpUploadError as exc:
            self.failed.emit(str(exc))
        except OSError as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)

    def stop(self):
        """Cooperatively cancel the running transaction and join the thread."""
        self._client.cancelled = True
        self.wait(3000)
