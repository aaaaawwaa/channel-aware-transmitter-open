"""Finite board evidence runner: real UI hits and same-frame raw evidence."""
import dataclasses
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_ui_view as H
import test_rf_rx_view as RX
import test_rf_rx_pipeline as PIPE
import rf_bpsk_pipeline as B
import rf_status_worker as W
import rf_rx_constellation as C
import pynq_status as PANEL

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import rf_bpsk_ui_board_session as S


def test_module_import_does_not_select_board_release_or_load_drivers():
    source = (ROOT / 'scripts/rf_bpsk_ui_board_session.py').read_text(encoding='utf-8')
    assert 'sys.path.insert(0, str(RELEASE))' not in source
    assert 'import rf_status_client as RC' not in source.split('class LiveUiSession:')[0]


def status(**changes):
    value = RX.rx_status(tx_ready=True, release_id=PIPE.RELEASE)
    value.update(changes)
    return value


class Client(PIPE.FakeCaptureClient):
    def __init__(self):
        super().__init__(samples=4096, blocked=False)
        self.current = status()
    def poll(self):
        self.current['received_at_monotonic'] = time.monotonic()
        return SimpleNamespace(to_dict=lambda: dict(self.current))
    def control_disarm(self):
        self.current['armed'] = False
        return {'armed': False}


def prepared(tmp_path):
    client = Client()
    observed = S.EvidenceClient(client)
    worker = W.RfStatusWorker(observed, poll_interval=100)
    worker._demod_checked = True
    worker._demod_pipeline = B
    worker._demod_info = dict(available=True, algorithm_version=B.VERSION,
        algorithm_sha256='a'*64, release_id=PIPE.RELEASE, reason='')
    session = S.LiveUiSession.__new__(S.LiveUiSession)
    session.expected = PIPE.RELEASE
    session.output = tmp_path
    session.client = observed
    session.worker = worker
    session.app = RX.rx_app(status())
    session.app.set_rx_demod_setting('reference_enabled', True)
    session._last_payload = session._last_status = None
    session.ui_events = []
    session._bind_view(H.V, H.ui, PANEL)
    return session, client


def test_off_capture_hits_real_view_and_saves_replayable_same_frame(tmp_path):
    s, c = prepared(tmp_path)
    try:
        record = s.capture('s00-off-10', False)
        assert s.ui_events == ['capture']
        assert c.calls[0][2] is False
        assert record['egress_safe'] is True
        raw = (tmp_path / 's00-off-10.iq').read_bytes()
        meta = json.loads((tmp_path / 's00-off-10.json').read_text())
        assert meta['format'] == 's16_iq_le'
        assert meta['iq_sha256'] == hashlib.sha256(raw).hexdigest()
        assert meta['capture_id'] == record['capture_id']
        assert meta['demod_result']['result']['capture_id'] == meta['capture_id']
        assert meta['analysis']['capture_id'] == meta['capture_id']
        assert meta['display']['capture_id'] == meta['capture_id']
        assert record['result']['ber'] is None
    finally:
        s.worker.stop()


def test_physical_capture_requires_actual_two_hits_and_prompt(tmp_path):
    s, c = prepared(tmp_path)
    try:
        record = s.capture('s00-on-00', True)
        assert s.ui_events == ['loopback', 'loopback_confirm']
        assert c.calls[0][2] is True
        assert record['result']['ui_diagnostic']['stage1_pending_action'] == 'loopback'
        assert (tmp_path / 's00-on-00.iq').exists()
    finally:
        s.worker.stop()


@pytest.mark.parametrize('change', [dict(online=False), dict(stale=True),
    dict(release_id='old'), dict(received_at_monotonic=time.monotonic()-10),
    dict(pl_status={'io_ok': False}),
    dict(pl_status={'io_ok': True, 'rx': {'armed': True},
                    'tx': {'source_selected': False}})])
def test_egress_must_be_fresh_online_expected_release_and_closed(tmp_path, change):
    s, c = prepared(tmp_path)
    try:
        assert s.egress_safe(status(**change)) is False
    finally:
        s.worker.stop()


def test_timeout_requests_cancel_and_retains_failure_receipt(tmp_path, monkeypatch):
    s, c = prepared(tmp_path)
    c.release.clear()
    monkeypatch.setattr(S, 'CAPTURE_DEADLINE', .02)
    try:
        with pytest.raises(TimeoutError):
            s.capture('timeout', False)
        assert s.worker._capture_cancel is True
        saved = json.loads((tmp_path/'timeout.json').read_text())
        assert saved['capture_status']['state'] == 'timeout'
        # D3: the timed-out frame's own document carries the diagnostic the capture path
        # assembled BEFORE the dynamic _save(), not just the worker's last status.
        assert saved['ui_diagnostic']['phase'] == 'capture_timeout'
        assert saved['ui_diagnostic']['frame_present'] is False
        assert saved['ui_diagnostic']['payload_present'] is False
    finally:
        c.release.set()
        s.worker.stop()


def test_cli_refuses_unattested_cable_and_nonempty_output_before_factory(tmp_path, monkeypatch):
    created = []
    monkeypatch.setattr(S, 'LiveUiSession', lambda *a, **k: created.append(a))
    with pytest.raises(SystemExit, match='cable'):
        S.main(['--expected-release', PIPE.RELEASE, '--output', str(tmp_path)])
    (tmp_path/'keep').write_text('old')
    with pytest.raises(SystemExit, match='empty'):
        S.main(['--expected-release', PIPE.RELEASE, '--output', str(tmp_path), '--cable-confirmed'])
    assert not created


def test_unowned_refusal_close_stops_worker_without_disarming():
    calls = []
    s = S.LiveUiSession.__new__(S.LiveUiSession)
    s.worker = SimpleNamespace(stop=lambda: calls.append('stop'))
    s.client = SimpleNamespace(close=lambda: calls.append('close'),
                              control_disarm=lambda: calls.append('disarm'))
    s.close()
    assert calls == ['stop', 'close']


def test_failed_engine_constructor_closes_both_before_any_control(tmp_path, monkeypatch):
    import rf_status_client as RC
    import merged_release as R
    calls = []
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, 'resolve', lambda p, *a, **k: tmp_path if str(p).startswith('/home/xilinx/overlays')
                        else original_resolve(p, *a, **k))
    monkeypatch.setattr(R, 'verify_release', lambda *a, **k: dict(release_id=PIPE.RELEASE))
    monkeypatch.setattr(RC, 'RfStatusClient', lambda **k: SimpleNamespace(close=lambda: calls.append('close')))
    class Worker:
        def __init__(self, *a, **k): pass
        def start(self): calls.append('start')
        def stop(self): calls.append('stop')
        def demod_status(self): return {'available': False}
    monkeypatch.setattr(W, 'RfStatusWorker', Worker)
    steps = iter((0., 31.))
    monkeypatch.setattr(S, 'time', SimpleNamespace(monotonic=lambda: next(steps), sleep=lambda _: None))
    try:
        with pytest.raises(RuntimeError, match='engine not available'):
            S.LiveUiSession(PIPE.RELEASE, tmp_path)
        assert calls == ['start', 'stop', 'close']
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


@pytest.mark.parametrize('ack,expected', [({'armed': False}, True), ({'armed': True}, False),
                                       ({'ok': True}, False), ({}, False)])
def test_cleanup_checks_actual_disarm_response_even_when_readback_safe(tmp_path, ack, expected):
    s, c = prepared(tmp_path)
    def disarm():
        c.current['armed'] = False
        return ack
    c.control_disarm = disarm
    try:
        assert s.cleanup()['disarm_ack'] is expected
    finally:
        s.close()


def test_missing_demod_handoff_persists_evidence_and_stops(tmp_path, monkeypatch):
    s, c = prepared(tmp_path)
    original = s.worker.take_rx_result
    def incomplete():
        payload, status = original()
        if payload is not None:
            payload = dict(payload, demod_result=None)
        return payload, status
    monkeypatch.setattr(s.worker, 'take_rx_result', incomplete)
    try:
        with pytest.raises(RuntimeError, match='incomplete capture'):
            s.capture('missing-demod', False)
        meta = json.loads((tmp_path/'missing-demod.json').read_text())
        assert meta['capture_status']['state'] == 'done'
        assert meta['demod_result'] is None
        assert (tmp_path/'missing-demod.iq').exists()
    finally:
        s.close()


def test_refused_real_view_callback_saves_diagnostic_and_stops(tmp_path, monkeypatch):
    s, c = prepared(tmp_path)
    monkeypatch.setattr(PANEL, '_rx_action', lambda app, worker, event:
                        app.set_rx_capture_status(dict(state='refused', code='NOT_READY')))
    try:
        with pytest.raises(RuntimeError, match='incomplete capture'):
            s.capture('refused', False)
        assert s.ui_events == ['capture'] and not c.calls
        assert (tmp_path/'refused.json').exists()
    finally:
        s.close()


def test_teaching_capture_retains_same_frame_constellation(tmp_path, monkeypatch):
    s, _ = prepared(tmp_path)
    s._teaching = True
    original = s.worker.take_rx_result
    def with_symbols():
        payload, state = original()
        if payload is not None:
            payload = dict(payload, constellation=C.estimate_constellation(
                s.client.frame, payload['demod_result']))
        return payload, state
    monkeypatch.setattr(s.worker, 'take_rx_result', with_symbols)
    try:
        s.capture('teaching-off', False)
        meta = json.loads((tmp_path/'teaching-off.json').read_text())
        assert meta['constellation']['capture_id'] == meta['capture_id']
        assert meta['constellation']['points'] == []
    finally:
        s.close()


def test_teaching_missing_constellation_stops_after_saving(tmp_path,monkeypatch):
    s, _ = prepared(tmp_path)
    s._teaching = True
    original=s.worker.take_rx_result
    def absent():
        payload,status=original()
        return (dict(payload,constellation=None) if payload else payload),status
    monkeypatch.setattr(s.worker,'take_rx_result',absent)
    try:
        with pytest.raises(RuntimeError, match='incomplete capture'):
            s.capture('teaching-incomplete', False)
        assert (tmp_path/'teaching-incomplete.json').exists()
    finally:
        s.close()


def test_teaching_attestation_refuses_old_contract_before_client(tmp_path,monkeypatch):
    import merged_release as R
    monkeypatch.setattr(R,'verify_release',lambda *a,**k:dict(release_id=PIPE.RELEASE,
        software_contract='cnn-bpsk-v1'))
    original_resolve=Path.resolve
    monkeypatch.setattr(Path,'resolve',lambda p,*a,**k:tmp_path if str(p).startswith('/home/xilinx/overlays')
        else original_resolve(p,*a,**k))
    try:
        with pytest.raises(RuntimeError,match='teaching contract'):
            S.LiveUiSession(PIPE.RELEASE,tmp_path,teaching=True)
    finally:
        if str(tmp_path) in sys.path:sys.path.remove(str(tmp_path))
