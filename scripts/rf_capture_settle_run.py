"""One bounded settling-wait comparison campaign, never an automatic retry.

The four-frame timing driver next door is LEFT ALONE: it guards an experiment that
ran on daemon 1.0.2 / minor 4, and rewriting it would rewrite what its existing
evidence was produced by.  This is the second entry point, for daemon
1.0.3 / minor 5 and the eight-frame comparison.

Everything here is one transaction: qualify the candidate, upload and hash-check
what will run, run it once, download whatever exists, independently re-check it,
and verify the board is safe.  Any failure preserves the receipt and still
performs the independent safety read -- a run that broke halfway is exactly when
the board state matters most.  A missing original IQ is never success.
"""
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
from scripts.rf_capture_timing_probe import (validate_diagnostic,FIXED_RF,
    APPROVED_WAIT_LEVELS,APPROVED_FRAMES,APPROVED_SESSIONS,SESSION_ORDER,
    SETTLE_WAIT_DEADLINE_NS)

DAEMON_VERSION='1.0.3'
PROTOCOL_MINOR=5
# Everything the board needs that the INSTALLED RELEASE does not already provide.
# The release supplies the runtime modules (rf_environment, rf_bpsk_pipeline,
# rf_status_client, ...) because the board session puts its directory on sys.path.
# These are repo-only tools, and the REPLAY chain is one of them: leaving
# rf_environment_verify / rf_teaching_verify / rf_bpsk_replay out meant the
# per-frame same-IQ replay had no module to import on a clean board, so the whole
# comparison would have failed at the first frame -- on hardware, after arming.
SOURCES=('rf_capture_timing_probe.py','rf_environment_board_session.py',
    'rf_environment_pilot.py','rf_bpsk_ui_board_session.py','rf_bpsk_ui_acceptance.py',
    'rf_teaching_install_state.py',
    'rf_environment_verify.py','rf_teaching_verify.py','rf_bpsk_replay.py')
# Uploaded under scripts/, so a module that resolves its own package root as
# parents[1] finds the repo layout it expects and `scripts.<name>` imports resolve.
UPLOAD_SUBDIR='scripts'


def expected_layout():
    """The tags the approved design produces, in the order it produces them."""
    return ['s%02d-%s-w%04d'%(index,kind,level)
            for index,order in enumerate(SESSION_ORDER)
            for kind in ('normal','narrowband') for level in order]


def load_frozen_baseline(path):
    """Parse a frozen baseline with the SAME class the replay uses.

    The gate used to accept anything that was a non-empty file of a plausible
    size, and the first thing that actually PARSED it was the per-frame replay --
    which runs after the part is armed.  A pre-flight check that cannot fail
    before the connection is not a pre-flight check; a baseline this rejects is
    one the replay would have rejected too, only much later and with hardware in
    a different state."""
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size>131072:
        raise ValueError('bounded frozen environmental baseline required')
    raw=p.read_bytes()
    if not raw.strip():
        raise ValueError('the frozen environmental baseline is empty')
    root=Path(__file__).resolve().parents[1]
    if str(root/'pynq_overlay/sw') not in sys.path:
        sys.path.insert(0,str(root/'pynq_overlay/sw'))
    from rf_environment import EnvironmentBaseline
    try:
        return EnvironmentBaseline.from_dict(json.loads(raw.decode('utf-8')))
    except Exception as exc:
        raise ValueError('the frozen environmental baseline is not usable: %s' % (exc,)) from exc


def run(candidate,output,baseline):
    candidate,output=Path(candidate).resolve(),Path(output).resolve()
    baseline=Path(baseline).resolve()
    manifest=R.verify_release(candidate,require_software=True)
    if (manifest.get('software_contract')!='cnn-bpsk-environment-v1'
            or manifest.get('daemon_version')!=DAEMON_VERSION):
        raise ValueError('exact verified settling-wait candidate required (daemon %s)'
                         % DAEMON_VERSION)
    build=json.loads((candidate/'merged_build_receipt.json').read_text(encoding='utf-8'))
    provenance=build.get('rx_gain_observer_build') or {}
    if (provenance.get('daemon_version')!=DAEMON_VERSION or provenance.get('protocol_major')!=1
            or provenance.get('protocol_minor')!=PROTOCOL_MINOR
            or provenance.get('daemon_sha256')!=manifest['daemon_sha256']):
        raise ValueError('explicit native daemon/major/minor %s/1/%d provenance required'
                         % (DAEMON_VERSION,PROTOCOL_MINOR))
    if manifest['software']!=R.software_digests(ROOT,'cnn-bpsk-environment-v1'):
        raise ValueError('candidate software source changed')
    # The baseline is not merely "a file that exists": the replay reads it with a
    # strict schema, and it must BE the baseline the candidate froze.  A different
    # but still valid baseline would make every recorded frame disagree with its
    # own environment for a reason that has nothing to do with the wait under
    # test, and the run would only find out after arming.
    supplied=load_frozen_baseline(baseline)
    frozen=candidate/'rf_environment_baseline.json'
    if not frozen.is_file() or frozen.is_symlink():
        raise ValueError('the candidate carries no frozen baseline to compare against')
    if supplied.sha256!=load_frozen_baseline(frozen).sha256:
        raise ValueError("the supplied baseline is not this candidate's frozen baseline")
    output.mkdir(parents=True,exist_ok=False)
    baseline_sha=hashlib.sha256(baseline.read_bytes()).hexdigest()
    sources=[ROOT/'scripts'/name for name in SOURCES]
    remote='/tmp/rf-capture-settle-'+uuid.uuid4().hex
    declaration=dict(expected_release=manifest['release_id'],software=manifest['software'],
        daemon_sha256=manifest['daemon_sha256'],protocol_major=1,protocol_minor=PROTOCOL_MINOR,
        wait_levels=sorted(APPROVED_WAIT_LEVELS),maximum_sessions=APPROVED_SESSIONS,
        maximum_physical_frames=APPROVED_FRAMES,session_order=[list(o) for o in SESSION_ORDER],
        expected_layout=expected_layout(),reference_seeds=[1,37],kinds=['normal','narrowband'],
        actual_scene_prbs7_seed=1,fixed_rf=FIXED_RF,split='development-settle',
        baseline_sha256=baseline_sha,
        scope='bounded single-variable settling wait; every frame replayed from its own IQ; '
              'API brackets not sample edges or an AGC trajectory',
        cable_confirmation='user confirmed TX1-RX1; explicit --cable-confirmed',
        source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        upload_subdir=UPLOAD_SUBDIR,
        host_driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        settle_wait_deadline_ns=SETTLE_WAIT_DEADLINE_NS,
        stop_conditions=['clipped','wait short of its request',
                         'wait beyond the declared deadline','missing evidence',
                         'identity mismatch','unsafe egress','close unconfirmed','no retry'])
    (output/'declaration.json').write_text(json.dumps(declaration,indent=2),encoding='utf-8')
    result=dict(release_id=manifest['release_id'],passed=False,board_state='UNKNOWN',remote=remote,
        physical_frame_files=0,formal_physical='NOT_RUN',human_acceptance='NOT_RUN',
        s2_stage_passed=False,
        policy='one bounded campaign; no retry, no failed frame replaced, no frame topped up')
    prefix='sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 -u -B '
    client=None
    try:
        helper=load_helper();client=helper.connect()
        with client.open_sftp() as s:
            s.mkdir(remote)
            s.mkdir(remote+'/'+UPLOAD_SUBDIR)
            for p in sources:s.put(str(p),remote+'/'+UPLOAD_SUBDIR+'/'+p.name)
            s.put(str(output/'declaration.json'),remote+'/declaration.json')
            s.put(str(baseline),remote+'/baseline.json')
        guard=('import hashlib,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
            'd=json.loads((p/"declaration.json").read_text()); sub=d["upload_subdir"]; '
            'assert all(hashlib.sha256((p/sub/k).read_bytes()).hexdigest()==v '
            'for k,v in d["source_hashes"].items()); '
            'assert hashlib.sha256((p/"baseline.json").read_bytes()).hexdigest()'
            '==d["baseline_sha256"]')
        if _run(client,helper,'python3 -B -c '+shlex.quote(guard)+' '+remote,
                output/'upload-check.log',timeout=15)!=0:
            raise RuntimeError('uploaded probe sources or baseline changed; '
                               'no initialization/capture')
        cmd=prefix+remote+'/'+UPLOAD_SUBDIR+'/rf_capture_timing_probe.py --expected-release '+manifest['release_id']
        cmd+=' --cable-confirmed --output '+remote+'/out'
        cmd+=' --wait-levels '+','.join(str(v) for v in sorted(APPROVED_WAIT_LEVELS))
        cmd+=' --baseline '+remote+'/baseline.json'
        result['exit_code']=_run(client,helper,cmd,output/'run.log',timeout=300)
    except Exception as exc:result.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if client is not None:
            try:
                with client.open_sftp() as s:result['download_errors']=fetch_evidence(s,remote+'/out',output)
            except Exception as exc:result['download_errors']=[dict(error=str(exc))]
            try:
                inspector=remote+'/'+UPLOAD_SUBDIR+'/rf_teaching_install_state.py'
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
            result.update(records=len(rows),stop_cause=probe.get('stop_cause'),
                sessions_closed=sessions_closed(sessions))
            # The host re-checks every downloaded frame ITSELF, including the
            # same-IQ replay against its own copy of the baseline, and reads the
            # record from the DOWNLOADED directory rather than the board path the
            # sidecar carries.
            checked=[];seen=set();digests=set()
            for row in rows:
                value=validate_diagnostic(row,output,manifest['release_id'],row['truth'],
                    row.get('settle_wait_us'),baseline,tmp_objects(output))
                identity=tuple(value['identity'])
                if identity in seen or value['iq_sha256'] in digests:
                    raise ValueError('duplicate original settling-wait frame')
                seen.add(identity);digests.add(value['iq_sha256'])
                checked.append(dict(tag=row['tag'],settle_wait_us=row.get('settle_wait_us'),**value))
            result['independent_checks']=checked
            result['passed']=bool(result.get('exit_code')==0 and result['board_state']=='VERIFIED_SAFE'
                and not result.get('download_errors') and not any(result.get(k) for k in ('error','inspection_error','close_error'))
                and probe.get('expected_release')==manifest['release_id']
                and probe.get('passed') is True and probe.get('comparison_valid') is True
                and [r.get('tag') for r in rows]==expected_layout()
                and len(checked)==APPROVED_FRAMES
                and all(c['replay'] and c['replay'].get('passed') is True for c in checked)
                and sessions_closed(sessions) and len(sessions)==APPROVED_SESSIONS
                and [s.get('reference_seed') for s in sessions]==[1,37]
                and all(not any(s.get(k) for k in ('error','cleanup_error','close_error')) for s in sessions))
        except Exception as exc:result['result_error']=str(exc)
        result['physical_frame_files']=sum(p.is_file() and p.stat().st_size==16384 for p in output.glob('s??-*-w????.iq'))
        result['evidence_sha256']={p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.rglob('*') if p.is_file()}
        (output/'receipt.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    return result


def tmp_objects(output):
    """Where the DOWNLOADED records live; see _local_record."""
    return output/'records'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--baseline',required=True,type=Path)
    parser.add_argument('--cable-confirmed',required=True,action='store_true')
    args=parser.parse_args();result=run(args.candidate,args.output,args.baseline)
    print(json.dumps({k:v for k,v in result.items() if k not in ('evidence_sha256','independent_checks')},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
