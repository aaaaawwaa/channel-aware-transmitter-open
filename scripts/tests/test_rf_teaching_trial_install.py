"""Execute the real trial wrapper with a sandbox installer; never contact a board."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / 'scripts/rf_teaching_trial_install.sh'
NEW, OLD = 'b' * 32, 'a' * 32


@pytest.fixture
def trial(tmp_path):
    shells = [Path('<LOCAL_PATH>')]
    found = shutil.which('bash')
    if found:
        shells.append(Path(found))
    shell = next((str(p) for p in shells if p.is_file()), None)
    if shell is None:
        pytest.skip('a POSIX bash is required')
    stage = tmp_path / 'stage'
    stage.mkdir()
    (stage / 'current').write_text(OLD, encoding='ascii')
    # The actual installer owns this same EXIT trap across its complete shell.
    # This sandbox version replaces board mutations with one release-id file.
    (stage / 'install.sh').write_bytes('''set -eu
stage=$1
expected=$2
parent=$3
archive_sha=$4
changed=0
rollback() {
    rc=$1
    trap - EXIT INT TERM
    if [ "$rc" -ne 0 ] && [ "$changed" -eq 1 ]; then
        if [ ! -f "$stage/rollback-broken" ]; then
            printf '%s' "$parent" > "$stage/current"
        fi
        printf 'BASE_ROLLBACK\\n'
    fi
    exit "$rc"
}
trap 'rollback $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
changed=1
printf '%s' "$expected" > "$stage/current"
if [ -f "$stage/install-fails" ]; then exit 19; fi
printf 'INSTALL_ACCEPTED\\n'
'''.encode('utf8'))
    (stage / 'install_state.py').write_bytes('''import json,sys
from pathlib import Path
expected, output = sys.argv[1:]
stage = Path(output).parent
actual = (stage/'current').read_text()
safe = not ((stage/'unsafe-new').exists() and expected == 'b'*32)
with Path(output).open('x') as f:
    json.dump(dict(release_id=actual,safe=safe), f)
raise SystemExit(0 if actual == expected and safe else 1)
'''.encode('utf8'))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    shim = bin_dir / 'python3'
    shim.write_bytes(('#!/bin/sh\nexec ' + shlex.quote(sys.executable.replace('\\', '/')) + ' "$@"\n').encode('utf8'))
    shim.chmod(0o755)

    def run(code='raise SystemExit(0)', seconds='5', command=True):
        checker = stage / 'acceptance.py'
        checker.write_text(code, encoding='utf8')
        args = [shell, '--noprofile', '--norc', '-c',
                'PATH="$(cd "$1" && pwd):$PATH"; export PATH; shift; exec sh "$@"',
                'trial-test', bin_dir.as_posix(), WRAPPER.as_posix(), stage.as_posix(), NEW, OLD,
                'c' * 64, seconds]
        if command:
            args += ['python3', '-B', checker.as_posix()]
        return subprocess.run(args, text=True, capture_output=True, timeout=15,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))

    return stage, run


def test_acceptance_success_keeps_new_release_and_records_final_readback(trial):
    stage, run = trial
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (stage / 'current').read_text() == NEW
    assert 'TRIAL_ACCEPTED' in result.stdout
    assert 'BASE_ROLLBACK' not in result.stdout
    assert json.loads((stage / 'trial-state.json').read_text()) == dict(release_id=NEW, safe=True)


def test_acceptance_failure_restores_parent_and_verifies_it(trial):
    stage, run = trial
    result = run('raise SystemExit(7)')
    assert result.returncode == 7, result.stdout + result.stderr
    assert (stage / 'current').read_text() == OLD
    assert 'TRIAL_ROLLED_BACK' in result.stdout
    assert 'TRIAL_ACCEPTED' not in result.stdout
    assert json.loads((stage / 'trial-rollback-state.json').read_text())['release_id'] == OLD


def test_acceptance_timeout_rolls_back_without_retry(trial):
    stage, run = trial
    result = run('import time; time.sleep(60)', seconds='1')
    assert result.returncode == 124, result.stdout + result.stderr
    assert (stage / 'current').read_text() == OLD
    assert result.stdout.count('BASE_ROLLBACK') == 1
    assert 'TRIAL_ROLLED_BACK' in result.stdout


def test_zero_exit_cannot_hide_unsafe_final_state(trial):
    stage, run = trial
    (stage / 'unsafe-new').touch()
    result = run()
    assert result.returncode != 0
    assert (stage / 'current').read_text() == OLD
    assert 'TRIAL_ACCEPTED' not in result.stdout
    assert 'TRIAL_ROLLED_BACK' in result.stdout


def test_failed_rollback_is_reported_as_recovery_required(trial):
    stage, run = trial
    (stage / 'rollback-broken').touch()
    result = run('raise SystemExit(7)')
    assert result.returncode != 0
    assert (stage / 'current').read_text() == NEW
    assert 'TRIAL_RECOVERY_REQUIRED' in result.stdout
    assert 'TRIAL_ROLLED_BACK' not in result.stdout


def test_base_install_failure_does_not_run_acceptance(trial):
    stage, run = trial
    (stage / 'install-fails').touch()
    result = run('raise RuntimeError("ACCEPTANCE_MUST_NOT_RUN")')
    assert result.returncode == 19, result.stdout + result.stderr
    assert (stage / 'current').read_text() == OLD
    assert 'ACCEPTANCE_MUST_NOT_RUN' not in result.stderr
    # 2026-10-22 regression: a base-installer exit must not bypass the terminal
    # marker, and the base EXIT trap must have rolled back exactly once.
    assert 'TRIAL_ROLLED_BACK' in result.stdout, result.stdout + result.stderr
    assert 'TRIAL_ACCEPTED' not in result.stdout
    assert result.stdout.count('BASE_ROLLBACK') == 1, result.stdout


@pytest.mark.parametrize('seconds', ['0', '301', 'abc'])
def test_invalid_timeout_is_refused_before_install(trial, seconds):
    stage, run = trial
    result = run(seconds=seconds)
    assert result.returncode == 2
    assert 'trial refused:' in result.stderr
    assert (stage / 'current').read_text() == OLD
    assert 'INSTALL_ACCEPTED' not in result.stdout


def test_missing_acceptance_command_is_refused_before_install(trial):
    stage, run = trial
    result = run(command=False)
    assert result.returncode == 2
    assert 'trial refused:' in result.stderr
    assert (stage / 'current').read_text() == OLD


def test_incompatible_timeout_is_refused_before_install(trial):
    stage, run = trial
    shim = stage.parent / 'bin/timeout'
    shim.write_bytes(b'#!/bin/sh\nexit 2\n')
    shim.chmod(0o755)
    result = run()
    assert result.returncode == 2
    assert 'trial refused:' in result.stderr
    assert (stage / 'current').read_text() == OLD
