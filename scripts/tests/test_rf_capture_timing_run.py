"""Timing RF campaign gate and failure safety use no actual network in tests."""
import json
import shutil
from types import SimpleNamespace

import pytest
from scripts import rf_capture_timing_run as T
from scripts.tests.test_rf_capture_timing_probe import (downloaded_case,
    safe_state_document,StateSftp)


class Sftp:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def mkdir(self,*args):pass
    def put(self,*args):pass
    def get(self,remote,local):open(local,'w').write('{}')


def qualified(tmp_path,monkeypatch):
    candidate=tmp_path/'candidate';candidate.mkdir()
    (candidate/'merged_build_receipt.json').write_text(json.dumps(dict(rx_gain_observer_build=dict(
        daemon_version='1.0.2',daemon_sha256='a'*64,protocol_major=1,protocol_minor=4))))
    monkeypatch.setattr(T.R,'verify_release',lambda *a,**k:dict(software_contract='cnn-bpsk-environment-v1',
        daemon_version='1.0.2',software={},release_id='release',daemon_sha256='a'*64))
    monkeypatch.setattr(T.R,'software_digests',lambda *a:{})
    return candidate


LAYOUT=['s00-normal-00','s00-narrowband-00','s01-normal-00','s01-narrowband-00']


def test_the_archived_four_frame_shape_is_a_real_success(tmp_path,monkeypatch):
    """The minor-4 four-frame driver, end to end, on a downloaded board image.

    Everything a real run produces is here: four verified frames with their saved
    records under `records/`, the two session summaries, and a safe-state document
    that came back from the board.  The records are reachable ONLY through the
    downloaded root -- their receipts name board absolute paths -- so this also
    fails if the driver stops telling the shared validator where the download
    landed.  A regression there is invisible in the archived four-frame evidence,
    which was produced before the shared validator started reading records."""
    candidate=qualified(tmp_path,monkeypatch)
    fixture=tmp_path/'fixture';fixture.mkdir()
    rows=[]
    for i,tag in enumerate(LAYOUT):
        kind='normal' if 'normal' in tag else 'narrowband'
        # iq_seed keeps every frame's SAMPLES distinct while the two frames of a
        # session share a connection generation, which is the real shape.
        row=downloaded_case(fixture,tag,i//2,kind,iq_seed=i)
        row.update(tag=tag,truth=kind,reference_seed=1 if i<2 else 37)
        rows.append(row)
    sessions=[dict(owned=True,closed=True,disarm_ack=True,reference_seed=1),
              dict(owned=True,closed=True,disarm_ack=True,reference_seed=37)]
    output=tmp_path/'output'
    state=safe_state_document()
    client=SimpleNamespace(open_sftp=lambda:StateSftp(state),close=lambda:None)
    monkeypatch.setattr(T,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(T,'fetch_evidence',
        lambda s,remote,dest:(shutil.copytree(str(fixture),str(dest),dirs_exist_ok=True),[])[1])

    def fake_run(client,helper,cmd,log,timeout=0):
        if 'rf_capture_timing_probe.py' in cmd:
            (output/'result.json').write_text(json.dumps(dict(expected_release='release',
                passed=True,records=rows,sessions=sessions)),encoding='utf-8')
        return 0
    monkeypatch.setattr(T,'_run',fake_run)

    result=T.run(candidate,output)
    assert result['passed'] is True
    assert result['board_state']=='VERIFIED_SAFE'
    assert [c['tag'] for c in result['independent_checks']]==LAYOUT
    assert result['physical_frame_files']==4


def test_upload_guard_failure_only_executes_separately_verified_inspection(tmp_path,monkeypatch):
    candidate=qualified(tmp_path,monkeypatch)
    client=SimpleNamespace(open_sftp=Sftp,close=lambda:None)
    monkeypatch.setattr(T,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(T,'fetch_evidence',lambda *a:[])
    calls=[]
    def run(*a,**k):calls.append(a[2]);return 1 if len(calls)==1 else 0
    monkeypatch.setattr(T,'_run',run)
    result=T.run(candidate,tmp_path/'output')
    assert not result['passed'] and len(calls)==3
    assert 'sudo -S' not in calls[1] and 'rf_teaching_install_state.py' in calls[1]
    assert 'sudo -S' in calls[2] and 'rf_capture_timing_probe.py' not in calls[2]


@pytest.mark.parametrize('failure',['download','close'])
def test_cleanup_failures_still_inspect_and_preserve_receipt(tmp_path,monkeypatch,failure):
    candidate=qualified(tmp_path,monkeypatch)
    def fail(*a):raise OSError('injected '+failure)
    client=SimpleNamespace(open_sftp=Sftp,close=fail if failure=='close' else lambda:None)
    monkeypatch.setattr(T,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(T,'fetch_evidence',fail if failure=='download' else lambda *a:[])
    calls=[];monkeypatch.setattr(T,'_run',lambda *a,**k:calls.append(a[2]) or 0)
    output=tmp_path/'output';result=T.run(candidate,output)
    assert not result['passed'] and (output/'receipt.json').exists()
    assert any('sudo -S' in c and 'rf_teaching_install_state.py' in c for c in calls)


def test_unverified_inspector_never_runs_as_root(tmp_path,monkeypatch):
    candidate=qualified(tmp_path,monkeypatch)
    client=SimpleNamespace(open_sftp=Sftp,close=lambda:None)
    monkeypatch.setattr(T,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(T,'fetch_evidence',lambda *a:[])
    calls=[];monkeypatch.setattr(T,'_run',lambda *a,**k:calls.append(a[2]) or 1)
    result=T.run(candidate,tmp_path/'output')
    assert not result['passed'] and result['board_state']=='UNKNOWN'
    assert not any('sudo -S' in c for c in calls)


def test_missing_minor_provenance_rejected_before_network(tmp_path,monkeypatch):
    candidate=qualified(tmp_path,monkeypatch)
    (candidate/'merged_build_receipt.json').write_text('{}')
    monkeypatch.setattr(T,'load_helper',lambda:pytest.fail('unqualified source must not connect'))
    with pytest.raises(ValueError):T.run(candidate,tmp_path/'output')
    assert not (tmp_path/'output').exists()
