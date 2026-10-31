"""Only current, matching BPSK snapshots may populate measured UI values."""
import dataclasses
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ui_model as M
from rf_rx_analysis import summarize
from test_rf_bpsk_receiver import frame, signal
from test_rf_bpsk_pipeline import pipeline

VERSION = 'bpsk-rect-snapshot-v2'
HASH = '23b9ab9fdc1cc7647c2c40169ae73a23504c262d2270d82ebf8ea65349e16760'


def app():
    a = M.UiApp()
    assert hasattr(a, 'set_rx_demod_setting'), 'explicit RX demod setter is not implemented'
    a.set_rf_status(dict(online=True, stale=False, mode='status_only', armed=True,
                         daemon_state='READY', release_id='release',
                         control_ops=['rx.capture'], rx_session_ready=True))
    a.set_rx_demod_status(dict(available=True, algorithm_version=VERSION,
                              algorithm_sha256=HASH, release_id='release', reason=''))
    return a


def payload(a):
    P = pipeline()
    f = dataclasses.replace(frame(signal()), completed_at_monotonic=time.monotonic())
    submitted = a.rx_demod_submission()
    a.rx_capture_submitted()
    return dict(frame=f.to_dict(), analysis=summarize(f),
                demod_result=P.analyze_demod(f, submitted))


def land(a):
    assert a.set_rx_demod_setting('reference_enabled', True) is None
    data = payload(a)
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_demod_summary()['ber'] == 0
    return data


def test_default_explicit_configuration_and_capability_are_not_measurements():
    a = app()
    assert a.rx_draft['expected_mode'] == 'BPSK'
    assert a.rx_draft['symrate_hz'] == 3000000 and a.rx_draft['shaping'] == 'RECT'
    assert a.rx_draft['reference_enabled'] is False
    assert a.capabilities.sync and a.capabilities.demod and a.capabilities.ber
    summary = a.rx_demod_summary()
    assert summary['available'] and not summary['current'] and summary['ber'] is None
    assert a.rx_demod_submission().config.reference_prbs7_seed is None


@pytest.mark.parametrize('key,value', [('expected_mode','QPSK'),('symrate_hz',2000000),
    ('shaping','RRC'),('reference_enabled',False),('reference_seed',37),('decim',2),
    ('rx_len',2048)])
def test_each_configuration_change_advances_separate_revision_and_invalidates(key, value):
    a = app(); land(a)
    hardware = a._rx_config_revision
    revision = a.rx_demod_revision
    assert a.set_rx_demod_setting(key, value) is None
    assert a.rx_demod_revision == revision+1 and a._rx_config_revision == hardware
    assert a.rx_demod_result is None and a.rx_demod_summary()['ber'] is None


@pytest.mark.parametrize('field,value', [('capture_id','other'),('release_id','old'),
    ('connection_generation',100),('config_revision',100),('completed_at_monotonic',0.)])
def test_mismatched_result_identity_preserves_base_analysis_and_refuses_ber(field, value):
    a = app(); a.set_rx_demod_setting('reference_enabled', True)
    data = payload(a)
    envelope = data['demod_result']
    data['demod_result'] = dataclasses.replace(envelope,
        result=dataclasses.replace(envelope.result, **{field: value}))
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_analysis is data['analysis'] and a.rx_demod_result is None
    assert a.rx_demod_summary()['ber'] is None


def test_old_submission_is_rejected_after_edit_and_restore_same_values():
    a = app(); a.set_rx_demod_setting('reference_enabled', True)
    data = payload(a)
    a.set_rx_demod_setting('reference_seed',37)
    a.set_rx_demod_setting('reference_seed',1)
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_demod_result is None


def test_new_capture_clears_previous_ber_and_reference_off_has_no_ber():
    a = app(); land(a)
    a.rx_capture_submitted()
    assert a.rx_demod_result is None
    a.set_rx_demod_setting('reference_enabled',False)
    assert a.set_rx_capture_result(payload(a))[0]
    summary = a.rx_demod_summary()
    assert summary['carrier_sync'] and summary['timing_sync']
    assert summary['reference_sync'] is None and summary['ber'] is None
    assert summary['reason'] == 'REFERENCE_NOT_PROVIDED' and summary['polarity_ambiguous']


def test_expiry_clears_current_values(monkeypatch):
    a = app(); data = land(a)
    monkeypatch.setattr(M.time, 'monotonic', lambda: data['frame']['completed_at_monotonic']+31)
    summary = a.rx_demod_summary()
    assert not summary['current'] and summary['ber'] is None
    assert a.rx_demod_result is None and summary['reason'] == 'EXPIRED'


@pytest.mark.parametrize('event', ['clear','cancel','offline','device','failed_capture'])
def test_lifecycle_invalidation_leaves_no_old_ber(event):
    a = app(); land(a)
    if event == 'clear': a.clear_rx_snapshot()
    elif event == 'cancel': a.rx_capture_cancel()
    elif event == 'offline': a.set_rf_status({'online':False})
    elif event == 'device': a.select_device('AD9361')
    elif event == 'failed_capture': a.set_rx_capture_status({'state':M.RX_CAPTURE_ERROR, 'seq':20})
    assert a.rx_demod_result is None and a.rx_demod_summary()['ber'] is None


def test_invalid_setting_refused_without_mutation_and_status_fail_closed():
    a = app()
    for key,value in [('reference_enabled',1),('reference_seed',True),('reference_seed',0),
                      ('rx_len',True),('symrate_hz',True),('shaping','bad'),('unknown',1)]:
        before = dict(a.rx_draft), a.rx_demod_revision
        assert a.set_rx_demod_setting(key,value) is not None
        assert (a.rx_draft,a.rx_demod_revision) == before
    a.set_rx_demod_status(dict(available=True,algorithm_version=VERSION,
                              algorithm_sha256='bad',release_id='release'))
    assert not a.capabilities.demod and a.rx_demod_submission() is None


def test_current_submission_values_must_match_not_only_revision():
    a = app(); a.set_rx_demod_setting('reference_enabled', True)
    data = payload(a)
    envelope = data['demod_result']
    wrong = dataclasses.replace(envelope.submitted,
            config=dataclasses.replace(envelope.submitted.config, reference_prbs7_seed=37))
    data['demod_result'] = dataclasses.replace(envelope, submitted=wrong)
    assert a.set_rx_capture_result(data)[0] and a.rx_demod_result is None


@pytest.mark.parametrize('key,value', [('samples',2048),('ratio',1),('sample_rate_hz',30000000)])
def test_frame_acquisition_must_match_frozen_submission(key,value):
    a = app(); a.set_rx_demod_setting('reference_enabled', True)
    data = payload(a)
    data['frame'] = dict(data['frame'], **{key:value})
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_demod_result is None and a.rx_demod_summary()['ber'] is None


def test_analysis_and_demod_completion_moment_must_match():
    a = app(); a.set_rx_demod_setting('reference_enabled', True)
    data = payload(a)
    data['analysis'] = dataclasses.replace(data['analysis'],
        completed_at_monotonic=data['frame']['completed_at_monotonic']-1)
    assert a.set_rx_capture_result(data)[0]
    assert a.rx_demod_result is None


@pytest.mark.parametrize('action', ['stop','disarm','init','arm','configure'])
def test_successful_control_actions_clear_demod_even_before_next_status_poll(action):
    a = app(); land(a)
    a.set_rf_control_result(action, {'ok':True,'args':{}})
    assert a.rx_demod_result is None and a.rx_demod_summary()['ber'] is None


def test_cnn_fill_uses_setter_without_adopting_reference_or_changing_symbol_rate():
    a = app(); land(a)
    revision = a.rx_demod_revision
    a.rx_fill_block = lambda: None
    a.recommendation = M.Recommendation(action={'expected_mode':'QPSK', 'symrate_hz':5000000,
                                               'reference_seed':37,'reference_enabled':True})
    assert a.rx_fill_draft() is None
    assert a.rx_draft['expected_mode'] == 'QPSK' and a.rx_demod_revision == revision+1
    assert a.rx_draft['symrate_hz'] == 3000000 and a.rx_draft['reference_seed'] == 1
    assert a.rx_demod_result is None


@pytest.mark.parametrize('action', ['stop','disarm'])
@pytest.mark.parametrize('phase', ['pending','failed'])
def test_stop_and_disarm_invalidate_before_confirmed_shutdown(action,phase):
    a = app(); land(a)
    if phase == 'pending': a.set_rf_control_pending(action)
    else: a.set_rf_control_result(action, {'ok':False,'code':'UNCONFIRMED','error':'close unconfirmed'})
    assert a.rx_demod_result is None and a.rx_demod_summary()['ber'] is None
