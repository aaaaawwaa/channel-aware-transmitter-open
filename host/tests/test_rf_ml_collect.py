"""Offline controls for the real cabled-collection orchestrator.

The board is not touched here.  Every test drives collect.py through its injection
seam (an in-process DryRunLink) so the choreography, the release gate, the egress
verification, the clipping re-collection and the manifest assembly are all judged
without a socket -- and so a wrong choreography cannot be hidden behind a live board.
"""
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
VERIFIED = "46fe2c080deb9d0c42eb0a612a86d091"
CELL = "BPSK-2M-RECT"


@pytest.fixture
def collect():
    try:
        return importlib.import_module("scripts.rf_ml.collect")
    except ModuleNotFoundError:
        pytest.fail("Task7 board collector has not been implemented")


@pytest.fixture
def dataset():
    try:
        return importlib.import_module("scripts.rf_ml.dataset")
    except ModuleNotFoundError:
        pytest.fail("Task6 manifest validator has not been implemented")


def argv(collect, output, **overrides):
    args = ["--expected-release", VERIFIED, "--split", "train", "--cable-confirmed",
            "--dry-run", "--output", str(output)]
    for name, value in overrides.items():
        flag = "--" + name.replace("_", "-")
        if value is True:
            args.append(flag)
        elif value is not None:
            args += [flag, str(value)]
    return args


def rows(result, label, rate=None, shaping=None, split="train", kind=None):
    return [row for row in result.coverage_missing if row["split"] == split
            and row["label"] == label and row["symbol_rate_hz"] == rate
            and row["shaping"] == shaping and row["other_kind"] == kind]


def load(output):
    return json.loads((output / "manifest.json").read_text(encoding="utf-8"))


def test_current_verified_release_dry_run_records_exact_manifest_identity(collect, dataset, tmp_path):
    output = tmp_path / "current-release"
    link = collect.DryRunLink(release_id=VERIFIED)
    assert collect.main(argv(collect, output, cells=CELL, sessions=1, frames=1), link=link) == 0
    manifest = load(output)
    assert dataset.validate_manifest(manifest, output, coverage_origin="synthetic").valid
    assert {frame["release_id"] for frame in manifest["frames"]} == {VERIFIED}
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["release_id"] == VERIFIED and summary["dry_run"] is True
    assert {frame["origin"] for frame in manifest["frames"]} == {"synthetic"}


def test_dry_run_delivers_synthetic_frames_without_physical_coverage(collect, dataset, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink()
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10), link=link)
    assert rc == 0
    manifest = load(output)
    result = dataset.validate_manifest(manifest, output, coverage_origin="synthetic")
    assert result.valid, result.errors
    assert result.valid_frames == 10
    assert len(rows(result, "BPSK", 2000000, "RECT")) == 1
    assert rows(result, "BPSK", 2000000, "RECT")[0]["session_frame_counts"] == {
        manifest["sessions"][0]["session_id"]: 10}
    for frame in manifest["frames"]:
        assert frame["origin"] == "synthetic"
        assert frame["release_id"] == VERIFIED
        assert frame["samples"] == 4096 and frame["complete"] and frame["clipping_count"] == 0
        assert frame["exclusion_reasons"] == []
        assert frame["tx_reference"]["symbol_rate_hz"] == 2000000
        assert frame["tx_reference"]["shaping"] == "RECT"
        assert frame["agc"]["mode"] == "internal_40db_txpeak60"
        assert frame["configuration"] == {"sample_rate_hz": 60000000, "ratio": 0,
                                         "lo_hz": 80000000, "bandwidth_hz": 30000000}


def test_session_choreography_is_arm_init_configure_load_capture_then_disarm(collect, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink()
    assert collect.main(argv(collect, output, cells=CELL, sessions=1, frames=3), link=link) == 0
    assert link.events == ["verify_release", "arm", "init", "configure", "load_tx",
                           "capture", "capture", "capture", "disarm", "state"]
    assert [item["physical"] for item in link.captures] == [True, True, True]
    assert link.captures[0]["args"] == {"samples": 4096, "ratio": 0,
                                       "cable_confirmed": True, "attenuation_mdB": 40000}


def test_off_session_uses_ordinary_capture_with_an_idle_loaded_tx(collect, dataset, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink()
    assert collect.main(argv(collect, output, cells="OFF", sessions=1, frames=10),
                        link=link) == 0
    assert [item["physical"] for item in link.captures] == [False] * 10
    assert link.captures[0]["args"] == {"samples": 4096, "ratio": 0}
    manifest = load(output)
    assert all(frame["tx_reference"] is None for frame in manifest["frames"])
    assert all(frame["label"] == "OFF" for frame in manifest["frames"])
    assert link.loaded_tx, "the OFF negative control must still prove an idle loaded TX"
    assert link.loaded_tx[0]["control"] is True
    assert dataset.validate_manifest(manifest, output).valid


def test_cable_confirmation_is_required_before_anything_is_created(collect, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink()
    rc = collect.main(["--expected-release", VERIFIED, "--split", "train", "--dry-run",
                       "--output", str(output), "--cells", CELL, "--sessions", "1",
                       "--frames", "1"], link=link)
    assert rc != 0
    assert link.events == []
    assert not output.exists()


@pytest.mark.parametrize("release", ["30e012e834d8fb0adacd536efb09a1c0", "b" * 32,
                                     "e201d491bf2e5eecb3979c9bc12ab37",
                                     "E201D491BF2E5EECB3979C9BC12AB370", ""])
def test_an_unverified_release_id_is_refused_before_any_hardware_action(collect, tmp_path, release):
    output = tmp_path / "run"
    link = collect.DryRunLink()
    rc = collect.main(["--expected-release", release, "--split", "train", "--cable-confirmed",
                       "--dry-run", "--output", str(output), "--cells", CELL,
                       "--sessions", "1", "--frames", "1"], link=link)
    assert rc != 0
    assert link.events == []
    assert not output.exists()


def test_a_board_reporting_another_release_is_refused_before_any_hardware_action(collect, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink(release_id="c" * 32)
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=1), link=link)
    assert rc != 0
    assert link.events == ["verify_release"]
    assert not output.exists()


def test_existing_output_is_refused_without_resume(collect, tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("existing", encoding="utf-8")
    link = collect.DryRunLink()
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=1), link=link)
    assert rc != 0
    assert marker.read_text(encoding="utf-8") == "existing"
    assert not (output / "manifest.json").exists()


def test_resume_skips_sessions_that_are_already_collected(collect, tmp_path):
    output = tmp_path / "run"
    first = collect.DryRunLink()
    assert collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10),
                        link=first) == 0
    assert len(first.session_ids) == 1
    second = collect.DryRunLink()
    assert collect.main(argv(collect, output, cells=CELL, sessions=3, frames=10, resume=True),
                        link=second) == 0
    assert len(second.session_ids) == 2
    manifest = load(output)
    assert len(manifest["sessions"]) == 3
    third = collect.DryRunLink()
    assert collect.main(argv(collect, output, cells=CELL, sessions=3, frames=10, resume=True),
                        link=third) == 0
    assert third.session_ids == []
    assert len(load(output)["sessions"]) == 3
    assert len(load(output)["frames"]) == 30


def test_frozen_split_time_survives_resume_and_precedes_every_frame(collect, tmp_path):
    output = tmp_path / "run"
    assert collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10),
                        link=collect.DryRunLink()) == 0
    frozen = load(output)["sessions"][0]["split_frozen_at_utc"]
    assert collect.main(argv(collect, output, cells=CELL, sessions=2, frames=10, resume=True),
                        link=collect.DryRunLink()) == 0
    manifest = load(output)
    assert all(session["split_frozen_at_utc"] == frozen for session in manifest["sessions"])
    assert all(frame["captured_at_utc"] >= frozen for frame in manifest["frames"])


def test_a_clipping_condition_is_retained_and_recollected_at_a_lower_amplitude(collect, dataset, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink(clip_sessions=1)
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10), link=link)
    assert rc == 0
    manifest = load(output)
    clipped = [f for f in manifest["frames"] if f["clipping_count"]]
    usable = [f for f in manifest["frames"] if not f["exclusion_reasons"]]
    assert len(clipped) == 10 and all(f["clipping_count"] >= 8000 for f in clipped)
    assert all("clipping" in f["exclusion_reasons"] for f in clipped)
    assert all("amp060" in f["session_id"] for f in clipped)
    assert len(usable) == 10 and all("amp040" in f["session_id"] for f in usable)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert [f["amplitude"] for f in summary["failures"]] == [60]
    assert summary["failures"][0]["reason"] == "clipping"
    assert summary["amplitudes_used"] == [60, 40]
    row = rows(dataset.validate_manifest(manifest, output, coverage_origin="synthetic"),
               "BPSK", 2000000, "RECT")[0]
    assert row["qualified_sessions"] == 1


def test_a_clipping_cell_retries_the_fallback_rung_before_descending_to_the_floor(collect, tmp_path):
    # Amendment-2 (2026-10-03): ladder (60,40,40,30) bounds one retry at 40
    # then one fallback at 30 for AGC-variant cells (e.g. QPSK-3M-RRC0.35).
    output = tmp_path / "run"
    link = collect.DryRunLink(clip_sessions=3)
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10), link=link)
    assert rc == 0
    manifest = load(output)
    assert len(manifest["sessions"]) == 4
    assert [s["session_id"] for s in manifest["sessions"]] == [
        "rx-train-%s-s00-amp060" % CELL.lower(), "rx-train-%s-s01-amp040" % CELL.lower(),
        "rx-train-%s-s02-amp040" % CELL.lower(), "rx-train-%s-s03-amp030" % CELL.lower()]
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert [f["amplitude"] for f in summary["failures"]] == list(collect.AMPLITUDE_LADDER)[:-1]
    assert summary["amplitudes_used"] == [60, 40, 30]
    usable = [f for f in manifest["frames"] if not f["exclusion_reasons"]]
    assert len(usable) == 10 and all("amp030" in f["session_id"] for f in usable)


def test_a_class_that_clips_at_every_amplitude_stays_a_reported_failure(collect, dataset, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink(clip_sessions=99)
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10), link=link)
    assert rc != 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert [f["amplitude"] for f in summary["failures"]] == list(collect.AMPLITUDE_LADDER)
    manifest = load(output)
    assert manifest["frames"]
    assert all("clipping" in f["exclusion_reasons"] for f in manifest["frames"])
    assert not any(not f["exclusion_reasons"] for f in manifest["frames"])
    row = rows(dataset.validate_manifest(manifest, output), "BPSK", 2000000, "RECT")[0]
    assert row["qualified_sessions"] == 0


def test_a_failed_disarm_refuses_to_deliver_the_session_frames(collect, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink(fail_disarm=True)
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10), link=link)
    assert rc != 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert "disarm" in summary["failures"][0]["reason"]
    assert summary["stopped_early"] is True
    manifest = load(output)
    assert all(frame["exclusion_reasons"] for frame in manifest["frames"])
    assert (output / "sessions").is_dir(), "raw session evidence must be kept"


def test_a_final_readback_with_rx_still_armed_refuses_the_session(collect, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink(stale_rx_armed=True)
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10), link=link)
    assert rc != 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert "rx_armed" in summary["failures"][0]["reason"]
    assert all(frame["exclusion_reasons"] for frame in load(output)["frames"])


def test_a_final_readback_with_the_tx_source_selected_refuses_the_session(collect, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink(stale_tx_source=True)
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10), link=link)
    assert rc != 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert "tx_source" in summary["failures"][0]["reason"] or "source" in summary["failures"][0]["reason"]


def test_a_capture_timeout_stops_the_remaining_hardware_sessions(collect, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink(fail_capture_at=3)
    rc = collect.main(argv(collect, output, cells=CELL + ",QPSK-2M-RECT", sessions=1,
                           frames=10), link=link)
    assert rc != 0
    assert link.session_ids == [link.session_ids[0]]
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["stopped_early"] is True
    assert "timeout" in summary["failures"][0]["reason"].lower()
    assert summary["recovery"]["attempted"] is True


def test_a_reused_capture_buffer_is_reported_and_never_delivered_as_valid(collect, dataset, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink(duplicate_payloads=True)
    rc = collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10), link=link)
    assert rc != 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["duplicate_hashes"]
    manifest = load(output)
    assert len(manifest["frames"]) == 10
    usable = [f for f in manifest["frames"] if not f["exclusion_reasons"]]
    assert len(usable) == 1
    assert all("duplicate" in f["exclusion_reasons"][0]
               for f in manifest["frames"] if f["exclusion_reasons"])


def test_a_cell_outside_the_frozen_matrix_is_refused(collect, tmp_path):
    output = tmp_path / "run"
    link = collect.DryRunLink()
    rc = collect.main(argv(collect, output, cells="BPSK-4M-RECT", sessions=1, frames=1),
                      link=link)
    assert rc != 0
    assert link.events == []
    assert not output.exists()


@pytest.mark.parametrize("name", ["BOARD_SESSION_SOURCE", "BOARD_BRIDGE_SOURCE"])
def test_embedded_board_scripts_are_valid_python(collect, name):
    import ast

    ast.parse(getattr(collect, name))


def test_collector_matrix_equals_the_synthetic_matrix(collect):
    synthetic = importlib.import_module("scripts.rf_ml.synthetic")
    assert {cell.key for cell in collect.CELLS} == {cell.key for cell in synthetic.CELLS}


def test_on_and_off_capture_arguments_follow_the_frozen_capture_recipe(collect):
    jobs = collect.build_jobs("train", [collect.cell_by_key(CELL)], sessions=1, frames=2)
    on = jobs[0]
    assert on.physical is True
    assert on.capture_args == {"samples": 4096, "ratio": 0, "cable_confirmed": True,
                              "attenuation_mdB": 40000}
    assert on.params["lo_hz"] == 80000000 and on.params["sample_rate_hz"] == 60000000
    assert on.params["rx_bandwidth_hz"] == 30000000 and on.params["idelay_tap"] == 29
    assert on.params["attenuation_mdB"] == 40000
    assert on.tx["samples"] == 127 * 30 and len(on.tx_payload) == 127 * 30 * 4
    off = collect.build_jobs("train", [collect.cell_by_key("OFF")], sessions=1, frames=2)[0]
    assert off.physical is False
    assert off.capture_args == {"samples": 4096, "ratio": 0}
    assert off.tx is not None and off.tx_control is True


def test_prbs_seeds_and_seed_families_never_cross_splits(collect):
    families, seeds = {}, {}
    for split in ("train", "val", "test"):
        jobs = collect.build_jobs(split, list(collect.CELLS), sessions=3, frames=10)
        families[split] = {job.seed_family for job in jobs}
        seeds[split] = {job.tx_seed for job in jobs}
    for first in ("train", "val", "test"):
        for second in ("train", "val", "test"):
            if first < second:
                assert families[first].isdisjoint(families[second])
                assert seeds[first].isdisjoint(seeds[second])
    assert None not in families["train"]
    assert all(1 <= seed <= 127 for seed in seeds["train"])


def test_dry_run_full_matrix_cannot_reach_physical_production_coverage(collect, dataset, tmp_path):
    output = tmp_path / "physical"
    cells = ",".join(cell.key for cell in collect.CELLS)
    for split in ("train", "val", "test"):
        extra = {"resume": True} if split != "train" else {}
        link = collect.DryRunLink()
        assert collect.main(argv(collect, output, split=split, cells=cells, sessions=3,
                                 frames=10, augment_per_session=0, **extra),
                            link=link) == 0
        assert len(link.session_ids) == 90
    manifest = load(output)
    assert len(manifest["sessions"]) == 270
    assert len(manifest["frames"]) == 2700
    result = dataset.validate_manifest(manifest, output, production=True,
                                       coverage_origin="physical")
    assert not result.valid and not result.production_ready
    assert len(result.coverage_missing) == 90
    rehearsal = dataset.validate_manifest(manifest, output, coverage_origin="synthetic")
    assert rehearsal.valid and not rehearsal.coverage_missing
    assert result.valid_frames == 2700
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["duplicate_hashes"] == []
    assert summary["production_ready"] is False


def test_completed_session_is_durable_before_process_interruption(collect, tmp_path):
    output = tmp_path / "interrupted"

    class InterruptNext(collect.DryRunLink):
        def run_session(self, job):
            if self.session_ids:
                raise KeyboardInterrupt("process interrupted during unconfirmed session")
            return super().run_session(job)

    with pytest.raises(KeyboardInterrupt):
        collect.main(argv(collect, output, cells=CELL, sessions=2, frames=10),
                     link=InterruptNext())
    assert (output / "manifest.json").is_file()
    before = load(output)
    original = {f["capture_id"]: f["iq_sha256"] for f in before["frames"]}
    assert len(original) == 10
    resumed = collect.DryRunLink()
    assert collect.main(argv(collect, output, cells=CELL, sessions=2, frames=10,
                             resume=True), link=resumed) == 0
    assert "ensure_safe" in resumed.events
    assert len(resumed.session_ids) == 1
    after = load(output)
    assert len(after["frames"]) == 20
    assert all(next(f for f in after["frames"] if f["capture_id"] == key)["iq_sha256"]
               == value for key, value in original.items())
    assert not (output / "pending-session.json").exists()


def test_resume_refuses_new_capture_when_interrupted_session_cannot_be_closed(collect, tmp_path):
    output = tmp_path / "interrupted"

    class Interrupt(collect.DryRunLink):
        def run_session(self, job):
            raise KeyboardInterrupt("capture may still be armed")

    with pytest.raises(KeyboardInterrupt):
        collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10),
                     link=Interrupt())
    assert (output / "manifest.json").is_file()

    class UnsafeRecovery(collect.DryRunLink):
        def ensure_safe(self):
            super().ensure_safe()
            return {"online": True, "io_ok": True, "armed": False,
                    "rx_armed": True, "tx_source_selected": False}

    resumed = UnsafeRecovery()
    assert collect.main(argv(collect, output, cells=CELL, sessions=1, frames=10,
                             resume=True), link=resumed) != 0
    assert resumed.session_ids == []
    assert load(output)["frames"] == []
    assert (output / "pending-session.json").is_file()


def test_real_resume_refuses_rehearsal_provenance_before_board_probe(collect, tmp_path):
    output = tmp_path / "rehearsal"
    assert collect.main(argv(collect, output, cells=CELL, sessions=1, frames=1),
                        link=collect.DryRunLink()) == 0
    args = argv(collect, output, cells=CELL, sessions=2, frames=1, resume=True)
    args.remove("--dry-run")
    resumed = collect.DryRunLink()
    assert collect.main(args, link=resumed) != 0
    assert resumed.events == []


def test_real_resume_refuses_legacy_dry_run_marked_physical(collect, tmp_path):
    output = tmp_path / "legacy-rehearsal"
    assert collect.main(argv(collect, output, cells=CELL, sessions=1, frames=1),
                        link=collect.DryRunLink()) == 0
    data = load(output)
    for row in data["sessions"] + data["frames"]:
        row["origin"] = "physical"  # output emitted by the earlier faulty collector
    (output / "manifest.json").write_text(json.dumps(data))
    args = argv(collect, output, cells=CELL, sessions=2, frames=1, resume=True)
    args.remove("--dry-run")
    resumed = collect.DryRunLink()
    assert collect.main(args, link=resumed) != 0
    assert resumed.events == []


def test_transport_timeout_recovers_and_retains_a_failed_session(collect, tmp_path):
    import subprocess

    class Timeout(collect.DryRunLink):
        def run_session(self, job):
            raise subprocess.TimeoutExpired("board bridge", 300)

    output = tmp_path / "timeout"
    link = Timeout()
    assert collect.main(argv(collect, output, cells=CELL, sessions=1, frames=1),
                        link=link) != 0
    assert "ensure_safe" in link.events
    assert load(output)["frames"] == []
    assert json.loads((output / "summary.json").read_text())["stopped_early"]


def test_transport_disarm_failure_refuses_frames_even_when_final_state_is_safe(collect, tmp_path,
                                                                            monkeypatch):
    link = object.__new__(collect.ParamikoLink)
    link.workdir = tmp_path
    link.remote_root = "/tmp/test-collect"
    local = tmp_path / "reply"
    local.mkdir()
    (local / "frame.iq").write_bytes(bytes(16384))
    (local / "result.json").write_text(json.dumps({
        "error": None, "disarm": "failed: acknowledgment lost",
        "frames": [{"file": "frame.iq", "samples": 4096, "ratio": 0,
                    "physical": True, "rx_armed": False, "tx_source_selected": False}],
        "final_state": {"online": True, "io_ok": True, "armed": False,
                        "rx_armed": False, "tx_source_selected": False}}))
    monkeypatch.setattr(link, "_request", lambda *a, **k: ({"ok": True, "exit_code": 0}, local))
    job = collect.build_jobs("train", [collect.cell_by_key(CELL)], sessions=1, frames=1)[0]
    with pytest.raises(collect.SessionFailure, match="disarm"):
        link.run_session(job)
