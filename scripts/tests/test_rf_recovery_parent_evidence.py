"""The sixth rollback-parent route: a release recovered by an after-reboot read-back.

1c0894bf is the board's current release.  Its reviewed non-emitting trial deployment
was interrupted by a board-side fault: the dispatch client reported
"remote install deadline; state unknown", the unit could not even be observed, and no
receipt -- least of all an INSTALLED_VERIFIED one -- was ever written.  After the
operator's reboot the same release was still current, one independent read-only
read-back found it safe and the six approved acceptance checks passed on the release
that was running.  verify_installed_parent_via_recovery_acceptance rebuilds that chain
and binds every identity and digest to the candidate directory R.verify_release
verified.

These tests prove the real chain is accepted, that both deployment gates can use it,
that it cannot stand in for an INSTALLED_VERIFIED receipt, and that every step --
the trial intent, the lost dispatch, the six acceptance checks, the archived frame,
the read-only read-backs, the board's own release listing and a self-consistent
forgery -- is load-bearing.
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
RELEASE = '1c0894bfe7751be2e1afa4e8bac564b8'
CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-d6-calibration-fix-2026-10-18/'
             'work/candidate-d6/releases' / RELEASE)
EVIDENCE = ROOT / 'evidence/rf-teaching/codex-s2-takeover-2026-10-18'
TRIAL_PARENT = '1a30f4b7b9aebe27965fcdd6dcf7634e'
BOOT = '9672af66-21ff-4ceb-8f91-f112ad444651'

# The new baseline candidate this route unblocks: built from 1c0894bf with one new
# baseline file, never deployed, so no route may claim it was ever on the board.
OTHER = '03b9b883a61f29605be2c1f3817024d0'
OTHER_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-baseline-install-2026-10-18/'
                   'candidate/releases' / OTHER)

# Headroom gate inputs the frozen activation route already uses.
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

ACCEPTANCE = 'recovery/acceptance-report.json'
RAW = 'recovery/acceptance-raw.log'
STATE = 'recovery/192953/state.json'
PREFLIGHT = 'readback/post-01-preflight.json'
LISTING = 'readback/release-listing.json'
RECORD = 'deployment-recovery.json'
SCRIPT = 'acceptance.py'
DISPATCH = 'logs/dispatch.result.json'
TRIAL = 'trial/trial.json'
INTENT = 'trial/dispatch-intent.json'
FRAME = 'trial/stage/frames/attempt-2/frame.iq'
REPLAY = 'trial/stage/rf_headroom_replay.py'


def route(parent, evidence, candidate=None):
    return D.verify_installed_parent_via_recovery_acceptance(parent, evidence, candidate)


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def parent_of(candidate=CANDIDATE):
    return R.verify_release(Path(candidate), require_software=True)


def copy_evidence(tmp_path):
    """The required members of the recovery package, byte for byte.

    Only the files the route reads are copied: unlike the receipt-bound routes this
    package has no document that lists every artifact, so a partial copy cannot make
    a check under test pass or fail for the wrong reason.
    """
    directory = Path(tmp_path) / 'recovery-package'
    for name in D.RECOVERY_EVIDENCE_FILES:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EVIDENCE / name, target)
    return directory


def mutate_json(mutate):
    """A mutation of one JSON document, applied by expect_refusal at its path."""
    def apply(directory, relative):
        path = Path(directory) / relative
        before = path.read_bytes()
        document = _load(path)
        mutate(document)
        _write(path, document)
        assert path.read_bytes() != before, 'the mutation changed nothing in ' + relative
    return apply


def mutate_linewise(mutate):
    """Mutate a capture that carries sudo's own trailing text after its JSON line."""
    def apply(directory, relative):
        path = Path(directory) / relative
        before = path.read_bytes()
        document = None
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.startswith('{'):
                document = json.loads(line)
                break
        assert document is not None
        mutate(document)
        path.write_text(json.dumps(document), encoding='utf-8')
        assert path.read_bytes() != before, 'the mutation changed nothing in ' + relative
    return apply


def mutate_text(mutate):
    def apply(directory, relative):
        path = Path(directory) / relative
        before = path.read_bytes()
        path.write_bytes(mutate(path.read_text(encoding='utf-8', errors='replace'))
                         .encode('utf-8'))
        assert path.read_bytes() != before, 'the mutation changed nothing in ' + relative
    return apply


def _set(path, value):
    def mutate(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def _drop(path):
    def mutate(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return mutate


def _evidence_before(directory=None):
    directory = EVIDENCE if directory is None else Path(directory)
    return {path.relative_to(directory).as_posix(): D.sha(path)
            for path in directory.rglob('*') if path.is_file()}


def expect_refusal(tmp_path, relative, mutation, message, parent=None):
    directory = copy_evidence(tmp_path)
    if mutation is not None:
        mutation(directory, relative)
    with pytest.raises(ValueError, match=message):
        route(parent_of() if parent is None else parent, directory, CANDIDATE)
    return directory


# --- the real chain is accepted -------------------------------------------------

def test_the_real_recovery_package_proves_the_running_parent():
    proof = route(parent_of(), EVIDENCE, CANDIDATE)
    assert proof['release_id'] == RELEASE
    assert proof['source'] == 'recovery-acceptance-evidence'
    assert proof['software_contract'] == 'cnn-bpsk-environment-v1'
    assert proof['software_modules'] == 44
    assert proof['trial_parent'] == TRIAL_PARENT
    assert proof['recovery_boot_id'] == BOOT
    assert proof['evidence_dir'] == str(EVIDENCE.resolve())


def test_the_proof_binds_every_required_member_it_read():
    proof = route(parent_of(), EVIDENCE, CANDIDATE)
    assert set(proof['evidence_sha256']) == set(D.RECOVERY_EVIDENCE_FILES)
    for name, digest in proof['evidence_sha256'].items():
        assert digest == D.sha(EVIDENCE / name)


def test_the_archived_frame_and_the_acceptance_replay_module_are_the_staged_bytes():
    proof = route(parent_of(), EVIDENCE, CANDIDATE)
    assert proof['evidence_sha256'][FRAME] == _load(EVIDENCE / TRIAL)['files'][
        'frames/attempt-2/frame.iq']
    assert proof['evidence_sha256'][REPLAY] == _load(EVIDENCE / TRIAL)['files'][
        'rf_headroom_replay.py']


def test_the_route_never_writes_or_renames_a_receipt(tmp_path):
    directory = copy_evidence(tmp_path)
    before = _evidence_before(directory)
    route(parent_of(), directory, CANDIDATE)
    assert _evidence_before(directory) == before


def test_the_real_evidence_directory_is_untouched_by_the_route():
    before = _evidence_before()
    route(parent_of(), EVIDENCE, CANDIDATE)
    assert _evidence_before() == before


def test_the_route_requires_the_parent_candidate_directory():
    with pytest.raises(ValueError, match='requires the parent candidate'):
        route(parent_of(), EVIDENCE, None)


def test_an_evidence_path_that_is_not_a_directory_is_refused(tmp_path):
    with pytest.raises(ValueError, match='evidence directory required'):
        route(parent_of(), tmp_path / 'absent', CANDIDATE)


@pytest.mark.parametrize('name', list(D.RECOVERY_EVIDENCE_FILES))
def test_every_required_member_missing_is_refused_by_name(tmp_path, name):
    directory = copy_evidence(tmp_path)
    (directory / name).unlink()
    with pytest.raises(ValueError, match='missing'):
        route(parent_of(), directory, CANDIDATE)


# --- 1. identity ----------------------------------------------------------------

def test_the_evidence_cannot_stand_in_for_a_release_it_never_installed(tmp_path):
    """The candidate this route unblocks was never deployed: refuse it."""
    other = R.verify_release(OTHER_CANDIDATE, require_software=True)
    with pytest.raises(ValueError, match='does not name this release'):
        route(other, EVIDENCE, OTHER_CANDIDATE)


@pytest.mark.parametrize('relative,mutation', [
    (ACCEPTANCE, _set(('report', 'expected_release'), 'f' * 32)),
    (ACCEPTANCE, _set(('report', 'checks', 0, 'detail', 'release_id'), 'f' * 32)),
    (ACCEPTANCE, _set(('report', 'state', 'release_id_from_name'), 'f' * 32)),
    (ACCEPTANCE, _set(('report', 'state', 'expected_release'), 'f' * 32)),
    (TRIAL, _set(('release_id',), 'f' * 32)),
    (INTENT, _set(('release_id',), 'f' * 32)),
    (LISTING, _set(('release_id',), 'f' * 32)),
    (STATE, _set(('current',), '/home/xilinx/overlays/awg_merged/releases/' + 'f' * 32)),
    (STATE, _set(('active_release',), '/home/xilinx/overlays/awg_merged/releases/' + 'f' * 32)),
    (STATE, _set(('manifest', 'release_id'), 'f' * 32)),
    (PREFLIGHT, _set(('state', 'release_id'), 'f' * 32)),
    (PREFLIGHT, _set(('snapshot', 'current_release'), 'f' * 32)),
    (RECORD, _set(('release_id',), 'f' * 32)),
    (ACCEPTANCE, _set(('report', 'checks', 0, 'detail', 'software_contract'), 'other-v1')),
])
def test_identity_that_disagrees_with_the_verified_release_is_refused(tmp_path, relative,
                                                                     mutation):
    mutator = mutate_linewise if relative == LISTING else mutate_json
    expect_refusal(tmp_path, relative, mutator(mutation), 'releas|identity|does not name')


def test_the_trial_must_name_the_release_it_replaced(tmp_path):
    expect_refusal(tmp_path, TRIAL, mutate_json(_set(('parent_release',), RELEASE)),
                   'release it replaced')


def test_the_recovery_record_must_report_the_unknown_original_trial(tmp_path):
    expect_refusal(tmp_path, RECORD,
                   mutate_json(_set(('original_trial_outcome',), 'INSTALLED_VERIFIED')),
                   'original trial outcome')


def test_the_recovery_record_must_not_report_a_reinstallation(tmp_path):
    expect_refusal(tmp_path, RECORD, mutate_json(_set(('reinstallation',), True)),
                   'reinstallation')


def test_the_recovery_record_must_not_report_rf_captures(tmp_path):
    expect_refusal(tmp_path, RECORD, mutate_json(_set(('rf_captures',), 1)), 'rf capture')


def test_the_recovery_record_must_name_the_recovery_boot(tmp_path):
    expect_refusal(tmp_path, RECORD, mutate_json(_set(('boot_id',), 'f' * 36)),
                   'recovery boot')


def test_the_recovery_record_must_carry_the_six_checks(tmp_path):
    expect_refusal(tmp_path, RECORD, mutate_json(_set(('acceptance_checks',), 5)),
                   'six acceptance checks')


def test_the_dispatch_must_be_the_transport_loss_it_is_claimed_to_be(tmp_path):
    expect_refusal(tmp_path, DISPATCH, mutate_json(_set(('exit_code',), 0)),
                   'transport loss')


def test_the_dispatch_must_record_the_unknown_state(tmp_path):
    expect_refusal(tmp_path, DISPATCH, mutate_json(_set(('error',), 'ok')),
                   'unknown state')


def test_the_dispatch_must_name_the_trial_command(tmp_path):
    expect_refusal(tmp_path, DISPATCH, mutate_json(_set(('command',), 'sudo -S true')),
                   'dispatch')


# --- 2. the board's own listing against the candidate tree ----------------------

@pytest.mark.parametrize('key,value', [
    ('rf_daemon', 'f' * 64),
    ('ui_model.py', 'f' * 64),
])
def test_a_release_listing_that_differs_from_the_candidate_is_refused(tmp_path, key, value):
    expect_refusal(tmp_path, LISTING,
                   mutate_linewise(_set(('files', key), value)), 'listing')


def test_a_release_listing_that_drops_a_candidate_file_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / LISTING
    document = None
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('{'):
            document = json.loads(line)
            break
    document['files'].pop(sorted(document['files'])[0])
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='listing'):
        route(parent_of(), directory, CANDIDATE)


def test_a_release_listing_that_adds_a_file_is_refused(tmp_path):
    expect_refusal(tmp_path, LISTING,
                   mutate_linewise(_set(('files', 'extra.txt'), 'f' * 64)), 'listing')


def test_the_second_board_listing_must_agree_as_well(tmp_path):
    expect_refusal(tmp_path, STATE,
                   mutate_json(_set(('candidate_dirs', RELEASE, 'files', 'rf_daemon'),
                                    'f' * 64)), 'listing')


def test_the_board_listing_must_not_be_a_symlink_directory(tmp_path):
    expect_refusal(tmp_path, STATE,
                   mutate_json(_set(('candidate_dirs', RELEASE, 'symlink'), True)),
                   'listing')


def test_the_board_listing_must_materialise_the_release(tmp_path):
    expect_refusal(tmp_path, STATE,
                   mutate_json(_set(('candidate_dirs', RELEASE, 'exists'), False)),
                   'listing')


def test_the_board_listing_must_verify_the_release_on_the_board(tmp_path):
    expect_refusal(tmp_path, STATE,
                   mutate_json(_set(('candidate_dirs', RELEASE, 'verified_release'), 'f' * 32)),
                   'listing')


def test_the_listing_record_must_carry_the_manifest_daemon(tmp_path):
    expect_refusal(tmp_path, LISTING, mutate_linewise(_set(('daemon_sha256',), 'f' * 64)),
                   'daemon')


# --- 3. digests -----------------------------------------------------------------

@pytest.mark.parametrize('relative,mutation', [
    (ACCEPTANCE, _set(('report', 'checks', 0, 'detail', 'software_digest'), 'f' * 64)),
    (ACCEPTANCE, _set(('report', 'checks', 0, 'detail', 'software_modules'), 43)),
    (ACCEPTANCE, _set(('report', 'checks', 1, 'detail', 'rf_environment', 'sha256'), 'f' * 64)),
    (ACCEPTANCE, _set(('report', 'checks', 1, 'detail', 'rf_environment', 'module'),
                      '/home/xilinx/overlays/awg_merged/releases/' + 'f' * 32
                      + '/rf_environment.py')),
    (ACCEPTANCE, _set(('report', 'checks', 2, 'detail', 'file_sha256'), 'f' * 64)),
    (ACCEPTANCE, _set(('report', 'checks', 2, 'detail', 'canonical_sha256'), 'f' * 64)),
    (ACCEPTANCE, _set(('report', 'checks', 2, 'detail', 'formal_acceptance_baseline'), True)),
    (ACCEPTANCE, _set(('report', 'checks', 2, 'detail', 'calibration_status'), 'formal')),
    (STATE, _set(('manifest', 'daemon_sha256'), 'f' * 64)),
    (STATE, _set(('manifest', 'bit_sha256'), 'f' * 64)),
    (PREFLIGHT, _set(('state', 'software_digest'), 'f' * 64)),
    (PREFLIGHT, _set(('state', 'software_modules'), 43)),
    (PREFLIGHT, _set(('state', 'baseline', 'file_sha256'), 'f' * 64)),
    (PREFLIGHT, _set(('state', 'baseline', 'canonical_sha256'), 'f' * 64)),
    (PREFLIGHT, _set(('state', 'baseline', 'calibration_status'), 'formal')),
    (PREFLIGHT, _set(('state', 'baseline', 'environment_version'), 'other-v9')),
])
def test_a_digest_that_disagrees_with_the_verified_release_is_refused(tmp_path, relative,
                                                                     mutation):
    expect_refusal(tmp_path, relative, mutate_json(mutation),
                   'disagree|baseline|identity|release')


def test_the_trial_archive_digest_must_be_the_staged_archive(tmp_path):
    expect_refusal(tmp_path, TRIAL, mutate_json(_set(('archive_sha',), 'f' * 64)),
                   'staged archive')


def test_the_staged_payload_must_still_match_the_recorded_stage_map(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'trial/stage/rf_headroom_replay.py'
    path.write_bytes(path.read_bytes() + b'#')
    with pytest.raises(ValueError, match='stage does not match its local map'):
        route(parent_of(), directory, CANDIDATE)


def test_the_frame_bytes_the_acceptance_smoked_must_be_the_staged_frame(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 5, 'detail', 'frame', 'iq_sha256'),
                                    'f' * 64)), 'smoke|frame')


def test_the_frame_size_the_acceptance_smoked_must_match_the_staged_bytes(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 5, 'detail', 'frame', 'bytes'), 1)),
                   'smoke|frame')


@pytest.mark.parametrize('key,value', [
    ('bit_errors', 3),
    ('compared_bits', 137),
    ('ber', 0.5),
    ('rail_count', 1),
    ('carrier_sync', False),
    ('reference_sync', False),
])
def test_a_failed_archived_frame_smoke_test_is_refused(tmp_path, key, value):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 5, 'detail', 'demod', key), value)),
                   'smoke|frame')


def test_the_acceptance_replay_helper_must_be_the_staged_replay_module(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 5, 'detail', 'replay_helper',
                                     'sha256'), 'f' * 64)), 'smoke|frame')


def test_the_acceptance_script_must_be_the_staged_reviewed_script(tmp_path):
    expect_refusal(tmp_path, SCRIPT, mutate_text(lambda text: text + '\n# x\n'),
                   'acceptance script')


def test_the_trial_intent_must_bind_the_reviewed_acceptance_script(tmp_path):
    expect_refusal(tmp_path, TRIAL,
                   mutate_json(_set(('reviewed', 'acceptance_sha256'), 'f' * 64)),
                   'acceptance')


# --- 4. the six-check recovery acceptance ---------------------------------------

@pytest.mark.parametrize('name', ['a_release_identity', 'b_domain_constants',
                                  'd_services', 'e_rf_closed', 'f_archived_iq_smoke'])
def test_a_missing_acceptance_check_is_refused(tmp_path, name):
    directory = copy_evidence(tmp_path)
    path = directory / ACCEPTANCE
    document = _load(path)
    document['report']['checks'] = [c for c in document['report']['checks']
                                    if c['name'] != name]
    _write(path, document)
    with pytest.raises(ValueError, match='six approved checks'):
        route(parent_of(), directory, CANDIDATE)


def test_an_extra_acceptance_check_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / ACCEPTANCE
    document = _load(path)
    document['report']['checks'].append(dict(name='g_extra', ok=True, detail={}))
    _write(path, document)
    with pytest.raises(ValueError, match='six approved checks'):
        route(parent_of(), directory, CANDIDATE)


def test_a_renamed_acceptance_check_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 3, 'name'), 'd_services_v2')),
                   'six approved checks')


@pytest.mark.parametrize('index', range(6))
def test_a_failing_acceptance_check_is_refused(tmp_path, index):
    directory = copy_evidence(tmp_path)
    path = directory / ACCEPTANCE
    document = _load(path)
    document['report']['checks'][index]['ok'] = False
    _write(path, document)
    with pytest.raises(ValueError, match='failed check'):
        route(parent_of(), directory, CANDIDATE)


def test_an_acceptance_report_that_denies_itself_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE, mutate_json(_set(('ok',), False)),
                   'acceptance report unverified')


def test_an_acceptance_report_with_an_unknown_schema_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE, mutate_json(_set(('report', 'schema'), 'other-v1')),
                   'acceptance report unverified')


def test_an_acceptance_report_without_its_state_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE, mutate_json(_drop(('report', 'state'))),
                   'acceptance state')


def test_an_acceptance_state_under_another_boot_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'state', 'boot_id'), 'f' * 36)),
                   'boot')


def test_an_acceptance_state_pointing_at_another_release_path_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'state', 'release_path'),
                                    '/home/xilinx/overlays/awg_merged/releases/' + 'f' * 32)),
                   'acceptance state')


# --- 5. the raw capture: a failure or a rollback is refused ---------------------

@pytest.mark.parametrize('marker', ['TRIAL_FAILED', 'TRIAL_ROLLED_BACK',
                                    'TRIAL_RECOVERY_REQUIRED', 'ACTIVATION_ROLLED_BACK',
                                    'RECOVERY_ACCEPTANCE_FAILED',
                                    'Traceback (most recent call last)'])
def test_a_recovery_capture_recording_a_failure_is_refused(tmp_path, marker):
    expect_refusal(tmp_path, RAW,
                   mutate_text(lambda text: text + '\n' + marker + '\n'),
                   'failed or rolled-back')


def test_a_capture_without_the_acceptance_record_is_refused(tmp_path):
    expect_refusal(tmp_path, RAW,
                   mutate_text(lambda text: text.replace('RECOVERY_ACCEPTANCE ', '')),
                   'acceptance record')


def test_a_repeated_acceptance_record_is_refused(tmp_path):
    expect_refusal(tmp_path, RAW,
                   mutate_text(lambda text: text + text.strip().splitlines()[-1] + '\n'),
                   'more than one')


def test_a_report_that_disagrees_with_its_own_capture_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_text(lambda text: text.replace('"ok": true', '"ok": false', 1))(directory, RAW)
    with pytest.raises(ValueError, match='disagrees with the acceptance report'):
        route(parent_of(), directory, CANDIDATE)


# --- 6. the execution-time read-only read-back ----------------------------------

def test_a_read_back_under_another_boot_than_the_acceptance_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(_set(('boot_id',), 'f' * 36))(directory, STATE)
    mutate_json(_set(('snapshot', 'boot_id'), 'f' * 36))(directory, PREFLIGHT)
    with pytest.raises(ValueError, match='another boot'):
        route(parent_of(), directory, CANDIDATE)


def test_a_read_back_of_another_release_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT,
                   mutate_json(_set(('snapshot', 'active_release'),
                                    '/home/xilinx/overlays/awg_merged/releases/' + 'f' * 32)),
                   'read-back')


def test_a_read_back_that_did_not_close_its_session_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT,
                   mutate_json(_set(('read_only_session_closed',), False)), 'read-only')


def test_a_read_back_that_was_not_read_only_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT, mutate_json(_set(('board_contact',), 'read-write')),
                   'read-only')


def test_a_read_back_that_reports_failures_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT, mutate_json(_set(('failures',), ['B1'])),
                   'read-back')


def test_a_read_back_with_a_failing_item_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT,
                   mutate_json(_set(('items', 'B5_services', 'ok'), False)), 'read-back')


def test_a_read_back_reporting_storage_faults_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT,
                   mutate_json(_set(('dmesg_faults',), ['mmc0: error -110'])), 'storage fault')


def test_the_recovery_state_reporting_storage_faults_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(_set(('dmesg', 'stdout'),
                     '[    1.0] mmc0: error -110 whilst initialising SD card\n'))(
        directory, STATE)
    with pytest.raises(ValueError, match='storage fault'):
        route(parent_of(), directory, CANDIDATE)


def test_a_read_back_whose_root_is_not_read_write_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT,
                   mutate_json(_set(('items', 'B8_mounts_rw', 'root_rw'), False)),
                   'read-only|root')


def test_the_recovery_state_with_a_read_only_root_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(_set(('mounts',), ['/dev/root / ext4 ro,relatime 0 0']))(directory, STATE)
    with pytest.raises(ValueError, match='root'):
        route(parent_of(), directory, CANDIDATE)


def test_a_read_back_with_a_restarted_service_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(_set(('services', 'rf-daemon', 'stdout'),
                     'MainPID=603\nNRestarts=1\nActiveState=active\nSubState=running\n'))(
        directory, STATE)
    with pytest.raises(ValueError, match='service'):
        route(parent_of(), directory, CANDIDATE)


def test_a_read_back_with_a_dead_service_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT,
                   mutate_json(_set(('state', 'services', 'rf-daemon', 'MainPID'), '0')),
                   'service')


def test_a_read_back_with_rf_armed_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT,
                   mutate_json(_set(('state', 'status', 'armed'), True)), 'RF|read-back')


def test_a_read_back_with_a_transmitter_selected_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(_set(('status', 'pl_status', 'tx', 'source_selected'), True))(directory, STATE)
    with pytest.raises(ValueError, match='RF'):
        route(parent_of(), directory, CANDIDATE)


def test_the_acceptance_domain_constants_must_agree_with_the_candidate(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 1, 'detail', 'constants',
                                     'rf_teaching_profile_ATTENUATION_MDB'), 40000)),
                   'domain')


def test_a_read_back_domain_that_disagrees_with_the_acceptance_is_refused(tmp_path):
    expect_refusal(tmp_path, PREFLIGHT,
                   mutate_json(_set(('state', 'client_ATTENUATION_MDB'), 40000)),
                   'domain')


def test_the_acceptance_domain_versions_must_be_the_candidate_baseline(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 1, 'detail', 'constants',
                                     'rf_environment_VERSION'), 'other-v9')),
                   'domain')


def test_an_acceptance_services_check_with_a_dead_unit_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 3, 'detail', 'rf-daemon',
                                     'ActiveState'), 'inactive')), 'services')


def test_an_acceptance_rf_check_that_is_not_closed_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 4, 'detail', 'armed'), True)),
                   'RF state')


def test_an_acceptance_rf_check_of_another_release_is_refused(tmp_path):
    expect_refusal(tmp_path, ACCEPTANCE,
                   mutate_json(_set(('report', 'checks', 4, 'detail', 'status_release_id'),
                                    'f' * 32)), 'RF state|releas')


# --- 7. a self-consistent rewrite still refuses --------------------------------

def rewrite_every_document_to_another_release(directory):
    """Rewrite every identity in the package, consistently, to a release id nobody ran.

    This is the strongest forgery the package's own documents can express: the
    acceptance report, its capture, the trial intent, the dispatch intent, the board
    listings, both read-backs and the recovery record all name the forged release.
    """
    forged = 'f' * 32
    acceptance = _load(directory / ACCEPTANCE)
    acceptance['report']['expected_release'] = forged
    acceptance['report']['checks'][0]['detail']['release_id'] = forged
    acceptance['report']['state']['release_id_from_name'] = forged
    acceptance['report']['state']['expected_release'] = forged
    _write(directory / ACCEPTANCE, acceptance)
    mutate_text(lambda text: text.replace(RELEASE, forged))(directory, RAW)
    for relative in (TRIAL, INTENT):
        document = _load(directory / relative)
        document['release_id'] = forged
        _write(directory / relative, document)
    for relative in (STATE, PREFLIGHT, RECORD):
        document = _load(directory / relative)
        text = json.dumps(document).replace(RELEASE, forged)
        _write(directory / relative, json.loads(text))
    listing = None
    for line in (directory / LISTING).read_text(encoding='utf-8').splitlines():
        if line.startswith('{'):
            listing = json.loads(line)
            break
    listing['release_id'] = forged
    (directory / LISTING).write_text(json.dumps(listing), encoding='utf-8')
    assert forged in (directory / ACCEPTANCE).read_text(encoding='utf-8')


def test_a_self_consistent_rewrite_still_names_the_verified_release(tmp_path):
    directory = copy_evidence(tmp_path)
    rewrite_every_document_to_another_release(directory)
    with pytest.raises(ValueError, match='releas|does not name'):
        route(parent_of(), directory, CANDIDATE)


def test_a_self_consistent_digest_rewrite_still_compares_with_the_candidate(tmp_path):
    directory = copy_evidence(tmp_path)
    acceptance = _load(directory / ACCEPTANCE)
    acceptance['report']['checks'][0]['detail']['software_digest'] = 'f' * 64
    _write(directory / ACCEPTANCE, acceptance)
    preflight = _load(directory / PREFLIGHT)
    preflight['state']['software_digest'] = 'f' * 64
    preflight['items']['B2_software_map']['digest'] = 'f' * 64
    _write(directory / PREFLIGHT, preflight)
    with pytest.raises(ValueError, match='identity|disagree|releas'):
        route(parent_of(), directory, CANDIDATE)


def test_a_self_consistent_listing_rewrite_still_compares_with_the_candidate(tmp_path):
    directory = copy_evidence(tmp_path)
    forged = {name: 'f' * 64 for name in
              _load(directory / TRIAL)['files']}
    for relative in (STATE,):
        document = _load(directory / relative)
        document['candidate_dirs'][RELEASE]['files'] = {
            name: 'f' * 64 for name in document['candidate_dirs'][RELEASE]['files']}
        document['candidate_dirs'][RELEASE]['files']['rf_daemon'] = D.sha(
            CANDIDATE / 'rf_daemon')
        _write(directory / relative, document)
    listing = None
    for line in (directory / LISTING).read_text(encoding='utf-8').splitlines():
        if line.startswith('{'):
            listing = json.loads(line)
            break
    listing['files'] = dict(forged)
    listing['files']['rf_daemon'] = D.sha(CANDIDATE / 'rf_daemon')
    (directory / LISTING).write_text(json.dumps(listing), encoding='utf-8')
    with pytest.raises(ValueError, match='listing'):
        route(parent_of(), directory, CANDIDATE)


# --- 8. it is not, and cannot become, an INSTALLED_VERIFIED receipt -------------

def test_the_route_returns_its_own_source_and_never_an_install_outcome():
    proof = route(parent_of(), EVIDENCE, CANDIDATE)
    assert proof['source'] == 'recovery-acceptance-evidence'
    assert proof['source'] != D.OUTCOME_INSTALLED
    assert 'installed' not in proof


def test_a_forged_receipt_is_still_refused_by_the_installer_route(tmp_path):
    """A document that only claims INSTALLED_VERIFIED is not the installer's chain."""
    receipt = tmp_path / 'install-receipt.json'
    _write(receipt, dict(installed=True, release_id=RELEASE,
                         board_state='VERIFIED_SAFE_NEW_RELEASE',
                         outcome=D.OUTCOME_INSTALLED,
                         evidence_sha256={'post-state.json': 'f' * 64}))
    with pytest.raises(ValueError, match='read-back'):
        D.verify_parent(CANDIDATE, receipt)


def test_the_frozen_routes_are_unreachable_without_naming_them(tmp_path):
    """The new route is only reachable by naming its evidence: nothing falls back."""
    with pytest.raises(ValueError, match='installation receipt required'):
        D.verify_parent(CANDIDATE, None)


def test_the_gate_accepts_the_recovery_parent_for_the_environment_contract():
    gate = E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None, None,
                          None, None, None, EVIDENCE)
    assert gate['parent_release'] == RELEASE
    assert gate['parent_recovery_boot_id'] == BOOT
    assert set(gate['parent_recovery_acceptance_sha256']) == set(
        D.RECOVERY_EVIDENCE_FILES)


def test_the_gate_accepts_the_recovery_parent_for_the_headroom_contract():
    gate = D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                          HEADROOM_CHECKPOINT,
                          parent_recovery_acceptance=EVIDENCE)
    assert gate['parent_release'] == RELEASE
    assert gate['parent_recovery_boot_id'] == BOOT


def test_the_headroom_gate_refuses_two_parent_proofs():
    with pytest.raises(ValueError, match='one rollback-parent proof at a time'):
        D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                       HEADROOM_CHECKPOINT, parent_recovery_acceptance=EVIDENCE,
                       parent_trial_acceptance=EVIDENCE)


def test_the_environment_gate_refuses_two_parent_proofs():
    with pytest.raises(ValueError, match='one S2 rollback parent proof at a time'):
        E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None, ENV_CLOSURE,
                       EVIDENCE, None, None, EVIDENCE)


def test_the_environment_gate_needs_the_parent_candidate_for_this_route():
    with pytest.raises(ValueError, match='needs its candidate'):
        E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, None, None, None,
                       None, None, None, EVIDENCE)


def test_the_headroom_cli_requires_exactly_one_parent_proof(tmp_path):
    completed = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts' / 'rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(HEADROOM_CANDIDATE), '--arm', str(HEADROOM_ARM),
         '--checkpoint', str(HEADROOM_CHECKPOINT), '--parent-candidate', str(CANDIDATE),
         '--output', str(tmp_path / 'naming-nothing')],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    assert completed.returncode != 0
    assert 'exactly one rollback-parent proof' in completed.stderr
    assert '--parent-recovery-acceptance' in completed.stderr


def test_the_headroom_cli_refuses_two_parent_proofs(tmp_path):
    completed = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts' / 'rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(HEADROOM_CANDIDATE), '--arm', str(HEADROOM_ARM),
         '--checkpoint', str(HEADROOM_CHECKPOINT), '--parent-candidate', str(CANDIDATE),
         '--parent-recovery-acceptance', str(EVIDENCE),
         '--parent-activation', str(ROOT / D.ACTIVATION_EVIDENCE),
         '--output', str(tmp_path / 'naming-two')],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    assert completed.returncode != 0
    assert 'exactly one rollback-parent proof' in completed.stderr


def test_the_environment_cli_refuses_two_parent_proofs(tmp_path):
    completed = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts' / 'rf_environment_deploy.py'), 'prepare',
         '--candidate', str(ENV_CANDIDATE), '--closure', str(ENV_CLOSURE),
         '--arm', str(ENV_ARM), '--parent-candidate', str(CANDIDATE),
         '--parent-recovery-acceptance', str(EVIDENCE),
         '--parent-install', str(ENV_CLOSURE),
         '--output', str(tmp_path / 'naming-two')],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    assert completed.returncode != 0
    assert 'at most one rollback-parent proof' in completed.stderr
    assert '--parent-recovery-acceptance' in completed.stderr


def test_an_end_to_end_prepare_and_verify_bundle_with_the_recovery_parent(tmp_path):
    output = tmp_path / 'bundle'
    subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts' / 'rf_environment_deploy.py'), 'prepare',
         '--candidate', str(ENV_CANDIDATE), '--closure', str(ENV_CLOSURE),
         '--arm', str(ENV_ARM), '--parent-candidate', str(CANDIDATE),
         '--parent-recovery-acceptance', str(EVIDENCE), '--output', str(output)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=900, check=True)
    receipt = _load(output / 'bundle-receipt.json')
    assert receipt['parent_release'] == RELEASE
    assert receipt['parent_recovery_acceptance'] == str(EVIDENCE.resolve())
    assert set(receipt['parent_recovery_acceptance_sha256']) == set(
        D.RECOVERY_EVIDENCE_FILES)
    assert E.verify_bundle(output)['release_id'] == receipt['release_id']
