"""Bounded reader for the RF daemon: status polling plus the binary RX path."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import asdict, dataclass, replace
import json
import socket
import struct
import threading
import time
from typing import Optional, Tuple

from rf_rx_types import (RX_BYTES_PER_SAMPLE, RX_DIRECTION, RX_IQ_FORMAT,
                         RX_IQ_MAX, RX_IQ_MIN, RX_MAX_RATIO, RX_MAX_SAMPLES,
                         RX_NORMAL_OP, RX_PHYSICAL_OP, RxIqFrame, RxReadback)

try:  # the single source, in every release that ships the teaching profile
    from rf_teaching_profile import ATTENUATION_MDB
except ImportError:  # pragma: no cover - only trees built under pre-profile contracts
    # Compatibility read for the software maps that predate the teaching profile
    # (cnn-v1 / rx-v1 / cnn-bpsk-v1).  Such a tree ships these CURRENT client
    # sources, so the current domain value is the correct one; the historical
    # release carried its own literal.  The equality with
    # rf_teaching_profile.ATTENUATION_MDB in the current tree is asserted by
    # test_rf_rx_client, so this pin cannot drift silently here.
    ATTENUATION_MDB = 46000

READ_ONLY_OPS = ("protocol.hello", "protocol.close", "device.info", "state.get",
                 "status.subscribe", "pl.status.get", "rf.interface_status")
MAX_LINE = 4096
# The ceiling on an opt-in settling wait, matching RF_SETTLE_WAIT_MAX_US in
# rf_daemon.h.  The client refuses the same boundary the daemon does, locally and
# before sending, so a bad value never becomes a round trip.
SETTLE_WAIT_MAX_US = 5000


@dataclass(frozen=True)
class TxStatus:
    source_selected: Optional[bool] = None
    length_words: Optional[int] = None
    position_words: Optional[int] = None


@dataclass(frozen=True)
class RxStatus:
    armed: Optional[bool] = None
    done: Optional[bool] = None
    position_words: Optional[int] = None


@dataclass(frozen=True)
class PlStatus:
    sampled_at_monotonic_ns: Optional[int] = None
    io_ok: bool = False
    tx: TxStatus = TxStatus()
    rx: RxStatus = RxStatus()
    # Optional additive diagnostics from a newer daemon. None keeps every old
    # daemon/release compatible; the raw wire response remains authoritative.
    diagnostics: Optional[dict] = None


@dataclass(frozen=True)
class RfStatusSnapshot:
    online: bool = False
    stale: bool = False
    error: Optional[str] = None
    received_at_monotonic: Optional[float] = None
    mode: Optional[str] = None
    core: Optional[bool] = None
    capabilities: Tuple[str, ...] = ()
    daemon_state: Optional[str] = None
    pending: Optional[bool] = None
    pending_op: Optional[str] = None
    lease_held: Optional[bool] = None
    release_id: Optional[str] = None
    bit_sha256: Optional[str] = None
    hwh_sha256: Optional[str] = None
    pl_status: PlStatus = PlStatus()
    # Phase 2: `armed` is a RUNTIME state, separate from `mode`.  A status-only daemon
    # stays status-only when armed, which is why the read-only verification above still
    # holds and why these are the only new fields.  `control_ops` is what the daemon says
    # it will honour right now; the client never sends anything outside it.
    armed: bool = False
    control_ops: Tuple[str, ...] = ()
    # Local evidence: a successful load on this still-valid connection.
    tx_session_ready: bool = False
    # Local evidence: a successful bounded RX capture on this still-valid, retained
    # control session.  Deliberately a SEPARATE field from tx_session_ready: an RX
    # capture is not evidence that a TX buffer was loaded, and conflating them is how
    # "the receive path works" would silently unlock tx.start.
    rx_session_ready: bool = False
    # The research contract the running daemon advertises, exactly as parsed from
    # `device.info` -- `daemon_version` is the same reply's `daemon` field.  Held
    # as the live capability so the panel can decide whether a study is possible
    # without inventing one from an installed manifest.  None means the reply did
    # not carry it, which is not the same as "0000".
    headroom: Optional[dict] = None
    daemon_version: Optional[str] = None

    def expired(self) -> "RfStatusSnapshot":
        return replace(self, stale=True, pl_status=PlStatus(),
                       tx_session_ready=False, rx_session_ready=False)

    def to_dict(self) -> dict:
        """Plain fields for the UI; a copy cannot mutate the published snapshot."""
        return asdict(self)


class StatusProtocolError(Exception):
    """A protocol-level failure, optionally carrying the daemon's error code.

    The daemon answers `ok:false` with `error:{"code":...,"message":...}`.  The panel
    has to show WHY a control action was refused, so the code is lifted here rather than
    flattened into the message (plan Task 6 Step 1: failures show the daemon's code).
    `code` defaults to None so every existing `StatusProtocolError("...")` still works
    and a transport failure with no daemon answer is distinguishable from a refusal.
    """

    def __init__(self, message="", code=None):
        super().__init__(message)
        self.code = code


def _field(obj, name, kind, nullable=False):
    value = obj.get(name)
    if value is None and nullable:
        return None
    if kind is int:
        valid = type(value) is int and value >= 0
    else:
        valid = type(value) is kind
    if not valid:
        raise StatusProtocolError("invalid status field: " + name)
    return value


def _pl_status(obj):
    if not isinstance(obj, dict):
        raise StatusProtocolError("invalid PL status")
    tx, rx = obj.get("tx"), obj.get("rx")
    if not isinstance(tx, dict) or not isinstance(rx, dict):
        raise StatusProtocolError("missing PL status groups")
    diagnostics = obj.get("diagnostics")
    if diagnostics is not None and not isinstance(diagnostics, dict):
        raise StatusProtocolError("invalid PL diagnostics")
    return PlStatus(
        sampled_at_monotonic_ns=_field(obj, "sampled_at_monotonic_ns", int),
        io_ok=_field(obj, "io_ok", bool),
        tx=TxStatus(_field(tx, "source_selected", bool, True),
                    _field(tx, "length_words", int, True),
                    _field(tx, "position_words", int, True)),
        rx=RxStatus(_field(rx, "armed", bool, True),
                    _field(rx, "done", bool, True),
                    _field(rx, "position_words", int, True)),
        diagnostics=diagnostics,
    )


def _gain_observation(info):
    """Validate raw hardware evidence independently of status and captured IQ."""
    identity = {
        "release_id": _field(info, "release_id", str, True),
        "daemon": _field(info, "daemon", str),
        "protocol_major": _field(info, "protocol_major", int),
        "protocol_minor": _field(info, "protocol_minor", int),
    }
    if identity["protocol_major"] != 1:
        raise StatusProtocolError("RF protocol major mismatch")
    if "gain_observation" not in info:
        return dict(identity, gain_observation=None, reason="UNSUPPORTED")
    gain = info["gain_observation"]
    reason = info.get("gain_observation_unavailable")
    if gain is None:
        if type(reason) is not str or not reason.strip():
            raise StatusProtocolError("missing gain observation unavailability reason")
        return dict(identity, gain_observation=None, reason=reason)
    if type(gain) is not dict or reason is not None:
        raise StatusProtocolError("invalid gain observation availability")
    if (gain.get("available") is not True or gain.get("source") != "hardware" or
            gain.get("scope") != "sequential_reads_not_atomic"):
        raise StatusProtocolError("invalid gain observation provenance")

    def integer(name, maximum):
        value = gain.get(name)
        if type(value) is not int or not 0 <= value <= maximum:
            raise StatusProtocolError("invalid gain observation field: " + name)
        return value

    # Return a new dictionary containing only validated fields; never retain or
    # publish this observation as a status snapshot, configuration, or IQ frame.
    checked = {"available": True, "source": "hardware", "scope": "sequential_reads_not_atomic"}
    for name in ("started_at_monotonic_ns", "finished_at_monotonic_ns"):
        checked[name] = integer(name, 0xFFFFFFFFFFFFFFFF)
    for name in ("rx_enable_raw", "agc_config1_raw", "agc_config2_raw"):
        checked[name] = integer(name, 255)
    checked["mode_rx1"] = integer("mode_rx1", 3)
    for name in ("gain_index_before", "gain_index_after"):
        checked[name] = integer(name, 76)
    checked["digital_gain_code"] = integer("digital_gain_code", 31)
    checked["index_stable"] = _field(gain, "index_stable", bool)
    if (checked["finished_at_monotonic_ns"] < checked["started_at_monotonic_ns"] or
            not checked["rx_enable_raw"] & 0x40 or
            not checked["agc_config2_raw"] & 0x08 or
            checked["mode_rx1"] != checked["agc_config1_raw"] & 0x03 or
            checked["index_stable"] != (checked["gain_index_before"] == checked["gain_index_after"])):
        raise StatusProtocolError("inconsistent gain observation fields")
    return dict(identity, gain_observation=checked, reason=None)


def _capture_timing_observation(info, frame, requested_wait_us=0):
    """Validate API call brackets against the original IQ frame identity."""
    identity = {
        "release_id": _field(info, "release_id", str, True),
        "daemon": _field(info, "daemon", str),
        "protocol_major": _field(info, "protocol_major", int),
        "protocol_minor": _field(info, "protocol_minor", int),
    }
    if identity["release_id"] != frame.release_id:
        raise StatusProtocolError("capture timing release does not match the IQ frame")
    if identity["protocol_major"] != 1:
        raise StatusProtocolError("RF protocol major mismatch")
    if identity["protocol_minor"] < 4 or "last_capture_timing" not in info:
        return dict(identity, last_capture_timing=None, reason="UNSUPPORTED")
    trace = info["last_capture_timing"]
    reason = info.get("capture_timing_unavailable")
    if trace is None:
        if type(reason) is not str or not reason.strip():
            raise StatusProtocolError("missing capture timing unavailability reason")
        return dict(identity, last_capture_timing=None, reason=reason)
    if type(trace) is not dict or reason is not None:
        raise StatusProtocolError("invalid capture timing availability")
    scope = "monotonic_api_brackets_not_sample_edges"
    if trace.get("available") is not True or trace.get("scope") != scope:
        raise StatusProtocolError("invalid capture timing provenance")

    def integer(name, minimum=1, maximum=0xFFFFFFFFFFFFFFFF):
        value = trace.get(name)
        if type(value) is not int or not minimum <= value <= maximum:
            raise StatusProtocolError("invalid capture timing field: " + name)
        return value

    checked = {"available": True, "scope": scope,
               "capture_request_id": integer("capture_request_id"),
               "capture_timestamp_ns": integer("capture_timestamp_ns"),
               "samples": integer("samples", 1, RX_MAX_SAMPLES),
               "ratio": integer("ratio", 0, RX_MAX_RATIO)}
    if (checked["capture_request_id"] != frame.request_id or
            checked["capture_timestamp_ns"] != frame.daemon_timestamp_ns or
            checked["samples"] != frame.samples or checked["ratio"] != frame.ratio):
        raise StatusProtocolError("capture timing identity does not match the IQ frame")
    previous = 0
    for name in ("tx_on_begin_ns", "tx_on_end_ns", "rx_arm_begin_ns", "rx_arm_end_ns",
                 "drain_begin_ns", "drain_end_ns", "tx_off_begin_ns", "tx_off_end_ns"):
        value = integer(name)
        if not previous <= value <= checked["capture_timestamp_ns"]:
            raise StatusProtocolError("unordered capture timing field: " + name)
        checked[name] = value
        previous = value
    # The settling wait is minor 5 and additive, and the FIELD is part of that
    # contract -- so its presence is checked, not assumed.  A minor-4 daemon must
    # not report it (it has no such feature), and a minor-5 daemon must report it
    # even when the value is zero: otherwise "reported zero" and "silently
    # omitted" are the same answer, which is exactly the hole the earlier shape
    # left open.  The value compared against is the one this client SENT, never a
    # caller claim.
    minor = identity["protocol_minor"]
    if "settle_wait_requested_us" in trace:
        if minor < 5:
            raise StatusProtocolError(
                "a minor-%d trace must not report a settling wait" % minor)
        wait_us = integer("settle_wait_requested_us", 0, SETTLE_WAIT_MAX_US)
    elif minor >= 5:
        raise StatusProtocolError(
            "a minor-%d trace must report the settling wait, even a zero one" % minor)
    else:
        wait_us = 0
    if wait_us != int(requested_wait_us):
        raise StatusProtocolError(
            "capture settling wait does not match the request that was sent")
    if wait_us:
        begin = integer("settle_begin_ns")
        end = integer("settle_end_ns")
        if not checked["tx_on_end_ns"] <= begin <= end <= checked["rx_arm_begin_ns"]:
            raise StatusProtocolError(
                "settle bracket is outside the TX-on..RX-arm gap")
        checked["settle_wait_requested_us"] = wait_us
        checked["settle_begin_ns"] = begin
        checked["settle_end_ns"] = end
    elif "settle_begin_ns" in trace or "settle_end_ns" in trace:
        raise StatusProtocolError(
            "a zero settling wait must not report settle brackets")
    elif "settle_wait_requested_us" in trace:
        checked["settle_wait_requested_us"] = 0
    return dict(identity, last_capture_timing=checked, reason=None)


class RfStatusClient:
    """Bounded status reader; retains one connection from explicit TX load.

    Polling and control share the I/O lock and framing state. Only controls renew
    the daemon watchdog; a retained session never reconnects or restarts itself.
    """

    def __init__(self, port=5000, timeout=0.75, control_timeout=20.0,
                 capture_timeout=3.0):
        if (not 0 < int(port) < 65536 or timeout <= 0 or control_timeout <= 0 or
                capture_timeout <= 0):
            raise ValueError("invalid port or timeout")
        self.port = int(port)
        self.timeout = float(timeout)
        self.control_timeout = float(control_timeout)
        # The capture network deadline is its own, shorter budget (spec 数据与控制链:
        # 采集网络deadline为3秒).  It never replaces rf.init/rf.configure's long one.
        self.capture_timeout = float(capture_timeout)
        self._io_lock = threading.RLock()
        self._research_owner = None
        self._research_generation = None
        self._research_socket = None
        self._lock = threading.Lock()
        # A client that has never polled has been told nothing, which is not the same
        # as being told "no control operations": both must refuse to send.  An error
        # snapshot carries no control_ops, so a failed poll is fail-closed too.
        self._snapshot = RfStatusSnapshot(error="RF daemon has not been polled")
        self._session_socket = None
        self._session_buffer = b""
        self._session_id = 0
        self._protocol_minor_seen = None
        self._tx_session_ready = False
        self._rx_session_ready = False
        # HEADROOM-1 is one study on one connection: the daemon owns the session
        # id, seals the study on disconnect, and refuses a restart in the same
        # process, so this flag exists to keep the connection alive -- never to
        # reopen one.
        self._headroom_session_ready = False
        # The research module whose `ResearchFrame` this client builds with.
        # `None` means "import it by name", which is what a standalone client
        # does; the worker hands over the module it loaded from the attested
        # release, so there is one class and not two (see `use_research_module`).
        self._research_module = None
        self._capture_settle_wait_us = 0
        self._session_saw_active = False
        # Local bindings the frame carries: which connection produced it, which
        # configuration revision it was taken under, and a monotonic sequence for the
        # local capture id.  None of these is a wire field -- the current header has
        # no config-version and no session id, so they are generated locally.
        self._connection_generation = 0
        self._config_revision = 0
        self._capture_sequence = 0
        self._frame = None

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("RF status poll deadline expired")
        return remaining

    def _read_line(self, sock, buffer, deadline):
        """One newline-terminated frame; leftover bytes are returned, never dropped.

        The RX path needs the leftover for the frame payload that follows the header
        line, and the same rule keeps a later reply that arrived early usable by the
        next request instead of being swallowed as a header.
        """
        while b"\n" not in buffer:
            if len(buffer) > MAX_LINE:
                raise StatusProtocolError("RF response line too long")
            sock.settimeout(self._remaining(deadline))
            chunk = sock.recv(4096)
            self._remaining(deadline)
            if not chunk:
                raise StatusProtocolError("RF daemon disconnected")
            buffer += chunk
        line, buffer = buffer.split(b"\n", 1)
        if len(line) > MAX_LINE:
            raise StatusProtocolError("RF response line too long")
        return line, buffer

    def _read_payload(self, sock, buffer, deadline, count):
        """Exactly count more bytes; the rest of the buffer is left for the next read."""
        while len(buffer) < count:
            sock.settimeout(self._remaining(deadline))
            chunk = sock.recv(65536)
            self._remaining(deadline)
            if not chunk:
                raise StatusProtocolError(
                    "RF daemon disconnected during the IQ payload")
            buffer += chunk
        return buffer[:count], buffer[count:]

    def _send_and_read_json(self, sock, buffer, deadline, request_id, op, args,
                            payload=b"", validate_id=True):
        """Frame one request and read its JSON reply.

        Returns (response, unread bytes).  This is the ONE copy of the wire framing
        (request line, MAX_LINE, send timeout, response line, id check) shared by the
        JSON round trip (_exchange) and the binary capture (_capture_exchange), so the
        two paths cannot drift apart.
        """
        header = (json.dumps({"id": request_id, "op": op, "args": args},
                             separators=(",", ":")) + "\n").encode("ascii")
        if len(header) > MAX_LINE:
            raise StatusProtocolError("RF request line too long")
        sock.settimeout(self._remaining(deadline))
        sock.sendall(header + payload)
        self._remaining(deadline)
        line, buffer = self._read_line(sock, buffer, deadline)
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise StatusProtocolError("malformed RF response") from exc
        if (not isinstance(response, dict) or (validate_id and
                (type(response.get("id")) is not int or response["id"] != request_id))):
            raise StatusProtocolError("wrong RF response id")
        return response, buffer

    def _exchange(self, sock, buffer, deadline, request_id, op, args, payload=b""):
        """One JSON request/response round trip over an open connection.

        Returns (result, unread bytes); binary load follows its JSON header.
        """
        response, buffer = self._send_and_read_json(
            sock, buffer, deadline, request_id, op, args, payload)
        if response.get("ok") is not True or not isinstance(response.get("result"), dict):
            # The message string stays byte-identical ("RF daemon refused " + op): the
            # only addition is the code, and only when the daemon sent a usable one.
            error = response.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            raise StatusProtocolError("RF daemon refused " + op,
                                      code if isinstance(code, str) else None)
        return response["result"], buffer

    def _remember(self, snapshot):
        with self._lock:
            self._snapshot = snapshot

    def _advertised(self):
        with self._lock:
            return self._snapshot.control_ops

    def _drop_session(self):
        sock, self._session_socket = self._session_socket, None
        self._session_buffer = b""
        self._session_id = 0
        self._protocol_minor_seen = None
        self._tx_session_ready = False
        self._rx_session_ready = False
        # A study never outlives its connection: the daemon seals it on
        # disconnect, so clearing this flag here is what makes the next research
        # request open a fresh session rather than believe it still holds one.
        self._headroom_session_ready = False
        self._capture_settle_wait_us = 0
        self._session_saw_active = False
        with self._lock:
            self._snapshot = replace(self._snapshot, tx_session_ready=False,
                                     rx_session_ready=False)
            # The frozen result belongs to the connection that produced it: an
            # unexpected disconnect discards it rather than letting it outlive the
            # evidence that it is still current.  This is also what discards the
            # loaded-TX proof, and nothing on this path arms or retries a transmit.
            self._frame = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass  # Session ownership is already cleared; preserve the original error.

    def close(self):
        """Release the connection; the daemon stops active TX on disconnect."""
        with self._io_lock:
            self._drop_session()

    def _request(self, deadline, op, args, payload=b""):
        self._session_id += 1
        result, self._session_buffer = self._exchange(
            self._session_socket, self._session_buffer, deadline,
            self._session_id, op, args, payload)
        return result

    @contextmanager
    def _connection(self, deadline):
        try:
            if self._session_socket is None:
                if self._research_generation is not None:
                    raise StatusProtocolError('research connection was lost; no reconnect')
                self._session_socket = socket.create_connection(
                    ("127.0.0.1", self.port), timeout=self._remaining(deadline))
                self._connection_generation += 1
                self._session_buffer = b""
                self._session_id = 0
                hello = self._request(deadline, "protocol.hello", {"major": 1, "minor": 0})
                if _field(hello, "protocol_major", int) != 1:
                    raise StatusProtocolError("RF protocol major mismatch")
                minor = hello.get("protocol_minor")
                # Optional for legacy/default paths, but only a valid hello on
                # this retained connection can prove timing support before TX.
                self._protocol_minor_seen = minor if type(minor) is int and minor >= 0 else None
            yield
        except Exception:
            self._drop_session()
            raise
        finally:
            # A session is retained only while it is the evidence for something: a
            # loaded TX, a configured control session that produced an RX frame, or
            # a HEADROOM study still in progress (a disconnect seals it natively,
            # so the study is only reachable on the connection that began it).
            if not (self._tx_session_ready or self._rx_session_ready
                    or self._headroom_session_ready or self._research_owner is not None):
                self._drop_session()

    def _ordinary_owner(self):
        if self._research_owner is not None:
            raise StatusProtocolError('HEADROOM study owns the connection', 'BUSY')

    @contextmanager
    def headroom_lease(self):
        """Reserve one connection from live preflight until final close, with no retry."""
        with self._io_lock:
            if self._research_owner is not None:
                raise StatusProtocolError('HEADROOM study already owns I/O', 'BUSY')
            self._research_owner = threading.get_ident()
            try:
                deadline = time.monotonic() + self.timeout
                with self._connection(deadline):
                    self._research_generation = self._connection_generation
                    self._research_socket = self._session_socket
                    yield
            finally:
                self._drop_session()
                self._research_generation = None
                self._research_socket = None
                self._research_owner = None

    def _research_owned(self):
        if (self._research_owner != threading.get_ident() or
                self._research_generation != self._connection_generation or
                self._session_socket is None or
                self._session_socket is not self._research_socket):
            raise StatusProtocolError('research requires its original live I/O lease')

    def headroom_arm(self, token):
        self._research_owned()
        if type(token) is not str or not token:
            raise StatusProtocolError('explicit control token required')
        deadline = time.monotonic() + self.control_timeout
        info = self._request(deadline, 'device.info', {})
        if info.get('armed') is not False or 'control.arm' not in info.get('capabilities', {}).get('control_ops', []):
            raise StatusProtocolError('live arm gate refused research')
        ack = self._request(deadline, 'control.arm', {'token': token})
        if ack.get('armed') is not True:
            raise StatusProtocolError('research arm was not acknowledged')
        info = self._request(deadline, 'device.info', {})
        from rf_headroom import CONTROL_OPS
        ops = info.get('capabilities', {}).get('control_ops')
        if info.get('armed') is not True or type(ops) is not list or not all(op in ops for op in CONTROL_OPS):
            raise StatusProtocolError('armed daemon did not advertise research operations')
        self._remember(replace(self._snapshot, armed=True, control_ops=tuple(ops)))
        return ack

    def headroom_closing_readback(self):
        self._research_owned()
        deadline = time.monotonic() + self.timeout
        info = self._request(deadline, 'device.info', {})
        state = self._request(deadline, 'state.get', {})
        pl = self._request(deadline, 'pl.status.get', {})
        parsed = _pl_status(pl)
        self._research_owned()
        return dict(armed=info.get('armed'), state=state, pl=asdict(parsed),
                    confirmed=(info.get('armed') is False and state.get('state')=='OFFLINE'
                        and state.get('pending') is False and state.get('lease_held') is True
                        and state.get('pending_op') is None
                        and parsed.io_ok is True and parsed.tx.source_selected is False
                        and parsed.rx.armed is False))

    @staticmethod
    def _validate_payload(op, args, payload):
        if not isinstance(payload, bytes):
            raise StatusProtocolError("RF payload must be immutable bytes")
        if op not in ("tx.load", "headroom.load"):
            if payload:
                raise StatusProtocolError("binary payload is only valid for tx.load")
            return
        samples = args.get("samples")
        count = args.get("bytes")
        if (type(samples) is not int or not 1 <= samples <= 4096 or
                type(count) is not int or count != samples * 4 or len(payload) != count or
                args.get("format") != "s16_iq_le" or args.get("direction") != "tx" or
                type(args.get("wrap")) is not bool):
            raise StatusProtocolError("invalid tx.load metadata or payload length")
        if any(not -2048 <= value <= 2047 for (value,) in struct.iter_unpack("<h", payload)):
            raise StatusProtocolError("tx.load sample is outside signed 12-bit range")

    def _control(self, op, args, payload=b""):
        self._ordinary_owner()
        with self._io_lock:
            self._ordinary_owner()
            return self._control_unlocked(op, args, payload)

    def _control_unlocked(self, op, args, payload=b""):
        try:
            self._validate_payload(op, args, payload)
            if op not in self._advertised():
                raise StatusProtocolError(
                    "the RF daemon did not advertise " + op + ", so it was not sent")
            if op == "tx.start" and not self._tx_session_ready:
                raise StatusProtocolError("tx.start requires a loaded live connection")
            timeout = self.control_timeout if op in ("rf.init", "rf.configure") else self.timeout
            deadline = time.monotonic() + timeout
            with self._connection(deadline):
                result = self._request(deadline, op, args, payload)
                if op == "tx.load":
                    self._tx_session_ready = True
                    self._session_saw_active = False
                elif op == "tx.start":
                    self._session_saw_active = True
                elif op in ("tx.stop", "control.disarm"):
                    self._drop_session()
                if op in ("rf.init", "rf.configure"):
                    # A local configuration revision: the wire has no config-version
                    # field, and a frame is only current for the configuration it was
                    # taken under, so the change is counted here.
                    self._config_revision += 1
                return result
        except (OSError, TimeoutError, StatusProtocolError, ValueError) as exc:
            self._drop_session()
            code = getattr(exc, "code", None)
            raise StatusProtocolError(str(exc) or type(exc).__name__,
                                      code if isinstance(code, str) else None) from exc

    def control_arm(self, token):
        """Arm control for this process.  See `_control` for the send rule."""
        return self._control("control.arm", {"token": token})

    def control_disarm(self):
        """Return the daemon to refusing control operations."""
        return self._control("control.disarm", {})

    def set_idelay_lanes(self, mask, tap):
        """Tap-only PL IDELAY lane update; no AD9361 reconfigure or capture."""
        if type(mask) is not int or not 1 <= mask <= 0x7F or type(tap) is not int or not 0 <= tap <= 31:
            raise ValueError("mask must be 1..127 and tap 0..31")
        return self._control("idelay.configure", {"mask": mask, "tap": tap})

    def get_interface_status(self):
        """Read the five fixed AD9361 interface registers.

        Read-only: it neither arms, initializes nor writes anything.  If the
        daemon has no core (for example, unarmed), the daemon refuses it with
        INVALID_STATE instead of acquiring or initializing hardware.
        """
        self._ordinary_owner()
        with self._io_lock:
            deadline = time.monotonic() + self.timeout
            with self._connection(deadline):
                return self._request(deadline, "rf.interface_status", {})

    def set_rx_data_delay(self, value):
        """Set only AD9361 0x006 RX_DATA_DELAY[3:0].

        The daemon re-reads 0x006/0x007/0x010/0x011/0x012, preserves all fields
        except the low nibble of 0x006, and rejects 0..15 violations without
        clamp or wrap.  Requires READY, TX source off and RX unarmed.
        """
        if type(value) is not int or not 0 <= value <= 15:
            raise ValueError("RX data delay must be an integer in 0..15")
        return self._control("rf.rx_data_delay.set", {"value": value})

    def control_op(self, op, args=None, payload=b""):
        """Send one advertised control operation with explicit arguments.

        The general primitive the panel needs for the operations that DO take
        parameters.  The gate is the same object the arm/disarm calls use: the op must
        be in the most recent snapshot's `control_ops`.  The op name is NOT checked
        against a second, locally maintained list -- that duplication is exactly what
        let this module and the panel drift apart.
        """
        return self._control(op, dict(args or {}), payload)

    def control_armed(self):
        """The daemon's own armed state, read live.  Read-only; never arms.

        This exists because `control.disarm` answering ok is not evidence that
        the part is unarmed: `op_control_disarm` ignores the return of the
        disarm it performs, and after a terminal research close the daemon has
        already disarmed itself and no longer advertises `control.disarm` at
        all.  A study's closing evidence therefore has to be the daemon's
        reported state, not the fact that an RPC was answered.
        """
        with self._io_lock:
            deadline = time.monotonic() + self.timeout
            try:
                with self._connection(deadline):
                    info = self._request(deadline, "device.info", {})
            except (OSError, TimeoutError, ValueError) as exc:
                raise StatusProtocolError(str(exc) or type(exc).__name__) from exc
        armed = info.get("armed")
        if type(armed) is not bool:
            raise StatusProtocolError("RF daemon reported a non-boolean armed state")
        return armed

    # ------------------------------------------------------- HEADROOM research
    # The research contract is a separate, separately reviewed transaction.  It
    # is deliberately NOT expressed through control_op: a study must run on one
    # retained connection in a fixed order, and a client that could send its
    # operations one at a time from anywhere would not be able to promise that.
    def headroom_capability(self):
        """The live research capability, read from device.info -- never a manifest.

        Returns what the daemon actually announced, with `available` meaning only
        "a headroom object was present".  Whether that object names a contract
        this client may run is `rf_headroom.capability_available`'s decision.
        """
        with self._io_lock:
            if self._research_owner is not None:
                self._research_owned()
            deadline = time.monotonic() + self.timeout
            try:
                with self._connection(deadline):
                    info = self._request(deadline, "device.info", {})
                    state = self._request(deadline, 'state.get', {})
                    pl = _pl_status(self._request(deadline, 'pl.status.get', {}))
                    hello_minor = self._protocol_minor_seen
                    if self._research_owner is not None:
                        self._research_owned()
            except (OSError, TimeoutError, ValueError) as exc:
                raise StatusProtocolError(str(exc) or type(exc).__name__) from exc
        capabilities = info.get("capabilities")
        headroom = (capabilities.get("headroom")
                    if isinstance(capabilities, dict) else None)
        if not isinstance(headroom, dict):
            return dict(study=None, protocol_minor=None, daemon_version=None,
                        operations=[], available=False)
        operations = headroom.get("operations")
        if not isinstance(operations, list) or any(not isinstance(op, str)
                                                   for op in operations):
            operations = []
        return dict(headroom, daemon_version=info.get('daemon'),
                    hello_major=1, hello_minor=hello_minor,
                    announced_major=info.get('protocol_major'), announced_minor=info.get('protocol_minor'),
                    armed=info.get('armed'), state=state.get('state'),
                    pending=state.get('pending'), pending_op=state.get('pending_op'),
                    lease_held=state.get('lease_held'), io_ok=pl.io_ok,
                    tx_active=pl.tx.source_selected, rx_busy=pl.rx.armed, available=True)

    def headroom_begin(self, args):
        return self._research("headroom.begin", args)

    def headroom_load(self, args, payload):
        return self._research("headroom.load", args, payload)

    def headroom_capture(self, args):
        return self._research("headroom.capture", args, expect_frame=True)

    def headroom_accept(self, args):
        return self._research("headroom.accept", args)

    def headroom_abort(self, args):
        return self._research("headroom.abort", args)

    def _research(self, op, args, payload=b"", expect_frame=False):
        """Send one research request on the study's retained connection.

        The same arm/disarm gate as every other control operation applies: the op
        must be in the most recent snapshot's `control_ops`.  A refusal or a
        transport failure drops the connection, which is also what seals the
        study natively, so a failed research request is never retried here.
        """
        with self._io_lock:
            self._research_owned()
            try:
                args = dict(args or {})
                self._validate_payload(op, args, payload)
                if op not in self._advertised():
                    raise StatusProtocolError(
                        "the RF daemon did not advertise " + op + ", so it was not sent")
                deadline = time.monotonic() + self.control_timeout
                with self._connection(deadline):
                    self._headroom_session_ready = True
                    result = (self._research_frame(deadline, op, args)
                              if expect_frame
                              else self._request(deadline, op, args, payload))
                    return result
            except (OSError, TimeoutError, StatusProtocolError, ValueError) as exc:
                self._headroom_session_ready = False
                self._drop_session()
                code = getattr(exc, "code", None)
                raise StatusProtocolError(str(exc) or type(exc).__name__,
                                          code if isinstance(code, str) else None) from exc

    def _research_frame(self, deadline, op, args):
        """One research capture: the header first, then its bytes read exactly once.

        Only the frame's own framing is checked here -- that it is a research
        frame and how long it says it is.  Which transaction it belongs to is
        `rf_headroom.check_research_frame`'s job, from the same bytes.
        """
        frame_class = self._research_frame_class()
        self._session_id += 1
        request_id = self._session_id
        sock = self._session_socket
        response, buffer = self._send_and_read_json(
            sock, self._session_buffer, deadline, request_id, op, args,
            validate_id=False)
        if 'bytes' not in response:
            self._session_buffer = buffer
            return frame_class(request_id, response, b'')
        samples = response.get("samples")
        declared = response.get("bytes")
        if (type(samples) is not int or type(declared) is not int
                or not 1 <= samples <= RX_MAX_SAMPLES
                or declared != samples * RX_BYTES_PER_SAMPLE):
            raise StatusProtocolError("invalid research IQ frame length")
        while len(buffer) < declared:
            try:
                sock.settimeout(self._remaining(deadline))
                chunk = sock.recv(min(65536, declared-len(buffer)))
                buffer += chunk
                self._remaining(deadline)
                if not chunk:
                    raise StatusProtocolError('truncated research IQ payload')
            except (OSError, TimeoutError, StatusProtocolError) as exc:
                partial = buffer[:declared]
                self._session_buffer = b''
                return frame_class(request_id, response, partial,
                    {'complete': False, 'received_bytes': len(partial), 'error': str(exc)})
        payload, buffer = buffer[:declared], buffer[declared:]
        self._session_buffer = buffer
        return frame_class(request_id, response, payload)

    def use_research_module(self, module):
        """Use this module's `ResearchFrame` for research frames.

        The worker loads `rf_headroom` from the attested release BY PATH, while
        this client would otherwise `import rf_headroom` by name.  Those are two
        module objects even when they are the same file, so the class the client
        built would fail `check_research_frame`'s `isinstance` against the class
        the controller knows -- a study that refused itself for a reason that has
        nothing to do with the contract.  Handing the client the module the
        worker attested makes it one object and one class.

        `None` is accepted and restores the name-based fallback, which is what a
        standalone client (no release directory) needs.
        """
        self._research_module = module

    def _research_frame_class(self):
        module = self._research_module
        if module is None:
            import rf_headroom
            module = rf_headroom
        return module.ResearchFrame

    # -------------------------------------------------------------- RX capture
    @staticmethod
    def _capture_args(args, physical):
        """Validate the caller's request and return the exact wire arguments.

        A REJECT, never a coercion: an out-of-range ratio, a missing sample count, a
        field this op does not take, or a physical capture without the explicit cable
        and single-source domain attenuation (46000 mdB) attestation is refused
        locally and nothing is sent.

        `ratio` is REQUIRED.  The daemon keeps its initialized ratio when the argument
        is absent (rf_daemon.c:1210 ratio = 1u, so an omitted ratio means decimation 2),
        so defaulting it here would silently halve the sample rate instead of failing.
        """
        if not isinstance(args, dict):
            raise StatusProtocolError(
                "capture arguments must be a mapping carrying samples and ratio")
        allowed = {"samples", "ratio"}
        if physical:
            allowed |= {"cable_confirmed", "attenuation_mdB", "observe_timing",
                        "settle_wait_us"}
        unexpected = sorted(set(args) - allowed)
        if unexpected:
            raise StatusProtocolError("unexpected capture argument: " + unexpected[0])
        samples = args.get("samples")
        if type(samples) is not int or not 1 <= samples <= RX_MAX_SAMPLES:
            raise StatusProtocolError(
                "capture samples must be an integer 1..%d" % RX_MAX_SAMPLES)
        ratio = args.get("ratio")
        if type(ratio) is not int or not 0 <= ratio <= RX_MAX_RATIO:
            raise StatusProtocolError(
                "capture ratio must be an explicit selection code 0..%d "
                "(decimation 1/2/4/8/16)" % RX_MAX_RATIO)
        wire = {"samples": samples, "ratio": ratio}
        if physical:
            if args.get("cable_confirmed") is not True:
                raise StatusProtocolError(
                    "a physical loopback capture requires explicit cable confirmation")
            attenuation = args.get("attenuation_mdB")
            # The accepted value is the teaching profile's single source, never a
            # private literal: 45999, 46100 and 40000 are refused exactly like a
            # missing or non-integer value.
            if type(attenuation) is not int or attenuation != ATTENUATION_MDB:
                raise StatusProtocolError(
                    "a physical loopback capture requires attenuation_mdB %d"
                    % ATTENUATION_MDB)
            wire["cable_confirmed"] = True
            wire["attenuation_mdB"] = attenuation
            if "observe_timing" in args:
                if type(args["observe_timing"]) is not bool:
                    raise StatusProtocolError("capture observe_timing must be a boolean")
                wire["observe_timing"] = args["observe_timing"]
            if "settle_wait_us" in args:
                wait = args["settle_wait_us"]
                if type(wait) is not int or not 0 <= wait <= SETTLE_WAIT_MAX_US:
                    raise StatusProtocolError(
                        "capture settle_wait_us must be an integer 0..%d"
                        % SETTLE_WAIT_MAX_US)
                if wait and args.get("observe_timing") is not True:
                    raise StatusProtocolError(
                        "capture settle_wait_us requires observe_timing: an "
                        "unmeasured wait is not evidence")
                wire["settle_wait_us"] = wait
        return wire

    def capture(self, args, physical=False):
        """Run one finite RX capture and return the validated immutable frame.

        The ordinary path sends `rx.capture` and nothing else.  The physical path
        needs `physical=True`, an explicit cable attestation at the single-source
        46000 mdB, and a TX buffer already loaded on this session: a plain capture
        never implies a loopback, and nothing here arms or transmits.
        """
        physical = bool(physical)
        wire = self._capture_args(args, physical)
        op = RX_PHYSICAL_OP if physical else RX_NORMAL_OP
        self._ordinary_owner()
        with self._io_lock:
            self._ordinary_owner()
            return self._capture_unlocked(op, wire, physical)

    def _capture_unlocked(self, op, wire, physical):
        # The local gates run OUTSIDE the try: a request that was never sent cannot
        # have invalidated the retained session, so it must not discard it either.
        if op not in self._advertised():
            raise StatusProtocolError(
                "the RF daemon did not advertise " + op + ", so it was not sent")
        if physical and not self._tx_session_ready:
            raise StatusProtocolError(
                "a physical loopback capture requires a loaded TX on a live connection")
        if wire.get("observe_timing") is True and (
                self._session_socket is None or self._protocol_minor_seen is None or
                self._protocol_minor_seen < 4):
            raise StatusProtocolError(
                "capture timing requires protocol minor >= 4 confirmed by the live hello",
                "UNSUPPORTED")
        if wire.get("settle_wait_us", 0) > 0 and (
                self._session_socket is None or self._protocol_minor_seen is None or
                self._protocol_minor_seen < 5):
            raise StatusProtocolError(
                "capture settling wait requires protocol minor >= 5 confirmed by "
                "the live hello", "UNSUPPORTED")
        try:
            deadline = time.monotonic() + self.capture_timeout
            with self._connection(deadline):
                generation = self._connection_generation
                release = self._device_identity(deadline)
                pre = self._pl_readback(deadline)
                request_id, header, payload = self._capture_exchange(deadline, op, wire)
                post = self._pl_readback(deadline)
                post_release = self._device_identity(deadline)
                self._confirm_capture(pre, post, release, post_release, physical)
                # The capture_id is generated locally, after every check passed, so a
                # rejected frame can never have been named and published.
                self._capture_sequence += 1
                token = release[:8] if isinstance(release, str) and release else "unknown"
                frame = RxIqFrame(
                    capture_id="rx-%s-g%d-c%d-%d" % (token, generation,
                                                     self._config_revision,
                                                     self._capture_sequence),
                    payload=payload, samples=header["samples"],
                    sample_rate_hz=header["sample_rate_hz"], ratio=header["ratio"],
                    daemon_timestamp_ns=header["timestamp_ns"],
                    connection_generation=generation,
                    config_revision=self._config_revision, request_id=request_id,
                    release_id=release, op=op, physical=physical,
                    pre_readback=pre, post_readback=post,
                    completed_at_monotonic=time.monotonic())
                self._rx_session_ready = True
                # What was ACTUALLY sent for this frame, remembered so the trace
                # is checked against the request rather than against whatever a
                # caller claims later.  A caller-supplied value would let the two
                # disagree while the check still passed.
                self._capture_settle_wait_us = int(wire.get("settle_wait_us", 0))
                self._publish_frame(frame)
                return frame
        except (OSError, TimeoutError, StatusProtocolError, ValueError) as exc:
            self._drop_session()
            code = getattr(exc, "code", None)
            raise StatusProtocolError(str(exc) or type(exc).__name__,
                                      code if isinstance(code, str) else None) from exc

    def latest_frame(self):
        """The most recent fully validated frame, or None if none is current."""
        with self._lock:
            return self._frame

    def _publish_frame(self, frame):
        with self._lock:
            self._frame = frame

    def _device_identity(self, deadline):
        info = self._request(deadline, "device.info", {})
        release = info.get("release_id")
        if release is not None and not isinstance(release, str):
            raise StatusProtocolError("invalid release id in device.info")
        return release

    def _pl_readback(self, deadline):
        pl = _pl_status(self._request(deadline, "pl.status.get", {}))
        return RxReadback(io_ok=pl.io_ok, rx_armed=pl.rx.armed, rx_done=pl.rx.done,
                          rx_position_words=pl.rx.position_words,
                          tx_source_selected=pl.tx.source_selected,
                          sampled_at_monotonic_ns=pl.sampled_at_monotonic_ns)

    def _capture_exchange(self, deadline, op, args):
        """Send one capture request; read either an IQ frame or the daemon's refusal."""
        self._session_id += 1
        request_id = self._session_id
        sock = self._session_socket
        response, buffer = self._send_and_read_json(
            sock, self._session_buffer, deadline, request_id, op, args)
        if "ok" in response or "result" in response:
            self._session_buffer = buffer
            if response.get("ok") is not True:
                error = response.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                raise StatusProtocolError("RF daemon refused " + op,
                                          code if isinstance(code, str) else None)
            raise StatusProtocolError(
                "RF daemon answered " + op + " without an IQ frame")
        if response.get("type") != "iq_frame":
            raise StatusProtocolError(
                "RF daemon answered " + op + " with a non-IQ frame")
        meta = self._iq_header(response)
        for name in ("samples", "ratio"):
            if meta[name] != args[name]:
                raise StatusProtocolError(
                    "IQ frame field does not match capture request: " + name)
        payload, buffer = self._read_payload(sock, buffer, deadline, meta["bytes"])
        self._session_buffer = buffer
        for (value,) in struct.iter_unpack("<h", payload):
            if not RX_IQ_MIN <= value <= RX_IQ_MAX:
                raise StatusProtocolError(
                    "IQ sample is outside the signed 12-bit range")
        return request_id, meta, payload

    @staticmethod
    def _iq_header(response):
        """Validate the frame metadata; the payload is read only after this passes."""
        def integer(name, low, high):
            value = response.get(name)
            if type(value) is not int or not low <= value <= high:
                raise StatusProtocolError("invalid IQ frame field: " + name)
            return value

        if response.get("direction") != RX_DIRECTION:
            raise StatusProtocolError("invalid IQ frame field: direction")
        if response.get("format") != RX_IQ_FORMAT:
            raise StatusProtocolError("invalid IQ frame field: format")
        samples = integer("samples", 1, RX_MAX_SAMPLES)
        declared = integer("bytes", 0, RX_MAX_SAMPLES * RX_BYTES_PER_SAMPLE)
        if declared != samples * RX_BYTES_PER_SAMPLE:
            raise StatusProtocolError("invalid IQ frame field: bytes")
        return {"samples": samples, "bytes": declared,
                "sample_rate_hz": integer("sample_rate_hz", 1, 0xFFFFFFFF),
                "ratio": integer("ratio", 0, RX_MAX_RATIO),
                "timestamp_ns": integer("timestamp_ns", 0, 0xFFFFFFFFFFFFFFFF)}

    @staticmethod
    def _confirm_capture(pre, post, release, post_release, physical):
        """A capture is only real if the readback around it confirms it ended."""
        if not pre.io_ok or not post.io_ok:
            raise StatusProtocolError(
                "the PL status readback around the capture was not available")
        if release != post_release:
            raise StatusProtocolError(
                "the daemon release changed during the capture")
        if post.rx_armed is not False:
            raise StatusProtocolError(
                "the daemon did not confirm the capture released RX (CAP_ARM is not 0)")
        if physical and post.tx_source_selected is not False:
            raise StatusProtocolError(
                "the physical capture did not confirm the TX source is off (SRC_SEL)")

    def observe_rx_gain(self) -> dict:
        """Explicit read-only RX1 raw observation; sequential reads are not atomic.

        This does not arm, configure, capture, renew the control watchdog, or alter
        the published status/frame. A complete but invalid optional observation
        raises without discarding the existing control session. Transport/framing
        failures still close the unsafe connection through `_connection`.
        """
        with self._io_lock:
            deadline = time.monotonic() + self.timeout
            try:
                with self._connection(deadline):
                    info = self._request(deadline, "device.info", {"observe_rx_gain": True})
            except (OSError, TimeoutError, ValueError) as exc:
                raise StatusProtocolError(str(exc) or type(exc).__name__) from exc
            # The JSON reply is fully consumed before schema validation. Rejecting
            # an optional measurement is not evidence that the retained IQ or
            # configured connection became invalid, and must not clear either.
            return _gain_observation(info)

    def observe_capture_timing(self, frame) -> dict:
        """Read optional call brackets for this client's current physical IQ frame.

        This uses the retained connection without control, capture, or retries.
        Brackets describe monotonic API calls, not hardware sample edges or gain.
        Complete optional-schema errors preserve the frame; framing/transport
        errors follow the existing connection drop semantics.
        """
        with self._io_lock:
            if (not isinstance(frame, RxIqFrame) or frame is not self.latest_frame() or
                    frame.physical is not True or frame.op != RX_PHYSICAL_OP or
                    self._session_socket is None or not self._rx_session_ready or
                    frame.connection_generation != self._connection_generation or
                    frame.config_revision != self._config_revision):
                raise StatusProtocolError("capture timing requires the current physical IQ frame")
            deadline = time.monotonic() + self.timeout
            try:
                with self._connection(deadline):
                    info = self._request(deadline, "device.info", {"observe_timing": True})
            except (OSError, TimeoutError, ValueError) as exc:
                raise StatusProtocolError(str(exc) or type(exc).__name__) from exc
            # Validate only after the complete JSON has been consumed: bad optional
            # diagnostics cannot invalidate the already checked IQ/configuration.
            return _capture_timing_observation(info, frame, self._capture_settle_wait_us)

    def poll(self) -> RfStatusSnapshot:
        # The daemon handles native initialization synchronously. Waiting here avoids
        # timing out a read against an occupied daemon; the old snapshot still expires.
        if self._research_owner is not None:
            with self._lock:
                return self._snapshot.expired()
        with self._io_lock:
            if self._research_owner is not None:
                with self._lock:
                    return self._snapshot.expired()
            return self._poll_unlocked()

    def _poll_unlocked(self) -> RfStatusSnapshot:
        deadline = time.monotonic() + self.timeout
        try:
            with self._connection(deadline):
                info = self._request(deadline, "device.info", {})
                state = self._request(deadline, "state.get", {})
                subscribed = self._request(deadline, "status.subscribe", {})
                pl = self._request(deadline, "pl.status.get", {})
                capabilities = info.get("capabilities")
                ops = capabilities.get("ops") if isinstance(capabilities, dict) else None
                # `core` is deliberately NOT part of this gate.  A production daemon
                # reports mode="status_only" for its whole life; arming changes `core`
                # and `control_ops`, NOT `mode`.  Requiring `core is False` made the
                # panel go dark the moment an operator armed: the next poll raised, the
                # error snapshot replaced the good one and took `control_ops` with it
                # (so every later control call was refused locally, and the operator
                # could not even disarm from the panel).  What keeps the READ path safe
                # is the read-only op set below, not `core`.
                if info.get("mode") != "status_only" or ops != list(READ_ONLY_OPS):
                    raise StatusProtocolError("RF daemon is not status-only")
                core = info.get("core")
                if not isinstance(core, bool):
                    raise StatusProtocolError("RF daemon reported a non-boolean core state")
                # Phase 2 additions.  Absent fields mean a phase-1 daemon: unarmed, no
                # control operations -- which is exactly how such a daemon behaves, so
                # absence is not an error.  Anything present must be well formed, and a
                # control operation may never also be advertised as a read-only one.
                armed = info.get("armed")
                if armed is not None and not isinstance(armed, bool):
                    raise StatusProtocolError("RF daemon reported a non-boolean armed state")
                control_ops = capabilities.get("control_ops") if isinstance(capabilities, dict) else None
                if control_ops is None:
                    control_ops = []
                if not isinstance(control_ops, list) or any(not isinstance(name, str) for name in control_ops):
                    raise StatusProtocolError("RF daemon reported an invalid control operation list")
                if set(control_ops) & set(READ_ONLY_OPS):
                    raise StatusProtocolError(
                        "RF daemon advertises a read-only operation as a control operation")
                # The research capability as announced on THIS reply.  A daemon
                # without the contract simply has no such field, which stays None.
                headroom = capabilities.get("headroom") if isinstance(capabilities, dict) else None
                if headroom is not None and not isinstance(headroom, dict):
                    raise StatusProtocolError(
                        "RF daemon reported an invalid research capability")
                daemon = info.get("daemon")
                if daemon is not None and not isinstance(daemon, str):
                    raise StatusProtocolError(
                        "RF daemon reported a non-string daemon version")
                if subscribed.get("subscribed") is not True:
                    raise StatusProtocolError("RF status subscription refused")
                parsed_pl = _pl_status(pl)
                if isinstance(headroom, dict):
                    headroom = dict(headroom, daemon_version=daemon,
                        hello_major=1, hello_minor=self._protocol_minor_seen,
                        announced_major=info.get('protocol_major'), announced_minor=info.get('protocol_minor'),
                        armed=armed, state=state.get('state'), pending=state.get('pending'),
                        pending_op=state.get('pending_op'), lease_held=state.get('lease_held'),
                        io_ok=parsed_pl.io_ok, tx_active=parsed_pl.tx.source_selected,
                        rx_busy=parsed_pl.rx.armed, available=True)
                snapshot = RfStatusSnapshot(
                    online=True, error=None if parsed_pl.io_ok else "PL registers unavailable",
                    received_at_monotonic=time.monotonic(), mode=info.get("mode"), core=core,
                    capabilities=READ_ONLY_OPS, daemon_state=_field(state, "state", str),
                    pending=_field(state, "pending", bool),
                    pending_op=_field(state, "pending_op", str, True),
                    lease_held=_field(state, "lease_held", bool),
                    release_id=_field(info, "release_id", str, True),
                    bit_sha256=_field(info, "bit_sha256", str, True),
                    hwh_sha256=_field(info, "hwh_sha256", str, True),
                    pl_status=parsed_pl,
                    armed=bool(armed),
                    control_ops=tuple(control_ops),
                    headroom=copy.deepcopy(headroom),
                    daemon_version=daemon,
                    tx_session_ready=self._tx_session_ready,
                    rx_session_ready=self._rx_session_ready)
                if self._tx_session_ready:
                    active = snapshot.daemon_state == "TX_ACTIVE"
                    valid = (snapshot.armed and parsed_pl.io_ok and
                             snapshot.daemon_state in ("READY", "TX_ACTIVE") and
                             (not active or parsed_pl.tx.source_selected is True) and
                             not (self._session_saw_active and not active))
                    if not valid:
                        self._drop_session()
                        snapshot = replace(snapshot, tx_session_ready=False)
                    elif active:
                        self._session_saw_active = True
                if self._rx_session_ready:
                    # The retained session is the operator's configured control
                    # session, so it is valid exactly while the daemon still reports
                    # it armed and holding a started part.  A session that went
                    # OFFLINE/FAULT (or was disarmed) is a DIFFERENT session, and the
                    # frozen frame taken on the old one stops being current with it.
                    # This never re-arms and never retries anything.
                    valid = (snapshot.armed and parsed_pl.io_ok and
                             snapshot.daemon_state in ("READY", "RX_CAPTURING",
                                                       "TX_ACTIVE"))
                    if not valid:
                        self._drop_session()
                        snapshot = replace(snapshot, rx_session_ready=False)
                # The snapshot was built with BOTH flags copied BEFORE the branches ran,
                # and either branch can drop the session -- which clears both local flags
                # and the frame.  A snapshot built before that drop can still claim a
                # retained RX session (this task's explicit retention condition) while the
                # socket is already None.  Re-read both flags on the one path that
                # publishes, so the field always describes the session actually retained.
                snapshot = replace(snapshot, tx_session_ready=self._tx_session_ready,
                                   rx_session_ready=self._rx_session_ready)
                self._remember(snapshot)
                return snapshot
        except (OSError, TimeoutError, StatusProtocolError, ValueError) as exc:
            snapshot = RfStatusSnapshot(error=str(exc) or type(exc).__name__)
            # Recorded, not just returned: the arming rule reads the most recent
            # snapshot, and a failed poll tells the client nothing about what it may
            # send -- so it must replace the previous answer rather than leave a stale
            # capability in place.
            self._remember(snapshot)
            return snapshot

