"""Run with system Python (PyVISA/Keysight VISA); GR runs in Radioconda child."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
RESOURCE = '<VISA_RESOURCE>'


def main():
    import pyvisa
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--transmit', action='store_true')
    p.add_argument('--path-confirmed')
    a = p.parse_args()
    if a.transmit and not a.path_confirmed:
        p.error('--transmit requires --path-confirmed')
    out = ROOT / 'results' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-scope')
    out.mkdir(parents=True)
    result = {'resource': RESOURCE, 'tx_requested': a.transmit,
              'rf_verdict': 'NOT_CONFIRMED_SCOPE_BANDWIDTH_INSUFFICIENT'}
    manager = pyvisa.ResourceManager()
    scope = manager.open_resource(RESOURCE)
    scope.timeout = 3000
    proc = None
    log = None
    try:
        result['idn'] = scope.query('*IDN?').strip()
        if 'DSO-X 1102A,<DEVICE_SERIAL>' not in result['idn']:
            raise RuntimeError('Scope identity mismatch')
        result['options'] = scope.query('*OPT?').strip()
        result['initial_settings'] = {q: scope.query(q).strip() for q in
                                      (':CHAN1:DISP?', ':CHAN1:PROB?', ':CHAN1:SCAL?', ':TIM:SCAL?')}
        def measure():
            return {q: scope.query(q).strip() for q in
                    (':MEAS:FREQ? CHAN1', ':MEAS:VPP? CHAN1', ':MEAS:VRMS? CHAN1')}
        result['before'] = measure()
        if a.transmit:
            env = dict(os.environ)
            env['PATH'] = '<LOCAL_PATH>;<LOCAL_PATH>;' + env['PATH']
            marker = out / 'stream-started.txt'
            log = (out / 'radio-output.txt').open('w', encoding='utf-8')
            proc = subprocess.Popen(['<LOCAL_PATH>', '-X', 'utf8', '-B',
                                     str(ROOT / 'radio_test.py'), 'tx', '--path-confirmed',
                                     a.path_confirmed, '--ready-file', str(marker)],
                                    stdout=log, stderr=subprocess.STDOUT, env=env)
            limit = time.monotonic() + 12
            while not marker.exists() and proc.poll() is None and time.monotonic() < limit:
                time.sleep(.05)
            if not marker.exists() or proc.poll() is not None:
                raise RuntimeError('TX did not reach active stream in time; inspect radio-output.txt')
            result['during'] = measure()
            result['tx_exit_code'] = proc.wait(timeout=15)
            if proc.returncode:
                raise RuntimeError('TX or cleanup failed; inspect radio-output.txt')
        result['after'] = measure()
        try:
            data = scope.query_binary_values(':DISP:DATA? PNG,COLor', datatype='B', container=bytes)
            if not data.startswith(b'\x89PNG'):
                raise RuntimeError('Unexpected screenshot format')
            (out / 'scope.png').write_bytes(data)
            result['screenshot'] = 'scope.png'
        except Exception as e:
            result['screenshot_error'] = repr(e)
        result['scpi_error'] = scope.query(':SYST:ERR?').strip()
        result['status'] = 'COMPLETED'
    except BaseException as e:
        result['status'] = 'ERROR'
        result['error'] = repr(e)
        raise
    finally:
        if proc is not None and proc.poll() is None:
            # Let the bounded child finish its own cleanup even if SCPI fails.
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
                result['cleanup_required'] = 'Child hung; unplug Pluto power/USB and inspect before more RF tests'
        if log is not None:
            log.close()
        scope.close()
        manager.close()
        (out / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'result_dir': str(out), **result}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
