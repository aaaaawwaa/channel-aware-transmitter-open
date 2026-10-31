"""Board-side 11-frame 46 dB TRAIN/VAL session: ten normal frames then one off.

Runs ON THE BOARD (uploaded and executed by
scripts/rf_environment_trainval_collect.py); it is never a workstation entry.
It drives the installed release's production worker/UiView exactly like the
reviewed S2 pilot entry does and writes ONE session directory in the layout the
calibration adapter consumes:

    <output>/
      normal-01.iq .. normal-10.iq, off-01.iq          # 16384 B signed16 LE I/Q
      normal-01.json .. off-01.json                    # board metadata + session block
      normal-01.environment.json .. off-01.environment.json
      records/<record-id>/{frame.iq,metadata.json,receipt.json}
      session-manifest.json, receipt.json              # only when all 11 frames landed

Preconditions (none of them is optional):
* the verified installed release is the expected release id and its software
  contract is cnn-bpsk-environment-v1 (the S2 environment contract);
* the release's teaching profile domain equals the domain recorded in the
  manifest, and its frozen environment baseline declares the same domain version
  and RF pairs (the host orchestrator refuses the candidate before connecting);
* an explicit --cable-confirmed attestation; the two-hit loopback confirmation
  inside the production capture path is the second, per-frame one;
* the installed daemon's own physical-loopback attenuation equals the release's
  domain attenuation.  The daemon approves exactly the value compiled into it
  (rf_state.h:26, rf_state.c:114) while the request field can only carry the release's
  single source, so a release that disagrees cannot produce one physical frame.  Such a
  campaign is refused BEFORE dispatch -- no output directory, no client, no worker, no
  RF -- with stop_cause DAEMON_LOOPBACK_ATTENUATION_MISMATCH (or
  ..._UNKNOWN when the installed daemon's requirement cannot be read).  This is the one
  precondition the entry can check without touching the part.

Frame order is fixed by the session entry: normal-01..normal-10 (physical
TX1->RX1 loopback captures) then off-01 (plain RX capture over the same window
with the TX source released: the energy-floor negative control).

The scene is loaded once per contiguous scenario group and never assumed: the
entry loads the product's normal scene before normal-01 and the product's OFF
scene before off-01, through the same two-hit scene load the panel uses
(EnvironmentSession.scene -> UiView.environment_scene_btns ->
ui_model.set_environment_scene -> the scenario module).  The declaration bound to
a frame is therefore the declaration of the scene that was actually loaded, and
evaluate_frame still refuses any frame whose declared truth differs from its slot
(FRAME_SCENARIO_MISMATCH).  An OFF frame is a plain RX capture: the OFF scene is a
zero waveform (nothing radiated), the capture path is the non-physical one, and
the per-frame gate requires the post readback to show tx_source_selected=false;
the negative control can never be produced by relabelling a normal frame.

Collection mode (spec 2026-10-17 section J, added 2026-10-18) is declared per session:
standard_acceptance (default) keeps the formal rule that a VAL normal frame must be
CLEAR, while calibration_collection additionally accepts qualified VAL pairs
UNKNOWN / AMPLITUDE_OUTSIDE_RULE_SUPPORT and PULSE / SPARSE_AMPLITUDE_OUTLIERS
(spec sections J/K) from the pre-fit (n=1 development) baseline.  These pairs are
accepted only after every quality gate below has passed.  Original labels are
kept in the manifest and receipt; other non-CLEAR VAL labels still stop the
session.  TRAIN rules are unchanged.  The mode never changes the final
criteria: scripts/rf_environment_calibration.py still fits the baseline from the 20
TRAIN normal frames only and still requires VAL 10/10 CLEAR with zero OFF
false-CLEARs.

Stop conditions (spec 2026-10-17 section 2.5; any hit stops the whole campaign,
no retry, no re-collection, no re-tuning):
1. any frame CLIPPED / INVALID_IQ / egress_safe != true / missing evidence;
2. any duplicated frame identity or IQ digest;
3. a session that cannot be closed (close unconfirmed / disarm_ack false);
4. a TRAIN normal frame that is not reliable zero-BER BPSK;
5. a VAL normal frame that is not CLEAR (recorded, then stopped);
6. any threshold/baseline/domain change made to let the data pass;
7. release identity drift or an unexplained service restart.
On a stop every captured artifact is kept, the session is disarmed in the
finally block and the named cause is written to session-result.json; a stopped
session is never completed into a downloadable session (no manifest, no receipt).

Per-frame record: requested RF (the eight applied parameters), actual RF (the
per-frame PL readback, the daemon status identity and the optional RX1 gain
observation), the environment label with its named reason, and the code-rail
(clipping) flags.  The attenuation register is NOT read back on this path and
that hole is named, never filled with a claim.

No claim: this module is implemented and verified offline against a stub
session factory; it has never been executed against a board (NOT_RUN).
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:  # workstation package import; on the board the two files sit side by side
    from scripts import rf_environment_trainval_session as SESSION
except ImportError:  # pragma: no cover - board execution path
    import rf_environment_trainval_session as SESSION

RESULT_SCHEMA = 'rf-environment-trainval-session-result-v1'
PREFLIGHT_SCHEMA = 'rf-environment-trainval-board-preflight-v1'
ENTRY = 'scripts/rf_environment_trainval_board_session.py'
ENVIRONMENT_CONTRACT = 'cnn-bpsk-environment-v1'
BOARD_ORIGIN = ('board physical TRAIN/VAL session via the production worker/UiView; '
                'ten normal loopback frames then one off frame')
STUB_ORIGIN = ('stub board test double: synthetic codes, NOT a physical capture; '
               'only the session envelope is under test')
IDENTITY_KEYS = ('capture_id', 'release_id', 'connection_generation', 'config_revision',
                 'completed_at_monotonic')
IQ_BYTES = SESSION.IQ_BYTES
FRAME_TAGS = SESSION.FRAME_TAGS
STOP_CONDITIONS = (
    'any frame CLIPPED / INVALID_IQ / egress_safe != true / missing download',
    'any duplicated frame identity or IQ digest',
    'a session that cannot be closed (close unconfirmed / disarm_ack false)',
    'a TRAIN normal frame that is not reliable zero-BER BPSK',
    'a VAL normal frame judged NARROWBAND/PULSE or otherwise not CLEAR (in '
    'calibration_collection only the qualified UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT '
    'pair is additionally accepted, with its label kept)',
    'any threshold/baseline/domain change made to let the data pass',
    'release identity drift, an unexplained service restart or a storage fault',
)
NOT_RUN = ['physical TRAIN/VAL collection on hardware: NOT_RUN in this round',
           'development pilot: NOT_RUN', 'formal TEST: NOT_RUN',
           'human acceptance: NOT_RUN']

# ------------------------------------------------- the daemon's loopback rule
# The physical loopback is a transaction the DAEMON owns, and it approves exactly one
# attenuation, compiled into it (rf_overlay/sw/board_app/rf_state.h:26,
# RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB).  The request field itself is pinned by the
# release's Python side to the single-source profile value
# (rf_status_client._capture_args refuses anything but rf_teaching_profile.ATTENUATION_MDB,
# ui_model.rx_capture_args builds the panel's request from the same constant), so the
# release's domain attenuation IS the value that goes on the wire.  When the two sides
# disagree every physical frame is refused with INVALID_ARGUMENT before any hardware
# write -- 2026-10-18 campaign2 train-01: ten planned physical frames, zero delivered,
# and the campaign was already un-runnable before it was dispatched.
#
# The requirement is read out of the daemon's own refusal message, so this probe cannot
# drift from the rule it is checking: the literal is the authority, not a copy of it.
DAEMON_BINARY_NAME = 'rf_daemon'
LOOPBACK_ATTENUATION_RE = re.compile(
    rb'physical loopback requires attenuation_mdB ([0-9]+)')


def daemon_loopback_attenuation_mdb(release_directory):
    """(value, source) the installed daemon demands for a physical loopback.

    Read-only and board-local: no client, no socket, no RF, no output directory.
    (None, why) means the requirement could not be READ, which is reported as unknown
    and never as agreement.
    """
    path = Path(release_directory) / DAEMON_BINARY_NAME
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, '%s unreadable: %s' % (path, exc)
    found = LOOPBACK_ATTENUATION_RE.search(raw)
    if found is None:
        return None, ('%s carries no compiled-in physical-loopback attenuation '
                      'requirement' % path)
    return int(found.group(1)), str(path)


def loopback_attenuation_verdict(domain, daemon_attenuation_mdb, source=None):
    """What a physical frame will name vs what the installed daemon accepts.

    ok=True only when the two are proven equal; ok=False is a proven mismatch;
    ok=None means the daemon's requirement could not be read (never a silent pass).
    """
    sent = (domain or {}).get('attenuation_mdB')
    verdict = dict(sent=sent, daemon=daemon_attenuation_mdb, source=source)
    if daemon_attenuation_mdb is None or type(sent) is not int:
        verdict.update(ok=None, code='DAEMON_LOOPBACK_ATTENUATION_UNKNOWN')
    elif sent != daemon_attenuation_mdb:
        verdict.update(ok=False, code='DAEMON_LOOPBACK_ATTENUATION_MISMATCH')
    else:
        verdict.update(ok=True, code=None)
    return verdict


def loopback_refusal(verdict):
    """The named (code, detail) refusal for a verdict that is not a proven agreement."""
    if verdict.get('ok') is True:
        return None
    code = verdict.get('code') or 'DAEMON_LOOPBACK_ATTENUATION_UNKNOWN'
    if code == 'DAEMON_LOOPBACK_ATTENUATION_MISMATCH':
        detail = ('a physical loopback frame would name attenuation_mdB %r while the '
                  'installed daemon approves only %r (%s); every physical frame would be '
                  'refused with INVALID_ARGUMENT and no RF action would be taken'
                  % (verdict.get('sent'), verdict.get('daemon'), verdict.get('source')))
    else:
        detail = ('the installed daemon physical-loopback attenuation requirement could '
                  'not be read (%s), so the campaign cannot be shown to be runnable'
                  % verdict.get('source'))
    return code, detail


class BoardSessionError(SESSION.SessionError):
    """One named board-session refusal; callers match on the code, never prose."""


def _require(condition, code, detail=''):
    if not condition:
        raise BoardSessionError(code, detail)


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path):
    return _sha256_bytes(Path(path).read_bytes())


def _read_json(path, code, label):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise BoardSessionError(code, '%s not found: %s' % (label, path))
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BoardSessionError(code, '%s is not valid JSON: %s' % (label, exc))


def _write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    with temporary.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False))
    os.replace(str(temporary), str(path))
    return path


# The one write probe the entry uses to prove --output is really writable.  It is
# created and removed before any session object exists, so the directory is still
# empty when the session starts.
OUTPUT_PROBE_NAME = '.session-output-write-probe'


def prepare_output_directory(output):
    """Own one session output directory before a session object exists.

    The board session writes its artifacts straight into --output and never creates it
    (rf_bpsk_ui_board_session.LiveUiSession._save opens <tag>.iq/<tag>.json in place and
    rf_experiment_record.write_record only makes <output>/records), so the entry creates
    and verifies the directory itself: it must exist, be a real directory, be empty and
    be writable.  Every failure is named here instead of surfacing as whichever artifact
    was written first (2026-10-18 board run train-01: bare FileNotFoundError on frame 1).
    """
    output = Path(output)
    if output.is_symlink():
        raise BoardSessionError('SESSION_OUTPUT_NOT_A_DIRECTORY',
                                'the session output path is a symlink: %s' % output)
    if output.exists() and not output.is_dir():
        raise BoardSessionError(
            'SESSION_OUTPUT_NOT_A_DIRECTORY',
            'the session output path exists and is not a directory: %s' % output)
    if output.is_dir() and any(output.iterdir()):
        raise BoardSessionError('SESSION_OUTPUT_NOT_EMPTY',
                                'the session directory must be absent or empty: %s' % output)
    created = not output.exists()
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BoardSessionError('SESSION_OUTPUT_UNCREATABLE',
                                'the session output directory cannot be created: %s (%s)'
                                % (output, exc))
    if not output.is_dir():
        raise BoardSessionError(
            'SESSION_OUTPUT_UNCREATABLE',
            'the session output path is not a directory after creation: %s' % output)
    probe = output / OUTPUT_PROBE_NAME
    try:
        with probe.open('x', encoding='utf-8') as handle:
            handle.write('session output write probe\n')
    except OSError as exc:
        raise BoardSessionError('SESSION_OUTPUT_NOT_WRITABLE',
                                'the session output directory cannot be written: %s (%s)'
                                % (output, exc))
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    if any(output.iterdir()):
        raise BoardSessionError('SESSION_OUTPUT_NOT_EMPTY',
                                'the session directory is not empty after preparation: %s'
                                % output)
    return dict(path=str(output), created=created, empty=True, writable=True)


def capture_is_complete(status, metadata, teaching=False):
    """Is this saved frame the complete handoff the session envelope may bind?

    This is exactly the evidence the production capture path requires before it returns
    a frame (rf_bpsk_ui_board_session.capture(): state 'done', a frame, a matching
    handoff payload and a demod result).  A frame that is not complete is a FAILED
    capture: its diagnostics are the product, and it must never enter the success-frame
    identity/domain/envelope binding below (review D3).
    """
    state = status.get('state') if type(status) is dict else None
    complete = bool(state == 'done'
                    and metadata.get('capture_id') is not None
                    and metadata.get('iq_sha256') is not None
                    and metadata.get('handoff_frame_matches') is True
                    and metadata.get('demod_result') is not None)
    if complete and teaching and metadata.get('constellation') is None:
        # The teaching/environment contract also carries the constellation, so the
        # capture path refuses such a frame as incomplete; it must not be bound as a
        # success frame here either.
        return False
    return complete


def capture_diagnostic(output, tag):
    """Read back the diagnostic a failed capture saved beside its frame.

    Best effort and read-only: the failed frame's own <tag>.json is the only place the
    raw UI state/code/reason survives, and metadata_sha256 pins the bytes that failure
    left behind, so a later save cannot quietly rewrite them.
    """
    path = Path(output) / (tag + '.json')
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    if type(document) is not dict:
        return None
    diagnostic = document.get('ui_diagnostic')
    diagnostic = dict(diagnostic) if type(diagnostic) is dict else {}
    diagnostic['capture_status'] = document.get('capture_status')
    try:
        diagnostic['metadata_sha256'] = _file_sha256(path)
    except OSError:
        diagnostic['metadata_sha256'] = None
    return diagnostic


def session_files(output, tag):
    """The three canonical artifacts of one frame, in one place."""
    base = Path(output)
    return (base / (tag + '.iq'), base / (tag + '.json'),
            base / (tag + '.environment.json'))


def frame_plan(split, index):
    """The 11 ordered slots with the physical flag the session must produce."""
    document = SESSION.plan_document(split, index)
    slots = []
    for slot in document['slots']:
        row = dict(slot)
        row['physical'] = slot['scenario'] == 'normal'
        slots.append(row)
    return document, slots


def scene_sequence(slots):
    """The scene loads one session needs: one per contiguous scenario group.

    The product scene is a real load (two-hit confirmation + scenario module), so
    the runner switches it exactly when the slot's scenario changes: normal before
    frame 1, OFF before the off frame.  Deriving the list from the slots keeps the
    load order identical to the frame order and makes the OFF switch an explicit,
    checkable step instead of an assumption.
    """
    sequence, active = [], None
    for slot in slots:
        if slot['scenario'] != active:
            sequence.append(dict(position=slot['position'], tag=slot['tag'],
                                 scenario=slot['scenario']))
            active = slot['scenario']
    return sequence


def session_block_of(plan_document_value):
    """The manifest session block (with index and the rotating reference seed)."""
    session = plan_document_value['session']
    return dict(session_id=session['session_id'], split=session['split'],
                index=session['index'], seed=session['seed'],
                reference_prbs7_seed=session['reference_prbs7_seed'])


def frame_block_of(session_block):
    """The per-frame session block the session entry checks: id, split, seed."""
    return dict(session_id=session_block['session_id'], split=session_block['split'],
                seed=session_block['seed'])


def bind_frame_documents(metadata, sidecar, *, slot, session_block, environment_version,
                         release_id, trainval, origin):
    """Add the session envelope to one frame's two JSON documents.

    Additive only: the board's own identity, demod result, environment result and
    record stay exactly as the production path wrote them, and the raw IQ file is
    never touched.  Both documents are updated in place and returned.
    """
    _require(type(metadata) is dict and type(sidecar) is dict,
             'FRAME_DOCUMENT_MISSING', 'frame %s needs metadata and a sidecar' % slot['tag'])
    physical = bool(slot['physical'])
    frame_block = frame_block_of(session_block)
    for document in (metadata, sidecar):
        document['session'] = dict(frame_block)
        document['fixture'] = False
        document['collected'] = True
        document['physical'] = physical
        document['release_id'] = release_id
        document['environment_version'] = environment_version
        document['trainval'] = dict(trainval)
        document['origin'] = origin
    return metadata, sidecar


def _environment_block(sidecar):
    block = sidecar.get('environment')
    _require(type(block) is dict, 'FRAME_ENVIRONMENT_MISSING',
             'frame sidecar carries no environment result')
    return block


def evaluate_frame(output, slot, *, session_block, domain, environment_version, release_id,
                   collection_mode=SESSION.STANDARD_COLLECTION):
    """Verify one captured frame's artifacts; a refusal is a named stop.

    This is the single per-frame gate: the board session runs it right after each
    capture and build_session_documents runs it again over the files it is about
    to bind, so a frame can never enter a manifest it did not pass.  Every quality
    gate is checked before the label rule below, so the calibration collection mode
    can only ever accept an otherwise fully qualified frame.
    """
    collection_mode = SESSION.check_collection_mode(collection_mode)
    tag = slot['tag']
    split, seed = session_block['split'], session_block['seed']
    frame_block = frame_block_of(session_block)
    iq_path, metadata_path, sidecar_path = session_files(output, tag)
    for path in (iq_path, metadata_path, sidecar_path):
        _require(path.is_file() and not path.is_symlink(), 'FRAME_ARTIFACT_MISSING',
                 'frame %s artifact missing: %s' % (tag, path))
    raw = iq_path.read_bytes()
    _require(len(raw) == IQ_BYTES, 'FRAME_ARTIFACT_SIZE',
             'frame %s is %d bytes, the frozen window is %d' % (tag, len(raw), IQ_BYTES))
    digest = _sha256_bytes(raw)
    metadata = _read_json(metadata_path, 'FRAME_ARTIFACT_MISSING', 'frame %s metadata' % tag)
    sidecar = _read_json(sidecar_path, 'FRAME_ARTIFACT_MISSING', 'frame %s sidecar' % tag)
    _require(metadata.get('iq_sha256') == digest and sidecar.get('iq_sha256') == digest,
             'FRAME_IQ_SHA_MISMATCH',
             'frame %s metadata/sidecar digest differs from its bytes' % tag)
    identity = {key: metadata.get(key) for key in IDENTITY_KEYS}
    _require(all(value is not None for value in identity.values()),
             'FRAME_IDENTITY_MISSING',
             'frame %s is missing part of the five-tuple identity: %r' % (tag, identity))
    environment = _environment_block(sidecar)
    sidecar_identity = dict(
        capture_id=sidecar.get('capture_id'), release_id=sidecar.get('release_id'),
        connection_generation=environment.get('connection_generation'),
        config_revision=environment.get('config_revision'),
        completed_at_monotonic=environment.get('completed_at_monotonic'))
    _require(identity == sidecar_identity and environment.get('capture_id') == identity['capture_id']
             and environment.get('release_id') == identity['release_id'],
             'FRAME_IDENTITY_MISMATCH',
             'frame %s metadata and sidecar disagree on identity: %r vs %r'
             % (tag, identity, sidecar_identity))
    _require(metadata.get('physical') is bool(slot['physical']),
             'FRAME_PHYSICAL_FLAG_MISMATCH',
             'frame %s declares physical=%r, a %s frame requires %r'
             % (tag, metadata.get('physical'), slot['scenario'], bool(slot['physical'])))
    _require(metadata.get('session') == frame_block
             and sidecar.get('session') == frame_block,
             'FRAME_SESSION_MISMATCH',
             'frame %s carries session %r, this session is %r'
             % (tag, metadata.get('session'), frame_block))
    _require(metadata.get('release_id') == release_id and sidecar.get('release_id') == release_id
             and identity['release_id'] == release_id, 'FRAME_RELEASE_MISMATCH',
             'frame %s release %r differs from the session release %r'
             % (tag, identity['release_id'], release_id))
    _require(sidecar.get('rf_config') == domain, 'FRAME_DOMAIN_MISMATCH',
             'frame %s rf_config differs from the session domain' % tag)
    _require(sidecar.get('environment_version') == environment_version
             and environment.get('environment_version') == environment_version,
             'FRAME_ENVIRONMENT_VERSION_MISMATCH',
             'frame %s environment version differs from the session version %r'
             % (tag, environment_version))
    declaration = sidecar.get('scenario_declaration')
    _require(type(declaration) is dict and declaration.get('truth') == slot['scenario'],
             'FRAME_SCENARIO_MISMATCH',
             'frame %s declares truth %r, the slot says %r'
             % (tag, (declaration or {}).get('truth'), slot['scenario']))
    demod = metadata.get('demod_result')
    result = (demod or {}).get('result') if type(demod) is dict else None
    _require(type(result) is dict and all(result.get(key) == identity[key] for key in IDENTITY_KEYS),
             'FRAME_DEMOD_IDENTITY_MISMATCH',
             'frame %s demod result belongs to another capture' % tag)
    submitted = (demod or {}).get('submitted') if type(demod) is dict else None
    config = (submitted or {}).get('config') if type(submitted) is dict else None
    _require(type(config) is dict and config.get('mode') == 'BPSK'
             and config.get('symbol_rate_hz') == 3000000 and config.get('shaping') == 'RECT'
             and config.get('reference_prbs7_seed') == seed
             and set(config) == {'mode', 'symbol_rate_hz', 'shaping', 'reference_prbs7_seed'}
             # The board's submitted revision is the local RX-draft revision, never a
             # constant: setup() sets reference_enabled (default False) and the rotating
             # reference seed, so rx_demod_revision >= 1 on real hardware
             # (scripts/rf_bpsk_ui_board_session.py:242-248, ui_model.py:1122-1127,1963-1981).
             # The offline stub writes 0.  The production formal path accepts any
             # non-negative integer (scripts/rf_environment_acceptance.py:166); a negative or
             # non-integer revision is still a named refusal here.
             and type(submitted.get('revision')) is int and submitted.get('revision') >= 0
             and submitted.get('decim') == 1
             and submitted.get('rx_len') == 4096 and submitted.get('input_fs_hz') == 60000000,
             'FRAME_DEMOD_CONFIG_MISMATCH',
             'frame %s submitted demod block is outside the frozen RX domain' % tag)
    pre = metadata.get('pre_readback')
    post = metadata.get('post_readback')
    _require(type(pre) is dict and type(post) is dict and pre.get('io_ok') is True
             and post.get('io_ok') is True and post.get('rx_armed') is False,
             'FRAME_READBACK_UNSAFE',
             'frame %s did not confirm a released, readable PL state' % tag)
    _require(post.get('tx_source_selected') is False, 'FRAME_READBACK_UNSAFE',
             'frame %s left the TX source selected (a physical capture must release it and an '
             'off frame must never run with it)' % tag)
    analysis = metadata.get('analysis')
    analysis = analysis if type(analysis) is dict else {}
    clip_count = analysis.get('clipping_count')
    clip_count = clip_count if type(clip_count) is int and clip_count >= 0 else 0
    rail = bool(analysis.get('output_clipped') is True or clip_count > 0)
    label = environment.get('label')
    reason = environment.get('reason')
    reason = reason if type(reason) is str else ''
    _require(reason != 'INVALID_IQ', 'FRAME_INVALID_IQ',
             'frame %s holds IQ outside the signed 12-bit range' % tag)
    _require(not rail and reason != 'CLIPPED', 'FRAME_CLIPPED',
             'frame %s hit the code rail (clipping_count=%d, reason=%r)'
             % (tag, clip_count, reason))
    reference_sync = result.get('reference_sync')
    ber = result.get('ber')
    compared = result.get('compared_bits')
    # Pre-fit labels are retained only here, after every physical quality gate.
    # identity/domain/version/declaration/readback/rail/sync/BER all passed above.
    provisional = SESSION.provisional_unknown(label, reason)
    if slot['scenario'] == 'normal':
        _require(reference_sync is True and type(ber) in (int, float) and ber == 0
                 and type(compared) is int and compared >= 32, 'NORMAL_NOT_RELIABLE_BPSK',
                 'normal frame %s is not reliable zero-BER BPSK (sync=%r ber=%r bits=%r)'
                 % (tag, reference_sync, ber, compared))
        if split == 'val':
            _require(label == 'CLEAR'
                     or (collection_mode == SESSION.CALIBRATION_COLLECTION
                         and (provisional or (label, reason) ==
                              ('PULSE', 'SPARSE_AMPLITUDE_OUTLIERS'))),
                     'VAL_NORMAL_NOT_CLEAR',
                     'VAL normal frame %s is %r (%s) in %s mode; record it and stop'
                     % (tag, label, reason, collection_mode))
        else:
            _require(label != 'INVALID', 'TRAIN_NORMAL_INVALID',
                     'TRAIN normal frame %s is INVALID (%s)' % (tag, reason))
    else:
        _require(label != 'CLEAR', 'OFF_FALSE_CLEAR',
                 'off frame %s is CLEAR: the negative control did not stay a floor' % tag)
    trainval = metadata.get('trainval')
    trainval = dict(trainval) if type(trainval) is dict else {}
    return dict(
        position=slot['position'], tag=tag, frame_id=slot['frame_id'],
        source_id=slot['source_id'], scenario=slot['scenario'], physical=bool(slot['physical']),
        seed=seed, reference_prbs7_seed=seed, iq=slot['iq'], metadata=slot['metadata'],
        environment_file=slot['environment'], iq_sha256=digest,
        capture_id=identity['capture_id'], identity=dict(identity),
        label=label, reason=reason, rail=rail, clipping_count=clip_count,
        provisional_unknown=bool(provisional),
        reference_sync=reference_sync, ber=ber, compared_bits=compared,
        requested_rf=dict(trainval.get('requested_rf') or {}),
        actual_rf=dict(trainval.get('actual_rf') or {}),
        metadata_sha256=_file_sha256(metadata_path),
        environment_sha256=_file_sha256(sidecar_path), trainval=trainval)


def build_session_documents(output, *, split, index, domain, environment_version,
                            release_id, session_closed, disarm_ack, origin,
                            requested_rf=None, board=None,
                            collection_mode=SESSION.STANDARD_COLLECTION):
    """Bind 11 verified frames into session-manifest.json + receipt.json.

    The directory is re-verified through evaluate_frame and then through
    SESSION.check_session, so a manifest is only ever written over frames that
    would pass the download-side check.  Raises (and writes no manifest) otherwise.
    """
    output = Path(output)
    plan, slots = frame_plan(split, index)
    session_block = session_block_of(plan)
    collection_mode = SESSION.check_collection_mode(collection_mode)
    rows = [evaluate_frame(output, slot, session_block=session_block, domain=dict(domain),
                           environment_version=environment_version, release_id=release_id,
                           collection_mode=collection_mode)
            for slot in slots]
    provisional_frames = [row['tag'] for row in rows if row['provisional_unknown']]
    seen_capture, seen_iq = {}, {}
    for row in rows:
        previous = seen_capture.setdefault(row['capture_id'], row['tag'])
        _require(previous == row['tag'], 'FRAME_IDENTITY_DUPLICATE',
                 'capture id %r is used by %s and %s'
                 % (row['capture_id'], previous, row['tag']))
        previous = seen_iq.setdefault(row['iq_sha256'], row['tag'])
        _require(previous == row['tag'], 'FRAME_IQ_DUPLICATE',
                 'IQ digest %s is used by %s and %s' % (row['iq_sha256'], previous, row['tag']))
    evidence = {}
    for row in rows:
        for name in (row['iq'], row['metadata'], row['environment_file']):
            evidence[name] = _file_sha256(output / name)
    manifest = dict(
        schema=SESSION.MANIFEST_SCHEMA, fixture=False, collected=True, physical=True,
        not_for_formal_acceptance=False, frame_count=SESSION.FRAMES_PER_SESSION,
        normal_count=SESSION.NORMAL_PER_SESSION, off_count=SESSION.OFF_PER_SESSION,
        frame_order=list(FRAME_TAGS), session=session_block,
        slots=[dict(position=row['position'], tag=row['tag'], frame_id=row['frame_id'],
                    source_id=row['source_id'], scenario=row['scenario'], seed=row['seed'],
                    reference_prbs7_seed=row['reference_prbs7_seed'], iq=row['iq'],
                    metadata=row['metadata'], environment=row['environment_file'],
                    iq_sha256=row['iq_sha256'], capture_id=row['capture_id'],
                    physical=row['physical'], label=row['label'], reason=row['reason'],
                    rail=row['rail'], clipping_count=row['clipping_count'],
                    collected_label=row['label'], collected_reason=row['reason'],
                    provisional_unknown=row['provisional_unknown'])
               for row in rows],
        collection_mode=collection_mode, provisional_unknown_frames=list(provisional_frames),
        domain=dict(domain), environment_version=environment_version, release_id=release_id,
        provenance=dict(kind='board_session', origin=origin,
                        detail='one closed 11-frame session (10 normal + 1 off) captured on the '
                               'installed release through the production worker/UiView'),
        requested_rf=dict(requested_rf or {}), board=dict(board or {}),
        stop_conditions=list(STOP_CONDITIONS), not_run=list(NOT_RUN),
        created_by=ENTRY, origin=origin)
    _write_json_atomic(output / 'session-manifest.json', manifest)
    manifest_sha = _file_sha256(output / 'session-manifest.json')
    receipt = dict(
        schema=SESSION.RECEIPT_SCHEMA, fixture=False, collected=True, physical=True,
        collection_mode=collection_mode, provisional_unknown_frames=list(provisional_frames),
        split=split, seed=session_block['seed'], session_id=session_block['session_id'],
        index=index, session_manifest_sha256=manifest_sha,
        frame_count=SESSION.FRAMES_PER_SESSION, normal_count=SESSION.NORMAL_PER_SESSION,
        off_count=SESSION.OFF_PER_SESSION, evidence_sha256=evidence,
        frames=[dict(position=row['position'], tag=row['tag'], frame_id=row['frame_id'],
                     iq=row['iq'], metadata=row['metadata'],
                     environment=row['environment_file'], iq_sha256=row['iq_sha256'],
                     capture_id=row['capture_id'], scenario=row['scenario'],
                     label=row['label'], reason=row['reason'], rail=row['rail'],
                     clipping_count=row['clipping_count'],
                     provisional_unknown=row['provisional_unknown'],
                     reference_sync=row['reference_sync'], ber=row['ber'],
                     compared_bits=row['compared_bits'],
                     requested_rf=dict(row['requested_rf']), actual_rf=dict(row['actual_rf']))
              for row in rows],
        session_closed=bool(session_closed), disarm_ack=bool(disarm_ack), complete=True,
        provenance=dict(kind='board_session', origin=origin),
        stop_conditions=list(STOP_CONDITIONS), not_run=list(NOT_RUN),
        created_by=ENTRY, origin=origin)
    _write_json_atomic(output / 'receipt.json', receipt)
    return SESSION.check_session(output), rows


def summarize_session(summary, rows):
    """Small printable view; the documents stay in the session directory."""
    return dict(
        session_id=summary['session_id'], split=summary['split'], index=summary['index'],
        seed=summary['seed'], release_id=summary['release_id'],
        collection=dict(fixture=summary['fixture'], collected=summary['collected'],
                        physical=summary['physical']),
        domain=summary['domain'], environment_version=summary['environment_version'],
        collection_mode=summary.get('collection_mode'),
        provisional_unknown_count=summary.get('provisional_unknown_count'),
        frame_count=summary['frame_count'], normal_count=summary['normal_count'],
        off_count=summary['off_count'], frame_order=summary['frame_order'],
        manifest_sha256=summary['manifest_sha256'], receipt_sha256=summary['receipt_sha256'],
        session_closed=summary['session_closed'], disarm_ack=summary['disarm_ack'],
        frames=[dict(tag=row['tag'], scenario=row['scenario'], label=row['label'],
                     reason=row['reason'], rail=row['rail'], clipping_count=row['clipping_count'],
                     provisional_unknown=row['provisional_unknown'],
                     capture_id=row['capture_id'], iq_sha256=row['iq_sha256'],
                     reference_sync=row['reference_sync'], ber=row['ber'],
                     compared_bits=row['compared_bits'],
                     requested_rf=dict(row['requested_rf']), actual_rf=dict(row['actual_rf']))
                for row in rows])


def session_result_document(*, split, index, expected_release, domain, environment_version,
                            origin, session_block=None, loopback_attenuation=None,
                            collection_mode=SESSION.STANDARD_COLLECTION):
    """The one result-document shape: the pre-dispatch gate and a session share it."""
    collection_mode = SESSION.check_collection_mode(collection_mode)
    if session_block is None:
        plan, _slots = frame_plan(split, index)
        session_block = session_block_of(plan)
    return dict(
        schema=RESULT_SCHEMA, entry=ENTRY, expected_release=expected_release,
        release_id=None, split=split, index=index, session_id=session_block['session_id'],
        seed=session_block['seed'], domain=dict(domain),
        environment_version=environment_version, collection_mode=collection_mode,
        scenes=[], session_closed=False, disarm_ack=False, manifest_written=False, passed=False,
        stop_cause=None, stop_frame=None, error=None, cleanup_error=None,
        stop_diagnostic=None, validation_error=None,
        loopback_attenuation=dict(loopback_attenuation) if loopback_attenuation else None,
        frames=[], origin=origin, physical=True, collected=True, fixture=False,
        stop_conditions=list(STOP_CONDITIONS), not_run=list(NOT_RUN))


def run_session(session, *, split, index, output, expected_release, domain,
                environment_version, result_path=None, origin=BOARD_ORIGIN,
                requested_rf=None, board=None, loopback_attenuation=None,
                collection_mode=SESSION.STANDARD_COLLECTION):
    """Drive one session object through the 11-frame shape; stop on the first hit.

    The session object is the transport under test: the production one comes from
    trainval_session_class(), and the offline tests inject a stub with the same
    initial/setup/scene/capture/cleanup/close protocol.  Nothing here retries,
    re-collects or re-tunes.  The scene is loaded once per scenario group (see
    scene_sequence), so the OFF slot is captured under the OFF scene: the frame's
    own declaration then has to say so, or FRAME_SCENARIO_MISMATCH stops the run.
    """
    collection_mode = SESSION.check_collection_mode(collection_mode)
    output = Path(output)
    plan, slots = frame_plan(split, index)
    session_block = session_block_of(plan)
    seed = session_block['seed']
    result = session_result_document(
        split=split, index=index, expected_release=expected_release, domain=domain,
        environment_version=environment_version, origin=origin, session_block=session_block,
        loopback_attenuation=loopback_attenuation, collection_mode=collection_mode)
    rows, cause, error, owned = [], None, None, False
    stop_frame = None
    stop_diagnostic = None
    cleanup_error = None
    closed = ack = False
    try:
        # The frames land straight in <output> (the board's LiveUiSession opens
        # <tag>.iq/<tag>.json in place and write_record only makes <output>/records), so
        # a missing directory is refused by name here instead of escaping as a raw
        # FileNotFoundError for whichever artifact is written first.
        _require(output.is_dir() and not output.is_symlink(), 'SESSION_OUTPUT_MISSING',
                 'the session output directory does not exist: %s' % output)
        initial = session.initial()
        _require(isinstance(initial, dict) and initial.get('safe') is True,
                 'INITIAL_NOT_SAFE', 'the pre-session readback is not safe: %r' % (initial,))
        _require(initial.get('release_id') == expected_release, 'RELEASE_MISMATCH',
                 'installed release %r is not the expected %r'
                 % (initial.get('release_id'), expected_release))
        result['release_id'] = initial.get('release_id')
        owned = True
        session.setup(seed)
        loaded_scene = None
        for slot in slots:
            stop_frame = slot['tag']
            # The scene load for this slot's scenario, through the session's real
            # scene entry (two-hit confirmation in the product path).  A load failure
            # is named beside the frame, never flattened into CAPTURE_FAILED.
            if slot['scenario'] != loaded_scene:
                try:
                    session.scene(slot['scenario'])
                except SESSION.SessionError as exc:
                    cause, error = exc.code, exc.detail
                    break
                except Exception as exc:
                    cause, error = 'SCENE_LOAD_FAILED', '%s: %s' % (type(exc).__name__, exc)
                    break
                loaded_scene = slot['scenario']
                result['scenes'].append(dict(position=slot['position'], tag=slot['tag'],
                                             scenario=slot['scenario']))
            try:
                row = session.capture(slot['tag'], bool(slot['physical']))
            except SESSION.SessionError as exc:
                # A named refusal keeps its own name (never flattened into CAPTURE_FAILED).
                cause, error = exc.code, exc.detail
                stop_diagnostic = capture_diagnostic(output, slot['tag'])
                break
            except TimeoutError:
                cause, error = 'CAPTURE_TIMEOUT', 'capture deadline for %s' % slot['tag']
                stop_diagnostic = capture_diagnostic(output, slot['tag'])
                break
            except Exception as exc:  # the evidence is already on disk at this point
                cause, error = 'CAPTURE_FAILED', '%s: %s' % (type(exc).__name__, exc)
                stop_diagnostic = capture_diagnostic(output, slot['tag'])
                break
            _require(isinstance(row, dict), 'CAPTURE_RECORD_MALFORMED',
                     'capture %s returned no record' % slot['tag'])
            _require(row.get('egress_safe') is True, 'EGRESS_UNSAFE',
                     'capture %s read back an unsafe egress state' % slot['tag'])
            try:
                frame = evaluate_frame(output, slot, session_block=session_block,
                                       domain=domain, environment_version=environment_version,
                                       release_id=expected_release,
                                       collection_mode=collection_mode)
            except SESSION.SessionError as exc:
                cause, error = exc.code, exc.detail
                break
            _require(row.get('iq_sha256') == frame['iq_sha256']
                     and row.get('capture_id') == frame['capture_id'],
                     'FRAME_ROW_MISMATCH',
                     'capture %s row disagrees with its artifacts' % slot['tag'])
            rows.append(frame)
            stop_frame = None
    except SESSION.SessionError as exc:
        cause, error = exc.code, exc.detail
    except Exception as exc:
        cause, error = 'SESSION_ERROR', '%s: %s' % (type(exc).__name__, exc)
    finally:
        if owned:
            try:
                cleanup = session.cleanup()
                closed = bool(isinstance(cleanup, dict) and cleanup.get('safe') is True)
                ack = bool(isinstance(cleanup, dict) and cleanup.get('disarm_ack') is True)
            except Exception as exc:
                # A cleanup failure is recorded BESIDE the capture error, never over it:
                # losing the capture reason to a disarm timeout is what made the
                # 2026-10-18 failure undiagnosable (review D3).
                cleanup_error = '%s: %s' % (type(exc).__name__, exc)
        try:
            session.close()
        except Exception:
            pass
    result['session_closed'], result['disarm_ack'] = closed, ack
    result['cleanup_error'] = cleanup_error
    result['stop_diagnostic'] = stop_diagnostic
    if cause is None and len(rows) == len(slots):
        try:
            summary, bound = build_session_documents(
                output, split=split, index=index, domain=domain,
                environment_version=environment_version, release_id=expected_release,
                session_closed=closed, disarm_ack=ack, origin=origin,
                requested_rf=requested_rf, board=board, collection_mode=collection_mode)
            rows = bound
            result['manifest_written'] = True
            result['manifest_sha256'] = summary['manifest_sha256']
            result['receipt_sha256'] = summary['receipt_sha256']
        except SESSION.SessionError as exc:
            result['validation_error'] = dict(error=exc.code, detail=exc.detail)
            cause, stop_frame = exc.code, None
    # A complete but unclosed session keeps its artifacts and its manifest; the
    # adaptation gate (PHYSICAL_SESSION_NOT_CLOSED) is what refuses it.
    if cause is None and not (closed and ack):
        cause = 'CLOSE_UNCONFIRMED'
        error = 'the session did not confirm a close and a disarm acknowledgement'
    result['stop_cause'] = cause
    result['stop_frame'] = stop_frame
    result['error'] = error
    result['frames'] = [dict(tag=row['tag'], scenario=row['scenario'], label=row['label'],
                             reason=row['reason'], rail=row['rail'],
                             clipping_count=row['clipping_count'],
                             provisional_unknown=row['provisional_unknown'],
                             capture_id=row['capture_id'],
                             iq_sha256=row['iq_sha256'], reference_sync=row['reference_sync'],
                             ber=row['ber'], compared_bits=row['compared_bits'],
                             requested_rf=dict(row['requested_rf']),
                             actual_rf=dict(row['actual_rf'])) for row in rows]
    result['passed'] = bool(cause is None and result['manifest_written'] and closed and ack)
    if result_path is not None:
        _write_json_atomic(result_path, result)
    return result


# --------------------------------------------------------------------- preflight

def preflight(expected_release, split=None, index=None, release_directory=None,
              collection_mode=SESSION.STANDARD_COLLECTION):
    """Read-only board preflight: release, contract, domain, baseline, plan.

    No worker, no client, no socket, no RF, no output directory: importing this and
    reading the installed release's own files is the whole action.  The declared
    collection mode is echoed (and validated) so the host can prove the board entry
    received the mode its own declaration names.
    """
    collection_mode = SESSION.check_collection_mode(collection_mode)
    release = Path(release_directory or '/home/xilinx/overlays/awg_merged/current')
    release = release.resolve()
    if str(release) not in sys.path:
        sys.path.insert(0, str(release))
    try:
        import merged_release as R
    except ImportError as exc:
        raise BoardSessionError(
            'RELEASE_UNREADABLE',
            'the release at %s cannot be imported by this user (%s); the installed release '
            'directories are root-only (dr-x------ root root), so the board preflight must '
            'run privileged (sudo) and read-only' % (release, exc))
    verified = R.verify_release(release, require_software=True)
    _require(verified.get('release_id') == expected_release, 'RELEASE_MISMATCH',
             'installed release %r is not the expected %r'
             % (verified.get('release_id'), expected_release))
    _require(verified.get('software_contract') == ENVIRONMENT_CONTRACT,
             'RELEASE_CONTRACT_MISMATCH',
             'the 46 dB TRAIN/VAL session needs the %s software contract, this release '
             'declares %r' % (ENVIRONMENT_CONTRACT, verified.get('software_contract')))
    import rf_teaching_profile as profile
    import rf_environment as environment
    domain = dict(profile.teaching_capability().rf_parameters)
    baseline_path = release / 'rf_environment_baseline.json'
    body = _read_json(baseline_path, 'BASELINE_MISSING', 'frozen environment baseline')
    try:
        baseline = environment.EnvironmentBaseline.from_dict(body)
    except ValueError as exc:
        raise BoardSessionError('BASELINE_INVALID',
                                'the installed frozen environment baseline is refused: %s' % exc)
    _require(dict(baseline.rf_config) == domain, 'BASELINE_DOMAIN_MISMATCH',
             'the installed baseline declares attenuation %r, the release teaching profile %r'
             % (dict(baseline.rf_config).get('attenuation_mdB'), domain.get('attenuation_mdB')))
    _require(baseline.environment_version == environment.VERSION,
             'BASELINE_VERSION_MISMATCH',
             'the installed baseline is %s, the release environment module is %s'
             % (baseline.environment_version, environment.VERSION))
    # The daemon's own physical-loopback rule, read from the installed binary: the
    # campaign is refused before dispatch when the request it would send cannot be
    # approved (see the section above).
    daemon_loopback, loopback_source = daemon_loopback_attenuation_mdb(release)
    verdict = loopback_attenuation_verdict(domain, daemon_loopback, loopback_source)
    result = dict(schema=PREFLIGHT_SCHEMA, ok=True, entry=ENTRY,
                  expected_release=expected_release, release_id=verified['release_id'],
                  collection_mode=collection_mode,
                  release_directory=str(release),
                  software_contract=verified.get('software_contract'),
                  daemon_loopback_attenuation_mdB=verdict['daemon'],
                  loopback_attenuation=dict(verdict),
                  domain=domain, environment_version=environment.VERSION,
                  baseline_sha256=baseline.sha256,
                  baseline_file_sha256=_file_sha256(baseline_path),
                  baseline_calibration_status=baseline.calibration_status,
                  baseline_sources=len(baseline.sources),
                  captures_started=False, rf_actions=False,
                  scope='read-only release/domain/baseline preflight; no client, no RF',
                  stop_conditions=list(STOP_CONDITIONS), not_run=list(NOT_RUN))
    if split is not None and index is not None:
        plan, slots = frame_plan(split, index)
        result['session'] = session_block_of(plan)
        result['frames'] = [dict(tag=slot['tag'], scenario=slot['scenario'],
                                 physical=slot['physical']) for slot in slots]
        result['scenes'] = scene_sequence(slots)
    return result


def trainval_session_class():
    """The production session object; the board imports are deferred to construction."""
    try:
        from rf_environment_board_session import EnvironmentSession
    except ImportError:  # pragma: no cover - workstation import path
        from scripts.rf_environment_board_session import EnvironmentSession

    class TrainvalSession(EnvironmentSession):
        """EnvironmentSession plus the session envelope the TRAIN/VAL shape needs."""

        ORIGIN = BOARD_ORIGIN

        def __init__(self, expected, output, domain, environment_version):
            super().__init__(expected, output)
            try:
                self.expected = expected
                self._domain = dict(domain)
                self._environment_version = environment_version
                self._session_block = None
                self._slots = []
                self._observed = {}
                from rf_environment import VERSION as installed_version
                from rf_teaching_profile import teaching_capability
                if installed_version != environment_version:
                    raise BoardSessionError('RELEASE_VERSION_MISMATCH', installed_version)
                if dict(teaching_capability().rf_parameters) != self._domain:
                    raise BoardSessionError('RELEASE_DOMAIN_MISMATCH', str(self._domain))
            except Exception:
                self.close()
                raise

        def configure(self, split, index):
            """Bind this session object to one matrix session before any RF action."""
            document, slots = frame_plan(split, index)
            self._split, self._index = split, index
            self._session_block = session_block_of(document)
            self._slots = slots
            self._observed = {}
            return dict(self._session_block)

        def _slot(self, tag):
            for slot in self._slots:
                if slot['tag'] == tag:
                    return slot
            raise BoardSessionError('FRAME_TAG_NOT_IN_SESSION',
                                    '%s is not part of this session' % tag)

        def requested_rf(self):
            from rf_teaching_profile import teaching_capability
            return dict(teaching_capability().rf_parameters)

        def _actual_rf(self, metadata):
            observation, unavailable = None, None
            try:
                observation = dict(self.client.observe_rx_gain())
            except Exception as exc:
                unavailable = '%s: %s' % (type(exc).__name__, exc)
            status = dict(self.app.rf_status or {})
            return dict(
                source='per-frame PL readback, daemon status identity and the optional RX1 '
                       'gain observation; the attenuation register is not read back here',
                pre_readback=metadata.get('pre_readback'),
                post_readback=metadata.get('post_readback'),
                daemon=dict(release_id=status.get('release_id'), armed=status.get('armed'),
                            online=status.get('online'), stale=status.get('stale'),
                            daemon_state=status.get('daemon_state'),
                            tx_session_ready=status.get('tx_session_ready'),
                            rx_session_ready=status.get('rx_session_ready')),
                rx_gain_observation=observation,
                rx_gain_observation_unavailable=unavailable,
                attenuation_readback=None,
                attenuation_readback_reason='NOT_READ_BACK_ON_ENVIRONMENT_PATH')

        def setup(self, seed):
            super().setup(seed)
            applied = dict(getattr(self.app, '_rf_applied_parameters', {}) or {})
            if applied != self._domain:
                raise BoardSessionError('RF_DOMAIN_MISMATCH',
                                        'applied RF %r is not the session domain %r'
                                        % (applied, self._domain))

        def _save(self, tag, status, readback):
            metadata_path = self.output / (tag + '.json')
            sidecar_path = self.output / (tag + '.environment.json')
            # super() writes <tag>.iq (only when a frame arrived) and <tag>.json with the
            # capture path's own raw ui_diagnostic, so the failure product is complete
            # before anything below can refuse.
            meta = super()._save(tag, status, readback)
            slot = self._slot(tag)
            metadata = _read_json(metadata_path, 'FRAME_DOCUMENT_MISSING', 'frame metadata')
            if not capture_is_complete(status, metadata, getattr(self, '_teaching', False)):
                # No frame, or a failed status: keep the original reason and stop here.
                # The success-frame release/domain/sidecar/envelope checks below would
                # otherwise report 'belongs to release None' and mask the real refusal
                # (review D3).  Nothing is rewritten: this frame's <tag>.json stays the
                # bytes the failing capture saved.
                return meta
            sidecar = _read_json(sidecar_path, 'FRAME_DOCUMENT_MISSING', 'frame sidecar')
            if metadata.get('release_id') != self.expected:
                raise BoardSessionError('FRAME_RELEASE_MISMATCH',
                                        'frame %s belongs to release %r'
                                        % (tag, metadata.get('release_id')))
            environment = sidecar.get('environment') or {}
            analysis = metadata.get('analysis') or {}
            result = ((metadata.get('demod_result') or {}).get('result') or {})
            clip_count = analysis.get('clipping_count')
            clip_count = clip_count if type(clip_count) is int and clip_count >= 0 else 0
            trainval = dict(
                tag=tag, position=slot['position'], scenario=slot['scenario'],
                truth=slot['scenario'], physical=bool(slot['physical']),
                requested_rf=self.requested_rf(),
                client_requested_rf=dict(getattr(self.app, '_rx_record_rf_config', {}) or {}),
                actual_rf=self._actual_rf(metadata),
                label=environment.get('label'), reason=environment.get('reason'),
                rail=bool(analysis.get('output_clipped') is True or clip_count > 0),
                clipping_count=clip_count,
                reference_sync=result.get('reference_sync'), ber=result.get('ber'),
                compared_bits=result.get('compared_bits'))
            metadata, sidecar = bind_frame_documents(
                metadata, sidecar, slot=slot, session_block=self._session_block,
                environment_version=self._environment_version, release_id=self.expected,
                trainval=trainval, origin=self.ORIGIN)
            _write_json_atomic(metadata_path, metadata)
            _write_json_atomic(sidecar_path, sidecar)
            self._observed[tag] = trainval
            return meta

    return TrainvalSession


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-release', required=True)
    parser.add_argument('--split', choices=SESSION.SPLITS, required=True)
    parser.add_argument('--session', type=int, required=True)
    parser.add_argument('--output', type=Path, help='session directory (board side)')
    parser.add_argument('--result', type=Path, help='result JSON, written outside the session')
    parser.add_argument('--cable-confirmed', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--release-directory', type=Path,
                        help='installed release directory; defaults to the board path. '
                             'The capture session itself always resolves the installed '
                             'release through the production entry, never this flag.')
    parser.add_argument('--collection-mode', choices=SESSION.COLLECTION_MODES,
                        default=SESSION.STANDARD_COLLECTION,
                        help='standard_acceptance (default; a VAL normal frame must be '
                             'CLEAR) or calibration_collection (additionally accepts the '
                             'qualified UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT pair and '
                             'keeps its label).  The mode is written into the session '
                             'manifest and receipt; it never relaxes the fitted criteria.')
    arguments = parser.parse_args(argv)
    if not arguments.cable_confirmed:
        raise SystemExit('REFUSE: --cable-confirmed attestation required for the TX1-RX1 cable')
    if arguments.preflight:
        document = preflight(arguments.expected_release, arguments.split, arguments.session,
                             release_directory=arguments.release_directory,
                             collection_mode=arguments.collection_mode)
        if arguments.result is not None:
            _write_json_atomic(arguments.result, document)
        print(json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False, sort_keys=True))
        return 0
    if arguments.output is None:
        raise SystemExit('REFUSE: --output <session directory> is required')
    document = preflight(arguments.expected_release, arguments.split, arguments.session,
                         release_directory=arguments.release_directory,
                         collection_mode=arguments.collection_mode)
    # Read-only pre-dispatch gate.  A physical frame this release cannot have approved
    # makes the whole campaign un-runnable, so it is refused here -- BEFORE the output
    # directory, the client, the worker and every RF action -- and the named cause goes
    # into the result document the host orchestrator reads.
    loopback = dict(document['loopback_attenuation'])
    refusal = loopback_refusal(loopback)
    if refusal is not None:
        result = session_result_document(
            split=arguments.split, index=arguments.session,
            expected_release=arguments.expected_release, domain=document['domain'],
            environment_version=document['environment_version'], origin=BOARD_ORIGIN,
            loopback_attenuation=loopback, collection_mode=arguments.collection_mode)
        result['stop_cause'], result['error'] = refusal
        if arguments.result is not None:
            _write_json_atomic(arguments.result, result)
        summary = {key: result[key] for key in
                   ('passed', 'stop_cause', 'stop_frame', 'session_closed', 'disarm_ack',
                    'error')}
        print(json.dumps(summary, indent=2, ensure_ascii=True, allow_nan=False))
        return 1
    document['captures_started'] = True
    # The session writes every artifact straight into --output and never creates it, so
    # the entry owns the directory (as the two sibling entries do) and proves it exists,
    # is a real empty directory and is writable BEFORE the session object is constructed
    # or any RF action can happen.  The first real board run on 2026-10-18 died on frame
    # 1 because <stage>/out never existed.
    output = arguments.output
    prepare_output_directory(output)
    session_class = trainval_session_class()
    session = session_class(arguments.expected_release, output, document['domain'],
                            document['environment_version'])
    session.configure(arguments.split, arguments.session)
    result = run_session(
        session, split=arguments.split, index=arguments.session, output=output,
        expected_release=arguments.expected_release, domain=document['domain'],
        environment_version=document['environment_version'], result_path=arguments.result,
        requested_rf=session.requested_rf(), loopback_attenuation=loopback,
        collection_mode=arguments.collection_mode,
        board=dict(release_id=document['release_id'],
                   release_directory=document['release_directory'],
                   baseline_sha256=document['baseline_sha256'],
                   software_contract=document['software_contract']))
    if result['manifest_written']:
        summary = summarize_session(SESSION.check_session(output), result['frames'])
        summary['passed'] = result['passed']
        summary['stop_cause'] = result['stop_cause']
        summary['collection_mode'] = result['collection_mode']
    else:
        summary = {key: result[key] for key in
                   ('passed', 'stop_cause', 'stop_frame', 'session_closed', 'disarm_ack',
                    'error')}
    print(json.dumps(summary, indent=2, ensure_ascii=True, allow_nan=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
