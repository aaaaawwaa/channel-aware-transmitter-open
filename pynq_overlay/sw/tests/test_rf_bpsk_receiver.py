"""Independent waveform/reference checks for the finite BPSK receiver."""
import dataclasses
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rf_rx_types import RxIqFrame, RxReadback
import rf_bpsk_receiver as R


def bits(n=127):
    state, out = 1, []
    for _ in range(n):
        out.append(state // 64)
        feedback = ((state // 64) ^ (state // 32)) & 1
        state = (state * 2 + feedback) % 128
    return np.array(out)


def frame(z, ratio=0, fs=60000000):
    iq = np.column_stack((np.round(z.real), np.round(z.imag))).astype('<i2')
    return RxIqFrame('test', iq.tobytes(), len(z), fs, ratio, 1, 2, 3, 4,
                     'release', 'rx.capture', False, RxReadback(True),
                     RxReadback(True, rx_armed=False, tx_source_selected=False), 100.)


def signal(offset=0, phase=0., cfo=0., noise=0., flips=()):
    n = np.arange(4096)
    symbols = (n + offset) // 20
    values = 1 - 2 * bits()[symbols % 127]
    values[np.isin(symbols, flips)] *= -1
    z = 500 * values * np.exp(1j * (phase + 2*np.pi*cfo*n/60000000))
    rng = np.random.RandomState(17)
    return z + noise*(rng.normal(size=4096)+1j*rng.normal(size=4096))


@pytest.mark.parametrize('offset', range(20))
@pytest.mark.parametrize('phase,cfo', [(0,0),(.7,40000),(np.pi/2,-70000),(np.pi,100000),
    (-.3,-100000),(-1.1,5000),(2.1,-20000),(2.8,80000)])
def test_recovers_unknown_sample_phase_carrier_and_pi_ambiguity(offset, phase, cfo):
    f = frame(signal(offset, phase, cfo))
    before = f.payload
    r = R.receive_bpsk(f, R.BpskConfig(reference_prbs7_seed=1))
    assert r.carrier_sync and r.timing_sync and r.reference_sync, r
    assert r.bit_errors == 0 and r.compared_bits >= 64
    assert r.ber == 0 and abs(r.cfo_hz-cfo) <= 1000
    assert f.payload == before
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ber = 1


def test_ber_counts_errors_in_separate_interval_without_realignment():
    clean = R.receive_bpsk(frame(signal()), R.BpskConfig(reference_prbs7_seed=1))
    damaged = R.receive_bpsk(frame(signal(flips=(100,110,120))), R.BpskConfig(reference_prbs7_seed=1))
    assert damaged.reference_sync and damaged.bit_errors == 3
    assert damaged.compared_bits == clean.compared_bits
    assert damaged.ber == 3 / damaged.compared_bits
    assert damaged.alignment_bits == 63


def test_without_reference_decisions_do_not_claim_ber():
    r = R.receive_bpsk(frame(signal(7,.8,10000)), R.BpskConfig())
    assert r.carrier_sync and r.timing_sync
    assert r.reference_sync is None and r.ber is None and r.compared_bits is None
    assert len(r.bits_preview) <= 64


@pytest.mark.parametrize('kind', ['off','tone','noise','qpsk','clipped'])
def test_non_bpsk_quality_negatives_never_claim_reference_ber(kind):
    n = np.arange(4096)
    rng = np.random.RandomState(24)
    z = {'off':np.zeros(4096,complex),
         'tone':500*np.exp(2j*np.pi*50000*n/60000000),
         'noise':300*(rng.normal(size=4096)+1j*rng.normal(size=4096)),
         'qpsk':500*(1-2*bits()[n//20%127])+500j*(1-2*bits()[(n//20+31)%127]),
         'clipped':signal()}[kind]
    if kind=='clipped': z[10]=2047
    r = R.receive_bpsk(frame(z),R.BpskConfig(reference_prbs7_seed=1))
    assert not r.reference_sync and r.ber is None, r


@pytest.mark.parametrize('config', [R.BpskConfig(mode='QPSK'),R.BpskConfig(shaping='RRC'),
                                    R.BpskConfig(symbol_rate_hz=2000000),R.BpskConfig(reference_prbs7_seed=0)])
def test_unsupported_config_refuses(config):
    r = R.receive_bpsk(frame(signal()),config)
    assert r.reason and r.ber is None


@pytest.mark.parametrize('kwargs', [{'ratio':1},{'fs':30000000}])
def test_actual_frame_metadata_cannot_be_overridden(kwargs):
    assert R.receive_bpsk(frame(signal(),**kwargs),R.BpskConfig()).reason=='UNSUPPORTED'


def test_short_frame_and_signed12_validation():
    assert R.receive_bpsk(frame(signal()[:100]),R.BpskConfig()).ber is None
    z=signal();z[20]=3000
    assert R.receive_bpsk(frame(z),R.BpskConfig()).reason=='INVALID_IQ'


def test_awgn_iq_swap_and_different_valid_seed():
    z=signal(13,.4,-20000,noise=30)
    a=R.receive_bpsk(frame(z.imag+1j*z.real),R.BpskConfig(reference_prbs7_seed=37))
    assert a.reference_sync and a.ber==0
    assert abs(a.cfo_hz-20000)<1000


def test_bandlimited_zero_crossings_do_not_accumulate_phase_unwrap_cycles():
    # Analogue filtering sends transitions through zero; noisy per-sample square
    # phase has cycle slips even when most samples have a coherent carrier.
    n=np.arange(4096)
    base=signal(13).real
    base=np.convolve(base,np.hanning(9)/np.hanning(9).sum(),mode='same')
    rng=np.random.RandomState(20)
    z=base*np.exp(1j*(.9+2*np.pi*40000*n/60000000))
    z+=35*(rng.normal(size=4096)+1j*rng.normal(size=4096))
    r=R.receive_bpsk(frame(z),R.BpskConfig(reference_prbs7_seed=1))
    assert r.reference_sync and r.ber==0, r
    assert abs(r.cfo_hz-40000)<1000


def test_fractional_sample_origin_and_out_of_range_cfo():
    z=signal(8,.5,-60000)
    n=np.arange(len(z))
    shifted=np.interp(n+.4,n,z.real)+1j*np.interp(n+.4,n,z.imag)
    r=R.receive_bpsk(frame(shifted),R.BpskConfig(reference_prbs7_seed=1))
    assert r.reference_sync and r.ber==0
    assert R.receive_bpsk(frame(signal(cfo=200000)),R.BpskConfig()).reason=='CFO_OUT_OF_RANGE'


def test_qam_and_bad_alignment_do_not_create_a_ber():
    rng=np.random.RandomState(10)
    qam=100*(rng.choice([-3,-1,1,3],205)+1j*rng.choice([-3,-1,1,3],205))
    r=R.receive_bpsk(frame(np.repeat(qam,20)[:4096]),R.BpskConfig(reference_prbs7_seed=1))
    assert not r.reference_sync and r.ber is None
    # Payload contains BPSK signs but no PRBS prefix. Carrier/timing still work.
    symbols=rng.choice([-500,500],205)
    r=R.receive_bpsk(frame(np.repeat(symbols,20)[:4096].astype(complex)),R.BpskConfig(reference_prbs7_seed=1))
    assert r.carrier_sync and r.timing_sync and r.reason=='REFERENCE_NOT_SYNCED'
    assert r.ber is None
