import copy
import hashlib
import json
from pathlib import Path
import sys
import pytest
from scripts import rf_environment_acceptance as A
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'pynq_overlay/sw'))
from rf_experiment_scenarios import ScenarioConfig, generate_scenario


def freeze():
    # The declaration carries the single-source domain, exactly as the product does:
    # a private 40000/v3 copy is what the migration removed.
    return dict(expected_release='release', environment_version=A.ENVIRONMENT_VERSION,
        baseline_sha256='b'*64, algorithm_version='bpsk-rect-snapshot-v2', split='test',
        expected_frames=108, reference_seeds=[1,37,73],actual_scene_prbs7_seed=1,
        fixed_rf=dict(A.FIXED_RF),
        software={name:'a'*64 for name in ('rf_environment.py','rf_environment_baseline.json',
            'rf_experiment_scenarios.py','rf_status_worker.py','ui_model.py','ui_view.py')},
        rx_config=dict(expected_mode='BPSK',symrate_hz=3000000,shaping='RECT',decim=1,
            rx_len=4096,input_fs_hz=60000000,reference_enabled=True),
        scene_declarations={kind:generate_scenario(ScenarioConfig(kind=kind)).declaration_dict()
            for kind in ('normal','narrowband','pulse','compound')}, calibration_iq_sha256=[])


class Session:
    def __init__(self, output, counter, unsafe=False):
        self.output,self.counter,self.unsafe=output,counter,unsafe
        self.kind='normal';self.reference_seed=1
    def initial(self): return {'safe':not self.unsafe,'release_id':'release'}
    def setup(self, seed): self.reference_seed=seed
    def scene(self, kind): self.kind=kind
    def domain(self, outside): self.kind='domain' if outside else 'normal'
    def cleanup(self): self.counter['closes']+=1;return {'safe':True,'disarm_ack':True}
    def close(self): pass
    def save(self, tag, row, meta, side):
        (self.output/(tag+'.json')).write_text(json.dumps(meta))
        (self.output/(tag+'.environment.json')).write_text(json.dumps(side))
        return row
    def capture(self,tag,physical):
        self.counter['frames']+=1;n=self.counter['frames'];f=freeze()
        label={'normal':'CLEAR','narrowband':'NARROWBAND','pulse':'PULSE','compound':'UNKNOWN','domain':'INVALID'}[self.kind] if physical else 'UNKNOWN'
        identity=dict(capture_id='client-local-'+str(n%30),release_id='release',
            connection_generation=1,config_revision=2,completed_at_monotonic=float(n))
        payload=bytes([n%256])*16384;digest=hashlib.sha256(payload).hexdigest()
        frame=dict(identity,samples=4096,bytes=16384,sample_rate_hz=60000000,ratio=0,
            decimation=1,output_fs_hz=60000000,op='loopback.physical.capture' if physical else 'rx.capture',
            physical=physical,request_id=n,daemon_timestamp_ns=n*1000000,pre_io_ok=True,
            post_io_ok=True,post_rx_armed=False,post_tx_source_selected=False)
        env=dict(identity,submitted_revision=1,algorithm_version=f['algorithm_version'],
            environment_version=f['environment_version'],baseline_sha256=f['baseline_sha256'],
            label=label,reason='TEST_REASON',features={},rule_evidence={})
        submitted=dict(config=dict(mode='BPSK',symbol_rate_hz=2000000 if self.kind=='domain' else 3000000,
            shaping='RECT',reference_prbs7_seed=self.reference_seed),revision=1,
            decim=1,rx_len=4096,input_fs_hz=60000000)
        demod=dict(result=dict(identity,algorithm_version=f['algorithm_version']),
            algorithm_version=f['algorithm_version'],submitted=submitted)
        meta=dict(frame,iq_sha256=digest,handoff_frame_matches=True,handoff_frame=frame,demod_result=demod,
            capture_status=dict(state='done',close_confirmed=True,cancelled=False,physical=physical,capture_id=identity['capture_id']))
        scene=f['scene_declarations']['normal' if self.kind=='domain' else self.kind]
        display=dict(available=True,current=True,capture_id=identity['capture_id'],label=label,reason='TEST_REASON',
            features={},baseline_sha256=f['baseline_sha256'])
        side=dict(environment=copy.deepcopy(env),display_environment=display,rf_config=f['fixed_rf'],
            scenario_declaration=scene,iq_sha256=digest,capture_id=identity['capture_id'],release_id='release')
        row=dict(capture_id=identity['capture_id'],release_id='release',iq_sha256=digest,
            egress_safe=True,iq_file=tag+'.iq',metadata_file=tag+'.json',environment_file=tag+'.environment.json',environment=env)
        (self.output/(tag+'.iq')).write_bytes(payload)
        return self.save(tag,row,meta,side)


def run(tmp_path, cls=Session, declaration=None):
    count={'frames':0,'closes':0}
    return A.campaign(lambda:cls(tmp_path,count),tmp_path,'release',declaration or freeze()),count


def test_all_108_records_counted_with_six_closed_sessions(tmp_path):
    r,count=run(tmp_path)
    assert r['formal_rf_passed'] and len(r['records'])==108 and count['closes']==6
    assert r['known_correct']=={'normal':30,'narrowband':30,'pulse':30}
    assert r['human_acceptance']=='NOT_RUN' and r['offline_rejection']=='NOT_RUN'
    assert r['freeze_sha256'] and r['baseline_sha256']==freeze()['baseline_sha256']


def test_known_rejections_are_errors_even_with_complete_capture(tmp_path):
    class Rejected(Session):
        def save(self,tag,row,meta,side):
            if 'narrowband' in tag:
                row['environment']['label']=side['environment']['label']=side['display_environment']['label']='UNKNOWN'
            return super().save(tag,row,meta,side)
    r,count=run(tmp_path,Rejected)
    assert not r['formal_rf_passed'] and r['known_correct']['narrowband']==0
    assert len(r['records'])==108


@pytest.mark.parametrize('field', ['baseline','version','rf','scene','domain','submitted_revision',
    'handoff','display','sidecar','hash','duplicate_iq','old_calibration'])
def test_contract_or_artifact_tampering_stops_and_retains_frame(tmp_path,field):
    class Changed(Session):
        def save(self,tag,row,meta,side):
            if field=='duplicate_iq' and self.counter['frames']==1:
                return super().save(tag,row,meta,side)
            if field=='baseline':row['environment']['baseline_sha256']='c'*64;side['environment']=copy.deepcopy(row['environment'])
            elif field=='version':row['environment']['environment_version']='old';side['environment']=copy.deepcopy(row['environment'])
            elif field=='rf':side['rf_config']['lo_hz']+=1
            elif field=='scene':side['scenario_declaration']['tone_amplitude']=99
            elif field=='domain':meta['demod_result']['submitted']['config']['symbol_rate_hz']=2000000
            elif field=='submitted_revision':row['environment']['submitted_revision']=2;side['environment']=copy.deepcopy(row['environment'])
            elif field=='handoff':meta['handoff_frame']=dict(meta['handoff_frame'],config_revision=99)
            elif field=='display':side['display_environment']['label']='PULSE'
            elif field=='sidecar':side['environment']['connection_generation']=99
            elif field=='hash':(tmp_path/(tag+'.iq')).write_bytes(b'!')
            elif field=='duplicate_iq':
                raw=bytes([1])*16384;digest=hashlib.sha256(raw).hexdigest()
                (tmp_path/(tag+'.iq')).write_bytes(raw);row['iq_sha256']=meta['iq_sha256']=side['iq_sha256']=digest
            return super().save(tag,row,meta,side)
    declaration=freeze()
    if field=='old_calibration':declaration['calibration_iq_sha256']=[hashlib.sha256(bytes([1])*16384).hexdigest()]
    r,count=run(tmp_path,Changed,declaration)
    assert not r['formal_rf_passed'] and count['closes']==1
    assert len(r['records'])==(2 if field=='duplicate_iq' else 1)
    assert (tmp_path/'result.json').exists()


@pytest.mark.parametrize('failure',['unsafe','timeout','missing','malformed','close'])
def test_failure_preserves_result_and_respects_ownership(tmp_path,failure):
    count={'frames':0,'closes':0}
    class Failed(Session):
        def capture(self,tag,physical):
            if failure=='timeout':raise TimeoutError('capture')
            row=super().capture(tag,physical)
            if failure=='missing':row['environment']=None
            if failure=='malformed':row['environment']='broken environment'
            return row
        def cleanup(self):
            reply=super().cleanup()
            if failure=='close':reply['disarm_ack']=False
            return reply
    r=A.campaign(lambda:Failed(tmp_path,count,unsafe=failure=='unsafe'),tmp_path,'release',freeze())
    assert not r['formal_rf_passed'] and len(r['sessions'])==1
    assert count['closes']==(0 if failure=='unsafe' else 1)
    assert (tmp_path/'result.json').exists()


@pytest.mark.parametrize('field', ['environment_version','baseline_sha256','fixed_rf','software','scene_declarations','rx_config'])
def test_incomplete_freeze_refuses_before_creating_session(tmp_path,field):
    f=freeze();f.pop(field)
    def forbidden():raise AssertionError('must not construct hardware session')
    with pytest.raises(ValueError):A.campaign(forbidden,tmp_path,'release',f)


def test_the_acceptance_domain_comes_from_the_single_source():
    import rf_environment as ENV
    from rf_teaching_profile import teaching_capability
    assert A.FIXED_RF == dict(teaching_capability().rf_parameters)
    assert A.FIXED_RF['attenuation_mdB'] == 46000
    assert A.ENVIRONMENT_VERSION == ENV.VERSION


@pytest.mark.parametrize('attenuation',[40000,45999,46100])
def test_frozen_rf_outside_the_single_domain_refuses_before_session(attenuation):
    declaration=freeze();declaration['fixed_rf']['attenuation_mdB']=attenuation
    with pytest.raises(ValueError,match='domain/version mismatch'):
        A.validate_freeze(declaration,'release')


@pytest.mark.parametrize('version',['rf-window-environment-v2','rf-window-environment-v3','rf-window-environment-v999'])
def test_wrong_frozen_environment_version_refuses_before_session(tmp_path,version):
    declaration=freeze();declaration['environment_version']=version
    with pytest.raises(ValueError,match='domain/version mismatch'):
        A.validate_freeze(declaration,'release')


@pytest.mark.parametrize('field',['negative_label','missing_reason'])
def test_complete_rejection_label_and_reason_required(tmp_path,field):
    class Changed(Session):
        def save(self,tag,row,meta,side):
            if field=='missing_reason':
                row['environment'].pop('reason');side['environment'].pop('reason')
                side['display_environment'].pop('reason')
            elif 'compound' in tag:
                row['environment']['label']=side['environment']['label']=side['display_environment']['label']='NARROWBAND'
            return super().save(tag,row,meta,side)
    r,count=run(tmp_path,Changed)
    assert not r['formal_rf_passed'] and count['closes']>=1
    assert len(r['records'])==(1 if field=='missing_reason' else 91)


def test_validate_frame_public_api_replays_real_artifacts(tmp_path):
    counter={'frames':0,'closes':0};session=Session(tmp_path,counter)
    row=session.capture('independent-normal',True);row['tag']='independent-normal'
    identity,digest=A.validate_frame(row,tmp_path,freeze(),'normal',1)
    assert identity==tuple(row['environment'][key] for key in A.IDENTITY)
    assert digest==row['iq_sha256']
    with pytest.raises(ValueError):A.validate_frame(row,tmp_path,freeze(),'normal',37)
    row['tag']='../escaped'
    with pytest.raises(ValueError):A.validate_frame(row,tmp_path,freeze(),'normal',1)
