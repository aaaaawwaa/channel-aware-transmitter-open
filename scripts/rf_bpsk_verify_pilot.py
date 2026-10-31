"""Independently replay every saved physical pilot IQ on the host."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.rf_bpsk_replay import frame_from_metadata
from rf_bpsk_receiver import BpskConfig,receive_bpsk


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    args=p.parse_args();directory=args.directory
    report=json.loads((directory/'campaign.json').read_text(encoding='utf-8'))
    comparisons=[];hashes=[]
    for row in report['records']:
        raw=(directory/(row['tag']+'.iq')).read_bytes()
        digest=hashlib.sha256(raw).hexdigest();hashes.append(digest)
        assert digest==row['iq_sha256']
        meta=json.loads((directory/(row['tag']+'.json')).read_text(encoding='utf-8'))
        assert meta==row['header']
        host=json.loads(json.dumps(receive_bpsk(frame_from_metadata(raw,meta),
            BpskConfig(reference_prbs7_seed=row['seed'])).to_dict()))
        mismatches=[];max_float_error=0.
        for k,actual in host.items():
            expected=row['result'][k]
            if type(actual) is float and type(expected) is float:
                delta=abs(actual-expected);max_float_error=max(max_float_error,delta)
                if delta>1.e-6:mismatches.append(k)
            elif actual!=expected:mismatches.append(k)
        comparisons.append(dict(tag=row['tag'],mismatches=mismatches,max_float_error=max_float_error))
    receipt=dict(scope='host replay versus ARM results on exact fresh physical IQ',
        frames=len(comparisons),all_hashes_match=True,unique_iq_hashes=len(set(hashes)),
        numerical_tolerance=1.e-6,passed=all(not r['mismatches'] for r in comparisons),
        max_float_error=max(r['max_float_error'] for r in comparisons),records=comparisons)
    (directory/'host-arm-parity.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k!='records'},indent=2))
    return 0 if receipt['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
