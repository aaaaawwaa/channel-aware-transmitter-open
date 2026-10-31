"""Independent acceptance must recompute gates, not trust reported PASS."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def inputs():
    release='c'*32
    records=[]; metas=[]
    for session,seed in enumerate((1,37,73)):
        for i in range(11):
            on=i<10; tag='s%02d-%s-%02d'%(session,'on' if on else 'off',i)
            result=dict(reference_sync=on,bit_errors=0 if on else None,
                compared_bits=138 if on else None,ber=0 if on else None,reason='',
                capture_id=str(i),carrier_sync=on,timing_sync=on,cfo_hz=0.,algorithm_version='v')
            digest=('%064x'%(session*11+i+1))
            records.append(dict(tag=tag,session_index=session,seed=seed,on=on,
                capture_id=str(i),release_id=release,iq_sha256=digest,egress_safe=True,
                result=dict(result,display_ber=result['ber'],display_reference_sync=on,
                            display_current=True,envelope_present=True)))
            metas.append(dict(capture_id=str(i),release_id=release,physical=on,samples=4096,
                sample_rate_hz=60000000,ratio=0,handoff_frame_matches=True,
                iq_sha256=digest,demod_result=dict(result=result,submitted=dict(
                    config=dict(mode='BPSK',symbol_rate_hz=3000000,shaping='RECT',
                                reference_prbs7_seed=seed),decim=1,rx_len=4096,input_fs_hz=60000000)),
                constellation=dict(points=[[1.,0.]]*128 if on else [],symbol_count=201 if on else 0),
                display=dict(result,available=True,current=True),
                ui_events=['loopback','loopback_confirm'] if on else ['capture']))
    report=dict(passed=True,stop_cause=None,expected_release=release,records=records,
        sessions=[dict(index=i,seed=s,closed=True,disarm_ack=True,cause=None) for i,s in enumerate((1,37,73))],
        on_aligned=30,off_false_ber=0,bit_errors=0,compared_bits=4140,aggregate_ber=0.)
    replay=dict(passed=True,frames=33,records=[dict(passed=True) for _ in records])
    manifest=dict(release_id=release,software_contract='cnn-bpsk-teaching-v1',
        software={'rf_bpsk_receiver.py':'a'*64,'rf_cnn_runtime.py':'b'*64},
        model_artifact=dict(model_sha256='d'*64,status='production'))
    declaration=dict(expected_release=release,required_contract='cnn-bpsk-teaching-v1',
        source_hashes={'rf_bpsk_res_bench.py':'e'*64})
    benchmark=dict(passed=True,release_id=release,frames=100,warmup=5,times_ms=[200.]*100,
        p50_ms=200.,p95_ms=200.,max_ms=200.,target_p95_ms=300,cnn_enabled=True,teaching_enabled=True,
        model_sha256='d'*64,algorithm_sha256='a'*64,runtime_sha256='b'*64,
        script_sha256='e'*64,iq_sha256=records[0]['iq_sha256'],peak_rss_kib=10000)
    return report,replay,benchmark,declaration,manifest,metas


def check(values):
    from scripts.rf_teaching_acceptance import check_gates
    return check_gates(*values)


def test_complete_machine_gates_still_leave_human_pending():
    r=check(inputs())
    assert r['machine_passed'] and not r['stage_passed']
    assert r['human_touch']=='NOT_RUN' and r['touch_latency']=='NOT_MEASURED'
    assert r['compared_bits']==4140 and r['p95_ms']==200.


@pytest.mark.parametrize('bad',['duplicate','seed','on','release','physical','domain','handoff',
    'closed','reported_total','row_result','display','actual_display','off_sync','off_points','replay','short_times',
    'nan_time','fake_p95','over_budget','bench_release','bench_model','bench_script','bench_iq',
    'short_path','rss','old_contract'])
def test_false_pass_is_refused(bad):
    v=list(copy.deepcopy(inputs())); r,replay,b,d,m,metas=v
    if bad=='duplicate':r['records'][1]['iq_sha256']=r['records'][0]['iq_sha256']
    elif bad=='seed':r['records'][0]['seed']=12
    elif bad=='on':r['records'][0]['on']=False
    elif bad=='release':r['expected_release']='9'*32
    elif bad=='physical':metas[0]['physical']=False
    elif bad=='domain':metas[0]['demod_result']['submitted']['config']['symbol_rate_hz']=2000000
    elif bad=='handoff':metas[0]['handoff_frame_matches']=False
    elif bad=='closed':r['sessions'][1]['disarm_ack']=False
    elif bad=='reported_total':r['compared_bits']=9999
    elif bad=='row_result':r['records'][0]['result']['compared_bits']=139
    elif bad=='display':r['records'][0]['result']['display_ber']=.5
    elif bad=='actual_display':metas[0]['display']['current']=False
    elif bad=='off_sync':metas[10]['demod_result']['result']['reference_sync']=True
    elif bad=='off_points':metas[10]['constellation']['points']=[[1.,0.]]
    elif bad=='replay':replay['records'][0]['passed']=False
    elif bad=='short_times':b['times_ms']=b['times_ms'][:99]
    elif bad=='nan_time':b['times_ms'][0]=float('nan')
    elif bad=='fake_p95':b['p95_ms']=1.
    elif bad=='over_budget':b.update(times_ms=[350.]*100,p50_ms=350.,p95_ms=350.,max_ms=350.)
    elif bad=='bench_release':b['release_id']='8'*32
    elif bad=='bench_model':b['model_sha256']='8'*64
    elif bad=='bench_script':b['script_sha256']='8'*64
    elif bad=='bench_iq':b['iq_sha256']='8'*64
    elif bad=='short_path':b['teaching_enabled']=False
    elif bad=='rss':b['peak_rss_kib']=0
    elif bad=='old_contract':m['software_contract']='cnn-bpsk-v1'
    assert not check(v)['machine_passed'],bad


def test_missing_files_produce_failed_receipt_without_connecting(tmp_path):
    from scripts.rf_teaching_acceptance import evaluate
    r=evaluate(tmp_path,inputs()[4])
    assert not r['machine_passed'] and r['failures']


def test_teaching_driver_refuses_without_verified_local_candidate_before_network(tmp_path):
    r=subprocess.run([sys.executable,str(ROOT/'scripts/rf_bpsk_ui_board_run.py'),
        '--release','c'*32,'--teaching','--cable-confirmed','--output',str(tmp_path/'new')],
        cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert r.returncode!=0 and 'candidate' in r.stderr.lower()
    assert not (tmp_path/'new').exists()


def test_real_mathematical_replay_is_required_by_directory_gate(tmp_path):
    """Generated IQ exercises production replay; this is not physical RF evidence."""
    import dataclasses
    import hashlib
    import numpy as np
    from test_rf_bpsk_receiver import frame, signal
    from rf_bpsk_pipeline import DemodConfig,analyze_demod
    from rf_bpsk_receiver import BpskConfig
    from rf_rx_constellation import estimate_constellation
    from scripts.rf_teaching_acceptance import evaluate
    report,_,benchmark,declaration,manifest,metas=inputs()
    errors=bits=aligned=0
    for index,row in enumerate(report['records']):
        on=row['on'];rng=np.random.RandomState(100+index)
        z=signal(phase=index*.01,noise=2+index*.1) if on else (
            rng.normal(size=4096)+1j*rng.normal(size=4096))*3
        f=dataclasses.replace(frame(z),physical=on,capture_id=row['capture_id'],release_id=manifest['release_id'])
        e=analyze_demod(f,DemodConfig(BpskConfig(reference_prbs7_seed=row['seed']),7,1,4096,60000000))
        c=estimate_constellation(f,e)
        actual=e.result.to_dict()
        meta=dict(f.to_dict(),format='s16_iq_le',demod_result=dataclasses.asdict(e),
            constellation=c.to_dict(),handoff_frame_matches=True,ui_events=metas[index]['ui_events'],
            iq_sha256=hashlib.sha256(f.payload).hexdigest(),display_constellation=dict(
                available=True,current=True,capture_id=f.capture_id,point_count=len(c.points),
                symbol_count=c.symbol_count,reason=c.reason,normalization=c.normalization),
            display=dict(actual,available=True,current=True))
        row['ui_events']=meta['ui_events'];row['iq_sha256']=meta['iq_sha256']
        row['result']={k:actual[k] for k in ('reference_sync','bit_errors','compared_bits','ber','reason')}
        row['result'].update(display_ber=actual['ber'],display_reference_sync=actual['reference_sync'],
            display_current=True,envelope_present=True)
        if on and actual['reference_sync']:
            aligned+=1;errors+=actual['bit_errors'];bits+=actual['compared_bits']
        (tmp_path/(row['tag']+'.iq')).write_bytes(f.payload)
        (tmp_path/(row['tag']+'.json')).write_text(json.dumps(meta),encoding='utf-8')
    report.update(on_aligned=aligned,bit_errors=errors,compared_bits=bits,aggregate_ber=errors/bits)
    benchmark['iq_sha256']=report['records'][0]['iq_sha256']
    for field,name in (('host_teaching_verifier_sha256','rf_teaching_verify.py'),
                       ('host_acceptance_sha256','rf_teaching_acceptance.py')):
        declaration[field]=hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
    declaration['source_hashes']={name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
        for name in ('rf_bpsk_ui_board_session.py','rf_bpsk_ui_acceptance.py','rf_bpsk_res_bench.py')}
    benchmark['script_sha256']=declaration['source_hashes']['rf_bpsk_res_bench.py']
    for name,data in (('result.json',report),('declaration.json',declaration),
                      ('resource-benchmark.json',benchmark),('transport.json',dict(exit_code=0))):
        (tmp_path/name).write_text(json.dumps(data),encoding='utf-8')
    result=evaluate(tmp_path,manifest)
    assert result['machine_passed'],result
    assert not result['stage_passed'] and result['replay']['frames']==33
    (tmp_path/'transport.json').write_text(json.dumps(dict(exit_code=7)),encoding='utf-8')
    assert not evaluate(tmp_path,manifest)['machine_passed']
    (tmp_path/'transport.json').write_text(json.dumps(dict(exit_code=0)),encoding='utf-8')
    declaration['source_hashes']['rf_bpsk_res_bench.py']='f'*64
    benchmark['script_sha256']='f'*64
    for name,data in (('declaration.json',declaration),('resource-benchmark.json',benchmark)):
        (tmp_path/name).write_text(json.dumps(data),encoding='utf-8')
    assert not evaluate(tmp_path,manifest)['machine_passed']
    declaration['source_hashes']['rf_bpsk_res_bench.py']=hashlib.sha256(
        (ROOT/'scripts/rf_bpsk_res_bench.py').read_bytes()).hexdigest()
    benchmark['script_sha256']=declaration['source_hashes']['rf_bpsk_res_bench.py']
    for name,data in (('declaration.json',declaration),('resource-benchmark.json',benchmark)):
        (tmp_path/name).write_text(json.dumps(data),encoding='utf-8')
    # Change both local displayed/stored symbols: numerical replay must still refuse.
    path=tmp_path/(report['records'][0]['tag']+'.json')
    meta=json.loads(path.read_text(encoding='utf-8'))
    meta['constellation']['points'][0]=[99.,99.]
    path.write_text(json.dumps(meta),encoding='utf-8')
    result=evaluate(tmp_path,manifest)
    assert not result['machine_passed'] and 'host_replay' in result['failures']


def test_teaching_connection_failure_retains_machine_refusal(tmp_path,monkeypatch):
    from scripts import rf_bpsk_ui_board_run as driver
    from types import SimpleNamespace
    candidate=ROOT/'evidence/rf-teaching/s1/candidate/releases/c3bf915c544563324778d1c879b38e3a'
    def failed_connect():raise OSError('test network failure')
    monkeypatch.setattr(driver,'_load_helper',lambda:SimpleNamespace(connect=failed_connect))
    out=tmp_path/'failed'
    code=driver.main(['--release',candidate.name,'--candidate',str(candidate),
        '--teaching','--cable-confirmed','--output',str(out)])
    assert code!=0
    transport=json.loads((out/'transport.json').read_text(encoding='utf-8'))
    assert transport['error_type']=='OSError' and transport['exit_code']!=0
    result=json.loads((out/'machine-acceptance.json').read_text(encoding='utf-8'))
    assert not result['machine_passed'] and not result['stage_passed']
