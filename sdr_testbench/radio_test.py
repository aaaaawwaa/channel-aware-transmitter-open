"""Bounded GNU Radio tests for a serial-selected Pluto. Never opens serial ports."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parent

# Frozen BPSK TX playback contract (task-14).  These are the contract values
# validated by board_integration.planner.validate_config; they are not tunables.
BPSK_TX_MODE = 'tx-bpsk'
BPSK_FS_HZ = 6000000
BPSK_SYMBOL_RATE_HZ = 3000000
BPSK_SPS = 2
BPSK_SEED = 1
# task-17 A1: statistical direction-B acceptance may use the frozen seed
# triplet; anything else is refused (same PRBS7 sequence, phase-shifted).
BPSK_SEEDS = (1, 37, 73)
BPSK_AMPLITUDE = 0.1
BPSK_TX_ATTENUATION_DB = 60.0
BPSK_MAX_DURATION_S = 2.0
# The normal path sleeps slightly less than the configured duration so the
# MEASURED graph stream window stays <= 2.0 s despite scheduling overshoot.
BPSK_STREAM_GUARD_S = 0.15
# 2032 symbols = 16 * 127: a repeat of the vector continues the PRBS7 period,
# so the loop point is not a discontinuity in the frozen bit sequence.
BPSK_PAYLOAD_SYMBOLS = 2032


def validate_bpsk_tx(c, path_confirmed):
    """Refuse anything that is not the frozen 6 MSPS/sps2 BPSK TX playback."""
    import math
    if not isinstance(path_confirmed, str) or not path_confirmed.strip():
        raise ValueError('BPSK TX requires --path-confirmed describing the wiring')
    if not isinstance(c.get('serial'), str) or not c['serial']:
        raise ValueError('BPSK TX requires the explicit Pluto serial')
    if not isinstance(c.get('center_hz'), (int, float)) or not (
            325e6 <= c['center_hz'] <= 3.8e9):
        raise ValueError('BPSK TX center_hz outside the Pluto RF range')
    if c.get('sample_rate') != BPSK_FS_HZ:
        raise ValueError('BPSK TX sample_rate must be 6000000')
    if c.get('bandwidth_hz') != 1500000:
        raise ValueError('BPSK TX bandwidth_hz must be 1500000')
    if c.get('amplitude') != BPSK_AMPLITUDE:
        raise ValueError('BPSK TX amplitude must be the frozen 0.1')
    if 'symbol_rate_hz' in c:
        if (type(c['symbol_rate_hz']) is not int
                or c['symbol_rate_hz'] != BPSK_SYMBOL_RATE_HZ):
            raise ValueError('BPSK TX symbol_rate_hz must be the frozen 3000000')
    if c.get('tx_attenuation_db') != BPSK_TX_ATTENUATION_DB:
        raise ValueError('BPSK TX attenuation must be the frozen 60 dB')
    seed = c.get('prbs7_seed', BPSK_SEED)
    if type(seed) is not int or seed not in BPSK_SEEDS:
        raise ValueError('BPSK TX prbs7_seed must be one of 1, 37, 73')
    duration = c.get('duration_s')
    if (not isinstance(duration, (int, float)) or not math.isfinite(duration)
            or not 0 < duration <= BPSK_MAX_DURATION_S):
        raise ValueError('BPSK TX duration_s must be >0 and <= 2.0 s')
    if not isinstance(c.get('capture_samples'), int) or c['capture_samples'] < 4096:
        raise ValueError('BPSK TX config needs a valid capture_samples')


def bpsk_tx_payload(c):
    """Frozen planner payload at 6 MSPS/sps2 as complex baseband in [-1, 1].

    The planner (sdr_testbench/board_integration/planner.py) owns the frozen
    PRBS7/RECT generator; this playback path never re-implements it.
    """
    import sys
    planner_dir = ROOT / 'board_integration'
    if str(planner_dir) not in sys.path:
        sys.path.insert(0, str(planner_dir))
    import planner
    payload_bytes, meta = planner.bpsk_rect_payload(
        c.get('prbs7_seed', BPSK_SEED), BPSK_FS_HZ, BPSK_SYMBOL_RATE_HZ,
        float(c['amplitude']), symbols=BPSK_PAYLOAD_SYMBOLS)
    codes = np.frombuffer(payload_bytes, dtype='<i2').reshape(-1, 2)
    z = (codes[:, 0].astype(float) + 1j * codes[:, 1].astype(float)) / 2047.0
    meta = dict(meta)
    meta.update(mode='BPSK', differential_encoding=False,
                duration_s=float(c['duration_s']),
                attenuation_db=float(c['tx_attenuation_db']))
    return [complex(v) for v in z], meta


def validate(c):
    import math
    for key in ('center_hz', 'sample_rate', 'bandwidth_hz', 'tone_hz',
                'amplitude', 'tx_attenuation_db', 'rx_gain_db', 'duration_s',
                'capture_samples'):
        if not isinstance(c[key], (float, int)) or not math.isfinite(c[key]):
            raise ValueError('Non-finite/nonnumeric ' + key)
    if not c['serial'] or not 325e6 <= c['center_hz'] <= 3.8e9:
        raise ValueError('Explicit serial and standard Pluto RF range required')
    if not 2084000 <= c['sample_rate'] <= 3000000:
        raise ValueError('Smoke-test sample rate must be 2.084–3 MSPS')
    if not 200000 <= c['bandwidth_hz'] <= c['sample_rate']:
        raise ValueError('Invalid RF bandwidth')
    if not 0 < abs(c['tone_hz']) < min(c['sample_rate'], c['bandwidth_hz']) / 2:
        raise ValueError('Tone must be inside Nyquist and RF passband')
    if not 0 < c['amplitude'] <= .25 or not 40 <= c['tx_attenuation_db'] <= 89.75:
        raise ValueError('Initial smoke test requires amplitude <=.25, attenuation >=40 dB')
    if not 0 <= c['rx_gain_db'] <= 30 or not 0 < c['duration_s'] <= 5:
        raise ValueError('Invalid gain/duration')
    if not isinstance(c['capture_samples'], int) or not 4096 <= c['capture_samples'] <= 1048576:
        raise ValueError('Invalid capture size')


def summarize(x, fs):
    x = np.asarray(x, dtype=np.complex64)
    if len(x) < 4096 or not np.isfinite(x).all():
        raise ValueError('Insufficient or invalid IQ')
    y = x[-min(len(x), 65536):]
    spectrum = abs(np.fft.fftshift(np.fft.fft((y-y.mean()) * np.hanning(len(y))))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(len(y), 1 / fs))
    index = int(np.argmax(spectrum))
    return {'samples': len(x), 'rms_iq': float(np.sqrt(np.mean(abs(x) ** 2))),
            'peak_offset_hz': float(f[index]),
            'peak_to_median_db': float(10 * np.log10((spectrum[index]+1e-30) /
                                                   (np.median(spectrum)+1e-30))),
            'all_zero': bool(np.all(x == 0))}


def context_for(serial):
    import iio as libiio
    found = libiio.scan_contexts()
    matches = [uri for uri, desc in found.items() if 'serial=' + serial.lower() in desc.lower()]
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one matching Pluto: ' + repr(found))
    ctx = libiio.Context(matches[0])
    ctx.set_timeout(3000)
    if ctx.attrs.get('hw_serial', '').lower() != serial.lower():
        raise RuntimeError('Pluto identity mismatch')
    return ctx, matches[0]


def snapshot(ctx):
    phy = ctx.find_device('ad9361-phy')
    ans = {'context': dict(ctx.attrs), 'channels': {}}
    for name, output in [('altvoltage0', True), ('altvoltage1', True),
                         ('voltage0', False), ('voltage0', True)]:
        ch = phy.find_channel(name, output)
        ans['channels'][name + ('_out' if output else '_in')] = {
            k: a.value for k, a in ch.attrs.items() if k in
            ('frequency', 'powerdown', 'hardwaregain', 'gain_control_mode',
             'sampling_frequency', 'rf_bandwidth', 'rf_port_select')}
    return ans


def configure(block, c, tx=False):
    block.set_len_tag_key('')
    block.set_frequency(int(c['center_hz']))
    block.set_samplerate(int(c['sample_rate']))
    if tx:
        block.set_bandwidth(int(c['bandwidth_hz']))
        block.set_attenuation(0, c['tx_attenuation_db'])
    else:
        block.set_gain_mode(0, 'manual')
        block.set_gain(0, c['rx_gain_db'])
        block.set_quadrature(True)
        block.set_rfdc(True)
        block.set_bbdc(True)
    block.set_filter_params('Auto', '', 0, 0)


def build_stream_receipt(started_utc, started_monotonic, end_requested_utc,
                         end_requested_monotonic, stopped_utc,
                         stopped_monotonic, requested_duration_s, sleep_seconds,
                         guard_seconds, stop_confirmed):
    """B1 receipt: stream_seconds covers start through confirmed tb.stop/wait."""
    request = {
        'stream_started_utc': started_utc.isoformat(),
        'stream_started_monotonic': started_monotonic,
        'stream_end_requested_utc': end_requested_utc.isoformat(),
        'stream_end_requested_monotonic': end_requested_monotonic,
        'requested_duration_s': float(requested_duration_s),
        'sleep_seconds': float(sleep_seconds),
        'guard_seconds': float(guard_seconds)}
    request.update({
        'stream_stopped_confirmed_utc':
            stopped_utc.isoformat() if stop_confirmed else None,
        'stream_stopped_confirmed_monotonic':
            stopped_monotonic if stop_confirmed else None,
        'stream_stopped_utc': stopped_utc.isoformat(),
        'stream_stopped_monotonic': stopped_monotonic,
        'stream_stop_confirmed': bool(stop_confirmed),
        'stream_seconds': ((stopped_monotonic - started_monotonic)
                           if stop_confirmed else None),
        'stream_requested_seconds': float(requested_duration_s)})
    return request


def run_graph(mode, c, uri, ready_file=None):
    """Keep GNU Radio's exclusive USB ownership within this function."""
    from gnuradio import analog, blocks, gr, iio
    tb = gr.top_block('External Pluto smoke test', catch_exceptions=True)
    sink = None
    source = None
    error = None
    iq = None
    stream = None
    try:
        if mode == BPSK_TX_MODE:
            payload, _meta = bpsk_tx_payload(c)
            source = blocks.vector_source_c(payload, True)
        elif mode in ('offline', 'tx', 'loopback'):
            source = analog.sig_source_c(c['sample_rate'], analog.GR_COS_WAVE,
                                         c['tone_hz'], c['amplitude'], 0)
        if mode in ('tx', 'loopback', BPSK_TX_MODE):
            tx = iio.fmcomms2_sink_fc32(uri, [True, True], 32768, False)
            configure(tx, c, True)
            tb.connect(source, tx)
        if mode in ('rx', 'loopback'):
            rx = iio.fmcomms2_source_fc32(uri, [True, True], 32768)
            configure(rx, c)
            source_rx = rx
        elif mode == 'offline':
            source_rx = source
        if mode in ('rx', 'loopback', 'offline'):
            head = blocks.head(gr.sizeof_gr_complex, c['capture_samples'])
            sink = blocks.vector_sink_c()
            tb.connect(source_rx, head, sink)
        tb.start()
        stream_started = datetime.now(timezone.utc)
        stream_started_monotonic = time.monotonic()
        if ready_file is not None:
            ready_file.write_text('STREAM_STARTED', encoding='utf-8')
        if mode == 'offline':
            tb.wait()
        else:
            sleep_s = c['duration_s']
            if mode == BPSK_TX_MODE:
                sleep_s = max(0.0, sleep_s - BPSK_STREAM_GUARD_S)
            time.sleep(sleep_s)
            if mode in ('tx', 'loopback', BPSK_TX_MODE):
                end_requested = datetime.now(timezone.utc)
                end_requested_monotonic = time.monotonic()
                request = {
                    'stream_started_utc': stream_started.isoformat(),
                    'stream_started_monotonic': stream_started_monotonic,
                    'stream_end_requested_utc': end_requested.isoformat(),
                    'stream_end_requested_monotonic': end_requested_monotonic,
                    'requested_duration_s': float(c['duration_s']),
                    'sleep_seconds': float(sleep_s),
                    'guard_seconds': (BPSK_STREAM_GUARD_S
                                      if mode == BPSK_TX_MODE else 0.0)}
                if ready_file is not None:
                    # Early marker: stream phase ended, graph NOT stopped yet.
                    requested_marker = Path(str(ready_file)
                                            + '.stream_end_requested')
                    requested_marker.write_text(json.dumps(request),
                                                encoding='utf-8')
                stop_confirmed = False
                try:
                    tb.stop()
                    tb.wait()
                    stop_confirmed = True
                except BaseException as e:
                    error = (error or '') + '; graph stop: ' + repr(e)
                stream_stopped = datetime.now(timezone.utc)
                stream_stopped_monotonic = time.monotonic()
                stream = build_stream_receipt(
                    stream_started, stream_started_monotonic,
                    end_requested, end_requested_monotonic,
                    stream_stopped, stream_stopped_monotonic,
                    c['duration_s'], sleep_s,
                    (BPSK_STREAM_GUARD_S
                     if mode == BPSK_TX_MODE else 0.0), stop_confirmed)
                if ready_file is not None and stop_confirmed:
                    # Written only after tb.stop()+tb.wait() returned.
                    stopped = Path(str(ready_file) + '.stopped')
                    stopped.write_text(json.dumps(stream), encoding='utf-8')
        if sink is not None:
            # rx: the head has filled; loopback has already been stopped above.
            if not (mode == 'loopback' and stream is not None):
                tb.stop()
                tb.wait()
            iq = np.asarray(sink.data(), dtype=np.complex64)
    except BaseException as e:
        # Return text instead of a traceback holding USB-owning blocks alive.
        error = repr(e)
    finally:
        try:
            tb.stop()
            tb.wait()
            tb.disconnect_all()
        except BaseException as e:
            error = (error or '') + '; graph cleanup: ' + repr(e)
    return iq, error, stream


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['probe', 'offline', 'rx', 'tx', 'loopback',
                                    BPSK_TX_MODE])
    p.add_argument('--config', type=Path, default=ROOT / 'config/pluto.json')
    p.add_argument('--path-confirmed', help='Operator description of actual RF wiring; required for TX')
    p.add_argument('--ready-file', type=Path, help='Optional streaming-start marker for scope orchestration')
    a = p.parse_args()
    c = json.loads(a.config.read_text(encoding='utf-8'))
    if a.mode == BPSK_TX_MODE:
        try:
            validate_bpsk_tx(c, a.path_confirmed)
        except ValueError as exc:
            p.error(str(exc))
    else:
        validate(c)
        if a.mode in ('tx', 'loopback') and not a.path_confirmed:
            p.error('TX requires --path-confirmed describing actual wiring')
    out = ROOT / 'results' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + a.mode)
    out.mkdir(parents=True)
    result = {'mode': a.mode, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'config': c, 'path_confirmation': a.path_confirmed, 'status': 'RUNNING'}
    if a.mode == BPSK_TX_MODE:
        _payload, bpsk_meta = bpsk_tx_payload(c)
        result['bpsk'] = bpsk_meta
    ctx = None
    tx_attempted = False
    lo_wall_started = None
    try:
        from gnuradio import analog, blocks, gr, iio
        result['gnuradio_version'] = gr.version()
        if a.mode == 'probe':
            ctx, uri = context_for(c['serial'])
            result['before'] = snapshot(ctx)
            result['status'] = 'DEVICE_IDENTIFIED'
            return
        uri = None
        if a.mode != 'offline':
            ctx, uri = context_for(c['serial'])
            result['before'] = snapshot(ctx)
            phy = ctx.find_device('ad9361-phy')
            if a.mode in ('rx', 'loopback'):
                phy.find_channel('voltage0', False).attrs['rf_bandwidth'].value = str(c['bandwidth_hz'])
            if a.mode in ('tx', 'loopback', BPSK_TX_MODE):
                tx_attempted = True
                lo_wall_started = datetime.now(timezone.utc)
                phy.find_channel('voltage0', True).attrs['hardwaregain'].value = '-89.75'
                phy.find_channel('altvoltage1', True).attrs['powerdown'].value = '0'
            del phy
            # libiio USB is exclusive. Release Python context before GR opens it.
            ctx = None
        iq, graph_error, stream = run_graph(a.mode, c, uri, a.ready_file)
        if graph_error:
            raise RuntimeError(graph_error)
        if stream is not None:
            result['stream'] = stream
            result['stream_seconds'] = stream.get('stream_seconds')
        if iq is not None:
            np.save(out / 'iq.npy', iq)
            result['iq'] = summarize(iq, c['sample_rate'])
            if a.mode == 'offline':
                ok = abs(result['iq']['peak_offset_hz'] - c['tone_hz']) < 2*c['sample_rate']/65536
                result['status'] = 'OFFLINE_PASS' if ok else 'FAIL'
            elif a.mode == 'rx':
                result['status'] = 'RX_STREAM_PASS' if not result['iq']['all_zero'] else 'FAIL'
            else:
                ok = (abs(result['iq']['peak_offset_hz']-c['tone_hz']) < 2000 and
                      result['iq']['peak_to_median_db'] > 20 and not result['iq']['all_zero'])
                result['status'] = 'TONE_OBSERVED_NOT_CALIBRATED' if ok else 'TONE_NOT_CONFIRMED'
        elif a.mode == BPSK_TX_MODE:
            secs = result.get('stream_seconds')
            if (not isinstance(secs, (int, float)) or secs != secs
                    or not 0 < secs <= BPSK_MAX_DURATION_S):
                result['status'] = 'BPSK_STREAM_OVER_BUDGET'
            else:
                result['status'] = 'TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED'
        else:
            result['status'] = 'TX_STREAM_COMPLETED_RF_UNVERIFIED'
    except BaseException as e:
        result['status'] = 'ERROR'
        result['error'] = repr(e)
        raise
    finally:
        try:
            if tx_attempted:
                try:
                    import gc
                    gc.collect()
                    ctx, _ = context_for(c['serial'])
                    phy = ctx.find_device('ad9361-phy')
                    phy.find_channel('voltage0', True).attrs['hardwaregain'].value = '-89.75'
                    lo = phy.find_channel('altvoltage1', True)
                    lo.attrs['powerdown'].value = '1'
                    result['tx_powerdown_readback'] = lo.attrs['powerdown'].value
                    if result['tx_powerdown_readback'] != '1':
                        result['status'] = 'CLEANUP_FAILED'
                    if lo_wall_started is not None:
                        result['tx_lo_on_wall'] = {
                            'started_utc': lo_wall_started.isoformat(),
                            'stopped_utc': datetime.now(timezone.utc).isoformat()}
                except Exception as e:
                    result['status'] = 'CLEANUP_FAILED'
                    result['cleanup_error'] = repr(e)
            elif a.mode == 'rx':
                ctx, _ = context_for(c['serial'])
            if ctx is not None:
                result['after'] = snapshot(ctx)
        finally:
            result['finished_utc'] = datetime.now(timezone.utc).isoformat()
            (out / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
            print(json.dumps({'result_dir': str(out), **result}, indent=2, ensure_ascii=False))
    if result['status'] in ('FAIL', 'TONE_NOT_CONFIRMED', 'CLEANUP_FAILED',
                            'BPSK_STREAM_OVER_BUDGET'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
