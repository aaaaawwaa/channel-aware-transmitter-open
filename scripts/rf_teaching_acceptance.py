"""S1 machine acceptance from retained evidence; human gates remain explicit.

Offline only. No hardware access or control. Recompute counts/timings and replay
the exact pilot IQ rather than trusting remote summary flags.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
from scripts.rf_teaching_verify import verify_pilot
from rf_experiment_record import MAX_METADATA_BYTES
import merged_release as R

SEEDS=(1,37,73)
CONTRACT='cnn-bpsk-teaching-v1'


def candidate_manifest(directory, expected_release):
    manifest=R.verify_release(Path(directory),require_software=True)
    if manifest['release_id']!=expected_release or manifest.get('software_contract')!=CONTRACT:
        raise ValueError('verified local teaching candidate/release required')
    return manifest


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def check_gates(report,replay,benchmark,declaration,manifest,metas):
    result=dict(machine_passed=False,stage_passed=False,human_touch='NOT_RUN',
        touch_latency='NOT_MEASURED',scope='S1 machine RF/replay/compute gates; human acceptance pending',
        failures=[])
    failures=result['failures']

    def require(condition,name):
        if not condition: failures.append(name)

    try:
        release=manifest['release_id']
        result['expected_release']=release
        require(manifest.get('software_contract')==CONTRACT,'candidate_contract')
        require(report.get('expected_release')==release and declaration.get('expected_release')==release
                and declaration.get('required_contract')==CONTRACT,'release_declaration')
        require(report.get('passed') is True and report.get('stop_cause') is None,'campaign_stopped')
        sessions=report['sessions']; rows=report['records']
        require(len(sessions)==3,'session_count')
        for i,session in enumerate(sessions):
            require(i<3 and session.get('index')==i and session.get('seed')==SEEDS[i]
                and session.get('closed') is True and session.get('disarm_ack') is True
                and session.get('cause') is None,'session_closure_'+str(i))
        require(len(rows)==33 and len(metas)==33,'frame_count')
        aligned=errors=bits=0
        seen_iq=set();seen_capture=set()
        for index,(row,meta) in enumerate(zip(rows,metas)):
            session,i=divmod(index,11)
            if session>=3: raise ValueError('too many records')
            on=i<10; seed=SEEDS[session]
            tag='s%02d-%s-%02d'%(session,'on' if on else 'off',i)
            prefix=tag+':'
            require(row.get('tag')==tag and row.get('session_index')==session
                and row.get('seed')==seed and row.get('on') is on,'layout_'+tag)
            require(row.get('release_id')==release and meta.get('release_id')==release,
                prefix+'release')
            digest=row.get('iq_sha256')
            require(isinstance(digest,str) and len(digest)==64 and digest not in seen_iq
                and digest==meta.get('iq_sha256'),prefix+'duplicate_or_unbound_iq')
            seen_iq.add(digest)
            capture=row.get('capture_id')
            require(isinstance(capture,str) and bool(capture) and capture==meta.get('capture_id')
                and (session,capture) not in seen_capture,prefix+'capture_identity')
            seen_capture.add((session,capture))
            require(meta.get('physical') is on and meta.get('handoff_frame_matches') is True
                and meta.get('samples')==4096 and meta.get('sample_rate_hz')==60000000
                and meta.get('ratio')==0 and row.get('egress_safe') is True,prefix+'capture')
            submitted=meta['demod_result']['submitted']
            require(submitted.get('config')==dict(mode='BPSK',symbol_rate_hz=3000000,
                shaping='RECT',reference_prbs7_seed=seed) and submitted.get('decim')==1
                and submitted.get('rx_len')==4096 and submitted.get('input_fs_hz')==60000000,
                prefix+'submitted_domain')
            require(meta.get('ui_events')==(['loopback','loopback_confirm'] if on else ['capture'])
                and row.get('ui_events',meta.get('ui_events'))==meta.get('ui_events'),prefix+'ui_events')
            actual=meta['demod_result']['result']; observed=row['result']
            display=meta['display']
            require(display.get('available') is True and display.get('current') is True,
                prefix+'actual_display_current')
            for key in ('capture_id','carrier_sync','timing_sync','reference_sync','bit_errors',
                        'compared_bits','ber','cfo_hz','reason','algorithm_version'):
                value,target=display.get(key),actual[key]
                equal=(_finite(value) and _finite(target) and abs(value-target)<=1e-6
                    if key=='cfo_hz' and target is not None else type(value) is type(target) and value==target)
                require(equal,prefix+'actual_display_'+key)
            for key in ('reference_sync','bit_errors','compared_bits','ber','reason'):
                require(observed.get(key)==actual.get(key),prefix+'row_'+key)
            require(observed.get('display_current') is True and observed.get('envelope_present') is True
                and observed.get('display_ber')==actual.get('ber')
                and observed.get('display_reference_sync')==actual.get('reference_sync'),prefix+'display')
            require(actual.get('reason') not in ('CLIPPED','INVALID_IQ'),prefix+'invalid_iq')
            if on and actual.get('reference_sync') is True:
                err,n,ber=(actual.get(k) for k in ('bit_errors','compared_bits','ber'))
                valid=(type(err) is int and type(n) is int and 0<=err<=n and n>0
                    and _finite(ber) and 0<=ber<=1 and abs(ber-err/n)<=1e-12)
                require(valid,prefix+'ber_counts')
                if valid: aligned+=1;errors+=err;bits+=n
            else:
                require(all(actual.get(k) is None for k in ('bit_errors','compared_bits','ber')),
                    prefix+'unexpected_ber')
            if not on:
                require(actual.get('reference_sync') is not True
                    and not meta['constellation'].get('points'),prefix+'off_false_sync')
        aggregate=errors/bits if bits else None
        result.update(on_aligned=aligned,bit_errors=errors,compared_bits=bits,aggregate_ber=aggregate)
        require(aligned>=27 and bits>0 and aggregate<=.01,'ber_gate')
        for name,value in (('on_aligned',aligned),('bit_errors',errors),('compared_bits',bits),
                           ('aggregate_ber',aggregate),('off_false_ber',0)):
            require(report.get(name)==value,'reported_'+name)
        require(replay.get('passed') is True and replay.get('frames')==33
            and len(replay.get('records',[]))==33
            and all(x.get('passed') is True for x in replay['records']),'host_replay')
        times=benchmark.get('times_ms',[])
        complete=(len(times)==100 and all(_finite(t) and t>0 for t in times))
        require(complete,'100_finite_timings')
        if complete:
            ordered=sorted(times); p95=ordered[94];result['p95_ms']=p95
            require(p95<=300,'compute_budget')
            for key,value in (('p50_ms',ordered[50]),('p95_ms',p95),('max_ms',ordered[-1])):
                require(_finite(benchmark.get(key)) and abs(benchmark[key]-value)<=1e-9,'reported_'+key)
        require(benchmark.get('passed') is True and benchmark.get('release_id')==release
            and benchmark.get('frames')==100 and benchmark.get('warmup')==5
            and benchmark.get('target_p95_ms')==300 and benchmark.get('cnn_enabled') is True
            and benchmark.get('teaching_enabled') is True,'full_compute_path')
        require(type(benchmark.get('peak_rss_kib')) is int and benchmark['peak_rss_kib']>0,'peak_rss')
        for field,value in (('model_sha256',manifest['model_artifact']['model_sha256']),
            ('algorithm_sha256',manifest['software']['rf_bpsk_receiver.py']),
            ('runtime_sha256',manifest['software']['rf_cnn_runtime.py']),
            ('script_sha256',declaration['source_hashes']['rf_bpsk_res_bench.py']),
            ('iq_sha256',rows[0]['iq_sha256'])):
            require(benchmark.get(field)==value,'benchmark_'+field)
        first=metas[0]['constellation']
        require(first.get('symbol_count')==201 and len(first.get('points',[]))==128,'benchmark_full_symbols')
    except (KeyError,TypeError,ValueError,IndexError,ZeroDivisionError) as exc:
        failures.append('invalid_evidence:'+type(exc).__name__)
    result['machine_passed']=not failures
    return result


def _read_json(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>MAX_METADATA_BYTES:
        raise ValueError('invalid or oversized evidence file')
    return json.loads(path.read_text(encoding='utf-8'))


def evaluate(directory,manifest):
    directory=Path(directory)
    try:
        report=_read_json(directory/'result.json')
        declaration=_read_json(directory/'declaration.json')
        benchmark=_read_json(directory/'resource-benchmark.json')
        transport=_read_json(directory/'transport.json')
        tags=['s%02d-%s-%02d'%(session,'on' if i<10 else 'off',i)
              for session in range(3) for i in range(11)]
        if [r.get('tag') for r in report['records']]!=tags:
            raise ValueError('pilot layout differs from fixed declaration')
        metas=[_read_json(directory/(tag+'.json')) for tag in tags]
        replay=verify_pilot(directory)
        result=check_gates(report,replay,benchmark,declaration,manifest,metas)
        if type(transport.get('exit_code')) is not int or transport['exit_code']!=0 or transport.get('error_type'):
            result['failures'].append('transport_failure')
        result['replay']=replay
        for field,name in (('host_teaching_verifier_sha256','rf_teaching_verify.py'),
                           ('host_acceptance_sha256','rf_teaching_acceptance.py')):
            if declaration.get(field)!=hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest():
                result['failures'].append('host_source:'+name)
        for name in ('rf_bpsk_ui_board_session.py','rf_bpsk_ui_acceptance.py','rf_bpsk_res_bench.py'):
            if declaration.get('source_hashes',{}).get(name)!=hashlib.sha256(
                    (ROOT/'scripts'/name).read_bytes()).hexdigest():
                result['failures'].append('board_source:'+name)
        result['machine_passed']=not result['failures']
        result['evidence_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [directory/'result.json',directory/'declaration.json',directory/'resource-benchmark.json',
                      directory/'transport.json']
                +[directory/(t+'.json') for t in tags]+[directory/(t+'.iq') for t in tags]}
        return result
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return dict(machine_passed=False,stage_passed=False,human_touch='NOT_RUN',
            touch_latency='NOT_MEASURED',failures=['incomplete_evidence:'+type(exc).__name__],
            detail=str(exc),expected_release=manifest.get('release_id'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    p.add_argument('--candidate',required=True,type=Path)
    p.add_argument('--release',required=True)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(argv)
    if a.output.exists(): p.error('output already exists')
    result=evaluate(a.directory,candidate_manifest(a.candidate,a.release))
    with a.output.open('x',encoding='utf-8') as f: json.dump(result,f,indent=2,allow_nan=False)
    print(json.dumps(result,allow_nan=False))
    return 0 if result['machine_passed'] else 1


if __name__=='__main__':raise SystemExit(main())
