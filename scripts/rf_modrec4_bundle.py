#!/usr/bin/env python3
"""Offline candidate/trial/rollback bundle for the additive modrec4 contract.

No installation, no board access, no RF.  The candidate and its rollback parent
are verified first; the trial stage is an unpacked copy for inspection/harness
runs; the rollback package carries the current installed parent (ad37bad4...).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'scripts'))
import merged_release as R

ROLLBACK_TARGET = 'ad37bad4f6b0a6e3ba707a95be7fc196'
CANDIDATE_CONTRACTS = ('cnn-bpsk-modrec4-v1', 'cnn-bpsk-modrec4-qpsk-v1',
                       'cnn-bpsk-modrec4-qpsk-rrc-v1')
CANDIDATE_CONTRACT = CANDIDATE_CONTRACTS[0]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _members(release, contract):
    result = {}
    for item in sorted(Path(release).iterdir()):
        if item.is_symlink() or not item.is_file():
            raise ValueError('nonregular release member')
        result['release/' + item.name] = item
    for name, relative in R.software_sources(contract).items():
        result[relative.replace('\\', '/')] = Path(release) / name
    return result


def _write_tgz(output, members):
    with tarfile.open(output, 'w:gz') as archive:
        for name, path in sorted(members.items()):
            archive.add(path, arcname=name, recursive=False)
    return {'sha256': sha(output), 'bytes': output.stat().st_size,
            'members': {name: {'sha256': sha(path), 'bytes': path.stat().st_size}
                        for name, path in sorted(members.items())}}


def _acceptance_script(candidate_id, parent_id):
    return '''#!/usr/bin/env python3
"""Candidate-specific offline trial acceptance for %s (parent %s).

No RF, no install, no board writes. PASS requires the exact candidate identity,
the expected parent, unchanged six-class/baseline/RF profile, the modrec4
artifact, and the full-path modrec4 result bound to the supplied frame.
UNKNOWN (exit 2) means the required frame/ARM input was not supplied.
"""
import argparse, hashlib, json, sys
from pathlib import Path
CANDIDATE_ID = "%s"
PARENT_ID = "%s"
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import merged_release as R
import rf_modrec4_fullpath as F

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--release", type=Path, default=ROOT)
    p.add_argument("--parent", type=Path)
    p.add_argument("--frame-dir", type=Path)
    p.add_argument("--tag")
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    m = R.verify_release(a.release, require_software=True)
    checks = {"candidate_id": m["release_id"] == CANDIDATE_ID,
              "contract": m.get("software_contract") in ("cnn-bpsk-modrec4-v1", "cnn-bpsk-modrec4-qpsk-v1", "cnn-bpsk-modrec4-qpsk-rrc-v1"),
              "modrec4_artifact": bool(m.get(R.MODREC4_MODEL_KEY)),
              "worker_hook": "_ensure_modrec4" in (a.release / "rf_status_worker.py").read_text(encoding="utf-8"),
              "adapter_present": (a.release / "rf_modrec4_adapter.py").is_file()}
    if a.parent:
        pm = R.verify_release(a.parent, require_software=True)
        checks["parent_id"] = pm["release_id"] == PARENT_ID
        checks["six_class_unchanged"] = pm.get(R.MODEL_KEY) == m.get(R.MODEL_KEY)
        checks["baseline_unchanged"] = pm["software"].get("rf_environment_baseline.json") == m["software"].get("rf_environment_baseline.json")
        checks["rf_46db_unchanged"] = pm["software"].get("rf_teaching_profile.py") == m["software"].get("rf_teaching_profile.py")
    result = {"schema": "rf-modrec4-trial-acceptance-v1", "candidate_id": CANDIDATE_ID,
              "parent_id": PARENT_ID, "checks": checks}
    if a.frame_dir and a.tag:
        try:
            fr = F.run(a.frame_dir, a.tag, a.release)
            mod = fr.get("modrec4") or {}
            checks["fullpath_modrec4_ok"] = mod.get("status") == "OK"
            checks["fullpath_identity"] = bool((mod.get("frame_identity") or {}).get("capture_id"))
            checks["fullpath_stages"] = all(k in fr.get("stages", {}) for k in
                ("analysis", "six_class", "bpsk", "environment", "modrec4"))
            result["fullpath"] = fr
        except Exception as exc:
            checks["fullpath_modrec4_ok"] = False
            result["error"] = str(exc)
    else:
        result["unknown"] = "no --frame-dir/--tag supplied"
    result["passed"] = all(checks.values()) and not result.get("unknown")
    text = json.dumps(result, indent=2, default=str)
    if a.output: a.output.write_text(text + "\\n", encoding="utf-8")
    print(text)
    return 0 if result["passed"] else (2 if result.get("unknown") else 1)

if __name__ == "__main__":
    raise SystemExit(main())
''' % (candidate_id, parent_id, candidate_id, parent_id)


def _preflight_script(candidate_id, parent_id, archive_sha, trial_id):
    return '''#!/usr/bin/env python3
"""Six pre-dispatch checks; read-only. No install, no RF, no board writes."""
import argparse, hashlib, json, tarfile
from pathlib import Path
CANDIDATE = "%s"
PARENT = "%s"
ARCHIVE_SHA = "%s"
TRIAL_ID = "%s"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", type=Path, required=True)
    p.add_argument("--dest", type=Path, default=Path("/home/xilinx/overlays/awg_merged"))
    p.add_argument("--parent-manifest", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    checks = {}
    stage = a.stage.resolve()
    try:
        with tarfile.open(stage/"candidate.tgz") as t:
            m = json.load(t.extractfile("release/merged_release_manifest.json"))
        checks["candidate_identity"] = (m.get("release_id") == CANDIDATE
                                        and m.get("software_contract") in ("cnn-bpsk-modrec4-v1", "cnn-bpsk-modrec4-qpsk-v1", "cnn-bpsk-modrec4-qpsk-rrc-v1"))
    except Exception as exc:
        checks["candidate_identity"] = False
        checks["candidate_error"] = str(exc)
    checks["parent_identity"] = True
    if a.parent_manifest:
        try:
            checks["parent_identity"] = json.loads(a.parent_manifest.read_text())["release_id"] == PARENT
        except Exception:
            checks["parent_identity"] = False
    checks["new_release_absent"] = not (a.dest/"releases"/CANDIDATE).exists()
    checks["archive_digest"] = hashlib.sha256((stage/"candidate.tgz").read_bytes()).hexdigest() == ARCHIVE_SHA
    checks["unique_identity"] = (not (stage/"trial-state.json").exists()
        and not (stage/"trial-rollback-state.json").exists()
        and (stage/"TRIAL-STAGE.json").is_file()
        and json.loads((stage/"TRIAL-STAGE.json").read_text()).get("trial_id") == TRIAL_ID)
    checks["bounded_acceptance"] = ((stage/"trial-install.sh").is_file()
        and (stage/"trial"/"acceptance").is_file()
        and (stage/"trial"/"frames"/"nf-00-normal.iq").is_file()
        and json.loads((stage/"TRIAL-STAGE.json").read_text()).get("budget_s") == 120)
    result = {"schema": "rf-modrec4-dispatch-preflight-v1", "trial_id": TRIAL_ID,
              "candidate_id": CANDIDATE, "parent_id": PARENT,
              "stage": str(stage), "checks": checks, "passed": all(checks.values())}
    text = json.dumps(result, indent=2)
    if a.output: a.output.write_text(text + "\\n", encoding="utf-8")
    print(text)
    return 0 if result["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
''' % (candidate_id, parent_id, archive_sha, trial_id)


def _trial_stage(candidate, parent, cm, pm, candidate_info, rollback_command):
    candidate_id, parent_id = cm['release_id'], pm['release_id']
    trial_id = 'rf-modrec4-' + candidate_id[:8]
    stage_path = '/tmp/rf-teaching-install-' + trial_id
    unit = 'rf-modrec4-trial-' + candidate_id[:8] + '.service'
    alias = 'rf-modrec4-trial.service'
    durable = '/tmp/rf-modrec4-trial-' + candidate_id[:8]
    archive_sha = candidate_info['sha256']
    return {
        'schema': 'rf-modrec4-trial-stage-v1',
        'trial_id': trial_id,
        'candidate_id': candidate_id,
        'parent_id': parent_id,
        'contract': cm['software_contract'],
        'budget_s': 120,
        'stage_path_prefix': '/tmp/rf-teaching-install-',
        'stage_path': stage_path,
        'unit': unit,
        'alias': alias,
        'durable_dir': durable,
        'archive_sha256': archive_sha,
        'base_installer': 'install.sh (scripts/rf_teaching_install.sh)',
        'candidate_tgz': 'candidate.tgz',
        'rollback_archive': 'rollback-parent.tgz',
        'rollback_target': parent_id,
        'acceptance': 'trial/acceptance',
        'frames': 'trial/frames',
        'preflight': 'dispatch_preflight.py',
        'commands': {
            'preflight': ('python3 -B dispatch_preflight.py --stage "%s" '
                          '--parent-manifest /home/xilinx/overlays/awg_merged/releases/%s/merged_release_manifest.json'
                          % (stage_path, parent_id)),
            'dispatch': ('systemctl start %s' % unit),
            'observe_unknown': ('systemctl status --no-pager %s; '
                                'readlink -f /home/xilinx/overlays/awg_merged/current; '
                                'cat /run/pynq-status/active-release' % unit),
            'rollback_readback': rollback_command,
        },
        'terminal_states': {
            'TRIAL_ACCEPTED': 'trial-state.json + current == candidate + both units active/running',
            'TRIAL_ROLLED_BACK': 'trial-rollback-state.json + current == ad37bad4 parent',
            'TRIAL_RECOVERY_REQUIRED': 'candidate/current or unit state unverified; stop, observe, no retry',
            'no_marker': 'UNKNOWN; observe the same unit; never dispatch a second trial',
        },
        'cleanup': ['remove %s after terminal readback' % stage_path,
                    'remove %s if created' % durable,
                    'do not stop/alter current services or point current at trial'],
        'executed': False,
        'installed': False,
        'rf_emitted': False,
    }


def prepare(candidate, parent, output):
    candidate, parent, output = Path(candidate).resolve(), Path(parent).resolve(), Path(output).resolve()
    cm = R.verify_release(candidate, require_software=True)
    pm = R.verify_release(parent, require_software=True)
    if cm.get('software_contract') not in CANDIDATE_CONTRACTS:
        raise ValueError('candidate contract is not one of ' + repr(CANDIDATE_CONTRACTS))
    if cm.get('release_id') == pm.get('release_id'):
        raise ValueError('candidate id equals parent id')
    if pm.get('release_id') != ROLLBACK_TARGET:
        raise ValueError('rollback parent must be ' + ROLLBACK_TARGET)
    if not cm.get(R.MODREC4_MODEL_KEY):
        raise ValueError('candidate has no modrec4 artifact')
    output.mkdir(parents=True, exist_ok=False)
    candidate_info = _write_tgz(output / 'candidate.tgz', _members(candidate, cm['software_contract']))
    rollback_info = _write_tgz(output / 'rollback-parent.tgz', _members(parent, pm['software_contract']))
    for source, target in (('rf_teaching_install.sh', 'install.sh'),
                           ('rf_teaching_install_state.py', 'install_state.py')):
        shutil.copyfile(ROOT / 'scripts' / source, output / target)
    # Transactional stage: base installer bundle plus a bounded trial wrapper.
    # This is NOT an offline replay; it is the exact stage a board job would run.
    trial = output / 'trial'
    trial.mkdir()
    for item in candidate.iterdir():
        if item.is_file():
            shutil.copy2(item, trial / item.name)
    acceptance = trial / 'acceptance'
    acceptance.write_text(_acceptance_script(cm['release_id'], pm['release_id']), encoding='utf-8')
    frame_src = ROOT / 'evidence' / 'rf-teaching' / 's2-46db-80mhz-narrowband-2026-10-21' / 'raw'
    frames = trial / 'frames'
    frames.mkdir()
    for suffix in ('.iq', '.json', '.environment.json'):
        shutil.copyfile(frame_src / ('nf-00-normal' + suffix), frames / ('nf-00-normal' + suffix))
    shutil.copyfile(ROOT / 'scripts' / 'rf_teaching_trial_install.sh',
                    output / 'trial-install.sh')
    rollback_command = (
        'python scripts/rf_environment_readonly_check.py '
        '--candidate "<candidate-release-dir>" --phase post '
        '--output "<readback-dir>" --expect-boot-id "<boot-id>" '
        '--rollback-state "<rollback-state.json>" '
        '--rollback-candidate "<local ad37bad4 parent copy>"'
    )
    stage = _trial_stage(candidate, parent, cm, pm, candidate_info, rollback_command)
    stage_path = stage['stage_path']
    (output / 'TRIAL-STAGE.json').write_text(json.dumps(stage, indent=2), encoding='utf-8')
    (output / 'dispatch_preflight.py').write_text(
        _preflight_script(cm['release_id'], pm['release_id'],
                          candidate_info['sha256'], stage['trial_id']), encoding='utf-8')
    dispatch = ('sh "%s/trial-install.sh" "%s" "%s" "%s" "%s" 120 '
                'python3 -B "%s/trial/acceptance" --release /home/xilinx/overlays/awg_merged/current '
                '--parent /home/xilinx/overlays/awg_merged/releases/%s '
                '--frame-dir "%s/trial/frames" --tag nf-00-normal '
                '--output "%s/trial-acceptance.json"'
                % (stage_path, stage_path, cm['release_id'], pm['release_id'],
                   candidate_info['sha256'], stage_path, pm['release_id'],
                   stage_path, stage_path))
    preflight = ('python3 -B "%s/dispatch_preflight.py" --stage "%s" '
                 '--parent-manifest /home/xilinx/overlays/awg_merged/releases/%s/merged_release_manifest.json'
                 % (stage_path, stage_path, pm['release_id']))
    (output / (stage['unit'] + '.in')).write_text(
        '[Unit]\nDescription=RF modrec4 trial %s\nAfter=network.target\n\n'
        '[Service]\nType=oneshot\nExecStart=/bin/sh %s/trial-install.sh %s %s %s %s 120 '
        'python3 -B %s/trial/acceptance --release /home/xilinx/overlays/awg_merged/current '
        '--parent /home/xilinx/overlays/awg_merged/releases/%s '
        '--frame-dir %s/trial/frames --tag nf-00-normal --output %s/trial-acceptance.json\n'
        'RemainAfterExit=yes\n\n[Install]\nAlias=%s\n'
        % (stage['trial_id'], stage_path, stage_path, cm['release_id'], pm['release_id'],
           candidate_info['sha256'], stage_path, pm['release_id'],
           stage_path, stage_path, stage['alias']), encoding='utf-8')
    (output / 'TRIAL-STAGE.md').write_text(
        '# Transactional trial stage (NOT installed here)\n\n'
        'Candidate: `%s`  \nParent/rollback: `%s`  \nTrial id: `%s`  \n'
        'Unit: `%s`  \nAlias: `%s`  \nDurable: `%s`\n\n'
        'This is a real installer/trial stage (base `install.sh` + `candidate.tgz`), '
        'not an offline `trial/` replay. **Do not execute dispatch/install/rollback in this task.**\n\n'
        '## Preflight (six checks)\n\n```\n%s\n```\n\n'
        '## Dispatch (board job, after preflight PASS)\n\n```\n%s\n```\n\n'
        '## Bounded acceptance (inside wrapper, 120 s)\n\n'
        'The wrapper runs the base installer, then `trial/acceptance` on existing IQ; '
        'zero RF, zero capture; timeout expiry is failure and triggers parent restore.\n\n'
        '## Terminal state table\n\n'
        '| marker | meaning | next action |\n|---|---|---|\n'
        '| `TRIAL_ACCEPTED` | `trial-state.json`, current==candidate, both units active | retain/observe; no auto-rollback |\n'
        '| `TRIAL_ROLLED_BACK` | `trial-rollback-state.json`, current==parent | verify parent; no retry |\n'
        '| `TRIAL_RECOVERY_REQUIRED` | restore/readback unverified | stop; observe same unit; no second trial |\n'
        '| no marker | UNKNOWN | observe same unit/readlink; do not dispatch another trial |\n\n'
        '## Boundaries\n\n'
        '- Budget: 120 s acceptance; one trial invocation; no retry; 0 new RF captures/installs in A.\n'
        '- Cleanup: remove `%s` and `%s` after terminal readback; do not alter current/services.\n'
        '- Rollback gate: see `ROLLBACK.md`; success is `VERIFIED_SAFE_ROLLBACK`.\n'
        % (cm['release_id'], pm['release_id'], stage['trial_id'], stage['unit'],
           stage['alias'], stage['durable_dir'], preflight, 'systemctl start ' + stage['unit'],
           stage_path, stage['durable_dir']), encoding='utf-8')
    (output / 'ROLLBACK.md').write_text(
        '# Transactional rollback package\n\nTarget: `%s` (contract `%s`).\n\n'
        'Archive: `rollback-parent.tgz`; hashes in `bundle-receipt.json`. '
        'This task does NOT install or execute a rollback.\n\n'
        '## Complete parent gate (post-install readback; do not run here)\n\n'
        '```\n%s\n```\n\n'
        'Success: `VERIFIED_SAFE_ROLLBACK` with `--rollback-candidate` = local `%s` '
        'release copy and `--rollback-state` naming the same parent. '
        'Failure/UNKNOWN: stop; observe same unit; do not retry or install twice.\n'
        % (ROLLBACK_TARGET, pm['software_contract'], rollback_command, ROLLBACK_TARGET),
        encoding='utf-8')
    stage_hashes = {}
    for path in sorted(output.rglob('*')):
        if path.is_file():
            rel = path.relative_to(output).as_posix()
            if rel in ('trial-stage.tgz', 'stage-sha256.json', 'bundle-receipt.json'):
                continue
            stage_hashes[rel] = sha(path)
    (output / 'stage-sha256.json').write_text(json.dumps({
        'schema': 'rf-modrec4-trial-stage-sha-v1', 'trial_id': stage['trial_id'],
        'files': stage_hashes}, indent=2), encoding='utf-8')
    stage_archive = output / 'trial-stage.tgz'
    with tarfile.open(stage_archive, 'w:gz') as archive:
        for path in sorted(output.rglob('*')):
            if not path.is_file() or path == stage_archive:
                continue
            rel = path.relative_to(output).as_posix()
            if rel == 'bundle-receipt.json':
                continue
            archive.add(path, arcname=rel, recursive=False)
    stage_info = {'sha256': sha(stage_archive), 'bytes': stage_archive.stat().st_size,
                  'members': len(stage_hashes)}
    receipt = {
        'schema': 'rf-modrec4-bundle-v2',
        'candidate': str(candidate),
        'candidate_release_id': cm['release_id'],
        'candidate_contract': cm['software_contract'],
        'parent_release_id': pm['release_id'],
        'rollback_target': ROLLBACK_TARGET,
        'rollback_contract': pm['software_contract'],
        'installed': False,
        'hardware_accessed': False,
        'rf_emitted': False,
        'candidate_tgz': candidate_info,
        'rollback_parent_tgz': rollback_info,
        'modrec4_model': cm[R.MODREC4_MODEL_KEY],
        'six_class_model_unchanged': cm.get(R.MODEL_KEY) == pm.get(R.MODEL_KEY),
        'environment_baseline_unchanged': cm['software'].get('rf_environment_baseline.json')
        == pm['software'].get('rf_environment_baseline.json'),
        'rf_46db_locked': cm['software'].get('rf_teaching_profile.py')
        == pm['software'].get('rf_teaching_profile.py'),
        'trial_stage': stage_path,
        'trial_stage_dir': str(output),
        'trial_stage_metadata': str(output / 'TRIAL-STAGE.json'),
        'trial_stage_archive': stage_info,
        'trial_stage_sha_file': str(output / 'stage-sha256.json'),
        'trial_stage_sha256': sha(output / 'stage-sha256.json'),
        'trial_install_wrapper': str(output / 'trial-install.sh'),
        'dispatch_preflight': str(output / 'dispatch_preflight.py'),
        'trial_acceptance': str(acceptance),
        'trial_acceptance_command': dispatch,
        'preflight_command': preflight,
        'dispatch_command': 'systemctl start ' + stage['unit'],
        'rollback_command_template': rollback_command,
        'trial_binding': {'candidate_id': cm['release_id'],
                          'parent_id': pm['release_id'],
                          'contract': cm['software_contract'],
                          'modrec4_artifact': True},
        'identity': {'trial_id': stage['trial_id'], 'unit': stage['unit'],
                     'alias': stage['alias'], 'durable_dir': stage['durable_dir']},
        'budget_s': 120,
        'executed': False,
        'terminal_states': stage['terminal_states'],
    }
    (output / 'bundle-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--parent', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    a = parser.parse_args(argv)
    print(json.dumps({k: v for k, v in prepare(a.candidate, a.parent, a.output).items()
                      if k not in ('candidate_tgz', 'rollback_parent_tgz')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())



