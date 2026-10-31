"""Read-only board state via the required skill helper; never arm or load Overlay."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shlex
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.rf_teaching_deploy import load_helper
from scripts.rf_teaching_install_state import verified_state
PARENT='c3bf915c544563324778d1c879b38e3a'


def inspect(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    helper=load_helper();client=None
    receipt=dict(expected_release=PARENT,board_state='UNKNOWN',safe=False,
        scope='read-only state via installed client; no arm/load/capture/Overlay',
        utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    try:
        client=helper.connect();remote='/tmp/rf-environment-readonly-'+uuid.uuid4().hex
        with client.open_sftp() as s:
            s.put(str(ROOT/'scripts/rf_teaching_install_state.py'),remote+'.py')
        command='sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 '+shlex.quote(remote+'.py')+' '+PARENT+' '+shlex.quote(remote+'.json')
        stdin,stdout,stderr=client.exec_command(command,timeout=30)
        stdin.write(helper.PASS+'\n');stdin.flush()
        text=stdout.read().decode('utf-8','replace')+stderr.read().decode('utf-8','replace')
        rc=stdout.channel.recv_exit_status()
        (output/'probe.log').write_text(text,encoding='utf-8')
        receipt['exit_code']=rc
        with client.open_sftp() as s:s.get(remote+'.json',str(output/'state.json'))
        state=json.loads((output/'state.json').read_text(encoding='utf-8'))
        receipt['safe']=verified_state(state,PARENT)
        if receipt['safe']:receipt['board_state']='VERIFIED_SAFE_S1'
    except Exception as exc:
        receipt.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if client is not None:client.close()
        receipt['evidence_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir() if p.is_file()}
        (output/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();result=inspect(a.output);print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['safe'] else 1)
