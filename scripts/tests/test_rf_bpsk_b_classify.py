"""task-17: offline exploratory B-direction classification script (zero RF)."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'rf_bpsk_b_classify.py'


def _classify_module():
    spec = importlib.util.spec_from_file_location('rf_bpsk_b_classify', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model_document():
    classes = ['TONE', 'BPSK', 'QPSK', 'QAM16', 'QAM64', 'OTHER']
    shapes = {'conv1.weight': [8, 2, 7], 'conv1.bias': [8],
              'conv2.weight': [16, 8, 5], 'conv2.bias': [16],
              'conv3.weight': [16, 16, 3], 'conv3.bias': [16],
              'head.weight': [6, 16], 'head.bias': [6]}
    weights = {}
    for name, shape in shapes.items():
        n = 1
        for value in shape:
            n *= value
        weights[name] = {'shape': shape, 'values': [0.0] * n}
    return {
        'schema_version': 1, 'network': 'tiny-iq-cnn-v1',
        'preprocessing': 'dc-complex-rms-v1', 'classes': classes,
        'input_shape': [2, 4096],
        'training_manifest_sha256': 'a' * 64,
        'evaluation_manifest_sha256': 'b' * 64,
        'status': 'production',
        'supported_domain': {'input_fs_hz': 60000000, 'decim': [1],
                             'symbol_rates_hz': [3000000],
                             'shaping': ['RECT']},
        'thresholds': {'max_softmax': 0.0, 'margin': 0.0,
                       'min_complex_rms': 1e-9},
        'weights': weights,
    }


def _write_iq(path, amplitude=1000):
    rng = np.random.default_rng(3)
    iq = (rng.standard_normal(4096) + 1j * rng.standard_normal(4096)) * amplitude
    codes = np.empty(8192, dtype=np.int16)
    codes[0::2] = np.clip(np.round(iq.real), -2047, 2047).astype(np.int16)
    codes[1::2] = np.clip(np.round(iq.imag), -2047, 2047).astype(np.int16)
    path.write_bytes(codes.astype('<i2').tobytes())


def _run(module, argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = module.main(argv)
    return rc, buf.getvalue()


class BpskBClassifyTests(unittest.TestCase):
    def test_missing_model_identity_fails_named(self):
        mod = _classify_module()
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'input').mkdir()
        (tmp / 'out').mkdir()
        rc, text = _run(mod, ['--input', str(tmp / 'input'),
                              '--output', str(tmp / 'out')])
        self.assertEqual(rc, 2)
        self.assertIn('MODEL_IDENTITY_REQUIRED', text)

    def test_model_hash_mismatch_fails_named(self):
        mod = _classify_module()
        tmp = Path(tempfile.mkdtemp())
        model = tmp / 'rf_model.json'
        model.write_text(json.dumps(_model_document()), encoding='utf-8')
        (tmp / 'input').mkdir()
        (tmp / 'out').mkdir()
        rc, text = _run(mod, ['--input', str(tmp / 'input'),
                              '--model', str(model),
                              '--model-sha256', '0' * 64,
                              '--output', str(tmp / 'out')])
        self.assertEqual(rc, 2)
        self.assertIn('MODEL_HASH_MISMATCH', text)

    def test_valid_model_reports_on_and_off_separately(self):
        mod = _classify_module()
        tmp = Path(tempfile.mkdtemp())
        model = tmp / 'rf_model.json'
        model.write_text(json.dumps(_model_document()), encoding='utf-8')
        expected_sha = hashlib.sha256(model.read_bytes()).hexdigest()
        inp = tmp / 'input'
        inp.mkdir()
        _write_iq(inp / 'board-s0-on-00.iq')
        _write_iq(inp / 'board-s0-on-01.iq')
        _write_iq(inp / 'board-s0-off.iq')
        for name in ('s0-on-00', 's0-on-01', 's0-off'):
            (inp / ('board-%s.json' % name)).write_text(json.dumps({
                'capture_id': name,
                # Demod fields are present but must not be used as labels.
                'reference_sync': True, 'bit_errors': 0,
                'compared_bits': 138, 'ber': 0.0}), encoding='utf-8')
        out = tmp / 'out'
        out.mkdir()
        rc, text = _run(mod, ['--input', str(inp), '--model', str(model),
                              '--model-sha256', expected_sha,
                              '--output', str(out)])
        self.assertEqual(rc, 0)
        payload = json.loads(text)
        self.assertTrue(payload['exploratory'])
        self.assertFalse(payload['domain_declared'])
        self.assertFalse(payload['acceptance_claimed'])
        self.assertEqual(payload['model_sha256'], expected_sha)
        on_names = [r['tag'] for r in payload['on']]
        off_names = [r['tag'] for r in payload['off']]
        self.assertEqual(sorted(on_names),
                         ['board-s0-on-00.iq', 'board-s0-on-01.iq'])
        self.assertEqual(off_names, ['board-s0-off.iq'])
        for record in payload['on'] + payload['off']:
            self.assertIn('label', record)
            self.assertIn('class_index', record)
            self.assertIn('scores', record)
            self.assertIn('margin', record)
            self.assertIn('reason', record)
            self.assertEqual(record['preprocessing_version'],
                             'dc-complex-rms-v1')
            self.assertNotIn('reference_sync', record)
            self.assertNotIn('ber', record)


if __name__ == '__main__':
    unittest.main()
