"""pynq_lcd.py 的离线几何/寄存器测试。

板子上的 pynq 包在开发机上不存在, 所以这里用 `fake_pynq` 把它桩掉: allocate() 直接给一个
numpy 数组(带 device_address/flush), GPIO 通道只记录被写入的值。这样像素坐标、行距、
裁剪、抽头位域这些最容易写错的地方就能在开发机上验证, 不必占着板子。

跑法: python -m pytest pynq_overlay/sw/tests/
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_pynq  # noqa: E402
from fake_pynq import FakeOverlay, set_ring, set_taps  # noqa: E402

fake_pynq.install()

import pynq_lcd  # noqa: E402


def make(with_dbg=True):
    overlay = FakeOverlay(with_dbg=with_dbg)
    return pynq_lcd.Lcd(overlay), overlay


def test_starts_disabled():
    lcd, overlay = make()
    assert overlay.axi_gpio_fb.channel2.value == 0


def test_geometry():
    lcd, _ = make()
    assert lcd.array.shape == (pynq_lcd.HEIGHT, pynq_lcd.WIDTH)
    # 行距是 2048 B = 1024 像素, 不是 800: 读口的 stride 是固定的
    assert lcd._buf.shape == (pynq_lcd.HEIGHT, 1024)


def test_fill_touches_only_visible_columns():
    lcd, _ = make()
    lcd.fill(0x1234)
    assert lcd.array[0, 0] == 0x1234
    assert lcd.array[pynq_lcd.HEIGHT - 1, pynq_lcd.WIDTH - 1] == 0x1234
    assert lcd._buf[0, pynq_lcd.WIDTH] == 0


def test_rect_border_leaves_interior():
    lcd, _ = make()
    lcd.fill(0x1234)
    lcd.rect(10, 20, 30, 40, 0xABCD)
    assert lcd.array[20, 10] == 0xABCD
    assert lcd.array[40, 30] == 0xABCD
    assert lcd.array[30, 20] == 0x1234


def test_rect_fill():
    lcd, _ = make()
    lcd.rect(10, 20, 30, 40, 0x5678, fill=True)
    assert lcd.array[30, 20] == 0x5678
    assert lcd.array[20, 10] == 0x5678


def test_blit_clips_at_corner():
    lcd, _ = make()
    lcd.fill(0)
    img = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint16)
    lcd.blit(pynq_lcd.WIDTH - 2, pynq_lcd.HEIGHT - 2, img)
    assert lcd.array[478, 798] == 1
    assert lcd.array[479, 799] == 5
    assert lcd.array[479, 798] == 4


def test_blit_unshifted():
    lcd, _ = make()
    img = np.arange(4, dtype=np.uint16).reshape(2, 2)
    lcd.blit(100, 100, img)
    assert lcd.array[100, 100] == 0
    assert lcd.array[101, 101] == 3


def test_color_bars_match_the_pl_pattern():
    lcd, _ = make()
    lcd.color_bars()
    assert lcd.array[0, 0] == 0xFFFF
    assert lcd.array[0, 150] == 0xFFE0
    assert lcd.array[0, 250] == 0x07FF
    assert lcd.array[pynq_lcd.HEIGHT - 1, pynq_lcd.WIDTH - 1] == 0x0000


def test_show_writes_base_and_enable():
    lcd, overlay = make()
    lcd.show()
    assert overlay.axi_gpio_fb.channel1.value == lcd.address
    assert overlay.axi_gpio_fb.channel1.mask == 0xFFFFFFFF
    assert overlay.axi_gpio_fb.channel2.value == 1
    assert overlay.axi_gpio_fb.channel2.mask == 0x1
    assert lcd._buf.flushed


def test_disable_clears_only_the_enable_bit():
    lcd, overlay = make()
    lcd.show()
    lcd.disable()
    assert overlay.axi_gpio_fb.channel2.value == 0


def test_rgb565_round_trip_primaries():
    assert pynq_lcd.rgb565(255, 255, 255) == 0xFFFF
    assert pynq_lcd.rgb565(255, 0, 0) == 0xF800
    assert pynq_lcd.rgb565(0, 255, 0) == 0x07E0
    assert pynq_lcd.rgb565(0, 0, 255) == 0x001F
    assert pynq_lcd.rgb565(0, 0, 0) == 0x0000


# ---------------- 链路自检 ----------------

def test_ramp_signatures_are_distinct_per_row():
    # 自检要有意义, 任意两行的签名就必须不同 —— 否则行错位也照样"通过"
    lcd, _ = make()
    lcd.ramp()
    sigs = [lcd.line_signature(y) for y in range(pynq_lcd.HEIGHT)]
    assert len(set(sigs)) == pynq_lcd.HEIGHT
    # 也不能撞上全 0/全 F 那种退化值
    assert lcd.line_signature(0) != lcd.line_signature(pynq_lcd.HEIGHT - 1)


def test_signature_is_the_sum_of_the_visible_columns_only():
    lcd, _ = make()
    lcd.fill(0x0001)
    lcd._buf[:, pynq_lcd.WIDTH] = 0xFFFF          # 行距里的填充列不该进签名
    assert lcd.line_signature(7) == pynq_lcd.WIDTH


def test_ring_decode_bit_layout():
    lcd, overlay = make()
    set_ring(overlay, [100, 101, 102], reader_line=103, oor=1, sig=0xDEADBEEF, sig_line=99)
    faults = lcd._read_faults()
    assert faults["tag"] == [100, 101, 102]
    assert faults["reader_line"] == 103
    assert faults["reader_out_of_page"] is True
    assert faults["base_misaligned"] is False
    assert faults["display_stall"] is False
    assert lcd._read_signature() == 0xDEADBEEF
    assert lcd._read_signature_line() == 99


def test_self_check_passes_when_every_line_arrives_intact():
    # 读口轮流吐出 480 个合法签名 = 整屏每一行的像素都原样到达
    lcd, overlay = make()
    lcd.ramp()
    set_ring(overlay, [10, 11, 12])
    overlay.axi_gpio_dbg2.channel1.read_value = [
        lcd.line_signature(y) for y in range(pynq_lcd.HEIGHT)]
    report = lcd.self_check(samples=8000, diagnostics=4)
    assert report["ok"] is True
    assert report["signature_coverage"] == 1.0
    assert report["lines_verified"] == pynq_lcd.HEIGHT
    assert report["unknown_signatures"] == {}
    assert report["sticky_faults"] == {}


def test_self_check_catches_a_corrupted_line():
    # 有一个签名不在这 480 个合法值里 = 有一行的像素被读口改坏了。
    # 注意判据不看行号: 行号与签名分属两个 GPIO, 板端必然读到跨行的假配对。
    lcd, overlay = make()
    lcd.ramp()
    good = [lcd.line_signature(y) for y in range(pynq_lcd.HEIGHT)]
    overlay.axi_gpio_dbg2.channel1.read_value = [good[0], good[1], good[1] ^ 1]
    overlay.axi_gpio_dbg2.channel2.read_value = 1        # 只作诊断用
    report = lcd.self_check(samples=6, diagnostics=1)
    assert report["ok"] is False
    assert report["signature_coverage"] < 1.0
    assert hex(good[1] ^ 1) in report["unknown_signatures"]
    assert report["unknown_signatures"][hex(good[1] ^ 1)]["lines"] == [1]


def test_self_check_reports_incomplete_coverage_instead_of_ok():
    # 只看到一个合法签名: 链路没坏, 但"整屏 480 行都验过"这句话还没成立, 不能判过
    lcd, overlay = make()
    lcd.ramp()
    set_ring(overlay, [10, 11, 12], sig=lcd.line_signature(10))
    overlay.axi_gpio_dbg2.channel1.read_value = [lcd.line_signature(0)]
    report = lcd.self_check(samples=40, diagnostics=2)
    assert report["ok"] is False
    assert report["unknown_signatures"] == {}
    assert report["signature_coverage"] == round(1 / 480.0, 4)


def test_self_check_reports_the_sticky_faults_the_hardware_latched():
    lcd, overlay = make()
    lcd.ramp()
    overlay.axi_gpio_dbg2.channel1.read_value = [lcd.line_signature(y)
                                                 for y in range(pynq_lcd.HEIGHT)]
    set_ring(overlay, [10, 11, 12], tag_bad=True, bad_row=11, bad_got=200, stall=True)
    report = lcd.self_check(samples=8, diagnostics=8)
    assert report["ok"] is False
    assert report["sticky_faults"]["tag_bad"] == 8
    assert report["sticky_faults"]["display_stall"] == 8
    assert report["sticky_faults"]["bad_row"] == 11
    assert report["sticky_faults"]["bad_got"] == 200


def test_self_check_needs_the_bits_with_the_taps():
    lcd, _ = make(with_dbg=False)
    try:
        lcd.self_check(samples=1, diagnostics=1)
    except RuntimeError as exc:
        assert "axi_gpio_dbg" in str(exc)
    else:
        raise AssertionError("缺抽头时应当报错, 而不是静默通过")
