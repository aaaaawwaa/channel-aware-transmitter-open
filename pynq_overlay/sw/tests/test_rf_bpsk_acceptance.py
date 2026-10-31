"""Finite acceptance owns only a session it armed; stop on incomplete closure."""
import sys
import subprocess
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def test_obsolete_live_entry_refuses_before_client_import():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, str(root/'scripts/rf_bpsk_ui_acceptance.py')],
        capture_output=True, text=True, cwd=root, timeout=20)
    assert result.returncode != 0
    assert 'REFUSE: use rf_bpsk_ui_board_run.py' in result.stderr


class Session:
    def __init__(self, bad=None):
        self.bad, self.closed, self.setup_called = bad, False, False
    def initial(self):
        return dict(safe=self.bad != 'armed', release_id='other' if self.bad == 'release' else 'test')
    def setup(self, seed): self.setup_called = True
    def capture(self, tag, physical):
        if self.bad == 'timeout': raise TimeoutError('deadline')
        return dict(tag=tag, on=physical, result=dict(reference_sync=physical,
            bit_errors=0 if physical else None, compared_bits=138 if physical else None,
            ber=0 if physical else None, reason='CLIPPED' if self.bad == 'clip' else ''),
            egress_safe=self.bad != 'unsafe')
    def cleanup(self):
        self.closed=True
        return dict(safe=True, disarm_ack=self.bad != 'close')
    def close(self): pass


@pytest.mark.parametrize('bad', ['armed','release','timeout','clip','unsafe','close'])
def test_unsafe_campaign_stops_without_second_session(tmp_path, bad):
    from scripts.rf_bpsk_ui_acceptance import campaign
    sessions=[]
    def factory():
        s=Session(bad); sessions.append(s); return s
    result=campaign(factory,tmp_path,'test')
    assert not result['passed'] and len(sessions)==1
    assert sessions[0].setup_called is (bad not in ('armed','release'))
    assert sessions[0].closed is (bad not in ('armed','release'))
    if bad in ('clip','unsafe'): assert len(result['records'])==1


def test_complete_campaign_has_exact_finite_frames_and_seeds(tmp_path):
    from scripts.rf_bpsk_ui_acceptance import campaign
    result=campaign(lambda: Session(), tmp_path, 'test')
    assert result['passed'] and len(result['sessions'])==3
    assert len(result['records'])==33 and result['on_aligned']==30
    assert result['off_false_ber']==0 and result['aggregate_ber']==0


def test_setup_error_still_closes_the_owned_session_and_stops(tmp_path):
    """A session raises during setup -> cleanup must run (it was armed) and the
    campaign must stop without a second session (board incident 2026-10-03)."""
    from scripts.rf_bpsk_ui_acceptance import campaign
    class ExplodingSession(Session):
        def setup(self, seed):
            self.setup_called = True
            raise RuntimeError('controlled setup failure')
    sessions=[]
    def factory():
        s=ExplodingSession(); sessions.append(s); return s
    result=campaign(factory, tmp_path, 'test')
    assert not result['passed'] and len(sessions)==1
    assert sessions[0].setup_called is True
    assert sessions[0].closed is True
    assert result['stop_cause'] and 'control_error' in result['stop_cause']


@pytest.mark.parametrize('errors,bits,ber', [(2,138,2/138), (0,None,None),
                                          (True,138,0), (0,138,float('nan')),
                                          (0,138,.001)])
def test_synchronization_alone_cannot_pass_ber_gate(tmp_path, errors, bits, ber):
    from scripts.rf_bpsk_ui_acceptance import campaign
    class BadBerSession(Session):
        def capture(self, tag, physical):
            row=super().capture(tag,physical)
            if physical:
                row['result'].update(bit_errors=errors, compared_bits=bits, ber=ber)
            return row
    result=campaign(lambda: BadBerSession(),tmp_path,'test')
    assert not result['passed']


def test_at_least_27_aligned_frames_is_the_frozen_gate(tmp_path):
    from scripts.rf_bpsk_ui_acceptance import campaign
    class OneUnalignedSession(Session):
        def capture(self, tag, physical):
            row=super().capture(tag,physical)
            if physical and tag.endswith('-00'):
                row['result'].update(reference_sync=False,bit_errors=None,compared_bits=None,ber=None)
            return row
    result=campaign(lambda: OneUnalignedSession(),tmp_path,'test')
    assert result['passed'] and result['on_aligned']==27
