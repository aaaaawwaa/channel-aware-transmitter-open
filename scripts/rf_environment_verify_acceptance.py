"""Independent formal evidence replay plus 12 derived rejection checks, no RF I/O."""
import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
from scripts.rf_environment_freeze import check_candidate
from scripts.rf_environment_acceptance import validate_frame,EXPECTED,FRAME_FIELDS
from scripts.rf_environment_verify import verify_record
from scripts.rf_bpsk_replay import frame_from_metadata
from rf_bpsk_pipeline import analyze_demod,DemodConfig
from rf_bpsk_receiver import BpskConfig
from rf_environment import analyze_environment,EnvironmentBaseline
from rf_experiment_record import read_record


def rejection_checks(parents,baseline,output,rf):
    """TEST descendants remain TEST, but are explicitly offline, never new captures."""
    output.mkdir(parents=True,exist_ok=False);rows=[]
    for index,(raw,meta) in enumerate(parents[:6]):
        source=frame_from_metadata(raw,meta)
        submitted=meta['demod_result']['submitted']
        config=DemodConfig(BpskConfig(**submitted['config']),submitted['revision'],
            submitted['decim'],submitted['rx_len'],submitted['input_fs_hz'])
        a=np.frombuffer(raw,'<i2').reshape(-1,2)
        for kind in ('low','clipped'):
            if kind=='low':
                values=np.rint(a.astype(float)*(.0,.001,.002,.004,.008,.012)[index]).astype('<i2')
            else:
                values=a.copy();values[100+index,index%2]=-2048 if index%2 else 2047
            payload=values.tobytes();tag='%s-%02d'%(kind,index)
            frame=dataclasses.replace(source,payload=payload,capture_id='offline-'+source.capture_id+'-'+tag,
                physical=False,op='offline-derived')
            env=analyze_environment(frame,analyze_demod(frame,config),rf,baseline)
            passed=env.label=='INVALID' and env.reason in (
                ('NO_ENERGY','LOW_ENERGY') if kind=='low' else ('CLIPPED',))
            (output/(tag+'.iq')).write_bytes(payload)
            rows.append(dict(tag=tag,passed=passed,environment=env.to_dict(),split='test',
                origin='offline derivative / not physical / not calibration',
                physical=False,op='offline-derived',
                parent_identity={key:meta[key] for key in FRAME_FIELDS[:5]},
                parent_metadata_sha256=hashlib.sha256(json.dumps(meta,sort_keys=True,
                    separators=(',',':'),allow_nan=False).encode('ascii')).hexdigest(),
                transformation=dict(kind=kind,scale=(.0,.001,.002,.004,.008,.012)[index]
                    if kind=='low' else None,rail_sample=100+index if kind=='clipped' else None,
                    rail_component=index%2 if kind=='clipped' else None,
                    rail_value=(-2048 if index%2 else 2047) if kind=='clipped' else None),
                parent_iq_sha256=hashlib.sha256(raw).hexdigest(),
                iq_sha256=hashlib.sha256(payload).hexdigest()))
    result=dict(passed=len(rows)==12 and all(r['passed'] for r in rows),rows=rows,
                new_physical_frames=0,scope='six low-energy and six clipped offline TEST descendants')
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    return result


def verify(directory,candidate,output):
    directory,candidate,output=map(Path,(directory,candidate,output))
    freeze,digest=check_candidate(json.loads((directory/'freeze.json').read_text(encoding='utf-8')),candidate)
    result=json.loads((directory/'result.json').read_text(encoding='utf-8'))
    receipt=json.loads((directory/'receipt.json').read_text(encoding='utf-8'))
    declaration=json.loads((directory/'declaration.json').read_text(encoding='utf-8'))
    errors=[];checked=[];ids=set();hashes=set();parents=[]
    collector_names=('rf_environment_board_session.py','rf_environment_pilot.py',
        'rf_bpsk_ui_board_session.py','rf_bpsk_ui_acceptance.py','rf_environment_acceptance.py')
    if (declaration.get('expected_release')!=freeze['expected_release']
            or declaration.get('software')!=freeze['software'] or declaration.get('split')!='test'
            or declaration.get('freeze_sha256')!=digest or declaration.get('sessions')!=6
            or declaration.get('expected_frames')!=108
            or declaration.get('source_hashes')!={name:freeze['tool_hashes'][name] for name in collector_names}):
        errors.append('formal collector declaration/source mismatch')
    if (result.get('freeze_sha256')!=digest or receipt.get('freeze_sha256')!=digest
            or result.get('expected_release')!=freeze['expected_release']
            or result.get('split')!='test' or receipt.get('board_state')!='VERIFIED_SAFE'
            or receipt.get('exit_code')!=0 or receipt.get('download_errors')
            or receipt.get('error') or len(result.get('sessions',[]))!=6
            or any(s.get('closed') is not True or s.get('disarm_ack') is not True
                   for s in result.get('sessions',[]))):errors.append('formal qualification/closure mismatch')
    rows=result.get('records',[])
    if len(rows)!=108:errors.append('incomplete formal TEST')
    layout=[(index,kind,number) for index in range(6)
        for kind in (('normal','narrowband','pulse') if index<3 else ('compound','off','domain'))
        for number in range(10 if index<3 else 2)]
    for row,(index,kind,number) in zip(rows,layout):
        tag='s%02d-%s-%02d'%(index,kind,number)
        try:
            if (row.get('tag')!=tag or row.get('truth')!=kind or row.get('session_index')!=index
                    or row.get('split')!='test' or row.get('reference_seed')!=(1,37,73)[index%3]
                    or row.get('actual_scene_prbs7_seed')!=1):raise ValueError('formal layout/truth mismatch')
            identity,iq_digest=validate_frame(row,directory,freeze,kind,(1,37,73)[index%3])
            if identity in ids or iq_digest in hashes:raise ValueError('duplicate TEST frame')
            ids.add(identity);hashes.add(iq_digest)
            side=json.loads((directory/(tag+'.environment.json')).read_text(encoding='utf-8'))
            record_id=(side.get('record') or {}).get('record_id')
            if not record_id or Path(record_id).name!=record_id:raise ValueError('record id mismatch')
            folder=directory/'records'/record_id
            loaded=read_record(folder);stored=loaded['metadata']
            flat=json.loads((directory/(tag+'.json')).read_text(encoding='utf-8'))
            raw=(directory/(tag+'.iq')).read_bytes()
            if (stored.get('environment')!=row['environment'] or (folder/'frame.iq').read_bytes()!=raw
                    or stored.get('scenario_declaration')!=side.get('scenario_declaration')
                    or stored.get('rf_config')!=side.get('rf_config')
                    or any(stored.get(k)!=flat.get(k) for k in FRAME_FIELDS)
                    or stored.get('demod_result')!=flat.get('demod_result')
                    or stored.get('constellation')!=flat.get('constellation')
                    or loaded['receipt'].get('record_id')!=record_id):raise ValueError('saved record not original frame')
            replay=verify_record(folder,candidate/'rf_environment_baseline.json')
            if not replay['passed']:raise ValueError('same-IQ replay: '+str(replay['mismatches']))
            checked.append(dict(tag=tag,iq_sha256=iq_digest,label=replay['label']))
            if kind=='normal':parents.append((raw,stored))
        except Exception as exc:errors.append(tag+': '+str(exc))
    correct={kind:sum(row.get('truth')==kind and (row.get('environment') or {}).get('label')==label
        for row in rows) for kind,label in EXPECTED.items()}
    negatives=[row for row in rows if row.get('truth') not in EXPECTED]
    false_clear=sum((row.get('environment') or {}).get('label')=='CLEAR' for row in negatives)
    if (any(correct[k]<27 for k in EXPECTED) or len(negatives)!=18 or false_clear):
        errors.append('formal classification threshold failed')
    offline=None
    if not errors:
        baseline=EnvironmentBaseline.from_dict(json.loads((candidate/'rf_environment_baseline.json').read_text()))
        offline=rejection_checks(parents,baseline,output.parent/'offline-rejections',freeze['fixed_rf'])
        if not offline['passed']:errors.append('offline rejection failed')
    summary=dict(machine_passed=not errors and len(checked)==108,errors=errors,checked=checked,
        known_correct=correct,negative_false_clear=false_clear,new_physical_frames=len(rows),
        offline_rejection='PASS' if offline and offline['passed'] else 'NOT_RUN',
        freeze_sha256=digest,release_id=freeze['expected_release'],
        human_acceptance='NOT_RUN',stage_passed=False)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:json.dump(summary,f,indent=2,allow_nan=False)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('test','candidate','output'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args();r=verify(a.test,a.candidate,a.output)
    print(json.dumps({k:v for k,v in r.items() if k!='checked'},indent=2))
    raise SystemExit(0 if r['machine_passed'] else 1)
