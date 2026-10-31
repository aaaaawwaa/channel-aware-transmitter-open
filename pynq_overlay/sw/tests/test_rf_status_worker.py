import sys
import threading
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rf_status_client import (PlStatus, RfStatusSnapshot, RxStatus,
                              StatusProtocolError, TxStatus)
from rf_status_worker import RfStatusWorker


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.called = threading.Event()

    def poll(self):
        self.calls += 1
        self.called.set()
        return RfStatusSnapshot(online=True, received_at_monotonic=time.monotonic(),
                                mode="status_only", daemon_state="OFF",
                                pl_status=PlStatus(123, True, TxStatus(True, 73, 4),
                                                   RxStatus(False, True, 8)))


def test_worker_is_nonblocking_immutable_and_clears_expired_registers():
    client = FakeClient()
    worker = RfStatusWorker(client, poll_interval=10, stale_after=0.08)
    start = time.monotonic()
    worker.start()
    assert time.monotonic() - start < 0.05
    assert client.called.wait(1)
    snap = worker.latest()
    assert snap.online and snap.pl_status.tx.length_words == 73
    try:
        snap.mode = "control_test"
    except FrozenInstanceError:
        pass
    else:
        assert False, "published snapshot must be immutable"
    plain = snap.to_dict()
    plain["pl_status"]["tx"]["length_words"] = 0
    assert worker.latest().pl_status.tx.length_words == 73
    time.sleep(0.11)
    stale = worker.latest()
    assert stale.online and stale.stale
    assert stale.pl_status.tx.length_words is None
    assert stale.pl_status.rx.position_words is None
    assert stale.daemon_state == "OFF"
    worker.stop()
    assert not worker.is_alive()


def test_stop_wakes_sleeping_worker():
    worker = RfStatusWorker(FakeClient(), poll_interval=100, stale_after=1)
    worker.start()
    worker.stop()
    assert not worker.is_alive()


class FakeControlClient:
    """A client that records control calls; `fail=True` makes them raise as the real
    client does, carrying the daemon's code on the exception."""

    def __init__(self, fail=False, code=None, message="refused"):
        self.arm_tokens = []
        self.disarm_calls = 0
        self.op_calls = []
        self.fail = fail
        self.code = code
        self.message = message

    def poll(self):
        return RfStatusSnapshot(online=True, received_at_monotonic=time.monotonic(),
                                mode="status_only")

    def control_arm(self, token):
        self.arm_tokens.append(token)
        if self.fail:
            raise StatusProtocolError("RF daemon refused control.arm", self.code)
        return {"armed": True}

    def control_disarm(self):
        self.disarm_calls += 1
        if self.fail:
            raise StatusProtocolError("RF daemon refused control.disarm", self.code)
        return {"armed": False}

    def control_op(self, op, args):
        self.op_calls.append((op, args))
        if self.fail:
            raise StatusProtocolError("RF daemon refused " + op, self.code)
        return {}


def test_control_arm_reads_the_token_from_the_operator_path_and_never_stores_it(tmp_path):
    """判据 5: 令牌由操作员在板上 provision; 面板只带路径、现读现用、绝不缓存/内置。"""
    token_file = tmp_path / "rf-control.token"
    token_file.write_text("s3cret\n", encoding="ascii")
    client = FakeControlClient()
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    result = worker.control("arm", str(token_file))
    assert result == {"ok": True, "code": None, "message": ""}
    assert client.arm_tokens == ["s3cret"], "训练好的换行必须去掉"
    assert "s3cret" not in repr(result)
    assert "s3cret" not in repr(vars(worker))
    assert not hasattr(worker, "token")


def test_control_arm_fails_closed_without_a_readable_token_file(tmp_path):
    """缺失/空/没给路径一律 fail-closed, 且**一个请求都不发** —— 绝不落到内置默认值。"""
    client = FakeControlClient()
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    missing = worker.control("arm", str(tmp_path / "absent.token"))
    assert missing["ok"] is False and missing["code"] == "TOKEN_UNAVAILABLE"
    empty_file = tmp_path / "empty.token"
    empty_file.write_text("  \n", encoding="ascii")
    empty = worker.control("arm", str(empty_file))
    assert empty["ok"] is False and empty["code"] == "TOKEN_UNAVAILABLE"
    no_path = worker.control("arm")
    assert no_path["ok"] is False and no_path["code"] == "TOKEN_UNAVAILABLE"
    assert client.arm_tokens == [], "没有可用令牌时不该发任何请求"


def test_control_disarm_reaches_the_client():
    client = FakeControlClient()
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    assert worker.control("disarm") == {"ok": True, "code": None, "message": ""}
    assert client.disarm_calls == 1


def test_control_failure_carries_the_daemons_code(tmp_path):
    """判据 6: 失败的码必须原样上浮, 才能显示 daemon 的码而不是泛化消息。"""
    token_file = tmp_path / "rf-control.token"
    token_file.write_text("s3cret", encoding="ascii")
    client = FakeControlClient(fail=True, code="PERMISSION")
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    result = worker.control("arm", str(token_file))
    assert result["ok"] is False
    assert result["code"] == "PERMISSION", result
    assert "control.arm" in result["message"]


def test_panel_does_not_send_init_or_configure_while_the_client_lacks_them():
    """init/configure 的面板门禁已就绪, 但当前客户端没有对应发送方法 ——
    诚实地回 NOT_WIRED, 而不是假装成功或私自绕过广告门。"""
    client = FakeControlClient()
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    for action in ("init", "configure"):
        result = worker.control(action)
        assert result["ok"] is False, action
        assert result["code"] == "NOT_WIRED", (action, result)
    assert client.arm_tokens == [] and client.disarm_calls == 0


def test_stop_is_wired_to_tx_stop_while_init_and_configure_still_send_nothing():
    """`stop` 不带参数, 所以它现在真的发得出去(判据 6: stop 必须始终可达)。

    `init` / `configure` 需要操作员的 LO / 速率 / 带宽 / 衰减, 而面板**没有任何参数输入
    控件**(已确认 ui_model.py / ui_view.py / pynq_status.py 里不存在 lo_hz /
    sample_rate_hz / attenuation / idelay 字面量)。拿默认值去配置一台真的 TX 硬件是
    危险的, 所以这两个动作继续诚实回 NOT_WIRED, 并且**一个请求都不发**。
    """
    client = FakeControlClient()
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    assert worker.control("stop") == {"ok": True, "code": None, "message": ""}
    assert client.op_calls == [("tx.stop", {})]
    before = list(client.op_calls)
    for action in ("init", "configure"):
        result = worker.control(action)
        assert result["ok"] is False, action
        assert result["code"] == "NOT_WIRED", (action, result)
    assert client.op_calls == before, "init/configure 不该发出任何请求"
    assert client.arm_tokens == [] and client.disarm_calls == 0

def test_init_and_configure_send_the_operators_arguments_when_supplied():
    """The worker builds no arguments and invents none: the caller supplies them, and
    without them the call refuses locally rather than sending an incomplete request the
    daemon would reject for a reason the operator cannot act on."""
    client = FakeControlClient()
    worker = RfStatusWorker(client, poll_interval=100, stale_after=1)
    init_args = {"lo_hz": 80000000, "sample_rate_hz": 60000000,
                 "attenuation_mdB": 40000}
    reply = worker.control("init", None, init_args)
    assert reply["ok"] is True and reply["code"] is None and reply["message"] == ""
    assert reply["args"] == init_args
    assert reply["result"] == {}
    assert client.op_calls == [("rf.init", init_args)]
    config_args = dict(init_args, rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000)
    assert worker.control("configure", None, config_args)["ok"] is True
    assert client.op_calls[-1] == ("rf.configure", config_args)
    before = list(client.op_calls)
    for action in ("init", "configure"):
        result = worker.control(action)
        assert result["ok"] is False and result["code"] == "NOT_WIRED", result
    assert client.op_calls == before, "a parameterless init/configure was sent anyway"
