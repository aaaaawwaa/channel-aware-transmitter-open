"""Development checks on retained S1 IQ and derived VAL anomalies; no new RF."""
import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
from scripts.rf_bpsk_replay import frame_from_metadata
from rf_bpsk_receiver import BpskConfig
from rf_bpsk_pipeline import DemodConfig,analyze_demod
from rf_environment import EnvironmentBaseline,analyze_environment,FIXED_RF
from scripts.rf_environment_dataset import validate_splits


def verify(pilot,baseline_path,output):
    pilot,output=Path(pilot),Path(output)
    baseline=EnvironmentBaseline.from_dict(json.loads(Path(baseline_path).read_text(encoding='utf-8')))
    report=json.loads((pilot/'result.json').read_text(encoding='utf-8'))
    records=[];groups=[dict(source) for source in baseline.sources]
    for entry in report['records']:
        if not entry['tag'].startswith('s02'):continue
        tag=entry['tag']
        if Path(tag).name!=tag:raise ValueError('invalid source tag')
        iq_path,meta_path=pilot/(tag+'.iq'),pilot/(tag+'.json')
        if (iq_path.is_symlink() or meta_path.is_symlink() or iq_path.stat().st_size!=16384
                or meta_path.stat().st_size>131072):raise ValueError('unbounded or symbolic source')
        meta=json.loads(meta_path.read_text(encoding='utf-8'));raw=iq_path.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=entry['iq_sha256'] or digest!=meta.get('iq_sha256'):raise ValueError('source hash mismatch')
        groups.append(dict(source_id='s1-'+tag,session_id='s1-'+tag[:3],split='val',iq_sha256=digest))
        validate_splits(groups)
        frame=frame_from_metadata(raw,meta);submitted=meta['demod_result']['submitted']
        cfg=DemodConfig(BpskConfig(**submitted['config']),submitted['revision'],
            submitted['decim'],submitted['rx_len'],submitted['input_fs_hz'])
        cases=[('normal' if entry['on'] else 'off',raw,'physical S1 IQ reused offline')]
        if entry['on']:
            samples=np.frombuffer(raw,'<i2').reshape(-1,2)
            z=samples[:,0].astype(float)+1j*samples[:,1].astype(float)
            gain=float(np.median(np.abs(z)))/60
            n=np.arange(4096)
            for phase,start in ((0.,300),(.7,600),(-1.,1200)):
                tone=np.exp(1j*(2*np.pi*(254*60000000/2540)*n/60000000+phase))
                pulse=np.zeros(4096,complex)
                for offset in (start,start+2540):pulse[offset:min(offset+80,4096)]=1
                for kind,j in (('narrowband',40*gain*tone),('pulse',40*gain*pulse),
                               ('compound',20*gain*(tone+pulse))):
                    values=np.rint(np.column_stack(((z+j).real,(z+j).imag)))
                    if np.any(values<-2048) or np.any(values>2047):raise ValueError('derived signed12 overflow')
                    cases.append((kind,values.astype('<i2').tobytes(),'derived VAL same parent; not physical'))
        for index,(truth,payload,origin) in enumerate(cases):
            f=dataclasses.replace(frame,payload=payload,capture_id=frame.capture_id+'-derived-'+str(index))
            envelope=analyze_demod(f,cfg)
            result=analyze_environment(f,envelope,dict(FIXED_RF),baseline)
            expected=dict(normal='CLEAR',narrowband='NARROWBAND',pulse='PULSE',compound='UNKNOWN')
            correct=result.label==expected[truth] if truth!='off' else result.label in ('UNKNOWN','INVALID')
            records.append(dict(parent_source=tag,split='val',origin=origin,truth=truth,
                iq_sha256=hashlib.sha256(payload).hexdigest(),correct=correct,result=result.to_dict()))
    counts={kind:dict(total=sum(r['truth']==kind for r in records),
        correct=sum(r['truth']==kind and r['correct'] for r in records),
        labels={label:sum(r['truth']==kind and r['result']['label']==label for r in records)
                for label in ('CLEAR','NARROWBAND','PULSE','UNKNOWN','INVALID')})
        for kind in ('normal','narrowband','pulse','compound','off')}
    result=dict(scope='offline development VAL checks / zero new physical S2 frames',
        baseline_sha256=baseline.sha256,counts=counts,records=records,
        s2_stage_passed=False,formal_test='NOT_RUN')
    output.mkdir(parents=True,exist_ok=False)
    (output/'receipt.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pilot',required=True,type=Path);p.add_argument('--baseline',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    result=verify(a.pilot,a.baseline,a.output)
    print(json.dumps(result['counts'],indent=2))
