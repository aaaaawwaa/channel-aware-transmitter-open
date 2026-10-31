"""Strict readback parsing and the Pluto stop gate (offline only).

The real TX receipt is replayed byte for byte from
sdr_testbench/results/20261019-173154-376468-tx/result.json.  It is never
rewritten to fit the implementation: a gate that only passes on a fixture it
authored is not a gate.
"""
import json
from pathlib import Path
import sys
import types
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

import readback  # noqa: E402

REAL_TX_RECEIPT = (PROJECT / 'sdr_testbench' / 'results' /
                   '20261019-173154-376468-tx' / 'result.json')


def _real_receipt():
    return json.loads(REAL_TX_RECEIPT.read_text(encoding='utf-8'))


class ScalarParsingTests(unittest.TestCase):
    def test_accepts_real_libiio_db_text_and_bare_numbers(self):
        for text, expected in [('-89.750000 dB', -89.75),
                               ('-60.000000 dB', -60.0),
                               ('0.000000 dB', 0.0),
                               ('0 dB', 0.0),
                               ('  -12.5 dB  ', -12.5),
                               ('+3.5e1 dB', 35.0),
                               ('-.5 dB', -0.5),
                               ('12. dB', 12.0),
                               ('-89.75', -89.75),
                               (60, 60.0),
                               (-89.75, -89.75)]:
            with self.subTest(value=text):
                self.assertAlmostEqual(readback.parse_db_value(text), expected, places=9)

    def test_rejects_wrong_units_missing_values_and_non_finite_text(self):
        for value in [None, '', '   ', 'dB', ' dB', '-89.75 dBm', '-89.75 db',
                      '-89.75 DB', '-89.75 dB extra', 'nan', 'nan dB', 'NaN',
                      'inf', '-inf dB', 'Infinity', '1e999', '0x10', '1_0',
                      '-89,75 dB', '--89.75', '89.75-', True, False, [], {}]:
            with self.subTest(value=repr(value)):
                with self.assertRaises(readback.ReadbackError):
                    readback.parse_db_value(value)

    def test_require_db_rejects_a_wrong_value(self):
        self.assertAlmostEqual(
            readback.require_db('-89.750000 dB', -89.75, 'tx gain'), -89.75, places=9)
        with self.assertRaises(readback.ReadbackError) as ctx:
            readback.require_db('-60.000000 dB', -89.75, 'tx gain')
        self.assertIn('-89.75', str(ctx.exception))
        with self.assertRaises(readback.ReadbackError):
            readback.require_db('-89.7500001 dB', -89.75, 'tx gain',
                                tolerance_db=1e-9)

    def test_powerdown_is_exactly_zero_or_one(self):
        self.assertEqual(readback.parse_powerdown('1'), '1')
        self.assertEqual(readback.parse_powerdown(' 0 '), '0')
        for value in [None, '', 'yes', 'true', 1, 0, True, False, '01', '1.0']:
            with self.subTest(value=repr(value)):
                with self.assertRaises(readback.ReadbackError):
                    readback.parse_powerdown(value)


class PlutoPostGateTests(unittest.TestCase):
    def test_real_tx_receipt_is_accepted(self):
        result = readback.pluto_post_readback(_real_receipt())
        self.assertEqual(result['tx_powerdown'], '1')
        self.assertAlmostEqual(result['hardwaregain_db'], -89.75, places=9)

    def test_real_receipt_has_gain_on_the_voltage_channel_not_the_lo_channel(self):
        after = _real_receipt()['after']['channels']
        self.assertIn('hardwaregain', after['voltage0_out'])
        self.assertNotIn('hardwaregain', after['altvoltage1_out'])
        self.assertIn('powerdown', after['altvoltage1_out'])
        self.assertNotIn('powerdown', after['voltage0_out'])

    def test_gain_on_the_wrong_channel_is_refused(self):
        receipt = _real_receipt()
        channels = receipt['after']['channels']
        channels['altvoltage1_out']['hardwaregain'] = channels['voltage0_out'].pop('hardwaregain')
        with self.assertRaises(readback.ReadbackError) as ctx:
            readback.pluto_post_readback(receipt)
        self.assertIn('voltage0_out', str(ctx.exception))

    def test_powerdown_on_the_wrong_channel_is_refused(self):
        receipt = _real_receipt()
        channels = receipt['after']['channels']
        channels['voltage0_out']['powerdown'] = channels['altvoltage1_out'].pop('powerdown')
        with self.assertRaises(readback.ReadbackError) as ctx:
            readback.pluto_post_readback(receipt)
        self.assertIn('altvoltage1_out', str(ctx.exception))

    def test_missing_after_section_is_refused(self):
        receipt = _real_receipt()
        receipt.pop('after')
        with self.assertRaises(readback.ReadbackError):
            readback.pluto_post_readback(receipt)

    def test_non_finite_and_wrong_gain_are_refused(self):
        for bad in ['nan dB', 'inf dB', '-89.75 dBm', '-10.000000 dB', '']:
            receipt = _real_receipt()
            receipt['after']['channels']['voltage0_out']['hardwaregain'] = bad
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(readback.ReadbackError):
                    readback.pluto_post_readback(receipt)

    def test_powerdown_not_one_is_refused(self):
        receipt = _real_receipt()
        receipt['after']['channels']['altvoltage1_out']['powerdown'] = '0'
        with self.assertRaises(readback.ReadbackError):
            readback.pluto_post_readback(receipt)


def _snapshot(online=True, stale=False, io_ok=True, tx=False, rx=False, error=None):
    return types.SimpleNamespace(
        online=online, stale=stale, error=error, release_id='offline-test-release',
        pl_status=types.SimpleNamespace(
            io_ok=io_ok,
            tx=types.SimpleNamespace(source_selected=tx),
            rx=types.SimpleNamespace(armed=rx)))


class BoardIdleGateTests(unittest.TestCase):
    def test_idle_board_passes_and_reports_every_field(self):
        ok, problems, detail = readback.board_idle_gate(_snapshot())
        self.assertTrue(ok, problems)
        self.assertEqual(problems, [])
        self.assertEqual(detail['io_ok'], True)
        self.assertIs(detail['tx_source_selected'], False)
        self.assertIs(detail['rx_armed'], False)

    def test_every_single_condition_is_enforced(self):
        cases = [dict(tx=True), dict(rx=True), dict(io_ok=False),
                 dict(online=False), dict(stale=True), dict(online=False, stale=True)]
        for overrides in cases:
            with self.subTest(**overrides):
                ok, problems, _ = readback.board_idle_gate(_snapshot(**overrides))
                self.assertFalse(ok)
                self.assertTrue(problems)

    def test_unknown_tx_or_rx_state_is_refused(self):
        ok, problems, _ = readback.board_idle_gate(_snapshot(tx=None, rx=None))
        self.assertFalse(ok)
        self.assertEqual(len(problems), 2, problems)


if __name__ == '__main__':
    unittest.main()
