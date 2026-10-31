"""Offline simulated tests for the real HEADROOM research runner.

Scope: everything the RUNNER owns -- the gate order (`G0`..`G4`), the one-shot
authorization and its append-only consumption ledger, wiring freshness, the one
synchronous execution entry, the evidence model, the runner-level terminal labels
and the final independent read-only safety readback.

What these tests are NOT: a re-run of the frozen chain's own semantics.  T1/T2/
T3/T7-T12 of the runner design are already asserted by
`pynq_overlay/sw/tests/test_rf_headroom*.py`, which are AS-reviewed frozen files;
duplicating their assertions would add no evidence, so those suites are re-run
separately instead (see `evidence/rf-teaching/s2/headroom-runner-2026-10-12/`).

Every test runs in this process.  An autouse guard makes any attempt to create a
socket fail, so "zero board contact" is enforced rather than promised: no SSH, no
`127.0.0.1:5000`, no upload, no install, no Overlay, no arm, no transmit, no
capture.  The frozen worker, controller, recorder, replay and OFF guard ARE the
real objects, loaded from the sealed candidate release directory BY PATH.

The runner module under test can be redirected to a mutated COPY of itself with
`RF_HEADROOM_RUNNER_PATH` (used only by the mutation harness; the repository tree
is never modified).
"""
import ast
import contextlib
import datetime
import fnmatch
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
SW = REPO / 'pynq_overlay' / 'sw'
SEALED = REPO / 'evidence/rf-teaching/s2-headroom-candidate2-2026-10-17'
CANDIDATE = (SEALED / 'candidate' / 'releases'
             / 'e66197ed79131b73aa4f47804487c053')
SIM_CHAIN = REPO / 'evidence/rf-teaching/s2/headroom-runner-2026-10-12/sim-chain.py'
RUNNER_FILE = REPO / 'scripts/rf_headroom_research_run.py'

for _extra in (str(REPO), str(SW), str(SW / 'tests')):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

RUNNER_OVERRIDE = os.environ.get('RF_HEADROOM_RUNNER_PATH')
# The release the runner under test is pointed at.  It is the sealed candidate
# by default; the mutation harness redirects it to a %TEMP% copy when the
# mutation lives inside the release itself (which the runner loads BY PATH, so a
# module-name redirect cannot reach it).
RELEASE_DIRECTORY = Path(os.environ.get('RF_HEADROOM_RELEASE_DIR', str(CANDIDATE)))


def _load_by_path(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner_under_test():
    if RUNNER_OVERRIDE:
        module = _load_by_path(RUNNER_OVERRIDE, 'runner_under_test')
        assert Path(module.__file__).resolve() == Path(RUNNER_OVERRIDE).resolve()
        return module
    from scripts import rf_headroom_research_run as module
    return module


R = _runner_under_test()
sim = _load_by_path(SIM_CHAIN, 'headroom_sim_chain')
RUNNER_SOURCE = Path(RUNNER_OVERRIDE) if RUNNER_OVERRIDE else RUNNER_FILE

SESSION = '0123456789abcdef0123456789abcdef'
NONCE = 'b' * 32
TOKEN_VALUE = 'simulation-token'
IQ_BYTES = 16384


# --------------------------------------------------------------- test harness
@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    """Zero board contact, enforced: any socket in these tests fails the test."""
    def forbidden(*args, **kwargs):
        raise AssertionError('this test tried to open a socket')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    return forbidden


def authorization_document(*, nonce=NONCE, session=SESSION, token_ref, issued_at,
                           expires_at, confirmed_at, authorized_attempts=5,
                           single_use=True, expected_release=None,
                           software_contract=None, daemon_version=None,
                           protocol_minor=None, statement=None,
                           confirmed_by='operator', method='visual cable check',
                           artifact_digest='c' * 64, wiring=True, **extra):
    document = dict(
        authorization_id='a' * 32, nonce=nonce, session_id=session,
        expected_release=(R.QUALIFIED_RELEASE if expected_release is None
                          else expected_release),
        software_contract=(R.SOFTWARE_CONTRACT if software_contract is None
                           else software_contract),
        daemon_version=(R.DAEMON_VERSION if daemon_version is None else daemon_version),
        protocol_minor=(R.PROTOCOL_MINOR if protocol_minor is None else protocol_minor),
        issued_at=issued_at, expires_at=expires_at, single_use=single_use,
        wiring=(dict(confirmed_by=confirmed_by, confirmed_at=confirmed_at,
                     statement=(R.WIRING_STATEMENT if statement is None else statement),
                     method=method, artifact_digest=artifact_digest) if wiring else wiring),
        token_ref=token_ref, authorized_attempts=authorized_attempts)
    document.update(extra)
    return document


def _iso(moment):
    return moment.isoformat()


class Harness:
    """One runner invocation's inputs, all inside the test's tmp_path.

    The clock is the real one, frozen at construction and injected into the
    bundle, so the API tests are deterministic and the CLI tests (which use the
    runner's own default clock) see a consistent "now".
    """

    def __init__(self, root, *, nonce=NONCE, session=SESSION, token_exists=True,
                 token_text=TOKEN_VALUE, experiment_root=None, authorization=True,
                 now=None, wiring_age_s=1800, issued_ago_s=3600, expires_in_s=10800,
                 **overrides):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # The default instant is the fixture's frozen one, which is also the one
        # `simulate`'s fake clock uses -- so a CLI test and an API test see the
        # same "now".  A test that drives the real default clock passes it in.
        self.now = now or sim.SIMULATED_NOW
        self.token = self.root / 'token'
        if token_exists:
            self.token.write_text(token_text, encoding='ascii')
        self.evidence = self.root / 'evidence'
        self.ledger = self.root / 'ledger.jsonl'
        self.experiment_root = experiment_root or str(self.root / 'experiments')
        self.authorization_path = self.root / 'authorization.json'
        self.document = authorization_document(
            nonce=nonce, session=session, token_ref=str(self.token),
            issued_at=_iso(self.now - datetime.timedelta(seconds=issued_ago_s)),
            expires_at=_iso(self.now + datetime.timedelta(seconds=expires_in_s)),
            confirmed_at=_iso(self.now - datetime.timedelta(seconds=wiring_age_s)),
            **overrides)
        if authorization:
            self.write()

    def write(self, document=None):
        if document is not None:
            self.document = document
        self.authorization_path.parent.mkdir(parents=True, exist_ok=True)
        self.authorization_path.write_text(json.dumps(self.document), encoding='utf-8')
        return self.authorization_path

    def at(self, seconds):
        """An ISO timestamp `seconds` from this harness's frozen now."""
        return _iso(self.now + datetime.timedelta(seconds=seconds))

    def bundle(self, case='full-five', scenario='real-lease-semantics', **overrides):
        overrides.setdefault('clock', sim.SimClock(self.now))
        return sim.make_bundle(release_directory=str(RELEASE_DIRECTORY),
                               evidence_root=str(self.evidence), case=case,
                               scenario=scenario, **overrides)

    def run(self, bundle=None, *, release_directory=None, wiring_statement=None,
            attempt_id='d' * 32, authorization_path=None, token_path=None,
            evidence_root=None, ledger_path=None, dependencies=None,
            host_utc=None, anchor_boot_id=None, anchor_uptime_s=None,
            board_ledger_dir=None):
        # The three anchor inputs are the TRUSTED clock (contract section 8);
        # without them a bundle that declares real hardware refuses by name.
        if dependencies is None:
            bundle = self.bundle() if bundle is None else bundle
            dependencies = R.Dependencies(**bundle)
        return R.run_research(
            authorization_path=str(authorization_path or self.authorization_path),
            token_path=str(token_path or self.token),
            wiring_statement=(R.WIRING_STATEMENT if wiring_statement is None
                              else wiring_statement),
            release_directory=str(release_directory or RELEASE_DIRECTORY),
            evidence_root=str(evidence_root or self.evidence),
            ledger_path=str(ledger_path or self.ledger), attempt_id=attempt_id,
            dependencies=dependencies, experiment_root=self.experiment_root,
            host_utc=host_utc, anchor_boot_id=anchor_boot_id,
            anchor_uptime_s=anchor_uptime_s, board_ledger_dir=board_ledger_dir)

    # -- evidence helpers ---------------------------------------------------
    def session_directory(self, session=SESSION):
        return self.evidence / session

    def gate_document(self, session=SESSION):
        return self.session_directory(session) / 'run-gate.json'

    def refusal_documents(self, attempt_id='d' * 32):
        return self.evidence / 'run-gate' / (attempt_id + '.json')

    def ledger_lines(self):
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in
                self.ledger.read_text(encoding='utf-8').splitlines() if line.strip()]

    def evidence_text(self):
        """Every byte the runner wrote: the evidence root, the ledger, the artifact."""
        chunks = []
        for base in (self.evidence,):
            for path in sorted(base.rglob('*')):
                if path.is_file():
                    chunks.append(path.read_bytes().decode('utf-8', 'replace'))
        for extra in (self.ledger, self.authorization_path):
            if extra.exists():
                chunks.append(extra.read_bytes().decode('utf-8', 'replace'))
        return '\n'.join(chunks)


def make_release(tree, *, contract=R.SOFTWARE_CONTRACT, research_modules=True,
                 research_source=None):
    """A release that verifies, built from the frozen suite's own release recipe.

    The research_source argument selects which tree the two research modules are
    copied from; the default is the working tree, and the sealed release's own
    copies are used when a test needs the frozen digests to pass (after the
    2026-10-17 migration the working copies no longer match them).
    """
    import shutil
    from test_rf_model_release import R as MR, _release, _save_identity
    tree = Path(tree)
    manifest, receipt = _release(tree, contract=contract)
    if research_modules:
        source = Path(research_source) if research_source is not None else SW
        for name in ('rf_headroom.py', 'rf_headroom_replay.py'):
            shutil.copyfile(str(source / name), str(tree / name))
            manifest['software'][name] = MR.sha256_file(tree / name)
        receipt['software'] = manifest['software']
        _save_identity(tree, manifest, receipt)
    return tree


def tamper_release_module(tree, name):
    """Flip one byte of a release module and re-seal the manifest around it.

    Re-sealing matters: the release still verifies as a release, so the refusal
    can only come from the FROZEN digest comparison -- which is the gate under
    test.  (A manifest left inconsistent would be caught one layer earlier, by
    `verify_release`'s own hash check.)
    """
    from test_rf_model_release import R as MR, _save_identity
    tree = Path(tree)
    manifest = json.loads((tree / MR.MANIFEST_NAME).read_text(encoding='utf-8'))
    receipt = json.loads((tree / 'merged_build_receipt.json').read_text(encoding='utf-8'))
    target = tree / name
    data = bytearray(target.read_bytes())
    data[-1] = data[-1] ^ 0x01
    target.write_bytes(bytes(data))
    manifest['software'][name] = MR.sha256_file(target)
    receipt['software'] = manifest['software']
    _save_identity(tree, manifest, receipt)
    return tree


# ----------------------------------------------------- the frozen seal is real
def test_the_qualified_release_pin_is_the_sealed_candidate_review():
    review = json.loads((SEALED / 'python' / 'critical-review.json').read_text('utf-8'))
    assert review['verdict'] == 'APPROVED'
    assert R.QUALIFIED_RELEASE == review['candidate_release_id']


def test_the_frozen_module_digests_are_the_sealed_source_freeze_ones():
    freeze = json.loads((SEALED / 'source-freeze.json').read_text('utf-8'))
    for name, digest in R.FROZEN_RESEARCH_MODULES.items():
        assert freeze['software'][name] == digest


def test_the_frozen_module_digests_are_the_installed_release_files():
    """The sealed release still matches; the migrated working tree has moved on.

    After the 2026-10-17 domain migration the working copies of the research
    modules differ from the frozen digests of the installed release, so another
    research run needs a new candidate + ARM + install (migration steps M3/M4).
    Both directions are asserted, so neither a silent accept nor a silent
    re-freeze of the working tree can hide the drift.
    """
    for name, digest in R.FROZEN_RESEARCH_MODULES.items():
        assert hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest() == digest
        assert hashlib.sha256((SW / name).read_bytes()).hexdigest() != digest


# --------------------------------------------------------------- gate order
def test_the_gate_order_is_the_reviewed_one_and_every_gate_ran(tmp_path):
    report = Harness(tmp_path).run()
    assert [gate['gate'] for gate in report['gates']] == list(R.GATE_ORDER)
    assert all(gate['passed'] is True for gate in report['gates'])
    assert report['gate_order'] == list(R.GATE_ORDER)
    assert report['terminal'] == R.RUN_COMPLETED
    assert report['retry_allowed'] is False


def test_no_board_read_happens_before_the_authorization_gate(tmp_path):
    harness = Harness(tmp_path, wiring=False)          # G0 refuses
    bundle = harness.bundle()
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert bundle['storage_probe'].calls == []
    assert bundle['identity_probe'].calls == 0
    assert bundle['readback_probe'].calls == []
    assert report['study_invoked'] is False
    assert [gate['gate'] for gate in report['gates']] == [R.GATE_AUTHORIZATION]
    assert report['gates'][0]['board_read'] is False


def test_the_consumption_ledger_record_precedes_the_first_board_read(tmp_path):
    harness = Harness(tmp_path)

    class OrderProbe(sim.StorageProbe):
        def __call__(self):
            self.ledger_at_call = (harness.ledger.read_text(encoding='utf-8')
                                   if harness.ledger.exists() else '')
            return sim.StorageProbe.__call__(self)

    ordered = OrderProbe()
    bundle = harness.bundle()
    bundle['storage_probe'] = ordered
    harness.run(bundle)
    assert NONCE in ordered.ledger_at_call
    assert len(ordered.calls) == 2                  # preflight + post-run read-back


def test_a_refusal_before_the_study_never_builds_a_worker_or_a_session(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle(case='storage-fault')
    calls = []
    inner = bundle['worker_factory']

    def factory(**kwargs):
        calls.append(kwargs)
        return inner(**kwargs)
    bundle['worker_factory'] = factory
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_ABORTED_STORAGE_FAULT
    assert calls == []
    assert not harness.session_directory().exists()
    assert not (harness.session_directory() / 'receipt.json').exists()


# ------------------------------------------------- G0: the authorization gate
def test_a_missing_authorization_refuses_before_any_board_read(tmp_path):
    harness = Harness(tmp_path, authorization=False)
    bundle = harness.bundle()
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['exit_code'] == R.EXIT_NOT_STARTED
    assert report['refusal']['gate'] == R.GATE_AUTHORIZATION
    assert bundle['storage_probe'].calls == []
    assert not harness.ledger.exists()


def test_an_authorization_that_is_not_json_refuses(tmp_path):
    harness = Harness(tmp_path)
    harness.authorization_path.write_text('{not json', encoding='utf-8')
    report = harness.run(harness.bundle())
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'not JSON' in report['refusal']['reason']


@pytest.mark.parametrize('fault,needle', [
    ('missing_field', 'reviewed field set'),
    ('unknown_field', 'reviewed field set'),
    ('bad_session', 'session_id'),
    ('bad_release', 'not qualified for'),
    ('bad_contract', 'software_contract'),
    ('bad_daemon', 'daemon_version'),
    ('bad_minor', 'protocol_minor'),
    ('bad_attempts_type', 'protocol_minor'),
    ('single_use_false', 'single_use'),
    ('attempts_four', 'authorized_attempts'),
    ('attempts_boolean', 'authorized_attempts'),
    ('bad_nonce', 'nonce'),
    ('bad_wiring_digest', 'artifact_digest'),
    ('bad_confirmed_by', 'confirmed_by'),
    ('wiring_unknown_field', 'reviewed field set'),
    ('expires_before_issued', 'after issued_at'),
    ('expired', 'expired'),
    ('not_yet_valid', 'not valid yet'),
])
def test_each_malformed_or_mismatched_authorization_field_refuses(tmp_path, fault, needle):
    harness = Harness(tmp_path)
    document = dict(harness.document)
    if fault == 'missing_field':
        document.pop('token_ref')
    elif fault == 'unknown_field':
        document['extra_field'] = 1
    elif fault == 'bad_session':
        document['session_id'] = 'not-hex'
    elif fault == 'bad_release':
        document['expected_release'] = 'f' * 32
    elif fault == 'bad_contract':
        document['software_contract'] = 'cnn-bpsk-environment-v1'
    elif fault == 'bad_daemon':
        document['daemon_version'] = '1.0.3'
    elif fault == 'bad_minor':
        document['protocol_minor'] = 5
    elif fault == 'bad_attempts_type':
        document['protocol_minor'] = 6.0
    elif fault == 'single_use_false':
        document['single_use'] = False
    elif fault == 'attempts_four':
        document['authorized_attempts'] = 4
    elif fault == 'attempts_boolean':
        document['authorized_attempts'] = True
    elif fault == 'bad_nonce':
        document['nonce'] = 'short'
    elif fault == 'bad_wiring_digest':
        document['wiring'] = dict(document['wiring'], artifact_digest='nope')
    elif fault == 'bad_confirmed_by':
        document['wiring'] = dict(document['wiring'], confirmed_by='')
    elif fault == 'wiring_unknown_field':
        document['wiring'] = dict(document['wiring'], extra='x')
    elif fault == 'expires_before_issued':
        document['expires_at'] = harness.at(-7200)
    elif fault == 'expired':
        document['expires_at'] = harness.at(-60)
    elif fault == 'not_yet_valid':
        document['issued_at'] = harness.at(3600)
    harness.write(document)
    bundle = harness.bundle()
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_AUTHORIZATION
    assert needle in report['refusal']['reason'], report['refusal']
    assert bundle['storage_probe'].calls == []
    assert report['study_invoked'] is False


def test_an_expired_authorization_does_not_consume_the_ledger(tmp_path):
    harness = Harness(tmp_path, expires_in_s=-60)
    harness.run(harness.bundle())
    assert not harness.ledger.exists()


def test_a_replayed_nonce_is_refused_by_the_consumption_ledger(tmp_path):
    first = Harness(tmp_path / 'first')
    report = first.run()
    assert report['terminal'] == R.RUN_COMPLETED
    assert len(first.ledger_lines()) == 1
    assert first.ledger_lines()[0]['nonce'] == NONCE
    assert first.ledger_lines()[0]['phase'] == 'AUTHORIZATION_CONSUMED'
    assert first.ledger_lines()[0]['seq'] == 1

    second = Harness(tmp_path / 'second', nonce=NONCE)
    bundle = second.bundle()
    second.run(bundle, ledger_path=first.ledger)
    replay = second.run(second.bundle(), ledger_path=first.ledger)
    assert replay['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'replayed' in replay['refusal']['reason']
    assert len(first.ledger_lines()) == 1


def test_a_torn_trailing_ledger_line_is_truncated_before_the_nonce_check(tmp_path):
    harness = Harness(tmp_path)
    harness.ledger.write_text(json.dumps(
        dict(phase='AUTHORIZATION_CONSUMED', nonce='9' * 32, seq=1)) + '\n'
        + '{"phase": "AUTHORIZATION_CONSUMED", "nonce": "abc', encoding='utf-8')
    report = harness.run()
    assert report['terminal'] == R.RUN_COMPLETED
    lines = harness.ledger_lines()
    assert [line['nonce'] for line in lines] == ['9' * 32, NONCE]
    assert '"abc' not in harness.ledger.read_text(encoding='utf-8')


# ---------------------------------------------- the consumption ledger (F2/F7)
def test_a_ledger_whose_parent_directory_is_missing_is_a_reported_refusal(tmp_path):
    """F2: an unusable ledger path is a refusal, not an OSError out of the API.

    The realistic operator typo `--ledger <dir that does not exist>/ledger.jsonl`.
    The rejected revision raised `FileNotFoundError` out of `run_research` and
    wrote no `run-gate.json` at all; the design requires a reported refusal with
    the gate fields recorded (design 10 and 3.3).
    """
    harness = Harness(tmp_path)
    ledger = tmp_path / 'no-such-dir' / 'ledger.jsonl'
    bundle = harness.bundle()
    report = harness.run(bundle, ledger_path=ledger)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['exit_code'] == R.EXIT_NOT_STARTED
    assert report['refusal']['gate'] == R.GATE_AUTHORIZATION
    assert 'consumption ledger is unusable' in report['refusal']['reason']
    assert report['refusal']['detail']['error_type'] == 'FileNotFoundError'
    assert report['refusal']['detail']['ledger_path'] == str(ledger)
    assert bundle['storage_probe'].calls == []            # nothing reached the board
    assert bundle['identity_probe'].calls == 0
    assert bundle['readback_probe'].calls == []
    assert report['study_invoked'] is False
    assert not ledger.exists()
    assert not harness.session_directory().exists()
    document = harness.refusal_documents()
    assert document.is_file()                             # the record an operator needs
    written = json.loads(document.read_text('utf-8'))
    assert written['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert written['retry_allowed'] is False
    assert written['study_invoked'] is False


def test_a_ledger_path_that_is_a_directory_is_a_reported_refusal(tmp_path):
    """F2, second shape: the ledger path is a directory (read and append both fail)."""
    harness = Harness(tmp_path)
    ledger = tmp_path / 'ledger-directory'
    ledger.mkdir()
    bundle = harness.bundle()
    report = harness.run(bundle, ledger_path=ledger)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['exit_code'] == R.EXIT_NOT_STARTED
    assert report['refusal']['gate'] == R.GATE_AUTHORIZATION
    assert 'consumption ledger is unusable' in report['refusal']['reason']
    assert report['refusal']['detail']['error_type'] in ('PermissionError',
                                                         'IsADirectoryError')
    assert bundle['storage_probe'].calls == []
    assert report['study_invoked'] is False
    assert harness.refusal_documents().is_file()
    assert not list(harness.evidence.rglob('receipt.json'))


def test_a_ledger_sync_failure_after_append_never_claims_nonce_unconsumed(
        tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    original = R.ConsumptionLedger.consume

    def append_then_fail(self, **fields):
        original(self, **fields)
        raise OSError('simulated fsync failure after write')

    monkeypatch.setattr(R.ConsumptionLedger, 'consume', append_then_fail)
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['retry_allowed'] is False
    assert report['study_invoked'] is False
    assert bundle['storage_probe'].calls == []
    assert harness.ledger_lines()[0]['nonce'] == NONCE
    reason = report['refusal']['reason']
    assert 'consumption outcome is unknown' in reason
    assert 'nothing was consumed' not in reason
    assert report['refusal']['detail']['consumption_status'] == 'UNKNOWN'
    written = json.loads(harness.refusal_documents().read_text('utf-8'))
    assert written['refusal']['detail']['consumption_status'] == 'UNKNOWN'


def test_an_authorization_artifact_that_cannot_be_read_is_a_reported_refusal(
        tmp_path, monkeypatch):
    """F7: the artifact exists and is a regular file, but this process cannot read it.

    The read failure is injected (a permission fault cannot be produced portably in
    a test), and every other path is the real one: the same refusal class as F2,
    with a gate document instead of an exception out of `run_research`.
    """
    harness = Harness(tmp_path)
    original = Path.read_bytes

    def blocked(self):
        if self.name == 'authorization.json':
            raise PermissionError(13, 'the artifact is not readable by this process')
        return original(self)
    monkeypatch.setattr(Path, 'read_bytes', blocked)
    bundle = harness.bundle()
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['exit_code'] == R.EXIT_NOT_STARTED
    assert report['refusal']['gate'] == R.GATE_AUTHORIZATION
    assert 'could not be read' in report['refusal']['reason']
    assert 'PermissionError' in report['refusal']['reason']
    assert bundle['storage_probe'].calls == []
    assert not harness.ledger.exists()                    # nothing was consumed
    assert not harness.session_directory().exists()
    assert harness.refusal_documents().is_file()


def test_the_token_path_must_be_the_one_the_authorization_references(tmp_path):
    harness = Harness(tmp_path)
    other = tmp_path / 'other-token'
    other.write_text(TOKEN_VALUE, encoding='ascii')
    bundle = harness.bundle()
    report = harness.run(bundle, token_path=other)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'not the one the authorization references' in report['refusal']['reason']
    assert bundle['storage_probe'].calls == []


def test_an_unusable_token_refuses_with_zero_attempts_and_no_directory(tmp_path):
    harness = Harness(tmp_path, token_exists=False)
    bundle = harness.bundle()
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['token_present'] is False
    assert report['study_invoked'] is False
    assert bundle['storage_probe'].calls == []
    assert not harness.session_directory().exists()
    assert not (harness.session_directory() / 'receipt.json').exists()


# ------------------------------------------------------- wiring freshness
def test_a_wiring_confirmation_older_than_the_fixed_window_is_refused(tmp_path):
    assert R.WIRING_FRESHNESS_S == 12 * 3600
    harness = Harness(tmp_path, wiring_age_s=12 * 3600 + 1)
    bundle = harness.bundle()
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'older than the fixed 12 h window' in report['refusal']['reason']
    assert bundle['storage_probe'].calls == []


def test_a_wiring_confirmation_inside_the_window_is_accepted(tmp_path):
    harness = Harness(tmp_path, wiring_age_s=12 * 3600 - 1)
    assert harness.run()['terminal'] == R.RUN_COMPLETED


def test_a_wiring_confirmation_stamped_in_the_future_is_refused(tmp_path):
    harness = Harness(tmp_path, wiring_age_s=-3600)
    report = harness.run()
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'stamped in the future' in report['refusal']['reason']


def test_the_statement_typed_on_the_command_line_must_match_the_signed_one(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    report = harness.run(bundle, wiring_statement='I think the cable is fine')
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'not the one in the signed authorization' in report['refusal']['reason']
    assert bundle['storage_probe'].calls == []


def test_the_wiring_record_is_copied_into_the_evidence(tmp_path):
    report = Harness(tmp_path).run()
    assert report['wiring']['confirmed_by'] == 'operator'
    assert report['wiring']['statement'] == R.WIRING_STATEMENT
    assert report['wiring']['age_s'] == 1800.0
    assert report['wiring']['freshness_limit_s'] == 12 * 3600
    assert report['wiring']['artifact_digest'] == 'c' * 64
    assert report['authorization']['artifact_sha256']


# ------------------------------------------------------- G1: storage preflight
def test_detected_storage_faults_abort_with_zero_board_writes(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle(case='storage-fault')
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_ABORTED_STORAGE_FAULT
    assert report['exit_code'] == R.EXIT_NOT_STARTED
    assert report['storage']['preflight']['preflight_status'] == 'FAULTS_DETECTED'
    assert report['storage']['preflight']['faults']
    assert report['study_invoked'] is False
    assert len(bundle['storage_probe'].calls) == 1        # the abort is immediate
    assert next(g for g in report['gates'] if g['gate'] == R.GATE_STORAGE)['board_read'] is True
    assert bundle['readback_probe'].calls == []


@pytest.mark.parametrize('key', ['timeout waiting for hardware interrupt', 'adma err',
                                 'i/o error', 'buffer i/o error', 'ext4-fs error',
                                 'mmc0: error'])
def test_the_storage_predicate_is_the_reviewed_deploy_predicate(tmp_path, key):
    from scripts.rf_headroom_deploy import KERNEL_FAULT_KEYS, storage_faults
    assert key in KERNEL_FAULT_KEYS
    text = '[    1.0] mmc0: SDHCI controller using ADMA\n[    2.0] %s\n' % key
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(kernel_text=text))
    assert report['terminal'] == R.RUN_ABORTED_STORAGE_FAULT
    assert report['storage']['preflight']['faults'] == storage_faults(text)


def test_an_incomplete_preflight_is_not_evidence_of_a_clean_board(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle(complete=False)
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['terminal'] != R.RUN_ABORTED_STORAGE_FAULT
    assert report['refusal']['gate'] == R.GATE_STORAGE
    assert report['storage']['preflight']['preflight_status'] == 'INCOMPLETE'
    assert 'NOT evidence of an error-free board' in report['refusal']['reason']
    assert report['study_invoked'] is False
    assert next(g for g in report['gates'] if g['gate'] == R.GATE_STORAGE)['board_read'] is True


def test_a_failing_preflight_read_is_not_evidence_of_a_clean_board(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle()

    def broken():
        raise OSError('dmesg is unavailable')
    bundle['storage_probe'] = broken
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_STORAGE
    assert report['storage']['preflight']['preflight_status'] == 'UNREADABLE'
    assert 'NOT evidence of an error-free board' in report['refusal']['reason']
    assert report['study_invoked'] is False
    assert next(g for g in report['gates'] if g['gate'] == R.GATE_STORAGE)['board_read'] is True


def test_the_two_abort_labels_are_distinguishable(tmp_path):
    aborted = Harness(tmp_path / 'a').run(
        Harness(tmp_path / 'a').bundle(case='storage-fault'))
    incomplete = Harness(tmp_path / 'b').run(
        Harness(tmp_path / 'b').bundle(complete=False))
    assert aborted['terminal'] == R.RUN_ABORTED_STORAGE_FAULT
    assert incomplete['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert aborted['refusal']['gate'] == incomplete['refusal']['gate'] == R.GATE_STORAGE
    assert aborted['storage']['preflight']['faults']
    assert not incomplete['storage']['preflight']['faults']
    assert aborted['storage']['preflight']['preflight_status'] == 'FAULTS_DETECTED'
    assert incomplete['storage']['preflight']['preflight_status'] == 'INCOMPLETE'


# ------------------------------------------------- G2/G3: release attestation
def test_the_installed_release_must_be_the_qualified_candidate(tmp_path):
    """A release that carries the frozen modules but is NOT the qualified one.

    The modules are copied from the sealed candidate, so the attestation gate
    passes and the identity gate is the one that has to refuse.
    """
    other = make_release(tmp_path / 'other-release', research_source=CANDIDATE)
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    report = harness.run(bundle, release_directory=other)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_IDENTITY
    assert 'not the release this run is authorized for' in report['refusal']['reason']
    assert report['refusal']['detail']['installed_release'] != R.QUALIFIED_RELEASE
    assert bundle['storage_probe'].calls                # the storage gate did run
    assert report['study_invoked'] is False
    assert next(g for g in report['gates'] if g['gate'] == R.GATE_IDENTITY)['board_read'] is False


def test_a_tampered_frozen_research_module_is_refused_by_the_attestation(tmp_path):
    release = tamper_release_module(make_release(tmp_path / 'tampered-release'),
                                    'rf_headroom.py')
    # The tampered release still verifies as a release: only the frozen digest
    # comparison can refuse it, and that is the gate under test.
    from scripts import merged_release
    assert merged_release.verify_release(release, require_software=True)['release_id']
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    report = harness.run(bundle, release_directory=release)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_ATTESTATION
    assert 'not the frozen one: rf_headroom.py' in report['refusal']['reason']
    assert report['study_invoked'] is False


def test_the_release_must_carry_the_headroom_software_contract(tmp_path):
    environment = make_release(tmp_path / 'environment',
                               contract='cnn-bpsk-environment-v1',
                               research_modules=False)
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(), release_directory=environment)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_ATTESTATION
    assert 'another software contract' in report['refusal']['reason']


@pytest.mark.parametrize('fault', ['release_id', 'daemon_version', 'protocol_minor',
                                   'protocol_major'])
def test_the_live_daemon_identity_must_be_the_authorized_one(tmp_path, fault):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    bundle['identity_probe'].identity[fault] = {
        'release_id': 'e' * 32, 'daemon_version': '1.0.3', 'protocol_minor': 5,
        'protocol_major': 2}[fault]
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_IDENTITY
    assert 'not the authorized release/daemon/protocol' in report['refusal']['reason']
    assert report['refusal']['detail']['mismatch'] == [fault]
    assert bundle['storage_probe'].calls and bundle['readback_probe'].calls == []
    assert report['study_invoked'] is False
    assert next(g for g in report['gates'] if g['gate'] == R.GATE_IDENTITY)['board_read'] is True


def test_an_unreadable_live_identity_refuses_before_the_study(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle(identity_error=OSError('device.info refused'))
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_IDENTITY
    assert report['study_invoked'] is False
    assert next(g for g in report['gates'] if g['gate'] == R.GATE_IDENTITY)['board_read'] is True


# ---------------------------------------------------- G4: worker attestation
class _UndeclaredWorker:
    """A worker whose release-side attestation does not name this release."""

    def __init__(self, release_id=None):
        self._release_id = release_id
        self.client = None
        self.calls = []

    def headroom_status(self):
        if self._release_id is None:
            return dict(available=False, reason='余量研究不可用: 模拟未接入',
                        release_id=None, module_sha256={})
        return dict(available=True, reason='ok', release_id=self._release_id,
                    module_sha256={})

    def headroom_run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError('the execution entry must not be reached')


def test_a_worker_that_cannot_attest_the_release_is_refused(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    bundle['worker_factory'] = lambda **kwargs: _UndeclaredWorker()
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_REFUSED_CAPABILITY
    assert report['exit_code'] == R.EXIT_NOT_STARTED
    assert report['refusal']['gate'] == R.GATE_WORKER
    assert report['study_invoked'] is False
    assert bundle['readback_probe'].calls == []
    assert next(g for g in report['gates'] if g['gate'] == R.GATE_WORKER)['board_read'] is False


def test_a_worker_that_attests_another_release_is_refused(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    bundle['worker_factory'] = lambda **kwargs: _UndeclaredWorker(release_id='f' * 32)
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_WORKER
    assert report['study_invoked'] is False


@pytest.mark.parametrize('failure', ['factory', 'unavailable', 'wrong-release'])
def test_a_failed_worker_attestation_records_its_declared_board_read(tmp_path, failure):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    calls = []

    def factory(**kwargs):
        calls.append('board-poll')
        if failure == 'factory':
            raise R.DependencyUnavailable(R.GATE_WORKER, 'simulated board poll')
        return _UndeclaredWorker(release_id='f' * 32 if failure == 'wrong-release'
                                 else None)

    factory.performs_board_read = True
    bundle['worker_factory'] = factory
    report = harness.run(bundle)
    assert calls == ['board-poll']
    assert report['refusal']['gate'] == R.GATE_WORKER
    assert next(g for g in report['gates'] if g['gate'] == R.GATE_WORKER)['board_read'] is True


def test_the_gate_record_follows_what_the_factory_declares(tmp_path):
    """F3: G4 is `board_read=True` exactly when the factory's path reads the board.

    The injected factory the suite normally uses attests in-process (it calls the
    worker's own `_ensure_headroom()`), so its record says False and names itself
    an injected factory.  The PRODUCTION factory cannot attest without its own
    read-only daemon poll, so its record must say True -- a reader of
    `run-gate.json` must not conclude that no board access preceded G5.
    """
    assert R.factory_performs_board_read(R.release_worker_factory) is True
    assert R.factory_performs_board_read(object()) is False

    harness = Harness(tmp_path)
    client = sim.SimClient()
    bundle = harness.bundle()

    def production(**kwargs):
        # The REAL production factory, with the in-memory peer injected as its
        # client; the declaration is the production factory's own, not the
        # wrapper's invention.
        return R.release_worker_factory(client=client, **kwargs)
    production.performs_board_read = R.release_worker_factory.performs_board_read
    bundle['worker_factory'] = production
    try:
        report = harness.run(bundle)
    finally:
        client.close()
    assert report['terminal'] == R.RUN_COMPLETED
    gate = [record for record in report['gates'] if record['gate'] == R.GATE_WORKER][0]
    assert gate['board_read'] is True
    assert 'daemon poll' in gate['attestation_source']

    injected = Harness(tmp_path / 'injected').run(Harness(tmp_path / 'injected').bundle())
    other = [record for record in injected['gates'] if record['gate'] == R.GATE_WORKER][0]
    assert other['board_read'] is False
    assert 'injected factory' in other['attestation_source']

    partial_client = sim.SimClient()
    partial_harness = Harness(tmp_path / 'partial')
    partial_bundle = partial_harness.bundle()
    partial_bundle['worker_factory'] = functools.partial(
        R.release_worker_factory, client=partial_client)
    try:
        partial_report = partial_harness.run(partial_bundle)
    finally:
        partial_client.close()
    assert partial_report['terminal'] == R.RUN_COMPLETED
    partial_gate = [record for record in partial_report['gates']
                    if record['gate'] == R.GATE_WORKER][0]
    assert partial_gate['board_read'] is True


def test_the_production_factorys_declared_read_is_the_frozen_workers_poll():
    """The declaration is not a comment: the frozen worker's own poll reads the client."""
    source = (SW / 'rf_status_worker.py').read_text(encoding='utf-8')
    run_body = source.split('def _run(self):', 1)[1].split('\n    def ', 1)[0]
    assert 'self._ensure_headroom()' in run_body          # the attestation G4 reads
    assert 'self._poll_once()' in run_body
    poll_body = source.split('def _poll_once(self):', 1)[1].split('\n    def ', 1)[0]
    assert 'self.client.poll()' in poll_body


# ------------------------------------------- the one synchronous entry point
def test_the_execution_entry_is_called_exactly_once_and_synchronously(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    inner = bundle['worker_factory']
    counters = []

    def factory(**kwargs):
        worker = inner(**kwargs)
        original = worker.headroom_run

        def counting(*args, **kwargs2):
            counters.append(dict(args=args, kwargs=dict(kwargs2)))
            return original(*args, **kwargs2)
        worker.headroom_run = counting
        return worker
    bundle['worker_factory'] = factory
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_COMPLETED
    assert len(counters) == 1
    call = counters[0]
    assert call['args'] == (SESSION,)
    assert call['kwargs']['cable_confirmed'] is True
    assert call['kwargs']['token_path'] == str(harness.token)
    assert call['kwargs']['evidence_root'] == str(harness.evidence)
    assert call['kwargs']['require_hardware'] is False        # declared simulation
    assert 'token' not in call['kwargs']                      # never passed by value
    assert report['gates'][-1]['calls'] == 1
    assert report['gates'][-1]['entry'] == 'RfStatusWorker.headroom_run'


def test_a_physical_run_demands_native_hardware_attestation(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle(client=sim.SimClient(verified=False))
    bundle['simulation'] = False                       # a physical run, no simulation
    seen = {}
    inner = bundle['worker_factory']

    def factory(**kwargs):
        worker = inner(**kwargs)
        original = worker.headroom_run

        def capture(*args, **kwargs2):
            seen.update(kwargs2)
            return original(*args, **kwargs2)
        worker.headroom_run = capture
        return worker
    bundle['worker_factory'] = factory
    report = harness.run(bundle)
    assert seen['require_hardware'] is True
    assert report['gates'][-1]['require_hardware'] is True
    # A physical run checks hardware attestation in the `begin` ACK itself, so
    # the study stops there: no attempt is made and no frame is counted.
    assert report['terminal'] == 'RUN_STOPPED_headroom.begin'
    assert report['study']['attempts'] == 0
    assert report['observation_classes']['new_physical_frames'] == 0


def test_a_declared_physical_frame_is_labelled_a_new_physical_frame(tmp_path):
    """The criterion is the native reply: hardware_verified AND accepted."""
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    report = harness.run(dependencies=R.Dependencies(
        **dict(bundle, simulation=False, hardware_touched=True)))
    assert report['terminal'] == R.RUN_COMPLETED
    assert report['observation_classes']['new_physical_frames'] == 5
    assert report['observation_classes']['physical_attempts'] == [1, 2, 3, 4, 5]
    assert all(entry['observation_class'] == R.OBSERVATION_PHYSICAL
               for entry in report['attempts'])
    assert report['observation_classes']['narrative'].startswith('5 new physical frame')


def test_a_second_study_in_one_process_refuses_before_any_board_read(tmp_path,
                                                                   monkeypatch):
    monkeypatch.setattr(R, 'PROCESS_STUDY_SLOT', R.ClaimOnce())
    first = Harness(tmp_path / 'first')
    second = Harness(tmp_path / 'second', nonce='e' * 32)
    first_bundle, second_bundle = first.bundle(), second.bundle()
    for bundle in (first_bundle, second_bundle):
        bundle['claim_study_slot'] = None              # the process-wide claim
    assert first.run(first_bundle)['terminal'] == R.RUN_COMPLETED
    report = second.run(second_bundle)
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'single study slot' in report['refusal']['reason']
    assert second_bundle['storage_probe'].calls == []
    assert second_bundle['identity_probe'].calls == 0
    assert report['study_invoked'] is False


def test_an_injected_claim_lets_two_independent_sessions_run(tmp_path):
    first = Harness(tmp_path / 'first')
    second = Harness(tmp_path / 'second', nonce='e' * 32)
    assert first.run(first.bundle())['terminal'] == R.RUN_COMPLETED
    assert second.run(second.bundle())['terminal'] == R.RUN_COMPLETED


# ------------------------------------------------------ token discipline
def test_the_token_value_never_reaches_the_evidence(tmp_path):
    harness = Harness(tmp_path, token_text='s3cret-operator-token-value')
    report = harness.run()
    assert report['token_present'] is True
    assert report['token_consumed_at'].startswith('2026-10-12T20:00:0')
    text = harness.evidence_text()
    assert 's3cret-operator-token-value' not in text
    assert hashlib.sha256(b's3cret-operator-token-value').hexdigest() not in text
    assert harness.token.read_text(encoding='ascii') == 's3cret-operator-token-value'


def test_the_token_reader_is_the_release_own_read_control_token(tmp_path):
    harness = Harness(tmp_path)
    captured = []
    bundle = harness.bundle()
    release_reader = bundle['token_reader']

    def reader(path):
        captured.append(path)
        return release_reader(path)
    bundle['token_reader'] = reader
    assert harness.run(bundle)['terminal'] == R.RUN_COMPLETED
    assert captured == [str(harness.token)]
    assert Path(release_reader.__code__.co_filename).name == 'rf_status_worker.py'


# ------------------------------------------------------------- evidence model
def test_the_runner_records_the_committed_attempt_evidence_and_rehashes_it(tmp_path):
    harness = Harness(tmp_path)
    report = harness.run()
    assert [entry['attempt'] for entry in report['attempts']] == [1, 2, 3, 4, 5]
    for entry in report['attempts']:
        assert entry['intent'] and entry['header'] and entry['analysis']
        assert entry['replay'] and entry['decision']['accepted'] is True
        assert entry['iq_bytes'] == IQ_BYTES
        folder = harness.session_directory() / ('attempt-%d' % entry['attempt'])
        declared = json.loads((folder / 'header.json').read_text('utf-8'))
        assert entry['iq_sha256'] == declared['header']['headroom']['iq_sha256']
        assert entry['iq_sha256'] == hashlib.sha256(
            (folder / 'frame.iq').read_bytes()).hexdigest()
    assert report['observation_classes']['replayed_same_iq'] == 5
    assert report['replays'][0]['observation_class'] == R.OBSERVATION_REPLAY


def test_run_gate_json_sits_beside_the_recorder_receipt_without_changing_it(tmp_path):
    harness = Harness(tmp_path)
    report = harness.run()
    session = harness.session_directory()
    assert (session / 'receipt.json').is_file()
    assert (session / 'run-gate.json').is_file()
    gate = json.loads((session / 'run-gate.json').read_text('utf-8'))
    assert gate['terminal'] == R.RUN_COMPLETED and gate['retry_allowed'] is False
    assert gate['runner_sha256'] == hashlib.sha256(
        RUNNER_SOURCE.read_bytes()).hexdigest()
    assert report['gate_document_sha256'] == hashlib.sha256(
        (session / 'run-gate.json').read_bytes()).hexdigest()
    assert report['gate_document'] == str(session / 'run-gate.json')
    # The recorder's own schema is untouched: its frozen reader still reads it.
    study_reader = _load_by_path(CANDIDATE / 'rf_headroom.py', 'sealed_headroom_reader')
    study = study_reader.read_study(session)
    assert study['receipt']['outcome'] == 'COMPLETED'
    assert sorted(study['attempts']) == [1, 2, 3, 4, 5]


def test_a_refusal_before_the_study_writes_no_session_directory(tmp_path):
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(case='storage-fault'))
    assert not harness.session_directory().exists()
    refusal = harness.refusal_documents()
    assert refusal.is_file()
    document = json.loads(refusal.read_text('utf-8'))
    assert document['terminal'] == R.RUN_ABORTED_STORAGE_FAULT
    assert document['retry_allowed'] is False
    assert report['gate_document'] == str(refusal)
    assert not list(harness.evidence.rglob('receipt.json'))


def test_the_evidence_root_may_not_be_the_teaching_records_directory(tmp_path):
    harness = Harness(tmp_path)
    for bad in (tmp_path / 'experiments', tmp_path / 'experiments' / 'records'):
        bundle = harness.bundle()
        report = harness.run(bundle, evidence_root=bad)
        assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
        assert 'evidence root' in report['refusal']['reason']
        assert bundle['storage_probe'].calls == []
        # The refusal itself is recorded, but never as a study record: no
        # session directory and no receipt.json anywhere under that root.
        assert not (bad / SESSION).exists()
        assert not list(bad.rglob('receipt.json'))


def test_an_evidence_write_failure_from_the_chain_is_reported_as_such(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle()

    class Worker:
        client = None

        def headroom_status(self):
            return dict(available=True, reason='ok', release_id=R.QUALIFIED_RELEASE,
                        module_sha256={})

        def headroom_run(self, *args, **kwargs):
            error = OSError('the recorder could not write the terminal receipt')
            error.evidence_write_error = 'simulated evidence write failure'
            raise error
    bundle['worker_factory'] = lambda **kwargs: Worker()
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_EVIDENCE_WRITE_FAILED
    assert report['retry_allowed'] is False
    assert 'simulated evidence write failure' in report['evidence_write_error']
    assert report['exit_code'] == R.EXIT_STOPPED


def test_an_unwritable_evidence_root_is_reported_as_an_evidence_write_failure(tmp_path):
    harness = Harness(tmp_path)
    blocked = tmp_path / 'blocked'
    blocked.write_text('not a directory', encoding='utf-8')
    report = harness.run(harness.bundle(), evidence_root=blocked)
    assert report['terminal'] == R.RUN_EVIDENCE_WRITE_FAILED
    assert report['retry_allowed'] is False
    assert report['evidence_write_error']
    assert report['study_invoked'] is True


# ------------------------------- the POST-run storage read (F1)
class SecondReadFails:
    """Preflight succeeds; the post-run read fails or does not complete.

    This is the reviewer's counterexample shape (`review/counterexample-postrun-
    storage.py`): the study itself completes normally, so only the post-run read
    stands between it and a `RUN_COMPLETED` / exit 0 report.
    """

    def __init__(self, inner, *, error=None, complete=None):
        self.inner = inner
        self.error = error
        self.complete = complete
        self.calls = []
        self.first = None

    def __call__(self):
        index = len(self.calls)
        self.calls.append(index)
        if index == 0:
            self.first = dict(self.inner())
            return dict(self.first)
        if self.error is not None:
            raise self.error
        snapshot = dict(self.first)
        if self.complete is not None:
            snapshot['complete'] = self.complete
        return snapshot


class SecondReadMalformed:
    """The FIRST read is clean; the SECOND claims completeness without usable text."""

    def __init__(self, inner, text='drop'):
        self.inner = inner
        self.text = text
        self.calls = []

    def __call__(self):
        index = len(self.calls)
        self.calls.append(index)
        if index == 0:
            return self.inner()
        snapshot = dict(boot_id='boot-0001', complete=True)
        if self.text != 'drop':
            snapshot['kernel_storage'] = self.text
        return snapshot


def test_an_unreadable_post_run_storage_read_is_never_a_completion(tmp_path):
    """F1: a FAILED post-run read is not evidence of an error-free board."""
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    probe = SecondReadFails(bundle['storage_probe'],
                            error=OSError('dmesg is unavailable after the study'))
    bundle['storage_probe'] = probe
    report = harness.run(bundle)
    assert probe.calls == [0, 1]                       # preflight, then the failed read
    assert report['study']['completed'] is True        # the study itself did complete
    assert report['readback']['status'] == 'PASS'      # and the safety readback passed
    assert report['terminal'] != R.RUN_COMPLETED
    assert report['terminal'] == R.RUN_UNKNOWN_STORAGE_UNREADABLE
    assert report['exit_code'] == R.EXIT_STOPPED
    assert report['exit_code'] != 0
    assert report['retry_allowed'] is False
    postrun = report['storage']['postrun']
    assert postrun['preflight_status'] == 'UNREADABLE'
    assert postrun['error_type'] == 'OSError'
    # Not "no faults": the faults are unknown, and the report says so.
    assert postrun['faults'] is None
    assert postrun['faults_known'] is False
    assert 'NOT evidence of an error-free board' in report['reason']
    # ... and the boot-id/reboot comparison is reported as unknown, not skipped.
    assert postrun['boot_id'] is None
    assert report['storage']['reboot_determination'] == 'UNKNOWN'
    assert 'boot-id/reboot comparison' in report['reason']
    document = json.loads(harness.gate_document().read_text('utf-8'))
    assert document['terminal'] == R.RUN_UNKNOWN_STORAGE_UNREADABLE
    assert document['exit_code'] == R.EXIT_STOPPED
    assert document['storage']['postrun']['preflight_status'] == 'UNREADABLE'


def test_an_incomplete_post_run_storage_read_is_never_a_completion(tmp_path):
    """F1, second shape: the post-run read returned, but not completely."""
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    probe = SecondReadFails(bundle['storage_probe'], complete=False)
    bundle['storage_probe'] = probe
    report = harness.run(bundle)
    assert report['study']['completed'] is True
    assert report['terminal'] == R.RUN_UNKNOWN_STORAGE_UNREADABLE
    assert report['exit_code'] == R.EXIT_STOPPED
    assert report['storage']['postrun']['preflight_status'] == 'INCOMPLETE'
    assert report['storage']['postrun']['faults'] is None
    assert report['storage']['reboot_determination'] == 'UNKNOWN'
    assert 'NOT evidence of an error-free board' in report['reason']


def test_a_complete_post_run_read_without_kernel_text_is_never_clean(tmp_path):
    """Review finding N1, first shape: complete, but carrying no kernel text.

    The PRE-run read already refuses exactly this shape ('returned no kernel
    text'), so the post-run read may not report it as an error-free board: with
    an empty fault list the study was still labelled RUN_COMPLETED.
    """
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    probe = SecondReadMalformed(bundle['storage_probe'])
    bundle['storage_probe'] = probe
    report = harness.run(bundle)
    assert probe.calls == [0, 1]
    assert report['study']['completed'] is True
    assert report['terminal'] == R.RUN_UNKNOWN_STORAGE_UNREADABLE
    assert report['exit_code'] == R.EXIT_STOPPED
    postrun = report['storage']['postrun']
    assert postrun['preflight_status'] == 'UNREADABLE'
    assert postrun['faults'] is None
    assert postrun['faults_known'] is False
    # This snapshot DID carry a boot id, so the reboot comparison is answered
    # (SAME) rather than disarmed; what is unknown here is the fault list only.
    # The preflight keeps the boot id on the same shape, for the same reason.
    assert report['storage']['reboot_determination'] == 'SAME'
    assert 'NOT evidence of an error-free board' in report['reason']
    document = json.loads(harness.gate_document().read_text('utf-8'))
    assert document['terminal'] == R.RUN_UNKNOWN_STORAGE_UNREADABLE


def test_a_post_run_read_with_non_string_kernel_text_is_a_reported_refusal(tmp_path):
    """Review finding N1, second shape: complete, with a non-string text.

    storage_faults() raises TypeError on bytes, and that exception used to escape
    run_research entirely, leaving no run-gate.json at all - the same reported
    outcome break as F2, in the post-run half of the same rule.
    """
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    probe = SecondReadMalformed(bundle['storage_probe'], text=b'not kernel text')
    bundle['storage_probe'] = probe
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_UNKNOWN_STORAGE_UNREADABLE
    assert report['exit_code'] == R.EXIT_STOPPED
    assert report['storage']['postrun']['preflight_status'] == 'UNREADABLE'
    assert report['storage']['postrun']['faults'] is None
    document = json.loads(harness.gate_document().read_text('utf-8'))
    assert document['terminal'] == R.RUN_UNKNOWN_STORAGE_UNREADABLE


def test_a_clean_post_run_storage_read_still_completes(tmp_path):
    """The control: the fix must not turn a clean read-back into an unknown."""
    report = Harness(tmp_path).run()
    assert report['terminal'] == R.RUN_COMPLETED
    assert report['storage']['postrun']['preflight_status'] == 'CLEAN'
    assert report['storage']['postrun']['faults'] == []
    assert report['storage']['postrun']['faults_known'] is True
    assert report['storage']['reboot_determination'] == 'SAME'


def test_a_post_run_storage_fault_is_an_abort_and_not_an_unknown_state(tmp_path):
    """'faults detected' and 'storage health unreadable' must stay distinguishable."""
    faulty = Harness(tmp_path / 'fault')
    detected = faulty.run(faulty.bundle(second_kernel_text=sim.FAULT_KERNEL))
    assert detected['terminal'] == R.RUN_ABORTED_STORAGE_FAULT
    assert detected['exit_code'] == R.EXIT_NOT_STARTED
    assert detected['storage']['postrun']['preflight_status'] == 'FAULTS_DETECTED'
    assert detected['storage']['postrun']['faults']
    assert detected['study']['completed'] is True

    unknown = Harness(tmp_path / 'unreadable')
    bundle = unknown.bundle()
    bundle['storage_probe'] = SecondReadFails(bundle['storage_probe'],
                                              error=OSError('dmesg is gone'))
    report = unknown.run(bundle)
    assert report['terminal'] == R.RUN_UNKNOWN_STORAGE_UNREADABLE
    assert report['terminal'] != detected['terminal']
    assert report['exit_code'] != detected['exit_code']
    assert report['storage']['postrun']['faults'] is None


# ----------------------------------------------- the independent read-back
def test_the_readback_is_a_separate_read_only_probe_after_the_study(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    order = []
    inner_readback = bundle['readback_probe']
    inner_factory = bundle['worker_factory']

    def readback(expected):
        order.append(('readback', expected))
        return inner_readback(expected)

    def factory(**kwargs):
        order.append(('study-worker', None))
        return inner_factory(**kwargs)
    bundle['readback_probe'] = readback
    bundle['worker_factory'] = factory
    report = harness.run(bundle)
    assert order[0][0] == 'study-worker'
    assert order[-1] == ('readback', R.QUALIFIED_RELEASE)
    assert inner_readback.calls == [dict(expected_release=R.QUALIFIED_RELEASE)]
    assert report['readback']['status'] == 'PASS'
    assert report['readback']['verified_state'] is True
    assert 'separate read-only session' in report['readback']['source']
    assert report['readback']['headroom']['used'] is False
    assert report['readback']['headroom']['active'] is False


def test_a_refusal_before_the_study_never_opens_a_readback(tmp_path):
    harness = Harness(tmp_path)
    bundle = harness.bundle(case='storage-fault')
    report = harness.run(bundle)
    assert bundle['readback_probe'].calls == []
    assert report['readback']['status'] == 'NOT_ATTEMPTED'


def test_an_incomplete_readback_makes_the_conclusion_unknown(tmp_path):
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(case='readback-missing'))
    assert report['terminal'] == R.RUN_UNKNOWN_READBACK_INCOMPLETE
    assert report['retry_allowed'] is False
    assert report['readback']['status'] == 'UNAVAILABLE'
    # The study's own receipt is preserved, and is NOT the conclusion.
    assert report['study']['completed'] is True
    assert report['terminal'] != R.RUN_COMPLETED


def test_a_readback_that_is_not_the_verified_state_is_not_a_completion(tmp_path):
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(readback_verified=False))
    assert report['terminal'] == R.RUN_UNKNOWN_READBACK_INCOMPLETE
    assert report['readback']['status'] == 'UNVERIFIED'
    assert report['readback']['verified_state'] is False
    assert report['study']['completed'] is True


def test_a_board_that_was_used_and_left_active_is_reported_as_such(tmp_path):
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(readback_used=True, readback_active=True))
    assert report['readback']['headroom'] == dict(
        used=True, active=True,
        operations=['headroom.begin', 'headroom.load', 'headroom.capture',
                    'headroom.accept', 'headroom.abort'])
    assert report['zero_frame_claim']['facts']['board_reports_never_used'] is False
    assert report['zero_frame_claim']['supported'] is False


# ------------------------------------------------------- terminal labels
@pytest.mark.parametrize('case,scenario,label,exit_code', [
    ('full-five', 'real-lease-semantics', R.RUN_COMPLETED, 0),
    ('clipped', 'real-lease-semantics', 'RUN_STOPPED_CLIPPED', 3),
    ('off-violation', 'real-lease-semantics', 'RUN_STOPPED_OFF_EVIDENCE', 3),
    ('disconnect', 'real-lease-semantics', R.RUN_UNKNOWN_DISCONNECT, 3),
    ('interrupted', 'real-lease-semantics', R.RUN_INTERRUPTED_MID_ATTEMPT, 3),
    ('full-five', 'historical-lease-false', R.RUN_REFUSED_CAPABILITY, 2),
    ('storage-fault', 'real-lease-semantics', R.RUN_ABORTED_STORAGE_FAULT, 2),
    ('storage-unreadable', 'real-lease-semantics', R.RUN_NOT_STARTED_PREFLIGHT, 2),
    ('reboot', 'real-lease-semantics', R.RUN_UNKNOWN_REBOOT, 3),
    ('readback-missing', 'real-lease-semantics', R.RUN_UNKNOWN_READBACK_INCOMPLETE, 3),
    # F1: the study completed, but the POST-run storage read did not.
    ('postrun-storage-unreadable', 'real-lease-semantics',
     R.RUN_UNKNOWN_STORAGE_UNREADABLE, 3),
    ('postrun-storage-incomplete', 'real-lease-semantics',
     R.RUN_UNKNOWN_STORAGE_UNREADABLE, 3),
])
def test_every_stop_path_gets_a_runner_label_with_retry_allowed_false(
        tmp_path, case, scenario, label, exit_code):
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(case=case, scenario=scenario))
    assert report['terminal'] == label, (report['terminal'], report['reason'])
    assert report['retry_allowed'] is False
    assert report['exit_code'] == exit_code
    assert (report['terminal'] in R.FIXED_TERMINAL_LABELS
            or report['terminal'].startswith(R.STOPPED_LABEL_PREFIX))


def test_the_historical_lease_defect_is_refused_with_zero_attempts(tmp_path):
    """T2 of the design, at the runner level: the 2026-10-11 defect."""
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(scenario='historical-lease-false'))
    assert report['terminal'] == R.RUN_REFUSED_CAPABILITY
    assert report['study']['attempts'] == 0
    assert report['study']['stopped_at'] == 'CAPABILITY'
    assert report['study_invoked'] is True
    assert report['readback']['status'] == 'PASS'


def test_the_label_mapping_is_exact_for_every_state_the_run_can_be_in():
    label = R.terminal_label
    assert label({})[0] == R.RUN_NOT_STARTED_PREFLIGHT
    assert label(dict(evidence_write_error='x'))[0] == R.RUN_EVIDENCE_WRITE_FAILED
    assert label(dict(post_storage=dict(faults=['mmc0: error'])))[0] \
        == R.RUN_ABORTED_STORAGE_FAULT
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      receipt=dict(completed=True)))[0] == R.RUN_COMPLETED
    assert label(dict(study_invoked=True, readback=dict(status='UNAVAILABLE'),
                      receipt=dict(completed=True)))[0] == R.RUN_UNKNOWN_READBACK_INCOMPLETE
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      boot_id_before='a', boot_id_after='b',
                      receipt=dict(completed=True)))[0] == R.RUN_UNKNOWN_REBOOT
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      interrupted_attempts=[2],
                      receipt=dict(completed=True)))[0] == R.RUN_INTERRUPTED_MID_ATTEMPT
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      disconnect_markers=['rf daemon disconnected'],
                      receipt=dict(completed=True)))[0] == R.RUN_UNKNOWN_DISCONNECT
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      receipt=dict(stopped_at='CAPABILITY')))[0] == R.RUN_REFUSED_CAPABILITY
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      receipt=dict(stopped_at='UNAVAILABLE')))[0] == R.RUN_REFUSED_CAPABILITY
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      receipt=dict(stopped_at='STOPPED')))[0] == R.RUN_REFUSED_CAPABILITY
    # A post-run storage read that did not complete is an unknown outcome, in
    # every combination -- including "the study completed AND the readback passed".
    for status in R.POST_STORAGE_UNKNOWN_STATUSES:
        assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                          post_storage=dict(preflight_status=status, faults=None),
                          receipt=dict(completed=True)))[0] \
            == R.RUN_UNKNOWN_STORAGE_UNREADABLE
        assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                          post_storage=dict(preflight_status=status, faults=None),
                          receipt=dict(completed=True)))[1]
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      post_storage=dict(preflight_status='CLEAN', faults=[]),
                      receipt=dict(completed=True)))[0] == R.RUN_COMPLETED
    assert R.reboot_determination(None, 'boot-0001') == 'UNKNOWN'
    assert R.reboot_determination('boot-0001', None) == 'UNKNOWN'
    assert R.reboot_determination('boot-0001', 'boot-0001') == 'SAME'
    assert R.reboot_determination('boot-0001', 'boot-0002') == 'CHANGED'
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      receipt=dict(stopped_at='EVIDENCE:2')))[0] == 'RUN_STOPPED_EVIDENCE:2'
    assert label(dict(study_invoked=True, readback=dict(status='PASS'),
                      receipt=dict(stopped_at='CLOSE_UNCONFIRMED')))[0] \
        == 'RUN_STOPPED_CLOSE_UNCONFIRMED'
    assert label(dict(gate_label=R.RUN_REFUSED_CAPABILITY))[0] == R.RUN_REFUSED_CAPABILITY
    assert R.exit_code(R.RUN_COMPLETED) == 0
    assert R.exit_code('RUN_STOPPED_X') == R.EXIT_STOPPED
    assert R.exit_code(R.RUN_ABORTED_STORAGE_FAULT) == R.EXIT_NOT_STARTED


def test_a_stop_label_never_invents_a_controller_state():
    assert R.stopped_label('CLIPPED') == 'RUN_STOPPED_CLIPPED'
    assert R.stopped_label('headroom.capture:2') == 'RUN_STOPPED_headroom.capture:2'
    assert R.stopped_label('nonsense value!') == 'RUN_STOPPED_UNKNOWN'
    assert R.stopped_label(None) == 'RUN_STOPPED_UNKNOWN'


def test_stopped_is_a_pre_attempt_refusal_like_the_design_says():
    """F6: the design's start-up refusal set is UNAVAILABLE, CAPABILITY, STOPPED.

    A `stopped_at='STOPPED'` receipt consumes no attempt -- the frozen worker's own
    `_HEADROOM_NOT_ATTEMPTED` names the same three values -- so it must be labelled
    a refusal (with the zero-attempt exit code) and it may support the zero-frame
    claim, instead of becoming `RUN_STOPPED_STOPPED`.  The frozen controller does
    not currently emit that value; the set is aligned with the design anyway, and
    the report says so.
    """
    assert R.PRE_ATTEMPT_REFUSALS == ('UNAVAILABLE', 'CAPABILITY', 'STOPPED')
    label, reason = R.terminal_label(dict(study_invoked=True, readback=dict(status='PASS'),
                                          receipt=dict(stopped_at='STOPPED')))
    assert label == R.RUN_REFUSED_CAPABILITY
    assert R.exit_code(label) == R.EXIT_NOT_STARTED
    assert reason == 'the chain refused before any attempt'
    claim = R.zero_frame_claim([], dict(stopped_at='STOPPED'),
                               dict(headroom=dict(used=False, active=False)), True)
    assert claim['facts']['study_receipt_is_a_refusal'] is True
    assert claim['supported'] is True and claim['basis'] == 'BOARD_READBACK'
    # CANCELLED is NOT a start-up refusal: that study ran and was stopped.
    assert 'CANCELLED' not in R.PRE_ATTEMPT_REFUSALS
    assert R.stopped_label('CANCELLED') == 'RUN_STOPPED_CANCELLED'
    # The design's set is the frozen worker's own set, not an invention.
    worker = (SW / 'rf_status_worker.py').read_text(encoding='utf-8')
    assert ("_HEADROOM_NOT_ATTEMPTED = ('UNAVAILABLE', 'CAPABILITY', 'STOPPED')"
            in worker)


def test_a_deadline_is_not_reported_as_a_disconnect():
    """Only the frozen client's own lost-connection strings mean a disconnect."""
    assert R.disconnect_evidence(dict(reason='RF daemon disconnected'), []) == \
        ['rf daemon disconnected']
    assert R.disconnect_evidence(dict(reason='research connection was lost; no reconnect'),
                                 []) == ['research connection was lost; no reconnect']
    assert R.disconnect_evidence(dict(reason='the capture deadline expired'), []) == []
    assert R.disconnect_evidence(
        dict(reason=''), [dict(transport=dict(complete=False,
                                             error='deadline crossed during recv'))]) == []
    assert R.disconnect_evidence(
        dict(reason=''),
        [dict(transport=dict(complete=False,
                             error='RF daemon disconnected during the IQ payload'))]) \
        == ['rf daemon disconnected']


def test_an_announced_attempt_without_a_decision_is_detected(tmp_path):
    session = tmp_path / 'session'
    (session / 'attempt-1').mkdir(parents=True)
    (session / 'attempt-1' / 'intent.json').write_text('{}', encoding='utf-8')
    (session / 'attempt-1' / 'decision.json').write_text(
        json.dumps(dict(accepted=True)), encoding='utf-8')
    (session / 'attempt-2').mkdir()
    (session / 'attempt-2' / 'intent.json').write_text('{}', encoding='utf-8')
    (session / 'attempt-2' / 'header.json').write_text(
        json.dumps(dict(iq_available=False, transport=dict(complete=False))),
        encoding='utf-8')
    attempts = R.recorded_attempts(session)
    assert [entry['attempt'] for entry in attempts] == [1, 2]
    assert R.interrupted_attempts(attempts) == [2]
    assert attempts[1]['observation_class'] is None
    assert attempts[0]['observation_class'] is None


def test_a_corrupt_recorded_header_does_not_crash_the_evidence_index(tmp_path):
    session = tmp_path / 'session'
    (session / 'attempt-1').mkdir(parents=True)
    (session / 'attempt-1' / 'intent.json').write_text('{}', encoding='utf-8')
    (session / 'attempt-1' / 'header.json').write_text('{oops', encoding='utf-8')
    (session / 'attempt-1' / 'frame.iq').write_bytes(b'\x00' * 16)
    attempts = R.recorded_attempts(session)
    assert attempts[0]['read_error'].startswith('header.json')
    assert attempts[0]['hardware_verified'] is None


# --------------------------------------------- the "no new physical frame" story
def test_a_physical_observation_needs_both_native_attestation_and_acceptance():
    base = dict(attempt=1, decision=dict(accepted=True), hardware_verified=True)
    assert R._observation_class(dict(base, iq_bytes=IQ_BYTES)) == R.OBSERVATION_PHYSICAL
    assert R._observation_class(dict(base, iq_bytes=IQ_BYTES, hardware_verified=False)) \
        == R.OBSERVATION_SIMULATED
    assert R._observation_class(dict(base, iq_bytes=IQ_BYTES,
                                     decision=dict(accepted=False))) \
        == R.OBSERVATION_SIMULATED
    assert R._observation_class(dict(base, iq_bytes=None)) is None


@pytest.mark.parametrize('missing', ['no_accepted_decision', 'no_attested_accepted_frame',
                                     'board_reports_never_used',
                                     'study_receipt_is_a_refusal'])
def test_the_zero_frame_claim_needs_all_four_independent_facts(missing):
    """Break one fact at a time; the claim must notice and name it."""
    attempts = []
    readback = dict(headroom=dict(used=False, active=False))
    receipt = dict(stopped_at='CAPABILITY')
    if missing == 'no_accepted_decision':
        attempts = [dict(attempt=1, decision=dict(accepted=True),
                         observation_class=R.OBSERVATION_SIMULATED, iq_bytes=IQ_BYTES)]
    elif missing == 'no_attested_accepted_frame':
        attempts = [dict(attempt=1, decision=dict(accepted=True),
                         observation_class=R.OBSERVATION_PHYSICAL, iq_bytes=IQ_BYTES)]
    elif missing == 'board_reports_never_used':
        readback = dict(headroom=dict(used=True, active=False))
    elif missing == 'study_receipt_is_a_refusal':
        receipt = dict(completed=True, stopped_at=None)
    claim = R.zero_frame_claim(attempts, receipt, readback, True)
    assert claim['facts'][missing] is False
    assert claim['supported'] is False
    assert missing in claim['reason']
    full = R.zero_frame_claim([], dict(stopped_at='CAPABILITY'),
                              dict(headroom=dict(used=False, active=False)), True)
    assert full['supported'] is True and full['basis'] == 'BOARD_READBACK'


def test_no_board_contact_is_its_own_zero_frame_basis():
    claim = R.zero_frame_claim([], {}, {}, False)
    assert claim['supported'] is True
    assert claim['basis'] == 'NO_BOARD_CONTACT'
    assert claim['reason'] == 'no research session was opened, so no frame can exist'


def test_the_narrative_matches_the_numbers_for_a_simulated_completed_study(tmp_path):
    report = Harness(tmp_path).run()
    classes = report['observation_classes']
    assert classes['new_physical_frames'] == 0
    assert classes['physical_suppressed_by_simulation'] == 5
    assert 'declared simulation' in classes['narrative']
    assert classes['simulation'] is True
    assert report['zero_frame_claim']['supported'] is False


def test_the_narrative_matches_the_numbers_for_a_refused_study(tmp_path):
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(scenario='historical-lease-false'))
    assert report['terminal'] == R.RUN_REFUSED_CAPABILITY
    assert report['zero_frame_claim']['supported'] is True
    assert report['zero_frame_claim']['basis'] == 'BOARD_READBACK'
    assert report['observation_classes']['narrative'].startswith('0 new physical frames')


def test_the_narrative_for_a_preflight_refusal_says_no_board_contact(tmp_path):
    harness = Harness(tmp_path)
    report = harness.run(harness.bundle(case='storage-fault'))
    assert report['zero_frame_claim']['basis'] == 'NO_BOARD_CONTACT'
    assert report['observation_classes']['narrative'] == \
        'no study was started, so there is no frame of any class'


# --------------------------------------------------- staging prefix isolation
def test_the_runner_staging_prefix_is_not_the_installer_prefix():
    installer = (REPO / 'scripts/rf_teaching_install.sh').read_text(encoding='utf-8')
    guard = [line for line in installer.splitlines() if line.strip().startswith('case "')]
    assert guard, 'the installer guard is the source of truth and must be present'
    prefix = guard[0].split('in ')[1].split('*')[0].strip()
    assert prefix == R.INSTALLER_STAGING_PREFIX
    staging = R.staging_directory('a' * 32)
    assert staging.startswith(R.RESEARCH_STAGING_PREFIX)
    assert fnmatch.fnmatchcase(staging, prefix + '*') is False
    assert fnmatch.fnmatchcase(prefix + 'x', prefix + '*') is True
    assert R.assert_prefix_separation() is True


def test_the_prefix_guard_rejects_the_installer_prefix():
    # Each `pytest.raises` names the ONE message the case intends, so a pass for
    # the wrong reason (the project has been burned by that before) fails here.
    with pytest.raises(AssertionError,
                       match='must not overlap the installer prefix'):
        R.assert_prefix_separation(R.INSTALLER_STAGING_PREFIX + 'x-',
                                   R.INSTALLER_STAGING_PREFIX)
    with pytest.raises(AssertionError,
                       match='staging prefixes are absolute /tmp/ prefixes'):
        R.assert_prefix_separation('/home/xilinx/research-', R.INSTALLER_STAGING_PREFIX)
    with pytest.raises(ValueError, match='attempt_id must be 32 lowercase hex characters'):
        R.staging_directory('not-hex')


def test_the_runner_never_dispatches_an_installation_or_opens_its_own_socket():
    """The transport-free stage's invariant, RESTATED for the wired stage.

    What must stay true: no ssh/paramiko, no socket of its own, no installation or
    job dispatch.  subprocess is now used for exactly one thing -- the local
    kernel storage read -- so this test pins WHERE it may appear.
    """
    source = RUNNER_SOURCE.read_text(encoding='utf-8')
    for forbidden in ('systemd-run', 'systemd_run', 'rf_teaching_install.sh',
                      'rf_headroom_job', 'install.sh', 'paramiko',
                      'socket.socket(', 'socket.create_connection('):
        assert forbidden not in source, forbidden
    tree = ast.parse(source)
    imported, from_scripts = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
            if node.module.startswith('scripts'):
                from_scripts.update(alias.name for alias in node.names)
    assert not ({'socket', 'paramiko', 'shutil', 'pty', 'telnetlib',
                 'rf_headroom', 'rf_headroom_replay', 'rf_status_client'} & imported), \
        sorted(imported)
    assert 'subprocess' in imported
    users = [node.name for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef)
             and any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                     and n.value.id == 'subprocess' for n in ast.walk(node))]
    assert users == ['local_kernel_storage'], users
    # The only host tools it reuses are reviewed, read-only ones: the storage
    # predicate and the append-only ledger, the installed-state verdict ruler,
    # and the canonical release verifier.  Nothing from the installer, the job
    # dispatcher or the deployment execution path.
    assert from_scripts == {'AttemptJournal', 'KERNEL_FAULT_KEYS', 'storage_faults',
                            'verified_state', 'merged_release', 'inspect_state'}, \
        sorted(from_scripts)


def test_the_runner_never_calls_the_research_operations_the_controller_owns():
    """The controller owns every research operation; the runner calls one entry.

    The transport seam is the ONE place the installed release's client may be
    built (the runner still opens no socket itself), so this test pins the seam's
    location instead of forbidding the class name outright.
    """
    source = RUNNER_SOURCE.read_text(encoding='utf-8')
    for forbidden in ('HeadroomController(', '.headroom_begin(', '.headroom_load(',
                      '.headroom_capture(', '.headroom_accept(', '.headroom_abort(',
                      '.headroom_lease(', 'headroom_capability('):
        assert forbidden not in source, forbidden
    assert source.count('.headroom_run(') == 1
    tree = ast.parse(source)
    builders = [node.name for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and any(isinstance(n, ast.Attribute) and n.attr == 'RfStatusClient'
                        for n in ast.walk(node))]
    assert builders == ['release_client'], builders


def test_a_replayed_capture_identity_is_refused_by_the_frozen_chain(tmp_path):
    """A gap in the frozen suites: nothing asserted the replay-identity dedupe.

    The runner's `EVIDENCE:N` label and its interruption detection both rest on
    the controller's own `_seen_ids` / `_seen_stamps` refusal, so the runner's
    suite covers the one frozen behaviour its labels depend on and no frozen
    file asserts.  It is not a re-run of T1/T2/T3/T7-T12.
    """
    harness = Harness(tmp_path)

    class Replaying(sim.SimClient):
        def headroom_capture(self, args):
            frame = sim.SimClient.headroom_capture(self, args)
            if args['attempt'] == 1:
                self.first_capture_id = frame.request_id
            elif args['attempt'] == 2:
                # The frame is well formed and internally consistent, but it
                # carries attempt 1's native request id: it cannot have come
                # from this attempt, and only the replay-identity dedupe says so.
                header = sim.T._roundtrip(frame.header)
                header['id'] = self.first_capture_id
                header['headroom']['capture_request_id'] = self.first_capture_id
                return self._module.ResearchFrame(self.first_capture_id, header,
                                                  frame.payload, frame.transport)
            return frame
    report = harness.run(harness.bundle(client=Replaying()))
    assert report['terminal'] == 'RUN_STOPPED_EVIDENCE:2'
    assert report['study']['attempts'] == 1
    decisions = {entry['attempt']: entry['decision'] for entry in report['attempts']}
    assert decisions[2]['accepted'] is False
    assert 'replayed capture identity' in decisions[2]['detail']


# ------------------------------------------- the real frozen chain, in memory
def test_the_release_worker_factory_builds_the_class_from_the_release(tmp_path):
    client = sim.SimClient()
    worker = R.release_worker_factory(str(CANDIDATE), str(tmp_path / 'experiments'),
                                      client=client, timeout_s=10)
    try:
        assert type(worker).__module__ == 'release_rf_status_worker'
        assert worker.headroom_status()['available'] is True
        assert worker.headroom_status()['release_id'] == R.QUALIFIED_RELEASE
        assert worker._thread is None or not worker._thread.is_alive()
        assert worker._stop.is_set()
        assert client.calls == []                 # no research operation was sent
    finally:
        client.close()


def test_the_real_client_runs_the_whole_study_over_one_in_memory_connection(tmp_path,
                                                                           monkeypatch):
    """The frozen client, over the sealed chain's in-memory peer: no socket."""
    peer = sim.chain_peer(release_directory=str(CANDIDATE))
    addresses = []

    def connect(address, timeout=None):
        addresses.append(address)
        return peer.connect(address, timeout)
    monkeypatch.setattr(socket, 'create_connection', connect)

    harness = Harness(tmp_path)
    client = sim.RfStatusClient(port=41999, timeout=2.0, control_timeout=5.0,
                                capture_timeout=5.0)
    report = harness.run(harness.bundle(client=client))
    assert report['terminal'] == R.RUN_COMPLETED
    assert addresses == [('127.0.0.1', 41999)]
    assert peer.connections == 1
    assert report['release']['connection_generation'] == dict(before=0, after=1)
    assert report['release']['connection_generation_delta'] == 1
    assert report['release']['single_connection_no_reconnect'] is True
    assert report['release']['research_owner_released'] is True
    assert report['observation_classes']['new_physical_frames'] == 0
    assert report['observation_classes']['replayed_same_iq'] == 5
    assert 'headroom.begin' in peer.requests and 'control.arm' in peer.requests
    assert peer.requests.count('headroom.capture') == 5
    assert peer.armed is False and peer.active is False


def test_a_reconnect_style_generation_jump_is_not_reported_as_one_connection(tmp_path):
    harness = Harness(tmp_path)

    class Jumping(sim.SimClient):
        @contextlib.contextmanager
        def headroom_lease(self):
            self._connection_generation += 2
            yield
    report = harness.run(harness.bundle(client=Jumping()))
    assert report['terminal'] == R.RUN_COMPLETED
    assert report['release']['connection_generation_delta'] == 2
    assert report['release']['single_connection_no_reconnect'] is False


# ------------------------------------------------------------------- the CLI
BYPASS_FLAGS = ('--force', '--allow-unqualified', '--skip-preflight', '--attempts',
                '--attenuation-mdb', '--ignore-wiring-age', '--any-attempts',
                '--no-readback')


def _cli_arguments(harness, action):
    return [action, '--authorization', str(harness.authorization_path),
            '--token', str(harness.token), '--wiring-confirmed', R.WIRING_STATEMENT,
            '--release-directory', str(CANDIDATE),
            '--evidence-root', str(harness.evidence), '--ledger', str(harness.ledger),
            '--attempt-id', 'f' * 32]


@pytest.mark.parametrize('flag', BYPASS_FLAGS)
def test_the_cli_rejects_every_bypass_switch(tmp_path, flag, capsys):
    harness = Harness(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        R.main(_cli_arguments(harness, 'run') + [flag])
    assert exit_info.value.code == 2
    # ... and the refusal is argparse rejecting THIS flag, not another error:
    # `SystemExit` alone would also be raised by --help or by a bad path.
    message = capsys.readouterr().err
    assert 'unrecognized arguments' in message
    assert flag in message


def test_the_cli_parser_offers_no_way_to_change_the_contract():
    import argparse
    parser = R.build_parser()
    subparsers = [action for action in parser._actions
                  if isinstance(action, argparse._SubParsersAction)]
    assert len(subparsers) == 1
    options = set()
    for action in subparsers[0].choices.values():
        options.update(action._option_strings if hasattr(action, '_option_strings')
                       else [name for item in action._actions
                             for name in item.option_strings])
    assert not [name for name in options if name in BYPASS_FLAGS]
    for fixed in ('--attempts', '--attenuation-mdb', '--settle-wait-us',
                  '--scenario-order', '--sixth', '--alignment'):
        assert fixed not in options
    assert '--case' in options and '--scenario' in options      # simulate only
    assert '--fake-chain' in options


def test_the_cli_run_wires_the_real_transport_and_still_refuses_offline(tmp_path,
                                                                       monkeypatch):
    """The `run` subcommand now uses the board-side production bundle.

    This host has no board, so the local kernel storage read fails and G1 refuses
    BEFORE any study: wiring the transport must not weaken fail-closed.  The
    session's trusted clock anchor is supplied and the board's own /proc read is
    injected, so G0 passes and the assertion below is still about G1 (contract
    section 8: without the anchor this very run refuses one gate earlier, by name).
    """
    harness = Harness(tmp_path, now=datetime.datetime.now(datetime.timezone.utc))
    monkeypatch.setattr(R, 'local_board_clock',
                        lambda: dict(boot_id='board-boot-id', uptime_s=1000.0))
    code = R.main(_cli_arguments(harness, 'run') + [
        '--host-utc', harness.now.isoformat(), '--anchor-boot-id', 'board-boot-id',
        '--anchor-uptime-s', '1000.0'])
    assert code == R.EXIT_NOT_STARTED
    document = json.loads(harness.refusal_documents('f' * 32).read_text('utf-8'))
    assert document['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert document['study_invoked'] is False
    assert document['retry_allowed'] is False
    assert not (harness.evidence / SESSION).exists()
    gate = [g for g in document['gates'] if g['gate'] == R.GATE_STORAGE][0]
    assert gate['passed'] is False and gate['board_read'] is True


def test_the_cli_simulate_runs_the_whole_pipeline_with_a_declared_fake_chain(tmp_path):
    harness = Harness(tmp_path)
    code = R.main(_cli_arguments(harness, 'simulate')
                  + ['--fake-chain', str(SIM_CHAIN), '--case', 'full-five',
                     '--scenario', 'real-lease-semantics'])
    assert code == R.EXIT_COMPLETED
    document = json.loads(harness.gate_document().read_text('utf-8'))
    assert document['terminal'] == R.RUN_COMPLETED
    assert document['simulation'] is True and document['hardware_touched'] is False
    assert document['observation_classes']['new_physical_frames'] == 0
    assert document['readback']['status'] == 'PASS'
    assert document['gates'][-1]['calls'] == 1


def test_the_cli_simulate_reflects_the_historical_lease_defect(tmp_path):
    harness = Harness(tmp_path)
    code = R.main(_cli_arguments(harness, 'simulate')
                  + ['--fake-chain', str(SIM_CHAIN), '--case', 'full-five',
                     '--scenario', 'historical-lease-false'])
    assert code == R.EXIT_NOT_STARTED
    document = json.loads(harness.gate_document().read_text('utf-8'))
    assert document['terminal'] == R.RUN_REFUSED_CAPABILITY
    assert document['study']['attempts'] == 0
    assert document['study']['stopped_at'] == 'CAPABILITY'


def test_a_fake_chain_that_does_not_declare_a_simulation_is_refused(tmp_path):
    module_path = tmp_path / 'bad-chain.py'
    module_path.write_text(
        'def dependencies(**kwargs):\n'
        '    return dict(simulation=False, hardware_touched=False)\n', encoding='utf-8')
    module = _load_by_path(module_path, 'bad_chain')
    with pytest.raises(ValueError, match='simulation=True'):
        R.simulate_dependencies(module, 'full-five', 'real-lease-semantics', 'a' * 32,
                                str(tmp_path), str(CANDIDATE), str(tmp_path / 'l.jsonl'))
    missing = tmp_path / 'no-deps.py'
    missing.write_text('X = 1\n', encoding='utf-8')
    with pytest.raises(ValueError, match='dependencies'):
        R.simulate_dependencies(_load_by_path(missing, 'no_deps'), 'full-five',
                                'real-lease-semantics', 'a' * 32, str(tmp_path),
                                str(CANDIDATE), str(tmp_path / 'l.jsonl'))


# ------------------------- frozen coverage the frozen suites themselves miss
def test_the_rail_terminal_equivalence_is_the_reason_a_frame_is_refused():
    """F02 coverage (2026-10-13): the equivalence branch at rf_headroom.py:442.

    The frozen suites assert only "rail" in str(...) -- a substring of BOTH
    possible messages -- and all three of their parametrised cases break the
    declared rail COUNT as well, so the count check refuses them first.  Removing
    the equivalence branch therefore survived all 155 frozen tests (mutation F02).
    Here the declared count is honest, so only the equivalence can refuse.
    """
    H = sim.T.H
    railed = sim.T._frame(rails_at=2,
                          corrupt=sim.T._rails_only(2 * H.CAPTURE_SAMPLES, 'AWAIT_ACCEPT'))
    with pytest.raises(H.HeadroomEvidenceError, match='terminal state does not match'):
        H.check_research_frame(H.HeadroomPlan(), sim.T.SESSION, 2, railed)

    clean = sim.T._frame(rails_at=None, corrupt=sim.T._rails_only(0, 'CLIPPED'))
    with pytest.raises(H.HeadroomEvidenceError, match='terminal state does not match'):
        H.check_research_frame(H.HeadroomPlan(), sim.T.SESSION, 2, clean)


def test_a_lost_research_connection_is_never_reconnected(monkeypatch):
    """F11 coverage (2026-10-13): the reconnect guard at rf_status_client.py:467.

    No frozen test exercises it, so removing the guard survived all 155 frozen
    tests (mutation F11).  The guard is defence in depth: the frozen controller
    never retries after a lost link, so the only way to reach it is the exact
    state a lost study leaves behind -- the generation recorded by
    headroom_lease() (:508) with the socket already gone -- and then an ordinary
    read must refuse by name instead of silently opening a second connection.
    """
    peer = sim.chain_peer(release_directory=str(CANDIDATE))
    addresses = []

    def connect(address, timeout=None):
        addresses.append(address)
        return peer.connect(address, timeout)
    monkeypatch.setattr(socket, 'create_connection', connect)

    client = sim.RfStatusClient(port=41999, timeout=2.0, control_timeout=5.0,
                                capture_timeout=5.0)
    try:
        client.poll()                                   # the one live connection
        assert addresses == [('127.0.0.1', 41999)]
        generation = client._connection_generation
        client._research_generation = generation         # what the lease records
        client._drop_session()                           # and then the link dies
        snapshot = client.poll()                         # fail-soft: an error snapshot
        assert snapshot.online is False
        assert 'no reconnect' in (snapshot.error or '')
        assert addresses == [('127.0.0.1', 41999)], 'a second connection was opened'
    finally:
        client.close()


# ---------------------------------------------- the board-side real transport
class _IdentitySnapshot:
    release_id = R.QUALIFIED_RELEASE
    daemon_version = R.DAEMON_VERSION
    headroom = dict(hello_minor=R.PROTOCOL_MINOR, announced_major=R.PROTOCOL_MAJOR)


class _IdentityClient:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot if snapshot is not None else _IdentitySnapshot()
        self.error = error
        self.closed = False

    def poll(self):
        if self.error is not None:
            raise self.error
        return self.snapshot

    def close(self):
        self.closed = True


def test_local_kernel_storage_is_a_complete_read_or_a_failed_one(tmp_path):
    """Fault injection: the local read is fail-closed, never a silent clean board."""
    boot = tmp_path / 'boot_id'
    boot.write_text('d85fa469-aeb7-4ef9-860b-15389314ef30\n', encoding='ascii')

    class Result:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out

    def runner_for(result):
        return lambda *args, **kwargs: result

    clean = R.local_kernel_storage(boot_id_path=str(boot),
                                   runner=runner_for(Result(0, 'mmc0: all good\n')))
    assert clean == dict(boot_id='d85fa469-aeb7-4ef9-860b-15389314ef30',
                         kernel_storage='mmc0: all good\n', complete=True)
    for failed in (Result(1, 'mmc0: all good\n'), Result(0, ''), Result(0, '  \n')):
        with pytest.raises(OSError):
            R.local_kernel_storage(boot_id_path=str(boot), runner=runner_for(failed))


def test_the_live_identity_is_the_installed_releases_own_poll():
    client = _IdentityClient()
    identity = R.live_identity('unused', factory=lambda directory: client)
    assert identity == dict(release_id=R.QUALIFIED_RELEASE,
                            daemon_version=R.DAEMON_VERSION,
                            protocol_major=R.PROTOCOL_MAJOR,
                            protocol_minor=R.PROTOCOL_MINOR)
    assert client.closed is True                     # the probe's own session is closed
    with pytest.raises(OSError, match='device.info refused'):
        R.live_identity('unused', factory=lambda directory: _IdentityClient(
            error=OSError('device.info refused')))
    incomplete = R.load_release_module(str(CANDIDATE), 'rf_status_worker')
    with pytest.raises((AttributeError, ValueError)):
        R.live_identity('unused', factory=lambda directory: incomplete)


def test_the_independent_readback_is_the_reviewed_inspector_document():
    document = dict(release_id=R.QUALIFIED_RELEASE,
                    status=dict(headroom=dict(used=False, active=False)))
    out = R.independent_readback(R.QUALIFIED_RELEASE, inspector=lambda expected: document)
    assert out['state'] is document
    assert out['headroom'] == dict(used=False, active=False)
    assert R.independent_readback('x', inspector=lambda expected: {})['headroom'] == {}


def test_the_production_bundle_is_wired_to_the_release_itself():
    bundle = R.default_dependencies(str(CANDIDATE))
    assert R.factory_performs_board_read(bundle.worker_factory) is True
    assert bundle.simulation is False and bundle.hardware_touched is True
    assert bundle.token_reader.__name__ == 'read_control_token'
    assert Path(bundle.token_reader.__code__.co_filename) == CANDIDATE / 'rf_status_worker.py'
    for probe in (bundle.storage_probe, bundle.identity_probe, bundle.readback_probe):
        assert callable(probe)
    # the client seam is the INSTALLED release's own class, never a private socket
    assert type(R.release_client(str(CANDIDATE))).__name__ == 'RfStatusClient'


def test_the_production_probe_shapes_drive_the_whole_chain(tmp_path):
    """The board-side probes, with their board reads injected, run the real chain."""
    harness = Harness(tmp_path)
    reference = harness.bundle()
    production = R.default_dependencies(
        str(CANDIDATE),
        storage_reader=lambda: dict(boot_id='boot-0001', kernel_storage=sim.CLEAN_KERNEL,
                                    complete=True),
        client_factory=lambda directory: _IdentityClient(),
        inspector=lambda expected: sim.readback_document(expected)['state'])

    def declaring_factory(**kwargs):
        return reference['worker_factory'](**kwargs)
    declaring_factory.performs_board_read = True

    bundle = dict(reference)
    bundle.update(storage_probe=production.storage_probe,
                  identity_probe=production.identity_probe,
                  readback_probe=production.readback_probe,
                  token_reader=production.token_reader,
                  worker_factory=declaring_factory,
                  simulation=False, hardware_touched=True)
    report = harness.run(bundle)
    assert report['terminal'] == 'RUN_COMPLETED'
    assert report['simulation'] is False and report['hardware_touched'] is True
    assert report['storage']['preflight']['preflight_status'] == 'CLEAN'
    assert report['readback']['status'] == 'PASS'
    assert [g for g in report['gates'] if g['gate'] == R.GATE_WORKER][0]['board_read'] is True


