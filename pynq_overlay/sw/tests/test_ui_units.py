import importlib
import pytest
from test_ui_view import view, tap_at, center
import ui_model as M


def units():
    assert importlib.util.find_spec('ui_units'), 'frequency unit conversion is missing'
    return importlib.import_module('ui_units')


@pytest.mark.parametrize('text,unit,expected', [('100', 'MHz', 100000000), ('0.1', 'kHz', 100), ('2.1', 'GHz', 2100000000), ('0.000001', 'MHz', 1)])
def test_frequency_conversion_is_exact(text, unit, expected):
    assert units().parse_frequency(text, unit) == expected


@pytest.mark.parametrize('text,unit', [('0.1','Hz'), ('-1','MHz'), ('1.2.3','Hz'), ('NaN','Hz'), ('Infinity','Hz'), ('1e3','Hz'), ('2101','MHz'), ('1','bad'), ('','Hz')])
def test_invalid_or_overflow_frequency_is_rejected(text, unit):
    with pytest.raises(ValueError):
        units().parse_frequency(text, unit)


def test_switch_units_preserves_exact_hz_without_committing():
    _, _, v = view()
    calls = []
    v.keypad.open_for('LO', 100000001, '', {'min':1, 'max':2100000000, 'unit':'Hz', 'set':calls.append})
    assert hasattr(v.keypad, 'select_unit'), 'unit selector is missing'
    v.keypad.select_unit('MHz')
    assert v.keypad.value == '100.000001'
    v.keypad.select_unit('kHz')
    assert v.keypad.value == '100000.001'
    v.keypad.close()
    assert calls == []


def test_fractional_unit_value_commits_an_integer_hz():
    _, _, v = view()
    calls = []
    v.keypad.open_for('rate', 100000, '', {'min':10, 'max':1000000, 'unit':'Sym/s', 'set':calls.append})
    assert hasattr(v.keypad, 'select_unit')
    v.keypad.select_unit('kSym/s')
    v.keypad.value = '12.5'
    v.keypad._commit()
    assert calls == [12500]


def test_blank_rf_editor_accepts_unit_selection_before_digits():
    _, _, v = view()
    calls = []
    v.keypad.open_for('LO', '', '', {'min':1, 'max':2100000000, 'unit':'Hz', 'set':calls.append})
    v.keypad.select_unit('MHz')
    assert v.keypad.unit == 'MHz'
    assert v.keypad.value == '' and calls == []
    for key in '100':
        v.keypad._key_tap(key)(None)
    v.keypad._commit()
    assert calls == [100000000]


def test_rf_frequency_unit_buttons_use_real_touch_without_hardware_calls():
    calls = []
    app, _, v = view(page=M.PAGE_TX, on_rf_control=calls.append)
    v.render()
    tap_at(v, *center(v.device_buttons['AD9361']))
    tap_at(v, *center(v.rf_parameter_rows['lo_hz']))
    assert v.keypad.visible and v.keypad.unit == 'Hz'
    tap_at(v, *center(next(b for b in v.keypad.unit_buttons if b.text == 'MHz')))
    for digit in '100':
        tap_at(v, *center(next(b for b in v.keypad.buttons if b.text == digit)))
    tap_at(v, *center(v.keypad.ok))
    assert app.rf_parameter_values()['lo_hz'] == 100000000
    assert calls == []
