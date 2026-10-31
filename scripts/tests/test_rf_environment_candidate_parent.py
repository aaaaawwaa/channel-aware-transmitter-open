"""The candidate builder packages only on top of an EXPLICITLY accepted parent set.

2026-10-19 review, blocking item 1: the board's current release
`03b9b883a61f29605be2c1f3817024d0` (cnn-bpsk-environment-v1) must be a usable
parent, otherwise the only reviewed plan cannot produce a candidate at all.

The parent set is a NAMED list with a reason per entry -- never a wildcard and
never an id-only shortcut -- so this file pins both halves of that contract:

  * the installed environment release IS accepted (and a same-contract parent's
    carried software is reused byte for byte, not re-digested from this working
    tree, because that tree currently holds an n=1 bootstrap baseline while the
    board runs the audited n=20 one);
  * a release that verifies but is NOT on the list is still refused by name;
  * a parent directory whose evidence does not verify is still refused by the
    release verifier before any acceptance is considered.

No board I/O: every parent used here is a host-side release directory.
"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts import merged_release as R
from scripts import rf_environment_candidate as C

ROOT = Path(__file__).resolve().parents[2]
ENV = (ROOT / 'evidence/rf-teaching/s2-46db-baseline-install-2026-10-18'
       / 'candidate/releases/03b9b883a61f29605be2c1f3817024d0')
HEADROOM = (ROOT / 'evidence/rf-teaching/s2/headroom-contract-2026-10-11'
            / 'candidate-budget/releases/b67a8aaab0c5904822580c0647b96fd0')
BOARD_APP = ROOT / 'rf_overlay/sw/board_app'
GAIN_REG_SOURCE = 'rf_environment_baseline.json'


def _clone_parent(tmp_path, name):
    destination = tmp_path / name
    shutil.copytree(ENV, destination)
    return destination


def _reidentify(directory, build_id):
    """Give a copied release a new, SELF-CONSISTENT identity.

    It must still verify: the point of the unknown-parent case is that the
    builder refuses a release for not being on the named list, not because the
    release was malformed.
    """
    manifest_path = directory / 'merged_release_manifest.json'
    receipt_path = directory / 'merged_build_receipt.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    for document in (manifest, receipt):
        document['build_id'] = build_id
    release_id = R.release_identity(
        build_id, manifest['bit_sha256'], manifest['hwh_sha256'], manifest['daemon_version'],
        manifest['protocol_version'], manifest['daemon_sha256'], manifest['software'],
        software_contract=manifest.get('software_contract'),
        model_artifact=manifest.get('model_artifact'))
    manifest['release_id'] = release_id
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    assert R.verify_release(directory, require_software=True)['release_id'] == release_id
    return release_id


@pytest.fixture
def synthetic_observer_build(tmp_path):
    """A self-consistent ARM build RECEIPT over the current board_app sources.

    This is NOT a build: the ELF is a 32-bit little-endian ARM header only, and
    the receipt is computed here so the artifact/source gate can be exercised
    offline.  It exists to prove the plan's command SHAPE (parent + daemon +
    daemon-build) reaches the packaging step; it is not qualification evidence.
    """
    from scripts.rf_build_daemon import SOURCE_GLOBS, SUBDIRS
    binary = tmp_path / 'rf_daemon'
    elf = bytearray(64)
    elf[:6] = b'\x7fELF\x01\x01'
    elf[18:20] = (40).to_bytes(2, 'little')
    binary.write_bytes(elf)
    paths = {BOARD_APP / 'Makefile'}
    for pattern in SOURCE_GLOBS:
        paths.update(BOARD_APP.glob(pattern))
    for sub in SUBDIRS:
        paths.update(p for p in (BOARD_APP / sub).iterdir()
                     if p.is_file() and p.suffix in ('.c', '.h', '.py', ''))
    receipt = dict(architecture='armv7l', hardware_touched=False,
                   sha256=hashlib.sha256(bytes(elf)).hexdigest(),
                   source_sha256={p.relative_to(BOARD_APP).as_posix(): R.sha256_file(p)
                                  for p in paths},
                   targets=['all', 'host-test', 'host-test-core', 'host-test-iq',
                            'host-test-protocol', 'vendor-integrity'])
    evidence = binary.with_suffix('.build.json')
    evidence.write_text(json.dumps(receipt), encoding='utf-8')
    binary.with_suffix('.build.log').write_text(
        'BUILD_RC=0\nvendor integrity: 10 files passed\n', encoding='utf-8')
    return binary, evidence


def test_the_installed_environment_release_is_an_accepted_parent(tmp_path):
    parent = R.verify_release(ENV, require_software=True)
    result = C.build_candidate(ENV, tmp_path / 'candidate', carry_parent_software=True)
    manifest = R.verify_release(Path(result['candidate']), require_software=True)
    assert result['parent_release'] == parent['release_id']
    assert result['parent_role'] == 'installed_environment'
    assert manifest['software_contract'] == parent['software_contract']
    assert manifest[R.MODEL_KEY] == parent[R.MODEL_KEY]
    assert manifest['bit_sha256'] == parent['bit_sha256']
    assert manifest['hwh_sha256'] == parent['hwh_sha256']


def test_the_parents_carried_software_is_reused_byte_for_byte(tmp_path):
    """The tree holds an n=1 bootstrap baseline; the board runs the audited n=20 one.

    Re-digesting the tree would silently replace the carried baseline (and its
    release identity) -- exactly the change the reviewed plan says must NOT
    happen -- so a parent on the installed-environment route has to be able to
    carry its own attested bytes.
    """
    parent = R.verify_release(ENV, require_software=True)
    result = C.build_candidate(ENV, tmp_path / 'candidate', carry_parent_software=True)
    candidate = Path(result['candidate'])
    manifest = R.verify_release(candidate, require_software=True)
    assert manifest['software'] == parent['software']
    assert result['carried_software_from_parent'] == [GAIN_REG_SOURCE]
    for name in sorted(parent['software']):
        assert (candidate / name).read_bytes() == (ENV / name).read_bytes(), name
    assert R.sha256_file(candidate / GAIN_REG_SOURCE) == parent['software'][GAIN_REG_SOURCE]


def test_a_carried_software_difference_is_refused_unless_it_is_declared(tmp_path):
    with pytest.raises(ValueError, match='carry-parent-software'):
        C.build_candidate(ENV, tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()


def test_a_verified_release_outside_the_named_parent_set_is_refused(tmp_path):
    """A release the verifier ACCEPTS is still not a parent unless it is on the list.

    The refusal has to name the whole accepted set, so "which parents are allowed"
    is answerable from the error alone -- that is also what makes this criterion
    fail against the pre-fix code (whose message named neither 03b9b883... nor any
    other member) instead of passing for the wrong reason."""
    unknown = _clone_parent(tmp_path, 'unknown-parent')
    release_id = _reidentify(unknown, 'cnn-bpsk-environment-v1-unknown-parent-probe')
    assert release_id != '03b9b883a61f29605be2c1f3817024d0'
    with pytest.raises(ValueError, match='parent') as refused:
        C.build_candidate(unknown, tmp_path / 'refused')
    message = str(refused.value)
    for named in ('03b9b883a61f29605be2c1f3817024d0', 'c3bf915c544563324778d1c879b38e3a',
                  'b67a8aaab0c5904822580c0647b96fd0'):
        assert named in message
    assert not (tmp_path / 'refused').exists()


def test_a_parent_without_its_build_evidence_is_refused(tmp_path):
    broken = _clone_parent(tmp_path, 'no-receipt-parent')
    (broken / 'merged_build_receipt.json').unlink()
    with pytest.raises(ValueError, match='receipt'):
        C.build_candidate(broken, tmp_path / 'refused', carry_parent_software=True)
    assert not (tmp_path / 'refused').exists()


def test_a_parent_whose_attested_bytes_do_not_match_is_refused(tmp_path):
    broken = _clone_parent(tmp_path, 'tampered-parent')
    (broken / GAIN_REG_SOURCE).write_bytes(b'{"schema_version": 1}\n')
    with pytest.raises(ValueError, match='hash'):
        C.build_candidate(broken, tmp_path / 'refused', carry_parent_software=True)
    assert not (tmp_path / 'refused').exists()


def test_the_installed_environment_parent_cannot_build_a_headroom_revision(tmp_path):
    with pytest.raises(ValueError, match='parent'):
        C.build_candidate(ENV, tmp_path / 'refused', contract='cnn-bpsk-headroom-v1',
                          carry_parent_software=True)
    assert not (tmp_path / 'refused').exists()


def test_the_plans_daemon_rebuild_command_shape_reaches_packaging(tmp_path, synthetic_observer_build):
    binary, evidence = synthetic_observer_build
    parent = R.verify_release(ENV, require_software=True)
    result = C.build_candidate(ENV, tmp_path / 'candidate', binary, evidence,
                               contract='cnn-bpsk-environment-v1', carry_parent_software=True)
    manifest = R.verify_release(Path(result['candidate']), require_software=True)
    assert result['daemon_rebuilt'] is True
    assert result['native_artifact_reused'] is False
    assert manifest['daemon_sha256'] == R.sha256_file(binary)
    assert manifest['daemon_sha256'] != parent['daemon_sha256']
    assert manifest['software'] == parent['software']
    assert manifest['software_contract'] == 'cnn-bpsk-environment-v1'
