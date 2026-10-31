"""Prepare and verify frozen HEADROOM-1 materials offline; never execute RF.

The installed 1.0.3 / protocol 1.5 candidate only permits 40 dB physical
loopback. A 46 dB declaration is research material, never executable approval.
This tool deliberately has no connection, installation, or capture entry point.
"""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import merged_release as R

CONTRACT = 'cnn-bpsk-environment-v1'
RELEASE_ID = 'adfac1f14c3cdb379cb92569a21a9054'
DAEMON_SHA256 = '4246903635ceee2677ff937fa4b4c90b93545d46d27297fec791488e670469be'
# Native provenance is outside release_identity. Pin the exact reviewed receipt
# too, so a simultaneously rewritten native tree and provenance cannot pass.
BUILD_SHA256 = 'c6057a73f7c35ddbb076bde4ae131dc35b1668556d94f36e582d915c8b7650d3'
MANIFEST_SHA256 = '3e4e4ab52f2d4cd4b0c3743f2be26ac7440be684e7793a44bcf23ca044a02242'
BLOCKED = 'BLOCKED_BY_EXACT_40DB_CONTRACT'
KINDS = ('normal', 'narrowband', 'pulse')


def _checked_path(value, kind):
    """Check the lexical path before resolve() can hide a linked parent.

    Windows junctions are reparse points, not necessarily Path.is_symlink().
    Inspect every ancestor as well as the leaf. Missing output parents are fine;
    existing input leaves must be ordinary files or directories.
    """
    path = Path(value)
    if '..' in path.parts:
        raise ValueError('parent traversal is not an accepted input path')
    path = path.absolute()
    for component in reversed((path,) + tuple(path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('linked or reparse path forbidden: %s' % component)
        required = kind if component == path else 'directory'
        if required == 'file' and not stat.S_ISREG(info.st_mode):
            raise ValueError('regular file required: %s' % component)
        if required == 'directory' and not stat.S_ISDIR(info.st_mode):
            raise ValueError('ordinary directory required: %s' % component)
    if kind != 'new' and not path.exists():
        raise ValueError('required input missing: %s' % path)
    if kind == 'new' and path.exists():
        raise ValueError('fresh output directory required: %s' % path)
    return path


def _sha(path):
    return hashlib.sha256(_checked_path(path, 'file').read_bytes()).hexdigest()


def _json_bytes(document):
    return (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _read_json(path):
    return json.loads(_checked_path(path, 'file').read_text(encoding='utf-8'),
                      object_pairs_hook=_unique_object)


def _native_sources(root):
    """Match rf_build_daemon.package's build-input membership, not its guards.

    Root .c/.h + Makefile, and vendor/test immediate .c/.h/.py/no-suffix files
    include vendor/SHA256SUMS. Generated .o/.exe/cache trees are not inputs.
    Added inputs matter even when an old provenance map did not list them.
    """
    directory = _checked_path(root / 'rf_overlay/sw/board_app', 'directory')
    paths = [directory / 'Makefile']
    paths.extend(p for p in directory.iterdir() if p.suffix in ('.c', '.h'))
    for name in ('vendor', 'test'):
        sub = _checked_path(directory / name, 'directory')
        paths.extend(p for p in sub.iterdir() if p.suffix in ('.c', '.h', '.py', ''))
    result = {}
    for path in paths:
        # Ordinary non-input directories such as __pycache__ are not packaged.
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400):
            continue
        result[path.relative_to(directory).as_posix()] = _sha(path)
    return dict(sorted(result.items()))


def _qualify(candidate, baseline):
    candidate = _checked_path(candidate, 'directory')
    baseline = _checked_path(baseline, 'file')
    candidate_files = {}
    for path in candidate.iterdir():
        candidate_files[path.name] = _sha(path)
    if candidate_files.get(R.MANIFEST_NAME) != MANIFEST_SHA256:
        raise ValueError('exact reviewed candidate manifest required')
    if candidate_files.get('merged_build_receipt.json') != BUILD_SHA256:
        raise ValueError('exact reviewed native build provenance required')
    manifest = R.verify_release(candidate, require_software=True)
    if (manifest.get('release_id') != RELEASE_ID or manifest.get('software_contract') != CONTRACT
            or manifest.get('daemon_version') != '1.0.3'
            or manifest.get('protocol_version') != 1
            or manifest.get('daemon_sha256') != DAEMON_SHA256):
        raise ValueError('exact frozen 1.0.3 candidate required')
    root = _checked_path(ROOT, 'directory')
    for relative in R.software_sources(CONTRACT).values():
        _checked_path(root / relative, 'file')
    software = R.software_digests(root, CONTRACT)
    if software != manifest['software']:
        raise ValueError('candidate software source changed')
    build = _read_json(candidate / 'merged_build_receipt.json')
    provenance = build.get('rx_gain_observer_build') or {}
    native = _native_sources(root)
    if (provenance.get('daemon_version') != '1.0.3'
            or provenance.get('protocol_major') != 1 or provenance.get('protocol_minor') != 5
            or provenance.get('daemon_sha256') != DAEMON_SHA256
            or len(native) != 51 or native != provenance.get('source_sha256')):
        raise ValueError('complete frozen native input set or contents changed')
    frozen = candidate / 'rf_environment_baseline.json'
    if baseline.stat().st_size > 131072 or baseline.read_bytes() != frozen.read_bytes():
        raise ValueError('exact candidate baseline bytes required')
    sys.path.insert(0, str(root / 'pynq_overlay/sw'))
    # This tool is defined against the frozen tree it is handed, so the pinned copy
    # of the domain modules is imported from that tree even when another version is
    # already cached in this interpreter.  After the 2026-10-17 domain migration a
    # cached current copy answers with a different domain version, which would
    # refuse the very baseline this tool exists to bind.  The cache is restored
    # afterwards, so the caller's interpreter is left exactly as it was.
    pinned = ('rf_environment', 'rf_teaching_profile')
    saved = {name: sys.modules.pop(name) for name in pinned if name in sys.modules}
    try:
        from rf_environment import EnvironmentBaseline
        supplied = EnvironmentBaseline.from_dict(_read_json(baseline))
        frozen_semantic = EnvironmentBaseline.from_dict(_read_json(frozen))
    finally:
        for name in pinned:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
    if supplied.sha256 != frozen_semantic.sha256:
        raise ValueError('frozen baseline semantic identity mismatch')
    # These tool dependencies are not release software; bind them separately.
    tools = dict(preparer=Path(__file__), release_entry=Path(R.__file__),
                 release_verifier=Path(sys.modules[R.verify_release.__module__].__file__))
    return dict(release_id=RELEASE_ID, daemon_sha256=DAEMON_SHA256,
        manifest_sha256=MANIFEST_SHA256, merged_build_receipt_sha256=BUILD_SHA256,
        candidate_files_sha256=dict(sorted(candidate_files.items())),
        software_sha256=software, native_source_sha256=native,
        baseline_file_sha256=_sha(baseline), baseline_semantic_sha256=supplied.sha256,
        tool_sha256={role: _sha(path) for role, path in tools.items()})


def _expected_files(candidate, baseline):
    """Reconstruct ALL expected bytes from fixed scope and verified inputs.

    Verification deliberately does not trust a hash table rewritten alongside
    a payload, parameter, or readiness flag.
    """
    bindings = _qualify(candidate, baseline)
    from rf_experiment_scenarios import ScenarioConfig, generate_scenario
    files, sources = {}, {}
    for kind in KINDS:
        frame = generate_scenario(ScenarioConfig(kind=kind))
        payload_name, scenario_name = kind + '.tx.iq', kind + '.scenario.json'
        files[payload_name] = frame.payload
        files[scenario_name] = _json_bytes(frame.to_dict())
        sources[kind] = dict(payload_file=payload_name, scenario_file=scenario_name,
            payload_sha256=hashlib.sha256(frame.payload).hexdigest(),
            scenario_sha256=hashlib.sha256(files[scenario_name]).hexdigest(),
            metadata=frame.metadata_dict(), declaration=frame.declaration_dict())
    status = dict(prepared=True, execution_ready=False, rf_execution=BLOCKED,
        physical_frames=0, initializations=0, formal_physical='NOT_RUN',
        human_acceptance='NOT_RUN', s2_stage_passed=False)
    declaration = dict(schema_version=1, study_id='HEADROOM-1', **status,
        bindings=bindings, daemon_version='1.0.3', protocol_major=1, protocol_minor=5,
        single_variable='TX attenuation 40000 to 46000 mdB; all other settings fixed',
        fixed_rf=dict(lo_hz=80000000, sample_rate_hz=60000000,
            rx_bandwidth_hz=30000000, tx_bandwidth_hz=30000000,
            tx_attenuation_mdB=46000, tx_channels=[1, 2], two_rx_two_tx=True,
            idelay_enabled=True, idelay_tap=29),
        fixed_rx=dict(samples=4096, decimation=1, modulation='BPSK',
            symbol_rate_hz=3000000, shaping='RECT', prbs7_seed=1, reference_seed=1),
        maximum_initializations=1, maximum_capture_attempts=5, settle_wait_us=0,
        timing_observation=True,
        order=['off-before', 'normal', 'narrowband', 'pulse', 'off-after'],
        off=dict(action='ordinary_rx_capture', tx_source_enabled=False,
                 physical_loopback=False, require_no_sync_no_constellation_no_ber=True),
        on=dict(action='future_named_headroom_physical_loopback',
                execution_ready=False, kinds=list(KINDS)), sources=sources,
        stop_conditions=['clipping', 'RF readback mismatch', 'missing original IQ',
            'identity mismatch', 'timeout', 'OFF synchronization/points/BER',
            'unconfirmed cleanup', 'no retry/replacement/top-up'],
        conclusion_limits=['46 dB is a new RF domain',
            '40 dB baseline must return INVALID/RF_DOMAIN_MISMATCH',
            'historical G2 frames are not a paired control',
            'no assumed 6 dB RX amplitude reduction', 'no S2 qualification'])
    compatibility = dict(schema_version=1, **status, release_id=RELEASE_ID,
        existing_physical_attenuation_mdB=40000, requested_attenuation_mdB=46000,
        reason='native state, physical action, client and UI require exact 40000 mdB',
        forbidden_workarounds=['configure 46000 then confirm 40000',
            'bypass UI/worker/controller', 'second Overlay or direct hardware owner',
            'change production RF constants or relabel baseline domain'],
        next_requirement='independent named research contract and qualified new candidate')
    files['study-declaration.json'] = _json_bytes(declaration)
    files['compatibility.json'] = _json_bytes(compatibility)
    receipt = dict(schema_version=1, **status, release_id=RELEASE_ID, bindings=bindings,
        output_sha256={name: hashlib.sha256(data).hexdigest()
                       for name, data in sorted(files.items())})
    files['receipt.json'] = _json_bytes(receipt)
    return files, receipt


def prepare(candidate, output, baseline):
    output = _checked_path(output, 'new')
    files, receipt = _expected_files(candidate, baseline)
    # Every input has been checked before any output directory is created.
    output.parent.mkdir(parents=True, exist_ok=True)
    # Publish the directory only after every byte, including the final receipt,
    # has landed. A late disk failure must not expose prepared:true documents
    # in the promised output directory.
    staging = Path(tempfile.mkdtemp(prefix='.headroom-preparing-', dir=str(output.parent)))
    try:
        for name in sorted(set(files) - {'receipt.json'}):
            (staging / name).write_bytes(files[name])
        (staging / 'receipt.json').write_bytes(files['receipt.json'])
        _checked_path(output, 'new')
        staging.rename(output)
    finally:
        if staging.exists():
            # Only this invocation's freshly created flat directory is removed.
            # No recursive delete or computed external target is used.
            _checked_path(staging, 'directory')
            if staging.parent != output.parent or not staging.name.startswith('.headroom-preparing-'):
                raise ValueError('unexpected staging cleanup target')
            for path in staging.iterdir():
                _checked_path(path, 'file').unlink()
            staging.rmdir()
    return receipt


def verify_preparation(output, candidate, baseline):
    output = _checked_path(output, 'directory')
    files, receipt = _expected_files(candidate, baseline)
    actual = {p.name for p in output.iterdir()}
    if actual != set(files):
        raise ValueError('preparation output membership changed')
    for name, expected in files.items():
        if _checked_path(output / name, 'file').read_bytes() != expected:
            raise ValueError('preparation content changed: ' + name)
    return dict(verified=True, prepared=True, execution_ready=False, rf_execution=BLOCKED,
                release_id=receipt['release_id'], physical_frames=0, initializations=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'verify'))
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = (prepare(args.candidate, args.output, args.baseline)
                  if args.operation == 'prepare'
                  else verify_preparation(args.output, args.candidate, args.baseline))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print('HEADROOM preparation rejected: %s' % exc, file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
