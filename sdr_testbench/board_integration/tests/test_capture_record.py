"""Offline completeness tests for the board capture record (no hardware)."""
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import capture_record  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []

    def control_op(self, op, *args, **kwargs):
        self.calls.append((op, args))
        return {'op': op, 'ok': True}

    def capture(self, *args, **kwargs):
        return {
            'capture_id': 'rx-test-1',
            'request_id': 7,
            'daemon_timestamp_ns': 123456789,
            'release_id': 'release-test',
            'connection_generation': 3,
            'config_revision': 2,
            'samples': 4096,
            'sample_rate_hz': 60000000,
            'ratio': 0,
            'physical': True,
            'payload': b'\x01\x02\x03\x04',
        }


def complete_recorder():
    r = capture_record.CaptureRecorder()
    r.record_control('rf.init', {'lo_hz': 433900000}, {'ok': True}, duration_s=0.012)
    r.record_control('rf.configure', {'attenuation_mdB': 46000}, {'ok': True,
                                                                  'attenuation_mdB': 46000},
                     duration_s=0.013)
    r.record_control('tx.load', {'samples': 4096}, {'ok': True},
                     duration_s=0.014, payload=b'\x01\x02\x03\x04')
    r.record_capture(FakeClient().capture())
    r.set_waveform(
        truth='normal',
        parameters={'amplitude': 60, 'physical_max_code': 100, 'seed': 1,
                    'period_samples': 2540, 'quantization': 'signed12'},
        expected_config={'attenuation_mdB': 46000, 'amplitude': 60},
        actual_config={'attenuation_mdB': 46000, 'amplitude': 60},
        consistent=True)
    r.set_actual_config(
        rf_config={'lo_hz': 433900000, 'sample_rate_hz': 60000000},
        tx_attenuation_mdb=46000, rx_attenuation_mdb=40000, gain_index=76,
        chip_tx_attenuation_mdb=[46000, 46000], sample_rate_hz=60000000,
        samples=4096, ratio=0, release_id='release-test', config_revision=2,
        session='test-session', boot_id='boot-test')
    return r


class RecordCompletenessTests(unittest.TestCase):
    def test_complete_record_is_accepted_and_serializable(self):
        record = complete_recorder().finalize()
        self.assertEqual(record['schema'], capture_record.SCHEMA)
        self.assertEqual(len(record['controls']), 3)
        self.assertEqual(record['captures'][0]['iq_sha256'],
                         capture_record._sha(b'\x01\x02\x03\x04'))
        json.dumps(record)

    def test_each_required_control_op_missing_fails(self):
        for op in capture_record.REQUIRED_CONTROL_OPS:
            r = complete_recorder()
            r.controls = [c for c in r.controls if c['op'] != op]
            with self.subTest(op=op):
                with self.assertRaises(capture_record.RecordIncomplete) as ctx:
                    r.finalize()
                self.assertIn('control.' + op, str(ctx.exception))

    def test_control_timing_and_generation_are_required(self):
        r = complete_recorder()
        r.controls[0]['duration_s'] = None
        r.controls[1]['connection_generation'] = None
        with self.assertRaises(capture_record.RecordIncomplete) as ctx:
            r.finalize()
        text = str(ctx.exception)
        self.assertIn('control[0].duration_s', text)
        self.assertIn('control[1].connection_generation', text)

    def test_control_response_or_named_error_is_required(self):
        r = complete_recorder()
        r.controls[1]['response'] = None
        r.controls[1]['error'] = None
        with self.assertRaises(capture_record.RecordIncomplete) as ctx:
            r.finalize()
        self.assertIn('control[1].response_or_error', str(ctx.exception))

    def test_waveform_parameter_and_consistency_are_required(self):
        r = complete_recorder()
        del r.waveform['parameters']['seed']
        r.waveform['consistent'] = False
        with self.assertRaises(capture_record.RecordIncomplete) as ctx:
            r.finalize()
        text = str(ctx.exception)
        self.assertIn('waveform.parameters.seed', text)
        self.assertIn('waveform.consistent', text)

    def test_actual_config_gain_attenuation_identity_are_required(self):
        r = complete_recorder()
        r.actual_config['gain_index'] = None
        r.actual_config['rx_attenuation_mdb'] = None
        r.actual_config['chip_tx_attenuation_mdb'] = None
        r.actual_config['boot_id'] = None
        with self.assertRaises(capture_record.RecordIncomplete) as ctx:
            r.finalize()
        text = str(ctx.exception)
        self.assertIn('actual_config.gain_index', text)
        self.assertIn('actual_config.rx_attenuation_mdb', text)
        self.assertIn('actual_config.chip_tx_attenuation_mdb', text)
        self.assertIn('actual_config.boot_id', text)

    def test_capture_identity_is_required(self):
        r = complete_recorder()
        r.captures[0]['daemon_timestamp_ns'] = None
        with self.assertRaises(capture_record.RecordIncomplete) as ctx:
            r.finalize()
        self.assertIn('capture[0].daemon_timestamp_ns', str(ctx.exception))

    def test_optional_capture_is_allowed_only_when_requested(self):
        r = complete_recorder()
        r.require_capture = False
        r.captures = []
        record = r.finalize()
        self.assertEqual(record['captures'], [])
        strict = complete_recorder()
        strict.captures = []
        with self.assertRaises(capture_record.RecordIncomplete) as ctx:
            strict.finalize()
        self.assertIn('capture', str(ctx.exception))

    def test_recording_client_records_calls_and_backfills_identity(self):
        fake = FakeClient()
        recorder = capture_record.CaptureRecorder()
        client = capture_record.RecordingClient(fake, recorder)
        client.control_op('rf.init', {'lo_hz': 433900000})
        client.control_op('rf.configure', {'attenuation_mdB': 46000})
        client.control_op('tx.load', {'samples': 4096}, b'\x01\x02\x03\x04')
        client.capture()
        self.assertEqual(recorder.controls[0]['connection_generation'], 3)
        self.assertEqual(recorder.controls[0]['config_revision'], 2)
        self.assertEqual(recorder.controls[2]['request']['payload_sha256'],
                         capture_record._sha(b'\x01\x02\x03\x04'))
        self.assertNotIn('\x01\x02\x03\x04', json.dumps(recorder.controls))


if __name__ == '__main__':
    unittest.main()
