"""Replay archived TRAIN/VAL only; never select CNN TEST or tune thresholds."""
import hashlib
import json
from pathlib import Path
import sys
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'pynq_overlay/sw'))
from rf_bpsk_receiver import BpskConfig, receive_bpsk
from rf_rx_types import RxIqFrame, RxReadback


def main():
    root = Path('evidence/rx-smart/lowclip-recollect-2026-10-03/data-physical')
    manifest_path = root/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    records = []
    for row in manifest['frames']:
        if row['split'] not in ('train', 'val') or row['exclusion_reasons']:
            continue
        tx = row.get('tx_reference') or {}
        digital = (row['label'] == 'BPSK' and tx.get('symbol_rate_hz') == 3000000
                   and tx.get('shaping') == 'RECT')
        if not digital and row['label'] not in ('OFF', 'TONE') and row.get('other_kind') != 'NOISE':
            continue
        raw = (root/row['iq_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['iq_sha256']
        cfg = row['configuration']
        # Archival manifest lacks native connection/readback telemetry. Explicit
        # placeholders permit pure computation; they do not attest new live IO.
        frame = RxIqFrame(row['capture_id'],raw,row['samples'],cfg['sample_rate_hz'],cfg['ratio'],
            0,0,0,0,row['release_id'],'archival.replay',False,
            RxReadback(False),RxReadback(False),0.)
        r = receive_bpsk(frame,BpskConfig(reference_prbs7_seed=1)).to_dict()
        records.append(dict(capture_id=row['capture_id'],split=row['split'],label=row['label'],
            kind='BPSK3MRECT' if digital else (row.get('other_kind') or row['label']),
            iq_sha256=row['iq_sha256'],result=r))
    summary = {}
    for kind in sorted(set(r['kind'] for r in records)):
        rows = [r['result'] for r in records if r['kind'] == kind]
        aligned = [r for r in rows if r['reference_sync']]
        bits = sum(r['compared_bits'] for r in aligned)
        errors = sum(r['bit_errors'] for r in aligned)
        summary[kind] = dict(frames=len(rows),reference_sync=len(aligned),
            errors=errors,compared_bits=bits,aggregate_ber=errors/bits if bits else None,
            reasons=dict(Counter(r['reason'] for r in rows)))
    out=Path('evidence/rx-bpsk/2026-10-03/development-diagnostic.json')
    out.write_text(json.dumps(dict(scope='archival TRAIN/VAL diagnostic, not fresh acceptance',
        placeholders='missing readbacks=false; generation/revision/request/timestamps=0',
        reference='explicit seed1; all nonzero PRBS7 seeds are cyclic shifts',
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        summary=summary,records=records),indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__': main()
