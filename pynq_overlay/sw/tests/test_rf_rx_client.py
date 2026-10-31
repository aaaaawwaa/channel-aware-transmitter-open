"""Regressions for the bounded binary RX capture path (plan Task 2).

Written RED FIRST: none of this exists in the JSON-only client, so before the
implementation this file fails (RfStatusClient has no "capture", rf_rx_types does
not exist).

The fake daemon below reproduces the real framing, read from the daemon source
rather than from prose:

  * rf_protocol.c:rf_proto_write_iq_frame() builds the ONE header line
    {"id","type":"iq_frame","direction","format","samples","bytes",
     "sample_rate_hz","ratio","timestamp_ns"} -- there is no config-version and
    no session field on the current wire.
  * rf_daemon.c:op_rx_capture() / op_loopback_physical_capture() append "\n",
    then samples*4 payload bytes (i16 I, i16 Q, little endian; rf_iq.c), and send
    header + payload in ONE write.
  * rf_daemon.c refuses with {"id","ok":false,"error":{"code","message"}} and sends
    NO payload.
"""
import json
import socket
import struct
import sys
import threading
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rf_status_client import READ_ONLY_OPS, RfStatusClient, StatusProtocolError

SAMPLES = 4096
FS = 60_000_000
RELEASE = "56d0d0d4bd36d4e22d1c030926917a16"
CONTROLS = ["control.arm", "control.disarm", "rf.init", "rf.configure",
            "tx.load", "tx.start", "tx.stop", "rx.capture",
            "loopback.physical.capture"]
LOAD_META = {"samples": 64, "bytes": 256, "format": "s16_iq_le",
             "direction": "tx", "wrap": True}
LOAD_PAYLOAD = b"\0" * 256

# A header override whose value is DROP removes the field from the frame header
# entirely (a wire shape, not just a wrong value): the daemon may omit an optional
# field, and that is a different reply from one carrying a wrong value.
DROP = object()


class RxDaemon:
    """Real TCP framing, one synchronous connection at a time (like the daemon).

    Every field is scriptable so a criterion can corrupt exactly one thing:
    the header overrides, the payload, whether the response is chunked or glued
    to the next reply, whether the capture stalls, and the readback that follows.
    """

    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.seen = []
        self.frames = []
        self.connections = 0
        self.closed = 0
        self.armed = True
        self.state = "READY"
        self.lease_held = True
        self.rx_armed = False
        self.rx_done = False
        self.tx_source = False
        self.loaded = False
        self.release_id = RELEASE
        self.rate = FS
        self.control_ops = list(CONTROLS)
        # --- scripting knobs -------------------------------------------------
        self.capture_error = None        # {"code","message"} -> JSON refusal
        self.stall_capture = False       # accept the request, never answer
        self.disconnect_next = False     # drop the connection instead of replying
        self.post_arm_stuck = False      # capture leaves CAP_ARM high
        self.source_stuck = False        # physical capture leaves SRC_SEL high
        self.release_after_capture = None
        self.header_overrides = {}
        self.payload_override = None
        self.truncate_payload = None     # send this many payload bytes, then close
        self.chunk = 0                   # send every reply in N-byte pieces
        self.glue_next = False           # glue the next reply onto the frame
        self.capture_request = None
        self.capture_op = None
        self._pre_answered = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    # ------------------------------------------------------------------ wire
    def _send(self, conn, data):
        if self.chunk:
            for start in range(0, len(data), self.chunk):
                conn.sendall(data[start:start + self.chunk])
        else:
            conn.sendall(data)

    def _read_line(self, conn, buf):
        while b"\n" not in buf:
            try:
                part = conn.recv(8192)
            except socket.timeout:
                if self.stop.is_set():
                    return None, buf
                continue
            if not part:
                return None, buf
            buf += part
        line, buf = buf.split(b"\n", 1)
        return line, buf

    def _read_exact(self, conn, buf, count):
        while len(buf) < count:
            try:
                part = conn.recv(8192)
            except socket.timeout:
                if self.stop.is_set():
                    raise EOFError
                continue
            if not part:
                raise EOFError
            buf += part
        return buf[:count], buf[count:]

    def _pl_result(self):
        armed = self.rx_armed or (self.post_arm_stuck and self.rx_done)
        source = self.tx_source or (self.source_stuck and self.rx_done)
        return {"sampled_at_monotonic_ns": 1000, "io_ok": True,
                "tx": {"source_selected": source,
                       "length_words": 64 if self.loaded else 0,
                       "position_words": 0},
                "rx": {"armed": armed, "done": self.rx_done, "position_words": 0}}

    def _info_result(self):
        return {"daemon": "1.0", "protocol_major": 1, "protocol_minor": 2,
                "max_samples": 4096, "mode": "status_only", "core": True,
                "armed": self.armed, "release_id": self.release_id,
                "bit_sha256": "a" * 64, "hwh_sha256": "b" * 64,
                "capabilities": {"ops": list(READ_ONLY_OPS),
                                 "control_ops": list(self.control_ops)}}

    def _state_result(self):
        return {"state": self.state, "pending": False, "pending_op": None,
                "lease_held": self.lease_held, "max_samples": 4096}

    def _frame_header(self, req):
        samples = self.header_overrides.get("samples", req["args"].get("samples"))
        ratio = self.header_overrides.get("ratio", req["args"].get("ratio"))
        header = {"id": self.header_overrides.get("id", req["id"]),
                  "type": self.header_overrides.get("type", "iq_frame"),
                  "direction": self.header_overrides.get("direction", "rx"),
                  "format": self.header_overrides.get("format", "s16_iq_le"),
                  "samples": samples,
                  "bytes": self.header_overrides.get("bytes", samples * 4),
                  "sample_rate_hz": self.header_overrides.get("sample_rate_hz", self.rate),
                  "ratio": ratio,
                  "timestamp_ns": self.header_overrides.get("timestamp_ns", 123456789)}
        for name, value in list(header.items()):
            if value is DROP:
                del header[name]
        return (json.dumps(header, separators=(",", ":")) + "\n").encode()

    def _payload(self, samples):
        if self.payload_override is not None:
            return self.payload_override
        return b"".join(struct.pack("<hh", (n % 1000) - 500, 500 - (n % 1000))
                        for n in range(samples))

    def _glued_reply(self, rid):
        """One reply that is valid for EITHER request the client sends next."""
        result = dict(self._pl_result())
        result.update(self._info_result())
        return (json.dumps({"id": rid, "ok": True, "result": result}) + "\n").encode()

    # ----------------------------------------------------------------- serve
    def _serve(self, conn, number):
        buf = b""
        while not self.stop.is_set():
            line, buf = self._read_line(conn, buf)
            if line is None:
                return
            req = json.loads(line.decode("utf-8"))
            self.seen.append((number, req))
            op, rid = req["op"], req["id"]
            if self.disconnect_next:
                self.disconnect_next = False
                return
            if self._pre_answered is not None and rid == self._pre_answered:
                self._pre_answered = None
                continue
            if op == "tx.load":
                payload, buf = self._read_exact(conn, buf, req["args"]["bytes"])
                self.frames.append(payload)
                self.loaded = True
                out = {"id": rid, "ok": True, "result": {"accepted": True}}
            elif op in ("rx.capture", "loopback.physical.capture"):
                self.capture_op, self.capture_request = op, req
                if self.stall_capture:
                    continue
                if self.capture_error is not None:
                    self._send(conn, (json.dumps({"id": rid, "ok": False,
                                                  "error": self.capture_error}) + "\n").encode())
                    continue
                self.rx_armed, self.rx_done, self.state = False, True, "READY"
                if op == "loopback.physical.capture":
                    self.tx_source = False
                if self.release_after_capture is not None:
                    self.release_id = self.release_after_capture
                samples = self.header_overrides.get("samples", req["args"].get("samples"))
                header = self._frame_header(req)
                payload = self._payload(samples)
                if self.truncate_payload is not None:
                    self._send(conn, header + payload[:self.truncate_payload])
                    return
                if self.glue_next:
                    # ONE write: header + payload + the reply the next request would
                    # get, so the client is holding the leftover when the payload is
                    # complete (this is what makes the buffering rule observable).
                    self.glue_next = False
                    self._pre_answered = rid + 1
                    self._send(conn, header + payload + self._glued_reply(rid + 1))
                else:
                    self._send(conn, header + payload)
                continue
            elif op == "pl.status.get":
                out = {"id": rid, "ok": True, "result": self._pl_result()}
            elif op == "device.info":
                out = {"id": rid, "ok": True, "result": self._info_result()}
            elif op in ("state.get", "status.subscribe"):
                result = dict(self._state_result())
                if op == "status.subscribe":
                    result["subscribed"] = True
                out = {"id": rid, "ok": True, "result": result}
            elif op == "protocol.hello":
                out = {"id": rid, "ok": True, "result": {"daemon": "1.0",
                                                         "protocol_major": 1,
                                                         "protocol_minor": 2,
                                                         "max_samples": 4096}}
            elif op == "control.arm":
                self.armed = True
                out = {"id": rid, "ok": True, "result": {"armed": True}}
            elif op == "control.disarm":
                self.armed = False
                out = {"id": rid, "ok": True, "result": {"armed": False}}
            elif op == "tx.start":
                self.tx_source, self.state = True, "TX_ACTIVE"
                out = {"id": rid, "ok": True, "result": {"accepted": True}}
            elif op == "tx.stop":
                self.tx_source, self.state = False, "READY"
                out = {"id": rid, "ok": True, "result": {"accepted": True}}
            elif op in ("rf.init", "rf.configure"):
                out = {"id": rid, "ok": True, "result": {"accepted": True}}
            else:
                out = {"id": rid, "ok": False,
                       "error": {"code": "UNSUPPORTED", "message": op}}
            try:
                self._send(conn, (json.dumps(out) + "\n").encode())
            except OSError:
                return

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections += 1
            number = self.connections
            conn.settimeout(0.1)
            try:
                self._serve(conn, number)
            except (OSError, EOFError, ValueError):
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                self.closed += 1
        try:
            self.listener.close()
        except OSError:
            pass

    def close(self):
        self.stop.set()
        self.thread.join(1)

    def ops(self):
        return [req["op"] for _, req in self.seen]


@pytest.fixture
def daemon():
    d = RxDaemon()
    yield d
    d.close()


def armed_client(daemon, **kwargs):
    """A client whose most recent snapshot advertises the control operations."""
    client = RfStatusClient(port=daemon.port, timeout=1, **kwargs)
    assert client.poll().error is None
    return client


NORMAL = {"samples": SAMPLES, "ratio": 0}
# The loopback attestation carries the single-source teaching/environment domain.
PHYSICAL = {"samples": 256, "ratio": 0, "cable_confirmed": True,
            "attenuation_mdB": 46000}


# --------------------------------------------------------------- happy path
def test_full_binary_capture_returns_one_validated_immutable_frame(daemon):
    client = armed_client(daemon)
    frame = client.capture(NORMAL)

    assert frame.samples == SAMPLES and len(frame.payload) == SAMPLES * 4
    assert frame.sample_rate_hz == FS and frame.ratio == 0
    assert frame.decimation == 1 and frame.output_fs_hz == FS
    assert frame.op == "rx.capture" and frame.physical is False
    assert frame.release_id == RELEASE
    assert frame.connection_generation >= 1 and frame.config_revision == 0
    assert frame.daemon_timestamp_ns == 123456789
    assert frame.completed_at_monotonic <= time.monotonic()
    assert frame.pre_readback.io_ok and frame.post_readback.io_ok
    assert frame.post_readback.rx_armed is False
    assert frame.capture_id.startswith("rx-")
    assert struct.unpack("<hh", frame.payload[:4]) == (-500, 500)
    assert struct.unpack("<hh", frame.payload[-4:]) == (-405, 405)
    assert client.latest_frame() is frame

    # The normal receive path sends rx.capture and NOTHING else: no loopback op,
    # no arm, no transmit.
    assert daemon.ops().count("rx.capture") == 1
    assert not [op for op in daemon.ops() if op.startswith("loopback.")]
    assert "tx.start" not in daemon.ops()
    # ... and it sent exactly the daemon's own argument names, with ratio explicit
    # (the daemon defaults an ABSENT ratio to 1 = decim 2, rf_daemon.c:1210/1233).
    assert daemon.capture_request["args"] == {"samples": SAMPLES, "ratio": 0}

    with pytest.raises(FrozenInstanceError):
        frame.payload = b""
    telemetry = frame.to_dict()
    assert "payload" not in telemetry
    assert all(isinstance(v, (str, int, float, bool)) or v is None
               for v in telemetry.values())


def test_the_frame_type_is_the_immutable_validated_contract(daemon):
    # Imported here (not at module scope) so the RED run reports each criterion's
    # own failure instead of one collection error.
    from rf_rx_types import (RX_MAX_SAMPLES, RX_IQ_FORMAT, RxAnalysis, RxIqFrame,
                             RxModelResult, RxReadback)
    client = armed_client(daemon)
    frame = client.capture(NORMAL)
    assert isinstance(frame, RxIqFrame)
    assert isinstance(frame.pre_readback, RxReadback)
    assert RX_IQ_FORMAT == "s16_iq_le" and RX_MAX_SAMPLES == 4096

    analysis = RxAnalysis(capture_id=frame.capture_id, valid=True, samples=SAMPLES,
                          component_dbfs=(1.0, 2.0), complex_dbfs=4.0,
                          peak_hz=1e6, occupied_bandwidth_hz=2e6, clipping_count=0,
                          spectrum=(0.5, 0.25), preview=(1, -1),
                          completed_at_monotonic=time.monotonic())
    model = RxModelResult(capture_id=frame.capture_id, quality="OK", label="QPSK",
                          class_index=2, scores=(0.1, 0.2), model_version="v1",
                          source="cabled", experimental=True,
                          completed_at_monotonic=time.monotonic())
    for result in (analysis, model):
        telemetry = result.to_dict()
        assert all(isinstance(v, (str, int, float, bool)) or v is None
                   for v in telemetry.values()), telemetry
    assert "spectrum" not in analysis.to_dict()
    assert "scores" not in model.to_dict()
    with pytest.raises(FrozenInstanceError):
        analysis.valid = False


def test_chunked_header_and_payload_are_reassembled(daemon):
    daemon.chunk = 7
    client = armed_client(daemon)
    frame = client.capture(NORMAL)
    assert len(frame.payload) == SAMPLES * 4
    assert struct.unpack("<hh", frame.payload[:4]) == (-500, 500)


def test_a_glued_next_reply_is_buffered_and_used_by_the_next_read(daemon):
    # The daemon writes the frame AND the reply the client's next request would have
    # received in ONE write, and then does not answer that request again.  The frame
    # is deliberately small (~700 bytes, one loopback segment) so all three parts are
    # already in hand when the payload is complete: a client that discarded what it
    # had read would block on its next request, which is exactly the brief's
    # "leftover bytes stay buffered" rule.
    daemon.glue_next = True
    client = armed_client(daemon)
    frame = client.capture({"samples": 64, "ratio": 0})
    assert frame.samples == 64 and len(frame.payload) == 256
    assert frame.post_readback.rx_armed is False
    assert client._session_buffer == b""
    assert frame.release_id == RELEASE


def test_payload_is_consumed_so_the_next_json_poll_is_not_corrupted(daemon):
    client = armed_client(daemon)
    client.capture(NORMAL)
    snapshot = client.poll()
    assert snapshot.error is None, snapshot.error
    assert snapshot.online and snapshot.pl_status.tx.length_words == 0


def test_a_normal_capture_keeps_the_configured_control_session(daemon):
    client = armed_client(daemon)
    client.capture(NORMAL)
    assert client._session_socket is not None
    connections = daemon.connections
    snapshot = client.poll()
    assert snapshot.error is None, snapshot.error
    assert snapshot.rx_session_ready is True
    assert snapshot.tx_session_ready is False, (
        "an RX capture is not evidence that a TX buffer was loaded")
    assert daemon.connections == connections, "the control session was not retained"
    with pytest.raises(StatusProtocolError) as refused:
        client.control_op("tx.start", {})
    assert "loaded live connection" in str(refused.value)
    assert "tx.start" not in daemon.ops()


def test_a_poll_that_drops_the_session_never_publishes_a_stale_rx_ready(daemon):
    # Both proofs can be held at once (the physical path loads a TX and then captures).
    # When the very next poll finds the TX validity check failing, the drop discards the
    # session -- and the snapshot it publishes must agree: rx_session_ready describes a
    # RETAINED session, so it cannot stay True for a socket that no longer exists.
    client = armed_client(daemon)
    client.control_op("tx.load", LOAD_META, payload=LOAD_PAYLOAD)
    client.capture(NORMAL)
    assert client._tx_session_ready and client._rx_session_ready
    assert client._session_socket is not None

    daemon.armed = False                       # the TX validity check now fails
    snapshot = client.poll()

    assert snapshot.error is None, snapshot.error
    assert snapshot.rx_session_ready is False, (
        "the poll published an RX session it had already dropped")
    assert client._snapshot.rx_session_ready is False
    assert snapshot.tx_session_ready is False
    assert client._rx_session_ready is False
    assert client._session_socket is None
    assert client.latest_frame() is None


def test_ratio_is_a_selection_code_not_a_divisor(daemon):
    client = armed_client(daemon)
    frame = client.capture({"samples": SAMPLES, "ratio": 2})
    assert frame.ratio == 2 and frame.decimation == 4
    assert frame.output_fs_hz == FS // 4
    assert daemon.capture_request["args"]["ratio"] == 2


def test_config_revision_and_connection_generation_are_bound_to_the_frame(daemon):
    client = armed_client(daemon)
    first = client.capture(NORMAL)
    assert first.config_revision == 0
    client.control_op("rf.configure", {"lo_hz": 90000000})
    second = client.capture(NORMAL)
    assert second.config_revision == 1
    assert second.capture_id != first.capture_id
    assert second.connection_generation == first.connection_generation


def test_a_device_without_a_release_id_still_names_the_frame(daemon):
    # release_id is optional on the wire, so device.info may omit it.  The frame keeps
    # that absence honestly and its LOCAL capture id says "unknown" instead of echoing
    # a Python None into the id.
    daemon.release_id = None
    client = armed_client(daemon)
    frame = client.capture(NORMAL)
    assert frame.release_id is None
    assert frame.capture_id.startswith("rx-unknown-g")


# ------------------------------------------------------------ wire rejection
@pytest.mark.parametrize("change,needle", [
    ({"id": 999}, "id"),
    ({"id": "7"}, "id"),                # a non-int id, not just a wrong one
    ({"direction": "tx"}, "direction"),
    ({"format": "s16_iq_be"}, "format"),
    ({"type": "status"}, "IQ frame"),
    ({"samples": 0}, "samples"),
    ({"samples": 4097}, "samples"),
    ({"ratio": 5}, "ratio"),
    ({"sample_rate_hz": 0}, "sample_rate_hz"),
    ({"timestamp_ns": -1}, "timestamp_ns"),
    ({"bytes": SAMPLES * 4 - 1}, "bytes"),
    ({"bytes": "16384"}, "bytes"),      # a non-int byte count
    ({"type": DROP}, "IQ frame"),       # the field is ABSENT, not wrong
])
def test_a_malformed_frame_header_is_rejected_and_nothing_is_published(
        daemon, change, needle):
    daemon.header_overrides = change
    client = armed_client(daemon)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(NORMAL)
    assert needle in str(refused.value), str(refused.value)
    assert client.latest_frame() is None
    assert client._session_socket is None


def test_a_byte_count_that_is_not_a_multiple_of_four_is_rejected(daemon):
    daemon.header_overrides = {"samples": 250, "bytes": 1002}
    client = armed_client(daemon)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture({"samples": 250, "ratio": 0})
    assert "bytes" in str(refused.value)
    assert client.latest_frame() is None


@pytest.mark.parametrize("field,value", [("samples", 64), ("ratio", 1)])
def test_a_legal_frame_must_match_the_capture_request(daemon, field, value):
    daemon.header_overrides[field] = value
    client = armed_client(daemon)
    with pytest.raises(StatusProtocolError, match=field):
        client.capture(NORMAL)
    assert client.latest_frame() is None
    assert client._session_socket is None


def test_an_illegal_signed12_sample_is_rejected(daemon):
    for value in (2048, -2049):
        daemon.payload_override = (struct.pack("<h", value)
                                   + b"\0" * (SAMPLES * 4 - 2))
        client = armed_client(daemon)
        with pytest.raises(StatusProtocolError) as refused:
            client.capture(NORMAL)
        assert "12-bit" in str(refused.value) or "sample" in str(refused.value)
        assert client.latest_frame() is None
        daemon.payload_override = None


def test_the_signed12_boundaries_are_legal(daemon):
    daemon.payload_override = (struct.pack("<hh", -2048, 2047)
                               + b"\0" * (SAMPLES * 4 - 4))
    client = armed_client(daemon)
    frame = client.capture(NORMAL)
    assert struct.unpack("<hh", frame.payload[:4]) == (-2048, 2047)


def test_a_truncated_payload_is_not_published(daemon):
    daemon.truncate_payload = 100
    client = armed_client(daemon)
    with pytest.raises(StatusProtocolError):
        client.capture(NORMAL)
    assert client.latest_frame() is None
    assert client._session_socket is None


def test_a_daemon_refusal_is_not_an_iq_frame_and_keeps_its_code(daemon):
    daemon.capture_error = {"code": "HARDWARE_IO",
                            "message": "the capture release was not confirmed"}
    client = armed_client(daemon)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(NORMAL)
    assert refused.value.code == "HARDWARE_IO"
    assert client.latest_frame() is None


def test_a_capture_that_leaves_the_arm_set_is_not_published(daemon):
    daemon.post_arm_stuck = True
    client = armed_client(daemon)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(NORMAL)
    assert "arm" in str(refused.value) or "release" in str(refused.value)
    assert client.latest_frame() is None


def test_a_release_that_changes_during_the_capture_is_rejected(daemon):
    daemon.release_after_capture = "0" * 32
    client = armed_client(daemon)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(NORMAL)
    assert "release" in str(refused.value)
    assert client.latest_frame() is None


def test_capture_deadline_defaults_to_three_seconds_and_a_stall_refuses(daemon):
    assert RfStatusClient().capture_timeout == 3.0
    with pytest.raises(ValueError):
        RfStatusClient(capture_timeout=0)
    daemon.stall_capture = True
    client = armed_client(daemon, capture_timeout=0.3)
    start = time.monotonic()
    with pytest.raises(StatusProtocolError):
        client.capture(NORMAL)
    assert time.monotonic() - start < 1.5
    assert client.latest_frame() is None
    assert daemon.ops().count("rx.capture") == 1, "the capture was retried"


# ----------------------------------------------------------- local validation
@pytest.mark.parametrize("args,needle", [
    ({}, "samples"),
    ({"samples": 0}, "samples"),
    ({"samples": 4097}, "samples"),
    ({"samples": True}, "samples"),
    ({"samples": 4096.0}, "samples"),
    ({"samples": "4096"}, "samples"),
    ({"samples": 4096}, "ratio"),
    ({"samples": 4096, "ratio": 5}, "ratio"),
    ({"samples": 4096, "ratio": -1}, "ratio"),
    ({"samples": 4096, "ratio": True}, "ratio"),
    ({"samples": 4096, "ratio": 0, "continuous": False}, "continuous"),
    ({"samples": 4096, "ratio": 0, "bytes": 16384}, "bytes"),
    ("not-a-dict", "samples"),
])
def test_invalid_capture_arguments_are_rejected_without_touching_the_network(
        daemon, args, needle):
    client = armed_client(daemon)
    count = len(daemon.seen)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(args)
    assert needle in str(refused.value), str(refused.value)
    assert len(daemon.seen) == count, "an invalid request was sent anyway"
    assert client.latest_frame() is None


def test_a_local_rejection_does_not_discard_a_valid_session(daemon):
    client = armed_client(daemon)
    frame = client.capture(NORMAL)
    with pytest.raises(StatusProtocolError):
        client.capture({"samples": 4097, "ratio": 0})
    assert client._session_socket is not None
    assert client.latest_frame() is frame


def test_a_capture_is_refused_locally_when_it_was_not_advertised(daemon):
    daemon.control_ops = [op for op in CONTROLS if op != "rx.capture"]
    client = armed_client(daemon)
    count = len(daemon.seen)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(NORMAL)
    assert "advertise" in str(refused.value)
    assert len(daemon.seen) == count


# ------------------------------------------------------------ physical path
def test_a_physical_capture_requires_a_loaded_tx(daemon):
    client = armed_client(daemon)
    count = len(daemon.seen)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(PHYSICAL, physical=True)
    assert "loaded" in str(refused.value)
    assert len(daemon.seen) == count


def test_the_loopback_gate_reads_the_single_source():
    """The accepted value is the teaching profile's constant, not a second literal."""
    import rf_status_client as RC
    import rf_teaching_profile as TP
    assert RC.ATTENUATION_MDB == TP.ATTENUATION_MDB == 46000
    # A release that predates the profile still parses these current sources; the
    # compatibility pin is asserted equal to the source here so it cannot drift.
    assert TP.ATTENUATION_MDB == dict(TP.teaching_capability().rf_parameters)['attenuation_mdB']


@pytest.mark.parametrize("change,needle", [
    ({"cable_confirmed": None}, "cable"),
    ({"cable_confirmed": False}, "cable"),
    ({"cable_confirmed": 1}, "cable"),
    ({"attenuation_mdB": None}, "attenuation"),
    ({"attenuation_mdB": 40000}, "attenuation"),   # the historical 40 dB domain
    ({"attenuation_mdB": 45999}, "attenuation"),
    ({"attenuation_mdB": 46100}, "attenuation"),
    ({"attenuation_mdB": True}, "attenuation"),
])
def test_a_physical_capture_requires_the_explicit_46000_mdb_attestation(
        daemon, change, needle):
    client = armed_client(daemon)
    client.control_op("tx.load", LOAD_META, payload=LOAD_PAYLOAD)
    args = dict(PHYSICAL, **change)
    args = {k: v for k, v in args.items() if v is not None}
    count = len(daemon.seen)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(args, physical=True)
    assert needle in str(refused.value), str(refused.value)
    assert len(daemon.seen) == count
    assert client.latest_frame() is None


def test_a_physical_capture_sends_the_loopback_op_with_the_attestation(daemon):
    client = armed_client(daemon)
    client.control_op("tx.load", LOAD_META, payload=LOAD_PAYLOAD)
    frame = client.capture(PHYSICAL, physical=True)
    assert frame.physical is True and frame.op == "loopback.physical.capture"
    assert daemon.capture_op == "loopback.physical.capture"
    assert daemon.capture_request["args"] == PHYSICAL
    assert frame.post_readback.tx_source_selected is False
    assert "rx.capture" not in daemon.ops()


def test_a_physical_capture_that_leaves_the_source_on_is_not_published(daemon):
    daemon.source_stuck = True
    client = armed_client(daemon)
    client.control_op("tx.load", LOAD_META, payload=LOAD_PAYLOAD)
    with pytest.raises(StatusProtocolError) as refused:
        client.capture(PHYSICAL, physical=True)
    assert "source" in str(refused.value) or "SRC" in str(refused.value)
    assert client.latest_frame() is None


# --------------------------------------------------------- session lifetime
def test_an_unexpected_disconnect_discards_the_frame_and_the_loaded_tx(daemon):
    client = armed_client(daemon)
    client.control_op("tx.load", LOAD_META, payload=LOAD_PAYLOAD)
    first = client.capture(NORMAL)
    assert client.latest_frame() is first

    daemon.disconnect_next = True
    snapshot = client.poll()
    assert not snapshot.online and snapshot.error
    assert client._session_socket is None
    assert client.latest_frame() is None, (
        "an unexpected disconnect must discard the frozen result")
    assert client.poll().tx_session_ready is False

    second = client.capture(NORMAL)
    assert second.connection_generation > first.connection_generation
    assert second.config_revision == first.config_revision
    assert daemon.ops().count("rx.capture") == 2, "only the explicit captures ran"
    assert "control.arm" not in daemon.ops()
    assert "tx.start" not in daemon.ops()


def test_disarm_drops_the_retained_session_and_the_result(daemon):
    client = armed_client(daemon)
    client.capture(NORMAL)
    client.control_disarm()
    assert client.latest_frame() is None
    assert client._session_socket is None
