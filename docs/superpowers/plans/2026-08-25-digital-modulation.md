# FPGA 内部 BPSK/QPSK/QAM 发射链路实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有 AWG 链路的前提下，用 Python 参考模型和独立 RTL 模块验证 FPGA 内部 BPSK、QPSK、4QAM、16QAM、64QAM I/Q 发射链路的可行性。

**Architecture:** 新增独立 `iq_digital_mod` RTL 模块，内部使用 PRBS7、固定 10 kSym/s 符号保持和标准方形 QAM 映射，输出两路 signed 8-bit I/Q。Python 参考模型与 RTL 使用完全相同的 seed、bit 顺序、映射表和整数归一化；本计划不修改 `awg_top`、GPIO、PS 协议或 GUI，只有独立模块通过 Python/RTL 验证后才进入下一份集成计划。

**Tech Stack:** Verilog-2001/SystemVerilog-compatible Vivado xsim、Python 3、NumPy、pytest、现有 `host/.venv`。

---

## 文件结构与职责

| 文件 | 职责 |
|---|---|
| `host/siggen/digital_mod.py` | Python 黄金模型：PRBS7、符号分组、BPSK/QPSK/4QAM/16QAM/64QAM 映射、符号/采样生成 |
| `host/tests/test_digital_mod.py` | Python 单元测试：周期、星座集合、Gray 映射、边界、符号保持和黄金序列 |
| `src/rtl/iq_digital_mod.v` | 独立 PL 模块：PRBS7、5000 周期计数、I/Q 映射和非法模式安全输出 |
| `src/tb/iq_digital_mod_tb.v` | RTL 自检 testbench：时序、点集、重复性、非法模式和首段黄金向量 |
| `docs/superpowers/specs/2026-08-25-digital-modulation-design.md` | 已批准设计规格；本计划不重复修改 |

现有 `src/rtl/awg_top.v`、`src/rtl/reg_bridge.v`、`scripts/create_bd.tcl`、`sdk/src/main.c`、`host/siggen/ui/*` 均不在第一阶段修改列表中。

---

### Task 1: 建立 Python 参考模型的失败测试

**Files:**
- Create: `host/tests/test_digital_mod.py`
- Test: `host/tests/test_digital_mod.py`

- [ ] **Step 1: 写入公共测试辅助函数和 PRBS 周期测试**

```python
import numpy as np
import pytest

from siggen.digital_mod import (
    BITS_PER_SYMBOL,
    MODES,
    PRBS7_SEED,
    constellation_points,
    generate_samples,
    generate_symbols,
    prbs7_bits,
)


def point_set(i, q):
    return set(zip(i.tolist(), q.tolist()))


def test_prbs7_has_fixed_prefix_and_period():
    bits = prbs7_bits(254, seed=PRBS7_SEED)
    assert PRBS7_SEED == 0x7F
    assert bits[:16].tolist() == [int(x) for x in "1111111000000100"]
    assert np.array_equal(bits[:127], bits[127:254])
```

- [ ] **Step 2: 写入各阶数星座集合测试**

```python
@pytest.mark.parametrize(
    "mode, expected_count",
    [("BPSK", 2), ("QPSK", 4), ("QAM4", 4),
     ("QAM16", 16), ("QAM64", 64)],
)
def test_constellation_has_expected_points(mode, expected_count):
    points = constellation_points(mode)
    assert points.shape == (expected_count, 2)
    assert len(point_set(points[:, 0], points[:, 1])) == expected_count
    assert np.max(np.abs(points)) <= 127


def test_qpsk_and_qam4_are_identical():
    qpsk = constellation_points("QPSK")
    qam4 = constellation_points("QAM4")
    assert np.array_equal(qpsk, qam4)
```

- [ ] **Step 3: 写入精确映射、范围和符号保持测试**

```python
def test_constellation_levels_are_exact():
    assert point_set(*constellation_points("BPSK").T) == {
        (127, 0), (-127, 0),
    }
    assert set(constellation_points("QAM16")[:, 0]) == {126, 42, -42, -126}
    assert set(constellation_points("QAM64")[:, 0]) == {
        126, 90, 54, 18, -18, -54, -90, -126,
    }


def test_generate_symbols_is_deterministic_and_bounded():
    for mode in MODES:
        i1, q1 = generate_symbols(mode, 127)
        i2, q2 = generate_symbols(mode, 127)
        assert np.array_equal(i1, i2)
        assert np.array_equal(q1, q2)
        assert np.max(np.abs(i1)) <= 127
        assert np.max(np.abs(q1)) <= 127


def test_generate_samples_holds_each_symbol_exactly():
    i_sym, q_sym = generate_symbols("QAM16", 4)
    i, q = generate_samples("QAM16", 4, sym_ticks=5)
    assert len(i) == len(q) == 20
    assert np.array_equal(i, np.repeat(i_sym, 5))
    assert np.array_equal(q, np.repeat(q_sym, 5))
```

- [ ] **Step 4: 运行失败测试，确认缺少模型 API**

Run:

```bash
host/.venv/Scripts/python -m pytest host/tests/test_digital_mod.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'siggen.digital_mod'`。

---

### Task 2: 实现 Python 黄金模型

**Files:**
- Create: `host/siggen/digital_mod.py`
- Test: `host/tests/test_digital_mod.py`

- [ ] **Step 1: 定义常量和 PRBS7 逻辑**

```python
import numpy as np

PRBS7_SEED = 0x7F
MODES = ("BPSK", "QPSK", "QAM4", "QAM16", "QAM64")
BITS_PER_SYMBOL = {
    "BPSK": 1,
    "QPSK": 2,
    "QAM4": 2,
    "QAM16": 4,
    "QAM64": 6,
}


def _validate_mode(mode):
    if mode not in MODES:
        raise ValueError(f"bad digital modulation mode: {mode}")


def _prbs7_next(state):
    return ((state & 0x3F) << 1) | (((state >> 6) ^ (state >> 5)) & 1)


def prbs7_bits(count, seed=PRBS7_SEED):
    if count < 0:
        raise ValueError("count must be non-negative")
    state = int(seed) & 0x7F
    if state == 0:
        raise ValueError("PRBS7 seed must be non-zero")
    bits = np.empty(count, dtype=np.uint8)
    for index in range(count):
        bits[index] = (state >> 6) & 1
        state = _prbs7_next(state)
    return bits
```

- [ ] **Step 2: 实现 Gray 轴电平和星座表**

```python
_QAM16_LEVELS = np.array([126, 42, -126, -42], dtype=np.int16)
_QAM64_LEVELS = np.array([126, 90, 18, 54, -126, -90, -18, -54], dtype=np.int16)


def constellation_points(mode):
    _validate_mode(mode)
    if mode == "BPSK":
        return np.array([[127, 0], [-127, 0]], dtype=np.int16)
    if mode in ("QPSK", "QAM4"):
        return np.array([
            [127, 127], [127, -127], [-127, -127], [-127, 127],
        ], dtype=np.int16)
    if mode == "QAM16":
        levels = _QAM16_LEVELS
    else:
        levels = _QAM64_LEVELS
    n = len(levels)
    return np.array([[i, q] for i in levels for q in levels], dtype=np.int16)
```

不定义或保留未被 `generate_symbols` 使用的 helper（包括 `_gray_axis`）；这里直接使用下面的向量化映射实现。

- [ ] **Step 3: 实现确定性符号生成和符号保持**

```python
def generate_symbols(mode, count, seed=PRBS7_SEED):
    _validate_mode(mode)
    if count < 0:
        raise ValueError("count must be non-negative")
    bps = BITS_PER_SYMBOL[mode]
    bits = prbs7_bits(count * bps, seed).reshape(count, bps)
    if mode == "BPSK":
        i = np.where(bits[:, 0] == 0, 127, -127)
        q = np.zeros(count, dtype=np.int16)
    elif mode in ("QPSK", "QAM4"):
        code = bits[:, 0] * 2 + bits[:, 1]
        i = np.choose(code, [127, 127, -127, -127]).astype(np.int16)
        q = np.choose(code, [127, -127, 127, -127]).astype(np.int16)
    elif mode == "QAM16":
        i_code = bits[:, 0] * 2 + bits[:, 1]
        q_code = bits[:, 2] * 2 + bits[:, 3]
        i = _QAM16_LEVELS[i_code]
        q = _QAM16_LEVELS[q_code]
    else:
        i_code = bits[:, 0] * 4 + bits[:, 1] * 2 + bits[:, 2]
        q_code = bits[:, 3] * 4 + bits[:, 4] * 2 + bits[:, 5]
        i = _QAM64_LEVELS[i_code]
        q = _QAM64_LEVELS[q_code]
    return i.astype(np.int16), q.astype(np.int16)


def generate_samples(mode, symbol_count, sym_ticks=5000, seed=PRBS7_SEED):
    if symbol_count < 0 or sym_ticks <= 0:
        raise ValueError("symbol_count must be non-negative and sym_ticks positive")
    i, q = generate_symbols(mode, symbol_count, seed)
    return np.repeat(i, sym_ticks), np.repeat(q, sym_ticks)
```

The approved QPSK/QAM4 table is fixed and complete: code 0/1/2/3 correspond respectively to bit patterns 00/01/10/11 and map to `(+, +)`, `(+, -)`, `(-, +)`, `(-, -)`. Use this exact table in both implementations; no follow-up mapping check is pending.

- [ ] **Step 4: 运行 Python 单元测试**

Run:

```bash
host/.venv/Scripts/python -m pytest host/tests/test_digital_mod.py -q
```

Expected: all tests in the new file pass. Then run the existing host suite:

```bash
host/.venv/Scripts/python -m pytest host/tests/ -q
```

Expected: existing tests remain green and no GUI/protocol behavior changes occur.

---

### Task 3: 先写 RTL testbench 和自检断言

**Files:**
- Create: `src/tb/iq_digital_mod_tb.v`
- Test: `src/tb/iq_digital_mod_tb.v`

- [ ] **Step 1: 建立 50 MHz 时钟、快速仿真参数和检查任务**

```verilog
`timescale 1ns / 1ps
module iq_digital_mod_tb;
    localparam integer SYM_TICKS = 5;
    localparam [2:0] BPSK = 3'd0, QPSK = 3'd1, QAM4 = 3'd2,
                     QAM16 = 3'd3, QAM64 = 3'd4;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg enable = 1'b0;
    reg [2:0] mode = BPSK;
    wire signed [7:0] i_sample;
    wire signed [7:0] q_sample;
    wire symbol_valid;
    integer errors = 0;
    integer symbol_index = 0;

    iq_digital_mod #(.SYM_TICKS(SYM_TICKS)) uut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
        .i_sample(i_sample), .q_sample(q_sample),
        .symbol_valid(symbol_valid)
    );

    always #10 clk = ~clk;

    task step;
        begin @(posedge clk); #1; end
    endtask

    task check;
        input condition;
        input [255:0] name;
        begin
            if (!condition) begin
                $display("FAIL %0s", name);
                errors = errors + 1;
            end
        end
    endtask
```

- [ ] **Step 2: 加入模式点集、边界和保持周期检查**

Testbench must run each mode for at least 512 symbols, record unique `(i_sample,q_sample)` pairs in integer counters, and assert:

```text
BPSK  -> 2 points and q_sample is always 0
QPSK  -> 4 points
QAM4  -> 4 points
QAM16 -> 16 points
QAM64 -> 64 points
```

For each symbol, sample once at `symbol_valid`, then take `SYM_TICKS-1` additional clock edges and assert I/Q remain unchanged. Assert no output absolute value exceeds 127.

- [ ] **Step 3: 加入 PRBS、重复运行和非法模式检查**

Use hierarchical observation of `uut.prbs` only for the testbench period assertion. Starting after the first valid symbol, capture states until `7'h7f` returns and assert the number of unique states is 127. Then set `enable=0` for two clocks, re-enable, and assert the first symbol matches the first run. Set `mode=3'd7` and assert `(i_sample,q_sample)==(0,0)` at the next symbol boundary.

- [ ] **Step 4: 加入首段黄金向量输出

At every `symbol_valid`, print a machine-readable line for the first 127 symbols of each mode:

```verilog
$display("GOLD mode=%0d index=%0d i=%0d q=%0d", mode, symbol_index,
         $signed(i_sample), $signed(q_sample));
```

在每个模式的测试循环开始前执行 `symbol_index = 0;`。每次 `symbol_valid` 处理完上述 `$display` 后执行 `symbol_index = symbol_index + 1;`，因此每个模式的 GOLD 行从索引 0 开始并连续递增，且 `symbol_index` 可直接用于首段 127 个符号的边界判断。

The first bit prefix must correspond to the approved seed:

```text
1111111000000100
```

The testbench must finish with exactly one of:

```text
PASS iq_digital_mod
FAIL iq_digital_mod: <count> errors
```

- [ ] **Step 5: 先运行 testbench，确认模块尚不存在导致编译失败**

Run from the repository root after Vivado tools are available on `PATH`:

```bash
xvlog -sv src/rtl/iq_digital_mod.v src/tb/iq_digital_mod_tb.v
```

Expected before Task 4: FAIL because `src/rtl/iq_digital_mod.v` does not exist. Do not skip this failure check.

---

### Task 4: 实现独立 RTL `iq_digital_mod`

**Files:**
- Create: `src/rtl/iq_digital_mod.v`
- Test: `src/tb/iq_digital_mod_tb.v`

- [ ] **Step 1: 定义接口、模式常量和 PRBS 状态**

```verilog
`timescale 1ns / 1ps
module iq_digital_mod #(
    parameter integer SYM_TICKS = 5000
) (
    input  wire clk,
    input  wire rst_n,
    input  wire enable,
    input  wire [2:0] mode,
    output reg signed [7:0] i_sample,
    output reg signed [7:0] q_sample,
    output reg symbol_valid
);
    localparam [2:0] MODE_BPSK = 3'd0;
    localparam [2:0] MODE_QPSK = 3'd1;
    localparam [2:0] MODE_QAM4 = 3'd2;
    localparam [2:0] MODE_QAM16 = 3'd3;
    localparam [2:0] MODE_QAM64 = 3'd4;

    reg [6:0] prbs;
    reg [31:0] sym_count;
    reg started;

    wire [6:0] prbs1 = {prbs[5:0], prbs[6] ^ prbs[5]};
    wire [6:0] prbs2 = {prbs1[5:0], prbs1[6] ^ prbs1[5]};
    wire [6:0] prbs3 = {prbs2[5:0], prbs2[6] ^ prbs2[5]};
    wire [6:0] prbs4 = {prbs3[5:0], prbs3[6] ^ prbs3[5]};
    wire [6:0] prbs5 = {prbs4[5:0], prbs4[6] ^ prbs4[5]};
    wire b0 = prbs[6];
    wire b1 = prbs1[6];
    wire b2 = prbs2[6];
    wire b3 = prbs3[6];
    wire b4 = prbs4[6];
    wire b5 = prbs5[6];
```

- [ ] **Step 2: 实现 exact Gray 电平函数**

Use combinational functions with the approved values; the 2-bit table must be `00→126`, `01→42`, `10→-126`, `11→-42`, and the 3-bit table must be `000→126`, `001→90`, `010→18`, `011→54`, `100→-126`, `101→-90`, `110→-18`, `111→-54`.

```verilog
function signed [7:0] qam16_level;
    input [1:0] code;
    begin
        case (code)
            2'b00: qam16_level = 8'sd126;
            2'b01: qam16_level = 8'sd42;
            2'b10: qam16_level = -8'sd126;
            default: qam16_level = -8'sd42;
        endcase
    end
endfunction

function signed [7:0] qam64_level;
    input [2:0] code;
    begin
        case (code)
            3'b000: qam64_level = 8'sd126;
            3'b001: qam64_level = 8'sd90;
            3'b010: qam64_level = 8'sd18;
            3'b011: qam64_level = 8'sd54;
            3'b100: qam64_level = -8'sd126;
            3'b101: qam64_level = -8'sd90;
            3'b110: qam64_level = -8'sd18;
            default: qam64_level = -8'sd54;
        endcase
    end
endfunction
```

- [ ] **Step 3: Implement combinational symbol mapping and PRBS consumption**

```verilog
reg signed [7:0] mapped_i, mapped_q;
reg [6:0] prbs_after;

always @* begin
    mapped_i = 8'sd0;
    mapped_q = 8'sd0;
    prbs_after = prbs;
    case (mode)
        MODE_BPSK: begin
            mapped_i = b0 ? -8'sd127 : 8'sd127;
            prbs_after = prbs1;
        end
        MODE_QPSK, MODE_QAM4: begin
            case ({b0, b1})
                2'b00: begin mapped_i = 8'sd127;  mapped_q = 8'sd127;  end
                2'b01: begin mapped_i = 8'sd127;  mapped_q = -8'sd127; end
                2'b10: begin mapped_i = -8'sd127; mapped_q = 8'sd127;  end
                2'b11: begin mapped_i = -8'sd127; mapped_q = -8'sd127; end
            endcase
            prbs_after = prbs2;
        end
        MODE_QAM16: begin
            mapped_i = qam16_level({b0, b1});
            mapped_q = qam16_level({b2, b3});
            prbs_after = prbs4;
        end
        MODE_QAM64: begin
            mapped_i = qam64_level({b0, b1, b2});
            mapped_q = qam64_level({b3, b4, b5});
            prbs_after = prbs5;
        end
        default: begin
            mapped_i = 8'sd0;
            mapped_q = 8'sd0;
            prbs_after = prbs;
        end
    endcase
end
```

- [ ] **Step 4: Implement reset, enable restart and 5000-cycle symbol hold**

```verilog
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        prbs <= 7'h7f;
        sym_count <= 0;
        started <= 1'b0;
        i_sample <= 0;
        q_sample <= 0;
        symbol_valid <= 1'b0;
    end else if (!enable) begin
        prbs <= 7'h7f;
        sym_count <= 0;
        started <= 1'b0;
        i_sample <= 0;
        q_sample <= 0;
        symbol_valid <= 1'b0;
    end else begin
        symbol_valid <= 1'b0;
        if (!started || sym_count == SYM_TICKS - 1) begin
            started <= 1'b1;
            sym_count <= 0;
            prbs <= prbs_after;
            i_sample <= mapped_i;
            q_sample <= mapped_q;
            symbol_valid <= 1'b1;
        end else begin
            sym_count <= sym_count + 1'b1;
        end
    end
end
endmodule
```

For a mode change while enabled, the next `symbol_valid` uses the new mode. The current symbol remains stable until that boundary. Invalid modes produce zero and do not advance PRBS.

- [ ] **Step 5: Run RTL simulation and fix only behavior failures**

Run:

```bash
xvlog -sv src/rtl/iq_digital_mod.v src/tb/iq_digital_mod_tb.v
xelab iq_digital_mod_tb -s iq_digital_mod_sim
xsim iq_digital_mod_sim -runall
```

Expected:

```text
PASS iq_digital_mod
```

If Vivado tools are not on `PATH`, use the installed Vivado 2018.3 `bin` directory for this validation; do not replace the RTL check with a different simulator without recording the reason. The current environment previously lacked `xvlog/xelab/xsim`, so a tool-path blocker must be reported rather than treated as a passing simulation.

---

### Task 5: Cross-check Python and RTL golden output

**Files:**
- Modify: `src/tb/iq_digital_mod_tb.v` only if output formatting needs correction
- Test: `host/tests/test_digital_mod.py`

- [ ] **Step 1: Generate the expected first 127 symbols from Python**

Run:

```bash
host/.venv/Scripts/python -c "from siggen.digital_mod import generate_symbols; import json; print(json.dumps({m: [[int(i), int(q)] for i, q in zip(*generate_symbols(m, 127))] for m in ('BPSK','QPSK','QAM4','QAM16','QAM64')}))"
```

Save the output only as a temporary review artifact; do not add generated logs to the repository.

- [ ] **Step 2: Compare the `GOLD` lines emitted by xsim**

Run:

```bash
xsim iq_digital_mod_sim -runall > iq_digital_mod.sim.log
```

For each mode, compare the first 127 `(i,q)` pairs in the log against `generate_symbols(mode, 127)`. The comparison must be exact integers, not a tolerance comparison. Any mismatch is a blocking failure because it indicates a Python/RTL bit-order or mapping divergence.

- [ ] **Step 3: Add a Python regression for the approved first-bit prefix and all mapping tables**

The final Python test file must retain:

```python
assert prbs7_bits(16).tolist() == [int(x) for x in "1111111000000100"]
assert np.array_equal(constellation_points("QPSK"),
                      constellation_points("QAM4"))
```

and the exact 16QAM/64QAM level assertions from Task 1.

- [ ] **Step 4: Run both validation layers**

```bash
host/.venv/Scripts/python -m pytest host/tests/test_digital_mod.py -q
xsim iq_digital_mod_sim -runall
```

Expected: Python tests pass and xsim ends with `PASS iq_digital_mod`.

---

### Task 6: Feasibility checkpoint and handoff boundary

**Files:**
- No source changes unless a preceding test exposes a defect
- Review: `docs/superpowers/specs/2026-08-25-digital-modulation-design.md`

- [ ] **Step 1: Run the complete host regression**

```bash
host/.venv/Scripts/python -m pytest host/tests/ -q
```

Expected: all existing tests plus the new digital-modulation tests pass. No protocol, preview, or standard-waveform test may be weakened or skipped.

- [ ] **Step 2: Confirm the independent-module acceptance checklist**

Mark the feasibility gate passed only when all are true:

```text
[ ] BPSK has exactly 2 points and Q=0
[ ] QPSK and QAM4 have exactly 4 identical points
[ ] QAM16 has exactly 16 points
[ ] QAM64 has exactly 64 points
[ ] PRBS7 has 127-state period and approved prefix
[ ] Python/RTL first 127 symbols match exactly
[ ] Every symbol holds exactly 5000 clocks
[ ] No I/Q value clips beyond ±127
[ ] Invalid mode outputs zero and does not advance PRBS
[ ] Existing host regression remains green
```

- [ ] **Step 3: Stop before integration**

Do not modify `awg_top`, `reg_bridge`, `create_bd.tcl`, `sdk/src/main.c`, `host/siggen/protocol.py`, `host/siggen/controller.py`, `host/siggen/ui/widgets.py`, or `host/siggen/ui/main_window.py` in this plan. After the checklist passes, create a new approved design/plan for the second phase covering the selected GUI layout A, the right-side preview mode switch, DAC I/Q muxing, protocol registers, and board-level verification.

---

## Plan self-review

- **Spec coverage:** PRBS7 seed/feedback/prefix, all five modes, standard square QAM restriction, exact Gray levels, 50MHz/10kSym/s/5000 ticks, signed 8-bit bounds, invalid mode behavior, Python/RTL comparison, and GUI deferral are each covered by Tasks 1–6.
- **Placeholder scan:** no `TBD`, `TODO`, “implement later”, or unspecified validation step is required; the only deferred work is explicitly bounded as a second-phase plan.
- **Type consistency:** Python uses `mode: str`, `generate_symbols(mode, count, seed)` and signed integer arrays; RTL uses the same mode order `BPSK=0, QPSK=1, QAM4=2, QAM16=3, QAM64=4`; `SYM_TICKS=5000` is the production default and `5` is testbench-only.
- **Safety boundary:** no existing hardware, protocol, GUI, or bitstream files are changed until the independent module passes its acceptance checklist.

Git commits are intentionally not included because no commit was requested; preserve the working tree and stage only explicitly requested files when committing later.
