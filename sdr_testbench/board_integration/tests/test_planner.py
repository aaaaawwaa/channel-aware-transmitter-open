"""Offline tests for the Pluto<->board integration planner.

These tests never open a socket, a serial port, libiio or GNU Radio.  The
hardware modules are imported only by the CLI's execute path, which is not
exercised here.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT / 'pynq_overlay' / 'sw'))

import planner  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_default_config_loads_and_validates(self):
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        planner.validate_config(cfg)

    def test_unsafe_pluto_amplitude_is_rejected(self):
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        cfg['pluto']['amplitude'] = 0.30
        with self.assertRaises(planner.PlanError) as ctx:
            planner.validate_config(cfg)
        self.assertIn('amplitude', str(ctx.exception))

    def test_board_attenuation_must_stay_at_the_frozen_value(self):
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        cfg['board']['attenuation_mdb'] = 40000
        with self.assertRaises(planner.PlanError) as ctx:
            planner.validate_config(cfg)
        self.assertIn('46000', str(ctx.exception))

    def test_pluto_tx_attenuation_must_not_drop_below_start_value(self):
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        cfg['pluto']['tx_attenuation_db'] = 40.0
        with self.assertRaises(planner.PlanError) as ctx:
            planner.validate_config(cfg)
        self.assertIn('60', str(ctx.exception))

    def test_pluto_tone_must_stay_in_band(self):
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        cfg['pluto']['tone_hz'] = [2400000]
        with self.assertRaises(planner.PlanError):
            planner.validate_config(cfg)

    def test_dry_run_must_not_depend_on_hardware_modules(self):
        self.assertFalse(hasattr(planner, 'socket'))
        self.assertFalse(hasattr(planner, 'iio'))
        self.assertFalse(hasattr(planner, 'gnuradio'))


class ToneTests(unittest.TestCase):
    def test_board_tone_bins_are_periodic_and_exact(self):
        k, actual = planner.nearest_board_tone_bin(60000000, 4096, 100000)
        self.assertEqual(k, 7)
        self.assertAlmostEqual(actual, 102539.0625, places=6)
        k2, actual2 = planner.nearest_board_tone_bin(60000000, 4096, 200000)
        self.assertEqual(k2, 14)
        self.assertAlmostEqual(actual2, 205078.125, places=6)

    def test_tone_payload_has_expected_length_and_range(self):
        payload, meta = planner.tone_payload(
            sample_rate_hz=60000000, samples=4096, bin_index=7,
            amplitude=60)
        self.assertEqual(len(payload), 4096 * 4)
        self.assertEqual(meta['samples'], 4096)
        iq = np.frombuffer(payload, dtype='<i2').reshape(-1, 2)
        self.assertLessEqual(int(np.max(np.abs(iq))), 2047)
        self.assertEqual(int(np.max(np.abs(iq))), 60)

    def test_tone_payload_fft_peaks_at_requested_bin(self):
        payload, _ = planner.tone_payload(
            sample_rate_hz=60000000, samples=4096, bin_index=14,
            amplitude=60)
        iq = np.frombuffer(payload, dtype='<i2').reshape(-1, 2)
        z = iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)
        spectrum = np.abs(np.fft.fft(z))
        self.assertEqual(int(np.argmax(spectrum)), 14)

    def test_offline_replay_of_board_tone_is_detectable(self):
        # The Pluto receiver stores complex64; this is the same shape the
        # analysis uses, without touching a device.
        payload, _ = planner.tone_payload(60000000, 4096, 7, 60)
        iq = np.frombuffer(payload, dtype='<i2').reshape(-1, 2)
        z = (iq[:, 0] + 1j * iq[:, 1]).astype(np.complex64) / 2048.0
        result = planner.analyse_tone(z, 60000000.0, expected_offset_hz=102539.0625)
        self.assertTrue(result['peak_ok'])
        self.assertGreater(result['peak_to_median_db'], 40.0)


class BpskTests(unittest.TestCase):
    def _board_frame_from_pluto_waveform(self):
        payload, meta = planner.bpsk_rect_payload(
            seed=1, sample_rate_hz=6000000, symbol_rate_hz=3000000,
            amplitude=0.1, symbols=127)
        self.assertEqual(meta['samples_per_symbol'], 2)
        iq = np.frombuffer(payload, dtype='<i2').reshape(-1, 2)
        # Pluto at 6 MSPS -> board at 60 MSPS is a 10x hold, exactly RECT.
        up = np.repeat(iq, 10, axis=0)
        reps = int(np.ceil(4096.0 / len(up)))
        tiled = np.tile(up, (reps, 1))[:4096]
        return tiled.astype('<i2').tobytes(), meta

    def test_pluto_bpsk_parameters_are_demodulated_by_board_receiver(self):
        import rf_bpsk_receiver as R
        import rf_rx_types as T
        payload, meta = self._board_frame_from_pluto_waveform()
        rb = T.RxReadback(io_ok=True, rx_armed=False, rx_done=True,
                          rx_position_words=4096, tx_source_selected=False,
                          sampled_at_monotonic_ns=1)
        frame = T.RxIqFrame(capture_id='offline-test', payload=payload,
                            samples=4096, sample_rate_hz=60000000, ratio=0,
                            daemon_timestamp_ns=1, connection_generation=1,
                            config_revision=1, request_id=1,
                            release_id='offline', op='rx.capture',
                            physical=False, pre_readback=rb, post_readback=rb,
                            completed_at_monotonic=0.0)
        result = R.receive_bpsk(frame, R.BpskConfig(reference_prbs7_seed=1))
        self.assertTrue(result.reference_sync, result.reason)
        self.assertEqual(result.bit_errors, 0)
        self.assertIsNotNone(result.ber)
        self.assertLessEqual(result.ber, 0.0)

    def test_template_2p4_msps_cannot_carry_the_3msym_signal(self):
        with self.assertRaises(planner.PlanError):
            planner.bpsk_rect_payload(seed=1, sample_rate_hz=2400000,
                                      symbol_rate_hz=3000000, amplitude=0.1)

    def test_differential_encoding_is_declared_absent(self):
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        self.assertFalse(cfg['bpsk']['differential_encoding'])
        self.assertEqual(cfg['bpsk']['shaping'], 'RECT')


class ReadbackGateTests(unittest.TestCase):
    def _cfg(self):
        return planner.load_config(ROOT / 'config' / 'diagnostic.json')

    def test_pre_tx_readback_accepts_exact_domain(self):
        cfg = self._cfg()
        rb = dict(lo_hz=433900000, sample_rate_hz=60000000,
                  rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000,
                  attenuation_mdb=46000, phy_mode_1r1t=0,
                  product_id=0x0A, hardware_verified=True)
        self.assertEqual(planner.check_configure_readback(rb, cfg), [])

    def test_pre_tx_readback_rejects_wrong_attenuation(self):
        cfg = self._cfg()
        rb = dict(lo_hz=433900000, sample_rate_hz=60000000,
                  rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000,
                  attenuation_mdb=40000, phy_mode_1r1t=0,
                  product_id=0x0A, hardware_verified=True)
        problems = planner.check_configure_readback(rb, cfg)
        self.assertTrue(any('attenuation' in p for p in problems), problems)

    def test_pre_tx_readback_rejects_wrong_lo_and_sample_rate(self):
        cfg = self._cfg()
        rb = dict(lo_hz=80000000, sample_rate_hz=60000000,
                  rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000,
                  attenuation_mdb=46000, phy_mode_1r1t=0,
                  product_id=0x0A, hardware_verified=True)
        self.assertTrue(planner.check_configure_readback(rb, cfg))
        rb['lo_hz'] = 433900000
        rb['sample_rate_hz'] = 2400000
        self.assertTrue(planner.check_configure_readback(rb, cfg))

    def test_stop_readback_requires_ceiling_attenuation_and_tx_off(self):
        cfg = self._cfg()
        ok = dict(attenuation_mdb=89750, hardware_verified=True,
                  io_ok=True, tx_source_selected=False, rx_armed=False)
        self.assertEqual(planner.check_safe_stop(ok, cfg), [])
        bad = dict(ok, attenuation_mdb=46000, tx_source_selected=True)
        problems = planner.check_safe_stop(bad, cfg)
        self.assertTrue(any('attenuation' in p for p in problems), problems)
        self.assertTrue(any('tx' in p for p in problems), problems)

    def test_fixture_files_are_the_same_contract(self):
        cfg = self._cfg()
        ok = json.loads((ROOT / 'fixtures' / 'configure-readback-ok.json').read_text(encoding='utf-8'))
        bad = json.loads((ROOT / 'fixtures' / 'configure-readback-bad-atten.json').read_text(encoding='utf-8'))
        self.assertEqual(planner.check_configure_readback(ok, cfg), [])
        self.assertTrue(planner.check_configure_readback(bad, cfg))
        stop_ok = json.loads((ROOT / 'fixtures' / 'safe-stop-ok.json').read_text(encoding='utf-8'))
        stop_bad = json.loads((ROOT / 'fixtures' / 'safe-stop-bad.json').read_text(encoding='utf-8'))
        self.assertEqual(planner.check_safe_stop(stop_ok, cfg), [])
        self.assertTrue(planner.check_safe_stop(stop_bad, cfg))

    def test_pre_tx_readback_requires_hardware_verified(self):
        cfg = self._cfg()
        rb = dict(lo_hz=433900000, sample_rate_hz=60000000,
                  rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000,
                  attenuation_mdb=46000, phy_mode_1r1t=0,
                  product_id=0x0A, hardware_verified=False)
        self.assertTrue(planner.check_configure_readback(rb, cfg))


class BudgetTests(unittest.TestCase):
    def test_direction_a_smoke_budget(self):
        b = planner.budget('A', sessions=1)
        self.assertEqual(b['windows_total'], 4)
        self.assertEqual(b['board_inits'], 1)
        self.assertEqual(b['board_tx_loads'], 2)
        self.assertEqual(b['board_tx_starts'], 2)
        self.assertEqual(b['board_configures'], 3)
        self.assertEqual(b['board_rx_frames'], 0)
        self.assertEqual(b['pluto_rx_windows'], 4)
        self.assertEqual(b['pluto_tx_starts'], 0)
        self.assertEqual(b['tx_starts_total'], 2)

    def test_direction_b_smoke_budget(self):
        b = planner.budget('B', sessions=1)
        self.assertEqual(b['windows_total'], 4)
        self.assertEqual(b['board_inits'], 1)
        self.assertEqual(b['board_tx_starts'], 0)
        self.assertEqual(b['board_rx_frames'], 4)
        self.assertEqual(b['pluto_tx_starts'], 2)
        self.assertEqual(b['pluto_tx_seconds'], 4.0)
        self.assertEqual(b['tx_starts_total'], 2)

    def test_fifth_window_is_refused(self):
        with self.assertRaises(planner.PlanError):
            planner.budget('A', sessions=1, windows_per_session=5)

    def test_extension_includes_the_smoke_session(self):
        b = planner.budget('A', sessions=3, extended=True)
        self.assertEqual(b['windows_total'], 12)
        self.assertEqual(b['added_sessions'], 2)
        self.assertTrue(b['includes_smoke_session'])
        self.assertEqual(b['board_inits'], 3)
        self.assertEqual(b['board_tx_starts'], 6)
        self.assertEqual(b['pluto_rx_windows'], 12)

    def test_extension_requires_explicit_flag_and_three_session_ceiling(self):
        with self.assertRaises(planner.PlanError):
            planner.budget('A', sessions=3, extended=False)
        with self.assertRaises(planner.PlanError):
            planner.budget('A', sessions=4, extended=True)

    def test_windows_are_off_tone_tone_off(self):
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        names = [w['name'] for w in planner.build_windows('A', cfg)]
        self.assertEqual(names, ['OFF_PRE', 'TONE1', 'TONE2', 'OFF_POST'])
        names_b = [w['name'] for w in planner.build_windows('B', cfg)]
        self.assertEqual(names_b, ['OFF_PRE', 'TONE1', 'TONE2', 'OFF_POST'])


class CliTests(unittest.TestCase):
    def _run(self, args):
        return subprocess.run([sys.executable, '-X', 'utf8', '-B',
                               str(ROOT / 'board_integration.py')] + args,
                              cwd=str(PROJECT), capture_output=True,
                              text=True, encoding='utf-8', timeout=120)

    def test_help_works_without_hardware(self):
        p = self._run(['--help'])
        self.assertEqual(p.returncode, 0, p.stderr)
        for word in ('--dry-run', '--self-test', '--direction'):
            self.assertIn(word, p.stdout)

    def test_dry_run_a_is_offline_and_has_four_windows(self):
        p = self._run(['--dry-run', '--direction', 'A'])
        self.assertEqual(p.returncode, 0, p.stderr)
        plan = json.loads(p.stdout)
        self.assertFalse(plan['hardware_accessed'])
        self.assertEqual(len(plan['windows']), 4)
        self.assertEqual(plan['budget']['windows_total'], 4)
        self.assertEqual(plan['direction'], 'A')

    def test_dry_run_b_keeps_board_tx_off(self):
        p = self._run(['--dry-run', '--direction', 'B'])
        self.assertEqual(p.returncode, 0, p.stderr)
        plan = json.loads(p.stdout)
        self.assertFalse(any(w['board_tx'] for w in plan['windows']))
        self.assertEqual(plan['budget']['board_tx_starts'], 0)
        self.assertEqual(plan['budget']['board_rx_frames'], 4)

    def test_self_test_is_offline_and_passes(self):
        p = self._run(['--self-test'])
        self.assertEqual(p.returncode, 0, p.stderr)
        result = json.loads(p.stdout)
        self.assertTrue(result['passed'], result)
        self.assertFalse(result['hardware_accessed'])

    def test_execute_refuses_multi_session_without_touching_hardware(self):
        p = self._run(['--execute', '--direction', 'A', '--sessions', '3',
                       '--extended', '--path-confirmed', 'offline-test',
                       '--token-file', 'offline-test'])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('EXECUTE_REFUSED', p.stderr)

    def test_dry_run_rejects_unknown_direction(self):
        p = self._run(['--dry-run', '--direction', 'C'])
        self.assertNotEqual(p.returncode, 0)


REAL_TX_RECEIPT = (PROJECT / 'sdr_testbench' / 'results' /
                   '20261019-173154-376468-tx' / 'result.json')


class RealReceiptReplayTests(unittest.TestCase):
    """Replay the real 2026-10-19 17:31:54 TX receipt byte for byte.

    The receipt records powerdown on altvoltage1_out (the TX LO) and
    hardwaregain on voltage0_out (the TX chain).  Reading the gain from the LO
    channel rejects a correct safe-off, which is exactly the defect this test
    pins down.  The historical file is never rewritten to fit the code.
    """

    def _receipt(self):
        return json.loads(REAL_TX_RECEIPT.read_text(encoding='utf-8'))

    def test_post_readback_accepts_the_real_receipt(self):
        import board_integration
        post = board_integration._check_pluto_post(self._receipt())
        self.assertEqual(post['tx_powerdown'], '1')
        self.assertAlmostEqual(post['hardwaregain_db'], -89.75, places=9)

    def test_post_readback_refuses_the_pre_tx_snapshot(self):
        import board_integration
        receipt = self._receipt()
        receipt['after'] = receipt['before']
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._check_pluto_post(receipt)

    def test_post_readback_refuses_gain_moved_to_the_lo_channel(self):
        import board_integration
        receipt = self._receipt()
        channels = receipt['after']['channels']
        channels['altvoltage1_out']['hardwaregain'] = channels['voltage0_out'].pop('hardwaregain')
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._check_pluto_post(receipt)


class _FakeSnapshot(object):
    def __init__(self, online=True, stale=False, io_ok=True, tx=False,
                 rx=False, error=None):
        self.online = online
        self.stale = stale
        self.armed = False
        self.error = error
        self.release_id = 'offline-test-release'
        self.control_ops = ('control.arm',)
        self.pl_status = types.SimpleNamespace(
            io_ok=io_ok,
            tx=types.SimpleNamespace(source_selected=tx, length_words=None,
                                     position_words=None),
            rx=types.SimpleNamespace(armed=rx, done=None, position_words=None))

    def to_dict(self):
        return {'online': self.online, 'stale': self.stale, 'error': self.error,
                'armed': self.armed, 'release_id': self.release_id,
                'control_ops': list(self.control_ops),
                'pl_status': {'io_ok': self.pl_status.io_ok,
                              'tx': {'source_selected': self.pl_status.tx.source_selected},
                              'rx': {'armed': self.pl_status.rx.armed}}}


class PreflightCliTests(unittest.TestCase):
    """--preflight must judge the board, not print passed=true unconditionally.

    Every case stubs the Pluto readback and the board client, so no USB, socket
    or serial port is opened.
    """

    def setUp(self):
        import board_integration
        self.mod = board_integration
        self.addCleanup(self._restore)
        self._orig_off = board_integration._pluto_safe_off
        self._orig_client = board_integration._load_board_client
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.token = Path(self._tmp.name) / 'token.txt'
        self.token.write_text('offline-token\n', encoding='utf-8')

    def _restore(self):
        self.mod._pluto_safe_off = self._orig_off
        self.mod._load_board_client = self._orig_client

    def _stub_pluto(self, result=None, error=None):
        def fake(serial, iio_module=None):
            if error is not None:
                raise error
            return result if result is not None else {
                'uri': 'usb:offline', 'serial': serial,
                'hardwaregain_db': -89.75, 'tx_powerdown': '1'}
        self.mod._pluto_safe_off = fake

    def _stub_client(self, snapshot):
        class FakeClient(object):
            def __init__(self, port=5000, **kwargs):
                self.port = port

            def poll(self):
                return snapshot

            def close(self):
                pass
        self.mod._load_board_client = lambda client_dir: (FakeClient, RuntimeError)

    def _run(self, args):
        import contextlib
        import io
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            rc = self.mod.main(args)
        return rc, stream.getvalue()

    def _preflight(self, snapshot=None, with_token=True, pluto_result=None,
                   pluto_error=None):
        self._stub_pluto(pluto_result, pluto_error)
        args = ['--preflight']
        if with_token:
            self._stub_client(snapshot if snapshot is not None else _FakeSnapshot())
            args += ['--token-file', str(self.token)]
        return self._run(args)

    def test_idle_board_passes_and_is_labelled_as_a_write(self):
        rc, out = self._preflight()
        payload = json.loads(out)
        self.assertEqual(rc, 0, payload)
        self.assertTrue(payload['passed'], payload)
        self.assertEqual(payload['scope'], 'pluto+board')
        self.assertTrue(payload['board_checked'])
        self.assertFalse(payload['read_only'])
        self.assertTrue(payload['writes'])
        self.assertTrue(payload['board_gates']['passed'], payload['board_gates'])
        for key in ('online', 'stale', 'io_ok', 'tx_source_selected', 'rx_armed'):
            self.assertIn(key, payload['board_gates'])

    def test_a_transmitting_board_fails_preflight(self):
        rc, out = self._preflight(_FakeSnapshot(tx=True))
        payload = json.loads(out)
        self.assertEqual(rc, 1, payload)
        self.assertFalse(payload['passed'])
        self.assertTrue(payload['board_gates']['problems'])
        self.assertTrue(any('TX' in p for p in payload['board_gates']['problems']),
                        payload['board_gates'])

    def test_an_armed_board_fails_preflight(self):
        rc, out = self._preflight(_FakeSnapshot(rx=True))
        payload = json.loads(out)
        self.assertEqual(rc, 1, payload)
        self.assertFalse(payload['passed'])

    def test_every_board_condition_is_checked(self):
        for snapshot in (_FakeSnapshot(io_ok=False), _FakeSnapshot(online=False),
                         _FakeSnapshot(stale=True)):
            with self.subTest(snapshot=snapshot.to_dict()):
                rc, out = self._preflight(snapshot)
                payload = json.loads(out)
                self.assertEqual(rc, 1, payload)
                self.assertFalse(payload['passed'])

    def test_without_a_token_the_scope_is_pluto_only(self):
        rc, out = self._preflight(with_token=False)
        payload = json.loads(out)
        self.assertEqual(rc, 0, payload)
        self.assertTrue(payload['passed'], payload)
        self.assertEqual(payload['scope'], 'pluto_only')
        self.assertFalse(payload['board_checked'])
        self.assertIsNone(payload['board_gates'])

    def test_pluto_safe_off_failure_fails_preflight(self):
        rc, out = self._preflight(pluto_error=RuntimeError('no Pluto'))
        payload = json.loads(out)
        self.assertEqual(rc, 1, payload)
        self.assertFalse(payload['passed'])
        self.assertIn('no Pluto', payload['error'])

    def test_a_wrong_pluto_readback_fails_preflight(self):
        rc, out = self._preflight(pluto_result={
            'uri': 'usb:offline', 'serial': 'x',
            'hardwaregain_db': -60.0, 'tx_powerdown': '0'})
        payload = json.loads(out)
        self.assertEqual(rc, 1, payload)
        self.assertFalse(payload['passed'])


class ClippingEvidenceTests(unittest.TestCase):
    """task-5: the A-direction tone analysis must carry Stage 0 clipping proof.

    The five fields and the near-full-scale constant are the ones
    planner.analyse_stage0_window already uses; these tests pin that the tone
    path reports the exact same numbers for the same IQ.
    """

    FS = 2400000.0
    OFFSET = 100000.0

    def _tone(self, amplitude=0.2, samples=262144):
        t = np.arange(samples, dtype=float) / self.FS
        return (amplitude * np.exp(2j * np.pi * self.OFFSET * t)).astype(np.complex64)

    def test_tone_analysis_carries_the_stage0_clipping_fields(self):
        z = self._tone()
        tone = planner.analyse_tone(z, self.FS, expected_offset_hz=self.OFFSET)
        stage0 = planner.analyse_stage0_window(z, self.FS, self.OFFSET)
        for key in ('abs_max_iq', 'full_scale_fraction', 'hard_clip_fraction',
                    'full_scale_headroom_db', 'clipped', 'near_full_scale'):
            self.assertIn(key, tone, key)
            self.assertEqual(tone[key], stage0[key], key)
        self.assertFalse(tone['clipped'])
        self.assertEqual(tone['full_scale_fraction'], 0.0)

    def test_a_near_full_scale_window_is_clipped(self):
        z = np.full(262144, 1.05 + 0.0j, dtype=np.complex64)
        tone = planner.analyse_tone(z, self.FS, expected_offset_hz=0.0)
        self.assertTrue(tone['clipped'])
        self.assertEqual(tone['hard_clip_fraction'], 1.0)
        self.assertEqual(tone['full_scale_fraction'], 1.0)
        self.assertAlmostEqual(tone['abs_max_iq'], 1.05, places=6)
        self.assertLess(tone['full_scale_headroom_db'], 0.0)

    def test_a_normal_window_is_not_flagged_and_the_boundary_is_unchanged(self):
        tone = planner.analyse_tone(self._tone(0.2), self.FS,
                                    expected_offset_hz=self.OFFSET)
        self.assertFalse(tone['clipped'])
        self.assertGreater(tone['full_scale_headroom_db'], 10.0)
        # 0.995 trips the unchanged near-full-scale fraction (0.99) but is not
        # a hard clip, exactly like Stage 0: no threshold was relaxed.
        boundary = np.full(262144, 0.995 + 0.0j, dtype=np.complex64)
        near = planner.analyse_tone(boundary, self.FS, expected_offset_hz=0.0)
        self.assertFalse(near['clipped'])
        self.assertEqual(near['full_scale_fraction'], 1.0)
        self.assertEqual(near['hard_clip_fraction'], 0.0)


class DirectionAClippingTests(unittest.TestCase):
    """Any clipped Pluto RX window must fail and stop the direction-A session."""

    @staticmethod
    def _analysis(peak_hz, peak_to_median_db, clipped=False):
        return {'peak_offset_hz': float(peak_hz),
                'peak_to_median_db': float(peak_to_median_db),
                'abs_max_iq': 1.05 if clipped else 0.1,
                'full_scale_fraction': 1.0 if clipped else 0.0,
                'hard_clip_fraction': 0.0,
                'full_scale_headroom_db': -0.4 if clipped else 20.0,
                'near_full_scale': 0.99,
                'clipped': bool(clipped)}

    def _report(self, clipped=()):
        return {'windows': [
            {'tag': 'OFF_PRE',
             'analysis': self._analysis(0.0, 5.0, 'OFF_PRE' in clipped)},
            {'tag': 'TONE1',
             'analysis': self._analysis(102539.0625, 30.0, 'TONE1' in clipped)},
            {'tag': 'TONE2',
             'analysis': self._analysis(205078.125, 30.0, 'TONE2' in clipped)},
            {'tag': 'OFF_POST',
             'analysis': self._analysis(0.0, 5.0, 'OFF_POST' in clipped)},
        ]}

    def test_a_clean_report_still_passes_the_existing_gate(self):
        import board_integration
        self.assertTrue(board_integration._evaluate_a(self._report()))

    def test_any_clipped_window_fails_the_direction_a_session(self):
        import board_integration
        for tag in ('OFF_PRE', 'TONE1', 'TONE2', 'OFF_POST'):
            with self.subTest(tag=tag):
                self.assertFalse(
                    board_integration._evaluate_a(self._report((tag,))),
                    'a clipped %s window must fail the session' % tag)

    def _capture(self, iq):
        import board_integration
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        tmp = Path(tempfile.mkdtemp())
        np.save(str(tmp / 'iq.npy'), iq)
        result = {'result_dir': str(tmp)}
        original_run = board_integration._run_radio
        original_post = board_integration._check_pluto_post
        board_integration._run_radio = lambda *a, **k: result
        board_integration._check_pluto_post = lambda *a, **k: {'tx_powerdown': '1'}
        try:
            return board_integration._capture_pluto_rx(
                cfg, Path('unused-radioconda'), tmp, 'TONE1',
                expected_offset_hz=102539.0625)
        finally:
            board_integration._run_radio = original_run
            board_integration._check_pluto_post = original_post

    def test_a_clipped_capture_raises_instead_of_returning_a_window(self):
        import board_integration
        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._capture(np.full(262144, 1.05 + 0.0j, dtype=np.complex64))
        self.assertIn('CLIPPED', str(ctx.exception))

    def test_a_clean_capture_returns_the_clipping_evidence(self):
        t = np.arange(262144, dtype=float) / 2400000.0
        z = (0.2 * np.exp(2j * np.pi * 102539.0625 * t)).astype(np.complex64)
        window = self._capture(z)
        for key in ('abs_max_iq', 'full_scale_fraction', 'hard_clip_fraction',
                    'full_scale_headroom_db', 'clipped'):
            self.assertIn(key, window['analysis'], key)
        self.assertFalse(window['analysis']['clipped'])


class _FakeBoardStatus(object):
    def __init__(self, tx_selected=True):
        self.armed = True
        self.online = True
        self.stale = False
        self.error = None
        self.release_id = 'offline-test-release'
        self.pl_status = types.SimpleNamespace(
            io_ok=True,
            tx=types.SimpleNamespace(source_selected=tx_selected,
                                     length_words=None, position_words=None),
            rx=types.SimpleNamespace(armed=False))

    def to_dict(self):
        return {'online': True, 'stale': False, 'error': None,
                'armed': True, 'release_id': self.release_id,
                'control_ops': [],
                'pl_status': {
                    'io_ok': True,
                    'tx': {'source_selected': self.pl_status.tx.source_selected},
                    'rx': {'armed': self.pl_status.rx.armed}}}


class _FakeBoardClient(object):
    """Offline stand-in for RfStatusClient; records every operation."""

    def __init__(self, port=None):
        self.port = port
        self.calls = []
        self.configures = []
        self.closed = False
        self.tx_selected = False

    def poll(self):
        return _FakeBoardStatus(self.tx_selected)

    def control_arm(self, token):
        self.calls.append('arm')

    def control_disarm(self):
        self.calls.append('disarm')

    def control_op(self, op, payload=None):
        self.calls.append(op)
        if op == 'tx.start':
            self.tx_selected = True
        elif op == 'tx.stop':
            self.tx_selected = False
        if op == 'rf.configure':
            attenuation = (payload or {}).get('attenuation_mdB')
            self.configures.append(attenuation)
            return {'attenuation_mdB': attenuation, 'hardware_verified': True}
        return {}

    def close(self):
        self.closed = True
        self.calls.append('close')


class DirectionAFailureCleanupTests(unittest.TestCase):
    """task-7: a session failure after tx.start must auto safe-stop the board."""

    def _window(self, tag, peak_hz, ratio=30.0, clipped=False):
        return {'tag': tag, 'analysis': {
            'peak_offset_hz': peak_hz, 'peak_to_median_db': ratio,
            'clipped': clipped, 'abs_max_iq': 0.2,
            'full_scale_fraction': 0.0, 'hard_clip_fraction': 0.0,
            'full_scale_headroom_db': 14.0, 'near_full_scale': 0.99}}

    def _clean_windows(self):
        return [self._window('OFF_PRE', 0.0, 5.0),
                self._window('TONE1', 102539.0625),
                self._window('TONE2', 205078.125),
                self._window('OFF_POST', 0.0, 5.0)]

    def _args(self):
        tmp = Path(tempfile.mkdtemp())
        token = tmp / 'token'
        token.write_text('offline-token', encoding='utf-8')
        out = tmp / 'out'
        out.mkdir()
        return types.SimpleNamespace(
            client_dir=tmp, token_file=token, board_port=5000,
            radioconda=Path('unused-radioconda')), out

    @contextlib.contextmanager
    def _patched(self, captures, safe_stop=None):
        import board_integration
        created = []
        state = {'index': 0, 'tags': []}

        class _RecordingClient(_FakeBoardClient):
            def __init__(self, port=None):
                super().__init__(port)
                created.append(self)

        def fake_capture(cfg, radioconda, out_dir, tag, expected_offset_hz=None):
            state['tags'].append(tag)
            item = captures[state['index']]
            state['index'] += 1
            if isinstance(item, BaseException):
                raise item
            return item

        names = ('_load_board_client', '_pluto_safe_off', '_board_preflight',
                 '_configure_and_check', '_load_tone', '_capture_pluto_rx',
                 '_final_safe_stop')
        originals = {name: getattr(board_integration, name) for name in names}
        board_integration._load_board_client = lambda client_dir: (
            _RecordingClient, RuntimeError)
        board_integration._pluto_safe_off = lambda serial: {
            'tx_powerdown': '1', 'hardwaregain_db': -89.75}
        board_integration._board_preflight = lambda client, token, cfg: None
        board_integration._configure_and_check = (
            lambda client, cfg, attenuation_mdb=None: {})
        board_integration._load_tone = lambda client, cfg, bin_index: {}
        board_integration._capture_pluto_rx = fake_capture
        if safe_stop is not None:
            board_integration._final_safe_stop = safe_stop
        try:
            yield created, state
        finally:
            for name, fn in originals.items():
                setattr(board_integration, name, fn)

    def test_a_failure_after_tx_start_auto_stops_the_board(self):
        import board_integration
        args, out = self._args()
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        failing = board_integration.ExecutionError(
            'Pluto RX TONE1 window is CLIPPED (test injection)')
        with self._patched([self._clean_windows()[0], failing]) as (created, state):
            with self.assertRaises(board_integration.ExecutionError) as ctx:
                board_integration._run_a(args, cfg, out)
        client = created[0]
        # The original exception is re-raised unchanged.
        self.assertIn('CLIPPED', str(ctx.exception))
        cleanup = getattr(ctx.exception, 'cleanup_report', None)
        self.assertIsInstance(cleanup, dict)
        self.assertFalse(cleanup['cleanup_required'])
        self.assertEqual(cleanup['cleanup']['attenuation_mdb'], 89750)
        self.assertFalse(cleanup['cleanup']['tx_source_selected'])
        # The existing safe-stop primitive ran: tx.stop + configure 89750 + disarm.
        self.assertIn('tx.stop', client.calls)
        self.assertIn(89750, client.configures)
        self.assertIn('disarm', client.calls)
        # No retry, no second capture after the failure.
        self.assertEqual(state['tags'], ['OFF_PRE', 'TONE1'])
        sidecar = json.loads((out / 'failure-cleanup.json').read_text(encoding='utf-8'))
        self.assertFalse(sidecar['cleanup_required'])
        self.assertIn('CLIPPED', sidecar['original_error'])

    def test_a_failed_safe_stop_is_reported_and_the_original_error_survives(self):
        import board_integration
        args, out = self._args()
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')

        def broken_safe_stop(client, cfg, token):
            raise RuntimeError('injected cleanup failure')

        failing = board_integration.ExecutionError('capture boom')
        with self._patched([self._clean_windows()[0], failing],
                           safe_stop=broken_safe_stop):
            with self.assertRaises(board_integration.ExecutionError) as ctx:
                board_integration._run_a(args, cfg, out)
        self.assertIn('capture boom', str(ctx.exception))
        cleanup = getattr(ctx.exception, 'cleanup_report', None)
        self.assertIsInstance(cleanup, dict)
        self.assertTrue(cleanup['cleanup_required'])
        self.assertIn('injected cleanup failure', cleanup['cleanup_error'])
        self.assertIn('capture boom', cleanup['original_error'])
        sidecar = json.loads((out / 'failure-cleanup.json').read_text(encoding='utf-8'))
        self.assertTrue(sidecar['cleanup_required'])
        self.assertIn('disconnect', sidecar['cleanup_note'])

    def test_the_normal_path_is_unchanged(self):
        import board_integration
        args, out = self._args()
        cfg = planner.load_config(ROOT / 'config' / 'diagnostic.json')
        windows = self._clean_windows()
        with self._patched(list(windows)) as (created, state):
            report = board_integration._run_a(args, cfg, out)
        client = created[0]
        self.assertTrue(report['passed'])
        self.assertEqual(state['tags'],
                         ['OFF_PRE', 'TONE1', 'TONE2', 'OFF_POST'])
        self.assertFalse((out / 'failure-cleanup.json').exists())
        # Exactly the normal final safe stop (89750), no failure-path extra.
        self.assertEqual(client.configures.count(89750), 1)


class _CapabilityStatus(object):
    def __init__(self, armed, tx_selected=False, online=True, error=None):
        self.armed = armed
        self.online = online
        self.stale = False
        self.error = error
        self.pl_status = types.SimpleNamespace(
            io_ok=bool(online),
            tx=types.SimpleNamespace(source_selected=tx_selected,
                                     length_words=0, position_words=0),
            rx=types.SimpleNamespace(armed=False))

    def to_dict(self):
        return {'online': self.online,
                'stale': self.stale,
                'error': self.error,
                'armed': self.armed,
                'control_ops': ([] if self.armed else ['control.arm']),
                'pl_status': {
                    'io_ok': self.pl_status.io_ok,
                    'tx': {'source_selected': self.pl_status.tx.source_selected},
                    'rx': {'armed': False}}}


class _CapabilityBoardClient(object):
    """Fake daemon advertising `control.arm` ONLY while unarmed.

    This mirrors the real rf_daemon.c rule; the client refuses to send an
    operation that is not in the latest snapshot's control_ops.
    """

    def __init__(self, port=None, armed=False):
        self.port = port
        self.armed = bool(armed)
        self.tx_selected = False
        self.calls = []
        self.configures = []
        self.refuse_configure = False

    def _ops(self):
        if self.armed:
            return ('control.disarm', 'tx.stop', 'rf.configure', 'tx.load',
                    'tx.start', 'rf.init')
        return ('control.arm',)

    def _refuse(self, op):
        raise RuntimeError('the RF daemon did not advertise %s, so it was not sent'
                           % op)

    def poll(self):
        return _CapabilityStatus(self.armed, self.tx_selected)

    def control_arm(self, token):
        self.calls.append('arm')
        if 'control.arm' not in self._ops():
            self._refuse('control.arm')
        self.armed = True

    def control_disarm(self):
        self.calls.append('disarm')
        if 'control.disarm' not in self._ops():
            self._refuse('control.disarm')
        self.armed = False
        return {'armed': False}

    def control_op(self, op, payload=None):
        self.calls.append(op)
        if op not in self._ops():
            self._refuse(op)
        if op == 'tx.start':
            self.tx_selected = True
        elif op == 'tx.stop':
            self.tx_selected = False
        elif op == 'rf.configure':
            if self.refuse_configure:
                raise RuntimeError('RF daemon refused rf.configure')
            attenuation = (payload or {}).get('attenuation_mdB')
            self.configures.append(attenuation)
            return {'lo_hz': 433900000,
                    'sample_rate_hz': 60000000,
                    'rx_bandwidth_hz': 30000000,
                    'tx_bandwidth_hz': 30000000,
                    'attenuation_mb': attenuation,
                    'attenuation_mdB': attenuation,
                    'phy_mode_1r1t': 0,
                    'product_id': 0x08,
                    'hardware_verified': True}
        return {}

    def close(self):
        pass


class _FlakyFinalPollClient(_CapabilityBoardClient):
    """Fake daemon whose first poll(s) after disarm time out."""

    def __init__(self, armed=False, failures_after_disarm=1):
        super().__init__(port=None, armed=armed)
        self.failures_after_disarm = int(failures_after_disarm)
        self.disarmed_once = False
        self.final_poll_attempts = 0

    def poll(self):
        if self.disarmed_once:
            self.final_poll_attempts += 1
            if self.final_poll_attempts <= self.failures_after_disarm:
                return _CapabilityStatus(self.armed, self.tx_selected,
                                         online=False, error='timed out')
        return super().poll()

    def control_disarm(self):
        super().control_disarm()
        self.disarmed_once = True


class ArmIfNeededTests(unittest.TestCase):
    """task-9: never re-arm a daemon that is already armed."""

    def _cfg(self):
        return planner.load_config(ROOT / 'config' / 'diagnostic.json')

    def test_stop_tx_and_configure_does_not_arm_an_already_armed_daemon(self):
        import board_integration
        client = _CapabilityBoardClient(armed=True)
        client.tx_selected = True
        board_integration._stop_tx_and_configure(client, self._cfg(), 'tok')
        self.assertNotIn('arm', client.calls)
        self.assertEqual(client.configures, [46000])

    def test_final_safe_stop_does_not_arm_an_already_armed_daemon(self):
        import board_integration
        client = _CapabilityBoardClient(armed=True)
        client.tx_selected = True
        state = board_integration._final_safe_stop(client, self._cfg(), 'tok')
        self.assertNotIn('arm', client.calls)
        self.assertEqual(client.configures, [89750])
        self.assertFalse(state['tx_source_selected'])
        self.assertIn('disarm', client.calls)
        self.assertFalse(client.armed)

    def test_final_safe_stop_arms_only_when_unarmed(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)
        state = board_integration._final_safe_stop(client, self._cfg(), 'tok')
        self.assertEqual(client.calls.count('arm'), 1)
        self.assertEqual(client.configures, [89750])
        self.assertFalse(state['tx_source_selected'])
        self.assertFalse(client.armed)

    # ---- failure cleanup in both armed states ----
    def _run_a_harness(self, client, preflight, capture):
        import board_integration
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'token').write_text('offline-token', encoding='utf-8')
        out = tmp / 'out'
        out.mkdir()
        args = types.SimpleNamespace(client_dir=tmp, token_file=tmp / 'token',
                                     board_port=5000,
                                     radioconda=Path('unused-radioconda'))
        names = ('_load_board_client', '_pluto_safe_off', '_board_preflight',
                 '_configure_and_check', '_load_tone', '_capture_pluto_rx')
        originals = {name: getattr(board_integration, name) for name in names}
        board_integration._load_board_client = lambda client_dir: (
            lambda port=None: client, RuntimeError)
        board_integration._pluto_safe_off = lambda serial: {
            'tx_powerdown': '1', 'hardwaregain_db': -89.75}
        board_integration._board_preflight = preflight
        board_integration._configure_and_check = (
            lambda c, cfg, attenuation_mdb=None: {})
        board_integration._load_tone = lambda c, cfg, bin_index: {}
        board_integration._capture_pluto_rx = capture
        try:
            return board_integration._run_a(args, self._cfg(), out), out
        finally:
            for name, fn in originals.items():
                setattr(board_integration, name, fn)

    def _window(self, tag):
        return {'tag': tag, 'analysis': {
            'peak_offset_hz': 102539.0625 if tag == 'TONE1' else 0.0,
            'peak_to_median_db': 30.0, 'clipped': False,
            'abs_max_iq': 0.1, 'full_scale_fraction': 0.0,
            'hard_clip_fraction': 0.0, 'full_scale_headroom_db': 20.0,
            'near_full_scale': 0.99}}

    def test_failure_cleanup_skips_a_redundant_arm_and_reaches_89750(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)

        def preflight(c, cfg, token):
            c.control_arm(token)  # _board_preflight normally arms first

        def capture(cfg, radioconda, out_dir, tag, expected_offset_hz=None):
            if tag == 'OFF_PRE':
                return self._window('OFF_PRE')
            raise board_integration.ExecutionError(
                'Pluto RX TONE1 window is CLIPPED (test)')

        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._run_a_harness(client, preflight, capture)
        cleanup = ctx.exception.cleanup_report
        self.assertFalse(cleanup['cleanup_required'])
        self.assertEqual(client.calls.count('arm'), 1)  # only the preflight arm
        self.assertIn(89750, client.configures)
        self.assertFalse(client.armed)

    def test_failure_cleanup_arms_a_not_yet_armed_daemon(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)

        def preflight(c, cfg, token):
            raise ValueError('injected preflight failure before any arm')

        with self.assertRaises(ValueError) as ctx:
            self._run_a_harness(client, preflight,
                                lambda *a, **k: self._window('X'))
        cleanup = ctx.exception.cleanup_report
        self.assertFalse(cleanup['cleanup_required'])
        self.assertIn('arm', client.calls)
        self.assertIn(89750, client.configures)
        self.assertFalse(client.armed)

    def test_failure_cleanup_reports_unknown_when_configure_is_refused(self):
        import board_integration
        client = _CapabilityBoardClient(armed=True)
        client.refuse_configure = True

        def preflight(c, cfg, token):
            pass  # already armed in this scenario

        def capture(cfg, radioconda, out_dir, tag, expected_offset_hz=None):
            raise board_integration.ExecutionError('capture boom')

        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._run_a_harness(client, preflight, capture)
        cleanup = ctx.exception.cleanup_report
        self.assertTrue(cleanup['cleanup_required'])
        self.assertIn('refused rf.configure', cleanup['cleanup_error'])
        self.assertIn('capture boom', cleanup['original_error'])


BIN_HZ = 60000000.0 / 4096.0
FFT_BINS = 4096
FFT_SHIFT = FFT_BINS // 2


def _b_spectrum(peak_index, peak_db=-40.0, floor_db=-100.0):
    spectrum = [floor_db] * FFT_BINS
    spectrum[peak_index] = peak_db
    return spectrum


def _b_frame(tag, shift, peak_db=-40.0, floor_db=-100.0,
             clipped=False):
    """A frame faithful to _capture_board_rx: fftshift puts DC at N/2.

    ``shift`` is the signed bin offset from DC, so the stored peak index is
    N/2 + shift and the stored peak offset is shift * bin_hz.
    """
    index = FFT_SHIFT + shift
    return {
        'tag': tag,
        'spectrum_db': _b_spectrum(index, peak_db, floor_db),
        'bin_hz': BIN_HZ,
        'peak_bin_index': index,
        'peak_offset_hz': (index - FFT_SHIFT) * BIN_HZ,
        'peak_to_median_db': 40.0,
        'clipped': clipped,
        'clipped_samples': 0,
        'board_tx_off_before': {'tx_source_selected': False, 'io_ok': True},
        'board_tx_off_after': {'tx_source_selected': False, 'io_ok': True},
    }


def _b_report():
    # Faithful to the real 2026-10-20 direction-B frames: the board RX path
    # inverts the spectrum, so +100 kHz appears at -102.539 kHz (bin 2041) and
    # +200 kHz at -205.078 kHz (bin 2034).
    tone1 = _b_frame('TONE1', -7)
    tone1['expected_offset_hz'] = 100000.0
    tone2 = _b_frame('TONE2', -14)
    tone2['expected_offset_hz'] = 200000.0
    return {
        'direction': 'B',
        'windows': [
            _b_frame('OFF_PRE', 0),
            tone1,
            tone2,
            _b_frame('OFF_POST', 0),
            {'tag': 'TONE1', 'pluto_tx': {'config': {'duration_s': 2.0}}},
            {'tag': 'TONE2', 'pluto_tx': {'config': {'duration_s': 2.0}}},
        ],
        'budget': {'sessions': 1, 'board_inits': 1, 'board_configures': 1,
                   'board_captures': 4, 'pluto_tx_starts': 2,
                   'board_tx_starts': 0, 'pluto_tx_seconds_total': 4.0},
        'pluto_final_readback': {'hardwaregain_db': -89.75,
                                 'hardwaregain_raw': '-89.750000 dB',
                                 'tx_powerdown': '1',
                                 'tx_powerdown_raw': '1'},
        'final_status': {'online': True, 'stale': False, 'error': None,
                         'armed': False,
                         'pl_status': {
                             'io_ok': True,
                             'tx': {'source_selected': False},
                             'rx': {'armed': False}}},
        'final_status_unknown': False,
    }


class DirectionBEvaluateTests(unittest.TestCase):
    """task-10/13: criterion from the board IQ, fftshift-faithful fixtures.

    The real board RX path inverts the spectrum (task-13); the spacing gate is
    therefore on the magnitude while the signed delta is kept as evidence.
    """

    def test_a_clean_board_iq_passes_with_spectral_inversion(self):
        import board_integration
        report = _b_report()
        self.assertTrue(board_integration._evaluate_b(report))
        self.assertFalse(report.get('problems') or [])
        tone1 = report['windows'][1]
        self.assertTrue(tone1['board_rx_spectral_inversion_observed'])
        self.assertAlmostEqual(tone1['on_peak_delta_signed_hz'], -102539.0625)
        self.assertAlmostEqual(tone1['on_peak_delta_hz'], 102539.0625)
        self.assertAlmostEqual(tone1['on_peak_delta_bins'], 7.0)
        self.assertTrue(report['board_rx_spectral_inversion_observed'])

    def test_on_off_same_bin_below_10_db_fails_even_if_peak_ratio_is_high(self):
        import board_integration
        report = _b_report()
        tone1 = _b_frame('TONE1', -7, peak_db=-85.0)
        tone1['expected_offset_hz'] = 100000.0
        report['windows'][1] = tone1
        # OFF carries -90 dB in the same (fftshifted) bin: peak/median would
        # still be >10 dB, but the same-bin ON/OFF contrast is only 5 dB.
        for i in (0, 3):
            report['windows'][i]['spectrum_db'][FFT_SHIFT - 7] = -90.0
        self.assertFalse(board_integration._evaluate_b(report))
        self.assertIn('same-bin', '; '.join(report['problems']))

    def test_peak_more_than_one_bin_from_the_nominal_tone_fails(self):
        import board_integration
        report = _b_report()
        tone1 = _b_frame('TONE1', -9)  # 2 bins beyond round(100 kHz)
        tone1['expected_offset_hz'] = 100000.0
        report['windows'][1] = tone1
        self.assertFalse(board_integration._evaluate_b(report))
        self.assertIn('expected', '; '.join(report['problems']))

    def test_two_on_peaks_must_be_seven_bins_apart_within_one_bin(self):
        import board_integration
        report = _b_report()
        report['windows'][2] = _b_frame('TONE2', -16)  # 9 bins apart (2 too many)
        report['windows'][1].pop('expected_offset_hz', None)
        report['windows'][2].pop('expected_offset_hz', None)
        self.assertFalse(board_integration._evaluate_b(report))
        self.assertIn('two ON peaks', '; '.join(report['problems']))
        report = _b_report()
        report['windows'][2] = _b_frame('TONE2', -15)  # 8 bins apart (allowed)
        report['windows'][1].pop('expected_offset_hz', None)
        report['windows'][2].pop('expected_offset_hz', None)
        self.assertTrue(board_integration._evaluate_b(report))

    def test_magnitude_off_by_two_bins_fails(self):
        import board_integration
        report = _b_report()
        report['windows'][1] = _b_frame('TONE1', -5)   # |-5 - (-14)| = 9 bins
        report['windows'][1].pop('expected_offset_hz', None)
        report['windows'][2].pop('expected_offset_hz', None)
        self.assertFalse(board_integration._evaluate_b(report))
        self.assertIn('two ON peaks', '; '.join(report['problems']))

    def test_any_clipped_frame_fails(self):
        import board_integration
        report = _b_report()
        report['windows'][2]['clipped'] = True
        report['windows'][2]['clipped_samples'] = 7
        self.assertFalse(board_integration._evaluate_b(report))

    def test_board_tx_must_stay_off_in_every_window(self):
        import board_integration
        report = _b_report()
        report['windows'][0]['board_tx_off_after']['tx_source_selected'] = True
        self.assertFalse(board_integration._evaluate_b(report))
        report = _b_report()
        report['final_status']['pl_status']['tx']['source_selected'] = True
        self.assertFalse(board_integration._evaluate_b(report))

    def test_unknown_final_status_is_named_not_misreported_as_tx_on(self):
        import board_integration
        report = _b_report()
        report['final_status'] = None
        report['final_status_unknown'] = True
        report['final_status_poll_attempts'] = 3
        self.assertFalse(board_integration._evaluate_b(report))
        joined = '; '.join(report['problems'])
        self.assertIn('final board status UNKNOWN', joined)
        self.assertNotIn('final board TX source is not off', joined)
        self.assertNotIn('final board io_ok is not true', joined)

    def test_pluto_final_safe_off_readback_must_be_confirmed(self):
        import board_integration
        report = _b_report()
        report['pluto_final_readback'] = {'hardwaregain_db': -60.0,
                                          'tx_powerdown': '0'}
        self.assertFalse(board_integration._evaluate_b(report))
        report = _b_report()
        del report['pluto_final_readback']
        self.assertFalse(board_integration._evaluate_b(report))

    def test_budget_must_show_one_init_four_captures_two_tx_and_board_tx_zero(self):
        import board_integration
        for bad in ({'board_inits': 2}, {'board_captures': 3},
                    {'pluto_tx_starts': 3}, {'board_tx_starts': 1},
                    {'pluto_tx_seconds_total': 4.5}):
            report = _b_report()
            report['budget'].update(bad)
            self.assertFalse(board_integration._evaluate_b(report), bad)


class _FakePlutoProc(object):
    def __init__(self):
        self.calls = []
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.calls.append('wait')
        self.returncode = 0
        return 0

    def terminate(self):
        self.calls.append('terminate')

    def kill(self):
        self.calls.append('kill')
        self.returncode = -9

    def communicate(self, timeout=None):
        self.calls.append('communicate')
        raise subprocess.TimeoutExpired('pluto-tx', timeout)


class _GoodPlutoProc(_FakePlutoProc):
    def __init__(self, tx_result):
        super().__init__()
        self.tx_result = tx_result

    def communicate(self, timeout=None):
        self.calls.append('communicate')
        self.returncode = 0
        return (json.dumps(self.tx_result), '')


class _NonZeroPlutoProc(_FakePlutoProc):
    def communicate(self, timeout=None):
        self.calls.append('communicate')
        self.returncode = 3
        return ('', 'injected Pluto failure')


class DirectionBHardeningTests(unittest.TestCase):
    """task-10 gaps 1/2/3/5: Pluto TX child lifecycle, independent readback,
    budget enforcement and board TX stay-off."""

    def _cfg(self):
        return planner.load_config(ROOT / 'config' / 'diagnostic.json')

    def _tx_result(self, duration=2.0):
        return {'mode': 'tx', 'status': 'TX_STREAM_COMPLETED_RF_UNVERIFIED',
                'config': {'duration_s': duration},
                'tx_powerdown_readback': '1',
                'after': {'channels': {
                    'altvoltage1_out': {'powerdown': '1'},
                    'voltage0_out': {'hardwaregain': '-89.750000 dB'}}}}

    def _capture_factory(self, frames=None, fail_on=None):
        def capture(client, out_dir, tag, expected_offset_hz=None):
            if tag == fail_on:
                raise Exception('injected capture failure on %s' % tag)
            if frames is not None:
                return dict(frames[tag])
            peak = -7 if tag == 'TONE1' else (-14 if tag == 'TONE2' else 0)
            frame = _b_frame(tag, peak)
            frame['expected_offset_hz'] = expected_offset_hz
            return frame
        return capture

    def _harness(self, client, procs, capture, safe_off=None):
        import board_integration
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'token').write_text('offline-token', encoding='utf-8')
        out = tmp / 'out'
        out.mkdir()
        args = types.SimpleNamespace(
            client_dir=tmp, token_file=tmp / 'token', board_port=5000,
            radioconda=Path('unused-radioconda'), path_confirmed='offline',
            sessions=1, extended=False)
        calls = {'radio': 0, 'safe_off': 0}
        procs = list(procs)

        def radio(mode, config_path, radioconda, path_confirmed=None,
                  ready_file=None, background=False):
            index = calls['radio']
            calls['radio'] += 1
            proc = procs[index]
            if getattr(proc, 'write_ready', True) and ready_file is not None:
                Path(ready_file).write_text('STREAM_STARTED')
            return proc

        def default_safe_off(serial):
            calls['safe_off'] += 1
            return {'hardwaregain_db': -89.75,
                    'hardwaregain_raw': '-89.750000 dB',
                    'tx_powerdown': '1', 'tx_powerdown_raw': '1'}

        names = ('_load_board_client', '_pluto_safe_off', '_board_preflight',
                 '_configure_and_check', '_run_radio', '_capture_board_rx')
        originals = {name: getattr(board_integration, name) for name in names}
        board_integration._load_board_client = lambda d: (
            lambda port=None: client, RuntimeError)
        board_integration._pluto_safe_off = safe_off or default_safe_off
        board_integration._board_preflight = (
            lambda c, token, cfg: c.control_arm(token))
        board_integration._configure_and_check = (
            lambda c, cfg, attenuation_mdb=None: {})
        board_integration._run_radio = radio
        board_integration._capture_board_rx = capture
        try:
            return board_integration._run_b(args, self._cfg(), out), out, calls
        finally:
            self._last_calls = calls
            for name, fn in originals.items():
                setattr(board_integration, name, fn)

    def test_a_capture_failure_stops_pluto_and_reads_back_independently(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)
        proc = _GoodPlutoProc(self._tx_result())
        with self.assertRaises(Exception) as ctx:
            self._harness(client, [proc], self._capture_factory(fail_on='TONE1'))
        exc = ctx.exception
        self.assertIn('injected capture failure', str(exc))
        cleanup = getattr(exc, 'cleanup_report', None)
        self.assertIsInstance(cleanup, dict)
        self.assertFalse(cleanup['cleanup_required'])
        self.assertIn('terminate', proc.calls)
        self.assertTrue(cleanup['pluto_tx']['confirmed'])
        self.assertEqual(cleanup['board']['tx_source_selected'], False)
        self.assertFalse(client.armed)
        # No second TONE: the session stopped instead of continuing.
        self.assertEqual(self._last_calls['radio'], 1)

    def test_ready_timeout_stops_the_child_then_reads_back(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)
        proc = _GoodPlutoProc(self._tx_result())
        proc.write_ready = False
        old = getattr(board_integration, 'B_PLUTO_READY_TIMEOUT_S', 30.0)
        board_integration.B_PLUTO_READY_TIMEOUT_S = 0.05
        try:
            with self.assertRaises(Exception) as ctx:
                self._harness(client, [proc], self._capture_factory())
        finally:
            board_integration.B_PLUTO_READY_TIMEOUT_S = old
        self.assertIn('STREAM_STARTED', str(ctx.exception))
        cleanup = ctx.exception.cleanup_report
        self.assertFalse(cleanup['cleanup_required'])
        self.assertIn('terminate', proc.calls)

    def test_communicate_timeout_stops_the_child_then_reads_back(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)
        proc = _FakePlutoProc()
        with self.assertRaises(Exception) as ctx:
            self._harness(client, [proc, _GoodPlutoProc(self._tx_result())],
                          self._capture_factory())
        self.assertIn('did not exit', str(ctx.exception))
        cleanup = ctx.exception.cleanup_report
        self.assertFalse(cleanup['cleanup_required'])
        self.assertIn('terminate', proc.calls)

    def test_nonzero_exit_is_a_failure_and_still_stops_and_reads_back(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)
        proc = _NonZeroPlutoProc()
        with self.assertRaises(Exception) as ctx:
            self._harness(client, [proc, _GoodPlutoProc(self._tx_result())],
                          self._capture_factory())
        self.assertIn('Pluto TX', str(ctx.exception))
        cleanup = ctx.exception.cleanup_report
        self.assertFalse(cleanup['cleanup_required'])
        self.assertEqual(cleanup['pluto_readback']['hardwaregain_db'], -89.75)
        self.assertEqual(cleanup['pluto_readback']['tx_powerdown'], '1')

    def test_an_unconfirmed_safe_off_is_reported_as_unknown(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)
        calls = {'n': 0}

        def flaky_safe_off(serial):
            calls['n'] += 1
            if calls['n'] == 1:  # the session preflight readback
                return {'hardwaregain_db': -89.75, 'tx_powerdown': '1'}
            raise RuntimeError('injected libiio readback failure')

        proc = _GoodPlutoProc(self._tx_result())
        with self.assertRaises(Exception) as ctx:
            self._harness(client, [proc], self._capture_factory(fail_on='TONE1'),
                          safe_off=flaky_safe_off)
        cleanup = ctx.exception.cleanup_report
        self.assertTrue(cleanup['cleanup_required'])
        self.assertIn('injected libiio readback failure',
                      cleanup['pluto_tx']['error'])
        self.assertIn('injected capture failure', cleanup['original_error'])

    def test_the_normal_path_tracks_budget_and_reads_back_three_times(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)
        procs = [_GoodPlutoProc(self._tx_result()),
                 _GoodPlutoProc(self._tx_result())]
        report, _out, calls = self._harness(client, procs,
                                            self._capture_factory())
        self.assertTrue(report['passed'])
        self.assertEqual(calls['radio'], 2)
        # session preflight + two normal tone teardowns + one session final
        self.assertEqual(calls['safe_off'], 4)
        self.assertEqual(report['budget']['pluto_tx_seconds_total'], 4.0)
        self.assertEqual(report['budget']['board_captures'], 4)
        self.assertEqual(report['budget']['board_tx_starts'], 0)
        self.assertEqual(report['budget']['board_inits'], 1)
        self.assertEqual(report['budget']['board_configures'], 1)
        self.assertFalse(client.armed)

    def test_budget_overrun_stops_before_the_second_tx(self):
        import board_integration
        client = _CapabilityBoardClient(armed=False)
        procs = [_GoodPlutoProc(self._tx_result(duration=2.5)),
                 _GoodPlutoProc(self._tx_result(duration=2.5))]
        with self.assertRaises(Exception) as ctx:
            self._harness(client, procs, self._capture_factory())
        self.assertIn('BUDGET', str(ctx.exception))
        self.assertEqual(procs[1].calls, [])
        cleanup = ctx.exception.cleanup_report
        self.assertFalse(cleanup['cleanup_required'])

    def test_final_poll_is_retried_after_a_transient_timeout(self):
        import board_integration
        client = _FlakyFinalPollClient(failures_after_disarm=1)
        procs = [_GoodPlutoProc(self._tx_result()),
                 _GoodPlutoProc(self._tx_result())]
        report, _out, _calls = self._harness(client, procs,
                                             self._capture_factory())
        self.assertTrue(report['passed'])
        self.assertFalse(report['final_status_unknown'])
        self.assertGreaterEqual(report['final_status_poll_attempts'], 2)
        self.assertTrue(report['final_status_poll_errors'])
        self.assertTrue(report['final_status']['online'])
        self.assertFalse(report['final_status']['pl_status']['tx']
                         ['source_selected'])

    def test_persistent_final_poll_timeout_is_named_unknown(self):
        import board_integration
        client = _FlakyFinalPollClient(failures_after_disarm=99)
        procs = [_GoodPlutoProc(self._tx_result()),
                 _GoodPlutoProc(self._tx_result())]
        report, _out, _calls = self._harness(client, procs,
                                             self._capture_factory())
        self.assertFalse(report['passed'])
        self.assertTrue(report['final_status_unknown'])
        self.assertEqual(report['final_status_poll_attempts'], 3)
        self.assertEqual(len(report['final_status_poll_errors']), 3)
        joined = '; '.join(report['problems'])
        self.assertIn('final board status UNKNOWN', joined)
        self.assertNotIn('final board TX source is not off', joined)
        self.assertNotIn('final board io_ok is not true', joined)


PLUTO_BPSK_CONFIG = {
    'serial': '<DEVICE_SERIAL>',
    'center_hz': 433900000, 'sample_rate': 6000000, 'bandwidth_hz': 1500000,
    'tone_hz': 100000, 'amplitude': 0.1, 'tx_attenuation_db': 60.0,
    'rx_gain_db': 0.0, 'duration_s': 2.0, 'capture_samples': 262144,
}


def _radio_test_module():
    import importlib.util
    path = ROOT.parent / 'radio_test.py'
    spec = importlib.util.spec_from_file_location('radio_test_offline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bpsk_board_payload(inverted=False, noise=False, seed=1):
    """Build the i16 payload the board RX would publish for a 3 MSym/s BPSK."""
    payload6, meta = planner.bpsk_rect_payload(
        seed, 6000000, 3000000, 0.1, symbols=2032)
    z6 = np.frombuffer(payload6, dtype='<i2').reshape(-1, 2)
    z6 = z6[:, 0].astype(float) + 1j * z6[:, 1].astype(float)
    z60 = np.repeat(z6, 10)
    n = np.arange(len(z60), dtype=float)
    z60 = z60 * np.exp(1j * (2.0 * np.pi * 2700.0 * n / 60000000.0 + 0.6))
    if inverted:
        z60 = np.conj(z60)
    z60 = z60[:4096]
    if noise:
        rng = np.random.default_rng(7)
        z60 = (rng.standard_normal(4096)
               + 1j * rng.standard_normal(4096)) * 10.0
    codes = np.empty(2 * len(z60), dtype=np.int16)
    codes[0::2] = np.clip(np.round(z60.real), -2047, 2047).astype(np.int16)
    codes[1::2] = np.clip(np.round(z60.imag), -2047, 2047).astype(np.int16)
    return codes.astype('<i2').tobytes(), meta


def _b_bpsk_report():
    meta = {'capture_id': 'offline-bpsk', 'samples': 4096,
            'sample_rate_hz': 60000000, 'ratio': 0,
            'pre_io_ok': True, 'post_io_ok': True,
            'post_tx_source_selected': False, 'post_rx_armed': False,
            'clipped_samples': 0}
    return {
        'direction': 'B', 'mode': 'bpsk',
        'capture': meta,
        'demod': {
            'normal': {'reference_sync': True, 'reason': '',
                       'ber': 0.0, 'bit_errors': 0, 'compared_bits': 138},
            'conjugated': {'reference_sync': True, 'reason': '',
                           'ber': 0.0, 'bit_errors': 0, 'compared_bits': 138},
        },
        'budget': {'sessions': 1, 'board_inits': 1, 'board_configures': 1,
                   'board_captures': 1, 'pluto_tx_starts': 1,
                   'board_tx_starts': 0, 'pluto_tx_seconds_total': 1.98},
        'pluto_final_readback': {'hardwaregain_db': -89.75,
                                 'tx_powerdown': '1'},
        'pluto_tx_summary': {
            'status': 'TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED',
            'sample_rate_hz': 6000000, 'symbol_rate_hz': 3000000,
            'samples_per_symbol': 2, 'prbs7_seed': 1, 'shaping': 'RECT',
            'differential_encoding': False, 'configured_duration_s': 2.0,
            'stream_started_utc': '2026-10-20T12:22:05+00:00',
            'stream_stopped_utc': '2026-10-20T12:22:07+00:00',
            'stream_end_requested_utc': '2026-10-20T12:22:07+00:00',
            'stream_stopped_confirmed_utc': '2026-10-20T12:22:07+00:00',
            'stream_stop_confirmed': True,
            'stream_seconds': 1.98},
        'final_status': {'online': True, 'stale': False, 'armed': False,
                         'pl_status': {'io_ok': True,
                                       'tx': {'source_selected': False},
                                       'rx': {'armed': False}}},
        'final_status_unknown': False,
        'windows': [],
    }


class BpskPlaybackTests(unittest.TestCase):
    """task-14: gated Pluto TX BPSK playback path (offline, no GNU Radio)."""

    def test_frozen_payload_parameters(self):
        mod = _radio_test_module()
        payload, meta = mod.bpsk_tx_payload(dict(PLUTO_BPSK_CONFIG))
        self.assertEqual(meta['sample_rate_hz'], 6000000)
        self.assertEqual(meta['symbol_rate_hz'], 3000000)
        self.assertEqual(meta['samples_per_symbol'], 2)
        self.assertEqual(meta['prbs7_seed'], 1)
        self.assertEqual(meta['shaping'], 'RECT')
        self.assertFalse(meta['differential_encoding'])
        self.assertEqual(meta['samples'], 4064)
        self.assertEqual(meta['bytes'], 4064 * 4)
        self.assertAlmostEqual(max(abs(v) for v in payload), 0.1, places=2)

    def test_seed_gate_only_accepts_frozen_triplet(self):
        mod = _radio_test_module()
        payload1, meta1 = mod.bpsk_tx_payload(dict(PLUTO_BPSK_CONFIG))
        self.assertEqual(meta1['prbs7_seed'], 1)
        for seed in (37, 73):
            cfg = dict(PLUTO_BPSK_CONFIG, prbs7_seed=seed)
            mod.validate_bpsk_tx(cfg, 'checked wiring')
            payload, meta = mod.bpsk_tx_payload(cfg)
            self.assertEqual(meta['prbs7_seed'], seed)
            self.assertNotEqual(bytes(np.asarray(payload).tobytes()),
                                bytes(np.asarray(payload1).tobytes()))
        for bad in (0, 5, 126, 127, 37.0, True, '37', None):
            with self.assertRaises(ValueError):
                mod.validate_bpsk_tx(dict(PLUTO_BPSK_CONFIG, prbs7_seed=bad),
                                     'checked wiring')

    def test_playback_requires_path_confirmed_and_two_second_cap(self):
        mod = _radio_test_module()
        mod.validate_bpsk_tx(dict(PLUTO_BPSK_CONFIG), 'checked wiring')
        with self.assertRaises(ValueError):
            mod.validate_bpsk_tx(dict(PLUTO_BPSK_CONFIG), '')
        bad = dict(PLUTO_BPSK_CONFIG, duration_s=2.5)
        with self.assertRaises(ValueError):
            mod.validate_bpsk_tx(bad, 'checked wiring')
        bad = dict(PLUTO_BPSK_CONFIG, sample_rate=2400000)
        with self.assertRaises(ValueError):
            mod.validate_bpsk_tx(bad, 'checked wiring')
        bad = dict(PLUTO_BPSK_CONFIG, symbol_rate_hz=3000000.0)
        with self.assertRaises(ValueError):
            mod.validate_bpsk_tx(bad, 'checked wiring')


class _BpskDummyProc(object):
    def __init__(self, tx_result):
        self.tx_result = tx_result
        self.calls = []
        self.returncode = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.calls.append('terminate')

    def kill(self):
        self.calls.append('kill')

    def wait(self, timeout=None):
        self.calls.append('wait')
        return 0

    def communicate(self, timeout=None):
        self.calls.append('communicate')
        self.returncode = 0
        return (json.dumps(self.tx_result), '')


class DirectionBBpskTests(unittest.TestCase):
    """task-14: one BPSK session, two demodulation paths reported separately."""

    def _cfg(self):
        return planner.load_config(ROOT / 'config' / 'diagnostic.json')

    def _tx_result(self, duration=2.0, stream_seconds=1.98):
        return {'mode': 'tx-bpsk',
                'status': 'TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED',
                'config': {'duration_s': duration},
                'bpsk': {'symbol_rate_hz': 3000000, 'sample_rate_hz': 6000000,
                         'samples_per_symbol': 2, 'prbs7_seed': 1,
                         'shaping': 'RECT', 'differential_encoding': False},
                'stream': {'stream_started_utc': '2026-10-20T12:22:05+00:00',
                           'stream_stopped_utc': '2026-10-20T12:22:07+00:00',
                           'stream_end_requested_utc':
                               '2026-10-20T12:22:07+00:00',
                           'stream_stopped_confirmed_utc':
                               '2026-10-20T12:22:07+00:00',
                           'stream_stop_confirmed': True,
                           'stream_seconds': stream_seconds},
                'tx_powerdown_readback': '1',
                'after': {'channels': {
                    'altvoltage1_out': {'powerdown': '1'},
                    'voltage0_out': {'hardwaregain': '-89.750000 dB'}}}}

    def _harness(self, client, payload, tx_result=None, fail_radio=False):
        import board_integration
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'token').write_text('offline-token', encoding='utf-8')
        out = tmp / 'out'
        out.mkdir()
        args = types.SimpleNamespace(
            client_dir=tmp, token_file=tmp / 'token', board_port=5000,
            radioconda=Path('unused-radioconda'), path_confirmed='offline',
            sessions=1, extended=False, mode='bpsk')
        calls = {'radio': 0, 'safe_off': 0, 'capture': 0}
        meta = {'capture_id': 'offline-bpsk', 'samples': 4096,
                'sample_rate_hz': 60000000, 'ratio': 0,
                'pre_io_ok': True, 'post_io_ok': True,
                'post_tx_source_selected': False, 'post_rx_armed': False}

        def radio(mode, config_path, radioconda, path_confirmed=None,
                  ready_file=None, background=False):
            calls['radio'] += 1
            if fail_radio:
                raise RuntimeError('injected radio failure')
            if ready_file is not None:
                Path(ready_file).write_text('STREAM_STARTED')
            return _BpskDummyProc(tx_result or self._tx_result())

        def capture(c, out_dir, tag):
            calls['capture'] += 1
            return dict(meta), payload

        names = ('_load_board_client', '_pluto_safe_off', '_board_preflight',
                 '_configure_and_check', '_run_radio', '_capture_board_frame')
        originals = {name: getattr(board_integration, name) for name in names}

        def safe_off(serial):
            calls['safe_off'] += 1
            return {'hardwaregain_db': -89.75, 'tx_powerdown': '1'}
        board_integration._load_board_client = lambda d: (
            lambda port=None: client, RuntimeError)
        board_integration._pluto_safe_off = safe_off
        board_integration._board_preflight = (
            lambda c, token, cfg: c.control_arm(token))
        board_integration._configure_and_check = (
            lambda c, cfg, attenuation_mdb=None: {})
        board_integration._run_radio = radio
        board_integration._capture_board_frame = capture
        try:
            return board_integration._run_b_bpsk(args, self._cfg(), out), out, calls
        finally:
            self._last_calls = calls
            for name, fn in originals.items():
                setattr(board_integration, name, fn)

    def test_bpsk_session_reports_both_demodulation_paths(self):
        client = _CapabilityBoardClient(armed=False)
        payload, _meta = _bpsk_board_payload()
        report, _out, calls = self._harness(client, payload)
        self.assertTrue(report['passed'])
        self.assertEqual(calls['radio'], 1)
        self.assertEqual(calls['capture'], 1)
        self.assertEqual(report['budget']['board_captures'], 1)
        self.assertEqual(report['budget']['pluto_tx_starts'], 1)
        self.assertEqual(report['budget']['board_tx_starts'], 0)
        self.assertEqual(report['budget']['pluto_tx_seconds_total'], 1.98)
        self.assertEqual(report['pluto_tx_summary']['stream_seconds'], 1.98)
        for path in ('normal', 'conjugated'):
            demod = report['demod'][path]
            self.assertTrue(demod['reference_sync'], path)
            self.assertEqual(demod['reason'], '', path)
            self.assertEqual(demod['ber'], 0.0, path)
            self.assertGreaterEqual(demod['compared_bits'], 64)
            self.assertIn('cfo_hz', demod)
            self.assertIn('timing_quality', demod)
            self.assertIn('polarity', demod)
        self.assertFalse(client.armed)

    def test_bpsk_session_fails_when_demod_does_not_sync(self):
        client = _CapabilityBoardClient(armed=False)
        payload, _meta = _bpsk_board_payload(noise=True)
        report, _out, _calls = self._harness(client, payload)
        self.assertFalse(report['passed'])
        joined = '; '.join(report['problems'])
        self.assertIn('demod', joined)

    def test_bpsk_budget_rejects_more_than_one_capture_or_two_seconds(self):
        import board_integration
        for change in ({'board_captures': 2}, {'pluto_tx_seconds_total': 2.5},
                       {'board_tx_starts': 1}, {'pluto_tx_starts': 2}):
            report = _b_bpsk_report()
            report['budget'].update(change)
            self.assertFalse(board_integration._evaluate_b_bpsk(report), change)
        self.assertTrue(board_integration._evaluate_b_bpsk(_b_bpsk_report()))

    def test_pluto_tx_receipt_identity_is_gated(self):
        import board_integration
        self.assertTrue(board_integration._evaluate_b_bpsk(_b_bpsk_report()))
        bad_cases = (
            {'status': 'TX_STREAM_COMPLETED_RF_UNVERIFIED'},
            {'sample_rate_hz': 2400000},
            {'symbol_rate_hz': 3000000.0},
            {'samples_per_symbol': 20},
            {'prbs7_seed': 37},
            {'shaping': 'RRC'},
            {'differential_encoding': True},
            {'configured_duration_s': 2.5},
        )
        for bad in bad_cases:
            report = _b_bpsk_report()
            report['pluto_tx_summary'].update(bad)
            self.assertFalse(board_integration._evaluate_b_bpsk(report), bad)
            self.assertIn('Pluto TX receipt',
                          '; '.join(report['problems']), bad)

    def test_final_control_or_rx_armed_fails(self):
        import board_integration
        for mutation in ('control_armed', 'rx_armed'):
            report = _b_bpsk_report()
            if mutation == 'control_armed':
                report['final_status']['armed'] = True
            else:
                report['final_status']['pl_status']['rx']['armed'] = True
            self.assertFalse(board_integration._evaluate_b_bpsk(report), mutation)
            self.assertIn('armed', '; '.join(report['problems']), mutation)

    def test_bpsk_budget_uses_measured_stream_seconds(self):
        import board_integration
        self.assertTrue(board_integration._evaluate_b_bpsk(_b_bpsk_report()))
        for bad in (None, float('nan'), -0.1, 2.5):
            report = _b_bpsk_report()
            report['pluto_tx_summary']['stream_seconds'] = bad
            report['budget']['pluto_tx_seconds_total'] = bad
            self.assertFalse(board_integration._evaluate_b_bpsk(report), bad)
            self.assertIn('stream', '; '.join(report['problems']), bad)
        for key in ('stream_started_utc', 'stream_stopped_utc'):
            report = _b_bpsk_report()
            report['pluto_tx_summary'].pop(key)
            self.assertFalse(board_integration._evaluate_b_bpsk(report), key)

    def test_bpsk_differential_encoding_must_be_explicit_false(self):
        import board_integration
        for mode in ('none', 'missing'):
            report = _b_bpsk_report()
            if mode == 'missing':
                report['pluto_tx_summary'].pop('differential_encoding', None)
            else:
                report['pluto_tx_summary']['differential_encoding'] = None
            self.assertFalse(board_integration._evaluate_b_bpsk(report), mode)

    def test_code_rail_events_are_counted_before_clipping(self):
        import board_integration
        payload, _meta = _bpsk_board_payload()
        codes = np.frombuffer(payload, dtype='<i2').copy()
        codes[0] = -2048
        payload_rail = codes.astype('<i2').tobytes()
        meta = {'capture_id': 'rail', 'samples': 4096,
                'sample_rate_hz': 60000000, 'ratio': 0,
                'pre_io_ok': True, 'post_io_ok': True,
                'post_tx_source_selected': False, 'post_rx_armed': False}
        out = board_integration._demodulate_bpsk(
            payload_rail, meta, self._cfg(), conjugate=False)
        self.assertGreaterEqual(out['code_rail_events'], 1)

    def test_bpsk_dry_run_plan_has_the_one_capture_budget(self):
        import contextlib
        import io
        import board_integration
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = board_integration.main(['--dry-run', '--direction', 'B',
                                         '--mode', 'bpsk'])
        self.assertEqual(rc, 0)
        plan = json.loads(buf.getvalue())
        self.assertEqual(plan['mode'], 'bpsk-plan')
        self.assertEqual(plan['hardware_accessed'], False)
        self.assertEqual(plan['budget']['board_captures'], 1)
        self.assertEqual(plan['budget']['pluto_tx_starts'], 1)
        self.assertEqual(plan['budget']['pluto_tx_configured_seconds'], 2.0)
        self.assertIn('stream_seconds',
                      plan['budget']['pluto_tx_measured_seconds_gate'])
        self.assertEqual(plan['budget']['board_tx_starts'], 0)
        self.assertIn('demod', '; '.join(plan['gates']))


class _StatDummyProc(object):
    """Minimal Pluto TX child for the statistical runner (offline)."""

    def __init__(self, tx_result, exit_early=False, stopped_path=None):
        self.tx_result = tx_result
        self.exit_early = bool(exit_early)
        self.stopped_path = stopped_path
        self.returncode = 1 if exit_early else None

    def poll(self):
        return 1 if self.exit_early else None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, timeout=None):
        self.returncode = 0
        if self.stopped_path is not None:
            Path(self.stopped_path).write_text(
                json.dumps({'stream_seconds': 1.98}), encoding='utf-8')
        return (json.dumps(self.tx_result), '')


def _bpsk_stat_summary(seed, stream_seconds=1.98):
    return {
        'status': 'TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED',
        'sample_rate_hz': 6000000, 'symbol_rate_hz': 3000000,
        'samples_per_symbol': 2, 'prbs7_seed': seed,
        'shaping': 'RECT', 'differential_encoding': False,
        'configured_duration_s': 2.0, 'stream_seconds': stream_seconds,
        'stream_started_utc': '2026-10-20T12:22:05+00:00',
        'stream_stopped_utc': '2026-10-20T12:22:07+00:00',
        'stream_end_requested_utc': '2026-10-20T12:22:07+00:00',
        'stream_stopped_confirmed_utc': '2026-10-20T12:22:07+00:00',
        'stream_stop_confirmed': True}


def _bpsk_stat_report(tx_per_session=1):
    """A complete, passing 33-record statistical report (normal path only)."""
    seeds = (1, 37, 73)
    frames_per_segment = 10 // tx_per_session
    records = []
    sessions = []
    for si, seed in enumerate(seeds):
        session_records = []
        segments = []
        for seg in range(tx_per_session):
            seg_records = []
            for local in range(frames_per_segment):
                fi = seg * frames_per_segment + local
                record = {
                    'session': si, 'segment': seg, 'seed': seed, 'on': True,
                    'tag': 's%d-on-%02d' % (si, fi),
                    'iq_sha256': hashlib.sha256(
                        ('on-%d-%d' % (si, fi)).encode()).hexdigest(),
                    'capture': {'capture_id': 'c-%d-%d' % (si, fi),
                                'samples': 4096,
                                'sample_rate_hz': 60000000,
                                'ratio': 0, 'release_id': 'rel-test',
                                'pre_io_ok': True, 'post_io_ok': True,
                                'post_tx_source_selected': False,
                                'post_rx_armed': False,
                                'clipped_samples': 0},
                    'demod': {
                        'normal': {'reference_sync': True, 'reason': '',
                                   'ber': 0.0, 'bit_errors': 0,
                                   'compared_bits': 138, 'cfo_hz': 0.0,
                                   'timing_quality': 1.0, 'polarity': 1,
                                   'reference_offset': 1,
                                   'code_rail_events': 0},
                        'conjugated': {'reference_sync': True, 'reason': '',
                                       'ber': 0.0, 'bit_errors': 0,
                                       'compared_bits': 138}}}
                records.append(record)
                seg_records.append(record)
            segments.append({
                'index': seg,
                'records': [r['tag'] for r in seg_records],
                'frame_count': len(seg_records),
                'pluto_tx_summary': _bpsk_stat_summary(seed),
                'measured_stream_seconds': 1.98,
                'safe_off': {'hardwaregain_db': -89.75,
                             'tx_powerdown': '1'},
                'tx_off_before_tx': {'tx_source_selected': False},
                'tx_off_after_segment': (
                    {'tx_source_selected': False}
                    if seg < tx_per_session - 1 else None),
                'wall_seconds': 2.1})
            session_records.extend(seg_records)
        off = {
            'session': si, 'segment': tx_per_session - 1, 'seed': seed,
            'on': False, 'tag': 's%d-off' % si,
            'iq_sha256': hashlib.sha256(
                ('off-%d' % si).encode()).hexdigest(),
            'capture': {'capture_id': 'off-%d' % si, 'samples': 4096,
                        'sample_rate_hz': 60000000, 'ratio': 0,
                        'release_id': 'rel-test', 'pre_io_ok': True,
                        'post_io_ok': True,
                        'post_tx_source_selected': False,
                        'post_rx_armed': False, 'clipped_samples': 0},
            'demod': {
                'normal': {'reference_sync': False,
                           'reason': 'REFERENCE_MISSING', 'ber': None,
                           'bit_errors': None, 'compared_bits': None},
                'conjugated': {'reference_sync': False,
                               'reason': 'REFERENCE_MISSING', 'ber': None,
                               'bit_errors': None, 'compared_bits': None}}}
        records.append(off)
        session_records.append(off)
        sessions.append({
            'index': si, 'seed': seed, 'closed': True, 'disarm_ack': True,
            'disarm_response': {'armed': False},
            'capture_count': len(session_records),
            'segments': segments,
            'measured_stream_seconds': 1.98 * tx_per_session,
            'final_status_unknown': False,
            'pluto_tx_summary': segments[-1]['pluto_tx_summary'],
            'pluto_tx_summaries': [s['pluto_tx_summary'] for s in segments],
            'pluto_safe_off': segments[-1]['safe_off'],
            'tx_off_before_session': segments[0]['tx_off_before_tx'],
            'tx_off_after_session': {'tx_source_selected': False},
            'final_status': {'online': True, 'stale': False, 'armed': False,
                             'pl_status': {'io_ok': True,
                                           'tx': {'source_selected': False},
                                           'rx': {'armed': False}}}})
    return {
        'direction': 'B', 'mode': 'bpsk-stat',
        'stat_tx_per_session': tx_per_session,
        'seed': seeds[0],
        'records': records,
        'sessions': sessions,
        'budget': {'sessions': 3, 'board_inits': 3, 'board_configures': 3,
                   'board_captures': 33,
                   'pluto_tx_starts': 3 * tx_per_session,
                   'pluto_tx_seconds_total': 5.94 * tx_per_session,
                   'board_tx_starts': 0},
        'pluto_preflight': {'hardwaregain_db': -89.75, 'tx_powerdown': '1'},
        'final_status': sessions[-1]['final_status'],
        'final_status_unknown': False, 'problems': [],
    }


class _PreflightStatus(object):
    def __init__(self, online=True, stale=False, armed=False, io_ok=True,
                 tx_selected=False, rx_armed=False, release_id='rel-test',
                 error=None, control_ops=('control.arm',)):
        self.online = online
        self.stale = stale
        self.armed = armed
        self.error = error
        self.release_id = release_id
        self.control_ops = tuple(control_ops)
        self.pl_status = types.SimpleNamespace(
            io_ok=io_ok,
            tx=types.SimpleNamespace(source_selected=tx_selected,
                                     length_words=0, position_words=0),
            rx=types.SimpleNamespace(armed=rx_armed))

    def to_dict(self):
        return {'online': self.online, 'stale': self.stale,
                'armed': self.armed, 'error': self.error,
                'release_id': self.release_id,
                'control_ops': list(self.control_ops),
                'pl_status': {'io_ok': self.pl_status.io_ok,
                              'tx': {'source_selected':
                                     self.pl_status.tx.source_selected},
                              'rx': {'armed': self.pl_status.rx.armed}}}


class _RetryBoardClient(object):
    """Poll sequence: first `timeout_count` polls raise, then statuses."""

    def __init__(self, timeout_count=0, status=None, release_id='rel-test'):
        self.timeout_count = timeout_count
        self.polls = 0
        self.armed = False
        self.release_id = release_id
        self.status = status or _PreflightStatus(release_id=release_id)

    def poll(self):
        self.polls += 1
        if self.polls <= self.timeout_count:
            raise TimeoutError('timed out')
        return _PreflightStatus(
            online=self.status.online, stale=self.status.stale,
            armed=self.armed,
            io_ok=self.status.pl_status.io_ok,
            tx_selected=self.status.pl_status.tx.source_selected,
            rx_armed=self.status.pl_status.rx.armed,
            release_id=self.release_id, error=self.status.error,
            control_ops=self.status.control_ops)

    def control_arm(self, token):
        self.armed = True

    def control_disarm(self):
        self.armed = False


class _ArmCapabilityClient(object):
    """Fake daemon whose control.arm capability may appear late."""

    def __init__(self, op_sequence, arm_raises_once=False, armed=False):
        self.op_sequence = [tuple(item) for item in op_sequence]
        self.armed = bool(armed)
        self.arm_calls = 0
        self.arm_raises_once = arm_raises_once
        self.polls = 0

    def _ops(self):
        if len(self.op_sequence) > 1:
            return self.op_sequence.pop(0)
        return self.op_sequence[0]

    def poll(self):
        self.polls += 1
        return _PreflightStatus(armed=self.armed, control_ops=self._ops())

    def control_arm(self, token):
        self.arm_calls += 1
        if 'control.arm' not in self._ops():
            raise RuntimeError('the RF daemon did not advertise control.arm, '
                               'so it was not sent')
        if self.arm_raises_once:
            self.arm_raises_once = False
            raise RuntimeError('the RF daemon did not advertise control.arm, '
                               'so it was not sent')
        self.armed = True


class _UnsafeAfterArmClient(object):
    """Arm succeeds, then the first post-arm poll is unsafe (Codex B2)."""

    def __init__(self, kind):
        self.kind = kind
        self.polls = 0
        self.armed = False

    def poll(self):
        self.polls += 1
        if self.polls <= 2:
            return _PreflightStatus(armed=False,
                                    control_ops=('control.arm',))
        if self.polls == 3:
            if self.kind == 'stale':
                return _PreflightStatus(armed=True, stale=True)
            if self.kind == 'io_ok':
                return _PreflightStatus(armed=True, io_ok=False)
            return _PreflightStatus(armed=True, tx_selected=True)
        return _PreflightStatus(armed=True)

    def control_arm(self, token):
        self.armed = True


class ArmCapabilityTests(unittest.TestCase):
    """task-20: wait for control.arm to be re-advertised after disarm."""

    def test_delayed_arm_capability_passes(self):
        import board_integration
        client = _ArmCapabilityClient([(), ('control.arm',)])
        status = board_integration._arm_if_needed(
            client, 'token', attempts=5, delay_s=0)
        self.assertTrue(status.armed)
        self.assertGreaterEqual(client.polls, 2)
        self.assertEqual(client.arm_calls, 1)

    def test_arm_capability_delayed_two_polls_passes_with_evidence(self):
        import board_integration
        client = _ArmCapabilityClient([(), (), ('control.arm',)])
        status = board_integration._arm_if_needed(
            client, 'token', attempts=5, delay_s=0)
        self.assertTrue(status.armed)
        self.assertGreaterEqual(client.polls, 3)
        self.assertEqual(client.arm_calls, 1)
        evidence = board_integration._LAST_ARM_WAIT
        self.assertGreaterEqual(evidence['attempts'], 3)
        self.assertGreaterEqual(len(evidence['polls']), 3)
        self.assertNotIn('control.arm', evidence['polls'][0]['control_ops'])
        self.assertIn('control.arm', evidence['polls'][-1]['control_ops'])

    def test_stale_status_fails_arm_wait(self):
        import board_integration
        client = _RetryBoardClient(
            status=_PreflightStatus(online=True, stale=True))
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._arm_if_needed(client, 'token', attempts=3,
                                             delay_s=0)

    def test_unsafe_status_fails_arm_wait(self):
        import board_integration
        client = _RetryBoardClient(
            status=_PreflightStatus(online=True, stale=False, io_ok=False))
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._arm_if_needed(client, 'token', attempts=3,
                                             delay_s=0)

    def test_unsafe_post_arm_snapshot_is_rejected_and_kept(self):
        import board_integration
        for kind in ('tx_selected', 'io_ok', 'stale'):
            client = _UnsafeAfterArmClient(kind)
            with self.assertRaises(board_integration.ExecutionError):
                board_integration._board_preflight(client, 'token', {})
            evidence = board_integration._LAST_ARM_WAIT
            self.assertIn('unsafe_snapshot', evidence, kind)
            unsafe = evidence['unsafe_snapshot']
            if kind == 'stale':
                self.assertIs(unsafe['stale'], True)
            elif kind == 'io_ok':
                self.assertIs(unsafe['pl_status']['io_ok'], False)
            else:
                self.assertIs(
                    unsafe['pl_status']['tx']['source_selected'], True)
            self.assertEqual(evidence['polls'][-1]['phase'], 'post-arm')

    def test_never_advertised_arm_capability_fails(self):

        import board_integration
        client = _ArmCapabilityClient([()])
        with self.assertRaises(board_integration.ExecutionError) as ctx:
            board_integration._arm_if_needed(
                client, 'token', attempts=3, delay_s=0)
        self.assertIn('control.arm', str(ctx.exception))
        self.assertGreaterEqual(client.polls, 3)
        self.assertEqual(client.arm_calls, 0)

    def test_already_armed_skips_arm(self):
        import board_integration
        client = _ArmCapabilityClient([()], armed=True)
        status = board_integration._arm_if_needed(
            client, 'token', attempts=3, delay_s=0)
        self.assertTrue(status.armed)
        self.assertEqual(client.arm_calls, 0)

    def test_did_not_advertise_race_retries(self):
        import board_integration
        client = _ArmCapabilityClient([('control.arm',)],
                                      arm_raises_once=True)
        status = board_integration._arm_if_needed(
            client, 'token', attempts=3, delay_s=0)
        self.assertTrue(status.armed)
        self.assertEqual(client.arm_calls, 2)


class _PostArmTimeoutClient(_RetryBoardClient):
    """Arm succeeds; selected post-arm confirmation polls fail or stay unsafe."""

    def __init__(self, fail_polls=(4,), stale=False, unsafe=False,
                 armed_after_arm=True):
        super().__init__(timeout_count=0)
        self.polls = 0
        self.fail_polls = set(fail_polls)
        self.stale = stale
        self.unsafe = unsafe
        self.armed_after_arm = armed_after_arm

    def poll(self):
        self.polls += 1
        if self.polls in self.fail_polls or self.polls >= 100:
            raise TimeoutError('timed out')
        if self.polls >= 4 and self.stale:
            return _PreflightStatus(online=True, stale=True,
                                    armed=self.armed)
        if self.polls >= 4 and self.unsafe:
            return _PreflightStatus(online=True, stale=False, io_ok=False,
                                    armed=self.armed)
        if self.polls >= 4 and not self.armed_after_arm:
            return _PreflightStatus(online=True, stale=False, armed=False,
                                    control_ops=('control.arm',))
        return _PreflightStatus(armed=self.armed,
                                control_ops=('control.arm',))

    def control_arm(self, token):
        self.armed = True


class PreflightRetryTests(unittest.TestCase):
    """task-19: bounded retry for transient session-start control polls."""

    def test_first_poll_timeout_then_safe_passes_with_attempts(self):
        import board_integration
        client = _RetryBoardClient(timeout_count=1)
        status = board_integration._board_preflight(client, 'token', {})
        self.assertTrue(status.armed)
        evidence = board_integration._LAST_PREFLIGHT_POLL
        self.assertGreaterEqual(evidence['attempts'], 2)
        self.assertTrue(evidence['errors'])
        self.assertTrue(evidence['snapshot']['online'])

    def test_post_arm_poll_timeout_does_not_abort_preflight(self):
        import board_integration
        client = _PostArmTimeoutClient(fail_polls=(4,))
        status = board_integration._board_preflight(client, 'token', {})
        self.assertTrue(status.armed)
        evidence = board_integration._LAST_POST_ARM_POLL
        self.assertGreaterEqual(evidence['attempts'], 2)
        self.assertTrue(evidence['errors'])
        self.assertGreaterEqual(client.polls, 5)

    def test_post_arm_persistent_timeout_fails(self):
        import board_integration
        client = _PostArmTimeoutClient(fail_polls=range(4, 20))
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._board_preflight(client, 'token', {})

    def test_post_arm_persistent_armed_false_fails(self):
        import board_integration
        client = _PostArmTimeoutClient(armed_after_arm=False)
        with self.assertRaises(board_integration.ExecutionError) as ctx:
            board_integration._board_preflight(client, 'token', {})
        self.assertIn('armed=true', str(ctx.exception))

    def test_post_arm_stale_fails(self):
        import board_integration
        client = _PostArmTimeoutClient(stale=True)
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._board_preflight(client, 'token', {})

    def test_post_arm_unsafe_fails(self):
        import board_integration
        client = _PostArmTimeoutClient(unsafe=True)
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._board_preflight(client, 'token', {})

    def test_post_arm_normal_first_poll(self):
        import board_integration
        client = _PostArmTimeoutClient(fail_polls=())
        status = board_integration._board_preflight(client, 'token', {})
        self.assertTrue(status.armed)
        self.assertEqual(board_integration._LAST_POST_ARM_POLL['attempts'], 1)

    def test_all_polls_timeout_fails(self):
        import board_integration
        client = _RetryBoardClient(timeout_count=999)
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._poll_status_with_retry(client, attempts=5,
                                                      delay_s=0)
        self.assertEqual(client.polls, 5)

    def test_stale_status_fails_immediately(self):
        import board_integration
        client = _RetryBoardClient(
            status=_PreflightStatus(online=True, stale=True))
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._poll_status_with_retry(client, attempts=5,
                                                      delay_s=0)
        self.assertEqual(client.polls, 1)

    def test_unsafe_status_fails_immediately(self):
        import board_integration
        client = _RetryBoardClient(
            status=_PreflightStatus(online=True, stale=False, io_ok=False))
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._poll_status_with_retry(client, attempts=5,
                                                      delay_s=0)
        self.assertEqual(client.polls, 1)

    def test_tx_off_snapshot_retries_transient_timeout(self):
        import board_integration
        client = _RetryBoardClient(timeout_count=1)
        snap = board_integration._board_tx_off_snapshot(client, 'tag', 'when')
        self.assertIs(snap['tx_source_selected'], False)
        self.assertIs(snap['io_ok'], True)
        self.assertGreaterEqual(snap['poll_attempts'], 2)
        self.assertTrue(snap['poll_errors'])

    def test_tx_off_snapshot_rejects_tx_selected_immediately(self):
        import board_integration
        client = _RetryBoardClient(
            status=_PreflightStatus(online=True, stale=False,
                                    tx_selected=True))
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._board_tx_off_snapshot(client, 'tag', 'when')
        self.assertEqual(client.polls, 1)

    def test_two_consecutive_sessions_preflight_after_one_transient(self):
        import board_integration
        client = _RetryBoardClient(timeout_count=1)
        first = board_integration._board_preflight(client, 'token', {})
        self.assertTrue(first.armed)
        client.control_disarm()
        second = board_integration._board_preflight(client, 'token', {})
        self.assertTrue(second.armed)
        self.assertGreaterEqual(client.polls, 5)


class BpskStatTests(unittest.TestCase):
    """task-17: 3 sessions x (10 ON + 1 OFF), 33-record aggregate gates."""

    def _cfg(self):
        return planner.load_config(ROOT / 'config' / 'diagnostic.json')

    def _tx_result(self, seed, stream_seconds=1.98, stop_confirmed=True):
        return {'mode': 'tx-bpsk',
                'status': 'TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED',
                'config': {'duration_s': 2.0, 'prbs7_seed': seed},
                'bpsk': {'symbol_rate_hz': 3000000, 'sample_rate_hz': 6000000,
                         'samples_per_symbol': 2, 'prbs7_seed': seed,
                         'shaping': 'RECT', 'differential_encoding': False},
                'stream': {'stream_started_utc': '2026-10-20T12:22:05+00:00',
                           'stream_stopped_utc': '2026-10-20T12:22:07+00:00',
                           'stream_end_requested_utc':
                               '2026-10-20T12:22:07+00:00',
                           'stream_stopped_confirmed_utc':
                               ('2026-10-20T12:22:07+00:00'
                                if stop_confirmed else None),
                           'stream_stop_confirmed': stop_confirmed,
                           'stream_seconds': (stream_seconds
                                              if stop_confirmed else None)},
                'tx_powerdown_readback': '1',
                'after': {'channels': {
                    'altvoltage1_out': {'powerdown': '1'},
                    'voltage0_out': {'hardwaregain': '-89.750000 dB'}}}}

    def _harness(self, client=None, stream_seconds=1.98, on_noise=False,
                 exit_early=False, safe_off_fails=False, tx_per_session=1,
                 stopped_after=None, requested_after=None, bad_first_on=None,
                 stop_confirmed=True):
        import board_integration
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'token').write_text('offline-token', encoding='utf-8')
        out = tmp / 'out'
        out.mkdir()
        args = types.SimpleNamespace(
            client_dir=tmp, token_file=tmp / 'token', board_port=5000,
            radioconda=Path('unused-radioconda'), path_confirmed='offline',
            sessions=3, extended=False, mode='bpsk-stat',
            stat_tx_per_session=tx_per_session)
        client = client or _CapabilityBoardClient(armed=False)
        events = []
        calls = {'radio': 0, 'safe_off': 0, 'capture': 0}
        seeds = (1, 37, 73)
        state = {'stopped_path': None}

        def safe_off(serial):
            calls['safe_off'] += 1
            events.append('safe_off')
            if safe_off_fails:
                raise RuntimeError('injected safe-off failure')
            return {'hardwaregain_db': -89.75, 'tx_powerdown': '1'}

        def radio(mode, config_path, radioconda, path_confirmed=None,
                  ready_file=None, background=False):
            calls['radio'] += 1
            cfg = json.loads(Path(config_path).read_text(encoding='utf-8'))
            seed = cfg.get('prbs7_seed', 1)
            stopped_path = None
            if ready_file is not None:
                Path(ready_file).write_text('STREAM_STARTED')
                stopped_path = str(ready_file) + '.stopped'
                state['stopped_path'] = stopped_path
            return _StatDummyProc(
                self._tx_result(seed, stream_seconds,
                                stop_confirmed=stop_confirmed),
                exit_early=exit_early, stopped_path=stopped_path)

        def capture(c, out_dir, tag):
            calls['capture'] += 1
            events.append(tag)
            if (stopped_after is not None
                    and calls['capture'] >= stopped_after
                    and state['stopped_path'] is not None):
                Path(state['stopped_path']).write_text(
                    json.dumps({'stream_seconds': 1.98}), encoding='utf-8')
            if (requested_after is not None
                    and calls['capture'] >= requested_after
                    and state['stopped_path'] is not None):
                requested_path = Path(str(state['stopped_path']).replace(
                    '.stopped', '.stream_end_requested'))
                requested_path.write_text(
                    json.dumps({'stream_seconds': None}), encoding='utf-8')
            si = int(tag[1]) if tag.startswith('s') else 0
            seed = seeds[si]
            noise = ('off' in tag) or (on_noise and 'on' in tag)
            payload, _meta = _bpsk_board_payload(noise=noise, seed=seed)
            # Make every saved IQ unique (real captures always are); the last
            # sample is outside the receiver's 201-symbol comparison window.
            codes = np.frombuffer(payload, dtype='<i2').copy()
            codes[-1] = min(2046, max(-2046,
                                      int(codes[-1]) + calls['capture']))
            payload = codes.astype('<i2').tobytes()
            meta = {'capture_id': tag, 'samples': 4096,
                    'sample_rate_hz': 60000000, 'ratio': 0,
                    'release_id': 'rel-test', 'pre_io_ok': True,
                    'post_io_ok': True, 'post_tx_source_selected': False,
                    'post_rx_armed': False, 'clipped_samples': 0}
            if bad_first_on and calls['capture'] == 1 and 'on' in tag:
                if bad_first_on == 'tx_selected':
                    meta['post_tx_source_selected'] = True
                elif bad_first_on == 'clipped':
                    meta['clipped_samples'] = 1
                elif bad_first_on == 'post_io':
                    meta['post_io_ok'] = False
            return dict(meta), payload

        names = ('_load_board_client', '_pluto_safe_off', '_board_preflight',
                 '_configure_and_check', '_run_radio', '_capture_board_frame')
        originals = {name: getattr(board_integration, name) for name in names}
        board_integration._load_board_client = lambda d: (
            lambda port=None: client, RuntimeError)
        board_integration._pluto_safe_off = safe_off
        board_integration._board_preflight = (
            lambda c, token, cfg: c.control_arm(token))
        board_integration._configure_and_check = (
            lambda c, cfg, attenuation_mdb=None: {})
        board_integration._run_radio = radio
        board_integration._capture_board_frame = capture
        try:
            return board_integration._run_b_bpsk_stat(
                args, self._cfg(), out), out, calls, events, client
        finally:
            self._last_calls = calls
            for name, fn in originals.items():
                setattr(board_integration, name, fn)

    def test_stat_normal_three_sessions_order_and_counts(self):
        report, _out, calls, events, client = self._harness()
        self.assertTrue(report['passed'], report.get('problems'))
        self.assertEqual(calls['radio'], 3)
        self.assertEqual(calls['capture'], 33)
        self.assertEqual(calls['safe_off'], 3)
        self.assertEqual(report['budget']['sessions'], 3)
        self.assertEqual(report['budget']['board_inits'], 3)
        self.assertEqual(report['budget']['board_configures'], 3)
        self.assertEqual(report['budget']['board_captures'], 33)
        self.assertEqual(report['budget']['pluto_tx_starts'], 3)
        self.assertEqual(report['budget']['board_tx_starts'], 0)
        self.assertAlmostEqual(report['budget']['pluto_tx_seconds_total'], 5.94)
        self.assertEqual(len(report['records']), 33)
        self.assertEqual(report['on_aligned'], 30)
        self.assertEqual(report['aggregate_ber'], 0.0)
        self.assertEqual(report['off_false_ber'], 0)
        for record in report['records']:
            for key in ('poll_s', 'capture_s', 'demod_normal_s',
                        'demod_conjugated_s', 'total_s'):
                self.assertIn(key, record['timing'])
        for session in report['sessions']:
            self.assertIn('preflight_poll', session)
            self.assertIn('arm_wait', session)
            self.assertIn('post_arm_poll', session)
            self.assertIn('disarm_settle_s', session)
        # Each OFF capture must come after its session's safe-off readback.
        for si in range(3):
            off = events.index('s%d-off' % si)
            self.assertEqual(events[off - 1], 'safe_off')
        self.assertFalse(client.armed)

    def test_stat_rejects_wrong_session_count_before_hardware(self):
        import board_integration
        args = types.SimpleNamespace(
            client_dir=Path('.'), token_file=Path('.'), board_port=5000,
            radioconda=Path('.'), path_confirmed='offline', sessions=2,
            extended=False, mode='bpsk-stat')
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._run_b_bpsk_stat(
                args, self._cfg(), Path(tempfile.mkdtemp()))

    def test_stat_fails_when_tx_exits_before_ten_on_captures(self):
        import board_integration
        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._harness(exit_early=True)
        self.assertIn('INCOMPLETE', str(ctx.exception).upper())

    def test_stat_fails_when_measured_stream_exceeds_two_seconds(self):
        import board_integration
        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._harness(stream_seconds=2.5)
        self.assertIn('STREAM', str(ctx.exception).upper())

    def test_stat_fails_when_safe_off_readback_fails(self):
        import board_integration
        with self.assertRaises(board_integration.ExecutionError):
            self._harness(safe_off_fails=True)

    def test_stat_gate_rejects_low_alignment(self):
        import board_integration
        report = _bpsk_stat_report()
        on = [r for r in report['records'] if r['on']]
        for record in on[:4]:
            record['demod']['normal'] = dict(record['demod']['normal'],
                                             reference_sync=False, ber=None,
                                             bit_errors=None, compared_bits=None)
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        self.assertIn('aligned', '; '.join(report['problems']).lower())

    def test_stat_gate_rejects_high_aggregate_ber(self):
        import board_integration
        report = _bpsk_stat_report()
        record = next(r for r in report['records'] if r['on'])
        record['demod']['normal'].update(bit_errors=100, ber=100.0 / 138,
                                         compared_bits=138)
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        self.assertIn('ber', '; '.join(report['problems']).lower())

    def test_stat_gate_rejects_off_false_ber(self):
        import board_integration
        report = _bpsk_stat_report()
        off = next(r for r in report['records'] if not r['on'])
        off['demod']['normal'].update(reference_sync=True, bit_errors=0,
                                      compared_bits=138, ber=0.0)
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        self.assertIn('off', '; '.join(report['problems']).lower())

    def test_stat_gate_requires_disarm_ack_and_closed_session(self):
        import board_integration
        for change in ({'closed': False}, {'disarm_ack': False}):
            report = _bpsk_stat_report()
            report['sessions'][0].update(change)
            self.assertFalse(board_integration._evaluate_b_bpsk_stat(report),
                             change)

    def test_stat_gate_rejects_budget_overrun(self):
        import board_integration
        self.assertTrue(board_integration._evaluate_b_bpsk_stat(
            _bpsk_stat_report()))
        for change in ({'board_captures': 34}, {'pluto_tx_starts': 4},
                       {'sessions': 4}, {'pluto_tx_seconds_total': 6.5},
                       {'board_tx_starts': 1}):
            report = _bpsk_stat_report()
            report['budget'].update(change)
            self.assertFalse(board_integration._evaluate_b_bpsk_stat(report),
                             change)

    def test_stat_gate_rejects_tx_receipt_seed_mismatch(self):
        import board_integration
        report = _bpsk_stat_report()
        report['sessions'][0]['segments'][0]['pluto_tx_summary'][
            'prbs7_seed'] = 37
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        self.assertIn('seed', '; '.join(report['problems']).lower())

    def test_stat_gate_rejects_extra_record_or_eleventh_frame(self):
        import board_integration
        report = _bpsk_stat_report()
        extra = dict(report['records'][0], tag='s0-on-extra',
                     iq_sha256='f' * 64)
        report['records'].append(extra)
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        report = _bpsk_stat_report()
        report['sessions'][0]['capture_count'] = 12
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))

    def test_stat_disarm_ack_comes_from_observed_evidence(self):
        import board_integration

        class _EmptyDisarmClient(_CapabilityBoardClient):
            def control_disarm(self):
                super().control_disarm()
                return {}

        report, _out, _calls, _events, _client = self._harness(
            client=_EmptyDisarmClient())
        self.assertFalse(report['passed'])
        self.assertFalse(report['sessions'][0]['disarm_ack'])
        self.assertIn('disarm', '; '.join(report['problems']).lower())

    def test_stat_fails_when_final_poll_still_armed(self):
        import board_integration

        class _ArmedFinalClient(_CapabilityBoardClient):
            def control_disarm(self):
                self.calls.append('disarm')
                return {'armed': False}  # responds but leaves the daemon armed

        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._harness(client=_ArmedFinalClient())
        self.assertIn('SAFETY', str(ctx.exception))

    def test_stat_gate_rejects_off_conjugated_false_ber(self):
        import board_integration
        report = _bpsk_stat_report()
        off = next(r for r in report['records'] if not r['on'])
        off['demod']['conjugated'] = {'reference_sync': True,
                                      'bit_errors': 0,
                                      'compared_bits': 138, 'ber': 0.0}
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        self.assertIn('off', '; '.join(report['problems']).lower())

    def test_stat_stops_immediately_when_stream_stopped_midway(self):
        import board_integration
        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._harness(stopped_after=4)
        self.assertIn('STREAM_ENDED_EARLY', str(ctx.exception))
        self.assertLessEqual(self._last_calls['capture'], 5)

    def test_stat_rejects_first_frame_unsafe_before_more_capture_or_tx(self):
        import board_integration
        for bad in ('tx_selected', 'clipped', 'post_io'):
            with self.assertRaises(board_integration.ExecutionError) as ctx:
                self._harness(bad_first_on=bad)
            self.assertIn('BPSK_STAT_CAPTURE_SAFETY', str(ctx.exception))
            self.assertEqual(self._last_calls['capture'], 1)
            self.assertEqual(self._last_calls['radio'], 1)

    def test_stat_stops_on_requested_stream_end_marker(self):
        import board_integration
        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._harness(requested_after=4)
        self.assertIn('STREAM_ENDED_EARLY', str(ctx.exception))
        self.assertLessEqual(self._last_calls['capture'], 5)

    def test_stat_fails_when_tx_stop_is_unconfirmed(self):
        import board_integration
        with self.assertRaises(board_integration.ExecutionError) as ctx:
            self._harness(stop_confirmed=False)
        self.assertIn('STREAM_STOP_UNCONFIRMED', str(ctx.exception))
        self.assertEqual(self._last_calls['radio'], 1)

    def test_stat_gate_rejects_unconfirmed_stream_stop(self):
        import board_integration
        report = _bpsk_stat_report()
        report['sessions'][0]['segments'][0]['pluto_tx_summary'][
            'stream_stop_confirmed'] = False
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        report = _bpsk_stat_report()
        report['sessions'][0]['segments'][0]['pluto_tx_summary'][
            'stream_seconds'] = 1.9
        report['sessions'][0]['segments'][0]['pluto_tx_summary'][
            'stream_stop_confirmed'] = True
        report['sessions'][0]['segments'][0][
            'measured_stream_seconds'] = 1.9
        report['sessions'][0]['measured_stream_seconds'] = 1.9
        self.assertTrue(board_integration._evaluate_b_bpsk_stat(report))
        report = _bpsk_stat_report()
        report['sessions'][0]['segments'][0]['pluto_tx_summary'][
            'stream_seconds'] = 2.3
        report['sessions'][0]['segments'][0][
            'measured_stream_seconds'] = 2.3
        report['sessions'][0]['measured_stream_seconds'] = 2.3
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))

    def test_stat_two_segments_five_plus_five_and_per_segment_safe_off(self):
        report, _out, calls, events, client = self._harness(tx_per_session=2)
        self.assertTrue(report['passed'], report.get('problems'))
        self.assertEqual(calls['radio'], 6)
        self.assertEqual(calls['capture'], 33)
        self.assertEqual(calls['safe_off'], 6)
        self.assertEqual(report['budget']['pluto_tx_starts'], 6)
        self.assertAlmostEqual(report['budget']['pluto_tx_seconds_total'], 11.88)
        for si in range(3):
            session = report['sessions'][si]
            self.assertEqual(len(session['segments']), 2)
            for segment in session['segments']:
                self.assertEqual(segment['frame_count'], 5)
                self.assertLessEqual(segment['measured_stream_seconds'], 2.0)
                self.assertEqual(segment['safe_off']['tx_powerdown'], '1')
                self.assertIs(segment['tx_off_before_tx'][
                    'tx_source_selected'], False)
            self.assertIs(session['segments'][0]['tx_off_after_segment'][
                'tx_source_selected'], False)
            off = events.index('s%d-off' % si)
            self.assertEqual(events[off - 1], 'safe_off')
        self.assertFalse(client.armed)

    def test_stat_two_segment_budget_caps(self):
        import board_integration
        self.assertTrue(board_integration._evaluate_b_bpsk_stat(
            _bpsk_stat_report(2)))
        report = _bpsk_stat_report(2)
        report['budget']['pluto_tx_seconds_total'] = 12.5
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        report = _bpsk_stat_report(2)
        report['sessions'][0]['segments'][0]['measured_stream_seconds'] = 2.5
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))
        report = _bpsk_stat_report(1)
        report['budget']['pluto_tx_seconds_total'] = 6.5
        self.assertFalse(board_integration._evaluate_b_bpsk_stat(report))

    def test_stat_rejects_invalid_tx_per_session(self):
        import board_integration
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._bpsk_b_stat_plan(self._cfg(), 3)
        args = types.SimpleNamespace(
            client_dir=Path('.'), token_file=Path('.'), board_port=5000,
            radioconda=Path('.'), path_confirmed='offline', sessions=3,
            extended=False, mode='bpsk-stat', stat_tx_per_session=3)
        with self.assertRaises(board_integration.ExecutionError):
            board_integration._run_b_bpsk_stat(
                args, self._cfg(), Path(tempfile.mkdtemp()))

    def test_stat_dry_run_plan_two_segments(self):
        import contextlib
        import io
        import board_integration
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = board_integration.main([
                '--dry-run', '--direction', 'B', '--mode', 'bpsk-stat',
                '--stat-tx-per-session', '2'])
        self.assertEqual(rc, 0)
        plan = json.loads(buf.getvalue())
        self.assertEqual(plan['stat_tx_per_session'], 2)
        self.assertEqual(plan['budget']['board_captures'], 33)
        self.assertEqual(plan['budget']['pluto_tx_starts'], 6)
        self.assertEqual(plan['budget']['pluto_tx_configured_seconds'], 12.0)
        self.assertEqual(plan['budget']['pluto_tx_measured_seconds_cap'], 12.0)
        for window in plan['windows']:
            self.assertEqual(window['tx_per_session'], 2)
            self.assertEqual(len(window['segments']), 2)
            self.assertEqual([s['on_frames'] for s in window['segments']],
                             [5, 5])
        joined = '; '.join(plan['gates'])
        self.assertIn('stopped', joined)
        self.assertIn('safe-off readback', joined)

    def test_stat_dry_run_plan_has_three_sessions_and_33_captures(self):
        import contextlib
        import io
        import board_integration
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = board_integration.main(['--dry-run', '--direction', 'B',
                                         '--mode', 'bpsk-stat'])
        self.assertEqual(rc, 0)
        plan = json.loads(buf.getvalue())
        self.assertEqual(plan['mode'], 'bpsk-stat-plan')
        self.assertEqual(plan['hardware_accessed'], False)
        self.assertEqual(plan['budget']['sessions'], 3)
        self.assertEqual(plan['budget']['board_inits'], 3)
        self.assertEqual(plan['budget']['board_configures'], 3)
        self.assertEqual(plan['budget']['board_captures'], 33)
        self.assertEqual(plan['budget']['pluto_tx_starts'], 3)
        self.assertEqual(plan['budget']['board_tx_starts'], 0)
        self.assertEqual(plan['budget']['pluto_tx_configured_seconds'], 6.0)
        joined = '; '.join(plan['gates'])
        self.assertIn('27', joined)
        self.assertIn('0.01', joined)
        self.assertEqual([w['seed'] for w in plan['windows']], [1, 37, 73])


if __name__ == '__main__':
    unittest.main()