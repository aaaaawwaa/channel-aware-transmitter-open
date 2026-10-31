"""Independent TRAIN/VAL environment calibration entry and provisional baseline.

Offline only: this entry performs no board, network, RF, authorization or nonce action.
It reads sealed IQ artifacts (or writes a deterministic offline fixture) and runs the
production replay/fit/load path in this process.

Commands
--------
plan        emit or check the 46 dB TRAIN/VAL plan (2 sessions x 11 frames + 1 session x
            11 frames = 33 frames). A plan carries no IQ at all and must declare
            collected=false; it is a plan, not a measurement.
fixture     write a deterministic offline calibration set (3 sessions x 11 frames,
            10 normal + 1 off each) so the whole flow runs without a board.
fit         validate a collected set by session/split, fit the baseline from the TRAIN
            normal frames only, evaluate VAL as-is, write baseline + receipt.
            --require-physical refuses a set whose provenance is an offline fixture,
            so a formal packaging step cannot consume synthetic TRAIN/VAL.
provisional build the explicitly provisional n=1 baseline from ONE archived 46 dB
            research normal frame (development bootstrap only).
load-check  read a baseline back through the production loader and prove equality.
            --require-formal refuses a development body with fewer than two sources
            (the provisional n=1 bootstrap), so a formal packaging step cannot consume
            the bare body as if it were a formal baseline.

Group/split rules (every refusal has a stable code)
---------------------------------------------------
* a session owns exactly one split; a frame may repeat its session split but may never
  contradict it (SESSION_SPLIT_CONFLICT);
* no session id or IQ digest may appear in two splits (SPLIT_LEAKAGE; the shared
  scripts.rf_environment_dataset.validate_splits lineage rule is reused);
* TRAIN is 2 sessions and VAL is 1 session, 11 frames per session (10 normal + 1 off);
  narrowband/pulse/compound/domain never enter TRAIN/VAL (SCENARIO_NOT_ALLOWED);
* every frame is bound to its bytes: declared digest == sidecar digest == file bytes
  (FRAME_IQ_SHA_MISMATCH); missing, symlinked, absolute-escaping or oversized artifacts
  fail (FRAME_ARTIFACT_MISSING / FRAME_ARTIFACT_SIZE / FRAME_PATH);
* sources handed to the baseline may only be split=train (BASELINE_SOURCE_NOT_TRAIN).

Domain rule
-----------
rf_environment.EnvironmentBaseline refuses any body whose rf_config differs from
rf_environment.FIXED_RF, and FIXED_RF is derived from the single attenuation literal in
pynq_overlay/sw/rf_teaching_profile.py. A 46 dB baseline therefore cannot be constructed
before that truth source is migrated: this entry refuses with RF_DOMAIN_MIGRATION_REQUIRED
instead of mislabelling a domain. For the provisional artifact only, --truth-source <dir>
supplies an audited post-migration truth source (exactly one field different, both hashes
recorded); the product tree is never modified. scripts/rf_environment_acceptance.FIXED_RF
is a second, independent literal, so a real TRAIN/VAL calibration additionally requires
both truth sources to agree (DOMAIN_TRUTH_SOURCE_SPLIT).

No claim: the 51-frame matrix is a plan, the provisional n=1 baseline is not TRAIN/VAL and
not TEST, and this entry never asserts physical provenance it did not measure.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts.rf_environment_dataset import validate_splits  # noqa: E402  (shared lineage rule)

TEACHING_PROFILE = ROOT / 'pynq_overlay' / 'sw' / 'rf_teaching_profile.py'
ACCEPTANCE_MODULE = 'scripts.rf_environment_acceptance'
CODE_TRUTH_SOURCES = (ROOT / 'pynq_overlay' / 'sw' / 'rf_environment.py',
                      TEACHING_PROFILE,
                      ROOT / 'scripts' / 'rf_environment_acceptance.py')

SET_SCHEMA = 'rf-environment-calibration-set-v1'
PLAN_SCHEMA = 'rf-environment-calibration-plan-v1'
RECEIPT_SCHEMA = 'rf-environment-calibration-receipt-v1'
FRAME_SCHEMA = 'rf-environment-calibration-frame-v1'
PROVISIONAL_SCHEMA = 'rf-environment-provisional-baseline-v1'

PLAN_TRAIN_SESSIONS = 2
PLAN_VAL_SESSIONS = 1
PLAN_FRAMES_PER_SESSION = 11
PLAN_NORMAL_PER_SESSION = 10
PLAN_OFF_PER_SESSION = 1
PLAN_SEEDS = {'train': [1, 37], 'val': [73]}
PLAN_FRAMES = (PLAN_TRAIN_SESSIONS + PLAN_VAL_SESSIONS) * PLAN_FRAMES_PER_SESSION
SPLITS = ('train', 'val')
SCENARIOS = ('normal', 'off')
IQ_BYTES = 16384
METADATA_MAX_BYTES = 131072
CANONICAL_RULE = ('json.dumps(sort_keys=True,separators=(",",":"),ensure_ascii=True,'
                  'allow_nan=False)')
PROVISIONAL_FLAGS = dict(provisional=True, n=1, not_for_formal_acceptance=True)
# ---------------------------------------------------------------- collection mode
# The canonical declaration of HOW a session was collected, shared by the session
# schema, the board runner, the host orchestrator and this calibration entry.
#
# * STANDARD_COLLECTION is the formal acceptance rule: a VAL normal frame must be
#   CLEAR, and a caller that declares no mode gets this one (fail closed).
# * CALIBRATION_COLLECTION (spec 2026-10-17 section J, added 2026-10-18) additionally accepts one
#   UNKNOWN pair the pre-fit n=1 baseline produces for a qualified normal frame,
#   and PULSE / SPARSE_AMPLITUDE_OUTLIERS (operator-approved 2026-10-18 follow-up), only
#   after every quality gate already passed.  The label is preserved, never
#   rewritten, and the mode never relaxes the fitted criteria below: the baseline
#   is still fitted from the 20 TRAIN normal frames only, VAL must still be 10/10
#   CLEAR afterwards and OFF must still never be CLEAR.
STANDARD_COLLECTION = 'standard_acceptance'
CALIBRATION_COLLECTION = 'calibration_collection'
COLLECTION_MODES = (STANDARD_COLLECTION, CALIBRATION_COLLECTION)
PROVISIONAL_UNKNOWN_LABEL = 'UNKNOWN'
PROVISIONAL_UNKNOWN_REASON = 'AMPLITUDE_OUTSIDE_RULE_SUPPORT'
PROVISIONAL_UNKNOWN = (PROVISIONAL_UNKNOWN_LABEL, PROVISIONAL_UNKNOWN_REASON)
BASELINE_BODY_FIELDS = ('schema_version', 'environment_version', 'algorithm_version',
                        'calibration_status', 'rf_config', 'spectrum_envelope', 'thresholds',
                        'sources')


class CalibrationError(ValueError):
    """One named refusal; callers match on the code, never on prose."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__('%s: %s' % (code, detail) if detail else code)

    def to_dict(self):
        return dict(error=self.code, detail=self.detail)


def provisional_unknown(label, reason):
    """Is this the provisional UNKNOWN pair tracked by the metadata flag?"""
    return (label, reason) == PROVISIONAL_UNKNOWN


def declared_collection_mode(value, code='COLLECTION_MODE_UNKNOWN'):
    """Validate one declared collection mode; None is the pre-existing default.

    None is mapped to STANDARD_COLLECTION so every artifact written before the mode
    existed still reads as the formal rule, and an unknown mode is refused by name
    instead of being treated as the looser of the two.
    """
    if value is None:
        return STANDARD_COLLECTION
    if value not in COLLECTION_MODES:
        raise CalibrationError(code, 'unknown collection mode %r; expected one of %r'
                               % (value, list(COLLECTION_MODES)))
    return value


def check_collected_label(sidecar, collection_mode, frame_id, code='FRAME_LABEL_MODE_MISMATCH'):
    """A collected label may never be silently relabelled or left un-flagged.

    Returns True when the frame was collected as the provisional UNKNOWN pair.  The
    pairing is checked in both directions, so a hand-edited sidecar that adds the
    pair without the flag -- or flags a pair it does not carry -- is refused.
    """
    label = sidecar.get('collected_label')
    reason = sidecar.get('collected_reason')
    flagged = sidecar.get('provisional_unknown')
    pair = provisional_unknown(label, reason)
    if flagged is None:
        _require(not pair, code,
                 'frame %s carries the provisional pair %r/%r without declaring '
                 'provisional_unknown' % (frame_id, label, reason))
        return False
    _require(type(flagged) is bool, code,
             'frame %s declares provisional_unknown=%r, a boolean is required'
             % (frame_id, flagged))
    _require(flagged is pair, code,
             'frame %s declares provisional_unknown=%r with label %r/%r'
             % (frame_id, flagged, label, reason))
    if flagged:
        _require(collection_mode == CALIBRATION_COLLECTION, code,
                 'frame %s was collected as the provisional pair but its session declares '
                 'collection_mode %r' % (frame_id, collection_mode))
    return pair


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path):
    return _sha256_bytes(Path(path).read_bytes())


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _digest(value):
    return _sha256_bytes(_canonical(value))


def _sha_literal(value):
    return (type(value) is str and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _read_json(path, code, label):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise CalibrationError(code, '%s not found: %s' % (label, path))
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CalibrationError(code, '%s is not valid JSON: %s' % (label, exc))


def _write_json(path, value, *, overwrite=False):
    path = Path(path)
    if path.exists() and not overwrite:
        raise CalibrationError('OUTPUT_EXISTS', str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline='\n': evidence/receipt JSON must be LF so `git diff --check` stays clean;
    # the canonical sha256 of a body is computed over its JSON text, not its bytes.
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False))
    return path


def _pairs(rf_config, code='DOMAIN_INVALID', label='rf_config'):
    if type(rf_config) is not dict or len(rf_config) != 8:
        raise CalibrationError(code, '%s must be the eight frozen RF pairs' % label)
    if any(type(k) is not str or type(v) is not int for k, v in rf_config.items()):
        raise CalibrationError(code, '%s values must be integers' % label)
    return rf_config


def _module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise CalibrationError('DOMAIN_TRUTH_SOURCE_MISSING', 'cannot import %s' % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile_domain(path):
    profile = _module_from(path, 'calibration_profile_' + _sha256_bytes(str(path).encode())[:12])
    return _pairs(dict(profile.teaching_capability().rf_parameters),
                  'DOMAIN_TRUTH_SOURCE_MISSING', 'teaching profile %s' % path), profile


def _profile_surface(path):
    """Everything the teaching profile declares except the RF parameters."""
    domain, profile = _profile_domain(path)
    surface = dict(profile.teaching_capability().to_dict())
    surface.pop('rf_parameters')
    return domain, surface


def validate_truth_source(truth_source_dir, declared_mdB):
    """A post-migration truth source may differ in exactly one attenuation field."""
    if not truth_source_dir:
        raise CalibrationError('DOMAIN_TRUTH_SOURCE_MISSING', 'no truth source directory given')
    path = Path(truth_source_dir) / 'rf_teaching_profile.py'
    if not path.is_file():
        raise CalibrationError('DOMAIN_TRUTH_SOURCE_MISSING', str(path))
    product_domain, product_surface = _profile_surface(TEACHING_PROFILE)
    domain, surface = _profile_surface(path)
    if domain != dict(product_domain, attenuation_mdB=declared_mdB):
        raise CalibrationError('DOMAIN_TRUTH_SOURCE_MISMATCH',
            'truth source must differ from the product profile only in attenuation_mdB')
    if surface != product_surface:
        raise CalibrationError('DOMAIN_TRUTH_SOURCE_MISMATCH',
            'truth source changes fields other than attenuation_mdB: %s'
            % sorted(k for k in set(surface) | set(product_surface)
                     if surface.get(k) != product_surface.get(k)))
    return dict(directory=str(Path(truth_source_dir).resolve()), file=str(path.resolve()),
                sha256=_file_sha256(path), product_sha256=_file_sha256(TEACHING_PROFILE),
                product_attenuation_mdB=product_domain['attenuation_mdB'],
                attenuation_mdB=declared_mdB, diff_fields=['rf_parameters.attenuation_mdB'])


def code_truth_sources():
    """Hashes of every code file that decides the domain, pinned into each receipt."""
    return {str(path.relative_to(ROOT)).replace('\\', '/'): _file_sha256(path)
            for path in CODE_TRUTH_SOURCES}


def _acceptance_domain():
    from scripts import rf_environment_acceptance as acceptance
    return dict(acceptance.FIXED_RF)


def domain_audit(declared_mdB=None, truth_source_dir=None):
    """Record every domain truth source and whether they agree; never mutate anything."""
    product_domain = dict(_profile_domain(TEACHING_PROFILE)[0])
    acceptance = _acceptance_domain()
    truth = validate_truth_source(truth_source_dir, declared_mdB) if truth_source_dir else None
    effective = dict(product_domain)
    if truth is not None:
        effective['attenuation_mdB'] = truth['attenuation_mdB']
    return dict(
        declared_attenuation_mdB=declared_mdB,
        code_truth_sources=code_truth_sources(),
        product_teaching_attenuation_mdB=product_domain['attenuation_mdB'],
        product_teaching_profile=str(TEACHING_PROFILE.resolve()),
        product_teaching_profile_sha256=_file_sha256(TEACHING_PROFILE),
        product_acceptance_attenuation_mdB=acceptance['attenuation_mdB'],
        acceptance_truth_source=ACCEPTANCE_MODULE,
        effective_attenuation_mdB=effective['attenuation_mdB'],
        truth_source=truth,
        product_truth_sources_agree=product_domain == acceptance,
        effective_matches_declared=(declared_mdB is not None
                                    and effective['attenuation_mdB'] == declared_mdB),
        migration_required=(declared_mdB is not None
                            and product_domain['attenuation_mdB'] != declared_mdB))


def require_declared_domain(audit, *, require_product_agreement, truth_source_dir=None):
    """Refuse to fit or load a domain the installed product does not declare."""
    declared = audit['declared_attenuation_mdB']
    if type(declared) is not int:
        raise CalibrationError('DOMAIN_UNDECLARED', 'a declared attenuation_mdB is required')
    if not audit['effective_matches_declared']:
        raise CalibrationError('RF_DOMAIN_MIGRATION_REQUIRED',
            'declared %d mdB but the effective domain truth source declares %d mdB; migrate '
            '%s (single attenuation literal) and %s before constructing a %d mdB baseline'
            % (declared, audit['effective_attenuation_mdB'], audit['product_teaching_profile'],
               audit['acceptance_truth_source'], declared))
    if require_product_agreement:
        if truth_source_dir:
            raise CalibrationError('DOMAIN_TRUTH_SOURCE_NOT_ALLOWED',
                'a real TRAIN/VAL calibration must run on the migrated product tree, '
                'not through a supplied truth source')
        if not audit['product_truth_sources_agree']:
            raise CalibrationError('DOMAIN_TRUTH_SOURCE_SPLIT',
                'teaching profile declares %d mdB but %s declares %d mdB; migrate both'
                % (audit['product_teaching_attenuation_mdB'], audit['acceptance_truth_source'],
                   audit['product_acceptance_attenuation_mdB']))
    return audit


@contextlib.contextmanager
def environment_in_domain(truth_source_dir=None):
    """Import the production environment module inside the declared domain context.

    The overlay only changes sys.path for the duration of the call; the previous module
    objects are restored afterwards, so a domain can never leak between runs.
    """
    saved_modules = {name: sys.modules.get(name)
                     for name in ('rf_environment', 'rf_teaching_profile')}
    saved_path = list(sys.path)
    try:
        if truth_source_dir:
            sys.path.insert(0, str(Path(truth_source_dir).resolve()))
            sys.modules.pop('rf_teaching_profile', None)
            sys.modules.pop('rf_environment', None)
        import rf_environment as environment
        yield environment
    finally:
        sys.modules.pop('rf_environment', None)
        sys.modules.pop('rf_teaching_profile', None)
        for name, module in saved_modules.items():
            if module is not None:
                sys.modules[name] = module
        sys.path[:] = saved_path


def _require(condition, code, detail=''):
    if not condition:
        raise CalibrationError(code, detail)


def _relative_artifact(root, value, code):
    _require(type(value) is str and value and not Path(value).is_absolute(), code,
             'artifact path must be a non-empty relative path: %r' % (value,))
    root = Path(root).resolve()
    candidate = (root / value).resolve()
    _require(candidate != root and root in candidate.parents, code,
             'artifact path escapes the set directory: %r' % (value,))
    return candidate


def plan_document():
    """The 46 dB TRAIN/VAL minimum matrix as a plan: 33 frames, zero collected."""
    return dict(
        schema=PLAN_SCHEMA, collected=False, frames_collected=0, physical_frames=0,
        not_for_formal_acceptance=True, total_frames=PLAN_FRAMES,
        scope='46 dB environment TRAIN/VAL minimum matrix (spec 2026-10-17 section 2.2)',
        domain_attenuation_mdB=46000,
        groups=[
            dict(split='train', sessions=PLAN_TRAIN_SESSIONS, seeds=PLAN_SEEDS['train'],
                 per_session=dict(normal=PLAN_NORMAL_PER_SESSION, off=PLAN_OFF_PER_SESSION),
                 frames=PLAN_TRAIN_SESSIONS * PLAN_FRAMES_PER_SESSION,
                 role='normal frames only enter fit_baseline; off is the same-session energy '
                      'floor negative control'),
            dict(split='val', sessions=PLAN_VAL_SESSIONS, seeds=PLAN_SEEDS['val'],
                 per_session=dict(normal=PLAN_NORMAL_PER_SESSION, off=PLAN_OFF_PER_SESSION),
                 frames=PLAN_VAL_SESSIONS * PLAN_FRAMES_PER_SESSION,
                 role='held-out session; threshold-out-of-range check only, never fitted')],
        excluded_from_train_val={
            'narrowband': 'unknown class; collected in the development pilot, not TRAIN/VAL',
            'pulse': 'unknown class; collected in the development pilot, not TRAIN/VAL',
            'compound': 'negative control; collected in the development pilot, not TRAIN/VAL',
            'domain': 'negative control (2 MSym/s); pilot only, not TRAIN/VAL'},
        separate_campaigns=dict(development_pilot_frames=18, formal_test_frames=108),
        criteria=dict(
            train_normal='10/10 reliable BPSK per session (reference_sync, ber=0, rms_codes>=16,'
                         ' no rail/CLIPPED/INVALID)',
            val_normal='10/10 CLEAR; no NARROWBAND/PULSE misclassification',
            negative='off must never be CLEAR'),
        stop_conditions=[
            'any CLIPPED / INVALID_IQ / egress_safe!=true / missing download',
            'any duplicated frame identity or IQ digest',
            'a session cannot be closed (close unconfirmed / disarm_ack false)',
            'a TRAIN normal frame is not reliable BPSK (fit_baseline refuses)',
            'VAL misclassifies a normal frame (record and stop; never retune a threshold)',
            'release identity drift or an unexplained service restart or storage fault'],
        not_run=['physical TRAIN sessions: NOT_RUN', 'physical VAL session: NOT_RUN',
                 'development pilot: NOT_RUN', 'formal TEST: NOT_RUN', 'human acceptance: NOT_RUN'])


def check_plan(document):
    """Validate a plan document; a plan is never data and may never claim frames."""
    _require(type(document) is dict, 'PLAN_SCHEMA', 'plan must be a JSON object')
    _require(document.get('schema') == PLAN_SCHEMA, 'PLAN_SCHEMA',
             'expected schema %s' % PLAN_SCHEMA)
    _require(document.get('collected') is False and document.get('frames_collected') == 0
             and document.get('physical_frames') == 0, 'PLAN_NOT_A_PLAN',
             'a plan carries no collected frames')
    expected = plan_document()
    for key in ('total_frames', 'groups', 'excluded_from_train_val', 'separate_campaigns',
                'stop_conditions', 'not_for_formal_acceptance'):
        _require(document.get(key) == expected[key], 'PLAN_MATRIX_MISMATCH',
                 'plan field %r differs from the declared minimum matrix' % key)
    _require(document['total_frames'] == PLAN_FRAMES, 'PLAN_MATRIX_MISMATCH',
             'TRAIN/VAL total must be %d frames' % PLAN_FRAMES)
    return dict(schema=PLAN_SCHEMA, total_frames=PLAN_FRAMES, frames_collected=0, collected=False,
                splits={group['split']: dict(sessions=group['sessions'], frames=group['frames'],
                                             seeds=group['seeds'],
                                             per_session=group['per_session'])
                        for group in document['groups']},
                domain_attenuation_mdB=document['domain_attenuation_mdB'],
                not_run=document['not_run'])


def _plan_summary():
    return dict(train_sessions=PLAN_TRAIN_SESSIONS, val_sessions=PLAN_VAL_SESSIONS,
                frames_per_session=PLAN_FRAMES_PER_SESSION,
                normal_per_session=PLAN_NORMAL_PER_SESSION,
                off_per_session=PLAN_OFF_PER_SESSION, seeds=dict(PLAN_SEEDS))


def validate_set(declaration, root):
    """Group/split validation; returns the declared domain and the bound frame entries."""
    root = Path(root)
    _require(type(declaration) is dict, 'CAL_SET_SCHEMA', 'set.json must be a JSON object')
    _require(declaration.get('schema') == SET_SCHEMA, 'CAL_SET_SCHEMA',
             'expected schema %s (a plan is not a collected set)' % SET_SCHEMA)
    for key in ('provenance', 'domain', 'plan', 'sessions'):
        _require(key in declaration, 'CAL_SET_SCHEMA', 'set.json needs %r' % key)
    _require(declaration['plan'] == _plan_summary(), 'SPLIT_PLAN_MISMATCH',
             'declared plan must be TRAIN 2 x 11 + VAL 1 x 11 (10 normal + 1 off per session)')
    provenance = declaration['provenance']
    _require(type(provenance) is dict and provenance.get('kind') in
             ('offline_synthetic_fixture', 'new_physical_train_val')
             and type(provenance.get('detail')) is str, 'CAL_SET_SCHEMA',
             'provenance.kind must be offline_synthetic_fixture or new_physical_train_val')
    domain = _pairs(declaration['domain'], 'CAL_SET_SCHEMA', 'set domain')
    sessions = declaration['sessions']
    _require(type(sessions) is list and sessions, 'CAL_SET_SCHEMA', 'sessions must be a list')
    # One set, one declared collection mode (spec 2026-10-17 section J).  Absent
    # means the historical default (formal acceptance), so every set written before
    # the mode existed still reads; a declared mode must be one of the two, and a
    # mixed set is refused rather than reduced to its loosest session.
    declared_modes = {session.get('session_id'):
                      declared_collection_mode(session.get('collection_mode'), 'CAL_SET_SCHEMA')
                      for session in sessions}
    set_mode = declared_collection_mode(declaration.get('collection_mode'), 'CAL_SET_SCHEMA')
    _require(len(set(declared_modes.values())) == 1, 'COLLECTION_MODE_MISMATCH',
             'the set mixes session collection modes: %r'
             % sorted(set(declared_modes.values())))
    _require(set_mode == next(iter(declared_modes.values())), 'COLLECTION_MODE_MISMATCH',
             'the set declares collection_mode %r, its sessions declare %r'
             % (set_mode, next(iter(declared_modes.values()))))

    per_split = {split: [] for split in SPLITS}
    seen_sessions = {}
    for session in sessions:
        _require(type(session) is dict, 'CAL_SET_SCHEMA', 'session must be an object')
        split = session.get('split')
        _require(split in SPLITS, 'SPLIT_NOT_ALLOWED',
                 'session split must be train or val, not %r (test/other splits are refused)'
                 % (split,))
        session_id = session.get('session_id')
        _require(type(session_id) is str and session_id, 'CAL_SET_SCHEMA',
                 'session needs a non-empty session_id')
        _require(session_id not in seen_sessions, 'DUPLICATE_SESSION_ID', session_id)
        seen_sessions[session_id] = split
        frames = session.get('frames')
        _require(type(frames) is list, 'CAL_SET_SCHEMA', 'session %s needs frames' % session_id)
        _require(len(frames) == PLAN_FRAMES_PER_SESSION, 'SESSION_FRAME_COUNT',
                 'session %s has %d frames, the matrix requires %d'
                 % (session_id, len(frames), PLAN_FRAMES_PER_SESSION))
        per_split[split].append(session_id)
    # Structure first: a missing split or a wrong session count is a group error and must
    # never be reported as a per-frame artifact problem.
    for split in SPLITS:
        _require(per_split[split], 'MISSING_SPLIT', 'no %s session declared' % split)
    for split, count in (('train', PLAN_TRAIN_SESSIONS), ('val', PLAN_VAL_SESSIONS)):
        _require(len(per_split[split]) == count, 'SPLIT_PLAN_MISMATCH',
                 '%s needs %d sessions, found %d' % (split, count, len(per_split[split])))
    entries, split_of = [], {}
    for session in sessions:
        split, session_id = session['split'], session['session_id']
        counts = {name: 0 for name in SCENARIOS}
        for frame in session['frames']:
            _require(type(frame) is dict, 'CAL_SET_SCHEMA', 'frame must be an object')
            _require(frame.get('split', split) == split, 'SESSION_SPLIT_CONFLICT',
                     'frame %r declares split %r inside a %s session'
                     % (frame.get('frame_id'), frame.get('split'), split))
            scenario = frame.get('scenario')
            _require(scenario in SCENARIOS, 'SCENARIO_NOT_ALLOWED',
                     'TRAIN/VAL accepts only normal/off frames, not %r' % (scenario,))
            counts[scenario] += 1
            frame_id = frame.get('frame_id')
            source_id = frame.get('source_id')
            _require(type(frame_id) is str and frame_id and type(source_id) is str and source_id,
                     'CAL_SET_SCHEMA', 'frame needs frame_id and source_id')
            declared_sha = frame.get('iq_sha256')
            _require(_sha_literal(declared_sha), 'FRAME_IQ_SHA_MISMATCH',
                     'frame %s needs a full IQ sha256' % frame_id)
            iq_path = _relative_artifact(root, frame.get('iq'), 'FRAME_PATH')
            metadata_path = _relative_artifact(root, frame.get('metadata'), 'FRAME_PATH')
            _require(iq_path.is_file() and not iq_path.is_symlink()
                     and metadata_path.is_file() and not metadata_path.is_symlink(),
                     'FRAME_ARTIFACT_MISSING',
                     'frame %s is missing an artifact (%s / %s)'
                     % (frame_id, iq_path.name, metadata_path.name))
            _require(iq_path.stat().st_size == IQ_BYTES
                     and metadata_path.stat().st_size <= METADATA_MAX_BYTES,
                     'FRAME_ARTIFACT_SIZE',
                     'frame %s artifact size out of bounds (%d/%d bytes)'
                     % (frame_id, iq_path.stat().st_size, metadata_path.stat().st_size))
            raw = iq_path.read_bytes()
            actual = _sha256_bytes(raw)
            _require(actual == declared_sha, 'FRAME_IQ_SHA_MISMATCH',
                     'frame %s bytes hash to %s, declaration says %s'
                     % (frame_id, actual, declared_sha))
            sidecar = _read_json(metadata_path, 'FRAME_ARTIFACT_MISSING', 'frame sidecar')
            _require(type(sidecar) is dict and sidecar.get('schema') == FRAME_SCHEMA,
                     'FRAME_SIDECAR_MISMATCH', 'frame %s sidecar schema mismatch' % frame_id)
            for key, want in (('frame_id', frame_id), ('session_id', session_id),
                              ('split', split), ('scenario', scenario), ('iq_sha256', actual)):
                _require(sidecar.get(key) == want, 'FRAME_SIDECAR_MISMATCH',
                         'frame %s sidecar %s=%r, declaration says %r'
                         % (frame_id, key, sidecar.get(key), want))
            _require(_pairs(sidecar.get('rf'), 'FRAME_SIDECAR_MISMATCH',
                            'frame %s rf' % frame_id) == domain, 'FRAME_DOMAIN_MISMATCH',
                     'frame %s readback domain differs from the declared set domain' % frame_id)
            identity = sidecar.get('identity')
            _require(type(identity) is dict and identity.get('capture_id'),
                     'FRAME_SIDECAR_MISMATCH', 'frame %s needs a capture identity' % frame_id)
            # The collected label and its mode travel with the frame; the check is
            # one-directional only in that absent label fields mean "written before
            # the calibration mode existed", never "free to disagree".
            check_collected_label(sidecar, declared_modes[session_id], frame_id)
            entries.append(dict(frame_id=frame_id, source_id=source_id, session_id=session_id,
                                split=split, scenario=scenario, iq_path=iq_path,
                                metadata_path=metadata_path, iq_sha256=actual, raw=raw,
                                sidecar=sidecar,
                                collected_label=sidecar.get('collected_label'),
                                collected_reason=sidecar.get('collected_reason'),
                                provisional_unknown=bool(sidecar.get('provisional_unknown')),
                                metadata_sha256=_file_sha256(metadata_path)))
            split_of.setdefault(('source', source_id), set()).add(split)
            split_of.setdefault(('session', session_id), set()).add(split)
            split_of.setdefault(('iq', actual), set()).add(split)
        _require(counts['normal'] == PLAN_NORMAL_PER_SESSION
                 and counts['off'] == PLAN_OFF_PER_SESSION, 'SESSION_SCENARIO_MIX',
                 'session %s must hold %d normal + %d off frames, not %r'
                 % (session_id, PLAN_NORMAL_PER_SESSION, PLAN_OFF_PER_SESSION, counts))
    for (kind, value), splits in split_of.items():
        _require(len(splits) == 1, 'SPLIT_LEAKAGE',
                 '%s %r appears in splits %s' % (kind, value, sorted(splits)))
    for field in ('source_id', 'iq_sha256'):
        seen = {}
        for entry in entries:
            previous = seen.setdefault(entry[field], entry['frame_id'])
            _require(previous == entry['frame_id'], 'DUPLICATE_FRAME_IDENTITY',
                     '%s %r is used by %s and %s'
                     % (field, entry[field], previous, entry['frame_id']))
    for session in sessions:
        seeds = [frame.get('seed') for frame in session['frames']]
        declared_seed = session.get('seed')
        _require(all(seed == declared_seed for seed in seeds) and declared_seed is not None,
                 'SESSION_SEED_MISMATCH',
                 'session %s frames must all carry the session seed' % session['session_id'])
    ordered = {split: [session for session in sessions if session['split'] == split]
               for split in SPLITS}
    for split in SPLITS:
        _require([session['seed'] for session in ordered[split]] == PLAN_SEEDS[split],
                 'SESSION_SEED_MISMATCH',
                 '%s session seeds must be %r in order' % (split, PLAN_SEEDS[split]))
    try:
        validate_splits([dict(source_id=entry['source_id'], session_id=entry['session_id'],
                              iq_sha256=entry['iq_sha256'], split=entry['split'])
                         for entry in entries])
    except ValueError as exc:
        raise CalibrationError('SPLIT_LEAKAGE', str(exc))
    return domain, entries


def baseline_sources(entries):
    """The only provenance the baseline schema accepts: explicitly grouped TRAIN rows."""
    rows = [dict(source_id=entry['source_id'], session_id=entry['session_id'],
                 iq_sha256=entry['iq_sha256'], split=entry['split']) for entry in entries]
    offending = sorted(set(row['split'] for row in rows if row['split'] != 'train'))
    if offending:
        raise CalibrationError('BASELINE_SOURCE_NOT_TRAIN',
            'baseline sources may only be split=train, got %s' % offending)
    return rows


def frame_and_envelope(entry):
    """Rebuild the frame and its BPSK envelope through the production replay path."""
    from rf_bpsk_pipeline import DemodConfig, analyze_demod
    from rf_bpsk_receiver import BpskConfig
    from rf_rx_types import RxIqFrame, RxReadback
    sidecar = entry['sidecar']
    identity = dict(sidecar.get('identity') or {})
    submitted = sidecar.get('submitted')
    if type(submitted) is not dict or type(submitted.get('config')) is not dict:
        raise CalibrationError('FRAME_SIDECAR_MISMATCH',
                               'frame %s needs a submitted demod block' % entry['frame_id'])
    try:
        frame = RxIqFrame(capture_id=identity['capture_id'], payload=entry['raw'],
                          samples=sidecar['samples'], sample_rate_hz=sidecar['sample_rate_hz'],
                          ratio=sidecar['ratio'],
                          daemon_timestamp_ns=identity.get('daemon_timestamp_ns', 0),
                          connection_generation=identity['connection_generation'],
                          config_revision=identity['config_revision'],
                          request_id=identity.get('request_id', 0),
                          release_id=identity.get('release_id'), op=identity.get('op', 'rx.capture'),
                          physical=identity.get('physical', True),
                          pre_readback=RxReadback(True),
                          post_readback=RxReadback(True, rx_armed=False, tx_source_selected=False),
                          completed_at_monotonic=identity['completed_at_monotonic'])
        config = DemodConfig(BpskConfig(**submitted['config']), submitted['revision'],
                             submitted['decim'], submitted['rx_len'], submitted['input_fs_hz'])
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationError('FRAME_SIDECAR_MISMATCH',
                               'frame %s cannot be rebuilt: %s' % (entry['frame_id'], exc))
    return frame, analyze_demod(frame, config)


def load_check(baseline_path, truth_source_dir=None, expected_sha256=None,
               require_formal=False):
    """Read a baseline back through the production loader and prove equality.

    `require_formal` is the formal packaging gate: the bare body is exactly what the
    product loader consumes, and the provisional labels live only in the wrapper, so a
    formal entry point must ask for a body that is not the n=1 development bootstrap.
    """
    value = _read_json(baseline_path, 'BASELINE_SCHEMA', 'baseline')
    if type(value) is dict and value.get('schema') == PROVISIONAL_SCHEMA:
        for key, want in PROVISIONAL_FLAGS.items():
            if value.get(key) != want:
                raise CalibrationError('PROVISIONAL_FLAGS_MISSING',
                                       'provisional wrapper %s must declare %s=%r'
                                       % (baseline_path, key, want))
        body = value.get('baseline')
    else:
        body = value
    _require(type(body) is dict, 'BASELINE_SCHEMA', 'baseline body must be a JSON object')
    _require(set(body) == set(BASELINE_BODY_FIELDS) | set(['sha256']), 'BASELINE_SCHEMA',
             'baseline body must be the eight canonical fields plus sha256, got %s'
             % sorted(body))
    canonical = _digest(dict((key, body[key]) for key in BASELINE_BODY_FIELDS))
    _require(body['sha256'] == canonical, 'BASELINE_CANONICAL_SHA_MISMATCH',
             'file sha256 %s, canonical sha256 %s' % (body['sha256'], canonical))
    if expected_sha256 is not None:
        _require(body['sha256'] == expected_sha256, 'BASELINE_CANONICAL_SHA_MISMATCH',
                 'expected %s, file carries %s' % (expected_sha256, body['sha256']))
    if require_formal:
        _require(body['calibration_status'] != 'development' or len(body['sources']) >= 2,
                 'PROVISIONAL_BASELINE_NOT_FORMAL',
                 'a formal entry requires calibration_status != "development" or at least '
                 'two sources; this body declares calibration_status=%r with %d source(s)'
                 % (body['calibration_status'], len(body['sources'])))
    audit = domain_audit(_pairs(body['rf_config'], 'BASELINE_SCHEMA',
                                'baseline rf_config')['attenuation_mdB'], truth_source_dir)
    require_declared_domain(audit, require_product_agreement=False)
    with environment_in_domain(truth_source_dir) as environment:
        try:
            restored = environment.EnvironmentBaseline.from_dict(json.loads(json.dumps(body)))
        except ValueError as exc:
            raise CalibrationError('BASELINE_LOAD_NOT_EQUAL',
                                   'the production loader refused the body: %s' % exc)
        loaded = json.loads(json.dumps(restored.to_dict()))
    _require(loaded == body, 'BASELINE_LOAD_NOT_EQUAL',
             'the loaded baseline does not equal the stored body')
    return dict(loaded=True, sha256=restored.sha256, canonical_sha256=canonical,
                environment_version=restored.environment_version,
                algorithm_version=restored.algorithm_version,
                calibration_status=restored.calibration_status,
                rf_config=dict(restored.rf_config),
                sources=[dict(source) for source in restored.sources], body_equal=True,
                spectrum_envelope_values=len(restored.spectrum_envelope),
                thresholds=dict(restored.thresholds), domain_audit=audit)


def fit_from_set(set_dir, output_dir, truth_source_dir=None, overwrite=False,
                 require_physical=False):
    """Fit the baseline from TRAIN only; VAL is evaluated as-is and never fitted."""
    set_dir, output_dir = Path(set_dir), Path(output_dir)
    declaration = _read_json(set_dir / 'set.json', 'CAL_SET_SCHEMA', 'set declaration')
    domain, entries = validate_set(declaration, set_dir)
    if require_physical:
        _require(declaration['provenance']['kind'] == 'new_physical_train_val',
                 'SET_NOT_PHYSICAL',
                 'a formal TRAIN/VAL fit requires provenance kind '
                 'new_physical_train_val, this set declares %r'
                 % (declaration['provenance']['kind'],))
    audit = require_declared_domain(domain_audit(domain['attenuation_mdB'], truth_source_dir),
                                    require_product_agreement=True,
                                    truth_source_dir=truth_source_dir)
    source_set_sha = _file_sha256(set_dir / 'set.json')
    train = [entry for entry in entries if entry['split'] == 'train']
    val = [entry for entry in entries if entry['split'] == 'val']
    train_normal = [entry for entry in train if entry['scenario'] == 'normal']
    val_normal = [entry for entry in val if entry['scenario'] == 'normal']
    negatives = [entry for entry in entries if entry['scenario'] == 'off']
    _require(len(train_normal) == PLAN_TRAIN_SESSIONS * PLAN_NORMAL_PER_SESSION
             and len(val_normal) == PLAN_VAL_SESSIONS * PLAN_NORMAL_PER_SESSION,
             'SPLIT_PLAN_MISMATCH', 'normal frame counts do not match the matrix')
    sources = baseline_sources(train_normal)
    frames, envelopes = [], []
    with environment_in_domain(truth_source_dir) as environment:
        for entry in train_normal:
            frame, envelope = frame_and_envelope(entry)
            report = environment.analyze_environment(frame, envelope, dict(environment.FIXED_RF),
                                                     None)
            if report.label == 'INVALID':
                raise CalibrationError('TRAIN_FRAME_NOT_QUALIFIED',
                    'TRAIN normal frame %s is %s (%s)'
                    % (entry['frame_id'], report.label, report.reason))
            frames.append(frame)
            envelopes.append(envelope)
            entry['environment'] = report.to_dict()
        try:
            baseline = environment.fit_baseline(frames, envelopes, dict(environment.FIXED_RF),
                                                sources, calibration_status='development')
        except ValueError as exc:
            raise CalibrationError('TRAIN_FRAME_NOT_QUALIFIED', str(exc))
        for entry in val_normal + negatives:
            frame, envelope = frame_and_envelope(entry)
            entry['environment'] = environment.analyze_environment(
                frame, envelope, dict(environment.FIXED_RF), baseline).to_dict()
        body = json.loads(json.dumps(baseline.to_dict()))
        _require(bool(body['sources']) and all(source['split'] == 'train'
                                              for source in body['sources']),
                 'BASELINE_SOURCE_NOT_TRAIN', 'a fitted baseline carries only TRAIN sources')
    val_clear = sum(entry['environment']['label'] == 'CLEAR' for entry in val_normal)
    val_misclassified = sorted(set(entry['environment']['label'] for entry in val_normal
                                   if entry['environment']['label'] in ('NARROWBAND', 'PULSE')))
    false_clear = [entry['frame_id'] for entry in negatives
                   if entry['environment']['label'] == 'CLEAR']
    invalid_normal = [entry['frame_id'] for entry in train_normal + val_normal
                      if entry['environment']['label'] == 'INVALID']
    passed = (val_clear == len(val_normal) and not val_misclassified and not false_clear
              and not invalid_normal and len(train_normal) == 20 and len(val_normal) == 10)
    provisional_frames = [entry['frame_id'] for entry in entries
                          if entry.get('provisional_unknown')]
    receipt = dict(
        schema=RECEIPT_SCHEMA, entry='scripts/rf_environment_calibration.py fit',
        scope='offline TRAIN/VAL calibration; zero new physical frames; no board/RF/authorization',
        provenance=declaration['provenance'], set=str(set_dir.resolve()),
        set_sha256=source_set_sha, plan=declaration['plan'], domain=domain, domain_audit=audit,
        # How the frames were collected, carried for the record.  The mode is a
        # declaration of the collection gate only: everything below (TRAIN-only fit,
        # 10/10 CLEAR VAL, zero OFF false-CLEAR, no manual threshold) is unconditional.
        collection_mode=declared_collection_mode(declaration.get('collection_mode')),
        collection_modes=sorted({declared_collection_mode(session.get('collection_mode'))
                                 for session in declaration['sessions']}),
        provisional_unknown_frames=provisional_frames,
        collection_mode_note='the collection mode never relaxes these criteria; the baseline '
                             'is fitted from the 20 TRAIN normal frames only',
        environment_version=body['environment_version'],
        algorithm_version=body['algorithm_version'],
        counts=dict(train_sessions=sum(1 for session in declaration['sessions']
                                       if session['split'] == 'train'),
                    val_sessions=sum(1 for session in declaration['sessions']
                                     if session['split'] == 'val'),
                    train_frames=len(train), val_frames=len(val),
                    train_normal=len(train_normal), val_normal=len(val_normal),
                    negative_off=len(negatives), test_frames=0),
        train_frame_ids=[entry['frame_id'] for entry in train_normal],
        val_frame_ids=[entry['frame_id'] for entry in val_normal],
        baseline_body_fields=list(BASELINE_BODY_FIELDS), canonical_json_rule=CANONICAL_RULE,
        baseline_canonical_sha256=body['sha256'], thresholds=body['thresholds'],
        spectrum_envelope_source='per-bin max over the TRAIN normal frames (8-bin rolling max)',
        sources=[dict(source) for source in body['sources']],
        val_results=[dict(frame_id=entry['frame_id'], session_id=entry['session_id'],
                          scenario=entry['scenario'], label=entry['environment']['label'],
                          reason=entry['environment']['reason']) for entry in val_normal],
        negative_results=[dict(frame_id=entry['frame_id'], session_id=entry['session_id'],
                               split=entry['split'], label=entry['environment']['label'],
                               reason=entry['environment']['reason']) for entry in negatives],
        verdict=dict(passed=passed, val_normal_clear=val_clear, val_normal_total=len(val_normal),
                     val_misclassified=val_misclassified, negative_false_clear=false_clear,
                     invalid_normal=invalid_normal),
        physical_claim_source='the set declaration only; this entry measures no hardware',
        formal_physical=('NOT_RUN' if declaration['provenance']['kind']
                         == 'offline_synthetic_fixture'
                         else 'DECLARED_BY_SET_NOT_VERIFIED_HERE'),
        not_run=['physical TRAIN/VAL sessions: NOT_RUN', 'development pilot: NOT_RUN',
                 'formal TEST: NOT_RUN', 'human acceptance: NOT_RUN'])
    if passed:
        load = load_check(_write_json(output_dir / 'baseline.json', body, overwrite=overwrite),
                          truth_source_dir, expected_sha256=body['sha256'])
        receipt['load_verification'] = load
        receipt['baseline_file'] = str((output_dir / 'baseline.json').resolve())
        receipt['baseline_file_sha256'] = _file_sha256(output_dir / 'baseline.json')
    else:
        receipt['load_verification'] = None
        receipt['baseline_file'] = None
        receipt['baseline_file_sha256'] = None
    _write_json(output_dir / 'calibration-receipt.json', receipt, overwrite=overwrite)
    if not passed:
        raise CalibrationError('CALIBRATION_VERDICT_FAIL',
            'VAL/negative criteria not met; receipt written to %s and no baseline emitted'
            % (output_dir / 'calibration-receipt.json'))
    return receipt


def fixture_from_domain(domain, output_dir, overwrite=False):
    """Deterministic offline fixture: 3 sessions x (10 normal + 1 off), zero hardware."""
    import numpy as np
    from rf_experiment_scenarios import ScenarioConfig, generate_scenario
    from rf_iq_signal import prbs7_bits
    output_dir = Path(output_dir)
    if output_dir.exists() and not overwrite:
        raise CalibrationError('OUTPUT_EXISTS', str(output_dir))
    domain = _pairs(dict(domain), 'DOMAIN_INVALID', 'fixture domain')
    sessions, rows = [], []
    for index in range(PLAN_TRAIN_SESSIONS + PLAN_VAL_SESSIONS):
        split = 'train' if index < PLAN_TRAIN_SESSIONS else 'val'
        position_in_split = index if split == 'train' else index - PLAN_TRAIN_SESSIONS
        seed = PLAN_SEEDS[split][position_in_split]
        session_id = 'fixture-%s-%02d' % (split, index)
        folder = '%s-%02d' % (split, index)
        frames = []
        for position in range(PLAN_FRAMES_PER_SESSION):
            scenario = 'off' if position == PLAN_NORMAL_PER_SESSION else 'normal'
            frame_id = '%s-f%02d' % (session_id, position)
            source_id = 'fixture-source-%s' % frame_id
            if scenario == 'normal':
                bits = np.asarray(prbs7_bits(seed, 127))
                samples = np.arange(4096)
                symbols = (samples + (position * 3 + index) % 20) // 20
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
                # Off is the same window with no source selected: receiver noise only,
                # still unique per frame so no two frames can share an IQ digest.
                off_rng = np.random.RandomState(seed * 1000 + position + 500)
                off = 1.5 * (off_rng.normal(size=4096) + 1j * off_rng.normal(size=4096))
                codes = np.rint(np.column_stack((off.real, off.imag)))
            payload = codes.astype('<i2').tobytes()
            digest = _sha256_bytes(payload)
            iq_rel = '%s/f%02d.iq' % (folder, position)
            meta_rel = '%s/f%02d.json' % (folder, position)
            iq_path = output_dir / iq_rel
            iq_path.parent.mkdir(parents=True, exist_ok=True)
            iq_path.write_bytes(payload)
            _write_json(output_dir / meta_rel, dict(
                schema=FRAME_SCHEMA, frame_id=frame_id, session_id=session_id, split=split,
                scenario=scenario, seed=seed, reference_prbs7_seed=seed, iq_sha256=digest,
                samples=4096, sample_rate_hz=60000000, ratio=0, rf=domain,
                identity=dict(capture_id='fixture:%s' % frame_id, release_id='offline-fixture',
                              connection_generation=0, config_revision=0,
                              completed_at_monotonic=float(position + 1), request_id=position,
                              daemon_timestamp_ns=0, op='rx.capture', physical=False),
                submitted=dict(config=dict(mode='BPSK', symbol_rate_hz=3000000, shaping='RECT',
                                           reference_prbs7_seed=seed),
                               revision=0, decim=1, rx_len=4096, input_fs_hz=60000000),
                truth_declaration=generate_scenario(ScenarioConfig(
                    kind='off' if scenario == 'off' else 'normal', amplitude=60,
                    prbs7_seed=seed)).declaration_dict(),
                origin='offline synthetic fixture frame; not a physical capture'),
                overwrite=True)
            frames.append(dict(frame_id=frame_id, source_id=source_id, scenario=scenario,
                               seed=seed, iq=iq_rel, metadata=meta_rel, iq_sha256=digest))
            rows.append(dict(frame_id=frame_id, split=split, scenario=scenario, iq_sha256=digest))
        sessions.append(dict(session_id=session_id, split=split, seed=seed, frames=frames))
    _write_json(output_dir / 'set.json', dict(
        schema=SET_SCHEMA, plan=_plan_summary(), domain=domain, sessions=sessions,
        provenance=dict(kind='offline_synthetic_fixture',
                        detail='deterministic synthetic BPSK/off frames; zero physical frames'),
        fixture=dict(generator='scripts/rf_environment_calibration.py fixture',
                     normal='PRBS7 BPSK, 20 samples/symbol, amplitude 60..63, seeded 1.5-code noise',
                     off='zero-code window', not_for_formal_acceptance=True)), overwrite=True)
    return dict(set=str(output_dir.resolve()), sessions=len(sessions), frames=len(rows),
                normal=sum(row['scenario'] == 'normal' for row in rows),
                off=sum(row['scenario'] == 'off' for row in rows),
                set_sha256=_file_sha256(output_dir / 'set.json'),
                provenance=dict(kind='offline_synthetic_fixture',
                                detail='deterministic synthetic BPSK/off frames; '
                                       'zero physical frames'))


def _replay_binding(recorded, recomputed):
    left = json.loads(json.dumps(recorded))
    right = json.loads(json.dumps(recomputed))
    differences = {}
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            differences[key] = ('float_delta=%.3e' % abs(left[key] - right[key])
                                if isinstance(left.get(key), float)
                                and isinstance(right.get(key), float) else 'MISMATCH')
    return differences


def provisional_from_frame(frame_dir, output_dir, session_dir=None, truth_source_dir=None,
                           overwrite=False):
    """One archived research normal frame -> explicitly provisional n=1 development baseline."""
    frame_dir = Path(frame_dir)
    session_dir = Path(session_dir) if session_dir else frame_dir.parent
    output_dir = Path(output_dir)
    header_doc = _read_json(frame_dir / 'header.json', 'RESEARCH_FRAME_MISSING', 'frame header')
    analysis_doc = _read_json(frame_dir / 'analysis.json', 'RESEARCH_FRAME_MISSING',
                              'frame analysis')
    replay_doc = _read_json(frame_dir / 'replay.json', 'RESEARCH_FRAME_MISSING', 'frame replay')
    run_gate = _read_json(session_dir / 'run-gate.json', 'RESEARCH_FRAME_BINDING',
                          'session run gate')
    header = header_doc.get('header') or {}
    headroom = header.get('headroom') or {}
    analysis = analysis_doc.get('summary') or {}
    replay = replay_doc.get('summary') or {}
    recorded = (replay.get('analysis') or {}).get('demod') or {}
    recorded_result = recorded.get('result') or {}
    raw_path = frame_dir / 'frame.iq'
    _require(raw_path.is_file() and not raw_path.is_symlink()
             and raw_path.stat().st_size == IQ_BYTES, 'RESEARCH_FRAME_MISSING', str(raw_path))
    raw = raw_path.read_bytes()
    digest = _sha256_bytes(raw)
    _require(digest == headroom.get('iq_sha256') == (analysis.get('payload') or {}).get('sha256')
             == analysis.get('iq_sha256'), 'RESEARCH_FRAME_BINDING',
             'frame bytes, header and analysis disagree on the IQ digest')
    attempt = header_doc.get('attempt')
    gate = [row for row in (run_gate.get('attempts') or []) if row.get('attempt') == attempt]
    _require(len(gate) == 1 and gate[0].get('iq_sha256') == digest
             and gate[0].get('iq_bytes') == IQ_BYTES and gate[0].get('iq_available') is True
             and gate[0].get('observation_class') == 'NEW_PHYSICAL_FRAME'
             and (gate[0].get('decision') or {}).get('accepted') is True
             and gate[0].get('hardware_verified') is True, 'RESEARCH_FRAME_BINDING',
             'attempt %r is not an accepted hardware-verified physical frame in %s'
             % (attempt, session_dir / 'run-gate.json'))
    _require(headroom.get('scenario') == 'normal', 'RESEARCH_FRAME_NOT_NORMAL',
             'provisional baseline requires a normal frame, got %r' % headroom.get('scenario'))
    _require(headroom.get('rail_count') == 0 and analysis.get('rail_count') == 0
             and analysis.get('simulated') is False and headroom.get('source_enabled') is True,
             'RESEARCH_FRAME_RAIL', 'frame carries a rail/clip or a simulated/off status')
    _require(header.get('samples') == 4096 and header.get('sample_rate_hz') == 60000000
             and header.get('ratio') == 0 and header.get('type') == 'research_iq_frame',
             'RESEARCH_FRAME_DOMAIN', 'frame is not the 4096-sample 60 MSPS research domain')
    after = headroom.get('actual_rf_after') or {}
    before = headroom.get('actual_rf_before') or {}
    attenuations = set([after.get('attenuation_mdB'), after.get('tx1_attenuation_mdB'),
                        after.get('tx2_attenuation_mdB'), before.get('attenuation_mdB')])
    _require(len(attenuations) == 1 and type(list(attenuations)[0]) is int,
             'RESEARCH_FRAME_DOMAIN',
             'RF readback attenuations disagree: %s' % sorted(attenuations))
    declared = list(attenuations)[0]
    audit = require_declared_domain(domain_audit(declared, truth_source_dir),
                                    require_product_agreement=False,
                                    truth_source_dir=truth_source_dir)
    binding = dict(frame=str(frame_dir.resolve()), session=str(session_dir.resolve()),
                   attempt=attempt, header_sha256=_file_sha256(frame_dir / 'header.json'),
                   analysis_sha256=_file_sha256(frame_dir / 'analysis.json'),
                   replay_sha256=_file_sha256(frame_dir / 'replay.json'),
                   run_gate_sha256=_file_sha256(session_dir / 'run-gate.json'),
                   frame_iq_sha256=digest, run_gate_attempt=dict(gate[0]))
    entry = dict(frame_id='research-attempt-%s-normal' % attempt,
                 source_id='research-normal-%s' % digest[:12],
                 session_id=headroom.get('session_id'), split='train', scenario='normal',
                 iq_path=raw_path, raw=raw, iq_sha256=digest,
                 sidecar=dict(schema=FRAME_SCHEMA,
                              identity=dict(capture_id=recorded_result.get('capture_id'),
                                            release_id=(replay.get('analysis') or {}).get(
                                                'release_id'),
                                            connection_generation=recorded_result.get(
                                                'connection_generation'),
                                            config_revision=recorded_result.get(
                                                'config_revision'),
                                            completed_at_monotonic=recorded_result.get(
                                                'completed_at_monotonic'),
                                            request_id=header.get('id'),
                                            daemon_timestamp_ns=header.get('timestamp_ns'),
                                            op='rx.capture', physical=True),
                              samples=header.get('samples'),
                              sample_rate_hz=header.get('sample_rate_hz'),
                              ratio=header.get('ratio'), submitted=recorded.get('submitted')))
    _require(bool(entry['session_id']) and bool(entry['sidecar']['identity']['release_id']),
             'RESEARCH_FRAME_BINDING', 'frame identity is incomplete')
    frame, envelope = frame_and_envelope(entry)
    differences = _replay_binding(recorded_result, envelope.result.to_dict())
    _require(envelope.algorithm_version == recorded.get('algorithm_version') and not differences,
             'RESEARCH_FRAME_BINDING',
             'the rebuilt replay differs from the recorded replay: %s' % differences)
    _require(envelope.result.reference_sync is True and envelope.result.ber == 0
             and envelope.result.compared_bits and envelope.result.compared_bits >= 32,
             'RESEARCH_FRAME_NOT_QUALIFIED',
             'the archived frame is not a qualified reference-synced zero-BER normal frame')
    sources = baseline_sources([entry])
    with environment_in_domain(truth_source_dir) as environment:
        report = environment.analyze_environment(frame, envelope, dict(environment.FIXED_RF), None)
        _require(report.label != 'INVALID', 'RESEARCH_FRAME_NOT_QUALIFIED',
                 'the archived frame is INVALID (%s)' % report.reason)
        baseline = environment.fit_baseline([frame], [envelope], dict(environment.FIXED_RF),
                                            sources, calibration_status='development')
        body = json.loads(json.dumps(baseline.to_dict()))
        post_hoc = environment.analyze_environment(frame, envelope,
                                                   dict(environment.FIXED_RF), baseline).to_dict()
    _write_json(output_dir / 'provisional-baseline.body.json', body, overwrite=overwrite)
    body_file_sha = _file_sha256(output_dir / 'provisional-baseline.body.json')
    load = load_check(output_dir / 'provisional-baseline.body.json', truth_source_dir,
                      expected_sha256=body['sha256'])
    must_not = ['formal TRAIN/VAL evidence', 'formal TEST or acceptance',
                'threshold or domain validation', 'any claim of physical qualification',
                'a variance or consistency statement (n=1)']
    receipt = dict(
        schema=RECEIPT_SCHEMA, entry='scripts/rf_environment_calibration.py provisional',
        scope='provisional development bootstrap from ONE archived 46 dB normal research frame',
        not_for_formal_acceptance=True, provisional=True, n=1, formal_train_val=False,
        test_frames=0, train_sessions=0, val_sessions=0,
        sources_role='the baseline schema accepts only split=train; this single archived '
                     'research frame is NOT a TRAIN session and must never be counted as one',
        must_not_be_used_for=must_not, domain_audit=audit, record_binding=binding,
        recorded_replay=dict(reference_sync=envelope.result.reference_sync,
                             ber=envelope.result.ber, compared_bits=envelope.result.compared_bits,
                             carrier_coherence=envelope.result.carrier_coherence,
                             timing_quality=envelope.result.timing_quality,
                             reference_correlation=envelope.result.reference_correlation,
                             archived_environment_label=replay.get('environment_label'),
                             archived_environment_reason=replay.get('environment_reason')),
        rebuild_binding=dict(differing_fields=sorted(differences),
                             recorded_algorithm_version=recorded.get('algorithm_version'),
                             rebuilt_equals_recorded=not differences),
        baseline_body_fields=[field for field in BASELINE_BODY_FIELDS],
        canonical_json_rule=CANONICAL_RULE, baseline_canonical_sha256=body['sha256'],
        baseline_body_file=str((output_dir / 'provisional-baseline.body.json').resolve()),
        baseline_body_file_sha256=body_file_sha, thresholds=body['thresholds'],
        sources=[dict(source) for source in body['sources']],
        post_hoc_self_label=dict(label=post_hoc['label'], reason=post_hoc['reason'],
                                 not_evidence_of_classification_correctness=True),
        load_verification=load,
        not_run=['formal TRAIN/VAL: NOT_RUN', 'development pilot: NOT_RUN', 'formal TEST: NOT_RUN',
                 'physical re-measurement of this frame: NOT_RUN', 'human acceptance: NOT_RUN'])
    wrapper = dict(schema=PROVISIONAL_SCHEMA, **PROVISIONAL_FLAGS)
    wrapper.update(role='development bootstrap only: lets a 46 dB development candidate load '
                        'the environment engine',
                   formal_train_val=False, test_frames=0,
                   must_not_be_used_for=must_not, domain_audit=audit, record_binding=binding,
                   baseline_sha256=body['sha256'], baseline=body,
                   baseline_body_file='provisional-baseline.body.json',
                   baseline_body_file_sha256=body_file_sha, load_verification=load,
                   receipt=receipt)
    _write_json(output_dir / 'provisional-baseline.json', wrapper, overwrite=overwrite)
    return wrapper


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan', help='emit or check the TRAIN/VAL plan (no frames)')
    plan.add_argument('--plan', type=Path, help='validate this plan document')
    plan.add_argument('--output', type=Path, help='write the canonical plan document here')
    plan.add_argument('--overwrite', action='store_true')
    fixture = commands.add_parser('fixture', help='write a deterministic offline fixture set')
    fixture.add_argument('--output', type=Path, required=True)
    fixture.add_argument('--domain-mdB', type=int)
    fixture.add_argument('--overwrite', action='store_true')
    fit = commands.add_parser('fit', help='validate a set, fit TRAIN, evaluate VAL as-is')
    fit.add_argument('--set', type=Path, required=True)
    fit.add_argument('--output', type=Path, required=True)
    fit.add_argument('--overwrite', action='store_true')
    fit.add_argument('--require-physical', action='store_true',
                     help='refuse a set whose provenance is an offline fixture')
    provisional = commands.add_parser('provisional', help='one archived normal frame -> n=1')
    provisional.add_argument('--frame', type=Path, required=True)
    provisional.add_argument('--session-dir', type=Path)
    provisional.add_argument('--output', type=Path, required=True)
    provisional.add_argument('--truth-source', type=Path)
    provisional.add_argument('--overwrite', action='store_true')
    check = commands.add_parser('load-check', help='read a baseline back through the loader')
    check.add_argument('--baseline', type=Path, required=True)
    check.add_argument('--truth-source', type=Path)
    check.add_argument('--expected-sha256')
    check.add_argument('--require-formal', action='store_true',
                       help='refuse a development body with fewer than two sources '
                            '(the provisional n=1 bootstrap)')
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            if args.plan:
                result = check_plan(_read_json(args.plan, 'PLAN_SCHEMA', 'plan document'))
            else:
                document = plan_document()
                result = check_plan(document)
                if args.output:
                    _write_json(args.output, document, overwrite=args.overwrite)
                    result['plan_file'] = str(args.output.resolve())
        elif args.command == 'fixture':
            domain = dict(_profile_domain(TEACHING_PROFILE)[0])
            if args.domain_mdB is not None:
                domain['attenuation_mdB'] = args.domain_mdB
            result = fixture_from_domain(domain, args.output, overwrite=args.overwrite)
        elif args.command == 'fit':
            result = fit_from_set(args.set, args.output, overwrite=args.overwrite,
                                  require_physical=args.require_physical)
        elif args.command == 'provisional':
            result = provisional_from_frame(args.frame, args.output,
                                            session_dir=args.session_dir,
                                            truth_source_dir=args.truth_source,
                                            overwrite=args.overwrite)
        else:
            result = load_check(args.baseline, truth_source_dir=args.truth_source,
                                expected_sha256=args.expected_sha256,
                                require_formal=args.require_formal)
    except CalibrationError as exc:
        print(json.dumps(exc.to_dict(), indent=2, ensure_ascii=True))
        return 3
    print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
