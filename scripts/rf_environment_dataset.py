"""Freeze source-session calibration splits; never count replay as new RF data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pynq_overlay/sw'))
sys.path.insert(0, str(ROOT))


def validate_splits(rows):
    """A source, its descendants, identical IQ and a session stay in one split."""
    parents = {}
    for row in rows:
        if (not isinstance(row, dict) or row.get('split') not in ('train', 'val', 'test')
                or any(not isinstance(row.get(k), str) or not row[k] for k in
                       ('source_id', 'session_id', 'iq_sha256'))
                or len(row['iq_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in row['iq_sha256'])):
            raise ValueError('invalid source split declaration')
        source = row['source_id']
        parent = row.get('parent_source_id', source)
        if not isinstance(parent, str) or not parent:
            raise ValueError('invalid parent source')
        if source in parents and parents[source] != parent:
            raise ValueError('inconsistent parent source')
        parents[source] = parent
    assignments = {}
    for row in rows:
        ancestor, seen = row['source_id'], set()
        while parents.get(ancestor, ancestor) != ancestor:
            if ancestor in seen:
                raise ValueError('cyclic source lineage')
            seen.add(ancestor)
            ancestor = parents[ancestor]
            if ancestor not in parents:
                raise ValueError('missing parent source group')
        for key in (('source', ancestor), ('session', row['session_id']),
                    ('iq', row['iq_sha256'])):
            if key in assignments and assignments[key] != row['split']:
                raise ValueError('source/session/IQ leakage across splits')
            assignments[key] = row['split']


def calibrate_s1(pilot, output):
    """Reuse two S1 sessions as TRAIN and the third as VAL, explicitly offline."""
    from scripts.rf_bpsk_replay import frame_from_metadata
    from rf_bpsk_pipeline import DemodConfig, analyze_demod
    from rf_bpsk_receiver import BpskConfig
    from rf_environment import fit_baseline, analyze_environment
    from rf_teaching_profile import teaching_capability
    pilot, output = Path(pilot), Path(output)
    if output.exists():
        raise ValueError('calibration output already exists')
    # Historical domain pin.  S1 was captured at 40 dB; the teaching/environment
    # domain has since moved to the single source value.  Fitting here would stamp
    # the current domain on 40 dB frames, so this S1-only entry refuses instead of
    # relabelling them; the 46 dB calibration entry is the migration step that
    # replaces it.
    if dict(teaching_capability().rf_parameters)['attenuation_mdB'] != 40000:
        raise ValueError('S1 calibration is bound to the historical 40 dB domain; the '
                         'teaching/environment domain moved to the single source value '
                         '-- build the 46 dB calibration entry instead')
    declaration = json.loads((pilot / 'declaration.json').read_text(encoding='utf-8'))
    expected = dict(lo_hz=80000000, fs_hz=60000000, bandwidth_hz=30000000,
                    amplitude=60, ratio=0, phy=0, tap=29, attenuation_mdB=40000)
    if any(declaration.get(k) != v for k, v in expected.items()):
        raise ValueError('S1 declared RF conditions differ from calibration domain')
    report = json.loads((pilot / 'result.json').read_text(encoding='utf-8'))
    if report.get('passed') is not True:
        raise ValueError('S1 source campaign did not pass')
    frames, envelopes, rows = [], [], []
    rf_config = dict(teaching_capability().rf_parameters)
    for entry in report['records']:
        if entry.get('on') is not True:
            continue
        tag = entry['tag']
        if Path(tag).name != tag or tag[:3] not in ('s00', 's01', 's02'):
            raise ValueError('invalid S1 source tag')
        iq_path, meta_path = pilot / (tag + '.iq'), pilot / (tag + '.json')
        if iq_path.stat().st_size != 16384 or meta_path.stat().st_size > 131072:
            raise ValueError('source artifact size outside bounds')
        raw = iq_path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        digest = hashlib.sha256(raw).hexdigest()
        if digest != meta.get('iq_sha256') or digest != entry.get('iq_sha256'):
            raise ValueError('calibration source hash mismatch')
        if meta.get('release_id') != declaration['expected_release']:
            raise ValueError('source release mismatch')
        f = frame_from_metadata(raw, meta)
        submitted = meta['demod_result']['submitted']
        cfg = DemodConfig(BpskConfig(**submitted['config']), submitted['revision'],
                          submitted['decim'], submitted['rx_len'], submitted['input_fs_hz'])
        envelope = analyze_demod(f, cfg)
        if envelope.result.reference_sync is not True or envelope.result.ber != 0:
            raise ValueError('normal source is not qualified BPSK')
        row = dict(source_id='s1-' + tag, session_id='s1-' + tag[:3],
                   iq_sha256=digest, split='val' if tag.startswith('s02') else 'train',
                   iq_file=str(iq_path.resolve()), metadata_file=str(meta_path.resolve()),
                   origin='previous physical S1 capture; offline S2 calibration reuse')
        frames.append(f); envelopes.append(envelope); rows.append(row)
    validate_splits(rows)
    if len(rows) != 30 or len({r['iq_sha256'] for r in rows}) != 30:
        raise ValueError('expected 30 unique qualified S1 normal frames')
    indices = [i for i, r in enumerate(rows) if r['split'] == 'train']
    baseline = fit_baseline([frames[i] for i in indices], [envelopes[i] for i in indices],
                            rf_config, [{k: rows[i][k] for k in
                                ('source_id', 'session_id', 'iq_sha256', 'split')}
                                for i in indices])
    validation = [dict(source_id=rows[i]['source_id'],
        result=analyze_environment(frames[i], envelopes[i], rf_config, baseline).to_dict())
        for i in range(len(rows)) if rows[i]['split'] == 'val']
    encoded = json.dumps(baseline.to_dict(), allow_nan=False, indent=2).encode('utf-8')
    if len(encoded) > 131072:
        raise ValueError('baseline exceeds release calibration bound')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'baseline.json').write_bytes(encoded)
    receipt = dict(scope='offline development calibration; zero new physical frames',
        calibration_status='development', baseline_sha256=baseline.sha256,
        baseline_file_sha256=hashlib.sha256(encoded).hexdigest(), sources=rows,
        train_frames=len(indices), val_frames=len(validation), test_frames=0,
        validation=validation, val_normal_clear=sum(r['result']['label'] == 'CLEAR'
                                                   for r in validation),
        s2_stage_passed=False, rf_config=rf_config,
        source_declaration_sha256=hashlib.sha256((pilot / 'declaration.json').read_bytes()).hexdigest())
    (output / 'calibration-receipt.json').write_text(
        json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--s1-pilot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = calibrate_s1(args.s1_pilot, args.output)
    print(json.dumps({k: result[k] for k in ('scope', 'train_frames', 'val_frames',
        'val_normal_clear', 'baseline_sha256', 's2_stage_passed')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
