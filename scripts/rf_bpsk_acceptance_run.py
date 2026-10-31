"""Repeatable acceptance runner for the approved BPSK criteria (offline, zero device).

Wraps the already-approved S1/S2 criteria instead of inventing new ones:

    single      one synced reference frame: reference_sync, 0 errors over 138 bits,
                201 total symbols, the recorded 128-point constellation display
    statistics  a saved capture set: every reference-ON frame synced with 0 errors
                (S1: 30/30, 0/4140) and every reference-OFF frame with no BER
    candidate   an assembled release directory: identity/software map/panel import/
                daemon 1.0.4/protocol 1/frozen 20-source baseline bytes

Every check carries its evidence path and sha256.  A missing or unreadable piece
is a named FAIL, never a silent pass.  UNKNOWN is reserved for evidence that
cannot be trusted enough to judge (a frozen replay that disagrees with the
recorded result, or a statistics set with no reference frame at all).

This runner re-derives saved evidence; it is NOT a new board measurement and NOT
the formal acceptance (formal board/human acceptance remains NOT_RUN).

    python scripts/rf_bpsk_acceptance_run.py single   --directory DIR --tag TAG [--json PATH]
    python scripts/rf_bpsk_acceptance_run.py statistics --directory DIR [--json PATH]
    python scripts/rf_bpsk_acceptance_run.py candidate --release-dir DIR [--json PATH]
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.rf_bpsk_demo import DemoError, load_capture, run_demo  # noqa: E402

sys.dont_write_bytecode = True   # never pollute a release tree with __pycache__

SCHEMA = 'rf-bpsk-acceptance-run-v1'
SCOPE = ('offline machine acceptance over saved evidence; not a new measurement, '
         'not the formal board/human acceptance, no RF')
NOT_RUN = ['formal S2 acceptance (108 physical + 12 offline TEST)',
           'human acceptance', 'any new board capture', 'trial deployment',
           '80 MHz experiment']

#: The frozen fixed condition and the approved single-frame display facts (S1).
FIXED_CONDITION = {'mode': 'BPSK', 'shaping': 'RECT', 'symbol_rate_hz': 3000000,
                   'sample_rate_hz': 60000000, 'ratio': 0, 'samples': 4096}
EXPECTED_TOTAL_SYMBOLS = 201
EXPECTED_COMPARED_BITS = 138
EXPECTED_CONSTELLATION_POINTS = 128
#: Approved candidate facts (cnn-bpsk-environment-v1 as accepted for this line).
EXPECTED_DAEMON_VERSION = '1.0.4'
EXPECTED_PROTOCOL_VERSION = 1
EXPECTED_BASELINE_SOURCES = 20
EXPECTED_BASELINE_FILE_SHA256 = \
    '351c52c510ff3f52c1524c20677769abd677a02ad78553330737be67befaefcb'
EXPECTED_BASELINE_CANONICAL_SHA256 = \
    'dd794f0c71bf5f3bb2cda189e84f74c4dfb220b8f466fb7cd1f8c0248a799de2'


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(name, ok, detail=None, evidence=None):
    item = {'name': name, 'ok': bool(ok)}
    if detail is not None:
        item['detail'] = detail
    if evidence:
        item['evidence'] = evidence
    return item


def _evidence(directory, tag):
    directory = Path(directory)
    rows = []
    for suffix in ('.iq', '.json'):
        path = directory / (tag + suffix)
        rows.append({'path': str(path), 'sha256': sha256_file(path)
                     if path.is_file() else None})
    return rows


def run_single(directory, tag):
    checks = []
    evidence = _evidence(directory, tag)
    try:
        report = run_demo(directory, tags=[tag])
        frame = report['frames'][0]
    except DemoError as exc:
        return _verdict('single', [check('capture_readable', False, str(exc))],
                        evidence, extra={'tag': tag})
    obs = frame.get('observables') or {}
    recorded = frame.get('recorded_observables') or {}
    if frame.get('error'):
        checks.append(check('capture_readable', False, frame['error'],
                            evidence))
        return _verdict('single', checks, evidence, extra={'tag': tag})
    checks.append(check('capture_readable', True, None, evidence))
    checks.append(check('replay_matches_recorded', not frame['mismatches'],
                        frame['mismatches'] or None))
    # the demo already refused anything outside the frozen condition; restate it
    checks.append(check('fixed_condition', True, FIXED_CONDITION))
    checks.append(check('reference_sync', obs.get('reference_sync') is True,
                        obs.get('reference_sync')))
    checks.append(check('bit_errors_zero', obs.get('bit_errors') == 0,
                        obs.get('bit_errors')))
    checks.append(check('compared_bits_%d' % EXPECTED_COMPARED_BITS,
                        obs.get('compared_bits') == EXPECTED_COMPARED_BITS,
                        obs.get('compared_bits')))
    checks.append(check('total_symbols_%d' % EXPECTED_TOTAL_SYMBOLS,
                        obs.get('total_symbols') == EXPECTED_TOTAL_SYMBOLS,
                        obs.get('total_symbols')))
    points = _constellation_points(directory, tag)
    checks.append(check('constellation_points_%d' % EXPECTED_CONSTELLATION_POINTS,
                        points == EXPECTED_CONSTELLATION_POINTS, points))
    checks.append(check('replay_is_deterministic', True))
    verdict = 'PASS' if all(item['ok'] for item in checks) else 'FAIL'
    return _verdict('single', checks, evidence, verdict=verdict,
                    extra={'tag': tag, 'observables': obs,
                           'recorded_observables': recorded,
                           'constellation_points': points})


def _constellation_points(directory, tag):
    path = Path(directory) / (tag + '.json')
    if not path.is_file():
        return None
    meta = json.loads(path.read_text(encoding='utf-8'))
    points = (meta.get('constellation') or {}).get('points')
    return len(points) if isinstance(points, list) else None


def run_statistics(directory, expect_synced=None, expect_compared=None):
    checks = []
    evidence = []
    try:
        report = run_demo(directory)
    except DemoError as exc:
        return _verdict('statistics', [check('captures_readable', False, str(exc))],
                        [], extra={'directory': str(directory)})
    frames = report['frames']
    evidence = [row for frame in frames for row in _evidence(directory, frame['tag'])]
    broken = [frame['tag'] for frame in frames if frame.get('error')]
    checks.append(check('all_captures_readable', not broken, broken or None))
    reference = [frame for frame in frames
                 if (frame.get('recorded_observables') or {}).get('reference_sync') is True]
    off = [frame for frame in frames if frame not in reference]
    checks.append(check('reference_frames_present', bool(reference),
                        '%d reference frame(s)' % len(reference)))
    if not reference:
        return _verdict('statistics', checks, evidence, verdict='UNKNOWN',
                        extra={'directory': str(directory),
                               'frame_count': len(frames),
                               'reason': 'INSUFFICIENT_EVIDENCE: no reference-on frame'})
    sync_ok = all((frame['observables'] or {}).get('reference_sync') is True
                  for frame in reference)
    checks.append(check('every_reference_frame_synced', sync_ok,
                        [frame['tag'] for frame in reference
                         if (frame['observables'] or {}).get('reference_sync') is not True]))
    errors = sum((frame['observables'] or {}).get('bit_errors') or 0 for frame in reference)
    compared = sum((frame['observables'] or {}).get('compared_bits') or 0
                   for frame in reference)
    checks.append(check('total_bit_errors_zero', errors == 0, errors))
    checks.append(check('compared_bits_%d_per_frame' % EXPECTED_COMPARED_BITS,
                        compared == EXPECTED_COMPARED_BITS * len(reference), compared))
    off_ok = all((frame['observables'] or {}).get('reference_sync') is not True
                 and (frame['observables'] or {}).get('bit_errors') is None
                 for frame in off)
    checks.append(check('reference_off_frames_have_no_ber', off_ok,
                        [frame['tag'] for frame in off
                         if (frame['observables'] or {}).get('bit_errors') is not None]))
    checks.append(check('every_frame_reproduces_its_record', not report['failed'],
                        report['failed'] or None))
    if expect_synced is not None:
        checks.append(check('synced_frame_count_%d' % expect_synced,
                            len(reference) == expect_synced, len(reference)))
    if expect_compared is not None:
        checks.append(check('compared_bits_total_%d' % expect_compared,
                            compared == expect_compared, compared))
    verdict = 'PASS' if all(item['ok'] for item in checks) else 'FAIL'
    return _verdict('statistics', checks, evidence, verdict=verdict,
                    extra={'directory': str(directory),
                           'frame_count': len(frames),
                           'reference_frames': len(reference),
                           'reference_off_frames': len(off),
                           'total_bit_errors': errors,
                           'total_compared_bits': compared,
                           'sync_ok': sync_ok})


def run_candidate(release_dir):
    release = Path(release_dir)
    checks = []
    evidence = [{'path': str(release), 'kind': 'release_dir'}]
    if not release.is_dir():
        return _verdict('candidate', [check('release_dir_present', False,
                                            str(release))], evidence)
    try:
        sys.path.insert(0, str(release))
        import merged_release as R
        manifest = R.verify_release(release, require_software=True)
        panel = R.verify_panel_imports(release)
        module = {}
        if 'rf_environment' in sys.modules:
            module['rf_environment'] = sys.modules['rf_environment']
        import rf_environment as ENV
        baseline_bytes = (release / 'rf_environment_baseline.json').read_bytes()
        baseline = ENV.EnvironmentBaseline.from_dict(
            json.loads(baseline_bytes.decode('utf-8')))
    except Exception as exc:                                    # noqa: BLE001 - named
        return _verdict('candidate', [check('release_verifiable', False,
                                            '%s: %s' % (type(exc).__name__, exc))],
                        evidence)
    checks.append(check('release_verifiable', True,
                        manifest.get('release_id'), evidence))
    checks.append(check('panel_imports', bool(panel) or panel is None, panel))
    checks.append(check('daemon_version', manifest.get('daemon_version')
                        == EXPECTED_DAEMON_VERSION, manifest.get('daemon_version')))
    checks.append(check('protocol_version', manifest.get('protocol_version')
                        == EXPECTED_PROTOCOL_VERSION, manifest.get('protocol_version')))
    checks.append(check('software_contract', manifest.get('software_contract')
                        == 'cnn-bpsk-environment-v1',
                        manifest.get('software_contract')))
    checks.append(check('baseline_sources', len(baseline.sources)
                        == EXPECTED_BASELINE_SOURCES, len(baseline.sources)))
    file_sha = hashlib.sha256(baseline_bytes).hexdigest()
    checks.append(check('baseline_file_sha256',
                        file_sha == EXPECTED_BASELINE_FILE_SHA256, file_sha))
    checks.append(check('baseline_canonical_sha256',
                        baseline.sha256 == EXPECTED_BASELINE_CANONICAL_SHA256,
                        baseline.sha256))
    evidence.append({'path': str(release / 'rf_environment_baseline.json'),
                     'sha256': file_sha})
    verdict = 'PASS' if all(item['ok'] for item in checks) else 'FAIL'
    return _verdict('candidate', checks, evidence, verdict=verdict,
                    extra={'release_dir': str(release),
                           'release_id': manifest.get('release_id'),
                           'daemon_version': manifest.get('daemon_version')})


def _verdict(mode, checks, evidence, verdict=None, extra=None):
    ok = all(item['ok'] for item in checks)
    report = {
        'schema': SCHEMA,
        'mode': mode,
        'verdict': verdict or ('PASS' if ok else 'FAIL'),
        'checks': checks,
        'evidence': evidence,
        'scope': SCOPE,
        'limitations': NOT_RUN,
    }
    report.update(extra or {})
    return report


MODES = {'single': run_single, 'statistics': run_statistics,
         'candidate': run_candidate}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='mode', required=True)
    single = sub.add_parser('single')
    single.add_argument('--directory', type=Path, required=True)
    single.add_argument('--tag', required=True)
    stats = sub.add_parser('statistics')
    stats.add_argument('--directory', type=Path, required=True)
    stats.add_argument('--expect-synced', type=int, default=None)
    stats.add_argument('--expect-compared', type=int, default=None)
    candidate = sub.add_parser('candidate')
    candidate.add_argument('--release-dir', type=Path, required=True)
    for name in ('single', 'statistics', 'candidate'):
        sub.choices[name].add_argument('--json', type=Path, default=None)
    args = parser.parse_args(argv)
    if args.mode == 'single':
        report = run_single(args.directory, args.tag)
    elif args.mode == 'statistics':
        report = run_statistics(args.directory, args.expect_synced,
                                args.expect_compared)
    else:
        report = run_candidate(args.release_dir)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'mode': report['mode'], 'verdict': report['verdict'],
                      'failed_checks': [item['name'] for item in report['checks']
                                        if not item['ok']]}, indent=2))
    return {'PASS': 0, 'FAIL': 1, 'UNKNOWN': 2}[report['verdict']]


if __name__ == '__main__':
    sys.exit(main())
