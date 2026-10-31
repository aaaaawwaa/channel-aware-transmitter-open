# RF 源同步 DDR 接口时序覆盖：缺口诊断、边界与最小修复方案

> **状态：OFFLINE_ONLY / ZERO_BOARD / DOCUMENT_ONLY。**
> 日期：2026-10-31。
> 对象：AC820（Zynq-7020 `xc7z020clg484-2`）+ AD-FMCOMMS3-EBZ（AD9361，2R2T LVDS DDR，FMC LPC）的合并产品 Overlay `host_bd_wrapper`。
> **结论先行：约束缺陷已确认；板级接口时序未测；不得宣称 RF DDR 接口时序通过。** 内部时序通过 ≠ 外部接口通过；同时，负的试跑 slack 也 ≠ 接口失效。
> **本轮边界**：未启动 Vivado、未综合/实现、未操作板卡、未修改任何 RTL/XDC、未安装/未采集。本文件是 task-10 的唯一仓库产物；候选约束只以"审查材料/摘录"形式存在于本文，没有放进产品约束。
> 引用本文时请连带日期与状态一起引用。QAM64 研究只作为**诊断对象背景**（该线复核了交付 bit 对应的同一 routed DCP 与同一份约束），不作为交付结论。

---

## 0. 证据层级、占位与引用规则

### 0.1 占位符

| 占位 | 含义 |
|---|---|
| 仓库相对路径（如 [`../rf/boardtest.md`](../rf/boardtest.md)） | 可在本仓库阅读的文档。原始收据/报告不在仓库，不能据此复算。 |
| `<LOCAL_EVIDENCE>/…` | 开发机私有证据与原始构建根目录（`evidence/` 收据 + `awg/`、`rf_overlay/`、`pynq_overlay/` 三个 Vivado 工程的 `*.runs/{synth_1,impl_1}` 报告与 routed DCP）。**不在本仓库**，路径去掉盘符与用户名后表示（与 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §1.2 同一约定）。 |
| `TBD` | 未测/待确认。**禁止**用"看起来合理"的估算值代替。 |

本文已按公开材料要求脱敏：不含个人姓名、Windows 用户名、盘符绝对路径、设备串号或私网 IP；主机名/开发机路径一律以 `<LOCAL_EVIDENCE>` 或相对路径表示。

### 0.2 证据层级（本文只使用前三级）

1. **厂商一手资料**：AD9361 UG-570 Rev.A（Table 51 / Figure 79 / page 106–111）、AD9361 datasheet Rev.G（DIGITAL DATA TIMING (LVDS)）、Xilinx UG471（7 Series SelectIO/IDELAYE2）。
2. **本地只读工具证据**：Vivado 2025.2 对真实 routed DCP 的 `check_timing` / `report_timing` / `report_clocks` / `report_exceptions` / `report_methodology`；DCP 与交付 bit 的 SHA-256/载荷绑定收据。
3. **离线推导**：由上面两级数据按声明公式推得的量（标"推导"）。
4. **未测（TBD）**：板级走线 skew、AD9361 内部寄存器实际值、运行期 IDELAY tap 的眼图效果。
5. **不可用**：任何没有来源的"典型值"、为了让报告变绿而填的 delay、把内部 slack 当作接口结论。

### 0.3 一句话读法

`check_timing` 说"没有 input/output delay" → 那是**约束缺失**；Vivado 给出 `slack=inf`/`Path Group=(none)` → 那是**没被计时**，不是"通过"；给接口补上 delay 之后出现负 slack → 那是**模型输入（board skew/tap/时钟模型）没有闭合**，也不是"硬件坏了"。

---

## 1. 结论与边界

### 1.1 三条结论

1. **约束缺陷已确认。** 交付合并设计（`host_bd` Overlay）的 AD9361 2R2T LVDS DDR 接口没有任何 `set_input_delay`/`set_output_delay`：
   - 产品约束文件 [`rf_overlay/constraints/rf.xdc`](../../rf_overlay/constraints/rf.xdc) 只有引脚/电平、`create_clock data_clk`、两条静态 false path 与时钟组（时钟组在 [`tools/impl1_clock_groups.tcl`](../../tools/impl1_clock_groups.tcl)），**没有** I/O delay，也**没有** `tx_clk_out_p` 的生成时钟；
   - 2026-10-26 只读检查真实 routed DCP 的 `check_timing` 给出"9 个输入端口无 input delay（HIGH）+ 1 个有 false path 的输入"、"39 个输出端口无 output delay（HIGH）+ 9 个有 false path 的输出"；
   - RX lane1 输入路径与 TX lane1 输出路径在该 DCP 中均为 `slack=inf`、`Path Group=(none)`——即工具**根本没有按外部接口来计时**。
2. **板级接口时序未测。** 板级 DATA_CLK↔data/frame 走线 skew（RX）、TX_CLK↔TX_D/TX_FRAME 走线 skew（TX）都没有项目内数据；AD9361 侧 0x010/0x011/0x012（LVDS/2R2T/FDD/data swap）与 0x006/0x007（data delay）只有源码初值、没有硅上读回；运行期 IDELAY tap（0..31，典型 ~78 ps/tap）不在静态 DCP 模型中。因此**当前没有任何物理证据**能说明接口满足或不满足 AD9361 的时序要求。
3. **不得宣称通过，也不得宣称失效。** "内部 WNS/WHS 为正"只覆盖已被约束的内部路径；"约束齐全"也只是必要条件。要升级为"接口时序满足"，必须有板级测量或受控的相位/眼图实验，并且结论只在其测量条件（配置、温度、探头、仪器带宽）内成立。

### 1.2 可以说 / 不可以说

| 不可以说（反例） | 可以说（有据） |
|---|---|
| "RF 接口时序已满足/已收敛" | "RF 源同步 DDR 接口没有 I/O delay 约束（已确认缺陷）；内部时序通过不覆盖外部接口" |
| "内部 WNS +0.213 / WHS +0.018，所以接口没问题" | "内部路由时序对**已约束路径**为正；接口路径 `slack=inf`、`Path Group=(none)`，未被计时" |
| "试跑 WNS −5.240 / WHS −2.879，所以板卡接口坏了" | "0 ns skew 试跑的负裕量是**模型未闭合**证据，不是硬件故障结论" |
| "check_timing 的 48 个输出就是 48 个 RF 输出" | "全局 9/48 是工具口径；RF 子集是 7 个 RX 输入（P 侧）/ 8 个 TX 输出（P 侧），其中 `tx_clk_out_p` 是时钟输出而非数据端口" |
| "补上约束就通过" | "补上约束是必要第一步；是否满足取决于未测的 board skew、器件内部相位与 runtime tap" |
| "BPSK/QPSK 冒烟 0 误码，所以链路时序已闭合" | "冒烟只能说明该小样本在该配置下可工作；共享前端的接口裕量未测" |

### 1.3 本轮实际做了什么 / 没做什么

| 项 | 状态 |
|---|---|
| 只读阅读本地证据（REPORT/JSON/XDC/TCL/`.rpt` 摘录）与产品约束文件 | 已做（只读） |
| 启动 Vivado、open_checkpoint、综合、实现、bit/HWH 生成 | **未做**（本轮禁止） |
| 板卡、RF、SPI/寄存器读、示波器、安装、采集 | **未做**（本轮禁止） |
| 修改 `rf_overlay/constraints/rf.xdc`、任何 RTL、产品文件 | **未做** |
| 新增候选 XDC 到仓库 | **未做**（仅本文摘录；见 §7.1 阶段 0 的入库建议） |
| 本文 | task-10 的唯一仓库产物 |

---

## 2. 范围与端口清单

### 2.1 分析对象与身份链

分析对象是**合并产品 Overlay**（不是独立 RF Overlay，也不是仿真 TB）：

| 项 | 值 | 出处 |
|---|---|---|
| 设计 | `host_bd_wrapper`，`Design State : Routed` | `<LOCAL_EVIDENCE>/rf-teaching/qam64-lane1-timing-2026-10-26/vivado/check_timing.rpt` 表头 |
| routed DCP | `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_routed.dcp` | 同上；`CHAIN-TIMING.json` |
| DCP 大小/时间 | 22,937,299 B；文件时间戳 **2026-09-24 10:10:33** | `<LOCAL_EVIDENCE>/rf-teaching/qam64-halfword-dcp-2026-10-26/dcp-audit.json` §identity |
| DCP SHA-256 | `63f679bcce815ba6c883f2e47b286dfc35a549ae9d040eba028548bd2be7c305` | 同上（本次只读复核时重算一致） |
| 工具 | Vivado v.2025.2 (win64) Build 6299465；报告生成于 **2026-10-26 11:51–11:55** | 各 `.rpt` 表头 |
| 器件/速度等级 | `7z020-clg484`；Speed File `-2 PRODUCTION 1.12 2019-11-22` | 同上 |
| 与交付 bit 的关系 | 交付 bit `d5bfe5d9…` 与同一次实现归档 bit `76e242fe…` **可编程载荷逐字节相同**（`payload_equal: true`；仅 175 B 文件头有 7 字节差异）；交付 HWH 与该次构建 HWH 仅 `TIMESTAMP` 不同 | `<LOCAL_EVIDENCE>/codex-frame-idelay-check-20261025/verification.json`；`<LOCAL_EVIDENCE>/rf-teaching/qam64-halfword-dcp-2026-10-26/REPORT.md` §1 |
| 交付发布 | `4814bbc42b9d5e0165a3ed3ebe7eaeef`（2026-10-26 最终交付；bit/HWH 与父发布 `036a4ec9…` 相同） | `<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/REPORT.md`、`<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/docs/RELEASE-SUMMARY.md` |

> 口径限制：这是**产物级身份**（同一配置载荷），不是"源码级同源"——构建收据里没有 RTL/XDC 的哈希。见 §6 缺口 G-08。

### 2.2 "全局 9 输入 / 48 输出无 I/O delay"的准确口径

Vivado 的 `check_timing` 会分成两段列出端口：一段是**完全没有任何 delay** 的端口（HIGH），另一段是**没有 delay 但已有 false path** 的端口（MEDIUM）。两段合计才是章节标题里的数字。2026-10-26 报告原文：

| 章节 | 原文 | 细分 |
|---|---|---|
| `checking no_input_delay (10)` | "There are **9** input ports with no input delay specified. (HIGH)" | `btn[0]`、`btn[1]`、`rx_data_in_p[0..5]`、`rx_frame_in_p` |
| 同上 | "There is **1** input port with no input delay but user has a false path constraint. (MEDIUM)" | `spi_miso` |
| `checking no_output_delay (48)` | "There are **39** ports with no output delay specified. (HIGH)" | LCD 19 条 + `led[0..7]` 8 条 + `spi_csn/sclk/mosi` 3 条 + `tp_rst` 1 条 + **RF TX 8 条** = 39 |
| 同上 | "There are **9** ports with no output delay but user has a false path constraint. (MEDIUM)" | `rf_gpio[0..8]`（静态模式脚，`rf.xdc` 里已有 false path） |

由此外推：

- **全局"9 输入"是 HIGH 段**，不是 10；**全局"48 输出"是 39+9 两段合计**。旧 routed timing summary 报告里写的 9/39 与 HIGH 段一致；
- **48 − 39 = 9 恰好是 `rf_gpio`**（有 false path 的输出），不是 RF 串行端口。**不得**把 48 与 39 的差值算到 RF 头上；
- `report_methodology` 的 `TIMING-18 Missing input or output delay` 共 **37** 条（input 9 + output 28），是**另一种计数口径**：input 9 与 check_timing 的 9 相同；output 28 = 未列 LCD 的 19 条 + 额外列入 TX 的 N 侧 8 条（39 − 19 + 8 = 28）。两种口径不矛盾，引用时必须说明用的是哪一种。

> 结论：**"9 / 48"是 `check_timing` 章节口径；"7 / 8"是 RF 差分对 P 侧口径。** 混用会造成错报。

### 2.3 RF 子集：7 个 RX 输入 / 8 个 TX 输出（P 侧）

LVDS 差分对在 Vivado 中由 **P 侧端口代表整对**：IBUFDS/OBUFDS 的另一半（N 侧）是同一差分对的互补端，**不能单独再加一份 delay**（否则重复计数）。`io.rpt` 物理上能看到 `rx_data_in_n[*]`、`tx_data_out_n[*]`、`tx_frame_out_n`、`tx_clk_out_n`，但计时只需约束 P 侧。

| 方向 | 端口（准确名） | 差分对 | 角色 | 需要什么 |
|---|---|---|---|---|
| RX 输入 | `rx_data_in_p[0]` … `rx_data_in_p[5]` | 各配 `rx_data_in_n[i]` | 6 条串行数据 lane（2R2T 半字） | `set_input_delay`（rise + fall 两边缘） |
| RX 输入 | `rx_frame_in_p` | `rx_frame_in_n` | 帧同步 | `set_input_delay`（同上；tDDDV） |
| RX 输入（时钟） | `rx_clk_in_p` / `rx_clk_in_n` | 自身为一对 | **DATA_CLK 输入=恢复时钟源** | `create_clock`（已有）；它**不是**数据端口，不应加 input delay |
| TX 输出 | `tx_data_out_p[0]` … `tx_data_out_p[5]` | 各配 `tx_data_out_n[i]` | 6 条串行数据 lane | `set_output_delay`（相对**转发时钟**，两边缘） |
| TX 输出 | `tx_frame_out_p` | `tx_frame_out_n` | 帧同步 | `set_output_delay`（同上） |
| TX 输出（时钟） | `tx_clk_out_p` / `tx_clk_out_n` | 自身为一对 | **转发 FB_CLK（生成时钟输出）** | `create_generated_clock`；**不是**数据端口，不应加数据 output delay |

因此：

- **RX 端"7 个输入"= 6 data + 1 frame（P 侧）**；`rx_clk_in_p/n` 不在 7 里（它是时钟源，已被 `create_clock` 约束）；
- **TX 端"8 个输出"= 6 data + 1 frame + 1 clock（P 侧）**；其中真正需要 `set_output_delay` 的是 **7 个数据/帧端口**，`tx_clk_out_p` 需要的是**生成时钟**；
- N 侧端口（7 个 RX N、8 个 TX N）= 16 个物理端口，**都不需要独立 delay 约束**；它们出现在 `io.rpt` 与 methodology 的计数里，是口径问题，不是额外缺陷。

### 2.4 现有 `rf.xdc` 已有什么、缺什么

[`rf_overlay/constraints/rf.xdc`](../../rf_overlay/constraints/rf.xdc) 共 127 行，实际内容分类：

| 类别 | 内容 | 与接口时序的关系 |
|---|---|---|
| 引脚 + 电平（行 18–51） | `rx_clk_in_p/n`、`rx_frame_in_p/n`、`rx_data_in_p/n[0..5]` 为 `LVDS_25 + DIFF_TERM TRUE`；`tx_clk_out_p/n`、`tx_frame_out_p/n`、`tx_data_out_p/n[0..5]` 为 `LVDS_25`；SPI 4 线 `LVCMOS25`；`rf_gpio[8:0]` `LVCMOS25` | 只说明"能连/什么电平"，**不含外部延时** |
| 时钟（行 116–118） | `RF_SAMPLE_RATE_HZ=60000000` → `RF_DATA_CLK_HZ=4×60 MHz` → `create_clock -name data_clk` 在 `rx_clk_in_p` 上 | 给出 RX 的源同步参考时钟；但**没有** `tx_clk_out_p` 的生成时钟 |
| 静态 false path（行 84、86） | `set_false_path -to [get_ports {rf_gpio[*]}]`；`set_false_path -from [get_ports spi_miso]` | 与 RF 串行数据/帧/时钟无关（`exceptions.rpt` 已确认：RF 串行线上没有任何 false path） |
| 跨域时钟组 | 不在 `rf.xdc`；在 [`tools/impl1_clock_groups.tcl`](../../tools/impl1_clock_groups.tcl) 的 `opt_design` 前 hook：`clk_fpga_0` / `sys_clk` / `data_clk` 三组 asynchronous | 只声明域间异步，**不覆盖 RF 源同步接口**；不能当作接口时序约束来引用 |
| IDELAY | RTL 里 7 个 `IDELAYE2` 为 `VAR_LOAD`、`IDELAY_VALUE=0`；`rf.xdc` 里**没有** IDELAY 属性/时序 | 静态模型只代表 tap 0，不代表运行期 tap |
| **缺失** | `set_input_delay`、`set_output_delay`、`create_generated_clock`（TX 侧） | 全部没有 |

`report_exceptions` 的实测结果同样确认：RF 串行数据/帧/时钟上**没有**任何 false path 或 max-delay 例外；只有 `rf_gpio[*]`（静态脚）与 `spi_miso`；另有两个 `data_clk ↔ clk_fpga_0/其它` 的 clock group。

### 2.5 受影响端口清单（按端口角色）

| 序号 | 端口 | 方向 | 目前 | 缺口 |
|---:|---|---|---|---|
| 1–6 | `rx_data_in_p[0..5]` | in | 引脚/电平/DIFF_TERM；无 delay | 缺 `set_input_delay`（相对 `data_clk`，rise+fall） |
| 7 | `rx_frame_in_p` | in | 同上 | 缺 `set_input_delay`（tDDDV） |
| 8–13 | `tx_data_out_p[0..5]` | out | 引脚/电平；无 delay | 缺 `set_output_delay`（相对 `tx_clk_fwd`，rise+fall） |
| 14 | `tx_frame_out_p` | out | 同上 | 缺 `set_output_delay`（tSTX/tHTX） |
| 15 | `tx_clk_out_p` | out | 引脚/电平；**无时钟对象**（`clocks.rpt` 中不存在） | 缺 `create_generated_clock`（**不是** output delay） |
| 16 | `rx_clk_in_p` | in | `create_clock` 4.167 ns | 已有时钟；不需要 input delay |

---

## 3. 时钟与数据通路

### 3.1 时钟表（来自真实 DCP 的 `clocks.rpt` / `timing_summary`）

| 时钟 | 周期 | 波形 | 来源/说明 |
|---|---|---|---|
| `data_clk` | 4.167 ns（报告 Frequency 列显示 **239.981 MHz**，即 1/4.167 ns 的取整倒数） | `{0.000 2.083}` | **恢复时钟**：`rx_clk_in_p/n` → IBUFDS → BUFG；`create_clock` 由 `rf.xdc` 建立在输入端口；4 × 60 MSPS |
| `clk_fpga_0` | 20.000 ns（50 MHz） | `{0.000 10.000}` | PS FCLK0（AXI 域） |
| `clk_fpga_1` | 5.000 ns（200 MHz） | `{0.000 2.500}` | PS FCLK1；BD 中 `ps7_0/FCLK_CLK1 → ad9361_phy_0/ref_clk200m`（[`scripts/create_bd.tcl`](../../scripts/create_bd.tcl)），即 **IDELAYCTRL REFCLK** |
| `sys_clk` | 20.000 ns（50 MHz） | `{0.000 10.000}` | 板载 50 MHz 时钟 |
| `daclk0/daclk1` | 20.000 ns | `{10.000 20.000}`，`P,G,I` | AWG 侧生成时钟 |
| `clkfb_raw` / `pclk_raw` | 20.000 / 30.000 ns | — | LCD MMCM 生成时钟 |

关键点：

- `data_clk` **不是**本地时钟，是 AD9361 送来的 DATA_CLK 经 IBUFDS+BUFG 恢复出来的；RX 方向 AD9361 是时钟主，FPGA 是接收方。这是"源同步输入"的定义。
- **`tx_clk_out_p/n` 上没有时钟对象**：`clocks.rpt` 只列上面这些时钟，没有 TX 转发时钟。也就是说交付设计里 TX 转发时钟在静态时序上完全不存在（既没有生成时钟，也没有 output delay）。
- IDELAYCTRL 需要 200 MHz ± 10 MHz REFCLK。`clk_fpga_1` 在 DCP 中即 5.000 ns（200 MHz），满足该参考；这条是"工具/配置"层面的事实，不等于 tap 的板级有效性已测。

### 3.2 RX 数据通路与源同步关系

实际实现（来自 routed DCP，只读复核）：

```
rx_data_in_p[i]/n  ──IBUFDS(DIFF_TERM=1)──► rx_data_i
       └─► IDELAYE2 (VAR_LOAD, IDELAY_VALUE=0, DELAY_SRC=IDATAIN,
                      C=ref_clk200m, LD=idelay_en[i], CNTVALUEIN=idelay_tap[4:0])
             └─► IDDR (SAME_EDGE_PIPELINED, C=phy_rx_inst/CLK=data_clk,
                       Q1=posedge 样本, Q2=negedge 样本)
                   └─► Q2 额外寄存一拍 → 24-bit shift → rx_data_d1/q1/d2/q2 → 采集 BRAM
rx_frame_in_p/n  ──IBUFDS(DIFF_TERM=1)──► IDELAYE2（同上，LD=idelay_en[6]）
             └─► IDDR (SAME_EDGE_PIPELINED, C=data_clk) ──► frame shift
```

- 7 个 `IDELAYE2` + 7 个 `IDDR`：全部 `VAR_LOAD/value 0`、全部 `SAME_EDGE_PIPELINED`、`C=data_clk`。IBUFDS 的实际 `DIFF_TERM=1`（端口属性；RTL 字面 `DIFF_TERM="FALSE"` 被 XDC 覆盖，这是文本差异不是实现缺陷）。
- 源同步关系：AD9361 用同一根 DATA_CLK 驱动 `Rx_D[5:0]` 与 `Rx_FRAME`，输出延迟规范为 `tDDRx`/`tDDDV`（0.25..1.25 ns）。FPGA 在 DATA_CLK 的两个边缘采样（IDDR 的 Q1/Q2），所以**输入约束必须同时覆盖 rise 与 fall 两个边缘**。
- 2R2T 帧结构（协议参考，与本文时序参数同源）：一个 frame = **4 个 DATA_CLK 周期 = 8 个 DDR 半字**；顺序为 `I1[11:6], Q1[11:6], I1[5:0], Q1[5:0]`（frame 高）再 `I2[11:6], Q2[11:6], I2[5:0], Q2[5:0]`（frame 低）；一个 12-bit 分量 = **相邻两个同类型边缘**的 6-bit 半字；lane1 落在 bit7（MSB 半字）与 bit1（LSB 半字）。

### 3.3 TX 数据通路、转发时钟与源同步关系

```
dac_data_d1/q1/d2/q2 ──► phy_tx 两拍半字寄存器
    ├─ ODDR_tx_data_inst[0..5]  (SAME_EDGE, C=phy_tx_inst/CLK=data_clk,
    │     D1=tx_data_posedge_reg[i], D2=tx_data_negedge_reg[i]) ──OBUFDS──► tx_data_out_p/n[i]
    ├─ ODDR_frame_inst          (SAME_EDGE, D1=D2=tx_frame_reg, C=data_clk) ──OBUFDS──► tx_frame_out_p/n
    └─ ODDR_data_clk_inst       (SAME_EDGE, D1=0, D2=1, C=data_clk)          ──OBUFDS──► tx_clk_out_p/n  (= FB_CLK)
```

- 8 个 ODDR 全部 `SAME_EDGE`、全部由 `data_clk` 驱动；D1 在上升沿输出（posedge 半字），D2 在下降沿输出（negedge 半字）。
- **TX 源同步关系是反过来的**：FPGA 是时钟主，AD9361 用 FPGA 转发出去的 `FB_CLK`（即 `tx_clk_out_p/n`）采样 `TX_D/TX_FRAME`。规范要求是 `tSTx ≥ 1.0 ns` setup、`tHTx ≥ 0.0 ns` hold，**相对 FB_CLK 的下降沿**（UG-570 Table 51）。
- 转发时钟的边沿关系（由网表推导，未测量）：`ODDR SAME_EDGE + D1=0/D2=1` ⇒ `tx_clk_out` 在 `data_clk` **上升沿**处输出 0、**下降沿**处输出 1；因此它的**上升沿在 data_clk 下降沿**、**下降沿在 data_clk 上升沿**，即相对 `data_clk` 为 **180° 反相**（频率仍为 240 MHz，不是倍频）。
- **已记录事实**：ADI 官方 2R2T 参考实现（`axi_ad9361_lvds_if.v` / `ad_data_out.v`，commit `c9a501d8…`）用的是 `D1=1/D2=0`，本设计与之相差 180°。这是"设计现状记录"，不是缺陷声明，也不是根因结论；但它直接决定了 TX 生成时钟必须用 `-invert` 建模（见 §5.3），否则模型是错的。
- 8 个 TX ODDR 共用同一个 `data_clk`，所以当 `tx_clk_fwd` 以**转发 ODDR 的 C 引脚**为 `-source` 时，对所有 TX 数据/帧 ODDR 都是同一个有效参考。

### 3.4 IDELAYE2（VAR_LOAD）与运行期 tap

| 项 | 事实 | 出处 |
|---|---|---|
| 数量/类型 | 7 × `IDELAYE2`，`IDELAY_TYPE=VAR_LOAD`，`DELAY_SRC=IDATAIN`，`IDELAY_VALUE=0`，`REFCLK_FREQUENCY=200.0` | routed DCP（`inspect-console.log`）；RTL `phy_rx.v` |
| 装载控制 | `C=ref_clk200m`；frame `LD=idelay_en[6]`，lane *i* `LD=idelay_en[i]`；**共用** `CNTVALUEIN=idelay_tap[4:0]` | 同上 |
| 静态 DCP 的固定延时 | `IDELAYE2` 在 routed RX lane1 路径上是 **0.680 ns（slow/max）** / **0.244 ns（fast/min）**——**tap 0 不是"零延时"** | `<LOCAL_EVIDENCE>/rf-teaching/qam64-lane1-timing-2026-10-26/vivado/rx_input_paths.rpt` |
| 运行期 tap | 0..31；此前离线工作观察/装载过 **25/27/29/30/31**；交付版最终读回 `IDELAY_EN=127/TAP=29` | `CHAIN-TIMING.json`；`<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/docs/RELEASE-SUMMARY.md` |
| 增量延时 | 典型 ~78 ps/tap → 0..2.418 ns（tap29≈2.262、tap30≈2.340、tap31≈2.418） | UG471 典型值（见 §4.3） |

> **静态 DCP 的 `IDELAY_VALUE=0` 不代表运行接口**（运行期是 tap 29 附近）。因此任何基于静态 DCP 的时序报告都不能说"已经代表 tap29 的接口"；把 tap 当作 `set_input_delay` 里的固定值也是错的口径，正确做法是**场景分组**（§5.4）。

### 3.5 内部固定延时锚点（只读 DCP 实测，供计算参考）

这些值是**FPGA 内部**的、已经包含在到达时间里的量，**不应**再塞进 `set_input_delay`/`set_output_delay` 的数值里：

| 路径 | 值（ns） | 详情 |
|---|---|---|
| RX lane1 数据路径（static） | **1.620**（slow） | IBUFDS 0.940 + IDELAYE2 0.680；hold 角 0.679 = IBUFDS 0.435 + IDELAYE2 0.244 |
| RX 时钟到达 IDDR C（slow） | DCD **3.973** = 6.056 − 2.083 | IBUFDS 0.798 + net 1.701 + BUFG 0.078 + net 1.396 |
| RX 时钟到达 IDDR C（fast） | DCD **2.029** = 4.113 − 2.083 | 对应 fast 值 |
| TX lane1 发射路径（slow，fall） | 时钟到 ODDR C **6.474**；ODDR C→Q 0.418 + net 0.001 + OBUFDS 1.457 ⇒ **8.350** | 数据路径 `Delay 1.876 ns` |
| TX lane1 发射路径（fast，rise） | 时钟到 ODDR C **1.654**；1.654 + 0.177 + 0.001 + 0.693 ⇒ **2.525** | 数据路径 `Delay 0.871 ns` |

> **边界观察（不作为根因）**：试加 input delay 的只读报告里 DCD 为 4.390 ns，配合固定数据路径 1.620 ns 得到约 +2.77 ns 的固定偏差，按 78 ps/tap 约需 35.5 tap，而 tap 上限是 31、tap31 仍剩约 0.35 ns。这个算术依赖某一种边缘/角配对，且**没有包含 AD9361 tDDRx、板级 skew、运行时 tap 场景**；它只能说明"静态 tap0 模型明显不居中"，不能当作根因或 tap 需求结论。

---

## 4. 参数来源（逐项给依据，不填猜测值）

### 4.1 AD9361 LVDS DDR 时序参数（UG-570 Rev.A Table 51 / Figure 79）

一手来源：AD9361 Reference Manual **UG-570 Rev. A**，LVDS 数据通路章节（p.106–111），其中 **Figure 79**（Receive Data Path, LVDS）在 **p.110**、**Table 51**（Data Path Timing Constraint Values—LVDS Mode）在 **p.111**。本地保存了官方 PDF 与文本抽取（`sources/UG-570-RevA-extracted.txt`、`sources/UG-570-page110-Figure79-400dpi.png`），SHA-256 见该证据目录的 `SHA256SUMS.txt`。

Table 51 原文值（逐字摘录关键行）：

| 参数 | Min | Max | 含义（原文） |
|---|---|---|---|
| `tCP` | 4.069 ns | — | DATA_CLK cycle time（对应 LVDS 最大 245.76 MHz） |
| `tMP` | 45% of tCP | 55% of tCP | DATA_CLK/FB_CLK 高/低最小脉宽（含占空比失真/抖动） |
| `tSTx` | 1 ns | — | `Tx_D[5:0]`, `Tx_FRAME` setup to **FB_CLK falling edge** at AD9361 inputs |
| `tHTx` | 0 ns | — | `Tx_D[5:0]`, `Tx_FRAME` hold from **FB_CLK falling edge** at AD9361 inputs |
| `tDDRx` | 0.25 ns | 1.25 ns | Delay from **DATA_CLK** to `Rx_D[5:0]` outputs |
| `tDDDV` | 0.25 ns | 1.25 ns | Delay from **DATA_CLK** to `Rx_FRAME` |

AD9361 datasheet Rev.G 的 `DIGITAL DATA TIMING (LVDS)`（p.9 of 39）给出同一组值：`tCP ≥ 4.069 ns`、`tMP 45–55% tCP`、`tSTX 1 ns`、`tHTX 0 ns`、`tDDRX/tDDDV 0.25..1.25 ns`。两份厂商资料一致。本地抽取见 `sources/AD9361-datasheet-extracted.txt`。

> 注意：datasheet 的 **CMOS** 段落里另有 `tDDRX 0..1.5 ns（1.8 V）/ 0..1.2 ns（2.5 V）` 的条目；这是 CMOS 并行口，**不适用于本设计的 LVDS 接口**。引用时不要混用 CMOS 表。LVDS 是 DDR-only、FDD Full 等限制也来自 UG-570（p.110）。

### 4.2 本设计 `tCP`/`tMP` 校核（唯一可以离线确认的"满足"项）

- 设计：`RF_SAMPLE_RATE_HZ = 60000000`，`data_clk = 4 × 60 MHz = 240 MHz`（周期 4.1667 ns；Vivado 报告按 3 位小数显示 4.167 ns / 239.981 MHz）。
- `tCP`：**4.167 ns > 4.069 ns** ⇒ 满足 Table 51 的最小周期（余量 0.098 ns）。注意：**"tCP 满足"只是时钟周期校验，不是接口采样关系**。
- `tMP`：45–55% × 4.167 ns ⇒ 允许的脉宽不对称 **±0.208 ns**；实际 DATA_CLK 占空比未测量（TBD）。
- 该结论与本设计采样率一致；采样率变化时先看 `RF_SAMPLE_RATE_HZ` 与 `tCP` 的关系（`rf.xdc` 的行 116–117）。

### 4.3 Xilinx 7 Series IDELAYE2（~78 ps/tap、0..31）

| 项 | 值/来源 | 状态 |
|---|---|---|
| tap 范围 | `IDELAYE2` 为 5-bit，`0..31` | 器件结构；RTL 参数 `IDELAY_VALUE(0)`、`CNTVALUEIN` 5 位与 DCP 一致 |
| 单 tap 分辨率 | **~78 ps/tap**（名义值，200 MHz IDELAYCTRL，VTC 校准） | 厂商典型值（UG471）；**本工程未在本地复核 UG471**，属于"厂商典型、非板级实测" |
| 增量范围 | 31 × 78 ps ≈ **2.418 ns** | 推导；tap29/30/31 ≈ 2.262/2.340/2.418 ns |
| 参考时钟 | 200 MHz ± 10 MHz | 设计：`ref_clk200m` 由 `FCLK_CLK1` 驱动（[`scripts/create_bd.tcl`](../../scripts/create_bd.tcl)），`clk_fpga_1` 在 DCP 中为 5.000 ns（200 MHz）；`docs/rf/boardtest.md` §0.5/§2.2 说明 FCLK1 必须为 200 MHz 且不能通过 PYNQ 的 `Clocks` 去"设置" |
| 不确定性 | 真实 tap 延时会随 PVT 变化；IDELAYCTRL `RDY` 未观测 | 需按不确定性项保留，不能当精确常数 |

> 使用 78 ps/tap 时必须带"典型值/未本地复核"标签，并保留 ±5% 级不确定度；不要用它在报告里做"精确补偿量"。

### 4.4 板级与器件内部参数（**全部 TBD**，禁止填估算值）

| 参数 | 含义 | 状态/来源 |
|---|---|---|
| `rx_board_skew_ns` | `t(RX_D/RX_FRAME trace at FPGA) − t(DATA_CLK trace at FPGA)`（AD9361→FPGA） | **UNKNOWN/TBD**；项目内没有走线长度/延时数据；候选里 0.000 只是曝光值 |
| `tx_board_skew_ns` | `t(TX_D/TX_FRAME trace at AD9361) − t(TX_CLK trace at AD9361)`（FPGA→AD9361） | **UNKNOWN/TBD**；与 RX 物理方向不同，不能共用一个常数 |
| `tx_clk_fwd_delta_ns` | 转发时钟 ODDR/OBUF 发射路径 − 数据 ODDR/OBUF 发射路径（逐角） | 未从 DCP 单独读；DCP 只给数据路径 1.876/0.871 ns（slow/fast）。残余按未测处理 |
| AD9361 内部延时寄存器 | 器件侧可编程输入/输出延时：`rx_data_delay`=0x006[3:0]、`data_clk_delay`=0x006[7:4]、`fb_clk_delay`=0x007[7:4]、`tx_data_delay`=0x007[3:0] | 只有源码初值（`rx_data_delay=4`、TX FB clock 高 nibble=7、DATA_CLK delay=0），**不是**硅上读回；实际值 TBD（0.3 ns/LSB 的典型值来自 UG-672/AD9364 文档，对 AD9361 的适用性未证明，仅作 read-only 实验的参考，不用于本文任何数值） |
| DATA_CLK 实际占空比/抖动 | `tMP` 是否满足 | TBD（需板级测量或用足够带宽的仪器观察） |
| PCB/连接器走线延时 | 上述 skew 的物理来源 | 项目内无 data；TBD（不得按"等长"假设填 0） |

> 这些 TBD 是**模型输入**，不是"以后再说"的细节：只要它们未知，任何 "接口是否满足" 的结论都只是关于所填数值的结论。

---

## 5. 计算方法

### 5.1 符号与基本关系

```
T          = 4.167 ns              # data_clk 周期（本设计；LVDS 最大 240 MHz 档）
T/2        = 2.083 ns              # DDR 半周期
tDDRx/tDDDV = 0.25 .. 1.25 ns      # AD9361 DATA_CLK → Rx_D/Rx_FRAME（UG-570 Table 51）
tSTx/tHTx  = 1.0 / 0.0 ns          # AD9361 对 FB_CLK falling 的 setup/hold（同上）
tap_delay(t) = t × 0.078 ns        # IDELAYE2 增量（典型；未本地复核）
```

变量与外器件参数的对应（避免用错术语）：

| 通用叫法 | 本接口的对应量 | 进哪个约束 |
|---|---|---|
| 外部器件输出延时 `tCO`（时钟沿→数据有效） | RX：`tDDRx`（数据）/ `tDDDV`（帧），0.25..1.25 ns | `set_input_delay -max/-min`（相对 `data_clk`） |
| 外部器件 setup 要求 `tSU` | TX：`tSTx` ≥ 1.0 ns（相对 FB_CLK falling） | `set_output_delay -max` |
| 外部器件 hold 要求 `tH` | TX：`tHTx` ≥ 0.0 ns（相对 FB_CLK falling） | `set_output_delay -min = −tHTx + …` |
| 板级走线/连接器偏斜 skew | `rx_board_skew_ns` / `tx_board_skew_ns`（**TBD**） | 分别加在 max/min 上 |
| 时钟相位/周期 | `data_clk` 4.167 ns；TX 转发时钟相位由 `-invert` 表达 | 时钟定义，不写进 delay 数值 |
| FPGA 内部固定延时 | DCP 的 IBUFDS/IDELAYE2（固定部分）/IDDR、ODDR/OBUFDS、BUFG/时钟树 | **已在到达时间里**，不重复加到 delay 数值 |

Vivado 的 I/O 分析本质：

- **输入**：`set_input_delay` 把外部器件的输出延时（相对恢复时钟）加到数据到达上，工具再和 FPGA 内部 IOB/IDDR 的 setup/hold 窗口比较；
- **输出**：`set_output_delay` 把外部接收器件的 setup/hold 要求（相对转发时钟）加到"要求时间"上，工具再和 FPGA 内部发射路径比较。

### 5.2 RX：`set_input_delay`（源同步输入，两 DDR 边缘）

对 6 条数据 lane 与 frame（**P 侧**）各 4 行：

```tcl
# RX：tDDRx/tDDDV + rx_board_skew；-clock_fall -add_delay 是必须的
# （IDDR 是 SAME_EDGE_PIPELINED，Q1 上升沿、Q2 下降沿都采样）
set_input_delay -clock data_clk        -max [expr {1.250 + $rx_board_skew_ns}] $port
set_input_delay -clock data_clk        -min [expr {0.250 + $rx_board_skew_ns}] -add_delay $port
set_input_delay -clock data_clk -clock_fall -max [expr {1.250 + $rx_board_skew_ns}] -add_delay $port
set_input_delay -clock data_clk -clock_fall -min [expr {0.250 + $rx_board_skew_ns}] -add_delay $port
```

- `rx_frame_in_p` 用 `tDDDV`，数值与 `tDDRx` 相同（0.25/1.25），因此可先归入同一组；若以后测到 frame 与 data 的板级/器件延时不同，必须拆开，不能继续共用。
- `rx_board_skew` 的符号约定：`t(数据/帧走线) − t(DATA_CLK 走线)`；它同时以 1:1 影响 setup 与 hold 的**相反**方向（见 §5.5）。
- N 侧（`rx_data_in_n[*]`、`rx_frame_in_n`）是同一 IBUFDS 对的互补端，**不得**重复约束。
- 若要在同一 run 里表示运行期 tap，把 `+ rx_tap_scenario_ns` 作为**场景项**加在 max/min 上（§5.4），并明确这是"场景修正后的外部等效延时"，不是纯板级 delay。

### 5.3 TX：生成时钟 + `set_output_delay`（相对转发时钟）

**第一步：给 `tx_clk_out_p` 建生成时钟**（只有 P 侧；`tx_clk_out_n` 是同一 OBUFDS 的互补输出，不得建第二个时钟）：

```tcl
create_generated_clock -name tx_clk_fwd \
  -source [get_pins <phy_tx_inst>/ODDR_data_clk_inst/C] \
  -divide_by 1 -invert [get_ports tx_clk_out_p]
```

- `-source` 必须是**转发 ODDR 的时钟输入引脚**（即 `data_clk` 到达该 OLOGIC row 的位置），这样生成时钟自动继承 master clock 到该 row 的插入延时，转发路径与数据路径的公共部分才会相消；
- `-divide_by 1`：转发时钟仍是 240 MHz（不是 `-multiply_by 2`；ODDR 的 DDR 翻转率 ≠ 2 倍时钟）；
- `-invert`：由 `D1=0/D2=1` 的 SAME_EDGE 网表事实决定（§3.3）。若沿用 ADI 参考的 `D1=1/D2=0`，则不需要 `-invert`——**本设计与 ADI 参考差 180°**，必须按实际网表写。

**第二步：对 7 个 TX 数据/帧端口（P 侧）加 output delay**：

```tcl
# TX：tSTx/tHTx + tx_board_skew (+ tx_clk_fwd_delta)，相对 tx_clk_fwd，两边缘
# -max = tSTx + skew (+delta)；-min = -tHTx + skew (+delta)；tHTx=0 时 min 只剩 skew 项
set_output_delay -clock tx_clk_fwd        -max [expr {1.000 + $tx_board_skew_ns + $tx_clk_fwd_delta_ns}] $port
set_output_delay -clock tx_clk_fwd        -min [expr {-0.000 + $tx_board_skew_ns + $tx_clk_fwd_delta_ns}] -add_delay $port
set_output_delay -clock tx_clk_fwd -clock_fall -max [expr {1.000 + $tx_board_skew_ns + $tx_clk_fwd_delta_ns}] -add_delay $port
set_output_delay -clock tx_clk_fwd -clock_fall -min [expr {-0.000 + $tx_board_skew_ns + $tx_clk_fwd_delta_ns}] -add_delay $port
```

- **不能**像第一版候选那样把 TX delay 建在输入端口上的 `data_clk` 上：那等于让"要求时间"从 0 起算，而实际发射点已经有 6.474 ns（fall, slow）的时钟插入 + 1.876 ns 的 ODDR/OBUF 路径，导致约 −5 ns 的**纯模型伪差**（见 §5.6）；
- **不能**在 `tx_clk_out_p` 上再加数据 output delay：它是时钟参考，不是数据端口；
- N 侧（`tx_data_out_n[*]`、`tx_frame_out_n`）是 OBUFDS 互补端，不得重复约束。

### 5.4 运行期 IDELAY tap：场景，而不是一个固定值

```
t_IDELAY_total(tap) = t_IDELAY_fixed(corner) + tap × 0.078 ns      tap ∈ [0, 31]
  t_IDELAY_fixed = 0.680 ns (slow/max) / 0.244 ns (fast/min)   # 已在 routed 路径的 arrival 里
```

| 场景 | tap | 增量 | 用途 |
|---|---|---|---|
| `TAP_STATIC` | 0 | 0 ns | 只复现静态 DCP，**不代表运行接口** |
| `TAP_LOW` | 1..8 | 0.078..0.624 ns | hold 侧扫描 |
| `TAP_MID` | 9..16 | 0.702..1.248 ns | 找眼中心 |
| `TAP_HIGH` | 17..24 | 1.326..1.872 ns | setup 侧扫描 |
| `TAP_RUNTIME` | 25..29 | 1.950..2.262 ns | 观察到的运行邻域（tap29 附近） |
| `TAP_MAX` | 30..31 | 2.340..2.418 ns | 边界/饱和 |

规则：

- **一次 run 只对应一个 tap 组**，不要用一个固定 tap 代表整个 0..31 范围；
- 同一组内：**setup 用最大 tap、hold 用最小 tap**（tap 变大 = 数据更晚 = setup 减小、hold 增大，1:1）；
- 产出应是 **margin-vs-tap 的一段窗口**，而不是一个数；
- 若要写进约束，只能作为 `+ $rx_tap_scenario_ns` 的**场景等效项**，并如实标注"这不是板级 delay"；
- 也可以（在 scratch DCP 上、且先验证 Vivado 确实会对 VAR_LOAD 用 `IDELAY_VALUE` 重新计时后）用 `set_property IDELAY_VALUE <tap>`；**未验证前不得使用**。

### 5.5 为什么 board skew 未测时不能用"0"作为物理值

1. **0 不是"没有延时"的物理事实**：PCB 走线、FMC 连接器、封装、器件内部输出延时（tDDRx/tDDV）都会让数据与时钟的到达时刻不同；把 `rx_board_skew_ns=0` 写成物理值等于断言"所有走线零延时"，这个断言没有证据。
2. **1:1 敏感性**：`rx_board_skew +0.100 ns` ⇒ RX setup 余量 −0.100、hold 余量 +0.100；`tx_board_skew +0.100 ns` ⇒ TX setup hole −0.100。skew 可正可负，所以 0 既不是乐观也不是保守，只是**未暴露风险**。
3. **它是三个未知量之一**：`rx_board_skew_ns`、`tx_board_skew_ns`、`tx_clk_fwd_delta_ns` 目前都未测；任何基于它们的 "met/violated" 都只是对假设值的判断。
4. 候选参数里的 `RX_BOARD_SKEW_NS=0.000` / `TX_BOARD_SKEW_NS=0.000` **只是审查用曝光值**；第一版候选额外用了单一 `RF_IO_BOARD_SKEW_NS=0.000` 同时代表 RX 和 TX，这在修正模型里已被拆成两个独立参数（物理方向不同，不能共享）。

### 5.6 2026-10-26 只读试跑负 slack 的性质（模型未闭合，不是硬件结论）

当时的试跑（`candidate-io-check.tcl`，内存中给 DCP 加约束、不写回、不重建）用了：RX `-max 1.250 / -min 0.250` 相对 `data_clk`；TX `-max 1.000 / -min 0.000` 相对 `data_clk`；skew=0；tap=0。结果 **WNS −5.240 / WHS −2.879**（TNS failing 14 / THS failing 7）。它的性质是"模型未闭合"，原因可分解为：

1. **TX 参考时钟用错了（主要项）**：把 output delay 挂在 `data_clk` 的输入端口上，required 从 0 起算；实际发射点已含 6.474 ns（fall, slow）时钟插入 + 0.418/0.001/1.457 ns 路径 = 8.350 ns。约 −7.3 ns 的 lane1 伪差（整体 WNS −5.240）是"缺生成时钟"造成的模型伪差，与硅/板卡无关。修正模型（§5.3）把这个项消掉。
2. **RX tap=0 不是运行接口（第二项）**：静态模型只有 tap0 的固定延时（0.680/0.244 ns）；运行期 tap29/30 还要再加约 2.26–2.34 ns。试跑的 RX hold 项被这个差异主导，不是板级故障。
3. **skew=0 是曝光值**：1:1 影响且符号未知（§5.5）。

> 所以：这两个负数是**"约束模型没有闭合"的证据**，不是"硬件失败"结论，也不是改 RTL/tap 默认值/解调算法的理由。修正模型本身也只是"条件于未测参数"的模型，不构成 PASS/FAIL。

### 5.7 读报告口径（避免错报）

1. **`slack=inf` / `Path Group=(none)` = 没约束、没计时**，不能读成"通过"；也不能读成"失败"。
2. **P/N 侧**：check_timing 的 HIGH 列表只列 P 侧（差分对的计时代表）；methodology TIMING-18 的 output 段包含 N 侧，所以 37≠48 不矛盾（§2.2）。
3. **`data_clk` 的 WHS 有两个口径**：routed 报告 Intra-Clock Table 是 **+0.066 ns**（783 端点）；`async_default` path group 才是 **+0.349 ns**（175 端点）。旧 `CHAIN-TIMING.json` 把 0.349 记作 `data_clk_WHS_ns`，与报告 Intra-Clock 值不同；本文采用报告表值 0.066，并把 0.349 标为 async_default 组（与 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §3.5 的口径差异说明一致）。
4. **总体 WNS/WHS**：0.213/0.018 ns、67128 端点、TNS/THS = 0、`All user specified timing constraints are met.` —— 这句话只对**已写进约束的路径**成立。
5. **内部 WNS 为正 ≠ 外部接口裕量**：接口路径没有被计时，外部器件的 setup/hold 根本没有进入分析。

---

## 6. 缺口清单

| ID | 缺口 | 为什么关键 | 现状 | 关闭方式（需授权） |
|---|---|---|---|---|
| G-01 | **RX 板级 skew 未测**：DATA_CLK ↔ RX_D/RX_FRAME 走线延时差（两边缘） | 它按 1:1 吃掉 RX setup/hold 余量；0 ns 只是曝光值 | **TBD** | 阶段 1 板级测量（§7.2） |
| G-02 | **TX 板级 skew 未测**：TX_CLK ↔ TX_D/TX_FRAME 走线延时差 | 决定 tSTx/tHTx 在 AD9361 端是否满足；TX 没有 IDELAY 可调 | **TBD** | 阶段 1 板级测量（§7.2） |
| G-03 | **`tx_clk_out_p/n` 生成时钟模型未定**（无时钟对象；边沿/极性/发射路径与 ADI 参考差 180°） | 不解决就无法把 TX 发射路径按正确时钟参考计时 | 修正模型已离线给出形式（`create_generated_clock -source ODDR C -divide_by 1 -invert`），**未应用/未验证** | 阶段 2 只读 scratch 验证（§7.3） |
| G-04 | **runtime IDELAY tap 不在静态 DCP**（`IDELAY_VALUE=0`；运行期 tap 29 附近） | 静态报告不代表运行接口；tap 场景未建模 | 已记录 7 个 VAR_LOAD/共用 tap 总线/分 lane LD；场景化方案未执行 | 阶段 2 场景 run（§7.3），或板级相位测量 |
| G-05 | **AD9361 内部寄存器未读回**：0x010/0x011/0x012（LVDS/2R2T/FDD/data swap）、0x006/0x007（data delay） | 模式与内部延时只来自源码初值；不知道硅上实际值 | 只有源码口径；`rf.interface_status` 读路径已作为候选准备（交付软件已含客户端方法，需板级确认），**未上板** | 阶段 1 单次只读/单初始化会话（§7.2） |
| G-06 | **只读试跑负 slack 不可当物理结论** | 模型缺 TX 生成时钟、缺 tap 场景、skew=0 | 已分解（§5.6），verdict = `REJECTED_AS_QUALIFICATION_MODEL_NOT_A_PHYSICAL_FAILURE_CLAIM` | 阶段 2 用修正模型重跑；仍只是"模型一致性"证据 |
| G-07 | **可用示波器带宽不足以做 240 MHz LVDS 亚纳秒判定** | 70/100 MHz 级示波器无法分辨 ~0.1 ns 级 skew；用它测出的数不能当 sub-ns 证据 | 项目记录明确说明：可用 70/100 MHz 示波器**不是** 240 MHz LVDS 的亚纳秒资格器 | 阶段 1 需要足够带宽的差分探头/示波器（或改用受控 IDELAY/眼图实验，另立授权）；若仪器不满足，测量结果只能记"未取得资格数据" |
| G-08 | **DCP/bit 只是产物级身份，不是源码级同源** | 构建记录无 RTL/XDC 哈希，无法断言交付 bit 由当前 HEAD 源文件构建 | 已确认真实：DCP 与交付 bit 载荷相同（payload_equal）、HWH 仅时间戳差异；source-level 未证明 | 如评审要求，归档构建输入/哈希（阶段 2 一并） |
| G-09 | **TX 侧没有可编程延时**（无 IDELAY；只有 ODDR/时钟域） | 若实测 TX 相位不对，不能用 tap 修；只能在 RTL/时钟极性/板级层面处理 | 现状事实 | 阶段 1 测量后决定；任何 RTL 改动另立审查，本文不提出 |
| G-10 | **计数口径未统一**（9/39 vs 10/48 vs 37 vs 7/8） | 错报会影响对外表述与验收判断 | 已在 §2.2/§2.3/§5.7 建立映射 | Lead/评审在引用时统一用本文口径 |

---

## 7. 最小修复方案（可执行、分阶段）

> 总原则：**先补模型（约束），再测板级，最后才谈是否满足。** 任何阶段都不允许用 false path、放宽时钟、或没有来源的 delay 来让报告变绿。

### 7.1 阶段 0 —— 离线（本轮完成的部分 + 需授权的后续动作）

**本轮已完成**：识别缺陷、建立端口/口径映射、参数来源、计算方法、缺口与本文档。**未创建/未应用任何候选 XDC**。

**后续动作（离线，无需板卡；Vivado 语法检查需单独授权）**：

1. **候选入库（建议，需 Lead 授权）**：把修正后的参数化候选 XDC 作为**审查材料**加入版本控制，建议位置 `rf_overlay/constraints/candidate/rf-io-timing.xdc`（与产品 `rf.xdc` 分开；**不加入** `build.tcl` 的 `constrs_1`、不进入任何构建）。文件头必须写：`CANDIDATE / NOT APPLIED / NOT BUILT / NOT INSTALLED`、参数含义、来源等级、TBD 项。
2. **静态检查（不启动 Vivado）**：
   - `rf.xdc` 中确实没有 `set_input_delay`/`set_output_delay`/`create_generated_clock`（已由本次只读核对确认）；
   - 候选文件里**不出现绝对路径/盘符/用户名**；端口名与 DCP 的 P 侧端口集合一致（§2.3）；N 侧不施加 delay；`tx_clk_out_p` 只加生成时钟；
   - 候选文件与产品构建无引用关系（grep `build.tcl`、`package_merged_overlay.py` 等入口）；
   - 参数值：来源值（tDDRx/tDDV/tSTX/tHTX）来自 UG-570 Table 51；未测项全部保持符号参数（`rx_board_skew_ns`/`tx_board_skew_ns`/`tx_clk_fwd_delta_ns`/`rx_tap_scenario_ns`），**不得**出现数值型"物理假设"。
3. **Vivado 只读语法/模型检查（需单独授权，脚本 open_checkpoint 只读、close_design 不写回）**：
   - 应用候选（或仅生成时钟）→ `report_clocks -verbose`：应出现 `tx_clk_fwd`，master 为 `data_clk`，period 4.167、source 为转发 ODDR 的 C 引脚；
   - `check_timing`：RF 7 个输入端口应从 `no_input_delay`（HIGH）消失；7 个 TX 数据/帧端口应从 `no_output_delay`（HIGH）消失；`tx_clk_out_p` 仍可能被报"无 output delay"（预期：它是时钟，不是数据 delay 目标）；`check_timing` 的 `generated_clocks` 违规计数应保持 0（不是 +1），新增的 `tx_clk_fwd` 用 `report_clocks` 确认；不得新增 unconstrained endpoint；
   - `report_exceptions`：RF 串行线上仍然**没有** false path；
   - `report_timing`（lane1）应出现 `Path Group` 不再是 `(none)`、`slack` 不再是 `inf`（数值本身受 TBD 参数影响，**不作为通过/失败判据**）。
4. **证据**：命令、日志、`.rpt`、DCP 的 SHA-256、工具版本、候选文件 SHA-256 存入本地证据目录（命名沿用项目惯例）；**不写回产品 DCP**。

**阶段 0 通过标准**：语法/端口/时钟对象/check_timing 计数符合预期；没有新增 false path、没有放宽时钟、没有无来源数值。**不是**"接口时序通过"。

### 7.2 阶段 1 —— 板级测量（最小权限；本轮 NOT RUN）

**前置**：单独授权；确认板卡安全状态（RF 关闭、未布防、TX 源未选）；不改 bit/HWH、不重装（若读寄存器路径需要候选软件，先按项目既有试部署/回滚流程评审）。

**步骤（一次会话、最小动作）**：

1. **单初始化会话读配置**：按正常 own/token 流程初始化 + 配置一个固定工作点（让 DATA_CLK 出现）；**不采集、不加载/改变 TX 载荷、不做 RF 发射实验**；读取 AD9361 `0x010/0x011/0x012`（LVDS/2R2T/FDD/data swap）与 `0x006/0x007`（RX/TX data delay），记录实际值。可用路径：交付软件已含 `rf.interface_status` 客户端方法（`candidate.tgz` 内 `release/rf_status_client.py` 已确认存在 `get_interface_status()`/`set_rx_data_delay()`；实际能力以会话现场握手为准）；若不可用，则按已准备但未安装的读接口候选执行（另走部署评审）。失败/不可读 → 记录并停止，不得用源码值冒充读回。
2. **示波器/探头测量**（与步骤 1 同一次授权内，或另行授权）：
   - 测点：FMC connector/模块侧或板上可达测试点（FPGA BGA ball 直接探测通常不可行；如用转接/探头需记录负载与接地方式）；
   - 对象：`DATA_CLK` 边沿 → `RX_D[1]`/`RX_FRAME` 跳变（rise/fall 两边缘各测）；`TX_CLK` 边沿 → `TX_D`/`TX_FRAME` 跳变（两边缘）；记录 lane1 与其他 lane（至少再 1 条）以判断 lane-to-lane skew；
   - 记录：配置/采样率/LO/温度、探头型号与带宽、示波器带宽/采样率/时基、触发方式、平均次数、测点位置；
   - **仪器门槛**：若要支撑 sub-ns 结论，需要足够带宽的差分探测（项目现有 70/100 MHz 级示波器**不能**作为 240 MHz LVDS 的亚纳秒资格器）。若只能用低带宽仪器，则本阶段只能产出"定性/粗测"，**不得**据此宣称接口满足。
3. **通过条件（先写死，后测量）**：
   - **RX**：测得的数据/帧跳变（相对恢复出的 DATA_CLK 采样沿）必须位于采样窗口的中心区域、远离采样孔径，使 IDDR 的 setup/hold 都为正并留有可声明裕量；所需的相位修正必须能被 **0..31 tap（0..2.418 ns 增量）**覆盖（IDELAY 只能在数据上加延时；若数据"太晚"到 IDELAY 范围之外，则属于时钟相位/板级问题，不是 tap 能修的）。
   - **TX**：在 AD9361 输入引脚处，`TX_D/TX_FRAME` 相对 `FB_CLK` 的 setup/hold 必须满足 **tSTx ≥ 1.0 ns / tHTx ≥ 0.0 ns**（Table 51，FB_CLK falling）；TX 没有 FPGA 侧可编程延时，若违反 → 记录为"板级/时钟相位问题"，进入方案权衡（不能在本阶段临时改 RTL）。
   - **两边缘偏斜**：rise/fall 的测量差必须在测量不确定度以内；若仪器不确定度与待测 skew 同量级，判"未取得合格数据"。
   - **任何一项不能测量 → 仍然是 TBD**，不允许用"应该没问题"补位。
4. **产出**：测量原始数据/截图/配置记录/仪器设置 → 本地证据；更新 §4.4 的 TBD 值（带测量条件与不确定度）。

**本阶段明确不做**：不发射 RF 业务载荷、不采集 IQ、不做 tap 扫描研究、不改解调/默认参数、不下载新 bit（除非另立授权）。

### 7.3 阶段 2 —— 离线实现试跑（需单独授权；NOT RUN）

**目的**：把"参数化候选 XDC + 已测 skew（或明确的符号参数）"应用到合并设计，验证**约束模型自身**，并区分"模型问题"与"板级相位问题"。**不下载、不采集、不安装。**

**建议顺序**：

1. **只读模型验证（先做，代价小）**：open routed DCP → 应用修正候选（生成时钟 + RX/TX delay，skew 用已测值或符号参数）→ `report_clocks -verbose`、`check_timing`、`report_exceptions`、`report_timing`（lane1 RX/TX）→ 确认时钟对象/路径组/端口覆盖正确；关闭不写回。
2. **完整实现试跑（如授权）**：在合并设计约束集中加入候选（variant 或独立 constrs 文件，不并入产品 `rf.xdc`），跑 synth+impl（**不生成/不下载 bit** 或按授权仅生成、不安装），保存 `utilization/timing/check_timing/methodology` 报告；与基线（WNS +0.213 / WHS +0.018、TNS/THS 0）对比，并列出：
   - RF 7 输入 / 7 数据/帧输出是否仍有"无 delay"报告；
   - `tx_clk_out_p` 是否有生成时钟且没有误加数据 delay；
   - RF 串行线上没有 false path/clock relaxation；
   - `data_clk` 域和总体 WNS/WHS 的变化（只作同口径对比；实现重跑会改变内部插入延时，不能与旧 DCP 数值硬配）。
3. **判定规则**：
   - 若模型应用后接口路径仍有负 slack：先区分 (a) 模型错误（时钟参考/边缘/极性/N 侧/场景 tap 不匹配）→ 修模型；或 (b) 板级相位超预算（用 §7.2 测量值代入仍为负）→ 进入板级/时钟相位方案讨论；或 (c) 实现版图变化导致内部延时变化 → 用同一 build 内的新数值重算。**不允许**靠调 delay 数值硬凑正 slack。
   - 若模型应用后为正：只能表述为"**在该参数集（含 TBD 假设）与本次实现下，约束模型闭合**"，不得写成"接口时序通过/眼图合格/硅上裕量满足"。
   - 若要改 RTL（如 TX 时钟极性/相位、IDELAY 结构），另立 RTL 变更 + 独立审查 + 重新 build/验证，**不属于本文最小方案**。

### 7.4 验收口径与"不能宣称"

| 事项 | 可以宣称 | 不能宣称 |
|---|---|---|
| 约束齐全 | "RF 7 输入 / 7 数据/帧输出已加 delay；TX 转发时钟已建模（在候选/实现中）" | "接口时序通过" |
| 内部时序 | "已约束内部路径 WNS/WHS 为正" | "外部接口裕量为正" |
| 实现试跑 | "约束模型在给定参数集下闭合/未闭合" | "板级眼图/硅上裕量合格" |
| 板级测量 | "在记录的条件与不确定度下，测得 skew = X ± u ns" | "任意温度/电压/配置下都满足" |
| TB/仿真 | "逻辑重组在给定发射相位下正确" | "物理采样相位/IDELAY tap 有效/硅裕量" |
| 演示冒烟 | "该小样本该配置下可工作" | "共享前端链路已闭合" |

### 7.5 需要确认的事项（提交 Lead/用户）

1. **是否可以执行阶段 1 的板级只读/单初始化会话**（读 0x010–0x012/0x006–0x007 + 示波器测量）；需要单次授权与现场接线确认。
2. **是否具备足够带宽的差分探头/示波器**（现有 70/100 MHz 级不满足 sub-ns 资格）；若不具备，阶段 1 只能产出定性数据，或改为受控 IDELAY/眼图实验（另立范围）。
3. **是否授权阶段 0 的候选 XDC 入库**（仅 candidate 文件，不应用）与**阶段 2 的离线实现试跑**（Vivado 综合/实现，不下载）。
4. **文件名规则例外**：本文按任务指定的 `RF-INTERFACE-TIMING.md`（大写连字符）落盘；仓库命名规则（全小写）与此冲突，[GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md) 已记为 H04/G14 待确认，请 Lead 统一决定是否改小写及是否同步索引。

---

## 8. 与交付的关系

### 8.1 交付版本口径（2026-10-26）

- 交付/安装版本：`4814bbc42b9d5e0165a3ed3ebe7eaeef`；bit `d5bfe5d9…`；HWH `ed8d011a…`；daemon `e51e97ae…`（1.0.4 / protocol 1）；软件合同 `cnn-bpsk-modrec4-qpsk-rrc-qam16-v1`（62 文件，**QAM64 已移出**，不再交付 QAM64 接收/管线）；bundle `74c6e32c…`；回退目标 `036a4ec9…`。
- 交付复审（[2026-10-26 交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)）的结论是 **LIMITED APPROVED**（产品缩减 + 本轮 BPSK/QPSK 演示证据）但**完整交付闭环声明为 CHANGES_REQUIRED**：保留 QAM16 的 1/256 非零误码、试部署失败后多次续跑导致的预算偏差、以及若干材料与原始记录不一致。这些**不是**接口时序证据，也不因本文而升级。
- 交付的已知限制（交付包 `docs/KNOWN-LIMITATIONS.md` 第 6 条；交付范围文档 §2.3 的口径）明确保留：**RF DDR I/O timing model is not closed**——routed DCP 无 input/output delay；静态 DCP 的 `IDELAY_VALUE=0` 不代表运行 tap；0 ns skew 试跑负裕量是模型未闭合证据，不是硬件失败结论。
- 交付 bit 与本文件分析的 routed DCP 是**同一配置载荷**（§2.1），因此**该接口约束缺口对交付 bit 同样成立**（与 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §3.5 的表述一致）。

### 8.2 对当前 BPSK/QPSK 有限演示的影响

- 交付冒烟（2026-10-26 实测）：BPSK 两 ON 帧 `0/138`、QPSK 两 ON 帧 `0/128`、QAM16 `1/256` 与 `0/256`；OFF 帧干净；12 个接受帧，另有 2 次 QAM16 ON2 出现在失败尝试中（物理捕获 14）。**BPSK/QPSK 的 0 误码只是小样本冒烟证据**，不构成接口裕量证明。
- 风险表述只能到："当前 BPSK/QPSK 有限演示在 2026-10-26 的记录条件下观察到 0 误码；**RF 源同步 DDR 接口时序未被覆盖/未被测量**，因此存在未覆盖的残余风险。" **不得**写成"必然失败"，也**不得**写成"链路已闭合"。
- 原因（交付范围文档已写）：RX/TX 串行链路、IDELAY、IDDR/ODDR 是**所有模式共用**的前端；QAM64 研究中暴露的 raw-Q（`adc_data_q1`/`in_q`，源 `IDDR_data[1].Q2`）set-only 0→1 残余是统计性线索，不是确定性结论；低阶模式也可能低概率出现，不能因冒烟 0 误码推断链路闭合。

### 8.3 QAM64 移出交付的边界

- QAM64 **不在交付范围**；其源码/历史证据保留，但**不作为交付依据，不追认**。
- QAM64 的失败/未闭合**不是硬件故障结论**：现有证据支持的是"约束/模型未闭合 + 共享链路采样相位残余"，不是"板卡坏了"或"RTL 确定缺陷"。
- 本文使用 QAM64 线的**只读工具证据**，是因为它复核的正是交付 bit 对应的同一 DCP/同一约束；使用范围仅限"接口覆盖缺口"这一事实，**不外推** QAM64 的任何性能结论。

### 8.4 对外表述建议（可直接引用）

> "合并设计内部已约束路径的 routed 时序为 WNS +0.213 / WHS +0.018 ns（TNS/THS = 0）；但 AD9361 2R2T LVDS DDR 外部接口没有 `set_input_delay`/`set_output_delay`，RX 7 个输入 / TX 8 个输出（含 1 个转发时钟输出）未被计时，且板级走线 skew、AD9361 内部 delay 与运行期 IDELAY tap 均未测量。因此**不能宣称 RF 接口时序通过**；该缺口是交付的已知限制之一，已保留为后续测量/实现试跑的最小方案。"

---

## 9. 出处与复现

### 9.1 本地原件（`<LOCAL_EVIDENCE>`；**不在仓库**）

| 证据 | 路径（相对 `<LOCAL_EVIDENCE>`） | 用途 |
|---|---|---|
| Lane1 时序缺口主报告 | `rf-teaching/qam64-lane1-timing-2026-10-26/REPORT.md` | 链、时序、trial、下一步 |
| 机器可读事实 | `rf-teaching/qam64-lane1-timing-2026-10-26/CHAIN-TIMING.json` | DCP/链/参数/计数/试跑 |
| 第一版候选 XDC（review-only） | `rf-teaching/qam64-lane1-timing-2026-10-26/candidate-rf-io-timing.xdc` | 单 skew、TX 参考错误的历史候选 |
| 试跑脚本（只读，不改产品） | `rf-teaching/qam64-lane1-timing-2026-10-26/{inspect.tcl,inspect2.tcl,candidate-io-check.tcl,candidate-rx-check.tcl,syntax-check.tcl}` | 复现证据 |
| Vivado 只读报告 | `rf-teaching/qam64-lane1-timing-2026-10-26/vivado/{check_timing,clocks,exceptions,io,rx_input_paths,tx_output_paths,timing_summary_max,timing_summary_min,candidate_rx_input_paths,methodology}.rpt`、`syntax-check.log` | 本文全部 DCP 数字 |
| 修正后的 I/O 时序模型 | `rf-teaching/qam64-io-timing-model-2026-10-26/{REPORT.md,corrected-xdc-candidate.md}` | 生成时钟、RX/TX 分离、tap 场景、负 slack 分解 |
| RX 延迟接口/寄存器证据 | `rf-teaching/qam64-rxdelay-interface-2026-10-26/{REPORT.md,TIMING-MODEL.md,NEXT-EXPERIMENT-PLAN.md,REGISTER-EVIDENCE.md}` | 0x006/0x007/0x010–0x012、0.3 ns/LSB（UG-672）、70/100 MHz 示波器限制 |
| 2R2T 协议/时序参考 | `rf-teaching/qam64-serial-reference-2026-10-26/REPORT.md` 与 `sources/{UG-570-RevA-extracted.txt,UG-570-page110-Figure79-400dpi.png,AD9361-datasheet-extracted.txt,adi-hdl/*}` | Table 51/Figure 79、2R2T frame、ADI HDL 参考 |
| DCP 身份链 | `rf-teaching/qam64-halfword-dcp-2026-10-26/{REPORT.md,dcp-audit.json}` | DCP/bit/HWH SHA 与载荷关系 |
| bit↔DCP 载荷绑定 | `codex-frame-idelay-check-20261025/verification.json` | `payload_equal:true`、DCP SHA、7×IDELAY 连接 |
| 交付身份/限制 | `rf-teaching/qam64-delivery-release-2026-10-26/{REPORT.md,docs/*}`；`rf-teaching/qam64-delivery-scope-2026-10-26/release-docs/KNOWN-LIMITATIONS.md` | 交付版本、QAM64 移出、接口限制 |
| 交付复审 | `docs/reviews/2026-10-26-codex-delivery-closeout-review.md`（仓库内） | LIMITED APPROVED / CHANGES_REQUIRED 与 P1/P2 |
| Lane1 残差推进建议 | `docs/reviews/2026-10-26-codex-lane1-residual-next.md`（仓库内） | 旧报告 9/39 的来源 |

### 9.2 仓库内相关文档

- [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §3.5（接口缺口与 G2/G5）、§2 指标表第 7 行、§7 复现说明；
- [DESIGN-REPORT.md](DESIGN-REPORT.md)、[DELIVERY-INDEX.md](DELIVERY-INDEX.md)（公开材料的阅读顺序与限制）、[GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md)（文件名大小写 H04/G14）；
- [`rf_overlay/constraints/rf.xdc`](../../rf_overlay/constraints/rf.xdc)、[`tools/impl1_clock_groups.tcl`](../../tools/impl1_clock_groups.tcl)；
- [`docs/rf/compatibility.md`](../rf/compatibility.md)（FMC→Bank 34/35、2.5 V、LVDS_25 电气前提）、[`docs/rf/boardtest.md`](../rf/boardtest.md)（IDELAYCTRL 200 MHz、判据先于测量、L1/L2/L3 边界）；
- [2026-10-26 交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[2026-10-26 lane1 残差推进](../reviews/2026-10-26-codex-lane1-residual-next.md)。

### 9.3 复现步骤（在开发机；本轮**未执行**）

```text
# 0) 先在开发机核对 DCP 身份（只读）
SHA-256 of <LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_routed.dcp
  → 期望 63f679bcce815ba6c883f2e47b286dfc35a549ae9d040eba028548bd2be7c305
  大小 22,937,299 B；文件时间戳 2026-09-24 10:10:33

# 1) 用 Vivado 2025.2 只读打开（不综合、不实现、不写回）
open_checkpoint <LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_routed.dcp
check_timing -verbose -file <out>/check_timing.rpt
report_clocks        -file <out>/clocks.rpt
report_exceptions    -file <out>/exceptions.rpt
report_timing -from [get_ports {rx_data_in_p[1]}] -delay_type min_max -max_paths 5 -nworst 2 -file <out>/rx_input_paths.rpt
report_timing -to   [get_ports {tx_data_out_p[1]}] -delay_type min_max -max_paths 5 -nworst 2 -file <out>/tx_output_paths.rpt
report_timing_summary -delay_type min_max -no_detailed_paths -file <out>/timing_summary.rpt
report_methodology   -file <out>/methodology.rpt
# 读取端口/原语属性：get_ports/get_cells/get_pins/get_nets/get_property（只读）
close_design
```

- 证据目录中的 `inspect.tcl`/`inspect2.tcl` 可直接复用（它们含开发机绝对路径，复用时替换为本地路径）；`candidate-io-check.tcl`/`candidate-rx-check.tcl` 会在内存中加约束并出报告，**不得**写回产品 DCP；`syntax-check.tcl` 只验证 `set_input_delay` 语法。
- 所有命令的输出、日志、SHA-256、工具版本一起归档；**不要**把"打开了 DCP/跑出报告"当成"接口通过"。
- 复现时禁止：`write_checkpoint`/`write_bitstream`/`launch_runs`/安装/板卡操作；禁止为让报告变绿而修改 RTL/XDC/时钟/false path。

### 9.4 本文限制

1. 数字全部转引自本地只读报告与厂商资料；**本轮没有重跑任何 Vivado 命令**。
2. 板级/器件内部未知量（§4.4）保持 TBD；任何读者不得把 0.000 的审查占位值当物理值引用。
3. 本文不构成"接口满足"或"接口失效"的结论，也不替代阶段 1/2 的证据。
4. 本文的端口/计数口径（§2.2/§2.3）若与其他文档冲突，以真实 DCP 报告原文为准，并回写统一。

---

## 附录 A：参数化候选约束（review-only 摘录，**未应用、未入库为产品**）

> 摘自修正模型 `<LOCAL_EVIDENCE>/rf-teaching/qam64-io-timing-model-2026-10-26/corrected-xdc-candidate.md` 的结构，参数保持符号；**不是可直接使用的最终文件**。第一版候选（`candidate-rf-io-timing.xdc`）把 TX delay 建在 `data_clk` 上并同时约束 P/N 两侧，已被修正模型取代。

```tcl
# CANDIDATE-ONLY / NOT APPLIED / NOT BUILT / NOT INSTALLED
# 来源：UG-570 Rev.A Table 51（tDDRx 0.25..1.25、tSTx 1.0、tHTx 0.0）
# 未测项保持符号；下面的 0.000 只是"曝光值"，不是物理假设。
set rf_data_clk_period_ns 4.167
set rx_board_skew_ns      0.000   ;# TBD: t(RX_D/RX_FRAME trace) - t(DATA_CLK trace)
set tx_board_skew_ns      0.000   ;# TBD: t(TX_D/TX_FRAME trace) - t(TX_CLK trace)
set tx_clk_fwd_delta_ns   0.000   ;# TBD: forward-clock launch path - data launch path
set rx_tap_scenario_ns    0.000   ;# TBD: tap x 0.078 ns（按场景组选择，不是一个常数）

# 1) TX 转发时钟：REF 必须取转发 ODDR 的 C 引脚；-divide_by 1 -invert = D1=0/D2=1 的 SAME_EDGE 事实
create_generated_clock -name tx_clk_fwd \
  -source [get_pins <phy_tx_inst>/ODDR_data_clk_inst/C] \
  -divide_by 1 -invert [get_ports tx_clk_out_p]
# tx_clk_out_n 是同一个 OBUFDS 的互补端：不建第二个时钟

# 2) RX：7 个 P 侧输入，两个 DDR 边缘；frame 暂与 data 同组（tDDDV=tDDRx）
foreach p {rx_data_in_p[0] rx_data_in_p[1] rx_data_in_p[2] rx_data_in_p[3] \
           rx_data_in_p[4] rx_data_in_p[5] rx_frame_in_p} {
    set_input_delay -clock data_clk        -max [expr {1.250 + $rx_board_skew_ns + $rx_tap_scenario_ns}] [get_ports $p]
    set_input_delay -clock data_clk        -min [expr {0.250 + $rx_board_skew_ns + $rx_tap_scenario_ns}] -add_delay [get_ports $p]
    set_input_delay -clock data_clk -clock_fall -max [expr {1.250 + $rx_board_skew_ns + $rx_tap_scenario_ns}] -add_delay [get_ports $p]
    set_input_delay -clock data_clk -clock_fall -min [expr {0.250 + $rx_board_skew_ns + $rx_tap_scenario_ns}] -add_delay [get_ports $p]
}

# 3) TX：7 个 P 侧数据/帧端口，相对 tx_clk_fwd，两边缘。
#    -max = tSTx + skew + delta；-min = -tHTx + skew + delta（tHTx=0）
foreach p {tx_data_out_p[0] tx_data_out_p[1] tx_data_out_p[2] tx_data_out_p[3] \
           tx_data_out_p[4] tx_data_out_p[5] tx_frame_out_p} {
    set_output_delay -clock tx_clk_fwd        -max [expr {1.000 + $tx_board_skew_ns + $tx_clk_fwd_delta_ns}] [get_ports $p]
    set_output_delay -clock tx_clk_fwd        -min [expr {0.000 + $tx_board_skew_ns + $tx_clk_fwd_delta_ns}] -add_delay [get_ports $p]
    set_output_delay -clock tx_clk_fwd -clock_fall -max [expr {1.000 + $tx_board_skew_ns + $tx_clk_fwd_delta_ns}] -add_delay [get_ports $p]
    set_output_delay -clock tx_clk_fwd -clock_fall -min [expr {0.000 + $tx_board_skew_ns + $tx_clk_fwd_delta_ns}] -add_delay [get_ports $p]
}
# 4) runtime IDELAY tap 不进 set_input_delay 的"纯板级"项；按 §5.4 的场景组单独 run
```

## 附录 B：一页检查清单

**任何时候引用/升级结论前：**

- [ ] 你说的是"约束齐了"还是"接口时序通过"？只有前者可能被本文/阶段 0/2 支持。
- [ ] 引用的 slack/WNS/WHS 是否带"只覆盖已约束内部路径"的限定？
- [ ] 是否把 `slack=inf`/`Path Group=(none)` 误读成"通过"？
- [ ] 是否把 0.000 的审查占位 skew 当成物理值？
- [ ] 端口数是否按 §2.2/§2.3 的 P 侧口径，且区分了 `tx_clk_out_p`（时钟）与 7 个 TX 数据/帧端口？
- [ ] 是否把 BPSK/QPSK 冒烟 0 误码当成了"共享前端时序闭合"？
- [ ] 引用 QAM64 证据时，是否写明"只用于接口覆盖事实、不作交付结论"？
- [ ] 板级测量是否记录了仪器带宽/探头/配置/温度/不确定度；低带宽仪器的数据是否被明确降级？

**阶段 0 回归口径**：

- [ ] `check_timing` 的 `no_input_delay` HIGH 段少了 7 个 RF 输入；
- [ ] `no_output_delay` HIGH 段少了 7 个 TX 数据/帧（`tx_clk_out_p` 属于时钟口径，不计）；
- [ ] `report_clocks -verbose` 出现 `tx_clk_fwd`（master=`data_clk`，4.167 ns，`-invert`）；
- [ ] `report_exceptions` 中 RF 串行线仍无 false path；
- [ ] 无新增 unconstrained endpoint，无放宽时钟定义，无无来源 delay 数值。

---

*本文为离线诊断文档；所有未测项保持 TBD。若后续完成阶段 1/2 并取得证据，应新建带日期/授权的收据并在本文追加"更新记录"，不得直接改写既有局限。*
