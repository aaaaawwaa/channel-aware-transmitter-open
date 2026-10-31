"""Verify all new development-pilot IQ/records against exact candidate sources.

Read-only local replay. Never counts a derived frame as new physical evidence.
This is not the formal S2 TEST acceptance gate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import merged_release as R
from scripts.rf_environment_verify import verify_record
from scripts.rf_teaching_verify import compare


def verify(directory, candidate):
    directory, candidate = Path(directory).resolve(), Path(candidate).resolve()
    manifest = R.verify_release(candidate, require_software=True)
    if (manifest.get('software_contract') != 'cnn-bpsk-environment-v1'
            or R.software_digests(ROOT, manifest['software_contract']) != manifest['software']):
        raise ValueError('replay sources must exactly match the S2 candidate')
    declaration = json.loads((directory / 'declaration.json').read_text(encoding='utf-8'))
    pilot = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
    if (declaration.get('expected_release') != manifest['release_id']
            or declaration.get('software') != manifest['software']
            or pilot.get('expected_release') != manifest['release_id']
            or pilot.get('split') != 'development-pilot'):
        raise ValueError('pilot qualification identity mismatch')
    failures, checked, ids, hashes = [], [], set(), set()
    rows = pilot.get('records') or []
    if len(rows) != 18 or len(pilot.get('sessions', [])) != 3:
        failures.append('incomplete pilot')
    for session in pilot.get('sessions', []):
        if session.get('closed') is not True or session.get('disarm_ack') is not True:
            failures.append('session closure unconfirmed')
    for index, row in enumerate(rows):
        tag = row['tag']
        if Path(tag).name != tag: raise ValueError('invalid pilot tag')
        expected = 's%02d-%s-00' % (index // 6, ('normal', 'narrowband', 'pulse', 'compound', 'off', 'domain')[index % 6])
        if tag != expected: failures.append('layout:' + tag)
        raw = (directory / (tag + '.iq')).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        meta = json.loads((directory / (tag + '.json')).read_text(encoding='utf-8'))
        extra = json.loads((directory / (tag + '.environment.json')).read_text(encoding='utf-8'))
        # capture_id is client-local; complete immutable identity includes time.
        identity = tuple(meta.get(k) for k in ('capture_id', 'release_id', 'connection_generation',
                                              'config_revision', 'completed_at_monotonic'))
        if (len(raw) != 16384 or digest != row.get('iq_sha256') or digest != meta.get('iq_sha256')
                or digest != extra.get('iq_sha256') or identity in ids or digest in hashes
                or meta.get('release_id') != manifest['release_id'] or row.get('egress_safe') is not True
                or meta.get('handoff_frame_matches') is not True):
            failures.append('frame identity:' + tag)
        ids.add(identity)
        hashes.add(digest)
        record = extra.get('record') or {}
        folder = directory / 'records' / record.get('record_id', '')
        replay = verify_record(folder, candidate / 'rf_environment_baseline.json')
        failures += [tag + ':' + value for value in replay['mismatches']]
        stored = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        failures += compare(extra.get('environment'), stored.get('environment'), tag + '.environment')
        failures += compare(row.get('environment'), stored.get('environment'), tag + '.row_environment')
        if ((folder / 'frame.iq').read_bytes() != raw
                or extra['display_environment'].get('capture_id') != row['capture_id']
                or extra['display_environment'].get('label') != stored['environment'].get('label')):
            failures.append('record/display:' + tag)
        checked.append(dict(tag=tag, capture_id=row['capture_id'], iq_sha256=digest,
                            label=replay['label'], replay_passed=replay['passed']))
    return dict(passed=not failures, failures=failures, checked=checked, new_physical_frames=len(rows),
                release_id=manifest['release_id'], scope='development pilot same-IQ mathematical replay',
                formal_physical='NOT_RUN', human_acceptance='NOT_RUN')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pilot', required=True, type=Path)
    p.add_argument('--candidate', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    result = verify(a.pilot, a.candidate)
    with a.output.open('x', encoding='utf-8') as f: json.dump(result, f, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'checked'}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
