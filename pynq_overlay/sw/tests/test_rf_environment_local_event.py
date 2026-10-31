"""Sparse tone residuals and boundary-truncated events have different support."""
import numpy as np
import pytest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rf_environment import _local_event_group


@pytest.mark.parametrize('indices', [np.arange(0, 320, 40),
    np.array([0, 1, 2, 3, 4, 5, 6, 40]),
    np.r_[np.arange(7), 100 + np.arange(0, 100, 10)]])
def test_separated_or_individually_insufficient_groups_cannot_combine(indices):
    assert _local_event_group(indices, 8, .30) is None


def test_local_density_boundary_and_truncated_end_event():
    assert _local_event_group(np.r_[np.arange(7), 26], 8, .30) is None
    assert np.array_equal(_local_event_group(np.r_[np.arange(7), 25], 8, .30), np.r_[np.arange(7), 25])
    assert np.array_equal(_local_event_group(np.arange(4088, 4096), 8, .30), np.arange(4088, 4096))


def test_exact_count_and_density_endpoints():
    assert _local_event_group(np.arange(7), 8, .30) is None
    assert np.array_equal(_local_event_group(np.arange(8), 8, .30), np.arange(8))
    at_boundary=np.r_[np.arange(8),29]  # 9 / 30 == .300
    assert _local_event_group(at_boundary,8,.301) is None
    assert np.array_equal(_local_event_group(at_boundary,8,.300),at_boundary)


@pytest.mark.parametrize('key,value', [('pulse_after_tone_min_samples',0),
    ('pulse_after_tone_min_samples',4097), ('pulse_after_tone_min_density',0),
    ('pulse_after_tone_min_density',1.01), ('pulse_plateau_min_samples',0),
    ('pulse_plateau_min_samples',4097), ('pulse_plateau_min_density',0),
    ('pulse_plateau_min_density',1.01)])
def test_new_baseline_thresholds_reject_missing_or_out_of_bounds(key,value):
    import dataclasses
    from test_rf_environment import baseline
    b=baseline();thresholds=dict(b.thresholds);thresholds[key]=value
    with pytest.raises(ValueError):dataclasses.replace(b,thresholds=tuple(sorted(thresholds.items())))
    thresholds.pop(key)
    with pytest.raises(ValueError):dataclasses.replace(b,thresholds=tuple(sorted(thresholds.items())))


def test_dense_plateau_has_independent_count_and_density_limits():
    assert _local_event_group(np.arange(15),16,.35) is None
    assert np.array_equal(_local_event_group(np.arange(16),16,.35),np.arange(16))
    dispersed=np.arange(0,48,3) # count16 / span46 < .35
    assert _local_event_group(dispersed,16,.35) is None
    assert _local_event_group(np.r_[np.arange(15),50],16,.35) is None


@pytest.mark.parametrize('scale', [1.45, 1.50])
def test_moderate_broad_plateau_never_clear(scale):
    from test_rf_environment import module, envelope, RF, frame, signal
    import json
    E=module()
    # The stored envelope/threshold/source numbers are reused as an arithmetic
    # fixture for the plateau rule only.  The shipped file is now the 46 dB
    # provisional development baseline (M2 bootstrap, see the two tests below), so the
    # fixture is built exactly the way the production loader builds it.
    stored=json.loads((Path(__file__).resolve().parents[1]/'rf_environment_baseline.json').read_text())
    b=E.EnvironmentBaseline(tuple(stored['spectrum_envelope']),
        tuple(sorted(stored['thresholds'].items())),
        tuple(tuple(sorted(source.items())) for source in stored['sources']))
    z=signal();z[1000:1800]*=scale
    f=frame(z);e=envelope(f)
    r=E.analyze_environment(f,e,RF,b)
    assert dict(r.rule_evidence)['bpsk_reliable']
    assert dict(r.features)['pulse_plateau_sample_fraction']>.1
    assert r.label=='UNKNOWN',r.to_dict()


PROVISIONAL_CANONICAL_SHA256='6b697e0e63f7ab4fd1cd06da324128ebb625dbc81c4acf29048014c83549d6bf'
PROVISIONAL_SOURCE_FRAME_IQ_SHA256=    'd435eea79ed64d56ee62f8fd38f95f6487722e89a2fa25872fafaf73008e302a'
ARCHIVED_40DB_BASELINE=(Path(__file__).resolve().parents[3]/'evidence'/'rf-teaching'/
    's2-46db-m2-bootstrap-2026-10-17'/'work'/'baseline-archive'/
    'rf_environment_baseline.v3-40db.721baa769743.json')


def test_the_shipped_provisional_baseline_loads_but_is_not_a_formal_acceptance_baseline():
    """The M2 bootstrap baseline is loadable and labelled, and cannot serve acceptance.

    The frozen schema admits exactly the eight body keys plus sha256, so the provisional
    labels (provisional / n=1 / not_for_formal_acceptance / source_frame_sha256) live in
    the evidence record and in the campaign receipt, never in this file.  What keeps the
    bootstrap out of formal acceptance is asserted here: it stays development, and it has
    one source -- a real 46 dB research frame, not a TRAIN campaign -- so it can never
    satisfy the formal TRAIN provenance a real baseline has to be fitted from.
    """
    from test_rf_environment import module
    import json
    E=module()
    stored=json.loads((Path(__file__).resolve().parents[1]/'rf_environment_baseline.json').read_text())
    assert set(stored)=={'schema_version','environment_version','algorithm_version',
        'calibration_status','rf_config','spectrum_envelope','thresholds','sources','sha256'}
    assert stored['rf_config']['attenuation_mdB']==46000
    assert stored['environment_version']==E.VERSION
    loaded=E.EnvironmentBaseline.from_dict(stored)
    assert loaded.sha256==stored['sha256']==PROVISIONAL_CANONICAL_SHA256
    assert loaded.calibration_status=='development'
    assert loaded.calibration_status!='frozen'
    assert len(loaded.sources)==1
    source=dict(loaded.sources[0])
    assert source['iq_sha256']==PROVISIONAL_SOURCE_FRAME_IQ_SHA256
    assert source['split']=='train'  # literal schema requirement, not a TRAIN campaign
    assert dict(loaded.rf_config)==dict(E.FIXED_RF)
    # The labels cannot be smuggled into the file: an annotated variant is refused.
    with pytest.raises(ValueError,match='baseline schema fields mismatch'):
        E.EnvironmentBaseline.from_dict(dict(stored,provisional=True))


def test_the_archived_historical_40db_baseline_is_still_refused():
    """The bytes the bootstrap replaced are archived byte-identically and still refused."""
    from test_rf_environment import module
    import json
    E=module()
    assert ARCHIVED_40DB_BASELINE.is_file(),ARCHIVED_40DB_BASELINE
    stored=json.loads(ARCHIVED_40DB_BASELINE.read_text(encoding='utf-8'))
    assert stored['rf_config']['attenuation_mdB']==40000
    assert stored['environment_version']=='rf-window-environment-v3'
    with pytest.raises(ValueError,match='unsupported frozen baseline version'):
        E.EnvironmentBaseline.from_dict(stored)


@pytest.mark.parametrize('indices,expected', [
    (np.arange(15),'CLEAR'),(np.arange(16),'PULSE'),
    (np.arange(256),'PULSE'),(np.arange(257),'UNKNOWN'),
    (np.arange(409),'UNKNOWN'),(np.arange(410),'UNKNOWN'),
    (np.r_[np.arange(200),1000+np.arange(209)],'PULSE'),
    (np.r_[np.arange(200),1000+np.arange(210)],'UNKNOWN'),
    (np.arange(0,48,3),'CLEAR'),
    (np.r_[np.arange(14),39],'CLEAR'),
    (np.r_[np.arange(15),39],'PULSE'),
    (np.r_[np.arange(8),1000+np.arange(8)],'CLEAR'),
    (np.r_[np.arange(0,300,2),1000+np.arange(16)],'UNKNOWN')])
def test_plateau_count_density_span_and_share_boundaries(monkeypatch,indices,expected):
    from test_rf_environment import module, baseline, envelope, RF, frame, signal
    E=module();b=baseline();f=frame(signal());e=envelope(f)
    # Controlled receive features isolate the support gates from DSP noise.
    import dataclasses
    thresholds=dict(b.thresholds);thresholds.update(pulse_crest=1.8,pulse_outlier=1.8,
                                                    pulse_plateau_outlier=1.3)
    b=dataclasses.replace(b,thresholds=tuple(sorted(thresholds.items())))
    normalized=np.ones(4096);normalized[indices]=1.4
    features=dict(window_us=4096/60.,rms_codes=300.,crest_ratio=1.1,
                  q99_ratio=1.1,q999_ratio=1.1,tone_concentration=0.)
    monkeypatch.setattr(E,'_features',lambda _: (dict(features),np.zeros(4096),normalized,None))
    r=E.analyze_environment(f,e,RF,b)
    assert r.label==expected,r.to_dict()
