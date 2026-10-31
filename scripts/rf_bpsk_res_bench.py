"""100 hot calculations on saved IQ; installed release verified only by main()."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time


def validate_teaching_result(envelope, constellation):
    """A short-circuit/failure is never a full real-symbol timing observation."""
    result=envelope.result
    if (result.carrier_sync is not True or result.timing_sync is not True
            or result.total_symbols!=201 or constellation is None
            or constellation.reason or constellation.symbol_count!=201
            or len(constellation.points)!=128):
        raise ValueError('resource input did not execute full synchronized real constellation')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('iq',type=Path)
    parser.add_argument('seed',type=int)
    parser.add_argument('output',type=Path)
    parser.add_argument('expected_release')
    parser.add_argument('--teaching',action='store_true')
    args=parser.parse_args(argv)
    release=Path('/home/xilinx/overlays/awg_merged/current').resolve()
    sys.path.insert(0,str(release))
    import merged_release as R
    manifest=R.verify_release(release,require_software=True)
    if manifest['release_id']!=args.expected_release:
        raise SystemExit('REFUSE: expected installed release required')
    teaching=manifest.get('software_contract')=='cnn-bpsk-teaching-v1'
    if args.teaching and not teaching:
        raise SystemExit('REFUSE: teaching benchmark requires teaching contract')
    if args.output.exists():raise FileExistsError(str(args.output))
    if args.iq.stat().st_size!=16384:raise ValueError('full 4096-sample IQ required')
    import numpy as np
    from rf_rx_analysis import summarize
    from rf_cnn_runtime import load_model
    from rf_rx_types import RxIqFrame
    from rf_bpsk_receiver import BpskConfig, receive_bpsk, VERSION
    if teaching:
        from rf_rx_constellation import estimate_constellation
        from rf_bpsk_pipeline import DemodConfig, analyze_demod
    raw=args.iq.read_bytes()
    iq=np.frombuffer(raw,dtype='<i2').reshape(-1,2)
    frame=RxIqFrame(capture_id='bench',payload=raw,samples=4096,sample_rate_hz=60000000,
        ratio=0,daemon_timestamp_ns=0,connection_generation=0,config_revision=0,
        request_id=0,release_id='bench',op='rx.capture',physical=False,
        pre_readback=None,post_readback=None,completed_at_monotonic=0.)
    model=load_model(str(release/'rf_model.json'),
        'f76f2fc05aee93f72b2427f17f41e0bf59ce538f84688b8350a8d0800ddb9d47')
    cfg=BpskConfig('BPSK',3000000,'RECT',args.seed)

    def one():
        analysis=summarize(frame)
        prediction=model.predict(iq,input_fs_hz=60000000,decim=1)
        constellation=None
        if teaching:
            envelope=analyze_demod(frame,DemodConfig(cfg,0,1,4096,60000000))
            result=envelope.result
            constellation=estimate_constellation(frame,envelope)
            # Includes warmup and every measured iteration. Any failure aborts;
            # no drop/retry/substitution can shorten the accepted resource path.
            validate_teaching_result(envelope,constellation)
        else:result=receive_bpsk(frame,cfg)
        return analysis,prediction,result,constellation

    for _ in range(5):one()
    times_ms=[]
    for _ in range(100):
        started=time.monotonic();one();times_ms.append((time.monotonic()-started)*1000)
    ordered=sorted(times_ms)
    import resource
    result=dict(frames=100,warmup=5,p50_ms=ordered[50],p95_ms=ordered[94],max_ms=ordered[-1],
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        numpy_version=np.__version__,python_version=sys.version.split()[0],
        model_sha256=model.model_sha256,bpsk_version=VERSION,
        iq_sha256=hashlib.sha256(raw).hexdigest(),times_ms=times_ms,target_p95_ms=300,
        cnn_enabled=True,teaching_enabled=teaching,passed=ordered[94]<=300,
        release_id=manifest['release_id'],
        scope='100 hot base analysis+CNN+BPSK'+('+real constellation' if teaching else '')+
              '; no capture/network/touch in timing',
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        algorithm_sha256=manifest['software']['rf_bpsk_receiver.py'],
        runtime_sha256=manifest['software']['rf_cnn_runtime.py'])
    with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
