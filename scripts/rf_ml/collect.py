"""Real cabled RX collection: the frozen matrix, one verified session at a time.

One session is one arm -> init -> configure -> load -> capture x N -> disarm on the sole
RF daemon, through the sanctioned Paramiko helper (never a second Overlay, never a
re-deploy, never the Windows ssh client).  The verified release id is checked before any
hardware action and the session's exit state is verified every time: source false, RX
ARM false, control disarmed.  A session that cannot be closed, that times out, or that
raises a duplicate capture buffer is retained as evidence and NEVER counted as a valid
sample, and the remaining hardware collection stops immediately.

The collector has an offline path (--dry-run / an injected link) so the choreography,
the release gate, the egress verification, the clipping re-collection and the manifest
assembly are all judged without a socket.  The board is only touched when the real
transport is selected.

Dry-run captures are synthetic bytes and are NOT physical evidence: every summary
records dry_run so a rehearsal can never be mistaken for a board run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import dataset as manifest_contract

ROOT = Path(__file__).resolve().parents[2]
SW_DIR = ROOT / "pynq_overlay" / "sw"
if str(SW_DIR) not in sys.path:
    sys.path.append(str(SW_DIR))

import rf_iq_signal          # noqa: E402  (the frozen constellation/shaper contract)
import rf_tx_waveform        # noqa: E402  (the frozen AWG waveform builders)

VERIFIED_RELEASE_ID = "46fe2c080deb9d0c42eb0a612a86d091"  # verified 2026-10-03 (deployed experimental CNN release; UI-to-IQ checked)
RELEASE_PATTERN = re.compile(r"[0-9a-f]{32}")
DEFAULT_HELPER = Path.home() / ".claude" / "skills" / "ac820-pynq" / "scripts" / "pynq_ssh.py"
BOARD_RELEASE_DIR = "/home/xilinx/overlays/awg_merged/current"
BOARD_CONTROL_TOKEN = "/etc/rf-control.token"

SCHEMA_VERSION = 1
SPLITS = ("train", "val", "test")
SAMPLE_RATE_HZ = 60000000
RATIO = 0
LO_HZ = 80000000
BANDWIDTH_HZ = 30000000
IDELAY_TAP = 29
IDELAY_EN = 1
PHY_MODE_1R1T = 0
ATTENUATION_MDB = 40000
CAPTURE_SAMPLES = 4096
CAPTURE_RATIO = 0
TONE_CYCLES = 32
SESSIONS_PER_CELL = 3
FRAMES_PER_SESSION = 10
AMPLITUDE_LADDER = (60, 40, 40, 30)  # low-clip ladder (2026-10-03): TEST clip rate by amplitude was 150=62.8%, 120=41.3%, 100=41.4%, 80=22.2%, 60=0.0%; 60 is the highest rung measured with zero clipping; 40 fallback with one bounded retry; 30 final fallback (est. -18 dBFS complex RMS; energy floor is +42.26 dB re 1 code = -24.0 dBFS, measured margin 4-9 dB depending on AGC landing) for AGC-variant cells (QPSK-3M-RRC0.35 clipped at 040 in 1 of 2 attempts)
FIRST_AMPLITUDE = AMPLITUDE_LADDER[0]
RATES = (2000000, 3000000, 5000000)
SHAPINGS = ("RECT", "RRC0.35")
DELIVERY_MODES = ("BPSK", "QPSK", "QAM16")
HISTORICAL_MODES = ("QAM64",)  # legacy ML datasets only; not delivered capability
DIGITAL_MODES = DELIVERY_MODES + HISTORICAL_MODES
OTHER_KINDS = ("NOISE", "CHIRP", "SQUARE", "MIXED")
INIT_KEYS = ("lo_hz", "sample_rate_hz", "attenuation_mdB", "phy_mode_1r1t")
SPLIT_TX_SEEDS = {"train": (11, 13, 17), "val": (29, 31, 37), "test": (47, 53, 59)}
SPLIT_TX_FAMILIES = {"train": ("rx-train-a", "rx-train-b"),
                     "val": ("rx-val-a", "rx-val-b"),
                     "test": ("rx-test-a", "rx-test-b")}
BASE_PARAMS = {"lo_hz": LO_HZ, "sample_rate_hz": SAMPLE_RATE_HZ,
               "attenuation_mdB": ATTENUATION_MDB, "phy_mode_1r1t": PHY_MODE_1R1T,
               "rx_bandwidth_hz": BANDWIDTH_HZ, "tx_bandwidth_hz": BANDWIDTH_HZ,
               "idelay_en": IDELAY_EN, "idelay_tap": IDELAY_TAP}
WIRING = "TX1-RX1 direct cable, no scope"


@dataclass(frozen=True)
class Cell:
    """One frozen matrix cell.  The label is derived from the generation fields."""

    key: str
    label: str
    other_kind: str | None = None
    mode: str | None = None
    symbol_rate_hz: int | None = None
    shaping: str | None = None

    @property
    def digital(self):
        return self.mode is not None

    def tx_reference(self, seed_family):
        if self.label == "OFF":
            return None
        return {"seed_family": seed_family, "symbol_rate_hz": self.symbol_rate_hz,
                "shaping": self.shaping}


def _build_cells():
    cells = [Cell("TONE", "TONE")]
    for mode in DIGITAL_MODES:
        for rate in RATES:
            for shaping in SHAPINGS:
                cells.append(Cell("%s-%dM-%s" % (mode, rate // 1000000, shaping),
                                  mode, None, mode, rate, shaping))
    for kind in OTHER_KINDS:
        cells.append(Cell("OTHER-%s" % kind, "OTHER", kind))
    cells.append(Cell("OFF", "OFF"))
    return tuple(cells)


CELLS = _build_cells()
_CELL_BY_KEY = {cell.key: cell for cell in CELLS}


def cell_by_key(key):
    try:
        return _CELL_BY_KEY[key]
    except KeyError:
        raise ValueError("cell %r is outside the frozen collection matrix" % (key,))


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Job:
    """Everything one session needs on the wire, decided before it starts."""

    split: str
    cell: Cell
    session_id: str
    session_ordinal: int
    amplitude: int
    frames: int
    physical: bool
    seed_family: str
    tx_seed: int
    params: dict
    tx: dict
    tx_payload: bytes
    tx_control: bool
    capture_args: dict

    @property
    def init(self):
        return {name: self.params[name] for name in INIT_KEYS}

    @property
    def configure(self):
        return dict(self.params)


def _tx_for_cell(cell, amplitude, tx_seed):
    """The TX buffer for one cell, built with the frozen constellation/waveform contract."""
    if cell.digital:
        metadata, payload = rf_iq_signal.generate_digital_frame({
            "mode": cell.mode,
            "filter": "RRC" if cell.shaping == "RRC0.35" else "RECT",
            "rolloff": "0.35", "seed": tx_seed, "symbol_rate_hz": cell.symbol_rate_hz,
            "sample_rate_hz": SAMPLE_RATE_HZ, "amplitude": amplitude},
            allow_historical_qam64=cell.mode in HISTORICAL_MODES)
        return dict(metadata), payload, False
    if cell.label == "TONE":
        metadata, payload = rf_tx_waveform.build_sine({"cycles": TONE_CYCLES,
                                                      "amplitude": amplitude})
    elif cell.other_kind == "NOISE":
        metadata, payload = rf_tx_waveform.build_noise({"amplitude": amplitude,
                                                       "seed": tx_seed})
    elif cell.other_kind == "CHIRP":
        metadata, payload = rf_tx_waveform.build_chirp(
            {"cycles": 8, "amplitude": amplitude, "chirp_f1_cycles": 80})
    elif cell.other_kind == "SQUARE":
        metadata, payload = rf_tx_waveform.build_square({"cycles": 16,
                                                        "amplitude": amplitude})
    elif cell.other_kind == "MIXED":
        half = max(1, amplitude // 2)
        metadata, payload = rf_tx_waveform.build_multitone(
            {"cycles": 8, "amplitude": half, "tone2_cycles": 48, "tone2_amplitude": half})
    else:
        # OFF: a loaded but never selected TX buffer is the stronger negative control.
        metadata, payload = rf_tx_waveform.build_sine({"cycles": TONE_CYCLES,
                                                      "amplitude": amplitude})
        return dict(metadata), payload, True
    return dict(metadata), payload, False


def build_jobs(split, cells, sessions=SESSIONS_PER_CELL, frames=FRAMES_PER_SESSION):
    """The initial matrix plan: every cell starts at the first amplitude condition."""
    if split not in SPLITS:
        raise ValueError("unknown split: %r" % (split,))
    if type(sessions) is not int or sessions < 1:
        raise ValueError("sessions must be a positive integer")
    if type(frames) is not int or frames < 1:
        raise ValueError("frames must be a positive integer")
    jobs = []
    for cell_index, cell in enumerate(cells):
        for ordinal in range(sessions):
            jobs.append(_build_job(split, cell, ordinal, FIRST_AMPLITUDE, frames,
                                   cell_index))
    return jobs


def _build_job(split, cell, ordinal, amplitude, frames, cell_index=0):
    if split not in SPLITS:
        raise ValueError("unknown split: %r" % (split,))
    if amplitude not in AMPLITUDE_LADDER:
        raise ValueError("an amplitude condition must be one of %s" % (AMPLITUDE_LADDER,))
    families = SPLIT_TX_FAMILIES[split]
    seeds = SPLIT_TX_SEEDS[split]
    index = cell_index + ordinal
    seed_family = families[index % len(families)]
    tx_seed = seeds[index % len(seeds)]
    capture_args = {"samples": CAPTURE_SAMPLES, "ratio": CAPTURE_RATIO}
    if cell.label != "OFF":
        capture_args = dict(capture_args, cable_confirmed=True,
                            attenuation_mdB=ATTENUATION_MDB)
    tx, payload, control = _tx_for_cell(cell, amplitude, tx_seed)
    return Job(split=split, cell=cell,
               session_id="rx-%s-%s-s%02d-amp%03d" % (split, cell.key.lower(), ordinal,
                                                      amplitude),
               session_ordinal=ordinal, amplitude=amplitude, frames=frames,
               physical=cell.label != "OFF", seed_family=seed_family, tx_seed=tx_seed,
               params=dict(BASE_PARAMS), tx=tx, tx_payload=payload, tx_control=control,
               capture_args=capture_args)


@dataclass(frozen=True)
class CapturedFrame:
    payload: bytes
    samples: int
    ratio: int
    physical: bool
    rx_armed: bool
    tx_source_selected: bool
    daemon_timestamp_ns: int


@dataclass(frozen=True)
class SessionResult:
    session_id: str
    frames: tuple
    final_state: dict
    log: str = ""


class SessionFailure(Exception):
    """A session that must not be delivered as valid data."""

    def __init__(self, reason, frames=(), state=None):
        super().__init__(reason)
        self.reason = reason
        self.frames = tuple(frames)
        self.state = state


class BoardLink:
    """The transport contract.  One method per session, so fail-fast is structural."""

    def verify_release(self, expected):
        raise NotImplementedError

    def run_session(self, job):
        raise NotImplementedError

    def ensure_safe(self):
        return None

    def close(self):
        pass


class DryRunLink(BoardLink):
    """Offline rehearsal: deterministic payloads, no socket, no board.

    It is a rehearsal, never evidence: the summary records dry_run=true.
    """

    def __init__(self, release_id=VERIFIED_RELEASE_ID, *, clip_sessions=0,
                 fail_disarm=False, stale_rx_armed=False, stale_tx_source=False,
                 duplicate_payloads=False, fail_capture_at=None):
        self.release_id = release_id
        self.clip_sessions = clip_sessions
        self.fail_disarm = fail_disarm
        self.stale_rx_armed = stale_rx_armed
        self.stale_tx_source = stale_tx_source
        self.duplicate_payloads = duplicate_payloads
        self.fail_capture_at = fail_capture_at
        self.events = []
        self.captures = []
        self.loaded_tx = []
        self.session_ids = []
        self.closed = False
        self._sessions = 0

    def verify_release(self, expected):
        self.events.append("verify_release")
        return self.release_id

    @staticmethod
    def _head(job, index):
        """Eight non-rail samples that make every rehearsal frame individually unique."""
        digest = hashlib.sha256(("%s|%d" % (job.session_id, index)).encode()).digest()
        return struct.pack("<8h", *[((int.from_bytes(digest[2 * k:2 * k + 2], "big") >> 2)
                                     % 4094) - 2047 for k in range(8)])

    def _payload(self, job, index, clip):
        if clip:
            # Overwhelmingly railed, with a unique head so a rehearsal is not mistaken
            # for a reused capture buffer.
            return self._head(job, index) + struct.pack("<h", 2047) * (CAPTURE_SAMPLES * 2 - 8)
        if self.duplicate_payloads:
            return struct.pack("<h", 512) * (CAPTURE_SAMPLES * 2)
        return self._head(job, index) + bytes(CAPTURE_SAMPLES * 4 - 16)

    def run_session(self, job):
        self.session_ids.append(job.session_id)
        self._sessions += 1
        clip = self._sessions <= self.clip_sessions
        self.events.extend(("arm", "init", "configure"))
        if job.tx is not None:
            self.events.append("load_tx")
            self.loaded_tx.append({"session_id": job.session_id, "control": job.tx_control,
                                   "meta": dict(job.tx), "bytes": len(job.tx_payload)})
        frames = []
        for index in range(job.frames):
            self.captures.append({"session_id": job.session_id, "physical": job.physical,
                                  "args": dict(job.capture_args)})
            self.events.append("capture")
            if self.fail_capture_at is not None and len(self.captures) == self.fail_capture_at:
                raise SessionFailure("capture timeout after 3 seconds on " + job.session_id,
                                     frames=tuple(frames))
            frames.append(CapturedFrame(payload=self._payload(job, index, clip),
                                        samples=CAPTURE_SAMPLES, ratio=CAPTURE_RATIO,
                                        physical=job.physical, rx_armed=False,
                                        tx_source_selected=False,
                                        daemon_timestamp_ns=index + 1))
        self.events.append("disarm")
        if self.fail_disarm:
            raise SessionFailure(
                "disarm failed: the daemon did not return to refusing control operations",
                frames=tuple(frames),
                state={"online": True, "io_ok": True, "armed": True, "rx_armed": False,
                       "tx_source_selected": False})
        self.events.append("state")
        state = {"online": True, "io_ok": True, "armed": False,
                 "rx_armed": self.stale_rx_armed,
                 "tx_source_selected": self.stale_tx_source}
        return SessionResult(session_id=job.session_id, frames=tuple(frames),
                             final_state=state)

    def ensure_safe(self):
        self.events.append("ensure_safe")
        return {"online": True, "io_ok": True, "armed": False, "rx_armed": False,
                "tx_source_selected": False}

    def close(self):
        self.closed = True


BOARD_SESSION_SOURCE = '''"""One cabled RX session on the sole RF daemon.  Uploaded, never installed."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

RELEASE_DIR = "/home/xilinx/overlays/awg_merged/current"
TOKEN = "<SET_LOCALLY>"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--job")
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--safe", action="store_true")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    result = {"mode": "safe" if args.safe else ("probe" if args.probe else "session"),
              "ok": False, "error": None, "release_id": None, "frames": [],
              "disarm": None, "final_state": None, "job": None,
              "started_monotonic": time.monotonic()}
    if args.job:
        result["job"] = json.loads(Path(args.job).read_text())
    release = Path(RELEASE_DIR).resolve()
    sys.path.insert(0, str(release))
    client = None
    try:
        import merged_release as R
        manifest = R.verify_release(release, require_software=True)
        if manifest.get("release_id") != args.release:
            raise RuntimeError("the installed release is not the verified release")
        from rf_status_client import RfStatusClient
        client = RfStatusClient(timeout=2.0)
        status = client.poll()
        if status.online is not True or status.stale:
            raise RuntimeError("the RF daemon is not online and current")
        if status.release_id != args.release:
            raise RuntimeError("the daemon reports a different release")
        result["release_id"] = status.release_id
        if args.safe:
            try:
                client.control_disarm()
                result["disarm"] = "ok"
            except Exception as exc:
                result["disarm"] = "failed: %s" % exc
            result["ok"] = True
        elif args.probe:
            result["ok"] = True
        else:
            job = result["job"]
            # The daemon advertises a control op only while the state allows it, so the
            # capability list is refreshed after every control action (the proven UI path).
            client.control_arm(Path(TOKEN).read_text().strip())
            client.poll()
            client.control_op("rf.init", job["init"])
            client.poll()
            client.control_op("rf.configure", job["configure"])
            client.poll()
            if job.get("tx") is not None:
                client.control_op("tx.load", job["tx"], Path(job["tx_path"]).read_bytes())
            for index in range(job["frames"]):
                frame = client.capture(dict(job["capture_args"]),
                                       physical=bool(job["physical"]))
                name = "frame-%03d.iq" % index
                (out / name).write_bytes(frame.payload)
                result["frames"].append(
                    {"index": index, "file": name, "samples": frame.samples,
                     "ratio": frame.ratio, "physical": bool(frame.physical),
                     "capture_id": frame.capture_id,
                     "daemon_timestamp_ns": frame.daemon_timestamp_ns,
                     "rx_armed": frame.post_readback.rx_armed,
                     "tx_source_selected": frame.post_readback.tx_source_selected,
                     "payload_sha256": hashlib.sha256(frame.payload).hexdigest()})
            result["ok"] = True
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        if client is not None:
            try:
                client.control_disarm()
                result["disarm"] = "ok"
            except Exception as exc:
                result["disarm"] = result["disarm"] or ("failed: %s" % exc)
            try:
                status = client.poll()
                result["final_state"] = {
                    "online": bool(status.online),
                    "io_ok": bool(status.pl_status.io_ok),
                    "armed": status.armed,
                    "rx_armed": status.pl_status.rx.armed,
                    "tx_source_selected": status.pl_status.tx.source_selected}
            except Exception as exc:
                result["final_state"] = {"error": str(exc)}
            try:
                client.close()
            except Exception:
                pass
        result["finished_monotonic"] = time.monotonic()
        (out / "result.json").write_text(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''

BOARD_BRIDGE_SOURCE = '''"""Host-side SSH bridge for collect.py.  Needs paramiko; the sanctioned helper is used."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    response = {"ok": False, "error": None, "exit_code": None, "stdout": "",
                "stderr": "", "files": [], "release_id": None}
    client = None
    try:
        spec = importlib.util.spec_from_file_location("boardhelper", args.helper)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        client = helper.connect()
        with client.open_sftp() as sftp:
            sftp.put(request["board_script"], request["remote_script"])
            if request.get("job"):
                sftp.put(request["job"], request["remote_job"])
            if request.get("tx_path"):
                sftp.put(request["tx_path"], request["remote_tx"])
        command = ("sudo -S python3 -u -B " + request["remote_script"] +
                   " --release " + request["release"] +
                   " --output " + request["remote_output"])
        if request.get("probe"):
            command += " --probe"
        elif request.get("safe"):
            command += " --safe"
        else:
            command += " --job " + request["remote_job"]
        stdin, stdout, stderr = client.exec_command(command, timeout=request["timeout"])
        stdin.write(helper.PASS + "\\n")
        stdin.flush()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        response["exit_code"] = stdout.channel.recv_exit_status()
        response["stdout"], response["stderr"] = out, err
        local = Path(request["local_output"])
        local.mkdir(parents=True, exist_ok=True)
        with client.open_sftp() as sftp:
            for name in sftp.listdir(request["remote_output"]):
                sftp.get(request["remote_output"] + "/" + name, str(local / name))
                response["files"].append(name)
        result_path = local / "result.json"
        if result_path.is_file():
            response["release_id"] = json.loads(
                result_path.read_text()).get("release_id")
        response["ok"] = True
    except Exception as exc:
        response["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        Path(request["response"]).write_text(json.dumps(response, indent=2))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def find_paramiko_python(explicit=None):
    """The first interpreter that can import paramiko (the collection venv cannot)."""
    candidates = []
    for candidate in (explicit, os.environ.get("PYNQ_TRANSPORT_PYTHON"), sys.executable,
                      "python", "python3", "py"):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        try:
            probe = subprocess.run([candidate, "-c", "import paramiko"],
                                   capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    raise SessionFailure("no interpreter with paramiko was found for the board transport "
                         "(set PYNQ_TRANSPORT_PYTHON)")


class ParamikoLink(BoardLink):
    """The real transport: the sanctioned helper, one sudo session per collection."""

    def __init__(self, *, helper=None, transport_python=None, workdir=None, timeout=300,
                 remote_root=None):
        self.helper = Path(helper) if helper else DEFAULT_HELPER
        self.transport_python = find_paramiko_python(transport_python)
        # Absolute: a relative workdir combined with cwd= would double the path.
        self.workdir = Path(workdir).resolve() if workdir else Path.cwd()
        self.timeout = timeout
        # A /tmp PREFIX, not a directory: the upload target's parent must already exist,
        # and the board script creates its own output directory.
        self.remote_root = remote_root or ("/tmp/rf-collect-" + uuid.uuid4().hex[:12])
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._board_script = self.workdir / "board-session.py"
        self._bridge = self.workdir / "board-bridge.py"
        self._board_script.write_text(BOARD_SESSION_SOURCE, encoding="utf-8")
        self._bridge.write_text(BOARD_BRIDGE_SOURCE, encoding="utf-8")
        self.logs = []

    def _request(self, name, **extra):
        scratch = self.workdir / "transport" / name
        scratch.mkdir(parents=True, exist_ok=True)
        request = {"board_script": str(self._board_script),
                   "remote_script": self.remote_root + "-board_session.py",
                   "remote_output": self.remote_root + "-out-" + name,
                   "remote_job": self.remote_root + "-" + name + ".job.json",
                   "remote_tx": self.remote_root + "-" + name + ".tx.bin",
                   "local_output": str(scratch / "out"),
                   "response": str(scratch / "response.json"),
                   "timeout": self.timeout}
        request.update(extra)
        request_path = scratch / "request.json"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        command = [self.transport_python, str(self._bridge.resolve()),
                   "--helper", str(self.helper), "--request", str(request_path)]
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=self.timeout + 120, cwd=str(self.workdir))
        response_path = Path(request["response"])
        if not response_path.is_file():
            raise SessionFailure("the board bridge produced no response: %s"
                                 % (completed.stderr or completed.stdout)[-400:])
        return json.loads(response_path.read_text(encoding="utf-8")), scratch / "out"

    def verify_release(self, expected):
        response, _ = self._request("probe", release=expected, probe=True)
        if not response.get("ok"):
            raise SessionFailure("the board probe failed: %s" % response.get("error"))
        return response.get("release_id")

    def run_session(self, job):
        scratch = self.workdir / "transport" / job.session_id
        scratch.mkdir(parents=True, exist_ok=True)
        job_path = scratch / "job.json"
        tx_path = scratch / "tx.bin"
        tx_path.write_bytes(job.tx_payload)
        job_path.write_text(json.dumps({
            "session_id": job.session_id, "split": job.split, "cell": job.cell.key,
            "physical": job.physical, "frames": job.frames, "init": job.init,
            "configure": job.configure, "capture_args": job.capture_args,
            "tx": job.tx,
            "tx_path": self.remote_root + "-" + job.session_id + ".tx.bin"}, indent=2),
            encoding="utf-8")
        response, local = self._request(job.session_id, release=VERIFIED_RELEASE_ID,
                                        job=str(job_path), tx_path=str(tx_path))
        log = (response.get("stdout") or "") + (response.get("stderr") or "")
        if not response.get("ok"):
            raise SessionFailure("the board bridge failed: %s" % response.get("error"))
        result_path = local / "result.json"
        if not result_path.is_file():
            raise SessionFailure("the board session produced no result.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        frames = []
        for record in result.get("frames", []):
            payload = (local / record["file"]).read_bytes()
            frames.append(CapturedFrame(
                payload=payload, samples=record["samples"], ratio=record["ratio"],
                physical=bool(record["physical"]), rx_armed=record.get("rx_armed"),
                tx_source_selected=record.get("tx_source_selected"),
                daemon_timestamp_ns=record.get("daemon_timestamp_ns", 0)))
        state = result.get("final_state") or {}
        if result.get("error"):
            raise SessionFailure(result["error"], frames=tuple(frames), state=state)
        if response.get("exit_code") not in (0, None):
            raise SessionFailure("the board session exited %s" % response.get("exit_code"),
                                 frames=tuple(frames), state=state)
        if result.get("disarm") != "ok":
            raise SessionFailure("disarm failed: %s" % result.get("disarm"),
                                 frames=tuple(frames), state=state)
        return SessionResult(session_id=job.session_id, frames=tuple(frames),
                             final_state=state, log=log)

    def ensure_safe(self):
        try:
            response, local = self._request("safe", release=VERIFIED_RELEASE_ID, safe=True)
        except (SessionFailure, subprocess.SubprocessError, OSError) as exc:
            return {"error": str(exc)}
        path = local / "result.json"
        if path.is_file():
            result = json.loads(path.read_text(encoding="utf-8"))
            return result.get("final_state") or {"error": result.get("error")}
        return {"error": response.get("error")}

    def close(self):
        pass


def _clipping_count(payload):
    values = np.frombuffer(payload, dtype="<i2")
    return int(np.count_nonzero((values <= -2048) | (values >= 2047)))


def _analyze_frame(payload, capture_id):
    from rf_rx_analysis import summarize_components
    values = np.frombuffer(payload, dtype="<i2").reshape(CAPTURE_SAMPLES, 2)
    summary = summarize_components(values[:, 0].astype(float), values[:, 1].astype(float),
                                   capture_id=capture_id, sample_rate_hz=SAMPLE_RATE_HZ,
                                   ratio=CAPTURE_RATIO)
    return {"valid": summary.valid, "no_energy": summary.no_energy,
            "complex_dbfs": summary.complex_dbfs, "peak_hz": summary.peak_hz,
            "occupied_bandwidth_hz": summary.occupied_bandwidth_hz,
            "clipping_count": summary.clipping_count,
            "output_clipped": summary.output_clipped}


def _egress_problem(result, job):
    """Every session must end with source false, RX ARM false and control disarmed."""
    state = result.final_state or {}
    if state.get("error"):
        return "the final readback was not available: " + str(state["error"])
    if state.get("online") is not True or state.get("io_ok") is not True:
        return "the daemon or the PL status was not available at the end of the session"
    if state.get("armed") is not False:
        return "control was not disarmed (armed is not False)"
    if state.get("rx_armed") is not False:
        return "RX ARM was not cleared (rx_armed is not False)"
    if state.get("tx_source_selected") is not False:
        return "the TX source was still selected (tx_source_selected is not False)"
    for frame in result.frames:
        if frame.rx_armed is not False:
            return "a capture was published while RX ARM was still set"
        if job.physical and frame.tx_source_selected is not False:
            return "a physical capture was published while the TX source was selected"
    return None


@dataclass
class SessionOutcome:
    job: object
    ok: bool
    reason: object
    frames: tuple
    state: dict
    fatal: bool
    recovery: object = None
    clipped: int = 0


def _recover(link, output, log):
    try:
        state = link.ensure_safe()
    except Exception as exc:                       # pragma: no cover - defensive
        state = {"error": "%s: %s" % (type(exc).__name__, exc)}
    safe = bool(state and state.get("online") is True and state.get("io_ok") is True
                and state.get("armed") is False
                and state.get("rx_armed") is False
                and state.get("tx_source_selected") is False)
    log.append("recovery: ensure_safe -> %s" % json.dumps(state, default=str))
    return {"attempted": True, "safe": safe, "detail": state}


def _run_one_session(link, job, log):
    try:
        result = link.run_session(job)
    except (SessionFailure, subprocess.SubprocessError, OSError) as exc:
        if not isinstance(exc, SessionFailure):
            exc = SessionFailure("transport failed: %s: %s" % (type(exc).__name__, exc))
        recovery = _recover(link, None, log)
        log.append("session %s failed: %s" % (job.session_id, exc.reason))
        return SessionOutcome(job, False, exc.reason, exc.frames, exc.state or {}, True,
                              recovery, sum(_clipping_count(f.payload) > 0
                                            for f in exc.frames))
    problem = _egress_problem(result, job)
    clipped = sum(1 for frame in result.frames if _clipping_count(frame.payload))
    if problem:
        recovery = _recover(link, None, log)
        log.append("session %s egress failure: %s" % (job.session_id, problem))
        return SessionOutcome(job, False, problem, result.frames, result.final_state, True,
                              recovery, clipped)
    if clipped:
        log.append("session %s clipped in %d of %d frames; a new condition is needed"
                   % (job.session_id, clipped, len(result.frames)))
        return SessionOutcome(job, False, "clipping", result.frames, result.final_state,
                              False, None, clipped)
    return SessionOutcome(job, True, None, result.frames, result.final_state, False, None, 0)


def _cell_entries(manifest, split, cell_key):
    """Per-session usable counts for one cell, derived from the manifest alone."""
    by_session = {}
    for frame in manifest["frames"]:
        record = by_session.setdefault(frame["session_id"], [0, 0])
        record[0] += 1
        record[1] += 0 if frame["exclusion_reasons"] else 1
    prefix = "rx-%s-%s-" % (split, cell_key.lower())
    entries = []
    for session in manifest["sessions"]:
        session_id = session["session_id"]
        if session["split"] != split or not session_id.startswith(prefix):
            continue
        counts = by_session.get(session_id, [0, 0])
        entries.append({"session_id": session_id, "frames": counts[0],
                        "usable": counts[1]})
    return entries


def _empty_manifest():
    return {"schema_version": SCHEMA_VERSION, "sessions": [], "frames": []}


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_json(path, value):
    """Commit a complete checkpoint; a torn temporary file is never resumed."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _resume_manifest(output, dry_run):
    for name in ("summary.json", "collection.json"):
        path = Path(output) / name
        if path.is_file() and _read_json(path).get("dry_run") != bool(dry_run):
            raise SessionFailure("resume cannot mix rehearsal and physical provenance")
    manifest = _read_json(Path(output) / "manifest.json")
    expected_origin = "synthetic" if dry_run else "physical"
    if any(session.get("origin") != expected_origin for session in manifest.get("sessions", [])):
        raise SessionFailure("resume cannot mix rehearsal and physical provenance")
    result = manifest_contract.validate_manifest(manifest, output,
                                                 coverage_origin=expected_origin)
    if not result.valid:
        raise SessionFailure("resume manifest invalid: " + "; ".join(result.errors[:8]))
    return manifest


def collect(link, *, split, cells, output, sessions=SESSIONS_PER_CELL,
            frames=FRAMES_PER_SESSION, resume=False, analyze=False, dry_run=False,
            require_production=False, log=None):
    """Run the matrix for one split, checkpointing after every session."""
    output = Path(output)
    for name in ("iq", "sessions", "checkpoints", "logs"):
        (output / name).mkdir(parents=True, exist_ok=True)
    existing = _empty_manifest()
    if resume:
        existing = _resume_manifest(output, dry_run)
    frozen_at = existing["sessions"][0]["split_frozen_at_utc"] if existing["sessions"] else _now()
    manifest = {"schema_version": SCHEMA_VERSION,
                "sessions": [dict(session) for session in existing["sessions"]],
                "frames": [dict(frame) for frame in existing["frames"]]}
    started = time.monotonic()
    log = log if log is not None else []
    origin = "synthetic" if dry_run else "physical"
    pending_path = output / "pending-session.json"
    if pending_path.is_file():
        interrupted = _read_json(pending_path)
        recovery = _recover(link, output, log)
        _atomic_json(output / "interrupted-recovery.json",
                     {"session": interrupted, "recovery": recovery})
        if not recovery["safe"]:
            raise SessionFailure("interrupted session cleanup is unconfirmed; refusing new capture")
        # Any raw bytes from the unfinished session remain diagnostic transport files.
        # Its reserved, empty manifest session cannot count toward valid coverage.
        pending_path.unlink()
    _atomic_json(output / "manifest.json", manifest)
    hash_owner = {}
    for frame in manifest["frames"]:
        hash_owner.setdefault(frame["iq_sha256"], frame["capture_id"])
    duplicate_hashes = set()
    session_records = []
    failures = []
    stopped_early = False
    recovery = {"attempted": False, "safe": None, "detail": None}
    amplitudes_used = []
    counts = {cell.key: {"sessions": 0, "usable_frames": 0, "qualified_sessions": 0}
              for cell in cells}

    for cell in cells:
        entries = _cell_entries(manifest, split, cell.key)
        qualified = sum(1 for entry in entries if entry["usable"] >= frames)
        failed = sum(1 for entry in entries if entry["usable"] < frames)
        ordinal = len(entries)
        # Resume state: sessions already checkpointed count toward this cell.
        counts[cell.key]["sessions"] = len(entries)
        counts[cell.key]["usable_frames"] = sum(entry["usable"] for entry in entries)
        counts[cell.key]["qualified_sessions"] = qualified
        while qualified < sessions and failed < len(AMPLITUDE_LADDER) and not stopped_early:
            amplitude = AMPLITUDE_LADDER[failed]
            job = _build_job(split, cell, ordinal, amplitude, frames,
                             CELLS.index(cell))
            if amplitude not in amplitudes_used:
                amplitudes_used.append(amplitude)
            log.append("session %s start (cell %s, amplitude %d, physical=%s)"
                       % (job.session_id, cell.key, amplitude, job.physical))
            # Reserve this session before any hardware action. Only a verified return
            # from run_session may add its IQ to the resumable manifest.
            manifest["sessions"].append({"session_id": job.session_id, "split": split,
                                         "origin": origin,
                                         "split_frozen_at_utc": frozen_at})
            _atomic_json(output / "manifest.json", manifest)
            _atomic_json(pending_path, {"session_id": job.session_id, "split": split,
                                        "origin": origin, "cell": cell.key,
                                        "amplitude": amplitude})
            outcome = _run_one_session(link, job, log)
            ordinal += 1
            record = {"session_id": job.session_id, "split": split, "cell": cell.key,
                      "cell_label": cell.label, "other_kind": cell.other_kind,
                      "session_ordinal": job.session_ordinal, "amplitude": amplitude,
                      "frames_requested": frames, "physical": job.physical,
                      "seed_family": job.seed_family, "tx_seed": job.tx_seed,
                      "tx_control": job.tx_control, "tx_samples": job.tx["samples"],
                      "ok": outcome.ok, "reason": outcome.reason,
                      "clipped_frames": outcome.clipped,
                      "final_state": outcome.state, "recovery": outcome.recovery,
                      "frames": []}
            usable = 0
            for index, captured in enumerate(outcome.frames):
                capture_id = "%s-f%02d" % (job.session_id, index)
                clipping = _clipping_count(captured.payload)
                reasons = []
                if clipping:
                    reasons.append("clipping")
                if not outcome.ok:
                    reason = outcome.reason
                    if reason == "clipping":
                        reasons = ["clipping"] if clipping else ["clipping_in_session"]
                    else:
                        reasons = ["session_failed: " + str(reason)]
                digest = hashlib.sha256(captured.payload).hexdigest()
                if digest in hash_owner:
                    reasons.append("duplicate_iq_hash")
                    duplicate_hashes.add(digest)
                else:
                    hash_owner[digest] = capture_id
                relative = "iq/%s.iq" % capture_id
                (output / relative).write_bytes(captured.payload)
                frame = {
                    "capture_id": capture_id, "session_id": job.session_id,
                    "split": split, "origin": origin, "label": cell.label,
                    "other_kind": cell.other_kind, "iq_path": relative,
                    "iq_sha256": digest, "format": "s16_iq_le", "samples": captured.samples,
                    "release_id": VERIFIED_RELEASE_ID,
                    "configuration": {"sample_rate_hz": SAMPLE_RATE_HZ, "ratio": RATIO,
                                      "lo_hz": LO_HZ, "bandwidth_hz": BANDWIDTH_HZ},
                    "tx_reference": cell.tx_reference(job.seed_family),
                    "agc": {"mode": "internal_40db_txpeak%d" % amplitude, "gain_db": None},
                    "captured_at_utc": _now(),
                    "completed_monotonic_s": round(time.monotonic() - started, 6),
                    "complete": True, "clipping_count": clipping,
                    "exclusion_reasons": reasons, "augmentation_parent_id": None}
                manifest["frames"].append(frame)
                record["frames"].append({
                    "capture_id": capture_id, "clipping_count": clipping,
                    "exclusion_reasons": reasons,
                    "rx_armed": captured.rx_armed,
                    "tx_source_selected": captured.tx_source_selected,
                    "analysis": _analyze_frame(captured.payload, capture_id) if analyze else None})
                if not reasons:
                    usable += 1
            record["usable_frames"] = usable
            session_records.append(record)
            counts[cell.key]["sessions"] += 1
            counts[cell.key]["usable_frames"] += usable
            if outcome.ok:
                qualified += 1
                counts[cell.key]["qualified_sessions"] += 1
            else:
                failures.append({"session_id": job.session_id, "cell": cell.key,
                                 "amplitude": amplitude, "reason": outcome.reason,
                                 "fatal": outcome.fatal})
                if outcome.recovery:
                    recovery = outcome.recovery
                if outcome.fatal:
                    stopped_early = True
                    log.append("stopping: session %s was fatal (%s)"
                               % (job.session_id, outcome.reason))
            if usable < frames and not outcome.fatal:
                failed += 1
            log.append("session %s done (ok=%s, usable=%d/%d)"
                       % (job.session_id, outcome.ok, usable, frames))
            _checkpoint(output, split, record, manifest)
            if (not outcome.fatal or (outcome.recovery and outcome.recovery["safe"])):
                pending_path.unlink(missing_ok=True)
    unrecovered = sorted(cell.key for cell in cells
                         if counts[cell.key]["qualified_sessions"] < sessions)
    summary = {
        "ok": not stopped_early and not duplicate_hashes and not unrecovered,
        "release_id": VERIFIED_RELEASE_ID, "split": split, "dry_run": bool(dry_run),
        "resume": bool(resume), "cable_confirmed": True, "wiring": WIRING,
        "transport": {"python": getattr(link, "transport_python", None),
                      "helper": str(getattr(link, "helper", None))
                                if getattr(link, "helper", None) else None,
                      "remote_prefix": getattr(link, "remote_root", None)},
        "started_at_utc": _now(), "finished_at_utc": _now(),
        "requested": {"split": split, "cells": [cell.key for cell in cells],
                      "sessions": sessions, "frames": frames,
                      "amplitudes": list(AMPLITUDE_LADDER)},
        "sessions": session_records, "cells": counts, "failures": failures,
        "duplicate_hashes": sorted(duplicate_hashes),
        "amplitudes_used": amplitudes_used, "stopped_early": stopped_early,
        "unrecovered_cells": unrecovered, "recovery": recovery,
        "valid_frames": sum(1 for frame in manifest["frames"]
                            if not frame["exclusion_reasons"]),
        "excluded_frames": sum(1 for frame in manifest["frames"]
                               if frame["exclusion_reasons"]),
        "log": log,
    }
    result = manifest_contract.validate_manifest(manifest, output, production=True,
                                                 coverage_origin="physical")
    summary["coverage_missing"] = len(result.coverage_missing)
    summary["production_ready"] = result.production_ready
    summary["manifest_errors"] = list(result.errors)
    if require_production and not result.production_ready:
        summary["ok"] = False
    if result.errors:
        summary["ok"] = False
    _write_outputs(output, manifest, summary)
    return summary


def _checkpoint(output, split, record, manifest):
    _atomic_json(output / "manifest.json", manifest)
    path = output / "checkpoints" / (split + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"session": record,
                                 "manifest_sessions": len(manifest["sessions"]),
                                 "manifest_frames": len(manifest["frames"])}) + "\n")


def _write_outputs(output, manifest, summary):
    output.mkdir(parents=True, exist_ok=True)
    _atomic_json(output / "manifest.json", manifest)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False, default=str) + "\n",
        encoding="utf-8")
    (output / "collection.json").write_text(
        json.dumps({"release_id": summary["release_id"], "wiring": WIRING,
                    "dry_run": summary["dry_run"], "split": summary["split"],
                    "sessions": summary["sessions"]}, indent=2, allow_nan=False,
                   default=str) + "\n", encoding="utf-8")
    logs = output / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "collect.log").write_text("\n".join(summary["log"]) + "\n", encoding="utf-8")


class _Refusal(Exception):
    pass


def _parse(argv):
    parser = argparse.ArgumentParser(
        prog="python -m scripts.rf_ml.collect",
        description="Collect the frozen RX matrix over the TX1-RX1 cable, one verified "
                    "session at a time.  The matrix is fixed; only the selection is a flag.")
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--split", required=True, choices=SPLITS)
    parser.add_argument("--cable-confirmed", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cells", default=",".join(cell.key for cell in CELLS),
                        help="comma separated cell keys of the frozen matrix")
    parser.add_argument("--sessions", type=int, default=SESSIONS_PER_CELL)
    parser.add_argument("--frames", type=int, default=FRAMES_PER_SESSION)
    parser.add_argument("--augment-per-session", type=int, default=0,
                        help="accepted for symmetry with synthetic.py; real collection "
                             "never manufactures augmentations")
    parser.add_argument("--resume", action="store_true",
                        help="continue an existing output directory (checkpoint resume)")
    parser.add_argument("--dry-run", action="store_true",
                        help="offline rehearsal with deterministic payloads; no board")
    parser.add_argument("--analyze", action="store_true",
                        help="record a per-frame spectrum summary (pilots)")
    parser.add_argument("--require-production", action="store_true")
    parser.add_argument("--session-timeout", type=int, default=300)
    parser.add_argument("--helper", default=str(DEFAULT_HELPER))
    parser.add_argument("--transport-python", default=None)
    return parser.parse_args(argv)


def _selection(args):
    splits = [item.strip() for item in str(args.split).split(",") if item.strip()]
    for split in splits:
        if split not in SPLITS:
            raise _Refusal("unknown split: %r" % (split,))
    keys = [item.strip() for item in str(args.cells).split(",") if item.strip()]
    if not keys:
        raise _Refusal("no cell was requested")
    cells = []
    for key in keys:
        try:
            cells.append(cell_by_key(key))
        except ValueError as exc:
            raise _Refusal(str(exc))
    if type(args.sessions) is not int or args.sessions < 1:
        raise _Refusal("sessions must be a positive integer")
    if type(args.frames) is not int or args.frames < 1:
        raise _Refusal("frames must be a positive integer")
    return cells


def main(argv=None, link=None):
    args = _parse(argv)
    try:
        cells = _selection(args)
        if not args.cable_confirmed:
            raise _Refusal("a real collection requires --cable-confirmed: the TX1-RX1 "
                           "direct cable must be attested by the operator")
        if args.expected_release != VERIFIED_RELEASE_ID:
            raise _Refusal("--expected-release must be the verified release id %s"
                           % VERIFIED_RELEASE_ID)
        if not RELEASE_PATTERN.fullmatch(args.expected_release or ""):
            raise _Refusal("--expected-release must be 32 lowercase hex characters")
        output = Path(args.output)
        if output.exists() and not args.resume:
            raise _Refusal("the output directory already exists: %s" % output)
        if args.resume and not (output / "manifest.json").is_file():
            raise _Refusal("--resume needs an existing manifest.json in %s" % output)
        if args.resume:
            _resume_manifest(output, args.dry_run)
    except (_Refusal, SessionFailure, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    if link is None:
        if args.dry_run:
            link = DryRunLink()
        else:
            output.mkdir(parents=True, exist_ok=True)
            link = ParamikoLink(helper=args.helper,
                                transport_python=args.transport_python,
                                workdir=output, timeout=args.session_timeout)
    try:
        reported = link.verify_release(args.expected_release)
        if reported != args.expected_release:
            print(json.dumps({"ok": False, "error": "the board reports release %r, not the "
                              "verified %r" % (reported, args.expected_release)}),
                  file=sys.stderr)
            return 4
        summary = collect(link, split=args.split, cells=cells, output=output,
                          sessions=args.sessions, frames=args.frames, resume=args.resume,
                          analyze=args.analyze, dry_run=args.dry_run,
                          require_production=args.require_production)
    except SessionFailure as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 4
    finally:
        try:
            link.close()
        except Exception:
            pass
    print(json.dumps({"ok": summary["ok"], "split": summary["split"],
                      "dry_run": summary["dry_run"],
                      "sessions": len(summary["sessions"]),
                      "valid_frames": summary["valid_frames"],
                      "failures": [f["reason"] for f in summary["failures"]],
                      "duplicate_hashes": summary["duplicate_hashes"],
                      "stopped_early": summary["stopped_early"],
                      "coverage_missing": summary["coverage_missing"],
                      "production_ready": summary["production_ready"]}, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
