#!/usr/bin/env python3
"""环形喂数决策(板端版): 播放中把文件队列的下一块喂给非活动 bank。

这是 `host/siggen/ring_feeder.py` 的移植 —— **逻辑必须与它逐条一致**, 因为两侧表达的是
同一条固件语义(`main.c:1662-1690` 的切换判定)。差别只在状态从哪来:

  * 主机侧: 每次 `GET DDR PP` 回包解析出来的 dict(串口/UDP 往返一次才有新状态);
  * 板端:    `DdrPp` 对象本身就在同一进程里, 直接读 `pp.active` / `pp.active_bank` /
            `pp.banks[other].fresh` —— 没有回包延迟, 也不会因为串口掉线而看不见状态。

`status_from_pp(pp)` 就是把前者变成后者的适配器, 于是 `RingFeeder` 的判据两边共用一份。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import awg_ddr_pp as P  # noqa: E402


def _bank_index(value):
    """`NONE`/None/越界 -> None; 0/1 -> int。与主机侧同名函数逐字一致。"""
    if value is None or value == "NONE":
        return None
    try:
        bank = int(value)
    except (TypeError, ValueError):
        return None
    return bank if bank in (0, 1) else None


def status_from_pp(pp):
    """把 `DdrPp` 的实时状态整理成 `RingFeeder.next_feed` 认得的 dict。

    ⚠ `next_bank` 的语义是**"非活动 bank 是否已就绪(fresh)"**, 不是"DdrPp 记的下一块"。
    主机侧是从回包文本里解析 `NEXT_BANK <n>|NONE`(`status_line()` 同款判定), 这里直接从
    `banks[other].fresh` 算 —— 同一个量, 少了"文本解析"这一层。
    """
    if not pp.active:
        return {"active": 0, "active_bank": "NONE", "next_bank": "NONE",
                "error_code": pp.error}
    active = pp.active_bank
    nxt = "NONE"
    if active < 2 and pp.banks[active ^ 1].fresh:
        nxt = str(active ^ 1)
    return {"active": 1,
            "active_bank": "NONE" if active > 1 else str(active),
            "next_bank": nxt,
            "error_code": pp.error}


class RingFeeder(object):
    """决定下一次把队列里的哪一块喂给哪个 bank。**纯逻辑, 不碰硬件。**"""

    def __init__(self):
        self.cursor = 0

    def reset(self):
        self.cursor = 0

    def skip_past(self, index, queue_len):
        """手动把 `queue[index]` 预装到板上后, 让环形从下一块继续。

        否则环形游标仍停在 0, 会再把同一个文件喂一遍。
        """
        if queue_len <= 0:
            self.cursor = 0
            return
        self.cursor = (int(index) + 1) % queue_len

    def next_feed(self, status, queue_len, upload_in_flight):
        """返回 `(bank, queue_index)`, 或 None 表示当前无需喂数。

        判据逐条对应固件: 未播放不喂(没人消费)、有错不喂(状态机已停)、活动 bank 解不出
        不喂、非活动 bank 已 fresh 不喂(它等着被切, 再喂就是覆盖)。全过则取游标指向的
        队列项、游标前进一格。
        """
        if not status or queue_len <= 0 or upload_in_flight:
            return None
        if not status.get("active"):
            return None
        if status.get("error_code"):
            return None
        active = _bank_index(status.get("active_bank"))
        if active is None:
            return None
        if _bank_index(status.get("next_bank")) is not None:
            return None
        other = 1 - active
        index = self.cursor % queue_len
        self.cursor = (self.cursor + 1) % queue_len
        return other, index


def load_waveform(path, max_bytes=P.BANK_BYTES):
    """读一个波形文件; 返回 `(data, reason)`。

    格式约定(与主机侧"大文件数据流"一致): **裸字节**, 4 B..1 MiB、4 字节对齐
    (`bit_length % 32 == 0`, 因为上传字节被当作符号比特)。超长只报错不截断 ——
    截断会静默改掉内容, 而 CRC/长度回读都会对不上, 不如当场说清楚。
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return None, "READ %s" % getattr(exc, "strerror", exc)
    n = len(data)
    if n == 0:
        return None, "EMPTY"
    if n > max_bytes:
        return None, "TOO BIG"
    if n % 4:
        return None, "ALIGN"
    return data, "OK"