"""HEADROOM-1 candidate activation with the installed release as rollback parent.

Preparation is offline.  Activation never arms RF, never starts a transmission and
never opens a second Overlay; it installs a release and reads the board back.  A
transport failure is retained as UNKNOWN and is never automatically retried.

The gate is deliberately narrow.  It accepts exactly one contract
(`cnn-bpsk-headroom-v1`), exactly the ARM qualification of the candidate it is
about to install, and exactly the release that is installed right now as the
rollback parent -- so an installation cannot be prepared against a candidate
nobody qualified or against a rollback target nobody verified.
"""
import argparse
import base64
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import merged_release as R
from scripts.rf_teaching_deploy import sha, inspect_archive, load_helper, _run
from scripts.rf_teaching_install_state import safe_state, verified_state

CONTRACT = 'cnn-bpsk-headroom-v1'
# Historical rollback parent, retained for old qualification/installation proof.
ROLLBACK_PARENT = 'adfac1f14c3cdb379cb92569a21a9054'
# Independently verified current release after the interrupted installation.
# Also the candidate build parent: every candidate in this contract declares it as
# 'parent_release'.  It stays the historical recovery parent; it is no longer the
# release the read-only checker is required to find installed.
RECOVERED_PARENT = 'b67a8aaab0c5904822580c0647b96fd0'
# The original installation evidence of the release the board runs now: the
# acceptance receipt the reviewed 2026-10-13 activation wrote, and the installer
# receipt that acceptance receipt binds by sha256.  The read-only checker resolves
# the release it may expect from these documents instead of from a constant.
ACCEPTANCE_EVIDENCE = ('evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/'
                       'acceptance-receipt-b82b141e.json')
INSTALLER_RECEIPT_EVIDENCE = ('evidence/rf-teaching/s2-parent-b82-proof-2026-10-15/'
                              'install-receipt.json')
INSTALL_ACCEPTANCE_SCHEMA = 'headroom-install-acceptance-v1'
INSTALL_ACCEPTANCE_VERDICT = 'VERIFIED_SAFE_NEW_RELEASE'
INSTALLER_RECEIPT_OUTCOME = 'INSTALLED_VERIFIED'
INSTALLER_RECEIPT_STATE = 'VERIFIED_SAFE_NEW_RELEASE'
RELEASE_PATH_PREFIX = '/home/xilinx/overlays/awg_merged/releases/'
# Read-back documents a reviewed installer leaves beside its receipt as the proof
# of what it just installed.  The receipt names the one it wrote by sha256, so the
# third route below reads the shape that is there instead of a second fixed id.
INSTALL_READBACKS = ('post-state.json', 'readback-state-new.json')
# Fourth parent route: a release the reviewed activation orchestration switched to
# current instead of installing.  Its chain is complete but it has no
# INSTALLED_VERIFIED receipt, and the route is only reachable when a caller names
# this evidence explicitly -- nothing falls back to it.
ACTIVATION_EVIDENCE = 'evidence/rf-teaching/s2-headroom-activate-2026-10-17'
ACTIVATION_PHASES = ('guards_passed', 'pre_state_written', 'stopped_units', 'switched',
                     'units_started', 'active_release_published', 'accepted',
                     'verified_new', 'exited')
ACTIVATION_TERMINAL = 'ACTIVATION_ACCEPTED'
ACTIVATION_REFUSED_TERMINALS = ('ACTIVATION_ROLLED_BACK', 'ACTIVATION_RECOVERY_REQUIRED')
ACTIVATION_SOFTWARE_MODULES = 46
ACTIVATION_ARTIFACTS = ('activation.jsonl', 'acceptance-report.json', 'post-state.json',
                        'pre-state.json')
ACTIVATION_ACCEPTANCE_CHECKS = ('a_installed_release_manifest', 'b_import_and_digest',
                                'c_real_archived_frame_accepted', 'd_allow_set_bit_probes')
ACTIVATION_READBACK_CHECKS = (
    'snapshot_valid', 'kernel_storage_faults_empty', 'current_is_new',
    'active_release_is_new', 'manifest_release_id_is_new', 'software_contract',
    'software_modules_46', 'new_headroom_digest', 'new_replay_digest',
    'new_daemon_digest', 'units_match_release', 'boot_id_unchanged_since_preflight',
    'no_active_activation_units', 'no_pending_systemd_jobs',
    'rf_not_armed_not_tx_not_rx', 'root_and_boot_rw')
ACTIVATION_FILES = ('board-download/activation.jsonl',
                    'board-download/acceptance-report.json',
                    'board-download/post-state.json',
                    'board-download/pre-state.json',
                    'logs/unit-journal.log',
                    'work/artifact-collection.json',
                    'work/target-consistency.json',
                    'readback/readback-summary.json',
                    'readback/final-confirm.json',
                    'readback/readback.log')
# `scripts/rf_teaching_install.sh` refuses any staging path outside its own
# prefix with exit 2 before it touches the board, so activation must be staged
# where that reviewed guard expects it.  The gate is the installer's, not ours.
# Fifth parent route: a release the reviewed non-emitting trial deployment installed
# and accepted.  Its chain is complete -- one dispatch, on-board acceptance, an
# independent read-only read-back -- but it carries no INSTALLED_VERIFIED receipt,
# and the route is only reachable when a caller names this evidence explicitly.
# Nothing falls back to it, and it never stands in for an installation receipt.
TRIAL_EVIDENCE = 'evidence/rf-teaching/s2-46db-trial-exec-2026-10-17'
TRIAL_TERMINAL = 'TRIAL_ACCEPTED'
TRIAL_INSTALL_TERMINAL = 'INSTALL_ACCEPTED'
TRIAL_ACCEPTANCE_TERMINAL = 'TRIAL_ACCEPTANCE PASS'
TRIAL_CHECK_TERMINAL = 'TRIAL_CHECK_BEGIN'
TRIAL_REFUSED_TERMINALS = ('TRIAL_FAILED', 'TRIAL_ROLLED_BACK', 'TRIAL_RECOVERY_REQUIRED')
TRIAL_ACCEPTANCE_SCHEMA = 'environment-trial-acceptance-v1'
TRIAL_ACCEPTANCE_CHECKS = ('a_release_identity', 'b_domain_constants',
                           'c_baseline_load_not_formal', 'd_services', 'e_rf_closed',
                           'f_archived_iq_smoke')
TRIAL_DOMAIN_KEYS = ('rf_bpsk_receiver_VERSION', 'rf_environment_FIXED_RF',
                     'rf_environment_VERSION', 'rf_status_client_ATTENUATION_MDB',
                     'rf_teaching_profile_ATTENUATION_MDB', 'ui_model_ATTENUATION_MDB')
TRIAL_ATTENUATION_KEYS = ('rf_status_client_ATTENUATION_MDB',
                          'rf_teaching_profile_ATTENUATION_MDB', 'ui_model_ATTENUATION_MDB')
TRIAL_RECEIPT_SCHEMA = 'rf-trial-exec-receipt-v1'
TRIAL_STAGE_FILES = ('candidate.tgz', 'install.sh', 'install_state.py', 'trial-install.sh',
                     'acceptance.py', 'rf_headroom_replay.py',
                     'frames/attempt-2/header.json', 'frames/attempt-2/frame.iq')
TRIAL_BOARD_LISTED = ('candidate.tgz', 'install.sh', 'install_state.py', 'trial-install.sh',
                      'acceptance.py', 'rf_headroom_replay.py')
TRIAL_BOARD_DOWNLOADS = ('acceptance-report.json', 'pre-state.json', 'post-state.json',
                         'trial-state.json', 'pynq-status.before', 'rf-daemon.before')
TRIAL_ROLLBACK_ARTIFACTS = ('rollback-state.json', 'trial-rollback-state.json')
TRIAL_FILES = ('trial.json', 'receipt.json', 'dispatch-intent.json', 'cleanup.json',
               'logs/dispatch-journal.log', 'logs/dispatch-summary.json',
               'logs/dispatch.result.json', 'logs/readback-summary.json',
               'logs/final-summary.json', 'board-download/acceptance-report.json',
               'board-download/pre-state.json', 'board-download/post-state.json',
               'board-download/trial-state.json', 'board-download/pynq-status.before',
               'board-download/rf-daemon.before',
               'readback/board-download-manifest.json',
               'stage/stage-local-sha256.json', 'stage/staged-sha256.json',
               'prepare/candidate.tgz', 'prepare/install.sh', 'prepare/install_state.py')
# The symlink the installer and the acceptance both publish, as the trial records it.
CURRENT_RELEASE_PATH = RELEASE_PATH_PREFIX.rsplit('/releases/', 1)[0] + '/current'


def _trial_document(path, what):
    """Read one trial-evidence document, refusing an unreadable one by name."""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('trial acceptance evidence %s unreadable' % what) from exc


def _canonical_digest(value):
    """The canonical JSON digest rule the release's own environment baseline uses."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False)
                          .encode('ascii')).hexdigest()


def _trial_number(value):
    """A real JSON number; a boolean is not a number here even though Python says so."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _trial_services_ok(services):
    """Both board services active/running with a live PID and no restart."""
    if type(services) is not dict or set(services) != {'pynq-status', 'rf-daemon'}:
        return False
    for unit in services.values():
        if (type(unit) is not dict or unit.get('ActiveState') != 'active'
                or unit.get('SubState') != 'running' or str(unit.get('NRestarts')) != '0'
                or not str(unit.get('MainPID')).isdigit() or int(unit.get('MainPID')) <= 0):
            return False
    return True


def _trial_sha256sums(text, marker):
    """One sha256sum section of a read-back log, as {absolute path: digest}."""
    lines = (text or '').splitlines() if type(text) is str else []
    if marker not in lines:
        return None
    digests = {}
    for line in lines[lines.index(marker) + 1:]:
        if line.startswith('=='):
            break
        match = re.search(r'([0-9a-f]{64})\s+(\S+)\s*$', line)
        if match:
            digests[match.group(2)] = match.group(1)
    return digests


def _trial_stage_map(evidence, stage_name='stage', local_map='stage-local-sha256.json',
                     board_map='staged-sha256.json', prefix='trial acceptance evidence',
                     board_path=None):
    """The staged payload on disk now, checked against the map the trial recorded.

    The staging tree is what the installer consumed, so every member is recomputed here
    and the board's own re-read of the same tree (staged-sha256.json) must equal it: a
    stored map alone would be a claim, not evidence.  The 2026-10-18 package keeps the
    same two maps one directory deeper, so the caller names the directory and the two
    file names; the defaults are the frozen route's own layout.
    """
    stage = Path(evidence) / stage_name
    # The two digest maps are evidence about the payload, not payload members: the
    # trial wrote them after hashing the tree, so they are excluded from the map.
    maps = (local_map, board_map)
    names = sorted(path.relative_to(stage).as_posix() for path in stage.rglob('*')
                   if path.is_file() and path.name not in maps)
    actual = {name: sha(stage / name) for name in names}
    recorded = _trial_document(stage / local_map, 'local stage map')
    if recorded != actual:
        raise ValueError('%s stage does not match its local map' % prefix)
    for name in TRIAL_STAGE_FILES:
        if name not in actual:
            raise ValueError('%s staged file missing: %s' % (prefix, name))
    # The 2026-10-18 package keeps the board's re-read of the staged tree beside the
    # trial intent instead of inside the stage directory, so the caller may name it.
    staged = _trial_document(Path(evidence) / board_path if board_path
                             else stage / board_map, 'board stage map')
    if (staged.get('files') != actual or staged.get('identical_to_local') is not True
            or staged.get('exit_code') != 0):
        raise ValueError('%s board stage differs from the local bytes' % prefix)
    for key in ('upload', 'durable', 'alias'):
        if type(staged.get(key)) is not str or not staged[key]:
            raise ValueError('%s board stage path missing: %s' % (prefix, key))
    if staged.get('alias_target') != staged['durable'] + '/stage':
        raise ValueError('%s stage alias does not name the durable tree' % prefix)
    return actual, staged

REMOTE_PREFIX = '/tmp/rf-teaching-install-'
TOOLS = {'rf_teaching_install.sh': 'install.sh',
         'rf_teaching_install_state.py': 'install_state.py',
         'rf_headroom_job.py': 'job.py'}
# The two research modules this contract adds; a release that carries one and not
# the other cannot run a study whose OFF guard is a replay.
RESEARCH_MODULES = ('rf_headroom.py', 'rf_headroom_replay.py')
# The eight stop classes the reviewed fake research chain exercises on both host
# and ARM, under the harness's own case names.  `qualify-runtime.py` requires the
# five it names as a subset; the frozen receipt carries all eight.
CHAIN_CASES = ('full-five', 'cancel', 'off-false-positive', 'rails',
               'missing-capability', 'wrong-id', 'no-iq', 'partial-iq')
# Observer deadline only. The independent systemd installer has no timeout.
# Reaching this deadline retains the same attempt and requires later observation.
INSTALL_TIMEOUT = 240
JOURNAL_NAME = 'attempt.jsonl'
OUTCOME_INSTALLED = 'INSTALLED_VERIFIED'
OUTCOME_ROLLBACK = 'SAFE_ROLLBACK_VERIFIED'
OUTCOME_UNEXPECTED = 'UNEXPECTED_STATE'
OUTCOME_INCOMPLETE = 'UNKNOWN_READBACK_INCOMPLETE'
OUTCOME_ABORTED = 'ABORTED_STORAGE_FAULT'
OUTCOME_ABORTED_PREFLIGHT = 'ABORTED_PREFLIGHT_UNKNOWN'
OUTCOMES = (OUTCOME_INSTALLED, OUTCOME_ROLLBACK, OUTCOME_UNEXPECTED, OUTCOME_INCOMPLETE,
            OUTCOME_ABORTED, OUTCOME_ABORTED_PREFLIGHT)
# Substrings that mark a kernel storage fault on this board.  Kept here so the
# pre-flight snapshot, the post-flight read-back and any later report agree.
KERNEL_FAULT_KEYS = ('timeout waiting for hardware interrupt', 'adma err', 'i/o error',
                     'buffer i/o error', 'ext4-fs error', 'mmc0: error')


def chain_evidence_ok(receipt, arm_result, host_result):
    """Every stop class ran on both platforms, and the receipt is their report.

    Equivalence between host and ARM is the qualifier's record comparison
    (`chain_parity_mismatches`), not byte-identical artifacts: the same computed
    value can serialize with a differing final bit on the two platforms.  What is
    enforced here is content rather than shape.  Both runs must report success,
    must carry one passing record per stop class, must be labelled simulation
    with no hardware touched, and the receipt's two inventories must equal the
    `evidence_sha256` maps of the two chain results whose own digests the gate
    has already checked.  Without that last binding a receipt could rename all 96
    digests, drop 88 artifacts and plant eight invented keys, and still look
    complete to a check that only compared key sets.
    """
    chain = receipt.get('chain_artifacts_sha256') or {}
    host = receipt.get('chain_host_artifacts_sha256') or {}
    if not chain or set(chain) != set(host):
        return False
    if chain != (arm_result.get('evidence_sha256') or {}):
        return False
    if host != (host_result.get('evidence_sha256') or {}):
        return False
    for result in (arm_result, host_result):
        if (result.get('passed') is not True or result.get('simulation') is not True
                or result.get('hardware_touched') is not False
                or result.get('new_rf_frames') != 0
                or result.get('release_id') != receipt.get('release_id')):
            return False
        records = result.get('records')
        if type(records) is not dict or set(records) != set(CHAIN_CASES):
            return False
        if any(not isinstance(value, dict) or value.get('passed') is not True
               for value in records.values()):
            return False
    return True


def verify_installed_parent(parent, parent_install, proof):
    """Third route: the release installed right now, accepted on evidence shape.

    The rollback target is whatever the board has installed, which is not a fixed
    id -- it was the environment candidate 399b on 2026-10-15 and will be another
    release later.  A name proves nothing on its own, so the chain of documents
    the reviewed installer wrote is rebound here in the order it was written:
    the receipt must report the release id of the candidate directory this run
    reads, report it installed and verified safe, and name by sha256 exactly one
    read-back document; that document must be present beside the receipt, hash to
    the digest the receipt bound, declare the software contract of that candidate
    directory and satisfy verified_state -- the very predicate the installer uses.

    A receipt for another release, a digest left over from an earlier installation,
    a missing or rehashed read-back, an unsafe state or a state written under
    another contract all refuse the parent.
    """
    bound = proof.get('evidence_sha256')
    names = [name for name in INSTALL_READBACKS
             if type(bound) is dict and isinstance(bound.get(name), str)]
    if (proof.get('installed') is not True or proof.get('release_id') != parent['release_id']
            or proof.get('board_state') != 'VERIFIED_SAFE_NEW_RELEASE' or len(names) != 1):
        raise ValueError('installed rollback parent installation unverified')
    readback = parent_install.parent / names[0]
    if not readback.is_file() or sha(readback) != bound[names[0]]:
        raise ValueError('installed rollback parent read-back unverified')
    state = json.loads(readback.read_text(encoding='utf-8'))
    # The checker records the release it was asked to read; a read-back taken for
    # another release (or renamed afterwards) is not this release's proof.
    if state.get('expected_release') != parent['release_id']:
        raise ValueError('installed rollback parent read-back was not taken for this release')
    if state.get('software_contract') != parent.get('software_contract'):
        raise ValueError('installed rollback parent read-back contract unverified')
    if not verified_state(state, parent['release_id']):
        raise ValueError('installed rollback parent read-back state not verified safe')
    return parent['release_id']


def _activation_json(path, what):
    """Read one activation-evidence document, refusing an unreadable one by name."""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('activation evidence %s unreadable' % what) from exc


def verify_installed_parent_via_activation_evidence(parent, activation_evidence):
    """Fourth route: the release the reviewed activation orchestration switched to.

    On 2026-10-17 the board's current release was switched by the reviewed activation
    job rather than installed by the reviewed installer, so it has no
    INSTALLED_VERIFIED receipt and must not be given one by renaming anything.  What
    it does have is its own chain, rebuilt here in the order it was written:

      activation.jsonl   the phase chain, every phase naming this release, ending
                         with the switch verified and rc 0
      unit journal       the ACTIVATION_ACCEPTED marker the job echoes only after
                         its own acceptance passed, and no rollback/recovery marker
      acceptance report  the four acceptance checks, all passing, with the 46-module
                         inventory, the contract and the rf_headroom.py digest
      post-state.json    the safe state the job itself verified, by verified_state
      target consistency the whole release directory compared file by file against
                         the host candidate, with no difference and no extra member
      readback/          the independent read-only read-back on a fresh session and
                         its final confirmation, plus their phase-chain binding

      every one of them is bound to the candidate directory this run verified:
      release id, software contract, the 46-module inventory, the headroom/replay
      module digests and the native daemon digest.  A self-consistent rewrite inside
      the evidence directory still refuses, because the digests are read from the
      verified manifest, not from the documents that claim them.

    Refusals are named: missing evidence, phase chain, terminal marker, acceptance
    report, post-state, tree comparison, independent read-back, final confirmation,
    artifact digest or identity disagreement.  Nothing here widens the installer
    route, and this function never writes or upgrades a receipt.
    """
    evidence = Path(activation_evidence)
    if not evidence.is_dir():
        raise ValueError('activation evidence directory required')
    release = parent['release_id']
    release_path = RELEASE_PATH_PREFIX + release
    if (parent.get('software_contract') != CONTRACT
            or type(parent.get('software')) is not dict
            or len(parent['software']) != ACTIVATION_SOFTWARE_MODULES
            or 'rf_headroom.py' not in parent['software']
            or 'rf_headroom_replay.py' not in parent['software']
            or type(parent.get('daemon_sha256')) is not str):
        raise ValueError('activation evidence route requires a 46-module HEADROOM release')
    files = {name: evidence / name for name in ACTIVATION_FILES}
    for name, path in files.items():
        if not path.is_file():
            raise ValueError('activation evidence %s missing' % name)

    # 1. The phase chain the job appended as it ran, in its own order.
    entries = []
    for line in files['board-download/activation.jsonl'].read_text(
            encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError as exc:
            raise ValueError('activation evidence phase chain unreadable') from exc
    if (not all(type(entry) is dict for entry in entries)
            or tuple(entry.get('phase') for entry in entries) != ACTIVATION_PHASES):
        raise ValueError('activation evidence phase chain incomplete or out of order')
    phases = {entry['phase']: entry for entry in entries}
    previous = phases['guards_passed'].get('parent')
    if (phases['guards_passed'].get('new') != release
            or type(previous) is not str or not previous or previous == release
            or phases['pre_state_written'].get('current') != RELEASE_PATH_PREFIX + previous
            or any(phases[name].get('current') != release_path
                   for name in ('switched', 'verified_new', 'exited'))
            or str(phases['active_release_published'].get('tries')) != '0'
            or str(phases['exited'].get('rc')) != '0'):
        raise ValueError('activation evidence phase chain does not name this release')

    # 1b. The pre-state the job wrote before switching is bound by the artifact
    #     collection, but a self-consistent rewrite can rebind those bytes; what it
    #     cannot forge is agreement with the phase chain it was written from.
    pre = _activation_json(files['board-download/pre-state.json'], 'pre-state')
    if (pre.get('expected_release') != previous
            or pre.get('release_path') != RELEASE_PATH_PREFIX + previous):
        raise ValueError('activation evidence pre-state does not name the parent it replaced')

    # 2. The terminal marker, written only after the job's own acceptance passed.
    journal = files['logs/unit-journal.log'].read_text(encoding='utf-8', errors='replace')
    if journal.count(ACTIVATION_TERMINAL) != 1:
        raise ValueError('activation evidence terminal marker missing or repeated')
    if any(marker in journal for marker in ACTIVATION_REFUSED_TERMINALS):
        raise ValueError('activation evidence records a rolled-back activation')
    if release not in journal:
        raise ValueError('activation evidence journal does not name this release')

    # 3. The acceptance report: four checks, all passing, bound to this release.
    acceptance = _activation_json(files['board-download/acceptance-report.json'],
                                  'acceptance report')
    checks = acceptance.get('checks')
    if (acceptance.get('schema') != 'headroom-trial-acceptance-v1'
            or acceptance.get('ok') is not True
            or acceptance.get('expected_release') != release
            or type(checks) is not list or not checks
            or any(type(check) is not dict or check.get('ok') is not True for check in checks)):
        raise ValueError('activation evidence acceptance report unverified')
    details = {check.get('name'): (check.get('detail') or {}) for check in checks}
    if not set(ACTIVATION_ACCEPTANCE_CHECKS) <= set(details):
        raise ValueError('activation evidence acceptance report checks incomplete')
    acceptance_state = acceptance.get('state') or {}
    if (details['a_installed_release_manifest'].get('release_id') != release
            or details['a_installed_release_manifest'].get('software_contract')
               != parent['software_contract']
            or details['a_installed_release_manifest'].get('software_modules')
               != ACTIVATION_SOFTWARE_MODULES
            or details['b_import_and_digest'].get('sha256') != parent['software']['rf_headroom.py']
            or acceptance_state.get('release_id_from_name') != release
            or acceptance_state.get('release_path') != release_path):
        raise ValueError('activation evidence acceptance report disagrees with the release')

    # 4. The post-state the job itself verified, by the installer's own predicate.
    post = _activation_json(files['board-download/post-state.json'], 'post-state')
    if (post.get('expected_release') != release or post.get('release_path') != release_path
            or post.get('software_contract') != parent['software_contract']
            or not verified_state(post, release)):
        raise ValueError('activation evidence post-state is not a verified safe state '
                         'of this release')

    # 5. The whole release tree, compared file by file against the host candidate.
    consistency = _activation_json(files['work/target-consistency.json'],
                                   'target consistency')
    consistency_checks = consistency.get('checks')
    if (consistency.get('release_id') != release
            or consistency.get('board_path') != release_path
            or consistency.get('consistent') is not True
            or consistency.get('missing_on_board') != []
            or consistency.get('extra_on_board') != []
            or consistency.get('digest_differences') != []
            or consistency.get('unexplained_differences') != []
            or consistency.get('pycache_differences') != []
            or type(consistency.get('board_files')) is not int
            or consistency.get('board_files') != consistency.get('candidate_files')
            or consistency.get('board_files') <= 0
            or consistency.get('board_has_pycache_dir') is not False
            or type(consistency_checks) is not list or not consistency_checks
            or any(type(check) is not dict or check.get('ok') is not True
                   for check in consistency_checks)):
        raise ValueError('activation evidence release tree is not identical on board '
                         'and host')

    # 6. The independent read-only read-back on a fresh session, after /tmp cleanup.
    readback = _activation_json(files['readback/readback-summary.json'],
                                'read-back summary')
    readback_checks = readback.get('checks')
    if (readback.get('schema') != 'headroom-activation-readback-v1'
            or readback.get('ok') is not True or readback.get('failed') != []
            or readback.get('jobs') != [] or readback.get('board_contact') != 'read-only'
            or readback.get('candidate') != release
            or type(readback_checks) is not list or not readback_checks
            or any(type(check) is not dict or check.get('ok') is not True
                   for check in readback_checks)):
        raise ValueError('activation evidence independent read-back unverified')
    readback_details = {check.get('name'): check.get('detail') for check in readback_checks}
    if not set(ACTIVATION_READBACK_CHECKS) <= set(readback_details):
        raise ValueError('activation evidence independent read-back checks incomplete')

    def reported(name, key='board'):
        detail = readback_details.get(name)
        value = detail.get(key) if type(detail) is dict else None
        if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
            raise ValueError('activation evidence read-back digest missing: ' + name)
        return value

    headroom = parent['software']['rf_headroom.py']
    replay = parent['software']['rf_headroom_replay.py']
    daemon = parent['daemon_sha256']
    recorded = readback.get('state') or {}
    if (recorded.get('release_id') != release
            or recorded.get('software_contract') != parent['software_contract']
            or recorded.get('software_modules') != ACTIVATION_SOFTWARE_MODULES
            or recorded.get('rf_headroom') != headroom
            or recorded.get('rf_headroom_replay') != replay
            or recorded.get('daemon_sha256') != daemon
            or readback_details.get('software_modules_46') != ACTIVATION_SOFTWARE_MODULES
            or readback_details.get('software_contract') != parent['software_contract']
            or reported('new_headroom_digest') != headroom
            or reported('new_headroom_digest', 'manifest') != headroom
            or reported('new_replay_digest') != replay
            or reported('new_replay_digest', 'manifest') != replay
            or reported('new_daemon_digest') != daemon
            or reported('new_daemon_digest', 'manifest') != daemon):
        raise ValueError('activation evidence read-back disagrees with the verified release')
    reported_paths = readback.get('paths') or {}
    if (reported_paths.get('NEW_HEADROOM') != headroom
            or reported_paths.get('NEW_REPLAY') != replay
            or reported_paths.get('NEW_DAEMON') != daemon
            or reported_paths.get('NEW_MEMBERS') != str(consistency['board_files'])
            or reported_paths.get('NEW_PYCACHE') != '0'
            or reported_paths.get('NEW_EXISTS') != 'yes'
            or reported_paths.get('NEW_ISDIR') != 'yes'
            or reported_paths.get('NEW_ISLINK') != 'no'):
        raise ValueError('activation evidence read-back paths disagree with the release')

    # 7. The final confirmation on another fresh connection, same boot, same release.
    final = _activation_json(files['readback/final-confirm.json'],
                             'read-back final confirm')
    final_data = final.get('data') or {}
    final_units = final_data.get('units') or {}
    if (final.get('schema') != 'headroom-activation-final-confirm-v1'
            or final.get('ok') is not True or final.get('failed') != []
            or final_data.get('current') != release or final_data.get('active') != release
            or final_data.get('boot_id') != readback.get('boot_id')
            or set(final_units) != {'pynq-status', 'rf-daemon'}
            or any(type(unit) is not list
                   or 'ActiveState=active' not in unit or 'SubState=running' not in unit
                   for unit in final_units.values())):
        raise ValueError('activation evidence final confirmation unverified')

    # 8. The collection record binds the bytes of the four downloaded artifacts.
    collection = _activation_json(files['work/artifact-collection.json'],
                                  'artifact collection')
    downloaded = collection.get('downloaded') or {}
    if (collection.get('schema') != 'headroom-activation-artifact-collection-v1'
            or collection.get('board_contact') != 'read-only'
            or collection.get('listing_exit_code') != 0
            or collection.get('rollback_state_absent') is not True
            or collection.get('absent') != ['rollback-state.json']
            or collection.get('present') != []
            or not set(ACTIVATION_ARTIFACTS) <= set(downloaded)):
        raise ValueError('activation evidence artifact collection incomplete')
    for name in ACTIVATION_ARTIFACTS:
        bound = downloaded.get(name) or {}
        path = files['board-download/' + name]
        if bound.get('sha256') != sha(path) or bound.get('bytes') != path.stat().st_size:
            raise ValueError('activation evidence artifact digest differs: ' + name)

    return dict(release_id=release, source='activation-evidence',
                software_contract=parent['software_contract'],
                software_modules=len(parent['software']),
                evidence_dir=str(evidence.resolve()),
                evidence_sha256={name: sha(path) for name, path in sorted(files.items())})


def verify_installed_parent_via_trial_acceptance_evidence(parent, trial_evidence,
                                                          parent_candidate=None):
    """Fifth route: the release the reviewed non-emitting trial deployment installed.

    On 2026-10-17 the board's current release was installed by the trial transaction --
    the reviewed installer, a bounded on-board acceptance and then an independent
    read-only read-back -- so it has no INSTALLED_VERIFIED receipt and must not be
    given one by renaming anything.  Its own chain is rebuilt here, in the order it
    was written:

      logs/dispatch-journal.log  exactly one TRIAL_ACCEPTED line naming this release,
                                 with one bounded TRIAL_CHECK_BEGIN, one
                                 INSTALL_ACCEPTED and one TRIAL_ACCEPTANCE PASS before
                                 it, and no TRIAL_FAILED / TRIAL_ROLLED_BACK /
                                 TRIAL_RECOVERY_REQUIRED
      dispatch-intent.json       the one dispatch recorded before it happened, and the
                                 systemd result of that same unit
      board-download/            the six acceptance checks, the pre-state it replaced
                                 and the post-state it reached, both by verified_state
      logs/readback-summary.json the independent read-only read-back: release
                                 identity, manifest bytes, software digest, domain
                                 constants, baseline bytes and every check
      logs/final-summary.json    the same read-back repeated after the staging cleanup
      readback/...manifest.json  the downloaded board bytes, re-hashed against disk
      stage/*                    the staged payload, re-hashed against its own map
      receipt.json               the trial receipt, which binds all of those artifacts
                                 by sha256

    Every identity and digest must agree with the candidate directory this run
    verified -- release id, contract, module inventory, canonical software digest,
    build id, daemon and protocol version, manifest bytes, daemon bytes and the
    baseline file -- so a self-consistent rewrite inside the evidence directory still
    refuses: the values are read from the verified candidate, not from the documents
    that claim them.  The route refuses any other release, a trial that failed,
    rolled back or required recovery, a trial whose staged bytes changed, a missing
    independent read-back, and a read-back that disagrees with the release.  Nothing
    here writes, renames or upgrades a receipt.
    """
    evidence = Path(trial_evidence)
    if not evidence.is_dir():
        raise ValueError('trial acceptance evidence directory required')
    if parent_candidate is None:
        raise ValueError('trial acceptance evidence route requires the parent candidate')
    candidate = Path(parent_candidate)
    release = parent['release_id']
    release_path = RELEASE_PATH_PREFIX + release
    if (type(parent.get('software')) is not dict or not parent['software']
            or type(parent.get('software_contract')) is not str
            or type(parent.get('daemon_sha256')) is not str
            or type(parent.get('build_id')) is not str
            or type(parent.get('daemon_version')) is not str
            or parent.get('protocol_version') != 1):
        raise ValueError('trial acceptance evidence route requires a verified software release')
    software = parent['software']
    canonical = _canonical_digest(software)
    # The candidate's own bytes are the truth every claim below is compared with.
    manifest_sha = sha(candidate / 'merged_release_manifest.json')
    if sha(candidate / 'rf_daemon') != parent['daemon_sha256']:
        raise ValueError('trial acceptance evidence parent daemon differs from its manifest')
    baseline = _trial_document(candidate / 'rf_environment_baseline.json', 'parent baseline')
    baseline_sha = sha(candidate / 'rf_environment_baseline.json')
    baseline_body = {key: value for key, value in baseline.items() if key != 'sha256'}
    if (baseline.get('sha256') != _canonical_digest(baseline_body)
            or type(baseline.get('rf_config')) is not dict
            or type(baseline.get('sources')) is not list or not baseline['sources']
            or type(baseline.get('spectrum_envelope')) is not list):
        raise ValueError('trial acceptance evidence parent baseline is not canonical')
    files = {name: evidence / name for name in TRIAL_FILES}
    for name, path in files.items():
        if not path.is_file():
            raise ValueError('trial acceptance evidence %s missing' % name)
    stage, staged = _trial_stage_map(evidence)
    for name in ('candidate.tgz', 'install.sh', 'install_state.py'):
        if sha(files['prepare/' + name]) != stage[name]:
            raise ValueError('trial acceptance evidence prepared payload differs from the stage')

    # 1. The trial intent: one release, one parent, one unit, one staged archive.
    trial = _trial_document(files['trial.json'], 'trial intent')
    trial_parent = trial.get('parent_release')
    budget = trial.get('acceptance_budget_seconds')
    if (trial.get('release_id') != release
            or type(trial_parent) is not str
            or re.fullmatch('[0-9a-f]{32}', trial_parent) is None or trial_parent == release
            or trial.get('files') != stage
            or trial.get('archive_sha') != stage['candidate.tgz']
            or not _trial_number(budget) or not 1 <= budget <= 300
            or type(trial.get('unit')) is not str or not trial['unit']
            or trial['unit'] != 'rf-trial-' + str(trial.get('trial_id'))
            or type(trial.get('boot_id')) is not str
            or re.fullmatch('[0-9a-f-]{36}', trial['boot_id']) is None
            or (trial.get('reviewed') or {}).get('acceptance_sha256') != stage['acceptance.py']
            or (trial.get('reviewed') or {}).get('replay_sha256')
               != stage['rf_headroom_replay.py']
            or (trial.get('reviewed') or {}).get('trial_install_sha256')
               != stage['trial-install.sh']
            or type(trial.get('rollback_plan')) is not str
            or stage['install_state.py'] not in trial['rollback_plan']
            or type(trial.get('dispatch')) is not str or not trial['dispatch']):
        raise ValueError('trial acceptance evidence intent does not name this release')
    for key in ('upload', 'durable', 'alias'):
        if trial.get(key) != staged[key]:
            raise ValueError('trial acceptance evidence intent and stage paths disagree')

    # 2. The journal: the acceptance happened exactly once and named this release.
    journal = files['logs/dispatch-journal.log'].read_text(encoding='utf-8', errors='replace')
    accepted = TRIAL_TERMINAL + ' release=' + release
    if any(marker in journal for marker in TRIAL_REFUSED_TERMINALS):
        raise ValueError('trial acceptance evidence records a failed or rolled-back trial')
    if (journal.count(TRIAL_TERMINAL) != 1 or journal.count(accepted) != 1
            or journal.count(TRIAL_CHECK_TERMINAL) != 1
            or journal.count(TRIAL_INSTALL_TERMINAL) != 1
            or journal.count(TRIAL_ACCEPTANCE_TERMINAL) != 1):
        raise ValueError('trial acceptance evidence terminal marker missing, repeated or '
                         'not this release')
    check = re.search(r'TRIAL_CHECK_BEGIN seconds=(\d+)', journal)
    if check is None or int(check.group(1)) != budget:
        raise ValueError('trial acceptance evidence bounded acceptance budget changed')
    if not (journal.index(TRIAL_INSTALL_TERMINAL) < journal.index(TRIAL_CHECK_TERMINAL)
            < journal.index(TRIAL_ACCEPTANCE_TERMINAL) < journal.index(accepted)):
        raise ValueError('trial acceptance evidence journal is out of order')
    if trial['unit'] not in journal or trial_parent not in journal:
        raise ValueError('trial acceptance evidence journal does not name this trial')

    # 3. The one dispatch: recorded before it ran, and successful in systemd's report.
    dispatch_summary = _trial_document(files['logs/dispatch-summary.json'], 'dispatch summary')
    dispatch_result = _trial_document(files['logs/dispatch.result.json'], 'dispatch result')
    dispatch_intent = _trial_document(files['dispatch-intent.json'], 'dispatch intent')
    dispatch = dispatch_summary.get('dispatch') or {}
    intent = dispatch_summary.get('intent') or {}
    unit_text = dispatch_summary.get('unit')
    unit_fields = {}
    for line in (unit_text or '').splitlines() if type(unit_text) is str else []:
        if '=' in line:
            unit_key, unit_value = line.split('=', 1)
            unit_fields[unit_key.strip()] = unit_value.strip()
    if (type(intent) is not dict or dispatch_intent != intent
            or intent.get('unit') != trial['unit'] or intent.get('command') != trial['dispatch']
            or intent.get('release_id') != release or intent.get('parent_release') != trial_parent
            or intent.get('archive_sha') != stage['candidate.tgz']
            or intent.get('boot_id') != trial['boot_id']
            or not _trial_number(intent.get('acceptance_budget_seconds'))
            or intent.get('acceptance_budget_seconds') != budget
            or intent.get('staged') != staged
            or type(intent.get('policy')) is not str
            or 'single dispatch' not in intent['policy']):
        raise ValueError('trial acceptance evidence dispatch intent unverified')
    # The one command names the staged entry point, the staged acceptance script, the
    # release, the parent and the archive it installs -- all of them by the digests
    # and paths this route just recomputed.
    if ('--unit=' + trial['unit'] not in trial['dispatch']
            or staged['alias'] + '/trial-install.sh' not in trial['dispatch']
            or staged['alias'] + '/acceptance.py' not in trial['dispatch']
            or release not in trial['dispatch'] or trial_parent not in trial['dispatch']
            or stage['candidate.tgz'] not in trial['dispatch']):
        raise ValueError('trial acceptance evidence dispatch command does not name this trial')
    if (dispatch_result.get('exit_code') != 0 or dispatch_result.get('error') is not None
            or dispatch_result.get('command') != trial['dispatch']
            or dispatch.get('exit_code') != 0 or dispatch.get('error') is not None
            or dispatch.get('command') != trial['dispatch']
            or unit_fields.get('Result') != 'success' or unit_fields.get('ExecMainCode') != '0'
            or unit_fields.get('ExecMainStatus') != '0'):
        raise ValueError('trial acceptance evidence dispatch did not succeed exactly once')

    # 4. The six acceptance checks, bound to this release and to the candidate bytes.
    acceptance = _trial_document(files['board-download/acceptance-report.json'],
                                 'acceptance report')
    report_checks = acceptance.get('checks')
    if (acceptance.get('schema') != TRIAL_ACCEPTANCE_SCHEMA
            or acceptance.get('ok') is not True
            or acceptance.get('expected_release') != release
            or type(report_checks) is not list or not report_checks
            or any(type(item) is not dict or item.get('ok') is not True
                   for item in report_checks)):
        raise ValueError('trial acceptance evidence acceptance report unverified')
    details = {item.get('name'): (item.get('detail') or {}) for item in report_checks}
    if set(details) != set(TRIAL_ACCEPTANCE_CHECKS):
        raise ValueError('trial acceptance evidence acceptance checks incomplete')
    identity = details['a_release_identity']
    if (identity.get('release_id') != release or identity.get('release_verified') is not True
            or identity.get('software_contract') != parent['software_contract']
            or identity.get('software_modules') != len(software)
            or identity.get('software_digest') != canonical
            or identity.get('build_id') != parent['build_id']
            or identity.get('daemon_version') != parent['daemon_version']
            or identity.get('protocol_version') != parent['protocol_version']):
        raise ValueError('trial acceptance evidence acceptance report disagrees with the release')
    domain = details['b_domain_constants'].get('constants')
    if type(domain) is not dict or set(domain) != set(TRIAL_DOMAIN_KEYS):
        raise ValueError('trial acceptance evidence domain constants incomplete')
    attenuation = domain.get('rf_environment_FIXED_RF')
    if (not _trial_number(attenuation) or attenuation <= 0
            or any(domain.get(key) != attenuation for key in TRIAL_ATTENUATION_KEYS)
            or domain.get('rf_environment_VERSION') != baseline.get('environment_version')
            or domain.get('rf_bpsk_receiver_VERSION') != baseline.get('algorithm_version')):
        raise ValueError('trial acceptance evidence domain constants disagree with the release')
    environment_module = details['b_domain_constants'].get('rf_environment') or {}
    if (environment_module.get('sha256') != software.get('rf_environment.py')
            or environment_module.get('module') != release_path + '/rf_environment.py'):
        raise ValueError('trial acceptance evidence domain module is not the release module')
    recorded = details['c_baseline_load_not_formal']
    if (recorded.get('loaded') is not True
            or recorded.get('file_sha256') != baseline_sha
            or recorded.get('canonical_sha256') != baseline.get('sha256')
            or recorded.get('calibration_status') != baseline.get('calibration_status')
            or recorded.get('environment_version') != baseline.get('environment_version')
            or recorded.get('algorithm_version') != baseline.get('algorithm_version')
            or recorded.get('sources') != baseline.get('sources')
            or recorded.get('spectrum_values') != len(baseline['spectrum_envelope'])
            or type(recorded.get('formal_acceptance_baseline')) is not bool
            or (recorded.get('rf_config') or {}).get('attenuation_mdB') != attenuation):
        raise ValueError('trial acceptance evidence baseline record disagrees with the release')
    if not _trial_services_ok(details['d_services']):
        raise ValueError('trial acceptance evidence acceptance services are not running')
    closed = details['e_rf_closed']
    if (closed.get('active_release') != release_path
            or closed.get('active_release_expected') != release_path
            or closed.get('status_release_id') != release
            or closed.get('armed') is not False or closed.get('io_ok') is not True
            or closed.get('online') is not True or closed.get('stale') is not False
            or closed.get('rx_armed') is not False
            or closed.get('tx_source_selected') is not False):
        raise ValueError('trial acceptance evidence acceptance RF state is not closed')
    frame = details['f_archived_iq_smoke']
    frame_record = frame.get('frame') or {}
    frame_bytes = (evidence / 'stage/frames/attempt-2/frame.iq').stat().st_size
    demod = frame.get('demod') or {}
    environment_leg = frame.get('environment') or {}
    if (frame_record.get('iq_sha256') != stage['frames/attempt-2/frame.iq']
            or not _trial_number(frame_record.get('bytes')) or frame_record['bytes'] != frame_bytes
            or (frame.get('replay_helper') or {}).get('sha256') != stage['rf_headroom_replay.py']
            or not _trial_number(demod.get('bit_errors')) or demod['bit_errors'] != 0
            or not _trial_number(demod.get('compared_bits')) or demod['compared_bits'] != 138
            or not _trial_number(demod.get('ber')) or demod['ber'] != 0
            or not _trial_number(demod.get('rail_count')) or demod['rail_count'] != 0
            or demod.get('carrier_sync') is not True or demod.get('timing_sync') is not True
            or demod.get('reference_sync') is not True
            or environment_leg.get('baseline_sha256') != baseline.get('sha256')
            or environment_leg.get('environment_version') != baseline.get('environment_version')
            or environment_leg.get('algorithm_version') != baseline.get('algorithm_version')
            or (frame.get('rf_config') or {}).get('attenuation_mdB') != attenuation):
        raise ValueError('trial acceptance evidence archived-frame smoke test unverified')
    acceptance_state = acceptance.get('state') or {}
    if (acceptance_state.get('expected_release') != release
            or acceptance_state.get('release_id_from_name') != release
            or acceptance_state.get('release_path') != CURRENT_RELEASE_PATH
            or acceptance_state.get('boot_id') != trial['boot_id']):
        raise ValueError('trial acceptance evidence acceptance state does not name this release')

    # 5. The pre-state it replaced and the post-state it reached, by the installer's
    #    own predicate; the board's own trial-state.json is the same judgement.
    pre = _trial_document(files['board-download/pre-state.json'], 'pre-state')
    if (pre.get('expected_release') != trial_parent or pre.get('release_id') != trial_parent
            or pre.get('release_path') != RELEASE_PATH_PREFIX + trial_parent
            or not verified_state(pre, trial_parent)):
        raise ValueError('trial acceptance evidence pre-state is not a verified safe state '
                         'of the release it replaced')
    post = _trial_document(files['board-download/post-state.json'], 'post-state')
    if (post.get('expected_release') != release or post.get('release_path') != release_path
            or post.get('software_contract') != parent['software_contract']
            or not verified_state(post, release)):
        raise ValueError('trial acceptance evidence post-state is not a verified safe state '
                         'of this release')
    trial_state = _trial_document(files['board-download/trial-state.json'], 'board trial state')
    if (trial_state.get('expected_release') != release
            or trial_state.get('release_path') != release_path
            or trial_state.get('software_contract') != parent['software_contract']
            or not verified_state(trial_state, release)):
        raise ValueError('trial acceptance evidence board trial state is not verified safe')

    # 6. The independent read-only read-back on its own session, and its repeat after
    #    the staging cleanup: identity, manifest bytes, software digest, domain and
    #    baseline all have to be this release's.
    readback = _trial_document(files['logs/readback-summary.json'], 'read-back summary')
    final = _trial_document(files['logs/final-summary.json'], 'final read-back summary')

    def check_readback(summary, label, phase):
        # The one read-back predicate both evidence formats share, so the 2026-10-18
        # adapter cannot be weaker than this frozen route by accident.
        _trial_readback_checks(label, summary, phase, release, release_path, trial_parent,
                               trial, parent, software, baseline, baseline_sha, canonical,
                               attenuation, 'trial acceptance evidence')

    check_readback(readback, 'independent read-back', 'readback')
    check_readback(final, 'final read-back', 'final')
    current = _trial_sha256sums(readback.get('paths'), '==CURRENT_MANIFEST==')
    if (current is None
            or current.get(CURRENT_RELEASE_PATH + '/merged_release_manifest.json')
               != manifest_sha
            or current.get(CURRENT_RELEASE_PATH + '/rf_daemon') != parent['daemon_sha256']):
        raise ValueError('trial acceptance evidence read-back release tree is not the '
                         'candidate tree')

    # 7. The downloaded board bytes and the staged payload, re-hashed against disk.
    downloads = _trial_document(files['readback/board-download-manifest.json'],
                                'board download record')
    listing = downloads.get('board_listing') or {}
    downloaded = downloads.get('downloaded') or {}
    if (downloads.get('exit_code') != 0 or type(listing) is not dict
            or type(downloaded) is not dict
            or downloads.get('stage') != staged['durable'] + '/stage'):
        raise ValueError('trial acceptance evidence board download record unverified')
    if any(name in listing or name in downloaded for name in TRIAL_ROLLBACK_ARTIFACTS):
        raise ValueError('trial acceptance evidence records a rollback state')
    for name in TRIAL_BOARD_LISTED:
        if listing.get(name) != stage[name]:
            raise ValueError('trial acceptance evidence board stage bytes differ: ' + name)
    for name in TRIAL_BOARD_DOWNLOADS:
        record = downloaded.get(name)
        path = files['board-download/' + name]
        if (type(record) is not dict or record.get('verified') is not True
                or record.get('sha256') != sha(path)
                or record.get('bytes') != path.stat().st_size
                or listing.get(name) != record.get('sha256')):
            raise ValueError('trial acceptance evidence board download differs: ' + name)

    # 8. The cleanup that removed only this trial's staging paths, and the receipt
    #    that binds every artifact above by sha256.
    cleanup = _trial_document(files['cleanup.json'], 'cleanup record')
    cleanup_board = cleanup.get('board') or {}
    if (cleanup.get('exit_code') != 0 or type(cleanup_board) is not dict
            or cleanup_board.get('durable_kept') is not True
            or cleanup_board.get(staged['upload']) != 'dir'
            or cleanup_board.get(staged['alias']) != 'symlink:' + staged['alias_target']):
        raise ValueError('trial acceptance evidence cleanup record unverified')
    receipt = _trial_document(files['receipt.json'], 'trial receipt')
    receipt_dispatch = receipt.get('dispatch') or {}
    receipt_build = receipt.get('build') or {}
    receipt_staging = receipt.get('staging') or {}
    if (receipt.get('schema') != TRIAL_RECEIPT_SCHEMA
            or receipt.get('release_tested') != release
            or receipt.get('release_final') != release
            or receipt.get('parent_release') != trial_parent
            or receipt.get('parent_unchanged') is not True
            or receipt.get('terminal_marker') != accepted
            or accepted not in str(receipt.get('terminal_marker_verbatim'))
            or TRIAL_INSTALL_TERMINAL not in str(receipt.get('install_accepted_verbatim'))
            or TRIAL_ACCEPTANCE_TERMINAL not in str(receipt.get('acceptance_marker_verbatim'))
            or receipt_dispatch.get('command') != trial['dispatch']
            or receipt_dispatch.get('exit_code') != 0
            or receipt_dispatch.get('unit_result') != 'success'
            or receipt_dispatch.get('unit_status') != 0
            or receipt_build.get('verify_bundle') != 'PASS'
            or receipt_build.get('candidate_tgz_sha256') != stage['candidate.tgz']
            or not _trial_number(receipt_build.get('members'))
            or receipt_staging.get('files') != stage
            or receipt_staging.get('identical_local_board') is not True
            or receipt_staging.get('durable') != staged['durable'] + '/stage'
            or receipt_staging.get('alias') != staged['alias']
            or not _trial_number(receipt.get('new_physical_frames'))
            or receipt['new_physical_frames'] != 0
            or receipt.get('boot_id') != trial['boot_id']
            or (receipt.get('rollback') or {}).get('triggered') is not False):
        raise ValueError('trial acceptance evidence receipt unverified')
    receipt_acceptance = receipt.get('acceptance') or {}
    if (receipt_acceptance.get('ok') is not True
            or not _trial_number(receipt_acceptance.get('checks_passed'))
            or receipt_acceptance['checks_passed'] != len(TRIAL_ACCEPTANCE_CHECKS)
            or receipt_acceptance.get('expected_release') != release
            or receipt_acceptance.get('software_modules') != len(software)
            or receipt_acceptance.get('software_digest') != canonical
            or receipt_acceptance.get('domain') != domain
            or receipt_acceptance.get('services') != details['d_services']
            or (receipt_acceptance.get('baseline') or {}).get('file_sha256') != baseline_sha
            or (receipt_acceptance.get('baseline') or {}).get('canonical_sha256')
               != baseline.get('sha256')):
        raise ValueError('trial acceptance evidence receipt disagrees with the acceptance')
    receipt_readback = receipt.get('readback') or {}
    receipt_closed = receipt_readback.get('rf_closed') or {}
    if (receipt_readback.get('passed') is not True
            or receipt_readback.get('final_after_cleanup_passed') is not True
            or receipt_readback.get('release_id') != release
            or receipt_readback.get('software_modules') != len(software)
            or receipt_readback.get('software_digest') != canonical
            or receipt_readback.get('boot_id') != trial['boot_id']
            or receipt_readback.get('checks') != readback.get('checks')
            or receipt_readback.get('storage_faults') != []
            or receipt_readback.get('mounts_rw') != {'root': True, 'boot': True}
            or not _trial_services_ok(receipt_readback.get('services'))
            or receipt_closed.get('armed') is not False
            or receipt_closed.get('io_ok') is not True
            or receipt_closed.get('tx_source_selected') is not False
            or receipt_closed.get('rx_armed') is not False):
        raise ValueError('trial acceptance evidence receipt read-back disagrees with the '
                         'read-back')
    bound = receipt.get('evidence_sha256')
    if (type(bound) is not dict or not bound or 'receipt.json' in bound
            or not set(TRIAL_FILES) - {'receipt.json'} <= set(bound)):
        raise ValueError('trial acceptance evidence receipt does not bind its artifacts')
    for name, digest in bound.items():
        path = evidence / name
        if not path.is_file() or sha(path) != digest:
            raise ValueError('trial acceptance evidence artifact digest differs: ' + name)

    return dict(release_id=release, source='trial-acceptance-evidence',
                software_contract=parent['software_contract'],
                software_modules=len(software), trial_parent=trial_parent,
                evidence_dir=str(evidence.resolve()),
                evidence_sha256={name: sha(path) for name, path in sorted(files.items())})


# --------------------------------------------------------------------------- #
# The fifth parent route, 2026-10-18 evidence format.
#
# The 2026-10-18 trial is the same reviewed chain -- one dispatch, the installer's
# INSTALL_ACCEPTED, a bounded on-board acceptance, an independent read-only read-back
# and its repeat after cleanup -- recorded in a reorganised package: bundle/, trial/,
# readback/ and preflight/ replace the old flat layout, the host receipt and the two
# read-back summaries are gone, and every board reading is now a 17-item preflight
# document beside its raw log.  verify_installed_parent_via_trial_acceptance_
# evidence_v2 rebuilds that chain.  Each artefact the frozen route reads is mapped to
# the path that carries the same bytes (TRIAL_V2_MAP); the three artefacts with no
# counterpart at all -- receipt.json, logs/readback-summary.json and
# logs/final-summary.json -- are rebuilt from the named semantic equivalents in
# TRIAL_V2_SUBSTITUTES, and every identity and digest is still compared with the
# candidate directory R.verify_release just verified rather than with the documents
# that claim it.  The format is named by the caller: nothing auto-detects it and
# neither format falls back to the other.
# --------------------------------------------------------------------------- #
TRIAL_FORMAT_2026_10_17 = '2026-10-17'
TRIAL_FORMAT_2026_10_18 = '2026-10-18'
# The 2026-10-18 baseline-deployment package is a third layout; the caller names it.
TRIAL_FORMAT_2026_10_18_BASELINE = '2026-10-18-baseline'
TRIAL_FORMATS = (TRIAL_FORMAT_2026_10_17, TRIAL_FORMAT_2026_10_18,
                 TRIAL_FORMAT_2026_10_18_BASELINE)
TRIAL_V2_SOURCE = 'trial-acceptance-evidence-v2-2026-10-18'
# Old artefact -> the 2026-10-18 path that carries the same bytes.  None marks the
# three artefacts with no counterpart, whose claims are rebuilt from
# TRIAL_V2_SUBSTITUTES below.
TRIAL_V2_MAP = {
    'trial.json': 'trial/trial.json',
    'dispatch-intent.json': 'trial/dispatch-intent.json',
    'logs/dispatch-journal.log': 'logs/dispatch-journal.log',
    'logs/dispatch-summary.json': 'logs/dispatch-summary.json',
    'logs/dispatch.result.json': 'logs/dispatch.result.json',
    'board-download/acceptance-report.json': 'readback/board-download/acceptance-report.json',
    'board-download/pre-state.json': 'readback/board-download/pre-state.json',
    'board-download/post-state.json': 'readback/board-download/post-state.json',
    'board-download/trial-state.json': 'readback/board-download/trial-state.json',
    'board-download/pynq-status.before': 'readback/board-download/pynq-status.before',
    'board-download/rf-daemon.before': 'readback/board-download/rf-daemon.before',
    'readback/board-download-manifest.json':
        'readback/board-download/board-download-manifest.json',
    'cleanup.json': 'readback/cleanup.json',
    'stage/stage-local-sha256.json': 'trial/stage/stage-local-sha256.json',
    'stage/staged-sha256.json': 'trial/staged-sha256.json',
    'prepare/candidate.tgz': 'bundle/candidate.tgz',
    'prepare/install.sh': 'bundle/install.sh',
    'prepare/install_state.py': 'bundle/install_state.py',
    'logs/readback-summary.json': 'readback/post-00-preflight.json',
    'logs/final-summary.json': 'readback/post-01-preflight.json',
    'receipt.json': None,
}
# The three artefacts with no counterpart, and the named equivalents that replace
# them.  A claim of the missing artefact whose equivalent is absent refuses by name;
# nothing is silently skipped.
TRIAL_V2_SUBSTITUTES = {
    'receipt.json': ('logs/15-evidence-sha256.json', 'bundle/bundle-receipt.json',
                     'logs/01-verify-bundle.json', 'logs/01-verify-bundle.log',
                     'logs/07-acceptance-summary.txt', 'logs/08-daemon-check-summary.txt',
                     'logs/16-parent-rollback-target.log', 'logs/02-stage-local.log',
                     'logs/03-upload.log', 'logs/upload-verify.log',
                     'logs/stage-binding.log', 'readback/cleanup.json'),
    'logs/readback-summary.json': ('readback/post-00-01-snapshot.log',
                                   'readback/post-00-02-state.log',
                                   'readback/post-00-03-paths.log',
                                   'readback/post-00-04-dmesg.log',
                                   'readback/post-00-preflight.json'),
    'logs/final-summary.json': ('readback/post-01-01-snapshot.log',
                                'readback/post-01-02-state.log',
                                'readback/post-01-03-paths.log',
                                'readback/post-01-04-dmesg.log',
                                'readback/post-01-preflight.json'),
}
# Artefacts the 2026-10-18 package adds: the raw board record of the release tree and
# of the installed daemon, the fetch payloads, the unit report, the board's own re-read
# of the staged tree, the pre-installation preflight and the evidence index.
TRIAL_V2_EXTRA = ('logs/13-release-listing.log', 'logs/13-release-listing.json',
                  'logs/08-daemon-check.log', 'logs/fetch.log', 'logs/fetch.result.json',
                  'logs/dispatch-unit.log', 'logs/dispatch-unit.result.json',
                  'logs/cleanup.result.json', 'logs/00-candidate-pins.json',
                  'logs/12-readback-facts.json', 'logs/15-evidence-sha256.json',
                  'logs/upload-verify.result.json', 'logs/01-verify-bundle.json',
                  'preflight/pre-01-preflight.json', 'preflight/pre-01-01-snapshot.log',
                  'preflight/pre-01-02-state.log', 'preflight/pre-01-03-paths.log',
                  'preflight/pre-01-04-dmesg.log',
                  'trial/stage/frames/attempt-2/header.json')
TRIAL_V2_FILES = tuple(sorted(
    set(name for name in TRIAL_V2_MAP.values() if name)
    | set(name for names in TRIAL_V2_SUBSTITUTES.values() for name in names)
    | set(TRIAL_V2_EXTRA)))
TRIAL_V2_ITEMS = ('B1_release_identity', 'B2_software_map', 'B3_domain_46000',
                  'B4_baseline', 'B5_services', 'B6_rf_safe', 'B7_dmesg_faults',
                  'B8_mounts_rw', 'B9_no_inflight', 'B10_lock_free', 'B11_tmp_clean',
                  'B12_boot_identity', 'H1_git_head', 'H2_tool_hashes',
                  'H3_candidate_release', 'H4_candidate_domain', 'H5_candidate_baseline')
TRIAL_V2_PREFLIGHT = {'readback': 'readback/post-00-preflight.json',
                      'final': 'readback/post-01-preflight.json',
                      'pre': 'preflight/pre-01-preflight.json'}
TRIAL_V2_TAG = {'readback': 'post-00', 'final': 'post-01', 'pre': 'pre-01'}
TRIAL_V2_RAW = {
    'readback': ('readback/post-00-01-snapshot.log', 'readback/post-00-02-state.log',
                 'readback/post-00-03-paths.log', 'readback/post-00-04-dmesg.log'),
    'final': ('readback/post-01-01-snapshot.log', 'readback/post-01-02-state.log',
              'readback/post-01-03-paths.log', 'readback/post-01-04-dmesg.log'),
    'pre': ('preflight/pre-01-01-snapshot.log', 'preflight/pre-01-02-state.log',
            'preflight/pre-01-03-paths.log', 'preflight/pre-01-04-dmesg.log'),
}
TRIAL_V2_FACTS = 'logs/12-readback-facts.json'
TRIAL_V2_INDEX = 'logs/15-evidence-sha256.json'
# The release modules whose attenuation constant and version the read-back records only
# in part; they are read from the candidate bytes instead (see
# _trial_v2_domain_constants).
TRIAL_V2_ATTENUATION_MODULES = (('rf_teaching_profile.py', 'ATTENUATION_MDB'),
                                ('rf_status_client.py', 'ATTENUATION_MDB'),
                                ('ui_model.py', 'ATTENUATION_MDB'))
TRIAL_V2_FRAME = 'trial/stage/frames/attempt-2/frame.iq'
TRIAL_V2_FRAME_HEADER = 'trial/stage/frames/attempt-2/header.json'


def _trial_v2_record(path, marker, what):
    """One raw board record of the 2026-10-18 package, exactly as the log wrote it."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        raise ValueError('trial acceptance evidence v2 %s unreadable' % what) from exc
    for line in text.splitlines():
        if line.startswith(marker + ' '):
            try:
                return json.loads(line[len(marker) + 1:])
            except ValueError as exc:
                raise ValueError('trial acceptance evidence v2 %s unreadable'
                                 % what) from exc
    raise ValueError('trial acceptance evidence v2 %s missing' % what)


def _trial_v2_journal_payload(journal):
    """The unit's own output lines, with the journal prefix removed."""
    payload = []
    for line in journal.splitlines():
        head, marker, rest = line.partition('] pynq ')
        if not marker or not head.startswith('['):
            continue
        payload.append(rest.partition(': ')[2])
    return payload


def _trial_v2_journal_acceptance(payload):
    """The acceptance report the unit printed itself, rebuilt from the journal.

    The unit writes the report on the board and prints it; the downloaded copy and
    this record have to be the same report, so an edited download refuses unless the
    journal -- separately checked for its markers, order and bound budget -- is edited
    too.
    """
    start = None
    for index, line in enumerate(payload):
        if line.startswith(TRIAL_CHECK_TERMINAL):
            start = index
            break
    if start is None:
        return None
    begin = None
    for index in range(start + 1, len(payload)):
        if payload[index].strip() == '{':
            begin = index
            break
    end = None
    for index in range(len(payload) - 1, -1, -1):
        if payload[index].strip() == '}':
            end = index
            break
    if begin is None or end is None or end <= begin:
        return None
    try:
        return json.loads(chr(10).join(line.strip() for line in payload[begin:end + 1]))
    except ValueError:
        return None


def _trial_v2_paths(text, what):
    """The named sections of one paths reading."""
    sections = {}
    current = None
    for line in text.splitlines():
        if len(line) > 4 and line.startswith('==') and line.endswith('=='):
            current = line[2:-2]
            sections[current] = []
        elif current is not None and line.endswith('_DONE'):
            current = None
        elif current is not None and line != '':
            sections[current].append(line)
    if not sections:
        raise ValueError('trial acceptance evidence v2 %s unreadable' % what)
    return sections


def _trial_v2_listing(path):
    """The board's own sha256sum of the installed release directory, name -> digest."""
    digests = {}
    for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            digests[parts[1].lstrip('*')] = parts[0].lower()
    return digests


def _trial_v2_domain_constants(candidate, baseline):
    """The domain constants the installed modules carry, read from the candidate bytes.

    The 2026-10-18 read-back records three attenuation constants and the environment
    version, but not the ui_model constant or the demodulator version.  They are read
    here out of the candidate directory whose every file this route separately proves
    byte-identical to the installed release, so the value checked is the installed
    module's own value, not a claim about it.  A candidate that does not declare a
    constant refuses by name rather than being skipped.
    """
    quote_single, quote_double = chr(39), chr(34)

    def constant(name, key):
        try:
            text = (Path(candidate) / name).read_text(encoding='utf-8')
        except OSError as exc:
            raise ValueError('trial acceptance evidence v2 cannot read the candidate '
                             'module %s' % name) from exc
        for line in text.splitlines():
            # The constant is module level but may sit inside the compatibility guard,
            # so the assignment is matched after stripping; a commented-out one never
            # matches because the comment marker stays in front of the name.
            stripped = line.strip()
            if stripped.startswith(key + ' = '):
                return (stripped.split(' = ', 1)[1].strip()
                        .strip(quote_single).strip(quote_double))
        raise ValueError('trial acceptance evidence v2 candidate module %s does not '
                         'declare %s' % (name, key))

    attenuation = constant('rf_teaching_profile.py', 'ATTENUATION_MDB')
    if not attenuation.isdigit() or int(attenuation) <= 0:
        raise ValueError('trial acceptance evidence v2 candidate attenuation constant '
                         'is not a positive integer')
    attenuation = int(attenuation)
    for name, key in TRIAL_V2_ATTENUATION_MODULES:
        if constant(name, key) != str(attenuation):
            raise ValueError('trial acceptance evidence v2 candidate module %s disagrees '
                             'about the attenuation' % name)
    environment = constant('rf_environment.py', 'VERSION')
    demod = constant('rf_bpsk_receiver.py', 'VERSION')
    if environment != baseline.get('environment_version'):
        raise ValueError('trial acceptance evidence v2 candidate environment version '
                         'disagrees with its baseline')
    if demod != baseline.get('algorithm_version'):
        raise ValueError('trial acceptance evidence v2 candidate demodulator version '
                         'disagrees with its baseline')
    if (baseline.get('rf_config') or {}).get('attenuation_mdB') != attenuation:
        raise ValueError('trial acceptance evidence v2 candidate baseline attenuation '
                         'disagrees with its own modules')
    return attenuation, environment, demod


def _trial_v2_board_view(evidence, phase, what):
    """One read-back phase, re-derived from the raw board logs it wrote.

    Every field below comes out of the board's own output: the DEPLOY_SNAPSHOT and
    PREFLIGHT_STATE records, the named sections of the paths reading, and the raw
    kernel log.  The 17-item document is required to equal this derivation, so the
    document is a report about bytes rather than the bytes themselves.
    """
    snapshot = _trial_v2_record(Path(evidence) / TRIAL_V2_RAW[phase][0],
                                'DEPLOY_SNAPSHOT', what + ' snapshot')
    state = _trial_v2_record(Path(evidence) / TRIAL_V2_RAW[phase][1],
                             'PREFLIGHT_STATE', what + ' state')
    paths = _trial_v2_paths((Path(evidence) / TRIAL_V2_RAW[phase][2]).read_text(
        encoding='utf-8', errors='replace'), what + ' paths')
    dmesg = (Path(evidence) / TRIAL_V2_RAW[phase][3]).read_text(
        encoding='utf-8', errors='replace')
    units = {}
    for name, unit in sorted((snapshot.get('units') or {}).items()):
        fields = {}
        for line in str((unit or {}).get('out') or '').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                fields[key.strip()] = value.strip()
        units[name] = fields
    # The paths reading prints mounts the way `mount` does, or the way `findmnt` does
    # (`<dev> on <point> type <fs> (<options>)`); both carry the same two lines.
    mounts_rw = {}
    for line in paths.get('MOUNT') or []:
        parts = line.split()
        if len(parts) >= 5 and parts[1] == 'on' and parts[2] in ('/', '/boot'):
            point = parts[2]
            options = parts[5].strip('()').split(',') if len(parts) > 5 else []
        elif len(parts) >= 4 and parts[1] in ('/', '/boot'):
            point, options = parts[1], parts[3].split(',')
        else:
            continue
        mounts_rw['root' if point == '/' else 'boot'] = 'rw' in options
    return dict(snapshot=snapshot, state=state, paths=paths, dmesg=dmesg, units=units,
                mounts=paths.get('MOUNT') or [], mounts_rw=mounts_rw,
                tmp=paths.get('TMP') or [], residue=paths.get('TMP_RESIDUE') or [],
                faults=storage_faults(dmesg))


def _trial_v2_facts(document):
    """The decision facts one 17-item document carries, as the package records them.

    The route rebuilds the recorded facts from the document it has just compared with
    the raw board reading, so an edited facts file (or an edited document) refuses.
    """
    items = document.get('items') or {}
    snapshot = document.get('snapshot') or {}
    state = document.get('state') or {}
    status = state.get('status') or {}
    pl_status = status.get('pl_status') or {}
    services = {name: {key: value.get(key) for key in
                       ('ActiveState', 'SubState', 'MainPID', 'NRestarts')}
                for name, value in (state.get('services') or {}).items()}
    return dict(
        utc=document.get('utc'), passed=document.get('passed'),
        failures=document.get('failures'),
        current_release=snapshot.get('current_release'),
        boot_id=(items.get('B12_boot_identity') or {}).get('boot_id'),
        uptime=(items.get('B12_boot_identity') or {}).get('uptime_seconds'),
        snapshot={key: snapshot.get(key) for key in sorted(snapshot)},
        release_id=state.get('release_id'),
        software_contract=state.get('software_contract'),
        software_modules=state.get('software_modules'),
        software_digest=state.get('software_digest'),
        daemon_version=state.get('daemon_version'),
        protocol_version=state.get('protocol_version'),
        profile_domain=state.get('profile_domain'),
        env_FIXED_RF=state.get('env_FIXED_RF'),
        profile_ATTENUATION_MDB=state.get('profile_ATTENUATION_MDB'),
        client_ATTENUATION_MDB=state.get('client_ATTENUATION_MDB'),
        env_VERSION=state.get('env_VERSION'), baseline=state.get('baseline'),
        active_release=state.get('active_release'), services=services,
        rf=dict(armed=status.get('armed'), core=status.get('core'),
                online=status.get('online'), stale=status.get('stale'),
                io_ok=pl_status.get('io_ok'),
                tx_source_selected=(pl_status.get('tx') or {}).get('source_selected'),
                rx_armed=(pl_status.get('rx') or {}).get('armed'),
                mode=status.get('mode'), lease_held=status.get('lease_held'),
                headroom_used=(status.get('headroom') or {}).get('used')),
        dmesg={key: (items.get('B7_dmesg_faults') or {}).get(key)
               for key in ('faults', 'benign', 'other', 'lines')},
        mounts={key: (items.get('B8_mounts_rw') or {}).get(key)
                for key in ('root_rw', 'boot_rw')},
        lock=items.get('B10_lock_free', {}).get('state'),
        lock_reason=items.get('B10_lock_free', {}).get('reason'),
        b9=items.get('B9_no_inflight'), b11=items.get('B11_tmp_clean'),
    )


def _trial_v2_subset(part, whole):
    """A restated subset of one acceptance detail: every key it does state must agree.

    The package's acceptance summary restates part of the downloaded report, so the
    check is that nothing it does state contradicts the report -- and that it does state
    the decisive identity, baseline and RF-closed fields, which the caller requires by
    name.  The fields it leaves out are still read from the report itself.
    """
    return (type(part) is dict and type(whole) is dict and bool(part)
            and set(part) <= set(whole)
            and all(whole[key] == value for key, value in part.items()))


def _trial_v2_summary_txt(text):
    """The acceptance summary the package wrote from the downloaded report."""
    summary = {}
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        head = line.split(': ', 1)[0]
        if ': ' in line and '=' not in head:
            key, value = line.split(': ', 1)
            try:
                summary[key.strip()] = json.loads(value)
            except ValueError:
                summary[key.strip()] = value
            continue
        tokens = line.split()
        for index, token in enumerate(tokens):
            if token.endswith('=') and index + 1 < len(tokens) and len(token) > 1:
                key = token[:-1]
                value = tokens[index + 1]
                summary[key] = value == 'True' if key == 'ok' else value
    return summary


def _trial_readback_checks(label, summary, phase, release, release_path, trial_parent,
                           trial, parent, software, baseline, baseline_sha, canonical,
                           attenuation, prefix):
    """The read-back predicate of the fifth route, unchanged.

    Both evidence formats hand the same shape to this one predicate: a summary with
    phase/passed/board_contact/session_closed/snapshot_valid/checks/state, where state
    is the board's own software, baseline, domain, status and identity reading.
    """
    checks_record = summary.get('checks')
    snapshot = summary.get('snapshot') or {}
    reported = summary.get('state') or {}
    baseline_state = reported.get('baseline') or {}
    domain_state = reported.get('domain') or {}
    status = reported.get('status') or {}
    pl_status = status.get('pl_status') or {}
    if (summary.get('phase') != phase or summary.get('passed') is not True
            or summary.get('board_contact') != 'read-only'
            or summary.get('session_closed') is not True
            or summary.get('snapshot_valid') is not True
            or summary.get('expected_release') != release
            or summary.get('expected_parent') != trial_parent
            or type(checks_record) is not dict or not checks_record
            or any(value is not True for value in checks_record.values())
            or summary.get('kernel_storage_faults') != []
            or summary.get('dmesg_faults') != []):
        raise ValueError('%s %s unverified' % (prefix, label))
    if (snapshot.get('current_release') != release
            or snapshot.get('active_release') != release_path
            or snapshot.get('boot_id') != trial['boot_id']
            or not _trial_number(snapshot.get('uptime_seconds'))
            or snapshot['uptime_seconds'] < 0):
        raise ValueError('%s %s release or boot identity changed' % (prefix, label))
    if (reported.get('release') != release or reported.get('release_id') != release
            or reported.get('current') != release_path
            or reported.get('active_release') != release_path
            or reported.get('software_contract') != parent['software_contract']
            or reported.get('software_modules') != len(software)
            or reported.get('software_digest') != canonical
            or reported.get('build_id') != parent['build_id']
            or reported.get('daemon_version') != parent['daemon_version']
            or reported.get('protocol_version') != parent['protocol_version']
            or reported.get('boot_id') != trial['boot_id']
            or not _trial_services_ok(reported.get('services'))
            or status.get('release_id') != release or status.get('armed') is not False
            or pl_status.get('io_ok') is not True
            or (pl_status.get('tx') or {}).get('source_selected') is not False
            or (pl_status.get('rx') or {}).get('armed') is not False):
        raise ValueError('%s %s disagrees with the verified release' % (prefix, label))
    if (baseline_state.get('file_sha256') != baseline_sha
            or baseline_state.get('canonical_sha256') != baseline.get('sha256')
            or baseline_state.get('calibration_status') != baseline.get('calibration_status')
            or baseline_state.get('sources') != len(baseline['sources'])
            or baseline_state.get('spectrum_values') != len(baseline['spectrum_envelope'])
            or baseline_state.get('rf_config') != baseline.get('rf_config')):
        raise ValueError('%s %s baseline disagrees with the release' % (prefix, label))
    if (domain_state.get('env_FIXED_RF') != attenuation
            or any(domain_state.get(key) != attenuation
                   for key in ('client_ATTENUATION_MDB', 'profile_ATTENUATION_MDB',
                               'ui_ATTENUATION_MDB'))
            or domain_state.get('env_VERSION') != baseline.get('environment_version')
            or domain_state.get('demod_VERSION') != baseline.get('algorithm_version')):
        raise ValueError('%s %s domain disagrees with the release' % (prefix, label))

def verify_installed_parent_via_trial_acceptance_evidence_v2(parent, trial_evidence,
                                                              parent_candidate=None):
    """Fifth route, 2026-10-18 layout: the release the reviewed non-emitting trial
    deployment installed, rebuilt from the reorganised evidence package.

    The chain is the same one the frozen route rebuilds, in the same order:

      trial/trial.json             the one intent naming this release, its parent, its
                                   unit, its archive and its bounded acceptance budget
      logs/dispatch-journal.log    exactly one TRIAL_ACCEPTED release=<id>, one bounded
                                   TRIAL_CHECK_BEGIN, one INSTALL_ACCEPTED and one
                                   TRIAL_ACCEPTANCE PASS before it, no TRIAL_FAILED /
                                   TRIAL_ROLLED_BACK / TRIAL_RECOVERY_REQUIRED, and the
                                   acceptance report the unit printed equal to the
                                   downloaded copy
      trial/dispatch-intent.json   the one dispatch recorded before it happened, plus
                                   the unit report of that same unit
      readback/board-download/     the six acceptance checks, the pre-state it replaced
                                   and the post-states it reached, all by verified_state
      readback/post-0X-*.log       the raw board readings of the independent read-back
                                   (post-00, before the cleanup) and of the same
                                   read-back repeated after it (post-01)
      readback/post-0X-preflight   the 17-item reports of those readings, required to
                                   equal the derivation from those raw logs
      logs/12-readback-facts.json  the recorded decision facts, required to equal the
                                   same derivation
      logs/13-release-listing.log  the board's own sha256sum of the installed release
                                   directory, compared file by file with the candidate
      logs/fetch.log               the bytes the board returned, decoded and re-hashed
      trial/stage/*                the staged payload, rehashed against its own map and
                                   against the board's own re-read of the same tree
      bundle/*                     the prepared payload and the bundle receipt
      logs/15-evidence-sha256.json the index that binds every file of the package

    receipt.json, logs/readback-summary.json and logs/final-summary.json have no
    counterpart in this layout.  Their claims are rebuilt from the equivalents named in
    TRIAL_V2_SUBSTITUTES: the evidence index and the bundle receipt for the artefact
    binding, the acceptance, daemon and staging summaries plus the parent
    rollback-target log for the rest, and the raw readings plus the 17-item documents
    for the two read-back summaries.  A claim whose equivalent is missing refuses by
    name; nothing is skipped.

    Every identity and digest still has to agree with the candidate directory this run
    verified -- release id, contract, module inventory, canonical software digest, build
    id, daemon and protocol version, manifest bytes, daemon bytes, baseline file and the
    whole release tree -- so a self-consistent rewrite inside the evidence directory
    still refuses.  Nothing here writes, renames or upgrades a receipt.
    """
    evidence = Path(trial_evidence)
    if not evidence.is_dir():
        raise ValueError('trial acceptance evidence v2 directory required')
    if parent_candidate is None:
        raise ValueError('trial acceptance evidence v2 route requires the parent candidate')
    candidate = Path(parent_candidate)
    release = parent['release_id']
    release_path = RELEASE_PATH_PREFIX + release
    if (type(parent.get('software')) is not dict or not parent['software']
            or type(parent.get('software_contract')) is not str
            or type(parent.get('daemon_sha256')) is not str
            or type(parent.get('build_id')) is not str
            or type(parent.get('daemon_version')) is not str
            or parent.get('protocol_version') != 1):
        raise ValueError('trial acceptance evidence v2 route requires a verified software '
                         'release')
    software = parent['software']
    sources = R.software_sources(parent['software_contract'])
    if set(sources) != set(software):
        raise ValueError('trial acceptance evidence v2 candidate software map changed')
    canonical = _canonical_digest(software)
    manifest_sha = sha(candidate / 'merged_release_manifest.json')
    if sha(candidate / 'rf_daemon') != parent['daemon_sha256']:
        raise ValueError('trial acceptance evidence v2 parent daemon differs from its '
                         'manifest')
    baseline = _trial_document(candidate / 'rf_environment_baseline.json', 'parent baseline')
    baseline_sha = sha(candidate / 'rf_environment_baseline.json')
    baseline_body = {key: value for key, value in baseline.items() if key != 'sha256'}
    if (baseline.get('sha256') != _canonical_digest(baseline_body)
            or type(baseline.get('rf_config')) is not dict
            or type(baseline.get('sources')) is not list or not baseline['sources']
            or type(baseline.get('spectrum_envelope')) is not list):
        raise ValueError('trial acceptance evidence v2 parent baseline is not canonical')
    attenuation, environment_version, demod_version = _trial_v2_domain_constants(
        candidate, baseline)
    files = {name: evidence / name for name in TRIAL_V2_FILES}
    for name, path in files.items():
        if not path.is_file():
            raise ValueError('trial acceptance evidence v2 %s missing' % name)
    stage, staged = _trial_stage_map(evidence, 'trial/stage', 'stage-local-sha256.json',
                                     'staged-sha256.json', 'trial acceptance evidence v2',
                                     board_path='trial/staged-sha256.json')
    for name in ('candidate.tgz', 'install.sh', 'install_state.py'):
        if sha(files['bundle/' + name]) != stage[name]:
            raise ValueError('trial acceptance evidence v2 prepared payload differs from '
                             'the stage')

    # 1. The trial intent: one release, one parent, one unit, one staged archive.
    trial = _trial_document(files['trial/trial.json'], 'trial intent')
    trial_parent = trial.get('parent_release')
    budget = trial.get('acceptance_budget_seconds')
    if (trial.get('release_id') != release
            or type(trial_parent) is not str
            or re.fullmatch('[0-9a-f]{32}', trial_parent) is None or trial_parent == release
            or trial.get('files') != stage
            or trial.get('archive_sha') != stage['candidate.tgz']
            or not _trial_number(budget) or not 1 <= budget <= 300
            or type(trial.get('unit')) is not str or not trial['unit']
            or trial['unit'] != 'rf-trial-' + str(trial.get('trial_id'))
            or type(trial.get('boot_id')) is not str
            or re.fullmatch('[0-9a-f-]{36}', trial['boot_id']) is None
            or (trial.get('reviewed') or {}).get('acceptance_sha256') != stage['acceptance.py']
            or (trial.get('reviewed') or {}).get('replay_sha256')
               != stage['rf_headroom_replay.py']
            or (trial.get('reviewed') or {}).get('trial_install_sha256')
               != stage['trial-install.sh']
            or type(trial.get('rollback_plan')) is not str
            or stage['install_state.py'] not in trial['rollback_plan']
            or type(trial.get('dispatch')) is not str or not trial['dispatch']):
        raise ValueError('trial acceptance evidence v2 intent does not name this release')
    for key in ('upload', 'durable', 'alias'):
        if trial.get(key) != staged[key]:
            raise ValueError('trial acceptance evidence v2 intent and stage paths disagree')

    # 2. The journal: the acceptance happened exactly once and named this release.
    journal = files['logs/dispatch-journal.log'].read_text(encoding='utf-8', errors='replace')
    accepted = TRIAL_TERMINAL + ' release=' + release
    if any(marker in journal for marker in TRIAL_REFUSED_TERMINALS):
        raise ValueError('trial acceptance evidence v2 records a failed or rolled-back trial')
    if (journal.count(TRIAL_TERMINAL) != 1 or journal.count(accepted) != 1
            or journal.count(TRIAL_CHECK_TERMINAL) != 1
            or journal.count(TRIAL_INSTALL_TERMINAL) != 1
            or journal.count(TRIAL_ACCEPTANCE_TERMINAL) != 1):
        raise ValueError('trial acceptance evidence v2 terminal marker missing, repeated or '
                         'not this release')
    payload = _trial_v2_journal_payload(journal)
    seconds = None
    for line in payload:
        if line.startswith(TRIAL_CHECK_TERMINAL + ' seconds='):
            seconds = line[len(TRIAL_CHECK_TERMINAL) + len(' seconds='):].strip()
            break
    if seconds is None or not seconds.isdigit() or int(seconds) != budget:
        raise ValueError('trial acceptance evidence v2 bounded acceptance budget changed')
    if not (journal.index(TRIAL_INSTALL_TERMINAL) < journal.index(TRIAL_CHECK_TERMINAL)
            < journal.index(TRIAL_ACCEPTANCE_TERMINAL) < journal.index(accepted)):
        raise ValueError('trial acceptance evidence v2 journal is out of order')
    if trial['unit'] not in journal or trial_parent not in journal:
        raise ValueError('trial acceptance evidence v2 journal does not name this trial')
    for verbatim in (accepted, TRIAL_INSTALL_TERMINAL, TRIAL_ACCEPTANCE_TERMINAL):
        if payload.count(verbatim) != 1:
            raise ValueError('trial acceptance evidence v2 receipt-equivalent verbatim '
                             'marker missing or repeated: ' + verbatim)

    # 3. The one dispatch: recorded before it ran, and successful in systemd's report.
    dispatch_summary = _trial_document(files['logs/dispatch-summary.json'], 'dispatch summary')
    dispatch_result = _trial_document(files['logs/dispatch.result.json'], 'dispatch result')
    dispatch_intent = _trial_document(files['trial/dispatch-intent.json'], 'dispatch intent')
    dispatch = dispatch_summary.get('dispatch') or {}
    intent = dispatch_summary.get('intent') or {}
    unit_text = dispatch_summary.get('unit')
    unit_fields = {}
    for line in (unit_text or '').splitlines() if type(unit_text) is str else []:
        if '=' in line:
            unit_key, unit_value = line.split('=', 1)
            unit_fields[unit_key.strip()] = unit_value.strip()
    if (type(intent) is not dict or dispatch_intent != intent
            or intent.get('unit') != trial['unit'] or intent.get('command') != trial['dispatch']
            or intent.get('release_id') != release or intent.get('parent_release') != trial_parent
            or intent.get('archive_sha') != stage['candidate.tgz']
            or intent.get('boot_id') != trial['boot_id']
            or not _trial_number(intent.get('acceptance_budget_seconds'))
            or intent.get('acceptance_budget_seconds') != budget
            or intent.get('staged') != staged
            or type(intent.get('policy')) is not str
            or 'single dispatch' not in intent['policy']):
        raise ValueError('trial acceptance evidence v2 dispatch intent unverified')
    if ('--unit=' + trial['unit'] not in trial['dispatch']
            or staged['alias'] + '/trial-install.sh' not in trial['dispatch']
            or staged['alias'] + '/acceptance.py' not in trial['dispatch']
            or release not in trial['dispatch'] or trial_parent not in trial['dispatch']
            or stage['candidate.tgz'] not in trial['dispatch']):
        raise ValueError('trial acceptance evidence v2 dispatch command does not name this '
                         'trial')
    unit_result = _trial_document(files['logs/dispatch-unit.result.json'], 'dispatch unit result')
    unit_report = files['logs/dispatch-unit.log'].read_text(encoding='utf-8', errors='replace')
    unit_report_fields = {}
    for line in unit_report.splitlines():
        if '=' in line:
            unit_key, unit_value = line.split('=', 1)
            unit_report_fields[unit_key.strip()] = unit_value.strip()
    if (dispatch_result.get('exit_code') != 0 or dispatch_result.get('error') is not None
            or dispatch_result.get('command') != trial['dispatch']
            or dispatch.get('exit_code') != 0 or dispatch.get('error') is not None
            or dispatch.get('command') != trial['dispatch']
            or unit_fields.get('Result') != 'success' or unit_fields.get('ExecMainCode') != '0'
            or unit_fields.get('ExecMainStatus') != '0'
            or unit_result.get('exit_code') != 0 or unit_result.get('error') is not None
            or trial['unit'] not in str(unit_result.get('command'))
            or unit_report_fields.get('Result') != 'success'
            or unit_report_fields.get('ExecMainStatus') != '0'):
        raise ValueError('trial acceptance evidence v2 dispatch did not succeed exactly once')

    # 4. The six acceptance checks, bound to this release and to the candidate bytes.
    acceptance = _trial_document(
        files['readback/board-download/acceptance-report.json'], 'acceptance report')
    report_checks = acceptance.get('checks')
    if (acceptance.get('schema') != TRIAL_ACCEPTANCE_SCHEMA
            or acceptance.get('ok') is not True
            or acceptance.get('expected_release') != release
            or type(report_checks) is not list or not report_checks
            or any(type(item) is not dict or item.get('ok') is not True
                   for item in report_checks)):
        raise ValueError('trial acceptance evidence v2 acceptance report unverified')
    details = {item.get('name'): (item.get('detail') or {}) for item in report_checks}
    if set(details) != set(TRIAL_ACCEPTANCE_CHECKS):
        raise ValueError('trial acceptance evidence v2 acceptance checks incomplete')
    if _trial_v2_journal_acceptance(payload) != acceptance:
        raise ValueError('trial acceptance evidence v2 journal and downloaded acceptance '
                         'report disagree')
    identity = details['a_release_identity']
    if (identity.get('release_id') != release or identity.get('release_verified') is not True
            or identity.get('software_contract') != parent['software_contract']
            or identity.get('software_modules') != len(software)
            or identity.get('software_digest') != canonical
            or identity.get('build_id') != parent['build_id']
            or identity.get('daemon_version') != parent['daemon_version']
            or identity.get('protocol_version') != parent['protocol_version']):
        raise ValueError('trial acceptance evidence v2 acceptance report disagrees with the '
                         'release')
    domain = details['b_domain_constants'].get('constants')
    if type(domain) is not dict or set(domain) != set(TRIAL_DOMAIN_KEYS):
        raise ValueError('trial acceptance evidence v2 domain constants incomplete')
    if (not _trial_number(domain.get('rf_environment_FIXED_RF'))
            or domain.get('rf_environment_FIXED_RF') != attenuation
            or any(domain.get(key) != attenuation for key in TRIAL_ATTENUATION_KEYS)
            or domain.get('rf_environment_VERSION') != environment_version
            or domain.get('rf_bpsk_receiver_VERSION') != demod_version):
        raise ValueError('trial acceptance evidence v2 domain constants disagree with the '
                         'release')
    environment_module = details['b_domain_constants'].get('rf_environment') or {}
    if (environment_module.get('sha256') != software.get('rf_environment.py')
            or environment_module.get('module') != release_path + '/rf_environment.py'):
        raise ValueError('trial acceptance evidence v2 domain module is not the release module')
    recorded = details['c_baseline_load_not_formal']
    if (recorded.get('loaded') is not True
            or recorded.get('file_sha256') != baseline_sha
            or recorded.get('canonical_sha256') != baseline.get('sha256')
            or recorded.get('calibration_status') != baseline.get('calibration_status')
            or recorded.get('environment_version') != baseline.get('environment_version')
            or recorded.get('algorithm_version') != baseline.get('algorithm_version')
            or recorded.get('sources') != baseline.get('sources')
            or recorded.get('spectrum_values') != len(baseline['spectrum_envelope'])
            or type(recorded.get('formal_acceptance_baseline')) is not bool
            or (recorded.get('rf_config') or {}).get('attenuation_mdB') != attenuation):
        raise ValueError('trial acceptance evidence v2 baseline record disagrees with the '
                         'release')
    if not _trial_services_ok(details['d_services']):
        raise ValueError('trial acceptance evidence v2 acceptance services are not running')
    closed = details['e_rf_closed']
    if (closed.get('active_release') != release_path
            or closed.get('active_release_expected') != release_path
            or closed.get('status_release_id') != release
            or closed.get('armed') is not False or closed.get('io_ok') is not True
            or closed.get('online') is not True or closed.get('stale') is not False
            or closed.get('rx_armed') is not False
            or closed.get('tx_source_selected') is not False):
        raise ValueError('trial acceptance evidence v2 acceptance RF state is not closed')
    frame = details['f_archived_iq_smoke']
    frame_record = frame.get('frame') or {}
    frame_bytes = (evidence / TRIAL_V2_FRAME).stat().st_size
    demod = frame.get('demod') or {}
    environment_leg = frame.get('environment') or {}
    if (frame_record.get('iq_sha256') != stage['frames/attempt-2/frame.iq']
            or not _trial_number(frame_record.get('bytes')) or frame_record['bytes'] != frame_bytes
            or (frame.get('replay_helper') or {}).get('sha256')
               != stage['rf_headroom_replay.py']
            or not _trial_number(demod.get('bit_errors')) or demod['bit_errors'] != 0
            or not _trial_number(demod.get('compared_bits')) or demod['compared_bits'] != 138
            or not _trial_number(demod.get('ber')) or demod['ber'] != 0
            or not _trial_number(demod.get('rail_count')) or demod['rail_count'] != 0
            or demod.get('carrier_sync') is not True or demod.get('timing_sync') is not True
            or demod.get('reference_sync') is not True
            or environment_leg.get('baseline_sha256') != baseline.get('sha256')
            or environment_leg.get('environment_version') != environment_version
            or environment_leg.get('algorithm_version') != demod_version
            or (frame.get('rf_config') or {}).get('attenuation_mdB') != attenuation):
        raise ValueError('trial acceptance evidence v2 archived-frame smoke test unverified')
    acceptance_state = acceptance.get('state') or {}
    if (acceptance_state.get('expected_release') != release
            or acceptance_state.get('release_id_from_name') != release
            or acceptance_state.get('release_path') != CURRENT_RELEASE_PATH
            or acceptance_state.get('boot_id') != trial['boot_id']):
        raise ValueError('trial acceptance evidence v2 acceptance state does not name this '
                         'release')

    # 4b. No new physical frame: the smoke-test frame is the archived frame the release's
    #     own baseline names, and its capture timestamp precedes the trial on this boot.
    #     The old receipt asserted new_physical_frames == 0; this derives that claim.
    header = _trial_document(files[TRIAL_V2_FRAME_HEADER], 'frame header')
    header_body = header.get('header') or {}
    # The capture facts live under the header's own headroom record.
    headroom = header_body.get('headroom') or {}
    frame_ns = header_body.get('timestamp_ns')
    dispatch_monotonic = None
    for line in journal.splitlines():
        if line.startswith('['):
            head = line[1:].split(']', 1)[0]
            try:
                dispatch_monotonic = float(head)
            except ValueError:
                continue
            break
    frame_source = [source for source in baseline['sources']
                    if source.get('iq_sha256') == frame_record.get('iq_sha256')]
    if (header.get('attempt') != 2
            or headroom.get('iq_sha256') != stage['frames/attempt-2/frame.iq']
            or not _trial_number(header_body.get('bytes'))
            or header_body['bytes'] != frame_bytes
            or not _trial_number(frame_ns) or frame_ns <= 0
            or dispatch_monotonic is None or frame_ns / 1e9 >= dispatch_monotonic
            or len(frame_source) != 1
            or frame_source[0].get('session_id') != headroom.get('session_id')
            or headroom.get('rail_count') != frame_record.get('rail_count')
            or headroom.get('scenario') != frame_record.get('scenario')):
        raise ValueError('trial acceptance evidence v2 archived frame is not the release '
                         'baseline frame captured before this trial')

    # 5. The pre-state it replaced and the post-state it reached, by the installer's own
    #    predicate; the board's own trial-state.json is the same judgement.
    pre = _trial_document(files['readback/board-download/pre-state.json'], 'pre-state')
    if (pre.get('expected_release') != trial_parent or pre.get('release_id') != trial_parent
            or pre.get('release_path') != RELEASE_PATH_PREFIX + trial_parent
            or not verified_state(pre, trial_parent)):
        raise ValueError('trial acceptance evidence v2 pre-state is not a verified safe '
                         'state of the release it replaced')
    post = _trial_document(files['readback/board-download/post-state.json'], 'post-state')
    if (post.get('expected_release') != release or post.get('release_path') != release_path
            or post.get('software_contract') != parent['software_contract']
            or not verified_state(post, release)):
        raise ValueError('trial acceptance evidence v2 post-state is not a verified safe '
                         'state of this release')
    trial_state = _trial_document(files['readback/board-download/trial-state.json'],
                                  'board trial state')
    if (trial_state.get('expected_release') != release
            or trial_state.get('release_path') != release_path
            or trial_state.get('software_contract') != parent['software_contract']
            or not verified_state(trial_state, release)):
        raise ValueError('trial acceptance evidence v2 board trial state is not verified safe')

    # 6. The independent read-only read-back and its post-cleanup repeat, each rebuilt
    #    from its own raw board reading and each handed to the frozen predicate.
    views = {}
    for phase in ('readback', 'final'):
        tag = TRIAL_V2_TAG[phase]
        document = _trial_document(files[TRIAL_V2_PREFLIGHT[phase]], tag + ' read-back')
        raw = _trial_v2_board_view(evidence, phase, tag)
        if document.get('tag') != tag:
            raise ValueError('trial acceptance evidence v2 %s names another reading' % tag)
        if (document.get('snapshot') != raw['snapshot']
                or document.get('state') != raw['state']):
            raise ValueError('trial acceptance evidence v2 %s report disagrees with the raw '
                             'board reading' % tag)
        items = document.get('items') or {}
        if set(items) != set(TRIAL_V2_ITEMS) or any(
                (item or {}).get('ok') is not True for item in items.values()):
            raise ValueError('trial acceptance evidence v2 %s checks incomplete or failing'
                             % tag)
        if (document.get('passed') is not True or document.get('failures') != []
                or document.get('board_contact') != 'read-only'
                or document.get('read_only_session_closed') is not True
                or document.get('snapshot_ok') is not True
                or document.get('snapshot_error') is not None
                or any(document.get(key) != 0 for key in
                       ('snapshot_rc', 'state_rc', 'paths_rc', 'dmesg_rc'))):
            raise ValueError('trial acceptance evidence v2 %s unverified' % tag)
        if (raw['faults'] or document.get('dmesg_faults') != []
                or items['B12_boot_identity'].get('kernel_storage_faults') != []
                or items['B7_dmesg_faults'].get('faults') != []):
            raise ValueError('trial acceptance evidence v2 %s reports kernel storage faults'
                             % tag)
        if items['B5_services'].get('services') != raw['units']:
            raise ValueError('trial acceptance evidence v2 %s services disagree with the raw '
                             'reading' % tag)
        if (items['B8_mounts_rw'].get('mounts') != raw['mounts']
                or items['B8_mounts_rw'].get('root_rw') is not True
                or items['B8_mounts_rw'].get('boot_rw') is not True
                or raw['mounts_rw'].get('root') is not True
                or raw['mounts_rw'].get('boot') is not True):
            raise ValueError('trial acceptance evidence v2 %s mounts are not read-write' % tag)
        if (items['B1_release_identity'].get('current') != release
                or items['B1_release_identity'].get('verified') != release
                or items['B1_release_identity'].get('active_release') != release_path
                or items['B2_software_map'].get('contract') != parent['software_contract']
                or items['B2_software_map'].get('modules') != len(software)
                or items['B2_software_map'].get('digest') != canonical
                or items['B2_software_map'].get('daemon_version') != parent['daemon_version']
                or items['B2_software_map'].get('protocol_version')
                   != parent['protocol_version']
                or items['B12_boot_identity'].get('boot_id') != trial['boot_id']):
            raise ValueError('trial acceptance evidence v2 %s disagrees with the verified '
                             'release' % tag)
        lock = raw['paths'].get('LOCK') or []
        if ('LOCK_FREE' not in lock or 'LOCK_SOURCE=flock_acquired' not in lock
                or items['B10_lock_free'].get('state') != 'LOCK_FREE'
                or items['B10_lock_free'].get('reason') != 'flock_acquired'
                or items['B9_no_inflight'].get('rf_jobs') != []
                or (raw['paths'].get('UNITS') or []) != []
                or (raw['paths'].get('JOBS') or []) != []):
            raise ValueError('trial acceptance evidence v2 %s deployment lock or in-flight '
                             'jobs unverified' % tag)
        # The /tmp listing prints bare names, so a trial path is recognised by its
        # basename; anything else that looks like trial residue refuses.
        residue = [entry for entry in raw['tmp'] if 'rf-' in entry
                   and not any(Path(name).name in entry
                               for name in (trial['alias'], trial['upload']))]
        if (residue != [] or raw['residue'] != items['B11_tmp_clean'].get('excused')
                or items['B11_tmp_clean'].get('residue') != []):
            raise ValueError('trial acceptance evidence v2 %s left another trial behind' % tag)
        for name in (release, trial_parent):
            if name not in (raw['paths'].get('RELEASES') or []):
                raise ValueError('trial acceptance evidence v2 %s release listing misses %s'
                                 % (tag, name))
        # The first read-back runs before the cleanup and must find both staging paths;
        # the second runs after it and must find neither.  The listing is the board's own
        # bytes, so the order of the two readings is proved rather than assumed.
        expected_residue = (sorted([trial['alias'], trial['upload']]) if phase == 'readback'
                            else [])
        for name in (trial['alias'], trial['upload']):
            present = any(Path(name).name in entry for entry in raw['tmp'])
            if present != (phase == 'readback'):
                raise ValueError('trial acceptance evidence v2 %s staging paths prove the '
                                 'read-back was taken at the wrong point of the trial' % tag)
        if sorted(raw['residue']) != expected_residue:
            raise ValueError('trial acceptance evidence v2 %s residue proves the read-back '
                             'was taken at the wrong point of the trial' % tag)
        reported = dict(document['state'])
        reported['domain'] = dict(
            env_FIXED_RF=(document['state'].get('env_FIXED_RF') or {}).get('attenuation_mdB'),
            client_ATTENUATION_MDB=document['state'].get('client_ATTENUATION_MDB'),
            profile_ATTENUATION_MDB=document['state'].get('profile_ATTENUATION_MDB'),
            ui_ATTENUATION_MDB=attenuation,
            env_VERSION=environment_version, demod_VERSION=demod_version)
        reported['build_id'] = parent['build_id']
        view = dict(phase=phase, passed=document.get('passed'),
                    board_contact=document.get('board_contact'),
                    session_closed=document.get('read_only_session_closed'),
                    snapshot_valid=document.get('snapshot_ok'),
                    expected_release=release, expected_parent=trial_parent,
                    checks={name: items[name].get('ok') for name in TRIAL_V2_ITEMS},
                    kernel_storage_faults=items['B12_boot_identity'].get(
                        'kernel_storage_faults'),
                    dmesg_faults=document.get('dmesg_faults'),
                    snapshot={key: document['snapshot'].get(key) for key in
                              ('current_release', 'active_release', 'boot_id',
                               'uptime_seconds')},
                    state=reported, tag=tag, items=items, utc=document.get('utc'))
        _trial_readback_checks(tag + ' read-back', view, phase, release, release_path,
                               trial_parent, trial, parent, software, baseline, baseline_sha,
                               canonical, attenuation, 'trial acceptance evidence v2')
        if (document['state'].get('profile_domain') != baseline.get('rf_config')
                or document['state'].get('env_FIXED_RF') != baseline.get('rf_config')):
            raise ValueError('trial acceptance evidence v2 %s profile domain disagrees with '
                             'the release baseline' % tag)
        views[phase] = view

    # 6b. The recorded decision facts have to equal the same derivation.
    facts = _trial_document(files[TRIAL_V2_FACTS], 'read-back facts')
    for phase in ('readback', 'final'):
        tag = TRIAL_V2_TAG[phase]
        document = _trial_document(files[TRIAL_V2_PREFLIGHT[phase]], tag + ' read-back')
        if facts.get(tag) != _trial_v2_facts(document):
            raise ValueError('trial acceptance evidence v2 recorded facts disagree with the '
                             'read-back: ' + tag)
        if facts[tag].get('passed') is not True or facts[tag].get('failures') != []:
            raise ValueError('trial acceptance evidence v2 recorded facts report a failure: '
                             + tag)

    # 6c. The pre-installation reading: the release that was replaced, as the board
    #     itself reported it before the dispatch.
    pre_document = _trial_document(files['preflight/pre-01-preflight.json'], 'pre read-back')
    pre_raw = _trial_v2_board_view(evidence, 'pre', 'pre-01')
    pre_items = pre_document.get('items') or {}
    pre_state = pre_document.get('state') or {}
    if (pre_document.get('snapshot') != pre_raw['snapshot']
            or pre_document.get('state') != pre_raw['state']
            or pre_document.get('tag') != 'pre-01'
            or set(pre_items) != set(TRIAL_V2_ITEMS)
            or any((item or {}).get('ok') is not True for item in pre_items.values())
            or pre_document.get('passed') is not True
            or pre_document.get('failures') != []
            or pre_document.get('board_contact') != 'read-only'
            or pre_document.get('read_only_session_closed') is not True
            or pre_raw['faults'] or pre_document.get('dmesg_faults') != []):
        raise ValueError('trial acceptance evidence v2 pre read-back unverified')
    if (pre_state.get('release_id') != trial_parent or pre_state.get('release') != trial_parent
            or pre_state.get('active_release') != RELEASE_PATH_PREFIX + trial_parent
            or pre_items['B1_release_identity'].get('verified') != trial_parent
            or pre_items['B1_release_identity'].get('current') != trial_parent
            or pre_items['B1_release_identity'].get('active_release')
               != RELEASE_PATH_PREFIX + trial_parent
            or not _trial_services_ok(pre_state.get('services'))
            or (pre_state.get('status') or {}).get('armed') is not False
            or ((pre_state.get('status') or {}).get('pl_status') or {}).get('io_ok')
               is not True
            or pre_items['B8_mounts_rw'].get('root_rw') is not True
            or pre_items['B8_mounts_rw'].get('boot_rw') is not True):
        raise ValueError('trial acceptance evidence v2 pre read-back does not name the '
                         'release it replaced as a verified safe state')
    if not str(pre_document.get('utc')) < str(views['readback'].get('utc')):
        raise ValueError('trial acceptance evidence v2 pre read-back does not precede the '
                         'deployment')
    if facts.get('pre-01') != _trial_v2_facts(pre_document):
        raise ValueError('trial acceptance evidence v2 recorded facts disagree with the '
                         'pre read-back')

    # 7. The installed release directory, file by file, against the candidate tree.
    listing = _trial_v2_listing(files['logs/13-release-listing.log'])
    candidate_files = {path.name: sha(path) for path in candidate.iterdir() if path.is_file()}
    if listing != candidate_files:
        raise ValueError('trial acceptance evidence v2 installed release tree is not the '
                         'candidate tree')
    listing_record = _trial_document(files['logs/13-release-listing.json'], 'release listing')
    if (listing_record.get('identical') is not True or listing_record.get('exit_code') != 0
            or listing_record.get('missing_on_board') != []
            or listing_record.get('extra_on_board') != []
            or listing_record.get('mismatched') != []
            or listing_record.get('host_files') != len(candidate_files)
            or listing_record.get('board_files') != len(candidate_files)
            or listing_record.get('manifest_sha256') != manifest_sha
            or listing_record.get('daemon_sha256') != parent['daemon_sha256']):
        raise ValueError('trial acceptance evidence v2 release listing record unverified')
    daemon = _trial_v2_record(files['logs/08-daemon-check.log'], 'DAEMON_CHECK', 'daemon check')
    if (daemon.get('daemon_sha256') != parent['daemon_sha256']
            or daemon.get('daemon_bytes') != (candidate / 'rf_daemon').stat().st_size
            or daemon.get('manifest_sha256') != manifest_sha
            or daemon.get('release') != release_path
            or daemon.get('active_release') != release_path
            or daemon.get('loopback_attenuation_mdB') != attenuation):
        raise ValueError('trial acceptance evidence v2 installed daemon read-back unverified')

    # 8. The downloaded board bytes: the payload the board returned, decoded and rehashed,
    #    and the placeholders that prove no rollback state was ever downloaded.
    downloads = _trial_document(
        files['readback/board-download/board-download-manifest.json'], 'board download record')
    fetch = _trial_v2_record(files['logs/fetch.log'], 'FETCH', 'fetch record')
    fetch_result = _trial_document(files['logs/fetch.result.json'], 'fetch result')
    fetched = downloads.get('files')
    if (fetch_result.get('exit_code') != 0 or fetch_result.get('error') is not None
            or type(fetched) is not dict or set(fetched) != set(fetch)
            or downloads.get('trial') != trial.get('trial_id')):
        raise ValueError('trial acceptance evidence v2 board download record unverified')
    for name in TRIAL_ROLLBACK_ARTIFACTS:
        if name not in fetched or fetched[name] is not None or fetch.get(name) is not None:
            raise ValueError('trial acceptance evidence v2 records a rollback state')
    for name in TRIAL_BOARD_DOWNLOADS:
        record = fetched.get(name)
        payload = fetch.get(name)
        path = files['readback/board-download/' + name]
        if (type(record) is not dict or type(payload) is not str
                or type(record.get('sha256')) is not str
                or re.fullmatch('[0-9a-f]{64}', record['sha256']) is None
                or not _trial_number(record.get('bytes'))
                or record['sha256'] != sha(path)
                or record['bytes'] != path.stat().st_size):
            raise ValueError('trial acceptance evidence v2 board download differs: ' + name)
        try:
            raw_bytes = base64.b64decode(payload, validate=True)
        except ValueError as exc:
            raise ValueError('trial acceptance evidence v2 fetch payload unreadable: '
                             + name) from exc
        if (hashlib.sha256(raw_bytes).hexdigest() != record['sha256']
                or len(raw_bytes) != record['bytes'] or raw_bytes != path.read_bytes()):
            raise ValueError('trial acceptance evidence v2 fetch payload differs: ' + name)

    # 9. The staged payload: the board's own re-read of the same tree, and the symlink the
    #    installer consumed, both against the local map.
    binding = _trial_v2_record(files['logs/stage-binding.log'], 'STAGE_BINDING', 'stage binding')
    verified_stage = _trial_v2_record(files['logs/upload-verify.log'], 'STAGE_VERIFIED',
                                      'staged tree')
    local_stage = _trial_document(files['logs/02-stage-local.log'], 'local stage record')
    upload = _trial_document(files['logs/03-upload.log'], 'upload record')
    if (binding.get('files') != stage
            or binding.get('alias_is_symlink') is not True
            or binding.get('alias_resolves_to') != staged['alias_target']
            or binding.get('alias_target') != staged['alias_target']
            or verified_stage.get('files') != stage
            or verified_stage.get('count') != len(stage)
            or verified_stage.get('alias') != staged['alias_target']
            or upload.get('upload') != 'OK' or upload.get('staged') != len(stage)
            or upload.get('identical_to_local') is not True
            or local_stage.get('archive_sha') != stage['candidate.tgz']
            or local_stage.get('files') != len(stage)
            or local_stage.get('boot_id') != trial['boot_id']
            or local_stage.get('trial_id') != trial.get('trial_id')
            or local_stage.get('unit') != trial['unit']
            or local_stage.get('reviewed') != trial.get('reviewed')):
        raise ValueError('trial acceptance evidence v2 staged payload unverified')

    # 10. The cleanup that removed only this trial's staging paths, and the parent
    #     rollback target that stayed intact.
    cleanup = _trial_document(files['readback/cleanup.json'], 'cleanup record')
    cleanup_report = cleanup.get('report') or {}
    cleanup_result = _trial_document(files['logs/cleanup.result.json'], 'cleanup result')
    if (cleanup.get('exit_code') != 0 or type(cleanup_report) is not dict
            or cleanup_report.get('durable_stage_still_exists') is not True
            or cleanup_report.get('upload_existed') is not True
            or cleanup_report.get('upload_removed') is not True
            or cleanup_report.get('alias_existed') is not True
            or cleanup_report.get('alias_removed') is not True
            or cleanup_report.get('tmp_rf_residue') != []
            or cleanup_result.get('exit_code') != 0 or cleanup_result.get('error') is not None
            or trial['upload'] not in str(cleanup_result.get('command'))
            or trial['alias'] not in str(cleanup_result.get('command'))):
        raise ValueError('trial acceptance evidence v2 cleanup record unverified')
    rollback_log = files['logs/16-parent-rollback-target.log'].read_text(
        encoding='utf-8', errors='replace')
    if (RELEASE_PATH_PREFIX + trial_parent not in rollback_log
            or 'PARENT_REAL_DIR' not in rollback_log
            or 'PARENT_VERIFY_RC=0' not in rollback_log):
        raise ValueError('trial acceptance evidence v2 parent rollback target is not a '
                         'verified directory')

    # 11. The receipt-equivalent: every claim the old receipt made, rebuilt from the named
    #     equivalents.  A missing equivalent refuses instead of being skipped.
    summary = _trial_v2_summary_txt(files['logs/07-acceptance-summary.txt'].read_text(
        encoding='utf-8', errors='replace'))
    daemon_summary = _trial_document(files['logs/08-daemon-check-summary.txt'], 'daemon summary')
    bundle = _trial_document(files['bundle/bundle-receipt.json'], 'bundle receipt')
    bundle_members = {}
    for item in sorted(candidate.iterdir()):
        if item.is_symlink() or not item.is_file():
            raise ValueError('trial acceptance evidence v2 nonregular candidate member')
        bundle_members['release/' + item.name] = item
    for name in sorted(software):
        bundle_members[sources[name].replace(chr(92), '/')] = candidate / name
    bundle_members = {name: dict(sha256=sha(path), bytes=path.stat().st_size)
                      for name, path in bundle_members.items()}
    if (bundle.get('release_id') != release or bundle.get('parent_release') != trial_parent
            or bundle.get('contract') != parent['software_contract']
            or bundle.get('candidate') != str(candidate.resolve())
            or bundle.get('members') != bundle_members
            or bundle.get('installed') is not False
            or bundle.get('payload_sha256', {}).get('candidate.tgz') != stage['candidate.tgz']
            or bundle.get('payload_sha256', {}).get('install.sh') != stage['install.sh']
            or bundle.get('payload_sha256', {}).get('install_state.py')
               != stage['install_state.py']):
        raise ValueError('trial acceptance evidence v2 bundle receipt unverified')
    verify_record = _trial_document(files['logs/01-verify-bundle.json'], 'verify bundle record')
    verify_log = files['logs/01-verify-bundle.log'].read_text(encoding='utf-8', errors='replace')
    if (verify_record.get('verify_bundle') != 'PASS'
            or verify_record.get('release_id') != release
            or verify_record.get('parent_release') != trial_parent
            or verify_record.get('candidate_tgz_sha256') != stage['candidate.tgz']
            or verify_record.get('install_sh_sha256') != stage['install.sh']
            or verify_record.get('install_state_py_sha256') != stage['install_state.py']
            or verify_record.get('members_count') != len(bundle_members)
            or verify_record.get('candidate') != str(candidate.resolve())
            or 'PASS' not in verify_log):
        raise ValueError('trial acceptance evidence v2 bundle build record unverified')
    if (summary.get('schema') != TRIAL_ACCEPTANCE_SCHEMA
            or summary.get('expected_release') != release
            or summary.get('ok') is not True
            or summary.get('boot_id') != trial['boot_id']
            or not _trial_v2_subset(summary.get('a'), identity)
            or not _trial_v2_subset(summary.get('b'), domain)
            or not _trial_v2_subset(summary.get('c'), recorded)
            or not _trial_v2_subset(summary.get('d'), details['d_services'])
            or not _trial_v2_subset(summary.get('e'), closed)
            or not _trial_v2_subset(summary.get('f.frame'), frame_record)
            or not _trial_v2_subset(summary.get('f.demod'), demod)
            or not _trial_v2_subset(summary.get('f.environment'), environment_leg)
            or not set(summary.get('a') or {}) >= {'release_id', 'software_contract',
                                                    'software_digest', 'software_modules',
                                                    'build_id', 'daemon_version'}
            or 'canonical_sha256' not in (summary.get('c') or {})
            or 'armed' not in (summary.get('e') or {})):
        raise ValueError('trial acceptance evidence v2 acceptance summary unverified')
    if (daemon_summary.get('release') != release_path
            or daemon_summary.get('active_release') != release_path
            or daemon_summary.get('daemon_sha256') != parent['daemon_sha256']
            or daemon_summary.get('manifest_sha256') != manifest_sha
            or daemon_summary.get('loopback_attenuation_mdB') != attenuation):
        raise ValueError('trial acceptance evidence v2 daemon summary unverified')
    pins = _trial_document(files['logs/00-candidate-pins.json'], 'candidate pins')
    pin_iq = ((pins.get('pins') or {}).get('baseline_source_iq_sha256') or {})
    if (pins.get('ok') is not True or pins.get('failures') != []
            or pins.get('candidate') != str(candidate.resolve())
            or pin_iq.get('actual') != frame_record.get('iq_sha256')
            or pin_iq.get('ok') is not True):
        raise ValueError('trial acceptance evidence v2 candidate pins unverified')

    # 12. The evidence index binds every file of the package except itself, and each
    #     digest has to be the file on disk.
    # The index is keyed by the path of the package in the repository the trial ran in,
    # and the package may since have been copied elsewhere, so the keys are read as one
    # common directory prefix plus the relative name -- never by assuming this checkout's
    # location.  A key outside that one prefix refuses by name.
    index = _trial_document(files[TRIAL_V2_INDEX], 'evidence index')
    bound = {}
    if type(index) is not dict or not index:
        raise ValueError('trial acceptance evidence v2 index does not bind every artefact')
    keys = sorted(index)
    for key in keys:
        if type(key) is not str or not key.startswith('evidence/') or '/' not in key:
            raise ValueError('trial acceptance evidence v2 index binds a file outside the '
                             'package: ' + str(key))
    # The longest common prefix of every key, cut at a directory boundary, is the package
    # directory the keys are relative to.  Comparing each key against it keeps the check
    # independent of where this checkout happens to live.
    head = keys[0]
    for key in keys[1:]:
        cut = 0
        for index_char, (first, second) in enumerate(zip(head, key)):
            if first != second:
                break
            cut = index_char + 1
        head = head[:cut]
        if not head:
            break
    home = head[:head.rfind('/') + 1]
    if not home.startswith('evidence/'):
        raise ValueError('trial acceptance evidence v2 index binds a file outside the '
                         'package: ' + keys[0])
    for key, record in index.items():
        name = key[len(home):]
        if not name or name in bound or name == TRIAL_V2_INDEX:
            raise ValueError('trial acceptance evidence v2 index does not bind every artefact')
        bound[name] = record
    names = sorted(path.relative_to(evidence).as_posix() for path in evidence.rglob('*')
                   if path.is_file() and path != files[TRIAL_V2_INDEX])
    for name in sorted(set(bound) - set(names)):
        raise ValueError('trial acceptance evidence v2 index binds a file outside the '
                         'package: ' + home + name)
    if set(bound) != set(names):
        raise ValueError('trial acceptance evidence v2 index does not bind every artefact')
    for name in names:
        record = bound[name]
        path = evidence / name
        if (type(record) is not dict or record.get('sha256') != sha(path)
                or record.get('bytes') != path.stat().st_size):
            raise ValueError('trial acceptance evidence v2 artifact digest differs: ' + name)

    return dict(release_id=release, source=TRIAL_V2_SOURCE,
                software_contract=parent['software_contract'],
                software_modules=len(software), trial_parent=trial_parent,
                trial_format=TRIAL_FORMAT_2026_10_18,
                evidence_dir=str(evidence.resolve()),
                evidence_sha256={name: sha(path) for name, path in sorted(files.items())})

# --------------------------------------------------------------------------- #
# Seventh parent route: the 2026-10-18 baseline-deployment package.
#
# 03b9b883 is the release the 2026-10-18 baseline trial installed and accepted on
# the board -- the reviewed installer, one bounded on-board acceptance and an
# independent read-only read-back -- so it has no INSTALLED_VERIFIED receipt and
# must not be given one by renaming, inventing or upgrading anything.
#
# The package is a third layout.  It shares the v2 package's bundle/ trial/
# readback/ preflight/ directories, but the readings are tagged post-01 and
# post-02 (the first post-installation reading failed its release-listing check
# because the release directory is root-only; post-02 is the corrected, passing
# repeat), there is no host receipt, no read-back summary, no facts file and no
# evidence index, and the board's own listing of the installed release tree lives
# in the post-02 preflight document and the raw log beside it.  The route rebuilds
# the same chain the frozen route rebuilds, from those real artefacts only:
#
#   trial/trial.json            one release, one parent, one unit, one staged
#                               archive and a bounded acceptance budget
#   logs/dispatch-journal.log   exactly one TRIAL_ACCEPTED release=<id>, one
#                               INSTALL_ACCEPTED, one bounded TRIAL_CHECK_BEGIN
#                               and one TRIAL_ACCEPTANCE PASS before it, in that
#                               order, and no failure/rollback/recovery marker
#   trial/dispatch-intent.json  the single dispatch recorded before it ran
#   logs/dispatch-*.{json,log}  the transport result and systemd's verdict
#   readback/board-download/    the six acceptance checks, the pre-state it
#                               replaced and the post-states it reached
#   readback/post-02-*          the independent read-only reading re-derived from
#                               its own raw logs, and the board's 51-file
#                               sha256sum of the installed release directory
#   preflight/pre-01-*          the reading taken immediately before the
#                               dispatch: the replaced release, verified safe
#   trial/stage/*               the staged payload, rehashed against its own map
#                               and against the board's own re-read of it
#   bundle/*                    the prepared payload and its receipt
#   logs/fetch.log              the bytes the board returned, decoded and
#                               re-hashed against the downloaded files
#   logs/final-stage-listing    the board's durable stage listing, which binds
#                               the downloaded reports to the bytes the board
#                               wrote
#   logs/single-dispatch-check  exactly one durable trial directory for this id
#
# Every identity and digest must agree with the candidate directory this run
# verified -- release id, contract, module inventory, canonical software digest,
# build id, daemon and protocol version, manifest/bit/HWH/daemon bytes, the
# baseline file and canonical digest, the whole release tree and the archive
# contents -- so a self-consistent rewrite inside the evidence directory still
# refuses: the values are read from the verified candidate, not from the
# documents that claim them.  A missing read-back, a failed, rolled-back or
# recovered trial, a rollback state, a changed staged payload and a read-back
# that disagrees with the release each refuse by name.  Nothing here writes,
# renames or upgrades a receipt.
# --------------------------------------------------------------------------- #
TRIAL_BASELINE_SOURCE = 'trial-acceptance-evidence-baseline-2026-10-18'
TRIAL_BASELINE_STAGE = 'trial/stage'
TRIAL_BASELINE_FRAME = 'trial/stage/frames/attempt-2/frame.iq'
TRIAL_BASELINE_FRAME_HEADER = 'trial/stage/frames/attempt-2/header.json'
TRIAL_BASELINE_PRE = 'preflight/pre-01-preflight.json'
TRIAL_BASELINE_READBACK = 'readback/post-02-preflight.json'
# The 18 checks the post-02 reading carries: the twelve board checks, the board's
# own release listing, and the five host cross-checks.  The pre-01 reading carries
# the same set without the release listing.
TRIAL_BASELINE_ITEMS = ('B1_release_identity', 'B2_software_map', 'B3_domain_46000',
                        'B4_baseline', 'B5_services', 'B6_rf_safe', 'B7_dmesg_faults',
                        'B8_mounts_rw', 'B9_no_inflight', 'B10_lock_free',
                        'B11_tmp_clean', 'B12_boot_identity', 'B13_release_listing',
                        'H1_git_head', 'H2_tool_hashes', 'H3_candidate_release',
                        'H4_candidate_domain', 'H5_candidate_baseline')
TRIAL_BASELINE_PRE_ITEMS = tuple(name for name in TRIAL_BASELINE_ITEMS
                                 if name != 'B13_release_listing')
TRIAL_BASELINE_FILES = (
    'trial/trial.json', 'trial/dispatch-intent.json', 'trial/staged-sha256.json',
    'trial/stage/stage-local-sha256.json', 'trial/stage/candidate.tgz',
    'trial/stage/install.sh', 'trial/stage/install_state.py',
    'trial/stage/acceptance.py', 'trial/stage/rf_headroom_replay.py',
    'trial/stage/trial-install.sh', TRIAL_BASELINE_FRAME, TRIAL_BASELINE_FRAME_HEADER,
    'acceptance-repin/acceptance.py', 'bundle/bundle-receipt.json',
    'bundle/candidate.tgz', 'bundle/install.sh', 'bundle/install_state.py',
    'logs/dispatch-journal.log', 'logs/dispatch-summary.json',
    'logs/dispatch.result.json', 'logs/dispatch-unit.result.json',
    'logs/dispatch-unit.log', 'logs/dispatch.log', 'logs/stage-binding.log',
    'logs/trial-bind-check.log', 'logs/upload-verify.log',
    'logs/upload-verify.result.json', 'logs/fetch.log', 'logs/fetch.result.json',
    'logs/cleanup.log', 'logs/cleanup.result.json',
    'logs/single-dispatch-check.log', 'logs/final-stage-listing.log',
    'logs/final-readonly-check.log', 'readback/cleanup.json',
    'readback/board-download/acceptance-report.json',
    'readback/board-download/pre-state.json', 'readback/board-download/post-state.json',
    'readback/board-download/trial-state.json',
    'readback/board-download/pynq-status.before',
    'readback/board-download/rf-daemon.before',
    'readback/board-download/board-download-manifest.json',
    TRIAL_BASELINE_READBACK, 'readback/post-02-01-snapshot.log',
    'readback/post-02-02-state.log', 'readback/post-02-03-paths.log',
    'readback/post-02-04-dmesg.log', 'readback/post-02-05-release-files.log',
    'readback/preflight_probe_post.py', 'readback/make_post_probe.json',
    TRIAL_BASELINE_PRE, 'preflight/pre-01-01-snapshot.log',
    'preflight/pre-01-02-state.log', 'preflight/pre-01-03-paths.log',
    'preflight/pre-01-04-dmesg.log', 'preflight/preflight_probe_pre.py')


def _trial_baseline_reading(evidence, directory, tag, what):
    """One read-only reading, re-derived from the raw board logs it wrote."""
    base = Path(evidence) / directory
    return dict(
        snapshot=_trial_v2_record(base / (tag + '-01-snapshot.log'), 'DEPLOY_SNAPSHOT',
                                  what + ' snapshot'),
        state=_trial_v2_record(base / (tag + '-02-state.log'), 'PREFLIGHT_STATE',
                               what + ' state'),
        paths=(base / (tag + '-03-paths.log')).read_text(encoding='utf-8',
                                                         errors='replace'),
        dmesg=(base / (tag + '-04-dmesg.log')).read_text(encoding='utf-8',
                                                         errors='replace'))


def _trial_baseline_units(snapshot):
    """The units of one DEPLOY_SNAPSHOT record, as systemctl's own fields."""
    units = {}
    for name, unit in sorted((snapshot.get('units') or {}).items()):
        if type(unit) is not dict or unit.get('rc') != 0 or type(unit.get('out')) is not str:
            return {}
        fields = {}
        for line in unit['out'].splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                fields[key.strip()] = value.strip()
        units[name] = fields
    return units


def _trial_baseline_reading_ok(document, raw, tag, expected, what):
    """One reading document must be the report of its own raw board bytes."""
    items = document.get('items') or {}
    if (document.get('tag') != tag
            or document.get('board_contact') != 'read-only'
            or document.get('read_only_session_closed') is not True
            or document.get('snapshot_ok') is not True
            or document.get('snapshot_error') is not None
            or any(document.get(key) != 0 for key in
                   ('snapshot_rc', 'state_rc', 'paths_rc', 'dmesg_rc'))
            or document.get('passed') is not True or document.get('failures') != []
            or document.get('dmesg_faults') != []
            or type(items) is not dict or set(items) != set(expected)
            or any((item or {}).get('ok') is not True for item in items.values())):
        raise ValueError('%s %s checks incomplete or failing' % (what, tag))
    if (document.get('snapshot') != raw['snapshot']
            or document.get('state') != raw['state']
            or (document.get('paths') or '').rstrip(chr(10))
               != raw['paths'].rstrip(chr(10))):
        raise ValueError('%s %s report disagrees with the raw board reading'
                         % (what, tag))
    kernel = ((raw['snapshot'].get('commands') or {}).get('kernel_storage') or {})
    if (kernel.get('rc') != 0
            or (kernel.get('out') or '').rstrip(chr(10))
               != raw['dmesg'].rstrip(chr(10))):
        raise ValueError('%s %s kernel log disagrees with the raw board reading'
                         % (what, tag))
    return items


def verify_installed_parent_via_trial_acceptance_evidence_baseline(parent, trial_evidence,
                                                                   parent_candidate=None):
    """Seventh route: the release the 2026-10-18 baseline trial installed.

    Rebuilds the chain from the package's own layout -- bundle/, trial/, readback/,
    preflight/ -- in the order it was written, and refuses every gap by name.  The
    package is neither the frozen 2026-10-17 layout nor the v2 2026-10-18 layout,
    so the caller has to name this format; nothing detects it and nothing falls
    back to another route.  What this proves is the release that was installed,
    accepted and still current at the two read-only readings in one boot; it does
    not manufacture an INSTALLED_VERIFIED receipt.
    """
    evidence = Path(trial_evidence)
    if not evidence.is_dir():
        raise ValueError('baseline trial acceptance evidence directory required')
    if parent_candidate is None:
        raise ValueError('baseline trial acceptance evidence route requires the parent '
                         'candidate')
    candidate = Path(parent_candidate)
    release = parent['release_id']
    release_path = RELEASE_PATH_PREFIX + release
    if (type(parent.get('software')) is not dict or not parent['software']
            or type(parent.get('software_contract')) is not str
            or type(parent.get('daemon_sha256')) is not str
            or type(parent.get('build_id')) is not str
            or type(parent.get('daemon_version')) is not str
            or parent.get('protocol_version') != 1
            or type(parent.get('bit_sha256')) is not str
            or type(parent.get('hwh_sha256')) is not str):
        raise ValueError('baseline trial acceptance evidence route requires a verified '
                         'software release')
    software = parent['software']
    sources = R.software_sources(parent['software_contract'])
    if set(sources) != set(software):
        raise ValueError('baseline trial acceptance evidence candidate software map '
                         'changed')
    canonical = _canonical_digest(software)
    manifest_sha = sha(candidate / 'merged_release_manifest.json')
    if sha(candidate / 'rf_daemon') != parent['daemon_sha256']:
        raise ValueError('baseline trial acceptance evidence parent daemon differs from '
                         'its manifest')
    baseline = _trial_document(candidate / 'rf_environment_baseline.json', 'parent baseline')
    baseline_sha = sha(candidate / 'rf_environment_baseline.json')
    baseline_body = {key: value for key, value in baseline.items() if key != 'sha256'}
    if (baseline.get('sha256') != _canonical_digest(baseline_body)
            or type(baseline.get('rf_config')) is not dict
            or type(baseline.get('sources')) is not list or not baseline['sources']
            or type(baseline.get('spectrum_envelope')) is not list):
        raise ValueError('baseline trial acceptance evidence parent baseline is not '
                         'canonical')
    attenuation = baseline['rf_config'].get('attenuation_mdB')
    if not _trial_number(attenuation) or attenuation <= 0:
        raise ValueError('baseline trial acceptance evidence parent baseline has no fixed '
                         'RF')
    files = {name: evidence / name for name in TRIAL_BASELINE_FILES}
    for name, path in files.items():
        if not path.is_file():
            raise ValueError('baseline trial acceptance evidence %s missing' % name)
    stage, staged = _trial_stage_map(evidence, TRIAL_BASELINE_STAGE,
                                     'stage-local-sha256.json', 'staged-sha256.json',
                                     'baseline trial acceptance evidence',
                                     board_path='trial/staged-sha256.json')
    candidate_files = {path.name: sha(path) for path in candidate.iterdir()
                       if path.is_file()}
    members = {}
    for item in sorted(candidate.iterdir()):
        if item.is_symlink() or not item.is_file():
            raise ValueError('baseline trial acceptance evidence nonregular candidate '
                             'member')
        members['release/' + item.name] = dict(sha256=sha(item),
                                              bytes=item.stat().st_size)
    for name in sorted(software):
        path = candidate / name
        if not path.is_file():
            raise ValueError('baseline trial acceptance evidence candidate software '
                             'missing: ' + name)
        members[sources[name].replace(chr(92), '/')] = dict(
            sha256=sha(path), bytes=path.stat().st_size)
    for name in ('candidate.tgz', 'install.sh', 'install_state.py'):
        if sha(files['bundle/' + name]) != stage[name]:
            raise ValueError('baseline trial acceptance evidence prepared payload differs '
                             'from the stage')
    try:
        inspect_archive(files[TRIAL_BASELINE_STAGE + '/candidate.tgz'], members)
    except ValueError as exc:
        raise ValueError('baseline trial acceptance evidence staged archive is not the '
                         'candidate tree: %s' % exc) from exc
    bundle = _trial_document(files['bundle/bundle-receipt.json'], 'bundle receipt')
    if (bundle.get('release_id') != release
            or bundle.get('contract') != parent['software_contract']
            or bundle.get('installed') is not False
            or bundle.get('members') != members
            or bundle.get('payload_sha256', {}).get('candidate.tgz')
               != stage['candidate.tgz']
            or bundle.get('payload_sha256', {}).get('install.sh') != stage['install.sh']
            or bundle.get('payload_sha256', {}).get('install_state.py')
               != stage['install_state.py']
            or type(bundle.get('candidate')) is not str
            or bundle['candidate'].replace(chr(92), '/').rsplit('/', 1)[-1]
               != release):
        raise ValueError('baseline trial acceptance evidence bundle receipt unverified')

    # 1. The trial intent: one release, one parent, one unit, one staged archive.
    trial = _trial_document(files['trial/trial.json'], 'trial intent')
    trial_id = trial.get('trial_id')
    trial_parent = trial.get('parent_release')
    budget = trial.get('acceptance_budget_seconds')
    scope = trial.get('scope')
    reviewed = trial.get('reviewed') or {}
    if (trial.get('release_id') != release
            or type(trial_parent) is not str
            or re.fullmatch('[0-9a-f]{32}', trial_parent) is None or trial_parent == release
            or trial.get('files') != stage
            or trial.get('archive_sha') != stage['candidate.tgz']
            or not _trial_number(budget) or not 1 <= budget <= 300
            or type(trial_id) is not str or not trial_id
            or trial.get('unit') != 'rf-trial-' + trial_id
            or trial.get('durable') != '/var/lib/rf-deployment-trials/' + trial_id
            or trial.get('alias') != '/tmp/rf-teaching-install-trial-' + trial_id
            or trial.get('upload') != '/tmp/rf-trial-upload-' + trial_id
            or type(trial.get('boot_id')) is not str
            or re.fullmatch('[0-9a-f-]{36}', trial['boot_id']) is None
            or reviewed.get('acceptance_sha256') != stage['acceptance.py']
            or reviewed.get('acceptance_requested_sha256') != stage['acceptance.py']
            or reviewed.get('replay_sha256') != stage['rf_headroom_replay.py']
            or reviewed.get('trial_install_sha256') != stage['trial-install.sh']
            or re.fullmatch('[0-9a-f]{64}', str(reviewed.get('rollback_sha256'))) is None
            or type(reviewed.get('acceptance_source')) is not str
            or not reviewed['acceptance_source'].endswith('acceptance-repin/acceptance.py')
            or type(trial.get('rollback_plan')) is not str
            or stage['install_state.py'] not in trial['rollback_plan']
            or type(scope) is not str or 'non-emitting' not in scope
            or 'no arm/TX/capture/Overlay/token' not in scope
            or type(trial.get('dispatch')) is not str or not trial['dispatch']):
        raise ValueError('baseline trial acceptance evidence intent does not name this '
                         'release')
    for key in ('upload', 'durable', 'alias'):
        if trial.get(key) != staged[key]:
            raise ValueError('baseline trial acceptance evidence intent and stage paths '
                             'disagree')
    if sha(files['acceptance-repin/acceptance.py']) != stage['acceptance.py']:
        raise ValueError('baseline trial acceptance evidence reviewed acceptance script '
                         'differs from the staged script')
    if bundle.get('parent_release') != trial_parent:
        raise ValueError('baseline trial acceptance evidence bundle receipt names another '
                         'parent')

    # 2. The journal: the acceptance happened exactly once and named this release.
    journal = files['logs/dispatch-journal.log'].read_text(encoding='utf-8',
                                                           errors='replace')
    accepted = TRIAL_TERMINAL + ' release=' + release
    if any(marker in journal for marker in
           TRIAL_REFUSED_TERMINALS + ('Traceback (most recent call last)',)):
        raise ValueError('baseline trial acceptance evidence records a failed or '
                         'rolled-back trial')
    if (journal.count(TRIAL_TERMINAL) != 1 or journal.count(accepted) != 1
            or journal.count(TRIAL_CHECK_TERMINAL) != 1
            or journal.count(TRIAL_INSTALL_TERMINAL) != 1
            or journal.count(TRIAL_ACCEPTANCE_TERMINAL) != 1):
        raise ValueError('baseline trial acceptance evidence terminal marker missing, '
                         'repeated or not this release')
    check = re.search(TRIAL_CHECK_TERMINAL + ' seconds=([0-9]+)', journal)
    if check is None or int(check.group(1)) != budget:
        raise ValueError('baseline trial acceptance evidence bounded acceptance budget '
                         'changed')
    if not (journal.index(TRIAL_INSTALL_TERMINAL) < journal.index(TRIAL_CHECK_TERMINAL)
            < journal.index(TRIAL_ACCEPTANCE_TERMINAL) < journal.index(accepted)):
        raise ValueError('baseline trial acceptance evidence journal is out of order')
    if (trial['unit'] not in journal or trial_parent not in journal
            or staged['alias'] + '/trial-install.sh' not in journal
            or stage['candidate.tgz'] not in journal):
        raise ValueError('baseline trial acceptance evidence journal does not name this '
                         'trial')

    # 3. The one dispatch: recorded before it ran, and successful in systemd's report.
    dispatch_summary = _trial_document(files['logs/dispatch-summary.json'],
                                       'dispatch summary')
    dispatch_result = _trial_document(files['logs/dispatch.result.json'], 'dispatch result')
    dispatch_intent = _trial_document(files['trial/dispatch-intent.json'],
                                      'dispatch intent')
    dispatch = dispatch_summary.get('dispatch') or {}
    intent = dispatch_summary.get('intent') or {}
    unit_text = dispatch_summary.get('unit')
    unit_fields = {}
    for line in (unit_text or '').splitlines() if type(unit_text) is str else []:
        if '=' in line:
            unit_key, unit_value = line.split('=', 1)
            unit_fields[unit_key.strip()] = unit_value.strip()
    if (type(intent) is not dict or dispatch_intent != intent
            or intent.get('unit') != trial['unit'] or intent.get('command') != trial['dispatch']
            or intent.get('release_id') != release
            or intent.get('parent_release') != trial_parent
            or intent.get('archive_sha') != stage['candidate.tgz']
            or intent.get('boot_id') != trial['boot_id']
            or not _trial_number(intent.get('acceptance_budget_seconds'))
            or intent.get('acceptance_budget_seconds') != budget
            or intent.get('staged') != staged
            or (intent.get('acceptance') or {}).get('sha256') != stage['acceptance.py']
            or (intent.get('acceptance') or {}).get('staged_sha256')
               != stage['acceptance.py']
            or (intent.get('acceptance') or {}).get('staged_file') != 'acceptance.py'
            or type(intent.get('policy')) is not str
            or 'single dispatch' not in intent['policy']):
        raise ValueError('baseline trial acceptance evidence dispatch intent unverified')
    if ('--unit=' + trial['unit'] not in trial['dispatch']
            or staged['alias'] + '/trial-install.sh' not in trial['dispatch']
            or staged['alias'] + '/acceptance.py' not in trial['dispatch']
            or release not in trial['dispatch'] or trial_parent not in trial['dispatch']
            or stage['candidate.tgz'] not in trial['dispatch']
            or (' %d python3 ' % budget) not in trial['dispatch']):
        raise ValueError('baseline trial acceptance evidence dispatch command does not '
                         'name this trial')
    unit_result = _trial_document(files['logs/dispatch-unit.result.json'],
                                  'dispatch unit result')
    unit_report = files['logs/dispatch-unit.log'].read_text(encoding='utf-8',
                                                           errors='replace')
    unit_report_fields = {}
    for line in unit_report.splitlines():
        if '=' in line:
            unit_key, unit_value = line.split('=', 1)
            unit_report_fields[unit_key.strip()] = unit_value.strip()
    dispatch_log = files['logs/dispatch.log'].read_text(encoding='utf-8',
                                                       errors='replace')
    if (dispatch_result.get('exit_code') != 0 or dispatch_result.get('error') is not None
            or dispatch_result.get('command') != trial['dispatch']
            or not _trial_number(dispatch_result.get('duration_seconds'))
            or dispatch_result['duration_seconds'] <= 0
            or dispatch.get('exit_code') != 0 or dispatch.get('error') is not None
            or dispatch.get('command') != trial['dispatch']
            or unit_fields != unit_report_fields
            or unit_fields.get('Result') != 'success'
            or unit_fields.get('ExecMainCode') != '0'
            or unit_fields.get('ExecMainStatus') != '0'
            or unit_fields.get('ActiveState') != 'inactive'
            or unit_result.get('exit_code') != 0 or unit_result.get('error') is not None
            or trial['unit'] not in str(unit_result.get('command'))
            or 'Finished with result: success' not in dispatch_log
            or 'status=0' not in dispatch_log):
        raise ValueError('baseline trial acceptance evidence dispatch did not succeed '
                         'exactly once')

    # 4. The six acceptance checks, bound to this release and to the candidate bytes.
    acceptance = _trial_document(
        files['readback/board-download/acceptance-report.json'], 'acceptance report')
    report_checks = acceptance.get('checks')
    if (acceptance.get('schema') != TRIAL_ACCEPTANCE_SCHEMA
            or acceptance.get('ok') is not True
            or acceptance.get('expected_release') != release
            or type(report_checks) is not list or not report_checks
            or any(type(item) is not dict or item.get('ok') is not True
                   for item in report_checks)):
        raise ValueError('baseline trial acceptance evidence acceptance report unverified')
    details = {item.get('name'): (item.get('detail') or {}) for item in report_checks}
    if set(details) != set(TRIAL_ACCEPTANCE_CHECKS):
        raise ValueError('baseline trial acceptance evidence acceptance checks incomplete')
    identity = details['a_release_identity']
    if (identity.get('release_id') != release or identity.get('release_verified') is not True
            or identity.get('software_contract') != parent['software_contract']
            or identity.get('software_modules') != len(software)
            or identity.get('software_digest') != canonical
            or identity.get('build_id') != parent['build_id']
            or identity.get('daemon_version') != parent['daemon_version']
            or identity.get('protocol_version') != parent['protocol_version']):
        raise ValueError('baseline trial acceptance evidence acceptance report disagrees '
                         'with the release')
    domain = details['b_domain_constants'].get('constants')
    if type(domain) is not dict or set(domain) != set(TRIAL_DOMAIN_KEYS):
        raise ValueError('baseline trial acceptance evidence domain constants incomplete')
    if (not _trial_number(domain.get('rf_environment_FIXED_RF'))
            or domain.get('rf_environment_FIXED_RF') != attenuation
            or any(domain.get(key) != attenuation for key in TRIAL_ATTENUATION_KEYS)
            or domain.get('rf_environment_VERSION') != baseline.get('environment_version')
            or domain.get('rf_bpsk_receiver_VERSION') != baseline.get('algorithm_version')):
        raise ValueError('baseline trial acceptance evidence domain constants disagree '
                         'with the release')
    environment_module = details['b_domain_constants'].get('rf_environment') or {}
    if (environment_module.get('sha256') != software.get('rf_environment.py')
            or environment_module.get('module') != release_path + '/rf_environment.py'):
        raise ValueError('baseline trial acceptance evidence domain module is not the '
                         'release module')
    recorded = details['c_baseline_load_not_formal']
    if (recorded.get('loaded') is not True
            or recorded.get('file_sha256') != baseline_sha
            or recorded.get('canonical_sha256') != baseline.get('sha256')
            or recorded.get('calibration_status') != baseline.get('calibration_status')
            or recorded.get('environment_version') != baseline.get('environment_version')
            or recorded.get('algorithm_version') != baseline.get('algorithm_version')
            or recorded.get('sources') != baseline.get('sources')
            or recorded.get('spectrum_values') != len(baseline['spectrum_envelope'])
            or type(recorded.get('formal_acceptance_baseline')) is not bool
            or (recorded.get('rf_config') or {}).get('attenuation_mdB') != attenuation):
        raise ValueError('baseline trial acceptance evidence baseline record disagrees '
                         'with the release')
    if not _trial_services_ok(details['d_services']):
        raise ValueError('baseline trial acceptance evidence acceptance services are not '
                         'running')
    closed = details['e_rf_closed']
    if (closed.get('active_release') != release_path
            or closed.get('active_release_expected') != release_path
            or closed.get('status_release_id') != release
            or closed.get('armed') is not False or closed.get('io_ok') is not True
            or closed.get('online') is not True or closed.get('stale') is not False
            or closed.get('rx_armed') is not False
            or closed.get('tx_source_selected') is not False):
        raise ValueError('baseline trial acceptance evidence acceptance RF state is not '
                         'closed')
    frame = details['f_archived_iq_smoke']
    frame_record = frame.get('frame') or {}
    frame_path = files[TRIAL_BASELINE_FRAME]
    demod = frame.get('demod') or {}
    environment_leg = frame.get('environment') or {}
    if (frame_record.get('iq_sha256') != stage['frames/attempt-2/frame.iq']
            or frame_record.get('iq_sha256') != sha(frame_path)
            or not _trial_number(frame_record.get('bytes'))
            or frame_record['bytes'] != frame_path.stat().st_size
            or (frame.get('replay_helper') or {}).get('sha256')
               != stage['rf_headroom_replay.py']
            or not _trial_number(demod.get('bit_errors')) or demod['bit_errors'] != 0
            or not _trial_number(demod.get('compared_bits'))
            or demod['compared_bits'] != 138
            or not _trial_number(demod.get('ber')) or demod['ber'] != 0
            or not _trial_number(demod.get('rail_count')) or demod['rail_count'] != 0
            or demod.get('carrier_sync') is not True
            or demod.get('timing_sync') is not True
            or demod.get('reference_sync') is not True
            or environment_leg.get('baseline_sha256') != baseline.get('sha256')
            or environment_leg.get('environment_version')
               != baseline.get('environment_version')
            or environment_leg.get('algorithm_version') != baseline.get('algorithm_version')
            or (frame.get('rf_config') or {}).get('attenuation_mdB') != attenuation
            or type(frame.get('scope')) is not str
            or 'archived frame' not in frame['scope']):
        raise ValueError('baseline trial acceptance evidence archived-frame smoke test '
                         'unverified')
    header = _trial_document(files[TRIAL_BASELINE_FRAME_HEADER], 'frame header')
    header_body = header.get('header') or {}
    headroom = header_body.get('headroom') or {}
    if (header.get('attempt') != 2
            or header_body.get('bytes') != frame_path.stat().st_size
            or headroom.get('iq_sha256') != stage['frames/attempt-2/frame.iq']
            or headroom.get('session_id') != frame_record.get('session_id')
            or headroom.get('rail_count') != frame_record.get('rail_count')
            or headroom.get('scenario') != frame_record.get('scenario')):
        raise ValueError('baseline trial acceptance evidence archived-frame header '
                         'disagrees with the smoke test')
    acceptance_state = acceptance.get('state') or {}
    if (acceptance_state.get('expected_release') != release
            or acceptance_state.get('release_id_from_name') != release
            or acceptance_state.get('release_path') != CURRENT_RELEASE_PATH
            or acceptance_state.get('boot_id') != trial['boot_id']):
        raise ValueError('baseline trial acceptance evidence acceptance state does not '
                         'name this release')

    # 5. The pre-state it replaced and the post-states it reached, by the installer's
    #    own predicate; the board's own trial-state.json is the same judgement.
    pre = _trial_document(files['readback/board-download/pre-state.json'], 'pre-state')
    if (pre.get('expected_release') != trial_parent or pre.get('release_id') != trial_parent
            or pre.get('release_path') != RELEASE_PATH_PREFIX + trial_parent
            or not verified_state(pre, trial_parent)):
        raise ValueError('baseline trial acceptance evidence pre-state is not a verified '
                         'safe state of the release it replaced')
    post = _trial_document(files['readback/board-download/post-state.json'], 'post-state')
    if (post.get('expected_release') != release or post.get('release_path') != release_path
            or post.get('software_contract') != parent['software_contract']
            or not verified_state(post, release)):
        raise ValueError('baseline trial acceptance evidence post-state is not a verified '
                         'safe state of this release')
    trial_state = _trial_document(files['readback/board-download/trial-state.json'],
                                  'board trial state')
    if (trial_state.get('expected_release') != release
            or trial_state.get('release_path') != release_path
            or trial_state.get('software_contract') != parent['software_contract']
            or not verified_state(trial_state, release)):
        raise ValueError('baseline trial acceptance evidence board trial state is not '
                         'verified safe')

    # 6. The reading taken immediately before the dispatch, re-derived from its own
    #    raw logs: the release it replaced, verified safe, with exactly this trial's
    #    two staging paths already present in /tmp.
    pre_document = _trial_document(files[TRIAL_BASELINE_PRE], 'pre read-back')
    pre_raw = _trial_baseline_reading(evidence, 'preflight', 'pre-01',
                                      'baseline trial acceptance evidence pre read-back')
    pre_items = _trial_baseline_reading_ok(pre_document, pre_raw, 'pre-01',
                                           TRIAL_BASELINE_PRE_ITEMS,
                                           'baseline trial acceptance evidence')
    pre_state = pre_document.get('state') or {}
    pre_snapshot = pre_document.get('snapshot') or {}
    if (pre_document.get('release') != trial_parent
            or pre_state.get('release') != trial_parent
            or pre_state.get('release_id') != trial_parent
            or pre_state.get('current') != RELEASE_PATH_PREFIX + trial_parent
            or pre_state.get('active_release') != RELEASE_PATH_PREFIX + trial_parent
            or pre_snapshot.get('current_release') != trial_parent
            or pre_snapshot.get('active_release') != RELEASE_PATH_PREFIX + trial_parent
            or pre_snapshot.get('boot_id') != trial['boot_id']
            or not _trial_services_ok(pre_state.get('services'))
            or not safe_state(pre_state.get('status') or {}, trial_parent)
            or (pre_items.get('B1_release_identity') or {}).get('current') != trial_parent
            or (pre_items.get('B1_release_identity') or {}).get('verified') != trial_parent
            or (pre_items.get('B1_release_identity') or {}).get('active_release')
               != RELEASE_PATH_PREFIX + trial_parent
            or (pre_items.get('B12_boot_identity') or {}).get('boot_id') != trial['boot_id']
            or (pre_items.get('B7_dmesg_faults') or {}).get('faults') != []
            or (pre_items.get('B12_boot_identity') or {}).get('kernel_storage_faults')
               != []
            or (pre_items.get('B8_mounts_rw') or {}).get('root_rw') is not True
            or (pre_items.get('B8_mounts_rw') or {}).get('boot_rw') is not True
            or (pre_items.get('B9_no_inflight') or {}).get('rf_jobs') != []
            or (pre_items.get('B10_lock_free') or {}).get('state') != 'LOCK_FREE'
            or (pre_items.get('B11_tmp_clean') or {}).get('residue') != []
            or sorted((pre_items.get('B11_tmp_clean') or {}).get('excused') or [])
               != sorted([trial['alias'], trial['upload']])):
        raise ValueError('baseline trial acceptance evidence pre read-back does not name '
                         'the release it replaced as a verified safe state')
    if (type(pre_document.get('utc')) is not str
            or type(intent.get('utc')) is not str
            or not pre_document['utc'] < intent['utc']):
        raise ValueError('baseline trial acceptance evidence pre read-back does not '
                         'precede the dispatch')

    # 7. The independent read-only reading: document, raw logs and the board's own
    #    listing all have to be one reading of this release.
    readback = _trial_document(files[TRIAL_BASELINE_READBACK], 'independent read-back')
    raw = _trial_baseline_reading(evidence, 'readback', 'post-02',
                                  'baseline trial acceptance evidence read-back')
    items = _trial_baseline_reading_ok(readback, raw, 'post-02', TRIAL_BASELINE_ITEMS,
                                       'baseline trial acceptance evidence')
    release_files = files['readback/post-02-05-release-files.log'].read_text(
        encoding='utf-8', errors='replace')
    if (readback.get('release') != release
            or (readback.get('release_files') or '').rstrip(chr(10))
               != release_files.rstrip(chr(10))):
        raise ValueError('baseline trial acceptance evidence post-02 release listing '
                         'disagrees with the raw board reading')
    if (type(readback.get('utc')) is not str
            or not intent['utc'] < readback['utc']):
        raise ValueError('baseline trial acceptance evidence read-back does not follow '
                         'the dispatch')
    state = readback.get('state') or {}
    status = state.get('status') or {}
    snapshot = readback.get('snapshot') or {}
    expected_units = {}
    if _trial_services_ok(state.get('services')):
        expected_units = {name: {key: value.get(key) for key in
                                 ('ActiveState', 'SubState', 'MainPID', 'NRestarts')}
                          for name, value in state['services'].items()}
    if (state.get('release') != release or state.get('release_id') != release
            or state.get('current') != release_path
            or state.get('active_release') != release_path
            or state.get('software_contract') != parent['software_contract']
            or state.get('software_modules') != len(software)
            or state.get('software_digest') != canonical
            or state.get('daemon_version') != parent['daemon_version']
            or state.get('protocol_version') != parent['protocol_version']
            or state.get('boot_id') != trial['boot_id']
            or snapshot.get('current_release') != release
            or snapshot.get('active_release') != release_path
            or snapshot.get('boot_id') != trial['boot_id']
            or not _trial_services_ok(state.get('services'))
            or expected_units != _trial_baseline_units(snapshot)
            or status.get('release_id') != release
            or status.get('daemon_version') != parent['daemon_version']
            or status.get('bit_sha256') != parent['bit_sha256']
            or status.get('hwh_sha256') != parent['hwh_sha256']
            or not safe_state(status, release)):
        raise ValueError('baseline trial acceptance evidence read-back disagrees with the '
                         'verified release')
    if ((items.get('B1_release_identity') or {}).get('current') != release
            or (items.get('B1_release_identity') or {}).get('verified') != release
            or (items.get('B1_release_identity') or {}).get('active_release') != release_path
            or (items.get('B2_software_map') or {}).get('contract')
               != parent['software_contract']
            or (items.get('B2_software_map') or {}).get('modules') != len(software)
            or (items.get('B2_software_map') or {}).get('digest') != canonical
            or (items.get('B2_software_map') or {}).get('daemon_version')
               != parent['daemon_version']
            or (items.get('B2_software_map') or {}).get('protocol_version')
               != parent['protocol_version']
            or (items.get('B5_services') or {}).get('services') != state['services']
            or (items.get('B6_rf_safe') or {}).get('armed') is not False
            or (items.get('B6_rf_safe') or {}).get('rx_armed') is not False
            or (items.get('B6_rf_safe') or {}).get('tx_source_selected') is not False
            or (items.get('B6_rf_safe') or {}).get('pl_io_ok') is not True
            or (items.get('B6_rf_safe') or {}).get('core') is not False
            or (items.get('B7_dmesg_faults') or {}).get('faults') != []
            or (items.get('B8_mounts_rw') or {}).get('root_rw') is not True
            or (items.get('B8_mounts_rw') or {}).get('boot_rw') is not True
            or (items.get('B9_no_inflight') or {}).get('rf_jobs') != []
            or (items.get('B10_lock_free') or {}).get('state') != 'LOCK_FREE'
            or (items.get('B11_tmp_clean') or {}).get('residue') != []
            or (items.get('B11_tmp_clean') or {}).get('excused') != []
            or (items.get('B12_boot_identity') or {}).get('boot_id') != trial['boot_id']
            or (items.get('B12_boot_identity') or {}).get('kernel_storage_faults') != []):
        raise ValueError('baseline trial acceptance evidence read-back reports a failed '
                         'item')
    baseline_state = state.get('baseline') or {}
    source_hashes = sorted({source.get('iq_sha256') for source in baseline['sources']})
    if (baseline_state.get('file_sha256') != baseline_sha
            or baseline_state.get('canonical_sha256') != baseline.get('sha256')
            or baseline_state.get('canonical_recomputed_from_board_bytes')
               != baseline.get('sha256')
            or baseline_state.get('embedded_sha256') != baseline.get('sha256')
            or baseline_state.get('calibration_status')
               != baseline.get('calibration_status')
            or baseline_state.get('environment_version')
               != baseline.get('environment_version')
            or baseline_state.get('sources') != len(baseline['sources'])
            or baseline_state.get('source_splits') != ['train']
            or baseline_state.get('source_iq_sha256s') != source_hashes
            or baseline_state.get('spectrum_values') != len(baseline['spectrum_envelope'])
            or baseline_state.get('rf_config') != baseline.get('rf_config')
            or baseline_state.get('bytes')
               != (candidate / 'rf_environment_baseline.json').stat().st_size
            or (items.get('B4_baseline') or {}).get('file_sha256') != baseline_sha
            or (items.get('B4_baseline') or {}).get('canonical_sha256')
               != baseline.get('sha256')
            or (items.get('B4_baseline') or {}).get('calibration_status')
               != baseline.get('calibration_status')
            or (items.get('B4_baseline') or {}).get('environment_version')
               != baseline.get('environment_version')):
        raise ValueError('baseline trial acceptance evidence read-back baseline disagrees '
                         'with the verified release')
    if (state.get('env_FIXED_RF') != baseline.get('rf_config')
            or state.get('profile_domain') != baseline.get('rf_config')
            or state.get('client_ATTENUATION_MDB') != attenuation
            or state.get('profile_ATTENUATION_MDB') != attenuation
            or state.get('env_VERSION') != baseline.get('environment_version')
            or (items.get('B3_domain_46000') or {}).get('env_FIXED_RF')
               != baseline.get('rf_config')
            or (items.get('B3_domain_46000') or {}).get('profile_domain')
               != baseline.get('rf_config')
            or (items.get('B3_domain_46000') or {}).get('client_ATTENUATION_MDB')
               != attenuation
            or (items.get('B3_domain_46000') or {}).get('profile_ATTENUATION_MDB')
               != attenuation
            or (items.get('B3_domain_46000') or {}).get('env_VERSION')
               != baseline.get('environment_version')):
        raise ValueError('baseline trial acceptance evidence read-back domain disagrees '
                         'with the verified release')
    h3 = items.get('H3_candidate_release') or {}
    h4 = items.get('H4_candidate_domain') or {}
    h5 = items.get('H5_candidate_baseline') or {}
    if (str(h3.get('path')).replace(chr(92), '/').rsplit('/', 1)[-1] != release
            or h3.get('release_id') != release
            or h3.get('software_contract') != parent['software_contract']
            or h3.get('software_modules') != len(software)
            or h4.get('domain') != baseline.get('rf_config')
            or h5.get('file_sha256') != baseline_sha
            or h5.get('canonical_sha256') != baseline.get('sha256')
            or h5.get('calibration_status') != baseline.get('calibration_status')
            or h5.get('environment_version') != baseline.get('environment_version')
            or h5.get('sources') != len(baseline['sources'])):
        raise ValueError('baseline trial acceptance evidence host cross-checks disagree '
                         'with the verified release')
    listing = _trial_sha256sums(readback.get('release_files'), '==RELEASE_FILES==')
    if listing is None or listing != candidate_files:
        raise ValueError('baseline trial acceptance evidence installed release tree is not '
                         'the candidate tree')
    b13 = items.get('B13_release_listing') or {}
    if (b13.get('board_files') != len(candidate_files)
            or b13.get('candidate_files') != len(candidate_files)
            or b13.get('differing') != []):
        raise ValueError('baseline trial acceptance evidence release listing record '
                         'unverified')

    # 8. The downloaded board bytes: the board's FETCH payload, decoded and re-hashed,
    #    the download record, and the placeholders that prove no rollback state.
    downloads = _trial_document(
        files['readback/board-download/board-download-manifest.json'],
        'board download record')
    fetched = downloads.get('files')
    fetch = _trial_v2_record(files['logs/fetch.log'], 'FETCH', 'fetch record')
    fetch_result = _trial_document(files['logs/fetch.result.json'], 'fetch result')
    if (type(fetched) is not dict
            or set(fetched) != set(TRIAL_BOARD_DOWNLOADS) | set(TRIAL_ROLLBACK_ARTIFACTS)
            or set(fetch) != set(fetched)
            or downloads.get('trial') != trial_id
            or type(downloads.get('fetched_utc')) is not str
            or fetch_result.get('exit_code') != 0 or fetch_result.get('error') is not None
            or staged['durable'] not in str(fetch_result.get('command'))
            or any(name not in str(fetch_result.get('command'))
                   for name in TRIAL_BOARD_DOWNLOADS)):
        raise ValueError('baseline trial acceptance evidence board download record '
                         'unverified')
    for name in TRIAL_ROLLBACK_ARTIFACTS:
        if fetched.get(name) is not None or fetch.get(name) is not None:
            raise ValueError('baseline trial acceptance evidence records a rollback state')
    for name in TRIAL_BOARD_DOWNLOADS:
        record = fetched.get(name)
        payload = fetch.get(name)
        path = files['readback/board-download/' + name]
        if (type(record) is not dict or type(payload) is not str
                or record.get('sha256') != sha(path)
                or record.get('bytes') != path.stat().st_size):
            raise ValueError('baseline trial acceptance evidence board download differs: '
                             + name)
        try:
            raw_bytes = base64.b64decode(payload, validate=True)
        except ValueError as exc:
            raise ValueError('baseline trial acceptance evidence fetch payload '
                             'unreadable: ' + name) from exc
        if (hashlib.sha256(raw_bytes).hexdigest() != record['sha256']
                or len(raw_bytes) != record['bytes']
                or raw_bytes != path.read_bytes()):
            raise ValueError('baseline trial acceptance evidence fetch payload differs: '
                             + name)

    # 9. The staged payload: the board's own re-read of the same tree, the symlink the
    #    installer consumed and the upload's own verdict, all against the local map.
    binding = _trial_v2_record(files['logs/stage-binding.log'], 'STAGE_BINDING',
                               'stage binding')
    verified_stage = _trial_v2_record(files['logs/upload-verify.log'], 'STAGE_VERIFIED',
                                      'staged tree')
    bind_check = _trial_document(files['logs/trial-bind-check.log'], 'trial binding check')
    upload_result = _trial_document(files['logs/upload-verify.result.json'],
                                    'upload verify result')
    if (binding.get('files') != stage
            or binding.get('alias_is_symlink') is not True
            or binding.get('alias_resolves_to') != staged['alias_target']
            or binding.get('alias_target') != staged['alias_target']
            or binding.get('durable') != staged['durable'] + '/stage'
            or binding.get('identical_to_local') is not True
            or binding.get('install_sh') != stage['install.sh']
            or binding.get('trial_state_exists') is not False
            or binding.get('trial_rollback_state_exists') is not False
            or verified_stage.get('files') != stage
            or verified_stage.get('count') != len(stage)
            or verified_stage.get('alias') != staged['alias_target']
            or upload_result.get('exit_code') != 0
            or upload_result.get('error') is not None
            or staged['alias'] not in str(upload_result.get('command'))
            or staged['durable'] not in str(upload_result.get('command'))
            or bind_check.get('binding') != 'OK' or bind_check.get('files') != len(stage)
            or bind_check.get('identical_to_local') is not True
            or bind_check.get('alias_target') != staged['alias_target']):
        raise ValueError('baseline trial acceptance evidence staged payload unverified')

    # 10. The cleanup that removed only this trial's staging paths, and the board's
    #     durable stage listing that still holds the reports it downloaded.
    cleanup = _trial_document(files['readback/cleanup.json'], 'cleanup record')
    report = cleanup.get('report') or {}
    cleanup_log = _trial_v2_record(files['logs/cleanup.log'], 'CLEANUP', 'cleanup record')
    cleanup_result = _trial_document(files['logs/cleanup.result.json'], 'cleanup result')
    if (cleanup.get('exit_code') != 0 or report != cleanup_log
            or report.get('durable_stage_still_exists') is not True
            or report.get('upload_existed') is not True
            or report.get('upload_removed') is not True
            or report.get('alias_existed') is not True
            or report.get('alias_removed') is not True
            or report.get('tmp_rf_residue') != []
            or cleanup_result.get('exit_code') != 0
            or cleanup_result.get('error') is not None
            or staged['upload'] not in str(cleanup_result.get('command'))
            or staged['alias'] not in str(cleanup_result.get('command'))):
        raise ValueError('baseline trial acceptance evidence cleanup record unverified')
    stage_listing_text = files['logs/final-stage-listing.log'].read_text(
        encoding='utf-8', errors='replace')
    stage_listing = _trial_sha256sums(stage_listing_text, '==STAGE_FILES==')
    if (stage_listing is None
            or set(stage_listing) != set(stage) | set(TRIAL_BOARD_DOWNLOADS)
            or any(name in stage_listing for name in TRIAL_ROLLBACK_ARTIFACTS)):
        raise ValueError('baseline trial acceptance evidence durable stage listing '
                         'unverified')
    for name in stage:
        if stage_listing.get(name) != stage[name]:
            raise ValueError('baseline trial acceptance evidence durable stage payload '
                             'differs: ' + name)
    for name in TRIAL_BOARD_DOWNLOADS:
        if stage_listing.get(name) != sha(files['readback/board-download/' + name]):
            raise ValueError('baseline trial acceptance evidence durable stage report '
                             'differs: ' + name)
    if 'DONE_STAGE_LISTING' not in stage_listing_text:
        raise ValueError('baseline trial acceptance evidence durable stage listing '
                         'incomplete')

    # 11. Exactly one dispatch, and the final read-only check that follows the reading.
    single = files['logs/single-dispatch-check.log'].read_text(encoding='utf-8',
                                                              errors='replace')
    durable = None
    lines = single.splitlines()
    if '==DURABLE==' in lines:
        durable = []
        for line in lines[lines.index('==DURABLE==') + 1:]:
            if line.startswith('=='):
                break
            durable.append(line.strip())
    if (durable is None or sum(1 for line in durable if line == trial_id) != 1
            or 'NO_UNIT_FILES' not in single or 'NO_RF_TMP' not in single
            or '/home/xilinx/overlays/awg_merged/current -> releases/' + release
               not in single
            or RELEASE_PATH_PREFIX + release not in single):
        raise ValueError('baseline trial acceptance evidence records more than one '
                         'dispatch')
    final = files['logs/final-readonly-check.log'].read_text(encoding='utf-8',
                                                            errors='replace')
    readback_uptime = snapshot.get('uptime_seconds')
    final_uptime = re.search('==UPTIME==[ ]*' + chr(10) + '[ ]*([0-9.]+)', final)
    if (not _trial_number(readback_uptime)
            or 'ALIAS_GONE' not in final or 'NO_RF_TMP' not in final
            or '==BOOTID==' not in final or trial['boot_id'] not in final
            or 'Result=success' not in final or 'ExecMainStatus=0' not in final
            or final_uptime is None
            or float(final_uptime.group(1)) < readback_uptime):
        raise ValueError('baseline trial acceptance evidence final read-only check '
                         'unverified')

    # 12. The read-back probe itself: generated from the pre-dispatch probe, and bound
    #     to the candidate facts the reading is supposed to check.
    maker = _trial_document(files['readback/make_post_probe.json'],
                            'read-back probe record')
    if (maker.get('release') != release
            or maker.get('output_sha256') != sha(files['readback/preflight_probe_post.py'])
            or maker.get('source_sha256') != sha(files['preflight/preflight_probe_pre.py'])
            or maker.get('baseline_file_sha256') != baseline_sha
            or maker.get('baseline_canonical_sha256') != baseline.get('sha256')
            or maker.get('baseline_sources') != len(baseline['sources'])
            or maker.get('software_digest') != canonical
            or maker.get('candidate_files') != len(candidate_files)
            or str(maker.get('output')).replace(chr(92), '/').rsplit('/', 1)[-1]
               != 'preflight_probe_post.py'
            or str(maker.get('source')).replace(chr(92), '/').rsplit('/', 1)[-1]
               != 'preflight_probe_pre.py'):
        raise ValueError('baseline trial acceptance evidence read-back probe unverified')

    return dict(release_id=release, source=TRIAL_BASELINE_SOURCE,
                software_contract=parent['software_contract'],
                software_modules=len(software), trial_parent=trial_parent,
                trial_format=TRIAL_FORMAT_2026_10_18_BASELINE,
                evidence_dir=str(evidence.resolve()),
                evidence_sha256={name: sha(path) for name, path in sorted(files.items())})

# Sixth parent route: a release the reviewed non-emitting trial installed but whose
# dispatch was lost to a board-side fault.  The installation transaction has no
# terminal outcome at all -- the client reported "remote install deadline; state
# unknown", the unit could not even be observed and no receipt, least of all an
# INSTALLED_VERIFIED one, was ever written -- so the release is proved instead by what
# the board did after the operator's reboot: an independent read-only read-back found
# it still current, safe and byte-identical to its candidate, and the six approved
# acceptance checks passed on the release that was running.  This route proves the
# release that is actually running.  It never claims the original installation
# transaction succeeded, and it writes, renames and upgrades nothing.
RECOVERY_SOURCE = 'recovery-acceptance-evidence'
RECOVERY_ACCEPTANCE_SCHEMA = 'environment-trial-acceptance-v1'
RECOVERY_ACCEPTANCE_CHECKS = ('a_release_identity', 'b_domain_constants',
                              'c_baseline_load_not_formal', 'd_services', 'e_rf_closed',
                              'f_archived_iq_smoke')
RECOVERY_ACCEPTANCE_MARKER = 'RECOVERY_ACCEPTANCE '
# Markers that say the recovery did not complete, or that a rollback ran.  The route
# exists because the installation transaction had no terminal state at all, so a
# capture carrying one of these is a capture of a different, failed outcome.
RECOVERY_REFUSED_MARKERS = (TRIAL_REFUSED_TERMINALS + ACTIVATION_REFUSED_TERMINALS
                            + ('RECOVERY_ACCEPTANCE_FAILED',
                               'Traceback (most recent call last)'))
RECOVERY_RECOVERY_OUTCOME = 'RECOVERED_VERIFIED_ACTIVE_RELEASE'
RECOVERY_UNKNOWN_OUTCOME = 'UNKNOWN'
# The 20 documents this route rebuilds its chain from.  Every one of them is a file the
# recovery execution itself wrote: the trial intent and the staged payload it uploaded,
# the dispatch record of the lost installation, the two read-only read-backs, the
# board's own listing of the running release, and the six-check acceptance with the raw
# capture it came from.
RECOVERY_SCRIPT_FILE = 'acceptance.py'
RECOVERY_RECOVERY_FILE = 'deployment-recovery.json'
RECOVERY_RESULT_FILE = 'logs/dispatch.result.json'
RECOVERY_READBACK_FILE = 'readback/post-01-preflight.json'
RECOVERY_LISTING_FILE = 'readback/release-listing.json'
# The recovery execution's own timestamped read-back directory.  It is named, not
# searched: a route that looks for "some state file" could be pointed at another one.
RECOVERY_STATE_FILE = 'recovery/192953/state.json'
RECOVERY_ACCEPTANCE_FILE = 'recovery/acceptance-report.json'
RECOVERY_RAW_FILE = 'recovery/acceptance-raw.log'
RECOVERY_TRIAL_FILE = 'trial/trial.json'
RECOVERY_INTENT_FILE = 'trial/dispatch-intent.json'
RECOVERY_STAGE_MAP_FILE = 'trial/stage/stage-local-sha256.json'
RECOVERY_BOARD_MAP_FILE = 'trial/staged-sha256.json'
RECOVERY_STAGE_DIRECTORY = 'trial/stage'
RECOVERY_EVIDENCE_FILES = (RECOVERY_SCRIPT_FILE, RECOVERY_RECOVERY_FILE,
                           RECOVERY_RESULT_FILE, RECOVERY_READBACK_FILE,
                           RECOVERY_LISTING_FILE, RECOVERY_STATE_FILE,
                           RECOVERY_ACCEPTANCE_FILE, RECOVERY_RAW_FILE,
                           RECOVERY_TRIAL_FILE, RECOVERY_INTENT_FILE,
                           RECOVERY_STAGE_MAP_FILE, RECOVERY_BOARD_MAP_FILE) + tuple(
    RECOVERY_STAGE_DIRECTORY + '/' + name for name in TRIAL_STAGE_FILES)
# The twelve board checks the read-back must carry and pass.  The host-side H items are
# the same driver's cross-checks of the candidate directory: their recorded path is this
# workstation's, and this route re-derives those facts from the candidate bytes instead
# of from a report about them.
RECOVERY_BOARD_ITEMS = ('B1_release_identity', 'B2_software_map', 'B3_domain_46000',
                        'B4_baseline', 'B5_services', 'B6_rf_safe', 'B7_dmesg_faults',
                        'B8_mounts_rw', 'B9_no_inflight', 'B10_lock_free',
                        'B11_tmp_clean', 'B12_boot_identity')


def _recovery_document(path, what):
    """One JSON document a read-only capture wrote, possibly beside sudo's own text.

    A capture made through sudo -S appends the prompt -- or the transport's own error --
    after the JSON the board printed, so the file as a whole is not always JSON.  The
    first line that parses as an object is that document; a second one refuses, because
    a capture carrying two documents is not a capture of one reading.
    """
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        raise ValueError('recovery acceptance evidence %s unreadable' % what) from exc
    try:
        document = json.loads(text)
    except ValueError:
        document = None
        for line in text.splitlines():
            if not line.strip().startswith('{'):
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if type(parsed) is not dict:
                continue
            if document is not None:
                raise ValueError('recovery acceptance evidence %s carries more than one '
                                 'JSON line' % what)
            document = parsed
    if type(document) is not dict:
        raise ValueError('recovery acceptance evidence %s unreadable' % what)
    return document


def _recovery_units(services):
    """One read-back's systemctl records as the field maps the unit predicate wants.

    The two read-backs store the units differently -- one keeps systemctl's own stdout,
    the other a parsed map -- so both are reduced to the same four fields here and
    judged by the one predicate the installer already uses.
    """
    units = {}
    if type(services) is not dict:
        return units
    for name in ('pynq-status', 'rf-daemon'):
        record = services.get(name)
        if type(record) is not dict:
            continue
        if 'stdout' in record:
            if record.get('rc') != 0 or type(record.get('stdout')) is not str:
                continue
            fields = {}
            for line in record['stdout'].splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    fields[key.strip()] = value.strip()
            record = fields
        units[name] = record
    return units


def verify_installed_parent_via_recovery_acceptance(parent, recovery_evidence,
                                                   parent_candidate=None):
    """Sixth route: the release a lost installation left running, recovered by read-back.

    On 2026-10-18 the reviewed non-emitting trial of 1c0894bf was dispatched once and
    never reported an outcome: the client's deadline expired with "remote install
    deadline; state unknown", the unit could not be observed afterwards and the board
    was rebooted by the operator.  No receipt -- least of all an INSTALLED_VERIFIED one
    -- exists for that installation, and the trial's own terminal state stays UNKNOWN;
    fabricating or renaming one is refused by the routes that judge receipts, and this
    route never produces, renames or upgrades one.

    What the execution did leave is a chain about the release that is actually running,
    rebuilt here in the order it was written:

      trial/trial.json            the one dispatch recorded before it happened: this
                                  release, the release it replaced, the staged archive
                                  and the reviewed acceptance script it bound
      trial/stage/                the staged payload, every member recomputed from disk
                                  and equal to the map the trial wrote and to the
                                  board's own re-read of the same tree
      logs/dispatch.result.json   the transport loss that left the installation UNKNOWN
                                  -- the precondition this route exists for
      deployment-recovery.json    the recovery the operator performed: no
                                  reinstallation, no RF capture, six checks on the
                                  recovered release
      recovery/acceptance-report  the six approved acceptance checks, all passing, with
                                  every identity and digest bound to the candidate
      recovery/acceptance-raw.log the board's own capture of that acceptance, which must
                                  be the report's own text and must record no failure
      recovery/192953/state.json  the read-only read-back of the recovery execution: the
                                  release still current, its manifest and its whole
                                  51-file listing equal to the candidate, both services
                                  running, RF closed, no storage fault, root rw
      readback/                   the second, independent read-only session: the board
                                  checks, the domain constants, the baseline, the
                                  storage faults, and the board's own listing of the
                                  release directory

    Every identity and digest is compared with the candidate directory this run verified
    -- release id, contract, module inventory, canonical software digest, build id,
    daemon and protocol version, daemon and bit byte digests, the baseline file and its
    canonical digest, the archived frame and the replay module -- so a self-consistent
    rewrite inside the evidence directory still refuses: the values are read from the
    verified candidate, not from the documents that claim them.

    What this proves is the release that was actually running on the board when the two
    read-backs were taken, in one boot.  It does not prove that the original installation
    transaction succeeded -- that outcome stays UNKNOWN -- and it says nothing about a
    later boot except that current persisted, which is what the installer resolves.  A
    release that never ran on this board has no read-back, no listing and no acceptance,
    so it is refused.  Every gap refuses by name; nothing falls back to another route,
    and no other route falls back to this one.
    """
    evidence = Path(recovery_evidence)
    if not evidence.is_dir():
        raise ValueError('recovery acceptance evidence directory required')
    if parent_candidate is None:
        raise ValueError('recovery acceptance evidence route requires the parent candidate')
    candidate = Path(parent_candidate)
    release = parent['release_id']
    release_path = RELEASE_PATH_PREFIX + release
    if (type(parent.get('software')) is not dict or not parent['software']
            or type(parent.get('software_contract')) is not str
            or type(parent.get('daemon_sha256')) is not str
            or type(parent.get('build_id')) is not str
            or type(parent.get('daemon_version')) is not str
            or parent.get('protocol_version') != 1):
        raise ValueError('recovery acceptance evidence route requires a verified software '
                         'release')
    software = parent['software']
    canonical = _canonical_digest(software)
    candidate_files = {path.name: sha(path) for path in candidate.iterdir()
                       if path.is_file()}
    if sha(candidate / 'rf_daemon') != parent['daemon_sha256']:
        raise ValueError('recovery acceptance evidence parent daemon differs from its '
                         'manifest')
    baseline = _recovery_document(candidate / 'rf_environment_baseline.json',
                                  'parent baseline')
    baseline_sha = sha(candidate / 'rf_environment_baseline.json')
    baseline_body = {key: value for key, value in baseline.items() if key != 'sha256'}
    if (baseline.get('sha256') != _canonical_digest(baseline_body)
            or type(baseline.get('rf_config')) is not dict
            or type(baseline.get('sources')) is not list or not baseline['sources']
            or type(baseline.get('spectrum_envelope')) is not list):
        raise ValueError('recovery acceptance evidence parent baseline is not canonical')
    files = {name: evidence / name for name in RECOVERY_EVIDENCE_FILES}
    for name, path in files.items():
        if not path.is_file():
            raise ValueError('recovery acceptance evidence %s missing' % name)

    # 1. The trial that installed this release and the payload it staged.  The stage is
    #    recomputed from disk and compared with both the map the trial wrote and the map
    #    the board read back from its own copy of the same tree.
    stage, staged = _trial_stage_map(evidence, RECOVERY_STAGE_DIRECTORY,
                                     'stage-local-sha256.json', 'staged-sha256.json',
                                     'recovery acceptance evidence',
                                     board_path=RECOVERY_BOARD_MAP_FILE)
    trial = _recovery_document(files[RECOVERY_TRIAL_FILE], 'trial record')
    trial_parent = trial.get('parent_release')
    if trial.get('release_id') != release:
        raise ValueError('recovery acceptance evidence trial record does not name this '
                         'release')
    if (type(trial_parent) is not str
            or re.fullmatch('[0-9a-f]{32}', trial_parent) is None
            or trial_parent == release):
        raise ValueError('recovery acceptance evidence trial record does not name the '
                         'release it replaced')
    if trial.get('files') != stage:
        raise ValueError('recovery acceptance evidence trial record does not bind the '
                         'staged files')
    if trial.get('archive_sha') != stage['candidate.tgz']:
        raise ValueError('recovery acceptance evidence trial record does not bind the '
                         'staged archive')
    if ((trial.get('reviewed') or {}).get('acceptance_sha256') != stage['acceptance.py']
            or (trial.get('reviewed') or {}).get('replay_sha256')
               != stage['rf_headroom_replay.py']):
        raise ValueError('recovery acceptance evidence trial record does not bind the '
                         'reviewed acceptance script')
    if sha(files[RECOVERY_SCRIPT_FILE]) != stage['acceptance.py']:
        raise ValueError('recovery acceptance evidence acceptance script differs from the '
                         'staged script')
    if (type(trial.get('unit')) is not str or not trial['unit']
            or trial['unit'] != 'rf-trial-' + str(trial.get('trial_id'))
            or type(trial.get('dispatch')) is not str or not trial['dispatch']
            or type(trial.get('boot_id')) is not str or not trial['boot_id']):
        raise ValueError('recovery acceptance evidence trial record does not name its '
                         'dispatch')
    for key in ('upload', 'durable', 'alias'):
        if trial.get(key) != staged[key]:
            raise ValueError('recovery acceptance evidence intent and stage paths disagree')

    # 2. The dispatch that was lost.  This route exists for a release whose installation
    #    never reached a terminal state, and it says so out loud rather than treating a
    #    missing outcome as a success.
    intent = _recovery_document(files[RECOVERY_INTENT_FILE], 'dispatch intent')
    result = _recovery_document(files[RECOVERY_RESULT_FILE], 'dispatch result')
    if (intent.get('release_id') != release or intent.get('parent_release') != trial_parent
            or intent.get('archive_sha') != stage['candidate.tgz']
            or intent.get('unit') != trial['unit']
            or intent.get('command') != trial['dispatch']
            or intent.get('boot_id') != trial['boot_id']
            or intent.get('staged') != staged
            or (intent.get('acceptance') or {}).get('sha256') != stage['acceptance.py']
            or type(intent.get('policy')) is not str
            or 'single dispatch' not in intent['policy']):
        raise ValueError('recovery acceptance evidence dispatch intent does not name this '
                         'trial')
    if result.get('command') != trial['dispatch']:
        raise ValueError('recovery acceptance evidence dispatch result does not name the '
                         'trial dispatch')
    if result.get('exit_code') is not None:
        raise ValueError('recovery acceptance evidence dispatch result is not the '
                         'recorded transport loss')
    if type(result.get('error')) is not str or 'state unknown' not in result['error']:
        raise ValueError('recovery acceptance evidence dispatch result does not record '
                         'the unknown state')
    recovery = _recovery_document(files[RECOVERY_RECOVERY_FILE], 'recovery record')
    if (recovery.get('outcome') != RECOVERY_RECOVERY_OUTCOME
            or recovery.get('original_trial_outcome') != RECOVERY_UNKNOWN_OUTCOME):
        raise ValueError('recovery acceptance evidence recovery record does not report '
                         'the unknown original trial outcome')
    if recovery.get('release_id') != release:
        raise ValueError('recovery acceptance evidence recovery record does not name this '
                         'release')
    if recovery.get('reinstallation') is not False:
        raise ValueError('recovery acceptance evidence recovery record reports a '
                         'reinstallation')
    if recovery.get('rf_captures') != 0:
        raise ValueError('recovery acceptance evidence recovery record reports rf captures')
    if (recovery.get('acceptance_passed') is not True
            or recovery.get('acceptance_checks') != len(RECOVERY_ACCEPTANCE_CHECKS)):
        raise ValueError('recovery acceptance evidence recovery record does not carry the '
                         'six acceptance checks')

    # 3. The six approved checks on the recovered release, every value bound to the
    #    candidate the gate verified.
    acceptance = _recovery_document(files[RECOVERY_ACCEPTANCE_FILE], 'acceptance report')
    report = acceptance.get('report') or {}
    checks = report.get('checks')
    if (report.get('schema') != RECOVERY_ACCEPTANCE_SCHEMA or report.get('ok') is not True
            or acceptance.get('ok') is not True):
        raise ValueError('recovery acceptance evidence acceptance report unverified')
    if report.get('expected_release') != release:
        raise ValueError('recovery acceptance evidence acceptance report does not name '
                         'this release')
    if (type(checks) is not list
            or [check.get('name') for check in checks if type(check) is dict]
               != list(RECOVERY_ACCEPTANCE_CHECKS)):
        raise ValueError('recovery acceptance evidence acceptance report does not carry '
                         'the six approved checks')
    failed = [check['name'] for check in checks if check.get('ok') is not True]
    if failed:
        raise ValueError('recovery acceptance evidence acceptance report failed check: '
                         + ', '.join(failed))
    if any(type(check.get('detail')) is not dict for check in checks):
        raise ValueError('recovery acceptance evidence acceptance report check detail '
                         'missing')
    details = {check['name']: check['detail'] for check in checks}
    identity = details['a_release_identity']
    if (identity.get('release_id') != release or identity.get('release_verified') is not True
            or identity.get('software_contract') != parent['software_contract']
            or identity.get('software_modules') != len(software)
            or identity.get('software_digest') != canonical
            or identity.get('build_id') != parent['build_id']
            or identity.get('daemon_version') != parent['daemon_version']
            or identity.get('protocol_version') != parent['protocol_version']):
        raise ValueError('recovery acceptance evidence acceptance report identity '
                         'disagrees with the verified release')
    domain = details['b_domain_constants'].get('constants')
    if type(domain) is not dict or set(domain) != set(TRIAL_DOMAIN_KEYS):
        raise ValueError('recovery acceptance evidence acceptance domain constants '
                         'incomplete')
    attenuation = domain.get('rf_environment_FIXED_RF')
    if (not _trial_number(attenuation) or attenuation <= 0
            or any(domain.get(key) != attenuation for key in TRIAL_ATTENUATION_KEYS)
            or domain.get('rf_environment_VERSION') != baseline.get('environment_version')
            or domain.get('rf_bpsk_receiver_VERSION') != baseline.get('algorithm_version')):
        raise ValueError('recovery acceptance evidence acceptance domain constants '
                         'disagree with the verified release')
    environment_module = details['b_domain_constants'].get('rf_environment') or {}
    if (environment_module.get('sha256') != software.get('rf_environment.py')
            or environment_module.get('module') != release_path + '/rf_environment.py'):
        raise ValueError('recovery acceptance evidence acceptance domain module is not the '
                         'release module')
    recorded = details['c_baseline_load_not_formal']
    if (recorded.get('loaded') is not True
            or recorded.get('file_sha256') != baseline_sha
            or recorded.get('canonical_sha256') != baseline.get('sha256')
            or recorded.get('calibration_status') != baseline.get('calibration_status')
            or recorded.get('environment_version') != baseline.get('environment_version')
            or recorded.get('algorithm_version') != baseline.get('algorithm_version')
            or type(recorded.get('sources')) is not list or not recorded['sources']
            or any((source or {}).get('split') != 'train' for source in recorded['sources'])
            or recorded.get('spectrum_values') != len(baseline['spectrum_envelope'])
            or type(recorded.get('formal_acceptance_baseline')) is not bool
            or (recorded.get('rf_config') or {}).get('attenuation_mdB') != attenuation):
        raise ValueError('recovery acceptance evidence acceptance baseline disagrees with '
                         'the verified release')
    if not _trial_services_ok(details['d_services']):
        raise ValueError('recovery acceptance evidence acceptance services are not running')
    closed = details['e_rf_closed']
    if (closed.get('active_release') != release_path
            or closed.get('active_release_expected') != release_path
            or closed.get('status_release_id') != release
            or closed.get('armed') is not False or closed.get('io_ok') is not True
            or closed.get('online') is not True or closed.get('stale') is not False
            or closed.get('rx_armed') is not False
            or closed.get('tx_source_selected') is not False):
        raise ValueError('recovery acceptance evidence acceptance RF state is not closed')
    frame = details['f_archived_iq_smoke']
    frame_record = frame.get('frame') or {}
    frame_path = files[RECOVERY_STAGE_DIRECTORY + '/frames/attempt-2/frame.iq']
    demod = frame.get('demod') or {}
    environment_leg = frame.get('environment') or {}
    if (frame_record.get('iq_sha256') != stage['frames/attempt-2/frame.iq']
            or frame_record.get('iq_sha256') != sha(frame_path)
            or not _trial_number(frame_record.get('bytes'))
            or frame_record['bytes'] != frame_path.stat().st_size
            or (frame.get('replay_helper') or {}).get('sha256')
               != stage['rf_headroom_replay.py']
            or not _trial_number(demod.get('bit_errors')) or demod['bit_errors'] != 0
            or not _trial_number(demod.get('compared_bits')) or demod['compared_bits'] != 138
            or not _trial_number(demod.get('ber')) or demod['ber'] != 0
            or not _trial_number(demod.get('rail_count')) or demod['rail_count'] != 0
            or demod.get('carrier_sync') is not True or demod.get('timing_sync') is not True
            or demod.get('reference_sync') is not True
            or environment_leg.get('baseline_sha256') != baseline.get('sha256')
            or environment_leg.get('environment_version')
               != baseline.get('environment_version')
            or environment_leg.get('algorithm_version') != baseline.get('algorithm_version')
            or (frame.get('rf_config') or {}).get('attenuation_mdB') != attenuation):
        raise ValueError('recovery acceptance evidence acceptance archived-frame smoke test '
                         'unverified')
    acceptance_state = report.get('state') or {}
    if (acceptance_state.get('expected_release') != release
            or acceptance_state.get('release_id_from_name') != release
            or acceptance_state.get('release_path') != CURRENT_RELEASE_PATH):
        raise ValueError('recovery acceptance evidence acceptance state does not name this '
                         'release')
    boot_id = acceptance_state.get('boot_id')
    if type(boot_id) is not str or not boot_id:
        raise ValueError('recovery acceptance evidence acceptance state was taken under '
                         'another boot')
    if recovery.get('boot_id') != boot_id:
        raise ValueError('recovery acceptance evidence recovery record does not name the '
                         'recovery boot')

    # 4. The board's own capture of that acceptance: exactly one record, byte-compatible
    #    with the report, and no failure or rollback marker anywhere in it.
    raw = files[RECOVERY_RAW_FILE].read_text(encoding='utf-8', errors='replace')
    if any(marker in raw for marker in RECOVERY_REFUSED_MARKERS):
        raise ValueError('recovery acceptance evidence raw capture records a failed or '
                         'rolled-back recovery')
    records = [line for line in raw.splitlines()
               if line.startswith(RECOVERY_ACCEPTANCE_MARKER)]
    if not records:
        raise ValueError('recovery acceptance evidence raw capture carries no acceptance '
                         'record')
    if len(records) != 1:
        raise ValueError('recovery acceptance evidence raw capture records more than one '
                         'acceptance')
    try:
        captured = json.loads(records[0][len(RECOVERY_ACCEPTANCE_MARKER):])
    except ValueError as exc:
        raise ValueError('recovery acceptance evidence raw capture disagrees with the '
                         'acceptance report') from exc
    if captured != acceptance:
        raise ValueError('recovery acceptance evidence raw capture disagrees with the '
                         'acceptance report')

    # 5. The read-back of the recovery execution itself: the release it left running, its
    #    manifest, both services, the RF state, the kernel log, its own listing of the
    #    release directory and the durable stage, all in the boot the acceptance ran in.
    state = _recovery_document(files[RECOVERY_STATE_FILE], 'recovery state')
    if state.get('boot_id') != boot_id:
        raise ValueError('recovery acceptance evidence recovery read-back was taken under '
                         'another boot than the acceptance')
    if (state.get('current') != release_path
            or state.get('active_release') != release_path):
        raise ValueError('recovery acceptance evidence recovery read-back does not name '
                         'this release as current')
    state_manifest = state.get('manifest') or {}
    if (state_manifest.get('release_id') != release
            or state_manifest.get('software') != software
            or state_manifest.get('daemon_sha256') != parent['daemon_sha256']
            or state_manifest.get('bit_sha256') != parent['bit_sha256']):
        raise ValueError('recovery acceptance evidence recovery read-back manifest '
                         'disagrees with the verified release')
    if not _trial_services_ok(_recovery_units(state.get('services'))):
        raise ValueError('recovery acceptance evidence recovery read-back services are not '
                         'both running')
    if storage_faults((state.get('dmesg') or {}).get('stdout')):
        raise ValueError('recovery acceptance evidence recovery read-back reports kernel '
                         'storage faults')
    if not safe_state(state.get('status') or {}, release):
        raise ValueError('recovery acceptance evidence recovery read-back reports RF that '
                         'is not safe')
    mounts = [line.split() for line in (state.get('mounts') or [])
              if type(line) is str and len(line.split()) >= 4 and line.split()[1] == '/']
    if len(mounts) != 1 or 'rw' not in mounts[0][3].split(','):
        raise ValueError('recovery acceptance evidence recovery read-back root is not '
                         'read-write')
    if (state.get('stage_exists') is not True
            or not set(TRIAL_STAGE_FILES) <= set(state.get('stage_files') or [])):
        raise ValueError('recovery acceptance evidence recovery read-back durable trial '
                         'stage is incomplete')
    directory_facts = (state.get('candidate_dirs') or {}).get(release) or {}
    if (directory_facts.get('exists') is not True
            or directory_facts.get('symlink') is not False
            or directory_facts.get('verified_release') != release
            or directory_facts.get('files') != candidate_files):
        raise ValueError('recovery acceptance evidence recovery read-back release listing '
                         'is not the candidate tree')

    # 6. The independent read-only session on the same boot: board checks, domain,
    #    baseline, storage faults and the board's own second listing of the release.
    readback = _recovery_document(files[RECOVERY_READBACK_FILE], 'read-back')
    if (readback.get('board_contact') != 'read-only'
            or readback.get('read_only_session_closed') is not True):
        raise ValueError('recovery acceptance evidence read-back is not the read-only '
                         'session it claims')
    if readback.get('passed') is not True or readback.get('failures') != []:
        raise ValueError('recovery acceptance evidence read-back reports failures')
    items = readback.get('items') or {}
    if (not set(RECOVERY_BOARD_ITEMS) <= set(items)
            or any((items.get(name) or {}).get('ok') is not True
                   for name in RECOVERY_BOARD_ITEMS)):
        raise ValueError('recovery acceptance evidence read-back reports a failed item')
    if readback.get('dmesg_faults') != []:
        raise ValueError('recovery acceptance evidence read-back reports storage faults')
    snapshot = readback.get('snapshot') or {}
    readback_state = readback.get('state') or {}
    if (snapshot.get('boot_id') != boot_id
            or snapshot.get('current_release') != release
            or snapshot.get('active_release') != release_path
            or readback_state.get('release_id') != release
            or readback_state.get('release') != release
            or readback_state.get('active_release') != release_path
            or (items.get('B1_release_identity') or {}).get('current') != release
            or (items.get('B1_release_identity') or {}).get('active_release') != release_path):
        raise ValueError('recovery acceptance evidence read-back does not name this release')
    if (readback_state.get('software_contract') != parent['software_contract']
            or readback_state.get('software_modules') != len(software)
            or readback_state.get('software_digest') != canonical
            or readback_state.get('daemon_version') != parent['daemon_version']
            or readback_state.get('protocol_version') != parent['protocol_version']
            or (items.get('B2_software_map') or {}).get('digest') != canonical
            or (items.get('B2_software_map') or {}).get('modules') != len(software)):
        raise ValueError('recovery acceptance evidence read-back software map disagrees '
                         'with the verified release')
    if not _trial_services_ok(readback_state.get('services')):
        raise ValueError('recovery acceptance evidence read-back services are not both '
                         'running')
    if (items.get('B5_services') or {}).get('services') != readback_state.get('services'):
        raise ValueError('recovery acceptance evidence read-back services are not both '
                         'running')
    if (items.get('B8_mounts_rw') or {}).get('root_rw') is not True:
        raise ValueError('recovery acceptance evidence read-back root is not read-write')
    if not safe_state(readback_state.get('status') or {}, release):
        raise ValueError('recovery acceptance evidence read-back reports RF that is not safe')
    readback_baseline = readback_state.get('baseline') or {}
    if (readback_baseline.get('file_sha256') != baseline_sha
            or readback_baseline.get('canonical_sha256') != baseline.get('sha256')
            or readback_baseline.get('calibration_status')
               != baseline.get('calibration_status')
            or readback_baseline.get('environment_version')
               != baseline.get('environment_version')
            or (readback_baseline.get('rf_config') or {}).get('attenuation_mdB') != attenuation
            or (items.get('B4_baseline') or {}).get('canonical_sha256')
               != baseline.get('sha256')):
        raise ValueError('recovery acceptance evidence read-back baseline disagrees with '
                         'the verified release')
    # The read-back records the fixed RF as the whole parameters map and the attenuation
    # beside it, so the domain is compared with the candidate's own baseline rather than
    # with the report about it: the acceptance's four constants were already bound above.
    if (readback_state.get('profile_domain') != baseline.get('rf_config')
            or readback_state.get('env_FIXED_RF') != baseline.get('rf_config')
            or readback_state.get('client_ATTENUATION_MDB') != attenuation
            or readback_state.get('profile_ATTENUATION_MDB') != attenuation
            or readback_state.get('env_VERSION') != baseline.get('environment_version')
            or (items.get('B3_domain_46000') or {}).get('client_ATTENUATION_MDB')
               != attenuation
            or (items.get('B3_domain_46000') or {}).get('env_FIXED_RF')
               != baseline.get('rf_config')):
        raise ValueError('recovery acceptance evidence read-back domain disagrees with the '
                         'verified release')
    listing = _recovery_document(files[RECOVERY_LISTING_FILE], 'release listing')
    if listing.get('release_id') != release:
        raise ValueError('recovery acceptance evidence release listing does not name this '
                         'release')
    if listing.get('daemon_sha256') != parent['daemon_sha256']:
        raise ValueError('recovery acceptance evidence release listing daemon disagrees '
                         'with the verified release')
    if listing.get('files') != candidate_files:
        raise ValueError('recovery acceptance evidence release listing is not the '
                         'candidate tree')

    return dict(release_id=release, source=RECOVERY_SOURCE,
                software_contract=parent['software_contract'],
                software_modules=len(software), trial_parent=trial_parent,
                recovery_boot_id=boot_id, recovery_outcome=recovery['outcome'],
                evidence_dir=str(evidence.resolve()),
                evidence_sha256={name: sha(path) for name, path in sorted(files.items())})


def verify_parent(parent_candidate, parent_install=None, *, activation_evidence=None):
    """The release installed now, as the rollback parent of the next installation.

    Four routes, each proved by evidence rather than by a name: the historical
    adfac installation with its legacy post-state.json, the b67a recovery proof,
    the release the board has installed right now, whatever its id, proved by the
    chain verify_installed_parent rebuilds -- and, when the caller names the
    activation record, the reviewed activation evidence chain
    verify_installed_parent_via_activation_evidence rebuilds.  Recovery documents
    are historical. Only the installed software and immutable observations are
    rebound here, not mutable CLAUDE/plans/deployment sources.  The original
    transport UNKNOWN receipt remains untouched.
    """
    parent_candidate = Path(parent_candidate)
    parent = R.verify_release(parent_candidate, require_software=True)
    release = parent['release_id']
    if activation_evidence is not None:
        proof = verify_installed_parent_via_activation_evidence(parent, activation_evidence)
        return proof['release_id']
    if parent_install is None:
        raise ValueError('verified rollback parent installation receipt required')
    parent_install = Path(parent_install)
    if not parent_install.is_file():
        raise ValueError('verified rollback parent installation receipt required')
    proof = json.loads(parent_install.read_text(encoding='utf8'))
    if release == ROLLBACK_PARENT:
        post = parent_install.parent / 'post-state.json'
        if (proof.get('installed') is not True or proof.get('release_id') != release
                or proof.get('board_state') != 'VERIFIED_SAFE_NEW_RELEASE'
                or not post.is_file()
                or sha(post) != proof.get('evidence_sha256', {}).get('post-state.json')
                or not verified_state(json.loads(post.read_text('utf8')), release)):
            raise ValueError('verified rollback parent installation required')
        return release
    if release != RECOVERED_PARENT:
        return verify_installed_parent(parent, parent_install, proof)
    if (proof.get('passed') is not True or proof.get('installed_release') != release
            or proof.get('activation_verified_after_reboot') is not True
            or proof.get('original_install_result') != 'UNKNOWN'
            or proof.get('original_install_receipt_preserved') is not True
            or proof.get('safe') is not True or proof.get('service_units_match') is not True
            or proof.get('root_read_write') is not True
            or proof.get('current_boot_storage_errors') != []
            or proof.get('software') != parent['software']):
        raise ValueError('verified rollback parent recovery required')
    documents = {}
    for name in ('final-safe-state.json', 'final-os-observation.json', 'final-observation-receipt.json'):
        path = parent_install.parent / name
        key = 'evidence/rf-teaching/s2/board-recovery-2026-10-12/' + name
        if not path.is_file() or sha(path) != proof.get('artifacts_sha256', {}).get(key):
            raise ValueError('verified rollback parent recovery hash differs: ' + name)
        documents[name] = json.loads(path.read_text('utf8'))
    state = documents['final-safe-state.json']
    observation = documents['final-os-observation.json']
    receipt = documents['final-observation-receipt.json']
    units = observation.get('units') or {}
    mounts = [line.split() for line in observation.get('mounts', '').splitlines()
              if len(line.split()) >= 4 and line.split()[1] == '/']
    if (not verified_state(state, release) or receipt.get('passed') is not True
            or receipt.get('read_only') is not True or receipt.get('safe') is not True
            or receipt.get('release_id') != release or receipt.get('root_read_write') is not True
            or receipt.get('service_units_match') is not True
            or receipt.get('services') != state.get('services')
            or receipt.get('current_boot_storage_errors') != []
            or type(observation.get('kernel')) is not str or storage_faults(observation['kernel'])
            or len(mounts) != 1 or 'rw' not in mounts[0][3].split(',')
            or set(units) != {'pynq-status', 'rf-daemon'}
            or any(units[name] != dict(installed=parent['software'][name + '.service'],
                                       release=parent['software'][name + '.service']) for name in units)):
        raise ValueError('verified rollback parent recovery observations required')
    return release


class InstalledReleaseRefused(ValueError):
    """The installed release is not one the original installation evidence approves.

    A named refusal rather than a bare assertion: every route that refuses says
    which document or which release id it refused, so a report can quote the
    reason instead of restating a verdict.
    """


def resolve_installed_release_evidence(acceptance_path=None, installer_receipt_path=None):
    """The release the original installation evidence proves, and what it binds.

    The read-only checker used to carry the expected release as a constant.  That
    was true only while the release the constant named was installed; since the
    2026-10-13 activation the board runs b82b141e, so the expectation is resolved
    from the documents that activation wrote rather than from a name:

      acceptance receipt (schema, accepted, one verdict, every check, no RF)
        -> installer receipt, by the sha256 the acceptance receipt records
        -> the read-back state that installer receipt binds, by sha256
        -> verified_state, the same predicate the installer itself uses.

    A missing document, a digest left over from another installation, an
    installation that is not recorded as INSTALLED_VERIFIED, evidence that
    disagrees with itself about the release, or a read-back that is not a verified
    safe state all refuse.  The returned 'approved' tuple is the complete set of
    releases a later read-back may report: the evidence-proven release and the
    historical recovery parent, and nothing else.
    """
    acceptance_path = (ROOT / ACCEPTANCE_EVIDENCE if acceptance_path is None
                       else Path(acceptance_path))
    installer_receipt_path = (ROOT / INSTALLER_RECEIPT_EVIDENCE
                              if installer_receipt_path is None
                              else Path(installer_receipt_path))
    if not acceptance_path.is_file():
        raise InstalledReleaseRefused('original installation evidence missing')
    acceptance = json.loads(acceptance_path.read_text(encoding='utf-8'))
    if (acceptance.get('schema') != INSTALL_ACCEPTANCE_SCHEMA
            or acceptance.get('accepted') is not True
            or acceptance.get('verdict') != INSTALL_ACCEPTANCE_VERDICT):
        raise InstalledReleaseRefused(
            'original installation evidence is not an accepted verification')
    if acceptance.get('new_rf_frames') != 0 or acceptance.get('real_initializations') != 0:
        raise InstalledReleaseRefused('original installation evidence records RF activity')
    checks = acceptance.get('checks')
    if type(checks) is not dict or not checks or any(v is not True for v in checks.values()):
        raise InstalledReleaseRefused('original installation evidence failed check')
    release = acceptance.get('expected_release')
    if type(release) is not str or re.fullmatch('[0-9a-f]{32}', release) is None:
        raise InstalledReleaseRefused('original installation evidence names no release')
    bound = acceptance.get('installer_receipt')
    if type(bound) is not dict:
        raise InstalledReleaseRefused('original installation evidence binds no installer receipt')
    if not installer_receipt_path.is_file():
        raise InstalledReleaseRefused('original installation evidence installer receipt missing')
    if type(bound.get('sha256')) is not str or sha(installer_receipt_path) != bound['sha256']:
        raise InstalledReleaseRefused(
            'original installation evidence digest differs from the installer receipt')
    receipt = json.loads(installer_receipt_path.read_text(encoding='utf-8'))
    if receipt.get('installed') is not True or bound.get('installed') is not True:
        raise InstalledReleaseRefused(
            'original installation evidence does not record an installed release')
    if (receipt.get('outcome') != INSTALLER_RECEIPT_OUTCOME
            or bound.get('outcome') != INSTALLER_RECEIPT_OUTCOME):
        raise InstalledReleaseRefused(
            'original installation evidence outcome is not INSTALLED_VERIFIED')
    if (receipt.get('board_state') != INSTALLER_RECEIPT_STATE
            or bound.get('board_state') != INSTALLER_RECEIPT_STATE):
        raise InstalledReleaseRefused(
            'original installation evidence board state is not verified safe')
    # The two documents, and the summary and the snapshot the acceptance receipt
    # took of the same installation, must all name the one release.
    snapshot = acceptance.get('snapshot') or {}
    summary = acceptance.get('state') or {}
    if (receipt.get('release_id') != release
            or snapshot.get('current_release') != release
            or snapshot.get('active_release') != RELEASE_PATH_PREFIX + release
            or summary.get('release_id') != release
            or summary.get('active_release') != RELEASE_PATH_PREFIX + release):
        raise InstalledReleaseRefused('original installation evidence disagrees about the release')
    digests = receipt.get('evidence_sha256')
    names = [name for name in INSTALL_READBACKS
             if type(digests) is dict and isinstance(digests.get(name), str)]
    if len(names) != 1:
        raise InstalledReleaseRefused(
            'original installation evidence does not bind exactly one read-back')
    readback_path = installer_receipt_path.parent / names[0]
    if not readback_path.is_file() or sha(readback_path) != digests[names[0]]:
        raise InstalledReleaseRefused('original installation evidence read-back unverified')
    readback = json.loads(readback_path.read_text(encoding='utf-8'))
    if readback.get('expected_release') != release or not verified_state(readback, release):
        raise InstalledReleaseRefused(
            'original installation evidence read-back is not a verified safe state')
    return dict(release_id=release, parent_release=receipt.get('parent_release'),
                acceptance_sha256=sha(acceptance_path),
                installer_receipt_sha256=sha(installer_receipt_path),
                install_readback_sha256=sha(readback_path),
                install_readback=readback_path.name,
                install_boot_id=snapshot.get('boot_id'),
                approved=((release,) if release == RECOVERED_PARENT
                          else (release, RECOVERED_PARENT)))
def approve_installed_readback(evidence, readback, *, readback_boot_id, boot_id,
                               storage_faults, root_read_write):
    """Approve the release an execution-time read-only read-back reports.

    The read-back must be the state document produced during the execution that is
    asking, and readback_boot_id the boot identity read in the same session as it.
    This is not a gate a name can pass: the document has to be a verified safe
    state of the release it names, that release has to be one the original
    installation evidence approves, the read-back has to come from the boot that is
    asking, the root filesystem has to be read/write and the kernel must report no
    storage fault.

    A historical document offered on its own, a document from another boot, an
    unsafe state (services, RF, storage, root) and a drifted release each refuse by
    name.  Nothing here widens what the deployment gate already requires.
    """
    if type(readback) is not dict:
        raise InstalledReleaseRefused('execution-time read-back required')
    if (type(readback_boot_id) is not str or type(boot_id) is not str
            or not boot_id or readback_boot_id != boot_id):
        raise InstalledReleaseRefused(
            'execution-time read-back was taken under another boot')
    release = readback.get('release_id')
    if type(release) is not str or not release:
        raise InstalledReleaseRefused('execution-time read-back names no release')
    # First question: may this release be accepted at all?  A read-back of another
    # release was taken for that other release, so its own safe/verified verdict
    # says nothing about the installation this check is about.
    if release not in evidence['approved']:
        raise InstalledReleaseRefused(
            'the installed release is neither the evidence-proven release nor the historical '
            'recovery parent: ' + release)
    services = readback.get('services')
    if type(services) is not dict or set(services) != {'pynq-status', 'rf-daemon'} or any(
            type(unit) is not dict or unit.get('ActiveState') != 'active'
            or unit.get('SubState') != 'running'
            or not str(unit.get('MainPID', '')).isdigit() or int(unit['MainPID']) <= 0
            for unit in services.values()):
        raise InstalledReleaseRefused(
            'execution-time read-back services are not both running')
    if readback.get('safe') is not True or not safe_state(readback.get('status') or {}, release):
        raise InstalledReleaseRefused('execution-time read-back reports RF that is not safe')
    if list(storage_faults):
        raise InstalledReleaseRefused('execution-time read-back reports kernel storage faults')
    if root_read_write is not True:
        raise InstalledReleaseRefused('execution-time read-back root is not read-write')
    if readback.get('expected_release') != release or not verified_state(readback, release):
        raise InstalledReleaseRefused(
            'execution-time read-back is not a verified safe state of the release it names')
    return dict(release_id=release,
                source=('original-installation-evidence'
                        if release == evidence['release_id']
                        else 'historical-recovery-parent'))


def verify_gates(candidate, arm_path, parent_candidate, parent_install, checkpoint,
                 parent_activation=None, parent_trial_acceptance=None,
                 parent_trial_acceptance_format=None, parent_recovery_acceptance=None):
    """Every fact the installation rests on, checked against files on disk."""
    candidate, arm_path = Path(candidate).resolve(), Path(arm_path).resolve()
    parent_candidate = Path(parent_candidate).resolve()
    parent_install = None if parent_install is None else Path(parent_install).resolve()
    checkpoint = Path(checkpoint).resolve()
    manifest = R.verify_release(candidate, require_software=True)
    if manifest.get('software_contract') != CONTRACT:
        raise ValueError('verified HEADROOM-1 candidate required')
    if not set(RESEARCH_MODULES) <= set(manifest['software']):
        raise ValueError('the candidate does not attest both research modules')
    for name in RESEARCH_MODULES:
        if sha(candidate / name) != manifest['software'][name]:
            raise ValueError('candidate research module changed: ' + name)

    # The stage that produced this candidate, and the scope it was approved for.
    stage = json.loads(checkpoint.read_text(encoding='utf-8'))
    if (stage.get('passed') is not True or stage.get('release_id') != manifest['release_id']
            or stage.get('software_contract') != CONTRACT
            or stage.get('installed') is not False
            or stage.get('physical_headroom') != 'NOT_RUN'
            or stage.get('software') != manifest['software']):
        raise ValueError('passing development/G1 stage bound to this candidate required')

    receipt = json.loads(arm_path.read_text(encoding='utf-8'))
    for name in ('runner.py', 'chain-runner.py'):
        if receipt.get('runner_sha256', {}).get(name) != sha(arm_path.parent / name):
            raise ValueError('ARM qualification runner changed: ' + name)
    if (receipt.get('passed') is not True or receipt.get('release_id') != manifest['release_id']
            or receipt.get('software') != manifest['software']
            or receipt.get('native_cnn') is not True
            or receipt.get('parity_mismatches') != []
            or receipt.get('chain_parity_mismatches') != []
            or receipt.get('arm_exit_code') != 0 or receipt.get('chain_arm_exit_code') != 0
            or receipt.get('guard_exit_code') != 0
            or receipt.get('new_rf_frames') != 0
            or type(receipt.get('p95_ms')) not in (float, int)
            or not math.isfinite(receipt['p95_ms']) or not 0 < receipt['p95_ms'] <= 300
            or type(receipt.get('rss_kib')) is not int or not 0 < receipt['rss_kib'] <= 65536):
        raise ValueError('passing exact-candidate ARM qualification required')
    for name in ('arm.json', 'host.json', 'chain-arm.json', 'chain-host.json'):
        if sha(arm_path.parent / name) != receipt.get('evidence_sha256', {}).get(name):
            raise ValueError('ARM evidence hash mismatch: ' + name)
    arm = json.loads((arm_path.parent / 'arm.json').read_text(encoding='utf-8'))
    samples = arm.get('timings_ms') or []
    if (arm.get('release_id') != manifest['release_id'] or arm.get('software') != manifest['software']
            or arm.get('native_cnn') is not True or arm.get('iterations') != 100
            or len(samples) != 100
            or any(type(x) not in (float, int) or not math.isfinite(x) or x <= 0 for x in samples)):
        raise ValueError('ARM 100-iteration run incomplete')
    import numpy as np
    if abs(float(np.percentile(samples, 95)) - receipt['p95_ms']) > 1e-6:
        raise ValueError('ARM P95 does not match its own samples')
    # The saved-IQ chain: every stop class was exercised on ARM and matched host.
    chain_results = [json.loads((arm_path.parent / name).read_text(encoding='utf-8'))
                     for name in ('chain-arm.json', 'chain-host.json')]
    if not chain_evidence_ok(receipt, chain_results[0], chain_results[1]):
        raise ValueError('ARM saved-IQ chain evidence incomplete')

    # The rollback target: the release installed NOW, with a verified-safe post state
    # -- or, when the caller names the activation record, the reviewed activation
    # evidence chain of a release that was switched to current instead of installed
    # -- or, when the caller names the trial record, the reviewed non-emitting trial
    # deployment's acceptance chain of a release that was installed and accepted but
    # has no INSTALLED_VERIFIED receipt
    # -- or, when the caller names the recovery record, the read-back chain of a
    # release whose installation never reached a terminal state and which the
    # post-reboot recovery found still running and accepted.  The proofs are
    # alternatives, never a combination or a fallback, and none of them can be
    # reached by not naming it.
    named_proofs = [proof for proof in (parent_activation, parent_trial_acceptance,
                                        parent_recovery_acceptance)
                    if proof is not None]
    if len(named_proofs) > 1:
        raise ValueError('one rollback-parent proof at a time')
    if named_proofs:
        if parent_candidate is None:
            raise ValueError('the named rollback-parent proof needs its candidate directory')
        parent = R.verify_release(Path(parent_candidate), require_software=True)
        if parent_trial_acceptance is not None:
            # The evidence format is named, never guessed: '2026-10-17' is the frozen
            # route's package, '2026-10-18' is the v2 entry point above, and
            # '2026-10-18-baseline' is the baseline-deployment package; each refuses
            # the others' package instead of adapting to it.
            if parent_trial_acceptance_format is None:
                parent_trial_acceptance_format = TRIAL_FORMAT_2026_10_17
            if parent_trial_acceptance_format not in TRIAL_FORMATS:
                raise ValueError('unknown trial acceptance evidence format: %s'
                                 % (parent_trial_acceptance_format,))
            if parent_trial_acceptance_format == TRIAL_FORMAT_2026_10_18:
                proof = verify_installed_parent_via_trial_acceptance_evidence_v2(
                    parent, parent_trial_acceptance, Path(parent_candidate))
            elif parent_trial_acceptance_format == TRIAL_FORMAT_2026_10_18_BASELINE:
                proof = verify_installed_parent_via_trial_acceptance_evidence_baseline(
                    parent, parent_trial_acceptance, Path(parent_candidate))
            else:
                proof = verify_installed_parent_via_trial_acceptance_evidence(
                    parent, parent_trial_acceptance, Path(parent_candidate))
            parent_gate = dict(parent_release=proof['release_id'],
                               parent_trial_acceptance=proof['evidence_dir'],
                               parent_trial_acceptance_sha256=proof['evidence_sha256'],
                               parent_trial_parent=proof['trial_parent'])
        elif parent_activation is not None:
            proof = verify_installed_parent_via_activation_evidence(parent,
                                                                    parent_activation)
            parent_gate = dict(parent_release=proof['release_id'],
                               parent_activation=proof['evidence_dir'],
                               parent_activation_sha256=proof['evidence_sha256'])
        else:
            proof = verify_installed_parent_via_recovery_acceptance(
                parent, parent_recovery_acceptance, Path(parent_candidate))
            parent_gate = dict(parent_release=proof['release_id'],
                               parent_recovery_acceptance=proof['evidence_dir'],
                               parent_recovery_acceptance_sha256=proof['evidence_sha256'],
                               parent_recovery_boot_id=proof['recovery_boot_id'])
    else:
        parent_gate = dict(parent_release=verify_parent(parent_candidate, parent_install),
                           parent_install_sha256=sha(Path(parent_install)))
    return dict(release_id=manifest['release_id'], contract=CONTRACT,
                checkpoint_sha256=sha(checkpoint), arm_receipt_sha256=sha(arm_path),
                **parent_gate)


def _members(candidate):
    members = {}
    for item in sorted(candidate.iterdir()):
        if item.is_symlink() or not item.is_file():
            raise ValueError('nonregular candidate member')
        members['release/' + item.name] = item
    for name, relative in R.software_sources(CONTRACT).items():
        members[relative.replace('\\', '/')] = candidate / name
    return members


def prepare(candidate, arm, parent_candidate, parent_install, checkpoint, output,
            parent_activation=None, parent_trial_acceptance=None,
            parent_trial_acceptance_format=None, parent_recovery_acceptance=None):
    candidate, output = Path(candidate).resolve(), Path(output).resolve()
    gate = verify_gates(candidate, arm, parent_candidate, parent_install, checkpoint,
                        parent_activation, parent_trial_acceptance,
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
                  arm_path=str(Path(arm).resolve()),
                  parent_candidate=str(Path(parent_candidate).resolve()),
                  parent_install=(str(Path(parent_install).resolve())
                                  if parent_install else None),
                  parent_activation=(str(Path(parent_activation).resolve())
                                     if parent_activation else None),
                  parent_trial_acceptance=(str(Path(parent_trial_acceptance).resolve())
                                           if parent_trial_acceptance else None),
                  parent_recovery_acceptance=(
                      str(Path(parent_recovery_acceptance).resolve())
                      if parent_recovery_acceptance else None),
                  parent_trial_acceptance_format=(None if parent_trial_acceptance is None
                                                  else (parent_trial_acceptance_format
                                                        or TRIAL_FORMAT_2026_10_17)),
                  checkpoint_path=str(Path(checkpoint).resolve()),
                  payload_sha256={name: sha(output / name) for name in (
                      'candidate.tgz',) + tuple(TOOLS.values())})
    (output / 'bundle-receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def verify_bundle(directory):
    directory = Path(directory).resolve()
    receipt = json.loads((directory / 'bundle-receipt.json').read_text(encoding='utf-8'))
    gate = verify_gates(receipt['candidate'], receipt['arm_path'],
                        receipt['parent_candidate'], receipt['parent_install'],
                        receipt['checkpoint_path'], receipt.get('parent_activation'),
                        receipt.get('parent_trial_acceptance'),
                        receipt.get('parent_trial_acceptance_format'),
                        receipt.get('parent_recovery_acceptance'))
    if any(receipt.get(k) != v for k, v in gate.items()):
        raise ValueError('bundle qualification changed')
    expected = {name: dict(sha256=sha(path), bytes=path.stat().st_size)
                for name, path in _members(Path(receipt['candidate'])).items()}
    if receipt['members'] != expected:
        raise ValueError('bundle/candidate changed')
    if set(receipt['payload_sha256']) != {'candidate.tgz'} | set(TOOLS.values()):
        raise ValueError('missing installation payload')
    for name, digest in receipt['payload_sha256'].items():
        if sha(directory / name) != digest:
            raise ValueError('bundle payload changed')
    for source, target in TOOLS.items():
        if sha(ROOT / 'scripts' / source) != sha(directory / target):
            raise ValueError('installation tool changed')
    inspect_archive(directory / 'candidate.tgz', expected)
    return receipt


def remote_stage():
    """One fresh staging directory under the prefix the installer accepts."""
    return REMOTE_PREFIX + uuid.uuid4().hex


def storage_faults(text):
    """Kernel storage-fault lines, as a sorted unique list."""
    return sorted({line.strip() for line in (text or '').splitlines()
                   if any(key in line.lower() for key in KERNEL_FAULT_KEYS)})


class AttemptJournal:
    """Append-only, fsynced record of one activation attempt.

    The 2026-10-11 activation left no durable trace of its phases: after the
    client's 240 s deadline the only surviving facts were an UNKNOWN receipt and
    a `/tmp` staging directory that the reboot destroyed.  Every phase is now
    appended (and flushed) as it happens, so a dead session, a killed process or
    a rebooted board still leaves a parseable prefix -- and the prefix is what
    any later report has to read instead of reconstructing a story.
    """

    def __init__(self, path):
        self.path = Path(path)

    def repair(self):
        """Drop an incomplete trailing line left by a killed process.

        A partial write can only ever be the last thing on disk, so appending
        after it would otherwise plant an unparseable line in the middle of the
        journal and hide every later phase from `read_journal`.
        """
        if not self.path.exists():
            return False
        data = self.path.read_bytes()
        cut = data.rfind(b'\n')
        tail = data[cut + 1:]
        if not tail:
            return False
        with self.path.open('r+b') as handle:
            handle.truncate(cut + 1)
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def record(self, phase, **fields):
        self.repair()
        entry = dict(phase=phase, **fields)
        entry['utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        entry['seq'] = len(self.entries()) + 1
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, sort_keys=True, allow_nan=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def entries(self):
        return read_journal(self.path)


def read_journal(path):
    """Parse the valid prefix of a journal; a torn last line is dropped, not fatal."""
    path = Path(path)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            break
    return entries


SNAPSHOT = r'''
import json,subprocess
from pathlib import Path
def sh(cmd):
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        return dict(rc=r.returncode,out=r.stdout.rstrip('\n'),err=r.stderr.strip())
    except Exception as e: return dict(rc=None,out='',err='EXC:'+type(e).__name__)
d={}
for key,path in (('current_release','/home/xilinx/overlays/awg_merged/current'),
                 ('active_release','/run/pynq-status/active-release'),
                 ('boot_id','/proc/sys/kernel/random/boot_id')):
    try:
        value=Path(path).resolve(strict=True).name if key=='current_release' else Path(path).read_text().strip()
        d[key]=value
    except Exception as e: d[key]='ERR:'+type(e).__name__
try: d['uptime_seconds']=float(Path('/proc/uptime').read_text().split()[0])
except Exception as e: d['uptime_seconds']=None
d['units']={n:sh(['systemctl','show',n,'--property=ActiveState,SubState,MainPID,NRestarts'])
            for n in ('pynq-status','rf-daemon')}
d['commands']={'kernel_storage':sh(['dmesg','--color=never']),
               'mounts':sh(['cat','/proc/mounts'])}
for key in ('kernel_storage','mounts'): d[key]=d['commands'][key]['out']
print('DEPLOY_SNAPSHOT '+json.dumps(d))
'''


def validate_snapshot(data, rc):
    """A missing/failed read is unknown, never evidence of an error-free board."""
    if type(rc) is not int or rc != 0 or type(data) is not dict:
        raise ValueError('snapshot command unsuccessful')
    current = data.get('current_release')
    if (type(current) is not str or re.fullmatch('[0-9a-f]{32}', current) is None
            or data.get('active_release') != '/home/xilinx/overlays/awg_merged/releases/' + current
            or type(data.get('boot_id')) is not str
            or re.fullmatch('[0-9a-f-]{36}', data['boot_id']) is None
            or type(data.get('uptime_seconds')) not in (int, float)
            or not math.isfinite(data['uptime_seconds']) or data['uptime_seconds'] < 0):
        raise ValueError('snapshot release/boot identity incomplete')
    for key in ('kernel_storage', 'mounts'):
        command = (data.get('commands') or {}).get(key)
        if (type(command) is not dict or type(command.get('rc')) is not int
                or command['rc'] != 0 or command.get('err') != ''
                or type(data.get(key)) is not str or command.get('out') != data[key]):
            raise ValueError('snapshot subcommand failed: ' + key)
    roots = [line.split() for line in data['mounts'].splitlines()
             if len(line.split()) >= 4 and line.split()[1] == '/']
    if len(roots) != 1 or 'rw' not in roots[0][3].split(',') or 'ro' in roots[0][3].split(','):
        raise ValueError('snapshot root mount is not known read/write')
    units = data.get('units')
    if type(units) is not dict or set(units) != {'pynq-status', 'rf-daemon'}:
        raise ValueError('snapshot services incomplete')
    for name, unit in units.items():
        if (type(unit) is not dict or type(unit.get('rc')) is not int or unit['rc'] != 0
                or unit.get('err') != '' or type(unit.get('out')) is not str):
            raise ValueError('snapshot service read failed: ' + name)
        fields = dict(line.split('=', 1) for line in unit['out'].splitlines() if '=' in line)
        if (fields.get('ActiveState') != 'active' or fields.get('SubState') != 'running'
                or not fields.get('MainPID', '').isdigit() or int(fields['MainPID']) <= 0
                or not fields.get('NRestarts', '').isdigit()):
            raise ValueError('snapshot service not running: ' + name)
    return data


def board_snapshot(client, helper, output, journal, phase):
    """Read-only before/after snapshot: services, boot id, kernel storage lines."""
    log = output / ('snapshot-%s.log' % phase)
    data = {}
    try:
        rc = _run(client, helper, 'sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr '
                  'python3 -B -c ' + shlex.quote(SNAPSHOT), log, timeout=60)
        text = log.read_text(encoding='utf-8', errors='replace')
        if 'DEPLOY_SNAPSHOT ' in text:
            data = json.loads(text.split('DEPLOY_SNAPSHOT ', 1)[1].splitlines()[0])
        else:
            rc = rc if rc else 1
        validate_snapshot(data, rc)
    except Exception as exc:
        journal.record('snapshot_%s_failed' % phase, error_type=type(exc).__name__, error=str(exc))
        return {}, []
    faults = storage_faults(data.get('kernel_storage'))
    journal.record('snapshot_%s' % phase, exit_code=rc, boot_id=data.get('boot_id'),
                   uptime_seconds=data.get('uptime_seconds'),
                   current_release=data.get('current_release'),
                   active_release=data.get('active_release'),
                   kernel_storage_faults=faults, snapshot_log=log.name)
    return data, faults


def readback_state(client, helper, expected, output, journal, tag):
    """Read-only confirmation of what is actually installed, on its own connection.

    Uploads the reviewed state checker into an isolated `/tmp` directory, verifies
    its digest on the board before running it, and downloads the bounded state
    JSON.  It never loads an Overlay, never arms RF and never touches a release.
    """
    source = ROOT / 'scripts/rf_teaching_install_state.py'
    digest = sha(source)
    remote = '/tmp/rf-headroom-readback-' + uuid.uuid4().hex
    with client.open_sftp() as sftp:
        sftp.mkdir(remote)
        sftp.put(str(source), remote + '/rf_state_check.py')
    # The guard must be a RUNNABLE program: on 2026-10-13 a missing parenthesis
    # here made python3 exit 2 on the board for every read-back (finding N2).
    guard = 'python3 -B -c ' + shlex.quote(
        "import hashlib;assert hashlib.sha256(open('%s/rf_state_check.py','rb').read())"
        ".hexdigest()=='%s'" % (remote, digest))
    if _run(client, helper, guard, output / ('readback-guard-%s.log' % tag), timeout=20):
        raise ValueError('read-back state checker digest differs')
    rc = _run(client, helper, 'sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr '
              'python3 -B %s/rf_state_check.py %s %s/state.json' % (remote, expected, remote),
              output / ('readback-state-%s.log' % tag), timeout=60)
    local = output / ('readback-state-%s.json' % tag)
    with client.open_sftp() as sftp:
        info = sftp.lstat(remote + '/state.json')
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size < 1048576:
            raise ValueError('bounded regular read-back state required')
        sftp.get(remote + '/state.json', str(local))
    state = json.loads(local.read_text(encoding='utf-8'))
    verified = type(rc) is int and rc == 0 and verified_state(state, expected)
    journal.record('readback_%s' % tag, expected_release=expected, exit_code=rc,
                   verified=verified, release_id=state.get('release_id'),
                   safe=state.get('safe'), state_file=local.name,
                   checker_sha256=digest, remote=remote)
    return dict(expected_release=expected, verified=verified, exit_code=rc,
                release_id=state.get('release_id'), safe=state.get('safe'),
                services=state.get('services'), state_file=local.name,
                checker_sha256=digest, remote=remote, journal=journal.path.name)


def readback_job(client, helper, remote, attempt, payload, output, journal):
    """Observation timeout stops only this read process, never the system job."""
    job = '/var/lib/rf-headroom-deploy/' + attempt
    command = ('sudo -S python3 -B %s/stage/job.py observe --job %s --wait-seconds %d'
               % (job, job, INSTALL_TIMEOUT))
    log = output / 'job-observation.log'
    rc = _run(client, helper, command, log, timeout=INSTALL_TIMEOUT + 20)
    lines = [line[len('HEADROOM_JOB '):] for line in log.read_text('utf8').splitlines()
             if line.startswith('HEADROOM_JOB ')]
    if type(rc) is not int or rc != 0 or len(lines) != 1:
        raise ValueError('durable job observation unavailable')
    record = json.loads(lines[0])
    if (record.get('schema') != 'headroom-durable-job-v1'
            or record.get('attempt_id') != attempt
            or record.get('release_id') != payload['release_id']
            or record.get('parent_release') != payload['parent_release']
            or record.get('payload_sha256') != payload['payload_sha256']
            or record.get('retry_allowed') is not False
            or type(record.get('boot_id')) is not str
            or type(record.get('observed_boot_id')) is not str
            or record.get('phase') not in ('PREPARED', 'SUBMITTING', 'RUNNING', 'FINISHED',
                                          'INTERRUPTED', 'LAUNCH_UNKNOWN', 'EXECUTION_UNKNOWN')):
        raise ValueError('durable job binding differs')
    if record['phase'] == 'FINISHED' and (type(record.get('exit_code')) is not int
            or record.get('execution_result') != 'RETURNED'
            or record['boot_id'] != record['observed_boot_id']):
        raise ValueError('durable job terminal record incomplete')
    (output / 'remote-job.json').write_text(json.dumps(record, indent=2), encoding='utf8')
    journal.record('job_observed', remote_job=job, job_phase=record['phase'],
                   exit_code=record.get('exit_code'), boot_id=record['observed_boot_id'])
    return record


def install(bundle, output):
    """Install the verified bundle, then confirm what is installed read-only.

    One systemd job owns execution. SSH submits it once and closes; a fresh
    connection observes its persistent record and then reads the board safely.
    Neither a lost submission acknowledgment nor an observer deadline resubmits
    or kills that job. Current activation after reboot is a separate safe-state
    conclusion and leaves the original execution result UNKNOWN.
    """
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    payload = verify_bundle(bundle)
    output.mkdir(parents=True, exist_ok=False)
    remote = remote_stage()
    journal = AttemptJournal(output / JOURNAL_NAME)
    attempt_id = uuid.uuid4().hex
    result = dict(attempt_id=attempt_id,
                  utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  release_id=payload['release_id'], parent_release=payload['parent_release'],
                  remote=remote, arm_receipt_sha256=payload['arm_receipt_sha256'],
                  payload_sha256=payload['payload_sha256'], installed=False,
                  board_state='UNKNOWN', outcome=None, retry_allowed=False,
                  physical_headroom='NOT_RUN', human_acceptance='NOT_RUN',
                  journal=JOURNAL_NAME, install_timeout=False,
                  policy='dispatch once; no automatic retry; no arm/TX/second Overlay; '
                         'a transport timeout requires a read-only read-back, never a reinstall')
    journal.record('attempt_started', attempt_id=attempt_id, release_id=payload['release_id'],
                   parent_release=payload['parent_release'], remote=remote,
                   payload_sha256=payload['payload_sha256'], retry_allowed=False)
    client = None
    try:
        helper = load_helper()
        client = helper.connect()
        journal.record('execution_connected', role='install execution')
        pre, faults = board_snapshot(client, helper, output, journal, 'pre')
        result['pre_snapshot'] = pre
        result['kernel_storage_faults_before'] = faults
        if not pre or pre.get('current_release') != payload['parent_release']:
            # Fail closed: an unavailable pre-flight is not a healthy pre-flight.
            result.update(aborted=True, dispatched=False,
                          board_state='UNCHANGED_NOT_DISPATCHED',
                          abort_reason='pre-flight storage/release snapshot unavailable or parent differs')
            journal.record('aborted_before_dispatch', reason=result['abort_reason'], faults=[])
        elif faults:
            # A storage controller that is already faulting must not receive an
            # installation: stop before the board is written to at all.  The
            # 2026-10-12 incident showed an MMC interrupt timeout plus an SDHCI
            # ADMA error, and only a read-only observation is allowed after that.
            result.update(aborted=True, dispatched=False,
                          board_state='UNCHANGED_NOT_DISPATCHED',
                          abort_reason='kernel storage fault present before dispatch')
            journal.record('aborted_before_dispatch', reason=result['abort_reason'],
                           faults=faults)
        else:
            with client.open_sftp() as s:
                s.mkdir(remote)
                for name in payload['payload_sha256']:
                    s.put(str(bundle / name), remote + '/' + name)
            journal.record('staged', files=sorted(payload['payload_sha256']),
                           remote=remote)
            guard = 'python3 -B -c ' + shlex.quote(
                "import hashlib,pathlib; p=pathlib.Path(%r); expected=%r; "
                "assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==s "
                "for n,s in expected.items())" % (remote, payload['payload_sha256']))
            if _run(client, helper, guard, output / 'payload-guard.log', timeout=20):
                raise ValueError('uploaded installation payload changed')
            cmd = ('sudo -S python3 -B %s/job.py dispatch --stage %s --attempt %s '
                   '--release %s --parent %s' % (remote, remote, attempt_id,
                                                payload['release_id'], payload['parent_release']))
            result['dispatched'] = True
            journal.record('install_dispatched', release_id=payload['release_id'],
                           acknowledgment_timeout_seconds=60, dispatched_once=True,
                           independent_systemd_job=True)
            try:
                rc = _run(client, helper, cmd, output / 'dispatch.log', timeout=60)
                result['dispatch_exit_code'] = rc
                journal.record('dispatch_acknowledged', exit_code=rc)
            except Exception as exc:
                result.update(install_timeout=isinstance(exc, TimeoutError),
                              error_type=type(exc).__name__, error=str(exc))
                if not result['install_timeout']:
                    result['dispatch_error'] = type(exc).__name__
                journal.record('install_timeout' if result['install_timeout'] else 'install_dispatch_failed',
                               error_type=type(exc).__name__, error=str(exc),
                               dispatch_log_sha256=sha(output / 'dispatch.log')
                               if (output / 'dispatch.log').exists() else None)
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
        journal.record('execution_error', error_type=type(exc).__name__, error=str(exc))
    finally:
        if client is not None:
            client.close()
        journal.record('execution_session_closed')

    # Observation: its own session, read-only, and the only source of a verdict
    # when the execution session ended without one.
    if result.get('aborted'):
        result['outcome'] = (OUTCOME_ABORTED if result['kernel_storage_faults_before']
                             else OUTCOME_ABORTED_PREFLIGHT)
    elif not result['installed']:
        observer = None
        readback = {}
        try:
            helper = load_helper()
            observer = helper.connect()
            journal.record('observation_connected', role='read-only read-back',
                           separate_session=True)
            job = readback_job(observer, helper, remote, attempt_id, payload, output, journal)
            result['remote_job'] = job
            result['installation_execution'] = job.get('execution_result', 'UNKNOWN')
            # A live job may still be stopping/restarting owners. Do not compete
            # with it or label an unchanged parent a completed rollback.
            if job['phase'] not in ('FINISHED', 'INTERRUPTED'):
                raise ValueError('remote job is still pending; observe this same attempt later')
            new = readback_state(observer, helper, payload['release_id'], output, journal, 'new')
            readback['new_release'] = {k: v for k, v in new.items() if k != 'services'}
            if new['verified']:
                result.update(installed=True, board_state='VERIFIED_SAFE_NEW_RELEASE')
            else:
                old = readback_state(observer, helper, payload['parent_release'], output, journal, 'parent')
                readback['parent_release'] = {k: v for k, v in old.items() if k != 'services'}
                result['board_state'] = 'VERIFIED_SAFE_ROLLBACK' if old['verified'] else 'UNKNOWN'
            post_snapshot, faults = board_snapshot(observer, helper, output, journal, 'post')
            result['post_snapshot'] = post_snapshot
            result['kernel_storage_faults_after'] = faults
            if not post_snapshot or faults or post_snapshot['boot_id'] != job['observed_boot_id']:
                result.update(installed=False, board_state='UNKNOWN')
                raise ValueError('post-flight health/boot identity unavailable or storage fault present')
            if job['phase'] != 'FINISHED' and result['board_state'] != 'VERIFIED_SAFE_NEW_RELEASE':
                result['board_state'] = 'UNKNOWN'
                raise ValueError('interrupted/unknown execution cannot imply completed rollback')
            result['outcome'] = (OUTCOME_INSTALLED if result['installed'] else
                                 OUTCOME_ROLLBACK if result['board_state'] == 'VERIFIED_SAFE_ROLLBACK'
                                 else OUTCOME_UNEXPECTED)
        except Exception as exc:
            result.update(readback_error_type=type(exc).__name__, readback_error=str(exc))
            journal.record('readback_failed', error_type=type(exc).__name__, error=str(exc))
            result['outcome'] = OUTCOME_INCOMPLETE
        finally:
            if observer is not None:
                observer.close()
            journal.record('observation_session_closed')
            result['readback'] = readback
    else:
        result['outcome'] = OUTCOME_INSTALLED

    result['retry_allowed'] = False
    if result.get('aborted'):
        result['next_action'] = ('resolve storage health with a read-only check before any '
                                 'further deployment')
    elif result['outcome'] == OUTCOME_INCOMPLETE:
        result['next_action'] = 'read-only read-back required before any further deployment'
    else:
        result['next_action'] = ('none from this attempt; a new attempt needs a new bundle and '
                                 'a fresh gate')
    journal.record('attempt_finished', outcome=result['outcome'], installed=result['installed'],
                   board_state=result['board_state'], retry_allowed=False)
    result['phases'] = [entry['phase'] for entry in journal.entries()]
    result['evidence_sha256'] = {p.name: sha(p) for p in output.iterdir() if p.is_file()}
    (output / 'install-receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    for name in ('candidate', 'arm', 'parent-candidate', 'checkpoint', 'output'):
        prep.add_argument('--' + name, required=True, type=Path)
    # Exactly one rollback-parent proof: the installer receipt route, or one of the
    # explicitly named evidence routes.  There is no implicit fallback between them.
    prep.add_argument('--parent-install', type=Path)
    prep.add_argument('--parent-activation', type=Path)
    prep.add_argument('--parent-trial-acceptance', type=Path)
    # The post-reboot recovery evidence of a release whose installation transaction
    # never reached a terminal state.  Named explicitly; never detected, never a
    # fallback for a missing receipt.
    prep.add_argument('--parent-recovery-acceptance', type=Path)
    # The evidence layout has to be named.  '2026-10-17' is the frozen fifth route's
    # package, '2026-10-18' is the v2 package; there is no detection and no fallback,
    # and the flag is refused without --parent-trial-acceptance.
    prep.add_argument('--parent-trial-acceptance-format', choices=TRIAL_FORMATS)
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
        if len(proofs) != 1:
            p.error('exactly one rollback-parent proof is required: --parent-install, '
                    '--parent-activation, --parent-trial-acceptance or '
                    '--parent-recovery-acceptance')
        result = prepare(a.candidate, a.arm, a.parent_candidate, a.parent_install,
                         a.checkpoint, a.output, a.parent_activation,
                         a.parent_trial_acceptance, a.parent_trial_acceptance_format,
                         a.parent_recovery_acceptance)
    else:
        result = install(a.bundle, a.output)
    print(json.dumps({k: v for k, v in result.items() if k != 'members'}, indent=2))
    raise SystemExit(0 if (a.action == 'prepare' or result['installed']) else 1)
