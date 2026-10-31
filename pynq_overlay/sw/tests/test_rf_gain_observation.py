"""Explicit raw gain observations must not become capture or control evidence."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_status_client as module
from rf_status_client import READ_ONLY_OPS, RfStatusClient, RfStatusSnapshot, StatusProtocolError


GAIN = {
    "available": True, "source": "hardware", "scope": "sequential_reads_not_atomic",
    "started_at_monotonic_ns": 100, "finished_at_monotonic_ns": 120,
    "rx_enable_raw": 0x40, "agc_config1_raw": 2, "agc_config2_raw": 8,
    "mode_rx1": 2, "gain_index_before": 42, "gain_index_after": 43,
    "digital_gain_code": 0, "index_stable": False,
}
IDENTITY = {"release_id": "observation-release", "daemon": "1.0.1",
            "protocol_major": 1, "protocol_minor": 3}
PL = {"sampled_at_monotonic_ns": 1, "io_ok": True,
      "tx": {"source_selected": False, "length_words": 0, "position_words": 0},
      "rx": {"armed": False, "done": False, "position_words": 0}}


class ScriptedSocket:
    """Exercises the real request framing without touching a board or network."""
    def __init__(self, info):
        self.info = info
        self.requests = []
        self.pending = b""
        self.closed = False
        self.bad_id = False
        self.disconnect = False

    def settimeout(self, timeout):
        assert timeout > 0

    def sendall(self, data):
        assert not self.closed
        request = json.loads(data)
        self.requests.append(request)
        op = request["op"]
        if op == "protocol.hello":
            result = {"daemon": "1.0.1", "protocol_major": 1, "protocol_minor": 3}
        elif op == "device.info":
            result = self.info
        elif op in ("state.get", "status.subscribe"):
            result = {"state": "READY", "pending": False, "pending_op": None,
                      "lease_held": False, "subscribed": True}
        elif op == "pl.status.get":
            result = PL
        else:
            pytest.fail("unexpected control/capture operation: " + op)
        reply = {"id": request["id"] + int(self.bad_id), "ok": True, "result": result}
        self.pending += (json.dumps(reply) + "\n").encode("ascii")

    def recv(self, count):
        if self.disconnect:
            return b""
        # Split headers to exercise bounded accumulation as well as serialization.
        data, self.pending = self.pending[:17], self.pending[17:]
        return data

    def close(self):
        self.closed = True


def fixture(monkeypatch, gain=GAIN, reason=None, retained=False):
    info = dict(IDENTITY, mode="status_only", core=True,
                capabilities={"ops": list(READ_ONLY_OPS)}, armed=True)
    if gain != "absent":
        info["gain_observation"] = copy.deepcopy(gain)
    if reason is not None:
        info["gain_observation_unavailable"] = reason
    sock = ScriptedSocket(info)
    calls = []

    def connect(address, timeout):
        calls.append(address)
        return sock

    monkeypatch.setattr(module.socket, "create_connection", connect)
    client = RfStatusClient()
    client._config_revision = 19
    client._capture_sequence = 7
    client._frame = object()
    if retained:
        client._session_socket = sock
        client._connection_generation = 4
        client._rx_session_ready = True
        client._tx_session_ready = True
        client._snapshot = RfStatusSnapshot(online=True, release_id="frame-release",
                                            rx_session_ready=True, tx_session_ready=True)
    return client, sock, calls


def retained_state(client):
    return (client._session_socket, client._snapshot, client.latest_frame(),
            client._config_revision, client._capture_sequence, client._connection_generation,
            client._rx_session_ready, client._tx_session_ready)


def test_explicit_read_preserves_retained_capture_and_returns_independent_result(monkeypatch):
    client, sock, calls = fixture(monkeypatch, retained=True)
    before = retained_state(client)
    result = client.observe_rx_gain()
    assert result == dict(IDENTITY, gain_observation=GAIN, reason=None)
    assert sock.requests == [{"id": 1, "op": "device.info", "args": {"observe_rx_gain": True}}]
    assert calls == [] and not sock.closed
    assert retained_state(client) == before
    result["gain_observation"]["gain_index_after"] = 1
    assert sock.info["gain_observation"] == GAIN
    assert retained_state(client) == before


def test_unretained_explicit_request_has_only_handshake_and_info(monkeypatch):
    client, sock, calls = fixture(monkeypatch)
    assert client.observe_rx_gain()["gain_observation"] == GAIN
    assert [(r["op"], r["args"]) for r in sock.requests] == [
        ("protocol.hello", {"major": 1, "minor": 0}),
        ("device.info", {"observe_rx_gain": True})]
    assert len(calls) == 1 and sock.closed
    assert client._config_revision == 19 and client._capture_sequence == 7


def test_poll_and_handshake_do_not_request_gain(monkeypatch):
    client, sock, calls = fixture(monkeypatch, "absent")
    assert client.poll().online
    assert [r["op"] for r in sock.requests] == [
        "protocol.hello", "device.info", "state.get", "status.subscribe", "pl.status.get"]
    assert all("observe_rx_gain" not in r["args"] for r in sock.requests)
    assert len(calls) == 1


@pytest.mark.parametrize("gain,reason,expected", [
    ("absent", None, "UNSUPPORTED"), (None, "HARDWARE_IO", "HARDWARE_IO"),
    (None, "NOT_READY_OR_UNSUPPORTED", "NOT_READY_OR_UNSUPPORTED"),
])
def test_unavailable_and_legacy_are_explicit_without_zero_or_cached_values(monkeypatch, gain, reason, expected):
    client, sock, calls = fixture(monkeypatch, gain, reason, retained=True)
    before = retained_state(client)
    assert client.observe_rx_gain() == dict(IDENTITY, gain_observation=None, reason=expected)
    assert retained_state(client) == before and calls == []


@pytest.mark.parametrize("field,value", [
    ("available", 1), ("available", False), ("source", "cached"), ("scope", "atomic"),
    ("started_at_monotonic_ns", True), ("started_at_monotonic_ns", -1),
    ("finished_at_monotonic_ns", 2 ** 64), ("finished_at_monotonic_ns", 99),
    ("rx_enable_raw", 256), ("rx_enable_raw", False), ("rx_enable_raw", 0),
    ("agc_config1_raw", -1), ("agc_config1_raw", 3), ("agc_config2_raw", 0),
    ("agc_config2_raw", 256), ("mode_rx1", 4), ("mode_rx1", True),
    ("gain_index_before", -1), ("gain_index_before", 77),
    ("gain_index_after", 77), ("gain_index_after", 43.0),
    ("digital_gain_code", 32), ("digital_gain_code", True),
    ("index_stable", True), ("index_stable", 0),
])
def test_forged_gain_is_rejected_without_losing_retained_evidence(monkeypatch, field, value):
    gain = dict(GAIN)
    gain[field] = value
    client, sock, calls = fixture(monkeypatch, gain, retained=True)
    before = retained_state(client)
    with pytest.raises(StatusProtocolError):
        client.observe_rx_gain()
    assert retained_state(client) == before and not sock.closed and calls == []


@pytest.mark.parametrize("gain,reason", [(None, None), (None, ""), (None, 3), ([], None),
                                          (GAIN, "HARDWARE_IO")])
def test_malformed_or_contradictory_availability_is_rejected(monkeypatch, gain, reason):
    client, sock, _ = fixture(monkeypatch, gain, reason, retained=True)
    before = retained_state(client)
    with pytest.raises(StatusProtocolError):
        client.observe_rx_gain()
    assert retained_state(client) == before


def test_stable_boundary_values_are_valid(monkeypatch):
    gain = dict(GAIN, rx_enable_raw=255, agc_config1_raw=255, agc_config2_raw=255,
                mode_rx1=3, gain_index_before=76, gain_index_after=76,
                digital_gain_code=31, index_stable=True,
                started_at_monotonic_ns=2 ** 64 - 1, finished_at_monotonic_ns=2 ** 64 - 1)
    client, _, _ = fixture(monkeypatch, gain)
    assert client.observe_rx_gain()["gain_observation"] == gain


@pytest.mark.parametrize("failure", ["bad_id", "disconnect"])
def test_transport_or_framing_failure_drops_unsafe_session_without_retry(monkeypatch, failure):
    client, sock, calls = fixture(monkeypatch, retained=True)
    setattr(sock, failure, True)
    with pytest.raises(StatusProtocolError):
        client.observe_rx_gain()
    assert calls == [] and len(sock.requests) == 1
    assert sock.closed and client.latest_frame() is None
    assert client._session_socket is None and client._config_revision == 19
