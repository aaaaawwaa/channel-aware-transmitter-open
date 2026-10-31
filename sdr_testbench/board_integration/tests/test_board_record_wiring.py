"""Offline fake-client test for the optional --record runner wiring.

No board, no RF: the fake client returns synthetic control/capture results.
"""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import board_integration  # noqa: E402
import capture_record  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []

    def control_op(self, op, *args, **kwargs):
        self.calls.append((op, args))
        if op == 'rf.configure':
            return {'attenuation_mdB': 46000, 'hardware_verified': True}
        return {'op': op, 'ok': True}

    def capture(self, *args, **kwargs):
        return {
            'capture_id': 'rx-fake-1', 'request_id': 7,
            'daemon_timestamp_ns': 123456789, 'release_id': 'release-test',
            'connection_generation': 3, 'config_revision': 2,
            'samples': 4096, 'sample_rate_hz': 60000000, 'ratio': 0,
            'physical': True, 'payload': b'\x01\x02\x03\x04',
        }

    def close(self):
        pass


def context():
    return {
        'session': 'fake-session', 'boot_id': 'boot-test', 'gain_index': 76,
        'rx_attenuation_mdb': 40000, 'release_id': 'release-test',
        'config_revision': 2,
        'waveform': {
            'truth': 'normal', 'amplitude': 60, 'physical_max_code': 100,
            'seed': 1, 'period_samples': 4096, 'quantization': 'signed12',
            'chip_tx_attenuation_mdb': [46000, 46000],
        },
    }


def cfg():
    return {'board': {'lo_hz': 433900000, 'sample_rate_hz': 60000000,
                      'attenuation_mdb': 46000, 'amplitude': 60,
                      'bram_words': 4096}}


class RunnerRecordWiringTests(unittest.TestCase):
    def test_record_off_returns_raw_client(self):
        fake = FakeClient()
        args = types.SimpleNamespace(board_port=5000, record=False)
        client, recorder = board_integration._new_board_client(args, lambda **kw: fake)
        self.assertIs(client, fake)
        self.assertIsNone(recorder)

    def test_record_on_wraps_and_writes_a_complete_record(self):
        fake = FakeClient()
        args = types.SimpleNamespace(board_port=5000, record=True)
        client, recorder = board_integration._new_board_client(args, lambda **kw: fake)
        self.assertIsInstance(client, capture_record.RecordingClient)
        client.control_op('rf.init', {'lo_hz': 433900000})
        client.control_op('rf.configure', {'attenuation_mdB': 46000})
        client.control_op('tx.load', {'samples': 4096}, b'\x01\x02\x03\x04')
        client.capture()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ctx = td / 'context.json'
            ctx.write_text(json.dumps(context()), encoding='utf-8')
            args.record_context = ctx
            args.mode = 'tone'
            path = board_integration._finish_capture_record(
                recorder, args, cfg(), td,
                {'pre_readback': {'attenuation_mdB': 46000,
                                  'hardware_verified': True}})
            record = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(record['schema'], capture_record.SCHEMA)
            self.assertEqual(len(record['controls']), 3)
            self.assertEqual(len(record['captures']), 1)
            self.assertEqual(record['actual_config']['gain_index'], 76)

    def test_missing_context_field_fails(self):
        fake = FakeClient()
        args = types.SimpleNamespace(board_port=5000, record=True)
        client, recorder = board_integration._new_board_client(args, lambda **kw: fake)
        client.control_op('rf.init', {})
        client.control_op('rf.configure', {})
        client.control_op('tx.load', {}, b'\x01')
        client.capture()
        bad = context(); bad.pop('boot_id')
        with tempfile.TemporaryDirectory() as td:
            td = Path(td); (td / 'c.json').write_text(json.dumps(bad), encoding='utf-8')
            args.record_context = td / 'c.json'
            args.mode = 'tone'
            with self.assertRaises(board_integration.ExecutionError):
                board_integration._finish_capture_record(
                    recorder, args, cfg(), td,
                    {'pre_readback': {'attenuation_mdB': 46000,
                                      'hardware_verified': True}})

    def test_cli_record_without_context_refuses_before_hardware(self):
        with tempfile.TemporaryDirectory() as td:
            token = Path(td) / 'token'
            token.write_text('fake-token\n', encoding='utf-8')
            rc = board_integration.main([
                '--execute', '--record', '--direction', 'B', '--mode', 'tone',
                '--path-confirmed', 'offline fake', '--token-file', str(token)])
            self.assertEqual(rc, 2)


if __name__ == '__main__':
    unittest.main()
