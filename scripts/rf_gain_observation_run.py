"""Run one finite initialized RX probe, retain failures, and inspect final safety."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import merged_release as R
from scripts.rf_teaching_deploy import load_helper,_run
from scripts.rf_environment_board_run import fetch_evidence
from scripts.rf_teaching_install_state import verified_state


def run(candidate,output):
    candidate,output=Path(candidate).resolve(),Path(output).resolve()
    manifest=R.verify_release(candidate,require_software=True)
    if manifest.get('software_contract')!='cnn-bpsk-environment-v1' or manifest.get('daemon_version')!='1.0.1':
        raise ValueError('exact verified RX observer candidate required')
    if manifest['software']!=R.software_digests(ROOT,'cnn-bpsk-environment-v1'):
        raise ValueError('candidate software source changed')
    output.mkdir(parents=True,exist_ok=False)
    sources=[ROOT/'scripts'/name for name in ('rf_gain_observation_probe.py',
        'rf_environment_board_session.py','rf_environment_pilot.py',
        'rf_bpsk_ui_board_session.py','rf_bpsk_ui_acceptance.py','rf_teaching_install_state.py')]
    remote='/tmp/rf-gain-observation-'+uuid.uuid4().hex
    declaration=dict(expected_release=manifest['release_id'],software=manifest['software'],
        daemon_sha256=manifest['daemon_sha256'],physical_frames=0,observations=3,
        scope='existing arm/init/configure/load; TX source and RX arm remain off; raw reads only',
        source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (output/'declaration.json').write_text(json.dumps(declaration,indent=2),encoding='utf-8')
    result=dict(release_id=manifest['release_id'],passed=False,board_state='UNKNOWN',remote=remote,
        physical_frames=0,formal_physical='NOT_RUN',human_acceptance='NOT_RUN',policy='one attempt; no retry')
    prefix='sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 -u -B '
    client=None
    try:
        helper=load_helper();client=helper.connect()
        with client.open_sftp() as s:
            s.mkdir(remote)
            for p in sources:s.put(str(p),remote+'/'+p.name)
            s.put(str(output/'declaration.json'),remote+'/declaration.json')
        guard=('import hashlib,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
            'd=json.loads((p/"declaration.json").read_text()); '
            'assert all(hashlib.sha256((p/k).read_bytes()).hexdigest()==v '
            'for k,v in d["source_hashes"].items())')
        if _run(client,helper,'python3 -B -c '+shlex.quote(guard)+' '+remote,
                output/'upload-check.log',timeout=15)!=0:
            raise RuntimeError('uploaded probe sources changed; no initialization')
        cmd=prefix+remote+'/rf_gain_observation_probe.py --expected-release '+manifest['release_id']
        cmd+=' --initialize --output '+remote+'/out'
        result['exit_code']=_run(client,helper,cmd,output/'run.log',timeout=120)
    except Exception as exc:result.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if client is not None:
            try:
                with client.open_sftp() as s:result['download_errors']=fetch_evidence(s,remote+'/out',output)
            except Exception as exc:result['download_errors']=[dict(error=str(exc))]
            try:
                # The complete upload guard may have failed. Independently prove
                # this one tool's approved bytes before executing it as root.
                inspector=remote+'/rf_teaching_install_state.py'
                digest=declaration['source_hashes']['rf_teaching_install_state.py']
                check='import hashlib,pathlib,sys; assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()==sys.argv[2]'
                command='python3 -B -c '+shlex.quote(check)+' '+inspector+' '+digest
                result['inspection_upload_exit_code']=_run(client,helper,command,output/'inspection-upload-check.log',timeout=15)
                if result['inspection_upload_exit_code']!=0:
                    raise RuntimeError('independent inspection source unverified; no script executed')
                cmd=prefix+remote+'/rf_teaching_install_state.py '+manifest['release_id']+' '+remote+'/final-state.json'
                result['state_exit_code']=_run(client,helper,cmd,output/'state.log',timeout=30)
                with client.open_sftp() as s:s.get(remote+'/final-state.json',str(output/'final-state.json'))
                if result['state_exit_code']==0 and verified_state(json.loads((output/'final-state.json').read_text(encoding='utf-8')),manifest['release_id']):
                    result['board_state']='VERIFIED_SAFE'
            except Exception as exc:result['inspection_error']=str(exc)
            try:client.close()
            except Exception as exc:result['close_error']=str(exc)
        try:
            probe=json.loads((output/'result.json').read_text(encoding='utf-8'))
            result.update(observations=len(probe.get('observations') or []),
                passed=result.get('exit_code')==0 and result['board_state']=='VERIFIED_SAFE'
                    and not result.get('download_errors')
                    and not any(result.get(k) for k in ('error','inspection_error','close_error'))
                    and probe.get('expected_release')==manifest['release_id'] and probe.get('passed') is True
                    and probe.get('physical_frames')==0 and len(probe.get('observations') or [])==3
                    and probe.get('closed') is True and probe.get('disarm_ack') is True)
        except Exception as exc:result['result_error']=str(exc)
        result['evidence_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
        (output/'receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=run(args.candidate,args.output)
    print(json.dumps({k:v for k,v in result.items() if k!='evidence_sha256'},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
