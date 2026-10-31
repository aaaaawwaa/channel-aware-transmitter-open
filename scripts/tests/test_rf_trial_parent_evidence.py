"""The fifth rollback-parent route: a reviewed non-emitting trial deployment.

e4dae554 is the board's current release and it was installed by the 2026-10-17 trial
transaction -- the reviewed installer, a bounded on-board acceptance and an
independent read-only read-back -- so it has no INSTALLED_VERIFIED receipt, and the
trial documents must not be renamed into one.
verify_installed_parent_via_trial_acceptance_evidence rebuilds that release's own
chain (one TRIAL_ACCEPTED, the six acceptance checks, pre-/post-state, the staged
payload, the independent read-back, the downloaded board bytes and the receipt that
binds them) and binds every identity and digest to the candidate directory
R.verify_release has just verified.

These tests prove the real chain is accepted, that both deployment gates can use it,
that it cannot stand in for an INSTALLED_VERIFIED receipt, and that each evidence
step -- uniqueness of the terminal marker, identity, digests, the independent
read-back, the staged bytes and a self-consistent forgery -- is load-bearing.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import merged_release as R          # noqa: E402
from scripts import rf_environment_deploy as E   # noqa: E402
from scripts import rf_headroom_deploy as D      # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RELEASE = 'e4dae554182e8c9f5f963c1d5040913a'
CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-m2-bootstrap-2026-10-17/candidate/'
             'releases' / RELEASE)
EVIDENCE = ROOT / D.TRIAL_EVIDENCE
TRIAL_PARENT = 'e66197ed79131b73aa4f47804487c053'
ACCEPTED = 'TRIAL_ACCEPTED release=' + RELEASE

# A release this trial never installed: an environment candidate that was never
# deployed.  The trial evidence must refuse it instead of standing in for a receipt.
OTHER = 'ad05576aff6b9bf2b6b495347a6269ec'
OTHER_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-candidate-b-2026-10-17/candidate/'
                   'releases' / OTHER)
# A release that was switched to current by the activation job, not by this trial.
ACTIVATED = TRIAL_PARENT
ACTIVATED_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-headroom-candidate2-2026-10-17/'
                       'candidate/releases' / ACTIVATED)
ACTIVATION_EVIDENCE = ROOT / D.ACTIVATION_EVIDENCE

# Headroom gate inputs for the release that route already knows how to install.
HEADROOM = 'b82b141e355b4cb1833969383a409c79'
HEADROOM_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                      'candidate-final/releases' / HEADROOM)
HEADROOM_ARM = ROOT / ('evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                       'arm-runtime/receipt.json')
HEADROOM_CHECKPOINT = ROOT / ('evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                              'development-checkpoint.json')

# Environment gate inputs.
ENV_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-candidate-environment-2026-10-15/'
                 'candidate/releases/399b300e59b8d00e41d43c8123884c7f')
ENV_ARM = ROOT / 'evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json'
ENV_CLOSURE = ROOT / 'evidence/rf-teaching/s1/closure-2026-10-04/receipt.json'


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def parent_of(candidate=CANDIDATE):
    return R.verify_release(Path(candidate), require_software=True)


def copy_evidence(tmp_path):
    """The whole trial evidence directory, byte for byte.

    The receipt binds every artifact it lists by sha256, not only the ones this route
    reads, so a partial copy would be refused by the byte binding rather than by the
    check under test.
    """
    directory = Path(tmp_path) / 'trial'
    shutil.copytree(EVIDENCE, directory)
    return directory


def mutate_json(directory, relative, mutate):
    path = Path(directory) / relative
    before = path.read_bytes()
    document = _load(path)
    mutate(document)
    _write(path, document)
    assert path.read_bytes() != before, 'the mutation changed nothing in ' + relative


def mutate_text(directory, relative, mutate):
    path = Path(directory) / relative
    before = path.read_bytes()
    path.write_text(mutate(path.read_text(encoding='utf-8')), encoding='utf-8')
    assert path.read_bytes() != before, 'the mutation changed nothing in ' + relative


def _set(path, value):
    def mutate(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def _evidence_names(directory):
    return sorted(path.relative_to(directory).as_posix()
                  for path in Path(directory).rglob('*') if path.is_file())


def rebind_receipt_map(directory):
    """Rehash only the receipt's own artifact map, leaving every document alone.

    A forger that edits one document has to do at least this much; the tables inside
    the download record are separate evidence and are deliberately not repaired here.
    """
    directory = Path(directory)
    receipt_path = directory / 'receipt.json'
    receipt = _load(receipt_path)
    receipt['evidence_sha256'] = {name: D.sha(directory / name)
                                  for name in _evidence_names(directory)
                                  if name != 'receipt.json'}
    _write(receipt_path, receipt)


def rebind_maps(directory):
    """Rehash what a forger would rehash: the download record and the receipt map."""
    directory = Path(directory)
    downloads = _load(directory / 'readback/board-download-manifest.json')
    for name in D.TRIAL_BOARD_DOWNLOADS:
        path = directory / 'board-download' / name
        downloads['downloaded'][name] = dict(sha256=D.sha(path), bytes=path.stat().st_size,
                                             verified=True)
        downloads['board_listing'][name] = D.sha(path)
    _write(directory / 'readback/board-download-manifest.json', downloads)
    receipt_path = directory / 'receipt.json'
    receipt = _load(receipt_path)
    receipt['evidence_sha256'] = {name: D.sha(directory / name)
                                  for name in _evidence_names(directory)
                                  if name != 'receipt.json'}
    _write(receipt_path, receipt)


def sync_receipt(directory):
    """Make the receipt's own summaries agree with the edited reports.

    A self-consistent rewrite edits both the report and every document that restates
    it, then rehashes the maps.  The route then has to refuse on the comparison with
    the verified candidate rather than on a stale digest.
    """
    directory = Path(directory)
    acceptance = _load(directory / 'board-download/acceptance-report.json')
    details = {item.get('name'): (item.get('detail') or {})
               for item in (acceptance.get('checks') or [])}
    identity = details.get('a_release_identity') or {}
    baseline = details.get('c_baseline_load_not_formal') or {}
    readback = _load(directory / 'logs/readback-summary.json')
    final = _load(directory / 'logs/final-summary.json')
    state = readback.get('state') or {}
    status = state.get('status') or {}
    pl_status = status.get('pl_status') or {}
    mounts = {}
    for line in readback.get('mounts') or []:
        parts = line.split()
        if len(parts) >= 4 and parts[1] in ('/', '/boot'):
            mounts['root' if parts[1] == '/' else 'boot'] = 'rw' in parts[3].split(',')
    receipt_path = directory / 'receipt.json'
    receipt = _load(receipt_path)
    receipt['acceptance'] = dict(
        receipt['acceptance'], ok=acceptance.get('ok'),
        checks_passed=sum(1 for item in (acceptance.get('checks') or [])
                          if item.get('ok') is True),
        expected_release=acceptance.get('expected_release'),
        software_modules=identity.get('software_modules'),
        software_digest=identity.get('software_digest'),
        domain=details.get('b_domain_constants', {}).get('constants'),
        services=details.get('d_services'),
        baseline={key: baseline.get(key) for key in
                  ('file_sha256', 'canonical_sha256', 'calibration_status', 'sources',
                   'formal_acceptance_baseline')})
    receipt['readback'] = dict(
        receipt['readback'], passed=readback.get('passed'),
        final_after_cleanup_passed=final.get('passed'),
        release_id=state.get('release_id'), software_modules=state.get('software_modules'),
        software_digest=state.get('software_digest'), boot_id=state.get('boot_id'),
        checks=readback.get('checks'), storage_faults=readback.get('kernel_storage_faults'),
        mounts_rw=mounts, services=state.get('services'),
        rf_closed=dict(armed=status.get('armed'), io_ok=pl_status.get('io_ok'),
                       tx_source_selected=(pl_status.get('tx') or {}).get('source_selected'),
                       rx_armed=(pl_status.get('rx') or {}).get('armed')))
    _write(receipt_path, receipt)
    rebind_maps(directory)


# --------------------------------------------------------------------------- #
# The real chain, both gates that consume it, and the CLI.
# --------------------------------------------------------------------------- #

def test_the_real_trial_chain_is_accepted_as_the_installed_parent():
    proof = D.verify_installed_parent_via_trial_acceptance_evidence(
        parent_of(), EVIDENCE, CANDIDATE)
    assert proof['release_id'] == RELEASE
    assert proof['source'] == 'trial-acceptance-evidence'
    assert proof['source'] != D.INSTALLER_RECEIPT_OUTCOME
    assert proof['software_contract'] == 'cnn-bpsk-environment-v1'
    assert proof['software_modules'] == 44
    assert proof['trial_parent'] == TRIAL_PARENT
    assert set(proof['evidence_sha256']) == set(D.TRIAL_FILES)
    assert proof['evidence_sha256']['logs/dispatch-journal.log'] == D.sha(
        EVIDENCE / 'logs/dispatch-journal.log')
    assert proof['evidence_sha256']['board-download/acceptance-report.json'] == D.sha(
        EVIDENCE / 'board-download/acceptance-report.json')


def test_the_route_never_writes_or_renames_a_receipt():
    before = {name: D.sha(EVIDENCE / name) for name in _evidence_names(EVIDENCE)}
    D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), EVIDENCE, CANDIDATE)
    after = {name: D.sha(EVIDENCE / name) for name in _evidence_names(EVIDENCE)}
    assert before == after
    assert 'INSTALLED_VERIFIED' not in Path(
        EVIDENCE / 'receipt.json').read_text(encoding='utf-8')
    assert 'INSTALLED_VERIFIED' not in Path(
        EVIDENCE / 'board-download/acceptance-report.json').read_text(encoding='utf-8')


def test_headroom_gate_binds_the_trial_parent_without_an_installer_receipt():
    gate = D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                          HEADROOM_CHECKPOINT, None, EVIDENCE)
    assert gate['release_id'] == HEADROOM
    assert gate['parent_release'] == RELEASE
    assert gate['parent_trial_acceptance'] == str(EVIDENCE.resolve())
    assert gate['parent_trial_parent'] == TRIAL_PARENT
    assert gate['parent_trial_acceptance_sha256'] == {
        name: D.sha(EVIDENCE / name) for name in D.TRIAL_FILES}
    assert 'parent_install_sha256' not in gate
    assert 'parent_activation_sha256' not in gate


def test_environment_gate_binds_the_trial_parent():
    gate = E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None, None, None,
                          EVIDENCE)
    assert gate['release_id'] == '399b300e59b8d00e41d43c8123884c7f'
    assert gate['parent_release'] == RELEASE
    assert gate['contract'] == E.CONTRACT
    assert gate['parent_trial_acceptance'] == str(EVIDENCE.resolve())
    assert set(gate['parent_trial_acceptance_sha256']) == set(D.TRIAL_FILES)
    assert 'parent_activation' not in gate


def test_the_trial_and_activation_proofs_cannot_be_combined():
    with pytest.raises(ValueError, match='one S2 rollback parent proof'):
        E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None, None,
                       ACTIVATION_EVIDENCE, EVIDENCE)
    with pytest.raises(ValueError, match='one rollback-parent proof at a time'):
        D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                       HEADROOM_CHECKPOINT, ACTIVATION_EVIDENCE, EVIDENCE)


def test_environment_gate_refuses_a_tampered_trial_parent(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_text(directory, 'logs/dispatch-journal.log',
                lambda text: text.replace(ACCEPTED, 'TRIAL_PENDING'))
    with pytest.raises(ValueError, match='trial acceptance evidence'):
        E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None, None, None,
                       directory)


@pytest.fixture
def prepared(tmp_path):
    directory = copy_evidence(tmp_path / 'evidence')
    output = tmp_path / 'bundle'
    receipt = E.prepare(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, output, CANDIDATE, None,
                        None, None, directory)
    return dict(output=output, receipt=receipt, evidence=directory)


def test_prepare_and_verify_bundle_round_trip_on_the_trial_parent(prepared):
    receipt = prepared['receipt']
    assert receipt['parent_release'] == RELEASE
    assert receipt['parent_trial_acceptance'] == str(prepared['evidence'].resolve())
    assert receipt['parent_trial_parent'] == TRIAL_PARENT
    assert receipt['parent_install'] is None and receipt['parent_activation'] is None
    verified = E.verify_bundle(prepared['output'])
    assert verified['parent_release'] == RELEASE
    assert verified['parent_trial_acceptance_sha256'] == receipt[
        'parent_trial_acceptance_sha256']


def test_bundle_refuses_trial_evidence_changed_after_prepare(prepared):
    """One changed byte in the evidence: refused either by the byte binding or by the
    gate comparison, never accepted."""
    path = prepared['evidence'] / 'logs/readback-summary.json'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='artifact digest differs|bundle qualification changed'):
        E.verify_bundle(prepared['output'])


def test_the_cli_refuses_to_guess_between_parent_proofs(tmp_path):
    base = [sys.executable, str(ROOT / 'scripts/rf_environment_deploy.py'), 'prepare',
            '--candidate', str(ENV_CANDIDATE), '--closure', str(ENV_CLOSURE),
            '--arm', str(ENV_ARM), '--parent-candidate', str(CANDIDATE),
            '--parent-trial-acceptance', str(EVIDENCE),
            '--output', str(tmp_path / 'bundle')]
    both = base + ['--parent-activation', str(ACTIVATION_EVIDENCE)]
    completed = subprocess.run(both, capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert 'at most one rollback-parent proof' in completed.stderr
    with_acceptance = base + ['--parent-acceptance', str(ROOT / D.ACCEPTANCE_EVIDENCE)]
    completed = subprocess.run(with_acceptance, capture_output=True, text=True, check=False)
    assert completed.returncode == 1
    assert 'one S2 rollback parent proof at a time' in (completed.stdout + completed.stderr)


def test_the_headroom_cli_accepts_the_new_switch_and_refuses_combinations(tmp_path):
    output = tmp_path / 'bundle'
    none_given = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(HEADROOM_CANDIDATE), '--arm', str(HEADROOM_ARM),
         '--parent-candidate', str(CANDIDATE), '--checkpoint', str(HEADROOM_CHECKPOINT),
         '--output', str(output)], capture_output=True, text=True, check=False)
    assert none_given.returncode == 2
    assert 'exactly one rollback-parent proof' in none_given.stderr
    two_given = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(HEADROOM_CANDIDATE), '--arm', str(HEADROOM_ARM),
         '--parent-candidate', str(CANDIDATE), '--checkpoint', str(HEADROOM_CHECKPOINT),
         '--parent-trial-acceptance', str(EVIDENCE),
         '--parent-activation', str(ACTIVATION_EVIDENCE),
         '--output', str(output)], capture_output=True, text=True, check=False)
    assert two_given.returncode == 2
    assert 'exactly one rollback-parent proof' in two_given.stderr
    assert not output.exists()
    one_given = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(HEADROOM_CANDIDATE), '--arm', str(HEADROOM_ARM),
         '--parent-candidate', str(CANDIDATE), '--checkpoint', str(HEADROOM_CHECKPOINT),
         '--parent-trial-acceptance', str(EVIDENCE),
         '--output', str(output)], capture_output=True, text=True, check=False)
    assert one_given.returncode == 0, one_given.stdout + one_given.stderr
    receipt = _load(output / 'bundle-receipt.json')
    assert receipt['parent_release'] == RELEASE
    assert receipt['parent_trial_acceptance'] == str(EVIDENCE.resolve())
    assert D.verify_bundle(output)['parent_release'] == RELEASE


# --------------------------------------------------------------------------- #
# It can never stand in for an INSTALLED_VERIFIED receipt.
# --------------------------------------------------------------------------- #

def test_the_installer_route_still_needs_its_receipt():
    with pytest.raises(ValueError, match='installation receipt'):
        D.verify_parent(CANDIDATE, None)
    # Naming the trial evidence through the installer route changes nothing: the
    # trial record is not a receipt and is not read there.
    with pytest.raises(ValueError, match='installation receipt'):
        D.verify_parent(CANDIDATE, None, activation_evidence=None)


def test_a_release_the_trial_never_installed_is_refused():
    with pytest.raises(ValueError, match='trial acceptance evidence'):
        D.verify_installed_parent_via_trial_acceptance_evidence(
            parent_of(OTHER_CANDIDATE), EVIDENCE, OTHER_CANDIDATE)


def test_a_release_another_route_installed_is_not_a_trial_parent():
    with pytest.raises(ValueError, match='trial acceptance evidence'):
        D.verify_installed_parent_via_trial_acceptance_evidence(
            parent_of(ACTIVATED_CANDIDATE), EVIDENCE, ACTIVATED_CANDIDATE)


def test_the_route_needs_its_candidate_directory():
    with pytest.raises(ValueError, match='requires the parent candidate'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), EVIDENCE)


# --------------------------------------------------------------------------- #
# Negative controls: every evidence step of the route is load-bearing.
# --------------------------------------------------------------------------- #

def test_missing_evidence_directory_is_refused(tmp_path):
    with pytest.raises(ValueError, match='trial acceptance evidence directory'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), tmp_path / 'absent',
                                                                CANDIDATE)


@pytest.mark.parametrize('name', [
    'trial.json',
    'receipt.json',
    'dispatch-intent.json',
    'cleanup.json',
    'logs/dispatch-journal.log',
    'logs/dispatch-summary.json',
    'logs/dispatch.result.json',
    'logs/readback-summary.json',
    'logs/final-summary.json',
    'board-download/acceptance-report.json',
    'board-download/pre-state.json',
    'board-download/post-state.json',
    'board-download/trial-state.json',
    'readback/board-download-manifest.json',
    'stage/stage-local-sha256.json',
    'stage/staged-sha256.json',
    'prepare/candidate.tgz',
])
def test_missing_evidence_document_is_refused_by_name(tmp_path, name):
    directory = copy_evidence(tmp_path)
    (directory / name).unlink()
    with pytest.raises(ValueError, match=name.split('/')[-1] + r' missing'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(_set(('release_id',), OTHER),
                 'intent does not name this release', id='other-release'),
    pytest.param(_set(('parent_release',), RELEASE),
                 'intent does not name this release', id='parent-is-the-release'),
    pytest.param(_set(('acceptance_budget_seconds',), 0),
                 'intent does not name this release', id='zero-budget'),
    pytest.param(_set(('unit',), 'rf-trial-other'),
                 'intent does not name this release', id='other-unit'),
    pytest.param(_set(('archive_sha',), 'f' * 64),
                 'intent does not name this release', id='other-archive'),
    pytest.param(_set(('reviewed', 'acceptance_sha256'), 'f' * 64),
                 'intent does not name this release', id='other-acceptance-script'),
])
def test_trial_intent_not_naming_this_release_is_refused(tmp_path, mutate, needle):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'trial.json', mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(lambda text: text.replace(ACCEPTED, 'TRIAL_PENDING'),
                 'terminal marker missing, repeated or not this release', id='renamed'),
    pytest.param(lambda text: text + ACCEPTED + '\n',
                 'terminal marker missing, repeated or not this release', id='repeated'),
    pytest.param(lambda text: text + 'TRIAL_ACCEPTED release=' + OTHER + '\n',
                 'terminal marker missing, repeated or not this release', id='other-release'),
    pytest.param(lambda text: text.replace(ACCEPTED, 'TRIAL_ACCEPTED release=' + OTHER),
                 'terminal marker missing, repeated or not this release',
                 id='other-release-only'),
    pytest.param(lambda text: text.replace('INSTALL_ACCEPTED', 'INSTALL_PENDING'),
                 'terminal marker missing, repeated or not this release', id='no-install'),
    pytest.param(lambda text: text.replace('TRIAL_ACCEPTANCE PASS', 'TRIAL_ACCEPTANCE FAIL'),
                 'terminal marker missing, repeated or not this release', id='no-acceptance'),
    pytest.param(lambda text: text.replace('TRIAL_CHECK_BEGIN seconds=120',
                                           'TRIAL_CHECK_BEGIN seconds=300'),
                 'bounded acceptance budget changed', id='other-budget'),
])
def test_journal_that_is_not_one_acceptance_of_this_release_is_refused(tmp_path, mutate, needle):
    directory = copy_evidence(tmp_path)
    mutate_text(directory, 'logs/dispatch-journal.log', mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('marker', ['TRIAL_FAILED exit=7', 'TRIAL_ROLLED_BACK parent=' + OTHER,
                                    'TRIAL_RECOVERY_REQUIRED parent=' + OTHER])
def test_a_failed_rolled_back_or_recovered_trial_is_refused(tmp_path, marker):
    directory = copy_evidence(tmp_path)
    mutate_text(directory, 'logs/dispatch-journal.log', lambda text: text + marker + '\n')
    with pytest.raises(ValueError, match='records a failed or rolled-back trial'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_a_repeated_dispatch_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'logs/dispatch.result.json', _set(('exit_code',), 1))
    mutate_json(directory, 'logs/dispatch-summary.json', _set(('dispatch', 'exit_code'), 1))
    with pytest.raises(ValueError, match='did not succeed exactly once'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)
    directory = copy_evidence(tmp_path / 'second')
    mutate_json(directory, 'logs/dispatch-summary.json', _set(('unit',), 'Result=failed'))
    with pytest.raises(ValueError, match='did not succeed exactly once'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)
    directory = copy_evidence(tmp_path / 'third')
    mutate_json(directory, 'dispatch-intent.json', _set(('policy',), 'retry until accepted'))
    with pytest.raises(ValueError, match='dispatch intent unverified'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(_set(('ok',), False), 'acceptance report unverified', id='not-ok'),
    pytest.param(_set(('schema',), 'environment-trial-acceptance-v0'),
                 'acceptance report unverified', id='other-schema'),
    pytest.param(_set(('expected_release',), OTHER), 'acceptance report unverified',
                 id='other-expected-release'),
    pytest.param(_set(('checks',), []), 'acceptance report unverified', id='no-checks'),
    pytest.param(_set(('state', 'release_id_from_name'), OTHER),
                 'acceptance state does not name this release', id='other-name'),
    pytest.param(_set(('state', 'release_path'), D.CURRENT_RELEASE_PATH + '/other'),
                 'acceptance state does not name this release', id='other-path'),
])
def test_tampered_acceptance_report_is_refused(tmp_path, mutate, needle):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/acceptance-report.json', mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_one_failing_acceptance_check_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/acceptance-report.json',
                lambda document: document['checks'][5].update(ok=False))
    with pytest.raises(ValueError, match='acceptance report unverified'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)
    directory = copy_evidence(tmp_path / 'renamed')
    mutate_json(directory, 'board-download/acceptance-report.json',
                lambda document: document['checks'][2].update(name='c_something_else'))
    with pytest.raises(ValueError, match='acceptance checks incomplete'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def _check_detail(name, *keys, value='f' * 64):
    """Rewrite one leaf of one named acceptance check."""
    def mutate(report):
        for item in report['checks']:
            if item['name'] == name:
                node = item['detail']
                for key in keys[:-1]:
                    node = node[key]
                node[keys[-1]] = value
    return mutate


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(_check_detail('a_release_identity', 'software_digest'),
                 'acceptance report disagrees with the release', id='software-digest'),
    pytest.param(_check_detail('a_release_identity', 'software_modules'),
                 'acceptance report disagrees with the release', id='software-modules'),
    pytest.param(_check_detail('a_release_identity', 'build_id'),
                 'acceptance report disagrees with the release', id='build-id'),
    pytest.param(_check_detail('a_release_identity', 'daemon_version'),
                 'acceptance report disagrees with the release', id='daemon-version'),
    pytest.param(_check_detail('a_release_identity', 'protocol_version'),
                 'acceptance report disagrees with the release', id='protocol-version'),
    pytest.param(_check_detail('a_release_identity', 'release_verified', value=False),
                 'acceptance report disagrees with the release', id='not-verified'),
    pytest.param(_check_detail('b_domain_constants', 'constants',
                                'rf_environment_VERSION'),
                 'domain constants disagree with the release', id='environment-version'),
    pytest.param(_check_detail('b_domain_constants', 'rf_environment', 'sha256'),
                 'domain module is not the release module', id='module-digest'),
    pytest.param(_check_detail('c_baseline_load_not_formal', 'file_sha256'),
                 'baseline record disagrees with the release', id='baseline-file'),
    pytest.param(_check_detail('c_baseline_load_not_formal', 'canonical_sha256'),
                 'baseline record disagrees with the release', id='baseline-canonical'),
    pytest.param(_check_detail('c_baseline_load_not_formal', 'loaded', value=False),
                 'baseline record disagrees with the release', id='baseline-not-loaded'),
    pytest.param(_check_detail('d_services', 'rf-daemon', value=0),
                 'acceptance services are not running', id='services'),
    pytest.param(_check_detail('e_rf_closed', 'armed', value=0),
                 'acceptance RF state is not closed', id='armed'),
    pytest.param(_check_detail('e_rf_closed', 'active_release', value='/other'),
                 'acceptance RF state is not closed', id='active-release'),
    pytest.param(_check_detail('f_archived_iq_smoke', 'frame', 'iq_sha256'),
                 'archived-frame smoke test unverified', id='frame-digest'),
    pytest.param(_check_detail('f_archived_iq_smoke', 'frame', 'bytes', value=1),
                 'archived-frame smoke test unverified', id='frame-bytes'),
    pytest.param(_check_detail('f_archived_iq_smoke', 'replay_helper', 'sha256'),
                 'archived-frame smoke test unverified', id='replay-helper'),
])
def test_acceptance_detail_that_contradicts_the_release_is_refused(tmp_path, mutate, needle):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/acceptance-report.json', mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def _mutate_frame_demod(document):
    for item in document['checks']:
        if item['name'] == 'f_archived_iq_smoke':
            item['detail']['demod']['bit_errors'] = 3


def _mutate_constants(document):
    for item in document['checks']:
        if item['name'] == 'b_domain_constants':
            item['detail']['constants']['ui_model_ATTENUATION_MDB'] = 40000


def _mutate_environment_module(document):
    for item in document['checks']:
        if item['name'] == 'b_domain_constants':
            item['detail']['rf_environment']['sha256'] = 'f' * 64


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(_mutate_frame_demod, 'archived-frame smoke test unverified',
                 id='frame-bit-errors'),
    pytest.param(_mutate_constants, 'domain constants disagree with the release',
                 id='attenuation'),
    pytest.param(_mutate_environment_module,
                 'domain module is not the release module', id='module-digest'),
])
def test_semantic_acceptance_edits_are_refused(tmp_path, mutate, needle):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/acceptance-report.json', mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('mutate', [
    pytest.param(_set(('safe',), False), id='safe-false'),
    pytest.param(_set(('release_verified',), False), id='release-unverified'),
    pytest.param(_set(('status', 'armed'), True), id='armed'),
    pytest.param(_set(('status', 'pl_status', 'tx', 'source_selected'), True), id='tx'),
    pytest.param(_set(('active_release',), D.RELEASE_PATH_PREFIX + OTHER), id='active-elsewhere'),
])
def test_pre_state_that_is_not_verified_safe_is_refused(tmp_path, mutate):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/pre-state.json', mutate)
    with pytest.raises(ValueError, match='pre-state'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('relative,mutate,needle', [
    pytest.param('board-download/post-state.json', _set(('safe',), False), 'post-state',
                 id='post-safe'),
    pytest.param('board-download/post-state.json',
                 _set(('expected_release',), OTHER), 'post-state', id='post-release'),
    pytest.param('board-download/post-state.json',
                 _set(('services', 'rf-daemon', 'SubState'), 'dead'), 'post-state',
                 id='post-service'),
    pytest.param('board-download/trial-state.json', _set(('expected_release',), OTHER),
                 'board trial state', id='trial-state-release'),
    pytest.param('board-download/trial-state.json', _set(('safe',), False),
                 'board trial state', id='trial-state-safe'),
])
def test_post_or_trial_state_that_is_not_verified_safe_is_refused(tmp_path, relative, mutate,
                                                                  needle):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, relative, mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(_set(('passed',), False), 'independent read-back unverified', id='not-passed'),
    pytest.param(_set(('checks', 'snapshot_valid'), False),
                 'independent read-back unverified', id='one-check-failed'),
    pytest.param(_set(('kernel_storage_faults',), ['mmc0: error']),
                 'independent read-back unverified', id='storage-fault'),
    pytest.param(_set(('board_contact',), 'read-write'),
                 'independent read-back unverified', id='not-read-only'),
    pytest.param(_set(('expected_release',), OTHER), 'independent read-back unverified',
                 id='other-release'),
    pytest.param(_set(('snapshot', 'current_release'), OTHER),
                 'release or boot identity changed', id='current-elsewhere'),
    pytest.param(_set(('snapshot', 'boot_id'), '00000000-0000-0000-0000-000000000000'),
                 'release or boot identity changed', id='other-boot'),
    pytest.param(_set(('state', 'software_contract'), 'cnn-bpsk-headroom-v1'),
                 'disagrees with the verified release', id='other-contract'),
    pytest.param(_set(('state', 'software_modules'), 46),
                 'disagrees with the verified release', id='other-modules'),
    pytest.param(_set(('state', 'software_digest'), 'f' * 64),
                 'disagrees with the verified release', id='forged-digest'),
    pytest.param(_set(('state', 'build_id'), 'other-build'),
                 'disagrees with the verified release', id='other-build'),
    pytest.param(_set(('state', 'services', 'rf-daemon', 'NRestarts'), '1'),
                 'disagrees with the verified release', id='restarted-service'),
    pytest.param(_set(('state', 'status', 'armed'), True),
                 'disagrees with the verified release', id='armed'),
    pytest.param(_set(('state', 'baseline', 'canonical_sha256'), 'f' * 64),
                 'baseline disagrees with the release', id='baseline'),
    pytest.param(_set(('state', 'domain', 'env_FIXED_RF'), 40000),
                 'domain disagrees with the release', id='domain'),
    pytest.param(_set(('state', 'domain', 'demod_VERSION'), 'other-demod'),
                 'domain disagrees with the release', id='demod'),
])
def test_readback_that_disagrees_with_the_release_is_refused(tmp_path, mutate, needle):
    """Rewriting the read-back and re-syncing the receipt still refuses."""
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'logs/readback-summary.json', mutate)
    sync_receipt(directory)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_final_readback_that_disagrees_with_the_release_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'logs/final-summary.json', _set(('passed',), False))
    mutate_json(directory, 'logs/final-summary.json', _set(('checks', 'storage_clean'), False))
    sync_receipt(directory)
    with pytest.raises(ValueError, match='final read-back unverified'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_final_readback_state_that_disagrees_with_the_release_is_refused(tmp_path):
    """The second, post-cleanup read-back is compared to the candidate on its own.

    The receipt's byte binding is rehashed and no other document restates the final
    read-back's state, so the named refusal can only come from this route's own
    comparison of that summary.
    """
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'logs/final-summary.json',
                _set(('state', 'software_digest'), 'f' * 64))
    rebind_maps(directory)
    with pytest.raises(ValueError, match='final read-back disagrees with the verified release'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_readback_with_forged_manifest_bytes_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'logs/readback-summary.json',
                _set(('paths',), _load(directory / 'logs/readback-summary.json')['paths']
                     .replace(D.sha(CANDIDATE / 'merged_release_manifest.json'), 'f' * 64)))
    sync_receipt(directory)
    with pytest.raises(ValueError, match='release tree is not the candidate tree'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_board_download_not_rebound_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/post-state.json', _set(('free_bytes',), 1))
    with pytest.raises(ValueError, match='board download differs: post-state.json'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_staged_payload_that_changed_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'stage/acceptance.py'
    path.write_bytes(path.read_bytes() + b'\n# tampered\n')
    with pytest.raises(ValueError, match='stage does not match its local map'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)
    # Rehashing the local map as well still refuses: the board's own re-read of the
    # staged tree is a second, independent copy of the same digests.
    directory = copy_evidence(tmp_path / 'rebound')
    path = directory / 'stage/acceptance.py'
    path.write_bytes(path.read_bytes() + b'\n# tampered\n')
    _write(directory / 'stage/stage-local-sha256.json',
           {name: D.sha(directory / 'stage' / name) for name in D.TRIAL_STAGE_FILES})
    rebind_maps(directory)
    with pytest.raises(ValueError, match='board stage differs from the local bytes'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('name', ['install.sh', 'candidate.tgz', 'acceptance.py'])
def test_board_listing_that_disagrees_with_the_staged_bytes_is_refused(tmp_path, name):
    """The board's own listing of the staged tree is verified, not trusted.

    Only the receipt's artifact map is rebound, so nothing else can be what refuses:
    the local staging map, the board re-read map and the downloaded copies all still
    carry the true digest.
    """
    directory = copy_evidence(tmp_path)
    downloads = _load(directory / 'readback/board-download-manifest.json')
    before = json.dumps(downloads['board_listing'][name], sort_keys=True)
    downloads['board_listing'][name] = 'f' * 64
    assert json.dumps(downloads['board_listing'][name], sort_keys=True) != before
    _write(directory / 'readback/board-download-manifest.json', downloads)
    rebind_receipt_map(directory)
    with pytest.raises(ValueError, match='board stage bytes differ: ' + name):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_prepared_payload_that_differs_from_the_stage_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'prepare/candidate.tgz'
    path.write_bytes(path.read_bytes() + b'\x00')
    with pytest.raises(ValueError, match='prepared payload differs from the stage'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


@pytest.mark.parametrize('mutate,needle', [
    pytest.param(_set(('rollback', 'triggered'), True),
                 'receipt unverified', id='rollback-triggered'),
    pytest.param(_set(('new_physical_frames',), 1),
                 'receipt unverified', id='physical-frame'),
    pytest.param(_set(('parent_unchanged',), False), 'receipt unverified',
                 id='parent-changed'),
    pytest.param(_set(('terminal_marker',), 'TRIAL_PENDING'), 'receipt unverified',
                 id='no-terminal'),
    pytest.param(_set(('dispatch', 'unit_result'), 'failed'), 'receipt unverified',
                 id='unit-failed'),
])
def test_receipt_that_denies_the_acceptance_is_refused(tmp_path, mutate, needle):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'receipt.json', mutate)
    with pytest.raises(ValueError, match=needle):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_receipt_that_does_not_bind_its_artifacts_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'receipt.json',
                lambda document: document['evidence_sha256'].pop(
                    'board-download/acceptance-report.json'))
    with pytest.raises(ValueError, match='does not bind its artifacts'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)
    directory = copy_evidence(tmp_path / 'stale')
    mutate_json(directory, 'receipt.json',
                lambda document: document['evidence_sha256'].update(
                    {'board-download/acceptance-report.json': 'f' * 64}))
    with pytest.raises(ValueError, match='artifact digest differs'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_a_rollback_artifact_on_the_board_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    downloads = _load(directory / 'readback/board-download-manifest.json')
    downloads['board_listing']['trial-rollback-state.json'] = 'f' * 64
    _write(directory / 'readback/board-download-manifest.json', downloads)
    with pytest.raises(ValueError, match='records a rollback state'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_cleanup_that_left_another_trial_behind_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'cleanup.json', _set(('exit_code',), 1))
    with pytest.raises(ValueError, match='cleanup record unverified'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)


def test_self_consistent_forgery_of_the_acceptance_is_refused(tmp_path):
    """Every document that restates the forged digest is rewritten and rehashed.

    The acceptance identity digest, the baseline canonical digest, the receipt's own
    acceptance summary and read-back summary, the download record and the receipt's
    artifact map all say the forged values -- and the route still refuses, because
    those values are read from the verified candidate directory.
    """
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'board-download/acceptance-report.json',
                _check_detail('a_release_identity', 'software_digest'))
    mutate_json(directory, 'board-download/acceptance-report.json',
                _check_detail('c_baseline_load_not_formal', 'canonical_sha256'))
    receipt_before = _load(directory / 'receipt.json')
    sync_receipt(directory)
    receipt_after = _load(directory / 'receipt.json')
    assert receipt_after['acceptance']['software_digest'] == 'f' * 64
    assert receipt_after['acceptance']['baseline']['canonical_sha256'] == 'f' * 64
    assert receipt_after['evidence_sha256']['board-download/acceptance-report.json'] == D.sha(
        directory / 'board-download/acceptance-report.json')
    assert receipt_after['evidence_sha256'] != receipt_before['evidence_sha256']
    assert receipt_before['acceptance']['software_digest'] != 'f' * 64
    with pytest.raises(ValueError, match='acceptance report disagrees with the release'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), directory,
                                                                CANDIDATE)
