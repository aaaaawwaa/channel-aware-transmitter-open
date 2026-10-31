"""Fake-protocol tests for QAM64 control ordering and optional PL diagnostics.

No board, RF, install, production or runner change.  The fake models only the
contract under review: configure -> prepare_rx -> start_rx -> poll_rx/finish_rx,
a read-only ``pl.status.get`` that remains available in FAULT, and an optional
``diagnostics`` field whose missing/stale/failed values stay explicitly unknown.
"""
from __future__ import annotations

import time

import pytest

FAULT = {"code": "FAULT", "message": "core is faulted"}
CONTROL_OPS = ("rf.configure", "rf.init", "tx.load", "rx.capture",
               "loopback.digital.capture")


class FakeDaemon:
    """Minimal protocol/state fake; no hardware, no sleep, no retry."""
    def __init__(self):
        self.state = "READY"
        self.armed = True
        self.pl = {
            "io_ok": True,
            "tx": {"source_selected": False, "length_words": 2540,
                   "position_words": 0},
            "rx": {"armed": False, "done": False, "position_words": 0},
            "sampled_at_monotonic_ns": 123,
        }
        self.diag = dict(present=True, stale=False, read_error=None,
                         idelay_en=0x7F, idelay_tap=29)
        self.events = []
        self.request_seq = 0
        self.capture_buffer = "ORIGINAL_IQ"
        self.tx_count = 2540
        self.tx_loaded = True
        self.reload_count = 0

    def request(self, op, request_id, args=None):
        self.events.append((op, request_id))
        self.request_seq += 1
        if self.state == "FAULT" and op in CONTROL_OPS:
            return {"ok": False, "error": dict(FAULT)}
        if op == "pl.status.get":
            return {"ok": True, "result": self._pl_result()}
        if op == "rf.configure":
            # Full RF reconfiguration model: no implicit reload/retry.
            assert self.state == "READY"
            self.capture_buffer = "UNCHANGED_BY_CONFIGURE"
            return {"ok": True, "result": {"hardware_verified": True}}
        if op == "prepare_rx":
            assert self.state in ("READY", "RX_CAPTURING")
            self.state = "RX_PREPARED"
            return {"ok": True}
        if op == "start_rx":
            assert self.state == "RX_PREPARED"
            self.state = "RX_CAPTURING"
            return {"ok": True}
        if op == "poll_rx":
            assert self.state == "RX_CAPTURING"
            return {"ok": True, "result": {"done": True, "position": 4096}}
        if op == "finish_rx":
            assert self.state == "RX_CAPTURING"
            self.state = "READY"
            return {"ok": True, "result": {"frozen": self.capture_buffer}}
        raise AssertionError("unexpected op " + op)

    def _pl_result(self):
        diag = self.diag
        if not diag["present"]:
            d = dict(available=False, reason="MISSING", idelay_en=None,
                     idelay_tap=None)
        elif diag["stale"]:
            d = dict(available=False, reason="STALE", idelay_en=None,
                     idelay_tap=None)
        elif diag["read_error"] is not None:
            d = dict(available=False, reason="FAILED", idelay_en=None,
                     idelay_tap=None, error=diag["read_error"])
        else:
            d = dict(available=True, reason=None,
                     idelay_en=diag["idelay_en"], idelay_tap=diag["idelay_tap"])
        return dict(self.pl, diagnostics=d)


class FakeClient:
    def __init__(self, daemon):
        self.d = daemon
        self.next_id = 1
        self.capture_buffer = "ORIGINAL_IQ"

    def call(self, op, args=None):
        rid = self.next_id
        self.next_id += 1
        return self.d.request(op, rid, args)

    def configure(self):
        return self.call("rf.configure")

    def capture_order(self):
        before = self.capture_buffer
        self.call("prepare_rx")
        self.call("start_rx")
        self.call("poll_rx")
        self.call("finish_rx")
        assert self.capture_buffer == before
        return self.d.events

    def pl_status_get(self):
        return self.call("pl.status.get")


@pytest.fixture(autouse=True)
def no_host_sleep(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("control/protocol path must not sleep on the host")
    monkeypatch.setattr(time, "sleep", forbidden)


def test_configure_prepare_start_poll_finish_order_without_extra_pl_request():
    d = FakeDaemon(); c = FakeClient(d)
    c.configure()
    c.capture_order()
    ops = [op for op, _ in d.events]
    assert ops == ["rf.configure", "prepare_rx", "start_rx", "poll_rx",
                   "finish_rx"]
    assert "pl.status.get" not in ops
    assert [rid for _, rid in d.events] == list(range(1, 6))
    assert d.capture_buffer == "UNCHANGED_BY_CONFIGURE"


def test_configure_does_not_auto_reload_or_retry():
    d = FakeDaemon(); c = FakeClient(d)
    c.configure()
    assert d.reload_count == 0
    assert [op for op, _ in d.events] == ["rf.configure"]
    assert d.tx_count == 2540 and d.tx_loaded is True


def test_fault_pl_status_is_still_described_but_control_is_rejected_raw():
    d = FakeDaemon(); c = FakeClient(d)
    d.state = "FAULT"
    assert c.pl_status_get()["ok"] is True
    assert "diagnostics" in c.pl_status_get()["result"]
    raw = c.configure()
    assert raw == {"ok": False, "error": dict(FAULT)}
    assert d.state == "FAULT"
    assert raw["error"]["code"] == "FAULT" and raw["error"]["message"]


@pytest.mark.parametrize("diag,reason", [
    (dict(present=False, stale=False, read_error=None), "MISSING"),
    (dict(present=True, stale=True, read_error=None), "STALE"),
    (dict(present=True, stale=False, read_error={"code": "TIMEOUT"}),
     "FAILED"),
])
def test_optional_pl_diagnostics_missing_stale_failed_are_unknown(diag, reason):
    d = FakeDaemon(); c = FakeClient(d)
    d.diag = dict(present=True, stale=False, read_error=None,
                  idelay_en=0x7F, idelay_tap=29)
    d.diag.update(diag)
    out = c.pl_status_get()["result"]["diagnostics"]
    assert out["available"] is False
    assert out["reason"] == reason
    assert out["idelay_en"] is None and out["idelay_tap"] is None
    if reason == "FAILED":
        assert out["error"] == {"code": "TIMEOUT"}


def test_optional_diagnostics_available_are_reported_not_guessed():
    d = FakeDaemon(); c = FakeClient(d)
    out = c.pl_status_get()["result"]["diagnostics"]
    assert out == dict(available=True, reason=None,
                       idelay_en=0x7F, idelay_tap=29)


def test_pl_status_does_not_replace_capture_buffer_or_skip_request_id():
    d = FakeDaemon(); c = FakeClient(d)
    c.configure()
    c.capture_order()
    assert d.capture_buffer == "UNCHANGED_BY_CONFIGURE"
    before = c.next_id
    out = c.pl_status_get()
    assert out["ok"] is True
    assert d.capture_buffer == "UNCHANGED_BY_CONFIGURE"
    assert c.next_id == before + 1
    assert d.events[-1][0] == "pl.status.get"


def test_fault_does_not_prevent_reading_pl_but_does_not_recover_state():
    d = FakeDaemon(); c = FakeClient(d)
    d.state = "FAULT"
    assert c.pl_status_get()["result"]["diagnostics"]["available"] is True
    assert d.state == "FAULT"
    for op in CONTROL_OPS:
        before = d.state
        raw = d.request(op, 99, {})
        assert raw["ok"] is False and raw["error"]["code"] == "FAULT"
        assert d.state == before == "FAULT"


def test_raw_error_code_and_message_are_not_rewritten():
    d = FakeDaemon(); c = FakeClient(d)
    d.state = "FAULT"
    raw = c.call("rx.capture")
    assert raw["error"] == {"code": "FAULT",
                            "message": "core is faulted"}
    assert raw["error"]["code"] == "FAULT"
    assert raw["error"]["message"] == "core is faulted"
