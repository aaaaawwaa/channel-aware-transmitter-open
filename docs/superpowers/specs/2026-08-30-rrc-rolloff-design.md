# RRC 滚降因子可配置设计

日期：2026-08-30
状态：已批准（brainstorming 完成）

## 背景与动机

AC820 信号发生器的数字调制支持 BPSK/QPSK/QAM4/QAM16/QAM64，RRC 成形（`rrc_digital_mod` + 33-tap FIR）当前将滚降因子固定为 0.35（编译期常量）。本设计将滚降因子变为运行时可配置，支持 {0.2, 0.35, 0.5} 三档，默认 0.35。

## 目标

- 滚降因子支持 0.2 / 0.35 / 0.5 三档，默认 0.35；全数字模式（BPSK..QAM64）下均生效。
- 板级（DSOX1102A）BER 验收：QPSK 100 kSym/s × 三档 rolloff 各 1 个 BER 用例 + 保留现有 5 个 0.35 用例。
- 保持 50 MHz / 20 ns 时序约束不放宽（当前 WNS +0.598ns）。

## 非目标

- 任意连续滚降因子（仅三档离散集合）。
- 运行中热切换系数（仅 MODE OFF 时可改，同 FILTER/SYMRATE 约束）。
- 修改 4 SPS / span 8 / 33 taps 的其余 RRC 配置。
- 板上接收解调 / EVM / BER 硬件测量（沿用示波器闭环）。

## 当前限制点（探索结论）

| 层 | 位置 | 当前限制 |
|------|------|---------|
| RTL 系数 | `src/rtl/rrc_coeffs.vh` | 仅 0.35 一组 33 个 localparam |
| RTL 滤波器 | `src/rtl/rrc_filter.v:81-152` | 乘加树直接引用常量 `RRC_COEFF_XX` |
| RTL 桥接 | `src/rtl/reg_bridge.v:109` | `assign digmod_filter = r11[20]`；无 rolloff 位 |
| RTL 顶层 | `src/rtl/awg_top.v` | 未透传 rolloff |
| 桥协议 | `sdk/src/main.c` | FILTER/SYMRATE 仅 OFF 可改；无 ROLLOFF 命令 |
| 上位机 | `host/siggen/rrc.py:53` | `alpha != 0.35` 时 `raise ValueError`（冻结） |
| 板级验收 | `scripts/scope_verify.py` | `rrc_expected_waveform` 硬编码 0.35 |

## 关键现状（已确认）

- `rrc_coeffs.vh` 定义 33 个 `localparam signed [15:0] RRC_COEFF_00.._32`，对称结构（C00=C32=0x0021、C01=C31…）。0.35 组数值必须与现有完全一致。
- `rrc_filter.v` 4 个 polyphase 分支共引用 33 个系数常量；系数经 DSP48 常量折叠进乘加树。系数改动后若直接变输入，DSP 乘法从常量乘法变通用乘法 → 可能改变时序。**缓解：MUX 输出寄存一拍再馈入乘加树**，系数仅在 `!enable`（MODE OFF）时更新，与数据路径完全解耦，时序零影响。
- `reg_bridge.v` 用两级同步寄存器 `s11/r11` 抽 GPIO11：`r11[20]=digmod_filter`、`r11[19:17]=digmod_sel`。空闲位 `r11[31:21]`，建议 `r11[22:21]` 作为 rolloff。
- GPIO11 位图（当前）：bit0=phaselink，bit[8:1]=CH1 duty，bit[16:9]=CH2 duty，bit[19:17]=digmod_mode，bit20=digmod_filter，**bit[22:21]=digmod_rolloff（新增）**，bit[31:23] 仍空闲。
- 上位机 `rrc.py` 常量：`RRC_ALPHA=0.35`、`RRC_SPS=4`、`RRC_SPAN=8`、`RRC_TAPS=33`、`RRC_COEFF_SCALE=32767`、`RRC_INPUT_SHIFT=8`、`RRC_OUTPUT_SHIFT=8`、`RRC_GROUP_DELAY=16`、`RRC_MATCH_DELAY=32`。系数生成函数 `rrc_coefficients(alpha, sps, span)`。

## 设计

### 1. RTL 系数表（`src/rtl/rrc_coeffs.vh`）

由 Python 生成脚本（复用 `host/siggen/rrc.py:rrc_coefficients`）预计算三组 33-tap 系数，写为 3×33 个 localparam：

```verilog
localparam signed [15:0] RRC_COEFF_00_A35 = 16'sh0021;  // 与现有 0.35 完全一致
localparam signed [15:0] RRC_COEFF_00_A20 = ...;
localparam signed [15:0] RRC_COEFF_00_A50 = ...;
// ... 0..32
```

0.35 组数值逐位与现有 `rrc_coeffs.vh` 相同（对称结构保留），作为回归基线。

### 2. 滤波器选择（`src/rtl/rrc_filter.v`）

```verilog
module rrc_filter (
    ...
    input wire [1:0] rolloff,          // 新增：00=0.35, 01=0.2, 10=0.5
    ...
);
```

- 每个系数索引 `k∈[0,32]`：3:1 组合 MUX 从三组 localparam 选值 → **寄存一拍**得 `c_k`。
- 乘加树中 `RRC_COEFF_XX` 引用全部替换为 `c_XX`。
- 相位 / hist / round_acc / sample_valid / load_symbol 逻辑不动。
- 寄存器化理由：系数仅在 `!enable` 时变化，寄存后不在数据路径上，DSP 乘法输入为稳定寄存器值，避免常量乘法退化与额外组合延迟。

### 3. 桥接与顶层（`src/rtl/reg_bridge.v` + `src/rtl/awg_top.v`）

```verilog
// reg_bridge.v
assign digmod_rolloff = r11[22:21];   // 00=0.35 默认，01=0.2，10=0.5，11 保留(按 0.35)
```
- 复位默认 `r11=0` → rolloff=00 → 0.35。**旧 ELF/bitstream 天然向后兼容。**
- `awg_top.v`：`u_rrc` 增加 `.rolloff(digmod_rolloff)` 端口；`rrc_active` 判定不变。

### 4. 桥协议（`sdk/src/main.c`）

- 新增 `SET DIGMOD ROLLOFF 0.2|0.35|0.5`：
  - 仅 `MODE OFF` 可设（同 FILTER/SYMRATE 分支，运行中返回 `ERR DIGMOD RUNNING`）。
  - 非法值 `ERR BAD VALUE`。
  - 写 GPIO11 bit[22:21]：0.2→`2'b01`、0.35→`2'b00`、0.5→`2'b10`。
- `GET DIGMOD` 扩展为 `... FILTER <f> ROLLOFF <r> SYMRATE <Hz> STATE <s>`（`<r>` 为 `0.2|0.35|0.5` 文本）。
- 更新头注释与打包宏（`digmod_rolloff<<21`）。

### 5. 上位机（`host/siggen/`）

- `rrc.py`：去掉 `rrc_coefficients` 的 alpha 冻结检查，支持 {0.2, 0.35, 0.5}；新增 `RRC_ROLLOFFS = (0.2, 0.35, 0.5)` 与默认值常量。
- `protocol.py`：新增 `DIGMOD_ROLLOFFS`、`digmod_rolloff_cmd`；`parse_digmod_status` 解析新增 `ROLLOFF` 字段（旧格式兼容）。
- `controller.py`：`set_digmod_rolloff`。
- `preview.py`：`make_digital_mod_data` / `WavePreview.set_digital` 透传 rolloff 给 `rrc_filter_symbols`。
- `ui/widgets.py`：数字调制页新增“滚降因子”下拉（0.2/0.35/0.5），仅 FILTER=RRC 且 MODE OFF 可编辑；运行中锁定；连接 `main_window.py` 下发与回读。

### 6. 板级验收（`scripts/scope_verify.py`）

- `rrc_expected_waveform` / `rrc_symbol_ber` / `rrc_spectrum_rolloff` 增加 `rolloff` 参数，参考波形按档生成（复用 `host/siggen/rrc.py`）。
- 新增 rolloff 环：QPSK 100 kSym/s × {0.2, 0.5} 各 1 个 matched BER + OFF→RECT 恢复各 1 档（0.35@100k 已含于现有 "RRC QPSK 100000Sym/s" 用例，故不重复）→ 覆盖全部三档。
- 保留现有 5 个 0.35 用例（QPSK 三档 + QAM16/QAM64 各一档）。
- 新增 rolloff 光谱滚降判据（`rrc_spectrum_rolloff` 阈值 <0.2 保持）。

## 测试

- RTL：`rrc_digital_mod_tb` / `awg_top_tb` 增加三档 rolloff 档位，与 Python GOLD（QPSK 127 symbols × 3 档）比对；0.35 档与现有期望完全一致（回归基线）。
- host：`test_rrc.py` 参数化三档；`test_scope_verify_rrc.py` 离线三档聚类 BER=0 + 光谱判据；`test_protocol.py` / `test_controller.py` / `test_widgets.py` 覆盖 ROLLOFF 命令、解析与锁定逻辑。
- 协议回归：`test_bridge.py` 增加 ROLLOFF 序列（OFF 设置 / 运行中拒绝 / 非法值 / GET 回读）。
- 板级：`scope_verify.py COM11` 预期 38 + 4（新增 0.2/0.5 两档 rolloff 各 BER+OFF→RECT 恢复）= **42/42**。
- 时序：重建后复核 `sys_clk` WNS ≥ 0、0 失败端点；系数寄存化保证不新增数据路径延迟。

## 验收标准

- `SET DIGMOD ROLLOFF 0.2|0.35|0.5` 在 OFF 下可设并回读；运行中返回 `ERR DIGMOD RUNNING`；非法值 `ERR BAD VALUE`。
- `GET DIGMOD` 返回含 `ROLLOFF` 字段；旧格式兼容解析。
- 三档 rolloff 的 RTL 输出与上位机 Python GOLD 完全一致；0.35 档与改前逐位一致。
- host 回归全过；板级 scope_verify 42/42（含三档 QPSK 100k BER=0：0.35 现有 + 0.2/0.5 新增）。
- 重建 bitstream 后 `sys_clk` setup/hold 0 失败、WNS ≥ 0；freshness 校验通过。
- 不修改 20 ns 时序约束。

## 风险与缓解

- **DSP 常量乘法退化**（系数从 localparam 变 MUX 输入）：MUX 输出寄存一拍再进乘加树，系数更新仅在 `!enable`，数据路径零新增延迟；重建后时序复核兜底。
- **0.35 组回归**：三档中 0.35 数值与现有完全一致，RTL TB 与 host 离线 GOLD 双保险。
- **旧固件兼容**：rolloff 位在 `r11[22:21]`，复位默认 00=0.35；旧 ELF 不写该位，行为不变。
- **光谱带宽**：rolloff 0.2 更窄过渡带，示波器 FFT 分辨带宽有限；BER/星座判据为主，光谱判据阈值保持 <0.2 并仅作参考。
