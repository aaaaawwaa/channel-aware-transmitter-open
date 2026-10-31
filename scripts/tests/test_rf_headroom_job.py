"""Persistent deployment identity and Linux supervisor behavior."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / 'scripts/rf_headroom_job.py'


def module():
    assert JOB.is_file(), 'independent supervisor module is missing'
    spec = importlib.util.spec_from_file_location('headroom_job', JOB)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def payload(tmp_path):
    stage = tmp_path / 'input'
    stage.mkdir()
    for name in ('candidate.tgz', 'install.sh', 'install_state.py', 'job.py'):
        (stage / name).write_bytes(b'fake input\n')
    return stage


def test_attempt_identity_is_exclusive_and_persisted(tmp_path):
    J = module()
    path = J.prepare_attempt(payload(tmp_path), 'a' * 32, 'b' * 32, 'c' * 32,
                             tmp_path / 'jobs', boot='old-boot')
    assert json.loads((path / 'job.json').read_text('utf8'))['phase'] == 'PREPARED'
    with pytest.raises(FileExistsError):
        J.prepare_attempt(tmp_path / 'input', 'a' * 32, 'b' * 32, 'c' * 32,
                          tmp_path / 'jobs', boot='old-boot')


def test_reboot_does_not_turn_running_job_into_success(tmp_path):
    J = module()
    path = J.prepare_attempt(payload(tmp_path), 'a' * 32, 'b' * 32, 'c' * 32,
                             tmp_path / 'jobs', boot='old-boot')
    state = J.observe(path, boot='new-boot')
    assert state['phase'] == 'INTERRUPTED'
    assert state['execution_result'] == 'UNKNOWN'
    assert state['recorded_phase'] == 'PREPARED'
    assert json.loads((path / 'job.json').read_text('utf8'))['phase'] == 'PREPARED'


def test_systemd_job_has_no_ssh_stdio_or_runtime_limit(tmp_path):
    J = module()
    command = J.service_command(tmp_path / 'job', 'a' * 32)
    assert command[:2] == ['systemd-run', '--quiet']
    assert '--property=Type=exec' in command
    assert all('RuntimeMax' not in arg and 'wait' not in arg for arg in command)


def test_failed_launch_is_persisted_and_cannot_be_retried(tmp_path, monkeypatch):
    J = module()
    monkeypatch.setattr(J, 'boot_id', lambda: 'old-boot')
    stage = payload(tmp_path)
    def refuse(command, **kwargs):
        raise OSError('systemd unavailable')
    monkeypatch.setattr(J, 'make_stage_alias', lambda path, attempt: str(path / 'stage'))
    monkeypatch.setattr(J.subprocess, 'run', refuse)
    with pytest.raises(OSError):
        J.dispatch(stage, 'a' * 32, 'b' * 32, 'c' * 32, tmp_path / 'jobs')
    state = J.observe(tmp_path / 'jobs' / ('a' * 32))
    assert state['phase'] == 'LAUNCH_UNKNOWN'
    assert state['execution_result'] == 'UNKNOWN'
    with pytest.raises(FileExistsError):
        J.dispatch(stage, 'a' * 32, 'b' * 32, 'c' * 32, tmp_path / 'jobs')


@pytest.mark.skipif(sys.platform != 'linux', reason='real isolated Linux OS process test runs on ARM G1')
def test_real_detached_fake_installer_survives_observer_exit(tmp_path):
    import subprocess
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/tests/rf_headroom_job_selftest.py'),
                             '--job-script', str(JOB), '--output', str(tmp_path / 'result')],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((tmp_path / 'result/receipt.json').read_text('utf8'))
    assert receipt['passed'] is True
    assert receipt['hardware_touched'] is False
