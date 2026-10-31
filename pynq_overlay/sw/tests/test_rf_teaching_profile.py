"""Teaching presets describe one tested domain and only edit local drafts."""
import importlib
import sys
from pathlib import Path
import pytest
import test_ui_view as H
import test_rf_touch_model as RF

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def profile():
    assert importlib.util.find_spec('rf_teaching_profile'), 'teaching profile is not implemented'
    return importlib.import_module('rf_teaching_profile')


def available(app):
    app.set_teaching_status(dict(available=True, release_id='release',
        module_sha256={name: 'a'*64 for name in ('rf_teaching_profile.py',
        'rf_rx_constellation.py','rf_experiment_record.py')}, reason=''))


def test_capability_is_immutable_explicit_and_reference_off():
    P=profile(); c=P.teaching_capability()
    assert (c.device,c.mode,c.symbol_rate_hz,c.shaping,c.input_fs_hz,c.decim,c.rx_len) == (
        'AD9361','BPSK',3000000,'RECT',60000000,1,4096)
    assert c.reference_enabled is False
    with pytest.raises((AttributeError,TypeError)):
        c.decim=2
    copied=c.to_dict();copied['mode']='QPSK'
    assert P.teaching_capability().mode=='BPSK'


def test_apply_uses_setters_clears_load_and_measurements_without_control():
    P=profile();a=RF.loaded();available(a)
    old_awg=a.draft.copy()
    calls=[]
    for name in ('set_rf_parameter','set_rf_signal_kind','set_rf_digital_parameter','set_rx_demod_setting'):
        original=getattr(a,name)
        def observe(*args,_original=original,_name=name):
            calls.append((_name,args));return _original(*args)
        setattr(a,name,observe)
    assert a.apply_teaching_profile() is None
    assert a.selected_device=='AD9361' and not a._rf_tx_loaded
    assert a.rx_demod_result is None and a.rx_analysis is None
    assert a.rf_parameter_values()['attenuation_mdB']==P.ATTENUATION_MDB==46000
    import ui_model as UM
    assert UM.RX_LOOPBACK_ATTENUATION_MDB==P.ATTENUATION_MDB, 'the panel pin drifted from the single source'
    assert a.rf_parameter_values()['idelay_tap']==29
    assert a.rf_digital_values()['amplitude']==60
    assert a.rf_digital_values()['symbol_rate_hz']==3000000
    assert a.rx_draft['reference_enabled'] is False
    assert a.draft==old_awg
    assert {name for name,args in calls}=={'set_rf_parameter','set_rf_signal_kind',
        'set_rf_digital_parameter','set_rx_demod_setting'}
    assert a.rf_control['pending_action'] is None


def test_busy_preset_refuses_before_any_draft_mutation():
    profile();a=RF.loaded();available(a)
    a.set_rf_status(RF.snapshot(state='TX_ACTIVE',selected=True))
    before=(a.rf_parameter_values(),a.rf_digital_values(),dict(a.rx_draft))
    assert a.apply_teaching_profile() is not None
    assert before==(a.rf_parameter_values(),a.rf_digital_values(),dict(a.rx_draft))


def test_teaching_button_hit_is_local_and_advanced_tx_is_preserved():
    profile();a=H.M.UiApp();available(a);a.page=H.M.PAGE_RX;a.rx_subpage='constellation'
    events=[];v=H.V.UiView(H.FakeLcd(),a,on_rx=events.append)
    v.render()
    assert v.teaching_profile_btn.visible and v.teaching_profile_btn.enabled
    H.tap_at(v,*H.center(v.teaching_profile_btn))
    assert events==[] and a.rf_digital_values()['mode']=='BPSK'
    H.tap_at(v,*H.center(v.teaching_advanced_btn))
    assert a.page==H.M.PAGE_DIGITAL
    # QAM64 已移出本次交付支持范围：配置入口显式拒绝、且不静默改写草稿/不回落其他模式。
    why = a.set_rf_digital_parameter('mode','QAM64')
    assert why is not None and 'QAM64' in why
    assert a.rf_digital_values()['mode'] == 'BPSK'
    # 交付模式 BPSK/QPSK/QAM16 仍可正常设置（高级 TX 草稿保留）。
    assert a.set_rf_digital_parameter('mode','QAM16') is None
    assert a.set_rf_digital_parameter('filter','RRC') is None
    assert a.rf_digital_values()['mode'] == 'QAM16'


def test_old_contract_disables_teaching_without_importing_modules():
    a=H.M.UiApp();a.page=H.M.PAGE_RX;a.rx_subpage='constellation'
    assert hasattr(a,'teaching_status'), 'teaching availability is not implemented'
    v=H.V.UiView(H.FakeLcd(),a);v.render()
    assert not v.teaching_profile_btn.enabled
