"""The research contract is attested by a release and announced by a daemon.

Both facts are needed and neither substitutes for the other: an installed
release can carry the research module while the running daemon is an older
build, and a daemon can announce the contract into a release that does not have
the module.  Nothing here starts a study, arms anything or reaches a board.
"""
import hashlib
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_rf_model_release import R, _release, _save_identity

import rf_headroom as H
import rf_status_worker as W
import ui_model as U

SW = Path(__file__).resolve().parents[1]
SESSION = '0123456789abcdef0123456789abcdef'
CONTRACT = 'cnn-bpsk-headroom-v1'


def headroom_release(tree):
    """A verified release whose research modules are the REAL ones.

    `_release` writes stub bytes for every mapped file, which is enough to test
    the manifest but not to load a module; the attested files have to be the
    modules a study would actually run.
    """
    manifest, receipt = _release(tree, contract=CONTRACT)
    for name in ('rf_headroom.py', 'rf_headroom_replay.py'):
        shutil.copyfile(str(SW / name), str(tree / name))
        manifest['software'][name] = R.sha256_file(tree / name)
    receipt['software'] = manifest['software']
    _save_identity(tree, manifest, receipt)
    return tree


def live(minor=H.PROTOCOL_MINOR, version=H.DAEMON_VERSION, study=H.STUDY,
         operations=H.CONTROL_OPS, present=True):
    if not present:
        return None
    from test_rf_headroom_corrections import eligible
    value=eligible(); value.update(study=study,protocol_minor=minor,daemon_version=version,
                                  operations=list(operations))
    return value



class Snapshot:
    def __init__(self, headroom, daemon_version=H.DAEMON_VERSION):
        self.headroom = headroom
        self.daemon_version = daemon_version


def worker(tree=None):
    return W.RfStatusWorker(release_directory=None if tree is None else str(tree))


# ------------------------------------------------------------------- worker

def test_the_research_contract_is_unavailable_without_a_verified_release():
    w = worker()
    w._ensure_headroom()
    status = w.headroom_status()
    assert status['available'] is False
    assert status['module_sha256'] == {}
    assert w.headroom_run(SESSION)['stopped_at'] == 'UNAVAILABLE'


def test_an_environment_release_does_not_gain_the_research_contract(tmp_path):
    """A release that installs the old contract cannot claim the new one."""
    tree = tmp_path / 'environment'
    _release(tree, contract='cnn-bpsk-environment-v1')
    w = worker(tree)
    w._ensure_headroom()
    assert w.headroom_status()['available'] is False
    assert w._headroom_module is None


def test_a_research_release_is_attested_but_not_yet_live(tmp_path):
    tree = headroom_release(tmp_path / 'headroom')
    w = worker(tree)
    w._ensure_headroom()
    status = w.headroom_status()
    assert status['available'] is True
    assert set(status['module_sha256']) == {'rf_headroom.py',
                                            'rf_headroom_replay.py'}
    assert status['live'] is None
    # An attested release with no live announcement still refuses to run.
    assert w.headroom_run(SESSION)['stopped_at'] == 'CAPABILITY'


def test_the_live_capability_is_taken_from_the_poll_that_just_ran(tmp_path):
    tree = headroom_release(tmp_path / 'headroom')
    w = worker(tree)
    w._ensure_headroom()
    w.note_headroom_capability(Snapshot(live()))
    assert H.capability_available(w.headroom_status()['live']) is True
    # The daemon moved to a different protocol minor: the panel stops offering
    # the study, even though the release is unchanged and still attested.
    w.note_headroom_capability(Snapshot(live(minor=5)))
    assert H.capability_available(w.headroom_status()['live']) is False


def test_an_unannounced_contract_is_recorded_as_absent_not_invented(tmp_path):
    tree = headroom_release(tmp_path / 'headroom')
    w = worker(tree)
    w._ensure_headroom()
    w.note_headroom_capability(Snapshot(None))
    assert w.headroom_status()['live'] is None
    assert w.headroom_run(SESSION)['stopped_at'] == 'CAPABILITY'
    # A half-announced contract (one operation missing) is not availability.
    w.note_headroom_capability(Snapshot(live(operations=H.CONTROL_OPS[:-1])))
    assert w.headroom_run(SESSION)['stopped_at'] == 'CAPABILITY'


def test_the_worker_runs_the_controller_only_after_both_facts_hold(tmp_path):
    tree = headroom_release(tmp_path / 'headroom')
    w = worker(tree)
    w._ensure_headroom()
    w.note_headroom_capability(Snapshot(live()))
    calls = []

    class Client:
        def headroom_capability(self):
            return None  # the controller must use the capability it was given

        def headroom_begin(self, args):
            calls.append('begin')
            return dict(study=H.STUDY, session_id=args['session_id'],
                        initializations=1, attempt_budget=5, actual_rf={})

        def control_disarm(self):
            calls.append('disarm')
            return dict(disarmed=True)

    w.client = Client()
    receipt = w.headroom_run(SESSION)
    # The controller refuses immediately, because the LIVE capability comes from
    # the daemon and this stand-in client reports none.  The worker did not fall
    # back to the release attestation.
    assert receipt['completed'] is False
    assert calls == []


def test_the_worker_never_starts_a_study_from_a_poll(tmp_path):
    """`note_headroom_capability` is the only poll-path entry point, and it is read-only."""
    source = (Path(W.__file__)).read_text(encoding='utf-8')
    body = source[source.index('def note_headroom_capability'):
                  source.index('def headroom_run')]
    for forbidden in ('headroom_begin', 'headroom_capture', 'headroom_load',
                      'control_arm', 'headroom_run('):
        assert forbidden not in body, forbidden


# ---------------------------------------------------------------- the panel

class _StubView:
    """The only parts of the panel `_PanelScreen.render` touches here."""

    def __init__(self):
        self.app = U.UiApp()
        self.lcd = self.ui = self.cursor = None

    def render(self, state):
        pass


class _StubSnapshot:
    def to_dict(self):
        # A real snapshot carries the control state the panel gates on; the
        # render loop lands it every frame, so an empty one would silently
        # reset `armed` between assertions.
        return dict(armed=False, daemon_state='OFFLINE')


class _StubWorker:
    """The parts of a status worker `_PanelScreen.render` reaches for."""

    def latest(self):
        return _StubSnapshot()


def test_the_daemon_lands_the_research_status_on_the_panel():
    """The chain is not just callable: the panel loop is where it is reached.

    `pynq_status._PanelScreen.render` is the ONE place a worker's frozen status
    becomes panel state.  Without this line the module, the worker and the view
    are all correct and the operator still sees nothing.
    """
    import pynq_status

    class Worker(_StubWorker):
        def headroom_status(self):
            return dict(available=True, release_id='release-1',
                        module_sha256={'rf_headroom.py': 'a' * 64,
                                       'rf_headroom_replay.py': 'b' * 64},
                        live=live())

        def headroom_study_status(self):
            return dict(running=self.running)

        def take_headroom_result(self):
            return self.finished

    view = _StubView()
    view.app.set_rf_status(dict(view.app.rf_status, armed=True,
                                daemon_state='OFFLINE'))
    worker = Worker()
    worker.running, worker.finished = True, None
    screen = pynq_status._PanelScreen(view, None, None, None, rf_worker=worker)
    screen.render(None)
    assert view.app.headroom_status['available'] is True
    assert view.app.headroom_summary()['attempts'] == 5
    # An in-flight study is drawn as running and blocks a second one.
    assert view.app.headroom_summary()['running'] is True
    assert view.app.headroom_run_block() == '余量研究正在进行'
    # A finished one lands its receipt, stops being "running", and refuses a
    # re-run on the receipt instead.
    worker.running, worker.finished = False, dict(completed=True, attempts=5)
    screen.render(None)
    assert view.app.headroom_receipt == dict(completed=True, attempts=5)
    assert view.app.headroom_summary()['running'] is False
    assert view.app.headroom_run_block() == '本进程已完成一次余量研究, 不能重跑'
    # And the same call site is the only one: a second landing would let the two
    # copies disagree about whether the study is available.
    source = Path(pynq_status.__file__).read_text(encoding='utf-8')
    assert source.count('set_headroom_status(') == 1
    assert source.count('set_headroom_receipt(') == 1


def test_a_worker_without_the_research_contract_is_not_a_crash():
    """The landing site is behind `hasattr`, like its three neighbours."""
    import pynq_status

    view = _StubView()
    screen = pynq_status._PanelScreen(view, None, None, None, rf_worker=_StubWorker())
    screen.render(None)
    assert view.app.headroom_status['available'] is False


# ------------------------------------------------- the wired study (worker)

def _bpsk_payload(phase=0.3):
    import numpy as np
    import test_rf_bpsk_receiver as B
    z = B.signal(phase=phase)
    return np.column_stack((np.round(z.real), np.round(z.imag))).astype('<i2').tobytes()


def _capture_with(payload):
    """A capture hook whose header agrees with the bytes it returns."""
    import hashlib
    import test_rf_headroom as T

    def capture(request):
        attempt = int(request['args']['attempt'])
        built = T._frame(attempt=attempt)
        body = T._roundtrip(built.header)
        body['id'] = request['id']
        body['headroom']['capture_request_id'] = request['id']
        body['timestamp_ns']=100+request['id']
        body['headroom']['iq_sha256'] = hashlib.sha256(payload).hexdigest()
        return body, payload
    return capture


def _wired_worker(tree):
    import test_rf_headroom as T
    w = worker(tree)
    w._ensure_headroom()
    w.note_headroom_capability(Snapshot(live()))
    return w, T


def test_the_worker_writes_a_complete_study_the_recorder_can_read_back(tmp_path):
    """Bullet 4: intent, raw frame, replay and receipt all reach the disk.

    The default recorder is the production one -- a study run through the worker
    leaves a directory that `read_study` accepts, which is what makes the
    receipt a commit rather than a callback nobody implemented.
    """
    w, T = _wired_worker(headroom_release(tmp_path / 'headroom'))
    daemon = T.ScriptedDaemon()
    w.client = T._client(daemon,leased=False)
    root = tmp_path / 'evidence'
    receipt = w.headroom_run(SESSION, cable_confirmed=True, evidence_root=root,token='<SET_LOCALLY>',require_hardware=False)
    daemon.settle(17)
    assert receipt['completed'] is True and receipt['attempts'] == 5
    study = H.read_study(root / SESSION)
    assert study['receipt']['outcome'] == 'COMPLETED'
    assert sorted(study['attempts']) == [1, 2, 3, 4, 5]
    for attempt, files in study['attempts'].items():
        assert files['intent.json']['attempt'] == attempt
        assert files['header.json']['iq_available'] is True
        assert len(files['frame.iq']) == H.IQ_BYTES
        # The replay is written from the saved bytes and says so.
        assert files['replay.json']['summary']['replay'] is True
        assert files['replay.json']['summary']['source_sha256'] == hashlib.sha256(
            files['frame.iq']).hexdigest()
        assert files['decision.json']['accepted'] is True
    w.client.close()


def test_the_production_off_guard_stops_a_study_whose_off_frame_synchronised(tmp_path):
    """The OFF guard is the replay, on the bytes, not a header field.

    Every attempt returns a synchronising BPSK payload, so the very first OFF
    attempt is a violation and the study stops there -- before any acceptance.
    """
    w, T = _wired_worker(headroom_release(tmp_path / 'headroom'))
    payload = _bpsk_payload()
    daemon = T.ScriptedDaemon(capture=_capture_with(payload))
    w.client = T._client(daemon,leased=False)
    receipt = w.headroom_run(SESSION, cable_confirmed=True,
                             evidence_root=tmp_path / 'evidence',token='<SET_LOCALLY>',require_hardware=False)
    daemon.settle(3)
    assert receipt['completed'] is False
    assert receipt['stopped_at'] == 'OFF_EVIDENCE'
    assert 'synchronized' in receipt['reason']
    assert receipt['attempts'] == 0
    ops = [r['op'] for r in daemon.seen]
    assert ops.count('headroom.capture') == 1
    assert 'headroom.accept' not in ops
    # The study stopped, so the daemon is unarmed and says so.
    assert daemon.armed is False
    w.client.close()


def test_a_clean_off_frame_lets_the_study_run_to_its_end(tmp_path):
    """Negative control for the guard: silence is not a violation."""
    w, T = _wired_worker(headroom_release(tmp_path / 'headroom'))
    import numpy as np
    rng = np.random.RandomState(3)
    quiet = np.column_stack((np.round(2 * rng.normal(size=4096)),
                             np.round(2 * rng.normal(size=4096)))).astype('<i2').tobytes()
    daemon = T.ScriptedDaemon(capture=_capture_with(quiet))
    w.client = T._client(daemon,leased=False)
    receipt = w.headroom_run(SESSION, cable_confirmed=True,
                                 evidence_root=tmp_path / 'evidence',token='<SET_LOCALLY>',require_hardware=False)
    daemon.settle(17)
    assert receipt['completed'] is True
    assert receipt['attempts'] == 5
    w.client.close()


def test_physical_study_without_evidence_is_refused_before_io(tmp_path):
    w,T=_wired_worker(headroom_release(tmp_path/'headroom'))
    daemon=T.ScriptedDaemon(); w.client=T._client(daemon,leased=False)
    receipt=w.headroom_run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert receipt['completed'] is False and receipt['attempts']==0
    assert receipt['stopped_at']=='CAPABILITY' and daemon.seen==[]
    assert not (tmp_path/'evidence').exists()
    w.client.close()


def test_the_replay_module_travels_with_the_research_release(tmp_path):
    """The replay is attested by the same contract that attests the plan."""
    w, _ = _wired_worker(headroom_release(tmp_path / 'headroom'))
    status = w.headroom_status()
    assert set(status['module_sha256']) == {'rf_headroom.py',
                                            'rf_headroom_replay.py'}
    assert w._headroom_replay_module is not None
    assert w._headroom_replay_module.VERSION


def test_the_panel_needs_two_taps_and_the_second_one_is_the_confirmation():
    """The study consumes the daemon's one initialization; one tap is not enough."""
    app = _app()
    app.set_headroom_status(_status(module=True, live_value=live()))
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    # Nothing registered yet: the confirmation is not available.
    assert app.headroom_study_args() == (None, 'NOT_CONFIRMED')
    ok, message = app.headroom_prompt()
    assert ok is True and '确认' in message
    assert app.headroom_pending is True
    args, why = app.headroom_study_args()
    assert why is None and args == {'cable_confirmed': True}
    # Cancel drops it, and the confirmation goes away with it.
    assert app.headroom_cancel()[0] is True
    assert app.headroom_pending is False
    assert app.headroom_study_args() == (None, 'NOT_CONFIRMED')


def test_a_finished_or_running_study_refuses_a_second_one():
    app = _app()
    app.set_headroom_status(_status(module=True, live_value=live()))
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    assert app.headroom_prompt()[0] is True
    app.set_headroom_running(True)
    assert app.headroom_run_block() == '余量研究正在进行'
    assert app.headroom_prompt()[0] is False
    assert app.headroom_summary()['running'] is True
    app.set_headroom_running(False)
    app.set_headroom_receipt(dict(completed=True, attempts=5))
    assert app.headroom_run_block() == '本进程已完成一次余量研究, 不能重跑'
    assert app.headroom_prompt()[0] is False
    assert app.headroom_summary()['receipt']['attempts'] == 5


def test_the_panel_action_reaches_the_worker_only_after_the_second_tap():
    """The production caller of `headroom_run`: the panel's confirmed action."""
    import pynq_status

    app = _app()
    app.set_headroom_status(_status(module=True, live_value=live()))
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    submitted = []

    class Worker:
        def submit_headroom_study(self, session_id, cable_confirmed=False,
                                  evidence_root=None,token_path=None):
            submitted.append((session_id, cable_confirmed))
            return {'queued': True}

    # The first tap registers and sends nothing.
    pynq_status._rx_action(app, Worker(), 'headroom_study')
    assert submitted == [] and app.headroom_pending is True
    # The second tap is the operator's confirmation, and only it submits.
    pynq_status._rx_action(app, Worker(), 'headroom_confirm')
    assert len(submitted) == 1
    session_id, cable = submitted[0]
    assert len(session_id) == 32 and all(c in '0123456789abcdef' for c in session_id)
    assert cable is True
    # Cancel sends nothing more.
    pynq_status._rx_action(app, Worker(), 'headroom_study')
    pynq_status._rx_action(app, Worker(), 'headroom_cancel')
    assert len(submitted) == 1


def test_the_panel_never_submits_a_study_that_was_not_confirmed():
    import pynq_status

    app = _app()
    app.set_headroom_status(_status(module=True, live_value=live()))
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    submitted = []

    class Worker:
        def submit_headroom_study(self, session_id, cable_confirmed=False,
                                  evidence_root=None,token_path=None):
            submitted.append(session_id)
            return {'queued': True}

    pynq_status._rx_action(app, Worker(), 'headroom_confirm')
    assert submitted == []
    # And a blocked study cannot even register an intent.
    app.set_rf_status(dict(app.rf_status, armed=True))
    assert app.headroom_prompt()[0] is False
    assert app.headroom_pending is False


def test_the_worker_runs_a_submitted_study_on_its_own_thread(tmp_path):
    """The panel's thread must never be the one that runs a five-attempt study."""
    import threading
    w, T = _wired_worker(headroom_release(tmp_path / 'headroom'))
    daemon = T.ScriptedDaemon()
    w.client = T._client(daemon,leased=False)
    w._headroom_root = tmp_path / 'evidence'
    original=w.headroom_run
    w.headroom_run=lambda *a,**k: original(*a,token='<SET_LOCALLY>',require_hardware=False,**k)
    reply = w.submit_headroom_study(SESSION, cable_confirmed=True)
    assert reply == {'queued': True}
    # A second submission while one is in flight is refused, not queued.
    assert w.submit_headroom_study(SESSION, cable_confirmed=True)['code'] == 'BUSY'
    assert w.headroom_study_status()['running'] is True
    w._headroom_thread.join(10)
    daemon.settle(17)
    assert w.headroom_study_status()['running'] is False
    receipt = w.take_headroom_result()
    assert receipt['completed'] is True and receipt['attempts'] == 5
    assert w.take_headroom_result() is None
    # And now that it has finished, the process refuses a second study.
    assert w.submit_headroom_study(SESSION, cable_confirmed=True)['code'] == 'DONE'
    assert (tmp_path / 'evidence' / SESSION / 'receipt.json').is_file()
    w.client.close()


def test_the_panel_offers_the_study_only_when_it_could_run_it():
    """The chip's reachability is the model's gate, not a second copy of it."""
    import ui_model as M
    from test_ui_view import view, _controls, center, _name

    app, _lcd, v = view(M.PAGE_FUTURE)
    app.rx_subpage = 'environment'
    app.environment_status = dict(app.environment_status, available=True)
    app.headroom_status = dict(app.headroom_status, available=True,
                               release_attested=True, live=True, reason='')
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    # Everything the environment tab shows at once must still not overlap.
    v.render()
    names = {_name(w) for w in _controls(v)}
    assert '余量研究' in names
    chip = next(w for w in _controls(v) if _name(w) == '余量研究')
    assert v.ui._hit(*center(chip)) is chip
    # Unavailable: the chip is not offered at all.
    app.headroom_status = dict(app.headroom_status, available=False, live=False)
    v.render()
    assert '余量研究' not in {_name(w) for w in _controls(v)}
    # Armed and OFFLINE but no live capability is still not enough.
    app.headroom_status = dict(app.headroom_status, available=True, live=True,
                               release_attested=True, reason='')
    app.set_rf_status(dict(app.rf_status, armed=True))
    v.render()
    assert '余量研究' not in {_name(w) for w in _controls(v)}


def test_the_first_tap_turns_the_row_into_the_confirmation():
    """The two taps share one row, so the study cannot be started twice."""
    import ui_model as M
    from test_ui_view import view, _controls, _name

    app, _lcd, v = view(M.PAGE_FUTURE)
    app.rx_subpage = 'environment'
    app.environment_status = dict(app.environment_status, available=True)
    app.headroom_status = dict(app.headroom_status, available=True,
                               release_attested=True, live=True, reason='')
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    v.render()
    assert '余量研究' in {_name(w) for w in _controls(v)}
    assert '确认研究' not in {_name(w) for w in _controls(v)}
    app.headroom_prompt()
    v.render()
    names = {_name(w) for w in _controls(v)}
    assert '确认研究' in names and '取消研究' in names
    assert '余量研究' not in names and '加载实验' not in names


# ----------------------------------------------------------------- ui_model

def _app():
    return U.UiApp()


def _status(module=True, live_value=None, available=True):
    return dict(available=available,
                release_id='release-1' if module else None,
                module_sha256={'rf_headroom.py': 'a' * 64} if module else {},
                live=live_value)


def test_the_panel_needs_the_release_and_the_live_daemon():
    app = _app()
    app.set_headroom_status(_status())                       # neither
    assert app.headroom_status['available'] is False
    app.set_headroom_status(_status(module=True))            # release only
    assert app.headroom_status['release_attested'] is True
    assert app.headroom_status['live'] is False
    assert app.headroom_status['available'] is False
    app.set_headroom_status(_status(module=False, live_value=live()))  # live only
    assert app.headroom_status['release_attested'] is False
    assert app.headroom_status['live'] is True
    assert app.headroom_status['available'] is False
    app.set_headroom_status(_status(module=True, live_value=live()))
    assert app.headroom_status['available'] is True


def test_a_partial_or_stale_capability_never_becomes_available():
    app = _app()
    for value in (live(minor=5), live(version='1.0.3'), live(study='HEADROOM-2'),
                  live(operations=H.CONTROL_OPS[:2]), {}, 'HEADROOM-1'):
        app.set_headroom_status(_status(module=True, live_value=value))
        assert app.headroom_status['available'] is False
        assert app.headroom_run_block() is not None


def test_the_panel_summary_states_the_fixed_plan_and_never_claims_it_ran():
    app = _app()
    app.set_headroom_status(_status(module=True, live_value=live()))
    summary = app.headroom_summary()
    assert summary['available'] is True and summary['running'] is False
    assert summary['attempts'] == 5
    assert summary['order'] == ['off-before', 'normal', 'narrowband', 'pulse',
                                'off-after']
    assert summary['attenuation_mdB'] == 46000
    # One domain since 2026-10-17: teaching and research report the same value.
    assert summary['teaching_attenuation_mdB'] == summary['attenuation_mdB'] == 46000
    assert summary['receipt'] is None


def test_the_run_block_requires_unarmed_offline_before_explicit_confirmation():
    app = _app()
    app.set_headroom_status(_status(module=True, live_value=live()))
    assert app.headroom_run_block() is not None
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    assert app.headroom_run_block() is None
    app.set_rf_status(dict(app.rf_status, armed=True))
    assert app.headroom_run_block() is not None
    app.set_rf_status(dict(app.rf_status, armed=True, daemon_state='TX_ACTIVE'))
    assert app.headroom_run_block() is not None
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    assert app.headroom_run_block() is None
    app.headroom_receipt = dict(completed=True, attempts=5)
    assert app.headroom_run_block() is not None


def test_the_panel_does_not_offer_a_study_it_cannot_run():
    app = _app()
    app.set_headroom_status(_status(module=True, live_value=live(minor=5)))
    app.set_rf_status(dict(app.rf_status, armed=False, daemon_state='OFFLINE'))
    assert app.headroom_run_block() is not None
    assert app.headroom_summary()['available'] is False
