"""S2 candidate activation with S1 closure, exact ARM evidence and S1 rollback.

Preparation is offline. Activation never arms RF or starts a transmission.
Transport failures are retained as UNKNOWN and are never automatically retried.

The rollback parent is the release the board has installed *now*, which is not
always the candidate's build parent.  Three parent routes are accepted: the
historical S2 environment release, proved by the post-state.json beside its
installer receipt; an installed HEADROOM release, proved by its installation
acceptance receipt together with the read-back state that receipt binds; and --
since 2026-10-17 -- a HEADROOM release the reviewed activation orchestration
switched to current, proved by its activation evidence chain when the caller
names that evidence explicitly.  Either way the gate records the parent that
install.sh will actually have to find in
/home/xilinx/overlays/awg_merged/current.
"""
import argparse
import datetime
import json
import math
from pathlib import Path
import shlex
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import merged_release as R
from scripts.rf_headroom_deploy import (TRIAL_FORMAT_2026_10_17, TRIAL_FORMAT_2026_10_18,
                                        TRIAL_FORMAT_2026_10_18_BASELINE, TRIAL_FORMATS,
                                        verify_installed_parent_via_activation_evidence,
                                        verify_installed_parent_via_recovery_acceptance,
                                        verify_installed_parent_via_trial_acceptance_evidence,
                                        verify_installed_parent_via_trial_acceptance_evidence_baseline,
                                        verify_installed_parent_via_trial_acceptance_evidence_v2)
from scripts.rf_teaching_deploy import sha, inspect_archive, load_helper, _run
from scripts.rf_teaching_install_state import verified_state

PARENT = 'c3bf915c544563324778d1c879b38e3a'
CONTRACT = 'cnn-bpsk-environment-v1'
HEADROOM_CONTRACT = 'cnn-bpsk-headroom-v1'
HEADROOM_ACCEPTANCE_SCHEMA = 'headroom-install-acceptance-v1'
TOOLS = {'rf_teaching_install.sh': 'install.sh',
         'rf_teaching_install_state.py': 'install_state.py'}


def verify_headroom_parent(parent, installation_path, acceptance_path):
    """The one accepted cross-contract rollback parent: an installed HEADROOM release.

    The runtime rollback target is the release the board installed, which after the
    2026-10-13 HEADROOM-1 activation is b82 (cnn-bpsk-headroom-v1) rather than the
    environment candidate's build parent.  That installation has no post-state.json,
    so the original proof chain is rebound instead: the acceptance receipt names the
    installer receipt by digest, the installer receipt names the read-back state by
    digest, and the read-back state must satisfy the same verified_state predicate
    the installer itself uses.  A missing document, a changed digest or a rehashed
    semantic change refuses the parent.
    """
    installation_path, acceptance_path = Path(installation_path), Path(acceptance_path)
    for path, what in ((installation_path, 'installer receipt'), (acceptance_path, 'acceptance')):
        if not path.is_file():
            raise ValueError('S2 rollback parent %s unverified' % what)
    installation = json.loads(installation_path.read_text(encoding='utf-8'))
    acceptance = json.loads(acceptance_path.read_text(encoding='utf-8'))
    summary = acceptance.get('installer_receipt') or {}
    if (acceptance.get('schema') != HEADROOM_ACCEPTANCE_SCHEMA
            or acceptance.get('accepted') is not True
            or acceptance.get('expected_release') != parent['release_id']
            or acceptance.get('verdict') != 'VERIFIED_SAFE_NEW_RELEASE'):
        raise ValueError('S2 rollback parent acceptance unverified')
    if (summary.get('sha256') != sha(installation_path)
            or summary.get('installed') is not True
            or summary.get('board_state') != 'VERIFIED_SAFE_NEW_RELEASE'
            or summary.get('outcome') != 'INSTALLED_VERIFIED'):
        raise ValueError('S2 rollback parent installer receipt unverified')
    if (installation.get('installed') is not True
            or installation.get('release_id') != parent['release_id']
            or installation.get('board_state') != 'VERIFIED_SAFE_NEW_RELEASE'
            or installation.get('outcome') != 'INSTALLED_VERIFIED'):
        raise ValueError('S2 rollback parent installation unverified')
    readback = installation_path.parent / 'readback-state-new.json'
    if (not readback.is_file()
            or sha(readback) != installation.get('evidence_sha256', {}).get(
                'readback-state-new.json')):
        raise ValueError('S2 rollback parent read-back state unverified')
    state = json.loads(readback.read_text(encoding='utf-8'))
    if state.get('software_contract') != parent.get('software_contract'):
        raise ValueError('S2 rollback parent read-back contract unverified')
    if not verified_state(state, parent['release_id']):
        raise ValueError('S2 rollback parent read-back state not verified safe')


def verify_gates(candidate, closure_path, arm_path, parent_candidate=None, parent_install=None,
                 parent_acceptance=None, parent_activation=None,
                 parent_trial_acceptance=None, parent_trial_acceptance_format=None,
                 parent_recovery_acceptance=None):
    candidate, closure_path, arm_path = map(Path, (candidate, closure_path, arm_path))
    manifest = R.verify_release(candidate, require_software=True)
    if manifest.get('software_contract') != CONTRACT:
        raise ValueError('verified S2 candidate required')
    closure = json.loads(closure_path.read_text(encoding='utf-8'))
    if (closure.get('schema') != 's1-closure-v1' or closure.get('release_id') != PARENT
            or any(closure.get(k) is not True for k in (
                'stage_passed', 'machine_passed', 'human_functional_passed', 's2_authorized'))
            or not closure.get('user_authorization_verbatim')):
        raise ValueError('S1 closure and user S2 authorization required')
    for key in ('machine_receipt', 'human_receipt'):
        if sha(ROOT / closure[key]) != closure[key + '_sha256']:
            raise ValueError('S1 evidence changed')
    receipt = json.loads(arm_path.read_text(encoding='utf-8'))
    if receipt.get('runner_sha256') != sha(ROOT / 'scripts/rf_environment_arm_check.py'):
        raise ValueError('ARM qualification runner changed')
    if (receipt.get('passed') is not True or receipt.get('release_id') != manifest['release_id']
            or receipt.get('software') != manifest['software']
            or receipt.get('native_cnn') is not True or receipt.get('parity_mismatches') != []
            or receipt.get('exit_code') != 0
            or type(receipt.get('p95_ms')) not in (float, int)
            or not math.isfinite(receipt['p95_ms']) or not 0 < receipt['p95_ms'] <= 300):
        raise ValueError('passing exact-candidate ARM qualification required')
    for name in ('arm.json', 'host.json', 'arm.log', 'host.log'):
        if sha(arm_path.parent / name) != receipt.get('evidence_sha256', {}).get(name):
            raise ValueError('ARM evidence hash mismatch')
    arm = json.loads((arm_path.parent / 'arm.json').read_text(encoding='utf-8'))
    import numpy as np
    from scripts.rf_teaching_verify import compare
    host = json.loads((arm_path.parent / 'host.json').read_text(encoding='utf-8'))
    samples = arm.get('timings_ms', [])
    if (arm.get('release_id') != manifest['release_id'] or arm.get('software') != manifest['software']
            or arm.get('native_cnn') is not True or arm.get('iterations') != 100
            or len(samples) != 100 or any(type(x) not in (float, int)
                or not math.isfinite(x) or x <= 0 for x in samples)
            or arm.get('p95_ms') != receipt['p95_ms']
            or abs(float(np.percentile(samples, 95)) - receipt['p95_ms']) > 1e-6
            or compare(host.get('records'), arm.get('records'), 'records')):
        raise ValueError('ARM underlying qualification mismatch')
    baseline = json.loads((candidate / 'rf_environment_baseline.json').read_text(encoding='utf-8'))
    baseline_sha = baseline['sha256']
    declarations = receipt.get('input_declarations') or []
    kinds = {'normal', 'narrowband', 'pulse', 'compound'}
    if len(declarations) != 4 or {x.get('tag') for x in declarations} != kinds:
        raise ValueError('ARM four input declarations required')
    expected_inputs = {x['tag']: x['iq_sha256'] for x in declarations}
    for data in (arm, host):
        statuses = data.get('statuses') or {}
        rows = data.get('records') or []
        if (data.get('release_id') != manifest['release_id'] or data.get('software') != manifest['software']
                or data.get('baseline_sha256') != baseline_sha
                or any((statuses.get(k) or {}).get('available') is not True
                    or (statuses.get(k) or {}).get('release_id') != manifest['release_id']
                    for k in ('model', 'demod', 'teaching', 'environment'))
                or (statuses.get('model') or {}).get('production') is not True
                or (statuses.get('environment') or {}).get('baseline_sha256') != baseline_sha
                or len(rows) != 4 or {x.get('tag') for x in rows} != kinds
                or {x.get('tag'): x.get('iq_sha256') for x in rows} != expected_inputs
                or any((x.get('environment') or {}).get('baseline_sha256') != baseline_sha for x in rows)):
            raise ValueError('ARM/host engine, input or baseline qualification incomplete')
    components = arm.get('component_timings_ms') or []
    if (arm.get('warmups') != 5 or arm.get('component_order') != [
            'base', 'cnn', 'bpsk', 'constellation', 'environment'] or len(components) != 100
            or any(len(row) != 5 or any(type(x) not in (int, float) or not math.isfinite(x)
                or x <= 0 for x in row) for row in components)):
        raise ValueError('ARM full-chain component timings incomplete')
    gate=dict(release_id=manifest['release_id'], parent_release=PARENT, contract=CONTRACT,
              closure_sha256=sha(closure_path), arm_receipt_sha256=sha(arm_path))
    if parent_activation is not None and parent_candidate is None:
        raise ValueError('S2 rollback parent activation evidence needs its candidate')
    if parent_trial_acceptance is not None and parent_candidate is None:
        raise ValueError('S2 rollback parent trial acceptance needs its candidate')
    if parent_recovery_acceptance is not None and parent_candidate is None:
        raise ValueError('S2 rollback parent recovery acceptance needs its candidate')
    named_proofs = [proof for proof in (parent_activation, parent_trial_acceptance,
                                        parent_recovery_acceptance)
                    if proof is not None]
    if len(named_proofs) > 1 or (named_proofs and parent_acceptance is not None):
        raise ValueError('one S2 rollback parent proof at a time')
    if (not named_proofs and (parent_candidate is None) != (parent_install is None)):
        raise ValueError('S2 rollback parent needs candidate and confirmed installation')
    if parent_acceptance is not None and parent_candidate is None:
        raise ValueError('S2 rollback parent acceptance needs its candidate and installation')
    if parent_candidate is not None:
        parent=R.verify_release(Path(parent_candidate),require_software=True)
        if parent_trial_acceptance is not None:
            # The explicit fifth route: a release the reviewed non-emitting trial
            # deployment installed and accepted, with no INSTALLED_VERIFIED receipt
            # and no renamed substitute.  The evidence layout is named, never guessed.
            if parent_trial_acceptance_format is None:
                parent_trial_acceptance_format = TRIAL_FORMAT_2026_10_17
            if parent_trial_acceptance_format not in TRIAL_FORMATS:
                raise ValueError('unknown trial acceptance evidence format: %s'
                                 % (parent_trial_acceptance_format,))
            if parent_trial_acceptance_format == TRIAL_FORMAT_2026_10_18:
                proof=verify_installed_parent_via_trial_acceptance_evidence_v2(
                    parent,parent_trial_acceptance,Path(parent_candidate))
            elif parent_trial_acceptance_format == TRIAL_FORMAT_2026_10_18_BASELINE:
                proof=verify_installed_parent_via_trial_acceptance_evidence_baseline(
                    parent,parent_trial_acceptance,Path(parent_candidate))
            else:
                proof=verify_installed_parent_via_trial_acceptance_evidence(
                    parent,parent_trial_acceptance,Path(parent_candidate))
            gate.update(parent_release=proof['release_id'],
                        parent_trial_acceptance=proof['evidence_dir'],
                        parent_trial_acceptance_sha256=proof['evidence_sha256'],
                        parent_trial_parent=proof['trial_parent'])
            return gate
        if parent_recovery_acceptance is not None:
            # The explicit sixth route: a release whose installation transaction never
            # reached a terminal state and which the post-reboot recovery found still
            # running, accepted and byte-identical to its candidate.  It carries no
            # INSTALLED_VERIFIED receipt and must not be given one by renaming anything.
            proof=verify_installed_parent_via_recovery_acceptance(
                parent,parent_recovery_acceptance,Path(parent_candidate))
            gate.update(parent_release=proof['release_id'],
                        parent_recovery_acceptance=proof['evidence_dir'],
                        parent_recovery_acceptance_sha256=proof['evidence_sha256'],
                        parent_recovery_boot_id=proof['recovery_boot_id'])
            return gate
        if parent_activation is not None:
            # The explicit fourth route: a release the reviewed activation job switched
            # to current, with no INSTALLED_VERIFIED receipt and no renamed substitute.
            proof=verify_installed_parent_via_activation_evidence(parent,parent_activation)
            gate.update(parent_release=proof['release_id'],
                        parent_activation=proof['evidence_dir'],
                        parent_activation_sha256=proof['evidence_sha256'])
            return gate
        installation_path=Path(parent_install)
        if parent.get('software_contract') == HEADROOM_CONTRACT:
            if parent_acceptance is None:
                raise ValueError('HEADROOM rollback parent needs its installation acceptance receipt')
            verify_headroom_parent(parent, installation_path, parent_acceptance)
            gate.update(parent_release=parent['release_id'],
                        parent_install_sha256=sha(installation_path),
                        parent_acceptance_sha256=sha(Path(parent_acceptance)))
            return gate
        if parent_acceptance is not None:
            raise ValueError('acceptance receipt is only for a HEADROOM rollback parent')
        installation=json.loads(installation_path.read_text(encoding='utf-8'))
        post=installation_path.parent/'post-state.json'
        if (parent.get('software_contract')!=CONTRACT or installation.get('installed') is not True
                or installation.get('release_id')!=parent['release_id']
                or installation.get('board_state')!='VERIFIED_SAFE_NEW_RELEASE'
                or sha(post)!=installation.get('evidence_sha256',{}).get('post-state.json')
                or not verified_state(json.loads(post.read_text(encoding='utf-8')),parent['release_id'])):
            raise ValueError('S2 rollback parent installation unverified')
        gate.update(parent_release=parent['release_id'],parent_install_sha256=sha(installation_path))
    return gate


def _members(candidate):
    members = {}
    for item in sorted(candidate.iterdir()):
        if item.is_symlink() or not item.is_file():
            raise ValueError('nonregular candidate member')
        members['release/' + item.name] = item
    for name, relative in R.software_sources(CONTRACT).items():
        members[relative.replace('\\', '/')] = candidate / name
    return members


def prepare(candidate, closure, arm, output, parent_candidate=None, parent_install=None,
            parent_acceptance=None, parent_activation=None, parent_trial_acceptance=None,
            parent_trial_acceptance_format=None, parent_recovery_acceptance=None):
    candidate, output = Path(candidate).resolve(), Path(output).resolve()
    gate = verify_gates(candidate, closure, arm, parent_candidate, parent_install,
                        parent_acceptance, parent_activation, parent_trial_acceptance,
                        parent_trial_acceptance_format, parent_recovery_acceptance)
    R.verify_panel_imports(candidate)
    members = _members(candidate)
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(output / 'candidate.tgz', 'w:gz') as bundle:
        for name, path in sorted(members.items()):
            bundle.add(path, arcname=name, recursive=False)
    expected = {name: dict(sha256=sha(path), bytes=path.stat().st_size)
                for name, path in members.items()}
    inspect_archive(output / 'candidate.tgz', expected)
    for source, target in TOOLS.items():
        (output / target).write_bytes((ROOT / 'scripts' / source).read_bytes())
    result = dict(gate, candidate=str(candidate), installed=False, members=expected,
                  closure_path=str(Path(closure).resolve()), arm_path=str(Path(arm).resolve()),
                  parent_candidate=str(Path(parent_candidate).resolve()) if parent_candidate else None,
                  parent_install=str(Path(parent_install).resolve()) if parent_install else None,
                  parent_acceptance=(str(Path(parent_acceptance).resolve())
                                     if parent_acceptance else None),
                  parent_activation=(str(Path(parent_activation).resolve())
                                     if parent_activation else None),
                  parent_trial_acceptance=(str(Path(parent_trial_acceptance).resolve())
                                           if parent_trial_acceptance else None),
                  parent_trial_acceptance_format=(None if parent_trial_acceptance is None
                                                  else (parent_trial_acceptance_format
                                                        or TRIAL_FORMAT_2026_10_17)),
                  parent_recovery_acceptance=(
                      str(Path(parent_recovery_acceptance).resolve())
                      if parent_recovery_acceptance else None),
                  payload_sha256={name: sha(output / name) for name in (
                      'candidate.tgz', 'install.sh', 'install_state.py')})
    (output / 'bundle-receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def verify_bundle(directory):
    directory = Path(directory).resolve()
    receipt = json.loads((directory / 'bundle-receipt.json').read_text(encoding='utf-8'))
    candidate = Path(receipt['candidate'])
    gate = verify_gates(candidate, receipt['closure_path'], receipt['arm_path'],
                        receipt.get('parent_candidate'),receipt.get('parent_install'),
                        receipt.get('parent_acceptance'),receipt.get('parent_activation'),
                        receipt.get('parent_trial_acceptance'),
                        receipt.get('parent_trial_acceptance_format'),
                        receipt.get('parent_recovery_acceptance'))
    if any(receipt.get(k) != v for k, v in gate.items()):
        raise ValueError('bundle qualification changed')
    expected = {name: dict(sha256=sha(path), bytes=path.stat().st_size)
                for name, path in _members(candidate).items()}
    if receipt['members'] != expected:
        raise ValueError('bundle/candidate changed')
    for name, digest in receipt['payload_sha256'].items():
        if name not in ('candidate.tgz', 'install.sh', 'install_state.py') or sha(directory / name) != digest:
            raise ValueError('bundle payload changed')
    if set(receipt['payload_sha256']) != {'candidate.tgz', 'install.sh', 'install_state.py'}:
        raise ValueError('missing installation payload')
    for source, target in TOOLS.items():
        if sha(ROOT / 'scripts' / source) != sha(directory / target):
            raise ValueError('installation tool changed')
    inspect_archive(directory / 'candidate.tgz', expected)
    return receipt


def install(bundle, output):
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    payload = verify_bundle(bundle)
    output.mkdir(parents=True, exist_ok=False)
    remote = '/tmp/rf-teaching-install-' + uuid.uuid4().hex
    result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  release_id=payload['release_id'], parent_release=payload['parent_release'], remote=remote,
                  closure_sha256=payload['closure_sha256'], arm_receipt_sha256=payload['arm_receipt_sha256'],
                  payload_sha256=payload['payload_sha256'], installed=False, board_state='UNKNOWN',
                  formal_physical='NOT_RUN', human_acceptance='NOT_RUN',
                  policy='no automatic retry; no arm/TX/second Overlay; S1 rollback')
    client = None
    try:
        helper = load_helper()
        client = helper.connect()
        with client.open_sftp() as s:
            s.mkdir(remote)
            for name in ('candidate.tgz', 'install.sh', 'install_state.py'):
                s.put(str(bundle / name), remote + '/' + name)
        args = [remote + '/install.sh', remote, payload['release_id'], payload['parent_release'],
                payload['payload_sha256']['candidate.tgz']]
        cmd = 'sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr sh ' + ' '.join(map(shlex.quote, args))
        rc = _run(client, helper, cmd, output / 'install.log')
        result['exit_code'] = rc
        with client.open_sftp() as s:
            for name in ('pre-state.json', 'post-state.json', 'rollback-state.json'):
                try:
                    s.get(remote + '/' + name, str(output / name))
                except OSError:
                    pass
        post = output / 'post-state.json'
        if rc == 0 and post.exists() and verified_state(
                json.loads(post.read_text(encoding='utf-8')), payload['release_id']):
            result.update(installed=True, board_state='VERIFIED_SAFE_NEW_RELEASE')
        rollback = output / 'rollback-state.json'
        if not result['installed'] and rollback.exists() and verified_state(
                json.loads(rollback.read_text(encoding='utf-8')), payload['parent_release']):
            result['board_state'] = 'VERIFIED_SAFE_ROLLBACK'
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        if client is not None:
            client.close()
        result['evidence_sha256'] = {p.name: sha(p) for p in output.iterdir() if p.is_file()}
        (output / 'install-receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    for name in ('candidate', 'closure', 'arm', 'output'):
        prep.add_argument('--' + name, required=True, type=Path)
    prep.add_argument('--parent-candidate',type=Path)
    prep.add_argument('--parent-install',type=Path)
    prep.add_argument('--parent-acceptance',type=Path)
    prep.add_argument('--parent-activation',type=Path)
    prep.add_argument('--parent-trial-acceptance',type=Path)
    # The post-reboot recovery evidence of a release whose installation transaction
    # never reached a terminal state.  Named explicitly; never detected, never a
    # fallback for a missing receipt.
    prep.add_argument('--parent-recovery-acceptance',type=Path)
    # The evidence layout has to be named: '2026-10-17' is the frozen fifth route's
    # package, '2026-10-18' is the v2 package.  Refused without the evidence switch.
    prep.add_argument('--parent-trial-acceptance-format',choices=TRIAL_FORMATS)
    live = sub.add_parser('install')
    live.add_argument('--bundle', required=True, type=Path)
    live.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    if a.action == 'prepare':
        proofs = [proof for proof in (a.parent_install, a.parent_activation,
                                      a.parent_trial_acceptance,
                                      a.parent_recovery_acceptance)
                  if proof is not None]
        if a.parent_trial_acceptance_format and a.parent_trial_acceptance is None:
            p.error('--parent-trial-acceptance-format needs --parent-trial-acceptance')
        if len(proofs) > 1:
            p.error('at most one rollback-parent proof is allowed: --parent-install, '
                    '--parent-activation, --parent-trial-acceptance or '
                    '--parent-recovery-acceptance')
    if a.action == 'prepare':
        result = prepare(a.candidate, a.closure, a.arm, a.output, a.parent_candidate,
                         a.parent_install, a.parent_acceptance, a.parent_activation,
                         a.parent_trial_acceptance, a.parent_trial_acceptance_format,
                         a.parent_recovery_acceptance)
    else:
        result = install(a.bundle, a.output)
    print(json.dumps({k: v for k, v in result.items() if k != 'members'}, indent=2))
    raise SystemExit(0 if a.action == 'prepare' or result['installed'] else 1)
