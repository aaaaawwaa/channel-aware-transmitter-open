# 性能证据摘要（一页）

维护日期：2026-10-31。用途：给评审/海报/PPT 一页可核对的性能与证据清单。**所有数值照抄原文、带条件与限定语**；证据层级用项目状态词（`OFFLINE_VERIFIED`/`ARM_VERIFIED`/`BOARD_VERIFIED`/`文档记载`/`估计值`/`未取得`/`NOT_RUN`/`NOT_MEASURED`）。完整背景见 [综合与实现指标](SYNTHESIS-METRICS.md)、[证据包](EVIDENCE-PACK.md)、[优化与复现](OPTIMIZATION-AND-REPRODUCTION.md) 与 [RF 接口时序](RF-INTERFACE-TIMING.md)。

> **口径**：当前交付版本 = 2026-10-26 交付复审口径（BPSK/QPSK 有限演示 + 受限 QAM16；**QAM64 已移出交付**）。历史候选、辅助 AWG、QAM64 指标**不得**移植为本表当前版本结果。原始报告/DCP/bit/HWH/原始 IQ **不在本仓库**，本表不能在本副本内复算。

## 1. 一页证据表

| # | 指标 | 数值（照抄） | 条件 / 版本 | 证据层级 | 出处 |
|---:|---|---|---|---|---|
| 1 | 合并产品 Overlay 资源 | Slice LUTs **28745（54.03%）**；Slice Registers **22620（21.26%）**；Block RAM Tile **18.5（13.21%）**；DSP48E1 **104（47.27%）** | `report_utilization`，Fully Placed，xc7z020clg484-2；2026-09-24 构建（交付 bit 载荷相同） | 本地原件（Vivado 报告） | [SYNTHESIS-METRICS §3.1](SYNTHESIS-METRICS.md)；[EVIDENCE-PACK §2 行1](EVIDENCE-PACK.md)；[merged 摘要 §2](../evidence/merged-overlay-2026-09-24.md) |
| 2 | 合并产品 Overlay 时序 | **WNS +0.213 ns / WHS +0.018 ns**；TNS/THS 0.000；失败端点 **0/0**（67128 端点）；`All user specified timing constraints are met.` | `report_timing_summary`，Routed；同上构建 | 本地原件（Vivado 报告） | [merged 摘要 §3](../evidence/merged-overlay-2026-09-24.md) |
| 3 | 合并产品 Overlay 时钟 | `clk_fpga_0` 50.000 MHz、`clk_fpga_1` 200.000 MHz、`data_clk` **239.981 MHz**（4.167 ns）、`sys_clk` 50.000 MHz、`pclk_raw` 33.333 MHz | Clock Summary + `report_clock_utilization`；同上构建 | 本地原件（Vivado 报告） | [merged 摘要 §4](../evidence/merged-overlay-2026-09-24.md) |
| 4 | 合并产品 Overlay 功耗 | Total On-Chip Power **2.418 W**（Dynamic 2.247 W + Device Static 0.171 W；Tj 52.9 ℃；预算 `Unspecified`） | `report_power`，commercial/typical/Production；同上构建 | **估计值**（Vivado 工具估计，**非板上实测**） | [merged 摘要 §5](../evidence/merged-overlay-2026-09-24.md)；[EVIDENCE-PACK §3](EVIDENCE-PACK.md) |
| 5 | 交付 bit 载荷绑定 | `payload_equal: true`；两 bit sync 偏移均 **175**；payload SHA-256 `0ce74071…`；routed DCP `63f679bc…` | 交付 release `4814bbc4…`（bit `d5bfe5d9…`）↔ 同次构建 bit `76e242fe…`（2026-09-24） | 本地原件 + 本地只读绑定记录 | [bitstream-binding §2](../evidence/bitstream-binding-2026-10-26.md) |
| 6 | 交付 12 帧有限冒烟 | BPSK ON1/ON2 **0/138、0/138**；QPSK ON1/ON2 **0/128、0/128**；QAM16 ON1 **1/256**（BER 0.00390625，指标负例）、ON2 **0/256**；各模式 OFF 帧 clean | release `4814bbc4…`；2026-10-26；单帧 campaign，每模式 2 ON + 2 OFF | 板端有限冒烟（`BOARD_VERIFIED` 仅限该释放/该 12 帧；不是整体 BER 资格） | [board-results §1](../evidence/board-results-2026-10-26.md)；[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| 7 | QAM64 移除负例 | **9/9 PASS** | 对安装 release 文件执行；2026-10-26 | 离线负例（文档记载） | [board-results §1](../evidence/board-results-2026-10-26.md) |
| 8 | QPSK 5 帧有限回放 | 5 帧均 QI 显式、`sync=true`、BER **0/128**；独立参考一致 | 2026-10-23 开发版回放（**不重新采集**；非独立 TEST 集） | 开发回放（`部分`） | [QPSK 报告](../reports/2026-10-23-p3-qpsk-offline.md) |
| 9 | 双 TX 示波器实测 | 三帧下/上边带 **78.112 / 81.888 MHz**（目标 78.125/81.875，误差 **±13 kHz**）；12/12 检验通过；削波 **0** | 2026-09-30 历史板级；LO 80 MHz / 60 MSPS / 2R2T；CH1 经 HK-50、CH2 直连 | 历史板级记录（**非当前交付版本**） | [双 TX 记录](../reports/2026-09-30-dual-tx-scope-and-touch.md) |
| 10 | 独立 RF Overlay 板级 L2 Stage 1 | **8 PASS / 0 FAIL / 0 NOT-APPLICABLE / 2 RECORD-ONLY**；C1 边带 78.128/81.872 MHz（偏差 0.0 kHz）；C5 衰减差 19.85 dB；287 寄存器地址 0 读失败 | 2026-09-18；独立 `rf_overlay.bit` sha `4bc8ba11…`；1R1T；LO 80 MHz / 60 MSPS | 历史板级记录（**非当前交付 bit**） | [L2 Stage 1 记录](../rf/l2_stage1_record.md) |
| 11 | ARM 完整计算 P95 / RSS（历史候选） | `b82b141e…` **278.835 ms / 31704 KiB**（2026-10-12）；`399b300e…` **258.6409 ms / 31428 KiB**（2026-10-15）；`aba80e92…` **272.785 ms / 31728 KiB**（2026-10-16）；门槛 P95 ≤ 300 ms、RSS 记录预算 64 MiB | 板端隔离 ARM，100 次完整计算（含原生 CNN） | `ARM_VERIFIED`（历史候选，**均非当前交付版本**，不可移植） | [board-results §6](../evidence/board-results-2026-10-26.md)；[SYNTHESIS-METRICS §4.1](SYNTHESIS-METRICS.md) |
| 12 | RF 源同步 DDR 接口时序 | `check_timing` 全局 **9** 输入无 input delay、**48** 输出无 output delay（其中 39 为 HIGH + 9 为 false-path 输出；**48 不能算作 RF 端口数**）；RF 子集 **7 个 RX 输入（P 侧）/ 8 个 TX 输出（P 侧）**；接口无 `set_input_delay`/`set_output_delay`；lane1 路径 `slack=inf`、`Path Group=(none)` | 交付 bit 同源 routed DCP（只读检查）；板级走线 skew、AD9361 内部相位、runtime IDELAY tap **TBD** | `NOT_MEASURED`（约束缺陷已确认；**不得宣称接口时序通过**，负 slack 也不等于失效） | [RF-INTERFACE-TIMING](RF-INTERFACE-TIMING.md)；[merged 摘要 §7](../evidence/merged-overlay-2026-09-24.md) |
| 13 | 当前交付版本"优化前后" | **未取得（无同版本同条件基线）** | 交付版本只有一个版本身份，无同候选前/后两次测量；当前交付口径 ARM P95/RSS 亦为 `未取得` | `未取得` | [OPTIMIZATION-AND-REPRODUCTION §1.3](OPTIMIZATION-AND-REPRODUCTION.md) |
| 14 | 历史 modrec4 A/B（**历史优化案例，仅研究原型**） | 五阶段合计 median **378.8867 → 292.0840 ms（−22.9%）**、P95 **444.5217 → 311.7357 ms（−29.9%）**；modrec4 阶段 median **154.5762 → 64.8125 ms（−58.1%）**；host median 37.2272 → 29.4654 ms | 历史候选 `b3cca2ac…`（contract `cnn-bpsk-modrec4-v1`）；同 4 帧、同顺序、warmup 5 + repeats 20（80 samples）、固定顺序不交替；零 RF/未安装；单变量替换 `ModRec4Adapter.evaluate_iq` | `ARM_VERIFIED`（历史候选；**研究原型，未在当前交付版重测，不可移植**） | [OPTIMIZATION-AND-REPRODUCTION §1.4](OPTIMIZATION-AND-REPRODUCTION.md) |

## 2. 功耗：Vivado 估计值声明（必须保留）

表中第 4 行 `2.418 W` 是 **Vivado `report_power` 的工具估计**（commercial/typical/Production 条件），**不是板上功耗/电流/温度实测**；本项目没有功耗预算对照（`Design Power Budget: Unspecified`）。任何对外材料必须写"**估计值**"或"Vivado 报告估计"，**不得**写成"实测功耗 2.418 W"，也不得据此宣称散热/供电余量。

## 3. 当前交付版本：优化前后 = 未取得

- 交付版本没有"优化前"版本：已合入的优化在交付候选产生之前就进入产品源码，交付只有一个版本身份，**不存在同候选内前/后两次测量**。
- 当前交付口径的 ARM P95/RSS 本身**未重测**；历史候选的 `278.835 / 258.6409 / 272.785 ms` 分属不同候选/日期/合同，**不可移植**。
- 交付 bit 与提交源码不保证同源（2026-09-29 工作区构建；当时 `create_bd.tcl`、`awg.xdc` 等为未提交修改，见 [积压分诊](../reports/2026-09-30-backlog-triage.md) §0），资源/时序"前后"对照也失去同源基础。
- 五阶段 full-path 指标（第 14 行）与历史 `P95 ≤ 300 ms` 资格路径**不是同一口径**：五阶段合计即使"优化后"P95 为 `311.74 ms`，而 300 ms 是"100 次热完整计算（含原生 CNN）"资格路径预算；两者不可互换。
- 若评审要求交付版本上的前后数据，只能按 [OPTIMIZATION-AND-REPRODUCTION §1.6](OPTIMIZATION-AND-REPRODUCTION.md) 补测（需板卡授权；本轮不执行）。

## 4. 历史 modrec4 A/B：单列为"历史优化案例"（不可移植）

**它是什么**：task-46 ARM full-path A/B（2026-10-21），在**同一历史候选 release 副本**内做"基线 vs 单次共享计算"单变量受控实验；是本次检索发现的唯一"同版本/同条件/同口径"配对，但**不是当前交付版本**。

**数字**（照抄 §1 第 14 行）：五阶段合计 median −22.9%、P95 −29.9%；modrec4 阶段 median −58.1%。

**引用时必须同时写的边界**：

1. **研究原型**：optimized 侧是研究原型（`optimized_fullpath.py` 包裹），当时不是已发布候选；后续按该证据合入产品后**没有重新测 ARM**（原文引用，不是本表推断）。
2. **固定顺序、单次运行、不交替**：80 samples（warmup 5 轮 + repeats 20 轮 × 4 帧），存在热漂移/会话漂移风险；未做交替顺序或多会话重复。
3. **`0 mismatch` 是容差一致，不是逐字节相等**：输出等价判据是语义/字段容差比较；原始严格比较存在最后一位浮点差 **2.84e-14**、six margin **1.48e-8**（均在声明容差内）；**不得**写成"逐字节相同"。
4. **未在当前交付版本重测**：交付版本（`4814bbc4…`）无此前后配对；不得把本案例当作交付版本优化收益。
5. **不可移植**：其指标是"五阶段合计"，不能套 300 ms 资格预算；零 RF、未安装、未做 C 运行时/真实 RF/UI 端到端，不证明真实链路时延。
6. **其他近似数据（BPSK 参考批量、RRC 两轮、QAM64 v1→v2、RF Overlay 重建等）均为跨候选/跨构建，不作优化对照**（见 [OPTIMIZATION-AND-REPRODUCTION §1.5](OPTIMIZATION-AND-REPRODUCTION.md)）。

## 5. RF 接口时序缺口：一行结论

**约束缺陷已确认；板级接口时序未测；不得宣称 RF DDR 接口时序通过。** RF 子集是 **7 个 RX 输入（P 侧：6 data + 1 frame；`rx_clk_in_p/n` 是时钟源，不在 7 内）** 与 **8 个 TX 输出（P 侧：6 data + 1 frame + 1 clock；真正需 `set_output_delay` 的是 7 个数据/帧端口，`tx_clk_out_p` 需要生成时钟）**；`rf.xdc` 无 `set_input_delay`/`set_output_delay`，同源 DCP 上 lane1 路径 `slack=inf`、`Path Group=(none)`。板级 DATA_CLK↔data/frame skew、AD9361 内部相位、runtime IDELAY tap 全部 `TBD`。**内部 WNS/WHS 为正只覆盖已约束的内部路径**；补上约束也只是必要条件，是否满足取决于未测参数（详见 [RF-INTERFACE-TIMING](RF-INTERFACE-TIMING.md)）。

## 6. 原始证据与不可复算声明

- 本表引用的 Vivado 报告、routed DCP、位流/HWH、绑定记录、安装读回日志与原始 IQ **均不在本仓库**；在开发机 `<LOCAL_EVIDENCE>/`，含开发机路径与主机名，不随仓库分发。
- 本表是**文档级/脱敏摘要级**引用，不能用本副本复算或验证原件 SHA-256；需复核时按 [EVIDENCE-PACK §7](EVIDENCE-PACK.md) 的步骤回查原件哈希。
- 本表**不证明**：外部 RF 接口时序满足、当前交付版本性能、功耗/散热、完整物理验收、真人验收、EVM、自动选档、QAM64 交付能力；对应项为 `NOT_RUN`/`NOT_MEASURED`/`NOT_IMPLEMENTED`/`估计值`/已移出。
