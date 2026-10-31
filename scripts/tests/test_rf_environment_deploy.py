"""Deployment-gate tests.

Two rollback-parent routes are exercised: the historical S2 environment parent
(a post-state.json next to its installer receipt) and, added 2026-10-15, the
HEADROOM parent, whose proof is an installation acceptance receipt plus the
hash-bound read-back state it names.
"""
import json
from pathlib import Path

import pytest

from scripts import rf_environment_deploy as D

ROOT = Path(__file__).resolve().parents[2]
ARM = ROOT / 'evidence/rf-teaching/s2/arm-cached-v1/receipt.json'
CLOSURE = ROOT / 'evidence/rf-teaching/s1/closure-2026-10-04/receipt.json'
CANDIDATE = ROOT / 'evidence/rf-teaching/s2/candidate-v7/releases/427dfa8ac7ad6b7b58edc6b59a873897'
ENV_PARENT_INSTALL = ROOT / 'evidence/rf-teaching/s2/deployment-2026-10-05-v1/install-receipt.json'

# 2026-10-15 round: the target 399b environment candidate with the release that is
# actually installed on the board, b82, as its runtime rollback parent.
ENV_CANDIDATE = ROOT / ('evidence/rf-teaching/s2-candidate-environment-2026-10-15/'
                        'candidate/releases/399b300e59b8d00e41d43c8123884c7f')
ENV_ARM = ROOT / 'evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json'
B82 = 'b82b141e355b4cb1833969383a409c79'
C3 = 'c3bf915c544563324778d1c879b38e3a'
B82_CANDIDATE = ROOT / ('evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                        'candidate-final/releases/' + B82)
S1_CANDIDATE = ROOT / ('evidence/rf-teaching/s1/candidate/releases/' + C3)
B82_PROOF = ROOT / 'evidence/rf-teaching/s2-parent-b82-proof-2026-10-15'
B82_INSTALL = B82_PROOF / 'install-receipt.json'
B82_READBACK = B82_PROOF / 'readback-state-new.json'
B82_ACCEPTANCE = ROOT / ('evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/'
                         'acceptance-receipt-b82b141e.json')


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _set(path, value):
    def mutate(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def headroom_proof(directory, mutate_installation=None, mutate_readback=None,
                   mutate_acceptance=None, write_readback=True, rebind_acceptance=True,
                   rebind_readback=True):
    """Rebuild the imported b82 proof for one injected defect.

    The acceptance receipt is re-bound to the mutated installer receipt (and the
    installer receipt to the mutated read-back state) unless the caller asks for a
    broken digest instead.  Every rehashed case therefore still fails only because
    the semantic requirement is enforced, not because a hash was left stale.
    """
    installation = _load(B82_INSTALL)
    readback = _load(B82_READBACK)
    acceptance = _load(B82_ACCEPTANCE)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    readback_path = directory / 'readback-state-new.json'
    if mutate_readback is not None:
        mutate_readback(readback)
    if write_readback:
        readback_path.write_text(json.dumps(readback, indent=2), encoding='utf-8')
        if rebind_readback:
            installation.setdefault('evidence_sha256', {})[
                'readback-state-new.json'] = D.sha(readback_path)
    if mutate_installation is not None:
        mutate_installation(installation)
    install_path = directory / 'install-receipt.json'
    install_path.write_text(json.dumps(installation, indent=2), encoding='utf-8')
    if mutate_acceptance is not None:
        mutate_acceptance(acceptance)
    if rebind_acceptance:
        acceptance.setdefault('installer_receipt', {})['sha256'] = D.sha(install_path)
    acceptance_path = directory / 'acceptance.json'
    acceptance_path.write_text(json.dumps(acceptance, indent=2), encoding='utf-8')
    return install_path, acceptance_path


def headroom_gate(install, acceptance):
    return D.verify_gates(ENV_CANDIDATE, CLOSURE, ENV_ARM, B82_CANDIDATE, install, acceptance)


# --------------------------------------------------------------------------- #
# The historical S2 environment parent route.
# --------------------------------------------------------------------------- #

def test_gate_accepts_authorized_closure_and_actual_arm():
    gate = D.verify_gates(CANDIDATE, CLOSURE, ARM)
    assert gate['parent_release'] == D.PARENT
    assert gate['contract'] == D.CONTRACT


def test_upgrade_parent_is_verified_installed_s2_and_rollback_bound():
    gate = D.verify_gates(CANDIDATE, CLOSURE, ARM, CANDIDATE, ENV_PARENT_INSTALL)
    assert gate['parent_release'] == _load(ENV_PARENT_INSTALL)['release_id']
    assert gate['parent_install_sha256'] == D.sha(ENV_PARENT_INSTALL)


def test_environment_parent_route_is_unchanged_for_the_2026_10_15_candidate():
    gate = D.verify_gates(ENV_CANDIDATE, CLOSURE, ENV_ARM, CANDIDATE, ENV_PARENT_INSTALL)
    assert gate['parent_release'] == _load(ENV_PARENT_INSTALL)['release_id']
    assert gate['contract'] == D.CONTRACT
    assert 'parent_acceptance_sha256' not in gate


def test_environment_parent_does_not_use_the_headroom_acceptance_receipt():
    with pytest.raises(ValueError, match='acceptance'):
        D.verify_gates(ENV_CANDIDATE, CLOSURE, ENV_ARM, CANDIDATE, ENV_PARENT_INSTALL,
                       B82_ACCEPTANCE)


def test_parent_without_confirmed_installation_is_rejected(tmp_path):
    data = _load(ENV_PARENT_INSTALL)
    data['installed'] = False
    path = tmp_path / 'parent.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        D.verify_gates(CANDIDATE, CLOSURE, ARM, CANDIDATE, path)


@pytest.mark.parametrize('field,value', [('s2_authorized', False), ('stage_passed', False),
                                         ('release_id', '0' * 32)])
def test_closure_failure_is_rejected(tmp_path, field, value):
    receipt = _load(CLOSURE)
    receipt[field] = value
    path = tmp_path / 'closure.json'
    path.write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(ValueError):
        D.verify_gates(CANDIDATE, path, ARM)


def test_arm_payload_tampering_is_rejected(tmp_path):
    for path in ARM.parent.iterdir():
        if path.is_file():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / 'arm.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='ARM evidence'):
        D.verify_gates(CANDIDATE, CLOSURE, tmp_path / ARM.name)


def test_false_summary_cannot_hide_failed_arm(tmp_path):
    data = _load(ARM)
    data['p95_ms'] = 301
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        D.verify_gates(CANDIDATE, CLOSURE, path)


@pytest.mark.parametrize('mutation', ['runner', 'empty_records', 'warmups', 'model', 'baseline', 'components'])
def test_rehashed_incomplete_evidence_is_rejected(tmp_path, mutation):
    for source in ARM.parent.iterdir():
        if source.is_file():
            (tmp_path / source.name).write_bytes(source.read_bytes())
    receipt = _load(tmp_path / ARM.name)
    arm = _load(tmp_path / 'arm.json')
    host = _load(tmp_path / 'host.json')
    if mutation == 'runner': receipt['runner_sha256'] = '0' * 64
    if mutation == 'empty_records': arm['records'] = host['records'] = []
    if mutation == 'warmups': arm['warmups'] = 0
    if mutation == 'model': arm['statuses']['model']['production'] = False
    if mutation == 'baseline': host['baseline_sha256'] = '0' * 64
    if mutation == 'components': arm['component_timings_ms'] = []
    for name, data in [('arm.json', arm), ('host.json', host)]:
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding='utf-8')
        receipt['evidence_sha256'][name] = D.sha(path)
    (tmp_path / ARM.name).write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(ValueError):
        D.verify_gates(CANDIDATE, CLOSURE, tmp_path / ARM.name)


# --------------------------------------------------------------------------- #
# The 2026-10-15 HEADROOM parent route: the real imported b82 proof.
# --------------------------------------------------------------------------- #

def test_real_b82_headroom_parent_chain_is_accepted():
    gate = headroom_gate(B82_INSTALL, B82_ACCEPTANCE)
    assert gate['parent_release'] == B82
    assert gate['release_id'] == '399b300e59b8d00e41d43c8123884c7f'
    assert gate['contract'] == D.CONTRACT
    assert gate['parent_install_sha256'] == D.sha(B82_INSTALL)
    assert gate['parent_acceptance_sha256'] == D.sha(B82_ACCEPTANCE)


def test_headroom_parent_release_id_is_never_the_build_parent_c3():
    gate = headroom_gate(B82_INSTALL, B82_ACCEPTANCE)
    assert gate['parent_release'] != D.PARENT
    assert D.PARENT == C3


def test_missing_headroom_acceptance_is_rejected():
    with pytest.raises(ValueError, match='acceptance'):
        D.verify_gates(ENV_CANDIDATE, CLOSURE, ENV_ARM, B82_CANDIDATE, B82_INSTALL)


def test_missing_installer_receipt_is_rejected():
    with pytest.raises(ValueError):
        D.verify_gates(ENV_CANDIDATE, CLOSURE, ENV_ARM, B82_CANDIDATE, None, B82_ACCEPTANCE)


def test_acceptance_without_parent_candidate_is_rejected():
    with pytest.raises(ValueError):
        D.verify_gates(ENV_CANDIDATE, CLOSURE, ENV_ARM, None, None, B82_ACCEPTANCE)


def test_missing_hash_bound_readback_is_rejected(tmp_path):
    install, acceptance = headroom_proof(tmp_path, write_readback=False)
    with pytest.raises(ValueError, match='read-back'):
        headroom_gate(install, acceptance)


def test_stale_installer_receipt_digest_is_rejected(tmp_path):
    install, acceptance = headroom_proof(
        tmp_path, mutate_installation=_set(('payload_sha256', 'candidate.tgz'), '0' * 64),
        rebind_acceptance=False)
    with pytest.raises(ValueError, match='installer receipt'):
        headroom_gate(install, acceptance)


def test_tampered_readback_bytes_are_rejected(tmp_path):
    install, acceptance = headroom_proof(
        tmp_path, mutate_readback=_set(('free_bytes',), 1), rebind_readback=False)
    with pytest.raises(ValueError, match='read-back'):
        headroom_gate(install, acceptance)


@pytest.mark.parametrize('mutation', [
    pytest.param(_set(('accepted',), False), id='not-accepted'),
    pytest.param(_set(('expected_release',), '427dfa8ac7ad6b7b58edc6b59a873897'), id='other-release'),
    pytest.param(_set(('verdict',), 'VERIFIED_SAFE_ROLLBACK'), id='other-verdict'),
    pytest.param(_set(('schema',), 'headroom-install-acceptance-v0'), id='other-schema'),
])
def test_tampered_acceptance_is_rejected(tmp_path, mutation):
    install, acceptance = headroom_proof(tmp_path, mutate_acceptance=mutation)
    with pytest.raises(ValueError, match='acceptance'):
        headroom_gate(install, acceptance)


def test_absent_acceptance_file_is_rejected(tmp_path):
    install, _ = headroom_proof(tmp_path)
    with pytest.raises(ValueError, match='acceptance'):
        headroom_gate(install, tmp_path / 'missing-acceptance.json')


def test_absent_installer_receipt_file_is_rejected(tmp_path):
    _, acceptance = headroom_proof(tmp_path)
    with pytest.raises(ValueError, match='installer receipt'):
        headroom_gate(tmp_path / 'missing-install-receipt.json', acceptance)


def test_rehashed_uninstalled_parent_is_rejected(tmp_path):
    install, acceptance = headroom_proof(
        tmp_path, mutate_installation=_set(('installed',), False))
    with pytest.raises(ValueError):
        headroom_gate(install, acceptance)


@pytest.mark.parametrize('field,value', [
    ('release_id', '427dfa8ac7ad6b7b58edc6b59a873897'),
    ('board_state', 'VERIFIED_SAFE_ROLLBACK'),
    ('outcome', 'UNKNOWN'),
    ('board_state', None),
])
def test_rehashed_wrong_installer_summary_is_rejected(tmp_path, field, value):
    install, acceptance = headroom_proof(
        tmp_path, mutate_installation=_set((field,), value))
    with pytest.raises(ValueError):
        headroom_gate(install, acceptance)


@pytest.mark.parametrize('field,value', [
    ('board_state', 'VERIFIED_SAFE_ROLLBACK'),
    ('outcome', 'UNKNOWN'),
    ('installed', False),
])
def test_acceptance_summary_disagreeing_with_its_receipt_is_rejected(tmp_path, field, value):
    def mutate_acceptance(document):
        document['installer_receipt'][field] = value
    install, acceptance = headroom_proof(tmp_path, mutate_acceptance=mutate_acceptance)
    with pytest.raises(ValueError, match='installer receipt'):
        headroom_gate(install, acceptance)


@pytest.mark.parametrize('mutation', [
    pytest.param(_set(('safe',), False), id='safe-false'),
    pytest.param(_set(('release_verified',), False), id='release-unverified'),
    pytest.param(_set(('release_id',), '427dfa8ac7ad6b7b58edc6b59a873897'), id='other-release'),
    pytest.param(_set(('status', 'stale'), True), id='readback-stale'),
    pytest.param(_set(('status', 'online'), False), id='service-offline'),
    pytest.param(_set(('status', 'armed'), True), id='core-armed'),
    pytest.param(_set(('status', 'pl_status', 'io_ok'), False), id='pl-io-not-ok'),
    pytest.param(_set(('status', 'pl_status', 'tx', 'source_selected'), True), id='tx-source-selected'),
    pytest.param(_set(('status', 'pl_status', 'rx', 'armed'), True), id='rx-armed'),
    pytest.param(_set(('services', 'rf-daemon', 'ActiveState'), 'failed'), id='service-failed'),
    pytest.param(_set(('services', 'rf-daemon', 'SubState'), 'dead'), id='service-dead'),
    pytest.param(_set(('services', 'rf-daemon', 'MainPID'), '0'), id='service-no-pid'),
    pytest.param(_set(('software_contract',), 'cnn-bpsk-environment-v1'), id='other-contract'),
])
def test_unsafe_or_unbound_readback_state_is_rejected(tmp_path, mutation):
    install, acceptance = headroom_proof(tmp_path, mutate_readback=mutation)
    with pytest.raises(ValueError):
        headroom_gate(install, acceptance)


@pytest.mark.parametrize('parent', [
    pytest.param(CANDIDATE, id='427dfa-environment-release'),
    pytest.param(S1_CANDIDATE, id='c3-teaching-release'),
])
def test_another_release_cannot_stand_in_for_the_b82_parent(parent):
    with pytest.raises(ValueError):
        D.verify_gates(ENV_CANDIDATE, CLOSURE, ENV_ARM, parent, B82_INSTALL, B82_ACCEPTANCE)


def test_environment_parent_with_the_headroom_installer_receipt_is_rejected():
    with pytest.raises(ValueError):
        D.verify_gates(ENV_CANDIDATE, CLOSURE, ENV_ARM, CANDIDATE, B82_INSTALL, B82_ACCEPTANCE)


# --------------------------------------------------------------------------- #
# prepare/verify_bundle: both directions recompute the same chain.
# --------------------------------------------------------------------------- #

@pytest.fixture
def prepared(tmp_path):
    install, acceptance = headroom_proof(tmp_path / 'proof')
    output = tmp_path / 'bundle'
    receipt = D.prepare(ENV_CANDIDATE, CLOSURE, ENV_ARM, output, B82_CANDIDATE,
                        install, acceptance)
    return dict(output=output, receipt=receipt, install=install, acceptance=acceptance)


def test_headroom_prepare_and_verify_bundle_round_trip(prepared):
    output, receipt = prepared['output'], prepared['receipt']
    assert receipt['parent_release'] == B82
    assert receipt['parent_acceptance'] == str(prepared['acceptance'].resolve())
    assert receipt['parent_install_sha256'] == D.sha(prepared['install'])
    assert receipt['parent_acceptance_sha256'] == D.sha(prepared['acceptance'])
    assert D.sha(output / 'candidate.tgz') == receipt['payload_sha256']['candidate.tgz']
    verified = D.verify_bundle(output)
    assert verified['parent_release'] == B82
    assert verified['parent_acceptance_sha256'] == D.sha(prepared['acceptance'])
    assert verified['release_id'] == receipt['release_id']
    assert verified['members'] == receipt['members']


def test_bundle_parent_relabelled_as_b82_is_refused(prepared):
    receipt_path = prepared['output'] / 'bundle-receipt.json'
    receipt = _load(receipt_path)
    receipt['parent_release'] = D.PARENT
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(ValueError, match='bundle qualification changed'):
        D.verify_bundle(prepared['output'])


def test_bundle_without_the_recorded_acceptance_digest_is_refused(prepared):
    receipt_path = prepared['output'] / 'bundle-receipt.json'
    receipt = _load(receipt_path)
    del receipt['parent_acceptance_sha256']
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(ValueError, match='bundle qualification changed'):
        D.verify_bundle(prepared['output'])


@pytest.mark.parametrize('target', ['acceptance', 'install', 'readback'])
def test_bundle_refuses_a_parent_proof_file_changed_after_prepare(prepared, target):
    path = prepared['acceptance'] if target == 'acceptance' else prepared['install']
    if target == 'readback':
        path = prepared['install'].parent / 'readback-state-new.json'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError):
        D.verify_bundle(prepared['output'])


def test_bundle_payload_tamper_is_refused(prepared):
    archive = prepared['output'] / 'candidate.tgz'
    archive.write_bytes(archive.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='bundle payload changed'):
        D.verify_bundle(prepared['output'])


@pytest.fixture
def default_parent_bundle(tmp_path):
    output = tmp_path / 'bundle'
    receipt = D.prepare(ENV_CANDIDATE, CLOSURE, ENV_ARM, output)
    return dict(output=output, receipt=receipt)


def test_default_parent_bundle_is_bound_to_c3_and_carries_no_b82_proof(default_parent_bundle):
    receipt = default_parent_bundle['receipt']
    assert receipt['parent_release'] == D.PARENT == C3
    assert receipt['parent_release'] != B82
    assert receipt['parent_acceptance'] is None
    assert 'parent_acceptance_sha256' not in receipt
    assert D.verify_bundle(default_parent_bundle['output'])['parent_release'] == C3


def test_default_parent_bundle_cannot_claim_the_b82_parent(default_parent_bundle):
    receipt_path = default_parent_bundle['output'] / 'bundle-receipt.json'
    receipt = _load(receipt_path)
    receipt['parent_release'] = B82
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(ValueError, match='bundle qualification changed'):
        D.verify_bundle(default_parent_bundle['output'])
