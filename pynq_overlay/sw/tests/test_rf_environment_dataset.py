"""Source identity, not filenames or PRBS seeds, governs calibration splits."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts import rf_environment_dataset as D


def row(split='train', session='a', source='one', digest='a' * 64):
    return dict(split=split, session_id=session, source_id=source, iq_sha256=digest)


@pytest.mark.parametrize('other', [
    row('test', 'b', 'two'),
    row('val', 'b', 'one', 'b' * 64),
    row('val', 'a', 'two', 'b' * 64),
])
def test_hash_source_or_session_reuse_across_splits_is_rejected(other):
    with pytest.raises(ValueError):
        D.validate_splits([row(), other])


def test_distinct_sessions_and_source_hashes_can_be_frozen():
    assert D.validate_splits([row(), row('val', 'b', 'two', 'b' * 64)]) is None


def test_renaming_derived_iq_does_not_change_parent_group():
    original = row()
    derived = row('test', 'b', 'renamed', 'c' * 64)
    derived['parent_source_id'] = original['source_id']
    with pytest.raises(ValueError):
        D.validate_splits([original, derived])
