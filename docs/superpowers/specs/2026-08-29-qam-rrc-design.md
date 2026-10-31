# QAM RRC 成形支持设计

日期：2026-08-29
状态：已批准（brainstorming 完成）

## 背景与动机

AC820 信号发生器的数字调制支持 BPSK/QPSK/QAM4/QAM16/QAM64，数据源为内部固定 PRBS7。RECT 滤波（`iq_digital_mod`）已支持全部五种模式；RRC 成形（`rrc_digital_mod` + 33-tap FIR）当前仅允许 QPSK。本设计将 RRC 成形扩展到全部五种模式，使高阶 QAM 也能获得根升余弦成形与匹配滤波接收。

## 目标

- RRC 成形支持 BPSK/QPSK/QAM4/QAM16/QAM64 全部五种模式，与 RECT 一致。
- 板级（DSOX1102A）BER 验收覆盖 QPSK（现有三档）+ QAM16/QAM64（各一档）；BPSK/QAM4 仅 RTL/协议/GUI 支持。
- 保持 50 MHz / 20 ns 时序约束不放宽。

## 非目标

- 板上接收解调 / EVM / BER 硬件测量（沿用示波器闭环）。
- 外部数据源、用户数据上传、成帧/FEC。
- 修改 33-tap FIR 系数或 RRC 配置（4 SPS、rolloff 0.35、span 8）。

## 当前限制点（探索结论）

| 层 | 位置 | 当前限制 |
|------|------|---------|
| RTL 调度 | `src/rtl/rrc_digital_mod.v:14` | `rrc_enable = enable && (mode == 3'd1)` |
| RTL 调度 | `src/rtl/rrc_digital_mod.v:51` | `advance_bits(3'd2)` 硬编码 |
| RTL 调度 | `src/rtl/rrc_digital_mod.v:56` | `u_mapper .mode(3'd1)` 硬编码 |
| RTL 顶层 | `src/rtl/awg_top.v:211` | `rrc_active = ... && (digmod_mode == 3'd1)` |
| 桥协议 | `sdk/src/main.c:296` | `ERR FILTER REQUIRES QPSK` |
| GUI 提示 | `host/siggen/ui/widgets.py:568,613` | “RRC 仅支持 QPSK” |
| GUI 预览 | `host/siggen/preview.py:207,408` | RRC 时 `mode != QPSK` 抛错 |
| 板级验收 | `scripts/scope_verify.py` | `RRC_SCOPE_CASES` 仅 QPSK；`center_sample_qpsk_labels` 仅 ±127 |
| 协议回归 | `scripts/test_bridge.py:152` | 断言 RRC 仅允许 QPSK |

## 关键编码（已确认）

- `awg_top.v:203`：`digmod_mode = digmod_sel - 1`，已是 **mapper 编码**：BPSK=0, QPSK=1, QAM4=2, QAM16=3, QAM64=4。非 RRC 的 `digmod_sel`（OFF=0..QAM64=5）编码不同，勿混用。
- `iq_constellation_mapper.v` 已支持全部星座并输出 `consume_bits`：BPSK=1, QPSK/QAM4=2, QAM16=4, QAM64=6。
- `prbs7_source.v` 的 `advance_state` 已支持 `advance_bits` 1/2/4/6；`symbol_bits[5:0]` 覆盖 QAM64 的 6 bit。
- `iq_digital_mod.v` 已用 `consume_bits` 驱动 `advance_bits` 并直传 `mode`，本设计让 RRC 路径对齐该既有模式。

## 设计

### 1. RTL（`src/rtl/awg_top.v` + `src/rtl/rrc_digital_mod.v`）

`awg_top.v`：
```verilog
wire rrc_active = digmod_enable && digmod_filter;   // 放开 QPSK-only
```
（`digmod_enable` 已保证 `digmod_mode ∈ 0..4`。）

`rrc_digital_mod.v` 三处：
```verilog
wire rrc_enable = enable && (mode <= 3'd4);        // 放开全部 5 模式
...
.advance_bits(consume_bits),                        // mapper 输出的 1/2/2/4/6
...
u_mapper .mode(mode), ...                           // 传外部 digmod_mode
```

时序安全：`consume_bits` 由当前 `symbol_bits`（PRBS 寄存器输出）与 mode 组合决定；`advance_prbs` 由 `sample_tick`/`poly_phase` 驱动。二者无组合环。mode 仅 STOPPED 时可经桥修改，active 期间恒定。

### 2. 桥协议（`sdk/src/main.c`）

- 删除 `:296` 的 `ERR FILTER REQUIRES QPSK` 分支，RRC 下允许 BPSK..QAM64。
- 更新 `:17` 头注释。

### 3. 上位机（`host/siggen/`）

- `protocol.py:18`：模块注释同步。
- `ui/widgets.py:568,613`：`filter_hint` 改为“RRC 支持全部模式；运行中需先关闭数字调制”。
- `preview.py:207,408`：删除 RRC 仅 QPSK 断言。`rrc_filter_symbols` 已接受任意 I/Q 符号序列。

### 4. 板级验收（`scripts/scope_verify.py`）

- `main()` 的 `rrc_cases` 扩展为含 mode 的 5 档（QPSK 三档保持，新增 QAM16/QAM64 @ 100 kSym/s），并按 mode 泛化 `rrc_expected_waveform`、GET 状态断言、星座判据；测试辅助常量 `RRC_SCOPE_CASES` 同步。
- 星座聚类：`center_sample_qpsk_labels` 泛化为按模式量化到最近星座点。
  - QPSK：±127（2 值/轴）
  - QAM16：`{126,42,-42,-126}`，决策边界 `±84, 0`
  - QAM64：`{126,90,54,18,-18,-54,-90,-126}`，决策边界 `±108, ±72, ±36, 0`
- `rrc_expected_waveform(mode, ...)`、`rrc_symbol_ber(...)`、`_align_qpsk_labels(...)` 增加 `mode` 参数，用 `generate_symbols(mode, 127)` 生成 PRBS7 参考（`host/siggen/digital_mod.py` 已支持全星座）。
- QPSK 三档行为不变（±127 量化等价于现逻辑）。

### 5. 测试

- RTL：`rrc_digital_mod_tb` 增加 QAM16/QAM64 档（含 Python GOLD 比对）。
- host：`test_scope_verify_rrc.py` 增加 QAM16/QAM64 离线聚类 BER=0 回归 + 决策边界单测。
- 协议：`test_bridge.py` RRC 模式断言改为允许 QAM。
- 板级：`scope_verify.py COM11` 预期 38/38（34 + QAM16/QAM64 各 1 档 BER + 各 1 档 OFF→RECT 恢复）。
- 时序：RRC FIR 不变，仅调度器参数化；重建后复核 `sys_clk` WNS ≥ 0。

## 验收标准

- RRC 下 BPSK/QPSK/QAM4/QAM16/QAM64 均可设置并回读 `FILTER RRC`。
- RTL `rrc_digital_mod_tb` 全部模式 PASS；host 回归全过。
- 板级 `scope_verify.py`：QPSK 三档 + QAM16/QAM64 各一档 BER=0（38/38）。
- 重建 bitstream 后 `sys_clk` setup/hold 0 失败、WNS ≥ 0；freshness 校验通过。
- 不修改 20 ns 时序约束。

## 风险与缓解

- QAM16/QAM64 星座点更密，示波器噪声/量化可能压近决策边界：选择 100 kSym/s 中档并在阈值上加余量；BER 判据仍要求 0 误码。
- `consume_bits` 组合时序：无组合环（见上），由 `rrc_digital_mod_tb` 断言覆盖。
- RRC 顶层 `rrc_active` 放开后 BPSK 输出 Q=0：符合预期（BPSK 仅 I 路），板级不采集 BPSK。
