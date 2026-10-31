# RRC 滚降因子可配置实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 RRC 成形滚降因子从编译期固定 0.35 变为运行时 {0.2, 0.35, 0.5} 三档可配置（默认 0.35），全数字模式生效。

**Architecture:** RTL 侧三套预生成系数（`rrc_coeffs.vh` 3×33 localparam）+ `rrc_filter.v` 内 3:1 组合 MUX → 寄存一拍 → 乘加树（系数仅在 MODE OFF 时更新，MUX 完全脱离数据路径，时序零影响）。GPIO11 bit[22:21] 携带 rolloff 选择（00=0.35 默认，01=0.2，10=0.5，11 按 0.35 处理，旧固件天然兼容）。PS 桥新增 `SET DIGMOD ROLLOFF`（仅 OFF 可设）+ `GET DIGMOD` 增 `ROLLOFF` 字段。上位机 `rrc.py` 参数化 alpha，数字调制页新增"滚降因子"下拉（仅 RRC+OFF 可编辑）。

**Tech Stack:** Verilog（Vivado 2018.3 / XSIM）、Xilinx SDK 2018.3 裸机 C（XUartPs+XGpio）、Python 3 + NumPy + pytest、PySide6 GUI。

---

## 文件结构

| 文件 | 责任 |
|------|------|
| `host/siggen/rrc.py` | 系数/滤波/matched-filter 理论模型，alpha 参数化 |
| `tools/generate_rrc_vectors.py` | 生成 3 套系数 `rrc_coeffs.vh` + 每档 GOLD `.mem` |
| `src/rtl/rrc_coeffs.vh` | 3×33 localparam 系数表（生成产物，入库） |
| `src/rtl/rrc_filter.v` | `rolloff[1:0]` 输入 + 3:1 MUX + 寄存 + 乘加树引用 `c[k]` |
| `src/rtl/rrc_digital_mod.v` | 新增 `rolloff` 端口透传给 `u_filter` |
| `src/rtl/reg_bridge.v` | `digmod_rolloff = r11[22:21]` 输出 |
| `src/rtl/awg_top.v` | 声明 `digmod_rolloff` 线网，桥→RRC 透传 |
| `src/tb/rrc_filter_tb.v` | 补 `.rolloff(2'd0)` 实例化 |
| `src/tb/rrc_digital_mod_tb.v` | 新增 `rolloff` reg + `check_gold_rolloff` 任务 + GOLD 档位 |
| `src/tb/rrc_expected_qpsk_a020.mem` / `_a050.mem` | 0.2/0.5 档 QPSK GOLD（生成） |
| `src/tb/awg_top_tb.v` | 新增 `digmod_rolloff` 层级断言 |
| `sdk/src/main.c` | ROLLOFF 命令 + GET 扩展 + pack_word_f |
| `host/siggen/protocol.py` | `DIGMOD_ROLLOFFS`、`digmod_rolloff_cmd`、13-token 解析 |
| `host/siggen/controller.py` | `DigitalModState.rolloff`、`set_digmod_rolloff` |
| `host/siggen/preview.py` | rolloff 透传进 `rrc_filter_symbols` |
| `host/siggen/ui/widgets.py` | 滚降因子下拉 + 锁定逻辑 |
| `host/siggen/ui/main_window.py` | 信号接线 + `_on_digmod_state` 透传 |
| `scripts/scope_verify.py` | `rrc_expected_waveform` rolloff 参数 + `RRC_ROLLOFF_CASES` + 用例循环 |
| `scripts/test_bridge.py` | ROLLOFF 协议序列 |
| `host/tests/*.py` | 各层单测更新 |

---

### Task 1: host rrc.py alpha 参数化

**Files:**
- Modify: `host/siggen/rrc.py:52-54`
- Test: `host/tests/test_rrc.py`

- [ ] **Step 1: 写失败测试**（`host/tests/test_rrc.py` 追加）

```python
def test_rrc_coefficients_support_three_rolloff_settings():
    for alpha, center, energy in ((0.2, 19427, 1_134_868_576),
                                  (0.35, 17953, 1_073_666_113),
                                  (0.5, 15042, 988_412_348)):
        coeff = rrc_coefficients(alpha=alpha)
        assert coeff.dtype == np.int16
        assert coeff.shape == (33,)
        assert np.array_equal(coeff, coeff[::-1])
        assert int(coeff[16]) == center
        assert int(np.dot(coeff.astype(np.int64), coeff.astype(np.int64))) == energy


def test_rrc_coefficients_reject_unsupported_rolloff():
    with pytest.raises(ValueError):
        rrc_coefficients(alpha=0.1)
```

> 注：center/energy 期望值是首次运行后回填（红→绿校验：先确认 0.35 行与现有常量一致，0.2/0.5 行以实测值回填）。0.35 行 `17953 / 1_073_666_113` 已由 `test_rrc_constants_and_coefficients_are_symmetric` 确认。

- [ ] **Step 2: 运行确认失败**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_rrc.py -q`
Expected: FAIL — `rrc_coefficients(alpha=0.2)` 抛 `ValueError("only the frozen RRC configuration is supported")`

- [ ] **Step 3: 实现**

`host/siggen/rrc.py:6` 后加 `RRC_ROLLOFFS = (0.2, 0.35, 0.5)`；`:52-54` 改为：

```python
def rrc_coefficients(alpha=RRC_ALPHA, sps=RRC_SPS, span=RRC_SPAN):
    if sps != RRC_SPS or span != RRC_SPAN:
        raise ValueError("only the frozen RRC sps/span configuration is supported")
    if alpha not in RRC_ROLLOFFS:
        raise ValueError(f"unsupported RRC rolloff factor: {alpha}")
```

- [ ] **Step 4: 运行确认通过**（回填 0.2/0.5 期望值后）

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_rrc.py -q`
Expected: PASS（含既有测试）

- [ ] **Step 5: Commit**

```bash
git add host/siggen/rrc.py host/tests/test_rrc.py
git commit -m "feat: rrc.py 支持 0.2/0.35/0.5 三档滚降因子"
```

---

### Task 2: 系数生成脚本 + 3 套系数表 + GOLD 向量

**Files:**
- Modify: `tools/generate_rrc_vectors.py`
- Regenerate: `src/rtl/rrc_coeffs.vh`、`src/tb/rrc_expected.mem`
- Create: `src/tb/rrc_expected_qpsk_a020.mem`、`src/tb/rrc_expected_qpsk_a050.mem`
- Test: `host/tests/test_rrc.py:109-117`

- [ ] **Step 1: 写失败测试**（`host/tests/test_rrc.py` 改 `test_generated_rrc_files_have_33_coefficients_and_paired_samples`）

```python
def test_generated_rrc_files_have_three_coefficient_sets_and_paired_samples():
    coeff_file = Path("src/rtl/rrc_coeffs.vh")
    vector_file = Path("src/tb/rrc_expected.mem")
    a020 = Path("src/tb/rrc_expected_qpsk_a020.mem")
    a050 = Path("src/tb/rrc_expected_qpsk_a050.mem")
    assert coeff_file.exists() and vector_file.exists() and a020.exists() and a050.exists()
    text = coeff_file.read_text(encoding="ascii")
    assert text.count("RRC_COEFF_") == 99
    assert text.count("_A20") == 33 and text.count("_A35") == 33 and text.count("_A50") == 33
    lines = [line for line in vector_file.read_text(encoding="ascii").splitlines() if line]
    assert len(lines) == 16 * 4 + 32
    assert all(len(line) == 8 for line in lines)
    for extra in (a020, a050):
        extra_lines = [line for line in extra.read_text(encoding="ascii").splitlines() if line]
        assert len(extra_lines) == 16 * 4 + 32
        assert all(len(line) == 8 for line in extra_lines)
    # 三档 GOLD 首采样互不相同,确保档位真正切换
    first = [f.read_text(encoding="ascii").splitlines()[0]
             for f in (vector_file, a020, a050)]
    assert len(set(first)) == 3
```

- [ ] **Step 2: 运行确认失败**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_rrc.py::test_generated_rrc_files_have_three_coefficient_sets_and_paired_samples -q`
Expected: FAIL — 旧断言 `.count("RRC_COEFF_") == 33`、`a020/a050` 不存在

- [ ] **Step 3: 重写生成脚本**

`tools/generate_rrc_vectors.py` 整体替换为：

```python
#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

from siggen.digital_mod import generate_symbols
from siggen.rrc import rrc_coefficients, rrc_filter_symbols

ROLLOFFS = (0.2, 0.35, 0.5)
ROLLOFF_SUFFIX = {0.2: "A20", 0.35: "A35", 0.5: "A50"}


def _coeff_block(alpha):
    coeff = rrc_coefficients(alpha=alpha)
    suffix = ROLLOFF_SUFFIX[alpha]
    return "".join(
        f"localparam signed [15:0] RRC_COEFF_{n:02d}_{suffix} = 16'sh{int(v) & 0xffff:04x};\n"
        for n, v in enumerate(coeff)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="QPSK",
                        choices=["BPSK", "QPSK", "QAM4", "QAM16", "QAM64"])
    parser.add_argument("--symbols", type=int, default=16)
    parser.add_argument("--alpha", type=float, default=0.35,
                        choices=[0.2, 0.35, 0.5])
    parser.add_argument("--coeff", type=Path, default=ROOT / "src/rtl/rrc_coeffs.vh")
    parser.add_argument("--vectors", type=Path, default=ROOT / "src/tb/rrc_expected.mem")
    args = parser.parse_args()

    # 系数表: 每档都写入全部三组,供 rrc_filter 3:1 MUX 选用。
    coeff_text = ("`ifndef RRC_COEFFS_VH\n`define RRC_COEFFS_VH\n"
                  + "".join(_coeff_block(a) for a in ROLLOFFS)
                  + "`endif\n")
    args.coeff.write_text(coeff_text, encoding="ascii")

    # 向量: 按 --alpha 选档(与 RTL TB 的 rolloff 档位对齐)。
    coeff = rrc_coefficients(alpha=args.alpha)
    i_sym, q_sym = generate_symbols(args.mode, args.symbols)
    i_out, q_out = rrc_filter_symbols(i_sym, q_sym, coeff)
    args.vectors.write_text(
        "\n".join(f"{int(i) & 0xffff:04x}{int(q) & 0xffff:04x}" for i, q in zip(i_out, q_out))
        + "\n",
        encoding="ascii",
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 生成全部文件**（回到工程根目录）

```bash
host/.venv/Scripts/python.exe tools/generate_rrc_vectors.py --alpha 0.35 --vectors src/tb/rrc_expected.mem
host/.venv/Scripts/python.exe tools/generate_rrc_vectors.py --alpha 0.2 --vectors src/tb/rrc_expected_qpsk_a020.mem
host/.venv/Scripts/python.exe tools/generate_rrc_vectors.py --alpha 0.5 --vectors src/tb/rrc_expected_qpsk_a050.mem
```
Expected: 生成 `rrc_coeffs.vh`（99 localparam）+ 3 个 `.mem`。校验 `git diff src/rtl/rrc_coeffs.vh` 中 `_A35` 组与旧文件逐行一致（0.35 回归基线）。

- [ ] **Step 5: 运行确认通过**（先删除旧 33-count 断言，上面已替换）

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_rrc.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tools/generate_rrc_vectors.py src/rtl/rrc_coeffs.vh src/tb/rrc_expected.mem src/tb/rrc_expected_qpsk_a020.mem src/tb/rrc_expected_qpsk_a050.mem host/tests/test_rrc.py
git commit -m "feat: 系数表三档预生成 + 各档 RRC GOLD 向量"
```

---

### Task 3: rrc_filter.v rolloff 选择

**Files:**
- Modify: `src/rtl/rrc_filter.v`
- Modify: `src/tb/rrc_filter_tb.v:49-55`

- [ ] **Step 1: 改测试台实例化（先让旧 TB 可编译）**

`src/tb/rrc_filter_tb.v:49-55` 的 `rrc_filter uut` 增加 `.rolloff(2'd0)`：

```verilog
    rrc_filter uut (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .sample_tick(sample_tick), .phase(phase),
        .load_symbol(load_symbol), .rolloff(2'd0),
        .new_i(new_i), .new_q(new_q),
        .i_sample(i_sample), .q_sample(q_sample),
        .sample_valid(sample_valid)
    );
```

- [ ] **Step 2: 运行旧 TB 确认（改 RTL 前基线）**

```bash
"$LOCALAPPDATA/../Vivado/Vivado/2018.3/bin/xvlog.bat" --sv src/rtl/rrc_filter.v src/rtl/rrc_coeffs.vh src/tb/rrc_filter_tb.v && "$LOCALAPPDATA/../Vivado/Vivado/2018.3/bin/xelab.bat" rrc_filter_tb -s sim && "$LOCALAPPDATA/../Vivado/Vivado/2018.3/bin/xsim.bat" sim -runall
```
Expected: 编译失败（`rrc_filter` 尚无 `rolloff` 端口）——这正是红。若编译器路径不同，用 `cli-anything-vivado config show` 取实际 `Vivado/2018.3/bin` 前缀。

- [ ] **Step 3: 实现 rrc_filter.v**

端口 `load_symbol` 后加：

```verilog
    input wire [1:0] rolloff,          // 00=0.35 默认, 01=0.2, 10=0.5, 11=0.35(保留)
```

在 `reg signed [15:0] p0_q [0:8];` 之后加系数数组与系数选择函数：

```verilog
    // 系数选择: rolloff 仅在 !enable(MODE OFF)时变化, 寄存后乘加树使用稳定
    // 寄存器系数, 使 3:1 MUX 完全脱离数据路径(时序零影响)。
    reg signed [15:0] c [0:32];
    integer k;

    function signed [15:0] rrc_coeff;
        input integer idx;
        input [1:0] alpha;
        begin
            case (alpha)
                2'd1: case (idx)
                    0: rrc_coeff = RRC_COEFF_00_A20;  1: rrc_coeff = RRC_COEFF_01_A20;
                    2: rrc_coeff = RRC_COEFF_02_A20;  3: rrc_coeff = RRC_COEFF_03_A20;
                    4: rrc_coeff = RRC_COEFF_04_A20;  5: rrc_coeff = RRC_COEFF_05_A20;
                    6: rrc_coeff = RRC_COEFF_06_A20;  7: rrc_coeff = RRC_COEFF_07_A20;
                    8: rrc_coeff = RRC_COEFF_08_A20;  9: rrc_coeff = RRC_COEFF_09_A20;
                    10: rrc_coeff = RRC_COEFF_10_A20; 11: rrc_coeff = RRC_COEFF_11_A20;
                    12: rrc_coeff = RRC_COEFF_12_A20; 13: rrc_coeff = RRC_COEFF_13_A20;
                    14: rrc_coeff = RRC_COEFF_14_A20; 15: rrc_coeff = RRC_COEFF_15_A20;
                    16: rrc_coeff = RRC_COEFF_16_A20; 17: rrc_coeff = RRC_COEFF_17_A20;
                    18: rrc_coeff = RRC_COEFF_18_A20; 19: rrc_coeff = RRC_COEFF_19_A20;
                    20: rrc_coeff = RRC_COEFF_20_A20; 21: rrc_coeff = RRC_COEFF_21_A20;
                    22: rrc_coeff = RRC_COEFF_22_A20; 23: rrc_coeff = RRC_COEFF_23_A20;
                    24: rrc_coeff = RRC_COEFF_24_A20; 25: rrc_coeff = RRC_COEFF_25_A20;
                    26: rrc_coeff = RRC_COEFF_26_A20; 27: rrc_coeff = RRC_COEFF_27_A20;
                    28: rrc_coeff = RRC_COEFF_28_A20; 29: rrc_coeff = RRC_COEFF_29_A20;
                    30: rrc_coeff = RRC_COEFF_30_A20; 31: rrc_coeff = RRC_COEFF_31_A20;
                    default: rrc_coeff = RRC_COEFF_32_A20;
                endcase
                2'd2: case (idx)
                    0: rrc_coeff = RRC_COEFF_00_A50;  1: rrc_coeff = RRC_COEFF_01_A50;
                    2: rrc_coeff = RRC_COEFF_02_A50;  3: rrc_coeff = RRC_COEFF_03_A50;
                    4: rrc_coeff = RRC_COEFF_04_A50;  5: rrc_coeff = RRC_COEFF_05_A50;
                    6: rrc_coeff = RRC_COEFF_06_A50;  7: rrc_coeff = RRC_COEFF_07_A50;
                    8: rrc_coeff = RRC_COEFF_08_A50;  9: rrc_coeff = RRC_COEFF_09_A50;
                    10: rrc_coeff = RRC_COEFF_10_A50; 11: rrc_coeff = RRC_COEFF_11_A50;
                    12: rrc_coeff = RRC_COEFF_12_A50; 13: rrc_coeff = RRC_COEFF_13_A50;
                    14: rrc_coeff = RRC_COEFF_14_A50; 15: rrc_coeff = RRC_COEFF_15_A50;
                    16: rrc_coeff = RRC_COEFF_16_A50; 17: rrc_coeff = RRC_COEFF_17_A50;
                    18: rrc_coeff = RRC_COEFF_18_A50; 19: rrc_coeff = RRC_COEFF_19_A50;
                    20: rrc_coeff = RRC_COEFF_20_A50; 21: rrc_coeff = RRC_COEFF_21_A50;
                    22: rrc_coeff = RRC_COEFF_22_A50; 23: rrc_coeff = RRC_COEFF_23_A50;
                    24: rrc_coeff = RRC_COEFF_24_A50; 25: rrc_coeff = RRC_COEFF_25_A50;
                    26: rrc_coeff = RRC_COEFF_26_A50; 27: rrc_coeff = RRC_COEFF_27_A50;
                    28: rrc_coeff = RRC_COEFF_28_A50; 29: rrc_coeff = RRC_COEFF_29_A50;
                    30: rrc_coeff = RRC_COEFF_30_A50; 31: rrc_coeff = RRC_COEFF_31_A50;
                    default: rrc_coeff = RRC_COEFF_32_A50;
                endcase
                default: case (idx)
                    0: rrc_coeff = RRC_COEFF_00_A35;  1: rrc_coeff = RRC_COEFF_01_A35;
                    2: rrc_coeff = RRC_COEFF_02_A35;  3: rrc_coeff = RRC_COEFF_03_A35;
                    4: rrc_coeff = RRC_COEFF_04_A35;  5: rrc_coeff = RRC_COEFF_05_A35;
                    6: rrc_coeff = RRC_COEFF_06_A35;  7: rrc_coeff = RRC_COEFF_07_A35;
                    8: rrc_coeff = RRC_COEFF_08_A35;  9: rrc_coeff = RRC_COEFF_09_A35;
                    10: rrc_coeff = RRC_COEFF_10_A35; 11: rrc_coeff = RRC_COEFF_11_A35;
                    12: rrc_coeff = RRC_COEFF_12_A35; 13: rrc_coeff = RRC_COEFF_13_A35;
                    14: rrc_coeff = RRC_COEFF_14_A35; 15: rrc_coeff = RRC_COEFF_15_A35;
                    16: rrc_coeff = RRC_COEFF_16_A35; 17: rrc_coeff = RRC_COEFF_17_A35;
                    18: rrc_coeff = RRC_COEFF_18_A35; 19: rrc_coeff = RRC_COEFF_19_A35;
                    20: rrc_coeff = RRC_COEFF_20_A35; 21: rrc_coeff = RRC_COEFF_21_A35;
                    22: rrc_coeff = RRC_COEFF_22_A35; 23: rrc_coeff = RRC_COEFF_23_A35;
                    24: rrc_coeff = RRC_COEFF_24_A35; 25: rrc_coeff = RRC_COEFF_25_A35;
                    26: rrc_coeff = RRC_COEFF_26_A35; 27: rrc_coeff = RRC_COEFF_27_A35;
                    28: rrc_coeff = RRC_COEFF_28_A35; 29: rrc_coeff = RRC_COEFF_29_A35;
                    30: rrc_coeff = RRC_COEFF_30_A35; 31: rrc_coeff = RRC_COEFF_31_A35;
                    default: rrc_coeff = RRC_COEFF_32_A35;
                endcase
            endcase
        end
    endfunction
```

在 `always @*` 之前的顺序块中加载系数（放在 `always @(posedge clk or negedge rst_n)` 段内，与 i_sample 同段追加，或独立 `always`）：

```verilog
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            for (k = 0; k < 33; k = k + 1) c[k] <= 16'sd0;
        end else begin
            for (k = 0; k < 33; k = k + 1) c[k] <= rrc_coeff(k, rolloff);
        end
```

将 `always @*` 乘加树内全部 `RRC_COEFF_XX` 常量引用替换为 `c[XX]`（一一对应：`RRC_COEFF_00`→`c[0]`、`RRC_COEFF_04`→`c[4]` … `RRC_COEFF_32`→`c[32]`，共 33 处）。注意 `rolloff=2'd0` 时系数 == 旧 0.35，`rrc_filter_tb` 全部既有断言语义不变。

- [ ] **Step 4: 运行 TB 确认通过**

Run: 同 Step 2 命令
Expected: `PASS rrc_filter`（0.35 回归 + 既有全部断言）

- [ ] **Step 5: Commit**

```bash
git add src/rtl/rrc_filter.v src/tb/rrc_filter_tb.v
git commit -m "feat: rrc_filter 三档滚降系数 MUX+寄存, 数据路径零新增延迟"
```

---

### Task 4: rrc_digital_mod / reg_bridge / awg_top 透传

**Files:**
- Modify: `src/rtl/rrc_digital_mod.v`
- Modify: `src/rtl/reg_bridge.v`
- Modify: `src/rtl/awg_top.v`
- Modify: `src/tb/awg_top_tb.v`

- [ ] **Step 1: rrc_digital_mod.v 加端口**

`module rrc_digital_mod` 端口表 `input wire [2:0] mode,` 后加：

```verilog
    input wire [1:0] rolloff,          // 00=0.35, 01=0.2, 10=0.5, 11=0.35
```

`u_filter` 实例加 `.rolloff(rolloff),`：

```verilog
    rrc_filter u_filter (
        .clk(clk), .rst_n(rst_n), .enable(rrc_enable),
        .sample_tick(sample_tick_r), .phase(phase_r),
        .load_symbol(load_symbol_r), .rolloff(rolloff),
        .new_i(new_i_r), .new_q(new_q_r),
        .i_sample(filter_i_sample), .q_sample(filter_q_sample),
        .sample_valid(filter_sample_valid)
    );
```

- [ ] **Step 2: reg_bridge.v 加输出**

模块端口 `output wire digmod_filter,` 后加：

```verilog
    output wire [1:0] digmod_rolloff,
```

`:109` `assign digmod_filter = r11[20];` 后加：

```verilog
    assign digmod_rolloff = r11[22:21];   // 00=0.35 默认, 01=0.2, 10=0.5, 11 按 0.35
```

- [ ] **Step 3: awg_top.v 声明并接线**

`:49` `wire digmod_filter;` 后加：

```verilog
    wire [1:0] digmod_rolloff;        // RRC 滚降因子(reg_bridge 字F bit[22:21])
```

`u_reg` 实例 `.digmod_filter(digmod_filter),` 后加：

```verilog
        .digmod_rolloff(digmod_rolloff),
```

`u_rrc` 实例 `.enable(rrc_active), .mode(digmod_mode),` 后加：

```verilog
        .rolloff(digmod_rolloff),
```

- [ ] **Step 4: awg_top_tb.v 加断言**

在 `:358` 附近（FILTER=0 QPSK 块前）加：

```verilog
        // ROLLOFF=0.2 (bit[22:21]=01) 必须从同步寄存器正确抽出。
        gpio11_in = (32'd1 << 21) | (32'd2 << 17) | (32'd50 << 1) | (32'd50 << 9);
        #1000;
        chk("digmod rolloff 0.2 select", uut.digmod_rolloff === 2'b01);
        chk("digmod rolloff 0.2 default filter RRC", uut.digmod_filter === 1'b1);
```

- [ ] **Step 5: 运行 awg_top_tb（回归）**

```bash
"$XSIM_PREFIX/bin/xvlog.bat" --sv src/rtl/*.v src/tb/awg_top_tb.v && "$XSIM_PREFIX/bin/xelab.bat" awg_top_tb -s sim && "$XSIM_PREFIX/bin/xsim.bat" sim -runall
```
Expected: `PASS awg_top`（含新断言）。`$XSIM_PREFIX` = `<LOCAL_PATH>`（用 `cli-anything-vivado config show` 核实）。

- [ ] **Step 6: Commit**

```bash
git add src/rtl/rrc_digital_mod.v src/rtl/reg_bridge.v src/rtl/awg_top.v src/tb/awg_top_tb.v
git commit -m "feat: reg_bridge/awg_top 透传 digmod_rolloff(GPIO11 bit22:21)"
```

---

### Task 5: rrc_digital_mod_tb 三档 GOLD 回归

**Files:**
- Modify: `src/tb/rrc_digital_mod_tb.v`

- [ ] **Step 1: TB 加 rolloff reg + 任务**

`:15` `reg [2:0] mode = MODE_QPSK;` 后加：

```verilog
    reg [1:0] rolloff = 2'd0;
```

DUT 实例 `sym_phase_step(sym_phase_step),` 后加：

```verilog
        .rolloff(rolloff),
```

`check_gold_qam64` 任务后加新任务：

```verilog
    task check_gold_rolloff;
        input [1:0] rf;
        input [255:0] memfile;
        integer guard;
        begin
            reset_dut();
            $readmemh(memfile, gold_mem);
            rolloff = rf;
            sym_phase_step = STEP_100K;
            mode = MODE_QPSK;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count < 64 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    gold_i = gold_mem[sample_count][31:16];
                    gold_q = gold_mem[sample_count][15:0];
                    if (i_sample !== gold_i || q_sample !== gold_q) begin
                        $display("FAIL rolloff GOLD rf=%0d index=%0d got=%h/%h expected=%h/%h",
                                 rf, sample_count, i_sample, q_sample, gold_i, gold_q);
                        failures = failures + 1;
                    end
                    if (symbol_valid !== (sample_count % 4 == 0))
                        fail("rolloff symbol_valid phase0 position");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count != 64)
                fail("rolloff GOLD sample timeout");
            rolloff = 2'd0;
            check_idle();
        end
    endtask
```

`initial` 块 `check_gold(64, STEP_1M);` 后加：

```verilog
        check_gold_rolloff(2'd1, "src/tb/rrc_expected_qpsk_a020.mem");
        check_gold_rolloff(2'd2, "src/tb/rrc_expected_qpsk_a050.mem");
```

- [ ] **Step 2: 运行回归（全 RRC 相关 TB）**

```bash
"$XSIM_PREFIX/bin/xvlog.bat" --sv src/rtl/rrc_digital_mod.v src/rtl/rrc_filter.v src/rtl/prbs7_source.v src/rtl/iq_constellation_mapper.v src/tb/rrc_digital_mod_tb.v && "$XSIM_PREFIX/bin/xelab.bat" rrc_digital_mod_tb -s sim && "$XSIM_PREFIX/bin/xsim.bat" sim -runall
```
Expected: `PASS rrc_digital_mod`（含 0.35 既有 GOLD + QAM16/64 + 新增 0.2/0.5 GOLD）

- [ ] **Step 3: Commit**

```bash
git add src/tb/rrc_digital_mod_tb.v
git commit -m "feat: rrc_digital_mod_tb 三档滚降 GOLD 回归"
```

---

### Task 6: PS 桥 ROLLOFF 协议

**Files:**
- Modify: `sdk/src/main.c`

- [ ] **Step 1: 头注释**

`:18` 后加：

```c
 *   SET DIGMOD ROLLOFF 0.2|0.35|0.5        -> OK (仅 STOPPED)
```

`:20` 改为：

```c
 *   GET DIGMOD  -> DIGMOD MODE <name> SOURCE PRBS7 FILTER <name> ROLLOFF <name> SYMRATE <Hz> STATE <state>
```

`:32-33` 改为：

```c
 *   wordF = phaselink | pulse_duty_ch1<<1 | pulse_duty_ch2<<9 | digmod_mode<<17
 *            | digmod_filter<<20 | digmod_rolloff<<21
 *   GPIO11[22:21] = DIGMOD ROLLOFF (00=0.35, 01=0.2, 10=0.5)
```

- [ ] **Step 2: 状态变量与名字表**

`:88` 后加：

```c
static const char *DIGMOD_ROLLOFF_NAME[4] = {"0.35", "0.2", "0.5", "0.35"};
```

`:91` 后加：

```c
static u32 digmod_rolloff = 0;   /* GPIO11[22:21]: 0=0.35, 1=0.2, 2=0.5 */
```

- [ ] **Step 3: pack_word_f**

`:143` 改为：

```c
    return s2->phaselink | (s1->pulse_duty << 1) | (s2->pulse_duty << 9)
         | (digmod_mode << 17) | (digmod_filter << 20) | (digmod_rolloff << 21);
```

- [ ] **Step 4: GET DIGMOD**

`:219-222` 改为：

```c
            char out[128];
            sprintf(out, "DIGMOD MODE %s SOURCE PRBS7 FILTER %s ROLLOFF %s SYMRATE %lu STATE %s\r\n",
                    DIGMOD_NAME[digmod_mode], DIGMOD_FILTER_NAME[digmod_filter],
                    DIGMOD_ROLLOFF_NAME[digmod_rolloff],
                    (unsigned long)digmod_symrate_hz,
                    digmod_mode ? "RUNNING" : "STOPPED");
```

- [ ] **Step 5: SET handler（FILTER 块后加）**

`:288`（FILTER 块 `return;` 后、`if (n != 4 || strcmp(tok[2], "MODE")...` 前）加：

```c
            if (n == 4 && strcmp(tok[2], "ROLLOFF") == 0) {
                u32 rf;
                if (digmod_mode != 0) {
                    uart_send("ERR DIGMOD RUNNING\r\n"); return;
                }
                if (strcmp(tok[3], "0.2") == 0)
                    rf = 1;
                else if (strcmp(tok[3], "0.35") == 0)
                    rf = 0;
                else if (strcmp(tok[3], "0.5") == 0)
                    rf = 2;
                else {
                    uart_send("ERR BAD VALUE\r\n"); return;
                }
                digmod_rolloff = rf;
                write_all();
                uart_send("OK\r\n");
                return;
            }
```

- [ ] **Step 6: 构建校验（不烧板，先过编译）**

```bash
cli-anything-vivado sdk build awg_bridge --workspace sdk/ws_qam_rrc
```
Expected: build 成功（无语法错误）。若工作区 main.c 与 `sdk/src/main.c` 不同步，先 `cp sdk/src/main.c sdk/ws_qam_rrc/awg_bridge/src/main.c` 再 build（workspace 是派生副本）。

- [ ] **Step 7: Commit**

```bash
git add sdk/src/main.c
git commit -m "feat: PS 桥 SET DIGMOD ROLLOFF + GET 增 ROLLOFF 字段"
```

---

### Task 7: host protocol.py + 测试

**Files:**
- Modify: `host/siggen/protocol.py`
- Test: `host/tests/test_protocol.py`

- [ ] **Step 1: 写失败测试**

`host/tests/test_protocol.py` 追加：

```python
def test_digmod_rolloff_command_and_new_get_status():
    assert digmod_rolloff_cmd("0.2") == "SET DIGMOD ROLLOFF 0.2"
    assert digmod_rolloff_cmd(0.35) == "SET DIGMOD ROLLOFF 0.35"
    assert digmod_rolloff_cmd("0.5") == "SET DIGMOD ROLLOFF 0.5"
    assert parse_digmod_status(
        "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.2 SYMRATE 100000 STATE RUNNING"
    ) == ("QPSK", "PRBS7", "RRC", "0.2", 100000, True)


@pytest.mark.parametrize("bad", ["0.1", "1.0", "0.35 ", ""])
def test_digmod_rolloff_command_rejects_unknown_values(bad):
    with pytest.raises(ValueError):
        digmod_rolloff_cmd(bad)


@pytest.mark.parametrize("line", [
    "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.9 SYMRATE 100000 STATE RUNNING",
    "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.2 SYMRATE 9 STATE RUNNING",
    "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.2 SYMRATE 100000 STATE RUNNING EXTRA",
])
def test_parse_digmod_status_rejects_bad_rolloff(line):
    with pytest.raises(ValueError):
        parse_digmod_status(line)
```

- [ ] **Step 2: 运行确认失败**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_protocol.py -q`
Expected: FAIL — `digmod_rolloff_cmd` 未定义、旧解析器对 13-token 行抛错

- [ ] **Step 3: 实现**

`protocol.py:42` 后加：

```python
DIGMOD_ROLLOFFS = ("0.2", "0.35", "0.5")
```

`digmod_filter_cmd` 后加：

```python
def digmod_rolloff_cmd(rolloff) -> str:
    """构造 SET DIGMOD ROLLOFF 命令;非法滚降抛 ValueError。"""
    name = str(rolloff)
    if name not in DIGMOD_ROLLOFFS:
        raise ValueError(f"unsupported DIGMOD rolloff: {rolloff}")
    return f"SET DIGMOD ROLLOFF {name}"
```

`parse_digmod_status` 整体替换为：

```python
def parse_digmod_status(line: str) -> tuple:
    """解析新旧 DIGMOD 状态行，返回 (mode, source, filter, rolloff, symrate_hz, running)。"""
    parts = line.split()
    if len(parts) == 9:
        mode, source, filter_name, rolloff = parts[2], parts[4], "RECT", "0.35"
        rate_key, rate_value, state_key, state_value = parts[5:9]
    elif len(parts) == 11:
        mode, source, filter_name, rolloff = parts[2], parts[4], parts[6], "0.35"
        if parts[5] != "FILTER":
            raise ValueError(f"bad digmod status line: {line!r}")
        rate_key, rate_value, state_key, state_value = parts[7:11]
    elif len(parts) == 13:
        mode, source, filter_name, rolloff = parts[2], parts[4], parts[6], parts[8]
        if parts[5] != "FILTER" or parts[7] != "ROLLOFF":
            raise ValueError(f"bad digmod status line: {line!r}")
        rate_key, rate_value, state_key, state_value = parts[9:13]
    else:
        raise ValueError(f"bad digmod status line: {line!r}")
    if (parts[0] != "DIGMOD" or parts[1] != "MODE" or mode not in DIGMODES
            or parts[3] != "SOURCE" or source != "PRBS7"
            or filter_name not in DIGMOD_FILTERS or rolloff not in DIGMOD_ROLLOFFS
            or rate_key != "SYMRATE" or not rate_value.isdigit()
            or state_key != "STATE" or state_value not in ("RUNNING", "STOPPED")):
        raise ValueError(f"bad digmod status line: {line!r}")
    symrate_hz = int(rate_value)
    if not DIGMOD_SYMRATE_MIN <= symrate_hz <= DIGMOD_SYMRATE_MAX:
        raise ValueError(f"bad digmod symrate: {symrate_hz}")
    running = state_value == "RUNNING"
    if running != (mode != "OFF"):
        raise ValueError(f"inconsistent digmod state: {line!r}")
    return mode, source, filter_name, rolloff, symrate_hz, running
```

- [ ] **Step 4: 更新既有 5-tuple 断言**

`host/tests/test_protocol.py` 中现有 5-tuple 断言补 `"0.35"`：
- `test_digmod_filter_command_and_new_get_status`（`:266-269`）→ 追加 `, "0.35"`
- `test_old_get_status_defaults_filter_to_rect`（`:271-276`）→ `(mode, "PRBS7", "RECT", "0.35", 10000, False)` 形态
- `test_parse_digmod_status_accepts_all`（`:284-288`）→ `(mode, "PRBS7", "RECT", "0.35", 100000, running)`

- [ ] **Step 5: 运行确认通过**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_protocol.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add host/siggen/protocol.py host/tests/test_protocol.py
git commit -m "feat: DIGMOD ROLLOFF 协议命令与 13-token 状态解析"
```

---

### Task 8: host controller.py + 测试

**Files:**
- Modify: `host/siggen/controller.py`
- Test: `host/tests/test_controller.py`

- [ ] **Step 1: 写失败测试**

`host/tests/test_controller.py` 追加：

```python
def test_set_digmod_rolloff_waits_for_get_confirmation(ctrl):
    ctrl.set_digmod_rolloff("0.2")
    assert ctrl._pending == (None, "digmod_rolloff", "0.2")
    assert ctrl.digmod.rolloff == "0.35"
    ctrl._on_line("OK")
    assert ctrl.digmod.rolloff == "0.35"
    ctrl._on_line("DIGMOD MODE OFF SOURCE PRBS7 FILTER RRC ROLLOFF 0.2 SYMRATE 100000 STATE STOPPED")
    assert ctrl.digmod.rolloff == "0.2"


def test_digmod_rolloff_invalid_no_send(ctrl):
    ctrl.set_digmod_rolloff("0.9")
    assert ctrl._pending is None


def test_digmod_rolloff_error_keeps_state(ctrl):
    ctrl.set_digmod_rolloff("0.5")
    ctrl._on_line("ERR BAD VALUE")
    assert ctrl.digmod.rolloff == "0.35"
```

- [ ] **Step 2: 运行确认失败**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_controller.py -q`
Expected: FAIL — `set_digmod_rolloff` 不存在

- [ ] **Step 3: 实现**

`controller.py` 引入 `digmod_rolloff_cmd`（`:11-12` 的 import 追加）;`DigitalModState` 加字段：

```python
    rolloff: str = "0.35"
```

`set_digmod_symrate` 后加：

```python
    def set_digmod_rolloff(self, rolloff) -> None:
        """下发 SET DIGMOD ROLLOFF;确认前不改确认态。"""
        if not self.worker:
            self.log.emit("未连接", "error")
            return
        try:
            cmd = digmod_rolloff_cmd(rolloff)
        except ValueError as exc:
            self.log.emit(str(exc), "error")
            return
        self.worker.send(cmd + "\r\n")
        self._pending = (None, "digmod_rolloff", rolloff)
```

`:220` unpacking 改为：

```python
                mode, source, filter_name, rolloff, symrate_hz, running = parse_digmod_status(line)
```

`:226-228` 后加：

```python
            self.digmod.rolloff = rolloff
```

`:237`、`:269`、`:277` 三处 pending 字段集合均改为：

```python
                if field in ("digmod", "digmod_symrate", "digmod_filter", "digmod_rolloff"):
```

- [ ] **Step 4: 更新既有 6-tuple unpack**

`controller.py:220` 已改（上面 Step 3）。检查 `host/tests/test_controller.py` 中既有 `parse_digmod_status` 消费断言是否需要补 `"0.35"`（`:275-282 test_digmod_status_line_updates_state` 用 `_on_line`，不直接 tuple 断言，无需改）。

- [ ] **Step 5: 运行确认通过**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_controller.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add host/siggen/controller.py host/tests/test_controller.py
git commit -m "feat: 控制器 set_digmod_rolloff 与 rolloff 状态"
```

---

### Task 9: preview / widgets / main_window

**Files:**
- Modify: `host/siggen/preview.py`
- Modify: `host/siggen/ui/widgets.py`
- Modify: `host/siggen/ui/main_window.py`
- Test: `host/tests/test_preview.py`、`host/tests/test_widgets.py`

- [ ] **Step 1: 写失败测试**

`host/tests/test_preview.py` 追加：

```python
def test_make_digital_mod_rrc_accepts_rolloff():
    t1, i1, q1 = make_digital_mod_data("QPSK", 32, 5000, symrate_hz=100000,
                                       filter_name="RRC", rolloff="0.2")
    t2, i2, q2 = make_digital_mod_data("QPSK", 32, 5000, symrate_hz=100000,
                                       filter_name="RRC", rolloff="0.5")
    assert len(t1) == len(t2) == 32 * 4 + 32
    assert not np.allclose(i1, i2)   # 不同滚降 -> 不同波形
```

`host/tests/test_widgets.py` 追加：

```python
def test_digital_panel_rolloff_defaults_and_emits(dig_panel):
    assert dig_panel.rolloff_combo.currentText() == "0.35"
    got = []
    dig_panel.rolloff_requested.connect(got.append)
    dig_panel.rolloff_combo.setCurrentText("0.2")
    assert got == ["0.2"]


def test_digital_panel_rolloff_locks_unless_rrc_and_stopped(dig_panel):
    dig_panel.set_confirmed("OFF", "PRBS7", 100000, False, filter_name="RECT", rolloff="0.5")
    assert dig_panel.rolloff_combo.isEnabled() is False
    dig_panel.set_confirmed("OFF", "PRBS7", 100000, False, filter_name="RRC", rolloff="0.5")
    assert dig_panel.rolloff_combo.isEnabled() is True
    dig_panel.set_confirmed("QPSK", "PRBS7", 100000, True, filter_name="RRC", rolloff="0.5")
    assert dig_panel.rolloff_combo.isEnabled() is False
    assert dig_panel.rolloff_combo.currentText() == "0.5"
```

- [ ] **Step 2: 运行确认失败**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_preview.py host/tests/test_widgets.py -q`
Expected: FAIL — `make_digital_mod_data` 无 `rolloff` 参数 / `rolloff_combo` 不存在

- [ ] **Step 3: preview.py**

`make_digital_mod_data` 签名 `filter_name: str = "RECT"` 后加 `, rolloff: str = "0.35"`；RRC 分支改为：

```python
    if filter_name == "RRC":
        from .rrc import RRC_INPUT_SHIFT, RRC_SPS, rrc_coefficients, rrc_filter_symbols
        i_sym, q_sym = generate_symbols(mode, symbols)
        i16, q16 = rrc_filter_symbols(i_sym, q_sym,
                                      coeffs=rrc_coefficients(alpha=float(rolloff)))
```

`set_digital` 签名 `filter_name: str = "RECT"` 后加 `, rolloff: str = "0.35"`；`:422` 调用加 `rolloff=rolloff`：

```python
                mode, 64, 5000, symrate_hz=symrate_hz, filter_name=filter_name, rolloff=rolloff)
```

- [ ] **Step 4: widgets.py**

`DigitalModPanel` 信号区（`:543` 后）加：

```python
    rolloff_requested = Signal(str)   # 0.2/0.35/0.5,下拉框变更即下发
```

`filter_row` 布局之后加：

```python
        rolloff_row = QHBoxLayout()
        rolloff_row.setSpacing(8)
        rolloff_row.addWidget(QLabel("滚降因子"))
        self.rolloff_combo = QComboBox()
        self.rolloff_combo.addItems(["0.2", "0.35", "0.5"])
        self.rolloff_combo.currentTextChanged.connect(self.rolloff_requested.emit)
        rolloff_row.addWidget(self.rolloff_combo)
        lay.addLayout(rolloff_row)
        self.rolloff_hint = QLabel("仅 RRC 成形有效；运行中需先关闭数字调制")
        self.rolloff_hint.setStyleSheet("color: orange;")
        lay.addWidget(self.rolloff_hint)
```

`set_confirmed` 签名加 `, *, rolloff: str = "0.35"`，并在 filter_combo 使能逻辑后加：

```python
        rolloff_name = str(rolloff)
        if rolloff_name not in ("0.2", "0.35", "0.5"):
            rolloff_name = "0.35"
        self.rolloff_combo.blockSignals(True)
        self.rolloff_combo.setCurrentText(rolloff_name)
        self.rolloff_combo.blockSignals(False)
        self.rolloff_combo.setEnabled(not running and filter_name == "RRC")
```

（`set_confirmed` 已是 keyword-only 的 `filter_name`，rolloff 加在 `*,` 后与 filter_name 并列即可。）

- [ ] **Step 5: main_window.py**

`:102` 后加：

```python
        self.dig.rolloff_requested.connect(self.ctrl.set_digmod_rolloff)
```

`:156-160` 的 `set_confirmed` 调用加 `rolloff=getattr(state, "rolloff", "0.35")`：

```python
        self.dig.set_confirmed(
            mode, state.source, state.symrate_hz,
            state.running if mode == state.mode else mode != "OFF",
            filter_name=getattr(state, "filter", "RECT"),
            rolloff=getattr(state, "rolloff", "0.35"),
        )
```

`:167-169` 的 `set_digital` 调用加 `rolloff=getattr(state, "rolloff", "0.35")`：

```python
            self.status_panel.preview.set_digital(
                mode, view, symrate_hz=state.symrate_hz,
                filter_name=getattr(state, "filter", "RECT"),
                rolloff=getattr(state, "rolloff", "0.35"))
```

- [ ] **Step 6: 运行确认通过**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_preview.py host/tests/test_widgets.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add host/siggen/preview.py host/siggen/ui/widgets.py host/siggen/ui/main_window.py host/tests/test_preview.py host/tests/test_widgets.py
git commit -m "feat: GUI 滚降因子下拉(仅 RRC+OFF 可编辑)与预览透传"
```

---

### Task 10: scope_verify.py rolloff 板级用例

**Files:**
- Modify: `scripts/scope_verify.py`
- Test: `host/tests/test_scope_verify_rrc.py`

- [ ] **Step 1: 写失败测试**

`host/tests/test_scope_verify_rrc.py` 追加：

```python
def test_rrc_expected_waveform_supports_rolloff():
    w020 = rrc_expected_waveform("QPSK", 100000, 32, rolloff=0.2)
    w050 = rrc_expected_waveform("QPSK", 100000, 32, rolloff=0.5)
    w035 = rrc_expected_waveform("QPSK", 100000, 32, rolloff=0.35)
    assert w020.shape == w035.shape == w050.shape == (2, 32 * 4 + 32)
    assert not np.allclose(w020, w035)
    assert not np.allclose(w050, w035)


def test_rrc_rolloff_cases_exist():
    cases = rrc_rolloff_cases()
    assert cases == (
        ("QPSK RRC rolloff 0.2", "QPSK", 100_000, 200e-6, 0.2),
        ("QPSK RRC rolloff 0.5", "QPSK", 100_000, 200e-6, 0.5),
    )
```

- [ ] **Step 2: 运行确认失败**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_scope_verify_rrc.py -q`
Expected: FAIL — `rrc_expected_waveform` 无 `rolloff` 参数 / `rrc_rolloff_cases` 不存在

- [ ] **Step 3: 实现**

`scope_verify.py` 引入 `rrc_coefficients`（`:1-12` 的 from siggen import 追加）。`rrc_expected_waveform` 改为：

```python
def rrc_expected_waveform(mode, symrate_hz, symbol_count, seed=0x7f, rolloff=0.35):
    if symrate_hz <= 0 or symbol_count < 1:
        raise ValueError("RRC scope reference requires positive parameters")
    i_sym, q_sym = generate_symbols(mode, symbol_count, seed=seed)
    coeffs = rrc_coefficients(alpha=float(rolloff))
    return np.vstack(rrc_filter_symbols(i_sym, q_sym, coeffs))
```

`RRC_SCOPE_CASES` 后加：

```python
RRC_ROLLOFF_CASES = (
    ("QPSK RRC rolloff 0.2", "QPSK", 100_000, 200e-6, 0.2),
    ("QPSK RRC rolloff 0.5", "QPSK", 100_000, 200e-6, 0.5),
)


def rrc_rolloff_cases():
    """Return the deterministic rolloff-variation RRC scope cases."""
    return RRC_ROLLOFF_CASES
```

- [ ] **Step 4: 抽取每档校验为 helper 并接入双循环**

将 `main()` 中 RRC 循环体（`setup_board(...)` 到 `report("RRC %dSym/s OFF -> RECT 1kHz" ...)` 整段，含两个 try/except）抽为模块级：

```python
def _verify_rrc_case(mode, symrate, tim_s, chan_s, rolloff=0.35):
    try:
        setup_board(["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RRC",
                     "SET DIGMOD ROLLOFF %.2f" % rolloff,
                     "SET DIGMOD SYMRATE %d" % symrate,
                     "SET DIGMOD MODE %s" % mode])
        expected = rrc_expected_waveform(mode, symrate, 127, rolloff=rolloff)
        expected_status = (
            "DIGMOD MODE %s SOURCE PRBS7 FILTER RRC ROLLOFF %.2f "
            "SYMRATE %d STATE RUNNING" % (mode, rolloff, symrate)
        )
        status = serial_line("GET DIGMOD")
        status_ok = status == expected_status
        scope_acq(tim_s, chan_s)
        xinc0, y0 = scope_wave("CHAN1")
        xinc1, y1 = scope_wave("CHAN2")
        xinc_ok = np.isclose(xinc0, xinc1, rtol=1e-6, atol=1e-15)
        alignment = (_align_rrc_capture(
            scope_capture_to_digital(y0), scope_capture_to_digital(y1),
            symrate, xinc0, expected, mode=mode,
        ) if xinc_ok else None)
        if alignment is None:
            center_ok = constellation_ok = eye_ok = ber_ok = False
            ber = symbol_offset = None
            timing_offset = None
            recovered = (np.zeros(0), np.zeros(0))
        else:
            ber, symbol_offset, timing_offset, recovered = alignment
            center_ok = len(recovered[0]) >= 127
            valid = slice(8, 119)
            ri = np.asarray(recovered[0][valid], dtype=np.float64)
            rq = np.asarray(recovered[1][valid], dtype=np.float64)
            if mode != "QPSK":
                peak = max(float(np.max(np.abs(ri))), float(np.max(np.abs(rq))))
                if peak > 0.0:
                    ri = ri / peak * 126.0
                    rq = rq / peak * 126.0
            eye_margin = rrc_eye_margin(ri, rq)
            eye_ok = np.isfinite(eye_margin) and eye_margin > 0.0
            labels = center_sample_labels(ri, rq, mode)
            points = set(zip(labels[0].tolist(), labels[1].tolist()))
            valid_points = set(map(tuple, constellation_points(mode).tolist()))
            constellation_ok = points <= valid_points and len(points) >= 4
            ber_ok = ber == 0.0
        rolloff_ratio = rrc_spectrum_rolloff(y0, y1, symrate, xinc0)
        spectrum_ok = rolloff_ratio is not None and rolloff_ratio < 0.2
        rrc_ok = (status_ok and xinc_ok and center_ok and constellation_ok
                  and eye_ok and ber_ok and spectrum_ok)
        report("RRC %s %dSym/s matched BER" % (mode, symrate), rrc_ok,
               "rolloff=%.2f status=%s center=%s constellation=%s eye=%s BER=%s "
               "prbs_offset=%s timing=%.3fus rolloff_ratio=%s"
               % (rolloff, "OK" if status_ok else "BAD", center_ok,
                  constellation_ok, eye_ok,
                  "0" if ber is not None and ber == 0.0 else str(ber),
                  str(symbol_offset),
                  timing_offset * 1e6 if timing_offset is not None else -1.0,
                  "%.3f" % rolloff_ratio if rolloff_ratio is not None else "NA"))
    except Exception as e:
        report("RRC %s %dSym/s matched BER" % (mode, symrate), False,
               "capture or match failed: %r" % e)

    try:
        setup_board(["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT",
                     "SET CH1 WAVE SINE", "SET CH1 FREQ 1000",
                     "SET CH1 AMPL 50", "SET CH1 BASE 0"])
        scope_acq(2e-3, 2.0)
        xinc, off_wave = scope_wave("CHAN1")
        off_freq = frequency_from_zero_cross(xinc, off_wave)
        off_min, off_max = float(np.min(off_wave)), float(np.max(off_wave))
        off_span = off_max - off_min
        off_ok = (off_freq is not None
                  and abs(off_freq - 1000.0) <= 100.0
                  and off_min < 0x80 < off_max and off_span >= 20.0)
        report("RRC %dSym/s OFF -> RECT 1kHz" % symrate, off_ok,
               "freq=%.1fHz min=%.0f max=%.0f span=%.0f"
               % (off_freq if off_freq is not None else -1.0,
                  off_min, off_max, off_span))
    except Exception as e:
        report("RRC %dSym/s OFF -> RECT 1kHz" % symrate, False,
               "recovery failed: %r" % e)
```

`main()` 中原 RRC 循环体替换为：

```python
    for mode, symrate, tim_s, chan_s in rrc_cases:
        _verify_rrc_case(mode, symrate, tim_s, chan_s, 0.35)
    for _, mode, symrate, tim_s, chan_s in RRC_ROLLOFF_CASES:
        _verify_rrc_case(mode, symrate, tim_s, chan_s, chan_s and rolloff_placeholder)
```

> 注意：rolloff 循环应传 `rolloff`（第 5 元素）。修正为：

```python
    for _, mode, symrate, tim_s, chan_s, rf in RRC_ROLLOFF_CASES:
        _verify_rrc_case(mode, symrate, tim_s, chan_s, rf)
```

（上面占位行仅作提醒，实际提交使用本修正行。**删除占位行，只保留 `for _, mode, symrate, tim_s, chan_s, rf in RRC_ROLLOFF_CASES:` 版本。**）

- [ ] **Step 5: 运行离线测试**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/test_scope_verify_rrc.py -q`
Expected: PASS（新用例 + 既有全部）

- [ ] **Step 6: Commit**

```bash
git add scripts/scope_verify.py host/tests/test_scope_verify_rrc.py
git commit -m "feat: scope_verify 新增 0.2/0.5 滚降 BER 档(42/42)"
```

---

### Task 11: test_bridge.py ROLLOFF 协议序列

**Files:**
- Modify: `scripts/test_bridge.py`

- [ ] **Step 1: 改 digmod_line 期望（含 ROLLOFF）**

`test_bridge.py:132-137` 改为：

```python
def digmod_line(mode, rate=10000, filter_name="RECT", rolloff="0.35", running=None):
    if running is None:
        running = mode != "OFF"
    state = "RUNNING" if running else "STOPPED"
    return "DIGMOD MODE %s SOURCE PRBS7 FILTER %s ROLLOFF %s SYMRATE %d STATE %s" % (
        mode, filter_name, rolloff, rate, state)
```

- [ ] **Step 2: 追加 ROLLOFF 序列（`:163` FILTER BOGUS 前后）**

```python
cmd("SET DIGMOD ROLLOFF 0.2", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RRC", "0.2"))
cmd("SET DIGMOD ROLLOFF 0.5", "OK")
cmd("SET DIGMOD ROLLOFF 0.35", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RRC"))
cmd("SET DIGMOD ROLLOFF 0.1", "ERR BAD VALUE")
cmd("SET DIGMOD ROLLOFF bogus", "ERR BAD VALUE")
cmd("SET DIGMOD ROLLOFF", "ERR BAD VALUE")
cmd("SET DIGMOD ROLLOFF 0.2 EXTRA", "ERR BAD VALUE")
cmd("SET DIGMOD MODE QPSK", "OK")
cmd("SET DIGMOD ROLLOFF 0.5", "ERR DIGMOD RUNNING")
cmd("SET DIGMOD MODE OFF", "OK")
```

> 放置位置：需确保插入点 MODE 已是 OFF（前面 `:162` 已 `SET DIGMOD FILTER RECT` 恢复 OFF+RECT）。插入后恢复 `SET DIGMOD FILTER RRC` + `SET DIGMOD MODE BPSK` 的既有流程前先 `SET DIGMOD ROLLOFF 0.35` 复位。

- [ ] **Step 3: 全量 pytest（本任务无板,仅静态检查）**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/ -q`（test_bridge 无离线测试，仅确保未破坏其它）

- [ ] **Step 4: Commit**

```bash
git add scripts/test_bridge.py
git commit -m "feat: test_bridge 增加 DIGMOD ROLLOFF 协议序列"
```

---

### Task 12: host 全量回归

- [ ] **Step 1: 全量 pytest**

Run: `host/.venv/Scripts/python.exe -m pytest host/tests/ -q`
Expected: 全部 PASS（当前 host/tests 实际为 320 项）

- [ ] **Step 2: 代码自查（临时）**

Run: `host/.venv/Scripts/python.exe -m pyflakes host/siggen/ scripts/scope_verify.py scripts/test_bridge.py`（若装有 pyflakes）
Expected: 无未定义名（`digmod_rolloff_cmd`、`rolloff` 等均已接线）

---

### Task 13: 重建 + 时序 + 板级验收

- [ ] **Step 1: 同步 SDK 工作区并构建**

```bash
cp sdk/src/main.c sdk/ws_qam_rrc/awg_bridge/src/main.c
cli-anything-vivado sdk build awg_bridge --workspace sdk/ws_qam_rrc
```
Expected: 构建成功

- [ ] **Step 2: 全量重建 bitstream（RTL 已变,必须走 build_bitstream.py）**

Run: `python scripts/build_bitstream.py --force`
Expected: BITSTREAM-OK；manifest 更新（约 14-17 分钟）

- [ ] **Step 3: 时序 signoff**

Run: `python scripts/check_bitstream.py` → 读 `awg/awg.runs/impl_1/host_bd_wrapper_timing_summary_routed.rpt` 的 `sys_clk`
Expected: setup/hold/PW 0 失败、WNS ≥ 0（系数寄存化保证不新增数据路径延迟；若个别失败,回查锥 B 路径,继续最小切分,不放宽 20ns）

- [ ] **Step 4: 烧板 + 桥**

```bash
bash scripts/program_board.sh
cli-anything-vivado sdk run "awg/awg.xpr" --elf sdk/ws_qam_rrc/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/ws_qam_rrc/awg_bridge_hw --bit awg/awg.runs/impl_1/host_bd_wrapper.bit
```

- [ ] **Step 5: 协议回归**

Run: `python scripts/test_bridge.py COM11`
Expected: 全部 PASS（含 ROLLOFF 序列）

- [ ] **Step 6: 示波器闭环**

Run: `python scripts/scope_verify.py COM11`
Expected: `RESULT: 42/42 PASS`（38 既有 + QPSK 100k rolloff 0.2/0.5 各 1 BER + 各 1 OFF→RECT 恢复）

- [ ] **Step 7: 文档与提交**

`README.md` / `CLAUDE.md` 同步：ROLLOFF 命令、42/42、RRC 三档支持。`git add -A` 提交（含 build_manifest.json）。

---

## Self-Review

**Spec 覆盖：**
- 三档系数表（0.2/0.35/0.5）→ Task 2 ✓；0.35 组逐位一致 → Task 2 Step 4 校验 ✓
- rrc_filter MUX+寄存 → Task 3 ✓；reg_bridge r11[22:21] → Task 4 ✓；awg_top 透传 → Task 4 ✓
- `SET DIGMOD ROLLOFF` 仅 OFF 可设 / `ERR DIGMOD RUNNING` / `ERR BAD VALUE` → Task 6 ✓
- GET 增 ROLLOFF、旧格式兼容 → Task 6/7 ✓
- GUI 滚降因子下拉（仅 RRC+OFF）→ Task 9 ✓；预览透传 → Task 9 ✓
- 板级：保留 5 个 0.35 用例 + 新增 0.2/0.5 各 1 BER → Task 10 ✓（42/42）
- 时序不放松、系数寄存化 → Task 3 + Task 13 ✓
- 向后兼容（旧 ELF/bitstream 默认 0.35）→ r11[22:21] 复位 0 = 0.35 ✓

**类型一致性：** rolloff 在 RTL 为 `[1:0]`（00=0.35/01=0.2/10=0.5/11→0.35）、C 为 hw 值 0/1/2（名表 `DIGMOD_ROLLOFF_NAME[4]`）、协议字符串 `0.2|0.35|0.5`、host `DigitalModState.rolloff: str`、GUI combo 同字符串、scope_verify `rolloff` float。C 名表 `[4]` 覆盖 hw 0..3（index 3→"0.35"），与 RTL 保留语义一致。

**无占位符：** 仅 Task 10 Step 4 中一处循环行已用"修正行"显式覆盖（实现时只用修正行，不引入占位）。
