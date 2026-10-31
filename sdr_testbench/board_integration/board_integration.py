"""PlutoSDR <-> board 433.9 MHz integration planner / guarded executor.

Offline paths (always available, no hardware):
  --help
  --self-test
  --dry-run --direction A|B

Hardware paths (UNEXECUTED in the 2026-10-19 offline preparation; require a
separate physical authorization, real wiring and the operator-provisioned
control token):
  --preflight   writes the Pluto TX safe-off state and reads it back; with
                --token-file it also gates the board idle state.  Not read-only.
  --execute --direction A|B --path-confirmed ... --token-file ... --board-port ...

The board daemon is reached through the maintained rf_status_client over a
local port forward; this tool never opens a serial port and never touches
COM11.  The Pluto is reached only through the maintained radio_test.py
subprocess, which owns the USB interface in its own phase.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

import capture_record

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
DEFAULT_CONFIG = ROOT / 'config' / 'diagnostic.json'
RADIO_TEST = PROJECT / 'sdr_testbench' / 'radio_test.py'
DEFAULT_CLIENT_DIR = PROJECT / 'pynq_overlay' / 'sw'
DEFAULT_RADIOCONDA = Path(r'<LOCAL_PATH>')
PLUTO_SERIAL = '<DEVICE_SERIAL>'

# Direction B (Pluto TX -> board RX1, board TX forbidden) execution limits.
# One session, one board init/configure, four board captures, at most two
# Pluto TX starts and at most 4.0 s of cumulative modulated Pluto TX.
B_CAPTURE_WINDOWS = 4
B_MAX_PLUTO_TX_STARTS = 2
B_MAX_PLUTO_TX_SECONDS = 4.0
B_PLUTO_READY_TIMEOUT_S = 30.0
B_PLUTO_EXIT_TIMEOUT_S = 60.0
B_MIN_ON_OFF_DB = 10.0
B_EXPECTED_TONE_BIN_STEP = 7
B_BIN_TOLERANCE = 1

# Direction B BPSK single-capture session (task-14): one capture, one Pluto TX,
# at most 2.0 s of BPSK playback, board TX zero.  The frozen BPSK contract
# (3 MSym/s, RECT, PRBS7 seed 1, non-differential) is validated by planner.
B_BPSK_CAPTURE_WINDOWS = 1
B_BPSK_MAX_PLUTO_TX_STARTS = 1
B_BPSK_MAX_TX_SECONDS = 2.0
B_BPSK_TX_MODE = 'tx-bpsk'

# Direction B statistical acceptance (task-17/18): 3 sessions x (10 ON + 1 OFF),
# frozen seed triplet, 33 captures, board TX zero.  Aggregation uses the declared
# `normal` demod path only; the conjugated path is a consistency check, never
# extra bits.  A session may be split into 2 TX segments (5 ON each, <=2.0 s
# measured per segment, independent safe-off readback per segment).
B_BPSK_STAT_SEEDS = (1, 37, 73)
B_BPSK_STAT_SESSIONS = 3
B_BPSK_STAT_ON_FRAMES = 10
B_BPSK_STAT_OFF_FRAMES = 1
B_BPSK_STAT_RECORDS = (B_BPSK_STAT_ON_FRAMES
                       + B_BPSK_STAT_OFF_FRAMES) * B_BPSK_STAT_SESSIONS
B_BPSK_STAT_ALIGNED = 27
B_BPSK_STAT_MAX_AGGREGATE_BER = 0.01
B_BPSK_STAT_TX_PER_SESSION_CHOICES = (1, 2)
B_BPSK_STAT_MAX_TX_SECONDS_TOTAL = {1: 6.0, 2: 12.0}
# Short settle after a session disarm before the next session's preflight
# (transition aid only: it delays the next session, never changes a predicate).
B_BPSK_STAT_DISARM_SETTLE_S = 0.3


def _bpsk_stat_tx_seconds_cap(tx_per_session):
    try:
        return B_BPSK_STAT_MAX_TX_SECONDS_TOTAL[int(tx_per_session)]
    except (KeyError, TypeError, ValueError):
        raise ExecutionError('BUDGET: --stat-tx-per-session must be 1 or 2, '
                             'got %r' % (tx_per_session,))


def _bpsk_stat_segment_frames(tx_per_session):
    if int(tx_per_session) not in B_BPSK_STAT_TX_PER_SESSION_CHOICES:
        raise ExecutionError('BUDGET: --stat-tx-per-session must be 1 or 2, '
                             'got %r' % (tx_per_session,))
    return B_BPSK_STAT_ON_FRAMES // int(tx_per_session)

import planner  # noqa: E402  (same directory)
import readback  # noqa: E402  (same directory)


class ExecutionError(RuntimeError):
    pass


# Last session-start safety poll evidence (task-19); read-only reporting.
_LAST_PREFLIGHT_POLL = None
# Last arm-wait evidence (task-20): per-attempt control_ops/armed/errors.
_LAST_ARM_WAIT = None
# Last post-arm confirmation evidence (task-21).
_LAST_POST_ARM_POLL = None


def _poll_status_with_retry(client, attempts=5, delay_s=0.2):
    """Bounded retry for a TRANSIENT session-start control-plane poll.

    Only a fully online, non-stale, safe idle snapshot is accepted.  A stale or
    unsafe snapshot is a hard failure immediately (never retried away), and a
    persistent timeout raises after the bounded attempts.  No safety predicate
    is relaxed.
    """
    errors = []
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            status = client.poll()
        except BaseException as exc:  # noqa: BLE001 - transient retry
            errors.append('poll %d: %s: %s'
                          % (attempt, type(exc).__name__, exc))
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        snapshot = status.to_dict()
        if snapshot.get('online') is not True:
            errors.append('poll %d: online=%r error=%r'
                          % (attempt, snapshot.get('online'),
                             snapshot.get('error')))
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        if snapshot.get('stale') is not False:
            raise ExecutionError(
                'board status is stale (%r); refusing after %d poll(s)'
                % (snapshot.get('stale'), attempt))
        if not _safe_snapshot(status):
            raise ExecutionError(
                'board is not in the safe idle state after %d poll(s): %r'
                % (attempt, snapshot))
        return status, attempt, errors
    raise ExecutionError('board status unavailable after %d poll(s): %s'
                         % (attempts, errors))


def _poll_status_any_with_retry(client, attempts=3, delay_s=0.2):
    """Bounded retry for a PURE-READ status poll (diagnostics/teardown).

    This retries only transport-level transients (exception/timeout, or an
    offline snapshot); it deliberately does NOT apply a safety predicate here,
    because teardown must be able to observe an unsafe/armed state and then
    disarm it.  Callers judge the returned snapshot.
    """
    errors = []
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            status = client.poll()
        except BaseException as exc:  # noqa: BLE001 - transient retry
            errors.append('poll %d: %s: %s'
                          % (attempt, type(exc).__name__, exc))
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        snapshot = status.to_dict()
        if snapshot.get('online') is not True:
            errors.append('poll %d: online=%r error=%r'
                          % (attempt, snapshot.get('online'),
                             snapshot.get('error')))
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        return status
    raise ExecutionError('board status unavailable after %d poll(s): %s'
                         % (attempts, errors))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n',
                    encoding='utf-8')
    return path


def _load_board_client(client_dir):
    client_dir = Path(client_dir)
    if not (client_dir / 'rf_status_client.py').is_file():
        raise ExecutionError('rf_status_client.py not found in %s' % client_dir)
    sys.path.insert(0, str(client_dir))
    from rf_status_client import RfStatusClient, StatusProtocolError
    return RfStatusClient, StatusProtocolError


def _new_board_client(args, RfStatusClient, require_capture=True):
    """Construct the real client and, with --record, wrap it read-only.

    The wrapper records every control_op/capture but delegates all behavior;
    it does not add commands, polls, retries or budget changes.
    """
    client = RfStatusClient(port=args.board_port)
    if not getattr(args, 'record', False):
        return client, None
    recorder = capture_record.CaptureRecorder(require_capture=require_capture)
    return capture_record.RecordingClient(client, recorder), recorder


def _load_record_context(args):
    path = getattr(args, 'record_context', None)
    if path is None:
        raise ExecutionError('--record requires --record-context JSON')
    try:
        context = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ExecutionError('cannot read --record-context: %s' % exc)
    required = ('session', 'boot_id', 'gain_index', 'rx_attenuation_mdb',
                'release_id', 'config_revision', 'waveform')
    missing = [key for key in required
               if key not in context or context[key] is None]
    if missing:
        raise ExecutionError('--record-context missing: ' + ', '.join(missing))
    return context


def _finish_capture_record(recorder, args, cfg, out_dir, report):
    """Write the complete capture record at session end (record mode only)."""
    if recorder is None:
        return None
    context = _load_record_context(args)
    readback = report.get('pre_readback') or report.get('pre_tx_readback') or {}
    board = cfg['board']
    expected = {
        'lo_hz': board['lo_hz'],
        'sample_rate_hz': board['sample_rate_hz'],
        'attenuation_mdB': board['attenuation_mdb'],
        'amplitude': board['amplitude'],
        'bram_words': board['bram_words'],
    }
    actual = dict(expected)
    actual_atten = readback.get('attenuation_mdB')
    if actual_atten is None:
        actual_atten = context.get('tx_attenuation_mdb')
    verified = readback.get('hardware_verified')
    if verified is None:
        verified = context.get('hardware_verified')
    if actual_atten is not None:
        actual['attenuation_mdB'] = actual_atten
    consistent = (verified is True
                  and actual_atten == expected['attenuation_mdB'])
    wave_ctx = context['waveform']
    recorder.set_waveform(
        truth=wave_ctx.get('truth', args.mode),
        parameters={
            'amplitude': wave_ctx.get('amplitude', expected['amplitude']),
            'physical_max_code': wave_ctx.get('physical_max_code'),
            'seed': wave_ctx.get('seed'),
            'period_samples': wave_ctx.get('period_samples', expected['bram_words']),
            'quantization': wave_ctx.get('quantization'),
        },
        expected_config=expected,
        actual_config=actual,
        consistent=consistent,
        notes=['runner-derived expected/actual; consistent requires configure readback'])
    recorder.set_actual_config(
        rf_config=dict(board),
        tx_attenuation_mdb=actual_atten,
        rx_attenuation_mdb=context['rx_attenuation_mdb'],
        gain_index=context['gain_index'],
        chip_tx_attenuation_mdb=wave_ctx.get('chip_tx_attenuation_mdb'),
        sample_rate_hz=board['sample_rate_hz'],
        samples=board['bram_words'],
        ratio=0,
        release_id=context['release_id'],
        config_revision=context['config_revision'],
        session=context['session'],
        boot_id=context['boot_id'])
    return recorder.write(out_dir / 'capture-record.json')


def _safe_snapshot(status):
    """One definition of "safe idle", shared with --preflight."""
    ok, _problems, _detail = readback.board_idle_gate(status)
    return ok


def _board_args(cfg, attenuation_mdb=None):
    b = cfg['board']
    return {
        'lo_hz': b['lo_hz'],
        'sample_rate_hz': b['sample_rate_hz'],
        'rx_bandwidth_hz': b['rx_bandwidth_hz'],
        'tx_bandwidth_hz': b['tx_bandwidth_hz'],
        'attenuation_mdB': (b['attenuation_mdb'] if attenuation_mdb is None
                            else attenuation_mdb),
        'phy_mode_1r1t': b['phy_mode_1r1t'],
        'idelay_en': b['idelay_en'],
        'idelay_tap': b['idelay_tap'],
    }


def _init_args(cfg):
    b = cfg['board']
    return {'lo_hz': b['lo_hz'],
            'sample_rate_hz': b['sample_rate_hz'],
            'attenuation_mdB': b['attenuation_mdb'],
            'phy_mode_1r1t': b['phy_mode_1r1t']}


def _board_preflight(client, token, cfg):
    global _LAST_PREFLIGHT_POLL
    status, attempts, errors = _poll_status_with_retry(client)
    _LAST_PREFLIGHT_POLL = {'attempts': attempts, 'errors': list(errors),
                            'snapshot': status.to_dict()}
    if status.release_id is None:
        raise ExecutionError('board did not report a release id')
    _arm_if_needed(client, token)
    status = _poll_armed_with_retry(client)
    return status


def _configure_and_check(client, cfg, attenuation_mdb=None):
    result = client.control_op('rf.configure', _board_args(cfg, attenuation_mdb))
    problems = planner.check_configure_readback(result, cfg)
    if problems:
        raise ExecutionError('configure readback gate failed: ' + '; '.join(problems))
    return result


def _arm_if_needed(client, token, attempts=5, delay_s=0.2):
    """Arm only after the daemon re-advertises `control.arm`.

    The daemon stops advertising `control.arm` while it is armed, and after a
    disarm it may briefly report armed=false before re-advertising the op.
    Sending arm in that gap makes the frozen client refuse the operation
    ("did not advertise"), which aborted real package #2 after session 0.
    Wait for an online, non-stale, safe snapshot whose control_ops contain
    `control.arm`; retry a "did not advertise" refusal as transient.  Persistent
    absence is a hard failure; no predicate is relaxed.
    """
    errors = []
    evidence = {'attempts': 0, 'polls': [], 'errors': errors}
    global _LAST_ARM_WAIT
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        evidence['attempts'] = attempt
        try:
            status = client.poll()
        except BaseException as exc:  # noqa: BLE001 - transient retry
            errors.append('attempt %d poll: %s: %s'
                          % (attempt, type(exc).__name__, exc))
            evidence['polls'].append({'attempt': attempt, 'error':
                                      '%s: %s' % (type(exc).__name__, exc)})
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        snapshot = status.to_dict()
        evidence['polls'].append({
            'attempt': attempt,
            'online': snapshot.get('online'),
            'stale': snapshot.get('stale'),
            'armed': snapshot.get('armed'),
            'control_ops': list(snapshot.get('control_ops') or ())})
        if snapshot.get('online') is not True:
            errors.append('attempt %d: online=%r error=%r'
                          % (attempt, snapshot.get('online'),
                             snapshot.get('error')))
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        if snapshot.get('stale') is not False:
            raise ExecutionError(
                'board status is stale while arming (%r); refusing after '
                '%d attempt(s)' % (snapshot.get('stale'), attempt))
        if not _safe_snapshot(status):
            evidence['unsafe_snapshot'] = snapshot
            _LAST_ARM_WAIT = evidence
            raise ExecutionError(
                'board is not in the safe idle state while arming after %d '
                'attempt(s): %r' % (attempt, snapshot))
        if getattr(status, 'armed', False):
            _LAST_ARM_WAIT = evidence
            return status
        ops = snapshot.get('control_ops') or ()
        if 'control.arm' not in ops:
            errors.append('attempt %d: armed=false and control.arm not '
                          'advertised (ops=%r)' % (attempt, list(ops)))
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        try:
            client.control_arm(token)
        except BaseException as exc:  # noqa: BLE001 - race on advertisement
            message = str(exc)
            if 'advertise' in message or 'not advertised' in message:
                errors.append('attempt %d: control.arm refused as unadvertised: '
                              '%s' % (attempt, message))
                if attempt < attempts:
                    time.sleep(delay_s)
                continue
            raise
        try:
            status = client.poll()
        except BaseException as exc:  # noqa: BLE001 - transient retry
            errors.append('attempt %d post-arm poll: %s: %s'
                          % (attempt, type(exc).__name__, exc))
            evidence['polls'].append({'attempt': attempt, 'phase': 'post-arm',
                                      'error': '%s: %s'
                                      % (type(exc).__name__, exc)})
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        post_snapshot = status.to_dict()
        evidence['polls'].append({
            'attempt': attempt, 'phase': 'post-arm',
            'online': post_snapshot.get('online'),
            'stale': post_snapshot.get('stale'),
            'armed': post_snapshot.get('armed'),
            'io_ok': (post_snapshot.get('pl_status') or {}).get('io_ok'),
            'tx_source_selected': ((post_snapshot.get('pl_status') or {})
                                   .get('tx') or {}).get('source_selected'),
            'rx_armed': ((post_snapshot.get('pl_status') or {})
                         .get('rx') or {}).get('armed'),
            'control_ops': list(post_snapshot.get('control_ops') or ())})
        if post_snapshot.get('online') is not True:
            errors.append('attempt %d: post-arm online=%r'
                          % (attempt, post_snapshot.get('online')))
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        if post_snapshot.get('stale') is not False:
            evidence['unsafe_snapshot'] = post_snapshot
            _LAST_ARM_WAIT = evidence
            raise ExecutionError(
                'board status is stale after arm (%r); refusing after %d '
                'poll(s)' % (post_snapshot.get('stale'), attempt))
        if not _safe_snapshot(status):
            evidence['unsafe_snapshot'] = post_snapshot
            _LAST_ARM_WAIT = evidence
            raise ExecutionError(
                'board is not in the safe idle state after arm (%d poll(s)): '
                '%r' % (attempt, post_snapshot))
        if status.armed:
            _LAST_ARM_WAIT = evidence
            return status
        errors.append('attempt %d: arm acknowledged but armed=false'
                      % attempt)
        if attempt < attempts:
            time.sleep(delay_s)
    _LAST_ARM_WAIT = evidence
    raise ExecutionError(
        'board did not advertise/confirm control.arm after %d attempt(s): %s'
        % (attempts, errors))


def _poll_armed_with_retry(client, attempts=5, delay_s=0.2):
    """Bounded retry for the post-arm `armed=true` confirmation poll.

    A transient timeout after arm must not abort the whole package (real
    package #3 failure).  Accept only online, non-stale, safe snapshots with
    armed=true; stale/unsafe is a hard failure immediately; persistent timeout
    or persistent armed=false raises with attempts/errors.  No predicate is
    relaxed.
    """
    errors = []
    polls = []
    attempts = max(1, int(attempts))
    evidence = {'attempts': 0, 'polls': polls, 'errors': errors}
    global _LAST_POST_ARM_POLL
    for attempt in range(1, attempts + 1):
        evidence['attempts'] = attempt
        try:
            status = client.poll()
        except BaseException as exc:  # noqa: BLE001 - transient retry
            errors.append('poll %d: %s: %s'
                          % (attempt, type(exc).__name__, exc))
            polls.append({'attempt': attempt, 'error':
                          '%s: %s' % (type(exc).__name__, exc)})
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        snapshot = status.to_dict()
        polls.append({'attempt': attempt,
                      'online': snapshot.get('online'),
                      'stale': snapshot.get('stale'),
                      'armed': snapshot.get('armed'),
                      'control_ops': list(snapshot.get('control_ops') or ())})
        if snapshot.get('online') is not True:
            errors.append('poll %d: online=%r error=%r'
                          % (attempt, snapshot.get('online'),
                             snapshot.get('error')))
            if attempt < attempts:
                time.sleep(delay_s)
            continue
        if snapshot.get('stale') is not False:
            raise ExecutionError(
                'board status is stale after arm (%r); refusing after %d '
                'poll(s)' % (snapshot.get('stale'), attempt))
        if not _safe_snapshot(status):
            raise ExecutionError(
                'board is not in the safe idle state after arm (%d poll(s)): '
                '%r' % (attempt, snapshot))
        if getattr(status, 'armed', False):
            _LAST_POST_ARM_POLL = evidence
            return status
        errors.append('poll %d: armed=false after arm' % attempt)
        if attempt < attempts:
            time.sleep(delay_s)
    _LAST_POST_ARM_POLL = evidence
    raise ExecutionError(
        'board did not confirm armed=true after %d poll(s): %s'
        % (attempts, errors))


def _stop_tx_and_configure(client, cfg, token):
    """Stop the source, reconnect, arm if needed and re-assert 46 dB; do not disarm.

    tx.stop drops the retained client session.  The daemon is usually still
    armed after tx.stop, so re-arm only when the live poll says it is not
    armed before rf.configure (configure requires READY and armed control).
    """
    client.control_op('tx.stop', {})
    _arm_if_needed(client, token)
    return _configure_and_check(client, cfg)


def _final_safe_stop(client, cfg, token):
    """Force the vendor ceiling, read it back, confirm TX off, then disarm."""
    try:
        client.control_op('tx.stop', {})
    except Exception:
        pass
    _arm_if_needed(client, token)
    rb = client.control_op('rf.configure', _board_args(
        cfg, planner.BOARD_SAFE_ATTENUATION_MDB))
    if rb.get('attenuation_mdB') != planner.BOARD_SAFE_ATTENUATION_MDB:
        raise ExecutionError('post-stop attenuation readback %r != 89750'
                             % (rb.get('attenuation_mdB'),))
    if rb.get('hardware_verified') is not True:
        raise ExecutionError('post-stop hardware_verified is not true')
    status = _poll_status_any_with_retry(client)
    pl = status.pl_status
    state = {'attenuation_mdb': rb.get('attenuation_mdB'),
             'hardware_verified': rb.get('hardware_verified'),
             'io_ok': pl.io_ok,
             'tx_source_selected': pl.tx.source_selected,
             'rx_armed': pl.rx.armed}
    problems = planner.check_safe_stop(state, cfg)
    if problems:
        raise ExecutionError('post-stop gate failed: ' + '; '.join(problems))
    client.control_disarm()
    return state


def _load_tone(client, cfg, bin_index):
    b = cfg['board']
    payload, meta = planner.tone_payload(
        sample_rate_hz=b['sample_rate_hz'], samples=b['bram_words'],
        bin_index=bin_index, amplitude=b['amplitude'])
    decl = {'samples': meta['samples'], 'bytes': meta['bytes'],
            'format': meta['format'], 'direction': 'tx', 'wrap': True}
    client.control_op('tx.load', decl, payload)
    return meta


def _pluto_config(cfg, tone_hz=None, sample_rate=None):
    p = cfg['pluto']
    return {
        'serial': p['serial'],
        'center_hz': p['center_hz'],
        'sample_rate': p['sample_rate'] if sample_rate is None else sample_rate,
        'bandwidth_hz': p['bandwidth_hz'],
        'tone_hz': p['tone_hz'][0] if tone_hz is None else tone_hz,
        'amplitude': p['amplitude'],
        'tx_attenuation_db': p['tx_attenuation_db'],
        'rx_gain_db': p['rx_gain_db'],
        'duration_s': p['duration_s'],
        'capture_samples': p['capture_samples'],
    }


def _pluto_bpsk_config(cfg, seed=None):
    """The gated 6 MSPS/sps2 BPSK playback config for radio_test tx-bpsk."""
    p = cfg['pluto']
    cfg_out = _pluto_config(cfg,
                            sample_rate=cfg['bpsk']['pluto_sample_rate_hz'])
    cfg_out['duration_s'] = min(float(p['duration_s']),
                                B_BPSK_MAX_TX_SECONDS)
    cfg_out['tone_hz'] = 0.0
    cfg_out['prbs7_seed'] = int(1 if seed is None else seed)
    return cfg_out


def _radio_env(radioconda):
    env = dict(os.environ)
    prefix = str(Path(radioconda))
    env['PATH'] = prefix + os.pathsep + os.path.join(prefix, 'Library', 'bin') + os.pathsep + env.get('PATH', '')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def _parse_radio_json(text):
    """radio_test prints one JSON object, but GNU Radio may prefix underflow U."""
    start = text.find('{')
    if start < 0:
        raise ExecutionError('radio_test produced no JSON object')
    decoder = json.JSONDecoder()
    value, _end = decoder.raw_decode(text[start:])
    return value


def _run_radio(mode, config_path, radioconda, path_confirmed=None,
               ready_file=None, background=False):
    python = Path(radioconda) / 'python.exe'
    if not python.is_file():
        raise ExecutionError('Radioconda python not found: %s' % python)
    cmd = [str(python), '-X', 'utf8', '-B', str(RADIO_TEST), mode,
           '--config', str(config_path)]
    if path_confirmed:
        cmd += ['--path-confirmed', path_confirmed]
    if ready_file:
        cmd += ['--ready-file', str(ready_file)]
    env = _radio_env(radioconda)
    if background:
        return subprocess.Popen(cmd, cwd=str(PROJECT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8')
    proc = subprocess.run(cmd, cwd=str(PROJECT), env=env, capture_output=True,
                          text=True, encoding='utf-8',
                          timeout=float(60))
    if proc.returncode != 0:
        raise ExecutionError('radio_test %s failed (%d): %s' %
                             (mode, proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
    return _parse_radio_json(proc.stdout)


def _pluto_safe_off(serial, iio_module=None):
    """Writes the safe off state and reads it back; does not change the LO.

    The readback is parsed strictly: real libiio returns '-89.750000 dB', and
    a bare float() of that text is a ValueError, not a readback.
    """
    import iio as libiio
    found = libiio.scan_contexts()
    matches = [uri for uri, desc in found.items()
               if 'serial=' + serial.lower() in desc.lower()]
    if len(matches) != 1:
        raise ExecutionError('expected exactly one Pluto: %r' % (found,))
    ctx = libiio.Context(matches[0])
    ctx.set_timeout(3000)
    if ctx.attrs.get('hw_serial', '').lower() != serial.lower():
        raise ExecutionError('Pluto identity mismatch')
    phy = ctx.find_device('ad9361-phy')
    tx = phy.find_channel('voltage0', True)
    lo = phy.find_channel('altvoltage1', True)
    tx.attrs['hardwaregain'].value = '%.6f' % planner.PLUTO_SAFE_HARDWARE_GAIN_DB
    lo.attrs['powerdown'].value = '1'
    gain_raw = tx.attrs['hardwaregain'].value
    powerdown_raw = lo.attrs['powerdown'].value
    out = {'uri': matches[0], 'serial': ctx.attrs.get('hw_serial'),
           'hardwaregain_db': None, 'hardwaregain_raw': gain_raw,
           'tx_powerdown': None, 'tx_powerdown_raw': powerdown_raw}
    del phy
    ctx = None
    try:
        out['hardwaregain_db'] = readback.require_db(
            gain_raw, planner.PLUTO_SAFE_HARDWARE_GAIN_DB,
            'Pluto TX hardwaregain after safe-off')
        out['tx_powerdown'] = readback.parse_powerdown(
            powerdown_raw, 'Pluto TX LO powerdown after safe-off')
    except readback.ReadbackError as exc:
        raise ExecutionError('Pluto safe-off readback refused: %s' % exc)
    if out['tx_powerdown'] != '1':
        raise ExecutionError('Pluto safe-off readback mismatch: %r' % out)
    return out


def _read_iq(result):
    result_dir = Path(result['result_dir'])
    iq_path = result_dir / 'iq.npy'
    if not iq_path.is_file():
        raise ExecutionError('radio_test did not save iq.npy in %s' % result_dir)
    return np.load(str(iq_path))


def _check_pluto_post(result):
    """Gate the stop state of a radio_test receipt.

    The field positions are the real ones: powerdown lives on the TX LO channel
    (altvoltage1_out) and hardwaregain on the TX chain channel (voltage0_out).
    Reading the gain from the LO channel rejects a correct safe-off.
    """
    try:
        return readback.pluto_post_readback(
            result, expected_gain_db=planner.PLUTO_SAFE_HARDWARE_GAIN_DB)
    except readback.ReadbackError as exc:
        raise ExecutionError('Pluto post readback refused: %s' % exc)


def _capture_pluto_rx(cfg, radioconda, out_dir, tag, expected_offset_hz=None):
    cfg_path = out_dir / ('pluto-rx-%s.json' % tag)
    _write_json(cfg_path, _pluto_config(cfg))
    result = _run_radio('rx', cfg_path, radioconda)
    iq = _read_iq(result)
    expected = (float(cfg['pluto']['tone_hz'][0]) if expected_offset_hz is None
                else float(expected_offset_hz))
    analysis = planner.analyse_tone(
        iq, float(cfg['pluto']['sample_rate']),
        expected_offset_hz=expected,
        tolerance_hz=20000.0)
    # The Pluto RX stop state is gated first: even a clipped capture must not
    # skip the -89.75/powerdown=1 readback of the capture it just finished.
    post = _check_pluto_post(result)
    if analysis.get('clipped'):
        raise ExecutionError(
            'Pluto RX %s window is CLIPPED (abs_max_iq=%.6f, '
            'near-full-scale fraction=%.6f, hard-clip fraction=%.6f); '
            'stopping the direction-A session - no retry, no re-capture'
            % (tag, analysis.get('abs_max_iq', -1.0),
               analysis.get('full_scale_fraction', -1.0),
               analysis.get('hard_clip_fraction', -1.0)))
    return {'tag': tag, 'result': result, 'analysis': analysis,
            'post_readback': post}


def _capture_board_rx(client, out_dir, tag, expected_offset_hz=None):
    frame = client.capture({'samples': planner.BOARD_BRAM_WORDS, 'ratio': 0})
    (out_dir / ('board-%s.iq' % tag)).write_bytes(frame.payload)
    _write_json(out_dir / ('board-%s.json' % tag), frame.to_dict())
    iq = np.frombuffer(frame.payload, dtype='<i2').reshape(-1, 2)
    z = iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)
    spectrum = np.abs(np.fft.fftshift(np.fft.fft(
        (z - z.mean()) * np.hanning(len(z))))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(z), 1.0 / frame.sample_rate_hz))
    index = int(np.argmax(spectrum))
    spectrum_db = (10.0 * np.log10(spectrum + 1e-30)).astype(float).tolist()
    return {'tag': tag, 'frame': frame.to_dict(),
            'peak_offset_hz': float(freqs[index]),
            'peak_bin_index': index,
            'bin_hz': float(frame.sample_rate_hz / len(z)),
            'expected_offset_hz': (None if expected_offset_hz is None
                                   else float(expected_offset_hz)),
            'spectrum_db': spectrum_db,
            'peak_to_median_db': float(10.0 * np.log10(
                (spectrum[index] + 1e-30) / (np.median(spectrum) + 1e-30))),
            'clipped': bool(np.any(iq <= -2048) or np.any(iq >= 2047)),
            'clipped_samples': int(np.count_nonzero(
                (iq <= -2048) | (iq >= 2047)))}


def _board_tx_off_snapshot(client, tag, when):
    """Poll the board and refuse any window where the board TX could be on.

    A transient timeout is retried a bounded number of times; a stale/unsafe
    snapshot is refused immediately and no predicate is relaxed.
    """
    errors = []
    for attempt in range(1, 4):
        try:
            status = client.poll()
        except BaseException as exc:  # noqa: BLE001 - transient retry
            errors.append('poll %d: %s: %s'
                          % (attempt, type(exc).__name__, exc))
            if attempt < 3:
                time.sleep(0.2)
            continue
        pl = status.pl_status
        snap = {'tag': tag, 'when': when,
                'online': bool(getattr(status, 'online', False)),
                'stale': bool(getattr(status, 'stale', False)),
                'armed': bool(getattr(status, 'armed', False)),
                'io_ok': bool(pl.io_ok),
                'tx_source_selected': bool(pl.tx.source_selected),
                'rx_armed': bool(pl.rx.armed),
                'poll_attempts': attempt,
                'poll_errors': list(errors)}
        if snap['online'] is not True:
            errors.append('poll %d: online=false' % attempt)
            if attempt < 3:
                time.sleep(0.2)
            continue
        if snap['stale'] is not False:
            raise ExecutionError('board status is stale during direction B '
                                 '(%s %s); refusing' % (tag, when))
        if snap['tx_source_selected'] is not False:
            raise ExecutionError(
                'board TX source is selected during direction B (%s %s); '
                'refusing' % (tag, when))
        if snap['io_ok'] is not True:
            raise ExecutionError(
                'board io_ok is not true during direction B (%s %s); refusing'
                % (tag, when))
        return snap
    raise ExecutionError('board TX-off poll unavailable after 3 polls '
                         '(%s %s): %s' % (tag, when, errors))


def _b_capture(client, cfg, out_dir, tag, report, expected_offset_hz=None):
    before = _board_tx_off_snapshot(client, tag, 'before')
    frame = _capture_board_rx(client, out_dir, tag,
                             expected_offset_hz=expected_offset_hz)
    after = _board_tx_off_snapshot(client, tag, 'after')
    frame['board_tx_off_before'] = before
    frame['board_tx_off_after'] = after
    report['budget']['board_captures'] += 1
    if report['budget']['board_captures'] > B_CAPTURE_WINDOWS:
        raise ExecutionError('BUDGET: board captures exceed the %d-window limit'
                             % B_CAPTURE_WINDOWS)
    return frame


def _stop_pluto_proc(proc):
    """Stop a Pluto TX child: terminate, wait, then kill; never just kill."""
    evidence = {'terminate_sent': False, 'kill_sent': False,
                'returncode_before': None, 'returncode_after': None}
    if proc is None:
        return evidence
    try:
        evidence['returncode_before'] = proc.poll()
    except BaseException:
        evidence['returncode_before'] = None
    if evidence['returncode_before'] is None:
        try:
            proc.terminate()
            evidence['terminate_sent'] = True
        except BaseException:
            pass
        try:
            proc.wait(timeout=5.0)
        except BaseException:
            try:
                proc.kill()
                evidence['kill_sent'] = True
            except BaseException:
                pass
            try:
                proc.wait(timeout=5.0)
            except BaseException:
                pass
    try:
        evidence['returncode_after'] = proc.poll()
    except BaseException:
        evidence['returncode_after'] = None
    return evidence


def _pluto_safe_stop_after_tx(proc, serial):
    """Stop the child and then read the safe-off state back independently."""
    stop = _stop_pluto_proc(proc)
    try:
        readback = _pluto_safe_off(serial)
    except BaseException as exc:
        return {'confirmed': False, 'stop': stop,
                'error': '%s: %s' % (type(exc).__name__, exc)}
    return {'confirmed': True, 'stop': stop, 'readback': readback}


def _board_teardown(client):
    """Keep the board RX-only on failure: disarm if armed, confirm TX off."""
    evidence = {'armed_before': None, 'armed_after': None, 'disarmed': False,
                'tx_source_selected': None, 'io_ok': None, 'error': None}
    try:
        status = _poll_status_any_with_retry(client)
        evidence['armed_before'] = bool(getattr(status, 'armed', False))
        if evidence['armed_before']:
            client.control_disarm()
        status = _poll_status_any_with_retry(client)
        evidence['armed_after'] = bool(getattr(status, 'armed', False))
        pl = status.pl_status
        evidence['tx_source_selected'] = bool(pl.tx.source_selected)
        evidence['io_ok'] = bool(pl.io_ok)
        evidence['disarmed'] = evidence['armed_after'] is False
    except BaseException as exc:
        evidence['error'] = '%s: %s' % (type(exc).__name__, exc)
    return evidence


def _poll_final_status(client, attempts=3, delay_s=0.2):
    """Read the post-disarm status, retrying a transient timeout.

    One timed-out poll is not evidence that the board TX is on: retry a bounded
    number of times and report UNKNOWN if every attempt failed.
    """
    errors = []
    for attempt in range(1, attempts + 1):
        try:
            status = client.poll()
            snapshot = status.to_dict()
            if snapshot.get('online') is True and snapshot.get('stale') is not True:
                if errors:
                    snapshot['retried_after'] = list(errors)
                return snapshot, attempt, errors
            errors.append('poll %d: online=%r error=%r'
                          % (attempt, snapshot.get('online'),
                             snapshot.get('error')))
        except BaseException as exc:
            errors.append('poll %d: %s: %s'
                          % (attempt, type(exc).__name__, exc))
        if attempt < attempts:
            time.sleep(delay_s)
    return None, attempts, errors


def _run_a(args, cfg, out_dir):
    RfStatusClient, StatusProtocolError = _load_board_client(args.client_dir)
    token = args.token_file.read_text(encoding='utf-8').strip()
    if not token:
        raise ExecutionError('control token file is empty')
    pluto = _pluto_safe_off(pluto_serial(cfg))
    client, recorder = _new_board_client(args, RfStatusClient,
                                         require_capture=False)
    report = {'direction': 'A', 'pluto_preflight': pluto, 'windows': []}
    try:
        _board_preflight(client, token, cfg)
        client.control_op('rf.init', _init_args(cfg))
        rb = _configure_and_check(client, cfg)
        report['pre_tx_readback'] = rb

        # OFF before any board transmit.
        report['windows'].append(_capture_pluto_rx(cfg, args.radioconda, out_dir, 'OFF_PRE'))

        # tone 1
        _load_tone(client, cfg, cfg['board']['tone_bins'][0])
        client.control_op('tx.start', {})
        status = client.poll()
        if status.pl_status.tx.source_selected is not True:
            raise ExecutionError('board TX source not confirmed on for tone1')
        report['windows'].append(_capture_pluto_rx(
            cfg, args.radioconda, out_dir, 'TONE1',
            expected_offset_hz=cfg['board']['tone_bins'][0] *
            cfg['board']['sample_rate_hz'] / cfg['board']['bram_words']))
        _stop_tx_and_configure(client, cfg, token)

        # tone 2: the stop above dropped the session, so re-arm and configure.
        _load_tone(client, cfg, cfg['board']['tone_bins'][1])
        client.control_op('tx.start', {})
        status = client.poll()
        if status.pl_status.tx.source_selected is not True:
            raise ExecutionError('board TX source not confirmed on for tone2')
        report['windows'].append(_capture_pluto_rx(
            cfg, args.radioconda, out_dir, 'TONE2',
            expected_offset_hz=cfg['board']['tone_bins'][1] *
            cfg['board']['sample_rate_hz'] / cfg['board']['bram_words']))
        report['post_tx_readback'] = _final_safe_stop(client, cfg, token)

        # OFF after the source and the ceiling attenuation are confirmed.
        report['windows'].append(_capture_pluto_rx(cfg, args.radioconda, out_dir, 'OFF_POST'))
        report['final_status'] = _poll_status_any_with_retry(client).to_dict()
        report['passed'] = _evaluate_a(report)
        _finish_capture_record(recorder, args, cfg, out_dir, report)
        return report
    except BaseException as exc:  # noqa: BLE001 - the board TX must be stopped
        # Any failure after the session started (CLIPPED, capture error, Ctrl-C)
        # must try the existing safe-stop primitive before the original
        # exception is re-raised.  No retry, no extra capture.
        try:
            state = _final_safe_stop(client, cfg, token)
            cleanup = {
                'cleanup_required': False,
                'cleanup': state,
                'cleanup_note': ('automatic safe stop after the failure: '
                                 'tx.stop -> configure 89750 -> TX off / RX '
                                 'disarmed -> disarm'),
                'original_error': '%s: %s' % (type(exc).__name__, exc),
            }
        except BaseException as cleanup_exc:  # noqa: BLE001
            cleanup = {
                'cleanup_required': True,
                'cleanup_error': '%s: %s' % (type(cleanup_exc).__name__,
                                             cleanup_exc),
                'cleanup_note': ('automatic safe stop failed or could not be '
                                 'confirmed; disconnect the board TX1 and Pluto '
                                 'RX coax (and Pluto USB) on site'),
                'original_error': '%s: %s' % (type(exc).__name__, exc),
            }
        try:
            _write_json(out_dir / 'failure-cleanup.json', cleanup)
        except Exception:
            pass
        try:
            exc.cleanup_report = cleanup
        except Exception:
            pass
        raise
    finally:
        try:
            client.close()
        except Exception:
            pass


def _evaluate_a(report):
    windows = {w['tag']: w for w in report['windows']}
    # Any clipped Pluto RX window fails the session, even if a caller bypassed
    # the capture-time stop: the same computed flag Stage 0 uses is the gate.
    if any(w.get('analysis', {}).get('clipped') for w in report['windows']):
        return False
    off = windows.get('OFF_PRE', {}).get('analysis', {})
    t1 = windows.get('TONE1', {}).get('analysis', {})
    t2 = windows.get('TONE2', {}).get('analysis', {})
    if not all(isinstance(x, dict) and 'peak_offset_hz' in x for x in (t1, t2)):
        return False
    delta = t2['peak_offset_hz'] - t1['peak_offset_hz']
    expected = 205078.125 - 102539.0625
    return bool(abs(delta - expected) <= 2000.0 and
                t1['peak_to_median_db'] > 20.0 and
                t2['peak_to_median_db'] > 20.0 and
                (t1['peak_to_median_db'] - off.get('peak_to_median_db', 0.0)) > 10.0)


def _run_b(args, cfg, out_dir):
    RfStatusClient, StatusProtocolError = _load_board_client(args.client_dir)
    if args.sessions != 1 or args.extended:
        raise ExecutionError('BUDGET: direction B is exactly one session / '
                             'four windows; refusing %r sessions'
                             % (args.sessions,))
    token = args.token_file.read_text(encoding='utf-8').strip()
    if not token:
        raise ExecutionError('control token file is empty')
    serial = pluto_serial(cfg)
    client, recorder = _new_board_client(args, RfStatusClient)
    report = {'direction': 'B', 'pluto_preflight': _pluto_safe_off(serial),
              'windows': [],
              'budget': {'sessions': 1, 'board_inits': 0, 'board_configures': 0,
                         'board_captures': 0, 'pluto_tx_starts': 0,
                         'board_tx_starts': 0, 'pluto_tx_seconds_total': 0.0}}
    current_proc = None
    try:
        _board_preflight(client, token, cfg)
        report['budget']['board_inits'] += 1
        client.control_op('rf.init', _init_args(cfg))
        rb = _configure_and_check(client, cfg)
        report['budget']['board_configures'] += 1
        report['pre_readback'] = rb

        report['windows'].append(_b_capture(client, cfg, out_dir, 'OFF_PRE', report))
        for tone, tag in ((cfg['pluto']['tone_hz'][0], 'TONE1'),
                          (cfg['pluto']['tone_hz'][1], 'TONE2')):
            if report['budget']['pluto_tx_starts'] >= B_MAX_PLUTO_TX_STARTS:
                raise ExecutionError('BUDGET: Pluto TX starts would exceed %d'
                                     % B_MAX_PLUTO_TX_STARTS)
            spent = float(report['budget']['pluto_tx_seconds_total'])
            requested = float(cfg['pluto']['duration_s'])
            if spent + requested > B_MAX_PLUTO_TX_SECONDS:
                raise ExecutionError(
                    'BUDGET: the next Pluto TX (%.3f s) would exceed the %.1f s '
                    'cumulative limit (%.3f s already used)'
                    % (requested, B_MAX_PLUTO_TX_SECONDS, spent))
            _board_tx_off_snapshot(client, tag, 'before_tx')

            cfg_path = out_dir / ('pluto-tx-%s.json' % tag)
            _write_json(cfg_path, _pluto_config(cfg, tone_hz=tone))
            ready = out_dir / ('pluto-tx-%s.ready' % tag)
            proc = _run_radio('tx', cfg_path, args.radioconda,
                              path_confirmed=args.path_confirmed,
                              ready_file=ready, background=True)
            current_proc = proc
            report['budget']['pluto_tx_starts'] += 1
            started = time.time()
            deadline = time.time() + B_PLUTO_READY_TIMEOUT_S
            while not ready.is_file() and time.time() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            if not ready.is_file():
                raise ExecutionError(
                    'Pluto TX %s did not signal STREAM_STARTED within %.1f s'
                    % (tag, B_PLUTO_READY_TIMEOUT_S))

            report['windows'].append(_b_capture(
                client, cfg, out_dir, tag, report, expected_offset_hz=tone))
            try:
                out, err = proc.communicate(timeout=B_PLUTO_EXIT_TIMEOUT_S)
            except subprocess.TimeoutExpired as exc:
                raise ExecutionError(
                    'Pluto TX %s did not exit within %.1f s'
                    % (tag, B_PLUTO_EXIT_TIMEOUT_S)) from exc
            current_proc = None
            if proc.returncode != 0:
                raise ExecutionError('Pluto TX %s failed (%d): %s'
                                     % (tag, proc.returncode,
                                        (err or out)[-2000:]))
            tx_result = _parse_radio_json(out)
            post = _check_pluto_post(tx_result)
            # Gap 2: the child's own receipt is not enough - read the safe-off
            # state back through an independent libiio context after it exited.
            independent = _pluto_safe_off(serial)
            measured = float((tx_result.get('config') or {}).get('duration_s', 0.0))
            report['budget']['pluto_tx_seconds_total'] = spent + measured
            report['windows'].append({'tag': tag, 'pluto_tx': tx_result,
                                      'post_readback': post,
                                      'independent_readback': independent,
                                      'measured_tx_seconds': measured,
                                      'wall_seconds': round(time.time() - started, 3)})
            if report['budget']['pluto_tx_seconds_total'] > B_MAX_PLUTO_TX_SECONDS:
                raise ExecutionError(
                    'BUDGET: cumulative Pluto TX %.3f s exceeds the %.1f s limit'
                    % (report['budget']['pluto_tx_seconds_total'],
                       B_MAX_PLUTO_TX_SECONDS))

        report['windows'].append(_b_capture(client, cfg, out_dir, 'OFF_POST', report))
        report['pluto_final_readback'] = _pluto_safe_off(serial)
        client.control_disarm()
        final_status, poll_attempts, poll_errors = _poll_final_status(client)
        report['final_status'] = final_status
        report['final_status_unknown'] = final_status is None
        report['final_status_poll_attempts'] = poll_attempts
        report['final_status_poll_errors'] = poll_errors
        report['passed'] = _evaluate_b(report)
        _finish_capture_record(recorder, args, cfg, out_dir, report)
        return report
    except BaseException as exc:  # noqa: BLE001 - stop the Pluto TX child
        cleanup = {'original_error': '%s: %s' % (type(exc).__name__, exc)}
        if current_proc is not None:
            cleanup['pluto_tx'] = _pluto_safe_stop_after_tx(current_proc, serial)
            current_proc = None
            pluto_ok = bool(cleanup['pluto_tx'].get('confirmed'))
        else:
            try:
                cleanup['pluto_readback'] = _pluto_safe_off(serial)
                pluto_ok = True
            except BaseException as readback_exc:  # noqa: BLE001
                cleanup['pluto_error'] = ('%s: %s'
                                          % (type(readback_exc).__name__,
                                             readback_exc))
                pluto_ok = False
        cleanup['board'] = _board_teardown(client)
        board = cleanup['board']
        board_ok = (board.get('tx_source_selected') is False
                    and board.get('io_ok') is True
                    and board.get('disarmed') is True)
        cleanup['cleanup_required'] = not (pluto_ok and board_ok)
        if cleanup['cleanup_required']:
            cleanup['cleanup_note'] = (
                'automatic safe stop could not be fully confirmed; disconnect '
                'the Pluto TX and the board RX1 coax (and Pluto USB) on site')
        else:
            cleanup['cleanup_note'] = (
                'automatic safe stop after the direction-B failure: Pluto TX '
                'child stopped -> independent safe-off readback; board '
                'disarmed with TX off')
        try:
            _write_json(out_dir / 'failure-cleanup.json', cleanup)
        except Exception:
            pass
        try:
            exc.cleanup_report = cleanup
        except Exception:
            pass
        raise
    finally:
        try:
            client.close()
        except Exception:
            pass


def _final_status_problems(final):
    """B1 final safety predicate: control armed=false and RX armed=false too."""
    problems = []
    if not isinstance(final, dict):
        return ['final board status is not an object']
    if final.get('online') is not True:
        problems.append('final board online is not true')
    if final.get('stale') is True:
        problems.append('final board status is stale')
    if final.get('armed') is not False:
        problems.append('final board control armed is not false (%r)'
                        % (final.get('armed'),))
    pl = final.get('pl_status') or {}
    if pl.get('io_ok') is not True:
        problems.append('final board io_ok is not true')
    if (pl.get('tx') or {}).get('source_selected') is not False:
        problems.append('final board TX source is not off')
    if (pl.get('rx') or {}).get('armed') is not False:
        problems.append('final board RX armed is not false (%r)'
                        % ((pl.get('rx') or {}).get('armed'),))
    return problems


def _evaluate_b(report):
    """Direction-B gates from the captured board IQ (board RX is the truth).

    The two devices use independent references, so an absolute peak position
    is not a gate: each ON window is compared with the OFF windows in the SAME
    FFT bin, which cancels the common LO offset.  The two ON peaks must be
    seven 4096-point/60 MSPS bins apart (102.539 kHz) within one bin.
    """
    problems = []
    budget = report.get('budget') or {}
    windows = report.get('windows') or []
    frames = [w for w in windows if 'spectrum_db' in w]
    tx_windows = [w for w in windows if 'pluto_tx' in w]
    if len(frames) != 4:
        problems.append('expected 4 board IQ frames, got %d' % len(frames))
    if len(tx_windows) != 2:
        problems.append('expected 2 Pluto TX receipts, got %d' % len(tx_windows))
    expected_budget = {'sessions': 1, 'board_inits': 1, 'board_configures': 1,
                       'board_captures': 4, 'pluto_tx_starts': 2,
                       'board_tx_starts': 0}
    for key, want in expected_budget.items():
        if budget.get(key) != want:
            problems.append('budget %s=%r, required %r'
                            % (key, budget.get(key), want))
    seconds = budget.get('pluto_tx_seconds_total')
    if not isinstance(seconds, (int, float)) or seconds > B_MAX_PLUTO_TX_SECONDS:
        problems.append('budget Pluto TX seconds=%r exceeds %.1f'
                        % (seconds, B_MAX_PLUTO_TX_SECONDS))

    for frame in frames:
        tag = frame.get('tag')
        if frame.get('clipped'):
            problems.append('%s is clipped (%r samples)'
                            % (tag, frame.get('clipped_samples')))
        for key in ('board_tx_off_before', 'board_tx_off_after'):
            snapshot = frame.get(key)
            if not isinstance(snapshot, dict):
                problems.append('%s has no %s board poll' % (tag, key))
            elif snapshot.get('tx_source_selected') is not False:
                problems.append('%s %s: board TX source is selected'
                                % (tag, key))

    if report.get('final_status_unknown') is True:
        attempts = report.get('final_status_poll_attempts')
        errors = report.get('final_status_poll_errors') or []
        problems.append(
            'final board status UNKNOWN: %r post-disarm polls returned no '
            'online snapshot (last error: %s); this session does NOT assert '
            'TX-off/io_ok, an independent read-only poll is required'
            % (attempts, errors[-1] if errors else 'none'))
    else:
        problems.extend(_final_status_problems(
            report.get('final_status') or {}))

    final_readback = report.get('pluto_final_readback')
    if not isinstance(final_readback, dict):
        problems.append('Pluto final safe-off readback is missing')
    else:
        gain = final_readback.get('hardwaregain_db')
        if (not isinstance(gain, (int, float))
                or abs(float(gain) - planner.PLUTO_SAFE_HARDWARE_GAIN_DB) > 1e-6):
            problems.append('Pluto final hardwaregain=%r != %.2f'
                            % (gain, planner.PLUTO_SAFE_HARDWARE_GAIN_DB))
        if final_readback.get('tx_powerdown') != '1':
            problems.append('Pluto final powerdown=%r != 1'
                            % (final_readback.get('tx_powerdown'),))

    off = [f for f in frames if str(f.get('tag', '')).startswith('OFF')]
    on = [f for f in frames if str(f.get('tag', '')).startswith('TONE')]
    if len(off) == 2 and len(on) == 2:
        bin_hz = float(on[0].get('bin_hz') or (planner.BOARD_FS_HZ / 4096.0))
        for frame in on:
            index = frame.get('peak_bin_index')
            spectrum = frame.get('spectrum_db')
            if (not isinstance(index, int) or not isinstance(spectrum, list)
                    or not 0 <= index < len(spectrum)):
                problems.append('%s has no usable spectrum/bin' % frame.get('tag'))
                continue
            off_power = None
            for off_frame in off:
                off_spectrum = off_frame.get('spectrum_db')
                if isinstance(off_spectrum, list) and index < len(off_spectrum):
                    value = float(off_spectrum[index])
                    off_power = value if off_power is None else max(off_power, value)
            if off_power is None:
                problems.append('%s has no OFF same-bin reference'
                                % frame.get('tag'))
                continue
            delta_db = float(spectrum[index]) - off_power
            frame['on_off_same_bin_db'] = delta_db
            frame['on_off_reference_bin'] = index
            if delta_db < B_MIN_ON_OFF_DB:
                problems.append('%s ON/OFF same-bin %.2f dB < %.1f dB'
                                % (frame.get('tag'), delta_db, B_MIN_ON_OFF_DB))
            expected = frame.get('expected_offset_hz')
            if isinstance(expected, (int, float)):
                # peak_bin_index is fftshifted (DC at len//2), so an
                # index-based target would have to duplicate that base.  Compare
                # the physical offset instead: it is base-independent and is
                # exactly what the capture already reports.
                peak_offset = frame.get('peak_offset_hz')
                if not isinstance(peak_offset, (int, float)):
                    problems.append('%s has no usable peak offset'
                                    % frame.get('tag'))
                else:
                    offset_delta = abs(abs(float(peak_offset))
                                       - abs(float(expected)))
                    frame['expected_offset_delta_hz'] = offset_delta
                    frame['expected_bin_tolerance_hz'] = B_BIN_TOLERANCE * bin_hz
                    if offset_delta > B_BIN_TOLERANCE * bin_hz:
                        problems.append(
                            '%s peak %.1f Hz is more than %d bin (%.1f Hz) from '
                            'the expected %.1f Hz'
                            % (frame.get('tag'), float(peak_offset),
                               B_BIN_TOLERANCE, B_BIN_TOLERANCE * bin_hz,
                               float(expected)))
        delta_signed_hz = (float(on[1].get('peak_offset_hz', 0.0))
                           - float(on[0].get('peak_offset_hz', 0.0)))
        delta_hz = abs(delta_signed_hz)
        delta_bins_signed = delta_signed_hz / bin_hz
        delta_bins = delta_hz / bin_hz
        # The real board RX path inverts the spectrum (task-13 observation):
        # +100 kHz arrives at -102.539 kHz and +200 kHz at -205.078 kHz.  The
        # spacing gate is therefore on the magnitude; the signed value and the
        # inversion flag are kept as evidence for the BPSK demodulator.
        inversion = delta_signed_hz < 0.0
        for frame in on:
            frame['on_peak_delta_signed_hz'] = delta_signed_hz
            frame['on_peak_delta_hz'] = delta_hz
            frame['on_peak_delta_signed_bins'] = delta_bins_signed
            frame['on_peak_delta_bins'] = delta_bins
            frame['board_rx_spectral_inversion_observed'] = inversion
        report['board_rx_spectral_inversion_observed'] = inversion
        expected_hz = B_EXPECTED_TONE_BIN_STEP * bin_hz
        if abs(delta_hz - expected_hz) > B_BIN_TOLERANCE * bin_hz:
            problems.append(
                'two ON peaks differ by %.1f Hz (%.2f bins signed, %.2f bins '
                'magnitude); expected %.1f Hz (%d bins) +/- %d bin'
                % (delta_hz, delta_bins_signed, delta_bins, expected_hz,
                   B_EXPECTED_TONE_BIN_STEP, B_BIN_TOLERANCE))
    report['problems'] = problems
    return not problems


def _capture_board_frame(client, out_dir, tag):
    """Capture one 4096@60 MSPS ratio-0 frame; return (metadata, payload)."""
    frame = client.capture({'samples': planner.BOARD_BRAM_WORDS, 'ratio': 0})
    payload = frame.payload
    (out_dir / ('board-%s.iq' % tag)).write_bytes(payload)
    iq = np.frombuffer(payload, dtype='<i2')
    meta = frame.to_dict()
    meta['clipped_samples'] = int(np.count_nonzero((iq <= -2048) | (iq >= 2047)))
    _write_json(out_dir / ('board-%s.json' % tag), meta)
    return meta, payload


def _frozen_bpsk():
    """Import the frozen board receiver without changing it."""
    receiver_dir = PROJECT / 'pynq_overlay' / 'sw'
    if str(receiver_dir) not in sys.path:
        sys.path.insert(0, str(receiver_dir))
    from rf_bpsk_receiver import BpskConfig, receive_bpsk
    from rf_rx_types import RxIqFrame, RxReadback
    return BpskConfig, receive_bpsk, RxIqFrame, RxReadback


def _demodulate_bpsk(payload, meta, cfg, conjugate, seed=None):
    """Demodulate one board frame with the frozen receiver.

    conjugate=True normalises the observed board-RX spectral inversion before
    the frozen receiver sees the frame; both paths are reported separately.
    seed pins the PRBS7 reference seed used for this frame/session.
    """
    BpskConfig, receive_bpsk, RxIqFrame, RxReadback = _frozen_bpsk()
    iq = np.frombuffer(payload, dtype='<i2').reshape(-1, 2).astype(np.int16)
    # Count code-rail events on the ORIGINAL payload, before any clipping or
    # conjugation, so a negative rail (-2048) is not masked by the normaliser.
    clipped = int(np.count_nonzero((iq <= -2048) | (iq >= 2047)))
    z = iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)
    if conjugate:
        z = np.conj(z)
    codes = np.empty(2 * len(z), dtype=np.int16)
    codes[0::2] = np.clip(np.round(z.real), -2047, 2047).astype(np.int16)
    codes[1::2] = np.clip(np.round(z.imag), -2047, 2047).astype(np.int16)
    payload_norm = codes.astype('<i2').tobytes()

    def readback(side):
        return RxReadback(
            io_ok=bool(meta.get('%s_io_ok' % side, True)),
            rx_armed=meta.get('post_rx_armed'),
            tx_source_selected=meta.get('post_tx_source_selected'))
    frame = RxIqFrame(
        capture_id=str(meta.get('capture_id', 'bpsk')),
        payload=payload_norm,
        samples=int(meta.get('samples', planner.BOARD_BRAM_WORDS)),
        sample_rate_hz=int(meta.get('sample_rate_hz', planner.BOARD_FS_HZ)),
        ratio=int(meta.get('ratio', 0)),
        daemon_timestamp_ns=int(meta.get('daemon_timestamp_ns') or 0),
        connection_generation=int(meta.get('connection_generation') or 0),
        config_revision=int(meta.get('config_revision') or 0),
        request_id=int(meta.get('request_id') or 0),
        release_id=meta.get('release_id'),
        op=str(meta.get('op', 'rx.capture')),
        physical=bool(meta.get('physical', False)),
        pre_readback=readback('pre'), post_readback=readback('post'),
        completed_at_monotonic=float(meta.get('completed_at_monotonic') or 0.0))
    config = BpskConfig(reference_prbs7_seed=int(
        cfg['bpsk']['prbs7_seed'] if seed is None else seed))
    result = receive_bpsk(frame, config)
    out = result.to_dict()
    out['conjugate_input'] = bool(conjugate)
    out['code_rail_events'] = clipped
    return out


def _parse_bpsk_tx_result(tx_result):
    bpsk = tx_result.get('bpsk') or {}
    stream = tx_result.get('stream') or {}
    configured = (tx_result.get('config') or {}).get('duration_s')
    return {'status': tx_result.get('status'),
            'sample_rate_hz': bpsk.get('sample_rate_hz'),
            'symbol_rate_hz': bpsk.get('symbol_rate_hz'),
            'samples_per_symbol': bpsk.get('samples_per_symbol'),
            'prbs7_seed': bpsk.get('prbs7_seed'),
            'shaping': bpsk.get('shaping'),
            'differential_encoding': bpsk.get('differential_encoding'),
            'configured_duration_s': (None if configured is None
                                      else float(configured)),
            'stream_started_utc': stream.get('stream_started_utc'),
            'stream_stopped_utc': stream.get('stream_stopped_utc'),
            'stream_end_requested_utc': stream.get('stream_end_requested_utc'),
            'stream_stopped_confirmed_utc':
                stream.get('stream_stopped_confirmed_utc'),
            'stream_stop_confirmed': stream.get('stream_stop_confirmed'),
            'stream_seconds': stream.get('stream_seconds')}


def _run_b_bpsk(args, cfg, out_dir):
    """One gated BPSK session: one board capture, one Pluto TX, board TX off."""
    RfStatusClient, StatusProtocolError = _load_board_client(args.client_dir)
    if args.sessions != 1 or args.extended:
        raise ExecutionError('BUDGET: the BPSK session is exactly one session; '
                             'refusing %r sessions' % (args.sessions,))
    token = args.token_file.read_text(encoding='utf-8').strip()
    if not token:
        raise ExecutionError('control token file is empty')
    serial = pluto_serial(cfg)
    client, recorder = _new_board_client(args, RfStatusClient)
    report = {'direction': 'B', 'mode': 'bpsk',
              'pluto_preflight': _pluto_safe_off(serial), 'windows': [],
              'budget': {'sessions': 1, 'board_inits': 0,
                         'board_configures': 0, 'board_captures': 0,
                         'pluto_tx_starts': 0, 'board_tx_starts': 0,
                         'pluto_tx_seconds_total': 0.0}}
    current_proc = None
    try:
        _board_preflight(client, token, cfg)
        report['budget']['board_inits'] += 1
        client.control_op('rf.init', _init_args(cfg))
        report['pre_readback'] = _configure_and_check(client, cfg)
        report['budget']['board_configures'] += 1

        cfg_path = out_dir / 'pluto-tx-bpsk.json'
        _write_json(cfg_path, _pluto_bpsk_config(cfg))
        duration = float(_pluto_bpsk_config(cfg)['duration_s'])
        if duration > B_BPSK_MAX_TX_SECONDS:
            raise ExecutionError('BUDGET: BPSK TX %.3f s exceeds %.1f s'
                                 % (duration, B_BPSK_MAX_TX_SECONDS))
        _board_tx_off_snapshot(client, 'BPSK', 'before_tx')
        ready = out_dir / 'pluto-tx-bpsk.ready'
        proc = _run_radio(B_BPSK_TX_MODE, cfg_path, args.radioconda,
                          path_confirmed=args.path_confirmed,
                          ready_file=ready, background=True)
        current_proc = proc
        report['budget']['pluto_tx_starts'] += 1
        started = time.time()
        deadline = time.time() + B_PLUTO_READY_TIMEOUT_S
        while not ready.is_file() and time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if not ready.is_file():
            raise ExecutionError(
                'Pluto BPSK TX did not signal STREAM_STARTED within %.1f s'
                % B_PLUTO_READY_TIMEOUT_S)

        before_capture = _board_tx_off_snapshot(client, 'BPSK', 'before_capture')
        meta, payload = _capture_board_frame(client, out_dir, 'BPSK')
        after_capture = _board_tx_off_snapshot(client, 'BPSK', 'after_capture')
        report['capture'] = meta
        report['budget']['board_captures'] += 1
        if report['budget']['board_captures'] > B_BPSK_CAPTURE_WINDOWS:
            raise ExecutionError('BUDGET: BPSK captures exceed %d'
                                 % B_BPSK_CAPTURE_WINDOWS)

        try:
            out, err = proc.communicate(timeout=B_PLUTO_EXIT_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise ExecutionError(
                'Pluto BPSK TX did not exit within %.1f s'
                % B_PLUTO_EXIT_TIMEOUT_S) from exc
        current_proc = None
        if proc.returncode != 0:
            raise ExecutionError('Pluto BPSK TX failed (%d): %s'
                                 % (proc.returncode, (err or out)[-2000:]))
        tx_result = _parse_radio_json(out)
        report['pluto_tx_summary'] = _parse_bpsk_tx_result(tx_result)
        report['tx_configured_duration_s'] = duration
        if report['pluto_tx_summary'].get('stream_stop_confirmed') is not True:
            raise ExecutionError(
                'BPSK_STREAM_STOP_UNCONFIRMED: TX stop was not confirmed')
        measured = report['pluto_tx_summary'].get('stream_seconds')
        report['budget']['pluto_tx_seconds_total'] = measured
        if (isinstance(measured, (int, float)) and math.isfinite(float(measured))
                and float(measured) > B_BPSK_MAX_TX_SECONDS):
            raise ExecutionError(
                'BUDGET: measured BPSK stream %.3f s exceeds %.1f s'
                % (float(measured), B_BPSK_MAX_TX_SECONDS))
        report['windows'].append({
            'tag': 'BPSK',
            'capture': meta,
            'board_tx_off_before_capture': before_capture,
            'board_tx_off_after_capture': after_capture,
            'pluto_tx': tx_result,
            'pluto_tx_summary': _parse_bpsk_tx_result(tx_result),
            'post_readback': _check_pluto_post(tx_result),
            'independent_readback': _pluto_safe_off(serial),
            'measured_tx_seconds': measured,
            'configured_duration_s': duration,
            'wall_seconds': round(time.time() - started, 3)})
        report['demod'] = {
            'normal': _demodulate_bpsk(payload, meta, cfg, conjugate=False),
            'conjugated': _demodulate_bpsk(payload, meta, cfg, conjugate=True)}
        report['pluto_final_readback'] = _pluto_safe_off(serial)
        client.control_disarm()
        final_status, poll_attempts, poll_errors = _poll_final_status(client)
        report['final_status'] = final_status
        report['final_status_unknown'] = final_status is None
        report['final_status_poll_attempts'] = poll_attempts
        report['final_status_poll_errors'] = poll_errors
        report['passed'] = _evaluate_b_bpsk(report)
        _finish_capture_record(recorder, args, cfg, out_dir, report)
        return report
    except BaseException as exc:  # noqa: BLE001 - stop the Pluto TX child
        cleanup = {'original_error': '%s: %s' % (type(exc).__name__, exc)}
        if current_proc is not None:
            cleanup['pluto_tx'] = _pluto_safe_stop_after_tx(current_proc, serial)
            current_proc = None
            pluto_ok = bool(cleanup['pluto_tx'].get('confirmed'))
        else:
            try:
                cleanup['pluto_readback'] = _pluto_safe_off(serial)
                pluto_ok = True
            except BaseException as readback_exc:  # noqa: BLE001
                cleanup['pluto_error'] = ('%s: %s'
                                          % (type(readback_exc).__name__,
                                             readback_exc))
                pluto_ok = False
        cleanup['board'] = _board_teardown(client)
        board = cleanup['board']
        board_ok = (board.get('tx_source_selected') is False
                    and board.get('io_ok') is True
                    and board.get('disarmed') is True)
        cleanup['cleanup_required'] = not (pluto_ok and board_ok)
        cleanup['cleanup_note'] = (
            'automatic safe stop after the BPSK failure' if not
            cleanup['cleanup_required'] else
            'automatic safe stop could not be fully confirmed; disconnect the '
            'Pluto TX and the board RX1 coax (and Pluto USB) on site')
        try:
            _write_json(out_dir / 'failure-cleanup.json', cleanup)
        except Exception:
            pass
        try:
            exc.cleanup_report = cleanup
        except Exception:
            pass
        raise
    finally:
        try:
            client.close()
        except Exception:
            pass


def _evaluate_b_bpsk(report):
    """Gates for the one-capture BPSK session; every result is reported apart."""
    problems = []
    budget = report.get('budget') or {}
    expected_budget = {'sessions': 1, 'board_inits': 1, 'board_configures': 1,
                       'board_captures': 1, 'pluto_tx_starts': 1,
                       'board_tx_starts': 0}
    for key, want in expected_budget.items():
        if budget.get(key) != want:
            problems.append('budget %s=%r, required %r'
                            % (key, budget.get(key), want))
    seconds = budget.get('pluto_tx_seconds_total')
    if (not isinstance(seconds, (int, float))
            or not math.isfinite(float(seconds))
            or not 0 < float(seconds) <= B_BPSK_MAX_TX_SECONDS):
        problems.append('budget BPSK measured stream_seconds=%r not in (0, %.1f]'
                        % (seconds, B_BPSK_MAX_TX_SECONDS))
    # The TX receipt itself is part of the evidence: it must prove that the
    # playback really was the frozen 6 MSPS/sps2 BPSK stream, not just that
    # some TX subprocess exited zero.
    summary = report.get('pluto_tx_summary') or {}
    expected_summary = {
        'status': 'TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED',
        'sample_rate_hz': 6000000,
        'symbol_rate_hz': 3000000,
        'samples_per_symbol': 2,
        'prbs7_seed': 1,
        'shaping': 'RECT',
    }
    for key, want in expected_summary.items():
        got = summary.get(key)
        type_ok = (not isinstance(want, int)) or type(got) is int
        if got != want or not type_ok:
            problems.append('Pluto TX receipt %s=%r, required %r'
                            % (key, got, want))
    if summary.get('differential_encoding') is not False:
        problems.append('Pluto TX receipt differential_encoding=%r, required '
                        'explicit false'
                        % (summary.get('differential_encoding'),))
    configured = summary.get('configured_duration_s')
    if (not isinstance(configured, (int, float))
            or not math.isfinite(float(configured))
            or not 0 < float(configured) <= B_BPSK_MAX_TX_SECONDS):
        problems.append('Pluto TX receipt configured_duration_s=%r not in (0, %.1f]'
                        % (configured, B_BPSK_MAX_TX_SECONDS))
    # B1: the reported window must cover tb.stop()+tb.wait() confirmation.
    stream_seconds = summary.get('stream_seconds')
    if summary.get('stream_stop_confirmed') is not True:
        problems.append('Pluto TX stream_stop_confirmed=%r; budget UNKNOWN'
                        % (summary.get('stream_stop_confirmed'),))
    if (not isinstance(stream_seconds, (int, float))
            or not math.isfinite(float(stream_seconds))
            or not 0 < float(stream_seconds) <= B_BPSK_MAX_TX_SECONDS):
        problems.append('Pluto TX measured stream_seconds=%r not in (0, %.1f]'
                        % (stream_seconds, B_BPSK_MAX_TX_SECONDS))
    for key in ('stream_started_utc', 'stream_stopped_utc',
                'stream_stopped_confirmed_utc'):
        value = summary.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append('Pluto TX %s is missing; the stream stop cannot be '
                            'confirmed, budget UNKNOWN' % key)
    capture = report.get('capture') or {}
    for key in ('pre_io_ok', 'post_io_ok'):
        if capture.get(key) is not True:
            problems.append('capture %s is not true' % key)
    if capture.get('post_tx_source_selected') is not False:
        problems.append('capture post_tx_source_selected is not false')
    if capture.get('post_rx_armed') is not False:
        problems.append('capture post_rx_armed is not false')
    if int(capture.get('clipped_samples') or 0) != 0:
        problems.append('capture has code-rail clipping (%r)'
                        % capture.get('clipped_samples'))
    if report.get('final_status_unknown') is True:
        attempts = report.get('final_status_poll_attempts')
        problems.append('final board status UNKNOWN: %r post-disarm polls '
                        'returned no online snapshot' % (attempts,))
    else:
        problems.extend(_final_status_problems(
            report.get('final_status') or {}))
    final_readback = report.get('pluto_final_readback') or {}
    if final_readback.get('hardwaregain_db') != planner.PLUTO_SAFE_HARDWARE_GAIN_DB:
        problems.append('Pluto final hardwaregain=%r != %.2f'
                        % (final_readback.get('hardwaregain_db'),
                           planner.PLUTO_SAFE_HARDWARE_GAIN_DB))
    if final_readback.get('tx_powerdown') != '1':
        problems.append('Pluto final powerdown=%r != 1'
                        % (final_readback.get('tx_powerdown'),))
    demod = report.get('demod') or {}
    for path in ('normal', 'conjugated'):
        d = demod.get(path) or {}
        if d.get('reference_sync') is not True:
            problems.append('%s demod reference_sync is not true (reason %r)'
                            % (path, d.get('reason')))
            continue
        if d.get('reason') not in ('', None):
            problems.append('%s demod reason is %r' % (path, d.get('reason')))
        if d.get('ber') != 0:
            problems.append('%s demod BER is %r, required 0' % (path, d.get('ber')))
        if not isinstance(d.get('compared_bits'), int) or d['compared_bits'] < 64:
            problems.append('%s demod compared_bits is %r, required >= 64'
                            % (path, d.get('compared_bits')))
    report['problems'] = problems
    return not problems


def _disarm_ack_evidence(response, final):
    """A disarm ACK must be observed, never assumed.

    The frozen client returns the daemon's control.disarm result (the real
    daemon answers ``{"armed": false}``).  The client source itself warns that
    an answered RPC is not proof of the disarmed state, so the final poll's
    ``armed`` field is required as well.
    """
    if not isinstance(response, dict) or response.get('armed') is not False:
        return False
    return isinstance(final, dict) and final.get('armed') is False


def _bpsk_stat_record(client, cfg, out_dir, tag, seed, on, session_index,
                      segment_index=0):
    """Capture one frame with per-frame timing; no extra per-frame TX polls.

    Per-frame safety comes from the capture receipt itself
    (pre/post io_ok, post_tx_source_selected=false, post_rx_armed=false,
    clipped_samples=0).  Only one poll before TX and one after the session
    remain in the runner.
    """
    started = time.monotonic()
    meta, payload = _capture_board_frame(client, out_dir, tag)
    captured = time.monotonic()
    # B3: judge this frame BEFORE any further capture/TX.
    capture_problems = _bpsk_stat_capture_problems(
        {'tag': tag, 'capture': meta})
    if capture_problems:
        raise ExecutionError('BPSK_STAT_CAPTURE_SAFETY: ' + '; '.join(
            capture_problems))
    normal = _demodulate_bpsk(payload, meta, cfg, False, seed=seed)
    demodded = time.monotonic()
    conjugated = _demodulate_bpsk(payload, meta, cfg, True, seed=seed)
    finished = time.monotonic()
    return {
        'session': session_index, 'segment': segment_index,
        'seed': int(seed), 'on': bool(on), 'tag': tag,
        'iq_sha256': hashlib.sha256(payload).hexdigest(),
        'capture': meta,
        'demod': {'normal': normal, 'conjugated': conjugated},
        'timing': {
            'poll_s': 0.0,
            'capture_s': captured - started,
            'demod_normal_s': demodded - captured,
            'demod_conjugated_s': finished - demodded,
            'total_s': finished - started,
        },
    }


def _run_b_bpsk_stat(args, cfg, out_dir):
    """3 independent BPSK sessions: 10 ON + 1 OFF each, aggregated (task-17)."""
    if args.sessions != B_BPSK_STAT_SESSIONS:
        raise ExecutionError(
            'BUDGET: --mode bpsk-stat requires exactly %d sessions, got %r'
            % (B_BPSK_STAT_SESSIONS, args.sessions))
    RfStatusClient, StatusProtocolError = _load_board_client(args.client_dir)
    token = args.token_file.read_text(encoding='utf-8').strip()
    if not token:
        raise ExecutionError('control token file is empty')
    serial = pluto_serial(cfg)
    client, recorder = _new_board_client(args, RfStatusClient)
    tx_per_session = int(getattr(args, 'stat_tx_per_session', 1))
    if tx_per_session not in B_BPSK_STAT_TX_PER_SESSION_CHOICES:
        raise ExecutionError('BUDGET: --stat-tx-per-session must be 1 or 2, '
                             'got %r' % (tx_per_session,))
    frames_per_segment = _bpsk_stat_segment_frames(tx_per_session)
    tx_seconds_cap = _bpsk_stat_tx_seconds_cap(tx_per_session)
    report = {'direction': 'B', 'mode': 'bpsk-stat',
              'seeds': list(B_BPSK_STAT_SEEDS), 'sessions': [],
              'stat_tx_per_session': tx_per_session,
              'tx_seconds_cap': tx_seconds_cap,
              'frames_per_segment': frames_per_segment,
              'records': [], 'pluto_preflight': None,
              'budget': {'sessions': 0, 'board_inits': 0,
                         'board_configures': 0, 'board_captures': 0,
                         'pluto_tx_starts': 0, 'board_tx_starts': 0,
                         'pluto_tx_seconds_total': 0.0}}
    current_proc = None
    try:
        for index, seed in enumerate(B_BPSK_STAT_SEEDS):
            if report['budget']['sessions'] >= B_BPSK_STAT_SESSIONS:
                raise ExecutionError('BUDGET: BPSK-stat sessions exceed %d'
                                     % B_BPSK_STAT_SESSIONS)
            _board_preflight(client, token, cfg)
            preflight_poll = dict(_LAST_PREFLIGHT_POLL or {})
            arm_wait = dict(_LAST_ARM_WAIT or {})
            post_arm_poll = dict(_LAST_POST_ARM_POLL or {})
            report['budget']['sessions'] += 1
            client.control_op('rf.init', _init_args(cfg))
            report['budget']['board_inits'] += 1
            pre = _configure_and_check(client, cfg)
            report['budget']['board_configures'] += 1
            if report['budget']['board_inits'] > B_BPSK_STAT_SESSIONS:
                raise ExecutionError('BUDGET: BPSK-stat inits exceed %d'
                                     % B_BPSK_STAT_SESSIONS)
            if report['budget']['board_configures'] > B_BPSK_STAT_SESSIONS:
                raise ExecutionError('BUDGET: BPSK-stat configures exceed %d'
                                     % B_BPSK_STAT_SESSIONS)

            session_started = time.time()
            session_records = []
            segments = []
            for segment_index in range(tx_per_session):
                # Exactly one board poll before each TX segment (the second
                # segment's poll also proves the previous TX is off).
                tx_off_before = _board_tx_off_snapshot(
                    client, 's%d-seg%d-pre' % (index, segment_index),
                    'before_tx')
                cfg_path = out_dir / ('pluto-tx-bpsk-s%d-seg%d.json'
                                      % (index, segment_index))
                _write_json(cfg_path, _pluto_bpsk_config(cfg, seed))
                duration = float(_pluto_bpsk_config(cfg, seed)['duration_s'])
                if duration > B_BPSK_MAX_TX_SECONDS:
                    raise ExecutionError(
                        'BUDGET: BPSK-stat TX %.3f s exceeds %.1f s'
                        % (duration, B_BPSK_MAX_TX_SECONDS))
                ready = out_dir / ('pluto-tx-bpsk-s%d-seg%d.ready'
                                   % (index, segment_index))
                stopped_path = Path(str(ready) + '.stopped')
                requested_path = Path(str(ready) + '.stream_end_requested')
                proc = _run_radio(B_BPSK_TX_MODE, cfg_path, args.radioconda,
                                  path_confirmed=args.path_confirmed,
                                  ready_file=ready, background=True)
                current_proc = proc
                report['budget']['pluto_tx_starts'] += 1
                if (report['budget']['pluto_tx_starts']
                        > B_BPSK_STAT_SESSIONS * tx_per_session):
                    raise ExecutionError(
                        'BUDGET: BPSK-stat TX starts exceed %d'
                        % (B_BPSK_STAT_SESSIONS * tx_per_session))
                deadline = time.time() + B_PLUTO_READY_TIMEOUT_S
                while not ready.is_file() and time.time() < deadline:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                if not ready.is_file():
                    raise ExecutionError(
                        'Pluto BPSK TX did not signal STREAM_STARTED within '
                        '%.1f s' % B_PLUTO_READY_TIMEOUT_S)

                segment_started = time.time()
                segment_records = []
                for frame_index in range(frames_per_segment):
                    if stopped_path.is_file() or requested_path.is_file():
                        marker = ('<ready>.stopped' if stopped_path.is_file()
                                  else '<ready>.stream_end_requested')
                        raise ExecutionError(
                            'BPSK_STAT_STREAM_ENDED_EARLY: %s appeared before '
                            'ON capture %d/%d of session %d segment %d; '
                            'refusing to capture noise'
                            % (marker, frame_index + 1, frames_per_segment,
                               index, segment_index))
                    if proc.poll() is not None:
                        raise ExecutionError(
                            'BPSK_STAT_INCOMPLETE_STREAM: Pluto TX exited '
                            'before %d ON captures (got %d)'
                            % (frames_per_segment, len(segment_records)))
                    tag = 's%d-on-%02d' % (
                        index, segment_index * frames_per_segment + frame_index)
                    record = _bpsk_stat_record(
                        client, cfg, out_dir, tag, seed, True, index,
                        segment_index=segment_index)
                    report['records'].append(record)
                    segment_records.append(record)
                    report['budget']['board_captures'] += 1
                    if (report['budget']['board_captures']
                            > B_BPSK_STAT_RECORDS):
                        raise ExecutionError(
                            'BUDGET: BPSK-stat captures exceed %d'
                            % B_BPSK_STAT_RECORDS)
                    if ((stopped_path.is_file() or requested_path.is_file())
                            and len(segment_records) < frames_per_segment):
                        marker = ('<ready>.stopped' if stopped_path.is_file()
                                  else '<ready>.stream_end_requested')
                        raise ExecutionError(
                            'BPSK_STAT_STREAM_ENDED_EARLY: %s appeared after '
                            'only %d/%d ON captures of session %d segment %d'
                            % (marker, len(segment_records), frames_per_segment,
                               index, segment_index))
                if len(segment_records) != frames_per_segment:
                    raise ExecutionError(
                        'BPSK_STAT_INCOMPLETE_STREAM: only %d/%d ON captures'
                        % (len(segment_records), frames_per_segment))

                try:
                    out, err = proc.communicate(timeout=B_PLUTO_EXIT_TIMEOUT_S)
                except subprocess.TimeoutExpired as exc:
                    raise ExecutionError(
                        'Pluto BPSK TX did not exit within %.1f s'
                        % B_PLUTO_EXIT_TIMEOUT_S) from exc
                current_proc = None
                if proc.returncode != 0:
                    raise ExecutionError('Pluto BPSK TX failed (%d): %s'
                                         % (proc.returncode,
                                            (err or out)[-2000:]))
                tx_result = _parse_radio_json(out)
                summary = _parse_bpsk_tx_result(tx_result)
                if summary.get('stream_stop_confirmed') is not True:
                    raise ExecutionError(
                        'BPSK_STAT_STREAM_STOP_UNCONFIRMED: TX stop was not '
                        'confirmed; budget UNKNOWN')
                measured = summary.get('stream_seconds')
                if (not isinstance(measured, (int, float))
                        or not math.isfinite(float(measured))
                        or not 0 < float(measured) <= B_BPSK_MAX_TX_SECONDS):
                    raise ExecutionError(
                        'BPSK_STAT_STREAM_OVER: measured stream_seconds=%r not '
                        'in (0, %.1f]' % (measured, B_BPSK_MAX_TX_SECONDS))
                report['budget']['pluto_tx_seconds_total'] += float(measured)
                if (report['budget']['pluto_tx_seconds_total']
                        > tx_seconds_cap):
                    raise ExecutionError(
                        'BUDGET: cumulative measured BPSK TX %.3f s exceeds '
                        '%.1f s' % (
                            report['budget']['pluto_tx_seconds_total'],
                            tx_seconds_cap))
                if summary.get('prbs7_seed') != seed:
                    raise ExecutionError(
                        'BPSK_STAT_TX_RECEIPT_SEED: receipt seed=%r, session '
                        'seed=%r' % (summary.get('prbs7_seed'), seed))

                try:
                    safe_off = _pluto_safe_off(serial)
                except BaseException as exc:  # noqa: BLE001 - named failure
                    raise ExecutionError(
                        'BPSK_STAT_SAFE_OFF_READBACK: %s: %s'
                        % (type(exc).__name__, exc)) from exc
                if (safe_off.get('hardwaregain_db')
                        != planner.PLUTO_SAFE_HARDWARE_GAIN_DB
                        or safe_off.get('tx_powerdown') != '1'):
                    raise ExecutionError(
                        'BPSK_STAT_SAFE_OFF_READBACK: %r is not the safe-off '
                        'state' % (safe_off,))
                segment = {'index': segment_index,
                           'records': [r['tag'] for r in segment_records],
                           'frame_count': len(segment_records),
                           'pluto_tx_summary': summary,
                           'measured_stream_seconds': float(measured),
                           'safe_off': safe_off,
                           'tx_off_before_tx': tx_off_before,
                           'tx_off_after_segment': None,
                           'wall_seconds': round(
                               time.time() - segment_started, 3)}
                segments.append(segment)
                session_records.extend(segment_records)
                if segment_index < tx_per_session - 1:
                    segment['tx_off_after_segment'] = _board_tx_off_snapshot(
                        client, 's%d-seg%d-post' % (index, segment_index),
                        'after_segment')

            # OFF negative control strictly after the last segment's safe-off.
            off_record = _bpsk_stat_record(
                client, cfg, out_dir, 's%d-off' % index, seed, False, index,
                segment_index=tx_per_session - 1)
            report['records'].append(off_record)
            session_records.append(off_record)
            report['budget']['board_captures'] += 1
            if report['budget']['board_captures'] > B_BPSK_STAT_RECORDS:
                raise ExecutionError('BUDGET: BPSK-stat captures exceed %d'
                                     % B_BPSK_STAT_RECORDS)
            tx_off_after_session = _board_tx_off_snapshot(
                client, 's%d-after-session' % index, 'after_session')

            disarm_response = None
            disarm_error = None
            try:
                disarm_response = client.control_disarm()
            except BaseException as exc:  # noqa: BLE001 - evidence, not a pass
                disarm_error = '%s: %s' % (type(exc).__name__, exc)
            final, attempts, errors = _poll_final_status(client)
            disarm_ack = _disarm_ack_evidence(disarm_response, final)
            closed = final is not None and not _final_status_problems(final)
            disarm_settle_s = 0.0
            if index < B_BPSK_STAT_SESSIONS - 1:
                disarm_settle_s = B_BPSK_STAT_DISARM_SETTLE_S
                time.sleep(disarm_settle_s)
            measured_total = sum(s['measured_stream_seconds']
                                 for s in segments)
            session = {'index': index, 'seed': seed,
                       'pre_readback': pre,
                       'preflight_poll': preflight_poll,
                       'arm_wait': arm_wait,
                       'post_arm_poll': post_arm_poll,
                       'pluto_tx_summary': segments[-1]['pluto_tx_summary'],
                       'pluto_tx_summaries': [s['pluto_tx_summary']
                                              for s in segments],
                       'segments': segments,
                       'measured_stream_seconds': measured_total,
                       'pluto_safe_off': segments[-1]['safe_off'],
                       'tx_off_before_session': segments[0]['tx_off_before_tx'],
                       'tx_off_after_session': tx_off_after_session,
                       'capture_count': len(session_records),
                       'closed': bool(closed), 'disarm_ack': disarm_ack,
                       'disarm_response': disarm_response,
                       'disarm_error': disarm_error,
                       'final_status': final,
                       'final_status_unknown': final is None,
                       'final_status_poll_attempts': attempts,
                       'final_status_poll_errors': errors,
                       'disarm_settle_s': disarm_settle_s,
                       'wall_seconds': round(time.time() - session_started, 3)}
            report['sessions'].append(session)
            if final is None:
                raise ExecutionError(
                    'BPSK_STAT_FINAL_UNKNOWN: session %d final status UNKNOWN'
                    % index)
            final_problems = _final_status_problems(final)
            if final_problems:
                raise ExecutionError('BPSK_STAT_SAFETY: session %d: %s'
                                     % (index, '; '.join(final_problems)))
            if not (closed and disarm_ack):
                break
        report['passed'] = _evaluate_b_bpsk_stat(report)
        _finish_capture_record(recorder, args, cfg, out_dir, report)
        return report
    except BaseException as exc:  # noqa: BLE001 - stop the Pluto TX child
        cleanup = {'original_error': '%s: %s' % (type(exc).__name__, exc)}
        if current_proc is not None:
            cleanup['pluto_tx'] = _pluto_safe_stop_after_tx(current_proc, serial)
            current_proc = None
            pluto_ok = bool(cleanup['pluto_tx'].get('confirmed'))
        else:
            try:
                cleanup['pluto_readback'] = _pluto_safe_off(serial)
                pluto_ok = True
            except BaseException as readback_exc:  # noqa: BLE001
                cleanup['pluto_error'] = ('%s: %s'
                                          % (type(readback_exc).__name__,
                                             readback_exc))
                pluto_ok = False
        cleanup['board'] = _board_teardown(client)
        board = cleanup['board']
        board_ok = (board.get('tx_source_selected') is False
                    and board.get('io_ok') is True
                    and board.get('disarmed') is True)
        cleanup['cleanup_required'] = not (pluto_ok and board_ok)
        cleanup['cleanup_note'] = (
            'automatic safe stop after the BPSK-stat failure' if not
            cleanup['cleanup_required'] else
            'automatic safe stop could not be fully confirmed; disconnect the '
            'Pluto TX and the board RX1 coax (and Pluto USB) on site')
        try:
            _write_json(out_dir / 'failure-cleanup.json', cleanup)
        except Exception:
            pass
        try:
            exc.cleanup_report = cleanup
        except Exception:
            pass
        raise
    finally:
        try:
            client.close()
        except Exception:
            pass


def _demod_claims_reference(demod):
    """True when one demod path claims PRBS alignment or BER counts."""
    return (isinstance(demod, dict)
            and (demod.get('reference_sync') is True
                 or any(demod.get(key) is not None for key in
                        ('bit_errors', 'compared_bits', 'ber'))))


def _bpsk_stat_capture_problems(record):
    problems = []
    capture = record.get('capture') or {}
    for key in ('pre_io_ok', 'post_io_ok'):
        if capture.get(key) is not True:
            problems.append('capture %s is not true (%r)'
                            % (key, record.get('tag')))
    if capture.get('post_tx_source_selected') is not False:
        problems.append('capture post_tx_source_selected is not false (%r)'
                        % (record.get('tag'),))
    if capture.get('post_rx_armed') is not False:
        problems.append('capture post_rx_armed is not false (%r)'
                        % (record.get('tag'),))
    if int(capture.get('clipped_samples') or 0) != 0:
        problems.append('capture has code-rail clipping (%r: %r)'
                        % (record.get('tag'), capture.get('clipped_samples')))
    return problems


def _bpsk_stat_receipt_problems(summary, seed):
    problems = []
    expected = {'status': 'TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED',
                'sample_rate_hz': 6000000, 'symbol_rate_hz': 3000000,
                'samples_per_symbol': 2, 'prbs7_seed': seed,
                'shaping': 'RECT'}
    for key, want in expected.items():
        got = summary.get(key)
        type_ok = (not isinstance(want, int)) or type(got) is int
        if got != want or not type_ok:
            problems.append('TX receipt %s=%r, required %r'
                            % (key, got, want))
    if summary.get('differential_encoding') is not False:
        problems.append('TX receipt differential_encoding=%r, required explicit '
                        'false' % (summary.get('differential_encoding'),))
    configured = summary.get('configured_duration_s')
    if (not isinstance(configured, (int, float))
            or not math.isfinite(float(configured))
            or not 0 < float(configured) <= B_BPSK_MAX_TX_SECONDS):
        problems.append('TX receipt configured_duration_s=%r not in (0, %.1f]'
                        % (configured, B_BPSK_MAX_TX_SECONDS))
    stream_seconds = summary.get('stream_seconds')
    if summary.get('stream_stop_confirmed') is not True:
        problems.append('TX receipt stream_stop_confirmed=%r; stream stop '
                        'UNKNOWN' % (summary.get('stream_stop_confirmed'),))
    if (not isinstance(stream_seconds, (int, float))
            or not math.isfinite(float(stream_seconds))
            or not 0 < float(stream_seconds) <= B_BPSK_MAX_TX_SECONDS):
        problems.append('TX receipt measured stream_seconds=%r not in (0, %.1f]'
                        % (stream_seconds, B_BPSK_MAX_TX_SECONDS))
    for key in ('stream_started_utc', 'stream_stopped_utc',
                'stream_stopped_confirmed_utc'):
        value = summary.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append('TX receipt %s is missing; stream stop UNKNOWN'
                            % key)
    return problems


def _evaluate_b_bpsk_stat(report):
    """Aggregate gates for 3 sessions x (10 ON + 1 OFF), 33 records.

    A session may have 1 or 2 TX segments; every segment must carry its own
    measured stream window, safe-off readback and seed-matched receipt.
    """
    problems = []
    tx_per_session = report.get('stat_tx_per_session', 1)
    if tx_per_session not in B_BPSK_STAT_TX_PER_SESSION_CHOICES:
        problems.append('stat_tx_per_session=%r, required 1 or 2'
                        % (tx_per_session,))
        tx_per_session = 1
    try:
        seconds_cap = _bpsk_stat_tx_seconds_cap(tx_per_session)
    except ExecutionError as exc:
        problems.append(str(exc))
        seconds_cap = B_BPSK_STAT_MAX_TX_SECONDS_TOTAL[1]
    frames_per_segment = _bpsk_stat_segment_frames(tx_per_session)
    budget = report.get('budget') or {}
    expected_budget = {'sessions': B_BPSK_STAT_SESSIONS,
                       'board_inits': B_BPSK_STAT_SESSIONS,
                       'board_configures': B_BPSK_STAT_SESSIONS,
                       'board_captures': B_BPSK_STAT_RECORDS,
                       'pluto_tx_starts': B_BPSK_STAT_SESSIONS * tx_per_session,
                       'board_tx_starts': 0}
    for key, want in expected_budget.items():
        if budget.get(key) != want:
            problems.append('budget %s=%r, required %r'
                            % (key, budget.get(key), want))
    seconds = budget.get('pluto_tx_seconds_total')
    if (not isinstance(seconds, (int, float))
            or not math.isfinite(float(seconds))
            or not 0 < float(seconds) <= seconds_cap):
        problems.append('budget cumulative measured stream_seconds=%r not in '
                        '(0, %.1f]' % (seconds, seconds_cap))
    records = report.get('records') or []
    if len(records) != B_BPSK_STAT_RECORDS:
        problems.append('records=%d, required %d'
                        % (len(records), B_BPSK_STAT_RECORDS))
    on_records = [r for r in records if r.get('on') is True]
    off_records = [r for r in records if r.get('on') is False]
    if len(on_records) != (B_BPSK_STAT_ON_FRAMES * B_BPSK_STAT_SESSIONS):
        problems.append('ON records=%d, required %d'
                        % (len(on_records), B_BPSK_STAT_ON_FRAMES
                           * B_BPSK_STAT_SESSIONS))
    if len(off_records) != (B_BPSK_STAT_OFF_FRAMES * B_BPSK_STAT_SESSIONS):
        problems.append('OFF records=%d, required %d'
                        % (len(off_records), B_BPSK_STAT_OFF_FRAMES
                           * B_BPSK_STAT_SESSIONS))
    tags, iq_hashes = set(), set()
    on_aligned = 0
    bit_errors = compared_bits = 0
    for record in records:
        tag = record.get('tag')
        if tag in tags:
            problems.append('duplicate capture tag %r' % (tag,))
        tags.add(tag)
        iq_sha = record.get('iq_sha256')
        if not isinstance(iq_sha, str) or len(iq_sha) != 64:
            problems.append('record %r has no IQ sha256' % (tag,))
        elif iq_sha in iq_hashes:
            problems.append('record %r reuses an IQ (sha256 collision)' % (tag,))
        iq_hashes.add(iq_sha)
        problems.extend(_bpsk_stat_capture_problems(record))
        demod = (record.get('demod') or {}).get('normal') or {}
        if record.get('on') is True:
            if demod.get('reference_sync') is True:
                errors, compared, ber = (demod.get('bit_errors'),
                                         demod.get('compared_bits'),
                                         demod.get('ber'))
                valid = (type(errors) is int and type(compared) is int
                         and compared > 0 and 0 <= errors <= compared
                         and type(ber) in (int, float)
                         and math.isfinite(ber) and 0 <= ber <= 1
                         and abs(ber - errors / compared) <= 1e-12)
                if not valid:
                    problems.append('record %r has invalid BER counts (%r/%r/%r)'
                                    % (tag, errors, compared, ber))
                else:
                    on_aligned += 1
                    bit_errors += errors
                    compared_bits += compared
        else:
            for path in ('normal', 'conjugated'):
                d = (record.get('demod') or {}).get(path) or {}
                if _demod_claims_reference(d):
                    problems.append(
                        'OFF record %r %s claims reference_sync/BER'
                        % (tag, path))
    if on_aligned < B_BPSK_STAT_ALIGNED:
        problems.append('on_aligned=%d, required >= %d'
                        % (on_aligned, B_BPSK_STAT_ALIGNED))
    aggregate = (bit_errors / compared_bits) if compared_bits else None
    if (compared_bits <= 0 or aggregate is None
            or aggregate > B_BPSK_STAT_MAX_AGGREGATE_BER):
        problems.append('aggregate_ber=%r over %d bits, required <= %.2f'
                        % (aggregate, compared_bits,
                           B_BPSK_STAT_MAX_AGGREGATE_BER))
    sessions = report.get('sessions') or []
    if len(sessions) != B_BPSK_STAT_SESSIONS:
        problems.append('sessions=%d, required %d'
                        % (len(sessions), B_BPSK_STAT_SESSIONS))
    for index, session in enumerate(sessions):
        if session.get('closed') is not True:
            problems.append('session %d is not closed' % index)
        if session.get('disarm_ack') is not True:
            problems.append('session %d has no disarm ACK' % index)
        if session.get('capture_count') != (B_BPSK_STAT_ON_FRAMES
                                            + B_BPSK_STAT_OFF_FRAMES):
            problems.append('session %d capture_count=%r, required %d'
                            % (index, session.get('capture_count'),
                               B_BPSK_STAT_ON_FRAMES
                               + B_BPSK_STAT_OFF_FRAMES))
        segments = session.get('segments') or []
        if len(segments) != tx_per_session:
            problems.append('session %d segments=%d, required %d'
                            % (index, len(segments), tx_per_session))
        segment_seconds = 0.0
        for segment_index, segment in enumerate(segments):
            if segment.get('frame_count') != frames_per_segment:
                problems.append('session %d segment %d frame_count=%r, '
                                'required %d'
                                % (index, segment_index,
                                   segment.get('frame_count'),
                                   frames_per_segment))
            measured = segment.get('measured_stream_seconds')
            if (not isinstance(measured, (int, float))
                    or not math.isfinite(float(measured))
                    or not 0 < float(measured) <= B_BPSK_MAX_TX_SECONDS):
                problems.append('session %d segment %d measured_stream_seconds'
                                '=%r not in (0, %.1f]'
                                % (index, segment_index, measured,
                                   B_BPSK_MAX_TX_SECONDS))
            else:
                segment_seconds += float(measured)
            summary = segment.get('pluto_tx_summary') or {}
            problems.extend(
                'session %d segment %d: %s' % (index, segment_index, problem)
                for problem in _bpsk_stat_receipt_problems(
                    summary, session.get('seed')))
            safe_off = segment.get('safe_off') or {}
            if (safe_off.get('hardwaregain_db')
                    != planner.PLUTO_SAFE_HARDWARE_GAIN_DB):
                problems.append('session %d segment %d Pluto safe-off gain=%r '
                                '!= %.2f'
                                % (index, segment_index,
                                   safe_off.get('hardwaregain_db'),
                                   planner.PLUTO_SAFE_HARDWARE_GAIN_DB))
            if safe_off.get('tx_powerdown') != '1':
                problems.append('session %d segment %d Pluto safe-off '
                                'powerdown=%r != 1'
                                % (index, segment_index,
                                   safe_off.get('tx_powerdown')))
            before = segment.get('tx_off_before_tx') or {}
            if before.get('tx_source_selected') is not False:
                problems.append('session %d segment %d has no TX-off poll '
                                'before TX' % (index, segment_index))
            if segment_index < len(segments) - 1:
                after = segment.get('tx_off_after_segment') or {}
                if after.get('tx_source_selected') is not False:
                    problems.append('session %d segment %d has no TX-off poll '
                                    'after segment/safe-off'
                                    % (index, segment_index))
        measured_total = session.get('measured_stream_seconds')
        if (not isinstance(measured_total, (int, float))
                or not math.isfinite(float(measured_total))
                or abs(float(measured_total) - segment_seconds) > 1e-6):
            problems.append('session %d measured total=%r != sum(segments)=%r'
                            % (index, measured_total, segment_seconds))
        after_session = session.get('tx_off_after_session') or {}
        if after_session.get('tx_source_selected') is not False:
            problems.append('session %d has no TX-off poll after the session'
                            % index)
        if session.get('final_status_unknown') is True:
            problems.append('session %d final status UNKNOWN' % index)
        else:
            problems.extend('session %d: %s' % (index, problem) for problem in
                            _final_status_problems(
                                session.get('final_status') or {}))
        safe_off = session.get('pluto_safe_off') or {}
        if safe_off.get('hardwaregain_db') != planner.PLUTO_SAFE_HARDWARE_GAIN_DB:
            problems.append('session %d Pluto safe-off gain=%r != %.2f'
                            % (index, safe_off.get('hardwaregain_db'),
                               planner.PLUTO_SAFE_HARDWARE_GAIN_DB))
        if safe_off.get('tx_powerdown') != '1':
            problems.append('session %d Pluto safe-off powerdown=%r != 1'
                            % (index, safe_off.get('tx_powerdown')))
    report['on_aligned'] = on_aligned
    report['bit_errors'] = bit_errors
    report['compared_bits'] = compared_bits
    report['aggregate_ber'] = aggregate
    report['off_false_ber'] = sum(
        1 for record in off_records
        if any(_demod_claims_reference(
            (record.get('demod') or {}).get(path) or {})
            for path in ('normal', 'conjugated')))
    report['problems'] = problems
    return not problems


def _bpsk_b_stat_plan(cfg, tx_per_session=1):
    if int(tx_per_session) not in B_BPSK_STAT_TX_PER_SESSION_CHOICES:
        raise ExecutionError('BUDGET: --stat-tx-per-session must be 1 or 2, '
                             'got %r' % (tx_per_session,))
    tx_per_session = int(tx_per_session)
    frames_per_segment = _bpsk_stat_segment_frames(tx_per_session)
    duration = min(float(cfg['pluto']['duration_s']), B_BPSK_MAX_TX_SECONDS)
    total = duration * B_BPSK_STAT_SESSIONS * tx_per_session
    seconds_cap = _bpsk_stat_tx_seconds_cap(tx_per_session)
    windows = []
    for index, seed in enumerate(B_BPSK_STAT_SEEDS):
        segments = [{'segment': segment_index, 'seed': seed,
                     'on_frames': frames_per_segment,
                     'seconds': duration,
                     'board_capture': '4096 samples @60 MSPS ratio 0'}
                    for segment_index in range(tx_per_session)]
        windows.append({
            'index': index, 'name': 'BPSK-STAT-%d' % index, 'seed': seed,
            'board_role': 'rx', 'pluto_role': 'tx_on',
            'tx_per_session': tx_per_session,
            'segments': segments,
            'on_frames': B_BPSK_STAT_ON_FRAMES,
            'off_frames': B_BPSK_STAT_OFF_FRAMES,
            'board_capture': '4096 samples @60 MSPS ratio 0',
            'note': ('%d ON captures over %d TX segment(s); <ready>.stopped '
                     'guards every capture; one safe-off readback per segment; '
                     'OFF after the last safe-off' % (
                         B_BPSK_STAT_ON_FRAMES, tx_per_session))})
    return {
        'schema': planner.SCHEMA,
        'mode': 'bpsk-stat-plan',
        'hardware_accessed': False,
        'direction': 'B',
        'bpsk_stat': True,
        'stat_tx_per_session': tx_per_session,
        'frames_per_segment': frames_per_segment,
        'wiring': ('Pluto TX -> 50 ohm coax -> board RX1; board TX1 open; '
                   'board TX software off and verified; scope disconnected'),
        'seeds': list(B_BPSK_STAT_SEEDS),
        'windows': windows,
        'budget': {'sessions': B_BPSK_STAT_SESSIONS,
                   'board_inits': B_BPSK_STAT_SESSIONS,
                   'board_configures': B_BPSK_STAT_SESSIONS,
                   'board_captures': B_BPSK_STAT_RECORDS,
                   'pluto_tx_starts': B_BPSK_STAT_SESSIONS * tx_per_session,
                   'pluto_tx_configured_seconds': total,
                   'pluto_tx_measured_seconds_gate':
                       'per segment 0<stream_seconds<=2.0 and cumulative<=%.1f'
                       % seconds_cap,
                   'pluto_tx_measured_seconds_cap': seconds_cap,
                   'board_tx_starts': 0},
        'gates': [
            'capture: pre/post io_ok, post_tx_source_selected=false, '
            'post_rx_armed=false, no code-rail clipping, unique capture_id/IQ; '
            'no per-frame TX polls (one before each TX, one after the session)',
            'stream guard: <ready>.stopped (written at stream end before '
            'tb.stop) checked before and after every ON capture; early stop => '
            'BPSK_STAT_STREAM_ENDED_EARLY, no extra noise captures',
            'records: 33 = 3 sessions x (10 ON + 1 OFF); each session has %d TX '
            'segment(s), one safe-off readback per segment, TX-off poll before '
            'segment 2 and after the session; OFF strictly after the last '
            'safe-off' % tx_per_session,
            'aggregate: on_aligned>=27, aggregate_ber=sum(errors)/sum(bits)'
            '<=0.01 over the declared normal path only (never both paths)',
            'OFF negative control: both paths must have reference_sync not '
            'true and no errors/compared/ber claims (off_false_ber=0)',
            'sessions: all 3 closed with disarm ACK, B1 final safety, Pluto '
            'safe-off readback -89.75/powerdown=1 per segment',
            'TX receipt: frozen identity + session seed 1/37/73 + measured '
            'stream_started/stopped/seconds (B2) per segment, cumulative '
            '<=%.1f s' % seconds_cap,
            'budget: 3 sessions/3 init/3 configure/33 capture/%d TX/'
            '<=%.1f s measured/0 board TX; fail-fast, no retry' % (
                B_BPSK_STAT_SESSIONS * tx_per_session, seconds_cap)],
        'level_budget': planner.level_budget(cfg),
    }


def _bpsk_b_plan(cfg):
    duration = min(float(cfg['pluto']['duration_s']), B_BPSK_MAX_TX_SECONDS)
    return {
        'schema': planner.SCHEMA,
        'mode': 'bpsk-plan',
        'hardware_accessed': False,
        'direction': 'B',
        'bpsk': True,
        'wiring': ('Pluto TX -> 50 ohm coax -> board RX1; board TX1 open; '
                   'board TX software off and verified; scope disconnected'),
        'windows': [{'index': 0, 'name': 'BPSK', 'board_role': 'rx',
                     'pluto_role': 'tx_on', 'seconds': duration,
                     'board_capture': '4096 samples @60 MSPS ratio 0',
                     'note': 'one Pluto BPSK/RECT/PRBS7 3 MSym/s playback at '
                             '6 MSPS/sps2; board RX-only'}],
        'budget': {'sessions': 1, 'board_inits': 1, 'board_configures': 1,
                   'board_captures': 1, 'pluto_tx_starts': 1,
                   'pluto_tx_configured_seconds': duration,
                   'pluto_tx_measured_seconds_gate': '0 < stream_seconds <= 2.0',
                   'board_tx_starts': 0},
        'gates': [
            'capture: pre/post io_ok, post_tx_source_selected=false, '
            'post_rx_armed=false, no code-rail clipping',
            'demod: reference_sync=true and reason empty for BOTH the raw and '
            'the conjugated IQ paths',
            'BER: reported separately for both paths; frozen BER=0 and '
            'compared_bits>=64',
            'board TX: no tx.load/tx.start was called; snapshots before/after '
            'the capture and in the final poll show TX source off (not '
            'continuous monitoring)',
            'Pluto: safe-off write followed by independent readback '
            '-89.75/powerdown=1',
            'budget: 1 session/1 init/1 configure/1 capture/1 Pluto TX; the '
            'MEASURED stream_seconds (radio_test stream window) must satisfy '
            '0 < stream_seconds <= 2.0; 0 board TX'],
        'level_budget': planner.level_budget(cfg),
    }


def pluto_serial(cfg):
    return cfg['pluto']['serial']


def main(argv=None):
    p = argparse.ArgumentParser(
        description='PlutoSDR <-> board 433.9 MHz integration planner/executor. '
                    'Offline by default; --dry-run never touches hardware.')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true',
                      help='print the exact plan, budget, gates and commands; no hardware')
    mode.add_argument('--self-test', action='store_true',
                      help='run the offline self-test; no hardware')
    mode.add_argument('--preflight', action='store_true',
                      help='Pluto TX safe-off write + readback (writes '
                           'hardwaregain=-89.75 and powerdown=1); with '
                           '--token-file it also gates the board idle state '
                           '(online, not stale, io_ok, TX off, RX disarmed). '
                           'Not read-only. No RF.')
    mode.add_argument('--execute', action='store_true',
                      help='guarded physical execution; requires real wiring and token')
    p.add_argument('--direction', choices=['A', 'B'],
                   help='A: board TX1 -> Pluto RX; B: Pluto TX -> board RX1')
    p.add_argument('--mode', choices=['tone', 'bpsk', 'bpsk-stat'],
                   default='tone',
                   help='direction-B payload: tone (default), bpsk (single '
                        'capture) or bpsk-stat (3 sessions/33 captures)')
    p.add_argument('--sessions', type=int, default=1,
                   help='1 (four-window smoke) or 3 (12-window extension)')
    p.add_argument('--stat-tx-per-session', type=int, choices=[1, 2], default=1,
                   help='bpsk-stat only: 1 TX segment/session (10 ON) or 2 '
                        'segments/session (5 ON each, <=2.0 s measured each, '
                        'independent safe-off per segment)')
    p.add_argument('--extended', action='store_true',
                   help='authorise the 3-session/12-window extension; session 1 is the smoke')
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--client-dir', type=Path, default=DEFAULT_CLIENT_DIR)
    p.add_argument('--board-port', type=int, default=5000)
    p.add_argument('--token-file', type=Path)
    p.add_argument('--path-confirmed',
                   help='operator description of the actual RF wiring; required for execute')
    p.add_argument('--radioconda', type=Path, default=DEFAULT_RADIOCONDA)
    p.add_argument('--output', type=Path,
                   help='session output directory (created new)')
    p.add_argument('--record', action='store_true',
                   help='execute only: wrap the board client with RecordingClient '
                        'and write out_dir/capture-record.json; missing fields fail')
    p.add_argument('--record-context', type=Path,
                   help='JSON context required by --record (session/boot_id/gain_index/'
                        'rx_attenuation_mdb/release_id/config_revision/waveform)')
    args = p.parse_args(argv)

    if not any((args.dry_run, args.self_test, args.preflight, args.execute)):
        p.print_help()
        return 0

    try:
        cfg = planner.load_config(args.config)
        planner.validate_config(cfg)
    except planner.PlanError as exc:
        print('CONFIG_REFUSED: %s' % exc, file=sys.stderr)
        return 2

    if args.self_test:
        result = planner.self_test(cfg)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result['passed'] else 1

    if args.dry_run:
        if not args.direction:
            print('--dry-run requires --direction A or B', file=sys.stderr)
            return 2
        if args.mode in ('bpsk', 'bpsk-stat') and args.direction != 'B':
            print('--mode bpsk/bpsk-stat is only defined for --direction B',
                  file=sys.stderr)
            return 2
        if args.mode == 'bpsk':
            plan = _bpsk_b_plan(cfg)
            print(json.dumps(plan, indent=2, ensure_ascii=False))
            return 0
        if args.mode == 'bpsk-stat':
            plan = _bpsk_b_stat_plan(cfg, args.stat_tx_per_session)
            print(json.dumps(plan, indent=2, ensure_ascii=False))
            return 0
        try:
            plan = planner.build_plan(args.direction, cfg, args.sessions, args.extended)
        except planner.PlanError as exc:
            print('PLAN_REFUSED: %s' % exc, file=sys.stderr)
            return 2
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    if args.preflight:
        # This path WRITES the Pluto safe-off state (hardwaregain=-89.75 and
        # TX LO powerdown=1) and reads it back.  It is therefore not a
        # read-only command, and with --token-file it additionally gates the
        # board idle state instead of trusting that a poll succeeded.
        out = {'started_utc': _now(), 'hardware_accessed': True,
               'read_only': False,
               'writes': ['Pluto TX hardwaregain=%.6f'
                          % planner.PLUTO_SAFE_HARDWARE_GAIN_DB,
                          'Pluto TX LO powerdown=1'],
               'scope': ('pluto+board' if args.token_file else 'pluto_only'),
               'board_checked': bool(args.token_file),
               'board': None, 'board_gates': None, 'pluto': None,
               'pluto_gate': None, 'passed': False, 'problems': []}
        try:
            pluto = _pluto_safe_off(pluto_serial(cfg))
            out['pluto'] = pluto
            out['pluto_gate'] = {
                'hardwaregain_db': readback.require_db(
                    pluto.get('hardwaregain_db'),
                    planner.PLUTO_SAFE_HARDWARE_GAIN_DB,
                    'Pluto TX hardwaregain after safe-off'),
                'tx_powerdown': readback.parse_powerdown(
                    pluto.get('tx_powerdown'),
                    'Pluto TX LO powerdown after safe-off')}
            if out['pluto_gate']['tx_powerdown'] != '1':
                raise ExecutionError('Pluto TX LO powerdown is not 1 after safe-off')
            if args.token_file:
                RfStatusClient, _ = _load_board_client(args.client_dir)
                client = RfStatusClient(port=args.board_port)
                try:
                    status = _poll_status_any_with_retry(client)
                    out['board'] = status.to_dict()
                    ok, problems, detail = readback.board_idle_gate(status)
                finally:
                    client.close()
                out['board_gates'] = dict(detail, passed=ok, problems=problems)
                if not ok:
                    out['problems'].extend(problems)
                else:
                    out['passed'] = True
            else:
                out['notes'] = ('pure Pluto preflight: the board idle state was '
                                'NOT checked (no --token-file was given), so this '
                                'result says nothing about the board')
                out['passed'] = True
        except Exception as exc:
            out['passed'] = False
            out['error'] = '%s: %s' % (type(exc).__name__, exc)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out['passed'] else 1

    if args.execute:
        if not args.direction:
            print('--execute requires --direction A or B', file=sys.stderr)
            return 2
        if args.mode in ('bpsk', 'bpsk-stat') and args.direction != 'B':
            print('--mode bpsk/bpsk-stat is only defined for --direction B',
                  file=sys.stderr)
            return 2
        if not args.path_confirmed or not args.token_file:
            print('--execute requires --path-confirmed and --token-file', file=sys.stderr)
            return 2
        if args.record and not args.record_context:
            print('--record requires --record-context JSON', file=sys.stderr)
            return 2
        if args.mode == 'bpsk-stat':
            if args.sessions != B_BPSK_STAT_SESSIONS or args.extended:
                print('EXECUTE_REFUSED: --mode bpsk-stat requires exactly %d '
                      'sessions (no --extended)' % B_BPSK_STAT_SESSIONS,
                      file=sys.stderr)
                return 2
        elif args.sessions != 1 or args.extended:
            print('EXECUTE_REFUSED: the multi-session executor is not part of this '
                  'offline preparation; use --dry-run --sessions 3 --extended for '
                  'the budget preview', file=sys.stderr)
            return 2
        try:
            args.sessions = 1 if args.sessions is None else args.sessions
            if args.mode != 'bpsk-stat':
                planner.budget(args.direction, args.sessions,
                               extended=args.extended)
        except planner.PlanError as exc:
            print('BUDGET_REFUSED: %s' % exc, file=sys.stderr)
            return 2
        out_dir = args.output or (ROOT / 'results' /
                                  (datetime.now().strftime('%Y%m%d-%H%M%S-%f') +
                                   '-' + args.direction +
                                   ({'bpsk': '-bpsk', 'bpsk-stat': '-bpsk-stat'}
                                    .get(args.mode, ''))))
        out_dir.mkdir(parents=True, exist_ok=False)
        started = _now()
        try:
            if args.direction == 'A':
                report = _run_a(args, cfg, out_dir)
            elif args.mode == 'bpsk-stat':
                report = _run_b_bpsk_stat(args, cfg, out_dir)
            elif args.mode == 'bpsk':
                report = _run_b_bpsk(args, cfg, out_dir)
            else:
                report = _run_b(args, cfg, out_dir)
            report['started_utc'] = started
            report['finished_utc'] = _now()
            report['path_confirmed'] = args.path_confirmed
            report['hardware_accessed'] = True
            _write_json(out_dir / 'session.json', report)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report.get('passed') else 1
        except Exception as exc:
            failure = {'started_utc': started, 'finished_utc': _now(),
                       'direction': args.direction, 'hardware_accessed': True,
                       'passed': False, 'error': '%s: %s' % (type(exc).__name__, exc),
                       'cleanup_required': True}
            cleanup = getattr(exc, 'cleanup_report', None)
            if isinstance(cleanup, dict):
                failure.update(cleanup)
            _write_json(out_dir / 'session.json', failure)
            print(json.dumps(failure, indent=2, ensure_ascii=False))
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
