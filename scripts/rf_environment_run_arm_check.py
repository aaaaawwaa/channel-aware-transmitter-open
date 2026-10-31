"""Transfer a verified candidate to /tmp; leave installed board software untouched."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import uuid
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import merged_release as R
from scripts.rf_teaching_deploy import load_helper, _run
from scripts.rf_teaching_verify import compare


def run(candidate,pilot,output):
    candidate=Path(candidate).resolve();pilot=Path(pilot).resolve();output=Path(output).resolve()
    manifest=R.verify_release(candidate,require_software=True)
    if manifest['software_contract']!='cnn-bpsk-environment-v1':raise ValueError('S2 contract required')
    output.mkdir(parents=True,exist_ok=False);inputs=output/'inputs';inputs.mkdir()
    source=pilot/'s02-on-00.iq';raw=source.read_bytes()
    original=json.loads(source.with_suffix('.json').read_text(encoding='utf-8'))
    if len(raw)!=16384 or hashlib.sha256(raw).hexdigest()!=original['iq_sha256']:
        raise ValueError('retained S1 source hash/size mismatch')
    samples=np.frombuffer(raw,'<i2').reshape(-1,2)
    z=samples[:,0].astype(float)+1j*samples[:,1].astype(float)
    gain=np.median(np.abs(z))/60;n=np.arange(4096)
    tone=np.exp(2j*np.pi*.1*n);pulse=np.zeros(4096,complex)
    pulse[600:680]=pulse[3140:3220]=1
    declarations=[]
    for kind,j in (('normal',0),('narrowband',40*gain*tone),('pulse',40*gain*pulse),
                    ('compound',20*gain*(tone+pulse))):
        values=np.rint(np.column_stack(((z+j).real,(z+j).imag)))
        if np.any(values<-2048) or np.any(values>2047):raise ValueError('derived overflow')
        payload=values.astype('<i2').tobytes();meta=copy.deepcopy(original)
        meta.update(capture_id=original['capture_id']+'-offline-'+kind,
                    iq_sha256=hashlib.sha256(payload).hexdigest())
        (inputs/(kind+'.iq')).write_bytes(payload)
        (inputs/(kind+'.json')).write_text(json.dumps(meta),encoding='utf-8')
        declarations.append(dict(tag=kind,parent_iq_sha256=original['iq_sha256'],split='val',
            origin='retained S1' if kind=='normal' else 'derived from retained S1 / not physical',
            iq_sha256=meta['iq_sha256']))
    runner=ROOT/'scripts/rf_environment_arm_check.py'
    host_args=[sys.executable,'-B',str(runner),'--release-dir',str(candidate),'--iq-dir',str(inputs),
               '--output',str(output/'host.json'),'--iterations','0']
    host=subprocess.run(host_args,capture_output=True,text=True,timeout=30)
    (output/'host.log').write_text(host.stdout+host.stderr,encoding='utf-8')
    if host.returncode:raise RuntimeError('host isolated check failed: '+host.stderr)
    helper=load_helper();client=None
    remote='/tmp/rf-environment-offline-'+uuid.uuid4().hex
    receipt=dict(release_id=manifest['release_id'],candidate=str(candidate),remote=remote,
        scope='isolated offline candidate on host/ARM; installed board software untouched; zero RF captures',
        input_declarations=declarations,runner_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
        software=manifest['software'],passed=False,formal_physical='NOT_RUN',human_acceptance='NOT_RUN')
    try:
        client=helper.connect()
        with client.open_sftp() as s:
            s.mkdir(remote);s.mkdir(remote+'/release');s.mkdir(remote+'/inputs')
            s.put(str(runner),remote+'/runner.py')
            for p in sorted(candidate.iterdir()):
                if p.is_symlink() or not p.is_file():raise ValueError('candidate must be flat regular files')
                s.put(str(p),remote+'/release/'+p.name)
            for p in sorted(inputs.iterdir()):s.put(str(p),remote+'/inputs/'+p.name)
        command='python3 -B '+shlex.quote(remote+'/runner.py')+' --release-dir '+remote+'/release --iq-dir '+remote+'/inputs --output '+remote+'/arm.json'
        rc=_run(client,helper,command,output/'arm.log',timeout=120)
        receipt['exit_code']=rc
        with client.open_sftp() as s:s.get(remote+'/arm.json',str(output/'arm.json'))
        arm=json.loads((output/'arm.json').read_text(encoding='utf-8'))
        expected=json.loads((output/'host.json').read_text(encoding='utf-8'))
        mismatches=compare(expected['records'],arm['records'],'records')
        receipt.update(parity_mismatches=mismatches,p95_ms=arm.get('p95_ms'),
            rss_kib=arm.get('rss_kib'),native_cnn=arm.get('native_cnn'),
            passed=bool(rc==0 and not mismatches and arm['release_id']==manifest['release_id']
                and arm['software']==manifest['software'] and arm.get('native_cnn') is True
                and arm.get('iterations')==100 and arm.get('p95_ms')<=300))
    except Exception as exc:
        receipt.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if client is not None:client.close()
        receipt['evidence_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir() if p.is_file()}
        (output/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate',required=True,type=Path);p.add_argument('--pilot',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    result=run(a.candidate,a.pilot,a.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('software','input_declarations')},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
