"""Generate a structured JSON + Markdown report from saved evidence (zero device).

The report is assembled FROM artifacts that already exist (demo report, acceptance
runs, record replay).  It never invents a verdict: if an input is missing the
corresponding section says NOT PROVIDED and the overall verdict is UNKNOWN, so a
report cannot look like a pass it did not receive.

    python scripts/rf_evidence_report.py \
        --demo demo.json --acceptance acceptance-*.json --records replay.json \
        --output-json report.json --output-md report.md
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = 'rf-evidence-report-v1'
SCOPE = ('automatically assembled from saved offline evidence; not a new board '
         'measurement, not the formal acceptance, no RF, no board')
NOT_RUN = [
    'trial deployment / install (authorization pending)',
    'PL reload / overlay change',
    'any new RF capture or transmission',
    '80 MHz experiment',
    'formal S2 acceptance (108 physical + 12 offline TEST)',
    'human acceptance',
]


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8'))


def _input_row(path):
    return {'path': str(path), 'sha256': sha256_file(path)}


def summarise_demo(path):
    demo = load_json(path)
    frames = demo.get('frames') or []
    synced = [f for f in frames
              if (f.get('observables') or {}).get('reference_sync') is True]
    off = [f for f in frames
           if (f.get('observables') or {}).get('reference_sync') is False]
    return {
        'provided': True,
        'passed': demo.get('passed'),
        'frame_count': demo.get('frame_count'),
        'reference_frames': len(synced),
        'reference_off_frames': len(off),
        'engine_version': demo.get('engine_version'),
        'condition': demo.get('condition'),
        'max_float_error': max([f.get('max_float_error') or 0.0 for f in frames]
                               or [0.0]),
        'failed': demo.get('failed'),
        'scope': demo.get('scope'),
    }


def summarise_acceptance(path):
    report = load_json(path)
    return {
        'provided': True,
        'mode': report.get('mode'),
        'verdict': report.get('verdict'),
        'failed_checks': [c.get('name') for c in report.get('checks') or []
                          if not c.get('ok')],
        'checks': len(report.get('checks') or []),
        'evidence_files': len(report.get('evidence') or []),
        'release_id': report.get('release_id'),
    }


def summarise_records(path):
    report = load_json(path)
    return {
        'provided': True,
        'passed': report.get('passed'),
        'record_count': report.get('record_count'),
        'failure_records_replayed': report.get('failure_records_replayed'),
        'failed': report.get('failed'),
    }


def verdict_of(demo, acceptances, records):
    """PASS only if every provided artifact passes; UNKNOWN if any is missing."""
    provided = [demo is not None] + [a is not None for a in acceptances] \
        + [records is not None]
    if not any(provided):
        return 'UNKNOWN', 'NO_EVIDENCE'
    checks = []
    if demo is not None:
        checks.append(('demo', demo.get('passed') is True))
    for acceptance in acceptances:
        checks.append(('acceptance:%s' % acceptance.get('mode'),
                       acceptance.get('verdict') == 'PASS'))
    if records is not None:
        checks.append(('records', records.get('passed') is True))
    if not all(ok for _, ok in checks):
        return 'FAIL', [name for name, ok in checks if not ok]
    if not acceptances:
        return 'UNKNOWN', 'NO_ACCEPTANCE_INPUT'
    if not all(provided):
        return 'UNKNOWN', 'SOME_EVIDENCE_NOT_PROVIDED'
    return 'PASS', []


def build_report(*, demo=None, acceptances=(), records=None, title='RX evidence report'):
    demo_summary = summarise_demo(demo) if demo else {'provided': False}
    acceptance_summaries = [summarise_acceptance(path) for path in acceptances]
    records_summary = summarise_records(records) if records else {'provided': False}
    verdict, reasons = verdict_of(demo_summary if demo else None,
                                  [a for a in acceptance_summaries],
                                  records_summary if records else None)
    inputs = []
    for path in ([demo] if demo else []) + list(acceptances) + ([records] if records else []):
        inputs.append(_input_row(path))
    return {
        'schema': SCHEMA,
        'title': title,
        'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope': SCOPE,
        'verdict': verdict,
        'verdict_reasons': reasons,
        'inputs': inputs,
        'demo': demo_summary,
        'acceptance': acceptance_summaries,
        'records': records_summary,
        'limitations': NOT_RUN,
        'statement': ('generated from the input artifacts above; it adds no '
                      'measurement and prefills no pass'),
    }


def render_markdown(report):
    lines = ['# %s' % report['title'], '']
    lines.append('- verdict: **%s**%s' % (
        report['verdict'],
        '' if not report['verdict_reasons']
        else ' (%s)' % ', '.join(map(str, report['verdict_reasons']))))
    lines.append('- schema: `%s`' % report['schema'])
    lines.append('- generated_utc: `%s`' % report['generated_utc'])
    lines.append('- scope: %s' % report['scope'])
    lines += ['', '## Inputs', '',
              '| file | sha256 |', '|---|---|']
    for row in report['inputs']:
        lines.append('| `%s` | `%s` |' % (row['path'], row['sha256']))
    lines += ['', '## Demo', '']
    demo = report['demo']
    if demo.get('provided'):
        lines += ['- passed: `%s`' % demo['passed'],
                  '- frames: %s (reference %s / off %s)' % (
                      demo['frame_count'], demo['reference_frames'],
                      demo['reference_off_frames']),
                  '- engine: `%s`' % demo['engine_version'],
                  '- max float error vs recorded: `%s`' % demo['max_float_error'],
                  '- condition: `%s`' % json.dumps(demo['condition'])]
    else:
        lines.append('- NOT PROVIDED')
    lines += ['', '## Acceptance', '']
    if report['acceptance']:
        lines += ['| mode | verdict | failed checks | release |',
                  '|---|---|---|---|']
        for row in report['acceptance']:
            lines.append('| %s | %s | %s | %s |' % (
                row['mode'], row['verdict'],
                ','.join(row['failed_checks']) or '-', row.get('release_id') or '-'))
    else:
        lines.append('- NOT PROVIDED')
    lines += ['', '## Records', '']
    records = report['records']
    if records.get('provided'):
        lines += ['- replay passed: `%s`' % records['passed'],
                  '- records: %s (failed demods replayed: %s)' % (
                      records['record_count'], records['failure_records_replayed'])]
    else:
        lines.append('- NOT PROVIDED')
    lines += ['', '## Limitations / NOT RUN', '']
    lines += ['- %s' % item for item in report['limitations']]
    lines += ['', '> %s' % report['statement'], '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--demo', type=Path, default=None)
    parser.add_argument('--acceptance', type=Path, action='append', default=[])
    parser.add_argument('--records', type=Path, default=None)
    parser.add_argument('--title', default='RX evidence report')
    parser.add_argument('--output-json', type=Path, default=None)
    parser.add_argument('--output-md', type=Path, default=None)
    args = parser.parse_args(argv)
    if args.demo is None and not args.acceptance and args.records is None:
        print(json.dumps({'error': 'no input artifact; nothing to report',
                          'code': 'NO_INPUTS'}, indent=2))
        return 2
    try:
        report = build_report(demo=args.demo, acceptances=args.acceptance,
                              records=args.records, title=args.title)
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': '%s: %s' % (type(exc).__name__, exc),
                          'code': 'INPUT_UNREADABLE'}, indent=2))
        return 2
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2) + '\n',
                                    encoding='utf-8')
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(render_markdown(report), encoding='utf-8')
    print(json.dumps({'schema': report['schema'], 'verdict': report['verdict'],
                      'verdict_reasons': report['verdict_reasons']}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
