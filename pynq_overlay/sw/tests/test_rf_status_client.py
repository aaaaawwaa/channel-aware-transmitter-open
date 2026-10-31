import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rf_status_client import RfStatusClient, StatusProtocolError

OPS = ("protocol.hello", "device.info", "state.get", "status.subscribe", "pl.status.get")
CAPABILITIES = ("protocol.hello", "protocol.close", "device.info", "state.get", "status.subscribe", "pl.status.get", "rf.interface_status")
PL = {"sampled_at_monotonic_ns": 123456789, "io_ok": True,
      "tx": {"source_selected": True, "length_words": 73, "position_words": 4},
      "rx": {"armed": False, "done": True, "position_words": 8}}
RESULTS = (
    {"daemon": "1.0", "protocol_major": 1, "protocol_minor": 0, "max_samples": 4096},
    {"mode": "status_only", "core": False, "capabilities": {"ops": list(CAPABILITIES)},
     "release_id": "r1", "bit_sha256": "a" * 64, "hwh_sha256": "b" * 64},
    {"state": "OFF", "pending": False, "pending_op": None, "lease_held": False, "max_samples": 4096},
    {"state": "OFF", "pending": False, "pending_op": None, "lease_held": False,
     "max_samples": 4096, "subscribed": True}, PL)


def server(reply_mutator=None, fragment=False, trickle=0.0, control=None,
           refusals=None):
    """A scripted RF daemon.

    Connection 1 is served the five read-only poll operations in OPS order; any
    other request on it is recorded and refused, so a client that sends something
    it was not told it may send is visible in `seen` rather than as a dead socket.
    With `control` given -- a dict from operation name to the result object the
    daemon answers with -- the listener then accepts exactly one more connection
    and serves control operations on it.  That second connection is not an
    invention: one poll is one connection and one control call is another, which is
    the client's real shape.  A control operation that is not a key of `control` is
    recorded and refused with ok:false, so "the daemon said no" is testable.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]
    seen = []

    def read_line(conn, buf):
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None, buf
            buf += chunk
        line, buf = buf.split(b"\n", 1)
        return line, buf

    def send(conn, data):
        if fragment or trickle:
            for byte in data:
                conn.sendall(bytes([byte]))
                if trickle:
                    time.sleep(trickle)
        else:
            conn.sendall(data)

    def serve_poll(conn):
        buf = b""
        for i, expected in enumerate(OPS):
            line, buf = read_line(conn, buf)
            if line is None:
                return False
            req = json.loads(line)
            seen.append(req)
            assert req["op"] == expected
            out = {"id": req["id"], "ok": True, "result": RESULTS[i]}
            if reply_mutator:
                out = reply_mutator(i, out)
            data = (json.dumps(out) + "\n").encode() if isinstance(out, dict) else out
            try:
                send(conn, data)
            except OSError:
                return False
        return True

    def serve_requests(conn):
        buf = b""
        first = True
        while True:
            line, buf = read_line(conn, buf)
            if line is None:
                return
            req = json.loads(line)
            seen.append(req)
            if first and req["op"] != "protocol.hello":
                # The real daemon refuses any other first request on a connection:
                # "the first request on a connection must be protocol.hello" (measured
                # on the board 2026-09-30).  Enforcing the same rule here is what makes
                # a client that skips the handshake fail LOUDLY, instead of passing
                # against a fixture more permissive than the daemon it stands for.
                conn.sendall((json.dumps({"id": req["id"], "ok": False, "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": "the first request on a connection must be "
                               "protocol.hello"}}) + "\n").encode())
                return
            first = False
            if req["op"] == "protocol.hello":
                conn.sendall((json.dumps({"id": req["id"], "ok": True,
                                          "result": RESULTS[0]}) + "\n").encode())
                continue
            result = (control or {}).get(req["op"])
            refusal = (refusals or {}).get(req["op"])
            if refusal is not None:
                # A scripted daemon refusal: the error object is exactly what the test
                # asks for, so "the code is lifted" and "a malformed error is harmless"
                # are both testable without inventing a second server shape.
                out = {"id": req["id"], "ok": False, "error": refusal}
            elif result is None:
                out = {"id": req["id"], "ok": False,
                       "error": {"code": "UNSUPPORTED",
                                 "message": "the daemon did not advertise " + req["op"]}}
            else:
                out = {"id": req["id"], "ok": True, "result": result}
            try:
                conn.sendall((json.dumps(out) + "\n").encode())
            except OSError:
                return

    def run():
        try:
            conn, _ = listener.accept()
            with conn:
                if not serve_poll(conn):
                    return
                if control is None:
                    serve_requests(conn)
            if control is not None:
                conn, _ = listener.accept()
                with conn:
                    serve_requests(conn)
        finally:
            listener.close()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, seen, thread


def test_full_read_only_poll_and_fragmented_lines():
    port, seen, th = server(fragment=True)
    snap = RfStatusClient(port=port, timeout=2).poll()
    th.join(2)
    assert snap.online and not snap.stale and snap.error is None
    assert snap.mode == "status_only" and snap.core is False
    assert snap.capabilities == CAPABILITIES
    assert snap.daemon_state == "OFF"
    assert snap.pl_status.sampled_at_monotonic_ns == 123456789
    assert snap.pl_status.tx.length_words == 73
    assert snap.pl_status.rx.done is True
    assert [v["op"] for v in seen] == list(OPS)
    assert all(v["args"] == {} for v in seen[1:])


def test_bad_json_and_wrong_id_are_unavailable():
    for mutator in (lambda i, out: b"broken\n" if i == 2 else out,
                    lambda i, out: dict(out, id=999) if i == 2 else out):
        port, _, th = server(reply_mutator=mutator)
        snap = RfStatusClient(port=port, timeout=1).poll()
        th.join(2)
        assert not snap.online and snap.error
        assert snap.pl_status.tx.length_words is None


def test_failed_pl_fields_remain_unknown():
    def mutate(i, out):
        if i == 4:
            out = dict(out, result={"sampled_at_monotonic_ns": 123, "io_ok": False,
                                    "tx": {"source_selected": None},
                                    "rx": {"armed": None, "done": None}})
        return out
    port, _, th = server(reply_mutator=mutate)
    snap = RfStatusClient(port=port, timeout=1).poll()
    th.join(2)
    assert snap.online and snap.error
    assert snap.pl_status.tx.length_words is None
    assert snap.pl_status.rx.position_words is None
    assert snap.pl_status.io_ok is False


def test_disconnect_and_total_trickle_deadline():
    port = unused_port()
    snap = RfStatusClient(port=port, timeout=0.15).poll()
    assert not snap.online and snap.error
    port, _, th = server(trickle=0.025)
    start = time.monotonic()
    snap = RfStatusClient(port=port, timeout=0.2).poll()
    elapsed = time.monotonic() - start
    assert not snap.online and snap.error
    assert elapsed < 0.55
    th.join(2)


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]



def test_combined_response_lines_are_buffered():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def run():
        try:
            conn, _ = listener.accept()
            with conn:
                buf = b""
                for i in range(5):
                    while b"\n" not in buf:
                        buf += conn.recv(4096)
                    line, buf = buf.split(b"\n", 1)
                    assert json.loads(line)["op"] == OPS[i]
                    if i == 0:
                        pair = b"".join((json.dumps({"id": j + 1, "ok": True,
                                                     "result": RESULTS[j]}) + "\n").encode()
                                        for j in (0, 1))
                        conn.sendall(pair)
                    elif i != 1:
                        conn.sendall((json.dumps({"id": i + 1, "ok": True,
                                                 "result": RESULTS[i]}) + "\n").encode())
        finally:
            listener.close()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    snap = RfStatusClient(port=port, timeout=1).poll()
    th.join(2)
    assert snap.online and snap.pl_status.tx.length_words == 73


def test_control_capability_is_rejected():
    def mutate(i, out):
        if i == 1:
            result = dict(out["result"])
            result["capabilities"] = {"ops": list(CAPABILITIES) + ["rf.init"]}
            return dict(out, result=result)
        return out
    port, _, th = server(reply_mutator=mutate)
    snap = RfStatusClient(port=port, timeout=1).poll()
    th.join(2)
    assert not snap.online and "status-only" in snap.error


# --- phase 2 fields (armed / control_ops) ------------------------------------------------

def _device_info_mutator(add_fields):
    """Mutate only the device.info reply.

    Detection is by the `mode` key rather than by OPS index: the ordering of OPS is not
    this criterion's business.  The result is deep-copied because RESULTS is a module-level
    list -- mutating it in place would leak into every other test in this file.
    """
    def mutator(_index, out):
        result = out.get("result") if isinstance(out, dict) else None
        if not isinstance(result, dict) or "mode" not in result:
            return out
        result = json.loads(json.dumps(result))
        add_fields(result)
        return {"id": out["id"], "ok": True, "result": result}
    return mutator


def test_absent_phase_two_fields_mean_unarmed_and_no_control_ops():
    port, _, th = server()
    snapshot = RfStatusClient(port=port).poll()
    th.join(timeout=2)
    assert snapshot.error is None, snapshot.error
    assert snapshot.armed is False and snapshot.control_ops == (), (
        "a phase-1 daemon sends neither field; the client must read that as unarmed with "
        "no control operations rather than inventing capability")


def test_phase_two_fields_are_parsed():
    def add(result):
        result["armed"] = True
        result.setdefault("capabilities", {})["control_ops"] = ["control.disarm", "rf.init"]

    port, _, th = server(reply_mutator=_device_info_mutator(add))
    snapshot = RfStatusClient(port=port).poll()
    th.join(timeout=2)
    assert snapshot.error is None, snapshot.error
    assert snapshot.armed is True
    assert snapshot.control_ops == ("control.disarm", "rf.init")


def test_a_control_op_advertised_as_read_only_is_rejected():
    def add(result):
        result.setdefault("capabilities", {})["control_ops"] = ["device.info"]

    port, _, th = server(reply_mutator=_device_info_mutator(add))
    snapshot = RfStatusClient(port=port).poll()
    th.join(timeout=2)
    assert snapshot.error and "control operation" in snapshot.error, snapshot.error


def test_a_non_boolean_armed_field_is_rejected():
    def add(result):
        result["armed"] = "yes"

    port, _, th = server(reply_mutator=_device_info_mutator(add))
    snapshot = RfStatusClient(port=port).poll()
    th.join(timeout=2)
    assert snapshot.error and "armed" in snapshot.error, snapshot.error


# --- phase 2: the client half of the arming gate (plan Task 2 Step 2) -------------------
#
# "The client never sends more than it was told it may" is a LOCAL rule, so it needs its
# own criteria: the server-side gate is asserted in test_rf_daemon.c, and a client that
# sends an operation the daemon did not advertise would look identical from the daemon's
# side (it refuses) while being a bug on the client's.  The daemon advertises the control
# operations reachable RIGHT NOW in `capabilities.control_ops`, so the client's rule is
# "send only what the most recent snapshot advertised".


def _refusal(call):
    """The StatusProtocolError message `call` raises, or None if it did not raise one.

    A control call that returns a value when it should refuse -- or that fails with a
    transport error instead of a protocol refusal -- must not pass a criterion, so the
    tests below compare the MESSAGE and not merely "something was raised"."""
    try:
        call()
    except StatusProtocolError as exc:
        return str(exc)
    except Exception:                             # noqa: BLE001 - reported as "no refusal"
        return None
    return None


def _advertise(ops, armed=False):
    """Advertise exactly `ops` as the daemon's reachable control operations."""
    def add(result):
        result["armed"] = armed
        result.setdefault("capabilities", {})["control_ops"] = list(ops)
    return _device_info_mutator(add)


def test_control_arm_is_refused_locally_when_it_was_not_advertised():
    port, seen, th = server()                     # a phase-1 daemon: no control_ops at all
    client = RfStatusClient(port=port, timeout=0.5)
    assert client.poll().error is None
    message = _refusal(lambda: client.control_arm("s3cret"))
    th.join(timeout=2)
    assert message and "control.arm" in message, message
    assert [v["op"] for v in seen] == list(OPS), (
        "an operation the daemon never advertised was sent anyway: %s"
        % [v["op"] for v in seen])


def test_control_arm_is_sent_and_returns_the_daemons_result_when_advertised():
    port, seen, th = server(reply_mutator=_advertise(["control.arm"]),
                            control={"control.arm": {"armed": True}})
    client = RfStatusClient(port=port, timeout=1)
    assert client.poll().error is None
    assert client.control_arm("s3cret") == {"armed": True}
    th.join(timeout=2)
    assert [v["op"] for v in seen] == list(OPS) + ["protocol.hello", "control.arm"]
    assert seen[-1]["args"] == {"token": "<SET_LOCALLY>"}


def test_a_control_op_the_daemon_refuses_is_not_a_success():
    port, seen, th = server(reply_mutator=_advertise(["control.arm"]), control={})
    client = RfStatusClient(port=port, timeout=1)
    assert client.poll().error is None
    message = _refusal(lambda: client.control_arm("s3cret"))
    th.join(timeout=2)
    assert message and "control.arm" in message, message
    assert [v["op"] for v in seen] == list(OPS) + ["protocol.hello", "control.arm"], (
        "the operation was advertised, so it must be sent -- what must not happen is "
        "reporting the daemon's ok:false as a success")


def test_control_disarm_is_sent_only_while_the_daemon_advertises_it():
    port, seen, th = server(reply_mutator=_advertise(["control.disarm"], armed=True),
                            control={"control.disarm": {"armed": False}})
    client = RfStatusClient(port=port, timeout=1)
    snapshot = client.poll()
    assert snapshot.error is None and snapshot.armed is True, snapshot.error
    assert client.control_disarm() == {"armed": False}
    th.join(timeout=2)
    assert [v["op"] for v in seen] == list(OPS) + ["protocol.hello", "control.disarm"]
    assert seen[-1]["args"] == {}

    port, seen, th = server()                     # unarmed: not advertised, so not sent
    client = RfStatusClient(port=port, timeout=0.5)
    assert client.poll().error is None
    message = _refusal(client.control_disarm)
    th.join(timeout=2)
    assert message and "control.disarm" in message, message
    assert [v["op"] for v in seen] == list(OPS)


def test_a_control_call_before_any_snapshot_is_refused_locally():
    """The last snapshot is the only thing that says what may be sent, and a client
    that has never polled has no such snapshot -- so it must send nothing rather than
    guess.  This is the local half of the fail-closed default."""
    client = RfStatusClient(port=unused_port(), timeout=0.2)
    for call in (lambda: client.control_disarm(), lambda: client.control_arm("t")):
        message = _refusal(call)
        assert message and "advertise" in message, message

def _refusal_error(call):
    """(message, code) from the StatusProtocolError `call` raises, or (None, None)."""
    try:
        call()
    except StatusProtocolError as exc:
        return str(exc), exc.code
    except Exception as exc:                      # noqa: BLE001 - reported as no refusal
        return str(exc), None
    return None, None


def test_a_refused_control_op_carries_the_daemons_code():
    """A refusal must carry the daemon's code, not just a generic message.

    The daemon answers `ok:false` with `error.code`; the panel needs that code to show
    the operator WHY a control action failed (plan Task 6 Step 1).  Losing it turns
    every failure into "RF daemon refused control.arm", which is not actionable.
    """
    port, seen, th = server(reply_mutator=_advertise(["control.arm"]), control={},
                            refusals={"control.arm": {"code": "PERMISSION",
                                                      "message": "control token rejected"}})
    client = RfStatusClient(port=port, timeout=1)
    assert client.poll().error is None
    message, code = _refusal_error(lambda: client.control_arm("s3cret"))
    th.join(timeout=2)
    assert message and "control.arm" in message, message
    assert code == "PERMISSION", code


def test_a_refusal_without_a_usable_error_code_carries_none():
    """A malformed/absent error object must degrade to code=None, never crash.

    The display layer then shows the message alone instead of inventing a code.
    """
    for bad in ("not-a-dict", {"code": 7}, {}):
        port, seen, th = server(reply_mutator=_advertise(["control.arm"]), control={},
                                refusals={"control.arm": bad})
        client = RfStatusClient(port=port, timeout=1)
        assert client.poll().error is None
        message, code = _refusal_error(lambda: client.control_arm("s3cret"))
        th.join(timeout=2)
        assert message and "control.arm" in message, (bad, message)
        assert code is None, (bad, code)


# --- 2026-09-30: the armed shape the real board actually sends (delivery record 41) ----


def test_a_poll_while_armed_stays_online_and_keeps_the_advertised_ops():
    """The board's armed shape: mode stays "status_only" AND core becomes True.

    Before this, a fixture like this did not exist and the poll gate required
    `core is False`, so the FIRST poll after an operator armed returned an error
    snapshot.  That error snapshot replaced the good one and took `control_ops` with
    it, so the panel could no longer send anything -- not even disarm.  The read
    path's safety comes from the read-only op set, not from `core`.
    """
    armed_ops = ["control.disarm", "rf.init", "rf.configure", "tx.stop"]

    def add(result):
        result["armed"] = True
        result["core"] = True
        result.setdefault("capabilities", {})["control_ops"] = list(armed_ops)

    port, _, th = server(reply_mutator=_device_info_mutator(add))
    snapshot = RfStatusClient(port=port).poll()
    th.join(timeout=2)
    assert snapshot.error is None, snapshot.error
    assert snapshot.armed is True
    assert snapshot.core is True, "the snapshot must quote the daemon, not hardcode False"
    assert snapshot.control_ops == tuple(armed_ops)


def test_a_non_boolean_core_field_is_rejected():
    def add(result):
        result["core"] = "yes"

    port, _, th = server(reply_mutator=_device_info_mutator(add))
    snapshot = RfStatusClient(port=port).poll()
    th.join(timeout=2)
    assert snapshot.error and "core" in snapshot.error, snapshot.error


def test_control_op_is_refused_locally_when_it_was_not_advertised():
    port, seen, th = server()
    client = RfStatusClient(port=port, timeout=0.5)
    assert client.poll().error is None
    message = _refusal(lambda: client.control_op("rf.configure", {"lo_hz": 1}))
    th.join(timeout=2)
    assert message and "rf.configure" in message, message
    assert [v["op"] for v in seen] == list(OPS)


def test_control_op_sends_the_advertised_op_with_its_arguments():
    port, seen, th = server(reply_mutator=_advertise(["rf.configure"], armed=True),
                            control={"rf.configure": {"hardware_verified": True}})
    client = RfStatusClient(port=port, timeout=1)
    assert client.poll().error is None
    assert client.control_op("rf.configure", {"lo_hz": 90000000}) == {
        "hardware_verified": True}
    th.join(timeout=2)
    assert [v["op"] for v in seen] == list(OPS) + ["protocol.hello", "rf.configure"]
    assert seen[-1]["args"] == {"lo_hz": 90000000}


def test_a_control_connection_opens_with_the_protocol_handshake():
    """The daemon refuses any other first request on a connection: "the first request on
    a connection must be protocol.hello" (measured on the board 2026-09-30).  The fixture
    enforces that rule now, so a client that skips the handshake fails loudly; this pins
    the client's side of it explicitly.
    """
    port, seen, th = server(reply_mutator=_advertise(["control.arm"]),
                            control={"control.arm": {"armed": True}})
    client = RfStatusClient(port=port, timeout=1)
    assert client.poll().error is None
    assert client.control_arm("s3cret") == {"armed": True}
    th.join(timeout=2)
    assert [v["op"] for v in seen] == list(OPS) + ["protocol.hello", "control.arm"], (
        "the control connection did not open with the handshake")
    assert seen[len(OPS)]["op"] == "protocol.hello"


def test_rx_data_delay_is_validated_before_any_connection():
    """0..15 is the entire accepted set; negatives, 16 and bools are local errors."""
    client = RfStatusClient(port=unused_port(), timeout=0.2)
    for bad in (-1, 16, 0x10, True):
        try:
            client.set_rx_data_delay(bad)
        except ValueError:
            pass
        else:                                     # pragma: no cover - explicit failure
            raise AssertionError("accepted invalid RX data delay %r" % (bad,))


def test_rx_data_delay_is_sent_only_when_advertised():
    port, seen, th = server(reply_mutator=_advertise(["rf.rx_data_delay.set"], armed=True),
                            control={"rf.rx_data_delay.set": {"changed": True}})
    client = RfStatusClient(port=port, timeout=1)
    assert client.poll().error is None
    assert client.set_rx_data_delay(5) == {"changed": True}
    th.join(timeout=2)
    assert seen[-1]["op"] == "rf.rx_data_delay.set"
    assert seen[-1]["args"] == {"value": 5}


def test_interface_status_is_not_a_control_capability():
    """The read-only interface op is not in control_ops; it must not be smuggled into
    the control path or advertised as a settable operation."""
    port, seen, th = server(reply_mutator=_advertise(["control.arm"], armed=True))
    client = RfStatusClient(port=port, timeout=1)
    assert client.poll().error is None
    assert "rf.interface_status" not in client._advertised()
    th.join(timeout=2)
