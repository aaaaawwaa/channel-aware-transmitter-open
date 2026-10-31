"""Formal-path wiring of the measured offline rejection set (delta-review fixes).

Closes the two P1 findings of the delta review: the host-side launcher and the board
entry must both carry the measured N1-N12 receipt, the freeze declaration must bind the
current FORMAL_TOOLS digests, and the loader must refuse a null/oversized receipt.
No board, no network, no RF, no new physical frame."""
import json
from pathlib import Path
import runpy
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import rf_environment_acceptance as ACC  # noqa: E402
import rf_environment_freeze as FRZ  # noqa: E402
import rf_environment_board_run as BOARD  # noqa: E402
from scripts import rf_environment_freeze as BOARD_FRZ  # noqa: E402

BOARD_SESSION = ROOT / 'scripts' / 'rf_environment_board_session.py'
BOARD_RUN = ROOT / 'scripts' / 'rf_environment_board_run.py'
RECEIPT = ROOT / 'evidence' / 'rf-teaching' / 's2-offline-negatives-2026-10-14' / 'receipt.json'
DECLARATION = (ROOT / 'evidence' / 'rf-teaching' / 's2-freeze-declaration-2026-10-14' /
               'freeze-declaration.json')
# The two 46 dB TRAIN/VAL entries appended to FORMAL_TOOLS after that declaration was
# sealed.  The historical declaration cannot carry them, so until M11 re-signs it in the
# 46 dB/v4 domain their digests are pinned here; a silent change is then reported by name
# exactly like the historical drift.
NEW_FORMAL_TOOLS = ('rf_environment_trainval_session.py', 'rf_environment_trainval_adapter.py')
NEW_FORMAL_TOOL_SHA256 = {
    # Re-pinned 2026-10-18 09:2x (redispatch instance): the 11-frame session entry's
    # submitted-revision gate now accepts the board's local RX-draft revision
    # (non-negative integer) instead of the constant 0, which is what the real board
    # carries (ui_model.rx_demod_revision >= 1 after setup enables the reference); see
    # spec G.8 and evidence/rf-teaching/s2-46db-collect-impl-2026-10-18/NOTES-redispatch.md.
    # Re-pinned 2026-10-18 (D6 / calibration-ordering fix, spec section J): the session
    # schema now validates the declared collection mode and the preserved collected
    # label, and the adapter carries that mode plus every collected label into the set.
    'rf_environment_trainval_session.py':
        '88e46f07ee21c6b78e00b341059394369caced6a50ce54e2eff2bec9e634df70',
    'rf_environment_trainval_adapter.py':
        '4ad5be751d30bdf04aecc73da3677b860ac63888fe7fca1acfdc1daaaa23b839',
}
# Historical drift: the two domain-migration clients plus rf_environment_freeze.py,
# which this preparation round changed (FORMAL_TOOLS now carries the two TRAIN/VAL
# entries), and rf_environment_board_session.py, which the 2026-10-18 D6 fix changed
# (the scene range is now read off the installed view instead of a private 4-scene
# list -- spec section J).  The declaration is stale by design until M11 re-signs it.
HISTORICAL_DRIFT = ['rf_bpsk_ui_board_session.py', 'rf_environment_acceptance.py',
                    'rf_environment_board_session.py', 'rf_environment_freeze.py']


def _board_session(namespace, monkeypatch):
    """Run the real board-session __main__ block with argv control (no hardware)."""
    monkeypatch.setattr(sys, 'argv', ['rf_environment_board_session.py'] + namespace)
    return runpy.run_path(str(BOARD_SESSION), run_name='__main__')


def test_board_session_refuses_a_formal_run_without_the_measured_receipt(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exit_info:
        _board_session(['--expected-release', 'x' * 32, '--output', str(tmp_path / 'out'),
                        '--cable-confirmed', '--freeze', str(DECLARATION)], monkeypatch)
    assert 'requires --offline-rejection' in str(exit_info.value)
    assert not (tmp_path / 'out' / 'result.json').exists()


def test_board_session_passes_the_measured_counts_to_the_campaign(tmp_path, monkeypatch):
    seen = {}
    def recorder(factory, output, release, freeze, offline_rejection=None):
        seen['offline_rejection'] = offline_rejection
        seen['release'] = release
        return {'formal_rf_passed': True}
    monkeypatch.setattr(ACC, 'campaign', recorder)
    with pytest.raises(SystemExit) as exit_info:
        _board_session(['--expected-release', 'x' * 32, '--output', str(tmp_path / 'out'),
                        '--cable-confirmed', '--freeze', str(DECLARATION),
                        '--offline-rejection', str(RECEIPT)], monkeypatch)
    assert exit_info.value.code == 0
    assert seen['offline_rejection'] == {'cases': 12, 'passed': 12, 'failed': 0, 'cleared': 0}


def test_board_run_launcher_carries_the_receipt_to_the_board():
    """Text-level by necessity: run() verifies a candidate release before it uploads."""
    source = BOARD_RUN.read_text(encoding='utf-8')
    assert 'offline_rejection_path' in source
    assert 'requires --offline-rejection' in source
    assert 'load_offline_rejection(offline_rejection_path)' in source
    assert source.count('--offline-rejection') >= 2   # board command + CLI option
    assert 'measured_offline' in source


def test_freeze_declaration_binds_the_historical_tools_and_shows_the_migration_drift():
    """The shipped declaration is the 40 dB one; the current domain is 46 dB/v4.

    Re-signing this declaration is migration step M11 (it needs the final 46 dB
    TRAIN/VAL baseline and the same-candidate 18-frame pilot), so until then the drift
    is asserted by name instead of being accepted silently: any further tool change
    shows up here as an additional name, and the two TRAIN/VAL entries the declaration
    cannot know yet are pinned by digest.  The declared skin stays the historical
    40 dB declaration while the current tree must report v4/46000, so a half-migrated
    tree cannot pass by resembling the declaration.
    """
    declaration = json.loads(DECLARATION.read_text(encoding='utf-8'))
    assert declaration['environment_version'] == 'rf-window-environment-v3'
    assert declaration['fixed_rf']['attenuation_mdB'] == 40000
    assert FRZ.VERSION == 'rf-window-environment-v4'
    assert ACC.FIXED_RF['attenuation_mdB'] == 46000
    current = FRZ.tool_hashes()
    # Membership is pinned exactly: the historical names plus the two new entries.
    assert set(FRZ.FORMAL_TOOLS) == set(declaration['tool_hashes']) | set(NEW_FORMAL_TOOLS)
    assert set(declaration['tool_hashes']) <= set(current)
    drifted = sorted(name for name, digest in declaration['tool_hashes'].items()
                     if current[name] != digest)
    assert drifted == HISTORICAL_DRIFT
    new_drifted = sorted(name for name, digest in NEW_FORMAL_TOOL_SHA256.items()
                         if current[name] != digest)
    assert new_drifted == []
    # That entry was sealed at the 40 dB declaration and is now one of the named
    # drifts above, so it is recorded as a changed tool instead of an equality.
    assert 'rf_environment_board_session.py' in HISTORICAL_DRIFT
    assert declaration['tool_hashes']['rf_environment_board_session.py'] != (
        __import__('hashlib').sha256(BOARD_SESSION.read_bytes()).hexdigest())


def test_load_offline_rejection_refuses_null_counts_and_oversize(tmp_path):
    null_counts = tmp_path / 'null.json'
    null_counts.write_text(json.dumps({'counts': None}), encoding='utf-8')
    with pytest.raises(ValueError):
        ACC.load_offline_rejection(null_counts)
    big = tmp_path / 'big.json'
    big.write_text('{"counts": {}}' + ' ' * 131072, encoding='utf-8')
    with pytest.raises(ValueError):
        ACC.load_offline_rejection(big)


@pytest.mark.parametrize('board_counts, accepted', [
    ({'cases': 12, 'passed': 0, 'failed': 12, 'cleared': 0}, False),
    (None, False),
    ({'cases': 12, 'passed': 12, 'failed': 0, 'cleared': 0}, True),
])
def test_host_launcher_binds_board_result_to_uploaded_offline_counts(
        tmp_path, monkeypatch, board_counts, accepted):
    expected = {'cases': 12, 'passed': 12, 'failed': 0, 'cleared': 0}
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    (candidate / 'rf_environment_baseline.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(BOARD.R, 'verify_release', lambda *args, **kwargs: {
        'release_id': 'x' * 32, 'software_contract': 'cnn-bpsk-environment-v1',
        'software': {},
    })
    monkeypatch.setattr(BOARD_FRZ, 'check_candidate', lambda value, path: (value, 'freeze-digest'))

    class Sftp:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def mkdir(self, path): pass
        def put(self, source, target): pass
        def listdir(self, path): return ['result.json']
        def get(self, source, target):
            Path(target).write_text(json.dumps({
                'formal_rf_passed': True,
                **({'offline_rejection': board_counts} if board_counts is not None else {}),
                'sessions': [{'owned': True, 'closed': True, 'disarm_ack': True}],
            }), encoding='utf-8')

    class Client:
        def open_sftp(self): return Sftp()
        def close(self): pass

    monkeypatch.setattr(BOARD, 'load_helper', lambda: type('Helper', (), {'connect': lambda self: Client()})())
    def board_command(client, helper, command, log, timeout):
        assert '--freeze ' in command and '--offline-rejection ' in command
        return 0
    monkeypatch.setattr(BOARD, '_run', board_command)
    result = BOARD.run(candidate, tmp_path / 'output', DECLARATION, RECEIPT)
    assert result['passed'] is accepted
    assert (result['formal_physical'] == 'PASS') is accepted
    if not accepted:
        assert 'offline_rejection' in result['result_error']
    assert expected == ACC.load_offline_rejection(RECEIPT)


def test_delivered_headroom_candidate_cannot_start_formal_environment_campaign(tmp_path, monkeypatch):
    candidate = (ROOT / 'evidence' / 'rf-teaching' / 's2' / 'executable-candidate-2026-10-12' /
                 'candidate-final' / 'releases' / 'b82b141e355b4cb1833969383a409c79')
    assert candidate.is_dir()
    def no_board_connection():
        pytest.fail('the board transport must not be opened for an unqualified candidate')
    monkeypatch.setattr(BOARD, 'load_helper', no_board_connection)
    with pytest.raises(ValueError, match='exact verified S2 candidate required'):
        BOARD.run(candidate, tmp_path / 'output', DECLARATION, RECEIPT)
    assert not (tmp_path / 'output').exists()
