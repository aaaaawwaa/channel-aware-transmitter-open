"""Minimal Stage 0: Pluto TX -> Pluto RX self-loop, three windows.

    OFF_PRE (receive only) -> TONE (one TX start) -> OFF_POST (receive only)

Offline paths (no hardware at all):
    --help / --dry-run / --self-test

Physical path (requires the operator's wiring description):
    --execute --path-confirmed "<actual wiring>"

Stage 0 touches **Pluto only**: no board, no COM11, no token, no nonce.  It
proves the SDR's own transmit/receive chain before any board is involved, so
the older `run.ps1 loopback` entry (one ON stream, no OFF contrast, no clipping
evidence, no LO-off target readback) cannot stand in for it.

USB ownership is phased, never overlapping:

1. libiio pre-phase: with the TX LO still powered down, write and verify the
   target 433900000 Hz LO frequency and the target -60 dB attenuation, read
   hardwaregain and powerdown back, then enable the LO and read that back.
   Both targets are proven before the LO is enabled, so the OFF_PRE window
   cannot inherit the previous LO (the real 2026-10-19 receipt showed the LO
   sitting at 2.45 GHz before the TONE graph tuned it).
2. GNU Radio OFF_PRE: a receive-only graph; no TX block is started.
3. GNU Radio TONE: one graph with the TX sink and the RX source; exactly one
   TX start, stopped inside the 2 s budget with a guard.
4. GNU Radio OFF_POST: a receive-only graph after the TX graph is gone.
5. libiio post-phase: reopen libiio only after GNU Radio released USB; write
   -89.75 dB and powerdown=1 and read each one back twice.

No readback is attempted while a stream is running (that would need a second
USB context).  On any failure the transmitter is forced off, the failure and
the cleanup result are saved, and the TX is **never** retried automatically.
If the driver hangs so that the safe-off cannot be confirmed, the run stays
UNKNOWN with cleanup_required=true.  The measured TONE on-time ends only once
the graph stop is confirmed; an unconfirmed stop never claims a budget pass.

The 2 s transmit budget covers **only** the modulated TONE graph.  The TX LO is
enabled from the pre-phase until the post-phase readback, so it is on in all
three capture windows (roughly three windows of LO-on time).  OFF_PRE and
OFF_POST measure the receiver noise floor with the LO running and **no TX
graph**, not a LO-off baseline, and tx_seconds_measured is the TONE graph's
on-time only.

Stage 0 does **not** write the RX rf_bandwidth: the real GNU Radio 3.10 iio
source has no set_bandwidth (only the sink does), so the RX keeps the device's
current bandwidth.  The report states that policy as rx_bandwidth_policy
instead of claiming a 1.5 MHz write.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
RADIO_DIR = PROJECT / 'sdr_testbench'
DEFAULT_CONFIG = ROOT / 'config' / 'stage0_selfloop.json'
if str(RADIO_DIR) not in sys.path:
    sys.path.insert(0, str(RADIO_DIR))

import planner  # noqa: E402  (same directory)
import readback  # noqa: E402  (same directory)
import radio_test  # noqa: E402  (sdr_testbench/radio_test.py, pure at import)


class Stage0Error(readback.ReadbackError):
    """A Stage 0 refusal.  Also a ReadbackError: every gate is a readback gate."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n',
                    encoding='utf-8')
    return path


def hardware_gain_write(db):
    """The exact text libiio receives for a hardwaregain write."""
    return '%.6f' % float(db)


def pre_gain_text():
    """The exact hardwaregain text the pre-phase writes; locked by --self-test."""
    return hardware_gain_write(planner.STAGE0_TX_HARDWARE_GAIN_DB)


def post_gain_text():
    """The exact hardwaregain text the post-phase writes; locked by --self-test."""
    return hardware_gain_write(planner.STAGE0_CEILING_HARDWARE_GAIN_DB)


def lo_frequency_text():
    """The exact TX LO frequency text the pre-phase writes before enabling."""
    return str(int(planner.STAGE0_LO_HZ))


def tone_seconds(cfg, budget_s, guard_s):
    """How long the TONE graph transmits: min(duration_s, budget - guard).

    The configured window duration is the intent; budget minus guard is the
    hard stop.  Using the budget alone (ignoring duration_s) would misreport a
    shorter requested tone, so the configured duration is always the first
    term.
    """
    return max(0.0, min(float(cfg['duration_s']),
                        float(budget_s) - float(guard_s)))


def _require_hz(value, expected_hz, label):
    """Parse an integer Hz readback and require it to equal expected_hz."""
    text = str(value).strip()
    if not text.lstrip('+').isdigit():
        raise Stage0Error('%s is not an integer Hz value: %r' % (label, value))
    parsed = int(text)
    if parsed != int(expected_hz):
        raise Stage0Error('%s is %d Hz, not %d Hz (raw %r)'
                          % (label, parsed, int(expected_hz), value))
    return parsed


def check_stop_receipt(receipt, expected_gain_db=planner.STAGE0_CEILING_HARDWARE_GAIN_DB):
    """Gate a saved radio_test receipt through the shared strict parser."""
    try:
        return readback.pluto_post_readback(receipt,
                                            expected_gain_db=expected_gain_db,
                                            expected_powerdown='1')
    except readback.ReadbackError as exc:
        raise Stage0Error('Pluto stop readback refused: %s' % exc)


def _radio_config(cfg):
    """The exact radio_test configuration keys for this Stage 0 run."""
    return {'serial': cfg['serial'], 'center_hz': cfg['center_hz'],
            'sample_rate': cfg['sample_rate'], 'bandwidth_hz': cfg['bandwidth_hz'],
            'tone_hz': cfg['tone_hz'], 'amplitude': cfg['amplitude'],
            'tx_attenuation_db': cfg['tx_attenuation_db'],
            'rx_gain_db': cfg['rx_gain_db'], 'duration_s': cfg['duration_s'],
            'capture_samples': cfg['capture_samples']}


def _release_usb():
    """Let the previous USB owner be collected before the next phase opens it."""
    gc.collect()
    time.sleep(0.25)


def _libiio_pre(cfg):
    """Phase 1: target LO frequency and attenuation proven while the LO is off."""
    ctx = None
    uri = None
    phy = None
    try:
        ctx, uri = radio_test.context_for(cfg['serial'])
        phy = ctx.find_device('ad9361-phy')
        if phy is None:
            raise Stage0Error('ad9361-phy not found in the Pluto context')
        tx = phy.find_channel('voltage0', True)
        lo = phy.find_channel('altvoltage1', True)
        # The LO is explicitly off before anything else, so both targets are
        # written and read back with no transmit path enabled.
        lo.attrs['powerdown'].value = '1'
        out = {'stage': 'libiio_pre', 'uri': uri,
               'tx_lo_frequency_raw': lo.attrs['frequency'].value}
        # Write and verify the target LO frequency while the LO is still
        # powered down.  A freshly powered Pluto keeps its previous LO (the
        # real receipt showed 2.45 GHz), so without this the whole OFF_PRE
        # window could radiate at the wrong frequency before the TONE graph
        # ever calls set_frequency.
        lo.attrs['frequency'].value = lo_frequency_text()
        lo_frequency_set = lo.attrs['frequency'].value
        out['tx_lo_frequency_set'] = lo_frequency_set
        out['tx_lo_frequency_hz'] = _require_hz(
            lo_frequency_set, planner.STAGE0_LO_HZ,
            'Pluto TX LO frequency before the LO is enabled')
        out['tx_lo_frequency_ok'] = True
        tx.attrs['hardwaregain'].value = pre_gain_text()
        gain_raw = tx.attrs['hardwaregain'].value
        powerdown_before = lo.attrs['powerdown'].value
        out['hardwaregain_raw'] = gain_raw
        out['powerdown_before_enable'] = powerdown_before
        readback.require_db(gain_raw, planner.STAGE0_TX_HARDWARE_GAIN_DB,
                            'Pluto TX hardwaregain before the LO is enabled')
        if readback.parse_powerdown(powerdown_before) != '1':
            raise Stage0Error('the TX LO was not powered down while the target '
                              'attenuation and frequency were written')
        out['attenuation_db'] = float(planner.STAGE0_TX_ATTENUATION_DB)
        out['hardwaregain_db'] = float(planner.STAGE0_TX_HARDWARE_GAIN_DB)
        # Only now enable the TX LO; the target values above are proven.
        lo.attrs['powerdown'].value = '0'
        out['powerdown_after_enable'] = readback.parse_powerdown(
            lo.attrs['powerdown'].value)
        if out['powerdown_after_enable'] != '0':
            raise Stage0Error('the TX LO did not report powerdown=0 after enable')
        return out
    finally:
        try:
            del phy
        except Exception:
            pass
        ctx = None
        _release_usb()


def _libiio_post(cfg):
    """Phase 5: force the ceiling attenuation and the LO powerdown, then read."""
    ctx = None
    phy = None
    try:
        ctx, uri = radio_test.context_for(cfg['serial'])
        phy = ctx.find_device('ad9361-phy')
        if phy is None:
            raise Stage0Error('ad9361-phy not found in the Pluto context')
        tx = phy.find_channel('voltage0', True)
        lo = phy.find_channel('altvoltage1', True)
        tx.attrs['hardwaregain'].value = post_gain_text()
        lo.attrs['powerdown'].value = '1'
        # Both the ceiling attenuation and the LO powerdown are read twice and
        # both reads must agree (the documentation promised two readbacks).
        gain_raw = tx.attrs['hardwaregain'].value
        powerdown_raw = lo.attrs['powerdown'].value
        gain_raw_second = tx.attrs['hardwaregain'].value
        powerdown_raw_second = lo.attrs['powerdown'].value
        out = {'stage': 'libiio_post', 'uri': uri,
               'hardwaregain_raw': gain_raw,
               'hardwaregain_raw_second_read': gain_raw_second,
               'tx_powerdown_raw': powerdown_raw,
               'tx_powerdown_raw_second_read': powerdown_raw_second,
               'tx_lo_frequency_raw': lo.attrs['frequency'].value}
        readback.require_db(gain_raw, planner.STAGE0_CEILING_HARDWARE_GAIN_DB,
                            'Pluto TX hardwaregain after the stop')
        readback.require_db(gain_raw_second,
                            planner.STAGE0_CEILING_HARDWARE_GAIN_DB,
                            'Pluto TX hardwaregain after the stop (second read)')
        powerdown = readback.parse_powerdown(powerdown_raw)
        powerdown_second = readback.parse_powerdown(powerdown_raw_second)
        if powerdown != '1' or powerdown_second != '1':
            raise Stage0Error(
                'the TX LO powerdown readback is %r / %r, not 1 / 1'
                % (powerdown, powerdown_second))
        out['attenuation_db'] = float(planner.STAGE0_CEILING_DB)
        out['hardwaregain_db'] = float(planner.STAGE0_CEILING_HARDWARE_GAIN_DB)
        out['tx_powerdown'] = powerdown
        out['tx_powerdown_second_read'] = powerdown_second
        return out
    finally:
        try:
            del phy
        except Exception:
            pass
        ctx = None
        _release_usb()


def _capture_rx_only(cfg, uri, seconds):
    """Phases 2 and 4: a receive-only graph; no TX block exists in it."""
    from gnuradio import blocks, gr, iio
    c = _radio_config(cfg)
    tb = gr.top_block('stage0 rx-only', catch_exceptions=True)
    error = None
    iq = None
    try:
        rx = iio.fmcomms2_source_fc32(uri, [True, True], 32768)
        radio_test.configure(rx, c, False)
        # Stage 0 does not set the RX rf_bandwidth: the real GNU Radio 3.10
        # iio source has no set_bandwidth (only the sink does), so the RX keeps
        # the device's current bandwidth.  configure() applies its Auto filter.
        head = blocks.head(gr.sizeof_gr_complex, c['capture_samples'])
        sink = blocks.vector_sink_c()
        tb.connect(rx, head, sink)
        tb.start()
        time.sleep(seconds)
        tb.stop()
        tb.wait()
        iq = np.asarray(sink.data(), dtype=np.complex64)
    except BaseException as exc:  # noqa: BLE001 - returned as text, not a traceback
        error = repr(exc)
    finally:
        try:
            tb.stop()
            tb.wait()
            tb.disconnect_all()
        except BaseException as exc:  # noqa: BLE001
            error = (error or '') + '; graph cleanup: ' + repr(exc)
        # Drop every reference a traceback could keep alive: the Stage0Error
        # below carries this frame, and a graph object left in a local would
        # keep USB out of reach of the forced safe-off readback.
        rx = head = sink = tb = None
    if error:
        raise Stage0Error('GNU Radio receive-only window failed: ' + error)
    return iq


def _capture_tone(cfg, uri, budget_s, guard_s, stats=None):
    """Phase 3: the single TX start, bounded by duration_s and the budget guard.

    'stats' is the run-wide TX accounting dict.  The attempt is recorded
    *before* tb.start() so a failure after the start is still reported as an
    attempted transmission with a measured on-time, not as zero.  The end of
    the attempt is recorded only **after** the graph is confirmed stopped
    (stop() and wait() returned); if that cannot be confirmed the report never
    claims a budget pass.
    """
    from gnuradio import analog, blocks, gr, iio
    if stats is None:
        stats = {'tx_starts': 0, 'tx_started': None, 'tx_stopped': None,
                 'tx_stop_confirmed': False}
    stats.setdefault('tx_stop_confirmed', False)
    c = _radio_config(cfg)
    seconds = tone_seconds(cfg, budget_s, guard_s)
    tb = gr.top_block('stage0 tone', catch_exceptions=True)
    error = None
    iq = None
    started = None
    stopped = None
    stop_confirmed = False
    try:
        source = analog.sig_source_c(c['sample_rate'], analog.GR_COS_WAVE,
                                     c['tone_hz'], c['amplitude'], 0)
        rx = iio.fmcomms2_source_fc32(uri, [True, True], 32768)
        radio_test.configure(rx, c, False)
        # The RX source has no set_bandwidth (real GNU Radio 3.10 API), so the
        # RX bandwidth stays at the device's current value; the report states
        # that policy instead of claiming a 1.5 MHz write.
        tx = iio.fmcomms2_sink_fc32(uri, [True, True], 32768, False)
        # The reviewed order: frequency -> samplerate -> bandwidth ->
        # attenuation -> filter.  set_attenuation is the last gain write
        # before the stream starts.
        radio_test.configure(tx, c, True)
        head = blocks.head(gr.sizeof_gr_complex, c['capture_samples'])
        sink = blocks.vector_sink_c()
        tb.connect(source, tx)
        tb.connect(rx, head, sink)
        # Record the TX attempt before start(); a failure in start()/wait()
        # still counts as an attempted transmission.
        stats['tx_starts'] += 1
        started = time.monotonic()
        stats['tx_started'] = started
        tb.start()
        deadline = started + seconds
        while time.monotonic() < deadline:
            time.sleep(0.02)
        tb.stop()
        tb.wait()
        # The graph is confirmed stopped only once stop() and wait() returned;
        # only then is the end of the measured transmission recorded.
        stop_confirmed = True
        stopped = time.monotonic()
        stats['tx_stopped'] = stopped
        stats['tx_stop_confirmed'] = True
        iq = np.asarray(sink.data(), dtype=np.complex64)
    except BaseException as exc:  # noqa: BLE001
        error = repr(exc)
    finally:
        if not stop_confirmed:
            try:
                tb.stop()
                tb.wait()
                stop_confirmed = True
            except BaseException as exc:  # noqa: BLE001
                error = (error or '') + '; graph cleanup: ' + repr(exc)
        if stop_confirmed and started is not None:
            # The cleanup stop()+wait() above is part of the on-time: record
            # the end only once the graph is confirmed stopped.
            stats['tx_stop_confirmed'] = True
            if stats.get('tx_stopped') is None:
                stats['tx_stopped'] = time.monotonic()
        try:
            tb.disconnect_all()
        except BaseException as exc:  # noqa: BLE001
            error = (error or '') + '; graph cleanup: ' + repr(exc)
        # Drop every reference a traceback could keep alive (B2): the raised
        # Stage0Error carries this frame, and a graph object left in a local
        # keeps USB out of reach of the forced safe-off readback.
        source = rx = tx = head = sink = tb = None
    if error:
        raise Stage0Error('GNU Radio tone window failed: ' + error)
    return iq, started, stopped


def _window_record(out_dir, name, iq, cfg, tx_on, seconds, tx_seconds=None):
    window_dir = Path(out_dir) / name
    window_dir.mkdir(parents=True, exist_ok=True)
    np.save(str(window_dir / 'iq.npy'), iq)
    analysis = planner.analyse_stage0_window(
        iq, float(cfg['sample_rate']), float(cfg['tone_hz']))
    record = {'name': name, 'tx_on': bool(tx_on), 'rx_only': (not tx_on),
              'seconds': float(seconds), 'iq_file': str(window_dir / 'iq.npy'),
              'iq_samples': int(len(iq)), 'analysis': analysis}
    if tx_seconds is not None:
        record['tx_seconds_measured'] = float(tx_seconds)
    _write_json(window_dir / 'result.json', record)
    return record


def _merge_tx_stats(report, stats):
    """Copy the run-wide TX accounting into the report (success and failure)."""
    report['tx_starts'] = int(stats.get('tx_starts', 0))
    started = stats.get('tx_started')
    stopped = stats.get('tx_stopped')
    confirmed = bool(stats.get('tx_stop_confirmed'))
    report['tx_stop_confirmed'] = confirmed
    if started is not None and stopped is not None and confirmed:
        seconds = max(0.0, float(stopped) - float(started))
        report['tx_seconds_measured'] = seconds
        budget = report.get('budget') or {}
        budget_s = float(budget.get('tx_budget_s', planner.STAGE0_TX_BUDGET_S))
        report['tx_budget_ok'] = bool(seconds <= budget_s)
    elif started is not None:
        # An attempt exists but the graph stop could not be confirmed: never
        # claim the transmit budget passed.
        report['tx_budget_ok'] = False


def run(cfg, out_dir, path_confirmed):
    """Execute Stage 0 and always save a report, pass or fail."""
    out_dir = Path(out_dir)
    plan = planner.stage0_plan(cfg)
    stats = {'tx_starts': 0, 'tx_started': None, 'tx_stopped': None}
    report = {'schema': planner.STAGE0_SCHEMA, 'mode': 'stage0-execute',
              'hardware_accessed': True, 'pluto_only': True,
              'board_actions': 0, 'com11': False, 'token_used': False,
              'nonce_generated': False, 'path_confirmed': path_confirmed,
              'started_utc': _now(), 'status': 'RUNNING', 'passed': False,
              'parameters': plan['parameters'], 'budget': plan['budget'],
              'tx_budget_scope': plan['tx_budget_scope'],
              'lo_on_windows': list(plan['lo_on_windows']),
              'rx_bandwidth_policy': plan['rx_bandwidth_policy'],
              'usb_phases': [], 'second_context_during_stream': False,
              'windows': [], 'tx_starts': 0, 'tx_stop_confirmed': False,
              'cleanup_required': False, 'released_usb_before_cleanup': False,
              'tx_retries': 0}
    windows_by_name = {}
    iqs_by_name = {}
    try:
        report['pre_tx_readback'] = _libiio_pre(cfg)
        report['usb_phases'].append('libiio_pre')

        iq = _capture_rx_only(cfg, report['pre_tx_readback']['uri'],
                              float(cfg['duration_s']))
        report['usb_phases'].append('gnuradio_off_pre')
        iqs_by_name['OFF_PRE'] = iq
        record = _window_record(out_dir, 'OFF_PRE', iq, cfg, False,
                                cfg['duration_s'])
        report['windows'].append(record)
        windows_by_name['OFF_PRE'] = record['analysis']
        _release_usb()

        iq, started, stopped = _capture_tone(cfg, report['pre_tx_readback']['uri'],
                                             plan['budget']['tx_budget_s'],
                                             plan['budget']['tx_guard_s'],
                                             stats)
        report['usb_phases'].append('gnuradio_tone')
        _merge_tx_stats(report, stats)
        tx_seconds = report['tx_seconds_measured']
        iqs_by_name['TONE'] = iq
        record = _window_record(out_dir, 'TONE', iq, cfg, True, cfg['duration_s'],
                                tx_seconds=tx_seconds)
        report['windows'].append(record)
        windows_by_name['TONE'] = record['analysis']
        _release_usb()

        iq = _capture_rx_only(cfg, report['pre_tx_readback']['uri'],
                              float(cfg['duration_s']))
        report['usb_phases'].append('gnuradio_off_post')
        iqs_by_name['OFF_POST'] = iq
        record = _window_record(out_dir, 'OFF_POST', iq, cfg, False,
                                cfg['duration_s'])
        report['windows'].append(record)
        windows_by_name['OFF_POST'] = record['analysis']
        _release_usb()

        report['post_stop_readback'] = _libiio_post(cfg)
        report['usb_phases'].append('libiio_post')
        report['final_tx_stop_state'] = {
            'attenuation_db': report['post_stop_readback']['attenuation_db'],
            'tx_powerdown': report['post_stop_readback']['tx_powerdown'],
            'source': 'libiio readback after the GNU Radio graph was stopped'}
        report['cleanup_required'] = False
        verdict = planner.evaluate_stage0_windows(
            windows_by_name,
            narrowband=planner.matched_narrowband_on_off(
                iqs_by_name['TONE'], iqs_by_name['OFF_PRE'],
                iqs_by_name['OFF_POST'], float(cfg['sample_rate'])))
        report['on_off_comparison'] = verdict['on_off_comparison']
        report['clipping_evidence'] = {
            name: {'abs_max_iq': a['abs_max_iq'],
                   'full_scale_headroom_db': a['full_scale_headroom_db'],
                   'full_scale_fraction': a['full_scale_fraction'],
                   'hard_clip_fraction': a['hard_clip_fraction'],
                   'clipped': a['clipped']}
            for name, a in windows_by_name.items()}
        report['tx_digital_evidence'] = plan['tx_digital_evidence']
        problems = list(verdict['problems'])
        if not report.get('tx_budget_ok', False):
            problems.append('measured TX on-time %.3f s exceeds the %.3f s budget'
                            % (tx_seconds, plan['budget']['tx_budget_s']))
        if report['tx_starts'] != 1:
            problems.append('TX starts %r != 1' % (report['tx_starts'],))
        report['problems'] = problems
        report['passed'] = not problems
        report['status'] = 'STAGE0_PASS' if report['passed'] else 'STAGE0_FAIL'
    except BaseException as exc:  # noqa: BLE001 - the report must survive
        report['error'] = '%s: %s' % (type(exc).__name__, exc)
        report['status'] = 'FAILED'
        report['tx_retries'] = 0
        # Drop any graph the failed frame still holds, let USB go, and only
        # then reopen libiio to force the transmitter off.  The release and the
        # forced safe-off share one handler: if the release itself is
        # interrupted (for example Ctrl-C during its settle wait), the safe-off
        # did not run, so the result must stay UNKNOWN/cleanup_required rather
        # than reporting a clean stop.
        try:
            _release_usb()
            report['usb_phases'].append('release_usb_before_cleanup')
            report['released_usb_before_cleanup'] = True
            report['cleanup'] = _libiio_post(cfg)
            report['usb_phases'].append('libiio_cleanup')
            report['cleanup_required'] = False
            report['final_tx_stop_state'] = {
                'attenuation_db': report['cleanup']['attenuation_db'],
                'tx_powerdown': report['cleanup']['tx_powerdown'],
                'source': 'forced cleanup after the failure'}
        except BaseException as cleanup_exc:  # noqa: BLE001
            report['cleanup_error'] = '%s: %s' % (type(cleanup_exc).__name__,
                                                  cleanup_exc)
            report['cleanup_required'] = True
            report['status'] = 'UNKNOWN'
    finally:
        # B1: the TX accounting covers both paths.  A failure after the start
        # must not be written as tx_starts=0 / no measured time.
        _merge_tx_stats(report, stats)
        report['finished_utc'] = _now()
        _write_json(out_dir / 'stage0.json', report)
    return report


def _gnuradio_available():
    try:
        import iio  # noqa: F401
        import gnuradio  # noqa: F401
    except Exception:
        return False
    return True


def _summary(report):
    return {key: report.get(key) for key in
            ('status', 'passed', 'error', 'problems', 'tx_starts',
             'tx_stop_confirmed', 'tx_seconds_measured', 'tx_budget_ok',
             'tx_budget_scope', 'lo_on_windows', 'rx_bandwidth_policy',
             'second_context_during_stream',
             'pre_tx_readback', 'post_stop_readback', 'final_tx_stop_state',
             'on_off_comparison', 'clipping_evidence', 'cleanup_required',
             'released_usb_before_cleanup', 'cleanup_error', 'usb_phases',
             'started_utc', 'finished_utc')
            if key in report}


def stage0_gain_text_checks():
    """Executor-side text locks: the exact libiio text each phase writes."""
    return [
        ('stage0-gain-write-text',
         'ok' if pre_gain_text() == '-60.000000' and
         post_gain_text() == '-89.750000' else 'FAIL'),
        ('stage0-lo-frequency-write',
         'ok' if lo_frequency_text() == '433900000' else 'FAIL'),
    ]


def stage0_offline_self_test(cfg=None):
    """planner.stage0_self_test plus the executor-side text locks."""
    result = planner.stage0_self_test(cfg)
    result['checks'] = list(result['checks']) + stage0_gain_text_checks()
    result['passed'] = all(status == 'ok' for _, status in result['checks'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Minimal Stage 0: Pluto TX -> Pluto RX self-loop '
                    '(OFF_PRE -> TONE -> OFF_POST). Offline by default; '
                    '--dry-run and --self-test never touch hardware. '
                    '--execute touches Pluto only: no board, no COM11, no token.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true',
                      help='print the exact plan, budget and gates; no hardware')
    mode.add_argument('--self-test', action='store_true',
                      help='run the offline planner/analysis self-test; no hardware')
    mode.add_argument('--execute', action='store_true',
                      help='physical Stage 0; requires --path-confirmed')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--path-confirmed',
                        help='operator description of the actual RF wiring')
    parser.add_argument('--output', type=Path,
                        help='session output directory (created new)')
    args = parser.parse_args(argv)

    if not any((args.dry_run, args.self_test, args.execute)):
        parser.print_help()
        return 0

    try:
        cfg = planner.load_config(args.config)
        planner.validate_stage0_config(cfg)
    except planner.PlanError as exc:
        print('CONFIG_REFUSED: %s' % exc, file=sys.stderr)
        return 2

    if args.self_test:
        result = stage0_offline_self_test(cfg)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result['passed'] else 1

    if args.dry_run:
        print(json.dumps(planner.stage0_plan(cfg), indent=2, ensure_ascii=False))
        return 0

    if not args.path_confirmed:
        print('--execute requires --path-confirmed describing the actual wiring',
              file=sys.stderr)
        return 2
    if not _gnuradio_available():
        print('--execute requires an interpreter with libiio and GNU Radio '
              '(use <LOCAL_PATH>', file=sys.stderr)
        return 2
    out_dir = args.output or (ROOT / 'results' /
                              (datetime.now().strftime('%Y%m%d-%H%M%S-%f') +
                               '-stage0'))
    out_dir.mkdir(parents=True, exist_ok=False)
    report = run(cfg, out_dir, args.path_confirmed)
    summary = _summary(report)
    summary['result_dir'] = str(out_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if report.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())