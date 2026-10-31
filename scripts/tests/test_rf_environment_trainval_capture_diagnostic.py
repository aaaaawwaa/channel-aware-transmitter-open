"""D3 regression: a failed capture keeps its own reason, through the REAL chain.

Three shapes, all driven through the real save/inheritance chain
(TrainvalSession._save -> EnvironmentSession._save -> LiveUiSession._save) and the real
BOARD.run_session - never a stub's successful return value:

1. a local UI refusal (a named code and reason) with no frame;
2. a worker failure status with no frame;
3. a COMPLETE frame, which must still run the original identity/envelope checks (and
   pass them) - and still be refused by name when the identity differs.

Before this fix, cases 1/2 came out as
'FRAME_RELEASE_MISMATCH: frame normal-01 belongs to release None' and the saved
normal-01.json held only the worker's idle status: no ui_diagnostic, no UI code, no
reason.  That is the shape DSH reproduced on 2026-10-18 with
evidence/rf-teaching/codex-trainval-diagnostic-2026-10-18/reproduce_masked_refusal.py.

Nothing here constructs a board session, opens a socket or touches hardware: the
session constructor is skipped (the same __new__ seam the review used) and only the
UI/worker/client transport is a memory double.
"""
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'scripts' / 'tests'),
               str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import trainval_stub_board as STUB  # noqa: E402
from scripts import rf_environment_trainval_board_session as BOARD  # noqa: E402
from scripts import rf_environment_trainval_collect as COLLECT  # noqa: E402
from scripts import rf_environment_trainval_session as SESSION  # noqa: E402

RELEASE = 'd3diagnostic0000000000000000000000'
OTHER_RELEASE = 'someone-elses-release-00000000000'
REFUSAL = dict(state='refused', code='BUSY', reason='synthetic local refusal')
WORKER_FAILURE = dict(state='error', code='RX_TIMEOUT',
                      reason='worker gave up waiting for the RX engine')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope='module')
def domain(tmp_path_factory):
    """The 46 dB domain and environment version of the offline candidate fixture."""
    candidate = STUB.build_offline_candidate(tmp_path_factory.mktemp('d3') / 'candidate')
    declaration = COLLECT.candidate_declaration(Path(candidate['path']))
    return dict(domain=declaration['domain'],
                environment_version=declaration['environment_version'])


class Harness:
    """A real TrainvalSession with only its board transport replaced.

    capture() (LiveUiSession -> EnvironmentSession -> TrainvalSession), the whole _save
    inheritance chain, run_session, evaluate_frame and build_session_documents are the
    production code; the doubles below stand in for the socket, the worker thread and
    the offscreen UiView.
    """

    def __init__(self, output, *, mode, domain, environment_version, split='train',
                 index=1, expected=RELEASE, frame_release=None, cleanup_raises=False):
        base = BOARD.trainval_session_class()
        self.mode = mode
        self.frame_release = frame_release or expected
        self.expected = expected
        self.domain = dict(domain)
        self.environment_version = environment_version
        self.split, self.index = split, index
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.cleanup_raises = cleanup_raises
        self.ui_events = []

        class DiagnosticSession(base):
            """The real class plus a tag hook, so the transport knows which frame to hand over."""

            def capture(inner, tag, physical):
                inner._harness_tag = tag
                return super().capture(tag, physical)

        s = DiagnosticSession.__new__(DiagnosticSession)
        self.session = s
        document, slots = BOARD.frame_plan(split, index)
        s.expected = expected
        s.output = self.output
        s._domain = self.domain
        s._environment_version = environment_version
        s._session_block = BOARD.session_block_of(document)
        s._slots = slots
        s._observed = {}
        s._rx_page = 'rx'
        s._teaching = False
        s._panel_configured = True
        s.ui_events = self.ui_events
        self.session_block = dict(s._session_block)
        self.seed = self.session_block['seed']
        # The RF domain lookup and the RF setup/scene legs belong to the installed
        # release and the board's RF chain; neither is under test here.
        s.requested_rf = lambda: dict(self.domain)
        s.setup = lambda seed: setattr(self, 'seed_used', seed)
        s.scene = lambda kind: setattr(self, 'scene_kind', kind)
        s.view = SimpleNamespace(rx_loopback_btn='first', rx_loopback_confirm_btn='confirm',
                                 rx_capture_btn='capture')
        s._tap = self._tap
        s.panel_tick = self._install
        if cleanup_raises:
            def cleanup():
                raise RuntimeError('disarm transport died')
            s.cleanup = cleanup
        self.app = SimpleNamespace(
            page=None, rx_subpage=None, rx_capture={}, rf_status={},
            _rf_control_online=True, _rf_control_state='READY', _rf_control_armed=True,
            rf_control={}, _rx_record_rf_config=dict(self.domain),
            _rx_record_scenario=None, _rf_applied_parameters=dict(self.domain),
            rx_demod_result=None, set_rf_status=lambda value: None)
        self.app.rx_demod_summary = lambda: dict(ber=None, reference_sync=None, current=True)
        self.app.rx_environment_summary = lambda: dict(available=True, current=True)
        self.app.rx_capture_capability = lambda: (None, None)
        s.app = self.app
        self.worker = SimpleNamespace(is_capturing=lambda: False, stop=lambda: None,
                                      cancel_capture=lambda: None)
        s.worker = self.worker
        self.client = SimpleNamespace(frame=None, close=lambda: None)
        self.client.poll = lambda: SimpleNamespace(to_dict=self._readback)
        self.client.observe_rx_gain = lambda: dict(mode=2, index=76, source='harness')
        self.client.control_disarm = lambda: dict(armed=False)
        s.client = self.client
        self.frames = []

    # --------------------------------------------------------------- the transport
    def _readback(self):
        return dict(online=True, stale=False, armed=False, release_id=self.expected,
                    pl_status=dict(io_ok=True, rx=dict(armed=False),
                                   tx=dict(source_selected=False)),
                    daemon_state='idle', tx_session_ready=True, rx_session_ready=True,
                    received_at_monotonic=time.monotonic())

    def _frame_and_payload(self, tag):
        from rf_bpsk_pipeline import DemodConfig, analyze_demod
        from rf_bpsk_receiver import BpskConfig
        from rf_environment import analyze_environment
        from rf_experiment_scenarios import ScenarioConfig, generate_scenario
        position = BOARD.FRAME_TAGS.index(tag)
        scenario = 'normal' if position < SESSION.NORMAL_PER_SESSION else 'off'
        codes = SESSION._fixture_codes(self.split, self.index, position, self.seed)
        stub = STUB.StubSession(self.split, self.index, self.output,
                                release=self.frame_release, domain=self.domain,
                                environment_version=self.environment_version)
        frame = stub._frame(tag, scenario == 'normal', codes)
        submitted = DemodConfig(BpskConfig('BPSK', 3000000, 'RECT', self.seed),
                                3, 1, 4096, 60000000)
        envelope = analyze_demod(frame, submitted)
        report = analyze_environment(frame, envelope, self.domain, STUB.product_baseline())
        declaration = generate_scenario(ScenarioConfig(
            kind=scenario, amplitude=60, prbs7_seed=self.seed)).declaration_dict()
        payload = dict(frame=frame.to_dict(), analysis=None, model_result=None,
                       demod_result=envelope, environment_result=report, constellation=None)
        return frame, payload, declaration

    def _tap(self, widget):
        self.ui_events.append(widget)
        if widget == 'first':
            self.app.rx_capture = dict(state='idle', pending_action='loopback')
            return
        self.app.rx_capture = dict(self.final_ui_state)

    def _install(self):
        """Land the frame/status the worker would have published (one panel tick)."""
        if self.mode == 'complete':
            frame, payload, declaration = self._frame_and_payload(self.session._harness_tag)
            self.frames.append(frame)
            self.app._rx_record_scenario = declaration
            self.app.rx_demod_result = payload['demod_result']
            self.client.frame = frame
            self.session._last_payload = payload
            self.session._last_status = dict(state='done', code=None, reason='')
            self.app.rx_capture = dict(state='done', code=None, reason='',
                                       capture_id=frame.capture_id)
            return
        self.client.frame = None
        self.session._last_payload = None
        self.session._last_status = dict(self.final_worker_status)
        self.app.rx_capture = dict(self.final_ui_state)

    def run(self, result_path):
        return BOARD.run_session(
            self.session, split=self.split, index=self.index, output=self.output,
            expected_release=self.expected, domain=self.domain,
            environment_version=self.environment_version,
            result_path=result_path, origin=BOARD.STUB_ORIGIN,
            requested_rf=dict(self.domain))


def harness_for(tmp_path, domain, *, mode, **kwargs):
    value = Harness(tmp_path / 'session', mode=mode, domain=domain['domain'],
                    environment_version=domain['environment_version'], **kwargs)
    if mode == 'refused':
        value.final_ui_state = dict(REFUSAL)
        value.final_worker_status = dict(state='idle', code=None, reason='')
    elif mode == 'worker_failure':
        value.final_ui_state = dict(WORKER_FAILURE)
        value.final_worker_status = dict(WORKER_FAILURE)
    else:
        value.final_ui_state = dict(state='done', code=None, reason='')
        value.final_worker_status = dict(state='done', code=None, reason='')
    return value


# ------------------------------------------------------- (1)(2) failed captures

def _failed(tmp_path, domain, mode, **kwargs):
    harness = harness_for(tmp_path, domain, mode=mode, **kwargs)
    result = harness.run(tmp_path / 'session-result.json')
    return harness, result, tmp_path / 'session'


@pytest.mark.parametrize('mode,ui_state,ui_code,ui_reason,worker_state,worker_code,worker_reason', (
    # a local UI refusal never reaches the worker, so the worker keeps its own status
    ('refused', 'refused', 'BUSY', REFUSAL['reason'], 'idle', None, ''),
    # a worker failure is published by the worker: same code, same reason
    ('worker_failure', 'error', 'RX_TIMEOUT', WORKER_FAILURE['reason'], 'error', 'RX_TIMEOUT',
     WORKER_FAILURE['reason']),
))
def test_a_failed_capture_keeps_its_own_reason(tmp_path, domain, mode, ui_state, ui_code,
                                               ui_reason, worker_state, worker_code,
                                               worker_reason):
    """The saved failure product carries the raw UI/worker reason, not a release None."""
    harness, result, session_dir = _failed(tmp_path, domain, mode)
    assert result['passed'] is False
    assert result['stop_cause'] == 'CAPTURE_FAILED', result
    assert 'incomplete capture' in result['error'], result['error']
    assert 'FRAME_RELEASE_MISMATCH' not in (result['error'] or '')

    metadata = load(session_dir / 'normal-01.json')
    diagnostic = metadata['ui_diagnostic']
    assert diagnostic['phase'] == 'capture'
    assert diagnostic['ui_state'] == ui_state
    assert diagnostic['ui_code'] == ui_code
    assert diagnostic['ui_reason'] == ui_reason
    assert diagnostic['worker_state'] == worker_state
    assert diagnostic['worker_code'] == worker_code
    assert diagnostic['worker_reason'] == worker_reason
    assert diagnostic['frame_present'] is False and diagnostic['payload_present'] is False
    assert diagnostic['frame_capture_id'] is None
    assert diagnostic['readback']['online'] is True and diagnostic['egress_safe'] is True
    # no frame: no IQ artifact, and no success-frame envelope was bound
    assert not (session_dir / 'normal-01.iq').exists()
    assert 'session' not in metadata and 'trainval' not in metadata
    assert metadata.get('release_id') is None
    # the result carries the SAME bytes the failure left behind, unrewritten
    assert result['stop_diagnostic']['ui_code'] == ui_code
    assert result['stop_diagnostic']['metadata_sha256'] == sha256(session_dir / 'normal-01.json')
    assert result['cleanup_error'] is None
    assert result['session_closed'] is True and result['disarm_ack'] is True
    assert harness.session._last_status is not None


def test_a_local_refusal_is_not_masked_by_a_later_save(tmp_path, domain):
    """The exact 2026-10-18 reproducer shape: the failure file is never rewritten."""
    harness, result, session_dir = _failed(tmp_path, domain, 'refused')
    assert sha256(session_dir / 'normal-01.json') == result['stop_diagnostic']['metadata_sha256']
    assert result['stop_frame'] == 'normal-01'
    assert result['frames'] == []
    assert load(tmp_path / 'session-result.json')['error'] == result['error']


def test_a_cleanup_failure_does_not_overwrite_the_capture_reason(tmp_path, domain):
    harness, result, session_dir = _failed(tmp_path, domain, 'refused', cleanup_raises=True)
    assert result['stop_cause'] == 'CAPTURE_FAILED'
    assert 'incomplete capture' in result['error'], result
    assert 'disarm transport died' not in result['error']
    assert 'disarm transport died' in result['cleanup_error']
    assert result['session_closed'] is False and result['disarm_ack'] is False
    assert result['stop_diagnostic']['ui_code'] == 'BUSY'


# ------------------------------------------------------------ (3) complete frame

def test_a_complete_frame_still_runs_the_identity_and_envelope_checks(tmp_path, domain):
    """The success path is unchanged: full 11 frames, bound envelope, green session."""
    harness = harness_for(tmp_path, domain, mode='complete')
    result = harness.run(tmp_path / 'session-result.json')
    session_dir = tmp_path / 'session'
    assert result['passed'] is True, result
    assert result['stop_cause'] is None and result['manifest_written'] is True
    assert [row['tag'] for row in result['frames']] == list(BOARD.FRAME_TAGS)
    assert SESSION.check_session(session_dir)['frame_count'] == len(BOARD.FRAME_TAGS)
    metadata = load(session_dir / 'normal-01.json')
    sidecar = load(session_dir / 'normal-01.environment.json')
    frame_block = BOARD.frame_block_of(harness.session_block)
    assert metadata['session'] == sidecar['session'] == frame_block
    assert metadata['release_id'] == RELEASE and sidecar['release_id'] == RELEASE
    assert metadata['trainval']['truth'] == 'normal'
    assert metadata['trainval']['physical'] is True
    assert metadata['ui_diagnostic']['phase'] == 'capture'
    assert metadata['ui_diagnostic']['frame_present'] is True
    assert (session_dir / 'normal-01.iq').is_file()
    assert (session_dir / 'session-manifest.json').is_file()


def test_a_complete_frame_with_another_release_is_still_refused_by_name(tmp_path, domain):
    """The identity check is NOT relaxed for a frame that looks complete."""
    harness, result, session_dir = _failed(tmp_path, domain, 'complete',
                                           frame_release=OTHER_RELEASE)
    assert result['passed'] is False
    assert result['stop_cause'] == 'FRAME_RELEASE_MISMATCH', result
    assert 'normal-01' in result['error']
    assert result['manifest_written'] is False
    metadata = load(session_dir / 'normal-01.json')
    assert metadata['release_id'] == OTHER_RELEASE      # the frame's own bytes, kept
    assert 'session' not in metadata and 'trainval' not in metadata
    assert (session_dir / 'normal-01.iq').is_file()     # the IQ evidence is retained
    assert not (session_dir / 'session-manifest.json').exists()
