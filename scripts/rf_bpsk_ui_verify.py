"""Replay retained M4 IQ on the host; verify every frame and display identity."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.rf_bpsk_replay import frame_from_metadata
from rf_bpsk_receiver import BpskConfig, receive_bpsk, VERSION


def verify(directory):
    directory = Path(directory)
    report = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
    checked, hashes = [], []
    for row in report['records']:
        tag = row['tag']
        if Path(tag).name != tag:
            raise ValueError('invalid evidence tag')
        raw = (directory / (tag + '.iq')).read_bytes()
        metadata_bytes = (directory / (tag + '.json')).read_bytes()
        meta = json.loads(metadata_bytes)
        digest = hashlib.sha256(raw).hexdigest()
        hashes.append(digest)
        mismatches = []
        if digest != meta['iq_sha256'] or digest != row['iq_sha256']:
            mismatches.append('iq_sha256')
        envelope = meta['demod_result']
        host = json.loads(json.dumps(receive_bpsk(frame_from_metadata(raw, meta),
            BpskConfig(**envelope['submitted']['config'])).to_dict()))
        arm = envelope['result']
        delta_max = 0.
        for key, value in host.items():
            target = arm.get(key)
            if type(value) is float and type(target) is float:
                delta = abs(value-target)
                delta_max = max(delta_max, delta)
                if not math.isfinite(delta) or delta > 1e-6:
                    mismatches.append(key)
            elif value != target:
                mismatches.append(key)
        for key in ('capture_id', 'release_id'):
            if row.get(key) != meta[key] or arm[key] != meta[key]:
                mismatches.append('identity.'+key)
        if meta['release_id'] != report['expected_release']:
            mismatches.append('release')
        display = meta['display']
        for key in ('capture_id', 'carrier_sync', 'timing_sync', 'reference_sync',
                    'bit_errors', 'compared_bits', 'ber', 'cfo_hz', 'reason',
                    'algorithm_version'):
            value, target = display.get(key), arm[key]
            if key == 'cfo_hz' and type(value) in (int, float) and type(target) in (int, float):
                equal = math.isfinite(value) and math.isfinite(target) and abs(value-target) <= 1e-6
            else:
                equal = value == target
            if not equal:
                mismatches.append('display.'+key)
        if display.get('available') is not True:
            mismatches.append('display.available')
        if display.get('current') is not True:
            mismatches.append('display.current')
        if envelope['algorithm_version'] != VERSION or arm['algorithm_version'] != VERSION:
            mismatches.append('algorithm_version')
        checked.append(dict(tag=tag, mismatches=mismatches, max_float_error=delta_max,
            iq_sha256=digest, metadata_sha256=hashlib.sha256(metadata_bytes).hexdigest()))
    result = dict(scope='host versus ARM on exact new M4 IQ; not GT911 validation',
        frames=len(checked), unique_iq_hashes=len(set(hashes)),
        passed=len(checked)==33 and all(not x['mismatches'] for x in checked),
        max_float_error=max((x['max_float_error'] for x in checked), default=0.),
        tolerance=1e-6, records=checked)
    (directory/'host-arm-parity.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    print(json.dumps({k:v for k,v in result.items() if k != 'records'},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
