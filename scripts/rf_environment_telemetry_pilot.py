"""12-frame 46 dB telemetry diagnostic: 3 sessions x [off, normal, normal, off].

The orchestrator is the 18-frame development pilot's shape with the telemetry frame
plan and the budget hard stop added; the frozen pilot entry itself is NOT modified.
Every owned session is disarmed in finally, and any hit stops the whole campaign with
no re-capture, no retry and the original IQ kept:

* initial_not_safe_or_wrong_release;
* unsafe_or_incomplete_capture: egress unsafe, release drift, missing/duplicate
  (capture_id, iq_sha256), missing environment, environment reason in
  {CLIPPED, INVALID_IQ}, or the per-frame telemetry document missing its required
  fields (missing evidence);
* the captured exception type (capture incomplete / timeout);
* close_unconfirmed;
* budget_initialization_cap / budget_frame_cap: 3 initializations or 12 frames.

This module is board-import safe and host-import safe: it imports no board module.
"""
import json
from pathlib import Path

try:  # board: the uploaded sibling modules live in the same directory
    from rf_environment_telemetry_session import (APPROVED_ATTENUATION_MDB,
                                                  NOT_AVAILABLE,
                                                  NOT_AVAILABLE_ITEMS,
                                                  REQUIRED_TELEMETRY_KEYS,
                                                  TELEMETRY_SCHEMA)
except ImportError:  # pragma: no cover - workstation import path
    from scripts.rf_environment_telemetry_session import (APPROVED_ATTENUATION_MDB,
                                                          NOT_AVAILABLE,
                                                          NOT_AVAILABLE_ITEMS,
                                                          REQUIRED_TELEMETRY_KEYS,
                                                          TELEMETRY_SCHEMA)

SEEDS = (1, 37, 73)
# Per session: pre OFF (plain rx.capture), normal, normal (same scene, loaded again),
# post OFF.  OFF frames never load a scene and never carry observe_timing.
FRAME_PLAN = (('off', False), ('normal', True), ('normal', True), ('off', False))
APPROVED_SESSIONS = 3
APPROVED_INITIALIZATIONS = 3
APPROVED_FRAMES = 12
TELEMETRY_SPLIT = 'development-telemetry-12'
STOP_CLIPPED = ('CLIPPED', 'INVALID_IQ')


def budget_refusal(seeds, plan):
    """The declared plan may not exceed the approved budget, before any session exists."""
    if len(seeds) > APPROVED_INITIALIZATIONS or len(seeds) > APPROVED_SESSIONS:
        return 'budget_initialization_cap'
    if len(seeds) * len(plan) > APPROVED_FRAMES:
        return 'budget_frame_cap'
    return None


def telemetry_incomplete(telemetry):
    if not isinstance(telemetry, dict):
        return True
    return not set(REQUIRED_TELEMETRY_KEYS) <= set(telemetry)


def telemetry_file_present(output, row):
    name = row.get('telemetry_file')
    return isinstance(name, str) and bool(name) and (Path(output) / name).is_file()


def telemetry_not_available_complete(telemetry):
    declared = telemetry.get('not_available') if isinstance(telemetry, dict) else None
    if not isinstance(declared, dict) or set(declared) != set(NOT_AVAILABLE_ITEMS):
        return False
    return all(isinstance(item, dict) and item.get('value') == NOT_AVAILABLE
               for item in declared.values())


def campaign(factory, output, release):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    seeds, plan = SEEDS, FRAME_PLAN
    rows, sessions, seen = [], [], set()
    cause = budget_refusal(seeds, plan)
    initializations = 0
    for index, seed in enumerate(seeds) if cause is None else ():
        if index >= APPROVED_SESSIONS or initializations >= APPROVED_INITIALIZATIONS:
            cause = 'budget_initialization_cap'
            break
        session, owned, error = None, False, None
        closed = ack = False
        try:
            session = factory()
            initial = session.initial()
            if initial.get('safe') is not True or initial.get('release_id') != release:
                cause = 'initial_not_safe_or_wrong_release'
            else:
                owned = True
                session.setup(seed)
                initializations += 1
                for position, (kind, physical) in enumerate(plan):
                    if len(rows) >= APPROVED_FRAMES:
                        cause = 'budget_frame_cap'
                        break
                    if physical:
                        session.scene(kind)
                    occurrence = sum(1 for earlier, _ in plan[:position] if earlier == kind)
                    tag = 's%02d-%s-%02d' % (index, kind, occurrence)
                    row = session.capture(tag, physical)
                    row.update(tag=tag, truth=kind, reference_seed=seed,
                               actual_scene_prbs7_seed=1, session_index=index,
                               physical=bool(physical), split=TELEMETRY_SPLIT,
                               origin='new physical TX1-RX1 12-frame telemetry diagnostic')
                    rows.append(row)
                    environment = row.get('environment') or {}
                    telemetry = row.get('telemetry')
                    identity = (row.get('capture_id'), row.get('iq_sha256'))
                    if (row.get('release_id') != release or row.get('egress_safe') is not True
                            or not all(identity) or identity in seen or not environment
                            or environment.get('reason') in STOP_CLIPPED
                            or telemetry_incomplete(telemetry)):
                        cause = 'unsafe_or_incomplete_capture'
                        break
                    seen.add(identity)
        except Exception as exc:                                   # noqa: BLE001
            cause, error = type(exc).__name__, str(exc)
        finally:
            if owned:
                try:
                    cleanup = session.cleanup()
                    closed = cleanup.get('safe') is True
                    ack = cleanup.get('disarm_ack') is True
                except Exception as exc:                           # noqa: BLE001
                    error = str(exc)
            if session is not None:
                try:
                    session.close()
                except Exception:                                  # noqa: BLE001
                    pass
            sessions.append(dict(index=index, seed=seed, owned=owned, closed=closed,
                                 disarm_ack=ack, cause=cause, error=error))
        if cause is not None:
            break
        if not closed or not ack:
            cause = 'close_unconfirmed'
            break
    files_present = bool(rows) and all(telemetry_file_present(output, row) for row in rows)
    not_available_complete = bool(rows) and all(
        telemetry_not_available_complete(row.get('telemetry')) for row in rows)
    result = dict(
        passed=(cause is None and len(rows) == APPROVED_FRAMES
                and len(sessions) == APPROVED_SESSIONS
                and initializations == APPROVED_INITIALIZATIONS
                and files_present and not_available_complete),
        stop_cause=cause, expected_release=release, records=rows, sessions=sessions,
        frames=len(rows), initializations=initializations,
        budget=dict(sessions=APPROVED_SESSIONS, initializations=APPROVED_INITIALIZATIONS,
                    frames=APPROVED_FRAMES),
        per_session_order=[list(item) for item in FRAME_PLAN], reference_seeds=list(SEEDS),
        attenuation_mdB=APPROVED_ATTENUATION_MDB, telemetry_schema=TELEMETRY_SCHEMA,
        telemetry_files_present=files_present, not_available_complete=not_available_complete,
        split=TELEMETRY_SPLIT, origin='new physical TX1-RX1 12-frame telemetry diagnostic',
        formal_physical='NOT_RUN', human_acceptance='NOT_RUN', s2_stage_passed=False)
    (output / 'result.json').write_text(json.dumps(result, indent=2, allow_nan=False),
                                        encoding='utf-8')
    return result
