"""Rollback-parent routes accepted by the HEADROOM deployment gate.

Three routes, each proved by the evidence of the release it names: the
historical adfac installation with its legacy post-state.json, the b67
recovery proof, and -- added 2026-10-15 -- the release installed right now,
proved by its own installation receipt, the read-back state that receipt binds
by sha256 and the same verified_state() predicate the installer itself uses.
"""
import json
from pathlib import Path
import shutil
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import rf_headroom_deploy as D
ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT / 'evidence/rf-teaching/s2/headroom-contract-2026-10-11/candidate-budget/releases/b67a8aaab0c5904822580c0647b96fd0'
PROOF = ROOT / 'evidence/rf-teaching/s2/board-recovery-2026-10-12/recovery-checkpoint.json'

# The historical adfac route: its own installer receipt plus the legacy read-back.
ADFAC = 'adfac1f14c3cdb379cb92569a21a9054'
ADFAC_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2/g1-qualification-2026-10-08/candidate/'
                   'releases' / ADFAC)
ADFAC_INSTALL = ROOT / 'evidence/rf-teaching/s2/g2-execution-2026-10-10/install/install-receipt.json'

# The HEADROOM release this round reinstalls, with the gate inputs it was qualified by.
B82 = 'b82b141e355b4cb1833969383a409c79'
B82_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                 'candidate-final/releases' / B82)
HEADROOM_ARM = ROOT / ('evidence/rf-teaching/s2/executable-candidate-2026-10-12/arm-runtime/'
                       'receipt.json')
HEADROOM_CHECKPOINT = ROOT / ('evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                              'development-checkpoint.json')

# The release the board installed on 2026-10-15: the parent a new bundle needs.
CURRENT = '399b300e59b8d00e41d43c8123884c7f'
CURRENT_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-candidate-environment-2026-10-15/candidate/'
                     'releases' / CURRENT)
CURRENT_INSTALL = ROOT / ('evidence/rf-teaching/s2-environment-live-install-2026-10-15/'
                          'install-live/install-receipt.json')
CURRENT_READBACK = CURRENT_INSTALL.parent / 'post-state.json'


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _set(path, value):
    def mutate(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def _drop(*path):
    def mutate(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return mutate


def current_proof(directory, mutate_receipt=None, mutate_state=None, write_readback=True,
                  rebind_readback=True):
    """Rebuild the real 399b live-install proof with one injected defect.

    The receipt is re-bound to the mutated read-back state by sha256 unless the
    caller asks for a broken digest instead, so a rehashed defect still fails
    only because the semantic requirement is enforced.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    receipt, state = _load(CURRENT_INSTALL), _load(CURRENT_READBACK)
    readback = directory / 'post-state.json'
    if mutate_state is not None:
        mutate_state(state)
    if write_readback:
        readback.write_text(json.dumps(state, indent=2), encoding='utf-8')
        if rebind_readback:
            receipt['evidence_sha256']['post-state.json'] = D.sha(readback)
    if mutate_receipt is not None:
        mutate_receipt(receipt)
    path = directory / 'install-receipt.json'
    path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return path


# --------------------------------------------------------------------------- #
# The two historical routes stay accepted.
# --------------------------------------------------------------------------- #

def test_current_recovered_parent_is_qualified_without_rewriting_unknown():
    original = (ROOT / 'evidence/rf-teaching/s2/headroom-research-2026-10-11/install/install-receipt.json').read_bytes()
    assert hasattr(D, 'verify_parent'), 'actual recovered rollback parent gate missing'
    assert D.verify_parent(PARENT, PROOF) == 'b67a8aaab0c5904822580c0647b96fd0'
    assert (ROOT / 'evidence/rf-teaching/s2/headroom-research-2026-10-11/install/install-receipt.json').read_bytes() == original


def test_recovery_proof_does_not_accept_false_safety(tmp_path):
    assert hasattr(D, 'verify_parent'), 'actual recovered rollback parent gate missing'
    data = json.loads(PROOF.read_text('utf8')); data['safe'] = False
    proof = tmp_path / 'checkpoint.json'; proof.write_text(json.dumps(data), encoding='utf8')
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(PARENT, proof)


def test_historical_adfac_post_state_parent_is_still_accepted():
    assert _load(ADFAC_INSTALL)['release_id'] == ADFAC
    assert D.verify_parent(ADFAC_CANDIDATE, ADFAC_INSTALL) == ADFAC


def test_historical_adfac_parent_is_not_widened_to_another_receipt():
    """The hard-coded route still checks the receipt it is handed, not the name."""
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(ADFAC_CANDIDATE, CURRENT_INSTALL)


# --------------------------------------------------------------------------- #
# The third route: the release installed right now, accepted on evidence shape.
# --------------------------------------------------------------------------- #

def test_installed_environment_parent_is_accepted_by_evidence_shape():
    assert D.verify_parent(CURRENT_CANDIDATE, CURRENT_INSTALL) == CURRENT


def test_installed_parent_evidence_is_hash_bound_and_safe():
    """What the accepted route rests on, so a later change to it is visible."""
    receipt, state = _load(CURRENT_INSTALL), _load(CURRENT_READBACK)
    assert receipt['installed'] is True
    assert receipt['release_id'] == CURRENT
    assert receipt['board_state'] == 'VERIFIED_SAFE_NEW_RELEASE'
    assert receipt['evidence_sha256']['post-state.json'] == D.sha(CURRENT_READBACK)
    assert state['software_contract'] == _load(
        CURRENT_CANDIDATE / 'merged_release_manifest.json')['software_contract']
    assert D.verified_state(state, CURRENT) is True


def test_gate_binds_the_installed_environment_parent():
    gate = D.verify_gates(B82_CANDIDATE, HEADROOM_ARM, CURRENT_CANDIDATE, CURRENT_INSTALL,
                          HEADROOM_CHECKPOINT)
    assert gate['parent_release'] == CURRENT
    assert gate['release_id'] == B82
    assert gate['parent_install_sha256'] == D.sha(CURRENT_INSTALL)


# --------------------------------------------------------------------------- #
# Negative controls: every evidence step of the third route is load-bearing.
# --------------------------------------------------------------------------- #

def test_missing_parent_candidate_directory_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        D.verify_parent(tmp_path / 'missing-candidate', CURRENT_INSTALL)


def test_missing_install_receipt_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, tmp_path / 'missing-install-receipt.json')


def test_readback_binding_absent_is_rejected(tmp_path):
    install = current_proof(tmp_path, mutate_receipt=_drop('evidence_sha256', 'post-state.json'))
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


def test_readback_binding_absent_entirely_is_rejected(tmp_path):
    install = current_proof(tmp_path, mutate_receipt=_drop('evidence_sha256'))
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


def test_absent_readback_file_is_rejected(tmp_path):
    install = current_proof(tmp_path / 'no-readback', write_readback=False)
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


def test_stale_readback_digest_is_rejected(tmp_path):
    install = current_proof(tmp_path, mutate_receipt=_set(
        ('evidence_sha256', 'post-state.json'), '0' * 64))
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


def test_tampered_readback_bytes_are_rejected(tmp_path):
    install = current_proof(tmp_path, mutate_state=_set(('free_bytes',), 1),
                            rebind_readback=False)
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


def test_ambiguous_readback_binding_is_rejected(tmp_path):
    def both(document):
        document['evidence_sha256']['readback-state-new.json'] = (
            document['evidence_sha256']['post-state.json'])
    install = current_proof(tmp_path, mutate_receipt=both)
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


@pytest.mark.parametrize('mutation', [
    pytest.param(_set(('installed',), False), id='not-installed'),
    pytest.param(_set(('installed',), None), id='installed-absent'),
    pytest.param(_set(('release_id',), B82), id='other-release'),
    pytest.param(_set(('board_state',), 'VERIFIED_SAFE_ROLLBACK'), id='other-board-state'),
    pytest.param(_set(('board_state',), 'UNKNOWN'), id='unknown-board-state'),
    pytest.param(_set(('board_state',), None), id='board-state-absent'),
])
def test_rehashed_uninstalled_or_unknown_receipt_is_rejected(tmp_path, mutation):
    install = current_proof(tmp_path, mutate_receipt=mutation)
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


@pytest.mark.parametrize('mutation', [
    pytest.param(_set(('safe',), False), id='safe-false'),
    pytest.param(_set(('release_verified',), False), id='release-unverified'),
    pytest.param(_set(('release_id',), B82), id='other-release'),
    pytest.param(_set(('expected_release',), B82), id='other-expected-release'),
    pytest.param(_set(('status', 'release_id'), B82), id='status-other-release'),
    pytest.param(_set(('status', 'online'), False), id='daemon-offline'),
    pytest.param(_set(('status', 'stale'), True), id='daemon-stale'),
    pytest.param(_set(('status', 'armed'), True), id='core-armed'),
    pytest.param(_set(('status', 'pl_status', 'io_ok'), False), id='pl-io-not-ok'),
    pytest.param(_set(('status', 'pl_status', 'tx', 'source_selected'), True),
                 id='tx-source-selected'),
    pytest.param(_set(('status', 'pl_status', 'rx', 'armed'), True), id='rx-armed'),
    pytest.param(_drop('services', 'rf-daemon'), id='service-missing'),
    pytest.param(_set(('services', 'rf-daemon', 'ActiveState'), 'failed'), id='service-failed'),
    pytest.param(_set(('services', 'rf-daemon', 'SubState'), 'dead'), id='service-dead'),
    pytest.param(_set(('services', 'pynq-status', 'MainPID'), '0'), id='service-no-pid'),
    pytest.param(_set(('active_release',),
                      '/home/xilinx/overlays/awg_merged/releases/' + B82), id='other-active'),
])
def test_rehashed_unsafe_readback_is_rejected(tmp_path, mutation):
    install = current_proof(tmp_path, mutate_state=mutation)
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


def test_readback_rejection_is_the_installer_predicate(tmp_path):
    """The verdict is verified_state()'s own, not a private re-implementation."""
    install = current_proof(tmp_path, mutate_state=_set(
        ('services', 'rf-daemon', 'SubState'), 'dead'))
    assert D.verified_state(_load(install.parent / 'post-state.json'), CURRENT) is False
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(CURRENT_CANDIDATE, install)


def test_readback_under_another_contract_is_rejected(tmp_path):
    install = current_proof(tmp_path, mutate_state=_set(('software_contract',), D.CONTRACT))
    assert _load(CURRENT_CANDIDATE / 'merged_release_manifest.json')[
        'software_contract'] != D.CONTRACT
    with pytest.raises(ValueError, match='contract'):
        D.verify_parent(CURRENT_CANDIDATE, install)


@pytest.mark.parametrize('candidate', [
    pytest.param(B82_CANDIDATE, id='b82'),
    pytest.param(ADFAC_CANDIDATE, id='adfac'),
    pytest.param(PARENT, id='b67a'),
])
def test_another_release_cannot_borrow_the_installed_receipt(candidate):
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(candidate, CURRENT_INSTALL)


def test_renamed_receipt_cannot_impersonate_another_release(tmp_path):
    """Rebinding every digest does not launder a rename: the state still names 399b."""
    directory = tmp_path / 'renamed'
    install = current_proof(directory)
    receipt, state = _load(install), _load(directory / 'post-state.json')
    receipt['release_id'] = B82
    state['release_id'] = B82
    state['expected_release'] = B82
    (directory / 'post-state.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    receipt['evidence_sha256']['post-state.json'] = D.sha(directory / 'post-state.json')
    install.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    assert D.verified_state(_load(directory / 'post-state.json'), B82) is False
    with pytest.raises(ValueError, match='rollback parent'):
        D.verify_parent(B82_CANDIDATE, install)


def test_fully_renamed_proof_is_refused_by_the_contract_binding(tmp_path):
    """Even a self-consistent rename fails: the read-back declares its own contract."""
    directory = tmp_path / 'consistent-rename'
    install = current_proof(directory)
    receipt, state = _load(install), _load(directory / 'post-state.json')
    state['release_id'] = state['expected_release'] = B82
    state['status']['release_id'] = B82
    state['active_release'] = '/home/xilinx/overlays/awg_merged/releases/' + B82
    (directory / 'post-state.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    receipt['release_id'] = B82
    receipt['evidence_sha256']['post-state.json'] = D.sha(directory / 'post-state.json')
    install.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    assert D.verified_state(_load(directory / 'post-state.json'), B82) is True
    with pytest.raises(ValueError, match='contract'):
        D.verify_parent(B82_CANDIDATE, install)


def test_historical_adfac_receipt_with_unsafe_board_state_is_rejected(tmp_path):
    """The adfac route's own board_state clause, beside a read-back that fits.

    The pre-existing negative for this route mutates a receipt whose post-state.json
    does not travel with it, so the read-back clause refuses that copy first and the
    board_state clause is never reached.  Here the read-back is copied too, so this
    clause is the one that has to refuse.
    """
    receipt = _load(ADFAC_INSTALL)
    receipt['board_state'] = 'UNKNOWN'
    (tmp_path / 'post-state.json').write_bytes(
        (ADFAC_INSTALL.parent / 'post-state.json').read_bytes())
    path = tmp_path / 'install-receipt.json'
    path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    with pytest.raises(ValueError, match='verified rollback parent installation required'):
        D.verify_parent(ADFAC_CANDIDATE, path)


def test_recovery_proof_is_bound_to_its_own_safe_check(tmp_path):
    """The b67 route's checkpoint safe clause, with all three documents present.

    The pre-existing negative runs the checkpoint on its own, so the document-hash
    loop refuses it first.  Copying the documents it hashes reaches the clause.
    """
    for name in ('recovery-checkpoint.json', 'final-safe-state.json',
                 'final-os-observation.json', 'final-observation-receipt.json'):
        (tmp_path / name).write_bytes((PROOF.parent / name).read_bytes())
    checkpoint = _load(PROOF)
    checkpoint['safe'] = False
    proof = tmp_path / 'recovery-checkpoint.json'
    proof.write_text(json.dumps(checkpoint, indent=2), encoding='utf-8')
    with pytest.raises(ValueError, match='verified rollback parent recovery required'):
        D.verify_parent(PARENT, proof)


def test_tampered_parent_candidate_file_is_rejected(tmp_path):
    """The candidate directory is itself hash-bound: one changed byte refuses it."""
    copy = tmp_path / 'candidate'
    # copyfile rather than copy2: the sealed evidence carries the read-only attribute.
    shutil.copytree(CURRENT_CANDIDATE, copy, copy_function=shutil.copyfile)
    target = copy / 'rf_environment.py'
    target.write_bytes(target.read_bytes() + b'X')
    with pytest.raises(ValueError, match='software hash mismatch'):
        D.verify_parent(copy, CURRENT_INSTALL)
