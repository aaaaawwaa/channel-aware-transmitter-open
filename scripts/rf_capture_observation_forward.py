"""Runner-side forwarding of the frozen frame header's `capture_observation` member.

THE GAP THIS CLOSES
-------------------
The daemon appends `capture_observation` to the physical frame header
(`rf_overlay/sw/board_app/rf_daemon.c:capture_observation_json`, written through
`rf_proto_write_iq_frame_observed`).  The frozen client is a WHITELIST reader:

    pynq_overlay/sw/rf_status_client.py:1015  _iq_header(response) -> {samples, bytes,
                                                         sample_rate_hz, ratio, timestamp_ns}

so the member is validated-not-at-all and dropped, and `RxIqFrame`
(`pynq_overlay/sw/rf_rx_types.py:70`) has no field for it.  A runner therefore cannot see
the observation even on a daemon that emits it.

WHAT THIS DOES (and deliberately does not do)
---------------------------------------------
* It does NOT change the frozen client, its whitelist, the frame header, the telemetry
  schema (`rf-46db-frame-telemetry-v1`) or any product module.  The client keeps returning
  exactly what it returned before -- the tests compare the two results byte for byte.
* It wraps the ALREADY CONSTRUCTED inner client (the same object the reviewed
  `wrap_capture_requests` seam exposes) and retains the raw header dict that the frozen
  `_iq_header` was called with, for the frame that is being built.
* It turns that header into one extraction record per frame using
  `scripts.rf_capture_observation` and writes it as an ADDITIVE sidecar file
  (`<tag>.capture-observation.json`) beside the existing `<tag>.telemetry.json`, so no
  frozen document gains or loses a key.
* Pairing is checked against the frame's OWN identity (`request_id` vs the header's `id`,
  `daemon_timestamp_ns` vs the header's `timestamp_ns`).  If the retained header is not
  that frame's header -- a later capture on another path, a re-used array, anything -- the
  observation is a NAMED refusal, never another frame's reading.
* `stop` is carried through unchanged from the extractor: an unusable observation must end
  the campaign, and this module never supplies a zero, a default or a replacement.

WHY AN INSTANCE-LEVEL HOOK IS THE MINIMAL SEAM
----------------------------------------------
`RfStatusClient._capture_exchange` builds `RxIqFrame` from a local `meta`; the raw response
`dict` never leaves that frame.  `_iq_header(response)` is called with exactly that dict
(`:1001`), once per capture, and its return value is what the frozen code continues with.
Retaining `dict(response)` there adds one name to the inner client instance and nothing to
the frozen module; the reviewed runner seams (`CaptureTimingClient`, `wrap_capture_requests`)
already extend the client this way rather than editing it.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts import rf_capture_observation as C

SIDECAR_SCHEMA = 'rf-46db-capture-observation-sidecar-v1'

FRAME_HEADER_NOT_RETAINED = 'FRAME_HEADER_NOT_RETAINED'
NO_CLIENT = 'NO_CLIENT'

#: The only names this module adds to a client instance.
HOOK_FLAG = '_capture_observation_hook'
HEADER_ATTR = 'last_frame_header'


class ForwardingError(RuntimeError):
    """A named refusal that happens before any capture (nothing was armed or started)."""

    def __init__(self, code, message):
        super().__init__('%s: %s' % (code, message))
        self.code = code


def innermost_client(client):
    """Follow the reviewed wrapper chain (`CaptureTimingClient.inner`, ...) to the reader."""
    seen = set()
    while client is not None and id(client) not in seen:
        seen.add(id(client))
        inner = getattr(client, 'inner', None)
        if inner is None:
            break
        client = inner
    return client


def wrap_header_capture(evidence_client):
    """Retain the raw frame header on the inner client; idempotent.

    The frozen validator runs first and its result is returned untouched: the wrapper can
    only observe the header, never change what the client accepts or reports.
    """
    if evidence_client is None or getattr(evidence_client, 'client', None) is None:
        raise ForwardingError(NO_CLIENT, 'the evidence client exposes no inner client')
    client = innermost_client(evidence_client.client)
    if client is None:
        raise ForwardingError(NO_CLIENT, 'no inner client in the wrapper chain')
    if getattr(client, HOOK_FLAG, False):
        return evidence_client
    original = client._iq_header                    # frozen staticmethod, unchanged

    def _iq_header(response):
        meta = original(response)                   # frozen validation first, same result
        client.last_frame_header = dict(response) if isinstance(response, dict) else None
        return meta

    client._iq_header = _iq_header
    client._capture_observation_hook = True
    client.last_frame_header = None
    return evidence_client


def retained_header(evidence_client):
    client = innermost_client(getattr(evidence_client, 'client', None))
    if client is None:
        return None
    return getattr(client, HEADER_ATTR, None)


def _stop_record(frame, reason):
    return {'schema': SIDECAR_SCHEMA,
            'capture_id': getattr(frame, 'capture_id', None),
            'request_id': getattr(frame, 'request_id', None),
            'daemon_timestamp_ns': getattr(frame, 'daemon_timestamp_ns', None),
            'release_id': getattr(frame, 'release_id', None),
            'usable': False, 'stop': True, 'reasons': [reason], 'observation': None}


def frame_observation_sidecar(evidence_client, frame):
    """One sidecar document for ONE frame, paired by the frame's own identity."""
    header = retained_header(evidence_client)
    if frame is None:
        return _stop_record(frame, C.FRAME_ID_MISSING)
    if not isinstance(header, dict):
        return _stop_record(frame, FRAME_HEADER_NOT_RETAINED)
    record = C.extract_frame_observation(
        header,
        declared_frame_id=getattr(frame, 'request_id', None),
        declared_timestamp_ns=getattr(frame, 'daemon_timestamp_ns', None),
        source='frame:' + str(getattr(frame, 'capture_id', '')))
    return {'schema': SIDECAR_SCHEMA,
            'capture_id': getattr(frame, 'capture_id', None),
            'request_id': getattr(frame, 'request_id', None),
            'daemon_timestamp_ns': getattr(frame, 'daemon_timestamp_ns', None),
            'release_id': getattr(frame, 'release_id', None),
            'usable': record['usable'], 'stop': record['stop'],
            'reasons': record['reasons'], 'observation': record}


def write_observation_sidecar(directory, tag, sidecar):
    """Write `<tag>.capture-observation.json`; never overwrite an earlier frame's file."""
    if not isinstance(sidecar, dict) or sidecar.get('schema') != SIDECAR_SCHEMA:
        raise ForwardingError('SIDECAR_MALFORMED', 'a capture-observation sidecar is required')
    path = Path(directory) / (tag + '.capture-observation.json')
    with path.open('x', encoding='utf-8') as handle:
        json.dump(sidecar, handle, indent=2, allow_nan=False)
    return path
