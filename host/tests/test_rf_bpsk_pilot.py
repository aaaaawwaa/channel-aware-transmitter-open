"""Pilot choreography: refuse takeovers, keep failures and always verify cleanup."""
import sys
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
from scripts.rf_bpsk_board_pilot import campaign
from rf_status_client import RfStatusSnapshot,PlStatus,TxStatus,RxStatus
from rf_rx_types import RxIqFrame,RxReadback
from rf_bpsk_receiver import BpskConfig,receive_bpsk
from rf_iq_signal import generate_digital_frame


class Client:
    def __init__(self, failure=None,initial_armed=False):
        self.failure=failure;self.armed=initial_armed;self.events=[];self.payload=None
    def poll(self):
        return RfStatusSnapshot(online=True,release_id='expected',armed=self.armed,
            pl_status=PlStatus(io_ok=True,tx=TxStatus(False),rx=RxStatus(False)))
    def control_arm(self,token):self.events.append('arm');self.armed=True
    def control_op(self,op,args,payload=b''):
        self.events.append(op)
        if op=='tx.load':self.payload=payload
    def capture(self,args,physical=False):
        self.events.append('on' if physical else 'off')
        if physical and self.failure=='timeout':raise TimeoutError('test timeout')
        if physical:
            a=np.frombuffer(self.payload,dtype='<i2').reshape(-1,2)
            a=np.tile(a,(2,1))[:4096].copy()
            if self.failure=='clipped':a[0,0]=2047
            payload=a.tobytes()
        else:payload=b'\0'*16384
        return RxIqFrame('fake',payload,4096,60000000,0,1,1,1,1,'expected',
            'loopback.physical.capture' if physical else 'rx.capture',physical,
            RxReadback(True),RxReadback(True,rx_armed=False,tx_source_selected=False),1.)
    def control_disarm(self):
        self.events.append('disarm');self.armed=False
        if self.failure=='disarm':raise TimeoutError('missing ack')
    def close(self):self.events.append('close')


def run(tmp_path,**kwargs):
    clients=[]
    def factory():
        c=Client(**kwargs);clients.append(c);return c
    report,_=campaign(factory,tmp_path,'expected','fake-token',generate_digital_frame,receive_bpsk,BpskConfig)
    return report,clients


def test_finite_three_connections_and_off_before_tx_load(tmp_path):
    r,clients=run(tmp_path)
    assert r['passed'] and r['on_captured']==30 and r['off_captured']==3
    assert r['safe'] and r['compared_bits']==4140 and r['bit_errors']==0
    assert len(clients)==3
    for c in clients:
        assert c.events[:5]==['arm','rf.init','rf.configure','off','tx.load']
        assert c.events[-2:]==['disarm','close']


@pytest.mark.parametrize('failure',['timeout','clipped','disarm'])
def test_failure_stops_remaining_sessions_and_cleans_owned_control(tmp_path,failure):
    r,clients=run(tmp_path,failure=failure)
    assert not r['passed'] and len(clients)==1
    assert clients[0].events[-2:]==['disarm','close']
    if failure=='clipped':assert r['records'][-1]['result']['reason']=='CLIPPED'
    if failure=='timeout':assert r['error'] and r['on_captured']==0


def test_operator_armed_state_never_taken_over_or_disarmed(tmp_path):
    r,clients=run(tmp_path,initial_armed=True)
    assert not r['passed'] and not r['safe'] and r['error']
    assert clients[0].events==['close']


def test_last_session_missing_disarm_ack_cannot_pass_even_when_readback_safe(tmp_path):
    clients=[Client(),Client(),Client(failure='disarm')]
    r,_=campaign(iter(clients).__next__,tmp_path,'expected','fake-token',
        generate_digital_frame,receive_bpsk,BpskConfig)
    assert r['safe'] and r['on_aligned']==30
    assert not r['passed'] and r['sessions'][-1]['disarm_error']
