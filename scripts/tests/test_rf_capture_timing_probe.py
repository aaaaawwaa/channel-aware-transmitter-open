"""Finite timing evidence preserves failed frames and never retries a session."""
import hashlib
import copy
import shutil
import sys
from types import SimpleNamespace
import json
from pathlib import Path

import numpy as np
import pytest
from scripts import rf_capture_timing_probe as P


def downloaded_case(fixture,tag,number,kind,board_root='/home/xilinx/rf-capture',
                    iq_seed=None,settle_wait_us=None):
    """A frame the way the HOST holds it after a download.

    The frame files sit directly in the evidence directory and the saved record
    sits under `records/<record_id>/`, while the receipt inside the sidecar still
    names the BOARD's absolute path -- which does not exist on the host.  That is
    the shape a real download produces, and it is what makes the records root
    load-bearing: a driver that forgets to pass it falls back to the board path
    and fails, instead of silently finding the record through a host path that
    only a hand-built fixture would have."""
    row=saved_case(fixture,tag,number,kind,iq_seed=iq_seed,settle_wait_us=settle_wait_us)
    moved=fixture/'records'/('record-'+tag)
    moved.parent.mkdir(parents=True,exist_ok=True)
    shutil.move(str(fixture/('record-'+tag)),str(moved))
    side_path=fixture/(tag+'.environment.json')
    side=json.loads(side_path.read_text(encoding='utf-8'))
    side['record']['path']=board_root+'/records/record-'+tag
    side_path.write_text(json.dumps(side),encoding='utf-8')
    return row


class StateSftp:
    """The SFTP stub every driver test needs: `final-state.json` is the ONE
    download whose bytes matter, so it returns a real state document for that
    path and an inert '{}' for everything else."""
    def __init__(self,state):self.state=state
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def mkdir(self,*args):pass
    def put(self,*args):pass
    def get(self,remote,local):
        open(local,'w').write(json.dumps(self.state) if remote.endswith('final-state.json') else '{}')


def safe_state_document():
    """A state document `verified_state` accepts: release verified, part at rest,
    both services up.  Built here rather than mocked so the driver's own safety
    gate is what accepts it."""
    return dict(release_verified=True,release_id='release',safe=True,
        active_release='/home/xilinx/overlays/awg_merged/releases/release',
        status=dict(release_id='release',online=True,stale=False,armed=False,
            pl_status=dict(io_ok=True,rx=dict(armed=False),tx=dict(source_selected=False))),
        services={name:dict(ActiveState='active',SubState='running',MainPID='6200')
                  for name in ('pynq-status','rf-daemon')})


def frozen_baseline_document(spectrum_value=0.0):
    """A schema-VALID frozen baseline, built through the SAME class the replay
    parses it with.

    A hand-written dict would not survive `from_dict` -- it demands an exact field
    set and a matching canonical SHA -- so the fixture would stop modelling what a
    real baseline file is and a gate that accepted it would be accepting something
    that cannot exist.  `spectrum_value` is the one knob, for producing a
    DIFFERENT-but-still-valid baseline when a test needs a mismatch."""
    root=Path(__file__).resolve().parents[2]
    if str(root/'pynq_overlay/sw') not in sys.path:
        sys.path.insert(0,str(root/'pynq_overlay/sw'))
    import rf_environment as E
    thresholds=tuple(sorted(dict(
        tone_concentration=0.5,tone_excess=0.5,tone_weak_excess=1.0,pulse_crest=3.0,
        pulse_outlier=3.0,minimum_rms_codes=8.0,pulse_max_fraction=0.05,
        pulse_min_samples=16,pulse_after_tone_min_samples=16,
        pulse_after_tone_min_density=0.05,pulse_plateau_outlier=3.0,
        pulse_plateau_min_samples=16,pulse_plateau_min_density=0.05).items()))
    sources=(tuple(sorted(dict(iq_sha256='a'*64,session_id='session-0',
        source_id='train-0',split='train').items())),)
    return E.EnvironmentBaseline(tuple(spectrum_value for _ in range(4096)),thresholds,
        sources,tuple(sorted(dict(E.FIXED_RF).items())),'frozen').to_dict()


def saved_case(output,tag,number,kind,clipped=False,settle_wait_us=None,iq_seed=None,
               actual_wait_us=None):
    """`settle_wait_us=None` models the minor-4 daemon, which cannot report a wait
    at all.  An int models the minor-5 daemon, which always reports the field --
    including a zero wait, because that is the 0 us level of the comparison.

    `iq_seed` decouples the SAMPLES from `number` (the connection generation).
    Two frames of one session share a generation but never share an IQ, and a
    fixture that tied them together would model two identical captures -- which
    the real drivers reject as a duplicate.

    `actual_wait_us` decouples the wait the TRACE reports from the wait that was
    REQUESTED.  A scheduler can overshoot a sleep, so the two are genuinely
    different quantities; a fixture that always made them equal could not model
    the overshoot the deadline exists for."""
    values=np.zeros((4096,2),dtype='<i2');values[:,0]=100+(number if iq_seed is None else iq_seed)
    if clipped:values[9,1]=-2048
    raw=values.tobytes();digest=hashlib.sha256(raw).hexdigest()
    daemon,minor=('1.0.2',4) if settle_wait_us is None else ('1.0.3',5)
    # Nanosecond times on a REALISTIC scale: a 1000 us wait has to be able to fit
    # inside the TX-on..RX-arm gap, or the fixture would be modelling a wait five
    # orders of magnitude shorter than the request and every level would come out
    # unqualified.  The earlier fixture spanned 10 ns across the whole transaction.
    if actual_wait_us is not None:
        span=actual_wait_us*1000
    else:
        span=0 if not settle_wait_us else settle_wait_us*1000
    us=1000000
    base=100000000
    offsets=(0,us,us+span,2*us+span,3*us+span,4*us+span,5*us+span,6*us+span)
    times={k:base+v for k,v in zip(P.TIME_FIELDS,offsets)}
    meta=dict(capture_id=tag,connection_generation=number,config_revision=2,
        request_id=7,daemon_timestamp_ns=base+7*us+span,samples=4096,bytes=16384,
        sample_rate_hz=60000000,ratio=0,decimation=1,output_fs_hz=60000000,
        release_id='release',physical=True,op='loopback.physical.capture',pre_io_ok=True,
        post_io_ok=True,post_rx_armed=False,post_tx_source_selected=False,iq_sha256=digest,
        handoff_frame_matches=True,capture_status=dict(state='done',close_confirmed=True,cancelled=False))
    meta['handoff_frame']={k:v for k,v in meta.items() if k not in ('capture_status','handoff_frame_matches')}
    env=dict(release_id='release',capture_id=tag,label='INVALID' if clipped else 'CLEAR',reason='CLIPPED' if clipped else 'NORMAL')
    side=dict(iq_sha256=digest,capture_id=tag,release_id='release',environment=env,
        rf_config=dict(P.FIXED_RF),scenario_declaration=dict(truth=kind,prbs7_seed=1,amplitude=60))
    # A REAL saved-record folder, so the acceptance can re-verify it with the
    # production reader instead of trusting the probe's own summary.  Truncate,
    # edit or delete any of these three files and the validation must fail.
    rec_dir=output/('record-'+tag);rec_dir.mkdir(parents=True,exist_ok=True)
    meta_bytes=json.dumps(dict(iq_sha256=digest,bytes=len(raw),capture_id=tag,
        format='s16_iq_le',environment_label=env['label'])).encode('utf-8')
    (rec_dir/'frame.iq').write_bytes(raw)
    (rec_dir/'metadata.json').write_bytes(meta_bytes)
    receipt=dict(schema_version=1,record_id='record-'+tag,capture_id=tag,iq_sha256=digest,
        metadata_sha256=hashlib.sha256(meta_bytes).hexdigest(),bytes=len(raw),
        path=str(rec_dir.resolve()),historical=False)
    (rec_dir/'receipt.json').write_bytes(json.dumps(receipt).encode('utf-8'))
    side['record']=receipt
    side['display_environment']=dict(available=True,current=True,capture_id=tag,
        label=env['label'],reason=env['reason'],features={},baseline_sha256=None)
    trace=dict(available=True,scope=P.TIMING_SCOPE,capture_request_id=7,
        capture_timestamp_ns=meta['daemon_timestamp_ns'],samples=4096,ratio=0,**times)
    if settle_wait_us is not None:
        trace['settle_wait_requested_us']=settle_wait_us
        if settle_wait_us:
            # Exactly the span that was asked for, sitting where the daemon takes it.
            trace['settle_begin_ns']=times['tx_on_end_ns']
            trace['settle_end_ns']=times['rx_arm_begin_ns']
    gain=dict(release_id='release',daemon=daemon,protocol_major=1,protocol_minor=minor,
        gain_observation=dict(available=True,source='hardware',scope='sequential_reads_not_atomic',
            started_at_monotonic_ns=1,finished_at_monotonic_ns=2,rx_enable_raw=0x40,
            agc_config1_raw=2,agc_config2_raw=8,mode_rx1=2,gain_index_before=76,
            gain_index_after=76,digital_gain_code=0,index_stable=True),reason=None)
    after=copy.deepcopy(gain);after['gain_observation'].update(started_at_monotonic_ns=meta['daemon_timestamp_ns']+1,
        finished_at_monotonic_ns=meta['daemon_timestamp_ns']+2)
    diag=dict(frame={k:meta[k] for k in P.IDENTITY},iq_sha256=digest,
        before_gain=gain,after_gain=after,timing=dict(release_id='release',daemon=daemon,
            protocol_major=1,protocol_minor=minor,last_capture_timing=trace,reason=None),
        statistics=P.window_statistics(raw),rf_config=side['rf_config'],scope=P.TIMING_SCOPE)
    if settle_wait_us is not None:
        diag['settle_wait_requested_us']=settle_wait_us
    (output/(tag+'.iq')).write_bytes(raw)
    for name,value in ((tag+'.json',meta),(tag+'.environment.json',side),(tag+'.timing.json',diag)):
        (output/name).write_text(json.dumps(value),encoding='utf-8')
    row=dict(capture_id=tag,release_id='release',iq_sha256=digest,iq_file=tag+'.iq',
        metadata_file=tag+'.json',environment=env,environment_file=tag+'.environment.json',
        timing_file=tag+'.timing.json',egress_safe=True)
    if settle_wait_us is not None:
        row['settle_wait_requested_us']=settle_wait_us
    return row


class Session:
    def __init__(self,output,number,clipped=False,safe=True,settle_wait_us=None,
                 actual_wait_us=None):
        self.output=output;self.number=number;self.clipped=clipped;self.safe=safe;self.calls=[];self.kind=None
        self.settle_wait_us=settle_wait_us
        self.actual_wait_us=actual_wait_us
        self.waits=[]
    def initial(self):return dict(safe=self.safe,release_id='release')
    def setup(self,seed):self.calls.append(('setup',seed))
    def scene(self,kind):self.kind=kind;self.calls.append(('scene',kind))
    def capture(self,tag,physical,settle_wait_us=None):
        wait=self.settle_wait_us if settle_wait_us is None else settle_wait_us
        self.calls.append(('capture',tag,physical));self.waits.append((tag,wait));self.number+=1
        return saved_case(self.output,tag,self.number,self.kind,self.clipped,wait,
            actual_wait_us=self.actual_wait_us)
    def cleanup(self):self.calls.append('cleanup');return dict(safe=True,disarm_ack=True)
    def close(self):self.calls.append('close')


def test_exact_two_sessions_four_frames_close(tmp_path):
    sessions=[]
    def factory():
        s=Session(tmp_path,len(sessions)*10);sessions.append(s);return s
    result=P.campaign(factory,tmp_path,'release')
    assert result['passed'] and len(result['records'])==4 and len(result['sessions'])==2
    assert [s.calls[0] for s in sessions]==[('setup',1),('setup',37)]
    assert all(s.calls[-2:]==['cleanup','close'] for s in sessions)
    assert result['formal_physical']=='NOT_RUN' and not result['s2_stage_passed']


def test_clipped_first_frame_is_retained_without_retry(tmp_path):
    sessions=[]
    def factory():
        s=Session(tmp_path,1,clipped=True);sessions.append(s);return s
    result=P.campaign(factory,tmp_path,'release')
    assert not result['passed'] and result['stop_cause']=='CLIPPED'
    assert len(sessions)==1 and len(result['records'])==1 and len(result['attempts'])==1
    assert (tmp_path/'s00-normal-00.iq').exists() and sessions[0].calls[-2:]==['cleanup','close']


def test_initial_unsafe_is_unowned_and_not_disarmed(tmp_path):
    session=Session(tmp_path,1,safe=False)
    result=P.campaign(lambda:session,tmp_path,'release')
    assert not result['passed'] and not result['sessions'][0]['owned']
    assert session.calls==['close']


@pytest.mark.parametrize('change',['trace_identity','backward_clock','iq','gain_source','rf','handoff','statistics',
    'boolean_ratio','before_gain_late','after_gain_early','timestamp_overflow','before_gain_zero_clock'])
def test_cross_artifact_tampering_rejected(tmp_path,change):
    row=saved_case(tmp_path,'case',1,'normal')
    path=tmp_path/'case.timing.json';diag=json.loads(path.read_text(encoding='utf-8'))
    if change=='trace_identity':diag['timing']['last_capture_timing']['capture_request_id']=8
    elif change=='backward_clock':diag['timing']['last_capture_timing']['rx_arm_begin_ns']=1
    elif change=='gain_source':diag['before_gain']['gain_observation']['source']='cached'
    elif change=='statistics':diag['statistics']['rail_components']=99
    elif change=='boolean_ratio':diag['timing']['last_capture_timing']['ratio']=False
    elif change=='before_gain_late':
        # Derived from the trace rather than a literal, so the tamper keeps meaning
        # "the idle gain finished AFTER the capture began" whatever scale the
        # fixture uses.
        diag['before_gain']['gain_observation']['finished_at_monotonic_ns']=\
            diag['timing']['last_capture_timing']['tx_on_begin_ns']+1
    elif change=='before_gain_zero_clock':diag['before_gain']['gain_observation'].update(started_at_monotonic_ns=0,finished_at_monotonic_ns=0)
    elif change=='after_gain_early':diag['after_gain']['gain_observation']['started_at_monotonic_ns']=1000
    elif change=='timestamp_overflow':
        meta_path=tmp_path/'case.json';meta=json.loads(meta_path.read_text(encoding='utf-8'))
        meta['daemon_timestamp_ns']=meta['handoff_frame']['daemon_timestamp_ns']=2**65
        meta_path.write_text(json.dumps(meta),encoding='utf-8')
        diag['frame']['daemon_timestamp_ns']=diag['timing']['last_capture_timing']['capture_timestamp_ns']=2**65
    elif change=='iq':(tmp_path/'case.iq').write_bytes(b'\0'*16384)
    elif change=='rf':
        path=tmp_path/'case.environment.json';diag=json.loads(path.read_text(encoding='utf-8'));diag['rf_config']['lo_hz']=90000000
    elif change=='handoff':
        path=tmp_path/'case.json';diag=json.loads(path.read_text(encoding='utf-8'));diag['handoff_frame']['request_id']=8
    path.write_text(json.dumps(diag),encoding='utf-8')
    with pytest.raises(ValueError):P.validate_diagnostic(row,tmp_path,'release','normal')


def test_existing_result_is_not_overwritten(tmp_path):
    (tmp_path/'result.json').write_text('failed original')
    with pytest.raises(FileExistsError):P.campaign(lambda:pytest.fail('no session'),tmp_path,'release')


def test_capture_and_cleanup_errors_are_both_retained(tmp_path):
    class Failed(Session):
        def capture(self,*args):raise RuntimeError('original capture failure')
        def cleanup(self):raise OSError('cleanup failure')
    result=P.campaign(lambda:Failed(tmp_path,1),tmp_path,'release')
    assert not result['passed'] and len(result['attempts'])==1
    assert result['sessions'][0]['error']=='original capture failure'
    assert result['sessions'][0]['cleanup_error']=='cleanup failure'
    assert (tmp_path/'result.json').exists()


def test_after_gain_failure_retains_successful_trace_and_frame(tmp_path,monkeypatch):
    row=saved_case(tmp_path,'seed',1,'normal')
    proof=json.loads((tmp_path/'seed.timing.json').read_text(encoding='utf-8'))
    calls=[]
    def gain():
        calls.append('gain')
        if len(calls)>1:raise OSError('post gain read failed')
        return proof['before_gain']
    raw=SimpleNamespace(observe_rx_gain=gain,observe_capture_timing=lambda frame:proof['timing'])
    frame=SimpleNamespace(payload=(tmp_path/'seed.iq').read_bytes(),to_dict=lambda:proof['frame'])
    class Evidence:
        def __init__(self):self.client=raw;self.frame=frame
        def __getattr__(self,name):return getattr(self.client,name)
    class Parent:
        def __init__(self,*args):
            self.client=Evidence();self.output=tmp_path;self.app=SimpleNamespace(_rx_record_rf_config=dict(P.FIXED_RF))
        def capture(self,*args):return row
    monkeypatch.setitem(sys.modules,'rf_environment_board_session',SimpleNamespace(EnvironmentSession=Parent))
    session=P.live_factory('release',tmp_path)()
    with pytest.raises(OSError):session.capture('failed',True)
    retained=json.loads((tmp_path/'failed.timing.json').read_text(encoding='utf-8'))
    assert retained['frame']==proof['frame'] and retained['iq_sha256']==proof['iq_sha256']
    assert retained['timing']==proof['timing'] and retained['error']=='post gain read failed'


# ------------------------------------------------------- settling wait comparison

WAIT_GRID=[('s00-normal-w0000','normal',0),('s00-normal-w1000','normal',1000),
    ('s00-narrowband-w0000','narrowband',0),('s00-narrowband-w1000','narrowband',1000),
    ('s01-normal-w1000','normal',1000),('s01-normal-w0000','normal',0),
    ('s01-narrowband-w1000','narrowband',1000),('s01-narrowband-w0000','narrowband',0)]


BASELINE='baseline.json'


@pytest.fixture(autouse=True)
def replay(monkeypatch):
    """Every frame is replayed from its own IQ.  This replaces exactly that
    boundary with a controlled one: the DSP chain has its own suite, and what
    these tests pin is the CAMPAIGN's use of the replay -- that it happens per
    frame, that its label has to agree, and that a replay which fails stops the
    run."""
    calls=[]
    state=dict(passed=True,label=None,mismatches=[])
    def fake(record_path,baseline):
        calls.append(record_path)
        meta=json.loads((Path(record_path)/'metadata.json').read_text(encoding='utf-8'))
        if not state['passed']:
            return dict(passed=False,mismatches=state['mismatches'] or ['replay mismatch'],
                        label=meta.get('environment_label'),points=0)
        return dict(passed=True,mismatches=[],
                    label=state['label'] or meta.get('environment_label'),points=0)
    monkeypatch.setattr(P,'_same_iq_replay',fake)
    return SimpleNamespace(calls=calls,state=state)


def test_a_failed_same_iq_replay_stops_the_run(tmp_path,replay):
    output=tmp_path/'replayfail'
    sessions,factory=wait_sessions(output)
    replay.state['passed']=False
    replay.state['mismatches']=['demod']
    result=P.wait_campaign(factory,output,'release',baseline=BASELINE)
    assert result['stop_cause']=='ValueError' and result['passed'] is False
    assert result['comparison_valid'] is False
    assert not any(r.get('validated') for r in result['records'])
    assert replay.calls, 'the replay must have been attempted'


def test_a_replayed_label_that_disagrees_stops_the_run(tmp_path,replay):
    output=tmp_path/'labelmismatch'
    sessions,factory=wait_sessions(output)
    replay.state['label']='NOT_THE_RECORDED_LABEL'
    result=P.wait_campaign(factory,output,'release',baseline=BASELINE)
    assert result['passed'] is False
    assert not any(r.get('validated') for r in result['records'])


def test_the_campaign_refuses_to_run_without_a_baseline(tmp_path):
    output=tmp_path/'nobaseline'
    sessions,factory=wait_sessions(output)
    with pytest.raises(ValueError,match='baseline'):
        P.wait_campaign(factory,output,'release')
    assert sessions==[], 'no session may be created for an unreplayable campaign'


def wait_sessions(tmp_path):
    sessions=[]
    def factory():
        s=Session(tmp_path,len(sessions)*10);sessions.append(s);return s
    return sessions,factory


def test_wait_campaign_runs_the_declared_grid_in_order(tmp_path):
    sessions,factory=wait_sessions(tmp_path)
    result=P.wait_campaign(factory,tmp_path,'release',baseline=BASELINE)
    assert [w for s in sessions for w in s.waits]==[(t,w) for t,_,w in WAIT_GRID]
    assert result['passed'] and len(result['records'])==8 and len(result['sessions'])==2
    assert result['levels']==[0,1000] and result['stop_cause'] is None
    assert [s.calls[0] for s in sessions]==[('setup',1),('setup',37)]
    assert all(s.calls[-2:]==['cleanup','close'] for s in sessions)
    assert [r['settle_wait_requested_us'] for r in result['records']]==[w for _,_,w in WAIT_GRID]


def test_wait_campaign_rejects_any_design_that_is_not_the_approved_one(tmp_path):
    """The frame cap is a property of the approved design, not of len(levels): a
    third level, a missing zero, a swapped-out 1000, a duplicate, a bad type or a
    plain list must all be refused, and refused BEFORE any factory call.

    The baseline is supplied on purpose.  Without it the campaign's own
    missing-baseline guard fires FIRST and refuses everything, so this criterion
    would pass while the level check had no coverage at all -- proved by deleting
    the level check entirely and watching the whole file stay green."""
    built=[]
    def factory():
        built.append(1)
        return Session(tmp_path,len(built)*10)
    for bad in ((0,1000,2000),(1000,),(1000,2000),(0,0),(0,5000),(),(0,'1000'),
                (0,None),list((0,1000))):
        built.clear()
        with pytest.raises(ValueError,match='approved'):
            P.wait_campaign(factory,tmp_path/('bad'+repr(bad).replace('/','')),'release',bad,
                            baseline=BASELINE)
        assert built==[], 'a rejected design must not have created a session'
    # The same SET in the other order is the same approved experiment.  The
    # factory must write where wait_campaign validates; they share a directory.
    swapped=tmp_path/'swapped'; swapped.mkdir()
    sessions=[]
    def swapped_factory():
        s=Session(swapped,len(sessions)*10);sessions.append(s);return s
    ok=P.wait_campaign(swapped_factory,swapped,'release',(1000,0),baseline=BASELINE)
    assert ok['passed'] and len(ok['records'])==8
    assert ok['levels']==[0,1000]


def test_wait_campaign_stops_at_the_first_clipped_frame(tmp_path):
    sessions=[]
    def factory():
        s=Session(tmp_path,len(sessions)*10,clipped=True);sessions.append(s);return s
    result=P.wait_campaign(factory,tmp_path,'release',baseline=BASELINE)
    assert result['passed'] is False and result['stop_cause']=='CLIPPED'
    assert len(result['records'])==1 and sessions[0].calls[-2:]==['cleanup','close']


def test_wait_campaign_records_the_measured_wait_not_the_request(tmp_path):
    sessions,factory=wait_sessions(tmp_path)
    result=P.wait_campaign(factory,tmp_path,'release',baseline=BASELINE)
    # 1000 us is 1_000_000 ns; the zero level owes nothing.
    assert [r['diagnostic_check']['settle_actual_ns'] for r in result['records']] \
        ==[0,1000000,0,1000000,1000000,0,1000000,0]
    assert [r['instrumentation_clock_reads'] for r in result['records']]==[8,10,8,10,10,8,10,8]
    assert all(r['settle_qualified'] for r in result['records'])
    assert result['comparison_valid'] is True


def test_a_wait_that_came_up_short_is_unqualified_and_stops_the_run(tmp_path):
    output=tmp_path/'short'
    def factory():
        s=Session(output,0,settle_wait_us=1000)
        original=s.capture
        def capture(tag,physical,settle_wait_us=None):
            row=original(tag,physical,settle_wait_us)
            # Only the WAITING level is shortened; a zero level carries no bracket
            # at all, which is the whole point of the zero level.
            if settle_wait_us:
                diag=json.loads((output/(tag+'.timing.json')).read_text(encoding='utf-8'))
                trace=diag['timing']['last_capture_timing']
                trace['settle_end_ns']=trace['settle_begin_ns']+1000   # 1 us, asked 1000
                (output/(tag+'.timing.json')).write_text(json.dumps(diag),encoding='utf-8')
            return row
        s.capture=capture
        return s
    result=P.wait_campaign(factory,output,'release',baseline=BASELINE)
    assert result['stop_cause']=='WAIT_SHORT' and result['passed'] is False
    assert result['comparison_valid'] is False
    # Session 0 runs low-then-high, so the zero level completes and the short
    # 1000 us level is the one that disqualifies the comparison.
    assert [r['settle_wait_requested_us'] for r in result['records']]==[0,1000]
    assert result['records'][0]['settle_qualified'] is True
    assert result['records'][1]['settle_qualified'] is False
    assert result['records'][1]['settle_actual_ns']==1000
    # The raw frame is KEPT: a short wait disqualifies the level, it does not
    # erase the evidence.
    assert (output/'s00-normal-w1000.iq').exists()


def test_an_overshoot_within_the_deadline_is_qualified_and_reported_raw(tmp_path):
    """An overshoot is not a failure -- it is the scheduler's number, not the
    requester's, and it is recorded rather than rounded back to what was asked.

    The measured wait here is 3000 us longer than the request, still inside the
    declared deadline, so the frame counts; what makes it usable evidence is that
    `settle_deviation_ns` says so in raw nanoseconds instead of the run pretending
    it got exactly what it asked for."""
    output=tmp_path/'overshoot'
    sessions=[]
    def factory():
        # Offset the connection generation per session, exactly as the grid fixture
        # does: two sessions that reused it would produce the SAME samples and the
        # duplicate-frame guard would stop the run for a reason unrelated to waits.
        s=Session(output,len(sessions)*10,settle_wait_us=1000,actual_wait_us=4000)
        sessions.append(s);return s
    result=P.wait_campaign(factory,output,'release',baseline=BASELINE)
    assert result['stop_cause'] is None and result['passed'] is True
    waited=[r for r in result['records'] if r['settle_wait_requested_us']==1000]
    assert waited and all(r['settle_qualified'] for r in waited)
    assert all(r['settle_actual_ns']==4000000 for r in waited)
    assert all(r['settle_deviation_ns']==4000000-1000*1000 for r in waited)
    assert all(r['settle_deadline_ns']==P.SETTLE_WAIT_DEADLINE_NS for r in waited)
    # The zero level performs no wait, so it has no deviation to report.
    zeros=[r for r in result['records'] if r['settle_wait_requested_us']==0]
    assert zeros and all(r['settle_deviation_ns']==0 for r in zeros)


def test_a_wait_past_the_deadline_is_unqualified_and_stops_the_run(tmp_path):
    """The deadline is the other bound on a wait that is not what was asked for.

    A sleep that overshot its own ceiling by that much measures the machine's
    load, not the settling behaviour, so the frame keeps its IQ and its raw
    deviation and the comparison stops -- the same treatment a short wait gets,
    from the other side."""
    output=tmp_path/'deadline'
    sessions=[]
    def factory():
        s=Session(output,len(sessions)*10,settle_wait_us=1000,actual_wait_us=P.SETTLE_WAIT_MAX_US+1000)
        sessions.append(s);return s
    result=P.wait_campaign(factory,output,'release',baseline=BASELINE)
    assert result['stop_cause']=='WAIT_OVER_DEADLINE' and result['passed'] is False
    assert result['comparison_valid'] is False
    assert [r['settle_wait_requested_us'] for r in result['records']]==[0,1000]
    over=result['records'][1]
    assert over['settle_qualified'] is False
    assert over['settle_actual_ns']==(P.SETTLE_WAIT_MAX_US+1000)*1000
    assert over['settle_deviation_ns']==over['settle_actual_ns']-1000*1000
    assert (output/(over['tag']+'.iq')).exists(), 'the original IQ is retained'
    assert len(sessions)==1, 'the comparison stops at the first unqualified frame'


def test_a_wait_that_the_daemon_did_not_record_is_rejected(tmp_path):
    output=tmp_path/'case';output.mkdir()
    row=saved_case(output,'s00-normal-w1000',1,'normal',settle_wait_us=1000)
    assert P.validate_diagnostic(row,output,'release','normal',1000)['settle_actual_ns']==1000000
    row=saved_case(output,'s00-normal-w0000-b',2,'normal',settle_wait_us=0)
    with pytest.raises(ValueError):
        P.validate_diagnostic(row,output,'release','normal',1000)


@pytest.mark.parametrize('where,value',[
    ('row',False),('trace',False),('diag',0.0),('row',True),('trace',0.0)])
def test_a_boolean_or_float_wait_is_not_an_integer_wait(tmp_path,where,value):
    """`False != 0` is False and `0.0 != 0` is False, so an equality test would
    accept a boolean or a float where an integer wait is required."""
    output=tmp_path/('t'+where+str(value));output.mkdir()
    row=saved_case(output,'f',1,'normal',settle_wait_us=0)
    if where=='row':
        row['settle_wait_requested_us']=value
    else:
        path=output/'f.timing.json';diag=json.loads(path.read_text(encoding='utf-8'))
        if where=='trace':
            diag['timing']['last_capture_timing']['settle_wait_requested_us']=value
        else:
            diag['settle_wait_requested_us']=value
        path.write_text(json.dumps(diag),encoding='utf-8')
    with pytest.raises(ValueError):
        P.validate_diagnostic(row,output,'release','normal',0)


@pytest.mark.parametrize('orphan',['settle_begin_ns','settle_end_ns'])
def test_a_legacy_trace_with_one_orphan_bracket_is_rejected(tmp_path,orphan):
    output=tmp_path/('orphan'+orphan);output.mkdir()
    row=saved_case(output,'f',1,'normal')          # the minor-4 shape
    path=output/'f.timing.json';diag=json.loads(path.read_text(encoding='utf-8'))
    diag['timing']['last_capture_timing'][orphan]=1
    path.write_text(json.dumps(diag),encoding='utf-8')
    with pytest.raises(ValueError):
        P.validate_diagnostic(row,output,'release','normal')


@pytest.mark.parametrize('missing',['settle_begin_ns','settle_end_ns'])
def test_a_requested_wait_missing_one_bracket_is_rejected(tmp_path,missing):
    output=tmp_path/('one'+missing);output.mkdir()
    row=saved_case(output,'f',1,'normal',settle_wait_us=1000)
    path=output/'f.timing.json';diag=json.loads(path.read_text(encoding='utf-8'))
    del diag['timing']['last_capture_timing'][missing]
    path.write_text(json.dumps(diag),encoding='utf-8')
    with pytest.raises(ValueError):
        P.validate_diagnostic(row,output,'release','normal',1000)


@pytest.mark.parametrize('damage',[
    'truncate_iq','edit_iq','delete_metadata','edit_metadata','delete_receipt',
    'display_not_current','display_other_id','display_disagrees','record_wrong_id',
    'no_record','no_display'])
def test_per_frame_evidence_damage_is_rejected(tmp_path,damage):
    """Every part of the per-frame evidence is load-bearing: damage any field or
    delete any file and the frame must stop being acceptable."""
    output=tmp_path/damage;output.mkdir()
    row=saved_case(output,'f',1,'normal')
    rec=output/'record-f'
    side_path=output/'f.environment.json'
    side=json.loads(side_path.read_text(encoding='utf-8'))
    if damage=='truncate_iq':
        (rec/'frame.iq').write_bytes((rec/'frame.iq').read_bytes()[:-1])
    elif damage=='edit_iq':
        raw=bytearray((rec/'frame.iq').read_bytes());raw[0]^=0xFF;(rec/'frame.iq').write_bytes(bytes(raw))
    elif damage=='delete_metadata':
        (rec/'metadata.json').unlink()
    elif damage=='edit_metadata':
        m=json.loads((rec/'metadata.json').read_text(encoding='utf-8'));m['capture_id']='other'
        (rec/'metadata.json').write_text(json.dumps(m),encoding='utf-8')
    elif damage=='delete_receipt':
        (rec/'receipt.json').unlink()
    elif damage=='display_not_current':
        side['display_environment']['current']=False
    elif damage=='display_other_id':
        side['display_environment']['capture_id']='other'
    elif damage=='display_disagrees':
        side['display_environment']['label']='NOT_THE_RECORDED_LABEL'
    elif damage=='record_wrong_id':
        side['record']['capture_id']='other'
    elif damage=='no_record':
        side.pop('record')
    elif damage=='no_display':
        side.pop('display_environment')
    side_path.write_text(json.dumps(side),encoding='utf-8')
    with pytest.raises(ValueError):
        P.validate_diagnostic(row,output,'release','normal')


def test_a_minor_4_frame_is_not_accepted_as_a_waiting_frame(tmp_path):
    """A minor-4 frame cannot be a waiting frame, and the DAEMON-VERSION check is
    what enforces it: asking for a 1000 us wait switches the expected daemon to
    1.0.3/minor 5, and the minor-4 fixture is refused there.  The settle block
    below that check is unreachable for this input by design, which is why the
    match is pinned to the version message rather than left implicit -- a bare
    raises() here would pass for a reason the test's name does not describe."""
    output=tmp_path/'case4';output.mkdir()
    row=saved_case(output,'s00-normal-w0000',1,'normal')
    assert P.validate_diagnostic(row,output,'release','normal')['settle_actual_ns']==0
    with pytest.raises(ValueError,match='same-frame instrumented timing'):
        P.validate_diagnostic(row,output,'release','normal',1000)


def test_a_zero_wait_frame_must_not_carry_settle_brackets(tmp_path):
    output=tmp_path/'case0';output.mkdir()
    row=saved_case(output,'s00-normal-w0000',1,'normal',settle_wait_us=0)
    diag=json.loads((output/'s00-normal-w0000.timing.json').read_text(encoding='utf-8'))
    diag['timing']['last_capture_timing']['settle_begin_ns']=20
    (output/'s00-normal-w0000.timing.json').write_text(json.dumps(diag),encoding='utf-8')
    with pytest.raises(ValueError):
        P.validate_diagnostic(row,output,'release','normal',0)
