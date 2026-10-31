"""Offline tests for the parameterised read-only pre/post-install check.

No board: the board facts are fixtures, and every judgement is a pure function over them.
The two things these tests are designed to prove are the ones the review cares about:

* nothing is hardcoded to an old release/parent/boot id -- the same code judges a parent
  (pre) and a new candidate (post);
* a missing, unreadable or undecidable fact is a NAMED failure or UNKNOWN, never a pass.
"""
import ast
import inspect
import json

import pytest

from scripts import rf_environment_readonly_check as M

RELEASE = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
PARENT = '03b9b883a61f29605be2c1f3817024d0'
OTHER = '1c0894bfe7751be2e1afa4e8bac564b8'
BOOT = '9672af66-1111-2222-3333-444455556666'
BOOT2 = '00000000-aaaa-bbbb-cccc-111122223333'
FILE_SHA = '351c52c510ff3f52' + '0' * 48
CANON_SHA = 'dd794f0c71bf5f3b' + '0' * 48
DIGEST = 'e' * 64
CONTRACT = 'cnn-bpsk-environment-v1'
MOUNTS_RW = ('/dev/root / ext4 rw,relatime 0 0\n'
             '/dev/mmcblk0p1 /boot vfat rw,relatime 0 0\n')
MOUNTS_RO = ('/dev/root / ext4 ro,relatime 0 0\n'
             '/dev/mmcblk0p1 /boot vfat rw,relatime 0 0\n')
SERVICES_TEXT = ('ActiveState=active\nSubState=running\nMainPID=551\nNRestarts=0\n')


def expected_fixture(release=RELEASE, **overrides):
    value = {
        'candidate': 'fixture/release',
        'release_id': release,
        'software_contract': CONTRACT,
        'software_modules': 44,
        'software_digest': DIGEST,
        'daemon_version': '1.0.4',
        'protocol_version': 1,
        'baseline': {'file_sha256': FILE_SHA, 'canonical_sha256': CANON_SHA,
                     'environment_version': 'rf-window-environment-v4', 'sources': 20},
        'release_path': 'fixture/release',
    }
    value.update(overrides)
    return value


def status_fixture(overrides=None):
    status = {'release_id': RELEASE, 'online': True, 'stale': False, 'armed': False,
              'core': False, 'mode': 'status_only',
              'pl_status': {'io_ok': True, 'tx': {'source_selected': False,
                                                  'position_words': 0},
                            'rx': {'armed': False, 'position_words': 0}}}
    status.update(overrides or {})
    return status


def facts_fixture(release=RELEASE, *, boot=BOOT, status=None, state_services=None,
                  storage='', mounts=MOUNTS_RW, residue=(), lock_lines=None,
                  units='', jobs='', snapshot_ok=True, snapshot_error=None, state_rc=0,
                  paths_rc=0, state_release=None, sample_state=True):
    release = release
    state_release = state_release or release
    snapshot = {
        'current_release': release,
        'active_release': '/home/xilinx/overlays/awg_merged/releases/' + release,
        'boot_id': boot, 'uptime_seconds': 1234.5,
        'units': {name: {'rc': 0, 'out': SERVICES_TEXT, 'err': ''}
                  for name in ('pynq-status', 'rf-daemon')},
        'commands': {'kernel_storage': {'rc': 0, 'out': storage, 'err': ''},
                     'mounts': {'rc': 0, 'out': mounts, 'err': ''}},
        'kernel_storage': storage, 'mounts': mounts,
    }
    status = status_fixture(status)
    status['release_id'] = state_release
    services = state_services or {name: {'ActiveState': 'active', 'SubState': 'running',
                                         'MainPID': '551', 'NRestarts': '0'}
                                  for name in ('pynq-status', 'rf-daemon')}
    state = None if not sample_state else {
        'expected_release': state_release, 'release_id': state_release,
        'release_path': '/home/xilinx/overlays/awg_merged/releases/' + state_release,
        'release_verified': True, 'software_contract': CONTRACT, 'status': status,
        'services': dict(services),
        'active_release': '/home/xilinx/overlays/awg_merged/releases/' + state_release,
        'software_modules': 44, 'software_digest': DIGEST, 'daemon_version': '1.0.4',
        'protocol_version': 1,
        'baseline': {'file_sha256': FILE_SHA, 'canonical_sha256': CANON_SHA,
                     'environment_version': 'rf-window-environment-v4',
                     'calibration_status': 'candidate', 'sources': 20},
        'boot_id': boot, 'uptime_seconds': 1234.5,
    }
    if state is not None:
        state['safe'] = bool(status.get('release_id') == state_release
                             and status.get('online') is True
                             and status.get('stale') is False
                             and status.get('armed') is False
                             and (status.get('pl_status') or {}).get('io_ok') is True
                             and ((status.get('pl_status') or {}).get('rx') or {}
                                  ).get('armed') is False
                             and ((status.get('pl_status') or {}).get('tx') or {}
                                  ).get('source_selected') is False)
    if lock_lines is None:
        lock_lines = ['LOCK_ABSENT', 'LOCK_FREE', 'LOCK_SOURCE=file_absent']
    residue_lines = list(residue)
    paths = '\n'.join(
        ['==LOCK=='] + list(lock_lines) + ['LOCK_DONE',
                                           '==UNITS==', units, 'UNITS_DONE',
                                           '==JOBS==', jobs, 'JOBS_DONE',
                                           '==TMP_RESIDUE=='] + residue_lines
        + ['TMP_RESIDUE_DONE', '==MOUNTS==', mounts, 'MOUNTS_DONE',
           '==RELEASES==', release, 'RELEASES_DONE', 'READONLY_PATHS_DONE']) + '\n'
    return {'utc': '2026-10-20T00:00:00+00:00', 'snapshot': snapshot,
            'snapshot_ok': snapshot_ok, 'snapshot_error': snapshot_error,
            'snapshot_rc': 0, 'state': state, 'state_rc': state_rc,
            'paths': paths, 'paths_rc': paths_rc}


def check(expected, facts, **kwargs):
    kwargs.setdefault('phase', M.PHASE_PRE)
    return M.evaluate(expected, facts, **kwargs)


# --------------------------------------------------------------------- the happy paths

def test_the_pre_phase_passes_on_the_release_it_was_given():
    report = check(expected_fixture(), facts_fixture())
    assert report['verdict'] == M.VERIFIED_SAFE_EXPECTED
    assert report['board_state'] == 'VERIFIED_SAFE_EXPECTED_RELEASE'
    assert report['passed'] is True and report['failures'] == []
    assert report['items']['B1_release_identity']['board_release'] == RELEASE


def test_the_post_phase_passes_on_the_new_release_with_the_recorded_boot_id():
    new = '11112222333344445555666677778888'
    report = check(expected_fixture(new), facts_fixture(new), phase=M.PHASE_POST,
                   expect_boot_id=BOOT)
    assert report['passed'] is True and report['verdict'] == M.VERIFIED_SAFE_EXPECTED
    assert report['items']['B11_boot_identity']['expected_boot_id'] == BOOT


def test_the_same_code_judges_a_parent_and_a_new_release():
    """No release id is baked in: the identical fixtures directed at another id pass."""
    new = '99998888777766665555444433332222'
    for release in (PARENT, OTHER, new):
        report = check(expected_fixture(release), facts_fixture(release))
        assert report['passed'] is True, (release, report['failures'])


# ------------------------------------------------------------ named identity failures

def test_a_release_mismatch_is_a_named_failure():
    report = check(expected_fixture(RELEASE), facts_fixture(PARENT))
    assert report['passed'] is False
    assert 'B1_release_identity' in report['failures']
    assert 'B0_strict_state' in report['failures']


def test_a_software_map_mismatch_is_named():
    facts = facts_fixture()
    facts['state']['software_modules'] = 43
    report = check(expected_fixture(), facts)
    assert report['failures'] == ['B2_software_map']


def test_a_daemon_or_protocol_mismatch_is_named():
    facts = facts_fixture()
    facts['state']['daemon_version'] = '1.0.3'
    report = check(expected_fixture(), facts)
    assert 'B2_software_map' in report['failures']


def test_a_baseline_byte_mismatch_is_named():
    facts = facts_fixture()
    facts['state']['baseline']['canonical_sha256'] = 'f' * 64
    report = check(expected_fixture(), facts)
    assert report['failures'] == ['B3_baseline_bytes']


def test_an_unreadable_board_baseline_is_named():
    facts = facts_fixture()
    facts['state']['baseline'] = {'error': 'ValueError: bad document'}
    report = check(expected_fixture(), facts)
    assert 'B3_baseline_bytes' in report['failures']


# ------------------------------------------------------------------- service failures

def test_a_stopped_service_is_a_named_failure_not_unknown():
    facts = facts_fixture()
    facts['state']['services']['rf-daemon']['ActiveState'] = 'inactive'
    report = check(expected_fixture(), facts)
    assert report['verdict'] == M.FAILED
    assert 'B4_services' in report['failures']


def test_a_restarted_service_is_a_named_failure():
    facts = facts_fixture()
    facts['state']['services']['pynq-status']['NRestarts'] = '2'
    report = check(expected_fixture(), facts)
    assert 'B4_services' in report['failures']


def test_the_snapshot_services_must_agree_with_the_state_services():
    facts = facts_fixture()
    facts['snapshot']['units']['rf-daemon']['out'] = (
        'ActiveState=inactive\nSubState=dead\nMainPID=0\nNRestarts=0\n')
    report = check(expected_fixture(), facts)
    assert 'B4_services' in report['failures']
    assert report['verdict'] == M.FAILED, 'a stopped unit is a failure, not unknown'


# ----------------------------------------------------------------------- RF safety

@pytest.mark.parametrize('override,breaks_strict_gate', [
    ({'armed': True}, True),
    # `core` is not part of the audited safe_state predicate; this check is STRICTER
    # than the gate, so the item fails while the strict gate still holds.
    ({'core': True}, False),
    ({'pl_status': {'io_ok': False, 'tx': {'source_selected': False},
                    'rx': {'armed': False}}}, True),
    ({'pl_status': {'io_ok': True, 'tx': {'source_selected': True},
                    'rx': {'armed': False}}}, True),
    ({'pl_status': {'io_ok': True, 'tx': {'source_selected': False},
                    'rx': {'armed': True}}}, True),
])
def test_any_unsafe_rf_state_is_a_named_failure(override, breaks_strict_gate):
    facts = facts_fixture(status=override)
    report = check(expected_fixture(), facts)
    assert 'B5_rf_safe' in report['failures']
    assert ('B0_strict_state' in report['failures']) is breaks_strict_gate


# ------------------------------------------------------------- storage / mounts / lock

def test_a_kernel_storage_fault_is_named():
    facts = facts_fixture(storage='[ 12.3] mmc0: error -110 whilst initialising SD card\n')
    report = check(expected_fixture(), facts)
    assert report['failures'] == ['B6_storage']


def test_a_fat_not_unmounted_line_is_not_a_storage_fault():
    facts = facts_fixture(storage='[ 43.7] FAT-fs (mmcblk0p1): Volume was not properly '
                                  'unmounted. Some data may be corrupt. Please run fsck.\n')
    report = check(expected_fixture(), facts)
    assert report['passed'] is True


def test_a_read_only_root_is_named():
    facts = facts_fixture(mounts=MOUNTS_RO)
    report = check(expected_fixture(), facts)
    assert report['failures'] == ['B7_mounts_rw']
    assert report['items']['B7_mounts_rw']['root_rw'] is False


def test_a_missing_mount_line_is_a_failure_not_a_default_yes():
    facts = facts_fixture(mounts='/dev/root / ext4 rw,relatime 0 0\n')
    report = check(expected_fixture(), facts)
    assert report['failures'] == ['B7_mounts_rw']
    assert report['items']['B7_mounts_rw']['boot_rw'] is None


@pytest.mark.parametrize('kwargs,state', [
    ({'units': 'rf-trial-x.service loaded failed failed'}, 'LOCK_FREE'),
    ({'jobs': '123 rf-activate-abc.service start running'}, 'LOCK_FREE'),
    ({}, 'LOCK_HELD'),
    ({}, 'LOCK_UNKNOWN'),
])
def test_inflight_deployment_or_a_held_lock_is_named(kwargs, state):
    lock_lines = ['LOCK_PRESENT', state]
    facts = facts_fixture(lock_lines=lock_lines, **kwargs)
    report = check(expected_fixture(), facts)
    failing = set(report['failures'])
    assert 'B8_no_inflight' in failing or 'B9_lock' in failing
    assert report['passed'] is False


def test_tmp_residue_is_named_and_excused_residue_is_not():
    facts = facts_fixture(residue=['/tmp/rf-teaching-install-abc'])
    report = check(expected_fixture(), facts)
    assert report['failures'] == ['B10_tmp_clean']
    report = check(expected_fixture(), facts,
                   expected_residue=['/tmp/rf-teaching-install-abc'])
    assert report['passed'] is True


# -------------------------------------------------------------------- boot identity

def test_a_changed_boot_id_is_a_named_failure():
    report = check(expected_fixture(), facts_fixture(boot=BOOT2), phase=M.PHASE_POST,
                   expect_boot_id=BOOT)
    assert report['failures'] == ['B11_boot_identity']
    assert report['items']['B11_boot_identity']['reason'] == M.BOOT_ID_CHANGED


def test_a_post_check_without_a_boot_id_can_never_pass():
    report = check(expected_fixture(), facts_fixture(), phase=M.PHASE_POST)
    assert report['passed'] is False
    assert report['items']['B11_boot_identity']['reason'] == M.BOOT_ID_NOT_ASSERTED


def test_the_pre_phase_records_a_well_formed_boot_id():
    report = check(expected_fixture(), facts_fixture())
    assert report['items']['B11_boot_identity']['ok'] is True
    assert report['items']['B11_boot_identity']['boot_id'] == BOOT


def test_a_malformed_boot_id_fails():
    report = check(expected_fixture(), facts_fixture(boot='not-a-boot-id'))
    assert 'B11_boot_identity' in report['failures']


# ------------------------------------------------------- unknown facts are not a pass

def test_a_missing_snapshot_is_unknown_not_pass():
    facts = facts_fixture()
    facts.update(snapshot=None, snapshot_ok=False,
                 snapshot_error='snapshot command unsuccessful')
    report = check(expected_fixture(), facts)
    assert report['verdict'] == M.UNKNOWN
    assert report['passed'] is False
    assert report['missing_facts']


def test_a_failed_state_command_is_unknown_not_pass():
    facts = facts_fixture(state_rc=1)
    report = check(expected_fixture(), facts)
    assert report['verdict'] == M.UNKNOWN and report['passed'] is False


def test_a_missing_state_document_is_unknown_not_pass():
    facts = facts_fixture(sample_state=False)
    report = check(expected_fixture(), facts)
    assert report['verdict'] == M.UNKNOWN and report['passed'] is False


def test_a_failed_paths_command_is_unknown_not_pass():
    facts = facts_fixture(paths_rc=2)
    report = check(expected_fixture(), facts)
    assert report['verdict'] == M.UNKNOWN and report['passed'] is False


def test_no_single_missing_fact_yields_a_pass():
    """Drop one fact at a time: every variant must fail or be unknown, never pass."""
    base = facts_fixture()
    variants = []
    for key in ('snapshot', 'state', 'paths'):
        facts = dict(base)
        facts[key] = None
        variants.append((key, facts))
    for key in ('snapshot_ok',):
        facts = dict(base)
        facts[key] = False
        variants.append((key, facts))
    for key in ('state_rc', 'paths_rc'):
        facts = dict(base)
        facts[key] = 1
        variants.append((key, facts))
    for name, facts in variants:
        report = check(expected_fixture(), facts)
        assert report['passed'] is False, name


# ---------------------------------------------------------------- rollback read-back

def test_a_verified_fallback_to_the_recorded_parent_is_a_safe_terminal_state(tmp_path):
    state_file = tmp_path / 'rollback-state.json'
    state_file.write_text(json.dumps({'parent_release': PARENT}), encoding='utf-8')
    report = check(expected_fixture(RELEASE), facts_fixture(PARENT), phase=M.PHASE_POST,
                   expect_boot_id=BOOT, rollback_state=state_file,
                   rollback_expected=expected_fixture(PARENT))
    assert report['verdict'] == M.VERIFIED_SAFE_ROLLBACK
    assert report['board_state'] == 'VERIFIED_SAFE_ROLLBACK'
    assert report['passed'] is False and report['safe_terminal'] is True
    assert report['rollback']['parent_release'] == PARENT
    # the safe verdict comes from the COMPLETE parent gate, not from the identity alone
    assert report['rollback']['failures'] == []
    assert set(report['rollback']['items']) == set(report['items'])


def test_a_rollback_state_naming_another_parent_fails(tmp_path):
    state_file = tmp_path / 'rollback-state.json'
    state_file.write_text(json.dumps({'parent_release': OTHER}), encoding='utf-8')
    report = check(expected_fixture(RELEASE), facts_fixture(PARENT), phase=M.PHASE_POST,
                   expect_boot_id=BOOT, rollback_state=state_file)
    assert report['verdict'] == M.FAILED and report['safe_terminal'] is False


def test_an_unverifiable_rollback_parent_fails(tmp_path):
    state_file = tmp_path / 'rollback-state.json'
    state_file.write_text(json.dumps({'parent_release': PARENT}), encoding='utf-8')
    facts = facts_fixture(PARENT)
    facts['state']['services']['rf-daemon']['ActiveState'] = 'failed'
    report = check(expected_fixture(RELEASE), facts, phase=M.PHASE_POST,
                   expect_boot_id=BOOT, rollback_state=state_file)
    assert report['verdict'] == M.FAILED


@pytest.mark.parametrize('body,expected_parent', [
    ({'parent_release': PARENT}, PARENT),
    ({'release_id': PARENT}, PARENT),
    ({'release': PARENT}, PARENT),
    ({'expected_release': PARENT}, PARENT),
    ({'parent_release': 'not-a-release'}, None),
    ({'other': PARENT}, None),
    ([1, 2], None),
])
def test_the_rollback_parent_is_read_from_the_named_fields(tmp_path, body,
                                                           expected_parent):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(body), encoding='utf-8')
    assert M.rollback_parent(path) == expected_parent


# --------------------------------------------------------------------- CLI refusals

def test_an_unverifiable_candidate_is_a_named_error(tmp_path):
    code = M.main(['--candidate', str(tmp_path / 'nope'), '--phase', 'pre',
                   '--output', str(tmp_path / 'out')])
    assert code == 2
    assert not (tmp_path / 'out').exists()


def test_a_post_check_without_a_boot_id_is_refused_before_any_board_contact(tmp_path,
                                                                           capsys):
    candidate = _real_candidate_or_skip()
    code = M.main(['--candidate', str(candidate), '--phase', 'post',
                   '--output', str(tmp_path / 'out')])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['code'] == M.BOOT_ID_NOT_ASSERTED
    assert not (tmp_path / 'out').exists()


def test_an_existing_output_directory_is_refused(tmp_path, monkeypatch, capsys):
    candidate = _real_candidate_or_skip()
    output = tmp_path / 'out'
    output.mkdir()
    monkeypatch.setattr(M, 'collect', lambda *a, **k: facts_fixture())
    code = M.main(['--candidate', str(candidate), '--phase', 'pre',
                   '--output', str(output)])
    assert code == 2
    assert json.loads(capsys.readouterr().out)['code'] == 'OUTPUT_EXISTS'
    assert list(output.iterdir()) == [], 'a refused run writes nothing'


def test_the_cli_runs_end_to_end_on_fixture_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(M, 'candidate_expectations',
                        lambda candidate: expected_fixture(RELEASE))
    monkeypatch.setattr(M, 'collect', lambda host, logs, timeout: facts_fixture())
    output = tmp_path / 'out'
    code = M.main(['--candidate', 'fixture', '--phase', 'pre', '--output', str(output)])
    assert code == 0
    report = json.loads((output / 'pre-readonly-check.json').read_text(encoding='utf-8'))
    assert report['passed'] is True and report['schema'] == M.SCHEMA
    assert report['scope'].startswith('read-only board check')


def test_an_unreachable_board_is_a_named_unknown_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(M, 'candidate_expectations',
                        lambda candidate: expected_fixture(RELEASE))

    def boom(host, logs, timeout):
        raise TimeoutError('ssh handshake timed out')

    monkeypatch.setattr(M, 'collect', boom)
    output = tmp_path / 'out'
    code = M.main(['--candidate', 'fixture', '--phase', 'pre', '--output', str(output)])
    assert code == 1
    report = json.loads((output / 'pre-readonly-check.json').read_text(encoding='utf-8'))
    assert report['verdict'] == M.UNKNOWN and report['passed'] is False
    assert 'TimeoutError' in report['collect_error']


def test_the_cli_exits_1_on_a_named_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(M, 'candidate_expectations',
                        lambda candidate: expected_fixture(RELEASE))
    monkeypatch.setattr(M, 'collect',
                        lambda host, logs, timeout: facts_fixture(PARENT))
    output = tmp_path / 'out'
    code = M.main(['--candidate', 'fixture', '--phase', 'pre', '--output', str(output)])
    assert code == 1


# ------------------------------------------------------------- derivation & no constants

def _real_candidate_or_skip():
    from pathlib import Path
    candidate = Path('evidence/rf-teaching/s2-46db-parent-proof-03b9b883-2026-10-19/'
                     'work/candidate-03b9b883/03b9b883a61f29605be2c1f3817024d0')
    if not candidate.is_dir():
        pytest.skip('verified parent candidate copy is not in this checkout')
    return candidate


def test_the_expectations_come_from_the_named_release():
    candidate = _real_candidate_or_skip()
    expected = M.candidate_expectations(candidate)
    assert expected['release_id'] == '03b9b883a61f29605be2c1f3817024d0'
    assert expected['software_contract'] == CONTRACT
    assert expected['software_modules'] == 44
    assert expected['baseline']['file_sha256'].startswith('351c52c5')
    assert expected['baseline']['canonical_sha256'].startswith('dd794f0c')


def test_the_module_holds_no_release_id_or_boot_id_constant():
    """The whole point: the release/boot identity is an argument, never a constant."""
    tree = ast.parse(inspect.getsource(M))
    suspicious = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) == 32 and all(c in '0123456789abcdef' for c in node.value):
                suspicious.append(node.value)
            if len(node.value) == 36 and node.value.count('-') == 4:
                suspicious.append(node.value)
    assert suspicious == [], 'release/boot ids must not be baked into the module'


def test_the_module_only_talks_to_a_board_through_one_function():
    tree = ast.parse(inspect.getsource(M))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert imported & {'socket', 'paramiko', 'pysftp', 'requests', 'urllib',
                       'subprocess'} == set()
    from scripts import rf_environment_readonly_check as module
    source = inspect.getsource(module.collect)
    assert 'load_helper' in source and '_run(' in source
    # nothing else in the module is allowed to open a session
    for name, member in vars(module).items():
        if inspect.isfunction(member) and member is not module.collect:
            text = inspect.getsource(member)
            assert 'load_helper' not in text, name
            assert 'paramiko' not in text, name


# =====================================================================================
# B1 (Codex stage-candidate review): a rollback read-back must recompute the COMPLETE
# gate against the verified parent.  Only the expected identity difference (new vs
# parent release id) may be exempted; storage, RF, lock, services, boot, in-flight
# unit and /tmp failures must never be reported as a safe terminal state.
# =====================================================================================

def parent_expected():
    """The verified parent's expectations (same software/baseline/versions, other id)."""
    return expected_fixture(PARENT)


def rollback_state_file(tmp_path, parent=PARENT, name='rollback-state.json'):
    path = tmp_path / name
    path.write_text(json.dumps({'release_id': parent, 'expected_release': parent,
                                'parent_release': parent}), encoding='utf-8')
    return path


def rollback_check(tmp_path, facts, *, expected=None, parent=None):
    """A post-install read-back whose board state is the verified parent."""
    return check(expected or expected_fixture(RELEASE), facts, phase=M.PHASE_POST,
                 expect_boot_id=BOOT,
                 rollback_state=rollback_state_file(tmp_path, parent or PARENT),
                 rollback_expected=parent_expected())


def test_a_clean_rollback_to_the_verified_parent_is_a_safe_terminal_state(tmp_path):
    report = rollback_check(tmp_path, facts_fixture(PARENT))
    assert report['verdict'] == M.VERIFIED_SAFE_ROLLBACK
    assert report['safe_terminal'] is True and report['passed'] is False
    assert report['rollback']['parent_release'] == PARENT
    assert report['rollback']['failures'] == []
    assert report['rollback']['items']['B0_strict_state']['ok'] is True


def test_a_rollback_with_a_storage_fault_is_not_safe(tmp_path):
    facts = facts_fixture(PARENT, storage='[ 12.3] mmc0: error -110 whilst initialising SD card\n')
    report = rollback_check(tmp_path, facts)
    assert report['safe_terminal'] is False
    assert report['verdict'] == M.FAILED
    assert 'B6_storage' in report['rollback']['failures']


def test_a_rollback_with_a_read_only_root_is_not_safe(tmp_path):
    report = rollback_check(tmp_path, facts_fixture(PARENT, mounts=MOUNTS_RO))
    assert report['safe_terminal'] is False
    assert 'B7_mounts_rw' in report['rollback']['failures']


def test_a_rollback_with_an_inflight_deployment_unit_is_not_safe(tmp_path):
    facts = facts_fixture(PARENT, units='rf-trial-x.service loaded failed failed')
    report = rollback_check(tmp_path, facts)
    assert report['safe_terminal'] is False
    assert 'B8_no_inflight' in report['rollback']['failures']


def test_a_rollback_with_a_changed_boot_id_is_not_safe(tmp_path):
    report = rollback_check(tmp_path, facts_fixture(PARENT, boot=BOOT2))
    assert report['safe_terminal'] is False
    assert 'B11_boot_identity' in report['rollback']['failures']


def test_a_rollback_with_tmp_residue_is_not_safe(tmp_path):
    report = rollback_check(tmp_path, facts_fixture(PARENT, residue=['/tmp/rf-trial-x']))
    assert report['safe_terminal'] is False
    assert 'B10_tmp_clean' in report['rollback']['failures']


def test_a_rollback_with_core_asserted_is_not_safe(tmp_path):
    report = rollback_check(tmp_path, facts_fixture(PARENT, status={'core': True}))
    assert report['safe_terminal'] is False
    assert 'B5_rf_safe' in report['rollback']['failures']


def test_a_rollback_with_a_wrong_software_digest_is_not_safe(tmp_path):
    facts = facts_fixture(PARENT)
    facts['state']['software_digest'] = 'f' * 64
    report = rollback_check(tmp_path, facts)
    assert report['safe_terminal'] is False
    assert 'B2_software_map' in report['rollback']['failures']


def test_a_rollback_with_a_wrong_baseline_digest_is_not_safe(tmp_path):
    facts = facts_fixture(PARENT)
    facts['state']['baseline']['canonical_sha256'] = 'f' * 64
    report = rollback_check(tmp_path, facts)
    assert report['safe_terminal'] is False
    assert 'B3_baseline_bytes' in report['rollback']['failures']


def test_a_rollback_with_a_held_lock_is_not_safe(tmp_path):
    facts = facts_fixture(PARENT, lock_lines=['LOCK_PRESENT', 'LOCK_HELD'])
    report = rollback_check(tmp_path, facts)
    assert report['safe_terminal'] is False
    assert 'B9_lock' in report['rollback']['failures']


def test_a_rollback_with_unreadable_facts_is_unknown_not_safe(tmp_path):
    facts = facts_fixture(PARENT, state_rc=1)
    report = rollback_check(tmp_path, facts)
    assert report['verdict'] == M.UNKNOWN
    assert report['safe_terminal'] is None


def test_a_rollback_whose_parent_candidate_does_not_match_the_state_is_not_safe(tmp_path):
    facts = facts_fixture(PARENT)
    report = check(expected_fixture(RELEASE), facts, phase=M.PHASE_POST,
                   expect_boot_id=BOOT,
                   rollback_state=rollback_state_file(tmp_path, OTHER),
                   rollback_expected=parent_expected())
    assert report['safe_terminal'] is False
    assert report['verdict'] == M.FAILED
    assert report['rollback']['error'] == M.ROLLBACK_CANDIDATE_MISMATCH


def test_a_rollback_without_the_parent_candidate_can_never_be_safe(tmp_path):
    """An identity check alone is not a gate: without the parent's release bytes there
    is no way to recompute it, so this is a named FAILED, never safe."""
    report = check(expected_fixture(RELEASE), facts_fixture(PARENT), phase=M.PHASE_POST,
                   expect_boot_id=BOOT,
                   rollback_state=rollback_state_file(tmp_path))
    assert report['safe_terminal'] is False
    assert report['verdict'] == M.FAILED
    assert report['rollback']['error'] == M.ROLLBACK_CANDIDATE_REQUIRED


def test_the_board_running_a_third_release_is_not_a_rollback(tmp_path):
    report = check(expected_fixture(RELEASE), facts_fixture(OTHER),
                   phase=M.PHASE_POST, expect_boot_id=BOOT,
                   rollback_state=rollback_state_file(tmp_path),
                   rollback_expected=parent_expected())
    assert report['safe_terminal'] is False and report['verdict'] == M.FAILED
    assert report['rollback'] is None


# ------------------------------------------------- mode=status_only on the main path

@pytest.mark.parametrize('mode', ['active', 'physical', '', None, 'STATUS_ONLY'])
def test_a_non_status_only_mode_fails_the_main_path(mode):
    facts = facts_fixture(status={'mode': mode})
    report = check(expected_fixture(RELEASE), facts)
    assert 'B5_rf_safe' in report['failures']
    assert report['passed'] is False


def test_status_only_mode_passes_the_main_path():
    report = check(expected_fixture(RELEASE), facts_fixture(status={'mode': 'status_only'}))
    assert report['passed'] is True


def test_a_non_status_only_mode_also_blocks_a_rollback(tmp_path):
    report = rollback_check(tmp_path, facts_fixture(PARENT, status={'mode': 'active'}))
    assert report['safe_terminal'] is False
    assert 'B5_rf_safe' in report['rollback']['failures']


# ------------------------------------------------------------- CLI naming of B1 refusals

def test_the_cli_refuses_a_rollback_state_without_a_parent_candidate(tmp_path, monkeypatch,
                                                                     capsys):
    candidate = _real_candidate_or_skip()
    monkeypatch.setattr(M, 'candidate_expectations',
                        lambda candidate: expected_fixture(RELEASE))
    monkeypatch.setattr(M, 'collect', lambda host, logs, timeout: facts_fixture(PARENT))
    state = rollback_state_file(tmp_path)
    code = M.main(['--candidate', 'fixture', '--phase', 'post', '--expect-boot-id', BOOT,
                   '--rollback-state', str(state), '--output', str(tmp_path / 'out')])
    assert code == 2
    assert json.loads(capsys.readouterr().out)['code'] == M.ROLLBACK_CANDIDATE_REQUIRED


def test_the_cli_refuses_a_parent_candidate_that_disagrees_with_the_state(tmp_path):
    parent = _real_candidate_or_skip()
    state = rollback_state_file(tmp_path, parent=OTHER)
    code = M.main(['--candidate', str(parent), '--phase', 'post',
                   '--expect-boot-id', BOOT, '--rollback-state', str(state),
                   '--rollback-candidate', str(parent), '--output', str(tmp_path / 'out')])
    assert code == 2
