#!/usr/bin/env python3
"""Summarize the A 6-frame QPSK real-demod smoke, with same-IQ offline sidecar.

The real worker is authoritative.  This tool never invents a BER / denominator and
never replaces a failed real result with an offline result.  The same-IQ offline
product replay is an explicitly separate, optional sidecar (prepared only until
real IQ exists).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(r'<LOCAL_PATH>')
EV = ROOT / 'evidence/rf-teaching/s2-46db-qpsk-real-rx-2026-10-24'
DEFAULT_RUNNER = EV / 'runner'
DEFAULT_TRUTH = EV / 'truth.json'
DEFAULT_RELEASE = ROOT / 'evidence/rf-teaching/s2-46db-p3-qpsk-candidate-2026-10-23/candidate/release'
CRITERIA = {
    'structure': 'required real-worker fields exact/present; missing => unavailable',
    'identity': 'label/mode/seed/tx payload/IQ identity/capture exact; QI explicit',
    'sync_reason': 'reported sync flags and reason exact; QPSK expected sync true, reason ""',
    'ber_counts': 'QPSK valid_symbols=127, compared_bits=128, integer bit_errors; exact if same-IQ sidecar exists',
    'denominator': 'do not supplement a missing/invalid denominator; mark unavailable',
    'timing': 'recorded only, never a pass gate',
    'bpsk': 'reported in a separate BPSK-compatibility section; never counted as QPSK',
    'off': 'no QPSK sync/BER may be invented; classification false-accept is separate',
    'same_iq': 'offline product replay is side-by-side only; never overwrites real worker',
    'claims': '6-frame development smoke; no low-BER or overall-accuracy claim',
}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def norm_label(v):
    return re.sub(r'[^a-z0-9]+', '-', str(v).lower()).strip('-')


def pick(d, *names):
    for name in names:
        if isinstance(d, dict) and name in d and d[name] is not None:
            return d[name]
    return None


def load_records(runner_dir):
    records = []
    seen = set()
    if not runner_dir or not Path(runner_dir).is_dir():
        return records
    paths = sorted(Path(runner_dir).rglob('*.json'),
                   key=lambda p: (p.name != 'frame-result.json', str(p)))
    for p in paths:
        if p.name in ('summary.json', 'truth.json', 'manifest.json'):
            continue
        try:
            obj = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        for item in _flatten(obj):
            if not isinstance(item, dict):
                continue
            tag = norm_label(pick(item, 'tag', 'label', 'frame_label', 'name') or '')
            key = tag or str(p.relative_to(runner_dir).parent) + '/' + str(pick(item, 'frame_index') or '')
            if key in seen:
                continue
            seen.add(key)
            item['_source'] = str(p.relative_to(runner_dir))
            item['_runner_dir'] = str(runner_dir)
            records.append(item)
    return records


def _flatten(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _flatten(x)
        return
    if not isinstance(obj, dict):
        return
    for key in ('frames', 'records', 'results', 'items', 'rows', 'attempts'):
        if isinstance(obj.get(key), list):
            for x in obj[key]:
                yield from _flatten(x)
            return
    # A per-frame result file / a record with a nested result.
    if any(k in obj for k in ('result', 'worker', 'demod', 'qpsk', 'label', 'frame_index')):
        yield obj


def record_result(rec):
    for key in ('worker', 'demod', 'qpsk', 'result', 'modem_result', 'final_result'):
        value = rec.get(key)
        if isinstance(value, dict):
            return value
    return rec


def record_meta(rec):
    meta = {}
    sources = [rec]
    for key in ('tx', 'frame', 'metadata', 'tx_load_evidence', 'frame_identity'):
        value = rec.get(key)
        if isinstance(value, dict):
            sources.append(value)
    for src in sources:
        if isinstance(src, dict):
            meta.update({k: v for k, v in src.items() if v is not None})
    # A frame-result record has a sibling <tag>.json with capture command timing.
    runner = rec.get('_runner_dir')
    source = rec.get('_source')
    tag = pick(rec, 'tag', 'label')
    if runner and source and tag:
        sibling = Path(runner) / Path(source).parent / (str(tag) + '.json')
        try:
            sibling_data = json.loads(sibling.read_text(encoding='utf-8'))
            if isinstance(sibling_data, dict):
                for key, value in sibling_data.items():
                    if value is not None and key not in meta:
                        meta[key] = value
        except Exception:
            pass
    return meta


def find_truth(rec, rows):
    label = norm_label(pick(rec, 'label', 'tag', 'frame_label', 'name', 'id') or '')
    seed = pick(rec, 'tx_seed', 'seed')
    mode = pick(rec, 'mode', 'tx_mode', 'truth_class', 'label_mode')
    idx = pick(rec, 'frame_index', 'index', 'frame_no')
    for row in rows:
        if label and norm_label(row['label']) == label:
            return row
    if idx is not None:
        try:
            for row in rows:
                if int(row['frame_index']) == int(idx):
                    return row
        except (TypeError, ValueError):
            pass
    if seed is not None and mode:
        for row in rows:
            if row.get('mode') == str(mode).upper() and row.get('seed') == seed:
                return row
    for row in rows:
        if seed is not None and row.get('seed') == seed:
            return row
    return None


def find_iq(rec, runner_dir, row):
    for key in ('iq_file', 'iq_path', 'frame_iq', 'iq'):
        p = rec.get(key)
        if isinstance(p, str) and Path(p).is_file():
            return Path(p)
    source = rec.get('_source')
    if source:
        d = Path(runner_dir) / Path(source).parent
        tag = str(rec.get('tag') or rec.get('label') or '')
        for name in ('frame.iq', row['label'] + '.iq', tag + '.iq', 'iq.bin'):
            if name and (d / name).is_file():
                return d / name
    if runner_dir:
        for p in Path(runner_dir).rglob('*.iq'):
            if row['label'].replace('-', '') in norm_label(p.stem).replace('-', ''):
                return p
    return None


def offline_qpsk(iq_path, row, release):
    """Optional same-IQ product replay; never writes into the real record."""
    old = list(sys.path)
    try:
        sys.path.insert(0, str(release))
        from rf_qpsk_pipeline import freeze_qpsk_demod_config, analyze_qpsk
        from rf_rx_types import RxIqFrame, RxReadback
        raw = Path(iq_path).read_bytes()
        samples = len(raw) // 4
        frame = RxIqFrame(capture_id='offline-%s' % row['label'],
                          payload=raw, samples=samples,
                          sample_rate_hz=60_000_000, ratio=0, daemon_timestamp_ns=1,
                          connection_generation=1, config_revision=1, request_id=1,
                          release_id='offline', op='rx', physical=False,
                          pre_readback=RxReadback(io_ok=True),
                          post_readback=RxReadback(io_ok=True),
                          completed_at_monotonic=1.0)
        draft = {'reference_enabled': True, 'reference_seed': row['seed'],
                 'expected_mode': 'QPSK', 'shaping': 'RECT', 'symrate_hz': 3_000_000,
                 'decim': 1, 'rx_len': frame.samples, 'input_fs_hz': 60_000_000}
        cfg = freeze_qpsk_demod_config(draft, 1, iq_order='QI')
        r = analyze_qpsk(frame, cfg).result
        return {'status': 'RESULT', 'carrier_sync': r.carrier_sync,
                'timing_sync': r.timing_sync, 'reference_sync': r.reference_sync,
                'reason': r.reason, 'valid_symbols': r.valid_symbols,
                'bit_errors': r.bit_errors, 'compared_bits': r.compared_bits,
                'ber': r.ber, 'iq_order': 'QI',
                'iq_sha256': sha(iq_path), 'samples': samples}
    except Exception as exc:  # noqa: BLE001
        return {'status': 'ERROR', 'error': '%s: %s' % (type(exc).__name__, exc)}
    finally:
        sys.path[:] = old


def compare_real_offline(real, offline):
    """Side-by-side comparison only; the real worker remains authoritative."""
    diffs = []
    for key in ('carrier_sync', 'timing_sync', 'reference_sync', 'reason',
                'valid_symbols', 'bit_errors', 'compared_bits', 'ber'):
        if real.get(key) != offline.get(key):
            diffs.append({'field': key, 'real': real.get(key), 'offline': offline.get(key)})
    return {'status': 'MATCH' if not diffs else 'DIFFER',
            'diffs': diffs, 'real_authoritative': True,
            'timing_not_gate': True}


def real_view(rec, row):
    res = record_result(rec)
    meta = record_meta(rec)
    fid = pick(rec, 'frame_identity') or pick(res, 'frame_identity') or {}
    view = {
        'label': row['label'], 'truth_mode': row['mode'], 'truth_seed': row.get('seed'),
        'carrier_sync': pick(res, 'carrier_sync'), 'timing_sync': pick(res, 'timing_sync'),
        'reference_sync': pick(res, 'reference_sync'), 'reason': pick(res, 'reason'),
        'valid_symbols': pick(res, 'valid_symbols'),
        'bit_errors': pick(res, 'bit_errors'), 'compared_bits': pick(res, 'compared_bits'),
        'ber': pick(res, 'ber'),
        'processing_seconds': pick(res, 'processing_seconds', 'demod_seconds',
                                   'worker_seconds', 'elapsed_seconds') or
                              pick(meta, 'processing_seconds', 'demod_seconds', 'worker_seconds'),
        'capture_command_seconds': pick(meta, 'capture_command_seconds')
                                   or pick(rec, 'capture_command_seconds'),
        'tx_seed': pick(meta, 'tx_seed', 'seed'),
        'rx_reference_seed': pick(res, 'reference_seed', 'rx_reference_seed',
                                  'reference_prbs7_seed', 'rx_seed')
                             or pick(meta, 'rx_reference_seed', 'reference_seed', 'rx_seed'),
        'iq_order': pick(res, 'iq_order') or pick(meta, 'iq_order'),
        'capture_id': pick(fid, 'capture_id') or pick(meta, 'capture_id'),
        'iq_sha256': pick(fid, 'iq_sha256') or pick(meta, 'iq_sha256'),
        'tx_payload_sha256': pick(meta, 'tx_payload_sha256', 'payload_sha256',
                                  'expected_tx_payload_sha256'),
        'classification_label': pick(rec, 'worker_label', 'classification', 'class_label', 'value_label')
                                or pick(res, 'worker_label', 'classification', 'class_label', 'value_label')
                                or pick(meta.get('model_result') if isinstance(meta.get('model_result'), dict) else {},
                                       'label', 'class_label')
                                or pick((meta.get('modrec4_result') or {}).get('modulation', {})
                                       if isinstance(meta.get('modrec4_result'), dict) else {},
                                       'class', 'final_label'),
        'classifier_rejected': pick(meta.get('model_result') if isinstance(meta.get('model_result'), dict) else {},
                                    'rejected'),
        'modrec4_class': pick((meta.get('modrec4_result') or {}).get('modulation', {})
                              if isinstance(meta.get('modrec4_result'), dict) else {},
                              'class', 'final_label'),
        'source': rec.get('_source'),
    }
    return view


def qpsk_checks(real, row):
    fails = []
    identity = {}
    if real['tx_seed'] != row.get('seed'):
        fails.append('tx_seed')
    if real['rx_reference_seed'] != row.get('seed'):
        fails.append('rx_reference_seed')
    if str(real['iq_order']).upper() != 'QI':
        fails.append('iq_order')
    if not real['capture_id']:
        fails.append('capture_id')
    if not isinstance(real['iq_sha256'], str) or len(real['iq_sha256']) != 64:
        fails.append('iq_sha256')
    if real['tx_payload_sha256'] not in (None, row['payload_sha256']):
        fails.append('tx_payload_sha256')
    sync_ok = bool(real['carrier_sync'] and real['timing_sync'] and real['reference_sync'])
    if not sync_ok:
        fails.append('sync')
    if real['reason'] != '':
        fails.append('reason')
    denominator_ok = (real['valid_symbols'] == 127 and real['compared_bits'] == 128)
    if not denominator_ok:
        fails.append('qpsk_denominator_127_128')
    if not isinstance(real['bit_errors'], int) or real['bit_errors'] < 0 or real['bit_errors'] > 128:
        fails.append('bit_errors')
    return {'identity_pass': not any(
                x.startswith(('tx_seed', 'rx_reference', 'iq_order',
                              'capture_id', 'iq_sha')) for x in fails),
            'sync_reason_pass': 'sync' not in fails and 'reason' not in fails,
            'denominator_pass': denominator_ok, 'ber_counts_pass': 'bit_errors' not in fails,
            'pass': not fails, 'failures': fails,
            'unavailable': None if denominator_ok else 'QPSK_DENOMINATOR_NOT_127_128'}


def summarize(runner_dir, truth_path, release, offline=False):
    truth = json.loads(Path(truth_path).read_text(encoding='utf-8'))
    rows = truth['rows']
    records = load_records(runner_dir)
    out = {
        'schema': 's2-qpsk-real-rx-summary-v1',
        'status': 'WAITING_RUNNER_OUTPUT' if not records else 'READY',
        'truth': str(truth_path), 'runner_dir': str(runner_dir),
        'criteria': CRITERIA, 'record_count': len(records),
        'qpsk': [], 'bpsk': [], 'off': [], 'classification_false_accept': [],
        'same_iq': {'status': 'WAITING_REAL_IQ', 'note': 'prepared; offline replay requires real IQ files'},
        'limitations': ['6-frame development smoke; no low-BER/overall-accuracy claim',
                        'BPSK is separate; OFF has no valid QPSK sync/BER'],
    }
    used = set()
    for row in rows:
        rec = None
        if row['mode'] == 'OFF':
            for i, candidate in enumerate(records):
                if i in used:
                    continue
                tag = norm_label(pick(candidate, 'tag', 'label', 'frame_label', 'name') or '')
                if 'off' in tag:
                    rec = candidate
                    used.add(i)
                    break
        else:
            for i, candidate in enumerate(records):
                if i in used:
                    continue
                if find_truth(candidate, [row]) is not None:
                    rec = candidate
                    used.add(i)
                    break
        if rec is None:
            item = {'label': row['label'], 'truth_mode': row['mode'], 'status': 'UNAVAILABLE',
                    'reason': 'NO_REAL_WORKER_RECORD'}
            (out['qpsk'] if row['mode'] == 'QPSK' else out['bpsk'] if row['mode'] == 'BPSK' else out['off']).append(item)
            continue
        real = real_view(rec, row)
        if row['mode'] == 'QPSK':
            checks = qpsk_checks(real, row)
            q = {'label': row['label'], 'truth_seed': row['seed'], 'real': real,
                 'checks': checks, 'status': 'AVAILABLE' if checks['pass'] else 'UNAVAILABLE'}
            iq = find_iq(rec, runner_dir, row)
            if iq is None:
                q['same_iq'] = {'status': 'WAITING_REAL_IQ'}
            elif not offline:
                q['same_iq'] = {'status': 'PREPARED_NOT_RUN', 'iq_file': str(iq)}
            else:
                q['same_iq'] = offline_qpsk(iq, row, release)
                if q['same_iq'].get('status') == 'RESULT':
                    q['same_iq_comparison'] = compare_real_offline(real, q['same_iq'])
            out['qpsk'].append(q)
        elif row['mode'] == 'BPSK':
            out['bpsk'].append({'label': row['label'], 'truth_seed': row['seed'],
                                'real': real, 'status': 'RECORDED_SEPARATE'})
        else:
            accepted = str(real.get('classification_label') or '').upper() in ('QPSK', 'BPSK', 'QAM16', 'QAM64')
            has_sync = bool(real.get('reference_sync') or real.get('carrier_sync'))
            off = {'label': row['label'], 'real': real, 'status': 'OFF_NO_QPSK_BER',
                   'valid_qpsk_sync': has_sync, 'valid_qpsk_ber': real.get('ber'),
                   'classification_false_accept': accepted}
            out['off'].append(off)
            if accepted:
                out['classification_false_accept'].append({'label': row['label'],
                                                           'classification': real.get('classification_label')})
    if out['status'] == 'READY':
        out['qpsk_counts'] = {'available': sum(1 for x in out['qpsk'] if x['status'] == 'AVAILABLE'),
                              'unavailable': sum(1 for x in out['qpsk'] if x['status'] != 'AVAILABLE')}
        same_results = [x.get('same_iq', {}) for x in out['qpsk']]
        if any(x.get('status') == 'RESULT' for x in same_results):
            out['same_iq'] = {'status': 'COMPARED',
                              'items': sum(1 for x in same_results if x.get('status') == 'RESULT'),
                              'matches': sum(1 for x in out['qpsk']
                                             if x.get('same_iq_comparison', {}).get('status') == 'MATCH')}
        elif any(x.get('status') == 'PREPARED_NOT_RUN' for x in same_results):
            out['same_iq'] = {'status': 'PREPARED_NOT_RUN'}
    return out


def self_check():
    """Synthetic parser/validator check; no real data and no product truth."""
    truth = json.loads(json.dumps({
        'rows': [
            {'frame_index': 1, 'label': 'qpsk-39', 'mode': 'QPSK', 'seed': 39,
             'payload_sha256': 'a' * 64},
            {'frame_index': 2, 'label': 'qpsk-40', 'mode': 'QPSK', 'seed': 40,
             'payload_sha256': 'b' * 64},
            {'frame_index': 3, 'label': 'qpsk-41', 'mode': 'QPSK', 'seed': 41,
             'payload_sha256': 'c' * 64},
            {'frame_index': 4, 'label': 'bpsk-6', 'mode': 'BPSK', 'seed': 6,
             'payload_sha256': 'd' * 64},
            {'frame_index': 5, 'label': 'off-1', 'mode': 'OFF', 'seed': None},
            {'frame_index': 6, 'label': 'off-2', 'mode': 'OFF', 'seed': None},
        ]}))
    with tempfile.TemporaryDirectory(prefix='qpsk-summary-') as td:
        td = Path(td)
        (td / 'truth.json').write_text(json.dumps(truth), encoding='utf-8')
        good = {'label': 'qpsk-39', 'tx_seed': 39, 'tx_mode': 'QPSK',
                'tx_payload_sha256': 'a' * 64, 'iq_sha256': 'e' * 64,
                'capture_id': 'rx-x', 'iq_order': 'QI',
                'worker': {'carrier_sync': True, 'timing_sync': True,
                           'reference_sync': True, 'reason': '', 'valid_symbols': 127,
                           'bit_errors': 0, 'compared_bits': 128, 'ber': 0.0,
                           'processing_seconds': 0.01, 'reference_seed': 39}}
        short = json.loads(json.dumps(good))
        short['label'] = 'qpsk-40'; short['tx_seed'] = 40; short['tx_payload_sha256'] = 'b' * 64
        short['worker']['reference_seed'] = 40; short['worker']['valid_symbols'] = 126
        off = {'label': 'off-1', 'tx_mode': 'OFF', 'worker': {'classification': 'QPSK'}}
        (td / 'runner').mkdir()
        (td / 'runner' / 'frames.json').write_text(json.dumps([good, short, off]), encoding='utf-8')
        out = summarize(td / 'runner', td / 'truth.json', DEFAULT_RELEASE, offline=False)
        assert out['status'] == 'READY'
        assert out['qpsk'][0]['status'] == 'AVAILABLE'
        assert out['qpsk'][1]['status'] == 'UNAVAILABLE'
        assert out['off'][0]['classification_false_accept'] is True
        assert out['classification_false_accept']
        assert out['qpsk'][0]['same_iq']['status'] == 'WAITING_REAL_IQ'
        return {'status': 'SELF_CHECK_OK', 'qpsk_available': out['qpsk_counts'],
                'unavailable_denominator': out['qpsk'][1]['checks']['unavailable']}
    raise AssertionError('unreachable')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runner-dir', type=Path, default=DEFAULT_RUNNER)
    ap.add_argument('--truth', type=Path, default=DEFAULT_TRUTH)
    ap.add_argument('--release', type=Path, default=DEFAULT_RELEASE)
    ap.add_argument('--out', type=Path, default=EV / 'summary.json')
    ap.add_argument('--offline', action='store_true',
                    help='run same-IQ frozen-product sidecar when IQ file exists')
    ap.add_argument('--self-check', action='store_true')
    a = ap.parse_args()
    if a.self_check:
        print(json.dumps(self_check(), indent=2))
        return
    out = summarize(a.runner_dir, a.truth, a.release, offline=a.offline)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': out['status'], 'record_count': out['record_count'],
                      'qpsk': [{x['label']: x['status']} for x in out['qpsk']],
                      'bpsk': len(out['bpsk']), 'off': len(out['off']),
                      'false_accept': out['classification_false_accept']}, indent=2))


if __name__ == '__main__':
    main()
