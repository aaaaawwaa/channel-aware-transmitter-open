"""Isolated candidate imports, same-IQ replay and full compute benchmark. No RF I/O."""
import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
try:
    import resource
except ImportError:
    resource=None
import sys
import time


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release-dir',required=True,type=Path)
    p.add_argument('--iq-dir',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--iterations',type=int,default=100)
    a=p.parse_args(argv)
    if a.iterations not in (0,100):raise ValueError('zero or 100 iterations required')
    release=a.release_dir.resolve();sys.path.insert(0,str(release))
    import numpy as np
    import merged_release as R
    import rf_cnn_runtime as CNN
    from rf_status_worker import RfStatusWorker
    from rf_rx_types import RxIqFrame,RxReadback
    from rf_rx_analysis import summarize
    from rf_bpsk_pipeline import DemodConfig,analyze_demod
    from rf_bpsk_receiver import BpskConfig
    from rf_rx_constellation import estimate_constellation
    from rf_environment import analyze_environment,FIXED_RF
    manifest=R.verify_release(release,require_software=True)
    if manifest['software_contract']!='cnn-bpsk-environment-v1':raise ValueError('S2 candidate required')
    worker=RfStatusWorker(release_directory=str(release))
    worker._ensure_model();worker._ensure_demod();worker._ensure_teaching();worker._ensure_environment()
    statuses={name:getattr(worker,name+'_status')() for name in ('model','demod','teaching','environment')}
    if not all(s['available'] for s in statuses.values()):raise ValueError(str(statuses))
    if not statuses['model']['production']:raise ValueError('production CNN required')
    digest_times=[]
    for _ in range(5):
        start=time.perf_counter();worker._environment_baseline.sha256
        digest_times.append((time.perf_counter()-start)*1000)
    records=[];inputs=[]
    for path in sorted(a.iq_dir.glob('*.iq')):
        raw=path.read_bytes();meta=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        if len(raw)!=16384 or hashlib.sha256(raw).hexdigest()!=meta['iq_sha256']:
            raise ValueError('offline source IQ size/hash mismatch')
        frame=RxIqFrame(meta['capture_id'],raw,meta['samples'],meta['sample_rate_hz'],meta['ratio'],
            meta['daemon_timestamp_ns'],meta['connection_generation'],meta['config_revision'],meta['request_id'],
            meta['release_id'],meta['op'],meta['physical'],RxReadback(meta['pre_io_ok']),
            RxReadback(meta['post_io_ok'],rx_armed=meta['post_rx_armed'],
                tx_source_selected=meta['post_tx_source_selected']),meta['completed_at_monotonic'])
        s=meta['demod_result']['submitted']
        config=DemodConfig(BpskConfig(**s['config']),s['revision'],s['decim'],s['rx_len'],s['input_fs_hz'])
        envelope=analyze_demod(frame,config)
        result=analyze_environment(frame,envelope,dict(FIXED_RF),worker._environment_baseline)
        records.append(dict(tag=path.stem,source_release=frame.release_id,
            iq_sha256=meta['iq_sha256'],environment=result.to_dict()))
        inputs.append((frame,config))
    if len(inputs)!=4:raise ValueError('exactly four offline development windows required')
    def compute(frame,config):
        # Rebind only a private benchmark frame to candidate software identity. Original
        # source metadata remains unchanged, and this is never claimed as a physical frame.
        virtual=dataclasses.replace(frame,release_id=manifest['release_id'],
                                    capture_id='offline-compute-'+frame.capture_id)
        points=[time.perf_counter()]
        base=summarize(virtual);points.append(time.perf_counter())
        cnn=worker._predict_frame(virtual)
        points.append(time.perf_counter())
        demod=analyze_demod(virtual,config)
        points.append(time.perf_counter())
        constellation=estimate_constellation(virtual,demod)
        points.append(time.perf_counter())
        env=analyze_environment(virtual,demod,dict(FIXED_RF),worker._environment_baseline)
        points.append(time.perf_counter())
        if cnn is None or cnn.reason=='release_mismatch':raise ValueError('CNN not computed')
        return [(points[i+1]-points[i])*1000 for i in range(5)]
    timings=[];components=[]
    if a.iterations:
        for i in range(5):compute(*inputs[i%4])
        for i in range(100):
            start=time.perf_counter();components.append(compute(*inputs[i%4]));timings.append((time.perf_counter()-start)*1000)
    result=dict(scope='offline candidate computation on retained/derived IQ; zero RF captures/GT911',
        release_id=manifest['release_id'],software=manifest['software'],statuses=statuses,
        baseline_sha256=worker._environment_baseline.sha256,records=records,
        baseline_digest_access_ms=digest_times,
        native_cnn=CNN._CONV_LIB is not None,iterations=len(timings),warmups=5 if timings else 0,
        timings_ms=timings,p95_ms=float(np.percentile(timings,95)) if timings else None,
        component_order=['base','cnn','bpsk','constellation','environment'],component_timings_ms=components,
        p50_ms=float(np.percentile(timings,50)) if timings else None,
        max_ms=max(timings) if timings else None,
        rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None,
        formal_physical='NOT_RUN',human_acceptance='NOT_RUN')
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    print(json.dumps(dict(release_id=result['release_id'],native_cnn=result['native_cnn'],
        frames=len(records),p95_ms=result['p95_ms'],rss_kib=result['rss_kib'])),flush=True)
    worker.stop()
    return 0


if __name__=='__main__':raise SystemExit(main())
