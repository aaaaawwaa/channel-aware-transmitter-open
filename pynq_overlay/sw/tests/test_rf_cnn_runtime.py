"""Data-only loader, quality gates and numerical parity for the fixed CNN."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'pynq_overlay' / 'sw'))


def model_document(seed=7):
    from rf_cnn_runtime import CLASSES, WEIGHT_SHAPES
    rng = np.random.RandomState(seed)
    weights = {k: {'shape': list(s), 'values': (rng.randn(*s) * .05).astype(np.float32).ravel().tolist()}
               for k, s in WEIGHT_SHAPES.items()}
    return dict(schema_version=1, network='tiny-iq-cnn-v1', preprocessing='dc-complex-rms-v1',
                input_shape=[2, 4096], classes=list(CLASSES), weights=weights,
                training_manifest_sha256='a'*64, evaluation_manifest_sha256='b'*64,
                supported_domain=dict(input_fs_hz=60000000, decim=[1],
                    symbol_rates_hz=[2000000, 3000000, 5000000], shaping=['RECT', 'RRC0.35']),
                thresholds=dict(max_softmax=.0, margin=.0, min_complex_rms=1.0), status='experimental')


def write_model(tmp_path, document):
    path = tmp_path / 'rf_model.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_preprocess_complex_rms_and_invalid_samples():
    from rf_cnn_runtime import preprocess
    iq = np.random.RandomState(4).randint(-700, 700, (4096, 2)).astype(np.int16)
    x, gate = preprocess(iq)
    assert gate['ok'] and x.shape == (2, 4096) and x.dtype == np.float32
    assert np.abs(x.mean(axis=1)).max() < 1e-6
    assert abs(np.sqrt(np.mean(np.sum(x.astype(np.float64)**2, axis=0))) - 1) < 1e-6
    assert preprocess(np.zeros((4096, 2), np.int16))[1]['reason'] == 'no_energy'
    iq[0, 0] = -2048
    assert preprocess(iq)[1]['reason'] == 'clipping'
    for code in (2048, -2049, 65536, 1.5, float('nan'), float('inf')):
        bad = np.zeros((4096, 2)); bad[0, 0] = code
        with pytest.raises(ValueError):
            preprocess(bad)
    with pytest.raises(ValueError):
        preprocess(np.zeros((2, 4096), np.int16))


@pytest.mark.parametrize('kind', ['schema', 'shape', 'nan', 'inf', 'huge', 'bool', 'values', 'classes', 'domain'])
def test_model_validation_rejects_invalid_data(tmp_path, kind):
    from rf_cnn_runtime import load_model
    doc = model_document()
    if kind == 'schema': doc['schema_version'] = True
    if kind == 'shape': doc['weights']['conv1.weight']['shape'] = [8, 2, 8]
    if kind in ('nan', 'inf', 'huge', 'bool'):
        doc['weights']['head.bias']['values'][0] = {'nan': float('nan'), 'inf': float('inf'), 'huge': 1e300, 'bool': True}[kind]
    if kind == 'values': doc['weights']['head.bias']['values'].pop()
    if kind == 'classes': doc['classes'].reverse()
    if kind == 'domain': doc['supported_domain']['decim'] = [2]
    path, digest = write_model(tmp_path, doc)
    with pytest.raises(ValueError): load_model(path, digest)


def test_loader_rejects_hash_size_pickle_and_duplicate_json(tmp_path):
    from rf_cnn_runtime import load_model
    path, digest = write_model(tmp_path, model_document())
    with pytest.raises(ValueError, match='hash'): load_model(path, '0'*64)
    path.write_bytes(b' ' * (128*1024+1))
    with pytest.raises(ValueError, match='128'): load_model(path, hashlib.sha256(path.read_bytes()).hexdigest())
    # A pickle-like payload is never deserialized, nor executed.
    path.write_bytes(b'\x80\x04cos\nsystem\n(Vecho unsafe\ntR.')
    with pytest.raises(ValueError): load_model(path, hashlib.sha256(path.read_bytes()).hexdigest())
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match='duplicate'): load_model(path, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize('convolution_weight_scale', [1, 40])
def test_numpy_layers_match_torch_with_cross_correlation(tmp_path, convolution_weight_scale):
    torch = pytest.importorskip("torch", reason="Torch parity requires the dedicated .venv-rf-ml CPU environment")
    from scripts.rf_ml.train import build_network
    from rf_cnn_runtime import load_model, preprocess
    torch.set_num_threads(1)
    doc = model_document()
    # Exercise large pre-ReLU activations as well as the tiny random network;
    # the absolute-error contract applies before ReLU discards negatives.
    for name, tensor in doc['weights'].items():
        if name.startswith('conv'):
            tensor['values'] = [float(np.float32(v * convolution_weight_scale))
                                for v in tensor['values']]
    path, digest = write_model(tmp_path, doc)
    model = load_model(path, digest)
    net = build_network().eval()
    mapping = {'conv1': 'body.0', 'conv2': 'body.2', 'conv3': 'body.4', 'head': 'head'}
    state = {mapping[k.split('.')[0]] + '.' + k.split('.')[1]:
             torch.tensor(v['values']).reshape(v['shape']) for k, v in doc['weights'].items()}
    net.load_state_dict(state)
    for seed in (3, 41, 109):
        iq = np.random.RandomState(seed).randint(-900, 900, (4096, 2)).astype(np.int16)
        x, _ = preprocess(iq)
        logits, actual = model.forward(x, return_activations=True)
        expected = {}
        with torch.no_grad():
            z = torch.from_numpy(x[None])
            for index in range(3):
                z = net.body[index*2](z); expected['conv%d' % (index+1)] = z.numpy()[0]
                z = net.body[index*2+1](z); expected['relu%d' % (index+1)] = z.numpy()[0]
            z = z.mean(-1); expected['gap'] = z.numpy()[0]
            expected['logits'] = net.head(z).numpy()[0]
        for key in expected:
            assert actual[key].dtype == np.float32
            assert np.max(np.abs(expected[key] - actual[key])) <= 1e-4, key
        assert np.max(np.abs(logits - expected['logits'])) <= 1e-4


def test_trained_model_train_frame_layer_parity():
    torch = pytest.importorskip('torch', reason='Trained-weight parity requires the CPU ML environment')
    from scripts.rf_ml.train import build_network
    from rf_cnn_runtime import load_model, preprocess
    model_path = ROOT / 'evidence/rx-smart/task7/benchmark/board-model.json'
    iq_path = ROOT / 'evidence/rx-smart/task7/data-physical/iq/rx-train-bpsk-2m-rrc0.35-s01-amp120-f08.iq'
    if not model_path.is_file() or not iq_path.is_file():
        pytest.skip('Frozen physical TRAIN fixture is unavailable in this checkout')
    model = load_model(model_path, '6d5a251ba697cc166c48115c45f3d30cb4f1d083a0e0e1b7c584e927ce994d99')
    raw = iq_path.read_bytes()
    manifest = json.loads((iq_path.parent.parent / 'manifest.json').read_bytes())
    receipt = next(f for f in manifest['frames'] if f['capture_id'] == iq_path.stem)
    assert receipt['split'] == 'train' and not receipt['exclusion_reasons']
    assert hashlib.sha256(raw).hexdigest() == receipt['iq_sha256']
    x, gate = preprocess(np.frombuffer(raw, dtype='<i2').reshape(4096, 2))
    assert gate['ok']
    net = build_network().eval()
    torch.set_num_threads(1)
    mapping = {'conv1': 'body.0', 'conv2': 'body.2', 'conv3': 'body.4', 'head': 'head'}
    net.load_state_dict({mapping[k.split('.')[0]] + '.' + k.split('.')[1]:
                        torch.from_numpy(v.copy()) for k, v in model.weights.items()})
    _, actual = model.forward(x, return_activations=True)
    with torch.no_grad():
        z = torch.from_numpy(x[None])
        for index in range(3):
            z = net.body[index*2](z)
            assert np.max(np.abs(z.numpy()[0] - actual['conv%d' % (index+1)])) <= 1e-4
            z = net.body[index*2+1](z)
            assert np.max(np.abs(z.numpy()[0] - actual['relu%d' % (index+1)])) <= 1e-4
        gap = z.mean(-1)
        assert np.max(np.abs(gap.numpy()[0] - actual['gap'])) <= 1e-4
        assert np.max(np.abs(net.head(gap).numpy()[0] - actual['logits'])) <= 1e-4




def test_rot200_frozen_corpus_logit_parity():
    """Amendment-5 regression: exported rot200 production model logits must match
    the torch checkpoint within 1e-4 on sampled frozen physical TEST frames.
    Conv/ReLU/GAP layers are bit-exact; the head linear layer is the residual."""
    torch = pytest.importorskip("torch", reason="Torch parity requires the dedicated .venv-rf-ml CPU environment")
    from rf_cnn_runtime import load_model
    from scripts.rf_ml.train import build_network, preprocess as train_preprocess
    checkpoint = Path(r"<LOCAL_PATH>")
    exported = Path(r"<LOCAL_PATH>")
    manifest_path = Path(r"<LOCAL_PATH>")
    if not (checkpoint.is_file() and exported.is_file() and manifest_path.is_file()):
        pytest.skip("rot200 frozen-corpus fixture is unavailable in this checkout")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    torch.set_num_threads(1)
    net = build_network()
    ckpt = torch.load(checkpoint, map_location="cpu", weights_only=True)
    net.load_state_dict(ckpt["model_state_dict"]); net.eval()
    model = load_model(str(exported), "f76f2fc05aee93f72b2427f17f41e0bf59ce538f84688b8350a8d0800ddb9d47")
    checked = 0
    worst = 0.0
    for frame in manifest["frames"]:
        if frame["split"] != "test" or frame["exclusion_reasons"]:
            continue
        iq = np.frombuffer((root / frame["iq_path"]).read_bytes(), dtype="<i2").reshape(-1, 2)
        x, gate = train_preprocess(iq)
        if not gate["ok"]:
            continue
        checked += 1
        with torch.no_grad():
            expected = net(torch.from_numpy(x[None, ...])).numpy()[0]
        actual = model.forward(x)
        worst = max(worst, float(np.abs(actual - expected).max()))
    assert checked >= 700, checked
    assert worst <= 1e-4, worst

def test_predict_unknown_gates_and_sampling_domain(tmp_path):
    from rf_cnn_runtime import load_model
    doc = model_document()
    for value in doc['weights'].values(): value['values'] = [0.0] * len(value['values'])
    doc['weights']['head.bias']['values'][5] = 20.0
    path, digest = write_model(tmp_path, doc)
    model = load_model(path, digest)
    iq = np.random.RandomState(5).randint(-100, 100, (4096, 2)).astype(np.int16)
    assert model.predict(iq)['reason'] == 'other'
    assert model.predict(iq)['label'] == 'UNKNOWN'
    assert model.predict(iq, decim=2)['reason'] == 'unsupported_domain'
    assert model.predict(np.zeros_like(iq))['reason'] == 'no_energy'
    doc['weights']['head.bias']['values'] = [0.0] * 6
    doc['weights']['head.bias']['values'][0] = .01
    doc['thresholds']['max_softmax'] = .7
    path, digest = write_model(tmp_path, doc); model = load_model(path, digest)
    assert model.predict(iq)['reason'] == 'confidence'
    doc['thresholds']['max_softmax'] = 0; doc['thresholds']['margin'] = .5
    path, digest = write_model(tmp_path, doc); model = load_model(path, digest)
    assert model.predict(iq)['reason'] == 'margin'
    doc['thresholds']['min_complex_rms'] = 1000
    path, digest = write_model(tmp_path, doc); model = load_model(path, digest)
    assert model.predict(iq)['reason'] == 'below_energy_floor'
