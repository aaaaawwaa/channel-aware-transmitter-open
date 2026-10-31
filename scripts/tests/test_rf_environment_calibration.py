"""Independent TRAIN/VAL calibration entry: group validation, baseline, provisional path.

Offline only.  The positive case runs on a deterministic synthetic fixture; the sealed
46 dB research frame is read (never written) for the provisional case and the test skips
if that evidence is absent.  Every negative case asserts a named refusal code.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import rf_environment_calibration as CAL   # noqa: E402
import rf_environment as ENV                            # noqa: E402

SEALED_FRAME = (ROOT / 'evidence' / 'rf-teaching'
                / 's2-headroom-research-attempt4-2026-10-17' / 'board-download' / 'session'
                / 'attempt-2')
PRODUCT_MDB = dict(CAL._profile_domain(CAL.TEACHING_PROFILE)[0])['attenuation_mdB']


def refusal(code, function, *args, **kwargs):
    with pytest.raises(CAL.CalibrationError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value


def declaration(set_dir):
    return json.loads((set_dir / 'set.json').read_text(encoding='utf-8'))


def save_declaration(set_dir, document):
    (set_dir / 'set.json').write_text(json.dumps(document, indent=2), encoding='utf-8')


def sessions_of(document, split):
    return [session for session in document['sessions'] if session['split'] == split]


def entry_of(document, split, position=0):
    return sessions_of(document, split)[0]['frames'][position]


def sidecar_of(set_dir, frame):
    return json.loads((set_dir / frame['metadata']).read_text(encoding='utf-8'))


def save_sidecar(set_dir, frame, document):
    (set_dir / frame['metadata']).write_text(json.dumps(document, indent=2), encoding='utf-8')


def rebind(set_dir, document, frame, payload):
    """Replace one frame payload and keep declaration, sidecar and digest consistent."""
    import hashlib
    (set_dir / frame['iq']).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    frame['iq_sha256'] = digest
    sidecar = sidecar_of(set_dir, frame)
    sidecar['iq_sha256'] = digest
    save_sidecar(set_dir, frame, sidecar)
    save_declaration(set_dir, document)
    return digest


@pytest.fixture(scope='module')
def fixture_set(tmp_path_factory):
    root = tmp_path_factory.mktemp('calibration-fixture')
    CAL.fixture_from_domain(dict(CAL._profile_domain(CAL.TEACHING_PROFILE)[0]), root / 'set')
    return root / 'set'


@pytest.fixture(scope='module')
def fitted(fixture_set, tmp_path_factory):
    output = tmp_path_factory.mktemp('calibration-fit') / 'fit'
    return CAL.fit_from_set(fixture_set, output), output


@pytest.fixture
def a_set(fixture_set, tmp_path):
    def make(name='set-copy'):
        target = tmp_path / name
        shutil.copytree(str(fixture_set), str(target))
        return target
    return make


def overlay_dir(tmp_path, *, attenuation=None, symbol_rate=None, name='overlay'):
    """The audited post-migration truth source used to prove the domain follows one file."""
    source = CAL.TEACHING_PROFILE.read_text(encoding='utf-8')
    if attenuation is not None:
        source = source.replace('ATTENUATION_MDB = %d' % PRODUCT_MDB,
                                'ATTENUATION_MDB = %d' % attenuation)
    if symbol_rate is not None:
        source = source.replace('symbol_rate_hz: int = 3000000',
                                'symbol_rate_hz: int = %d' % symbol_rate)
    target = tmp_path / name
    target.mkdir(exist_ok=True)
    (target / 'rf_teaching_profile.py').write_text(source, encoding='utf-8')
    return target


# --------------------------------------------------------------------------- plan

def test_plan_is_emitted_as_a_plan_with_zero_collected_frames(tmp_path):
    document = CAL.plan_document()
    summary = CAL.check_plan(document)
    assert summary['total_frames'] == 33 and summary['frames_collected'] == 0
    assert summary['splits']['train']['sessions'] == 2
    assert summary['splits']['val']['sessions'] == 1
    assert all('NOT_RUN' in row for row in summary['not_run'])
    path = tmp_path / 'plan.json'
    assert CAL.main(['plan', '--output', str(path)]) == 0
    assert CAL.check_plan(json.loads(path.read_text(encoding='utf-8')))['total_frames'] == 33


def test_plan_refuses_to_claim_collected_frames():
    document = dict(CAL.plan_document(), collected=True, frames_collected=33)
    refusal('PLAN_NOT_A_PLAN', CAL.check_plan, document)


def test_plan_matrix_mismatch_is_named():
    document = dict(CAL.plan_document(), total_frames=34)
    refusal('PLAN_MATRIX_MISMATCH', CAL.check_plan, document)


def test_fit_refuses_a_plan_used_as_a_collected_set(tmp_path):
    set_dir = tmp_path / 'plan-as-set'
    set_dir.mkdir()
    (set_dir / 'set.json').write_text(json.dumps(CAL.plan_document()), encoding='utf-8')
    refusal('CAL_SET_SCHEMA', CAL.fit_from_set, set_dir, tmp_path / 'out')


# ------------------------------------------------------------------- positive path

def test_legal_train_group_passes_and_produces_a_loadable_baseline(fitted):
    receipt, output = fitted
    assert receipt['verdict'] == dict(passed=True, val_normal_clear=10, val_normal_total=10,
                                      val_misclassified=[], negative_false_clear=[],
                                      invalid_normal=[])
    assert receipt['counts']['train_normal'] == 20 and receipt['counts']['val_normal'] == 10
    assert receipt['counts']['test_frames'] == 0 and receipt['counts']['negative_off'] == 3
    assert len(receipt['baseline_body_fields']) == 8
    assert receipt['formal_physical'] == 'NOT_RUN'
    body = json.loads((output / 'baseline.json').read_text(encoding='utf-8'))
    assert set(body) == set(CAL.BASELINE_BODY_FIELDS) | set(['sha256'])
    assert body['sha256'] == receipt['baseline_canonical_sha256']
    assert all(source['split'] == 'train' for source in body['sources'])
    assert len(body['sources']) == 20 and len(body['spectrum_envelope']) == 4096
    assert body['calibration_status'] == 'development'
    assert body['rf_config']['attenuation_mdB'] == PRODUCT_MDB
    loaded = ENV.EnvironmentBaseline.from_dict(body)
    assert loaded.sha256 == body['sha256']
    assert receipt['load_verification']['loaded'] is True
    assert receipt['load_verification']['body_equal'] is True
    assert receipt['load_verification']['sha256'] == body['sha256']
    assert receipt['domain_audit']['product_truth_sources_agree'] is True
    assert receipt['domain_audit']['code_truth_sources']['pynq_overlay/sw/rf_environment.py']


def test_fitted_baseline_sources_are_the_twenty_train_normal_frames(fitted):
    receipt, _ = fitted
    assert len(receipt['sources']) == 20
    assert all(source['split'] == 'train' for source in receipt['sources'])
    assert len({source['iq_sha256'] for source in receipt['sources']}) == 20
    assert len({source['session_id'] for source in receipt['sources']}) == 2


def test_every_off_frame_is_a_recorded_negative_control(fitted):
    receipt, _ = fitted
    assert len(receipt['negative_results']) == 3
    assert all(row['label'] != 'CLEAR' for row in receipt['negative_results'])
    assert all(row['label'] == 'INVALID' for row in receipt['negative_results'])
    assert all(row['reason'] == 'LOW_ENERGY' for row in receipt['negative_results'])


def test_output_directory_is_never_silently_overwritten(fixture_set, tmp_path):
    output = tmp_path / 'taken'
    CAL.fit_from_set(fixture_set, output)
    refusal('OUTPUT_EXISTS', CAL.fit_from_set, fixture_set, output)


# --------------------------------------------------------------- group validation

def test_cross_split_iq_reuse_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    train = entry_of(document, 'train')
    val = entry_of(document, 'val')
    payload = (set_dir / train['iq']).read_bytes()
    rebind(set_dir, document, val, payload)
    refusal('SPLIT_LEAKAGE', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_a_missing_split_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    document['sessions'] = sessions_of(document, 'train')
    save_declaration(set_dir, document)
    refusal('MISSING_SPLIT', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_a_frame_may_not_contradict_its_session_split(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    entry_of(document, 'train')['split'] = 'val'
    save_declaration(set_dir, document)
    refusal('SESSION_SPLIT_CONFLICT', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_a_test_or_other_split_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    sessions_of(document, 'val')[0]['split'] = 'test'
    save_declaration(set_dir, document)
    refusal('SPLIT_NOT_ALLOWED', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_a_third_train_session_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    extra = json.loads(json.dumps(sessions_of(document, 'train')[1]))
    extra['session_id'] = 'fixture-train-99'
    for position, frame in enumerate(extra['frames']):
        frame['frame_id'] = 'fixture-train-99-f%02d' % position
        frame['source_id'] = 'fixture-source-fixture-train-99-f%02d' % position
        sidecar = sidecar_of(set_dir, frame)
        sidecar['session_id'] = 'fixture-train-99'
        sidecar['frame_id'] = frame['frame_id']
        save_sidecar(set_dir, frame, sidecar)
    document['sessions'].append(extra)
    save_declaration(set_dir, document)
    refusal('SPLIT_PLAN_MISMATCH', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_eleven_frames_per_session_are_required(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    sessions_of(document, 'train')[0]['frames'].pop()
    save_declaration(set_dir, document)
    refusal('SESSION_FRAME_COUNT', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_ten_normal_plus_one_off_per_session_are_required(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    frame = entry_of(document, 'val')
    frame['scenario'] = 'off'
    sidecar = sidecar_of(set_dir, frame)
    sidecar['scenario'] = 'off'
    save_sidecar(set_dir, frame, sidecar)
    save_declaration(set_dir, document)
    refusal('SESSION_SCENARIO_MIX', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_pilot_only_scenarios_are_refused_inside_train_val(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    entry_of(document, 'val')['scenario'] = 'narrowband'
    save_declaration(set_dir, document)
    refusal('SCENARIO_NOT_ALLOWED', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_session_seeds_must_match_the_matrix(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    session = sessions_of(document, 'val')[0]
    session['seed'] = 99
    for frame in session['frames']:
        frame['seed'] = 99
    save_declaration(set_dir, document)
    refusal('SESSION_SEED_MISMATCH', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_a_duplicate_session_id_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    sessions_of(document, 'val')[0]['session_id'] = sessions_of(document, 'train')[0]['session_id']
    save_declaration(set_dir, document)
    refusal('DUPLICATE_SESSION_ID', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_duplicate_identity_inside_one_split_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    val_frames = sessions_of(document, 'val')[0]['frames']
    payload = (set_dir / val_frames[3]['iq']).read_bytes()
    rebind(set_dir, document, val_frames[4], payload)
    refusal('DUPLICATE_FRAME_IDENTITY', CAL.fit_from_set, set_dir, tmp_path / 'out')


# --------------------------------------------------------- per-frame byte binding

def test_a_missing_frame_artifact_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    (set_dir / entry_of(document, 'val')['iq']).unlink()
    refusal('FRAME_ARTIFACT_MISSING', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_an_out_of_bounds_frame_artifact_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    path = set_dir / entry_of(document, 'val')['iq']
    path.write_bytes(path.read_bytes()[:16380])
    refusal('FRAME_ARTIFACT_SIZE', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_frame_bytes_must_match_the_declared_digest(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    path = set_dir / entry_of(document, 'val')['iq']
    payload = bytearray(path.read_bytes())
    payload[40] ^= 0x01
    path.write_bytes(bytes(payload))
    refusal('FRAME_IQ_SHA_MISMATCH', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_a_sidecar_disagreement_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    frame = entry_of(document, 'val')
    sidecar = sidecar_of(set_dir, frame)
    sidecar['session_id'] = 'somewhere-else'
    save_sidecar(set_dir, frame, sidecar)
    refusal('FRAME_SIDECAR_MISMATCH', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_a_frame_from_another_rf_domain_is_refused(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    frame = entry_of(document, 'val')
    sidecar = sidecar_of(set_dir, frame)
    sidecar['rf'] = dict(sidecar['rf'], attenuation_mdB=PRODUCT_MDB - 2000)
    save_sidecar(set_dir, frame, sidecar)
    refusal('FRAME_DOMAIN_MISMATCH', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_an_artifact_path_may_not_escape_the_set(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    entry_of(document, 'val')['iq'] = '../outside.iq'
    save_declaration(set_dir, document)
    refusal('FRAME_PATH', CAL.fit_from_set, set_dir, tmp_path / 'out')


# --------------------------------------------------------------------- domain gate

def test_a_mis_declared_domain_is_refused_not_relabelled(a_set, tmp_path):
    set_dir = a_set()
    document = declaration(set_dir)
    other = dict(document['domain'], attenuation_mdB=PRODUCT_MDB - 6000)
    document['domain'] = other
    for session in document['sessions']:
        for frame in session['frames']:
            sidecar = sidecar_of(set_dir, frame)
            sidecar['rf'] = dict(other)
            save_sidecar(set_dir, frame, sidecar)
    save_declaration(set_dir, document)
    error = refusal('RF_DOMAIN_MIGRATION_REQUIRED', CAL.fit_from_set, set_dir, tmp_path / 'out')
    assert 'migrate' in error.detail


def test_a_truth_source_that_changes_another_field_is_refused(tmp_path):
    bad = overlay_dir(tmp_path, symbol_rate=2000000, name='bad-overlay')
    refusal('DOMAIN_TRUTH_SOURCE_MISMATCH', CAL.domain_audit, PRODUCT_MDB, bad)


def test_a_truth_source_for_another_attenuation_is_refused(tmp_path):
    bad = overlay_dir(tmp_path, attenuation=PRODUCT_MDB - 1000, name='wrong-value')
    refusal('DOMAIN_TRUTH_SOURCE_MISMATCH', CAL.domain_audit, PRODUCT_MDB, bad)


def test_a_real_train_val_fit_may_not_use_a_supplied_truth_source(fixture_set, tmp_path):
    source = overlay_dir(tmp_path, attenuation=PRODUCT_MDB - 1000, name='optional')
    refusal('DOMAIN_TRUTH_SOURCE_MISMATCH', CAL.fit_from_set, fixture_set, tmp_path / 'out',
            source)


def test_the_audited_truth_source_can_move_the_domain_for_a_provisional_body(tmp_path):
    moved = PRODUCT_MDB - 1000
    source = overlay_dir(tmp_path, attenuation=moved, name='moved')
    audit = CAL.domain_audit(moved, source)
    assert audit['truth_source']['diff_fields'] == ['rf_parameters.attenuation_mdB']
    assert audit['effective_attenuation_mdB'] == moved
    body = json.loads((SEALED_FRAME / 'unused.json').read_text(encoding='utf-8')) if False else None
    assert body is None
    assert CAL.require_declared_domain(audit, require_product_agreement=False) is audit


# --------------------------------------------------------------------- load checks

def loadable_body(fitted):
    return json.loads((fitted[1] / 'baseline.json').read_text(encoding='utf-8'))


def test_canonical_sha_mismatch_is_named(fitted, tmp_path):
    body = loadable_body(fitted)
    body['thresholds']['pulse_crest'] = body['thresholds']['pulse_crest'] + 1
    path = tmp_path / 'tampered.json'
    path.write_text(json.dumps(body), encoding='utf-8')
    refusal('BASELINE_CANONICAL_SHA_MISMATCH', CAL.load_check, path)


def test_an_unexpected_declared_sha_is_named(fitted):
    body = loadable_body(fitted)
    refusal('BASELINE_CANONICAL_SHA_MISMATCH', CAL.load_check, fitted[1] / 'baseline.json',
            None, '0' * 64)


def test_a_body_the_production_loader_refuses_is_named(fitted, tmp_path):
    body = loadable_body(fitted)
    body['schema_version'] = 2
    body['sha256'] = CAL._digest(dict((key, body[key]) for key in CAL.BASELINE_BODY_FIELDS))
    path = tmp_path / 'wrong-schema.json'
    path.write_text(json.dumps(body), encoding='utf-8')
    refusal('BASELINE_LOAD_NOT_EQUAL', CAL.load_check, path)


def test_a_body_that_is_not_the_frozen_schema_is_named(fitted, tmp_path):
    body = loadable_body(fitted)
    body['extra'] = True
    path = tmp_path / 'extra-field.json'
    path.write_text(json.dumps(body), encoding='utf-8')
    refusal('BASELINE_SCHEMA', CAL.load_check, path)


def test_a_provisional_wrapper_without_flags_is_named(fitted, tmp_path):
    body = loadable_body(fitted)
    wrapper = dict(schema=CAL.PROVISIONAL_SCHEMA, baseline=body)
    path = tmp_path / 'wrapper.json'
    path.write_text(json.dumps(wrapper), encoding='utf-8')
    refusal('PROVISIONAL_FLAGS_MISSING', CAL.load_check, path)


def test_require_formal_refuses_a_one_source_development_body(fitted, tmp_path):
    """F6: the bare body is what the product loader consumes; it must not pass formally."""
    body = loadable_body(fitted)
    body['sources'] = body['sources'][:1]
    body['sha256'] = CAL._digest(dict((key, body[key]) for key in CAL.BASELINE_BODY_FIELDS))
    path = tmp_path / 'one-source.json'
    path.write_text(json.dumps(body), encoding='utf-8')
    # The n=1 development body stays loadable for development use ...
    assert CAL.load_check(path)['loaded'] is True
    # ... and the formal entry refuses it by name.
    refused = refusal('PROVISIONAL_BASELINE_NOT_FORMAL', CAL.load_check, path, None, None,
                      True)
    assert 'development' in refused.detail and '1 source' in refused.detail


def test_require_formal_accepts_the_fitted_twenty_source_baseline(fitted):
    """The flag is not a blanket refusal: a real two-session TRAIN fit carries 20 sources."""
    loaded = CAL.load_check(fitted[1] / 'baseline.json', require_formal=True)
    assert loaded['loaded'] is True
    assert len(loaded['sources']) == 20
    assert loaded['calibration_status'] == 'development'


def test_the_cli_require_formal_flag_refuses_a_one_source_body(fitted, tmp_path):
    body = loadable_body(fitted)
    body['sources'] = body['sources'][:1]
    body['sha256'] = CAL._digest(dict((key, body[key]) for key in CAL.BASELINE_BODY_FIELDS))
    path = tmp_path / 'one-source.json'
    path.write_text(json.dumps(body), encoding='utf-8')
    assert CAL.main(['load-check', '--baseline', str(path), '--require-formal']) == 3
    assert CAL.main(['load-check', '--baseline', str(path)]) == 0


def test_baseline_sources_may_only_be_train(fitted):
    body = loadable_body(fitted)
    row = dict(body['sources'][0], split='val')
    refusal('BASELINE_SOURCE_NOT_TRAIN', CAL.baseline_sources, [row])


# ------------------------------------------------------------------- verdict gates

def test_an_unqualified_train_frame_is_named(a_set, tmp_path):
    import numpy as np
    set_dir = a_set()
    document = declaration(set_dir)
    noise = np.zeros((4096, 2), dtype='<i2').tobytes()
    rebind(set_dir, document, entry_of(document, 'train'), noise)
    refusal('TRAIN_FRAME_NOT_QUALIFIED', CAL.fit_from_set, set_dir, tmp_path / 'out')


def test_a_misclassified_val_frame_fails_without_emitting_a_baseline(a_set, tmp_path):
    import numpy as np
    set_dir = a_set()
    output = tmp_path / 'out'
    document = declaration(set_dir)
    noise = np.zeros((4096, 2), dtype='<i2').tobytes()
    rebind(set_dir, document, entry_of(document, 'val'), noise)
    refusal('CALIBRATION_VERDICT_FAIL', CAL.fit_from_set, set_dir, output)
    assert not (output / 'baseline.json').exists()
    receipt = json.loads((output / 'calibration-receipt.json').read_text(encoding='utf-8'))
    assert receipt['verdict']['passed'] is False
    assert receipt['baseline_file'] is None


# ------------------------------------------------------------------ provisional n=1

pytestmark_sealed = pytest.mark.skipif(not SEALED_FRAME.is_dir(),
                                       reason='sealed attempt4 research frame not present')


@pytestmark_sealed
def test_the_archived_normal_frame_builds_a_provisional_baseline(tmp_path):
    output = tmp_path / 'provisional'
    wrapper = CAL.provisional_from_frame(SEALED_FRAME, output)
    assert wrapper['provisional'] is True and wrapper['n'] == 1
    assert wrapper['not_for_formal_acceptance'] is True
    assert wrapper['formal_train_val'] is False and wrapper['test_frames'] == 0
    body = wrapper['baseline']
    assert set(body) == set(CAL.BASELINE_BODY_FIELDS) | set(['sha256'])
    assert body['sha256'] == wrapper['baseline_sha256']
    assert body['rf_config']['attenuation_mdB'] == 46000
    assert len(body['sources']) == 1 and body['sources'][0]['split'] == 'train'
    assert wrapper['load_verification']['loaded'] is True
    assert wrapper['load_verification']['body_equal'] is True
    assert wrapper['receipt']['rebuild_binding']['rebuilt_equals_recorded'] is True
    assert CAL.load_check(output / 'provisional-baseline.json')['sha256'] == body['sha256']
    assert CAL.load_check(output / 'provisional-baseline.body.json')['loaded'] is True
    # F6: the formal flag closes the bare-body bypass on both shapes of the artifact.
    for name in ('provisional-baseline.json', 'provisional-baseline.body.json'):
        formal = refusal('PROVISIONAL_BASELINE_NOT_FORMAL', CAL.load_check,
                         output / name, None, None, True)
        assert 'development' in formal.detail


@pytestmark_sealed
def test_the_provisional_body_is_not_a_train_or_val_set(tmp_path):
    wrapper = CAL.provisional_from_frame(SEALED_FRAME, tmp_path / 'provisional')
    assert wrapper['receipt']['train_sessions'] == 0
    assert wrapper['receipt']['val_sessions'] == 0
    assert 'formal TRAIN/VAL evidence' in wrapper['must_not_be_used_for']
    assert 'formal TRAIN/VAL: NOT_RUN' in wrapper['receipt']['not_run']


@pytestmark_sealed
def test_a_tampered_archive_binding_is_refused(tmp_path):
    session = tmp_path / 'session'
    shutil.copytree(str(SEALED_FRAME.parent), str(session))
    frame = session / 'attempt-2'
    analysis = json.loads((frame / 'analysis.json').read_text(encoding='utf-8'))
    analysis['summary']['payload']['sha256'] = '0' * 64
    (frame / 'analysis.json').write_text(json.dumps(analysis), encoding='utf-8')
    refusal('RESEARCH_FRAME_BINDING', CAL.provisional_from_frame, frame, tmp_path / 'out')


@pytestmark_sealed
def test_a_missing_run_gate_is_refused(tmp_path):
    session = tmp_path / 'session'
    shutil.copytree(str(SEALED_FRAME.parent), str(session))
    (session / 'run-gate.json').unlink()
    refusal('RESEARCH_FRAME_BINDING', CAL.provisional_from_frame, session / 'attempt-2',
            tmp_path / 'out')


@pytestmark_sealed
def test_a_non_normal_archive_frame_is_refused(tmp_path):
    session = tmp_path / 'session'
    shutil.copytree(str(SEALED_FRAME.parent), str(session))
    frame = session / 'attempt-2'
    header = json.loads((frame / 'header.json').read_text(encoding='utf-8'))
    header['header']['headroom']['scenario'] = 'narrowband'
    (frame / 'header.json').write_text(json.dumps(header), encoding='utf-8')
    refusal('RESEARCH_FRAME_NOT_NORMAL', CAL.provisional_from_frame, frame, tmp_path / 'out')
