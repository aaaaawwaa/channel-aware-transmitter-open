"""Portable isolated Linux G1: real fake installer processes, no services/RF.

Run python3 -B this.py --job-script <reviewed rf_headroom_job.py> --output
<fresh /tmp directory>. No root/systemd is needed. Uses real independently
detached supervisor processes to test lifetime, locks and durable exit records.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def run(script, output):
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('durable_job', script)
    J = importlib.util.module_from_spec(spec); spec.loader.exec_module(J)
    records = {}
    for name, code in (('success', 0), ('installer-failure', 7)):
        attempt = uuid.uuid4().hex
        stage = output / ('input-' + name); stage.mkdir()
        (stage / 'install.sh').write_text('#!/bin/sh\nprintf "BEGIN\\n"\nsleep 2\nprintf "END\\n"\nexit %d\n' % code)
        (stage / 'candidate.tgz').write_bytes(b'FAKE NO HARDWARE\n')
        (stage / 'install_state.py').write_text('# fake never executed\n')
        (stage / 'job.py').write_bytes(script.read_bytes())
        job = J.prepare_attempt(stage, attempt, 'b' * 32, 'c' * 32, output / 'jobs')
        record = json.loads((job / 'job.json').read_text('utf8'))
        record['stage_alias'] = J.make_stage_alias(job, attempt)
        J.phase(job, record, 'SUBMITTING')
        # Emitter is a disposable stand-in for an SSH dispatch connection. Its
        # child has no inherited pipes/terminal and runs after emitter exits.
        emitter = ('import subprocess,sys; subprocess.Popen([sys.executable,"-B",'
                   'sys.argv[1],"run","--job",sys.argv[2]],start_new_session=True,'
                   'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)')
        subprocess.run([sys.executable, '-B', '-c', emitter,
                        str(job / 'stage/job.py'), str(job)], check=True, timeout=5)
        end = time.monotonic() + 5
        while J.observe(job)['phase'] != 'RUNNING' and time.monotonic() < end: time.sleep(.02)
        assert J.observe(job)['phase'] == 'RUNNING'
        duplicate = subprocess.run([sys.executable, '-B', str(script), 'run', '--job', str(job)],
                                   capture_output=True, text=True, timeout=5)
        assert duplicate.returncode != 0
        try:
            subprocess.run([sys.executable, '-B', str(script), 'observe', '--job', str(job),
                            '--wait-seconds', '20'], capture_output=True, timeout=.1)
            raise AssertionError('observer should hit its deadline')
        except subprocess.TimeoutExpired: pass
        assert J.observe(job)['phase'] == 'RUNNING', 'observer timeout stopped supervisor'
        end = time.monotonic() + 8
        while J.observe(job)['phase'] == 'RUNNING' and time.monotonic() < end: time.sleep(.02)
        final = J.observe(job)
        assert final['phase'] == 'FINISHED' and final['exit_code'] == code
        assert final['install_log_sha256'] == J.digest(job / 'install.log')
        assert b'END' in (job / 'install.log').read_bytes()
        try: J.prepare_attempt(stage, attempt, 'b' * 32, 'c' * 32, output / 'jobs')
        except FileExistsError: pass
        else: raise AssertionError('duplicate attempt accepted')
        interrupted = J.observe(job, boot='other-boot')
        assert interrupted['phase'] == 'INTERRUPTED' and interrupted['execution_result'] == 'UNKNOWN'
        assert json.loads((job / 'job.json').read_text('utf8'))['phase'] == 'FINISHED'
        records[name] = dict(passed=True, observer_timeout=True, emitter_exited=True,
                             duplicate_run_refused=True, duplicate_dispatch_refused=True,
                             exit_code=code, interrupted_boot_is_unknown=True,
                             job_sha256=J.digest(job / 'job.json'), log_sha256=J.digest(job / 'install.log'))
    receipt = dict(passed=True, simulation=True, hardware_touched=False, systemd_touched=False,
                   new_rf_frames=0, actual_install=False, records=records,
                   job_script_sha256=J.digest(script), driver_sha256=J.digest(Path(__file__)))
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(json.dumps(receipt, sort_keys=True))
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-script', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    run(a.job_script.resolve(strict=True), a.output.resolve())
