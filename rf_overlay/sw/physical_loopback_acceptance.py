"""Cabled analog capture through the sole RF daemon; no Overlay/board_app."""
import json
import socket
import struct
import time
import sys
from pathlib import Path
import numpy as np

expected_release, tap = sys.argv[1], int(sys.argv[2])
amplitude = int(sys.argv[4]) if len(sys.argv)>4 else 1500
assert 1 <= amplitude <= 2047
sock = socket.create_connection(('127.0.0.1', 5000), timeout=20)
sock.settimeout(20)
stream = sock.makefile('rb')
serial = 0
events, records = [], []


def request(op, args=None, payload=b''):
    global serial
    serial += 1
    sock.sendall(json.dumps({'id': serial, 'op': op, 'args': args or {}}).encode() + b'\n' + payload)
    header = json.loads(stream.readline())
    assert header['id'] == serial
    if header.get('type') == 'iq_frame':
        data = stream.read(header['bytes'])
        assert len(data) == header['bytes'] == header['samples'] * 4
        assert header['format'] == 's16_iq_le'
        return header, list(struct.iter_unpack('<hh', data))
    assert header.get('ok'), header
    events.append({'op': op, 'response': header, 'monotonic': time.monotonic()})
    return header['result']


def capture(tag, op):
    args = dict(samples=4096, ratio=0)
    if op == 'loopback.physical.capture':
        args.update(attenuation_mdB=40000, cable_confirmed=True)
    header, iq = request(op, args)
    records.append(dict(tag=tag, header=header, iq=iq))
    pl = request('pl.status.get')
    assert not pl['tx']['source_selected'], pl


passed = False
error = None
try:
    request('protocol.hello', {'major': 1, 'minor': 2})
    info = request('device.info')
    assert info['release_id'] == expected_release
    request('control.arm', {'token': Path('/etc/rf-control.token').read_text().strip()})
    params = dict(lo_hz=80000000, sample_rate_hz=60000000, attenuation_mdB=40000, phy_mode_1r1t=0)
    request('rf.init', params)
    readback = request('rf.configure', dict(params, rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000, idelay_en=1, idelay_tap=tap))
    tx = b''.join(struct.pack('<hh', int(round(float(amplitude*np.sin(2*np.pi*32*n/1024)))), 0) for n in range(1024))
    request('tx.load', dict(samples=1024, bytes=len(tx), format='s16_iq_le', direction='tx', wrap=True), tx)
    capture('off-before', 'rx.capture')
    for i in range(3):
        capture('on-%d' % (i+1), 'loopback.physical.capture')
    capture('off-after', 'rx.capture')
    passed = True  # Acquisition completeness only; host analysis decides signal pass.
except Exception as exc:
    error = str(exc)
    raise
finally:
    try:
        request('control.disarm')
        final = request('pl.status.get')
        assert not final['tx']['source_selected'] and not final['rx']['armed'], final
    finally:
        Path(sys.argv[3] if len(sys.argv)>3 else '/tmp/physical-loopback-result.json').write_text(json.dumps(
            dict(acquisition_complete=passed, error=error, tap=tap, tx_amplitude=amplitude, events=events,
                 records=records, criteria=dict(frequency_tolerance_hz=30000,
                    local_floor_db=20, on_off_db=10, frames=3, clipping=0)), indent=2))
        stream.close()
        sock.close()
