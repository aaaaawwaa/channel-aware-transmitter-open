"""Offline tests for the runner-side capture_observation forwarding.

No board and no frozen client: a small stand-in reproduces the frozen `_iq_header`
whitelist exactly (validate five fields, drop everything else), so the tests can prove the
two claims that matter:

* the frozen validation and its returned metadata are UNCHANGED by the wrapper (and the
  observation never becomes part of it);
* the observation of frame N is paired to frame N -- a stale/re-used header is a named
  refusal, never a neighbour's reading.
"""
import ast
import inspect
import json
from types import SimpleNamespace

import pytest

from scripts import rf_capture_observation as C
from scripts import rf_capture_observation_forward as F

RELEASE = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
SCOPE = C.EXPECTED_SCOPE


class FrozenLikeClient:
    """Stands in for `pynq_overlay/sw/rf_status_client.RfStatusClient`.

    `_iq_header` reproduces the frozen contract: validate five integer/str fields and return
    ONLY those, silently ignoring every other member -- including `capture_observation`.
    """

    @staticmethod
    def _iq_header(response):
        def integer(name, low, high):
            value = response.get(name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('invalid IQ frame field: ' + name)
            return value

        if response.get('direction') != 'rx':
            raise ValueError('invalid IQ frame field: direction')
        if response.get('format') != 's16_iq_le':
            raise ValueError('invalid IQ frame field: format')
        samples = integer('samples', 1, 4096)
        declared = integer('bytes', 0, 4096 * 4)
        if declared != samples * 4:
            raise ValueError('invalid IQ frame field: bytes')
        return {'samples': samples, 'bytes': declared,
                'sample_rate_hz': integer('sample_rate_hz', 1, 0xFFFFFFFF),
                'ratio': integer('ratio', 0, 4),
                'timestamp_ns': integer('timestamp_ns', 0, 0xFFFFFFFFFFFFFFFF)}


class TimingWrapper:
    """Stands in for the reviewed `CaptureTimingClient` wrapper (`client.inner`)."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)


def header(frame_id=3, timestamp_ns=1234567890123, member='ok'):
    value = {'id': frame_id, 'type': 'iq_frame', 'direction': 'rx',
             'format': 's16_iq_le', 'samples': 4096, 'bytes': 16384,
             'sample_rate_hz': 60000000, 'ratio': 0, 'timestamp_ns': timestamp_ns}
    if member == 'ok':
        value['capture_observation'] = {
            'scope': SCOPE, 'in_window_rx_gain_index': 76,
            'chip_tx_attenuation_mdb': [46000, 46000]}
    elif member == 'failure':
        value['capture_observation'] = {
            'scope': SCOPE, 'in_window_rx_gain_index': None,
            'in_window_rx_gain_unavailable': 'HARDWARE_IO',
            'chip_tx_attenuation_mdb': None,
            'chip_tx_attenuation_unavailable': 'HARDWARE_IO'}
    return value


def frame(request_id=3, timestamp_ns=1234567890123, capture_id='rx-abc-g0-c1-1'):
    return SimpleNamespace(capture_id=capture_id, request_id=request_id,
                           daemon_timestamp_ns=timestamp_ns, release_id=RELEASE)


def evidence_client():
    return SimpleNamespace(client=FrozenLikeClient())


def wrapped_evidence_client():
    return F.wrap_header_capture(evidence_client())


def capture(evidence, document):
    """What `_capture_exchange` does: the frozen header call, then the frame."""
    return evidence.client._iq_header(document)


# ------------------------------------------------------------ frozen behaviour is intact

def test_the_frozen_header_result_is_byte_identical_with_and_without_the_wrapper():
    document = header()
    plain = FrozenLikeClient._iq_header(document)
    evidence = wrapped_evidence_client()
    observed = capture(evidence, document)
    assert observed == plain
    assert 'capture_observation' not in observed
    assert json.dumps(observed, sort_keys=True) == json.dumps(plain, sort_keys=True)


def test_the_wrapper_does_not_patch_the_client_class():
    before = FrozenLikeClient._iq_header
    evidence = wrapped_evidence_client()
    assert FrozenLikeClient._iq_header is before
    assert type(evidence.client)._iq_header is before


def test_the_wrapper_returns_the_same_evidence_client_and_is_idempotent():
    evidence = evidence_client()
    inner = evidence.client
    assert F.wrap_header_capture(evidence) is evidence
    first = inner._iq_header
    assert F.wrap_header_capture(evidence) is evidence
    assert inner._iq_header is first, 'a second wrap must not nest another hook'


def test_the_wrapper_targets_the_innermost_client_of_a_wrapper_chain():
    inner = FrozenLikeClient()
    evidence = SimpleNamespace(client=TimingWrapper(inner))
    F.wrap_header_capture(evidence)
    assert getattr(inner, F.HOOK_FLAG) is True
    # the wrapper only proxies attributes to the inner client; the hook lives on the inner
    assert F.HOOK_FLAG not in vars(evidence.client)


def test_the_frozen_validation_still_rejects_bad_headers_after_wrapping():
    evidence = wrapped_evidence_client()
    bad = header()
    bad['direction'] = 'tx'
    with pytest.raises(ValueError, match='direction'):
        capture(evidence, bad)
    assert F.retained_header(evidence) is None, 'a refused header is never retained'


# --------------------------------------------------------------------- success pairing

def test_a_successful_frame_carries_its_own_observation():
    evidence = wrapped_evidence_client()
    capture(evidence, header())
    sidecar = F.frame_observation_sidecar(evidence, frame())
    assert sidecar['schema'] == F.SIDECAR_SCHEMA
    assert sidecar['usable'] is True and sidecar['stop'] is False
    assert sidecar['capture_id'] == 'rx-abc-g0-c1-1'
    observation = sidecar['observation']
    assert observation['in_window_rx_gain_index'] == {
        'available': True, 'value': 76, 'reason': None}
    assert observation['chip_tx_attenuation_mdb'] == {
        'available': True, 'value': [46000, 46000], 'reason': None}


def test_a_daemon_named_failure_is_carried_through_without_a_value():
    evidence = wrapped_evidence_client()
    capture(evidence, header(member='failure'))
    sidecar = F.frame_observation_sidecar(evidence, frame())
    assert sidecar['usable'] is False and sidecar['stop'] is True
    assert sidecar['reasons'] == ['HARDWARE_IO']
    assert sidecar['observation']['in_window_rx_gain_index']['value'] is None


def test_a_frame_without_the_member_stops_rather_than_reports_zero():
    evidence = wrapped_evidence_client()
    capture(evidence, header(member=None))
    sidecar = F.frame_observation_sidecar(evidence, frame())
    assert sidecar['stop'] is True
    assert sidecar['reasons'] == [C.OBSERVATION_MEMBER_MISSING]
    assert sidecar['observation']['in_window_rx_gain_index']['value'] is None


# --------------------------------------------------------------- pairing refuses reuse

def test_a_header_of_another_frame_is_never_reused():
    evidence = wrapped_evidence_client()
    capture(evidence, header(frame_id=7, timestamp_ns=999))
    sidecar = F.frame_observation_sidecar(evidence, frame(request_id=8,
                                                          timestamp_ns=1000))
    assert sidecar['usable'] is False and sidecar['stop'] is True
    assert C.FRAME_IDENTITY_MISMATCH in sidecar['reasons']
    assert sidecar['observation']['in_window_rx_gain_index']['value'] is None


def test_a_matching_id_with_a_different_timestamp_is_refused():
    evidence = wrapped_evidence_client()
    capture(evidence, header(frame_id=3, timestamp_ns=111))
    sidecar = F.frame_observation_sidecar(evidence, frame(request_id=3,
                                                          timestamp_ns=222))
    assert sidecar['stop'] is True
    assert C.FRAME_IDENTITY_MISMATCH in sidecar['reasons']


def test_the_second_capture_replaces_the_retained_header():
    evidence = wrapped_evidence_client()
    capture(evidence, header(frame_id=1, timestamp_ns=10))
    capture(evidence, header(frame_id=2, timestamp_ns=20))
    sidecar = F.frame_observation_sidecar(evidence, frame(request_id=2, timestamp_ns=20))
    assert sidecar['usable'] is True
    stale = F.frame_observation_sidecar(evidence, frame(request_id=1, timestamp_ns=10))
    assert stale['usable'] is False, 'the first frame can no longer borrow the second header'


def test_no_retained_header_is_a_named_stop():
    evidence = evidence_client()                       # never wrapped, never captured
    sidecar = F.frame_observation_sidecar(evidence, frame())
    assert sidecar['stop'] is True
    assert sidecar['reasons'] == [F.FRAME_HEADER_NOT_RETAINED]
    assert sidecar['observation'] is None


def test_a_missing_inner_client_is_a_named_error():
    with pytest.raises(F.ForwardingError) as info:
        F.wrap_header_capture(SimpleNamespace(client=None))
    assert info.value.code == F.NO_CLIENT


# ------------------------------------------------------------------------- sidecar file

def test_the_sidecar_is_written_as_an_additive_file(tmp_path):
    evidence = wrapped_evidence_client()
    capture(evidence, header())
    sidecar = F.frame_observation_sidecar(evidence, frame())
    path = F.write_observation_sidecar(tmp_path, 's00-on-00', sidecar)
    assert path.name == 's00-on-00.capture-observation.json'
    body = json.loads(path.read_text(encoding='utf-8'))
    assert body == sidecar
    # the frozen telemetry document is a DIFFERENT file and is not created here
    assert not (tmp_path / 's00-on-00.telemetry.json').exists()


def test_a_second_write_for_the_same_tag_is_refused(tmp_path):
    evidence = wrapped_evidence_client()
    capture(evidence, header())
    sidecar = F.frame_observation_sidecar(evidence, frame())
    F.write_observation_sidecar(tmp_path, 'tag', sidecar)
    with pytest.raises(FileExistsError):
        F.write_observation_sidecar(tmp_path, 'tag', sidecar)


def test_a_malformed_sidecar_is_refused(tmp_path):
    with pytest.raises(F.ForwardingError):
        F.write_observation_sidecar(tmp_path, 'tag', {'schema': 'something-else'})


# --------------------------------------------------------- no schema / client footprint

def test_the_forwarder_does_not_reference_the_frozen_telemetry_schema_or_session():
    tree = ast.parse(inspect.getsource(F))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert imported & {'socket', 'paramiko', 'pysftp', 'subprocess', 'requests'} == set()
    constants = {node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert 'rf-46db-frame-telemetry-v1' not in constants
    assert 'rf_environment_telemetry_session' not in constants
    assert F.SIDECAR_SCHEMA != 'rf-46db-frame-telemetry-v1'
    source = inspect.getsource(F)
    assert 'rf_environment_telemetry_session' not in source


def test_the_only_client_names_added_are_the_hook_and_the_header():
    evidence = wrapped_evidence_client()
    client = evidence.client
    capture(evidence, header())
    added = {name for name in vars(client) if name.startswith('_capture') or
             name == F.HEADER_ATTR}
    assert added == {F.HOOK_FLAG, F.HEADER_ATTR}


# ------------------------------------------------- against the REAL frozen client class

def _real_client_class():
    """The frozen `RfStatusClient`, imported read-only from the product tree."""
    import sys
    from pathlib import Path
    product = str(Path('pynq_overlay/sw').resolve())
    if product not in sys.path:
        sys.path.insert(0, product)
    try:
        from rf_status_client import RfStatusClient
    except Exception as exc:                                     # pragma: no cover
        pytest.skip('product client is not importable here: %s' % (exc,))
    return RfStatusClient


def test_the_real_frozen_client_keeps_its_exact_result_and_the_header_is_retained():
    client_class = _real_client_class()
    before = client_class._iq_header
    plain = client_class(timeout=2.0)
    try:
        expected_meta = client_class._iq_header(header())
    finally:
        plain.close()
    real = client_class(timeout=2.0)
    evidence = SimpleNamespace(client=real)
    try:
        F.wrap_header_capture(evidence)
        observed_meta = real._iq_header(header())
        assert observed_meta == expected_meta, 'the frozen whitelist result must not change'
        assert 'capture_observation' not in observed_meta
        assert F.retained_header(evidence)['capture_observation']['in_window_rx_gain_index'] == 76
        sidecar = F.frame_observation_sidecar(evidence, frame())
        assert sidecar['usable'] is True
        assert sidecar['observation']['chip_tx_attenuation_mdb']['value'] == [46000, 46000]
    finally:
        real.close()
    assert client_class._iq_header is before, 'the frozen class itself is never patched'


def test_the_real_frozen_client_still_rejects_a_bad_header_after_wrapping():
    client_class = _real_client_class()
    real = client_class(timeout=2.0)
    evidence = SimpleNamespace(client=real)
    try:
        F.wrap_header_capture(evidence)
        bad = header()
        bad['bytes'] = 12
        with pytest.raises(Exception, match='bytes'):
            real._iq_header(bad)
        assert F.retained_header(evidence) is None
    finally:
        real.close()
