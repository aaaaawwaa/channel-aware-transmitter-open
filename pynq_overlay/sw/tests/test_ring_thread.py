"""`ring_thread` 的离线验收: 命令队列、喂入门禁、快照内容、环形节奏。

全部走**假 server + 假 clock + 假 DdrPp**, 不碰硬件。线程本身也真的起(为了验证"命令塞进去
会被执行"), 但用 `join` + 事件把它变成一个可等待的确定性对象, 而不是靠 sleep 猜。
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import awg_ddr_pp as P  # noqa: E402
import feeder_decision as F  # noqa: E402
import ring_thread as RT  # noqa: E402


class FakeServer(object):
    """`RingServer` 的替身: 不碰 socket, 只记录被调次数。"""

    def __init__(self):
        self.flushed = []
        self.pumps = 0
        self.services = 0
        self._events = []

    def open_socket(self):
        return object()

    def _pump(self, sock):
        self.pumps += 1

    def service_once(self):
        self.services += 1
        return self._events.pop(0) if self._events else None

    def _flush(self, bank):
        self.flushed.append(bank)

    def push_event(self, ev):
        self._events.append(ev)


class FakeBank(object):
    def __init__(self):
        self.valid = 1
        self.fresh = 0
        self.bit_length = 4096 * 8
        self.crc16 = 0
        self.owner = P.STAGE_NONE


class FakePp(object):
    """最小 `DdrPp` 替身: 只提供 RingThread 真正用到的接口。"""

    def __init__(self, digmod_mode=5):
        self.banks = [FakeBank(), FakeBank()]
        self.bank_mem = (bytearray(P.BANK_BYTES), bytearray(P.BANK_BYTES))
        self.active = 0
        self.active_bank = 0xFF
        self.state = "IDLE"
        self.error = 0
        self._mode = digmod_mode
        self.started = []
        self.stopped = 0
        self.fed = []                     # (bank, n)
        self.feed_gate = None             # 设成 (ok, reason) 可强制 feed_file 结果

    def start(self, bank=2):
        if bank > 1:
            bank = 0
        self.active, self.active_bank, self.state, self.error = 1, bank, "RUNNING", 0
        self.started.append(bank)
        self.banks[bank].fresh = 0
        return True

    def stop(self):
        self.active, self.active_bank, self.state, self.error = 0, 0xFF, "IDLE", 0
        self.stopped += 1
        return True

    def service(self):
        return None, bool(self.active)

    def feed_file(self, bank, data, memory_view=None):
        if self.feed_gate is not None:
            return self.feed_gate
        if self.active and bank == self.active_bank:
            return False, "BUSY"
        if not self.active and self._mode != 0:
            return False, "BUSY"
        self.bank_mem[bank][0:len(data)] = data
        self.fed.append((bank, len(data)))
        # ⚠ `valid` **也要置起** —— 真 `feed_file` 走 `latch_bank`, 那里 `valid/fresh/`
        #   `bit_length` 是一起置的。少了 `valid`, 桩就与真实现分道扬镳: 装填完 bank 仍
        #   不是 valid, 于是 `start()` 该失败的地方"通过"了, 而游标该前进的地方没前进。
        self.banks[bank].valid = 1
        self.banks[bank].fresh = 1
        self.banks[bank].bit_length = len(data) * 8
        return True, "OK"

    def pl_view(self):
        return {"src_addr": 0x1000 if self.active_bank in (0, 1) else None,
                "bank": self.active_bank if self.active_bank in (0, 1) else None}


def _start_thread(pp=None, server=None, load_fn=None, queue=None):
    pp = pp if pp is not None else FakePp()
    server = server if server is not None else FakeServer()
    th = RT.RingThread(server, pp, load_fn=load_fn, poll_interval=0.001,
                       snapshot_interval=0.0)
    th.start()
    return th, pp, server


def _wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.002)
    return False


def test_start_command_reaches_the_pp():
    th, pp, _ = _start_thread()
    try:
        th.submit(RT.CMD_START, 1)
        assert _wait_for(lambda: pp.started == [1]), pp.started
        assert th.snapshot()["active"] == 1
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_stop_command_stops_the_pp():
    th, pp, _ = _start_thread()
    try:
        th.submit(RT.CMD_START, 0)
        assert _wait_for(lambda: pp.started)
        th.submit(RT.CMD_STOP)
        assert _wait_for(lambda: pp.stopped == 1)
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_feed_command_loads_and_writes_the_bank(tmp_path):
    """播放中喂**非活动** bank —— 这正是面板「入队/上传」按钮走的那条路。"""
    data = b"\xAB" * 64
    p = tmp_path / "w.bin"
    p.write_bytes(data)
    th, pp, server = _start_thread()
    try:
        th.submit(RT.CMD_START, 0)                      # 播 bank0 -> 非活动的是 bank1
        assert _wait_for(lambda: pp.started == [0])
        th.submit(RT.CMD_FEED, (str(p), None))          # bank=None -> 自动选非活动
        assert _wait_for(lambda: pp.fed == [(1, 64)]), pp.fed
        assert server.flushed == [1], "锁存后必须 flush(HP 口非相干)"
        assert bytes(pp.bank_mem[1][:64]) == data
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_load_fills_a_bank_while_stopped(tmp_path):
    """「装填」是**停播时**的动作: MODE OFF 下把一块写进 bank 并锁存。"""
    data = b"\x5A" * 64
    p = tmp_path / "first.bin"
    p.write_bytes(data)
    pp = FakePp(digmod_mode=0)                          # 装填要求 MODE OFF
    for bank in pp.banks:
        bank.valid = 0
    th, pp, server = _start_thread(pp=pp)
    try:
        th.submit(RT.CMD_LOAD, (str(p), 0))
        assert _wait_for(lambda: pp.fed == [(0, 64)]), pp.fed
        assert pp.banks[0].valid == 1, "装填后该 bank 必须 valid(起播要求它)"
        assert server.flushed == [0], "锁存后必须 flush(HP 口非相干)"
        assert bytes(pp.bank_mem[0][:64]) == data
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_load_is_refused_while_the_mode_is_on(tmp_path):
    """MODE 非 OFF 时装填必须被拒 —— 数据源还在 AWG 手里。

    这条门禁是 `feed_file` 给的, 不是 `RingThread` 自己编的: 装填(MODE OFF)与播放
    (MODE 非 OFF)**不可能同时成立**, 所以"起播"这条命令里装不进任何东西 —— 顺序只能
    是 MODE OFF -> 装填 -> MODE <调制> -> 起播。
    """
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x01" * 64)
    pp = FakePp(digmod_mode=5)                          # 非 OFF
    for bank in pp.banks:
        bank.valid = 0
    th, pp, _server = _start_thread(pp=pp)
    try:
        th.submit(RT.CMD_LOAD, (str(p), 0))
        assert _wait_for(lambda: bool(th.snapshot()["last_error"])), "应当记下失败原因"
        assert pp.fed == [], "MODE 非 OFF 时不该写进 bank"
        assert "BUSY" in th.snapshot()["last_error"], th.snapshot()["last_error"]
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_load_advances_the_ring_cursor_so_the_first_feed_is_not_a_repeat(tmp_path):
    """装填过的那一块不能再被环形喂一遍 —— 游标要跟着前进(`skip_past`)。

    否则环形第一拍会把队列第 0 项**再喂一次**给非活动 bank, 用户看到的第一个切换就是
    重复内容(两份相同数据, 光看输出完全看不出来 —— 只有数喂了几块才发现)。
    """
    a, b = b"\x11" * 64, b"\x22" * 64
    pa, pb = tmp_path / "a.bin", tmp_path / "b.bin"
    pa.write_bytes(a)
    pb.write_bytes(b)
    pp = FakePp(digmod_mode=0)
    for bank in pp.banks:
        bank.valid = 0
    th, pp, _server = _start_thread(pp=pp)
    try:
        th.set_queue([str(pa), str(pb)])
        th.submit(RT.CMD_LOAD, (str(pa), 0))
        assert _wait_for(lambda: pp.fed == [(0, 64)]), pp.fed

        # 切到 MODE 非 OFF 并起播 bank0 -> 环形该喂**第 1 项**(b), 不是第 0 项
        pp._mode = 5
        th.submit(RT.CMD_SET_RING, True)
        th.submit(RT.CMD_START, 0)
        assert _wait_for(lambda: len(pp.fed) == 2), pp.fed
        assert pp.fed == [(0, 64), (1, 64)], pp.fed
        assert bytes(pp.bank_mem[1][:64]) == b, "环形喂的是第 0 项(重复), 不是第 1 项"
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_feed_to_the_active_bank_is_refused_by_the_pp_gate(tmp_path):
    """活动 bank 正被 DMA 读 —— 喂它就是当场改写正在播的内容, `feed_file` 必须拒。"""
    p = tmp_path / "w.bin"
    p.write_bytes(b"\x00" * 8)
    pp = FakePp()
    th, _, _ = _start_thread(pp=pp)
    try:
        th.submit(RT.CMD_START, 0)
        assert _wait_for(lambda: pp.started == [0])
        th.submit(RT.CMD_FEED, (str(p), 0))             # 显式指定活动 bank
        assert _wait_for(lambda: "BUSY" in th.snapshot()["last_error"]), th.snapshot()
        assert pp.fed == []
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_feed_to_a_bad_file_records_an_error_and_does_not_write(tmp_path):
    p = tmp_path / "odd.bin"
    p.write_bytes(b"\x01\x02\x03")
    th, pp, _ = _start_thread()
    try:
        th.submit(RT.CMD_FEED, (str(p), 0))
        assert _wait_for(lambda: "ALIGN" in th.snapshot()["last_error"]), th.snapshot()
        assert pp.fed == []
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_a_feed_refused_by_the_pp_is_surfaced_as_an_error(tmp_path):
    p = tmp_path / "w.bin"
    p.write_bytes(b"\x00" * 8)
    pp = FakePp()
    pp.feed_gate = (False, "BUSY")
    th, _, _ = _start_thread(pp=pp)
    try:
        th.submit(RT.CMD_FEED, (str(p), 0))
        assert _wait_for(lambda: "BUSY" in th.snapshot()["last_error"]), th.snapshot()
        assert pp.fed == []
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_ring_feeds_the_inactive_bank_once_per_switch(tmp_path):
    """环形: 播放中队列非空 -> 喂非活动 bank; 喂好后 next_bank 就绪 -> 不再喂。"""
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"\x11" * 32)
    b.write_bytes(b"\x22" * 32)
    pp = FakePp(digmod_mode=5)
    th, pp, _ = _start_thread(pp=pp)
    try:
        th.set_queue([str(a), str(b)])
        th.submit(RT.CMD_START, 0)
        assert _wait_for(lambda: pp.started)
        th.submit(RT.CMD_SET_RING, True)
        # 第一次: 非活动 bank1 不 fresh -> 喂 a
        assert _wait_for(lambda: pp.fed == [(1, 32)]), pp.fed
        # 喂好后 bank1 已 fresh -> feeder 看到 next_bank 就绪, 不再喂
        time.sleep(0.05)
        assert pp.fed == [(1, 32)], "非活动 bank 已就绪时不该继续喂(会覆盖)"
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_ring_off_stops_feeding(tmp_path):
    a = tmp_path / "a.bin"
    a.write_bytes(b"\x11" * 32)
    pp = FakePp()
    th, pp, _ = _start_thread(pp=pp)
    try:
        th.set_queue([str(a)])
        th.submit(RT.CMD_START, 0)
        assert _wait_for(lambda: pp.started)
        th.submit(RT.CMD_SET_RING, False)
        time.sleep(0.05)
        assert pp.fed == [], "环形关着不该喂"
        assert th.snapshot()["ring_on"] == 0
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_snapshot_carries_the_pl_view_and_bank_lengths():
    pp = FakePp()
    pp.banks[0].bit_length = 1024 * 8
    pp.banks[1].bit_length = 2048 * 8
    th, _, _ = _start_thread(pp=pp)
    try:
        th.submit(RT.CMD_START, 1)
        assert _wait_for(lambda: th.snapshot()["active"] == 1)
        snap = th.snapshot()
        assert snap["bank0_len"] == 1024 and snap["bank1_len"] == 2048
        assert snap["active_bank"] == "1"
        assert snap["src_bank"] == 1, "PL 侧解出的 bank 要进快照(硬件实际读哪块)"
        assert snap["present"] == 1
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_the_snapshot_is_pushed_to_the_uart_bridge():
    """`GET DDR PP` 走串口桥, 而桥只能看 `AwgCtl` —— 所以快照必须**推**过去。

    不推的话桥回的是 P0 基线金标(`ACTIVE 0 STATE IDLE`), 环形在播时那是假话, 而计划
    Phase 5 的验收原文就是"串口/SSH 观测 GET DDR PP 的 ACTIVE_BANK 交替"。
    """
    import awg_ctl as A
    ctl = A.AwgCtl()
    assert "ACTIVE 0" in ctl.handle("GET DDR PP"), "没推过时回基线金标"
    pp = FakePp()
    pp.banks[1].crc16 = 0xBEEF
    pp.banks[1].bit_length = 512 * 8
    th = RT.RingThread(FakeServer(), pp, ctl=ctl, poll_interval=0.001,
                       snapshot_interval=0.0)
    th.start()
    try:
        th.submit(RT.CMD_START, 1)
        assert _wait_for(lambda: "ACTIVE 1" in ctl.handle("GET DDR PP"))
        line = ctl.handle("GET DDR PP")
        assert "ACTIVE_BANK 1" in line
        assert "BANK1 VALID 1 LENGTH 512 CRC BEEF" in line, line
        assert "STATE RUNNING" in line, line
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_snapshot_marks_present_zero_before_any_state_exists():
    """守护进程在 RingThread 缺席时给 UI 的必须是"没有 DDR", 不是全 0 的假状态。

    构造后(线程还没跑出第一拍)读快照, 必须还是 `present=0` 的空白态 —— UI 靠这个区分
    "DDR 不可用"与"可用但空闲"。
    """
    pp = FakePp()
    server = FakeServer()
    th = RT.RingThread(server, pp, poll_interval=0.001, snapshot_interval=0.0)
    assert th.snapshot()["present"] == 0, "线程未启动时必须是空白快照"
    th.start()
    try:
        assert _wait_for(lambda: th.snapshot()["present"] == 1), "跑起来之后才报 present"
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_the_thread_keeps_running_after_a_bad_command(tmp_path):
    """一条命令出错不该拖垮线程 —— 后面的命令仍要被执行。"""
    th, pp, _ = _start_thread()
    try:
        th.submit(RT.CMD_FEED, (str(tmp_path / "nope.bin"), 0))
        th.submit(RT.CMD_START, 0)
        assert _wait_for(lambda: pp.started == [0]), "坏命令之后线程死了"
    finally:
        th.request_stop()
        th.join(timeout=2)


def test_quit_stops_the_thread():
    th, _, _ = _start_thread()
    th.submit(RT.CMD_QUIT)
    th.join(timeout=2)
    assert not th.is_alive()


def test_a_socket_that_cannot_open_is_recorded_not_fatal():
    """端口被占等: 记下来继续跑(状态机仍可被面板驱动), 不静默死。"""
    class BadServer(FakeServer):
        def open_socket(self):
            raise OSError(98, "Address already in use")
    pp = FakePp()
    th = RT.RingThread(BadServer(), pp, poll_interval=0.001, snapshot_interval=0.0)
    th.start()
    try:
        assert _wait_for(lambda: "socket" in th.last_error)
        th.submit(RT.CMD_START, 0)
        assert _wait_for(lambda: pp.started == [0]), "socket 开不了也要能起播"
    finally:
        th.request_stop()
        th.join(timeout=2)