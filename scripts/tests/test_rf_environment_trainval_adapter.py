"""Download -> set.json adapter: binding, domain/split gates, named refusals.

The positive path runs entirely on deterministic offline fixture sessions.  The
physical provenance branch is exercised only as a pure decision function: this file
never produces a synthetic artifact labelled physical, and no board is contacted.
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

from scripts import rf_environment_calibration as CAL  # noqa: E402
from scripts import rf_environment_trainval_adapter as A  # noqa: E402
from scripts import rf_environment_trainval_session as S  # noqa: E402

MDB = dict(CAL._profile_domain(CAL.TEACHING_PROFILE)[0])['attenuation_mdB']


def refusal(code, function, *args, **kwargs):
    with pytest.raises(CAL.CalibrationError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, document):
    Path(path).write_text(json.dumps(document, indent=2), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slots_of(session_dir):
    return load(session_dir / 'session-manifest.json')['slots']


def slot_of(session_dir, tag):
    return [slot for slot in slots_of(session_dir) if slot['tag'] == tag][0]


def refresh_receipt(session_dir):
    manifest = load(session_dir / 'session-manifest.json')
    receipt = load(session_dir / 'receipt.json')
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
    slot = slot_of(session_dir, tag)
    (session_dir / slot['iq']).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = load(session_dir / 'session-manifest.json')
    [row for row in manifest['slots'] if row['tag'] == tag][0]['iq_sha256'] = digest
    save(session_dir / 'session-manifest.json', manifest)
    metadata = load(session_dir / slot['metadata'])
    metadata['iq_sha256'] = digest
    save(session_dir / slot['metadata'], metadata)
    sidecar = load(session_dir / slot['environment'])
    sidecar['iq_sha256'] = digest
    save(session_dir / slot['environment'], sidecar)
    refresh_receipt(session_dir)


def replace_frame_artifacts(session_dir, tag, foreign_dir, foreign_tag):
    slot, foreign = slot_of(session_dir, tag), slot_of(foreign_dir, foreign_tag)
    shutil.copyfile(str(foreign_dir / foreign['iq']), str(session_dir / slot['iq']))
    shutil.copyfile(str(foreign_dir / foreign['metadata']),
                    str(session_dir / slot['metadata']))
    shutil.copyfile(str(foreign_dir / foreign['environment']),
                    str(session_dir / slot['environment']))
    digest = sha(session_dir / slot['iq'])
    manifest = load(session_dir / 'session-manifest.json')
    row = [item for item in manifest['slots'] if item['tag'] == tag][0]
    row['iq_sha256'] = digest
    row['capture_id'] = load(session_dir / slot['metadata'])['capture_id']
    save(session_dir / 'session-manifest.json', manifest)
    refresh_receipt(session_dir)


def rebind_identity(session_dir, tag, identity):
    """Keep every declared identity copy consistent in an offline fixture."""
    slot = slot_of(session_dir, tag)
    metadata = load(session_dir / slot['metadata'])
    metadata.update(identity)
    metadata['capture_status']['capture_id'] = identity['capture_id']
    metadata['demod_result']['result'].update(identity)
    save(session_dir / slot['metadata'], metadata)
    sidecar = load(session_dir / slot['environment'])
    sidecar.update({key: identity[key] for key in ('capture_id', 'release_id')})
    sidecar['environment'].update(identity)
    sidecar['display_environment'].update(identity)
    sidecar['record']['capture_id'] = identity['capture_id']
    save(session_dir / slot['environment'], sidecar)
    manifest = load(session_dir / 'session-manifest.json')
    [row for row in manifest['slots'] if row['tag'] == tag][0]['capture_id'] = identity['capture_id']
    save(session_dir / 'session-manifest.json', manifest)
    refresh_receipt(session_dir)


def mark_physical(session_dir):
    """Negative-test helper: relabel one fixture session as a closed board session.

    It never leaves this test: the adapter must still refuse to build a physical
    set without a host download receipt.
    """
    manifest = load(session_dir / 'session-manifest.json')
    manifest.update(fixture=False, collected=True, physical=True,
                    not_for_formal_acceptance=False)
    save(session_dir / 'session-manifest.json', manifest)
    for slot in manifest['slots']:
        # A physical session captures normal frames over the TX->RX loopback and off
        # frames as ordinary RX, so only normal frames carry physical=true.
        physical = slot['scenario'] == 'normal'
        metadata = load(session_dir / slot['metadata'])
        metadata.update(fixture=False, collected=True, physical=physical)
        save(session_dir / slot['metadata'], metadata)
        sidecar = load(session_dir / slot['environment'])
        sidecar.update(fixture=False, collected=True, physical=physical)
        save(session_dir / slot['environment'], sidecar)
    receipt = load(session_dir / 'receipt.json')
    receipt.update(fixture=False, collected=True, physical=True,
                   session_closed=True, disarm_ack=True)
    save(session_dir / 'receipt.json', receipt)
    refresh_receipt(session_dir)


def move_domain(session_dir, attenuation, version):
    manifest = load(session_dir / 'session-manifest.json')
    manifest['domain'] = dict(manifest['domain'], attenuation_mdB=attenuation)
    manifest['environment_version'] = version
    save(session_dir / 'session-manifest.json', manifest)
    for slot in manifest['slots']:
        sidecar = load(session_dir / slot['environment'])
        sidecar['rf_config'] = dict(sidecar['rf_config'], attenuation_mdB=attenuation)
        sidecar['environment_version'] = version
        sidecar['environment']['environment_version'] = version
        save(session_dir / slot['environment'], sidecar)
    refresh_receipt(session_dir)


def host_receipt(root, *, fixture=True):
    evidence = {}
    for session_dir in sorted(path for path in Path(root).iterdir() if path.is_dir()):
        receipt = load(session_dir / 'receipt.json')
        for name, digest in receipt['evidence_sha256'].items():
            evidence['%s/%s' % (session_dir.name, name)] = digest
    return dict(schema='test-download-receipt', fixture=fixture,
                physical=not fixture, evidence_sha256=evidence)


@pytest.fixture(scope='module')
def download_root(tmp_path_factory):
    root = tmp_path_factory.mktemp('trainval-download')
    for split, index in (('train', 1), ('train', 2), ('val', 1)):
        S.build_fixture_session(split, index, root / S.session_id(split, index))
    return root


@pytest.fixture
def a_download(download_root, tmp_path):
    def make(name='download'):
        target = tmp_path / name
        shutil.copytree(str(download_root), str(target))
        return target
    return make


@pytest.fixture(scope='module')
def adapted(download_root, tmp_path_factory):
    output = tmp_path_factory.mktemp('trainval-set') / 'set'
    return A.adapt(download_root, output), output


@pytest.fixture(scope='module')
def fitted(adapted, tmp_path_factory):
    output = tmp_path_factory.mktemp('trainval-fit') / 'fit'
    return CAL.fit_from_set(adapted[1], output), output


# ------------------------------------------------------------------ positive path

def test_the_adapter_builds_a_marked_fixture_set(adapted):
    result, output = adapted
    assert result['provenance']['kind'] == 'offline_synthetic_fixture'
    assert result['fixture'] == dict(fixture=True, collected=False, physical_frames=0,
                                     not_for_formal_acceptance=True,
                                     notice='offline synthetic frames; not physical '
                                            'TRAIN/VAL evidence')
    assert result['environment_version'] == 'rf-window-environment-v4'
    assert result['domain']['attenuation_mdB'] == MDB == 46000
    assert result['counts'] == dict(sessions=3, train_sessions=2, val_sessions=1,
                                    frames=33, normal=30, off=3, train_normal=20,
                                    val_normal=10, test_frames=0)
    document = load(output / 'set.json')
    assert document['schema'] == CAL.SET_SCHEMA
    assert document['plan'] == CAL._plan_summary()
    assert document['not_for_formal_acceptance'] is True
    assert [session['session_id'] for session in document['sessions']] == [
        'train-01', 'train-02', 'val-01']
    assert [session['split'] for session in document['sessions']] == [
        'train', 'train', 'val']
    assert all(session['fixture'] is True and session['collected'] is False
               for session in document['sessions'])
    assert all('NOT_RUN' in row for row in document['not_run'])


def test_every_adapted_frame_is_bound_to_its_download_bytes(adapted, download_root):
    _, output = adapted
    document = load(output / 'set.json')
    frames = [frame for session in document['sessions'] for frame in session['frames']]
    assert len(frames) == 33
    for frame in frames:
        copied = output / frame['iq']
        download = download_root / frame['download']['path']
        assert copied.read_bytes() == download.read_bytes()
        assert sha(download) == frame['iq_sha256'] == frame['download']['sha256']
        sidecar = load(output / frame['metadata'])
        assert sidecar['schema'] == CAL.FRAME_SCHEMA
        assert sidecar['frame_id'] == frame['frame_id']
        assert sidecar['session_id'] == frame['frame_id'].rsplit('-', 2)[0]
        assert sidecar['split'] == frame['split']
        assert sidecar['iq_sha256'] == frame['iq_sha256']
        assert sidecar['rf'] == document['domain']
        assert sidecar['fixture'] is True and sidecar['collected'] is False
        assert sidecar['download']['path'] == frame['download']['path']
        assert sidecar['download']['sha256'] == frame['iq_sha256']


def test_the_adapter_receipt_traces_every_source(adapted):
    result, output = adapted
    receipt = load(output / 'adapter-receipt.json')
    assert receipt['counts']['frames'] == 33
    assert receipt['download_receipt'] is None
    assert len(receipt['sessions']) == 3
    for session in receipt['sessions']:
        assert session['download_path']
        assert session['manifest_sha256'] == sha(Path(session['download_path'])
                                                 / 'session-manifest.json')
        assert session['receipt_sha256'] == sha(Path(session['download_path'])
                                                / 'receipt.json')
        assert len(session['frames']) == 11
        for frame in session['frames']:
            assert sha(frame['source_path']) == frame['iq_sha256']
    assert result['set_sha256'] == sha(output / 'set.json')
    assert result['receipt_sha256'] == sha(output / 'adapter-receipt.json')


def test_the_adapted_set_fits_and_loads(fitted):
    receipt, output = fitted
    assert receipt['verdict'] == dict(passed=True, val_normal_clear=10,
                                      val_normal_total=10, val_misclassified=[],
                                      negative_false_clear=[], invalid_normal=[])
    assert receipt['counts']['train_normal'] == 20
    assert receipt['counts']['val_normal'] == 10
    assert receipt['counts']['test_frames'] == 0
    assert receipt['provenance']['kind'] == 'offline_synthetic_fixture'
    assert receipt['formal_physical'] == 'NOT_RUN'
    body = load(output / 'baseline.json')
    assert len(body['sources']) == 20
    assert all(source['split'] == 'train' for source in body['sources'])
    assert body['rf_config']['attenuation_mdB'] == 46000
    assert body['environment_version'] == 'rf-window-environment-v4'
    assert CAL.load_check(output / 'baseline.json', require_formal=True)['loaded'] is True


def test_require_physical_refuses_the_fixture_set(adapted, tmp_path):
    _, set_dir = adapted
    refusal('SET_NOT_PHYSICAL', CAL.fit_from_set, set_dir, tmp_path / 'fit', None,
            False, True)
    assert CAL.main(['fit', '--set', str(set_dir), '--output', str(tmp_path / 'cli-fit'),
                     '--require-physical']) == 3


# ---------------------------------------------------------------- download layout

def test_a_missing_session_directory_is_named(a_download, tmp_path):
    root = a_download()
    shutil.rmtree(str(root / 'val-01'))
    refusal('DOWNLOAD_SESSION_COUNT', A.adapt, root, tmp_path / 'set')


def test_a_duplicated_session_is_named(a_download, tmp_path):
    root = a_download()
    shutil.rmtree(str(root / 'val-01'))
    shutil.copytree(str(root / 'train-01'), str(root / 'val-01'))
    refusal('DUPLICATE_SESSION_ID', A.adapt, root, tmp_path / 'set')


def test_a_missing_frame_artifact_is_named(a_download, tmp_path):
    root = a_download()
    (root / 'train-01' / 'normal-02.iq').unlink()
    refusal('SESSION_FRAME_MISSING', A.adapt, root, tmp_path / 'set')


def test_a_byte_digest_mismatch_is_named(a_download, tmp_path):
    root = a_download()
    path = root / 'train-01' / 'normal-02.iq'
    payload = bytearray(path.read_bytes())
    payload[7] ^= 0x01
    path.write_bytes(bytes(payload))
    refusal('SESSION_FRAME_IQ_SHA_MISMATCH', A.adapt, root, tmp_path / 'set')


def test_an_identity_mismatch_is_named(a_download, tmp_path):
    root = a_download()
    session = root / 'train-01'
    slot = slot_of(session, 'normal-03')
    sidecar = load(session / slot['environment'])
    sidecar['capture_id'] = 'somewhere-else'
    save(session / slot['environment'], sidecar)
    refresh_receipt(session)
    refusal('SESSION_FRAME_IDENTITY_MISMATCH', A.adapt, root, tmp_path / 'set')


def test_a_domain_mismatch_is_named(a_download, tmp_path):
    root = a_download()
    move_domain(root / 'train-01', 40000, 'rf-window-environment-v3')
    error = refusal('DOMAIN_MISMATCH', A.adapt, root, tmp_path / 'set')
    assert '46000' in error.detail


def test_an_environment_version_mismatch_is_named(a_download, tmp_path):
    root = a_download()
    session = root / 'train-01'
    move_domain(session, 46000, 'rf-window-environment-v3')
    refusal('ENVIRONMENT_VERSION_MISMATCH', A.adapt, root, tmp_path / 'set')


# --------------------------------------------------------------- split leakage

def test_restarted_connections_may_reuse_local_capture_ids(a_download, tmp_path):
    root = a_download()
    local_id = load(root / 'train-01' / 'normal-01.json')['capture_id']
    for name in ('train-02', 'val-01'):
        metadata = load(root / name / 'normal-01.json')
        identity = {key: metadata[key] for key in (
            'capture_id', 'release_id', 'connection_generation',
            'config_revision', 'completed_at_monotonic')}
        identity['capture_id'] = local_id
        rebind_identity(root / name, 'normal-01', identity)
    result = A.adapt(root, tmp_path / 'set')
    assert result['counts']['frames'] == 33


@pytest.mark.parametrize('target,code', [
    ('train-02', 'DUPLICATE_FRAME_IDENTITY'), ('val-01', 'CROSS_SPLIT_LEAKAGE')])
def test_duplicate_complete_identity_is_still_refused(a_download, tmp_path, target, code):
    root = a_download()
    metadata = load(root / 'train-01' / 'normal-01.json')
    identity = {key: metadata[key] for key in (
        'capture_id', 'release_id', 'connection_generation',
        'config_revision', 'completed_at_monotonic')}
    rebind_identity(root / target, 'normal-01', identity)
    refusal(code, A.adapt, root, tmp_path / 'set')


def test_the_same_iq_inside_train_is_still_refused(a_download, tmp_path):
    root = a_download()
    payload = (root / 'train-01' / 'normal-01.iq').read_bytes()
    rebind_iq(root / 'train-02', 'normal-01', payload)
    refusal('DUPLICATE_FRAME_IDENTITY', A.adapt, root, tmp_path / 'set')


def test_a_val_frame_placed_in_a_train_session_is_named(a_download, tmp_path):
    root = a_download()
    replace_frame_artifacts(root / 'train-02', 'normal-01', root / 'val-01', 'normal-01')
    error = refusal('SESSION_FRAME_SESSION_MISMATCH', A.adapt, root, tmp_path / 'set')
    assert 'val-01' in error.detail


def test_the_same_iq_in_two_splits_is_named(a_download, tmp_path):
    root = a_download()
    payload = (root / 'val-01' / 'normal-01.iq').read_bytes()
    rebind_iq(root / 'train-02', 'normal-01', payload)
    error = refusal('CROSS_SPLIT_LEAKAGE', A.adapt, root, tmp_path / 'set')
    assert 'train' in error.detail and 'val' in error.detail


def test_the_declared_session_order_is_the_matrix():
    good = [dict(split='train', index=1, seed=1), dict(split='train', index=2, seed=37),
            dict(split='val', index=1, seed=73)]
    ordered = A.order_sessions(good)
    assert [session['seed'] for session in ordered['train']] == [1, 37]
    assert [session['seed'] for session in ordered['val']] == [73]
    refusal('SESSION_SPLIT_PLAN', A.order_sessions,
            [dict(split='train', index=1, seed=1), dict(split='val', index=1, seed=73)])
    refusal('SESSION_SPLIT_PLAN', A.order_sessions,
            [dict(split='train', index=1, seed=1), dict(split='train', index=2, seed=37),
             dict(split='val', index=1, seed=73), dict(split='val', index=2, seed=73)])


# ------------------------------------------------------------- download receipt

def test_a_download_receipt_that_binds_the_tree_is_accepted(a_download, tmp_path):
    root = a_download()
    receipt_path = tmp_path / 'download-receipt.json'
    save(receipt_path, host_receipt(root))
    result = A.adapt(root, tmp_path / 'set', download_receipt=receipt_path)
    assert result['provenance']['kind'] == 'offline_synthetic_fixture'
    assert result['download_receipt']['sha256'] == sha(receipt_path)
    assert result['download_receipt']['evidence_entries'] == 99


def test_a_tampered_download_receipt_digest_is_named(a_download, tmp_path):
    root = a_download()
    document = host_receipt(root)
    key = sorted(document['evidence_sha256'])[0]
    document['evidence_sha256'][key] = '0' * 64
    receipt_path = tmp_path / 'download-receipt.json'
    save(receipt_path, document)
    refusal('DOWNLOAD_RECEIPT_SHA_MISMATCH', A.adapt, root, tmp_path / 'set',
            download_receipt=receipt_path)


def test_a_download_receipt_without_a_frame_is_named(a_download, tmp_path):
    root = a_download()
    document = host_receipt(root)
    key = sorted(document['evidence_sha256'])[0]
    document['evidence_sha256'].pop(key)
    receipt_path = tmp_path / 'download-receipt.json'
    save(receipt_path, document)
    refusal('DOWNLOAD_RECEIPT_FRAME_UNBOUND', A.adapt, root, tmp_path / 'set',
            download_receipt=receipt_path)


def test_a_download_receipt_that_claims_physical_is_named(a_download, tmp_path):
    root = a_download()
    document = host_receipt(root, fixture=True)
    document['fixture'] = False
    receipt_path = tmp_path / 'download-receipt.json'
    save(receipt_path, document)
    refusal('DOWNLOAD_RECEIPT_FLAG_MISMATCH', A.adapt, root, tmp_path / 'set',
            download_receipt=receipt_path)


# ------------------------------------------------------------------- provenance

def test_a_fixture_cannot_be_adapted_as_physical(download_root, tmp_path):
    refusal('PHYSICAL_PROVENANCE_CONFLICT', A.adapt, download_root, tmp_path / 'set',
            physical=True)


def test_physical_flags_without_bound_provenance_are_named(a_download, tmp_path):
    root = a_download()
    for name in ('train-01', 'train-02', 'val-01'):
        mark_physical(root / name)
    refusal('PHYSICAL_PROVENANCE_UNVERIFIED', A.adapt, root, tmp_path / 'set')
    refusal('DOWNLOAD_RECEIPT_MISSING', A.adapt, root, tmp_path / 'set2',
            physical=True)


def test_the_provenance_decision_is_pure():
    fixture = dict(session_id='train-01', fixture=True, collected=False, physical=False)
    kind, block = A.decide_provenance([fixture])
    assert kind == 'offline_synthetic_fixture'
    assert block['not_for_formal_acceptance'] is True
    refusal('PHYSICAL_PROVENANCE_CONFLICT', A.decide_provenance, [fixture], True)
    physical = dict(session_id='train-01', fixture=False, collected=True,
                    physical=True, session_closed=True, disarm_ack=True,
                    release_id='release-id')
    refusal('PHYSICAL_PROVENANCE_UNVERIFIED', A.decide_provenance, [physical])
    kind, block = A.decide_provenance([physical], True)
    assert kind == 'new_physical_train_val'
    assert block == dict(fixture=False, collected=True, physical_frames=11,
                         not_for_formal_acceptance=False,
                         notice='declared board sessions, re-bound to the supplied '
                                'download receipt')
    refusal('PHYSICAL_SESSION_NOT_CLOSED', A.decide_provenance,
            [dict(physical, session_closed=False)], True)
    refusal('RELEASE_ID_MISMATCH', A.decide_provenance,
            [dict(physical, release_id='')], True)


# ------------------------------------------------------------------------ output

def test_the_output_is_never_silently_overwritten(download_root, tmp_path):
    output = tmp_path / 'set'
    A.adapt(download_root, output)
    refusal('OUTPUT_EXISTS', A.adapt, download_root, output)


def test_the_cli_adapts_a_download(download_root, tmp_path, capsys):
    assert A.main(['adapt', '--download', str(download_root),
                   '--output', str(tmp_path / 'set')]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['provenance']['kind'] == 'offline_synthetic_fixture'
    assert printed['counts']['frames'] == 33
    assert A.main(['adapt', '--download', str(tmp_path / 'missing'),
                   '--output', str(tmp_path / 'never')]) == 3
    assert json.loads(capsys.readouterr().out)['error'] == 'DOWNLOAD_ROOT_MISSING'
