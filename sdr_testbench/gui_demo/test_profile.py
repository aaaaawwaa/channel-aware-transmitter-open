"""Offline contract tests: real board PRBS compatibility and explicit TX gating."""
import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import profile_contract as demo


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.config = demo.load_profile()

    def test_preview_defaults_and_no_transmit(self):
        self.assertEqual(self.config['center_hz'], 433900000)
        self.assertEqual(self.config['sample_rate'], 6000000)
        self.assertEqual(self.config['symbol_rate_hz'], 3000000)
        self.assertEqual(self.config['tx_attenuation_db'], 60.0)
        self.assertEqual(self.config['duration_s'], 2.0)
        with self.assertRaisesRegex(ValueError, '接线'):
            demo.tx_command(self.config, '', False)
        with self.assertRaisesRegex(ValueError, '板卡TX'):
            demo.tx_command(self.config, 'Pluto TX -> board RX1', False)

    def test_command_uses_existing_bounded_worker(self):
        cmd = demo.tx_command(self.config, 'Pluto TX -> board RX1', True)
        self.assertEqual(cmd[0], sys.executable)
        self.assertIn('tx-bpsk', cmd)
        self.assertIn('--path-confirmed', cmd)
        self.assertTrue(Path(cmd[3]).name == 'tx_worker.py')

    def test_prbs_and_quantization_match_existing_sender(self):
        parent = HERE.parent
        spec = importlib.util.spec_from_file_location('existing_radio', parent / 'radio_test.py')
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        actual = demo.preview_samples(self.config)
        expected, _ = old.bpsk_tx_payload(self.config)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 4064)

    def test_serial_frequency_and_sampling_cannot_silently_drift(self):
        for key, value in [('serial', ''), ('center_hz', 80000000),
                           ('sample_rate', 2400000), ('symbol_rate_hz', 1000000),
                           ('tx_attenuation_db', 0), ('duration_s', 20),
                           ('amplitude', float('nan')), ('prbs7_seed', 0)]:
            c = dict(self.config, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                demo.validate_profile(c)

    def test_receipt_does_not_claim_rf_verified(self):
        r = dict(status='TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED',
                 tx_powerdown_readback='1', stream_seconds=1.86,
                 stream={'stream_stop_confirmed': True})
        self.assertEqual(demo.receipt_state(r), 'TX_FINISHED_RF_UNVERIFIED')
        r['tx_powerdown_readback'] = '0'
        self.assertEqual(demo.receipt_state(r), 'CLEANUP_UNVERIFIED')
        self.assertEqual(demo.receipt_state({}), 'CLEANUP_UNVERIFIED')

    def test_cooperative_stop_raises_without_terminating_a_process(self):
        from tx_worker import InterruptibleTime
        class StopRequested:
            def exists(self):
                return True
        with self.assertRaises(KeyboardInterrupt):
            InterruptibleTime(StopRequested()).sleep(0.2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
