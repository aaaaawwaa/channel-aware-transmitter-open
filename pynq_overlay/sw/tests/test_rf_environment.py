"""Receive-only fixed-window rules, independent truth and baseline provenance."""
import dataclasses
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_rf_bpsk_receiver import frame, signal
from rf_bpsk_pipeline import freeze_demod_config, analyze_demod
from rf_teaching_profile import teaching_capability

RF = dict(teaching_capability().rf_parameters)
DRAFT = dict(expected_mode='BPSK', symrate_hz=3000000, shaping='RECT',
    reference_enabled=False, reference_seed=1, decim=1, rx_len=4096, input_fs_hz=60000000)


def module():
    assert importlib.util.find_spec('rf_environment'), 'S2 receive diagnostic missing'
    import rf_environment
    return rf_environment


def envelope(f, **changes):
    return analyze_demod(f, freeze_demod_config(dict(DRAFT, **changes), 9))


def baseline():
    E = module()
    frames = [dataclasses.replace(frame(signal(offset=i*7, phase=i*.4, cfo=i*1000)),
        capture_id='train-%d'%i) for i in range(6)]
    sources = [dict(iq_sha256=hashlib.sha256(f.payload).hexdigest(),
        session_id='session-%d'%i, source_id=f.capture_id, split='train') for i,f in enumerate(frames)]
    return E.fit_baseline(frames, [envelope(f) for f in frames], RF, sources)


def result(z, b=None, **changes):
    E = module()
    f = frame(z)
    return E.analyze_environment(f, envelope(f, **changes), RF, b)


def test_missing_baseline_is_unknown_and_result_is_frozen_bounded_json():
    r = result(signal())
    assert r.label == 'UNKNOWN' and r.reason == 'BASELINE_MISSING'
    assert r.submitted_revision == 9
    d = r.to_dict()
    assert d['features']['window_us'] == pytest.approx(4096/60.)
    assert len(json.dumps(d, allow_nan=False)) < 6000
    assert 'payload' not in d and 'spectrum' not in d
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.label = 'CLEAR'


def test_baseline_roundtrip_canonical_hash_and_provenance_reject_tamper():
    E = module()
    b = baseline()
    d = b.to_dict()
    restored = E.EnvironmentBaseline.from_dict(json.loads(json.dumps(d)))
    assert b.sha256 == restored.sha256 and len(b.sha256) == 64
    assert len(json.dumps(d)) < 131072
    assert all(s['split']=='train' for s in d['sources'])
    d['thresholds']['pulse_crest'] += 1
    with pytest.raises(ValueError):
        E.EnvironmentBaseline.from_dict(d)


def test_frozen_baseline_sha_is_computed_once_and_replace_recomputes(monkeypatch):
    E=module();b=baseline();digest=b.sha256
    original=E._digest;calls=[]
    def track(value):
        calls.append(value);return original(value)
    monkeypatch.setattr(E,'_digest',track)
    assert all(b.sha256==digest for _ in range(100))
    assert calls==[]
    changed=dataclasses.replace(b,calibration_status='frozen')
    assert changed.sha256!=digest and len(calls)==1


@pytest.mark.parametrize('offset', [0, 7, 31, 620, 1260])
@pytest.mark.parametrize('phase,cfo,scale', [(0,0,1),(.7,40000,1.25),(-1,-70000,.7)])
def test_normal_prbs_lines_phase_cfo_scale_do_not_false_alarm(offset, phase,cfo,scale):
    r = result(signal(offset, phase,cfo)*scale, baseline())
    assert r.label == 'CLEAR', r.to_dict()


def test_receive_features_find_tone_pulse_and_compound_without_truth():
    E = module()
    b = baseline()
    n = np.arange(4096)
    clean = signal()
    tone = 330*np.exp(2j*np.pi*6000000*n/60000000)
    pulse = np.zeros(4096, complex); pulse[600:680] = 330
    narrow = result(clean+tone, b)
    impulsive = result(clean+pulse, b)
    combined = result(clean+tone+pulse, b)
    assert narrow.label == 'NARROWBAND', narrow.to_dict()
    assert impulsive.label == 'PULSE', impulsive.to_dict()
    assert combined.label == 'UNKNOWN' and combined.reason == 'COMPOUND_RULES', combined.to_dict()
    assert dict(impulsive.features)['pulse_longest_samples'] >= 3
    assert dict(impulsive.features)['pulse_sample_fraction'] < .1
    assert 'tone_excess' in dict(narrow.features)


def test_strong_narrowband_can_report_feature_even_when_receiver_loses_sync():
    b = baseline(); n=np.arange(4096)
    r = result(signal()+900*np.exp(2j*np.pi*6000000*n/60000000),b)
    assert r.label == 'NARROWBAND'


def test_dense_amplitude_variation_not_pulse_or_clear():
    z = signal(); z[1024:3072] *= 1.8
    r = result(z,baseline())
    assert r.label == 'UNKNOWN', r.to_dict()


@pytest.mark.parametrize('z,reason', [(np.zeros(4096,complex),'NO_ENERGY'),
    (signal()*.02,'LOW_ENERGY')])
def test_low_energy_never_clear(z,reason):
    r = result(z,baseline())
    assert r.label == 'INVALID' and r.reason == reason


def test_code_rails_unsupported_rf_or_rx_and_all_identity_fields_refuse():
    E=module();b=baseline();f=frame(signal());e=envelope(f)
    z=signal();z[20]=2047
    assert result(z,b).reason=='CLIPPED'
    assert result(signal(),b,symrate_hz=2000000).label=='INVALID'
    for key,value in RF.items():
        changed=dict(RF);changed[key]=value+1
        assert E.analyze_environment(f,e,changed,b).reason=='RF_DOMAIN_MISMATCH'
    for field,value in [('capture_id','wrong'),('release_id','wrong'),
        ('connection_generation',20),('config_revision',20),('completed_at_monotonic',101.)]:
        bad=dataclasses.replace(e,result=dataclasses.replace(e.result,**{field:value}))
        assert E.analyze_environment(f,bad,RF,b).reason=='IDENTITY_MISMATCH'
    bad=dataclasses.replace(e,algorithm_version='wrong')
    assert E.analyze_environment(f,bad,RF,b).reason=='ALGORITHM_MISMATCH'


def test_noise_qpsk_or_failed_receiver_cannot_clear_and_reference_has_no_class_authority():
    b=baseline();rng=np.random.RandomState(7)
    z=300*(rng.normal(size=4096)+1j*rng.normal(size=4096))
    assert result(z,b).label=='UNKNOWN'
    z=signal()+1j*np.roll(signal(),620)
    assert result(z,b).label=='UNKNOWN'
    E=module();f=frame(signal());e=envelope(f)
    failed=dataclasses.replace(e,result=dataclasses.replace(e.result,carrier_sync=False))
    assert E.analyze_environment(f,failed,RF,b).label=='UNKNOWN'
    a=result(signal(),b,reference_enabled=True,reference_seed=1)
    c=result(signal(),b,reference_enabled=True,reference_seed=37)
    assert a.label==c.label=='CLEAR' and a.features==c.features


def test_baseline_fit_refuses_test_inputs_false_sha_or_unqualified_normals():
    E=module();f=frame(signal());source=dict(iq_sha256=hashlib.sha256(f.payload).hexdigest(),
        session_id='session',source_id='source',split='test')
    with pytest.raises(ValueError): E.fit_baseline([f],[envelope(f)],RF,[source])
    source['split']='train';source['iq_sha256']='0'*64
    with pytest.raises(ValueError): E.fit_baseline([f],[envelope(f)],RF,[source])


def test_nested_baseline_fields_reject_mutable_or_duplicate_pairs():
    b=baseline()
    with pytest.raises(ValueError):
        dataclasses.replace(b,thresholds=tuple(list(pair) for pair in b.thresholds))
    with pytest.raises(ValueError):
        dataclasses.replace(b,thresholds=b.thresholds+(b.thresholds[0],))
    with pytest.raises(ValueError):
        dataclasses.replace(b,sources=(tuple(list(pair) for pair in b.sources[0]),))


def test_nonfinite_identity_is_refused_without_emitting_nan_json():
    E=module();f=dataclasses.replace(frame(signal()),completed_at_monotonic=float('nan'))
    r=E.analyze_environment(f,envelope(f),RF,baseline())
    assert r.label=='INVALID'
    assert r.completed_at_monotonic is None
    json.dumps(r.to_dict(),allow_nan=False)


def test_physical_code_limit_scenarios_are_receive_diagnosed_without_declarations():
    from rf_experiment_scenarios import ScenarioConfig,generate_scenario
    b=baseline();E=module()
    for kind,expected in [('normal','CLEAR'),('narrowband','NARROWBAND'),
                          ('pulse','PULSE'),('compound','UNKNOWN'),('off','INVALID')]:
        tx=generate_scenario(ScenarioConfig(kind=kind))
        samples=np.frombuffer(tx.payload,'<i2').reshape(-1,2)
        samples=np.tile(samples,(2,1))[:4096]*8
        f=frame(samples[:,0]+1j*samples[:,1])
        r=E.analyze_environment(f,envelope(f),RF,b)
        assert r.label==expected,r.to_dict()
