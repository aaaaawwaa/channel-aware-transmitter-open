"""环形喂数决策:播放中把文件队列的下一块喂给非活动 bank。

纯逻辑、无 Qt,便于单测。调用方在每次 GET DDR PP 状态更新时调用 next_feed();
返回 (bank, index) 时启动一次上传,并在上传在途期间传 upload_in_flight=True,
避免同一块被重复喂。上传 COMMIT 后固件会把 NEXT_BANK 置为另一 bank,
据此自然停止喂数,直到下一次切换。
"""


def _bank_index(value):
    if value is None or value == "NONE":
        return None
    try:
        bank = int(value)
    except (TypeError, ValueError):
        return None
    return bank if bank in (0, 1) else None


class RingFeeder:
    """决定下一次把队列里的哪一块喂给哪个 bank。"""

    def __init__(self):
        self.cursor = 0

    def reset(self) -> None:
        self.cursor = 0

    def skip_past(self, index, queue_len) -> None:
        """手动把 queue[index] 预装到板上后,让环形从下一块继续。

        否则环形游标仍停在 0,会再把同一个文件喂一遍。
        """
        if queue_len <= 0:
            self.cursor = 0
            return
        self.cursor = (int(index) + 1) % queue_len

    def next_feed(self, status, queue_len, upload_in_flight):
        """返回 (bank, queue_index),或 None 表示当前无需喂数。

        status: 已解析的 GET DDR PP 状态 dict(或 None)。
        queue_len: 文件队列长度。
        upload_in_flight: 是否已有上传在途。
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
            return None   # 非活动 bank 已就绪(fresh),无需再喂
        other = 1 - active
        index = self.cursor % queue_len
        self.cursor = (self.cursor + 1) % queue_len
        return other, index
