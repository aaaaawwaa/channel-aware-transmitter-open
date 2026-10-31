import pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import ui_model as M
OPS=tuple(M.RF_CONTROL_OP.values())+('tx.load','tx.start')
PARAMS={'lo_hz':80000000,'sample_rate_hz':60000000,'attenuation_mdB':0,'phy_mode_1r1t':0,'rx_bandwidth_hz':30000000,'tx_bandwidth_hz':30000000,'idelay_en':1,'idelay_tap':7}
def snapshot(state='READY',ready=True,selected=False,stale=False):
    return {'online':True,'mode':'status_only','armed':True,'core':True,'control_ops':OPS,'daemon_state':state,'tx_session_ready':ready,'stale':stale,'pl_status':{'io_ok':True,'tx':{'source_selected':selected,'length_words':1024,'position_words':100}}}
def configured():
    a=M.UiApp();a.set_rf_status(snapshot(ready=False))
    for k,v in PARAMS.items():assert a.set_rf_parameter(k,v) is None
    a.set_rf_control_result('configure',{'ok':True,'args':PARAMS})
    return a
def loaded():
    a=configured();a.set_rf_parameter('tx_cycles',32);a.set_rf_parameter('tx_amplitude',1500)
    a.set_rf_status(snapshot())
    a.set_rf_control_result('load',{'ok':True,'args':{'cycles':32,'amplitude':1500}})
    return a
def test_explicit_waveform_and_start_gates():
    a=configured()
    assert 'load' in a.rf_control['actions']
    assert a.rf_parameter_plan('load')[1] is not None
    assert 'start' not in a.rf_control['actions']
    a=loaded()
    assert a.rf_parameter_plan('load')==({'waveform':'sine','cycles':32,'amplitude':1500},None)
    assert a.rf_control_prompt('start')[:2]==(True,True)
    assert a.rf_control_block_reason('start') is None
def test_loaded_invalidated_by_draft_change_and_connection_generation():
    for name,value in [('tx_cycles',31),('lo_hz',81000000)]:
        a=loaded();assert 'start' in a.rf_control['actions']
        a.set_rf_parameter(name,value)
        assert 'start' not in a.rf_control['actions']
    a=loaded();a.set_rf_status(snapshot(ready=False))
    assert 'start' not in a.rf_control['actions']
def test_applied_config_not_unapplied_draft_defines_waveform_rate():
    a=loaded();assert a.rf_tone_frequency_hz()==1875000
    a.set_rf_parameter('sample_rate_hz',30000000)
    assert a.rf_parameter_plan('load')[1] is not None
    assert a.rf_tone_frequency_hz()==1875000
def test_real_fresh_pl_status_required_to_show_transmitting():
    a=loaded();a.set_rf_control_result('start',{'ok':True})
    assert a.rf_transmitting() is False
    a.set_rf_status(snapshot(state='TX_ACTIVE',selected=True))
    assert a.rf_transmitting() is True
    assert 'stop' in a.rf_control['actions']
    a.set_rf_status(snapshot(state='TX_ACTIVE',selected=True,stale=True))
    assert a.rf_transmitting() is False and 'start' not in a.rf_control['actions']
def test_active_edit_and_configure_blocked_stop_is_immediate():
    a=loaded();a.set_rf_status(snapshot(state='TX_ACTIVE',selected=True))
    assert a.set_rf_parameter('tx_amplitude',1000) is not None
    assert 'configure' not in a.rf_control['actions'] and 'load' not in a.rf_control['actions']
    assert a.rf_control_prompt('stop')[:2]==(True,False)
def test_success_cannot_recreate_loaded_after_post_control_poll_failure():
    a=loaded();a.set_rf_status(snapshot(ready=False))
    a.set_rf_control_result('load',{'ok':True})
    assert 'start' not in a.rf_control['actions']

def test_unknown_connection_evidence_invalidates_loaded():
    for evidence in (None, 0, 1, "true", "missing"):
        a=loaded(); status=snapshot()
        if evidence=="missing":status.pop("tx_session_ready")
        else:status["tx_session_ready"]=evidence
        a.set_rf_status(status)
        assert "start" not in a.rf_control["actions"]
