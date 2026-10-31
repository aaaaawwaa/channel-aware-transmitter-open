"""Bounded offline IQ replay; no board, model or executable input loading."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'pynq_overlay/sw'))
from rf_bpsk_receiver import BpskConfig, receive_bpsk
from rf_rx_types import RxIqFrame, RxReadback


def frame_from_metadata(raw, meta):
    # Native RxIqFrame telemetry has an implicit s16_iq_le contract. If an
    # external producer declares a format, it must agree with that contract.
    if meta.get('format', 's16_iq_le') != 's16_iq_le':
        raise ValueError('unsupported IQ format')
    return RxIqFrame(meta['capture_id'], raw, meta['samples'], meta['sample_rate_hz'],
        meta['ratio'], meta['daemon_timestamp_ns'], meta['connection_generation'],
        meta['config_revision'], meta['request_id'], meta['release_id'], meta['op'],
        meta['physical'], RxReadback(meta['pre_io_ok']),
        RxReadback(meta['post_io_ok'], rx_armed=meta['post_rx_armed'],
                   tx_source_selected=meta['post_tx_source_selected']),meta['completed_at_monotonic'])


def replay(iq_path, metadata_path, output, expected_sha256, config):
    iq_path, metadata_path, output = map(Path,(iq_path,metadata_path,output))
    if output.exists(): raise FileExistsError(str(output))
    if not 4 <= iq_path.stat().st_size <= 16384:
        raise ValueError('IQ size must be 4..16384 bytes')
    if metadata_path.stat().st_size > 65536:
        raise ValueError('metadata size exceeds 65536 bytes')
    raw=iq_path.read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256: raise ValueError('IQ hash mismatch')
    metadata_raw=metadata_path.read_bytes()
    metadata=json.loads(metadata_raw.decode('utf-8'))
    frame=frame_from_metadata(raw,metadata)
    result=receive_bpsk(frame,config).to_dict()
    # JSON arrays are used consistently in the returned and persisted receipt.
    result=json.loads(json.dumps(result))
    receipt=dict(iq_path=str(iq_path.resolve()),metadata_path=str(metadata_path.resolve()),
        iq_sha256=digest,metadata_sha256=hashlib.sha256(metadata_raw).hexdigest(),
        config=asdict(config),result=result,scope='offline snapshot; not a new board measurement')
    output.mkdir(parents=True,exist_ok=False)
    (output/'result.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',required=True,type=Path)
    p.add_argument('--metadata',required=True,type=Path)
    p.add_argument('--sha256',required=True)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--reference-prbs7-seed',type=int)
    p.add_argument('--symbol-rate-hz',type=int,default=3000000)
    args=p.parse_args(argv)
    config=BpskConfig(symbol_rate_hz=args.symbol_rate_hz,reference_prbs7_seed=args.reference_prbs7_seed)
    r=replay(args.iq,args.metadata,args.output,args.sha256,config)['result']
    print(json.dumps(r))
    return 0 if r['timing_sync'] and (args.reference_prbs7_seed is None or r['reference_sync']) else 2


if __name__=='__main__': raise SystemExit(main())
