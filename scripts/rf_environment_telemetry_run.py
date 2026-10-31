"""Host launcher for the 12-frame 46 dB telemetry diagnostic: upload, run once, retrieve.

Modelled on scripts/rf_environment_board_run.py (the frozen 18-frame entry, which is
NOT modified): one bounded attempt, no retry, one read-only evidence download, and a
receipt whose passed flag requires all 12 frames AND their per-frame telemetry files.

Gates, in order (each is a named refusal before anything is uploaded or created):
1. explicit --cable-confirmed (the environment control-token path; no HEADROOM lease,
   attempt budget or one-shot nonce is consumed);
2. the output directory must not exist;
3. the candidate must verify with software_contract cnn-bpsk-environment-v1;
4. the candidate must be the one approved release of this diagnostic.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import merged_release as R                                    # noqa: E402
from scripts.rf_environment_board_run import fetch_evidence, sessions_closed  # noqa: E402
from scripts.rf_environment_telemetry_pilot import (APPROVED_FRAMES,       # noqa: E402
                                                    APPROVED_INITIALIZATIONS,
                                                    APPROVED_SESSIONS,
                                                    FRAME_PLAN, SEEDS,
                                                    TELEMETRY_SPLIT)
from scripts.rf_environment_telemetry_session import (APPROVED_ATTENUATION_MDB,  # noqa: E402
                                                      APPROVED_RELEASE,
                                                      TELEMETRY_SCHEMA)
from scripts.rf_teaching_deploy import _run, load_helper                   # noqa: E402

SOURCES = ('rf_environment_telemetry_session.py', 'rf_environment_telemetry_pilot.py',
           'rf_environment_board_session.py', 'rf_bpsk_ui_board_session.py',
           'rf_bpsk_ui_acceptance.py',
           # rf_environment_board_session.py imports it at module import time
           # (from rf_environment_pilot import campaign); without it the board dies in
           # telemetry_session_class() before the first session exists.  The frozen
           # 18-frame launcher (rf_environment_board_run.py:84-86) uploads it too.
           'rf_environment_pilot.py')
SOFTWARE_CONTRACT = 'cnn-bpsk-environment-v1'


class Refusal(RuntimeError):
    """A named refusal that happens before any upload, session or capture."""

    def __init__(self, code, message):
        super().__init__('%s: %s' % (code, message))
        self.code = code


def declaration_for(candidate, manifest, sources):
    return dict(
        expected_release=manifest['release_id'], software=manifest.get('software'),
        baseline_file_sha256=hashlib.sha256(
            (candidate / 'rf_environment_baseline.json').read_bytes()).hexdigest(),
        scope='12-frame 46 dB telemetry diagnostic through the production worker/UiView; '
              'no GT911; not the formal TEST campaign',
        split=TELEMETRY_SPLIT, sessions=APPROVED_SESSIONS,
        initializations_max=APPROVED_INITIALIZATIONS,
        reference_seeds=list(SEEDS), actual_scene_prbs7_seed=1,
        per_session_order=[list(item) for item in FRAME_PLAN],
        kinds=[kind for kind, _ in FRAME_PLAN], frames_per_session=len(FRAME_PLAN),
        expected_frames=APPROVED_FRAMES, attenuation_mdB=APPROVED_ATTENUATION_MDB,
        telemetry_schema=TELEMETRY_SCHEMA,
        telemetry=['pre_frame_gain', 'post_frame_gain', 'capture_timing (physical frames '
                   'only)', 'session_attenuation_readback (init/configure actual TX1)',
                   'per_frame_daemon_and_host_timestamps', 'not_available (N1..N7)'],
        cable_confirmation='user confirmed TX1-RX1; explicit host --cable-confirmed and '
                           'board --cable-confirmed',
        authorization='installed control token /etc/rf-control.token (read by the worker, '
                      'never stored) + explicit --cable-confirmed; no HEADROOM lease, no '
                      'attempt budget and no one-shot nonce',
        stop_conditions=['initial_not_safe_or_wrong_release', 'egress unsafe',
                         'missing evidence (environment or telemetry)',
                         'duplicate (capture_id, iq_sha256)', 'capture incomplete / timeout',
                         'environment reason in {CLIPPED, INVALID_IQ}',
                         'close/disarm unconfirmed',
                         'budget: 3 initializations or 12 frames'],
        source_hashes={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in sources})


def run(candidate, output, cable_confirmed):
    if not cable_confirmed:
        raise Refusal('CABLE_NOT_CONFIRMED',
                      'the explicit host --cable-confirmed attestation for the TX1-RX1 '
                      'cable is required; nothing was uploaded and no session exists')
    output = Path(output).resolve()
    if output.exists():
        raise Refusal('OUTPUT_EXISTS', 'the output directory must not exist: %s' % output)
    candidate = Path(candidate).resolve()
    manifest = R.verify_release(candidate, require_software=True)
    if manifest.get('software_contract') != SOFTWARE_CONTRACT:
        raise Refusal('SOFTWARE_CONTRACT_MISMATCH',
                      'exact verified %s candidate required' % SOFTWARE_CONTRACT)
    if manifest.get('release_id') != APPROVED_RELEASE:
        raise Refusal('RELEASE_NOT_APPROVED',
                      'the 12-frame diagnostic is approved only for release %s, not %s'
                      % (APPROVED_RELEASE, manifest.get('release_id')))
    sources = [ROOT / 'scripts' / name for name in SOURCES]
    for source in sources:
        if not source.is_file():
            raise Refusal('SOURCE_MISSING', 'upload source missing: %s' % source)
    output.mkdir(parents=True, exist_ok=False)
    remote = '/tmp/rf-environment-telemetry-' + uuid.uuid4().hex
    declaration = declaration_for(candidate, manifest, sources)
    (output / 'declaration.json').write_text(
        json.dumps(declaration, indent=2, allow_nan=False), encoding='utf-8')
    result = dict(remote=remote, release_id=manifest['release_id'], passed=False,
                  board_state='UNKNOWN', frames=0, telemetry_files_present=False,
                  formal_physical='NOT_RUN', human_acceptance='NOT_RUN', s2_stage_passed=False)
    client = None
    try:
        helper = load_helper()
        client = helper.connect()
        with client.open_sftp() as sftp:
            sftp.mkdir(remote)
            for source in sources:
                sftp.put(str(source), remote + '/' + source.name)
        cmd = ('sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 -u -B '
               + remote + '/rf_environment_telemetry_session.py --expected-release '
               + APPROVED_RELEASE + ' --cable-confirmed --output ' + remote + '/out')
        result['exit_code'] = _run(client, helper, cmd, output / 'run.log', timeout=180)
    except Exception as exc:                                   # noqa: BLE001
        result.update(error_type=type(exc).__name__, error=str(exc),
                      policy='no retry; inspect read-only if unknown')
    finally:
        if client is not None:
            try:
                with client.open_sftp() as sftp:
                    result['download_errors'] = fetch_evidence(sftp, remote + '/out', output)
            except Exception as exc:                           # noqa: BLE001
                result['download_errors'] = [dict(error=str(exc))]
            client.close()
        try:
            pilot = json.loads((output / 'result.json').read_text(encoding='utf-8'))
            records = [row for row in pilot.get('records', []) if isinstance(row, dict)]
            telemetry_files = bool(records) and all(
                isinstance(row.get('telemetry_file'), str)
                and (output / row['telemetry_file']).is_file() for row in records)
            result.update(
                frames=len(records), telemetry_files_present=telemetry_files,
                board_state=('VERIFIED_SAFE' if sessions_closed(pilot.get('sessions', []))
                             else 'UNKNOWN'),
                passed=(result.get('exit_code') == 0 and not result.get('error')
                        and not result.get('download_errors')
                        and pilot.get('passed') is True and telemetry_files))
            if pilot.get('stop_cause') is not None:
                result['stop_cause'] = pilot['stop_cause']
        except Exception as exc:                               # noqa: BLE001
            result.update(result_error=str(exc), frames=0, telemetry_files_present=False)
        result['evidence_sha256'] = {
            str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in output.rglob('*') if path.is_file()}
        (output / 'receipt.json').write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cable-confirmed', action='store_true')
    args = parser.parse_args(argv)
    if not args.cable_confirmed:
        raise SystemExit('REFUSE: --cable-confirmed attestation required for the TX1-RX1 '
                         'cable; nothing was uploaded and no session exists (environment '
                         'control-token path, no HEADROOM nonce)')
    result = run(args.candidate, args.output, True)
    print(json.dumps({key: value for key, value in result.items()
                      if key != 'evidence_sha256'}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
