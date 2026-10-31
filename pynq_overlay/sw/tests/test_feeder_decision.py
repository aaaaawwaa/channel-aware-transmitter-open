"""`feeder_decision` 的离线验收: 环形喂数判据 + 文件读取门禁, 不碰硬件。

判据的来源是**主机侧同一份逻辑**(`host/siggen/ring_feeder.py`)—— 板端这份是移植, 所以
这里把主机侧的全部用例搬过来, 再补上"状态从哪来"那半边(`status_from_pp`)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feeder_decision as F  # noqa: E402


# ---------------- RingFeeder: 主机侧用例移植 ----------------

def _status(active=1, active_bank="0", next_bank="NONE", error_code=0):
    return {"active": active, "active_bank": active_bank,
            "next_bank": next_bank, "error_code": error_code}


def test_no_feed_when_not_playing():
    f = F.RingFeeder()
    assert f.next_feed(_status(active=0), 3, False) is None


def test_no_feed_when_the_queue_is_empty():
    f = F.RingFeeder()
    assert f.next_feed(_status(), 0, False) is None


def test_no_feed_while_an_upload_is_in_flight():
    f = F.RingFeeder()
    assert f.next_feed(_status(), 3, True) is None


def test_no_feed_when_the_state_machine_is_in_error():
    f = F.RingFeeder()
    assert f.next_feed(_status(error_code=0x24), 3, False) is None


def test_no_feed_when_the_inactive_bank_is_already_fresh():
    """`next_bank` 不是 NONE 说明另一块已就绪 —— 再喂就是覆盖它。"""
    f = F.RingFeeder()
    assert f.next_feed(_status(active_bank="0", next_bank="1"), 3, False) is None


def test_feed_goes_to_the_inactive_bank_and_advances_the_cursor():
    f = F.RingFeeder()
    assert f.next_feed(_status(active_bank="0"), 3, False) == (1, 0)
    # 第二次调用(上一块还没 commit, 但 next_bank 仍 NONE)会取队列下一项
    assert f.next_feed(_status(active_bank="0"), 3, False) == (1, 1)
    assert f.next_feed(_status(active_bank="1"), 3, False) == (0, 2)
    assert f.next_feed(_status(active_bank="1"), 3, False) == (0, 0), "游标回绕"


def test_an_undecodable_active_bank_blocks_feeding():
    """`active_bank` 解不出时**不喂** —— 猜一个 bank 出来会喂到正在播的那块上。"""
    f = F.RingFeeder()
    assert f.next_feed(_status(active_bank="NONE"), 3, False) is None
    assert f.next_feed(_status(active_bank="7"), 3, False) is None
    assert f.next_feed(None, 3, False) is None


def test_skip_past_resumes_after_the_manually_preloaded_file():
    f = F.RingFeeder()
    f.skip_past(2, 3)
    assert f.next_feed(_status(active_bank="0"), 3, False) == (1, 0)
    f.skip_past(0, 4)
    assert f.next_feed(_status(active_bank="0"), 4, False) == (1, 1)


def test_skip_past_with_an_empty_queue_resets():
    f = F.RingFeeder()
    f.skip_past(5, 0)
    assert f.cursor == 0


# ---------------- status_from_pp: 从 DdrPp 对象取状态 ----------------

class FakeBank(object):
    def __init__(self, fresh=0):
        self.fresh = fresh


class FakePp(object):
    def __init__(self, active=0, active_bank=0xFF, error=0, other_fresh=0):
        self.active = active
        self.active_bank = active_bank
        self.error = error
        self.banks = [FakeBank(), FakeBank(fresh=other_fresh)]


def test_status_from_pp_reports_the_inactive_bank_as_next_when_fresh():
    pp = FakePp(active=1, active_bank=0, other_fresh=1)
    st = F.status_from_pp(pp)
    assert st["active"] == 1 and st["active_bank"] == "0" and st["next_bank"] == "1"


def test_status_from_pp_reports_none_when_the_inactive_bank_is_not_ready():
    pp = FakePp(active=1, active_bank=0, other_fresh=0)
    st = F.status_from_pp(pp)
    assert st["next_bank"] == "NONE"
    # 于是 feeder 决定喂它 —— 这正是环形要的
    assert F.RingFeeder().next_feed(st, 2, False) == (1, 0)


def test_status_from_pp_is_idle_when_not_active():
    st = F.status_from_pp(FakePp(active=0, active_bank=0xFF))
    assert st["active"] == 0 and st["active_bank"] == "NONE"


def test_status_from_pp_does_not_guess_a_bank_for_an_out_of_range_active():
    st = F.status_from_pp(FakePp(active=1, active_bank=7))
    assert st["active_bank"] == "NONE", "解不出就不猜 —— 猜错会喂到正在播的那块"
    assert F.RingFeeder().next_feed(st, 2, False) is None


def test_status_from_pp_carries_the_error_code_through():
    st = F.status_from_pp(FakePp(active=1, active_bank=0, error=0x26))
    assert st["error_code"] == 0x26
    assert F.RingFeeder().next_feed(st, 2, False) is None, "有错不喂"


# ---------------- load_waveform: 文件门禁 ----------------

def test_load_waveform_reads_a_valid_file(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"\x01\x02\x03\x04" * 4)
    data, reason = F.load_waveform(str(p))
    assert reason == "OK" and len(data) == 16


def test_load_waveform_rejects_an_empty_file(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    data, reason = F.load_waveform(str(p))
    assert data is None and reason == "EMPTY"


def test_load_waveform_rejects_a_misaligned_file(tmp_path):
    """4 字节对齐是硬要求(`bit_length % 32 == 0`)—— 上传字节被当作符号比特。"""
    p = tmp_path / "odd.bin"
    p.write_bytes(b"\x01\x02\x03\x04\x05")
    data, reason = F.load_waveform(str(p))
    assert data is None and reason == "ALIGN"


def test_load_waveform_rejects_a_file_over_one_bank(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"\x00" * 64)
    data, reason = F.load_waveform(str(p), max_bytes=32)
    assert data is None and reason == "TOO BIG", "超长只报错, 不静默截断"


def test_load_waveform_reports_a_missing_file(tmp_path):
    data, reason = F.load_waveform(str(tmp_path / "nope.bin"))
    assert data is None and reason.startswith("READ")