"""Offline tests for the minimal Stage 0 (Pluto TX -> Pluto RX) entry point.

Nothing here opens USB, libiio, GNU Radio, a serial port or a socket.  The
executor is exercised only through its pure planner/analysis half and the
CLI's --dry-run/--self-test paths.
"""
import contextlib
import copy
import gc
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
import weakref

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT / 'sdr_testbench'))

import planner  # noqa: E402
import readback  # noqa: E402
import radio_test  # noqa: E402
import stage0  # noqa: E402

CONFIG = ROOT / 'config' / 'stage0_selfloop.json'
REAL_TX_RECEIPT = (PROJECT / 'sdr_testbench' / 'results' /
                   '20261019-173154-376468-tx' / 'result.json')
FS = 2400000.0
TARGET_HZ = 100000.0


def _cfg():
    return planner.load_config(CONFIG)


def _tone_window(amplitude=0.5, noise=0.01, samples=65536, offset_hz=TARGET_HZ):
    rng = np.random.default_rng(20261019)
    t = np.arange(samples, dtype=float) / FS
    z = amplitude * np.exp(2j * np.pi * offset_hz * t)
    z = z + noise * (rng.standard_normal(samples) + 1j * rng.standard_normal(samples))
    return z.astype(np.complex64)


def _noise_window(noise=0.01, samples=65536):
    rng = np.random.default_rng(7)
    return (noise * (rng.standard_normal(samples) +
                     1j * rng.standard_normal(samples))).astype(np.complex64)


class Stage0ConfigTests(unittest.TestCase):
    def test_default_stage0_config_validates(self):
        planner.validate_stage0_config(_cfg())

    def test_parameter_changes_are_refused(self):
        cases = [(('amplitude',), 0.25), (('tx_attenuation_db',), 40.0),
                 (('center_hz',), 80000000), (('sample_rate',), 6000000),
                 (('tone_hz',), 200000), (('rx_gain_db',), 10.0),
                 (('gain_mode',), 'slow_attack'), (('duration_s',), 3.0),
                 (('tx_attenuation_ceiling_db',), 60.0)]
        for keys, value in cases:
            cfg = _cfg()
            cfg[keys[0]] = value
            with self.subTest(key=keys[0], value=value):
                with self.assertRaises(planner.PlanError):
                    planner.validate_stage0_config(cfg)
        cfg = _cfg()
        del cfg['gain_mode']
        with self.assertRaises(planner.PlanError):
            planner.validate_stage0_config(cfg)


class Stage0PlanTests(unittest.TestCase):
    def test_plan_is_pluto_only_and_never_touches_the_board(self):
        plan = planner.stage0_plan(_cfg())
        self.assertTrue(plan['pluto_only'])
        self.assertEqual(plan['board_actions'], 0)
        self.assertFalse(plan['com11'])
        self.assertFalse(plan['token_required'])
        self.assertFalse(plan['nonce_generated'])
        self.assertFalse(plan['hardware_accessed'])

    def test_three_windows_one_tx_start_and_a_two_second_budget(self):
        plan = planner.stage0_plan(_cfg())
        self.assertEqual([w['name'] for w in plan['windows']],
                         ['OFF_PRE', 'TONE', 'OFF_POST'])
        off = [w for w in plan['windows'] if w['name'] != 'TONE']
        tone = [w for w in plan['windows'] if w['name'] == 'TONE'][0]
        self.assertTrue(tone['tx_on'])
        self.assertTrue(all(w['rx_only'] and not w['tx_on'] for w in off))
        self.assertEqual(plan['budget']['tx_starts'], 1)
        self.assertEqual(plan['budget']['tx_seconds'], 2.0)
        self.assertLessEqual(plan['budget']['tx_seconds'], plan['budget']['tx_budget_s'])
        self.assertTrue(plan['budget']['tx_budget_ok'])

    def test_pre_tx_readback_is_taken_with_the_lo_off(self):
        plan = planner.stage0_plan(_cfg())
        pre = plan['gates']['pre_tx_readback']
        self.assertEqual(pre['lo_powerdown'], '1')
        self.assertEqual(pre['attenuation_db'], 60.0)
        self.assertTrue(pre['before_lo_enable'])

    def test_post_stop_readback_is_the_ceiling_and_powerdown(self):
        plan = planner.stage0_plan(_cfg())
        post = plan['gates']['post_stop_readback']
        self.assertEqual(post['attenuation_db'], 89.75)
        self.assertEqual(post['lo_powerdown'], '1')

    def test_tx_digital_evidence_is_calculated(self):
        plan = planner.stage0_plan(_cfg())
        tx = plan['tx_digital_evidence']
        self.assertEqual(tx['amplitude'], 0.1)
        self.assertEqual(tx['peak_code'], 205)
        self.assertAlmostEqual(tx['backoff_db'], 20.0 * np.log10(0.1), places=6)
        self.assertFalse(tx['clipping_possible'])


class Stage0AnalysisTests(unittest.TestCase):
    def test_tone_window_peaks_in_band_without_full_scale(self):
        a = planner.analyse_stage0_window(_tone_window(), FS, TARGET_HZ)
        self.assertTrue(a['peak_ok'], a)
        self.assertGreater(a['peak_to_median_db'], 20.0)
        self.assertFalse(a['clipped'])
        self.assertEqual(a['full_scale_fraction'], 0.0)
        self.assertGreater(a['full_scale_headroom_db'], 3.0)

    def test_digital_full_scale_is_computed_not_inferred_from_text(self):
        a = planner.analyse_stage0_window(_tone_window(amplitude=1.0, noise=0.0),
                                          FS, TARGET_HZ)
        self.assertTrue(a['clipped'])
        self.assertGreater(a['full_scale_fraction'], 0.0)
        self.assertLessEqual(a['full_scale_headroom_db'], 0.0)

    def test_off_window_is_quieter_in_the_target_band(self):
        on = planner.analyse_stage0_window(_tone_window(), FS, TARGET_HZ)
        off = planner.analyse_stage0_window(_noise_window(), FS, TARGET_HZ)
        self.assertLess(off['target_band_power_db'], on['target_band_power_db'] - 20.0)
        self.assertFalse(off['clipped'])

    def _iqs(self, **override):
        iqs = {'OFF_PRE': _noise_window(), 'TONE': _tone_window(),
               'OFF_POST': _noise_window()}
        iqs.update(override)
        return iqs

    def _report(self, iqs=None):
        iqs = iqs or self._iqs()
        return {name: planner.analyse_stage0_window(iqs[name], FS, TARGET_HZ)
                for name in ('OFF_PRE', 'TONE', 'OFF_POST')}

    def _narrowband(self, **override):
        iqs = self._iqs(**override)
        return planner.matched_narrowband_on_off(
            iqs['TONE'], iqs['OFF_PRE'], iqs['OFF_POST'], FS)

    def test_evaluation_passes_a_clean_on_off_contrast(self):
        verdict = planner.evaluate_stage0_windows(
            self._report(), narrowband=self._narrowband())
        self.assertTrue(verdict['passed'], verdict)
        comparison = verdict['on_off_comparison']
        self.assertEqual(comparison['metric'], planner.STAGE0_ON_OFF_METRIC)
        self.assertEqual(comparison['nbins'], 2 * planner.STAGE0_ON_OFF_HALF_BINS + 1)
        self.assertEqual(comparison['threshold_db'], planner.STAGE0_MIN_ON_OFF_DB)
        self.assertGreaterEqual(comparison['delta_db'],
                                planner.STAGE0_MIN_ON_OFF_DB)
        self.assertTrue(comparison['passed'])

    def test_evaluation_fails_a_missing_window(self):
        report = self._report()
        report.pop('OFF_POST')
        verdict = planner.evaluate_stage0_windows(
            report, narrowband=self._narrowband())
        self.assertFalse(verdict['passed'])

    def test_evaluation_fails_a_clipped_window(self):
        clipped_iq = _tone_window(amplitude=1.0, noise=0.0)
        report = self._report(self._iqs(TONE=clipped_iq))
        verdict = planner.evaluate_stage0_windows(
            report, narrowband=self._narrowband(TONE=clipped_iq))
        self.assertFalse(verdict['passed'])
        self.assertTrue(any('full scale' in p for p in verdict['problems']), verdict)

    def test_evaluation_fails_when_the_off_window_is_not_quieter(self):
        tone_iq = _tone_window()
        report = self._report(self._iqs(OFF_PRE=tone_iq))
        verdict = planner.evaluate_stage0_windows(
            report, narrowband=self._narrowband(OFF_PRE=tone_iq))
        self.assertFalse(verdict['passed'])

    def test_evaluation_fails_a_peak_outside_the_tolerance(self):
        shifted = _tone_window(offset_hz=300000.0)
        report = self._report(self._iqs(TONE=shifted))
        verdict = planner.evaluate_stage0_windows(
            report, narrowband=self._narrowband(TONE=shifted))
        self.assertFalse(verdict['passed'])


class Stage0NarrowbandCriterionTests(unittest.TestCase):
    """task-6: replacing the 20 kHz band gate with the matched 9-bin metric.

    Direct counterexample: a narrowband CW tone over a broadband noise floor
    fails the old 546-bin band mean but passes the matched 9-bin gate.
    """

    SAMPLES = 65536

    def _fixture(self, amplitude=1e-4, sigma=3.6e-4, dc=0.0 + 0.0j):
        t = np.arange(self.SAMPLES, dtype=float) / FS
        rng = np.random.default_rng(20261019)
        noise = (sigma * (rng.standard_normal(self.SAMPLES) +
                          1j * rng.standard_normal(self.SAMPLES))).astype(np.complex64)
        tone = (amplitude * np.exp(2j * np.pi * TARGET_HZ * t)).astype(np.complex64)
        tone_window = (tone + noise + dc).astype(np.complex64)
        off_pre = (noise + dc).astype(np.complex64)
        rng2 = np.random.default_rng(7)
        noise2 = (sigma * (rng2.standard_normal(self.SAMPLES) +
                           1j * rng2.standard_normal(self.SAMPLES))).astype(np.complex64)
        off_post = (noise2 + dc).astype(np.complex64)
        return tone_window, off_pre, off_post

    @staticmethod
    def _analyses(tone_iq, off_pre, off_post):
        return {'TONE': planner.analyse_stage0_window(tone_iq, FS, TARGET_HZ),
                'OFF_PRE': planner.analyse_stage0_window(off_pre, FS, TARGET_HZ),
                'OFF_POST': planner.analyse_stage0_window(off_post, FS, TARGET_HZ)}

    @staticmethod
    def _legacy_delta(analyses):
        on = analyses['TONE']['target_band_power_db']
        off = max(analyses['OFF_PRE']['target_band_power_db'],
                  analyses['OFF_POST']['target_band_power_db'])
        return on - off

    def test_the_band_mean_would_fail_while_the_matched_gate_passes(self):
        tone_iq, off_pre, off_post = self._fixture()
        analyses = self._analyses(tone_iq, off_pre, off_post)
        narrowband = planner.matched_narrowband_on_off(
            tone_iq, off_pre, off_post, FS)
        legacy = self._legacy_delta(analyses)
        # This is the exact #2 shape: the 546-bin band mean is well below 20 dB
        # while the matched 9-bin ratio is above it.
        self.assertLess(legacy, planner.STAGE0_MIN_ON_OFF_DB)
        self.assertGreaterEqual(narrowband['delta_db'],
                                planner.STAGE0_MIN_ON_OFF_DB)
        verdict = planner.evaluate_stage0_windows(
            analyses, narrowband=narrowband)
        self.assertTrue(verdict['passed'], verdict)
        comparison = verdict['on_off_comparison']
        # The legacy band delta survives as information, but is not a gate.
        self.assertFalse(comparison['legacy_band_passed'])
        self.assertLess(comparison['legacy_band_delta_db'],
                        planner.STAGE0_MIN_ON_OFF_DB)
        self.assertIn('not a gate', comparison['legacy_band_note'])
        self.assertTrue(comparison['passed'])

    def test_a_weak_or_absent_tone_fails_both_metrics(self):
        for amplitude in (0.0, 2e-5):
            with self.subTest(amplitude=amplitude):
                tone_iq, off_pre, off_post = self._fixture(amplitude=amplitude)
                analyses = self._analyses(tone_iq, off_pre, off_post)
                narrowband = planner.matched_narrowband_on_off(
                    tone_iq, off_pre, off_post, FS)
                self.assertLess(self._legacy_delta(analyses),
                                planner.STAGE0_MIN_ON_OFF_DB)
                self.assertLess(narrowband['delta_db'],
                                planner.STAGE0_MIN_ON_OFF_DB)
                verdict = planner.evaluate_stage0_windows(
                    analyses, narrowband=narrowband)
                self.assertFalse(verdict['passed'])
                self.assertTrue(any('matched narrowband' in p
                                    for p in verdict['problems']), verdict)

    def test_a_dc_offset_does_not_move_the_matched_metric(self):
        tone_iq, off_pre, off_post = self._fixture()
        base = planner.matched_narrowband_on_off(tone_iq, off_pre, off_post, FS)
        dc_tone, dc_pre, dc_post = self._fixture(dc=0.5 + 0.2j)
        shifted = planner.matched_narrowband_on_off(dc_tone, dc_pre, dc_post, FS)
        self.assertAlmostEqual(shifted['delta_db'], base['delta_db'], places=2)


class Stage0GainSignTests(unittest.TestCase):
    """libiio hardwaregain is negative; the attenuation is positive."""

    def test_hardwaregain_constants_are_the_negative_of_the_attenuation(self):
        self.assertEqual(planner.STAGE0_TX_HARDWARE_GAIN_DB, -60.0)
        self.assertEqual(planner.STAGE0_CEILING_HARDWARE_GAIN_DB, -89.75)
        self.assertEqual(planner.STAGE0_TX_HARDWARE_GAIN_DB,
                         -planner.STAGE0_TX_ATTENUATION_DB)
        self.assertEqual(planner.STAGE0_CEILING_HARDWARE_GAIN_DB,
                         -planner.STAGE0_CEILING_DB)

    def test_libiio_write_text_round_trips_through_the_strict_parser(self):
        pre = stage0.hardware_gain_write(planner.STAGE0_TX_HARDWARE_GAIN_DB)
        post = stage0.hardware_gain_write(planner.STAGE0_CEILING_HARDWARE_GAIN_DB)
        self.assertEqual(pre, '-60.000000')
        self.assertEqual(post, '-89.750000')
        self.assertAlmostEqual(
            readback.require_db(pre + ' dB', planner.STAGE0_TX_HARDWARE_GAIN_DB),
            -60.0, places=9)
        self.assertAlmostEqual(
            readback.require_db(post + ' dB', planner.STAGE0_CEILING_HARDWARE_GAIN_DB),
            -89.75, places=9)
        with self.assertRaises(readback.ReadbackError):
            readback.require_db(pre + ' dB', planner.STAGE0_TX_ATTENUATION_DB)

    def test_self_test_covers_the_gain_signs(self):
        names = [name for name, _ in planner.stage0_self_test(_cfg())['checks']]
        self.assertIn('stage0-gain-signs', names)


class Stage0StopGateTests(unittest.TestCase):
    def test_real_receipt_passes_the_stage0_stop_gate(self):
        receipt = json.loads(REAL_TX_RECEIPT.read_text(encoding='utf-8'))
        out = stage0.check_stop_receipt(receipt)
        self.assertEqual(out['tx_powerdown'], '1')
        self.assertAlmostEqual(out['hardwaregain_db'], -89.75, places=9)

    def test_stage0_stop_gate_refuses_the_pre_tx_snapshot(self):
        receipt = json.loads(REAL_TX_RECEIPT.read_text(encoding='utf-8'))
        swapped = copy.deepcopy(receipt)
        swapped['after'] = swapped['before']
        with self.assertRaises(readback.ReadbackError):
            stage0.check_stop_receipt(swapped)


class Stage0CliTests(unittest.TestCase):
    def _run(self, args):
        return subprocess.run([sys.executable, '-X', 'utf8', '-B',
                               str(ROOT / 'stage0.py')] + args,
                              cwd=str(PROJECT), capture_output=True,
                              text=True, encoding='utf-8', timeout=120)

    def test_help_is_offline(self):
        p = self._run(['--help'])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('--dry-run', p.stdout)
        self.assertIn('--execute', p.stdout)

    def test_dry_run_is_offline_and_plans_three_windows(self):
        p = self._run(['--dry-run'])
        self.assertEqual(p.returncode, 0, p.stderr)
        plan = json.loads(p.stdout)
        self.assertFalse(plan['hardware_accessed'])
        self.assertEqual([w['name'] for w in plan['windows']],
                         ['OFF_PRE', 'TONE', 'OFF_POST'])
        self.assertEqual(plan['budget']['tx_starts'], 1)

    def test_self_test_is_offline_and_passes(self):
        p = self._run(['--self-test'])
        self.assertEqual(p.returncode, 0, p.stderr)
        result = json.loads(p.stdout)
        self.assertTrue(result['passed'], result)
        self.assertFalse(result['hardware_accessed'])

    def test_execute_requires_a_path_confirmation(self):
        p = self._run(['--execute'])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('path-confirmed', (p.stderr + p.stdout).lower())



# ---------------------------------------------------------------------------
# Offline fakes for the two hardware halves.
#
# Nothing here imports iio or gnuradio.  The fakes model exactly what Stage 0
# touches and add one property the real stack has: exclusive USB ownership.  A
# live graph object blocks the next radio_test.context_for call, so the order
# of "drop the failed graph" -> "release USB" -> "reopen for the safe-off
# readback" is provable without hardware.
# ---------------------------------------------------------------------------


class _FakeAttr:
    """A libiio channel attribute; reads are counted and writes are logged."""

    def __init__(self, name, value, log=None, tag='', sequence=None):
        self._name = name
        self._value = str(value)
        self._log = log
        self._tag = tag
        self._sequence = list(sequence) if sequence is not None else None
        self.reads = 0

    @property
    def value(self):
        self.reads += 1
        if self._sequence:
            index = min(self.reads - 1, len(self._sequence) - 1)
            return self._sequence[index]
        return self._value

    @value.setter
    def value(self, value):
        self._value = str(value)
        if self._log is not None:
            self._log.append((self._tag, self._name, str(value)))


class _StickyAttr(_FakeAttr):
    """A write that never takes effect (a LO that stays at its old frequency)."""

    @_FakeAttr.value.setter
    def value(self, value):
        if self._log is not None:
            self._log.append((self._tag, self._name, str(value)))


class _FakeChannel:
    def __init__(self, attrs, log=None, tag=''):
        self.attrs = {name: _FakeAttr(name, value, log, tag)
                      for name, value in attrs.items()}


class _FakeDevice:
    """The ad9361-phy subset Stage 0 reads and writes."""

    def __init__(self, lo_frequency='2450000000', powerdown='1'):
        self.events = []
        self.tx = _FakeChannel({'hardwaregain': '0.000000 dB',
                                'rf_bandwidth': '20000000'},
                               log=self.events, tag='tx')
        self.lo = _FakeChannel({'powerdown': powerdown,
                                'frequency': lo_frequency},
                               log=self.events, tag='lo')

    def find_channel(self, name, output):
        return self.lo if name == 'altvoltage1' else self.tx


class _StickyLoDevice(_FakeDevice):
    """A device whose LO frequency write is silently ignored."""

    def __init__(self):
        super().__init__(lo_frequency='2450000000')
        self.lo.attrs['frequency'] = _StickyAttr('frequency', '2450000000',
                                                 self.events, 'lo')


class _SequenceDevice(_FakeDevice):
    """A device whose LO powerdown answers the given sequence on each read."""

    def __init__(self, sequence):
        super().__init__()
        self.lo.attrs['powerdown'] = _FakeAttr('powerdown', '1', self.events,
                                               'lo', sequence=sequence)


class _FakeContext:
    def __init__(self, device):
        self.device = device
        self.timeout = None

    def set_timeout(self, seconds):
        self.timeout = seconds

    def find_device(self, name):
        return self.device if name == 'ad9361-phy' else None


class _FakeLibiio:
    """Stand-in for radio_test.context_for; never opens a real device."""

    def __init__(self, device=None, registry=None):
        self.device = device or _FakeDevice()
        self.context = _FakeContext(self.device)
        self.registry = registry
        self.opens = 0
        self.uri = 'usb:2.1.5'

    def context_for(self, serial):
        if self.registry is not None and self.registry.live():
            raise RuntimeError('injected USB busy: a live graph still owns it')
        self.opens += 1
        return self.context, self.uri


class _FakeUsbRegistry:
    """Exclusive USB ownership by weak reference, mirroring C++ block lifetime.

    Graph objects are wired into a reference cycle (top_block <-> blocks), so an
    unreachable graph is released only by gc.collect() -- which is exactly what
    stage0._release_usb does.  Tests disable the automatic collector so that
    only an explicit collect can free the cycle.
    """

    def __init__(self):
        self._owners = []

    def hold(self, obj):
        self._owners.append(weakref.ref(obj))

    def live(self):
        return [ref for ref in self._owners if ref() is not None]


class _FakeClock:
    """A controllable monotonic clock: sleeps advance time deterministically."""

    def __init__(self, start=0.0):
        self.now = float(start)

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += float(seconds)


class _FakeBlock:
    def __init__(self):
        self.calls = []
        self.parent = None

    def __getattr__(self, name):
        if name.startswith('set_'):
            def recorder(*args, **kwargs):
                self.calls.append((name, args))
            return recorder
        raise AttributeError(name)


class _FakeSink(_FakeBlock):
    def data(self):
        return np.asarray(self.parent.window, dtype=np.complex64)


class _FakeIioSource(_FakeBlock):
    """Faithful GNU Radio 3.10 iio source: no sink-only setters.

    The real ``gnuradio.iio.iio_python.fmcomms2_source_fc32`` (Radioconda GNU
    Radio 3.10, observed 2026-10-19) has no ``set_bandwidth``/``set_attenuation``;
    only the sink does.  Calling them on a source raises AttributeError, which
    is exactly the real Stage 0 failure this fixture must reproduce.
    """

    _missing = ('set_bandwidth', 'set_attenuation')

    def __getattr__(self, name):
        if name in self._missing:
            raise AttributeError(
                "'gnuradio.iio.iio_python.fmcomms2_source_fc32' object has no "
                "attribute %r" % name)
        return super().__getattr__(name)


class _FakeIioSink(_FakeBlock):
    """The iio sink keeps the sink-only setters (real API, 2026-10-19)."""


class _FakeTopBlock:
    def __init__(self, name, registry, fail, window):
        self.name = name
        self.registry = registry
        self.fail = fail
        self.window = window
        self.blocks = []
        self.calls = []
        self._failed_wait = False
        if registry is not None:
            registry.hold(self)

    def connect(self, *blocks):
        for block in blocks:
            block.parent = self
            self.blocks.append(block)
            if self.registry is not None:
                self.registry.hold(block)

    def start(self):
        self.calls.append('start')
        if self.fail == 'start' and self.name == 'stage0 tone':
            raise RuntimeError('injected GNU Radio start failure')

    def stop(self):
        self.calls.append('stop')

    def wait(self):
        self.calls.append('wait')
        if (self.fail == 'wait' and self.name == 'stage0 tone' and
                not self._failed_wait):
            self._failed_wait = True
            raise RuntimeError('injected GNU Radio wait failure after TX start')

    def disconnect_all(self):
        self.calls.append('disconnect_all')


class _InstrumentedToneBlock(_FakeTopBlock):
    """A TONE graph that can fail a stop and charge time to the cleanup.

    Used by the B1 counterexamples: the cleanup stop()/wait() takes
    ``cleanup_delay`` seconds on the fake clock (the first successful wait),
    and the first ``stop_fail_times`` stop() calls raise instead of stopping.
    """

    def __init__(self, name, registry, fail, window, clock=None,
                 cleanup_delay=0.0, stop_fail_times=0):
        super().__init__(name, registry, fail, window)
        self.clock = clock
        self.cleanup_delay = float(cleanup_delay)
        self.stop_fail_times = int(stop_fail_times)
        self.stop_calls = 0
        self.wait_calls = 0

    def stop(self):
        self.calls.append('stop')
        self.stop_calls += 1
        if self.stop_calls <= self.stop_fail_times:
            raise RuntimeError('injected GNU Radio stop failure')

    def wait(self):
        self.calls.append('wait')
        self.wait_calls += 1
        if (self.fail == 'wait' and self.name == 'stage0 tone' and
                not self._failed_wait):
            self._failed_wait = True
            raise RuntimeError('injected GNU Radio wait failure after TX start')
        if self.clock is not None and self.cleanup_delay and self.wait_calls == 1:
            self.clock.sleep(self.cleanup_delay)


@contextlib.contextmanager
def _fake_gnuradio(registry, fail=None, windows=None, track=False,
                   tone_behavior=None):
    """Install a fake gnuradio package for the duration of the block.

    track=True keeps the created iio blocks alive for inspection (N4); the
    half-physical ownership model must not, or the fake bookkeeping would
    defeat the very lifetime check it exists to prove.  tone_behavior replaces
    the TONE top_block with _InstrumentedToneBlock (B1 clock/stop injection).
    """
    windows = list(windows or [])
    counter = {'index': 0}

    def next_window():
        index = counter['index']
        counter['index'] += 1
        if index < len(windows):
            return windows[index]
        return np.zeros(65536, dtype=np.complex64)

    def top_block(name='', catch_exceptions=False):
        window = next_window()
        if tone_behavior is not None and name == 'stage0 tone':
            return _InstrumentedToneBlock(name, registry, fail, window,
                                          **tone_behavior)
        return _FakeTopBlock(name, registry, fail, window)

    root = types.ModuleType('gnuradio')
    gr = types.ModuleType('gnuradio.gr')
    gr.sizeof_gr_complex = 8
    gr.top_block = top_block

    blocks = types.ModuleType('gnuradio.blocks')
    blocks.head = lambda itemsize, nitems: _FakeBlock()
    blocks.vector_sink_c = _FakeSink

    analog = types.ModuleType('gnuradio.analog')
    analog.GR_COS_WAVE = 0
    analog.sig_source_c = lambda *args, **kwargs: _FakeBlock()

    iio = types.ModuleType('gnuradio.iio')
    iio.created = [] if track else None

    def _source(uri, channels, buffer_size):
        block = _FakeIioSource()
        if iio.created is not None:
            iio.created.append(('source', block))
        return block

    def _sink(uri, channels, buffer_size, whatever):
        block = _FakeIioSink()
        if iio.created is not None:
            iio.created.append(('sink', block))
        return block

    iio.fmcomms2_source_fc32 = _source
    iio.fmcomms2_sink_fc32 = _sink

    root.gr = gr
    root.blocks = blocks
    root.analog = analog
    root.iio = iio

    saved = {}
    for name, module in (('gnuradio', root), ('gnuradio.gr', gr),
                         ('gnuradio.blocks', blocks),
                         ('gnuradio.analog', analog), ('gnuradio.iio', iio)):
        saved[name] = sys.modules.get(name)
        sys.modules[name] = module
    try:
        yield iio
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@contextlib.contextmanager
def _patched_context_for(replacement):
    original = radio_test.context_for
    radio_test.context_for = replacement
    try:
        yield
    finally:
        radio_test.context_for = original


def _stage0_run(cfg, registry, fail=None, windows=None, tone_behavior=None,
                clock=None):
    """Run the real run() on fakes with the automatic collector disabled."""
    out = Path(tempfile.mkdtemp()) / 'out'
    out.mkdir()
    libiio = _FakeLibiio(registry=registry)
    was_enabled = gc.isenabled()
    saved_time = stage0.time
    gc.disable()
    if clock is not None:
        stage0.time = clock
    try:
        with _patched_context_for(libiio.context_for):
            with _fake_gnuradio(registry, fail=fail, windows=windows,
                                tone_behavior=tone_behavior):
                report = stage0.run(cfg, out, 'half-physical injected run')
    finally:
        stage0.time = saved_time
        if was_enabled:
            gc.enable()
    saved = json.loads((out / 'stage0.json').read_text(encoding='utf-8'))
    return report, saved, libiio


class Stage0LibiioPreTests(unittest.TestCase):
    def test_pre_writes_and_compares_the_negative_gain_text(self):
        fake = _FakeLibiio()
        with _patched_context_for(fake.context_for):
            out = stage0._libiio_pre(_cfg())
        self.assertEqual(fake.device.tx.attrs['hardwaregain'].value, '-60.000000')
        self.assertEqual(out['hardwaregain_raw'], '-60.000000')
        self.assertAlmostEqual(out['hardwaregain_db'], -60.0, places=9)

    def test_pre_sets_and_verifies_the_lo_frequency_before_enabling(self):
        fake = _FakeLibiio()
        with _patched_context_for(fake.context_for):
            out = stage0._libiio_pre(_cfg())
        self.assertEqual(fake.device.lo.attrs['frequency'].value, '433900000')
        self.assertEqual(out['tx_lo_frequency_raw'], '2450000000')
        self.assertEqual(out['tx_lo_frequency_set'], '433900000')
        self.assertTrue(out['tx_lo_frequency_ok'])
        self.assertEqual(out['tx_lo_frequency_hz'], 433900000)
        self.assertEqual(out['powerdown_after_enable'], '0')
        events = fake.device.events
        frequency = events.index(('lo', 'frequency', '433900000'))
        enable = events.index(('lo', 'powerdown', '0'))
        self.assertLess(frequency, enable)
        before = [event[2] for event in events[:frequency]
                  if event[0] == 'lo' and event[1] == 'powerdown']
        self.assertEqual(before[-1], '1')

    def test_pre_refuses_a_lo_frequency_that_did_not_take(self):
        fake = _FakeLibiio(device=_StickyLoDevice())
        with _patched_context_for(fake.context_for):
            with self.assertRaises(stage0.Stage0Error):
                stage0._libiio_pre(_cfg())
        self.assertEqual(fake.device.lo.attrs['powerdown'].value, '1')


class Stage0LibiioPostTests(unittest.TestCase):
    def test_post_writes_the_ceiling_and_reads_powerdown_twice(self):
        fake = _FakeLibiio()
        with _patched_context_for(fake.context_for):
            out = stage0._libiio_post(_cfg())
        self.assertEqual(fake.device.tx.attrs['hardwaregain'].value, '-89.750000')
        self.assertGreaterEqual(fake.device.lo.attrs['powerdown'].reads, 2)
        self.assertEqual(out['tx_powerdown'], '1')
        self.assertEqual(out['tx_powerdown_raw_second_read'], '1')
        self.assertEqual(out['tx_powerdown_second_read'], '1')
        self.assertEqual(out['hardwaregain_raw_second_read'], '-89.750000')

    def test_post_refuses_a_second_powerdown_read_that_is_not_one(self):
        fake = _FakeLibiio(device=_SequenceDevice(['1', '0']))
        with _patched_context_for(fake.context_for):
            with self.assertRaises(stage0.Stage0Error):
                stage0._libiio_post(_cfg())


class Stage0GainWriteTextTests(unittest.TestCase):
    def test_helpers_return_the_exact_libiio_text(self):
        self.assertEqual(stage0.pre_gain_text(), '-60.000000')
        self.assertEqual(stage0.post_gain_text(), '-89.750000')
        self.assertEqual(stage0.lo_frequency_text(), '433900000')

    def test_self_test_locks_the_write_text(self):
        result = stage0.stage0_offline_self_test(_cfg())
        statuses = {name: status for name, status in result['checks']}
        self.assertEqual(statuses.get('stage0-gain-write-text'), 'ok')
        self.assertEqual(statuses.get('stage0-lo-frequency-write'), 'ok')
        self.assertTrue(result['passed'])

    def test_cli_self_test_reports_the_write_text_check(self):
        p = subprocess.run([sys.executable, '-X', 'utf8', '-B',
                            str(ROOT / 'stage0.py'), '--self-test'],
                           cwd=str(PROJECT), capture_output=True,
                           text=True, encoding='utf-8', timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        result = json.loads(p.stdout)
        names = [name for name, _ in result['checks']]
        self.assertIn('stage0-gain-write-text', names)
        self.assertTrue(result['passed'], result)


class Stage0ToneDurationTests(unittest.TestCase):
    def test_tone_seconds_uses_duration_s_not_the_budget(self):
        cfg = _cfg()
        self.assertAlmostEqual(stage0.tone_seconds(cfg, 2.0, 0.05), 1.95)
        # A shorter requested duration must shorten the tone: using the
        # budget alone would report 1.95 s instead of the requested 1.0 s.
        cfg['duration_s'] = 1.0
        self.assertAlmostEqual(stage0.tone_seconds(cfg, 2.0, 0.05), 1.0)
        plan = planner.stage0_plan(cfg)
        self.assertAlmostEqual(plan['budget']['tone_seconds'], 1.0)


class Stage0RxApiFidelityTests(unittest.TestCase):
    """fix4: the real GNU Radio 3.10 iio source has no set_bandwidth.

    These are the RED tests for the real 2026-10-19 failure: the fake source now
    matches the real API, so the old ``rx.set_bandwidth(...)`` reproduces the
    same AttributeError before the fix and the corrected code passes after it.
    """

    def test_fake_source_matches_the_real_iio_api(self):
        with _fake_gnuradio(None, windows=[_noise_window()], track=True) as iio:
            stage0._capture_rx_only(_cfg(), 'usb:fake', 0.0)
        sources = [block for kind, block in iio.created if kind == 'source']
        sinks = [block for kind, block in iio.created if kind == 'sink']
        self.assertTrue(sources)
        self.assertFalse(hasattr(sources[0], 'set_bandwidth'))
        self.assertFalse(hasattr(sources[0], 'set_attenuation'))
        with _fake_gnuradio(None, windows=[_tone_window()], track=True) as iio:
            cfg = _cfg()
            cfg['duration_s'] = 0.0
            stage0._capture_tone(cfg, 'usb:fake', 2.0, 0.05)
        sinks = [block for kind, block in iio.created if kind == 'sink']
        self.assertTrue(sinks)
        self.assertTrue(hasattr(sinks[0], 'set_bandwidth'))
        self.assertTrue(hasattr(sinks[0], 'set_attenuation'))

    def test_rx_only_works_without_a_source_set_bandwidth(self):
        with _fake_gnuradio(None, windows=[_noise_window()], track=True) as iio:
            stage0._capture_rx_only(_cfg(), 'usb:fake', 0.0)
        sources = [block for kind, block in iio.created if kind == 'source']
        self.assertEqual(len(sources), 1)
        self.assertNotIn('set_bandwidth', [name for name, _ in sources[0].calls])
        # configure() still applies its Auto filter exactly once.
        self.assertEqual(
            [name for name, _ in sources[0].calls].count('set_filter_params'), 1)

    def test_tone_works_without_a_source_set_bandwidth(self):
        registry = _FakeUsbRegistry()
        with _fake_gnuradio(registry, windows=[_tone_window()], track=True) as iio:
            cfg = _cfg()
            cfg['duration_s'] = 0.0
            stage0._capture_tone(cfg, 'usb:fake', 2.0, 0.05)
        sources = [block for kind, block in iio.created if kind == 'source']
        self.assertEqual(len(sources), 1)
        self.assertNotIn('set_bandwidth', [name for name, _ in sources[0].calls])
        self.assertEqual(
            [name for name, _ in sources[0].calls].count('set_filter_params'), 1)

    def test_plan_and_report_declare_the_rx_bandwidth_policy(self):
        plan = planner.stage0_plan(_cfg())
        self.assertEqual(plan['rx_bandwidth_policy'],
                         planner.STAGE0_RX_BANDWIDTH_POLICY)
        cfg = _cfg()
        cfg['duration_s'] = 0.05
        report, saved, libiio = _stage0_run(
            cfg, _FakeUsbRegistry(),
            windows=[_noise_window(), _tone_window(), _noise_window()])
        self.assertEqual(saved['rx_bandwidth_policy'],
                         planner.STAGE0_RX_BANDWIDTH_POLICY)


class Stage0HalfPhysicalTests(unittest.TestCase):
    def _failure(self, fail='wait'):
        cfg = _cfg()
        cfg['duration_s'] = 0.05
        registry = _FakeUsbRegistry()
        return _stage0_run(cfg, registry, fail=fail,
                           windows=[_noise_window(), _tone_window(),
                                    _noise_window()]) + (registry,)

    def test_failure_after_start_reports_the_attempted_tx(self):
        report, saved, libiio, registry = self._failure()
        self.assertEqual(saved['status'], 'FAILED')
        self.assertEqual(saved['tx_starts'], 1)
        self.assertIn('tx_seconds_measured', saved)
        self.assertGreaterEqual(saved['tx_seconds_measured'], 0.0)
        self.assertTrue(saved['tx_budget_ok'])
        self.assertEqual(saved['tx_retries'], 0)

    def test_failed_tone_is_cleaned_up_and_not_reported_unknown(self):
        report, saved, libiio, registry = self._failure()
        self.assertEqual(saved['status'], 'FAILED')
        self.assertFalse(saved['cleanup_required'])
        self.assertEqual(saved['final_tx_stop_state']['tx_powerdown'], '1')
        self.assertIn('release_usb_before_cleanup', saved['usb_phases'])
        self.assertTrue(saved['released_usb_before_cleanup'])
        self.assertEqual(libiio.opens, 2)

    def test_graph_objects_are_collected_before_the_cleanup_context_opens(self):
        report, saved, libiio, registry = self._failure()
        self.assertEqual(registry.live(), [])
        self.assertFalse(saved['cleanup_required'])

    def test_success_path_passes_and_states_the_budget_scope(self):
        cfg = _cfg()
        cfg['duration_s'] = 0.05
        registry = _FakeUsbRegistry()
        report, saved, libiio = _stage0_run(
            cfg, registry, windows=[_noise_window(), _tone_window(),
                                    _noise_window()])
        self.assertEqual(saved['status'], 'STAGE0_PASS', saved.get('problems'))
        self.assertEqual(saved['tx_starts'], 1)
        self.assertIn('tx_seconds_measured', saved)
        self.assertEqual(saved['tx_budget_scope'], 'modulated TONE graph only')
        self.assertEqual(saved['lo_on_windows'],
                         ['OFF_PRE', 'TONE', 'OFF_POST'])
        self.assertEqual(saved['rx_bandwidth_policy'],
                         planner.STAGE0_RX_BANDWIDTH_POLICY)
        self.assertEqual(registry.live(), [])


class _Interrupt(BaseException):
    """A BaseException that is not KeyboardInterrupt, so a RED run is an error."""


class Stage0TxAccountingTests(unittest.TestCase):
    """Codex blocking B1/B2 counterexamples: stop time, budget and cleanup."""

    def _short_cfg(self):
        cfg = _cfg()
        cfg['duration_s'] = 0.05
        return cfg

    def test_delayed_cleanup_after_a_start_failure_is_measured_not_claimed(self):
        # Codex repro B1 (a): "started" then raised, cleanup stop()+wait() takes
        # 3 s.  The old code recorded the stop time before the cleanup, so the
        # report said 0.00 s and tx_budget_ok=true.
        clock = _FakeClock()
        registry = _FakeUsbRegistry()
        report, saved, libiio, registry = _stage0_run(
            self._short_cfg(), registry, fail='start',
            windows=[_noise_window()],
            tone_behavior={'clock': clock, 'cleanup_delay': 3.0},
            clock=clock) + (registry,)
        self.assertEqual(saved['status'], 'FAILED')
        self.assertEqual(saved['tx_starts'], 1)
        self.assertTrue(saved['tx_stop_confirmed'])
        self.assertAlmostEqual(saved['tx_seconds_measured'], 3.0, places=6)
        self.assertFalse(saved['tx_budget_ok'])

    def test_first_stop_failure_charges_the_cleanup_time(self):
        # Codex repro B1 (b): the first stop() raises; the cleanup stop()+wait()
        # takes 3 s.  The old code recorded 1.96 s and claimed the budget.
        clock = _FakeClock()
        registry = _FakeUsbRegistry()
        report, saved, libiio, registry = _stage0_run(
            self._short_cfg(), registry,
            windows=[_noise_window(), _tone_window(), _noise_window()],
            tone_behavior={'clock': clock, 'cleanup_delay': 3.0,
                           'stop_fail_times': 1},
            clock=clock) + (registry,)
        self.assertEqual(saved['status'], 'FAILED')
        self.assertEqual(saved['tx_starts'], 1)
        self.assertTrue(saved['tx_stop_confirmed'])
        self.assertGreaterEqual(saved['tx_seconds_measured'], 3.0)
        self.assertFalse(saved['tx_budget_ok'])

    def test_unconfirmed_stop_never_claims_the_budget(self):
        # Both the in-try stop and the cleanup stop fail: the graph is never
        # confirmed stopped, so no measured TX time and no budget pass.
        clock = _FakeClock()
        registry = _FakeUsbRegistry()
        report, saved, libiio, registry = _stage0_run(
            self._short_cfg(), registry,
            windows=[_noise_window(), _tone_window(), _noise_window()],
            tone_behavior={'clock': clock, 'stop_fail_times': 2},
            clock=clock) + (registry,)
        self.assertEqual(saved['status'], 'FAILED')
        self.assertEqual(saved['tx_starts'], 1)
        self.assertFalse(saved['tx_stop_confirmed'])
        self.assertNotIn('tx_seconds_measured', saved)
        self.assertFalse(saved['tx_budget_ok'])
        # The forced libiio readback still proves the transmitter is off.
        self.assertFalse(saved['cleanup_required'])
        self.assertEqual(saved['final_tx_stop_state']['tx_powerdown'], '1')

    def test_interrupted_usb_release_is_reported_unknown_not_clean(self):
        # Codex repro B2: the TONE wait fails and the cleanup-path _release_usb
        # is interrupted.  The forced safe-off never runs, so the report must be
        # UNKNOWN / cleanup_required=true, not FAILED / cleanup_required=false.
        registry = _FakeUsbRegistry()
        original = stage0._release_usb
        calls = {'n': 0}

        def interrupting_release():
            calls['n'] += 1
            if calls['n'] == 3:  # pre-phase, after OFF_PRE, then run cleanup
                raise _Interrupt('injected USB release interruption')
            original()

        stage0._release_usb = interrupting_release
        try:
            report, saved, libiio, registry = _stage0_run(
                self._short_cfg(), registry, fail='wait',
                windows=[_noise_window(), _tone_window(),
                         _noise_window()]) + (registry,)
        finally:
            stage0._release_usb = original
        self.assertEqual(saved['status'], 'UNKNOWN')
        self.assertTrue(saved['cleanup_required'])
        self.assertIn('_Interrupt', saved['cleanup_error'])
        self.assertNotIn('release_usb_before_cleanup', saved['usb_phases'])
        self.assertFalse(saved['released_usb_before_cleanup'])
        self.assertNotIn('final_tx_stop_state', saved)
        # The forced safe-off never opened a second libiio context: the TX LO
        # stayed enabled, which is exactly why the run must say UNKNOWN.
        self.assertEqual(libiio.opens, 1)
        self.assertEqual(libiio.device.lo.attrs['powerdown'].value, '0')

    def test_tone_window_keeps_the_sink_side_bandwidth_setter(self):
        # The TONE graph configures the TX sink (which does have
        # set_bandwidth); the RX source must not be asked for it.
        cfg = self._short_cfg()
        cfg['duration_s'] = 0.0
        with _fake_gnuradio(None, windows=[_tone_window()], track=True) as iio:
            stage0._capture_tone(cfg, 'usb:fake', 2.0, 0.05)
        sinks = [block for kind, block in iio.created if kind == 'sink']
        sources = [block for kind, block in iio.created if kind == 'source']
        self.assertEqual(len(sinks), 1)
        self.assertEqual(len(sources), 1)
        self.assertIn(('set_bandwidth', (1500000,)), sinks[0].calls)
        self.assertNotIn('set_bandwidth', [name for name, _ in sources[0].calls])


if __name__ == '__main__':
    unittest.main()