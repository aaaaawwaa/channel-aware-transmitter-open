"""pynq_touch.py 的离线测试。

触摸这一步最容易错、又最难在板上看出来的是**协议层**: 寄存器位、坐标换算、状态位的
"读走才更新"。这些用假的 I2C 总线就能验; 控制器本身的时序(写 CR/TRANS_SIZE/ADDR 的顺序)
用假 MMIO 记下写序列来验。真板上的 /dev/mem 与 MIO 复用不在离线测试范围内。

跑法: python -m pytest pynq_overlay/sw/tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pynq_touch as t  # noqa: E402


# ---------------- 分频与寄存器 ----------------

def test_divisors_hit_roughly_100khz_within_the_field_widths():
    a, b = t._divisors(100000, 111111111.0)
    assert 0 <= a <= 3 and 0 <= b <= 63, "分频必须能放进 CR 的位域"
    scl = 111111111.0 / (22.0 * (a + 1) * (b + 1))
    assert abs(scl - 100000) / 100000 <= 0.05, "SCL 应当落在 100kHz 附近(实测 %g)" % scl


def test_cr_always_enables_master_normal_addressing_and_ack():
    bus = t.I2CBus.__new__(t.I2CBus)          # 不跑 __init__(那会去开 /dev/mem)
    bus._a, bus._b = 0, 50
    cr = bus._cr()
    assert cr & t.CR_MS and cr & t.CR_NEA and cr & t.CR_ACKEN
    assert not cr & t.CR_RD_WR and not cr & t.CR_CLR_FIFO and not cr & t.CR_HOLD
    assert (cr >> t.CR_DIV_B_SHIFT) & 0x3F == 50
    assert (cr >> t.CR_DIV_A_SHIFT) & 0x3 == 0

    assert bus._cr(rd_wr=1) & t.CR_RD_WR
    assert bus._cr(hold=1) & t.CR_HOLD
    assert bus._cr(clr_fifo=1) & t.CR_CLR_FIFO


def test_isr_decoding_names_the_error_bits():
    assert "COMP" in t._isr_text(t.ISR_COMP)
    txt = t._isr_text(t.ISR_NACK | t.ISR_TO)
    assert "NACK" in txt and "TIMEOUT" in txt
    assert t._isr_text(0) == "()"


def test_route_mio_writes_the_trm_example_value_to_both_pins():
    class FakeMmio:
        def __init__(self):
            self.writes = []

        def write(self, offset, value):
            self.writes.append((offset, value))

    mmio = FakeMmio()
    assert t.route_mio_to_i2c0(mmio) == 0x1240
    assert mmio.writes == [(t.SLCR_MIO_PIN_50 - 0xF8000000, 0x1240),
                           (t.SLCR_MIO_PIN_51 - 0xF8000000, 0x1240)]


def test_mio_value_decodes_to_i2c0_per_the_ug585_bit_layout():
    # 位定义: Lx_SEL 在低位(L3=b5..7), IO_Type=b9..11, PULLUP=b12, TRI_ENABLE=b0
    v = t.MIO_I2C0
    assert (v >> 5) & 0x7 == 2, "L3_SEL=2 才是 I2C0"
    assert (v >> 9) & 0x7 == 1, "LVCMOS18(MIO Bank1 的电压)"
    assert v & (1 << 12), "内部上拉使能"
    assert not v & 1, "TRI_ENABLE=0: 3 态交给 I2C 控制器"


def test_gpio_register_map_matches_the_xilinx_gpiops_header():
    # 出处: embeddedsw/XilinxProcessorIPLib/drivers/gpiops_*/src/xgpiops_hw.h
    #   DATA(bank)=0x040+4*bank, DATA_RO(bank)=0x060+4*bank
    #   DIRM(bank)=0x204+0x40*bank, OEN(bank)=0x208+0x40*bank
    # 写错这里的后果是"引脚电平"读的其实是别的 bank 的输出寄存器 —— 读数与外部电路无关,
    # 却看着像电平, 足以把排查方向彻底带偏。
    assert (t.GPIO_DATA0, t.GPIO_DATA1) == (0x040, 0x044)
    assert (t.GPIO_DATA0_RO, t.GPIO_DATA1_RO) == (0x060, 0x064)
    assert (t.GPIO_DIRM0, t.GPIO_OEN0) == (0x204, 0x208)
    assert (t.GPIO_DIRM1, t.GPIO_OEN1) == (0x244, 0x248)


class FakeGpioMmio:
    """够用的 GPIO 模拟: DATA_RO = DATA & DIRM & OEN, 于是"驱动了什么"能被验出来。"""

    def __init__(self):
        self.regs = {}

    def read(self, off):
        if off == t.GPIO_DATA1_RO:
            return (self.regs.get(t.GPIO_DATA1, 0) & self.regs.get(t.GPIO_DIRM1, 0)
                    & self.regs.get(t.GPIO_OEN1, 0))
        return self.regs.get(off, 0)

    def write(self, off, value):
        self.regs[off] = value


def test_drive_pins_actually_drives_the_lines_high():
    mmio = FakeGpioMmio()
    assert t.drive_pins(mmio=mmio) == [("驱动 1", True, True), ("驱动 0", False, False)]
    assert mmio.regs[t.GPIO_DIRM1] & (3 << 18) == 0, "收尾要把方向还成输入"
    assert mmio.regs[t.GPIO_OEN1] & (3 << 18) == 0, "收尾要关掉输出使能"


def test_diagnose_pins_reads_bank1_data_ro():
    class Fake(object):
        def __init__(self):
            self.writes = []
            self.value = 1 << (51 - 32)          # 只有 SDA 是高

        def write(self, off, value):
            self.writes.append((off, value))

        def read(self, off):
            return self.value if off == t.GPIO_DATA1_RO else 0

    mmio = Fake()
    assert t.diagnose_pins(mmio=mmio)["内部上拉 ON"] == (False, True)
    assert mmio.writes[-2:] == [(t.SLCR_MIO_PIN_50 - 0xF8000000, t.MIO_I2C0),
                                (t.SLCR_MIO_PIN_51 - 0xF8000000, t.MIO_I2C0)], "读完要还回复用"


# ---------------- 控制器时序(假 MMIO 记账) ----------------

class FakeMmio:
    """控制器替身: 记下所有写; 按脚本回答读(主发时 COMP, 主收时 RXDV+数据)。"""

    def __init__(self, rx=b"", nack=False):
        self.writes = []
        self.state = {}
        self.rx = bytearray(rx)
        self.nack = nack

    def read(self, offset):
        if offset == t.SR:
            return t.SR_RXDV if self.rx else 0
        if offset == t.ISR:
            return t.ISR_NACK if self.nack else t.ISR_COMP
        if offset == t.DATA:
            return self.rx.pop(0) if self.rx else 0
        return self.state.get(offset, 0)

    def write(self, offset, value):
        self.writes.append((offset, value))
        self.state[offset] = value

    def values(self, offset):
        return [v for o, v in self.writes if o == offset]


def make_bus(mmio):
    return t.I2CBus(mmio=mmio, timeout=0.2)


def test_configure_sets_timeout_and_clears_interrupts():
    mmio = FakeMmio()
    make_bus(mmio)
    assert mmio.values(t.TIME_OUT) == [0xFF]
    assert mmio.values(t.ISR) == [t.ISR_ALL], "配置时要把粘滞中断全清掉"
    cr = mmio.values(t.CR)
    assert cr and (cr[0] & t.CR_CLR_FIFO), "配置的第一笔是带清 FIFO 的 CR"


def test_write_sends_address_bytes_then_the_slave_address():
    mmio = FakeMmio()
    make_bus(mmio)
    mmio.writes = []
    make_bus(mmio).write(0x5D, b"\x81\x40")
    assert mmio.values(t.TRANS_SIZE)[-1] == 1, "两个字节: 还没发出去的字节数-1 = 1"
    assert mmio.values(t.DATA) == [0x81, 0x40], "寄存器地址是大端"
    assert mmio.values(t.ADDR)[-1] == 0x5D, "ADDR 放的是 7 位地址本身(add[6:0]), 不左移"
    assert not mmio.values(t.CR)[-1] & t.CR_RD_WR, "写传输的方向位在 CR, 这里是 0"


def test_read_fetches_the_requested_byte_count():
    mmio = FakeMmio(rx=b"911\x00")
    bus = make_bus(mmio)
    mmio.writes = []
    assert bus.read(0x5D, 4) == b"911\x00"
    assert mmio.values(t.TRANS_SIZE)[-1] == 4
    assert mmio.values(t.ADDR)[-1] == 0x5D, "读也是写 7 位地址本身"
    assert mmio.values(t.CR)[-1] & t.CR_RD_WR, "读传输的 R/W 位在 CR[RD_WR]"


def test_read_regs_writes_the_register_address_first():
    mmio = FakeMmio(rx=b"\x00\x00")
    bus = make_bus(mmio)
    mmio.writes = []
    bus.read_regs(0x14, 0x814E, 1)
    datas = mmio.values(t.DATA)
    assert datas[:2] == [0x81, 0x4E], "先写 16 位寄存器地址"
    assert mmio.values(t.ADDR) == [0x14, 0x14], "同一个 7 位地址, 方向由 CR 决定"
    assert not mmio.values(t.CR)[0] & t.CR_RD_WR, "写那一步 CR[RD_WR]=0"
    assert mmio.values(t.CR)[-1] & t.CR_RD_WR, "读那一步 CR[RD_WR]=1"


def test_a_nack_is_reported_as_an_error_with_the_register_snapshot():
    mmio = FakeMmio(nack=True)
    bus = make_bus(mmio)
    try:
        bus.write(0x5D, b"\x81\x40")
    except t.I2CError as exc:
        assert "NACK" in str(exc)
        assert "SR=0x" in str(exc) and "ISR=0x" in str(exc)
    else:
        raise AssertionError("NACK 应当报错, 而不是静默当成功")


# ---------------- GT911 协议 ----------------

class FakeBus:
    """记下读写了哪些寄存器, 按寄存器号回答。"""

    def __init__(self, regs=None):
        self.regs = dict(regs or {})
        self.read_log = []
        self.write_log = []

    def read_regs(self, addr7, reg16, count):
        self.read_log.append((addr7, reg16, count))
        if reg16 not in self.regs:
            raise t.I2CError("没有这个寄存器 0x%04X" % reg16)
        return self.regs[reg16][:count]

    def write_regs(self, addr7, reg16, data):
        self.write_log.append((addr7, reg16, bytes(data)))
        self.regs[reg16] = bytes(data)


def no_int():
    """离线测试用的 INT 替身: 不碰 /dev/mem, 只记录被怎么调过。"""
    calls = []

    def fake(level):
        calls.append(level)

    fake.calls = calls
    return fake


def test_open_picks_whichever_address_answers():
    class Bus(FakeBus):
        def read_regs(self, addr7, reg16, count):
            if addr7 == 0x5D:
                raise t.I2CError("没人应答")
            return super(Bus, self).read_regs(addr7, reg16, count)

    bus = Bus({t.REG_PRODUCT_ID: b"911\x00"})
    touch = t.Touch.open(bus=bus, route=lambda: None, tp_int=no_int())
    assert touch.addr == 0x14


def test_open_raises_when_nothing_answers():
    bus = FakeBus({})                       # 任何寄存器都读不到
    try:
        t.Touch.open(bus=bus, route=lambda: None, tp_int=no_int(), retry_seconds=0)
    except t.I2CError as exc:
        assert "GT911" in str(exc)
    else:
        raise AssertionError("没有器件时应当报错")


def test_open_rejects_a_device_that_is_not_a_gt911():
    bus = FakeBus({t.REG_PRODUCT_ID: b"ABC\x00"})
    try:
        t.Touch.open(bus=bus, route=lambda: None, tp_int=no_int(), retry_seconds=0)
    except t.I2CError as exc:
        assert "911" in str(exc)
    else:
        raise AssertionError("产品 ID 不对时应当报错")


def test_open_keeps_retrying_while_the_device_is_still_waking_up():
    # 复位放开后器件有一段自检窗口, 期间读会 NACK —— open 必须重试而不是一次定生死
    class SlowBus(FakeBus):
        def __init__(self, fails):
            FakeBus.__init__(self, {t.REG_PRODUCT_ID: b"911\x00"})
            self.fails = fails

        def read_regs(self, addr7, reg16, count):
            if self.fails > 0:
                self.fails -= 1
                raise t.I2CError("还没醒")
            return super(SlowBus, self).read_regs(addr7, reg16, count)

    touch = t.Touch.open(bus=SlowBus(3), route=lambda: None, tp_int=no_int(),
                         retry_seconds=1.0)
    assert touch.addr in t.GT911_ADDRS, "前几次 NACK 不该直接判死, 要接着轮询"


def test_open_toggles_the_reset_line():
    seen = []
    bus = FakeBus({t.REG_PRODUCT_ID: b"911\x00"})
    t.Touch.open(bus=bus, rst=seen.append, route=lambda: None, tp_int=no_int())
    assert seen == [False, True], "先按住复位再放开(GT911 在放开的沿采样地址脚)"


def test_open_drives_int_low_before_releasing_reset():
    # GT911 在复位释放的那一刻采样 INT 选地址, 所以 INT 必须先被拉低, 之后交回输入(中断用)
    events = []

    def rst(up):
        events.append("rst=%d" % int(up))

    def tp_int(level):
        events.append("int=%s" % level)

    bus = FakeBus({t.REG_PRODUCT_ID: b"911\x00"})
    t.Touch.open(bus=bus, rst=rst, route=lambda: None, tp_int=tp_int)
    assert events == ["int=0", "rst=0", "rst=1", "int=None"]


def test_read_points_parses_records_and_clears_the_status():
    # 状态: bit7=有新数据, 低 4 位=点数; 每个点 8 字节: id, x_lo, x_hi, y_lo, y_hi, size...
    raw = bytes([0x01, 0x10, 0x01, 0x20, 0x01, 0x00, 0x00, 0x00,      # id=1 x=0x0110 y=0x0120
                 0x02, 0x30, 0x00, 0x40, 0x00, 0x00, 0x00, 0x00])     # id=2 x=0x0030 y=0x0040
    bus = FakeBus({t.REG_STATUS: bytes([0x82]), t.REG_POINT1: raw})
    touch = t.Touch(bus, 0x5D)
    assert touch.read_points() == [(0x0110, 0x0120, 1), (0x0030, 0x0040, 2)]
    assert bus.write_log == [(0x5D, t.REG_STATUS, b"\x00")], "读走之后要清状态位"


def test_read_points_returns_nothing_when_the_host_is_not_asked_to_update():
    bus = FakeBus({t.REG_STATUS: bytes([0x00])})
    touch = t.Touch(bus, 0x5D)
    assert touch.read_points() == []
    assert bus.write_log == [], "没有新数据就不要扰动状态位"


def test_read_points_reports_a_lift_as_no_points():
    bus = FakeBus({t.REG_STATUS: bytes([0x80])})       # bit7 有新数据, 但点数为 0 = 抬手
    touch = t.Touch(bus, 0x5D)
    assert touch.read_points() == []


def test_read_report_distinguishes_no_new_data_from_fresh_release():
    bus = FakeBus({t.REG_STATUS: bytes([0])})
    touch = t.Touch(bus, 0x5D)
    assert hasattr(touch, 'read_report')
    assert touch.read_report() is None
    assert bus.write_log == []
    bus.regs[t.REG_STATUS] = bytes([0x80])
    assert touch.read_report() == []
    assert bus.write_log == [(0x5D, t.REG_STATUS, bytes([0]))]
    assert bus.write_log == [(0x5D, t.REG_STATUS, b"\x00")]


def test_to_screen_scales_and_swaps():
    # 竖屏配置的面板: 原始 480x800, 原始 Y 是屏幕 X、原始 X 是屏幕 Y
    touch = t.Touch(None, 0x5D, swap_xy=True, raw_x_max=479, raw_y_max=799,
                    raw_x_min=0, raw_y_min=0)
    assert touch.to_screen(0, 0) == (0, 0)
    assert touch.to_screen(479, 799) == (799, 479)
    assert touch.to_screen(240, 400) == (400, 240)


def test_default_calibration_matches_the_board_measurement():
    # 默认值 = 板上五点标定的实测锚点(2026-09-13)。改动默认值会让这里失败 —— 那正是目的。
    touch = t.Touch(None, 0x5D)
    assert (touch.swap_xy, touch.flip_x, touch.flip_y) == (False, False, False)
    for raw, want in (((43, 49), (60, 60)), ((730, 46), (739, 60)),
                      ((46, 422), (60, 419)), ((716, 409), (739, 419)),
                      ((402, 227), (399, 239))):
        got = touch.to_screen(*raw)
        assert max(abs(got[0] - want[0]), abs(got[1] - want[1])) <= 16, (raw, got, want)


def test_to_screen_clamps_out_of_range_raw_values():
    touch = t.Touch(None, 0x5D, swap_xy=True, raw_x_max=479, raw_y_max=799,
                    raw_x_min=0, raw_y_min=0)
    assert touch.to_screen(9999, 9999) == (799, 479)
    assert touch.to_screen(-5, -5) == (0, 0)


def test_to_screen_honours_the_flip_options():
    touch = t.Touch(None, 0x5D, swap_xy=False, flip_x=True, raw_x_max=799, raw_y_max=479,
                    raw_x_min=0, raw_y_min=0)
    assert touch.to_screen(0, 0) == (799, 0)
    assert touch.to_screen(799, 479) == (0, 479)


def test_to_screen_without_an_offset_is_unchanged():
    # raw_*_min 默认就是标定值; 显式给 0 时必须退化成纯比例
    touch = t.Touch(None, 0x5D, swap_xy=False, raw_x_max=799, raw_y_max=479,
                    raw_x_min=0, raw_y_min=0)
    assert touch.to_screen(400, 240) == (round(400 * 799 / 799.0), round(240 * 479 / 479.0))


def test_to_screen_subtracts_the_inset_of_the_digitizer():
    # 实测这块屏点屏幕 0 处读到 raw≈40、点 799 处读到 raw≈760: 有内缩, 必须减掉
    touch = t.Touch(None, 0x5D, swap_xy=False, raw_x_min=40, raw_x_max=760,
                    raw_y_min=30, raw_y_max=450)
    assert touch.to_screen(40, 30) == (0, 0)
    assert touch.to_screen(760, 450) == (799, 479)
    assert touch.to_screen(20, 20) == (0, 0), "缩到范围外要夹住"
    assert touch.to_screen(400, 240) == (400, 240), "中点落在屏幕中间(取整到最近的像素)"


# ---------------- 标定拟合 ----------------

def _synthetic_pairs(true):
    """按一组真参数反算出"点屏幕某处会读到什么", 再交给拟合器去还原。

    注意轴向: **`raw_*_min/max` 描述的是原始的那条轴**(名字里的 x/y 是原始侧),
    `swap_xy` 只决定哪条原始轴去喂屏幕 X —— 所以交换时喂屏幕 X 的是原始 Y 那条轴。
    """
    ref = t.Touch(None, 0x5D, **true)
    pairs = []
    for sx, sy in t.CALIB_TARGETS:
        tx = (799 - sx) if ref.flip_x else sx
        ty = (479 - sy) if ref.flip_y else sy
        hi_x, lo_x = ((ref.raw_y_max, ref.raw_y_min) if ref.swap_xy
                      else (ref.raw_x_max, ref.raw_x_min))
        hi_y, lo_y = ((ref.raw_x_max, ref.raw_x_min) if ref.swap_xy
                      else (ref.raw_y_max, ref.raw_y_min))
        comp = [0.0, 0.0]
        comp[1 if ref.swap_xy else 0] = lo_x + tx * (hi_x - lo_x) / 799.0
        comp[0 if ref.swap_xy else 1] = lo_y + ty * (hi_y - lo_y) / 479.0
        pairs.append((tuple(comp), (sx, sy)))
    return pairs


def test_fit_calibration_recovers_the_parameters_it_was_given():
    for true in (
        dict(swap_xy=False, flip_x=False, flip_y=False,
             raw_x_min=40, raw_x_max=760, raw_y_min=30, raw_y_max=450),
        dict(swap_xy=True, flip_x=False, flip_y=False,
             raw_x_min=0, raw_x_max=479, raw_y_min=0, raw_y_max=799),
        dict(swap_xy=True, flip_x=True, flip_y=True,
             raw_x_min=10, raw_x_max=470, raw_y_min=20, raw_y_max=780),
        dict(swap_xy=False, flip_x=True, flip_y=False,
             raw_x_min=0, raw_x_max=800, raw_y_min=0, raw_y_max=480),
    ):
        got = t.fit_calibration(_synthetic_pairs(true))
        assert got["swap_xy"] == true["swap_xy"], (got, true)
        assert (got["flip_x"], got["flip_y"]) == (true["flip_x"], true["flip_y"]), (got, true)
        for key in ("raw_x_min", "raw_x_max", "raw_y_min", "raw_y_max"):
            assert abs(got[key] - true[key]) < 1.0, (key, got, true)


def test_fit_calibration_picks_the_orientation_that_actually_fits():
    # 原始两轴与屏幕同向 -> 不该判成交换; 这正是板上实测的形态(角上 raw x 小、raw y 也小)
    pairs = [((51, 37), (0, 0)), ((767, 31), (799, 0)),
             ((20, 441), (0, 479)), ((768, 446), (799, 479)), ((335, 241), (399, 239))]
    got = t.fit_calibration(pairs)
    assert got["swap_xy"] is False, got
    assert got["flip_x"] is False and got["flip_y"] is False, got


def test_fit_calibration_rejects_too_few_points():
    try:
        t.fit_calibration([((1, 1), (0, 0))])
    except ValueError as exc:
        assert "标定" in str(exc)
    else:
        raise AssertionError("退化输入应当报错, 而不是给一组假参数")


def test_fit_calibration_output_drops_straight_into_touch():
    pairs = _synthetic_pairs(dict(swap_xy=True, flip_x=False, flip_y=True,
                                  raw_x_min=5, raw_x_max=475, raw_y_min=15, raw_y_max=785))
    touch = t.Touch(None, 0x5D, **t.fit_calibration(pairs))
    for (raw, want) in pairs:
        assert touch.to_screen(*raw) == want, (raw, want)


# ---------------- 接成 UI 的输入源 ----------------

def test_touch_input_reports_only_real_touches():
    # 竖屏量程的面板: 原始 (x=0x110=272, y=0x120=288) 在"交换 XY"下, 原始 Y 是屏幕 X(量程 800)、
    # 原始 X 是屏幕 Y(量程 480) —— 两个量程刚好等于屏宽/屏高, 所以映射是 1:1。
    bus = FakeBus({t.REG_STATUS: bytes([0x81]),
                   t.REG_POINT1: bytes([1, 0x10, 0x01, 0x20, 0x01, 0, 0, 0])})
    src = t.TouchInput(t.Touch(bus, 0x5D, swap_xy=True, raw_x_max=479, raw_y_max=799,
                               raw_x_min=0, raw_y_min=0))
    assert src.poll() == (288, 272, True)
    assert src.last_raw == (272, 288), "原始读数要留下来给标定用"
    bus.regs[t.REG_STATUS] = bytes([0x80])            # 抬手
    # ⚠ 抬手回的是 (x, y, **False**) 而不是 None —— UI 只认 down=False 来合成抬起沿并触发
    #   on_tap; 回 None 的话按钮永远停在 pressed、松手什么也不发生(板上踩过: 面板切换点了不跳)。
    assert src.poll() == (288, 272, False), "抬手必须带 down=False"
    assert src.poll() is None, "手已抬起且没再按下 -> 没有状态"


def test_touch_input_swallows_bus_errors_instead_of_killing_the_ui():
    class Dead(FakeBus):
        def read_regs(self, *a):
            raise t.I2CError("总线没了")

    src = t.TouchInput(t.Touch(Dead({}), 0x5D))
    assert src.poll() is None
    assert src.errors == 1, "出错次数要记下来, 但不该抛"


def test_a_bus_error_does_not_synthesise_a_release_edge():
    """读失败时不能凭空造抬起沿 —— 掉线不该被当成"用户松手点了这个按钮"。"""
    class Flaky(FakeBus):
        def __init__(self, regs):
            FakeBus.__init__(self, regs)
            self.fail = False

        def read_regs(self, *a):
            if self.fail:
                raise t.I2CError("总线掉了")
            return FakeBus.read_regs(self, *a)

    bus = Flaky({t.REG_STATUS: bytes([0x81]),
                 t.REG_POINT1: bytes([1, 0x10, 0x01, 0x20, 0x01, 0, 0, 0])})
    src = t.TouchInput(t.Touch(bus, 0x5D))
    assert src.poll()[2] is True
    bus.fail = True
    assert src.poll() is None, "读失败 -> None(不是抬起沿)"
    assert src.poll() is None, "还是失败 -> 还是 None"
    bus.fail = False
    bus.regs[t.REG_STATUS] = bytes([0x80])
    released = src.poll()
    assert released is not None and released[2] is False, "恢复后真抬手仍要报出来"
    assert src.poll() is None, "抬手只报一次"


def test_touch_cursor_reads_once_and_replays_the_same_point():
    # GT911 的状态是"读走才更新": 一帧里读两次, 第二次就没有新数据了 —— 所以全帧只能有一个读者
    class OnceBus(FakeBus):
        def read_regs(self, addr7, reg16, count):
            out = FakeBus.read_regs(self, addr7, reg16, count)
            if reg16 == t.REG_STATUS:
                self.regs[t.REG_STATUS] = bytes([0x80])       # 读完就当抬手
            return out

    bus = OnceBus({t.REG_STATUS: bytes([0x81]),
                   t.REG_POINT1: bytes([1, 0x10, 0x01, 0x20, 0x01, 0, 0, 0])})
    cursor = t.TouchCursor(t.Touch(bus, 0x5D, swap_xy=False, raw_x_min=0, raw_x_max=799,
                                   raw_y_min=0, raw_y_max=479))
    assert cursor.read() == (272, 288, True)
    assert cursor.replay() == (272, 288, True), "UI 那股拿到的是同一个点, 不能再读一次"
    assert cursor.source().poll() == (272, 288, True)
    # 清除 READY 后还没有新证据，不能凭空抬手；真实 READY+零触点才合成释放沿。
    assert cursor.read() is None, "无新数据保持原按下状态"
    bus.regs[t.REG_STATUS] = bytes([0x80])
    assert cursor.read() == (272, 288, False), "新上报的零触点才是抬手(down=False)"
    assert cursor.replay() == (272, 288, False), "抬手那一帧 UI 也要看到同一个值"
    assert cursor.read() is None, "抬手也报过之后才真的没有状态"
    # 标定要的就是这两个数(器件原始坐标 -> 屏幕坐标), 而且要走真 read_points/to_screen
    bus = FakeBus({t.REG_PRODUCT_ID: b"911\x00",
                   t.REG_STATUS: bytes([0x81]),
                   t.REG_POINT1: bytes([1, 0x10, 0x01, 0x20, 0x01, 0, 0, 0])})
    touch = t.Touch(bus, 0x5D, swap_xy=True, raw_x_max=479, raw_y_max=799,
                    raw_x_min=0, raw_y_min=0)
    lines = []
    assert t.report_points(0.05, touch=touch, out=lines.append, period=0.001) >= 1
    assert any("raw=(272, 288) -> screen=(288, 272)" in s for s in lines), lines
    assert any("GT911 @ 0x5D" in s for s in lines), lines
