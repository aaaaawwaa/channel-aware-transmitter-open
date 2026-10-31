"""The research fixtures must speak the shape the daemon actually emits.

Every other test in this stage talks to 'ScriptedDaemon', a Python stand-in.  A
fixture that invents a field, drops one, or nests it differently makes the whole
client suite pass for the wrong reason -- and there is no host build of the
daemon's socket transport to test against instead ('rf_daemon_main.c' puts its
transport behind '#ifndef PLATFORM_HOST').

So the frame is not typed out here either: it is rebuilt from the C source the
way the daemon writes it, in two steps ('rf_daemon.c:2130-2142'):

  1. the frame template, whose literal ends '...timing_ns":[...]}}' -- the first
     closing brace closes 'headroom', the second closes the frame;
  2. the evidence append, written with 'snprintf(d->frame+n-2, ...)': it starts
     ON TOP of those two braces and ends with its own ']}}'.

Both the offset and the append text are read out of the C source, so the layer
the four appended keys land on is a property of the daemon that these tests
MEASURE.  That is the 2026-10-16 defect #2: the predicate read 'native_error',
'closure', 'timing_scope' and 'timing_order' from the frame object while the
daemon writes them inside 'headroom', and the old version of this file only
compared the template literal with the fixture -- so moving the append one
character to the right ('n-2' -> 'n-1', the review's M10) left all 2116 host
tests green.
"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rf_headroom as H
import test_rf_headroom as T

ROOT = Path(__file__).resolve().parents[3]
DAEMON_C = ROOT / 'rf_overlay/sw/board_app/rf_daemon.c'

TEMPLATE_MARKER = '{"type":"research_iq_frame"'
APPEND_MARKER = ',\\"native_error\\":'
APPEND_CALL = re.compile(r'\(char\s*\*\s*\)\s*d->frame\s*\+\s*n\s*([+-])\s*(\d+)')
APPENDED = ('native_error', 'closure', 'timing_scope', 'timing_order')


def _escaped():
    return DAEMON_C.read_text(encoding='utf-8')


def _unescaped():
    return _escaped().replace('\\"', '"')


def _json_after(raw, marker):
    """Recover one JSON object from the unescaped C source.

    Adjacent C literals are concatenated at compile time; after unescaping they
    leave a seam of quote-whitespace-quote, which is joined here.  Braces in the
    surrounding code are not involved because the scan starts at the marker and
    stops when depth returns to zero.
    """
    index = raw.index(marker)
    depth = 0
    out = []
    while index < len(raw):
        char = raw[index]
        if char == '"':
            probe = index + 1
            while probe < len(raw) and raw[probe] in ' \t\r\n':
                probe += 1
            if probe > index + 1 and probe < len(raw) and raw[probe] == '"':
                index = probe + 1
                continue
        if char in '{[':
            depth += 1
        elif char in '}]':
            depth -= 1
            out.append(char)
            if depth == 0:
                return ''.join(out)
            index += 1
            continue
        out.append(char)
        index += 1
    raise AssertionError('unbalanced JSON after ' + marker)


def _fill(text):
    """printf placeholders back into valid JSON, keeping their string-ness."""
    for quoted in ('"%s"', '"%u"', '"%llu"', '"%ld"', '"%d"'):
        text = text.replace(quoted, '"S"')
    for bare in ('%llu', '%lu', '%ld', '%u', '%d', '%s'):
        text = text.replace(bare, '0')
    return text


def _decode(text):
    return json.loads(_fill(text))


def _append_offset(raw):
    """The '-N' in 'snprintf(d->frame+n-N, ...)', read from the source."""
    found = APPEND_CALL.search(raw)
    assert found, 'the append site no longer writes at a fixed offset from n'
    return int(found.group(1) + found.group(2))


def _literal_forward(escaped, index):
    """The C string literal containing 'index', concatenations included.

    Scanning the ESCAPED source is what makes this exact: every JSON quote in
    the literal is written as backslash-quote, so an unescaped quote is either a
    closing delimiter or the opening delimiter of the next concatenated
    literal.  Neither carries content, so both are dropped -- exactly as the
    compiler does.
    """
    out = []
    while index < len(escaped):
        char = escaped[index]
        if char == '\\' and index + 1 < len(escaped):
            following = escaped[index + 1]
            out.append(following if following == '"' else char + following)
            index += 2
            continue
        if char == '"':
            probe = index + 1
            while probe < len(escaped) and escaped[probe] in ' \t\r\n':
                probe += 1
            if (probe > index + 1 and probe < len(escaped)
                    and escaped[probe] == '"'):
                index = probe + 1
                continue
            return ''.join(out)
        out.append(char)
        index += 1
    raise AssertionError('unterminated append literal in rf_daemon.c')


def _append_text():
    """The evidence append as the compiler concatenates it, ']}}' included."""
    return _literal_forward(_escaped(), _escaped().index(APPEND_MARKER))


def _whole_frame(raw=None):
    """(template, filled text, parsed frame) for the daemon's two writes."""
    raw = _unescaped() if raw is None else raw
    template = _json_after(raw, TEMPLATE_MARKER)
    text = template[:len(template) + _append_offset(raw)] + _append_text()
    return template, _fill(text), json.loads(_fill(text))


def _keys(value, prefix=''):
    if isinstance(value, dict):
        out = set()
        for key, item in value.items():
            out.add(prefix + key)
            out |= _keys(item, prefix + key + '.')
        return out
    if isinstance(value, list):
        out = set()
        for item in value:
            out |= _keys(item, prefix + '[]' + '.')
        return out
    return set()


SUBSTITUTED = tuple(['headroom.%s.' % key for key in
                     ('actual_rf_before', 'actual_rf_after', 'native_error',
                      'closure')])


def _pruned(keys):
    return {key for key in keys
            if not any(key.startswith(prefix) for prefix in SUBSTITUTED)}


def test_the_daemon_appends_the_evidence_inside_headroom():
    """The landing point: offset n-2 overwrites the template's ]}} tail."""
    raw = _unescaped()
    template, _, frame = _whole_frame()
    # The landing point is asserted on the COMPOSED frame first, so a moved
    # append offset fails here on the key's layer -- not on a literal pin.
    for key in APPENDED:
        assert key in frame['headroom'], key
        assert key not in frame, key
    assert template.endswith(']}}')
    assert _append_text().endswith(']}}')
    assert _append_offset(raw) == -2


def test_a_one_character_append_move_would_land_outside_headroom():
    """The contrast that makes the assertion above a measurement, not a claim.

    n-1 keeps the template's outer brace, so a one-object frame needs the append
    to close only 'headroom' -- the review's M10 mutation.  Composed here
    without touching the C source, it moves all four keys up a level.
    """
    raw = _unescaped()
    template = _json_after(raw, TEMPLATE_MARKER)
    fragment = _append_text()
    moved = json.loads(_fill(template[:-1] + fragment[:-1]))
    assert not set(APPENDED) & set(moved['headroom'])
    assert set(APPENDED) <= set(moved)


def test_the_daemon_speaks_the_timing_contract_the_client_requires():
    _, _, frame = _whole_frame()
    head = frame['headroom']
    assert head['timing_scope'] == H.TIMING_SCOPE
    assert head['timing_order'] == list(H.TIMING_ORDER)
    assert list(H.APPENDED_EVIDENCE) == list(APPENDED)


def test_the_daemon_emits_exactly_the_research_frame_the_fixture_builds():
    raw = _unescaped()
    _, _, frame = _whole_frame()
    fixture = T.FakeClient()._frame(41, 2).header
    # Both sides place the four appended keys inside 'headroom'; the frame
    # object carries exactly the template's keys, and 'headroom' carries the
    # template's keys plus the four the append adds.
    assert set(fixture) == set(frame)
    assert set(fixture['headroom']) == set(frame['headroom'])
    for key in APPENDED:
        assert key in fixture['headroom'] and key not in fixture, key
    # 'actual_rf_before'/'actual_rf_after' are substituted objects ('hr_rb_json')
    # and 'native_error'/'closure' come from their own literals, so their inner
    # keys are pinned by the tests below rather than by the frame template.
    assert _pruned(_keys(frame)) == _pruned(_keys(fixture))
    assert any(key.startswith('headroom.actual_rf_before.') for key in _keys(fixture))
    assert any(key.startswith('headroom.actual_rf_after.') for key in _keys(fixture))
    assert 'hr_rb_json(bj' in raw and 'hr_rb_json(aj' in raw


def test_the_daemon_frame_is_still_a_single_json_object_plus_its_bytes():
    raw = _unescaped()
    decoded = _decode(_json_after(raw, TEMPLATE_MARKER))
    assert decoded['samples'] == H.CAPTURE_SAMPLES
    assert decoded['bytes'] == H.IQ_BYTES
    assert decoded['ratio'] == 0 and decoded['sample_rate_hz'] == 60000000
    # The payload is appended after the newline, not embedded in the JSON.
    assert 'd->frame[n]=' in raw and 'send_all(c,d->frame,n+1+16384)' in raw


def test_the_daemon_begin_reply_carries_exactly_the_fixture_fields():
    raw = _unescaped()
    decoded = _decode(_json_after(raw, '{"study":"HEADROOM-1","session_id"'))
    assert set(decoded) == {'study', 'session_id', 'initializations',
                            'attempt_budget', 'actual_rf'}
    assert decoded['study'] == H.STUDY
    assert decoded['initializations'] == 1
    assert decoded['attempt_budget'] == H.ATTEMPT_BUDGET


def test_the_daemon_readback_object_is_what_the_fixture_reports():
    raw = _unescaped()
    emitted = _keys(_decode(_json_after(raw, '{"rx_lo_hz":')))
    assert emitted == _keys(T.rf_json())
    assert 'pl.idelay_tap' in emitted


def test_the_daemon_advertises_the_contract_the_client_requires():
    raw = _unescaped()
    advertised = _decode(_json_after(raw, '{"study":"HEADROOM-1","protocol_minor"'))
    assert advertised['study'] == H.STUDY
    assert advertised['protocol_minor'] == H.PROTOCOL_MINOR
    assert advertised['initialization_budget'] == 1
    assert advertised['attempt_budget'] == H.ATTEMPT_BUDGET
    assert advertised['operations'] == list(H.CONTROL_OPS)
    assert set(advertised) == {'study', 'protocol_minor', 'initialization_budget',
                              'attempt_budget', 'used', 'active', 'operations'}


def test_the_daemon_refusal_is_the_object_the_client_lifts_a_code_from():
    raw = _unescaped()
    refusal = _decode(_json_after(raw, '{"id":%ld,"ok":false,"error":%s}'))
    # The client reads 'error.code'; a differently nested refusal would lose it.
    assert set(refusal) == {'id', 'ok', 'error'}
    assert refusal['ok'] is False
    detail = _unescaped()
    marker = detail.index('"code":%d,"message":"research original IQ unavailable"')
    assert '"stage":"%s"' in detail[marker:marker + 600]


def test_the_fixture_does_not_invent_fields_the_daemon_never_sends():
    """The other direction: an extra fixture field is its own kind of failure."""
    raw = _unescaped()
    fixture = T.FakeClient()._frame(41, 2).header
    errors = _keys(_decode(_json_after(raw, '{"stage":"%s","code":%d')))
    assert _keys(fixture['headroom']['native_error']) == errors
    assert set(fixture['headroom']['closure']) == {'attempted', 'confirmed'}
    railed = T.FakeClient(rails_at=2)._frame(41, 2).header
    assert _keys(railed['headroom']['closure']) == _keys(_decode(
        _json_after(raw, '{"attempted":true,"confirmed":%s')))
    for key in sorted(_keys(fixture) | _keys(fixture['headroom'])):
        leaf = key.split('.')[-1].split('[]')[-1]
        assert leaf and ('"%s"' % leaf) in raw, 'fixture-only field: ' + key
    assert len(fixture['headroom']['timing_ns']) == len(H.TIMING_ORDER)
