"""Task 4: the background RX capture path -- arbitration, cancellation and handoff.

RED FIRST.  Before the implementation this file cannot even run: 'RfStatusWorker' has no
'submit_capture' / 'take_rx_result' / 'cancel_capture', 'rf_status_worker' does not import
the analysis module, and 'pynq_status' has no RX action path at all.  The criteria below
name exactly what the brief fixes:

  * the touch/draw thread SUBMITS and nothing else -- the socket round trip and the FFT
    both run on the worker's own capture thread, and the main loop only lands a frozen
    result and draws it;
  * 'ratio' is passed EXPLICITLY, because the daemon keeps its initialized ratio (1 =>
    decimation 2) when the argument is absent, so omitting it would silently halve Fs;
  * control and capture serialize on the worker (one active job), and STOP stays reachable;
  * cancellation is cooperative: the panel shows "cancelling" until the in-flight request
    returns or the client's 3 s capture deadline expires; close-and-readback then runs and
    the cancelled result does NOT land;
  * a close the readback cannot confirm is reported as UNCONFIRMED -- never "stopped";
  * a normal drain keeps its frozen samples; a failure discards them.
"""
import inspect
import math
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rf_rx_analysis as ANA
import rf_status_worker as W
from rf_rx_types import RxIqFrame, RxReadback
from rf_status_client import (PlStatus, RfStatusClient, RfStatusSnapshot,
                              RxStatus, StatusProtocolError, TxStatus)
from rf_status_worker import RfStatusWorker

import test_rf_rx_client as C

SAMPLES = 64
FS = 60_000_000
RELEASE = C.RELEASE
TONE_HZ = 1_875_000
CONTROLS = list(C.CONTROLS)


# --------------------------------------------------------------------------- payloads

def iq_payload(samples, tone_hz=TONE_HZ, rate=FS, amplitude=1000, clipped=False):
    """A deterministic two-tone-ish I/Q record, all inside signed 12-bit range."""
    out = bytearray()
    for n in range(samples):
        i = int(round(amplitude * math.cos(2.0 * math.pi * tone_hz * n / rate)))
        q = int(round(amplitude * math.sin(2.0 * math.pi * tone_hz * n / rate)))
        if clipped and n % 4 == 0:
            i = ANA.RX_IQ_MAX
        out += struct.pack("<hh", i, q)
    return bytes(out)


def make_frame(samples=SAMPLES, ratio=0, rate=FS, amplitude=1000, clipped=False,
               capture_id="rx-test-0-0-1", op="rx.capture", physical=False):
    payload = iq_payload(samples, rate=rate, amplitude=amplitude, clipped=clipped)
    return RxIqFrame(
        capture_id=capture_id, payload=payload, samples=samples, sample_rate_hz=rate,
        ratio=ratio, daemon_timestamp_ns=123456789, connection_generation=1,
        config_revision=0, request_id=3, release_id=RELEASE, op=op, physical=physical,
        pre_readback=RxReadback(True, False, False, 0, False, 1),
        post_readback=RxReadback(True, False, True, 0, False, 2),
        completed_at_monotonic=time.monotonic())


# ------------------------------------------------------------------- client boundary

class FakeCaptureClient:
    """The worker's client boundary with NO sockets.

    It carries the production client's own local validator, so the "ratio is required"
    rule is exercised against the real contract instead of a relaxed double, and the
    readback it reports after a close is scriptable.
    """

    _capture_args = staticmethod(RfStatusClient._capture_args)

    def __init__(self, samples=SAMPLES, rate=FS, blocked=True, error=None,
                 post_armed=False, post_source=False, io_ok=True, delay=0.0,
                 amplitude=1000, clipped=False, ratio=None):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.closed = 0
        self.polls = 0
        self.samples, self.rate, self.ratio = samples, rate, ratio
        self.error = error
        self.post_armed, self.post_source, self.io_ok = post_armed, post_source, io_ok
        self.delay = delay
        self.amplitude, self.clipped = amplitude, clipped

    def capture(self, args, physical=False):
        self.calls.append(("capture", dict(args), bool(physical)))
        self.entered.set()
        assert self.release.wait(5), "the test never released the fake capture"
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise StatusProtocolError(self.error["message"], self.error["code"])
        ratio = args["ratio"] if self.ratio is None else self.ratio
        return make_frame(samples=args["samples"], ratio=ratio, rate=self.rate,
                          amplitude=self.amplitude, clipped=self.clipped,
                          capture_id="rx-test-0-%d-%d" % (ratio, len(self.calls)),
                          op="loopback.physical.capture" if physical else "rx.capture",
                          physical=bool(physical))

    def poll(self):
        self.polls += 1
        return RfStatusSnapshot(
            online=True, received_at_monotonic=time.monotonic(), mode="status_only",
            core=True, armed=True, daemon_state="READY", release_id=RELEASE,
            control_ops=tuple(CONTROLS),
            pl_status=PlStatus(1000, self.io_ok, TxStatus(self.post_source, 0, 0),
                               RxStatus(self.post_armed, True, 0)))

    def close(self):
        self.closed += 1


def drain(worker, timeout=5.0):
    """Wait for the worker's capture thread to finish, then hand the result off."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not worker.is_capturing():
            break
        time.sleep(0.01)
    return worker.take_rx_result()


def test_cancel_during_analysis_discards_result_and_confirms_close(monkeypatch):
    client = FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100)
    entered, release = threading.Event(), threading.Event()
    original = W.summarize

    def delayed(frame):
        entered.set()
        assert release.wait(3)
        return original(frame)

    monkeypatch.setattr(W, "summarize", delayed)
    try:
        worker.submit_capture({"samples": SAMPLES, "ratio": 0})
        assert entered.wait(3)
        assert worker.cancel_capture()["state"] == "cancelling"
        release.set()
        payload, status = drain(worker)
        assert payload is None
        assert status["state"] == "cancelled" and status["close_confirmed"]
        assert client.closed == 1 and client.polls == 1
    finally:
        release.set()
        worker.stop()


def test_completed_publication_and_cancel_are_one_atomic_decision(monkeypatch):
    client = FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100)
    published, release = threading.Event(), threading.Event()
    original = worker._capture_once

    def hold_after_publication():
        original()
        published.set()
        assert release.wait(3)

    monkeypatch.setattr(worker, "_capture_once", hold_after_publication)
    try:
        worker.submit_capture({"samples": SAMPLES, "ratio": 0})
        assert published.wait(3)
        # A finished publication is terminal; cancellation must not be accepted
        # after it while the thread is merely returning through its finally block.
        assert worker.cancel_capture()["state"] == "done"
        payload, status = worker.take_rx_result()
        assert payload is not None and status["state"] == "done"
    finally:
        release.set()
        worker.stop()


# ---------------------------------------------------------------- worker behaviours

def test_submission_returns_before_the_hardware_and_analysis_runs_off_this_thread():
    client = FakeCaptureClient(blocked=True)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    seen = []
    real = W.summarize

    def slow(frame):
        seen.append(threading.current_thread().name)
        return real(frame)

    W.summarize = slow
    try:
        started = time.monotonic()
        reply = worker.submit_capture({"samples": SAMPLES, "ratio": 0})
        assert time.monotonic() - started < 0.2
        assert reply.get("queued") is True
        assert client.entered.wait(2)
        # While the request is still in flight the drawing thread has nothing to draw and
        # no analysis has run anywhere.
        assert seen == []
        payload, status = worker.take_rx_result()
        assert payload is None and status["state"] == W.RX_CAPTURE_RUNNING
        assert worker.is_capturing() is True
        client.release.set()
        payload, status = drain(worker)
    finally:
        W.summarize = real
        client.release.set()
        worker.stop()
    assert status["state"] == W.RX_CAPTURE_DONE
    assert payload is not None
    # The FFT ran on the worker's capture thread, never on the caller's.
    assert seen and seen[0] != threading.current_thread().name
    assert seen[0].startswith("rf-rx-capture")


def test_the_capture_ratio_is_always_explicit_and_a_missing_one_sends_nothing():
    client = FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        refused = worker.submit_capture({"samples": SAMPLES})
        assert refused["ok"] is False
        assert refused["code"] == "INVALID_ARGUMENT", refused
        assert "ratio" in refused["message"]
        assert client.calls == [], "a refused submission must send nothing"
        assert worker.is_capturing() is False
        # The explicit selection code reaches the client unmodified.
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 2})["queued"]
        payload, _status = drain(worker)
    finally:
        client.release.set()
        worker.stop()
    assert client.calls == [("capture", {"samples": SAMPLES, "ratio": 2}, False)]
    assert payload is not None
    assert payload["frame"]["ratio"] == 2 and payload["frame"]["decimation"] == 4
    assert payload["frame"]["output_fs_hz"] == FS // 4


def test_a_completed_capture_hands_off_one_frozen_frame_and_its_analysis_exactly_once():
    client = FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        payload, status = drain(worker)
        assert worker.take_rx_result()[0] is None, "a handoff is taken exactly once"
        closed_by_drain = client.closed
    finally:
        client.release.set()
        worker.stop()
    assert status["state"] == W.RX_CAPTURE_DONE
    assert status["cancelled"] is False and status["close_confirmed"] is True
    frame, analysis = payload["frame"], payload["analysis"]
    assert frame["capture_id"] == "rx-test-0-0-1"
    assert frame["samples"] == SAMPLES and frame["bytes"] == SAMPLES * 4
    assert frame["post_rx_armed"] is False
    assert analysis.capture_id == frame["capture_id"]
    # The I/Q pair is cos + j*sin, so its single peak sits at +f0 on the two-sided axis.
    assert analysis.peak_hz == pytest.approx(TONE_HZ), analysis.peak_hz
    assert analysis.output_clipped is False
    # The worker did NOT close the retained control session on a normal drain.
    assert closed_by_drain == 0


def test_a_normal_drain_keeps_its_frozen_samples_and_a_reload_still_works():
    client = FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        first, _ = drain(worker)
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 1})["queued"]
        second, _ = drain(worker)
        closed_by_drain = client.closed
    finally:
        client.release.set()
        worker.stop()
    assert first["analysis"].capture_id != second["analysis"].capture_id
    assert first["frame"]["capture_id"] == "rx-test-0-0-1"
    assert second["frame"]["decimation"] == 2
    assert closed_by_drain == 0


def test_control_and_capture_serialize_and_the_captures_stop_stays_reachable():
    client = FakeCaptureClient(blocked=True, samples=8)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        assert client.entered.wait(2)
        # No control job may start while the capture owns the worker, and a queued tx.stop
        # would never be drained -- so it is refused rather than silently dropped.
        for action, args in (("configure", {"lo_hz": 80000000}), ("stop", None)):
            busy = worker.submit_control(action, None, args)
            assert busy["ok"] is False and busy["code"] == "BUSY", (action, busy)
        # The reachable STOP while a capture is in flight is the capture's own cancel.
        assert worker.cancel_capture()["state"] == W.RX_CAPTURE_CANCELLING
        worker.cancel_capture()
        client.release.set()
        payload, final = drain(worker)
        assert payload is None and final["state"] == W.RX_CAPTURE_CANCELLED
    finally:
        client.release.set()
        worker.stop()


def test_a_control_action_in_flight_refuses_a_new_capture_without_sending_anything():
    client = FakeCaptureClient(blocked=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    worker._control_running = "configure"          # a submitted op owns the worker
    try:
        refused = worker.submit_capture({"samples": SAMPLES, "ratio": 0})
    finally:
        worker._control_running = None
        worker.stop()
    assert refused["ok"] is False and refused["code"] == "BUSY", refused
    assert client.calls == []


def test_cancel_shows_cancelling_then_closes_reads_back_and_never_lands_the_result():
    client = FakeCaptureClient(blocked=True)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        assert client.entered.wait(2)
        status = worker.cancel_capture()
        assert status["state"] == W.RX_CAPTURE_CANCELLING, status
        assert "stop" not in status["state"]
        client.release.set()
        payload, final = drain(worker)
        closed_by_cancel, polls = client.closed, client.polls
    finally:
        client.release.set()
        worker.stop()
    assert payload is None, "a cancelled capture must not land a result"
    assert final["state"] == W.RX_CAPTURE_CANCELLED
    assert final["close_confirmed"] is True
    assert closed_by_cancel == 1 and polls >= 1


def test_a_close_the_readback_cannot_confirm_is_never_reported_as_stopped():
    client = FakeCaptureClient(blocked=True, post_armed=True)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        assert client.entered.wait(2)
        worker.cancel_capture()
        client.release.set()
        payload, final = drain(worker)
    finally:
        client.release.set()
        worker.stop()
    assert payload is None
    assert final["close_confirmed"] is False
    assert final["state"] == W.RX_CAPTURE_CANCELLED
    assert final["state"] != "stopped"
    assert "关闭未确认" in final["reason"], final


def test_an_unreadable_readback_after_a_cancel_is_also_unconfirmed():
    client = FakeCaptureClient(blocked=True, io_ok=False)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        assert client.entered.wait(2)
        worker.cancel_capture()
        client.release.set()
        payload, final = drain(worker)
    finally:
        client.release.set()
        worker.stop()
    assert payload is None and final["close_confirmed"] is False
    assert "关闭未确认" in final["reason"], final


def test_a_refused_capture_does_not_land_and_still_closes_and_reads_back():
    client = FakeCaptureClient(blocked=False,
                               error={"code": "FAULT", "message": "capture faulted"})
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        payload, status = drain(worker)
        closed_by_failure = client.closed
    finally:
        client.release.set()
        worker.stop()
    assert payload is None
    assert status["state"] == W.RX_CAPTURE_ERROR
    assert status["code"] == "FAULT", status
    assert closed_by_failure == 1


def test_a_clipped_record_is_a_visible_quality_reason_not_a_refusal():
    client = FakeCaptureClient(blocked=False, amplitude=2047, clipped=True)
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        payload, status = drain(worker)
    finally:
        client.release.set()
        worker.stop()
    # A clipped record is a QUALITY reason, not a refusal: it lands, flagged.
    assert payload is not None
    assert payload["analysis"].output_clipped is True
    assert payload["analysis"].clipping_count > 0
    assert status["state"] == W.RX_CAPTURE_DONE


def test_the_worker_reads_the_freshness_windows_from_the_analysis_module():
    assert W.IQ_SNAPSHOT_FRESHNESS_S is ANA.IQ_SNAPSHOT_FRESHNESS_S
    assert W.TELEMETRY_FRESHNESS_S is ANA.TELEMETRY_FRESHNESS_S
    assert W.snapshot_is_fresh is ANA.snapshot_is_fresh
    assert W.telemetry_is_fresh is ANA.telemetry_is_fresh
    default = inspect.signature(RfStatusWorker.__init__).parameters["stale_after"].default
    assert default == ANA.TELEMETRY_FRESHNESS_S


# ------------------------------------------------------- real client, real framing

def test_the_whole_path_from_a_real_socket_frame_to_a_frozen_analysis():
    """The production client over REAL TCP framing, driven by the worker's capture thread.

    The analysis must not be able to run before the frame is fully validated, and the
    explicit ratio must survive the wire.
    """
    daemon = C.RxDaemon()
    client = RfStatusClient(port=daemon.port, timeout=1.0)
    try:
        snapshot = client.poll()
        assert snapshot.error is None and "rx.capture" in snapshot.control_ops
        worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
        try:
            assert worker.submit_capture({"samples": C.SAMPLES, "ratio": 0})["queued"]
            payload, status = drain(worker, timeout=8)
        finally:
            worker.stop()
    finally:
        daemon.close()
    assert status["state"] == W.RX_CAPTURE_DONE, status
    assert "rx.capture" in daemon.ops()
    assert daemon.capture_request["args"] == {"samples": C.SAMPLES, "ratio": 0}
    frame, analysis = payload["frame"], payload["analysis"]
    assert frame["samples"] == C.SAMPLES and frame["sample_rate_hz"] == C.FS
    assert frame["release_id"] == RELEASE
    assert analysis.samples == C.SAMPLES and analysis.input_fs_hz == C.FS
    assert analysis.decimation == 1 and analysis.analysis_fs_hz == float(C.FS)
    assert analysis.spectrum_window == "hann"


def test_a_daemons_refusal_reaches_the_panel_with_its_own_code_and_no_frame():
    daemon = C.RxDaemon()
    client = RfStatusClient(port=daemon.port, timeout=1.0)
    try:
        assert client.poll().error is None
        daemon.capture_error = {"code": "FAULT", "message": "cleanup readback failed"}
        worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
        try:
            assert worker.submit_capture({"samples": C.SAMPLES, "ratio": 0})["queued"]
            payload, status = drain(worker, timeout=8)
        finally:
            worker.stop()
    finally:
        daemon.close()
    assert payload is None
    assert status["state"] == W.RX_CAPTURE_ERROR
    # The daemon's own code survives; the message is the client's, which names the op.
    assert status["code"] == "FAULT", status
    assert "rx.capture" in status["reason"]

# ------------------------------------------------- a crash can never wedge the worker

def test_a_capture_thread_that_escapes_its_own_error_handling_still_releases_the_job():
    """The request flag is what makes the worker BUSY; nothing may leave it set.

    `_close_and_readback` only guards OSError around `close()`, so any other exception used
    to escape the capture thread with `_capture_request` still set: the chip stays on
    "取消采集" forever, `submit_capture` answers BUSY forever and `submit_control` refuses
    `tx.stop` forever (a panel that cannot stop anything).
    """

    class BoomOnClose(FakeCaptureClient):
        """close() fails the way a non-OSError failure would inside the readback."""

        def close(self):
            self.closed += 1
            if self.closed == 1:
                raise RuntimeError("close blew up")  # NOT an OSError

    client = BoomOnClose(blocked=False,
                         error={"code": "FAULT", "message": "capture faulted"})
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    try:
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"]
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and worker.is_capturing():
            time.sleep(0.01)
        assert worker.is_capturing() is False, "the capture request was never released"
        # Not permanently BUSY: a new capture is accepted again.
        assert worker.submit_capture({"samples": SAMPLES, "ratio": 0})["queued"] is True
        drain(worker)
    finally:
        client.release.set()
        worker.stop()
    assert client.closed >= 1
