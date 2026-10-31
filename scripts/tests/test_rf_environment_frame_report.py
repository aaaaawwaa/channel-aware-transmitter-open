"""Amplitude-report tests: field presence, verbatim labels, doc constants.

Two contracts are pinned here.

1. The per-frame report (scripts/rf_environment_frame_report.py) is a
   presentation layer: every number it prints is read back from an artifact a
   qualified tool already wrote, and every label/reason is copied verbatim.  It
   never evaluates the environment rules, never recomputes a label, never reads
   IQ samples and never derives a missing value.
2. The append-only documentation section that describes
   AMPLITUDE_OUTSIDE_RULE_SUPPORT (its real semantics = crest/plateau outside
   the TRAIN fitted envelope) and the amplitude annotation (a synthetic-model
   boundary that is explicitly NOT an acceptance precondition and NOT a reason
   to exclude or reclassify a failing frame) must stay consistent with the
   product source, the frozen baseline and the recorded pilot artifacts.

Nothing in this file asserts or creates a new acceptance criterion, and nothing
here reclassifies a failing frame.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts import rf_environment_frame_report as R

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs/superpowers/specs/2026-10-17-headroom-46db-environment-migration.md'
RULE = ROOT / 'pynq_overlay/sw/rf_environment.py'
PILOT = ROOT / 'evidence/rf-teaching/s2-46db-baseline-pilot-2026-10-18/pilot-01'
FROZEN_BASELINE = (ROOT / 'evidence/rf-teaching/s2-46db-baseline-pilot-2026-10-18/replay/source-root'
                   / 'pynq_overlay/sw/rf_environment_baseline.json')
ANNOTATION = (ROOT / 'evidence/rf-teaching/s2-46db-first-frame-amplitude-2026-10-19/work'
              / 'amplitude-support-experiment.json')
LAW = (ROOT / 'evidence/rf-teaching/s2-46db-first-frame-amplitude-2026-10-19/work/campaign-law.json')
SECTION_MARKER = '## L. 2026-10-19'
CONSTANTS_BEGIN = '<!-- amplitude-support-doc-constants:begin -->'
CONSTANTS_END = '<!-- amplitude-support-doc-constants:end -->'
EVIDENCE_BOUND = pytest.mark.skipif(not (PILOT / 'result.json').is_file(),
                                    reason='the 46 dB pilot evidence package is not present')


def doc_text():
    assert DOC.is_file(), 'the migration spec must exist'
    return DOC.read_text(encoding='utf-8')


def doc_section():
    text = doc_text()
    assert SECTION_MARKER in text, 'the append-only amplitude section is missing'
    return text[text.index(SECTION_MARKER):]


def doc_constants():
    text = doc_text()
    assert CONSTANTS_BEGIN in text and CONSTANTS_END in text, 'the doc constants block is missing'
    body = text.split(CONSTANTS_BEGIN, 1)[1].split(CONSTANTS_END, 1)[0]
    values = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if sep:
            values[key.strip()] = value.strip()
    return values


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def frame_artifacts(pilot, tag, *, truth, label, reason, rms_codes, crest, threshold,
                    complex_rms, complex_dbfs, op='rx.capture', physical=True,
                    declaration_truth=None, features=None, evidence=None,
                    metadata=True, sidecar=True):
    """Write only the artifacts a finished capture leaves behind (never IQ)."""
    if metadata:
        write_json(pilot / (tag + '.json'),
                   dict(op=op, physical=physical,
                        analysis=dict(complex_rms=complex_rms, complex_dbfs=complex_dbfs)))
    if sidecar:
        features = dict(rms_codes=rms_codes, crest_ratio=crest, **(features or {}))
        evidence = dict(pulse_crest_threshold=threshold, **(evidence or {}))
        write_json(pilot / (tag + '.environment.json'),
                   dict(environment=dict(label=label, reason=reason, features=features,
                                         rule_evidence=evidence),
                        scenario_declaration=dict(
                            truth=truth if declaration_truth is None else declaration_truth)))


def fixture_pilot(tmp_path):
    """Two sessions: s00 has normal+off, s01 has normal only (no noise floor)."""
    pilot = tmp_path / 'pilot-01'
    frame_artifacts(pilot, 's00-normal-00', truth='normal', label='UNKNOWN',
                    reason='AMPLITUDE_OUTSIDE_RULE_SUPPORT', rms_codes=268.91733449436634,
                    crest=2.0911514508063367, threshold=1.9810145218306414,
                    complex_rms=269.79795002536736, complex_dbfs=-20.616126092740245,
                    features=dict(pulse_plateau_sample_fraction=0.007080078125),
                    evidence=dict(pulse_plateau_outside_support=False))
    frame_artifacts(pilot, 's00-off-00', truth='off', label='UNKNOWN',
                    reason='AMPLITUDE_OUTSIDE_RULE_SUPPORT', rms_codes=89.0168420883521,
                    crest=4.025729000088425, threshold=1.9810145218306414,
                    complex_rms=90.5676, complex_dbfs=-30.1014, physical=False,
                    declaration_truth='compound',
                    evidence=dict(pulse_plateau_outside_support=True))
    frame_artifacts(pilot, 's01-normal-00', truth='normal', label='CLEAR',
                    reason='NO_COVERED_ANOMALY_IN_WINDOW', rms_codes=630.6954926048226,
                    crest=1.5003843702884907, threshold=1.9810145218306414,
                    complex_rms=630.8110, complex_dbfs=-13.24)
    write_json(pilot / 'result.json', dict(
        passed=False, stop_cause=None, known_correct=2, negative_false_clear=0,
        sessions=[dict(index=0), dict(index=1)],
        records=[dict(tag='s00-normal-00', truth='normal', session_index=0),
                 dict(tag='s00-off-00', truth='off', session_index=0),
                 dict(tag='s01-normal-00', truth='normal', session_index=1)]))
    write_json(pilot / 'receipt.json', dict(board_state='VERIFIED_SAFE', passed=True))
    return pilot


def baseline_file(tmp_path, pulse_crest=1.9810145218306414):
    path = tmp_path / 'baseline.json'
    write_json(path, dict(environment_version='rf-window-environment-v4',
                          thresholds=dict(pulse_crest=pulse_crest, minimum_rms_codes=16.0)))
    return path


def test_frame_report_exposes_amplitude_noise_floor_and_crest_columns(tmp_path):
    report = R.build_report(fixture_pilot(tmp_path), baseline=baseline_file(tmp_path))
    assert report['schema'] == 'rf-environment-frame-report-v1'
    assert report['rule_evaluated_here'] is False
    normal, off, second = report['frames']
    assert [row['tag'] for row in report['frames']] == ['s00-normal-00', 's00-off-00', 's01-normal-00']
    assert normal['rms_codes'] == 268.91733449436634
    assert normal['complex_rms'] == 269.79795002536736
    assert normal['complex_dbfs'] == -20.616126092740245
    assert normal['noise_floor_codes'] == 89.0168420883521
    assert normal['noise_floor_dbfs'] == -30.1014
    assert normal['noise_floor_source_tag'] == 's00-off-00'
    assert normal['crest_ratio'] == 2.0911514508063367
    assert normal['crest_threshold'] == 1.9810145218306414
    assert normal['crest_excess'] == pytest.approx(2.0911514508063367 - 1.9810145218306414)
    assert normal['crest_exceeds_threshold'] is True
    assert normal['rms_lower_bound_codes'] == 16.0
    assert normal['below_rule_support_level'] is True
    assert normal['plateau_outside_support'] is False
    assert off['crest_exceeds_threshold'] is True and off['below_rule_support_level'] is True
    assert second['crest_exceeds_threshold'] is False and second['below_rule_support_level'] is False
    assert second['noise_floor_codes'] is None
    floors = {row['session_index']: row for row in report['session_noise_floor']}
    assert floors[0]['rms_codes'] == 89.0168420883521 and floors[0]['source_tag'] == 's00-off-00'
    assert floors[1]['rms_codes'] is None and floors[1]['source_tag'] is None


def test_markdown_carries_the_new_columns_for_every_frame(tmp_path):
    report = R.build_report(fixture_pilot(tmp_path), baseline=baseline_file(tmp_path))
    text = R.render_markdown(report)
    for header in ('rms_codes', 'complex RMS', 'dBFS', '噪声底', 'crest', '阈值'):
        assert header in text
    frame_table = text.split('同批噪声底（按会话）')[0]
    body = [line for line in frame_table.splitlines()
            if line.startswith('| ') and not line.startswith('|---') and 'tag' not in line]
    assert len(body) == len(report['frames'])
    for line in body:
        assert line.count('|') >= 16
    assert 'NOT_AVAILABLE' in text
    assert '不改变任何判据' in text


def test_markdown_marks_the_amplitude_boundary_as_exploration_only(tmp_path):
    report = R.build_report(fixture_pilot(tmp_path), baseline=baseline_file(tmp_path))
    annotation = report['amplitude_support_annotation']
    assert annotation['not_a_criterion'] is True
    assert annotation['not_an_acceptance_precondition'] is True
    text = R.render_markdown(report)
    assert '探索结果' in text
    assert '非验收前置条件' in text
    assert '不是板卡实际总体 normal 拒收率' in text
    assert '不得据此排除' in text
    assert 's00-normal-00' in text and '原样保留' in text
    # No requirement wording may reappear in the rendered report.
    assert '需要窗内' not in text
    assert '前置条件' not in text.replace('非验收前置条件', '')


def test_labels_and_verdict_are_copied_verbatim_and_no_rule_is_evaluated(tmp_path):
    pilot = fixture_pilot(tmp_path)
    # A CLEAR label with a crest far above the threshold and an rms far below the
    # support level: a renderer that judged anything would have to disagree.
    frame_artifacts(pilot, 's01-normal-00', truth='normal', label='CLEAR',
                    reason='NO_COVERED_ANOMALY_IN_WINDOW', rms_codes=10.0, crest=9.9,
                    threshold=1.9810145218306414, complex_rms=10.0, complex_dbfs=-46.0)
    report = R.build_report(pilot, baseline=baseline_file(tmp_path))
    row = report['frames'][2]
    assert row['label'] == 'CLEAR' and row['reason'] == 'NO_COVERED_ANOMALY_IN_WINDOW'
    assert row['verdict'] == 'correct'
    assert row['below_rule_support_level'] is True and row['crest_exceeds_threshold'] is True
    assert report['rule_evaluated_here'] is False
    assert 'verbatim' in report['labels_source']


def test_missing_frame_artifacts_are_not_available_and_never_recomputed(tmp_path):
    pilot = fixture_pilot(tmp_path)
    (pilot / 's01-normal-00.json').unlink()
    (pilot / 's01-normal-00.environment.json').unlink()
    report = R.build_report(pilot, baseline=baseline_file(tmp_path))
    row = report['frames'][2]
    for key in ('label', 'reason', 'rms_codes', 'complex_rms', 'complex_dbfs', 'crest_ratio',
                'crest_excess', 'noise_floor_codes'):
        assert row[key] is None, key
    assert row['label_source'] == 'NOT_AVAILABLE'
    # The only value that survives a missing frame artifact is the one read from the
    # separately supplied frozen baseline; it names its own source and derives nothing.
    assert row['crest_threshold'] == 1.9810145218306414
    assert row['crest_threshold_source'] == 'frozen baseline thresholds.pulse_crest'
    text = R.render_markdown(report)
    assert 'NOT_AVAILABLE' in text


def test_crest_threshold_that_disagrees_with_the_frozen_baseline_is_refused(tmp_path):
    pilot = fixture_pilot(tmp_path)
    with pytest.raises(ValueError, match='frozen baseline'):
        R.build_report(pilot, baseline=baseline_file(tmp_path, pulse_crest=1.75))


def test_sidecar_and_result_row_disagreement_is_flagged_not_resolved(tmp_path):
    pilot = fixture_pilot(tmp_path)
    result = json.loads((pilot / 'result.json').read_text(encoding='utf-8'))
    result['records'][0]['environment'] = dict(label='CLEAR', reason='NO_COVERED_ANOMALY_IN_WINDOW')
    write_json(pilot / 'result.json', result)
    report = R.build_report(pilot, baseline=baseline_file(tmp_path))
    row = report['frames'][0]
    assert row['sidecar_matches_row'] is False
    assert row['label'] == 'UNKNOWN' and row['label_source'].startswith('s00-normal-00.environment.json')
    assert 'sidecar 与 result.json 行不一致' in R.render_markdown(report)


def test_report_reads_the_artifacts_without_writing_them(tmp_path):
    pilot = fixture_pilot(tmp_path)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(pilot.rglob('*')) if path.is_file()}
    R.build_report(pilot, baseline=baseline_file(tmp_path))
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(pilot.rglob('*')) if path.is_file()}
    assert before == after


def test_renderer_constants_match_the_documented_amplitude_support_constants():
    constants = doc_constants()
    assert float(constants['support_boundary_codes']) == pytest.approx(R.AMPLITUDE_SUPPORT_BOUNDARY_CODES)
    assert float(constants['support_boundary_dbfs']) == pytest.approx(R.AMPLITUDE_SUPPORT_BOUNDARY_DBFS)
    assert float(constants['support_boundary_codes']) == pytest.approx(303.0, abs=1.0)
    assert float(constants['support_boundary_dbfs']) == pytest.approx(-19.6, abs=0.1)


def test_documented_rule_facts_match_the_product_source():
    constants = doc_constants()
    source = RULE.read_text(encoding='utf-8')
    lines = source.splitlines()

    def at(reference):
        path, _, span = reference.rpartition(':')
        assert path.endswith('rf_environment.py'), reference
        first, _, last = span.partition('-')
        start = int(first)
        end = int(last) if last else start
        assert 1 <= start <= end <= len(lines), reference
        return chr(10).join(lines[start - 1:end])

    crest_branch = at(constants['reason_crest_branch'])
    assert 'crest_ratio' in crest_branch and 'pulse_crest' in crest_branch
    assert 'AMPLITUDE_OUTSIDE_RULE_SUPPORT' in crest_branch
    plateau_branch = at(constants['reason_plateau_branch'])
    assert 'plateau_outside' in plateau_branch
    assert 'AMPLITUDE_OUTSIDE_RULE_SUPPORT' in plateau_branch
    threshold_line = at(constants['crest_threshold_line']).replace(' ', '')
    assert 'pulse_crest=max(1.20,max(crest)+.08)' in threshold_line
    lower_line = at(constants['minimum_rms_codes_line']).replace(' ', '')
    assert 'minimum_rms_codes=16' in lower_line
    gate_line = at(constants['rms_lower_bound_compare'])
    assert 'minimum_rms_codes' in gate_line and 'LOW_ENERGY' in gate_line
    # There is no RMS upper bound anywhere in the rule: the reason name is about
    # the crest/plateau envelope, not about an RMS interval.
    assert not re.search(r"rms_codes'?\]?\s*>\s*t\[", source)
    assert constants['reason_token'] == 'AMPLITUDE_OUTSIDE_RULE_SUPPORT'
    assert constants['threshold_owner'] == 'pulse_crest'
    assert constants['crest_threshold_rule'].replace(' ', '') == 'pulse_crest=max(1.20,max(TRAINcrest)+0.08)'
    assert constants['changes_no_criterion'] == 'true'
    # The synthetic boundary is an exploration result: never an acceptance
    # precondition, never a frame-exclusion rule, never a board rejection rate.
    assert constants['amplitude_boundary_is_exploration_only'] == 'true'
    assert constants['amplitude_boundary_is_acceptance_precondition'] == 'false'
    assert constants['amplitude_boundary_may_exclude_frames'] == 'false'
    assert constants['synthetic_probability_is_board_rejection_rate'] == 'false'


def test_documented_amplitude_annotation_is_exploration_only_not_a_precondition():
    section = doc_section()
    annotation = [line for line in section.splitlines()
                  if '303' in line and 'dBFS' in line and 'RMS' in line]
    assert annotation, 'the amplitude annotation line is missing'
    joined = chr(10).join(annotation)
    assert '探索' in joined, 'the boundary must be presented as an exploration result'
    assert '不是验收前置条件' in joined, 'the boundary must deny being an acceptance precondition'
    assert '不得' in joined and '排除' in joined, 'the boundary must forbid excluding frames'
    # The old, stronger framing must not come back: no requirement wording and no
    # line that turns the synthetic boundary into a gate or an exclusion rule.
    assert '电平前置条件' not in section
    assert '写明，不是放宽' not in section
    assert not any('要求窗内 complex RMS' in line for line in section.splitlines())
    assert not any(line.strip().startswith('**前置条件') for line in section.splitlines())
    assert 's00-normal-00' in section and 'known_correct=8' in section
    assert '不改变任何判据' in section
    assert 'stale-by-design' in section
    assert 'rx.capture' in section
    assert 'AMPLITUDE_OUTSIDE_RULE_SUPPORT' in section
    assert 'crest' in section and 'RMS' in section
    assert 's2-46db-first-frame-amplitude-2026-10-19' in section


@EVIDENCE_BOUND
def test_documented_frozen_baseline_and_annotation_constants_match_the_artifacts():
    constants = doc_constants()
    baseline = json.loads(FROZEN_BASELINE.read_text(encoding='utf-8'))
    annotation = json.loads(ANNOTATION.read_text(encoding='utf-8'))
    law = json.loads(LAW.read_text(encoding='utf-8'))
    assert float(constants['frozen_baseline_pulse_crest']) == baseline['thresholds']['pulse_crest']
    assert float(constants['frozen_baseline_pulse_crest']) == annotation['thresholds']['pulse_crest']
    assert float(constants['frozen_baseline_minimum_rms_codes']) == baseline['thresholds']['minimum_rms_codes']
    assert float(constants['support_boundary_codes']) == pytest.approx(annotation['rms_at_boundary'], abs=0.5)
    assert float(constants['support_boundary_dbfs']) == pytest.approx(annotation['dbfs_at_boundary'], abs=0.05)
    assert float(constants['synthetic_unknown_probability']) == law['synthetic']['p_crest_above_threshold']
    assert float(constants['synthetic_trials']) == law['synthetic']['n']
    assert float(constants['noise_floor_codes_approx']) == pytest.approx(
        annotation['noise_rms']['s00'], abs=1.0)


@EVIDENCE_BOUND
def test_documented_pilot_frame_facts_match_the_pilot_artifacts():
    constants = doc_constants()
    result = json.loads((PILOT / 'result.json').read_text(encoding='utf-8'))
    row = result['records'][0]
    sidecar = json.loads((PILOT / 's00-normal-00.environment.json').read_text(encoding='utf-8'))
    saved = json.loads((PILOT / 's00-normal-00.json').read_text(encoding='utf-8'))
    assert row['tag'] == constants['pilot_frame']
    assert sidecar['environment']['label'] == constants['pilot_frame_label']
    assert sidecar['environment']['reason'] == constants['pilot_frame_reason']
    assert sidecar['environment']['features']['rms_codes'] == float(constants['pilot_frame_rms_codes'])
    assert sidecar['environment']['features']['crest_ratio'] == float(constants['pilot_frame_crest'])
    assert sidecar['environment']['rule_evidence']['pulse_crest_threshold'] == float(
        constants['frozen_baseline_pulse_crest'])
    assert result['known_correct'] == int(constants['pilot_known_correct'])
    assert result['passed'] is False
    assert int(constants['pilot_required_known_correct']) - result['known_correct'] == 1
    assert saved['analysis']['complex_dbfs'] == pytest.approx(
        float(constants['pilot_frame_complex_dbfs']), abs=1e-9)
    report = R.build_report(PILOT)
    assert len(report['frames']) == 18
    first = report['frames'][0]
    assert first['label'] == constants['pilot_frame_label']
    assert first['complex_dbfs'] == saved['analysis']['complex_dbfs']
    assert first['noise_floor_codes'] == pytest.approx(
        report['session_noise_floor'][0]['rms_codes'])
    # The three UNSUPPORTED_DOMAIN frames return before feature extraction, so they
    # have no rule features at all: the report says NOT_AVAILABLE instead of guessing.
    without_features = [frame['tag'] for frame in report['frames'] if frame['rms_codes'] is None]
    assert without_features == [frame['tag'] for frame in report['frames']
                                if frame['reason'] == 'UNSUPPORTED_DOMAIN']
    assert len(without_features) == 3
    assert all(frame['rms_codes'] is not None for frame in report['frames']
               if frame['reason'] != 'UNSUPPORTED_DOMAIN')


@EVIDENCE_BOUND
def test_off_frame_declaration_is_stale_by_design_and_documented():
    result = json.loads((PILOT / 'result.json').read_text(encoding='utf-8'))
    off_row = [row for row in result['records'] if row['tag'] == 's00-off-00'][0]
    sidecar = json.loads((PILOT / 's00-off-00.environment.json').read_text(encoding='utf-8'))
    saved = json.loads((PILOT / 's00-off-00.json').read_text(encoding='utf-8'))
    assert off_row['truth'] == 'off'
    assert saved['op'] == 'rx.capture' and saved['physical'] is False
    assert sidecar['scenario_declaration']['truth'] != 'off'
    section = doc_section()
    stale = [line for line in section.splitlines()
             if 'stale-by-design' in line and 'scenario_declaration' in line]
    assert stale, 'the stale-by-design off declaration must be documented'
    assert 'rx.capture' in section
