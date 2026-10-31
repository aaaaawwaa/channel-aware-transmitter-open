"""Same-frame teaching handoff and asynchronous records never delay STOP."""
import dataclasses
import sys
import threading
import time
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import rf_bpsk_pipeline as B
import rf_rx_constellation as C
import rf_experiment_record as E
import test_rf_bpsk_worker as BW
import test_rf_rx_pipeline as H
import test_rf_rx_view as RX
import test_ui_view as VIEW
from test_rf_bpsk_receiver import frame, signal
import pynq_status as PANEL


def prepared(tmp_path):
    c,w,s=BW.prepared()
    w._teaching_checked=True
    w._constellation_module=C;w._record_module=E
    w._experiment_root=tmp_path
    w._teaching_info=dict(available=True,release_id=H.RELEASE,
        module_sha256={n:'a'*64 for n in ('rf_teaching_profile.py','rf_rx_constellation.py',
                                       'rf_experiment_record.py')},reason='')
    raw=dataclasses.replace(frame(signal(noise=10)),release_id=H.RELEASE,
        connection_generation=1,config_revision=0,completed_at_monotonic=time.monotonic())
    c.capture=lambda args,physical=False: raw
    a=RX.rx_app(RX.rx_status(release_id=H.RELEASE,tx_ready=True))
    assert hasattr(w,'teaching_status'), 'teaching handoff is not implemented'
    a.set_teaching_status(w.teaching_status())
    a.set_rx_demod_status(w.demod_status())
    a.set_rx_demod_setting('reference_enabled',True)
    return c,w,a,raw


def capture(w,a):
    a.rx_capture_submitted()
    assert w.submit_capture({'samples':4096,'ratio':0},demod_config=a.rx_demod_submission())['queued']
    payload,status=H.drain(w)
    a.set_rx_capture_status(status)
    assert a.set_rx_capture_result(payload)[0]
    return payload


def test_worker_handoff_retains_exact_raw_frame_and_real_symbols(tmp_path):
    c,w,a,f=prepared(tmp_path)
    try:
        p=capture(w,a)
        assert p['raw_frame'] is f and p['constellation'].capture_id==f.capture_id
        assert a.rx_raw_frame is f
        assert a.rx_constellation_summary()['current']
        assert len(a.rx_constellation.points)==128
        assert len(set(a.rx_constellation.points))>2
    finally:w.stop()


@pytest.mark.parametrize('field,value',[('capture_id','old'),('release_id','old'),
    ('config_revision',20),('connection_generation',20),('completed_at_monotonic',0.),
    ('submitted_revision',20),('algorithm_version','old')])
def test_mismatched_constellation_preserves_iq_but_never_draws_old_points(tmp_path,field,value):
    c,w,a,f=prepared(tmp_path)
    try:
        p=capture(w,a)
        p=dict(p,constellation=dataclasses.replace(p['constellation'],**{field:value}))
        assert a.set_rx_capture_result(p)[0]
        assert a.rx_analysis is p['analysis'] and a.rx_constellation is None
        assert a.rx_constellation_summary()['point_count']==0
    finally:w.stop()


def test_cancel_during_constellation_compute_discards_every_handoff(tmp_path,monkeypatch):
    c,w,a,f=prepared(tmp_path);entered,release=threading.Event(),threading.Event()
    original=C.estimate_constellation
    def slow(*args):
        entered.set();assert release.wait(3);return original(*args)
    monkeypatch.setattr(C,'estimate_constellation',slow)
    try:
        assert w.submit_capture({'samples':4096,'ratio':0},demod_config=a.rx_demod_submission())['queued']
        assert entered.wait(2);w.cancel_capture();release.set()
        payload,status=H.drain(w)
        assert payload is None and status['state']=='cancelled'
    finally:release.set();w.stop()


def test_exception_preserves_base_demod_but_clears_constellation(tmp_path,monkeypatch):
    c,w,a,f=prepared(tmp_path)
    monkeypatch.setattr(C,'estimate_constellation',lambda *args: (_ for _ in ()).throw(ValueError('bad')))
    try:
        p=capture(w,a)
        assert p['demod_result'].result.ber==0 and p['analysis'] is not None
        assert p['constellation'] is None and a.rx_constellation is None
    finally:w.stop()


def test_save_copies_current_frame_before_later_capture_and_stop_does_not_wait(tmp_path,monkeypatch):
    c,w,a,f=prepared(tmp_path);entered,release=threading.Event(),threading.Event();saved=[]
    def slow(root,raw,envelope,constellation,**kwargs):
        entered.set();assert release.wait(3);saved.append((raw,kwargs));return {'record_id':'saved'}
    monkeypatch.setattr(E,'write_record',slow)
    c.control_op=lambda *args: None
    try:
        capture(w,a);request=a.rx_experiment_request()
        started=time.monotonic()
        assert w.submit_experiment_save(**request)['queued']
        assert time.monotonic()-started<.1 and entered.wait(1)
        a.clear_rx_snapshot()
        assert w.submit_control('stop')['queued']
        deadline=time.monotonic()+1
        while w.control_status() is not None and time.monotonic()<deadline:time.sleep(.01)
        assert w.control_status() is None and not release.is_set()
        release.set();w._save_thread.join(2)
        assert saved[0][0] is f
        result=w.take_experiment_save_result()
        assert result['state']=='saved' and result['capture_id']==f.capture_id
    finally:release.set();w.stop()


def test_expiry_clears_current_points_and_historical_save_is_explicit(tmp_path,monkeypatch):
    c,w,a,f=prepared(tmp_path)
    try:
        capture(w,a)
        monkeypatch.setattr(a,'rx_snapshot_is_current',lambda *args:False)
        assert not a.rx_constellation_summary()['current'] and a.rx_constellation is None
        assert a.rx_experiment_request() is None
        request=a.rx_experiment_request(historical=True)
        assert request['frame'] is f and request['historical'] is True
    finally:w.stop()


def test_actual_save_and_stop_hits_leave_loopback_controls_reachable(tmp_path):
    c,w,a,f=prepared(tmp_path)
    try:
        capture(w,a);a.page=VIEW.M.PAGE_RX;a.rx_subpage='constellation'
        v=VIEW.V.UiView(VIEW.FakeLcd(),a,on_rx=lambda event:PANEL._rx_action(a,w,event))
        v.render()
        assert v.rx_constellation_plot.visible and v.rx_constellation_plot.series
        assert v.rx_loopback_btn.visible and v.rf_stop_btn.visible
        VIEW.tap_at(v,*VIEW.center(v.rx_save_btn))
        w._save_thread.join(3)
        assert w.take_experiment_save_result()['state']=='saved'
        assert v.rx_capture_btn.visible
    finally:w.stop()


@pytest.mark.parametrize('change',['configure','disconnect','device'])
def test_history_retains_capture_rf_config_after_later_change(tmp_path,change):
    c,w,a,f=prepared(tmp_path)
    before={'lo_hz':80000000,'sample_rate_hz':60000000,'attenuation_mdB':40000}
    a._rf_applied_parameters=dict(before)
    try:
        capture(w,a)
        if change=='configure':
            a.set_rf_control_result('configure',dict(ok=True,args=dict(before,lo_hz=90000000)))
        elif change=='disconnect':
            a.set_rf_status(dict(online=False,stale=True))
        else:a.select_device('ACM2108')
        request=a.rx_experiment_request(historical=True)
        assert request is not None
        assert request['rf_config']==before
        assert not a.rx_constellation_summary()['current']
    finally:w.stop()
