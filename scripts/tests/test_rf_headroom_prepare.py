"""HEADROOM material integrity and blocked execution; all tests are offline."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
CANDIDATE = REPO / ('evidence/rf-teaching/s2/g1-qualification-2026-10-08/'
                    'candidate/releases/adfac1f14c3cdb379cb92569a21a9054')
BASELINE = CANDIDATE / 'rf_environment_baseline.json'
HISTORICAL_SOURCES = REPO / 'evidence/rf-teaching/s2/headroom-contract-2026-10-11/starting-inputs'


@pytest.fixture
def tool(monkeypatch):
    module_name = 'scripts.rf_headroom_prepare'
    if importlib.util.find_spec(module_name) is None:
        return None
    from scripts import rf_headroom_prepare as module
    # This preparer deliberately pins the 1.0.3 source set.  Exercise it with
    # the preserved exact 51-native/44-software inputs, not the new 1.0.4 tree.
    # _qualify still hashes every member against the immutable old candidate.
    monkeypatch.setattr(module,'ROOT',HISTORICAL_SOURCES)

    def forbidden(*args, **kwargs):
        pytest.fail('offline preparation attempted network access')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    return module


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rewrite(path, document):
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def repair_receipt_hashes(output):
    receipt_path = output / 'receipt.json'
    receipt = load(receipt_path)
    receipt['output_sha256'] = {
        p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in output.rglob('*') if p.is_file() and p.name != 'receipt.json'}
    rewrite(receipt_path, receipt)


def test_real_candidate_materials_are_complete_but_never_rf_ready(tool, tmp_path):
    assert tool is not None, 'offline HEADROOM preparer is missing'
    output = tmp_path / 'prepared'
    receipt = tool.prepare(CANDIDATE, output, BASELINE)
    assert receipt['prepared'] is True
    assert receipt['execution_ready'] is False
    assert receipt['rf_execution'] == 'BLOCKED_BY_EXACT_40DB_CONTRACT'
    assert receipt['physical_frames'] == receipt['initializations'] == 0
    assert receipt['formal_physical'] == receipt['human_acceptance'] == 'NOT_RUN'
    assert receipt['s2_stage_passed'] is False
    assert tool.verify_preparation(output, CANDIDATE, BASELINE)['verified'] is True
    declaration = load(output / 'study-declaration.json')
    assert declaration['fixed_rf']['tx_attenuation_mdB'] == 46000
    assert declaration['order'] == ['off-before', 'normal', 'narrowband', 'pulse', 'off-after']
    assert declaration['maximum_initializations'] == 1
    assert declaration['maximum_capture_attempts'] == 5
    assert declaration['settle_wait_us'] == 0
    expected_output_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.iterdir() if path.name != 'receipt.json'}
    assert receipt['output_sha256'] == expected_output_hashes
    assert load(output / 'receipt.json')['output_sha256'] == expected_output_hashes
    assert len(declaration['bindings']['native_source_sha256']) == 51
    tool_bindings = declaration['bindings']['tool_sha256']
    assert set(tool_bindings) == {'preparer', 'release_entry', 'release_verifier'}
    assert tool_bindings['release_entry'] != tool_bindings['release_verifier']
    assert not (output / 'off.tx.iq').exists()
    for kind in ('normal', 'narrowband', 'pulse'):
        assert (output / (kind + '.tx.iq')).stat().st_size == 10160
        document = load(output / (kind + '.scenario.json'))
        assert document['declaration']['truth'] == kind
        assert document['declaration']['amplitude'] == 60
        assert document['declaration']['prbs7_seed'] == 1


@pytest.mark.parametrize('mutation', ['ready', 'attenuation', 'budget', 'order',
                                     'subset', 'payload', 'scenario', 'unknown', 'missing'])
def test_rehashed_tampering_is_not_accepted_as_preparation(tool, tmp_path, mutation):
    output = tmp_path / 'prepared'
    tool.prepare(CANDIDATE, output, BASELINE)
    path = output / 'study-declaration.json'
    declaration = load(path)
    if mutation == 'ready':
        declaration['execution_ready'] = True
        receipt = load(output / 'receipt.json'); receipt['execution_ready'] = True
        rewrite(output / 'receipt.json', receipt)
    elif mutation == 'attenuation': declaration['fixed_rf']['tx_attenuation_mdB'] = 40000
    elif mutation == 'budget': declaration['maximum_capture_attempts'] = 4
    elif mutation == 'order': declaration['order'].reverse()
    elif mutation == 'subset': declaration['order'] = ['normal']
    elif mutation == 'payload':
        payload = output / 'normal.tx.iq'
        raw = bytearray(payload.read_bytes()); raw[0] ^= 1; payload.write_bytes(raw)
    elif mutation == 'scenario':
        scenario = output / 'narrowband.scenario.json'
        doc = load(scenario); doc['declaration']['tone_amplitude'] = 15; rewrite(scenario, doc)
    elif mutation == 'unknown': (output / 'compound.tx.iq').write_bytes(b'forged')
    elif mutation == 'missing': (output / 'pulse.tx.iq').unlink()
    rewrite(path, declaration)
    repair_receipt_hashes(output)
    with pytest.raises((ValueError, OSError)):
        tool.verify_preparation(output, CANDIDATE, BASELINE)


def test_preparation_refuses_output_reuse_without_overwriting(tool, tmp_path):
    output = tmp_path / 'prepared'; output.mkdir()
    sentinel = output / 'sentinel'; sentinel.write_bytes(b'keep')
    with pytest.raises((ValueError, OSError)):
        tool.prepare(CANDIDATE, output, BASELINE)
    assert sentinel.read_bytes() == b'keep'
    assert not (output / 'receipt.json').exists()


def test_verification_checks_receipt_bytes_even_when_payloads_are_untouched(tool, tmp_path):
    """Rebuilt payload digests cannot replace verification of the receipt itself."""
    output = tmp_path / 'prepared'
    tool.prepare(CANDIDATE, output, BASELINE)
    receipt = load(output / 'receipt.json')
    receipt['unreviewed_annotation'] = 'all payload digests remain unchanged'
    rewrite(output / 'receipt.json', receipt)
    with pytest.raises((ValueError, OSError)):
        tool.verify_preparation(output, CANDIDATE, BASELINE)


@pytest.mark.parametrize('mutation', ['daemon', 'manifest_capability', 'build', 'baseline'])
def test_changed_candidate_or_baseline_refused_before_output(tool, tmp_path, mutation):
    candidate = tmp_path / 'candidate'
    shutil.copytree(str(CANDIDATE), str(candidate))
    baseline = candidate / 'rf_environment_baseline.json'
    if mutation == 'daemon':
        path = candidate / 'rf_daemon'; path.chmod(stat.S_IWRITE)
        path.write_bytes(path.read_bytes() + b'x')
    elif mutation == 'manifest_capability':
        path = candidate / 'merged_release_manifest.json'; doc = load(path)
        path.chmod(stat.S_IWRITE)
        doc['headroom_capability'] = True; rewrite(path, doc)
    elif mutation == 'build':
        path = candidate / 'merged_build_receipt.json'; doc = load(path)
        path.chmod(stat.S_IWRITE)
        doc['rx_gain_observer_build']['protocol_minor'] = 6; rewrite(path, doc)
    elif mutation == 'baseline':
        baseline = tmp_path / 'baseline.json'
        baseline.write_bytes(BASELINE.read_bytes() + b'\n')
    output = tmp_path / 'output'
    with pytest.raises((ValueError, OSError)):
        tool.prepare(candidate, output, baseline)
    assert not output.exists()


def test_build_receipt_byte_identity_rejects_even_unrelated_field_changes(tool, tmp_path):
    """The reviewed receipt is pinned in full, beyond validated native fields."""
    candidate = tmp_path / 'candidate'
    shutil.copytree(str(CANDIDATE), str(candidate))
    receipt = candidate / 'merged_build_receipt.json'
    receipt.chmod(stat.S_IWRITE)
    document = load(receipt)
    document['unreviewed_annotation'] = 'native provenance remains unchanged'
    rewrite(receipt, document)
    output = tmp_path / 'prepared'
    with pytest.raises((ValueError, OSError)):
        tool.prepare(candidate, output, candidate / 'rf_environment_baseline.json')
    assert not output.exists()


def cloned_sources(tool, tmp_path, monkeypatch):
    root = tmp_path / 'source-root'
    for relative in tool.R.software_sources(tool.CONTRACT).values():
        target = root / relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(tool.ROOT / relative), str(target))
    shutil.copytree(str(tool.ROOT / 'rf_overlay/sw/board_app'),
                    str(root / 'rf_overlay/sw/board_app'))
    monkeypatch.setattr(tool, 'ROOT', root)
    return root


def test_historical_preparer_still_refuses_the_new_working_tree(tool,tmp_path,monkeypatch):
    monkeypatch.setattr(tool,'ROOT',REPO)
    output=tmp_path/'refused'
    with pytest.raises(ValueError,match='source changed|frozen native'):
        tool.prepare(CANDIDATE,output,BASELINE)
    assert not output.exists()


@pytest.mark.parametrize('mutation', ['software', 'native_content', 'native_added', 'native_missing'])
def test_source_drift_including_native_membership_is_refused(tool, tmp_path, monkeypatch, mutation):
    root = cloned_sources(tool, tmp_path, monkeypatch)
    native = root / 'rf_overlay/sw/board_app'
    if mutation == 'software':
        path = root / 'pynq_overlay/sw/rf_environment.py'; path.write_bytes(path.read_bytes() + b'\n')
    elif mutation == 'native_content':
        path = native / 'rf_core.c'; path.write_bytes(path.read_bytes() + b'\n')
    elif mutation == 'native_added': (native / 'unattested.h').write_text('new source\n')
    elif mutation == 'native_missing': (native / 'vendor/SHA256SUMS').unlink()
    output = tmp_path / 'prepared'
    with pytest.raises((ValueError, OSError)):
        tool.prepare(CANDIDATE, output, BASELINE)
    assert not output.exists()


def test_verification_rechecks_sources_instead_of_trusting_saved_receipt(tool, tmp_path, monkeypatch):
    root = cloned_sources(tool, tmp_path, monkeypatch)
    output = tmp_path / 'prepared'; tool.prepare(CANDIDATE, output, BASELINE)
    path = root / 'rf_overlay/sw/board_app/rf_core.c'; path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError): tool.verify_preparation(output, CANDIDATE, BASELINE)


def directory_link(link, target):
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else: link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize('linked', ['candidate_parent', 'baseline_parent', 'source_parent', 'output_parent'])
def test_linked_parent_is_rejected_before_output(tool, tmp_path, monkeypatch, linked):
    actual = tmp_path / 'actual'; actual.mkdir()
    alias = tmp_path / 'alias'; directory_link(alias, actual)
    candidate, baseline, output = CANDIDATE, BASELINE, tmp_path / 'prepared'
    if linked == 'candidate_parent':
        shutil.copytree(str(CANDIDATE), str(actual / 'candidate'))
        candidate = alias / 'candidate'
    elif linked == 'baseline_parent':
        shutil.copy2(str(BASELINE), str(actual / 'baseline.json')); baseline = alias / 'baseline.json'
    elif linked == 'source_parent':
        root = cloned_sources(tool, actual, monkeypatch)
        monkeypatch.setattr(tool, 'ROOT', alias / root.relative_to(actual))
    else: output = alias / 'prepared'
    with pytest.raises((ValueError, OSError)):
        tool.prepare(candidate, output, baseline)
    assert not output.exists()


def test_nonregular_baseline_is_rejected_before_output(tool, tmp_path):
    baseline = tmp_path / 'directory.json'; baseline.mkdir()
    output = tmp_path / 'prepared'
    with pytest.raises((ValueError, OSError)): tool.prepare(CANDIDATE, output, baseline)
    assert not output.exists()


def test_write_failure_leaves_no_success_receipt(tool, tmp_path, monkeypatch):
    original = Path.write_bytes
    def fail_payload(path, data):
        if path.name == 'pulse.tx.iq': raise OSError('simulated disk failure')
        return original(path, data)
    monkeypatch.setattr(Path, 'write_bytes', fail_payload)
    output = tmp_path / 'prepared'
    with pytest.raises(OSError): tool.prepare(CANDIDATE, output, BASELINE)
    assert not (output / 'receipt.json').exists()
    with pytest.raises((ValueError, OSError)): tool.verify_preparation(output, CANDIDATE, BASELINE)


def test_receipt_write_failure_leaves_no_prepared_document(tool, tmp_path, monkeypatch):
    original = Path.write_bytes
    def fail_receipt(path, data):
        if path.name == 'receipt.json': raise OSError('simulated receipt disk failure')
        return original(path, data)
    monkeypatch.setattr(Path, 'write_bytes', fail_receipt)
    output = tmp_path / 'prepared'
    with pytest.raises(OSError): tool.prepare(CANDIDATE, output, BASELINE)
    for path in output.glob('*.json'):
        assert load(path).get('prepared') is not True


def test_cli_has_no_run_option_and_never_creates_output(tmp_path):
    script = REPO / 'scripts/rf_headroom_prepare.py'
    assert script.is_file(), 'offline HEADROOM preparer is missing'
    output = tmp_path / 'prepared'
    result = subprocess.run([sys.executable, '-B', str(script), 'prepare',
        '--candidate', str(CANDIDATE), '--baseline', str(BASELINE),
        '--output', str(output), '--run'], capture_output=True, text=True)
    assert result.returncode != 0
    assert '--run' in result.stderr
    assert not output.exists()
