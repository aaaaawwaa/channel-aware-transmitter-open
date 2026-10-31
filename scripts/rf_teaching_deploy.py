"""Prepare and install a verified S1 candidate with explicit human evidence.

prepare is entirely offline; install never arms or transmits. No retry after a
transport failure: retain UNKNOWN state for a separate read-only inspection.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sys
import tarfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import merged_release as R
from scripts.rf_teaching_acceptance import candidate_manifest
from scripts.rf_teaching_install_state import verified_state

PARENT='993ffe21199a311eabec143a50e7e8a2'
CHECKS=('edit_clear_invalidates','expires_after_31s','cancel_no_capture','stop_disarm_safe','off_no_ber')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect_archive(archive,expected):
    if Path(archive).stat().st_size>64*1024*1024:raise ValueError('oversized archive')
    seen=set()
    with tarfile.open(archive,'r:gz') as stream:
        for member in stream:
            if member.name in seen or member.name not in expected or not member.isfile():
                raise ValueError('unexpected/duplicate/nonregular archive member')
            seen.add(member.name);info=expected[member.name]
            if member.size!=info['bytes']:raise ValueError('archive size mismatch')
            payload=stream.extractfile(member).read(member.size+1)
            if hashlib.sha256(payload).hexdigest()!=info['sha256']:raise ValueError('archive hash mismatch')
    if seen!=set(expected):raise ValueError('missing archive member')


def prepare(candidate,output,expected_release):
    candidate=Path(candidate).resolve();output=Path(output).resolve()
    manifest=candidate_manifest(candidate,expected_release)
    R.verify_panel_imports(candidate)
    members={}
    for item in sorted(candidate.iterdir()):
        if item.is_symlink() or not item.is_file():raise ValueError('nonregular candidate member')
        members['release/'+item.name]=item
    for name,relative in R.software_sources('cnn-bpsk-teaching-v1').items():
        members[relative.replace('\\','/')]=candidate/name
    output.mkdir(parents=True,exist_ok=False)
    archive=output/'candidate.tgz'
    with tarfile.open(archive,'w:gz') as bundle:
        for name,path in sorted(members.items()):bundle.add(path,arcname=name,recursive=False)
    expected={name:dict(sha256=sha(path),bytes=path.stat().st_size) for name,path in members.items()}
    inspect_archive(archive,expected)
    for source,target in (('rf_teaching_install.sh','install.sh'),
                          ('rf_teaching_install_state.py','install_state.py')):
        (output/target).write_bytes((ROOT/'scripts'/source).read_bytes())
    receipt=dict(directory=str(output),archive=str(archive),release_id=manifest['release_id'],
        candidate=str(candidate),parent_release=PARENT,installed=False,contract=manifest['software_contract'],
        members=expected,payload_sha256={name:sha(output/name) for name in (
            'candidate.tgz','install.sh','install_state.py')},
        scope='offline immutable candidate packaging; no board action')
    with (output/'bundle-receipt.json').open('x',encoding='utf-8') as f:json.dump(receipt,f,indent=2)
    return receipt


def verify_bundle(directory):
    directory=Path(directory).resolve()
    receipt=json.loads((directory/'bundle-receipt.json').read_text(encoding='utf-8'))
    candidate=Path(receipt['candidate'])
    manifest=candidate_manifest(candidate,receipt['release_id'])
    if receipt['parent_release']!=PARENT:raise ValueError('unexpected rollback parent')
    expected={'release/'+p.name:dict(sha256=sha(p),bytes=p.stat().st_size)
        for p in candidate.iterdir() if p.is_file() and not p.is_symlink()}
    for name,relative in R.software_sources('cnn-bpsk-teaching-v1').items():
        p=candidate/name;expected[relative.replace('\\','/')]=dict(sha256=sha(p),bytes=p.stat().st_size)
    if receipt['members']!=expected:raise ValueError('bundle/candidate content mismatch')
    for name in ('candidate.tgz','install.sh','install_state.py'):
        if sha(directory/name)!=receipt['payload_sha256'][name]:raise ValueError('bundle payload changed')
    for source,target in (('rf_teaching_install.sh','install.sh'),('rf_teaching_install_state.py','install_state.py')):
        if sha(ROOT/'scripts'/source)!=sha(directory/target):raise ValueError('unapproved installation tool')
    inspect_archive(directory/'candidate.tgz',expected)
    receipt.update(directory=str(directory),archive=str(directory/'candidate.tgz'))
    return receipt


def verify_human(path):
    human=json.loads(Path(path).read_text(encoding='utf-8'))
    if (human.get('source')!='human reply in this chat' or not human.get('answer_verbatim')
            or human.get('cable_confirmed') is not True
            or any(human.get('checks',{}).get(key) is not True for key in CHECKS)):
        raise ValueError('S0 human confirmation and cable evidence required')
    return human


def load_helper():
    path=Path.home()/'.claude/skills/ac820-pynq/scripts/pynq_ssh.py'
    spec=importlib.util.spec_from_file_location('boardhelper',path)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    return helper


def _run(client,helper,command,log,timeout=240):
    stdin,stdout,stderr=client.exec_command(command,timeout=timeout)
    stdin.write(helper.PASS+'\n');stdin.flush()
    channel=stdout.channel;channel.settimeout(timeout)
    start=time.monotonic();parts=[]
    while not channel.exit_status_ready():
        while channel.recv_ready():parts.append(channel.recv(8192).decode('utf-8','replace'))
        if time.monotonic()-start>timeout:raise TimeoutError('remote install deadline; state unknown')
        # Preserve partial output if the connection fails or deadline expires.
        log.write_text(''.join(parts),encoding='utf-8')
        time.sleep(.25)
    while channel.recv_ready():parts.append(channel.recv(8192).decode('utf-8','replace'))
    parts.append(stderr.read().decode('utf-8','replace'))
    log.write_text(''.join(parts),encoding='utf-8')
    return channel.recv_exit_status()


def install(bundle,human,output):
    bundle=Path(bundle).resolve();output=Path(output).resolve()
    payload=verify_bundle(bundle);verify_human(human)
    output.mkdir(parents=True,exist_ok=False)
    remote='/tmp/rf-teaching-install-'+uuid.uuid4().hex
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),release_id=payload['release_id'],
        parent_release=PARENT,remote=remote,payload_sha256=payload['payload_sha256'],
        human_receipt_sha256=sha(human),installed=False,board_state='UNKNOWN',
        policy='no automatic retry; no arm/TX/second Overlay')
    client=None
    try:
        helper=load_helper();client=helper.connect()
        with client.open_sftp() as s:
            s.mkdir(remote)
            for name in ('candidate.tgz','install.sh','install_state.py'):s.put(str(bundle/name),remote+'/'+name)
        args=[remote+'/install.sh',remote,payload['release_id'],PARENT,payload['payload_sha256']['candidate.tgz']]
        cmd='sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr sh '+' '.join(shlex.quote(x) for x in args)
        rc=_run(client,helper,cmd,output/'install.log')
        result['exit_code']=rc
        with client.open_sftp() as s:
            for name in ('pre-state.json','post-state.json','rollback-state.json'):
                try:s.get(remote+'/'+name,str(output/name))
                except OSError:pass
        post=output/'post-state.json'
        if rc==0 and post.exists():
            state=json.loads(post.read_text(encoding='utf-8'))
            result['installed']=verified_state(state,payload['release_id'])
            if result['installed']:result['board_state']='VERIFIED_SAFE_NEW_RELEASE'
        rollback=output/'rollback-state.json'
        if not result['installed'] and rollback.exists():
            state=json.loads(rollback.read_text(encoding='utf-8'))
            if verified_state(state,PARENT):
                result['board_state']='VERIFIED_SAFE_ROLLBACK'
    except Exception as exc:
        result.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if client is not None:client.close()
        result['evidence_sha256']={p.name:sha(p) for p in output.iterdir() if p.is_file()}
        with (output/'install-receipt.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--candidate',required=True,type=Path)
    prep.add_argument('--release',required=True);prep.add_argument('--output',required=True,type=Path)
    live=sub.add_parser('install');live.add_argument('--bundle',required=True,type=Path)
    live.add_argument('--s0-receipt',required=True,type=Path);live.add_argument('--output',required=True,type=Path)
    a=p.parse_args(argv)
    result=prepare(a.candidate,a.output,a.release) if a.action=='prepare' else install(a.bundle,a.s0_receipt,a.output)
    print(json.dumps({k:v for k,v in result.items() if k!='members'},indent=2),flush=True)
    return 0 if a.action=='prepare' or result['installed'] else 1


if __name__=='__main__':raise SystemExit(main())
