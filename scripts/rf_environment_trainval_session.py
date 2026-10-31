"""One independent TRAIN/VAL session: ten normal frames then one off frame.

Offline by construction.  This entry never opens a socket, never imports a board
client and never consumes an authorization or a nonce.  It defines the session
shape that the 46 dB TRAIN/VAL matrix needs (spec 2026-10-17 section 2.2) and the
download layout the calibration adapter consumes:

    <session-dir>/
      session-manifest.json          # 11 ordered slots, split, seed, flags, domain
      normal-01.iq .. normal-10.iq   # 16384 byte signed16 LE I/Q frames
      off-01.iq
      normal-01.json .. normal-10.json, off-01.json
                                     # board-shaped frame metadata (identity + demod)
      normal-01.environment.json ..  # environment sidecar (rf_config + declaration)
      receipt.json                   # per-artifact SHA-256 + frame bindings

Commands
--------
plan     emit a session plan: 11 ordered slots (10 normal + 1 off), no IQ at all.
fixture  write a complete deterministic offline session in the layout above.  The
         manifest and every artifact are marked fixture=true / collected=false, so
         a fixture can never be adapted as physical TRAIN/VAL evidence.
check    validate a materialized session directory: frame order, seed, identity,
         byte digests, receipt binding, flags.  Every refusal carries a stable code.
collect  the real acquisition route.  Without the explicit
         --authorize-board-collection flag it is refused by name
         (PHYSICAL_COLLECTION_NOT_AUTHORIZED) before anything is imported,
         connected or written.  With that flag, --candidate and --cable-confirmed,
         the host orchestrator scripts/rf_environment_trainval_collect.py runs ONE
         authorized 11-frame board session through the installed release and
         downloads it into <output>/<session-id>/ with the host download receipt.
         That route needs board-time authorization and a wiring confirmation; it
         has never been executed on a board (NOT_RUN there).

Session identity
----------------
* session ids are train-01, train-02, val-01; seeds rotate (1, 37) for TRAIN and
  (73,) for VAL, exactly as the calibration matrix declares (the calibration entry
  remains the single source of the matrix constants);
* frame ids/source ids are session-scoped, so two sessions can never share an
  identity; the ordered tags normal-01..normal-10 then off-01 are fixed;
* every frame carries a session block {session_id, split, seed}.  A frame from
  another split placed in this directory therefore contradicts the manifest and is
  refused by name instead of being silently relabelled.

No claim: a fixture session is synthetic data, not a capture; the matrix is still
NOT_RUN, and 51 frames (33 TRAIN/VAL + a separate 18 frame pilot) are not collected.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import rf_environment_calibration as CAL  # noqa: E402

PLAN_SCHEMA = 'rf-environment-session-plan-v1'
MANIFEST_SCHEMA = 'rf-environment-session-manifest-v1'
RECEIPT_SCHEMA = 'rf-environment-session-receipt-v1'
FRAME_SCHEMA = CAL.FRAME_SCHEMA

SPLITS = CAL.SPLITS
SEEDS = CAL.PLAN_SEEDS
NORMAL_PER_SESSION = CAL.PLAN_NORMAL_PER_SESSION
OFF_PER_SESSION = CAL.PLAN_OFF_PER_SESSION
FRAMES_PER_SESSION = CAL.PLAN_FRAMES_PER_SESSION
NORMAL_TAGS = tuple('normal-%02d' % (position + 1) for position in range(NORMAL_PER_SESSION))
OFF_TAGS = tuple('off-%02d' % (position + 1) for position in range(OFF_PER_SESSION))
FRAME_TAGS = NORMAL_TAGS + OFF_TAGS
# The global session index feeds the deterministic fixture offsets.  It is a
# property of the matrix, so a session generated on its own is identical to the
# same session generated inside the three-session campaign.
SESSION_GLOBAL_INDEX = {('train', 1): 0, ('train', 2): 1, ('val', 1): 2}
NON_FRAME_FILES = ('session-manifest.json', 'session-plan.json', 'receipt.json')
IQ_BYTES = CAL.IQ_BYTES
METADATA_MAX_BYTES = CAL.METADATA_MAX_BYTES

# ---------------------------------------------------------------- collection mode
# A session is collected under exactly one declared mode, and the mode is written
# into the manifest, the receipt and every frame's envelope so a reader never has
# to guess how a label was produced.
#
# * STANDARD_COLLECTION is the formal acceptance rule: a VAL normal frame must be
#   CLEAR.  It is the default, so a caller that forgets to declare the mode fails
#   closed instead of silently collecting against a pre-fit baseline.
# * CALIBRATION_COLLECTION is the explicit calibration-data mode the 2026-10-17
#   spec section J (2026-10-18) declares: the pre-fit (n=1 development) baseline labels a
#   perfectly qualified normal frame UNKNOWN / AMPLITUDE_OUTSIDE_RULE_SUPPORT
#   because its threshold was fitted from a single frame, and collecting the
#   TRAIN/VAL matrix would otherwise be impossible before the first fit.  The mode
#   accepts that pair and, under section K, PULSE / SPARSE_AMPLITUDE_OUTLIERS
#   for VAL normal frames, only after every quality gate in
#   scripts/rf_environment_trainval_board_session.evaluate_frame has already
#   passed (identity five-tuple, domain, version, declaration, readback, no rail,
#   reference sync, ber=0, compared bits).  The label is KEPT, never rewritten.
#   Other non-CLEAR VAL labels still stop the session; TRAIN rules and the final
#   criteria are unchanged: the baseline is
#   fitted from the 20 TRAIN normal frames only, and afterwards VAL must be 10/10
#   CLEAR with zero OFF false-CLEARs (scripts/rf_environment_calibration.py).
# The canonical constants live in the calibration entry (the base module of this
# import chain), so the session schema, the board runner, the host orchestrator and
# the fit all read one declaration.
STANDARD_COLLECTION = CAL.STANDARD_COLLECTION
CALIBRATION_COLLECTION = CAL.CALIBRATION_COLLECTION
COLLECTION_MODES = CAL.COLLECTION_MODES
PROVISIONAL_UNKNOWN_LABEL = CAL.PROVISIONAL_UNKNOWN_LABEL
PROVISIONAL_UNKNOWN_REASON = CAL.PROVISIONAL_UNKNOWN_REASON
PROVISIONAL_UNKNOWN = CAL.PROVISIONAL_UNKNOWN


def provisional_unknown(label, reason):
    """Is this exactly the one label pair a calibration collection may accept?"""
    return CAL.provisional_unknown(label, reason)


def check_collected_label(sidecar, collection_mode, frame_id):
    """The session-schema wording of the shared collected-label check."""
    return CAL.check_collected_label(sidecar, collection_mode, frame_id,
                                     code='SESSION_FRAME_LABEL_MODE_MISMATCH')


def check_collection_mode(mode, *, code='SESSION_COLLECTION_MODE'):
    """Validate one declared mode; None means "the pre-existing default"."""
    return CAL.declared_collection_mode(mode, code)


class SessionError(CAL.CalibrationError):
    """One named session refusal; callers match on the code, never on prose."""


def _require(condition, code, detail=''):
    if not condition:
        raise SessionError(code, detail)


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path):
    return _sha256_bytes(Path(path).read_bytes())


def _sha_literal(value):
    return CAL._sha_literal(value)


def _read_json(path, code, label):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise SessionError(code, '%s not found: %s' % (label, path))
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SessionError(code, '%s is not valid JSON: %s' % (label, exc))


def _write_json(path, value, *, overwrite=False):
    path = Path(path)
    if path.exists() and not overwrite:
        raise SessionError('OUTPUT_EXISTS', str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False))
    return path


def _write_bytes(path, payload, *, overwrite=False):
    path = Path(path)
    if path.exists() and not overwrite:
        raise SessionError('OUTPUT_EXISTS', str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def session_id(split, index):
    return '%s-%02d' % (split, index)


def _validate_session_arguments(split, index):
    if split not in SPLITS:
        raise SessionError('SESSION_SPLIT_NOT_ALLOWED',
                           'session split must be train or val, not %r' % (split,))
    seeds = SEEDS[split]
    if type(index) is not int or not 1 <= index <= len(seeds):
        raise SessionError('SESSION_INDEX_NOT_ALLOWED',
                           '%s has sessions 1..%d, not %r' % (split, len(seeds), index))
    return seeds[index - 1]


def plan_document(split, index):
    """The ordered 11-slot shape; a plan carries no IQ and claims no collection."""
    seed = _validate_session_arguments(split, index)
    identity = session_id(split, index)
    slots = []
    for position, tag in enumerate(FRAME_TAGS):
        scenario = 'normal' if position < NORMAL_PER_SESSION else 'off'
        slots.append(dict(
            position=position, tag=tag, frame_id='%s-%s' % (identity, tag),
            source_id='%s-src-%s' % (identity, tag), scenario=scenario,
            seed=seed, reference_prbs7_seed=seed, iq=tag + '.iq',
            metadata=tag + '.json', environment=tag + '.environment.json',
            iq_sha256=None, capture_id=None))
    return dict(
        schema=PLAN_SCHEMA, collected=False, fixture=False, physical=False,
        frame_count=FRAMES_PER_SESSION, normal_count=NORMAL_PER_SESSION,
        off_count=OFF_PER_SESSION, frame_order=list(FRAME_TAGS),
        session=dict(session_id=identity, split=split, index=index, seed=seed,
                     reference_prbs7_seed=seed),
        slots=slots, not_for_formal_acceptance=True,
        not_run=['physical session collection: NOT_RUN',
                 'board session close/readback: NOT_RUN',
                 'development pilot: NOT_RUN', 'formal TEST: NOT_RUN'],
        note='a plan carries no IQ; use fixture for an offline session, or the '
             'separately authorized board job for a physical one')


def check_plan(document):
    """A plan is never data: refuse any collected/fixture claim and matrix drift."""
    _require(type(document) is dict, 'SESSION_PLAN_SCHEMA', 'plan must be a JSON object')
    _require(document.get('schema') == PLAN_SCHEMA, 'SESSION_PLAN_SCHEMA',
             'expected schema %s' % PLAN_SCHEMA)
    _require(document.get('collected') is False and document.get('fixture') is False
             and document.get('physical') is False, 'SESSION_PLAN_NOT_A_PLAN',
             'a plan declares no collected/fixture/physical frames')
    _require(document.get('not_for_formal_acceptance') is True, 'SESSION_PLAN_NOT_A_PLAN',
             'a plan is not acceptance evidence')
    session = document.get('session') or {}
    expected = plan_document(session.get('split'), session.get('index'))
    for key in ('frame_order', 'slots', 'frame_count', 'normal_count', 'off_count',
                'session'):
        _require(document.get(key) == expected[key], 'SESSION_PLAN_MATRIX_MISMATCH',
                 'plan field %r differs from the declared 10 normal + 1 off shape' % key)
    _require(document['frame_count'] == FRAMES_PER_SESSION, 'SESSION_PLAN_MATRIX_MISMATCH',
             'a session holds exactly %d frames' % FRAMES_PER_SESSION)
    return dict(schema=PLAN_SCHEMA, collected=False, frame_count=FRAMES_PER_SESSION,
                normal_count=NORMAL_PER_SESSION, off_count=OFF_PER_SESSION,
                frame_order=list(FRAME_TAGS), session=dict(document['session']),
                not_run=list(document['not_run']))


def _fixture_codes(split, index, position, seed):
    """Deterministic synthetic codes; mirrors the calibration fixture recipe.

    The recipe is the same one the calibration entry uses for its own 3x11 fixture
    (PRBS7 BPSK at 20 samples/symbol, amplitude 60..63, seeded 1.5-code noise), so
    the fitted TRAIN envelope and the VAL/negative verdict stay meaningful offline.
    """
    import numpy as np
    from rf_iq_signal import prbs7_bits
    offset = SESSION_GLOBAL_INDEX[(split, index)]
    if position < NORMAL_PER_SESSION:
        bits = np.asarray(prbs7_bits(seed, 127))
        samples = np.arange(4096)
        symbols = (samples + (position * 3 + offset) % 20) // 20
        values = 1 - 2 * bits[symbols % 127]
        phase = (position % 6) * 0.4 - 1.0
        cfo = ((position % 5) - 2) * 1000.0
        amplitude = 60 + (position % 4)
        wave = amplitude * values * np.exp(
            1j * (phase + 2 * np.pi * cfo * samples / 60000000))
        rng = np.random.RandomState(seed * 1000 + position)
        wave = wave + 1.5 * (rng.normal(size=4096) + 1j * rng.normal(size=4096))
        codes = np.rint(np.column_stack((wave.real, wave.imag)))
    else:
        # Off is the same window with no source selected: receiver noise only, still
        # unique per frame so no two frames can share an IQ digest.
        off_rng = np.random.RandomState(seed * 1000 + position + 500)
        off = 1.5 * (off_rng.normal(size=4096) + 1j * off_rng.normal(size=4096))
        codes = np.rint(np.column_stack((off.real, off.imag)))
    return codes.astype('<i2').tobytes()


def _fixture_documents(split, index, position, tag, seed, domain, environment_version):
    """One board-shaped metadata document plus its environment sidecar."""
    from rf_bpsk_pipeline import DemodConfig, analyze_demod
    from rf_bpsk_receiver import BpskConfig
    from rf_experiment_scenarios import ScenarioConfig, generate_scenario
    from rf_environment import analyze_environment
    from rf_rx_types import RxIqFrame, RxReadback

    identity = session_id(split, index)
    offset = SESSION_GLOBAL_INDEX[(split, index)]
    scenario = 'normal' if position < NORMAL_PER_SESSION else 'off'
    payload = _fixture_codes(split, index, position, seed)
    digest = _sha256_bytes(payload)
    capture_id = 'fixture:%s:%s' % (identity, tag)
    completed = float(offset * 100 + position + 1)
    frame = RxIqFrame(
        capture_id=capture_id, payload=payload, samples=4096, sample_rate_hz=60000000,
        ratio=0, daemon_timestamp_ns=0, connection_generation=0, config_revision=0,
        request_id=position, release_id='offline-fixture', op='offline.fixture',
        physical=False, pre_readback=RxReadback(True),
        post_readback=RxReadback(True, rx_armed=False, tx_source_selected=False),
        completed_at_monotonic=completed)
    submitted = DemodConfig(BpskConfig('BPSK', 3000000, 'RECT', seed), 0, 1, 4096,
                            60000000)
    envelope = analyze_demod(frame, submitted)
    if scenario == 'normal':
        _require(envelope.result.reference_sync is True and envelope.result.ber == 0
                 and (envelope.result.compared_bits or 0) >= 32, 'FIXTURE_NOT_QUALIFIED',
                 'fixture normal frame %s is not reliable zero-BER BPSK' % tag)
    report = analyze_environment(frame, envelope, dict(domain), None)
    if scenario == 'off':
        _require(report.label != 'CLEAR', 'FIXTURE_NOT_QUALIFIED',
                 'fixture off frame %s is CLEAR, so it is not a negative control' % tag)
    declaration = generate_scenario(ScenarioConfig(
        kind=scenario, amplitude=60, prbs7_seed=seed)).declaration_dict()
    session_block = dict(session_id=identity, split=split, seed=seed)
    flags = dict(fixture=True, collected=False, physical=False)
    metadata = dict(
        capture_status=dict(state='done', physical=False, capture_id=capture_id,
                            code=None, reason='', close_confirmed=True,
                            cancelled=False, seq=position),
        capture_id=capture_id, samples=4096, bytes=16384, sample_rate_hz=60000000,
        ratio=0, decimation=1, output_fs_hz=60000000, op='offline.fixture',
        release_id='offline-fixture', connection_generation=0,
        config_revision=0, request_id=position, daemon_timestamp_ns=0,
        completed_at_monotonic=completed, pre_io_ok=True, post_io_ok=True,
        post_rx_armed=False, post_tx_source_selected=False, format='s16_iq_le',
        iq_sha256=digest,
        demod_result=dict(algorithm_version=envelope.algorithm_version,
                          result=envelope.result.to_dict(),
                          submitted=dict(config=dict(
                              mode='BPSK', symbol_rate_hz=3000000, shaping='RECT',
                              reference_prbs7_seed=seed),
                              revision=0, decim=1, rx_len=4096,
                              input_fs_hz=60000000)),
        session=dict(session_block), **dict(flags),
        origin='offline synthetic fixture frame; not a physical capture')
    sidecar = dict(
        environment=report.to_dict(),
        display_environment=dict(report.to_dict(), available=True, current=True),
        environment_version=environment_version,
        rf_config=dict(domain), scenario_declaration=declaration,
        # The fixture record is a placeholder: no metadata file digest is claimed.
        record=dict(record_id='fixture-record-%s-%s' % (identity, tag),
                    capture_id=capture_id, iq_sha256=digest, metadata_sha256=None,
                    bytes=16384, path=None, historical=False),
        iq_sha256=digest, capture_id=capture_id, release_id='offline-fixture',
        session=dict(session_block), **dict(flags),
        origin='offline synthetic fixture sidecar; not a physical capture')
    return payload, digest, metadata, sidecar


def build_fixture_session(split, index, output_dir, *, overwrite=False):
    """Materialize one deterministic offline session in the download layout."""
    seed = _validate_session_arguments(split, index)
    output_dir = Path(output_dir)
    if output_dir.exists() and not overwrite:
        raise SessionError('OUTPUT_EXISTS', str(output_dir))
    domain = dict(CAL._profile_domain(CAL.TEACHING_PROFILE)[0])
    from rf_environment import VERSION as environment_version
    if environment_version != 'rf-window-environment-v4':
        raise SessionError('ENVIRONMENT_VERSION_MISMATCH',
                           'this fixture builds the v4 calibration domain, got %r'
                           % (environment_version,))
    identity = session_id(split, index)
    entries, files = [], {}
    for position, tag in enumerate(FRAME_TAGS):
        payload, digest, metadata, sidecar = _fixture_documents(
            split, index, position, tag, seed, domain, environment_version)
        entries.append(dict(
            position=position, tag=tag, frame_id='%s-%s' % (identity, tag),
            source_id='%s-src-%s' % (identity, tag),
            scenario='normal' if position < NORMAL_PER_SESSION else 'off',
            seed=seed, reference_prbs7_seed=seed, iq=tag + '.iq',
            metadata=tag + '.json', environment=tag + '.environment.json',
            iq_sha256=digest, capture_id=metadata['capture_id']))
        files[tag + '.iq'] = payload
        files[tag + '.json'] = metadata
        files[tag + '.environment.json'] = sidecar
    session_block = dict(session_id=identity, split=split, index=index, seed=seed,
                         reference_prbs7_seed=seed)
    manifest = dict(
        schema=MANIFEST_SCHEMA, fixture=True, collected=False, physical=False,
        not_for_formal_acceptance=True, collection_mode=None,
        frame_count=FRAMES_PER_SESSION,
        normal_count=NORMAL_PER_SESSION, off_count=OFF_PER_SESSION,
        frame_order=list(FRAME_TAGS), session=session_block, slots=entries,
        domain=dict(domain), environment_version=environment_version,
        release_id='offline-fixture',
        provenance=dict(kind='offline_synthetic_fixture',
                        detail='deterministic synthetic session; zero physical frames'),
        created_by='scripts/rf_environment_trainval_session.py fixture',
        not_run=['physical session collection: NOT_RUN', 'board close/readback: NOT_RUN',
                 'development pilot: NOT_RUN', 'formal TEST: NOT_RUN'],
        origin='offline synthetic fixture; not a physical capture')
    _write_json(output_dir / 'session-manifest.json', manifest, overwrite=overwrite)
    for name, value in files.items():
        if name.endswith('.iq'):
            _write_bytes(output_dir / name, value, overwrite=overwrite)
        else:
            _write_json(output_dir / name, value, overwrite=overwrite)
    evidence = {name: _file_sha256(output_dir / name) for name in sorted(files)}
    receipt = dict(
        schema=RECEIPT_SCHEMA, fixture=True, collected=False, physical=False,
        collection_mode=None,
        split=split, seed=seed, session_id=identity, index=index,
        session_manifest_sha256=_file_sha256(output_dir / 'session-manifest.json'),
        frame_count=FRAMES_PER_SESSION, normal_count=NORMAL_PER_SESSION,
        off_count=OFF_PER_SESSION, evidence_sha256=evidence,
        frames=[dict(position=entry['position'], tag=entry['tag'],
                     frame_id=entry['frame_id'], iq=entry['iq'],
                     metadata=entry['metadata'], environment=entry['environment'],
                     iq_sha256=entry['iq_sha256'], capture_id=entry['capture_id'])
                for entry in entries],
        session_closed=False, disarm_ack=False, complete=True,
        not_run=['physical session collection: NOT_RUN', 'board close/readback: NOT_RUN'],
        origin='offline synthetic fixture; not a physical capture')
    _write_json(output_dir / 'receipt.json', receipt, overwrite=overwrite)
    return check_session(output_dir)


def _manifest_domain(document, session):
    domain = document.get('domain')
    if type(domain) is not dict or len(domain) != 8:
        raise SessionError('SESSION_MANIFEST_SCHEMA',
                           'session %s needs the eight frozen RF pairs' % session['session_id'])
    if any(type(key) is not str or type(value) is not int for key, value in domain.items()):
        raise SessionError('SESSION_MANIFEST_SCHEMA',
                           'session %s RF values must be integers' % session['session_id'])
    return dict(domain)


def _session_files(session_dir):
    names = []
    for path in sorted(Path(session_dir).iterdir()):
        if path.is_dir():
            continue
        names.append(path.name)
    return names


def check_session(session_dir):
    """Validate one materialized session; every refusal has a stable code."""
    session_dir = Path(session_dir)
    manifest_path = session_dir / 'session-manifest.json'
    document = _read_json(manifest_path, 'SESSION_MANIFEST_MISSING', 'session manifest')
    _require(type(document) is dict, 'SESSION_MANIFEST_SCHEMA',
             'session manifest must be a JSON object')
    _require(document.get('schema') == MANIFEST_SCHEMA, 'SESSION_MANIFEST_SCHEMA',
             'expected schema %s' % MANIFEST_SCHEMA)
    session = document.get('session')
    _require(type(session) is dict, 'SESSION_MANIFEST_SCHEMA',
             'session manifest needs a session block')
    split, index = session.get('split'), session.get('index')
    seed = _validate_session_arguments(split, index)
    identity = session_id(split, index)
    _require(session.get('session_id') == identity, 'SESSION_MANIFEST_SCHEMA',
             'session id must be %r for %s session %r' % (identity, split, index))
    _require(session.get('seed') == seed, 'SESSION_SEED_MISMATCH',
             '%s session %d must carry seed %d, manifest says %r'
             % (split, index, seed, session.get('seed')))
    _require(session.get('reference_prbs7_seed') == seed, 'SESSION_SEED_MISMATCH',
             'the reference PRBS7 seed rotates with the session seed (%d)' % seed)
    for key in ('fixture', 'collected', 'physical', 'not_for_formal_acceptance'):
        _require(type(document.get(key)) is bool, 'SESSION_MANIFEST_SCHEMA',
                 'session %s needs boolean %r' % (identity, key))
    if document['fixture']:
        _require(document['collected'] is False and document['physical'] is False,
                 'SESSION_FLAG_CONFLICT',
                 'a fixture session cannot declare collected/physical frames')
        _require(document['not_for_formal_acceptance'] is True, 'SESSION_FLAG_CONFLICT',
                 'a fixture session is never formal acceptance evidence')
    else:
        _require(document['collected'] is True, 'SESSION_FLAG_CONFLICT',
                 'a non-fixture session with frames must declare collected=true')
        _require(document['not_for_formal_acceptance'] is False, 'SESSION_FLAG_CONFLICT',
                 'a non-fixture TRAIN/VAL session is not a development pilot')
    # The declared collection mode.  It is optional so every session written before
    # this field existed still validates, and absent means the historical default
    # (formal acceptance).  A declared mode must be one of the two, and a session
    # that carries calibration-collected labels must say so.
    mode = check_collection_mode(document.get('collection_mode'))
    if document['fixture']:
        _require(document.get('collection_mode') is None, 'SESSION_FLAG_CONFLICT',
                 'a fixture session collects nothing, so it declares no collection mode')
    _require(document.get('release_id') is not None, 'SESSION_MANIFEST_SCHEMA',
             'session %s needs a release_id (or the offline-fixture marker)' % identity)
    domain = _manifest_domain(document, session)
    environment_version = document.get('environment_version')
    _require(type(environment_version) is str and environment_version,
             'SESSION_MANIFEST_SCHEMA', 'session %s needs environment_version' % identity)
    slots = document.get('slots')
    _require(type(slots) is list, 'SESSION_MANIFEST_SCHEMA',
             'session %s needs a slots list' % identity)
    _require(len(slots) == FRAMES_PER_SESSION, 'SESSION_FRAME_COUNT',
             'session %s has %d slots, the matrix requires %d'
             % (identity, len(slots), FRAMES_PER_SESSION))
    _require([slot.get('tag') for slot in slots] == list(FRAME_TAGS),
             'SESSION_FRAME_ORDER',
             'session %s slots must be ordered %s' % (identity, list(FRAME_TAGS)))
    _require(document.get('frame_order') == list(FRAME_TAGS), 'SESSION_FRAME_ORDER',
             'session %s frame_order must be %s' % (identity, list(FRAME_TAGS)))
    counts = {'normal': 0, 'off': 0}
    provisional_count = 0
    for position, slot in enumerate(slots):
        _require(type(slot) is dict, 'SESSION_MANIFEST_SCHEMA',
                 'session %s slot %d must be an object' % (identity, position))
        scenario = 'normal' if position < NORMAL_PER_SESSION else 'off'
        _require(slot.get('scenario') == scenario, 'SESSION_SCENARIO_MIX',
                 'session %s slot %s declares %r, the shape requires %r'
                 % (identity, slot.get('tag'), slot.get('scenario'), scenario))
        counts[scenario] += 1
        _require(slot.get('seed') == seed, 'SESSION_SEED_MISMATCH',
                 'session %s slot %s must carry the session seed %d'
                 % (identity, slot.get('tag'), seed))
        _require(slot.get('reference_prbs7_seed') == seed, 'SESSION_SEED_MISMATCH',
                 'session %s slot %s reference seed must rotate with the session'
                 % (identity, slot.get('tag')))
        tag = FRAME_TAGS[position]
        _require(slot.get('position') == position, 'SESSION_FRAME_ORDER',
                 'session %s slot %s must declare position %d'
                 % (identity, tag, position))
        _require(slot.get('frame_id') == '%s-%s' % (identity, tag)
                 and slot.get('source_id') == '%s-src-%s' % (identity, tag),
                 'SESSION_MANIFEST_SCHEMA',
                 'session %s slot %s identity is not session-scoped' % (identity, tag))
        _require(slot.get('iq') == tag + '.iq'
                 and slot.get('metadata') == tag + '.json'
                 and slot.get('environment') == tag + '.environment.json',
                 'SESSION_MANIFEST_SCHEMA',
                 'session %s slot %s artifact names are not canonical' % (identity, tag))
        _require(_sha_literal(slot.get('iq_sha256')), 'SESSION_FRAME_IQ_SHA_MISMATCH',
                 'session %s slot %s needs a full IQ sha256' % (identity, tag))
        _require(type(slot.get('capture_id')) is str and slot['capture_id'],
                 'SESSION_MANIFEST_SCHEMA',
                 'session %s slot %s needs a capture id' % (identity, tag))
        # The collected label is preserved verbatim (a calibration collection never
        # rewrites a label into CLEAR).  Label fields are optional so sessions written
        # before the field existed still validate, but an inconsistency is refused:
        # the provisional pair must be flagged, the flag must match the pair, and a
        # flagged pair may only come from a session that declared the calibration mode
        # (one shared implementation, CAL.check_collected_label).
        collected_label, collected_reason = slot.get('collected_label'), slot.get('collected_reason')
        if check_collected_label(slot, mode, tag):
            provisional_count += 1
    _require(counts['normal'] == NORMAL_PER_SESSION
             and counts['off'] == OFF_PER_SESSION, 'SESSION_SCENARIO_MIX',
             'session %s must hold %d normal + %d off frames, not %r'
             % (identity, NORMAL_PER_SESSION, OFF_PER_SESSION, counts))

    expected = {}
    for slot in slots:
        expected[slot['iq']] = slot
        expected[slot['metadata']] = slot
        expected[slot['environment']] = slot
    # Every file that is not one of the three declared non-frame documents must be a
    # declared frame artifact.  Filtering by suffix would silently ignore an
    # undeclared product of any other type (a stray note, a log, a renamed frame),
    # so the actual listing is "everything else", never "only .iq/.json".
    actual = [name for name in _session_files(session_dir)
              if name not in NON_FRAME_FILES]
    for name in sorted(expected):
        _require((session_dir / name).is_file() and not (session_dir / name).is_symlink(),
                 'SESSION_FRAME_MISSING',
                 'session %s is missing %s' % (identity, name))
    for name in actual:
        _require(name in expected, 'SESSION_FRAME_EXTRA',
                 'session %s holds an undeclared artifact %r' % (identity, name))
    entries, seen_capture, seen_iq = [], {}, {}
    for slot in slots:
        # Read the collected label from THIS slot: the validation loop above leaves
        # its last slot's label behind, and reusing it here would bind the off frame's
        # label to every frame (caught by the adapter/fit label check).
        collected_label, collected_reason = slot.get('collected_label'), slot.get('collected_reason')
        iq_path = session_dir / slot['iq']
        metadata_path = session_dir / slot['metadata']
        environment_path = session_dir / slot['environment']
        _require(iq_path.stat().st_size == IQ_BYTES
                 and metadata_path.stat().st_size <= METADATA_MAX_BYTES
                 and environment_path.stat().st_size <= METADATA_MAX_BYTES,
                 'SESSION_FRAME_SIZE',
                 'session %s artifact size out of bounds at %s' % (identity, slot['tag']))
        raw = iq_path.read_bytes()
        digest = _sha256_bytes(raw)
        _require(digest == slot['iq_sha256'], 'SESSION_FRAME_IQ_SHA_MISMATCH',
                 'session %s frame %s bytes hash to %s, manifest says %s'
                 % (identity, slot['tag'], digest, slot['iq_sha256']))
        metadata = _read_json(metadata_path, 'SESSION_FRAME_MISSING',
                              'frame %s metadata' % slot['tag'])
        sidecar = _read_json(environment_path, 'SESSION_FRAME_MISSING',
                             'frame %s environment sidecar' % slot['tag'])
        frame_session = dict(session_id=identity, split=split, seed=seed)
        _require(metadata.get('session') == frame_session
                 and sidecar.get('session') == frame_session,
                 'SESSION_FRAME_SESSION_MISMATCH',
                 'frame %s declares session %r, this directory is %s/%s/%d'
                 % (slot['tag'], metadata.get('session'), identity, split, seed))
        for key in ('fixture', 'collected'):
            _require(metadata.get(key) is document[key] and sidecar.get(key) is document[key],
                     'SESSION_FRAME_FLAG_MISMATCH',
                     'frame %s declares %s=%r, session manifest says %r'
                     % (slot['tag'], key, metadata.get(key), document[key]))
        # The session flag says "captured on hardware"; only a normal frame exercises
        # the TX->RX loopback, so an off frame is collected but not a physical capture.
        expected_physical = bool(document['physical'] and slot['scenario'] == 'normal')
        _require(metadata.get('physical') is expected_physical
                 and sidecar.get('physical') is expected_physical,
                 'SESSION_FRAME_FLAG_MISMATCH',
                 'frame %s declares physical=%r, a %r session requires %r'
                 % (slot['tag'], metadata.get('physical'),
                    'physical' if document['physical'] else 'offline',
                    expected_physical))
        identity_keys = ('capture_id', 'release_id', 'connection_generation',
                         'config_revision', 'completed_at_monotonic')
        frame_identity = {key: metadata.get(key) for key in identity_keys}
        # The board sidecar keeps capture_id/release_id at the top level and the
        # numeric identity fields inside its environment result.
        environment_block = sidecar.get('environment') or {}
        sidecar_identity = dict(
            capture_id=sidecar.get('capture_id'),
            release_id=sidecar.get('release_id'),
            connection_generation=environment_block.get('connection_generation'),
            config_revision=environment_block.get('config_revision'),
            completed_at_monotonic=environment_block.get('completed_at_monotonic'))
        _require(all(value is not None for value in frame_identity.values())
                 and frame_identity == sidecar_identity
                 and environment_block.get('capture_id') == frame_identity['capture_id']
                 and environment_block.get('release_id') == frame_identity['release_id'],
                 'SESSION_FRAME_IDENTITY_MISMATCH',
                 'frame %s metadata and environment sidecar disagree on identity: %r vs %r'
                 % (slot['tag'], frame_identity, sidecar_identity))
        _require(metadata.get('capture_id') == slot['capture_id'],
                 'SESSION_FRAME_IDENTITY_MISMATCH',
                 'frame %s capture id differs from the manifest slot' % slot['tag'])
        _require(metadata.get('release_id') == document['release_id']
                 and sidecar.get('release_id') == document['release_id'],
                 'SESSION_RELEASE_MISMATCH',
                 'frame %s release %r differs from session release %r'
                 % (slot['tag'], metadata.get('release_id'), document['release_id']))
        _require(metadata.get('iq_sha256') == digest
                 and sidecar.get('iq_sha256') == digest,
                 'SESSION_FRAME_IQ_SHA_MISMATCH',
                 'frame %s metadata/sidecar digest differs from its bytes' % slot['tag'])
        _require(sidecar.get('rf_config') == domain, 'SESSION_FRAME_DOMAIN_MISMATCH',
                 'frame %s rf_config differs from the session domain' % slot['tag'])
        environment = sidecar.get('environment') or {}
        _require(sidecar.get('environment_version') == environment_version
                 and environment.get('environment_version') == environment_version,
                 'SESSION_FRAME_ENVIRONMENT_VERSION_MISMATCH',
                 'frame %s environment version differs from the session version %r'
                 % (slot['tag'], environment_version))
        declaration = sidecar.get('scenario_declaration') or {}
        _require(declaration.get('truth') == slot['scenario'],
                 'SESSION_FRAME_SCENARIO_MISMATCH',
                 'frame %s sidecar declares truth %r, the slot says %r'
                 % (slot['tag'], declaration.get('truth'), slot['scenario']))
        demod = metadata.get('demod_result')
        _require(type(demod) is dict and type(demod.get('result')) is dict
                 and type(demod.get('submitted')) is dict,
                 'SESSION_FRAME_DEMOD_MISMATCH',
                 'frame %s needs a demod result and its submitted block' % slot['tag'])
        _require(all(demod['result'].get(key) == frame_identity[key] for key in identity_keys),
                 'SESSION_FRAME_IDENTITY_MISMATCH',
                 'frame %s demod result belongs to another capture' % slot['tag'])
        submitted = demod['submitted']
        config = submitted.get('config') or {}
        _require(type(config) is dict
                 and set(config) == {'mode', 'symbol_rate_hz', 'shaping',
                                     'reference_prbs7_seed'}
                 and config.get('mode') == 'BPSK'
                 and config.get('symbol_rate_hz') == 3000000
                 and config.get('shaping') == 'RECT'
                 and config.get('reference_prbs7_seed') == seed
                 # The submitted revision is the board's own RX-draft revision, not a
                 # constant: LiveUiSession.setup() flips reference_enabled and reseeds the
                 # reference, so a real board frame carries a non-negative revision >= 1
                 # (scripts/rf_bpsk_ui_board_session.py:242-248, ui_model.py:1963-1981).
                 # The frozen fixture writes 0.  The production formal path validates the
                 # same way (scripts/rf_environment_acceptance.py:166: >= 0); a negative or
                 # non-integer revision is still refused by name below.
                 and type(submitted.get('revision')) is int and submitted.get('revision') >= 0
                 and submitted.get('decim') == 1
                 and submitted.get('rx_len') == 4096
                 and submitted.get('input_fs_hz') == 60000000,
                 'SESSION_FRAME_DEMOD_MISMATCH',
                 'frame %s submitted demod block is outside the frozen RX domain'
                 % slot['tag'])
        previous = seen_capture.setdefault(frame_identity['capture_id'], slot['tag'])
        _require(previous == slot['tag'], 'SESSION_FRAME_IDENTITY_DUPLICATE',
                 'capture id %r is used by %s and %s'
                 % (frame_identity['capture_id'], previous, slot['tag']))
        previous = seen_iq.setdefault(digest, slot['tag'])
        _require(previous == slot['tag'], 'SESSION_FRAME_IQ_DUPLICATE',
                 'IQ digest %s is used by %s and %s' % (digest, previous, slot['tag']))
        entries.append(dict(
            position=slot['position'], tag=slot['tag'], frame_id=slot['frame_id'],
            source_id=slot['source_id'], scenario=slot['scenario'], seed=slot['seed'],
            reference_prbs7_seed=slot['reference_prbs7_seed'], iq=slot['iq'],
            metadata=slot['metadata'], environment=slot['environment'],
            iq_sha256=digest, capture_id=frame_identity['capture_id'],
            identity=frame_identity, physical=metadata.get('physical'),
            op=metadata.get('op'), submitted=dict(submitted),
            collected_label=collected_label, collected_reason=collected_reason,
            provisional_unknown=bool(slot.get('provisional_unknown')),
            scenario_declaration=dict(declaration), metadata_sha256=_file_sha256(metadata_path),
            environment_sha256=_file_sha256(environment_path),
            metadata_document=metadata, environment_document=sidecar))

    receipt_path = session_dir / 'receipt.json'
    receipt = _read_json(receipt_path, 'SESSION_RECEIPT_MISSING', 'session receipt')
    _require(type(receipt) is dict and receipt.get('schema') == RECEIPT_SCHEMA,
             'SESSION_RECEIPT_MISMATCH', 'session receipt schema mismatch')
    _require(receipt.get('session_manifest_sha256')
             == _file_sha256(manifest_path), 'SESSION_RECEIPT_MISMATCH',
             'session receipt is not bound to this manifest')
    for key in ('fixture', 'collected', 'physical'):
        _require(receipt.get(key) is document[key], 'SESSION_RECEIPT_MISMATCH',
                 'session receipt %s=%r disagrees with the manifest'
                 % (key, receipt.get(key)))
    if 'collection_mode' in receipt:
        _require(check_collection_mode(receipt.get('collection_mode')) == mode,
                 'SESSION_RECEIPT_MISMATCH',
                 'session receipt collection_mode %r disagrees with the manifest %r'
                 % (receipt.get('collection_mode'), mode))
    _require(receipt.get('split') == split and receipt.get('seed') == seed
             and receipt.get('session_id') == identity
             and receipt.get('frame_count') == FRAMES_PER_SESSION
             and receipt.get('complete') is True, 'SESSION_RECEIPT_MISMATCH',
             'session receipt identity/count is incomplete')
    evidence = receipt.get('evidence_sha256')
    expected_evidence = {entry['iq']: entry['iq_sha256'] for entry in entries}
    expected_evidence.update({entry['metadata']: entry['metadata_sha256']
                              for entry in entries})
    expected_evidence.update({entry['environment']: entry['environment_sha256']
                              for entry in entries})
    _require(type(evidence) is dict and set(evidence) == set(expected_evidence)
             and all(evidence[name] == expected_evidence[name]
                     for name in expected_evidence), 'SESSION_RECEIPT_MISMATCH',
             'session receipt does not bind every artifact digest')
    binding = receipt.get('frames')
    _require(type(binding) is list and len(binding) == FRAMES_PER_SESSION,
             'SESSION_RECEIPT_MISMATCH', 'session receipt needs one binding per frame')
    for position, (row, entry) in enumerate(zip(binding, entries)):
        _require(type(row) is dict and row.get('position') == position
                 and row.get('tag') == entry['tag'] and row.get('frame_id') == entry['frame_id']
                 and row.get('iq') == entry['iq'] and row.get('metadata') == entry['metadata']
                 and row.get('environment') == entry['environment']
                 and row.get('iq_sha256') == entry['iq_sha256']
                 and row.get('capture_id') == entry['capture_id'],
                 'SESSION_RECEIPT_MISMATCH',
                 'session receipt frame binding %d disagrees with the verified frame'
                 % position)
    _require(type(receipt.get('session_closed')) is bool
             and type(receipt.get('disarm_ack')) is bool, 'SESSION_RECEIPT_MISMATCH',
             'session receipt must state session_closed and disarm_ack booleans')
    return dict(
        schema=MANIFEST_SCHEMA, session_id=identity, split=split, index=index,
        seed=seed, fixture=document['fixture'], collected=document['collected'],
        physical=document['physical'], release_id=document['release_id'],
        collection_mode=mode, provisional_unknown_count=provisional_count,
        domain=dict(domain), environment_version=environment_version,
        frame_count=FRAMES_PER_SESSION, normal_count=NORMAL_PER_SESSION,
        off_count=OFF_PER_SESSION, frame_order=list(FRAME_TAGS),
        manifest_path=str(manifest_path), receipt_path=str(receipt_path),
        manifest_sha256=_file_sha256(manifest_path),
        receipt_sha256=_file_sha256(receipt_path), receipt=receipt,
        session_closed=receipt['session_closed'], disarm_ack=receipt['disarm_ack'],
        manifest=document, frames=entries)


def summarize(result):
    """Small printable view; documents stay out of CLI output."""
    return dict(
        session_id=result['session_id'], split=result['split'],
        index=result['index'], seed=result['seed'],
        fixture=result['fixture'], collected=result['collected'],
        physical=result['physical'], release_id=result['release_id'],
        environment_version=result['environment_version'],
        domain=result['domain'], frame_count=result['frame_count'],
        normal_count=result['normal_count'], off_count=result['off_count'],
        frame_order=result['frame_order'], manifest_sha256=result['manifest_sha256'],
        receipt_sha256=result['receipt_sha256'],
        frames=[dict(tag=entry['tag'], scenario=entry['scenario'],
                     iq_sha256=entry['iq_sha256'], capture_id=entry['capture_id'])
                for entry in result['frames']],
        not_run=['physical session collection: NOT_RUN'] if result['fixture'] else [])


def collect_session(split, index, output_dir, *, authorized=False, candidate=None,
                    cable_confirmed=False, board_factory=None, timeout=900):
    """The real acquisition route, still fail-closed without explicit authorization.

    The authorized flag is set by the CLI only for the explicit operator flag
    --authorize-board-collection; without it this is the same named refusal as
    before and nothing is imported, connected, uploaded or written.  With it the
    host orchestrator (scripts/rf_environment_trainval_collect.py) is the only
    thing that talks to a board.
    """
    _validate_session_arguments(split, index)
    if not authorized:
        raise SessionError(
            'PHYSICAL_COLLECTION_NOT_AUTHORIZED',
            'physical collection of %s session %d is NOT_RUN without the explicit '
            'authorization flag: it needs a separately authorized board job (one init + '
            '10 normal + 1 off, disarmed and read back) on a release whose frozen baseline '
            'is the same domain, with the TX1-RX1 cable confirmed; no output was written '
            'to %s' % (split, index, output_dir))
    if candidate is None:
        raise SessionError('SESSION_COLLECT_CANDIDATE_REQUIRED',
                           'the authorized board route needs --candidate <verified release '
                           'directory>; no connection was attempted')
    if not cable_confirmed:
        raise SessionError('SESSION_COLLECT_CABLE_CONFIRMATION_REQUIRED',
                           'the authorized board route needs --cable-confirmed: the TX1-RX1 '
                           'wiring is a human attestation, never inferred; no connection '
                           'was attempted')
    from scripts import rf_environment_trainval_collect as COLLECT
    return COLLECT.collect(candidate, output_dir, split=split, index=index,
                           cable_confirmed=True, authorized=True,
                           board_factory=board_factory, timeout=timeout)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan', help='emit the 11-slot session plan (no IQ)')
    plan.add_argument('--split', choices=SPLITS, required=True)
    plan.add_argument('--session', type=int, required=True)
    plan.add_argument('--output', type=Path)
    plan.add_argument('--overwrite', action='store_true')
    fixture = commands.add_parser('fixture', help='write a deterministic offline session')
    fixture.add_argument('--split', choices=SPLITS, required=True)
    fixture.add_argument('--session', type=int, required=True)
    fixture.add_argument('--output', type=Path, required=True)
    fixture.add_argument('--overwrite', action='store_true')
    check = commands.add_parser('check', help='validate one materialized session')
    check.add_argument('--session', type=Path, required=True)
    collect = commands.add_parser(
        'collect', help='physical board route; fail-closed without the authorization flag')
    collect.add_argument('--split', choices=SPLITS, required=True)
    collect.add_argument('--session', type=int, required=True)
    collect.add_argument('--output', type=Path, required=True,
                         help='download root; the session lands under <root>/<session-id>/')
    collect.add_argument('--authorize-board-collection', action='store_true',
                         help='the explicit switch that enables the physical board route')
    collect.add_argument('--candidate', type=Path,
                         help='verified release directory carrying the same domain baseline')
    collect.add_argument('--cable-confirmed', action='store_true',
                         help='human attestation of the TX1-RX1 wiring')
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        if args.command == 'plan':
            document = plan_document(args.split, args.session)
            result = check_plan(document)
            if args.output:
                _write_json(args.output, document, overwrite=args.overwrite)
                result['plan_file'] = str(args.output.resolve())
        elif args.command == 'fixture':
            result = summarize(build_fixture_session(
                args.split, args.session, args.output, overwrite=args.overwrite))
        elif args.command == 'check':
            result = summarize(check_session(args.session))
        else:
            from scripts import rf_environment_trainval_collect as COLLECT
            receipt = collect_session(
                args.split, args.session, args.output,
                authorized=args.authorize_board_collection, candidate=args.candidate,
                cable_confirmed=args.cable_confirmed)
            result = COLLECT.summarize(receipt)
            exit_code = 0 if receipt['passed'] else 1
    except CAL.CalibrationError as exc:
        print(json.dumps(exc.to_dict(), indent=2, ensure_ascii=True))
        return 3
    print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False, sort_keys=True))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())