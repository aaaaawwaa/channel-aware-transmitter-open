"""Offline tests for RX-mode-aware board runner and 6-frame QPSK orchestrator."""
import dataclasses
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "pynq_overlay" / "sw"))

import rf_bpsk_ui_board_session as S  # noqa: E402
import rf_qpsk_real_validation as V  # noqa: E402
import ui_model as M  # noqa: E402


class _Worker:
    def __init__(self):
        self.calls = []

    def submit_control(self, action, token, args):
        self.calls.append((action, token, args))
        return {"queued": True}


def test_setup_uses_explicit_rx_mode_and_seed_reaches_product_submission():
    app = M.UiApp()
    app.set_rx_demod_status(dict(available=False, release_id="release",
                                 qpsk_available=True,
                                 qpsk_algorithm_version="qpsk-rect-snapshot-v1",
                                 qpsk_algorithm_sha256="b" * 64,
                                 algorithm_version=None, algorithm_sha256=None))
    app.rf_parameter_plan = lambda action: ({}, None)
    session = object.__new__(S.LiveUiSession)
    session.app = app
    session.worker = _Worker()
    session._panel_configured = False
    session._tx_mode = "QPSK"
    session._tx_seed = 39
    session._tx_amplitude = 60
    session._rx_mode = "QPSK"
    session._rx_seed = 39
    session._wait_control = lambda action: {"ok": True, "args": {"action": action}}
    session._finalize_load_evidence = lambda result: None
    session.panel_tick = lambda: None
    session.setup(39)
    assert app.rx_draft["expected_mode"] == "QPSK"
    assert app.rx_draft["reference_enabled"] is True
    assert app.rx_draft["reference_seed"] == 39
    submission = app.rx_demod_submission()
    assert submission is not None
    assert submission.config.mode == "QPSK"
    assert submission.iq_order == "QI"
    assert [c[0] for c in session.worker.calls] == [
        "arm", "init", "configure", "load"]


def test_setup_defaults_to_bpsk_and_preserves_tx_mode():
    app = M.UiApp()
    app.set_rx_demod_status(dict(available=True, release_id="release",
                                 algorithm_version="bpsk-rect-snapshot-v2",
                                 algorithm_sha256="a" * 64,
                                 qpsk_available=False,
                                 qpsk_algorithm_version=None,
                                 qpsk_algorithm_sha256=None))
    app.rf_parameter_plan = lambda action: ({}, None)
    session = object.__new__(S.LiveUiSession)
    session.app = app
    session.worker = _Worker()
    session._panel_configured = False
    session._tx_mode = "BPSK"
    session._tx_seed = None
    session._tx_amplitude = 60
    session._rx_mode = "BPSK"
    session._rx_seed = None
    session._wait_control = lambda action: {"ok": True, "args": {"action": action}}
    session._finalize_load_evidence = lambda result: None
    session.panel_tick = lambda: None
    session.setup(7)
    assert app.rx_draft["expected_mode"] == "BPSK"
    assert app.rx_draft["reference_seed"] == 7
    assert app.rx_demod_submission().config.mode == "BPSK"


def _result(frame, *, bad=None, off=False):
    if bad == "missing_demod":
        return dict(envelope_present=False, algorithm_version=None,
                    rx_mode=frame["rx_mode"], tx_mode=frame["tx_mode"],
                    tx_seed=frame["tx_seed"], rx_seed=frame["rx_seed"],
                    demod_config=None)
    version = ("qpsk-rect-snapshot-v1" if frame["rx_mode"] == "QPSK"
               else "bpsk-rect-snapshot-v2")
    mode = "BPSK" if bad == "wrong_config_mode" else frame["rx_mode"]
    result = dict(
        carrier_sync=not off, timing_sync=not off,
        reference_sync=(None if off else True),
        reason="", valid_symbols=(203 if frame["rx_mode"] == "QPSK" else None),
        bit_errors=(None if off else 0), compared_bits=(None if off else 128),
        ber=(None if off else 0.0), algorithm_version=version,
        iq_order=("QI" if frame["rx_mode"] == "QPSK" else None),
        evm_available=False, evm_rms=None,
        tx_mode=frame["tx_mode"], tx_seed=frame["tx_seed"],
        rx_mode=frame["rx_mode"], rx_seed=frame["rx_seed"],
        demod_config={"config": {"mode": mode}, "iq_order": "QI"},
        envelope_present=True,
        pre_tx_source_selected=False, post_tx_source_selected=False,
    )
    if bad == "off_ber":
        result.update(reference_sync=True, bit_errors=0, compared_bits=128, ber=0.0)
    return result


class _FakeSession:
    calls = []

    def __init__(self, expected, output, **kwargs):
        self.expected = expected
        self.output = pathlib.Path(output)
        self.kwargs = kwargs
        self.index = kwargs["session_index"]
        self.tag = kwargs["run_id"]
        self.frame = V.FRAMES[self.index]
        self._last_payload = None
        self.cleaned = False
        type(self).calls.append(("new", self.tag, kwargs))

    def initial(self):
        return {"safe": True, "release_id": self.expected}

    def setup(self, seed):
        type(self).calls.append(("setup", self.tag, seed))

    def capture(self, tag, physical):
        bad = getattr(type(self), "bad", None)
        type(self).calls.append(("capture", tag, physical))
        result = _result(self.frame, bad=bad, off=not physical)
        version = result["algorithm_version"]
        if physical and bad == "missing_demod":
            self._last_payload = {"demod_result": None}
            envelope = None
        else:
            envelope = {
                "algorithm_version": version,
                "submitted": {"config": {"mode": self.frame["rx_mode"]},
                              "iq_order": "QI"},
                "result": {"capture_id": ("old-capture" if bad == "stale" else self.tag),
                           "release_id": self.expected,
                           "algorithm_version": version},
            }
            self._last_payload = {"demod_result": envelope}
        return dict(capture_id=self.tag, iq_sha256="c" * 64,
                    release_id=self.expected, egress_safe=True,
                    result=result, tx_load_evidence={"tx_seed": self.frame["tx_seed"],
                                                     "payload_sha256": "d" * 64},
                    pre_tx_source_selected=False, post_tx_source_selected=False,
                    off_tx_ok=(True if not physical else None))

    def cleanup(self):
        self.cleaned = True
        type(self).calls.append(("cleanup", self.tag))
        return {"safe": True, "disarm_ack": True}

    def close(self):
        type(self).calls.append(("close", self.tag))


def _run(tmp_path, bad=None):
    _FakeSession.calls = []
    _FakeSession.bad = bad
    out = tmp_path / "out"
    summary = V.run_validation("release", out, session_factory=_FakeSession,
                               cable_confirmed=True)
    return summary, out


def test_six_frame_plan_counts_and_tx_seed_rx_seed(tmp_path):
    summary, out = _run(tmp_path)
    assert summary["structural_complete"] is True
    assert summary["counts"] == dict(sessions=6, initializations=6, loads=6,
                                     on_tx=4, captures=6)
    assert len(summary["rows"]) == 6
    assert len([c for c in _FakeSession.calls if c[0] == "new"]) == 6
    setup = {c[1]: c[2] for c in _FakeSession.calls if c[0] == "setup"}
    assert setup["02-qpsk-39"] == 39
    assert setup["03-qpsk-40"] == 40
    assert setup["04-qpsk-41"] == 41
    assert setup["05-bpsk-6"] == 6
    qpsk = [r for r in summary["rows"] if r["rx_mode"] == "QPSK" and r["physical"]]
    assert len(qpsk) == 3 and all(r["result"]["iq_order"] == "QI" for r in qpsk)
    assert all(r["result"]["tx_seed"] == r["result"]["rx_seed"] for r in qpsk)
    assert (out / "plan.json").is_file() and (out / "summary.json").is_file()


def test_missing_demod_or_wrong_mode_stops_without_success(tmp_path):
    summary, _ = _run(tmp_path, bad="missing_demod")
    assert summary["structural_complete"] is False
    assert summary["stop_cause"].startswith("missing_product_demod_result")
    assert summary["counts"]["captures"] == 1
    summary, _ = _run(tmp_path, bad="wrong_config_mode")
    assert summary["structural_complete"] is False
    assert summary["stop_cause"].startswith("demod_config_mode_mismatch")


def test_off_residual_sync_or_ber_stops(tmp_path):
    _FakeSession.calls = []
    _FakeSession.bad = "off_ber"
    out = tmp_path / "off"
    summary = V.run_validation("release", out, session_factory=_FakeSession,
                               cable_confirmed=True)
    assert summary["structural_complete"] is False
    # First OFF frame must stop on residual sync/BER.
    assert summary["stop_cause"].startswith("off_reference_sync_residual") or \
        summary["stop_cause"].startswith("off_ber_residual")
    assert len(summary["rows"]) == 1


def test_cancel_runs_cleanup_once(tmp_path):
    _FakeSession.calls = []
    _FakeSession.bad = None

    class Cancelled(_FakeSession):
        def capture(self, tag, physical):
            raise KeyboardInterrupt("operator cancel")

    with pytest.raises(KeyboardInterrupt):
        V.run_validation("release", tmp_path / "cancel",
                         session_factory=Cancelled, cable_confirmed=True)
    cleanup = [c for c in Cancelled.calls if c[0] == "cleanup"]
    assert len(cleanup) == 1


def test_demod_fields_are_generic_and_never_fabricate_ber():
    qpsk = type("E", (), {})()
    qpsk.result = type("R", (), dict(
        capture_id="x", carrier_sync=True, timing_sync=True, reference_sync=True,
        reason="", valid_symbols=127, bit_errors=0, compared_bits=None,
        ber=None, evm_available=False, evm_rms=None))()
    qpsk.submitted = type("S", (), dict(iq_order="QI"))()
    qpsk.algorithm_version = "qpsk-rect-snapshot-v1"
    fields = S._demod_fields(qpsk)
    assert fields["valid_symbols"] == 127 and fields["iq_order"] == "QI"
    assert fields["bit_errors"] == 0 and fields["ber"] is None
    assert fields["evm_available"] is False
    bpsk = type("E", (), {})()
    bpsk.result = type("R", (), dict(
        carrier_sync=True, timing_sync=True, reference_sync=None, reason="NO_REFERENCE",
        total_symbols=201, bit_errors=None, compared_bits=None, ber=None))()
    bpsk.submitted = type("S", (), {})()
    bpsk.algorithm_version = "bpsk-rect-snapshot-v2"
    fields = S._demod_fields(bpsk)
    assert fields["valid_symbols"] == 201 and fields["reference_sync"] is None
    assert fields["ber"] is None and fields["evm_available"] is False
    missing = S._demod_fields(None)
    assert missing["ber"] is None and missing["bit_errors"] is None


def test_stale_demod_capture_identity_stops(tmp_path):
    _FakeSession.calls = []
    _FakeSession.bad = "stale"
    summary = V.run_validation("release", tmp_path / "stale",
                               session_factory=_FakeSession, cable_confirmed=True)
    assert summary["structural_complete"] is False
    assert summary["stop_cause"].startswith("stale_demod_capture_id")


def test_cable_confirmation_is_required(tmp_path):
    with pytest.raises(ValueError):
        V.run_validation("release", tmp_path / "x", session_factory=_FakeSession,
                         cable_confirmed=False)



def test_qpsk_single_evidence_accepts_negative_sync_without_bpsk_ber():
    record = dict(egress_safe=True, capture_id="c", iq_sha256="a" * 64,
                  result=dict(envelope_present=True,
                              algorithm_version="qpsk-rect-snapshot-v1",
                              iq_order="QI", valid_symbols=127,
                              demod_config={"config": {"mode": "QPSK"},
                                            "iq_order": "QI"},
                              reference_sync=None, bit_errors=None,
                              compared_bits=None, ber=None))
    ok, problem = S.qpsk_single_evidence_ok(record)
    assert ok is True and problem == ""
    broken = json.loads(json.dumps(record))
    broken["result"]["iq_order"] = "IQ"
    ok, problem = S.qpsk_single_evidence_ok(broken)
    assert ok is False and problem == "qpsk_iq_order_not_qi"
    missing = json.loads(json.dumps(record))
    missing["result"]["envelope_present"] = False
    assert S.qpsk_single_evidence_ok(missing)[0] is False
