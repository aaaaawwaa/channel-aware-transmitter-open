"""Build a new reviewable candidate from already verified artifacts; no board I/O."""
import json
from pathlib import Path
import pytest
from scripts import rf_environment_candidate as C
from scripts import merged_release as R

ROOT=Path(__file__).resolve().parents[2]
PARENT=ROOT/'evidence/rf-teaching/s1/candidate/releases/c3bf915c544563324778d1c879b38e3a'
ARM=ROOT/'evidence/rf-teaching/s2/headroom-contract-2026-10-11/arm-native'
CURRENT=ROOT/'evidence/rf-teaching/s2/headroom-contract-2026-10-11/candidate-budget/releases/b67a8aaab0c5904822580c0647b96fd0'


def test_new_contract_inherits_the_unchanged_qualified_model():
    manifest=R.verify_release(PARENT,require_software=True)
    R.verify_model_artifact(PARENT,manifest[R.MODEL_KEY],'cnn-bpsk-headroom-v1')


def test_new_headroom_candidate_requires_new_native_contract(tmp_path):
    with pytest.raises(ValueError,match='1.0.4'):
        C.build_candidate(PARENT,tmp_path/'refused',contract='cnn-bpsk-headroom-v1')
    assert not (tmp_path/'refused').exists()


def test_new_headroom_candidate_preserves_parent_and_model(tmp_path):
    result=C.build_candidate(PARENT,tmp_path/'candidate',ARM/'rf_daemon',
        ARM/'rf_daemon.build.json',contract='cnn-bpsk-headroom-v1')
    manifest=R.verify_release(Path(result['candidate']),require_software=True)
    parent=R.verify_release(PARENT,require_software=True)
    assert manifest['software_contract']=='cnn-bpsk-headroom-v1'
    assert manifest['daemon_version']=='1.0.4'
    assert manifest[R.MODEL_KEY]==parent[R.MODEL_KEY]
    assert manifest['bit_sha256']==parent['bit_sha256']
    assert manifest['hwh_sha256']==parent['hwh_sha256']
    assert result['installed'] is False and result['physical_qualification']=='NOT_RUN'
    assert {'rf_headroom.py','rf_headroom_replay.py'} <= set(manifest['software'])


def test_headroom_revision_uses_current_verified_release_as_parent(tmp_path):
    result=C.build_candidate(CURRENT,tmp_path/'revision',ARM/'rf_daemon',
        ARM/'rf_daemon.build.json',contract='cnn-bpsk-headroom-v1')
    parent=R.verify_release(CURRENT,require_software=True)
    manifest=R.verify_release(Path(result['candidate']),require_software=True)
    receipt=json.loads((Path(result['candidate'])/'merged_build_receipt.json').read_text(encoding='utf-8'))
    assert result['parent_release']==parent['release_id']
    assert result['daemon_rebuilt'] is False
    assert result['native_artifact_reused'] is True
    assert receipt['software_rebuild']['parent_release_id']==parent['release_id']
    assert manifest[R.MODEL_KEY]==parent[R.MODEL_KEY]
    assert manifest['bit_sha256']==parent['bit_sha256']
    assert manifest['hwh_sha256']==parent['hwh_sha256']


def test_current_headroom_parent_cannot_be_used_for_environment_contract(tmp_path):
    with pytest.raises(ValueError,match='parent'):
        C.build_candidate(CURRENT,tmp_path/'refused',ARM/'rf_daemon',
            ARM/'rf_daemon.build.json',contract='cnn-bpsk-environment-v1')
    assert not (tmp_path/'refused').exists()
