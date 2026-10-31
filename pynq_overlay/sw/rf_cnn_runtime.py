"""Bounded JSON loader and numpy inference (hybrid BLAS conv1/2 + ordered FMA conv3).

Compatible with the board's Python 3.8 / numpy 1.17.4. Scores are uncalibrated
softmax scores. The declared symbol-rate domain is evidence, not an estimator.
"""
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
import ctypes
import os
from ctypes import POINTER, c_float, c_int

CLASSES = ('TONE', 'BPSK', 'QPSK', 'QAM16', 'QAM64', 'OTHER')
PREPROCESSING = 'dc-complex-rms-v1'
MAX_MODEL_BYTES = 128 * 1024
WEIGHT_SHAPES = {'conv1.weight': (8, 2, 7), 'conv1.bias': (8,),
                 'conv2.weight': (16, 8, 5), 'conv2.bias': (16,),
                 'conv3.weight': (16, 16, 3), 'conv3.bias': (16,),
                 'head.weight': (6, 16), 'head.bias': (6,)}
_HEX = re.compile(r'^[0-9a-f]{64}$')


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON field: ' + key)
        result[key] = value
    return result


def validate_model(document):
    """Check the same model/weight contract used by merged_release."""
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1 or document.get('network') != 'tiny-iq-cnn-v1'
            or document.get('preprocessing') != PREPROCESSING
            or document.get('classes') != list(CLASSES)
            or document.get('input_shape') != [2, 4096]
            or any(type(x) is not int for x in document.get('input_shape', []))):
        raise ValueError('invalid model schema/network/preprocessing/classes')
    for key in ('training_manifest_sha256', 'evaluation_manifest_sha256'):
        if not isinstance(document.get(key), str) or not _HEX.fullmatch(document[key]):
            raise ValueError('invalid manifest hash: ' + key)
    if document.get('status', 'experimental') not in ('experimental', 'production'):
        raise ValueError('invalid model status')
    domain = document.get('supported_domain')
    if (not isinstance(domain, dict) or type(domain.get('input_fs_hz')) is not int
            or domain['input_fs_hz'] != 60000000 or domain.get('decim') != [1]
            or type(domain['decim'][0]) is not int):
        raise ValueError('invalid model sampling domain')
    for key, choices in (('symbol_rates_hz', (2000000, 3000000, 5000000)),
                         ('shaping', ('RECT', 'RRC0.35'))):
        values = domain.get(key)
        expected_type = int if key == 'symbol_rates_hz' else str
        if (not isinstance(values, list) or not values
                or any(type(v) is not expected_type or v not in choices for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('invalid model domain: ' + key)
    thresholds = document.get('thresholds')
    if (not isinstance(thresholds, dict)
            or set(thresholds) != {'max_softmax', 'margin', 'min_complex_rms'}
            or any(not _number(v) for v in thresholds.values())
            or not 0 <= thresholds['max_softmax'] <= 1
            or not 0 <= thresholds['margin'] <= 1 or thresholds['min_complex_rms'] <= 0):
        raise ValueError('invalid model thresholds')
    weights = document.get('weights')
    if not isinstance(weights, dict) or set(weights) != set(WEIGHT_SHAPES):
        raise ValueError('invalid model weights')
    for name, shape in WEIGHT_SHAPES.items():
        tensor = weights[name]
        if (not isinstance(tensor, dict) or set(tensor) != {'shape', 'values'}
                or not isinstance(tensor['shape'], list) or tensor['shape'] != list(shape)
                or any(type(n) is not int for n in tensor['shape'])
                or not isinstance(tensor['values'], list) or len(tensor['values']) != math.prod(shape)
                or any(not _number(v) or abs(v) > 3.402823466e38 for v in tensor['values'])):
            raise ValueError('invalid model weight shape/values: ' + name)
    return document


def load_model(path, expected_sha256):
    """Read at most 128 KiB and verify identity before JSON parsing."""
    if not isinstance(expected_sha256, str) or not _HEX.fullmatch(expected_sha256):
        raise ValueError('invalid expected model hash')
    with Path(path).open('rb') as source:
        raw = source.read(MAX_MODEL_BYTES + 1)
    if len(raw) > MAX_MODEL_BYTES:
        raise ValueError('model exceeds 128 KiB')
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('model hash mismatch')
    try:
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError('invalid model JSON: ' + str(exc)) from exc
    return TinyIQCNNModel(validate_model(document), expected_sha256)



_LIB_NAME = 'rf_cnn_conv.dll' if os.name == 'nt' else 'rf_cnn_conv.so'
_CONV_LIB = None
_lib_path = Path(__file__).resolve().parent / _LIB_NAME
if _lib_path.exists():
    try:
        _CONV_LIB = ctypes.CDLL(str(_lib_path))
        _CONV_LIB.ordered_conv2d.argtypes = [
            POINTER(c_float), c_int, c_int, c_int, c_int, c_int,
            POINTER(c_float), POINTER(c_float), POINTER(c_float),
        ]
        _CONV_LIB.ordered_conv2d.restype = None
    except Exception:
        _CONV_LIB = None

def _convolution_c(x, weight, bias, padding):
    cin, lin = x.shape
    cout = weight.shape[0]
    kernel = weight.shape[2]
    length = (lin + 2 * padding - kernel) // 2 + 1
    out = np.empty((cout, length), dtype=np.float32)
    _CONV_LIB.ordered_conv2d(
        x.ctypes.data_as(POINTER(c_float)),
        cin, lin, cout, kernel, padding,
        weight.ctypes.data_as(POINTER(c_float)),
        bias.ctypes.data_as(POINTER(c_float)),
        out.ctypes.data_as(POINTER(c_float)),
    )
    return out

def _convolution(x, weight, bias, padding):
    if _CONV_LIB is not None:
        return _convolution_c(x, weight, bias, padding)
    return _convolution_fma(x, weight, bias, padding)

def preprocess(iq):
    """4096 signed 12-bit pairs -> [2,4096] float32 with complex RMS=1."""
    values = np.asarray(iq)
    if values.shape != (4096, 2) or values.dtype.kind not in 'ifu':
        raise ValueError('IQ must be 4096 numeric signed-12-bit I/Q pairs')
    if (not np.isfinite(values).all() or np.any(values < -2048) or np.any(values > 2047)
            or np.any(values != np.floor(values))):
        raise ValueError('invalid signed-12-bit IQ values')
    if np.any((values == -2048) | (values == 2047)):
        return None, dict(ok=False, reason='clipping', rms=0.0)
    x = values.astype(np.float32)
    x -= x.mean(axis=0, keepdims=True)
    rms = float(np.sqrt(np.mean(np.sum(x.astype(np.float64)**2, axis=1))))
    if rms <= 1e-6:
        return None, dict(ok=False, reason='no_energy', rms=rms)
    return np.ascontiguousarray((x / rms).T, dtype=np.float32), dict(ok=True, reason='', rms=rms)


def _convolution_blas(x, weight, bias, padding):
    """Stride=2 cross-correlation. Window copies remain below 1 MiB."""
    padded = np.pad(x, ((0, 0), (padding, padding)), mode='constant')
    kernel = weight.shape[2]
    length = (padded.shape[1] - kernel) // 2 + 1
    windows = np.lib.stride_tricks.as_strided(padded,
        shape=(x.shape[0], length, kernel),
        strides=(padded.strides[0], padded.strides[1]*2, padded.strides[1]))
    matrix = windows.transpose(1, 0, 2).reshape(length, -1)
    result = np.dot(matrix, weight.reshape(weight.shape[0], -1).T) + bias
    return np.ascontiguousarray(result.T, dtype=np.float32)




def _convolution_fma(x, weight, bias, padding):
    """Ordered float32 multiply-add rounding (same as frozen FMA candidate)."""
    padded = np.pad(x, ((0, 0), (padding, padding)), mode='constant')
    kernel = weight.shape[2]
    length = (padded.shape[1] - kernel) // 2 + 1
    windows = np.lib.stride_tricks.as_strided(padded,
        shape=(x.shape[0], length, kernel),
        strides=(padded.strides[0], padded.strides[1]*2, padded.strides[1]))
    matrix = windows.transpose(1, 0, 2).reshape(length, -1).astype(np.float64)
    weights = weight.reshape(weight.shape[0], -1).T.astype(np.float64)
    result = np.zeros((length, weight.shape[0]), dtype=np.float32)
    for index in range(matrix.shape[1]):
        product = matrix[:, index, None] * weights[index, None, :]
        product += result
        result = product.astype(np.float32)
    result += bias
    return np.ascontiguousarray(result.T, dtype=np.float32)

class TinyIQCNNModel:
    def __init__(self, document, model_sha256=''):
        validate_model(document)
        self.model_sha256 = model_sha256
        self.status = document.get('status', 'experimental')
        self.domain = dict(document['supported_domain'])
        self.thresholds = dict(document['thresholds'])
        self.weights = {}
        for key, tensor in document['weights'].items():
            value = np.asarray(tensor['values'], dtype=np.float32).reshape(tensor['shape'])
            value.setflags(write=False)
            self.weights[key] = value

    def forward(self, x, return_activations=False):
        """Expose each Conv/ReLU/GAP/logit activation for Torch comparisons."""
        x = np.asarray(x, dtype=np.float32)
        if x.shape != (2, 4096) or not np.isfinite(x).all():
            raise ValueError('invalid network input')
        activations = {}
        for index, padding in ((1, 3), (2, 2), (3, 1)):
            name = 'conv%d' % index
            x = _convolution(x, self.weights[name+'.weight'], self.weights[name+'.bias'], padding)
            if return_activations:
                activations[name] = x
            x = np.maximum(x, np.float32(0))
            if return_activations:
                activations['relu%d' % index] = x
        gap = x.mean(axis=1)
        # Ordered head accumulation (Amendment-5): float64 products with per-term
        # float32 rounding, bias added in float32 — identical policy to the convs.
        weight = self.weights['head.weight'].astype(np.float64)
        gap64 = gap.astype(np.float64)
        acc = np.zeros((weight.shape[0],), dtype=np.float32)
        for index in range(weight.shape[1]):
            product = weight[:, index] * gap64[index] + acc.astype(np.float64)
            acc = product.astype(np.float32)
        logits = acc + self.weights['head.bias']
        if not np.isfinite(logits).all():
            raise ValueError('nonfinite network output')
        if return_activations:
            activations.update(gap=gap, logits=logits)
            return logits, activations
        return logits

    def predict(self, iq, input_fs_hz=60000000, decim=1):
        result = dict(label='UNKNOWN', reason='', score=0.0, margin=0.0,
                      model_sha256=self.model_sha256, status=self.status)
        if (type(input_fs_hz) is not int or input_fs_hz != self.domain['input_fs_hz']
                or type(decim) is not int or decim not in self.domain['decim']):
            result['reason'] = 'unsupported_domain'
            return result
        x, gate = preprocess(iq)
        if not gate['ok']:
            result['reason'] = gate['reason']
            return result
        if gate['rms'] < self.thresholds['min_complex_rms']:
            result['reason'] = 'below_energy_floor'
            return result
        logits = self.forward(x)
        exp = np.exp(logits - logits.max())
        scores = exp / exp.sum()
        order = np.argsort(scores)[::-1]
        score = float(scores[order[0]])
        margin = float(score - scores[order[1]])
        result.update(score=score, margin=margin, scores=[float(s) for s in scores])
        if order[0] == len(CLASSES)-1:
            result['reason'] = 'other'
        elif score < self.thresholds['max_softmax']:
            result['reason'] = 'confidence'
        elif margin < self.thresholds['margin']:
            result['reason'] = 'margin'
        else:
            result['label'] = CLASSES[int(order[0])]
        return result
