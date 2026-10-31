import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_qam64_frame29_lane1_tap30_runner as R

class U:
    def __init__(self): self.mask=0x7F; self.tap=29; self.calls=[]; self.fail_restore=False; self.bad_quiet=False
    def set_idelay_lanes(self,mask,tap):
        self.calls.append((mask,tap))
        if self.fail_restore and mask==0x7F: raise RuntimeError("restore refused")
        self.mask,self.tap=mask,tap
        return {"idelay_en":mask,"idelay_tap":tap,"readback":{"idelay_en":mask,"idelay_tap":tap}}
    def _send_and_read_json(self,*a,**k): return ({"id":1,"ok":True,"result":{}},b"")
    def _request(self,deadline,op,args): return {"armed":True,"capabilities":{"control_ops":["idelay.configure"]}}

class Frame:
    payload=b"x"*16384; op="loopback.digital.capture"; physical=False
    capture_id="fake"; samples=4096; sample_rate_hz=60000000; ratio=0
    daemon_timestamp_ns=1; connection_generation=1; config_revision=1
    request_id=1; release_id="6384a07e2d7d135bfb021b9ac75f337f"
    post_readback=SimpleNamespace(rx_armed=False,tx_source_selected=False)
    pre_readback=SimpleNamespace(tx_source_selected=False)

class Client:
    def __init__(self,u): self.client=u; self.capture_timeout=10; self.n=0; self.fail_at=None
    def control_op(self,op,args=None,payload=b""): return {"ok":True}
    def poll(self): return SimpleNamespace(to_dict=lambda: {"safe":None,"release_id":"6384a07e2d7d135bfb021b9ac75f337f","pl_status":{"io_ok":True,"tx":{"source_selected":self.client.bad_quiet},"rx":{"armed":False},"diagnostics":{"idelay_en":{"raw":self.client.mask,"valid":True},"idelay_tap":{"raw":self.client.tap,"valid":True}}}})
    def digital_capture(self,args):
        self.n+=1
        if self.fail_at is not None and self.n==self.fail_at: raise RuntimeError("injected capture refused")
        return Frame()

class Session:
    instances=[]; last=None; order=[]
    def __init__(self,expected_release,out,tx_mode=None,**kw):
        self.u=U(); self.client=Client(self.u); self.app=SimpleNamespace(set_rf_parameter=lambda n,v:None)
        self.out=Path(out); self.out.mkdir(parents=True,exist_ok=True); self._tx_load_records=[]; self.cleanup_called=False; self.close_called=False
        Session.instances.append(self); Session.last=self
    def initial(self): return {"safe":True,"release_id":"6384a07e2d7d135bfb021b9ac75f337f"}
    def setup(self,seed):
        p=self.out/"tx-load-01.bin"; p.write_bytes(b"x"*10160); self._tx_load_records=[{"payload_file":"tx-load-01.bin"}]
    def cleanup(self): self.cleanup_called=True; Session.order.append("cleanup"); return {"safe":True,"disarm_ack":True}
    def close(self): self.close_called=True

class Poll:
    def __init__(self,s): self.s=s
    def poll(self): return SimpleNamespace(to_dict=lambda: {"release_id":"6384a07e2d7d135bfb021b9ac75f337f","armed":False,"core":False,"pl_status":{"diagnostics":{"idelay_en":{"raw":self.s.u.mask,"valid":True},"idelay_tap":{"raw":self.s.u.tap,"valid":True}}}})
    def close(self): pass

def _patch(monkeypatch,tmp_path,fail_at=None,fail_restore=False,quiet=False):
    import rf_status_client as cm
    holder={}
    class S(Session):
        def __init__(self,*a,**k):
            super().__init__(*a,**k); self.u.fail_restore=fail_restore; self.client.fail_at=fail_at; self.client.client.bad_quiet=quiet
            holder.setdefault("sessions",[]).append(self)
    monkeypatch.setattr(R,"LiveUiSession",S)
    monkeypatch.setattr(R,"_pl_snapshot",lambda session,label: {"label":label,"available":True,"pl_status":{"diagnostics":{"idelay_en":{"raw":session.u.mask,"valid":True},"idelay_tap":{"raw":session.u.tap,"valid":True}}}})
    def save(sd,tag,tap,frame,load):
        iq=sd/(tag+".iq"); meta=sd/(tag+".json"); iq.write_bytes(b"x"*16384); meta.write_text("{}")
        return iq,meta,{"iq_sha256":"x"}
    monkeypatch.setattr(R,"_save_digital",save)
    def compare(tx,iq):
        Session.order.append("compare")
        return {"lane1_error_count": 0 if "baseline" not in Path(iq).name else 10,
                "other_bit_types": [], "mismatch_pairs": 1, "exact_pairs": 4095,
                "alignment":{"iq_swap":True,"offset":0,"exact_pairs":4095,"mismatch_pairs":1},"iq_sha256":"x"}
    monkeypatch.setattr(R,"_compare_all",compare)
    monkeypatch.setattr(cm,"RfStatusClient",lambda *a,**k: Poll(holder["sessions"][-1] if holder.get("sessions") else Session.last))
    return holder

def test_normal_baseline_tap30_two_frames_restore(monkeypatch,tmp_path):
    h=_patch(monkeypatch,tmp_path)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True)
    assert s["execution_complete"] is True
    assert [r["requested_tap"] for r in s["rows"]]==[29,30,30]
    assert s["counts"]["attempted"]["digital_captures"]==3
    assert h["sessions"][0].u.calls==[(2,30),(127,29)]
    assert s["restored_tap"]==29
    assert Session.order.index("cleanup") < Session.order.index("compare")

def test_b_failure_stops_c_and_restores(monkeypatch,tmp_path):
    h=_patch(monkeypatch,tmp_path,fail_at=2)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True)
    assert s["execution_complete"] is False
    assert s["counts"]["attempted"]["digital_captures"]==2
    assert len([r for r in s["rows"] if r["requested_tap"]==30])==0
    assert h["sessions"][0].u.calls==[(2,30),(127,29)]

def test_missing_wiring_no_capture(monkeypatch,tmp_path):
    _patch(monkeypatch,tmp_path)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",False)
    assert s["execution_complete"] is False
    assert s["counts"]["attempted"]["digital_captures"]==0

def test_restore_failure_never_claims_restored(monkeypatch,tmp_path):
    _patch(monkeypatch,tmp_path,fail_restore=True)
    monkeypatch.setattr(R,"_recovery_tap29",lambda *a,**k: (_ for _ in ()).throw(RuntimeError("recovery failed")))
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True)
    assert s["restored_tap"] is None
    assert s["tap29_restore_pending"] is True

def test_illegal_tap_before_any_control(monkeypatch,tmp_path):
    _patch(monkeypatch,tmp_path)
    monkeypatch.setattr(R,"POINT",{"label":"bad","mask":2,"tap":32})
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True)
    assert s["execution_complete"] is False
    assert s["counts"]["attempted"]["main_sessions"]==0
    assert s["counts"]["attempted"]["digital_captures"]==0

def test_budget_blocks_capture(monkeypatch,tmp_path):
    _patch(monkeypatch,tmp_path)
    b=dict(R.BUDGET); b["digital_captures"]=0
    monkeypatch.setattr(R,"BUDGET",b)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True)
    assert s["execution_complete"] is False
    assert s["counts"]["attempted"]["digital_captures"]==0

def test_quiet_failure_blocks_capture(monkeypatch,tmp_path):
    _patch(monkeypatch,tmp_path,quiet=True)
    s=R.run("6384a07e2d7d135bfb021b9ac75f337f",tmp_path/"out",True)
    assert s["execution_complete"] is False
    assert s["counts"]["attempted"]["digital_captures"]==0
    assert "quiet_check_failed" in s["stop_cause"]
