"""Red-first tests for the 12-frame 46 dB telemetry diagnostic (offline, no board).

Covered surfaces, per the implementation brief:
1. per-frame telemetry landing and NOT_AVAILABLE -- a value that cannot be read is
   never replaced by a request value, a default or a guess;
2. every pilot hard-stop branch (initial unsafe / wrong release, unsafe egress,
   missing evidence, duplicate identity, CLIPPED / INVALID_IQ, capture exception,
   close unconfirmed);
3. the budget hard gate (3 initializations or 12 frames);
4. the authorization gate (--cable-confirmed, an output directory that must not
   exist, the approved release and the cnn-bpsk-environment-v1 contract);
5. the frozen 18-frame pilot entries -- byte-identical to the readiness inventory
   and still producing 18 records.

Nothing here opens a socket, creates an Overlay, imports a board module or touches
hardware: the transport is a memory double and every assertion is about the
runner-side telemetry envelope.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from rf_rx_types import RxIqFrame, RxReadback  # noqa: E402
from scripts import rf_environment_pilot as FROZEN  # noqa: E402
from scripts import rf_environment_telemetry_pilot as PILOT  # noqa: E402
from scripts import rf_environment_telemetry_run as RUN  # noqa: E402
from scripts import rf_environment_telemetry_session as T  # noqa: E402

REL = T.APPROVED_RELEASE
MDB = T.APPROVED_ATTENUATION_MDB

# The four runner files the readiness round inventoried (work/telemetry-inventory.json
# inputs_sha256).  Two are the frozen 18-frame entries; two are the insertion-point
# files this work package must not touch either.
FROZEN_ENTRY_SHA256 = {
    'scripts/rf_environment_pilot.py':
        'ed676c1555f07348d5d1dc8ed5eeee5f562e2d59cb2c0ed2efff70eae1d409c3',
    'scripts/rf_environment_board_run.py':
        '669d43a542b5b92719e48db58499e31794d99992b78a02da884c758f7168d3eb',
    'scripts/rf_environment_board_session.py':
        '9ee7166350b98759d7390411bbfbbe7e0406241016961da4f8d4d24cf2415623',
    'scripts/rf_bpsk_ui_board_session.py':
        'dcb3a906a4997739d06fd0d1518dce203875332cc059046a3a6e6043496aa63d',
}


def _frame(physical=True, payload=b'\x00' * 16, samples=4):
    return RxIqFrame(
        capture_id='rx-03b9b883-g1-c0-1', payload=payload, samples=samples,
        sample_rate_hz=60000000, ratio=0, daemon_timestamp_ns=9946138350668,
        connection_generation=1, config_revision=0, request_id=7, release_id=REL,
        op='loopback.physical.capture' if physical else 'rx.capture', physical=physical,
        pre_readback=RxReadback(io_ok=True, rx_armed=False, tx_source_selected=False,
                                sampled_at_monotonic_ns=11),
        post_readback=RxReadback(io_ok=True, rx_armed=False, tx_source_selected=False,
                                 sampled_at_monotonic_ns=12),
        completed_at_monotonic=1234.5)


def _gain_ok():
    return dict(available=True, source='hardware', scope='sequential_reads_not_atomic',
                started_at_monotonic_ns=10, finished_at_monotonic_ns=20, rx_enable_raw=0x41,
                agc_config1_raw=234, agc_config2_raw=8, mode_rx1=2, gain_index_before=76,
                gain_index_after=76, digital_gain_code=0, index_stable=True)


def _gain_reply(observation=None, reason=None):
    return dict(release_id=REL, daemon='1.0.4', protocol_major=1, protocol_minor=6,
                gain_observation=observation, reason=reason)


def _timing_ok():
    return dict(available=True, scope='monotonic_api_brackets_not_sample_edges',
                capture_request_id=7, capture_timestamp_ns=9946138350668, samples=4, ratio=0,
                tx_on_begin_ns=100, tx_on_end_ns=110, rx_arm_begin_ns=120, rx_arm_end_ns=130,
                drain_begin_ns=140, drain_end_ns=150, tx_off_begin_ns=160, tx_off_end_ns=170,
                settle_wait_requested_us=0)


def _timing_reply(trace=None, reason=None):
    return dict(release_id=REL, daemon='1.0.4', protocol_major=1, protocol_minor=6,
                last_capture_timing=trace, reason=reason)


def _readback():
    return dict(lo_hz=80000000, sample_rate_hz=60000000, rx_bandwidth_hz=30000000,
                tx_bandwidth_hz=30000000, attenuation_mdB=MDB, phy_mode_1r1t=0,
                product_id=1, hardware_verified=True)


class FakeClient:
    """The two read-only telemetry calls, with a scripted reply per call."""

    def __init__(self, gains=None, timings=None):
        self.gains = list(gains) if gains is not None else [_gain_reply(dict(_gain_ok()))]
        self.timings = list(timings) if timings is not None else [_timing_reply(dict(_timing_ok()))]
        self.gain_calls = 0
        self.timing_calls = 0
        self.frame = None

    def observe_rx_gain(self):
        self.gain_calls += 1
        reply = self.gains[min(self.gain_calls - 1, len(self.gains) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def observe_capture_timing(self, frame):
        self.timing_calls += 1
        reply = self.timings[min(self.timing_calls - 1, len(self.timings) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeBase:
    """Stands in for EnvironmentSession; the reviewed capture chain is not under test."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.capture_calls = []
        self.wait_calls = []
        self.control_results = {}

    def capture(self, tag, physical):
        self.capture_calls.append((tag, physical))
        row = self.rows.pop(0) if self.rows else dict(
            capture_id=tag, iq_sha256=tag, release_id=REL, egress_safe=True,
            environment=dict(label='CLEAR', reason=''))
        if isinstance(row, Exception):
            raise row
        return dict(row)

    def _wait_control(self, action, deadline=25.0):
        self.wait_calls.append(action)
        return self.control_results.get(action, dict(ok=True, code=None, message=''))


class Harness(T.TelemetryMixin, FakeBase):
    def __init__(self, output, client=None, rows=None, readbacks=None):
        FakeBase.__init__(self, rows)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.client = client or FakeClient()
        self.control_results = dict(readbacks or {})   # what the worker would land
        self._control_readbacks = {}                   # nothing recorded yet


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


# ---------------------------------------------------------------- 1. telemetry

def test_gain_fields_land_per_frame_and_the_file_round_trips(tmp_path):
    client = FakeClient(timings=[_timing_reply(dict(_timing_ok()))])
    client.frame = _frame(True)
    harness = Harness(tmp_path, client=client)
    row = harness.capture('s00-normal-00', True)

    telemetry = _load(tmp_path / 's00-normal-00.telemetry.json')
    assert telemetry['schema'] == T.TELEMETRY_SCHEMA
    assert telemetry['tag'] == 's00-normal-00' and telemetry['physical'] is True
    assert telemetry['pre_frame_gain']['gain_index_before'] == 76
    assert telemetry['pre_frame_gain']['gain_index_after'] == 76
    assert telemetry['pre_frame_gain']['mode_rx1'] == 2
    assert telemetry['pre_frame_gain']['agc_config1_raw'] == 234
    assert telemetry['pre_frame_gain']['agc_config2_raw'] == 8
    assert telemetry['pre_frame_gain']['index_stable'] is True
    assert telemetry['pre_frame_gain']['available'] is True
    assert telemetry['post_frame_gain']['gain_index_before'] == 76
    assert telemetry['capture_timing']['available'] is True
    assert telemetry['capture_timing']['tx_on_begin_ns'] == 100
    assert telemetry['capture_timing']['scope'] == 'monotonic_api_brackets_not_sample_edges'
    assert telemetry['frame']['daemon_timestamp_ns'] == 9946138350668
    assert telemetry['frame']['capture_id'] == 'rx-03b9b883-g1-c0-1'
    assert telemetry['frame']['release_id'] == REL
    assert telemetry['frame']['pre_sampled_at_monotonic_ns'] == 11
    assert telemetry['frame']['post_sampled_at_monotonic_ns'] == 12
    # P1 runs before the capture and P3 after it: exactly one read on each side
    assert [call[0] for call in harness.capture_calls] == ['s00-normal-00']
    assert client.gain_calls == 2 and client.timing_calls == 1
    # ... and the row returned to the campaign carries the same telemetry
    assert row['telemetry'] == telemetry
    assert row['telemetry_file'] == 's00-normal-00.telemetry.json'


def test_gain_unavailable_writes_not_available_with_the_daemon_reason(tmp_path):
    client = FakeClient(gains=[_gain_reply(None, 'NOT_READY_OR_UNSUPPORTED')])
    harness = Harness(tmp_path, client=client)
    harness.capture('s00-normal-00', True)
    gain = _load(tmp_path / 's00-normal-00.telemetry.json')['pre_frame_gain']
    assert gain['available'] is False
    assert gain['reason'] == 'NOT_READY_OR_UNSUPPORTED'
    assert gain['gain_index_before'] == T.NOT_AVAILABLE
    assert gain['gain_index_after'] == T.NOT_AVAILABLE
    assert gain['mode_rx1'] == T.NOT_AVAILABLE
    assert gain['index_stable'] == T.NOT_AVAILABLE
    assert gain['identity']['protocol_minor'] == 6


def test_gain_transport_error_is_not_available_and_named(tmp_path):
    client = FakeClient(gains=[TimeoutError('gain read timed out')])
    harness = Harness(tmp_path, client=client)
    harness.capture('s00-normal-00', True)
    gain = _load(tmp_path / 's00-normal-00.telemetry.json')['pre_frame_gain']
    assert gain['available'] is False
    assert gain['reason'] == 'OBSERVE_RX_GAIN_ERROR'
    assert 'TimeoutError' in gain['error'] and 'gain read timed out' in gain['error']
    assert gain['gain_index_before'] == T.NOT_AVAILABLE


def test_off_frame_never_requests_timing_and_says_so(tmp_path):
    client = FakeClient()
    client.frame = _frame(False)
    harness = Harness(tmp_path, client=client)
    harness.capture('s00-off-00', False)
    timing = _load(tmp_path / 's00-off-00.telemetry.json')['capture_timing']
    assert timing['available'] is False
    assert timing['reason'] == 'TIMING_BRACKETS_ONLY_ON_PHYSICAL_FRAMES'
    assert timing['tx_on_begin_ns'] == T.NOT_AVAILABLE
    assert client.timing_calls == 0


def test_physical_timing_unavailable_keeps_the_daemon_reason(tmp_path):
    client = FakeClient(timings=[_timing_reply(None, 'CLOCK_UNAVAILABLE')])
    client.frame = _frame(True)
    harness = Harness(tmp_path, client=client)
    harness.capture('s00-normal-00', True)
    timing = _load(tmp_path / 's00-normal-00.telemetry.json')['capture_timing']
    assert timing['available'] is False and timing['reason'] == 'CLOCK_UNAVAILABLE'
    assert timing['capture_request_id'] == T.NOT_AVAILABLE


def test_timing_transport_error_is_not_available_and_named(tmp_path):
    client = FakeClient(timings=[RuntimeError('timing read failed')])
    client.frame = _frame(True)
    harness = Harness(tmp_path, client=client)
    harness.capture('s00-normal-00', True)
    timing = _load(tmp_path / 's00-normal-00.telemetry.json')['capture_timing']
    assert timing['available'] is False and timing['reason'] == 'OBSERVE_TIMING_ERROR'
    assert 'timing read failed' in timing['error']


def test_unreadable_items_are_declared_per_frame_never_faked(tmp_path):
    client = FakeClient()
    client.frame = _frame(True)
    harness = Harness(tmp_path, client=client)
    harness.capture('s00-normal-00', True)
    telemetry = _load(tmp_path / 's00-normal-00.telemetry.json')
    declared = telemetry['not_available']
    assert set(declared) == set(T.NOT_AVAILABLE_ITEMS)
    for item in declared.values():
        assert item['value'] == T.NOT_AVAILABLE
        assert item['reason']
    # the three request-side traps of this diagnostic: a per-frame TX attenuation, an
    # in-window gain trace and an absolute level must never be a number here
    assert declared['per_frame_tx_attenuation_mdB']['value'] == T.NOT_AVAILABLE
    assert declared['intra_frame_rx_gain_trace']['value'] == T.NOT_AVAILABLE
    assert declared['rf_level_dbm']['value'] == T.NOT_AVAILABLE
    assert declared['headroom_lease_nonce']['value'] == T.NOT_AVAILABLE
    assert not isinstance(declared['per_frame_tx_attenuation_mdB']['value'], int)


def test_telemetry_is_written_even_when_the_capture_fails(tmp_path):
    client = FakeClient(gains=[_gain_reply(dict(_gain_ok())),
                               RuntimeError('session gone after the refusal')])
    harness = Harness(tmp_path, client=client,
                      rows=[TimeoutError('capture deadline')])
    with pytest.raises(TimeoutError):
        harness.capture('s00-normal-00', True)
    telemetry = _load(tmp_path / 's00-normal-00.telemetry.json')
    assert telemetry['error'].startswith('TimeoutError')
    assert telemetry['post_frame_gain']['reason'] == 'OBSERVE_RX_GAIN_ERROR'
    assert telemetry['post_frame_gain']['gain_index_before'] == T.NOT_AVAILABLE
    assert telemetry['frame']['capture_id'] == T.NOT_AVAILABLE


def test_session_attenuation_readback_is_retained_from_init_and_configure(tmp_path):
    harness = Harness(tmp_path, readbacks={'init': dict(ok=True, result=_readback()),
                                           'configure': dict(ok=True, result=_readback())})
    assert harness._wait_control('init')['ok'] is True
    harness._wait_control('configure')
    harness._wait_control('load')          # not a readback carrier
    assert sorted(harness.wait_calls) == ['configure', 'init', 'load']
    harness.capture('s00-normal-00', True)
    readback = _load(tmp_path / 's00-normal-00.telemetry.json')['session_attenuation_readback']
    assert readback['init']['attenuation_mdB'] == MDB
    assert readback['init']['hardware_verified'] is True
    assert readback['configure']['attenuation_mdB'] == MDB
    assert readback['configure']['reason'] is None


def test_session_readback_absent_writes_not_available(tmp_path):
    harness = Harness(tmp_path)
    harness._wait_control('init')
    harness.capture('s00-normal-00', True)
    readback = _load(tmp_path / 's00-normal-00.telemetry.json')['session_attenuation_readback']
    assert readback['init']['attenuation_mdB'] == T.NOT_AVAILABLE
    assert readback['init']['hardware_verified'] == T.NOT_AVAILABLE
    assert readback['init']['reason'] == 'SESSION_READBACK_NOT_RETAINED'
    assert readback['configure']['reason'] == 'SESSION_READBACK_NOT_RETAINED'


# ------------------------------------------------------- 2. the P2 client wrapper

class InnerClient:
    def __init__(self):
        self.calls = []

    def capture(self, args, physical=False):
        self.calls.append((dict(args), physical))
        return 'frame'

    def poll(self):
        return 'polled'


def test_capture_timing_is_injected_for_physical_frames_only():
    inner = InnerClient()
    wrapper = T.CaptureTimingClient(inner)
    original = {'samples': 4096, 'ratio': 0}
    assert wrapper.capture(original, physical=True) == 'frame'
    assert inner.calls[0] == ({'samples': 4096, 'ratio': 0, 'observe_timing': True}, True)
    assert original == {'samples': 4096, 'ratio': 0}      # the caller's dict is not mutated
    wrapper.capture({'samples': 4096, 'ratio': 0}, physical=False)
    assert 'observe_timing' not in inner.calls[1][0]
    with pytest.raises(ValueError):
        wrapper.capture({'samples': 4096, 'ratio': 0, 'observe_timing': True}, physical=False)
    assert len(inner.calls) == 2                          # the refusal never reached the client
    assert wrapper.poll() == 'polled'                     # everything else delegates


def test_wrap_capture_requests_wraps_once_and_keeps_delegation():
    inner = InnerClient()

    class Evidence:
        def __init__(self, client):
            self.client = client

    evidence = Evidence(inner)
    assert T.wrap_capture_requests(evidence) is evidence
    assert isinstance(evidence.client, T.CaptureTimingClient)
    T.wrap_capture_requests(evidence)
    assert isinstance(evidence.client.inner, InnerClient)
    assert evidence.client.poll() == 'polled'


# ------------------------------------------------------------ 3. pilot hard stops

def _full_telemetry(tag, physical):
    return dict(schema=T.TELEMETRY_SCHEMA, tag=tag, physical=bool(physical),
                frame=dict(capture_id=tag), pre_frame_gain=dict(available=True),
                post_frame_gain=dict(available=True), capture_timing=dict(available=bool(physical)),
                session_attenuation_readback=dict(init=dict(attenuation_mdB=MDB)),
                not_available={k: dict(value=T.NOT_AVAILABLE, reason=v)
                               for k, v in T.NOT_AVAILABLE_ITEMS.items()})


class PilotSession:
    """A memory double for TelemetrySession: telemetry file plus a label per frame."""

    def __init__(self, events, output, *, unsafe=False, release=REL, faults=None):
        self.events, self.output = events, Path(output)
        self.unsafe, self.release = unsafe, release
        self.faults = dict(faults or {})
        self.output.mkdir(parents=True, exist_ok=True)

    def initial(self):
        return dict(safe=not self.unsafe, release_id=self.release)

    def setup(self, seed):
        self.events.append(('setup', seed))

    def scene(self, kind):
        self.events.append(('scene', kind))

    def capture(self, tag, physical):
        self.events.append(('capture', tag, physical))
        row = dict(release_id=self.release, capture_id=tag, iq_sha256=tag, egress_safe=True,
                   environment=dict(label='CLEAR', reason=''),
                   telemetry=_full_telemetry(tag, physical),
                   telemetry_file=tag + '.telemetry.json')
        for key, value in self.faults.get(tag, {}).items():
            if value is None and key in row:
                row.pop(key)
            else:
                row[key] = value
        if isinstance(row.get('telemetry'), dict) and row['telemetry']:
            (self.output / (tag + '.telemetry.json')).write_text(
                json.dumps(row['telemetry']), encoding='utf-8')
        return row

    def cleanup(self):
        self.events.append(('cleanup',))
        return dict(safe=True, disarm_ack=True)

    def close(self):
        self.events.append(('close',))


def _factory(events, output, **kwargs):
    made = []

    def factory():
        session = PilotSession(events, output, **kwargs)
        made.append(session)
        return session
    return factory, made


def test_twelve_frame_campaign_shape_budget_scenes_and_telemetry(tmp_path):
    events = []
    factory, made = _factory(events, tmp_path)
    result = PILOT.campaign(factory, tmp_path, REL)
    assert result['passed'] is True, result
    assert result['stop_cause'] is None
    assert result['frames'] == 12 and len(result['records']) == 12
    assert result['initializations'] == 3 and len(result['sessions']) == 3
    assert len(made) == 3
    assert [row['tag'] for row in result['records']] == [
        's00-off-00', 's00-normal-00', 's00-normal-01', 's00-off-01',
        's01-off-00', 's01-normal-00', 's01-normal-01', 's01-off-01',
        's02-off-00', 's02-normal-00', 's02-normal-01', 's02-off-01']
    assert [row['truth'] for row in result['records']] == ['off', 'normal', 'normal', 'off'] * 3
    assert [row['session_index'] for row in result['records']] == [0] * 4 + [1] * 4 + [2] * 4
    assert [row['reference_seed'] for row in result['records']] == [1] * 4 + [37] * 4 + [73] * 4
    assert [e for e in events if e[0] == 'setup'] == [('setup', 1), ('setup', 37), ('setup', 73)]
    # scene('normal') exactly once before EACH normal frame -- six times, never for OFF
    assert [e for e in events if e[0] == 'scene'] == [('scene', 'normal')] * 6
    captures = [e for e in events if e[0] == 'capture']
    assert [c[2] for c in captures] == [False, True, True, False] * 3
    assert events.count(('cleanup',)) == 3 and events.count(('close',)) == 3
    assert result['attenuation_mdB'] == MDB
    assert result['split'] == PILOT.TELEMETRY_SPLIT
    assert result['formal_physical'] == 'NOT_RUN' and result['s2_stage_passed'] is False
    assert result['telemetry_files_present'] is True
    assert result['not_available_complete'] is True
    assert (tmp_path / 'result.json').is_file()
    for row in result['records']:
        landed = _load(tmp_path / row['telemetry_file'])
        assert landed == row['telemetry'] and landed['tag'] == row['tag']


def test_initial_not_safe_or_wrong_release_stops_before_any_capture(tmp_path):
    events = []
    factory, made = _factory(events, tmp_path, unsafe=True)
    result = PILOT.campaign(factory, tmp_path, REL)
    assert result['passed'] is False
    assert result['stop_cause'] == 'initial_not_safe_or_wrong_release'
    assert result['records'] == [] and result['sessions'][0]['owned'] is False
    assert made == [] or len(made) == 1
    assert events == [('close',)]
    other = PILOT.campaign(lambda: PilotSession([], tmp_path / 'wrong', release='other'),
                           tmp_path / 'wrong', REL)
    assert other['stop_cause'] == 'initial_not_safe_or_wrong_release'
    assert other['records'] == []


def test_unsafe_egress_is_retained_and_stops_before_the_next_capture(tmp_path):
    events = []
    factory, _ = _factory(events, tmp_path, faults={'s00-off-00': dict(egress_safe=False)})
    result = PILOT.campaign(factory, tmp_path, REL)
    assert result['stop_cause'] == 'unsafe_or_incomplete_capture'
    assert [row['tag'] for row in result['records']] == ['s00-off-00']
    assert result['records'][0]['egress_safe'] is False
    assert events[-2:] == [('cleanup',), ('close',)]


@pytest.mark.parametrize('reason', ('CLIPPED', 'INVALID_IQ'))
def test_clipped_or_invalid_iq_stops_and_keeps_the_original_frame(tmp_path, reason):
    factory, _ = _factory([], tmp_path, faults={
        's00-normal-00': dict(environment=dict(label=reason, reason=reason))})
    result = PILOT.campaign(factory, tmp_path, REL)
    assert result['stop_cause'] == 'unsafe_or_incomplete_capture'
    assert [row['tag'] for row in result['records']] == ['s00-off-00', 's00-normal-00']
    assert result['records'][-1]['environment']['reason'] == reason
    assert (tmp_path / 's00-normal-00.telemetry.json').is_file()


def test_duplicate_capture_identity_stops(tmp_path):
    factory, _ = _factory([], tmp_path, faults={
        's00-normal-00': dict(capture_id='s00-off-00', iq_sha256='s00-off-00')})
    result = PILOT.campaign(factory, tmp_path, REL)
    assert result['stop_cause'] == 'unsafe_or_incomplete_capture'
    assert len(result['records']) == 2


@pytest.mark.parametrize('fault', (dict(environment=None), dict(telemetry={})))
def test_missing_evidence_stops(tmp_path, fault):
    factory, _ = _factory([], tmp_path, faults={'s00-normal-00': fault})
    result = PILOT.campaign(factory, tmp_path, REL)
    assert result['stop_cause'] == 'unsafe_or_incomplete_capture'
    assert len(result['records']) == 2


def test_a_frame_without_its_telemetry_file_cannot_pass(tmp_path):
    factory, _ = _factory([], tmp_path, faults={
        's00-off-00': dict(telemetry_file='never-written.telemetry.json')})
    result = PILOT.campaign(factory, tmp_path, REL)
    assert result['stop_cause'] is None and result['frames'] == 12
    assert result['telemetry_files_present'] is False
    assert result['passed'] is False


def test_capture_exception_stops_with_no_retry(tmp_path):
    events = []

    class Timeout(PilotSession):
        def capture(self, tag, physical):
            if tag == 's00-normal-00':
                events.append(('capture', tag, physical))
                raise TimeoutError('capture deadline')
            return super().capture(tag, physical)

    result = PILOT.campaign(lambda: Timeout(events, tmp_path), tmp_path, REL)
    assert result['passed'] is False and result['stop_cause'] == 'TimeoutError'
    assert result['sessions'][0]['error'] == 'capture deadline'
    assert [row['tag'] for row in result['records']] == ['s00-off-00']
    assert events[-2:] == [('cleanup',), ('close',)]
    assert [e for e in events if e[0] == 'capture'].count(
        ('capture', 's00-normal-00', True)) == 1


def test_close_unconfirmed_stops_after_the_session(tmp_path):
    class NotClosed(PilotSession):
        def cleanup(self):
            return dict(safe=True, disarm_ack=False)

    result = PILOT.campaign(lambda: NotClosed([], tmp_path), tmp_path, REL)
    assert result['stop_cause'] == 'close_unconfirmed'
    assert len(result['records']) == 4 and result['passed'] is False


def test_factory_error_stops_without_owning_anything(tmp_path):
    def fail():
        raise RuntimeError('engine missing')

    result = PILOT.campaign(fail, tmp_path, REL)
    assert result['passed'] is False and result['records'] == []
    assert result['sessions'][0]['owned'] is False


# ---------------------------------------------------------------- 4. budget gate

def test_a_declared_plan_that_exceeds_the_budget_is_refused_before_any_session(
        tmp_path, monkeypatch):
    for name, value in (('SEEDS', (1, 37, 73, 99)),
                        ('FRAME_PLAN', (('off', False),) * 5)):
        monkeypatch.setattr(PILOT, name, value)
        events = []
        factory, made = _factory(events, tmp_path / name)
        result = PILOT.campaign(factory, tmp_path / name, REL)
        assert result['passed'] is False and result['records'] == []
        assert result['stop_cause'] in ('budget_initialization_cap', 'budget_frame_cap')
        assert made == [] and events == []
        monkeypatch.undo()


def test_runtime_frame_cap_stops_a_plan_that_grows_mid_campaign(tmp_path, monkeypatch):
    plan = [('off', False), ('normal', True), ('normal', True), ('off', False)]
    monkeypatch.setattr(PILOT, 'FRAME_PLAN', plan)
    events = []

    class Growing(PilotSession):
        def capture(self, tag, physical):
            if len(plan) < 20:                     # bounded drift, so this test cannot hang
                plan.extend([('normal', True), ('normal', True)])
            return super().capture(tag, physical)

    result = PILOT.campaign(lambda: Growing(events, tmp_path), tmp_path, REL)
    assert result['stop_cause'] == 'budget_frame_cap'
    assert len(result['records']) == 12 and result['passed'] is False
    assert result['initializations'] == 1 and len(result['sessions']) == 1
    assert events.count(('cleanup',)) == 1 and events.count(('close',)) == 1


def test_runtime_initialization_cap_stops_a_seed_list_that_grows(tmp_path, monkeypatch):
    seeds = [1]
    monkeypatch.setattr(PILOT, 'SEEDS', seeds)
    monkeypatch.setattr(PILOT, 'APPROVED_SESSIONS', 10)
    monkeypatch.setattr(PILOT, 'APPROVED_FRAMES', 100)
    events = []

    class Growing(PilotSession):
        def cleanup(self):
            seeds.extend([37, 73, 99])
            return super().cleanup()

    result = PILOT.campaign(lambda: Growing(events, tmp_path), tmp_path, REL)
    assert result['stop_cause'] == 'budget_initialization_cap'
    assert [e for e in events if e[0] == 'setup'] == [('setup', 1), ('setup', 37), ('setup', 73)]
    assert len(result['records']) == 12


# --------------------------------------------------------- 5. authorization gate

def test_run_refuses_without_the_cable_attestation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(RUN, 'load_helper', lambda: calls.append('helper'))
    with pytest.raises(RUN.Refusal) as refusal:
        RUN.run(tmp_path / 'candidate', tmp_path / 'out', cable_confirmed=False)
    assert refusal.value.code == 'CABLE_NOT_CONFIRMED'
    assert 'cable-confirmed' in str(refusal.value)
    assert calls == [] and not (tmp_path / 'out').exists()


def test_run_refuses_an_existing_output_directory_before_touching_the_candidate(tmp_path):
    output = tmp_path / 'out'
    output.mkdir()
    with pytest.raises(RUN.Refusal) as refusal:
        RUN.run(tmp_path / 'candidate-that-does-not-exist', output, cable_confirmed=True)
    assert refusal.value.code == 'OUTPUT_EXISTS'


def test_run_refuses_a_release_that_is_not_the_approved_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(RUN.R, 'verify_release', lambda *a, **k: dict(
        release_id='someone-elses-release', software_contract='cnn-bpsk-environment-v1'))
    with pytest.raises(RUN.Refusal) as refusal:
        RUN.run(tmp_path / 'candidate', tmp_path / 'out', cable_confirmed=True)
    assert refusal.value.code == 'RELEASE_NOT_APPROVED'
    assert not (tmp_path / 'out').exists()


def test_run_refuses_a_release_with_the_wrong_software_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(RUN.R, 'verify_release', lambda *a, **k: dict(
        release_id=REL, software_contract='cnn-bpsk-teaching-v1'))
    with pytest.raises(RUN.Refusal) as refusal:
        RUN.run(tmp_path / 'candidate', tmp_path / 'out', cable_confirmed=True)
    assert refusal.value.code == 'SOFTWARE_CONTRACT_MISMATCH'


def test_run_declares_twelve_frames_and_the_environment_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(RUN.R, 'verify_release', lambda *a, **k: dict(
        release_id=REL, software_contract='cnn-bpsk-environment-v1',
        software=['x'], release_directory=str(tmp_path)))
    attempts = []

    class Helper:
        def connect(self):
            attempts.append('connect')
            raise RuntimeError('no board in this test')

    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    (candidate / 'rf_environment_baseline.json').write_bytes(b'{}')
    monkeypatch.setattr(RUN, 'load_helper', lambda: Helper())
    result = RUN.run(candidate, tmp_path / 'out', cable_confirmed=True)
    declaration = _load(tmp_path / 'out' / 'declaration.json')
    assert declaration['expected_release'] == REL
    assert declaration['sessions'] == 3 and declaration['expected_frames'] == 12
    assert declaration['kinds'] == ['off', 'normal', 'normal', 'off']
    assert declaration['reference_seeds'] == [1, 37, 73]
    assert declaration['attenuation_mdB'] == MDB
    assert declaration['initializations_max'] == 3
    assert 'no HEADROOM' in declaration['authorization']
    assert 'rf-control.token' in declaration['authorization']
    assert set(declaration['source_hashes']) == set(RUN.SOURCES)
    assert declaration['source_hashes']['rf_environment_telemetry_session.py'] == hashlib.sha256(
        (ROOT / 'scripts' / 'rf_environment_telemetry_session.py').read_bytes()).hexdigest()
    assert result['passed'] is False and result['board_state'] == 'UNKNOWN'
    assert result['error_type'] == 'RuntimeError'
    assert result['frames'] == 0 and result['telemetry_files_present'] is False
    assert attempts == ['connect']                      # exactly one attempt, never a retry
    assert (tmp_path / 'out' / 'receipt.json').is_file()
    assert 'no retry' in result['policy']


def test_board_entry_refuses_without_the_cable_attestation(tmp_path, capsys):
    with pytest.raises(SystemExit) as refusal:
        T.main(['--expected-release', REL, '--output', str(tmp_path / 'out')])
    assert 'REFUSE' in str(refusal.value) and '--cable-confirmed' in str(refusal.value)
    assert not (tmp_path / 'out').exists()


def test_board_entry_refuses_output_that_already_exists(tmp_path):
    output = tmp_path / 'out'
    output.mkdir()
    with pytest.raises(SystemExit) as refusal:
        T.main(['--expected-release', REL, '--output', str(output), '--cable-confirmed'])
    assert 'REFUSE' in str(refusal.value) and 'must not exist' in str(refusal.value)


def test_board_entry_refuses_another_release(tmp_path):
    with pytest.raises(SystemExit) as refusal:
        T.main(['--expected-release', 'other-release', '--output', str(tmp_path / 'out'),
                '--cable-confirmed'])
    assert 'REFUSE' in str(refusal.value) and T.APPROVED_RELEASE in str(refusal.value)


# ------------------------------------------------- 6. the frozen pilot entry points

def test_frozen_entries_are_byte_identical_to_the_readiness_inventory():
    for name, expected in FROZEN_ENTRY_SHA256.items():
        path = ROOT / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected, name


def test_the_18_frame_pilot_still_has_its_original_shape(tmp_path):
    assert FROZEN.SEEDS == (1, 37, 73)
    assert FROZEN.KINDS == ('normal', 'narrowband', 'pulse', 'compound', 'off', 'domain')
    assert FROZEN.EXPECTED == {'normal': 'CLEAR', 'narrowband': 'NARROWBAND', 'pulse': 'PULSE'}
    events = []

    class Session:
        def initial(self):
            return dict(safe=True, release_id=REL)

        def setup(self, seed):
            events.append(('setup', seed))

        def scene(self, kind):
            events.append(('scene', kind))

        def domain(self, outside):
            events.append(('domain', outside))

        def capture(self, tag, physical):
            events.append(('capture', physical))
            kind = tag.split('-')[1]
            label = {'normal': 'CLEAR', 'narrowband': 'NARROWBAND', 'pulse': 'PULSE',
                     'compound': 'UNKNOWN', 'off': 'UNKNOWN', 'domain': 'INVALID'}[kind]
            return dict(release_id=REL, capture_id=tag, iq_sha256=tag, egress_safe=True,
                        environment=dict(label=label, reason=''))

        def cleanup(self):
            events.append(('cleanup',))
            return dict(safe=True, disarm_ack=True)

        def close(self):
            events.append(('close',))

    result = FROZEN.campaign(lambda: Session(), tmp_path, REL)
    assert result['passed'] is True and len(result['records']) == 18
    assert events.count(('cleanup',)) == 3
    assert events.count(('capture', False)) == 3

