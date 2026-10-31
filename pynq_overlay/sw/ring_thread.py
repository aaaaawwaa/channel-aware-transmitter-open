#!/usr/bin/env python3
"""`RingThread`: 一个线程拥有 DDR PP 的**全部写**, 并与面板/串口桥共存。

为什么要有它(而不是让面板直接调 `DdrPp`):

  * **写者唯一**。`DdrPp.start/stop/feed/service/udp_*` 会写 DMA 寄存器与 gpio7 两个通道,
    两处并发调用会让硬件落到"半条命令"的状态 —— 与 `AwgCtl` 需要一把锁是同一个道理, 只是
    这里的临界区横跨"收包 + 状态机 + 文件喂入", 用队列把写者收敛到一个线程更简单。
  * **面板不能阻塞**。`PanelSession` 的主循环还要 10 Hz 渲染 + 采样触摸按键, 而这里每拍
    要收包、轮询 DMA、必要时读一个 1 MiB 文件。放后台线程里, 面板只读 snapshot。

线程模型(与 `PanelSession` 的既有约定一致, 见 `pynq_status.py` 的 BridgeThread):
  * 本线程: `_pump`(收包) + `service_once`(状态机) + 排空命令队列 + 环形决策;
  * UI 线程: 只调 `submit()` 塞命令、只读 `snapshot()` —— **都不阻塞**;
  * `ctl` 是唯一跨线程共享的可变对象, 两边都在 `ctl.lock` 下碰它。

⚠ 环形的"喂"与"播"在本线程内**串行**发生, 不是真并行。这没问题: 喂的是非活动 bank, 而
1 MiB 在 400 kSym/s 下有 5.2 s 的播放预算, 一次 SD 读(几十~几百 ms)远小于它。但若将来符号率
提到 2 MSym/s 以上, 这个预算会缩到 1 s 以内 —— 那时要把读文件挪到另一个线程(读盘与落盘分离),
判据是"喂完一块的耗时 < 该块播放时长"。
"""
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import awg_ddr_pp as P  # noqa: E402
import feeder_decision as F  # noqa: E402

# 命令名
CMD_START = "start"          # payload: bank(0/1/2=自动)
CMD_STOP = "stop"
CMD_FEED = "feed"            # payload: (path, bank) —— bank 为 None 时喂非活动 bank
CMD_LOAD = "load"            # payload: (path, bank) —— **停播时**装填一个 bank(要 MODE OFF)
CMD_SET_RING = "set_ring"    # payload: bool
CMD_SET_QUEUE = "set_queue"  # payload: [path, ...]
CMD_QUIT = "quit"


class RingThread(threading.Thread):
    """拥有 `DdrPp` 全部写 + UDP socket + 环形决策的后台线程。

    构造注入: `server`(RingServer, 提供 `_pump`/`service_once`/`open_socket`)、
    `pp`、`ctl`(只用来读 digmod 影子/下发命令, 写都在 `ctl.lock` 下)、
    `feeder`(RingFeeder)、`load_fn`(读文件, 默认 `feeder_decision.load_waveform`)。
    """

    def __init__(self, server, pp, ctl=None, feeder=None, load_fn=None,
                 clock=time.monotonic, poll_interval=0.002, snapshot_interval=0.2):
        super(RingThread, self).__init__(daemon=True, name="ring")
        self.server = server
        self.pp = pp
        self.ctl = ctl
        self.feeder = feeder if feeder is not None else F.RingFeeder()
        self._load = load_fn if load_fn is not None else F.load_waveform
        self.clock = clock
        self.poll_interval = poll_interval
        self.snapshot_interval = snapshot_interval

        self._cmds = queue.Queue()
        self._lock = threading.Lock()          # 保护 snapshot / queue 这两个纯数据
        self._snapshot = self._blank_snapshot()
        self._queue = []                       # 文件名列表(环形按序喂)
        self._ring_on = False
        self._fed = 0
        self._last_snap_at = 0.0
        self._sock = None
        self._quit = threading.Event()
        self.last_error = ""

    # ---- UI 侧接口(全部非阻塞) ----

    def submit(self, cmd, payload=None):
        """塞一条命令给线程。**立即返回**, 不等执行结果。"""
        self._cmds.put((cmd, payload))

    def snapshot(self):
        """当前状态的一份拷贝。UI 每帧读这个, 不碰 `pp`。"""
        with self._lock:
            return dict(self._snapshot)

    def set_queue(self, paths):
        """设置环形队列(UI 线程持有它, 这里同步一份给线程用)。"""
        with self._lock:
            self._queue = list(paths)

    def queue(self):
        with self._lock:
            return list(self._queue)

    def set_ring(self, on):
        self._ring_on = bool(on)
        self.submit(CMD_SET_RING, bool(on))

    def request_stop(self):
        self._quit.set()
        self._cmds.put((CMD_QUIT, None))

    # ---- 线程体 ----

    def run(self):
        try:
            self._sock = self.server.open_socket()
        except Exception as exc:                     # 端口被占等: 记下来, 不静默死
            self.last_error = "socket: %s" % exc
            self._sock = None
        try:
            while not self._quit.is_set():
                self._drain_commands()
                if self._sock is not None:
                    try:
                        self.server._pump(self._sock)
                    except Exception as exc:
                        self.last_error = "pump: %s" % exc
                event = self.server.service_once()
                self._maybe_feed()
                self._publish(event)
        finally:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
            self._sock = None

    def _drain_commands(self):
        while True:
            try:
                cmd, payload = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                self._do(cmd, payload)
            except Exception as exc:                 # 一条命令出错不该拖垮整个线程
                self.last_error = "%s: %s" % (cmd, exc)

    def _do(self, cmd, payload):
        if cmd == CMD_QUIT:
            self._quit.set()
        elif cmd == CMD_START:
            self._do_start(payload)
        elif cmd == CMD_STOP:
            self.pp.stop()
            self._fed = 0
        elif cmd == CMD_FEED:
            path, bank = payload
            self._do_feed(path, bank)
        elif cmd == CMD_LOAD:
            path, bank = payload
            self._do_load(path, bank)
        elif cmd == CMD_SET_RING:
            self._ring_on = bool(payload)
            if not self._ring_on:
                self.feeder.reset()
        elif cmd == CMD_SET_QUEUE:
            self.set_queue(payload)
            if self._ring_on:
                # 队列换了, 游标可能越界 —— 从头开始比继续一个错位的位置更可预期。
                self.feeder.reset()

    def _do_start(self, bank):
        if bank is None or bank > 1:
            bank = 0 if self.pp.banks[0].valid else (1 if self.pp.banks[1].valid else 2)
        self._fed = 0
        self.pp.start(bank)

    def _do_feed(self, path, bank):
        data, reason = self._load(path)
        if data is None:
            self.last_error = "%s: %s" % (os.path.basename(path), reason)
            return
        if bank is None or bank > 1:
            bank = 0 if not self.pp.active else (self.pp.active_bank ^ 1)
        ok, why = self.pp.feed_file(bank, data, memory_view=self.pp.bank_mem[bank])
        if ok:
            self._flush(bank)
            self._fed += 1
        else:
            self.last_error = "%s: %s" % (os.path.basename(path), why)

    def _do_load(self, path, bank):
        """**停播时**把一块装进指定 bank(面板的「装填」动作)。

        为什么必须是一条独立的命令, 而不是塞进 `CMD_START` 里:

          * 装填要求 **`digmod_mode == 0`**(`feed_file` 的门禁 —— 数据源还在 AWG 手里时
            写 bank 是 BUSY), 而播放要求 **非 0**(`start()` 的门禁)。两者**不可能在同一个
            时刻成立**, 所以"起播"这条命令里**装不进**任何东西。
          * 于是顺序只能是: `MODE OFF` -> 装填 -> `MODE <调制>` -> 起播。这正是主机 GUI
            的做法(「大文件数据流」的手动上传要 MODE OFF), 面板照抄。

        装填成功后**游标前移**(`skip_past`), 否则环形第一拍会把同一个文件再喂一遍。
        只在该文件**在队列里**且这个 bank 是"从无到有"时才动游标 —— 重装同一个 bank
        不该改变环形位置。
        """
        if bank is None or bank > 1:
            bank = 0 if not self.pp.banks[0].valid else 1
        was_valid = self.pp.banks[bank].valid
        self._do_feed(path, bank)
        if was_valid or not self.pp.banks[bank].valid:
            return
        try:
            index = self._queue.index(path)
        except ValueError:
            return
        self.feeder.skip_past(index, len(self._queue))

    def _flush(self, bank):
        try:
            self.server._flush(bank)
        except Exception:
            pass

    def _maybe_feed(self):
        """环形: 播放中把队列下一块喂给非活动 bank(判据见 `feeder_decision`)。"""
        if not self._ring_on or not self._queue:
            return
        status = F.status_from_pp(self.pp)
        pick = self.feeder.next_feed(status, len(self._queue), False)
        if pick is None:
            return
        bank, index = pick
        self._do_feed(self._queue[index], bank)

    # ---- 快照 ----

    def _blank_snapshot(self):
        return {"present": 0, "active": 0, "state": "IDLE", "error_code": 0,
                "active_bank": "NONE", "next_bank": "NONE",
                "bank0_len": 0, "bank1_len": 0,
                "bank0_valid": 0, "bank1_valid": 0, "bank0_crc": 0, "bank1_crc": 0,
                "src_bank": None, "fed": 0, "ring_on": 0, "last_error": ""}

    def _publish(self, event):
        now = self.clock()
        if event is None and (now - self._last_snap_at) < self.snapshot_interval:
            return
        self._last_snap_at = now
        snap = F.status_from_pp(self.pp)
        view = self.pp.pl_view()
        out = {
            "present": 1,
            "active": snap["active"],
            "state": self.pp.state,
            "error_code": self.pp.error,
            "active_bank": snap["active_bank"],
            "next_bank": snap["next_bank"],
            "bank0_len": self.pp.banks[0].bit_length // 8,
            "bank1_len": self.pp.banks[1].bit_length // 8,
            # valid/CRC 也进快照: 串口 `GET DDR PP` 要报它们, 而它只能看快照(不许碰 DdrPp)。
            "bank0_valid": self.pp.banks[0].valid,
            "bank1_valid": self.pp.banks[1].valid,
            "bank0_crc": self.pp.banks[0].crc16,
            "bank1_crc": self.pp.banks[1].crc16,
            "src_bank": view["bank"],
            "fed": self._fed,
            "ring_on": 1 if self._ring_on else 0,
            "last_error": self.last_error,
        }
        with self._lock:
            self._snapshot = out
        if self.ctl is not None:
            # 同一个快照给串口桥 —— `GET DDR PP` 必须回**真**状态。不推的话桥回的是
            # P0 基线金标(ACTIVE 0 STATE IDLE), 环形在播时那是假话(计划 Phase 5 的验收
            # 原文就是"串口/SSH 观测 GET DDR PP 的 ACTIVE_BANK 交替")。
            self.ctl.set_pp_status(out)