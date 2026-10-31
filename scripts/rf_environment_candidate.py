"""Build an S2 software candidate while truthfully reusing verified PL/daemon/CNN."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import merged_release as R
from scripts.package_merged_overlay import package_release

PARENT='c3bf915c544563324778d1c879b38e3a'
HEADROOM_PARENT='b67a8aaab0c5904822580c0647b96fd0'
ENVIRONMENT_PARENT='03b9b883a61f29605be2c1f3817024d0'
INSTALLED_ENVIRONMENT_PARENT='3f84f6a189e833b6986f4c819ff89be0'
MODREC4_PARENT='ad37bad4f6b0a6e3ba707a95be7fc196'
CONTRACT='cnn-bpsk-environment-v1'
TEACHING_CONTRACT='cnn-bpsk-teaching-v1'
HEADROOM_CONTRACT='cnn-bpsk-headroom-v1'
MODREC4_CONTRACT='cnn-bpsk-modrec4-v1'
# Files intentionally changed for the additive modrec4 contract.  Every other
# inherited environment member is pinned to the installed parent's bytes so the
# six-class model/contract, RF 46 dB and environment baseline cannot drift.
MODREC4_MUTABLE={'rf_status_worker.py','merged_release.py','install.sh'}

# The parents a candidate may be built ON TOP OF.  Membership is explicit and by
# release id, each row naming the contract the parent must itself declare and why it
# is on the list: "any release the verifier accepts" is how a candidate ends up built
# on a tree nobody qualified, so there is deliberately no wildcard or id-only
# shortcut here.  The refusal below names the whole accepted set, and adding a parent
# is a reviewable change to this table.
PARENTS={
    PARENT:dict(contract=TEACHING_CONTRACT,role='s1_teaching',
        reason='verified S1 teaching release; the original environment parent'),
    HEADROOM_PARENT:dict(contract=HEADROOM_CONTRACT,role='headroom',
        reason='verified HEADROOM-1 research release (HEADROOM contract only)'),
    ENVIRONMENT_PARENT:dict(contract=CONTRACT,role='installed_environment',
        reason='verified S2 environment release the board runs today '
               '(current = active-release = 03b9b883..., 2026-10-19 read-only readback)'),
    INSTALLED_ENVIRONMENT_PARENT:dict(contract=CONTRACT,role='installed_environment_current',
        reason='currently installed environment release 3f84f6a1...; only the additive '
               'cnn-bpsk-modrec4-v1 extension may be built on it'),
    MODREC4_PARENT:dict(contract=MODREC4_CONTRACT,role='installed_modrec4_current',
        reason='currently installed ad37bad4... cnn-bpsk-modrec4-v1 release; only a '
               'same-contract worker repair may be built on it'),
}


def accepted_parent(manifest, contract):
    """The named parent row this manifest matches, or a refusal naming the set.

    The contract check is what keeps an id-only match from being enough: a tree that
    merely reuses a release id is refused because its own manifest disagrees with the
    row.  The two role rules preserve the existing direction of every route: the
    HEADROOM parent stays HEADROOM-only, and the installed environment release may
    only be revised with the environment contract."""
    entry=PARENTS.get(manifest['release_id'])
    if entry is None or manifest.get('software_contract')!=entry['contract']:
        raise ValueError('verified parent from the named set required (%s)'
            % ', '.join('%s=%s' % (k,v['role']) for k,v in sorted(PARENTS.items())))
    if entry['role']=='headroom' and contract!=HEADROOM_CONTRACT:
        raise ValueError('the HEADROOM parent may only build the HEADROOM contract')
    if entry['role']=='installed_environment' and contract!=CONTRACT:
        raise ValueError('the installed environment parent may only build the environment contract')
    if entry['role']=='installed_environment_current' and contract!=MODREC4_CONTRACT:
        raise ValueError('the current installed parent may only build cnn-bpsk-modrec4-v1')
    if entry['role']=='installed_modrec4_current' and contract!=MODREC4_CONTRACT:
        raise ValueError('the installed modrec4 parent may only build cnn-bpsk-modrec4-v1')
    return entry


def reuse_parent_software(release, parent, software):
    """Re-pin a packaged release onto the parent's attested software bytes.

    package_release copies the carried software out of THIS working tree, so for a
    same-contract parent it can ship different bytes than the parent does -- measured
    2026-10-19: the tree baseline is the n=1 bootstrap while the board runs the audited
    n=20 one.  This replaces exactly the members whose digests differ with the parent's
    bytes, re-pins the receipt/manifest software map, recomputes the release id and
    re-verifies the result; the release's own verifier is what makes the replacement a
    checked operation rather than a claim."""
    receipt_path=release/'merged_build_receipt.json'
    manifest_path=release/'merged_release_manifest.json'
    for name in sorted(software):
        target=release/name
        parent_path=parent/name
        if not parent_path.is_file():
            # Additive contract members (e.g. modrec4 adapter/runtime) have no
            # parent bytes; they were packaged from this tree and stay as packaged.
            continue
        if R.sha256_file(target)==software[name]:
            continue
        target.chmod(0o666)
        shutil.copyfile(parent_path,target)
        target.chmod(0o444)
    receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt['software']=software
    receipt_path.chmod(0o666)
    receipt_path.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['software']=software
    release_id=R.release_identity(manifest['build_id'],manifest['bit_sha256'],
        manifest['hwh_sha256'],manifest['daemon_version'],manifest['protocol_version'],
        manifest['daemon_sha256'],software,software_contract=manifest.get(R.SOFTWARE_CONTRACT_KEY),
        model_artifact=manifest.get(R.MODEL_KEY),
        modrec4_artifact=manifest.get(R.MODREC4_MODEL_KEY))
    manifest['release_id']=release_id
    manifest_path.chmod(0o666)
    manifest_path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    release.chmod(0o777)
    renamed=release.parent/release_id
    if renamed!=release:
        if renamed.exists():
            raise FileExistsError(str(renamed))
        release.rename(renamed)
    for item in renamed.iterdir():
        item.chmod(0o444)
    renamed.chmod(0o555)
    return renamed


def verify_observer_build(binary, evidence, board_app=None):
    """Verify the native artifact and every input of the recorded isolated build."""
    from scripts.rf_build_daemon import SOURCE_GLOBS, SUBDIRS
    binary,evidence=Path(binary),Path(evidence)
    board_app=Path(board_app or ROOT/'rf_overlay/sw/board_app')
    log=binary.with_suffix('.build.log')
    for path,limit in ((binary,16*1024*1024),(evidence,262144),(log,8*1024*1024)):
        if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=limit:
            raise ValueError('bounded native artifact and build evidence required')
    raw=binary.read_bytes();receipt=json.loads(evidence.read_text(encoding='utf-8'))
    if raw[:6]!=b'\x7fELF\x01\x01' or len(raw)<52 or int.from_bytes(raw[18:20],'little')!=40:
        raise ValueError('32-bit little-endian ARM ELF required')
    targets={'all','host-test','host-test-core','host-test-iq','host-test-protocol','vendor-integrity'}
    if (receipt.get('architecture')!='armv7l' or receipt.get('hardware_touched') is not False
        or receipt.get('sha256')!=hashlib.sha256(raw).hexdigest()
        or set(receipt.get('targets') or [])!=targets):
        raise ValueError('passing complete native build required')
    paths={board_app/'Makefile'}
    for pattern in SOURCE_GLOBS:paths.update(board_app.glob(pattern))
    for sub in SUBDIRS:
        paths.update(p for p in (board_app/sub).iterdir()
            if p.is_file() and p.suffix in ('.c','.h','.py',''))
    if any(p.is_symlink() or not p.is_file() for p in paths):
        raise ValueError('regular native build sources required')
    sources={p.relative_to(board_app).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    recorded=receipt.get('source_sha256')
    if not isinstance(recorded,dict):raise ValueError('native source digest map required')
    normalized={k.replace('\\','/'):v for k,v in recorded.items() if isinstance(k,str)}
    if len(normalized)!=len(recorded) or normalized!=sources:
        raise ValueError('native artifact source changed or incomplete')
    lines=log.read_text(encoding='utf-8').splitlines()
    if 'BUILD_RC=0' not in lines or 'vendor integrity: 10 files passed' not in lines:
        raise ValueError('native build and vendor tests did not pass')
    version=(board_app/'rf_version.h').read_text(encoding='utf-8')
    match=re.search(r'^#define RF_DAEMON_VERSION "([0-9.]+)"$',version,re.M)
    daemon=match.group(1) if match else None
    # The daemon version and the protocol minor move together, so this table is
    # the one place that says which pair a build receipt is allowed to claim.
    # 1.0.4/minor 6 is the HEADROOM-1 research contract; the older pairs stay
    # listed because their candidates remain verifiable.
    supported={'1.0.1':3,'1.0.2':4,'1.0.3':5,'1.0.4':6}
    if daemon not in supported:raise ValueError('explicitly supported observer daemon version required')
    minor=supported[daemon]
    if (not re.search(r'^#define RF_PROTOCOL_MAJOR 1u$',version,re.M)
            or not re.search(r'^#define RF_PROTOCOL_MINOR '+str(minor)+r'u$',version,re.M)):
        raise ValueError('observer daemon and major/minor pairing required')
    return dict(daemon_version=daemon,protocol_major=1,protocol_minor=minor,daemon_sha256=receipt['sha256'],
        build_receipt_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        build_log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),source_sha256=sources,
        scope='isolated native build and fake tests; no hardware qualification')


def modrec4_artifact():
    """Declare the three frozen 4-class model files from the product source tree."""
    import json as _json
    source=ROOT/'pynq_overlay/sw/modrec4_artifacts'
    files={'cnn':'modrec4_cnn_model.json','baseline':'modrec4_baseline_model.json',
           'rule':'modrec4_rule_config.json'}
    artifact={'schema_version':1,'status':'experimental'}
    for stem,name in files.items():
        path=source/name
        if not path.is_file():
            raise ValueError('modrec4 artifact source missing: '+str(path))
        artifact[stem+'_file']=name
        artifact[stem+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    return artifact


def build_candidate(parent, output, daemon=None, daemon_build=None, contract=CONTRACT,
                    carry_parent_software=False):
    parent=Path(parent).resolve();output=Path(output).resolve()
    manifest=R.verify_release(parent,require_software=True)
    entry=accepted_parent(manifest,contract)
    parent_id=manifest['release_id']
    if (daemon is None)!=(daemon_build is None):
        raise ValueError('daemon and exact build evidence must be supplied together')
    observer=verify_observer_build(daemon,daemon_build) if daemon is not None else None
    if contract not in (CONTRACT,HEADROOM_CONTRACT,MODREC4_CONTRACT):
        raise ValueError('explicit environment, HEADROOM or modrec4 contract required')
    if contract==MODREC4_CONTRACT and entry['role'] not in ('installed_environment_current',
                                                            'installed_modrec4_current'):
        raise ValueError('modrec4 must be built on the current installed environment or modrec4 parent')
    if contract==HEADROOM_CONTRACT and (observer is None or
            (observer['daemon_version'],observer['protocol_minor'])!=('1.0.4',6)):
        raise ValueError('HEADROOM requires freshly built 1.0.4/minor6 native artifact')
    native_reused=bool(observer and observer['daemon_sha256']==manifest.get('daemon_sha256'))
    if entry['role']=='headroom' and not native_reused:
        raise ValueError('HEADROOM revision must reuse the qualified parent native artifact')
    # A same-contract parent is a REVISION of what the parent already carries, so the
    # parent's software map is the qualified set and this tree's copy of it may be stale
    # (the baseline file is: the tree holds the n=1 bootstrap, the board runs the audited
    # n=20 one).  Re-digesting the tree would silently replace a carried member, so that
    # difference has to be declared and the parent's own bytes are what gets packaged.
    software=R.software_digests(ROOT,contract)
    final_software=software
    carried=[]
    if entry['role'] in ('installed_environment','installed_environment_current',
                          'installed_modrec4_current'):
        recorded=manifest.get('software') or {}
        if not set(recorded) <= set(software):
            raise ValueError('parent software map does not cover the carried contract')
        carried=sorted(name for name,digest in software.items()
                       if name in recorded and recorded[name]!=digest
                       and not (contract==MODREC4_CONTRACT and name in MODREC4_MUTABLE))
        if carried and not carry_parent_software:
            raise ValueError('the parent carries different software bytes than this tree '
                '(%s); pass carry-parent-software to reuse the parent\'s attested members'
                % ', '.join(carried))
        if carried or contract==MODREC4_CONTRACT:
            final_software=dict(software)
            for name,digest in recorded.items():
                if contract==MODREC4_CONTRACT and name in MODREC4_MUTABLE:
                    continue
                final_software[name]=digest
    if (not set(manifest.get('software') or {})) and contract==MODREC4_CONTRACT:
        raise ValueError('modrec4 parent must carry the environment software map')
    daemon_rebuilt=observer is not None and not native_reused
    if output.exists():raise FileExistsError(str(output))
    output.mkdir(parents=True)
    hardware=output/'hardware';hardware.mkdir()
    receipt=json.loads((parent/'merged_build_receipt.json').read_text(encoding='utf-8'))
    for kind,source,name in (('bit',R.BIT_NAME,'host_bd_wrapper.bit'),('hwh',R.HWH_NAME,'host_bd.hwh')):
        path=hardware/name;shutil.copyfile(parent/source,path)
        receipt[kind+'_source']=str(path)
    receipt[R.SOFTWARE_CONTRACT_KEY]=contract
    receipt['software']=software
    for name in (manifest[R.MODEL_KEY]['model_file'],manifest[R.MODEL_KEY]['evaluation_file']):
        shutil.copyfile(parent/name,output/name)
    receipt[R.MODEL_KEY]=manifest[R.MODEL_KEY]
    modrec4=None
    if contract==MODREC4_CONTRACT:
        modrec4=modrec4_artifact()
        for name in (modrec4['cnn_file'],modrec4['baseline_file'],modrec4['rule_file']):
            shutil.copyfile(ROOT/'pynq_overlay/sw/modrec4_artifacts'/name,output/name)
        receipt[R.MODREC4_MODEL_KEY]=modrec4
    receipt['software_rebuild']=dict(parent_release_id=parent_id,hardware_rebuilt=False,
        daemon_rebuilt=daemon_rebuilt,native_artifact_reused=native_reused,model_retrained=False,
        scope='S2 software + frozen development calibration; physical qualification pending')
    if observer:receipt['rx_gain_observer_build']=observer
    path=output/'merged_build_receipt.json'
    path.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    release=package_release(path,output,observer['daemon_version'] if observer else manifest['daemon_version'],
        manifest['protocol_version'],Path(daemon) if observer else parent/R.DAEMON_NAME)
    if carried:
        release=reuse_parent_software(release,parent,final_software)
    checked=R.verify_release(release,require_software=True)
    R.verify_panel_imports(release)
    result=dict(release_id=checked['release_id'],candidate=str(release),parent_release=parent_id,
        parent_role=entry['role'],carried_software_from_parent=carried,
        installed=False,software_contract=contract,hardware_rebuilt=False,daemon_rebuilt=daemon_rebuilt,
        native_artifact_reused=native_reused,
        model_retrained=False,physical_qualification='NOT_RUN',human_acceptance='NOT_RUN',
        software=checked['software'],modrec4_model=checked.get(R.MODREC4_MODEL_KEY),
        modrec4_readback_scope='additive; six-class/environment/RF 46 dB unchanged')
    if observer:result.update(daemon_version=observer['daemon_version'],protocol_major=1,protocol_minor=observer['protocol_minor'])
    (output/'candidate-receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--daemon',type=Path);p.add_argument('--daemon-build',type=Path)
    p.add_argument('--contract',choices=(CONTRACT,'cnn-bpsk-headroom-v1',MODREC4_CONTRACT),default=CONTRACT)
    p.add_argument('--carry-parent-software',action='store_true',
        help='reuse the installed environment parent\'s attested software bytes; required '
             'when this working tree carries different bytes for any carried member')
    a=p.parse_args(argv)
    result=build_candidate(a.parent,a.output,a.daemon,a.daemon_build,a.contract,
        a.carry_parent_software)
    print(json.dumps({k:v for k,v in result.items() if k!='software'},indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())

