"""RingFeeder 纯逻辑单测:环形喂数决策(非活动 bank、队列游标、在途去重)。"""
from siggen.ring_feeder import RingFeeder


def _status(active=True, active_bank=0, next_bank=None, error=0):
    return {
        "active": active, "active_bank": active_bank, "next_bank": next_bank,
        "state": "RUNNING" if active else "IDLE", "error_code": error,
        "bank0": {"valid": True, "bit_length": 8, "crc16": 1},
        "bank1": {"valid": True, "bit_length": 8, "crc16": 2},
    }


def test_no_feed_without_status_queue_or_when_upload_in_flight():
    feeder = RingFeeder()
    assert feeder.next_feed(None, 2, False) is None
    assert feeder.next_feed(_status(), 0, False) is None
    assert feeder.next_feed(_status(), 2, True) is None


def test_no_feed_when_not_playing():
    feeder = RingFeeder()
    assert feeder.next_feed(_status(active=False), 2, False) is None
    assert feeder.next_feed(_status(active_bank=None), 2, False) is None


def test_no_feed_when_next_bank_already_armed():
    feeder = RingFeeder()
    # active 0, other bank (1) already fresh -> next_bank 1 -> nothing to feed
    assert feeder.next_feed(_status(active_bank=0, next_bank=1), 2, False) is None


def test_no_feed_on_error():
    feeder = RingFeeder()
    assert feeder.next_feed(_status(active_bank=0, error=0x26), 2, False) is None


def test_feeds_non_active_bank_and_cycles_queue():
    feeder = RingFeeder()
    assert feeder.next_feed(_status(active_bank=0), 3, False) == (1, 0)
    assert feeder.next_feed(_status(active_bank=1), 3, False) == (0, 1)
    assert feeder.next_feed(_status(active_bank=0), 3, False) == (1, 2)
    assert feeder.next_feed(_status(active_bank=1), 3, False) == (0, 0)


def test_single_file_queue_loops_same_index():
    feeder = RingFeeder()
    assert feeder.next_feed(_status(active_bank=0), 1, False) == (1, 0)
    assert feeder.next_feed(_status(active_bank=1), 1, False) == (0, 0)


def test_reset_rewinds_cursor():
    feeder = RingFeeder()
    feeder.next_feed(_status(active_bank=0), 2, False)
    feeder.reset()
    assert feeder.next_feed(_status(active_bank=0), 2, False) == (1, 0)


def test_skip_past_advances_cursor_after_manual_preload():
    feeder = RingFeeder()
    feeder.skip_past(0, 3)   # queue[0] manually uploaded -> ring starts at queue[1]
    assert feeder.next_feed(_status(active_bank=0), 3, False) == (1, 1)
    assert feeder.next_feed(_status(active_bank=1), 3, False) == (0, 2)


def test_skip_past_wraps_and_handles_empty_queue():
    feeder = RingFeeder()
    feeder.skip_past(2, 3)
    assert feeder.next_feed(_status(active_bank=0), 3, False) == (1, 0)
    feeder.skip_past(5, 0)
    assert feeder.next_feed(_status(active_bank=0), 3, False) == (1, 0)


def test_accepts_string_bank_selectors():
    feeder = RingFeeder()
    assert feeder.next_feed(_status(active_bank="0"), 2, False) == (1, 0)
