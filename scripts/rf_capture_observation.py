"""Read one physical frame's in-window `capture_observation` member, per frame.

WHY THIS IS NEW (no existing extractor was reused)
--------------------------------------------------
`capture_observation` has exactly one producer and, before this module, no Python
reader at all:

* producer: `rf_overlay/sw/board_app/rf_daemon.c:capture_observation_json` (emitted
  through `rf_proto_write_iq_frame_observed`, `rf_protocol.c:531`), only from
  `op_loopback_physical_capture`;
* the frozen client `pynq_overlay/sw/rf_status_client.py::_iq_header` is a WHITELIST
  validator: it returns only samples/bytes/sample_rate_hz/ratio/timestamp_ns and
  silently DROPS every other member, so the observation never reaches the caller;
  `pynq_overlay/sw/rf_rx_types.py::RxIqFrame` has no field for it either.

A repository-wide search for the member name finds only the C producer/tests, the
reviewer's own throw-away probes, and prose.  So this is a genuinely missing small
piece, written to be the caller-side half of the observation: it turns one frame
header into per-field availability, and it can never invent a value.

WHAT IT DOES NOT DO
-------------------
It does not capture, retry, refill, configure, or talk to a board, and it does not
change any frozen threshold/baseline/schema/classification.  It only reads the header
JSON document it is handed (offline fixtures today; a runner-side path later).  A
missing, mis-shaped, mis-typed, out-of-range or self-contradictory field becomes a
NAMED unavailability -- never 0, never a default, never the other reading, never a
value from another frame.

THE FIELD CONTRACT (read from the C writer, not from prose)
-----------------------------------------------------------
`rf_daemon.c:1651-1682` writes, inside the frame header:

    "capture_observation":{
      "scope":"single_read_in_capture_window_plus_chip_attenuation_readback",
      ... either "in_window_rx_gain_index":<0..76>
          or  "in_window_rx_gain_index":null,"in_window_rx_gain_unavailable":"<CODE>"
      ... either "chip_tx_attenuation_mdb":[<tx1>,<tx2>]
          or  "chip_tx_attenuation_mdb":null,"chip_tx_attenuation_unavailable":"<CODE>" }

`<CODE>` is a protocol error-code name (`HARDWARE_IO` / `INVALID_STATE` / `TIMEOUT`),
the same vocabulary a refusal uses.  The two readings have INDEPENDENT availability:
one may be present while the other is named unavailable.  The gain index bound 0..76
is the part's full-table index bound (`rf_core.c:1997,2035`); the attenuation bound
0..89750 mdB is the chip's own 0..89.75 dB / 0.25 dB-step range (`rf_state.h:40`
shows the project's own 46000 mdB value for the cabled loopback).

STOP SEMANTICS (no silent continuation, no refill)
--------------------------------------------------
The only safe consumer behaviour is "observation unavailable -> stop this campaign".
`extract_batch(..., stop_at_unusable=True)` (the default) therefore stops EMITTING
frames at the first unusable one, and reports `skipped_after_stop` so nothing is
silently dropped.  No record is ever renumbered, re-paired or back-filled; a frame
whose declared identity does not match its own header is refused and never reassigned
to a neighbour.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXTRACTION_SCHEMA = 'rf-46db-capture-observation-extraction-v1'

OBSERVATION_MEMBER = 'capture_observation'
EXPECTED_SCOPE = 'single_read_in_capture_window_plus_chip_attenuation_readback'
EXPECTED_FRAME_TYPE = 'iq_frame'

GAIN_FIELD = 'in_window_rx_gain_index'
GAIN_UNAVAILABLE_FIELD = 'in_window_rx_gain_unavailable'
GAIN_INDEX_MIN = 0
GAIN_INDEX_MAX = 76              # rf_core.c:2035 (FULL_TABLE_GAIN_INDEX, part full table)

ATTENUATION_FIELD = 'chip_tx_attenuation_mdb'
ATTENUATION_UNAVAILABLE_FIELD = 'chip_tx_attenuation_unavailable'
ATTENUATION_PAIR_LENGTH = 2      # TX1 and TX2, never one channel twice
CHIP_TX_ATTENUATION_MDB_MIN = 0
CHIP_TX_ATTENUATION_MDB_MAX = 89750   # 0..89.75 dB in 0.25 dB steps, chip range

# Named reasons.  These are the extractor's own vocabulary: they say what is wrong
# with the DOCUMENT.  When the daemon itself named an unavailability, that protocol
# code is carried through unchanged instead (see `_reading`).
FRAME_HEADER_NOT_JSON = 'FRAME_HEADER_NOT_JSON'
FRAME_HEADER_NOT_AN_OBJECT = 'FRAME_HEADER_NOT_AN_OBJECT'
FRAME_TYPE_MISSING = 'FRAME_TYPE_MISSING'
FRAME_TYPE_NOT_IQ_FRAME = 'FRAME_TYPE_NOT_IQ_FRAME'
FRAME_ID_MISSING = 'FRAME_ID_MISSING'
FRAME_TIMESTAMP_MISSING = 'FRAME_TIMESTAMP_MISSING'
FRAME_IDENTITY_MISMATCH = 'FRAME_IDENTITY_MISMATCH'
FRAME_IDENTITY_DUPLICATE = 'FRAME_IDENTITY_DUPLICATE'
FRAME_IDENTITY_NOT_INTEGER = 'FRAME_IDENTITY_NOT_INTEGER'

OBSERVATION_MEMBER_MISSING = 'OBSERVATION_MEMBER_MISSING'
OBSERVATION_MEMBER_NOT_AN_OBJECT = 'OBSERVATION_MEMBER_NOT_AN_OBJECT'
OBSERVATION_SCOPE_MISSING = 'OBSERVATION_SCOPE_MISSING'
OBSERVATION_SCOPE_MISMATCH = 'OBSERVATION_SCOPE_MISMATCH'

GAIN_INDEX_MISSING = 'GAIN_INDEX_MISSING'
GAIN_INDEX_MALFORMED = 'GAIN_INDEX_MALFORMED'
GAIN_INDEX_OUT_OF_RANGE = 'GAIN_INDEX_OUT_OF_RANGE'
GAIN_INDEX_UNAVAILABLE_WITHOUT_REASON = 'GAIN_INDEX_UNAVAILABLE_WITHOUT_REASON'
GAIN_INDEX_CONTRADICTORY = 'GAIN_INDEX_CONTRADICTORY'

CHIP_TX_ATTENUATION_MISSING = 'CHIP_TX_ATTENUATION_MISSING'
CHIP_TX_ATTENUATION_MALFORMED = 'CHIP_TX_ATTENUATION_MALFORMED'
CHIP_TX_ATTENUATION_OUT_OF_RANGE = 'CHIP_TX_ATTENUATION_OUT_OF_RANGE'
CHIP_TX_ATTENUATION_UNAVAILABLE_WITHOUT_REASON = (
    'CHIP_TX_ATTENUATION_UNAVAILABLE_WITHOUT_REASON')
CHIP_TX_ATTENUATION_CONTRADICTORY = 'CHIP_TX_ATTENUATION_CONTRADICTORY'

IDENTITY_LENGTH_MISMATCH = 'IDENTITY_LENGTH_MISMATCH'


def _unavailable(reason):
    """A named unavailability; `value` stays None so nothing can read a zero."""
    return {'available': False, 'value': None, 'reason': reason}


def _available(value):
    return {'available': True, 'value': value, 'reason': None}


def _is_int(value):
    """True integers only: `bool` is an `int` subclass in Python and must not pass.

    A JSON `true` reaching the gain index would otherwise be read as the number 1.
    """
    return type(value) is int


def _parse_document(document):
    """Return (object, reason).  A non-parsable document is named, never guessed."""
    if isinstance(document, dict):
        return document, None
    if isinstance(document, (bytes, bytearray)):
        try:
            text = bytes(document).decode('utf-8')
        except UnicodeDecodeError:
            return None, FRAME_HEADER_NOT_JSON
        return _parse_document(text)
    if isinstance(document, str):
        try:
            parsed = json.loads(document)
        except ValueError:
            return None, FRAME_HEADER_NOT_JSON
        return _parse_document(parsed)
    return None, FRAME_HEADER_NOT_AN_OBJECT


def _named_unavailability(member, value_field, reason_field, *, missing, malformed,
                          unavailable_without_reason, contradictory):
    """One reading: a value, or the daemon's own null + NAMED reason.

    Returns None when the caller must validate the value itself.
    """
    has_reason = reason_field in member
    if value_field not in member:
        if not has_reason:
            return _unavailable(missing)
        # The writer emits the null value and its reason as a pair; a reason without the
        # frozen null marker is not the shape this extractor understands.
        return _unavailable(malformed)
    value = member[value_field]
    if value is None:
        reason = member.get(reason_field)
        if not has_reason or not isinstance(reason, str) or not reason:
            return _unavailable(unavailable_without_reason)
        return _unavailable(reason)                      # the daemon's own code, verbatim
    if has_reason:
        # A value AND an unavailability reason for the same reading is a document that
        # says two different things; neither may be preferred silently.
        return _unavailable(contradictory)
    return None                                          # caller validates the value


def _validate_int_or_pair(value, *, pair, low, high, malformed, out_of_range):
    if pair:
        if not isinstance(value, (list, tuple)) or len(value) != ATTENUATION_PAIR_LENGTH:
            return _unavailable(malformed)
        values = list(value)
    else:
        values = [value]
    for item in values:
        if not _is_int(item):
            return _unavailable(malformed)
    for item in values:
        if item < low or item > high:
            return _unavailable(out_of_range)
    # A JSON array stays a plain list so the record is JSON-serialisable as-is.
    return _available(values if pair else values[0])


def _unique(items):
    """Named reasons keep their first-seen order and are never repeated."""
    seen = []
    for item in items:
        if item is not None and item not in seen:
            seen.append(item)
    return seen


def read_observation(header):
    """Per-field availability of `capture_observation` in an already-parsed header.

    Returns (observation_present, scope, gain_reading, attenuation_reading, reasons)
    where each reading is `{'available', 'value', 'reason'}`.  Never raises for a bad
    document and never substitutes a value.
    """
    reasons = []
    if not isinstance(header, dict):
        return (False, None, _unavailable(FRAME_HEADER_NOT_AN_OBJECT),
                _unavailable(FRAME_HEADER_NOT_AN_OBJECT),
                [FRAME_HEADER_NOT_AN_OBJECT])

    frame_type = header.get('type')
    if frame_type is None:
        reasons.append(FRAME_TYPE_MISSING)
    elif frame_type != EXPECTED_FRAME_TYPE:
        reasons.append(FRAME_TYPE_NOT_IQ_FRAME)
    if reasons:
        reason = reasons[0]
        return (OBSERVATION_MEMBER in header, None, _unavailable(reason),
                _unavailable(reason), reasons)

    if OBSERVATION_MEMBER not in header:
        return (False, None, _unavailable(OBSERVATION_MEMBER_MISSING),
                _unavailable(OBSERVATION_MEMBER_MISSING), [])
    member = header[OBSERVATION_MEMBER]
    if not isinstance(member, dict):
        return (True, None, _unavailable(OBSERVATION_MEMBER_NOT_AN_OBJECT),
                _unavailable(OBSERVATION_MEMBER_NOT_AN_OBJECT), [])

    scope = member.get('scope')
    if scope is None:
        reason = OBSERVATION_SCOPE_MISSING
    elif scope != EXPECTED_SCOPE:
        reason = OBSERVATION_SCOPE_MISMATCH
    else:
        reason = None
    if reason is not None:
        # The scope says WHERE the numbers came from.  A different (or absent) scope
        # means these are not the readings this extractor understands.
        return (True, scope if isinstance(scope, str) else None,
                _unavailable(reason), _unavailable(reason), [reason])

    gain = _named_unavailability(
        member, GAIN_FIELD, GAIN_UNAVAILABLE_FIELD, missing=GAIN_INDEX_MISSING,
        malformed=GAIN_INDEX_MALFORMED,
        unavailable_without_reason=GAIN_INDEX_UNAVAILABLE_WITHOUT_REASON,
        contradictory=GAIN_INDEX_CONTRADICTORY)
    if gain is None:
        gain = _validate_int_or_pair(member[GAIN_FIELD], pair=False, low=GAIN_INDEX_MIN,
                                     high=GAIN_INDEX_MAX, malformed=GAIN_INDEX_MALFORMED,
                                     out_of_range=GAIN_INDEX_OUT_OF_RANGE)

    attenuation = _named_unavailability(
        member, ATTENUATION_FIELD, ATTENUATION_UNAVAILABLE_FIELD,
        missing=CHIP_TX_ATTENUATION_MISSING, malformed=CHIP_TX_ATTENUATION_MALFORMED,
        unavailable_without_reason=CHIP_TX_ATTENUATION_UNAVAILABLE_WITHOUT_REASON,
        contradictory=CHIP_TX_ATTENUATION_CONTRADICTORY)
    if attenuation is None:
        attenuation = _validate_int_or_pair(
            member[ATTENUATION_FIELD], pair=True, low=CHIP_TX_ATTENUATION_MDB_MIN,
            high=CHIP_TX_ATTENUATION_MDB_MAX, malformed=CHIP_TX_ATTENUATION_MALFORMED,
            out_of_range=CHIP_TX_ATTENUATION_OUT_OF_RANGE)

    return True, scope, gain, attenuation, []


def extract_frame_observation(document, *, declared_frame_id=None,
                              declared_timestamp_ns=None, source=None):
    """One extraction record for one frame header document.

    `declared_*` is the identity the caller believes this document has (for example the
    campaign row it was downloaded as).  When supplied it must equal the header's own
    value: a frame is never silently re-labelled to match a neighbour, and a mismatch
    makes the observation unusable (and stops the batch).
    """
    header, parse_reason = _parse_document(document)
    record = {
        'index': None,
        'source': source,
        'frame_id': None,
        'timestamp_ns': None,
        'frame_type': None,
        'identity_ok': False,
        'identity_reason': None,
        'observation_present': False,
        'scope': None,
        'in_window_rx_gain_index': _unavailable(FRAME_HEADER_NOT_JSON),
        'chip_tx_attenuation_mdb': _unavailable(FRAME_HEADER_NOT_JSON),
        'usable': False,
        'stop': True,
        'reasons': [],
    }
    if parse_reason is not None:
        record['reasons'] = [parse_reason]
        record['in_window_rx_gain_index'] = _unavailable(parse_reason)
        record['chip_tx_attenuation_mdb'] = _unavailable(parse_reason)
        record['identity_reason'] = parse_reason
        return record

    record['frame_type'] = header.get('type')

    identity_reason = None
    frame_id = header.get('id')
    timestamp_ns = header.get('timestamp_ns')
    if not _is_int(frame_id):
        identity_reason = FRAME_ID_MISSING if frame_id is None else (
            FRAME_IDENTITY_NOT_INTEGER)
    elif not _is_int(timestamp_ns):
        identity_reason = FRAME_TIMESTAMP_MISSING if timestamp_ns is None else (
            FRAME_IDENTITY_NOT_INTEGER)
    else:
        record['frame_id'] = frame_id
        record['timestamp_ns'] = timestamp_ns
        if declared_frame_id is not None and declared_frame_id != frame_id:
            identity_reason = FRAME_IDENTITY_MISMATCH
        elif (declared_timestamp_ns is not None
              and declared_timestamp_ns != timestamp_ns):
            identity_reason = FRAME_IDENTITY_MISMATCH

    present, scope, gain, attenuation, reasons = read_observation(header)
    record['observation_present'] = present
    record['scope'] = scope
    record['in_window_rx_gain_index'] = gain
    record['chip_tx_attenuation_mdb'] = attenuation
    record['reasons'] = list(reasons)

    if identity_reason is not None:
        record['identity_reason'] = identity_reason
        record['reasons'] = [identity_reason] + record['reasons']
        record['in_window_rx_gain_index'] = _unavailable(identity_reason)
        record['chip_tx_attenuation_mdb'] = _unavailable(identity_reason)
        return record

    record['identity_ok'] = True
    record['usable'] = bool(gain['available'] and attenuation['available'])
    record['stop'] = not record['usable']
    if not record['usable']:
        record['reasons'] = _unique(
            list(record['reasons'])
            + [reading['reason'] for reading in (gain, attenuation)
               if not reading['available']])
    return record


def extract_batch(documents, *, identities=None, stop_at_unusable=True):
    """Per-frame extraction over a campaign's header documents, in input order.

    `identities` is an optional sequence of `(frame_id, timestamp_ns)` pairs, positionally
    bound to `documents` (the campaign's own row order).  A header that does not carry the
    identity it was paired with is refused, never reassigned.

    With `stop_at_unusable` (default) the batch stops EMITTING at the first unusable frame
    -- the observation is the reason the campaign exists, so "unavailable" must end it
    rather than be averaged away.  The frames that were not emitted are listed in
    `skipped_after_stop`, so "stopped" never means "silently dropped".  Pass False only
    for offline inspection of a whole downloaded set.
    """
    documents = list(documents)
    if identities is not None:
        identities = list(identities)
        if len(identities) != len(documents):
            raise ValueError('%s: %d identities for %d frame headers'
                             % (IDENTITY_LENGTH_MISMATCH, len(identities),
                                len(documents)))

    frames = []
    skipped = []
    seen = {}
    stop_index = None
    stop_reason = None
    stop = False

    for index, document in enumerate(documents):
        declared_id = declared_ts = None
        if identities is not None:
            declared_id, declared_ts = identities[index]
        record = extract_frame_observation(
            document, declared_frame_id=declared_id, declared_timestamp_ns=declared_ts,
            source='frame[%d]' % index)
        record['index'] = index

        if record['identity_ok']:
            key = (record['frame_id'], record['timestamp_ns'])
            if key in seen:
                # The same frame twice in one campaign is a replay, not two
                # measurements: it must not be read as a second observation.
                record['identity_ok'] = False
                record['identity_reason'] = FRAME_IDENTITY_DUPLICATE
                record['reasons'] = [FRAME_IDENTITY_DUPLICATE] + record['reasons']
                record['in_window_rx_gain_index'] = _unavailable(FRAME_IDENTITY_DUPLICATE)
                record['chip_tx_attenuation_mdb'] = _unavailable(FRAME_IDENTITY_DUPLICATE)
                record['usable'] = False
                record['stop'] = True
            else:
                seen[key] = index

        if stop and stop_at_unusable:
            # Emitted frames stop here, but the frame is still accounted for by its own
            # identity and reason -- "stopped" must never mean "silently dropped".
            skipped.append({'index': index, 'frame_id': record['frame_id'],
                            'timestamp_ns': record['timestamp_ns'],
                            'reason': record['reasons'][0] if record['reasons'] else None})
            continue
        if record['stop'] and stop_index is None:
            stop = True
            stop_index = index
            stop_reason = record['reasons'][0] if record['reasons'] else None
        frames.append(record)

    return {
        'schema': EXTRACTION_SCHEMA,
        'scope_expected': EXPECTED_SCOPE,
        'input_count': len(documents),
        'emitted_count': len(frames),
        'usable_count': sum(1 for frame in frames if frame['usable']),
        'unusable_count': sum(1 for frame in frames if not frame['usable']),
        'paired_count': sum(1 for frame in frames if frame['identity_ok']),
        'stop': stop,
        'stop_index': stop_index,
        'stop_reason': stop_reason,
        'skipped_after_stop': skipped,
        'frames': frames,
    }


def load_documents(path):
    """Read a JSON object / JSON array / JSON Lines file of frame headers."""
    text = Path(path).read_text(encoding='utf-8')
    stripped = text.lstrip()
    if stripped.startswith('['):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError('a JSON array of frame headers was expected')
        return parsed
    if stripped.startswith('{'):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    return [line for line in text.splitlines() if line.strip()]


def load_identities(path):
    parsed = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(parsed, list):
        raise ValueError('an array of [frame_id, timestamp_ns] pairs was expected')
    pairs = []
    for item in parsed:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError('each identity must be [frame_id, timestamp_ns]')
        pairs.append((item[0], item[1]))
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Extract the in-window capture_observation of physical IQ frame '
                    'headers (offline; no board, no capture, no retry, no default).')
    parser.add_argument('--frames', required=True,
                        help='JSON object, JSON array or JSON Lines of frame headers')
    parser.add_argument('--identity', default=None,
                        help='optional array of [frame_id, timestamp_ns] to bind pairing')
    parser.add_argument('--output', default=None, help='write the report JSON here')
    parser.add_argument('--continue-after-unusable', action='store_true',
                        help='offline inspection only: parse every frame instead of '
                             'stopping at the first unusable observation')
    args = parser.parse_args(argv)

    try:
        documents = load_documents(args.frames)
        identities = load_identities(args.identity) if args.identity else None
        report = extract_batch(documents, identities=identities,
                               stop_at_unusable=not args.continue_after_unusable)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'error': '%s: %s' % (type(exc).__name__, exc)}, indent=2))
        return 2

    text = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(text + '\n', encoding='utf-8')
    print(text)
    # A stopped campaign is a failure, not a partial success: nothing may continue and
    # no missing value may be refilled.
    return 0 if (not report['stop'] and report['emitted_count'] > 0
                 and report['unusable_count'] == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
