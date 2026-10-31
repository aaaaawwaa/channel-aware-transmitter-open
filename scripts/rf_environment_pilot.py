"""Finite S2 development pilot. New physical IQ, not the formal TEST campaign.

The orchestrator never owns unsafe initial sessions; every owned session is
disarmed in finally. Missing evidence/unsafe egress stops the flow with no retry.
"""
import json
from pathlib import Path

SEEDS = (1, 37, 73)
KINDS = ('normal', 'narrowband', 'pulse', 'compound', 'off', 'domain')
EXPECTED = {'normal': 'CLEAR', 'narrowband': 'NARROWBAND', 'pulse': 'PULSE'}


def campaign(factory, output, release):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows, sessions, seen = [], [], set()
    cause = None
    for index, seed in enumerate(SEEDS):
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
                for kind in KINDS:
                    if kind not in ('off', 'domain'):
                        session.scene(kind)
                    if kind == 'domain':
                        session.scene('normal')
                        session.domain(True)
                    tag = 's%02d-%s-00' % (index, kind)
                    row = session.capture(tag, kind != 'off')
                    row.update(tag=tag, truth=kind, reference_seed=seed, actual_scene_prbs7_seed=1,
                               session_index=index,
                               split='development-pilot', origin='new physical TX1-RX1 capture')
                    rows.append(row)
                    env = row.get('environment') or {}
                    identity = (row.get('capture_id'), row.get('iq_sha256'))
                    if (row.get('release_id') != release or row.get('egress_safe') is not True
                            or not all(identity) or identity in seen or not env
                            or env.get('reason') in ('CLIPPED', 'INVALID_IQ')):
                        cause = 'unsafe_or_incomplete_capture'
                        break
                    seen.add(identity)
                    if kind == 'domain': session.domain(False)
        except Exception as exc:
            cause, error = type(exc).__name__, str(exc)
        finally:
            if owned:
                try:
                    cleanup = session.cleanup()
                    closed, ack = cleanup.get('safe') is True, cleanup.get('disarm_ack') is True
                except Exception as exc:
                    error = str(exc)
            if session is not None:
                try: session.close()
                except Exception: pass
            sessions.append(dict(index=index, seed=seed, owned=owned, closed=closed,
                                 disarm_ack=ack, cause=cause, error=error))
        if cause is not None: break
        if not closed or not ack:
            cause = 'close_unconfirmed'
            break
    negative = sum(row['truth'] not in EXPECTED and (row.get('environment') or {}).get('label') == 'CLEAR' for row in rows)
    correct = sum(row['truth'] in EXPECTED and (row.get('environment') or {}).get('label') == EXPECTED[row['truth']] for row in rows)
    result = dict(passed=cause is None and len(rows) == 18 and correct == 9 and negative == 0,
                  stop_cause=cause, expected_release=release, records=rows, sessions=sessions,
                  known_correct=correct, negative_false_clear=negative,
                  split='development-pilot', formal_physical='NOT_RUN', human_acceptance='NOT_RUN')
    (output / 'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    return result
