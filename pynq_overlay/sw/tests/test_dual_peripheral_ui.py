from test_ui_view import view, tap_at, center
from test_rf_touch_model import loaded, snapshot, PARAMS
import ui_model as M
import pynq_status as S


def test_selector_is_visible_and_only_changes_local_target():
    for page in (M.PAGE_TX, M.PAGE_TOOLS, M.PAGE_DIGITAL, M.PAGE_CHANNEL):
        calls = []
        app, _, v = view(page=page, on_apply=calls.append, on_rf_control=calls.append)
        assert getattr(app, 'selected_device', None) == 'ACM2108'
        v.render()
        tap_at(v, *center(v.device_buttons['AD9361']))
        assert app.selected_device == 'AD9361'
        assert calls == []
        assert v.device_buttons['AD9361'].visible


def test_switch_preserves_both_drafts_and_cancels_unsent_confirmation():
    app, _, v = view()
    assert hasattr(app, 'select_device')
    app.draft['awg'][0]['freq_hz'] = 123456
    app.set_rf_parameter('lo_hz', 81000000)
    app.rf_control['pending_action'] = 'init'
    assert app.select_device('AD9361') is None
    assert app.rf_control['pending_action'] is None
    app.set_rf_parameter('lo_hz', 82000000)
    assert app.select_device('ACM2108') is None
    assert app.draft['awg'][0]['freq_hz'] == 123456
    assert app.rf_parameter_values()['lo_hz'] == 82000000


def test_inflight_locks_target_but_unknown_state_only_blocks_hardware():
    app, _, v = view()
    assert hasattr(app, 'select_device')
    _, plan = app.begin(M.APPLY_AWG)
    assert app.select_device('AD9361') is not None
    assert app.selected_device == 'ACM2108'
    app.fail()
    assert app.select_device('AD9361') is None
    assert app.begin(M.APPLY_AWG)[1] is None
    app.set_rf_control_pending('configure')
    assert app.select_device('ACM2108') is not None


def test_rf_tools_cannot_accidentally_submit_awg_commands():
    calls = []
    app, _, v = view(page=M.PAGE_DIGITAL, on_apply=calls.append, on_toggle=calls.append)
    assert hasattr(app, 'select_device')
    app.select_device('AD9361')
    v.render()
    v._apply()
    v._toggle()
    assert calls == []
    assert app.begin(M.APPLY_DIGITAL)[1] is None


def test_rf_running_stop_remains_reachable_when_editing_acm():
    calls = []
    app, _, v = view(page=M.PAGE_TOOLS, on_rf_control=calls.append)
    prepared = loaded()
    app.__dict__.update(prepared.__dict__)
    app.page = M.PAGE_TOOLS
    app.set_rf_status(snapshot(state='TX_ACTIVE', selected=True))
    assert hasattr(app, 'select_device')
    app.select_device('ACM2108')
    v.render()
    assert v.rf_stop_btn.visible and v.rf_stop_btn.enabled
    tap_at(v, *center(v.rf_stop_btn))
    assert calls == ['stop']


def test_delayed_awg_result_stays_on_acm_and_plan_is_frozen():
    app, _, v = view()
    app.draft['awg'][0]['freq_hz'] = 123456
    _, plan = app.begin(M.APPLY_AWG)
    assert getattr(plan, 'device', None) == 'ACM2108'
    app.selected_device = 'AD9361'  # external navigation cannot reroute an existing transaction
    app.draft['awg'][0]['freq_hz'] = 222222
    app.set_rf_parameter('lo_hz', 81000000)
    assert plan.target['awg'][0]['freq_hz'] == 123456
    assert app.finish({'config':plan.target, 'actual_mode':'OFF'}) == M.OK
    assert app.observed['awg'][0]['freq_hz'] == 123456
    assert app.rf_parameter_values()['lo_hz'] == 81000000


def test_delayed_rf_configuration_is_a_copy_and_updates_rf_only():
    app, _, v = view()
    app.__dict__.update(loaded().__dict__)
    assert hasattr(app, 'select_device')
    app.select_device('AD9361')
    calls = []
    class Worker:
        def submit_control(self, action, token_path, args):
            calls.append((action, args))
            return {'queued':True}
    S._rf_control_action(app, Worker(), 'configure')
    app.selected_device = 'ACM2108'
    app._rf_parameters['lo_hz'] = 82000000
    app.set_rf_control_result('configure', {'ok':True, 'args':calls[0][1]})
    assert app._rf_applied_parameters['lo_hz'] == PARAMS['lo_hz']
    assert app.observed == M.default_config()


def test_acm_target_never_exposes_rf_editors_or_rf_start_confirmation():
    app, _, v = view(page=M.PAGE_TX)
    app.tx_subpage = 'transmit'  # legacy external navigation
    v.render()
    assert not v.rf_wave_row.visible
    assert not any(row.visible for row in v.rf_tone_rows.values())
    assert all(row.visible for row in v.awg_rows.values())
