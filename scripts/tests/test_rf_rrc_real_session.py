"""Fake/offline tests for RX/TX shaping handoff and the fixed 6-frame RRC smoke."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "pynq_overlay" / "sw"))

import rf_bpsk_ui_board_session as S  # noqa: E402
import rf_rrc_real_validation as V  # noqa: E402
import ui_model as M  # noqa: E402


class _Worker:
    def __init__(self):
        self.calls = []

    def submit_control(self, action, token, args):
        self.calls.append((action, token, args))
        return {"queued": True}


def _rrc_app(status="ok"):
    app = M.UiApp()
    if status == "ok":
        app.set_rx_demod_status(dict(
            available=True, release_id="release", qpsk_available=True,
            qpsk_algorithm_version="qpsk-rect-snapshot-v1",
            qpsk_algorithm_sha256="b" * 64,
            algorithm_version="bpsk-rect-snapshot-v2",
            algorithm_sha256="a" * 64,
            rrc_available=True,
            rrc_algorithm_version="rrc-alpha035-sps20-v3",
            rrc_algorithm_sha256="c" * 64))
    else:
        app.set_rx_demod_status(dict(
            available=True, release_id="release", qpsk_available=True,
            qpsk_algorithm_version="qpsk-rect-snapshot-v1",
            qpsk_algorithm_sha256="b" * 64,
            algorithm_version="bpsk-rect-snapshot-v2",
            algorithm_sha256="a" * 64,
            rrc_available=False, rrc_algorithm_version=None,
            rrc_algorithm_sha256=None))
    return app


def _session(app):
    session = object.__new__(S.LiveUiSession)
    session.app = app
    session.worker = _Worker()
    session._panel_configured = False
    session._tx_mode = "QPSK"
    session._tx_seed = 39
    session._tx_amplitude = 60
    session._tx_shaping = "RRC"
    session._tx_rolloff = "0.35"
    session._rx_mode = "QPSK"
    session._rx_seed = 39
    session._rx_shaping = "RRC"
    session._rx_rolloff = "0.35"
    session._rx_submission = None
    session._wait_control = lambda action: {"ok": True, "args": {"action": action}}
    session._finalize_load_evidence = lambda result: None
    session.panel_tick = lambda: None
    app.rf_parameter_plan = lambda action: ({}, None)
    return session


def test_rrc_rx_shaping_and_tx_shaping_are_independent_and_reach_submission():
    app = _rrc_app()
    session = _session(app)
    session.setup(39)
    assert app._rf_digital_draft["filter"] == "RRC"
    assert str(app._rf_digital_draft["rolloff"]) == "0.35"
    assert app.rx_draft["shaping"] == "RRC"
    assert app.rx_draft["expected_mode"] == "QPSK"
    assert app.rx_draft["reference_seed"] == 39
    assert session._rx_submission is not None
    assert session._rx_submission["config"]["mode"] == "QPSK"
    assert session._rx_submission["config"]["iq_order"] == "QI"
    from rf_rrc_pipeline import RrcDemodConfig
    assert isinstance(app.rx_demod_submission(), RrcDemodConfig)


def test_tx_rrc_does_not_imply_rx_rrc_and_rrc_requires_capability():
    app = _rrc_app()
    session = _session(app)
    session._tx_shaping = "RRC"
    session._rx_shaping = "RECT"
    session._tx_mode = "QPSK"
    session._rx_mode = "QPSK"
    session.setup(39)
    assert app._rf_digital_draft["filter"] == "RRC"
    assert app.rx_draft["shaping"] == "RECT"
    assert app.rx_demod_submission().config.mode == "QPSK"
    # Capability missing: explicit RRC RX must refuse, not silently use RECT.
    unavailable = _rrc_app("no_rrc")
    s2 = _session(unavailable)
    s2._rx_shaping = "RRC"
    unavailable.rf_parameter_plan = lambda action: ({}, None)
    s2._wait_control = lambda action: {"ok": True, "args": {"action": action}}
    s2._finalize_load_evidence = lambda result: None
    s2.panel_tick = lambda: None
    with pytest.raises(RuntimeError):
        s2.setup(39)


def _envelope(frame, capture_id, bad=None):
    rrc = frame["rx_shaping"] == "RRC"
    version = ("rrc-alpha035-sps20-v3" if rrc else "qpsk-rect-snapshot-v1")
    off = not frame["physical"]
    if off:
        result = dict(
            capture_id=capture_id, release_id="release", shaping=frame["rx_shaping"],
            carrier_sync=False, timing_sync=False, reference_sync=False,
            reason="NO_REFERENCE", valid_symbols=None, bit_errors=None,
            compared_bits=None, ber=None, algorithm_version=version,
            iq_order=("QI" if frame["rx_shaping"] == "RRC" else "QI"),
            evm_available=False, evm_rms=None)
    else:
        result = dict(
            capture_id=capture_id, release_id="release", shaping=frame["rx_shaping"],
            carrier_sync=True, timing_sync=True, reference_sync=True, reason="",
            valid_symbols=119,
            bit_errors=0,
            compared_bits=((112 if frame["rx_mode"] == "QPSK" else 56)
                           if rrc else 128),
            ber=0.0, algorithm_version=version, iq_order="QI",
            group_delay_samples=(0 if rrc else None),
            transition_symbols=(4 if rrc else None),
            valid_symbol_start=(4 if rrc else None),
            valid_symbol_end=(123 if rrc else None),
            comparison_start_symbol=(67 if rrc else None),
            comparison_end_symbol=(123 if rrc else None),
            evm_available=False, evm_rms=None)
    if bad == "off_sync":
        result["reference_sync"] = True
    result.update(
        tx_mode=frame["tx_mode"], tx_seed=frame["tx_seed"],
        tx_shaping=frame["tx_shaping"], tx_rolloff="0.35",
        rx_mode=frame["rx_mode"], rx_seed=frame["rx_seed"],
        rx_shaping=frame["rx_shaping"], rx_rolloff="0.35",
        rx_submission=dict(config=dict(mode=frame["rx_mode"], iq_order="QI")),
        demod_config=dict(config=dict(mode=frame["rx_mode"], iq_order="QI")),
        pre_tx_source_selected=False, post_tx_source_selected=False)
    if bad == "wrong_shaping":
        result["rx_shaping"] = "RECT" if frame["rx_shaping"] == "RRC" else "RRC"
    if bad == "one_error" and frame["physical"]:
        result["bit_errors"] = 1
        result["ber"] = 1.0 / result["compared_bits"]
    if bad == "desync" and frame["physical"]:
        result.update(carrier_sync=False, timing_sync=False, reference_sync=False,
                      reason="REFERENCE_NOT_FOUND", valid_symbols=None,
                      bit_errors=None, compared_bits=None, ber=None,
                      group_delay_samples=None, transition_symbols=None,
                      valid_symbol_start=None, valid_symbol_end=None,
                      comparison_start_symbol=None, comparison_end_symbol=None)
    if bad == "bad_denominator" and frame["physical"]:
        result["compared_bits"] = 111
        result["ber"] = 0.0
    if bad == "bad_ber" and frame["physical"]:
        result["bit_errors"] = 1
        result["ber"] = 0.5
    if bad == "bad_count" and frame["physical"]:
        result["bit_errors"] = 999
        result["ber"] = 999.0 / result["compared_bits"]
    if bad == "incomplete_metrics" and frame["physical"]:
        result["ber"] = None
    if bad == "missing_metrics" and frame["physical"]:
        result.update(bit_errors=None, compared_bits=None, ber=None)
    env = dict(algorithm_version=version,
               result=dict(capture_id=("old-capture" if bad == "stale" else capture_id),
                           release_id="release", algorithm_version=version))
    if bad == "missing":
        return None, dict(envelope_present=False)
    return env, result


class _FakeSession:
    calls = []
    bad = None
    frames = V.FRAMES

    def __init__(self, expected, output, **kwargs):
        self.expected = expected
        self.output = pathlib.Path(output)
        self.kwargs = kwargs
        self.index = kwargs["session_index"]
        self.frame = type(self).frames[self.index]
        self.tag = kwargs["run_id"]
        self._last_payload = None
        type(self).calls.append(("new", self.tag, kwargs))

    def initial(self):
        return {"safe": True, "release_id": self.expected}

    def setup(self, seed):
        type(self).calls.append(("setup", self.tag, seed))

    def _sidecar(self, tag):
        bad = type(self).bad
        physical = self.frame["physical"]
        if not physical:
            # The ordinary OFF rx.capture path provides no observation.  A
            # synthetic normal-absent sidecar is also possible because the
            # forwarder writes additively; both are allowed.
            if bad == "off_obs_identity":
                return dict(schema="rf-46db-capture-observation-sidecar-v1",
                            capture_id="other-capture", request_id=1,
                            daemon_timestamp_ns=2, release_id=self.expected,
                            usable=False, stop=True,
                            reasons=["OBSERVATION_MEMBER_MISSING"],
                            observation=dict(observation_present=False))
            if bad == "off_obs_damaged":
                return dict(schema="rf-46db-capture-observation-sidecar-v1",
                            capture_id=tag, request_id=1,
                            daemon_timestamp_ns=2, release_id=self.expected,
                            usable=False, stop=True,
                            reasons=["HARDWARE_IO"], observation=None)
            if bad == "off_obs_unusable":
                return dict(schema="rf-46db-capture-observation-sidecar-v1",
                            capture_id=tag, request_id=1,
                            daemon_timestamp_ns=2, release_id=self.expected,
                            usable=False, stop=True,
                            reasons=["HARDWARE_IO"],
                            observation=dict(observation_present=True,
                                             in_window_rx_gain_index=dict(
                                                 available=False)))
            if bad == "off_normal_sidecar":
                return dict(schema="rf-46db-capture-observation-sidecar-v1",
                            capture_id=tag, request_id=1,
                            daemon_timestamp_ns=2, release_id=self.expected,
                            usable=False, stop=True,
                            reasons=["OBSERVATION_MEMBER_MISSING"],
                            observation=dict(observation_present=False))
            return None
        if bad in ("missing_obs", "on_missing_obs"):
            return None
        if bad == "on_obs_read_failed":
            return None
        if bad == "on_obs_identity":
            return dict(schema="rf-46db-capture-observation-sidecar-v1",
                        capture_id="other-capture", request_id=1,
                        daemon_timestamp_ns=2, release_id=self.expected,
                        usable=True, stop=False, reasons=[],
                        observation=dict(input_used=None))
        usable = not (bad in ("obs_stop", "on_obs_stop")
                      or (bad in ("obs_stop_later", "on_obs_stop_later")
                          and self.index > 0))
        cap = "other-capture" if bad == "obs_identity" else tag
        return dict(schema="rf-46db-capture-observation-sidecar-v1",
                    capture_id=cap, request_id=1, daemon_timestamp_ns=2,
                    release_id=self.expected, usable=usable, stop=not usable,
                    reasons=([] if usable else ["HARDWARE_IO"]),
                    observation=dict(
                        observation_present=True,
                        in_window_rx_gain_index=dict(available=True, value=76),
                        chip_tx_attenuation_mdb=dict(available=True,
                                                     value=[46000, 46000])))

    def capture(self, tag, physical):
        type(self).calls.append(("capture", tag, physical))
        bad = type(self).bad
        env, result = _envelope(self.frame, tag, bad)
        if env is not None:
            self._last_payload = {"demod_result": env,
                                  "frame": {"capture_id": tag}}
            result["envelope_present"] = True
        else:
            self._last_payload = {"demod_result": None}
        record = dict(capture_id=tag, iq_sha256="c" * 64, release_id=self.expected,
                      request_id=1, daemon_timestamp_ns=2,
                      capture_observation=self._sidecar(tag),
                      capture_observation_file=tag + ".capture-observation.json",
                      result=result, tx_load_evidence={"payload_sha256": "d" * 64},
                      frame_identity={"capture_id": tag},
                      pre_tx_source_selected=False,
                      post_tx_source_selected=False,
                      off_tx_ok=(True if not physical else None))
        if physical and bad == "on_obs_read_failed":
            record["capture_observation_error"] = "IOError: read failed"
        return record

    def cleanup(self):
        type(self).calls.append(("cleanup", self.tag))
        if type(self).bad == "bad_cleanup":
            return {"safe": False, "disarm_ack": False}
        return {"safe": True, "disarm_ack": True}

    def close(self):
        type(self).calls.append(("close", self.tag))


def _run(tmp_path, bad=None, plan="six", frames=None):
    _FakeSession.calls = []
    _FakeSession.bad = bad
    if frames is not None:
        _FakeSession.frames = frames
    elif plan == "next-min":
        _FakeSession.frames = V.NEXT_MIN_FRAMES
    elif plan == "rootcause":
        _FakeSession.frames = V.ROOTCAUSE_FRAMES
    else:
        _FakeSession.frames = V.FRAMES
    return V.run_validation("release", tmp_path / "out",
                            session_factory=_FakeSession, cable_confirmed=True,
                            plan=plan, frames=frames)


def test_six_frame_rrc_plan_budget_and_shaping_arguments(tmp_path):
    summary = _run(tmp_path)
    assert summary["structural_complete"] is True
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is True
    assert summary["plan_name"] == "six"
    assert summary["counts"] == dict(sessions=6, initializations=6, loads=6,
                                     on_tx=4, captures=6)
    assert len(summary["rows"]) == 6
    new = [c for c in _FakeSession.calls if c[0] == "new"]
    assert all(c[2]["tx_shaping"] == V.FRAMES[i]["tx_shaping"] for i, c in enumerate(new))
    assert all(c[2]["rx_shaping"] == V.FRAMES[i]["rx_shaping"] for i, c in enumerate(new))
    assert all(c[2]["tx_rolloff"] == c[2]["rx_rolloff"] == "0.35" for c in new)
    rrc = [r for r in summary["rows"] if r["rx_shaping"] == "RRC" and r["physical"]]
    assert {r["tag"] for r in rrc} == {"02-qpsk-rrc-39", "03-qpsk-rrc-40",
                                       "04-bpsk-rrc-6"}
    assert all((r["result"]["valid_symbols"],
                r["result"]["comparison_start_symbol"],
                r["result"]["comparison_end_symbol"]) == (119, 67, 123)
               for r in rrc)
    assert [r["result"]["compared_bits"] for r in rrc] == [112, 112, 56]
    rect = [r for r in summary["rows"] if r["rx_shaping"] == "RECT"]
    assert len(rect) == 1 and rect[0]["tag"] == "05-qpsk-rect-41"
    assert (tmp_path / "out" / "plan.json").is_file()
    assert (tmp_path / "out" / "summary.json").is_file()


def test_wrong_shaping_or_missing_demod_stops(tmp_path):
    summary = _run(tmp_path / "shape", bad="wrong_shaping")
    assert summary["structural_complete"] is False
    assert summary["stop_cause"].startswith("rx_shaping_mismatch")
    summary = _run(tmp_path / "missing", bad="missing")
    assert summary["structural_complete"] is False
    assert summary["stop_cause"].startswith("missing_product_demod_result")


def test_off_residual_sync_or_ber_stops(tmp_path):
    summary = _run(tmp_path, bad="off_sync")
    assert summary["structural_complete"] is False
    assert summary["stop_cause"].startswith("off_reference_sync_residual")
    assert len(summary["rows"]) == 1


def test_stale_envelope_identity_stops(tmp_path):
    summary = _run(tmp_path, bad="stale")
    assert summary["structural_complete"] is False
    assert summary["stop_cause"].startswith("stale_demod_capture_id")


def test_cancel_runs_cleanup_once(tmp_path):
    _FakeSession.calls = []
    _FakeSession.bad = None

    class Cancelled(_FakeSession):
        def capture(self, tag, physical):
            raise KeyboardInterrupt("operator cancel")

    with pytest.raises(KeyboardInterrupt):
        V.run_validation("release", tmp_path / "cancel",
                         session_factory=Cancelled, cable_confirmed=True)
    assert len([c for c in Cancelled.calls if c[0] == "cleanup"]) == 1


def test_cable_confirmation_required(tmp_path):
    with pytest.raises(ValueError):
        V.run_validation("release", tmp_path / "x", session_factory=_FakeSession,
                         cable_confirmed=False)



def test_rrc_demod_fields_extract_intervals_and_denominator():
    class Result:
        carrier_sync = True
        timing_sync = True
        reference_sync = True
        reason = ""
        valid_symbols = 119
        bit_errors = 0
        compared_bits = 112
        ber = 0.0
        algorithm_version = "rrc-alpha035-sps20-v3"
        shaping = "RRC"
        iq_order = "QI"
        evm_available = False
        evm_rms = None
        comparison_start_symbol = 67

    class Submitted:
        iq_order = "QI"

    class Envelope:
        result = Result()
        submitted = Submitted()
        algorithm_version = "rrc-alpha035-sps20-v3"
        group_delay_samples = 0
        transition_symbols = 4
        valid_symbol_start = 4
        valid_symbol_end = 123
        comparison_start_symbol = 67
        comparison_end_symbol = 123

    fields = S._demod_fields(Envelope())
    assert fields["shaping"] == "RRC" and fields["iq_order"] == "QI"
    assert (fields["compared_bits"], fields["ber"]) == (112, 0.0)
    assert (fields["group_delay_samples"], fields["transition_symbols"]) == (0, 4)
    assert (fields["valid_symbol_start"], fields["valid_symbol_end"]) == (4, 123)
    assert (fields["comparison_start_symbol"],
            fields["comparison_end_symbol"]) == (67, 123)



def test_one_error_continues_all_six_frames_and_is_not_metric_pass(tmp_path):
    summary = _run(tmp_path, bad="one_error")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is False
    assert summary["stop_cause"] is None
    assert len(summary["rows"]) == 6
    on_rows = [r for r in summary["rows"] if r["physical"]]
    assert len(on_rows) == 4
    assert all(r["metrics_ok"] is False for r in on_rows)
    assert all(r["metric_problem"] == "metric_negative_nonzero_ber" for r in on_rows)
    assert "02-qpsk-rrc-39" in summary["metric_failures"]


def test_legal_desync_continues_with_empty_metrics(tmp_path):
    summary = _run(tmp_path, bad="desync")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is False
    assert summary["stop_cause"] is None
    assert len(summary["rows"]) == 6
    for row in [r for r in summary["rows"] if r["physical"]]:
        assert row["metrics_ok"] is False
        assert row["metric_problem"] == "legal_desync_negative"


def test_cleanup_failure_stops_campaign(tmp_path):
    summary = _run(tmp_path, bad="bad_cleanup")
    assert summary["execution_complete"] is False
    assert summary["metrics_passed"] is False
    assert str(summary["stop_cause"]).startswith("cleanup_not_safe")


def test_bad_denominator_is_structural_stop(tmp_path):
    summary = _run(tmp_path, bad="bad_denominator")
    assert summary["execution_complete"] is False
    assert summary["structural_valid"] is False
    assert str(summary["stop_cause"]).startswith("metric_denominator_mismatch")



@pytest.mark.parametrize("bad,prefix", [
    ("bad_ber", "metric_ber_inconsistent"),
    ("bad_count", "metric_count_out_of_range"),
    ("incomplete_metrics", "incomplete_metric_triplet"),
    ("missing_metrics", "missing_metrics_for_synced_result"),
])
def test_metric_integrity_violations_are_structural(tmp_path, bad, prefix):
    summary = _run(tmp_path / bad, bad=bad)
    assert summary["execution_complete"] is False
    assert summary["structural_valid"] is False
    assert str(summary["stop_cause"]).startswith(prefix)



def test_timeout_stops_execution(tmp_path):
    class TimedOut(_FakeSession):
        def capture(self, tag, physical):
            raise TimeoutError("capture deadline")

    _FakeSession.calls = []
    _FakeSession.bad = None
    summary = V.run_validation("release", tmp_path / "timeout",
                               session_factory=TimedOut, cable_confirmed=True)
    assert summary["execution_complete"] is False
    assert summary["metrics_passed"] is False
    assert str(summary["stop_cause"]).startswith("runtime_error:TimeoutError")



def test_next_min_plan_has_five_frames_and_three_on_tx(tmp_path):
    summary = _run(tmp_path, plan="next-min")
    assert summary["plan_name"] == "next-min"
    assert summary["counts"] == dict(sessions=5, initializations=5, loads=5,
                                     on_tx=3, captures=5)
    assert summary["budget"] == V.NEXT_MIN_BUDGET
    assert [r["tag"] for r in summary["rows"]] == [
        "01-off-first", "02-qpsk-rrc-40", "03-bpsk-rrc-6",
        "04-qpsk-rect-41", "05-off-last"]
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is True
    assert summary["stop_cause"] is None
    assert (tmp_path / "out" / "plan.json").is_file()
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert plan["plan_name"] == "next-min" and len(plan["frames"]) == 5


def test_next_min_plan_accepts_explicit_frame_list(tmp_path):
    summary = _run(tmp_path, plan="next-min", frames=V.NEXT_MIN_FRAMES)
    assert summary["plan_name"] == "next-min"
    assert len(summary["rows"]) == 5
    assert summary["counts"]["on_tx"] == 3


def test_next_min_metric_negative_continues_all_five(tmp_path):
    summary = _run(tmp_path, bad="one_error", plan="next-min")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is False
    assert summary["stop_cause"] is None
    assert len(summary["rows"]) == 5
    assert len([r for r in summary["rows"] if r["physical"]]) == 3



def test_runner_writes_capture_observation_sidecar_without_changing_frame(tmp_path):
    from types import SimpleNamespace
    from scripts import rf_capture_observation_forward as F
    from scripts.tests.test_rf_capture_observation_forward import FrozenLikeClient, header

    inner = FrozenLikeClient()
    evidence = S.EvidenceClient(inner)
    F.wrap_header_capture(evidence)
    hdr = header(frame_id=7, timestamp_ns=1234567890123)
    inner._iq_header(hdr)  # frozen header validation/hook lands before frame creation
    frame = SimpleNamespace(
        payload=b"\x00" * 16384, capture_id="rx-c1", request_id=7,
        daemon_timestamp_ns=1234567890123,
        release_id="a1b2c3d4e5f60718293a4b5c6d7e8f90",
        to_dict=lambda: dict(capture_id="rx-c1", samples=4096, bytes=16384,
                             sample_rate_hz=60000000, ratio=0,
                             release_id="a1b2c3d4e5f60718293a4b5c6d7e8f90",
                             request_id=7, daemon_timestamp_ns=1234567890123),
        pre_readback=SimpleNamespace(to_dict=lambda: {"io_ok": True}),
        post_readback=SimpleNamespace(to_dict=lambda: {"io_ok": True}))
    evidence.frame = frame
    session = object.__new__(S.LiveUiSession)
    session.output = tmp_path
    session.client = evidence
    session.app = SimpleNamespace(rx_demod_summary=lambda: {})
    session.ui_events = []
    session._capture_observation_forward = F
    session._last_payload = None
    session.run_id = "s00-on-00"
    session._session_index = 0
    meta = session._save("s00-on-00", {}, {})
    assert meta["capture_observation"]["observation"]["in_window_rx_gain_index"]["value"] == 76
    side = tmp_path / "s00-on-00.capture-observation.json"
    assert side.is_file()
    assert json.loads(side.read_text(encoding="utf-8")) == meta["capture_observation"]



def test_rootcause_plan_order_budget_and_fake_chain(tmp_path):
    summary = _run(tmp_path, plan="rootcause")
    assert summary["plan_name"] == "rootcause"
    assert summary["counts"] == dict(sessions=6, initializations=6, loads=6,
                                     on_tx=4, captures=6)
    assert summary["budget"] == V.ROOTCAUSE_BUDGET
    assert [r["tag"] for r in summary["rows"]] == [
        "01-off-first", "02-qpsk-rrc-40-a", "03-qpsk-rrc-40-b",
        "04-qpsk-rrc-39", "05-qpsk-rect-41", "06-off-last"]
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is True
    assert summary["stop_cause"] is None
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert plan["plan_name"] == "rootcause" and len(plan["frames"]) == 6


def test_rootcause_plan_accepts_explicit_frames_and_keeps_metric_semantics(tmp_path):
    summary = _run(tmp_path, plan="rootcause", frames=V.ROOTCAUSE_FRAMES)
    assert summary["plan_name"] == "rootcause" and len(summary["rows"]) == 6
    summary = _run(tmp_path / "err", bad="one_error", plan="rootcause")
    assert summary["execution_complete"] is True
    assert summary["structural_valid"] is True
    assert summary["metrics_passed"] is False
    assert summary["stop_cause"] is None
    assert len(summary["rows"]) == 6
    assert len([r for r in summary["rows"] if r["physical"]]) == 4



def test_rootcause_clean_requires_on_observation_but_off_path_does_not_provide_it(tmp_path):
    summary = _run(tmp_path, plan="rootcause")
    assert summary["execution_complete"] is True
    assert summary["metrics_passed"] is True
    assert len(summary["rows"]) == 6
    off = [r for r in summary["rows"] if r["physical"] is False]
    on = [r for r in summary["rows"] if r["physical"] is True]
    assert len(off) == 2 and len(on) == 4
    assert all(r["capture_observation"] is None for r in off)
    assert all(r["capture_observation_status"] == "OFF_PATH_NOT_PROVIDED" for r in off)
    assert all(r["capture_observation"] for r in on)
    assert all(r["capture_observation_status"] == "ON_OBSERVATION_VALID" for r in on)


def test_rootcause_off_missing_observation_continues_to_later_frames(tmp_path):
    summary = _run(tmp_path, plan="rootcause")
    assert summary["execution_complete"] is True
    assert summary["stop_cause"] is None
    assert len(summary["rows"]) == 6
    assert summary["counts"]["on_tx"] == 4
    # The OFF sidecar is absent, and no observation value was fabricated.
    assert summary["rows"][0]["capture_observation"] is None
    assert summary["rows"][0]["capture_observation_status"] == "OFF_PATH_NOT_PROVIDED"


def test_rootcause_off_normal_absent_sidecar_is_not_an_on_failure(tmp_path):
    summary = _run(tmp_path, bad="off_normal_sidecar", plan="rootcause")
    assert summary["execution_complete"] is True
    assert summary["stop_cause"] is None
    assert summary["rows"][0]["capture_observation_status"] == "OFF_PATH_NOT_PROVIDED"


def test_rootcause_on_missing_observation_stops_with_cleanup(tmp_path):
    summary = _run(tmp_path, bad="on_missing_obs", plan="rootcause")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("capture_observation_missing")
    assert len(summary["rows"]) == 2
    assert summary["counts"]["on_tx"] == 1
    assert [c[0] for c in _FakeSession.calls].count("cleanup") == 2


def test_rootcause_on_observation_identity_mismatch_stops(tmp_path):
    summary = _run(tmp_path, bad="on_obs_identity", plan="rootcause")
    assert summary["execution_complete"] is False
    assert "capture_observation_capture_id_mismatch" in str(summary["stop_cause"])
    assert len(summary["rows"]) == 2


def test_rootcause_on_observation_read_failure_stops(tmp_path):
    summary = _run(tmp_path, bad="on_obs_read_failed", plan="rootcause")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("capture_observation_read_failed")
    assert len(summary["rows"]) == 2


def test_rootcause_on_observation_stop_true_stops(tmp_path):
    summary = _run(tmp_path, bad="on_obs_stop", plan="rootcause")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("capture_observation_unusable")
    assert len(summary["rows"]) == 2


def test_rootcause_off_damaged_or_wrong_identity_is_structural(tmp_path):
    for bad, prefix in (("off_obs_damaged", "off_capture_observation_unusable"),
                        ("off_obs_identity", "off_capture_observation_")):
        summary = _run(tmp_path / bad, bad=bad, plan="rootcause")
        assert summary["execution_complete"] is False
        assert str(summary["stop_cause"]).startswith(prefix)
        assert len(summary["rows"]) == 1 and summary["rows"][0]["physical"] is False
        assert summary["counts"]["on_tx"] == 0


def test_rootcause_later_observation_stop_stops_without_later_frames(tmp_path):
    summary = _run(tmp_path, bad="on_obs_stop_later", plan="rootcause")
    assert summary["execution_complete"] is False
    assert str(summary["stop_cause"]).startswith("capture_observation_unusable")
    assert len(summary["rows"]) == 2
    assert summary["counts"]["on_tx"] == 1


def test_rootcause_observation_identity_mismatch_stops(tmp_path):
    summary = _run(tmp_path, bad="on_obs_identity", plan="rootcause")
    assert summary["execution_complete"] is False
    assert "capture_observation_capture_id_mismatch" in summary["stop_cause"]
    assert len(summary["rows"]) == 2


def test_six_and_next_min_remain_compatible_without_observation(tmp_path):
    for plan in ("six", "next-min"):
        summary = _run(tmp_path / plan, bad="on_missing_obs", plan=plan)
        assert summary["execution_complete"] is True
        assert summary["plan_name"] == plan



def test_rootcause_preflight_is_named_before_any_frame_or_init(tmp_path):
    class MissingObservation(_FakeSession):
        calls = []

        def __init__(self, expected, output, **kwargs):
            if kwargs.get("require_capture_observation"):
                raise S.CaptureObservationUnavailable(
                    "CAPTURE_OBSERVATION_LAYOUT_MISSING: rf_capture_observation.py")
            super().__init__(expected, output, **kwargs)

    with pytest.raises(S.CaptureObservationUnavailable) as info:
        V.run_validation("release", tmp_path / "preflight",
                         session_factory=MissingObservation,
                         cable_confirmed=True, plan="rootcause")
    assert "CAPTURE_OBSERVATION_LAYOUT_MISSING" in str(info.value)
    assert not [c for c in MissingObservation.calls if c[0] in ("setup", "capture")]


def test_capture_observation_layout_requires_the_remote_package(tmp_path):
    root = tmp_path / "remote"
    (root / "scripts").mkdir(parents=True)
    assert S.capture_observation_layout(root)["missing"] == list(
        S.CAPTURE_OBSERVATION_FILES)
    for name in S.CAPTURE_OBSERVATION_FILES:
        (root / "scripts" / name).write_text("# fake module\n", encoding="utf-8")
    info = S.capture_observation_layout(root)
    assert info["complete"] is True and info["package"].endswith("scripts")


def test_rootcause_import_failure_is_named_and_six_can_degrade(monkeypatch):
    monkeypatch.setattr(S, "capture_observation_layout",
                        lambda root=None: dict(package="x", missing=["m"], complete=False))
    with pytest.raises(S.CaptureObservationUnavailable) as info:
        S._capture_observation_support(required=True)
    assert "CAPTURE_OBSERVATION_LAYOUT_MISSING" in str(info.value)
    assert S._capture_observation_support(required=False) is None


def test_rootcause_hook_failure_is_named_before_init_or_tx():
    class Client:
        pass

    class Forward:
        HOOK_FLAG = "_capture_observation_hook"

        @staticmethod
        def wrap_header_capture(client):
            return client

        @staticmethod
        def innermost_client(client):
            return client

    session = object.__new__(S.LiveUiSession)
    session.client = Client()
    with pytest.raises(S.CaptureObservationUnavailable) as info:
        session._install_capture_observation(required=True, forward=Forward())
    assert "CAPTURE_OBSERVATION_HOOK_FAILED" in str(info.value)


def test_rootcause_hook_success_sets_forwarder():
    class Client:
        _capture_observation_hook = False

    class Forward:
        HOOK_FLAG = "_capture_observation_hook"

        @staticmethod
        def wrap_header_capture(client):
            client._capture_observation_hook = True
            return client

        @staticmethod
        def innermost_client(client):
            return client

    session = object.__new__(S.LiveUiSession)
    session.client = Client()
    forward = Forward()
    assert session._install_capture_observation(required=True, forward=forward) is True
    assert session._capture_observation_forward is forward



def test_remote_package_layout_imports_forward_and_extractor(tmp_path):
    import shutil
    import subprocess
    import sys

    root = tmp_path / "remote-run"
    (root / "scripts").mkdir(parents=True)
    for name in S.CAPTURE_OBSERVATION_FILES:
        shutil.copyfile(ROOT / name, root / "scripts" / name)
    (root / "scripts" / "rf_rrc_real_validation.py").write_text(
        "# uploaded runner placeholder\n", encoding="utf-8")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from scripts import rf_capture_observation_forward as F;"
        "from scripts import rf_capture_observation as C;"
        "assert callable(F.wrap_header_capture);"
        "assert callable(C.extract_frame_observation);"
        "print('REMOTE_LAYOUT_IMPORT_OK')")
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(root)],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "REMOTE_LAYOUT_IMPORT_OK" in proc.stdout
