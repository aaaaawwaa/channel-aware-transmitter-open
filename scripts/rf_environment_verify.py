"""Replay S2 records against the exact frozen baseline; no hardware access."""
import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
sys.path.insert(0,str(ROOT))
from scripts.rf_teaching_verify import compare, verify_payload
from scripts.rf_bpsk_replay import frame_from_metadata
from rf_environment import EnvironmentBaseline, analyze_environment
from rf_bpsk_receiver import BpskConfig
from rf_bpsk_pipeline import DemodConfig, analyze_demod
from rf_experiment_record import read_record


def verify_record(directory, baseline_path):
    path=Path(baseline_path)
    if path.is_symlink() or path.stat().st_size>131072:
        raise ValueError('unbounded or symbolic baseline')
    baseline=EnvironmentBaseline.from_dict(json.loads(path.read_text(encoding='utf-8')))
    record=read_record(directory);meta=record['metadata']
    stored=meta.get('environment')
    if not isinstance(stored,dict) or stored.get('baseline_sha256')!=baseline.sha256:
        raise ValueError('record needs its exact frozen environmental baseline')
    frame=frame_from_metadata(record['payload'],meta)
    submitted=meta['demod_result']['submitted']
    config=DemodConfig(BpskConfig(**submitted['config']),submitted['revision'],
        submitted['decim'],submitted['rx_len'],submitted['input_fs_hz'])
    envelope=analyze_demod(frame,config)
    measured=analyze_environment(frame,envelope,meta['rf_config'],baseline)
    result=verify_payload(record['payload'],meta)
    result['mismatches']+=compare(stored,measured.to_dict(),'environment')
    result['passed']=not result['mismatches']
    result.update(label=measured.label,baseline_sha256=baseline.sha256,
                  scope='offline same-IQ mathematical replay; zero physical captures')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--record',required=True,type=Path)
    p.add_argument('--baseline',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(argv)
    result=verify_record(a.record,a.baseline)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
