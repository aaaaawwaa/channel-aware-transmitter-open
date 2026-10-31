"""Settle campaign gate and failure safety; no actual network is used."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import rf_capture_settle_run as S
from scripts.tests.test_rf_capture_timing_probe import (downloaded_case,
    frozen_baseline_document,safe_state_document,StateSftp)

REPO=Path(__file__).resolve().parents[2]


def test_the_approved_eight_frame_grid_is_a_real_success(tmp_path,monkeypatch):
    """The minor-5 eight-frame comparison, end to end, on a downloaded board image.

    Every frame is a real waiting frame built by the production-shaped fixture:
    the two sessions carry the declared seeds in order, each frame carries its own
    requested wait, and the saved records are reachable ONLY through the downloaded
    root (their receipts name board absolute paths).  The per-frame replay is the
    one boundary replaced -- it needs the DSP chain and a frozen baseline, and it
    is exercised by its own tests -- so what this proves is the DRIVER: the grid it
    accepts, the ordering it records, and the fact that it re-reads every frame's
    record from where the download actually landed rather than from the board path
    the sidecar carries."""
    candidate=qualified(tmp_path,monkeypatch)
    fixture=tmp_path/'fixture';fixture.mkdir()
    rows=[]
    for i,tag in enumerate(S.expected_layout()):
        kind='normal' if 'normal' in tag else 'narrowband'
        wait=1000 if tag.endswith('w1000') else 0
        row=downloaded_case(fixture,tag,i//4,kind,iq_seed=i,settle_wait_us=wait)
        row.update(tag=tag,truth=kind,settle_wait_us=wait)
        rows.append(row)
    sessions=[dict(owned=True,closed=True,disarm_ack=True,reference_seed=1),
              dict(owned=True,closed=True,disarm_ack=True,reference_seed=37)]
    baseline_path=baseline(tmp_path)
    output=tmp_path/'output'
    state=safe_state_document()
    client=SimpleNamespace(open_sftp=lambda:StateSftp(state),close=lambda:None)
    monkeypatch.setattr(S,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(S,'fetch_evidence',
        lambda s,remote,dest:(shutil.copytree(str(fixture),str(dest),dirs_exist_ok=True),[])[1])
    # The replay boundary only: it is the DSP chain's job and has its own tests.
    monkeypatch.setattr('scripts.rf_capture_timing_probe._same_iq_replay',
        lambda record_path,baseline:dict(passed=True,label='CLEAR'))

    def fake_run(client,helper,cmd,log,timeout=0):
        if 'rf_capture_timing_probe.py' in cmd:
            (output/'result.json').write_text(json.dumps(dict(expected_release='release',
                passed=True,comparison_valid=True,records=rows,sessions=sessions)),encoding='utf-8')
        return 0
    monkeypatch.setattr(S,'_run',fake_run)

    result=S.run(candidate,output,baseline_path)
    assert result['passed'] is True
    assert result['board_state']=='VERIFIED_SAFE'
    assert [c['tag'] for c in result['independent_checks']]==S.expected_layout()
    assert [c['settle_wait_us'] for c in result['independent_checks']]==[0,1000,0,1000,1000,0,1000,0]
    assert result['physical_frame_files']==8


def test_the_uploaded_set_is_self_contained_in_a_clean_mock_board(tmp_path):
    """The board has NONE of this tree.

    Build the upload layout in a clean directory -- the upload subdir plus a
    directory standing in for the INSTALLED RELEASE, which is what supplies the
    runtime modules on a real board -- and import the probe and the whole replay
    chain there with the repository NOT on sys.path.  A dependency that exists in
    the repo but was never uploaded fails HERE instead of on hardware, after
    arming.

    This is the check that catches rf_environment_verify (and its
    rf_teaching_verify / rf_bpsk_replay dependencies) missing from the upload
    set: without them the per-frame same-IQ replay has no module to import and
    every frame would fail on the board."""
    board=tmp_path/'board'
    upload=board/S.UPLOAD_SUBDIR; upload.mkdir(parents=True)
    for name in S.SOURCES:
        shutil.copy2(REPO/'scripts'/name, upload/name)
    release=board/'release'; release.mkdir()
    for p in (REPO/'pynq_overlay/sw').glob('*.py'):
        shutil.copy2(p, release/p.name)
    probe=("\nimport importlib,sys\n"
           "sys.path.insert(0,str(sys.argv[1]))\n"
           "sys.path.insert(0,str(sys.argv[2]))\n"
           "for name in ('scripts.rf_capture_timing_probe','scripts.rf_environment_verify',\n"
           "             'scripts.rf_teaching_verify','scripts.rf_bpsk_replay'):\n"
           "    importlib.import_module(name)\n"
           "print('IMPORTS-OK')\n")
    # `scripts.<name>` must resolve, exactly as it does on the board where the
    # probe locates its own package root as parents[1] of itself.
    proc=subprocess.run([sys.executable,'-B','-c',probe,str(board),str(release)],
                        capture_output=True,text=True,cwd=str(tmp_path),
                        env={**os.environ,'PYTHONPATH':''})
    assert 'IMPORTS-OK' in proc.stdout, proc.stderr[-2000:]


def test_every_uploaded_source_is_hashed_in_the_declaration(tmp_path,monkeypatch):
    """The SHA gate has to cover what actually runs: an uploaded file that is not
    in source_hashes is unqualified code executing on the board."""
    c=qualified(tmp_path,monkeypatch)
    wired(monkeypatch)
    output=tmp_path/'output'
    S.run(c,output,baseline(tmp_path))
    d=json.loads((output/'declaration.json').read_text(encoding='utf-8'))
    assert set(d['source_hashes'])==set(S.SOURCES)
    assert d['upload_subdir']==S.UPLOAD_SUBDIR
    for name in S.SOURCES:
        assert (REPO/'scripts'/name).is_file(), name


class Sftp:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def mkdir(self,*args):pass
    def put(self,*args):pass
    def get(self,remote,local):open(local,'w').write('{}')


def qualified(tmp_path,monkeypatch,daemon='1.0.3',minor=5,spectrum_value=0.0):
    c=tmp_path/'candidate';c.mkdir(exist_ok=True)
    (c/'merged_build_receipt.json').write_text(json.dumps(dict(rx_gain_observer_build=dict(
        daemon_version=daemon,daemon_sha256='a'*64,protocol_major=1,protocol_minor=minor))))
    # The candidate carries the frozen baseline the run must be given; with no
    # such file the driver refuses before connecting, which is what the baseline
    # gate is for.  Same canonical document as baseline(), so the two AGREE by
    # construction rather than by a coincidence of formatting.
    (c/'rf_environment_baseline.json').write_text(
        json.dumps(frozen_baseline_document(spectrum_value)),encoding='utf-8')
    monkeypatch.setattr(S.R,'verify_release',lambda *a,**k:dict(
        software_contract='cnn-bpsk-environment-v1',daemon_version=daemon,
        software={},release_id='release',daemon_sha256='a'*64))
    monkeypatch.setattr(S.R,'software_digests',lambda *a:{})
    return c


def baseline(tmp_path,spectrum_value=0.0,name='baseline.json'):
    p=tmp_path/name;p.write_text(json.dumps(frozen_baseline_document(spectrum_value)),encoding='utf-8');return p


def wired(monkeypatch,client=None,calls=None,rc=lambda *a,**k:0):
    client=client or SimpleNamespace(open_sftp=Sftp,close=lambda:None)
    monkeypatch.setattr(S,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(S,'fetch_evidence',lambda *a:[])
    monkeypatch.setattr(S,'_run',lambda *a,**k:(calls.append(a[2]) if calls is not None else None) or rc(*a,**k))
    return client


def test_a_minor_4_candidate_is_rejected_before_any_network(tmp_path,monkeypatch):
    c=qualified(tmp_path,monkeypatch,daemon='1.0.2',minor=4)
    monkeypatch.setattr(S,'load_helper',lambda:pytest.fail('an unqualified daemon must not connect'))
    with pytest.raises(ValueError,match='1.0.3'):
        S.run(c,tmp_path/'output',baseline(tmp_path))
    assert not (tmp_path/'output').exists()


def test_a_daemon_still_declaring_minor_4_is_rejected_before_any_network(tmp_path,monkeypatch):
    """The pairing is the gate, not just the version string: 1.0.3 that still says
    minor 4 has no settling wait and must not run this experiment."""
    c=qualified(tmp_path,monkeypatch,daemon='1.0.3',minor=4)
    monkeypatch.setattr(S,'load_helper',lambda:pytest.fail('a mispaired daemon must not connect'))
    with pytest.raises(ValueError,match='provenance'):
        S.run(c,tmp_path/'output',baseline(tmp_path))


def test_a_missing_baseline_is_rejected_before_any_network(tmp_path,monkeypatch):
    c=qualified(tmp_path,monkeypatch)
    monkeypatch.setattr(S,'load_helper',lambda:pytest.fail('no baseline means no replay'))
    with pytest.raises(ValueError,match='baseline'):
        S.run(c,tmp_path/'output',tmp_path/'absent.json')
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('name,content,match',[
    ('empty.json','   ','empty'),
    ('notjson.json','{"frozen":true}','not usable'),
    ('truncated.json','{"schema_version":1}','not usable'),
])
def test_an_unusable_baseline_is_rejected_before_any_network(tmp_path,monkeypatch,name,content,match):
    """A baseline the replay could never parse must not reach the connection.

    Being a non-empty file of a plausible size is not the property that matters:
    the replay reads it through a strict schema, so anything else fails at the
    FIRST frame -- after the part is armed and a session has already been opened.
    These three are the shapes that used to get through: blank, valid JSON of the
    wrong shape, and a document truncated to one field."""
    c=qualified(tmp_path,monkeypatch)
    bad=tmp_path/name;bad.write_text(content,encoding='utf-8')
    monkeypatch.setattr(S,'load_helper',lambda:pytest.fail('an unusable baseline must not connect'))
    with pytest.raises(ValueError,match=match):
        S.run(c,tmp_path/'output',bad)
    assert not (tmp_path/'output').exists()


def test_a_valid_but_different_baseline_is_rejected_before_any_network(tmp_path,monkeypatch):
    """Parseable is not enough: it has to be THIS candidate's baseline.

    A different frozen baseline would make every recorded frame disagree with its
    own environment, and the run would report that as a frame failure long after
    arming -- indistinguishable, in the receipt, from the instability the
    comparison exists to measure."""
    c=qualified(tmp_path,monkeypatch,spectrum_value=0.0)
    monkeypatch.setattr(S,'load_helper',lambda:pytest.fail('a mismatched baseline must not connect'))
    with pytest.raises(ValueError,match='frozen baseline'):
        S.run(c,tmp_path/'output',baseline(tmp_path,spectrum_value=0.25))
    assert not (tmp_path/'output').exists()


def test_a_candidate_without_a_frozen_baseline_is_rejected_before_any_network(tmp_path,monkeypatch):
    c=qualified(tmp_path,monkeypatch)
    (c/'rf_environment_baseline.json').unlink()
    monkeypatch.setattr(S,'load_helper',lambda:pytest.fail('no frozen baseline to compare against'))
    with pytest.raises(ValueError,match='frozen baseline'):
        S.run(c,tmp_path/'output',baseline(tmp_path))
    assert not (tmp_path/'output').exists()


def test_the_declaration_pins_the_approved_design_and_the_run_asks_for_it(tmp_path,monkeypatch):
    c=qualified(tmp_path,monkeypatch);calls=[]
    wired(monkeypatch,calls=calls)
    output=tmp_path/'output'
    S.run(c,output,baseline(tmp_path))
    d=json.loads((output/'declaration.json').read_text(encoding='utf-8'))
    assert d['maximum_physical_frames']==8 and d['maximum_sessions']==2
    assert d['wait_levels']==[0,1000] and d['protocol_minor']==5
    assert d['expected_layout']==S.expected_layout()
    # Session 0 low-then-high, session 1 high-then-low: the order is part of the
    # approved design, not an implementation detail.
    assert d['expected_layout']==['s00-normal-w0000','s00-normal-w1000',
        's00-narrowband-w0000','s00-narrowband-w1000',
        's01-normal-w1000','s01-normal-w0000',
        's01-narrowband-w1000','s01-narrowband-w0000']
    assert d['baseline_sha256'] and d['source_hashes'] and d['host_driver_sha256']
    assert d['reference_seeds']==[1,37] and d['actual_scene_prbs7_seed']==1
    probe=[c for c in calls if 'rf_capture_timing_probe.py' in c]
    assert len(probe)==1
    assert '--wait-levels 0,1000' in probe[0]
    assert '--baseline' in probe[0] and 'baseline.json' in probe[0]


def test_upload_guard_failure_only_executes_separately_verified_inspection(tmp_path,monkeypatch):
    c=qualified(tmp_path,monkeypatch);calls=[]
    wired(monkeypatch,calls=calls,rc=lambda *a,**k:1 if len(calls)==1 else 0)
    result=S.run(c,tmp_path/'output',baseline(tmp_path))
    assert not result['passed'] and len(calls)==3
    assert 'sudo -S' not in calls[1] and 'rf_teaching_install_state.py' in calls[1]
    assert 'sudo -S' in calls[2] and 'rf_capture_settle_run.py' not in calls[2]


@pytest.mark.parametrize('failure',['download','close'])
def test_cleanup_failures_still_inspect_and_preserve_receipt(tmp_path,monkeypatch,failure):
    c=qualified(tmp_path,monkeypatch);calls=[]
    def fail(*a):raise OSError('injected '+failure)
    client=SimpleNamespace(open_sftp=Sftp,close=fail if failure=='close' else lambda:None)
    monkeypatch.setattr(S,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(S,'fetch_evidence',fail if failure=='download' else lambda *a:[])
    monkeypatch.setattr(S,'_run',lambda *a,**k:calls.append(a[2]) or 0)
    output=tmp_path/'output';result=S.run(c,output,baseline(tmp_path))
    assert not result['passed'] and (output/'receipt.json').exists()
    assert any('sudo -S' in c and 'rf_teaching_install_state.py' in c for c in calls)


def test_a_main_error_and_a_cleanup_error_coexist_without_hiding_each_other(tmp_path,monkeypatch):
    c=qualified(tmp_path,monkeypatch);calls=[]
    def fail(*a):raise OSError('cleanup broke')
    client=SimpleNamespace(open_sftp=Sftp,close=fail)
    monkeypatch.setattr(S,'load_helper',lambda:SimpleNamespace(connect=lambda:client))
    monkeypatch.setattr(S,'fetch_evidence',lambda *a:[])
    # The UPLOAD GUARD passes and the CAPTURE fails: a main error and a cleanup
    # error at once must both be reported, neither masking the other.
    monkeypatch.setattr(S,'_run',
        lambda *a,**k:calls.append(a[2]) or (1 if 'rf_capture_timing_probe.py' in a[2] else 0))
    output=tmp_path/'output';result=S.run(c,output,baseline(tmp_path))
    assert result.get('exit_code')==1 and result.get('close_error')=='cleanup broke'
    assert not result['passed'] and (output/'receipt.json').exists()


def test_unverified_inspector_never_runs_as_root(tmp_path,monkeypatch):
    c=qualified(tmp_path,monkeypatch);calls=[]
    wired(monkeypatch,calls=calls,rc=lambda *a,**k:1)
    result=S.run(c,tmp_path/'output',baseline(tmp_path))
    assert not result['passed'] and result['board_state']=='UNKNOWN'
    assert not any('sudo -S' in c for c in calls)


def test_a_short_campaign_never_passes(tmp_path,monkeypatch):
    """Seven good frames is not eight: the approved design is exactly eight, and
    topping up is not a thing this driver may do."""
    c=qualified(tmp_path,monkeypatch)
    wired(monkeypatch)
    output=tmp_path/'output'
    layout=S.expected_layout()
    def fake_run(client,helper,cmd,log,timeout=0):
        if 'rf_capture_timing_probe.py' in cmd:
            (output/'result.json').write_text(json.dumps(dict(
                expected_release='release',passed=True,comparison_valid=True,
                records=[dict(tag=t,truth='normal',settle_wait_us=0) for t in layout[:7]],
                sessions=[dict(index=i,closed=True,disarm_ack=True) for i in range(2)])),encoding='utf-8')
        return 0
    monkeypatch.setattr(S,'_run',fake_run)
    result=S.run(c,output,baseline(tmp_path))
    assert not result['passed']
