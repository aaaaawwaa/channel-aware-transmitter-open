# 当前交付版本脱敏证据包（EVIDENCE PACK）

维护日期：2026-10-31。本文件是**当前交付口径**（2026-10-26 交付复审）证据材料的索引与可核对性说明。**只做证据层级与身份/哈希绑定**，不重复 [综合与实现指标](SYNTHESIS-METRICS.md) 的全文与缺口清单。

> **边界**：本轮**没有重新综合、没有打开 Vivado 执行、没有操作板卡、没有补采、没有改源码/算法/判据/证据原件**；只读本地报告与文档。
> **公开副本不是原件**：原始 `.rpt`/位流/HWH/routed DCP/收据/原始 IQ 在开发机 `<LOCAL_EVIDENCE>/`（含开发机路径与主机名），**不在本仓库**；本包是人工转录的脱敏摘要，字节与原件不同，**不能在本副本内复算**。

## 1. 交付身份与版本绑定

| 项 | 值 | 出处 |
|---|---|---|
| 交付口径 | BPSK/QPSK 有限演示 + 受限 QAM16；QAM64 已移出交付 | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[README](../../README.md) |
| 交付 release | `4814bbc42b9d5e0165a3ed3ebe7eaeef`（2026-10-26 安装 + 只读读回；**历史安装记录，不代表当前板态**） | `<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/candidate/merged_release_manifest.json`（SHA-256 `7aba832d2ae706a90f252afaaa08029d8b37a34b2880552d8c365f0d9ed854b9`） |
| 交付位流 | `awg_merged.bit`，SHA-256 `d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555` | 同上 |
| 交付 HWH | `awg_merged.hwh`，SHA-256 `ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0` | 同上 |
| 同次构建位流 | `host_bd_wrapper.bit`，SHA-256 `76e242fe0438e1bec5596ef93be256f0532a53968c991b103bd710c89f08e1bb`（2026-09-24 10:11:52） | `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/`；仓库 `build_manifest.json` 记录同值 |
| 载荷绑定 | 交付 bit 与同次构建 bit 的**可编程载荷逐字节相同**：`payload_equal=true`，sync 偏移均 **175**，payload SHA-256 `0ce74071ee4f4ee625539a19ef3eef124f89aeeddb09c5ac68e157451f4ffb5e`；文件级 SHA 不同 | `<LOCAL_EVIDENCE>/codex-frame-idelay-check-20261025/verification.json`（SHA-256 `3ada5d36107d25b0efd9eef987075a497b20cc3968980ca5cdc742242aa82ef9`） |
| routed DCP | `host_bd_wrapper_routed.dcp`，SHA-256 `63f679bcce815ba6c883f2e47b286dfc35a549ae9d040eba028548bd2be7c305` | 同上 |
| 工具/器件 | `Vivado v.2025.2 (win64) Build 6299465`；`xc7z020clg484-2`；Speed File `-2 PRODUCTION 1.12` | `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/*.rpt` 报告头 |
| 交付日期 | 2026-10-26（release 打包/安装/读回）；PL 构建日期 2026-09-24 | 同上 |
| 仓库内可核对项 | `build_manifest.json`：bit sha `76e242fe…`、part、`built_at 2026-09-24 10:19:29`、WNS 0.213/WHS 0.018；`toolchain.vivado` 为 `null` | [build_manifest.json](../../build_manifest.json) |

> 绑定细节见 [bitstream-binding-2026-10-26.md](../evidence/bitstream-binding-2026-10-26.md)；三份实现摘要见同目录 `merged-*` / `rf-*` / `pynq-*`。各摘要文首均给"报告头 + 原件 SHA-256"。

## 2. 证据清单

> 状态词：`已有`（原件在本地、报告头已核对）/ `估计值`（工具估计）/ `部分`（有限帧或仅文档级）/ `非当前版本` / `未取得`。完整 SHA-256 在各摘要文末，本表取前 12 位。

| # | 指标摘要 | 版本/日期 | 条件 | 结果（照抄） | 原始证据位置 + SHA-256 | 公开副本位置 | 状态 |
|---:|---|---|---|---|---|---|---|
| 1 | 合并 Overlay 资源 | 2026-09-24 构建 | `report_utilization`，Fully Placed | LUT **28745（54.03%）**、FF **22620（21.26%）**、BRAM **18.5（13.21%）**、DSP **104（47.27%）** | `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_utilization_placed.rpt` · `c42c1d087e52` | [merged-overlay §2](../evidence/merged-overlay-2026-09-24.md) | `已有` |
| 2 | 合并 Overlay 时序 | 同上 | `report_timing_summary`，Routed | **WNS +0.213 / WHS +0.018 ns**；TNS/THS 0；失败端点 0/0；`All user specified timing constraints are met.` | `…timing_summary_routed.rpt` · `56f80e2f69b7` | [merged-overlay §3](../evidence/merged-overlay-2026-09-24.md) | `已有` |
| 3 | 合并 Overlay 时钟 | 同上 | Clock Summary + `report_clock_utilization` | `clk_fpga_0` 50.000、`clk_fpga_1` 200.000、`data_clk` **239.981**、`sys_clk` 50.000、`pclk_raw` 33.333 MHz；BUFGCTRL 6/32、MMCM 1/4 | `…clock_utilization_routed.rpt` · `ba38ab1bccd7` | [merged-overlay §4](../evidence/merged-overlay-2026-09-24.md) | `已有` |
| 4 | 合并 Overlay 功耗 | 同上 | `report_power`，commercial/typical | Total On-Chip **2.418 W**（Dynamic 2.247 + Static 0.171）；Tj 52.9 ℃；预算 `Unspecified` | `…power_routed.rpt` · `ce9e611b61c2` | [merged-overlay §5](../evidence/merged-overlay-2026-09-24.md) | `估计值`（非板端实测） |
| 5 | 合并 Overlay 实现状态 | 同上 | route_status / drc_routed / runme.log | 路由错误 **0**；DRC **0 Errors**；bitgen 成功 | `…route_status.rpt` · `56175c970378`；`…drc_routed.rpt` · `d73bcbc397ad`；`runme.log` · `bca856b4bf90` | [merged-overlay §6](../evidence/merged-overlay-2026-09-24.md) | `已有` |
| 6 | 交付 bit 载荷绑定 | 交付 2026-10-26 / 构建 2026-09-24 | 只读比对（未综合/未开板） | `payload_equal=true`；sync 偏移 175/175；DCP `63f679bc…` | `verification.json` · `3ada5d36107d`；bit `76e242fe…`；DCP `63f679bc…` | [bitstream-binding §2](../evidence/bitstream-binding-2026-10-26.md) | `已有` |
| 7 | 交付 release 身份 | 2026-10-26 | 发布清单 | release `4814bbc4…`；bit `d5bfe5d9…`；HWH `ed8d011a…`；daemon `e51e97ae…`（1.0.4/protocol1）；bundle `74c6e32c…`；software contract 62 项 | `merged_release_manifest.json` · `7aba832d2ae7`；`candidate.tgz` · `74c6e32c9edc` | [bitstream-binding §1](../evidence/bitstream-binding-2026-10-26.md) | `已有` |
| 8 | 交付 12 帧有限冒烟 | 2026-10-26 | BPSK/QPSK 各 2 ON+2 OFF；QAM16 2 ON+2 OFF | BPSK 0/138、0/138；QPSK 0/128、0/128；QAM16 **1/256** 与 0/256；OFF 帧 clean | `<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/acceptance/aggregate.json` · `8e63a511a15d`；`REPORT.md` · `d0db54ac1cdf` | [board-results §1](../evidence/board-results-2026-10-26.md) | `部分`（有限帧；QAM16 负例保留） |
| 9 | QAM64 移除负例 | 同上 | 对安装 release 文件执行 | **9/9 PASS** | `ACCEPTANCE-RESULTS.md` · `cb2c0c993358` | [board-results §1](../evidence/board-results-2026-10-26.md) | `部分`（负例测试，非产品功能验收） |
| 10 | QPSK 5 帧有限回放 | 2026-10-23 | 真实开发帧回放，不重采 | 5 帧均 QI 显式、`sync=true`、BER **0/128** | `<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-qpsk-arm-2026-10-23/FINAL-ARM-REPORT.md` · `59d054feba24`；`final-binding.json` · `76bfc323a532` | [board-results §2](../evidence/board-results-2026-10-26.md) | `部分`（非独立 TEST 集） |
| 11 | 双 TX 示波器实测 | 2026-09-30（历史板级） | LO 80 MHz / 60 MSPS / 2R2T | 边带 **78.112/81.888 MHz**（±13 kHz）；12/12 检验通过；削波 0 | `<LOCAL_EVIDENCE>/2026-09-30-dual-tx-scope/acquisition.json` · `f13682274e41`；`analysis.json` · `55efb1ace170` | [board-results §3](../evidence/board-results-2026-10-26.md) | `非当前版本`（历史板级记录） |
| 12 | 环境诊断标定 | 2026-10-18（release `1c0894bf…`） | TRAIN-only 20 帧拟合 | **VAL 10/10 CLEAR**；3 OFF 零误判；normal 无 INVALID | `codex-s2-takeover-2026-10-18/completion.json` · `69464437d3a2` | [board-results §4](../evidence/board-results-2026-10-26.md) | `非当前版本`（开发期标定） |
| 13 | 独立 RF Overlay 板级 L2 Stage 1 | 2026-09-18（bit 2026-09-17） | 1R1T；LO 80 MHz / 60 MSPS | **8 PASS / 0 FAIL / 2 RECORD**；C1 78.128/81.872 MHz；C5 19.85 dB；寄存器 0 读失败 | 仓库 `docs/rf/l2_audit_stage1.txt` · `edd9e7a00734`；`docs/rf/l2_audit_stage1_postexempt.txt` · `159d71314186` | [board-results §5](../evidence/board-results-2026-10-26.md) | `非当前版本`（独立 RF 目标） |
| 14 | ARM 完整计算 P95/RSS | 2026-10-12 / 10-15 / 10-16 | 板端隔离 ARM，100 次完整计算 | `b82b141e` 278.835 ms/31704 KiB；`399b300e` 258.6409 ms/31428 KiB；`aba80e92` 272.785 ms/31728 KiB | `<LOCAL_EVIDENCE>/rf-teaching/…/receipt.json`：`7a28de5a6763`、`a450d569576c`、`a28f5e026e0c` | [board-results §6](../evidence/board-results-2026-10-26.md) | `非当前版本`（不得移植） |
| 15 | 独立 RF Overlay 实现 | 2026-09-19 | impl_1（非交付 bit） | WNS +0.193/WHS +0.043；LUT 3294；BRAM 8；DSP 0 | `<LOCAL_EVIDENCE>/rf_overlay/…/impl_1/`：`52be159c0605`、`59a25984a860`、`caf326ea6019` | [rf-overlay-2026-09-19.md](../evidence/rf-overlay-2026-09-19.md) | `已有`（非交付 bit） |
| 16 | 独立 PYNQ Overlay 实现 | 2026-09-13 | impl_1（非交付 bit） | WNS +11.549/WHS +0.036；LUT 3291；BRAM 0；DSP 0 | `<LOCAL_EVIDENCE>/pynq_overlay/…/impl_1/`：`d29c62214695`、`9433680be7cb`、`8a63a59878d2` | [pynq-overlay-2026-09-13.md](../evidence/pynq-overlay-2026-09-13.md) | `已有`（非交付 bit） |
| 17 | RF DDR 接口时序覆盖缺口 | 同源 routed DCP（QAM64 线只读复核） | `check_timing`（只读） | `no_input_delay` 章节 **10 = 9 HIGH + 1 MEDIUM**（`spi_miso` false path）；`no_output_delay` 章节 **48 = 39 HIGH + 9 MEDIUM**（`rf_gpio` false path）；RF 子集 7 RX 输入 + 8 TX 输出（含转发时钟）；lane1 `slack=inf` | `<LOCAL_EVIDENCE>/rf-teaching/qam64-lane1-timing-2026-10-26/CHAIN-TIMING.json` · `0968f7e93b89` | [merged-overlay §7](../evidence/merged-overlay-2026-09-24.md)（口径）、[RF-INTERFACE-TIMING](RF-INTERFACE-TIMING.md) §2.2 | `已有`（缺口确认；QAM64 结论不作当前交付结果） |

## 3. 功耗专段（估计值，不是板端实测）

| 目标 | Vivado `report_power` 估计 | 说明 |
|---|---|---|
| 合并产品 Overlay（交付 bit 内容） | **2.418 W**（Dynamic 2.247 + Static 0.171；Tj 52.9 ℃；Max Ambient 57.1 ℃；预算 `Unspecified`） | 工具在 commercial/typical/Production 下的活动估算 |
| 独立 RF Overlay（非交付） | 2.071 W（Dynamic 1.915 + Static 0.155；Tj 48.9 ℃） | 同上 |
| 独立 PYNQ Overlay（非交付） | 1.804 W（Dynamic 1.657 + Static 0.148；Tj 45.8 ℃） | 同上 |

**必须遵守的表述**：以上是 `report_power` 的**估算**；本项目**没有**板级电压/电流/温度实测，也没有功耗预算对照。不得写成"实测功耗 2.418 W"或据此宣称散热/供电余量。原件与 SHA-256 见 §2 行 4/15/16 及各实现摘要。

## 4. 原件与公开副本的差异声明

1. **不是原件**：`docs/evidence/*.md` 与本文都是人工转录的**脱敏摘要**；原始 `.rpt`/bit/HWH/DCP/收据/日志/原始 IQ 在开发机 `<LOCAL_EVIDENCE>/`，未随仓库。
2. **字节不同**：摘要不含原件格式（表格边框、警告原文、报告头主机名等），因此摘要文件 SHA-256 ≠ 原件 SHA-256；引用原件时必须用原件 `.rpt` 的 SHA-256（本包各表已给）。
3. **不可复算**：本仓库没有 Vivado、位流/HWH、原始 IQ 与收据，本包指标不能在本副本内重算，旧摘要也不能验证原件字节。
4. **脱敏范围**：已剔除/抽象开发机绝对路径、主机名、板端账户名、设备串号；本地原件路径统一写 `<LOCAL_EVIDENCE>/…`。
5. **以原件为准**：摘要与原件有出入时以原件为准，并在新文档记录差异；不就地改写旧摘要。

## 5. 已知口径冲突（如实记录，不调和）

| # | 冲突 | 说法 A | 说法 B | 处理 |
|---|---|---|---|---|
| C1 | `data_clk` 域 WHS | 合并 Overlay 路由报告 Intra-Clock Table：**0.066 ns** | QAM64 线只读复核 `CHAIN-TIMING.json`：**0.349 ns**（`data_clk_WHS_ns`） | 两个值都保留；本包表格采用报告表值 0.066，差异来源未核查，不作调和 |
| C2 | 2026-09-17 RF Overlay 板级 WNS/WHS | `docs/rf/l2_stage1_record.md`：**WNS 12.359 / WHS 0.056 ns**（带位流 sha `4bc8ba11…`） | `docs/rf/boardtest.md` 模板：**WNS 12.710 / WHS 0.066 ns**（同标"2026-09-17 本构建"） | 两者都保留；本包不判断最终值 |
| C3 | 交付冒烟"是否发生过物理采集" | `ACCEPTANCE-RESULTS.md`：`no physical/RF cable-loopback capture` | 交付复审：所选帧的 ON `final.json` 记 `physical=true` | 区分"有线采集已发生"与"完整物理验收未完成"；不调和（见 [board-results §1](../evidence/board-results-2026-10-26.md)） |
| C4 | 交付冒烟帧数 | 验收集 **12 帧** | 物理计数 **14**（失败尝试多 2 次 QAM16 ON2） | 两个数都保留；预算偏差作为负面事实记录 |

## 6. 排除项与边界

| 排除对象 | 边界 |
|---|---|
| **历史 QAM64 报告/指标** | QAM64 已移出本轮交付，其时序/残差/负例报告仅作历史记录，不作为当前交付结果；本包只引用其"接口未约束"的方法性缺口（该缺口对交付 bit 同样成立，见 §2 行 17）。 |
| **辅助 AWG 指标** | 旧 ACM2108/辅助 AWG 链与 RF 产品是两套产物：`test_bridge 230 PASS`、`scope_verify 61/61` 等**不进入**本证据包，不用于当前 RF 交付。 |
| **其他候选** | `b82b141e`、`399b300e`、`aba80e92`、`e66197ed`、`b67a8aaa` 等只作为历史记录的来源引用（带候选与日期），不得移植为当前交付版本指标。 |
| **当前交付版本未测项** | 当前交付口径下**没有** ARM P95/RSS 重测、没有完整物理验收、没有功率实测、没有真人 LCD/触摸验收 → 全部标 `未取得`/`NOT_RUN`。 |
| **独立 RF / PYNQ Overlay** | 2026-09-19 / 2026-09-13 的两份实现报告**不是交付 bit** 的报告；只用于说明各自独立目标的 FPGA 实现结果。 |

## 7. 如何核对（建议顺序）

1. [bitstream-binding-2026-10-26.md](../evidence/bitstream-binding-2026-10-26.md)：交付 bit `d5bfe5d9…` 与 2026-09-24 构建 `76e242fe…` 的 `payload_equal` 绑定、DCP `63f679bc…`。
2. [merged-overlay-2026-09-24.md](../evidence/merged-overlay-2026-09-24.md)：资源/时序/时钟/功耗估计/实现状态；注意 §7"内部时序 ≠ 外部 RF 接口"。
3. [board-results-2026-10-26.md](../evidence/board-results-2026-10-26.md)：板端/参考比对结果与历史/非当前版本标记。
4. 在开发机按 `<LOCAL_EVIDENCE>` 取原件，用原件 SHA-256 核对；不一致以原件为准。
5. 完整指标背景与缺口见 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md)，本包不重复。

## 8. 本包不证明什么

- 不证明外部 RF DDR 接口满足 AD9361 采样/输出时序（接口未加 I/O delay；板级 skew 未表征）。
- 不证明当前交付版本的板端性能（无 2026-10-26 之后的 ARM/整机重测）。
- 不证明完整物理验收、真人验收、EVM、功耗/散热（对应项 `NOT_RUN`/`NOT_MEASURED`/`NOT_IMPLEMENTED`/`估计值`）。
- 不证明本仓库可独立重建交付 bit（缺 bit/HWH/DCP；该 bit 为工作区构建，见 `docs/reports/2026-09-30-backlog-triage.md` §0），也不证明 QAM64/辅助 AWG 的交付能力。
