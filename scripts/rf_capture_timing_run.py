"""One bounded instrumented physical capture campaign, never an automatic retry."""
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
from scripts.rf_environment_board_run import fetch_evidence,sessions_closed
from scripts.rf_teaching_install_state import verified_state
from scripts.rf_capture_timing_probe import validate_diagnostic,FIXED_RF


def run(candidate,output):
    candidate,output=Path(candidate).resolve(),Path(output).resolve()
    manifest=R.verify_release(candidate,require_software=True)
    if manifest.get('software_contract')!='cnn-bpsk-environment-v1' or manifest.get('daemon_version')!='1.0.2':
        raise ValueError('exact verified capture timing candidate required')
    build=json.loads((candidate/'merged_build_receipt.json').read_text(encoding='utf-8'))
    provenance=build.get('rx_gain_observer_build') or {}
    if (provenance.get('daemon_version')!='1.0.2' or provenance.get('protocol_major')!=1
            or provenance.get('protocol_minor')!=4 or provenance.get('daemon_sha256')!=manifest['daemon_sha256']):
        raise ValueError('explicit native daemon/major/minor provenance required')
    if manifest['software']!=R.software_digests(ROOT,'cnn-bpsk-environment-v1'):
        raise ValueError('candidate software source changed')
    output.mkdir(parents=True,exist_ok=False)
    sources=[ROOT/'scripts'/name for name in ('rf_capture_timing_probe.py',
        'rf_environment_board_session.py','rf_environment_pilot.py',
        'rf_bpsk_ui_board_session.py','rf_bpsk_ui_acceptance.py','rf_teaching_install_state.py')]
    remote='/tmp/rf-capture-timing-'+uuid.uuid4().hex
    declaration=dict(expected_release=manifest['release_id'],software=manifest['software'],
        daemon_sha256=manifest['daemon_sha256'],protocol_major=1,protocol_minor=4,
        maximum_physical_frames=4,maximum_sessions=2,reference_seeds=[1,37],kinds=['normal','narrowband'],
        actual_scene_prbs7_seed=1,fixed_rf=FIXED_RF,split='development-timing',
        scope='8 instrumenting clock reads; no settling wait; API brackets not sample edges or gain trajectory',
        cable_confirmation='user confirmed TX1-RX1; explicit --cable-confirmed',
        source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        host_driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        stop_conditions=['clipped','missing evidence','identity mismatch','unsafe egress','close unconfirmed'])
    (output/'declaration.json').write_text(json.dumps(declaration,indent=2),encoding='utf-8')
    result=dict(release_id=manifest['release_id'],passed=False,board_state='UNKNOWN',remote=remote,
        physical_frame_files=0,formal_physical='NOT_RUN',human_acceptance='NOT_RUN',s2_stage_passed=False,
        policy='one bounded campaign; no retry or failed frame replacement')
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
            raise RuntimeError('uploaded probe sources changed; no initialization/capture')
        cmd=prefix+remote+'/rf_capture_timing_probe.py --expected-release '+manifest['release_id']
        cmd+=' --cable-confirmed --output '+remote+'/out'
        result['exit_code']=_run(client,helper,cmd,output/'run.log',timeout=180)
    except Exception as exc:result.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if client is not None:
            try:
                with client.open_sftp() as s:result['download_errors']=fetch_evidence(s,remote+'/out',output)
            except Exception as exc:result['download_errors']=[dict(error=str(exc))]
            try:
                inspector=remote+'/rf_teaching_install_state.py'
                digest=declaration['source_hashes']['rf_teaching_install_state.py']
                check='import hashlib,pathlib,sys; assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()==sys.argv[2]'
                result['inspection_upload_exit_code']=_run(client,helper,'python3 -B -c '+shlex.quote(check)+' '+inspector+' '+digest,
                    output/'inspection-upload-check.log',timeout=15)
                if result['inspection_upload_exit_code']!=0:
                    raise RuntimeError('independent inspection source unverified; no script executed')
                cmd=prefix+inspector+' '+manifest['release_id']+' '+remote+'/final-state.json'
                result['state_exit_code']=_run(client,helper,cmd,output/'state.log',timeout=30)
                with client.open_sftp() as s:s.get(remote+'/final-state.json',str(output/'final-state.json'))
                if result['state_exit_code']==0 and verified_state(json.loads((output/'final-state.json').read_text(encoding='utf-8')),manifest['release_id']):
                    result['board_state']='VERIFIED_SAFE'
            except Exception as exc:result['inspection_error']=str(exc)
            try:client.close()
            except Exception as exc:result['close_error']=str(exc)
        try:
            probe=json.loads((output/'result.json').read_text(encoding='utf-8'))
            rows=probe.get('records') or [];sessions=probe.get('sessions') or []
            result.update(records=len(rows),stop_cause=probe.get('stop_cause'),sessions_closed=sessions_closed(sessions))
            checked=[];seen=set();digests=set()
            for row in rows:
                # The downloaded records live under output/records/<record_id>
                # (see fetch_evidence).  The shared validator now ALWAYS reads
                # the saved record back, and with no root it would fall back to
                # the board-absolute path the sidecar carries -- which does not
                # exist on the host, so every frame would fail for a reason that
                # has nothing to do with the frame.
                value=validate_diagnostic(row,output,manifest['release_id'],row['truth'],
                    records_root=output/'records')
                identity=tuple(value['identity'])
                if identity in seen or value['iq_sha256'] in digests:raise ValueError('duplicate original timing frame')
                seen.add(identity);digests.add(value['iq_sha256']);checked.append(dict(tag=row['tag'],**value))
            result['independent_checks']=checked
            layout=['s00-normal-00','s00-narrowband-00','s01-normal-00','s01-narrowband-00']
            result['passed']=bool(result.get('exit_code')==0 and result['board_state']=='VERIFIED_SAFE'
                and not result.get('download_errors') and not any(result.get(k) for k in ('error','inspection_error','close_error'))
                and probe.get('expected_release')==manifest['release_id'] and probe.get('passed') is True
                and [r.get('tag') for r in rows]==layout and sessions_closed(sessions) and len(sessions)==2
                and [s.get('reference_seed') for s in sessions]==[1,37]
                and all(not any(s.get(k) for k in ('error','cleanup_error','close_error')) for s in sessions))
        except Exception as exc:result['result_error']=str(exc)
        result['physical_frame_files']=sum(p.is_file() and p.stat().st_size==16384 for p in output.glob('s??-*-00.iq'))
        result['evidence_sha256']={p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.rglob('*') if p.is_file()}
        (output/'receipt.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--cable-confirmed',required=True,action='store_true')
    args=parser.parse_args();result=run(args.candidate,args.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('evidence_sha256','independent_checks')},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
