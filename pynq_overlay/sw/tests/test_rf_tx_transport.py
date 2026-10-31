import json
import socket
import struct
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rf_status_client import RfStatusClient, StatusProtocolError, READ_ONLY_OPS

META = {"samples":1024,"bytes":4096,"format":"s16_iq_le","direction":"tx","wrap":True}
PAYLOAD = b"\0" * 4096
CONTROLS = ["tx.load", "tx.start", "tx.stop", "control.arm", "control.disarm"]


class Daemon:
    """Real TCP framing with a single synchronous connection, like the daemon."""
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.seen = []
        self.frames = []
        self.connections = 0
        self.closed = 0
        self.armed = True
        self.state = "READY"
        self.fail_op = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            self.connections += 1
            number = self.connections
            conn.settimeout(.1)
            with conn:
                buffer = b""
                while not self.stop.is_set():
                    try:
                        while b"\n" not in buffer:
                            part = conn.recv(8192)
                            if not part:
                                raise EOFError
                            buffer += part
                        line, buffer = buffer.split(b"\n", 1)
                        request = json.loads(line)
                        op = request["op"]
                        self.seen.append((number, request))
                        if op == "tx.load":
                            count = request["args"]["bytes"]
                            while len(buffer) < count:
                                buffer += conn.recv(8192)
                            self.frames.append(buffer[:count])
                            buffer = buffer[count:]
                        if op == self.fail_op:
                            conn.sendall((json.dumps({"id":request["id"], "ok":False,
                                "error":{"code":"BAD_STATE"}})+"\n").encode())
                            continue
                        if op == "protocol.hello":
                            result = {"protocol_major":1}
                        elif op == "device.info":
                            result = {"mode":"status_only", "core":self.armed,
                                "armed":self.armed, "capabilities":{"ops":list(READ_ONLY_OPS),
                                "control_ops":CONTROLS}}
                        elif op in ("state.get", "status.subscribe"):
                            result = {"state":self.state,"pending":False,"pending_op":None,
                                "lease_held":self.armed,"subscribed":True}
                        elif op == "pl.status.get":
                            result = {"sampled_at_monotonic_ns":1,"io_ok":True,
                                "tx":{"source_selected":self.state=="TX_ACTIVE", "length_words":1024,
                                    "position_words":1}, "rx":{"armed":False,"done":False,"position_words":0}}
                        else:
                            if op == "tx.start": self.state = "TX_ACTIVE"
                            if op == "tx.stop": self.state = "READY"
                            if op == "control.arm": self.armed = True; self.state = "READY"
                            if op == "control.disarm": self.armed = False; self.state = "OFFLINE"
                            result = {"accepted":True}
                        conn.sendall((json.dumps({"id":request["id"],"ok":True,
                            "result":result})+"\n").encode())
                    except socket.timeout:
                        continue
                    except (OSError, EOFError):
                        break
            self.closed += 1
            if self.state == "TX_ACTIVE": self.state = "READY"
        self.listener.close()

    def close(self):
        self.stop.set()
        self.thread.join(1)


@pytest.fixture
def daemon():
    d = Daemon()
    yield d
    d.close()


def test_load_poll_start_reuses_one_binary_session_and_stop_closes(daemon):
    client = RfStatusClient(port=daemon.port, timeout=1)
    assert client.poll().online
    client.control_op("tx.load", META, payload=PAYLOAD)
    ready = client.poll()
    assert ready.tx_session_ready and ready.daemon_state == "READY"
    client.control_op("tx.start", {})
    active = client.poll()
    assert active.tx_session_ready and active.daemon_state == "TX_ACTIVE"
    assert daemon.frames == [PAYLOAD]
    retained = [req for number, req in daemon.seen if number == 2]
    assert sum(req["op"] == "protocol.hello" for req in retained) == 1
    assert [req["id"] for req in retained] == list(range(1, len(retained)+1))
    assert daemon.connections == 2
    client.control_op("tx.stop", {})
    assert not client.poll().tx_session_ready
    assert daemon.connections == 3
    client.close()


def test_expiry_releases_socket_and_explicit_rearm_does_not_restart(daemon):
    client = RfStatusClient(port=daemon.port, timeout=1)
    client.poll()
    client.control_op("tx.load", META, payload=PAYLOAD)
    daemon.armed = False
    daemon.state = "OFFLINE"
    assert not client.poll().tx_session_ready
    client.control_arm("explicit-token")
    fresh = client.poll()
    assert fresh.armed and not fresh.tx_session_ready
    assert not any(req["op"] == "tx.start" for _, req in daemon.seen)
    client.close()


def test_session_error_then_fresh_ready_is_not_loaded(daemon):
    client = RfStatusClient(port=daemon.port, timeout=1)
    client.poll()
    client.control_op("tx.load", META, payload=PAYLOAD)
    daemon.fail_op = "state.get"
    assert not client.poll().online
    daemon.fail_op = None
    fresh = client.poll()
    assert fresh.online and fresh.daemon_state == "READY" and fresh.armed
    assert not fresh.tx_session_ready
    with pytest.raises(StatusProtocolError):
        client.control_op("tx.start", {})
    client.close()


@pytest.mark.parametrize("change", [{"bytes":4095},{"samples":True},{"direction":"rx"},
    {"wrap":1},{"format":"s16_iq_be"},{"samples":4097,"bytes":16388}])
def test_bad_frame_refused_before_network(daemon, change):
    client = RfStatusClient(port=daemon.port, timeout=1)
    client.poll()
    meta = dict(META, **change)
    count = len(daemon.seen)
    with pytest.raises((ValueError, StatusProtocolError)):
        client.control_op("tx.load", meta, payload=PAYLOAD)
    assert len(daemon.seen) == count


def test_waveform_explicit_ints_little_endian_and_q_zero():
    from rf_tx_waveform import build_tx_waveform
    metadata, payload = build_tx_waveform({"cycles":32,"amplitude":1500})
    assert metadata == META and len(payload) == 4096
    values = struct.unpack("<2048h", payload)
    assert set(values[1::2]) == {0}
    assert values[16] == 1500 and values[48] == -1500
    assert max(values) == 1500 and min(values) == -1500
    for args in ({}, {"cycles":True,"amplitude":1},{"cycles":512,"amplitude":1},
                 {"cycles":1,"amplitude":2048},{"cycles":0,"amplitude":1},
                 {"cycles":1.,"amplitude":1}):
        with pytest.raises(ValueError): build_tx_waveform(args)
    zero_meta, zero_payload = build_tx_waveform({"cycles":1,"amplitude":0})
    assert zero_meta == META and struct.unpack("<2048h", zero_payload) == (0,) * 2048


def test_worker_binary_load_native_evidence_and_shutdown_close():
    from rf_status_worker import RfStatusWorker
    class Client:
        def __init__(self): self.calls = []; self.closed = False
        def control_op(self, op, args, payload=b""):
            self.calls.append((op, args, payload)); return {"accepted":True}
        def close(self): self.closed = True
    client = Client()
    worker = RfStatusWorker(client)
    args = {"cycles":32,"amplitude":1500}
    result = worker.control("load", args=args)
    assert result["ok"] and result["args"] == args and result["result"] == {"accepted":True}
    assert client.calls[0][0:2] == ("tx.load", META)
    assert len(client.calls[0][2]) == 4096
    assert worker.control("start")["ok"]
    assert client.calls[-1] == ("tx.start", {}, b"")
    worker.stop()
    assert client.closed


@pytest.mark.parametrize("state", ["ERROR", "UNKNOWN", "OFFLINE"])
def test_unknown_held_state_closes_and_clears_local_evidence(daemon, state):
    client = RfStatusClient(port=daemon.port, timeout=1)
    client.poll()
    client.control_op("tx.load", META, payload=PAYLOAD)
    daemon.state = state
    snapshot = client.poll()
    assert snapshot.online and not snapshot.tx_session_ready
    assert client._session_socket is None
    client.close()


def test_tx_active_returning_ready_closes_session(daemon):
    client = RfStatusClient(port=daemon.port, timeout=1)
    client.poll()
    client.control_op("tx.load", META, payload=PAYLOAD)
    client.control_op("tx.start", {})
    assert client.poll().tx_session_ready
    daemon.state = "READY"
    assert not client.poll().tx_session_ready
    assert client._session_socket is None
    client.close()


def test_start_refusal_is_not_retried_and_closes_loaded_session(daemon):
    client = RfStatusClient(port=daemon.port, timeout=1)
    client.poll()
    client.control_op("tx.load", META, payload=PAYLOAD)
    daemon.fail_op = "tx.start"
    with pytest.raises(StatusProtocolError) as error:
        client.control_op("tx.start", {})
    assert error.value.code == "BAD_STATE"
    assert client._session_socket is None
    assert sum(req["op"] == "tx.start" for _, req in daemon.seen) == 1
    assert not client.poll().tx_session_ready
    client.close()


def test_signed_sample_bounds_and_unexpected_binary_are_not_sent(daemon):
    client = RfStatusClient(port=daemon.port, timeout=1)
    client.poll()
    count = len(daemon.seen)
    bad = struct.pack("<h", 2048) + PAYLOAD[2:]
    with pytest.raises(StatusProtocolError): client.control_op("tx.load", META, payload=bad)
    with pytest.raises(StatusProtocolError): client.control_op("tx.start", {}, payload=b"extra")
    assert len(daemon.seen) == count
    client.close()


def test_stale_snapshot_drops_local_loaded_evidence():
    from rf_status_client import RfStatusSnapshot
    snapshot = RfStatusSnapshot(tx_session_ready=True)
    assert snapshot.to_dict()["tx_session_ready"] is True
    assert not snapshot.expired().tx_session_ready


def test_local_waveform_error_releases_previously_held_client():
    from rf_status_worker import RfStatusWorker
    class Client:
        def __init__(self): self.closed = False; self.calls = []
        def close(self): self.closed = True
        def control_op(self, *args, **kwargs): self.calls.append((args, kwargs))
    client = Client()
    worker = RfStatusWorker(client)
    result = worker.control("load", args={"cycles":0,"amplitude":1500})
    assert not result["ok"] and not client.calls
    assert client.closed

def test_extra_waveform_types_build():
    from rf_tx_waveform import build_tx_waveform
    for wf in ("cw", "square", "triangle", "sawtooth", "multitone", "chirp", "noise"):
        args = {"waveform": wf, "amplitude": 1000}
        if wf in ("square", "triangle", "sawtooth", "multitone", "chirp"):
            args["cycles"] = 16
        if wf == "multitone":
            args.update({"tone2_cycles": 8, "tone2_amplitude": 500})
        if wf == "chirp":
            args["chirp_f1_cycles"] = 32
        if wf == "noise":
            args["seed"] = 1
        meta, payload = build_tx_waveform(args)
        assert meta["samples"] == 1024 and len(payload) == 4096

def test_build_tx_waveform_digital_and_impairment():
    from rf_tx_waveform import build_tx_waveform
    base = {
        "waveform": "digital", "mode": "QPSK", "filter": "RECT",
        "rolloff": "0.35", "seed": 1, "symbol_rate_hz": 2_000_000,
        "sample_rate_hz": 60_000_000, "amplitude": 1500,
    }
    meta, payload = build_tx_waveform(base)
    assert meta["samples"] == 3810 and meta["bytes"] == 3810 * 4
    assert len(payload) == 3810 * 4
    imp = {"enabled": True, "noise_pct": 0, "noise_seed": 0, "taps": [
        {"gain_q8": 128, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0}]}
    meta2, payload2 = build_tx_waveform(dict(base, impairment=imp))
    assert meta2["samples"] == 3810 and len(payload2) == 3810 * 4


def test_worker_binary_load_digital_frame_native_evidence():
    from rf_status_worker import RfStatusWorker
    class Client:
        def __init__(self): self.calls = []; self.closed = False
        def control_op(self, op, args, payload=b""):
            self.calls.append((op, args, payload)); return {"accepted": True}
        def close(self): self.closed = True
    client = Client()
    worker = RfStatusWorker(client)
    args = {
        "waveform": "digital", "mode": "QPSK", "filter": "RRC",
        "rolloff": "0.35", "seed": 1, "symbol_rate_hz": 2_000_000,
        "sample_rate_hz": 60_000_000, "amplitude": 1500,
    }
    result = worker.control("load", args=args)
    assert result["ok"] and result["args"] == args
    assert client.calls[0][0] == "tx.load"
    assert client.calls[0][1]["samples"] == 3810
    assert len(client.calls[0][2]) == 3810 * 4
    worker.stop()
    assert client.closed
