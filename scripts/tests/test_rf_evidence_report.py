"""Tests for the automatic JSON+Markdown evidence report."""
import json
from pathlib import Path

from scripts import rf_evidence_report as rep

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / 'evidence/rf-teaching/s2-46db-observer-next-2026-10-19/demo'
DEMO = DEMO_DIR / 'bpsk-demo-2026-10-20.json'
SINGLE = DEMO_DIR / 'acceptance-single.json'
STATS = DEMO_DIR / 'acceptance-statistics.json'
CANDIDATE = DEMO_DIR / 'acceptance-candidate.json'
RECORDS = DEMO_DIR / 'replay-all.json'


def built():
    return rep.build_report(demo=DEMO, acceptances=[SINGLE, STATS, CANDIDATE],
                            records=RECORDS, title='demo evidence')


def test_a_complete_artifact_set_produces_a_pass_report():
    report = built()
    assert report['schema'] == 'rf-evidence-report-v1'
    assert report['verdict'] == 'PASS'
    assert len(report['inputs']) == 5
    assert all(row['sha256'] for row in report['inputs'])
    assert report['demo']['frame_count'] == 33
    assert report['records']['record_count'] == 33
    assert [row['mode'] for row in report['acceptance']] == \
        ['single', 'statistics', 'candidate']
    assert report['limitations']
    assert 'prefills no pass' in report['statement']


def test_a_missing_acceptance_makes_the_verdict_unknown_not_pass():
    report = rep.build_report(demo=DEMO, records=RECORDS)
    assert report['verdict'] == 'UNKNOWN'
    assert 'NO_ACCEPTANCE_INPUT' in report['verdict_reasons']


def test_a_failing_artifact_makes_the_verdict_fail(tmp_path):
    broken = json.loads(SINGLE.read_text(encoding='utf-8'))
    broken['verdict'] = 'FAIL'
    path = tmp_path / 'broken.json'
    path.write_text(json.dumps(broken), encoding='utf-8')
    report = rep.build_report(demo=DEMO, acceptances=[path], records=RECORDS)
    assert report['verdict'] == 'FAIL'
    assert 'acceptance:single' in report['verdict_reasons']


def test_markdown_contains_the_required_sections_and_not_run_list():
    markdown = rep.render_markdown(built())
    for section in ('## Inputs', '## Demo', '## Acceptance', '## Records',
                    '## Limitations / NOT RUN'):
        assert section in markdown
    assert 'NOT RUN' in markdown
    assert 'formal S2 acceptance' in markdown
    assert 'PASS' in markdown


def test_no_inputs_is_a_named_cli_error(tmp_path, capsys):
    assert rep.main(['--output-json', str(tmp_path / 'x.json'),
                     '--output-md', str(tmp_path / 'x.md')]) == 2
    assert json.loads(capsys.readouterr().out)['code'] == 'NO_INPUTS'


def test_an_unreadable_input_is_a_named_cli_error(tmp_path, capsys):
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json', encoding='utf-8')
    assert rep.main(['--demo', str(bad)]) == 2
    assert json.loads(capsys.readouterr().out)['code'] == 'INPUT_UNREADABLE'


def test_the_cli_writes_both_artifacts(tmp_path):
    out_json = tmp_path / 'report.json'
    out_md = tmp_path / 'report.md'
    assert rep.main(['--demo', str(DEMO), '--acceptance', str(SINGLE),
                     '--acceptance', str(STATS), '--acceptance', str(CANDIDATE),
                     '--records', str(RECORDS), '--output-json', str(out_json),
                     '--output-md', str(out_md)]) == 0
    report = json.loads(out_json.read_text(encoding='utf-8'))
    assert report['verdict'] == 'PASS'
    assert out_md.read_text(encoding='utf-8').startswith('# ')
