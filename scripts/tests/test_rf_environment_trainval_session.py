"""11-frame TRAIN/VAL session entry: shape, seeds, ordering, identity, receipt.

Offline only.  The positive case is a deterministic synthetic fixture; the real
collection route is asserted to be a named NOT_RUN and to write nothing.  Every
negative case asserts its stable refusal code (never prose).
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import rf_environment_trainval_session as S  # noqa: E402

TAGS = list(S.FRAME_TAGS)


def refusal(code, function, *args, **kwargs):
    with pytest.raises(S.SessionError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, document):
    Path(path).write_text(json.dumps(document, indent=2), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest_of(session_dir):
    return load(session_dir / 'session-manifest.json')


def receipt_of(session_dir):
    return load(session_dir / 'receipt.json')


def slot_of(manifest, tag):
    return [slot for slot in manifest['slots'] if slot['tag'] == tag][0]


def refresh_receipt(session_dir):
    """Recompute the receipt from the manifest and the files on disk."""
    manifest = manifest_of(session_dir)
    receipt = receipt_of(session_dir)
    receipt['session_manifest_sha256'] = sha(session_dir / 'session-manifest.json')
    receipt['frames'] = [dict(position=slot['position'], tag=slot['tag'],
                              frame_id=slot['frame_id'], iq=slot['iq'],
                              metadata=slot['metadata'],
                              environment=slot['environment'],
                              iq_sha256=slot['iq_sha256'],
                              capture_id=slot['capture_id'])
                         for slot in manifest['slots']]
    receipt['evidence_sha256'] = {}
    for slot in manifest['slots']:
        for name in (slot['iq'], slot['metadata'], slot['environment']):
            receipt['evidence_sha256'][name] = sha(session_dir / name)
    save(session_dir / 'receipt.json', receipt)


def rebind_iq(session_dir, tag, payload):
    manifest = manifest_of(session_dir)
    slot = slot_of(manifest, tag)
    (session_dir / slot['iq']).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    slot['iq_sha256'] = digest
    save(session_dir / 'session-manifest.json', manifest)
    metadata = load(session_dir / slot['metadata'])
    metadata['iq_sha256'] = digest
    save(session_dir / slot['metadata'], metadata)
    sidecar = load(session_dir / slot['environment'])
    sidecar['iq_sha256'] = digest
    save(session_dir / slot['environment'], sidecar)
    refresh_receipt(session_dir)


def set_identity(session_dir, tag, capture_id):
    manifest = manifest_of(session_dir)
    slot = slot_of(manifest, tag)
    slot['capture_id'] = capture_id
    save(session_dir / 'session-manifest.json', manifest)
    metadata = load(session_dir / slot['metadata'])
    metadata['capture_id'] = capture_id
    metadata['capture_status']['capture_id'] = capture_id
    metadata['demod_result']['result']['capture_id'] = capture_id
    save(session_dir / slot['metadata'], metadata)
    sidecar = load(session_dir / slot['environment'])
    sidecar['capture_id'] = capture_id
    sidecar['environment']['capture_id'] = capture_id
    sidecar['display_environment']['capture_id'] = capture_id
    if isinstance(sidecar.get('record'), dict):
        sidecar['record']['capture_id'] = capture_id
    save(session_dir / slot['environment'], sidecar)
    refresh_receipt(session_dir)


@pytest.fixture(scope='module')
def fixture_session(tmp_path_factory):
    root = tmp_path_factory.mktemp('trainval-session')
    target = root / 'train-01'
    S.build_fixture_session('train', 1, target)
    return target


@pytest.fixture
def a_session(fixture_session, tmp_path):
    def make(name='session'):
        target = tmp_path / name
        shutil.copytree(str(fixture_session), str(target))
        return target
    return make


# --------------------------------------------------------------------------- plan

def test_plan_is_ten_normal_then_one_off_and_claims_no_frames():
    document = S.plan_document('train', 2)
    summary = S.check_plan(document)
    assert summary['frame_count'] == 11
    assert summary['normal_count'] == 10 and summary['off_count'] == 1
    assert summary['frame_order'] == TAGS
    assert [slot['scenario'] for slot in document['slots']] == ['normal'] * 10 + ['off']
    assert document['session']['seed'] == 37
    assert all(slot['frame_id'].startswith('train-02-') for slot in document['slots'])
    assert document['collected'] is False and document['fixture'] is False
    assert all('NOT_RUN' in row for row in summary['not_run'])


def test_a_plan_claiming_collection_is_named():
    document = dict(S.plan_document('val', 1), collected=True)
    refusal('SESSION_PLAN_NOT_A_PLAN', S.check_plan, document)


def test_a_plan_with_the_wrong_order_is_named():
    document = S.plan_document('val', 1)
    document['frame_order'] = list(reversed(TAGS))
    refusal('SESSION_PLAN_MATRIX_MISMATCH', S.check_plan, document)


def test_an_unknown_split_or_session_index_is_named():
    refusal('SESSION_SPLIT_NOT_ALLOWED', S.plan_document, 'test', 1)
    refusal('SESSION_INDEX_NOT_ALLOWED', S.plan_document, 'train', 3)


# ------------------------------------------------------------------ positive path

def test_a_fixture_session_has_the_declared_shape_and_flags(fixture_session):
    summary = S.check_session(fixture_session)
    assert summary['session_id'] == 'train-01' and summary['split'] == 'train'
    assert summary['seed'] == 1 and summary['index'] == 1
    assert summary['fixture'] is True
    assert summary['collected'] is False and summary['physical'] is False
    assert summary['frame_count'] == 11
    assert summary['normal_count'] == 10 and summary['off_count'] == 1
    assert [entry['tag'] for entry in summary['frames']] == TAGS
    assert len({entry['iq_sha256'] for entry in summary['frames']}) == 11
    assert len({entry['identity']['capture_id'] for entry in summary['frames']}) == 11
    assert summary['domain']['attenuation_mdB'] == 46000
    assert summary['environment_version'] == 'rf-window-environment-v4'
    files = {path.name for path in fixture_session.iterdir() if path.is_file()}
    assert 'session-manifest.json' in files and 'receipt.json' in files
    assert {'normal-01.iq', 'normal-10.iq', 'off-01.iq'} <= files
    assert {'normal-01.json', 'normal-01.environment.json'} <= files


def test_the_session_receipt_binds_every_artifact_digest(fixture_session):
    receipt = receipt_of(fixture_session)
    assert receipt['complete'] is True
    assert receipt['fixture'] is True and receipt['collected'] is False
    assert receipt['session_closed'] is False and receipt['disarm_ack'] is False
    assert len(receipt['evidence_sha256']) == 33
    assert len(receipt['frames']) == 11
    for name, digest in receipt['evidence_sha256'].items():
        assert sha(fixture_session / name) == digest


def test_the_cli_writes_and_checks_a_fixture_session(tmp_path):
    target = tmp_path / 'session'
    assert S.main(['fixture', '--split', 'val', '--session', '1',
                   '--output', str(target)]) == 0
    assert S.main(['check', '--session', str(target)]) == 0
    assert S.check_session(target)['seed'] == 73


def test_the_cli_reports_a_named_failure(a_session, tmp_path, capsys):
    target = a_session()
    (target / 'normal-03.iq').unlink()
    assert S.main(['check', '--session', str(target)]) == 3
    printed = json.loads(capsys.readouterr().out)
    assert printed['error'] == 'SESSION_FRAME_MISSING'


# ---------------------------------------------------------------- structure gates

def test_a_missing_frame_artifact_is_named(a_session, tmp_path):
    target = a_session()
    (target / 'off-01.iq').unlink()
    refusal('SESSION_FRAME_MISSING', S.check_session, target)


def test_an_extra_artifact_is_named(a_session, tmp_path):
    target = a_session()
    (target / 'normal-11.iq').write_bytes(b'\x00' * 16384)
    refusal('SESSION_FRAME_EXTRA', S.check_session, target)


def test_an_undeclared_plain_file_is_named(a_session, tmp_path):
    """A product that is neither .iq nor .json is still an undeclared artifact."""
    target = a_session()
    (target / 'notes.txt').write_text('operator notes\n', encoding='utf-8')
    refusal('SESSION_FRAME_EXTRA', S.check_session, target)


def test_a_reordered_manifest_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    manifest['slots'][0], manifest['slots'][1] = manifest['slots'][1], manifest['slots'][0]
    save(target / 'session-manifest.json', manifest)
    refusal('SESSION_FRAME_ORDER', S.check_session, target)


def test_a_dropped_slot_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    manifest['slots'].pop()
    save(target / 'session-manifest.json', manifest)
    refusal('SESSION_FRAME_COUNT', S.check_session, target)


def test_a_wrong_scenario_mix_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    slot_of(manifest, 'normal-05')['scenario'] = 'off'
    save(target / 'session-manifest.json', manifest)
    refusal('SESSION_SCENARIO_MIX', S.check_session, target)


def test_a_wrong_seed_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    manifest['session']['seed'] = 37
    save(target / 'session-manifest.json', manifest)
    refusal('SESSION_SEED_MISMATCH', S.check_session, target)


def test_a_test_split_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    manifest['session']['split'] = 'test'
    save(target / 'session-manifest.json', manifest)
    refusal('SESSION_SPLIT_NOT_ALLOWED', S.check_session, target)


# ---------------------------------------------------------------- byte/identity

def test_a_byte_digest_mismatch_is_named(a_session, tmp_path):
    target = a_session()
    path = target / 'normal-02.iq'
    payload = bytearray(path.read_bytes())
    payload[11] ^= 0x01
    path.write_bytes(bytes(payload))
    refusal('SESSION_FRAME_IQ_SHA_MISMATCH', S.check_session, target)


def test_a_duplicate_capture_identity_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    duplicate = slot_of(manifest, 'normal-01')['capture_id']
    set_identity(target, 'normal-02', duplicate)
    refusal('SESSION_FRAME_IDENTITY_DUPLICATE', S.check_session, target)


def test_a_duplicate_iq_digest_is_named(a_session, tmp_path):
    target = a_session()
    payload = (target / 'normal-01.iq').read_bytes()
    rebind_iq(target, 'normal-02', payload)
    refusal('SESSION_FRAME_IQ_DUPLICATE', S.check_session, target)


def test_a_sidecar_identity_disagreement_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    slot = slot_of(manifest, 'normal-03')
    sidecar = load(target / slot['environment'])
    sidecar['environment']['capture_id'] = 'somewhere-else'
    save(target / slot['environment'], sidecar)
    refresh_receipt(target)
    refusal('SESSION_FRAME_IDENTITY_MISMATCH', S.check_session, target)


def test_a_frame_from_another_release_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    slot = slot_of(manifest, 'normal-04')
    metadata = load(target / slot['metadata'])
    metadata['release_id'] = 'another-release'
    metadata['demod_result']['result']['release_id'] = 'another-release'
    save(target / slot['metadata'], metadata)
    sidecar = load(target / slot['environment'])
    sidecar['release_id'] = 'another-release'
    sidecar['environment']['release_id'] = 'another-release'
    save(target / slot['environment'], sidecar)
    refresh_receipt(target)
    refusal('SESSION_RELEASE_MISMATCH', S.check_session, target)


def test_a_frame_sidecar_rf_domain_disagreement_is_named(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    slot = slot_of(manifest, 'normal-06')
    sidecar = load(target / slot['environment'])
    sidecar['rf_config'] = dict(sidecar['rf_config'], attenuation_mdB=40000)
    save(target / slot['environment'], sidecar)
    refresh_receipt(target)
    refusal('SESSION_FRAME_DOMAIN_MISMATCH', S.check_session, target)


# ------------------------------------------------------------------ receipt/flags

def test_a_tampered_receipt_digest_is_named(a_session, tmp_path):
    target = a_session()
    receipt = receipt_of(target)
    name = sorted(receipt['evidence_sha256'])[0]
    receipt['evidence_sha256'][name] = '0' * 64
    save(target / 'receipt.json', receipt)
    refusal('SESSION_RECEIPT_MISMATCH', S.check_session, target)


def test_a_missing_receipt_is_named(a_session, tmp_path):
    target = a_session()
    (target / 'receipt.json').unlink()
    refusal('SESSION_RECEIPT_MISSING', S.check_session, target)


def test_a_fixture_may_not_claim_collection(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    manifest['collected'] = True
    save(target / 'session-manifest.json', manifest)
    refusal('SESSION_FLAG_CONFLICT', S.check_session, target)


def test_a_frame_may_not_contradict_the_session_flags(a_session, tmp_path):
    target = a_session()
    manifest = manifest_of(target)
    slot = slot_of(manifest, 'normal-07')
    metadata = load(target / slot['metadata'])
    metadata['fixture'] = False
    save(target / slot['metadata'], metadata)
    refresh_receipt(target)
    refusal('SESSION_FRAME_FLAG_MISMATCH', S.check_session, target)


# ---------------------------------------------------------------- physical route

def test_collect_is_refused_by_name_and_writes_nothing(tmp_path):
    target = tmp_path / 'never'
    error = refusal('PHYSICAL_COLLECTION_NOT_AUTHORIZED', S.collect_session,
                    'train', 1, target)
    assert 'NOT_RUN' in error.detail
    assert not target.exists()


def test_the_collect_cli_exits_three_and_prints_the_code(tmp_path, capsys):
    target = tmp_path / 'never'
    assert S.main(['collect', '--split', 'val', '--session', '1',
                   '--output', str(target)]) == 3
    printed = json.loads(capsys.readouterr().out)
    assert printed['error'] == 'PHYSICAL_COLLECTION_NOT_AUTHORIZED'
    assert not target.exists()

def test_the_collect_flag_is_the_only_switch_that_reaches_a_board(tmp_path, capsys):
    """Without the flag the refusal is a named gate, not a transport failure."""
    target = tmp_path / 'never'
    assert S.main(['collect', '--split', 'train', '--session', '1',
                   '--output', str(target)]) == 3
    printed = json.loads(capsys.readouterr().out)
    assert printed['error'] == 'PHYSICAL_COLLECTION_NOT_AUTHORIZED'
    assert 'authorization flag' in printed['detail']
    assert not target.exists()


def test_the_collect_route_needs_a_candidate_and_a_cable_attestation(tmp_path):
    class ExplodingBoard:
        def __call__(self):
            raise AssertionError('a refused collection opened a board link')

    target = tmp_path / 'never'
    refusal('SESSION_COLLECT_CANDIDATE_REQUIRED', S.collect_session, 'train', 1, target,
            authorized=True, candidate=None, cable_confirmed=True,
            board_factory=ExplodingBoard())
    refusal('SESSION_COLLECT_CABLE_CONFIRMATION_REQUIRED', S.collect_session, 'train', 1,
            target, authorized=True, candidate=tmp_path / 'candidate', cable_confirmed=False,
            board_factory=ExplodingBoard())
    assert not target.exists()


def test_the_collect_cli_names_the_missing_inputs(tmp_path, capsys):
    target = tmp_path / 'never'
    assert S.main(['collect', '--split', 'train', '--session', '1',
                   '--authorize-board-collection', '--output', str(target)]) == 3
    assert json.loads(capsys.readouterr().out)['error'] == 'SESSION_COLLECT_CANDIDATE_REQUIRED'
    assert S.main(['collect', '--split', 'train', '--session', '1',
                   '--authorize-board-collection', '--candidate', str(tmp_path / 'candidate'),
                   '--output', str(target)]) == 3
    assert (json.loads(capsys.readouterr().out)['error']
            == 'SESSION_COLLECT_CABLE_CONFIRMATION_REQUIRED')
    assert not target.exists()

# ------------------------------------------------------- declared collection mode

def test_a_fixture_session_declares_no_collection_mode(fixture_session):
    """A fixture collects nothing, so it has no mode to declare."""
    manifest = manifest_of(fixture_session)
    assert manifest['collection_mode'] is None
    assert receipt_of(fixture_session)['collection_mode'] is None
    summary = S.check_session(fixture_session)
    assert summary['collection_mode'] == S.STANDARD_COLLECTION
    assert summary['provisional_unknown_count'] == 0


def test_a_fixture_may_not_declare_a_collection_mode(a_session):
    session = a_session()
    manifest = manifest_of(session)
    manifest['collection_mode'] = S.STANDARD_COLLECTION
    save(session / 'session-manifest.json', manifest)
    refusal('SESSION_FLAG_CONFLICT', S.check_session, session)
