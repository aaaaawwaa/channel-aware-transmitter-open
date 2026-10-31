"""The HEADROOM-1 installation gate, against the frozen qualification it binds.

These tests exist because the gate's first version could not be satisfied by the
qualification it was written for: it demanded byte-identical host/ARM artifacts
and used the pre-implementation case names.  It had never been executed while the
stage was sealed as `installation: NOT_RUN`.  The chain check is now bound to the
pinned chain results rather than to the shape of the receipt, and the adversarial
variants below are the ones an independent review used to break the shape-only
version.
"""
import json
from pathlib import Path
import re

import pytest

from scripts import rf_headroom_deploy as D

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / 'evidence/rf-teaching/s2/headroom-contract-2026-10-11'
CANDIDATE = STAGE / 'candidate-budget/releases/b67a8aaab0c5904822580c0647b96fd0'
ARM = STAGE / 'arm-runtime-budget/receipt.json'
PARENT_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2/g1-qualification-2026-10-08'
                    / 'candidate/releases/adfac1f14c3cdb379cb92569a21a9054')
PARENT_INSTALL = ROOT / 'evidence/rf-teaching/s2/g2-execution-2026-10-10/install/install-receipt.json'
CHECKPOINT = STAGE / 'stage-checkpoint.json'
SIBLINGS = ('runner.py', 'chain-runner.py', 'arm.json', 'host.json',
            'chain-arm.json', 'chain-host.json')


def receipt():
    return json.loads(ARM.read_text(encoding='utf-8'))


def pinned():
    return [json.loads((ARM.parent / name).read_text(encoding='utf-8'))
            for name in ('chain-arm.json', 'chain-host.json')]


def fixture(tmp_path, mutate):
    """A receipt the gate reads, next to the sibling files it re-hashes."""
    data = receipt()
    mutate(data)
    (tmp_path / 'receipt.json').write_text(json.dumps(data), encoding='utf-8')
    for name in SIBLINGS:
        (tmp_path / name).write_bytes((ARM.parent / name).read_bytes())
    return tmp_path


def test_gate_accepts_the_frozen_candidate_and_its_rollback_parent():
    """The whole gate, end to end, on the real frozen inputs."""
    gate = D.verify_gates(CANDIDATE, ARM, PARENT_CANDIDATE, PARENT_INSTALL, CHECKPOINT)
    assert gate['release_id'] == 'b67a8aaab0c5904822580c0647b96fd0'
    assert gate['parent_release'] == D.ROLLBACK_PARENT
    assert gate['contract'] == D.CONTRACT


def test_chain_gate_accepts_evidence_whose_artifacts_are_not_byte_identical():
    """Equivalence is the record comparison, not identical artifact bytes.

    The two platforms serialize the same computed value with a differing final
    bit, so three of the 96 saved artifacts differ.  The qualifier proves parity
    by comparing records (`chain_parity_mismatches`), and requiring byte equality
    made installation impossible.
    """
    data = receipt()
    chain, host = data['chain_artifacts_sha256'], data['chain_host_artifacts_sha256']
    assert set(chain) == set(host)
    assert any(chain[name] != host[name] for name in chain)
    assert D.chain_evidence_ok(data, *pinned()) is True


def test_receipt_inventories_must_equal_the_pinned_chain_results():
    """Variant A from the independent review: 96 digests renamed, zero agreeing."""
    data = receipt()
    data['chain_artifacts_sha256'] = {name: 'f' * 64 for name in data['chain_artifacts_sha256']}
    data['chain_host_artifacts_sha256'] = {name: '0' * 64 for name in data['chain_host_artifacts_sha256']}
    assert D.chain_evidence_ok(data, *pinned()) is False


def test_artifacts_cannot_be_dropped_and_replaced_by_planted_keys():
    """Variant B: 88 real artifacts dropped, eight invented keys planted."""
    data = receipt()
    data['chain_artifacts_sha256'] = {'%s/planted' % case: 'x' for case in D.CHAIN_CASES}
    data['chain_host_artifacts_sha256'] = dict(data['chain_artifacts_sha256'])
    assert D.chain_evidence_ok(data, *pinned()) is False


def test_bare_case_names_are_not_an_artifact_inventory():
    """Variant C: case names as keys with non-digest values."""
    data = receipt()
    data['chain_artifacts_sha256'] = {case: 'x' for case in D.CHAIN_CASES}
    data['chain_host_artifacts_sha256'] = dict(data['chain_artifacts_sha256'])
    assert D.chain_evidence_ok(data, *pinned()) is False


def test_every_stop_class_must_be_present_in_both_inventories():
    """Dropping a class on BOTH sides must be refused by the coverage clause.

    Dropping it on one side only trips the inventory-equality clause instead, so
    the coverage clause needs both maps edited to be exercised at all.
    """
    data = receipt()
    dropped = [name for name in data['chain_artifacts_sha256'] if name.startswith('rails/')]
    for name in dropped:
        del data['chain_artifacts_sha256'][name]
        del data['chain_host_artifacts_sha256'][name]
    assert D.chain_evidence_ok(data, *pinned()) is False


def test_host_and_arm_must_save_the_same_artifact_inventory():
    data = receipt()
    first = sorted(data['chain_host_artifacts_sha256'])[0]
    del data['chain_host_artifacts_sha256'][first]
    assert D.chain_evidence_ok(data, *pinned()) is False


def test_a_failing_chain_case_is_refused():
    """A pinned run that reports a failed stop class is not a qualification."""
    data = receipt()
    arm_result, host_result = pinned()
    case = sorted(arm_result['records'])[0]
    arm_result['records'][case] = dict(arm_result['records'][case], passed=False)
    assert D.chain_evidence_ok(data, arm_result, host_result) is False


def test_fake_hardware_must_be_labelled_simulation():
    data = receipt()
    arm_result, host_result = pinned()
    arm_result = dict(arm_result, hardware_touched=True)
    assert D.chain_evidence_ok(data, arm_result, host_result) is False
    arm_result = dict(pinned()[0], simulation=False)
    assert D.chain_evidence_ok(data, arm_result, host_result) is False
    assert D.chain_evidence_ok(data, *pinned()) is True


def test_absent_chain_evidence_is_refused():
    arm_result, host_result = pinned()
    assert D.chain_evidence_ok({}, arm_result, host_result) is False
    assert D.chain_evidence_ok({'chain_artifacts_sha256': {},
                                'chain_host_artifacts_sha256': {}},
                               arm_result, host_result) is False


def test_unqualified_candidate_is_refused(tmp_path):
    """The gate still refuses a candidate whose stage did not pass."""
    checkpoint = json.loads(CHECKPOINT.read_text(encoding='utf-8'))
    checkpoint['passed'] = False
    path = tmp_path / 'checkpoint.json'
    path.write_text(json.dumps(checkpoint), encoding='utf-8')
    with pytest.raises(ValueError, match='passing development/G1 stage'):
        D.verify_gates(CANDIDATE, ARM, PARENT_CANDIDATE, PARENT_INSTALL, path)


def test_over_budget_arm_qualification_is_refused(tmp_path):
    """The 300 ms budget stays binding; the retained 314.963 ms run is not usable."""
    arm = fixture(tmp_path, lambda data: data.update(p95_ms=314.96322809834965)) / 'receipt.json'
    with pytest.raises(ValueError, match='ARM qualification required'):
        D.verify_gates(CANDIDATE, arm, PARENT_CANDIDATE, PARENT_INSTALL, CHECKPOINT)


def test_gate_refuses_a_tampered_chain_inventory(tmp_path):
    """The whole gate, not just the helper, refuses variant A."""
    def mutate(data):
        data['chain_artifacts_sha256'] = {name: 'f' * 64
                                          for name in data['chain_artifacts_sha256']}
    arm = fixture(tmp_path, mutate) / 'receipt.json'
    with pytest.raises(ValueError, match='saved-IQ chain evidence incomplete'):
        D.verify_gates(CANDIDATE, arm, PARENT_CANDIDATE, PARENT_INSTALL, CHECKPOINT)


def test_unverified_rollback_parent_is_refused(tmp_path):
    installation = json.loads(PARENT_INSTALL.read_text(encoding='utf-8'))
    installation['board_state'] = 'UNKNOWN'
    path = tmp_path / 'parent-install.json'
    path.write_text(json.dumps(installation), encoding='utf-8')
    with pytest.raises(ValueError, match='verified rollback parent'):
        D.verify_gates(CANDIDATE, ARM, PARENT_CANDIDATE, path, CHECKPOINT)


def test_generated_stage_directory_is_accepted_by_the_reviewed_installer():
    """Activation must be staged where the installer's own guard expects it.

    The first real attempt exited 2 without touching the board: the deploy tool
    staged under `/tmp/rf-headroom-install-...` while install.sh accepts only
    `/tmp/rf-teaching-install-...`.  That guard is the installer's, so the tool
    has to match it rather than the other way round.
    """
    guard = re.search(r'case "\$stage" in (\S+?)\)',
                      (ROOT / 'scripts/rf_teaching_install.sh').read_text(encoding='utf-8'))
    assert guard, 'installer stage guard not found'
    pattern = guard.group(1)
    assert pattern.endswith('*')
    accepted = pattern[:-1]
    assert D.REMOTE_PREFIX == accepted
    for _ in range(4):
        stage = D.remote_stage()
        assert stage.startswith(accepted) and len(stage) == len(accepted) + 32
        assert re.fullmatch(re.escape(accepted) + '[0-9a-f]{32}', stage)
