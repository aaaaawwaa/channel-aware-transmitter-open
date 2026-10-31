#!/usr/bin/env python3
"""board-side QPSK ARM replay using the frozen product pipeline entry.

No hardware control: this imports release-local modules and analyzes fixed IQ
files only.  It never calls rf.init, Overlay, capture, TX or the installed
current release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--release', type=Path, required=True)
    ap.add_argument('--inputs', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    release = a.release.resolve()
    sys.path.insert(0, str(release))
    from rf_qpsk_pipeline import freeze_qpsk_demod_config, analyze_qpsk
    from rf_rx_types import RxIqFrame, RxReadback

    rows = []
    started = time.perf_counter()
    for group in ('real', 'negative'):
        for d in sorted((a.inputs / group).iterdir()):
            if not d.is_dir():
                continue
            identity = json.loads((d / 'identity.json').read_text(encoding='utf-8'))
            meta = json.loads((d / 'metadata.json').read_text(encoding='utf-8'))
            iq = np.frombuffer((d / 'frame.iq').read_bytes(), dtype='<i2').reshape(-1, 2)
            samples = int(identity.get('samples', len(iq)))
            fs = int(identity.get('sample_rate_hz', 60_000_000))
            frame = RxIqFrame(
                capture_id=identity.get('capture_id', d.name),
                payload=np.asarray(iq, dtype='<i2').tobytes(), samples=samples,
                sample_rate_hz=fs, ratio=0, daemon_timestamp_ns=1,
                connection_generation=8, config_revision=2, request_id=1,
                release_id=identity.get('release_id'), op='rx', physical=False,
                pre_readback=RxReadback(io_ok=True),
                post_readback=RxReadback(io_ok=True),
                completed_at_monotonic=1.0)
            seed = identity.get('truth_seed')
            enabled = identity.get('reference_enabled')
            if enabled is None:
                enabled = type(seed) is int and identity.get('kind') != 'off'
            draft = {
                'reference_enabled': enabled,
                'reference_seed': seed if enabled else None,
                'expected_mode': 'QPSK', 'shaping': 'RECT',
                'symrate_hz': 3_000_000, 'decim': 1,
                'rx_len': frame.samples, 'input_fs_hz': frame.sample_rate_hz,
            }
            t0 = time.perf_counter()
            try:
                cfg = freeze_qpsk_demod_config(draft, 2, iq_order=identity.get('iq_order', 'IQ'))
                env = analyze_qpsk(frame, cfg)
            except ValueError as exc:
                row = {'id': d.name, 'status': 'CONFIG_REJECTED',
                       'error_type': 'ValueError', 'error': str(exc),
                       'frame_identity': {'capture_id': frame.capture_id,
                                          'release_id': frame.release_id,
                                          'iq_sha256': identity.get('iq_sha256'),
                                          'samples': frame.samples,
                                          'sample_rate_hz': frame.sample_rate_hz,
                                          'iq_order': identity.get('iq_order')}}
            else:
                r = env.result
                row = {
                    'id': d.name, 'status': 'RESULT', 'version': r.algorithm_version,
                    'reason': r.reason, 'carrier_sync': r.carrier_sync,
                    'timing_sync': r.timing_sync, 'reference_sync': r.reference_sync,
                    'cfo_hz': r.cfo_hz, 'timing_offset_samples': r.timing_offset_samples,
                    'phase_ambiguity': r.phase_ambiguity, 'valid_symbols': r.valid_symbols,
                    'alignment_bits': r.alignment_bits,
                    'comparison_start_symbol': r.comparison_start_symbol,
                    'bit_errors': r.bit_errors, 'compared_bits': r.compared_bits,
                    'ber': r.ber,
                    'symbol_preview': [list(x) for x in r.symbol_preview],
                    'bit_preview': list(r.bit_preview),
                    'frame_identity': {'capture_id': r.capture_id,
                                       'release_id': r.release_id,
                                       'iq_sha256': identity.get('iq_sha256'),
                                       'samples': r.samples,
                                       'sample_rate_hz': r.sample_rate_hz,
                                       'iq_order': identity.get('iq_order')}}
            row['seconds'] = time.perf_counter() - t0
            rows.append(row)
    total = time.perf_counter() - started
    out = {
        'schema': 'p3-qpsk-arm-result-v1',
        'release': release.name,
        'python': sys.version,
        'numpy': np.__version__,
        'platform': platform.platform(),
        'rows': rows,
        'total_seconds': total,
        'per_frame_seconds': {r['id']: r['seconds'] for r in rows},
    }
    a.output.write_text(json.dumps(out, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'release': release.name, 'inputs': len(rows),
                      'total_seconds': total,
                      'python': sys.version.split()[0], 'numpy': np.__version__}))


if __name__ == '__main__':
    raise SystemExit(main())
