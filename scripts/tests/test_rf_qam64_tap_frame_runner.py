import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_qam64_tap_lane1_runner as R


class Underlying:
    def __init__(self, fail_restore=False):
        self.calls=[]; self.fail_restore=fail_restore; self.mask=0x7F; self.tap=29
    def _send_and_read_json(self,*a,**k):
        return ({"id":1,"ok":True,"result":{}},b"")
    def _request(self,deadline,op,args):
        return {"armed":True,"capabilities":{"control_ops":["idelay.configure"]}}
    def set_idelay_lanes(self,mask,tap):
        self.calls.append((mask,tap))
        if self.fail_restore and mask==0x7F:
            raise RuntimeError("restore refused")
        self.mask,self.tap=mask,tap
        return {"idelay_en":mask,"idelay_tap":tap,"readback":{"idelay_en":mask,"idelay_tap":tap}}

class Frame:
    payload=b"x"*16384
    op="loopback.digital.capture"; physical=False
    capture_id="fake"; samples=4096; sample_rate_hz=60000000; ratio=0
    daemon_timestamp_ns=1; connection_generation=1; config_revision=1
    request_id=1; release_id="6384a07e2d7d135bfb021b9ac75f337f"
    post_readback=SimpleNamespace(rx_armed=False,tx_source_selected=False)
    pre_readback=SimpleNamespace(tx_source_selected=False)

class Client:
    def __init__(self,u): self.client=u; self.capture_timeout=3; self.n=0
    def control_op(self,op,args=None,payload=b""): return {"ok":True}
    def digital_capture(self,args):
        self.n+=1
        if self.fail_probe and self.n==2: raise RuntimeError("injected capture refusal")
        return Frame()

class Session:
    last=None
    def __init__(self,expected_release,out,tx_mode=None,**kw):
        self.u=Underlying(fail_restore=False); self.client=Client(self.u); self.app=SimpleNamespace(set_rf_parameter=lambda n,v:None)
        self.out=Path(out); self.cleanup_called=False; self.close_called=False; Session.last=self
        self._tx_load_records=[]
    def initial(self): return {"safe":True,"release_id":"6384a07e2d7d135bfb021b9ac75f337f"}
    def setup(self,seed):
        self.setup_called=True
        p=self.out/"tx-load-01.bin"; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(b"x"*10160)
        self._tx_load_records=[{"payload_file":"tx-load-01.bin"}]
    def cleanup(self): self.cleanup_called=True; return {"safe":True,"disarm_ack":True}
    def close(self): self.close_called=True

class Poll:
    def __init__(self, fake): self.fake=fake
    def poll(self): return SimpleNamespace(to_dict=lambda: {"release_id":"6384a07e2d7d135bfb021b9ac75f337f","armed":False,"core":False,"pl_status":{"diagnostics":{"idelay_en":{"raw":self.fake.u.mask,"valid":True},"idelay_tap":{"raw":self.fake.u.tap,"valid":True}}}})
    def close(self): pass


def _patch(monkeypatch,tmp_path, fail_capture=False, fail_restore=False, cable=True):
    import rf_status_client as cm
    holder={}
    class S(Session):
        def __init__(self,*a,**k):
            super().__init__(*a,**k); self.u.fail_restore=fail_restore; self.client.fail_probe=fail_capture; holder["s"]=self
    monkeypatch.setattr(R,"LiveUiSession",S)
    monkeypatch.setattr(R,"_pl_snapshot",lambda session,label: {"label":label,"available":True,"pl_status":{"diagnostics":{"idelay_en":{"raw":session.u.mask,"valid":True},"idelay_tap":{"raw":session.u.tap,"valid":True}}}})
    def save(session_dir,tag,tap,frame,load):
        iq=session_dir/(tag+".iq"); meta=session_dir/(tag+".json")
        iq.write_bytes(b"x"*16384); meta.write_text("{}")
        return iq,meta,{"iq_sha256":"x"}
    monkeypatch.setattr(R,"_save_digital",save)
    monkeypatch.setattr(R,"_compare",lambda tx,iq: {"iq":Path(iq).name})
    monkeypatch.setattr(cm,"RfStatusClient",lambda *a,**k: Poll(holder["s"]))
    return holder

def test_normal_completion_restores_before_cleanup_then_compares(monkeypatch,tmp_path):
    h=_patch(monkeypatch,tmp_path)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True,probe_mask=0x40,probe_tap=25,tag="frame")
    assert s["execution_complete"] is True
    fake=h["s"]
    assert fake.u.calls==[(0x40,25),(0x7F,29)]
    assert fake.cleanup_called and fake.close_called
    assert s["restored_tap"]==29 and s["tap29_restore_pending"] is False
    assert s["post_readback"]["diagnostics"]["idelay_en"]["raw"]==0x7F
    assert s["comparison"]["baseline"]["iq"]=="frame-baseline.iq"
    assert s["comparison"]["probe"]["iq"]=="frame-probe.iq"

def test_probe_failure_stops_after_one_capture_and_restores(monkeypatch,tmp_path):
    h=_patch(monkeypatch,tmp_path,fail_capture=True)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True,probe_mask=0x40,probe_tap=25,tag="frame")
    assert s["execution_complete"] is False
    assert s["counts"]["attempted"]["digital_captures"]==2
    assert s["counts"]["completed"]["digital_captures"]==1
    assert h["s"].u.calls==[(0x40,25),(0x7F,29)]
    assert s["restored_tap"]==29
    assert "probe" not in s["comparison"]

def test_restore_failure_never_claims_restored(monkeypatch,tmp_path):
    _patch(monkeypatch,tmp_path,fail_restore=True)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True,probe_mask=0x40,probe_tap=25,tag="frame")
    assert s["restored_tap"] is None
    assert s["tap29_restore_pending"] is True
    assert s["stop_cause"].startswith("restore_idelay")

def test_missing_cable_confirmation_prevents_capture(monkeypatch,tmp_path):
    _patch(monkeypatch,tmp_path)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",False,probe_mask=0x40,probe_tap=25,tag="frame")
    assert s["execution_complete"] is False
    assert s["counts"]["attempted"]["digital_captures"]==0

def test_deadline_is_checked_before_capture(monkeypatch,tmp_path):
    _patch(monkeypatch,tmp_path)
    monkeypatch.setattr(R,"MAX_SECONDS",-1)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True,probe_mask=0x40,probe_tap=25,tag="frame")
    assert s["stop_cause"]=="research_deadline_exceeded"
    assert s["counts"]["attempted"]["digital_captures"]==0
