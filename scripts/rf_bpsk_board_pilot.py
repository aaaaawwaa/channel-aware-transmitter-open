"""Finite BPSK cabled pilot on the existing sole daemon. Uploaded, not installed."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

import numpy as np

PARAMS = dict(lo_hz=80000000, sample_rate_hz=60000000, attenuation_mdB=40000,
    phy_mode_1r1t=0, rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000,
    idelay_en=1, idelay_tap=29)


def safe(status):
    return bool(status.online and not status.stale and status.pl_status.io_ok
        and status.armed is False and status.pl_status.rx.armed is False
        and status.pl_status.tx.source_selected is False)


def campaign(client_factory, output, expected_release, token, generate, receive, config_factory):
    """Fresh control connection/init/config per session; cleanup even on failures."""
    report=dict(expected_release=expected_release, parameters=PARAMS, amplitude=60,
        seeds=[1,37,73], requested_on_frames=30, sessions=[], records=[],
        safe=False, passed=False, error=None)
    last_frame=None
    for session_index,seed in enumerate(report['seeds']):
        client=client_factory()
        session=dict(index=session_index,seed=seed,initial=None,final=None,safe=False)
        report['sessions'].append(session)
        owned=False
        try:
            status=client.poll();session['initial']=status.to_dict()
            if not safe(status) or status.release_id != expected_release:
                raise RuntimeError('initial state not safe or release changed; do not take over')
            # Mark ownership before arm so a lost acknowledgement still cleans up.
            owned=True
            client.control_arm(token)
            status=client.poll()
            if not status.online or status.release_id != expected_release: raise RuntimeError('arm poll failed')
            client.control_op('rf.init',{k:PARAMS[k] for k in
                ('lo_hz','sample_rate_hz','attenuation_mdB','phy_mode_1r1t')})
            status=client.poll()
            if not status.online or status.release_id != expected_release: raise RuntimeError('init poll failed')
            client.control_op('rf.configure',PARAMS)
            status=client.poll()
            if not status.online or status.release_id != expected_release: raise RuntimeError('configure poll failed')
            metadata,payload=generate(dict(mode='BPSK',filter='RECT',rolloff='0.35',
                seed=seed,symbol_rate_hz=3000000,sample_rate_hz=60000000,amplitude=60))
            for index in range(11):
                physical=index>0
                if index==1:
                    client.poll()
                    client.control_op('tx.load',metadata,payload)
                frame=client.capture(dict(samples=4096,ratio=0,
                    **(dict(cable_confirmed=True,attenuation_mdB=40000) if physical else {})),physical=physical)
                tag='s%d-%s-%02d'%(session_index,'on' if physical else 'off',index)
                (output/(tag+'.iq')).write_bytes(frame.payload)
                header=frame.to_dict();header['format']='s16_iq_le'
                (output/(tag+'.json')).write_text(json.dumps(header,indent=2))
                r=receive(frame,config_factory(reference_prbs7_seed=seed)).to_dict()
                row=dict(tag=tag,session=session_index,seed=seed,on=physical,
                    iq_sha256=hashlib.sha256(frame.payload).hexdigest(),header=header,result=r)
                report['records'].append(row)
                if (frame.release_id != expected_release or not frame.post_readback.io_ok
                    or frame.post_readback.rx_armed is not False
                    or frame.post_readback.tx_source_selected is not False):
                    raise RuntimeError('capture egress unsafe')
                if r['reason'] in ('CLIPPED','INVALID_IQ'): raise RuntimeError('clipped or invalid IQ; stop campaign')
                if physical: last_frame=frame
                print(json.dumps(dict(tag=tag,reason=r['reason'],ber=r['ber'])),flush=True)
        except Exception as exc:
            report['error']='%s: %s'%(type(exc).__name__,exc)
        finally:
            if owned:
                try:
                    client.poll()
                    client.control_disarm()
                    session['disarm']='ok'
                except Exception as exc:
                    session['disarm_error']=str(exc)
            try:
                status=client.poll();session['final']=status.to_dict()
                session['safe']=safe(status) and status.release_id == expected_release
            except Exception as exc: session['final_error']=str(exc)
            client.close()
            (output/'campaign.json').write_text(json.dumps(report,indent=2))
        if report['error'] or not session['safe'] or 'disarm_error' in session:
            break
    on=[r['result'] for r in report['records'] if r['on']]
    off=[r['result'] for r in report['records'] if not r['on']]
    aligned=[r for r in on if r['reference_sync']]
    errors=sum(r['bit_errors'] for r in aligned)
    bits=sum(r['compared_bits'] for r in aligned)
    report.update(on_captured=len(on),on_aligned=len(aligned),off_captured=len(off),
        off_false_ber=sum(r['ber'] is not None for r in off),bit_errors=errors,
        compared_bits=bits,aggregate_ber=errors/bits if bits else None,
        safe=all(s['safe'] for s in report['sessions']))
    report['passed']=bool(not report['error'] and len(on)==30 and len(off)==3
        and report['safe'] and all(s.get('disarm')=='ok' for s in report['sessions'])
        and len(aligned)>=27 and bits>0 and errors/bits<=.01
        and report['off_false_ber']==0)
    (output/'campaign.json').write_text(json.dumps(report,indent=2))
    return report,last_frame


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expected-release',required=True)
    p.add_argument('--cable-confirmed',required=True,action='store_true')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    release=Path('/home/xilinx/overlays/awg_merged/current').resolve()
    sys.path.insert(0,str(release))
    import merged_release as R
    manifest=R.verify_release(release,require_software=True)
    if manifest['release_id']!=args.expected_release: raise RuntimeError('installed release mismatch')
    from rf_status_client import RfStatusClient
    from rf_iq_signal import generate_digital_frame
    # The new standalone algorithm lives beside this script in a unique /tmp dir.
    from rf_bpsk_receiver import BpskConfig,receive_bpsk,VERSION
    report,last=campaign(lambda:RfStatusClient(timeout=2.),args.output,args.expected_release,
        Path('/etc/rf-control.token').read_text().strip(),generate_digital_frame,receive_bpsk,BpskConfig)
    bench=dict(scope='ARM pure compute; full finite receiver, no capture/IO in timing',
        version=VERSION,python=sys.version,numpy=np.__version__,iterations=0)
    if last is not None and report['safe']:
        cfg=BpskConfig(reference_prbs7_seed=report['records'][-1]['seed'])
        for _ in range(5): receive_bpsk(last,cfg)
        elapsed=[]
        for _ in range(100):
            start=time.perf_counter();receive_bpsk(last,cfg);elapsed.append((time.perf_counter()-start)*1000)
        bench.update(iterations=100,times_ms=elapsed,p95_ms=float(np.percentile(elapsed,95)),
            median_ms=float(np.median(elapsed)),goal_ms=200.,passed=bool(np.percentile(elapsed,95)<=200.))
    (args.output/'arm-benchmark.json').write_text(json.dumps(bench,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('records','sessions')},indent=2),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
