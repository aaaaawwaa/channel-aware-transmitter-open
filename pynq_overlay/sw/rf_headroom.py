"""HEADROOM-1: the bounded 46 dB research plan, controller and evidence.

Since the 2026-10-17 domain migration the teaching/environment domain runs at the
same 46 dB as this study (rf_teaching_profile.ATTENUATION_MDB is the single
source), so the research transaction is a separately reviewed contract on the
common domain: one initialization, five attempts in a fixed order, 46000 mdB, and
its own software contract.  This module holds the plan, the controller that
serialises it over one retained connection, and the checks that read each frame
back from the native reply rather than from what was sent.

Nothing here opens a socket, arms anything, or talks to a board.  The client
supplies the transport; the controller only decides what to send and when to
stop.  A study that fails for any reason stops permanently -- there is no retry,
no replacement frame and no top-up, because a second attempt at a consumed
budget is a different experiment.
"""
from dataclasses import dataclass, fields
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import time

from rf_experiment_scenarios import (PERIOD_SAMPLES, SAMPLE_RATE_HZ,
                                     ScenarioConfig, generate_scenario)
from rf_teaching_profile import ATTENUATION_MDB

STUDY = 'HEADROOM-1'
DAEMON_VERSION = '1.0.4'
PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 6

ATTEMPT_BUDGET = 5
SCENARIOS = ('off-before', 'normal', 'narrowband', 'pulse', 'off-after')
TRANSMITTING = (2, 3, 4)

RESEARCH_ATTENUATION_MDB = 46000
# Compatibility name for the old split-domain wording: the teaching domain is not a
# second attenuation any more, it is the same single source the platform reads.
TEACHING_ATTENUATION_MDB = ATTENUATION_MDB

CAPTURE_SAMPLES = 4096
IQ_BYTES = CAPTURE_SAMPLES * 4
LOAD_SAMPLES = PERIOD_SAMPLES
LOAD_BYTES = LOAD_SAMPLES * 4
MAX_IQ_BYTES = IQ_BYTES

CONTROL_OPS = ('headroom.begin', 'headroom.load', 'headroom.capture',
               'headroom.accept', 'headroom.abort')

# Same names and order as the teaching capability's rf_parameters, with the one
# reviewed variable moved.  Hard-coded on purpose: deriving it from the teaching
# profile would make this contract follow a later teaching change silently.
FIXED_RF = (('lo_hz', 80000000), ('sample_rate_hz', 60000000),
            ('rx_bandwidth_hz', 30000000), ('tx_bandwidth_hz', 30000000),
            ('attenuation_mdB', RESEARCH_ATTENUATION_MDB), ('phy_mode_1r1t', 0),
            ('idelay_en', 1), ('idelay_tap', 29))

TIMING_ORDER = ('source_begin', 'source_end', 'rx_arm_begin', 'rx_arm_end',
                'read_begin', 'read_end', 'release_begin', 'release_end')
TIMING_SCOPE = 'API_MONOTONIC_NOT_SAMPLE_EDGES'
TERMINALS = ('AWAIT_ACCEPT', 'CLIPPED', 'ERROR')

# The evidence block the daemon appends to the research frame template with a
# second `snprintf` that starts at offset `n-2` (rf_daemon.c:2136-2141).  The
# template's literal ends with `...timing_ns":[...]}}`, so that offset lands on
# top of the two closing braces and the append re-closes `headroom` and then the
# frame: all four keys are written INSIDE `headroom`.  No producer writes them
# on the frame object, which is why that layer must stay clear of them.
APPENDED_EVIDENCE = ('native_error', 'closure', 'timing_scope', 'timing_order')

# The reviewed payload/declaration identities the native contract embeds.  The
# plan recomputes them from the generator and refuses to send unless they match,
# so a generator change breaks the load before it reaches BRAM.
NATIVE_PAYLOAD_SHA = {
    2: '38b3f36ae20b6fe232d68d677b9e5e00c0b246c3617d0ff27239e89d0c3ef7f6',
    3: '4693355b00a98acc26851df3c86e38da49672fb7713983b29edd061627845f73',
    4: '22463cf461cd2141fbd3641d28294a740a39aa985c7943bea5aeb2bfcb13bbee',
}
NATIVE_DECLARATION_SHA = {
    2: '788e61dc71ab5d6ffbe28f67f7797457539819920c955daff2ad96ca2d200a6b',
    3: 'b02d0cdd464109d9df9a3dc13eb38b0d9b0b8075b88819c96a52a48c061b780a',
    4: 'e65d79dc4aa4912fdd84f5f41602590774d25ab1b96616f65f83ddea01d9c53b',
}

# The declaration as it is hashed: canonical sorted compact JSON, no truncation
# and no whitespace, so the digest in the native contract has one meaning.
DECLARATION_SEPARATORS = (',', ':')
_HEX = '0123456789abcdef'


class HeadroomEvidenceError(ValueError):
    """A research frame did not carry the identity its own transaction requires."""


class HeadroomStopped(RuntimeError):
    """The study ended before its fifth acceptance; the reason is the message."""


def version_pair():
    """The daemon/protocol pair this client may run; they move together."""
    return (DAEMON_VERSION, PROTOCOL_MAJOR, PROTOCOL_MINOR)


def new_session_id():
    """One 32-character lowercase hex identifier per study, chosen by the client."""
    return os.urandom(16).hex()


def declaration_digest(declaration):
    return hashlib.sha256(json.dumps(
        declaration, sort_keys=True, separators=DECLARATION_SEPARATORS,
        allow_nan=False).encode('utf-8')).hexdigest()


def payload_digest(payload):
    return hashlib.sha256(payload).hexdigest()


def capability(protocol_minor, operations, daemon_version=DAEMON_VERSION,
               study=STUDY):
    """The capability as read from one live device.info reply.

    The daemon advertises its own `headroom` object; `daemon_version` comes from
    the same reply.  This function only assembles what was received -- it never
    invents availability, which is why it takes the values rather than a
    manifest path.
    """
    ops = tuple(operations) if isinstance(operations, (list, tuple)) else None
    valid = (type(protocol_minor) is int and protocol_minor >= 0
             and ops is not None
             and all(type(op) is str for op in ops)
             and type(daemon_version) is str and type(study) is str)
    return dict(study=study, protocol_minor=protocol_minor,
                daemon_version=daemon_version,
                operations=list(ops or ()), available=bool(valid))


def capability_available(value):
    """True only for the exact reviewed pair announcing every research operation.

    A different minor is a different transaction this client has not reviewed,
    so it is refused rather than assumed compatible.
    """
    if type(value) is not dict or value.get('available') is not True:
        return False
    if (value.get('study') != STUDY or value.get('protocol_minor') != PROTOCOL_MINOR
            or value.get('daemon_version') != DAEMON_VERSION):
        return False
    operations = value.get('operations')
    if type(operations) is not list:
        return False
    if operations != list(CONTROL_OPS):
        return False
    for key, expected in (('hello_major',1),('hello_minor',6),
            ('announced_major',1),('announced_minor',6),
            ('initialization_budget',1),('attempt_budget',5)):
        if type(value.get(key)) is not int or value[key] != expected:
            return False
    if value.get('state') != 'OFFLINE' or value.get('pending_op') is not None:
        return False
    # serve() acquires the exclusive CONNECTION lease before the first request.
    # This idle reply is from that client; ownership is checked by the pinned
    # client before/after the live reads, not inferred from active RF state.
    return (value.get('lease_held') is True
            and all(value.get(k) is False for k in ('used','active','armed','pending',
                'tx_active','rx_busy')) and value.get('io_ok') is True)


@dataclass(frozen=True)
class ResearchFrame:
    """One native research reply: the request it answers, its header, its bytes."""
    request_id: int
    header: dict
    payload: bytes
    transport: object = None


@dataclass(frozen=True)
class HeadroomPlan:
    """The fixed contract.  It has no parameters, by construction.

    Every value is a literal of the reviewed contract, so there is no argument
    through which a caller could express the 40 dB teaching domain, a different
    attempt order, a nonzero wait, or a sixth attempt.
    """

    @property
    def attempts(self):
        return ATTEMPT_BUDGET

    def scenario(self, attempt):
        _check_attempt(attempt)
        return SCENARIOS[attempt - 1]

    def is_transmitting(self, attempt):
        _check_attempt(attempt)
        return attempt in TRANSMITTING

    def begin_args(self, session_id, cable_confirmed=False):
        _check_session_id(session_id)
        if cable_confirmed is not True:
            raise ValueError('the physical loopback cable must be confirmed')
        args = dict(study=STUDY, session_id=session_id,
                    cable_confirmed=True)
        args.update(FIXED_RF)
        return args

    def declaration(self, attempt):
        return generate_scenario(self._config(attempt)).declaration_dict()

    def payload(self, attempt):
        return generate_scenario(self._config(attempt)).payload

    def payload_digest(self, attempt):
        if attempt in TRANSMITTING:
            return payload_digest(self.payload(attempt))
        _check_attempt(attempt)
        return ''

    def declaration_digest(self, attempt):
        if attempt in TRANSMITTING:
            return declaration_digest(self.declaration(attempt))
        _check_attempt(attempt)
        return ''

    def load_args(self, attempt, session_id):
        """The flat typed declaration the native parser reconstructs and re-hashes."""
        if attempt not in TRANSMITTING:
            raise ValueError('only the normal, narrowband and pulse attempts load')
        _check_session_id(session_id)
        declaration = self.declaration(attempt)
        args = dict(study=STUDY, session_id=session_id, attempt=attempt,
                    scenario=self.scenario(attempt), samples=LOAD_SAMPLES,
                    bytes=LOAD_BYTES, format='s16_iq_le', direction='tx',
                    wrap=True)
        for field, name in _DECLARATION_FIELDS:
            args[field] = _flat(declaration[name])
        args['source_sha256'] = self.payload_digest(attempt)
        args['decl_sha256'] = self.declaration_digest(attempt)
        if args['source_sha256'] != NATIVE_PAYLOAD_SHA[attempt]:
            raise ValueError('the generator payload is not the reviewed payload')
        if args['decl_sha256'] != NATIVE_DECLARATION_SHA[attempt]:
            raise ValueError('the generator declaration is not the reviewed one')
        return args

    def capture_args(self, attempt, session_id):
        _check_attempt(attempt)
        _check_session_id(session_id)
        return dict(study=STUDY, session_id=session_id, attempt=attempt,
                    scenario=self.scenario(attempt), samples=CAPTURE_SAMPLES,
                    ratio=0, settle_wait_us=0)

    def accept_args(self, attempt, session_id, frame_id, timestamp_ns):
        _check_attempt(attempt)
        _check_session_id(session_id)
        if type(frame_id) is not int or frame_id < 0:
            raise ValueError('frame_id must be the native reply identifier')
        if type(timestamp_ns) is not int or timestamp_ns <= 0:
            raise ValueError('timestamp_ns must be the native reply timestamp')
        # The native parser compares this field as a string.
        return dict(study=STUDY, session_id=session_id, attempt=attempt,
                    frame_id=frame_id, timestamp_ns=str(timestamp_ns),
                    accepted=True)

    def abort_args(self, session_id):
        _check_session_id(session_id)
        return dict(study=STUDY, session_id=session_id)

    def _config(self, attempt):
        """The existing generator's configuration; only kind varies."""
        if attempt not in TRANSMITTING:
            raise ValueError('OFF attempts have no TX payload')
        return ScenarioConfig(kind=self.scenario(attempt))


# short wire key -> the generator's declaration field it must equal.  The native
# parser checks each typed field and then re-hashes the reconstructed
# declaration, so a digest alone would not be declaration validation.
_DECLARATION_FIELDS = (
    ('v', 'version'), ('truth', 'truth'), ('shape', 'shaping'),
    ('formula', 'formula'), ('quant', 'quantization'), ('a', 'amplitude'),
    ('ps', 'period_samples'), ('sy', 'period_symbols'), ('max', 'physical_max_code'),
    ('prbs', 'prbs7_seed'), ('seed', 'seed'), ('fs', 'sample_rate_hz'),
    ('rs', 'symbol_rate_hz'), ('pa', 'pulse_amplitude'),
    ('pp', 'pulse_period_samples'), ('ph', 'pulse_phase_rad'),
    ('start', 'pulse_start'), ('width', 'pulse_width'), ('ta', 'tone_amplitude'),
    ('bin', 'tone_bin'), ('fa', 'tone_frequency_actual_hz'),
    ('fr', 'tone_frequency_requested_hz'), ('tp', 'tone_phase_rad'))


def _flat(value):
    """The generator stores exact frequencies as floats; the wire is integral."""
    if type(value) is float:
        if value != int(value):
            raise ValueError('a non-integral declaration value has no wire form')
        return int(value)
    if type(value) in (str, int, bool):
        return value
    raise ValueError('unsupported declaration value type')


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise HeadroomEvidenceError('invalid research frame field: ' + name)
    return value


def _mapping(value, name):
    if type(value) is not dict:
        raise HeadroomEvidenceError('invalid research frame field: ' + name)
    return value


def _check_attempt(attempt):
    if type(attempt) is not int or not 1 <= attempt <= ATTEMPT_BUDGET:
        raise ValueError('the study has five attempts, so attempt must be 1..5')


def _check_session_id(session_id):
    if (type(session_id) is not str or len(session_id) != 32
            or any(c not in _HEX for c in session_id)):
        raise ValueError('session_id must be 32 lowercase hex characters')


# The plan speaks the request vocabulary (`lo_hz`); the native readback reports
# the receiver and transmitter sides separately, so every request value has to
# be found on BOTH paths -- an asymmetric readback is exactly the failure this
# catches (the TX and RX attenuator are separate registers on this part).
_READBACK_PATHS = {
    'lo_hz': ('rx_lo_hz', 'tx_lo_hz'),
    'sample_rate_hz': ('rx_sample_rate_hz', 'tx_sample_rate_hz'),
    'rx_bandwidth_hz': ('rx_bandwidth_hz',),
    'tx_bandwidth_hz': ('tx_bandwidth_hz',),
    'attenuation_mdB': ('attenuation_mdB',),
    'phy_mode_1r1t': ('phy_mode_1r1t',),
}


# `RF_REG_SRC_CFG` (0x14) and `RF_REG_CAP_CFG` (0x24) are named-bit words, not
# "the whole word must be zero" (platform_rf.h:50-64).  Bit 0 of each is the
# CONTROL bit -- it selects the TX source or arms the capture, and it alone
# decides whether a live RF path exists.  Bit 2 of each is the configuration
# COMMIT EPOCH: the RTL commits on the EDGE of that bit (rf_tx_source.v:144,
# rf_sample_capture.v:77), so `commit_toggle` (rf_core.c:467-470) leaves it at
# either level after a successful configure (rf_core.c:1782, :1834), and the
# reviewed release clears only the control bits (rf_core.c:1507-1516,
# :1562-1566) -- so the legal post-release readbacks are `cap_cfg == 4` (off
# frames) and `src_cfg == 4` for a wrap=false load, or `src_cfg == 6` for the
# wrap=true load this study itself requests.  The native fixed-domain predicate
# reads the same named bits and tests only bit 0 of each (rf_daemon.c:1992-2006).
#
# Bit 1 must be read per register, because the two words do NOT mean the same
# thing and the hardware does not leave them in the same place:
#
#   * `src_cfg` bit 1 is `SMP_WRAP`, the TX sample-buffer mode.  HEADROOM asks for
#     `wrap=true` on every transmitting attempt (rf_daemon.c:2079), that path
#     writes the bit unconditionally (rf_core.c:1780), and the release keeps it,
#     so `src_cfg == 6` is the faithful record of the study's own request and not
#     an activity.  It cannot select, arm or run anything while bit 0 is clear:
#     the RTL uses the register bit only through `wrap_lat`, which the reader
#     receives as `u_reader.wrap`, and the reader's `enable` is `src_br` -- the
#     synchronised bit 0 (rf_tx_source.v:130-176).  Allowing it therefore does
#     not widen the safety boundary.  2026-10-17: refusing it is what stopped
#     attempt 3 at EVIDENCE:2 on a frame the native gate had already accepted.
#
#   * `cap_cfg` bit 1 is `CAP_CONT`, the CONTINUOUS-CAPTURE mode, and it stays
#     refused.  A research frame must be ONE finite 4096-sample capture, and
#     `rf_core_prepare_rx(..., false)` (rf_daemon.c:2083) clears the bit before
#     every arm, so a 1 there would say the frame was not the fixed transaction
#     at all -- and that is a property the predicate can still prove.
#
# The fixed domain is therefore `src_cfg & ~0x6 == 0` and `cap_cfg & ~0x4 == 0`:
# the control bits are 0, bit 1 is allowed only where it is inert configuration,
# the commit epoch may hold either level, and bits above bit 2 are refused
# because neither register defines them.
_CONFIG_WORDS = (
    ('src_cfg', 0x7, ((0x1, 'SRC_SEL (bit0)'),)),
    ('cap_cfg', 0x7, ((0x1, 'CAP_ARM (bit0)'), (0x2, 'CAP_CONT (bit1)'))),
)


def _config_word_error(word, label, path, defined, forbidden):
    """Why a config word is outside the fixed domain, naming the bit at fault.

    The order is the order of consequence: the control bit that would arm or
    select something first, then -- for the capture word only -- the bit that
    would keep the capture running, then anything the register does not define.
    The commit epoch is deliberately in neither list: it is the one bit that may
    hold either level, and it never excuses another bit.  The TX wrap bit is in
    neither list either, because the release keeps it and the study sets it.
    """
    for mask, name in forbidden:
        if word & mask:
            return '%s.pl.%s has %s set' % (label, path, name)
    undefined = word & ~defined
    if undefined:
        return '%s.pl.%s has undefined bits set (0x%x)' % (label, path, undefined)
    return None


def _fixed_rf_error(readback, label):
    """The readback must be the same fixed RF the plan asked for, not a claim."""
    for key, value in FIXED_RF:
        for path in _READBACK_PATHS.get(key, ()):
            if type(readback.get(path)) is not int or readback[path] != value:
                return '%s.%s is not the fixed research value' % (label, path)
    for key in ('tx1_attenuation_mdB', 'tx2_attenuation_mdB'):
        if type(readback.get(key)) is not int or readback[key] != RESEARCH_ATTENUATION_MDB:
            return '%s.%s is not 46000 mdB' % (label, key)
    if type(readback.get('hardware_verified')) is not bool:
        return '%s.hardware_verified is not a boolean' % label
    if type(readback.get('tx_fir_enabled')) is not int or readback['tx_fir_enabled'] != 1:
        return '%s.tx_fir_enabled is not 1' % label
    if type(readback.get('rx_fir_enabled')) is not int or readback['rx_fir_enabled'] not in (0, 1):
        return '%s.rx_fir_enabled is out of range' % label
    for key in ('tx_fir_config_verified', 'rx_fir_config_verified'):
        if readback.get(key) is not True:
            return '%s.%s was not verified' % (label, key)
    pl = _mapping(readback.get('pl'), label + '.pl')
    for key in ('phy_mode','idelay_en','idelay_tap','src_cfg','cap_cfg'):
        if type(pl.get(key)) is not int:
            return '%s.pl.%s is not an integer' % (label,key)
    if pl.get('phy_mode') != 0:
        return '%s.pl.phy_mode is not the 2R2T research mode' % label
    # The request says idelay_en=1; the PL register is a 7-bit enable field.
    if pl.get('idelay_en') != 127:
        return '%s.pl.idelay_en is not the fixed enable value' % label
    if pl.get('idelay_tap') != 29:
        return '%s.pl.idelay_tap is not 29' % label
    for path, defined, forbidden in _CONFIG_WORDS:
        why = _config_word_error(pl[path], label, path, defined, forbidden)
        if why is not None:
            return why
    if (type(pl.get('cap_ratio')) is not int or type(pl.get('cap_len')) is not int
            or not 0 <= pl['cap_ratio'] <= 0xffff or not 0 <= pl['cap_len'] <= 4096):
        return label + '.pl invalid capture geometry'
    if label == 'actual_rf_after' and (pl['cap_ratio'] != 0 or pl['cap_len'] != CAPTURE_SAMPLES):
        return label + '.pl capture geometry mismatch'
    return None


def check_research_frame(plan, session_id, attempt, frame, require_hardware=True):
    """Read one native reply back as evidence; never as the arguments sent.

    Every identity is taken from the frame the daemon returned: the operation's
    own request id, the session, the attempt, the scenario, both declared
    digests and the payload digest.  The rail count is recounted from the bytes
    rather than trusted, and the surrounding RF readback must be the fixed
    research domain.  A physical run requires hardware attestation; a host run
    passes `require_hardware=False` and is recorded as simulated, never as
    attestation.
    """
    if not isinstance(frame, ResearchFrame):
        raise HeadroomEvidenceError('expected a research frame')
    if frame.transport is not None:
        transport = _mapping(frame.transport, 'transport')
        if transport.get('complete') is not True or transport.get('error') is not None:
            raise HeadroomEvidenceError('research transport did not complete within its deadline')
    _check_attempt(attempt)
    header = _mapping(frame.header, 'header')
    if header.get('type') != 'research_iq_frame':
        raise HeadroomEvidenceError('the reply is not a research IQ frame')
    if header.get('direction') != 'rx' or header.get('format') != 's16_iq_le':
        raise HeadroomEvidenceError('invalid research frame direction or format')
    if _integer(header.get('id'), 'id', 0, 0x7FFFFFFF) != frame.request_id:
        raise HeadroomEvidenceError('the reply id is not the request id')
    if _integer(header.get('samples'), 'samples', 1, CAPTURE_SAMPLES) != CAPTURE_SAMPLES:
        raise HeadroomEvidenceError('the research frame is not 4096 samples')
    declared = _integer(header.get('bytes'), 'bytes', 0, MAX_IQ_BYTES)
    if declared != IQ_BYTES:
        raise HeadroomEvidenceError('the research frame is not 16384 bytes')
    if _integer(header.get('sample_rate_hz'), 'sample_rate_hz', 1, 0xFFFFFFFF) != SAMPLE_RATE_HZ:
        raise HeadroomEvidenceError('the research frame is not at 60 MHz')
    if _integer(header.get('ratio'), 'ratio', 0, 0xFFFF) != 0:
        raise HeadroomEvidenceError('the research frame is not undecimated')
    timestamp = _integer(header.get('timestamp_ns'), 'timestamp_ns', 1,
                         0xFFFFFFFFFFFFFFFF)
    if type(frame.payload) is not bytes or len(frame.payload) != IQ_BYTES:
        raise HeadroomEvidenceError('the research frame payload is not 16384 bytes')

    head = _mapping(header.get('headroom'), 'headroom')
    if head.get('study') != STUDY:
        raise HeadroomEvidenceError('the frame belongs to another study')
    if head.get('session_id') != session_id:
        raise HeadroomEvidenceError('the frame belongs to another session')
    if _integer(head.get('attempt'), 'attempt', 1, ATTEMPT_BUDGET) != attempt:
        raise HeadroomEvidenceError('the frame answers another attempt')
    if head.get('scenario') != plan.scenario(attempt):
        raise HeadroomEvidenceError('the frame carries another scenario')
    if _integer(head.get('capture_request_id'), 'capture_request_id', 0,
                0x7FFFFFFF) != frame.request_id:
        raise HeadroomEvidenceError('the frame is not from its own capture request')

    transmitting = plan.is_transmitting(attempt)
    if head.get('source_enabled') is not transmitting:
        raise HeadroomEvidenceError('the TX source state is not the fixed one')
    if _integer(head.get('settle_wait_us'), 'settle_wait_us', 0, 0) != 0:
        raise HeadroomEvidenceError('the study fixes the settle wait at zero')
    expected_source = plan.payload_digest(attempt) if transmitting else ''
    expected_declaration = plan.declaration_digest(attempt) if transmitting else ''
    if head.get('source_sha256') != expected_source:
        raise HeadroomEvidenceError('the frame names another source payload')
    if head.get('declaration_sha256') != expected_declaration:
        raise HeadroomEvidenceError('the frame names another declaration')
    digest = payload_digest(frame.payload)
    if head.get('iq_sha256') != digest:
        raise HeadroomEvidenceError('the frame payload is not the frame hash')

    rails = sum(1 for (value,) in __import__('struct').iter_unpack('<h', frame.payload)
                if value in (-2048, 2047))
    if _integer(head.get('rail_count'), 'rail_count', 0, 2 * CAPTURE_SAMPLES) != rails:
        raise HeadroomEvidenceError('the declared rail count is not the measured one')
    terminal = head.get('terminal')
    if terminal not in TERMINALS:
        raise HeadroomEvidenceError('invalid research terminal state')
    if (terminal == 'CLIPPED') is not (rails > 0):
        raise HeadroomEvidenceError('the terminal state does not match the rails')

    before = _mapping(head.get('actual_rf_before'), 'actual_rf_before')
    after = _mapping(head.get('actual_rf_after'), 'actual_rf_after')
    for label, readback in (('actual_rf_before', before), ('actual_rf_after', after)):
        why = _fixed_rf_error(readback, label)
        if why is not None:
            raise HeadroomEvidenceError(why)
    verified = after['hardware_verified']
    if require_hardware and (verified is not True or before['hardware_verified'] is not True):
        raise HeadroomEvidenceError(
            'the research frame was not taken on attested hardware')

    timing = head.get('timing_ns')
    if type(timing) is not list or len(timing) != len(TIMING_ORDER):
        raise HeadroomEvidenceError('the research timing brackets are incomplete')
    previous = 0
    for value in timing:
        if type(value) is not int or value < previous:
            raise HeadroomEvidenceError('the research timing is not monotonic')
        previous = value
    # The frame is stamped after the teardown it reports, so a stamp before the
    # last bracket is not a slow clock, it is a frame from somewhere else.
    if timestamp < previous:
        raise HeadroomEvidenceError('the frame timestamp precedes its own teardown')
    # The four appended keys are read from `headroom`, which is where the daemon
    # writes them (see `APPENDED_EVIDENCE`).  Reading them one level up was the
    # 2026-10-16 defect #2: it refused every real frame at "the timing scope is
    # not the reviewed one", and the host fixture carried the same wrong shape,
    # so no test could see it.  A second copy on the frame object is refused
    # rather than preferred -- nothing writes one, and two layers would make the
    # reviewed value depend on which layer a reader happens to pick.
    for key in APPENDED_EVIDENCE:
        if key in header:
            raise HeadroomEvidenceError(
                'the frame carries ' + key + ' outside headroom')
    if head.get('timing_scope') != TIMING_SCOPE:
        raise HeadroomEvidenceError('the timing scope is not the reviewed one')
    order = head.get('timing_order')
    if type(order) is not list or list(order) != list(TIMING_ORDER):
        raise HeadroomEvidenceError('the timing order is not the reviewed one')

    native_error = _mapping(head.get('native_error'), 'native_error')
    closure = _mapping(head.get('closure'), 'closure')
    codes = ('code','capture_code','release_code','post_readback_code','clock_code','close_code')
    if native_error.get('stage') not in ('NONE','CAPTURE_OR_CONFIG','RELEASE','POST_READBACK','CLOCK'):
        raise HeadroomEvidenceError('invalid native error stage')
    for key in codes:
        _integer(native_error.get(key), 'native_error.'+key, -5, 0)
    if type(closure.get('attempted')) is not bool or type(closure.get('confirmed')) is not bool:
        raise HeadroomEvidenceError('invalid native closure flags')
    if terminal == 'AWAIT_ACCEPT':
        if (native_error['stage'] != 'NONE' or any(native_error[k] != 0 for k in codes)
                or closure != {'attempted':False, 'confirmed':False}):
            raise HeadroomEvidenceError('AWAIT_ACCEPT contradicts native failure or closure')
    else:
        for key in ('release_code','shutdown_code'):
            _integer(closure.get(key), 'closure.'+key, -5, 0)
        if closure.get('attempted') is not True or any(type(closure.get(k)) is not bool for k in ('tx_active','rx_busy')):
            raise HeadroomEvidenceError('terminal closure incomplete')
        if closure['confirmed'] is True and (closure['release_code'] or closure['shutdown_code']
                or closure['tx_active'] or closure['rx_busy']):
            raise HeadroomEvidenceError('closure contradicts active RF paths')
    return dict(attempt=attempt, scenario=head['scenario'],
                request_id=frame.request_id, timestamp_ns=timestamp,
                source_sha256=head['source_sha256'],
                declaration_sha256=head['declaration_sha256'],
                iq_sha256=digest, rail_count=rails, terminal=terminal,
                source_enabled=transmitting, timing_ns=list(timing),
                actual_rf_before=before, actual_rf_after=after,
                hardware_verified=verified, simulated=verified is not True,
                native_error=native_error, closure=closure, payload=frame.payload)


class HeadroomController:
    """Serialise one study over one retained connection; never retry.

    The client owns the wire.  This object decides the order, refuses to start
    without live capability, records each attempt's intent before it is sent and
    its raw frame before it is analysed, and stops the study permanently at the
    first refusal, rail, invalid frame, OFF violation or cancellation.
    """

    def __init__(self, client, plan=None, recorder=None, off_guard=None,
                 cancelled=None, require_hardware=True, replayer=None):
        self.client = client
        self.plan = plan or HeadroomPlan()
        self.recorder = recorder or _NullRecorder()
        self.off_guard = off_guard
        self.cancelled = cancelled or _never
        self.require_hardware = require_hardware
        # The saved-IQ replay.  Optional because a caller may only want the
        # transaction; the production worker always supplies one, because a
        # study whose frames were never read back is not evidence.
        self.replayer = replayer

    def run(self, session_id, cable_confirmed=False, token=None):
        """Run the five attempts; return the study receipt.

        Raises HeadroomStopped before anything is sent when the live daemon does
        not announce this contract -- an unavailable contract is a refusal, not
        a study that ran and failed.
        """
        _check_session_id(session_id)
        if cable_confirmed is not True or type(token) is not str or not token:
            raise HeadroomStopped('explicit cable confirmation and control token required')
        if self.require_hardware and (not isinstance(self.recorder, HeadroomRecorder)
                or self.replayer is None or self.off_guard is None):
            raise HeadroomStopped('physical research requires durable recorder, replay and OFF guard')
        self._crash_receipt = None
        self._write_errors = []
        self._announced_attempt = self._decided_attempt = None
        try:
            with self.client.headroom_lease():
                if not capability_available(self.client.headroom_capability()):
                    raise HeadroomStopped('live research capability or idle preflight refused')
                if self.cancelled():
                    self.recorder.terminal('CANCELLED', {'before_arm':True})
                    return dict(completed=False, stopped_at='CANCELLED', attempts=0,
                                frames=[], disarmed=True, close_ack=None)
                self.client.headroom_arm(token)
                return self._study(session_id, cable_confirmed)
        except BaseException as exc:
            # The record that says "this study is over" is written even when the
            # study died for a reason nobody modelled: a caller-supplied
            # predicate or a recorder that raised.  Losing it exactly when the
            # study crashed is the worst time to lose it.
            detail = self._crash_receipt or dict(completed=False, stopped_at='CRASHED',
                attempts=0, frames=[], disarmed=False, close_ack=None, session_id=session_id)
            detail.update(error=type(exc).__name__, detail=str(exc), write_errors=self._write_errors)
            exc.headroom_receipt = detail
            try:
                self.recorder.terminal('CRASHED', detail)
            except BaseException as write_exc:
                # Preserve the original crash and expose the failed final write
                # to the caller; a missing receipt never becomes a completed run.
                exc.evidence_write_error = str(write_exc)
            raise

    def _study(self, session_id, cable_confirmed):
        plan = self.plan
        frames, stopped_at, reason = [], None, None
        self._seen_ids, self._seen_stamps = set(), set()
        self._completion_ack = None
        try:
            begin_args = plan.begin_args(session_id, cable_confirmed)
            if isinstance(self.recorder, HeadroomRecorder):
                self.recorder.operation_intent('begin', begin_args)
            try:
                if self.cancelled(): raise HeadroomStopped('CANCELLED')
                ack = self.client.headroom_begin(begin_args)
                if (type(ack) is not dict or ack.get('study') != STUDY
                        or ack.get('session_id') != session_id
                        or type(ack.get('initializations')) is not int or ack['initializations'] != 1
                        or type(ack.get('attempt_budget')) is not int or ack['attempt_budget'] != 5):
                    raise HeadroomStopped('invalid headroom.begin acknowledgment')
                why = _fixed_rf_error(_mapping(ack.get('actual_rf'),'begin.actual_rf'), 'begin.actual_rf')
                if why or (self.require_hardware and ack['actual_rf'].get('hardware_verified') is not True):
                    raise HeadroomStopped(why or 'begin RF was not hardware verified')
            except Exception as exc:
                stopped_at, reason = 'headroom.begin', str(exc)
            else:
                for attempt in range(1, plan.attempts + 1):
                    outcome = self._attempt(session_id, attempt, frames)
                    if outcome is not None:
                        stopped_at, reason = outcome
                        break
        except BaseException as exc:
            self._crash_receipt = dict(study=STUDY, session_id=session_id,
                completed=False, stopped_at='CRASHED', attempts=len(frames),frames=frames,
                failed_attempt=self._announced_attempt, reason=str(exc))
            raise
        finally:
            abort_ack, abort_error = None, None
            if self._completion_ack is None:
                # This also runs during an analysis/predicate crash.  Stop on
                # the original connection before releasing its lease; never
                # reconnect to try to recover a consumed session.
                try:
                    abort = self.client.headroom_abort(plan.abort_args(session_id))
                    abort_ack = abort
                    if (abort != {'aborted':True, 'armed':False}
                            or abort.get('aborted') is not True or abort.get('armed') is not False):
                        raise HeadroomStopped('abort acknowledgment unconfirmed')
                except Exception as exc:
                    abort_error = str(exc)
                    reason = (reason or '') + ' (abort: ' + str(exc) + ')'
            try:
                close_ack = self.client.headroom_closing_readback()
                disarmed = _closing_confirmed(close_ack)
                note = '' if disarmed else ' (RF closing readback unconfirmed)'
            except Exception as exc:
                disarmed, close_ack, note = False, {'error':str(exc)}, ' (RF closing readback unavailable)'
            if self._crash_receipt is not None:
                self._crash_receipt.update(disarmed=disarmed,close_ack=close_ack,
                    abort_ack=abort_ack,abort_error=abort_error)
        reason = (reason or '') + note
        completed = (stopped_at is None and len(frames) == plan.attempts
                     and self._completion_ack == {'completed':True,'attempts':5,'armed':False}
                     and disarmed)
        if not completed and stopped_at is None:
            stopped_at = 'CLOSE_UNCONFIRMED'
        receipt = dict(study=STUDY, session_id=session_id, completed=completed,
                       attempts=len(frames), frames=frames,
                       stopped_at=stopped_at, reason=reason, disarmed=disarmed,
                       close_ack=close_ack, hardware_verified=(
                           bool(frames) and frames[-1]['hardware_verified'] is True))
        self.recorder.terminal('COMPLETED' if completed else (stopped_at or 'STOPPED'),
                               receipt)
        return receipt

    def _attempt(self, session_id, attempt, frames):
        try:
            return self._attempt_body(session_id, attempt, frames)
        except BaseException as exc:
            if self._announced_attempt == attempt and self._decided_attempt != attempt:
                try:
                    self._record('decision',attempt,False,'CRASHED: '+str(exc))
                except BaseException:
                    pass  # The terminal includes the explicit evidence write failure.
            raise

    def _record(self, name, *args):
        if name == 'decision':
            self._decided_attempt = args[0]
        try:
            value = getattr(self.recorder,name)(*args)
        except BaseException as exc:
            self._write_errors.append(dict(operation=name,error=type(exc).__name__,detail=str(exc)))
            raise
        if name == 'intent':
            self._announced_attempt = args[0]
        return value

    def _attempt_body(self, session_id, attempt, frames):
        """Run one attempt; return (stopped_at, reason) or None to continue.

        Every attempt that is announced gets exactly one decision, whichever way
        it ends.  An announced attempt with no decision is the shape a half-read
        study would take, and a half-read study must not look like a clean one.
        """
        plan = self.plan
        if self.cancelled():
            self._record('decision', attempt, False, 'CANCELLED')
            return 'CANCELLED', 'the operator cancelled the study'
        transmitting = plan.is_transmitting(attempt)
        self._record('intent', attempt, plan.scenario(attempt),
                             dict(capture=plan.capture_args(attempt, session_id),
                                  load=plan.load_args(attempt,session_id) if transmitting else None))
        if transmitting:
            try:
                if self.cancelled(): raise HeadroomStopped('CANCELLED')
                ack = self.client.headroom_load(plan.load_args(attempt, session_id),
                                               plan.payload(attempt))
                if (ack != {'loaded':True,'samples':LOAD_SAMPLES}
                        or ack.get('loaded') is not True or type(ack.get('samples')) is not int):
                    raise HeadroomStopped('invalid load acknowledgment')
            except Exception as exc:
                self._record('decision', attempt, False, str(exc))
                return ('CANCELLED' if str(exc) == 'CANCELLED' else
                        'headroom.load:%d' % attempt), str(exc)
        try:
            if self.cancelled(): raise HeadroomStopped('CANCELLED')
            frame = self.client.headroom_capture(plan.capture_args(attempt, session_id))
        except Exception as exc:
            # The original bytes never arrived, and that is itself evidence: a
            # study whose frame is missing must record the gap explicitly rather
            # than leave the attempt looking simply unfinished.
            self._record('frame', attempt, None)
            self._record('decision', attempt, False, str(exc))
            return ('CANCELLED' if str(exc) == 'CANCELLED' else
                    'headroom.capture:%d' % attempt), str(exc)
        # The original bytes are recorded before anything reads them, so a frame
        # that fails its checks is still preserved as evidence.
        self._record('frame', attempt, frame)
        try:
            evidence = check_research_frame(plan, session_id, attempt, frame,
                                            require_hardware=self.require_hardware)
        except HeadroomEvidenceError as exc:
            self._record('decision', attempt, False, str(exc))
            return 'EVIDENCE:%d' % attempt, str(exc)
        if evidence['request_id'] in self._seen_ids or evidence['timestamp_ns'] in self._seen_stamps:
            self._record('decision', attempt, False, 'replayed capture identity')
            return 'EVIDENCE:%d' % attempt, 'replayed capture identity'
        self._seen_ids.add(evidence['request_id'])
        self._seen_stamps.add(evidence['timestamp_ns'])
        if evidence['terminal'] != 'AWAIT_ACCEPT':
            self._record('decision', attempt, False, evidence['terminal'])
            return evidence['terminal'], 'the frame reported ' + evidence['terminal']
        self._record('analysis', attempt, evidence)
        summary = self.replayer(attempt, evidence) if self.replayer is not None else None
        if summary is not None:
            self._record('replay', attempt, summary)
        if not transmitting and self.off_guard is not None:
            try:
                guarded = dict(evidence, replay=summary)
                self.off_guard(attempt, guarded)
            except HeadroomStopped as exc:
                self._record('decision', attempt, False, str(exc))
                return 'OFF_EVIDENCE', str(exc)
        try:
            if self.cancelled(): raise HeadroomStopped('CANCELLED')
            ack = self.client.headroom_accept(plan.accept_args(
                attempt, session_id, frame.request_id, evidence['timestamp_ns']))
            expected = ({'completed':True,'attempts':5,'armed':False}
                        if attempt == 5 else {'accepted':True})
            valid_flags = (ack.get('completed') is True and ack.get('armed') is False
                           and type(ack.get('attempts')) is int) if attempt == 5 else ack.get('accepted') is True
            if ack != expected or not valid_flags:
                raise HeadroomStopped('invalid native acceptance acknowledgment')
            if attempt == 5: self._completion_ack = ack
        except Exception as exc:
            self._record('decision', attempt, False, str(exc))
            return ('CANCELLED' if str(exc) == 'CANCELLED' else
                    'headroom.accept:%d' % attempt), str(exc)
        self._record('decision', attempt, True, None)
        frames.append(evidence)
        return None


def _closing_confirmed(value):
    """Completion requires all live idle fields, not only an aggregate flag."""
    if type(value) is not dict or value.get('confirmed') is not True:
        return False
    state, pl = value.get('state'), value.get('pl')
    if type(state) is not dict or type(pl) is not dict:
        return False
    tx, rx = pl.get('tx'), pl.get('rx')
    return (value.get('armed') is False and state.get('state') == 'OFFLINE'
            and state.get('pending') is False and state.get('lease_held') is True
            and state.get('pending_op') is None
            and pl.get('io_ok') is True and type(tx) is dict and type(rx) is dict
            and tx.get('source_selected') is False and rx.get('armed') is False)


def _read_armed(client):
    """The daemon's live armed state, or `(None, why it could not be read)`."""
    try:
        return client.control_armed(), None
    except Exception as exc:
        return None, str(exc)


def _confirm_disarmed(client):
    """Ask the daemon whether it is unarmed, and believe only its answer.

    `control.disarm` returning ok is NOT evidence that the part is unarmed: the
    daemon's own terminal close disarms first (`headroom_close` calls
    `rf_daemon_disarm`), after which `control.disarm` is not even advertised, and
    `op_control_disarm` ignores the return of the disarm it performs.  So the
    acknowledgment is the daemon's live `armed` state; the disarm call itself is
    made only when that state says the daemon is still armed, i.e. for the study
    that stopped without a native close.
    """
    disarm_reply, disarm_error = None, None
    armed, armed_error = _read_armed(client)
    if armed is not False:
        try:
            disarm_reply = client.control_disarm()
        except Exception as exc:
            disarm_error = str(exc)
        armed, armed_error = _read_armed(client)
    note = ''
    if disarm_error:
        note += ' (disarm: ' + disarm_error + ')'
    if armed_error:
        note += ' (armed readback: ' + armed_error + ')'
    elif armed is not False:
        note += ' (the daemon did not report itself unarmed)'
    return (armed is False,
            dict(disarm=disarm_reply, disarm_error=disarm_error,
                 armed=armed, armed_error=armed_error),
            note)


class _NullRecorder:
    def intent(self, attempt, scenario, args):
        pass

    def frame(self, attempt, frame):
        pass

    def analysis(self, attempt, summary):
        pass

    def replay(self, attempt, summary):
        pass

    def decision(self, attempt, accepted, detail):
        pass

    def terminal(self, outcome, detail):
        pass


class HeadroomRecorder:
    """Write one study's evidence to disk; the terminal receipt is the commit.

    Research frames are NOT teaching captures and are deliberately not written
    by `rf_experiment_record`: that record's identity is a BPSK envelope and a
    constellation, which a research frame does not have until it is replayed.
    What is kept here is the primary evidence -- the intent before the request
    is sent, the header and the raw bytes before anything reads them, the replay
    summary, the decision, and finally a receipt.  Nothing is readable as a
    record until that receipt lands, so a study interrupted mid-write leaves an
    obviously incomplete directory rather than a plausible one.

    A frame that never arrived is recorded too (`frame(None)`) plus an explicit
    `iq_available` flag, because "the original bytes are missing" is itself a
    result of a 46 dB headroom study and must not look like "not reached yet".
    """

    SCHEMA = 1

    def __init__(self, root_dir, session_id):
        _check_session_id(session_id)
        self.root = Path(root_dir) / session_id
        self.session_id = session_id
        self.root.mkdir(parents=True, exist_ok=False)

    def _attempt_dir(self, attempt):
        _check_attempt(attempt)
        return self.root / ('attempt-%d' % attempt)

    def _write(self, attempt, name, payload):
        folder = self._attempt_dir(attempt)
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / (name + '.partial')
        with partial.open('wb') as stream:
            stream.write(payload)
        partial.replace(folder / name)
        return digest_of(payload)

    def _document(self, attempt, name, value):
        data = json.dumps(value, sort_keys=True, indent=2, allow_nan=False,
                          default=str).encode('utf-8')
        self._write(attempt, name, data)

    def intent(self, attempt, scenario, args):
        self._document(attempt, 'intent.json',
                       dict(schema_version=self.SCHEMA, attempt=attempt,
                            scenario=scenario, sent_args=args))

    def frame(self, attempt, frame):
        if frame is None:
            self._document(attempt, 'header.json',
                           dict(schema_version=self.SCHEMA, attempt=attempt,
                                iq_available=False,
                                detail='the original IQ bytes were not received'))
            return
        # The header is written first and the bytes second, both before any
        # analysis, so a crash between them leaves the smaller claim on disk.
        self._document(attempt, 'header.json',
                       dict(schema_version=self.SCHEMA, attempt=attempt,
                            iq_available=bool(frame.payload), request_id=frame.request_id,
                            header=frame.header, transport=frame.transport,
                            received_bytes=len(frame.payload),
                            complete=len(frame.payload)==IQ_BYTES))
        if frame.payload:
            self._write(attempt, 'frame.iq', frame.payload)

    def operation_intent(self, name, args):
        data=json.dumps(dict(op='headroom.'+name,args=args),sort_keys=True,
                        allow_nan=False).encode('utf-8')
        (self.root / (name+'-intent.json')).write_bytes(data)

    def analysis(self, attempt, summary):
        self._document(attempt, 'analysis.json',
                       dict(schema_version=self.SCHEMA, attempt=attempt,
                            summary=_summarisable(summary)))

    def replay(self, attempt, summary):
        """The saved-IQ replay of this attempt, written before its decision."""
        self._document(attempt, 'replay.json',
                       dict(schema_version=self.SCHEMA, attempt=attempt,
                            summary=_summarisable(summary)))

    def decision(self, attempt, accepted, detail):
        self._document(attempt, 'decision.json',
                       dict(schema_version=self.SCHEMA, attempt=attempt,
                            accepted=bool(accepted), detail=detail))

    def terminal(self, outcome, detail):
        receipt = dict(schema_version=self.SCHEMA, study=STUDY,
                       session_id=self.session_id, outcome=outcome,
                       detail=_summarisable(detail),
                       attempts=sorted(p.name for p in self.root.iterdir()
                                       if p.is_dir()))
        data = json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False,
                          default=str).encode('utf-8')
        partial = self.root / 'receipt.json.partial'
        with partial.open('wb') as stream:
            stream.write(data)
        partial.replace(self.root / 'receipt.json')
        return digest_of(data)


def _summarisable(value):
    """Evidence documents must survive json; bytes become a digest, not a blob."""
    if isinstance(value, bytes):
        return dict(bytes=len(value), sha256=digest_of(value))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # A frozen result (the replay summary, an envelope) is a document, not a
        # repr: `str(value)` would persist something no reader can parse back.
        return {str(key): _summarisable(item)
                for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _summarisable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_summarisable(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    return str(value)


def digest_of(data):
    return hashlib.sha256(data).hexdigest()


def read_study(directory):
    """Read a study back; the receipt is the commit marker, so it must exist."""
    root = Path(directory)
    receipt_path = root / 'receipt.json'
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('the study has no final receipt, so it is not a record')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('schema_version') != HeadroomRecorder.SCHEMA:
        raise ValueError('unknown study record schema')
    attempts = {}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not folder.name.startswith('attempt-'):
            continue
        files = {}
        for name in ('intent.json', 'header.json', 'analysis.json', 'replay.json',
                     'decision.json', 'frame.iq'):
            path = folder / name
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if name.endswith('.iq'):
                files['frame.iq'] = data
            else:
                files[name] = json.loads(data.decode('utf-8'))
        attempts[int(folder.name.split('-', 1)[1])] = files
    return dict(receipt=receipt, attempts=attempts)


def _never():
    return False


def plan_field_names():
    """The plan carries no parameters; exposed so a test can state that."""
    return tuple(field.name for field in fields(HeadroomPlan))
