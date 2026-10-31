"""Unified capture record + byte-checkable offline replay (zero device).

Why: the saved captures under ``evidence/rf-teaching/`` are the only physical
evidence for the fixed-condition demo, but their layout (``<tag>.iq`` +
``<tag>.json``) is an ad-hoc pilot format.  This module writes ONE record per
capture in a self-describing bundle that a later reader can verify and replay
without trusting any index:

    <out>/<tag>/record.json     the unified record (schema rf-capture-record-v1)
    <out>/<tag>/metadata.json   the original capture metadata, verbatim bytes
    <out>/<tag>/frame.iq        the original IQ bytes, verbatim

The original capture directory is only ever READ; this module never rewrites an
existing receipt or record (``write`` refuses an existing bundle).

Replay is performed with the FROZEN engine (``rf_bpsk_receiver.receive_bpsk``)
and the RX settings frozen in the capture, and the result is compared with the
recorded one at the reviewed 1e-6 tolerance.  Failed demods are recorded too:
their ``status`` is FAILED and their replay must reproduce the failure, not turn
it into a measurement.

    python scripts/rf_capture_record.py write   --directory DIR --tag TAG --output ROOT
    python scripts/rf_capture_record.py verify  ROOT [--tag TAG]
    python scripts/rf_capture_record.py replay  ROOT [--tag TAG]
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.rf_bpsk_demo import (DemoError, compare_results, frame_from_capture,
                                  frozen_config, load_capture)  # noqa: E402
from scripts.rf_bpsk_demo import TOLERANCE, sha256_bytes  # noqa: E402

SCHEMA = 'rf-capture-record-v1'
REPLAY_SCHEMA = 'rf-capture-replay-v1'
SCOPE = ('saved-evidence record and offline replay; not a new measurement, '
         'not a formal acceptance, no RF, no board')
NOT_RUN = ['new board capture', 'trial deployment / install', 'PL reload',
           'any RF transmission or capture', '80 MHz experiment',
           'formal S2 acceptance', 'human acceptance']


def _result_of(meta):
    result = (meta.get('demod_result') or {}).get('result')
    return json.loads(json.dumps(result)) if isinstance(result, dict) else None


def capture_status(meta):
    """Normalise the pilot's capture-status document into (state, reason)."""
    raw = meta.get('capture_status')
    if isinstance(raw, dict):
        return str(raw.get('state') or ''), str(raw.get('reason') or '')
    if isinstance(raw, str):
        return raw, ''
    return '', ''


def record_status(meta, result):
    """PASS only when the capture completed AND the demod did not fail."""
    state, capture_reason = capture_status(meta)
    demod_reason = str((result or {}).get('reason') or '')
    if state and state != 'done':
        return 'FAILED', 'capture_state=%s' % state
    if capture_reason:
        return 'FAILED', 'capture_reason=%s' % capture_reason
    if demod_reason:
        return 'FAILED', 'demod_reason=%s' % demod_reason
    return 'PASS', ''


def model_identity(meta):
    raw = meta.get('model_result')
    if not isinstance(raw, dict):
        return None
    keys = ('model_version', 'model_sha256', 'label', 'quality')
    return {key: raw.get(key) for key in keys}


def build_record(directory, tag):
    raw, meta, digest = load_capture(directory, tag)
    result = _result_of(meta)
    if result is None:
        raise DemoError('MISSING_RECORDED_RESULT', 'demod_result.result')
    status, status_reason = record_status(meta, result)
    return {
        'schema': SCHEMA,
        'record_id': tag,
        'capture_id': meta.get('capture_id'),
        'release_id': meta.get('release_id'),
        'op': meta.get('op'),
        'physical': meta.get('physical'),
        'iq': {
            'sha256': digest,
            'bytes': len(raw),
            'format': meta.get('format', 's16_iq_le'),
            'samples': meta.get('samples'),
            'sample_rate_hz': meta.get('sample_rate_hz'),
            'ratio': meta.get('ratio'),
        },
        'metadata_sha256': hashlib.sha256(
            (Path(directory) / (tag + '.json')).read_bytes()).hexdigest(),
        'result': result,
        'model_identity': model_identity(meta),
        'status': status,
        'status_reason': status_reason,
        'scope': SCOPE,
    }


def write_bundle(directory, tag, out_root):
    """Write one bundle; never overwrite an existing record."""
    directory = Path(directory)
    out_root = Path(out_root)
    source_meta = directory / (tag + '.json')
    source_iq = directory / (tag + '.iq')
    if not source_iq.is_file() or not source_meta.is_file():
        raise DemoError('MISSING_CAPTURE', '%s(.iq|.json)' % (directory / tag))
    record = build_record(directory, tag)
    bundle = out_root / tag
    if bundle.exists():
        raise DemoError('RECORD_EXISTS', str(bundle))
    bundle.mkdir(parents=True)
    frame_bytes = source_iq.read_bytes()
    meta_bytes = source_meta.read_bytes()
    (bundle / 'frame.iq').write_bytes(frame_bytes)
    (bundle / 'metadata.json').write_bytes(meta_bytes)
    record['written_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record['source'] = {'directory': str(directory), 'tag': tag}
    (bundle / 'record.json').write_text(json.dumps(record, indent=2) + '\n',
                                        encoding='utf-8')
    return record


def _load_bundle(bundle):
    bundle = Path(bundle)
    record_path = bundle / 'record.json'
    if not record_path.is_file():
        raise DemoError('RECORD_MISSING', str(record_path))
    try:
        record = json.loads(record_path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise DemoError('RECORD_UNREADABLE', '%s: %s' % (record_path, exc))
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise DemoError('RECORD_SCHEMA_MISMATCH', str(record.get('schema')))
    return record


def verify_bundle(bundle):
    """Byte-check a bundle against its own record: PASS/FAIL with named reasons."""
    try:
        record = _load_bundle(bundle)
        bundle = Path(bundle)
        frame = bundle / 'frame.iq'
        meta_path = bundle / 'metadata.json'
        failures = []
        if not frame.is_file():
            failures.append('FRAME_IQ_MISSING')
        elif sha256_bytes(frame.read_bytes()) != record['iq']['sha256']:
            failures.append('FRAME_IQ_HASH_MISMATCH')
        if not meta_path.is_file():
            failures.append('METADATA_MISSING')
        elif sha256_bytes(meta_path.read_bytes()) != record['metadata_sha256']:
            failures.append('METADATA_HASH_MISMATCH')
        if not failures:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            rebuilt = build_record_from_metadata(meta, frame.read_bytes())
            for key in ('capture_id', 'release_id', 'result', 'model_identity',
                        'status', 'status_reason'):
                if rebuilt.get(key) != record.get(key):
                    failures.append('RECORD_FIELD_%s_DIFFERS' % key.upper())
        return {
            'schema': 'rf-capture-verify-v1',
            'bundle': str(bundle),
            'record_id': record.get('record_id'),
            'status': record.get('status'),
            'passed': not failures,
            'failures': failures,
        }
    except DemoError as exc:
        return {'schema': 'rf-capture-verify-v1', 'bundle': str(bundle),
                'passed': False, 'failures': [exc.code], 'detail': exc.detail}


def build_record_from_metadata(meta, raw):
    """The same record fields, computed from bundle bytes only (no source dir)."""
    result = _result_of(meta)
    if result is None:
        raise DemoError('MISSING_RECORDED_RESULT', 'demod_result.result')
    status, status_reason = record_status(meta, result)
    return {
        'capture_id': meta.get('capture_id'),
        'release_id': meta.get('release_id'),
        'result': result,
        'model_identity': model_identity(meta),
        'status': status,
        'status_reason': status_reason,
    }


def replay_bundle(bundle):
    """Replay one bundle with the frozen engine; compare to the recorded result."""
    try:
        record = _load_bundle(bundle)
        bundle = Path(bundle)
        frame_bytes = (bundle / 'frame.iq').read_bytes()
        meta = json.loads((bundle / 'metadata.json').read_text(encoding='utf-8'))
        if sha256_bytes(frame_bytes) != record['iq']['sha256']:
            raise DemoError('FRAME_IQ_HASH_MISMATCH', str(bundle))
        from rf_bpsk_receiver import receive_bpsk
        config = frozen_config(meta)
        replayed = json.loads(json.dumps(
            receive_bpsk(frame_from_capture(frame_bytes, meta), config).to_dict()))
        mismatches, max_float_error = compare_results(replayed, record['result'],
                                                      TOLERANCE)
        return {
            'record_id': record.get('record_id'),
            'status': record.get('status'),
            'passed': not mismatches,
            'mismatches': mismatches,
            'max_float_error': max_float_error,
            'replayed_reason': replayed.get('reason'),
            'replayed_reference_sync': replayed.get('reference_sync'),
        }
    except DemoError as exc:
        return {'record_id': Path(bundle).name, 'passed': False,
                'failures': [exc.code], 'detail': exc.detail}


def replay_all(root, tags=None):
    root = Path(root)
    if tags:
        chosen = list(tags)
    else:
        chosen = sorted(path.name for path in root.iterdir() if path.is_dir()
                        and (path / 'record.json').is_file())
        if not chosen:
            raise DemoError('NO_RECORDS', str(root))
    rows = [replay_bundle(root / tag) for tag in chosen]
    failures = [row['record_id'] for row in rows if not row.get('passed')]
    return {
        'schema': REPLAY_SCHEMA,
        'root': str(root),
        'scope': SCOPE,
        'records': rows,
        'record_count': len(rows),
        'failed': failures,
        'passed': not failures,
        'failure_records_replayed': sum(1 for row in rows if row.get('status') == 'FAILED'),
    }


def _write_text(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    write = sub.add_parser('write')
    write.add_argument('--directory', type=Path, required=True)
    write.add_argument('--tag', required=True)
    write.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('root', type=Path)
    verify.add_argument('--tag', action='append')
    verify.add_argument('--output', type=Path)
    replay = sub.add_parser('replay')
    replay.add_argument('root', type=Path)
    replay.add_argument('--tag', action='append')
    replay.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'write':
            record = write_bundle(args.directory, args.tag, args.output)
            print(json.dumps({k: record[k] for k in
                              ('record_id', 'capture_id', 'status', 'iq')}, indent=2))
            return 0
        if args.command == 'verify':
            tags = args.tag or sorted(path.name for path in args.root.iterdir()
                                      if path.is_dir())
            results = [verify_bundle(args.root / tag) for tag in tags]
            payload = {'schema': 'rf-capture-verify-v1',
                       'bundles': results,
                       'passed': all(row['passed'] for row in results),
                       'fresh_record_dir': str(args.root)}
            if args.output:
                _write_text(args.output, payload)
            print(json.dumps({'passed': payload['passed'],
                              'failed': [row['record_id'] for row in results
                                         if not row['passed']]}, indent=2))
            return 0 if payload['passed'] else 1
        payload = replay_all(args.root, args.tag)
        if args.output:
            _write_text(args.output, payload)
        print(json.dumps({k: payload[k] for k in
                          ('schema', 'passed', 'record_count', 'failed',
                           'failure_records_replayed')}, indent=2))
        return 0 if payload['passed'] else 1
    except DemoError as exc:
        print(json.dumps({'error': str(exc), 'code': exc.code}, indent=2))
        return 2


if __name__ == '__main__':
    sys.exit(main())
