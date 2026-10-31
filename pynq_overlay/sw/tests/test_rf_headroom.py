"""HEADROOM-1: the 46 dB research plan, controller and evidence, offline only.

The 40 dB teaching contract is checked here too, from the other side: this module
must never be able to express it, and the environment classifier must keep
refusing 46 dB as a different RF domain.  Nothing in this file arms, captures,
or reaches a board.
"""
import dataclasses
import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rf_headroom as H
from rf_status_client import StatusProtocolError

# The three reviewed digests the native contract embeds.  Repeating them here is
# deliberate: the point of the test is that the Python plan reproduces the bytes
# the C refuses to accept anything else for, not that it reproduces itself.
NATIVE_PAYLOAD_SHA = {
    'normal': '38b3f36ae20b6fe232d68d677b9e5e00c0b246c3617d0ff27239e89d0c3ef7f6',
    'narrowband': '4693355b00a98acc26851df3c86e38da49672fb7713983b29edd061627845f73',
    'pulse': '22463cf461cd2141fbd3641d28294a740a39aa985c7943bea5aeb2bfcb13bbee',
}
NATIVE_DECLARATION_SHA = {
    'normal': '788e61dc71ab5d6ffbe28f67f7797457539819920c955daff2ad96ca2d200a6b',
    'narrowband': 'b02d0cdd464109d9df9a3dc13eb38b0d9b0b8075b88819c96a52a48c061b780a',
    'pulse': 'e65d79dc4aa4912fdd84f5f41602590774d25ab1b96616f65f83ddea01d9c53b',
}
SESSION = '0123456789abcdef0123456789abcdef'


def rf_json(verified=True):
    """The native `actual_rf` object, as rf_daemon's hr_rb_json emits it."""
    return dict(rx_lo_hz=80000000, tx_lo_hz=80000000, rx_sample_rate_hz=60000000,
                tx_sample_rate_hz=60000000, rx_bandwidth_hz=30000000,
                tx_bandwidth_hz=30000000, attenuation_mdB=46000,
                tx1_attenuation_mdB=46000, tx2_attenuation_mdB=46000,
                hardware_verified=verified, phy_mode_1r1t=0, tx_fir_enabled=1,
                rx_fir_enabled=0, tx_fir_config_verified=True,
                rx_fir_config_verified=True,
                pl=dict(phy_mode=0, idelay_en=127, idelay_tap=29, src_cfg=0,
                        cap_cfg=0, cap_ratio=0, cap_len=4096))


def _roundtrip(value):
    """Cross the wire the way the client does, so nothing is shared by reference."""
    return json.loads(json.dumps(value, allow_nan=False))


# The daemon writes one research frame in TWO steps (rf_daemon.c:2130-2142):
# the JSON template, whose serialisation ends with `...timing_ns":[...]}}`
# (the first `}` closes `headroom`, the second closes the frame), and then the
# evidence append, written with `snprintf(d->frame+n-2, ...)` -- on top of
# those two braces -- ending with its own `]}}`.  The four appended keys are
# therefore INSIDE `headroom`.  A fixture that types them somewhere else is a
# frame no daemon ever emits: that is exactly how the 2026-10-16 defect #2
# survived a review round, so the fixture now composes the frame the same way
# instead of asserting a shape by hand.
APPENDED_KEYS = ('native_error', 'closure', 'timing_scope', 'timing_order')


def daemon_frame(header, appended):
    """Compose the whole frame the way the daemon's two writes do.

    `header` is the template object written first; `appended` is the evidence
    block the second write adds at offset `n-2`.  The result is parsed back, so
    the fixture carries the nesting the wire carries rather than the nesting
    someone typed.
    """
    text = json.dumps(header, allow_nan=False)
    assert text.endswith('}}'), text[-8:]
    fragment = (',"native_error":%s,"closure":%s,"timing_scope":%s,'
                '"timing_order":%s}}' % (
                    json.dumps(appended['native_error'], allow_nan=False),
                    json.dumps(appended['closure'], allow_nan=False),
                    json.dumps(appended['timing_scope'], allow_nan=False),
                    json.dumps(appended['timing_order'], allow_nan=False)))
    return json.loads(text[:-2] + fragment)


_UNSET = object()


class FakeClient:
    """The narrow client surface the controller is allowed to depend on."""

    def __init__(self, plan=None, capability=_UNSET, fail_at=None, rails_at=None,
                 corrupt=None, verified=True):
        self.plan = plan or H.HeadroomPlan()
        # An explicit None must stay None: "the daemon announced nothing" is a
        # case this client has to be able to represent.
        from test_rf_headroom_corrections import eligible
        self.capability = (eligible()
                           if capability is _UNSET else capability)
        self.fail_at = fail_at
        self.rails_at = rails_at
        self.corrupt = corrupt
        self.verified = verified
        self.calls = []
        self.armed = False
        self.disarms = 0
        self._id = 0
        self.session = SESSION

    # -- capability ---------------------------------------------------------
    def headroom_capability(self):
        return self.capability

    @contextmanager
    def headroom_lease(self):
        yield

    def headroom_arm(self,token):
        assert token
        self.armed=True
        return {'armed':True}

    def headroom_closing_readback(self):
        armed=self.control_armed()
        return dict(confirmed=armed is False,armed=armed,
            state=dict(state='OFFLINE' if armed is False else 'READY',
                       pending=False,lease_held=True),
            pl=dict(io_ok=True,tx=dict(source_selected=False),rx=dict(armed=False)))

    # -- the five research operations ---------------------------------------
    def _next(self, op, args):
        self._id += 1
        self.calls.append((op, self._id, dict(args)))
        return self._id

    def headroom_begin(self, args):
        self._next('headroom.begin', args)
        if self.fail_at == 'headroom.begin':
            raise StatusProtocolError('RF daemon refused headroom.begin',
                                      'RF_INVALID_STATE')
        return dict(study=H.STUDY, session_id=args['session_id'],
                    initializations=1, attempt_budget=H.ATTEMPT_BUDGET,
                    actual_rf=rf_json(self.verified))

    def headroom_load(self, args, payload):
        self._next('headroom.load', args)
        if self.fail_at == 'headroom.load:%d' % args['attempt']:
            raise StatusProtocolError('RF daemon refused headroom.load',
                                      'RF_INVALID_ARGUMENT')
        assert payload == self.plan.payload(args['attempt'])
        return dict(loaded=True, samples=H.LOAD_SAMPLES)

    def headroom_capture(self, args):
        request_id = self._next('headroom.capture', args)
        attempt = args['attempt']
        if self.fail_at == 'headroom.capture:%d' % attempt:
            raise StatusProtocolError('RF daemon refused headroom.capture',
                                      'RF_INVALID_STATE')
        return self._frame(request_id, attempt)

    def headroom_accept(self, args):
        self._next('headroom.accept', args)
        if self.fail_at == 'headroom.accept:%d' % args['attempt']:
            raise StatusProtocolError('RF daemon refused headroom.accept',
                                      'RF_INVALID_STATE')
        if args['attempt'] == H.ATTEMPT_BUDGET:
            self.armed = False
            return dict(completed=True, attempts=H.ATTEMPT_BUDGET, armed=False)
        return dict(accepted=True)

    def headroom_abort(self, args):
        self._next('headroom.abort', args)
        self.armed = False
        return dict(aborted=True, armed=False)

    def control_disarm(self):
        self.disarms += 1
        self.armed = False
        return dict(armed=False)

    def control_armed(self):
        # The daemon's own live answer; the closing evidence is this, not the
        # fact that control.disarm was answered.
        return self.armed

    # -- frame construction --------------------------------------------------
    def _frame(self, request_id, attempt):
        plan = self.plan
        transmitting = plan.is_transmitting(attempt)
        railed = attempt == self.rails_at
        payload = ((b'\x00\xf8' * 2) * H.CAPTURE_SAMPLES if railed
                   else (0x0040).to_bytes(4, 'little', signed=True) * H.CAPTURE_SAMPLES)
        rails = 2 * H.CAPTURE_SAMPLES if railed else 0
        head = dict(study=H.STUDY, session_id=self.session, attempt=attempt,
                    scenario=plan.scenario(attempt),
                    capture_request_id=request_id,
                    source_sha256=plan.payload_digest(attempt),
                    declaration_sha256=plan.declaration_digest(attempt),
                    iq_sha256=hashlib.sha256(payload).hexdigest(),
                    source_enabled=transmitting,
                    terminal=('CLIPPED' if rails else 'AWAIT_ACCEPT'),
                    rail_count=rails, settle_wait_us=0,
                    actual_rf_before=rf_json(self.verified),
                    actual_rf_after=rf_json(self.verified),
                    timing_ns=[10 * (i + 1) for i in range(8)])
        header = dict(type='research_iq_frame', id=request_id, direction='rx',
                      format='s16_iq_le', samples=H.CAPTURE_SAMPLES,
                      bytes=H.IQ_BYTES, sample_rate_hz=60000000, ratio=0,
                      timestamp_ns=100+request_id, headroom=head)
        appended = dict(
            native_error=dict(stage='NONE', code=0, capture_code=0,
                              release_code=0, post_readback_code=0,
                              clock_code=0, close_code=0),
            closure=(dict(attempted=True, confirmed=True, release_code=0,
                          shutdown_code=0, tx_active=False, rx_busy=False)
                     if railed else dict(attempted=False, confirmed=False)),
            timing_scope=H.TIMING_SCOPE,
            timing_order=list(H.TIMING_ORDER))
        # Both writes, the same offset: the four keys land inside `headroom`.
        header = daemon_frame(header, appended)
        if self.corrupt is not None:
            self.corrupt(header, attempt)
        return H.ResearchFrame(request_id, _roundtrip(header), payload)


class FakeRecorder:
    def __init__(self):
        self.events = []
        self.raw = {}
        self.replays = {}

    def intent(self, attempt, scenario, args):
        self.events.append(('intent', attempt, scenario))

    def frame(self, attempt, frame):
        # `None` means the original bytes were never received; it is recorded as
        # such rather than skipped, exactly as the production recorder does.
        self.events.append(('frame', attempt))
        self.raw[attempt] = None if frame is None else frame.payload

    def analysis(self, attempt, summary):
        self.events.append(('analysis', attempt))

    def replay(self, attempt, summary):
        self.events.append(('replay', attempt))
        self.replays[attempt] = summary

    def decision(self, attempt, accepted, detail):
        self.events.append(('decision', attempt, accepted))

    def terminal(self, outcome, detail):
        self.events.append(('terminal', outcome))


class OffSync:
    """A stand-in for the worker's OFF check that reports a violation."""

    def __call__(self, attempt, evidence):
        if evidence['scenario'] in ('off-before', 'off-after'):
            raise H.HeadroomStopped('OFF capture synchronised')


def _controller(*args, **kwargs):
    kwargs.setdefault('require_hardware', False)
    return H.HeadroomController(*args, **kwargs)


# --------------------------------------------------------------------- plan

def test_the_python_plan_reproduces_the_digests_the_native_contract_embeds():
    plan = H.HeadroomPlan()
    for attempt in (2, 3, 4):
        scenario = plan.scenario(attempt)
        assert plan.payload_digest(attempt) == NATIVE_PAYLOAD_SHA[scenario]
        assert plan.declaration_digest(attempt) == NATIVE_DECLARATION_SHA[scenario]


def test_the_plan_is_the_fixed_five_attempt_order():
    plan = H.HeadroomPlan()
    assert plan.attempts == 5
    assert [plan.scenario(a) for a in range(1, 6)] == [
        'off-before', 'normal', 'narrowband', 'pulse', 'off-after']
    assert [plan.is_transmitting(a) for a in range(1, 6)] == [
        False, True, True, True, False]


def test_only_the_normal_narrowband_and_pulse_attempts_carry_a_payload():
    plan = H.HeadroomPlan()
    for attempt in (1, 5):
        with pytest.raises(ValueError):
            plan.payload(attempt)
        with pytest.raises(ValueError):
            plan.declaration(attempt)
        assert plan.payload_digest(attempt) == ''
        assert plan.declaration_digest(attempt) == ''


def test_begin_arguments_are_the_exact_fixed_set_at_46db():
    args = H.HeadroomPlan().begin_args(SESSION,cable_confirmed=True)
    assert args == {'study': H.STUDY, 'session_id': SESSION,
                    'cable_confirmed': True, 'lo_hz': 80000000,
                    'sample_rate_hz': 60000000, 'rx_bandwidth_hz': 30000000,
                    'tx_bandwidth_hz': 30000000,
                    'attenuation_mdB': H.RESEARCH_ATTENUATION_MDB,
                    'phy_mode_1r1t': 0, 'idelay_en': 1, 'idelay_tap': 29}
    assert args['attenuation_mdB'] == 46000
    # Since the 2026-10-17 migration teaching and research share the one domain:
    # the old "different attenuation" negative is now an equality, so a second
    # value appearing anywhere still fails here.
    assert args['attenuation_mdB'] == H.TEACHING_ATTENUATION_MDB == 46000


def test_the_plan_cannot_be_asked_for_the_teaching_40000_domain():
    plan = H.HeadroomPlan()
    with pytest.raises(TypeError):
        H.HeadroomPlan(attenuation_mdB=40000)
    assert 'attenuation_mdB' not in {f.name for f in dataclasses.fields(plan)}
    assert plan.begin_args(SESSION,cable_confirmed=True)['attenuation_mdB'] == 46000


def test_begin_arguments_refuse_a_session_id_the_native_parser_would_reject():
    plan = H.HeadroomPlan()
    for bad in ('', 'ABCDEF0123456789ABCDEF0123456789', '0123456789abcdef',
                '0123456789abcdef0123456789abcde', None, 1):
        with pytest.raises(ValueError):
            plan.begin_args(bad)


def test_load_arguments_are_flat_typed_fields_and_carry_generator_digests():
    plan = H.HeadroomPlan()
    for attempt in (2, 3, 4):
        args = plan.load_args(attempt, SESSION)
        scenario = plan.scenario(attempt)
        assert args['study'] == H.STUDY and args['session_id'] == SESSION
        assert args['attempt'] == attempt and args['scenario'] == scenario
        assert args['samples'] == H.LOAD_SAMPLES and args['bytes'] == H.LOAD_BYTES
        assert args['format'] == 's16_iq_le' and args['direction'] == 'tx'
        assert args['wrap'] is True
        # Every declared field is a bounded scalar the flat parser can read back.
        for key, value in args.items():
            assert type(value) in (str, int, bool), key
        assert args['source_sha256'] == NATIVE_PAYLOAD_SHA[scenario]
        assert args['decl_sha256'] == NATIVE_DECLARATION_SHA[scenario]
        assert args['pa'] == (40 if attempt == 4 else 0)
        assert args['ta'] == (40 if attempt == 3 else 0)


def test_load_arguments_are_reconstructed_not_a_digest_alone():
    """A digest is not declaration validation: the typed fields must be there."""
    plan = H.HeadroomPlan()
    args = plan.load_args(2, SESSION)
    declaration = plan.declaration(2)
    assert args['truth'] == 'normal' and args['shape'] == 'RECT'
    assert args['formula'] == 'x=A*s+j'
    assert args['quant'] == 'numpy.rint / signed12 / no global scaling'
    assert args['v'] == declaration['version'] == 'rf-controlled-scenarios-v1'
    assert args['a'] == declaration['amplitude'] == 60
    assert args['rs'] == declaration['symbol_rate_hz'] == 3000000
    assert args['prbs'] == declaration['prbs7_seed'] == 1


def test_the_plan_refuses_a_generator_that_no_longer_matches_the_reviewed_hashes(monkeypatch):
    """The embedded identities are a guard, not decoration.

    The generator currently reproduces them, so a plan that merely forwarded
    whatever it generated would pass every other test in this file.  Moving the
    reviewed constant is what a real generator drift would look like from the
    plan's side, and the load must refuse rather than send a different payload.
    """
    plan = H.HeadroomPlan()
    for name in ('NATIVE_PAYLOAD_SHA', 'NATIVE_DECLARATION_SHA'):
        wrong = dict(getattr(H, name))
        wrong[2] = '0' * 64
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(H, name, wrong)
            with pytest.raises(ValueError):
                plan.load_args(2, SESSION)
    assert plan.load_args(2, SESSION)['source_sha256'] == NATIVE_PAYLOAD_SHA['normal']


def test_capture_and_accept_arguments_are_the_fixed_transaction():
    plan = H.HeadroomPlan()
    for attempt in range(1, 6):
        capture = plan.capture_args(attempt, SESSION)
        assert capture == {'study': H.STUDY, 'session_id': SESSION,
                           'attempt': attempt,
                           'scenario': plan.scenario(attempt),
                           'samples': H.CAPTURE_SAMPLES, 'ratio': 0,
                           'settle_wait_us': 0}
    accept = plan.accept_args(2, SESSION, 7, 100)
    assert accept == {'study': H.STUDY, 'session_id': SESSION, 'attempt': 2,
                      'frame_id': 7, 'timestamp_ns': '100', 'accepted': True}
    # The native parser compares this field as a string, not a number.
    assert type(accept['timestamp_ns']) is str


# ---------------------------------------------------------------- evidence

def _frame(attempt=2, **kwargs):
    return FakeClient(**kwargs)._frame(41, attempt)


def test_a_native_frame_is_read_back_as_evidence_not_as_the_sent_arguments():
    frame = _frame()
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)
    assert evidence['attempt'] == 2 and evidence['scenario'] == 'normal'
    assert evidence['request_id'] == frame.request_id
    assert evidence['source_enabled'] is True
    assert evidence['rail_count'] == 0
    assert evidence['source_sha256'] == NATIVE_PAYLOAD_SHA['normal']
    assert evidence['payload'] == frame.payload
    assert evidence['actual_rf_before']['attenuation_mdB'] == 46000
    assert evidence['hardware_verified'] is True


# Every entry is a real corruption of the frame the daemon returned.  The
# 2026-10-16 commit-epoch tests below reuse this list to show that allowing bit
# 2 of the two sample-path config words does not excuse any of these.
_EVIDENCE_MUTATIONS = [
    lambda h, a: h['headroom'].update(session_id='f' * 32),
    lambda h, a: h['headroom'].update(attempt=3),
    lambda h, a: h['headroom'].update(scenario='pulse'),
    lambda h, a: h['headroom'].update(source_enabled=False),
    lambda h, a: h['headroom'].update(capture_request_id=99),
    lambda h, a: h['headroom'].update(iq_sha256='0' * 64),
    lambda h, a: h['headroom'].update(settle_wait_us=1000),
    lambda h, a: h['headroom'].update(rail_count=5),
    lambda h, a: h['headroom']['actual_rf_after'].update(attenuation_mdB=40000),
    lambda h, a: h['headroom']['actual_rf_after'].update(tx2_attenuation_mdB=45999),
    lambda h, a: h['headroom']['actual_rf_after'].update(hardware_verified=1),
    lambda h, a: h['headroom']['actual_rf_after']['pl'].update(idelay_tap=28),
    lambda h, a: h['headroom']['actual_rf_after']['pl'].update(src_cfg=0x10),
    lambda h, a: h['headroom'].update(timing_ns=[10, 20, 5, 40, 50, 60, 70, 80]),
    lambda h, a: h.update(id=99),
    lambda h, a: h.update(ratio=1),
    lambda h, a: h.update(sample_rate_hz=30000000),
    lambda h, a: h.update(type='iq_frame'),
    # The frame is stamped after its own teardown, so a stamp that precedes the
    # last bracket is a frame that cannot have come from this transaction.
    lambda h, a: h.update(timestamp_ns=5),
]


@pytest.mark.parametrize('mutate', _EVIDENCE_MUTATIONS)
def test_corrupted_same_frame_metadata_is_refused(mutate):
    frame = _frame(corrupt=mutate)
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)


# ------------------------------------ the appended evidence lives in `headroom`
#
# The daemon appends native_error/closure/timing_scope/timing_order with a second
# snprintf that starts at `n-2` (rf_daemon.c:2130-2142), so all four are written
# INSIDE `headroom`.  The 2026-10-16 defect #2 was the predicate reading them one
# level up, which refused every real frame at "the timing scope is not the
# reviewed one".  The fixture now composes that shape (see `daemon_frame`), and
# these cases pin it: the wrong layer is refused by name, and so is every value
# or type the frozen contract does not allow.


def _moved_out(key):
    """The one wrong-layer shape: the key present, but on the frame object."""
    def mutate(header, attempt):
        header[key] = header['headroom'].pop(key)
    return mutate


def _dropped(key):
    def mutate(header, attempt):
        del header['headroom'][key]
    return mutate


def _replaced(key, value):
    def mutate(header, attempt):
        header['headroom'][key] = value
    return mutate


def _updated(key, **values):
    def mutate(header, attempt):
        header['headroom'][key].update(values)
    return mutate


def test_the_appended_evidence_is_read_from_headroom():
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, 2, _frame())
    assert evidence['terminal'] == 'AWAIT_ACCEPT'
    assert evidence['native_error']['stage'] == 'NONE'
    assert evidence['closure'] == {'attempted': False, 'confirmed': False}


def test_the_fixture_carries_the_appended_evidence_inside_headroom():
    header = _frame().header
    assert set(APPENDED_KEYS) <= set(header['headroom'])
    assert not set(APPENDED_KEYS) & set(header)


@pytest.mark.parametrize('key', APPENDED_KEYS)
def test_appended_evidence_outside_headroom_is_refused_by_name(key):
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                               _frame(corrupt=_moved_out(key)))
    assert key + ' outside headroom' in str(raised.value)


@pytest.mark.parametrize('key,fragment', [
    ('timing_scope', 'the timing scope is not the reviewed one'),
    ('timing_order', 'the timing order is not the reviewed one'),
    ('native_error', 'invalid research frame field: native_error'),
    ('closure', 'invalid research frame field: closure'),
])
def test_missing_appended_evidence_is_refused_by_name(key, fragment):
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                               _frame(corrupt=_dropped(key)))
    assert fragment in str(raised.value)


@pytest.mark.parametrize('key,value,fragment', [
    ('timing_scope', 'API_MONOTONIC', 'the timing scope is not the reviewed one'),
    ('timing_scope', 5, 'the timing scope is not the reviewed one'),
    ('timing_scope', None, 'the timing scope is not the reviewed one'),
    ('timing_order', list(reversed(H.TIMING_ORDER)),
     'the timing order is not the reviewed one'),
    ('timing_order', list(H.TIMING_ORDER)[:-1],
     'the timing order is not the reviewed one'),
    ('timing_order', 'source_begin', 'the timing order is not the reviewed one'),
    ('timing_order', 8, 'the timing order is not the reviewed one'),
    ('native_error', [], 'invalid research frame field: native_error'),
    ('closure', [], 'invalid research frame field: closure'),
    ('closure', {'attempted': 1, 'confirmed': False},
     'invalid native closure flags'),
    ('closure', {'attempted': False}, 'invalid native closure flags'),
])
def test_illegal_appended_evidence_types_and_values_are_refused_by_name(
        key, value, fragment):
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                               _frame(corrupt=_replaced(key, value)))
    assert fragment in str(raised.value)


def test_an_unknown_native_error_stage_is_refused_by_name():
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, _frame(
            corrupt=_updated('native_error', stage='AFTER_THE_FACT')))
    assert 'invalid native error stage' in str(raised.value)


@pytest.mark.parametrize('key,value', [
    ('code', -6), ('capture_code', 1), ('release_code', None),
    ('post_readback_code', 0.0), ('clock_code', True), ('close_code', -6)])
def test_every_native_error_code_is_still_a_bounded_integer(key, value):
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(
            H.HeadroomPlan(), SESSION, 2,
            _frame(corrupt=_updated('native_error', **{key: value})))
    assert 'native_error.' + key in str(raised.value)


def test_await_accept_evidence_must_not_contradict_itself():
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, _frame(
            corrupt=_updated('native_error', stage='CLOCK', clock_code=-3)))
    assert 'AWAIT_ACCEPT contradicts native failure or closure' in str(raised.value)


def test_terminal_closure_is_read_from_headroom_and_checked_there():
    assert _frame(rails_at=2).header['headroom']['closure']['attempted'] is True
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, _frame(
            rails_at=2, corrupt=_updated('closure', attempted=False)))
    assert 'terminal closure incomplete' in str(raised.value)


# ------------------------------------------- the two config words' named bits
#
# `RF_REG_SRC_CFG` (0x14) and `RF_REG_CAP_CFG` (0x24) are named-bit words, not
# "the whole word must be zero": bit 2 of each is the configuration COMMIT
# EPOCH, and the core commits on an edge, so every successful configure leaves
# that bit set and a real post-release readback is `cap_cfg == 4`.  The safety
# property is the control bits, so both halves are pinned here: the epoch may
# hold either level, and every other bit -- including bit 0 while the epoch is
# high -- is still refused.  This is the 2026-10-15 evidence defect: the old
# predicate read the word as a boolean and called `cap_cfg=4` an armed capture.
# The 2026-10-17 defect (attempt 3) is the same class one bit over: `src_cfg` bit
# 1 is the TX wrap MODE, the study's own `wrap=true` load leaves it set, and
# refusing it stopped a frame the native gate had accepted.  The dedicated
# section at the bottom of this file carries that evidence and its counter-
# examples; the parametrizations here keep the per-word rule honest.
COMMIT_EPOCH = 0x4
TX_WRAP = 0x2


def _word_frame(label, key, value):
    """One frame whose named readback carries `value` in `key`."""
    frame = _frame()
    frame.header['headroom'][label]['pl'][key] = value
    return frame


def _with_commit_epoch(mutate):
    """A corruption applied on top of both words' allowed commit-epoch form."""
    def combined(header, attempt):
        for label in ('actual_rf_before', 'actual_rf_after'):
            header['headroom'][label]['pl'].update(src_cfg=COMMIT_EPOCH,
                                                   cap_cfg=COMMIT_EPOCH)
        mutate(header, attempt)
    return combined


@pytest.mark.parametrize('label', ['actual_rf_before', 'actual_rf_after'])
@pytest.mark.parametrize('key,value', [
    ('cap_cfg', 0), ('cap_cfg', COMMIT_EPOCH),
    ('src_cfg', 0), ('src_cfg', COMMIT_EPOCH),
])
def test_the_commit_epoch_may_hold_either_level_in_the_fixed_domain(key, value,
                                                                   label):
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                                      _word_frame(label, key, value))
    assert evidence['attempt'] == 2 and evidence['hardware_verified'] is True
    assert evidence[label]['pl'][key] == value


@pytest.mark.parametrize('label', ['actual_rf_before', 'actual_rf_after'])
@pytest.mark.parametrize('key,value', [
    # Bit 0 is the control bit: a real arm/select is fail-closed ...
    ('cap_cfg', 1), ('src_cfg', 1),
    # ... the capture word's bit 1 would run the capture continuously ...
    ('cap_cfg', 2),
    # ... and the commit epoch never excuses either of them.  The TX word's bit
    # 1 is deliberately NOT in this list since 2026-10-17: it is the wrap MODE
    # this study selects itself, so it is refused only together with bit 0.
    ('cap_cfg', 3), ('cap_cfg', 5), ('cap_cfg', 6), ('cap_cfg', 7),
    ('src_cfg', 3), ('src_cfg', 5), ('src_cfg', 7),
    # Bits 3 and above are undefined for both registers.
    ('cap_cfg', 8), ('src_cfg', 8),
])
def test_every_other_bit_of_the_two_config_words_is_still_refused(key, value,
                                                                 label):
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                               _word_frame(label, key, value))


@pytest.mark.parametrize('key,value,fragment', [
    ('cap_cfg', 0x1, 'CAP_ARM'),
    ('src_cfg', 0x1, 'SRC_SEL'),
    ('cap_cfg', 0x2, 'CAP_CONT'),
    ('cap_cfg', 0x6, 'CAP_CONT'),
    ('cap_cfg', 0x8, 'undefined'),
    ('src_cfg', 0x8, 'undefined'),
])
def test_the_refusal_names_the_bit_that_is_actually_set(key, value, fragment):
    """The old message blamed CAP_ARM for a commit epoch; the reason must fit."""
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                               _word_frame('actual_rf_after', key, value))
    message = str(raised.value)
    assert message.startswith('actual_rf_after.pl.' + key)
    assert fragment in message


def test_the_commit_epoch_alone_is_never_reported_as_an_arm():
    evidence = H.check_research_frame(
        H.HeadroomPlan(), SESSION, 2,
        _word_frame('actual_rf_after', 'cap_cfg', COMMIT_EPOCH))
    assert evidence['actual_rf_after']['pl']['cap_cfg'] == COMMIT_EPOCH


@pytest.mark.parametrize('mutate', _EVIDENCE_MUTATIONS)
def test_allowing_the_commit_epoch_does_not_weaken_any_other_gate(mutate):
    """Every other check still refuses a frame that carries the epoch."""
    frame = _frame(corrupt=_with_commit_epoch(mutate))
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)


def test_a_corrupted_payload_is_refused_even_when_the_header_hash_is_reused():
    frame = _frame()
    tampered = H.ResearchFrame(frame.request_id, dict(frame.header),
                               frame.payload[:-4] + b'\x01\x02\x03\x04')
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, tampered)


def test_an_off_frame_must_carry_no_source_and_no_declaration():
    frame = _frame(attempt=1)
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, 1, frame)
    assert evidence['source_enabled'] is False
    assert evidence['source_sha256'] == '' and evidence['declaration_sha256'] == ''
    bad = _roundtrip(frame.header)
    bad['headroom']['source_enabled'] = True
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(), SESSION, 1,
                               H.ResearchFrame(frame.request_id, bad, frame.payload))


def _rails_only(rails, terminal):
    """A header mutation that makes the declared rail state self-consistent.

    Both the count and the terminal are moved together, so a check that reads
    the header back instead of the bytes sees a coherent story and passes.
    """
    def mutate(header, attempt):
        header['headroom'].update(rail_count=rails, terminal=terminal)
    return mutate


def test_the_rail_count_is_recounted_from_the_payload_not_trusted():
    """The one property that separates this from a data passthrough.

    A header that agrees with itself but not with the bytes is the case that
    matters: the daemon's own claim is exactly what must not be believed, since
    rail-or-not is the entire outcome of a 46 dB headroom study.
    """
    assert H.check_research_frame(
        H.HeadroomPlan(), SESSION, 2, _frame())['rail_count'] == 0
    railed = _frame(rails_at=2)
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, 2, railed)
    assert evidence['rail_count'] == 2 * H.CAPTURE_SAMPLES
    assert evidence['terminal'] == 'CLIPPED'


@pytest.mark.parametrize('rails_at, declared, terminal', [
    # Railed bytes reported as a clean frame: the recount, not the header, is
    # what refuses it.
    (2, 0, 'AWAIT_ACCEPT'),
    # The other direction: a clean payload the daemon calls clipped.
    (None, 2 * H.CAPTURE_SAMPLES, 'CLIPPED'),
    # A coherent-looking lie that is simply the wrong number.
    (2, 4, 'CLIPPED'),
])
def test_a_header_that_disagrees_with_its_own_bytes_is_refused(
        rails_at, declared, terminal):
    frame = _frame(rails_at=rails_at, corrupt=_rails_only(declared, terminal))
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)
    assert 'rail' in str(raised.value)


def test_a_frame_must_name_the_reviewed_source_and_declaration():
    """Both declared digests, checked against the plan and not just for shape.

    A frame that carries a hash of some other payload -- or of a declaration the
    plan did not approve -- is a frame from another experiment, and the study's
    whole claim is that these five attempts used the reviewed inputs.
    """
    for field, wrong in (('source_sha256', 'a' * 64),
                         ('source_sha256', ''),          # the OFF-attempt value
                         ('declaration_sha256', 'b' * 64),
                         ('declaration_sha256', NATIVE_PAYLOAD_SHA['normal'])):
        frame = _frame(corrupt=lambda h, a, f=field, w=wrong:
                       h['headroom'].update({f: w}))
        with pytest.raises(H.HeadroomEvidenceError) as raised:
            H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)
        assert field.split('_')[0] in str(raised.value)


def test_an_off_frame_may_not_name_a_source_even_when_it_hashes_correctly():
    """The declaration is per-attempt: attempt 1 has none, and must say so."""
    frame = _frame(attempt=1, corrupt=lambda h, a: h['headroom'].update(
        source_sha256=NATIVE_PAYLOAD_SHA['normal']))
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(), SESSION, 1, frame)


@pytest.mark.parametrize('request_key, rx_path, tx_path', [
    ('lo_hz', 'rx_lo_hz', 'tx_lo_hz'),
    ('sample_rate_hz', 'rx_sample_rate_hz', 'tx_sample_rate_hz'),
])
def test_a_readback_must_agree_on_both_the_receiver_and_the_transmitter(
        request_key, rx_path, tx_path):
    """The plan asks once; the part has two registers, and both must match.

    An asymmetric readback is exactly the failure this catches: the TX and RX
    attenuators and their LO/sample-rate registers are separate, so one side
    silent and the other correct is a real half-applied configuration.
    """
    from rf_headroom import FIXED_RF as FIXED
    fixed = dict(FIXED)[request_key]
    for path in (rx_path, tx_path):
        for side in ('actual_rf_before', 'actual_rf_after'):
            frame = _frame(corrupt=lambda h, a, p=path, s=side:
                           h['headroom'][s].update({p: fixed + 1}))
            with pytest.raises(H.HeadroomEvidenceError) as raised:
                H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)
            why = str(raised.value)
            assert side in why and path in why, why


def test_the_same_fixed_rf_must_hold_before_the_capture_as_after_it():
    """Both legs of the transaction are evidence, not just the closing one.

    A frame proves the RF domain was right on both sides of the capture; a
    readback that only matched afterwards would leave the state the samples were
    actually taken in unproven.
    """
    for path, wrong in (('attenuation_mdB', 40000), ('tx1_attenuation_mdB', 40000),
                        ('tx2_attenuation_mdB', 40000), ('tx_fir_enabled', 0),
                        ('rx_fir_config_verified', False),
                        ('hardware_verified', 1)):
        frame = _frame(corrupt=lambda h, a, p=path, w=wrong:
                       h['headroom']['actual_rf_before'].update({p: w}))
        with pytest.raises(H.HeadroomEvidenceError) as raised:
            H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)
        assert 'actual_rf_before' in str(raised.value)
    frame = _frame(corrupt=lambda h, a:
                   h['headroom']['actual_rf_before']['pl'].update(idelay_tap=28))
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)
    assert 'actual_rf_before' in str(raised.value)


def test_a_simulated_frame_is_recorded_as_simulated_never_as_attestation():
    """The host fixture inverts hardware_verified; a fake run must stay labelled."""
    frame = _frame(verified=False)
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame,
                                      require_hardware=False)
    assert evidence['hardware_verified'] is False
    assert evidence['simulated'] is True
    assert H.check_research_frame(
        H.HeadroomPlan(), SESSION, 2, _frame())['simulated'] is False


# -------------------------------------------------------------- capability

def test_the_capability_gate_reads_the_live_daemon_not_a_hand_written_manifest():
    from test_rf_headroom_corrections import eligible
    live = eligible()
    assert live['available'] is True and live['study'] == H.STUDY
    assert live['protocol_minor'] == 6
    assert live['daemon_version'] == '1.0.4'
    for capability in (
            H.capability(5, H.CONTROL_OPS),
            H.capability(H.PROTOCOL_MINOR,
                         tuple(o for o in H.CONTROL_OPS if o != 'headroom.accept')),
            H.capability(H.PROTOCOL_MINOR + 1, H.CONTROL_OPS),
            H.capability(H.PROTOCOL_MINOR, H.CONTROL_OPS, daemon_version='1.0.3'),
            H.capability(H.PROTOCOL_MINOR, H.CONTROL_OPS, study='HEADROOM-2'),
            None, {}, 'HEADROOM-1'):
        assert H.capability_available(capability) is False
    assert H.capability_available(live) is True


def test_the_version_pair_is_declared_once():
    assert H.version_pair() == ('1.0.4', 1, 6)
    assert H.version_pair() == (H.DAEMON_VERSION, H.PROTOCOL_MAJOR, H.PROTOCOL_MINOR)


def test_the_controller_refuses_before_initialization_without_live_capability():
    for capability in (None, H.capability(5, H.CONTROL_OPS)):
        client = FakeClient(capability=capability)
        with pytest.raises(H.HeadroomStopped):
            _controller(client).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
        assert [c[0] for c in client.calls] == []
        assert client.disarms == 0


# -------------------------------------------------------------- controller

def test_the_controller_runs_the_fixed_order_on_one_retained_connection():
    client = FakeClient()
    receipt = _controller(client).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert [c[0] for c in client.calls] == [
        'headroom.begin',
        'headroom.capture', 'headroom.accept',
        'headroom.load', 'headroom.capture', 'headroom.accept',
        'headroom.load', 'headroom.capture', 'headroom.accept',
        'headroom.load', 'headroom.capture', 'headroom.accept',
        'headroom.capture', 'headroom.accept']
    assert [c[2]['attempt'] for c in client.calls if 'attempt' in c[2]] == [
        1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5]
    assert receipt['completed'] is True and receipt['disarmed'] is True
    assert receipt['attempts'] == H.ATTEMPT_BUDGET
    assert [a['scenario'] for a in receipt['frames']] == [
        'off-before', 'normal', 'narrowband', 'pulse', 'off-after']


def test_every_attempt_is_announced_before_it_is_sent_and_raw_before_analysis():
    recorder = FakeRecorder()
    _controller(FakeClient(), recorder=recorder).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    order = [(e[0], e[1]) for e in recorder.events]
    kinds = [e[0] for e in order]
    assert kinds.count('intent') == H.ATTEMPT_BUDGET
    assert kinds.count('frame') == H.ATTEMPT_BUDGET
    assert kinds.count('analysis') == H.ATTEMPT_BUDGET
    assert kinds.count('terminal') == 1 and kinds[-1] == 'terminal'
    for index, entry in enumerate(order):
        if entry[0] == 'frame':
            assert any(e == ('intent', entry[1]) for e in order[:index])
        if entry[0] == 'analysis':
            assert any(e == ('frame', entry[1]) for e in order[:index])
    assert recorder.raw[2] == _frame(attempt=2).payload
    assert len(recorder.raw[5]) == H.IQ_BYTES


def test_every_announced_attempt_ends_in_exactly_one_decision():
    """A refusal at load or capture is a decision about that attempt too.

    `decision` is what says how an attempt ended.  A refusal path that returns
    without recording one leaves an attempt that was announced (its `intent` is
    on disk, its request may even have reached the daemon) with no record of the
    outcome -- and the refusal at `accept` is recorded, so the same class of
    event would be filed two different ways.
    """
    for fail_at, announced in ((None, 5), ('headroom.begin', 0),
                               ('headroom.load:3', 3), ('headroom.capture:2', 2),
                               ('headroom.accept:4', 4)):
        recorder = FakeRecorder()
        client = FakeClient(fail_at=fail_at)
        _controller(client, recorder=recorder).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
        intents = [e[1] for e in recorder.events if e[0] == 'intent']
        decisions = [e[1] for e in recorder.events if e[0] == 'decision']
        assert intents == list(range(1, announced + 1)), (fail_at, intents)
        assert decisions == intents, (fail_at, decisions)
        assert len([e for e in recorder.events if e[0] == 'terminal']) == 1
        # And the study really is over.  A completed study sealed itself (the
        # fifth acceptance disarms), so nothing is asked for; every refusal above
        # leaves the daemon armed, and then the disarm has to be requested.
        assert client.armed is False
        assert client.disarms == 0
        assert [c[0] for c in client.calls].count('headroom.abort') == (0 if fail_at is None else 1)


def test_a_capture_that_never_arrived_is_recorded_as_missing_not_as_unreached():
    """Bullet 3: explicit incomplete evidence when the bytes are unavailable.

    "The original IQ is missing" is a result of a 46 dB headroom study, not a
    gap in the record.  It must be distinguishable from "that attempt was never
    reached", so the frame slot carries a marker rather than staying empty.
    """
    recorder = FakeRecorder()
    _controller(FakeClient(fail_at='headroom.capture:2'),
                         recorder=recorder).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert [e[1] for e in recorder.events if e[0] == 'frame'] == [1, 2]
    assert recorder.raw[2] is None
    assert recorder.raw[1] is not None
    decisions = [e for e in recorder.events if e[0] == 'decision']
    assert [e[1] for e in decisions] == [1, 2]
    assert decisions[1][2] is False


def test_the_saved_iq_replay_is_recorded_before_the_attempt_is_accepted():
    """A study that is accepted is one whose bytes were read back, not just kept.

    The replay runs on the payload that was already written to disk and its
    result is recorded before the acceptance is sent, so an accepted attempt
    cannot be one whose saved bytes were never re-read.
    """
    recorder = FakeRecorder()
    seen = []

    def replayer(attempt, evidence):
        seen.append(attempt)
        return dict(attempt=attempt, replay=True,
                    payload_sha256=evidence['iq_sha256'])

    _controller(FakeClient(), recorder=recorder,
                         replayer=replayer).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert seen == [1, 2, 3, 4, 5]
    assert sorted(recorder.replays) == [1, 2, 3, 4, 5]
    assert recorder.replays[2]['payload_sha256'] == _frame(attempt=2).header[
        'headroom']['iq_sha256']
    order = [e[0] for e in recorder.events]
    for index, entry in enumerate(recorder.events):
        if entry[0] == 'replay':
            assert any(e == ('frame', entry[1]) for e in recorder.events[:index])
            later = [e for e in recorder.events[index:] if e[0] == 'decision'
                     and e[1] == entry[1]]
            assert later, 'a replay must precede its own decision'


def test_a_replayer_that_raises_stops_the_study_before_acceptance():
    """A replay that cannot run is a failure of the evidence, and it stops."""
    class Boom(Exception):
        pass

    client = FakeClient()

    def replayer(attempt, evidence):
        raise Boom('the replay could not run')

    with pytest.raises(Boom):
        _controller(client, recorder=FakeRecorder(),
                             replayer=replayer).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert not any(c[0] == 'headroom.accept' for c in client.calls)
    assert client.armed is False


def test_a_native_refusal_stops_the_study_at_that_attempt_without_retry():
    cases = (('headroom.begin', 0, 0), ('headroom.load:3', 2, 2),
             ('headroom.capture:2', 1, 2), ('headroom.accept:4', 3, 4))
    for fail_at, completed, captures in cases:
        client = FakeClient(fail_at=fail_at)
        receipt = _controller(client).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
        assert receipt['completed'] is False
        assert receipt['stopped_at'] == fail_at
        assert receipt['attempts'] == completed
        assert client.disarms == 0
        assert any(c[0] == 'headroom.abort' for c in client.calls)
        assert sum(1 for c in client.calls if c[0] == 'headroom.capture') == captures
        assert sum(c[0] == 'headroom.abort' for c in client.calls) == 1


def test_a_railed_frame_is_kept_and_never_accepted():
    client = FakeClient(rails_at=2)
    recorder = FakeRecorder()
    receipt = _controller(client, recorder=recorder).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert receipt['completed'] is False
    assert receipt['stopped_at'] == 'CLIPPED'
    assert len(recorder.raw[2]) == H.IQ_BYTES
    assert not any(c[0] == 'headroom.accept' and c[2]['attempt'] == 2
                   for c in client.calls)


def test_an_invalid_frame_is_kept_as_evidence_and_stops_before_acceptance():
    def corrupt(header, attempt):
        header['headroom']['iq_sha256'] = '0' * 64

    client = FakeClient(corrupt=corrupt)
    recorder = FakeRecorder()
    receipt = _controller(client, recorder=recorder).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert receipt['completed'] is False
    assert receipt['stopped_at'] == 'EVIDENCE:1'
    assert len(recorder.raw[1]) == H.IQ_BYTES
    assert not any(c[0] == 'headroom.accept' for c in client.calls)


def test_an_off_attempt_that_synchronised_stops_the_study():
    client = FakeClient()
    receipt = _controller(client, off_guard=OffSync()).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert receipt['completed'] is False
    assert receipt['stopped_at'] == 'OFF_EVIDENCE'
    assert sum(1 for c in client.calls if c[0] == 'headroom.capture') == 1
    assert client.disarms == 0
    assert any(c[0] == 'headroom.abort' for c in client.calls)


def test_cancellation_stops_immediately_and_aborts_the_live_study():
    client = FakeClient()
    state = {'n': 0}

    def cancelled():
        return sum(c[0]=='headroom.capture' for c in client.calls) >= 3

    receipt = _controller(client, cancelled=cancelled).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert receipt['completed'] is False
    assert receipt['stopped_at'] == 'CANCELLED'
    # The abort IS the close: `op_headroom_abort` calls `headroom_close`, which
    # disarms, so no separate disarm is asked for -- and the daemon still has to
    # report itself unarmed for the receipt to say so.
    assert client.disarms == 0
    assert client.armed is False
    assert receipt['disarmed'] is True
    assert sum(1 for c in client.calls if c[0] == 'headroom.capture') == 3
    assert [c[0] for c in client.calls].count('headroom.abort') == 1


def test_the_controller_reports_an_unconfirmed_final_cleanup_as_disarmed_false():
    class Refusing(FakeClient):
        def headroom_abort(self,args):
            self._next('headroom.abort',args)
            raise StatusProtocolError('RF daemon refused headroom.abort','RF_HARDWARE_IO')
    receipt=_controller(Refusing(fail_at='headroom.capture:5')).run(
        SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert receipt['completed'] is False and receipt['disarmed'] is False
    assert receipt['close_ack']['armed'] is True
    assert 'closing readback unconfirmed' in receipt['reason']


def test_a_daemon_whose_armed_state_cannot_be_read_is_not_reported_as_disarmed():
    class Blind(FakeClient):
        def control_armed(self):
            raise StatusProtocolError('RF daemon is unreachable','RF_TRANSPORT')
    receipt=_controller(Blind()).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert receipt['completed'] is False and receipt['disarmed'] is False
    assert 'RF daemon is unreachable' in receipt['close_ack']['error']


def test_a_study_can_never_reach_a_sixth_attempt():
    plan = H.HeadroomPlan()
    for attempt in range(6, 10):
        with pytest.raises(ValueError):
            plan.capture_args(attempt, SESSION)
        with pytest.raises(ValueError):
            plan.accept_args(attempt, SESSION, 1, 1)
    client = FakeClient()
    _controller(client).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert sum(1 for c in client.calls if c[0] == 'headroom.capture') == 5


@pytest.mark.parametrize('predicate', ['off_guard', 'cancelled'])
@pytest.mark.parametrize('boom', [ValueError, KeyboardInterrupt])
def test_a_predicate_that_raises_still_records_that_the_study_is_over(
        predicate, boom):
    """The terminal record is written exactly when it is hardest to keep.

    A caller-supplied predicate raising something other than `HeadroomStopped`
    means nobody modelled what happened.  The study does stop (the caller sees
    the raise) and the disarm still runs -- but an artifact that says nothing
    about how the study ended, on disk, at the moment it crashed, is the worst
    time to lose it.
    """
    recorder = FakeRecorder()

    def broken(*args, **kwargs):
        raise boom('predicate crashed')

    client = FakeClient()
    with pytest.raises(BaseException) as raised:
        _controller(client, recorder=recorder,
                             **{predicate: broken}).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert type(raised.value) is boom
    terminal = [e for e in recorder.events if e[0] == 'terminal']
    assert len(terminal) == 1
    assert terminal[0][1] == 'CRASHED'
    assert client.disarms == 0
    assert any(c[0]=='headroom.abort' for c in client.calls) is (predicate == 'off_guard')


def test_a_recorder_that_fails_is_not_silently_eaten():
    """A recorder that cannot write is a failure of the evidence, not of the study."""
    class Broken(FakeRecorder):
        def terminal(self, outcome, detail):
            raise OSError('the evidence disk is full')

    client = FakeClient(fail_at='headroom.capture:2')
    with pytest.raises(OSError):
        _controller(client, recorder=Broken()).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    # The study still ended: the disarm ran even though the receipt could not be
    # written, so a failed evidence write never leaves the part armed.
    assert client.armed is False


def test_a_session_identifier_is_generated_once_and_is_native_shaped():
    value = H.new_session_id()
    assert len(value) == 32 and all(c in '0123456789abcdef' for c in value)
    assert value != H.new_session_id()


# ------------------------------------------------------------------- 46 dB

def test_the_single_46db_domain_is_accepted_and_any_other_value_is_refused():
    """One domain now covers teaching and research; the old 40 dB and near misses refuse.

    The migration moved the teaching/environment attenuation onto the research
    value, so the fixed set is accepted while 40000 / 45999 / 46100 stay out of
    domain -- including on the whole public path, not only in the helper.
    """
    import rf_environment as E
    from rf_bpsk_pipeline import freeze_demod_config, analyze_demod
    from test_rf_bpsk_receiver import frame as captured, signal

    shot = captured(signal(phase=0.3))
    config = freeze_demod_config(dict(expected_mode='BPSK', symrate_hz=3000000,
                                      shaping='RECT', reference_enabled=False,
                                      reference_seed=1, decim=1, rx_len=4096,
                                      input_fs_hz=60000000), 9)
    envelope = analyze_demod(shot, config)
    fixed = dict(E.FIXED_RF)
    assert fixed['attenuation_mdB'] == H.RESEARCH_ATTENUATION_MDB == 46000
    assert E._domain_error(shot, envelope, fixed) is None
    for rejected in (40000, 45999, 46100):
        other = dict(fixed, attenuation_mdB=rejected)
        assert E._domain_error(shot, envelope, other) == 'RF_DOMAIN_MISMATCH', rejected
        # The refusal has to survive the whole public path, not just the helper.
        result = E.analyze_environment(shot, envelope, other)
        assert (result.label, result.reason) == ('INVALID', 'RF_DOMAIN_MISMATCH')


# ---------------------------------------------------------- the real client

HEADROOM_OPS = list(H.CONTROL_OPS)


class ScriptedDaemon:
    """A minimal RF daemon speaking the real wire framing for research requests.

    The shared client test harness serves JSON results only; a research capture
    answers with a JSON header followed by 16384 raw bytes, and an oversized
    load sends 10160 raw bytes after its header.  Those two shapes are the point
    of this fixture, so they are scripted here rather than bolted onto a harness
    forty other tests depend on.
    """

    def __init__(self, capability=_UNSET, load_result=None, capture=None,
                 capture_refusal=None, accept_result=None, abort_result=None,
                 announce=True, verified=True, armed=False, misbehave=None):
        import socket
        import threading
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(4)
        self.port = self.socket.getsockname()[1]
        self.capability = (dict(study=H.STUDY, protocol_minor=H.PROTOCOL_MINOR,
                                initialization_budget=1, attempt_budget=5,
                                used=False, active=False, operations=HEADROOM_OPS)
                           if capability is _UNSET else capability)
        self.announce = announce
        self.verified = verified
        self.load_result = load_result or dict(loaded=True, samples=H.LOAD_SAMPLES)
        self.capture = capture or _default_capture()
        self.capture_refusal = capture_refusal
        self.accept_result = accept_result or dict(accepted=True)
        self.abort_result = abort_result or dict(aborted=True, armed=False)
        self.armed = armed
        # A hook that can misbehave on the wire (a wrong reply id, a wrong
        # declared length).  A fixture that is always well-formed cannot reach
        # the client's own framing guards, and a guard nothing reaches is not
        # evidence.
        self.misbehave = misbehave
        # The daemon's own study lifecycle (`d->headroom.active`): while it is
        # set, a control operation other than `headroom.*` seals the study, and
        # `headroom.accept` on the fifth attempt seals it itself.
        self.active = False
        self.seen = []
        self.connections = 0
        self.per_connection = []
        self.binary = {}
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def settle(self, requests, timeout=3.0):
        """Wait until the daemon has answered `requests` requests, or give up.

        The acceptor thread never ends, so `join` would always burn its whole
        timeout; waiting on a request count instead also makes each test assert
        against a wire state that has actually happened rather than a guess.
        """
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.seen) >= requests:
                return True
            time.sleep(0.005)
        raise AssertionError('the daemon saw %d of %d requests'
                             % (len(self.seen), requests))

    def _run(self):
        try:
            while True:
                conn, _ = self.socket.accept()
                self.connections += 1
                ops = []
                self.per_connection.append(ops)
                with conn:
                    self._serve(conn, ops)
        except OSError:
            pass

    def _serve(self, conn, ops):
        buffer = b""
        while True:
            while b"\n" not in buffer:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buffer += chunk
            line, buffer = buffer.split(b"\n", 1)
            request = json.loads(line.decode("utf-8"))
            self.seen.append(request)
            ops.append(request["op"])
            args = request.get("args") or {}
            if request["op"] == "headroom.load":
                want = args.get("bytes")
                while len(buffer) < want:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buffer += chunk
                self.binary["headroom.load"] = buffer[:want]
                buffer = buffer[want:]
            conn.sendall(self._reply(request))

    def _reply(self, request):
        op, rid = request["op"], request["id"]
        if op == "protocol.hello":
            return _json(rid, True, dict(daemon=H.DAEMON_VERSION, protocol_major=1,
                                         protocol_minor=H.PROTOCOL_MINOR,
                                         max_samples=4096))
        if op == "device.info":
            from rf_status_client import READ_ONLY_OPS
            capabilities = dict(ops=list(READ_ONLY_OPS))
            if self.announce:
                capabilities["headroom"] = self.capability
            if self.armed:
                capabilities["control_ops"] = ["control.disarm"]+HEADROOM_OPS
            else:
                capabilities["control_ops"] = ["control.arm"]
            return _json(rid, True, dict(daemon=H.DAEMON_VERSION, protocol_major=1,
                                         protocol_minor=H.PROTOCOL_MINOR,
                                         max_samples=4096, armed=self.armed,
                                         capabilities=capabilities))
        if op == 'state.get':
            return _json(rid,True,dict(state='OFFLINE' if not self.armed else 'READY',
                pending=False,pending_op=None,lease_held=True))
        if op == 'pl.status.get':
            return _json(rid,True,dict(sampled_at_monotonic_ns=1,io_ok=True,
                tx=dict(source_selected=False,length_words=0,position_words=0),
                rx=dict(armed=False,done=False,position_words=0)))
        if op == 'control.arm':
            self.armed=True
            return _json(rid,True,dict(armed=True))
        # `dispatch()` in rf_daemon.c: while a study is active, any control
        # operation that is not `headroom.*` ends it.  `control.disarm` /
        # `tx.stop` / `loopback.stop` answer ok with `headroom_terminal`; every
        # other one is refused with INVALID_STATE.
        if self.active and not op.startswith("headroom.") and op == "control.disarm":
            self.active = False
            self.armed = False
            return _json(rid, True, dict(armed=False, headroom_terminal=True))
        if op == "headroom.capture":
            if self.capture_refusal is not None:
                return _json(rid, False, None, error=self.capture_refusal)
            header, payload = self.capture(request)
            if self.misbehave is not None:
                header, payload = self.misbehave(header, payload)
            return (json.dumps(header, separators=(",", ":")).encode("ascii")
                    + b"\n" + payload)
        if op == "headroom.begin":
            self.active = True
            return _json(rid, True, dict(
                study=H.STUDY, session_id=request["args"]["session_id"],
                initializations=1, attempt_budget=H.ATTEMPT_BUDGET,
                actual_rf=rf_json(self.verified)))
        if op == "headroom.load":
            return _json(rid, True, self.load_result)
        if op == "headroom.accept":
            if self.accept_result.get("completed") or self._terminal_accept(request):
                # `op_headroom_accept` closes the study on attempt 5, and
                # `headroom_close` disarms: the reply itself is where the daemon
                # states that it is unarmed.
                self.active = False
                self.armed = False
                return _json(rid, True, dict(completed=True, attempts=H.ATTEMPT_BUDGET,
                                             armed=False))
            return _json(rid, True, self.accept_result)
        if op == "headroom.abort":
            self.active = False
            self.armed = False
            return _json(rid, True, self.abort_result)
        if op == "control.disarm":
            if not self.armed:
                # Unarmed in status-only mode, the daemon refuses every control
                # op but `control.arm` -- which is the state a finished study
                # leaves behind.  Code and message are rf_daemon.c's own.
                return _json(rid, False, None, error=dict(
                    code="UNSUPPORTED",
                    message="operation disabled in status-only mode"))
            self.active = False
            self.armed = False
            return _json(rid, True, dict(armed=False))
        return _json(rid, False, None,
                     error=dict(code="UNSUPPORTED", message="unknown " + op))

    @staticmethod
    def _terminal_accept(request):
        """Whether this acceptance is the fifth attempt's (the sealing one)."""
        from rf_headroom import HeadroomPlan
        try:
            attempt = int(request["args"]["attempt"])
        except (KeyError, TypeError, ValueError):
            return False
        return attempt == HeadroomPlan().attempts


def _json(rid, ok, result, error=None):
    body = dict(id=rid, ok=ok)
    body["result"] = result if ok else None
    if not ok:
        body.pop("result")
        body["error"] = error
    return (json.dumps(body, separators=(",", ":")) + "\n").encode("ascii")


def _default_capture():
    def capture(request):
        attempt = int(request["args"]["attempt"])
        built = _frame(attempt=attempt)
        body = _roundtrip(built.header)
        body["id"] = request["id"]
        body["headroom"]["capture_request_id"] = request["id"]
        body['timestamp_ns']=100+request['id']
        return body, built.payload
    return capture


def _client(daemon, control_ops=None, leased=True):
    from rf_status_client import RfStatusClient, RfStatusSnapshot
    client = RfStatusClient(port=daemon.port, timeout=2, control_timeout=2)
    advertised = list(HEADROOM_OPS) + ['control.disarm']
    client._snapshot = RfStatusSnapshot(
        capabilities=tuple(HEADROOM_OPS),
        control_ops=tuple(advertised if control_ops is None else control_ops))
    if leased:
        guard=client.headroom_lease()
        guard.__enter__()
        old_close=client.close
        def close():
            guard.__exit__(None,None,None)
            old_close()
        client.close=close
    return client


def test_the_client_reads_a_research_frame_header_then_its_bytes_exactly_once():
    daemon = ScriptedDaemon()
    client = _client(daemon)
    frame = client.headroom_capture(H.HeadroomPlan().capture_args(1, SESSION))
    daemon.settle(2)
    assert isinstance(frame, H.ResearchFrame)
    assert frame.payload == _frame(attempt=1).payload
    assert len(frame.payload) == H.IQ_BYTES
    assert [r["op"] for r in daemon.seen][-1] == "headroom.capture"
    # The header and the bytes are one exchange, and the client checked the
    # reply's id against its own request id before it read anything.
    assert client._session_id == 2
    client.close()


def test_the_client_sends_the_research_payload_after_the_load_header():
    daemon = ScriptedDaemon()
    client = _client(daemon)
    plan = H.HeadroomPlan()
    result = client.headroom_load(plan.load_args(2, SESSION), plan.payload(2))
    daemon.settle(2)
    assert result == dict(loaded=True, samples=H.LOAD_SAMPLES)
    assert daemon.binary["headroom.load"] == plan.payload(2)
    args = daemon.seen[-1]["args"]
    assert args["source_sha256"] == NATIVE_PAYLOAD_SHA['normal']
    assert args["decl_sha256"] == NATIVE_DECLARATION_SHA['normal']
    client.close()


def test_the_client_refuses_a_research_load_whose_payload_is_the_wrong_size():
    """The local length guard, not the daemon's digest, is what this asserts.

    A wrong-length `headroom.load` is refused before it is sent, so a truncated
    payload never reaches the wire and cannot consume the one initialization.
    """
    daemon = ScriptedDaemon()
    client = _client(daemon)
    plan = H.HeadroomPlan()
    args = plan.load_args(2, SESSION)
    for payload, declared in ((plan.payload(2)[:-4], args),
                              (plan.payload(2), dict(args, bytes=args['bytes'] + 4)),
                              (plan.payload(2), dict(args, samples=2541))):
        with pytest.raises(StatusProtocolError):
            client.headroom_load(declared, payload)
    assert not any(r['op'].startswith('headroom.') for r in daemon.seen)
    assert client._session_socket is None  # no retry after a refused lease

    client.close()


def test_the_client_runs_a_whole_study_on_one_retained_connection():
    daemon = ScriptedDaemon()
    client = _client(daemon,leased=False)
    receipt = _controller(client).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    daemon.settle(17)
    assert daemon.connections == 1
    ops=daemon.per_connection[0]
    assert ops[:5]==['protocol.hello','device.info','state.get','pl.status.get','device.info']
    assert ops.count('control.arm')==1 and ops.count('headroom.begin')==1
    assert ops.count('headroom.capture')==5 and ops.count('headroom.accept')==5
    assert ops[-3:]==['device.info','state.get','pl.status.get']
    assert receipt['disarmed'] is True
    assert receipt['close_ack']['armed'] is False
    assert daemon.armed is False
    assert 'control.disarm' not in [r['op'] for r in daemon.seen]
    assert receipt['attempts'] == H.ATTEMPT_BUDGET
    assert client._headroom_session_ready is False
    client.close()


def test_a_research_refusal_is_retained_and_stops_without_retry():
    error=dict(code=-3,stage='CLOCK',clock_code=-3,close_code=-3,
               shutdown_code=-3,close_confirmed=False,iq_available=False)
    daemon=ScriptedDaemon(capture_refusal=error)
    client=_client(daemon,leased=False)
    recorder=FakeRecorder()
    receipt=_controller(client,recorder=recorder).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert receipt['completed'] is False and receipt['attempts']==0
    assert recorder.raw[1]==b''
    assert sum(r['op']=='headroom.capture' for r in daemon.seen)==1
    assert not any(r['op']=='headroom.accept' for r in daemon.seen)
    assert client._session_socket is None
    client.close()


def test_the_client_preserves_a_bounded_nonresearch_frame_before_refusal():
    daemon=ScriptedDaemon(misbehave=lambda h,p:(dict(h,type='iq_frame'),p))
    client=_client(daemon)
    frame=client.headroom_capture(H.HeadroomPlan().capture_args(1,SESSION))
    assert len(frame.payload)==H.IQ_BYTES
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(),SESSION,1,frame)
    client.close()


def test_wrong_id_bytes_are_preserved_then_the_controller_refuses():
    daemon=ScriptedDaemon(misbehave=lambda h,p:(dict(h,id=h['id']+1),p))
    client=_client(daemon)
    frame=client.headroom_capture(H.HeadroomPlan().capture_args(1,SESSION))
    assert len(frame.payload)==H.IQ_BYTES and frame.header['id']!=frame.request_id
    with pytest.raises(H.HeadroomEvidenceError,match='id'):
        H.check_research_frame(H.HeadroomPlan(),SESSION,1,frame)
    assert sum(r['op']=='headroom.capture' for r in daemon.seen)==1
    client.close()


def test_the_client_refuses_a_research_frame_whose_declared_length_is_wrong():
    """The frame's own framing, reached by a daemon that lies about its length.

    A declared length that is not `samples * 4` decides how many bytes the
    client reads off a binary stream; believing it would either swallow the
    next reply or leave half a frame in the buffer.
    """
    for samples, declared in ((H.CAPTURE_SAMPLES * 2, H.IQ_BYTES),
                              (H.CAPTURE_SAMPLES, H.IQ_BYTES - 4),
                              ('4096', H.IQ_BYTES)):
        daemon = ScriptedDaemon(misbehave=lambda header, payload, d=declared,
                                s=samples: (dict(header, bytes=d, samples=s),
                                            payload))
        client = _client(daemon)
        with pytest.raises(StatusProtocolError) as raised:
            client.headroom_capture(H.HeadroomPlan().capture_args(1, SESSION))
        assert "length" in str(raised.value)
        daemon.settle(2)
        assert client._session_socket is None
        client.close()


def test_a_research_frame_is_never_published_as_a_teaching_capture():
    """Research frames are not the panel's current capture, structurally.

    The ordinary RX path publishes its frame so `latest_frame()` can hand the
    panel something to draw and to save.  A research frame has no envelope, no
    constellation and no teaching identity, so publishing it would put a frame
    no teaching consumer can read into the one slot they all read.

    The assertion is made while the study's connection is still open: by the
    time a study ends, `_drop_session` has already discarded the slot for its own
    reasons, so waiting until then would pass whether or not the frame was ever
    published.
    """
    daemon = ScriptedDaemon()
    client = _client(daemon)
    before = client.latest_frame()
    assert before is None
    frame = client.headroom_capture(H.HeadroomPlan().capture_args(1, SESSION))
    daemon.settle(2)
    assert isinstance(frame, H.ResearchFrame)
    assert len(frame.payload) == H.IQ_BYTES
    # The frame the caller got is not the frame the panel would now draw.
    assert client.latest_frame() is None
    assert client._headroom_session_ready is True
    # And nothing on the research session can be mistaken for a live TX or RX
    # capture session, which is the other thing publishing would imply.
    assert client._tx_session_ready is False
    assert client._rx_session_ready is False
    client.close()


def test_the_client_never_sends_a_research_operation_it_was_not_told_about():
    daemon = ScriptedDaemon()
    client = _client(daemon, control_ops=[op for op in HEADROOM_OPS
                                          if op != "headroom.begin"])
    with pytest.raises(StatusProtocolError):
        client.headroom_begin(H.HeadroomPlan().begin_args(SESSION,cable_confirmed=True))
    assert not any(r['op'].startswith('headroom.') for r in daemon.seen)
    client.close()


def test_the_client_reads_the_capability_from_a_live_device_info():
    daemon = ScriptedDaemon()
    client = _client(daemon)
    live = client.headroom_capability()
    daemon.settle(2)
    assert H.capability_available(live) is True
    assert live['study'] == H.STUDY and live['protocol_minor'] == 6
    assert live['daemon_version'] == H.DAEMON_VERSION
    # The refusal follows the reply, not this client's own constant.
    assert H.capability_available(dict(live, protocol_minor=5)) is False
    client.close()


def test_an_absent_headroom_object_is_unavailable_not_assumed():
    daemon = ScriptedDaemon(announce=False)
    client = _client(daemon,leased=False)
    live = client.headroom_capability()
    daemon.settle(2)
    assert live['available'] is False
    assert H.capability_available(live) is False
    with pytest.raises(H.HeadroomStopped):
        _controller(client).run(SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    client.close()


def test_one_domain_covers_both_the_research_plan_and_the_teaching_profile():
    """The migration moved the teaching domain onto the research attenuation.

    Before 2026-10-17 teaching was 40000 and research 46000.  Now both come from
    the one single source, and the research plan is the teaching domain with
    nothing moved: the comparison below is an equality over the whole vocabulary,
    so a second value appearing in either direction fails here.
    """
    from rf_teaching_profile import ATTENUATION_MDB, teaching_capability
    teaching = list(teaching_capability().rf_parameters)
    assert dict(teaching)['attenuation_mdB'] == 46000
    assert ATTENUATION_MDB == 46000
    assert H.RESEARCH_ATTENUATION_MDB == 46000
    assert dict(H.FIXED_RF)['attenuation_mdB'] == 46000
    assert H.FIXED_RF == tuple(teaching)
    assert H.TEACHING_ATTENUATION_MDB == ATTENUATION_MDB == H.RESEARCH_ATTENUATION_MDB


# ------------------------------ pl.src_cfg bit 1 after a wrap-mode TX load
#
# 2026-10-17, attempt 3 (evidence/rf-teaching/s2-headroom-research-attempt3-
# 2026-10-17/).  The second capture answered with actual_rf_before.pl.src_cfg = 0
# and actual_rf_after.pl.src_cfg = 6, cap_cfg = 0, and was refused with
# "actual_rf_after.pl.src_cfg has SMP_WRAP (bit1) set".  The word 6 is bits 1
# and 2: SMP_WRAP plus the commit epoch, which is exactly what the reviewed path
# produces for the study's own wrap=true TX load --
#
#   rf_daemon.c:2079   rf_core_configure_tx_path(core, 2540, true, NULL)
#   rf_core.c:1780     cfg = wrap ? (cfg | RF_SMP_WRAP) : (cfg & ~RF_SMP_WRAP);
#   rf_core.c:1781-82  commit_toggle(...) leaves SMP_COMMIT at 1
#   rf_daemon.c:2090   rf_core_release_part_keep_state()  -> clears bit 0 only
#   rf_daemon.c:2092   the after-readback, taken AFTER that release
#
# and the same sequence with wrap=false yields src_cfg = 4, which the old
# predicate accepted.  So the word the predicate refused differs from the word
# it accepted only by the mode bit the study itself requests.  The offline
# reproduction of every archived readback is work/probe_src_cfg_lifecycle.c
# (host PL model, real rf_core.c, no board).
#
# The native fixed-domain predicate never required this bit: hr_rf_fixed
# (rf_daemon.c:1992-2006) tests only bit 0 of each word, it passed this very
# frame (native_error.stage == NONE, post_readback_code == 0), and the frame
# reached the controller at AWAIT_ACCEPT.  The Python predicate was therefore
# strictly stronger than the safety gate it mirrors, on a bit that cannot arm,
# select or run anything while bit 0 is clear (rf_tx_source.v:130-176).
#
# The capture word's bit 1 is different and stays refused: CAP_CONT means a
# capture that never stops, and rf_core_prepare_rx(..., false) clears it before
# every arm, so a 1 there can only mean "this was not the fixed transaction".

# The two readbacks the board actually returned for the refused frame.  They are
# literals, not a re-read of the archive, so the test keeps its meaning if the
# evidence directory is ever moved.
ARCHIVED_BEFORE_PL = dict(phy_mode=0, idelay_en=127, idelay_tap=29, src_cfg=0,
                          cap_cfg=4, cap_ratio=0, cap_len=4096)
ARCHIVED_AFTER_PL = dict(phy_mode=0, idelay_en=127, idelay_tap=29, src_cfg=6,
                         cap_cfg=0, cap_ratio=0, cap_len=4096)


def test_the_archived_attempt3_readback_is_inside_the_fixed_domain():
    """The frame attempt 3 lost.  It must be evidence, not a refusal."""
    frame = _frame()
    frame.header['headroom']['actual_rf_before']['pl'].update(ARCHIVED_BEFORE_PL)
    frame.header['headroom']['actual_rf_after']['pl'].update(ARCHIVED_AFTER_PL)
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, 2, frame)
    assert evidence['actual_rf_after']['pl']['src_cfg'] == 6
    assert evidence['actual_rf_after']['pl']['cap_cfg'] == 0
    assert evidence['hardware_verified'] is True


@pytest.mark.parametrize('label', ['actual_rf_before', 'actual_rf_after'])
@pytest.mark.parametrize('value', [TX_WRAP, TX_WRAP | COMMIT_EPOCH])
def test_the_tx_wrap_bit_alone_is_not_an_activity(label, value):
    """SMP_WRAP with the control bit clear is inert configuration."""
    evidence = H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                                      _word_frame(label, 'src_cfg', value))
    assert evidence[label]['pl']['src_cfg'] == value


@pytest.mark.parametrize('label', ['actual_rf_before', 'actual_rf_after'])
@pytest.mark.parametrize('value', [1, 3, 5, 7])
def test_the_tx_control_bit_is_still_fail_closed_with_the_wrap_bit_set(label,
                                                                      value):
    """The counterexample to "allowing bit 1 lets an illegal state through".

    Every word that still selects the TX source is refused, whether or not the
    wrap bit is also set, and the reason names the control bit.
    """
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                               _word_frame(label, 'src_cfg', value))
    assert 'SRC_SEL' in str(raised.value)


@pytest.mark.parametrize('label', ['actual_rf_before', 'actual_rf_after'])
@pytest.mark.parametrize('value,fragment', [
    # CAP_CONT alone, or with the commit epoch, names the running capture ...
    (2, 'CAP_CONT'), (6, 'CAP_CONT'),
    # ... and the control bit is reported first when both are set.
    (3, 'CAP_ARM'), (7, 'CAP_ARM'),
])
def test_the_capture_continuous_bit_is_still_refused(label, value, fragment):
    """CAP_CONT is not the TX wrap bit: a capture that never stops is not the
    fixed transaction, so the same bit number means the opposite here."""
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                               _word_frame(label, 'cap_cfg', value))
    assert fragment in str(raised.value)


@pytest.mark.parametrize('label', ['actual_rf_before', 'actual_rf_after'])
@pytest.mark.parametrize('value', [0x8, 0xA, 0xC, 0xE, 0x10])
def test_the_undefined_bits_stay_refused_even_with_the_wrap_bit_set(label, value):
    """Bit 3 and above are defined by neither register, so they never become
    acceptable by association with the wrap bit."""
    with pytest.raises(H.HeadroomEvidenceError) as raised:
        H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                               _word_frame(label, 'src_cfg', value))
    assert 'undefined' in str(raised.value)


def _oracle_src(word):
    """The fixed domain of the TX word, written from the register map alone."""
    control = (word & 0x1) == 0        # RF_SRC_SEL must be clear
    undefined = (word & ~0x7) == 0     # platform_rf.h: only bits 0..2 exist
    return control and undefined


def _oracle_cap(word):
    """The fixed domain of the capture word: bit 1 is CAP_CONT here."""
    control = (word & 0x1) == 0        # RF_CAP_ARM must be clear
    continuous = (word & 0x2) == 0     # CAP_CONT must be clear
    undefined = (word & ~0x7) == 0
    return control and continuous and undefined


@pytest.mark.parametrize('key,oracle', [('src_cfg', _oracle_src),
                                        ('cap_cfg', _oracle_cap)])
def test_the_verdict_is_exactly_the_register_map_for_every_word(key, oracle):
    """Exhaustive truth table over the whole 8-bit space, against an oracle
    written independently of _CONFIG_WORDS.

    This is what makes "bit 1 of the TX word may be set" a bounded statement:
    for every one of the 256 values the predicate's verdict equals the map's, so
    it cannot admit an armed/selected word and cannot refuse a legal one.
    """
    for word in range(0x100):
        refused = False
        try:
            H.check_research_frame(H.HeadroomPlan(), SESSION, 2,
                                   _word_frame('actual_rf_after', key, word))
        except H.HeadroomEvidenceError:
            refused = True
        assert refused is (not oracle(word)), (key, hex(word))


def test_the_wrap_allowance_does_not_touch_the_requested_domain():
    """The allowance is about the READBACK, not the request: the plan still asks
    for exactly the reviewed 46 dB domain, and wrap is not in it."""
    args = H.HeadroomPlan().begin_args(SESSION, cable_confirmed=True)
    assert 'wrap' not in args
    assert dict(H.FIXED_RF)['attenuation_mdB'] == 46000
    assert dict(H.FIXED_RF)['phy_mode_1r1t'] == 0
    assert H.RESEARCH_ATTENUATION_MDB == 46000
