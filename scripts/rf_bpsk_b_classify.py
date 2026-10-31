"""Offline exploratory 433.9 MHz direction-B classification (task-17; 0 RF).

This is NOT an acceptance run:
- it never touches the board or the Pluto (reads only saved IQ files);
- it reports every result with ``exploratory=true`` and ``domain_declared=false``
  because the current production model was declared for the 80 MHz board
  TX->RX loopback domain, not for the 433.9 MHz Pluto-TX direction-B domain;
- it never treats ``reference_sync``/BER as a classification label and never
  quotes 80 MHz TEST metrics as direction-B metrics.
Model path and expected SHA-256 are mandatory; a missing/mismatched identity is
a named failure (exit 2), never a silent pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SW_DIR = ROOT / 'pynq_overlay' / 'sw'
INPUT_FS_HZ = 60000000
DECIM = 1


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def load_iq(path):
    """Load 4096 signed-12-bit I/Q pairs (the frozen classifier input contract)."""
    raw = Path(path).read_bytes()
    if len(raw) == 0 or len(raw) % 4:
        raise ValueError('IQ payload length %d is not 2x i16' % len(raw))
    codes = np.frombuffer(raw, dtype='<i2').reshape(-1, 2)
    if codes.shape != (4096, 2):
        raise ValueError('IQ must be 4096 I/Q pairs, got %r' % (codes.shape,))
    if np.any((codes <= -2048) | (codes >= 2047)):
        # Let the frozen preprocess report `clipping` as UNKNOWN, not a label.
        return codes.astype(np.int64)
    return codes.astype(np.int64)


def _fail(message, **fields):
    payload = {'exploratory': True, 'domain_declared': False,
               'acceptance_claimed': False, 'passed': False,
               'error': message}
    payload.update(fields)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True,
                        help='directory containing board-*.iq (and optional .json)')
    parser.add_argument('--model', type=Path,
                        help='rf_model.json path (mandatory)')
    parser.add_argument('--model-sha256',
                        help='expected model sha256 (mandatory)')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)

    if args.model is None or not args.model_sha256:
        return _fail('MODEL_IDENTITY_REQUIRED',
                     detail='--model and --model-sha256 are both mandatory')
    if not args.model.is_file():
        return _fail('MODEL_FILE_MISSING', model=str(args.model))
    actual_sha = sha256_file(args.model)
    if actual_sha != args.model_sha256:
        return _fail('MODEL_HASH_MISMATCH', model=str(args.model),
                     expected_sha256=args.model_sha256, actual_sha256=actual_sha)
    if not args.input.is_dir():
        return _fail('INPUT_MISSING', input=str(args.input))
    iq_paths = sorted(args.input.glob('board-*.iq'))
    if not iq_paths:
        return _fail('INPUT_EMPTY', input=str(args.input))

    sys.path.insert(0, str(SW_DIR))
    try:
        import rf_cnn_runtime as runtime
    except Exception as exc:  # noqa: BLE001 - named failure, not a pass
        return _fail('RUNTIME_IMPORT_FAILED', error=repr(exc))
    try:
        model = runtime.load_model(args.model, args.model_sha256)
    except Exception as exc:  # noqa: BLE001
        return _fail('MODEL_INVALID', error=repr(exc))

    on_records, off_records = [], []
    for iq_path in iq_paths:
        tag = iq_path.name
        kind = 'on' if '-on-' in tag else ('off' if '-off' in tag else 'unknown')
        try:
            iq = load_iq(iq_path)
            prediction = model.predict(iq, INPUT_FS_HZ, DECIM)
        except Exception as exc:  # noqa: BLE001
            return _fail('CLASSIFICATION_FAILED', tag=tag, error=repr(exc))
        label = prediction.get('label')
        class_index = (runtime.CLASSES.index(label)
                       if label in runtime.CLASSES else None)
        record = {
            'tag': tag,
            'kind': kind,
            'label': label,
            'class_index': class_index,
            'scores': prediction.get('scores'),
            'score': prediction.get('score'),
            'margin': prediction.get('margin'),
            'reason': prediction.get('reason'),
            'model_sha256': args.model_sha256,
            'preprocessing_version': runtime.PREPROCESSING,
            'model_status': prediction.get('status'),
            'domain_declared': False,
            'exploratory': True,
        }
        (on_records if kind == 'on' else off_records if kind == 'off'
         else on_records).append(record)

    output = {
        'exploratory': True,
        'domain_declared': False,
        'acceptance_claimed': False,
        'model_sha256': args.model_sha256,
        'preprocessing_version': runtime.PREPROCESSING,
        'model_status': model.status,
        'declared_domain': dict(model.domain),
        'on': on_records,
        'off': off_records,
        'summary': {
            'on_count': len(on_records),
            'off_count': len(off_records),
            'on_labels': sorted({r['label'] for r in on_records}),
            'off_labels': sorted({r['label'] for r in off_records}),
        },
        'notes': [
            'exploratory post-processing only; NOT a classification acceptance',
            'the current production model is declared for the 80 MHz board '
            'TX->RX loopback domain, not 433.9 MHz direction-B',
            'reference_sync/BER are NOT used as classification labels',
            '80 MHz TEST metrics must not be quoted as direction-B metrics',
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'classification.json').write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8')
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
