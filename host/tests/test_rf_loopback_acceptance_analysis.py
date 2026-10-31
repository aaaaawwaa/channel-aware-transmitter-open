"""A loopback verdict must reject noise, a wrong tone and clipped records."""
import numpy as np
from scripts.rf_loopback_acceptance import analyze


def fixture(frequency=1875000, amplitude=500):
    rng = np.random.RandomState(19)
    n = np.arange(4096)
    records = []
    for tag in ('off-before', 'on-1', 'on-2', 'on-3', 'off-after'):
        samples = rng.normal(0, 2, (4096, 2))
        if tag.startswith('on-'):
            samples[:, 0] += amplitude * np.sin(2*np.pi*frequency*n/60000000)
        records.append(dict(tag=tag, iq=np.round(samples).astype(int).tolist(),
            header=dict(sample_rate_hz=60000000, ratio=0)))
    return dict(acquisition_complete=True, records=records,
                criteria=dict(frequency_tolerance_hz=30000, local_floor_db=20,
                              on_off_db=10, frames=3, clipping=0))


def test_correct_signal_passes_and_controls_reject_false_evidence():
    assert analyze(fixture())['passed']
    assert not analyze(fixture(amplitude=0))['passed']
    assert not analyze(fixture(frequency=2250000))['passed']
    clipped = fixture()
    clipped['records'][1]['iq'][0][0] = 2047
    assert not analyze(clipped)['passed']


def test_incomplete_capture_cannot_pass():
    result = analyze(dict(acquisition_complete=False, error='TIMEOUT', criteria={}))
    assert not result['passed'] and result['error'] == 'TIMEOUT'
