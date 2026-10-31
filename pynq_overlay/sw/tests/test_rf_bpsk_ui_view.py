"""M4 explicit reference and shaping use production hit/press/release paths."""
import pytest
import test_rf_rx_view as H


def demod_view():
    app = H.rx_app()
    app.rx_subpage = 'demod'
    lcd, view = H.rx_view(app)
    return app, lcd, view


def test_reference_switch_is_explicit_and_revisioned():
    app, lcd, view = demod_view()
    before = app.rx_demod_revision
    H.H.tap_at(view, *H.H.center(view.rx_reference_btn))
    assert app.rx_draft['reference_enabled'] is True
    assert app.rx_demod_revision == before + 1
    H.H.tap_at(view, *H.H.center(view.rx_reference_btn))
    assert app.rx_draft['reference_enabled'] is False


def test_shaping_and_mode_taps_use_rx_setter():
    app, lcd, view = demod_view()
    before = app.rx_demod_revision
    H.H.tap_at(view, *H.H.center(view.rx_shape_btns[1]))
    assert app.rx_draft['shaping'] == 'RRC'
    assert app.rx_demod_revision == before + 1
    H.H.tap_at(view, *H.H.center(view.rx_mode_btns[1]))
    assert app.rx_draft['expected_mode'] == 'QPSK'
    assert app.rx_demod_revision == before + 2


def test_seed_edit_field_opens_real_keypad_and_hidden_switch_cannot_fire():
    app, lcd, view = demod_view()
    H.H.tap_at(view, *H.H.center(view.rx_seed_row))
    assert view.keypad.visible
    view.keypad.visible = False
    app.rx_subpage = 'overview'
    view.invalidate(); view.render()
    before = app.rx_demod_revision
    H.H.tap_at(view, *H.H.center(view.rx_reference_btn))
    assert app.rx_demod_revision == before


@pytest.mark.parametrize('available,reason', [(False, '解调未接入'),
                                             (True, 'UNSUPPORTED'),
                                             (True, 'CLIPPED'),
                                             (True, 'NO_ENERGY')])
def test_demod_draws_current_refusal_and_no_fabricated_ber(monkeypatch, available, reason):
    app, lcd, view = demod_view()
    monkeypatch.setattr(app, 'rx_demod_summary', lambda: dict(available=available,
        current=False, reason=reason, ber=None, bit_errors=None, compared_bits=None,
        carrier_sync=False, timing_sync=False, reference_sync=None, cfo_hz=None,
        capture_id=None, polarity_ambiguous=False))
    _lcd, boxes = H.render_logged(app, 'demod')
    text = '\n'.join(s for s, size, x, y in boxes)
    assert 'BER' in text and '0.000000' not in text
    assert reason in text or {'UNSUPPORTED':'不支持', 'CLIPPED':'削顶',
                             'NO_ENERGY':'能量不足'}.get(reason, reason) in text
    assert '参考' in text
