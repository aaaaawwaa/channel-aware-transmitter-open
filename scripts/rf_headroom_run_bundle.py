"""Host-side build + re-verification of the board HEADROOM run bundle.

spec (the judgement this file implements, and does not change):
`docs/superpowers/specs/2026-10-13-headroom-board-run-package-design.md`
sections 1.3 (member list), 1.4 (bundle receipt) and 4 (upload digest guard).

This file is HOST-side: it never reaches the board.  The board entry re-verifies
the same members with the same rules, and those rules live in ONE place --
`scripts/rf_headroom_research_run.py`, which is itself a bundle member and which
this packer loads BY PATH (never by module name).  `verify_run_bundle` therefore
delegates to the runner's `verify_run_bundle_members`, so a host check and a
board check can never drift apart.

Fix 2026-10-14 (spec 12.2): the repository has no `scripts/__init__.py`
(`scripts/` is a PEP 420 namespace package), so the packaging path GENERATES
that member instead of assuming it exists.
"""
import importlib.util
import json
from pathlib import Path
import re
import sys


def _load_runner(source_root):
    """Load the run bundle's runner BY PATH, from the tree it is packaged from."""
    path = Path(source_root) / 'scripts' / 'rf_headroom_research_run.py'
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError('the source tree has no runner: ' + str(path))
    spec = importlib.util.spec_from_file_location('run_bundle_runner_rules', str(path))
    if spec is None or spec.loader is None:
        raise ValueError('cannot load the runner rules from ' + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


_RULES = _load_runner(Path(__file__).resolve().parents[1])

# spec 1.3: the member list, the generated package marker and the refusal class
# all come from the runner, so both sides verify exactly the same thing.
BUNDLE_MEMBERS = tuple(_RULES.BUNDLE_MEMBERS)
BundleRefused = _RULES.BundleRefused
BUNDLE_RECEIPT_NAME = _RULES.BUNDLE_RECEIPT_NAME
GENERATED_PACKAGE_INIT = _RULES.GENERATED_PACKAGE_INIT
PACKAGE_INIT_MEMBER = 'scripts/__init__.py'
assert PACKAGE_INIT_MEMBER in BUNDLE_MEMBERS, (
    'the generated package marker must be a declared member')
_COMMIT = re.compile('[0-9a-f]{40}')


def sha256_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _sha256(path):
    return sha256_bytes(Path(path).read_bytes())


def build_run_bundle(source_root, destination, *, source_commit, builder=None,
                     runner_version=None):
    """Copy the declared members into `destination` and write `bundle-receipt.json`.

    The receipt records, for every member, the SHA-256 and the byte count an
    upload re-verifies (spec 4), plus the source commit, the builder and the
    packaged runner version (spec 1.4/5).  Returns the receipt it wrote.
    """
    source_root = Path(source_root)
    destination = Path(destination)
    if type(source_commit) is not str or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError('source_commit must be a 40-character lowercase hex commit id')
    if builder is not None and (type(builder) is not str or not builder.strip()):
        raise ValueError('builder must be a non-empty string when given')
    destination.mkdir(parents=True, exist_ok=True)
    for member in BUNDLE_MEMBERS:
        (destination / member).parent.mkdir(parents=True, exist_ok=True)
        if member == PACKAGE_INIT_MEMBER:
            # spec 1.3/12.2: the repository has no such file; GENERATE it, and
            # count it in the receipt like every other member.
            (destination / member).write_bytes(GENERATED_PACKAGE_INIT.encode('utf-8'))
            continue
        source = source_root / member
        if source.is_symlink() or not source.is_file():
            raise BundleRefused('%s is missing from the source tree' % member)
        (destination / member).write_bytes(source.read_bytes())
    packaged = _RULES._bundle_version_of(
        destination / 'scripts' / 'rf_headroom_research_run.py')
    if runner_version is not None and runner_version != packaged:
        raise BundleRefused('the requested runner version %r is not the packaged one %r'
                            % (runner_version, packaged))
    receipt = dict(
        runner_version=runner_version or packaged,
        source_commit=source_commit,
        builder=(builder or 'unspecified-builder'),
        members={member: dict(sha256=_sha256(destination / member),
                              bytes=(destination / member).stat().st_size,
                              ) for member in BUNDLE_MEMBERS},
    )
    (destination / BUNDLE_RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2, sort_keys=True), encoding='utf-8')
    return receipt


def verify_run_bundle(directory):
    """Re-verify a bundle member by member; refuse by name on any difference.

    The board entry performs this same verification before any hardware action
    (spec 4); this host copy exists so an upload can be checked BEFORE it is
    sent, with the identical rules.
    """
    return _RULES.verify_run_bundle_members(Path(directory), BUNDLE_MEMBERS)

