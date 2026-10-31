"""Offline S1 verifier refuses changed symbols and starts standalone."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from test_rf_rx_constellation import fixture
from rf_rx_constellation import estimate_constellation
from rf_experiment_record import write_record
import dataclasses
import hashlib
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def test_standalone_help():
    r=subprocess.run([sys.executable,str(ROOT/'scripts/rf_teaching_verify.py'),'--help'],
        cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stderr


def test_exact_record_replays_and_changed_point_is_refused(tmp_path):
    from scripts.rf_teaching_verify import verify_record
    f,e=fixture(); c=estimate_constellation(f,e)
    good=write_record(tmp_path,f,e,c)
    assert verify_record(Path(good['path']))['passed']
    changed=replace(c,points=((99.,99.),)+c.points[1:])
    forged=write_record(tmp_path,f,e,changed)
    r=verify_record(Path(forged['path']))
    assert not r['passed'] and any('points' in s for s in r['mismatches'])


def test_foreign_saved_algorithm_is_not_equivalent(tmp_path):
    from scripts.rf_teaching_verify import verify_record
    f,e=fixture(); c=estimate_constellation(f,e)
    changed=replace(e,algorithm_version='wrong',result=replace(e.result,algorithm_version='wrong'))
    forged=write_record(tmp_path,f,changed,replace(c,algorithm_version='wrong'))
    assert not verify_record(Path(forged['path']))['passed']


def test_mismatched_acquisition_config_cannot_replay_as_valid(tmp_path):
    from scripts.rf_teaching_verify import verify_record
    f,e=fixture()
    changed=replace(e,submitted=replace(e.submitted,decim=2))
    c=estimate_constellation(f,changed)
    receipt=write_record(tmp_path,f,changed,c)
    assert not verify_record(Path(receipt['path']))['passed']


def pilot(tmp_path):
    f,e=fixture();c=estimate_constellation(f,e)
    display=dict(available=True,current=True,capture_id=f.capture_id,
        point_count=len(c.points),symbol_count=c.symbol_count,reason=c.reason,
        normalization=c.normalization)
    meta=dict(f.to_dict(),format='s16_iq_le',demod_result=dataclasses.asdict(e),
        constellation=c.to_dict(),display_constellation=display,
        iq_sha256=hashlib.sha256(f.payload).hexdigest())
    records=[]
    for i in range(33):
        tag='frame-'+str(i)
        (tmp_path/(tag+'.iq')).write_bytes(f.payload)
        (tmp_path/(tag+'.json')).write_text(json.dumps(meta),encoding='utf-8')
        records.append(dict(tag=tag,capture_id=f.capture_id,release_id=f.release_id,
            iq_sha256=meta['iq_sha256']))
    report=dict(passed=True,records=records,expected_release=f.release_id)
    (tmp_path/'result.json').write_text(json.dumps(report),encoding='utf-8')
    return meta,report


@pytest.mark.parametrize('change',['row_identity','display','duplicate','off_points'])
def test_pilot_checks_row_and_display_identity_and_unique_artifacts(tmp_path,change):
    from scripts.rf_teaching_verify import verify_pilot
    meta,report=pilot(tmp_path)
    assert verify_pilot(tmp_path)['passed']
    if change=='row_identity':report['records'][0]['capture_id']='foreign'
    elif change=='duplicate':report['records'][1]=report['records'][0]
    elif change=='off_points':report['records'][0]['on']=False
    else:
        meta['display_constellation']['point_count']=999
        (tmp_path/'frame-0.json').write_text(json.dumps(meta),encoding='utf-8')
    (tmp_path/'result.json').write_text(json.dumps(report),encoding='utf-8')
    assert not verify_pilot(tmp_path)['passed']
