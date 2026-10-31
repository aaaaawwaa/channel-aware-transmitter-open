"""Capture timing is optional, frame-bound evidence, never a control retry."""
import copy
import json
from dataclasses import replace

import pytest

from test_rf_rx_client import (LOAD_META, LOAD_PAYLOAD, NORMAL, PHYSICAL,
                               RxDaemon, armed_client)
from rf_status_client import StatusProtocolError


TIMES = ("tx_on_begin_ns", "tx_on_end_ns", "rx_arm_begin_ns", "rx_arm_end_ns",
         "drain_begin_ns", "drain_end_ns", "tx_off_begin_ns", "tx_off_end_ns")
SCOPE = "monotonic_api_brackets_not_sample_edges"
ABSENT = object()


class TimingDaemon(RxDaemon):
    """Real TCP daemon with independent hello/info versions and optional traces."""
    def __init__(self):
        self.hello_minor = 4
        self.info_major = 1
        self.info_minor = 4
        self.trace = "automatic"
        self.reason = None
        self.info_overrides = {}
        self.observation_fault = None
        super().__init__()

    def _send(self, conn, data):
        req = self.seen[-1][1]
        if req["op"] == "protocol.hello":
            reply = json.loads(data)
            reply["result"]["daemon"] = "1.0.2"
            if self.hello_minor is ABSENT:
                reply["result"].pop("protocol_minor")
            else:
                reply["result"]["protocol_minor"] = self.hello_minor
            data = (json.dumps(reply) + "\n").encode()
        elif req["op"] == "device.info" and req["args"].get("observe_timing") is True:
            if self.observation_fault == "disconnect":
                conn.shutdown(2)
                return
            if self.observation_fault == "malformed":
                data = b"invalid-json\n"
            elif self.observation_fault == "bad_id":
                reply = json.loads(data)
                reply["id"] += 1
                data = (json.dumps(reply) + "\n").encode()
        super()._send(conn, data)

    def _info_result(self):
        info = dict(super()._info_result(), daemon="1.0.2",
                    protocol_major=self.info_major, protocol_minor=self.info_minor)
        req = self.seen[-1][1]
        if req["args"].get("observe_timing") is True:
            if self.trace is not ABSENT:
                info["last_capture_timing"] = self.expected_trace() if self.trace == "automatic" else copy.deepcopy(self.trace)
            if self.reason is not None:
                info["capture_timing_unavailable"] = self.reason
            info.update(self.info_overrides)
        return info

    def expected_trace(self):
        request = self.capture_request
        wait = request["args"].get("settle_wait_us", 0)
        trace = dict(available=True, scope=SCOPE, capture_request_id=request["id"],
                     capture_timestamp_ns=123456789, samples=request["args"]["samples"],
                     ratio=request["args"]["ratio"],
                     **dict(zip(TIMES, range(100, 108))))
        # A real minor-4 daemon never reports the settling wait at all, so the
        # double must not invent the field on a minor-4 connection.
        if self.info_minor >= 5:
            trace["settle_wait_requested_us"] = wait
            if wait:
                # Inside the TX-on..RX-arm gap: tx_on_end is 101, rx_arm_begin 102.
                trace["settle_begin_ns"] = 101
                trace["settle_end_ns"] = 102
        return trace


@pytest.fixture
def daemon():
    d = TimingDaemon()
    yield d
    d.close()


def loaded(daemon):
    client = armed_client(daemon)
    client.control_op("tx.load", LOAD_META, LOAD_PAYLOAD)
    return client


def captured(daemon, instrumented=False, settle_wait_us=0):
    client = loaded(daemon)
    args = dict(PHYSICAL)
    if instrumented:
        args["observe_timing"] = True
    if settle_wait_us:
        args["settle_wait_us"] = settle_wait_us
    return client, client.capture(args, physical=True)


def state(client):
    return (client._session_socket, client._snapshot, client.latest_frame(),
            client._config_revision, client._capture_sequence,
            client._connection_generation, client._tx_session_ready,
            client._rx_session_ready)


def test_instrumented_capture_and_readonly_trace_use_same_framed_connection(daemon):
    daemon.chunk = 7
    client, frame = captured(daemon, instrumented=True)
    assert daemon.capture_request["args"] == dict(PHYSICAL, observe_timing=True)
    assert client._protocol_minor_seen == 4
    before = state(client)
    start = len(daemon.seen)
    result = client.observe_capture_timing(frame)
    assert result == dict(release_id=frame.release_id, daemon="1.0.2",
                          protocol_major=1, protocol_minor=4,
                          last_capture_timing=daemon.expected_trace(), reason=None)
    requests = daemon.seen[start:]
    assert len(requests) == 1
    assert requests[0][0] == daemon.seen[-2][0]
    assert requests[0][1]["op"] == "device.info"
    assert requests[0][1]["args"] == {"observe_timing": True}
    assert state(client) == before
    result["last_capture_timing"]["tx_on_begin_ns"] = 999
    assert client.observe_capture_timing(frame)["last_capture_timing"] == daemon.expected_trace()
    client.close()
    assert client._protocol_minor_seen is None


@pytest.mark.parametrize("minor", [0, 2, 3, ABSENT, None, True, "4", -1])
def test_old_or_unproven_hello_refuses_before_any_capture_request_and_keeps_frame(daemon, minor):
    daemon.hello_minor = minor
    # An info reply claiming minor 4 must not overrule this connection's hello.
    client, frame = captured(daemon)
    before = state(client)
    start = len(daemon.seen)
    with pytest.raises(StatusProtocolError, match="timing"):
        client.capture(dict(PHYSICAL, observe_timing=True), physical=True)
    assert daemon.seen[start:] == []
    assert state(client) == before and client.latest_frame() is frame
    client.close()


@pytest.mark.parametrize("minor", [ABSENT, True, "4", -1, 3, 4])
def test_default_poll_and_both_capture_paths_keep_legacy_wire(daemon, minor):
    daemon.hello_minor = minor
    client = loaded(daemon)
    client.capture(NORMAL)
    assert daemon.capture_request["args"] == NORMAL
    client.capture(PHYSICAL, physical=True)
    assert daemon.capture_request["args"] == PHYSICAL
    assert client.poll().online
    assert all("observe_timing" not in req["args"] for _, req in daemon.seen)
    client.close()


def test_false_flag_is_physical_only_and_does_not_require_new_hello(daemon):
    daemon.hello_minor = 3
    client = loaded(daemon)
    frame = client.capture(dict(PHYSICAL, observe_timing=False), physical=True)
    assert frame.physical and daemon.capture_request["args"]["observe_timing"] is False
    client.close()


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_capture_flag_must_be_boolean_before_any_request(daemon, value):
    client, frame = captured(daemon)
    before, start = state(client), len(daemon.seen)
    with pytest.raises(StatusProtocolError):
        client.capture(dict(PHYSICAL, observe_timing=value), physical=True)
    assert state(client) == before and daemon.seen[start:] == []
    client.close()


@pytest.mark.parametrize("value", [False, True])
def test_ordinary_capture_rejects_even_boolean_flag_locally(daemon, value):
    client = loaded(daemon)
    before, start = state(client), len(daemon.seen)
    with pytest.raises(StatusProtocolError):
        client.capture(dict(NORMAL, observe_timing=value))
    assert state(client) == before and daemon.seen[start:] == []
    client.close()


@pytest.mark.parametrize("mismatch", ["copy", "ordinary", "op", "generation", "revision", "foreign", "none", "dropped"])
def test_observation_requires_current_real_physical_frame_without_request(daemon, mismatch):
    client, frame = captured(daemon)
    if mismatch == "copy":
        frame = replace(frame)
    elif mismatch == "ordinary":
        frame = client.capture(NORMAL)
    elif mismatch == "op":
        frame = replace(frame, op="rx.capture")
        client._publish_frame(frame)
    elif mismatch == "generation":
        client._connection_generation += 1
    elif mismatch == "revision":
        client._config_revision += 1
    elif mismatch == "foreign":
        frame = object()
    elif mismatch == "none":
        frame = None
    elif mismatch == "dropped":
        client.close()
    before, start = state(client), len(daemon.seen)
    with pytest.raises(StatusProtocolError):
        client.observe_capture_timing(frame)
    assert state(client) == before and daemon.seen[start:] == []
    client.close()


@pytest.mark.parametrize("minor,trace,reason,expected", [
    (3, ABSENT, None, "UNSUPPORTED"), (4, ABSENT, None, "UNSUPPORTED"),
    (4, None, "NOT_REQUESTED", "NOT_REQUESTED"),
    (4, None, "NO_CAPTURE_TIMING", "NO_CAPTURE_TIMING"),
    (4, None, "CLOCK_UNAVAILABLE", "CLOCK_UNAVAILABLE"),
])
def test_unavailable_diagnostic_is_explicit_and_preserves_frame(daemon, minor, trace, reason, expected):
    client, frame = captured(daemon)
    daemon.info_minor, daemon.trace, daemon.reason = minor, trace, reason
    before = state(client)
    result = client.observe_capture_timing(frame)
    assert result["last_capture_timing"] is None and result["reason"] == expected
    assert state(client) == before
    client.close()


BAD_TRACE_FIELDS = [(name, value) for name in TIMES for value in (True, 0, -1, 2 ** 64, 1.0, "100")]
BAD_TRACE_FIELDS += [
    ("available", False), ("available", 1), ("scope", "sample_edges"),
    ("capture_request_id", True), ("capture_request_id", 0), ("capture_request_id", 2 ** 64),
    ("capture_request_id", 9999), ("capture_timestamp_ns", 0),
    ("capture_timestamp_ns", True), ("capture_timestamp_ns", 2 ** 64),
    ("capture_timestamp_ns", 123456788), ("samples", True), ("samples", 0),
    ("samples", 4097), ("samples", 257), ("ratio", True), ("ratio", -1),
    ("ratio", 5), ("ratio", 1), ("tx_on_end_ns", 99),
    ("rx_arm_begin_ns", 100), ("drain_begin_ns", 102),
    ("tx_off_end_ns", 123456790),
]


@pytest.mark.parametrize("field,value", BAD_TRACE_FIELDS)
def test_malformed_or_mismatched_trace_preserves_complete_frame(daemon, field, value):
    client, frame = captured(daemon)
    daemon.trace = daemon.expected_trace()
    daemon.trace[field] = value
    before = state(client)
    with pytest.raises(StatusProtocolError):
        client.observe_capture_timing(frame)
    assert state(client) == before
    client.close()


@pytest.mark.parametrize("override", [
    {"release_id": "other"}, {"release_id": 2}, {"daemon": None},
    {"protocol_major": 2}, {"protocol_major": True},
    {"protocol_minor": True}, {"protocol_minor": "4"},
    {"last_capture_timing": []}, {"last_capture_timing": None},
    {"last_capture_timing": None, "capture_timing_unavailable": ""},
    {"last_capture_timing": None, "capture_timing_unavailable": 2},
    {"capture_timing_unavailable": "NO_CAPTURE_TIMING"},
])
def test_bad_optional_schema_does_not_drop_retained_session(daemon, override):
    client, frame = captured(daemon)
    daemon.info_overrides = override
    before = state(client)
    with pytest.raises(StatusProtocolError):
        client.observe_capture_timing(frame)
    assert state(client) == before
    client.close()


@pytest.mark.parametrize("field", TIMES + ("capture_request_id", "capture_timestamp_ns", "samples", "ratio", "available", "scope"))
def test_missing_trace_fields_rejected_after_full_json(daemon, field):
    client, frame = captured(daemon)
    daemon.trace = daemon.expected_trace()
    del daemon.trace[field]
    before = state(client)
    with pytest.raises(StatusProtocolError):
        client.observe_capture_timing(frame)
    assert state(client) == before
    client.close()


def test_equal_positive_brackets_at_uint64_limit_are_valid(daemon):
    daemon.header_overrides["timestamp_ns"] = 2 ** 64 - 1
    client, frame = captured(daemon)
    daemon.trace = daemon.expected_trace()
    daemon.trace.update({name: frame.daemon_timestamp_ns for name in TIMES})
    daemon.trace["capture_timestamp_ns"] = frame.daemon_timestamp_ns
    assert client.observe_capture_timing(frame)["last_capture_timing"] == daemon.trace
    client.close()


@pytest.mark.parametrize("failure", ["disconnect", "bad_id", "malformed"])
def test_observation_transport_or_framing_error_drops_without_retry(daemon, failure):
    client, frame = captured(daemon)
    daemon.observation_fault = failure
    connections, start = daemon.connections, len(daemon.seen)
    with pytest.raises(StatusProtocolError):
        client.observe_capture_timing(frame)
    assert len(daemon.seen[start:]) == 1 and daemon.connections == connections
    assert client.latest_frame() is None and client._session_socket is None
    assert client._protocol_minor_seen is None


# --------------------------------------------------------------- settling wait

SETTLE_MAX = 5000


def test_settle_wait_is_accepted_only_on_the_physical_path(daemon):
    client = loaded(daemon)
    start = len(daemon.seen)
    with pytest.raises(StatusProtocolError, match="unexpected capture argument"):
        client.capture(dict(NORMAL, settle_wait_us=1000))
    assert daemon.seen[start:] == []
    client.close()


@pytest.mark.parametrize("bad", [True, 1.5, "1000", None, [], {}])
def test_settle_wait_rejects_a_non_integer(daemon, bad):
    client = loaded(daemon)
    start = len(daemon.seen)
    with pytest.raises(StatusProtocolError, match="settle_wait_us"):
        client.capture(dict(PHYSICAL, observe_timing=True, settle_wait_us=bad),
                       physical=True)
    assert daemon.seen[start:] == []
    client.close()


@pytest.mark.parametrize("bad", [-1, SETTLE_MAX + 1, 1000000])
def test_settle_wait_rejects_an_out_of_range_value(daemon, bad):
    client = loaded(daemon)
    start = len(daemon.seen)
    with pytest.raises(StatusProtocolError, match="settle_wait_us"):
        client.capture(dict(PHYSICAL, observe_timing=True, settle_wait_us=bad),
                       physical=True)
    assert daemon.seen[start:] == []
    client.close()


def test_settle_wait_requires_observe_timing(daemon):
    """An unmeasured wait is not evidence, so it is refused rather than sent."""
    client = loaded(daemon)
    start = len(daemon.seen)
    with pytest.raises(StatusProtocolError, match="observe_timing"):
        client.capture(dict(PHYSICAL, settle_wait_us=1000), physical=True)
    assert daemon.seen[start:] == []
    client.close()


@pytest.mark.parametrize("wait", [0, 1, SETTLE_MAX])
def test_settle_wait_is_accepted_up_to_the_bound(daemon, wait):
    daemon.hello_minor = 5
    client = loaded(daemon)
    client.capture(dict(PHYSICAL, observe_timing=True, settle_wait_us=wait),
                   physical=True)
    assert daemon.capture_request["args"] == dict(
        PHYSICAL, observe_timing=True, settle_wait_us=wait)
    client.close()


@pytest.mark.parametrize("minor", [ABSENT, 3, 4])
def test_settle_wait_is_refused_before_send_on_an_old_daemon(daemon, minor):
    # The base frame is taken WITHOUT instrumentation: below minor 4 the timing
    # flag is itself refused, so it could not establish the frame under test.
    daemon.hello_minor = minor
    client, frame = captured(daemon)
    before = state(client)
    start = len(daemon.seen)
    with pytest.raises(StatusProtocolError, match="minor"):
        client.capture(dict(PHYSICAL, observe_timing=True, settle_wait_us=1000),
                       physical=True)
    assert daemon.seen[start:] == []
    assert state(client) == before and client.latest_frame() is frame
    client.close()


def test_a_minor_4_daemon_still_serves_the_base_trace(daemon):
    """The wait is minor 5; the trace itself is minor 4 and must keep working."""
    assert daemon.hello_minor == 4
    client, frame = captured(daemon, instrumented=True)
    trace = client.observe_capture_timing(frame)["last_capture_timing"]
    assert trace == daemon.expected_trace() and "settle_begin_ns" not in trace
    client.close()


def test_settle_trace_is_read_back_and_matches_the_request(daemon):
    daemon.hello_minor = 5
    daemon.info_minor = 5
    client, frame = captured(daemon, instrumented=True, settle_wait_us=1000)
    trace = client.observe_capture_timing(frame)["last_capture_timing"]
    assert trace["settle_wait_requested_us"] == 1000
    assert (trace["settle_begin_ns"], trace["settle_end_ns"]) == (101, 102)
    client.close()


@pytest.mark.parametrize("field,value", [
    ("settle_wait_requested_us", 0),
    ("settle_wait_requested_us", 2000),
    ("settle_begin_ns", 100),
    ("settle_begin_ns", 103),
    ("settle_end_ns", 100),
    ("settle_end_ns", 103),
])
def test_a_settle_trace_that_does_not_match_the_request_is_rejected(daemon, field, value):
    daemon.hello_minor = 5
    daemon.info_minor = 5
    client, frame = captured(daemon, instrumented=True, settle_wait_us=1000)
    daemon.trace = daemon.expected_trace()
    daemon.trace[field] = value
    before = state(client)
    with pytest.raises(StatusProtocolError):
        client.observe_capture_timing(frame)
    assert state(client) == before
    client.close()


def test_a_zero_wait_trace_must_not_report_settle_brackets(daemon):
    daemon.hello_minor = 5
    daemon.info_minor = 5
    client, frame = captured(daemon, instrumented=True)
    daemon.trace = daemon.expected_trace()
    daemon.trace["settle_begin_ns"] = 101
    with pytest.raises(StatusProtocolError, match="zero"):
        client.observe_capture_timing(frame)
    client.close()


def test_a_minor_4_trace_reporting_a_wait_is_refused(daemon):
    """The field is part of the minor-5 contract: a daemon that predates the
    feature must not invent it."""
    daemon.hello_minor = 5
    client, frame = captured(daemon, instrumented=True)
    daemon.info_minor = 4
    daemon.trace = daemon.expected_trace()
    daemon.trace["settle_wait_requested_us"] = 0
    before = state(client)
    with pytest.raises(StatusProtocolError, match="minor-4"):
        client.observe_capture_timing(frame)
    assert state(client) == before
    client.close()


def test_a_minor_5_trace_omitting_the_zero_wait_is_refused(daemon):
    """Otherwise "reported zero" and "silently omitted" are the same answer, and
    a daemon that dropped the field would look exactly like one that served a
    zero-wait frame."""
    daemon.hello_minor = 5
    daemon.info_minor = 5
    client, frame = captured(daemon, instrumented=True)
    daemon.trace = daemon.expected_trace()
    del daemon.trace["settle_wait_requested_us"]
    with pytest.raises(StatusProtocolError, match="must report"):
        client.observe_capture_timing(frame)
    client.close()


def test_a_waiting_frame_that_a_minor_4_daemon_cannot_describe_is_refused(daemon):
    """The wait was sent on a minor-5 hello, so a reply with no wait field is a
    mismatch, not a pass."""
    daemon.hello_minor = 5
    client, frame = captured(daemon, instrumented=True, settle_wait_us=1000)
    daemon.info_minor = 4
    with pytest.raises(StatusProtocolError):
        client.observe_capture_timing(frame)
    client.close()
