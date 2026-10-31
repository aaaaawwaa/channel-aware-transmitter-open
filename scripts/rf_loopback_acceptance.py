"""Operator cabled-loopback acceptance using the sanctioned board helper."""
import argparse
import importlib.util
import json
import time
import uuid
from pathlib import Path
import numpy as np


def analyze(data):
    rows = []
    criteria = data['criteria']
    if not data['acquisition_complete']:
        return dict(passed=False, error=data['error'], rows=rows, criteria=criteria)
    spectra = {}
    clipping = 0
    for record in data['records']:
        header = record['header']
        assert header['sample_rate_hz'] == 60000000 and header['ratio'] == 0
        iq = np.asarray(record['iq'])
        clipping += int(np.count_nonzero((iq <= -2048) | (iq >= 2047)))
        samples = iq[:, 0] + 1j * iq[:, 1]
        window = np.hanning(len(samples))
        freq = np.fft.fftfreq(len(samples), 1/header['sample_rate_hz'])
        amp = np.abs(np.fft.fft((samples-samples.mean())*window))/window.sum()
        spectra[record['tag']] = amp
    off = np.maximum(spectra['off-before'], spectra['off-after'])
    for tag in ('on-1', 'on-2', 'on-3'):
        amp = spectra[tag]
        floor = np.median(amp[(abs(freq)>0.2e6) & (abs(freq)<5e6) & (abs(abs(freq)-1.875e6)>0.2e6)])
        for target in (-1.875e6, 1.875e6):
            region = np.where(abs(freq-target)<=criteria['frequency_tolerance_hz'])[0]
            k = region[np.argmax(amp[region])]
            prominence = float(20*np.log10(max(amp[k],1e-12)/max(floor,1e-12)))
            growth = float(20*np.log10(max(amp[k],1e-12)/max(off[region].max(),1e-12)))
            rows.append(dict(tag=tag, target_hz=target, peak_hz=float(freq[k]),
                prominence_db=prominence, on_off_db=growth,
                passed=bool(prominence>=criteria['local_floor_db'] and growth>=criteria['on_off_db'])))
    return dict(passed=all(r['passed'] for r in rows) and clipping==0,
                clipping=clipping, rows=rows, criteria=criteria)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-release', required=True)
    parser.add_argument('--tap', required=True, type=int, choices=range(32))
    parser.add_argument('--amplitude', type=int, default=1500, choices=range(1,2048))
    parser.add_argument('--cable-confirmed', required=True, action='store_true')
    parser.add_argument('--output', type=Path, default=Path('evidence/manual-loopback-'+time.strftime('%Y%m%d-%H%M%S')))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    helper_path = Path.home()/'.claude/skills/ac820-pynq/scripts/pynq_ssh.py'
    spec = importlib.util.spec_from_file_location('pynq_ssh', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    client = helper.connect()
    remote = '/tmp/rf-loopback-'+uuid.uuid4().hex
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        with client.open_sftp() as sftp:
            sftp.put(str(root/'rf_overlay/sw/physical_loopback_acceptance.py'), remote+'.py')
        command = 'sudo -S python3 -u -B %s.py %s %d %s.json %d' % (remote, args.expected_release, args.tap, remote, args.amplitude)
        # release IDs are CLI input, so refuse shell metacharacters before execution.
        import re
        if not re.fullmatch(r'[A-Za-z0-9._-]+', args.expected_release):
            raise ValueError('invalid release ID')
        stdin, stdout, stderr = client.exec_command(command, timeout=120)
        stdin.write(helper.PASS+'\n'); stdin.flush()
        text = stdout.read().decode('utf-8','replace')
        errors = stderr.read().decode('utf-8','replace')
        rc = stdout.channel.recv_exit_status()
        (args.output/'run.log').write_text(text+errors, encoding='utf-8')
        with client.open_sftp() as sftp:
            sftp.get(remote+'.json', str(args.output/'raw-iq.json'))
        data = json.loads((args.output/'raw-iq.json').read_text(encoding='utf-8'))
        result = analyze(data)
        result['remote_exit_code'] = rc
        (args.output/'analysis.json').write_text(json.dumps(result,indent=2), encoding='utf-8')
        print(json.dumps(result,indent=2))
        return 0 if result['passed'] and rc==0 else 1
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
