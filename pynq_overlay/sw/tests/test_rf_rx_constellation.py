"""Real symbols come from same immutable IQ and frozen synchronization only."""
from dataclasses import replace, FrozenInstanceError
import numpy as np
import pytest

from test_rf_bpsk_receiver import frame, signal
from rf_bpsk_pipeline import DemodConfig, analyze_demod
from rf_bpsk_receiver import BpskConfig, VERSION


def fixture(seed=1, **signal_args):
    f=frame(signal(**signal_args))
    e=analyze_demod(f, DemodConfig(BpskConfig(reference_prbs7_seed=seed),7,1,4096,60000000))
    return f,e


def test_points_are_real_integration_not_ideal_and_ber_stays_unchanged():
    import rf_rx_constellation as C
    f,e=fixture(phase=.35,cfo=2500)
    before=e.result.to_dict()
    r=C.estimate_constellation(f,e)
    assert not r.reason and r.symbol_count==201 and len(r.points)==128
    assert r.capture_id==f.capture_id and r.submitted_revision==7
    assert r.algorithm_version==VERSION and r.normalization=='symbol_rms'
    iq=np.frombuffer(f.payload,dtype='<i2').reshape(-1,2)
    z=iq[:,0]+1j*iq[:,1]; z=z-z.mean()
    d=e.result; n=np.arange(4096)
    z=z*np.exp(-2j*np.pi*d.cfo_hz*n/60000000-1j*d.phase_mod_pi)
    # Independently integrate each already-chosen symbol and discard edges.
    values=np.array([sum(z[d.timing_offset_samples+i*20:d.timing_offset_samples+(i+1)*20])/20
        for i in range(1,d.raw_symbols-1)])
    values/=np.sqrt(np.mean(abs(values)**2))
    assert np.allclose(np.array(r.points),np.c_[values.real,values.imag][:128],atol=1e-12)
    assert e.result.to_dict()==before
    assert any(abs(abs(x)-1)>1e-5 or abs(y)>1e-5 for x,y in r.points)
    with pytest.raises(FrozenInstanceError): r.reason='changed'


def test_no_reference_still_has_real_points_without_ber():
    import rf_rx_constellation as C
    f,e=fixture(None)
    r=C.estimate_constellation(f,e)
    assert len(r.points)==128 and not r.reason
    assert e.result.ber is None


@pytest.mark.parametrize('field,value',[('capture_id','other'),('release_id','other'),
    ('connection_generation',999),('config_revision',999),('completed_at_monotonic',999.)])
def test_mismatched_demod_identity_refuses(field,value):
    import rf_rx_constellation as C
    f,e=fixture()
    r=C.estimate_constellation(f,replace(e,result=replace(e.result,**{field:value})))
    assert not r.points and r.reason=='RESULT_MISMATCH'


@pytest.mark.parametrize('field,value',[('cfo_hz',float('nan')),('phase_mod_pi',float('inf')),
    ('timing_offset_samples',True),('raw_symbols',10000),('total_symbols',999)])
def test_malformed_estimate_does_not_draw(field,value):
    import rf_rx_constellation as C
    f,e=fixture()
    r=C.estimate_constellation(f,replace(e,result=replace(e.result,**{field:value})))
    assert not r.points and r.reason=='INVALID_ESTIMATE'


def test_unsupported_and_unsynced_have_no_fake_points():
    import rf_rx_constellation as C
    f,e=fixture()
    for result in (replace(e.result,carrier_sync=False,reason='CARRIER_NOT_SYNCED'),
                   replace(e.result,timing_sync=False,reason='TIMING_NOT_SYNCED')):
        assert not C.estimate_constellation(f,replace(e,result=result)).points
    wrong=replace(e,submitted=replace(e.submitted,config=BpskConfig(shaping='RRC')))
    assert C.estimate_constellation(f,wrong).reason=='UNSUPPORTED'


def test_envelope_algorithm_identity_is_verified():
    import rf_rx_constellation as C
    f,e=fixture()
    assert not C.estimate_constellation(f,replace(e,algorithm_version='wrong')).points
