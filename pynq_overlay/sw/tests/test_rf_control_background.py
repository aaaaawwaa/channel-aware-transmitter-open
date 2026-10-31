"""Long RF operations must not hold the GUI or turn polling timeout into success."""
import threading
import time
import test_ui_view as H
import pynq_status as daemon
from rf_status_worker import RfStatusWorker
from rf_status_client import RfStatusClient, RfStatusSnapshot


class SlowClient:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = []

    def poll(self):
        return RfStatusSnapshot(online=True, armed=True, mode="status_only", core=True,
                                daemon_state="READY", control_ops=H._ARMED_OPS,
                                received_at_monotonic=time.monotonic())

    def control_op(self, op, args):
        self.calls.append((op, dict(args)))
        if op == "rf.init":
            self.entered.set()
            assert self.release.wait(2)


def test_background_control_returns_before_hardware_and_queues_only_one_stop():
    client = SlowClient()
    worker = RfStatusWorker(client)
    params = dict(lo_hz=80000000)
    started = time.monotonic()
    assert worker.submit_control("init", args=params)["queued"]
    assert time.monotonic()-started < 0.1
    assert client.entered.wait(1)
    try:
        params["lo_hz"] = 1
        assert worker.control_status() == "init"
        assert worker.take_control_results() == []
        assert worker.submit_control("configure", args=params)["code"] == "BUSY"
        assert worker.submit_control("stop")["queued"]
        assert worker.submit_control("stop")["queued"]  # duplicate must not send twice
    finally:
        client.release.set()
        worker.stop()
    results = worker.take_control_results()
    assert [a for a, r in results] == ["init", "stop"]
    assert all(r["ok"] for a, r in results)
    assert client.calls == [("rf.init", dict(lo_hz=80000000)), ("tx.stop", {})]
    assert worker.control_status() is None
    assert worker.take_control_results() == []


def test_long_control_deadline_is_separate_from_poll_deadline():
    client = RfStatusClient(timeout=0.04, control_timeout=0.3)
    assert client.timeout == 0.04 and client.control_timeout == 0.3


def test_local_inflight_control_keeps_stop_when_status_expires():
    app = H.M.UiApp()
    app.set_rf_status(H._rf_control_snapshot(armed=True, ops=H._ARMED_OPS))
    app.set_rf_control_pending("init")
    expired = H._rf_control_snapshot(armed=True, ops=H._ARMED_OPS, stale=True)
    app.set_rf_status(expired)
    assert app.rf_status["stale"] and not app.rf_status["pl_status"]["io_ok"]
    assert "stop" in app.rf_control["actions"] and app.rf_control["stop_reachable"]
    assert app.rf_control_block_reason("stop") is None
    assert app.rf_control_block_reason("init") is not None
    app.set_rf_control_result("init", dict(ok=False, code="TIMEOUT", message="unknown outcome"))
    assert app.rf_control["inflight_action"] is None
    assert app.rf_control["actions"] == ()
