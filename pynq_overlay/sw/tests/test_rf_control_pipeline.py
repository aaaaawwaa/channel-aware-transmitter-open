"""Production touch callback and loopback deadlines under a slow RF operation."""
import json
import socket
import threading
import time

import pytest
import test_ui_view as H
import test_rf_status_client as C
from test_rf_control_background import SlowClient
from test_rf_touch_parameters import PARAMS
import pynq_status as daemon
from rf_status_client import RfStatusClient
from rf_status_worker import RfStatusWorker


def test_touch_callback_stays_responsive_and_lands_only_completed_results():
    client, app = SlowClient(), H.M.UiApp()
    app.select_device("AD9361")
    worker = RfStatusWorker(client)
    app.set_rf_status(client.poll().to_dict())
    for name, value in PARAMS.items():
        app.set_rf_parameter(name, value)
    app.page, app.tx_subpage = H.M.PAGE_TX, "parameters"
    lcd = H.FakeLcd()
    view = H.V.UiView(lcd, app, on_rf_control=lambda action: daemon._rf_control_action(app, worker, action))
    screen = daemon._PanelScreen(view, None, None, None, worker)
    try:
        view.render()
        init = next(btn for btn in view.rf_action_btns if btn.visible and btn.text == "初始化")
        H.tap_at(view, *H.center(init))
        assert client.calls == []
        started = time.monotonic()
        H.tap_at(view, *H.center(view.rf_confirm_btn))
        assert time.monotonic()-started < 0.2 and client.entered.wait(1)
        assert app.rf_control["state"] == "armed"
        assert app.rf_control["inflight_action"] == "init"
        screen.render(daemon.Status())
        stop = next(btn for slot, btn in zip(view.rf_action_slots, view.rf_action_btns)
                    if slot["action"] == "stop")
        H.tap_at(view, *H.center(stop))
        assert client.calls[0] == ("rf.init", {key: PARAMS[key] for key in H.M.RF_ACTION_PARAMS["init"]})
        assert len(client.calls) == 1 and app.rf_control["state"] == "armed"
        # The modal keypad must not cover STOP during native initialization.
        H.tap_at(view, *H.center(view.rf_parameter_rows["lo_hz"]))
        assert not view.keypad.visible
        assert not view.rf_parameter_rows["lo_hz"].enabled
        assert view.ui._hit(*H.center(stop)) is stop
        client.release.set()
        worker.stop()
        screen.render(daemon.Status())
        assert app.rf_control["state"] == "stopped"
        assert app.rf_control["inflight_action"] is None
        assert not app.rf_control["last_error"]
        assert client.calls[-1] == ("tx.stop", {})
    finally:
        client.release.set()
        worker.stop()


def test_panel_takes_snapshot_and_control_completion_from_one_frame(monkeypatch):
    client = SlowClient()
    worker = RfStatusWorker(client)
    app, lcd, view = H.view(H.M.PAGE_RX)
    app.set_rf_status(client.poll().to_dict())
    app.set_rf_control_pending("init")
    app.set_rf_status(client.poll().expired().to_dict())
    worker._latest = client.poll()
    worker._control_results = [("init", {"ok": True})]
    # Separate legacy reads reproduce a stale observation paired with new completion.
    monkeypatch.setattr(worker, "latest", lambda: client.poll().expired())
    screen = daemon._PanelScreen(view, None, None, None, worker)
    screen.render(daemon.Status())
    assert app.rf_control["state"] == "initialized"
    assert "configure" in app.rf_control["actions"]
    assert app.rf_control["inflight_action"] is None
    assert worker.take_control_results() == []


def test_worker_polls_and_publishes_in_sample_order():
    first, release, second = threading.Event(), threading.Event(), threading.Event()
    class Client:
        count = 0
        def poll(self):
            self.count += 1
            n = self.count
            if n == 1:
                first.set()
                assert release.wait(2)
            else:
                second.set()
            return C.RfStatusClient()._snapshot.__class__(
                online=True, release_id=str(n), received_at_monotonic=time.monotonic())
    worker = RfStatusWorker(Client())
    threads = [threading.Thread(target=worker._poll_once) for _ in range(2)]
    threads[0].start()
    assert first.wait(1)
    threads[1].start()
    try:
        assert not second.wait(0.1), "a later poll must not overtake publication of an earlier one"
    finally:
        release.set()
        for thread in threads:
            thread.join(2)
    assert second.is_set()
    assert worker.latest().release_id == "2"


@pytest.mark.parametrize("op", ("rf.init", "rf.configure"))
def test_slow_loopback_control_uses_long_deadline_and_waiting_poll_gets_own_deadline(op):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(3)
    listener.settimeout(2)
    entered, seen, errors = threading.Event(), [], []
    def serve():
        try:
            for phase in ("poll", "control", "poll"):
                conn, _ = listener.accept()
                with conn:
                    stream = conn.makefile("rb")
                    for i in range(2 if phase == "control" else 5):
                        req = json.loads(stream.readline())
                        seen.append(req["op"])
                        if phase == "control" and i == 1:
                            assert req["op"] == op
                            entered.set()
                            time.sleep(0.15)  # Longer than the poll deadline.
                            result = {"state": "READY"}
                        else:
                            result = dict(C.RESULTS[i])
                            if i == 1:
                                result.update(core=True, armed=True, capabilities={
                                    "ops": list(C.CAPABILITIES), "control_ops": [op]})
                        conn.sendall((json.dumps({"id": req["id"], "ok": True, "result": result})+"\n").encode())
        except Exception as exc:
            errors.append(exc)
        finally:
            listener.close()
    server = threading.Thread(target=serve, daemon=True)
    server.start()
    client = RfStatusClient(port=listener.getsockname()[1], timeout=0.1, control_timeout=0.6)
    assert client.poll().online
    results = []
    control = threading.Thread(target=lambda: results.append(client.control_op(op, {"lo_hz": 80000000})))
    control.start()
    assert entered.wait(1)
    try:
        assert client.poll().online
    finally:
        control.join(2)
        server.join(2)
    assert not errors
    assert results == [{"state": "READY"}]
    assert seen == list(C.OPS) + ["protocol.hello", op] + list(C.OPS)
