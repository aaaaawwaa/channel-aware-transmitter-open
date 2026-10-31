"""The fourth rollback-parent route: a reviewed activation evidence chain.

On 2026-10-17 the board's current release was switched to e66197ed by the reviewed
activation orchestration, not installed, so the installer route has nothing to
accept -- and the activation documents must not be renamed into an
INSTALLED_VERIFIED receipt.  verify_installed_parent_via_activation_evidence
rebuilds that release's own chain (phase chain, ACTIVATION_ACCEPTED marker,
acceptance report, post-state, whole-tree comparison, independent read-back, final
confirmation and the artifact collection that binds the downloaded bytes) and binds
every one of them to the candidate directory R.verify_release has just verified.

These tests prove the real chain is accepted, that both deployment gates can use
it, and that each evidence step -- identity, digests, activation success, post
state, independent read-back, and a self-consistent forgery -- is load-bearing.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import merged_release as R          # noqa: E402
from scripts import rf_environment_deploy as E   # noqa: E402
from scripts import rf_headroom_deploy as D      # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RELEASE = 'e66197ed79131b73aa4f47804487c053'
CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-headroom-candidate2-2026-10-17/candidate/'
             'releases' / RELEASE)
EVIDENCE = ROOT / D.ACTIVATION_EVIDENCE
RELEASE_PREFIX = D.RELEASE_PATH_PREFIX

# An unrelated HEADROOM release: proves the evidence cannot be relabelled onto it.
OTHER = 'b82b141e355b4cb1833969383a409c79'
OTHER_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                   'candidate-final/releases' / OTHER)
OTHER_ARM = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
             'arm-runtime/receipt.json')
OTHER_CHECKPOINT = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                    'development-checkpoint.json')

# The environment candidate the 2026-10-15 round qualified, with its own closure/ARM.
ENV_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-candidate-environment-2026-10-15/'
                 'candidate/releases/399b300e59b8d00e41d43c8123884c7f')
ENV_ARM = ROOT / 'evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json'
ENV_CLOSURE = ROOT / 'evidence/rf-teaching/s1/closure-2026-10-04/receipt.json'
B82_ACCEPTANCE = ROOT / ('evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/'
                         'acceptance-receipt-b82b141e.json')


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def copy_evidence(tmp_path):
    """Copy only the documents the route reads, keeping the evidence layout."""
    directory = Path(tmp_path) / 'activation'
    for name in D.ACTIVATION_FILES:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((EVIDENCE / name).read_bytes())
    return directory


def rebind_collection(directory):
    """Make the collection agree with whatever is on disk -- forgery does this too."""
    path = Path(directory) / 'work/artifact-collection.json'
    collection = _load(path)
    for name in D.ACTIVATION_ARTIFACTS:
        artifact = Path(directory) / 'board-download' / name
        collection['downloaded'][name] = dict(sha256=D.sha(artifact),
                                              bytes=artifact.stat().st_size)
    _write(path, collection)


def mutate_json(directory, relative, mutate):
    path = Path(directory) / relative
    document = _load(path)
    mutate(document)
    _write(path, document)


def mutate_jsonl(directory, phase, **fields):
    path = Path(directory) / 'board-download/activation.jsonl'
    entries = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
               if line.strip()]
    for entry in entries:
        if entry.get('phase') == phase:
            entry.update(fields)
    path.write_text(''.join(json.dumps(entry) + '\n' for entry in entries), encoding='utf-8')


def _set(path, value):
    def mutate(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def parent_of(candidate=CANDIDATE):
    return R.verify_release(Path(candidate), require_software=True)


def relabel_to_other(directory):
    """Rewrite every release id inside the evidence, then rehash the collection."""
    for name in D.ACTIVATION_FILES:
        path = Path(directory) / name
        if path.suffix in ('.jsonl', '.log', '.txt'):
            path.write_text(path.read_text(encoding='utf-8').replace(RELEASE, OTHER),
                            encoding='utf-8')
        else:
            path.write_text(json.dumps(_load(path)).replace(RELEASE, OTHER),
                            encoding='utf-8')
    rebind_collection(directory)


# --------------------------------------------------------------------------- #
# The real chain, and both gates that consume it.
# --------------------------------------------------------------------------- #

def test_the_real_activation_chain_is_accepted_as_the_installed_parent():
    proof = D.verify_installed_parent_via_activation_evidence(parent_of(), EVIDENCE)
    assert proof['release_id'] == RELEASE
    assert proof['source'] == 'activation-evidence'
    assert proof['software_contract'] == 'cnn-bpsk-headroom-v1'
    assert proof['software_modules'] == 46
    assert set(proof['evidence_sha256']) == set(D.ACTIVATION_FILES)
    assert proof['evidence_sha256']['board-download/activation.jsonl'] == D.sha(
        EVIDENCE / 'board-download/activation.jsonl')


def test_the_activation_route_is_never_reached_unless_it_is_named():
    assert D.verify_parent(CANDIDATE, None, activation_evidence=EVIDENCE) == RELEASE
    with pytest.raises(ValueError, match='installation receipt'):
        D.verify_parent(CANDIDATE, None)


def test_the_cli_refuses_to_guess_between_parent_proofs():
    completed = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(CANDIDATE), '--arm', str(OTHER_ARM),
         '--parent-candidate', str(CANDIDATE), '--checkpoint', str(OTHER_CHECKPOINT),
         '--output', str(ROOT / 'evidence' / 'never-written-activation-bundle')],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert 'exactly one rollback-parent proof' in completed.stderr
    assert not (ROOT / 'evidence' / 'never-written-activation-bundle').exists()


def test_headroom_gate_binds_the_activation_parent_without_an_installer_receipt():
    gate = D.verify_gates(OTHER_CANDIDATE, OTHER_ARM, CANDIDATE, None, OTHER_CHECKPOINT,
                          EVIDENCE)
    assert gate['release_id'] == OTHER
    assert gate['parent_release'] == RELEASE
    assert gate['parent_activation'] == str(EVIDENCE.resolve())
    assert gate['parent_activation_sha256'] == {
        name: D.sha(EVIDENCE / name) for name in D.ACTIVATION_FILES}
    assert 'parent_install_sha256' not in gate


def test_environment_gate_binds_the_activation_parent_for_a_headroom_release():
    gate = E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None, None,
                          EVIDENCE)
    assert gate['parent_release'] == RELEASE
    assert gate['contract'] == E.CONTRACT
    assert gate['parent_activation'] == str(EVIDENCE.resolve())
    assert set(gate['parent_activation_sha256']) == set(D.ACTIVATION_FILES)
    assert 'parent_acceptance_sha256' not in gate


def test_the_two_parent_proofs_cannot_be_combined():
    with pytest.raises(ValueError, match='one S2 rollback parent proof'):
        E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None,
                       B82_ACCEPTANCE, EVIDENCE)


@pytest.fixture
def prepared(tmp_path):
    directory = copy_evidence(tmp_path / 'evidence')
    output = tmp_path / 'bundle'
    receipt = E.prepare(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, output, CANDIDATE, None,
                        None, directory)
    return dict(output=output, receipt=receipt, evidence=directory)


def test_prepare_and_verify_bundle_round_trip_on_the_activation_parent(prepared):
    receipt = prepared['receipt']
    assert receipt['parent_release'] == RELEASE
    assert receipt['parent_activation'] == str(prepared['evidence'].resolve())
    assert receipt['parent_acceptance'] is None
    verified = E.verify_bundle(prepared['output'])
    assert verified['parent_release'] == RELEASE
    assert verified['parent_activation_sha256'] == receipt['parent_activation_sha256']


def test_bundle_refuses_activation_evidence_changed_after_prepare(prepared):
    path = prepared['evidence'] / 'readback/readback-summary.json'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='bundle qualification changed'):
        E.verify_bundle(prepared['output'])


# --------------------------------------------------------------------------- #
# Negative controls: every evidence step of the route is load-bearing.
# --------------------------------------------------------------------------- #

def test_missing_evidence_directory_is_refused(tmp_path):
    with pytest.raises(ValueError, match='activation evidence directory'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), tmp_path / 'absent')


@pytest.mark.parametrize('name', [
    'board-download/activation.jsonl',
    'board-download/acceptance-report.json',
    'board-download/post-state.json',
    'logs/unit-journal.log',
    'work/target-consistency.json',
    'work/artifact-collection.json',
    'readback/readback-summary.json',
    'readback/final-confirm.json',
    'readback/readback.log',
])
def test_missing_evidence_document_is_refused_by_name(tmp_path, name):
    directory = copy_evidence(tmp_path)
    (directory / name).unlink()
    with pytest.raises(ValueError, match=name + ' missing'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


@pytest.mark.parametrize('phase,fields,needle', [
    pytest.param('guards_passed', dict(new=OTHER),
                 'phase chain does not name this release', id='another-release'),
    pytest.param('switched', dict(current=RELEASE_PREFIX + OTHER),
                 'phase chain does not name this release', id='switched-elsewhere'),
    pytest.param('verified_new', dict(current=RELEASE_PREFIX + OTHER),
                 'phase chain does not name this release', id='verified-elsewhere'),
    pytest.param('exited', dict(rc='1'),
                 'phase chain does not name this release', id='non-zero-exit'),
    pytest.param('active_release_published', dict(tries='1'),
                 'phase chain does not name this release', id='retried-publish'),
])
def test_phase_chain_identity_or_result_mismatch_is_refused(tmp_path, phase, fields, needle):
    directory = copy_evidence(tmp_path)
    mutate_jsonl(directory, phase, **fields)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


def test_phase_chain_out_of_order_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'board-download/activation.jsonl'
    lines = [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    reordered = lines[:5] + [lines[6], lines[5]] + lines[7:]
    path.write_text(''.join(line + '\n' for line in reordered), encoding='utf-8')
    with pytest.raises(ValueError, match='phase chain incomplete or out of order'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


@pytest.mark.parametrize('mutate', [
    pytest.param(_set(('expected_release',), OTHER), id='other-expected-release'),
    pytest.param(_set(('release_path',), RELEASE_PREFIX + OTHER), id='other-release-path'),
])
def test_pre_state_must_name_the_parent_the_chain_replaced(tmp_path, mutate):
    """M14 regression: rewriting pre-state.json and rebinding its bytes is still refused.

    The artifact collection is rehashed, so the byte-binding check cannot be what refuses:
    the phase chain's previous release and the pre-state document must agree.
    """
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/pre-state.json', mutate)
    rebind_collection(directory)
    with pytest.raises(ValueError, match='pre-state does not name the parent it replaced'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


def test_missing_activation_accepted_marker_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'logs/unit-journal.log'
    path.write_text(path.read_text(encoding='utf-8').replace(
        'ACTIVATION_ACCEPTED', 'ACTIVATION_PENDING'), encoding='utf-8')
    with pytest.raises(ValueError, match='terminal marker missing or repeated'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


def test_rolled_back_activation_journal_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'logs/unit-journal.log'
    path.write_text(path.read_text(encoding='utf-8') + '\nACTIVATION_ROLLED_BACK\n',
                    encoding='utf-8')
    with pytest.raises(ValueError, match='rolled-back activation'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(_set(('ok',), False), 'acceptance report unverified', id='not-ok'),
    pytest.param(_set(('schema',), 'headroom-trial-acceptance-v0'),
                 'acceptance report unverified', id='other-schema'),
    pytest.param(_set(('expected_release',), OTHER), 'acceptance report unverified',
                 id='other-release'),
    pytest.param(_set(('state', 'release_path'), RELEASE_PREFIX + OTHER),
                 'disagrees with the release', id='other-path'),
    pytest.param(_set(('state', 'release_id_from_name'), OTHER),
                 'disagrees with the release', id='other-name'),
])
def test_tampered_acceptance_report_is_refused(tmp_path, mutate, needle):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/acceptance-report.json', mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


def test_acceptance_check_failing_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/acceptance-report.json',
                lambda document: document['checks'][0].update(ok=False))
    with pytest.raises(ValueError, match='acceptance report unverified'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


@pytest.mark.parametrize('mutate', [
    pytest.param(_set(('expected_release',), OTHER), id='expected-release'),
    pytest.param(_set(('safe',), False), id='safe-false'),
    pytest.param(_set(('release_verified',), False), id='release-unverified'),
    pytest.param(_set(('software_contract',), 'cnn-bpsk-environment-v1'), id='other-contract'),
    pytest.param(_set(('services', 'rf-daemon', 'SubState'), 'dead'), id='service-dead'),
    pytest.param(_set(('status', 'pl_status', 'rx', 'armed'), True), id='rx-armed'),
    pytest.param(_set(('status', 'pl_status', 'tx', 'source_selected'), True),
                 id='tx-selected'),
])
def test_post_state_that_is_not_verified_safe_is_refused(tmp_path, mutate):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/post-state.json', mutate)
    with pytest.raises(ValueError, match='post-state'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


@pytest.mark.parametrize('mutate', [
    pytest.param(_set(('consistent',), False), id='not-consistent'),
    pytest.param(_set(('digest_differences',), [{'name': 'rf_headroom.py'}]),
                 id='digest-difference'),
    pytest.param(_set(('missing_on_board',), ['rf_headroom.py']), id='missing-member'),
    pytest.param(_set(('extra_on_board',), ['rf_extra.py']), id='extra-member'),
    pytest.param(_set(('board_files',), 52), id='member-count'),
    pytest.param(_set(('board_has_pycache_dir',), True), id='pycache'),
])
def test_release_tree_difference_is_refused(tmp_path, mutate):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'work/target-consistency.json', mutate)
    with pytest.raises(ValueError, match='release tree is not identical'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(_set(('ok',), False), 'independent read-back unverified', id='not-ok'),
    pytest.param(_set(('candidate',), OTHER), 'independent read-back unverified',
                 id='other-candidate'),
    pytest.param(_set(('failed',), ['pl_io_not_ok']), 'independent read-back unverified',
                 id='failed-check'),
])
def test_readback_that_is_not_this_release_is_refused(tmp_path, mutate, needle):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'readback/readback-summary.json', mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


def _forge_state(document):
    document['state']['rf_headroom'] = 'f' * 64
    document['state']['rf_headroom_replay'] = 'f' * 64
    document['state']['daemon_sha256'] = 'f' * 64


def _forge_reported(document):
    for check in document['checks']:
        if check['name'] in ('new_headroom_digest', 'new_replay_digest',
                             'new_daemon_digest'):
            check['detail'] = {'board': 'f' * 64, 'manifest': 'f' * 64}


def _forge_paths(document):
    document['paths']['NEW_HEADROOM'] = 'f' * 64
    document['paths']['NEW_REPLAY'] = 'f' * 64
    document['paths']['NEW_DAEMON'] = 'f' * 64


@pytest.mark.parametrize('family,forge,needle', [
    pytest.param('state', _forge_state, 'read-back disagrees with the verified release',
                 id='state-only'),
    pytest.param('reported', _forge_reported,
                 'read-back disagrees with the verified release', id='reported-only'),
    pytest.param('paths', _forge_paths, 'read-back paths disagree with the release',
                 id='paths-only'),
])
def test_readback_digest_family_forgery_alone_is_refused(tmp_path, family, forge, needle):
    """Each digest family is load-bearing on its own, not only together with paths.

    Exactly one family is forged and the artifact collection is then rehashed, so the
    byte binding cannot be what refuses; the named comparison of that family must be.
    The record is compared before/after so a silent no-op forging cannot pass.
    """
    directory = copy_evidence(tmp_path)
    path = directory / 'readback/readback-summary.json'
    before = json.dumps(_load(path), sort_keys=True)
    mutate_json(directory, 'readback/readback-summary.json', forge)
    assert json.dumps(_load(path), sort_keys=True) != before, (
        'the %s forging did not change the read-back document' % family)
    rebind_collection(directory)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


def test_self_consistent_relabelling_onto_another_release_is_refused(tmp_path):
    """Every id inside the evidence now says b82 and every digest was rehashed."""
    directory = copy_evidence(tmp_path)
    relabel_to_other(directory)
    with pytest.raises(ValueError, match='disagrees with the release'):
        D.verify_installed_parent_via_activation_evidence(parent_of(OTHER_CANDIDATE),
                                                          directory)


def test_relabelled_evidence_still_cannot_stand_in_for_its_own_release(tmp_path):
    directory = copy_evidence(tmp_path)
    relabel_to_other(directory)
    with pytest.raises(ValueError, match='activation evidence'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


def test_tampered_artifact_that_was_not_rebound_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'board-download/post-state.json'
    document = _load(path)
    document['free_bytes'] = 1
    _write(path, document)
    with pytest.raises(ValueError, match='artifact digest differs: post-state.json'):
        D.verify_installed_parent_via_activation_evidence(parent_of(), directory)


def test_environment_gate_refuses_a_tampered_activation_parent(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_jsonl(directory, 'switched', current=RELEASE_PREFIX + OTHER)
    with pytest.raises(ValueError, match='activation evidence'):
        E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None, None, directory)
