"""Same-frame diagnostic identity, expiry, cancellation and historical saving."""
import dataclasses
import hashlib
import sys
import threading
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_environment as ENV
import test_rf_teaching_pipeline as T
import test_rf_rx_pipeline as H
import rf_experiment_record as RECORD


def prepared(tmp_path):
    c, w, a, f = T.prepared(tmp_path)
    a._rf_applied_parameters = dict(ENV.FIXED_RF)
    submitted = a.rx_demod_submission()
    envelope = w._demod_pipeline.analyze_demod(f, submitted)
    source = dict(source_id='normal', session_id='train-session', split='train',
                  iq_sha256=hashlib.sha256(f.payload).hexdigest())
    baseline = ENV.fit_baseline([f], [envelope], dict(ENV.FIXED_RF), [source])
    w._environment_checked = True
    w._environment_module = ENV
    w._environment_baseline = baseline
    w._environment_info = dict(available=True, release_id=H.RELEASE,
        module_sha256={n:'a'*64 for n in ('rf_environment.py',
            'rf_experiment_scenarios.py','rf_environment_baseline.json')},
        baseline_sha256=baseline.sha256, environment_version=ENV.VERSION, reason='')
    a.set_environment_status(w.environment_status())
    return c, w, a, f


def capture(w, a):
    a.rx_capture_submitted()
    assert w.submit_capture({'samples':4096,'ratio':0},
        demod_config=a.rx_demod_submission(),rf_config=a._rx_submitted_rf_config)['queued']
    payload, status = H.drain(w)
    a.set_rx_capture_status(status)
    assert a.set_rx_capture_result(payload)[0]
    return payload


def test_frozen_rf_and_current_result(tmp_path):
    c,w,a,f=prepared(tmp_path)
    try:
        p=capture(w,a)
        assert p['environment_result'].label=='CLEAR'
        assert a.rx_environment_summary()['current']
        assert a.rx_environment_summary()['capture_id']==f.capture_id
        assert a.rx_experiment_request()['environment'] is p['environment_result']
        a.clear_rx_snapshot()
        assert not a.rx_environment_summary()['current']
        assert a.rx_experiment_request(True)['environment'] is p['environment_result']
    finally:w.stop()


@pytest.mark.parametrize('field,value', [('capture_id','old'),('release_id','old'),
    ('connection_generation',7),('config_revision',7),('completed_at_monotonic',0.),
    ('submitted_revision',7),('algorithm_version','old'),('environment_version','old'),
    ('baseline_sha256','b'*64)])
def test_foreign_environment_never_lands_but_base_survives(tmp_path,field,value):
    c,w,a,f=prepared(tmp_path)
    try:
        p=capture(w,a)
        p=dict(p,environment_result=dataclasses.replace(p['environment_result'],**{field:value}))
        assert a.set_rx_capture_result(p)[0]
        assert a.rx_analysis is p['analysis']
        assert not a.rx_environment_summary()['current']
    finally:w.stop()


def test_cancel_during_environment_discards_result(tmp_path,monkeypatch):
    c,w,a,f=prepared(tmp_path);entered=threading.Event();release=threading.Event()
    original=ENV.analyze_environment
    def slow(*args):
        entered.set();assert release.wait(3);return original(*args)
    monkeypatch.setattr(ENV,'analyze_environment',slow)
    try:
        assert w.submit_capture({'samples':4096,'ratio':0},
            demod_config=a.rx_demod_submission(),rf_config=dict(ENV.FIXED_RF))['queued']
        assert entered.wait(2);w.cancel_capture();release.set()
        p,status=H.drain(w)
        assert p is None and status['state']=='cancelled'
    finally:release.set();w.stop()


def test_record_identity_and_roundtrip(tmp_path):
    c,w,a,f=prepared(tmp_path)
    try:
        capture(w,a);args=a.rx_experiment_request()
        receipt=RECORD.write_record(tmp_path,**args)
        record=RECORD.read_record(receipt['path'])
        assert record['metadata']['environment']['label']=='CLEAR'
        assert record['metadata']['environment']['baseline_sha256']==w._environment_baseline.sha256
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
        from scripts.rf_environment_verify import verify_record
        import json
        baseline_path=tmp_path/'baseline.json'
        baseline_path.write_text(json.dumps(w._environment_baseline.to_dict()),encoding='utf-8')
        assert verify_record(receipt['path'],baseline_path)['passed']
        args['environment']=dataclasses.replace(args['environment'],capture_id='foreign')
        with pytest.raises(ValueError):RECORD.write_record(tmp_path,**args)
    finally:w.stop()


def test_expiry_and_stop_clear_current_environment_but_retain_history(tmp_path,monkeypatch):
    c,w,a,f=prepared(tmp_path)
    try:
        p=capture(w,a)
        import ui_model
        monkeypatch.setattr(ui_model.time,'monotonic',lambda:f.completed_at_monotonic+31)
        assert not a.rx_environment_summary()['current']
        assert a.rx_experiment_request(True)['environment'] is p['environment_result']
        a.set_rf_control_pending('stop')
        assert a.rx_environment is None
    finally:w.stop()


def test_environment_exception_preserves_bpsk_without_previous_label(tmp_path,monkeypatch):
    c,w,a,f=prepared(tmp_path)
    try:
        capture(w,a)
        monkeypatch.setattr(ENV,'analyze_environment',lambda *a: (_ for _ in ()).throw(ValueError('bad')))
        p=capture(w,a)
        assert p['demod_result'].result.ber==0 and p['environment_result'] is None
        assert not a.rx_environment_summary()['current']
    finally:w.stop()
