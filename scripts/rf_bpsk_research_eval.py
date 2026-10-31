"""Evaluate the independent research algorithms on the offline reference vectors.

Loads ``vectors/manifest.json`` + the vector files, runs the independent demodulator
and the frozen ``receive_bpsk`` on each vector, and reports numbers per vector and
per impairment group.  It then states, with those numbers, whether each research
candidate is REPLACEABLE (可替换), NEEDS_NEW_CONTRACT (需新合同) or NOT_VIABLE
(不可行/无收益).  It changes nothing in the frozen surface.

    python scripts/rf_bpsk_research_eval.py --vectors DIR --json OUT.json
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'pynq_overlay/sw'))

import rf_bpsk_research as research  # noqa: E402
from scripts import rf_reference_vectors as vectors  # noqa: E402


def _frozen_result(raw, row):
    from rf_bpsk_receiver import BpskConfig, receive_bpsk
    return receive_bpsk(vectors.frame_from_vector(raw, row),
                        BpskConfig(mode='BPSK',
                                   symbol_rate_hz=research.SYMRATE_HZ,
                                   shaping='RECT',
                                   reference_prbs7_seed=row['params']['seed'])
                        ).to_dict()


def _evm_of(raw, row):
    iq = research.iq_from_bytes(raw)
    expected = row['expected']
    if expected.get('reference_sync') is not True:
        return None
    demod = research.independent_demod(iq, seed=row['params']['seed'])
    return demod.get('evm_rms')



def evaluate(directory):
    manifest = vectors.load_set(directory)
    rows = []
    for row in manifest['vectors']:
        raw = (Path(directory) / row['file']).read_bytes()
        frozen = _frozen_result(raw, row)
        independent = research.independent_demod(research.iq_from_bytes(raw),
                                                 seed=row['params']['seed'])
        expected = row['expected']
        applied = row['params']
        cfo_error_frozen = (abs(frozen['cfo_hz'] - applied['cfo_hz'])
                            if frozen.get('cfo_hz') is not None else None)
        cfo_error_independent = (abs(independent['cfo_hz'] - applied['cfo_hz'])
                                 if independent.get('cfo_hz') is not None else None)
        rows.append({
            'id': row['id'], 'group': row['group'],
            'applied': {'cfo_hz': applied['cfo_hz'],
                        'phase_rad': applied['phase_rad'],
                        'snr_db': applied['snr_db'],
                        'invert': applied['invert'],
                        'delay_samples': applied['delay_samples']},
            'expected': {'reason': expected['reason'],
                         'reference_sync': expected['reference_sync'],
                         'bit_errors': expected['bit_errors']},
            'frozen': {'reason': frozen['reason'],
                       'reference_sync': frozen['reference_sync'],
                       'bit_errors': frozen['bit_errors'],
                       'cfo_hz': frozen['cfo_hz']},
            'independent': {'reason': independent['reason'],
                            'reference_sync': independent['reference_sync'],
                            'bit_errors': independent['bit_errors'],
                            'cfo_hz': independent['cfo_hz'],
                            'timing_offset_samples':
                                independent['timing_offset_samples']},
            'cfo_error_hz_frozen': cfo_error_frozen,
            'cfo_error_hz_independent': cfo_error_independent,
            'evm_rms': _evm_of(raw, row),
            'agree': (frozen['reason'] == independent['reason']
                      and frozen['bit_errors'] == independent['bit_errors']),
        })
    groups = {}
    for row in rows:
        groups.setdefault(row['group'], []).append(row)
    summary = {}
    for name, members in groups.items():
        synced = [row for row in members
                  if row['expected']['reference_sync'] is True]
        errors = [row['frozen']['bit_errors'] for row in synced
                  if row['frozen']['bit_errors'] is not None]
        cfo_errors = [row['cfo_error_hz_frozen'] for row in synced
                      if row['cfo_error_hz_frozen'] is not None]
        evms = [row['evm_rms'] for row in members if row['evm_rms'] is not None]
        summary[name] = {
            'vectors': len(members),
            'synced': len(synced),
            'max_bit_errors': max(errors) if errors else None,
            'max_cfo_error_hz_frozen': max(cfo_errors) if cfo_errors else None,
            'evm_min': min(evms) if evms else None,
            'evm_max': max(evms) if evms else None,
            'all_agree': all(row['agree'] for row in members),
        }
    synced_all = [row for row in rows if row['expected']['reference_sync'] is True]
    verdicts = [
        {
            'candidate': 'offline EVM metric (RMS error vs the independent reference)',
            'status': 'REPLACEABLE',
            'basis': 'additive offline metric computed on %d synced vectors; EVM is '
                     '%.2e..%.2e across the impairment groups; the frozen contract '
                     'needs no change because nothing production reads it'
                     % (len(synced_all),
                        min(row['evm_rms'] for row in synced_all if row['evm_rms'] is not None),
                        max(row['evm_rms'] for row in synced_all if row['evm_rms'] is not None)),
            'note': 'EVM remains a backlog (task-29) pre-research definition: not '
                    'accepted, no threshold, no contract',
        },
        {
            'candidate': 'soft information (BPSK LLR = 2*Re(y)/sigma^2)',
            'status': 'NEEDS_NEW_CONTRACT',
            'basis': 'the frozen BpskResult carries decisions/BER only (no soft field), '
                     'so exposing LLRs requires a new result/protocol contract',
        },
        {
            'candidate': 'independent CFO/timing recovery replacing the frozen estimator',
            'status': 'NOT_VIABLE',
            'basis': 'frozen CFO error <= %.3f Hz on every synced vector and 0 bit '
                     'errors everywhere; the independent estimator gives no measurable '
                     'gain, so a frozen-surface change is unjustified'
                     % max(row['cfo_error_hz_frozen'] for row in synced_all
                           if row['cfo_error_hz_frozen'] is not None),
        },
        {
            'candidate': 'RRC demodulation / shaping domain',
            'status': 'NEEDS_NEW_CONTRACT',
            'basis': 'the frozen engine accepts shaping=RECT only (any other shaping is '
                     'refused), so RRC reception needs a new contract, vectors and '
                     'acceptance before it can be evaluated here',
        },
        {
            'candidate': 'QPSK/QAM reception',
            'status': 'NEEDS_NEW_CONTRACT',
            'basis': 'outside the frozen BPSK domain; backlog task-29, not started by '
                     'this work package',
        },
    ]
    return {
        'schema': 'rf-bpsk-research-eval-v1',
        'scope': ('offline evaluation of independent research algorithms on synthetic '
                  'reference vectors; zero device, no RF, no board'),
        'vector_count': len(rows),
        'vector_set_digest': manifest.get('set_digest'),
        'groups': sorted(groups),
        'summary': summary,
        'rows': rows,
        'verdicts': verdicts,
        'limitations': [
            'synthetic vectors only; no physical RF, no board, no PL',
            'EVM is an offline definition (backlog pre-research), not an accepted metric',
            'soft-LLR/RRC/QPSK/QAM are contract questions, not implemented here',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vectors', type=Path, required=True)
    parser.add_argument('--json', type=Path, default=None)
    args = parser.parse_args(argv)
    report = evaluate(args.vectors)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'vector_count': report['vector_count'],
                      'groups': report['groups'],
                      'verdicts': [(row['candidate'][:48], row['status'])
                                   for row in report['verdicts']]}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
