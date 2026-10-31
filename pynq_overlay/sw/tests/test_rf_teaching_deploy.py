"""Deployment must use verified immutable bytes and documented human results."""
import io
import json
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
CANDIDATE=ROOT/'evidence/rf-teaching/s1/candidate/releases/c3bf915c544563324778d1c879b38e3a'
HUMAN=ROOT/'evidence/rf-teaching/s1/deployment-2026-10-04/s0-human-confirmation.json'


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    from scripts.rf_teaching_deploy import prepare
    out=tmp_path_factory.mktemp('deploy-bundle')
    # prepare refuses an already-existing output directory.
    return prepare(CANDIDATE,out/'bundle',CANDIDATE.name)


def test_archive_contains_all_installer_paths_from_frozen_candidate(prepared):
    from scripts.rf_teaching_deploy import verify_bundle
    receipt=verify_bundle(Path(prepared['directory']))
    assert receipt['release_id']==CANDIDATE.name
    assert not receipt['installed']
    with tarfile.open(receipt['archive'],'r:gz') as archive:
        names=archive.getnames()
        assert 'pynq_overlay/sw/install.sh' in names
        assert 'pynq_overlay/sw/tests/ddr_e2e.py' in names
        assert 'sdk/src/sincos_q15.h' in names
        for name in ('rf_teaching_profile.py','rf_rx_constellation.py','rf_experiment_record.py'):
            assert 'release/'+name in names and 'pynq_overlay/sw/'+name in names


@pytest.mark.parametrize('kind',['missing','changed','duplicate','symlink','escape'])
def test_bad_archive_is_refused_before_extraction(prepared,tmp_path,kind):
    from scripts.rf_teaching_deploy import inspect_archive
    archive=tmp_path/'bad.tgz'
    expected={'release/example.py':{'sha256':__import__('hashlib').sha256(b'valid').hexdigest(),'bytes':5}}
    with tarfile.open(archive,'w:gz') as stream:
        name='release/example.py'
        if kind=='missing':name='release/other.py'
        if kind=='escape':name='../example.py'
        info=tarfile.TarInfo(name)
        data=b'wrong' if kind=='changed' else b'valid'
        info.size=len(data)
        if kind=='symlink':info.type=tarfile.SYMTYPE;info.linkname='/tmp/target';info.size=0
        stream.addfile(info,io.BytesIO(data))
        if kind=='duplicate':stream.addfile(info,io.BytesIO(data))
    with pytest.raises(ValueError):inspect_archive(archive,expected)


def test_prepare_refuses_wrong_release_before_creating_output(tmp_path):
    from scripts.rf_teaching_deploy import prepare
    with pytest.raises(ValueError):prepare(CANDIDATE,tmp_path/'out','a'*32)
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('bad',['missing_check','false_check','no_cable','not_human'])
def test_incomplete_human_receipt_refuses_network(bad,tmp_path,prepared,monkeypatch):
    from scripts import rf_teaching_deploy as deploy
    human=json.loads(HUMAN.read_text(encoding='utf-8'))
    if bad=='missing_check':human['checks'].pop('off_no_ber')
    elif bad=='false_check':human['checks']['stop_disarm_safe']=False
    elif bad=='no_cable':human['cable_confirmed']=False
    else:human['source']='model inference'
    path=tmp_path/'human.json';path.write_text(json.dumps(human),encoding='utf-8')
    called=[]
    monkeypatch.setattr(deploy,'load_helper',lambda:called.append('network'))
    with pytest.raises(ValueError):deploy.install(Path(prepared['directory']),path,tmp_path/'run')
    assert not called and not (tmp_path/'run').exists()


def test_connection_failure_has_unknown_receipt_and_no_retry(prepared,tmp_path,monkeypatch):
    from scripts import rf_teaching_deploy as deploy
    attempts=[]
    def connect():attempts.append(1);raise OSError('test unreachable')
    monkeypatch.setattr(deploy,'load_helper',lambda:SimpleNamespace(connect=connect))
    result=deploy.install(Path(prepared['directory']),HUMAN,tmp_path/'run')
    assert result['installed'] is False and result['board_state']=='UNKNOWN'
    assert result['error_type']=='OSError' and len(attempts)==1
    assert (tmp_path/'run/install-receipt.json').exists()


def test_safe_check_requires_exact_release_and_both_routes_closed():
    from scripts.rf_teaching_install_state import safe_state
    status=dict(release_id='b'*32,online=True,stale=False,armed=False,
        pl_status=dict(io_ok=True,rx={'armed':False},tx={'source_selected':False}))
    assert safe_state(status,'b'*32)
    assert not safe_state(status,'c'*32)
    status['pl_status']['tx']['source_selected']=True
    assert not safe_state(status,'b'*32)


@pytest.mark.parametrize('bad',['failed_service','unverified','missing_service','foreign_marker','armed','pid_zero'])
def test_rollback_requires_verified_running_services_and_exact_safe_status(bad):
    from scripts.rf_teaching_deploy import verified_state
    release='b'*32
    state=dict(release_verified=True,release_id=release,safe=True,
        active_release='/home/xilinx/overlays/awg_merged/releases/'+release,
        services={name:dict(ActiveState='active',SubState='running',MainPID='123')
                  for name in ('pynq-status','rf-daemon')},
        status=dict(release_id=release,online=True,stale=False,armed=False,
            pl_status=dict(io_ok=True,rx={'armed':False},tx={'source_selected':False})))
    assert verified_state(state,release)
    if bad=='failed_service':state['services']['rf-daemon']['ActiveState']='failed'
    elif bad=='unverified':state['release_verified']=False
    elif bad=='missing_service':state['services'].pop('rf-daemon')
    elif bad=='foreign_marker':state['active_release']='/other/release'
    elif bad=='pid_zero':state['services']['rf-daemon']['MainPID']='0'
    else:state['status']['armed']=True
    assert not verified_state(state,release)


def test_board_main_rejects_marker_mismatch_even_when_safe(tmp_path,monkeypatch):
    from scripts import rf_teaching_install_state as module
    state=dict(safe=True,release_id='b'*32,release_verified=True,active_release='/wrong',
        status=dict(release_id='b'*32,online=True,stale=False,armed=False,
            pl_status=dict(io_ok=True,rx={'armed':False},tx={'source_selected':False})),
        services={name:dict(ActiveState='active',SubState='running',MainPID='123')
                  for name in ('pynq-status','rf-daemon')})
    monkeypatch.setattr(module,'inspect_state',lambda expected:state)
    assert module.main(['b'*32,str(tmp_path/'state.json')])!=0
    assert (tmp_path/'state.json').exists()
