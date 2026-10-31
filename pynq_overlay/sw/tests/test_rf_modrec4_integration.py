"""Product hook tests: additive modrec4 result on an immutable frame (offline)."""
import json
import dataclasses
from pathlib import Path
import sys
import time
import types
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

import rf_status_worker as W  # noqa: E402
import rf_bpsk_pipeline as BP  # noqa: E402
from rf_bpsk_pipeline import BpskConfig, DemodConfig  # noqa: E402
from rf_rx_types import RxIqFrame, RxReadback  # noqa: E402

FRAME_DIR = PROJECT / 'evidence' / 'rf-teaching' / 's2-46db-80mhz-narrowband-2026-10-21' / 'raw'
CANDIDATE_ROOT = (PROJECT / 'evidence' / 'rf-teaching' /
                  's2-46db-modrec4-product-2026-10-21' / 'candidate' / 'releases')
TAG = 'nf-00-normal'


def candidate():
    matches = list(CANDIDATE_ROOT.rglob('merged_release_manifest.json'))
    if not matches:
        raise unittest.SkipTest('modrec4 candidate not built')
    return max(matches, key=lambda path: path.stat().st_mtime).parent


def load():
    meta = json.loads((FRAME_DIR / (TAG + '.json')).read_text(encoding='utf-8'))
    frame = RxIqFrame(
        capture_id=meta['capture_id'], payload=(FRAME_DIR / (TAG + '.iq')).read_bytes(),
        samples=meta['samples'], sample_rate_hz=meta['sample_rate_hz'], ratio=meta['ratio'],
        daemon_timestamp_ns=meta['daemon_timestamp_ns'],
        connection_generation=meta['connection_generation'],
        config_revision=meta['config_revision'], request_id=meta['request_id'],
        release_id=meta['release_id'], op=meta['op'], physical=meta['physical'],
        pre_readback=RxReadback(**meta['pre_readback']),
        post_readback=RxReadback(**meta['post_readback']),
        completed_at_monotonic=meta['completed_at_monotonic'])
    submitted = DemodConfig(
        config=BpskConfig('BPSK', 3000000, 'RECT', 1), revision=1,
        decim=1, rx_len=4096, input_fs_hz=60000000)
    return frame, {'demod_config': submitted}


class FakeSocket:
    def fileno(self):
        return 3


class FakeClient:
    _connection_generation = 10
    _config_revision = 2
    _session_socket = FakeSocket()

    def close(self):
        pass


class CaptureOnceB1Tests(unittest.TestCase):
    """The real capture path, with only the client's capture result substituted."""

    def setUp(self):
        self.release = candidate()
        sys.path.insert(0, str(self.release))
        frame, request = load()
        manifest = json.loads((self.release / 'merged_release_manifest.json').read_text(
            encoding='utf-8'))
        self.release_id = manifest['release_id']
        self.frame = dataclasses.replace(frame, release_id=self.release_id)
        self.demod_config = request['demod_config']

        class CaptureClient(FakeClient):
            frame = None

            def capture(self, args, physical=False):
                return self.frame

        self.client = CaptureClient()
        self.worker = W.RfStatusWorker(self.client, release_directory=str(self.release))
        self.worker._demod_pipeline = BP
        self.worker._demod_info = {'available': True, 'release_id': self.release_id}

    def capture(self, frame):
        self.client.frame = frame
        self.worker._capture_request = {
            'physical': False, 'args': {'samples': 4096, 'ratio': 0},
            'demod_config': self.demod_config}
        self.worker._capture_cancel = False
        self.worker._capture_once()
        return self.worker.take_rx_result()

    def test_expired_frame_publishes_done_but_not_modrec4_ok(self):
        old = dataclasses.replace(self.frame,
                                  completed_at_monotonic=time.monotonic() - 60.0)
        payload, status = self.capture(old)
        self.assertEqual(status['state'], W.RX_CAPTURE_DONE)
        self.assertEqual(payload['modrec4_result']['status'], 'INVALID')
        self.assertEqual(payload['modrec4_result']['reason'], 'frame_expired')

    def test_disconnected_session_publishes_done_but_not_modrec4_ok(self):
        self.client._session_socket = None
        current = dataclasses.replace(self.frame, completed_at_monotonic=time.monotonic())
        payload, status = self.capture(current)
        self.assertEqual(status['state'], W.RX_CAPTURE_DONE)
        self.assertEqual(payload['modrec4_result']['status'], 'INVALID')
        self.assertEqual(payload['modrec4_result']['reason'], 'session_disconnected')


class ModRec4WorkerHookTests(unittest.TestCase):
    def setUp(self):
        self.release = candidate()
        self.frame, self.request = load()
        manifest = json.loads((self.release / 'merged_release_manifest.json').read_text(
            encoding='utf-8'))
        self.frame = dataclasses.replace(self.frame, release_id=manifest['release_id'],
                                         completed_at_monotonic=time.monotonic())
        self.worker = W.RfStatusWorker(FakeClient(), release_directory=str(self.release))
        sys.path.insert(0, str(self.release))

    def test_ensure_and_publish_additive_result(self):
        self.worker._ensure_modrec4()
        self.assertTrue(self.worker.modrec4_status()['available'])
        result = self.worker._modrec4_result(self.frame, self.request, 'env', 'demod', 'six')
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['frame_identity']['capture_id'], self.frame.capture_id)
        self.assertEqual(result['frame_identity']['request_id'], self.frame.request_id)
        self.assertIn('iq_sha256', result['frame_identity'])
        self.assertEqual(result['contract']['six_class_probabilities'], None)

    def test_missing_input_context_is_invalid_not_reused(self):
        result = self.worker._modrec4_result(self.frame, {}, None, None, None)
        self.assertEqual(result['status'], 'INVALID')
        self.assertEqual(result['reason'], 'input_context_unavailable')

    def test_wrong_symbol_domain_is_unavailable_not_run(self):
        submitted = DemodConfig(config=BpskConfig('BPSK', 2000000, 'RECT', 1),
                                revision=1, decim=1, rx_len=4096,
                                input_fs_hz=60000000)
        result = self.worker._modrec4_result(
            self.frame, {'demod_config': submitted}, None, None, None)
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertEqual(result['input_domain']['status'], 'UNAVAILABLE')
        self.assertEqual(result['input_domain']['reason'], 'unsupported_symbol_domain')

    def test_config_change_invalidates_without_running(self):
        self.worker.client._config_revision = 99
        result = self.worker._modrec4_result(self.frame, self.request, None, None, None)
        self.assertEqual(result['status'], 'INVALID')
        self.assertEqual(result['reason'], 'stale_config_revision')

    def test_expired_frame_is_invalid_not_ok(self):
        old = dataclasses.replace(self.frame,
                                  completed_at_monotonic=time.monotonic() - 60.0)
        result = self.worker._modrec4_result(old, self.request, None, None, None)
        self.assertEqual(result['status'], 'INVALID')
        self.assertEqual(result['reason'], 'frame_expired')
        self.assertTrue(result['stale'])
        self.assertGreaterEqual(result['age_s'], 59.0)

    def test_disconnected_session_is_invalid_not_ok(self):
        self.worker.client._session_socket = None
        result = self.worker._modrec4_result(self.frame, self.request, None, None, None)
        self.assertEqual(result['status'], 'INVALID')
        self.assertEqual(result['reason'], 'session_disconnected')
        self.assertTrue(result['stale'])

    def test_take_rx_result_rechecks_config_after_publication(self):
        frame = self.frame.to_dict()
        self.worker._rx_handoff = {
            'frame': frame,
            'modrec4_result': {'status': 'OK', 'validity': 'current', 'stale': False},
        }
        self.worker.client._config_revision = 99
        payload, _status = self.worker.take_rx_result()
        self.assertEqual(payload['modrec4_result']['status'], 'INVALID')
        self.assertEqual(payload['modrec4_result']['reason'],
                         'post_publish_stale_config_revision')
        self.assertTrue(payload['modrec4_result']['stale'])

    def test_passthrough_objects_are_unchanged(self):
        env = {'label': 'UNKNOWN'}
        demod = {'reference_sync': True}
        six = {'label': 'TONE'}
        self.worker._modrec4_result(self.frame, self.request, env, demod, six)
        self.assertEqual(env, {'label': 'UNKNOWN'})
        self.assertEqual(demod, {'reference_sync': True})
        self.assertEqual(six, {'label': 'TONE'})


if __name__ == '__main__':
    unittest.main()
