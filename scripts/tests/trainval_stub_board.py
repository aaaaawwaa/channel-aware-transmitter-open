"""Offline stub board for the TRAIN/VAL collection path (test double, NOT evidence).

The stub drives the REAL collection envelope: BOARD.run_session() and
BOARD.build_session_documents() run unchanged, and so do the host orchestrator,
the session check and the calibration adapter that consumes the result.  Only two
things are fake:

* the frames are the deterministic synthetic codes the calibration fixture already
  uses (normal = seeded PRBS7 BPSK at amplitude 60 with 1.5-code noise, off = the
  receiver floor), labelled against the product v4/46000 baseline so the CLEAR and
  non-CLEAR rules are actually exercised;
* the release id is 'stubboard...', which no board ever installed, and every
  artifact says origin = STUB NOTICE.

A session produced here is shaped exactly like a board session, so it is a valid
test double for the download path -- and it must never be presented, adapted or
archived as physical TRAIN/VAL evidence.  The host receipt from a stub run is
tagged by the caller, not by this module.
"""
import dataclasses
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import merged_release as R  # noqa: E402
from scripts import rf_environment_trainval_board_session as BOARD  # noqa: E402
from scripts import rf_environment_trainval_session as SESSION  # noqa: E402

STUB_RELEASE = 'stubboard00000000000000000000000000'
# One origin string for every stub artifact: the board driver owns it so a stub
# session can never be mistaken for the production envelope.
STUB_NOTICE = BOARD.STUB_ORIGIN
CANDIDATE_BASE = (ROOT / 'evidence' / 'rf-teaching' / 's2-46db-candidate-b-2026-10-17'
                  / 'candidate' / 'releases' / 'ad05576aff6b9bf2b6b495347a6269ec')
PRODUCT_BASELINE = ROOT / 'pynq_overlay' / 'sw' / 'rf_environment_baseline.json'


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_text(path, text):
    """LF-only text writing; Path.write_text(newline=...) needs Python 3.10."""
    with Path(path).open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    return Path(path)


def product_baseline():
    """The product tree's own frozen v4/46000 baseline (a label source, not a claim)."""
    from rf_environment import EnvironmentBaseline
    body = json.loads(PRODUCT_BASELINE.read_text(encoding='utf-8'))
    return EnvironmentBaseline.from_dict(body)


def build_offline_candidate(target, *, base=None):
    """A stand-in for the M4-built candidate: reviewed candidate + product baseline.

    The 46 dB candidate in the evidence tree still carries the historical 40 dB /
    v3 baseline, which is exactly what the collection entry refuses
    (CANDIDATE_BASELINE_DOMAIN_MISMATCH).  This copy differs in that one file plus
    the software map and the derived release id, and is only ever used offline.

    It also aligns the daemon's own physical-loopback rule with the profile it ships:
    the reviewed candidate carries a daemon that approves exactly 40000 mdB while its
    46 dB profile names 46000, and a release in that state cannot capture a single
    physical frame (rf_state.c:114 refuses it before any hardware write).  A stub
    candidate stands in for a RUNNABLE release, so its two constants agree; the
    disagreeing release is injected deliberately, and by name, in
    test_rf_environment_trainval_loopback_attenuation.py.
    """
    base = Path(base or CANDIDATE_BASE)
    target = Path(target)
    if target.exists():
        raise ValueError('candidate target already exists: %s' % target)
    shutil.copytree(str(base), str(target),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    baseline_path = target / 'rf_environment_baseline.json'
    manifest_path = target / 'merged_release_manifest.json'
    receipt_path = target / 'merged_build_receipt.json'
    # The sealed evidence tree is read-only; a copy that has to be edited is not.
    for path in (baseline_path, manifest_path, receipt_path):
        path.chmod(0o644)
    shutil.copyfile(str(PRODUCT_BASELINE), str(baseline_path))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    software = dict(receipt['software'])
    software['rf_environment_baseline.json'] = _digest(target / 'rf_environment_baseline.json')
    receipt['software'] = software
    manifest['software'] = dict(software)
    daemon_path = target / 'rf_daemon'
    daemon_path.chmod(0o644)
    raw = daemon_path.read_bytes()
    literal = b'physical loopback requires attenuation_mdB 40000'
    profile = (target / 'rf_teaching_profile.py').read_text(encoding='utf-8')
    agreed = int(re.search(r'^ATTENUATION_MDB = ([0-9]+)', profile, re.M).group(1))
    replacement = b'physical loopback requires attenuation_mdB %d' % agreed
    if literal in raw and len(replacement) == len(literal):
        daemon_path.write_bytes(raw.replace(literal, replacement))
    manifest['daemon_sha256'] = _digest(daemon_path)
    manifest['release_id'] = R.release_identity(
        manifest['build_id'], manifest['bit_sha256'], manifest['hwh_sha256'],
        manifest['daemon_version'], manifest['protocol_version'], manifest['daemon_sha256'],
        software=software, software_contract=receipt.get('software_contract'),
        model_artifact=receipt.get(R.MODEL_KEY))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    R.verify_release(target, require_software=True)
    return dict(path=str(target.resolve()), release_id=manifest['release_id'],
                daemon_loopback_attenuation_mdB=agreed)


class StubSession:
    """A card-free session with the production session protocol."""

    def __init__(self, split, index, output, *, release=STUB_RELEASE, domain=None,
                 environment_version=None, faults=None, payloads=None):
        self.split, self.index = split, index
        self.output = Path(output)
        # This double NEVER creates its output directory.  The production LiveUiSession
        # writes <tag>.iq/<tag>.json straight into its --output and does not create it
        # either (scripts/rf_bpsk_ui_board_session.py:156-184); only write_record() makes
        # <output>/records.  The host link owns <remote>/out (SftpBoardLink.open) and the
        # board entry owns --output (BOARD.main -> prepare_output_directory), so a stub
        # that quietly called mkdir() here would hide exactly the 2026-10-18 defect
        # (<stage>/out never existed) from every test that uses it.
        if self.output.is_dir():
            (self.output / 'records').mkdir(exist_ok=True)
        self.release = release
        self.domain = dict(domain or {})
        self.environment_version = environment_version
        self.faults = dict(faults or {})
        self.seed = None
        self.scene_kind = None
        self.scene_calls = []
        self.captures = []
        self.closed = False
        # Optional payload provider: payloads(split, index, tag, position) -> bytes.
        # The TRAIN/VAL fake chain uses it to capture the regime the board actually
        # produced (the campaign3 frames), while the default stays the deterministic
        # synthetic fixture codes.
        self.payloads = payloads

    # ------------------------------------------------------------- board protocol
    def initial(self):
        return dict(safe=bool(self.faults.get('initial_safe', True)), release_id=self.release)

    def setup(self, seed):
        self.seed = seed

    def scene(self, kind):
        # A real scene load: the product's worker generates the payload and the
        # declaration from ONE generate_scenario call, and the frame that is later
        # captured carries THAT declaration.  The double records every load so a test
        # can prove the runner switched scenes instead of assuming one.
        self.scene_calls.append(kind)
        # A load that is issued but does not take effect: the frame that is captured
        # afterwards still carries the previous scene's declaration.  This is the
        # 2026-10-18 board run (off-01 declared 'normal'), and it must be refused by
        # FRAME_SCENARIO_MISMATCH rather than accommodated.
        stuck = self.faults.get('stuck_scene')
        self.scene_kind = stuck if stuck is not None else kind

    def cleanup(self):
        return dict(safe=not self.faults.get('close_unconfirmed', False),
                    disarm_ack=not self.faults.get('disarm_not_acked', False))

    def close(self):
        self.closed = True

    # ------------------------------------------------------------------ artifacts
    def _frame(self, tag, physical, payload):
        from rf_rx_types import RxIqFrame, RxReadback
        position = BOARD.FRAME_TAGS.index(tag)
        session_id = SESSION.session_id(self.split, self.index)
        capture_id = 'stub:%s:%s' % (session_id, tag)
        completed = float(position + 1) * 1000.0 + float(self.index)
        return RxIqFrame(
            capture_id=capture_id, payload=bytes(payload), samples=4096,
            sample_rate_hz=60000000, ratio=0, daemon_timestamp_ns=position + 1,
            connection_generation=1, config_revision=1, request_id=position,
            release_id=self.release,
            op=('loopback.physical.capture' if physical else 'rx.capture'),
            physical=physical, pre_readback=RxReadback(True, rx_armed=False,
                                                       tx_source_selected=False),
            post_readback=RxReadback(
                True, rx_armed=False, rx_done=True,
                tx_source_selected=self.faults.get('tx_source_selected') == tag),
            completed_at_monotonic=completed)

    def capture(self, tag, physical):
        from rf_bpsk_pipeline import DemodConfig, analyze_demod
        from rf_bpsk_receiver import BpskConfig
        from rf_environment import analyze_environment
        from rf_experiment_scenarios import ScenarioConfig, generate_scenario
        position = BOARD.FRAME_TAGS.index(tag)
        scenario = 'normal' if position < SESSION.NORMAL_PER_SESSION else 'off'
        if self.faults.get('capture_fails') == tag:
            raise RuntimeError('stub capture failure for %s' % tag)
        supplied = self.payloads(self.split, self.index, tag, position) if self.payloads else None
        payload = bytearray(supplied if supplied is not None
                            else SESSION._fixture_codes(self.split, self.index, position, self.seed))
        if self.faults.get('clipped') == tag:
            payload[0:2] = (2047).to_bytes(2, 'little', signed=True)
        if self.faults.get('duplicate_iq') == tag and position:
            previous = self.output / (BOARD.FRAME_TAGS[position - 1] + '.iq')
            payload = bytearray(previous.read_bytes())
        payload = bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        frame = self._frame(tag, physical, payload)
        # The board's own RX-draft revision is >= 1 once setup enables the reference
        # (spec G.8); the fixture writes 0.  The stub can carry either, or a negative.
        revision = self.faults.get('submitted_revision', 0)
        submitted = DemodConfig(BpskConfig('BPSK', 3000000, 'RECT', self.seed),
                                revision, 1, 4096, 60000000)
        envelope = analyze_demod(frame, submitted)
        report = analyze_environment(frame, envelope, self.domain, product_baseline())
        # The one label the pre-fit baseline produced for an otherwise perfect normal
        # frame on the board (campaign3: every normal frame UNKNOWN /
        # AMPLITUDE_OUTSIDE_RULE_SUPPORT).  A test may force that pair -- or a
        # NARROWBAND/PULSE label -- to exercise the collection gate itself; the stub's
        # default label is always the one the product analyzer computed above and the
        # full fake chain uses the real campaign3 bytes.
        forced = (self.faults.get('environment_labels') or {}).get(tag)
        if forced is not None:
            report = dataclasses.replace(report, label=forced[0], reason=forced[1])
        # The declaration is the LOADED scene's, never the slot's: this is the exact
        # 2026-10-18 board defect (off-01 carried truth 'normal' because the runner
        # never loaded the OFF scene), so a runner that forgets the switch is refused
        # by FRAME_SCENARIO_MISMATCH here too.
        if self.scene_kind is None:
            raise RuntimeError('stub capture refused: no scene was loaded for %s' % tag)
        declaration = generate_scenario(ScenarioConfig(
            kind=self.scene_kind, amplitude=60, prbs7_seed=self.seed)).declaration_dict()
        rail = bool(report.reason in ('CLIPPED', 'INVALID_IQ'))
        trainval = dict(
            tag=tag, position=position, scenario=scenario, truth=scenario,
            physical=bool(physical), requested_rf=dict(self.domain),
            client_requested_rf=dict(self.domain),
            actual_rf=dict(
                source='stub board: no hardware readback exists',
                pre_readback=frame.pre_readback.to_dict(),
                post_readback=frame.post_readback.to_dict(),
                daemon=dict(release_id=self.release, armed=False, online=True, stale=False,
                            daemon_state='idle', tx_session_ready=bool(physical),
                            rx_session_ready=True),
                rx_gain_observation=None,
                rx_gain_observation_unavailable='STUB_BOARD_NO_HARDWARE',
                attenuation_readback=None,
                attenuation_readback_reason='NOT_READ_BACK_ON_ENVIRONMENT_PATH'),
            label=report.label, reason=report.reason, rail=rail, clipping_count=0,
            reference_sync=envelope.result.reference_sync, ber=envelope.result.ber,
            compared_bits=envelope.result.compared_bits)
        metadata = dict(
            capture_status=dict(state='done', physical=bool(physical),
                                capture_id=frame.capture_id, code=None, reason='',
                                close_confirmed=True, cancelled=False, seq=position),
            capture_id=frame.capture_id, samples=4096, bytes=len(payload),
            sample_rate_hz=60000000, ratio=0, decimation=1, output_fs_hz=60000000,
            op=frame.op, release_id=self.release, connection_generation=1,
            config_revision=1, request_id=position, daemon_timestamp_ns=position + 1,
            completed_at_monotonic=frame.completed_at_monotonic,
            pre_io_ok=True, post_io_ok=True, post_rx_armed=False,
            post_tx_source_selected=False, format='s16_iq_le', iq_sha256=digest,
            pre_readback=frame.pre_readback.to_dict(),
            post_readback=frame.post_readback.to_dict(),
            demod_result=dataclasses.asdict(envelope), analysis=None,
            handoff_frame=frame.to_dict(), handoff_frame_matches=True,
            ui_events=[], scope=STUB_NOTICE)
        sidecar = dict(
            environment=report.to_dict(),
            display_environment=dict(report.to_dict(), available=True, current=True),
            rf_config=dict(self.faults.get('sidecar_domain') or self.domain),
            scenario_declaration=declaration,
            record=dict(record_id='stub-record-%s-%s' % (self.split, tag),
                        capture_id=frame.capture_id, iq_sha256=digest,
                        metadata_sha256=None, bytes=len(payload), path=None,
                        historical=False),
            iq_sha256=digest, capture_id=frame.capture_id, release_id=self.release,
            scope=STUB_NOTICE)
        slot = None
        _document, slots = BOARD.frame_plan(self.split, self.index)
        for candidate_slot in slots:
            if candidate_slot['tag'] == tag:
                slot = candidate_slot
        metadata, sidecar = BOARD.bind_frame_documents(
            metadata, sidecar, slot=slot,
            session_block=BOARD.session_block_of(_document),
            environment_version=self.environment_version, release_id=self.release,
            trainval=trainval, origin=STUB_NOTICE)
        if self.faults.get('identity_mismatch') == tag:
            sidecar['environment']['capture_id'] = 'somewhere-else'
        if self.faults.get('skip_iq') != tag:
            (self.output / (tag + '.iq')).write_bytes(payload)
        _write_text(self.output / (tag + '.json'),
                    json.dumps(metadata, indent=2, allow_nan=False))
        _write_text(self.output / (tag + '.environment.json'),
                    json.dumps(sidecar, indent=2, allow_nan=False))
        record = self.output / 'records' / ('stub-%s' % tag)
        # write_record() creates the records root itself (rf_experiment_record.py:86-90).
        record.mkdir(parents=True, exist_ok=True)
        (record / 'frame.iq').write_bytes(payload)
        _write_text(record / 'metadata.json', json.dumps(
            dict(capture_id=frame.capture_id, iq_sha256=digest, scope=STUB_NOTICE), indent=2))
        _write_text(record / 'receipt.json', json.dumps(
            dict(record_id='stub-%s' % tag, capture_id=frame.capture_id,
                 iq_sha256=digest, scope=STUB_NOTICE), indent=2))
        self.captures.append(tag)
        return dict(on=bool(physical), egress_safe=self.faults.get('egress_unsafe') != tag,
                    capture_id=frame.capture_id, release_id=self.release,
                    iq_sha256=digest, iq_file=tag + '.iq', metadata_file=tag + '.json',
                    environment_file=tag + '.environment.json',
                    result=dict(reference_sync=envelope.result.reference_sync,
                                ber=envelope.result.ber,
                                compared_bits=envelope.result.compared_bits,
                                reason=report.reason))


class StubBoardLink:
    """A fake board link: the host orchestrator cannot tell it from the real one."""

    def __init__(self, root, *, candidate, declaration, faults=None, payloads=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.remote = '/stub/remote'
        self.session_dir = self.remote + '/out'
        # Mirror SftpBoardLink.open() exactly: the HOST LINK owns the remote layout it
        # declares.  This is not the board entry: BOARD.main() must still create and
        # verify --output itself, which the main() integration tests exercise.
        self._local(self.session_dir).mkdir(parents=True, exist_ok=True)
        self.result_path = self.remote + '/session-result.json'
        self.preflight_path = self.remote + '/preflight.json'
        self.candidate = Path(candidate)
        self.declaration = dict(declaration)
        self.faults = dict(faults or {})
        self.payloads = payloads
        self.uploads = {}
        self.runs = []
        # Dispatch spy: the one place a board command leaves the host.  A refused
        # preflight must leave session_commands empty (2026-10-18: the gate was skipped
        # and the real 11-frame session ran anyway).
        self.preflight_commands = []
        self.session_commands = []
        self.transport = 'stub'

    def _local(self, remote_path):
        relative = Path(remote_path).relative_to(self.remote)
        return self.root / relative

    def upload(self, local_path, name):
        self.uploads[name] = _digest(local_path)

    def run(self, argv, log, *, sudo=True, timeout=240):
        arguments = [str(argument) for argument in argv]
        entry = dict(argv=list(arguments), sudo=bool(sudo), timeout=timeout)
        self.runs.append(entry)
        if '--preflight' in arguments:
            self.preflight_commands.append(entry)
        else:
            self.session_commands.append(entry)
        Path(log).write_text('stub board link\n' + ' '.join(arguments) + '\n',
                             encoding='utf-8')
        def value(flag):
            return arguments[arguments.index(flag) + 1]
        split, index = value('--split'), int(value('--session'))
        release = value('--expected-release')
        # The board entry's own CLI carries the mode as a constrained choice, so the
        # double validates it the same way and refuses an unknown one by name.
        mode = SESSION.check_collection_mode(
            value('--collection-mode') if '--collection-mode' in arguments else None)
        if '--preflight' in arguments:
            fault = dict(self.faults.get('preflight') or {})
            if fault.get('missing_result'):
                # The 2026-10-18 shape: no result document at all, nonzero exit.
                return int(fault.get('exit_code', 1))
            if fault.get('empty_result'):
                # The file exists but holds nothing: not evaluable, not a pass.
                self._local(self.preflight_path).write_text('', encoding='utf-8')
                return int(fault.get('exit_code', 0))
            if fault.get('unparseable_result'):
                self._local(self.preflight_path).write_text('{not json', encoding='utf-8')
                return int(fault.get('exit_code', 0))
            document = BOARD.preflight(release, split, index,
                                       release_directory=self.candidate,
                                       collection_mode=mode)
            document.update(fault.get('document') or {})
            self._local(self.preflight_path).write_text(
                json.dumps(document, indent=2), encoding='utf-8')
            return int(fault.get('exit_code', 0))
        if self.faults.get('session_refuses'):
            # The board entry failed before run_session could write its result document
            # (e.g. SESSION_OUTPUT_UNCREATABLE): nonzero exit, nothing on disk.
            return 1
        if self.faults.get('preflight_release'):
            release = self.faults['preflight_release']
        session = StubSession(
            split, index, self._local(self.session_dir), release=release,
            domain=self.declaration['domain'],
            environment_version=self.declaration['environment_version'],
            faults=self.faults.get('session'), payloads=self.payloads)
        result = BOARD.run_session(
            session, split=split, index=index, output=self._local(self.session_dir),
            expected_release=release, domain=self.declaration['domain'],
            environment_version=self.declaration['environment_version'],
            result_path=self._local(self.result_path), origin=BOARD.STUB_ORIGIN,
            requested_rf=dict(self.declaration['domain']), collection_mode=mode,
            board=dict(release_id=release, release_directory=str(self.candidate),
                       baseline_sha256=self.declaration.get('baseline_sha256'),
                       software_contract='cnn-bpsk-environment-v1'))
        return 0 if result['passed'] else 1

    def fetch(self, remote_path, local_path):
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(self._local(remote_path)), str(local_path))
        return local_path

    def download_tree(self, remote_dir, local_dir):
        local_dir = Path(local_dir)
        errors = []
        remote = self._local(remote_dir)
        if not remote.is_dir():
            return [dict(path=remote_dir, error='stub: no session directory')]
        drop = self.faults.get('drop_download')
        for path in sorted(remote.rglob('*')):
            relative = path.relative_to(remote)
            target = local_dir / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if drop and relative.name == drop:
                errors.append(dict(path=str(relative), error='stub: download dropped'))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(path), str(target))
        return errors

    def close(self):
        self.closed = True

# ------------------------------------------------------- offline mutation helpers
# Used by the negative controls: each helper edits a REAL session directory the way
# a broken transfer, a mislabelled frame or a hostile edit would, and repairs only
# what an honest writer would have repaired, so the named refusal that follows is
# about the injected defect and nothing else.

def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_json(path, document):
    _write_text(path, json.dumps(document, indent=2, allow_nan=False))


def slot_of(manifest, tag):
    for slot in manifest['slots']:
        if slot['tag'] == tag:
            return slot
    raise KeyError(tag)


def refresh_receipt(session_dir):
    """Rebuild the receipt from the manifest and the files on disk."""
    session_dir = Path(session_dir)
    manifest = load_json(session_dir / 'session-manifest.json')
    receipt = load_json(session_dir / 'receipt.json')
    receipt['session_manifest_sha256'] = _digest(session_dir / 'session-manifest.json')
    receipt['frames'] = [dict(position=slot['position'], tag=slot['tag'],
                              frame_id=slot['frame_id'], iq=slot['iq'],
                              metadata=slot['metadata'], environment=slot['environment'],
                              iq_sha256=slot['iq_sha256'], capture_id=slot['capture_id'])
                         for slot in manifest['slots']]
    receipt['evidence_sha256'] = {}
    for slot in manifest['slots']:
        for name in (slot['iq'], slot['metadata'], slot['environment']):
            receipt['evidence_sha256'][name] = _digest(session_dir / name)
    save_json(session_dir / 'receipt.json', receipt)


def rebind_iq(session_dir, tag, payload):
    """Replace one frame's bytes, keeping every declared digest consistent."""
    session_dir = Path(session_dir)
    manifest = load_json(session_dir / 'session-manifest.json')
    slot = slot_of(manifest, tag)
    (session_dir / slot['iq']).write_bytes(bytes(payload))
    digest = hashlib.sha256(bytes(payload)).hexdigest()
    slot['iq_sha256'] = digest
    save_json(session_dir / 'session-manifest.json', manifest)
    metadata = load_json(session_dir / slot['metadata'])
    metadata['iq_sha256'] = digest
    save_json(session_dir / slot['metadata'], metadata)
    sidecar = load_json(session_dir / slot['environment'])
    sidecar['iq_sha256'] = digest
    save_json(session_dir / slot['environment'], sidecar)
    refresh_receipt(session_dir)


def set_identity(session_dir, tag, capture_id):
    """Move one frame's capture identity in every document that carries it."""
    session_dir = Path(session_dir)
    manifest = load_json(session_dir / 'session-manifest.json')
    slot = slot_of(manifest, tag)
    slot['capture_id'] = capture_id
    save_json(session_dir / 'session-manifest.json', manifest)
    metadata = load_json(session_dir / slot['metadata'])
    metadata['capture_id'] = capture_id
    metadata['capture_status']['capture_id'] = capture_id
    metadata['demod_result']['result']['capture_id'] = capture_id
    save_json(session_dir / slot['metadata'], metadata)
    sidecar = load_json(session_dir / slot['environment'])
    sidecar['capture_id'] = capture_id
    sidecar['environment']['capture_id'] = capture_id
    sidecar['display_environment']['capture_id'] = capture_id
    sidecar['record']['capture_id'] = capture_id
    save_json(session_dir / slot['environment'], sidecar)
    refresh_receipt(session_dir)


def drop_identity(session_dir, tag):
    """Remove the capture identity from the sidecar only (an incomplete writer)."""
    session_dir = Path(session_dir)
    manifest = load_json(session_dir / 'session-manifest.json')
    slot = slot_of(manifest, tag)
    sidecar = load_json(session_dir / slot['environment'])
    sidecar['environment']['capture_id'] = None
    save_json(session_dir / slot['environment'], sidecar)
    refresh_receipt(session_dir)
