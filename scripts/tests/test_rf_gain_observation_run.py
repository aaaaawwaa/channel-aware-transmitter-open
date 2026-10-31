"""Host probe failures still attempt independent read-only final inspection."""
from types import SimpleNamespace
import pytest
from scripts import rf_gain_observation_run as G


class Sftp:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def mkdir(self,*args):pass
    def put(self,*args):pass
    def get(self,remote,local):open(local,'w').write('{}')


def test_upload_guard_failure_still_inspects_without_initialization(tmp_path,monkeypatch):
    monkeypatch.setattr(G.R,'verify_release',lambda *a,**k:dict(software_contract='cnn-bpsk-environment-v1',
        daemon_version='1.0.1',software={},release_id='release',daemon_sha256='a'*64))
    monkeypatch.setattr(G.R,'software_digests',lambda *a:{})
    client=SimpleNamespace(open_sftp=Sftp,close=lambda:None)
    monkeypatch.setattr(G,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(G,'fetch_evidence',lambda *a:[])
    calls=[]
    def run(*a,**k):calls.append(a[2]);return 1 if len(calls)==1 else 0
    monkeypatch.setattr(G,'_run',run)
    result=G.run(tmp_path/'candidate',tmp_path/'output')
    assert not result['passed'] and result['physical_frames']==0
    assert len(calls)==3 and 'rf_teaching_install_state.py' in calls[1] and 'sudo -S' not in calls[1]
    assert 'rf_teaching_install_state.py' in calls[2] and 'sudo -S' in calls[2]
    assert not any('rf_gain_observation_probe.py --expected-release' in c for c in calls)


def test_old_daemon_is_rejected_before_connect_or_output(tmp_path,monkeypatch):
    monkeypatch.setattr(G.R,'verify_release',lambda *a,**k:dict(software_contract='cnn-bpsk-environment-v1',daemon_version='1.0.0'))
    monkeypatch.setattr(G,'load_helper',lambda:pytest.fail('must reject before network'))
    output=tmp_path/'output'
    with pytest.raises(ValueError):G.run(tmp_path/'candidate',output)
    assert not output.exists()


@pytest.mark.parametrize('failure', ['download','close'])
def test_cleanup_failures_still_inspect_and_write_receipt(tmp_path,monkeypatch,failure):
    monkeypatch.setattr(G.R,'verify_release',lambda *a,**k:dict(software_contract='cnn-bpsk-environment-v1',
        daemon_version='1.0.1',software={},release_id='release',daemon_sha256='a'*64))
    monkeypatch.setattr(G.R,'software_digests',lambda *a:{})
    def fail(*args):raise OSError('injected '+failure)
    client=SimpleNamespace(open_sftp=Sftp,close=fail if failure=='close' else lambda:None)
    monkeypatch.setattr(G,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(G,'fetch_evidence',fail if failure=='download' else lambda *a:[])
    calls=[]
    monkeypatch.setattr(G,'_run',lambda *a,**k:calls.append(a[2]) or 0)
    output=tmp_path/'output';result=G.run(tmp_path/'candidate',output)
    assert not result['passed'] and (output/'receipt.json').exists()
    assert any('rf_teaching_install_state.py' in c for c in calls)


def test_unverified_inspection_script_is_never_executed(tmp_path,monkeypatch):
    monkeypatch.setattr(G.R,'verify_release',lambda *a,**k:dict(software_contract='cnn-bpsk-environment-v1',
        daemon_version='1.0.1',software={},release_id='release',daemon_sha256='a'*64))
    monkeypatch.setattr(G.R,'software_digests',lambda *a:{})
    client=SimpleNamespace(open_sftp=Sftp,close=lambda:None)
    monkeypatch.setattr(G,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(G,'fetch_evidence',lambda *a:[])
    calls=[]
    monkeypatch.setattr(G,'_run',lambda *a,**k:calls.append(a[2]) or 1)
    result=G.run(tmp_path/'candidate',tmp_path/'output')
    assert not result['passed'] and result['board_state']=='UNKNOWN'
    assert not any('sudo -S' in c for c in calls)
    assert 'inspection_error' in result
