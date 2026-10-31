"""The initialized receiver probe never transmits or captures IQ."""
import pytest
from scripts import rf_gain_observation_probe as P


class Session:
    def __init__(self,safe=True,fail=False):
        self.calls=[];self.client=self;self.safe=safe;self.fail=fail;self.started=False
    def initial(self):return dict(safe=self.safe,release_id='release')
    def setup(self,seed):self.calls.append(('setup',seed));self.started=True
    def observe_rx_gain(self):
        self.calls.append('observe')
        if self.started and self.fail:raise RuntimeError('checked SPI failure')
        return dict(release_id='release',daemon='1.0.1',protocol_major=1,protocol_minor=3,
            gain_observation={'index_stable':True} if self.started else None,
            reason=None if self.started else 'NOT_READY_OR_UNSUPPORTED')
    def poll(self):return self
    def to_dict(self):return {'release_id':'release'}
    def egress_safe(self,status):return True
    def cleanup(self):self.calls.append('cleanup');return dict(safe=True,disarm_ack=True)
    def close(self):self.calls.append('close')
    def capture(self,*args):raise AssertionError('probe must never capture')


def test_three_raw_observations_with_no_rf_capture(tmp_path):
    session=Session();result=P.campaign(lambda:session,tmp_path,'release')
    assert result['passed'] and result['physical_frames']==0
    assert len(result['observations'])==3 and result['closed'] and result['disarm_ack']
    assert session.calls==['observe',('setup',1),'observe','observe','observe','cleanup','close']
    assert result['scope']=='initialized receiver raw reads; no RF transmit or IQ capture'


def test_initial_unsafe_session_is_not_initialized_or_disarmed(tmp_path):
    session=Session(safe=False);result=P.campaign(lambda:session,tmp_path,'release')
    assert not result['passed'] and not result['owned']
    assert session.calls==['close']


def test_failed_observation_is_retained_and_owned_session_closed(tmp_path):
    session=Session(fail=True);result=P.campaign(lambda:session,tmp_path,'release')
    assert not result['passed'] and result['error']=='checked SPI failure'
    assert result['closed'] and result['disarm_ack'] and (tmp_path/'result.json').exists()


def test_existing_output_cannot_be_overwritten(tmp_path):
    (tmp_path/'result.json').write_text('old failed evidence')
    with pytest.raises(FileExistsError):P.campaign(Session,tmp_path,'release')
