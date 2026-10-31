"""Offline replay of saved teaching records or all retained teaching pilot IQ.

No board connection or executable input loading. Reports mathematical parity,
not human touch validation or a fresh physical measurement.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
from scripts.rf_bpsk_replay import frame_from_metadata
from rf_bpsk_pipeline import DemodConfig, analyze_demod
from rf_bpsk_receiver import BpskConfig, VERSION
from rf_experiment_record import read_record, MAX_METADATA_BYTES, MAX_IQ_BYTES
from rf_rx_constellation import estimate_constellation


def compare(expected,observed,path='',mismatches=None):
    """Compare bounded JSON trees; finite measured floats tolerate ARM rounding."""
    if mismatches is None: mismatches=[]
    if isinstance(expected,dict) and isinstance(observed,dict):
        for key in expected:
            compare(expected[key],observed.get(key),path+'.'+key,mismatches)
        if set(observed)!=set(expected): mismatches.append(path+'.keys')
    elif isinstance(expected,(list,tuple)) and isinstance(observed,(list,tuple)):
        if len(expected)!=len(observed): mismatches.append(path+'.length')
        else:
            for i,(a,b) in enumerate(zip(expected,observed)):
                compare(a,b,path+'['+str(i)+']',mismatches)
    elif type(expected) is float and type(observed) in (float,int):
        if not math.isfinite(expected) or not math.isfinite(observed) or abs(expected-observed)>1e-6:
            mismatches.append(path)
    elif type(expected) is not type(observed) or expected!=observed:
        mismatches.append(path)
    return mismatches


def verify_payload(raw,meta):
    f=frame_from_metadata(raw,meta)
    stored=meta['demod_result']; submitted=stored['submitted']
    config=BpskConfig(**submitted['config'])
    frozen=DemodConfig(config,submitted['revision'],submitted['decim'],submitted['rx_len'],submitted['input_fs_hz'])
    envelope=analyze_demod(f,frozen)
    result=envelope.result
    measured=estimate_constellation(f,envelope)
    mismatches=compare(result.to_dict(),stored['result'],'demod')
    mismatches+=compare(measured.to_dict(),meta.get('constellation'),'constellation')
    if stored['algorithm_version']!=VERSION: mismatches.append('algorithm_version')
    return dict(capture_id=f.capture_id,release_id=f.release_id,passed=not mismatches,
        mismatches=mismatches,symbol_count=measured.symbol_count,points=len(measured.points),tolerance=1e-6)


def verify_record(directory):
    restored=read_record(directory)
    return verify_payload(restored['payload'],restored['metadata'])


def verify_pilot(directory):
    import hashlib
    directory=Path(directory)
    report=json.loads((directory/'result.json').read_text(encoding='utf-8'))
    if len(report['records'])!=33: raise ValueError('expected exactly 33 finite pilot records')
    checked=[];seen=set()
    for row in report['records']:
        tag=row['tag']
        if Path(tag).name!=tag: raise ValueError('invalid evidence tag')
        iq_path=directory/(tag+'.iq'); meta_path=directory/(tag+'.json')
        if iq_path.stat().st_size>MAX_IQ_BYTES or meta_path.stat().st_size>MAX_METADATA_BYTES:
            raise ValueError('evidence exceeds size limit')
        raw=iq_path.read_bytes(); meta=json.loads(meta_path.read_text(encoding='utf-8'))
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=meta['iq_sha256'] or digest!=row['iq_sha256']:
            raise ValueError('IQ hash mismatch')
        checked.append(verify_payload(raw,meta))
        for key in ('capture_id','release_id'):
            if row.get(key)!=meta[key]:checked[-1]['mismatches'].append('row.'+key)
        if tag in seen:checked[-1]['mismatches'].append('duplicate_artifact')
        seen.add(tag)
        constellation=meta.get('constellation') or {}
        display=meta.get('display_constellation') or {}
        expected=dict(available=True,current=True,capture_id=meta['capture_id'],
            point_count=len(constellation.get('points') or []),
            symbol_count=constellation.get('symbol_count'),reason=constellation.get('reason'),
            normalization=constellation.get('normalization'))
        for key,value in expected.items():
            if display.get(key)!=value:checked[-1]['mismatches'].append('display_constellation.'+key)
        if row.get('on') is False and constellation.get('points'):
            checked[-1]['mismatches'].append('OFF_FALSE_CONSTELLATION')
        if meta['release_id']!=report['expected_release']:
            checked[-1]['passed']=False;checked[-1]['mismatches'].append('release')
        checked[-1]['passed']=not checked[-1]['mismatches']
    return dict(passed=report.get('passed') is True and all(x['passed'] for x in checked),
        frames=len(checked),records=checked,scope='offline exact teaching pilot IQ; not new RF or GT911 validation')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    p.add_argument('--pilot',action='store_true')
    p.add_argument('--output',type=Path)
    a=p.parse_args(argv)
    if a.output is not None and a.output.exists(): p.error('output already exists')
    result=verify_pilot(a.directory) if a.pilot else verify_record(a.directory)
    encoded=json.dumps(result,indent=2,allow_nan=False)
    if a.output is not None:
        with a.output.open('x',encoding='utf-8') as f: f.write(encoded)
    print(encoded)
    return 0 if result['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
