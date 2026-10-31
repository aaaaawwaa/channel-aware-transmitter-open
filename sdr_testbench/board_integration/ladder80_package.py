"""Offline 80 MHz 9-ON+2-OFF attenuation-ladder experiment package.

The physical 80 MHz capture entry is the installed worker path:

  scripts/rf_environment_board_session.py:EnvironmentSession
    -> scripts/rf_bpsk_ui_board_session.py:LiveUiSession
    -> pynq_overlay/sw/rf_status_worker.py:RfStatusWorker
    -> pynq_overlay/sw/rf_status_client.py:RfStatusClient.capture(physical=True)
    -> loopback.physical.capture

`wrap_80mhz_session()` attaches the strict CaptureRecorder to the actual
worker client *after* the session is constructed; `split_records()` keeps
declared context, command receipts and real readbacks separate.

No hardware is imported or touched by the offline/dry-run paths.  The ladder
factor is currently blocked: 48000/50000 mdB exceed the frozen teaching RF
domain (46000) and the physical-capture client also insists on exactly 46000.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import capture_record

SCHEMA = 'rf-46db-ladder-package-v1'
FIXED_RF_MDB = 46000
CONTROL_UPPER_MDB = 89750
LEVELS = (46000, 48000, 50000)
REPEATS = 3
ON_FRAMES = len(LEVELS) * REPEATS
OFF_FRAMES = 2
TOTAL_FRAMES = ON_FRAMES + OFF_FRAMES
SEQUENCE_SEED = 20261021
NOMINAL_SOURCE_ON_US = ON_FRAMES * 4096.0 / 60e6 * 1e6
MEASURED_SPAN_CAP_S = 1.0

BUDGET = {
    'sessions': 1,
    'arm': 1,
    'init': 1,
    'configure': 1,
    'load': 1 + TOTAL_FRAMES,       # setup load + one load per scene
    'on': ON_FRAMES,
    'off': OFF_FRAMES,
    'physical_captures': ON_FRAMES,
    'tx_starts': ON_FRAMES,         # each physical loopback capture starts TX
    'nominal_source_on_us': NOMINAL_SOURCE_ON_US,
    'measured_source_on_span_cap_s': MEASURED_SPAN_CAP_S,
}


class ExecutionBlocked(RuntimeError):
    """The package cannot execute without a new contract/baseline."""


class SequenceInvalid(ValueError):
    pass


def attenuation_compliance():
    """Per-level control/contract compliance with exact deltas and paths."""
    rows = []
    for mdb in LEVELS:
        delta = mdb - FIXED_RF_MDB
        if mdb == FIXED_RF_MDB:
            rows.append({
                'attenuation_mdb': mdb, 'delta_vs_fixed_mdb': delta,
                'control_configure': 'accepted (<= 89750)',
                'physical_capture_client': 'accepted (exact 46000)',
                'environment_domain': 'accepted (FIXED_RF)',
                'baseline': 'accepted (rf_config 46000)',
                'path': 'allowed',
            })
        else:
            rows.append({
                'attenuation_mdb': mdb, 'delta_vs_fixed_mdb': delta,
                'control_configure': 'accepted by rf.configure (<= 89750)',
                'physical_capture_client': 'REFUSED: rf_status_client.capture requires exactly 46000',
                'environment_domain': 'REFUSED: rf_environment._domain_error -> RF_DOMAIN_MISMATCH',
                'baseline': 'REFUSED: EnvironmentBaseline.__post_init__ baseline RF domain mismatch',
                'path': 'blocked',
            })
    blocked = [r for r in rows if r['path'] != 'allowed']
    return {
        'fixed_rf_mdb': FIXED_RF_MDB,
        'control_upper_mdb': CONTROL_UPPER_MDB,
        'upper_margin_mdb': {str(m): CONTROL_UPPER_MDB - m for m in LEVELS},
        'rows': rows,
        'execution_allowed': not blocked,
        'blocked_reason': ('RF_DOMAIN_MISMATCH' if blocked else None),
        'blocked_levels': [r['attenuation_mdb'] for r in blocked],
    }


def generate_sequence(seed=SEQUENCE_SEED):
    """Pre-generate the read-only, balanced ON order; OFF frames bracket it."""
    on = []
    for level in LEVELS:
        for repeat in range(REPEATS):
            on.append({'tag': 'on-%d-r%d' % (level, repeat),
                       'kind': 'normal', 'on': True, 'physical': True,
                       'attenuation_mdb': level, 'repeat': repeat})
    random.Random(seed).shuffle(on)
    frames = [{'index': 0, 'tag': 'off-pre', 'kind': 'off', 'on': False,
               'physical': False, 'attenuation_mdb': FIXED_RF_MDB, 'repeat': 0}]
    for index, item in enumerate(on, 1):
        item = dict(item, index=index)
        frames.append(item)
    frames.append({'index': TOTAL_FRAMES - 1, 'tag': 'off-post', 'kind': 'off',
                   'on': False, 'physical': False,
                   'attenuation_mdb': FIXED_RF_MDB, 'repeat': 0})
    return {
        'schema': 'rf-46db-ladder-sequence-v1',
        'seed': seed,
        'levels_mdb': list(LEVELS),
        'repeats': REPEATS,
        'on': ON_FRAMES,
        'off': OFF_FRAMES,
        'total': TOTAL_FRAMES,
        'budget': dict(BUDGET),
        'frames': frames,
    }


def validate_sequence(sequence):
    if not isinstance(sequence, dict):
        raise SequenceInvalid('sequence must be an object')
    frames = sequence.get('frames')
    if not isinstance(frames, list) or len(frames) != TOTAL_FRAMES:
        raise SequenceInvalid('frames must be %d' % TOTAL_FRAMES)
    on = [f for f in frames if f.get('on') is True]
    off = [f for f in frames if f.get('on') is False]
    if len(on) != ON_FRAMES or len(off) != OFF_FRAMES:
        raise SequenceInvalid('on/off counts must be %d/%d' % (ON_FRAMES, OFF_FRAMES))
    counts = {level: sum(1 for f in on if f.get('attenuation_mdb') == level)
              for level in LEVELS}
    if any(counts[level] != REPEATS for level in LEVELS):
        raise SequenceInvalid('unbalanced attenuation counts: %r' % (counts,))
    tags = [f.get('tag') for f in frames]
    if len(set(tags)) != len(tags):
        raise SequenceInvalid('duplicate frame tag')
    if frames[0].get('tag') != 'off-pre' or frames[-1].get('tag') != 'off-post':
        raise SequenceInvalid('OFF frames must bracket the run')
    if any(f.get('physical') is not False for f in off):
        raise SequenceInvalid('OFF frames must be physical=False')
    if any(f.get('physical') is not True for f in on):
        raise SequenceInvalid('ON frames must be physical=True')
    return {'on': len(on), 'off': len(off), 'counts': counts,
            'seed': sequence.get('seed'), 'total': len(frames)}


OFF_MISSING = 'OBSERVATION_MEMBER_MISSING'
READING_KEYS = ('in_window_rx_gain_index', 'chip_tx_attenuation_mdb')


def _is_missing(reading):
    return (isinstance(reading, dict) and reading.get('available') is False
            and reading.get('reason') == OFF_MISSING)


def is_expected_off_absence(sidecar):
    """Only physical=False + identity_ok + unique missing-member is expected."""
    if not isinstance(sidecar, dict):
        return False
    if sidecar.get('usable') is not False or sidecar.get('stop') is not True:
        return False
    if sidecar.get('reasons') != [OFF_MISSING]:
        return False
    obs = sidecar.get('observation')
    if not isinstance(obs, dict):
        return False
    if obs.get('identity_ok') is not True or obs.get('identity_reason') is not None:
        return False
    if obs.get('observation_present') is not False:
        return False
    if obs.get('reasons') != [OFF_MISSING]:
        return False
    return all(_is_missing(obs.get(key)) for key in READING_KEYS)


def off_action(tag, physical, sidecar):
    if tag.endswith('-off'):
        if physical is False and is_expected_off_absence(sidecar):
            return 'expected_absent', 'OFF_PHYSICAL_FALSE_NO_OBSERVATION'
        return 'reject', 'OFF_NOT_EXPECTED_ABSENCE'
    if isinstance(sidecar, dict) and sidecar.get('usable') is True and sidecar.get('stop') is False:
        return 'usable', None
    return 'reject', 'ON_OBSERVATION_UNUSABLE'


def wrap_80mhz_session(session, recorder):
    """Attach recording to the actual worker client; no new commands/polls."""
    wrapped = capture_record.RecordingClient(session.worker.client, recorder)
    session.worker.client = wrapped
    return session


def split_records(record, declared_context):
    """Three physically separate record classes (never claimed as measured)."""
    context = dict(declared_context)
    context['source'] = 'declared'
    cfg = record['actual_config']
    readbacks = {
        'source': 'measured',
        'captures': record['captures'],
        'actual_config': cfg,
        'gain': {'value': cfg.get('gain_index'),
                 'kind': 'single_in_window_read',
                 'agc_trajectory': 'NOT_MEASURED'},
        'attenuation': {'tx_attenuation_mdb': cfg.get('tx_attenuation_mdb'),
                        'chip_tx_attenuation_mdb': cfg.get('chip_tx_attenuation_mdb')},
        'pre_post': [{'capture_id': c.get('capture_id'),
                      'daemon_timestamp_ns': c.get('daemon_timestamp_ns'),
                      'samples': c.get('samples'), 'sample_rate_hz': c.get('sample_rate_hz'),
                      'ratio': c.get('ratio'), 'iq_sha256': c.get('iq_sha256')}
                     for c in record['captures']],
    }
    return {
        'schema': 'rf-46db-ladder-record-v1',
        'context': context,
        'commands': record['controls'],
        'readbacks': readbacks,
        'waveform': record['waveform'],
    }


def finalize_records(recorder, declared, output):
    """Session-end finalize/write: three separate files, never merged claims."""
    waveform = declared['waveform']
    actual = declared['actual_config']
    expected_cfg = waveform.get('expected_config') or actual['rf_config']
    actual_cfg = waveform.get('actual_config') or actual['rf_config']
    recorder.set_waveform(
        truth=waveform['truth'],
        parameters={
            'amplitude': waveform['amplitude'],
            'physical_max_code': waveform['physical_max_code'],
            'seed': waveform['seed'],
            'period_samples': waveform['period_samples'],
            'quantization': waveform['quantization'],
        },
        expected_config=expected_cfg,
        actual_config=actual_cfg,
        consistent=waveform.get('consistent', True),
        notes=['declared context; actual readbacks kept in readbacks.json'])
    recorder.set_actual_config(
        rf_config=actual['rf_config'],
        tx_attenuation_mdb=actual['tx_attenuation_mdb'],
        rx_attenuation_mdb=actual['rx_attenuation_mdb'],
        gain_index=actual['gain_index'],
        chip_tx_attenuation_mdb=actual['chip_tx_attenuation_mdb'],
        sample_rate_hz=actual['sample_rate_hz'],
        samples=actual['samples'],
        ratio=actual['ratio'],
        release_id=actual['release_id'],
        config_revision=actual['config_revision'],
        session=declared['session'],
        boot_id=declared['boot_id'])
    record = recorder.finalize()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'capture-record.json').write_text(
        json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    split = split_records(record, declared)
    (output / 'declared-context.json').write_text(
        json.dumps(split['context'], indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (output / 'commands.json').write_text(
        json.dumps(split['commands'], indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (output / 'readbacks.json').write_text(
        json.dumps(split['readbacks'], indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return output


def run_recorded_session(session, sequence, recorder, declared, output):
    """Actual 80 MHz session loop: wrap worker client, run read-only sequence, finalize."""
    validate_sequence(sequence)
    wrap_80mhz_session(session, recorder)
    session.setup(declared.get('seed', 1))
    for frame in sequence['frames']:
        session.scene(frame['kind'])
        session.capture(frame['tag'], physical=frame['physical'])
    return finalize_records(recorder, declared, output)


def dry_run(sequence=None):
    sequence = sequence or generate_sequence()
    validation = validate_sequence(sequence)
    compliance = attenuation_compliance()
    return {
        'schema': SCHEMA,
        'dry_run': 'PASS_PLAN_VALIDATION',
        'execution_allowed': compliance['execution_allowed'],
        'blocked_reason': compliance['blocked_reason'],
        'blocked_levels': compliance['blocked_levels'],
        'sequence': validation,
        'budget': dict(BUDGET),
        'nominal_source_on_us': NOMINAL_SOURCE_ON_US,
        'measured_source_on_span_cap_s': MEASURED_SPAN_CAP_S,
        'compliance': compliance,
        'off_policy': 'physical=False + identity_ok + unique OBSERVATION_MEMBER_MISSING only',
    }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--sequence', type=Path)
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--record-context', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    sequence = (json.loads(args.sequence.read_text(encoding='utf-8'))
                if args.sequence else generate_sequence())
    if not args.dry_run:
        compliance = attenuation_compliance()
        if not compliance['execution_allowed']:
            raise SystemExit('EXECUTION_BLOCKED: %s levels=%r' % (
                compliance['blocked_reason'], compliance['blocked_levels']))
        raise SystemExit('EXECUTION_NOT_IMPLEMENTED_IN_OFFLINE_TASK')
    if args.record and not args.record_context:
        raise SystemExit('--record requires --record-context JSON')
    print(json.dumps(dry_run(sequence), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
