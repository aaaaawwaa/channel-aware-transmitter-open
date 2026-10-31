"""Single-dispatch root systemd installation job; SSH only submits/observes it.

The job and payload live on persistent root storage. /tmp contains only the
installer-compatible staging alias. A reboot removes that alias but cannot turn
an incomplete execution record into success. This module never loads an Overlay
or arms RF itself; its one child is the separately reviewed installer.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

JOB_ROOT = '/var/lib/rf-headroom-deploy'
PAYLOAD = ('candidate.tgz', 'install.sh', 'install_state.py', 'job.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def sync_dir(path):
    if os.name != 'nt':
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf8') as output:
        json.dump(value, output, sort_keys=True, allow_nan=False)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    os.replace(str(temporary), str(path))
    sync_dir(path.parent)


def phase(path, record, value, **fields):
    record.update(phase=value, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), **fields)
    with (path / 'phases.jsonl').open('a', encoding='utf8') as output:
        output.write(json.dumps(record, sort_keys=True, allow_nan=False) + '\n')
        output.flush()
        os.fsync(output.fileno())
    atomic_json(path / 'job.json', record)


def prepare_attempt(stage, attempt, release, parent, root=JOB_ROOT, boot=None):
    for value in (attempt, release, parent):
        if type(value) is not str or re.fullmatch('[0-9a-f]{32}', value) is None:
            raise ValueError('hex32 attempt/release/parent required')
    stage, root = Path(stage).resolve(strict=True), Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink(): raise ValueError('job root may not be a symlink')
    path = root / attempt
    path.mkdir(mode=0o700)  # Exclusive before copying or starting anything. Never resume/retry.
    sync_dir(root)
    durable = path / 'stage'
    durable.mkdir(mode=0o700)
    for name in PAYLOAD:
        source = stage / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('regular payload required: ' + name)
        shutil.copyfile(str(source), str(durable / name))
        with (durable / name).open('r+b') as handle: os.fsync(handle.fileno())
    sync_dir(durable)
    record = dict(schema='headroom-durable-job-v1', attempt_id=attempt,
                  release_id=release, parent_release=parent,
                  boot_id=boot if boot is not None else boot_id(),
                  payload_sha256={name: digest(durable / name) for name in PAYLOAD},
                  execution_result='UNKNOWN', retry_allowed=False)
    phase(path, record, 'PREPARED')
    return path


def make_stage_alias(path, attempt):
    alias = Path('/tmp/rf-teaching-install-' + attempt + '-job')
    os.symlink(str((path / 'stage').resolve()), str(alias))  # Refuse preexisting alias.
    sync_dir(alias.parent)
    return str(alias)


def service_command(path, attempt):
    return ['systemd-run', '--quiet', '--unit=rf-headroom-install-' + attempt,
            '--property=Type=exec', '--property=UMask=0077',
            '--property=StandardInput=null', '--property=StandardOutput=null',
            '--property=StandardError=journal',
            '/usr/bin/python3', '-B', str((path / 'stage/job.py').resolve()),
            'run', '--job', str(path.resolve())]


def dispatch(stage, attempt, release, parent, root=JOB_ROOT):
    path = prepare_attempt(stage, attempt, release, parent, root)
    record = json.loads((path / 'job.json').read_text('utf8'))
    try:
        record['stage_alias'] = make_stage_alias(path, attempt)
        phase(path, record, 'SUBMITTING')
        # Type=exec verifies exec, not installer completion. No --wait and no
        # RuntimeMaxSec: this system service is independent of the SSH session.
        result = subprocess.run(service_command(path, attempt), capture_output=True,
                                text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError('systemd submission: ' + result.stderr.strip())
    except Exception as exc:
        # Submission may have reached systemd even if its acknowledgment failed.
        # This separate observation file never writes supervisor-owned job.json;
        # there is no read-then-write race against RUNNING/FINISHED.
        atomic_json(path / 'launch-error.json', dict(error_type=type(exc).__name__, error=str(exc)))
        raise
    return observe(path)


def validate_attempt(path, record):
    if record['boot_id'] != boot_id():
        raise ValueError('attempt prepared on a different boot')
    stage = Path(record['stage_alias'])
    if not stage.is_symlink() or stage.resolve(strict=True) != path / 'stage':
        raise ValueError('durable stage alias changed')
    if any(digest(stage / name) != record['payload_sha256'][name] for name in PAYLOAD):
        raise ValueError('durable payload changed')
    return stage


def run_attempt(path):
    path = Path(path).resolve(strict=True)
    # O_EXCL survives crashes/reboots. Even an interrupted attempt is never rerun.
    with (path / 'run.lock').open('x', encoding='ascii') as lock:
        lock.write(str(os.getpid()))
        lock.flush()
        os.fsync(lock.fileno())
    sync_dir(path)
    record = json.loads((path / 'job.json').read_text('utf8'))
    try:
        stage = validate_attempt(path, record)
        phase(path, record, 'RUNNING', supervisor_pid=os.getpid())
        log_fd = os.open(str(path / 'install.log'), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_SYNC, 0o600)
        with os.fdopen(log_fd, 'ab', buffering=0) as output:
            child = subprocess.Popen(['sh', str(stage / 'install.sh'), str(stage),
                                      record['release_id'], record['parent_release'],
                                      record['payload_sha256']['candidate.tgz']],
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     env=dict(os.environ, PYTHONPATH='/home/xilinx', XILINX_XRT='/usr'))
            phase(path, record, 'RUNNING', installer_pid=child.pid)
            # No deadline. SSH observer expiration must not kill the installer.
            rc = child.wait()
            os.fsync(output.fileno())
        phase(path, record, 'FINISHED', exit_code=rc,
              execution_result='RETURNED', install_log_sha256=digest(path / 'install.log'))
        return rc
    except Exception as exc:
        phase(path, record, 'EXECUTION_UNKNOWN', error_type=type(exc).__name__, error=str(exc))
        raise


def observe(path, boot=None):
    path = Path(path)
    record = json.loads((path / 'job.json').read_text('utf8'))
    now = boot if boot is not None else boot_id()
    result = dict(record, observed_boot_id=now)
    if record['boot_id'] != now:
        result.update(recorded_phase=record['phase'], phase='INTERRUPTED', execution_result='UNKNOWN')
    elif record['phase'] in ('PREPARED', 'SUBMITTING') and (path / 'launch-error.json').exists():
        result.update(recorded_phase=record['phase'], phase='LAUNCH_UNKNOWN', execution_result='UNKNOWN')
    elif record['phase'] == 'RUNNING':
        try: os.kill(record['supervisor_pid'], 0)
        except (OSError, KeyError):
            result.update(recorded_phase='RUNNING', phase='EXECUTION_UNKNOWN', execution_result='UNKNOWN')
    return result


def wait_observation(path, timeout):
    end = time.monotonic() + timeout
    while True:
        record = observe(path)
        if record['phase'] not in ('PREPARED', 'SUBMITTING', 'RUNNING') or time.monotonic() >= end:
            return record
        time.sleep(min(0.2, max(0.001, end - time.monotonic())))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    submit = subs.add_parser('dispatch')
    for name in ('stage', 'attempt', 'release', 'parent'): submit.add_argument('--' + name, required=True)
    submit.add_argument('--job-root', default=JOB_ROOT)
    run = subs.add_parser('run'); run.add_argument('--job', required=True)
    read = subs.add_parser('observe'); read.add_argument('--job', required=True)
    read.add_argument('--wait-seconds', type=float, default=0)
    args = parser.parse_args()
    if args.action == 'run': raise SystemExit(run_attempt(args.job))
    result = (dispatch(args.stage, args.attempt, args.release, args.parent, args.job_root)
              if args.action == 'dispatch' else wait_observation(args.job, args.wait_seconds))
    print('HEADROOM_JOB ' + json.dumps(result, sort_keys=True, allow_nan=False))
