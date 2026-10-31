"""Bounded GNU Radio tests for a serial-selected Pluto. Never opens serial ports."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parent


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


def run_graph(mode, c, uri, ready_file=None):
    """Keep GNU Radio's exclusive USB ownership within this function."""
    from gnuradio import analog, blocks, gr, iio
    tb = gr.top_block('External Pluto smoke test', catch_exceptions=True)
    sink = None
    source = None
    error = None
    iq = None
    try:
        if mode in ('offline', 'tx', 'loopback'):
            source = analog.sig_source_c(c['sample_rate'], analog.GR_COS_WAVE,
                                         c['tone_hz'], c['amplitude'], 0)
        if mode in ('tx', 'loopback'):
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
        if ready_file is not None:
            ready_file.write_text('STREAM_STARTED', encoding='utf-8')
        if mode == 'offline':
            tb.wait()
        else:
            time.sleep(c['duration_s'])
        if sink is not None:
            # RX head has already filled; stop before reading the vector.
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
    return iq, error


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['probe', 'offline', 'rx', 'tx', 'loopback'])
    p.add_argument('--config', type=Path, default=ROOT / 'config/pluto.json')
    p.add_argument('--path-confirmed', help='Operator description of actual RF wiring; required for TX')
    p.add_argument('--ready-file', type=Path, help='Optional streaming-start marker for scope orchestration')
    a = p.parse_args()
    c = json.loads(a.config.read_text(encoding='utf-8'))
    validate(c)
    if a.mode in ('tx', 'loopback') and not a.path_confirmed:
        p.error('TX requires --path-confirmed describing actual wiring')
    out = ROOT / 'results' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + a.mode)
    out.mkdir(parents=True)
    result = {'mode': a.mode, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'config': c, 'path_confirmation': a.path_confirmed, 'status': 'RUNNING'}
    ctx = None
    tx_attempted = False
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
            if a.mode in ('tx', 'loopback'):
                tx_attempted = True
                phy.find_channel('voltage0', True).attrs['hardwaregain'].value = '-89.75'
                phy.find_channel('altvoltage1', True).attrs['powerdown'].value = '0'
            del phy
            # libiio USB is exclusive. Release Python context before GR opens it.
            ctx = None
        iq, graph_error = run_graph(a.mode, c, uri, a.ready_file)
        if graph_error:
            raise RuntimeError(graph_error)
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
    if result['status'] in ('FAIL', 'TONE_NOT_CONFIRMED', 'CLEANUP_FAILED'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
