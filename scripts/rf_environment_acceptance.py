"""Finite 90 known + 18 negative physical TEST orchestration, no offline/human claim.

Truth schedules the controlled experiment but is never input to diagnosis.
Every returned frame, including rejected/unsafe frames, stays in the receipt.
"""
import hashlib
import json
import math
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'pynq_overlay' / 'sw') not in sys.path:
    sys.path.insert(0, str(ROOT / 'pynq_overlay' / 'sw'))
from rf_environment import VERSION as ENVIRONMENT_VERSION
from rf_teaching_profile import teaching_capability

IDENTITY = ('capture_id','release_id','connection_generation','config_revision','completed_at_monotonic')
EXPECTED = {'normal':'CLEAR','narrowband':'NARROWBAND','pulse':'PULSE'}
# One domain source: the acceptance declaration must equal the teaching profile's
# attenuation and the environment domain version, never a private literal.
FIXED_RF = dict(teaching_capability().rf_parameters)
FIXED_RX = dict(expected_mode='BPSK',symrate_hz=3000000,shaping='RECT',decim=1,
    rx_len=4096,input_fs_hz=60000000,reference_enabled=True)
FRAME_FIELDS = IDENTITY + ('samples','bytes','sample_rate_hz','ratio','decimation',
    'output_fs_hz','op','physical','request_id','daemon_timestamp_ns','pre_io_ok',
    'post_io_ok','post_rx_armed','post_tx_source_selected')


def _sha(value):
    return (type(value) is str and len(value)==64
            and all(c in '0123456789abcdef' for c in value))


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def validate_freeze(freeze,release):
    """Snapshot the complete declaration before constructing any hardware client."""
    if type(freeze) is not dict:
        raise ValueError('complete frozen TEST declaration required')
    required={'expected_release','environment_version','baseline_sha256','algorithm_version',
        'fixed_rf','software','scene_declarations','rx_config','reference_seeds',
        'actual_scene_prbs7_seed','split','expected_frames'}
    if not required.issubset(freeze):
        raise ValueError('incomplete frozen TEST declaration')
    value=copy.deepcopy(freeze)
    encoded=_canonical(value)
    if len(encoded)>131072:
        raise ValueError('frozen declaration too large')
    if (value['expected_release']!=release or value['split']!='test'
            or type(value['expected_frames']) is not int or value['expected_frames']!=108
            or value['reference_seeds']!=[1,37,73] or value['actual_scene_prbs7_seed']!=1
            or value['algorithm_version']!='bpsk-rect-snapshot-v2'
            or value['environment_version']!=ENVIRONMENT_VERSION
            or not _sha(value['baseline_sha256'])
            or value['fixed_rf']!=FIXED_RF or value['rx_config']!=FIXED_RX):
        raise ValueError('frozen TEST domain/version mismatch')
    software=value['software']
    names={'rf_environment.py','rf_environment_baseline.json','rf_experiment_scenarios.py',
        'rf_status_worker.py','ui_model.py','ui_view.py'}
    if (type(software) is not dict or not names.issubset(software)
            or any(type(k) is not str or Path(k).name!=k or not _sha(v)
                   for k,v in software.items())):
        raise ValueError('complete software SHA mapping required')
    scenes=value['scene_declarations']
    if type(scenes) is not dict or set(scenes)!={'normal','narrowband','pulse','compound'}:
        raise ValueError('all controlled scene declarations required')
    for kind,scene in scenes.items():
        if (type(scene) is not dict or scene.get('truth')!=kind or scene.get('prbs7_seed')!=1
                or scene.get('version')!='rf-controlled-scenarios-v1'
                or scene.get('formula')!='x=A*s+j' or scene.get('physical_max_code')!=100
                or scene.get('sample_rate_hz')!=60000000 or scene.get('symbol_rate_hz')!=3000000
                or scene.get('shaping')!='RECT' or scene.get('period_samples')!=2540
                or scene.get('amplitude')!=60):
            raise ValueError('frozen controlled scene outside fixed safe domain')
        codes=[scene.get(k) for k in ('amplitude','tone_amplitude','pulse_amplitude')]
        if any(type(v) is not int or not 0<=v<=100 for v in codes) or sum(codes)>100:
            raise ValueError('frozen controlled scene peak bound exceeded')
    excluded=value.get('calibration_iq_sha256',[])
    if type(excluded) is not list or any(not _sha(v) for v in excluded):
        raise ValueError('invalid calibration/source IQ exclusion hashes')
    return value,hashlib.sha256(encoded).hexdigest()


def _read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>131072:
        raise ValueError('missing/unbounded/symbolic artifact: '+path.name)
    value=json.loads(path.read_text(encoding='utf-8'))
    if type(value) is not dict:
        raise ValueError('artifact must be an object: '+path.name)
    _canonical(value)  # Reject NaN/Infinity even though JSON's parser accepts them.
    return value


def _validate_frame(row,tag,kind,seed,output,release,freeze):
    env=row.get('environment')
    if type(env) is not dict:
        raise ValueError('missing environment result')
    identity=tuple(env.get(k) for k in IDENTITY)
    digest=row.get('iq_sha256')
    if (row.get('release_id')!=release or row.get('egress_safe') is not True
            or any(v is None for v in identity)
            or not _sha(digest) or digest in freeze.get('calibration_iq_sha256',[])
            or env.get('capture_id')!=row.get('capture_id') or env.get('release_id')!=release
            or type(identity[-1]) not in (int,float) or not math.isfinite(identity[-1])
            or type(env.get('reason')) is not str or not env['reason'] or len(env['reason'])>512
            or env.get('reason') in ('CLIPPED','INVALID_IQ')
            or env.get('environment_version')!=freeze['environment_version']
            or env.get('baseline_sha256')!=freeze['baseline_sha256']
            or env.get('algorithm_version')!=freeze['algorithm_version']
            or env.get('label') not in ('CLEAR','NARROWBAND','PULSE','UNKNOWN','INVALID')
            or (kind not in EXPECTED and env.get('label') not in ('UNKNOWN','INVALID'))):
        raise ValueError('unsafe, duplicate or foreign diagnostic identity')
    for field,value in zip(IDENTITY,identity):
        if field in row and row[field]!=value:
            raise ValueError('row identity mismatch')
    for key,suffix in (('iq_file','.iq'),('metadata_file','.json'),('environment_file','.environment.json')):
        if row.get(key)!=tag+suffix:
            raise ValueError('unexpected artifact name')
    iq_path=output/(tag+'.iq')
    if iq_path.is_symlink() or not iq_path.is_file() or iq_path.stat().st_size!=16384:
        raise ValueError('IQ artifact outside bounds')
    if hashlib.sha256(iq_path.read_bytes()).hexdigest()!=digest:
        raise ValueError('original IQ SHA mismatch')
    meta=_read_json(output/(tag+'.json'))
    side=_read_json(output/(tag+'.environment.json'))
    if (meta.get('iq_sha256')!=digest or tuple(meta.get(k) for k in IDENTITY)!=identity
            or side.get('iq_sha256')!=digest or side.get('capture_id')!=identity[0]
            or side.get('release_id')!=release or side.get('environment')!=env
            or side.get('rf_config')!=freeze['fixed_rf']):
        raise ValueError('metadata/sidecar identity, RF or diagnostic mismatch')
    handoff=meta.get('handoff_frame')
    if (type(handoff) is not dict or meta.get('handoff_frame_matches') is not True
            or any(k not in handoff or k not in meta or handoff[k]!=meta[k] for k in FRAME_FIELDS)):
        raise ValueError('actual handoff identity mismatch')
    physical=kind!='off'
    expected_frame=dict(samples=4096,bytes=16384,sample_rate_hz=60000000,ratio=0,
        decimation=1,output_fs_hz=60000000,physical=physical,
        op='loopback.physical.capture' if physical else 'rx.capture',pre_io_ok=True,
        post_io_ok=True,post_rx_armed=False,post_tx_source_selected=False)
    if any(meta.get(k)!=v for k,v in expected_frame.items()):
        raise ValueError('capture mode or acquisition/readback mismatch')
    status=meta.get('capture_status') or {}
    if (status.get('state')!='done' or status.get('close_confirmed') is not True
            or status.get('cancelled') is not False or status.get('physical') is not physical
            or status.get('capture_id')!=identity[0]):
        raise ValueError('capture status not successfully closed')
    display=side.get('display_environment') or {}
    if (display.get('available') is not True or display.get('current') is not True
            or any(display.get(k)!=env.get(k) for k in
                ('capture_id','label','reason','features','baseline_sha256'))
            or any(k in display and display[k]!=env[k] for k in IDENTITY)):
        raise ValueError('same-frame environment did not display')
    demod=meta.get('demod_result') or {}
    result=demod.get('result') or {};submitted=demod.get('submitted') or {}
    expected_submitted=dict(config=dict(mode='BPSK',
        symbol_rate_hz=2000000 if kind=='domain' else 3000000,shaping='RECT',
        reference_prbs7_seed=seed),
        decim=1,rx_len=4096,input_fs_hz=60000000)
    if (demod.get('algorithm_version')!=freeze['algorithm_version']
            or result.get('algorithm_version')!=freeze['algorithm_version']
            or tuple(result.get(k) for k in IDENTITY)!=identity
            or type(submitted.get('revision')) is not int or submitted['revision']<0
            or env.get('submitted_revision')!=submitted['revision']
            or any(submitted.get(k)!=v for k,v in expected_submitted.items())):
        raise ValueError('frozen RX/domain/BPSK submission mismatch')
    # OFF is ordinary RX while the preceding compound waveform remains loaded;
    # its declaration describes that load, not the receive diagnosis or TX-on truth.
    declared_kind='normal' if kind=='domain' else 'compound' if kind=='off' else kind
    if side.get('scenario_declaration')!=freeze['scene_declarations'][declared_kind]:
        raise ValueError('loaded scene differs from frozen declaration')
    return identity,digest


def validate_frame(row,output,freeze,kind,seed):
    """Independently validate one persisted frame against its frozen contract.

    Return the five-field identity and original IQ SHA; callers compare these
    across frames for duplicates. This function never constructs a board client.
    """
    try:
        if (type(row) is not dict or kind not in (*EXPECTED,'compound','off','domain')
                or type(seed) is not int or seed not in freeze['reference_seeds']):
            raise ValueError('unknown formal frame kind or reference seed')
        tag=row.get('tag')
        if (type(tag) is not str or not tag or len(tag)>128
                or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in tag)):
            raise ValueError('invalid bounded artifact tag')
        return _validate_frame(row,tag,kind,seed,Path(output),freeze['expected_release'],freeze)
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('malformed formal frame contract') from exc


def _environment_label(row):
    env=row.get('environment')
    return env.get('label') if type(env) is dict else None


def offline_rejection_result(value):
    """Validate the measured 12-item offline negative-control receipt (spec freeze 2.1).

    `None` keeps the historic 'NOT_RUN' marker: the offline set is a separate
    workstream, and a campaign that did not run it must say so rather than look
    like it passed.  A supplied receipt is recorded verbatim and must be the
    complete 12/12, zero-CLEAR result -- a failing offline set is refused here
    instead of being recorded as a pass."""
    if value is None:
        return 'NOT_RUN'
    if type(value) is not dict:
        raise ValueError('offline rejection must be the measured receipt or None')
    required=('cases','passed','failed','cleared')
    if any(type(value.get(k)) is not int for k in required):
        raise ValueError('offline rejection receipt requires integer counts')
    if (value['cases'],value['passed'],value['failed'],value['cleared'])!=(12,12,0,0):
        raise ValueError('offline rejection must be 12 of 12 with no CLEAR')
    return {k:value[k] for k in required}


def load_offline_rejection(path):
    """Read the measured N1-N12 receipt and return the counts the formal gate records.

    A formal TEST run must carry the measured offline set; a missing, unreadable,
    oversized or malformed receipt is refused here, before any session is constructed.
    Trust boundary: only the four measured counts are read; the receipt's own
    `passed`/`failed_ids`/`producer.file_sha256` are the producer's business."""
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>131072:
        raise ValueError('the offline rejection receipt is missing: '+str(path))
    try:
        document=json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError,ValueError) as exc:
        raise ValueError('the offline rejection receipt is not JSON: '+type(exc).__name__)
    counts=document.get('counts') if type(document) is dict else None
    if type(counts) is not dict:
        raise ValueError('the offline rejection receipt carries no counts object')
    return offline_rejection_result(counts)


def campaign(factory,output,release,freeze,offline_rejection=None):
    offline=offline_rejection_result(offline_rejection)
    freeze,freeze_sha256=validate_freeze(freeze,release)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    records,sessions,identities,hashes=[],[],set(),set()
    cause=None
    for index in range(6):
        session,owned,error=None,False,None
        closed=ack=False
        try:
            session=factory();initial=session.initial()
            if initial.get('safe') is not True or initial.get('release_id')!=release:
                cause='initial_not_safe_or_wrong_release'
            else:
                owned=True;session.setup((1,37,73)[index%3])
                kinds=('normal','narrowband','pulse') if index<3 else ('compound','off','domain')
                for kind in kinds:
                    if kind!='off':session.scene('normal' if kind=='domain' else kind)
                    if kind=='domain':session.domain(True)
                    for number in range(10 if index<3 else 2):
                        tag='s%02d-%s-%02d'%(index,kind,number)
                        row=session.capture(tag,kind!='off')
                        row.update(tag=tag,truth=kind,session_index=index,split='test',
                                   reference_seed=(1,37,73)[index%3],actual_scene_prbs7_seed=1)
                        records.append(row)
                        identity,digest=validate_frame(row,output,freeze,kind,(1,37,73)[index%3])
                        if identity in identities or digest in hashes:
                            raise ValueError('duplicate formal capture identity or IQ')
                        identities.add(identity);hashes.add(digest)
                    if kind=='domain':session.domain(False)
                    if cause is not None:break
        except Exception as exc:
            cause,error=type(exc).__name__,str(exc)
        finally:
            if owned:
                try:
                    reply=session.cleanup();closed=reply.get('safe') is True;ack=reply.get('disarm_ack') is True
                except Exception as exc:error=str(exc)
            if session is not None:
                try:session.close()
                except Exception:pass
            sessions.append(dict(index=index,owned=owned,closed=closed,disarm_ack=ack,cause=cause,error=error))
        if cause is not None:break
        if not closed or not ack:
            cause='close_unconfirmed';break
    correct={kind:sum(row['truth']==kind and _environment_label(row)==label
                      for row in records) for kind,label in EXPECTED.items()}
    totals={kind:sum(row['truth']==kind for row in records) for kind in EXPECTED}
    negatives=[row for row in records if row['truth'] not in EXPECTED]
    false_clear=sum(_environment_label(row)=='CLEAR' for row in negatives)
    normal_false_alarm=totals['normal']-correct['normal']
    passed=bool(cause is None and len(records)==108 and len(sessions)==6
        and all(row['closed'] and row['disarm_ack'] for row in sessions)
        and all(totals[k]==30 and correct[k]>=27 for k in EXPECTED)
        and normal_false_alarm<=3 and len(negatives)==18 and false_clear==0)
    result=dict(formal_rf_passed=passed,stop_cause=cause,expected_release=release,split='test',
        freeze_sha256=freeze_sha256,environment_version=freeze['environment_version'],
        baseline_sha256=freeze['baseline_sha256'],software=freeze['software'],
        records=records,sessions=sessions,known_correct=correct,known_total=totals,
        negative_false_clear=false_clear,normal_false_alarm=normal_false_alarm,
        offline_rejection=offline,human_acceptance='NOT_RUN',stage_passed=False)
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    return result
