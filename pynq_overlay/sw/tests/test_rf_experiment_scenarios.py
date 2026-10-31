"""Controlled TX declarations stay separate from receive-only diagnostics."""
import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def module():
    assert importlib.util.find_spec('rf_experiment_scenarios'), 'S2 scenarios missing'
    import rf_experiment_scenarios
    return rf_experiment_scenarios


def iq(item):
    return np.frombuffer(item.payload, '<i2').reshape(-1, 2).astype(float)


@pytest.mark.parametrize('kind', ['normal', 'narrowband', 'pulse', 'compound', 'off'])
def test_deterministic_frozen_periodic_payload_and_declaration(kind):
    S = module()
    config = S.ScenarioConfig(kind=kind)
    a, b = S.generate_scenario(config), S.generate_scenario(config)
    assert type(a.payload) is bytes and a.payload == b.payload
    assert len(a.payload) == 2540*4
    assert a.metadata.to_dict() == dict(samples=2540, bytes=10160,
        format='s16_iq_le', direction='tx', wrap=True)
    declaration = a.declaration.to_dict()
    assert declaration['truth'] == kind and declaration['period_samples'] == 2540
    assert declaration['quantization'] == 'numpy.rint / signed12 / no global scaling'
    json.dumps(declaration, allow_nan=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.payload = b''


@pytest.mark.parametrize('kind', ['narrowband', 'pulse', 'compound'])
def test_interference_unchanged_when_bpsk_amplitude_changes(kind):
    S = module()
    common = dict(kind=kind, tone_amplitude=20, pulse_amplitude=20)
    high = iq(S.generate_scenario(S.ScenarioConfig(amplitude=60, **common)))
    low = iq(S.generate_scenario(S.ScenarioConfig(amplitude=40, **common)))
    clean_high = iq(S.generate_scenario(S.ScenarioConfig(amplitude=60)))
    clean_low = iq(S.generate_scenario(S.ScenarioConfig(amplitude=40)))
    assert np.array_equal(high-clean_high, low-clean_low)
    assert np.max(np.abs(high)) <= 100


def test_frequency_is_integer_period_bin_and_declares_rounding():
    S = module()
    a = S.generate_scenario(S.ScenarioConfig(kind='narrowband', tone_frequency_hz=6100000))
    d = a.declaration.to_dict()
    assert d['tone_frequency_requested_hz'] == 6100000
    assert d['tone_frequency_actual_hz'] == d['tone_bin']*60000000/2540
    j = iq(a)-iq(S.generate_scenario(S.ScenarioConfig()))
    z = j[:, 0]+1j*j[:, 1]
    assert np.argmax(np.abs(np.fft.fft(z))) == d['tone_bin']


@pytest.mark.parametrize('kwargs', [dict(amplitude=True), dict(amplitude=2048),
    dict(amplitude=101), dict(tone_amplitude=-1), dict(pulse_width=0),
    dict(pulse_start=2530, pulse_width=80), dict(prbs7_seed=0), dict(seed=True),
    dict(tone_frequency_hz=float('nan')), dict(tone_frequency_hz=30000000),
    dict(tone_phase_rad=float('inf')), dict(kind='unknown'),
    dict(kind='compound', tone_amplitude=40, pulse_amplitude=40)])
def test_invalid_or_unsafe_config_rejected_without_auto_scaling(kwargs):
    S = module()
    with pytest.raises(ValueError):
        S.generate_scenario(S.ScenarioConfig(**kwargs))


def test_pulse_location_and_components_truth_off_explicit():
    S = module()
    item = S.generate_scenario(S.ScenarioConfig(kind='pulse', pulse_start=620, pulse_width=80))
    j = iq(item)-iq(S.generate_scenario(S.ScenarioConfig()))
    assert np.array_equal(np.flatnonzero(j[:,0]), np.arange(620,700))
    assert not np.any(j[:,1])
    assert not np.any(iq(S.generate_scenario(S.ScenarioConfig(kind='off'))))
