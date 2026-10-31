"""Presentation-only per-frame report for a finished environment pilot.

This module is a reader, not a judge:

* it copies label/reason/features/rule_evidence **verbatim** from the artifacts a
  qualified tool already wrote (the per-frame record sidecar, else the result row);
* it never evaluates the environment rules, never recomputes a label, never opens
  IQ bytes and never derives a value that is absent from an artifact (absent
  fields are reported as NOT_AVAILABLE);
* the only numbers it adds are unit conversions of an existing artifact number
  (codes -> dBFS for a noise floor that has no saved dBFS) and the annotation
  constants documented in the append-only spec section (see
  docs/superpowers/specs/2026-10-17-headroom-46db-environment-migration.md, section L).

Adding this report changes no threshold, no rule, no baseline and no verdict.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'rf-environment-frame-report-v1'
NOT_AVAILABLE = 'NOT_AVAILABLE'
FULL_SCALE_CODE = 2048.0
EXPECTED = {'normal': 'CLEAR', 'narrowband': 'NARROWBAND', 'pulse': 'PULSE'}
# Annotation, NOT a criterion and NOT an acceptance precondition: the received
# complex RMS at which a normal frame of this frozen baseline crosses
# pulse_crest = 1.9810145218306414 inside a specific scale-and-noise SYNTHETIC
# model, measured by
# evidence/rf-teaching/s2-46db-first-frame-amplitude-2026-10-19/work/amplitude_support_experiment.py
# (rms_at_boundary=303.1510618377535 codes, dbfs_at_boundary=-19.60371712744841).
AMPLITUDE_SUPPORT_BOUNDARY_CODES = 303.1510618377535
AMPLITUDE_SUPPORT_BOUNDARY_DBFS = -19.60371712744841
AMPLITUDE_SUPPORT_BOUNDARY_SOURCE = (
    'evidence/rf-teaching/s2-46db-first-frame-amplitude-2026-10-19/work/'
    'amplitude-support-experiment.json (rms_at_boundary=303.1510618377535 codes, '
    'dbfs_at_boundary=-19.60371712744841)')
NOISE_FLOOR_CODES_APPROX = 90.0
SYNTHETIC_UNKNOWN_PROBABILITY = 0.3225
SEMANTICS_NOTE = ('AMPLITUDE_OUTSIDE_RULE_SUPPORT 的真实语义 = crest/plateau 超出 TRAIN 拟合包络，'
                  '不是 RMS 区间；rms_codes 只与下界 minimum_rms_codes 比较。')


def load_json(path):
    path = Path(path)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def number(value):
    """Return a finite float, or None. Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def codes_to_dbfs(codes):
    value = number(codes)
    if value is None or value <= 0:
        return None
    return 20.0 * math.log10(value / FULL_SCALE_CODE)


def _tone(value):
    return value if isinstance(value, str) and value else None


def _environment(frame_json, row, tag):
    """Prefer the per-frame sidecar; fall back to the result row; say which."""
    environment = frame_json.get('environment') if isinstance(frame_json, dict) else None
    if isinstance(environment, dict):
        return environment, str(tag) + '.environment.json'
    row_environment = row.get('environment')
    if isinstance(row_environment, dict):
        return row_environment, 'result.json row environment'
    return None, NOT_AVAILABLE


def _frame_row(pilot, index, row, baseline_body):
    tag = row.get('tag')
    frame_json = load_json(pilot / (tag + '.json')) if isinstance(tag, str) else None
    sidecar = load_json(pilot / (tag + '.environment.json')) if isinstance(tag, str) else None
    environment, scope = _environment(sidecar, row, tag)
    features = environment.get('features') if isinstance(environment, dict) else None
    evidence = environment.get('rule_evidence') if isinstance(environment, dict) else None
    analysis = frame_json.get('analysis') if isinstance(frame_json, dict) else None
    truth = row.get('truth')
    expected = EXPECTED.get(truth)
    label = _tone(environment.get('label')) if isinstance(environment, dict) else None
    reason = _tone(environment.get('reason')) if isinstance(environment, dict) else None
    rms_codes = number(features.get('rms_codes')) if isinstance(features, dict) else None
    crest_ratio = number(features.get('crest_ratio')) if isinstance(features, dict) else None
    threshold = number(evidence.get('pulse_crest_threshold')) if isinstance(evidence, dict) else None
    threshold_scope = 'rule_evidence.pulse_crest_threshold'
    if threshold is None and isinstance(baseline_body, dict):
        threshold = number((baseline_body.get('thresholds') or {}).get('pulse_crest'))
        threshold_scope = 'frozen baseline thresholds.pulse_crest'
    row_environment = row.get('environment')
    if isinstance(environment, dict) and isinstance(row_environment, dict):
        sidecar_matches_row = (label == _tone(row_environment.get('label'))
                               and reason == _tone(row_environment.get('reason')))
    else:
        sidecar_matches_row = None
    declaration = sidecar.get('scenario_declaration') if isinstance(sidecar, dict) else None
    declaration_truth = _tone(declaration.get('truth')) if isinstance(declaration, dict) else None
    result = dict(
        index=index, tag=tag, session_index=row.get('session_index'), truth=truth,
        expected_label=expected, label=label, reason=reason, label_source=scope,
        verdict=('correct' if expected == label else
                 'negative-control-ok' if (expected is None and label != 'CLEAR') else
                 'MISMATCH' if label is not None else NOT_AVAILABLE),
        rms_codes=rms_codes, rms_codes_source='environment.features.rms_codes',
        complex_rms=number(analysis.get('complex_rms')) if isinstance(analysis, dict) else None,
        complex_dbfs=number(analysis.get('complex_dbfs')) if isinstance(analysis, dict) else None,
        analysis_source=('frame metadata analysis' if isinstance(analysis, dict) else NOT_AVAILABLE),
        crest_ratio=crest_ratio, crest_threshold=threshold, crest_threshold_source=threshold_scope,
        crest_excess=(None if crest_ratio is None or threshold is None else crest_ratio - threshold),
        crest_exceeds_threshold=(None if crest_ratio is None or threshold is None else crest_ratio > threshold),
        plateau_outside_support=(evidence.get('pulse_plateau_outside_support')
                                 if isinstance(evidence, dict) else None),
        rms_lower_bound_codes=(number((baseline_body.get('thresholds') or {}).get('minimum_rms_codes'))
                               if isinstance(baseline_body, dict) else None),
        below_rule_support_level=(None if rms_codes is None
                                  else rms_codes < AMPLITUDE_SUPPORT_BOUNDARY_CODES),
        op=frame_json.get('op') if isinstance(frame_json, dict) else None,
        physical=frame_json.get('physical') if isinstance(frame_json, dict) else None,
        scenario_declaration_truth=declaration_truth,
        declaration_is_stale=bool(truth == 'off' and declaration_truth not in (None, 'off')),
        sidecar_matches_row=sidecar_matches_row)
    return result


def _session_noise_floors(frames):
    floors = []
    for session in sorted({frame['session_index'] for frame in frames},
                          key=lambda value: (value is None, value)):
        source = None
        for frame in frames:
            if (frame['session_index'] == session and frame['truth'] == 'off'
                    and frame['rms_codes'] is not None):
                source = frame
                break
        floors.append(dict(
            session_index=session,
            source_tag=source['tag'] if source else None,
            rms_codes=source['rms_codes'] if source else None,
            dbfs=(source['complex_dbfs'] if source else None),
            physical=source['physical'] if source else None,
            note=('same-batch no-source energy floor: the off frame of this session'
                  if source else 'no off frame in this session')),
        )
    return floors


def build_report(pilot, *, baseline=None, replay=None):
    pilot = Path(pilot)
    result = load_json(pilot / 'result.json')
    if not isinstance(result, dict):
        raise ValueError('pilot result.json is missing or malformed: %s' % pilot)
    receipt = load_json(pilot / 'receipt.json') or {}
    baseline_path = Path(baseline) if baseline is not None else None
    baseline_body = load_json(baseline_path) if baseline_path is not None else None
    if baseline_path is not None and not isinstance(baseline_body, dict):
        raise ValueError('frozen baseline is unreadable: %s' % baseline_path)
    frames = [_frame_row(pilot, index, row, baseline_body)
              for index, row in enumerate(result.get('records') or [])]
    if isinstance(baseline_body, dict):
        frozen = number((baseline_body.get('thresholds') or {}).get('pulse_crest'))
        for frame in frames:
            if (frame['crest_threshold'] is not None and frozen is not None
                    and frame['crest_threshold'] != frozen):
                raise ValueError('frame crest threshold disagrees with the frozen baseline: %s'
                                 % frame['tag'])
    floors = _session_noise_floors(frames)
    by_session = {floor['session_index']: floor for floor in floors}
    for frame in frames:
        floor = by_session.get(frame['session_index'])
        frame['noise_floor_codes'] = floor['rms_codes'] if floor else None
        frame['noise_floor_dbfs'] = (floor['dbfs'] if floor and floor['dbfs'] is not None
                                     else codes_to_dbfs(floor['rms_codes']) if floor else None)
        frame['noise_floor_db_source'] = ('off frame analysis.complex_dbfs'
                                          if floor and floor['dbfs'] is not None
                                          else 'codes conversion of the off frame rms (presentation)'
                                          if floor and floor['rms_codes'] is not None else NOT_AVAILABLE)
        frame['noise_floor_source_tag'] = floor['source_tag'] if floor else None
    artifacts = ['result.json', 'receipt.json']
    if baseline_path is not None:
        artifacts.append(str(baseline_path))
    if replay is not None:
        artifacts.append(str(replay))
    return dict(
        schema=SCHEMA, pilot=str(pilot), artifacts_read=artifacts,
        rule_evaluated_here=False,
        labels_source=('environment.label / environment.reason copied verbatim from the per-frame '
                       'artifact (sidecar first, then the result row); this report never re-judges'),
        amplitude_support_annotation=dict(
            boundary_codes=AMPLITUDE_SUPPORT_BOUNDARY_CODES,
            boundary_dbfs=AMPLITUDE_SUPPORT_BOUNDARY_DBFS,
            boundary_source=AMPLITUDE_SUPPORT_BOUNDARY_SOURCE,
            noise_floor_codes_approx=NOISE_FLOOR_CODES_APPROX,
            synthetic_unknown_probability=SYNTHETIC_UNKNOWN_PROBABILITY,
            rms_upper_bound_in_rules=False, not_a_criterion=True,
            not_an_acceptance_precondition=True, semantics=SEMANTICS_NOTE),
        baseline=(None if baseline_path is None else dict(
            path=str(baseline_path),
            sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            environment_version=baseline_body.get('environment_version'),
            pulse_crest=number((baseline_body.get('thresholds') or {}).get('pulse_crest')),
            minimum_rms_codes=number((baseline_body.get('thresholds') or {}).get('minimum_rms_codes')))),
        verdict=dict(passed=result.get('passed'), stop_cause=result.get('stop_cause'),
                     frames=len(frames), sessions=len(result.get('sessions') or []),
                     known_correct=result.get('known_correct'),
                     negative_false_clear=result.get('negative_false_clear'),
                     expected_release=result.get('expected_release'),
                     board_state=receipt.get('board_state')),
        session_noise_floor=floors, frames=frames)


def cell(value):
    if value is None:
        return NOT_AVAILABLE
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float):
        # Exact artifact value: never round a threshold or a measured number in a report.
        return repr(value)
    return str(value)


def render_markdown(report):
    lines = ['# 逐帧报表（呈现层新增字段；不改变任何判据）', '',
             '- pilot：' + report['pilot'],
             '- 候选/发布：' + cell((report.get('verdict') or {}).get('expected_release'))
             + '；判定：passed=' + cell((report.get('verdict') or {}).get('passed'))
             + '、known_correct=' + cell((report.get('verdict') or {}).get('known_correct'))
             + '、board_state=' + cell((report.get('verdict') or {}).get('board_state')),
             '- 基线：' + cell((report.get('baseline') or {}).get('path'))
             + '；pulse_crest=' + cell((report.get('baseline') or {}).get('pulse_crest'))
             + '；minimum_rms_codes=' + cell((report.get('baseline') or {}).get('minimum_rms_codes')),
             '- **本表不改变任何判据**：label/reason 逐字复制自既有产物，报表不重算标签、不读 IQ 字节、'
             '不派生缺失值；缺失一律写 NOT_AVAILABLE，且 rule_evaluated_here=false。',
             '- ' + SEMANTICS_NOTE,
             '- 振幅支撑注解（**探索结果；非判据、非验收前置条件，也不得据此排除帧**）：在“对高电平帧线性缩放 + '
             '叠加实测噪声底”这一特定合成模型下，本冻结基线与约 90 codes 噪声底时 crest 恰好越过阈值约在窗内 '
             'complex RMS ≈303 codes（约 -19.6 dBFS）；该边界与合成拒收概率（32.25%，400 次合成）都只是该模型下的结果，'
             '**不是板卡实际总体 normal 拒收率**，也不得据此排除或重分类任何失败帧（`s00-normal-00` 的 '
             'UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT 原样保留，仍计 MISMATCH）。',
             '- 同批噪声底 = 同一会话 off 帧的 environment.features.rms_codes（其 dBFS 取该帧 analysis.complex_dbfs；'
             '无保存值时只做 codes→dBFS 的单位换算）。',
             '- 列来源：rms_codes/crest 来自规则侧车 environment.features，阈值来自 environment.rule_evidence，'
             'complex RMS/dBFS 来自板端元数据 analysis。',
             '- off 帧的 scenario_declaration 是 stale-by-design（沿用上次加载的场景），off 走 rx.capture、physical=false。',
             '']
    header = ('| # | 会话 | tag | 真值 | 期望 | 板端 label | reason | 判定 | rms_codes(规则特征) | complex RMS(codes) | '
              'complex dBFS | 噪声底(codes) | 噪声底(dBFS) | crest | crest 阈值 | crest 超出 | 电平低于支撑 | 声明真值 | 来源一致 |')
    lines += [header, '|' + '---|' * 19]
    for frame in report['frames']:
        lines.append('| ' + ' | '.join([
            cell(frame['index']), cell(frame['session_index']), cell(frame['tag']), cell(frame['truth']),
            cell(frame['expected_label']) if frame['expected_label'] else '（负控）', cell(frame['label']),
            cell(frame['reason']), cell(frame['verdict']), cell(frame['rms_codes']),
            cell(frame['complex_rms']), cell(frame['complex_dbfs']), cell(frame['noise_floor_codes']),
            cell(frame['noise_floor_dbfs']), cell(frame['crest_ratio']), cell(frame['crest_threshold']),
            cell(frame['crest_excess']), cell(frame['below_rule_support_level']),
            cell(frame['scenario_declaration_truth']), cell(frame['sidecar_matches_row'])]) + ' |')
    stale = [frame['tag'] for frame in report['frames'] if frame['declaration_is_stale']]
    mismatched = [frame['tag'] for frame in report['frames'] if frame['sidecar_matches_row'] is False]
    if stale:
        lines += ['', 'stale-by-design 声明（off 帧沿用上次加载场景，非本次发射真值）：' + ', '.join(stale)]
    if mismatched:
        lines += ['', 'sidecar 与 result.json 行不一致（本表只如实标注，不做取舍或改写）：'
                  + ', '.join(mismatched)]
    lines += ['', '同批噪声底（按会话）：', '',
              '| 会话 | off 帧 | 噪声底(codes) | 噪声底(dBFS) | physical |', '|---|---|---|---|---|']
    for floor in report['session_noise_floor']:
        lines.append('| %s | %s | %s | %s | %s |' % (cell(floor['session_index']), cell(floor['source_tag']),
                                                     cell(floor['rms_codes']), cell(floor['dbfs']),
                                                     cell(floor['physical'])))
    return chr(10).join(lines) + chr(10)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', required=True, type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--json', type=Path)
    parser.add_argument('--markdown', type=Path)
    args = parser.parse_args(argv)
    report = build_report(args.pilot, baseline=args.baseline, replay=args.replay)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding='utf-8')
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('frames', 'session_noise_floor')}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
