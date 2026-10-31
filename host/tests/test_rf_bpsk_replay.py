import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'pynq_overlay/sw'))
from scripts import rf_bpsk_replay as C
from rf_bpsk_receiver import BpskConfig


def inputs(tmp_path):
    from rf_iq_signal import generate_digital_frame
    _, raw = generate_digital_frame(dict(mode='BPSK',filter='RECT',rolloff='0.35',seed=1,
                                        sample_rate_hz=60000000,symbol_rate_hz=3000000,amplitude=500))
    iq = np.frombuffer(raw,dtype='<i2').reshape(-1,2)
    raw = np.tile(iq,(2,1))[:4096].tobytes()
    p=tmp_path/'input.iq';p.write_bytes(raw)
    m=tmp_path/'metadata.json';m.write_text(json.dumps(dict(capture_id='replay',samples=4096,
        sample_rate_hz=60000000,ratio=0,daemon_timestamp_ns=1,connection_generation=1,
        config_revision=1,request_id=1,release_id='test',op='rx.capture',physical=False,
        completed_at_monotonic=100,pre_io_ok=True,post_io_ok=True,post_rx_armed=False,
        post_tx_source_selected=False)))
    return p,m,hashlib.sha256(raw).hexdigest()


def test_replay_records_exact_input_and_reference(tmp_path):
    p,m,h=inputs(tmp_path)
    r=C.replay(p,m,tmp_path/'out',h,BpskConfig(reference_prbs7_seed=1))
    assert r['result']['ber']==0 and r['result']['compared_bits']>=64
    assert r['iq_sha256']==h and r['config']['reference_prbs7_seed']==1
    assert json.loads((tmp_path/'out/result.json').read_text())==r


def test_hash_mismatch_and_output_collision_are_refused(tmp_path):
    p,m,h=inputs(tmp_path)
    with pytest.raises(ValueError,match='hash'):
        C.replay(p,m,tmp_path/'out','0'*64,BpskConfig())
    assert not (tmp_path/'out').exists()
    (tmp_path/'out').mkdir()
    with pytest.raises(FileExistsError):
        C.replay(p,m,tmp_path/'out',h,BpskConfig())


def test_metadata_length_mismatch_and_oversized_input(tmp_path):
    p,m,h=inputs(tmp_path)
    meta=json.loads(m.read_text());meta['samples']=4095;m.write_text(json.dumps(meta))
    with pytest.raises(ValueError): C.replay(p,m,tmp_path/'out',h,BpskConfig())
    p.write_bytes(b'0'*20000)
    with pytest.raises(ValueError,match='size'): C.replay(p,m,tmp_path/'out',h,BpskConfig())


def test_no_reference_cli_has_no_ber_and_refusal_is_nonzero(tmp_path):
    p,m,h=inputs(tmp_path)
    assert C.main(['--iq',str(p),'--metadata',str(m),'--sha256',h,'--output',str(tmp_path/'out')])==0
    assert json.loads((tmp_path/'out/result.json').read_text())['result']['ber'] is None
    assert C.main(['--iq',str(p),'--metadata',str(m),'--sha256',h,'--output',str(tmp_path/'bad'),
                   '--symbol-rate-hz','2000000'])==2


def test_wrong_declared_format_is_refused_before_output(tmp_path):
    p,m,h=inputs(tmp_path)
    meta=json.loads(m.read_text());meta['format']='float32_iq';m.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='format'):
        C.replay(p,m,tmp_path/'out',h,BpskConfig())
    assert not (tmp_path/'out').exists()
