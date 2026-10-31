"""Offline tests for the per-frame `capture_observation` extractor.

Only offline fixtures: no board, no daemon, no socket, no capture.  The two wire
strings below are the daemon's own recorded headers (frozen prefix + the appended
observation object), taken from the observation fixtures that the C suite and the
independent review actually emitted:

* the appended object shape: `evidence/rf-teaching/s2-46db-inwindow-observer-2026-10-19/
  NOTES.md` section 3 and `rf_overlay/sw/board_app/test/test_rf_daemon.c:3097`;
* the double-null failure header verbatim: the recorded run output in
  `evidence/rf-teaching/s2-46db-inwindow-observer-review-2026-10-19/work/
  mutant-results/m2-zero-on-failure.json` (the header that run emitted).

The point of every case is the same: a value the wire did not carry, or carried in a
shape the daemon cannot write, must come back as a NAMED unavailability with
`value = None` -- never 0, never a default, never the frame-boundary reading, never a
value belonging to another frame.
"""
import ast
import inspect
import json
import textwrap

import pytest

from scripts import rf_capture_observation as C

FROZEN_PREFIX = ('{"id":%d,"type":"iq_frame","direction":"rx","format":"s16_iq_le",'
                 '"samples":4,"bytes":16,"sample_rate_hz":60000000,"ratio":0,'
                 '"timestamp_ns":%d')
SUCCESS_OBSERVATION = ('{"scope":"single_read_in_capture_window_plus_chip_attenuation_'
                       'readback","in_window_rx_gain_index":76,'
                       '"chip_tx_attenuation_mdb":[46000,46000]}')
FAILURE_OBSERVATION = ('{"scope":"single_read_in_capture_window_plus_chip_attenuation_'
                       'readback","in_window_rx_gain_index":null,'
                       '"in_window_rx_gain_unavailable":"HARDWARE_IO",'
                       '"chip_tx_attenuation_mdb":null,'
                       '"chip_tx_attenuation_unavailable":"HARDWARE_IO"}')


def wire(member=SUCCESS_OBSERVATION, frame_id=3, timestamp_ns=1000005000):
    """One frame header exactly as `rf_proto_write_iq_frame_observed` composes it."""
    return (FROZEN_PREFIX % (frame_id, timestamp_ns)
            + ',"capture_observation":' + member + '}')


WIRE_SUCCESS = wire()
# The failure header recorded in the review's mutant run, byte for byte (id 3 was that
# run's frame; a batch fixture gives every frame its own identity via `wire`).
WIRE_FAILURE_RECORDED = wire(FAILURE_OBSERVATION)

_MISSING = object()


def observation(drop=(), **fields):
    """The observation member as the C writer composes it, with overrides."""
    member = {'scope': C.EXPECTED_SCOPE, 'in_window_rx_gain_index': 76,
              'chip_tx_attenuation_mdb': [46000, 46000]}
    member.update(fields)
    for name in drop:
        member.pop(name, None)
    return member


def document(member=_MISSING, frame_id=3, timestamp_ns=1000005000,
             frame_type='iq_frame', drop=()):
    header = {'id': frame_id, 'type': frame_type, 'direction': 'rx',
              'format': 's16_iq_le', 'samples': 4, 'bytes': 16,
              'sample_rate_hz': 60000000, 'ratio': 0, 'timestamp_ns': timestamp_ns}
    if frame_type is None:
        header.pop('type')
    for name in drop:
        header.pop(name, None)
    if member is not _MISSING:
        header['capture_observation'] = member
    return header


def gain_of(record):
    return record['in_window_rx_gain_index']


def attenuation_of(record):
    return record['chip_tx_attenuation_mdb']


# --------------------------------------------------------------------------- shapes

def test_the_real_success_shape_reads_both_fields():
    record = C.extract_frame_observation(WIRE_SUCCESS)
    assert record['usable'] is True and record['stop'] is False
    assert record['frame_id'] == 3 and record['timestamp_ns'] == 1000005000
    assert record['observation_present'] is True
    assert record['scope'] == C.EXPECTED_SCOPE
    assert gain_of(record) == {'available': True, 'value': 76, 'reason': None}
    assert attenuation_of(record) == {'available': True, 'value': [46000, 46000],
                                      'reason': None}


def test_the_real_double_null_failure_shape_is_named_not_zero():
    record = C.extract_frame_observation(WIRE_FAILURE_RECORDED)
    assert record['usable'] is False and record['stop'] is True
    for reading, unavailable_field in ((gain_of(record), 'in_window_rx_gain_unavailable'),
                                       (attenuation_of(record),
                                        'chip_tx_attenuation_unavailable')):
        assert reading['available'] is False
        assert reading['value'] is None, 'a failed reading must never become a number'
        assert reading['reason'] == 'HARDWARE_IO'
    assert 'HARDWARE_IO' in record['reasons']


def test_the_daemons_own_error_code_is_carried_through_verbatim():
    for code in ('HARDWARE_IO', 'INVALID_STATE', 'TIMEOUT'):
        record = C.extract_frame_observation(document(observation(
            in_window_rx_gain_index=None,
            in_window_rx_gain_unavailable=code)))
        assert gain_of(record)['reason'] == code
        assert gain_of(record)['value'] is None
        # the other reading is independent and stays available
        assert attenuation_of(record)['available'] is True


def test_a_partial_failure_leaves_the_other_field_available():
    record = C.extract_frame_observation(document(observation(
        in_window_rx_gain_index=None, in_window_rx_gain_unavailable='TIMEOUT')))
    assert gain_of(record)['available'] is False
    assert attenuation_of(record) == {'available': True, 'value': [46000, 46000],
                                      'reason': None}
    assert record['usable'] is False

    record = C.extract_frame_observation(document(observation(
        chip_tx_attenuation_mdb=None,
        chip_tx_attenuation_unavailable='HARDWARE_IO')))
    assert attenuation_of(record)['available'] is False
    assert gain_of(record) == {'available': True, 'value': 76, 'reason': None}


def test_the_gain_index_endpoints_are_available():
    for value in (0, 76):
        record = C.extract_frame_observation(document(observation(
            in_window_rx_gain_index=value)))
        assert gain_of(record) == {'available': True, 'value': value, 'reason': None}


# ------------------------------------------------------------- missing / mis-shaped

def test_a_missing_observation_member_is_named():
    record = C.extract_frame_observation(document())
    assert record['observation_present'] is False
    assert record['usable'] is False and record['stop'] is True
    assert gain_of(record)['reason'] == C.OBSERVATION_MEMBER_MISSING
    assert attenuation_of(record)['reason'] == C.OBSERVATION_MEMBER_MISSING
    # an older candidate that simply has no such member can never be read as a pass
    assert gain_of(record)['value'] is None


def test_an_observation_that_is_not_an_object_is_named():
    for member in ('text', 5, [1, 2], None):
        record = C.extract_frame_observation(document(member))
        assert record['observation_present'] is True
        assert gain_of(record)['reason'] == C.OBSERVATION_MEMBER_NOT_AN_OBJECT
        assert attenuation_of(record)['reason'] == C.OBSERVATION_MEMBER_NOT_AN_OBJECT


def test_a_missing_gain_key_is_named():
    record = C.extract_frame_observation(document(observation(
        drop=('in_window_rx_gain_index',))))
    assert gain_of(record) == {'available': False, 'value': None,
                               'reason': C.GAIN_INDEX_MISSING}
    assert attenuation_of(record)['available'] is True


def test_a_missing_attenuation_key_is_named():
    record = C.extract_frame_observation(document(observation(
        drop=('chip_tx_attenuation_mdb',))))
    assert attenuation_of(record) == {'available': False, 'value': None,
                                      'reason': C.CHIP_TX_ATTENUATION_MISSING}
    assert gain_of(record)['available'] is True


def test_a_unavailable_reason_without_the_null_value_is_named():
    record = C.extract_frame_observation(document(observation(
        drop=('in_window_rx_gain_index',),
        in_window_rx_gain_unavailable='HARDWARE_IO')))
    assert gain_of(record)['reason'] == C.GAIN_INDEX_MALFORMED
    assert gain_of(record)['value'] is None


# ------------------------------------------------------------------- type / range

@pytest.mark.parametrize('value', [True, False])
def test_a_json_boolean_is_not_an_integer_gain_index(value):
    record = C.extract_frame_observation(document(observation(
        in_window_rx_gain_index=value)))
    assert gain_of(record)['reason'] == C.GAIN_INDEX_MALFORMED
    assert gain_of(record)['value'] is None, 'True must never be read as index 1'


@pytest.mark.parametrize('value', [1.0, 76.0, '76', [], {}])
def test_a_non_integer_gain_index_is_malformed(value):
    record = C.extract_frame_observation(document(observation(
        in_window_rx_gain_index=value)))
    assert gain_of(record)['reason'] == C.GAIN_INDEX_MALFORMED
    assert gain_of(record)['value'] is None


@pytest.mark.parametrize('value', [77, 200, -1])
def test_a_gain_index_outside_the_parts_full_table_is_named(value):
    record = C.extract_frame_observation(document(observation(
        in_window_rx_gain_index=value)))
    assert gain_of(record)['reason'] == C.GAIN_INDEX_OUT_OF_RANGE
    assert gain_of(record)['value'] is None


@pytest.mark.parametrize('value', [[46000], [46000, 46000, 46000], '46000', 46000,
                                   {'tx1': 46000}])
def test_an_attenuation_that_is_not_two_values_is_malformed(value):
    record = C.extract_frame_observation(document(observation(
        chip_tx_attenuation_mdb=value)))
    assert attenuation_of(record)['reason'] == C.CHIP_TX_ATTENUATION_MALFORMED
    assert attenuation_of(record)['value'] is None


@pytest.mark.parametrize('value', [[46000, True], [46000, 46000.0], [None, 46000],
                                   ['46000', 46000]])
def test_an_attenuation_member_that_is_not_an_integer_is_malformed(value):
    record = C.extract_frame_observation(document(observation(
        chip_tx_attenuation_mdb=value)))
    assert attenuation_of(record)['reason'] == C.CHIP_TX_ATTENUATION_MALFORMED
    assert attenuation_of(record)['value'] is None


def test_an_attenuation_pair_outside_the_chip_range_is_named():
    over = C.CHIP_TX_ATTENUATION_MDB_MAX + 250
    for value in ([over, 46000], [46000, over], [-250, 46000]):
        record = C.extract_frame_observation(document(observation(
            chip_tx_attenuation_mdb=value)))
        assert attenuation_of(record)['reason'] == C.CHIP_TX_ATTENUATION_OUT_OF_RANGE
        assert attenuation_of(record)['value'] is None


def test_the_chip_range_endpoints_are_available():
    record = C.extract_frame_observation(document(observation(
        chip_tx_attenuation_mdb=[0, C.CHIP_TX_ATTENUATION_MDB_MAX])))
    assert attenuation_of(record) == {
        'available': True, 'value': [0, C.CHIP_TX_ATTENUATION_MDB_MAX], 'reason': None}


@pytest.mark.parametrize('fields', [
    {'in_window_rx_gain_index': None},
    {'in_window_rx_gain_index': None, 'in_window_rx_gain_unavailable': ''},
    {'in_window_rx_gain_index': None, 'in_window_rx_gain_unavailable': None},
    {'in_window_rx_gain_index': None, 'in_window_rx_gain_unavailable': 7},
])
def test_a_null_without_a_usable_reason_is_named_unavailable(fields):
    record = C.extract_frame_observation(document(observation(**fields)))
    assert gain_of(record)['reason'] == C.GAIN_INDEX_UNAVAILABLE_WITHOUT_REASON
    assert gain_of(record)['value'] is None


@pytest.mark.parametrize('fields,expected', [
    ({'in_window_rx_gain_index': 76, 'in_window_rx_gain_unavailable': 'HARDWARE_IO'},
     C.GAIN_INDEX_CONTRADICTORY),
    ({'chip_tx_attenuation_mdb': [46000, 46000],
      'chip_tx_attenuation_unavailable': 'HARDWARE_IO'},
     C.CHIP_TX_ATTENUATION_CONTRADICTORY),
])
def test_a_value_and_a_reason_together_are_contradictory(fields, expected):
    record = C.extract_frame_observation(document(observation(**fields)))
    readings = [gain_of(record), attenuation_of(record)]
    assert [reading for reading in readings if reading['reason'] == expected]


def test_a_scope_that_differs_from_the_writer_is_refused():
    for scope in ('single_read_in_capture_window', 'something.else', 5):
        record = C.extract_frame_observation(document(observation(scope=scope)))
        assert record['usable'] is False
        assert record['reasons'] == [C.OBSERVATION_SCOPE_MISMATCH]
        assert gain_of(record)['value'] is None and attenuation_of(record)['value'] is None


def test_a_missing_scope_is_refused():
    record = C.extract_frame_observation(document(observation(drop=('scope',))))
    assert record['reasons'] == [C.OBSERVATION_SCOPE_MISSING]
    assert gain_of(record)['value'] is None


def test_a_header_that_is_not_an_iq_frame_is_refused():
    for frame_type in ('research_iq_frame', 'event', 7):
        record = C.extract_frame_observation(document(frame_type=frame_type))
        assert record['reasons'] == [C.FRAME_TYPE_NOT_IQ_FRAME]
        assert record['usable'] is False


def test_a_header_without_a_type_is_refused():
    record = C.extract_frame_observation(document(frame_type=None))
    assert C.FRAME_TYPE_MISSING in record['reasons']
    assert record['usable'] is False


# ---------------------------------------------------------------- document parsing

def test_a_json_text_document_is_parsed():
    record = C.extract_frame_observation(WIRE_SUCCESS)
    assert record['usable'] is True
    assert C.extract_frame_observation(WIRE_SUCCESS.encode('utf-8'))['usable'] is True


@pytest.mark.parametrize('broken', ['{"id":3,', 'not json at all', b'\xff\xfe\x00'])
def test_a_document_that_is_not_json_is_named(broken):
    record = C.extract_frame_observation(broken)
    assert record['reasons'] == [C.FRAME_HEADER_NOT_JSON]
    assert record['usable'] is False
    assert gain_of(record)['value'] is None


@pytest.mark.parametrize('broken', [None, 3, 3.5, [1, 2], (1, 2), object()])
def test_a_document_that_is_not_an_object_is_named(broken):
    record = C.extract_frame_observation(broken)
    assert record['reasons'] == [C.FRAME_HEADER_NOT_AN_OBJECT]
    assert record['usable'] is False


def test_a_python_string_that_is_not_json_is_a_parse_failure_not_a_shape_failure():
    # A str/bytes document IS the JSON text of a header, so a non-JSON one fails
    # parsing by name rather than being silently taken as a shape error.
    record = C.extract_frame_observation('text')
    assert record['reasons'] == [C.FRAME_HEADER_NOT_JSON]


# ------------------------------------------------------------------- batch pairing

def test_the_batch_pairs_every_frame_by_its_own_id_and_timestamp():
    third = wire(SUCCESS_OBSERVATION, frame_id=5, timestamp_ns=2000005000)
    report = C.extract_batch([WIRE_SUCCESS, third], identities=[
        (3, 1000005000), (5, 2000005000)])
    assert report['emitted_count'] == 2 and report['usable_count'] == 2
    assert report['paired_count'] == 2
    assert [frame['frame_id'] for frame in report['frames']] == [3, 5]
    assert [frame['timestamp_ns'] for frame in report['frames']] == [1000005000,
                                                                    2000005000]
    assert report['stop'] is False and report['skipped_after_stop'] == []


def test_a_header_that_does_not_carry_its_declared_identity_is_never_reassigned():
    report = C.extract_batch([WIRE_SUCCESS], identities=[(4, 1000005000)])
    frame = report['frames'][0]
    assert frame['identity_ok'] is False
    assert frame['identity_reason'] == C.FRAME_IDENTITY_MISMATCH
    assert frame['frame_id'] == 3, 'the header keeps its own id, never the declared one'
    assert frame['usable'] is False and frame['stop'] is True
    assert report['stop'] is True and report['stop_index'] == 0

    report = C.extract_batch([WIRE_SUCCESS], identities=[(3, 1)])
    assert report['frames'][0]['identity_reason'] == C.FRAME_IDENTITY_MISMATCH


def test_the_same_frame_twice_in_one_campaign_is_refused():
    report = C.extract_batch([WIRE_SUCCESS, WIRE_SUCCESS],
                             stop_at_unusable=False)
    assert report['frames'][0]['usable'] is True
    second = report['frames'][1]
    assert second['identity_ok'] is False
    assert second['identity_reason'] == C.FRAME_IDENTITY_DUPLICATE
    assert second['usable'] is False
    assert second['frame_id'] == 3, 'a replay is the same frame, not a second one'


def test_the_batch_stops_emitting_at_the_first_unusable_observation():
    third = wire(SUCCESS_OBSERVATION, frame_id=7, timestamp_ns=3000005000)
    failure = wire(FAILURE_OBSERVATION, frame_id=4, timestamp_ns=1000005001)
    report = C.extract_batch([WIRE_SUCCESS, failure, third])
    assert report['stop'] is True and report['stop_index'] == 1
    assert report['stop_reason'] == 'HARDWARE_IO'
    assert [frame['frame_id'] for frame in report['frames']] == [3, 4]
    assert report['emitted_count'] == 2 and report['input_count'] == 3
    assert [entry['index'] for entry in report['skipped_after_stop']] == [2]
    assert report['skipped_after_stop'][0]['frame_id'] == 7


def test_a_missing_member_stops_the_batch_too():
    report = C.extract_batch([WIRE_SUCCESS, document(frame_id=8)])
    assert report['stop'] is True and report['stop_index'] == 1
    assert report['stop_reason'] == C.OBSERVATION_MEMBER_MISSING


def test_diagnostic_mode_reads_every_frame_instead_of_stopping():
    failure = wire(FAILURE_OBSERVATION, frame_id=4, timestamp_ns=1000005001)
    report = C.extract_batch([WIRE_SUCCESS, failure, document(frame_id=8)],
                             stop_at_unusable=False)
    assert report['emitted_count'] == 3 and report['input_count'] == 3
    assert report['unusable_count'] == 2
    assert [frame['index'] for frame in report['frames']] == [0, 1, 2]
    assert report['stop'] is True and report['stop_index'] == 1
    assert report['skipped_after_stop'] == []


def test_a_short_identity_list_is_refused_rather_than_shifted():
    failure = wire(FAILURE_OBSERVATION, frame_id=4, timestamp_ns=1000005001)
    with pytest.raises(ValueError, match=C.IDENTITY_LENGTH_MISMATCH):
        C.extract_batch([WIRE_SUCCESS, failure], identities=[(3, 1000005000)])


def test_no_rejection_ever_yields_a_number():
    """The property behind every case above: unavailable implies value is None."""
    mutations = [WIRE_SUCCESS, wire(FAILURE_OBSERVATION, frame_id=2, timestamp_ns=2),
                 document(frame_id=20), document('text', frame_id=21)]
    index = 100
    for gain in (True, False, 0, 77, -1, 1.0, '76', [], {}, None):
        mutations.append(document(observation(in_window_rx_gain_index=gain),
                                  frame_id=index, timestamp_ns=index))
        index += 1
    for attenuation in ([46000], [46000, 46000, 46000], [46000, True], 'x', 46000, None):
        mutations.append(document(observation(chip_tx_attenuation_mdb=attenuation),
                                  frame_id=index, timestamp_ns=index))
        index += 1
    mutations.append(document(observation(drop=('in_window_rx_gain_index',
                                                'chip_tx_attenuation_mdb')),
                              frame_id=index, timestamp_ns=index))
    report = C.extract_batch(mutations, stop_at_unusable=False)
    assert report['emitted_count'] == len(mutations)
    assert report['paired_count'] == len(mutations), 'every fixture is its own frame'
    for frame in report['frames']:
        for reading in (gain_of(frame), attenuation_of(frame)):
            if not reading['available']:
                assert reading['value'] is None
                assert reading['reason'], 'an unavailable reading must be named'


# --------------------------------------------------------------------------- CLI

def run_cli(tmp_path, documents, *, identities=None, continue_after=False, capsys=None):
    frames = tmp_path / 'frames.json'
    frames.write_text(json.dumps(documents), encoding='utf-8')
    argv = ['--frames', str(frames)]
    if identities is not None:
        identity_file = tmp_path / 'identity.json'
        identity_file.write_text(json.dumps(identities), encoding='utf-8')
        argv += ['--identity', str(identity_file)]
    if continue_after:
        argv += ['--continue-after-unusable']
    output = tmp_path / 'report.json'
    argv += ['--output', str(output)]
    code = C.main(argv)
    captured = capsys.readouterr().out if capsys is not None else ''
    return code, json.loads(output.read_text(encoding='utf-8')) if output.exists() else None


def test_the_cli_reports_success_and_exits_zero(tmp_path, capsys):
    code, report = run_cli(tmp_path, [json.loads(WIRE_SUCCESS)], capsys=capsys)
    assert code == 0
    assert report['usable_count'] == 1 and report['stop'] is False


def test_the_cli_exits_nonzero_when_an_observation_is_unavailable(tmp_path, capsys):
    failure = wire(FAILURE_OBSERVATION, frame_id=4, timestamp_ns=1000005001)
    code, report = run_cli(tmp_path, [json.loads(WIRE_SUCCESS),
                                      json.loads(failure)], capsys=capsys)
    assert code == 1
    assert report['stop'] is True and report['stop_reason'] == 'HARDWARE_IO'


def test_the_cli_reads_the_whole_file_only_in_diagnostic_mode(tmp_path, capsys):
    failure = wire(FAILURE_OBSERVATION, frame_id=4, timestamp_ns=1000005001)
    code, report = run_cli(tmp_path, [json.loads(WIRE_SUCCESS),
                                      json.loads(failure)],
                           continue_after=True, capsys=capsys)
    assert code == 1
    assert report['emitted_count'] == 2 and report['unusable_count'] == 1


def test_the_cli_binds_identity_pairs(tmp_path, capsys):
    code, report = run_cli(tmp_path, [json.loads(WIRE_SUCCESS)],
                           identities=[[3, 1000005000]], capsys=capsys)
    assert code == 0 and report['paired_count'] == 1
    code, report = run_cli(tmp_path, [json.loads(WIRE_SUCCESS)], identities=[[4, 1]],
                           capsys=capsys)
    assert code == 1
    assert report['frames'][0]['identity_reason'] == C.FRAME_IDENTITY_MISMATCH


def test_the_cli_names_an_unreadable_input_file(tmp_path, capsys):
    code = C.main(['--frames', str(tmp_path / 'missing.json')])
    assert code == 2
    assert 'error' in json.loads(capsys.readouterr().out)


def test_the_module_has_no_board_or_network_entry_point():
    tree = ast.parse(inspect.getsource(C))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    forbidden = {'socket', 'ssl', 'paramiko', 'pysftp', 'subprocess', 'urllib',
                 'http', 'requests', 'ftplib', 'telnetlib', 'serial'}
    assert imported & forbidden == set(), 'the extractor must stay offline'
    assert 'argparse' in imported and 'json' in imported


def test_the_module_never_writes_a_value_it_did_not_read():
    """The reason vocabulary is closed: every named reason is a module constant."""
    source = textwrap.dedent(inspect.getsource(C))
    tree = ast.parse(source)
    constants = {node.targets[0].id: node.value.value
                 for node in tree.body
                 if isinstance(node, ast.Assign)
                 and len(node.targets) == 1
                 and isinstance(node.targets[0], ast.Name)
                 and isinstance(node.value, ast.Constant)
                 and isinstance(node.value.value, str)
                 and node.targets[0].id.isupper()}
    for name, value in constants.items():
        if name.endswith(('_MISSING', '_MALFORMED', '_OUT_OF_RANGE', '_MISMATCH',
                          '_DUPLICATE', '_CONTRADICTORY', '_WITHOUT_REASON',
                          '_NOT_JSON', '_NOT_AN_OBJECT', '_NOT_IQ_FRAME',
                          '_NOT_INTEGER')):
            assert value.isupper() and ' ' not in value
