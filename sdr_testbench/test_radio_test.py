import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

import numpy as np
from radio_test import build_stream_receipt, summarize, validate


class TestSmoke(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).parent / 'config/pluto.json').read_text())

    def test_default(self):
        validate(self.config)

    def test_reject_bad_rf_settings(self):
        for key, value in [('center_hz', 80e6), ('duration_s', 600),
                           ('tx_attenuation_db', 0), ('amplitude', 1),
                           ('sample_rate', float('nan')), ('tone_hz', 2e6),
                           ('capture_samples', 0)]:
            c = copy.deepcopy(self.config)
            c[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(c)

    def test_tone_frequency_and_amplitude(self):
        fs = self.config['sample_rate']
        x = .25 * np.exp(2j*np.pi*100000*np.arange(65536)/fs)
        r = summarize(x, fs)
        self.assertLess(abs(r['peak_offset_hz'] - 100000), fs/65536)
        self.assertAlmostEqual(r['rms_iq'], .25, places=5)

    def test_zero_is_not_signal(self):
        self.assertTrue(summarize(np.zeros(4096), 2400000)['all_zero'])

    def test_reject_short_or_nan(self):
        for x in [np.ones(8), np.full(4096, np.nan)]:
            with self.assertRaises(ValueError):
                summarize(x, 2400000)


class StreamReceiptTests(unittest.TestCase):
    """B1: stream_seconds covers start through confirmed tb.stop()+tb.wait()."""

    def _receipt(self, stop_after_s, confirmed=True):
        start = datetime.now(timezone.utc)
        end_req = start + timedelta(seconds=1.95)
        stopped = start + timedelta(seconds=stop_after_s)
        return build_stream_receipt(
            start, 100.0, end_req, 101.95, stopped, 100.0 + stop_after_s,
            2.0, 1.85, 0.15, confirmed)

    def test_confirmed_window_2_3_seconds(self):
        receipt = self._receipt(2.3)
        self.assertIs(receipt['stream_stop_confirmed'], True)
        self.assertAlmostEqual(receipt['stream_seconds'], 2.3)
        self.assertAlmostEqual(receipt['stream_end_requested_monotonic'],
                               101.95)

    def test_confirmed_window_1_9_seconds(self):
        receipt = self._receipt(1.9)
        self.assertIs(receipt['stream_stop_confirmed'], True)
        self.assertAlmostEqual(receipt['stream_seconds'], 1.9)

    def test_unconfirmed_stop_has_no_seconds(self):
        receipt = self._receipt(2.3, confirmed=False)
        self.assertIs(receipt['stream_stop_confirmed'], False)
        self.assertIsNone(receipt['stream_seconds'])
        self.assertIsNone(receipt['stream_stopped_confirmed_utc'])


if __name__ == '__main__':
    unittest.main()
