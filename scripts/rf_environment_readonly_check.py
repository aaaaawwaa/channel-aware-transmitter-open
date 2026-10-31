"""Parameterised read-only pre-install / post-install check and read-back.

WHY THIS EXISTS
---------------
Every earlier read-only probe is bound to ONE release, one parent and one boot id:

* `scripts/rf_environment_board_probe.py:14`  PARENT = 'c3bf915c...'  (the S1 parent)
* `.../s2-46db-baseline-deploy-2026-10-18/preflight/preflight_probe_pre.py:52` RELEASE = '1c0894bf...'
  plus fixed CANDIDATE / TOOLS / baseline hexes
* `.../s2-46db-board-status-2026-10-19-pm/work/board_status_probe.py`  RELEASE / REF_BOOT_ID /
  SOFTWARE_MODULES / SOFTWARE_DIGEST constants

So none of them can check a NEW candidate against the board that is running the OLD one (the
pre-install state), nor the new one after an install.  This tool takes the release as a
parameter and derives EVERY expectation from it, so there is no release id, contract,
module count, boot id or baseline digest written into the code.

WHAT IT DOES / DOES NOT DO
--------------------------
Board contact is strictly read-only: release/manifest/baseline/service/status reads, dmesg,
mounts, lock ownership, `/tmp` and release listings.  Nothing on the board is created,
written, moved, chmodded or deleted; no RF session is opened, nothing is armed, no TX source
is selected, no capture runs, no control token or nonce is consumed.  The only privileged
action is the reviewed non-blocking `flock` probe that decides who holds the deployment lock
(`scripts/rf_deployment_lock_probe.py`).

Phases
  --phase pre   the board must be running the release the caller names.  Use it with a
                local copy of the CURRENTLY INSTALLED release (the rollback parent) right
                before an install; it records the boot id that the post phase must match.
  --phase post  the board must be running the NEW candidate.  `--expect-boot-id` is
                REQUIRED: without it a reboot cannot be ruled out, and "cannot decide" must
                never be a pass.  With `--rollback-state <install rollback-state.json>` an
                install that fell back to the recorded parent is reported as
                VERIFIED_SAFE_ROLLBACK (safe, but not the expected outcome) instead of a
                bare failure.

Verdicts
  VERIFIED_SAFE_EXPECTED   every item passed and the strict audited state gate
                           (`scripts/rf_teaching_install_state.verified_state`) holds for the
                           expected release.                                                        exit 0
  VERIFIED_SAFE_ROLLBACK   the board is the parent named by --rollback-state and that state
                           also passes the strict gate.                                             exit 1
  FAILED                   a named item failed.                                                    exit 1
  UNKNOWN                  a fact could not be read (command rc, missing snapshot/state):
                           never reported as a pass.                                             exit 1
Usage errors (missing/unknown arguments, unverifiable candidate) exit 2 with a named JSON error.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import re
import shlex
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = 'rf-environment-readonly-check-v1'

PHASE_PRE = 'pre'
PHASE_POST = 'post'
PHASES = (PHASE_PRE, PHASE_POST)

VERIFIED_SAFE_EXPECTED = 'VERIFIED_SAFE_EXPECTED'
VERIFIED_SAFE_ROLLBACK = 'VERIFIED_SAFE_ROLLBACK'
FAILED = 'FAILED'
UNKNOWN = 'UNKNOWN'

#: Why a boot-id item can fail even though the id is well formed.
BOOT_ID_NOT_ASSERTED = 'BOOT_ID_NOT_ASSERTED'
BOOT_ID_CHANGED = 'BOOT_ID_CHANGED'

#: A rollback read-back is only judged against a VERIFIED parent release; an identity
#: match alone is not a gate (Codex B1).
ROLLBACK_CANDIDATE_REQUIRED = 'ROLLBACK_CANDIDATE_REQUIRED'
ROLLBACK_CANDIDATE_MISMATCH = 'ROLLBACK_CANDIDATE_MISMATCH'
ROLLBACK_STATE_UNREADABLE = 'ROLLBACK_STATE_UNREADABLE'

#: The only daemon mode a deployment read-back accepts: no RF window is open.
EXPECTED_DAEMON_MODE = 'status_only'

#: Item codes.  Each failure names one of these; there is no unnamed failure path.
CANDIDATE_UNVERIFIED = 'CANDIDATE_UNVERIFIED'
FACTS_UNAVAILABLE = 'FACTS_UNAVAILABLE'
RELEASE_IDENTITY = 'RELEASE_IDENTITY'
STRICT_STATE = 'STRICT_STATE'
SOFTWARE_MAP = 'SOFTWARE_MAP'
BASELINE_BYTES = 'BASELINE_BYTES'
SERVICES = 'SERVICES'
RF_SAFE = 'RF_SAFE'
STORAGE_FAULTS = 'STORAGE_FAULTS'
MOUNTS_RW = 'MOUNTS_RW'
INFLIGHT_DEPLOYMENT = 'INFLIGHT_DEPLOYMENT'
DEPLOYMENT_LOCK = 'DEPLOYMENT_LOCK'
TMP_RESIDUE = 'TMP_RESIDUE'
BOOT_IDENTITY = 'BOOT_IDENTITY'

# Board-side state reader.  Same shape as `rf_teaching_install_state.inspect_state` (so the
# audited `verified_state` can judge it) plus the software digest and baseline digests that
# the identity comparison needs.  It names no release: it verifies whatever `current` is.
STATE_SCRIPT = r'''
import hashlib, json, subprocess, sys
from pathlib import Path
release = Path('/home/xilinx/overlays/awg_merged/current').resolve()
sys.path.insert(0, str(release))
import merged_release as R
manifest = R.verify_release(release, require_software=True)
software = manifest['software']
canon = hashlib.sha256(json.dumps(software, sort_keys=True,
                                 separators=(',', ':')).encode('ascii')).hexdigest()
baseline = {}
try:
    raw = (release / 'rf_environment_baseline.json').read_bytes()
    import rf_environment as env
    loaded = env.EnvironmentBaseline.from_dict(json.loads(raw.decode('utf-8')))
    baseline = dict(file_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                    canonical_sha256=loaded.sha256,
                    environment_version=loaded.environment_version,
                    calibration_status=loaded.calibration_status,
                    sources=len(loaded.sources), rf_config=dict(loaded.rf_config))
except Exception as exc:
    baseline = dict(error=type(exc).__name__ + ': ' + str(exc))
units = {}
for name in ('pynq-status', 'rf-daemon'):
    p = subprocess.run(['systemctl', 'show', name,
                        '--property=ActiveState,SubState,MainPID,NRestarts'],
                       stdout=subprocess.PIPE, universal_newlines=True, timeout=10)
    units[name] = dict(line.split('=', 1) for line in p.stdout.splitlines() if '=' in line)
import rf_status_client as client_mod
c = client_mod.RfStatusClient()
try:
    status = c.poll().to_dict()
finally:
    try:
        c.close()
    except Exception:
        pass
state = dict(expected_release=manifest['release_id'], release_id=manifest['release_id'],
             release_path=str(release), release_verified=True,
             software_contract=manifest.get('software_contract'), status=status,
             services=units,
             active_release=Path('/run/pynq-status/active-release').read_text().strip(),
             software_modules=len(software), software_digest=canon,
             daemon_version=manifest.get('daemon_version'),
             protocol_version=manifest.get('protocol_version'),
             baseline=baseline,
             boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
             uptime_seconds=float(Path('/proc/uptime').read_text().split()[0]))
# Same predicate as scripts/rf_teaching_install_state.safe_state (which the workstation
# re-evaluates from `status` anyway, so this copy cannot weaken the strict gate).
pl = status.get('pl_status') or {}
state['safe'] = bool(status.get('release_id') == manifest['release_id']
                     and status.get('online') is True and status.get('stale') is False
                     and status.get('armed') is False and pl.get('io_ok') is True
                     and (pl.get('rx') or {}).get('armed') is False
                     and (pl.get('tx') or {}).get('source_selected') is False)
print('READONLY_STATE ' + json.dumps(state, default=str, sort_keys=True))
'''

# `==SECTION==` transcripts.  @LOCK_STEP@ is the reviewed lock ownership probe.
PATHS = r'''@LOCK_STEP@
echo ==UNITS==; systemctl list-units --all 'rf-trial-*' 'rf-rollback-*' 'rf-activate-*' --no-legend --no-pager 2>&1; echo UNITS_DONE
echo ==JOBS==; systemctl list-jobs --no-legend --no-pager 2>&1; echo JOBS_DONE
echo ==TMP_RESIDUE==; ls -d /tmp/rf-* 2>/dev/null; echo TMP_RESIDUE_DONE
echo ==MOUNTS==; cat /proc/mounts 2>&1; echo MOUNTS_DONE
echo ==RELEASES==; ls -1 /home/xilinx/overlays/awg_merged/releases/ 2>&1; echo RELEASES_DONE
echo READONLY_PATHS_DONE
'''


class CheckError(RuntimeError):
    """A named refusal that happens before any board write (there are none anyway)."""

    def __init__(self, code, message):
        super().__init__('%s: %s' % (code, message))
        self.code = code


# --------------------------------------------------------------------------- expectations

def candidate_expectations(candidate):
    """Everything the check is allowed to expect, derived from the named release."""
    from scripts import merged_release as R
    candidate = Path(candidate)
    try:
        manifest = R.verify_release(candidate, require_software=True)
    except Exception as exc:                                    # noqa: BLE001 - named
        raise CheckError(CANDIDATE_UNVERIFIED,
                         '%s: %s' % (type(exc).__name__, exc))
    software = manifest.get('software') or {}
    canonical = {}
    baseline_path = candidate / 'rf_environment_baseline.json'
    baseline = {}
    if baseline_path.is_file():
        raw = baseline_path.read_bytes()
        baseline = dict(file_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        try:
            board_sw = str(ROOT / 'pynq_overlay/sw')
            if board_sw not in sys.path:
                sys.path.insert(0, board_sw)
            import rf_environment as env
            loaded = env.EnvironmentBaseline.from_dict(json.loads(raw.decode('utf-8')))
            baseline.update(canonical_sha256=loaded.sha256,
                            environment_version=loaded.environment_version,
                            calibration_status=loaded.calibration_status,
                            sources=len(loaded.sources))
        except Exception as exc:                                # noqa: BLE001 - reported
            baseline.update(canonical_error='%s: %s' % (type(exc).__name__, exc))
    return {
        'candidate': str(candidate),
        'release_id': manifest['release_id'],
        'software_contract': manifest.get('software_contract'),
        'software_modules': len(software),
        'software_digest': hashlib.sha256(
            json.dumps(software, sort_keys=True, separators=(',', ':')).encode('ascii')
        ).hexdigest(),
        'daemon_version': manifest.get('daemon_version'),
        'protocol_version': manifest.get('protocol_version'),
        'baseline': baseline,
        'release_path': str(candidate),
        'canonical': canonical,
    }


# ------------------------------------------------------------------------ transcript parse

def marker_row(text, marker):
    for line in reversed((text or '').splitlines()):
        if line.startswith(marker):
            try:
                return json.loads(line[len(marker):])
            except ValueError:
                return None
    return None


def section(text, marker):
    rows, active = [], False
    for line in (text or '').splitlines():
        if line.strip() == '==' + marker + '==':
            active = True
            continue
        if active and (line.startswith('==') or line.strip() == marker + '_DONE'):
            break
        if active:
            rows.append(line)
    return rows


def mount_rw(text, target):
    """None when the mount is not in the transcript -- undecidable, never 'rw'."""
    for line in (text or '').splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == target:
            options = parts[3].split(',')
            return ('rw' in options) and ('ro' not in options)
    return None


def snapshot_facts_problem(snapshot, rc):
    """Why the board snapshot cannot be used as facts (None when it can).

    Deliberately NOT `rf_headroom_deploy.validate_snapshot`: that helper treats a stopped
    service as 'snapshot unsuccessful', which would report a real SERVICES failure as
    'facts unknown'.  Here a snapshot only has to be READABLE; whether the services are
    running is judged as its own named item.
    """
    if type(rc) is not int or rc != 0 or not isinstance(snapshot, dict):
        return 'snapshot command unsuccessful'
    current = snapshot.get('current_release')
    if (not isinstance(current, str) or re.fullmatch('[0-9a-f]{32}', current) is None
            or snapshot.get('active_release')
            != '/home/xilinx/overlays/awg_merged/releases/' + current
            or not isinstance(snapshot.get('boot_id'), str)
            or re.fullmatch('[0-9a-f-]{36}', snapshot['boot_id']) is None
            or type(snapshot.get('uptime_seconds')) not in (int, float)
            or not math.isfinite(snapshot['uptime_seconds'])
            or snapshot['uptime_seconds'] < 0):
        return 'snapshot release/boot identity incomplete'
    for key in ('kernel_storage', 'mounts'):
        command = (snapshot.get('commands') or {}).get(key)
        if (not isinstance(command, dict) or type(command.get('rc')) is not int
                or command['rc'] != 0 or command.get('err') != ''
                or not isinstance(snapshot.get(key), str)
                or command.get('out') != snapshot[key]):
            return 'snapshot subcommand failed: ' + key
    units = snapshot.get('units')
    if not isinstance(units, dict) or set(units) != {'pynq-status', 'rf-daemon'}:
        return 'snapshot services incomplete'
    for name, unit in units.items():
        if (not isinstance(unit, dict) or type(unit.get('rc')) is not int
                or unit['rc'] != 0 or unit.get('err') != ''
                or not isinstance(unit.get('out'), str)):
            return 'snapshot service read failed: ' + name
    if not [line for line in snapshot['mounts'].splitlines()
            if len(line.split()) >= 4 and line.split()[1] == '/']:
        return 'snapshot root mount missing'
    return None


def snapshot_unit_fields(snapshot, name):
    """The raw `systemctl show` fields of one service from the snapshot ({} if absent)."""
    unit = ((snapshot or {}).get('units') or {}).get(name) or {}
    return dict(line.split('=', 1) for line in (unit.get('out') or '').splitlines()
                if '=' in line)


# ----------------------------------------------------------------------------- evaluation

def _unit_ok(unit):
    if not isinstance(unit, dict):
        return False
    return (unit.get('ActiveState') == 'active' and unit.get('SubState') == 'running'
            and str(unit.get('MainPID', '')).isdigit() and int(unit['MainPID']) > 0
            and unit.get('NRestarts') == '0')


def evaluate(expected, facts, *, phase, expect_boot_id=None, rollback_state=None,
             rollback_expected=None, expected_residue=()):
    """The pure judgement: facts in, named items out.  No board access, no defaults.

    `rollback_expected` is the VERIFIED parent's expectations (from its own release
    directory).  A rollback verdict is computed by running this same complete judgement
    against that parent; only the expected new-vs-parent release id differs.  There is
    deliberately no identity-only shortcut: storage, RF, mode, lock, services, boot,
    in-flight and /tmp failures all stay failures in a rollback read-back.
    """
    from scripts.rf_teaching_install_state import verified_state
    from scripts.rf_headroom_deploy import storage_faults
    from scripts.rf_deployment_lock_probe import lock_item

    snapshot = facts.get('snapshot') if isinstance(facts.get('snapshot'), dict) else {}
    state = facts.get('state') if isinstance(facts.get('state'), dict) else {}
    paths_text = facts.get('paths') if isinstance(facts.get('paths'), str) else ''
    status = state.get('status') if isinstance(state.get('status'), dict) else {}
    pl_status = status.get('pl_status') if isinstance(status.get('pl_status'), dict) else {}
    pl_tx = pl_status.get('tx') if isinstance(pl_status.get('tx'), dict) else {}
    pl_rx = pl_status.get('rx') if isinstance(pl_status.get('rx'), dict) else {}
    release = expected['release_id']

    # Fact completeness first: a read that did not happen is UNKNOWN, never a pass.
    missing = []
    if facts.get('state_rc') != 0:
        missing.append('state command failed rc=%r' % (facts.get('state_rc'),))
    if not state:
        missing.append('state document missing')
    if facts.get('snapshot_ok') is not True:
        missing.append('snapshot invalid: %s' % (facts.get('snapshot_error') or 'unknown',))
    if facts.get('paths_rc') != 0:
        missing.append('paths command failed rc=%r' % (facts.get('paths_rc'),))

    items = {}
    items['C1_candidate'] = dict(ok=True, release_id=release,
                                 software_contract=expected['software_contract'],
                                 software_modules=expected['software_modules'],
                                 candidate=expected['candidate'])
    board_release = state.get('release_id')
    items['B1_release_identity'] = dict(
        ok=(board_release == release
            and snapshot.get('current_release') == release
            and state.get('release_verified') is True
            and (state.get('active_release') or '').endswith('/' + release)),
        expected=release, board_release=board_release,
        current=snapshot.get('current_release'), active_release=state.get('active_release'),
        release_path=state.get('release_path'))
    strict = bool(state) and verified_state(state, release)
    items['B0_strict_state'] = dict(
        ok=strict,
        note='the audited strict gate (release verified + safe + active-release + services)')
    items['B2_software_map'] = dict(
        ok=(state.get('software_contract') == expected['software_contract']
            and state.get('software_modules') == expected['software_modules']
            and state.get('software_digest') == expected['software_digest']
            and state.get('daemon_version') == expected['daemon_version']
            and state.get('protocol_version') == expected['protocol_version']),
        contract=state.get('software_contract'), modules=state.get('software_modules'),
        digest=state.get('software_digest'), daemon_version=state.get('daemon_version'),
        protocol_version=state.get('protocol_version'),
        expected_contract=expected['software_contract'],
        expected_modules=expected['software_modules'],
        expected_digest=expected['software_digest'])
    board_baseline = state.get('baseline') if isinstance(state.get('baseline'), dict) else {}
    wanted_baseline = expected.get('baseline') or {}
    baseline_ok = bool(
        wanted_baseline and not board_baseline.get('error')
        and board_baseline.get('file_sha256') == wanted_baseline.get('file_sha256')
        and board_baseline.get('canonical_sha256') == wanted_baseline.get('canonical_sha256'))
    items['B3_baseline_bytes'] = dict(
        ok=baseline_ok, board=board_baseline, expected=wanted_baseline)
    services = state.get('services') if isinstance(state.get('services'), dict) else {}
    snapshot_services_ok = all(
        (lambda fields: (fields.get('ActiveState') == 'active'
                         and fields.get('SubState') == 'running'
                         and str(fields.get('MainPID', '')).isdigit()
                         and int(fields['MainPID']) > 0
                         and fields.get('NRestarts') == '0'))(
            snapshot_unit_fields(snapshot, name))
        for name in ('pynq-status', 'rf-daemon'))
    items['B4_services'] = dict(
        ok=(set(services) == {'pynq-status', 'rf-daemon'}
            and all(_unit_ok(unit) for unit in services.values())
            and snapshot_services_ok),
        services=services,
        snapshot_services_ok=snapshot_services_ok)
    items['B5_rf_safe'] = dict(
        ok=(status.get('armed') is False and status.get('core') is False
            and pl_status.get('io_ok') is True
            and pl_tx.get('source_selected') is False
            and pl_rx.get('armed') is False
            and status.get('mode') == EXPECTED_DAEMON_MODE),
        armed=status.get('armed'), core=status.get('core'),
        pl_io_ok=pl_status.get('io_ok'), tx_source_selected=pl_tx.get('source_selected'),
        rx_armed=pl_rx.get('armed'), mode=status.get('mode'),
        expected_mode=EXPECTED_DAEMON_MODE,
        daemon_state=status.get('daemon_state'))
    faults = storage_faults(snapshot.get('kernel_storage') or '')
    items['B6_storage'] = dict(ok=not faults, faults=faults,
                               kernel_lines=len((snapshot.get('kernel_storage') or '')
                                                .splitlines()))
    root_rw = mount_rw(snapshot.get('mounts') or '', '/')
    boot_rw = mount_rw(snapshot.get('mounts') or '', '/boot')
    items['B7_mounts_rw'] = dict(ok=(root_rw is True and boot_rw is True),
                                 root_rw=root_rw, boot_rw=boot_rw)
    units_text = '\n'.join(section(paths_text, 'UNITS')).strip()
    jobs_text = '\n'.join(section(paths_text, 'JOBS')).strip()
    rf_jobs = [line.strip() for line in jobs_text.splitlines() if 'rf-' in line]
    items['B8_no_inflight'] = dict(
        ok=(facts.get('paths_rc') == 0 and not units_text and not rf_jobs),
        units=units_text, rf_jobs=rf_jobs)
    lock = lock_item(paths_text)
    items['B9_lock'] = dict(ok=(lock.get('ok') is True), state=lock.get('state'),
                            reason=lock.get('reason'))
    residue = [line.strip() for line in section(paths_text, 'TMP_RESIDUE') if line.strip()]
    excused = sorted(path for path in residue if path in set(expected_residue or ()))
    residue = [path for path in residue if path not in set(expected_residue or ())]
    items['B10_tmp_clean'] = dict(ok=not residue, residue=residue, excused=excused)
    boot_id = snapshot.get('boot_id')
    boot_formed = bool(re.fullmatch('[0-9a-f-]{36}', boot_id or ''))
    if expect_boot_id:
        boot_ok = boot_formed and boot_id == expect_boot_id
        boot_reason = None if boot_ok else BOOT_ID_CHANGED
    elif phase == PHASE_POST:
        # Refused at the CLI already; kept here so a direct call can never pass silently.
        boot_ok, boot_reason = False, BOOT_ID_NOT_ASSERTED
    else:
        boot_ok, boot_reason = boot_formed, None
    items['B11_boot_identity'] = dict(ok=boot_ok, reason=boot_reason, boot_id=boot_id,
                                      expected_boot_id=expect_boot_id,
                                      uptime_seconds=snapshot.get('uptime_seconds'))

    failures = [name for name, item in items.items() if item.get('ok') is not True]
    report = {
        'schema': SCHEMA,
        'phase': phase,
        'utc': facts.get('utc'),
        'expected': expected,
        'items': items,
        'failures': failures,
        'missing_facts': missing,
        'verdict': None,
        'board_state': None,
        'passed': False,
        'safe_terminal': None,
        'rollback': None,
        'scope': 'read-only board check; no arm, no TX, no capture, no nonce',
    }
    if missing:
        report['verdict'] = UNKNOWN
        report['board_state'] = 'UNKNOWN_FACTS_INCOMPLETE'
        report['unknown_reason'] = '; '.join(missing)
        return report
    if not failures:
        report.update(verdict=VERIFIED_SAFE_EXPECTED,
                      board_state='VERIFIED_SAFE_EXPECTED_RELEASE', passed=True,
                      safe_terminal=True)
        return report
    # A fallback to the recorded parent is a KNOWN terminal state -- but only when the
    # COMPLETE gate passes against the verified parent release, not just its identity.
    if phase == PHASE_POST and rollback_state is not None:
        parent = rollback_parent(rollback_state)
        entry = {'parent_release': parent, 'state_file': str(rollback_state)}
        if parent is None:
            entry['error'] = ROLLBACK_STATE_UNREADABLE
            report['rollback'] = entry
        elif rollback_expected is None:
            entry['error'] = ROLLBACK_CANDIDATE_REQUIRED
            report['rollback'] = entry
        elif rollback_expected['release_id'] != parent:
            entry['error'] = ROLLBACK_CANDIDATE_MISMATCH
            entry['candidate_parent_release'] = rollback_expected['release_id']
            report['rollback'] = entry
        elif board_release == parent:
            # Recompute every item for the parent: only the expected identity differs.
            parent_report = evaluate(rollback_expected, facts, phase=phase,
                                     expect_boot_id=expect_boot_id,
                                     expected_residue=expected_residue)
            entry.update(
                strict_state=parent_report['items']['B0_strict_state']['ok'],
                items=parent_report['items'],
                failures=parent_report['failures'],
                missing_facts=parent_report['missing_facts'])
            report['rollback'] = entry
            if not parent_report['missing_facts'] and not parent_report['failures']:
                report.update(verdict=VERIFIED_SAFE_ROLLBACK,
                              board_state='VERIFIED_SAFE_ROLLBACK', passed=False,
                              safe_terminal=True)
                return report
    report.update(verdict=FAILED, board_state='FAILED', passed=False, safe_terminal=False)
    return report


def rollback_parent(path):
    """The parent release id an installation rollback-state file names."""
    try:
        body = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    for key in ('parent_release', 'release_id', 'release', 'expected_release'):
        value = body.get(key)
        if isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value):
            return value
    return None


# ----------------------------------------------------------------------------- collection

def collect(host, logs, timeout=180):
    """Read-only board facts.  This is the ONLY function that touches a board."""
    from scripts.rf_headroom_deploy import SNAPSHOT
    from scripts.rf_deployment_lock_probe import lock_step
    from scripts.rf_teaching_deploy import _run, load_helper
    logs = Path(logs)
    logs.mkdir(parents=True, exist_ok=True)
    out = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    helper = load_helper()
    client = helper.connect()
    try:
        rc_snap = _run(client, helper,
                       "echo $PYNQ_PASSWORD | sudo -S -p '' env PYTHONDONTWRITEBYTECODE=1 python3 -B -c %s"
                       % (shlex.quote(helper.PASS), shlex.quote(SNAPSHOT)),
                       logs / '01-snapshot.log', timeout=timeout)
        snap_text = (logs / '01-snapshot.log').read_text(encoding='utf-8', errors='replace')
        snapshot = marker_row(snap_text, 'DEPLOY_SNAPSHOT ')
        problem = snapshot_facts_problem(snapshot, rc_snap)
        out.update(snapshot_rc=rc_snap, snapshot_text=snap_text[-4000:], snapshot=snapshot,
                   snapshot_ok=(problem is None), snapshot_error=problem)

        rc_state = _run(client, helper,
                        "echo $PYNQ_PASSWORD | sudo -S -p '' env PYTHONPATH=/home/xilinx XILINX_XRT=/usr "
                        "PYTHONDONTWRITEBYTECODE=1 python3 -B -c %s"
                        % (shlex.quote(helper.PASS), shlex.quote(STATE_SCRIPT)),
                        logs / '02-state.log', timeout=timeout)
        state_text = (logs / '02-state.log').read_text(encoding='utf-8', errors='replace')
        out.update(state_rc=rc_state, state_text=state_text[-4000:],
                   state=marker_row(state_text, 'READONLY_STATE '))

        transcript = PATHS.replace('@LOCK_STEP@', lock_step())
        rc_paths = _run(client, helper, transcript, logs / '03-paths.log', timeout=timeout)
        out.update(paths_rc=rc_paths,
                   paths=(logs / '03-paths.log').read_text(encoding='utf-8', errors='replace'))
    finally:
        try:
            client.close()
        finally:
            out['read_only_session_closed'] = True
    return out


def check(candidate, phase, output, *, expect_boot_id=None, rollback_state=None,
          rollback_candidate=None, expected_residue=(), host='192.0.2.10',
          timeout=180, collector=None):
    """One pre/post read-only check.  Returns (report, exit_code)."""
    expected = candidate_expectations(candidate)
    if phase not in PHASES:
        raise CheckError('UNKNOWN_PHASE', 'phase must be one of %r' % (PHASES,))
    if phase == PHASE_POST and not expect_boot_id:
        raise CheckError(BOOT_ID_NOT_ASSERTED,
                         'the post phase needs --expect-boot-id from the pre check; a '
                         'read-back that cannot rule out a reboot is not a pass')
    # A rollback verdict requires the VERIFIED parent's release bytes, checked against
    # the state file BEFORE any board contact (Codex B1: identity alone is not a gate).
    rollback_expected = None
    if rollback_state is not None:
        if phase != PHASE_POST:
            raise CheckError('ROLLBACK_NOT_FOR_PRE',
                             '--rollback-state is a post-install read-back argument')
        stated = rollback_parent(rollback_state)
        if stated is None:
            raise CheckError(ROLLBACK_STATE_UNREADABLE,
                             'the rollback state file does not name a 32-hex release: %s'
                             % rollback_state)
        if rollback_candidate is None:
            raise CheckError(ROLLBACK_CANDIDATE_REQUIRED,
                             'a rollback read-back must also pass --rollback-candidate '
                             '(a verified local copy of the parent release): the complete '
                             'gate is recomputed against it, an identity match is not '
                             'enough')
        rollback_expected = candidate_expectations(rollback_candidate)
        if rollback_expected['release_id'] != stated:
            raise CheckError(ROLLBACK_CANDIDATE_MISMATCH,
                             '--rollback-candidate is %s but the rollback state names %s'
                             % (rollback_expected['release_id'], stated))
    elif rollback_candidate is not None:
        raise CheckError('ROLLBACK_CANDIDATE_WITHOUT_STATE',
                         '--rollback-candidate only makes sense together with '
                         '--rollback-state')
    output = Path(output)
    if output.exists():
        raise CheckError('OUTPUT_EXISTS', str(output))
    output.mkdir(parents=True, exist_ok=False)
    try:
        facts = (collector or collect)(host, output, timeout)
    except Exception as exc:                                    # noqa: BLE001 - named
        # An unreachable/failed board is a named UNKNOWN, never a traceback and never a
        # silent pass: the report still gets written so the failure has a record.
        facts = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'collect_error': '%s: %s' % (type(exc).__name__, exc)}
    if facts.get('state') is not None and not isinstance(facts['state'], dict):
        raise CheckError(FACTS_UNAVAILABLE, 'state marker was not a JSON object')
    report = evaluate(expected, facts, phase=phase, expect_boot_id=expect_boot_id,
                      rollback_state=rollback_state,
                      rollback_expected=rollback_expected,
                      expected_residue=expected_residue)
    report['tag'] = phase
    report['host'] = host
    if facts.get('collect_error'):
        report['collect_error'] = facts['collect_error']
    report['log_files'] = sorted(p.name for p in output.iterdir() if p.is_file())
    report['output'] = str(output)
    (output / ('%s-readonly-check.json' % phase)).write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    return report, (0 if report['passed'] else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True,
                        help='release directory the board must be running (pre: a copy of '
                             'the currently installed release; post: the new candidate)')
    parser.add_argument('--phase', required=True, choices=PHASES)
    parser.add_argument('--output', required=True)
    parser.add_argument('--expect-boot-id', default=None,
                        help='boot id recorded by the pre phase; REQUIRED for --phase post')
    parser.add_argument('--rollback-state', default=None,
                        help='installation rollback-state.json; post only: a fallback to '
                             'the parent it names is judged by the COMPLETE gate against '
                             '--rollback-candidate and then reported as '
                             'VERIFIED_SAFE_ROLLBACK')
    parser.add_argument('--rollback-candidate', default=None,
                        help='verified local copy of the parent release named by '
                             '--rollback-state; REQUIRED with it (an identity match alone '
                             'is not a safe-terminal gate)')
    parser.add_argument('--expected-residue', default='',
                        help='comma-separated exact /tmp/rf-* paths this run created; any '
                             'other residue still fails the /tmp item')
    parser.add_argument('--host', default='192.0.2.10')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args(argv)
    if args.rollback_state and args.phase != PHASE_POST:
        parser.error('--rollback-state is a post-install read-back argument')
    try:
        report, code = check(
            args.candidate, args.phase, args.output,
            expect_boot_id=(args.expect_boot_id or '').strip() or None,
            rollback_state=args.rollback_state,
            rollback_candidate=args.rollback_candidate,
            expected_residue=[p.strip() for p in args.expected_residue.split(',') if p.strip()],
            host=args.host, timeout=args.timeout)
    except CheckError as exc:
        print(json.dumps({'error': str(exc), 'code': exc.code}, indent=2))
        return 2
    print(json.dumps({'phase': report['phase'], 'verdict': report['verdict'],
                      'board_state': report['board_state'], 'passed': report['passed'],
                      'failures': report['failures'],
                      'boot_id': report['items']['B11_boot_identity']['boot_id'],
                      'release_id': report['items']['B1_release_identity']['board_release']},
                     indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
