"""`touch_demo` 标定报告部分的离线测试。

`calibrate()` 本身要 overlay 与屏, 只能上板跑; 但"拟合 → 端到端校验 → 打印常数"这段是纯计算,
而它直接决定上板标出来的常数组对不对, 所以必须离线验: 一份按已知真值合成的读数进去,
还原出来的常数要能把每个靶打回原位。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_pynq  # noqa: E402

fake_pynq.install()                      # touch_demo 顶层就 import pynq

import pynq_touch as t  # noqa: E402
import touch_demo  # noqa: E402


def _pairs_for(true):
    """按真参数合成"点每个靶会读到什么"(与 to_screen 互逆, 所以是无噪声真值)。"""
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


def test_report_calibration_recovers_a_known_mapping():
    true = dict(swap_xy=False, flip_x=False, flip_y=False,
                raw_x_min=40, raw_x_max=760, raw_y_min=30, raw_y_max=450)
    lines = []
    params, worst = touch_demo.report_calibration(_pairs_for(true), out=lines.append)
    assert worst <= 1, lines
    assert params["swap_xy"] is False and not params["flip_x"] and not params["flip_y"]
    assert any("最大误差" in s for s in lines), lines


def test_report_calibration_prints_a_paste_ready_constant_line():
    true = dict(swap_xy=True, flip_x=False, flip_y=True,
                raw_x_min=12, raw_x_max=470, raw_y_min=24, raw_y_max=781)
    lines = []
    touch_demo.report_calibration(_pairs_for(true), out=lines.append)
    assert any("raw_x_min=12" in s and "raw_y_max=781" in s for s in lines), lines
    assert any(s.startswith("粘进 pynq_touch.py") for s in lines), lines


def test_report_calibration_reports_the_error_it_actually_sees():
    # 故意把最后一个靶的读数挪 40 px: 报告要如实反映"最大误差"变大, 而不是照样报 0
    pairs = _pairs_for(dict(swap_xy=False, flip_x=False, flip_y=False,
                            raw_x_min=0, raw_x_max=799, raw_y_min=0, raw_y_max=479))
    pairs[-1] = ((pairs[-1][0][0] + 40.0, pairs[-1][0][1]), pairs[-1][1])
    lines = []
    _, worst = touch_demo.report_calibration(pairs, out=lines.append)
    assert worst >= 20, lines
