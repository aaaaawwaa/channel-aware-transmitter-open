"""Strict, hardware-free capture-record completeness for the board runner.

The future physical runner wraps its board client with `RecordingClient`; the
wrapper records every control call and capture, while the runner supplies the
waveform identity and actual-configuration summary.  `finalize()` refuses an
incomplete record: a record missing an init/configure/load return, timing,
connection/config identity, waveform field, gain/attenuation readback, or
sample/release/session/boot identity is not evidence.

This module imports no serial/socket/GNU Radio/libiio/VISA code and never
touches a board.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

SCHEMA = 'board-capture-record-v1'
REQUIRED_CONTROL_OPS = ('rf.init', 'rf.configure', 'tx.load')
CONTROL_FIELDS = ('op', 'request', 'response', 'error', 'duration_s',
                  'connection_generation', 'config_revision')
WAVEFORM_FIELDS = ('truth', 'parameters', 'expected_config', 'actual_config',
                   'consistent')
WAVEFORM_PARAMETER_FIELDS = ('amplitude', 'physical_max_code', 'seed',
                             'period_samples', 'quantization')
ACTUAL_CONFIG_FIELDS = ('rf_config', 'tx_attenuation_mdb', 'rx_attenuation_mdb',
                        'gain_index', 'chip_tx_attenuation_mdb', 'sample_rate_hz',
                        'samples', 'ratio', 'release_id', 'config_revision',
                        'session', 'boot_id')
CAPTURE_FIELDS = ('capture_id', 'request_id', 'daemon_timestamp_ns',
                  'release_id', 'connection_generation', 'config_revision',
                  'samples', 'sample_rate_hz', 'ratio', 'physical', 'iq_sha256')


class RecordIncomplete(ValueError):
    """A record cannot be evidence because named required facts are absent."""

    def __init__(self, missing):
        self.missing = tuple(missing)
        super().__init__('capture record incomplete: ' + ', '.join(self.missing))


def _sha(data):
    if isinstance(data, bytes):
        return hashlib.sha256(data).hexdigest()
    return hashlib.sha256(str(data).encode('utf-8')).hexdigest()


def _is_int(value):
    return type(value) is int


def _is_number(value):
    return type(value) in (int, float) and value == value and abs(value) != float('inf')


def _missing(mapping, fields, prefix=''):
    return [prefix + f for f in fields if f not in mapping or mapping[f] is None]


class CaptureRecorder:
    """Collect one capture record and validate it on finalize()."""

    def __init__(self, expected_control_ops=REQUIRED_CONTROL_OPS,
                 require_capture=True):
        self.expected_control_ops = tuple(expected_control_ops)
        self.require_capture = bool(require_capture)
        self.controls = []
        self.captures = []
        self.waveform = None
        self.actual_config = None
        self.session = None
        self.boot_id = None

    def record_control(self, op, request=None, response=None, error=None,
                       duration_s=None, connection_generation=None,
                       config_revision=None, payload=None):
        request = {} if request is None else request
        if payload is not None:
            if not isinstance(payload, (bytes, bytearray)):
                raise TypeError('payload must be bytes')
            request = dict(request, payload_sha256=_sha(bytes(payload)),
                           payload_bytes=len(payload))
        entry = {
            'op': op,
            'request': request,
            'response': response,
            'error': None if error is None else str(error),
            'duration_s': None if duration_s is None else float(duration_s),
            'connection_generation': connection_generation,
            'config_revision': config_revision,
        }
        self.controls.append(entry)
        return entry

    def record_capture(self, frame):
        """Record one frame (object or mapping) and backfill pending identity."""
        def get(name):
            if isinstance(frame, dict):
                return frame.get(name)
            return getattr(frame, name, None)
        payload = get('payload')
        capture = {
            'capture_id': get('capture_id'),
            'request_id': get('request_id'),
            'daemon_timestamp_ns': get('daemon_timestamp_ns'),
            'release_id': get('release_id'),
            'connection_generation': get('connection_generation'),
            'config_revision': get('config_revision'),
            'samples': get('samples'),
            'sample_rate_hz': get('sample_rate_hz'),
            'ratio': get('ratio'),
            'physical': get('physical'),
            'iq_sha256': (_sha(payload) if isinstance(payload, (bytes, bytearray))
                          else get('iq_sha256')),
        }
        self.captures.append(capture)
        for control in self.controls:
            if control['connection_generation'] is None:
                control['connection_generation'] = capture['connection_generation']
            if control['config_revision'] is None:
                control['config_revision'] = capture['config_revision']
        return capture

    def set_waveform(self, truth, parameters, expected_config, actual_config,
                     consistent, notes=None):
        self.waveform = {
            'truth': truth,
            'parameters': dict(parameters),
            'expected_config': dict(expected_config),
            'actual_config': dict(actual_config),
            'consistent': bool(consistent),
            'notes': list(notes or []),
        }

    def set_actual_config(self, rf_config, tx_attenuation_mdb, rx_attenuation_mdb,
                          gain_index, chip_tx_attenuation_mdb, sample_rate_hz,
                          samples, ratio, release_id, config_revision, session,
                          boot_id):
        self.actual_config = {
            'rf_config': dict(rf_config),
            'tx_attenuation_mdb': tx_attenuation_mdb,
            'rx_attenuation_mdb': rx_attenuation_mdb,
            'gain_index': gain_index,
            'chip_tx_attenuation_mdb': list(chip_tx_attenuation_mdb)
            if chip_tx_attenuation_mdb is not None else None,
            'sample_rate_hz': sample_rate_hz,
            'samples': samples,
            'ratio': ratio,
            'release_id': release_id,
            'config_revision': config_revision,
            'session': session,
            'boot_id': boot_id,
        }
        self.session = session
        self.boot_id = boot_id

    def missing(self):
        missing = []
        for op in self.expected_control_ops:
            if not any(c['op'] == op for c in self.controls):
                missing.append('control.' + op)
        for i, c in enumerate(self.controls):
            for field in CONTROL_FIELDS:
                if field not in c or (field == 'request' and c[field] is None):
                    missing.append('control[%d].%s' % (i, field))
            if c.get('error') is None and c.get('response') is None:
                missing.append('control[%d].response_or_error' % i)
            if c.get('duration_s') is None or not _is_number(c['duration_s']):
                missing.append('control[%d].duration_s' % i)
            if not _is_int(c.get('connection_generation')):
                missing.append('control[%d].connection_generation' % i)
            if not _is_int(c.get('config_revision')):
                missing.append('control[%d].config_revision' % i)
        if not self.captures:
            if self.require_capture:
                missing.append('capture')
        else:
            for i, c in enumerate(self.captures):
                for field in CAPTURE_FIELDS:
                    if field not in c or c[field] is None:
                        missing.append('capture[%d].%s' % (i, field))
        if self.waveform is None:
            missing.append('waveform')
        else:
            for field in WAVEFORM_FIELDS:
                if field not in self.waveform or self.waveform[field] is None:
                    missing.append('waveform.' + field)
            for field in WAVEFORM_PARAMETER_FIELDS:
                if (not isinstance(self.waveform.get('parameters'), dict)
                        or field not in self.waveform['parameters']
                        or self.waveform['parameters'][field] is None):
                    missing.append('waveform.parameters.' + field)
            if self.waveform.get('consistent') is not True:
                missing.append('waveform.consistent')
        if self.actual_config is None:
            missing.append('actual_config')
        else:
            for field in ACTUAL_CONFIG_FIELDS:
                if field not in self.actual_config or self.actual_config[field] is None:
                    missing.append('actual_config.' + field)
        if not _is_int((self.actual_config or {}).get('config_revision')):
            missing.append('actual_config.config_revision')
        return missing

    def finalize(self):
        missing = self.missing()
        if missing:
            raise RecordIncomplete(missing)
        return {
            'schema': SCHEMA,
            'controls': self.controls,
            'captures': self.captures,
            'waveform': self.waveform,
            'actual_config': self.actual_config,
            'session': self.session,
            'boot_id': self.boot_id,
        }

    def write(self, path):
        record = self.finalize()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n',
                        encoding='utf-8')
        return path


class RecordingClient:
    """Transparent client wrapper; records control calls/captures, no new I/O."""

    def __init__(self, client, recorder=None):
        self._client = client
        self.recorder = recorder or CaptureRecorder()

    def __getattr__(self, name):
        return getattr(self._client, name)

    def control_op(self, op, *args, **kwargs):
        request = {}
        payload = None
        if args:
            if isinstance(args[0], dict):
                request.update(args[0])
            else:
                request['args'] = repr(args[0])
            if len(args) > 1 and isinstance(args[1], (bytes, bytearray)):
                payload = args[1]
                request['arg1_sha256'] = _sha(bytes(payload))
                request['arg1_bytes'] = len(payload)
        start = time.monotonic()
        try:
            response = self._client.control_op(op, *args, **kwargs)
        except Exception as exc:
            self.recorder.record_control(op, request, response=None, error=exc,
                                         duration_s=time.monotonic() - start,
                                         payload=payload)
            raise
        self.recorder.record_control(op, request, response=response,
                                     duration_s=time.monotonic() - start,
                                     payload=payload)
        return response

    def capture(self, *args, **kwargs):
        frame = self._client.capture(*args, **kwargs)
        self.recorder.record_capture(frame)
        return frame
