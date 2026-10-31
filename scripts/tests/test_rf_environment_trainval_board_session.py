"""Board-side 11-frame session envelope, driven by the stub board (no hardware).

The stub produces synthetic frames, and every assertion here is about the session
ENVELOPE the board driver owns: order, identity, labels, requested/actual RF,
rail/clip, closure, the manifest/receipt the adapter consumes, and the named stop
on the first hit.  No test in this file talks to a board, arms anything or claims
a physical capture.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'scripts' / 'tests')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import trainval_stub_board as STUB  # noqa: E402
from scripts import rf_environment_trainval_board_session as BOARD  # noqa: E402
from scripts import rf_environment_trainval_collect as COLLECT  # noqa: E402
from scripts import rf_environment_trainval_session as SESSION  # noqa: E402

TAGS = list(SESSION.FRAME_TAGS)


@pytest.fixture(scope='module')
def candidate(tmp_path_factory):
    target = tmp_path_factory.mktemp('trainval-board') / 'candidate'
    return STUB.build_offline_candidate(target)


@pytest.fixture(scope='module')
def declaration(candidate):
    return COLLECT.candidate_declaration(Path(candidate['path']))


@pytest.fixture
def stub_run(tmp_path, declaration):
    def run(split='train', index=1, *, faults=None, expected_release=None,
            domain=None, environment_version=None):
        session_dir = tmp_path / SESSION.session_id(split, index)
        # The stub session never creates --output (it must not hide the board entry's
        # job); whoever calls run_session() directly owns the directory, as the host
        # link and the entry do.  The missing-directory case has its own test below.
        session_dir.mkdir(parents=True, exist_ok=True)
        session = STUB.StubSession(
            split, index, session_dir, release=STUB.STUB_RELEASE,
            domain=domain or declaration['domain'],
            environment_version=environment_version or declaration['environment_version'],
            faults=faults)
        result = BOARD.run_session(
            session, split=split, index=index, output=session_dir,
            expected_release=expected_release or session.release,
            domain=session.domain, environment_version=session.environment_version,
            result_path=tmp_path / 'session-result.json', origin=BOARD.STUB_ORIGIN,
            requested_rf=dict(declaration['domain']))
        return result, session_dir
    return run


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


# ------------------------------------------------------------------ positive path

def test_a_complete_stub_session_has_the_eleven_frame_shape(stub_run):
    result, session_dir = stub_run()
    assert result['passed'] is True and result['stop_cause'] is None
    assert result['manifest_written'] is True
    assert len(result['frames']) == 11
    assert [row['tag'] for row in result['frames']] == TAGS
    assert [row['scenario'] for row in result['frames']] == ['normal'] * 10 + ['off']
    assert result['session_closed'] is True and result['disarm_ack'] is True
    summary = SESSION.check_session(session_dir)
    assert summary['fixture'] is False and summary['collected'] is True
    assert summary['physical'] is True
    assert summary['frame_count'] == 11 and summary['normal_count'] == 10
    assert summary['off_count'] == 1
    assert summary['domain']['attenuation_mdB'] == 46000
    assert summary['environment_version'] == 'rf-window-environment-v4'
    receipt = load(session_dir / 'receipt.json')
    assert receipt['complete'] is True and len(receipt['evidence_sha256']) == 33
    assert receipt['session_closed'] is True and receipt['disarm_ack'] is True
    assert receipt['provenance']['kind'] == 'board_session'
    assert receipt['origin'] == BOARD.STUB_ORIGIN


def test_the_session_records_labels_reason_and_the_per_frame_rf(stub_run):
    result, session_dir = stub_run()
    normal = [row for row in result['frames'] if row['scenario'] == 'normal']
    off = [row for row in result['frames'] if row['scenario'] == 'off']
    assert all(row['label'] == 'CLEAR' for row in normal)
    assert all(row['reference_sync'] is True and row['ber'] == 0 for row in normal)
    assert all(row['reason'] for row in normal)
    assert len(normal) == 10 and len(off) == 1
    assert off[0]['label'] != 'CLEAR'
    assert off[0]['reference_sync'] is not True and off[0]['ber'] is None
    assert all(row['rail'] is False for row in result['frames'])
    for row in result['frames']:
        assert row['requested_rf']['attenuation_mdB'] == 46000
        actual = row['actual_rf']
        assert actual['post_readback']['tx_source_selected'] is False
        assert actual['attenuation_readback'] is None
        assert actual['attenuation_readback_reason'] == 'NOT_READ_BACK_ON_ENVIRONMENT_PATH'
    manifest = load(session_dir / 'session-manifest.json')
    assert manifest['requested_rf']['attenuation_mdB'] == 46000
    assert all(slot['label'] == 'CLEAR' for slot in manifest['slots'][:10])
    assert manifest['slots'][10]['label'] != 'CLEAR'
    assert all(slot['physical'] is False for slot in manifest['slots'][10:])
    assert all(slot['physical'] is True for slot in manifest['slots'][:10])


def test_the_session_artifacts_say_they_are_a_stub_and_not_a_capture(stub_run):
    _result, session_dir = stub_run()
    metadata = load(session_dir / 'normal-01.json')
    sidecar = load(session_dir / 'normal-01.environment.json')
    assert metadata['origin'] == BOARD.STUB_ORIGIN
    assert sidecar['origin'] == BOARD.STUB_ORIGIN
    assert metadata['fixture'] is False and metadata['collected'] is True
    assert metadata['physical'] is True
    assert 'NOT a physical capture' in metadata['origin']
    assert (session_dir / 'records').is_dir()


def test_a_val_session_is_evaluated_against_the_clear_rule(stub_run):
    result, _session_dir = stub_run('val', 1)
    assert result['passed'] is True
    normal = [row for row in result['frames'] if row['scenario'] == 'normal']
    assert len(normal) == 10 and all(row['label'] == 'CLEAR' for row in normal)
    assert result['frames'][-1]['scenario'] == 'off'


# --------------------------------------------------------------------- stop set

def refusal(code, function, *args, **kwargs):
    with pytest.raises(SESSION.SessionError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value


def test_a_code_rail_frame_stops_the_session_and_keeps_the_evidence(stub_run):
    result, session_dir = stub_run(faults={'clipped': 'normal-03'})
    assert result['passed'] is False and result['stop_cause'] == 'FRAME_CLIPPED'
    assert result['stop_frame'] == 'normal-03'
    assert result['manifest_written'] is False
    assert not (session_dir / 'session-manifest.json').exists()
    assert (session_dir / 'normal-03.iq').is_file()
    assert len([path for path in session_dir.glob('*.iq')]) == 3
    assert result['session_closed'] is True and result['disarm_ack'] is True
    refusal('SESSION_MANIFEST_MISSING', SESSION.check_session, session_dir)


def test_an_unsafe_egress_readback_stops_the_session(stub_run):
    result, _session_dir = stub_run(faults={'egress_unsafe': 'normal-02'})
    assert result['passed'] is False and result['stop_cause'] == 'EGRESS_UNSAFE'
    assert result['manifest_written'] is False


def test_a_failed_capture_stops_the_session(stub_run):
    result, _session_dir = stub_run(faults={'capture_fails': 'normal-04'})
    assert result['passed'] is False and result['stop_cause'] == 'CAPTURE_FAILED'
    assert result['manifest_written'] is False
    assert result['session_closed'] is True


def test_a_duplicate_iq_digest_is_refused_at_binding(stub_run):
    result, session_dir = stub_run(faults={'duplicate_iq': 'normal-05'})
    assert result['passed'] is False and result['stop_cause'] == 'FRAME_IQ_DUPLICATE'
    assert result['manifest_written'] is False
    assert not (session_dir / 'session-manifest.json').exists()


def test_an_identity_mismatch_is_refused(stub_run):
    result, _session_dir = stub_run(faults={'identity_mismatch': 'normal-04'})
    assert result['passed'] is False and result['stop_cause'] == 'FRAME_IDENTITY_MISMATCH'


def test_a_missing_frame_artifact_is_refused(stub_run):
    result, _session_dir = stub_run(faults={'skip_iq': 'off-01'})
    assert result['passed'] is False and result['stop_cause'] == 'FRAME_ARTIFACT_MISSING'


def test_an_unclosed_session_keeps_its_documents_and_is_named(stub_run):
    result, session_dir = stub_run(faults={'disarm_not_acked': True})
    assert result['passed'] is False and result['stop_cause'] == 'CLOSE_UNCONFIRMED'
    assert result['manifest_written'] is True
    summary = SESSION.check_session(session_dir)
    assert summary['disarm_ack'] is False
    assert summary['session_closed'] is True


def test_a_release_mismatch_is_refused_before_any_capture(stub_run):
    result, session_dir = stub_run(expected_release='stubboard-other')
    assert result['passed'] is False and result['stop_cause'] == 'RELEASE_MISMATCH'
    assert not list(session_dir.glob('*.iq'))
    assert not (session_dir / 'session-manifest.json').exists()


def test_the_post_readback_must_release_the_tx_source(stub_run):
    result, _session_dir = stub_run(faults={'tx_source_selected': 'normal-06'})
    assert result['passed'] is False and result['stop_cause'] == 'FRAME_READBACK_UNSAFE'


def test_a_nonzero_board_rx_draft_revision_is_accepted_and_a_negative_is_not(stub_run):
    """A real board frame carries revision >= 1 (spec G.8); a negative one is refused."""
    result, _session_dir = stub_run(faults={'submitted_revision': 3})
    assert result['passed'] is True
    result, _session_dir = stub_run(faults={'submitted_revision': -1})
    assert result['passed'] is False
    assert result['stop_cause'] == 'FRAME_DEMOD_CONFIG_MISMATCH'


def test_a_frame_carrying_another_domain_is_refused(stub_run, declaration):
    """A frame that records a different RF domain cannot enter this session."""
    result, _session_dir = stub_run(faults={
        'sidecar_domain': dict(declaration['domain'], attenuation_mdB=40000)})
    assert result['passed'] is False
    assert result['stop_cause'] == 'FRAME_DOMAIN_MISMATCH'


# ------------------------------------------------------------------ preflight/cli

def test_the_board_preflight_is_read_only_and_named(candidate, declaration):
    document = BOARD.preflight(candidate['release_id'], 'train', 1,
                               release_directory=Path(candidate['path']))
    assert document['ok'] is True
    assert document['release_id'] == candidate['release_id']
    assert document['software_contract'] == 'cnn-bpsk-environment-v1'
    assert document['domain']['attenuation_mdB'] == 46000
    assert document['environment_version'] == 'rf-window-environment-v4'
    assert document['baseline_sources'] == declaration['baseline_sources']
    assert document['captures_started'] is False and document['rf_actions'] is False
    assert [row['tag'] for row in document['frames']] == TAGS


def test_the_board_preflight_cli_writes_a_read_only_document(candidate, tmp_path, capsys):
    result_path = tmp_path / 'preflight.json'
    assert BOARD.main(['--expected-release', candidate['release_id'], '--split', 'train',
                       '--session', '2', '--preflight', '--cable-confirmed',
                       '--release-directory', str(candidate['path']),
                       '--result', str(result_path)]) == 0
    document = load(result_path)
    assert document['ok'] is True
    assert document['captures_started'] is False and document['rf_actions'] is False
    assert [row['tag'] for row in document['frames']] == TAGS
    printed = json.loads(capsys.readouterr().out)
    assert printed['session']['session_id'] == 'train-02'
    assert printed['release_id'] == candidate['release_id']


def test_an_unreadable_release_is_named_not_a_module_not_found(tmp_path, monkeypatch):
    """The installed release dirs are root-only; an unprivileged import fails (defect B).

    The 2026-10-18 board preflight died here with a bare ModuleNotFoundError and wrote
    no result document, which the host then treated as "preflight skipped".
    """
    empty = tmp_path / 'release-without-modules'
    empty.mkdir()
    # The board's unprivileged process had no importable merged_release anywhere:
    # PYTHONPATH held only /home/xilinx and the root-only current/ could not be read.
    monkeypatch.delitem(sys.modules, 'merged_release', raising=False)
    monkeypatch.setattr(sys, 'path', [str(empty)])
    error = refusal('RELEASE_UNREADABLE', BOARD.preflight, 'any-release', 'train', 1,
                    release_directory=empty)
    assert 'privileged' in error.detail


def test_the_board_preflight_refuses_the_unmigrated_candidate():
    """The 46 dB candidate in the evidence tree still carries the 40 dB / v3 baseline."""
    manifest = json.loads((STUB.CANDIDATE_BASE / 'merged_release_manifest.json')
                          .read_text(encoding='utf-8'))
    error = refusal('BASELINE_INVALID', BOARD.preflight, manifest['release_id'],
                    'train', 1, release_directory=STUB.CANDIDATE_BASE)
    assert 'unsupported frozen baseline version' in error.detail


def test_the_board_cli_refuses_without_the_cable_attestation(tmp_path):
    with pytest.raises(SystemExit) as info:
        BOARD.main(['--expected-release', STUB.STUB_RELEASE, '--split', 'train',
                    '--session', '1', '--output', str(tmp_path / 'never')])
    assert 'cable' in str(info.value)
    assert not (tmp_path / 'never').exists()


# ------------------------------------------------- the session output directory
# 2026-10-18 board run train-01: the stage root existed, <stage>/out did not, and the
# session died on the first frame with a raw FileNotFoundError for
# '<stage>/out/normal-01.json' instead of a named stop.  The board session writes
# <tag>.iq/<tag>.json in place (rf_bpsk_ui_board_session.py:156-184) and
# write_record() only creates <output>/records (rf_experiment_record.py:86-90), so the
# entry itself must own --output, exactly like its two sibling entries
# (rf_bpsk_ui_board_session.py:367, rf_environment_board_session.py:96).

class NoMkdirBoardSession:
    """The production session shape: writes into --output, never creates it."""

    def __init__(self, expected, output, domain, environment_version):
        self.expected, self.output = expected, Path(output)
        self.domain, self.environment_version = dict(domain), environment_version
        self.inner = None

    def configure(self, split, index):
        self.inner = STUB.StubSession(split, index, self.output, release=self.expected,
                                      domain=self.domain,
                                      environment_version=self.environment_version)
        document, _slots = BOARD.frame_plan(split, index)
        return dict(BOARD.session_block_of(document))

    def requested_rf(self):
        return dict(self.domain)

    def __getattr__(self, name):
        inner = self.__dict__.get('inner')
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)


def test_the_board_entry_creates_the_session_output_directory(candidate, tmp_path,
                                                             monkeypatch):
    """The board entry must prepare --output before the first frame is written."""
    session_dir = tmp_path / 'rf-trainval-collect-0123456789abcdef' / 'out'
    assert not session_dir.exists()
    monkeypatch.setattr(BOARD, 'trainval_session_class', lambda: NoMkdirBoardSession)
    result_path = tmp_path / 'session-result.json'
    assert BOARD.main(['--expected-release', candidate['release_id'], '--split', 'train',
                       '--session', '1', '--output', str(session_dir),
                       '--result', str(result_path), '--cable-confirmed',
                       '--release-directory', str(candidate['path'])]) == 0
    result = load(result_path)
    assert result['passed'] is True and result['manifest_written'] is True
    # the REAL fixture identity, on the session and on every frame document: this is the
    # assertion the two 2026-10-18 tests lost when they mixed the candidate with the
    # fixed stub release id (they must never be repaired by dropping it).
    assert result['release_id'] == candidate['release_id']
    for tag in TAGS:
        assert (session_dir / (tag + '.json')).is_file(), tag
        assert (session_dir / (tag + '.iq')).is_file(), tag
        assert (session_dir / (tag + '.environment.json')).is_file(), tag
        assert load(session_dir / (tag + '.json'))['release_id'] == candidate['release_id']
    assert SESSION.check_session(session_dir)['release_id'] == candidate['release_id']
    assert SESSION.check_session(session_dir)['frame_count'] == len(TAGS)


def test_the_stub_session_never_creates_its_own_output_directory(declaration, tmp_path):
    """A test double must not silently provide a directory the entry is supposed to own.

    trainval_stub_board.StubSession used to mkdir(create_output=True), which is exactly
    what hid the 2026-10-18 defect (<stage>/out never existed) from every test using it.
    """
    session_dir = tmp_path / 'stage' / 'out'
    STUB.StubSession('train', 1, session_dir, release=STUB.STUB_RELEASE,
                     domain=declaration['domain'],
                     environment_version=declaration['environment_version'])
    assert not session_dir.exists()


def test_a_missing_output_directory_is_a_named_refusal(declaration, tmp_path):
    """No output directory is SESSION_OUTPUT_MISSING, never a raw FileNotFoundError."""
    session_dir = tmp_path / 'stage' / 'out'  # exactly the 2026-10-18 board layout
    session = STUB.StubSession('train', 1, session_dir, release=STUB.STUB_RELEASE,
                               domain=declaration['domain'],
                               environment_version=declaration['environment_version'])
    result = BOARD.run_session(
        session, split='train', index=1, output=session_dir,
        expected_release=STUB.STUB_RELEASE, domain=session.domain,
        environment_version=session.environment_version,
        result_path=tmp_path / 'session-result.json', origin=BOARD.STUB_ORIGIN,
        requested_rf=dict(declaration['domain']))
    assert result['passed'] is False
    assert result['stop_cause'] == 'SESSION_OUTPUT_MISSING', result
    assert result['manifest_written'] is False
    assert 'FileNotFoundError' not in (result['error'] or '')
    assert not session_dir.exists()


def _main_output(candidate, output, result_path):
    return ['--expected-release', candidate['release_id'], '--split', 'train',
            '--session', '1', '--output', str(output), '--result', str(result_path),
            '--cable-confirmed', '--release-directory', str(candidate['path'])]


def _probe_writable(directory):
    """The test's own write probe, so the observation is real, not a permission flag."""
    probe = Path(directory) / '.observing-session-write-probe'
    try:
        probe.write_text('probe', encoding='utf-8')
        probe.unlink()
        return True
    except OSError:
        return False


def test_the_board_entry_prepares_output_before_constructing_the_session(
        candidate, tmp_path, monkeypatch):
    """Integration through the REAL entry (main -> prepare -> run_session).

    The session double observes --output instead of providing it; if the entry stopped
    creating and verifying the directory, both the observation and the run would fail
    (2026-10-18: <stage>/out never existed and frame 1 died on FileNotFoundError).
    """
    observations = []

    class ObservingBoardSession(NoMkdirBoardSession):
        def __init__(self, expected, output, domain, environment_version):
            output = Path(output)
            observations.append(dict(
                stage='construct', exists=output.is_dir(),
                empty=bool(output.is_dir() and not any(output.iterdir())),
                writable=_probe_writable(output)))
            super().__init__(expected, output, domain, environment_version)

        def configure(self, split, index):
            output = Path(self.output)
            observations.append(dict(
                stage='configure', exists=output.is_dir(),
                empty=bool(output.is_dir() and not any(output.iterdir())),
                writable=_probe_writable(output)))
            return super().configure(split, index)

    monkeypatch.setattr(BOARD, 'trainval_session_class', lambda: ObservingBoardSession)
    session_dir = tmp_path / 'rf-trainval-collect-0123456789abcdef' / 'out'
    assert not session_dir.exists()
    result_path = tmp_path / 'session-result.json'
    assert BOARD.main(_main_output(candidate, session_dir, result_path)) == 0
    assert [entry['stage'] for entry in observations] == ['construct', 'configure']
    assert all(entry['exists'] and entry['empty'] and entry['writable']
               for entry in observations), observations
    result = load(result_path)
    assert result['passed'] is True and result['manifest_written'] is True
    assert result['release_id'] == candidate['release_id']
    assert SESSION.check_session(session_dir)['release_id'] == candidate['release_id']
    assert SESSION.check_session(session_dir)['frame_count'] == len(TAGS)


def test_the_board_entry_leaves_no_write_probe_behind(candidate, tmp_path, monkeypatch):
    monkeypatch.setattr(BOARD, 'trainval_session_class', lambda: NoMkdirBoardSession)
    session_dir = tmp_path / 'stage' / 'out'
    assert BOARD.main(_main_output(candidate, session_dir, tmp_path / 'result.json')) == 0
    assert not (session_dir / BOARD.OUTPUT_PROBE_NAME).exists()


def test_the_entry_refuses_a_non_empty_output_directory(candidate, tmp_path, monkeypatch):
    monkeypatch.setattr(BOARD, 'trainval_session_class', lambda: NoMkdirBoardSession)
    session_dir = tmp_path / 'stage' / 'out'
    session_dir.mkdir(parents=True)
    (session_dir / 'old-normal-01.json').write_text('{}', encoding='utf-8')
    with pytest.raises(BOARD.BoardSessionError) as info:
        BOARD.main(_main_output(candidate, session_dir, tmp_path / 'result.json'))
    assert info.value.code == 'SESSION_OUTPUT_NOT_EMPTY', info.value.detail
    assert (session_dir / 'old-normal-01.json').is_file()


def test_the_entry_refuses_an_output_path_that_is_a_regular_file(candidate, tmp_path,
                                                                 monkeypatch):
    """The 2026-10-18 notes flagged this as reachable only through a plain file."""
    blocker = tmp_path / 'out'
    blocker.write_text('a regular file, not a stage directory', encoding='utf-8')
    monkeypatch.setattr(BOARD, 'trainval_session_class', lambda: NoMkdirBoardSession)
    with pytest.raises(BOARD.BoardSessionError) as info:
        BOARD.main(_main_output(candidate, blocker, tmp_path / 'result.json'))
    assert info.value.code == 'SESSION_OUTPUT_NOT_A_DIRECTORY', info.value.detail
    assert blocker.read_text(encoding='utf-8') == 'a regular file, not a stage directory'


def test_an_output_directory_that_cannot_be_written_is_named(candidate, tmp_path,
                                                             monkeypatch):
    """A read-only stage must be refused by name before any session exists."""
    real_open = Path.open

    def refuse_the_probe(self, *args, **kwargs):
        if self.name == BOARD.OUTPUT_PROBE_NAME:
            raise OSError(13, 'Permission denied (injected at the OS boundary)')
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', refuse_the_probe)
    monkeypatch.setattr(BOARD, 'trainval_session_class', lambda: NoMkdirBoardSession)
    session_dir = tmp_path / 'stage' / 'out'
    with pytest.raises(BOARD.BoardSessionError) as info:
        BOARD.main(_main_output(candidate, session_dir, tmp_path / 'result.json'))
    assert info.value.code == 'SESSION_OUTPUT_NOT_WRITABLE', info.value.detail
    assert session_dir.is_dir() and not any(session_dir.iterdir())


def test_an_output_directory_that_cannot_be_created_is_named(candidate, tmp_path,
                                                             monkeypatch):
    blocker = tmp_path / 'not-a-directory'
    blocker.write_text('a regular file, not a stage directory', encoding='utf-8')
    monkeypatch.setattr(BOARD, 'trainval_session_class', lambda: NoMkdirBoardSession)
    with pytest.raises(BOARD.BoardSessionError) as info:
        BOARD.main(_main_output(candidate, blocker / 'out', tmp_path / 'result.json'))
    assert info.value.code == 'SESSION_OUTPUT_UNCREATABLE', info.value.detail
