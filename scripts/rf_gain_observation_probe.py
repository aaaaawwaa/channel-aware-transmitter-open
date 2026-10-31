"""Finite initialized-RX observation through the installed sole daemon owner.

The probe loads the ordinary verified BPSK teaching buffer but never starts TX,
never captures IQ, never changes AGC/gain, and never opens MMIO or an Overlay.
"""
import argparse
import json
from pathlib import Path


def campaign(factory,output,release):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    path=output/'result.json'
    if path.exists():raise FileExistsError(str(path))
    result=dict(expected_release=release,scope='initialized receiver raw reads; no RF transmit or IQ capture',
        passed=False,owned=False,closed=False,disarm_ack=False,physical_frames=0,
        observations=[],formal_physical='NOT_RUN',human_acceptance='NOT_RUN')
    session=None
    def observe():
        value=session.client.observe_rx_gain()
        if (value.get('release_id')!=release or value.get('daemon')!='1.0.1'
                or value.get('protocol_major')!=1 or value.get('protocol_minor')!=3):
            raise RuntimeError('exact installed observer identity required')
        return value
    try:
        session=factory()
        result['initial']=session.initial()
        if result['initial'].get('safe') is not True or result['initial'].get('release_id')!=release:
            raise RuntimeError('initial_not_safe_or_wrong_release')
        result['unarmed_observation']=observe()
        if result['unarmed_observation'].get('gain_observation') is not None:
            raise RuntimeError('unarmed observation unexpectedly available')
        result['owned']=True
        session.setup(1)
        for index in range(3):
            value=observe()
            result['observations'].append(dict(index=index,observation=value))
            if value.get('gain_observation') is None:
                raise RuntimeError('initialized observation unavailable: '+str(value.get('reason')))
            status=session.client.poll().to_dict()
            result['observations'][-1]['post_status']=status
            if not session.egress_safe(status):raise RuntimeError('observation egress unconfirmed')
        result['observations_complete']=True
    except Exception as exc:
        result.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if result['owned']:
            try:
                cleanup=session.cleanup()
                result.update(closed=cleanup.get('safe') is True,disarm_ack=cleanup.get('disarm_ack') is True)
            except Exception as exc:result['cleanup_error']=str(exc)
        if session is not None:
            try:session.close()
            except Exception as exc:result['close_error']=str(exc)
        result['passed']=bool(result.get('observations_complete') and result['closed']
            and result['disarm_ack'] and not any(k in result for k in ('error','cleanup_error','close_error')))
        with path.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-release',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--initialize',required=True,action='store_true',help='arm/init/configure/load only; never start TX')
    args=parser.parse_args()
    from rf_environment_board_session import EnvironmentSession
    result=campaign(lambda:EnvironmentSession(args.expected_release,args.output),args.output,args.expected_release)
    print(json.dumps({k:v for k,v in result.items() if k not in ('observations','initial','unarmed_observation')},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
