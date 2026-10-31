"""Downloaded 11-frame TRAIN/VAL sessions -> the calibration set.json shape.

Offline only: this adapter reads a download tree, binds every frame byte to its
declared digest, the session receipt and (for physical runs) the host download
receipt, and writes a calibration set that scripts/rf_environment_calibration.py
fit consumes unchanged.  It never opens a socket, deploys, initializes RF, emits,
captures or consumes an authorization/nonce.

Input (one directory per session, three sessions in total)
-----------------------------------------------------------
    <download-root>/
      train-01/  session-manifest.json, normal-01..10 + off-01 artifacts, receipt.json
      train-02/  ...
      val-01/    ...
      download-receipt.json     # optional for fixtures, REQUIRED for physical runs

The per-session layout is produced by scripts/rf_environment_trainval_session.py.
A host download receipt must carry {"evidence_sha256": {"<session>/<file>": sha}};
when present every artifact is cross-checked against it, and a physical adaptation
is refused without it.

Output
------
    <output>/set.json            # plan/domain/environment_version/sessions/provenance
    <output>/frames/<id>.iq      # byte-identical copy of the downloaded frame
    <output>/frames/<id>.json    # calibration frame sidecar (identity + submitted demod)
    <output>/adapter-receipt.json

Rules (every refusal is a stable code)
--------------------------------------
* exactly two TRAIN sessions (seeds 1, 37) and one VAL session (seed 73); a session
  owns one split and a frame may never contradict it (SESSION_SPLIT_PLAN,
  SESSION_FRAME_SESSION_MISMATCH -- this is how a VAL frame placed in a TRAIN
  directory is refused);
* no capture identity or IQ digest may appear in two splits (CROSS_SPLIT_LEAKAGE);
  a repeat inside one split is a duplicate identity (DUPLICATE_FRAME_IDENTITY);
  capture identity is the complete five-tuple, since local capture_id counters
  restart when a new client connects;
* declared digest == file bytes == session receipt == host download receipt
  (SESSION_FRAME_IQ_SHA_MISMATCH, DOWNLOAD_RECEIPT_SHA_MISMATCH,
  DOWNLOAD_RECEIPT_FRAME_UNBOUND);
* every frame carries the eight frozen RF pairs and the v4 environment version of
  the installed product (DOMAIN_MISMATCH, ENVIRONMENT_VERSION_MISMATCH);
* split is explicit on every session and frame; only normal/off frames may enter
  TRAIN/VAL (SCENARIO_NOT_ALLOWED, SESSION_SCENARIO_MIX);
* a fixture download can only produce an explicitly marked fixture set: the set
  carries fixture=true, collected=false, physical_frames=0 and
  not_for_formal_acceptance=true, and --physical is refused
  (PHYSICAL_PROVENANCE_CONFLICT).  A set can only claim new_physical_train_val when
  every session is a closed, disarmed, collected board session, release ids agree
  and a host download receipt is supplied; otherwise PHYSICAL_PROVENANCE_UNVERIFIED
  / DOWNLOAD_RECEIPT_MISSING / PHYSICAL_SESSION_NOT_CLOSED.

No claim: adapting is not measuring.  A fixture set is not TRAIN/VAL evidence; a
physical set only declares provenance it was handed and re-binds it to the receipts.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import rf_environment_calibration as CAL  # noqa: E402
from scripts import rf_environment_trainval_session as SESSION  # noqa: E402

RECEIPT_SCHEMA = 'rf-environment-trainval-adapter-receipt-v1'
DOWNLOAD_RECEIPT_FILE = 'download-receipt.json'
SET_SCHEMA = CAL.SET_SCHEMA
SEEDS = CAL.PLAN_SEEDS
FRAMES_PER_SESSION = CAL.PLAN_FRAMES_PER_SESSION
NORMAL_PER_SESSION = CAL.PLAN_NORMAL_PER_SESSION
CAPTURE_IDENTITY_FIELDS = ('capture_id', 'release_id', 'connection_generation',
                           'config_revision', 'completed_at_monotonic')


class AdapterError(CAL.CalibrationError):
    """One named adaptation refusal; callers match on the code, never on prose."""


def _require(condition, code, detail=''):
    if not condition:
        raise AdapterError(code, detail)


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path):
    return _sha256_bytes(Path(path).read_bytes())


def _sha_literal(value):
    return CAL._sha_literal(value)


def _read_json(path, code, label):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise AdapterError(code, '%s not found: %s' % (label, path))
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AdapterError(code, '%s is not valid JSON: %s' % (label, exc))


def _write_json(path, value, *, overwrite=False):
    path = Path(path)
    if path.exists() and not overwrite:
        raise AdapterError('OUTPUT_EXISTS', str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False))
    return path


def _write_bytes(path, payload, *, overwrite=False):
    path = Path(path)
    if path.exists() and not overwrite:
        raise AdapterError('OUTPUT_EXISTS', str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def discover_sessions(download_root):
    """Every non-hidden child directory is a session; nothing else is counted."""
    root = Path(download_root)
    if not root.is_dir() or root.is_symlink():
        raise AdapterError('DOWNLOAD_ROOT_MISSING', str(root))
    directories = [child for child in sorted(root.iterdir())
                   if child.is_dir() and not child.is_symlink()
                   and not child.name.startswith('.')]
    _require(len(directories) == len(SEEDS['train']) + len(SEEDS['val']),
             'DOWNLOAD_SESSION_COUNT',
             'a TRAIN/VAL download holds exactly %d session directories, found %d'
             % (len(SEEDS['train']) + len(SEEDS['val']), len(directories)))
    return root, directories


def order_sessions(sessions):
    """Canonical TRAIN(1, 37) + VAL(73) order; a wrong composition is named."""
    ordered = {}
    for split, expected in (('train', SEEDS['train']), ('val', SEEDS['val'])):
        group = sorted([session for session in sessions if session['split'] == split],
                       key=lambda session: session['index'])
        _require([session['seed'] for session in group] == expected,
                 'SESSION_SPLIT_PLAN',
                 '%s needs %d session(s) with seeds %r, found %r'
                 % (split, len(expected), expected,
                    [(session['split'], session.get('seed')) for session in sessions]))
        ordered[split] = group
    return ordered


def decide_provenance(sessions, physical=False):
    """Pure decision over validated session summaries; returns (kind, fixture block).

    Separated from file handling so the physical branch can be reasoned about and
    unit-tested without ever producing a synthetic "physical" artifact.
    """
    _require(bool(sessions), 'DOWNLOAD_SESSION_COUNT', 'no session summaries')
    for session in sessions:
        for key in ('fixture', 'collected', 'physical'):
            _require(type(session.get(key)) is bool, 'SESSION_FLAG_CONFLICT',
                     'session %r needs boolean %r' % (session.get('session_id'), key))
    fixtures = {session['fixture'] for session in sessions}
    _require(len(fixtures) == 1, 'SESSION_FLAG_CONFLICT',
             'every session of one set must agree on fixture=true/false')
    if fixtures.pop():
        if physical:
            raise AdapterError(
                'PHYSICAL_PROVENANCE_CONFLICT',
                'these sessions are offline fixtures; a fixture can never be relabelled '
                'as new_physical_train_val')
        _require(not any(session['collected'] or session['physical'] for session in sessions),
                 'SESSION_FLAG_CONFLICT',
                 'a fixture session cannot declare collected/physical frames')
        return 'offline_synthetic_fixture', dict(
            fixture=True, collected=False, physical_frames=0,
            not_for_formal_acceptance=True,
            notice='offline synthetic frames; not physical TRAIN/VAL evidence')
    _require(all(session['collected'] and session['physical'] for session in sessions),
             'SESSION_FLAG_CONFLICT',
             'a non-fixture session must declare collected=true and physical=true')
    if not physical:
        raise AdapterError(
            'PHYSICAL_PROVENANCE_UNVERIFIED',
            'the download declares collected physical sessions; pass --physical and a '
            'download receipt to bind that provenance, or the set stays unadapted')
    for session in sessions:
        _require(session.get('session_closed') is True
                 and session.get('disarm_ack') is True, 'PHYSICAL_SESSION_NOT_CLOSED',
                 'session %r is not closed/disarmed; a physical set cannot be built'
                 % (session.get('session_id'),))
    releases = {session.get('release_id') for session in sessions}
    _require(len(releases) == 1 and all(type(value) is str and value
                                        for value in releases), 'RELEASE_ID_MISMATCH',
             'physical sessions must share one non-empty release id, got %r'
             % (sorted(str(value) for value in releases),))
    return 'new_physical_train_val', dict(
        fixture=False, collected=True,
        physical_frames=len(sessions) * FRAMES_PER_SESSION,
        not_for_formal_acceptance=False,
        notice='declared board sessions, re-bound to the supplied download receipt')


def _receipt_evidence(receipt):
    evidence = receipt.get('evidence_sha256')
    _require(type(evidence) is dict and evidence, 'DOWNLOAD_RECEIPT_SCHEMA',
             'a download receipt needs a non-empty evidence_sha256 mapping')
    for key, value in evidence.items():
        _require(type(key) is str and key and not key.startswith('/')
                 and '\\' not in key and '..' not in key.split('/')
                 and _sha_literal(value), 'DOWNLOAD_RECEIPT_SCHEMA',
                 'download receipt keys must be relative paths and values full '
                 'sha256 digests (%r)' % (key,))
    return dict(evidence)


def load_download_receipt(path, reference, fixture):
    """Read and cross-check a host download receipt against the session tree."""
    document = _read_json(path, 'DOWNLOAD_RECEIPT_MISSING', 'download receipt')
    _require(type(document) is dict, 'DOWNLOAD_RECEIPT_SCHEMA',
             'a download receipt must be a JSON object')
    evidence = _receipt_evidence(document)
    if 'fixture' in document:
        _require(document['fixture'] is fixture, 'DOWNLOAD_RECEIPT_FLAG_MISMATCH',
                 'download receipt fixture=%r, the sessions declare %r'
                 % (document['fixture'], fixture))
    if 'physical' in document:
        _require(document['physical'] is (not fixture), 'DOWNLOAD_RECEIPT_FLAG_MISMATCH',
                 'download receipt physical=%r disagrees with the sessions'
                 % (document['physical'],))
    if 'release_id' in document:
        releases = {session.get('release_id') for session in reference}
        _require(len(releases) == 1 and document['release_id'] in releases,
                 'RELEASE_ID_MISMATCH',
                 'download receipt release %r is not the single session release %r'
                 % (document['release_id'], sorted(str(value) for value in releases)))
    for session in reference:
        for name, digest in sorted(session['artifact_digests'].items()):
            key = '%s/%s' % (session['download_name'], name)
            _require(key in evidence, 'DOWNLOAD_RECEIPT_FRAME_UNBOUND',
                     'download receipt does not bind %r' % key)
            _require(evidence[key] == digest, 'DOWNLOAD_RECEIPT_SHA_MISMATCH',
                     'download receipt binds %r to %s, the file hashes to %s'
                     % (key, evidence[key], digest))
    return dict(path=str(Path(path).resolve()), sha256=_file_sha256(path),
                evidence_entries=len(evidence))


def _sidecar(entry, session, domain, download_rel):
    return dict(
        schema=CAL.FRAME_SCHEMA, frame_id=entry['frame_id'],
        session_id=session['session_id'], split=session['split'],
        scenario=entry['scenario'], seed=entry['seed'],
        reference_prbs7_seed=entry['reference_prbs7_seed'],
        iq_sha256=entry['iq_sha256'], samples=4096, sample_rate_hz=60000000,
        ratio=0, rf=dict(domain), identity=dict(entry['identity'],
            physical=entry.get('physical', True), op=entry.get('op', 'rx.capture')),
        submitted=dict(entry['submitted']),
        # How this frame was collected and what the pre-fit baseline called it.  The
        # label is preserved verbatim; a calibration-collected provisional UNKNOWN is
        # never rewritten into CLEAR here or anywhere downstream.
        collection_mode=session.get('collection_mode'),
        collected_label=entry.get('collected_label'),
        collected_reason=entry.get('collected_reason'),
        provisional_unknown=bool(entry.get('provisional_unknown')),
        truth_declaration=dict(entry['scenario_declaration']),
        session=dict(session_id=session['session_id'], split=session['split'],
                     seed=session['seed']),
        fixture=session['fixture'], collected=session['collected'],
        physical=session['physical'],
        download=dict(path=download_rel, sha256=entry['iq_sha256'],
                      metadata_sha256=entry['metadata_sha256'],
                      environment_sha256=entry['environment_sha256']),
        origin=('offline synthetic fixture frame; not a physical capture'
                if session['fixture'] else
                'board download frame; provenance declared by the session receipt'))


def adapt(download_root, output_dir, *, download_receipt=None, physical=False,
          overwrite=False):
    """Bind a three-session download into <output>/set.json plus its receipt."""
    root, directories = discover_sessions(download_root)
    sessions = []
    for directory in directories:
        summary = SESSION.check_session(directory)
        summary['download_name'] = directory.name
        summary['download_root'] = str(root.resolve())
        summary['download_path'] = str(directory.resolve())
        summary['artifact_digests'] = {}
        for entry in summary['frames']:
            for key, name in (('iq', entry['iq']), ('metadata', entry['metadata']),
                              ('environment', entry['environment'])):
                summary['artifact_digests'][name] = entry[key + '_sha256']
        sessions.append(summary)

    seen_ids = {}
    for session in sessions:
        previous = seen_ids.setdefault(session['session_id'], session['download_name'])
        _require(previous == session['download_name'], 'DUPLICATE_SESSION_ID',
                 'session id %r appears in %r and %r'
                 % (session['session_id'], previous, session['download_name']))
    ordered = order_sessions(sessions)
    sequence = ordered['train'] + ordered['val']

    # One set, one collection mode: a mixed set cannot be described honestly, so it
    # is refused instead of being reduced to the least strict member.
    modes = {session.get('collection_mode') for session in sequence}
    _require(len(modes) == 1, 'COLLECTION_MODE_MISMATCH',
             'the sessions declare different collection modes: %r'
             % sorted(str(mode) for mode in modes))
    collection_mode = SESSION.check_collection_mode(modes.pop())
    provisional_frames = ['%s/%s' % (session['session_id'], entry['tag'])
                          for session in sequence for entry in session['frames']
                          if entry.get('provisional_unknown')]

    product_domain = dict(CAL._profile_domain(CAL.TEACHING_PROFILE)[0])
    with CAL.environment_in_domain() as environment:
        fixed = dict(environment.FIXED_RF)
        environment_version = environment.VERSION
    for session in sequence:
        _require(session['domain'] == fixed, 'DOMAIN_MISMATCH',
                 'session %r declares attenuation %r, the installed product domain is %r'
                 % (session['session_id'],
                    (session['domain'] or {}).get('attenuation_mdB'),
                    fixed.get('attenuation_mdB')))
        _require(session['environment_version'] == environment_version,
                 'ENVIRONMENT_VERSION_MISMATCH',
                 'session %r declares %r, the installed product declares %r'
                 % (session['session_id'], session['environment_version'],
                    environment_version))

    kind, fixture_block = decide_provenance(sequence, physical=physical)

    receipt_info = None
    receipt_path = (Path(download_receipt) if download_receipt
                    else root / DOWNLOAD_RECEIPT_FILE)
    if download_receipt is not None or receipt_path.is_file():
        receipt_info = load_download_receipt(receipt_path, sequence,
                                             sequence[0]['fixture'])
    elif physical:
        raise AdapterError('DOWNLOAD_RECEIPT_MISSING',
                           'a physical adaptation requires a host download receipt')

    captures, digests = {}, {}
    for session in sequence:
        for entry in session['frames']:
            key = tuple(entry['identity'][field] for field in CAPTURE_IDENTITY_FIELDS)
            previous = captures.get(key)
            if previous is not None:
                if previous[0] != session['split']:
                    raise AdapterError(
                        'CROSS_SPLIT_LEAKAGE',
                        'capture %r appears in %s (%s) and %s (%s)'
                        % (key, previous[0], previous[1], session['split'],
                           session['session_id']))
                raise AdapterError('DUPLICATE_FRAME_IDENTITY',
                                   'capture %r is used by %s and %s'
                                   % (key, previous[1], entry['frame_id']))
            captures[key] = (session['split'], entry['frame_id'])
            digest = entry['iq_sha256']
            previous = digests.get(digest)
            if previous is not None:
                if previous[0] != session['split']:
                    raise AdapterError(
                        'CROSS_SPLIT_LEAKAGE',
                        'IQ digest %s appears in %s (%s) and %s (%s)'
                        % (digest, previous[0], previous[1], session['split'],
                           session['session_id']))
                raise AdapterError('DUPLICATE_FRAME_IDENTITY',
                                   'IQ digest %s is used by %s and %s'
                                   % (digest, previous[1], entry['frame_id']))
            digests[digest] = (session['split'], entry['frame_id'])

    output_dir = Path(output_dir)
    if output_dir.exists() and not overwrite:
        raise AdapterError('OUTPUT_EXISTS', str(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'frames').mkdir(parents=True, exist_ok=True)
    rows, total = [], {'frames': 0, 'normal': 0, 'off': 0}
    for session in sequence:
        frame_rows = []
        for entry in session['frames']:
            source = Path(session['download_path']) / entry['iq']
            raw = source.read_bytes()
            actual = _sha256_bytes(raw)
            _require(actual == entry['iq_sha256'], 'SESSION_FRAME_IQ_SHA_MISMATCH',
                     'frame %s changed after validation' % entry['frame_id'])
            iq_rel = 'frames/%s.iq' % entry['frame_id']
            sidecar_rel = 'frames/%s.json' % entry['frame_id']
            _write_bytes(output_dir / iq_rel, raw, overwrite=overwrite)
            download_rel = '%s/%s' % (session['download_name'], entry['iq'])
            _write_json(output_dir / sidecar_rel,
                        _sidecar(entry, session, product_domain, download_rel),
                        overwrite=overwrite)
            frame_rows.append(dict(
                frame_id=entry['frame_id'], source_id=entry['source_id'],
                scenario=entry['scenario'], seed=entry['seed'], split=session['split'],
                iq=iq_rel, metadata=sidecar_rel, iq_sha256=actual,
                download=dict(path=download_rel, sha256=actual,
                              metadata_path='%s/%s' % (session['download_name'],
                                                       entry['metadata']),
                              metadata_sha256=entry['metadata_sha256'],
                              environment_path='%s/%s' % (session['download_name'],
                                                           entry['environment']),
                              environment_sha256=entry['environment_sha256'],
                              capture_id=entry['identity']['capture_id'])))
            total['frames'] += 1
            total[entry['scenario']] += 1
        rows.append(dict(
            session_id=session['session_id'], split=session['split'],
            seed=session['seed'], collection_mode=session.get('collection_mode'),
            provisional_unknown_count=session.get('provisional_unknown_count'),
            frames=frame_rows,
            fixture=session['fixture'], collected=session['collected'],
            physical=session['physical'], release_id=session['release_id'],
            download=dict(path=session['download_name'],
                          manifest_sha256=session['manifest_sha256'],
                          receipt_sha256=session['receipt_sha256'],
                          session_closed=session['session_closed'],
                          disarm_ack=session['disarm_ack'])))
    provenance_detail = (
        'offline synthetic fixture sessions written by '
        'scripts/rf_environment_trainval_session.py fixture; zero physical frames'
        if kind == 'offline_synthetic_fixture' else
        'three downloaded board sessions (2 TRAIN + 1 VAL) re-bound to their session '
        'and download receipts; provenance is declared, not measured by this adapter')
    not_run = ['physical collection by this adapter: NOT_RUN',
               'development pilot: NOT_RUN', 'formal TEST: NOT_RUN',
               'human acceptance: NOT_RUN']
    if kind == 'offline_synthetic_fixture':
        not_run.insert(0, 'physical TRAIN/VAL sessions: NOT_RUN (offline fixture)')
    set_document = dict(
        schema=SET_SCHEMA, plan=CAL._plan_summary(), domain=dict(product_domain),
        environment_version=environment_version, collection_mode=collection_mode,
        provisional_unknown_frames=list(provisional_frames), sessions=rows,
        provenance=dict(kind=kind, detail=provenance_detail),
        fixture=dict(fixture_block),
        not_for_formal_acceptance=fixture_block['not_for_formal_acceptance'],
        adapter=dict(
            entry='scripts/rf_environment_trainval_adapter.py adapt',
            download_root=str(root.resolve()),
            download_receipt=(dict(path=receipt_info['path'],
                                   sha256=receipt_info['sha256'],
                                   evidence_entries=receipt_info['evidence_entries'])
                              if receipt_info else None),
            domain_attenuation_mdB=product_domain['attenuation_mdB'],
            environment_version=environment_version,
            session_order=[session['session_id'] for session in sequence]),
        not_run=not_run)
    set_path = _write_json(output_dir / 'set.json', set_document, overwrite=overwrite)
    set_sha = _file_sha256(set_path)
    receipt = dict(
        schema=RECEIPT_SCHEMA, entry='scripts/rf_environment_trainval_adapter.py adapt',
        scope='offline adaptation of downloaded 11-frame sessions; no board/RF/authorization',
        provenance=dict(kind=kind, detail=provenance_detail),
        fixture=dict(fixture_block),
        not_for_formal_acceptance=fixture_block['not_for_formal_acceptance'],
        domain=dict(product_domain), environment_version=environment_version,
        collection_mode=collection_mode,
        provisional_unknown_frames=list(provisional_frames),
        download_root=str(root.resolve()),
        download_receipt=(receipt_info if receipt_info else None),
        counts=dict(sessions=len(sequence),
                    train_sessions=len(ordered['train']),
                    val_sessions=len(ordered['val']),
                    frames=total['frames'], normal=total['normal'], off=total['off'],
                    train_normal=len(ordered['train']) * NORMAL_PER_SESSION,
                    val_normal=len(ordered['val']) * NORMAL_PER_SESSION,
                    test_frames=0),
        set=dict(path=str(set_path.resolve()), sha256=set_sha),
        sessions=[dict(
            session_id=session['session_id'], split=session['split'],
            seed=session['seed'], collection_mode=session.get('collection_mode'),
            download_path=session['download_path'],
            manifest_sha256=session['manifest_sha256'],
            receipt_sha256=session['receipt_sha256'],
            session_closed=session['session_closed'],
            disarm_ack=session['disarm_ack'],
            frames=[dict(frame_id=entry['frame_id'], tag=entry['tag'],
                         scenario=entry['scenario'], iq=entry['iq'],
                         iq_sha256=entry['iq_sha256'],
                         capture_id=entry['identity']['capture_id'],
                         source_path=str((Path(session['download_path'])
                                          / entry['iq']).resolve()))
                    for entry in session['frames']]) for session in sequence],
        not_run=not_run,
        limits=['provenance is bound to the receipts; a deliberately forged manifest and '
                'receipt chain is outside what an offline reader can detect',
                'this adapter does not re-measure the board, thresholds or labels',
                'collection_mode is carried as a declaration (with every collected label '
                'preserved); it never relaxes the fitted VAL/negative criteria'])
    receipt_path_out = _write_json(output_dir / 'adapter-receipt.json', receipt,
                                   overwrite=overwrite)
    return dict(set=str(set_path.resolve()), set_sha256=set_sha,
                receipt=str(receipt_path_out.resolve()),
                receipt_sha256=_file_sha256(receipt_path_out),
                provenance=dict(kind=kind, detail=provenance_detail),
                fixture=dict(fixture_block), domain=dict(product_domain),
                environment_version=environment_version,
                collection_mode=collection_mode,
                provisional_unknown_frames=list(provisional_frames),
                counts=receipt['counts'], download_receipt=receipt_info,
                not_run=not_run)


def summarize(result):
    return dict(set=result['set'], set_sha256=result['set_sha256'],
                receipt=result['receipt'], receipt_sha256=result['receipt_sha256'],
                provenance=result['provenance'], fixture=result['fixture'],
                environment_version=result['environment_version'],
                counts=result['counts'], download_receipt=result['download_receipt'],
                not_run=result['not_run'])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    adapt_command = commands.add_parser('adapt', help='bind a download into set.json')
    adapt_command.add_argument('--download', type=Path, required=True)
    adapt_command.add_argument('--output', type=Path, required=True)
    adapt_command.add_argument('--download-receipt', type=Path,
                               help='host evidence_sha256 receipt (required for --physical)')
    adapt_command.add_argument('--physical', action='store_true',
                               help='claim new_physical_train_val; refused for fixtures')
    adapt_command.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = adapt(args.download, args.output,
                       download_receipt=args.download_receipt,
                       physical=args.physical, overwrite=args.overwrite)
    except CAL.CalibrationError as exc:
        print(json.dumps(exc.to_dict(), indent=2, ensure_ascii=True))
        return 3
    print(json.dumps(summarize(result), indent=2, ensure_ascii=True, allow_nan=False,
                     sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
