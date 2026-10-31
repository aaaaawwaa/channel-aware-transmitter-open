#!/usr/bin/env python3
"""AC820 5" TFT 的 GT911 电容触摸驱动。

为什么自己写 I2C: 本镜像的设备树里**没有 i2c 节点**(没有 `/dev/i2c-*`, `modprobe i2c-dev`
也建不出来), 而触摸的 SCL/SDA 走的是 **PS I2C0(MIO50/51)**。但控制器寄存器本身是可用的,
所以直接按 UG585 第 20 章(附录 B.22, 软件名 XIICPS, 基址 `0xE0004000`)的时序在 Python 里
操作它 —— 不依赖内核驱动, 也不需要改设备树。

上板前必须知道的三件事(都是实测/查手册得到的, 不是猜的):

1. **MIO50/51 在本镜像下没有被复用成 I2C0**(实测 `MIO_PIN_50/51 = 0x1201`, 按 UG585 附录 B
   的位定义 `TRI_ENABLE`=b0、`Lx_SEL`=b1..7、`IO_Type`=b9..11、`PULLUP`=b12 解出来
   L0..L3_SEL 全 0 = 该脚的复位态/GPIO)。厂商 PYNQ 镜像的 ps7_init 里**根本没有
   MIO_PIN_50/51 这两条**(它的 PS7 把 I2C0 设成了 disabled), 所以没人配它们。
   `route_mio_to_i2c0()` 写 UG585 20.5.1 那个例子的值 `0x1240`(L3_SEL=2 = I2C0,
   LVCMOS18, 内部上拉, 3 态交给 I2C)—— 与厂商 Ubuntu 镜像 FSBL 的 ps7_init **逐位相同**。
2. **PS I2C0 到面板之间有一个 PCA9306 电平转换器**(原理图 14 页 U39): PS 侧 1.8V、
   面板侧 3.3V(面板侧上拉是 J5 旁的 4.7K R68~R71), 没有使能脚(常开)。
   **⚠ 读引脚电平前先开 GPIO 的 APB 时钟**(`enable_aper_clocks`): 本镜像里 Linux 把没用到的
   外设时钟关了, 实测 `APER_CLK_CTRL` 的 GPIO 位(b22)是 0 —— 读一个时钟被关掉的 GPIO 块,
   `DATA_RO` 会稳稳返回 0, 看上去就像"引脚被外部拉住", 能把排查带偏好几轮。
   (I2C0 的位 b18 是开的; 注意 b7 是 GEM1, 不是 I2C0。)
   现状(2026-09-13): 复用值/速率/两个地址/复位走线都与厂商一致, 控制器也确实在发(NACK),
   但 MIO50/51 在开时钟、释放总线、开内部上拉后**仍读回低电平**, 推挽驱动 1 也读不回 1
   —— 软件侧已无余地, 落点在板级电气或实物接触。
3. **GT911 需要复位释放**。`TP_RST` 是 PL 脚 R15, 由 `axi_gpio_fb` 通道2 的 bit1 驱动
   (见 `pynq_overlay/bd/create_bd.tcl`), 复位值是 0 = 器件被按在复位里。复位释放的那一刻
   GT911 会采样它的地址选择脚, 所以地址可能是 0x5D 或 0x14 —— 两个都试。
4. **控制器寄存器必须按 32 位访问**(UG585 明说不能用 byte/halfword 访问)。

寄存器位定义(全部来自 UG585 附录 B.22):
    CR  0x00  [15:14]DIV_A [13:8]DIV_B [6]CLR_FIFO [4]HOLD [3]ACKEN [2]NEA [1]MS [0]RD_WR
    SR  0x04  [8]BA(总线忙) [7]RXOVF [6]TXDV [5]RXDV
    ADDR 0x08 [9:0] 从机地址(普通模式用 [6:0])
    DATA 0x0C [7:0]
    ISR  0x10 [9]ARB_LOST [7]RX_UNF [6]TX_OVR [5]RX_OVR [4]SLV_RDY [3]TO [2]NACK
              [1]DATA [0]COMP   (粘滞, 写 1 清除)
    TRANS_SIZE 0x14  主发: 还没发出去的字节数-1; 主收: 还要收的字节数
    TIME_OUT   0x1C  复位值 0x1F
    波特率: SCL = CPU_1X / (22 * (DIV_A+1) * (DIV_B+1))   ← UG585 20.2.5

用法(板端, root):
    from pynq_touch import Touch  # 或者直接跑本文件做自检
    touch = Touch.open()          # 复用 MIO + 放开复位 + 探测地址
    print(touch.product_id())     # b'911'
    print(touch.touch())          # [(x, y)] 或 []
"""
import mmap
import struct
import sys
import time

# ---- 寄存器(Zynq PS I2C, UG585 附录 B.22)----
CR, SR, ADDR, DATA, ISR, TRANS_SIZE, TIME_OUT = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x1C
CR_DIV_A_SHIFT, CR_DIV_B_SHIFT = 14, 8
CR_CLR_FIFO, CR_HOLD = 1 << 6, 1 << 4
CR_ACKEN, CR_NEA, CR_MS, CR_RD_WR = 1 << 3, 1 << 2, 1 << 1, 1 << 0
SR_BA, SR_RXOVF, SR_TXDV, SR_RXDV = 1 << 8, 1 << 7, 1 << 6, 1 << 5
ISR_ARB_LOST, ISR_RX_UNF, ISR_TX_OVR = 1 << 9, 1 << 7, 1 << 6
ISR_RX_OVR, ISR_SLV_RDY, ISR_TO = 1 << 5, 1 << 4, 1 << 3
ISR_NACK, ISR_DATA, ISR_COMP = 1 << 2, 1 << 1, 1 << 0
ISR_ALL = 0x2FF                      # XIICPS_IXR_ALL_INTR_MASK: b0..b9 全清
                                     # (曾经用 0x2EE 漏掉 b0=COMP, 粘滞的 COMP 会让
                                     #  "等完成"立刻返回)

MIO_I2C0 = 0x1240                    # UG585 20.5.1 的 I2C0→MIO50/51 例子: L3=2, LVCMOS18, 上拉
MIO_TP_INT_OUT = 0x1600              # 厂商 Ubuntu ps7_init 给 MIO0(TP_INT) 的值: GPIO 输出/LVCMOS33/上拉
SLCR_MIO_PIN_0 = 0xF8000700
SLCR_MIO_PIN_50, SLCR_MIO_PIN_51 = 0xF80007C8, 0xF80007CC
# Zynq PS GPIO 寄存器映射。**权威来源**: Vivado 自带
# `embeddedsw/XilinxProcessorIPLib/drivers/gpiops_*/src/xgpiops_hw.h`:
#   DATA(bank)    = 0x040 + 4*bank     (RW)
#   DATA_RO(bank) = 0x060 + 4*bank     (RO —— 这才是脚上的电平)
#   DIRM(bank)    = 0x204 + 0x40*bank,   OEN(bank) = 0x208 + 0x40*bank
# ⚠ 两个 DATA 区**相距 0x20, 并不相邻**: 0x044 是 **bank1 的 DATA(输出寄存器)**,
#   不是 bank0 的 DATA_RO。照那个读"引脚电平", 读到的其实是 bank1 里残留的输出值 ——
#   它跟外部电路毫无关系, 却长得就像一组高低电平, 于是能把整条排查带进
#   "线被拉住 / 电平转换器不通"的方向(2026-09-13 实测踩到, 白花数轮)。
#   同一类前科: 读时钟被关掉的 GPIO 块(见 enable_aper_clocks)。
GPIO_DATA0, GPIO_DATA1 = 0x040, 0x044
GPIO_DATA0_RO, GPIO_DATA1_RO = 0x060, 0x064
GPIO_DIRM0, GPIO_OEN0 = 0x204, 0x208
GPIO_DIRM1, GPIO_OEN1 = 0x244, 0x248
SLCR_APER_CLK_CTRL = 0xF800012C
APER_GPIO0 = 1 << 22                 # GPIO_CPU_1XCLKACT
APER_I2C0 = 1 << 18                  # I2C0_CPU_1XCLKACT
CPU_1X_HZ = 111111111.0              # Zynq 默认 CPU_1x(UG585 表 20-1 就是按它算的)
SCL_HZ = 100000

# ---- GT911 ----
GT911_ADDRS = (0x5D, 0x14)           # 7 位地址: 由复位释放时 INT 脚的电平决定
REG_PRODUCT_ID = 0x8140
REG_STATUS = 0x814E
REG_POINT1 = 0x814F


def _divisors(scl_hz=SCL_HZ, clock_hz=CPU_1X_HZ, strict=True):
    """按 SCL = CPU_1X / (22*(a+1)*(b+1)) 选一组分频。

    分频范围是 DIV_A 1..4 × DIV_B 1..64, 所以最低只能到 CPU_1X/(22*256) ≈ 19.7 kHz
    (111.11 MHz 时)。strict=False 时取最接近的一组 —— 排查总线问题时经常要故意跑很慢,
    让弱上拉有时间把线拉起来。
    """
    best, best_err = None, None
    for a in range(4):
        for b in range(64):
            scl = clock_hz / (22.0 * (a + 1) * (b + 1))
            err = abs(scl - scl_hz)
            if err <= max(1000, scl_hz * 0.05):
                return a, b
            if best_err is None or err < best_err:
                best, best_err = (a, b), err
    if strict:
        raise ValueError("找不到合适的分频: %g Hz / %g Hz" % (scl_hz, clock_hz))
    return best


class DevMem:
    """把一段物理地址映射成 32 位寄存器的读写(控制器要求 32 位访问)。"""

    def __init__(self, base, size=0x1000):
        if base % size:
            raise ValueError("base 必须按页对齐")
        self._fd = open("/dev/mem", "r+b", buffering=0)
        self._mm = mmap.mmap(self._fd.fileno(), size, offset=base)
        self._base = base

    def _at(self, offset):
        if not 0 <= offset <= 0x1000 - 4:
            raise ValueError("offset 越界: 0x%X" % offset)
        return offset

    def read(self, offset):
        o = self._at(offset)
        return struct.unpack("<I", self._mm[o:o + 4])[0]

    def write(self, offset, value):
        o = self._at(offset)
        self._mm[o:o + 4] = struct.pack("<I", value & 0xFFFFFFFF)


class I2CError(IOError):
    """一次 I2C 传输失败, 带上 SR/ISR 快照 —— 板端出问题时这就是全部线索。"""

    def __init__(self, msg, sr=None, isr=None, trans=None):
        detail = ""
        if sr is not None:
            detail += " SR=0x%04X" % sr
        if isr is not None:
            detail += " ISR=0x%04X%s" % (isr, _isr_text(isr))
        if trans is not None:
            detail += " TRANS_SIZE=%d" % trans
        super(I2CError, self).__init__(msg + detail)


def _isr_text(isr):
    names = [(ISR_ARB_LOST, "ARB_LOST"), (ISR_RX_UNF, "RX_UNF"), (ISR_TX_OVR, "TX_OVR"),
             (ISR_RX_OVR, "RX_OVR"), (ISR_SLV_RDY, "SLV_RDY"), (ISR_TO, "TIMEOUT"),
             (ISR_NACK, "NACK"), (ISR_DATA, "DATA"), (ISR_COMP, "COMP")]
    return "(" + ",".join(n for b, n in names if isr & b) + ")"


def set_tp_int(level, mmio=None):
    """把 TP_INT(MIO0) 配成 GPIO 输出并驱动到指定电平; `level=None` 表示恢复成输入。

    **这一条最初漏了, 而厂商驱动正是这么做的**: goodix 的 `goodix_reset()` 在拉复位之前先把
    INT 设为输出并拉到目标电平 —— GT911 在**复位释放的那一刻**采样 INT 来决定 I2C 地址
    (低 = 0x5D, 高 = 0x14)。而本 PYNQ 镜像的 ps7_init **根本没有配 MIO0**(悬空), 厂商 Ubuntu
    镜像配的是 `MIO_PIN_0 = 0x1600`(GPIO 输出/LVCMOS33/上拉)。**INT 悬空是 GT911 起不来的
    已知原因**, 所以这条必须补上, 否则复位序列本身就不完整。
    """
    mio = mmio if mmio is not None else DevMem(0xF8000000)
    gpio = mmio if mmio is not None else DevMem(0xE000A000)
    enable_aper_clocks(mio)                   # GPIO 块没时钟的话下面全是空操作
    mio.write(SLCR_MIO_PIN_0 - 0xF8000000, MIO_TP_INT_OUT)
    if level is None:
        gpio.write(GPIO_DIRM0, gpio.read(GPIO_DIRM0) & ~1)
        gpio.write(GPIO_OEN0, gpio.read(GPIO_OEN0) & ~1)
        return None
    gpio.write(GPIO_DIRM0, gpio.read(GPIO_DIRM0) | 1)
    gpio.write(GPIO_OEN0, gpio.read(GPIO_OEN0) | 1)
    gpio.write(GPIO_DATA0, (gpio.read(GPIO_DATA0) & ~1) | (1 if level else 0))
    return gpio.read(GPIO_DATA0_RO) & 1


def enable_aper_clocks(mmio=None):
    """确保 GPIO 与 I2C0 的**外设 APB 时钟**是开的(UG585 附录 B: APER_CLK_CTRL)。

    **这条是实测踩出来的**: 本镜像里 Linux 会把没用到的外设时钟关掉, 板上实测该寄存器的
    GPIO 位(b22)是 0 —— 而**读一个时钟被关掉的 GPIO 块, DATA_RO 会稳稳返回 0**, 看上去
    就和"引脚被外部拉住"一模一样, 整条排查思路会被带偏(本次就带偏了几轮)。
    所以凡是要读引脚电平或操作 GPIO, 先开这两位; I2C0 的位同样确保是 1。
    """
    regs = mmio if mmio is not None else DevMem(0xF8000000)
    raw = SLCR_APER_CLK_CTRL - 0xF8000000
    cur = regs.read(raw)
    want = cur | APER_GPIO0 | APER_I2C0
    if want != cur:
        regs.write(raw, want)
    return regs.read(raw)


class I2CBus:
    """Zynq PS I2C0 的最小主模式轮询驱动。

    **只支持"主发"和"主收"两种单次传输**(以及由它们拼出来的寄存器读)。没有中断、没有
    重复起始: 读寄存器是"写完寄存器地址发 STOP, 再单独发一次读"。GT911 的寄存器指针会
    被这两次传输里的写锁存住, 所以这样够用; 若哪天遇到必须在一次传输里重复起始的器件,
    就得用 CR[HOLD] 把总线挂住再切方向(UG585 里那条机制还在)。
    """

    def __init__(self, base=0xE0004000, clock_hz=CPU_1X_HZ, scl_hz=SCL_HZ, mmio=None,
                 timeout=0.05):
        self._mmio = mmio if mmio is not None else DevMem(base)
        self._a, self._b = _divisors(scl_hz, clock_hz)
        self._timeout = timeout
        self.configure()

    # ---- 寄存器小工具 ----

    def _rd(self, offset):
        return self._mmio.read(offset) & 0xFFFF

    def _wr(self, offset, value):
        self._mmio.write(offset, value & 0xFFFF)

    def _cr(self, rd_wr=0, hold=0, clr_fifo=0):
        return ((self._a << CR_DIV_A_SHIFT) | (self._b << CR_DIV_B_SHIFT)
                | CR_ACKEN | CR_NEA | CR_MS
                | (CR_HOLD if hold else 0) | (CR_RD_WR if rd_wr else 0)
                | (CR_CLR_FIFO if clr_fifo else 0))

    def configure(self):
        """配置成 100 kHz 主模式, 清 FIFO, 并把粘滞中断全清掉。"""
        self._wr(TIME_OUT, 0xFF)
        self._wr(CR, self._cr(clr_fifo=1))
        self._wr(ISR, ISR_ALL)
        return self

    def _start(self, rd_wr=0):
        """本次传输开始前的准备: 清粘滞中断(写 1 清除), 并把 CR 里的方向位设成 rd_wr。

        **方向位在 CR[RD_WR], 不在 ADDR 里** —— 见 `write()` 的注释。
        """
        self._wr(ISR, ISR_ALL)
        self._wr(CR, self._cr(clr_fifo=1, rd_wr=rd_wr))
        self._wr(CR, self._cr(rd_wr=rd_wr))

    def _wait(self, bit, want=True, timeout=None):
        end = time.monotonic() + (self._timeout if timeout is None else timeout)
        while time.monotonic() < end:
            if bool(self._rd(SR) & bit) == want:
                return True
        return False

    def _settle(self, timeout=None):
        """等这次传输收尾: 总线不再忙, 且拿到 COMP(或出错)。返回 ISR。"""
        end = time.monotonic() + (self._timeout if timeout is None else timeout)
        while time.monotonic() < end:
            isr = self._rd(ISR)
            if isr & (ISR_NACK | ISR_TO | ISR_ARB_LOST):
                raise I2CError("传输被拒", self._rd(SR), isr, self._rd(TRANS_SIZE))
            if isr & ISR_COMP:
                return isr
        raise I2CError("传输超时(等 COMP)", self._rd(SR), self._rd(ISR), self._rd(TRANS_SIZE))

    # ---- 传输 ----

    def write(self, addr7, data):
        """主发: 一次传输写出一串字节(第一字节通常是寄存器地址高位)。

        ⚠ **ADDR 寄存器里放的是 7 位地址本身(add[6:0]), 不是 `addr<<1`, 也没有 R/W 位**
        —— 方向由 CR[RD_WR] 决定。这条曾经写错(照 PL 侧 AXI-IIC / i2cdetect 的"8 位形式"
        写成 `(addr7<<1)|rw`), 于是硬件拿到的地址变成 `(addr7<<1)&0x7F`: 0x5D→0x3A、
        0x14→0x28, **一个都不存在**, 总线永远 NACK。当时两三个地址都 NACK 本身就应该是
        "编码错了"的信号, 却被误读成"板子这一侧的电气问题"。
        依据(都是本机 Vivado 自带的可查证材料, 不是印象):
          * `xgpiops/iicps_*/src/xiicps_hw.h`: "Normal addressing mode uses add[6:0]",
            ADDR_MASK=0x3FF(10 位扩展地址就是 add[9:0] 全放进去);
          * `iicps_*/src/xiicps_xfer.c` 的 `XIicPs_SetupMaster`: RECVING_ROLE 时
            `ControlReg |= XIICPS_CR_RD_WR_MASK` —— 方向在 CR;
          * `iicps_*/src/xiicps_master.c`: 各 Send/Recv 都是
            `XIicPs_WriteReg(..., XIICPS_ADDR_OFFSET, SlaveAddr)` 原样写;
          * `iicps_*/examples/xiicps_eeprom_polled_example.c`: `EepromAddr[] = {0x54, 0x55}`、
            `MuxAddr[] = {0x74}` —— 都是**未左移的 7 位地址**。
        """
        if not data:
            raise ValueError("没有数据可写")
        self._start(rd_wr=0)
        self._wr(TRANS_SIZE, len(data) - 1)
        for byte in data:
            self._wr(DATA, byte & 0xFF)
        self._wr(ADDR, addr7)                     # 写从机地址 = 启动传输
        self._settle()

    def read(self, addr7, count):
        """主收: 收 count 个字节。设备不应答时**立刻**报错, 不干等超时。"""
        if not 1 <= count <= 255:
            raise ValueError("count 要在 1..255")
        self._start(rd_wr=1)                      # 读方向由 CR[RD_WR] 给出
        self._wr(TRANS_SIZE, count)
        self._wr(ADDR, addr7)
        out = bytearray()
        end = time.monotonic() + self._timeout
        while len(out) < count:
            isr = self._rd(ISR)
            if isr & (ISR_NACK | ISR_TO | ISR_ARB_LOST):
                raise I2CError("读被拒(第 %d 字节)" % (len(out) + 1),
                               self._rd(SR), isr, self._rd(TRANS_SIZE))
            if self._rd(SR) & SR_RXDV:
                out.append(self._rd(DATA) & 0xFF)
            elif time.monotonic() > end:
                raise I2CError("等 RXDV 超时(第 %d 字节)" % (len(out) + 1),
                               self._rd(SR), self._rd(ISR), self._rd(TRANS_SIZE))
        self._settle()
        return bytes(out)

    def scan(self, addrs=range(0x08, 0x78)):
        """扫一遍总线, 返回会应答的 7 位地址。

        **别拿 EEPROM/RTC 当对照**: 原理图(AC820-CB 第 14 页)上 EEPROM/RTC 挂在
        `EEP_SCL/RTC_SCL` 网(经另一个 PCA9306 接到 QSPI 脚那一路), **不在 PS I2C0 上**。
        这条总线上实际只有 GT911(经 PCA9306 U39 电平转换到 3.3V 面板侧)。所以扫描结果
        只有"GT911 应答"与"没人应答"两种, 后者无法区分是总线问题还是器件没起来。
        用 1 字节读来判 ACK 与 i2cdetect 的做法一致。
        """
        found = []
        for addr in addrs:
            try:
                self.read(addr, 1)
            except I2CError:
                continue
            found.append(addr)
        return found

    def read_regs(self, addr7, reg16, count):
        """读器件寄存器: 先写 16 位寄存器地址(大端), 再单独读 —— 见类注释。"""
        self.write(addr7, bytes([(reg16 >> 8) & 0xFF, reg16 & 0xFF]))
        return self.read(addr7, count)

    def write_regs(self, addr7, reg16, data):
        self.write(addr7, bytes([(reg16 >> 8) & 0xFF, reg16 & 0xFF]) + bytes(data))


class Touch:
    """GT911: 探测、读点、并把原始坐标换算成屏幕坐标。

    **默认参数就是这块屏(800x480 + GT911)的实测标定**(2026-09-13, `touch_demo.py --calib`
    五点标定, 最大残差 15 px —— 与手指点靶的抖动 ±10 px 同量级): 原始坐标与屏幕**同向、
    不交换也不翻转**, 量程基本就是 800x480, 只需要一点偏移(数字化区比显示区略大)。
    ⚠ 别照抄"GT911 面板默认是竖屏 480x800 所以要交换 XY"那套 —— 这块屏不是。
    换屏/换面板之后重新跑 `--calib` 并更新这里的默认值。
    """

    def __init__(self, bus, addr7, product=b"911", swap_xy=False, flip_x=False, flip_y=False,
                 raw_x_max=787, raw_y_max=476, width=800, height=480,
                 raw_x_min=-12, raw_y_min=-15):
        self.bus = bus
        self.addr = addr7
        self.product = product
        self.swap_xy = swap_xy
        self.flip_x = flip_x
        self.flip_y = flip_y
        # raw_*_max / raw_*_min = **屏幕两端边缘**那一侧读到的原始值(不是"分辨率"): max 映射到
        # width-1 / height-1, min 映射到 0。min 不能省: 实测这块屏的数字化区比显示区略大,
        # 只按纯比例换算会在边上差出十几像素。
        self.raw_x_max = raw_x_max
        self.raw_y_max = raw_y_max
        self.raw_x_min = raw_x_min
        self.raw_y_min = raw_y_min
        self.width = width
        self.height = height

    @classmethod
    def open(cls, bus=None, rst=None, clock_hz=CPU_1X_HZ, scl_hz=SCL_HZ,
             route=None, tp_int=set_tp_int, retry_seconds=2.0):
        """复用 MIO → 放开复位 → **轮询**探测地址, 返回一个可用的 Touch。

        ⚠ **复位放开之后器件要跑一段自检才肯稳定应答。** 实测(2026-09-13): 放开后 60ms 就发
        第一次读, 出现两种失败形态 —— 产品 ID 读到了、紧接着的读点却连续 NACK 二十多次; 或者
        第一次读就 NACK。厂商内核日志里 `Goodix-TS ... I2C communication failure: -6`
        (ENXIO=无 ACK) 与成功启动交替出现, 说的就是这个窗口。所以这里**按 retry_seconds
        轮询**(两个地址每轮都试), 而不是"一次定生死"; 单纯再等久一点也能过, 但轮询能自适应。

        `rst` 是一个可调用对象(True=放开复位, False=按住), 通常传
        `lambda v: overlay.axi_gpio_fb.channel2.write(0b10 if v else 0, 0b10)`。
        `route`/`bus` 可注入, 方便离线测试(默认就是真的 /dev/mem 与 MIO 复用)。
        """
        (route_mio_to_i2c0 if route is None else route)()
        tp_int(0)                     # INT 拉低 = 选 0x5D; 必须在放复位之前(见 set_tp_int)
        if rst is not None:
            rst(False)
            time.sleep(0.02)
            rst(True)                 # 释放复位: GT911 在这一刻采样地址选择脚
            time.sleep(0.10)          # 器件自检窗口(见 docstring)
        tp_int(None)                  # 复位已过, INT 交回输入(中断用)
        bus = bus if bus is not None else I2CBus(clock_hz=clock_hz, scl_hz=scl_hz)
        end = time.monotonic() + retry_seconds
        last = None
        while True:
            for addr in GT911_ADDRS:
                try:
                    got = bus.read_regs(addr, REG_PRODUCT_ID, 4)
                except I2CError as exc:               # 这个地址上没器件(或还没醒)
                    last = exc
                    continue
                if got[:3] == b"911":
                    return cls(bus, addr)
                last = I2CError("地址 0x%02X 上的产品 ID 不是 '911': %r" % (addr, got))
            if time.monotonic() >= end:
                break
            time.sleep(0.05)
        raise I2CError("没找到 GT911(0x5D/0x14 都没应答): %s" % last)

    def product_id(self):
        """产品 ID(0x8140, 4 字节, 期望 b'911\x00')。"""
        return self.bus.read_regs(self.addr, REG_PRODUCT_ID, 4)

    def read_points(self):
        """读一次触摸点, 返回原始坐标 [(x, y, id), ...](没有触摸时是空表)。

        兼容列表接口；需要区分无新数据与真正抬手的输入事件层使用 read_report。
        """
        return self.read_report() or []

    def read_report(self):
        """None=无新数据；[]=新上报的抬手；非空列表=新触点。

        0x814E bit7 是 READY，不是按下位。仅确认新报告，避免快轮询凭空合成抬手。
        """
        status = self.bus.read_regs(self.addr, REG_STATUS, 1)[0]
        if not status & 0x80:
            return None
        count = status & 0x0F
        points = []
        if count:
            raw = self.bus.read_regs(self.addr, REG_POINT1, min(count, 5) * 8)
            for i in range(min(count, 5)):
                x = raw[i * 8 + 1] | (raw[i * 8 + 2] << 8)
                y = raw[i * 8 + 3] | (raw[i * 8 + 4] << 8)
                points.append((x, y, raw[i * 8]))
        self.bus.write_regs(self.addr, REG_STATUS, [0])     # 清状态位, 允许下一次上报
        return points

    def to_screen(self, x, y):
        """原始坐标 -> 屏幕坐标(仿射: 先减 min 再按量程缩放, 可选交换 XY / 翻转, 最后夹住)。

        交换 XY 时**量程也要一起换**: 面板的原始 X 轴对应屏幕的竖直方向, 所以它要按
        屏幕高度去缩放。只换数值不换量程, 坐标会被按错误的轴长缩放(实测过)。
        `raw_*_min` 默认 0, 于是退化成纯比例 —— 数字化区域有内缩时它才起作用。
        """
        raw_x, raw_x_lo = self.raw_x_max, self.raw_x_min
        raw_y, raw_y_lo = self.raw_y_max, self.raw_y_min
        if self.swap_xy:
            x, y = y, x
            raw_x, raw_x_lo = raw_y, raw_y_lo
            raw_y, raw_y_lo = self.raw_x_max, self.raw_x_min
        span_x, span_y = raw_x - raw_x_lo, raw_y - raw_y_lo
        sx = int(round((x - raw_x_lo) * (self.width - 1) / float(span_x))) if span_x else 0
        sy = int(round((y - raw_y_lo) * (self.height - 1) / float(span_y))) if span_y else 0
        if self.flip_x:
            sx = self.width - 1 - sx
        if self.flip_y:
            sy = self.height - 1 - sy
        sx = 0 if sx < 0 else (self.width - 1 if sx > self.width - 1 else sx)
        sy = 0 if sy < 0 else (self.height - 1 if sy > self.height - 1 else sy)
        return sx, sy

    def touch(self):
        """当前触摸位置(屏幕坐标, 单点)或 None。"""
        points = self.read_points()
        if not points:
            return None
        return self.to_screen(points[0][0], points[0][1])


def route_mio_to_i2c0(mmio=None):
    """把 MIO50/51 复用成 I2C0 —— 出厂是 GPIO, 不复用控制器就发不出波形。

    值 0x1240 就是 UG585 20.5.1 那个例子的字面值: L3_SEL=2(I2C0)、LVCMOS18(MIO Bank1 的
    电压)、内部上拉使能、TRI_ENABLE=0(3 态交给 I2C 控制器)。本镜像没有 i2c 驱动, 也没有
    任何东西在用这两个脚, 所以改了不影响别的。
    """
    mio = mmio if mmio is not None else DevMem(0xF8000000)
    for offset in (SLCR_MIO_PIN_50 - 0xF8000000, SLCR_MIO_PIN_51 - 0xF8000000):
        mio.write(offset, MIO_I2C0)
    return MIO_I2C0


# 标定靶位置: 四角内缩 + 正中。**内缩**是为了让手指点得舒服(边框处点不准),
# 但靶在哪由我们自己画, 所以屏幕坐标是**真值** —— 这才是能标定的前提。
CALIB_TARGETS = ((60, 60), (739, 60), (60, 419), (739, 419), (399, 239))


def _line(samples):
    """最小二乘拟合 `y = a*x + b`, 返回 `(a, b, 残差平方和)`。"""
    n = float(len(samples))
    mx = sum(p[0] for p in samples) / n
    my = sum(p[1] for p in samples) / n
    sxx = sum((p[0] - mx) ** 2 for p in samples)
    if not sxx:
        return 0.0, 0.0, float("inf")
    a = sum((p[0] - mx) * (p[1] - my) for p in samples) / sxx
    b = my - a * mx
    return a, b, sum((a * p[0] + b - p[1]) ** 2 for p in samples)


def _axis_calib(screens, raws, span):
    """一条轴: 拟合 `raw = a*屏幕 + b`, 再折成 `(flip, min, max, 残差)`。

    斜率为负说明这一轴是反的 —— 折成"翻转 + 正斜率"后残差不变, 所以**选 flip 靠的是斜率的
    符号, 不是残差**(残差在翻与不翻之间是一样的, 用它选会二义)。
    """
    a, b, res = _line(list(zip(screens, raws)))
    if not a:
        return None
    # a<0 时把它折成 `raw = |a|*(span - 屏幕) + (a*span + b)`: 翻转后斜率变正, 残差不变。
    flip, k = a < 0, abs(a)
    lo = b + a * span if flip else b
    return {"flip": flip, "min": lo, "max": lo + k * span, "res": res}


def fit_calibration(pairs, width=800, height=480):
    """五点标定拟合: `[((raw_x, raw_y), (屏幕 x, 屏幕 y)), ...]` → `Touch` 的换算参数。

    把 `swap_xy` 两种可能各试一遍(每种先把两轴各自的 flip 定下来), 取残差平方和小的那个。
    **不靠人判断"要不要交换 XY"**: 靶子是我们自己画的, 屏幕坐标是真值, 残差自己会说话。

    返回值可以直接展开进构造函数: `Touch(bus, addr, **fit_calibration(pairs))`。
    """
    best = None
    for swap in (False, True):
        # swap 之后: 屏幕 X 来自原始 Y, 屏幕 Y 来自原始 X
        rx = [p[0][1 if swap else 0] for p in pairs]
        ry = [p[0][0 if swap else 1] for p in pairs]
        cal_x = _axis_calib([p[1][0] for p in pairs], rx, width - 1)
        cal_y = _axis_calib([p[1][1] for p in pairs], ry, height - 1)
        if cal_x is None or cal_y is None:
            continue
        total = cal_x["res"] + cal_y["res"]
        if best is None or total < best[0]:
            if swap:
                raw_x, raw_y = cal_y, cal_x
            else:
                raw_x, raw_y = cal_x, cal_y
            best = (total, dict(swap_xy=swap, flip_x=cal_x["flip"], flip_y=cal_y["flip"],
                                raw_x_min=raw_x["min"], raw_x_max=raw_x["max"],
                                raw_y_min=raw_y["min"], raw_y_max=raw_y["max"],
                                width=width, height=height))
    if best is None:
        raise ValueError("标定点不足以解出换算参数")
    return best[1]


class TouchInput:
    """把触摸接成 `pynq_lcd_ui.Ui` 的输入源(接口与 NullInput/ScriptedInput 相同)。

    UI 每帧问一次 `poll()`: 返回 (x, y, 按下) 或 None(这一帧没有状态)。GT911 的状态寄存器
    是"读走才更新"的, 所以这里每次都真去读一次 I2C; 出错就返回 None(不抛) —— 触摸掉线不该
    把整个界面循环带走。

    ⚠ **抬手必须回 `(x, y, False)`, 不能回 `None`**: `Ui._poll_input` 只在收到 `down=False`
    时才合成抬起沿并触发 `on_release` → `on_tap`。若抬手回 `None`(旧实现把"没有点"和"读失败"
    都归成 None), 控件会永远停在 `pressed` 状态、`on_tap` 一次也不触发 —— 现象就是"按钮按下去
    有反应、松手却什么也不发生"(板上踩到: 面板切换点了不跳)。所以这里用 `last_point` 记住
    最后一次按下的坐标, 抬手时把同一个坐标连同 `False` 一起回上去。
    """

    def __init__(self, touch):
        self.touch = touch
        self.errors = 0
        self.last_raw = None            # 最近一次的**原始**读数(标定/诊断用)
        self.last_point = None          # 最近一次按下时的屏幕坐标(抬手要回它)

    def poll(self):
        try:
            read_report = getattr(self.touch, "read_report", None)
            points = read_report() if read_report is not None else self.touch.read_points()
        except Exception:                                  # noqa: BLE001
            self.errors += 1
            self.last_raw = None
            # 读失败: 当作"没有新状态"(None), 不要凭空造一个抬起沿 —— 掉线时不该触发点击
            return None
        if points is None:
            return None           # READY=0 is no new evidence; retain held contact
        if not points:
            self.last_raw = None
            if self.last_point is None:
                return None
            point, self.last_point = self.last_point, None
            return (point[0], point[1], False)             # 抬手
        self.last_raw = (points[0][0], points[0][1])
        x, y = self.touch.to_screen(*self.last_raw)
        self.last_point = (x, y)
        return (x, y, True)

    def close(self):
        pass


class _ReplaySource:
    """给 `Ui(source=...)` 的适配器: 只重放 `TouchCursor` 本帧读到的点, 自己绝不碰 I2C。"""

    def __init__(self, cursor):
        self.cursor = cursor

    def poll(self):
        return self.cursor.replay()

    def close(self):
        pass


class TouchCursor:
    """**一帧只读一次**触摸, 并把同一个点重放给 UI。

    GT911 的状态寄存器是"读走才更新"的: 一帧里读两次, 第二次永远读到"没有新数据",
    界面上的按钮就点不动了(`touch_demo` 里踩过)。所以真实读取只发生在 `read()` 一次,
    UI 那头拿到的(`source()`)只是同一帧的副本 —— 准星与控件看到的是**同一时刻**的手指,
    不是先后两次采样。

    用法: `cursor = TouchCursor(touch)`; 每帧先 `cursor.read()`, 把 `cursor.point` 交给准星,
    再把 `cursor.source()` 交给 `Ui(source=...)`。
    """

    def __init__(self, touch):
        self.inner = TouchInput(touch)
        self.point = None

    def read(self):
        """读一次触摸(唯一的 I2C 读取点), 返回 (x, y, True) 或 None。"""
        self.point = self.inner.poll()
        return self.point

    def replay(self):
        return self.point

    def source(self):
        return _ReplaySource(self)

    @property
    def errors(self):
        return self.inner.errors

    def close(self):
        pass


def report_points(seconds, touch=None, out=None, period=0.02):
    """把触摸点打到 stdout —— 标定/验证用(`python3 pynq_touch.py points [秒]`)。

    **只碰 /dev/mem, 不碰 overlay**, 所以可以和守护进程同时跑。前提是 GT911 已经不在复位里:
    `TP_RST` 在 PL 侧(`axi_gpio_fb` 通道2 的 bit1), 位流复位值是 0 —— 要么守护进程开机时
    放开它(见 `pynq_status.attach_screen`), 要么先跑一次 `touch_demo.py --probe` 留在高。

    读数路径与 `TouchInput` 完全一致, 所以这里打出来的就是 UI 会看到的东西: `raw` 是器件
    原始坐标, `screen` 是 `to_screen()` 换算后的屏幕坐标 —— 标定要的就是这一对。
    """
    say = out if out is not None else (lambda s: print(s, flush=True))
    touch = touch if touch is not None else Touch.open()
    say("GT911 @ 0x%02X, product_id=%r" % (touch.addr, touch.product_id()))
    say("开始报点(raw -> screen), Ctrl-C 结束 ...")
    src = TouchInput(touch)
    end = time.monotonic() + seconds
    seen = 0
    while time.monotonic() < end:
        point = src.poll()
        if point:
            say("raw=%s -> screen=%s" % (src.last_raw, point[:2]))
            seen += 1
        time.sleep(period)
    say("结束: %d 个点, 读失败 %d 次" % (seen, src.errors))
    return seen


def release_bus(mmio=None):
    """把 I2C0 控制器关掉并清干净, 放开 SCL/SDA。

    **控制器状态是留在硬件里的**: 上一次传输如果停在半路(比如被 NACK), 进程退出了它也不会
    自己复位, 下一个进程看到的就是"线被拉着"。做总线级诊断之前必须先释放。
    """
    regs = mmio if mmio is not None else DevMem(0xE0004000)
    regs.write(CR, 0)                  # 关掉控制器, 引脚不再被它驱动
    regs.write(ISR, ISR_ALL)           # 清粘滞中断
    regs.write(CR, CR_CLR_FIFO)        # 清 FIFO(EN 为 0 时这一位只做清理)
    regs.write(CR, 0)
    time.sleep(0.002)


def drive_pins(mmio=None):
    """把 MIO50/51 当**推挽输出**驱动, 再回读电平 —— 用来区分"外面拉着"和"控制器拉着"。

    * 驱动 1 能回读到 1 → 没有任何外部器件把线拉住(之前读到低是控制器或复用的问题);
    * 驱动 1 仍回读 0 → 外面有东西（电平转换器/面板）把线按住, 总线在硬件上就是死的。

    ⚠ 推挽一定要写 **bank1 的 DATA = 0x044**。这里曾经写成 0x048(那是 bank2/EMIO 的 DATA),
    结果 DIRM/OEN 把 MIO50/51 配成了输出、输出的值却取自 bank1 里**残留的旧值**(两个脚都是 0)
    —— 于是"驱动 1"其实是在把线**往下灌**, 读回来当然还是 0。这让"外面把线按住了"看起来
    证据确凿, 而真相是我们自己在按(见上面寄存器映射那段注释)。

    驱动时与外部对拉会有几毫安电流, 所以每一档只保持极短时间, 读完立刻恢复。
    """
    mio = mmio if mmio is not None else DevMem(0xF8000000)
    gpio = mmio if mmio is not None else DevMem(0xE000A000)
    bits = (1 << (50 - 32)) | (1 << (51 - 32))
    if mmio is None:
        release_bus()
        enable_aper_clocks()          # GPIO 时钟被关的话, 读回来永远是 0
    out = []
    try:
        for level, name in ((bits, "驱动 1"), (0, "驱动 0")):
            # TRI_ENABLE=0(输出由 GPIO 控制) + LVCMOS18 + 内部上拉
            for off in (SLCR_MIO_PIN_50 - 0xF8000000, SLCR_MIO_PIN_51 - 0xF8000000):
                mio.write(off, (1 << 12) | (1 << 9))
            gpio.write(GPIO_OEN1, gpio.read(GPIO_OEN1) | bits)      # 输出使能
            gpio.write(GPIO_DIRM1, gpio.read(GPIO_DIRM1) | bits)    # 方向=输出
            gpio.write(GPIO_DATA1, (gpio.read(GPIO_DATA1) & ~bits) | level)
            time.sleep(0.002)
            raw = gpio.read(GPIO_DATA1_RO)
            out.append((name, bool(raw & (1 << (50 - 32))), bool(raw & (1 << (51 - 32)))))
    finally:
        gpio.write(GPIO_OEN1, gpio.read(GPIO_OEN1) & ~bits)
        gpio.write(GPIO_DIRM1, gpio.read(GPIO_DIRM1) & ~bits)
        for off in (SLCR_MIO_PIN_50 - 0xF8000000, SLCR_MIO_PIN_51 - 0xF8000000):
            mio.write(off, MIO_I2C0)
    return out


def bus_state(clock_hz=CPU_1X_HZ, scl_hz=SCL_HZ):
    """把控制器配成"已使能且空闲", 再看 `SR[BA]` —— 软件可见的"总线被拉住"指示。

    器件手册里 BA(Bus Active)=1 表示总线上有传输; 而**没人发传输却一直 BA=1**, 正是
    SCL/SDA 被按住(接地/被某器件拉住/转换器不通)的典型特征。这个判据不需要把脚从
    I2C 复用上抢回来, 所以比读引脚电平更接近真实工作状态。
    """
    regs = DevMem(0xE0004000)
    a, b = _divisors(scl_hz, clock_hz)
    regs.write(TIME_OUT, 0xFF)
    regs.write(ISR, ISR_ALL)
    regs.write(CR, (a << CR_DIV_A_SHIFT) | (b << CR_DIV_B_SHIFT)
               | CR_ACKEN | CR_NEA | CR_MS | CR_CLR_FIFO)
    regs.write(CR, regs.read(CR) & ~CR_CLR_FIFO)
    time.sleep(0.005)
    sr = regs.read(SR) & 0xFFFF
    isr = regs.read(ISR) & 0xFFFF
    release_bus(regs)
    return sr, isr


def read_bank1(mmio=None):
    """bank1(MIO32..53) 的引脚电平, bit n ↔ MIO(32+n)。**读数前先开外设时钟。**

    为什么要整块读: bank1 里有几个电平是**已知的**, 可以当场当对照用 ——
    MIO46/47 与 MIO48/49 是两路串口(TX 空闲必为高)、MIO40..45 是 SD、MIO52/53 是 GEM0。
    先确认这些位读出来符合预期, 再看 MIO50/51 才有意义; 否则又是在坏的观测通道上下结论。
    """
    enable_aper_clocks(mmio)
    gpio = mmio if mmio is not None else DevMem(0xE000A000)
    return gpio.read(GPIO_DATA1_RO)


def _bank1_text(raw):
    marks = {46: "串口A_TX", 47: "串口A_RX", 48: "串口B_TX", 49: "串口B_RX",
             50: "TP_SCL", 51: "TP_SDA"}
    return " ".join("MIO%d=%d%s" % (32 + i, (raw >> i) & 1,
                                    "(%s)" % marks[32 + i] if 32 + i in marks else "")
                    for i in range(22))


def diagnose_pins(mmio=None):
    """看一眼 MIO50/51 的**电平**, 用来区分"线被拉住"和"复用没配对"。

    顺序很重要: 先把控制器**关掉**(`release_bus`)再复用回 GPIO —— 否则看到的是控制器
    还握着总线时的电平, 与引脚本身无关(实测踩过)。

    分别**开着/关掉内部上拉**各读一次电平:
      * 开了内部上拉仍是低 → 有线把它拉住(器件卡死/短路), 或引脚压根没接;
      * 关掉内部上拉还是高 → 外部有上拉, 总线能自己拉高;
      * 只有开内部上拉才高 → 外部没有上拉, 那 100 kHz 下靠 Zynq 那几十 kΩ 的内部上拉
        根本拉不起来, 任何器件都不会 ACK(把 SCL 降到 ~20 kHz 就能扫到)。

    读完把 I2C0 的复用还回去。
    """
    mio = mmio if mmio is not None else DevMem(0xF8000000)
    gpio = mmio if mmio is not None else DevMem(0xE000A000)
    if mmio is None:
        release_bus()
        enable_aper_clocks()          # 同上: 时钟关着读引脚只会读到 0
    out = {}
    for name, pullup in (("内部上拉 ON", 1 << 12), ("内部上拉 OFF", 0)):
        value = pullup | (1 << 9) | 1          # LVCMOS18 + TRI_ENABLE=1(输入, 3 态)
        for off in (SLCR_MIO_PIN_50 - 0xF8000000, SLCR_MIO_PIN_51 - 0xF8000000):
            mio.write(off, value)
        time.sleep(0.01)
        raw = gpio.read(GPIO_DATA1_RO)          # bank1 的 DATA_RO = 0x064, 不是 0x04C
        out[name] = (bool(raw & (1 << (50 - 32))), bool(raw & (1 << (51 - 32))))
    for off in (SLCR_MIO_PIN_50 - 0xF8000000, SLCR_MIO_PIN_51 - 0xF8000000):
        mio.write(off, MIO_I2C0)
    return out


def _selftest(argv):
    """板端自检: `python3 pynq_touch.py [scan]`。

    `scan` 扫整条总线(最有用的一步: 板上还有 EEPROM 0x50 / RTC 0x51, 它们不需要触摸复位,
    能应答就说明 MIO 复用、上拉、控制器都对); 不带参数则只探 GT911。
    """
    print("APER_CLK_CTRL -> 0x%08X (已确保 GPIO/I2C0 的 APB 时钟为 1)" % enable_aper_clocks())
    print("MIO50/51 <- 0x%04X (I2C0)" % route_mio_to_i2c0())
    if "points" in argv:
        secs = next((float(a) for a in argv if a[:1].isdigit()), 60.0)
        report_points(secs)
        return 0
    if "pins" in argv:
        print("  bank1 全脚读数(先看串口那几位的对照): 0x%08X" % read_bank1())
        print("   " + _bank1_text(read_bank1()))
        for label, (scl, sda) in diagnose_pins().items():
            print("  %-14s SCL(MIO50)=%d SDA(MIO51)=%d" % (label, scl, sda))
        print("  判读: '内部上拉 OFF' 仍为 1 -> 外部有上拉; 只有 ON 才 1 -> 外部没有上拉")
        return 0
    if "drive" in argv:
        for name, scl, sda in drive_pins():
            print("  %-8s 回读 SCL(MIO50)=%d SDA(MIO51)=%d" % (name, scl, sda))
        print("  判读: '驱动 1' 回读到 0 -> 外面有东西把线按住（电平转换器/面板）")
        return 0
    if "bus" in argv:
        sr, isr = bus_state()
        print("  控制器已使能、空闲: SR=0x%04X%s ISR=0x%04X"
              % (sr, " BA(BUS_ACTIVE)=1 ← 总线被拉住" if sr & SR_BA else " BA=0(总线空闲)",
                 isr))
        print("  判读: 闲置时 BA=1 说明 SCL/SDA 没回到高电平(硬件层面) —— 不是驱动逻辑问题")
        return 0
    bus = I2CBus()
    print("CR 配置: DIV_A=%d DIV_B=%d" % (bus._a, bus._b))
    if "scan" in argv:
        found = bus.scan()
        print("总线扫描: %s" % (["0x%02X" % a for a in found] if found else "没有器件应答"))
        for known, name in ((0x50, "24LC64 EEPROM"), (0x51, "PCF8563 RTC"),
                            (0x5D, "GT911(地址 A)"), (0x14, "GT911(地址 B)")):
            print("  %-14s 0x%02X: %s" % (name, known, "应答" if known in found else "无应答"))
        return 0
    if "slow" in argv:
        a, b = _divisors(20000, CPU_1X_HZ, strict=False)
        slow = I2CBus(scl_hz=CPU_1X_HZ / (22.0 * (a + 1) * (b + 1)))
        print("降到 DIV_A=%d DIV_B=%d (~%.0f Hz) 再扫一遍"
              % (a, b, CPU_1X_HZ / (22.0 * (a + 1) * (b + 1))))
        found = slow.scan()
        print("慢速扫描: %s" % (["0x%02X" % x for x in found] if found else "仍然没有器件应答"))
        return 0
    for addr in GT911_ADDRS:
        try:
            got = bus.read_regs(addr, REG_PRODUCT_ID, 4)
        except I2CError as exc:
            print("  0x%02X: %s" % (addr, exc))
            continue
        print("  0x%02X: product_id=%r" % (addr, got))
    print("提示: 复位由 TP_RST(R15) 控制, 见模块注释。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest(sys.argv[1:]))
