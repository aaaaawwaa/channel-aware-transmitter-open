"""Host collection orchestrator over the stub board, through the adapter and fit.

Every session in this file is produced by the offline stub board: synthetic IQ,
stub release id, and an origin string that says so.  What is under test is the
whole production chain around it -- the authorization gate, the candidate domain
preflight, the per-session download receipt, and the fact that the calibration
adapter and fit consume the result unchanged.  No test here contacts a board.
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
from scripts import rf_environment_trainval_collect as COLLECT  # noqa: E402
from scripts import rf_environment_trainval_session as SESSION  # noqa: E402

SESSIONS = (('train', 1), ('train', 2), ('val', 1))


def refusal(code, function, *args, **kwargs):
    """One named refusal; the collect, session and adapter codes all carry one."""
    with pytest.raises(CAL.CalibrationError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class ExplodingBoard:
    """A board factory that must never be called by a refused collection."""

    def __call__(self):
        raise AssertionError('a refused collection opened a board link')


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('trainval-collect')
    candidate = Path(STUB.build_offline_candidate(root / 'candidate')['path'])
    declaration = COLLECT.candidate_declaration(candidate)
    download = root / 'download'
    receipts = {}
    for split, index in SESSIONS:
        link = STUB.StubBoardLink(root / ('remote-%s%02d' % (split, index)),
                                  candidate=candidate, declaration=declaration)
        receipt = COLLECT.collect(candidate, download, split=split, index=index,
                                  cable_confirmed=True, authorized=True,
                                  board_factory=lambda link=link: link)
        assert receipt['passed'] is True, receipt
        receipts[SESSION.session_id(split, index)] = receipt
    return dict(root=root, candidate=candidate, declaration=declaration,
                download=download, receipts=receipts,
                release_id=receipts['val-01']['candidate']['release_id'])


@pytest.fixture
def download_copy(world, tmp_path):
    def make(name='download'):
        target = tmp_path / name
        shutil.copytree(str(world['download']), str(target))
        return target
    return make


# ------------------------------------------------------------------- the gate

def test_collect_refuses_without_authorization_before_any_board(tmp_path):
    target = tmp_path / 'never'
    error = refusal('BOARD_COLLECTION_NOT_AUTHORIZED', COLLECT.collect, None, target,
                    split='train', index=1, cable_confirmed=True, authorized=False,
                    board_factory=ExplodingBoard())
    assert 'no connection' in error.detail or 'connection' in error.detail
    assert not target.exists()


def test_collect_refuses_without_the_cable_attestation(world):
    error = refusal('CABLE_CONFIRMATION_REQUIRED', COLLECT.collect, world['candidate'],
                    world['root'] / 'never', split='train', index=1,
                    cable_confirmed=False, authorized=True, board_factory=ExplodingBoard())
    assert 'cable' in error.detail
    assert not (world['root'] / 'never').exists()


def test_collect_refuses_the_unmigrated_candidate_baseline(world):
    error = refusal('CANDIDATE_BASELINE_DOMAIN_MISMATCH', COLLECT.collect,
                    STUB.CANDIDATE_BASE, world['root'] / 'never', split='train', index=1,
                    cable_confirmed=True, authorized=True, board_factory=ExplodingBoard())
    assert '40000' in error.detail or '40000' in str(error.code)
    assert not (world['root'] / 'never').exists()


def test_collect_refuses_a_second_collection_of_one_session(world):
    error = refusal('SESSION_ALREADY_COLLECTED', COLLECT.collect, world['candidate'],
                    world['download'], split='train', index=1, cable_confirmed=True,
                    authorized=True, board_factory=ExplodingBoard())
    assert 'train-01' in error.detail


def test_the_session_entry_refuses_the_authorized_route_without_its_inputs(world):
    target = world['root'] / 'never'
    refusal('SESSION_COLLECT_CANDIDATE_REQUIRED', SESSION.collect_session, 'val', 1,
            target, authorized=True, candidate=None, cable_confirmed=True)
    refusal('SESSION_COLLECT_CABLE_CONFIRMATION_REQUIRED', SESSION.collect_session, 'val', 1,
            target, authorized=True, candidate=world['candidate'], cable_confirmed=False)
    assert not target.exists()


# ----------------------------------------------------------- the collected chain

def test_every_collected_session_binds_its_download(world):
    receipt = load(world['download'] / 'download-receipt.json')
    assert receipt['sessions'] == ['train-01', 'train-02', 'val-01']
    assert receipt['physical'] is True and receipt['fixture'] is False
    assert len(receipt['evidence_sha256']) == 204
    assert all('\\' not in key for key in receipt['evidence_sha256'])
    for session_id, collected in world['receipts'].items():
        assert collected['session']['session_closed'] is True
        assert collected['session']['disarm_ack'] is True
        assert collected['validation']['passed'] is True
        assert collected['validation']['frame_count'] == 11
        assert collected['board']['preflight']['ok'] is True
        assert collected['board']['exit_code'] == 0
        assert collected['download']['errors'] == []
        session_dir = world['download'] / session_id
        assert len(sorted(session_dir.glob('*.iq'))) == 11
        assert (session_dir / 'session-manifest.json').is_file()
        assert (session_dir / 'receipt.json').is_file()
    last = world['receipts']['val-01']
    assert last['adapter_ready'] is True


def test_the_download_adapts_to_a_physical_set_and_fits(world, tmp_path):
    set_dir = tmp_path / 'set'
    result = ADAPTER.adapt(world['download'], set_dir,
                           download_receipt=world['download'] / 'download-receipt.json',
                           physical=True)
    assert result['provenance']['kind'] == 'new_physical_train_val'
    assert result['fixture']['not_for_formal_acceptance'] is False
    assert result['counts'] == dict(sessions=3, train_sessions=2, val_sessions=1, frames=33,
                                    normal=30, off=3, train_normal=20, val_normal=10,
                                    test_frames=0)
    set_document = load(set_dir / 'set.json')
    assert set_document['domain']['attenuation_mdB'] == 46000
    assert set_document['environment_version'] == 'rf-window-environment-v4'
    assert set_document['provenance']['kind'] == 'new_physical_train_val'
    assert all(session['download']['session_closed'] and session['download']['disarm_ack']
               for session in set_document['sessions'])
    fit_dir = tmp_path / 'fit'
    fit = CAL.fit_from_set(set_dir, fit_dir, require_physical=True)
    assert fit['verdict']['passed'] is True
    assert fit['verdict']['val_normal_clear'] == 10
    assert fit['verdict']['negative_false_clear'] == []
    assert fit['counts']['train_normal'] == 20 and fit['counts']['negative_off'] == 3
    assert fit['provenance']['kind'] == 'new_physical_train_val'
    assert fit['formal_physical'] == 'DECLARED_BY_SET_NOT_VERIFIED_HERE'
    loaded = CAL.load_check(fit_dir / 'baseline.json', require_formal=True)
    assert loaded['loaded'] is True and loaded['body_equal'] is True
    assert len(loaded['sources']) == 20
    assert loaded['rf_config']['attenuation_mdB'] == 46000


def test_the_adapter_refuses_a_physical_download_without_the_flag(world, tmp_path):
    refusal('PHYSICAL_PROVENANCE_UNVERIFIED', ADAPTER.adapt, world['download'],
            tmp_path / 'set', download_receipt=world['download'] / 'download-receipt.json')


def test_the_adapter_refuses_a_physical_download_without_a_host_receipt(world, tmp_path):
    target = tmp_path / 'download'
    shutil.copytree(str(world['download']), str(target))
    (target / 'download-receipt.json').unlink()
    refusal('DOWNLOAD_RECEIPT_MISSING', ADAPTER.adapt, target, tmp_path / 'set',
            physical=True)


# ------------------------------------------------------------- download negatives

def test_a_tampered_download_receipt_digest_is_named(world, tmp_path):
    target = tmp_path / 'download'
    shutil.copytree(str(world['download']), str(target))
    receipt = load(target / 'download-receipt.json')  # the receipt itself is the lie
    key = sorted(receipt['evidence_sha256'])[0]
    receipt['evidence_sha256'][key] = '0' * 64
    (target / 'download-receipt.json').write_text(json.dumps(receipt, indent=2),
                                                  encoding='utf-8')
    refusal('DOWNLOAD_RECEIPT_SHA_MISMATCH', ADAPTER.adapt, target, tmp_path / 'set',
            download_receipt=target / 'download-receipt.json', physical=True)


def test_a_missing_frame_artifact_after_download_is_named(world, tmp_path):
    target = tmp_path / 'download'
    shutil.copytree(str(world['download']), str(target))
    (target / 'val-01' / 'normal-03.iq').unlink()
    refusal('SESSION_FRAME_MISSING', ADAPTER.adapt, target, tmp_path / 'set',
            download_receipt=target / 'download-receipt.json', physical=True)


def test_a_byte_digest_mismatch_after_download_is_named(world, tmp_path):
    target = tmp_path / 'download'
    shutil.copytree(str(world['download']), str(target))
    path = target / 'train-02' / 'normal-04.iq'
    payload = bytearray(path.read_bytes())
    payload[7] ^= 0x01
    path.write_bytes(bytes(payload))
    refusal('SESSION_FRAME_IQ_SHA_MISMATCH', ADAPTER.adapt, target, tmp_path / 'set',
            download_receipt=target / 'download-receipt.json', physical=True)


def test_an_identity_mismatch_after_download_is_named(world, tmp_path):
    target = tmp_path / 'download'
    shutil.copytree(str(world['download']), str(target))
    STUB.drop_identity(target / 'val-01', 'normal-02')
    (target / 'download-receipt.json').unlink()
    COLLECT.write_download_receipt(target, world['release_id'])
    refusal('SESSION_FRAME_IDENTITY_MISMATCH', ADAPTER.adapt, target, tmp_path / 'set',
            download_receipt=target / 'download-receipt.json', physical=True)


def test_a_cross_split_iq_leak_is_named(world, tmp_path):
    target = tmp_path / 'download'
    shutil.copytree(str(world['download']), str(target))
    payload = (target / 'train-01' / 'normal-01.iq').read_bytes()
    STUB.rebind_iq(target / 'val-01', 'normal-01', payload)
    (target / 'download-receipt.json').unlink()
    COLLECT.write_download_receipt(target, world['release_id'])
    refusal('CROSS_SPLIT_LEAKAGE', ADAPTER.adapt, target, tmp_path / 'set',
            download_receipt=target / 'download-receipt.json', physical=True)


def test_a_stopped_session_is_downloaded_as_evidence_and_not_adaptable(world, tmp_path):
    root = tmp_path / 'stopped'
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(session=dict(clipped='normal-02')))
    receipt = COLLECT.collect(world['candidate'], root, split='train', index=1,
                              cable_confirmed=True, authorized=True,
                              board_factory=lambda: link)
    assert receipt['passed'] is False
    assert receipt['stop_cause'] == 'FRAME_CLIPPED'
    assert receipt['validation']['error']['code'] == 'SESSION_MANIFEST_MISSING'
    assert len(sorted((root / 'train-01').glob('*.iq'))) == 2
    assert (root / 'session-result-train-01.json').is_file()
    assert receipt['adapter_ready'] is False


def test_a_dropped_download_is_recorded_and_not_adaptable(world, tmp_path):
    root = tmp_path / 'dropped'
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(drop_download='normal-03.iq'))
    receipt = COLLECT.collect(world['candidate'], root, split='train', index=1,
                              cable_confirmed=True, authorized=True,
                              board_factory=lambda: link)
    assert receipt['passed'] is False
    assert receipt['download']['errors']
    assert receipt['download']['errors'][0]['error'] == 'stub: download dropped'
    assert receipt['validation']['error']['code'] == 'SESSION_FRAME_MISSING'
    assert not (root / 'train-01' / 'normal-03.iq').exists()
    assert (root / 'train-01' / 'normal-03.json').is_file()


def test_an_unclosed_session_is_downloaded_but_never_a_physical_set(world, tmp_path):
    root = tmp_path / 'unclosed'
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(session=dict(disarm_not_acked=True)))
    receipt = COLLECT.collect(world['candidate'], root, split='train', index=1,
                              cable_confirmed=True, authorized=True,
                              board_factory=lambda: link)
    assert receipt['passed'] is False and receipt['stop_cause'] == 'CLOSE_UNCONFIRMED'
    assert receipt['validation']['passed'] is True
    assert receipt['session']['disarm_ack'] is False
    target = tmp_path / 'three'
    shutil.copytree(str(world['download']), str(target))
    shutil.rmtree(str(target / 'train-01'))
    shutil.copytree(str(root / 'train-01'), str(target / 'train-01'))
    (target / 'download-receipt.json').unlink()
    COLLECT.write_download_receipt(target, world['release_id'])
    refusal('PHYSICAL_SESSION_NOT_CLOSED', ADAPTER.adapt, target, tmp_path / 'set',
            download_receipt=target / 'download-receipt.json', physical=True)


# ----------------------------------------------------------- preflight privilege
# 2026-10-18 board preflight: run unprivileged it could not even import
# merged_release (the installed release directories are 'dr-x------ root root'), the
# result document was never written, link.fetch() failed, and the
# BOARD_PREFLIGHT_FAILED / BOARD_PREFLIGHT_MISMATCH checks lived in the else branch
# of that try, so the collection went on to capture.  The preflight is read-only
# (no client, no socket, no RF), so it must run under the same privilege as the
# session, and a missing result document must refuse by name.

class FakeSftpDirectory:
    def __init__(self, calls):
        self.calls = calls

    def mkdir(self, path):
        self.calls.append(path)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeBoardClient:
    def __init__(self, calls):
        self.calls = calls

    def open_sftp(self):
        return FakeSftpDirectory(self.calls)

    def close(self):
        self.calls.append('close')


class FakeSshHelper:
    def __init__(self, calls):
        self.calls = calls
        self.client = FakeBoardClient(calls)

    def connect(self):
        return self.client


def test_the_board_link_creates_the_remote_session_directory(tmp_path, monkeypatch):
    """<stage>/out is what the board session writes into; the link must make it."""
    from scripts import rf_teaching_deploy as DEPLOY
    calls = []
    monkeypatch.setattr(DEPLOY, 'load_helper', lambda: FakeSshHelper(calls))
    link = COLLECT.SftpBoardLink.open()
    try:
        assert link.session_dir == link.remote + '/out'
        assert calls == [link.remote, link.remote + '/scripts', link.remote + '/out'], calls
    finally:
        link.close()


def test_the_board_preflight_runs_privileged_and_read_only(world, tmp_path):
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'])
    receipt = COLLECT.collect(world['candidate'], tmp_path / 'root', split='train',
                              index=1, cable_confirmed=True, authorized=True,
                              board_factory=lambda: link)
    assert receipt['passed'] is True
    preflights = [run for run in link.runs if '--preflight' in run['argv']]
    captures = [run for run in link.runs if '--preflight' not in run['argv']]
    assert len(preflights) == 1 and preflights[0]['sudo'] is True, link.runs
    assert len(captures) == 1 and captures[0]['sudo'] is True, link.runs
    document = load(tmp_path / 'root' / 'preflight-train-01.json')
    assert document['ok'] is True
    assert document['captures_started'] is False and document['rf_actions'] is False


class SessionCommandSpy:
    """Injectable board runner: counts the REAL session commands at dispatch time.

    It wraps the link the collection entry was handed and records every run() it asks
    for; a session command is any run that is not the read-only preflight.  A refused
    preflight must leave that count at exactly zero -- an assertion about the error text
    alone cannot prove that no capture was started (2026-10-18: the gate was skipped and
    the 11-frame session command ran anyway).
    """

    def __init__(self, link):
        self.link = link
        self.dispatches = []
        self.session_commands = 0

    def run(self, argv, log, *, sudo=True, timeout=240):
        entry = dict(argv=[str(argument) for argument in argv], sudo=bool(sudo))
        self.dispatches.append(entry)
        if '--preflight' not in entry['argv']:
            self.session_commands += 1
        return self.link.run(argv, log, sudo=sudo, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self.link, name)


def refusal_receipt(code, link, candidate, root):
    """The preflight gate records its named stop cause; no session command is dispatched."""
    spy = SessionCommandSpy(link)
    receipt = COLLECT.collect(candidate, root, split='train', index=1,
                              cable_confirmed=True, authorized=True,
                              board_factory=lambda: spy)
    assert receipt['passed'] is False
    assert receipt['stop_cause'] == code, receipt
    assert spy.session_commands == 0, spy.dispatches      # ZERO real session commands
    assert len(spy.dispatches) == 1, spy.dispatches       # only the preflight was run
    assert spy.dispatches[0]['sudo'] is True, spy.dispatches
    assert (root / 'preflight-train-01.log').is_file()
    assert not (root / 'session-result-train-01.json').exists()
    assert not (root / 'train-01').exists()
    return receipt


def preflight_faults(world):
    """One injected fault per refusal class the gate has to stop on."""
    wrong_domain = dict(world['declaration']['domain'], attenuation_mdB=40000)
    return {
        'missing-result': dict(missing_result=True, exit_code=1),
        'empty-result': dict(empty_result=True),
        'unparseable-result': dict(unparseable_result=True),
        'nonzero-exit': dict(exit_code=1, document=dict(ok=True)),
        'ok-not-true': dict(exit_code=0, document=dict(ok=False)),
        'release-mismatch': dict(document=dict(release_id='b' * 32)),
        'domain-mismatch': dict(document=dict(domain=wrong_domain)),
        'contract-mismatch': dict(document=dict(software_contract='cnn-bpsk-teaching-v1')),
        'version-mismatch': dict(document=dict(environment_version='rf-window-environment-v0')),
    }


PREFLIGHT_REFUSALS = (
    ('missing-result', 'BOARD_PREFLIGHT_NOT_EVALUATED'),
    ('empty-result', 'BOARD_PREFLIGHT_MISSING'),
    ('unparseable-result', 'BOARD_PREFLIGHT_MISSING'),
    ('nonzero-exit', 'BOARD_PREFLIGHT_FAILED'),
    ('ok-not-true', 'BOARD_PREFLIGHT_FAILED'),
    ('release-mismatch', 'BOARD_PREFLIGHT_MISMATCH'),
    ('domain-mismatch', 'BOARD_PREFLIGHT_MISMATCH'),
    ('contract-mismatch', 'BOARD_PREFLIGHT_MISMATCH'),
    ('version-mismatch', 'BOARD_PREFLIGHT_MISMATCH'),
)


@pytest.mark.parametrize('case,code', PREFLIGHT_REFUSALS)
def test_every_preflight_refusal_is_named_privileged_and_dispatches_no_session(
        world, tmp_path, case, code):
    """Exit code, result document, ok flag, identity, contract, domain, version.

    Each class stops before the session command: the runner spy must report zero real
    session dispatches, and the receipt must show the session leg never ran.
    """
    link = STUB.StubBoardLink(tmp_path / ('remote-' + case), candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(preflight=preflight_faults(world)[case]))
    receipt = refusal_receipt(code, link, world['candidate'], tmp_path / ('root-' + case))
    assert receipt['board']['exit_code'] is None, receipt['board']
    assert receipt['board']['result'] is None
    assert not (tmp_path / ('root-' + case) / 'run-train-01.log').exists()


def test_the_dispatch_count_comes_from_the_runner_not_from_the_error_text(world, tmp_path):
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(preflight=dict(exit_code=0,
                                                         document=dict(ok=False))))
    spy = SessionCommandSpy(link)
    receipt = COLLECT.collect(world['candidate'], tmp_path / 'root', split='train', index=1,
                              cable_confirmed=True, authorized=True, board_factory=lambda: spy)
    assert receipt['stop_cause'] == 'BOARD_PREFLIGHT_FAILED'
    assert spy.session_commands == 0, spy.dispatches
    assert [entry['argv'][1] for entry in spy.dispatches] == ['--preflight']
    # the stub's own dispatch log agrees, and the one run really was privileged
    assert link.session_commands == [] and len(link.preflight_commands) == 1
    assert spy.dispatches[0]['sudo'] is True


def test_a_preflight_without_a_result_document_refuses_by_name_and_captures_nothing(
        world, tmp_path):
    """The 2026-10-18 shape: exit 1 and no result document at all."""
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(preflight=dict(missing_result=True, exit_code=1)))
    receipt = refusal_receipt('BOARD_PREFLIGHT_NOT_EVALUATED', link, world['candidate'],
                              tmp_path / 'root')
    assert 'preflight' in receipt['error']
    assert len(link.runs) == 1 and link.closed is True


def test_a_preflight_that_refused_stops_before_any_session(world, tmp_path):
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(preflight=dict(exit_code=1,
                                                         document=dict(ok=False))))
    refusal_receipt('BOARD_PREFLIGHT_FAILED', link, world['candidate'],
                    tmp_path / 'root')


def test_a_preflight_domain_mismatch_stops_before_any_session(world, tmp_path):
    wrong = dict(world['declaration']['domain'], attenuation_mdB=40000)
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(preflight=dict(document=dict(domain=wrong))))
    refusal_receipt('BOARD_PREFLIGHT_MISMATCH', link, world['candidate'],
                    tmp_path / 'root')


def test_a_session_that_left_no_result_document_is_named(world, tmp_path):
    """A refused board entry leaves no result document: name that, do not stay silent."""
    link = STUB.StubBoardLink(tmp_path / 'remote', candidate=world['candidate'],
                              declaration=world['declaration'],
                              faults=dict(session_refuses=True))
    receipt = COLLECT.collect(world['candidate'], tmp_path / 'root', split='train',
                              index=1, cable_confirmed=True, authorized=True,
                              board_factory=lambda: link)
    assert receipt['passed'] is False
    assert receipt['stop_cause'] == 'BOARD_SESSION_NOT_EVALUATED', receipt
    assert receipt['board']['result'] is None
    assert receipt['board']['preflight']['ok'] is True
    assert receipt['validation']['passed'] is False
    assert len(link.runs) == 2, link.runs   # the privileged preflight, then the session
