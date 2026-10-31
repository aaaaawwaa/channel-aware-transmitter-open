"""Regression boundaries from the independent evening review; fake I/O only."""
import dataclasses
import json
import threading
import time
import pytest
import test_rf_headroom as T
import rf_headroom as H
import rf_headroom_replay as RP
import rf_status_client as C
import rf_status_worker as W


def eligible():
    return dict(study=H.STUDY, protocol_minor=6, daemon_version='1.0.4',
                operations=list(H.CONTROL_OPS), initialization_budget=1,
                attempt_budget=5, used=False, active=False, available=True,
                hello_major=1, hello_minor=6, announced_major=1,
                announced_minor=6, armed=False, state='OFFLINE', pending=False,
                pending_op=None, lease_held=True, io_ok=True,
                tx_active=False, rx_busy=False)


@pytest.mark.parametrize('key,value', [('hello_minor',5),('initialization_budget',99),
    ('attempt_budget',True),('used',True),('active',True),('armed',True),
    ('pending',True),('io_ok',False),('tx_active',True),('rx_busy',True)])
def test_preflight_rejects_each_actual_ineligible_boundary(key,value):
    cap=eligible(); cap[key]=value
    assert not H.capability_available(cap)


def test_native_first_off_before_geometry_is_idle_not_preconfigured():
    frame=T.FakeClient()._frame(41,1)
    frame.header['headroom']['actual_rf_before']['pl']['cap_len']=0
    assert H.check_research_frame(H.HeadroomPlan(),T.SESSION,1,frame)['attempt']==1


def test_await_frame_refuses_contradictory_clock_and_close():
    frame=T.FakeClient()._frame(41,1)
    frame.header['headroom']['native_error'].update(stage='CLOCK',code=-3,clock_code=-3)
    frame.header['headroom']['closure'].update(attempted=True,confirmed=False,rx_busy=True)
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(),T.SESSION,1,frame)


def test_zero_error_ber_is_still_off_evidence():
    result=RP.ReplayResult('fake','0'*64,bit_errors=0,compared_bits=1,ber=0.0)
    assert RP.off_violation(result) is not None


def test_existing_evidence_directory_is_never_reused(tmp_path):
    H.HeadroomRecorder(tmp_path,T.SESSION)
    with pytest.raises(FileExistsError): H.HeadroomRecorder(tmp_path,T.SESSION)


class Wire:
    def __init__(self, response, payload=b''):
        self.data=json.dumps(response).encode()+b'\n'+payload
    def settimeout(self,value): pass
    def sendall(self,value): pass
    def recv(self,count):
        data,self.data=self.data[:count],self.data[count:]; return data
    def close(self): pass


def test_bounded_wrong_id_raw_arrives_before_validation():
    client=C.RfStatusClient(); client.use_research_module(H)
    client._session_socket=Wire(dict(id=999,type='research_iq_frame',samples=4096,bytes=16384),b'\0'*16384)
    frame=client._research_frame(time.monotonic()+1,'headroom.capture',{})
    assert frame.header['id']==999 and len(frame.payload)==16384


def test_no_iq_numeric_native_error_is_preserved_in_artifact():
    error=dict(code=-3,stage='CLOCK',clock_code=-3,close_code=-3,
               shutdown_code=-3,close_confirmed=False,iq_available=False)
    client=C.RfStatusClient(); client.use_research_module(H)
    client._session_socket=Wire(dict(id=1,ok=False,error=error))
    frame=client._research_frame(time.monotonic()+1,'headroom.capture',{})
    assert frame.header['error']==error and frame.payload==b''


def test_submission_is_async_and_lands_receipt_without_lock_deadlock():
    worker=W.RfStatusWorker(); entered=threading.Event(); release=threading.Event()
    def run(*args,**kwargs):
        entered.set(); assert release.wait(1)
        return dict(completed=False,stopped_at='UNAVAILABLE')
    worker.headroom_run=run
    began=time.monotonic(); assert worker.submit_headroom_study(T.SESSION,cable_confirmed=True)['queued']
    assert time.monotonic()-began<0.5 and entered.wait(0.5)
    assert worker.submit_headroom_study(T.SESSION)['code']=='BUSY'
    release.set(); worker._headroom_thread.join(1)
    assert not worker._headroom_thread.is_alive()
    assert worker.take_headroom_result()['stopped_at']=='UNAVAILABLE'


@pytest.mark.parametrize('bad', ['final-ack','closing-readback'])
def test_completion_requires_native_ack_and_closed_paths(bad):
    class Client(T.FakeClient):
        def headroom_accept(self,args):
            ack=super().headroom_accept(args)
            return {} if bad=='final-ack' and args['attempt']==5 else ack
        def headroom_closing_readback(self):
            result=super().headroom_closing_readback()
            if bad=='closing-readback': result['confirmed']=False
            return result
    recorder=T.FakeRecorder()
    result=H.HeadroomController(Client(), recorder=recorder, require_hardware=False).run(T.SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert result['completed'] is False


def test_replay_is_persisted_before_violated_off_judgment():
    recorder=T.FakeRecorder()
    def guard(a,e): raise H.HeadroomStopped('OFF synchronized')
    result=H.HeadroomController(T.FakeClient(),recorder=recorder,off_guard=guard,
        replayer=lambda a,e: {'off':'bad'},require_hardware=False).run(T.SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert result['completed'] is False and 1 in recorder.replays


def test_analysis_crash_aborts_before_the_connection_lease_is_released():
    client=T.FakeClient(); recorder=T.FakeRecorder()
    def broken(a,e): raise ValueError('analysis failed')
    with pytest.raises(ValueError, match='analysis failed'):
        H.HeadroomController(client,recorder=recorder,replayer=broken,
            require_hardware=False).run(T.SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert [call[0] for call in client.calls].count('headroom.abort')==1
    assert client.armed is False
    assert recorder.events[-1]==('terminal','CRASHED')


def test_cancellation_between_intent_and_capture_is_reported_as_cancelled():
    client=T.FakeClient(); recorder=T.FakeRecorder()
    def cancelled(): return any(e[0]=='intent' for e in recorder.events)
    result=H.HeadroomController(client,recorder=recorder,cancelled=cancelled,
        require_hardware=False).run(T.SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert result['stopped_at']=='CANCELLED'
    assert not any(c[0]=='headroom.capture' for c in client.calls)
    assert result['disarmed'] is True


def test_closing_confirmed_flag_does_not_override_active_path():
    class Bad(T.FakeClient):
        def headroom_closing_readback(self):
            return dict(confirmed=True,armed=False,state=dict(state='OFFLINE',
                pending=False,lease_held=True),pl=dict(io_ok=True,
                tx=dict(source_selected=True),rx=dict(armed=False)))
    result=H.HeadroomController(Bad(),require_hardware=False).run(
        T.SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert result['disarmed'] is False and result['completed'] is False


@pytest.mark.parametrize('key,value',[('src_cfg',None),('cap_cfg',False),
    ('phy_mode',False),('idelay_en',127.0)])
def test_rf_pl_fields_are_present_and_strict_integers(key,value):
    frame=T.FakeClient()._frame(41,2)
    frame.header['headroom']['actual_rf_before']['pl'][key]=value
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(),T.SESSION,2,frame)


def test_physical_frame_requires_both_rf_readbacks_attested():
    frame=T.FakeClient()._frame(41,2)
    frame.header['headroom']['actual_rf_before']['hardware_verified']=False
    with pytest.raises(H.HeadroomEvidenceError):
        H.check_research_frame(H.HeadroomPlan(),T.SESSION,2,frame)


@pytest.mark.parametrize('op',['load','accept'])
def test_integer_success_ack_is_not_boolean_acceptance(op):
    class Bad(T.FakeClient):
        def headroom_load(self,args,payload):
            result=super().headroom_load(args,payload)
            if op=='load': result['loaded']=1
            return result
        def headroom_accept(self,args):
            result=super().headroom_accept(args)
            if op=='accept' and args['attempt']==1: result['accepted']=1
            return result
    result=H.HeadroomController(Bad(),require_hardware=False).run(
        T.SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    assert result['completed'] is False


def test_crash_receipt_retains_the_actual_closing_readback(tmp_path):
    client=T.FakeClient(); recorder=H.HeadroomRecorder(tmp_path,T.SESSION)
    def broken(a,e): raise ValueError('analysis failed')
    with pytest.raises(ValueError,match='analysis failed'):
        H.HeadroomController(client,recorder=recorder,replayer=broken,
            require_hardware=False).run(T.SESSION,cable_confirmed=True,token='<SET_LOCALLY>')
    study=H.read_study(tmp_path/T.SESSION)
    detail=study['receipt']['detail']
    assert detail['completed'] is False and detail['disarmed'] is True
    assert detail['close_ack']['confirmed'] is True and detail['close_ack']['armed'] is False
    assert detail['abort_ack']=={'aborted':True,'armed':False}
    assert detail['stopped_at']=='CRASHED' and detail['failed_attempt']==1
    assert study['attempts'][1]['decision.json']['accepted'] is False


def test_background_crash_reports_the_same_closure_as_the_saved_receipt(tmp_path):
    import test_rf_headroom_pipeline as TP
    worker,_=TP._wired_worker(TP.headroom_release(tmp_path/'release'))
    peer=T.ScriptedDaemon(); worker.client=T._client(peer,leased=False)
    worker._headroom_root=tmp_path/'records'
    original=worker.headroom_run
    worker.headroom_run=lambda *a,**k: original(*a,token='<SET_LOCALLY>',require_hardware=False,**k)
    def broken(a,e): raise ValueError('analysis failed')
    worker._replayer=lambda *a:broken
    assert worker.submit_headroom_study(T.SESSION,cable_confirmed=True)['queued']
    worker._headroom_thread.join(5)
    assert not worker._headroom_thread.is_alive()
    result=worker.take_headroom_result()
    saved=H.read_study(tmp_path/'records'/T.SESSION)['receipt']['detail']
    assert result['completed'] is False and result['stopped_at']=='CRASHED'
    assert result['disarmed'] is True and result['close_ack']==saved['close_ack']
    assert result['failed_attempt']==1
    worker.client.close()


def test_bytes_received_at_the_deadline_are_retained_as_incomplete():
    client=C.RfStatusClient(); client.use_research_module(H)
    class Late:
        def settimeout(self,value): pass
        def recv(self,count): return b'\x01'*128
    client._session_socket=Late()
    client._send_and_read_json=lambda *a,**k:(dict(id=1,bytes=16384,samples=4096),b'')
    calls=[]
    def remaining(deadline):
        calls.append(True)
        if len(calls)==2: raise TimeoutError('deadline crossed during recv')
        return 1
    client._remaining=remaining
    frame=client._research_frame(1,'headroom.capture',{})
    assert frame.payload==b'\x01'*128
    assert frame.transport['received_bytes']==128 and frame.transport['complete'] is False


def test_complete_iq_received_after_deadline_is_saved_but_never_accepted():
    original=T.FakeClient()._frame(41,2)
    frame=H.ResearchFrame(original.request_id,original.header,original.payload,
        dict(complete=False,error='deadline crossed during recv',received_bytes=16384))
    with pytest.raises(H.HeadroomEvidenceError,match='transport'):
        H.check_research_frame(H.HeadroomPlan(),T.SESSION,2,frame)
