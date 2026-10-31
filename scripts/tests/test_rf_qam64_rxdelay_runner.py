"""Fake-chain tests for the complete RX_DATA_DELAY runner orchestration.

These tests call the real `run()` entry point.  A fake session/frame/post-readback
stands in for the board; no network, no service, no RF.  The candidate setter's
real local parameter validation is exercised directly for the list bug.
"""
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "pynq_overlay" / "sw"))
import rf_qam64_rxdelay_runner as R
import rf_status_client


def regs(v=4):
    return {"0x006": v, "0x007": 112, "0x010": 200, "0x011": 0, "0x012": 16}


def status(v=4):
    return {"valid": True, "registers": regs(v),
            "fields": {"rx_data_delay": v, "data_clk_delay": 0,
                       "tx_data_delay": 0, "fb_clk_delay": 7},
            "decoded": {"lvds_mode": True, "single_data_rate": False,
                        "half_duplex_mode": False, "single_port_mode": False,
                        "full_port": False, "swap_ports": False,
                        "fdd_alt_word_order": False, "fdd_rx_rate_2tx_rate": False}}


def pl(v=4, tap=29):
    return {"safe": True, "io_ok": True,
            "tx": {"source_selected": False, "length_words": 2540, "position_words": 0},
            "rx": {"armed": False, "done": False, "position_words": 0},
            "diagnostics": {"idelay_en": {"raw": 127, "valid": True},
                            "idelay_tap": {"raw": tap, "valid": True}}}


class Frame:
    def __init__(self, n, tag):
        self.payload = bytes([tag % 251]) * 16384
        self.op = "loopback.digital.capture"
        self.physical = False
        self.capture_id = "fake-%d" % n
        self.samples = 4096
        self.sample_rate_hz = 60000000
        self.ratio = 0
        self.daemon_timestamp_ns = n
        self.connection_generation = 1
        self.config_revision = 1
        self.request_id = n
        self.release_id = R.CANDIDATE
        self.post_readback = types.SimpleNamespace(rx_armed=False, tx_source_selected=False)
        self.pre_readback = types.SimpleNamespace(rx_armed=False, tx_source_selected=False)


class FakeClient:
    def __init__(self, owner, v=4):
        self.owner = owner
        self.v = v
        self.capture_timeout = 10.0
        self.calls = []
        self._send_and_read_json = lambda *a, **k: {}
        self.inner = self

    def control_op(self, op, args=None, payload=b""):
        self.calls.append((op, args or {}))
        if op == "rf.init": self.owner.init_count += 1
        if op == "rf.configure": self.owner.configure_count += 1
        if op == "tx.load": self.owner.load_count += 1
        return {"ok": True, "fake": op}

    def poll(self):
        return types.SimpleNamespace(to_dict=lambda: {
            "release_id": R.CANDIDATE, "safe": True, "armed": False, "core": False,
            "pl_status": pl(self.v)})

    def get_interface_status(self):
        if self.owner.interface_fail:
            raise RuntimeError("interface_read_failed")
        return status(self.v)

    def set_rx_data_delay(self, value):
        # Use the frozen candidate's real local validation for the list/type bug.
        if type(value) is not int or not 0 <= value <= 15:
            raise ValueError("RX data delay must be an integer in 0..15")
        self.owner.set_values.append(value)
        if self.owner.fail_set_value == value:
            raise rf_status_client.StatusProtocolError("daemon refused")
        if getattr(self.owner, "fail_restore_value", None) == value and value == 4:
            raise rf_status_client.StatusProtocolError("daemon refused restore")
        if self.owner.fail_after_write:
            self.owner.interface_fail = True
        self.v = value
        return {"changed": True, "after": {"rx_data_delay": value},
                "other_registers_unchanged": True}

    def set_idelay_lanes(self, mask, tap):
        self.owner.idelay_values.append((mask, tap))
        return {"idelay_en": 127, "idelay_tap": 29, "readback": {"idelay_en": 127, "idelay_tap": 29}}


class FakeSession:
    def __init__(self, expected, out, v=4):
        self.expected = expected
        self.out = pathlib.Path(out)
        self.init_count = self.configure_count = self.load_count = 0
        self.set_values = []
        self.idelay_values = []
        self.interface_fail = False
        self.fail_set_value = None
        self.fail_after_write = False
        self.quiet = True
        self.capture_fail = None
        self.capture_fail_after = None
        self.client = types.SimpleNamespace(
            client=FakeClient(self, v),
            control_op=lambda op, args=None, payload=b"": self.client.client.control_op(op, args),
            poll=lambda: self.client.client.poll(),
            get_interface_status=lambda: self._status(),
            set_rx_data_delay=lambda value: self._set(value),
            digital_capture=lambda args: self._capture())
        self.app = types.SimpleNamespace(set_rf_parameter=lambda name, value: None)
        self.initial_calls = 0
        self.setup_calls = 0
        self.cleanup_calls = 0
        self._tx_load_records = [{"payload_file": "tx-load.bin"}]
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "tx-load.bin").write_bytes(b"tx")

    def _status(self):
        if self.interface_fail:
            raise RuntimeError("interface_read_failed")
        return status(self.client.client.v)

    def _set(self, value):
        res = self.client.client.set_rx_data_delay(value)
        return res

    def _capture(self):
        made = len(getattr(self, "_captures", []))
        if self.capture_fail_after is not None and made >= self.capture_fail_after and self.capture_fail:
            exc = self.capture_fail
            self.capture_fail = None
            raise exc
        n = len(self.set_values) + made + 1
        self._captures = getattr(self, "_captures", []) + [n]
        return Frame(n, n)

    def initial(self):
        self.initial_calls += 1
        return {"safe": True, "release_id": self.expected}

    def setup(self, seed):
        self.setup_calls += 1
        self.client.control_op("rf.init", {})
        self.client.control_op("rf.configure", {})

    def cleanup(self):
        self.cleanup_calls += 1
        return {"safe": True, "disarm_ack": True}

    def close(self):
        pass


class FakeRecovery:
    instances = []

    def __init__(self, expected, out):
        self.expected = expected
        self.out = out
        self.prepared = False
        self.loads = 0
        self.captures = 0
        self.set_values = []
        self.closed = False
        FakeRecovery.instances.append(self)

    def prepare(self):
        self.prepared = True

    def read(self):
        return status(4)

    def set_original(self, value):
        self.set_values.append(value)

    def restore_fpga(self):
        pass

    def close(self):
        self.closed = True


def post_ok():
    return {"available": True, "release_id": R.CANDIDATE, "safe": True,
            "armed": False, "core": False, "mode": "status_only",
            "pl_status": pl(4)}


def run_fake(tmp_path, v=4, **kwargs):
    return R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt",
                 cable_confirmed=True,
                 session_factory=lambda e, o: FakeSession(e, o, v=v),
                 post_factory=post_ok,
                 compare_func=lambda tx, iq: {"fake": True},
                 recovery_factory=FakeRecovery, **kwargs)


def test_selector_all_0_to_15():
    for v in range(16):
        p1, p2 = R._select(v)
        assert type(p1) is int
        assert p2 is None or type(p2) is int
        if v == 0: assert (p1, p2) == (1, None)
        elif v == 15: assert (p1, p2) == (14, None)
        else: assert (p1, p2) == (v + 1, v - 1)


def test_real_candidate_setter_rejects_list():
    fake = object()
    with pytest.raises(ValueError, match="integer in 0..15"):
        rf_status_client.RfStatusClient.set_rx_data_delay(fake, [5])


def test_full_v4_fake_chain(tmp_path):
    s = run_fake(tmp_path, v=4)
    assert s["study_complete"] is True
    assert s["final_success"] is True
    assert [r["tag"] for r in s["rows"]] == ["A-original", "B-point1", "C-restored", "D-point2"]
    assert [r["value"] for r in s["records"] if r.get("stage") == "set"] == [5, 3]
    assert [r["value"] for r in s["records"] if r.get("stage") == "restore"] == [4, 4]
    assert s["chip_restored"] is True
    assert s["fpga_restored"] is True
    assert s["rf_safe"] is True
    assert s["counts"]["completed"]["main_sessions"] == 1


def test_boundaries_reduce_points(tmp_path):
    for v, expected_frames, expected_sets in ((0, 3, 1), (15, 3, 14)):
        s = run_fake(tmp_path / str(v), v=v)
        assert s["study_complete"] is True
        assert len(s["rows"]) == expected_frames
        assert [r["value"] for r in s["records"] if r.get("stage") == "set"] == [expected_sets]
        assert [r["value"] for r in s["records"] if r.get("stage") == "restore"] == [v]


def test_original_read_failure_stops_before_write_or_capture(tmp_path):
    def factory(e, o):
        s = FakeSession(e, o, v=4)
        s.interface_fail = True
        return s
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=factory, post_factory=post_ok,
              compare_func=lambda tx, iq: {"fake": True})
    assert s["study_complete"] is False
    assert s["rows"] == []
    assert s["counts"]["completed"]["rx_delay_set"] == 0
    assert s["counts"]["completed"]["digital_captures"] == 0
    assert s["chip_restored"] is False


def test_point_set_failure_stops_b_and_reports_not_restored(tmp_path):
    def factory(e, o):
        s = FakeSession(e, o, v=4)
        s.fail_set_value = 5
        return s
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=factory, post_factory=post_ok,
              compare_func=lambda tx, iq: {"fake": True})
    assert s["study_complete"] is False
    assert [r["tag"] for r in s["rows"]] == ["A-original"]
    assert s["counts"]["completed"]["rx_delay_set"] == 0
    assert s["counts"]["attempted"]["rx_delay_set_protocol_reject"] == 1
    assert s["final_success"] is False


def test_b_capture_timeout_stops_later_frames_and_restores(tmp_path):
    def factory(e, o):
        s = FakeSession(e, o, v=4)
        s.capture_fail = TimeoutError("capture deadline")
        s.capture_fail_after = 1
        return s
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=factory, post_factory=post_ok,
              compare_func=lambda tx, iq: {"fake": True})
    assert s["study_complete"] is False
    assert [r["tag"] for r in s["rows"]] == ["A-original"]
    assert s["counts"]["completed"]["rx_delay_set"] == 2  # point set + failure restore
    assert s["chip_restored"] is True


def test_fault_after_write_uses_recovery_only_session(tmp_path):
    FakeRecovery.instances = []
    def factory(e, o):
        s = FakeSession(e, o, v=4)
        s.fail_after_write = True
        return s
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=factory, post_factory=post_ok,
              compare_func=lambda tx, iq: {"fake": True},
              recovery_factory=FakeRecovery)
    assert s["final_success"] is False
    assert s["study_complete"] is False
    assert len(FakeRecovery.instances) == 1
    rec = FakeRecovery.instances[0]
    assert rec.prepared is True and rec.closed is True
    assert rec.loads == 0 and rec.captures == 0
    assert s["counts"]["attempted"]["recovery_sessions"] == 1
    assert s["chip_restored"] is True


def test_missing_cable_refuses_before_any_action(tmp_path):
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=False,
              session_factory=FakeSession, post_factory=post_ok)
    assert s["counts"]["attempted"]["main_sessions"] == 0
    assert s["counts"]["attempted"]["digital_captures"] == 0
    assert s["final_success"] is False


def test_post_readback_failure_is_not_swallowed(tmp_path):
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=lambda e, o: FakeSession(e, o, v=4),
              post_factory=lambda: {"available": False, "error": "boom"},
              compare_func=lambda tx, iq: {"fake": True})
    assert s["final_success"] is False
    assert s["rf_safe"] is False
    assert s["fpga_restored"] is False


def test_deadline_refuses_before_first_capture(tmp_path):
    calls = {"n": 0}
    def now():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 181.0
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=lambda e, o: FakeSession(e, o, v=4),
              post_factory=post_ok, compare_func=lambda tx, iq: {"fake": True},
              now_fn=now)
    assert s["study_complete"] is False
    assert s["counts"]["attempted"]["digital_captures"] == 0
    assert any("deadline" in f for f in s["failures"])


def test_budget_refuses_before_point_write(tmp_path, monkeypatch):
    monkeypatch.setitem(R.BUDGET, "rx_delay_set", 0)
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=lambda e, o: FakeSession(e, o, v=4),
              post_factory=post_ok, compare_func=lambda tx, iq: {"fake": True})
    assert s["study_complete"] is False
    assert s["counts"]["attempted"]["rx_delay_set"] == 0
    assert any("budget_exceeded" in f for f in s["failures"])


def test_quiet_failure_refuses_before_capture(tmp_path):
    def factory(e, o):
        s = FakeSession(e, o, v=4)
        s.quiet = False
        return s
    # _idle_check uses the fake poll result, so make it report busy.
    orig = factory
    def busy_factory(e, o):
        s = orig(e, o)
        orig_poll = s.client.poll
        s.client.poll = lambda: types.SimpleNamespace(to_dict=lambda: {
            "release_id": R.CANDIDATE, "safe": True, "armed": False, "core": False,
            "pl_status": {"safe": True, "io_ok": True,
                          "tx": {"source_selected": True},
                          "rx": {"armed": False},
                          "diagnostics": {"idelay_en": {"raw": 127},
                                          "idelay_tap": {"raw": 29}}}})
        return s
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=busy_factory, post_factory=post_ok,
              compare_func=lambda tx, iq: {"fake": True})
    assert s["study_complete"] is False
    assert s["counts"]["attempted"]["digital_captures"] == 0
    assert any("not_quiet" in f for f in s["failures"])


def test_restore_failure_does_not_report_chip_success(tmp_path):
    def factory(e, o):
        s = FakeSession(e, o, v=4)
        s.fail_restore_value = 4
        return s
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=factory, post_factory=post_ok,
              compare_func=lambda tx, iq: {"fake": True})
    assert s["study_complete"] is False
    assert s["final_success"] is False
    assert s["chip_restored"] is False
    assert any("daemon refused" in f for f in s["failures"])


def test_failed_set_attempt_is_counted_before_completion(tmp_path):
    def factory(e, o):
        s = FakeSession(e, o, v=4)
        s.fail_set_value = 5
        return s
    s = R.run(R.CANDIDATE, tmp_path / "run", "fake-attempt", cable_confirmed=True,
              session_factory=factory, post_factory=post_ok,
              compare_func=lambda tx, iq: {"fake": True})
    assert s["counts"]["attempted"]["rx_delay_set"] == 1
    assert s["counts"]["completed"]["rx_delay_set"] == 0
    assert s["counts"]["attempted"]["rx_delay_set_protocol_reject"] == 1
