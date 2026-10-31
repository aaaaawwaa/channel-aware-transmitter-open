"""One attenuation, two languages, and the message that names it.

The physical-loopback attenuation is decided by one constant per language:

* C:      rf_overlay/sw/board_app/rf_state.h
          RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB -- the gate in rf_state.c compares
          against it AND builds its refusal message from it (RF_STATE_STRINGIFY),
          so the number in the message cannot disagree with the number enforced.
* Python: pynq_overlay/sw/rf_teaching_profile.py ATTENUATION_MDB -- the single
          source the panel builds its physical request from, and the value
          rf_status_client refuses to send anything else for.

2026-10-18: the Python side had moved to 46000 and the C side had not.  Every
physical frame was refused with INVALID_ARGUMENT before any hardware write
(rf_daemon.c:1671, ahead of rf_state_begin and the tx_count gate), and nothing in
either suite compared the two constants -- which is why it survived.  These tests
compare them, read from the sources rather than from a copy, and they exercise the
transcribed gate (scripts/tests/loopback_stub_daemon.py) at runtime too.  Move
either side alone and this module fails.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'scripts' / 'tests'),
               str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import loopback_stub_daemon as STUB_DAEMON  # noqa: E402
from rf_teaching_profile import ATTENUATION_MDB  # noqa: E402
from scripts import rf_environment_trainval_board_session as BOARD  # noqa: E402

BOARD_APP = ROOT / 'rf_overlay' / 'sw' / 'board_app'
HEADER = BOARD_APP / 'rf_state.h'
SOURCE = BOARD_APP / 'rf_state.c'
# Deliberately this module's own parser as well as the stub's: both are checked
# against each other, so neither can quietly become the only definition.
DEFINE = re.compile(r'^#define\s+RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB\s+([0-9]+)u?\s*$',
                    re.MULTILINE)
STRINGIFY = re.compile(
    r'^#define\s+RF_STATE_LOOPBACK_ATTENUATION_TEXT\s*\\\s*\n\s*'
    r'RF_STATE_STRINGIFY\(RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB\)\s*$', re.MULTILINE)
# The refusal-message fragment the gate concatenates its macro onto.
FRAGMENT = re.compile(r'"(physical loopback requires attenuation_mdB )"')
PRE_MIGRATION_MDB = 40000


def c_gate_value():
    found = DEFINE.search(HEADER.read_text(encoding='utf-8'))
    assert found is not None, 'rf_state.h no longer defines the loopback attenuation'
    return int(found.group(1))


def test_the_c_gate_and_the_python_domain_are_one_number():
    value = c_gate_value()
    assert ATTENUATION_MDB == 46000, 'the 46 dB domain is the single source'
    assert value == ATTENUATION_MDB, (
        'rf_state.h RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB (%d) and '
        'rf_teaching_profile.ATTENUATION_MDB (%d) must be the same number: when they '
        'differ the gate refuses every physical frame the chain can send'
        % (value, ATTENUATION_MDB))
    assert value != PRE_MIGRATION_MDB


def test_the_stub_rule_is_the_header_rule_not_a_second_copy():
    assert STUB_DAEMON.c_gate_attenuation_mdb() == c_gate_value() == ATTENUATION_MDB
    assert STUB_DAEMON.C_GATE_ATTENUATION_MDB == c_gate_value()


def test_the_refusal_message_is_built_from_the_gate_constant():
    text = SOURCE.read_text(encoding='utf-8')
    assert STRINGIFY.search(text), (
        'RF_STATE_LOOPBACK_ATTENUATION_TEXT must expand '
        'RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB through RF_STATE_STRINGIFY')
    assert re.search(r'"physical loopback requires attenuation_mdB "\s*\n?\s*'
                     r'RF_STATE_LOOPBACK_ATTENUATION_TEXT', text), (
        'the refusal message must concatenate the macro, not a typed number')
    typed = re.findall(r'attenuation_mdB\s+[0-9]+', text)
    assert typed == [], (
        'rf_state.c types the attenuation into its own message: %r -- the message and '
        'the comparison would then drift independently' % typed)


def test_the_literal_the_board_probe_reads_is_the_gate_value(tmp_path):
    """The board entry reads the requirement out of the shipped binary's literal.

    That literal is the one the C source renders, so this builds it from the source's
    own message fragment plus the header value.  A message naming a different number
    would make the entry report UNKNOWN/MISMATCH on a correct release.
    """
    fragment = FRAGMENT.search(SOURCE.read_text(encoding='utf-8'))
    assert fragment is not None, 'the gate message fragment moved; update this test'
    value = c_gate_value()
    literal = fragment.group(1).encode('ascii') + str(value).encode('ascii')
    daemon = tmp_path / 'rf_daemon'
    daemon.write_bytes(b'\x7fELF\x01\x01 a stand-in binary ' + literal + b' tail')
    read, source = BOARD.daemon_loopback_attenuation_mdb(tmp_path)
    assert read == value, 'the probe must read back the value the C source renders'
    assert source == str(daemon)


def test_the_gate_approves_the_domain_value_and_refuses_every_other_by_name():
    value = c_gate_value()
    stub = STUB_DAEMON.LoopbackStubDaemon()
    try:
        assert stub.loopback_attenuation_mdb == value
        good = {'attenuation_mdB': value, 'cable_confirmed': True, 'samples': 4096,
                'ratio': 0, 'settle_wait_us': 0}
        # With nothing loaded the tx_count gate answers -- reachable only once the
        # attenuation gate has passed.
        assert stub.physical_gate(dict(good)) == (
            'INVALID_ARGUMENT',
            'the physical loopback needs a tx.load first: there is nothing to loop back')
        stub.tx_count = 4096
        assert stub.physical_gate(dict(good)) is None
        for refused in (PRE_MIGRATION_MDB, value - 1, value + 1):
            code, message = stub.physical_gate(dict(good, attenuation_mdB=refused))
            assert code == 'INVALID_ARGUMENT'
            assert message == 'physical loopback requires attenuation_mdB %d' % value
        # A missing field is its own refusal (rf_daemon.c:1665), ahead of the gate.
        code, message = stub.physical_gate(dict(good, attenuation_mdB=None))
        assert code == 'INVALID_ARGUMENT'
        assert message == 'a physical capture requires an explicit attenuation_mdB'
    finally:
        stub.close()


def test_the_panel_and_the_client_send_that_same_number():
    import ui_model
    value = c_gate_value()
    assert ui_model.RX_LOOPBACK_ATTENUATION_MDB == ATTENUATION_MDB == value
    args, why = ui_model.UiApp().rx_capture_args(physical=True)
    assert why is None
    assert args['attenuation_mdB'] == value
