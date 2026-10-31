"""Upload standalone algorithm/pilot via sanctioned helper; retrieve raw evidence."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import uuid


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',required=True)
    p.add_argument('--cable-confirmed',required=True,action='store_true')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not re.fullmatch('[0-9a-f]{32}',args.release): p.error('invalid release')
    args.output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1]
    sources=[root/'pynq_overlay/sw/rf_bpsk_receiver.py',root/'scripts/rf_bpsk_board_pilot.py']
    declaration=dict(scope='new finite TX1-RX1 cabled pilot; installed release unchanged',
        expected_release=args.release,seeds=[1,37,73],sessions=3,on_per_session=10,off_per_session=1,
        amplitude=60,lo_hz=80000000,fs_hz=60000000,bandwidth_hz=30000000,ratio=0,
        phy=0,tap=29,attenuation_mdB=40000,min_on_aligned=27,max_aggregate_ber=.01,
        stop_conditions=['timeout','clipping','unsafe egress','release mismatch'],
        algorithm_hashes={s.name:hashlib.sha256(s.read_bytes()).hexdigest() for s in sources})
    (args.output/'declaration.json').write_text(json.dumps(declaration,indent=2),encoding='utf-8')
    helper=Path.home()/'.claude/skills/ac820-pynq/scripts/pynq_ssh.py'
    spec=importlib.util.spec_from_file_location('boardhelper',helper)
    h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    c=h.connect();remote='/tmp/bpsk-pilot-'+uuid.uuid4().hex
    try:
        with c.open_sftp() as s:
            s.mkdir(remote)
            for src in sources: s.put(str(src),remote+'/'+src.name)
            s.put(str(args.output/'declaration.json'),remote+'/declaration.json')
        cmd='sudo -S python3 -u -B '+remote+'/rf_bpsk_board_pilot.py --expected-release '+args.release+' --cable-confirmed --output '+remote+'/out'
        i,o,e=c.exec_command(cmd,timeout=240)
        i.write(h.PASS+'\n');i.flush()
        stdout,stderr=o.read().decode('utf-8','replace'),e.read().decode('utf-8','replace')
        rc=o.channel.recv_exit_status()
        (args.output/'run.log').write_text(stdout+stderr,encoding='utf-8')
        with c.open_sftp() as s:
            for name in s.listdir(remote+'/out'):
                s.get(remote+'/out/'+name,str(args.output/name))
        (args.output/'transport.json').write_text(json.dumps(dict(remote=remote,exit_code=rc)),encoding='utf-8')
        print(stdout);print('remote exit code:',rc)
        return rc
    finally: c.close()


if __name__=='__main__': raise SystemExit(main())
