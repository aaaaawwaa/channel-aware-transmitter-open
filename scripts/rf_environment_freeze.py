"""Freeze the qualified development candidate before collecting any formal TEST."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
from scripts import merged_release as R
from scripts.rf_environment_verify_pilot import verify
from scripts.rf_environment_acceptance import validate_freeze,FIXED_RF,FIXED_RX
from rf_environment import EnvironmentBaseline,VERSION
from rf_bpsk_receiver import VERSION as BPSK_VERSION
from rf_experiment_scenarios import ScenarioConfig,generate_scenario

# The two 46 dB TRAIN/VAL entries belong to the same formal chain: the session entry
# produces the frames the freeze excludes (calibration_iq_sha256) and the adapter builds
# the set the final baseline is fitted from, so a silent change to either must invalidate
# a signed declaration at check_candidate time.
FORMAL_TOOLS=('rf_environment_board_session.py','rf_environment_pilot.py',
    'rf_environment_trainval_session.py','rf_environment_trainval_adapter.py',
    'rf_bpsk_ui_board_session.py','rf_bpsk_ui_acceptance.py','rf_environment_acceptance.py',
    'rf_environment_board_run.py','rf_environment_freeze.py','rf_environment_verify_acceptance.py',
    'rf_environment_verify_pilot.py','rf_environment_verify.py','rf_teaching_verify.py','rf_bpsk_replay.py')


def tool_hashes():
    return {name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest() for name in FORMAL_TOOLS}


def check_candidate(freeze,candidate):
    """Check exact code/baseline independently of the supplied expected labels."""
    manifest=R.verify_release(candidate,require_software=True)
    value,digest=validate_freeze(freeze,manifest['release_id'])
    baseline=EnvironmentBaseline.from_dict(json.loads(
        (Path(candidate)/'rf_environment_baseline.json').read_text(encoding='utf-8')))
    scenes={kind:generate_scenario(ScenarioConfig(kind=kind)).declaration_dict()
            for kind in ('normal','narrowband','pulse','compound')}
    if (manifest.get('software_contract')!='cnn-bpsk-environment-v1'
            or R.software_digests(ROOT,manifest['software_contract'])!=manifest['software']
            or value['software']!=manifest['software'] or value['environment_version']!=VERSION
            or value['algorithm_version']!=BPSK_VERSION or value['baseline_sha256']!=baseline.sha256
            or value['scene_declarations']!=scenes or value.get('tool_hashes')!=tool_hashes()):
        raise ValueError('freeze must bind the exact verified candidate/code/baseline/scenes')
    return value,digest


def build(candidate,pilot,output):
    candidate,pilot,output=map(Path,(candidate,pilot,output))
    if output.exists():raise FileExistsError(str(output))
    manifest=R.verify_release(candidate,require_software=True)
    # Recompute the qualified development replay; never trust a copied pass boolean.
    replay=verify(pilot,candidate)
    result=json.loads((pilot/'result.json').read_text(encoding='utf-8'))
    receipt=json.loads((pilot/'receipt.json').read_text(encoding='utf-8'))
    expected={'normal':'CLEAR','narrowband':'NARROWBAND','pulse':'PULSE'}
    checked=replay.get('checked') or []
    kinds=('normal','narrowband','pulse','compound','off','domain')*3
    qualified=(len(checked)==18 and all(row.get('tag')=='s%02d-%s-00'%(index//6,kind)
        for index,(row,kind) in enumerate(zip(checked,kinds))) and all(row.get('label')==expected[kind]
        if kind in expected else row.get('label') in ('UNKNOWN','INVALID')
        for row,kind in zip(checked,kinds)))
    if (not replay['passed'] or not qualified or result.get('passed') is not True
            or receipt.get('passed') is not True or receipt.get('board_state')!='VERIFIED_SAFE'
            or receipt.get('release_id')!=manifest['release_id']
            or result.get('known_correct')!=9
            or result.get('negative_false_clear')!=0):
        raise ValueError('complete successful development pilot and same-IQ replay required')
    baseline=EnvironmentBaseline.from_dict(json.loads(
        (candidate/'rf_environment_baseline.json').read_text(encoding='utf-8')))
    # All existing input IQ is excluded, including derived siblings and older pilots.
    # This list is hashed before TEST; formal frames can never join calibration.
    excluded=sorted({dict(source)['iq_sha256'] for source in baseline.sources} | {hashlib.sha256(p.read_bytes()).hexdigest()
        for area in ('s1','s2') for p in (ROOT/'evidence/rf-teaching'/area).rglob('*.iq')
        if p.is_file() and not p.is_symlink() and p.stat().st_size==16384})
    freeze=dict(schema='s2-formal-freeze-v1',expected_release=manifest['release_id'],
        software=manifest['software'],environment_version=VERSION,algorithm_version=BPSK_VERSION,
        tool_hashes=tool_hashes(),
        baseline_sha256=baseline.sha256,baseline_provenance_status=baseline.calibration_status,
        parameters_frozen_for_test=True,fixed_rf=FIXED_RF,rx_config=FIXED_RX,
        scene_declarations={kind:generate_scenario(ScenarioConfig(kind=kind)).declaration_dict()
            for kind in ('normal','narrowband','pulse','compound')},
        reference_seeds=[1,37,73],actual_scene_prbs7_seed=1,split='test',expected_frames=108,
        calibration_iq_sha256=excluded,
        development_receipt_sha256=hashlib.sha256((pilot/'receipt.json').read_bytes()).hexdigest(),
        window_scope='4096 samples / 60 MHz / 68.27 us; no continuous monitoring',
        thresholds_policy='no TEST tuning; a failed TEST remains failed',
        touch_latency='NOT_MEASURED; user waived dedicated timing')
    _,digest=check_candidate(freeze,candidate)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:json.dump(freeze,f,indent=2,allow_nan=False)
    return dict(freeze_file=str(output.resolve()),canonical_sha256=digest,
                expected_release=manifest['release_id'],excluded_iq=len(excluded))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('candidate','pilot','output'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args();print(json.dumps(build(a.candidate,a.pilot,a.output),indent=2))
