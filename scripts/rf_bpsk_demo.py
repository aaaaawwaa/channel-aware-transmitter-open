"""Fixed-condition BPSK receive demo, driven only by already-saved IQ (zero device).

Condition (frozen, from the S1 teaching pilot): BPSK 3 MSym/s, RECT shaping,
PRBS7 reference (non-differential), 60 MSPS, ratio 0, 4096 samples per frame.
Nothing here talks to the board, loads an executable or writes a frozen module.

What it does, per saved capture ``<tag>.iq`` + ``<tag>.json``:

1. verifies the IQ bytes against the sha256 recorded in the capture metadata;
2. checks the capture is inside the frozen fixed condition above;
3. rebuilds the frozen ``RxIqFrame`` and runs the FROZEN ``receive_bpsk`` engine
   with the RX settings frozen in the capture (mode/rate/shaping/reference seed);
4. compares the fresh result with the result recorded at capture time
   (tolerance 1e-6, the same tolerance used by the reviewed host/ARM replay);
5. reports reference_sync / reason / CFO / coherence / alignment / BER and a
   per-frame PASS/FAIL plus a named reason for anything that is not a pass.

The report is a re-derivation of *existing* evidence.  It is explicitly NOT a new
board measurement, NOT a formal acceptance and NOT a claim about physical RF.

    python scripts/rf_bpsk_demo.py <capture-dir> [--tag TAG ...] [--output out.json]

Exit 0 only when every selected frame reproduces its recorded result and is
inside the fixed condition.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pynq_overlay/sw'))

from rf_bpsk_receiver import BpskConfig, VERSION, receive_bpsk  # noqa: E402
from rf_rx_types import RxIqFrame, RxReadback  # noqa: E402

SCHEMA = 'rf-bpsk-offline-demo-v1'
TOLERANCE = 1e-6
SCOPE = ('offline replay of saved IQ and its recorded result; not a new board '
         'measurement, not a formal acceptance, no RF')

#: The frozen fixed condition this demo is allowed to speak about.
DEMO_CONDITION = {
    'mode': 'BPSK',
    'shaping': 'RECT',
    'symbol_rate_hz': 3000000,
    'sample_rate_hz': 60000000,
    'ratio': 0,
    'samples': 4096,
    'reference_prbs7_seed': 'recorded int or None',
}

FRAME_FIELDS = ('capture_id', 'samples', 'sample_rate_hz', 'ratio',
                'daemon_timestamp_ns', 'connection_generation', 'config_revision',
                'request_id', 'release_id', 'op', 'physical', 'pre_io_ok',
                'post_io_ok', 'post_rx_armed', 'post_tx_source_selected',
                'completed_at_monotonic')

OBSERVABLES = ('reference_sync', 'reason', 'carrier_sync', 'timing_sync', 'cfo_hz',
               'carrier_coherence', 'timing_quality', 'timing_offset_samples',
               'phase_mod_pi', 'total_symbols', 'alignment_bits', 'compared_bits',
               'bit_errors', 'ber', 'reference_offset', 'polarity',
               'reference_correlation', 'algorithm_version')


class DemoError(Exception):
    """A named, reportable reason a frame cannot be demoed honestly."""

    def __init__(self, code, detail=''):
        super().__init__('%s: %s' % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def load_capture(directory, tag):
    """Read one saved capture; every missing piece is a named failure."""
    directory = Path(directory)
    iq_path = directory / (tag + '.iq')
    meta_path = directory / (tag + '.json')
    if not iq_path.is_file():
        raise DemoError('MISSING_IQ', str(iq_path))
    if not meta_path.is_file():
        raise DemoError('MISSING_METADATA', str(meta_path))
    raw = iq_path.read_bytes()
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise DemoError('UNREADABLE_METADATA', '%s: %s' % (meta_path, exc))
    if not isinstance(meta, dict):
        raise DemoError('UNREADABLE_METADATA', str(meta_path))
    recorded = meta.get('iq_sha256')
    actual = sha256_bytes(raw)
    if recorded != actual:
        raise DemoError('IQ_HASH_MISMATCH', 'recorded=%r actual=%r' % (recorded, actual))
    missing = [name for name in FRAME_FIELDS if name not in meta]
    if missing:
        raise DemoError('METADATA_INCOMPLETE', ','.join(missing))
    if 'demod_result' not in meta:
        raise DemoError('MISSING_RECORDED_RESULT', tag)
    return raw, meta, recorded


def frozen_config(meta):
    """The RX settings frozen into the capture; refuse settings we cannot honour."""
    submitted = (meta.get('demod_result') or {}).get('submitted') or {}
    config = submitted.get('config') or {}
    if not isinstance(config, dict) or 'symbol_rate_hz' not in config:
        raise DemoError('MISSING_FROZEN_CONFIG', str(sorted(config)))
    for name, value in DEMO_CONDITION.items():
        if name in ('reference_prbs7_seed',):
            continue
        actual = config.get(name) if name in config else meta.get(name)
        if actual != value:
            raise DemoError('OUT_OF_DEMO_CONDITION',
                            '%s=%r expected %r' % (name, actual, value))
    return BpskConfig(mode=config.get('mode', 'BPSK'),
                      symbol_rate_hz=config['symbol_rate_hz'],
                      shaping=config.get('shaping', 'RECT'),
                      reference_prbs7_seed=config.get('reference_prbs7_seed'))


def frame_from_capture(raw, meta):
    if meta.get('format', 's16_iq_le') != 's16_iq_le':
        raise DemoError('UNSUPPORTED_IQ_FORMAT', str(meta.get('format')))
    try:
        return RxIqFrame(meta['capture_id'], raw, meta['samples'],
                         meta['sample_rate_hz'], meta['ratio'],
                         meta['daemon_timestamp_ns'], meta['connection_generation'],
                         meta['config_revision'], meta['request_id'],
                         meta['release_id'], meta['op'], meta['physical'],
                         RxReadback(meta['pre_io_ok']),
                         RxReadback(meta['post_io_ok'], rx_armed=meta['post_rx_armed'],
                                    tx_source_selected=meta['post_tx_source_selected']),
                         meta['completed_at_monotonic'])
    except (KeyError, TypeError) as exc:
        raise DemoError('BAD_FRAME_METADATA', '%s: %s' % (type(exc).__name__, exc))


def compare_results(replayed, recorded, tolerance=TOLERANCE):
    """Field-by-field comparison; floats use the reviewed replay tolerance."""
    mismatches = []
    max_float_error = 0.0
    for name in sorted(set(recorded) | set(replayed)):
        expected = recorded.get(name)
        actual = replayed.get(name)
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) \
                and not isinstance(expected, bool) and not isinstance(actual, bool):
            delta = abs(float(actual) - float(expected))
            max_float_error = max(max_float_error, delta)
            if delta > tolerance:
                mismatches.append(name)
        elif actual != expected:
            mismatches.append(name)
    return mismatches, max_float_error


def demo_frame(directory, tag, tolerance=TOLERANCE):
    """One frame: hash, condition, frozen replay, recorded-result comparison."""
    try:
        raw, meta, digest = load_capture(directory, tag)
        config = frozen_config(meta)
        frame = frame_from_capture(raw, meta)
        replayed = json.loads(json.dumps(receive_bpsk(frame, config).to_dict()))
        recorded = json.loads(json.dumps(
            (meta.get('demod_result') or {}).get('result') or {}))
        if not recorded:
            raise DemoError('MISSING_RECORDED_RESULT', 'demod_result.result')
        mismatches, max_float_error = compare_results(replayed, recorded, tolerance)
        ok = not mismatches
        return {
            'tag': tag,
            'ok': ok,
            'capture_id': meta.get('capture_id'),
            'release_id': meta.get('release_id'),
            'iq_sha256': digest,
            'iq_bytes': len(raw),
            'recorded_algorithm_version': meta['demod_result'].get('algorithm_version'),
            'engine_version': VERSION,
            'observables': {name: replayed.get(name) for name in OBSERVABLES},
            'recorded_observables': {name: recorded.get(name) for name in OBSERVABLES},
            'mismatches': mismatches,
            'max_float_error': max_float_error,
            'error': None if ok else 'RECORDED_RESULT_MISMATCH',
        }
    except DemoError as exc:
        return {'tag': tag, 'ok': False, 'error': exc.code, 'detail': exc.detail}


def capture_tags(directory):
    directory = Path(directory)
    if not directory.is_dir():
        raise DemoError('CAPTURE_DIR_MISSING', str(directory))
    return sorted(path.stem for path in directory.glob('*.iq')
                  if (directory / (path.stem + '.json')).is_file()
                  or not path.with_suffix('.json').exists())


def run_demo(directory, tags=None, tolerance=TOLERANCE):
    directory = Path(directory)
    if tags:
        chosen = list(tags)
    else:
        chosen = capture_tags(directory)
        if not chosen:
            raise DemoError('NO_CAPTURES', str(directory))
    frames = [demo_frame(directory, tag, tolerance) for tag in chosen]
    failures = [frame['tag'] for frame in frames if not frame['ok']]
    return {
        'schema': SCHEMA,
        'scope': SCOPE,
        'condition': DEMO_CONDITION,
        'engine_version': VERSION,
        'tolerance': tolerance,
        'directory': str(directory),
        'frames': frames,
        'frame_count': len(frames),
        'failed': failures,
        'passed': not failures,
        'limitations': [
            're-derivation of saved evidence, not a new measurement',
            'no RF path, no PL, no board, no model used',
            'not a formal acceptance; formal acceptance remains NOT_RUN',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path,
                        help='directory with <tag>.iq + <tag>.json saved captures')
    parser.add_argument('--tag', action='append', default=None,
                        help='capture tag to demo (repeatable; default: every .iq)')
    parser.add_argument('--output', type=Path, default=None,
                        help='write the JSON report here (default: print it)')
    parser.add_argument('--tolerance', type=float, default=TOLERANCE)
    args = parser.parse_args(argv)
    try:
        report = run_demo(args.directory, args.tag, args.tolerance)
    except DemoError as exc:
        print(json.dumps({'error': str(exc), 'code': exc.code}, indent=2))
        return 2
    if args.output is not None:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for frame in report['frames']:
        if frame['ok']:
            obs = frame['observables']
            print('%-18s PASS  ref_sync=%-5s ber=%s/%s  cfo=%8.3f  coh=%.3f  align=%s'
                  % (frame['tag'], obs['reference_sync'],
                     obs['bit_errors'], obs['compared_bits'], obs['cfo_hz'] or 0.0,
                     obs['carrier_coherence'] or 0.0, obs['alignment_bits']))
        else:
            print('%-18s FAIL  %s %s'
                  % (frame['tag'], frame['error'], frame.get('detail', '')))
    print(json.dumps({'schema': report['schema'], 'passed': report['passed'],
                      'frame_count': report['frame_count'],
                      'failed': report['failed']}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
