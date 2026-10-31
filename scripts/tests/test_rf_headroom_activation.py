"""The activation path itself: durable journal, separated read-back, and the
storage-fault abort.

The 2026-10-11 activation had `installation: NOT_RUN` for its whole lifetime, so
four defects survived in code nobody had ever executed.  The tests below run the
real `install()` against a fake board: every phase has to be on disk the moment
it happens, a dead install session has to end in a read-only read-back on a new
connection, and a board whose kernel is already reporting a storage fault must
not be written to at all.
"""
import builtins
import io
import json
import re
import shlex
import stat
from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import rf_headroom_deploy as D

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / 'evidence/rf-teaching/s2/headroom-contract-2026-10-11'
CANDIDATE = STAGE / 'candidate-budget/releases/b67a8aaab0c5904822580c0647b96fd0'
ARM = STAGE / 'arm-runtime-budget/receipt.json'
PARENT_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2/g1-qualification-2026-10-08'
                    / 'candidate/releases/adfac1f14c3cdb379cb92569a21a9054')
PARENT_INSTALL = ROOT / 'evidence/rf-teaching/s2/g2-execution-2026-10-10/install/install-receipt.json'
CHECKPOINT = STAGE / 'stage-checkpoint.json'
NEW = 'b67a8aaab0c5904822580c0647b96fd0'
OLD = D.ROLLBACK_PARENT


def verified_state_dict(release):
    """A board state that satisfies the reviewed `verified_state` predicate."""
    return dict(
        release_id=release, release_verified=True, safe=True,
        active_release='/home/xilinx/overlays/awg_merged/releases/' + release,
        status=dict(release_id=release, online=True, stale=False, armed=False,
                    pl_status=dict(io_ok=True, rx=dict(armed=False),
                                   tx=dict(source_selected=False))),
        services={name: dict(ActiveState='active', SubState='running', MainPID='4242',
                             NRestarts='0') for name in ('pynq-status', 'rf-daemon')})


class _FakeRemotePath:
    """pathlib.Path restricted to the fake board's remote files."""

    def __init__(self, path):
        self._path = str(path).rstrip('/')

    def __truediv__(self, other):
        return _FakeRemotePath(self._path + '/' + str(other))

    def __fspath__(self):
        return self._path

    def read_bytes(self):
        data = _BOARD_FOR_GUARD.remote_files.get(self._path)
        if data is None:
            raise IOError('no such remote file: ' + self._path)
        return data

    def read_text(self):
        return self.read_bytes().decode('utf-8')

    def stat(self):
        import os
        return os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, 0, 0,
                               len(self.read_bytes()), 0, 0, 0))


_BOARD_FOR_GUARD = None


def board_guard_result(board, command, log):
    """Run a python3 -c digest guard the way the REAL board would.

    The guards the installer sends are real Python programs.  The fake board used
    to answer 0 to every command containing 'hashlib', so a guard that was not
    even a program still looked successful -- which is exactly how the frozen
    read-back guard (a SyntaxError, finding N2 on 2026-10-13) survived the whole
    suite while exiting 2 on the real board.  Here the payload is parsed and then
    EXECUTED against the fake remote files: a non-program returns python3's own
    exit code 2, a failed assertion returns 1, and a correct guard returns 0.
    """
    global _BOARD_FOR_GUARD
    parts = shlex.split(command)
    if '-c' not in parts:
        return None
    payload = parts[parts.index('-c') + 1]
    try:
        code = compile(payload, '<board-guard>', 'exec')
    except SyntaxError as exc:
        Path(log).write_text('SyntaxError: %s\n' % exc, encoding='utf-8')
        return 2

    def fake_open(path, mode='r', *args, **kwargs):
        data = board.remote_files.get(str(path))
        if data is None:
            raise IOError('no such remote file: ' + str(path))
        return io.BytesIO(data) if 'b' in mode else io.StringIO(data.decode('utf-8'))

    previous, _BOARD_FOR_GUARD = _BOARD_FOR_GUARD, board
    # An "import pathlib" inside the guard must resolve to the fake remote path,
    # not to the host filesystem, so the module is swapped for the duration.
    fake_pathlib = types.ModuleType('pathlib')
    fake_pathlib.Path = _FakeRemotePath
    saved_pathlib = sys.modules.get('pathlib')
    sys.modules['pathlib'] = fake_pathlib
    try:
        import hashlib as real_hashlib
        namespace = {'__builtins__': dict(vars(builtins), open=fake_open),
                     'hashlib': real_hashlib, 'pathlib': fake_pathlib}
        exec(code, namespace)
    except AssertionError:
        Path(log).write_text('AssertionError\n', encoding='utf-8')
        return 1
    except BaseException as exc:                   # python3 exits 1 on any exception
        Path(log).write_text('%s: %s\n' % (type(exc).__name__, exc), encoding='utf-8')
        return 1
    finally:
        if saved_pathlib is None:
            sys.modules.pop('pathlib', None)
        else:
            sys.modules['pathlib'] = saved_pathlib
        _BOARD_FOR_GUARD = previous
    Path(log).write_text('', encoding='utf-8')
    return 0


class FakeSftp:
    def __init__(self, board):
        self.board = board

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def mkdir(self, path):
        self.board.dirs.append(path)

    def put(self, local, remote):
        self.board.uploads.append(remote)
        self.board.remote_files[remote] = Path(local).read_bytes()

    def get(self, remote, local):
        if remote not in self.board.remote_files:
            raise IOError('no such remote file: ' + remote)
        Path(local).write_bytes(self.board.remote_files[remote])

    def lstat(self, path):
        size = len(self.board.remote_files.get(path, b'')) or 4096
        return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_size=size)


class FakeBoard:
    """One board behind one connection: snapshots, uploads and downloads.

    `installed` is the release the board actually has; the state checker reports
    exactly that, whatever release the caller asked for -- so a read-back can
    never be answered with the release the tool hoped to find.
    """

    def __init__(self, installed, faults=(), timeout_install=False, post_state=None,
                 readback_fails=False):
        self.installed = OLD
        self.after_install = installed
        self.faults = list(faults)
        self.timeout_install = timeout_install
        self.post_state = post_state
        self.readback_fails = readback_fails
        self.remote_files = {}
        self.uploads = []
        self.dirs = []
        self.commands = []
        self.dispatch_count = 0
        self.snapshots = 0

    def snapshot(self):
        from scripts.tests.test_rf_headroom_preflight import valid_snapshot
        self.snapshots += 1
        data = valid_snapshot()
        data.update(current_release=self.installed,
                    active_release='/home/xilinx/overlays/awg_merged/releases/' + self.installed,
                    kernel_storage='\n'.join(self.faults))
        data['commands']['kernel_storage']['out'] = data['kernel_storage']
        return json.dumps(data).encode()


class FakeSession:
    """A connection: executions are driven by the test's `_run` replacement."""

    def __init__(self, board):
        self.board = board
        self.dispatch_failed = False

    def open_sftp(self):
        assert not self.dispatch_failed, 'failed execution session was reused for SFTP'
        return FakeSftp(self.board)

    def close(self):
        self.board.closed = True


def install_board(monkeypatch, board, run_hook=None):
    """Wire D.load_helper/_run to the fake board and return the run log."""
    sessions = []

    def connect():
        sessions.append(FakeSession(board))
        return sessions[-1]

    monkeypatch.setattr(D, 'load_helper', lambda: types.SimpleNamespace(connect=connect))

    def fake_run(client, helper, command, log, timeout=240):
        board.commands.append(command)
        if 'rf_state_check.py' in command and 'hashlib' not in command:
            remote, expected = None, None
            parts = command.split()
            for index, token in enumerate(parts):
                if token.endswith('/rf_state_check.py'):
                    remote = token.rsplit('/', 1)[0]
                    expected = parts[index + 1]
            if board.readback_fails:
                raise OSError('connection refused during read-back')
            board.remote_files[remote + '/state.json'] = json.dumps(
                verified_state_dict(board.installed)).encode()
            Path(log).write_text('{"release_id": "%s"}' % board.installed, encoding='utf-8')
            return 0 if board.installed == expected else 1
        if 'DEPLOY_SNAPSHOT' in command:
            Path(log).write_text('DEPLOY_SNAPSHOT ' + board.snapshot().decode() + '\n',
                                 encoding='utf-8')
            return 0
        if 'hashlib' in command:
            rc = board_guard_result(board, command, log)
            if rc is None:
                Path(log).write_text('', encoding='utf-8')
                rc = 0
            return rc
        if 'job.py' in command and ' observe ' in command:
            Path(log).write_text('HEADROOM_JOB ' + json.dumps(board.job) + '\n', encoding='utf8')
            return 0
        if 'job.py' in command and ' dispatch ' in command:
            board.dispatch_count += 1
            board.installed = board.after_install
            attempt = command.split('--attempt ')[1].split()[0]
            board.job = dict(schema='headroom-durable-job-v1', attempt_id=attempt,
                             release_id=NEW, parent_release=OLD,
                             boot_id='236dbcc4-a6db-a1ca-a1d5-d5bc61955d45',
                             observed_boot_id='236dbcc4-a6db-a1ca-a1d5-d5bc61955d45',
                             phase='FINISHED', execution_result='RETURNED',
                             exit_code=0 if board.installed == NEW else 1,
                             payload_sha256={name: __import__('hashlib').sha256(
                                 board.remote_files[board.dirs[0] + '/' + name]).hexdigest()
                                             for name in ('candidate.tgz', 'install.sh', 'install_state.py', 'job.py')},
                             retry_allowed=False)
            if board.timeout_install:
                Path(log).write_text('Installed; services remain stopped.\n', encoding='utf-8')
                client.dispatch_failed = True
                raise TimeoutError('remote install deadline; state unknown')
            Path(log).write_text('HEADROOM_JOB ' + json.dumps(board.job), encoding='utf8')
            if board.post_state is not None:
                board.remote_files[board.dirs[0] + '/post-state.json'] = json.dumps(
                    board.post_state).encode()
            return 0
        if run_hook is not None:
            return run_hook(command, log)
        raise AssertionError('unexpected remote command: ' + command[:120])

    monkeypatch.setattr(D, '_run', fake_run)
    return board


@pytest.fixture(scope='session')
def bundle(tmp_path_factory):
    """A real prepared bundle from the frozen candidate, built once per session."""
    output = tmp_path_factory.mktemp('bundle') / 'prepared'
    D.prepare(CANDIDATE, ARM, PARENT_CANDIDATE, PARENT_INSTALL, CHECKPOINT, output)
    return output


def run_install(monkeypatch, tmp_path, bundle, board):
    install_board(monkeypatch, board)
    output = tmp_path / 'attempt'
    return D.install(bundle, output), output


def phases(journal):
    return [entry['phase'] for entry in D.read_journal(journal)]


# --------------------------------------------------------------------------- journal
def test_journal_is_append_only_and_keeps_a_parseable_prefix(tmp_path):
    journal = D.AttemptJournal(tmp_path / 'attempt.jsonl')
    journal.record('attempt_started', attempt_id='a')
    journal.record('install_dispatched', timeout_seconds=240)
    with journal.path.open('a', encoding='utf-8') as handle:
        handle.write('{"phase": "install_retur')       # killed mid-write
    entries = D.read_journal(journal.path)
    assert [entry['phase'] for entry in entries] == ['attempt_started', 'install_dispatched']
    assert [entry['seq'] for entry in entries] == [1, 2]
    journal.record('attempt_finished', outcome='UNKNOWN')
    assert [entry['phase'] for entry in D.read_journal(journal.path)] == [
        'attempt_started', 'install_dispatched', 'attempt_finished']


def test_journal_rejects_non_json_values(tmp_path):
    journal = D.AttemptJournal(tmp_path / 'attempt.jsonl')
    with pytest.raises(ValueError):
        journal.record('bad', value=float('nan'))


# --------------------------------------------------------------------- happy path
def test_verified_install_always_observed_on_fresh_session(monkeypatch, tmp_path, bundle):
    board = FakeBoard(NEW, post_state=verified_state_dict(NEW))
    result, output = run_install(monkeypatch, tmp_path, bundle, board)
    assert result['outcome'] == D.OUTCOME_INSTALLED
    assert result['installed'] is True and result['board_state'] == 'VERIFIED_SAFE_NEW_RELEASE'
    assert board.dispatch_count == 1
    assert 'observation_connected' in result['phases']
    assert result['retry_allowed'] is False
    assert result['kernel_storage_faults_before'] == []
    assert (output / D.JOURNAL_NAME).exists()
    entries = D.read_journal(output / D.JOURNAL_NAME)
    assert entries[0]['phase'] == 'attempt_started'
    assert entries[0]['attempt_id'] == result['attempt_id']
    assert entries[0]['retry_allowed'] is False
    assert (output / 'install-receipt.json').exists()


def test_the_read_back_guard_is_a_runnable_program_and_discriminates(monkeypatch,
                                                                    tmp_path, bundle):
    """N2 (2026-10-13): the independent read-back guard must parse AND discriminate.

    The frozen installer sent a guard that was not even a Python program
    (hashlib.sha256(open(...).read().hexdigest()==... -- a missing parenthesis),
    so on the real board it exited 2 and readback_state() raised before it could
    ever conclude VERIFIED_SAFE_NEW_RELEASE.  The fake board used to answer 0 to
    every command containing hashlib, which is why the suite could not see it.
    """
    board = FakeBoard(NEW, post_state=verified_state_dict(NEW))
    result, _ = run_install(monkeypatch, tmp_path, bundle, board)
    assert result['installed'] is True
    guards = [command for command in board.commands
              if 'rf_state_check.py' in command and 'hashlib' in command]
    assert guards, 'the read-back guard was never sent to the board'
    for command in guards:
        parts = shlex.split(command)
        compile(parts[parts.index('-c') + 1], '<read-back-guard>', 'exec')
    # A guard that cannot fail is not a guard: tamper with the uploaded checker
    # and require the same emulated board to refuse it.
    guard = guards[-1]
    payload = shlex.split(guard)[shlex.split(guard).index('-c') + 1]
    remote = re.search(r"open\('([^']+)'", payload).group(1)
    assert remote.endswith('/rf_state_check.py')
    assert remote in board.remote_files
    board.remote_files[remote] = b'print("tampered checker")'
    assert board_guard_result(board, guard, tmp_path / 'tampered-guard.log') != 0
    # And the emulator must reproduce the real board's verdict on the exact broken
    # guard that was shipped: a non-program is exit code 2, not a pass.
    broken = ('python3 -B -c ' + shlex.quote(
        "import hashlib;assert hashlib.sha256(open('%s','rb').read()"
        ".hexdigest()=='%s'" % (remote, 'a' * 64)))
    assert board_guard_result(board, broken, tmp_path / 'broken-guard.log') == 2


# ------------------------------------------------------------------- timeout path
def test_install_timeout_is_read_back_on_a_new_connection(monkeypatch, tmp_path, bundle):
    """The exact 2026-10-11 situation: deadline first, board state unknown.

    The receipt must name the timeout, must not retry, and must resolve the board
    state from a *separate* read-only session -- which is what the operator then
    had to do by hand.
    """
    board = FakeBoard(NEW, timeout_install=True)
    result, output = run_install(monkeypatch, tmp_path, bundle, board)
    assert board.dispatch_count == 1, 'a timeout must never dispatch a second install'
    assert result['install_timeout'] is True
    assert result['outcome'] == D.OUTCOME_INSTALLED
    assert result['installed'] is True
    assert result['board_state'] == 'VERIFIED_SAFE_NEW_RELEASE'
    assert result['retry_allowed'] is False
    assert result['readback']['new_release']['verified'] is True
    assert 'install_timeout' in result['phases']
    assert 'observation_connected' in result['phases']
    assert result['phases'].index('execution_session_closed') < result['phases'].index(
        'observation_connected')
    assert (output / 'readback-state-new.json').exists()
    assert (output / 'snapshot-post.log').exists()
    assert 'reinstall' not in result['next_action']
    assert result['next_action'].startswith('none from this attempt')


def test_timeout_that_left_the_parent_installed_is_a_verified_rollback(monkeypatch, tmp_path, bundle):
    board = FakeBoard(OLD, timeout_install=True)
    result, _ = run_install(monkeypatch, tmp_path, bundle, board)
    assert result['installed'] is False
    assert result['board_state'] == 'VERIFIED_SAFE_ROLLBACK'
    assert result['outcome'] == D.OUTCOME_ROLLBACK
    assert result['readback']['new_release']['verified'] is False
    assert result['readback']['parent_release']['verified'] is True


def test_timeout_with_unreadable_board_is_incomplete_not_installed(monkeypatch, tmp_path, bundle):
    board = FakeBoard(NEW, timeout_install=True, readback_fails=True)
    result, _ = run_install(monkeypatch, tmp_path, bundle, board)
    assert result['outcome'] == D.OUTCOME_INCOMPLETE
    assert result['installed'] is False and result['board_state'] == 'UNKNOWN'
    assert result['retry_allowed'] is False
    assert 'readback_failed' in result['phases']
    assert 'read-back' in result['next_action']


def test_readback_of_an_unexpected_release_never_claims_success(monkeypatch, tmp_path, bundle):
    board = FakeBoard('d' * 32, timeout_install=True)
    result, _ = run_install(monkeypatch, tmp_path, bundle, board)
    assert result['outcome'] == D.OUTCOME_UNEXPECTED
    assert result['board_state'] == 'UNKNOWN'
    assert result['installed'] is False


# ------------------------------------------------------------- storage-fault abort
def test_storage_fault_before_dispatch_aborts_and_writes_nothing(monkeypatch, tmp_path, bundle):
    """A controller that is already faulting must not receive an installation."""
    board = FakeBoard(NEW,
                      faults=['[  44.5] mmc0: Timeout waiting for hardware interrupt.',
                              '[  44.5] mmc0: sdhci: ADMA Err:  0x00000001'])
    result, output = run_install(monkeypatch, tmp_path, bundle, board)
    assert result['outcome'] == D.OUTCOME_ABORTED
    assert result['dispatched'] is False
    assert result['board_state'] == 'UNCHANGED_NOT_DISPATCHED'
    assert result['installed'] is False
    assert board.dispatch_count == 0
    assert board.uploads == [], 'nothing may be uploaded while storage is faulting'
    assert board.dirs == []
    assert len(result['kernel_storage_faults_before']) == 2
    assert 'aborted_before_dispatch' in result['phases']
    assert 'staged' not in result['phases']
    assert result['retry_allowed'] is False
    assert (output / 'install-receipt.json').exists()


def test_unavailable_preflight_snapshot_also_aborts(monkeypatch, tmp_path, bundle):
    """An unknown pre-flight is not a healthy pre-flight: fail closed."""
    board = FakeBoard(NEW)
    install_board(monkeypatch, board)
    original = D.board_snapshot

    def unavailable(client, helper, output, journal, phase):
        journal.record('snapshot_%s_failed' % phase, error_type='OSError', error='no dmesg')
        return {}, []

    monkeypatch.setattr(D, 'board_snapshot', unavailable)
    result = D.install(bundle, tmp_path / 'attempt')
    assert result['outcome'] == D.OUTCOME_ABORTED_PREFLIGHT
    assert result['dispatched'] is False and board.dispatch_count == 0
    assert board.uploads == [] and board.dirs == []
    assert 'resolve storage health' in result['next_action']
    monkeypatch.setattr(D, 'board_snapshot', original)


def test_benign_mmc_lines_are_not_treated_as_faults():
    assert D.storage_faults('mmc0: new high speed SDHC card at address 0001\n') == []
    assert D.storage_faults('') == []
    assert D.storage_faults('mmc0: Timeout waiting for hardware interrupt.') == [
        'mmc0: Timeout waiting for hardware interrupt.']
    assert D.storage_faults('FAT-fs (mmcblk0p1): Volume was not properly unmounted.') == []


def test_dispatched_command_is_independent_job_not_ssh_installer(monkeypatch, tmp_path, bundle):
    board = FakeBoard(NEW)
    result, _ = run_install(monkeypatch, tmp_path, bundle, board)
    assert result['installed'] is True
    dispatch = [command for command in board.commands if ' dispatch ' in command]
    assert len(dispatch) == 1 and 'job.py' in dispatch[0]
    assert all('sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr sh ' not in command
               for command in board.commands)


@pytest.mark.parametrize('variant', ['running', 'wrong-attempt', 'wrong-payload', 'outer-failed'])
def test_job_not_finished_or_bound_never_claims_install(monkeypatch, tmp_path, bundle, variant):
    board = FakeBoard(NEW)
    install_board(monkeypatch, board)
    original = D._run
    def observe(client, helper, command, log, timeout=240):
        rc = original(client, helper, command, log, timeout)
        if 'job.py' in command and ' observe ' in command:
            data = dict(board.job)
            if variant == 'running': data['phase'] = 'RUNNING'
            elif variant == 'wrong-attempt': data['attempt_id'] = 'f' * 32
            elif variant == 'wrong-payload': data['payload_sha256'] = {}
            elif variant == 'outer-failed': rc = 1
            Path(log).write_text('HEADROOM_JOB ' + json.dumps(data), encoding='utf8')
        return rc
    monkeypatch.setattr(D, '_run', observe)
    result = D.install(bundle, tmp_path / 'attempt')
    assert result['installed'] is False and result['outcome'] == D.OUTCOME_INCOMPLETE
    assert board.dispatch_count == 1
    assert not any('rf_state_check.py' in command for command in board.commands)


def test_parent_mismatch_refuses_before_any_upload(monkeypatch, tmp_path, bundle):
    board = FakeBoard(NEW)
    board.installed = 'f' * 32
    result, _ = run_install(monkeypatch, tmp_path, bundle, board)
    assert result['outcome'] == D.OUTCOME_ABORTED_PREFLIGHT
    assert board.uploads == [] and board.dispatch_count == 0


def test_reboot_resolves_current_activation_without_rewriting_execution(monkeypatch, tmp_path, bundle):
    board = FakeBoard(NEW, timeout_install=True)
    install_board(monkeypatch, board)
    original = D._run
    def observe(client, helper, command, log, timeout=240):
        rc = original(client, helper, command, log, timeout)
        if 'job.py' in command and ' observe ' in command:
            data = dict(board.job, phase='INTERRUPTED', execution_result='UNKNOWN', boot_id='earlier-boot')
            Path(log).write_text('HEADROOM_JOB ' + json.dumps(data), encoding='utf8')
        return rc
    monkeypatch.setattr(D, '_run', observe)
    result = D.install(bundle, tmp_path / 'attempt')
    assert result['outcome'] == D.OUTCOME_INSTALLED
    assert result['installation_execution'] == 'UNKNOWN'
    assert result['install_timeout'] is True and board.dispatch_count == 1
