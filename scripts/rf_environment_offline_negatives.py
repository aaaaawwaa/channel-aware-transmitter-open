"""N1..N12 offline negative controls for the frozen S2 environment contract.

Task 2.2 of `docs/superpowers/plans/2026-10-13-final-prj-implementation-plan.md` and
section 2.1 of the frozen contract
`docs/superpowers/specs/2026-10-14-s2-environment-diagnosis-contract-freeze.md`.

Pure offline mathematical replay: no board, no network, no RF, no new physical frame, no
authorization consumed.  Every verdict in the receipt is produced by the EXISTING
production code -- this module injects one declared defect per case into an otherwise
valid formal record built from a sealed real IQ frame, then records the raw
label/reason/exception those functions return.  It never re-implements a threshold, a
label rule or a named reason:

* `scripts/rf_environment_acceptance.validate_frame` / `campaign` -- formal record gate
  and the duplicate-identity rule of the 108-frame campaign;
* `pynq_overlay/sw/rf_environment.analyze_environment` -- the window diagnosis;
* `pynq_overlay/sw/rf_bpsk_pipeline.analyze_demod` + `rf_bpsk_receiver.receive_bpsk` --
  the frozen BPSK/BER engine (including its reference refusal);
* `pynq_overlay/sw/rf_rx_analysis.snapshot_is_fresh` + `ui_model` snapshot/quality
  mapping -- the 30 s IQ-snapshot window;
* `pynq_overlay/sw/rf_environment.EnvironmentBaseline.from_dict` -- baseline tamper
  detection.

Baseline semantics (2026-10-17, M2 bootstrap): the product baseline file is the 46 dB
**provisional development baseline** -- one real 46 dB normal frame, n=1,
not_for_formal_acceptance (source frame IQ sha256 is pinned below).  The frozen baseline
schema admits exactly the eight body keys plus sha256, so those provisional labels live in
the evidence record, not in the file; `load_baseline` re-asserts the provenance and
refuses to run against anything that is not either that exact provisional baseline or a
`frozen` formal one.  The campaign itself is unchanged and stays fail-closed: it must
still refuse/named-reject all twelve cases and never judge one CLEAR.

Data source: the sealed real S2 development-pilot record
`evidence/rf-teaching/s2/board-pilot-2026-10-05-v2/s00-normal-00.*` (read-only).  Four
cases use a payload that is declared as constructed (never as real RF) in
`data_sources['constructed']` and in the evidence NOTES: N4/N9/N12 are declared rewrites
of that sealed frame, and only N11 is produced by the production scenario generator
(`kind='compound'`).  The declared injections live in parameters, never in private
thresholds.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import rf_environment_acceptance as ACC          # noqa: E402
from scripts.rf_bpsk_replay import frame_from_metadata        # noqa: E402
import rf_bpsk_pipeline as PIPE                               # noqa: E402
import rf_bpsk_receiver as BPSK                               # noqa: E402
import rf_environment as ENV                                  # noqa: E402
import rf_experiment_scenarios as SCEN                        # noqa: E402
import rf_rx_analysis as RXA                                  # noqa: E402
import ui_model as UI                                         # noqa: E402

BASELINE_PATH = ROOT / 'pynq_overlay' / 'sw' / 'rf_environment_baseline.json'
# The 46 dB provisional development bootstrap delivered by M2
# (evidence/rf-teaching/s2-46db-m2-bootstrap-2026-10-17): one real 46 dB normal frame
# from the HEADROOM research attempt-2 record, n=1, not_for_formal_acceptance.  It lets a
# 46 dB development candidate load the environment engine; it is NOT a formal acceptance
# baseline -- that one requires a real 46 dB TRAIN campaign and a re-signed declaration.
PROVISIONAL_CANONICAL_SHA256 = '6b697e0e63f7ab4fd1cd06da324128ebb625dbc81c4acf29048014c83549d6bf'
PROVISIONAL_SOURCE_FRAME_IQ_SHA256 = (
    'd435eea79ed64d56ee62f8fd38f95f6487722e89a2fa25872fafaf73008e302a')
PROVISIONAL_ANNOTATION = dict(provisional=True, n=1, not_for_formal_acceptance=True,
                              formal_train_val=False, test_frames=0)
EVIDENCE_DIR = ROOT / 'evidence' / 'rf-teaching' / 's2' / 'board-pilot-2026-10-05-v2'
PRIMARY_TAG = 's00-normal-00'
SOFTWARE_NAMES = ('rf_environment.py', 'rf_environment_baseline.json',
                  'rf_experiment_scenarios.py', 'rf_status_worker.py',
                  'ui_model.py', 'ui_view.py')
SCENE_KINDS = ('normal', 'narrowband', 'pulse', 'compound')
SNAPSHOT_AGE_STALE_S = 30.5      # strictly beyond rf_rx_analysis.IQ_SNAPSHOT_FRESHNESS_S
SNAPSHOT_AGE_FRESH_S = 5.0       # inside the same window
# Constructed compound negative control: inside the declared peak bound
# (amplitude+tone+pulse = 100 = physical_max_code) but stronger than the frozen
# compound scene declaration (60/15/15).  See NOTES for why the declared scene cannot
# reach COMPOUND_RULES at all.
COMPOUND_CONSTRUCTED = dict(amplitude=40, tone_amplitude=30, pulse_amplitude=30,
                            prbs7_seed=1)
CASE_IDS = ('N1', 'N2', 'N3', 'N4', 'N5', 'N6', 'N7', 'N8', 'N9', 'N10', 'N11', 'N12')
CASE_EXPECTED = {
    'N1': '拒绝（身份不符：非冻结 release_id）',
    'N2': '拒绝（STALE，超出 30 s 快照新鲜度）',
    'N3': '拒绝（identity mismatch）',
    'N4': '拒绝（CLIPPED），不得判 CLEAR',
    'N5': 'INVALID（域外采样参数：非 60 MSPS/decim1/4096）',
    'N6': '拒绝 BER，标签不得升级为 CLEAR',
    'N7': '拒绝（IQ 摘要命中 calibration_iq_sha256）',
    'N8': '拒绝（duplicate identity/IQ）',
    'N9': 'INVALID/LOW_ENERGY',
    'N10': '拒绝（baseline_sha256 不符）',
    'N11': 'UNKNOWN/COMPOUND_RULES，不得择一标签',
    'N12': '拒绝（截断 payload：长度/身份）',
}
SCOPE = ('offline mathematical replay of one sealed real IQ frame plus declared '
         'constructed frames; no board, no network, no RF, no new physical frame, '
         'no authorization consumed')


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path):
    return _sha256(Path(path).read_bytes())


def _json_normal(value):
    """Round-trip through JSON so features/rules become plain JSON containers."""
    return json.loads(json.dumps(value, allow_nan=False))


def _relative(path):
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def load_baseline(path=BASELINE_PATH):
    """Parse the frozen baseline with the production loader; also keep the raw file.

    Fail closed on provenance: either the exact 46 dB provisional development bootstrap
    (asserted here: calibration_status=development, exactly one source, that source is
    the pinned real 46 dB frame) or a genuinely frozen baseline.  A silently different
    development baseline, or a provisional file that has drifted, refuses by name.
    """
    path = Path(path)
    value = json.loads(path.read_text(encoding='utf-8'))
    baseline = ENV.EnvironmentBaseline.from_dict(value)   # production tamper check
    provisional = baseline.sha256 == PROVISIONAL_CANONICAL_SHA256
    if provisional:
        if (baseline.calibration_status != 'development' or len(baseline.sources) != 1
                or dict(baseline.sources[0]).get('iq_sha256')
                != PROVISIONAL_SOURCE_FRAME_IQ_SHA256):
            raise ValueError('provisional baseline provenance changed')
    elif baseline.calibration_status != 'frozen':
        raise ValueError('only a frozen formal baseline may replace the provisional bootstrap')
    info = dict(path=_relative(path), file_sha256=_file_sha256(path),
                canonical_sha256=baseline.sha256,
                environment_version=baseline.environment_version,
                algorithm_version=baseline.algorithm_version,
                calibration_status=baseline.calibration_status,
                source_count=len(baseline.sources),
                provisional=provisional,
                formal_acceptance_baseline=not provisional,
                n=1 if provisional else len(baseline.sources),
                not_for_formal_acceptance=provisional,
                source_frame_sha256=(PROVISIONAL_SOURCE_FRAME_IQ_SHA256
                                     if provisional else None))
    return baseline, value, info


def build_freeze(baseline, release):
    """Assemble the frozen TEST declaration and validate it with production code.

    The six software SHA-256 values are recomputed from this workspace (contract
    section 2.2: never reuse the source project's digests).
    """
    software = {name: _file_sha256(ROOT / 'pynq_overlay' / 'sw' / name)
                for name in SOFTWARE_NAMES}
    freeze = dict(
        expected_release=release, environment_version=ENV.VERSION,
        baseline_sha256=baseline.sha256, algorithm_version=BPSK.VERSION,
        fixed_rf=dict(ACC.FIXED_RF), software=software,
        scene_declarations={kind: SCEN.generate_scenario(
            SCEN.ScenarioConfig(kind=kind)).declaration_dict() for kind in SCENE_KINDS},
        rx_config=dict(ACC.FIXED_RX), reference_seeds=[1, 37, 73],
        actual_scene_prbs7_seed=1, split='test', expected_frames=108,
        calibration_iq_sha256=[])
    freeze, freeze_sha256 = ACC.validate_freeze(freeze, release)
    return freeze, freeze_sha256


def sealed_inputs(evidence_dir=EVIDENCE_DIR, tag=PRIMARY_TAG):
    """Read the sealed real record (read-only) and list its digests for the receipt."""
    directory = Path(evidence_dir)
    paths = dict(iq=directory / (tag + '.iq'), metadata=directory / (tag + '.json'),
                 sidecar=directory / (tag + '.environment.json'),
                 result=directory / 'result.json',
                 declaration=directory / 'declaration.json')
    values = dict(tag=tag,
                  raw=paths['iq'].read_bytes(),
                  meta=json.loads(paths['metadata'].read_text(encoding='utf-8')),
                  side=json.loads(paths['sidecar'].read_text(encoding='utf-8')),
                  digests={name: _file_sha256(path) for name, path in paths.items()},
                  paths={name: _relative(path) for name, path in paths.items()})
    declaration = json.loads(paths['declaration'].read_text(encoding='utf-8'))
    values['release'] = declaration['expected_release']
    if (values['meta']['release_id'] != values['release']
            or values['side']['release_id'] != values['release']):
        raise ValueError('sealed record release disagrees with its declaration')
    if len(values['raw']) != 16384:
        raise ValueError('sealed record is not the frozen 4096-sample frame')
    return values


def replay_payload(raw, meta_source, baseline, rf_config=None):
    """Replay one payload through the production chain; returns the raw production outputs."""
    frame = frame_from_metadata(raw, meta_source)
    submitted = meta_source['demod_result']['submitted']
    config = PIPE.DemodConfig(BPSK.BpskConfig(**submitted['config']),
                              submitted['revision'], submitted['decim'],
                              submitted['rx_len'], submitted['input_fs_hz'])
    envelope = PIPE.analyze_demod(frame, config)
    environment = ENV.analyze_environment(frame, envelope,
                                          dict(rf_config if rf_config else ENV.FIXED_RF),
                                          baseline)
    return frame, envelope, environment


def _meta_with(meta, **updates):
    """Copy the sealed metadata document and patch the requested nested fields."""
    result = copy.deepcopy(meta)
    for key, value in updates.items():
        result[key] = value
    return result


class Record:
    """One formal-record candidate in the exact shape the production campaign persists.

    Built only from production outputs: the frame from `frame_from_metadata`, the demod
    block from `dataclasses.asdict(analyze_demod(...))` and the environment block from
    `EnvironmentResult.to_dict()`.  Nothing here recomputes a label or a threshold.
    """

    def __init__(self, output, tag, kind, release, raw, frame, envelope, environment,
                 freeze, declared_kind=None, seed=1):
        self.output = Path(output)
        self.tag = tag
        self.kind = kind
        self.release = release
        self.seed = seed
        self.raw = raw
        self.frame = frame
        self.envelope = envelope
        self.environment = environment
        self.declared_kind = declared_kind or ('normal' if kind == 'domain' else
                                               'compound' if kind == 'off' else kind)
        self.input_sha256 = _sha256(raw)
        frame_dict = _json_normal(frame.to_dict())
        environment_block = _json_normal(environment.to_dict())
        physical = kind != 'off'
        expected_op = 'loopback.physical.capture' if physical else 'rx.capture'
        if frame_dict['op'] != expected_op or frame_dict['physical'] is not physical:
            raise ValueError('sealed frame op/physical disagrees with the formal kind')
        self.meta = dict(frame_dict)
        self.meta.update(
            format='s16_iq_le', iq_sha256=self.input_sha256,
            pre_readback=frame.pre_readback.to_dict(),
            post_readback=frame.post_readback.to_dict(),
            handoff_frame=copy.deepcopy(frame_dict), handoff_frame_matches=True,
            demod_result=_json_normal(dataclasses.asdict(envelope)),
            capture_status=dict(state='done', physical=physical,
                                capture_id=frame_dict['capture_id'], code=None,
                                reason='', close_confirmed=True, cancelled=False,
                                seq=3),
            display={}, ui_events=[],
            scope='offline negative control; constructed formal record')
        display = dict(available=True, current=True,
                       **{name: environment_block[name] for name in
                          ('capture_id', 'label', 'reason', 'features',
                           'baseline_sha256')})
        for name in ACC.IDENTITY:
            display[name] = environment_block[name]
        self.side = dict(environment=environment_block, display_environment=display,
                         rf_config=dict(freeze['fixed_rf']),
                         scenario_declaration=copy.deepcopy(
                             freeze['scene_declarations'][self.declared_kind]),
                         record=None, iq_sha256=self.input_sha256,
                         capture_id=frame_dict['capture_id'], release_id=release)
        self.row = dict(on=physical, egress_safe=True,
                        capture_id=frame_dict['capture_id'], release_id=release,
                        iq_sha256=self.input_sha256, iq_file=tag + '.iq',
                        metadata_file=tag + '.json',
                        environment_file=tag + '.environment.json',
                        ui_events=[], tag=tag, truth=kind, session_index=0,
                        split='test', reference_seed=seed,
                        actual_scene_prbs7_seed=1,
                        environment=environment_block,
                        result=dict(reference_sync=envelope.result.reference_sync,
                                    bit_errors=envelope.result.bit_errors,
                                    compared_bits=envelope.result.compared_bits,
                                    ber=envelope.result.ber,
                                    reason=envelope.result.reason))
        for name in ACC.IDENTITY:
            self.row[name] = frame_dict[name]

    # -- declared injections (each one is named in the case entry) -------------------
    def patch_release(self, release):
        self.release = release
        for target in (self.meta, self.meta['handoff_frame'], self.side,
                       self.side['environment'], self.side['display_environment'],
                       self.row):
            target['release_id'] = release
        return self

    def patch_environment(self, **fields):
        self.side['environment'].update(fields)
        self.side['display_environment'].update(fields)
        return self

    def write(self, output=None, iq_bytes=None):
        out = Path(output or self.output)
        out.mkdir(parents=True, exist_ok=True)
        payload = self.raw if iq_bytes is None else iq_bytes
        (out / (self.tag + '.iq')).write_bytes(payload)
        (out / (self.tag + '.json')).write_text(
            json.dumps(self.meta, indent=2, allow_nan=False), encoding='utf-8')
        (out / (self.tag + '.environment.json')).write_text(
            json.dumps(self.side, indent=2, allow_nan=False), encoding='utf-8')
        return dict(output=_relative(out), iq_bytes=len(payload),
                    file_sha256=_sha256(payload), declared_sha256=self.input_sha256)


def _record(ctx, raw, meta_source, kind='normal', declared_kind=None, release=None,
            rf_config=None, tag=None, frame=None, envelope=None, environment=None):
    """Build a formal record; production replay unless the pieces are supplied."""
    tag = tag or ctx['tag']
    release = ctx['release'] if release is None else release
    if frame is None:
        frame, envelope, environment = replay_payload(raw, meta_source,
                                                      ctx['baseline'], rf_config)
    return Record(ctx['workdir'], tag, kind, release, raw, frame, envelope, environment,
                  ctx['freeze'], declared_kind, ctx['seed'])


def _gate(ctx, record, kind=None, freeze=None):
    """Run the production formal-record gate on one written record."""
    kind = record.kind if kind is None else kind
    try:
        identity, digest = ACC.validate_frame(record.row, record.output,
                                              freeze or ctx['freeze'], kind, record.seed)
    except ValueError as exc:
        return dict(name='formal_record_gate',
                    production='scripts.rf_environment_acceptance.validate_frame',
                    accepted=False, outcome=str(exc))
    return dict(name='formal_record_gate',
                production='scripts.rf_environment_acceptance.validate_frame',
                accepted=True, outcome='ACCEPTED', identity=list(identity),
                iq_sha256=digest)


def _diagnosis(environment):
    return dict(label=environment.label, reason=environment.reason,
                environment_version=environment.environment_version,
                algorithm_version=environment.algorithm_version,
                baseline_sha256=environment.baseline_sha256,
                submitted_revision=environment.submitted_revision)


def _note(case, key, value):
    case.setdefault('notes', {})[key] = value
    return case


def _entry(ctx, cid, injection, checks, verdict_kind, verdict_reason, expectation_met,
           diagnosis=None, input_info=None):
    """Assemble one case entry.

    `cleared`/`passed` are settled by `run()` once the case's final production diagnosis
    is known, so the adjudication can never be computed against a different label than
    the one the receipt reports.
    """
    return dict(id=cid, injection=injection, expected=CASE_EXPECTED[cid],
                verdict_kind=verdict_kind, verdict_reason=verdict_reason,
                diagnosis=(_diagnosis(ctx['environment']) if diagnosis is None
                           else diagnosis),
                checks=list(checks),
                input=input_info or dict(tag=ctx['tag'], iq_sha256=ctx['raw_sha256'],
                                         iq_bytes=len(ctx['raw']),
                                         source='sealed real IQ (read-only)'),
                expectation=bool(expectation_met))


def _base(ctx):
    """Fresh control record from the sealed frame (re-replayed per case)."""
    record = _record(ctx, ctx['raw'], ctx['meta'])
    record.write()
    return record


# ---------------------------------------------------------------------------------
# N1..N12
# ---------------------------------------------------------------------------------
def _n1(ctx):
    """N1: a non-frozen release_id in the record identity."""
    foreign = 'f' * 32
    record = _base(ctx).patch_release(foreign)
    record.write()
    gate = _gate(ctx, record)
    case = _entry(ctx, 'N1',
                  "record release_id replaced everywhere (row/meta/handoff/sidecar/"
                  "environment/display) by '%s', which is not the frozen release %s"
                  % (foreign, ctx['release']),
                  [gate], 'REJECTED', gate['outcome'],
                  expectation_met=(gate['accepted'] is False
                                   and 'foreign diagnostic identity' in gate['outcome']))
    case['diagnosis'] = _diagnosis(record.environment)
    _note(case, 'injected_field', 'release_id (row + metadata + handoff_frame + sidecar + '
                                  'environment + display)')
    return case


def _n2(ctx):
    """N2: a snapshot older than the 30 s window (production freshness rule)."""
    moment = ctx['frame'].completed_at_monotonic
    window = RXA.IQ_SNAPSHOT_FRESHNESS_S
    fresh_inside = RXA.snapshot_is_fresh(moment, moment + SNAPSHOT_AGE_FRESH_S)
    fresh_boundary = RXA.snapshot_is_fresh(moment, moment + window)
    fresh_beyond = RXA.snapshot_is_fresh(moment, moment + SNAPSHOT_AGE_STALE_S)
    freshness = dict(name='snapshot_freshness_window',
                     production=['rf_rx_analysis.IQ_SNAPSHOT_FRESHNESS_S',
                                 'rf_rx_analysis.snapshot_is_fresh'],
                     accepted=bool(fresh_beyond),
                     outcome=('age %.1f s -> fresh=%s; age %.1f s (boundary) -> fresh=%s; '
                              'age %.1f s -> fresh=%s'
                              % (SNAPSHOT_AGE_FRESH_S, fresh_inside, window,
                                 fresh_boundary, SNAPSHOT_AGE_STALE_S, fresh_beyond)))
    analysis = RXA.summarize(ctx['frame'])
    stale_moment = time.monotonic() - SNAPSHOT_AGE_STALE_S
    frame_view = dict(ctx['frame'].to_dict(), completed_at_monotonic=stale_moment)
    snapshot = UI.rx_snapshot_from_capture(frame_view, analysis)
    display = dict(name='stale_snapshot_mapping',
                   production='ui_model.rx_snapshot_from_capture',
                   accepted=bool(not (snapshot.stale and snapshot.expired)),
                   outcome=('stale=%s expired=%s valid=%s error=%r'
                            % (snapshot.stale, snapshot.expired, snapshot.valid,
                               snapshot.error)))
    app = SimpleNamespace(rx_capture=dict(state='done'), rx_snapshot=snapshot,
                          rx_analysis=dict(completed_at_monotonic=stale_moment))
    reasons = UI.rx_quality_reasons(app, time.monotonic())
    codes = [reason['code'] for reason in reasons]
    quality = dict(name='stale_quality_reason',
                   production='ui_model.rx_quality_reasons',
                   accepted=bool(UI.RX_QUALITY_EXPIRED not in codes),
                   outcome='reason codes: %s' % (codes,))
    case = _entry(ctx, 'N2',
                  'snapshot completion moment moved beyond the frozen 30 s IQ window '
                  '(%.1f s old); the formal record gate has no freshness field, so the '
                  'refusal must come from the production session/UI freshness rule'
                  % SNAPSHOT_AGE_STALE_S,
                  [freshness, display, quality], 'STALE',
                  'STALE: %s; %s' % (quality['outcome'], display['outcome']),
                  expectation_met=(fresh_inside is True and fresh_boundary is True
                                   and fresh_beyond is False
                                   and snapshot.stale is True and snapshot.expired is True
                                   and UI.RX_QUALITY_EXPIRED in codes))
    case['diagnosis'] = _diagnosis(ctx['environment'])
    _note(case, 'gate_without_freshness_rule',
          'scripts/rf_environment_acceptance.validate_frame has no staleness field; '
          'staleness is refused by rf_rx_analysis/ui_model instead')
    return case


def _n3(ctx):
    """N3: connection generation / config revision not the ones in force."""
    bumped = _meta_with(ctx['meta'], connection_generation=ctx['frame'].connection_generation + 1)
    frame, _envelope, _environment = replay_payload(ctx['raw'], bumped, ctx['baseline'])
    environment = ENV.analyze_environment(frame, ctx['envelope'],
                                          dict(ENV.FIXED_RF), ctx['baseline'])
    analysis = dict(name='diagnosis_identity_mismatch',
                    production='rf_environment.analyze_environment',
                    accepted=bool(environment.label == 'CLEAR'),
                    outcome='%s/%s' % (environment.label, environment.reason))
    record = _base(ctx)
    record.row['config_revision'] = record.row['config_revision'] + 1
    record.write()
    gate = _gate(ctx, record)
    case = _entry(ctx, 'N3',
                  'diagnosis computed for connection_generation+1 while the record still '
                  'declares the original generation, and the record revision is +1 over '
                  'the environment result it carries',
                  [analysis, gate], 'REJECTED',
                  '%s; gate: %s' % (environment.reason, gate['outcome']),
                  expectation_met=(environment.label == 'INVALID'
                                   and environment.reason == 'IDENTITY_MISMATCH'
                                   and gate['accepted'] is False))
    case['diagnosis'] = _diagnosis(environment)
    return case


def _n4(ctx):
    """N4: a code-rail clipped frame."""
    iq = np.frombuffer(ctx['raw'], dtype='<i2').copy()
    iq[0] = 2047
    raw = iq.tobytes()
    record = _record(ctx, raw, ctx['meta'])
    record.write()
    gate = _gate(ctx, record)
    case = _entry(ctx, 'N4',
                  'first IQ sample driven to the +2047 code rail (a code-rail CLIPPED '
                  'event); original frame otherwise unchanged',
                  [gate], 'REJECTED', 'CLIPPED',
                  expectation_met=(record.environment.label == 'INVALID'
                                   and record.environment.reason == 'CLIPPED'
                                   and gate['accepted'] is False),
                  input_info=dict(tag=ctx['tag'], iq_sha256=record.input_sha256,
                                  iq_bytes=len(raw),
                                  source='sealed real IQ with one declared rail injection'))
    case['diagnosis'] = _diagnosis(record.environment)
    return case


def _n5(ctx):
    """N5: sampling parameters outside the frozen 60 MSPS / decim 1 / 4096 domain."""
    meta = _meta_with(ctx['meta'], ratio=1, decimation=2, output_fs_hz=30000000)
    meta['demod_result'] = copy.deepcopy(ctx['meta']['demod_result'])
    meta['demod_result']['submitted']['decim'] = 2
    record = _record(ctx, ctx['raw'], meta)
    record.write()
    gate = _gate(ctx, record)
    case = _entry(ctx, 'N5',
                  'acquisition declared as ratio=1 (decim 2, 30 MSPS output) with the '
                  'matching submitted decim, i.e. outside FIXED_RX decim=1/4096/60 MSPS',
                  [gate], 'INVALID', 'UNSUPPORTED_DOMAIN',
                  expectation_met=(record.environment.label == 'INVALID'
                                   and record.environment.reason == 'UNSUPPORTED_DOMAIN'
                                   and gate['accepted'] is False))
    case['diagnosis'] = _diagnosis(record.environment)
    return case


def _n6(ctx):
    """N6: no reference available; the production receiver must refuse a BER."""
    meta = copy.deepcopy(ctx['meta'])
    meta['demod_result']['submitted']['config']['reference_prbs7_seed'] = None
    record = _record(ctx, ctx['raw'], meta)
    record.write()
    result = record.envelope.result
    receiver = dict(name='ber_refusal_without_reference',
                    production=['rf_bpsk_pipeline.analyze_demod',
                                'rf_bpsk_receiver.receive_bpsk'],
                    accepted=bool(result.ber is not None),
                    outcome=('reason=%s reference_sync=%s ber=%s compared_bits=%s '
                             'bit_errors=%s'
                             % (result.reason, result.reference_sync, result.ber,
                                result.compared_bits, result.bit_errors)))
    gate = _gate(ctx, record)
    case = _entry(ctx, 'N6',
                  'RX submitted with the reference disabled (reference_prbs7_seed=None); '
                  'FIXED_RX of the frozen domain requires reference_enabled=True',
                  [receiver, gate], 'REJECTED',
                  'REFERENCE_NOT_PROVIDED (no BER) + frozen RX domain requires a reference',
                  expectation_met=(result.reason == 'REFERENCE_NOT_PROVIDED'
                                   and result.reference_sync is None
                                   and result.ber is None and result.compared_bits is None
                                   and gate['accepted'] is False))
    case['diagnosis'] = _diagnosis(record.environment)
    _note(case, 'analysis_label_without_reference',
          'rf_environment.analyze_environment calls the window %s/%s even though no BER '
          'was produced: its reliability test reads carrier/timing sync only.  The frozen '
          'RX domain (FIXED_RX reference_enabled=True) is what refuses this submission, so '
          'the record can never be admitted as a CLEAR formal frame; see NOTES.'
          % (record.environment.label, record.environment.reason))
    _note(case, 'prbs7_seed_shift',
          'a different PRBS7 seed is only a cyclic shift of the same m-sequence and is '
          'therefore NOT a usable "reference unavailable" injection (reference_sync stays '
          'True with ber 0.0); the injection used here is the missing reference itself')
    return case


def _n7(ctx):
    """N7: the IQ digest is in the frozen calibration/exclusion list."""
    record = _base(ctx)
    freeze, freeze_sha256 = ACC.validate_freeze(
        dict(ctx['freeze'], calibration_iq_sha256=[record.input_sha256]),
        ctx['release'])
    gate = _gate(ctx, record, freeze=freeze)
    case = _entry(ctx, 'N7',
                  'the sealed frame IQ digest %s is declared in the freeze field '
                  'calibration_iq_sha256, i.e. it belongs to the calibration/TRAIN '
                  'exclusion set' % record.input_sha256,
                  [gate], 'REJECTED', gate['outcome'],
                  expectation_met=(gate['accepted'] is False
                                   and 'foreign diagnostic identity' in gate['outcome']))
    case['diagnosis'] = _diagnosis(record.environment)
    _note(case, 'freeze_sha256_with_exclusion', freeze_sha256)
    _note(case, 'injected_field', 'iq_sha256 listed in freeze.calibration_iq_sha256 '
                                  '(the formal gate refuses a digest in the exclusion set)')
    return case


class _SealedReplaySession:
    """Offline stand-in for the board session used by the production campaign.

    No client, socket, Overlay or capture is constructed.  `campaign` supplies every tag
    and performs every validation itself; this stand-in answers with the same sealed
    record twice, which is the duplicate-identity injection.
    """

    def __init__(self, output, release, record):
        self.output = Path(output)
        self.release = release
        self.record = record
        self.captures = 0
        self.seed = None

    def initial(self):
        return dict(safe=True, release_id=self.release)

    def setup(self, seed):
        self.seed = seed

    def scene(self, kind):
        pass

    def domain(self, outside):
        pass

    def cleanup(self):
        return dict(safe=True, disarm_ack=True)

    def close(self):
        pass

    def capture(self, tag, physical):
        self.captures += 1
        row = copy.deepcopy(self.record.row)
        for suffix in ('.iq', '.json', '.environment.json'):
            (self.output / (tag + suffix)).write_bytes(
                (self.output / (self.record.tag + suffix)).read_bytes())
        row.update(iq_file=tag + '.iq', metadata_file=tag + '.json',
                   environment_file=tag + '.environment.json', tag=tag)
        return row


def _n8(ctx):
    """N8: duplicate identity / duplicate IQ digest inside the formal campaign."""
    output = Path(ctx['workdir']) / 'n8-campaign'
    record = _record(ctx, ctx['raw'], ctx['meta'], tag='s00-normal-00')
    record.write(output=output)
    session = _SealedReplaySession(output, ctx['release'], record)
    result = ACC.campaign(lambda: session, output, ctx['release'], ctx['freeze'])
    error = str((result['sessions'][0] or {}).get('error'))
    campaign = dict(name='duplicate_rejection',
                    production='scripts.rf_environment_acceptance.campaign',
                    accepted=bool('duplicate' not in error),
                    outcome=('stop_cause=%s sessions[0].error=%r captures=%d records=%d'
                             % (result['stop_cause'], error, session.captures,
                                len(result['records']))))
    case = _entry(ctx, 'N8',
                  'the same sealed capture (identical five-field identity and identical '
                  'IQ digest) is offered twice to the production campaign through an '
                  'offline session stand-in',
                  [campaign], 'REJECTED', error or result['stop_cause'],
                  expectation_met=('duplicate formal capture identity or IQ' in error
                                   and result['formal_rf_passed'] is False
                                   and len(result['records']) == 2
                                   and session.captures == 2))
    case['diagnosis'] = _diagnosis(record.environment)
    _note(case, 'campaign_passed', result['formal_rf_passed'])
    _note(case, 'duplicate_frame_retained',
          'the refused duplicate stays in result["records"] (the contract forbids '
          'dropping a returned frame); the refusal is the campaign stop, not a silent drop')
    return case


def _n9(ctx):
    """N9: a low-energy (non-usable) frame below the recorded energy floor."""
    iq = np.frombuffer(ctx['raw'], dtype='<i2')
    scaled = np.rint(iq.astype(np.int32) / 64.0).astype('<i2').tobytes()
    record = _record(ctx, scaled, ctx['meta'])
    record.write()
    gate = _gate(ctx, record)
    case = _entry(ctx, 'N9',
                  'sealed frame payload divided by 64 with numpy.rint (integer codes, '
                  'no clipping): a low-energy frame far below minimum_rms_codes=16.0',
                  [gate], 'INVALID', record.environment.reason,
                  expectation_met=(record.environment.label == 'INVALID'
                                   and record.environment.reason == 'LOW_ENERGY'),
                  input_info=dict(tag=ctx['tag'], iq_sha256=record.input_sha256,
                                  iq_bytes=len(scaled),
                                  source='sealed real IQ scaled by 1/64 (declared)'),
                  diagnosis=_diagnosis(record.environment))
    case['diagnosis'] = _diagnosis(record.environment)
    _note(case, 'gate_accepted_because_low_energy_is_a_named_diagnosis',
          'the formal gate admits any of the five labels for a known kind; the refusal '
          'here is the diagnosis itself (INVALID/LOW_ENERGY), which is never CLEAR')
    return case


def _n10(ctx):
    """N10: a wrong baseline_sha256 (declaration) and a tampered baseline file."""
    record = _base(ctx).patch_environment(baseline_sha256='0' * 64)
    record.write()
    gate = _gate(ctx, record)
    tampered = dict(ctx['baseline_value'], sha256='0' * 64)
    loader = dict(name='baseline_tamper_detection',
                  production='rf_environment.EnvironmentBaseline.from_dict',
                  accepted=False, outcome=None)
    try:
        ENV.EnvironmentBaseline.from_dict(tampered)
        loader['accepted'] = True
        loader['outcome'] = 'ACCEPTED (tamper not detected)'
    except ValueError as exc:
        loader['outcome'] = str(exc)
        loader['accepted'] = 'baseline canonical SHA mismatch' not in str(exc)
    case = _entry(ctx, 'N10',
                  "the record's environment block declares baseline_sha256='0'*64 "
                  "(wrong frozen baseline) and a baseline file whose stored sha256 was "
                  'tampered with is re-parsed',
                  [gate, loader], 'REJECTED',
                  '%s; loader: %s' % (gate['outcome'], loader['outcome']),
                  expectation_met=(gate['accepted'] is False
                                   and 'foreign diagnostic identity' in gate['outcome']
                                   and loader['outcome'] == 'baseline canonical SHA mismatch'))
    case['diagnosis'] = _diagnosis(record.environment)
    _note(case, 'injected_field', "environment.baseline_sha256 = '0'*64 in the record and "
                                  "the baseline file's stored sha256 = '0'*64")
    return case


def _n11(ctx):
    """N11: a compound frame (narrowband + pulse at once) must not pick one label."""
    scenario = SCEN.generate_scenario(SCEN.ScenarioConfig(kind='compound',
                                                          **COMPOUND_CONSTRUCTED))
    period = np.frombuffer(scenario.payload, dtype='<i2').reshape(-1, 2)
    repeats = int(math.ceil(4096 / period.shape[0]))
    window = np.tile(period, (repeats, 1))[:4096]
    raw = window.astype('<i2').tobytes()
    record = _record(ctx, raw, ctx['meta'], kind='compound', declared_kind='compound')
    record.write()
    gate = _gate(ctx, record)
    rules = dict(record.environment.rule_evidence)
    case = _entry(ctx, 'N11',
                  'compound frame built with the production scenario generator '
                  '(kind=compound, amplitude=40, tone=30, pulse=30, prbs7_seed=1, period '
                  '2540, tiled to the 4096-sample window like the wrapped TX period); it '
                  'is NOT a real RF frame',
                  [gate], 'UNKNOWN', record.environment.reason,
                  expectation_met=(record.environment.label == 'UNKNOWN'
                                   and record.environment.reason == 'COMPOUND_RULES'
                                   and rules.get('narrowband_triggered') is True
                                   and rules.get('pulse_triggered') is True),
                  input_info=dict(tag=ctx['tag'], iq_sha256=record.input_sha256,
                                  iq_bytes=len(raw),
                                  source='constructed compound frame (declared)'),
                  diagnosis=_diagnosis(record.environment))
    case['diagnosis'] = _diagnosis(record.environment)
    _note(case, 'rule_evidence', dict(narrowband_triggered=rules.get('narrowband_triggered'),
                                      pulse_triggered=rules.get('pulse_triggered'),
                                      pulse_local_count=rules.get('pulse_local_count'),
                                      pulse_local_density=rules.get('pulse_local_density')))
    _note(case, 'declared_scene_cannot_reach_compound_rules',
          'within the frozen compound scene declaration (amplitude 60, tone+pulse<=40) no '
          'grid point triggers both rules; see NOTES for the scan')
    return case


def _n12(ctx):
    """N12: a truncated payload artifact."""
    record = _base(ctx)
    truncated = ctx['raw'][:-4]
    written = record.write(iq_bytes=truncated)
    gate = _gate(ctx, record)
    frame_check = dict(name='frame_length_refusal',
                       production='scripts.rf_bpsk_replay.frame_from_metadata',
                       accepted=False, outcome=None)
    try:
        frame_from_metadata(truncated, ctx['meta'])
        frame_check['accepted'] = True
        frame_check['outcome'] = 'ACCEPTED (truncated payload not detected)'
    except ValueError as exc:
        frame_check['outcome'] = str(exc)
    case = _entry(ctx, 'N12',
                  'the <tag>.iq artifact is written with the last 4 payload bytes missing '
                  '(16380 of the declared 16384) while the metadata still declares the '
                  'complete sealed frame',
                  [gate, frame_check], 'REJECTED',
                  '%s; frame: %s' % (gate['outcome'], frame_check['outcome']),
                  expectation_met=(gate['accepted'] is False
                                   and 'IQ artifact outside bounds' in gate['outcome']
                                   and frame_check['outcome'].startswith(
                                       'an IQ frame payload is exactly samples*4 bytes')),
                  input_info=dict(tag=ctx['tag'], iq_sha256=written['file_sha256'],
                                  iq_bytes=written['iq_bytes'],
                                  source='sealed real IQ truncated by 4 bytes (declared)'))
    case['diagnosis'] = _diagnosis(record.environment)
    _note(case, 'declared_sha256', record.input_sha256)
    return case


_CASE_FUNCS = {'N1': _n1, 'N2': _n2, 'N3': _n3, 'N4': _n4, 'N5': _n5, 'N6': _n6,
               'N7': _n7, 'N8': _n8, 'N9': _n9, 'N10': _n10, 'N11': _n11, 'N12': _n12}


def run(evidence_dir=EVIDENCE_DIR, baseline_path=BASELINE_PATH, tag=PRIMARY_TAG,
        workdir=None, generated_utc=None):
    """Execute N1..N12 and return the JSON-able receipt."""
    baseline, baseline_value, baseline_info = load_baseline(baseline_path)
    sealed = sealed_inputs(evidence_dir, tag)
    freeze, freeze_sha256 = build_freeze(baseline, sealed['release'])
    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix='rf-offline-neg-'))
    workdir.mkdir(parents=True, exist_ok=True)
    frame, envelope, environment = replay_payload(sealed['raw'], sealed['meta'], baseline)
    ctx = dict(baseline=baseline, baseline_value=baseline_value, baseline_info=baseline_info,
               freeze=freeze, freeze_sha256=freeze_sha256, release=sealed['release'],
               tag=sealed['tag'], raw=sealed['raw'], meta=sealed['meta'], side=sealed['side'],
               raw_sha256=_sha256(sealed['raw']), frame=frame, envelope=envelope,
               environment=environment, workdir=workdir, seed=1)
    control_record = _record(ctx, sealed['raw'], sealed['meta'])
    control_record.write()
    control_gate = _gate(ctx, control_record)
    control_diagnosis = _diagnosis(control_record.environment)
    control = dict(kind='control', injection='none: the sealed frame as captured',
                   diagnosis=control_diagnosis, checks=[control_gate],
                   accepted=bool(control_gate['accepted']),
                   cleared=bool(control_gate['accepted']
                                and control_diagnosis['label'] == 'CLEAR'),
                   input=dict(tag=sealed['tag'], iq_sha256=ctx['raw_sha256'],
                              iq_bytes=len(sealed['raw']),
                              source='sealed real IQ (read-only)'))
    control['passed'] = bool(control['accepted'] and control['cleared'])
    cases = []
    for cid in CASE_IDS:
        case = _CASE_FUNCS[cid](ctx)
        if 'diagnosis' not in case:
            case['diagnosis'] = _diagnosis(ctx['environment'])
        _note(case, 'control_reference',
              'the same sealed frame without the declared injection is ACCEPTED by the '
              'formal record gate and diagnosed CLEAR (receipt.control), so the refusal is '
              'attributable to the declared injection and not to the harness')
        accepted = all(check['accepted'] for check in case['checks'])
        case['adjudication_accepted'] = bool(accepted)
        case['cleared'] = bool(accepted and case['diagnosis']['label'] == 'CLEAR')
        case['rejected'] = not case['cleared']
        case['expectation'] = bool(case['expectation'] and not case['cleared'])
        case['expectation_met'] = case['expectation']
        case['passed'] = case['expectation']
        cases.append(case)
    passed = [case['id'] for case in cases if case['passed']]
    failed = [case['id'] for case in cases if not case['passed']]
    cleared = [case['id'] for case in cases if case['cleared']]
    receipt = dict(
        schema_version=1,
        generated_utc=generated_utc or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        producer=dict(module=_relative(Path(__file__)),
                      file_sha256=_file_sha256(Path(__file__))),
        contract='docs/superpowers/specs/2026-10-14-s2-environment-diagnosis-contract-freeze.md#2.1',
        scope=SCOPE,
        python=sys.version.split()[0], numpy=np.__version__,
        baseline=baseline_info, freeze=dict(sha256=freeze_sha256,
                                            expected_release=freeze['expected_release'],
                                            environment_version=freeze['environment_version'],
                                            algorithm_version=freeze['algorithm_version'],
                                            baseline_sha256=freeze['baseline_sha256'],
                                            software=freeze['software'],
                                            reference_seeds=freeze['reference_seeds'],
                                            expected_frames=freeze['expected_frames'],
                                            calibration_iq_sha256=freeze['calibration_iq_sha256']),
        sealed=dict(tag=sealed['tag'], paths=sealed['paths'], digests=sealed['digests'],
                    iq_sha256=ctx['raw_sha256'], iq_bytes=len(sealed['raw']),
                    statement='real physical development-pilot capture, read-only'),
        data_sources=dict(
            real=[dict(tag=sealed['tag'], origin=sealed['paths']['iq'],
                       iq_sha256=ctx['raw_sha256'], used_by=['control', 'N1', 'N2', 'N3',
                                                             'N4', 'N5', 'N6', 'N7',
                                                             'N8', 'N9', 'N10', 'N12'])],
            constructed=[dict(case='N4', how='one IQ sample set to the +2047 code rail'),
                         dict(case='N9', how='numpy.rint(real codes / 64)'),
                         dict(case='N11', how='production scenario generator, '
                                              'kind=compound, amplitude=40, tone=30, '
                                              'pulse=30, tiled 2540 -> 4096'),
                         dict(case='N12', how='last 4 payload bytes removed')]),
        control=control, cases=cases,
        counts=dict(cases=len(cases), passed=len(passed), failed=len(failed),
                    cleared=len(cleared)),
        passed=bool(not failed), failed_ids=failed, cleared_ids=cleared,
        workdir=_relative(workdir),
        recompute='python -B scripts/rf_environment_offline_negatives.py --output <path>',
    )
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=EVIDENCE_DIR)
    parser.add_argument('--baseline', type=Path, default=BASELINE_PATH)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--workdir', type=Path)
    parser.add_argument('--tag', default=PRIMARY_TAG)
    args = parser.parse_args(argv)
    receipt = run(args.evidence, args.baseline, args.tag, args.workdir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, allow_nan=False),
                           encoding='utf-8')
    print(json.dumps(receipt, indent=2, allow_nan=False))
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
