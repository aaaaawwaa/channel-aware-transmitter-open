"""D6 (OFF scene declaration) and the declared calibration collection mode.

Two independent things are pinned here, both offline against the stub board:

* the runner loads the product scene once per scenario group, so the OFF slot is
  captured under the OFF scene and the frame's own declaration says 'off'.  The
  scenario gate is NOT relaxed: a session whose OFF frame still carries the normal
  declaration is refused with FRAME_SCENARIO_MISMATCH, and the off frame is a plain
  (non-physical) RX capture whose post readback must show the TX source released.
* the collection mode is an explicit declaration.  calibration_collection accepts
  the pre-fit UNKNOWN pair and PULSE / SPARSE_AMPLITUDE_OUTLIERS only after every
  quality gate passed; standard_acceptance still refuses both. Other VAL labels
  stop, OFF must never be CLEAR, and every collected label
  is kept verbatim.  The fitted criteria are unchanged: TRAIN-only fit, VAL 10/10
  CLEAR afterwards, zero OFF false-CLEARs.

Every session here is stub-board synthetic data with the stub release id; nothing
in this file touches a board.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'scripts' / 'tests')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import trainval_stub_board as STUB  # noqa: E402
from scripts import rf_environment_calibration as CAL  # noqa: E402
from scripts import rf_environment_trainval_adapter as ADAPTER  # noqa: E402
from scripts import rf_environment_trainval_board_session as BOARD  # noqa: E402
from scripts import rf_environment_trainval_collect as COLLECT  # noqa: E402
from scripts import rf_environment_trainval_session as SESSION  # noqa: E402

SESSIONS = (('train', 1), ('train', 2), ('val', 1))
PROVISIONAL = {tag: (CAL.PROVISIONAL_UNKNOWN_LABEL, CAL.PROVISIONAL_UNKNOWN_REASON)
               for tag in SESSION.NORMAL_TAGS}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def refusal(code, function, *args, **kwargs):
    with pytest.raises(CAL.CalibrationError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('d6-collection')
    candidate = Path(STUB.build_offline_candidate(root / 'candidate')['path'])
    return dict(root=root, candidate=candidate,
                declaration=COLLECT.candidate_declaration(candidate))


def drive(world, tmp_path, split, index, *, faults=None, collection_mode=None):
    """Run one session through BOARD.run_session with the stub board session."""
    session_dir = tmp_path / SESSION.session_id(split, index)
    session_dir.mkdir(parents=True, exist_ok=True)
    session = STUB.StubSession(split, index, session_dir, release=STUB.STUB_RELEASE,
                               domain=world['declaration']['domain'],
                               environment_version=world['declaration']['environment_version'],
                               faults=faults)
    extra = ({} if collection_mode is None else dict(collection_mode=collection_mode))
    result = BOARD.run_session(
        session, split=split, index=index, output=session_dir,
        expected_release=STUB.STUB_RELEASE, domain=world['declaration']['domain'],
        environment_version=world['declaration']['environment_version'],
        result_path=tmp_path / 'session-result.json', origin=BOARD.STUB_ORIGIN,
        requested_rf=dict(world['declaration']['domain']), **extra)
    return result, session_dir, session


# ------------------------------------------------------------------ D6: the scenes

def test_the_runner_loads_normal_then_off_and_binds_each_declaration(world, tmp_path):
    result, session_dir, session = drive(world, tmp_path, 'train', 1)
    assert result['passed'] is True, result
    assert session.scene_calls == ['normal', 'off']
    assert result['scenes'] == [dict(position=0, tag='normal-01', scenario='normal'),
                                dict(position=10, tag='off-01', scenario='off')]
    manifest = load(session_dir / 'session-manifest.json')
    assert [slot['collected_label'] for slot in manifest['slots']] == \
        ['CLEAR'] * 10 + ['INVALID']
    assert [slot['provisional_unknown'] for slot in manifest['slots']] == [False] * 11
    sidecar = load(session_dir / 'off-01.environment.json')
    assert sidecar['scenario_declaration']['truth'] == 'off'
    assert sidecar['scenario_declaration']['amplitude'] == 60
    metadata = load(session_dir / 'off-01.json')
    # The OFF slot is the energy-floor negative control: a plain RX capture, never a
    # physical loopback, and the post readback must show the TX source released.
    assert metadata['physical'] is False
    assert metadata['post_readback']['tx_source_selected'] is False
    assert metadata['post_readback']['rx_armed'] is False
    assert metadata['pre_readback']['io_ok'] is True and metadata['post_readback']['io_ok'] is True


def test_a_collected_label_is_recorded_verbatim_and_not_relabelled(world, tmp_path):
    """A non-pair label is still recorded as the analyzer named it, never rewritten."""
    result, session_dir, _session = drive(
        world, tmp_path, 'train', 1, faults={'environment_labels':
                                             {'normal-01': ('UNKNOWN', 'BASELINE_MISSING')}})
    assert result['stop_cause'] is None, result
    assert result['frames'][0]['label'] == 'UNKNOWN'
    assert result['frames'][0]['reason'] == 'BASELINE_MISSING'
    assert result['frames'][0]['provisional_unknown'] is False
    assert result['manifest_written'] is True
    manifest = load(session_dir / 'session-manifest.json')
    assert manifest['collection_mode'] == CAL.STANDARD_COLLECTION
    assert manifest['provisional_unknown_frames'] == []
    assert manifest['slots'][0]['collected_label'] == 'UNKNOWN'


def test_a_runner_that_never_switches_to_off_is_refused_by_name(world, tmp_path):
    """The 2026-10-18 board defect, reproduced: off-01 keeps the normal scene."""
    result, session_dir, session = drive(
        world, tmp_path, 'train', 1,
        faults={'stuck_scene': 'normal'})
    assert session.scene_calls == ['normal', 'off']
    assert result['passed'] is False
    assert result['stop_cause'] == 'FRAME_SCENARIO_MISMATCH'
    assert result['stop_frame'] == 'off-01'
    assert result['manifest_written'] is False
    assert result['session_closed'] is True and result['disarm_ack'] is True
    sidecar = load(session_dir / 'off-01.environment.json')
    assert sidecar['scenario_declaration']['truth'] == 'normal'


# ------------------------------------------------- the declared collection mode

def test_the_calibration_mode_collects_the_qualified_unknown_and_keeps_the_label(
        world, tmp_path):
    result, session_dir, _session = drive(
        world, tmp_path, 'val', 1, faults={'environment_labels': PROVISIONAL},
        collection_mode=CAL.CALIBRATION_COLLECTION)
    assert result['passed'] is True, result
    assert result['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert all(row['label'] == 'UNKNOWN' and row['reason'] == 'AMPLITUDE_OUTSIDE_RULE_SUPPORT'
               and row['provisional_unknown'] is True for row in result['frames'][:10])
    manifest = load(session_dir / 'session-manifest.json')
    assert manifest['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert manifest['provisional_unknown_frames'] == list(SESSION.NORMAL_TAGS)
    assert all(slot['collected_label'] == 'UNKNOWN'
               and slot['collected_reason'] == 'AMPLITUDE_OUTSIDE_RULE_SUPPORT'
               and slot['label'] == slot['collected_label'] for slot in manifest['slots'][:10])
    receipt = load(session_dir / 'receipt.json')
    assert receipt['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert receipt['provisional_unknown_frames'] == list(SESSION.NORMAL_TAGS)
    assert all(row['provisional_unknown'] is True for row in receipt['frames'][:10])
    summary = SESSION.check_session(session_dir)
    assert summary['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert summary['provisional_unknown_count'] == 10


def test_the_standard_mode_still_refuses_the_provisional_val_frame(world, tmp_path):
    result, session_dir, _session = drive(
        world, tmp_path, 'val', 1, faults={'environment_labels': PROVISIONAL})
    assert result['passed'] is False
    assert result['stop_cause'] == 'VAL_NORMAL_NOT_CLEAR'
    assert result['stop_frame'] == 'normal-01'
    assert result['collection_mode'] == CAL.STANDARD_COLLECTION
    assert result['manifest_written'] is False
    assert not (session_dir / 'session-manifest.json').exists()


@pytest.mark.parametrize('mode', [CAL.STANDARD_COLLECTION, CAL.CALIBRATION_COLLECTION])
def test_prefit_sparse_pulse_is_retained_only_by_calibration_collection(world, tmp_path, mode):
    result, directory, _ = drive(world, tmp_path, 'val', 1,
        faults={'environment_labels': {'normal-01': ('PULSE', 'SPARSE_AMPLITUDE_OUTLIERS')}},
        collection_mode=mode)
    if mode == CAL.STANDARD_COLLECTION:
        assert result['stop_cause'] == 'VAL_NORMAL_NOT_CLEAR'
        assert result['manifest_written'] is False
        return
    assert result['passed'] is True, result
    summary = SESSION.check_session(directory)
    row = summary['frames'][0]
    assert row['collected_label'] == 'PULSE'
    assert row['collected_reason'] == 'SPARSE_AMPLITUDE_OUTLIERS'
    assert row['provisional_unknown'] is False
    assert summary['frame_count'] == 11


def test_real_campaign4_val_pulse_keeps_its_label_in_calibration_mode():
    directory = ROOT / 'evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign4/download/val-01'
    if not directory.is_dir():
        pytest.skip('local physical failure evidence is not present')
    plan, slots = BOARD.frame_plan('val', 1)
    frame = BOARD.evaluate_frame(directory, slots[0],
        session_block=BOARD.session_block_of(plan),
        domain=dict(CAL._profile_domain(CAL.TEACHING_PROFILE)[0]),
        environment_version='rf-window-environment-v4',
        release_id='1c0894bfe7751be2e1afa4e8bac564b8',
        collection_mode=CAL.CALIBRATION_COLLECTION)
    assert (frame['label'], frame['reason']) == ('PULSE', 'SPARSE_AMPLITUDE_OUTLIERS')
    assert frame['ber'] == 0 and frame['reference_sync'] is True


@pytest.mark.parametrize('label,reason', [
    ('NARROWBAND', 'SPECTRAL_EXCESS'), ('PULSE', 'UNSUPPORTED_PULSE_REASON'),
    ('UNKNOWN', 'BPSK_NOT_RELIABLE'), ('UNKNOWN', 'WEAK_SPECTRAL_ANOMALY'),
    ('INVALID', 'LOW_ENERGY')])
def test_any_other_val_label_still_stops_in_calibration_mode(world, tmp_path, label, reason):
    result, _session_dir, _session = drive(
        world, tmp_path, 'val', 1,
        faults={'environment_labels': {'normal-01': (label, reason)}},
        collection_mode=CAL.CALIBRATION_COLLECTION)
    assert result['passed'] is False
    assert result['stop_cause'] in ('VAL_NORMAL_NOT_CLEAR', 'NORMAL_NOT_RELIABLE_BPSK',
                                    'FRAME_INVALID_IQ')
    assert result['manifest_written'] is False


@pytest.mark.parametrize('mode', [CAL.STANDARD_COLLECTION, CAL.CALIBRATION_COLLECTION])
def test_an_off_frame_that_is_clear_is_refused_in_both_modes(world, tmp_path, mode):
    result, _session_dir, _session = drive(
        world, tmp_path, 'train', 1,
        faults={'environment_labels': {'off-01': ('CLEAR', 'NO_COVERED_ANOMALY_IN_WINDOW')}},
        collection_mode=mode)
    assert result['passed'] is False
    assert result['stop_cause'] == 'OFF_FALSE_CLEAR'
    assert result['stop_frame'] == 'off-01'


def test_an_unknown_mode_is_refused_before_any_capture(world, tmp_path):
    refusal('SESSION_COLLECTION_MODE', drive, world, tmp_path, 'train', 1,
            collection_mode='relaxed_acceptance')


# --------------------------------------------------- the three-session fake chain

@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    root = tmp_path_factory.mktemp('d6-chain')
    candidate = Path(STUB.build_offline_candidate(root / 'candidate')['path'])
    declaration = COLLECT.candidate_declaration(candidate)
    download = root / 'download'
    receipts = {}
    for split, index in SESSIONS:
        link = STUB.StubBoardLink(
            root / ('remote-%s%02d' % (split, index)), candidate=candidate,
            declaration=declaration,
            faults={'session': {'environment_labels': PROVISIONAL}})
        receipt = COLLECT.collect(candidate, download, split=split, index=index,
                                  cable_confirmed=True, authorized=True,
                                  board_factory=lambda link=link: link,
                                  collection_mode=CAL.CALIBRATION_COLLECTION)
        assert receipt['passed'] is True, receipt
        receipts[SESSION.session_id(split, index)] = receipt
    set_dir = root / 'set'
    adapted = ADAPTER.adapt(download, set_dir,
                            download_receipt=download / 'download-receipt.json',
                            physical=True)
    fit_dir = root / 'fit'
    fitted = CAL.fit_from_set(set_dir, fit_dir, require_physical=True)
    return dict(root=root, download=download, receipts=receipts, set_dir=set_dir,
                adapted=adapted, fit_dir=fit_dir, fitted=fitted)


def test_the_three_session_chain_collects_every_provisional_frame(chain):
    for session_id, receipt in chain['receipts'].items():
        assert receipt['collection_mode'] == CAL.CALIBRATION_COLLECTION
        assert receipt['board']['result']['passed'] is True
        assert receipt['board']['result']['collection_mode'] == CAL.CALIBRATION_COLLECTION
        assert receipt['validation']['passed'] is True
        assert receipt['validation']['collection_mode'] == CAL.CALIBRATION_COLLECTION
        assert receipt['validation']['provisional_unknown_count'] == 10
    manifest = load(chain['download'] / 'train-01' / 'session-manifest.json')
    assert manifest['slots'][10]['collected_label'] != 'CLEAR'
    assert manifest['slots'][10]['provisional_unknown'] is False


def test_the_chain_reaches_the_adapter_with_its_mode_and_labels_preserved(chain):
    set_document = load(chain['set_dir'] / 'set.json')
    assert set_document['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert len(set_document['provisional_unknown_frames']) == 30
    assert set_document['provisional_unknown_frames'][0] == 'train-01/normal-01'
    sidecar = load(chain['set_dir'] / 'frames' / 'val-01-normal-01.json')
    assert sidecar['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert sidecar['collected_label'] == 'UNKNOWN'
    assert sidecar['collected_reason'] == 'AMPLITUDE_OUTSIDE_RULE_SUPPORT'
    assert sidecar['provisional_unknown'] is True
    receipt = load(chain['set_dir'] / 'adapter-receipt.json')
    assert receipt['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert len(receipt['provisional_unknown_frames']) == 30


def test_the_fit_is_train_only_and_the_val_rule_is_unchanged(chain):
    fit = chain['fitted']
    assert fit['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert len(fit['provisional_unknown_frames']) == 30
    assert fit['counts']['train_normal'] == 20 and fit['counts']['val_normal'] == 10
    assert fit['verdict']['passed'] is True
    assert fit['verdict']['val_normal_clear'] == 10
    assert fit['verdict']['negative_false_clear'] == []
    assert len(fit['sources']) == 20
    assert all(source['split'] == 'train' for source in fit['sources'])
    assert fit['val_frame_ids'] and not set(fit['train_frame_ids']) & set(fit['val_frame_ids'])
    loaded = CAL.load_check(chain['fit_dir'] / 'baseline.json', require_formal=True)
    assert loaded['loaded'] is True and loaded['body_equal'] is True
    assert len(loaded['sources']) == 20


def test_a_mixed_mode_set_is_refused(chain, tmp_path):
    target = tmp_path / 'mixed'
    shutil.copytree(str(chain['download']), str(target))
    manifest_path = target / 'val-01' / 'session-manifest.json'
    manifest = load(manifest_path)
    manifest['collection_mode'] = CAL.STANDARD_COLLECTION
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    receipt_path = target / 'val-01' / 'receipt.json'
    receipt = load(receipt_path)
    receipt['collection_mode'] = CAL.STANDARD_COLLECTION
    receipt['session_manifest_sha256'] = STUB._digest(manifest_path)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    error = refusal('SESSION_FRAME_LABEL_MODE_MISMATCH', SESSION.check_session,
                    target / 'val-01')
    assert 'standard_acceptance' in error.detail


def test_a_hand_edited_sidecar_that_relabels_the_pair_is_refused(chain, tmp_path):
    target = tmp_path / 'relabelled'
    shutil.copytree(str(chain['set_dir']), str(target))
    sidecar_path = target / 'frames' / 'train-01-normal-01.json'
    sidecar = load(sidecar_path)
    sidecar['collected_label'] = 'CLEAR'
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding='utf-8')
    refusal('FRAME_LABEL_MODE_MISMATCH', CAL.validate_set, load(target / 'set.json'), target)


def test_a_hand_edited_manifest_that_adds_the_pair_without_the_flag_is_refused(
        chain, tmp_path):
    target = tmp_path / 'unflagged'
    shutil.copytree(str(chain['download'] / 'train-01'), str(target / 'train-01'))
    manifest_path = target / 'train-01' / 'session-manifest.json'
    manifest = load(manifest_path)
    manifest['slots'][0]['provisional_unknown'] = False
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    receipt_path = target / 'train-01' / 'receipt.json'
    receipt = load(receipt_path)
    receipt['session_manifest_sha256'] = STUB._digest(manifest_path)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    refusal('SESSION_FRAME_LABEL_MODE_MISMATCH', SESSION.check_session,
            target / 'train-01')


def test_an_unknown_declared_mode_is_refused_by_the_session_check(chain, tmp_path):
    target = tmp_path / 'unknown-mode'
    shutil.copytree(str(chain['download'] / 'train-01'), str(target / 'train-01'))
    manifest_path = target / 'train-01' / 'session-manifest.json'
    manifest = load(manifest_path)
    manifest['collection_mode'] = 'whatever'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    refusal('SESSION_COLLECTION_MODE', SESSION.check_session, target / 'train-01')

# -------------------------------------- the session check on a collected session

@pytest.fixture
def provisional_session(world, tmp_path):
    """One real, written session collected in calibration mode with the pair flagged."""
    result, session_dir, _session = drive(
        world, tmp_path, 'val', 1, faults={'environment_labels': PROVISIONAL},
        collection_mode=CAL.CALIBRATION_COLLECTION)
    assert result['passed'] is True, result
    return session_dir


def test_the_session_check_reports_the_mode_and_the_flagged_frames(provisional_session):
    summary = SESSION.check_session(provisional_session)
    assert summary['collection_mode'] == CAL.CALIBRATION_COLLECTION
    assert summary['provisional_unknown_count'] == 10
    assert summary['frames'][0]['collected_label'] == CAL.PROVISIONAL_UNKNOWN_LABEL
    assert summary['frames'][0]['collected_reason'] == CAL.PROVISIONAL_UNKNOWN_REASON
    assert summary['frames'][0]['provisional_unknown'] is True
    assert summary['frames'][10]['provisional_unknown'] is False


def test_a_provisional_pair_is_refused_under_the_standard_mode(provisional_session, tmp_path):
    target = tmp_path / 'downgraded'
    shutil.copytree(str(provisional_session), str(target))
    manifest_path = target / 'session-manifest.json'
    manifest = load(manifest_path)
    manifest['collection_mode'] = CAL.STANDARD_COLLECTION
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    STUB.refresh_receipt(target)
    receipt_path = target / 'receipt.json'
    receipt = load(receipt_path)
    receipt['collection_mode'] = CAL.STANDARD_COLLECTION
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    error = refusal('SESSION_FRAME_LABEL_MODE_MISMATCH', SESSION.check_session, target)
    assert 'standard_acceptance' in error.detail


def test_a_receipt_that_contradicts_the_manifest_mode_is_refused(provisional_session, tmp_path):
    target = tmp_path / 'contradiction'
    shutil.copytree(str(provisional_session), str(target))
    receipt_path = target / 'receipt.json'
    receipt = load(receipt_path)
    receipt['collection_mode'] = CAL.STANDARD_COLLECTION
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    refusal('SESSION_RECEIPT_MISMATCH', SESSION.check_session, target)

