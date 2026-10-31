
"""The seventh rollback-parent route: the 2026-10-18 baseline-deployment package.

03b9b883 is the release the 2026-10-18 baseline trial installed and accepted on
the board -- the reviewed installer, one bounded on-board acceptance and one
independent read-only read-back -- so it has no INSTALLED_VERIFIED receipt and the
trial documents must not be renamed into one.  The package is a third layout
(bundle/ trial/ readback/ preflight/, readings tagged post-01/post-02, no host
receipt, no read-back summary and no evidence index), so the route is a named
entry point that rebuilds the chain from the real artefacts only.

These tests prove the real package is accepted, that the format has to be named
and the old routes refuse it, that both deployment gates and the prepare CLI can
use it, that it cannot stand in for an INSTALLED_VERIFIED receipt, and that every
step -- identity, digests, the unique terminal marker, failure/rollback markers,
the independent read-back and its raw logs, the release listing, the downloaded
bytes, the staged payload, the bundle, the probe and the candidate truth -- is
load-bearing, including against a self-consistent rewrite of every text file.
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
RELEASE = '03b9b883a61f29605be2c1f3817024d0'
CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-baseline-install-2026-10-18/'
             'candidate/releases' / RELEASE)
EVIDENCE = ROOT / 'evidence/rf-teaching/s2-46db-baseline-deploy-2026-10-18'
TRIAL_PARENT = '1c0894bfe7751be2e1afa4e8bac564b8'
FORMAT = D.TRIAL_FORMAT_2026_10_18_BASELINE
ACCEPTED = 'TRIAL_ACCEPTED release=' + RELEASE
HEX = 'f' * 64
OTHER = 'ad05576aff6b9bf2b6b495347a6269ec'
ENV_CLOSURE = ROOT / 'evidence/rf-teaching/s1/closure-2026-10-04/receipt.json'
ENV_ARM = (ROOT / 'evidence/rf-teaching/s2-46db-arm-check-baseline-2026-10-18/'
           'arm-run/receipt.json')
HEADROOM = 'b82b141e355b4cb1833969383a409c79'
HEADROOM_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                      'candidate-final/releases' / HEADROOM)
HEADROOM_ARM = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                'arm-runtime/receipt.json')
HEADROOM_CHECKPOINT = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                       'development-checkpoint.json')


@pytest.fixture(scope='module')
def candidate(tmp_path_factory):
    """The real release files, byte for byte, without the host's __pycache__.

    The directory in evidence/ carries a __pycache__ left by an earlier host-side
    import.  It is not a release member (the release listing has 51 files and no
    directory) and prepare's existing nonregular-member guard refuses it; the
    route must not be weakened to accept a tree prepare refuses.  The copy is
    verified file by file against the evidence directory first.
    """
    root = tmp_path_factory.mktemp('candidate')
    directory = root / 'releases' / RELEASE
    directory.parent.mkdir()
    shutil.copytree(str(CANDIDATE), str(directory),
                    ignore=shutil.ignore_patterns('__pycache__'))
    original = {path.name: D.sha(path) for path in CANDIDATE.iterdir() if path.is_file()}
    copied = {path.name: D.sha(path) for path in directory.iterdir() if path.is_file()}
    assert copied == original
    return directory


@pytest.fixture
def evidence(tmp_path):
    directory = tmp_path / 'evidence'
    shutil.copytree(str(EVIDENCE), str(directory))
    return directory


def parent_of(candidate_dir):
    return R.verify_release(Path(candidate_dir), require_software=True)


def prove_in(directory, candidate_dir):
    return D.verify_installed_parent_via_trial_acceptance_evidence_baseline(
        parent_of(candidate_dir), directory, candidate_dir)


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def mutate_json(directory, relative, mutate):
    path = Path(directory) / relative
    before = path.read_bytes()
    document = _load(path)
    mutate(document)
    _write(path, document)
    assert path.read_bytes() != before, 'the mutation changed nothing in ' + relative
    return path


def mutate_bytes(directory, relative, old, new):
    path = Path(directory) / relative
    before = path.read_bytes()
    after = before.replace(old.encode('utf-8'), new.encode('utf-8'), 1)
    assert after != before, 'the mutation changed nothing in ' + relative
    path.write_bytes(after)
    return path


def mutate_marker(directory, relative, marker, mutate):
    path = Path(directory) / relative
    lines = path.read_text(encoding='utf-8').splitlines()
    for index, line in enumerate(lines):
        if line.startswith(marker + ' '):
            document = json.loads(line[len(marker) + 1:])
            mutate(document)
            lines[index] = marker + ' ' + json.dumps(document)
            path.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
            return path
    raise AssertionError('no %s record in %s' % (marker, relative))


def _set(path, value):
    def mutate(document):
        target = document
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


def refusal(directory, candidate_dir, message):
    with pytest.raises(ValueError, match=message):
        prove_in(directory, candidate_dir)


# --------------------------------------------------------------------------- #
# The real package, both gates, the CLI, and the receipt boundary.
# --------------------------------------------------------------------------- #

def test_the_real_baseline_package_is_accepted(candidate):
    proof = prove_in(EVIDENCE, candidate)
    assert proof['release_id'] == RELEASE
    assert proof['source'] == D.TRIAL_BASELINE_SOURCE
    assert proof['software_contract'] == 'cnn-bpsk-environment-v1'
    assert proof['software_modules'] == 44
    assert proof['trial_parent'] == TRIAL_PARENT
    assert proof['trial_format'] == FORMAT
    assert set(proof['evidence_sha256']) == set(D.TRIAL_BASELINE_FILES)
    assert proof['evidence_sha256']['readback/post-02-preflight.json'] == D.sha(
        EVIDENCE / 'readback/post-02-preflight.json')
    assert proof['evidence_sha256']['logs/dispatch-journal.log'] == D.sha(
        EVIDENCE / 'logs/dispatch-journal.log')


def test_the_proof_is_not_and_cannot_become_an_installed_verified_receipt(candidate,
                                                                         tmp_path):
    proof = prove_in(EVIDENCE, candidate)
    assert 'INSTALLED_VERIFIED' not in json.dumps(proof)
    bundle = _load(EVIDENCE / 'bundle/bundle-receipt.json')
    assert bundle['installed'] is False
    with pytest.raises(ValueError, match='installed rollback parent installation '
                                        'unverified'):
        D.verify_installed_parent(parent_of(candidate), EVIDENCE, bundle)
    directory = Path(tmp_path) / 'evidence'
    shutil.copytree(str(EVIDENCE), str(directory))
    def claim(document):
        document.update(installed=True, outcome='INSTALLED_VERIFIED',
                        board_state='VERIFIED_SAFE_NEW_RELEASE')
    mutate_json(directory, 'trial/trial.json', claim)
    (directory / 'receipt.json').write_text(json.dumps(dict(
        installed=True, release_id=RELEASE, board_state='VERIFIED_SAFE_NEW_RELEASE',
        outcome='INSTALLED_VERIFIED')), encoding='utf-8')
    forged = _load(directory / 'receipt.json')
    with pytest.raises(ValueError, match='installed rollback parent installation '
                                        'unverified'):
        D.verify_installed_parent(parent_of(candidate), directory, forged)
    proof = prove_in(directory, candidate)
    assert proof['source'] == D.TRIAL_BASELINE_SOURCE
    assert 'INSTALLED_VERIFIED' not in json.dumps(proof)


def test_the_format_is_named_and_the_older_routes_refuse_this_package(candidate):
    assert FORMAT in D.TRIAL_FORMATS
    assert FORMAT in E.TRIAL_FORMATS
    with pytest.raises(ValueError, match='trial acceptance evidence trial.json missing'):
        D.verify_installed_parent_via_trial_acceptance_evidence(
            parent_of(candidate), EVIDENCE, candidate)
    with pytest.raises(ValueError, match='trial acceptance evidence v2'):
        D.verify_installed_parent_via_trial_acceptance_evidence_v2(
            parent_of(candidate), EVIDENCE, candidate)
    with pytest.raises(ValueError, match='requires the parent candidate'):
        D.verify_installed_parent_via_trial_acceptance_evidence_baseline(
            parent_of(candidate), EVIDENCE)


def test_the_environment_gate_and_prepare_accept_the_route(candidate, tmp_path):
    gate = E.verify_gates(candidate, ENV_CLOSURE, ENV_ARM, candidate, None, None, None,
                          EVIDENCE, FORMAT)
    assert gate['release_id'] == RELEASE
    assert gate['parent_release'] == RELEASE
    assert gate['parent_trial_parent'] == TRIAL_PARENT
    assert set(gate['parent_trial_acceptance_sha256']) == set(D.TRIAL_BASELINE_FILES)
    assert 'parent_install_sha256' not in gate
    output = Path(tmp_path) / 'bundle'
    result = E.prepare(candidate, ENV_CLOSURE, ENV_ARM, output,
                       parent_candidate=candidate,
                       parent_trial_acceptance=EVIDENCE,
                       parent_trial_acceptance_format=FORMAT)
    assert result['parent_release'] == RELEASE
    assert result['parent_trial_parent'] == TRIAL_PARENT
    assert result['parent_trial_acceptance_format'] == FORMAT
    assert result['installed'] is False
    assert len(result['members']) == 95
    receipt = E.verify_bundle(output)
    assert receipt['parent_release'] == RELEASE
    assert receipt['parent_trial_parent'] == TRIAL_PARENT


def test_the_prepare_cli_uses_the_named_route(candidate, tmp_path):
    output = Path(tmp_path) / 'bundle'
    command = [sys.executable, '-B', str(ROOT / 'scripts/rf_environment_deploy.py'),
               'prepare', '--candidate', str(candidate), '--closure', str(ENV_CLOSURE),
               '--arm', str(ENV_ARM), '--parent-candidate', str(candidate),
               '--parent-trial-acceptance', str(EVIDENCE),
               '--parent-trial-acceptance-format', FORMAT,
               '--output', str(output)]
    result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['parent_release'] == RELEASE
    assert payload['parent_trial_parent'] == TRIAL_PARENT
    assert payload['parent_trial_acceptance_format'] == FORMAT


def test_the_headroom_gate_also_binds_the_baseline_parent(candidate):
    gate = D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, candidate, None,
                          HEADROOM_CHECKPOINT, None, EVIDENCE, FORMAT)
    assert gate['release_id'] == HEADROOM
    assert gate['parent_release'] == RELEASE
    assert gate['parent_trial_parent'] == TRIAL_PARENT


# --------------------------------------------------------------------------- #
# Negative battery 1: identity and digests.
# --------------------------------------------------------------------------- #

def test_identity_mismatch_is_refused(evidence, candidate):
    mutate_json(evidence, 'readback/board-download/acceptance-report.json',
                _set(('checks', 0, 'detail', 'release_id'), OTHER))
    refusal(evidence, candidate, 'acceptance report disagrees with the release')


def test_domain_module_digest_mismatch_is_refused(evidence, candidate):
    mutate_json(evidence, 'readback/board-download/acceptance-report.json',
                _set(('checks', 1, 'detail', 'rf_environment', 'sha256'), HEX))
    refusal(evidence, candidate, 'domain module is not the release module')


def test_wrong_candidate_is_refused(evidence, candidate):
    parent = dict(parent_of(candidate), release_id=OTHER)
    with pytest.raises(ValueError, match='baseline trial acceptance evidence'):
        D.verify_installed_parent_via_trial_acceptance_evidence_baseline(
            parent, EVIDENCE, candidate)


# --------------------------------------------------------------------------- #
# Negative battery 2: terminal marker, failures and rollback.
# --------------------------------------------------------------------------- #

def test_missing_trial_accepted_is_refused(evidence, candidate):
    mutate_bytes(evidence, 'logs/dispatch-journal.log',
                 ACCEPTED, 'TRIAL_ACCEPTED release=' + OTHER)
    refusal(evidence, candidate, 'terminal marker missing, repeated or not this release')


def test_duplicate_trial_accepted_is_refused(evidence, candidate):
    mutate_bytes(evidence, 'logs/dispatch-journal.log',
                 ACCEPTED, ACCEPTED + chr(10) + ACCEPTED)
    refusal(evidence, candidate, 'terminal marker missing, repeated or not this release')


def test_failed_or_rolled_back_marker_is_refused(evidence, candidate):
    path = evidence / 'logs/dispatch-journal.log'
    path.write_bytes(path.read_bytes() + b'TRIAL_ROLLED_BACK' + bytes([10]))
    refusal(evidence, candidate, 'records a failed or rolled-back trial')


def test_rollback_state_artifact_is_refused(evidence, candidate):
    mutate_json(evidence,
                'readback/board-download/board-download-manifest.json',
                _set(('files', 'rollback-state.json'), dict(sha256=HEX, bytes=1)))
    refusal(evidence, candidate, 'records a rollback state')


# --------------------------------------------------------------------------- #
# Negative battery 3: the independent read-back and the release listing.
# --------------------------------------------------------------------------- #

def test_missing_independent_readback_is_refused(evidence, candidate):
    (evidence / 'readback/post-02-preflight.json').unlink()
    refusal(evidence, candidate, 'readback/post-02-preflight.json missing')


def test_missing_readback_raw_log_is_refused(evidence, candidate):
    (evidence / 'readback/post-02-04-dmesg.log').unlink()
    refusal(evidence, candidate, 'readback/post-02-04-dmesg.log missing')


def test_readback_report_must_equal_its_raw_log(evidence, candidate):
    mutate_bytes(evidence, 'readback/post-02-01-snapshot.log',
                 '9672af66-21ff-4ceb-8f91-f112ad444651',
                 '00000000-0000-0000-0000-000000000000')
    refusal(evidence, candidate, 'report disagrees with the raw board reading')


def test_release_listing_must_be_the_candidate_tree(evidence, candidate):
    document = _load(evidence / 'readback/post-02-preflight.json')
    text = document['release_files'].replace(
        '63c37ecc687987e01182fa4b5e7385544649fe0909573f7420d74e469f01f5c2', HEX, 1)
    assert text != document['release_files']
    document['release_files'] = text
    _write(evidence / 'readback/post-02-preflight.json', document)
    (evidence / 'readback/post-02-05-release-files.log').write_text(
        text + chr(10), encoding='utf-8')
    refusal(evidence, candidate, 'installed release tree is not the candidate tree')


def test_release_listing_record_must_be_all_equal(evidence, candidate):
    mutate_json(evidence, 'readback/post-02-preflight.json',
                _set(('items', 'B13_release_listing', 'differing'), ['awg_ctl.py']))
    refusal(evidence, candidate, 'release listing record unverified')


# --------------------------------------------------------------------------- #
# Negative battery 4: the staged, fetched and prepared bytes.
# --------------------------------------------------------------------------- #

def test_staged_payload_must_match_its_map(evidence, candidate):
    path = evidence / 'trial/stage/install.sh'
    data = path.read_bytes()
    path.write_bytes(data.replace(b'#!/bin/sh', b'#!/bin/bash', 1))
    refusal(evidence, candidate, 'stage does not match its local map')


def test_fetch_payload_must_be_the_downloaded_bytes(evidence, candidate):
    def flip(document):
        payload = document['acceptance-report.json']
        document['acceptance-report.json'] = ('A' if payload[0] != 'A' else 'B') + payload[1:]
    mutate_marker(evidence, 'logs/fetch.log', 'FETCH', flip)
    refusal(evidence, candidate, 'fetch payload (differs|unreadable)')


def test_bundle_receipt_must_describe_the_candidate(evidence, candidate):
    mutate_json(evidence, 'bundle/bundle-receipt.json',
                _set(('members', 'release/awg_ctl.py', 'sha256'), HEX))
    refusal(evidence, candidate, 'bundle receipt unverified')


def test_readback_probe_must_be_bound_to_the_candidate(evidence, candidate):
    mutate_json(evidence, 'readback/make_post_probe.json',
                _set(('baseline_file_sha256',), HEX))
    refusal(evidence, candidate, 'read-back probe unverified')


# --------------------------------------------------------------------------- #
# A self-consistent rewrite of every text file still refuses.
# --------------------------------------------------------------------------- #

def test_a_self_consistent_rewrite_is_refused(evidence, candidate):
    rewritten = 0
    for path in sorted(evidence.rglob('*')):
        if not path.is_file() or path.suffix in ('.tgz', '.iq', '.so', '.bit', '.hwh'):
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        if RELEASE in text:
            path.write_text(text.replace(RELEASE, OTHER), encoding='utf-8')
            rewritten += 1
    assert rewritten > 10
    refusal(evidence, candidate, 'baseline trial acceptance evidence')
