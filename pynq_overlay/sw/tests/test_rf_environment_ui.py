"""Environment scene intent is a draft; loading and RF capture remain explicit."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_rf_environment_pipeline as P
import test_ui_view as VIEW
import rf_experiment_scenarios as SCENES
import pynq_status as PANEL


def test_scene_selection_only_invalidates_and_load_stays_explicit(tmp_path):
    c,w,a,f=P.prepared(tmp_path)
    try:
        P.capture(w,a)
        a._rf_parameters.update(a._rf_applied_parameters)
        assert a.set_environment_scene('pulse') is None
        assert a._rf_signal_draft_kind=='environment'
        assert not a._rf_tx_loaded and not a.rx_environment_summary()['current']
        args,why=a.rf_parameter_plan('load')
        assert why is None and args['scenario']['kind']=='pulse'
        assert not c.sent if hasattr(c,'sent') else True
    finally:w.stop()


def test_scene_load_worker_separates_wire_metadata_and_truth(tmp_path):
    c,w,a,f=P.prepared(tmp_path);sent=[]
    try:
        w._scenario_module=SCENES
        c.control_op=lambda op,meta,payload=None: sent.append((op,meta,payload)) or {}
        reply=w.control('load',args={'waveform':'environment','scenario':{'kind':'pulse'}})
        assert reply['ok'] and reply['scenario_declaration']['truth']=='pulse'
        assert sent[0][0]=='tx.load' and 'truth' not in sent[0][1]
        assert len(sent[0][2])==2540*4
    finally:w.stop()


def test_view_environment_tab_has_manual_loading_and_current_diagnostic(tmp_path):
    c,w,a,f=P.prepared(tmp_path)
    try:
        P.capture(w,a);a.rx_subpage='environment'
        a.page=VIEW.M.PAGE_FUTURE
        v=VIEW.V.UiView(VIEW.FakeLcd(),a)
        v.render()
        assert len(v.rx_tab_btns)==6
        assert v.environment_scene_btns[0].visible
        assert v.environment_load_btn.visible
        assert v.rx_capture_btn.visible and v.rx_loopback_btn.visible
        assert v.rx_save_btn.visible and v.rx_save_history_btn.visible
        assert v.rx_constellation_plot.visible is False
        for widget in v.environment_scene_btns+[v.environment_load_btn,v.rx_capture_btn,
                v.rx_clear_btn,v.rx_loopback_btn,v.rx_save_btn,v.rx_save_history_btn]:
            if widget.enabled:
                assert v.ui._hit(*VIEW.center(widget)) is widget
    finally:w.stop()


def test_panel_freezes_applied_rf_config_for_diagnosis(tmp_path):
    c,w,a,f=P.prepared(tmp_path);calls=[]
    class Worker:
        def submit_capture(self,args,**kwargs):
            calls.append(kwargs);return {'queued':True}
    try:
        a._rf_parameters.update(a._rf_applied_parameters)
        PANEL._rx_action(a,Worker(),'capture')
        assert calls[0]['rf_config']==dict(P.ENV.FIXED_RF)
        assert calls[0]['rf_config'] is not a._rf_applied_parameters
    finally:w.stop()


def test_real_scene_load_first_hit_and_cancel_send_nothing_second_hit_sends_once(tmp_path):
    c,w,a,f=P.prepared(tmp_path);calls=[]
    try:
        a._rf_parameters.update(a._rf_applied_parameters)
        assert a.set_environment_scene('pulse') is None
        a.page=VIEW.M.PAGE_FUTURE;a.rx_subpage='environment'
        v=VIEW.V.UiView(VIEW.FakeLcd(),a,on_rf_control=calls.append)
        v.render()
        VIEW.tap_at(v,*VIEW.center(v.environment_load_btn))
        assert calls==[]
        assert a.rf_control['pending_action']=='load'
        assert v.environment_cancel_btn.visible
        VIEW.tap_at(v,*VIEW.center(v.environment_cancel_btn))
        assert calls==[] and a.rf_control['pending_action'] is None
        VIEW.tap_at(v,*VIEW.center(v.environment_load_btn))
        VIEW.tap_at(v,*VIEW.center(v.environment_load_btn))
        assert calls==['load']
    finally:w.stop()


def test_environment_load_model_gate_requires_current_confirmation(tmp_path):
    c,w,a,f=P.prepared(tmp_path)
    try:
        a._rf_parameters.update(a._rf_applied_parameters)
        a.set_environment_scene('pulse')
        assert a.rf_control_block_reason('load') is not None
        assert a.rf_control_prompt('load')[:2]==(True,True)
        assert a.rf_control_block_reason('load') is None
        a.rf_control_cancel()
        assert a.rf_control_block_reason('load') is not None
    finally:w.stop()


def test_environment_page_shows_save_failure_and_load_refusal(tmp_path):
    c,w,a,f=P.prepared(tmp_path)
    try:
        a.page=VIEW.M.PAGE_FUTURE;a.rx_subpage='environment'
        v=VIEW.V.UiView(VIEW.FakeLcd(),a)
        a.rx_save_status={'state':'error','reason':'DISK FULL'}
        v.render()
        assert any('DISK FULL' in label[4] for label in v._static_labels)
        a.rx_save_status={};a.rf_control['last_error']='TEST LOAD ERROR'
        v.invalidate();v.render()
        assert any('TEST LOAD ERROR' in label[4] for label in v._static_labels)
    finally:w.stop()


# --------------------------------------------------- the declared scene range (D6)

def test_the_scene_range_is_the_controlled_scenario_module():
    """The picker may not keep a private list: it must equal the product's own."""
    assert VIEW.M.ENVIRONMENT_SCENES == SCENES.KINDS
    assert 'off' in VIEW.M.ENVIRONMENT_SCENES


def test_the_off_scene_is_selectable_and_reaches_the_load_plan(tmp_path):
    c,w,a,f=P.prepared(tmp_path)
    try:
        a._rf_parameters.update(a._rf_applied_parameters)
        assert a.set_environment_scene('off') is None
        assert a.environment_scene=='off'
        assert a._rf_signal_draft_kind=='environment'
        args,why=a.rf_parameter_plan('load')
        assert why is None and args==dict(waveform='environment',scenario=dict(kind='off'))
        a.page=VIEW.M.PAGE_FUTURE;a.rx_subpage='environment'
        v=VIEW.V.UiView(VIEW.FakeLcd(),a)
        v.render()
        assert v.environment_scene_kinds==SCENES.KINDS
        assert len(v.environment_scene_btns)==len(SCENES.KINDS)
        off=v.environment_scene_btns[-1]
        assert off.text=='OFF负控'
        assert off.visible and off.enabled
        assert v.ui._hit(*VIEW.center(off)) is off
        for widget in v.environment_scene_btns:
            assert v.ui._hit(*VIEW.center(widget)) is widget
    finally:w.stop()


def test_an_unknown_scene_is_still_refused(tmp_path):
    c,w,a,f=P.prepared(tmp_path)
    try:
        assert a.set_environment_scene('headroom')=='未知实验场景'
        assert a.environment_scene=='normal'
    finally:w.stop()
