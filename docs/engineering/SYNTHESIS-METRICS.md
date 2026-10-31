# 综合与实现指标整理（资源 / 时钟 / 性能 / 上板实测）

整理日期：2026-10-31。本文按赛题指南 **3.3.5.1（综合与实现报告，含资源占用、时钟频率与关键性能指标；上板工程、运行脚本、实测输出与参考结果的比对数据）** 与 **3.3.4（优化前后性能与资源对比、可复现、判断依据）** 整理**已有**材料。

> **边界（必须保留）**
> 1. 本轮**只整理，没有重新综合、没有操作板卡、没有补采、没有修改源码/算法/判据/证据原件**。所有数字照抄原文，保留原文限定语。
> 2. 本仓库**不含**位流/HWH、Vivado 报告（`.rpt`）、原始收据与原始 IQ。因此本文指标分两级：**仓库内文档记载**（可在本仓库阅读原文）与**本地原件记载**（原始报告/收据在开发机，不在仓库，路径以 `<LOCAL_EVIDENCE>/…` 表示）。两者都不能在本副本内复算。
> 3. 状态词：`已有`（原件或报告存在且已核对表头）/ `部分`（只有文档记述或只覆盖部分路径）/ `未取得`（在可检索范围内没有对应原件）/ `非当前版本`（历史候选或已移出交付范围）。`NOT_RUN` / `NOT_MEASURED` / `NOT_IMPLEMENTED` 保持原样，不改写成"通过"。

## 1. 范围与口径

### 1.1 当前交付版本（以 2026-10-26 交付复审与 README 口径为准）

| 项 | 口径 | 出处 |
|---|---|---|
| 交付范围 | BPSK/QPSK 有限接收演示 + 有限条件 QAM16；**QAM64 已移出交付**；外部 SDR 发射、板卡纯接收为现场演示方向 | [README](../../README.md)「当前交付范围」 |
| 交付复审结论 | 产品缩减及 BPSK/QPSK 演示证据 `LIMITED APPROVED`；完整交付闭环声明 `CHANGES_REQUIRED` | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| 该口径下最后一次记录的安装候选 | `4814bbc42b9d5e0165a3ed3ebe7eaeef`（2026-10-26 19:26 只读读回 current/active 一致、双服务 active；**历史安装记录，不代表当前板态**） | 同上 |
| 该候选内 PL 产物 | `awg_merged.bit`，`bit_sha256 = d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555`；`awg_merged.hwh`，`hwh_sha256 = ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0`；`build_id = cnn-bpsk-teaching-v1-20261004-s1`；daemon `1.0.4`；协议 `1` | `<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/candidate/merged_release_manifest.json`（本地原件） |

> 候选 ID 代表**那一次打包**；本文件引用其清单只为说明交付 PL 产物的身份。该 PL 载荷与本地 2026-09-24 同次构建逐字节相同（§3.1），因此硬件指标来源不依赖候选的软件版本；反之，候选软件版本的验收数据也不因 PL 相同而升级为当前版本结论。

**版本判定规则（本文件贯彻）**：`4814bbc4`、`b82b141e`、`399b300e`、`aba80e92`、`e66197ed`、`b67a8aaa`、`adfac1f1`、`6384a07e` 等候选 ID 只作为**历史记录**引用，必须带候选与日期，不得移植成当前版本指标；**历史 QAM64 时序/资源报告不作为当前交付结果**（见 §3.5）。

### 1.2 证据层级与 `<LOCAL_EVIDENCE>`

| 占位 | 含义 |
|---|---|
| 仓库相对路径（如 [`../rf/l2_stage1_record.md`](../rf/l2_stage1_record.md)） | 可在本仓库内阅读的文档记述；原始收据不在仓库，不能复算 |
| `<LOCAL_EVIDENCE>/…` | 开发机私有证据与原始构建根目录（含 `evidence/` 收据、以及 `awg/`、`rf_overlay/`、`pynq_overlay/` 三个 Vivado 工程的 `*.runs/synth_1`、`*.runs/impl_1` 报告与 routed DCP）。**不在仓库**，路径去掉盘符与用户名后表示 |

### 1.3 实际构建目标（三条流水线，产物不混用）

| 目标 | 构建入口（仓库内） | 本地报告产物 | 本文状态 |
|---|---|---|---|
| **合并产品 Overlay**（PS7 + LCD + AXI DMA/BRAM + AD9361 样点通路 + AWG 逻辑） | `scripts/build_bitstream.py`、`scripts/create_bd.tcl`（`host_bd`，含 `awg_top_0`） | `host_bd_wrapper_*.rpt`（impl_1，2026-09-24） | `已有`（报告为本地原件；交付 bit 载荷已与同次构建绑定，见 §3.1） |
| 独立 RF Overlay（AD9361 板级 bring-up） | `rf_overlay/build.tcl` | `rf_bd_wrapper_*.rpt`（impl_1，2026-09-19） | `已有`（本地原件；不是交付 bit） |
| 独立 PYNQ Overlay（LCD/GPIO） | `pynq_overlay/build.tcl` | `ledbtn_bd_wrapper_*.rpt`（impl_1，2026-09-13） | `已有`（本地原件；不是交付 bit） |
| 辅助 AWG（旧 ACM2108 独立链） | `scripts/build_bitstream.py`（历史说明见 [awg-legacy-guide.md](awg-legacy-guide.md)） | 旧独立位流/报告不在本副本 | `未取得`（只有历史验收数据，见 §2 第 19 行） |

### 1.4 工具版本

| 项 | 值 | 出处 |
|---|---|---|
| Vivado | `Vivado v.2025.2 (win64) Build 6299465 Fri Nov 14 19:35:11 GMT 2025` | 三份 `*_utilization_placed.rpt` / `*_timing_summary_routed.rpt` 表头（`<LOCAL_EVIDENCE>`，三处一致） |
| 器件 / 封装 / 速度等级 | `xc7z020clg484-2`；时序报告 `7z020-clg484`，Speed File `-2 PRODUCTION 1.12 2019-11-22` | 同上 |
| PL/PS 工具链（文档口径） | Vivado / Vitis **2025.2** | [README](../../README.md)「硬件与工具版本」 |

## 2. 指标简表

> 每行 = 一个可引用的数值。条件列保留原文限定语；证据列给出可回查位置。所有指标都没有在本副本内重算。

| # | 指标 | 数值（照抄） | 测试/构建条件 | 版本/候选 | 证据路径 | 状态 |
|---:|---|---|---|---|---|---|
| 1 | 合并 Overlay 资源 | Slice LUTs **28745（54.03%）**；Slice Registers **22620（21.26%）**；Block RAM Tile **18.5（13.21%）**（RAMB36=17、RAMB18=3）；DSP48E1 **104（47.27%）** | `report_utilization`，Fully Placed，xc7z020clg484-2 | 合并产品构建 2026-09-24（bit `76e242fe…`，交付 bit 载荷相同，见 §3.1） | `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_utilization_placed.rpt` | `已有` |
| 2 | 合并 Overlay 时序 | **WNS +0.213 ns / WHS +0.018 ns**；TNS 0.000 / THS 0.000；失败端点 **0 / 0**（setup 67128、hold 67128 端点）；WPWS +0.264 ns；"All user specified timing constraints are met." | `report_timing_summary`，Routed | 同上 | `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_timing_summary_routed.rpt` | `已有` |
| 3 | 合并 Overlay 时钟频率 | `clk_fpga_0` 50.000 MHz；`clk_fpga_1` 200.000 MHz；`data_clk` **239.981 MHz**（周期 4.167 ns）；`sys_clk` 50.000 MHz；`daclk0/daclk1` 50.000 MHz；`pclk_raw` 33.333 MHz | Clock Summary（同一报告） | 同上 | 同上 | `已有` |
| 4 | 合并 Overlay 功耗 | Total On-Chip Power **2.418 W**（Dynamic 2.247 W + Device Static 0.171 W）；Tj 52.9 ℃；Max Ambient 57.1 ℃；预算 Unspecified | `report_power`，routed，commercial/typical/Production | 同上 | `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_power_routed.rpt` | `已有`（无功耗预算，见缺口 G12） |
| 5 | 合并 Overlay 实现状态 | 路由错误 **0**（51186 条可布通网全布通）；bitgen 成功；DRC routed **0 Errors**（`runme.log`：`DRC finished with 0 Errors, 42 Warnings`）；报告 Methodology 段另列 `TIMING-18 Missing input or output delay 37` 等（见 §3.1） | `route_status` / `runme.log` / `report_drc` | 同上 | `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_route_status.rpt`、`host_bd_wrapper_drc_routed.rpt`、`runme.log` | `已有` |
| 6 | data_clk 域时序 | WNS **+0.277 ns** / WHS **+0.066 ns**（783 端点）；`clk_fpga_0` +6.699/+0.018（49449）；`sys_clk` +0.213/+0.092（12728） | Intra-Clock Table（同 #2 报告） | 同上 | 同 #2 | `已有` |
| 7 | RF DDR 接口时序覆盖缺口 | `check_timing`（routed DCP）：**9** 个输入端口无 input delay（其中 RF 串行 7 个）、**48** 个输出端口无 output delay（其中 RF 串行 8 个）；RF lane1 输入路径 `slack=inf`、`Path Group=(none)` | 读取 `host_bd_wrapper_routed.dcp`（Vivado 2025.2，只读）；未重新综合 | 安装候选 `6384a07e…` 时代（QAM64 线，2026-10-26），约束文件与交付 bit 同源；**该缺口对交付 bit 同样成立**（见 §3.5 说明） | `<LOCAL_EVIDENCE>/rf-teaching/qam64-lane1-timing-2026-10-26/CHAIN-TIMING.json` + `REPORT.md` | `已有`（缺口已确认） |
| 8 | 独立 RF Overlay 资源 | Slice LUTs **3294（6.19%）**；Slice Registers **4369（4.11%）**；Block RAM Tile **8（5.71%）**；DSP **0**；Bonded IOB 45（22.50%）；IDELAYE2 7、ILOGIC 7、OLOGIC 8 | impl_1，Fully Placed | 独立 RF 构建 2026-09-19（`rf_overlay.bit` sha256 `b6a73e4d…`） | `<LOCAL_EVIDENCE>/rf_overlay/vivado/rf_overlay/rf_overlay.runs/impl_1/rf_bd_wrapper_utilization_placed.rpt` | `已有`（非交付 bit） |
| 9 | 独立 RF Overlay 时序/时钟 | **WNS +0.193 ns / WHS +0.043 ns**；TNS/THS 0.000；失败端点 0/0（10740 端点）；`clk_fpga_0` 50 MHz、`clk_fpga_1` 200 MHz、`data_clk` 239.981 MHz | Routed | 2026-09-19 构建 | `<LOCAL_EVIDENCE>/rf_overlay/…/rf_bd_wrapper_timing_summary_routed.rpt` | `已有` |
| 10 | 板载验证 RF Overlay 时序 | **WNS = 12.359 ns / WHS = 0.056 ns**；TNS = 0.000 / THS = 0.000（0 setup / 0 hold 违例）；FCLK0/FCLK1 = **50.000 / 200.000 MHz**（读 SLCR 分频器） | 上板记录；overlay `rf_overlay.bit` sha256 `4bc8ba11…`，本机构建 2026-09-17 17:58:44，板载同文件 | 独立 RF overlay（板级 L2 Stage 1，2026-09-18） | 仓库：[`../rf/l2_stage1_record.md`](../rf/l2_stage1_record.md) | `已有`（历史板级记录；与 09-19 重建报告不同源，见 §3.2 / §5） |
| 11 | 独立 PYNQ Overlay 资源/时序 | LUT **3291（6.19%）**；FF **3156（2.97%）**；BRAM **0**；DSP **0**；**WNS +11.549 ns / WHS +0.036 ns**（0/0 失败端点，15535 端点）；`clk50m` 50 MHz、`pclk_raw` 33.333 MHz、`clk_fpga_0` 50 MHz | impl_1，Fully Placed / Routed；构建日志 `0 Errors、0 Warnings、0 Critical Warnings` | 独立 PYNQ 构建 2026-09-13（`ledbtn.bit` sha256 `07dcfb52…`） | `<LOCAL_EVIDENCE>/pynq_overlay/vivado/ledbtn/ledbtn.runs/impl_1/{ledbtn_bd_wrapper_utilization_placed,ledbtn_bd_wrapper_timing_summary_routed}.rpt`；构建日志 `<LOCAL_EVIDENCE>/pynq_overlay/build.log`（未入库） | `已有`（非交付 bit） |
| 12 | 板端实测 DATA_CLK | **120.0 MHz**（真值；固件报 `20001220 Hz`/`20.000 MHz` 是 16 位窗计数器折回，已证伪）；`fs = 60 MSPS = DATA_CLK/2`（与驱动回读 `60000000 Hz` 独立吻合） | 1R1T，LO 80 MHz、60 MSPS（2026-09-18） | 独立 RF overlay 板级记录 | 仓库：[`../rf/l2_stage1_record.md`](../rf/l2_stage1_record.md)「Step 6 / 判据 3.2」 | `已有`（含观测通道缺陷记录） |
| 13 | ARM 完整计算 P95 / RSS（历史候选） | **278.835 ms / 31704 KiB**（b82，2026-10-12）；**258.6409 ms / 31428 KiB**（399b，2026-10-15）；**272.785 ms / 31728 KiB**（aba80e92，2026-10-16）；门槛 P95 ≤ 300 ms、RSS 记录预算 64 MiB | 板端隔离 ARM，100 次完整计算（含原生 CNN） | 历史候选，**均非当前交付版本** | 仓库：[证据索引](../memory/05-evidence-index.md)；[G2 状态报告](../reports/2026-10-17-headroom-g1-g4-status.md)、[2026-10-15 报告](../reports/2026-10-15-s2-parent-upgrade-review-and-execution-plan.md) | `非当前版本`（不得移植） |
| 14 | 交付 12 帧选用集误码（参考比对） | BPSK 两 ON 均 **0/138**；QPSK 两 ON 均 **0/128**；QAM16 分别 **1/256** 与 **0/256**；QAM64 移除相关负例 **9/9** 通过 | 有限帧冒烟 + 离线反例 | 当前交付口径（2026-10-26） | 仓库：[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) | `部分`（有限帧；不是整体 BER 资格） |
| 15 | QPSK 5 帧有限回放 | 5 帧均 **QI 显式、sync=true、BER 0/128**；独立参考一致 | 真实开发帧回放（不重新采集） | 2026-10-23 开发版 | 仓库：[QPSK 报告](../reports/2026-10-23-p3-qpsk-offline.md) | `部分`（开发回放，非独立 TEST 集） |
| 16 | 主机/ARM 判等 | 33/33 一致、浮点最大差 **1.82e-12**（容差 1e-6）；33/33 重放最大差 **1.07e-14**；870 帧 logit 最大绝对差 **9.155e-5**（≤1e-4）；`parity_mismatches: []` | 同 IQ / 同输入，ARM 板端 vs 主机 | 历史候选（2026-10-03～10-16） | 仓库：[BPSK 接收验收](../reports/2026-10-03-bpsk-receiver-acceptance.md)、[M4 审计](../reports/2026-10-03-bpsk-ui-progress-audit.md)、[生产审核](../reports/2026-10-03-production-board-continuation-audit.md)、[证据索引](../memory/05-evidence-index.md) | `部分`（历史候选，不移植） |
| 17 | 发射频谱实测（示波器） | 三帧下/上边带 **78.112 / 81.888 MHz**（目标 78.125/81.875，误差 **±13 kHz**）；12 个"通道×帧×边带"检验全过；削波比例 **0** | LO 80 MHz、Fs 60 MSPS；CH1/HK-50、CH2 直连；前后 OFF 对照 | 2026-09-30 双 TX 记录（AD9361 TX） | 仓库：[双 TX 记录](../reports/2026-09-30-dual-tx-scope-and-touch.md) | `部分`（仪器读数，未去嵌频响） |
| 18 | 环境诊断标定 | **VAL 10/10 CLEAR**；3 个 OFF **零误判 CLEAR**；normal 无 INVALID；拟合来源仅 20 帧 TRAIN | 2026-10-18 标定会话 | `trial`/开发阶段（非交付候选） | 仓库：[2026-10-18 报告](../reports/2026-10-18-codex-s2-takeover-completion.md) | `部分` |
| 19 | 辅助 AWG 历史基线 | `test_bridge.py COM11` **230 PASS**；`scope_verify.py COM11` **61/61 PASS**；位流 md5 `97e4da80…`（2026-09-12 14:14） | 历史 AWG（ACM2108）独立链，与 RF 产品无关 | 2026-09-13/14 基线冻结 | 仓库：[golden/BASELINE.md](../golden/BASELINE.md) | `非当前版本`（辅助链历史数据，无资源/时序报告） |
| 20 | 交付 bit 构建清单（仓库内可核对项） | 位流 `host_bd_wrapper.bit` sha256 `76e242fe…`；part `xc7z020clg484-2`；`built_at 2026-09-24 10:19:29`；WNS 0.213 / WHS 0.018；`toolchain.vivado: null` | `scripts/build_bitstream.py` 产出清单 | 本地构建（与交付 bit 载荷相同，见 §3.1） | 仓库：[`build_manifest.json`](../../build_manifest.json) | `已有`（工具版本字段为空，见缺口 G11） |

## 3. 综合与实现结果（按目标分列）

> 目标顺序：**§3.1 合并产品 Overlay（交付 bit / HWH 所属目标）→ §3.2 独立 RF Overlay → §3.3 独立 PYNQ Overlay → §3.4 辅助 AWG → §3.5 外部 RF DDR 接口缺口**。每个目标都给出资源、时序/时钟、工具版本与报告来源；没有原件的写"未取得"。

### 3.1 合并产品 Overlay（交付 bit 的可编程载荷）

**绑定链（为什么 2026-09-24 的报告能描述交付 bit）**

| 项 | 值 | 证据 |
|---|---|---|
| 交付 bit 文件 sha256 | `d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555` | `<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/candidate/merged_release_manifest.json`；仓库：[`build_manifest.json`](../../build_manifest.json) 同链承诺的本地构建为 `76e242fe…` |
| 交付 HWH 摘要 | `awg_merged.hwh` sha256 `ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0`；地址契约（`axi_ctrl_0 0x43000000`、`axi_bram_ctrl_rx 0x43040000` 等）见同一发布清单 | 同上（发布清单） |
| 本地同次构建 bit sha256 | `76e242fe0438e1bec5596ef93be256f0532a53968c991b103bd710c89f08e1bb` | `build_manifest.json`；本地原件 `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper.bit` |
| 二者关系 | **可编程载荷逐字节相同**（`payload_equal: true`，两侧 sync 偏移均为 175，payload sha256 `0ce74071ee4f4ee625539a19ef3eef124f89aeeddb09c5ac68e157451f4ffb5e`）；文件级 sha 不同，差异在可编程载荷之外（bit 文件头） | `<LOCAL_EVIDENCE>/codex-frame-idelay-check-20261025/verification.json` |
| 报告对应的 routed DCP | `host_bd_wrapper_routed.dcp` sha256 `63f679bcce815ba6c883f2e47b286dfc35a549ae9d040eba028548bd2be7c305`（同次构建） | 同上 |

> 该绑定只说明"报告对应的设计 = 交付 bit 的可编程内容"；它**不**说明报告覆盖了外部 RF 接口（见 §3.5）。

**资源占用**（`host_bd_wrapper_utilization_placed.rpt`，2026-09-24 10:03:31，Fully Placed）

| 资源 | Used | Available | Util% |
|---|---:|---:|---:|
| Slice LUTs | 28745 | 53200 | 54.03 |
| · LUT as Logic | 26187 | 53200 | 49.22 |
| · LUT as Memory | 2558 | 17400 | 14.70 |
| Slice Registers | 22620 | 106400 | 21.26 |
| · Register as Flip Flop | 22606 | 106400 | 21.25 |
| · Register as Latch | 14 | 106400 | 0.01 |
| Block RAM Tile | 18.5 | 140 | 13.21 |
| · RAMB36/FIFO | 17 | 140 | 12.14 |
| · RAMB18 | 3 | 280 | 1.07 |
| DSPs (DSP48E1) | 104 | 220 | 47.27 |
| Bonded IOB | 96 | 200 | 48.00 |
| BUFGCTRL | 6 | 32 | 18.75 |
| MMCME2_ADV | 1 | 4 | 25.00 |
| IDELAYE2 / ILOGIC / OLOGIC | 7 / 7 / 8 | 200 / 200 / 200 | 3.50 / 3.50 / 4.00 |

补充：`FDRE 17725`、`FDCE 4149`、`FDSE 647`、`FDPE 85`、`LDCE 14`；`CARRY4 2459`；`RAMB36E1 17`、`RAMB18E1 3`；`DSP48E1 104`；`IBUFDS 8`、`OBUFDS 8`、`IDDR 7`、`ODDR 8`、`IDELAYE2 7`。

**综合阶段报告**：同次运行另有 `synth_1/host_bd_wrapper_utilization_synth.rpt`（2026-09-24 09:52:20，`Design State: Synthesized`），记录 Slice LUTs 15796（29.69%）、Slice Registers 3846（3.61%）、Block RAM Tile 1（RAMB18=2）、DSP 104（47.27%）、Bonded IOB 64（32.00%）。该报告是**综合阶段中间结果**，与 placed 报告的统计范围不同（独立 RF/PYNQ 的 synth 报告只记 0 LUT/0 FF，说明 BD 内部逻辑在该阶段未计入）；**本文资源结论以 placed 报告为准，两阶段数字不直接对比**。

**时序与时钟**（`host_bd_wrapper_timing_summary_routed.rpt`，2026-09-24 10:09:43，Routed）

| 范围 | WNS (ns) | TNS (ns) | 失败端点 | 端点总数 | WHS (ns) | THS (ns) | 失败端点 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全局 | **0.213** | 0.000 | 0 | 67128 | **0.018** | 0.000 | 0 |
| `clk_fpga_0`（50 MHz） | 6.699 | 0.000 | 0 | 49449 | 0.018 | 0.000 | 0 |
| `data_clk`（239.981 MHz） | 0.277 | 0.000 | 0 | 783 | 0.066 | 0.000 | 0 |
| `sys_clk`（50 MHz） | 0.213 | 0.000 | 0 | 12728 | 0.092 | 0.000 | 0 |

- 全局 `WPWS(ns) 0.264` / `TPWS(ns) 0.000`，脉冲宽度失败端点 0（25516）。
- 时钟表：`clk_fpga_0` 50.000 MHz、`clk_fpga_1` 200.000 MHz、`data_clk` 239.981 MHz（4.167 ns）、`sys_clk` 50.000 MHz（含派生 `clkfb_raw`/`daclk0`/`daclk1` 50.000 MHz、`pclk_raw` 33.333/30.000 ns）。
- 报告结论行：`All user specified timing constraints are met.`
- 实现质量：报告 Methodology 段列出 `TIMING-18 Missing input or output delay 37`、`TIMING-6/7 各 1`、`TIMING-9/10 各 1`、`TIMING-20 14`、`TIMING-24 3`、`DPIR-1 2916`、`LUTAR-1 28` 等告警；**这些是告警不是错误**，但与 §3.5 的接口未约束缺口一致。

**功耗 / 路由 / DRC / bitgen**（同一次实现运行）

| 项 | 值 |
|---|---|
| 总片上功耗 | 2.418 W（Dynamic 2.247 W + Static 0.171 W）；Tj 52.9 ℃；Max Ambient 57.1 ℃；`Effective TJA 11.5 C/W`；预算 `Unspecified` |
| 路由 | 逻辑网 78836；可布通网 51186，全布通 51186；**路由错误 0** |
| DRC routed | **0 Errors**（告警：`DPIP-1 206`、`DPOP-1 76`、`DPOP-2 96`、`PDRC-153 14`、`RBOR-1 6`、`REQP-1839/1840 各 20`、`RTSTAT-10 1`；Advisory `REQP-181 1`） |
| bitgen | `Bitgen Completed Successfully`；`DRC finished with 0 Errors`；`write_bitstream completed successfully` |

**工具与时间**：Vivado v.2025.2 (win64) Build 6299465；报告日期 2026-09-24（本机时钟）；设备 `xc7z020clg484-2`。

**结论与限定**：
- 内部时序、路由、DRC、bitgen 均为 `已有` 且通过；资源记录完整（LUT/FF/BRAM/DSP/时钟资源/IO 原语）。
- **该报告不覆盖外部 RF 源同步接口**（无 `set_input_delay` / `set_output_delay`，见 §3.5），也不能证明 RX 采样裕量或 TX 输出眼图。
- 交付 bit 与本地构建 bit 的**文件级 sha 不同**，但**可编程载荷相同**（§3.1 绑定链）。若后续更换 bit，本节结论需重测。

### 3.2 独立 RF Overlay（`rf_overlay/build.tcl` → `rf_overlay.bit`）

| 项 | 值 | 出处 |
|---|---|---|
| 最近实现运行 | impl_1，2026-09-19 14:25–14:28；Vivado v.2025.2 Build 6299465；`rf_bd_wrapper`；综合阶段报告 `synth_1/rf_bd_wrapper_utilization_synth.rpt`（`Synthesized`，该阶段报 0 LUT / 0 FF，资源以 placed 为准） | `<LOCAL_EVIDENCE>/rf_overlay/vivado/rf_overlay/rf_overlay.runs/{impl_1,synth_1}/` |
| 资源 | LUT 3294（6.19%）、LUT as Logic 3092、LUT as Memory 202；FF 4369（4.11%）；BRAM 8（5.71%，全部 RAMB36）；DSP **0**；IOB 45（22.50%）；BUFG 3；MMCM 0；IDELAYE2/ILOGIC/OLOGIC = 7/7/8 | `rf_bd_wrapper_utilization_placed.rpt` |
| 时序 | WNS **+0.193 ns**、TNS 0.000、失败端点 0/10740；WHS **+0.043 ns**、THS 0.000；WPWS 0.264；`All user specified timing constraints are met.` | `rf_bd_wrapper_timing_summary_routed.rpt` |
| 时钟 | `clk_fpga_0` 50.000 MHz、`clk_fpga_1` 200.000 MHz、`data_clk` 239.981 MHz | 同上 |
| 功耗 | 报告存在（`rf_bd_wrapper_power_routed.rpt`），本次未逐项摘录（未取得明确预算） | 同上目录 |
| 路由/DRC | 路由错误 0（6914 条全布通）；DRC routed 0 Errors（`RBOR-1 4`、`REQP-1839 20`、`RTSTAT-10 1`） | `rf_bd_wrapper_route_status.rpt` / `rf_bd_wrapper_drc_routed.rpt` |
| 产物 bit | `rf_overlay.bit` sha256 `b6a73e4de60169ba5d5e998d521ccc15f5e7da5922e9bc80dc1d0004744435d1`（2026-09-19 14:28:10 重建） | 本地原件 |

**历史板级记录（不同构建，不可与上表硬配成"优化前后"）**：2026-09-17 17:58:44 构建的 `rf_overlay.bit`（sha256 `4bc8ba11…`）板载验证，WNS 12.359 ns / WHS 0.056 ns、FCLK0/FCLK1 50/200 MHz、AD9361 init 返回 0、8 PASS/0 FAIL/2 RECORD——见仓库 [`../rf/l2_stage1_record.md`](../rf/l2_stage1_record.md)。它与 2026-09-19 的重建**不是同一文件、也不是同一版报告**（构建日志为 `build_samplepath*`；09-17 记录没有对应的 `.rpt` 留存在本次可检索范围内），因此 WNS 12.359 → 0.193 不能当作优化或退化结论（见 §5）。

### 3.3 独立 PYNQ Overlay（`pynq_overlay/build.tcl` → `ledbtn.bit`）

| 项 | 值 | 出处 |
|---|---|---|
| 最近实现运行 | impl_1，2026-09-13 20:00–20:03；Vivado v.2025.2 Build 6299465；`ledbtn_bd_wrapper`；综合阶段报告 `synth_1/ledbtn_bd_wrapper_utilization_synth.rpt`（`Synthesized`，该阶段报 0 LUT / 0 FF，资源以 placed 为准） | `<LOCAL_EVIDENCE>/pynq_overlay/vivado/ledbtn/ledbtn.runs/{impl_1,synth_1}/` |
| 资源 | LUT 3291（6.19%）、Logic 2103、Memory 1188；FF 3156（2.97%）；BRAM **0**；DSP **0**；IOB 33（16.50%）；BUFG 3；MMCM 1（25%） | `ledbtn_bd_wrapper_utilization_placed.rpt` |
| 时序 | WNS **+11.549 ns**、TNS 0.000、失败端点 0/15535；WHS **+0.036 ns**、THS 0.000；WPWS 7.000；`All user specified timing constraints are met.` | `ledbtn_bd_wrapper_timing_summary_routed.rpt` |
| 时钟 | `clk50m` 50.000 MHz（含 `clkfb_raw`）、`pclk_raw` 33.333 MHz、`clk_fpga_0` 50.000 MHz | 同上 |
| 构建日志 | `139 Infos, 0 Warnings, 0 Critical Warnings and 0 Errors encountered`；`Bitgen Completed Successfully` | `<LOCAL_EVIDENCE>/pynq_overlay/build.log`（构建日志，未入库） |
| 产物 bit | `ledbtn.bit` sha256 `07dcfb52f2cde2c4ac8ef7a711f5a58567bc21b4ed4bd40e9108a9f49d578894`（2026-09-13 20:03:07） | 本地原件 |

### 3.4 辅助 AWG

- 本副本的合并 BD 构建脚本 `scripts/create_bd.tcl` 以 BD module 方式例化 `awg_top_0`（仓库内可核对），因此 AWG 逻辑计入合并产品 Overlay 的资源总量；**没有独立的 AWG 综合/实现报告可引用**。
- 旧 ACM2108 独立链的位流与 `utilization`/`timing` 报告**不在本副本**（`awg/` 目录未随仓库复制），仅有历史验收数据（见 §2 第 19 行）与历史说明 [awg-legacy-guide.md](awg-legacy-guide.md)：状态 `未取得`。

### 3.5 外部 RF DDR 接口的时序覆盖缺口（对交付 bit 同样成立）

> 该分析来自 QAM64 线的只读复核（2026-10-26）。**QAM64 已移出交付，QAM64 的结论不作为当前交付结果**；但被复核的对象是**交付 bit 对应的同一 routed DCP 与同一份约束**，因此"接口未约束"这一缺口本身对交付 bit 成立。

| 检查 | 结果 |
|---|---|
| 内部路由时序 | WNS **+0.213 ns** / WHS **+0.018 ns**（与 §3.1 一致），`All user specified timing constraints are met.` |
| `data_clk` 域 | WNS **+0.277 ns**（WHS 见口径差异说明） |
| RX lane1 输入路径（现有 DCP） | `slack=inf`、`Path Group=(none)`、数据路径 1.620 ns（IBUFDS 0.940 + IDELAYE2 0.680） |
| TX lane1 输出路径（现有 DCP） | `slack=inf`、`Path Group=(none)` |
| `check_timing`（实际 DCP） | **9** 个输入端口无 input delay；**48** 个输出端口无 output delay（旧报告全局计数为 9/39，不可把差值归给 RF） |
| RF 输入子集 | 7 个：`rx_data_in_p[0..5]`、`rx_frame_in_p` |
| RF 输出子集 | 8 个：`tx_clk_out_p`、`tx_data_out_p[0..5]`、`tx_frame_out_p` |
| 例外 | RF 串行数据/帧/时钟上无 false path；仅 `rf_gpio[*]` 与 `spi_miso` |
| `rf_overlay/constraints/rf.xdc` | 有 240 MHz recovered data_clk 与管脚/电平约束，**无 `set_input_delay`/`set_output_delay`** |
| 试加约束的只读试验 | 板级 skew 置 0、runtime tap 未建模时 WNS **-5.240 / WHS -2.879**；报告明确**不据此作物理结论**（verdict `REJECTED_AS_QUALIFICATION_MODEL_NOT_A_PHYSICAL_FAILURE_CLAIM`） |

**口径差异（如实记录，未核对）**：同一证据的 `CHAIN-TIMING.json` 记 `data_clk_WHS_ns = 0.349`，而 routed 报告的 Intra-Clock Table 为 **0.066**；本文 §2 采用报告表值（0.066）。

**判断依据（可复核）**：真实约束文件无 I/O delay + `check_timing` 计数 + lane1 输入/输出路径 `slack=inf` 三者一致；因此"内部 WNS 为正"不能证明外部 DDR 接口满足 AD9361 采样/输出要求，也**不能**证明接口失效（板级 skew、AD9361 tDDRX 等未表征）。参考模型与器件手册取值（`tDDRX/tDDV = 0.25..1.25 ns`、`tSTX/tHTX = 1/0 ns`、`tCP ≥ 4.069 ns`）见 `<LOCAL_EVIDENCE>/rf-teaching/qam64-lane1-timing-2026-10-26/CHAIN-TIMING.json`。

## 4. 上板与性能实测

> 本节只列已有记录；每条都带候选/日期与限定语。历史候选的数字不得移植到当前交付版本。

### 4.1 ARM 完整计算（P95 / RSS）

| 候选（日期） | 100 次完整计算 P95 | RSS | 判等 | 出处 | 状态 |
|---|---:|---:|---|---|---|
| `b82b141e…`（2026-10-12） | **278.835 ms**（≤300 ms） | **31704 KiB**（≤64 MiB） | 四窗 Host/ARM `parity_mismatches=[]`；原生 CNN；exit 0 | 仓库：[headroom-agent-handoff](../reports/2026-10-12-headroom-agent-handoff.md)；[G1/G4 状态](../reports/2026-10-17-headroom-g1-g4-status.md) | `非当前版本` |
| `399b300e…`（2026-10-15，隔离 ARM 资格） | **258.6409 ms** | **31428 KiB** | release/software 44/44 相符；同帧差异空 | 仓库：[S2 部署复核报告](../reports/2026-10-15-s2-parent-upgrade-review-and-execution-plan.md)；[证据索引](../memory/05-evidence-index.md) | `非当前版本` |
| `aba80e92…`（2026-10-16，G2） | **272.785 ms** | **31728 KiB**（≤65536） | `parity/chain parity []`；`new_rf_frames=0` | 仓库：[G1/G4 状态](../reports/2026-10-17-headroom-g1-g4-status.md)；[证据索引](../memory/05-evidence-index.md) | `非当前版本` |
| 当前交付口径（2026-10-26 后） | **未取得** | **未取得** | — | — | `未取得` |

- 门槛：**P95 ≤ 300 ms**（历史候选的资格门槛）；RSS 记录预算 64 MiB（65536 KiB），并非所有阶段都强制。
- 早期失败记录保留（不得挑选）：`314.963 ms` 首次 ARM 失败（2026-10-11）、`290.805 ms`（adfac1f 复核重算）、`281.68 ms`/`30476 KiB`、`287.87 ms`/`30076 KiB`、`292.52 ms`/`30276 KiB`、`294.90 ms`/`30376 KiB` 等——见 [CLAUDE.md](../../CLAUDE.md) 顶部历史段与各报告；这些数字属于各自候选/日期。
- **不构成**"优化前后"对照（见 §5）。

### 4.2 板端接收/发射实测与参考比对

| 项 | 实测 | 参考/判据 | 日期/条件 | 出处 |
|---|---|---|---|---|
| L2 Stage 1（AD9361 TX bring-up，1R1T） | **8 PASS / 0 FAIL / 0 NOT-APPLICABLE / 2 RECORD-ONLY**；C1：LO 80.000 MHz、边带 78.128/81.872 MHz（对称偏差 0.0 kHz）；C2：Δf 1872.0→3744.0 kHz，比值 2.0000；C5：衰减 10000→30000 差 **19.85 dB**；287 寄存器回读 0 读失败 | 测量前冻结判据；期望 LO 80 MHz、边带 ±1.875 MHz、比值 2.00±2%、衰减差 20±3 dB | 2026-09-18，`rf_overlay.bit` sha `4bc8ba11…`，LO 80 MHz / 60 MSPS | 仓库：[`../rf/l2_stage1_record.md`](../rf/l2_stage1_record.md) |
| 交付 12 帧选用集 | BPSK 0/138（两 ON）、QPSK 0/128（两 ON）、QAM16 1/256 与 0/256、6 个 OFF 均保存对应模式结果、QAM64 移除负例 9/9 | 参考 PRBS7 独立计算 BER | 2026-10-26，有限帧冒烟 | 仓库：[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| QPSK/RECT 有限板上验证 | 5 帧 QI 显式、`sync=true`、BER **0/128**；另有 6 帧真实解调冒烟（其中 QPSK seed39/40/41 均 `sync=true`、`valid_symbols=127`、BER 0/128、QI；BPSK seed6 单列 0/138） | 参考模式 127 符号/2540 样点，后 64 符号/128 bits 计 BER | 2026-10-23 | 仓库：[QPSK 报告](../reports/2026-10-23-p3-qpsk-offline.md) |
| 双 TX 示波器实测 | CH1/CH2 三帧边带 78.112/81.888 MHz（误差 ±13 kHz）；12/12 检验通过；削波 0；Vpp 0.385930/0.393970 V（CH1 带 HK-50）、0.739698 V（CH2 直连） | 判据：频率误差 ≤50 kHz、边带高于局部中位底噪 ≥14 dB、ON 比 OFF ≥10 dB、三帧重复、无削波、源选中 | 2026-09-30，LO 80 MHz / 60 MSPS / 2R2T 配置记录 | 仓库：[双 TX 记录](../reports/2026-09-30-dual-tx-scope-and-touch.md) |
| 环境诊断标定 | VAL 10/10 CLEAR、3 OFF 零误判 CLEAR、normal 无 INVALID；拟合来源仅 20 帧 TRAIN | 独立 VAL/OFF 验证，TRAIN-only 拟合 | 2026-10-18 | 仓库：[2026-10-18 报告](../reports/2026-10-18-codex-s2-takeover-completion.md) |
| 试部署 + 自动回滚 | 注入退出 42 后自动恢复父版本；独立读回服务/RF 安全态/`boot_id`/存储正常 | runbook 退出码与读回判据 | 2026-10-17 | 仓库：[回滚收尾报告](../reports/2026-10-17-trial-rollback-board-closeout.md) |

### 4.3 主机 / ARM 判等（跨平台一致性）

| 对象 | 结果 | 条件 | 出处 |
|---|---|---|---|
| BPSK 完整接收（33 帧） | 33/33 一致；浮点最大差 **1.82e-12**（容差 1e-6） | 33 个不同 IQ hash；状态/偏移/判决/分子分母一致 | [BPSK 接收验收](../reports/2026-10-03-bpsk-receiver-acceptance.md) |
| M4 同输入重放（33 帧） | 33/33 通过；最大浮点差 **1.07e-14** | 板端记录 vs 主机回放 | [M4 审计](../reports/2026-10-03-bpsk-ui-progress-audit.md) |
| 模型 logits（870 帧） | 最大绝对差 **9.155e-5**（≤1e-4） | ARM numpy vs Torch 导出 | [生产审核](../reports/2026-10-03-production-board-continuation-audit.md) |
| 环境分析四窗 + 8 场景/96 证据 | `parity_mismatches=[]` / 差异空 | G1/G2 隔离 ARM 资格 | [证据索引](../memory/05-evidence-index.md) |

### 4.4 失败与限制（必须保留，不得改写为通过）

| 项 | 状态/说明 | 出处 |
|---|---|---|
| QAM16 非零误码 | 12 帧选用集中 1/256；受限演示，不倒填成零误码验收 | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| 完整交付闭环 | 复审 `CHANGES_REQUIRED`（验收语义漂移、试部署重派超预算、文档与日志不一致） | 同上 |
| QAM64 | 已移出本轮交付；历史 QAM64 时序/资源报告不得作为当前交付结果 | [README](../../README.md) |
| 外部 SDR BPSK 历史统计 | 23/30 帧同步，整体未通过 | [README](../../README.md) |
| 外部 SDR RF 输出 | 未验证（示波器带宽不足） | [SDR 测试报告](../../sdr_testbench/TEST-REPORT.md) |
| S2 正式 108 物理帧 + 12 离线负控 | `NOT_RUN` | [验收清单](../requirements/acceptance-checklists-p1-p6.md) |
| 46 dB HEADROOM 真实研究 | `NOT_RUN`（曾被 `lease_held` 谓词阻塞） | [未决问题](../memory/06-open-questions.md) |
| EVM | `NOT_IMPLEMENTED` | [作品简介追踪表](../requirements/brief-traceability.md) |
| PL 实时多制式处理 | PL 未做实时同步解调/RRC/多制式处理；PS 侧只测过完整计算路径 | [README](../../README.md) |
| 触摸时延 | `NOT_MEASURED` | [作品简介追踪表](../requirements/brief-traceability.md) |
| 优化前后对比 | `未取得`（无同口径基线，见 §5；历史候选受控 A/B 见 [OPTIMIZATION-AND-REPRODUCTION](OPTIMIZATION-AND-REPRODUCTION.md) §1.4，非当前版本） | 本文 |

## 5. 优化前后对照（3.3.4）

### 5.1 结论

**未取得（无同口径优化前后基线）。**

原因（判断依据，可复核）：

1. 现有综合/实现报告分属**不同设计目标**（合并产品 / 独立 RF / 独立 PYNQ）与**不同日期**（2026-09-13 / 09-19 / 09-24），不是同一设计的两个版本，**不能配对**。
2. 合并产品 Overlay 只有**一次**实现的完整报告；没有同目标、同约束的"优化前"报告（旧报告目录 `awg/` 不在本副本，且历史 bit 与当前 bit 不同源）。
3. ARM P95/RSS 有多个候选记录，但这是**不同候选、不同软件版本、不同日期**的合格性测量，不是受控 A/B；把它们排成"优化前后"会把版本差异冒充优化收益。
4. 独立 RF Overlay 的 2026-09-17 板级记录（WNS 12.359 ns，位流 sha `4bc8ba11…`）与 2026-09-19 重建报告（WNS 0.193 ns，位流 sha `b6a73e4d…`）之间**位流文件、报告与构建均不同源**（09-19 构建日志为 `build_samplepath*`），两版不是同一 RTL/约束的前后对照。
5. 因此，按指南 3.3.4 要求"优化前后的性能与资源对比表"这一项：**现有材料不足以填写**；赛前需按 §6 生成同口径基线。

> **补充（2026-10-31，不改上述结论）**：按 [OPTIMIZATION-AND-REPRODUCTION.md](OPTIMIZATION-AND-REPRODUCTION.md) §1.4，历史候选 `b3cca2ac…` 存在 **1 组同版本受控 A/B**（modrec4 单次共享计算：ARM 五阶段 median 378.89→292.08 ms、P95 444.52→311.74 ms、modrec4 阶段 median 154.58→64.81 ms；80 samples；0 mismatch 为容差一致非逐字节）。该组**属于历史候选，不是当前交付版本**，不得移植为当前交付结果；当前交付版本仍为 `未取得`。当前版本的最小补测方案（指标/条件/验收/会话与构建次数）见该文 §1.6。

### 5.2 可引用但**不得**当作优化结论的近似数据（仅用于说明现状）

| 近似对照 | 数值 | 为什么不构成优化对照 |
|---|---|---|
| ARM P95 候选序列 | 278.835 → 258.6409 → 272.785 ms（b82 → 399b → aba80e92） | 三个不同候选/日期、合同不同（headroom vs environment），且 399b 是隔离资格、aba80e92 是 G2；无同一版本的"优化前" |
| 独立 RF Overlay 时序 | 12.359 ns（09-17 板级记录）vs 0.193 ns（09-19 重建报告） | 位流文件（`4bc8ba11…` vs `b6a73e4d…`）、构建与报告均不同源，不是同版本前后报告 |
| 合并目标资源占比 | LUT 54.03%、DSP 47.27%、BRAM 13.21% | 没有可比较的基线；不能判断"是否以显著资源代价换有限性能" |
| 门槛值 | P95 ≤ 300 ms、RSS ≤ 64 MiB、QAM16 零误码（原批准判据） | 门槛是判据，不是优化前后对比 |

### 5.3 判断依据应如何写（当前可执行版本）

- 判据先于测量：L2 Stage 1、12 帧冒烟、QPSK 5 帧、双 TX 示波器均记录了**测量前冻结判据**（见 §4.2 出处）。
- 失败保留：QAM16 1/256、外部 SDR 23/30、S2 `NOT_RUN` 等均如实保留（§4.4）。
- 资源与时序的"结论"只能是：**内部路径在 2026-09-24 构建上收敛且资源记录完整；外部 RF DDR 接口未建模，不能证明接口裕量**。

## 6. 缺口与后续需生成项（赛前清单）

> 优先级：P1 = 指南明确要求且当前缺失；P2 = 影响可信度/可复现；P3 = 可选增强。

| ID | 缺口 | 影响的指南条目 | 赛前需要做的事 | 现状 |
|---|---|---|---|---|
| G1 | 交付 bit 的**同源同次**综合/实现报告未归档为工程包证据（现有报告为 2026-09-24 同次构建，交付 bit 与其载荷相同、文件级 sha 不同） | 3.3.5.1 综合与实现报告 | 把现有 `utilization/timing/power/DRC/route` 报告 + routed DCP + payload 绑定收据（`verification.json`）作为附件列入工程包；或在允许时用交付源码/约束重跑一次并归档 | 报告已有，**归档动作未做**（P1） |
| G2 | RF 源同步 DDR 接口 **9 输入 / 48 输出无 I/O delay 约束**，7 个 RF 输入 + 8 个 RF 输出未建模 | 3.3.5.1 关键性能指标；3.3.4 判断依据 | 补 `set_input_delay`/`set_output_delay`（需板级走线/器件手册参数来源），重新综合实现并重出报告；再决定是否能宣称接口裕量 | `未取得`（P1） |
| G3 | **优化前后性能与资源对比表**缺失（无同口径基线） | 3.3.4 + 3.3.5.3 优化过程 | 固定一个设计基线版本，保留同口径 `utilization/timing` + 同一测量路径的性能数据，至少两版可配 | `未取得`（P1） |
| G4 | 当前交付口径下**没有 ARM P95/RSS 重测**（最后一次记录是历史候选） | 3.3.5.1 关键性能指标、上板实测 | 在板端跑一次 100 次完整计算并保存 timings/RSS（需一次性板卡授权，本轮禁止） | `未取得`（P1） |
| G5 | 板级 DATA_CLK/data/frame **skew 未表征**；`tx_clk_out` 生成时钟模型未定 | 3.3.5.1 时序指标完整性 | 引脚/示波器测量或引述器件手册+板级仿真；没有来源时明确标注未知，不填假值 | `未取得`（P1） |
| G6 | 外部 SDR/示波器 **RF 输出验证**未完成（仪器带宽不足） | 3.3.5.1 实测输出与参考比对 | 用带宽足够的示波器/频谱仪重测；或明确写"未验证"并给出限制 | `未取得`（P2） |
| G7 | S2 正式 **108 物理帧 + 12 离线负控** `NOT_RUN` | 3.3.5.1 实测输出来源 | 按冻结判据执行正式采集（需授权与接线确认） | `NOT_RUN`（P2） |
| G8 | **触摸时延** `NOT_MEASURED` | 3.3.5.1 关键性能指标（人机时延） | 用户已取消专门计时；如赛题要求可补一次触摸时延测量 | `NOT_MEASURED`（P3） |
| G9 | QAM16 非零误码（1/256） | 3.3.5.1 实测指标 | 保留为"受限演示"；若要宣称合格需独立 TEST 集与零误码门槛 | `部分`（P2） |
| G10 | 5 个第三方参考 RTL **许可未决**，影响可复现构建与再分发 | 3.3.4 可复现、3.3.3.3 开源 | 取得许可或替换实现；否则公开副本不能完整重建 PL | `NOT_RUN`（P1，法务/许可） |
| G11 | 报告口径不一致：`docs/rf/boardtest.md` 模板记 WNS 12.710/WHS 0.066（"2026-09-17 本构建"），`l2_stage1_record.md` 记同日期构建 WNS 12.359/WHS 0.056；`build_manifest.json` 的 `toolchain.vivado` 为 `null`；`CHAIN-TIMING.json` data_clk WHS 0.349 vs 报告 0.066 | 3.3.5.1 报告可信度 | 逐项回查原始报告/构建日志，统一为一份带出处的口径 | `部分`（P2） |
| G12 | 功耗报告无预算（`Design Power Budget (W): Unspecified`），且未与板级供电对照 | 3.3.5.1 关键性能指标 | 如需要，补功耗预算/实测对照；否则保留"估算、无预算"表述 | `部分`（P3） |
| G13 | 辅助 AWG 独立位流的综合/实现报告缺失（旧链 `awg/` 未随本副本） | 3.3.5.1（若把 AWG 作为独立作品提交） | 从原工程找回旧报告，或明确 AWG 只作为合并 BD 内模块、不单独声明 | `未取得`（P2） |

## 7. 引用与复现说明

### 7.1 如何回查本文数字

1. **先看仓库文档**：表格中"仓库"列给出相对路径；这些文档自身也声明"原始收据不在仓库，不能复算"。
2. **再回本地原件**：`<LOCAL_EVIDENCE>` 下按 §1.2 解引用；关键原件：
   - 交付 bit/HWH 与发布清单：`<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/candidate/merged_release_manifest.json`
   - 交付 bit 与同次构建 bit 的载荷绑定：`<LOCAL_EVIDENCE>/codex-frame-idelay-check-20261025/verification.json`
   - 合并目标报告：`<LOCAL_EVIDENCE>/awg/awg.runs/{impl_1,synth_1}/host_bd_wrapper_{utilization_placed,utilization_synth,timing_summary_routed,power_routed,drc_routed,route_status}.rpt`、`host_bd_wrapper_routed.dcp`、`runme.log`
   - 独立 RF 报告：`<LOCAL_EVIDENCE>/rf_overlay/vivado/rf_overlay/rf_overlay.runs/{impl_1,synth_1}/rf_bd_wrapper_*.rpt`
   - 独立 PYNQ 报告：`<LOCAL_EVIDENCE>/pynq_overlay/vivado/ledbtn/ledbtn.runs/{impl_1,synth_1}/ledbtn_bd_wrapper_*.rpt`
   - 接口时序复核：`<LOCAL_EVIDENCE>/rf-teaching/qam64-lane1-timing-2026-10-26/`
3. **核对顺序建议**：报告表头（工具版本/器件/Design State）→ 汇总表（WNS/WHS/资源）→ 与 bit 的绑定（sha/payload）→ 再看本条指标的限定语。

### 7.2 可复现构建（仓库内）

| 目标 | 命令 | 说明 |
|---|---|---|
| 合并产品 Overlay（AWG+LCD+RF） | `python scripts/build_bitstream.py` | 需 Vivado 2025.2、本仓库源码与 BD 脚本；产物写入 `awg/…`，本副本不含该目录 |
| 独立 PYNQ Overlay | `vivado -mode batch -source pynq_overlay/build.tcl` | 产物 `pynq_overlay/out/` |
| 独立 RF Overlay | `vivado -mode batch -source rf_overlay/build.tcl` | 产物 `rf_overlay/out/` |
| 发布打包/校验 | `scripts/package_merged_overlay.py`、`pynq_overlay/sw/merged_release.py` | 需要先有完成构建的 bit/HWH |

**上板工程与运行脚本（仓库内入口）**：

| 用途 | 入口 | 说明 |
|---|---|---|
| 板端 C 守护进程构建 | `make -C rf_overlay/sw/board_app` | 板端原生 gcc 9.3.0；主机侧 `make … host-test` |
| 发布安装 | `bash pynq_overlay/sw/install.sh` | 见 [部署 runbook](../rf/deployment-trial-runbook.md) |
| 状态读回 | `python scripts/rf_teaching_install_state.py` | 只读读回 current/active 与服务状态 |
| 试部署 + 回滚 | `sh scripts/rf_teaching_trial_install.sh` | 单次入口，终态见 runbook |
| 采集/验收工具 | `scripts/rf_capture_*`、`scripts/rf_bpsk_*`、`scripts/rf_qpsk_*` 等 | 见 [DELIVERY-INDEX](DELIVERY-INDEX.md) §3 |

> 这些脚本的"存在与入口"可在仓库核对；**本轮没有执行任何板端/硬件动作**。

**复现限制（必须写明）**：

- 本副本**不含**位流/HWH/原始报告/原始 IQ；旧摘要不能用本副本验证。
- 交付 bit（`d5bfe5d9…`）是**工作区构建**（2026-09-29 14:35），当时的 `create_bd.tcl`/`awg.xdc` 等为未提交修改，因此**不能保证从提交历史重建出完全一致的 bit**（见 [2026-09-30 积压分诊](../reports/2026-09-30-backlog-triage.md) §0）。重建后必须重新产出并归档本文件 §3 的报告。
- 5 个第三方参考 RTL 许可未确认（[LICENSES/UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)），公开副本的完整 PL 重建存在法务阻塞。
- 本文未重新综合、未上板、未补采；任何"可复现"结论都只指**命令与源码入口存在**，不指本轮验证过。

### 7.3 引用规则

- 引用指标必须同时给出：数值原文 + 条件/候选/日期 + 出处路径 + 证据层级（文档 / 本地原件 / 板端 / 真人）。
- 不得把 `NOT_RUN`、`NOT_MEASURED`、`NOT_IMPLEMENTED` 写成通过；不得把有限帧零误码写成整体误码率；不得把历史候选指标移植到当前交付版本。
- 本文与 [DELIVERY-INDEX.md](DELIVERY-INDEX.md) / [COMPETITION-MATERIALS.md](COMPETITION-MATERIALS.md) 的分工：DELIVERY-INDEX 是阅读地图与能力/限制索引，COMPETITION-MATERIALS 是与"作品简介"的对应，本文只做**综合/实现/实测指标**的集中整理与缺口清单。

## 8. 本文不证明什么

- 不证明外部 RF DDR 接口满足 AD9361 采样/输出时序（无 I/O delay 约束、无板级 skew）。
- 不证明当前交付版本在板端的性能（无 2026-10-26 之后的 ARM/整机重测）。
- 不证明资源使用"最优"或"合理"（无基线、无优化前后对照）。
- 不证明 QAM64 相关结论（已移出交付；其报告仅作历史记录）。
- 不证明本仓库可独立重建交付 bit（缺 bit/HWH、缺未入库历史修改、第三方 RTL 许可未决）。
