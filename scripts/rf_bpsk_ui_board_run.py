"""Host driver: upload board session runner + acceptance module, run, fetch evidence."""
import argparse, hashlib, importlib.util, json, re, uuid
from pathlib import Path

def _load_helper():
    helper = Path.home() / '.claude/skills/ac820-pynq/scripts/pynq_ssh.py'
    spec = importlib.util.spec_from_file_location('boardhelper', helper)
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    return h


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--release", required=True)
    p.add_argument("--cable-confirmed", required=True, action="store_true")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument('--teaching',action='store_true',help='S1 same-frame constellation acceptance')
    p.add_argument('--candidate',type=Path,help='Verified local S1 candidate; required with --teaching')
    args = p.parse_args(argv)
    if not re.fullmatch("[0-9a-f]{32}", args.release):
        p.error("invalid release")
    manifest=None
    if args.teaching:
        if args.candidate is None: p.error('--teaching requires --candidate before board access')
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from scripts.rf_teaching_acceptance import candidate_manifest, evaluate
        try: manifest=candidate_manifest(args.candidate,args.release)
        except (ValueError,OSError) as exc: p.error('candidate verification failed: '+str(exc))
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    sources = [root / "scripts/rf_bpsk_ui_board_session.py",
               root / "scripts/rf_bpsk_ui_acceptance.py",
               root / "scripts/rf_bpsk_res_bench.py"]
    declaration = dict(scope="M4 actual UiView button hit/press/release on memory LCD; physical RF, not GT911",
        expected_release=args.release, seeds=[1, 37, 73], sessions=3,
        on_per_session=10, off_per_session=1, amplitude=60,
        lo_hz=80000000, fs_hz=60000000, bandwidth_hz=30000000, ratio=0, phy=0, tap=29,
        attenuation_mdB=40000, min_on_aligned=27, max_aggregate_ber=.01,
        stop_conditions=["timeout", "clipping", "unsafe egress", "release mismatch", "close unconfirmed"],
        source_hashes={s.name: hashlib.sha256(s.read_bytes()).hexdigest() for s in sources})
    declaration['evidence'] = 'every IQ/frame/header/envelope/display + same-input host replay;100 full compute timings'
    if args.teaching:
        declaration.update(scope='S1 actual UiView hits on memory LCD; physical RF, not GT911',
            required_contract='cnn-bpsk-teaching-v1',constellation_max_points=128,
            constellation_tolerance=1e-6,human_touch_measurement='NOT_RUN')
        declaration['host_teaching_verifier_sha256']=hashlib.sha256(
            (root/'scripts/rf_teaching_verify.py').read_bytes()).hexdigest()
        declaration['host_acceptance_sha256']=hashlib.sha256(
            (root/'scripts/rf_teaching_acceptance.py').read_bytes()).hexdigest()
    declaration['host_verifier_sha256'] = hashlib.sha256(
        (root/'scripts/rf_bpsk_ui_verify.py').read_bytes()).hexdigest()
    (args.output / "declaration.json").write_text(json.dumps(declaration, indent=2), encoding="utf-8")
    c=None;rc=1;transport_error=None
    remote = "/tmp/bpsk-live-" + uuid.uuid4().hex
    try:
        h=_load_helper()
        c=h.connect()
        with c.open_sftp() as s:
            s.mkdir(remote)
            for src in sources:
                s.put(str(src), remote + "/" + src.name)
        cmd = ("sudo -S python3 -u -B " + remote + "/rf_bpsk_ui_board_session.py"
               + " --expected-release " + args.release + " --cable-confirmed --output " + remote + "/out"
               + (' --teaching' if args.teaching else ''))
        i, o, e = c.exec_command(cmd, timeout=600)
        i.write(h.PASS + "\n"); i.flush()
        stdout, stderr = o.read().decode("utf-8", "replace"), e.read().decode("utf-8", "replace")
        rc = o.channel.recv_exit_status()
        (args.output / "run.log").write_text(stdout + stderr, encoding="utf-8")
        if rc == 0:
            cmd = ('sudo -S python3 -u -B ' + remote + '/rf_bpsk_res_bench.py '
                   + remote + '/out/s00-on-00.iq 1 ' + remote + '/out/resource-benchmark.json ' + args.release
                   + (' --teaching' if args.teaching else ''))
            i, o, e = c.exec_command(cmd, timeout=120)
            i.write(h.PASS + '\n'); i.flush()
            bench = o.read().decode('utf-8', 'replace') + e.read().decode('utf-8', 'replace')
            benchmark_rc = o.channel.recv_exit_status()
            (args.output / 'benchmark.log').write_text(bench, encoding='utf-8')
            if benchmark_rc: rc = benchmark_rc
        with c.open_sftp() as s:
            for name in s.listdir(remote + "/out"):
                s.get(remote + "/out/" + name, str(args.output / name))
        print(stdout)
    except Exception as exc:
        if not args.teaching: raise
        rc=1
        transport_error=dict(error_type=type(exc).__name__,error=str(exc),
            hardware_closure='UNCONFIRMED; do not automatically retry')
    finally:
        transport=dict(remote=remote,exit_code=rc)
        if transport_error:transport.update(transport_error)
        (args.output/'transport.json').write_text(json.dumps(transport,indent=2),encoding='utf-8')
        if args.teaching:
            accepted=evaluate(args.output,manifest)
            with (args.output/'machine-acceptance.json').open('x',encoding='utf-8') as f:
                json.dump(accepted,f,indent=2,allow_nan=False)
            if not accepted['machine_passed']: rc=rc or 1
            print('S1 machine gates:',accepted['machine_passed'],'human gates: NOT_RUN')
        if c is not None:c.close()
        print("remote exit code:", rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
