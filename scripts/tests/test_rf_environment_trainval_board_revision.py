"""The submitted RX-draft revision is a board value, never the constant 0.

A real board frame carries UiApp.rx_demod_revision: LiveUiSession.setup() enables the
reference engine (whose draft default is False) and reseeds it, so the revision is
>= 1 on hardware (scripts/rf_bpsk_ui_board_session.py:242-248,
pynq_overlay/sw/ui_model.py:1122-1127,1963-1981) - there is no reset point.  The
deterministic offline fixture writes 0, and the production formal path accepts any
non-negative integer (scripts/rf_environment_acceptance.py:166).

Both gates of the 11-frame TRAIN/VAL path must therefore accept a non-negative
revision and still refuse a negative or non-integer one by name:

* scripts/rf_environment_trainval_session.py check_session (download side), and
* scripts/rf_environment_trainval_board_session.py evaluate_frame (board side).

The board side is exercised here on a board-shaped directory built from the frozen
fixture: no socket, no board, no RF.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import rf_environment_calibration as CAL  # noqa: E402
from scripts import rf_environment_trainval_board_session as BOARD  # noqa: E402
from scripts import rf_environment_trainval_session as S  # noqa: E402


def refusal(code, function, *args, **kwargs):
    with pytest.raises(CAL.CalibrationError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, info.value.detail


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, document):
    Path(path).write_text(json.dumps(document, indent=2), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def set_revision(session_dir, revision):
    """Rewrite the submitted revision and keep the session receipt bound to it."""
    manifest = load(session_dir / 'session-manifest.json')
    receipt = load(session_dir / 'receipt.json')
    for slot in manifest['slots']:
        metadata = load(session_dir / slot['metadata'])
        metadata['demod_result']['submitted']['revision'] = revision
        save(session_dir / slot['metadata'], metadata)
        receipt['evidence_sha256'][slot['metadata']] = sha(session_dir / slot['metadata'])
    save(session_dir / 'receipt.json', receipt)


def board_shape(session_dir, slot, revision):
    """Add the fields a board capture carries that the offline fixture does not."""
    metadata = load(session_dir / slot['metadata'])
    readback = dict(io_ok=True, rx_armed=False, rx_done=True, rx_position_words=4096,
                    tx_source_selected=False, sampled_at_monotonic_ns=1)
    metadata.update(physical=bool(slot['physical']), pre_readback=dict(readback),
                    post_readback=dict(readback), trainval={},
                    analysis=dict(output_clipped=False, clipping_count=0))
    metadata['demod_result']['submitted']['revision'] = revision
    save(session_dir / slot['metadata'], metadata)
    return metadata


@pytest.fixture(scope='module')
def fixture_session(tmp_path_factory):
    target = tmp_path_factory.mktemp('trainval-revision') / 'train-01'
    S.build_fixture_session('train', 1, target)
    return target


@pytest.fixture
def a_session(fixture_session, tmp_path):
    target = tmp_path / 'session'
    shutil.copytree(str(fixture_session), str(target))
    return target


def test_a_board_revision_above_zero_passes_the_download_check(a_session):
    set_revision(a_session, 7)
    summary = S.check_session(a_session)
    assert summary['session_id'] == 'train-01'
    assert summary['frames'][0]['submitted']['revision'] == 7
    assert all(entry['submitted']['revision'] == 7 for entry in summary['frames'])


def test_the_fixture_zero_revision_still_passes(a_session):
    assert S.check_session(a_session)['frames'][0]['submitted']['revision'] == 0


def test_a_negative_or_non_integer_revision_is_named(a_session):
    set_revision(a_session, -1)
    refusal('SESSION_FRAME_DEMOD_MISMATCH', S.check_session, a_session)
    set_revision(a_session, '0')
    refusal('SESSION_FRAME_DEMOD_MISMATCH', S.check_session, a_session)


def test_the_board_frame_gate_accepts_the_local_rx_draft_revision(a_session):
    plan, slots = BOARD.frame_plan('train', 1)
    session_block = BOARD.session_block_of(plan)
    slot = [row for row in slots if row['tag'] == 'normal-01'][0]
    domain = dict(CAL._profile_domain(CAL.TEACHING_PROFILE)[0])
    with CAL.environment_in_domain() as environment:
        environment_version = environment.VERSION
    board_shape(a_session, slot, 7)
    row = BOARD.evaluate_frame(a_session, slot, session_block=session_block,
                               domain=domain, environment_version=environment_version,
                               release_id='offline-fixture')
    assert row['tag'] == 'normal-01' and row['scenario'] == 'normal'
    assert row['label'] != 'INVALID'
    board_shape(a_session, slot, -1)
    refusal('FRAME_DEMOD_CONFIG_MISMATCH', BOARD.evaluate_frame, a_session, slot,
            session_block=session_block, domain=domain,
            environment_version=environment_version, release_id='offline-fixture')
