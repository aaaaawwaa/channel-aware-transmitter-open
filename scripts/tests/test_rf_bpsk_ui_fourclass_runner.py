"""Fake-session control-flow tests for the four-class runner (no board/RF)."""
import json
import pathlib
import sys
import hashlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rf_bpsk_ui_acceptance as A  # noqa: E402
import rf_bpsk_ui_board_session as S  # noqa: E402

RELEASE = "r" * 32


class FakeSession:
    instances = []
    fail_on_capture = None
    bad_off = False
    accepted_none = False
    no_ber = False
    same_identity = False

    def __init__(self, expected, output, teaching=False, tx_mode="BPSK", seed=None,
                 amplitude=60):
        self.expected = expected
        self.tx_mode = tx_mode
        self.seed = seed
        self.amplitude = amplitude
        self.calls = []
        self.n = 0
        FakeSession.instances.append(self)

    def initial(self):
        self.calls.append("initial")
        return {"safe": True, "release_id": self.expected}

    def setup(self, seed):
        self.calls.append(("setup", seed))

    def capture(self, tag, physical):
        self.calls.append(("capture", tag, physical))
        self.n += 1
        if FakeSession.fail_on_capture == self.n:
            raise RuntimeError("injected capture failure")
        if physical:
            mod = {"status": "OK", "modulation": {"final_label": self.tx_mode,
                                                   "accepted": True}}
            record = {"egress_safe": True,
                      "capture_id": ("same-capture" if FakeSession.same_identity
                                     else "c%d" % self.n),
                      "request_id": (7 if FakeSession.same_identity else self.n),
                      "release_id": self.expected,
                      "iq_sha256": (hashlib.sha256(self.tx_mode.encode()).hexdigest()
                                    if FakeSession.same_identity else "a" * 64),
                      "metadata_file": tag + ".json", "modrec4": mod,
                      "pre_tx_source_selected": False,
                      "post_tx_source_selected": False}
            if not FakeSession.no_ber:
                record["result"] = {"reference_sync": True, "bit_errors": 0,
                                    "compared_bits": 100, "ber": 0.0}
            return record
        accepted = None if FakeSession.accepted_none else False
        return {"egress_safe": True, "capture_id": "off%d" % self.n,
                "release_id": self.expected, "iq_sha256": "b" * 64,
                "metadata_file": tag + ".json",
                "off_tx_ok": not FakeSession.bad_off,
                "pre_tx_source_selected": bool(FakeSession.bad_off),
                "post_tx_source_selected": False,
                "modrec4": {"status": "OK", "modulation": {"final_label": "UNKNOWN",
                                                            "accepted": accepted}}}

    def cleanup(self):
        self.calls.append("cleanup")
        return {"safe": True, "disarm_ack": True}

    def close(self):
        self.calls.append("close")


def test_fourclass_campaign_counts_modes_off_and_classification(tmp_path):
    FakeSession.instances = []
    FakeSession.fail_on_capture = None
    factory = lambda mode, seed, amp: FakeSession(RELEASE, tmp_path, tx_mode=mode,
                                                    seed=seed, amplitude=amp)
    result = A.fourclass_campaign(factory, tmp_path, RELEASE, amplitude=60)
    assert result["passed"] is True
    assert result["counts"] == dict(sessions=4, initializations=4, loads=4,
                                    tx_frames=12, physical_captures=14,
                                    on_frames=12, off_frames=2, valid_on=12, valid_off=2)
    assert len(result["sessions"]) == 4
    assert [s["mode"] for s in result["sessions"]] == ["BPSK", "QPSK", "QAM16", "QAM64"]
    assert [s["seed"] for s in result["sessions"]] == [1, 37, 73, 127]
    assert [s.tx_mode for s in FakeSession.instances] == ["BPSK", "QPSK", "QAM16", "QAM64"]
    assert [s.seed for s in FakeSession.instances] == [1, 37, 73, 127]
    assert [s.amplitude for s in FakeSession.instances] == [60] * 4
    assert result["false_accepts"] == 0
    assert all(v["valid"] and v["correct_rejection"] for v in result["off"])
    assert all(v["label_correct"] == v["total"] == 3 for v in result["classification"].values())
    # No retry: exactly 12 physical + 2 OFF captures, one setup per session.
    assert sum(1 for s in FakeSession.instances for c in s.calls
               if isinstance(c, tuple) and c[0] == "capture" and c[2] is True) == 12
    assert sum(1 for s in FakeSession.instances for c in s.calls
               if isinstance(c, tuple) and c[0] == "capture" and c[2] is False) == 2
    assert sum(1 for s in FakeSession.instances for c in s.calls if isinstance(c, tuple) and c[0] == "setup") == 4


def test_fourclass_campaign_stops_on_capture_error_and_cleans_up(tmp_path):
    FakeSession.instances = []
    FakeSession.fail_on_capture = 2
    factory = lambda mode, seed, amp: FakeSession(RELEASE, tmp_path, tx_mode=mode,
                                                    seed=seed, amplitude=amp)
    result = A.fourclass_campaign(factory, tmp_path, RELEASE)
    assert result["passed"] is False
    assert result["stop_cause"] == "capture_error: RuntimeError"
    assert len(result["records"]) == 1
    assert result["sessions"][0]["closed"] is True
    assert "cleanup" in FakeSession.instances[0].calls
    assert "close" in FakeSession.instances[0].calls
    FakeSession.fail_on_capture = None


def test_main_single_mode_seed_amplitude_and_off(tmp_path, monkeypatch):
    FakeSession.instances = []
    FakeSession.fail_on_capture = None
    monkeypatch.setattr(S, "LiveUiSession", FakeSession)
    output = tmp_path / "single"
    rc = S.main(["--expected-release", RELEASE, "--output", str(output),
                 "--cable-confirmed", "--single-frame", "--mode", "QPSK",
                 "--seed", "37", "--amplitude", "60"])
    assert rc == 0
    assert len(FakeSession.instances) == 1
    assert FakeSession.instances[0].tx_mode == "QPSK"
    assert FakeSession.instances[0].seed == 37
    assert FakeSession.instances[0].amplitude == 60
    final = json.loads((output / "final.json").read_text())
    assert final["single_frame"] is True and final["physical"] is True


def test_main_single_off_uses_no_physical_capture(tmp_path, monkeypatch):
    FakeSession.instances = []
    FakeSession.fail_on_capture = None
    monkeypatch.setattr(S, "LiveUiSession", FakeSession)
    output = tmp_path / "off"
    rc = S.main(["--expected-release", RELEASE, "--output", str(output),
                 "--cable-confirmed", "--single-frame", "--off"])
    assert rc == 0
    final = json.loads((output / "final.json").read_text())
    assert final["single_frame"] is True and final["physical"] is False
    assert len(FakeSession.instances) == 1
    assert [c for c in FakeSession.instances[0].calls
            if isinstance(c, tuple) and c[0] == "capture"] == [("capture", "s00-off-00", False)]


def test_main_fourclass_cli_counts(tmp_path, monkeypatch):
    FakeSession.instances = []
    FakeSession.fail_on_capture = None
    FakeSession.bad_off = FakeSession.accepted_none = FakeSession.no_ber = False
    monkeypatch.setattr(S, "LiveUiSession", FakeSession)
    output = tmp_path / "four"
    rc = S.main(["--expected-release", RELEASE, "--output", str(output),
                 "--cable-confirmed", "--fourclass"])
    assert rc == 0
    final = json.loads((output / "fourclass.json").read_text())
    assert final["passed"] is True
    assert final["counts"]["sessions"] == 4
    assert final["counts"]["tx_frames"] == 12
    assert final["counts"]["off_frames"] == 2
    assert [s.tx_mode for s in FakeSession.instances] == ["BPSK", "QPSK", "QAM16", "QAM64"]


def test_single_non_bsk_accepts_modrec4_without_ber(tmp_path):
    FakeSession.instances = []
    FakeSession.fail_on_capture = None
    FakeSession.bad_off = FakeSession.accepted_none = False
    FakeSession.no_ber = True
    result = A.campaign(lambda: FakeSession(RELEASE, tmp_path, tx_mode="QAM64",
                                             seed=127, amplitude=60),
                        tmp_path, RELEASE, single=True, seed=127,
                        require_ber=False)
    assert result["passed"] is True, result
    assert result["require_ber"] is False
    assert result["records"][0]["modrec4"]["modulation"]["final_label"] == "QAM64"
    assert result["records"][0].get("result", {}).get("reference_sync") is None
    FakeSession.no_ber = False


def test_invalid_off_stops_immediately_and_preserves_tx_fields(tmp_path):
    FakeSession.instances = []
    FakeSession.fail_on_capture = None
    FakeSession.accepted_none = FakeSession.no_ber = False
    FakeSession.bad_off = True
    factory = lambda mode, seed, amp: FakeSession(RELEASE, tmp_path, tx_mode=mode,
                                                    seed=seed, amplitude=amp)
    result = A.fourclass_campaign(factory, tmp_path, RELEASE)
    assert result["passed"] is False
    assert result["stop_cause"] == "off_tx_invalid"
    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["physical"] is False
    assert record["off_tx_ok"] is False
    assert record["pre_tx_source_selected"] is True
    assert record["post_tx_source_selected"] is False
    # No ON capture after the invalid OFF, and the field was not overwritten.
    calls = FakeSession.instances[0].calls
    assert not [c for c in calls if isinstance(c, tuple) and c[0] == "capture"
                and c[2] is True]
    FakeSession.bad_off = False


def test_off_accepted_none_is_invalid_not_correct_rejection(tmp_path):
    FakeSession.instances = []
    FakeSession.fail_on_capture = FakeSession.bad_off = FakeSession.no_ber = False
    FakeSession.accepted_none = True
    factory = lambda mode, seed, amp: FakeSession(RELEASE, tmp_path, tx_mode=mode,
                                                    seed=seed, amplitude=amp)
    result = A.fourclass_campaign(factory, tmp_path, RELEASE)
    assert result["passed"] is False
    assert result["counts"]["valid_off"] == 0
    assert all(v["valid"] is False and v["correct_rejection"] is False
               and v["invalid_reason"] == "accepted_missing" for v in result["off"])
    FakeSession.accepted_none = False


def test_same_request_capture_is_disambiguated_by_session_and_iq(tmp_path):
    FakeSession.instances = []
    FakeSession.fail_on_capture = FakeSession.bad_off = False
    FakeSession.accepted_none = FakeSession.no_ber = False
    FakeSession.same_identity = True
    factory = lambda mode, seed, amp: FakeSession(RELEASE, tmp_path, tx_mode=mode,
                                                    seed=seed, amplitude=amp)
    result = A.fourclass_campaign(factory, tmp_path, RELEASE)
    assert result["passed"] is True
    on = [r for r in result["records"] if r["physical"]]
    assert {r["capture_id"] for r in on} == {"same-capture"}
    assert {r["request_id"] for r in on} == {7}
    # Same request/capture id is not enough to associate frames: session and IQ sha
    # are the disambiguators exposed by the runner.
    assert len({(r["session_index"], r["iq_sha256"]) for r in on}) == 4
    assert {r["session_index"] for r in on} == {0, 1, 2, 3}
    FakeSession.same_identity = False


def test_failed_setup_produces_no_successful_frame(tmp_path):
    class SetupFails(FakeSession):
        def setup(self, seed):
            self.calls.append(("setup", seed))
            raise RuntimeError("load failed")

    FakeSession.instances = []
    SetupFails.fail_on_capture = None
    SetupFails.bad_off = SetupFails.accepted_none = False
    SetupFails.no_ber = SetupFails.same_identity = False
    result = A.fourclass_campaign(
        lambda mode, seed, amp: SetupFails(RELEASE, tmp_path, tx_mode=mode,
                                           seed=seed, amplitude=amp),
        tmp_path, RELEASE)
    assert result["passed"] is False
    assert result["records"] == []
    assert result["sessions"][0]["closed"] is True
    assert "cleanup" in FakeSession.instances[0].calls
    assert "close" in FakeSession.instances[0].calls


def test_keyboard_interrupt_runs_existing_cleanup(tmp_path):
    class Cancel(FakeSession):
        def capture(self, tag, physical):
            self.calls.append(("capture", tag, physical))
            raise KeyboardInterrupt("injected cancel")

    FakeSession.instances = []
    Cancel.fail_on_capture = None
    Cancel.bad_off = Cancel.accepted_none = False
    Cancel.no_ber = Cancel.same_identity = False
    try:
        A.campaign(lambda: Cancel(RELEASE, tmp_path), tmp_path, RELEASE,
                   single=True, seed=1)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("cancellation must propagate")
    calls = FakeSession.instances[0].calls
    assert "cleanup" in calls
    assert "close" in calls







