"""Read-only installed-release/state check. Never loads an Overlay or arms RF."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


# spec 7 of docs/superpowers/specs/2026-10-13-headroom-board-run-package-design.md:
# the named terminals of the BOARD run package.  The checker is a declared member
# of the same run bundle as the runner, so the vocabulary lives in both members;
# each one is defined exactly once, as a constant whose value is its own name, and
# the runner collects the whole set in FIXED_TERMINAL_LABELS.  Adding them changes
# no behaviour of this read-only checker.
RUN_REFUSED_BUNDLE = 'RUN_REFUSED_BUNDLE'
RUN_REFUSED_IMPORT = 'RUN_REFUSED_IMPORT'
RUN_REFUSED_AUTHORIZATION = 'RUN_REFUSED_AUTHORIZATION'
RUN_REFUSED_CAPABILITY = 'RUN_REFUSED_CAPABILITY'
RUN_UNKNOWN_READBACK_INCOMPLETE = 'RUN_UNKNOWN_READBACK_INCOMPLETE'
RUN_UNKNOWN_REBOOT = 'RUN_UNKNOWN_REBOOT'
RUN_ABORTED_STORAGE_FAULT = 'RUN_ABORTED_STORAGE_FAULT'
RUN_ABORTED_PREFLIGHT_UNKNOWN = 'RUN_ABORTED_PREFLIGHT_UNKNOWN'
RUN_STOPPED_CANCELLED = 'RUN_STOPPED_CANCELLED'


def safe_state(status,expected):
    pl=status.get('pl_status') or {}
    return bool(status.get('release_id')==expected and status.get('online') is True
        and status.get('stale') is False and status.get('armed') is False
        and pl.get('io_ok') is True and (pl.get('rx') or {}).get('armed') is False
        and (pl.get('tx') or {}).get('source_selected') is False)


def inspect_state(expected):
    release=Path('/home/xilinx/overlays/awg_merged/current').resolve()
    sys.path.insert(0,str(release))
    import merged_release as R
    from rf_status_client import RfStatusClient
    manifest=R.verify_release(release,require_software=True)
    c=RfStatusClient()
    try:status=c.poll().to_dict()
    finally:c.close()
    units={}
    for name in ('pynq-status','rf-daemon'):
        p=subprocess.run(['systemctl','show',name,'--property=ActiveState,SubState,MainPID,NRestarts'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=10)
        units[name]=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
    return dict(expected_release=expected,release_id=manifest['release_id'],release_path=str(release),
        release_verified=True,software_contract=manifest.get('software_contract'),status=status,
        safe=safe_state(status,expected),services=units,
        active_release=Path('/run/pynq-status/active-release').read_text().strip(),
        free_bytes=shutil.disk_usage('/home/xilinx/overlays/awg_merged').free)


def verified_state(state,expected):
    services=state.get('services') or {}
    return bool(state.get('release_verified') is True and state.get('release_id')==expected
        and state.get('safe') is True and safe_state(state.get('status') or {},expected)
        and state.get('active_release')=='/home/xilinx/overlays/awg_merged/releases/'+expected
        and set(services)=={'pynq-status','rf-daemon'}
        and all(v.get('ActiveState')=='active' and v.get('SubState')=='running'
            and str(v.get('MainPID','')).isdigit() and int(v['MainPID'])>0
            for v in services.values()))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('expected');p.add_argument('output',type=Path)
    a=p.parse_args(argv)
    if a.output.exists():raise FileExistsError(str(a.output))
    state=inspect_state(a.expected)
    with a.output.open('x',encoding='utf-8') as f:json.dump(state,f,indent=2,allow_nan=False)
    print(json.dumps(dict(release_id=state['release_id'],safe=state['safe'])),flush=True)
    return 0 if verified_state(state,a.expected) else 1


if __name__=='__main__':raise SystemExit(main())
