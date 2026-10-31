"""The installed release a read-only check may expect, resolved from evidence.

The read-only checker used to carry a hard-coded expectation, b67a8aaa, which was
correct only while b67 was installed. On 2026-10-13 the b82 release was installed
and verified, so the expectation has to come from (a) the original installation
evidence and (b) a read-back taken now, not from a name.

This file pins both halves. Every fixture is a real file recorded by a real
installation or a real read-only observation; the negative cases either show a
second real file that must be refused or a *coherent* falsification of a real
document (a mutated file plus the digests its binders record, recomputed), never
a document that only looks broken because a digest was left behind.

b67a8aaa keeps its historical meaning: it is the candidate build parent and the
historical recovery parent, and the read-only check must still accept a board
that is still running it.
"""
import json
from pathlib import Path
import shutil
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import rf_headroom_deploy as D

ROOT = Path(__file__).resolve().parents[2]

B82 = 'b82b141e355b4cb1833969383a409c79'
B67 = 'b67a8aaab0c5904822580c0647b96fd0'
DRIFT = 'aba80e92aec4ed3d4e83196733a800ae'
ENVIRONMENT = '399b300e59b8d00e41d43c8123884c7f'

INSTALLER_RECEIPT = ROOT / 'evidence/rf-teaching/s2-parent-b82-proof-2026-10-15/install-receipt.json'
ACCEPTANCE_RECEIPT = (ROOT / 'evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/'
                      'acceptance-receipt-b82b141e.json')
INSTALLER_RECEIPT_SHA = '9dfb0adeef055157edf1d8be0e64d8775953c69f251cbaeabb7b8b4bd9621be7'
INSTALL_READBACK_SHA = 'e15f5e4b87d7a263b7370447f4bc06e61d911757f2ca14f9b6f6fc4485bb4c7c'
INSTALL_BOOT = 'd85fa469-aeb7-4ef9-860b-15389314ef30'
CURRENT_BOOT = 'd39c6371-9905-4255-aa99-cfa6de6e619a'

INSTALL_READBACK = INSTALLER_RECEIPT.parent / 'readback-state-new.json'
ACCEPTANCE_STATE = ACCEPTANCE_RECEIPT.parent / 'acceptance-state-b82b141e.json'
RECOVERY_STATE = ROOT / 'evidence/rf-teaching/s2/board-recovery-2026-10-12/final-safe-state.json'
ENVIRONMENT_STATE = (ROOT / 'evidence/rf-teaching/s2-environment-live-install-2026-10-15/'
                     'install-live/post-state.json')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rewrite(path, mutate):
    document = load(path)
    mutate(document)
    path.write_text(json.dumps(document, indent=2), encoding='utf-8')
    return path


def copy_to(path, directory):
    target = Path(directory) / Path(path).name
    shutil.copyfile(path, target)
    return target


def rebind_acceptance(acceptance, installer):
    return rewrite(acceptance, lambda d: d['installer_receipt'].update(sha256=D.sha(installer)))


def rebind_install_readback(installer, readback):
    return rewrite(installer, lambda d: d['evidence_sha256'].update(
        {'readback-state-new.json': D.sha(readback)}))


def approve(evidence, state, boot=INSTALL_BOOT, readback_boot=None, faults=(), rw=True):
    return D.approve_installed_readback(
        evidence, state, readback_boot_id=boot if readback_boot is None else readback_boot,
        boot_id=boot, storage_faults=list(faults), root_read_write=rw)


def test_the_real_b82_installation_evidence_resolves_to_b82():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    assert evidence['release_id'] == B82
    assert evidence['installer_receipt_sha256'] == INSTALLER_RECEIPT_SHA
    assert evidence['install_readback_sha256'] == INSTALL_READBACK_SHA
    assert evidence['install_boot_id'] == INSTALL_BOOT
    assert set(evidence['approved']) == {B82, B67}


def test_the_historical_recovery_parent_and_rollback_parent_keep_their_meaning():
    assert D.RECOVERED_PARENT == B67
    assert D.ROLLBACK_PARENT == 'adfac1f14c3cdb379cb92569a21a9054'
    assert (ROOT / 'evidence/rf-teaching/s2/board-recovery-2026-10-12/'
            'recovery-checkpoint.json').is_file()
# (i) the release the original installation evidence proves, and (ii) the
# historical recovery parent, are the only two releases a read-back may report.

def test_the_real_b82_readback_is_approved_as_the_evidence_proven_release():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    approved = approve(evidence, load(INSTALL_READBACK))
    assert approved['release_id'] == B82
    assert approved['source'] == 'original-installation-evidence'


def test_the_acceptance_state_of_the_same_installation_is_approved():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    approved = approve(evidence, load(ACCEPTANCE_STATE))
    assert approved['release_id'] == B82
    assert approved['source'] == 'original-installation-evidence'


def test_a_real_b67_readback_is_still_approved_as_the_historical_parent():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    assert load(RECOVERY_STATE)['release_id'] == B67
    approved = approve(evidence, load(RECOVERY_STATE))
    assert approved['release_id'] == B67
    assert approved['source'] == 'historical-recovery-parent'


# (iii) an unknown or drifted release is refused by name.

def test_a_real_readback_of_another_installed_release_is_refused():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    state = load(ENVIRONMENT_STATE)
    assert state['release_id'] == ENVIRONMENT
    with pytest.raises(D.InstalledReleaseRefused,
                       match='neither the evidence-proven release nor the historical'):
        approve(evidence, state)


def test_a_coherently_drifted_readback_is_refused_with_its_release_named(tmp_path):
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    drifted = copy_to(INSTALL_READBACK, tmp_path)

    def mutate(document):
        document.update(release_id=DRIFT, expected_release=DRIFT,
                        active_release=document['active_release'].replace(B82, DRIFT))
        document['status'].update(release_id=DRIFT, bit_sha256='0' * 64, hwh_sha256='1' * 64)

    rewrite(drifted, mutate)
    with pytest.raises(D.InstalledReleaseRefused, match=DRIFT):
        approve(evidence, load(drifted))


# (iv) missing evidence, a digest that does not match, an installation that is not
# recorded as installed, and evidence that disagrees about the release.

def test_missing_installation_evidence_is_refused(tmp_path):
    with pytest.raises(D.InstalledReleaseRefused, match='original installation evidence missing'):
        D.resolve_installed_release_evidence(tmp_path / 'absent.json', INSTALLER_RECEIPT)


def test_a_missing_installer_receipt_is_refused(tmp_path):
    with pytest.raises(D.InstalledReleaseRefused, match='installer receipt missing'):
        D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, tmp_path / 'absent.json')


def test_a_digest_that_does_not_match_the_installer_receipt_is_refused(tmp_path):
    wrong = copy_to(INSTALL_READBACK, tmp_path)
    with pytest.raises(D.InstalledReleaseRefused, match='digest differs from the installer receipt'):
        D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, wrong)


def test_a_coherently_rebound_receipt_that_is_not_installed_is_refused(tmp_path):
    installer = rewrite(copy_to(INSTALLER_RECEIPT, tmp_path), lambda d: d.update(installed=False))
    acceptance = rebind_acceptance(copy_to(ACCEPTANCE_RECEIPT, tmp_path), installer)
    with pytest.raises(D.InstalledReleaseRefused, match='does not record an installed release'):
        D.resolve_installed_release_evidence(acceptance, installer)


def test_a_coherently_rebound_receipt_with_another_outcome_is_refused(tmp_path):
    installer = rewrite(copy_to(INSTALLER_RECEIPT, tmp_path),
                        lambda d: d.update(outcome='UNEXPECTED_STATE'))
    acceptance = rebind_acceptance(copy_to(ACCEPTANCE_RECEIPT, tmp_path), installer)
    with pytest.raises(D.InstalledReleaseRefused, match='outcome is not INSTALLED_VERIFIED'):
        D.resolve_installed_release_evidence(acceptance, installer)


def test_a_coherently_rebound_readback_that_is_not_safe_is_refused(tmp_path):
    readback = rewrite(copy_to(INSTALL_READBACK, tmp_path), lambda d: d.update(safe=False))
    installer = rebind_install_readback(copy_to(INSTALLER_RECEIPT, tmp_path), readback)
    acceptance = rebind_acceptance(copy_to(ACCEPTANCE_RECEIPT, tmp_path), installer)
    with pytest.raises(D.InstalledReleaseRefused, match='read-back is not a verified safe state'):
        D.resolve_installed_release_evidence(acceptance, installer)


def test_evidence_that_disagrees_about_the_release_is_refused(tmp_path):
    acceptance = rewrite(copy_to(ACCEPTANCE_RECEIPT, tmp_path),
                         lambda d: d['snapshot'].update(current_release=DRIFT))
    with pytest.raises(D.InstalledReleaseRefused, match='disagrees about the release'):
        D.resolve_installed_release_evidence(acceptance, INSTALLER_RECEIPT)


def test_evidence_without_an_accepted_verdict_is_refused(tmp_path):
    acceptance = rewrite(copy_to(ACCEPTANCE_RECEIPT, tmp_path),
                         lambda d: d.update(accepted=False, verdict='REJECTED'))
    with pytest.raises(D.InstalledReleaseRefused, match='not an accepted verification'):
        D.resolve_installed_release_evidence(acceptance, INSTALLER_RECEIPT)


def test_evidence_with_a_failed_check_is_refused(tmp_path):
    acceptance = rewrite(copy_to(ACCEPTANCE_RECEIPT, tmp_path),
                         lambda d: d['checks'].update(rf_safe=False))
    with pytest.raises(D.InstalledReleaseRefused, match='failed check'):
        D.resolve_installed_release_evidence(acceptance, INSTALLER_RECEIPT)


def test_evidence_that_records_rf_activity_is_refused(tmp_path):
    acceptance = rewrite(copy_to(ACCEPTANCE_RECEIPT, tmp_path),
                         lambda d: d.update(new_rf_frames=1))
    with pytest.raises(D.InstalledReleaseRefused, match='records RF activity'):
        D.resolve_installed_release_evidence(acceptance, INSTALLER_RECEIPT)


# (v) a read-back that is not safe: services, RF, storage, root filesystem.

def test_a_readback_whose_services_are_not_running_is_refused(tmp_path):
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    state = rewrite(copy_to(INSTALL_READBACK, tmp_path),
                    lambda d: d['services']['rf-daemon'].update(SubState='dead'))
    with pytest.raises(D.InstalledReleaseRefused, match='services are not both running'):
        approve(evidence, load(state))


def test_a_readback_whose_rf_is_not_safe_is_refused(tmp_path):
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    state = rewrite(copy_to(INSTALL_READBACK, tmp_path), lambda d: d['status'].update(armed=True))
    with pytest.raises(D.InstalledReleaseRefused, match='RF that is not safe'):
        approve(evidence, load(state))


def test_a_readback_with_kernel_storage_faults_is_refused():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    with pytest.raises(D.InstalledReleaseRefused, match='kernel storage faults'):
        approve(evidence, load(INSTALL_READBACK), faults=['mmc0: error -110'])


def test_a_readback_whose_root_is_not_read_write_is_refused():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    with pytest.raises(D.InstalledReleaseRefused, match='root is not read-write'):
        approve(evidence, load(INSTALL_READBACK), rw=False)


# (vi) a boot identity that changed, and a historical file offered with no
# read-back taken during the execution that is asking.

def test_a_readback_without_an_execution_time_readback_is_refused():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    with pytest.raises(D.InstalledReleaseRefused, match='execution-time read-back required'):
        approve(evidence, None)


def test_a_readback_taken_under_another_boot_is_refused():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    with pytest.raises(D.InstalledReleaseRefused, match='taken under another boot'):
        approve(evidence, load(INSTALL_READBACK), boot=CURRENT_BOOT, readback_boot=INSTALL_BOOT)


def test_a_readback_without_a_boot_identity_is_refused():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    with pytest.raises(D.InstalledReleaseRefused, match='taken under another boot'):
        D.approve_installed_readback(evidence, load(INSTALL_READBACK), readback_boot_id=None,
                                     boot_id=INSTALL_BOOT, storage_faults=[], root_read_write=True)


def test_a_historical_document_offered_as_the_current_readback_is_refused():
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    assert D.verified_state(load(RECOVERY_STATE), B67)
    with pytest.raises(D.InstalledReleaseRefused, match='taken under another boot'):
        approve(evidence, load(RECOVERY_STATE), boot=CURRENT_BOOT, readback_boot=INSTALL_BOOT)

# (vii) the two guards a document that is coherent on its surface still cannot
# pass: the read-back must be a verified safe state of the release it *names*,
# not merely a document whose expected_release field agrees with it; and the
# installer receipt must bind exactly one read-back, so a second document whose
# digest is recorded just as correctly cannot be offered in its place.

def test_a_readback_that_is_not_a_verified_state_of_the_release_it_names_is_refused(tmp_path):
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    assert D.verified_state(load(INSTALL_READBACK), B82)
    state = rewrite(copy_to(INSTALL_READBACK, tmp_path),
                    lambda d: d.update(active_release=D.RELEASE_PATH_PREFIX + B67))
    document = load(state)
    # Every field the earlier guards read still agrees: the document names b82
    # as its release everywhere, and only its own active-releases pointer names
    # the historical parent.  Safe_state and services still pass.
    assert document['release_id'] == B82
    assert document['expected_release'] == B82
    assert document['status']['release_id'] == B82
    assert document['safe'] is True and D.safe_state(document['status'], B82) is True
    assert D.verified_state(document, B82) is False
    with pytest.raises(D.InstalledReleaseRefused,
                       match='is not a verified safe state of the release it names'):
        approve(evidence, document)


def test_an_installer_receipt_that_binds_two_readbacks_is_refused(tmp_path):
    readback = copy_to(INSTALL_READBACK, tmp_path)
    second = Path(tmp_path) / 'post-state.json'
    shutil.copyfile(INSTALL_READBACK, second)
    installer = rewrite(copy_to(INSTALLER_RECEIPT, tmp_path), lambda d: d['evidence_sha256'].update(
        {'post-state.json': D.sha(second)}))
    acceptance = rebind_acceptance(copy_to(ACCEPTANCE_RECEIPT, tmp_path), installer)
    # The chain is coherent, not merely digest-stale: both names are bound to a
    # document that really is a verified safe state of b82, and the acceptance
    # receipt records the digest of this installer receipt.  Removing the
    # exactly-one-read-back check alone would let this evidence resolve.
    bound = load(installer)['evidence_sha256']
    assert bound['readback-state-new.json'] == D.sha(readback)
    assert bound['post-state.json'] == D.sha(second)
    assert load(acceptance)['installer_receipt']['sha256'] == D.sha(installer)
    assert D.verified_state(load(second), B82)
    with pytest.raises(D.InstalledReleaseRefused,
                       match='does not bind exactly one read-back'):
        D.resolve_installed_release_evidence(acceptance, installer)


# (viii) order semantics: a release that is not approved is refused by name even
# when the same document is also unsafe, so the refusal always says which
# release was not approved rather than which check happened to run first.

def test_a_document_of_another_release_is_refused_by_membership_before_its_rf_verdict(tmp_path):
    evidence = D.resolve_installed_release_evidence(ACCEPTANCE_RECEIPT, INSTALLER_RECEIPT)
    state = rewrite(copy_to(ENVIRONMENT_STATE, tmp_path), lambda d: d['status'].update(armed=True))
    document = load(state)
    assert document['release_id'] == ENVIRONMENT
    assert document['safe'] is True and D.safe_state(document['status'], ENVIRONMENT) is False
    with pytest.raises(D.InstalledReleaseRefused,
                       match='neither the evidence-proven release nor the historical recovery '
                             'parent: ' + ENVIRONMENT):
        approve(evidence, document)
