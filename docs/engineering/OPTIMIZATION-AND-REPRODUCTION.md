# 优化前后对照与从零复现说明

整理日期：2026-10-31。本文按赛题指南 **3.3.4（优化目标：可测量的性能表现与基线对比、资源合理、从零复现、判断依据）**、**3.3.5.1（工程包：仿真/验证、综合与实现报告、上板实测、可复现构建脚本）** 与 **3.3.5.3（设计报告的优化过程与复现说明）** 整理已有材料。

> **边界（必须保留）**
> 1. 本文**只读整理**：没有重新综合、没有操作板卡、没有补采、没有修改源码/算法/判据/证据原件。所有数字照抄原文并保留原文限定语。
> 2. 本仓库（公开源码副本）**不含**位流/HWH、Vivado 报告、原始收据、原始 IQ 与模型/数据集产物。因此本文证据分两级：**仓库内文档记载**（可阅读原文、不能复算）与 **`<LOCAL_EVIDENCE>/…`**（开发机私有证据与构建根目录，不在仓库）。两级的完整定义与引用规则同 [综合与实现指标](SYNTHESIS-METRICS.md) §1.2、§7.1。
> 3. 状态词：`可用配对` / `未取得` / `不作配对` / `部分` / `NOT_RUN`；不把跨候选、跨日期、跨构建的数据凑成优化前后对照。
> 4. 与已有文档的分工：[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) 负责资源/时序/功耗/上板指标汇总；本文负责**优化前后配对判定**与**从零复现步骤**；[GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md) 负责逐条要求的状态与缺口。

## 1. 优化前后对照核查（指南 3.3.4）

### 1.1 配对判定口径

本文只把同时满足以下三条的数据当作"优化前后配对"：

| 条件 | 判定标准 | 反例（不得配对） |
|---|---|---|
| **同版本** | 同一候选 release / 同一发布身份 / 同一被测实现版本；优化实验在同一候选副本内做基线-优化两段测量 | 不同候选 ID、不同发布、不同日期 | 
| **同测试条件** | 同一输入集（同一 IQ/同一帧列表）、同一 harness、同一顺序/次数/warmup、同一板卡与环境状态（空闲、无 RF、无并发构建） | 不同帧集、不同 harness、不同重复次数、有 CPU 争用 |
| **同指标口径** | 指标名与计算方式相同（例如同为"五阶段合计"、同为"100 次热运行 P95"、同为 VmHWM）；**不同路径的指标不可互换** | 把"五阶段合计"与"五组件资格"混用、把 host 数字当 ARM、换口径追认通过 |

本仓库历史材料的一个关键事实（见 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §5）：**按仓库内报告/索引层面核查，优化前后对照为"未取得"**。本次整理补充检索了开发机 `<LOCAL_EVIDENCE>`（公开源码副本不含），发现在**历史候选**上存在一组满足上述三条的受控 A/B；但**当前交付版本仍无同版本同条件基线**。下文分别给出。

### 1.2 检索范围与结果概览

已只读检索：

- [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §4–§6（指标简表、ARM P95/RSS、缺口 G1–G4）；
- [DELIVERY-INDEX.md](DELIVERY-INDEX.md) §4（已验证能力）、§5（失败/限制）；
- [docs/reports/](../reports/)、[docs/reviews/](../reviews/) 中与性能/优化/候选相关的报告与复审；
- `<LOCAL_EVIDENCE>/rf-teaching/` 下与基准、性能、优化候选相关的证据目录（本次只读打开的报告/JSON/日志，未运行任何脚本；关键目录见 §1.4/§1.5 证据列）。

| 版本 | 条件 | 指标 | 前值 | 后值 | 证据 | 状态 |
|---|---|---|---|---|---|---|
| 当前交付版本（2026-10-26 交付口径，最后记录的安装候选 `4814bbc4…`） | —（无同版本/同条件/同口径的"前/后"两次测量） | 任一优化指标 | **未取得** | **未取得** | —（无基线） | **未取得（无同版本同条件基线）**；见 §1.3 |
| 历史候选 `b3cca2ac…`（contract `cnn-bpsk-modrec4-v1`） | 同一候选 release 副本；同 4 帧；80 samples（warmup 5 + repeats 20）；固定顺序；零 RF/未安装；单变量替换 `ModRec4Adapter.evaluate_iq` | ARM 五阶段合计 median / P95；modrec4 阶段 median；VmHWM | 378.8867 / 444.5217 ms；154.5762 ms；31,704–32,440 KiB | 292.0840 / 311.7357 ms；64.8125 ms；31,520–32,516 KiB | `<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-perf-arm-2026-10-21/result/PERF-ARM-RESULT.md` + `perf-result.json` | **可用配对**（受控 A/B；历史候选，**非当前交付版本**）；见 §1.4 |
| 其他历史候选/历史构建（`ebad0de`→`b67a8aa`、`90ac6b12`→`bedf98c7`、RF Overlay 两版时序等） | 跨候选或跨构建 | — | 见 §1.5 | 见 §1.5 | 见 §1.5 | **不作配对**；只作背景 |

> 读法：第一行说明"当前交付版本没有可填的数据"；第二行是本次找到的唯一可用配对，填的是"同一候选内基线/优化两段测量"，**不是**跨候选比较；第三行的数字对照见 §1.5，不得改写成优化收益。

### 1.3 当前交付版本：未取得（无同版本同条件基线）

**结论：当前交付版本（交付复审口径，见 [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)）没有可配对的优化前后数据。** 判断依据：

1. **没有"优化前"版本**：已合入的优化（单次共享计算、RRC 接收等）在产生交付候选之前就已进入产品源码；交付版本只有一个版本身份，不存在同一交付候选内的"前/后"两次测量。
2. **当前交付口径的 ARM 实测本身为 `未取得`**：[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §4.1 明确"当前交付口径（2026-10-26 后）P95/RSS = 未取得"；历史候选的 100 次 P95/RSS（如 `278.835 ms`、`258.6409 ms`、`272.785 ms`）分属不同候选、不同日期与不同合同，不能移植。
3. **交付 bit 与提交源码不保证同源**：交付 bit 是 2026-09-29 工作区构建，当时 `create_bd.tcl`、`awg.xdc` 等为未提交修改；"没有任何提交能重建板卡上正在运行的东西"的历史结论见 [积压分诊](../reports/2026-09-30-backlog-triage.md) §0。资源/时序类"前后"对照因此也失去同源基础。
4. **五阶段 full-path 指标与既有 300 ms 预算不是同一路径**：历史 `modrec4` full-path 把 analysis/six_class/bpsk/environment/modrec4 五阶段相加（见 §1.4），其 P95 即使优化后也为 `311.74 ms`；而 `P95 ≤ 300 ms`、`RSS ≤ 64 MiB` 是历史候选"100 次热完整计算（含原生 CNN）"资格路径的预算（[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §4.1 门槛行）。**两者不可互换口径**，不能拿五阶段数字去套 300 ms，也不能拿 300 ms 通过去宣称五阶段达标。

因此：**按"同版本+同条件+同口径"三条严格判定，当前交付版本的优化前后对比表为"未取得（无同版本同条件基线）"。** 若评审要求交付版本上有"前后"数据，只能按 §1.6 补测；本文不执行补测。

### 1.4 表 A：可用的同版本受控配对（历史候选 `b3cca2ac…`，modrec4 单次共享计算）

这是本次检索发现的**唯一**满足"同版本/同条件/同口径"的配对。它来自 task-46 的 ARM full-path A/B（2026-10-21），**不是当前交付版本**，只可如实引用为"历史候选上的受控优化实验"。

**版本与条件**

| 项 | 值 |
|---|---|
| 候选/版本 | `b3cca2acc2ab6bdbd3e30509205b7382`，contract `cnn-bpsk-modrec4-v1`（同一次实验内**同一候选 release 副本**） |
| 被测差异（单变量） | 仅 `ModRec4Adapter.evaluate_iq` 改为"单次共享计算"原型（`optimized_fullpath.py` 包裹）；权重/阈值/输入域/标签/拒识/仲裁/identity/contract/passthrough/provenance 未改；14 个 reference 文件中 13 个与当前候选逐字节相同，差异文件为研究快照 |
| 输入 | 同 4 帧：`nf-00-normal`、`nf-01/02/03-narrowband`（同顺序、同候选 release） |
| 测量协议 | batch=1；warmup 5 整轮；repeats 20 整轮（4 帧/轮 → 80 samples）；固定顺序 baseline → optimized（各自独立进程，不交替；热漂移风险如实记录）；板端空闲、零 RF、未安装、未改 current/服务 |
| 同编译 | Python 实现替换；无 Vivado/原生重新构建；板端上传 95/95 文件 SHA 一致，测量在独立 `/tmp` release 副本上进行 |
| 指标口径 | ARM：harness `out.total_seconds`（五阶段合计）与 VmHWM/VmRSS；host 同协议仅对照；wall invocation 单列 |
| 通过判据（预声明） | 两实现逐帧六类/环境/解调/modrec4 字段在声明容差内一致；任一不一致/异常/超时即停、不重试 |

**指标/前值/后值**

| 指标（ARM，80 samples） | baseline（前） | optimized（后） | 变化 |
|---|---:|---:|---:|
| 五阶段合计 median | **378.8867 ms** | **292.0840 ms** | **−86.80 ms（−22.9%）** |
| 五阶段合计 P95 | **444.5217 ms** | **311.7357 ms** | **−132.79 ms（−29.9%）** |
| 五阶段合计 min / max | 297.24 / 559.51 ms | 212.16 / 371.10 ms | — |
| modrec4 阶段 median | **154.5762 ms** | **64.8125 ms** | **−89.76 ms（−58.1%）** |
| analysis / six_class / bpsk / environment median | 29.16 / 64.79 / 19.67 / 115.87 ms | 28.85 / 64.15 / 19.46 / 115.74 ms | 基本不变 |
| VmHWM（before/after） | 31,704 / 32,440 KiB | 31,520 / 32,516 KiB | 无实质变化（≤ 64 MiB） |
| wall invocation median / P95 | 1340.26 / 1560.99 ms | 1247.26 / 1456.58 ms | −6.9% / −6.7% |

| 指标（host 同协议，仅对照） | baseline（前） | optimized（后） | 变化 |
|---|---:|---:|---:|
| 五阶段合计 median | 37.2272 ms | 29.4654 ms | −20.8% |
| 五阶段合计 P95 | 43.1972 ms | 34.3467 ms | −20.5% |

**输出一致性与等价性**

- ARM full-path：**4/4 帧、0 mismatch**；host full-path **4/4**；独立 70/70 case 行为一致性 **0 mismatch**（`max_deltas` 全 0.0，见 `perf-result.json`）。
- **口径澄清（必须保留）**：后续产品决策文件明确"`0 mismatch` 是**语义/字段容差比较**，**不是逐字节相等**"。原始严格比较存在最后一位浮点/passthrough repr 差异：最大浮点差 **2.84e-14**、six margin **1.48e-8**，均在声明容差内；provenance basename 差异来自未改 adapter。引用时不得写成"逐字节相同"。
- 结果来源：`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-perf-arm-2026-10-21/result/`（`PERF-ARM-RESULT.md`、`perf-result.json`、`rf-perf-…-arm_baseline.json`、`-arm_optimized.json`、`-arm_fullpath_parity.json`、`host/`、`host70/`、`board_run.json`、`SHA256SUMS.json`）；口径澄清与产品决策：`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-product-2026-10-21/PERFORMANCE-DECISION.md`。原始收据不在本仓库。

**状态与限制**

- 状态：**可用配对（受控 A/B，历史候选；非当前交付版本）**。
- 限制 1：optimized 侧是**研究原型**（不是已发布候选）；后续虽已按该证据合入产品（`PERFORMANCE-DECISION.md` 记录最终候选 `4776ba0b…`），但该合入**没有重新测 ARM**（原文："ARM numbers above are referenced only; this task did not re-measure ARM"）。
- 限制 2：**单次运行、固定顺序、不交替**，存在热漂移/会话漂移风险；报告写明未做热控制或多会话重复（需要时应用交替顺序/多次重复，本任务未做）。
- 限制 3：**指标口径是五阶段合计**，不能与"100 次完整计算 P95≤300 ms"预算互换（见 §1.3 第 4 点）。若评审要求 300 ms 路径的对比，须按 §1.6 重新测该路径。
- 限制 4：零 RF、未安装、未做 C 运行时/真实 RF/UI 端到端；不证明真实链路时延。另按历史 ARM full-path 报告的限制：该测量是 **Python 驱动的产品模块路径**，候选含 ARM `.so` 且**未记录实际 backend**，不能证明整条路径未使用 C helper（`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-arm-2026-10-21/result/ARM-FULLPATH-RESULT.md` §9）。

### 1.5 表 B：不构成配对的数据（列出但**不得**当作优化结论）

| 近似数据 | 数值/结论 | 为什么不构成配对 |
|---|---|---|
| BPSK 参考计算批量优化（`ebad0de…`→`b67a8aa…`，2026-10-11） | ARM 100 次完整计算 P95 **314.963 ms → 270.793 ms**；把 127 次逐相位参考平均改为一次 127×63 批量整数计算；127 种子标量参考逐项相等、四个保存窗口五类分析严格相等 | **跨候选**（失败候选与修正候选不同 identity），且各只有一次测量；只能作为"优化方向 + 等价验证方法"的背景，不能当受控对照。出处：[HEADROOM 合同收尾](../reports/2026-10-11-headroom-contract-final.md) §"验证与资源" |
| RRC 接收两轮优化（`90ac6b12…`→`bedf98c7…`，2026-10-23/24） | 旧基线 hot median **16.751 s**、cold 17.136 s、max legal 17.373 s、总 269.264 s、max RSS **22,668 KiB**（≤3 s 目标**未达**）；最终 v3 cold **1.5599 s**、hot median **0.5377 s**、max legal 1.5599 s、21 输入总 **11.110 s**、max RSS **89,016 KiB**；v1→v3 21/21 `MATCH_WITH_ALLOWED_TOLERANCES` | **跨候选**（版本更新本身就是优化）；RSS 从 22,668 KiB 升到 89,016 KiB（**超过 64 MiB 记录预算**），属"以内存换时延"的明确权衡；速度类验收（≤3 s）与 300 ms 预算不是同一路径。证据：`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-rrc-perf-2026-10-24/PERF-REPORT.md`（旧基线）、`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-rrc-candidate-2026-10-24/CANDIDATE-REPORT-FINAL.md`（最终 ARM）；仓库内记录：[2026-10-23 QPSK 离线报告](../reports/2026-10-23-p3-qpsk-offline.md) §"RRC 优化产品候选" |
| QAM64 解码 v1→v2（2026-10-25） | 历史 IQ 离线重解：v1 `2/384`、`5/384` → v2 **0/384、0/384** | 跨候选；QAM64 已移出交付；历史 IQ 离线重解不是新鲜 VAL。不作优化结论 |
| RF Overlay 时序：板级记录 vs 重建报告 | 2026-09-17 板级 WNS **12.359 ns**（bit `4bc8ba11…`）vs 2026-09-19 重建报告 WNS **0.193 ns**（bit `b6a73e4d…`） | **位流文件、报告与构建均不同源**（09-19 构建日志为 `build_samplepath*`），不是同一 RTL/约束的前后报告；见 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §3.2 |
| 合并 Overlay 资源占比 | LUT 54.03%、DSP 47.27%、BRAM 13.21%（2026-09-24 单次实现） | 只有**一次**实现的完整报告，无同目标同约束的"优化前"报告；不能判断"是否以显著资源代价换有限性能收益" |
| 高干扰下的 ARM 计时 | 2026-10-18 一次 **413.601 ms**、33/100 超 300 ms（与板端编译并发，cc1 约 94.7% CPU） | 是**环境争用导致的失败记录**，不是优化/退化对照；应保留 FAIL 并做低干扰复测。出处：[DSH 耗时报告](../reports/2026-10-18-dsh-time-profile-and-azure-build-plan.md) §4 |
| 历史候选 P95 序列 | 278.835 → 258.6409 → 272.785 ms（`b82`→`399b`→`aba80e92`） | 三个不同候选/日期/合同，不是受控 A/B；见 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §5.2 |

### 1.6 最小补测方案（本文不执行；供赛前或获授权后使用）

目标：在**当前交付版本**上取得一组"同版本+同条件+同口径"的优化前后数据，且满足既有资格预算。以下方案均**假设获得一次性板卡只读授权**（无 RF、无安装、无 PL 重配置），并需要原始 IQ 与候选 release 副本（二者不在公开仓库）。

| 方案 | 适用目标 | 条件与次数 | 板端会话 / 构建 | 工作量 | 证据形式 | 验收（沿用既有预算） | 风险/前置 |
|---|---|---|---|---|---|---|---|
| **A（推荐，最快闭环）** | 复刻 task-46 方法：同候选 release 副本内做"基线 vs 优化"两段测量 | 同候选、同 IQ 帧集、同编译（Python 层替换，无 native/Vivado 重建）；每段 **100 次热运行**；固定顺序；先预声明协议 | **1 次板端会话**（隔离 `/tmp`，零 RF/未安装）；**0 次 Vivado 构建**；如涉及 native 改动则 **1 次板端原生构建** | ≈**0.5–1 人日**（协议+执行+报告） | 预声明协议、原始 timings JSON、parity JSON、前后只读安全读回、SHA256SUMS、`PERF-REPORT` 式报告 | 输出等价（帧级/字段容差）；**P95 ≤ 300 ms、RSS ≤ 64 MiB**（只适用于与该预算同一路径；须先声明路径一致）；否则按声明口径分别报告 | 板卡可用与授权；原始 IQ 不在仓库；单次固定顺序的热漂移（建议交替或两次重复） |
| B | 只补"当前交付版本是否达标"（不是优化对照） | 同候选、同 IQ、同编译；100 次热运行只做"后" | 1 次板端会话；0 Vivado；可能 1 次候选打包（若需新候选身份） | ≈0.5 人日 | 100 次 timings、RSS、parity、readback | P95 ≤ 300 ms、RSS ≤ 64 MiB | 只能证明单点达标/不达标，**不能**构成前后对照；当前板态未知 |
| C | 资源/时序类优化对照（如 LUT/DSP/BRAM/WNS） | 同源同约束两次 Vivado 实现，仅改一个参数/约束 | 0 板端；**2 次 Vivado 综合+实现** | 2–4 h 机时 + ≈0.5 人日核对 | 两份 utilization/timing 报告、bit sha、构建清单 | 同源、单变量、报告表头一致 | 交付 bit 与提交源码不同源（见 §1.3 第 3 点）；5 个第三方 RTL 许可未决（[UNKNOWN-PROVENANCE](../../LICENSES/UNKNOWN-PROVENANCE.md)）；**赛前不建议承诺** |
| D | RRC/算法路径时延对照（可选） | 同 21 帧、同 bench（cold/hot/RSS）；同候选 | 1 次板端会话；0 构建（用现有候选） | ≈0.5–1 人日 | cold/hot/max/RSS、21/21 parity | `legal ≤3 s`（历史目标）与 RSS 声明；64 MiB 是否适用需单独声明 | RRC 真实链路 `NOT_RUN`，只能在计算层做，不能宣称真实 RRC 接收；历史 RSS 89,016 KiB 已超 64 MiB |

**方案选择的判断依据**

- 若评审只要求"给出可测量性能与基线对比"：A 最省（0 Vivado、1 板端会话、半天级），且复用 task-46 已证明的协议与证据形式。
- 若评审要求"与历史 300 ms 资格路径对齐"：A 中必须把 100 次热运行的**路径/组件集合**与历史资格定义对齐，并在报告中写明；不得把五阶段合计与 100 次资格混用。
- 若无法取得板卡授权：保持"当前交付版本未取得（无同版本同条件基线）"，在报告中如实引用 §1.4 的历史受控配对并注明其边界；**不编造当前版本数字**。
- 所有方案沿用项目纪律：判据先于测量、失败与原帧保留、不事后换口径追认通过（[开发纪律](../memory/03-development-discipline.md) §1、§5；[DSH 耗时报告](../reports/2026-10-18-dsh-time-profile-and-azure-build-plan.md) §6"不承诺整体快几倍，先按同一统计口径比较"）。

## 2. 从零复现说明（指南 3.3.5.1 / 3.3.5.3）

### 2.1 可复现边界（先读）

本仓库是**脱敏的源码副本**，不是可安装发布包。以本仓库为起点可以：

- 复现**离线/主机**源码级构建与测试（Python/C 测试、RTL 仿真台、SDR 离线测试）；
- 用 Vivado 2025.2 重新构建**独立 PYNQ/RF Overlay** 与**合并产品 bit**（受 §2.9 限制）；
- 在具备板卡、镜像、授权与原始数据的前提下，按 runbook 做试部署/读回/有限演示；
- 重新训练/导出 `ml_training` 的模型（数据集不在仓库，需自建环境重跑）。

**不能**从本仓库单独保证：与历史交付逐字节相同的 bit/HWH/daemon、原始 IQ/收据、六类生产模型与评估文件、正式物理采集与真人验收。缺失项与再生成路径见 §2.7。

### 2.2 硬件与环境版本

| 层 | 组件 | 版本/型号（据文档记载） | 出处 |
|---|---|---|---|
| 板卡 | FPGA 器件 | `xc7z020clg484-2`（Zynq-7020，AC820 系列） | [README](../../README.md)「硬件与工具版本」、[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §1.4 |
| 板卡 | RF 收发器 | AD9361（FMCOMMS3 形态模块），1R1T/2R2T 由 `phy_mode` 配置；交付使用 1R1T | [README](../../README.md) |
| 板卡 | 显示屏/触摸 | 五寸触摸屏 800×480，GT911 触摸控制器 | [README](../../README.md) |
| 板端 | 镜像 / Linux / 工具链 | PYNQ **2.7** 镜像；Linux `5.4.0-xilinx-v2020.2`；原生 `gcc 9.3.0`（armv7l） | [README](../../README.md)；gcc/内核见 [G1 资格报告](../reports/2026-10-08-g1-qualification.md) |
| 板端 | Python / NumPy | CPython **3.8.2** / NumPy **1.17.4** | [README](../../README.md)（Python 3.8 + NumPy 1.17.4）；CPython 3.8.2 见 `<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-arm-2026-10-21/result/ARM-FULLPATH-RESULT.md` §3 |
| 主机 | PL/PS 工具链 | Vivado / Vitis **2025.2**（Vivado `v.2025.2 (win64) Build 6299465`） | [README](../../README.md)；表头见 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §1.4 |
| 主机 | Python（仓库/CI） | `host/requirements.txt` 未锁版本；**CI 用 Python 3.11**；`build_manifest.json` 记录的构建 Python 为 **3.8.6** | [host/requirements.txt](../../host/requirements.txt)、[CI 工作流](../../.github/workflows/awg-bitstream.yml)、[build_manifest.json](../../build_manifest.json) |
| 主机 | 模型训练环境（可选/独立） | 独立 venv `ml_training/.venv`：Python 3.12.10 + numpy 2.5.4 + torch 2.14.1+cpu + pytest 9.1.1；**lock 文件写 numpy 2.5.3，与 README 的 2.5.4 记录不一致（待确认）** | [ml_training/README.md](../../ml_training/README.md)「环境」；[训练 lock](../../scripts/rf_ml/requirements-training.lock.txt) |
| 主机 | GNU Radio（可选） | Radioconda：GNU Radio **3.10.12.0**、Python 3.12、gr-iio、libiio 0.26 | [SDR 测试台 README](../../sdr_testbench/README.md) |
| 主机 | 示波器（可选） | Keysight DSO-X 1102A（70/100 MHz，带宽不足以验收 434 MHz 载波），系统 Python + PyVISA | [SDR 实测报告](../../sdr_testbench/TEST-REPORT.md) |

> 版本原则：以上是**本项目历史记录**；其他板卡/镜像/工具链版本须自行重核，不能把 PYNQ 2.7 的命令与路径假定到 PYNQ 3.x 或其他板卡。

### 2.3 依赖安装

**主机（Windows 或 Linux 均可，命令以仓库 README 为准）**

1. **Git**：用于取源码与版本检查；Windows 需保证 Git 自带 `sh.exe` 在 `PATH`（否则 `make host-test` 会因 cmd 的 `mkdir -p` 失败——见 [工程陷阱](../memory/04-engineering-pitfalls.md) §15）。
2. **Python**：`python -m pip install -r host/requirements.txt`（PySide6、pyqtgraph、pyserial、pytest）；测试框架为 pytest。CI 使用 Python 3.11，历史本地基线为 3.8.6，二者均可作为起点（见 §2.2）。
3. **C 主机测试工具链**：`rf_overlay/sw/board_app` 的主机测试需要 MinGW/GCC 与 POSIX `sh`（Git Bash 提供）；板端原生构建需要板载 gcc（不在主机装交叉工具链——历史结论是主机/MinGW 不能产出 armv7l，见 [rf_build_daemon.py](../../scripts/rf_build_daemon.py) 文件头）。
4. **Vivado/Vitis 2025.2**：安装并配置许可；构建脚本通过环境变量 `VIVADO_BIN` 指向 Vivado 可执行文件（[scripts/build_bitstream.py](../../scripts/build_bitstream.py) 支持该变量，默认值仅为历史本机路径，须按本机设置）。
5. **Hog（仅合并 bit 的 CI 构建需要）**：仓库 CI 以 `Hog-CERN/Hog` 的固定版本 `Hog2026.2` 执行 `./Hog/Do awg_ci`（外部仓库，不随本仓库；需要 Vivado license secrets 与自托管 runner）。不采用 Hog 时可用本机的 `python scripts/build_bitstream.py`（见 §2.4）。
6. **可选：模型训练环境**：在 `ml_training/.venv` 中按 [ml_training/README.md](../../ml_training/README.md) 安装（Python 3.12 + numpy/torch CPU + pytest）；训练依赖与板端运行时是两套环境，不要混用。
7. **可选：SDR/GNU Radio**：安装 Radioconda（GNU Radio 3.10、gr-iio、libiio）与系统 Python 的 PyVISA；两套解释器不能混用（[SDR 测试台 README](../../sdr_testbench/README.md)「本机环境与依赖」）。GNU Radio 依赖脚本按 GPL-3.0 分发，见根 [README](../../README.md)「许可证」与 [LICENSES/](../../LICENSES/)。

**板端（PYNQ 2.7 镜像已就绪时）**

- PYNQ 镜像自带 Python 3.8/NumPy 1.17.4 与 gcc 9.3.0；构建板端 C 守护进程直接 `make`，不需要额外 pip 安装。
- 系统服务由发布安装脚本安装；需要 root 权限时沿用镜像已有机制，不在命令行拼明文口令（[工程陷阱](../memory/04-engineering-pitfalls.md) §8）。

### 2.4 构建入口与命令（全部为仓库现有脚本/文档）

| 目标 | 命令（在仓库根目录） | 产物 | 说明 |
|---|---|---|---|
| 合并产品 Overlay（AWG+LCD+RF 合并 bit） | `python scripts/build_bitstream.py` | `awg/awg.runs/impl_1/host_bd_wrapper.bit`、`awg/host_bd.hdf`、`build_manifest.json` | 脚本检测 `awg/awg.xpr` 缺失时先执行 `scripts/bootstrap_project.tcl`；脚本自带打印"完整重建（约 10–20 分钟）"；支持 `--force`、`--low-memory`；可选 `SDK_ELF` 归档裸机 ELF |
| 合并 bit 校验 | `python scripts/check_bitstream.py` | 校验输出 | 核对 manifest 与 bit 一致性；下载/发布前必跑 |
| 合并 bit 的 CI 构建 | GitHub Actions：[awg-bitstream.yml](../../.github/workflows/awg-bitstream.yml) | `awg/…` + 构建诊断 | 需自托管 runner（Vivado 2025.2）、外部 Hog 固定版本、license secrets；超时 180 min；构建后跑 `scripts/check_bitstream.py` + [ci/verify_bitstream_ci.py](../../ci/verify_bitstream_ci.py) |
| 独立 PYNQ Overlay | `vivado -mode batch -source pynq_overlay/build.tcl` | `pynq_overlay/out/ledbtn.bit`、`ledbtn.hwh` | 脚本内含 synth/impl 检查，失败即报错；产物名沿用板端历史名 `ledbtn`（见 [build.tcl](../../pynq_overlay/build.tcl) 头注释） |
| 独立 RF Overlay | `vivado -mode batch -source rf_overlay/build.tcl` | `rf_overlay/out/rf_overlay.bit`、`rf_overlay.hwh` | 脚本显式检查 WNS/WHS ≥ 0，未收敛即失败 |
| 板端 C 守护进程（armv7l） | 板端 `make -C rf_overlay/sw/board_app`；或主机驱动 `python scripts/rf_build_daemon.py --out rf_overlay/out/rf_daemon` | `rf_daemon` + `rf_daemon.build.json` / `.build.log` | 板端原生 gcc 构建；主机/Windows 不能产出 armv7l；ARM 构建**不保证整文件逐字节可复现**（DWARF 记录编译目录；历史实测两次构建 131,479 字节不同而 `.text/.rodata/.data` 逐字节相同，见脚本文件头） |
| 主机原生测试（C） | `make -C rf_overlay/sw/board_app host-test`（及 `host-test-core`、`host-test-iq`、`host-test-protocol`、`host-test-daemon`、`host-test-sleep`、`host-test-headroom` 等） | 测试可执行文件与 PASS/FAIL 行 | 需 POSIX `sh`（Git Bash）；`test_vendor_integrity.py` 是脚本不是 pytest 模块 |
| 发布打包（需已有构建收据） | `python scripts/package_merged_overlay.py --receipt <merged_build_receipt.json> --output <release-dir> --daemon <rf_daemon> --daemon-version <v> --protocol-version <n>`；或 `python scripts/rf_repackage.py --parent <既有release> --daemon <rf_daemon> --output <dir>` | 不可变 release 目录（bit/HWH/daemon/manifest/收据） | **需要 `merged_build_receipt.json`；本仓库只有消费方、没有从零生成它的脚本**（见 §2.7） |
| 发布安装/读回/试部署 | `bash pynq_overlay/sw/install.sh`（需 `LEDBTN_SOURCE` 指向已校验 release、服务已停）；`python scripts/rf_teaching_install_state.py`；`sh scripts/rf_teaching_trial_install.sh` | 板端固定目标 `awg_merged`（安装器写死的绝对路径见 `install.sh`）与新服务单元；读回/回退收据 | 安装器有固定目标路径、拒绝已存在 release、要求先停服务；试部署与回退语义见 [试部署 runbook](../rf/deployment-trial-runbook.md) |
| SDR 测试台（可选） | `.\sdr_testbench\run.ps1 probe` / `offline` / `rx` | `sdr_testbench/results/<时间戳>/` | 默认不发射；TX/回环需真实接线确认与限时窗口；本机解释器分工见 [README](../../sdr_testbench/README.md) |
| 模型训练全流程（可选） | `ml_training\.venv\Scripts\python.exe ml_training\run.py all`（Linux 用 `.venv/bin/python`） | `ml_training/artifacts/`、`ml_training/evaluation/reports/`、`pipeline-summary.json` | 数据集不在仓库，由 `generate` 步骤生成；仿真准确率不等于板卡准确率（[ml_training/README.md](../../ml_training/README.md)） |

> 命令与参数必须以上述脚本的 `--help`/源码和对应文档为准；不要从旧日志猜参数，也不要手工调用 `tools/rebuild_bitstream.tcl`（其头注释说明必须由 `scripts/build_bitstream.py` 调用，否则可能漏掉 OOC 综合）。

### 2.5 预期产物

| 产物 | 路径（相对仓库根） | 大小/身份（据文档记载） | 备注 |
|---|---|---|---|
| 合并 bit | `awg/awg.runs/impl_1/host_bd_wrapper.bit` | 本机构建 `76e242fe…`，4,045,691 B；交付 bit 载荷逐字节相同、文件级 sha 不同（`d5bfe5d9…`） | 交付 bit 身份见 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §3.1 |
| 合并 HDF | `awg/host_bd.hdf` | `b1f3ff9c…`，501,465 B | 旧 SDK/HDF 产物，非 PYNQ Overlay |
| 构建清单 | `build_manifest.json` | 仓库内已有（`git_sha`/`part`/`rtl_hash`/`bitstream_sha256`/`timing`） | `toolchain.vivado` 历史值为 `null`（缺口，见 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §6 G11） |
| 独立 PYNQ Overlay | `pynq_overlay/out/ledbtn.bit` + `.hwh` | 历史 `ledbtn.bit` sha `07dcfb52…`（2026-09-13） | 本仓库不含该产物 |
| 独立 RF Overlay | `rf_overlay/out/rf_overlay.bit` + `.hwh` | 历史 `rf_overlay.bit` sha `b6a73e4d…`（2026-09-19） | 本仓库不含该产物 |
| 板端 daemon | `rf_overlay/out/rf_daemon` + `.build.json` + `.build.log` | 本地历史工件（`out/` 被 `.gitignore` 排除） | 板端原生构建；整文件不保证逐字节可复现 |
| 发布 release | `package_merged_overlay.py` / `rf_repackage.py` 的 `--output` 目录 | 含 `awg_merged.bit/.hwh`、`rf_daemon`、`merged_release_manifest.json`、`merged_build_receipt.json` | 本仓库不含发布归档 |
| SDR 结果 | `sdr_testbench/results/<run-id>/` | `result.json`、`iq.npy`、示波器 PNG 等 | `results/` 不入库 |
| 模型产物 | `ml_training/artifacts/`、`ml_training/evaluation/reports/` | 权重 JSON、metadata、评估报告 | `datasets/artifacts/.venv` 不入库 |

### 2.6 运行方式

**离线/主机**

```powershell
# Python 测试（强烈建议带仓库内 --basetemp，避免系统临时目录权限问题）
python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-overlay pynq_overlay/sw/tests
python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-scripts scripts/tests
python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-host host/tests
python -m unittest discover -s tools -p test_project_maintenance.py -v

# 主机 C 测试（Git Bash 的 sh.exe 在 PATH 下）
make -C rf_overlay/sw/board_app host-test
python rf_overlay/sw/board_app/test/test_vendor_integrity.py

# 辅助 AWG PC 上位机（与 RF 产品无关；唯一 PC 入口）
python host/main.py
```

**板端（PYNQ 2.7，需板卡与授权）**

1. 将**同一次构建**的 bit/HWH 拷到板上（独立 Overlay 用于隔离实验；**同一套 PL 只能有一个重配置拥有者**——已有服务持有时不要从调试脚本再次 `Overlay()`，先按项目流程交接拥有权）。
2. 安装/切换发布：按 [试部署 runbook](../rf/deployment-trial-runbook.md) 或 `install.sh`（需先停 `pynq-status.service`、`rf-daemon.service`；`LEDBTN_SOURCE` 指向已校验 release；安装器使用固定板端目标目录，见 `install.sh`）。
3. 读回：`python scripts/rf_teaching_install_state.py`（只读；核对 current/active、服务状态、RF 安全态、`boot_id`）。
4. 硬件/接收测试：按 [boardtest](../rf/boardtest.md)（L1 控制面 → L2 → L3，判据先于测量）与对应 runbook；未实测项保持 `NOT_RUN`。

**SDR（可选，外接）**：`run.ps1 probe → offline → rx`；TX/回环须真实接线确认与限时窗口，RF 输出未验证（[SDR 实测报告](../../sdr_testbench/TEST-REPORT.md)）。

**模型（可选）**：`ml_training/run.py all`（自建 venv；数据集生成 + 训练 + 评估 + 推理示例）。

### 2.7 缺失的数据/模型/位流与再生成路径

| 缺失项 | 为什么缺 | 再生成路径 | 可复现性限制 |
|---|---|---|---|
| 交付 bit / HWH 与 Vivado 报告 | 二进制/大文件不入库（[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §1.2、§7.2） | 用 §2.4 的三个构建入口重跑并归档报告 | 交付 bit 与提交源码不保证同源（[积压分诊](../reports/2026-09-30-backlog-triage.md) §0）；5 个第三方 RTL 许可未决，公开副本不能完整重建 PL（[UNKNOWN-PROVENANCE](../../LICENSES/UNKNOWN-PROVENANCE.md)） |
| `merged_build_receipt.json`（发布打包前置） | 该收据由历史构建/候选流水线产生，未随仓库；仓库内只有消费方（`package_merged_overlay.py`、`rf_repackage.py`、`merged_release.py`） | 从既有发布归档/候选包取得，或按 `merged_release.py` 读取的 schema 重建并经一次审查确认 | **本仓库没有从零生成它的脚本**——这是"从零打包到板载发布"的硬缺口 |
| 六类生产模型 `rf_model.json` + `rf_model_evaluation.json` | 发布归档内容，不在源码副本 | 从发布归档取得；或用 `ml_training` 重新训练并按其导出流程生成（文件名/字段须与 `merged_release.py` 的模型合同对齐）；`arm_probe/model` 与 `pynq_overlay/sw/modrec4_artifacts` 的 JSON 权重不能直接替代（合同/类表不同） | 重训结果不保证与历史模型逐字节一致；仿真准确率不是板卡准确率 |
| 原始 IQ、实验收据、原始日志 | 隐私与体积原因不入库（[DELIVERY-INDEX.md](DELIVERY-INDEX.md) §9） | 有板卡与授权时按采集工具重新采集；离线自测可用仓内小型参考向量（`ml_training/fourclass_test_vectors/`） | 依赖原始证据的历史测试在公开副本上会失败；不得把缺证据当产品失败（见 §2.8） |
| `ml_training/datasets/`、`artifacts/`、`.venv` | 体积/环境产物不入库 | `ml_training/run.py all` 重新生成；venv 按 README 自建 | 数据集由脚本生成，不是缓存；重训耗时未记录 |
| 板端 SSH/部署辅助封装 | 属个人环境工具，不随仓库 | 用自有 SSH/Paramiko 封装；按 [runbook](../rf/deployment-trial-runbook.md) 的"不另起 ssh/scp 凭据流程"原则统一 | 仓库内脚本假定既有连接方式，直接照抄历史命令可能不可用 |
| 五寸屏/触摸/现场照片证据 | 图片默认不入库 | 现场按 [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) 重新验收 | 真人验收 `NOT_RUN`，不由仓库替代 |

### 2.8 验证步骤（顺序、预期与解释）

1. **源码完整性与命名**：按 [README](../../README.md)「目录对应关系」核对三条流水线与 `skills/`（指南 `skill/` 映射见 [skill/README.md](../../skill/README.md)）；本仓库导出成员约 1,392 个文件（本次只读统计，公开副本脱敏后略有差异）。
2. **主机 Python 测试**：先跑 `pynq_overlay/sw/tests`、`scripts/tests`、`host/tests`（命令见 §2.6）。注意：**公开副本不含 `evidence/`，部分测试会因缺依赖证据失败**——历史基线记录过 `pynq_overlay/sw/tests` 5 failed / 11 errors、`scripts/tests` 62 failed / 80 errors 的情形，其中 155 项为源证据未复制、0 项为产品代码失败（[离线基线报告](../reports/2026-10-13-final-prj-baseline.md) §方法；迁移受控证据后的基线为 2056 passed / 5 skipped 与 496 passed / 1 skipped，[2026-10-14 报告](../reports/2026-10-14-deepseek-board-and-p0-followup.md)）。**先区分"缺证据失败"与"产品失败"**。
3. **主机 C 测试**：在 Git Bash 可见 `sh.exe` 的 `PATH` 下运行 `make -C rf_overlay/sw/board_app host-test`；另跑 `python rf_overlay/sw/board_app/test/test_vendor_integrity.py`（脚本）。历史基线示例：`test_rf_core` 98/0、`test_rf_protocol` 58/0、`test_rf_sleep` 22/0、HEADROOM 148/0、vendor integrity 10 files passed（版本相关，以本次实际输出为准；两条 selftest FAIL 为预期负控，收尾有 `SELFTEST HARNESS OK`）。
4. **工具自检**：`python -m unittest discover -s tools -p test_project_maintenance.py -v`（导出/归档/隐私规则）。
5. **构建**：按 §2.4 逐个构建并用 `scripts/check_bitstream.py`/`ci/verify_bitstream_ci.py` 校验；构建日志中的 WNS/WHS 与资源数按 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) 口径记录（内部时序通过**不等于**外部 RF DDR 接口满足，见该文 §3.5）。
6. **板端**：若具备板卡与授权——先只读读回（发布/服务/`boot_id`/存储/RF 安全态），再按 runbook 做一次试部署/回退；`TRIAL_ACCEPTED`/`TRIAL_ROLLED_BACK` 之外一律记 UNKNOWN，不重试（[开发纪律](../memory/03-development-discipline.md) §5、§8）。
7. **SDR（可选）**：先 `probe`/`offline` 再 `rx`；TX 只在真实接线确认与限时窗口下执行；保存结果与停止读回（[SDR 实测报告](../../sdr_testbench/TEST-REPORT.md)）。
8. **模型（可选）**：按 [ml_training/README.md](../../ml_training/README.md) 重训/评估；仿真准确率不写成板卡准确率，ARM/板端结果另记。

### 2.9 已知限制（复现边界）

- **本仓库不是可安装包**：缺 bit/HWH、发布归档、模型/数据集、原始 IQ 与收据；旧报告里的 SHA-256 不能用本仓库文件验证。
- **交付 bit 不能从提交历史保证重建**：构建时存在未提交修改；见 [积压分诊](../reports/2026-09-30-backlog-triage.md) §0 与 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §7.2。
- **完整 PL 重建有法务阻塞**：5 个第三方参考 RTL 无许可声明，公开副本不能完整重建 PL（[UNKNOWN-PROVENANCE](../../LICENSES/UNKNOWN-PROVENANCE.md)）。
- **从零打包链缺一环**：`merged_build_receipt.json` 在仓库内没有生成器（§2.7），因此"从零源码 → 可安装发布"不能仅凭本仓库完成。
- **外部 RF DDR 接口时序未约束**：内部 WNS 为正不证明 AD9361 采样/输出裕量（[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §3.5）。
- **性能指标不能跨候选移植**：历史 ARM P95/RSS 属各自候选/日期；当前交付版本未重测（[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §4.1）。
- **文档/索引类材料有版本时效**：`docs/reports/` 与 `CLAUDE.md` 中的"当前/最新"按日期理解；冲突时以原始收据为准。
- **本轮未执行**：没有重新综合、没有板卡操作、没有补采、没有重新安装/训练；本文所有"可复现"结论只指**命令与源码入口存在**，不指本轮验证过。

### 2.10 时间与空间粗估

| 项 | 粗估 | 依据/说明 |
|---|---|---|
| 源码副本 | 约 **1,392 文件 / 17.6 MB**（本次只读统计导出成员；公开副本脱敏后略有差异） | `tools/project_maintenance.py` 的 `source_files` 成员统计（只读） |
| Vivado/Vitis 2025.2 安装 | **GB 级**（官方安装包，未在本轮测量） | 按官方安装；不在此给精确数字 |
| Radioconda / 训练 venv | **GB 级 / 数百 MB 级**（按官方与 torch CPU 轮子；未在本轮测量） | 可选依赖 |
| 合并 bit 构建 | **约 10–20 分钟** | [build_bitstream.py](../../scripts/build_bitstream.py) 自述"完整重建（约 10–20 分钟）" |
| 独立 PYNQ Overlay 构建 | **约 10.4 分钟**（本机历史日志：2026-09-13 19:52:47→20:03:10） | `pynq_overlay/build.log`（未入库，`*.log` 被忽略；本次只读读取） |
| 独立 RF Overlay 构建 | **约 8.3–8.8 分钟**（本机历史日志 2026-09-17 多次构建） | `rf_overlay/logs/build{3,4,5}.log`（未入库，同上） |
| 每次构建的工程磁盘占用 | 独立 PYNQ/RF Vivado 工程目录各约 **50 MB**（本机实测 50.9/47.9 MB） | `pynq_overlay/vivado`、`rf_overlay/vivado`（`.gitignore` 排除）；合并工程未在本副本保留，**未实测** |
| 合并构建机器门槛 | CI 检查默认要求可用内存 **20 GiB**（工作流覆盖为 **12 GiB**）、有效 CPU ≥1（工作流设 2） | [check_vivado_runner.py](../../ci/check_vivado_runner.py)、[CI 工作流](../../.github/workflows/awg-bitstream.yml) |
| 离线测试 | **分钟级**（未在本轮重新计时） | 按套件规模；历史基线见 §2.8 |
| 模型生成/训练 | **未取得**（仓库未记录耗时） | 需按实际机器实测；不虚构分钟数 |
| 板端会话 | 只读读回**分钟级**；真实采集/研究按授权预算（如一次会话固定窗口数/次数） | 报告与 runbook 的时限；本轮未操作板卡 |

## 3. 与指南 3.3.4 / 3.3.5 的对应

### 3.1 指南 3.3.4 优化目标

| 指南条目 | 本文/配套材料 | 状态 | 缺口与说明 |
|---|---|---|---|
| 在目标器件上取得可测量性能表现，并给出与基线的对比 | §1.4（历史候选受控配对）；[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §4（上板/ARM 指标）；[DELIVERY-INDEX.md](DELIVERY-INDEX.md) §4 | **部分** | 当前交付版本仍"未取得"（§1.3）；历史配对不可移植；需 §1.6-A/B 补测 |
| 合理使用片上资源，避免以显著资源代价换有限性能收益 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §3（资源/时序/功耗）；§1.5（RRC 内存换时延的权衡） | **部分** | 合并目标只有一次实现，无资源基线（§1.5 第 5 行）；"合理"缺对照 |
| 保证工程可由他人从零复现 | §2 全文；[README](../../README.md)「构建与运行」；[README](../../README.md)「赛事指南对照」 | **部分** | 源码/命令入口齐全；位流/模型/数据/发布收据缺失（§2.7）；第三方 RTL 许可与 `merged_build_receipt.json` 缺口（§2.9） |
| 将设计过程中的判断依据写进报告，而不只是呈现结果 | §1.1（配对口径）、§1.6（方案与验收）、§2.9（限制）；[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §5.3；[开发纪律](../memory/03-development-discipline.md) §1 | **已有（文档层面）** | 判据先于测量、失败保留已写入报告；但历史指标原始收据不在仓库 |
| 将与大模型协作中获得的经验提炼为他人可复用的技能包 | [skills/README.md](../../skills/README.md)（三套技能：范围/方法/失效条件/来源/已验证效果）；[AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md) | **已有（文档层面）** | 跨项目/跨板卡/陌生队伍复用**尚未验证**（见技能 README §4.4、§11） |

### 3.2 指南 3.3.5.1 工程包

| 指南条目 | 本文/配套材料 | 状态 | 缺口与说明 |
|---|---|---|---|
| 完整工程文件与源码 | 仓库目录（三条流水线 + `ml_training/` + `sdr_testbench/` + `scripts/`） | **已有** | 第三方 RTL 许可未决（不属本文范围，见 [LICENSES](../../LICENSES/)） |
| 仿真或验证结果，说明功能正确性如何确认 | [src/tb/](../../src/tb/)、[rf_overlay/tb/](../../rf_overlay/tb/)、[pynq_overlay/tb/](../../pynq_overlay/tb/)；Python/C 测试套件（§2.6） | **部分** | 缺统一 RTL 仿真入口与结果记录（G03，见 [GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md)） |
| 综合与实现报告（资源/时钟/关键性能） | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §2–§3（含本地原件路径） | **部分** | 原始 `.rpt`/DCP 不在仓库；外部 RF 接口未约束（该文 §3.5） |
| 上板工程、运行脚本、实测输出与参考比对 | §2.4/§2.6（安装/读回/试部署/boardtest）；[DELIVERY-INDEX.md](DELIVERY-INDEX.md) §4 | **部分** | 实测原始输出与收据不在仓库；历史安装读回不代表当前板态 |
| 可复现的构建脚本 | §2.4 全部入口；`build_manifest.json`；[CI 工作流](../../.github/workflows/awg-bitstream.yml) | **已有（入口层面）** | 构建依赖外部工具链/Hog/license；本轮未重跑构建 |

### 3.3 指南 3.3.5.3 设计报告

| 指南条目 | 本文/配套材料 | 状态 | 缺口与说明 |
|---|---|---|---|
| 优化过程，含优化前后的性能与资源对比表 | §1（含可用配对、排除项、最小补测方案） | **部分** | 当前交付版本未取得（§1.3）；历史配对见 §1.4；资源类无前后（§1.5） |
| 复现说明：一份可供他人从零开始完整执行的操作步骤 | §2（环境/依赖/构建/产物/运行/缺失项/验证/限制/粗估） | **部分** | 受 §2.7/§2.9 限制：不能仅凭公开副本产出可安装发布；`merged_build_receipt.json`、模型/数据/位流需外部来源或重生成 |
| 大模型协作记录（提示词/回答/纠错/工作流） | [AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md)（K1–K3 + 工作流 + 提示词骨架 + 技能提炼） | **已有（文档层面）** | 改写摘要，不发布完整私人会话；原始会话不在仓库 |
| 技能包提炼过程 | [skills/README.md](../../skills/README.md) §7/§8；[AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md) §5 | **已有（文档层面）** | 跨项目验证"尚未验证" |

## 4. 本文不证明什么

- 不证明**当前交付版本**存在优化收益或达标：该版本的优化前后配对为"未取得"（§1.3）；§1.4 是历史候选的受控 A/B，且其后未在优化后产品候选上复测 ARM。
- 不证明本文引用的指标可在本仓库复算：原始收据、原始 IQ、原始构建日志与报告不在仓库（§1.4、[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §1.2）。
- 不证明资源使用"最优"或"合理"：合并目标只有一次实现，无优化前基线（§1.5）。
- 不证明能从本仓库单独重建交付 bit 或得到可安装发布：缺位流/HWH、模型/数据、`merged_build_receipt.json` 生成器与第三方 RTL 许可（§2.7、§2.9）。
- 不证明外部 RF DDR 接口满足 AD9361 时序：接口未约束、板级 skew 未表征（[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §3.5）。
- 本篇未执行任何构建、板端操作、补采或安装；“可复现”只指命令与源码入口存在。

## 附录：引用与回查

1. **先查仓库文档**：本文所有仓库相对路径可直接打开；文档自身也声明原始收据不在仓库，不能复算。
2. **再回本地原件**：`<LOCAL_EVIDENCE>/…` 的定义同 [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §1.2；本文涉及的本地原件主要是：
   - `rf-teaching/s2-46db-modrec4-perf-arm-2026-10-21/result/`（§1.4 全部数字与 parity）；
   - `rf-teaching/s2-46db-modrec4-product-2026-10-21/PERFORMANCE-DECISION.md`（口径澄清与产品化决定）；
   - `rf-teaching/s2-46db-p3-rrc-perf-2026-10-24/PERF-REPORT.md`、`rf-teaching/s2-46db-p3-rrc-candidate-2026-10-24/CANDIDATE-REPORT-FINAL.md`（§1.5 RRC 数据）；
   - `rf-teaching/s2-46db-modrec4-arm-2026-10-21/result/ARM-FULLPATH-RESULT.md`（板端 CPython/NumPy 版本与 full-path 口径）。
3. **引用规则**：数值原文 + 条件/候选/日期 + 出处路径 + 证据层级；不得把 `NOT_RUN`/`UNKNOWN` 改写成通过；不得把有限数据移植到当前版本。

> 维护提示：若后续按 §1.6 补测或新增构建，需同步更新本文 §1、[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §5–§6 与 [GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md) 的 G01/G05 状态；本轮不改其它文件。
