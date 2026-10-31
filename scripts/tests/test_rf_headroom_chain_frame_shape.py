"""The pinned research-chain fixture must speak the shape the daemon writes.

`qualify-runtime.py` freezes `python/headroom-fake-chain.py` as the chain
runner: the same bytes are executed on the host here and, after upload, on the
ARM side, and `check_chain` demands `records['full-five']['receipt']['completed']
is True` from both.  So the fixture's frames have to BE the frames the native
daemon writes -- if they are not, the whole chain fails on both platforms for a
reason that has nothing to do with the release under test.

The daemon writes the four appended evidence keys INSIDE `headroom`
(`rf_daemon.c:2130-2142`: the second `snprintf` starts at `n-2`, on top of the
template's closing braces).  A real 2026-10-15 research frame corroborates the
layer: its frame object carries exactly bytes/direction/format/headroom/id/ratio/
sample_rate_hz/samples/timestamp_ns/type -- ten keys, none of the four -- with all
four inside `headroom`.

The pinned fixture predated that fix and still wrote them one level up, which
`rf_headroom.check_research_frame` refuses by name.  These cases pin the fixture
to the daemon's layers: the correct shape is accepted, and the wrong layer, a
missing key and an illegal value are each refused with the message that names the
fault.  No socket is ever created here.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
SW = REPO / 'pynq_overlay' / 'sw'
# The stage whose pin file and review binding freeze this fixture.
SEALED = REPO / 'evidence/rf-teaching/s2/executable-candidate-2026-10-16'
FIXTURE = SEALED / 'python' / 'headroom-fake-chain.py'
# The candidate READER under test is the pin-migrated one: the fixture's bytes stay
# frozen by the stage that sealed them, but the predicate that consumes them is the
# new release (2026-10-17 src_cfg wrap-bit repair).
CANDIDATE = (REPO / 'evidence/rf-teaching/s2-headroom-candidate2-2026-10-17'
             / 'candidate' / 'releases' / 'e66197ed79131b73aa4f47804487c053')
# A real board frame (2026-10-15 research attempt, attempt 1) is the ground truth
# for the layer: derived nowhere, typed out by the daemon on hardware.
REAL_FRAME = (REPO / 'evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15'
              / 'board-download/session/attempt-1/header.json')

SESSION = '0123456789abcdef0123456789abcdef'
# The ten keys the daemon's frame template closes with; the four appended keys
# are not among them.
OUTER_KEYS = frozenset(('type', 'id', 'direction', 'format', 'samples', 'bytes',
                        'sample_rate_hz', 'ratio', 'timestamp_ns', 'headroom'))


def _load_by_path(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SW))
H = _load_by_path(CANDIDATE / 'rf_headroom.py', 'candidate_rf_headroom')
CHAIN = _load_by_path(FIXTURE, 'pinned_headroom_fake_chain')

# The deterministic OFF payload the frozen chain generates for 'off-before'.
def _quiet():
    import numpy as np
    rng = np.random.RandomState(3)
    return np.column_stack((np.round(2 * rng.normal(size=4096)),
                            np.round(2 * rng.normal(size=4096)))).astype('<i2').tobytes()


QUIET = _quiet()


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    """Zero board contact, enforced: any socket in these tests fails the test."""
    def forbidden(*args, **kwargs):
        raise AssertionError('this test tried to open a socket')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def _capture(attempt=1, case='full-five', payload=QUIET):
    """The raw bytes the pinned fixture emits for one capture, and its header."""
    plan = H.HeadroomPlan()
    peer = CHAIN.Peer(H, dict(quiet=payload, normal=payload,
                              narrowband=payload, pulse=payload), case)
    peer.reply(dict(id=1, op='control.arm', args={'token': '<SET_LOCALLY>'}), b'')
    peer.reply(dict(id=2, op='headroom.begin', args=plan.begin_args(SESSION, True)), b'')
    raw = b''
    for number in range(1, attempt + 1):
        request_id = 3 + number
        raw = peer.reply(dict(id=request_id, op='headroom.capture',
                              args=plan.capture_args(number, SESSION)), b'')
        if number < attempt:
            # The frozen chain holds one pending capture, so the earlier attempt is
            # accepted before the next one is requested -- the daemon's own order.
            peer.reply(dict(id=request_id, op='headroom.accept',
                            args=plan.accept_args(number, SESSION, request_id,
                                                 1000 + request_id)), b'')
    line, _, bytes_payload = raw.partition(b'\n')
    return json.loads(line.decode('utf-8')), bytes_payload, 3 + attempt


def _frame(header, payload, request_id):
    return H.ResearchFrame(request_id=request_id, header=header, payload=payload)


def test_the_pinned_chain_fixture_has_not_drifted_from_its_frozen_digest():
    """The digest this stage's pin file and review binding must carry."""
    assert FIXTURE.is_file() and not FIXTURE.is_symlink()
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == (
        '6573f25991b89b8b77fceaa46e9abce3f33247b863e678f6908901dfe246f6bb')
    assert list(H.APPENDED_EVIDENCE) == ['native_error', 'closure',
                                        'timing_scope', 'timing_order']


def test_the_pinned_chain_fixture_writes_the_daemons_frame_layers():
    header, _, _ = _capture()
    on_the_frame_object = sorted(set(H.APPENDED_EVIDENCE) & set(header))
    assert not on_the_frame_object, (
        'the pinned fixture still writes %s on the frame object; the daemon '
        'writes them inside headroom' % on_the_frame_object)
    assert set(header) == set(OUTER_KEYS)


def test_the_daemons_layer_is_the_one_a_real_board_frame_carries():
    """The witness, checked on its own before it is used as a reference."""
    assert REAL_FRAME.is_file()
    real = json.loads(REAL_FRAME.read_text(encoding='utf-8'))['header']
    assert set(real) == set(OUTER_KEYS)
    for key in H.APPENDED_EVIDENCE:
        assert key in real['headroom'] and key not in real, key
    header, _, _ = _capture()
    assert set(header) == set(real)
    # Neither layer invents a field and neither drops one: the fixture's headroom
    # carries exactly the real frame's nineteen keys, the four included.
    assert set(header['headroom']) == set(real['headroom'])
    assert len(header['headroom']) == 19


@pytest.mark.parametrize('attempt,case', [(1, 'full-five'), (2, 'rails')])
def test_a_pinned_fixture_frame_is_accepted_by_the_candidate_reader(attempt, case):
    """The positive case: both the AWAIT_ACCEPT and the CLIPPED/closure branch."""
    header, payload, request_id = _capture(attempt, case)
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, attempt,
                                      _frame(header, payload, request_id),
                                      require_hardware=False)
    assert evidence['simulated'] is True
    assert evidence['attempt'] == attempt
    if case == 'rails':
        assert evidence['terminal'] == 'CLIPPED'
        assert evidence['closure'] == {'attempted': True, 'confirmed': True,
                                       'release_code': 0, 'shutdown_code': 0,
                                       'tx_active': False, 'rx_busy': False}
    else:
        assert evidence['terminal'] == 'AWAIT_ACCEPT'
        assert evidence['native_error']['stage'] == 'NONE'
        assert evidence['closure'] == {'attempted': False, 'confirmed': False}


@pytest.mark.parametrize('key', ['native_error', 'closure', 'timing_scope',
                                 'timing_order'])
def test_a_fixture_frame_carrying_a_key_outside_headroom_is_refused_by_name(key):
    """The old layer, refused by name rather than preferred or ignored."""
    header, payload, request_id = _capture()
    header = copy.deepcopy(header)
    header[key] = header['headroom'][key]
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 1,
                               _frame(header, payload, request_id),
                               require_hardware=False)
    assert key + ' outside headroom' in str(raised.value)


@pytest.mark.parametrize('key,fragment', [
    ('timing_scope', 'the timing scope is not the reviewed one'),
    ('timing_order', 'the timing order is not the reviewed one'),
    ('native_error', 'invalid research frame field: native_error'),
    ('closure', 'invalid research frame field: closure'),
])
def test_a_fixture_frame_missing_a_key_inside_headroom_is_refused_by_name(key, fragment):
    header, payload, request_id = _capture()
    header = copy.deepcopy(header)
    del header['headroom'][key]
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 1,
                               _frame(header, payload, request_id),
                               require_hardware=False)
    assert fragment in str(raised.value)


@pytest.mark.parametrize('key,value,fragment', [
    ('timing_scope', 'API_MONOTONIC', 'the timing scope is not the reviewed one'),
    ('timing_scope', 5, 'the timing scope is not the reviewed one'),
    ('timing_order', list(reversed(H.TIMING_ORDER)),
     'the timing order is not the reviewed one'),
    ('timing_order', list(H.TIMING_ORDER)[:-1],
     'the timing order is not the reviewed one'),
    ('native_error', [], 'invalid research frame field: native_error'),
    ('closure', [], 'invalid research frame field: closure'),
    ('closure', {'attempted': 1, 'confirmed': False},
     'invalid native closure flags'),
])
def test_an_illegal_value_inside_headroom_is_refused_by_name(key, value, fragment):
    header, payload, request_id = _capture()
    header = copy.deepcopy(header)
    header['headroom'][key] = value
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 1,
                               _frame(header, payload, request_id),
                               require_hardware=False)
    assert fragment in str(raised.value)


def test_an_unknown_native_error_stage_inside_headroom_is_refused_by_name():
    header, payload, request_id = _capture()
    header = copy.deepcopy(header)
    header['headroom']['native_error']['stage'] = 'AFTER_THE_FACT'
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 1,
                               _frame(header, payload, request_id),
                               require_hardware=False)
    assert 'invalid native error stage' in str(raised.value)
