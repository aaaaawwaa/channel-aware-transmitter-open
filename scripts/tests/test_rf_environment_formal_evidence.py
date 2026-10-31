"""Formal evidence contract tests; no candidate imports or physical RF access."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import rf_environment_freeze as F
from scripts import rf_environment_verify_acceptance as V
from scripts import rf_environment_acceptance as A
from scripts.tests.test_rf_environment_acceptance import Session, freeze


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def stub_baseline(monkeypatch, module):
    baseline=SimpleNamespace(sha256='b'*64, calibration_status='development',sources=[])
    monkeypatch.setattr(module,'EnvironmentBaseline',
                        SimpleNamespace(from_dict=lambda _:baseline))
    return baseline


@pytest.mark.parametrize('change',[None,'known_rejected','negative_clear','missing','duplicate'])
def test_freeze_recomputes_complete_development_labels(tmp_path,monkeypatch,change):
    declaration=freeze();candidate=tmp_path/'candidate';pilot=tmp_path/'pilot'
    candidate.mkdir();pilot.mkdir();write_json(candidate/'rf_environment_baseline.json',{})
    manifest=dict(release_id='release',software=declaration['software'],
                  software_contract='cnn-bpsk-environment-v1')
    monkeypatch.setattr(F.R,'verify_release',lambda *args,**kwargs:manifest)
    monkeypatch.setattr(F,'ROOT',tmp_path)
    monkeypatch.setattr(F,'tool_hashes',lambda:{name:'e'*64 for name in F.FORMAL_TOOLS})
    stub_baseline(monkeypatch,F)
    monkeypatch.setattr(F,'check_candidate',lambda value,_:(value,'f'*64))
    labels={'normal':'CLEAR','narrowband':'NARROWBAND','pulse':'PULSE',
            'compound':'UNKNOWN','off':'UNKNOWN','domain':'INVALID'}
    rows=[dict(tag='s%02d-%s-00'%(index,kind),truth=kind,session_index=index,
               reference_seed=(1,37,73)[index],split='development-pilot',
               environment=dict(label=label,reason='TEST_REASON'))
          for index in range(3) for kind,label in labels.items()]
    checked=[dict(tag=row['tag'],label=row['environment']['label'],replay_passed=True)
             for row in rows]
    if change=='known_rejected':checked[0]['label']='UNKNOWN'
    if change=='negative_clear':checked[3]['label']='CLEAR'
    if change=='missing':checked.pop()
    if change=='duplicate':checked[-1]=copy.deepcopy(checked[0])
    # The old summaries intentionally remain successful despite bad checked rows.
    write_json(pilot/'result.json',dict(passed=True,expected_release='release',
        split='development-pilot',records=rows,known_correct=9,negative_false_clear=0,
        sessions=[dict(index=i,closed=True,disarm_ack=True) for i in range(3)]))
    write_json(pilot/'receipt.json',dict(passed=True,board_state='VERIFIED_SAFE',release_id='release'))
    monkeypatch.setattr(F,'verify',lambda *args:dict(passed=True,checked=checked,
        new_physical_frames=18,release_id='release'))
    output=tmp_path/'freeze.json'
    if change is None:
        assert F.build(candidate,pilot,output)['expected_release']=='release'
        assert output.exists()
    else:
        with pytest.raises(ValueError):F.build(candidate,pilot,output)
        assert not output.exists()


def formal_artifacts(tmp_path,monkeypatch):
    declaration=freeze();declaration['tool_hashes']={name:'e'*64 for name in F.FORMAL_TOOLS}
    count={'frames':0,'closes':0}
    A.campaign(lambda:Session(tmp_path,count),tmp_path,'release',declaration)
    result=json.loads((tmp_path/'result.json').read_text())
    _,digest=A.validate_freeze(declaration,'release')
    write_json(tmp_path/'freeze.json',declaration)
    collector_names=('rf_environment_board_session.py','rf_environment_pilot.py',
        'rf_bpsk_ui_board_session.py','rf_bpsk_ui_acceptance.py','rf_environment_acceptance.py')
    write_json(tmp_path/'declaration.json',dict(expected_release='release',software=declaration['software'],
        split='test',freeze_sha256=digest,sessions=6,expected_frames=108,
        source_hashes={name:declaration['tool_hashes'][name] for name in collector_names}))
    write_json(tmp_path/'receipt.json',dict(freeze_sha256=digest,board_state='VERIFIED_SAFE',
                                          exit_code=0,download_errors=[],error=None))
    candidate=tmp_path/'candidate';candidate.mkdir()
    write_json(candidate/'rf_environment_baseline.json',{})
    monkeypatch.setattr(V,'check_candidate',lambda *args:(declaration,digest))
    stub_baseline(monkeypatch,V)
    monkeypatch.setattr(V,'rejection_checks',lambda *args:dict(passed=True))
    def replay(folder,_):
        stored=json.loads((folder/'metadata.json').read_text())
        return dict(passed=True,label=stored['environment']['label'],mismatches=[])
    monkeypatch.setattr(V,'verify_record',replay)
    for row in result['records']:
        tag=row['tag'];meta=json.loads((tmp_path/(tag+'.json')).read_text())
        side=json.loads((tmp_path/(tag+'.environment.json')).read_text())
        meta['format']='s16_iq_le'
        meta['constellation']=dict((key,meta[key]) for key in A.IDENTITY)
        meta['constellation'].update(algorithm_version=declaration['algorithm_version'],
                                     submitted_revision=1,points=[])
        write_json(tmp_path/(tag+'.json'),meta)
        record_id='experiment-'+tag;folder=tmp_path/'records'/record_id
        stored=copy.deepcopy(meta);stored.update(environment=side['environment'],
            rf_config=side['rf_config'],scenario_declaration=side['scenario_declaration'])
        write_json(folder/'metadata.json',stored)
        raw=(tmp_path/(tag+'.iq')).read_bytes();(folder/'frame.iq').write_bytes(raw)
        write_json(folder/'receipt.json',dict(record_id=record_id,capture_id=meta['capture_id'],
            iq_sha256=hashlib.sha256(raw).hexdigest(),schema_version=1,
            metadata_sha256=hashlib.sha256((folder/'metadata.json').read_bytes()).hexdigest()))
        side['record']=dict(record_id=record_id);write_json(tmp_path/(tag+'.environment.json'),side)
    return candidate


@pytest.mark.parametrize('change',[None,'header','seed','constellation'])
def test_formal_saved_header_demod_constellation_must_match_flat(tmp_path,monkeypatch,change):
    candidate=formal_artifacts(tmp_path,monkeypatch)
    folder=tmp_path/'records'/'experiment-s00-normal-00'
    stored=json.loads((folder/'metadata.json').read_text())
    if change=='header':stored['request_id']+=1
    elif change=='seed':stored['demod_result']['submitted']['config']['reference_prbs7_seed']=37
    elif change=='constellation':stored['constellation']['config_revision']=99
    if change is not None:
        write_json(folder/'metadata.json',stored)
        # Even an internally consistent archive receipt cannot override flat capture truth.
        receipt=json.loads((folder/'receipt.json').read_text())
        receipt['metadata_sha256']=hashlib.sha256((folder/'metadata.json').read_bytes()).hexdigest()
        write_json(folder/'receipt.json',receipt)
    result=V.verify(tmp_path,candidate,tmp_path/'verify.json')
    assert result['machine_passed'] is (change is None)
    assert result['human_acceptance']=='NOT_RUN' and not result['stage_passed']
    if change is None:assert len(result['checked'])==108
    else:assert any('s00-normal-00' in error for error in result['errors'])


def test_offline_descendants_bind_full_parent_and_transformation(tmp_path,monkeypatch):
    session=Session(tmp_path,{'frames':0,'closes':0});parents=[]
    for index in range(6):
        tag='parent-%02d'%index;session.capture(tag,True)
        meta=json.loads((tmp_path/(tag+'.json')).read_text());meta['tag']=tag
        parents.append(((tmp_path/(tag+'.iq')).read_bytes(),meta))
    monkeypatch.setattr(V,'analyze_demod',lambda *args:None)
    def diagnose(frame,*args):
        clipped=any(abs(int(value))>=2047 for value in V.np.frombuffer(frame.payload,'<i2'))
        value=dict((key,getattr(frame,key)) for key in A.IDENTITY)
        value.update(label='INVALID',reason='CLIPPED' if clipped else 'NO_ENERGY')
        return SimpleNamespace(label=value['label'],reason=value['reason'],to_dict=lambda:value)
    monkeypatch.setattr(V,'analyze_environment',diagnose)
    result=V.rejection_checks(parents,None,tmp_path/'derived',freeze()['fixed_rf'])
    assert result['passed'] and result['new_physical_frames']==0 and len(result['rows'])==12
    for index,(raw,meta) in enumerate(parents):
        for row in result['rows'][index*2:index*2+2]:
            assert row['parent_identity']=={key:meta[key] for key in A.IDENTITY}
            assert row['parent_iq_sha256']==hashlib.sha256(raw).hexdigest()
            expected_meta=hashlib.sha256(json.dumps(meta,sort_keys=True,separators=(',',':'),
                allow_nan=False).encode('ascii')).hexdigest()
            assert row['parent_metadata_sha256']==expected_meta
            assert row['physical'] is False and row['op']=='offline-derived'
            assert row['split']=='test'
            assert isinstance(row['transformation'],dict) and row['transformation']


@pytest.mark.parametrize('dependency',['rf_environment_verify_pilot.py','rf_environment_verify.py',
    'rf_teaching_verify.py','rf_bpsk_replay.py'])
def test_host_mathematical_replay_dependency_tampering_invalidates_freeze(tmp_path,monkeypatch,dependency):
    declaration=freeze();candidate=tmp_path/'candidate';candidate.mkdir()
    write_json(candidate/'rf_environment_baseline.json',{})
    manifest=dict(release_id='release',software=declaration['software'],
                  software_contract='cnn-bpsk-environment-v1')
    monkeypatch.setattr(F.R,'verify_release',lambda *args,**kwargs:manifest)
    monkeypatch.setattr(F.R,'software_digests',lambda *args:declaration['software'])
    monkeypatch.setattr(F,'ROOT',tmp_path)
    stub_baseline(monkeypatch,F)
    scripts=tmp_path/'scripts';scripts.mkdir()
    # Keep the hash calculation real. Only release/source eligibility is mocked.
    names=set(F.FORMAL_TOOLS)|{'rf_environment_verify_pilot.py','rf_environment_verify.py',
        'rf_teaching_verify.py','rf_bpsk_replay.py'}
    for name in names:(scripts/name).write_text('frozen content: '+name,encoding='utf-8')
    declaration['tool_hashes']=F.tool_hashes()
    assert F.check_candidate(declaration,candidate)[0]==declaration
    (scripts/dependency).write_text('changed replay logic',encoding='utf-8')
    with pytest.raises(ValueError,match='exact verified candidate'):
        F.check_candidate(declaration,candidate)
