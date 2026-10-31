from test_ui_view import view,tap_at,center
from test_rf_touch_model import loaded,snapshot
import ui_model as M
def test_tx_page_entry_and_confirmed_start_use_touch_hit_path():
    app,lcd,v=view(page=M.PAGE_TX,on_rf_control=lambda action: calls.append(action))
    calls=[]
    app.select_device("AD9361")
    tab=next(b for b in v.tx_tab_btns if b.text=="RF发射")
    tap_at(v,*center(tab))
    assert app.page==M.PAGE_TX and app.tx_subpage=="transmit"
    prepared=loaded();app.__dict__.update(prepared.__dict__);app.select_device("AD9361");app.page=M.PAGE_TX;app.tx_subpage="transmit"
    v.invalidate();v.render()
    assert all(r.visible for r in v.rf_tone_rows.values())
    start=next(b for b,slot in zip(v.rf_action_btns,v.rf_action_slots) if slot["action"]=="start")
    tap_at(v,*center(start));assert calls==[] and v.rf_confirm_btn.visible
    tap_at(v,*center(v.rf_confirm_btn));assert calls==["start"]
def test_running_shows_only_stop_disarm_and_locks_editors():
    app,lcd,v=view(page=M.PAGE_TX)
    app.__dict__.update(loaded().__dict__);app.select_device("AD9361");app.page=M.PAGE_TX;app.tx_subpage="transmit"
    app.set_rf_status(snapshot(state="TX_ACTIVE",selected=True))
    v.invalidate();v.render()
    assert all(not r.enabled for r in v.rf_tone_rows.values())
    assert {slot["action"] for slot,b in zip(v.rf_action_slots,v.rf_action_btns) if b.visible}=={"stop","disarm"}


def _visible_field_names(v):
    return sorted(name for name, row in list(v.rf_tone_rows.items()) + list(v.rf_extra_rows.items()) + list(v.rf_normal_rows.items()) if row.visible)


def test_named_rf_waveform_buttons_select_in_advanced_mode():
    app, lcd, v = view(page=M.PAGE_TX)
    app.__dict__.update(loaded().__dict__)
    app.select_device("AD9361")
    app.page = M.PAGE_TX
    app.tx_subpage = "transmit"
    app.set_rf_waveform_mode(True)
    v.invalidate(); v.render()
    assert not v.rf_wave_row.visible
    assert len(v.rf_wave_btns) == 9
    for i, name in enumerate(M.TX_WAVEFORM_TYPES):
        tap_at(v, *center(v.rf_wave_btns[i]))
        assert app.rf_parameter_values().get("tx_waveform") == name
        v.invalidate(); v.render()
        assert _visible_field_names(v) == sorted(M.rf_waveform_fields(name, advanced=True)), name


def test_named_rf_waveform_buttons_select_in_normal_mode():
    app, lcd, v = view(page=M.PAGE_TX)
    app.__dict__.update(loaded().__dict__)
    app.select_device("AD9361")
    app.page = M.PAGE_TX
    app.tx_subpage = "transmit"
    app.set_rf_waveform_mode(False)
    assert app.set_rf_parameter("tx_amplitude_pct", 50) is None
    v.invalidate(); v.render()
    for i, name in enumerate(M.TX_WAVEFORM_TYPES):
        tap_at(v, *center(v.rf_wave_btns[i]))
        assert app.rf_parameter_values().get("tx_waveform") == name
        v.invalidate(); v.render()
        assert _visible_field_names(v) == sorted(M.rf_waveform_fields(name, advanced=False)), name
