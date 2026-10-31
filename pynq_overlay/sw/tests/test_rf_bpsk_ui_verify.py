"""M4 verifier starts standalone and validates all measured display fields."""
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_rf_bpsk_receiver import frame, signal
import rf_bpsk_pipeline as P

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def test_standalone_verifier_cli_help():
    result = subprocess.run([sys.executable, str(ROOT/'scripts/rf_bpsk_ui_verify.py'), '--help'],
        capture_output=True, text=True, cwd=ROOT, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('key,value', [('carrier_sync', False), ('timing_sync',False),
    ('cfo_hz',1000.), ('reason','CLIPPED'), ('algorithm_version','wrong'), ('available',False)])
def test_wrong_display_scalar_fails_same_frame_verification(tmp_path, key, value):
    from scripts.rf_bpsk_ui_verify import verify
    f = frame(signal()); draft=dict(expected_mode='BPSK', symrate_hz=3000000,
        shaping='RECT', reference_enabled=True, reference_seed=1, decim=1, rx_len=4096,
        input_fs_hz=60000000)
    envelope=P.analyze_demod(f,P.freeze_demod_config(draft,0))
    arm=envelope.result.to_dict()
    display=dict(arm, available=True,current=True); display[key]=value
    meta=dict(f.to_dict(),format='s16_iq_le',demod_result=dataclasses.asdict(envelope),display=display)
    import hashlib
    meta['iq_sha256']=hashlib.sha256(f.payload).hexdigest()
    (tmp_path/'sample.iq').write_bytes(f.payload)
    (tmp_path/'sample.json').write_text(json.dumps(meta),encoding='utf-8')
    (tmp_path/'result.json').write_text(json.dumps(dict(expected_release=f.release_id,
        records=[dict(tag='sample',capture_id=f.capture_id,release_id=f.release_id,
        iq_sha256=meta['iq_sha256'])]*33)),encoding='utf-8')
    result=verify(tmp_path)
    assert not result['passed']
    assert 'display.'+key in result['records'][0]['mismatches']
