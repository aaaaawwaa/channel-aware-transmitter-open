"""Same-frame BPSK handoff stays off input and yields to cancellation."""
import inspect
import sys
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import rf_status_worker as W
import test_rf_rx_pipeline as H


def test_capture_signature_retains_optional_demod_and_status():
    parameters=inspect.signature(W.RfStatusWorker.submit_capture).parameters
    assert 'demod_config' in parameters and 'demod_revision' in parameters
    assert hasattr(W.RfStatusWorker,'demod_status')


def prepared():
    import rf_bpsk_pipeline as P
    c=H.FakeCaptureClient(blocked=False,samples=4096)
    w=W.RfStatusWorker(c,poll_interval=100)
    w._demod_checked=True;w._demod_pipeline=P
    w._demod_info=dict(available=True,algorithm_version='bpsk-rect-snapshot-v2',
        algorithm_sha256='a'*64,release_id=H.RELEASE,reason='ready')
    submitted=P.freeze_demod_config(dict(expected_mode='BPSK',symrate_hz=3000000,
        shaping='RECT',reference_enabled=False,reference_seed=1,decim=1,rx_len=4096,
        input_fs_hz=60000000),7)
    return c,w,submitted


def test_config_never_enters_wire_and_handoff_bound_to_this_frame():
    c,w,s=prepared()
    assert w.submit_capture(dict(samples=4096,ratio=0),demod_config=s,demod_revision=7)['queued']
    payload,status=H.drain(w)
    assert status['state']==W.RX_CAPTURE_DONE and payload['analysis'] is not None
    assert c.calls[0][1]==dict(samples=4096,ratio=0)
    assert payload['demod_result'].result.capture_id==payload['frame']['capture_id']
    assert payload['demod_result'].submitted==s


def test_cancel_while_bpsk_runs_wins_and_verifies_close(monkeypatch):
    c,w,s=prepared()
    entered,release=threading.Event(),threading.Event()
    original=w._demod_pipeline.analyze_demod
    def slow(frame,submitted):
        entered.set();assert release.wait(5)
        return original(frame,submitted)
    monkeypatch.setattr(w._demod_pipeline,'analyze_demod',slow)
    assert w.submit_capture(dict(samples=4096,ratio=0),demod_config=s)['queued']
    assert entered.wait(5)
    w.cancel_capture();release.set()
    payload,status=H.drain(w)
    assert payload is None and status['state']==W.RX_CAPTURE_CANCELLED
    assert status['close_confirmed'] is True


def test_algorithm_failure_preserves_current_base_analysis(monkeypatch):
    c,w,s=prepared()
    def broken(frame,submitted):raise RuntimeError('controlled failure')
    monkeypatch.setattr(w._demod_pipeline,'analyze_demod',broken)
    assert w.submit_capture(dict(samples=4096,ratio=0),demod_config=s)['queued']
    payload,status=H.drain(w)
    assert status['state']==W.RX_CAPTURE_DONE and payload['analysis'] is not None
    assert payload['demod_result'].result.reason=='DEMOD_FAILED'
    assert payload['demod_result'].result.ber is None


def test_legacy_call_does_not_compute_demod(monkeypatch):
    c,w,s=prepared()
    monkeypatch.setattr(w._demod_pipeline,'analyze_demod',lambda *_: (_ for _ in ()).throw(AssertionError()))
    w.submit_capture(dict(samples=4096,ratio=0))
    payload,status=H.drain(w)
    assert payload['demod_result'] is None and status['state']==W.RX_CAPTURE_DONE


def test_connection_changes_during_bpsk_discard_full_frame(monkeypatch):
    c,w,s=prepared()
    original=w._demod_pipeline.analyze_demod
    def changed(frame,submitted):
        c._connection_generation=frame.connection_generation+1
        return original(frame,submitted)
    monkeypatch.setattr(w._demod_pipeline,'analyze_demod',changed)
    w.submit_capture(dict(samples=4096,ratio=0),demod_config=s)
    payload,status=H.drain(w)
    assert payload is None and status['state']==W.RX_CAPTURE_CANCELLED
