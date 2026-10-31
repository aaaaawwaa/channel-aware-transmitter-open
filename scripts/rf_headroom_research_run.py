"""Real HEADROOM-1 research runner: gates, one-shot authorization, one study.

The runner is a NEW caller of the frozen research chain, never a second
implementation of it.  It does not parse the protocol, open a socket, build
research arguments, decide capability, or judge rail/terminal states: those
belong to `HeadroomController`, `RfStatusClient` and the release's own
`rf_headroom` module, which this file loads from the INSTALLED release
directory BY PATH (`load_release_module`) -- never by module name.

What the runner owns:

* the gate order (`G0` authorization + wiring, `G1` storage preflight,
  `G2` release attestation, `G3` release identity, `G4` worker attestation),
  every one of them fail-closed and ordered before any physical action;
* the one-shot human authorization and its append-only consumption ledger
  (`rf_headroom_deploy.AttemptJournal` semantics: fsync per record, torn
  trailing line truncated first);
* wiring freshness, with a fixed window and no command-line override;
* the ONE synchronous execution entry (`RfStatusWorker.headroom_run`), called
  once per process; the runner never calls `headroom_begin/load/capture/
  accept/abort` and never builds a second controller or a second client;
* runner-level terminal labels, all with `retry_allowed=False`;
* the final independent read-only safety readback, in a separate session, so
  the run's conclusion never comes from the research connection;
* `run-gate.json`, a runner-owned document beside the recorder's `receipt.json`
  (the recorder's own schema is not touched).

The authorized clock is NOT the board's.  Contract section 8 forbids board-side
RTC time for ordering events or deciding a validity window -- the board has no
RTC battery, so a real board reports 2021 -- and the 46 dB dispatch measured on
2026-10-15 was refused by G0 exactly because the window was judged with it.  G0
therefore resolves the session's TRUSTED CLOCK ANCHOR first: the host UTC the
operator carries into the session, bound to the board's own `boot_id` and uptime
(`now = host_utc + (board_uptime - anchor_uptime)`).  Every failure of that
resolution refuses by name (`RUN_REFUSED_CLOCK_ANCHOR`) BEFORE any board read and
before the nonce is consumed: there is no fallback to the board clock, and no
option that could allow one.

This implementation is offline-only by construction: it contains no transport
at all -- no socket, no SSH, no subprocess, no systemd, no installer.  Every
board access is an injected dependency (see `Dependencies`), and the default
bundle REFUSES instead of inventing one.  The runner has therefore never run
against hardware; see the design document
`docs/superpowers/plans/2026-10-12-real-headroom-research-runner-design.md`.

The BOARD RUN BUNDLE stage (spec
`docs/superpowers/specs/2026-10-13-headroom-board-run-package-design.md`) adds
the board entry's own startup gates, in the fixed order that spec names: the
uploaded bundle is re-verified member by member against `bundle-receipt.json`
before anything else (spec 4), the installed release is identified from its own
verified real path and only then may its modules be imported (spec 3), the
one-shot dispatch marker is created with `O_EXCL` (spec 6.2), and the one-shot
nonce ledger lives on the board's persistent path (spec 2).  Those gates are
reached through `main()`, which is what the board runs; the in-process API
(`run_research`) keeps the offline harness's injected dependencies and the
host-side `--ledger` seam unchanged.
"""
import argparse
import ast
import dataclasses
import datetime
import functools
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNNER_VERSION = '1.0.3'

# --- the reviewed contract this runner is allowed to drive -------------------
SOFTWARE_CONTRACT = 'cnn-bpsk-headroom-v1'
# The qualified candidate release.  Cross-checked against the sealed
# `python/critical-review.json` (`candidate_release_id`) by the test suite, so
# the pin is not an invention of this file.  Migrated from b82b141e... (the
# release installed on the board) to aba80e92... (framing fix) and then to this
# minimal revision of b67a8aaa, which carries the reviewed `src_cfg` wrap-bit
# predicate repair (`src_cfg & ~0x6 == 0`); see
# `evidence/rf-teaching/s2-headroom-pin-migration-2026-10-16/NOTES.md` and
# `evidence/rf-teaching/s2-headroom-candidate2-2026-10-17/NOTES.md`.
#
# HONESTY: this release is NOT yet independently sealed or reviewed.  The
# cross-check below therefore FAILS by name (missing critical-review.json in the
# new stage) until the next stage supplies the independent review and the new
# source freeze -- that failure is the gate, not an accident.
QUALIFIED_RELEASE = 'e66197ed79131b73aa4f47804487c053'
DAEMON_VERSION = '1.0.4'
PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 6
AUTHORIZED_ATTEMPTS = 5
ATTEMPT_BUDGET = 5
RESEARCH_ATTENUATION_MDB = 46000

# The two research modules whose digests the runner re-checks against the
# frozen values in `source-freeze.json` (cross-checked by the test suite).
FROZEN_RESEARCH_MODULES = {
    'rf_headroom.py':
        'fa8f92c5b85f6077172f0556dec42b2abe21dd2e731da88ae550846c67c62355',
    'rf_headroom_replay.py':
        'c446262b2646ca73b8756fad4e4d66e21c3fc9723ed1e15ed1a939b1db995500',
}

# Fixed by design; NOT a command-line option.  A confirmation older than this
# is refused, and there is no `--ignore-wiring-age`.
WIRING_FRESHNESS_S = 12 * 60 * 60
WIRING_FUTURE_SKEW_S = 60
WIRING_STATEMENT = 'TX1->RX1 physical path present'

# --- the trusted clock anchor (contract section 8) --------------------------
# The board's RTC has no battery, so a real board reports 2021; board-side time
# may therefore NOT decide an authorization window, judge wiring freshness or
# stamp evidence.  The authorized instant is the session's host UTC, carried in
# by the operator and bound to the board's own boot id and uptime:
#
#     now = host_utc + (board_uptime_now - anchor_uptime)
#
# Both limits below are FIXED here and are not command-line options: there is no
# --anchor-age, no --anchor-tolerance and no --allow-board-clock.  A missing or
# unusable anchor refuses by name (RUN_REFUSED_CLOCK_ANCHOR); it never falls back
# to the board's clock.
BOARD_UPTIME_PATH = '/proc/uptime'
CLOCK_ANCHOR_MAX_AGE_S = 120          # how old the anchor may be, in board uptime
CLOCK_ANCHOR_FUTURE_TOLERANCE_S = 60  # how far an anchor may sit in the future
CLOCK_ANCHOR_SOURCE = 'host UTC anchored to the board boot_id and uptime (/proc)'
CLOCK_ANCHOR_FORMULA = 'host_utc + (board_uptime - anchor_uptime), bound to the board boot_id'

# The runner's own board-side staging prefix.  It must NOT be the reviewed
# installer's prefix: reusing that would disguise the runner as the installer.
RESEARCH_STAGING_PREFIX = '/tmp/rf-headroom-research-'
INSTALLER_STAGING_PREFIX = '/tmp/rf-teaching-install-'

# Stock paths of a board-resident deployment.  They are defaults, not facts:
# nothing here is reachable without an injected dependency.
DEFAULT_RELEASE_DIRECTORY = '/home/xilinx/overlays/awg_merged/current'
DEFAULT_EXPERIMENT_ROOT = '/home/xilinx/experiments'

# --- runner-level terminal labels (design (c)); all retry_allowed=False ------
RUN_NOT_STARTED_PREFLIGHT = 'RUN_NOT_STARTED_PREFLIGHT'
# G0 could not establish the authorized clock (contract section 8): a missing or
# unusable host UTC anchor.  It is deliberately its own terminal, so the 2021
# board-clock defect is legible in one word instead of being read as a general
# preflight failure.
RUN_REFUSED_CLOCK_ANCHOR = 'RUN_REFUSED_CLOCK_ANCHOR'
RUN_REFUSED_CAPABILITY = 'RUN_REFUSED_CAPABILITY'
RUN_COMPLETED = 'RUN_COMPLETED'
RUN_INTERRUPTED_MID_ATTEMPT = 'RUN_INTERRUPTED_MID_ATTEMPT'
RUN_UNKNOWN_DISCONNECT = 'RUN_UNKNOWN_DISCONNECT'
RUN_UNKNOWN_REBOOT = 'RUN_UNKNOWN_REBOOT'
RUN_ABORTED_STORAGE_FAULT = 'RUN_ABORTED_STORAGE_FAULT'
# A post-run storage read that failed or did not complete.  It is deliberately
# NOT `RUN_ABORTED_STORAGE_FAULT` (no fault was detected -- none could be) and
# deliberately NOT `RUN_COMPLETED`: design 2.4 G1 states that a failed read is
# NOT evidence of an error-free board, and design 6 forbids "reading failure
# treated as no errors".  The board's storage health AND the boot-id/reboot
# comparison are unknown, so the run is an unknown outcome with a non-zero exit.
RUN_UNKNOWN_STORAGE_UNREADABLE = 'RUN_UNKNOWN_STORAGE_UNREADABLE'
RUN_UNKNOWN_READBACK_INCOMPLETE = 'RUN_UNKNOWN_READBACK_INCOMPLETE'
RUN_EVIDENCE_WRITE_FAILED = 'RUN_EVIDENCE_WRITE_FAILED'

# spec 7: the named terminals the BOARD RUN PACKAGE adds.  Each one is defined
# exactly once, as a module constant whose value is its own name, and every one
# is collected into FIXED_TERMINAL_LABELS below -- a terminal label that only
# exists as a scattered literal is a terminal nobody can audit.
RUN_REFUSED_BUNDLE = 'RUN_REFUSED_BUNDLE'
RUN_REFUSED_IMPORT = 'RUN_REFUSED_IMPORT'
RUN_REFUSED_AUTHORIZATION = 'RUN_REFUSED_AUTHORIZATION'
RUN_ABORTED_PREFLIGHT_UNKNOWN = 'RUN_ABORTED_PREFLIGHT_UNKNOWN'
RUN_STOPPED_CANCELLED = 'RUN_STOPPED_CANCELLED'

FIXED_TERMINAL_LABELS = (
    RUN_NOT_STARTED_PREFLIGHT, RUN_REFUSED_CLOCK_ANCHOR, RUN_REFUSED_CAPABILITY,
    RUN_COMPLETED,
    RUN_INTERRUPTED_MID_ATTEMPT, RUN_UNKNOWN_DISCONNECT, RUN_UNKNOWN_REBOOT,
    RUN_ABORTED_STORAGE_FAULT, RUN_UNKNOWN_STORAGE_UNREADABLE,
    RUN_UNKNOWN_READBACK_INCOMPLETE, RUN_EVIDENCE_WRITE_FAILED,
    RUN_REFUSED_BUNDLE, RUN_REFUSED_IMPORT, RUN_REFUSED_AUTHORIZATION,
    RUN_ABORTED_PREFLIGHT_UNKNOWN, RUN_STOPPED_CANCELLED,
)

# spec 7: the controller's own operator-cancel tag.  `stopped_label` already
# produces this terminal by concatenation; naming the tag keeps the two spellings
# of the same terminal provably identical instead of accidentally equal.
STOPPED_CANCELLED_TOKEN = 'CANCELLED'

# --- the board run package's fixed layout and startup gates (spec 2/3/4) -----
PERSISTENT_ROOT = '/var/lib/rf-headroom-research'
BOARD_LEDGER_DIR = PERSISTENT_ROOT + '/ledger'
BOARD_EVIDENCE_ROOT = PERSISTENT_ROOT + '/evidence'
BOARD_OBSERVATION_DIR = PERSISTENT_ROOT + '/observation'
LEDGER_FILE_NAME = 'authorization.jsonl'
DISPATCH_DIRECTORY_NAME = 'dispatch'
BUNDLE_RECEIPT_NAME = 'bundle-receipt.json'
# spec 1.3: the uploaded run bundle's declared members.  `scripts/__init__.py`
# does NOT exist in the repository (it is a namespace package); the packaging
# path GENERATES it, and the board then imports runner and checker as
# `scripts.<name>`.  Nothing else may be in the bundle.
BUNDLE_MEMBERS = ('scripts/__init__.py',
                  'scripts/rf_headroom_research_run.py',
                  'scripts/rf_teaching_install_state.py')
GENERATED_PACKAGE_INIT = ('"""Board-side package marker for the HEADROOM run '
                          'bundle (generated)."""\n')
BOARD_STARTUP_GATE = 'B0-BUNDLE-MEMBERS'
BOARD_IMPORT_GATE = 'B1-CONTROLLED-IMPORT'
BOARD_STARTUP_GATE_ORDER = (BOARD_STARTUP_GATE, BOARD_IMPORT_GATE)
# `RUN_STOPPED_<controller stopped_at>` is generated from the frozen
# controller's own vocabulary; these are the start-up refusals that consume no
# attempt (design 4.4(b): UNAVAILABLE, CAPABILITY, STOPPED -- the same three
# values as the frozen worker's own `_HEADROOM_NOT_ATTEMPTED`), reported as a
# capability refusal instead.
STOPPED_LABEL_PREFIX = 'RUN_STOPPED_'
PRE_ATTEMPT_REFUSALS = ('UNAVAILABLE', 'CAPABILITY', 'STOPPED')
# The post-run storage statuses that mean "health unknown", not "no faults".
POST_STORAGE_UNKNOWN_STATUSES = ('UNREADABLE', 'INCOMPLETE')
_STOPPED_TOKEN = re.compile('[A-Za-z0-9_.:-]{1,64}')

# Exit codes: 0 only for a completed study.  A gate refusal makes no board
# write and nothing was attempted, which is a different class from a study that
# ran and stopped.
EXIT_COMPLETED = 0
EXIT_NOT_STARTED = 2
EXIT_STOPPED = 3

# The frozen chain's own messages for a connection that is gone.  Matching them
# is how a disconnect is told apart from a deadline: the runner does not invent
# a transport predicate, it reads the strings the frozen client produces.
DISCONNECT_MARKERS = (
    'research connection was lost; no reconnect',
    'rf daemon disconnected',
    'truncated research iq payload',
    'research requires its original live i/o lease',
)

GATE_AUTHORIZATION = 'G0-AUTHORIZATION'
GATE_STORAGE = 'G1-STORAGE-PREFLIGHT'
GATE_ATTESTATION = 'G2-RELEASE-ATTESTATION'
GATE_IDENTITY = 'G3-RELEASE-IDENTITY'
GATE_WORKER = 'G4-WORKER-ATTESTATION'
GATE_CHAIN = 'G5-G8-SINGLE-CHAIN'

GATE_ORDER = (GATE_AUTHORIZATION, GATE_STORAGE, GATE_ATTESTATION,
              GATE_IDENTITY, GATE_WORKER, GATE_CHAIN)

OBSERVATION_PHYSICAL = 'NEW_PHYSICAL_FRAME'
OBSERVATION_REPLAY = 'REPLAYED_SAME_IQ'
OBSERVATION_SIMULATED = 'SIMULATED_FAKE_HARDWARE'

GATE_DOCUMENT_NAME = 'run-gate.json'
GATE_REFUSAL_DIRECTORY = 'run-gate'
AUTHORIZATION_MAX_BYTES = 65536
_SHA256 = re.compile('[0-9a-f]{64}')
_HEX32 = re.compile('[0-9a-f]{32}')
_UUID = re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

NOT_RUN_DEFAULT = (
    'physical HEADROOM research: no authorized session was run here, so this '
    'document reports gates only and carries no physical frame',
)
NOT_RUN_BOARD = (
    'real board contact (SSH/install/Overlay/arm/transmit/capture/systemd)',
    'real 46 dB physical frames (0)',
    'real initializations (0)',
    'new-candidate ARM G1 for this runner (NOT_RUN)',
    'real storage-fault abort on hardware (NOT_RUN)',
)


class RunnerRefused(RuntimeError):
    """A gate refused.  Never a study that ran: zero attempts, zero writes."""

    def __init__(self, label, gate, reason, **detail):
        super().__init__(reason)
        self.label = label
        self.gate = gate
        self.reason = reason
        self.detail = detail


class DependencyUnavailable(RunnerRefused):
    """A required board access was not injected; the runner refuses."""

    def __init__(self, gate, what, reason=None):
        super().__init__(RUN_NOT_STARTED_PREFLIGHT, gate,
                         reason or ('no %s implementation is wired in: this runner '
                                    'stage is offline-only and never invents board '
                                    'access' % what),
                         dependency=what)


class StudySlotTaken(RunnerRefused):
    """A second study in one process: structural, not a runtime convention."""

    def __init__(self):
        super().__init__(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                         'this process already claimed its single study slot; '
                         'one study per process is structural',
                         dependency='claim_study_slot')


# --------------------------------------------------------------- small helpers
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_utc(text, what):
    """Parse an ISO8601 instant WITH an offset; naive timestamps are refused."""
    if type(text) is not str or not text:
        raise ValueError(what + ' must be a non-empty ISO8601 string')
    value = text.strip()
    if value.endswith('Z') or value.endswith('z'):
        value = value[:-1] + '+00:00'
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(what + ' is not ISO8601')
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(what + ' has no UTC offset')
    return parsed


def atomic_write_bytes(path, data):
    """Write one file with fsync + replace, so a reader never sees half of it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))
    return sha256_bytes(data)


def atomic_write_json(path, value):
    return atomic_write_bytes(path, json.dumps(
        value, sort_keys=True, indent=2, allow_nan=False, default=str).encode('utf-8'))


def load_release_module(directory, name):
    """Load one module from the release directory BY PATH, never by name.

    `importlib.import_module` resolves through `sys.path`, so a different copy
    of the same file could be imported and only compared afterwards.  Loading
    the attested path makes "this is the release's file" structural.  The same
    helper exists in `rf_status_worker` for the same reason.
    """
    path = Path(directory) / (name + '.py')
    if path.is_symlink() or not path.is_file():
        raise ValueError('the release does not carry a regular file: ' + name + '.py')
    spec = importlib.util.spec_from_file_location('release_' + name, str(path))
    if spec is None or spec.loader is None:
        raise ValueError('cannot load from the release: ' + name)
    module = importlib.util.module_from_spec(spec)
    # A module loaded BY PATH must still be registered: the release's own modules
    # define dataclasses, and dataclasses resolves a class's string annotations
    # through sys.modules[cls.__module__] -- which is None for an unregistered
    # module, raising AttributeError while the class body is executing.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def load_module_by_path(path):
    """Load a test-only dependency module (the simulated fake chain) by path."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('the fake chain must be a regular file')
    spec = importlib.util.spec_from_file_location('fake_chain', str(path))
    if spec is None or spec.loader is None:
        raise ValueError('cannot load the fake chain module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_prefix_separation(research_prefix=RESEARCH_STAGING_PREFIX,
                             installer_prefix=INSTALLER_STAGING_PREFIX):
    """The runner's staging prefix must not be the installer's, either way.

    The reviewed installer accepts exactly `/tmp/rf-teaching-install-*` and
    exits 2 for anything else.  Sharing that prefix would let a research
    staging directory be mistaken for an installation one, so the overlap is
    rejected here -- at import time, where it cannot be forgotten.
    """
    for value in (research_prefix, installer_prefix):
        if type(value) is not str or not value.startswith('/tmp/') or not value.endswith('-'):
            raise AssertionError('staging prefixes are absolute /tmp/ prefixes: ' + repr(value))
    if (research_prefix.startswith(installer_prefix)
            or installer_prefix.startswith(research_prefix)):
        raise AssertionError('the research staging prefix must not overlap the '
                             'installer prefix ' + installer_prefix)
    return True


assert_prefix_separation()


def staging_directory(attempt_id):
    """The runner's board-side staging directory for one attempt."""
    if type(attempt_id) is not str or _HEX32.fullmatch(attempt_id) is None:
        raise ValueError('attempt_id must be 32 lowercase hex characters')
    return RESEARCH_STAGING_PREFIX + attempt_id


# spec 12.3(b): the reviewed storage predicate is PINNED here.  The reviewed
# implementation lives in `scripts/rf_headroom_deploy.py`, which is a HOST-ONLY
# tool and is deliberately NOT a member of the board run bundle (spec 1.3), so a
# board run may not import it.  The keys and the per-line predicate below are the
# reviewed ones character for character, and the offline suite still proves the
# two agree (`test_the_storage_predicate_is_the_reviewed_deploy_predicate`).
KERNEL_FAULT_KEYS = ('timeout waiting for hardware interrupt', 'adma err', 'i/o error',
                     'buffer i/o error', 'ext4-fs error', 'mmc0: error')


def storage_faults(text):
    """Kernel storage-fault lines, as a sorted unique list."""
    return sorted({line.strip() for line in (text or '').splitlines()
                   if any(key in line.lower() for key in KERNEL_FAULT_KEYS)})


def kernel_fault_keys():
    return KERNEL_FAULT_KEYS


def _host_reviewed_journal():
    """The reviewed HOST journal the offline `--ledger` seam reuses.

    `AttemptJournal` is the reviewed append-only, fsynced journal the deployment
    path already uses; the offline seam reuses that exact object instead of
    re-implementing it.  It is loaded lazily and ONLY there: the board entry uses
    `BoardAuthorizationLedger`, which is carried by the run bundle itself (spec
    2/6.1), because the board has no `rf_headroom_deploy`.  Loading it here also
    CROSS-CHECKS the pinned board predicate against the reviewed host one: a
    divergence is a refusal, never a silent difference.
    """
    try:
        from scripts.rf_headroom_deploy import (AttemptJournal, KERNEL_FAULT_KEYS as reviewed_keys,
                                                storage_faults as reviewed_predicate)
    except Exception as exc:                      # noqa: BLE001 - reported, not raised
        raise DependencyUnavailable(
            GATE_STORAGE, 'reviewed host ledger',
            'the reviewed append-only journal is unavailable (%s); the offline ledger '
            'seam does not re-implement it' % type(exc).__name__)
    if tuple(reviewed_keys) != KERNEL_FAULT_KEYS:
        raise DependencyUnavailable(
            GATE_STORAGE, 'reviewed storage/ledger predicate',
            'the pinned board storage predicate no longer equals the reviewed host one; '
            'the board path does not guess which of the two is right')
    sample = '[    2.000000] ' + KERNEL_FAULT_KEYS[0]
    if reviewed_predicate(sample) != storage_faults(sample):
        raise DependencyUnavailable(
            GATE_STORAGE, 'reviewed storage/ledger predicate',
            'the pinned board storage predicate and the reviewed host one disagree on '
            'the same kernel line; the board path does not guess')
    return AttemptJournal


class ConsumptionLedger:
    """The OFFLINE seam's append-only, fsynced one-shot ledger.

    The append/repair/fsync semantics are `rf_headroom_deploy.AttemptJournal`'s
    -- the same reviewed object the deployment path uses -- and are NOT
    re-implemented here.  This class exists for the offline harness seam
    (`--ledger <file>`); a BOARD run uses `BoardAuthorizationLedger` on the
    board persistent root instead (spec 2/6.1), because the bundle carries no
    `rf_headroom_deploy`.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._journal = _host_reviewed_journal()(self.path)

    def nonces(self):
        return {entry.get('nonce') for entry in self._journal.entries()}

    def refusal(self, nonce):
        if nonce in self.nonces():
            return ('the authorization nonce is already in the consumption ledger '
                    '(' + str(self.path) + '): a one-shot authorization cannot be replayed')
        return None

    def consume(self, **fields):
        return self._journal.record('AUTHORIZATION_CONSUMED', **fields)


class ClaimOnce:
    """One study per process, structurally."""

    def __init__(self):
        self._claimed = False

    def claim(self):
        if self._claimed:
            return False
        self._claimed = True
        return True


PROCESS_STUDY_SLOT = ClaimOnce()


# ------------------------------------------------------- the board run package
class BundleRefused(RuntimeError):
    """The uploaded run bundle does not match its own receipt.

    Every instance is raised BEFORE any hardware action, and the refusal writes
    nothing outside the runner's own evidence root (spec 4).  The host
    packer/verifier `scripts/rf_headroom_run_bundle.py` raises THIS class: the
    member rules have one implementation, shared by both sides.
    """


def _bundle_version_of(runner_file):
    """The packaged runner's own RUNNER_VERSION, read without executing it."""
    tree = ast.parse(Path(runner_file).read_text(encoding='utf-8'))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == 'RUNNER_VERSION'
                and isinstance(node.value, ast.Constant)
                and type(node.value.value) is str):
            return node.value.value
    raise BundleRefused('the packaged runner declares no RUNNER_VERSION string')


def _bundle_undeclared_members(directory, declared):
    """Every regular file in the bundle that the receipt does not declare.

    CPython's own `__pycache__` byte-code is not a member -- the interpreter
    writes it, it is never uploaded, and the board entry runs with `-B` -- but
    anything else is an undeclared member and refuses the bundle (spec 1.3/4).
    """
    root = Path(directory)
    found = []
    for item in sorted(root.rglob('*')):
        if not item.is_file():
            continue
        if '__pycache__' in item.parts:
            continue
        relative = item.relative_to(root).as_posix()
        if relative == BUNDLE_RECEIPT_NAME or relative in declared:
            continue
        found.append(relative)
    return found


def verify_run_bundle_members(directory, expected_members=BUNDLE_MEMBERS):
    """spec 1.3/4: re-verify every declared member against `bundle-receipt.json`.

    Refuses BY NAME: `<member> is missing from the bundle`, `<path> is not a
    declared bundle member`, `<member> does not match the receipt`.  It is
    read-only -- no release path, no system path and no state root is written --
    so it can run before every hardware action.  The host packer delegates its
    own verification to this function, so the rules cannot drift apart.
    """
    root = Path(directory)
    receipt_path = root / BUNDLE_RECEIPT_NAME
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise BundleRefused('the run bundle has no %s: nothing can be re-verified'
                            % BUNDLE_RECEIPT_NAME)
    try:
        receipt = json.loads(receipt_path.read_bytes().decode('utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise BundleRefused('the run bundle receipt is unreadable (%s)'
                            % type(exc).__name__)
    if type(receipt) is not dict:
        raise BundleRefused('the run bundle receipt is not a JSON object')
    members = receipt.get('members')
    if type(members) is not dict:
        raise BundleRefused('the run bundle receipt declares no members')
    if sorted(members) != sorted(expected_members):
        raise BundleRefused('the run bundle receipt declares another member set: %r'
                            % sorted(members))
    for member in expected_members:
        entry = members.get(member)
        member_path = root / member
        if member_path.is_symlink() or not member_path.is_file():
            raise BundleRefused('%s is missing from the bundle' % member)
        if type(entry) is not dict:
            raise BundleRefused('%s does not match the receipt' % member)
        data = member_path.read_bytes()
        if entry.get('sha256') != sha256_bytes(data) or entry.get('bytes') != len(data):
            raise BundleRefused('%s does not match the receipt' % member)
    undeclared = _bundle_undeclared_members(root, set(expected_members))
    if undeclared:
        raise BundleRefused('%s is not a declared bundle member' % undeclared[0])
    packaged = _bundle_version_of(root / 'scripts' / 'rf_headroom_research_run.py')
    if receipt.get('runner_version') != packaged:
        raise BundleRefused('the run bundle receipt names another runner version: %r, '
                            'the packaged runner is %r'
                            % (receipt.get('runner_version'), packaged))
    return receipt


def create_dispatch_marker(dispatch_dir, attempt_id, document):
    """spec 2/6.2: `dispatch/<attempt_id>.json`, created with `O_EXCL`.

    "Already dispatched" is decided by the filesystem, never by a look-then-write
    convention: a replay, or a second process racing on the same attempt id, gets
    `FileExistsError` and the marker already there is preserved byte for byte.
    The marker is the zero-action refusal's evidence (spec 4.3).
    """
    if type(attempt_id) is not str or _HEX32.fullmatch(attempt_id) is None:
        raise ValueError('attempt_id must be 32 lowercase hex characters')
    directory = Path(dispatch_dir)
    directory.mkdir(parents=True, exist_ok=True)
    marker_path = directory / (attempt_id + '.json')
    payload = json.dumps(document, sort_keys=True, indent=2, allow_nan=False,
                         default=str).encode('utf-8')
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0)
    descriptor = os.open(str(marker_path), flags, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return str(marker_path)


class BoardAuthorizationLedger:
    """spec 2/6.1/6.3: the BOARD-side one-shot nonce ledger.

    `<ledger-dir>/authorization.jsonl` on the board persistent root, appended to
    -- one fsynced line per consumed authorization -- with the torn trailing line
    a killed process leaves behind truncated before the next append: the
    `attempt.jsonl` semantics the deployment path already reviewed.  A board
    reboot does not empty it, so a consumed nonce stays refused (spec 6.3), and a
    replayed nonce never appends a second record.

    This is deliberately NOT the offline `ConsumptionLedger`: the run bundle
    carries no `rf_headroom_deploy`, and a board run may not depend on a tool the
    board does not have (spec 12.3).
    """

    def __init__(self, ledger_dir):
        self.ledger_dir = Path(ledger_dir)
        self.path = self.ledger_dir / LEDGER_FILE_NAME

    def repair(self):
        """Drop an incomplete trailing line left by a killed process."""
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

    def entries(self):
        """The valid prefix of the ledger; a torn last line is dropped, not fatal."""
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding='utf-8', errors='replace').splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                break
        return entries

    def nonces(self):
        return {entry.get('nonce') for entry in self.entries()}

    def refusal(self, nonce):
        if nonce in self.nonces():
            return ('the authorization nonce is already in the board ledger (%s): a '
                    'one-shot authorization cannot be replayed, and a board reboot is '
                    'not a new budget' % self.path)
        return None

    def consume(self, utc=None, **fields):
        """Append one consumed authorization.

        `utc` is the AUTHORIZED instant when the session carries a clock anchor
        (contract section 8): the ledger must record the host-anchored time, not
        the board's 2021 RTC.  Without an anchor the process clock is used, which
        on a board is only reachable through the offline host seam.
        """
        replay = self.refusal(fields.get('nonce'))
        if replay:
            raise RunnerRefused(RUN_REFUSED_AUTHORIZATION, GATE_AUTHORIZATION, replay,
                                nonce=fields.get('nonce'), ledger_path=str(self.path))
        self.repair()
        entry = dict(phase='AUTHORIZATION_CONSUMED', **fields)
        entry['utc'] = utc if utc is not None else utc_now().isoformat()
        entry['seq'] = len(self.entries()) + 1
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, sort_keys=True, allow_nan=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        return entry


def _is_source_tree(directory):
    """Whether a search-path entry looks like a development source tree (spec 3.4).

    The board entry may not import from the repository, so any entry that carries
    this package or the repository's product trees is dropped instead of being
    left to "happen to" resolve a module.
    """
    try:
        candidate = Path(directory)
        if not candidate.is_dir():
            return False
        if (candidate / 'scripts' / 'rf_headroom_research_run.py').is_file():
            return True
        return ((candidate / 'pynq_overlay').is_dir()
                or (candidate / 'rf_overlay').is_dir())
    except OSError:
        return False


def control_search_path(bundle_root, release_directory):
    """spec 3.1: the only PROJECT search entries are the bundle root and the release.

    The verified release's real path and the bundle root are put first, in that
    order; everything else that is a project path -- the entry script's own
    directory, which Python puts first, and any development tree -- is removed.
    The interpreter's standard-library and site-packages entries are left alone,
    because the release's own modules need them (numpy, json, ...).
    """
    release_directory = str(release_directory)
    bundle_root = str(bundle_root)
    keep = [release_directory, bundle_root]
    keep_keys = {os.path.normcase(os.path.normpath(item)) for item in keep}
    entry_directory = os.path.normcase(os.path.normpath(
        str(Path(os.path.abspath(str(__file__))).parent)))
    kept, dropped = [], []
    for item in sys.path:
        if not item:
            continue
        key = os.path.normcase(os.path.normpath(item))
        if key in keep_keys:
            continue
        if key == entry_directory or _is_source_tree(item):
            dropped.append(item)
            continue
        kept.append(item)
    sys.path[:] = keep + kept
    return dict(search_path=list(sys.path), dropped_search_path=dropped)

# ------------------------------------------------------------- authorizations
@dataclasses.dataclass(frozen=True)
class Wiring:
    confirmed_by: str
    confirmed_at: datetime.datetime
    statement: str
    method: str
    artifact_digest: str

    def to_dict(self, now=None, limit_s=WIRING_FRESHNESS_S):
        value = dict(confirmed_by=self.confirmed_by,
                     confirmed_at=self.confirmed_at.isoformat(),
                     statement=self.statement, method=self.method,
                     artifact_digest=self.artifact_digest,
                     freshness_limit_s=limit_s)
        if now is not None:
            value['age_s'] = (now - self.confirmed_at).total_seconds()
        return value


@dataclasses.dataclass(frozen=True)
class Authorization:
    authorization_id: str
    nonce: str
    session_id: str
    expected_release: str
    software_contract: str
    daemon_version: str
    protocol_minor: int
    issued_at: datetime.datetime
    expires_at: datetime.datetime
    single_use: bool
    wiring: Wiring
    token_ref: str
    authorized_attempts: int
    source_path: str
    artifact_sha256: str

    def to_dict(self):
        return dict(authorization_id=self.authorization_id, nonce=self.nonce,
                    session_id=self.session_id, expected_release=self.expected_release,
                    software_contract=self.software_contract,
                    daemon_version=self.daemon_version,
                    protocol_minor=self.protocol_minor,
                    issued_at=self.issued_at.isoformat(),
                    expires_at=self.expires_at.isoformat(),
                    single_use=self.single_use, token_ref=self.token_ref,
                    authorized_attempts=self.authorized_attempts,
                    source_path=self.source_path,
                    artifact_sha256=self.artifact_sha256)


AUTHORIZATION_FIELDS = ('authorization_id', 'nonce', 'session_id', 'expected_release',
                        'software_contract', 'daemon_version', 'protocol_minor',
                        'issued_at', 'expires_at', 'single_use', 'wiring', 'token_ref',
                        'authorized_attempts')
WIRING_FIELDS = ('confirmed_by', 'confirmed_at', 'statement', 'method', 'artifact_digest')


def load_authorization(path, now, expected_release=QUALIFIED_RELEASE):
    """Read and fully validate the one-shot authorization artifact.

    Every missing, malformed, expired or mismatched field refuses here, before
    any board access and before the nonce is consumed.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RunnerRefused(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                            'the one-shot authorization artifact is missing or not a '
                            'regular file: ' + str(path))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        # The artifact exists and is a regular file, but this process cannot read
        # it (permissions, a sharing violation, a vanished mount).  That is a
        # reported refusal, not an exception out of `run_research`.
        raise RunnerRefused(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                            'the one-shot authorization artifact could not be read (%s); '
                            'nothing was consumed and no board read was attempted'
                            % type(exc).__name__)
    if len(raw) > AUTHORIZATION_MAX_BYTES:
        raise RunnerRefused(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                            'the authorization artifact is too large')
    try:
        document = json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise RunnerRefused(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                            'the authorization artifact is not JSON: ' + type(exc).__name__)
    if type(document) is not dict:
        raise RunnerRefused(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                            'the authorization artifact is not a JSON object')
    missing = [name for name in AUTHORIZATION_FIELDS if name not in document]
    unknown = sorted(set(document) - set(AUTHORIZATION_FIELDS))
    if missing or unknown:
        raise RunnerRefused(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                            'the authorization artifact does not carry the reviewed field '
                            'set', missing_fields=missing, unknown_fields=unknown)

    def fail(reason, **detail):
        raise RunnerRefused(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION, reason, **detail)

    identifier = document['authorization_id']
    if type(identifier) is not str or not (_HEX32.fullmatch(identifier)
                                           or _UUID.fullmatch(identifier)):
        fail('authorization_id must be hex32 or a canonical UUID')
    nonce = document['nonce']
    if type(nonce) is not str or re.fullmatch('[0-9a-f]{16,128}', nonce) is None:
        fail('nonce must be 16..128 lowercase hex characters')
    session_id = document['session_id']
    if type(session_id) is not str or _HEX32.fullmatch(session_id) is None:
        fail('session_id must be 32 lowercase hex characters')
    release = document['expected_release']
    if type(release) is not str or _HEX32.fullmatch(release) is None:
        fail('expected_release must be 32 lowercase hex characters')
    if release != expected_release:
        fail('the authorization names a release this runner is not qualified for',
             expected_release=expected_release, authorized_release=release)
    if document['software_contract'] != SOFTWARE_CONTRACT:
        fail('software_contract must be ' + SOFTWARE_CONTRACT)
    if document['daemon_version'] != DAEMON_VERSION:
        fail('daemon_version must be ' + DAEMON_VERSION)
    if type(document['protocol_minor']) is not int or document['protocol_minor'] != PROTOCOL_MINOR:
        fail('protocol_minor must be exactly %d' % PROTOCOL_MINOR)
    if document['single_use'] is not True:
        fail('single_use must be exactly true')
    if (type(document['authorized_attempts']) is not int
            or document['authorized_attempts'] != AUTHORIZED_ATTEMPTS):
        fail('authorized_attempts must be exactly %d' % AUTHORIZED_ATTEMPTS)
    token_ref = document['token_ref']
    if type(token_ref) is not str or not token_ref or len(token_ref) > 4096:
        fail('token_ref must point at the operator token path')
    try:
        issued = parse_utc(document['issued_at'], 'issued_at')
        expires = parse_utc(document['expires_at'], 'expires_at')
    except ValueError as exc:
        fail('the authorization validity window is malformed: ' + str(exc))
    if expires <= issued:
        fail('expires_at must be after issued_at')
    if now < issued - datetime.timedelta(seconds=WIRING_FUTURE_SKEW_S):
        fail('the authorization is not valid yet', issued_at=issued.isoformat())
    if now >= expires:
        fail('the authorization has expired', expires_at=expires.isoformat(),
             now=now.isoformat())

    wiring_document = document['wiring']
    if type(wiring_document) is not dict:
        fail('wiring must be the human confirmation record')
    wiring_missing = [name for name in WIRING_FIELDS if name not in wiring_document]
    wiring_unknown = sorted(set(wiring_document) - set(WIRING_FIELDS))
    if wiring_missing or wiring_unknown:
        fail('the wiring record does not carry the reviewed field set',
             missing_fields=wiring_missing, unknown_fields=wiring_unknown)
    confirmed_by = wiring_document['confirmed_by']
    if type(confirmed_by) is not str or not confirmed_by.strip() or len(confirmed_by) > 128:
        fail('wiring.confirmed_by must name the person who confirmed the path')
    method = wiring_document['method']
    if type(method) is not str or not method.strip() or len(method) > 512:
        fail('wiring.method must describe how the path was confirmed')
    if wiring_document['statement'] != WIRING_STATEMENT:
        fail('wiring.statement must be the fixed reviewed statement',
             expected_statement=WIRING_STATEMENT)
    digest = wiring_document['artifact_digest']
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        fail('wiring.artifact_digest must be the sha256 of the confirmation artifact')
    try:
        confirmed_at = parse_utc(wiring_document['confirmed_at'], 'wiring.confirmed_at')
    except ValueError as exc:
        fail('the wiring confirmation time is malformed: ' + str(exc))
    age = (now - confirmed_at).total_seconds()
    if age < -WIRING_FUTURE_SKEW_S:
        fail('the wiring confirmation is stamped in the future',
             confirmed_at=confirmed_at.isoformat(), now=now.isoformat())
    if age > WIRING_FRESHNESS_S:
        fail('the wiring confirmation is older than the fixed %d h window; there is no '
             'override' % (WIRING_FRESHNESS_S // 3600),
             age_s=age, freshness_limit_s=WIRING_FRESHNESS_S)

    return Authorization(
        authorization_id=identifier, nonce=nonce, session_id=session_id,
        expected_release=release, software_contract=document['software_contract'],
        daemon_version=document['daemon_version'],
        protocol_minor=document['protocol_minor'], issued_at=issued,
        expires_at=expires, single_use=True,
        wiring=Wiring(confirmed_by=confirmed_by, confirmed_at=confirmed_at,
                      statement=wiring_document['statement'], method=method,
                      artifact_digest=digest),
        token_ref=token_ref, authorized_attempts=document['authorized_attempts'],
        source_path=str(path), artifact_sha256=sha256_bytes(raw))


# ------------------------------------------------------------- dependencies
@dataclasses.dataclass
class Dependencies:
    """Every board access the runner needs, injected.

    `worker_factory` is the ONLY way a worker appears, so the runner cannot
    build a second client or a second controller: it never constructs either
    class itself.  The remaining callables are the read-only probes.

    * `worker_factory(release_directory=..., experiment_root=...)` -> worker
      whose release attestation has already been read (so
      `headroom_status()['available']` is meaningful).
    * `storage_probe()` -> dict with `boot_id`, `kernel_storage` (raw kernel
      text) and `complete` (True only when the read really completed).
    * `identity_probe()` -> the live `device.info` identity, read on its own
      read-only session: `release_id`, `daemon_version`, `protocol_major`,
      `protocol_minor`.
    * `readback_probe(expected_release)` -> `{'state': ..., 'headroom': ...}`
      from a SEPARATE read-only session opened after the study.
    * `token_reader(path)` -> `(token, reason)`; the release's own
      `read_control_token`, which stores nothing.
    * `claim_study_slot()` -> True once per process.
    * `board_clock_probe()` -> the board's own `boot_id` and uptime, read from
      /proc on the board; the board half of the trusted clock anchor (section 8).
    * `clock()` -> aware UTC datetime, consulted ONLY by a bundle that does not
      declare real hardware (the offline harness and `simulate`).  A bundle that
      declares hardware and injects no clock must supply a board clock probe and
      the host anchor inputs: it has NO clock at all without them.

    There is no IMPLICIT default for any of them.  default_dependencies(
    release_directory) wires the board-side production bundle -- the runner runs
    on the board, beside the daemon it reads -- and a bundle that leaves one out
    makes the gate that needs it refuse by name.
    """
    worker_factory: object = None
    storage_probe: object = None
    identity_probe: object = None
    readback_probe: object = None
    token_reader: object = None
    claim_study_slot: object = None
    clock: object = None
    board_clock_probe: object = None
    cancelled: object = None
    simulation: bool = False
    hardware_touched: bool = False

    def claimed(self):
        """True exactly once per process (or per injected claim)."""
        claim = (self.claim_study_slot if self.claim_study_slot is not None
                 else PROCESS_STUDY_SLOT.claim)
        return claim() is True

    def now(self):
        """The bundle's own clock.  Never the board clock of a real run: a
        hardware bundle without an injected clock resolves its anchor instead
        (see `ResearchRun.authorized_now`), and refuses without one."""
        return self.clock() if self.clock is not None else utc_now()


# ------------------------------------------------- the board-side transport
BOOT_ID_PATH = '/proc/sys/kernel/random/boot_id'
KERNEL_STORAGE_COMMAND = ('dmesg', '--color=never')


def local_kernel_storage(boot_id_path=BOOT_ID_PATH, command=KERNEL_STORAGE_COMMAND,
                         runner=subprocess.run):
    """The board's own kernel storage read: the boot id and the whole dmesg.

    A read that fails or returns nothing raises.  G1 records the attempt as a
    FAILED read, which is not evidence of an error-free board -- the same rule
    the deploy preflight applies to the same text.
    """
    result = runner(list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    universal_newlines=True, timeout=30)
    boot_id = Path(boot_id_path).read_text(encoding='ascii').strip()
    text = result.stdout or ''
    if result.returncode != 0 or not text.strip() or not boot_id:
        raise OSError('the local kernel storage read did not complete (rc=%s)'
                      % result.returncode)
    return dict(boot_id=boot_id, kernel_storage=text, complete=True)


# ------------------------------------------------ the trusted clock anchor (§8)
def local_board_clock(boot_id_path=BOOT_ID_PATH, uptime_path=BOARD_UPTIME_PATH):
    """The board's OWN boot id and monotonic uptime: the anchor's board half.

    Both come from the kernel, not from a wall clock, which is exactly why they
    can anchor a session whose RTC is dead.  The board entry reads them itself
    (contract section 8); a read that fails, an empty boot id, or an uptime that
    is not a finite non-negative number RAISES, so the caller refuses by name
    instead of filling the gap with the board's own -- nonexistent -- clock.
    """
    boot_id = Path(boot_id_path).read_text(encoding='ascii').strip()
    if not boot_id:
        raise ValueError('the board reports no boot id in ' + str(boot_id_path))
    text = Path(uptime_path).read_text(encoding='ascii')
    fields = text.split()
    try:
        uptime_s = float(fields[0])
    except (IndexError, ValueError):
        raise ValueError('the board uptime in %s is not a number: %r'
                         % (uptime_path, text.strip()[:64]))
    if not math.isfinite(uptime_s) or uptime_s < 0.0:
        raise ValueError('the board uptime is not a finite non-negative number: %r'
                         % uptime_s)
    return dict(boot_id=boot_id, uptime_s=uptime_s)


@dataclasses.dataclass(frozen=True)
class ClockAnchor:
    """The authorized clock for ONE session: host UTC + this boot's uptime.

    The forward delta is resolved ONCE, so every decision and every timestamp of
    the session describes the same instant, and the values that produced it are
    recorded by `to_dict()` rather than left to be reconstructed afterwards.
    """

    host_utc: datetime.datetime
    boot_id: str
    anchor_uptime_s: float
    board_uptime_s: float
    age_s: float

    def now(self):
        return self.host_utc + datetime.timedelta(seconds=self.age_s)

    def to_dict(self):
        return dict(
            used=True, board_clock_used=False,
            source=CLOCK_ANCHOR_SOURCE, formula=CLOCK_ANCHOR_FORMULA,
            host_utc=self.host_utc.isoformat(),
            resolved_now=self.now().isoformat(),
            board_boot_id=self.boot_id,
            anchor_uptime_s=self.anchor_uptime_s,
            board_uptime_s=self.board_uptime_s, age_s=self.age_s,
            max_age_s=CLOCK_ANCHOR_MAX_AGE_S,
            future_tolerance_s=CLOCK_ANCHOR_FUTURE_TOLERANCE_S)


def resolve_clock_anchor(host_utc, anchor_boot_id, anchor_uptime_s, *,
                         board_clock_reader=None):
    """Resolve the trusted clock anchor; every failure refuses BY NAME.

    The failure set is closed and every member is fail-closed: a missing
    parameter, a host UTC without an offset (or not ISO8601 at all), an
    unreadable board clock, a boot id that is not this boot's, an uptime that
    went backwards since the anchor was taken, an anchor older than the fixed
    tolerance, and an anchor stamped in the future.  NONE of them falls back to
    the board's wall clock: that fallback IS the 2021 defect (the 46 dB dispatch
    of 2026-10-15 was refused with 'the authorization is not valid yet' because
    the window was judged against it).
    """
    def fail(reason, **detail):
        raise RunnerRefused(RUN_REFUSED_CLOCK_ANCHOR, GATE_AUTHORIZATION, reason,
                            **detail)

    missing = [flag for flag, value in
               (('--host-utc', host_utc), ('--anchor-boot-id', anchor_boot_id),
                ('--anchor-uptime-s', anchor_uptime_s)) if value is None]
    if missing:
        fail('the trusted clock anchor is incomplete (missing %s): the board RTC '
             'cannot decide a validity window or a wiring age, and this runner '
             'never falls back to the board clock' % ', '.join(missing),
             missing_anchor_parameters=missing)
    try:
        host = parse_utc(host_utc, '--host-utc')
    except ValueError as exc:
        fail('the host UTC anchor is unusable: ' + str(exc), host_utc=host_utc)
    if type(anchor_boot_id) is not str or not anchor_boot_id.strip():
        fail('--anchor-boot-id must be the board boot id the host read',
             anchor_boot_id=anchor_boot_id)
    if type(anchor_uptime_s) is bool:
        fail('--anchor-uptime-s must be the board uptime in seconds',
             anchor_uptime_s=repr(anchor_uptime_s))
    try:
        anchor_uptime = float(anchor_uptime_s)
    except (TypeError, ValueError):
        fail('--anchor-uptime-s must be the board uptime in seconds',
             anchor_uptime_s=repr(anchor_uptime_s))
    if not math.isfinite(anchor_uptime) or anchor_uptime < 0.0:
        fail('--anchor-uptime-s must be a finite non-negative number of seconds',
             anchor_uptime_s=repr(anchor_uptime_s))

    # Resolved at CALL time (not bound as a default), so the board's own /proc
    # read stays the one and only production reader and can still be injected by
    # a test or by the offline harness.
    reader = board_clock_reader if board_clock_reader is not None else local_board_clock
    try:
        board = reader()
    except Exception as exc:                      # noqa: BLE001 - reported, not raised
        fail('the board clock anchor could not be read (%s): %s; the board RTC is '
             'not used as a fallback' % (type(exc).__name__, exc),
             error_type=type(exc).__name__)
    if type(board) is not dict:
        fail('the board clock read returned nothing usable')
    board_boot_id = board.get('boot_id')
    if type(board_boot_id) is not str or not board_boot_id.strip():
        fail('the board clock read returned no boot id')
    board_uptime = board.get('uptime_s')
    if type(board_uptime) is bool or not isinstance(board_uptime, (int, float)):
        fail('the board clock read returned no uptime')
    board_uptime = float(board_uptime)
    if not math.isfinite(board_uptime) or board_uptime < 0.0:
        fail('the board clock read returned an unusable uptime',
             board_uptime_s=repr(board_uptime))
    if board_boot_id.strip() != anchor_boot_id.strip():
        fail('the board boot id is not the one the anchor was taken on: the anchor '
             'belongs to another boot', anchor_boot_id=anchor_boot_id.strip(),
             board_boot_id=board_boot_id.strip())

    age = board_uptime - anchor_uptime
    if age > CLOCK_ANCHOR_MAX_AGE_S:
        fail('the clock anchor is older than the fixed %d s tolerance: the host UTC '
             'instant it carries no longer identifies this session'
             % CLOCK_ANCHOR_MAX_AGE_S, age_s=age, max_age_s=CLOCK_ANCHOR_MAX_AGE_S)
    if age < -CLOCK_ANCHOR_FUTURE_TOLERANCE_S:
        fail('the clock anchor is stamped in the future: the board uptime is more '
             'than the fixed %d s behind the anchor'
             % CLOCK_ANCHOR_FUTURE_TOLERANCE_S, age_s=age,
             future_tolerance_s=CLOCK_ANCHOR_FUTURE_TOLERANCE_S)
    if age < 0.0:
        fail('the board uptime went backwards since the anchor was taken, so the '
             'anchor is not a monotonic clock for this boot', age_s=age)
    return ClockAnchor(host_utc=host, boot_id=board_boot_id.strip(),
                       anchor_uptime_s=anchor_uptime, board_uptime_s=board_uptime,
                       age_s=age)


def release_verifier_for(release_directory):
    """The release's OWN `merged_release` (spec 3.2), loaded BY PATH.

    The board entry's startup gate refuses any release that does not carry a
    regular `merged_release.py`, so the board always attests through the
    release's own verifier.  The offline harness's synthetic release trees
    carry a placeholder file that cannot execute; ONLY there (and only for the
    injected-dependency API path, which opens no board session) the reviewed
    host module is used, and the G2 record names which one ran.
    """
    try:
        return (load_release_module(release_directory, 'merged_release'),
                "the release's own merged_release.py, loaded BY PATH")
    except Exception:                             # noqa: BLE001 - reported by G2
        from scripts import merged_release
        return (merged_release,
                'the reviewed host merged_release (the release carries no loadable '
                'copy: offline harness only)')


def release_client(release_directory):
    """The INSTALLED release's own client: the socket belongs to the frozen class.

    The runner neither opens a socket nor frames a request itself; it hands the
    read to the release the manifest attested, exactly as the worker does.
    """
    return load_release_module(release_directory, 'rf_status_client').RfStatusClient()


def live_identity(release_directory, factory=None):
    """The live release identity, on its own read-only session (design G3)."""
    client = (factory or release_client)(release_directory)
    try:
        snapshot = client.poll()
    finally:
        client.close()
    headroom = snapshot.headroom if isinstance(snapshot.headroom, dict) else {}
    identity = dict(release_id=snapshot.release_id,
                    daemon_version=snapshot.daemon_version,
                    protocol_major=headroom.get('announced_major'),
                    protocol_minor=headroom.get('hello_minor'))
    if (type(identity['release_id']) is not str or not identity['release_id']
            or type(identity['daemon_version']) is not str
            or type(identity['protocol_major']) is not int
            or type(identity['protocol_minor']) is not int):
        raise ValueError('the live device.info identity is incomplete')
    return identity


def independent_readback(expected_release, inspector=None):
    """The SEPARATE read-only safety readback, on the reviewed inspector."""
    if inspector is None:
        from scripts.rf_teaching_install_state import inspect_state
        inspector = inspect_state
    document = inspector(expected_release)
    status = document.get('status') if isinstance(document, dict) else None
    headroom = status.get('headroom') if isinstance(status, dict) else None
    return dict(state=document, headroom=headroom if isinstance(headroom, dict) else {})


def release_token_reader(release_directory):
    """The release's own read_control_token: it reads the file and stores nothing."""
    return load_release_module(release_directory, 'rf_status_worker').read_control_token


def deferred_token_reader(release_directory, loader=release_token_reader):
    """The release's own `read_control_token`, imported when G0 first reads a token.

    Constructing the production bundle must not import a release module before
    the release identity has been established (spec 3.2), and the token path is
    read only at `G0`, which is after that.  The callable G0 finally calls IS the
    release's own function, so the wiring stays provable; the offline harness,
    which calls `default_dependencies` directly, keeps the eager form.
    """
    state = {}

    def read_token(path):
        if 'reader' not in state:
            state['reader'] = loader(release_directory)
        return state['reader'](path)

    return read_token


def default_dependencies(release_directory=DEFAULT_RELEASE_DIRECTORY, *,
                         storage_reader=None, client_factory=None, inspector=None,
                         defer_token_reader=False):
    """The board-side production bundle: every board access, wired for real.

    The runner runs ON THE BOARD, beside the daemon it reads, so each probe reuses
    an already-reviewed piece instead of inventing a transport:

    * storage_probe  -- the local kernel storage read (boot id + dmesg).
    * identity_probe -- the release's OWN client, one read-only poll.
    * readback_probe -- rf_teaching_install_state.inspect_state, the reviewed
      independent readback, on a separate session after the study.
    * board_clock_probe -- the board's own boot id and uptime (/proc), the board
      half of the trusted clock anchor the board entry must be given (section 8).
    * token_reader   -- the release's own read_control_token.
    * worker_factory -- release_worker_factory, which DECLARES
      performs_board_read because its poll really reads the daemon.

    The three keyword seams exist so the offline fault injection can drive this
    same bundle without a board; production leaves them at their defaults.
    """
    directory = str(release_directory)
    return Dependencies(
        worker_factory=release_worker_factory,
        storage_probe=storage_reader or local_kernel_storage,
        identity_probe=lambda: live_identity(directory, client_factory),
        readback_probe=lambda expected: independent_readback(expected, inspector),
        board_clock_probe=local_board_clock,
        token_reader=(deferred_token_reader(directory) if defer_token_reader
                      else release_token_reader(directory)),
        simulation=False, hardware_touched=True)


def _stop_polling(worker, timeout_s=10.0):
    """Stop the worker's read-only poll thread before the study takes the link.

    The worker has no public stop and the runner must not build a second client,
    so the worker's own lifecycle flag is used: a read-only access to the frozen
    object, the same class of access the design document allows for
    `_connection_generation` (it changes no source and no release id).
    """
    stop = getattr(worker, '_stop', None)
    thread = getattr(worker, '_thread', None)
    if stop is not None:
        stop.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout_s)


def factory_performs_board_read(factory):
    """Whether calling this factory necessarily reads the board.

    The PRODUCTION factory starts the release worker's own read-only poll, and
    that poll opens a daemon session before the worker can attest `release_id`
    (`rf_status_worker._run` -> `_ensure_headroom`, then `_poll_once` ->
    `client.poll`).  The G4 record must therefore say `board_read=True` for it.

    An injected or simulated factory attests in-process (the fake chain calls the
    worker's own `_ensure_headroom()` directly), so it declares nothing and is
    recorded as a local attestation.  The declaration lives on the factory, so
    the record follows the implementation instead of a hard-coded assumption
    about this gate.  A partial wrapper preserves the factory's behavior, so
    inspect its wrapped callable before reading the declaration.
    """
    while isinstance(factory, functools.partial):
        factory = factory.func
    return bool(getattr(factory, 'performs_board_read', False))


def release_worker_factory(release_directory, experiment_root, client=None,
                           timeout_s=10.0, poll_interval=0.01):
    """The production shape of the worker seam.

    The worker CLASS is loaded from the installed release directory BY PATH, so
    the plan, the controller and the frame class all come from the release the
    manifest attested -- the worker loads `rf_headroom` the same way.  The
    worker's own read-only poll performs the release attestation that `G4`
    reads, and polling is stopped again before the study opens its one retained
    session, so the study is the only thing holding the link.
    """
    module = load_release_module(release_directory, 'rf_status_worker')
    worker = module.RfStatusWorker(client=client,
                                   release_directory=str(release_directory),
                                   experiment_root=str(experiment_root))
    worker.start()
    try:
        deadline = time.monotonic() + timeout_s
        while (time.monotonic() < deadline
               and worker.headroom_status().get('release_id') is None):
            time.sleep(poll_interval)
        attested = worker.headroom_status().get('release_id') is not None
    finally:
        _stop_polling(worker, timeout_s)
    if not attested:
        raise DependencyUnavailable(
            GATE_WORKER, 'release attestation',
            'the worker did not finish attesting the installed release')
    return worker


# This factory's poll IS a daemon read (read-only, after G0, before any physical
# action), so gate G4's record may not claim otherwise.  The declaration is what
# keeps `run-gate.json` truthful in the production shape while an injected
# factory is still recorded as the local attestation it really is.
release_worker_factory.performs_board_read = True


# --------------------------------------------------------------- gate records
def evidence_root_is_dedicated(evidence_root, experiment_root):
    """Research evidence never lands in (or above) the teaching record root."""
    evidence = Path(evidence_root)
    teaching = Path(experiment_root)
    if evidence.name == 'records':
        return False, 'the evidence root may not be a teaching records directory'
    if str(evidence) == str(teaching):
        return False, 'the evidence root may not be the teaching experiment root'
    try:
        common = os.path.commonpath([str(evidence), str(teaching)])
    except ValueError:
        return True, ''
    if common == str(evidence) or common == str(teaching):
        return False, ('the evidence root and the teaching experiment root must be '
                       'separate directories')
    return True, ''


def recorded_attempts(session_directory):
    """Read back what the recorder committed, attempt by attempt.

    Only committed files are read: `intent.json` (written before the request),
    `header.json` + `frame.iq` (written before any analysis), `analysis.json`,
    `replay.json` and `decision.json`.  The runner re-hashes `frame.iq` itself,
    and takes `hardware_verified` / `accepted` from the record rather than from
    the in-memory receipt.
    """
    root = Path(session_directory)
    attempts = []
    if not root.is_dir():
        return attempts
    folders = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not folder.name.startswith('attempt-'):
            continue
        try:
            number = int(folder.name.split('-', 1)[1])
        except (IndexError, ValueError):
            continue
        folders.append((number, folder))
    for number, folder in sorted(folders):
        entry = dict(attempt=number, intent=False, header=False, analysis=False,
                     replay=False, decision=None, iq_bytes=None, iq_sha256=None,
                     hardware_verified=None, terminal=None, rail_count=None,
                     declared_rail_count=None, transport=None,
                     observation_class=None, read_error=None)
        intent = folder / 'intent.json'
        entry['intent'] = intent.is_file()
        header_path = folder / 'header.json'
        if header_path.is_file():
            entry['header'] = True
            try:
                body = json.loads(header_path.read_text(encoding='utf-8'))
            except (OSError, UnicodeError, ValueError) as exc:
                entry['read_error'] = 'header.json: ' + type(exc).__name__
                body = {}
            entry['iq_available'] = body.get('iq_available')
            entry['transport'] = body.get('transport')
            native = (body.get('header') or {}).get('headroom') or {}
            entry['terminal'] = native.get('terminal')
            entry['declared_rail_count'] = native.get('rail_count')
            after = native.get('actual_rf_after') or {}
            entry['hardware_verified'] = after.get('hardware_verified')
        iq = folder / 'frame.iq'
        if iq.is_file():
            raw = iq.read_bytes()
            entry['iq_bytes'] = len(raw)
            entry['iq_sha256'] = sha256_bytes(raw)
        entry['analysis'] = (folder / 'analysis.json').is_file()
        entry['replay'] = (folder / 'replay.json').is_file()
        decision = folder / 'decision.json'
        if decision.is_file():
            try:
                entry['decision'] = json.loads(decision.read_text(encoding='utf-8'))
            except (OSError, UnicodeError, ValueError) as exc:
                entry['read_error'] = 'decision.json: ' + type(exc).__name__
        entry['observation_class'] = _observation_class(entry)
        attempts.append(entry)
    return attempts


def _observation_class(entry):
    """The one criterion for a physical observation: native attestation + accept."""
    if entry['iq_bytes'] is None:
        return None
    accepted = (entry['decision'] or {}).get('accepted') is True
    if entry['hardware_verified'] is True and accepted:
        return OBSERVATION_PHYSICAL
    return OBSERVATION_SIMULATED


def interrupted_attempts(attempts):
    """Attempts that were announced and never decided: a half-read study."""
    return [entry['attempt'] for entry in attempts
            if entry['intent'] and not isinstance(entry['decision'], dict)]


def disconnect_evidence(receipt, attempts):
    """Whether the frozen chain said the connection was gone, not merely slow.

    The strings are the frozen client's own; a deadline (`complete: false` with
    a timeout message) is deliberately NOT a disconnect.
    """
    haystack = []
    for key in ('reason', 'error', 'detail'):
        value = (receipt or {}).get(key)
        if isinstance(value, str):
            haystack.append(value)
    for entry in attempts:
        transport = entry.get('transport')
        if isinstance(transport, dict) and transport.get('error'):
            haystack.append(str(transport['error']))
    text = ' '.join(haystack).lower()
    return [marker for marker in DISCONNECT_MARKERS if marker in text]


def stopped_label(stopped_at):
    if type(stopped_at) is not str or _STOPPED_TOKEN.fullmatch(stopped_at) is None:
        return STOPPED_LABEL_PREFIX + 'UNKNOWN'
    if stopped_at == STOPPED_CANCELLED_TOKEN:
        return RUN_STOPPED_CANCELLED
    return STOPPED_LABEL_PREFIX + stopped_at


def _frame_narrative(simulated, physical, zero, attempts, receipt):
    """One sentence that matches the numbers, so a reader cannot misread them."""
    accepted = [entry['attempt'] for entry in attempts
                if (entry['decision'] or {}).get('accepted') is True]
    if simulated and physical:
        return ('%d accepted attempt(s) came from a declared simulation whose peer claimed '
                'hardware attestation; a simulated run can never count a physical frame'
                % len(physical))
    if physical:
        return ('%d new physical frame(s): attempts %s carry native hardware_verified is '
                'True AND an accepted decision'
                % (len(physical), ','.join(str(value) for value in physical)))
    if not attempts and not receipt:
        return 'no study was started, so there is no frame of any class'
    if zero.get('supported'):
        return ('0 new physical frames, and the zero claim is supported on the basis %s'
                % zero.get('basis'))
    if accepted:
        return ('%d attempt(s) were accepted but none carries native hardware attestation, '
                'so none is a physical observation' % len(accepted))
    return ('0 new physical frames, but the zero claim is NOT supported: '
            + str(zero.get('reason')))


def terminal_label(state):
    """Map everything the run observed onto exactly one runner-level label.

    Order matters and is documented: an evidence-write failure outranks
    everything (nothing else can be trusted as recorded), a post-run storage
    fault outranks the study, a post-run storage read that did NOT complete is an
    unknown outcome (never a completion), and the independent readback is what
    concludes a run that actually started -- never the research connection's own
    receipt.
    """
    if state.get('evidence_write_error'):
        return RUN_EVIDENCE_WRITE_FAILED, 'the evidence could not be written'
    post = state.get('post_storage') or {}
    if post.get('faults'):
        return RUN_ABORTED_STORAGE_FAULT, 'the post-run storage read-back reported faults'
    if post.get('preflight_status') in POST_STORAGE_UNKNOWN_STATUSES:
        return RUN_UNKNOWN_STORAGE_UNREADABLE, (
            'the post-run storage read did not complete (%s), so the board\'s storage '
            'health AND the boot-id/reboot comparison are unknown; a failed read is NOT '
            'evidence of an error-free board' % post.get('preflight_status'))
    if state.get('study_invoked') and (state.get('readback') or {}).get('status') != 'PASS':
        return RUN_UNKNOWN_READBACK_INCOMPLETE, (
            'the independent read-only safety readback did not establish the verified '
            'state (%s)' % (state.get('readback') or {}).get('status', 'NOT_ATTEMPTED'))
    reboot = state.get('reboot')
    if reboot is None:
        reboot = reboot_determination(state.get('boot_id_before'),
                                      state.get('boot_id_after'))
    if reboot == 'CHANGED':
        return RUN_UNKNOWN_REBOOT, 'the board boot id changed during the run'
    if state.get('interrupted_attempts'):
        return RUN_INTERRUPTED_MID_ATTEMPT, (
            'attempt(s) %s were announced without a decision'
            % ','.join(str(value) for value in state['interrupted_attempts']))
    if state.get('disconnect_markers'):
        return RUN_UNKNOWN_DISCONNECT, (
            'the research connection was lost (%s)'
            % state['disconnect_markers'][0])
    receipt = state.get('receipt') or {}
    if receipt.get('completed') is True:
        return RUN_COMPLETED, ''
    stopped_at = receipt.get('stopped_at')
    if stopped_at in PRE_ATTEMPT_REFUSALS:
        return RUN_REFUSED_CAPABILITY, 'the chain refused before any attempt'
    if type(stopped_at) is str:
        return stopped_label(stopped_at), 'the controller stopped at ' + stopped_at
    return (state.get('gate_label') or RUN_NOT_STARTED_PREFLIGHT), (
        state.get('gate_reason') or 'no study was started')


def reboot_determination(before, after):
    """`SAME` / `CHANGED` / `UNKNOWN` -- an unreadable boot id is never "same".

    The post-run storage read is what carries the board's `boot_id`.  When that
    read does not complete there is nothing to compare, and the honest answer is
    UNKNOWN rather than a silently disarmed "no reboot".
    """
    if before is None or after is None:
        return 'UNKNOWN'
    return 'SAME' if before == after else 'CHANGED'


def exit_code(label):
    if label == RUN_COMPLETED:
        return EXIT_COMPLETED
    if label in (RUN_NOT_STARTED_PREFLIGHT, RUN_REFUSED_CLOCK_ANCHOR,
                 RUN_REFUSED_CAPABILITY, RUN_ABORTED_STORAGE_FAULT,
                 RUN_REFUSED_BUNDLE, RUN_REFUSED_IMPORT, RUN_REFUSED_AUTHORIZATION,
                 RUN_ABORTED_PREFLIGHT_UNKNOWN):
        return EXIT_NOT_STARTED
    # Everything else -- the controller stop classes, the interrupted attempt, the
    # disconnect, the reboot, the unreadable post-run storage, the incomplete
    # readback and the evidence-write failure -- is a started run with an unknown
    # or failed outcome: non-zero, and never a completion.
    return EXIT_STOPPED


def zero_frame_claim(attempts, receipt, readback, research_session_opened):
    """The independent facts a `new_physical_frames == 0` claim needs (design 5.7).

    Four separate sources: the committed decisions, the committed frames, the
    board's own `headroom.used/active` testimony from the independent readback,
    and the study's own terminal refusal.  When no research session was opened
    at all the claim rests on the stronger fact `NO_BOARD_CONTACT`, and says so
    instead of pretending a board said something.
    """
    physical = [entry['attempt'] for entry in attempts
                if entry['observation_class'] == OBSERVATION_PHYSICAL]
    no_accepted_decision = all((entry['decision'] or {}).get('accepted') is not True
                               for entry in attempts)
    headroom = (readback or {}).get('headroom') or {}
    board_says_idle = (headroom.get('used') is False and headroom.get('active') is False)
    stopped_at = (receipt or {}).get('stopped_at')
    refusal = (stopped_at in PRE_ATTEMPT_REFUSALS) or ('stopped_at' not in (receipt or {}))
    facts = dict(
        no_accepted_decision=bool(no_accepted_decision),
        no_attested_accepted_frame=not physical,
        board_reports_never_used=bool(board_says_idle),
        study_receipt_is_a_refusal=bool(refusal),
    )
    if not research_session_opened:
        return dict(facts=facts, physical_attempts=physical, basis='NO_BOARD_CONTACT',
                    supported=True,
                    reason='no research session was opened, so no frame can exist')
    reasons = [name for name, value in facts.items() if not value]
    return dict(facts=facts, physical_attempts=physical, basis='BOARD_READBACK',
                supported=not reasons,
                reason='' if not reasons else
                       'the zero-frame claim is missing: ' + ', '.join(sorted(reasons)))


# --------------------------------------------------------------------- runner
class ResearchRun:
    """One attempt at one real 46 dB study; every gate fail-closed."""

    def __init__(self, authorization_path, token_path, wiring_statement, release_directory,
                 evidence_root, ledger_path, attempt_id, dependencies=None,
                 experiment_root=DEFAULT_EXPERIMENT_ROOT, session_id=None,
                 observation_dir=None, board_ledger_dir=None, release_verifier=None,
                 board_startup=None, host_utc=None, anchor_boot_id=None,
                 anchor_uptime_s=None):
        self.authorization_path = authorization_path
        self.token_path = token_path
        self.wiring_statement = wiring_statement
        self.release_directory = Path(release_directory)
        self.evidence_root = Path(evidence_root)
        self.experiment_root = Path(experiment_root)
        self.ledger_path = Path(ledger_path)
        self.attempt_id = attempt_id
        # `--session-id` is a claim the signed artifact must confirm; None means
        # "no claim was made" and the authorization's own id is used.
        self.session_id = session_id
        self.observation_dir = (Path(observation_dir) if observation_dir is not None
                                else None)
        # The board ledger and the release verifier are supplied by the board
        # entry's startup gates; None selects the offline harness's seam.
        self.board_ledger_dir = (Path(board_ledger_dir) if board_ledger_dir is not None
                                 else None)
        self.release_verifier = release_verifier
        self.board_startup = board_startup
        # The trusted clock anchor inputs (contract section 8).  They are raw
        # operator inputs; G0 resolves them into self.clock_anchor.
        self.host_utc = host_utc
        self.anchor_boot_id = anchor_boot_id
        self.anchor_uptime_s = anchor_uptime_s
        self.clock_anchor = None
        self.dependencies = dependencies if dependencies is not None else default_dependencies()
        self.gates = []
        self.authorization = None
        self.ledger_entry = None
        self.preflight = None
        self.post_storage = None
        self.receipt = None
        self.readback = None
        self.worker = None
        self.status = None
        self.identity = None
        self.manifest = None
        self.attestation_source = None
        self.session_directory = None
        self.attempts = []
        self.study_invoked = False
        self.study_slot_claimed = False
        self.token_present = False
        self.token_consumed_at = None
        self.connection_generation = {'before': None, 'after': None}
        self.research_owner_released = None
        self.evidence_write_error = None
        # A failed read still contacted (or may have contacted) the board.
        # Keep that fact for both explicit refusals and dependency exceptions.
        self.board_read_attempted = set()

    # -- gate bookkeeping ---------------------------------------------------
    def _gate(self, name, passed, board_read=False, **detail):
        record = dict(gate=name, passed=bool(passed), board_read=bool(board_read),
                      **detail)
        self.gates.append(record)
        return record

    def _refuse(self, label, gate, reason, **detail):
        self._gate(gate, False, board_read=gate in self.board_read_attempted,
                   reason=reason, **detail)
        raise RunnerRefused(label, gate, reason, **detail)

    # -- the authorized clock (contract section 8) -------------------------
    def clock_now(self):
        """The authorized instant, or None when this run has none at all.

        A bundle that declares real hardware and injects no clock has NO instant
        without a resolved anchor -- that refusal happens at G0 -- so this
        returns None instead of inventing one from the board clock.  Only an
        injected clock (the offline harness, the simulate subcommand) or a bundle
        that declares no hardware may read the process clock here.
        """
        if self.clock_anchor is not None:
            return self.clock_anchor.now()
        if self.dependencies.hardware_touched and self.dependencies.clock is None:
            return None
        return self.dependencies.now()

    def clock_anchor_record(self):
        """The clock record every gate document carries (contract section 8)."""
        if self.clock_anchor is not None:
            return self.clock_anchor.to_dict()
        if self.dependencies.clock is not None:
            source = 'an injected in-process clock (offline harness or simulate)'
            reason = ('this bundle injects its own clock, so no host UTC anchor was '
                      'required and no board clock is involved')
        elif self.dependencies.hardware_touched:
            source = ('NO AUTHORIZED CLOCK: this run declares real hardware and no '
                      'anchor resolved')
            reason = ('the trusted clock anchor was not established, so this document '
                      'carries no authorized instant -- and never the board clock')
        else:
            source = 'the process clock of a bundle that declares no hardware'
            reason = ('this run declares no real hardware, so no host UTC anchor was '
                      'required and no board clock is involved')
        return dict(used=False, board_clock_used=False, source=source,
                    reason=reason, host_utc=None, resolved_now=None,
                    board_boot_id=None, anchor_uptime_s=None, board_uptime_s=None,
                    age_s=None, max_age_s=CLOCK_ANCHOR_MAX_AGE_S,
                    future_tolerance_s=CLOCK_ANCHOR_FUTURE_TOLERANCE_S,
                    formula=CLOCK_ANCHOR_FORMULA)

    def authorized_now(self):
        """The authorized instant: host UTC anchored to boot_id and uptime.

        NEVER the board's RTC (that is the 2021 defect).  A run on real hardware
        -- the bundle declares hardware_touched and injects no in-process clock --
        has no clock at all without a resolved anchor, and G0 refuses by name.  An
        anchor that IS supplied always wins, even beside an injected clock, so an
        anchored session can never be judged by anything else.
        """
        if self.clock_anchor is not None:
            return self.clock_anchor.now()
        supplied = any(value is not None for value in
                       (self.host_utc, self.anchor_boot_id, self.anchor_uptime_s))
        needed = (self.dependencies.hardware_touched
                  and not self.dependencies.simulation
                  and self.dependencies.clock is None)
        if not (supplied or needed):
            return self.dependencies.now()
        probe = self.dependencies.board_clock_probe
        if probe is None:
            raise DependencyUnavailable(
                GATE_AUTHORIZATION, 'board clock anchor reader',
                'this run declares real hardware but wires in no board clock reader; '
                'the runner refuses instead of falling back to the board clock')
        self.clock_anchor = resolve_clock_anchor(self.host_utc, self.anchor_boot_id,
                                                self.anchor_uptime_s,
                                                board_clock_reader=probe)
        return self.clock_anchor.now()

    # -- G0 ----------------------------------------------------------------
    def gate_authorization(self):
        now = self.authorized_now()
        authorization = load_authorization(self.authorization_path, now)
        self.authorization = authorization
        if self.session_id is not None and self.session_id != authorization.session_id:
            self._refuse(RUN_REFUSED_AUTHORIZATION, GATE_AUTHORIZATION,
                         'the session id given on the command line is not the one in the '
                         'signed authorization', session_id=self.session_id,
                         authorized_session_id=authorization.session_id)
        if (self.observation_dir is not None
                and os.path.normcase(os.path.normpath(str(self.observation_dir)))
                == os.path.normcase(os.path.normpath(str(self.evidence_root)))):
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                         'the read-only observation receipt directory must be separate '
                         'from the research evidence root (spec 5)',
                         observation_dir=str(self.observation_dir))
        if self.wiring_statement != authorization.wiring.statement:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                         'the wiring statement typed on the command line is not the one '
                         'in the signed authorization',
                         typed_statement=self.wiring_statement,
                         authorized_statement=authorization.wiring.statement)
        if self.attempt_id is None:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                         'an explicit attempt id is required')
        staging_directory(self.attempt_id)
        token_path = Path(self.token_path)
        if os.path.realpath(str(token_path)) != os.path.realpath(authorization.token_ref):
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                         'the token path on the command line is not the one the '
                         'authorization references',
                         authorized_token_ref=authorization.token_ref)
        allowed, why = evidence_root_is_dedicated(self.evidence_root, self.experiment_root)
        if not allowed:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION, why,
                         evidence_root=str(self.evidence_root),
                         experiment_root=str(self.experiment_root))
        # The token value enters here and NOWHERE else: it is read by the
        # release's own `read_control_token`, its presence is recorded, and the
        # value is handed to nobody (the chain reads the file itself again).
        reader = self.dependencies.token_reader
        if reader is None:
            raise DependencyUnavailable(GATE_AUTHORIZATION, 'reviewed control-token reader')
        token, reason = reader(str(token_path))
        self.token_present = token is not None
        del token
        if not self.token_present:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                         'the control token is unusable: ' + str(reason),
                         token_ref=authorization.token_ref)
        self._consume_authorization(authorization, now)

    def _consume_authorization(self, authorization, now):
        """Consume the one-shot nonce in the append-only ledger.

        The BOARD entry consumes it in `BoardAuthorizationLedger`, on the board
        persistent root (spec 2/6.1); the offline harness seam consumes it in the
        reviewed host journal.  Either ledger may be unusable for entirely ordinary
        reasons: the operator typed a path whose parent does not exist, the path
        is a directory, the file is read-only, or the journal cannot be read.
        Every one of those is a REPORTED refusal (design 10: the `run` subcommand
        exits non-zero on ANY gate failure, and design 3.3: the gate fields are
        recorded), never an exception out of `run_research` and never a run with
        no `run-gate.json`.  No board read has happened at this point.  If an
        append or fsync fails, consumption may already have occurred, so its
        outcome must remain unknown and the nonce must not be reused.
        """
        try:
            ledger = (BoardAuthorizationLedger(self.board_ledger_dir)
                      if self.board_ledger_dir is not None
                      else ConsumptionLedger(self.ledger_path))
            refusal = ledger.refusal(authorization.nonce)
            if refusal:
                self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION, refusal,
                             nonce=authorization.nonce)
            fields = dict(
                authorization_id=authorization.authorization_id,
                nonce=authorization.nonce, session_id=authorization.session_id,
                expected_release=authorization.expected_release,
                runner_version=RUNNER_VERSION, attempt_id=self.attempt_id,
                artifact_sha256=authorization.artifact_sha256)
            if self.clock_anchor is not None:
                # The ledger records the AUTHORIZED instant (section 8): a
                # one-shot consumption record must not carry a 2021 RTC stamp.
                fields['utc'] = self.clock_anchor.now().isoformat()
                fields['clock_anchor'] = self.clock_anchor_record()
            self.ledger_entry = ledger.consume(**fields)
        except RunnerRefused:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_AUTHORIZATION,
                         'the one-shot consumption ledger is unusable (%s): %s; '
                         'consumption outcome is unknown; never reuse this nonce; '
                         'no board read was attempted'
                         % (type(exc).__name__, exc),
                         ledger_path=str(self.ledger_path),
                         error_type=type(exc).__name__,
                         consumption_status='UNKNOWN')
        self._gate(GATE_AUTHORIZATION, True, board_read=False,
                   authorization_id=authorization.authorization_id,
                   nonce=authorization.nonce, session_id=authorization.session_id,
                   authorized_attempts=authorization.authorized_attempts,
                   token_present=True, token_ref=authorization.token_ref,
                   ledger_path=str(self.ledger_path),
                   ledger_seq=self.ledger_entry.get('seq'),
                   wiring=authorization.wiring.to_dict(now),
                   clock_anchor=self.clock_anchor_record(),
                   artifact_sha256=authorization.artifact_sha256,
                   staging_prefix=RESEARCH_STAGING_PREFIX,
                   staging_directory=staging_directory(self.attempt_id))

    # -- G1 ----------------------------------------------------------------
    def gate_storage_preflight(self):
        probe = self.dependencies.storage_probe
        if probe is None:
            raise DependencyUnavailable(GATE_STORAGE, 'storage preflight probe')
        self.board_read_attempted.add(GATE_STORAGE)
        try:
            snapshot = probe()
        except DependencyUnavailable:
            raise
        except Exception as exc:                  # noqa: BLE001 - a failed read is not "clean"
            self.preflight = dict(boot_id=None, faults=[],
                                  preflight_status='UNREADABLE',
                                  error_type=type(exc).__name__)
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_STORAGE,
                         'the kernel storage preflight could not be read; a failed read is '
                         'NOT evidence of an error-free board',
                         preflight_status='UNREADABLE', error_type=type(exc).__name__,
                         error=str(exc))
        if type(snapshot) is not dict or snapshot.get('complete') is not True:
            self.preflight = dict(boot_id=(snapshot or {}).get('boot_id')
                                  if type(snapshot) is dict else None,
                                  faults=[], preflight_status='INCOMPLETE',
                                  kernel_fault_keys=list(kernel_fault_keys()))
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_STORAGE,
                         'the kernel storage preflight did not complete; a failed read is '
                         'NOT evidence of an error-free board',
                         preflight_status='INCOMPLETE',
                         boot_id=(snapshot or {}).get('boot_id') if type(snapshot) is dict
                         else None)
        text = snapshot.get('kernel_storage')
        if type(text) is not str:
            self.preflight = dict(boot_id=snapshot.get('boot_id'), faults=[],
                                  preflight_status='UNREADABLE')
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_STORAGE,
                         'the kernel storage preflight returned no kernel text',
                         preflight_status='UNREADABLE')
        faults = storage_faults(text)
        self.preflight = dict(boot_id=snapshot.get('boot_id'), faults=faults,
                              preflight_status='FAULTS_DETECTED' if faults else 'CLEAN',
                              kernel_fault_keys=list(kernel_fault_keys()),
                              kernel_storage_bytes=len(text))
        if faults:
            self._refuse(RUN_ABORTED_STORAGE_FAULT, GATE_STORAGE,
                         'the board reports kernel storage faults; the run is aborted with '
                         'zero board writes', faults=faults,
                         boot_id=snapshot.get('boot_id'))
        self._gate(GATE_STORAGE, True, board_read=True, **self.preflight)

    # -- G2 ----------------------------------------------------------------
    def gate_release_attestation(self):
        verifier = self.release_verifier
        source = 'the release\'s own merged_release.py, loaded BY PATH'
        try:
            if verifier is None:
                verifier, source = release_verifier_for(self.release_directory)
            manifest = verifier.verify_release(self.release_directory,
                                               require_software=True)
        except Exception as exc:                  # noqa: BLE001 - refused, not raised
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_ATTESTATION,
                         'the installed release does not attest its software: '
                         + (str(exc) or type(exc).__name__),
                         release_directory=str(self.release_directory))
        self.attestation_source = source
        if manifest.get('software_contract') != SOFTWARE_CONTRACT:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_ATTESTATION,
                         'the installed release carries another software contract',
                         software_contract=manifest.get('software_contract'),
                         expected_contract=SOFTWARE_CONTRACT)
        software = manifest.get('software') or {}
        for name, frozen in sorted(FROZEN_RESEARCH_MODULES.items()):
            if software.get(name) != frozen:
                self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_ATTESTATION,
                             'the installed research module is not the frozen one: ' + name,
                             module=name, installed=software.get(name), frozen=frozen)
        self.manifest = manifest
        self._gate(GATE_ATTESTATION, True, board_read=False,
                   release_directory=str(self.release_directory),
                   installed_release_id=manifest.get('release_id'),
                   software_contract=manifest.get('software_contract'),
                   daemon_version=manifest.get('daemon_version'),
                   frozen_module_sha256=dict(FROZEN_RESEARCH_MODULES),
                   software_module_count=len(software),
                   attestation_source=getattr(self, 'attestation_source', None))

    # -- G3 ----------------------------------------------------------------
    def gate_release_identity(self):
        installed = self.manifest.get('release_id')
        if installed != self.authorization.expected_release:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_IDENTITY,
                         'the installed release is not the release this run is authorized '
                         'for; finish the independent review, ARM G1 and safe installation '
                         'of the qualified candidate first',
                         installed_release=installed,
                         expected_release=self.authorization.expected_release)
        probe = self.dependencies.identity_probe
        if probe is None:
            raise DependencyUnavailable(GATE_IDENTITY, 'live release-identity probe')
        self.board_read_attempted.add(GATE_IDENTITY)
        try:
            identity = probe()
        except DependencyUnavailable:
            raise
        except Exception as exc:                  # noqa: BLE001
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_IDENTITY,
                         'the live release identity could not be read: '
                         + (str(exc) or type(exc).__name__))
        if type(identity) is not dict:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_IDENTITY,
                         'the live release identity read returned nothing usable')
        self.identity = dict(identity)
        expected = dict(release_id=self.authorization.expected_release,
                        daemon_version=self.authorization.daemon_version,
                        protocol_major=PROTOCOL_MAJOR,
                        protocol_minor=self.authorization.protocol_minor)
        mismatch = {key: self.identity.get(key) for key, value in expected.items()
                    if self.identity.get(key) != value}
        if mismatch:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_IDENTITY,
                         'the live daemon is not the authorized release/daemon/protocol; '
                         'the runner refuses before any physical action',
                         live_identity=self.identity, expected=expected,
                         mismatch=sorted(mismatch))
        self._gate(GATE_IDENTITY, True, board_read=True,
                   installed_release_id=installed, expected_release=expected['release_id'],
                   live_identity=self.identity,
                   daemon_version=expected['daemon_version'],
                   protocol_major=expected['protocol_major'],
                   protocol_minor=expected['protocol_minor'])

    # -- G4 ----------------------------------------------------------------
    def gate_worker_attestation(self):
        factory = self.dependencies.worker_factory
        if factory is None:
            raise DependencyUnavailable(GATE_WORKER, 'worker/client factory')
        if factory_performs_board_read(factory):
            self.board_read_attempted.add(GATE_WORKER)
        worker = factory(release_directory=str(self.release_directory),
                         experiment_root=str(self.experiment_root))
        if worker is None:
            raise DependencyUnavailable(GATE_WORKER, 'worker/client factory')
        self.worker = worker
        try:
            status = worker.headroom_status()
        except Exception as exc:                  # noqa: BLE001
            self._refuse(RUN_REFUSED_CAPABILITY, GATE_WORKER,
                         'the release-side research attestation could not be read: '
                         + (str(exc) or type(exc).__name__))
        self.status = dict(status or {})
        if self.status.get('available') is not True:
            self._refuse(RUN_REFUSED_CAPABILITY, GATE_WORKER,
                         'the release-side research module is not available: '
                         + str(self.status.get('reason')),
                         headroom_status=self.status)
        if self.status.get('release_id') != self.authorization.expected_release:
            self._refuse(RUN_NOT_STARTED_PREFLIGHT, GATE_WORKER,
                         'the worker attested another release than the one authorized',
                         worker_release_id=self.status.get('release_id'),
                         expected_release=self.authorization.expected_release)
        reads_board = factory_performs_board_read(factory)
        self._gate(GATE_WORKER, True, board_read=reads_board,
                   available=True, release_id=self.status.get('release_id'),
                   module_sha256=self.status.get('module_sha256'),
                   reason=self.status.get('reason'),
                   attestation_source=(
                       "the worker's own read-only daemon poll "
                       '(rf_status_worker._run -> _ensure_headroom -> client.poll)'
                       if reads_board else
                       'an injected factory that attested without a declared board read'))

    # -- G5-G8: the single synchronous execution entry ---------------------
    def execute_study(self):
        """Call the ONE execution entry exactly once, synchronously.

        `HeadroomController.run` is what acquires the research lease, decides
        capability, arms, and sends `headroom.begin` as its first physical
        action.  The runner neither re-implements nor splits any of that: it
        makes one call and reads the receipt it returns.
        """
        if not self.study_slot_claimed:
            raise StudySlotTaken()
        cancelled = self.dependencies.cancelled
        # A physical run demands native hardware attestation.  Only a declared
        # simulation (fake chain, `simulation=True` AND `hardware_touched=False`)
        # is allowed to relax it, and the value used is recorded.
        require_hardware = not bool(self.dependencies.simulation)
        client = getattr(self.worker, 'client', None)
        self.connection_generation['before'] = getattr(client, '_connection_generation', None)
        self.study_invoked = True
        try:
            receipt = self.worker.headroom_run(
                self.authorization.session_id, cable_confirmed=True,
                evidence_root=str(self.evidence_root), token_path=str(self.token_path),
                cancelled=cancelled, require_hardware=require_hardware)
        except Exception as exc:                  # noqa: BLE001 - reported, never retried
            self.evidence_write_error = (getattr(exc, 'evidence_write_error', None)
                                        or self.evidence_write_error)
            self.receipt = dict(completed=False, stopped_at='CRASHED',
                                reason=str(exc) or type(exc).__name__, attempts=0,
                                frames=[], disarmed=False, close_ack=None,
                                error_type=type(exc).__name__)
        else:
            self.receipt = receipt if type(receipt) is dict else dict(
                completed=False, stopped_at='CRASHED', attempts=0, frames=[],
                reason='the execution entry returned no receipt')
        finally:
            instant = self.clock_now()
            # Never a board-clock stamp: an anchored session stamps the anchor,
            # and a run with no authorized instant records none (section 8).
            self.token_consumed_at = (instant.isoformat() if instant is not None
                                      else None)
            self.connection_generation['after'] = getattr(client, '_connection_generation', None)
            self.research_owner_released = (None if client is None
                                           else getattr(client, '_research_owner', None) is None)
        if self.receipt.get('evidence_write_error'):
            self.evidence_write_error = self.receipt['evidence_write_error']
        self.session_directory = self.evidence_root / self.authorization.session_id
        self.attempts = recorded_attempts(self.session_directory)
        self._gate(GATE_CHAIN, self.receipt.get('completed') is True,
                   board_read=True, entry='RfStatusWorker.headroom_run',
                   calls=1, synchronous=True,
                   lease_capability_arm_begin='delegated to HeadroomController.run',
                   session_id=self.authorization.session_id,
                   stopped_at=self.receipt.get('stopped_at'),
                   attempts=self.receipt.get('attempts'),
                   frames=len(self.receipt.get('frames') or []),
                   evidence_root=str(self.evidence_root),
                   require_hardware=require_hardware,
                   connection_generation=dict(self.connection_generation))

    # -- post-run observation ----------------------------------------------
    def postflight(self):
        """Post-run storage read and the SEPARATE independent readback.

        The post-run storage read obeys the same rule as the pre-run one: a read
        that raised, did not complete, or came back "complete" without any kernel
        text is NOT an error-free board.  Its faults
        are recorded as unknown (`faults=None`, `faults_known=False`) instead of
        as an empty list, and `terminal_label` turns that into
        `RUN_UNKNOWN_STORAGE_UNREADABLE` -- so this path can never end as
        `RUN_COMPLETED`, and the boot-id comparison it carries is reported as
        UNKNOWN rather than silently skipped.
        """
        probe = self.dependencies.storage_probe
        if probe is not None:
            try:
                snapshot = probe()
            except Exception as exc:              # noqa: BLE001 - a failed read is not "clean"
                self.post_storage = dict(boot_id=None, faults=None, faults_known=False,
                                         preflight_status='UNREADABLE',
                                         error_type=type(exc).__name__, error=str(exc))
            else:
                if type(snapshot) is dict and snapshot.get('complete') is True:
                    text = snapshot.get('kernel_storage')
                    # A "complete" read that carries no usable text is the same
                    # shape the PRE-run read already refuses ("returned no kernel
                    # text"), so the post-run read must not call it clean: with an
                    # empty fault list it would report an error-free board, and a
                    # non-string text would type-error out of run_research.
                    if type(text) is not str:
                        self.post_storage = dict(
                            boot_id=snapshot.get('boot_id'), faults=None,
                            faults_known=False, preflight_status='UNREADABLE',
                            error_type='MalformedStorageSnapshot',
                            error='the post-run storage read returned complete '
                                  'without kernel text')
                    else:
                        faults = storage_faults(text)
                        self.post_storage = dict(
                            boot_id=snapshot.get('boot_id'), faults=faults,
                            faults_known=True,
                            preflight_status='FAULTS_DETECTED' if faults else 'CLEAN')
                else:
                    self.post_storage = dict(
                        boot_id=None, faults=None, faults_known=False,
                        preflight_status=('INCOMPLETE' if type(snapshot) is dict
                                          else 'UNREADABLE'))
        if not self.study_invoked:
            self.readback = dict(status='NOT_ATTEMPTED',
                                 reason='no study started, so no board session was opened')
            return
        readback_probe = self.dependencies.readback_probe
        if readback_probe is None:
            self.readback = dict(status='UNAVAILABLE',
                                 reason='no independent read-only readback is wired in')
            return
        try:
            raw = readback_probe(self.authorization.expected_release)
        except Exception as exc:                  # noqa: BLE001
            self.readback = dict(status='UNAVAILABLE', error_type=type(exc).__name__,
                                 error=str(exc))
            return
        if type(raw) is not dict or type(raw.get('state')) is not dict:
            self.readback = dict(status='UNVERIFIED',
                                 reason='the readback returned nothing usable')
            return
        try:
            # The checker must be importable from the RUN PACKAGE (B1): a missing
            # or unimportable checker is a REPORTED UNVERIFIED readback, never an
            # exception that escapes without a gate document (B2).
            from scripts.rf_teaching_install_state import verified_state
            verified = verified_state(raw['state'],
                                      self.authorization.expected_release) is True
        except Exception as exc:                  # noqa: BLE001
            self.readback = dict(status='UNVERIFIED', error_type=type(exc).__name__,
                                 error=str(exc), state=raw['state'])
            return
        headroom = raw.get('headroom') if type(raw.get('headroom')) is dict else {}
        self.readback = dict(
            status='PASS' if verified else 'UNVERIFIED',
            verified_state=verified, release_id=raw['state'].get('release_id'),
            active_release=raw['state'].get('active_release'),
            safe=raw['state'].get('safe'),
            suspected_research_used=headroom.get('used'),
            headroom=dict(used=headroom.get('used'), active=headroom.get('active'),
                          operations=headroom.get('operations')),
            state=raw['state'], source='separate read-only session (not the research '
                                       'connection)')

    # -- report -------------------------------------------------------------
    def _generation_delta(self):
        before = self.connection_generation['before']
        after = self.connection_generation['after']
        if before is None or after is None or type(before) is not int or type(after) is not int:
            return None
        return after - before

    def _single_connection(self):
        delta = self._generation_delta()
        if delta is None:
            return None
        return 0 <= delta <= 1

    def _reboot_determination(self):
        """`SAME` / `CHANGED` / `UNKNOWN`, recorded rather than left implicit."""
        return reboot_determination((self.preflight or {}).get('boot_id'),
                                    (self.post_storage or {}).get('boot_id'))

    def build_report(self, label, reason):
        receipt = self.receipt or {}
        attempts = self.attempts
        physical = [entry['attempt'] for entry in attempts
                    if entry['observation_class'] == OBSERVATION_PHYSICAL]
        simulated = bool(self.dependencies.simulation)
        reported_physical = 0 if simulated else len(physical)
        zero = zero_frame_claim(attempts, receipt if self.study_invoked else {},
                                self.readback, self.study_invoked)
        replays = [dict(attempt=entry['attempt'], observation_class=OBSERVATION_REPLAY,
                        files=dict(replay=True, analysis=entry['analysis']))
                   for entry in attempts if entry['replay']]
        report = dict(
            schema='headroom-runner-gate-v1',
            runner_version=RUNNER_VERSION,
            runner_sha256=sha256_file(__file__),
            terminal=label, retry_allowed=False, reason=reason,
            exit_code=exit_code(label),
            simulation=bool(self.dependencies.simulation),
            hardware_touched=bool(self.dependencies.hardware_touched),
            study_invoked=self.study_invoked,
            gates=list(self.gates),
            gate_order=list(GATE_ORDER),
            token_present=bool(self.token_present),
            token_consumed_at=self.token_consumed_at,
            token_ref=(self.authorization.token_ref if self.authorization else None),
            attempt_id=self.attempt_id,
            staging_prefix=RESEARCH_STAGING_PREFIX,
            staging_directory=(staging_directory(self.attempt_id)
                               if self.attempt_id else None),
            ledger_path=str(self.ledger_path),
            ledger_seq=(self.ledger_entry or {}).get('seq'),
            evidence_root=str(self.evidence_root),
            experiment_root=str(self.experiment_root),
            session_directory=(str(self.session_directory)
                               if self.session_directory is not None else None),
            release=dict(
                release_directory=str(self.release_directory),
                installed_release_id=(self.manifest or {}).get('release_id'),
                software_contract=(self.manifest or {}).get('software_contract'),
                frozen_module_sha256=dict(FROZEN_RESEARCH_MODULES),
                live_identity=self.identity,
                connection_generation=dict(self.connection_generation),
                connection_generation_delta=self._generation_delta(),
                # A LOCAL HEURISTIC, not single-socket proof: the connection
                # generation may advance by at most one across the study (the
                # lease opens the first connection, and the frozen client refuses
                # any later one while the research owner is set: 'research
                # connection was lost; no reconnect').  A production factory's
                # poll opens its own connection BEFORE the study, so
                # `before=1, after=2` reads True while the process really opened
                # two connections.  Enforcement stays with the frozen client's
                # `_research_owned`; the runner implements no second predicate.
                single_connection_no_reconnect=self._single_connection(),
                research_owner_released=self.research_owner_released),
            storage=dict(preflight=self.preflight, postrun=self.post_storage,
                         reboot_determination=self._reboot_determination()),
            study=dict(receipt={key: value for key, value in receipt.items()
                                if key != 'frames'},
                       frame_count=len(receipt.get('frames') or []),
                       completed=receipt.get('completed'),
                       stopped_at=receipt.get('stopped_at'),
                       attempts=receipt.get('attempts'),
                       disarmed=receipt.get('disarmed')),
            attempts=attempts,
            replays=replays,
            observation_classes=dict(
                new_physical_frames=reported_physical,
                physical_attempts=physical,
                physical_suppressed_by_simulation=(len(physical) if simulated else 0),
                replayed_same_iq=len(replays),
                simulation=simulated,
                criterion='native hardware_verified is True AND accepted is True',
                narrative=_frame_narrative(simulated, physical, zero, attempts,
                                           receipt if self.study_invoked else {})),
            zero_frame_claim=zero,
            readback=self.readback,
            evidence_write_error=self.evidence_write_error,
            not_run=list(NOT_RUN_BOARD) + [NOT_RUN_DEFAULT],
        )
        report['observation_dir'] = (str(self.observation_dir)
                                     if self.observation_dir is not None else None)
        # The session's clock, recorded rather than implied (contract section 8):
        # a run with no anchor says used=False and carries no invented stamp.
        report['clock_anchor'] = self.clock_anchor_record()
        instant = self.clock_now()
        report['generated_at'] = instant.isoformat() if instant is not None else None
        if self.board_startup is not None:
            report['board_startup'] = dict(self.board_startup)
        if self.authorization is not None:
            report['authorization'] = self.authorization.to_dict()
            report['wiring'] = self.authorization.wiring.to_dict(instant)
        return report

    # -- gate document ------------------------------------------------------
    def gate_document_path(self):
        """`run-gate.json`, beside the recorder's `receipt.json` when it exists.

        A refusal that never reached the recorder must NOT create the session
        directory (that would make a refusal look like a study record), so it
        goes to a runner-owned subdirectory instead.  Neither location is the
        teaching records directory.
        """
        session = self.evidence_root / (self.authorization.session_id
                                       if self.authorization else self.attempt_id)
        if session.is_dir():
            return session / GATE_DOCUMENT_NAME
        return self.evidence_root / GATE_REFUSAL_DIRECTORY / (str(self.attempt_id)
                                                             + '.json')

    def write_gate_document(self, report):
        try:
            path = self.gate_document_path()
            report['gate_document'] = str(path)
            digest = atomic_write_json(path, report)
            report['gate_document_sha256'] = digest
            return path
        except Exception as exc:                  # noqa: BLE001 - recorded, not raised
            self.evidence_write_error = '%s: %s' % (type(exc).__name__, exc)
            report['evidence_write_error'] = self.evidence_write_error
            report['terminal'] = RUN_EVIDENCE_WRITE_FAILED
            report['retry_allowed'] = False
            report['exit_code'] = exit_code(RUN_EVIDENCE_WRITE_FAILED)
            report['reason'] = 'the gate document could not be written'
            return None

    # -- the ordered run ----------------------------------------------------
    def refusal_report(self, refusal, not_run=None):
        """One gate refusal: a reported outcome, never a study that ran."""
        if refusal.gate == GATE_STORAGE and 'faults' in refusal.detail:
            self.preflight = dict(self.preflight or {},
                                  **{key: refusal.detail[key]
                                     for key in ('faults', 'boot_id')
                                     if key in refusal.detail})
        if not any(record['gate'] == refusal.gate for record in self.gates):
            # A refusal raised while validating an artifact never reached
            # `_refuse`, and an unrecorded gate is a gate nobody can audit.
            self._gate(refusal.gate, False,
                       board_read=refusal.gate in self.board_read_attempted,
                       reason=refusal.reason, **refusal.detail)
        self.readback = dict(status='NOT_ATTEMPTED',
                             reason='no study started, so no board session was opened')
        state = dict(gate_label=refusal.label, gate_reason=refusal.reason,
                     study_invoked=False, receipt={}, readback=self.readback,
                     post_storage=None, evidence_write_error=None)
        resolved, reason = terminal_label(state)
        report = self.build_report(resolved, reason or refusal.reason)
        report['refusal'] = dict(label=refusal.label, gate=refusal.gate,
                                 reason=refusal.reason, detail=refusal.detail)
        report['not_run'] = (list(not_run) if not_run is not None
                             else ['the study never started: ' + refusal.reason,
                                   NOT_RUN_DEFAULT])
        self.write_gate_document(report)
        return report

    def execute(self):
        # ONE study per process, claimed BEFORE any gate: a process that has
        # already attempted a study must not read the board again, whatever
        # arguments it is handed the second time.  A fresh attempt needs a fresh
        # single-use process.
        if not self.dependencies.claimed():
            return self.refusal_report(
                StudySlotTaken(),
                not_run=['the study never started: this process already claimed its '
                         'single study slot', NOT_RUN_DEFAULT])
        self.study_slot_claimed = True
        try:
            self.gate_authorization()
            self.gate_storage_preflight()
            self.gate_release_attestation()
            self.gate_release_identity()
            self.gate_worker_attestation()
        except RunnerRefused as refusal:
            return self.refusal_report(refusal)
        try:
            self.execute_study()
        except RunnerRefused as refusal:
            return self.refusal_report(refusal)
        except Exception as exc:                  # noqa: BLE001 - reported, not raised
            # An unmodelled failure around the execution entry: the study is
            # NOT reported as completed, and the original error is preserved.
            self.receipt = dict(completed=False, stopped_at='CRASHED',
                                reason='%s: %s' % (type(exc).__name__, exc),
                                attempts=0, frames=[], error_type=type(exc).__name__,
                                unmodelled=True)
        self.postflight()
        boot_before = (self.preflight or {}).get('boot_id')
        boot_after = (self.post_storage or {}).get('boot_id')
        state = dict(
            receipt=self.receipt or {},
            study_invoked=self.study_invoked,
            readback=self.readback or {},
            post_storage=self.post_storage or {},
            boot_id_before=boot_before,
            boot_id_after=boot_after,
            reboot=reboot_determination(boot_before, boot_after),
            interrupted_attempts=interrupted_attempts(self.attempts),
            disconnect_markers=disconnect_evidence(self.receipt or {}, self.attempts),
            evidence_write_error=self.evidence_write_error,
            gate_label=RUN_NOT_STARTED_PREFLIGHT, gate_reason='')
        label, reason = terminal_label(state)
        report = self.build_report(label, reason)
        self.write_gate_document(report)
        return report


def run_research(authorization_path, token_path, wiring_statement, release_directory,
                 evidence_root, ledger_path, attempt_id, dependencies=None,
                 experiment_root=DEFAULT_EXPERIMENT_ROOT, session_id=None,
                 observation_dir=None, board_ledger_dir=None, release_verifier=None,
                 board_startup=None, host_utc=None, anchor_boot_id=None,
                 anchor_uptime_s=None):
    """Run one research session; returns the gate document as a dict.

    Never raises for an expected refusal: a refusal is a reported outcome with a
    terminal label and `retry_allowed=False`.  `board_ledger_dir`,
    `release_verifier` and `board_startup` are the board entry's startup results;
    the offline harness leaves them at None and keeps the host seams.
    `host_utc`, `anchor_boot_id` and `anchor_uptime_s` are the session's trusted
    clock anchor (contract section 8).  A bundle that declares real hardware needs
    all three: without them G0 refuses by name (`RUN_REFUSED_CLOCK_ANCHOR`)
    instead of judging the window with the board's RTC.
    """
    run = ResearchRun(authorization_path=authorization_path, token_path=token_path,
                      wiring_statement=wiring_statement,
                      release_directory=release_directory, evidence_root=evidence_root,
                      ledger_path=ledger_path, attempt_id=attempt_id,
                      dependencies=dependencies, experiment_root=experiment_root,
                      session_id=session_id, observation_dir=observation_dir,
                      board_ledger_dir=board_ledger_dir,
                      release_verifier=release_verifier, board_startup=board_startup,
                      host_utc=host_utc, anchor_boot_id=anchor_boot_id,
                      anchor_uptime_s=anchor_uptime_s)
    return run.execute()


# ------------------------------------------------------- board entry startup
def identify_release_identity(release_directory):
    """spec 3.2, first half: WHICH release is installed, from its OWN documents.

    This reads the release's identity documents and deliberately imports nothing
    from it: no release module may be imported before the identity is established
    ("发布身份先于导入核验").  The FULL attestation call --
    `merged_release.verify_release(dir, require_software=True)` plus the contract
    and the frozen research-module digests -- still runs, unchanged, in
    `G2-RELEASE-ATTESTATION` through the release's own verifier, before any
    research module is loaded.  What this function adds is the earlier refusal
    (`RUN_REFUSED_IMPORT`) and the proof that the file the by-path attestation
    needs is a regular file in the release.
    """
    directory = Path(os.path.realpath(str(release_directory)))
    if not directory.is_dir():
        raise ValueError('the release directory does not exist: ' + str(directory))
    verifier_file = directory / 'merged_release.py'
    if verifier_file.is_symlink() or not verifier_file.is_file():
        raise ValueError('the release carries no merged_release.py to attest it')
    manifest_path = directory / 'merged_release_manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('the release carries no merged_release_manifest.json')
    try:
        manifest = json.loads(manifest_path.read_bytes().decode('utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError('the release manifest is unreadable (%s)' % type(exc).__name__)
    if type(manifest) is not dict:
        raise ValueError('the release manifest is not a JSON object')
    if manifest.get('release_id') != QUALIFIED_RELEASE:
        raise ValueError('the release is not the qualified candidate: %r'
                         % manifest.get('release_id'))
    if manifest.get('software_contract') != SOFTWARE_CONTRACT:
        raise ValueError('the release carries another software contract: %r'
                         % manifest.get('software_contract'))
    software = manifest.get('software') or {}
    for module_name, frozen in sorted(FROZEN_RESEARCH_MODULES.items()):
        if software.get(module_name) != frozen:
            raise ValueError('the release does not declare the frozen research module: '
                             + module_name)
    return dict(release_directory=str(directory), manifest=manifest)

def dispatch_marker_clock(args):
    """The dispatch marker's own time: the ANCHOR when it resolves, never the RTC.

    The startup gates write the marker BEFORE G0 decides whether this session may
    run, so the anchor is resolved here independently and ONLY for this record:
    when it does not resolve, the marker carries NO time (`utc: null`) and names
    why.  A 2021 RTC stamp would be a false record, and the missing anchor is
    refused by name at G0 in any case (contract section 8).
    """
    try:
        anchor = resolve_clock_anchor(getattr(args, 'host_utc', None),
                                      getattr(args, 'anchor_boot_id', None),
                                      getattr(args, 'anchor_uptime_s', None))
    except RunnerRefused as refused:
        return dict(utc=None, clock_source='UNRESOLVED: ' + refused.reason)
    except Exception as exc:                      # noqa: BLE001 - recorded, not raised
        return dict(utc=None, clock_source='UNRESOLVED: %s: %s'
                    % (type(exc).__name__, exc))
    return dict(utc=anchor.now().isoformat(), clock_source=CLOCK_ANCHOR_SOURCE,
                host_utc=anchor.host_utc.isoformat(), age_s=anchor.age_s,
                board_boot_id=anchor.boot_id,
                max_age_s=CLOCK_ANCHOR_MAX_AGE_S,
                future_tolerance_s=CLOCK_ANCHOR_FUTURE_TOLERANCE_S)


def _write_dispatch_marker(dispatch_dir, attempt_id, receipt, failures, conclusion,
                           clock=None):
    """spec 4.3: one dispatch record per attempt, created with `O_EXCL`.

    Returns `(marker_path, error)`: an existing marker (a replay, or a race won by
    another process) is an error value, never an overwrite.  `utc` is the
    AUTHORIZED instant when the clock anchor resolved, and null (with
    `clock_source` naming the reason) when it did not: this record never carries
    the board's own RTC time (contract section 8).
    """
    document = dict(
        attempt_id=attempt_id,
        members=len(BUNDLE_MEMBERS),
        failures=list(failures),
        conclusion=conclusion,
        utc=(clock or {}).get('utc'),
        clock_source=(clock or {}).get('clock_source', 'UNRESOLVED: no clock record'),
    )
    if receipt is not None:
        document['runner_version'] = receipt.get('runner_version')
        document['source_commit'] = receipt.get('source_commit')
        document['builder'] = receipt.get('builder')
    try:
        return create_dispatch_marker(dispatch_dir, attempt_id, document), None
    except Exception as exc:                      # noqa: BLE001 - reported, not raised
        return None, '%s: %s' % (type(exc).__name__, exc)


def _board_startup_report(label, gate, reason, args, detail=None, failures=None,
                          marker=None, marker_error=None):
    """The board entry's own zero-action refusal document (spec 4.3/7).

    It carries the same shape as every other runner gate document, so an operator
    (and the offline suite) reads one schema; `study_invoked` is False and the
    frame class is zero because nothing was attempted.
    """
    evidence_root = Path(args.evidence_root)
    attempt_id = args.attempt_id if type(args.attempt_id) is str else 'UNKNOWN-ATTEMPT'
    report = dict(
        schema='headroom-runner-gate-v1',
        runner_version=RUNNER_VERSION,
        runner_sha256=sha256_file(__file__),
        terminal=label, retry_allowed=False, reason=reason,
        exit_code=exit_code(label),
        simulation=False, hardware_touched=False, study_invoked=False,
        gates=[dict(gate=gate, passed=False, board_read=False, reason=reason,
                    **dict(detail or {}))],
        gate_order=list(BOARD_STARTUP_GATE_ORDER),
        board_startup=dict(stage='BOARD-RUN-BUNDLE', passed=False, gate=gate,
                           failures=list(failures or []), dispatch_marker=marker,
                           dispatch_marker_error=marker_error),
        token_present=False, token_consumed_at=None, token_ref=None,
        generated_at=None,
        clock_anchor=dict(
            used=False, board_clock_used=False,
            source='not resolved: the board startup gate refused before G0',
            reason=('the board startup gate refused before the clock anchor gate, so '
                    'this document carries no authorized instant -- and never the '
                    'board clock')),
        attempt_id=args.attempt_id,
        staging_prefix=RESEARCH_STAGING_PREFIX, staging_directory=None,
        ledger_path=str(Path(args.ledger_dir) / LEDGER_FILE_NAME), ledger_seq=None,
        evidence_root=str(evidence_root),
        experiment_root=str(args.experiment_root), session_directory=None,
        observation_dir=(str(args.observation_dir)
                         if getattr(args, 'observation_dir', None) is not None else None),
        release=dict(release_directory=str(args.release_directory)),
        storage=dict(preflight=None, postrun=None, reboot_determination='UNKNOWN'),
        study=dict(receipt={}, frame_count=0, completed=None, stopped_at=None,
                   attempts=None, disarmed=None),
        attempts=[], replays=[],
        observation_classes=dict(
            new_physical_frames=0, physical_attempts=[],
            physical_suppressed_by_simulation=0, replayed_same_iq=0, simulation=False,
            criterion='native hardware_verified is True AND accepted is True',
            narrative='no study was started, so there is no frame of any class'),
        zero_frame_claim=dict(
            facts=dict(no_accepted_decision=True, no_attested_accepted_frame=True,
                       board_reports_never_used=None, study_receipt_is_a_refusal=True),
            physical_attempts=[], basis='NO_BOARD_CONTACT', supported=True,
            reason='no research session was opened, so no frame can exist'),
        readback=dict(status='NOT_ATTEMPTED',
                      reason='no study started, so no board session was opened'),
        evidence_write_error=None,
        not_run=list(NOT_RUN_BOARD) + [NOT_RUN_DEFAULT],
    )
    gate_path = evidence_root / GATE_REFUSAL_DIRECTORY / (attempt_id + '.json')
    try:
        report['gate_document'] = str(gate_path)
        report['gate_document_sha256'] = atomic_write_json(gate_path, report)
    except Exception as exc:                      # noqa: BLE001 - recorded, not raised
        report['gate_document'] = None
        report['evidence_write_error'] = '%s: %s' % (type(exc).__name__, exc)
    return report


def board_startup_gates(args, board_entry):
    """spec 3/4: the board entry's startup gates, in the spec's fixed order.

    Returns `dict(record, refusal, release_verifier)`.  A process that is NOT the
    board entry and has no uploaded bundle to re-verify (the offline harness, the
    `simulate` subcommand, the test suite) gets `(None, None, None)`: it writes no
    state root and touches no board.  The board entry ALWAYS re-verifies the
    bundle first (spec 4) -- with no receipt at all it refuses instead of skipping
    the gate -- then identifies the release (spec 3.2) before any of its modules
    may be imported, materialises the controlled search path (spec 3.1) and checks
    that the bundle's checker is importable (spec 3.3).

    The dispatch marker is created here with `O_EXCL` and carries the verification
    result (spec 4.3); the ledger is NOT touched, so a refusal consumes nothing.
    """
    receipt_path = ROOT / BUNDLE_RECEIPT_NAME
    dispatch_dir = Path(args.ledger_dir).parent / DISPATCH_DIRECTORY_NAME
    if not receipt_path.is_file():
        if not board_entry:
            return dict(record=None, refusal=None, release_verifier=None)
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_BUNDLE, BOARD_STARTUP_GATE,
            'this process IS the board entry, but there is no run bundle receipt at '
            '%s: the uploaded bundle is re-verified before any hardware action, and a '
            'missing receipt is refused, never skipped' % receipt_path, args))
    if type(args.attempt_id) is not str or _HEX32.fullmatch(args.attempt_id) is None:
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_AUTHORIZATION, BOARD_STARTUP_GATE,
            'attempt_id must be 32 lowercase hex characters: the single dispatch '
            'marker is named by it', args))
    # The marker's own time comes from the trusted anchor when it resolves and is
    # absent (with the reason) when it does not: never from the board RTC.
    marker_clock = dispatch_marker_clock(args)
    marker_path = dispatch_dir / (args.attempt_id + '.json')
    if marker_path.exists():
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_AUTHORIZATION, BOARD_STARTUP_GATE,
            'attempt %s has already been dispatched (%s exists): one attempt is '
            'dispatched once, and a replay neither re-verifies nor re-touches the '
            'board' % (args.attempt_id, marker_path), args))
    if args.ledger is not None:
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_AUTHORIZATION, BOARD_STARTUP_GATE,
            'the board entry does not take the offline --ledger file seam: the '
            'one-shot ledger is <ledger-dir>/%s on the board persistent root '
            '(spec 2/5)' % LEDGER_FILE_NAME, args,
            detail=dict(ledger_path=str(args.ledger))))
    try:
        receipt = verify_run_bundle_members(ROOT)
    except BundleRefused as refused:
        marker, marker_error = _write_dispatch_marker(
            dispatch_dir, args.attempt_id, None, [str(refused)], RUN_REFUSED_BUNDLE,
            clock=marker_clock)
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_BUNDLE, BOARD_STARTUP_GATE, str(refused), args,
            failures=[str(refused)], marker=marker, marker_error=marker_error))
    if (args.runner_version is not None
            and args.runner_version != receipt.get('runner_version')):
        reason = ('--runner-version %s is not the version the run bundle receipt '
                  'binds (%s)' % (args.runner_version, receipt.get('runner_version')))
        marker, marker_error = _write_dispatch_marker(
            dispatch_dir, args.attempt_id, receipt, [reason], RUN_REFUSED_BUNDLE,
            clock=marker_clock)
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_BUNDLE, BOARD_STARTUP_GATE, reason, args,
            failures=[reason], marker=marker, marker_error=marker_error))
    marker, marker_error = _write_dispatch_marker(
        dispatch_dir, args.attempt_id, receipt, [], BOARD_STARTUP_GATE,
        clock=marker_clock)
    if marker_error is not None:
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_AUTHORIZATION, BOARD_STARTUP_GATE,
            'the single dispatch marker could not be created: %s' % marker_error, args,
            detail=dict(dispatch_marker_error=marker_error)))
    release_directory = Path(args.release_directory)
    try:
        identity = identify_release_identity(release_directory)
    except (OSError, ValueError) as exc:
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_IMPORT, BOARD_IMPORT_GATE,
            'the installed release identity could not be established from %s before '
            'any import (%s): %s'
            % (release_directory, type(exc).__name__, str(exc) or type(exc).__name__),
            args, detail=dict(release_directory=str(release_directory),
                              error_type=type(exc).__name__), marker=marker))
    real_release = Path(identity['release_directory'])
    manifest = identity['manifest']
    path_record = control_search_path(ROOT, real_release)
    try:
        # spec 3.3: the checker comes from the RUN BUNDLE (it is a declared
        # member), and an unimportable checker refuses before any board action.
        from scripts.rf_teaching_install_state import verified_state  # noqa: F401
    except Exception as exc:                      # noqa: BLE001 - refused, not raised
        return dict(record=None, release_verifier=None, refusal=_board_startup_report(
            RUN_REFUSED_IMPORT, BOARD_IMPORT_GATE,
            'the run bundle checker is not importable (%s): %s'
            % (type(exc).__name__, exc), args,
            detail=dict(checker='scripts/rf_teaching_install_state.py'),
            marker=marker))
    record = dict(
        stage='BOARD-RUN-BUNDLE', passed=True, gate=BOARD_IMPORT_GATE,
        bundle_root=str(ROOT),
        runner_version=receipt.get('runner_version'),
        source_commit=receipt.get('source_commit'), builder=receipt.get('builder'),
        members=sorted(receipt.get('members') or {}),
        bundle_receipt_sha256=sha256_file(receipt_path), dispatch_marker=marker,
        release_directory=str(real_release), release_id=manifest.get('release_id'),
        software_contract=manifest.get('software_contract'),
        frozen_module_sha256=dict(FROZEN_RESEARCH_MODULES),
        search_path=path_record['search_path'],
        dropped_search_path=path_record['dropped_search_path'],
        gates=[dict(gate=BOARD_STARTUP_GATE, passed=True, board_read=False,
                    members=len(BUNDLE_MEMBERS), dispatch_marker=marker),
               dict(gate=BOARD_IMPORT_GATE, passed=True, board_read=False,
                    release_directory=str(real_release),
                    release_id=manifest.get('release_id'))],
    )
    return dict(record=record, refusal=None, release_verifier=None)


def _report_outcome(report):
    """The existing terminal observation point: one `HEADROOM_RUN <json>` line."""
    print('HEADROOM_RUN ' + json.dumps(
        dict(terminal=report['terminal'], retry_allowed=report['retry_allowed'],
             reason=report['reason'], gate_document=report.get('gate_document'),
             new_physical_frames=report['observation_classes']['new_physical_frames'],
             simulation=report['simulation']),
        sort_keys=True, allow_nan=False))
    return report['exit_code']

# ------------------------------------------------------------------------ CLI
def _add_common(parser):
    parser.add_argument('--authorization', required=True,
                        help='the one-shot authorization artifact (JSON)')
    parser.add_argument('--session-id', default=None,
                        help='32 lowercase hex characters; it must be the session id in '
                             'the signed authorization (default: that id)')
    parser.add_argument('--token-path', '--token', dest='token_path', required=True,
                        help='operator control token path; only its presence is recorded')
    parser.add_argument('--wiring-confirmed', required=True,
                        help='type the exact statement you signed; it must match the '
                             'authorization (there is no override)')
    parser.add_argument('--release-directory', default=DEFAULT_RELEASE_DIRECTORY)
    parser.add_argument('--experiment-root', default=DEFAULT_EXPERIMENT_ROOT)
    parser.add_argument('--evidence-root', default=BOARD_EVIDENCE_ROOT,
                        help='research session evidence root; it may never be the '
                             'teaching record directory')
    parser.add_argument('--observation-dir', default=BOARD_OBSERVATION_DIR,
                        help='independent READ-ONLY observation receipts; it must be '
                             'separate from the evidence root')
    parser.add_argument('--ledger-dir', default=BOARD_LEDGER_DIR,
                        help='BOARD persistent one-shot ledger DIRECTORY: the ledger is '
                             '<ledger-dir>/authorization.jsonl and the dispatch marker '
                             'directory is its sibling')
    parser.add_argument('--ledger', default=None,
                        help='offline development seam only: reuse the reviewed '
                             'journal as a ledger FILE; a board entry refuses it')
    parser.add_argument('--runner-version', default=None,
                        help='the runner version the uploaded bundle receipt binds; it '
                             'must be the packaged runner version')
    parser.add_argument('--attempt-id', required=True,
                        help='32 lowercase hex characters identifying this attempt')


def build_parser():
    parser = argparse.ArgumentParser(
        prog='rf_headroom_research_run.py', description=__doc__.splitlines()[0],
        epilog='There is deliberately no --force, --allow-unqualified, '
               '--skip-preflight, --attempts, --attenuation-mdb or '
               '--ignore-wiring-age: every one of those would be a bypass.  There is '
               'also no --anchor-age, --anchor-tolerance or --allow-board-clock: the '
               'trusted clock anchor tolerances are fixed, and the board RTC is '
               'never a clock.')
    subparsers = parser.add_subparsers(dest='action', required=True)
    run = subparsers.add_parser('run', help='one real session (needs injected board access)')
    _add_common(run)
    # Contract section 8: the board RTC may not decide anything, so the session's
    # trusted clock is injected explicitly.  All three are optional AT THE PARSER
    # so that a missing anchor is the runner's own named refusal
    # (RUN_REFUSED_CLOCK_ANCHOR) instead of an argparse usage error, and there is
    # deliberately no way to change the fixed tolerances.
    run.add_argument('--host-utc', default=None, metavar='ISO8601',
                     help='the trusted host UTC instant of this session, WITH an '
                          'offset (the authorization window, the wiring age and the '
                          'evidence timestamps are decided by host UTC anchored to '
                          '--anchor-boot-id and --anchor-uptime-s; the board RTC is '
                          'never used)')
    run.add_argument('--anchor-boot-id', default=None,
                     help='the board boot_id the host read when it took --host-utc; '
                          'it must be this boot id or the run is refused')
    run.add_argument('--anchor-uptime-s', default=None,
                     help='the board uptime in seconds at that same instant; it must '
                          'be within the fixed tolerance of the board uptime read '
                          'here, and it may not go backwards')
    simulate = subparsers.add_parser(
        'simulate', help='offline fake-hardware session; never opens a socket')
    _add_common(simulate)
    simulate.add_argument('--fake-chain', required=True, type=Path,
                          help='module exposing dependencies(case=..., scenario=..., ...)')
    simulate.add_argument('--case', required=True)
    simulate.add_argument('--scenario', required=True)
    return parser


def simulate_dependencies(module, case, scenario, attempt_id, evidence_root,
                          release_directory, ledger_path):
    """Load the injected fake chain and refuse anything that is not a simulation.

    The module is loaded BY PATH and must expose
    `dependencies(case=..., scenario=..., attempt_id=..., evidence_root=...,
    release_directory=..., ledger_path=...)` returning either a `Dependencies`
    bundle or a mapping of its fields.  A bundle that does not declare
    `simulation=True` and `hardware_touched=False` is refused: the `simulate`
    subcommand exists so that fake hardware cannot be dressed up as a board.
    """
    build = getattr(module, 'dependencies', None)
    if build is None:
        raise ValueError('the fake chain does not expose dependencies(...)')
    bundle = build(case=case, scenario=scenario, attempt_id=attempt_id,
                   evidence_root=evidence_root, release_directory=release_directory,
                   ledger_path=ledger_path)
    if isinstance(bundle, dict):
        unknown = sorted(set(bundle) - {field.name for field in
                                        dataclasses.fields(Dependencies)})
        if unknown:
            raise ValueError('the fake chain returned unknown dependency fields: '
                             + ', '.join(unknown))
        bundle = Dependencies(**bundle)
    if not isinstance(bundle, Dependencies):
        raise ValueError('the fake chain must return a Dependencies bundle')
    if bundle.simulation is not True or bundle.hardware_touched is not False:
        raise ValueError('the fake chain must declare simulation=True and '
                         'hardware_touched=False')
    return bundle


def main(argv=None, board_entry=None):
    """The board-side entry point (and the offline CLI).

    `board_entry` is True when this process IS the entry script the board runs
    (`python .../rf_headroom_research_run.py ...`); it defaults to that exact
    condition.  Only the board entry runs the startup gates of spec 3/4 and uses
    the board ledger and the release verifier those gates produced -- a bundle
    that is present is ALWAYS verified, whatever way this function was called.

    The `run` subcommand also carries the session's trusted clock anchor
    (`--host-utc`, `--anchor-boot-id`, `--anchor-uptime-s`); it is passed
    through to G0, which resolves it or refuses by name (contract section 8).
    """
    args = build_parser().parse_args(argv)
    if board_entry is None:
        board_entry = (__name__ == '__main__')
    ledger_path = (args.ledger if args.ledger is not None
                   else str(Path(args.ledger_dir) / LEDGER_FILE_NAME))
    startup = dict(record=None, refusal=None, release_verifier=None)
    if args.action == 'run':
        startup = board_startup_gates(args, board_entry)
    if startup['refusal'] is not None:
        return _report_outcome(startup['refusal'])
    if args.action == 'simulate':
        module = load_module_by_path(args.fake_chain)
        dependencies = simulate_dependencies(
            module, args.case, args.scenario, args.attempt_id, args.evidence_root,
            args.release_directory, ledger_path)
    else:
        dependencies = default_dependencies(args.release_directory,
                                            defer_token_reader=True)
    report = run_research(
        authorization_path=args.authorization, token_path=args.token_path,
        wiring_statement=args.wiring_confirmed,
        release_directory=args.release_directory, evidence_root=args.evidence_root,
        ledger_path=ledger_path, attempt_id=args.attempt_id, dependencies=dependencies,
        experiment_root=args.experiment_root, session_id=args.session_id,
        observation_dir=args.observation_dir,
        board_ledger_dir=(args.ledger_dir if (board_entry and args.action == 'run')
                          else None),
        release_verifier=startup['release_verifier'], board_startup=startup['record'],
        host_utc=getattr(args, 'host_utc', None),
        anchor_boot_id=getattr(args, 'anchor_boot_id', None),
        anchor_uptime_s=getattr(args, 'anchor_uptime_s', None))
    return _report_outcome(report)


if __name__ == '__main__':
    raise SystemExit(main())
