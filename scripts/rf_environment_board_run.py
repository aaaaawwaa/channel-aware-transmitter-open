"""Upload a bounded S2 development pilot and retrieve all evidence, without retry."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import merged_release as R
from scripts.rf_teaching_deploy import load_helper, _run


def fetch_evidence(s, remote, output):
    """One read-only best-effort download; failed/partial files remain evidence."""
    errors = []
    def download(source, target):
        try: s.get(source, str(target))
        except Exception as exc: errors.append(dict(path=source, error=str(exc)))
    try: names = s.listdir(remote)
    except Exception as exc: return [dict(path=remote, error=str(exc))]
    for name in names:
        if Path(name).name != name:
            errors.append(dict(path=remote, error='invalid evidence name'))
            continue
        if name != 'records':
            download(remote + '/' + name, output / name)
            continue
        records = output / 'records'
        records.mkdir(exist_ok=True)
        try: folders = s.listdir(remote + '/records')
        except Exception as exc:
            errors.append(dict(path=remote + '/records', error=str(exc)))
            continue
        for record_id in folders:
            if Path(record_id).name != record_id:
                errors.append(dict(path=remote, error='invalid record name'))
                continue
            dest = records / record_id
            dest.mkdir(exist_ok=True)
            origin = remote + '/records/' + record_id
            try: leaves = s.listdir(origin)
            except Exception as exc:
                errors.append(dict(path=origin, error=str(exc)))
                continue
            allowed = {'frame.iq', 'metadata.json', 'receipt.json'}
            allowed |= {name + '.partial' for name in allowed}
            for leaf in leaves:
                if leaf not in allowed:
                    errors.append(dict(path=origin, error='unexpected record member: ' + leaf))
                    continue
                download(origin + '/' + leaf, dest / leaf)
    return errors


def sessions_closed(sessions):
    """A partial failed campaign can close safely; completion is a separate gate."""
    return bool(sessions and all(x.get('owned') is True and x.get('closed') is True
        and x.get('disarm_ack') is True for x in sessions))


def run(candidate, output, freeze_path=None, offline_rejection_path=None):
    candidate, output = Path(candidate).resolve(), Path(output).resolve()
    manifest = R.verify_release(candidate, require_software=True)
    if manifest.get('software_contract') != 'cnn-bpsk-environment-v1':
        raise ValueError('exact verified S2 candidate required')
    freeze=None;freeze_sha=None
    if freeze_path is not None:
        from scripts.rf_environment_freeze import check_candidate
        path=Path(freeze_path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>131072:
            raise ValueError('bounded regular frozen TEST declaration required')
        freeze,freeze_sha=check_candidate(json.loads(path.read_text(encoding='utf-8')),candidate)
        # A formal TEST campaign must carry the measured N1-N12 set; the board entry
        # refuses to start without it (freeze spec 2.1), and so does this launcher.
        if offline_rejection_path is None:
            raise ValueError('a formal TEST run requires --offline-rejection: the measured '
                             'N1-N12 receipt must be uploaded with the freeze declaration')
        from scripts.rf_environment_acceptance import load_offline_rejection
        measured_offline=load_offline_rejection(offline_rejection_path)
    output.mkdir(parents=True, exist_ok=False)
    sources = [ROOT / 'scripts' / name for name in (
        'rf_environment_board_session.py', 'rf_environment_pilot.py',
        'rf_bpsk_ui_board_session.py', 'rf_bpsk_ui_acceptance.py')]
    if freeze is not None:sources.append(ROOT/'scripts/rf_environment_acceptance.py')
    remote = '/tmp/rf-environment-pilot-' + uuid.uuid4().hex
    declaration = dict(expected_release=manifest['release_id'], software=manifest['software'],
        baseline_file_sha256=hashlib.sha256((candidate / 'rf_environment_baseline.json').read_bytes()).hexdigest(),
        scope='new physical development pilot via production worker/UiView; no GT911; not formal TEST',
        split='development-pilot', reference_seeds=[1, 37, 73], actual_scene_prbs7_seed=1, sessions=3,
        kinds=['normal', 'narrowband', 'pulse', 'compound', 'off', 'domain'], expected_frames=18,
        cable_confirmation='user confirmed TX1-RX1, explicit host --cable-confirmed',
        stop_conditions=['timeout', 'clipping', 'unsafe egress', 'missing evidence', 'close unconfirmed'],
        source_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    if freeze is not None:
        declaration.update(scope='finite formal TEST via production worker/UiView; no GT911',
            split='test',sessions=6,expected_frames=108,freeze_sha256=freeze_sha)
        (output/'freeze.json').write_text(json.dumps(freeze,indent=2,allow_nan=False),encoding='utf-8')
    (output / 'declaration.json').write_text(json.dumps(declaration, indent=2), encoding='utf-8')
    result = dict(remote=remote, release_id=manifest['release_id'], passed=False,
                  board_state='UNKNOWN', formal_physical='NOT_RUN', human_acceptance='NOT_RUN')
    client = None
    try:
        helper = load_helper()
        client = helper.connect()
        with client.open_sftp() as s:
            s.mkdir(remote)
            for source in sources: s.put(str(source), remote + '/' + source.name)
            if freeze is not None:
                s.put(str(output/'freeze.json'),remote+'/freeze.json')
                s.put(str(offline_rejection_path),remote+'/offline-rejection.json')
        cmd = ('sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 -u -B '
               + remote + '/rf_environment_board_session.py --expected-release '
               + manifest['release_id'] + ' --cable-confirmed --output ' + remote + '/out')
        if freeze is not None:cmd+=' --freeze '+remote+'/freeze.json --offline-rejection '+remote+'/offline-rejection.json'
        result['exit_code'] = _run(client, helper, cmd, output / 'run.log', timeout=600 if freeze else 180)
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc), policy='no retry; inspect read-only if unknown')
    finally:
        if client is not None:
            try:
                with client.open_sftp() as s:
                    result['download_errors'] = fetch_evidence(s, remote + '/out', output)
            except Exception as exc:
                result['download_errors'] = [dict(error=str(exc))]
            client.close()
        try:
            pilot = json.loads((output / 'result.json').read_text(encoding='utf-8'))
            if freeze is not None and pilot.get('offline_rejection') != measured_offline:
                raise ValueError('board offline_rejection differs from the uploaded measured receipt')
            result.update(passed=result.get('exit_code') == 0 and not result.get('error')
                and not result.get('download_errors') and pilot.get('formal_rf_passed' if freeze else 'passed') is True,
                known_correct=pilot.get('known_correct'), negative_false_clear=pilot.get('negative_false_clear'),
                board_state='VERIFIED_SAFE' if sessions_closed(pilot.get('sessions', [])) else 'UNKNOWN')
            if freeze is not None:result.update(formal_physical='PASS' if result['passed'] else 'FAIL',
                freeze_sha256=freeze_sha,stage_passed=False,
                offline_rejection=pilot.get('offline_rejection', measured_offline))
        except Exception as exc:
            result['result_error'] = str(exc)
        result['evidence_sha256'] = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in output.rglob('*') if p.is_file()}
        (output / 'receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--cable-confirmed', required=True, action='store_true')
    p.add_argument('--freeze',type=Path,help='qualified frozen declaration; switches to 108-frame TEST')
    p.add_argument('--offline-rejection',type=Path,
                   help='measured N1-N12 receipt; required together with --freeze')
    a = p.parse_args()
    result = run(a.candidate, a.output,a.freeze,a.offline_rejection)
    print(json.dumps({k: v for k, v in result.items() if k != 'evidence_sha256'}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
