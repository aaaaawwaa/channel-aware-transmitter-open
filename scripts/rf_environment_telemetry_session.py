"""Runner-side telemetry for the 12-frame 46 dB diagnostic (no product code changed).

This module subclasses the reviewed S2 board session, so the product tree
(pynq_overlay/sw/*, rf_overlay/sw/*), the frozen environment baseline/thresholds and
the frozen 18-frame pilot entries (scripts/rf_environment_pilot.py,
scripts/rf_environment_board_run.py) stay untouched.  Four insertion points from the
readiness round (evidence/rf-teaching/s2-46db-telemetry-readiness-2026-10-19) are
implemented here:

* P1  pre-frame RX1 gain read            -> TelemetryMixin.capture, BEFORE super()
* P2  observe_timing on physical frames  -> CaptureTimingClient (runner-side wrapper;
                                            an OFF frame must never carry it -- N3/N7)
* P3  post-frame gain + timing brackets  -> TelemetryMixin.capture, AFTER super()
* P4  init/configure readback retained   -> TelemetryMixin._wait_control

Every frame writes <tag>.telemetry.json.  A value this interface cannot read is
written as the literal NOT_AVAILABLE with a named reason; it is never replaced by a
request-side number, a default or a guess.  The session-level attenuation readback is
the ACTUAL TX1 register value from rf.init/rf.configure (R3), not the requested 46000,
and is itself NOT_AVAILABLE when the control result did not retain it.

This file is board-run (uploaded next to the frozen session modules) and host-import
safe: nothing here imports a board module at import time.
"""
import argparse
import json
from pathlib import Path
import time

# The one approved domain of this diagnostic (readiness NOTES section 4.2): the
# candidate release and the single-source 46 dB attenuation.  Both are checked, not
# assumed -- a different installed release or a different teaching profile refuses
# before the first capture instead of silently measuring another domain.
APPROVED_RELEASE = '03b9b883a61f29605be2c1f3817024d0'
APPROVED_ATTENUATION_MDB = 46000

NOT_AVAILABLE = 'NOT_AVAILABLE'
TELEMETRY_SCHEMA = 'rf-46db-frame-telemetry-v1'

# Readiness NOTES section 2 (N1..N7): values this path genuinely cannot read.  Every
# frame carries the whole map with the literal NOT_AVAILABLE and a named reason, so a
# reader can never mistake a request-side number for a measurement.
NOT_AVAILABLE_ITEMS = {
    'per_frame_tx_attenuation_mdB': 'PER_FRAME_TX_ATTENUATION_NOT_READ_BACK_ON_THIS_PATH',
    'intra_frame_rx_gain_trace': 'DAEMON_SINGLE_THREADED_NO_IN_WINDOW_READ_PATH',
    'off_frame_capture_timing': 'TIMING_BRACKETS_ONLY_ON_PHYSICAL_FRAMES',
    'rf_level_dbm': 'NO_ABSOLUTE_LEVEL_INTERFACE_ON_THIS_PATH',
    'headroom_lease_nonce': 'OUT_OF_SCOPE_ENVIRONMENT_CONTROL_TOKEN_PATH',
    'scene_effective_amplitude': 'REQUEST_SIDE_SCENE_PARAMETER_ONLY',
    'per_frame_settle_wait_us': 'PRODUCT_UI_PATH_SENDS_NO_SETTLE_WAIT',
}

# The keys a per-frame telemetry document must carry for the campaign to accept it.
REQUIRED_TELEMETRY_KEYS = ('schema', 'tag', 'physical', 'frame', 'pre_frame_gain',
                           'post_frame_gain', 'capture_timing',
                           'session_attenuation_readback', 'not_available')

GAIN_FIELDS = ('gain_index_before', 'gain_index_after', 'mode_rx1', 'rx_enable_raw',
               'agc_config1_raw', 'agc_config2_raw', 'digital_gain_code', 'index_stable',
               'started_at_monotonic_ns', 'finished_at_monotonic_ns')
TIMING_FIELDS = ('capture_request_id', 'capture_timestamp_ns', 'samples', 'ratio',
                 'tx_on_begin_ns', 'tx_on_end_ns', 'rx_arm_begin_ns', 'rx_arm_end_ns',
                 'drain_begin_ns', 'drain_end_ns', 'tx_off_begin_ns', 'tx_off_end_ns',
                 'settle_wait_requested_us')
READBACK_FIELDS = ('lo_hz', 'sample_rate_hz', 'rx_bandwidth_hz', 'tx_bandwidth_hz',
                   'attenuation_mdB', 'phy_mode_1r1t', 'product_id', 'hardware_verified')
IDENTITY_FIELDS = ('release_id', 'daemon', 'protocol_major', 'protocol_minor')
FRAME_IDENTITY_FIELDS = ('capture_id', 'release_id', 'op', 'physical',
                         'connection_generation', 'config_revision', 'request_id',
                         'daemon_timestamp_ns', 'completed_at_monotonic', 'samples',
                         'ratio', 'iq_sha256', 'pre_sampled_at_monotonic_ns',
                         'post_sampled_at_monotonic_ns')

TIMING_SCOPE = 'monotonic_api_brackets_not_sample_edges'
OFF_TIMING_REASON = 'TIMING_BRACKETS_ONLY_ON_PHYSICAL_FRAMES'
READBACK_MISSING_REASON = 'SESSION_READBACK_NOT_RETAINED'


class TelemetryRefusal(RuntimeError):
    """A named refusal that happens before any capture (authorization/domain gate)."""

    def __init__(self, code, message):
        super().__init__('%s: %s' % (code, message))
        self.code = code


def _identity(source):
    if not isinstance(source, dict):
        return {name: NOT_AVAILABLE for name in IDENTITY_FIELDS}
    return {name: source.get(name, NOT_AVAILABLE) for name in IDENTITY_FIELDS}


def unavailable_gain(reason, error=None):
    """The NOT_AVAILABLE gain shape; a missing read is never a zero or a request value."""
    value = {'available': False, 'reason': reason, 'error': error, 'source': None,
             'scope': None, 'identity': {name: NOT_AVAILABLE for name in IDENTITY_FIELDS}}
    for name in GAIN_FIELDS:
        value[name] = NOT_AVAILABLE
    return value


def probe_gain(client):
    """R1: one explicit read-only RX1 gain observation (P1 / P3)."""
    try:
        observed = client.observe_rx_gain()
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        return unavailable_gain('OBSERVE_RX_GAIN_ERROR',
                                '%s: %s' % (type(exc).__name__, exc))
    if not isinstance(observed, dict):
        return unavailable_gain('OBSERVE_RX_GAIN_MALFORMED')
    value = unavailable_gain(None)
    value['identity'] = _identity(observed)
    observation = observed.get('gain_observation')
    if observation is None:
        value['reason'] = observed.get('reason') or 'OBSERVE_RX_GAIN_UNAVAILABLE'
        return value
    if not isinstance(observation, dict):
        value['reason'] = 'OBSERVE_RX_GAIN_MALFORMED'
        return value
    value.update(available=True, reason=None, source=observation.get('source', NOT_AVAILABLE),
                 scope=observation.get('scope', NOT_AVAILABLE))
    for name in GAIN_FIELDS:
        if observation.get(name) is not None:
            value[name] = observation[name]
    return value


def unavailable_timing(reason, error=None):
    value = {'available': False, 'reason': reason, 'error': error, 'scope': None,
             'identity': {name: NOT_AVAILABLE for name in IDENTITY_FIELDS}}
    for name in TIMING_FIELDS:
        value[name] = NOT_AVAILABLE
    return value


def probe_timing(client, frame, physical):
    """R5: API brackets for the CURRENT physical frame only (N3: never for an OFF frame).

    The brackets are monotonic API call brackets, not sample edges and not a gain
    trace; a zero settle wait is recorded as the field the daemon reports (0), and an
    unreported/absent one stays NOT_AVAILABLE.
    """
    if not physical:
        return unavailable_timing(OFF_TIMING_REASON)
    if frame is None:
        return unavailable_timing('NO_CURRENT_FRAME_FOR_TIMING')
    try:
        observed = client.observe_capture_timing(frame)
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        return unavailable_timing('OBSERVE_TIMING_ERROR',
                                  '%s: %s' % (type(exc).__name__, exc))
    if not isinstance(observed, dict):
        return unavailable_timing('OBSERVE_TIMING_MALFORMED')
    value = unavailable_timing(None)
    value['identity'] = _identity(observed)
    trace = observed.get('last_capture_timing')
    if trace is None:
        value['reason'] = observed.get('reason') or 'NO_CAPTURE_TIMING'
        return value
    if not isinstance(trace, dict):
        value['reason'] = 'OBSERVE_TIMING_MALFORMED'
        return value
    value.update(available=True, reason=None, scope=trace.get('scope', NOT_AVAILABLE))
    for name in TIMING_FIELDS:
        if trace.get(name) is not None:
            value[name] = trace[name]
    return value


def session_readback(control_readbacks):
    """R3: the ACTUAL TX1 attenuation readback retained per init/configure action."""
    result = {}
    for action in ('init', 'configure'):
        reply = control_readbacks.get(action) if isinstance(control_readbacks, dict) else None
        native = reply.get('result') if isinstance(reply, dict) else None
        entry = {'reason': None}
        if not isinstance(native, dict):
            for name in READBACK_FIELDS:
                entry[name] = NOT_AVAILABLE
            entry['reason'] = READBACK_MISSING_REASON
        else:
            for name in READBACK_FIELDS:
                entry[name] = native.get(name, NOT_AVAILABLE)
        result[action] = entry
    return result


def frame_identity(frame, row):
    """R4/R6: the per-frame daemon/host timestamps and the bindable capture identity."""
    identity = {name: NOT_AVAILABLE for name in FRAME_IDENTITY_FIELDS}
    if frame is not None:
        data = frame.to_dict()
        for name in FRAME_IDENTITY_FIELDS:
            if data.get(name) is not None:
                identity[name] = data[name]
        for name, readback in (('pre_sampled_at_monotonic_ns', frame.pre_readback),
                               ('post_sampled_at_monotonic_ns', frame.post_readback)):
            stamp = getattr(readback, 'sampled_at_monotonic_ns', None)
            if stamp is not None:
                identity[name] = stamp
    if isinstance(row, dict):
        for name in ('capture_id', 'release_id', 'iq_sha256'):
            if identity[name] == NOT_AVAILABLE and row.get(name) is not None:
                identity[name] = row[name]
    return identity


def build_frame_telemetry(tag, physical, frame, row, pre_gain, post_gain, timing,
                          readback, error=None):
    return dict(schema=TELEMETRY_SCHEMA, tag=tag, physical=bool(physical),
                assembled_at_monotonic=time.monotonic(),
                frame=frame_identity(frame, row),
                pre_frame_gain=pre_gain, post_frame_gain=post_gain,
                capture_timing=timing, session_attenuation_readback=readback,
                not_available={name: {'value': NOT_AVAILABLE, 'reason': reason}
                               for name, reason in NOT_AVAILABLE_ITEMS.items()},
                error=error)


class CaptureTimingClient:
    """P2: add observe_timing to PHYSICAL captures only; never to an OFF frame.

    The product UI builds {samples, ratio, cable_confirmed, attenuation_mdB} and never
    requests timing (N7); this runner-side wrapper is what makes R5 available without
    touching ui_model.py or the client.
    """

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def capture(self, args, physical=False):
        wire = dict(args)
        if physical:
            wire['observe_timing'] = True
        elif 'observe_timing' in wire or 'settle_wait_us' in wire:
            raise ValueError('capture timing may only be requested for a physical frame')
        return self.inner.capture(wire, physical=physical)


def wrap_capture_requests(evidence_client):
    """Wrap the EvidenceClient's inner client exactly once; returns the same object."""
    inner = getattr(evidence_client, 'client', None)
    if inner is None or isinstance(inner, CaptureTimingClient):
        return evidence_client
    evidence_client.client = CaptureTimingClient(inner)
    return evidence_client


class TelemetryMixin:
    """P1/P3/P4 for a session whose super().capture() is the reviewed environment path."""

    def _telemetry_readbacks(self):
        readbacks = getattr(self, '_control_readbacks', None)
        if readbacks is None:
            readbacks = self._control_readbacks = {}
        return readbacks

    def _wait_control(self, action, deadline=25.0):
        result = super()._wait_control(action, deadline)
        if action in ('init', 'configure'):
            self._telemetry_readbacks()[action] = result
        return result

    def capture(self, tag, physical):
        self._pre_gain = probe_gain(self.client)                     # P1
        try:
            row = super().capture(tag, physical)
        except Exception as exc:                                     # noqa: BLE001
            self._write_telemetry(tag, physical, None,
                                  '%s: %s' % (type(exc).__name__, exc))
            raise
        telemetry = self._write_telemetry(tag, physical, row, None)
        row['telemetry'] = telemetry
        row['telemetry_file'] = tag + '.telemetry.json'
        return row

    def _write_telemetry(self, tag, physical, row, error):
        frame = getattr(self.client, 'frame', None)
        pre_gain = getattr(self, '_pre_gain', None)
        if not isinstance(pre_gain, dict):
            pre_gain = unavailable_gain('PRE_FRAME_GAIN_NOT_PROBED')
        telemetry = build_frame_telemetry(
            tag, physical, frame, row, pre_gain, probe_gain(self.client),   # P3
            probe_timing(self.client, frame, physical),                     # P3
            session_readback(self._telemetry_readbacks()), error)           # P4
        with (Path(self.output) / (tag + '.telemetry.json')).open(
                'x', encoding='utf-8') as handle:
            json.dump(telemetry, handle, indent=2, allow_nan=False)
        return telemetry


def telemetry_session_class():
    """The board session object; the board imports are deferred to construction.

    The workstation fallback must never hide why the board import failed: the first
    ImportError is the real one (an incomplete upload set shows up here as a
    ModuleNotFoundError naming the missing sibling), so it is re-raised unchanged with
    the failed fallback attached as the explicit __cause__, instead of being replaced by
    the fallback's own "No module named 'scripts'".
    """
    try:
        from rf_environment_board_session import EnvironmentSession
    except ImportError as board_error:  # pragma: no cover - workstation import path
        try:
            from scripts.rf_environment_board_session import EnvironmentSession
        except ImportError as fallback_error:
            raise board_error from fallback_error

    class TelemetrySession(TelemetryMixin, EnvironmentSession):
        def __init__(self, expected, output):
            super().__init__(expected, output)
            try:
                try:
                    from rf_teaching_profile import ATTENUATION_MDB
                except ImportError as exc:
                    raise TelemetryRefusal('TEACHING_PROFILE_UNAVAILABLE', str(exc))
                if ATTENUATION_MDB != APPROVED_ATTENUATION_MDB:
                    raise TelemetryRefusal(
                        'ATTENUATION_DOMAIN_MISMATCH',
                        'installed teaching profile is %s mdB, this diagnostic is '
                        'approved only at %d mdB' % (ATTENUATION_MDB,
                                                     APPROVED_ATTENUATION_MDB))
                self._control_readbacks = {}
                self._pre_gain = None
                wrap_capture_requests(self.client)
            except Exception:
                self.close()
                raise

    return TelemetrySession


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-release', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cable-confirmed', action='store_true')
    args = parser.parse_args(argv)
    if not args.cable_confirmed:
        raise SystemExit('REFUSE: --cable-confirmed attestation required for the TX1-RX1 '
                         'cable; no session and no capture was created (environment '
                         'control-token path, no HEADROOM nonce)')
    if args.expected_release != APPROVED_RELEASE:
        raise SystemExit('REFUSE: the 12-frame 46 dB diagnostic is approved only for '
                         'release ' + APPROVED_RELEASE)
    if args.output.exists():
        raise SystemExit('REFUSE: output directory must not exist: %s' % args.output)
    try:
        from rf_environment_telemetry_pilot import campaign
    except ImportError:  # pragma: no cover - workstation import path
        from scripts.rf_environment_telemetry_pilot import campaign
    factory = lambda: telemetry_session_class()(args.expected_release, args.output)
    args.output.mkdir(parents=True, exist_ok=False)
    result = campaign(factory, args.output, args.expected_release)
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ('records', 'sessions')}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
