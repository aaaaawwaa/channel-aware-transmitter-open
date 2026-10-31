"""Development compound preset reserves peak headroom without relaxing RX refusal."""
from test_rf_experiment_scenarios import module,iq


def test_default_compound_preserves_bpsk_and_reserves_peak_headroom():
    S=module();item=S.generate_scenario(S.ScenarioConfig(kind='compound'))
    d=item.declaration_dict()
    assert d['amplitude']==60
    assert d['tone_amplitude']==d['pulse_amplitude']==15
    assert d['amplitude']+d['tone_amplitude']+d['pulse_amplitude']==90
    assert abs(iq(item)).max()<=90
