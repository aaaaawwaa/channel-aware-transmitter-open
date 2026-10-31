"""开发机上的 pynq 打桩。

板子上才有 `pynq` 包, 所以在开发机上把 `allocate()` / `Overlay()` 换成假实现, 这样
坐标、行距、寄存器位域、控件命中这些最容易写错的地方都能离屏验证, 不必占着板子。
放在单独模块里, 是因为帧缓冲测试与 UI 测试要用同一套桩。
"""
import sys
import types

import numpy as np

FAKE_ADDRESS = 0x1F100000


class FakeBuf(np.ndarray):
    def __new__(cls, shape, dtype):
        return np.zeros(shape, dtype=dtype).view(cls)

    def __array_finalize__(self, obj):
        pass

    @property
    def device_address(self):
        return FAKE_ADDRESS

    def flush(self):
        self.flushed = True


class FakeChannel:
    def __init__(self):
        self.value = None
        self.mask = None
        self.read_value = None      # None -> 回读到写入的值(只写通道的行为)
        self._i = 0

    def write(self, value, mask):
        self.value, self.mask = value, mask

    def read(self):
        if isinstance(self.read_value, list):
            # 给一串值时会轮流吐, 用来模拟"抽头随着读口推进一直在变"
            out = self.read_value[self._i % len(self.read_value)]
            self._i += 1
            return out
        if self.read_value is not None:
            return self.read_value
        return self.value or 0


class FakeGpio:
    def __init__(self):
        self.channel1 = FakeChannel()
        self.channel2 = FakeChannel()


class FakeOverlay:
    def __init__(self, with_dbg=True):
        self.axi_gpio_fb = FakeGpio()
        if with_dbg:
            self.axi_gpio_dbg = FakeGpio()
            self.axi_gpio_dbg2 = FakeGpio()


class FakeMmio:
    """够 `PynqBackend` 用的假 MMIO(`MMIO(base, len)` + `.write/.read`)。"""

    def __init__(self, base, length):
        self.base_addr = base
        self.length = length
        self.words = {}

    def write(self, offset, value):
        self.words[offset] = value & 0xFFFFFFFF

    def read(self, offset):
        return self.words.get(offset, 0)


class AwgOverlay(FakeOverlay):
    """带**整条 AWG 控制面**的假 overlay: `axi_gpio_0..12` + `axi_bram_ctrl_1`。

    给"守护进程那条完整路径"(PynqBackend -> AwgCtl -> 面板事务 -> 真寄存器回读)用。
    与 `FakeOverlay` 分开是有意的: 老的桩**故意**没有 `axi_gpio_6`, 用来测"控制面建不起来
    时降级成只有状态页"那条路。
    """

    class _Bram(object):
        class _Region(object):
            base_addr = 0x42000000
            length = 0x10000

        def __init__(self):
            self.mmio = self._Region()

    def __init__(self):
        FakeOverlay.__init__(self)
        for n in range(13):
            setattr(self, "axi_gpio_%d" % n, FakeGpio())
        self.axi_bram_ctrl_1 = self._Bram()


def install():
    """把假 pynq 注册进 sys.modules(重复调用是空操作)。"""
    if "pynq" in sys.modules:
        return
    fake = types.ModuleType("pynq")
    fake.allocate = lambda shape, dtype: FakeBuf(shape, dtype)
    fake.Overlay = lambda *a, **k: AwgOverlay()
    fake.MMIO = FakeMmio
    # `Clocks` 在真 pynq 里是个可写属性的单例(`Clocks.fclk0_mhz = 50.0` 是板端组装的第一步)。
    # 这里给一个空类就够 —— 少了它, 板端组装会以 `ImportError` 而非**真正的原因**
    # (假 overlay 没有 `axi_dma_0`)降级, 于是测试断言的是导入失败, 不是它声称的那条路。
    fake.Clocks = type("Clocks", (object,), {})
    sys.modules["pynq"] = fake


class FakeLcd:
    """只当画布用的假 Lcd: 真的 Lcd 需要板上的 allocate(), 这里给一块同几何的数组。

    记录 flush/enable 次数, 好断言"一帧一次 flush""只在第一帧切一次源"这类行为。
    """

    address = FAKE_ADDRESS

    def __init__(self):
        self.array = np.zeros((480, 800), dtype=np.uint16)
        self.flushes = 0
        self.enables = 0

    def flush(self):
        self.flushes += 1

    def enable(self):
        self.enables += 1

    def disable(self):
        pass


def set_taps(overlay, d0, d1, sig, sig_line):
    """把自检抽头的 4 个 32 位值摆进假 GPIO, 模拟一次静态的链路状态。"""
    overlay.axi_gpio_dbg.channel1.read_value = d0 & 0xFFFFFFFF
    overlay.axi_gpio_dbg.channel2.read_value = d1 & 0xFFFFFFFF
    overlay.axi_gpio_dbg2.channel1.read_value = sig & 0xFFFFFFFF
    overlay.axi_gpio_dbg2.channel2.read_value = sig_line & 0x1FF


def set_ring(overlay, tags, reader_line=0, **sticky):
    d0 = (int(sticky.get("misaligned", 0)) << 28 | int(sticky.get("oor", 0)) << 27
          | tags[1] << 18 | tags[0] << 9 | reader_line)
    d1 = (int(sticky.get("stall", 0)) << 28 | int(sticky.get("tag_bad", 0)) << 27
          | sticky.get("bad_got", 0) << 18 | sticky.get("bad_row", 0) << 9 | tags[2])
    set_taps(overlay, d0, d1,
             sticky.get("sig", 0), sticky.get("sig_line", 0))
