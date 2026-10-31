"""Host orchestration of one board 11-frame 46 dB TRAIN/VAL session.

This is the host half of the collection path: it verifies the candidate release
BEFORE touching the network, uploads the reviewed board session driver, runs the
read-only board preflight, runs one authorized 11-frame session under the
existing control token, downloads the session directory verbatim and binds every
downloaded byte into a host download receipt the calibration adapter consumes.

    <output-root>/
      <session-id>/                       # downloaded board session directory
      download-receipt.json               # evidence_sha256: <session>/<artifact>
      collect-declaration-<session>.json  # what was authorized, before connecting
      preflight-<session>.json            # the board's read-only preflight result
      session-result-<session>.json       # the board run result (pass or named stop)
      run-<session>.log / preflight-<session>.log
      collect-receipt.json                # the orchestration receipt

Fail-closed authorization
-------------------------
collect() refuses with BOARD_COLLECTION_NOT_AUTHORIZED unless the caller passes
authorized=True, and the session entry passes it ONLY when the operator typed the
explicit --authorize-board-collection flag; a separate --cable-confirmed
attestation is required in every case.  Nothing here opens a socket, deploys or
consumes anything before both are present.  This entry is implemented and
offline-verified against a stub board link; it has never been pointed at a
physical board (NOT_RUN).

No retry, no re-collection: an existing <session-id> directory (or a download
receipt that already binds that session) is refused; a failed or partial session
is downloaded as evidence, validated locally by name and never re-attempted.
"""
import argparse
import datetime
import hashlib
import json
import shlex
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import merged_release as R  # noqa: E402
from scripts import rf_environment_calibration as CAL  # noqa: E402
from scripts import rf_environment_trainval_board_session as BOARD  # noqa: E402
from scripts import rf_environment_trainval_session as SESSION  # noqa: E402

RECEIPT_SCHEMA = 'rf-environment-trainval-collect-receipt-v1'
DOWNLOAD_RECEIPT_SCHEMA = 'rf-environment-trainval-download-receipt-v1'
DOWNLOAD_RECEIPT_FILE = 'download-receipt.json'
ENTRY = 'scripts/rf_environment_trainval_collect.py'
RELEASE_CONTRACT = BOARD.ENVIRONMENT_CONTRACT
BOARD_ENTRY_NAME = 'rf_environment_trainval_board_session.py'
# Everything the uploaded driver imports on the board (the release supplies the
# rf_* product modules; these are the reviewed scripts-side helpers).
BOARD_SOURCES = (
    BOARD_ENTRY_NAME,
    'rf_environment_trainval_session.py',
    'rf_environment_calibration.py',
    'rf_environment_dataset.py',
    'rf_environment_board_session.py',
    'rf_bpsk_ui_board_session.py',
    'rf_environment_pilot.py',
    'rf_bpsk_ui_acceptance.py',
)


class CollectError(SESSION.SessionError):
    """One named collection refusal; callers match on the code, never prose."""


def _require(condition, code, detail=''):
    if not condition:
        raise CollectError(code, detail)


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path):
    return _sha256_bytes(Path(path).read_bytes())


def _read_json(path, code, label):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise CollectError(code, '%s not found: %s' % (label, path))
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CollectError(code, '%s is not valid JSON: %s' % (label, exc))


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False))
    return path


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def candidate_declaration(candidate):
    """The candidate's own truth sources: profile domain, version, baseline.

    Every one of these is read from the candidate directory, never from the
    workstation tree, so a candidate that carries the historical 40 dB / v3
    baseline is refused by name before anything connects.
    """
    candidate = Path(candidate)
    try:
        profile = dict(CAL._profile_domain(candidate / 'rf_teaching_profile.py')[0])
    except CAL.CalibrationError as exc:
        raise CollectError('CANDIDATE_TRUTH_SOURCE_MISSING', exc.detail)
    with CAL.environment_in_domain(truth_source_dir=candidate) as environment:
        version = environment.VERSION
        fixed = dict(environment.FIXED_RF)
        body = _read_json(candidate / 'rf_environment_baseline.json',
                          'CANDIDATE_BASELINE_MISSING', 'candidate environment baseline')
        raw_config = body.get('rf_config') if type(body) is dict else None
        _require(type(raw_config) is dict and len(raw_config) == 8
                 and all(type(value) is int for value in raw_config.values()),
                 'CANDIDATE_BASELINE_INVALID',
                 'the candidate baseline does not carry the eight frozen RF pairs')
        _require(profile == fixed, 'CANDIDATE_DOMAIN_SPLIT',
                 'the candidate teaching profile and rf_environment declare different domains')
        _require(dict(raw_config) == fixed, 'CANDIDATE_BASELINE_DOMAIN_MISMATCH',
                 'the candidate baseline declares attenuation %r, its teaching profile %r; '
                 'collecting 46 dB frames against that baseline would only produce '
                 'RF_DOMAIN_MISMATCH frames'
                 % (dict(raw_config).get('attenuation_mdB'), fixed.get('attenuation_mdB')))
        _require(body.get('environment_version') == version,
                 'CANDIDATE_BASELINE_VERSION_MISMATCH',
                 'the candidate baseline is %s, its environment module is %s'
                 % (body.get('environment_version'), version))
        try:
            baseline = environment.EnvironmentBaseline.from_dict(body)
        except ValueError as exc:
            raise CollectError('CANDIDATE_BASELINE_INVALID', str(exc))
    return dict(
        domain=fixed, environment_version=version, attenuation_mdB=fixed['attenuation_mdB'],
        baseline_sha256=baseline.sha256,
        baseline_file_sha256=_file_sha256(candidate / 'rf_environment_baseline.json'),
        baseline_calibration_status=baseline.calibration_status,
        baseline_sources=len(baseline.sources),
        profile_file_sha256=_file_sha256(candidate / 'rf_teaching_profile.py'))


def session_directories(root):
    """Every non-hidden child directory of a download root is a session."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(child for child in root.iterdir()
                  if child.is_dir() and not child.is_symlink()
                  and not child.name.startswith('.'))


def build_download_receipt(root, release_id, existing=None):
    """Bind every downloaded byte, keyed <session>/<relative path>."""
    root = Path(root)
    evidence = dict(existing or {})
    sessions = []
    for directory in session_directories(root):
        sessions.append(directory.name)
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            key = '%s/%s' % (directory.name, path.relative_to(directory).as_posix())
            digest = _file_sha256(path)
            previous = evidence.get(key)
            _require(previous is None or previous == digest, 'DOWNLOAD_RECEIPT_CONFLICT',
                     'the download receipt already binds %r to another digest' % key)
            evidence[key] = digest
    return dict(schema=DOWNLOAD_RECEIPT_SCHEMA, entry=ENTRY, fixture=False, physical=True,
                release_id=release_id, sessions=sessions, evidence_sha256=evidence,
                created_utc=_utc_now(),
                scope='host download receipt over a board TRAIN/VAL collection root',
                notice='provenance is declared by the board session receipt; this receipt '
                       'binds bytes, it does not measure the board')


def write_download_receipt(root, release_id):
    """Merge the current session directories into <root>/download-receipt.json."""
    root = Path(root)
    path = root / DOWNLOAD_RECEIPT_FILE
    existing = None
    if path.is_file():
        document = _read_json(path, 'DOWNLOAD_RECEIPT_INVALID', 'download receipt')
        existing = document.get('evidence_sha256')
        _require(existing is None or type(existing) is dict, 'DOWNLOAD_RECEIPT_INVALID',
                 'an existing download receipt needs an evidence_sha256 mapping')
    receipt = build_download_receipt(root, release_id, existing)
    _write_json(path, receipt)
    return dict(path=str(path.resolve()), sha256=_file_sha256(path),
                sessions=receipt['sessions'], evidence_entries=len(receipt['evidence_sha256']))


# ------------------------------------------------------------------- board link

class SftpBoardLink:
    """Production link: SFTP upload, board command, verbatim download."""

    def __init__(self, client, helper, remote):
        self.client = client
        self.helper = helper
        self.remote = remote
        self.session_dir = remote + '/out'
        self.result_path = remote + '/session-result.json'
        self.preflight_path = remote + '/preflight.json'

    @classmethod
    def open(cls):
        from scripts.rf_teaching_deploy import load_helper
        helper = load_helper()
        client = helper.connect()
        remote = '/tmp/rf-trainval-collect-' + uuid.uuid4().hex
        try:
            with client.open_sftp() as sftp:
                sftp.mkdir(remote)
                sftp.mkdir(remote + '/scripts')
                # The board session writes straight into <stage>/out and never creates
                # it (LiveUiSession._save opens <tag>.iq/<tag>.json in place), so the
                # link owns the session directory it declares here.
                sftp.mkdir(remote + '/out')
        except Exception:
            client.close()
            raise
        return cls(client, helper, remote)

    def upload(self, local_path, name):
        with self.client.open_sftp() as sftp:
            sftp.put(str(local_path), self.remote + '/scripts/' + name)

    def run(self, argv, log, *, sudo=True, timeout=240):
        from scripts.rf_teaching_deploy import _run
        command = (('sudo -S ' if sudo else '')
                   + 'env PYTHONPATH=/home/xilinx:' + self.remote + ' XILINX_XRT=/usr python3 -u -B '
                   + ' '.join(shlex.quote(str(argument)) for argument in argv))
        return _run(self.client, self.helper, command, Path(log), timeout=timeout)

    def fetch(self, remote_path, local_path):
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with self.client.open_sftp() as sftp:
            sftp.get(remote_path, str(local_path))
        return local_path

    def download_tree(self, remote_dir, local_dir):
        """Best-effort recursive download; failed/partial files remain evidence."""
        local_dir = Path(local_dir)
        errors = []
        try:
            with self.client.open_sftp() as sftp:
                entries = sftp.listdir_attr(remote_dir)
                local_dir.mkdir(parents=True, exist_ok=True)
                for entry in entries:
                    if Path(entry.filename).name != entry.filename:
                        errors.append(dict(path=remote_dir, error='invalid member name'))
                        continue
                    source = remote_dir + '/' + entry.filename
                    target = local_dir / entry.filename
                    if entry.st_mode is not None and (entry.st_mode & 0o040000):
                        errors.extend(self.download_tree(source, target))
                        continue
                    try:
                        sftp.get(source, str(target))
                    except Exception as exc:
                        errors.append(dict(path=source, error=str(exc)))
        except Exception as exc:
            errors.append(dict(path=remote_dir, error=str(exc)))
        return errors

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------- collect

def collect(candidate, output_root, *, split, index, cable_confirmed=False,
            authorized=False, board_factory=None, timeout=900, preflight_timeout=120,
            collection_mode=SESSION.STANDARD_COLLECTION):
    """Run ONE authorized 11-frame board session and bind its download.

    The caller owns authorization: this function refuses unless authorized=True
    (the session entry sets it only for the explicit operator flag).  The declared
    collection mode is passed to the board entry and enforced on the download: the
    session that comes back has to declare the same mode, so a campaign can never
    silently collect against the wrong label rule.
    """
    collection_mode = SESSION.check_collection_mode(collection_mode)
    _require(authorized is True, 'BOARD_COLLECTION_NOT_AUTHORIZED',
             'physical collection needs the explicit host authorization flag; the first '
             'attempt to a board is a separately authorized job, and this call is refused '
             'before any connection, upload or token use')
    _require(cable_confirmed is True, 'CABLE_CONFIRMATION_REQUIRED',
             'the TX1-RX1 cable confirmation must be attested explicitly')
    plan = SESSION.plan_document(split, index)
    session_block = plan['session']
    session_id, seed = session_block['session_id'], session_block['seed']
    candidate = Path(candidate).resolve()
    try:
        manifest = R.verify_release(candidate, require_software=True)
    except ValueError as exc:
        raise CollectError('CANDIDATE_NOT_VERIFIED', str(exc))
    _require(manifest.get('software_contract') == RELEASE_CONTRACT,
             'RELEASE_CONTRACT_MISMATCH',
             'a 46 dB TRAIN/VAL session runs on the %s software contract; this candidate '
             'declares %r' % (RELEASE_CONTRACT, manifest.get('software_contract')))
    release_id = manifest['release_id']
    declaration = candidate_declaration(candidate)
    output_root = Path(output_root)
    session_dir = output_root / session_id
    _require(not session_dir.exists(), 'SESSION_ALREADY_COLLECTED',
             '%s already holds a collected session; a stopped or failed session is never '
             're-collected or overwritten' % session_dir)
    receipt_path = output_root / DOWNLOAD_RECEIPT_FILE
    if receipt_path.is_file():
        existing_receipt = _read_json(receipt_path, 'DOWNLOAD_RECEIPT_INVALID', 'download receipt')
        _require(session_id not in (existing_receipt.get('sessions') or []),
                 'SESSION_ALREADY_COLLECTED',
                 'the download receipt already binds %s' % session_id)
    output_root.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / 'scripts' / name for name in BOARD_SOURCES]
    declaration_path = _write_json(output_root / ('collect-declaration-%s.json' % session_id),
                                   dict(
        schema='rf-environment-trainval-collect-declaration-v1', entry=ENTRY,
        authorized=True, authorization='explicit operator flag --authorize-board-collection',
        cable_confirmation='explicit --cable-confirmed on the host; the production loopback '
                           'path asks for the two-hit confirmation per physical frame',
        candidate=str(candidate), release_id=release_id,
        software_contract=manifest.get('software_contract'),
        domain=declaration['domain'], environment_version=declaration['environment_version'],
        baseline_sha256=declaration['baseline_sha256'],
        split=split, index=index, session_id=session_id, seed=seed,
        collection_mode=collection_mode,
        frames=[dict(tag=slot['tag'], scenario=slot['scenario'],
                     physical=slot['scenario'] == 'normal') for slot in plan['slots']],
        stop_conditions=list(BOARD.STOP_CONDITIONS),
        source_hashes={source.name: _file_sha256(source) for source in sources},
        created_utc=_utc_now()))
    result = dict(
        schema=RECEIPT_SCHEMA, entry=ENTRY, passed=False, authorized=True,
        candidate=dict(path=str(candidate), release_id=release_id,
                       software_contract=manifest.get('software_contract'),
                       domain=declaration['domain'],
                       environment_version=declaration['environment_version'],
                       baseline_sha256=declaration['baseline_sha256'],
                       baseline_file_sha256=declaration['baseline_file_sha256'],
                       baseline_calibration_status=declaration['baseline_calibration_status'],
                       baseline_sources=declaration['baseline_sources']),
        declaration=str(declaration_path.resolve()),
        split=split, index=index, session_id=session_id, seed=seed,
        collection_mode=collection_mode,
        session=dict(directory=str(session_dir.resolve())),
        board=dict(remote=None, preflight=None, result=None, exit_code=None,
                   preflight_exit_code=None, sources=len(sources)),
        download=dict(errors=[], files=0), validation=dict(passed=False, error=None),
        adapter_ready=False, stop_cause=None, created_utc=_utc_now(),
        not_run=['physical TRAIN/VAL collection: this run is the first authorized board '
                 'attempt and has NOT been executed in this round',
                 'development pilot: NOT_RUN', 'formal TEST: NOT_RUN',
                 'human acceptance: NOT_RUN'],
        limits=['the download receipt binds bytes and declared provenance; it cannot detect a '
                'deliberately forged board-side manifest/receipt chain',
                'the host never re-measures the board, the labels or the thresholds'])
    link, board_result = None, None
    try:
        link = (board_factory or SftpBoardLink.open)()
        result['board']['remote'] = getattr(link, 'remote', None)
        for source in sources:
            link.upload(source, source.name)
        driver = '%s/scripts/%s' % (link.remote, BOARD_ENTRY_NAME)
        preflight_command = [driver, '--preflight',
                             '--expected-release', release_id,
                             '--split', split, '--session', str(index),
                             '--collection-mode', collection_mode,
                             '--result', link.preflight_path, '--cable-confirmed']
        # The installed release directories are root-only, so an unprivileged preflight
        # cannot even import merged_release and writes no result document at all (the
        # 2026-10-18 board preflight: ModuleNotFoundError, exit 1, no JSON).  The
        # preflight is strictly read-only (no client, no socket, no RF), so it runs under
        # the same privilege as the session it gates.
        result['board']['preflight_exit_code'] = link.run(
            preflight_command, output_root / ('preflight-%s.log' % session_id),
            sudo=True, timeout=preflight_timeout)
        try:
            link.fetch(link.preflight_path, output_root / ('preflight-%s.json' % session_id))
        except Exception as exc:
            # Fail closed: with no result document the preflight was never evaluated, so
            # nothing may be dispatched on this run.
            result['board']['preflight'] = dict(error=str(exc))
            raise CollectError(
                'BOARD_PREFLIGHT_NOT_EVALUATED',
                'the board preflight wrote no result document (exit code %r, %s): the '
                'release/domain/baseline gate was never evaluated, so no capture was '
                'started' % (result['board']['preflight_exit_code'], exc))
        preflight = _read_json(output_root / ('preflight-%s.json' % session_id),
                               'BOARD_PREFLIGHT_MISSING', 'board preflight result')
        result['board']['preflight'] = dict(
            ok=preflight.get('ok'), release_id=preflight.get('release_id'),
            software_contract=preflight.get('software_contract'),
            domain_attenuation_mdB=(preflight.get('domain') or {}).get('attenuation_mdB'),
            environment_version=preflight.get('environment_version'),
            baseline_sha256=preflight.get('baseline_sha256'),
            baseline_calibration_status=preflight.get('baseline_calibration_status'))
        _require(result['board']['preflight_exit_code'] == 0
                 and preflight.get('ok') is True, 'BOARD_PREFLIGHT_FAILED',
                 'the board preflight refused: %r' % (preflight,))
        _require(preflight.get('release_id') == release_id
                 and preflight.get('software_contract') == RELEASE_CONTRACT
                 and dict(preflight.get('domain') or {}) == declaration['domain']
                 and preflight.get('environment_version') == declaration['environment_version']
                 and preflight.get('collection_mode') == collection_mode,
                 'BOARD_PREFLIGHT_MISMATCH',
                 'the installed release domain/version differs from the candidate '
                 'declaration; no capture was started')
        session_command = [driver,
                           '--expected-release', release_id,
                           '--split', split, '--session', str(index),
                           '--collection-mode', collection_mode,
                           '--output', link.session_dir,
                           '--result', link.result_path,
                           '--cable-confirmed']
        result['board']['exit_code'] = link.run(
            session_command, output_root / ('run-%s.log' % session_id),
            sudo=True, timeout=timeout)
        result['download']['errors'] = list(link.download_tree(link.session_dir, session_dir))
        try:
            link.fetch(link.result_path, output_root / ('session-result-%s.json' % session_id))
        except Exception as exc:
            result['download']['errors'].append(dict(path=link.result_path, error=str(exc)))
    except SESSION.SessionError as exc:
        result['stop_cause'] = exc.code
        result['error'] = exc.detail
    except Exception as exc:
        result['stop_cause'] = 'BOARD_LINK_FAILED'
        result['error'] = '%s: %s' % (type(exc).__name__, exc)
    finally:
        if link is not None:
            try:
                link.close()
            except Exception:
                pass
    result['download']['files'] = sum(1 for path in session_dir.rglob('*')
                                      if path.is_file() and not path.is_symlink()) \
        if session_dir.is_dir() else 0
    board_result_path = output_root / ('session-result-%s.json' % session_id)
    if board_result_path.is_file():
        board_result = _read_json(board_result_path, 'SESSION_RESULT_INVALID',
                                  'board session result')
        result['board']['result'] = dict(
            passed=board_result.get('passed'), stop_cause=board_result.get('stop_cause'),
            stop_frame=board_result.get('stop_frame'), error=board_result.get('error'),
            collection_mode=board_result.get('collection_mode'),
            scenes=board_result.get('scenes') or [],
            manifest_written=board_result.get('manifest_written'),
            session_closed=board_result.get('session_closed'),
            disarm_ack=board_result.get('disarm_ack'),
            release_id=board_result.get('release_id'),
            frames=board_result.get('frames') or [])
        if result['stop_cause'] is None and board_result.get('stop_cause') is not None:
            result['stop_cause'] = board_result['stop_cause']
        if board_result.get('error'):
            result['error'] = board_result['error']
    elif result['board']['exit_code'] is not None and result['stop_cause'] is None:
        # The session command ran but left no result document, so how it ended is not
        # evaluated at all; that is named here, never reported as a silent false.
        result['stop_cause'] = 'BOARD_SESSION_NOT_EVALUATED'
        result['error'] = ('the board session left no %s (exit code %r); the run log holds '
                           'whatever the board printed'
                           % (board_result_path.name, result['board']['exit_code']))
    # Local, download-side validation: the same check the calibration adapter runs.
    try:
        summary = SESSION.check_session(session_dir)
        # The session that came back has to declare the mode this host dispatched.
        # A mismatch means the board entry ran under a different label rule, so the
        # download is refused rather than adapted.
        _require(summary['collection_mode'] == collection_mode,
                 'SESSION_COLLECTION_MODE_MISMATCH',
                 'the downloaded session declares collection_mode %r, this collection '
                 'dispatched %r' % (summary['collection_mode'], collection_mode))
    except SESSION.SessionError as exc:
        result['validation'] = dict(passed=False, error=dict(code=exc.code, detail=exc.detail))
    else:
        result['validation'] = dict(
            passed=True, session_id=summary['session_id'], split=summary['split'],
            seed=summary['seed'], collection_mode=summary['collection_mode'],
            provisional_unknown_count=summary['provisional_unknown_count'],
            frame_count=summary['frame_count'],
            normal_count=summary['normal_count'], off_count=summary['off_count'],
            session_closed=summary['session_closed'], disarm_ack=summary['disarm_ack'],
            manifest_sha256=summary['manifest_sha256'], receipt_sha256=summary['receipt_sha256'],
            frames=[dict(tag=entry['tag'], scenario=entry['scenario'],
                         iq_sha256=entry['iq_sha256'], capture_id=entry['capture_id'])
                    for entry in summary['frames']])
        result['session'].update(dict(
            manifest_sha256=summary['manifest_sha256'],
            receipt_sha256=summary['receipt_sha256'],
            session_closed=summary['session_closed'], disarm_ack=summary['disarm_ack'],
            frame_count=summary['frame_count']))
    receipt_info = write_download_receipt(output_root, release_id)
    result['download']['receipt'] = receipt_info
    result['sessions_in_root'] = receipt_info['sessions']
    result['adapter_ready'] = bool(len(receipt_info['sessions']) == 3)
    result['passed'] = bool(
        result['board']['exit_code'] == 0
        and not result['download']['errors']
        and result['validation']['passed'] is True
        and (board_result or {}).get('passed') is True)
    _write_json(output_root / 'collect-receipt.json', result)
    return result


def summarize(result):
    return dict(passed=result['passed'], stop_cause=result['stop_cause'],
                collection_mode=result.get('collection_mode'),
                session_id=result['session_id'], split=result['split'], index=result['index'],
                release_id=result['candidate']['release_id'],
                domain_attenuation_mdB=result['candidate']['domain']['attenuation_mdB'],
                environment_version=result['candidate']['environment_version'],
                session=result['session'], board=result['board'],
                download=result['download'], validation=result['validation'],
                adapter_ready=result['adapter_ready'], error=result.get('error'),
                not_run=result['not_run'], limits=result['limits'])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--candidate', type=Path,
                        help='verified release directory whose baseline is the 46 dB domain')
    parser.add_argument('--output', type=Path, required=True,
                        help='download root; the session lands under <root>/<session-id>/')
    parser.add_argument('--split', choices=SESSION.SPLITS, required=True)
    parser.add_argument('--session', type=int, required=True)
    parser.add_argument('--cable-confirmed', action='store_true')
    parser.add_argument('--authorize-board-collection', action='store_true',
                        help='the explicit authorization switch for a physical board session')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--collection-mode', choices=SESSION.COLLECTION_MODES,
                        default=SESSION.STANDARD_COLLECTION,
                        help='the label rule this collection dispatches to the board entry '
                             '(standard_acceptance by default; calibration_collection for the '
                             'TRAIN/VAL matrix collected against the pre-fit baseline)')
    arguments = parser.parse_args(argv)
    try:
        if not arguments.authorize_board_collection:
            raise CollectError(
                'BOARD_COLLECTION_NOT_AUTHORIZED',
                'physical collection needs --authorize-board-collection; without it this '
                'entry refuses before any connection, upload or token use')
        if arguments.candidate is None:
            raise CollectError('CANDIDATE_REQUIRED', '--candidate <release directory> is required')
        result = collect(arguments.candidate, arguments.output, split=arguments.split,
                         index=arguments.session, cable_confirmed=arguments.cable_confirmed,
                         authorized=arguments.authorize_board_collection,
                         timeout=arguments.timeout,
                         collection_mode=arguments.collection_mode)
    except CAL.CalibrationError as exc:
        print(json.dumps(exc.to_dict(), indent=2, ensure_ascii=True))
        return 3
    print(json.dumps(summarize(result), indent=2, ensure_ascii=True, allow_nan=False,
                     sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
