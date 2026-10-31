"""RF editor tests through real hit/press/release and the production callback."""
import pytest
import test_ui_view as H

M, V, ui = H.M, H.V, H.ui
import pynq_status as daemon

PARAMS = dict(lo_hz=80000000, sample_rate_hz=60000000, attenuation_mdB=40000,
              phy_mode_1r1t=0, rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000,
              idelay_en=1, idelay_tap=7)
OPTIONAL = {}


def parameter_view():
    calls = []
    app, lcd, v = H.view(M.PAGE_TX, on_rf_control=calls.append)
    app.select_device("AD9361")
    app.set_rf_status(H._rf_control_snapshot(armed=True, ops=H._ARMED_OPS))
    v.render()
    tab = next((b for b in v.tx_tab_btns if b.text == "RF参数"), None)
    assert tab is not None, "the selected touch-parameter path needs a reachable RF tab"
    H.tap_at(v, *H.center(tab))
    assert app.tx_subpage == "parameters"
    return app, lcd, v, calls


def enter(v, name, text):
    row = v.rf_parameter_rows[name]
    assert v.ui._hit(*H.center(row)) is row
    H.tap_at(v, *H.center(row))
    for _ in range(len(v.keypad.value)):
        H.tap_at(v, *H.center(next(b for b in v.keypad.buttons if b.text == "DEL")))
    for char in str(text):
        key = "+/-" if char == "-" else char
        H.tap_at(v, *H.center(next(b for b in v.keypad.buttons if b.text == key)))
    H.tap_at(v, *H.center(v.keypad.ok))


def test_all_rf_fields_are_blank_reachable_and_touch_editing_never_sends():
    app, lcd, v, calls = parameter_view()
    assert set(v.rf_parameter_rows) == set(PARAMS) | set(OPTIONAL)
    assert app.rf_parameter_values() == {}
    for name, row in v.rf_parameter_rows.items():
        assert row.visible and row.enabled
        assert row.value == "未填"
    for name, value in PARAMS.items():
        enter(v, name, value)
        assert not v.keypad.visible
    assert calls == []
    assert app.rf_parameter_plan("init") == ({k: PARAMS[k] for k in M.RF_ACTION_PARAMS["init"]}, None)
    assert app.rf_parameter_plan("configure") == (PARAMS, None)
    for name, value in OPTIONAL.items():
        enter(v, name, value)
    assert app.rf_parameter_plan("configure") == (dict(PARAMS, **OPTIONAL), None)
    assert calls == []


def test_rf_keypad_empty_cancel_and_out_of_range_preserve_draft():
    app, lcd, v, calls = parameter_view()
    row = v.rf_parameter_rows["attenuation_mdB"]
    H.tap_at(v, *H.center(row))
    assert v.keypad.value == "", "no default attenuation may appear"
    H.tap_at(v, *H.center(v.keypad.ok))
    assert v.keypad.visible and v.keypad.error and app.rf_parameter_values() == {}
    H.tap_at(v, *H.center(v.keypad.cancel))
    enter(v, "attenuation_mdB", 40000)
    enter(v, "attenuation_mdB", 90000)
    assert v.keypad.visible and "89750" in v.keypad.error
    assert app.rf_parameter_values() == dict(attenuation_mdB=40000)
    H.tap_at(v, *H.center(v.keypad.cancel))
    enter(v, "idelay_tap", 32)
    assert v.keypad.visible and v.keypad.error
    H.tap_at(v, *H.center(v.keypad.cancel))
    assert calls == []


def test_edit_and_clear_cancel_confirmation_without_hardware_calls():
    app, lcd, v, calls = parameter_view()
    for name, value in PARAMS.items():
        assert app.set_rf_parameter(name, value) is None
    app.rf_control_prompt("init")
    enter(v, "attenuation_mdB", 45000)
    assert app.rf_control["pending_action"] is None
    app.rf_control_prompt("init")
    H.tap_at(v, *H.center(v.rf_clear_parameters_btn))
    assert app.rf_parameter_values() == {} and app.rf_control["pending_action"] is None
    assert app.rf_parameter_plan("init")[1] is not None
    assert calls == []


def test_parameter_signature_updates_even_after_programmatic_model_edit():
    app, lcd, v, calls = parameter_view()
    before = lcd.flushes
    app.set_rf_parameter("lo_hz", 90000000)
    v.render()
    assert lcd.flushes > before
    assert v.rf_parameter_rows["lo_hz"].value == "90000000"


def test_init_needs_complete_parameters_then_confirmation_and_stop_remains_reachable():
    app, lcd, v, calls = parameter_view()
    init = next(b for b in v.rf_action_btns if b.visible and b.text == V.RF_CONTROL_LABEL["init"])
    H.tap_at(v, *H.center(init))
    assert calls == [] and not app.rf_control["pending_action"]
    assert app.rf_control["last_error"], "missing fields must be visible rather than silently ignored"
    for name in M.RF_ACTION_PARAMS["init"]:
        app.set_rf_parameter(name, PARAMS[name])
    v.render()
    H.tap_at(v, *H.center(init))
    assert app.rf_control["pending_action"] == "init" and calls == []
    stop = next((b for b in v.rf_action_btns if b.visible and b.text == V.RF_CONTROL_LABEL["stop"]), None)
    assert stop is not None, "confirmation must not hide the stop entry"
    H.tap_at(v, *H.center(stop))
    assert calls == ["stop"]
    H.tap_at(v, *H.center(v.rf_cancel_btn))
    H.tap_at(v, *H.center(init))
    H.tap_at(v, *H.center(v.rf_confirm_btn))
    assert calls == ["stop", "init"]


@pytest.mark.parametrize("name", M.RF_POSITIVE_PARAMS)
def test_rf_parameter_integer_limit_matches_the_daemon_parser(name):
    app = M.UiApp()
    if name == "lo_hz":
        hi = M.RF_LO_MAX_HZ
    elif name == "sample_rate_hz":
        hi = M.RF_SAMPLE_RATE_MAX_HZ
    else:
        hi = M.RF_PROTOCOL_INT_MAX
    assert app.set_rf_parameter(name, hi) is None
    assert app.set_rf_parameter(name, hi + 1) is not None
    assert app.rf_parameter_plan("init")[0] == {}  # incomplete stays unsendable


def test_rf_flag_requires_an_integer_and_parameter_snapshot_cannot_mutate_model():
    app = M.UiApp()
    assert app.set_rf_parameter("idelay_en", 1.0) is not None
    assert app.set_rf_parameter("idelay_en", 1) is None
    snapshot = app.rf_parameter_values()
    snapshot["idelay_en"] = 0
    assert app.rf_parameter_values()["idelay_en"] == 1


class Worker:
    def __init__(self):
        self.calls = []

    def control(self, action, token_path=None, args=None):
        self.calls.append((action, token_path, args))
        return dict(ok=True, code=None, message="")


@pytest.mark.parametrize("action", ("init", "configure"))
def test_production_service_forwards_exact_complete_operator_plan(action):
    app, worker = M.UiApp(), Worker()
    for name, value in dict(PARAMS, **OPTIONAL).items():
        app.set_rf_parameter(name, value)
    expected = app.rf_parameter_plan(action)[0]
    daemon._rf_control_action(app, worker, action)
    assert worker.calls == [(action, M.RF_CONTROL_TOKEN_PATH, expected)]


def test_production_service_rejects_missing_params_before_worker():
    app, worker = M.UiApp(), Worker()
    daemon._rf_control_action(app, worker, "init")
    assert worker.calls == []
    assert app.rf_control["last_code"] == "INVALID_ARGUMENT"
    assert "lo_hz" in app.rf_control["last_error"]


def test_rf_parameter_controls_labels_and_keyboard_stay_inside_screen():
    app, lcd, v, calls = parameter_view()
    for name, value in PARAMS.items():
        app.set_rf_parameter(name, value)
    for pending in (False, True):
        if pending:
            app.rf_control_prompt("init")
        sink = []
        ui.set_text_log(sink)
        try:
            v.invalidate()
            v.render()
        finally:
            ui.set_text_log(None)
        controls = [w for w in v.ui.widgets if w.visible and w.enabled and w.tappable]
        for i, a in enumerate(controls):
            ax0, ay0, ax1, ay1 = a.box
            assert 0 <= ax0 <= ax1 < ui.WIDTH and 0 <= ay0 <= ay1 < ui.HEIGHT
            assert v.ui._hit(*H.center(a)) is a
            for b in controls[i+1:]:
                bx0, by0, bx1, by1 = b.box
                assert ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0
        for text, size, x, y in sink:
            width, height = ui.text_size(text, size)
            assert 0 <= x and 0 <= y and x+width <= ui.WIDTH and y+height <= ui.HEIGHT, text
            assert ui.has_glyphs(text, size), text
    app.rf_control_cancel()
    H.tap_at(v, *H.center(v.rf_parameter_rows["lo_hz"]))
    assert v.keypad.visible
