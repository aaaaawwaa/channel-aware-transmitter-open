"""Offline tests for the 80 MHz ladder package (no board/RF)."""
import json
from pathlib import Path
import sys
import types
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import ladder80_package as L  # noqa: E402
import capture_record  # noqa: E402


class FakeClient:
    def control_op(self, op, *args, **kwargs):
        return {'op': op, 'ok': True}

    def capture(self, *args, **kwargs):
        return {'capture_id': 'rx-fake', 'request_id': 1,
                'daemon_timestamp_ns': 2, 'release_id': 'rel',
                'connection_generation': 3, 'config_revision': 2,
                'samples': 4096, 'sample_rate_hz': 60000000,
                'ratio': 0, 'physical': True, 'payload': b'\x01\x02'}


class LadderPackageTests(unittest.TestCase):
    def test_compliance_exact_deltas_and_blocked_paths(self):
        c = L.attenuation_compliance()
        self.assertFalse(c['execution_allowed'])
        self.assertEqual(c['blocked_reason'], 'RF_DOMAIN_MISMATCH')
        rows = {r['attenuation_mdb']: r for r in c['rows']}
        self.assertEqual(rows[46000]['path'], 'allowed')
        self.assertEqual(rows[48000]['delta_vs_fixed_mdb'], 2000)
        self.assertEqual(rows[50000]['delta_vs_fixed_mdb'], 4000)
        self.assertIn('RF_DOMAIN_MISMATCH', rows[48000]['environment_domain'])
        self.assertIn('exactly 46000', rows[48000]['physical_capture_client'])
        self.assertEqual(c['upper_margin_mdb']['50000'], 39750)

    def test_sequence_is_balanced_and_deterministic(self):
        a = L.generate_sequence()
        b = L.generate_sequence()
        self.assertEqual(a, b)
        validation = L.validate_sequence(a)
        self.assertEqual((validation['on'], validation['off']), (9, 2))
        self.assertEqual(validation['counts'], {46000: 3, 48000: 3, 50000: 3})
        self.assertEqual(a['frames'][0]['tag'], 'off-pre')
        self.assertEqual(a['frames'][-1]['tag'], 'off-post')
        self.assertEqual(len({f['tag'] for f in a['frames']}), 11)

    def test_sequence_rejects_wrong_counts(self):
        s = L.generate_sequence()
        s['frames'] = [f for f in s['frames'] if f['tag'] != 'on-48000-r0']
        with self.assertRaises(L.SequenceInvalid):
            L.validate_sequence(s)

    def test_budget_counts(self):
        self.assertEqual(L.BUDGET['sessions'], 1)
        self.assertEqual(L.BUDGET['on'], 9)
        self.assertEqual(L.BUDGET['off'], 2)
        self.assertEqual(L.BUDGET['load'], 12)
        self.assertEqual(L.BUDGET['physical_captures'], 9)
        self.assertEqual(L.BUDGET['tx_starts'], 9)
        self.assertAlmostEqual(L.BUDGET['nominal_source_on_us'], 614.4)

    def test_off_policy_accepts_only_exact_missing_member(self):
        def reading():
            return {'available': False, 'reason': L.OFF_MISSING}
        sidecar = {'usable': False, 'stop': True,
                   'reasons': [L.OFF_MISSING],
                   'observation': {'identity_ok': True, 'identity_reason': None,
                                   'observation_present': False,
                                   'reasons': [L.OFF_MISSING],
                                   'in_window_rx_gain_index': reading(),
                                   'chip_tx_attenuation_mdb': reading()}}
        self.assertTrue(L.is_expected_off_absence(sidecar))
        self.assertEqual(L.off_action('x-off', False, sidecar)[0], 'expected_absent')
        bad = json.loads(json.dumps(sidecar))
        bad['observation']['identity_ok'] = False
        self.assertFalse(L.is_expected_off_absence(bad))
        bad2 = json.loads(json.dumps(sidecar))
        bad2['observation']['reasons'] = [L.OFF_MISSING, 'OTHER']
        self.assertFalse(L.is_expected_off_absence(bad2))
        self.assertEqual(L.off_action('x-off', True, sidecar)[0], 'reject')
        self.assertNotEqual(L.off_action('x-on', True, sidecar)[0], 'usable')

    def test_wrap_uses_worker_client_and_split_records(self):
        session = types.SimpleNamespace(
            worker=types.SimpleNamespace(client=FakeClient()))
        rec = capture_record.CaptureRecorder()
        L.wrap_80mhz_session(session, rec)
        self.assertIsInstance(session.worker.client, capture_record.RecordingClient)
        session.worker.client.control_op('rf.init', {'lo_hz': 80000000})
        session.worker.client.control_op('rf.configure', {'attenuation_mdB': 46000})
        session.worker.client.control_op('tx.load', {'samples': 4096}, b'\x01')
        session.worker.client.capture()
        rec.set_waveform(truth='normal',
                         parameters={'amplitude': 60, 'physical_max_code': 100,
                                     'seed': 1, 'period_samples': 4096,
                                     'quantization': 'signed12'},
                         expected_config={'attenuation_mdB': 46000},
                         actual_config={'attenuation_mdB': 46000},
                         consistent=True)
        rec.set_actual_config(rf_config={'lo_hz': 80000000},
                              tx_attenuation_mdb=46000, rx_attenuation_mdb=40000,
                              gain_index=76, chip_tx_attenuation_mdb=[46000, 46000],
                              sample_rate_hz=60000000, samples=4096, ratio=0,
                              release_id='rel', config_revision=2,
                              session='s', boot_id='b')
        split = L.split_records(rec.finalize(), {'session': 's', 'boot_id': 'b'})
        self.assertEqual(split['context']['source'], 'declared')
        self.assertEqual(split['readbacks']['source'], 'measured')
        self.assertEqual(split['readbacks']['gain']['kind'], 'single_in_window_read')
        self.assertEqual(split['readbacks']['gain']['agc_trajectory'], 'NOT_MEASURED')
        self.assertEqual(len(split['commands']), 3)

    def test_dry_run_is_blocked(self):
        out = L.dry_run()
        self.assertEqual(out['dry_run'], 'PASS_PLAN_VALIDATION')
        self.assertFalse(out['execution_allowed'])
        self.assertEqual(out['blocked_reason'], 'RF_DOMAIN_MISMATCH')
        self.assertEqual(out['sequence']['on'], 9)
        self.assertEqual(out['sequence']['off'], 2)

    def test_session_end_writes_three_separate_records(self):
        class FakeSession:
            def __init__(self):
                self.worker = types.SimpleNamespace(client=FakeClient())

            def setup(self, seed):
                self.worker.client.control_op('rf.init', {'lo_hz': 80000000})
                self.worker.client.control_op('rf.configure', {'attenuation_mdB': 46000})
                self.worker.client.control_op('tx.load', {'samples': 4096}, b'\x01')

            def scene(self, kind):
                pass

            def capture(self, tag, physical):
                self.worker.client.capture()

        declared = {
            'session': 's', 'boot_id': 'b', 'seed': 1,
            'waveform': {'truth': 'normal', 'amplitude': 60, 'physical_max_code': 100,
                         'seed': 1, 'period_samples': 4096,
                         'quantization': 'signed12', 'consistent': True},
            'actual_config': {
                'rf_config': {'lo_hz': 80000000},
                'tx_attenuation_mdb': 46000, 'rx_attenuation_mdb': 40000,
                'gain_index': 76, 'chip_tx_attenuation_mdb': [46000, 46000],
                'sample_rate_hz': 60000000, 'samples': 4096, 'ratio': 0,
                'release_id': 'rel', 'config_revision': 2,
            },
        }
        rec = capture_record.CaptureRecorder()
        with __import__('tempfile').TemporaryDirectory() as td:
            L.run_recorded_session(FakeSession(), L.generate_sequence(), rec, declared, td)
            for name in ('capture-record.json', 'declared-context.json',
                         'commands.json', 'readbacks.json'):
                self.assertTrue((Path(td) / name).is_file(), name)
            readbacks = json.loads((Path(td) / 'readbacks.json').read_text())
            context = json.loads((Path(td) / 'declared-context.json').read_text())
            self.assertEqual(context['source'], 'declared')
            self.assertEqual(readbacks['gain']['kind'], 'single_in_window_read')
            self.assertEqual(readbacks['gain']['agc_trajectory'], 'NOT_MEASURED')
            self.assertEqual(len(readbacks['captures']), 11)


if __name__ == '__main__':
    unittest.main()
