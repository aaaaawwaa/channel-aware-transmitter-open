"""The physical-loopback attenuation the daemon approves vs the one the chain sends.

2026-10-18 campaign2 train-01 was stopped by its FIRST frame:
    code=INVALID_ARGUMENT, reason="RF daemon refused loopback.physical.capture",
    frames=[], tx never sourced, egress safe.
The refusal was not about the session shape: it was the request FIELD.  The product chain
can only ever name the release's single-source attenuation
(rf_status_client._capture_args refuses anything but rf_teaching_profile.ATTENUATION_MDB,
ui_model.rx_capture_args builds the panel's physical request from the same constant),
while the installed daemon approves exactly the value compiled into it (rf_state.h /
rf_state.c).  In the 46 dB release those two were 46000 and 40000, so every physical
frame was refused before any hardware write.

That is fixed at the source: since 2026-10-18 the compiled-in value IS the domain value
(rf_state.h RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB = 46000), the refusal message is built
from that same macro, and scripts/tests/test_rf_state_loopback_attenuation_pin.py holds
the C constant equal to rf_teaching_profile.ATTENUATION_MDB.  The release that was
installed when the campaign failed (the directory named by STUB_DAEMON.RELEASE_ID)
still carries the pre-migration daemon, so it stays here as the negative control: a real
binary the entry gate must judge MISMATCH.

These tests hold both halves with REAL product code:

* the real UiApp builds the physical capture arguments,
* the real RfStatusClient validates and sends them, and
* a protocol-level stub daemon applies the daemon's own gate order
  (scripts/tests/loopback_stub_daemon.py, transcribed with line citations) whose default
  rule is READ from rf_state.h rather than copied,
so the same request is refused by a daemon that kept 40000 and accepted by one carrying
46000, with nothing else changed -- the single-variable proof, without a board.

The entry then has to refuse a mismatched campaign BEFORE it dispatches anything, by name.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'scripts' / 'tests'),
               str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import loopback_stub_daemon as STUB_DAEMON  # noqa: E402
from scripts import rf_environment_trainval_board_session as BOARD  # noqa: E402
from scripts import merged_release as RELEASE  # noqa: E402
from rf_status_client import RfStatusClient, StatusProtocolError  # noqa: E402

# The release that was installed when campaign2 train-01 was refused (its own board
# preflight verified this directory: release_id, contract, domain and baseline).  Its
# daemon predates the 2026-10-18 C-side migration, which is what makes it the control.
INSTALLED_CANDIDATE = (ROOT / 'evidence' / 'rf-teaching' / 's2-46db-m2-bootstrap-2026-10-17'
                       / 'candidate' / 'releases' / STUB_DAEMON.RELEASE_ID)
INSTALLED_RELEASE = STUB_DAEMON.RELEASE_ID
# The pre-migration rule compiled into that binary.  The tree's gate no longer says
# this (rf_state.h is the domain value, pinned by the pin test), which is the point.
OLD_DAEMON_LITERAL = b'physical loopback requires attenuation_mdB 40000'
LOOPBACK_META = {'samples': 4096, 'bytes': 4096 * 4, 'format': 's16_iq_le',
                 'direction': 'tx', 'wrap': True}
LOOPBACK_PAYLOAD = b'\x00' * (4096 * 4)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidate_copy(target, *, literal=None, drop_literal=False):
    """A copy of the installed candidate, optionally with the daemon's rule changed.

    The release directory is the release: the daemon binary's digest is part of the
    manifest and of the release id, so a copy that edits the binary must recompute both
    (or verify_release refuses it, which is the point of the digest).
    """
    target = Path(target)
    if target.exists():
        raise ValueError('candidate target already exists: %s' % target)
    shutil.copytree(str(INSTALLED_CANDIDATE), str(target),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    daemon = target / 'rf_daemon'
    manifest_path = target / 'merged_release_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if literal is not None or drop_literal:
        raw = daemon.read_bytes()
        assert raw.count(OLD_DAEMON_LITERAL) == 1, 'the installed binary moved: update this test'
        if drop_literal:
            raw = raw.replace(OLD_DAEMON_LITERAL, b'')
        else:
            replacement = b'physical loopback requires attenuation_mdB %d' % literal
            assert len(replacement) == len(OLD_DAEMON_LITERAL), 'keep the length so the copy stays a real binary'
            raw = raw.replace(OLD_DAEMON_LITERAL, replacement)
        daemon.chmod(0o644)
        daemon.write_bytes(raw)
        manifest['daemon_sha256'] = digest(daemon)
        manifest['release_id'] = RELEASE.release_identity(
            manifest['build_id'], manifest['bit_sha256'], manifest['hwh_sha256'],
            manifest['daemon_version'], manifest['protocol_version'],
            manifest['daemon_sha256'], software=manifest['software'],
            software_contract=manifest.get('software_contract'),
            model_artifact=manifest.get(RELEASE.MODEL_KEY))
        manifest_path.chmod(0o644)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    RELEASE.verify_release(target, require_software=True)
    return dict(path=str(target.resolve()), release_id=manifest['release_id'],
                daemon_sha256=manifest['daemon_sha256'])


def wire_job(daemon):
    """A real client with an armed control session and a loaded TX, like the board's."""
    client = RfStatusClient(port=daemon.port, timeout=1, control_timeout=2,
                            capture_timeout=2)
    assert client.poll().error is None
    client.control_arm('stub-token')
    client.control_op('tx.load', dict(LOOPBACK_META), payload=LOOPBACK_PAYLOAD)
    return client


@pytest.fixture
def daemon():
    """The rule the C header carries (46000): read, never copied, by the double."""
    stub = STUB_DAEMON.LoopbackStubDaemon()
    yield stub
    stub.close()


@pytest.fixture
def old_rule_daemon():
    """The pre-migration binary: only its compiled-in rule is 40000."""
    stub = STUB_DAEMON.LoopbackStubDaemon(loopback_attenuation_mdb=40000)
    yield stub
    stub.close()


# ------------------------------------------------------- the request the chain sends

def test_the_panel_builds_the_physical_request_from_the_single_source():
    import ui_model
    from rf_teaching_profile import ATTENUATION_MDB
    assert ATTENUATION_MDB == 46000, 'the 46 dB domain is the revised single source'
    assert ui_model.RX_LOOPBACK_ATTENUATION_MDB == ATTENUATION_MDB, (
        'the panel pin follows the profile, so the domain value is what it sends')
    args, why = ui_model.UiApp().rx_capture_args(physical=True)
    assert why is None
    assert args == {'samples': 4096, 'ratio': 0, 'cable_confirmed': True,
                    'attenuation_mdB': ATTENUATION_MDB}


def test_the_client_accepts_only_the_single_source_and_refuses_40000():
    client = RfStatusClient(port=1, timeout=1)
    ok = client._capture_args({'samples': 4096, 'ratio': 0, 'cable_confirmed': True,
                               'attenuation_mdB': 46000}, True)
    assert ok == {'samples': 4096, 'ratio': 0, 'cable_confirmed': True,
                  'attenuation_mdB': 46000}
    for refused in (40000, 45999, 46100, None):
        with pytest.raises(StatusProtocolError) as raised:
            client._capture_args({'samples': 4096, 'ratio': 0, 'cable_confirmed': True,
                                  'attenuation_mdB': refused}, True)
        assert 'requires attenuation_mdB 46000' in str(raised.value)


# --------------------------------------------- the daemon rule, single variable

def test_the_header_rule_approves_that_request(daemon):
    """The single variable that moved: the gate now carries the domain value."""
    client = wire_job(daemon)
    import ui_model
    args, _why = ui_model.UiApp().rx_capture_args(physical=True)
    wire = client._capture_args(dict(args), True)
    assert daemon.loopback_attenuation_mdb == 46000
    frame = client.capture(dict(wire), physical=True)
    assert daemon.physical_requests == [wire] and daemon.refusals == []
    assert frame.physical is True and len(frame.payload) == 4096 * 4
    assert 'tx.load' in daemon.ops() and daemon.tx_count == 4096


def test_a_daemon_that_kept_the_pre_migration_rule_refuses_that_request(old_rule_daemon):
    """The defect, kept as a control: the refused message names the OLD number."""
    client = wire_job(old_rule_daemon)
    import ui_model
    args, _why = ui_model.UiApp().rx_capture_args(physical=True)
    wire = client._capture_args(dict(args), True)
    with pytest.raises(StatusProtocolError) as raised:
        client.capture(wire, physical=True)
    # Exactly what the board's worker published for normal-01 on 2026-10-18.
    assert str(raised.value) == 'RF daemon refused loopback.physical.capture'
    assert raised.value.code == 'INVALID_ARGUMENT'
    assert old_rule_daemon.physical_requests == [wire]
    assert old_rule_daemon.refusals == [
        {'op': STUB_DAEMON.PHYSICAL_OP, 'code': 'INVALID_ARGUMENT',
         'message': 'physical loopback requires attenuation_mdB 40000'}]
    # The TX WAS loaded on the same retained session (the client's own gate at
    # rf_status_client.py:904 proves it before anything is sent), so the refusal is the
    # attenuation gate -- not the "nothing to loop back" gate at rf_daemon.c:1706 that
    # the campaign notes first suspected.  The stub shows the order on a fresh daemon:
    # with the approved value the attenuation gate passes and the tx_count gate answers.
    assert 'tx.load' in old_rule_daemon.ops() and old_rule_daemon.tx_count == 4096
    fresh = STUB_DAEMON.LoopbackStubDaemon()
    try:
        assert fresh.physical_gate(dict(wire, attenuation_mdB=40000)) == (
            'INVALID_ARGUMENT',
            'physical loopback requires attenuation_mdB 46000')
        assert fresh.physical_gate(dict(wire)) == (
            'INVALID_ARGUMENT',
            'the physical loopback needs a tx.load first: there is nothing to loop back')
    finally:
        fresh.close()


def test_only_the_daemon_rule_decides(old_rule_daemon, daemon):
    """Same request bytes on both sides; the rule is the only difference."""
    import ui_model
    args, _why = ui_model.UiApp().rx_capture_args(physical=True)
    refused_client = wire_job(old_rule_daemon)
    accepted_client = wire_job(daemon)
    wire = refused_client._capture_args(dict(args), True)
    assert accepted_client._capture_args(dict(args), True) == wire
    with pytest.raises(StatusProtocolError):
        refused_client.capture(dict(wire), physical=True)
    frame = accepted_client.capture(dict(wire), physical=True)
    assert (old_rule_daemon.physical_requests == daemon.physical_requests == [wire])
    assert frame.physical is True and len(frame.payload) == 4096 * 4
    assert daemon.refusals == []
    # The loaded-but-uncommitted readback: a tx.load alone leaves SMP_LEN 0
    # (rf_core.c:1161-1176 writes the BRAM, rf_core.c:1776 commits the length).
    assert old_rule_daemon.pl_length == 0 and daemon.pl_length == 4096


# ------------------------------------------------- the entry refuses before dispatch

def _run_entry(candidate, output, result_path, monkeypatch):
    return BOARD.main(['--expected-release', candidate['release_id'], '--split', 'train',
                       '--session', '1', '--output', str(output), '--result',
                       str(result_path), '--cable-confirmed', '--release-directory',
                       candidate['path']])


def test_the_entry_refuses_the_pre_migration_release_before_any_dispatch(tmp_path,
                                                                        monkeypatch):
    """The old binary is judged, by name, BEFORE the entry creates anything."""
    candidate = candidate_copy(tmp_path / 'candidate')
    built = []

    def spy(*args, **kwargs):
        built.append(args)
        raise AssertionError('the session object must not be constructed')

    monkeypatch.setattr(BOARD, 'trainval_session_class', spy)
    output = tmp_path / 'out'
    result_path = tmp_path / 'session-result.json'
    assert _run_entry(candidate, output, result_path, monkeypatch) == 1
    document = json.loads(result_path.read_text(encoding='utf-8'))
    assert document['stop_cause'] == 'DAEMON_LOOPBACK_ATTENUATION_MISMATCH'
    assert document['loopback_attenuation'] == {
        'sent': 46000, 'daemon': 40000, 'source': str(Path(candidate['path']) / 'rf_daemon'),
        'ok': False, 'code': 'DAEMON_LOOPBACK_ATTENUATION_MISMATCH'}
    assert document['passed'] is False and document['manifest_written'] is False
    assert document['session_closed'] is False and document['disarm_ack'] is False
    assert document['frames'] == [] and document['release_id'] is None
    assert '46000' in document['error'] and '40000' in document['error']
    assert built == []
    assert not output.exists(), 'the gate runs before the output directory is created'


def test_a_release_whose_daemon_carries_the_migrated_gate_passes(tmp_path, monkeypatch):
    candidate = candidate_copy(tmp_path / 'candidate', literal=46000)
    assert BOARD.daemon_loopback_attenuation_mdb(candidate['path'])[0] == 46000

    class Reached(Exception):
        pass

    def spy(*args, **kwargs):
        raise Reached()

    monkeypatch.setattr(BOARD, 'trainval_session_class', spy)
    output = tmp_path / 'out'
    result_path = tmp_path / 'session-result.json'
    with pytest.raises(Reached):
        _run_entry(candidate, output, result_path, monkeypatch)
    assert output.is_dir(), 'the entry proceeds to prepare the session directory'
    assert not result_path.exists(), 'a refusal document is only written by the gate'


def test_an_unreadable_daemon_rule_is_named_and_never_a_silent_pass(tmp_path,
                                                                   monkeypatch):
    candidate = candidate_copy(tmp_path / 'candidate', drop_literal=True)
    value, source = BOARD.daemon_loopback_attenuation_mdb(candidate['path'])
    assert value is None and 'no compiled-in physical-loopback' in source
    monkeypatch.setattr(BOARD, 'trainval_session_class',
                        lambda *args, **kwargs: pytest.fail('must not be constructed'))
    output = tmp_path / 'out'
    result_path = tmp_path / 'session-result.json'
    assert _run_entry(candidate, output, result_path, monkeypatch) == 1
    document = json.loads(result_path.read_text(encoding='utf-8'))
    assert document['stop_cause'] == 'DAEMON_LOOPBACK_ATTENUATION_UNKNOWN'
    assert document['loopback_attenuation']['ok'] is None
    assert not output.exists()


def test_the_preflight_reports_the_old_release_conflict_and_stays_read_only(tmp_path):
    """The pre-migration release is still a real mismatch: ok=False, verdict by name."""
    candidate = candidate_copy(tmp_path / 'candidate')
    document = BOARD.preflight(candidate['release_id'], 'train', 1,
                               release_directory=candidate['path'])
    assert document['ok'] is True and document['rf_actions'] is False
    assert document['captures_started'] is False
    assert document['domain']['attenuation_mdB'] == 46000
    assert document['daemon_loopback_attenuation_mdB'] == 40000
    assert document['loopback_attenuation']['ok'] is False
    assert document['loopback_attenuation']['code'] == 'DAEMON_LOOPBACK_ATTENUATION_MISMATCH'
    assert [row['physical'] for row in document['frames']] == [True] * 10 + [False]


def test_the_preflight_agrees_on_a_daemon_that_carries_the_migrated_gate(tmp_path):
    """The positive control for the same read-only path: ok=True with the new literal."""
    candidate = candidate_copy(tmp_path / 'candidate', literal=46000)
    document = BOARD.preflight(candidate['release_id'], 'train', 1,
                               release_directory=candidate['path'])
    assert document['ok'] is True and document['rf_actions'] is False
    assert document['captures_started'] is False
    assert document['domain']['attenuation_mdB'] == 46000
    assert document['daemon_loopback_attenuation_mdB'] == 46000
    assert document['loopback_attenuation'] == {
        'sent': 46000, 'daemon': 46000, 'ok': True, 'code': None,
        'source': str(Path(candidate['path']) / 'rf_daemon')}
