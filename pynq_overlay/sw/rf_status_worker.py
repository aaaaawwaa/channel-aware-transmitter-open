"""Background RF status polling, arbitration and immutable result handoff.

Three things live on this one worker, and they all run OFF the touch/draw thread:

1. **Telemetry polling** -- one immutable `RfStatusSnapshot` at a time.
2. **Control arbitration** -- one active control job plus one idempotent queued STOP.
3. **RX capture** -- a finite `rx.capture` (or the explicitly confirmed
   `loopback.physical.capture`) PLUS the whole windowed-FFT analysis.  The panel's input
   path only ever calls `submit_capture`; the socket round trip and the FFT happen on the
   worker's own `rf-rx-capture` thread, and the main loop receives an already-frozen
   result through `take_rx_result`.

Control and capture are serialized against each other: the worker accepts one job at a
time, and STOP stays reachable while either is running (a "can't stop" panel is worse than
a request the daemon refuses).

Cancellation is COOPERATIVE and deliberately not immediate.  The in-flight request owns
the client's I/O lock, so `cancel_capture` only marks the request; once the request
returns (or the client's 3 s capture deadline expires) close-and-readback takes priority
and the cancelled result is discarded.  A release the readback cannot confirm is reported
as `close_confirmed=False` with a "关闭未确认" reason -- never as "stopped".

The 30 s IQ-snapshot and 2 s hardware-telemetry windows are NOT declared here: they are
imported from `rf_rx_analysis`, the module that owns them (see its freshness note).
"""
from __future__ import annotations

import threading
import time
import importlib
import copy
import hashlib
import json
from pathlib import Path

import rf_rx_analysis
from rf_rx_analysis import (IQ_SNAPSHOT_FRESHNESS_S, TELEMETRY_FRESHNESS_S,
                            snapshot_is_fresh, summarize, telemetry_is_fresh)
from rf_status_client import RfStatusClient, RfStatusSnapshot
from rf_tx_waveform import build_tx_waveform
from rf_rx_types import RxModelResult
# The capture vocabulary is NOT declared here: `ui_model` owns the state strings the panel
# draws -- including the in-flight tuple `rx_capture_inflight` matches on -- and this module
# imports them so the two can never drift.  A second declaration is how a rename in one file
# silently breaks the panel's "a capture is in flight" detection.
from ui_model import (RX_CAPTURE_CANCELLED, RX_CAPTURE_CANCELLING, RX_CAPTURE_DONE,
                      RX_CAPTURE_ERROR, RX_CAPTURE_IDLE, RX_CAPTURE_QUEUED,
                      RX_CAPTURE_RUNNING)

# This one IS this module's own: it is worker evidence attached to a cancelled capture,
# not a state the panel draws.  "stopped" is deliberately not a state at all.
CLOSE_UNCONFIRMED = "关闭未确认"


def read_control_token(path):
    """Read the operator-provisioned control token; return (token, reason).

    There is NO built-in token and no default path fallback: `path` is whatever the
    operator typed (the UI only hints `ui_model.RF_CONTROL_TOKEN_PATH`).  A missing,
    unreadable or empty file is fail-closed -- the caller must not arm with nothing.
    The token is returned to the caller and never stored here.
    """
    if not path or not isinstance(path, str):
        return None, "no control token path was provided"
    try:
        with open(path, "r", encoding="ascii") as handle:
            token = handle.read(4096).strip()
    except OSError as exc:
        return None, "control token file unavailable (%s)" % type(exc).__name__
    except UnicodeError:
        return None, "control token file is not ASCII"
    if not token:
        return None, "control token file is empty"
    return token, None


# Both freshness windows and both predicates are RE-EXPORTED here on purpose: this
# module is the panel's single RF import, and the 30 s / 2 s values must never be typed a
# second time (rf_rx_analysis stays their one owner -- see its freshness note).
__all__ = ["RfStatusWorker", "read_control_token", "rf_rx_analysis",
           "IQ_SNAPSHOT_FRESHNESS_S", "TELEMETRY_FRESHNESS_S", "snapshot_is_fresh",
           "telemetry_is_fresh", "summarize"]

# Explicit operator arguments are copied at submission; no RF defaults.
PARAMETERLESS_OPS = {"stop": "tx.stop", "start": "tx.start"}

# Actions whose daemon op takes arguments.  The worker does not build those arguments
# and will not invent any: the caller must supply them, and the daemon re-validates the
# contract anyway.  Without arguments the call refuses locally (fail-closed) instead of
# sending an incomplete request that the daemon would reject for a confusing reason.
OP_WITH_ARGS = {"init": "rf.init", "configure": "rf.configure"}


def _load_release_module(directory, name):
    """Import one module from the release directory by explicit path.

    Never `importlib.import_module`: that resolves through `sys.path`, so a
    different copy of the same file could be imported and only compared
    afterwards.  Loading the attested path makes "this is the release's file"
    structural instead of a check that a later `sys.path` change could outrun.
    """
    import importlib.util
    path = Path(directory) / (name + '.py')
    if path.is_symlink() or not path.is_file():
        raise ValueError('发布内缺少普通文件: ' + name + '.py')
    spec = importlib.util.spec_from_file_location('release_' + name, str(path))
    if spec is None or spec.loader is None:
        raise ValueError('无法从发布加载: ' + name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RfStatusWorker:
    def __init__(self, client=None, poll_interval=0.5,
                 stale_after=TELEMETRY_FRESHNESS_S, release_directory=None,
                 experiment_root=None):
        if poll_interval <= 0 or stale_after <= 0:
            raise ValueError("poll interval and expiry must be positive")
        self.client = client if client is not None else RfStatusClient()
        self.poll_interval = float(poll_interval)
        # The 2 s hardware-telemetry window is not re-typed here: it is the constant
        # `rf_rx_analysis` declares, which `pynq_status.RF_STALE_AFTER` also equals (a
        # criterion in test_rf_rx_analysis.py pins all three together).
        self.stale_after = float(stale_after)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._latest = RfStatusSnapshot(error="RF daemon has not been polled")
        self._thread = None
        self._control_thread = None
        self._control_running = None
        self._queued_stop = None
        self._control_results = []
        # RX: one capture job, its cancellation flag, and the frozen handoff the drawing
        # thread takes.  `_capture_request` is the "a job is in flight" indicator.
        self._capture_thread = None
        self._capture_request = None
        self._capture_cancel = False
        self._rx_handoff = None
        # Every published RX status carries this monotonic number.  The panel applies the
        # worker's status EVERY frame, so it needs to know whether the worker actually
        # moved: a local refusal (or a configuration that changed under an in-flight
        # capture) must not be undone by re-applying a status it has already seen.
        self._rx_status_seq = 0
        self._rx_status = self._idle_rx_status()
        self._release_directory = release_directory
        self._model_lock = threading.Lock()
        self._model_checked = False
        self._model = None
        self._model_info = {"available": False, "production": False,
                            "reason": "无模型", "release_id": None,
                            "model_sha256": None, "model_version": None,
                            "preprocessing_version": None}
        # Additive 4-class recognition.  Its own lock/info; it never replaces the
        # six-class model, environment, demod or any hardware-decision path.
        self._modrec4_lock = threading.Lock()
        self._modrec4_checked = False
        self._modrec4 = None
        self._modrec4_info = {"available": False, "release_id": None,
                              "reason": "四类扩展未接入"}
        self._demod_lock = threading.Lock()
        self._demod_checked = False
        self._demod_pipeline = None
        self._qpsk_pipeline = None
        self._rrc_pipeline = None
        self._qam16_pipeline = None
        self._qam64_pipeline = None
        self._demod_info = {"available": False, "reason": "解调未接入",
                            "release_id": None, "algorithm_version": None,
                            "algorithm_sha256": None, "qpsk_available": False,
                            "qpsk_algorithm_version": None,
                            "qpsk_algorithm_sha256": None,
                            "qpsk_reason": "QPSK解调未接入",
                            "rrc_available": False,
                            "rrc_algorithm_version": None,
                            "rrc_algorithm_sha256": None,
                            "rrc_reason": "RRC解调未接入",
                            "qam16_available": False,
                            "qam16_algorithm_version": None,
                            "qam16_algorithm_sha256": None,
                            "qam16_reason": "QAM16解调未接入",
                            "qam64_available": False,
                            "qam64_algorithm_version": None,
                            "qam64_algorithm_sha256": None,
                            "qam64_reason": "QAM64超出本次交付支持范围"}
        self._teaching_lock = threading.Lock()
        self._teaching_checked = False
        self._constellation_module = self._record_module = None
        self._teaching_info = dict(available=False, release_id=None, module_sha256={},
                                   reason='教学模块未接入')
        self._experiment_root = Path(experiment_root or '/home/xilinx/experiments')
        # Research evidence is deliberately NOT written into the teaching record
        # directory: a research frame has no teaching identity, so mixing them
        # would put frames no teaching reader can parse into the one place they
        # all read.
        self._headroom_root = self._experiment_root.parent / 'headroom'
        self._save_thread = None
        self._save_handoff = None
        self._environment_lock = threading.Lock()
        self._environment_checked = False
        self._environment_module = self._scenario_module = None
        self._environment_baseline = None
        self._environment_info = dict(available=False, release_id=None,
            module_sha256={}, baseline_sha256=None, environment_version=None,
            reason='环境诊断未接入')
        # HEADROOM-1.  The release attestation is read once; the LIVE capability
        # is refreshed from every poll, because a daemon that was restarted onto
        # an older build must stop the panel offering a study it cannot run.
        self._headroom_lock = threading.Lock()
        self._headroom_checked = False
        self._headroom_module = None
        self._headroom_replay_module = None
        # One study per process, run on its own thread.  `_headroom_receipt` is
        # the delivery slot the panel drains once; `_headroom_used` is the
        # durable fact that a study was actually attempted, which is what makes
        # a second run a refusal rather than a request the daemon has to reject.
        self._headroom_running = False
        self._headroom_thread = None
        self._headroom_receipt = None
        self._headroom_used = False
        self._headroom_cancel = threading.Event()
        self._headroom_info = dict(available=False, release_id=None,
            module_sha256={}, live=None, reason='余量研究未接入')

    # ------------------------------------------------------------- HEADROOM-1
    def headroom_status(self):
        """Release attestation plus the last live capability; no I/O here."""
        with self._headroom_lock:
            return copy.deepcopy(self._headroom_info)

    def _ensure_headroom(self):
        """Attest the installed research module once; never run or arm anything."""
        with self._headroom_lock:
            if self._headroom_checked:
                return
            info = dict(self._headroom_info)
            try:
                if self._release_directory is not None:
                    import merged_release
                    directory = Path(self._release_directory).resolve(strict=True)
                    manifest = merged_release.verify_release(directory, require_software=True)
                    info['release_id'] = manifest['release_id']
                    assert manifest['software_contract'] in ('cnn-bpsk-headroom-v1',)
                    # Loaded BY PATH, not by name.  `import rf_headroom` resolves
                    # through sys.path, so a different copy of the file could be
                    # imported and then merely compared -- and a research module
                    # that decides whether a study may start is worth loading
                    # from the file the release manifest attested.
                    module = _load_release_module(directory, 'rf_headroom')
                    self._headroom_module = module
                    # The offline replay travels in the same release and is
                    # loaded the same way: it decides whether an OFF capture was
                    # really OFF, so it is not a module worth resolving through
                    # `sys.path` either.
                    self._headroom_replay_module = _load_release_module(
                        directory, 'rf_headroom_replay')
                    info.update(available=True, reason='余量研究合同已接入发布',
                        module_sha256={name: manifest['software'][name]
                                       for name in ('rf_headroom.py',
                                                    'rf_headroom_replay.py')})
            except Exception as exc:
                self._headroom_module = None
                self._headroom_replay_module = None
                info.update(available=False, reason='余量研究不可用: ' + str(exc))
            self._headroom_info = info
            self._headroom_checked = True

    def note_headroom_capability(self, snapshot):
        """Land the live `device.info` capability from the poll that just ran.

        Called on the poll thread.  `live` is whatever the daemon announced --
        the panel decides with `rf_headroom.capability_available`, so no part of
        this path can promote an unannounced contract into an available one.
        """
        with self._headroom_lock:
            info = dict(self._headroom_info)
            announced = getattr(snapshot, 'headroom', None)
            version = getattr(snapshot, 'daemon_version', None)
            live = None
            if isinstance(announced, dict):
                live = dict(announced, daemon_version=version, available=True)
            info['live'] = live
            self._headroom_info = info

    def headroom_run(self, session_id, cable_confirmed=False, cancelled=None,
                     recorder=None, off_guard=None, require_hardware=True,
                     evidence_root=None, token_path=None, token=None):
        """Run one bounded study; never retried, never started by a poll.

        The caller is the panel's explicit research action.  Everything the
        controller needs comes from the verified client and the attested module;
        a refusal or a transport failure simply returns a receipt that says so.

        With `evidence_root` the production recorder and the saved-IQ replay are
        supplied: the intent, the raw frame, the replay and the decision are
        written to disk, and an OFF capture that synchronized stops the study.
        Without it the controller runs with no recorder -- which is what a caller
        that only wants the transaction asks for, and is never the panel's path.
        """
        self._ensure_headroom()
        status = self.headroom_status()
        # The client builds the research frames and the controller checks them.
        # Handing the client the module this worker loaded from the attested
        # release makes the frame's class ONE object instead of two copies of the
        # same file, which is otherwise a refusal with nothing to do with the
        # contract.  Done here, at the point of use, so the hand-off cannot be
        # missed by a client that was attached after attestation.
        if hasattr(self.client, 'use_research_module'):
            self.client.use_research_module(self._headroom_module)
        if self._headroom_module is None or not status['available']:
            return dict(completed=False, stopped_at='UNAVAILABLE',
                        reason=status['reason'], attempts=0, frames=[],
                        disarmed=False, close_ack=None)
        if token is None:
            token, why = read_control_token(token_path)
            if token is None:
                return dict(completed=False,stopped_at='CAPABILITY',reason=why,
                            attempts=0,frames=[],disarmed=False,close_ack=None)
        if recorder is None and evidence_root is not None:
            recorder = self._headroom_module.HeadroomRecorder(
                evidence_root, session_id)
        if self._headroom_replay_module is not None:
            if off_guard is None:
                off_guard = self._off_guard(session_id, status['release_id'])
            replayer = self._replayer(session_id, status['release_id'])
        else:
            replayer = None
        controller = self._headroom_module.HeadroomController(
            self.client, recorder=recorder, off_guard=off_guard,
            cancelled=cancelled, require_hardware=require_hardware,
            replayer=replayer)
        try:
            return controller.run(session_id, cable_confirmed=cable_confirmed, token=token)
        except self._headroom_module.HeadroomStopped as exc:
            # The controller refuses before anything is sent when the daemon's
            # own capability reply does not name this contract.  That is a
            # refusal to start, not a study that ran: no attempt was made.
            return dict(completed=False, stopped_at='CAPABILITY', reason=str(exc),
                        attempts=0, frames=[], disarmed=False, close_ack=None)

    def _replay_summary(self, session_id, release_id, attempt, evidence):
        """Replay one attempt's saved bytes; the ONE place a replay is run."""
        replay = self._headroom_replay_module
        return replay.replay(
            evidence['payload'], 'replay:%s/%d' % (session_id, attempt),
            release_id,
            replay.rf_config_from_readback(evidence['actual_rf_after']),
            request_id=evidence['request_id'],
            timestamp_ns=evidence['timestamp_ns'])

    def _replayer(self, session_id, release_id):
        def replay(attempt, evidence):
            return self._replay_summary(session_id, release_id, attempt, evidence)
        return replay

    def _off_guard(self, session_id, release_id):
        """The production OFF guard: a replayed OFF frame must show no source.

        It is the same replay the record keeps, so "the OFF capture was clean" is
        a statement about the bytes that were saved rather than about a header
        field the daemon wrote.
        """
        stopped = self._headroom_module.HeadroomStopped

        def guard(attempt, evidence):
            summary = evidence.get('replay')
            if summary is None:
                raise stopped('OFF replay was not persisted')
            why = self._headroom_replay_module.off_violation(summary)
            if why is not None:
                raise stopped(why)
        return guard

    def submit_headroom_study(self, session_id, cable_confirmed=False,
                              evidence_root=None, token_path=None):
        """Start one bounded study on its own thread; returns immediately.

        The study is a long, blocking transaction -- five attempts over one
        retained connection -- so it must never run on the panel's input or
        render thread.  One study per process: a second submission is refused
        here, and the daemon would refuse it too (its budget is consumed by the
        first `headroom.begin`).

        The evidence root defaults to this worker's own research directory, which
        is not the teaching record directory.
        """
        with self._lock:
            if self._headroom_running:
                return {"ok": False, "code": "BUSY",
                        "message": "余量研究正在进行"}
            if cable_confirmed is not True:
                return {'ok':False,'code':'NOT_CONFIRMED','message':'请明确确认 TX1→RX1 接线'}
            if self._capture_request is not None or self._control_running is not None:
                return {'ok':False,'code':'BUSY','message':'RF worker busy'}
            if self._headroom_used:
                return {"ok": False, "code": "DONE",
                        "message": "本进程已完成一次余量研究, 不能重跑"}
            if self._stop.is_set():
                return {"ok": False, "code": "STOPPED", "message": "RF worker stopped"}
            self._headroom_running = True
            self._headroom_cancel.clear()
            self._headroom_thread = threading.Thread(
                target=self._run_headroom, name="rf-headroom", daemon=True,
                args=(session_id, cable_confirmed,
                      evidence_root if evidence_root is not None
                      else self._headroom_root, token_path))
            self._headroom_thread.start()
        return {"queued": True}

    # A study that never reached the daemon consumed nothing, so it is not a
    # reason to refuse the next one; everything else is.
    _HEADROOM_NOT_ATTEMPTED = ('UNAVAILABLE', 'CAPABILITY', 'STOPPED')

    def _run_headroom(self, session_id, cable_confirmed, evidence_root, token_path):
        try:
            receipt = self.headroom_run(session_id, cable_confirmed=cable_confirmed,
                                        evidence_root=evidence_root, token_path=token_path,
                                        cancelled=self._headroom_cancel.is_set)
        except Exception as exc:                # noqa: BLE001 - reported, not raised
            receipt = getattr(exc,'headroom_receipt',None)
            if type(receipt) is not dict:
                receipt = dict(completed=False, stopped_at='CRASHED',
                               reason=str(exc) or type(exc).__name__, attempts=0,
                               frames=[], disarmed=False, close_ack=None)
            if hasattr(exc,'evidence_write_error'):
                receipt = dict(receipt,evidence_write_error=exc.evidence_write_error)
        with self._lock:
            self._headroom_running = False
            self._headroom_receipt = receipt
            # `headroom.begin` consumes the daemon's one initialization, and the
            # daemon sets `used` for the rest of the process -- so a study that
            # began at all is terminal here too.
            if receipt.get('stopped_at') not in self._HEADROOM_NOT_ATTEMPTED:
                self._headroom_used = True

    def take_headroom_result(self):
        """The finished study's receipt, once; None while none has finished.

        Draining this does NOT re-open the study: the durable refusal is
        `_headroom_used`, not the presence of a receipt nobody has read yet.
        """
        with self._lock:
            receipt, self._headroom_receipt = self._headroom_receipt, None
            return receipt

    def headroom_study_status(self):
        with self._lock:
            return dict(running=bool(self._headroom_running))

    def cancel_headroom_study(self):
        self._headroom_cancel.set()
        return {'requested':True,'message':'已请求取消余量研究，等待关闭确认'}

    def environment_status(self):
        with self._lock:
            return copy.deepcopy(self._environment_info)

    def _ensure_environment(self):
        """Load only complete, release-local S2 code and its attested calibration."""
        with self._environment_lock:
            if self._environment_checked:
                return
            info = dict(self._environment_info)
            try:
                if self._release_directory is not None:
                    import merged_release
                    directory = Path(self._release_directory).resolve(strict=True)
                    manifest = merged_release.verify_release(directory, require_software=True)
                    info['release_id'] = manifest['release_id']
                    if manifest.get('software_contract') in ('cnn-bpsk-environment-v1','cnn-bpsk-headroom-v1','cnn-bpsk-modrec4-v1','cnn-bpsk-modrec4-qpsk-v1','cnn-bpsk-modrec4-qpsk-rrc-v1','cnn-bpsk-modrec4-qpsk-rrc-qam16-v1'):
                        names = ('rf_environment', 'rf_experiment_scenarios')
                        modules = [importlib.import_module(name) for name in names]
                        for module in modules:
                            if Path(module.__file__).resolve() != directory / (module.__name__ + '.py'):
                                raise ValueError('环境模块不属于已验证发布')
                        baseline_path = directory / 'rf_environment_baseline.json'
                        if baseline_path.is_symlink() or baseline_path.stat().st_size > 131072:
                            raise ValueError('环境基线无效或过大')
                        baseline = modules[0].EnvironmentBaseline.from_dict(
                            json.loads(baseline_path.read_text(encoding='utf-8')))
                        self._environment_module, self._scenario_module = modules
                        self._environment_baseline = baseline
                        info.update(available=True, reason='固定窗口实验诊断已接入',
                            module_sha256={name: manifest['software'][name] for name in
                                ('rf_environment.py', 'rf_experiment_scenarios.py',
                                 'rf_environment_baseline.json')},
                            baseline_sha256=baseline.sha256,
                            environment_version=modules[0].VERSION)
            except Exception as exc:
                self._environment_module = self._scenario_module = None
                self._environment_baseline = None
                info.update(available=False, reason='环境不可用: ' + str(exc))
            with self._lock:
                self._environment_info = info
            self._environment_checked = True

    def teaching_status(self):
        with self._lock:
            return copy.deepcopy(self._teaching_info)

    def _ensure_teaching(self):
        with self._teaching_lock:
            if self._teaching_checked:
                return
            info = dict(self._teaching_info)
            try:
                if self._release_directory is not None:
                    import merged_release
                    directory=Path(self._release_directory).resolve(strict=True)
                    manifest=merged_release.verify_release(directory,require_software=True)
                    info['release_id']=manifest['release_id']
                    if manifest.get('software_contract') in ('cnn-bpsk-teaching-v1','cnn-bpsk-environment-v1','cnn-bpsk-headroom-v1','cnn-bpsk-modrec4-v1','cnn-bpsk-modrec4-qpsk-v1','cnn-bpsk-modrec4-qpsk-rrc-v1','cnn-bpsk-modrec4-qpsk-rrc-qam16-v1'):
                        names=('rf_teaching_profile','rf_rx_constellation','rf_experiment_record')
                        modules=[importlib.import_module(name) for name in names]
                        for module in modules:
                            if Path(module.__file__).resolve() != directory/(module.__name__+'.py'):
                                raise ValueError('教学模块不属于已验证发布')
                        self._constellation_module,self._record_module=modules[1:]
                        info.update(available=True,reason='教学观察与本地记录已接入',
                            module_sha256={name+'.py':manifest['software'][name+'.py'] for name in names})
            except Exception as exc:
                self._constellation_module=self._record_module=None
                info.update(available=False,reason='教学不可用: '+str(exc))
            with self._lock:
                self._teaching_info=info
            self._teaching_checked=True

    def submit_experiment_save(self, frame, envelope, constellation, analysis=None,
                               model_result=None, rf_config=None, historical=False,
                               environment=None, scenario_declaration=None):
        """Freeze a save request; disk writes never hold control/capture arbitration."""
        from rf_rx_types import RxIqFrame
        if not isinstance(frame,RxIqFrame) or type(historical) is not bool:
            return dict(ok=False,code='INVALID_RECORD',message='需要不可变IQ快照')
        with self._lock:
            if self._stop.is_set():
                return dict(ok=False,code='STOPPED',message='RF worker已停止')
            if not self._teaching_info['available'] or self._record_module is None:
                return dict(ok=False,code='UNAVAILABLE',message='记录模块未接入')
            if self._save_thread is not None and self._save_thread.is_alive():
                return dict(ok=False,code='BUSY',message='正在保存上一条记录')
            kwargs=dict(analysis=analysis,model_result=model_result,
                        rf_config=copy.deepcopy(rf_config),historical=historical)
            if environment is not None or scenario_declaration is not None:
                kwargs.update(environment=environment,
                              scenario_declaration=copy.deepcopy(scenario_declaration))
            self._save_handoff=None
            self._save_thread=threading.Thread(target=self._write_experiment,
                args=(frame,envelope,constellation,kwargs),name='rf-experiment-save',daemon=True)
            self._save_thread.start()
        return dict(queued=True,state='saving',capture_id=frame.capture_id,historical=historical)

    def _write_experiment(self, frame, envelope, constellation, kwargs):
        result=dict(capture_id=frame.capture_id,historical=kwargs['historical'])
        try:
            receipt=self._record_module.write_record(self._experiment_root,frame,envelope,
                                                       constellation,**kwargs)
            result.update(state='saved',receipt=receipt,reason='')
        except Exception as exc:
            result.update(state='error',receipt=None,reason=str(exc) or type(exc).__name__)
        with self._lock:
            self._save_handoff=result

    def take_experiment_save_result(self):
        with self._lock:
            result,self._save_handoff=self._save_handoff,None
        return result

    def demod_status(self):
        """Already verified scalars only; no import or file IO on the UI thread."""
        with self._lock:
            return dict(self._demod_info)

    def _demod_kind(self, demod_config):
        """Classify an already-frozen config without guessing its mode."""
        if demod_config is None:
            return None
        if self._demod_pipeline is not None and isinstance(
                demod_config, self._demod_pipeline.DemodConfig):
            return 'BPSK'
        if self._qpsk_pipeline is not None and isinstance(
                demod_config, self._qpsk_pipeline.QpskDemodConfig):
            return 'QPSK'
        if self._rrc_pipeline is not None and isinstance(
                demod_config, self._rrc_pipeline.RrcDemodConfig):
            return 'RRC'
        if self._qam16_pipeline is not None and isinstance(
                demod_config, self._qam16_pipeline.Qam16DemodConfig):
            return 'QAM16'
        if self._qam64_pipeline is not None and isinstance(
                demod_config, self._qam64_pipeline.Qam64DemodConfig):
            return 'QAM64'
        return None

    def _failed_demod(self, kind, frame, submitted, reason):
        if kind == 'QAM64':
            return self._qam64_pipeline.failed_qam64(frame, submitted, reason)
        if kind == 'QAM16':
            return self._qam16_pipeline.failed_qam16(frame, submitted, reason)
        if kind == 'QPSK':
            return self._qpsk_pipeline.failed_qpsk(frame, submitted, reason)
        if kind == 'RRC':
            return self._rrc_pipeline.failed_rrc(frame, submitted, reason)
        return self._demod_pipeline.failed_demod(frame, submitted, reason)

    def _analyze_demod(self, kind, frame, submitted):
        if kind == 'QAM64':
            return self._qam64_pipeline.analyze_qam64(frame, submitted)
        if kind == 'QAM16':
            return self._qam16_pipeline.analyze_qam16(frame, submitted)
        if kind == 'QPSK':
            return self._qpsk_pipeline.analyze_qpsk(frame, submitted)
        if kind == 'RRC':
            return self._rrc_pipeline.analyze_rrc(frame, submitted)
        return self._demod_pipeline.analyze_demod(frame, submitted)

    def _ensure_demod(self):
        with self._demod_lock:
            if self._demod_checked:
                return
            info = dict(self._demod_info)
            info.update(available=False, qpsk_available=False,
                        qpsk_algorithm_version=None, qpsk_algorithm_sha256=None,
                        rrc_available=False, rrc_algorithm_version=None,
                        rrc_algorithm_sha256=None, qam16_available=False,
                        qam16_algorithm_version=None, qam16_algorithm_sha256=None,
                        qam64_available=False, qam64_algorithm_version=None,
                        qam64_algorithm_sha256=None)
            try:
                if self._release_directory is not None:
                    import merged_release
                    directory = Path(self._release_directory).resolve(strict=True)
                    manifest = merged_release.verify_release(directory, require_software=True)
                    info['release_id'] = manifest['release_id']
                    # BPSK is kept on its existing frozen path.  Missing additive
                    # modules must never make the legacy path unavailable.
                    if manifest.get('software_contract') in ('cnn-bpsk-v1','cnn-bpsk-teaching-v1','cnn-bpsk-environment-v1','cnn-bpsk-headroom-v1','cnn-bpsk-modrec4-v1','cnn-bpsk-modrec4-qpsk-v1','cnn-bpsk-modrec4-qpsk-rrc-v1','cnn-bpsk-modrec4-qpsk-rrc-qam16-v1','cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1'):
                        pipeline = importlib.import_module('rf_bpsk_pipeline')
                        algorithm = importlib.import_module('rf_bpsk_receiver')
                        for module in (pipeline, algorithm):
                            if Path(module.__file__).resolve() != directory / (module.__name__+'.py'):
                                raise ValueError('解调模块不属于已验证发布')
                        self._demod_pipeline = pipeline
                        info.update(available=True, reason='BPSK有限快照接收已接入',
                            algorithm_version=algorithm.VERSION,
                            algorithm_sha256=manifest['software']['rf_bpsk_receiver.py'])
                    # QPSK is additive and release-attested independently.  It is
                    # available only from the NEW contract; the old 55-member
                    # contract must stay verifiable without these files.  All three
                    # imports must resolve inside the same verified release.
                    contract = manifest.get('software_contract')
                    qpsk_contract = contract in ('cnn-bpsk-modrec4-qpsk-v1',
                                                 'cnn-bpsk-modrec4-qpsk-rrc-v1',
                                                 'cnn-bpsk-modrec4-qpsk-rrc-qam16-v1',
                                                 'cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1')
                    rrc_contract = contract in ('cnn-bpsk-modrec4-qpsk-rrc-v1',
                                                'cnn-bpsk-modrec4-qpsk-rrc-qam16-v1',
                                                'cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1')
                    qam16_contract = contract in (
                        'cnn-bpsk-modrec4-qpsk-rrc-qam16-v1',
                        'cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1')
                    # QAM64 已移出本次交付支持范围：即使旧发布合同列出 QAM64，也恒不可用，
                    # 且本 worker 不 import QAM64 pipeline；源码/历史证据保留。
                    qam64_contract = False
                    if not qpsk_contract:
                        info.update(qpsk_available=False,
                                    qpsk_reason='QPSK尚未包含在当前发布合同')
                    try:
                        if not qpsk_contract:
                            raise ValueError('当前发布合同不含QPSK模块')
                        qpsk_pipeline = importlib.import_module('rf_qpsk_pipeline')
                        qpsk_receiver = importlib.import_module('rf_qpsk_receiver')
                        modem_result = importlib.import_module('rf_modem_result')
                        for module in (qpsk_pipeline, qpsk_receiver, modem_result):
                            if Path(module.__file__).resolve() != directory / (module.__name__+'.py'):
                                raise ValueError('QPSK解调模块不属于已验证发布')
                        self._qpsk_pipeline = qpsk_pipeline
                        info.update(
                            qpsk_available=True, qpsk_reason='QPSK/RECT有限快照接收已接入',
                            qpsk_algorithm_version=qpsk_receiver.VERSION,
                            qpsk_algorithm_sha256=manifest['software'].get('rf_qpsk_receiver.py'))
                    except Exception as exc:
                        self._qpsk_pipeline = None
                        info.update(qpsk_available=False,
                                    qpsk_reason='QPSK解调不可用: '+str(exc))
                    if not rrc_contract:
                        info.update(rrc_available=False,
                                    rrc_reason='RRC尚未包含在当前发布合同')
                    else:
                        try:
                            rrc_pipeline = importlib.import_module('rf_rrc_pipeline')
                            rrc_receiver = importlib.import_module('rf_rrc_receiver')
                            for module in (rrc_pipeline, rrc_receiver):
                                if Path(module.__file__).resolve() != directory / (module.__name__+'.py'):
                                    raise ValueError('RRC解调模块不属于已验证发布')
                            self._rrc_pipeline = rrc_pipeline
                            info.update(
                                rrc_available=True,
                                rrc_reason='RRC/BPSK-QPSK有限快照接收已接入',
                                rrc_algorithm_version=rrc_receiver.VERSION,
                                rrc_algorithm_sha256=manifest['software'].get('rf_rrc_receiver.py'))
                        except Exception as exc:
                            self._rrc_pipeline = None
                            info.update(rrc_available=False,
                                        rrc_reason='RRC解调不可用: '+str(exc))
                    if not qam16_contract:
                        info.update(qam16_available=False,
                                    qam16_reason='QAM16尚未包含在当前发布合同')
                    else:
                        try:
                            qam16_pipeline = importlib.import_module('rf_qam16_pipeline')
                            qam16_receiver = importlib.import_module('rf_qam16_receiver')
                            modem_result = importlib.import_module('rf_modem_result')
                            for module in (qam16_pipeline, qam16_receiver, modem_result):
                                if Path(module.__file__).resolve() != directory / (module.__name__+'.py'):
                                    raise ValueError('QAM16解调模块不属于已验证发布')
                            self._qam16_pipeline = qam16_pipeline
                            info.update(
                                qam16_available=True,
                                qam16_reason='QAM16/RECT有限快照接收已接入',
                                qam16_algorithm_version=qam16_receiver.VERSION,
                                qam16_algorithm_sha256=manifest['software'].get('rf_qam16_receiver.py'))
                        except Exception as exc:
                            self._qam16_pipeline = None
                            info.update(qam16_available=False,
                                        qam16_reason='QAM16解调不可用: '+str(exc))
                    # QAM64 能力恒为 unavailable；不 import QAM64 pipeline。
                    self._qam64_pipeline = None
                    info.update(qam64_available=False,
                                qam64_algorithm_version=None,
                                qam64_algorithm_sha256=None,
                                qam64_reason='QAM64超出本次交付支持范围')
            except Exception as exc:
                self._demod_pipeline = None
                self._qpsk_pipeline = None
                self._rrc_pipeline = None
                self._qam16_pipeline = None
                self._qam64_pipeline = None
                info.update(available=False, qpsk_available=False,
                            rrc_available=False, qam16_available=False,
                            qam64_available=False, reason='解调不可用: '+str(exc))
            with self._lock:
                self._demod_info = info
            self._demod_checked = True

    def model_status(self):
        """Scalar handoff only; never import numpy or read a model on the draw thread."""
        with self._lock:
            return dict(self._model_info)

    def modrec4_status(self):
        """Additive 4-class status; never changes the six-class model status."""
        with self._lock:
            return dict(self._modrec4_info)

    def _ensure_model(self):
        """Load the optional attested artifact once, exclusively on a worker thread."""
        with self._model_lock:
            if self._model_checked:
                return
            info = {"available": False, "production": False,
                    "reason": "无模型", "release_id": None,
                    "model_sha256": None, "model_version": None,
                    "preprocessing_version": None}
            try:
                if self._release_directory is not None:
                    import merged_release
                    directory = Path(self._release_directory).resolve(strict=True)
                    manifest = merged_release.verify_release(directory, require_software=True)
                    info["release_id"] = manifest["release_id"]
                    artifact = manifest.get(merged_release.MODEL_KEY)
                    if manifest.get("software_contract") in ("cnn-v1", "cnn-bpsk-v1", "cnn-bpsk-teaching-v1", "cnn-bpsk-environment-v1", "cnn-bpsk-headroom-v1", "cnn-bpsk-modrec4-v1", "cnn-bpsk-modrec4-qpsk-v1", "cnn-bpsk-modrec4-qpsk-rrc-v1", "cnn-bpsk-modrec4-qpsk-rrc-qam16-v1") and artifact:
                        # rx-v1 has no runtime dependency: this import belongs inside
                        # the verified CNN branch, never at panel module import time.
                        runtime = importlib.import_module("rf_cnn_runtime")
                        if (manifest.get('software_contract') in ('cnn-bpsk-teaching-v1','cnn-bpsk-environment-v1','cnn-bpsk-headroom-v1','cnn-bpsk-modrec4-v1','cnn-bpsk-modrec4-qpsk-v1','cnn-bpsk-modrec4-qpsk-rrc-v1','cnn-bpsk-modrec4-qpsk-rrc-qam16-v1')
                                and Path(runtime.__file__).resolve() != directory/'rf_cnn_runtime.py'):
                            raise ValueError('模型模块不属于已验证发布')
                        model = runtime.load_model(directory / artifact["model_file"],
                                                   artifact["model_sha256"])
                        self._model = model
                        info.update(available=True,
                                    model_sha256=model.model_sha256,
                                    model_version="tiny-iq-cnn-v1",
                                    preprocessing_version=runtime.PREPROCESSING,
                                    production=(merged_release.model_capability(manifest)
                                                and model.status == "production"),
                                    reason=("生产模型" if artifact["status"] == "production"
                                            else "实验模型: 不生成可执行建议"))
            except Exception as exc:
                self._model = None
                info.update(available=False, production=False,
                            reason="模型不可用: %s" % (str(exc) or type(exc).__name__))
            with self._lock:
                self._model_info = info
            self._model_checked = True

    def _ensure_modrec4(self):
        """Load the optional additive 4-class artifact; failure never blocks RX."""
        with self._modrec4_lock:
            if self._modrec4_checked:
                return
            info = {"available": False, "release_id": None,
                    "reason": "四类扩展未接入"}
            try:
                if self._release_directory is not None:
                    import merged_release
                    directory = Path(self._release_directory).resolve(strict=True)
                    manifest = merged_release.verify_release(directory, require_software=True)
                    info["release_id"] = manifest["release_id"]
                    artifact = manifest.get(merged_release.MODREC4_MODEL_KEY)
                    if (manifest.get("software_contract") in ("cnn-bpsk-modrec4-v1", "cnn-bpsk-modrec4-qpsk-v1", "cnn-bpsk-modrec4-qpsk-rrc-v1", "cnn-bpsk-modrec4-qpsk-rrc-qam16-v1")
                            and artifact):
                        adapter_module = importlib.import_module("rf_modrec4_adapter")
                        if Path(adapter_module.__file__).resolve() != directory / "rf_modrec4_adapter.py":
                            raise ValueError("四类适配器不属于已验证发布")
                        info.update(
                            available=True,
                            reason="四类扩展已加载",
                            artifact_status=artifact.get("status"),
                            model_sha256=dict(adapter_module.ModRec4Adapter.from_release(
                                directory, artifact).model_sha256))
                        self._modrec4 = adapter_module.ModRec4Adapter.from_release(
                            directory, artifact)
            except Exception as exc:
                self._modrec4 = None
                info.update(available=False,
                            reason="四类扩展不可用: %s" % (str(exc) or type(exc).__name__))
            with self._lock:
                self._modrec4_info = info
            self._modrec4_checked = True

    def _modrec4_context(self, frame, request):
        """Return the explicit capture/configuration domain, or None if unavailable."""
        submitted = request.get("demod_config") if isinstance(request, dict) else None
        config = getattr(submitted, "config", None)
        if config is None:
            return None
        return {
            "format": "s16_iq_le",
            "samples": frame.samples,
            "sample_rate_hz": frame.sample_rate_hz,
            "decimation": frame.decimation,
            "symbol_rate_hz": config.symbol_rate_hz,
            "shaping": config.shaping,
        }

    def _modrec4_frame_identity(self, frame):
        return {
            "capture_id": frame.capture_id,
            "request_id": frame.request_id,
            "daemon_timestamp_ns": frame.daemon_timestamp_ns,
            "release_id": frame.release_id,
            "connection_generation": frame.connection_generation,
            "config_revision": frame.config_revision,
            "iq_sha256": hashlib.sha256(frame.payload).hexdigest(),
        }

    @staticmethod
    def _modrec4_value(frame, key):
        if isinstance(frame, dict):
            return frame.get(key)
        return getattr(frame, key, None)

    def _modrec4_validity_problem(self, frame, now=None):
        """Explicit validity gate shared by result creation and delivery."""
        now = time.monotonic() if now is None else float(now)
        completed = self._modrec4_value(frame, "completed_at_monotonic")
        if not rf_rx_analysis.snapshot_is_fresh(completed, now):
            return "frame_expired"
        sock = getattr(self.client, "_session_socket", None)
        if sock is None:
            return "session_disconnected"
        try:
            if sock.fileno() < 0:
                return "session_disconnected"
        except Exception:
            return "session_disconnected"
        generation = self._modrec4_value(frame, "connection_generation")
        if getattr(self.client, "_connection_generation", generation) != generation:
            return "stale_connection_generation"
        revision = self._modrec4_value(frame, "config_revision")
        if getattr(self.client, "_config_revision", revision) != revision:
            return "stale_config_revision"
        return None

    def _modrec4_invalid(self, reason, frame, stale=False, age_s=None):
        out = dict(status="INVALID", validity=("invalid" if stale else "current"),
                   stale=bool(stale), reason=str(reason),
                   frame_identity=self._modrec4_frame_identity(frame)
                   if not isinstance(frame, dict) else {
                       k: frame.get(k) for k in
                       ("capture_id", "request_id", "daemon_timestamp_ns", "release_id",
                        "connection_generation", "config_revision", "iq_sha256")})
        if age_s is not None:
            out["age_s"] = age_s
        return out

    def _modrec4_result(self, frame, request, environment_result, demod_result,
                        model_result):
        """Additive result only; never reads/overwrites six-class/env/demod."""
        # RRC is a demodulation domain, not a modrec4 classification input.  Its
        # additive classifier is explicitly unavailable and must not block RX.
        if (self._rrc_pipeline is not None
                and isinstance(demod_result, self._rrc_pipeline.RrcEnvelope)):
            return dict(status="INVALID", validity="current", stale=False,
                        reason="RRC_CLASSIFICATION_UNAVAILABLE",
                        **self._modrec4_frame_identity(frame))
        self._ensure_modrec4()
        if self._modrec4 is None:
            return None
        identity = self._modrec4_frame_identity(frame)
        if frame.release_id != self._modrec4_info.get("release_id"):
            return dict(status="INVALID", validity="current", stale=False,
                        reason="release_mismatch", **identity)
        validity = self._modrec4_validity_problem(frame)
        if validity is not None:
            age = None
            completed = frame.completed_at_monotonic
            if isinstance(completed, (int, float)):
                age = max(0.0, time.monotonic() - float(completed))
            return self._modrec4_invalid(validity, frame, stale=True, age_s=age)
        context = self._modrec4_context(frame, request)
        if context is None:
            return dict(status="INVALID", validity="current", stale=False,
                        reason="input_context_unavailable", **identity)
        try:
            import numpy as np
            iq = np.frombuffer(frame.payload, dtype="<i2").reshape(frame.samples, 2)
            result = self._modrec4.evaluate_iq(
                iq, context=context, environment=environment_result,
                demod=demod_result, six_class=model_result,
                frame_id=frame.capture_id)
        except Exception as exc:
            return dict(status="INVALID", validity="current", stale=False,
                        reason="ADAPTER_FAILED: %s" % (str(exc) or type(exc).__name__),
                        **identity)
        result["validity"] = "current"
        result["stale"] = False
        result["frame_identity"] = identity
        return result

    def _predict_frame(self, frame):
        """Only measured immutable IQ enters the classifier; no TX metadata."""
        self._ensure_model()
        if self._model is None:
            return None
        if frame.release_id != self._model_info["release_id"]:
            return RxModelResult(
                frame.capture_id, "UNKNOWN", "UNKNOWN", rejected=True,
                model_version="tiny-iq-cnn-v1", model_sha256=self._model.model_sha256,
                preprocessing_version="dc-complex-rms-v1", source=frame.op,
                experimental=not self._model_info["production"], reason="release_mismatch",
                completed_at_monotonic=frame.completed_at_monotonic)
        import numpy as np
        prediction = self._model.predict(
            np.frombuffer(frame.payload, dtype="<i2").reshape(frame.samples, 2),
            input_fs_hz=frame.sample_rate_hz, decim=frame.decimation)
        label = prediction["label"]
        scores = tuple(prediction.get("scores", ()))
        classes = ("TONE", "BPSK", "QPSK", "QAM16", "QAM64")
        return RxModelResult(
            capture_id=frame.capture_id, quality="UNKNOWN" if label == "UNKNOWN" else "OK",
            label=label, class_index=classes.index(label) if label in classes else None,
            scores=scores, model_version="tiny-iq-cnn-v1",
            model_sha256=self._model.model_sha256,
            preprocessing_version="dc-complex-rms-v1", source=frame.op,
            experimental=not self._model_info["production"], rejected=label == "UNKNOWN",
            reason=prediction.get("reason", ""),
            completed_at_monotonic=frame.completed_at_monotonic)

    @staticmethod
    def _idle_rx_status():
        return {"state": RX_CAPTURE_IDLE, "physical": False, "capture_id": None,
                "code": None, "reason": "", "close_confirmed": None,
                "cancelled": False, "seq": 0}

    def start(self):
        if self._thread is not None:
            raise RuntimeError("RF status worker already started")
        self._thread = threading.Thread(target=self._run, name="rf-status", daemon=True)
        self._thread.start()

    def _poll_once(self):
        # Both producers serialize through publication, so a delayed old read cannot
        # overwrite a newer post-control snapshot after releasing the client I/O lock.
        with self._poll_lock:
            try:
                snapshot = self.client.poll()
            except Exception as exc:
                snapshot = RfStatusSnapshot(error=str(exc) or type(exc).__name__)
            self.note_headroom_capability(snapshot)
            with self._lock:
                self._latest = snapshot

    def _run(self):
        self._ensure_model()
        self._ensure_demod()
        self._ensure_teaching()
        self._ensure_environment()
        self._ensure_headroom()
        while not self._stop.is_set():
            self._poll_once()
            if self._stop.wait(self.poll_interval):
                break

    def control(self, action, token_path=None, args=None):
        """Execute one panel control action; never raises.

        Returns `{"ok": bool, "code": str|None, "message": str}` so the caller can land
        the result in `ui_model.set_rf_control_result` without a try/except.  The token
        is read from the operator's path at the moment of arming and handed straight to
        the client -- it is not cached, logged or put in the result.

        """
        try:
            supplied_args = dict(args or {})
            native_result = None
            scenario_declaration = None
            if action == "arm":
                token, why = read_control_token(token_path)
                if token is None:
                    return {"ok": False, "code": "TOKEN_UNAVAILABLE", "message": why}
                self.client.control_arm(token)
            elif action == "disarm":
                self.client.control_disarm()
            elif action in PARAMETERLESS_OPS:
                native_result = self.client.control_op(PARAMETERLESS_OPS[action], {})
            elif action in OP_WITH_ARGS:
                if not args:
                    return {"ok": False, "code": "NOT_WIRED",
                            "message": ("%s was not sent: it needs operator parameters "
                                        "and none were supplied" % action)}
                native_result = self.client.control_op(OP_WITH_ARGS[action], supplied_args)
            elif action == "load":
                if supplied_args.get('waveform') == 'environment':
                    if not self._environment_info['available'] or self._scenario_module is None:
                        raise ValueError('环境场景模块未验证')
                    if set(supplied_args) != {'waveform','scenario'} or not isinstance(supplied_args['scenario'],dict):
                        raise ValueError('环境场景参数无效')
                    scene = self._scenario_module.generate_scenario(
                        self._scenario_module.ScenarioConfig(**supplied_args['scenario']))
                    metadata, payload = scene.metadata_dict(), scene.payload
                    scenario_declaration = scene.declaration_dict()
                else:
                    metadata, payload = build_tx_waveform(supplied_args)
                native_result = self.client.control_op("tx.load", metadata, payload=payload)
            else:
                return {"ok": False, "code": "NOT_WIRED",
                        "message": ("the panel does not send %r yet" % (action,))}
            result = {"ok": True, "code": None, "message": ""}
            if action in ("init", "configure", "load", "start"):
                result.update(result=native_result, args=supplied_args)
            if scenario_declaration is not None:
                result['scenario_declaration'] = scenario_declaration
            return result
        except Exception as exc:                       # noqa: BLE001 - reported, not raised
            # Even a local waveform validation failure invalidates a held load.
            close = getattr(self.client, "close", None)
            if close is not None:
                try:
                    close()
                except OSError:
                    pass  # Preserve the original daemon or validation error.
            code = getattr(exc, "code", None)
            return {"ok": False,
                    "code": code if isinstance(code, str) else None,
                    "message": str(exc) or type(exc).__name__}

    def submit_control(self, action, token_path=None, args=None):
        """Submit without network I/O; one active job and one idempotent queued STOP."""
        request = (action, token_path, dict(args or {}))
        with self._lock:
            if self._headroom_running:
                if action in ('stop','disarm'):
                    return self.cancel_headroom_study()
                return {'ok':False,'code':'BUSY','message':'HEADROOM study running'}
            if self._stop.is_set():
                return {"ok": False, "code": "STOPPED", "message": "RF worker stopped"}
            if self._capture_request is not None:
                # One job at a time.  The RX page's STOP is the CAPTURE's own cancel (always
                # reachable through cancel_capture); queueing a tx.stop here would be worse
                # than refusing, because no control thread exists to drain the queue and the
                # request would silently vanish.
                return {"ok": False, "code": "BUSY", "message": "RF capture is running"}
            if self._control_running is not None:
                # STOP stays reachable while a control operation runs: it is parameterless,
                # idempotent and queued behind exactly one active job.
                if action == "stop":
                    if self._control_running != "stop" and self._queued_stop is None:
                        self._queued_stop = request
                    return {"queued": True}
                return {"ok": False, "code": "BUSY", "message": "RF control is busy"}
            self._control_running = action
            self._control_thread = threading.Thread(
                target=self._run_control, args=(request,), name="rf-control", daemon=True)
            self._control_thread.start()
        return {"queued": True}

    def _run_control(self, request):
        while True:
            action, token_path, args = request
            result = self.control(action, token_path, args)
            self._poll_once()
            with self._lock:
                self._control_results.append((action, result))
                if self._queued_stop is None:
                    self._control_running = None
                    return
                request, self._queued_stop = self._queued_stop, None
                self._control_running = request[0]

    def control_status(self):
        with self._lock:
            return self._control_running

    def take_control_results(self):
        with self._lock:
            results, self._control_results = self._control_results, []
        return results

    def take_frame(self):
        """Atomically hand off telemetry, completions and the active operation."""
        with self._lock:
            snapshot = self._latest
            results, self._control_results = self._control_results, []
            running = self._control_running
        return self._expire(snapshot), results, running

    def _expire(self, snapshot):
        when = snapshot.received_at_monotonic
        return snapshot.expired() if when is not None and time.monotonic() - when >= self.stale_after else snapshot

    def latest(self) -> RfStatusSnapshot:
        with self._lock:
            snapshot = self._latest
        return self._expire(snapshot)

    # ------------------------------------------------------------------ RX capture
    def submit_capture(self, args, physical=False, demod_config=None, demod_revision=None,
                       rf_config=None):
        """Submit one finite RX capture; returns immediately and does NO network I/O.

        The capture, its round trip and the windowed-FFT analysis all run on one
        background thread, so the panel's input path only hands over arguments.  Refusals
        use the same `{ok, code, message}` shape as the control path, and a refused
        submission starts no thread and sends nothing.

        The request is validated through the CLIENT's own `_capture_args` rule when the
        client exposes it, so the "ratio is required" contract (the daemon keeps its
        initialized ratio of 1 => decimation 2 when the argument is absent) is enforced in
        one place rather than duplicated here.
        """
        physical = bool(physical)
        demod_kind = None
        if demod_config is not None:
            # Only an already-frozen explicit RX configuration is accepted. Never
            # send these local fields to the daemon's capture wire contract.
            demod_kind = self._demod_kind(demod_config)
            if demod_kind is None:
                return {"ok": False, "code": "DEMOD_UNAVAILABLE", "message": "解调未接入"}
            if (demod_revision is not None and demod_revision != demod_config.revision):
                return {"ok": False, "code": "INVALID_DEMOD_CONFIG", "message": "解调提交配置无效"}
        if self._stop.is_set():
            return {"ok": False, "code": "STOPPED", "message": "RF worker stopped"}
        validator = getattr(self.client, "_capture_args", None)
        if validator is not None:
            try:
                wire = validator(dict(args or {}), physical)
            except Exception as exc:            # noqa: BLE001 - reported, not raised
                code = getattr(exc, "code", None)
                return {"ok": False,
                        "code": code if isinstance(code, str) and code else "INVALID_ARGUMENT",
                        "message": str(exc) or type(exc).__name__}
        else:
            wire = dict(args or {})
        with self._lock:
            if self._capture_request is not None or self._control_running is not None:
                return {"ok": False, "code": "BUSY", "message": "RF control is busy"}
            self._capture_request = {"physical": physical, "args": wire, "demod_config": demod_config,
                                     "rf_config": copy.deepcopy(rf_config)}
            self._capture_cancel = False
            self._rx_status = self._rx_status_of(RX_CAPTURE_QUEUED, physical=physical)
            self._capture_thread = threading.Thread(
                target=self._run_capture, name="rf-rx-capture", daemon=True)
            self._capture_thread.start()
        return {"queued": True}

    def cancel_capture(self):
        """Mark the in-flight capture cancelled; returns the status to draw right now.

        Deliberately NOT immediate: the request already owns the client's I/O lock, so the
        worker waits for it to return (or for the client's 3 s capture deadline) and only
        then closes and reads back.  The panel therefore shows "cancelling", never
        "stopped".
        """
        with self._lock:
            if self._capture_request is None or self._rx_status["state"] == RX_CAPTURE_DONE:
                return dict(self._rx_status)
            self._capture_cancel = True
            self._rx_status = dict(self._rx_status, state=RX_CAPTURE_CANCELLING,
                                   reason="取消中: 等待采集请求返回")
            return dict(self._rx_status)

    def capture_status(self):
        with self._lock:
            return dict(self._rx_status)

    def is_capturing(self):
        with self._lock:
            return self._capture_request is not None

    def take_rx_result(self):
        """Atomically hand off the frozen RX result and the current capture status.

        Returns `(payload, status)`.  `payload` is None when there is no NEW captured
        result: a cancellation, a refusal and an in-flight request all publish their
        status but never a frame, so nothing can land from a capture that did not complete
        under the configuration it was submitted for.
        """
        with self._lock:
            payload, self._rx_handoff = self._rx_handoff, None
            if isinstance(payload, dict):
                modrec4 = payload.get("modrec4_result")
                if isinstance(modrec4, dict) and modrec4.get("status") == "OK":
                    problem = self._modrec4_validity_problem(payload.get("frame") or {})
                    if problem is not None:
                        payload["modrec4_result"] = dict(
                            modrec4, status="INVALID", validity="invalid",
                            stale=True, reason="post_publish_" + problem,
                            previous_status="OK")
            return payload, dict(self._rx_status)

    def _rx_status_of(self, state, physical=False, capture_id=None, code=None, reason="",
                      close_confirmed=None, cancelled=False):
        """Build one publishable RX status, stamped with a FRESH sequence number."""
        self._rx_status_seq += 1
        return {"state": state, "physical": bool(physical), "capture_id": capture_id,
                "code": code, "reason": reason, "close_confirmed": close_confirmed,
                "cancelled": bool(cancelled), "seq": self._rx_status_seq}

    def _run_capture(self):
        """The capture thread entry point: whatever happens, the request IS released.

        `_capture_request` is the worker's "a capture owns me" flag.  An escape from the
        body would leave it set for the life of the process -- permanently BUSY, the chip
        stuck on cancel, and `tx.stop` refused forever -- so the body runs in a guard that
        publishes any unexpected failure as a failed capture and always clears the flag.
        """
        try:
            self._capture_once()
        except Exception as exc:                # noqa: BLE001 - reported, never left set
            with self._lock:
                self._rx_status = self._rx_status_of(
                    RX_CAPTURE_ERROR, code=getattr(exc, "code", None),
                    reason=str(exc) or type(exc).__name__)
        finally:
            # ONE place releases the job, so no path can leave the worker permanently BUSY.
            with self._lock:
                self._capture_request = None
                self._capture_cancel = False

    def _capture_once(self):
        """One capture: request -> validated frame -> analysis -> frozen handoff."""
        with self._lock:
            request = self._capture_request
            cancelled = self._capture_cancel
            self._rx_status = self._rx_status_of(
                RX_CAPTURE_RUNNING, physical=request["physical"])
        physical, args = request["physical"], request["args"]
        frame, failure = None, None
        if not cancelled:
            try:
                frame = self.client.capture(args, physical=physical)
            except Exception as exc:            # noqa: BLE001 - reported, not raised
                code = getattr(exc, "code", None)
                failure = {"code": code if isinstance(code, str) and code else None,
                           "message": str(exc) or type(exc).__name__}
        with self._lock:
            # Cancellation is decided ONLY after the in-flight request returned, so a
            # request that completed while the operator was cancelling is still discarded.
            cancelled = self._capture_cancel
        analysis = None
        model_result = None
        demod_result = None
        constellation = None
        environment_result = None
        modrec4_result = None
        if failure is None and not cancelled:
            try:
                # The windowed FFT runs HERE, on the capture thread -- never on the
                # touch/draw thread that submitted the request.
                analysis = summarize(frame)
            except Exception as exc:            # noqa: BLE001 - reported, not raised
                failure = {"code": "ANALYSIS_FAILED",
                           "message": str(exc) or type(exc).__name__}

        if failure is None and not cancelled:
            try:
                model_result = self._predict_frame(frame)
            except Exception as exc:
                # Unsupported shapes or an optional runtime failure preserve base RX.
                # Explicit UNKNOWN belongs to this capture, never a previous one.
                if self._model is not None:
                    model_result = RxModelResult(
                        frame.capture_id, "UNKNOWN", "UNKNOWN", rejected=True,
                        model_version="tiny-iq-cnn-v1", model_sha256=self._model.model_sha256,
                        preprocessing_version="dc-complex-rms-v1", source=frame.op,
                        experimental=not self._model_info["production"],
                        reason=str(exc) or type(exc).__name__,
                        completed_at_monotonic=frame.completed_at_monotonic)

        if failure is None and not cancelled and request.get('demod_config') is not None:
            with self._lock:
                cancelled = self._capture_cancel
            if not cancelled:
                submitted = request['demod_config']
                kind = self._demod_kind(submitted)
                if frame.release_id == self._demod_info['release_id'] and kind is not None:
                    try:
                        demod_result = self._analyze_demod(kind, frame, submitted)
                    except Exception:
                        demod_result = self._failed_demod(kind, frame, submitted, 'DEMOD_FAILED')
                elif kind is not None:
                    demod_result = self._failed_demod(kind, frame, submitted, 'RELEASE_MISMATCH')

        if (failure is None and not cancelled and demod_result is not None
                and self._teaching_info['available'] and self._constellation_module is not None):
            try:
                constellation=self._constellation_module.estimate_constellation(frame,demod_result)
            except Exception:
                constellation=None

        if (failure is None and not cancelled and demod_result is not None
                and self._environment_info['available'] and self._environment_module is not None
                and frame.release_id == self._environment_info['release_id']):
            try:
                environment_result = self._environment_module.analyze_environment(
                    frame, demod_result, request.get('rf_config'), self._environment_baseline)
            except Exception:
                # Optional diagnosis must never resurrect a previous window or lose base IQ.
                environment_result = None

        if failure is None and not cancelled:
            modrec4_result = self._modrec4_result(
                frame, request, environment_result, demod_result, model_result)

        # Cancellation and successful publication share one lock/decision.  A STOP
        # received during the FFT must win, while a completed publication is already
        # terminal even if the thread has not returned through its finally block.
        with self._lock:
            cancelled = self._capture_cancel
            # A reconnect or a successful config on the retained client while numpy
            # runs invalidates the capture as a whole, just like cooperative STOP.
            if frame is not None and not cancelled:
                cancelled = (getattr(self.client, "_connection_generation", frame.connection_generation)
                             != frame.connection_generation
                             or getattr(self.client, "_config_revision", frame.config_revision)
                             != frame.config_revision)
                if not cancelled and isinstance(modrec4_result, dict) \
                        and modrec4_result.get("status") == "OK":
                    validity = self._modrec4_validity_problem(frame)
                    if validity is not None:
                        modrec4_result = self._modrec4_invalid(
                            validity, frame, stale=True,
                            age_s=max(0.0, time.monotonic() - frame.completed_at_monotonic))
            if not cancelled and failure is None:
                self._rx_status = self._rx_status_of(
                    RX_CAPTURE_DONE, physical=physical, capture_id=frame.capture_id,
                    close_confirmed=True)
                self._rx_handoff = {"frame": frame.to_dict(), "analysis": analysis,
                                    "model_result": model_result, "demod_result": demod_result,
                                    "raw_frame": frame if self._teaching_info['available'] else None,
                                    "constellation": constellation,
                                    "environment_result": environment_result,
                                    "modrec4_result": modrec4_result}
                return

        release = None
        if cancelled or failure is not None:
            # Close-and-readback takes priority over everything else on this path: the
            # panel must be able to say whether the release was actually observed.
            release = self._close_and_readback(physical)

        if cancelled:
            status = self._rx_status_of(
                RX_CAPTURE_CANCELLED, physical=physical,
                close_confirmed=release["confirmed"], cancelled=True,
                reason=("" if release["confirmed"] else
                        CLOSE_UNCONFIRMED + ": " + (release["detail"]
                                                    or "回读无法确认释放")))
        elif failure is not None:
            status = self._rx_status_of(
                RX_CAPTURE_ERROR, physical=physical, code=failure["code"],
                close_confirmed=release["confirmed"],
                reason=(failure["message"] if release["confirmed"] else
                        CLOSE_UNCONFIRMED + ": " + (release["detail"]
                                                    or "回读无法确认释放")))
        with self._lock:
            self._rx_status = status
            self._rx_handoff = None
            # `_capture_request` is released by `_run_capture`'s finally -- one place only.

    def _close_and_readback(self, physical):
        """Close the capture session and read the PL back; confirm only what was seen.

        The daemon's own disconnect handling is the safety net, but the panel has to be
        able to say whether the release was observed.  A readback that does not confirm
        CAP_ARM=0 (or, for a physical capture, SRC_SEL=false) is reported as unconfirmed,
        which is what keeps "关闭未确认" from ever being drawn as "stopped".
        """
        close = getattr(self.client, "close", None)
        closed = close is None
        if close is not None:
            try:
                close()
                closed = True
            except OSError:
                closed = False
        try:
            snapshot = self.client.poll()
        except Exception as exc:                # noqa: BLE001 - reported, not raised
            return {"confirmed": False, "closed": closed,
                    "detail": "关闭后的 PL 回读失败 (%s)"
                              % (str(exc) or type(exc).__name__)}
        pl = getattr(snapshot, "pl_status", None)
        io_ok = bool(getattr(pl, "io_ok", False))
        armed = getattr(getattr(pl, "rx", None), "armed", None)
        source = getattr(getattr(pl, "tx", None), "source_selected", None)
        confirmed = bool(closed and io_ok and armed is False
                         and (not physical or source is False))
        # The reason is drawn on an 800x480 panel, so each detail stays short and in the
        # panel's language; "关闭未确认" is prepended by the caller.
        if confirmed:
            detail = ""
        elif not closed:
            detail = "采集会话未能关闭"
        elif not io_ok:
            detail = "关闭后的 PL 回读不可用"
        elif armed is not False:
            detail = "回读未确认 CAP_ARM 归零"
        else:
            detail = "回读未确认 SRC_SEL 已关"
        return {"confirmed": confirmed, "closed": closed, "detail": detail}

    def stop(self):
        self._stop.set()
        self._headroom_cancel.set()
        if self._headroom_thread is not None:
            budget = getattr(self.client,'control_timeout',20.0)+3*getattr(self.client,'timeout',0.75)+2
            self._headroom_thread.join(budget)
            if self._headroom_thread.is_alive():
                raise RuntimeError('HEADROOM worker did not stop within deadline')
        if self._control_thread is not None:
            # Complete an already accepted STOP before releasing the worker.
            budget = getattr(self.client, "control_timeout", 20.0) + 2 * getattr(self.client, "timeout", 0.75) + 2.0
            self._control_thread.join(timeout=budget)
            if self._control_thread.is_alive():
                raise RuntimeError("RF control worker did not stop")
        if self._capture_thread is not None:
            # Complete an accepted capture before releasing the worker; its own budget is
            # the client's capture deadline plus one status poll plus slack.
            budget = (getattr(self.client, "capture_timeout", 3.0) +
                      getattr(self.client, "timeout", 0.75) + 2.0)
            self._capture_thread.join(timeout=budget)
            if self._capture_thread.is_alive():
                raise RuntimeError("RF capture worker did not stop")
        if self._thread is not None:
            self._thread.join(timeout=self.client.timeout + 1.0 if hasattr(self.client, "timeout") else 2.0)
            if self._thread.is_alive():
                raise RuntimeError("RF status worker did not stop")
        close = getattr(self.client, "close", None)
        if close is not None:
            close()

    def is_alive(self):
        return ((self._thread is not None and self._thread.is_alive()) or
                (self._control_thread is not None and self._control_thread.is_alive()) or
                (self._capture_thread is not None and self._capture_thread.is_alive()))
