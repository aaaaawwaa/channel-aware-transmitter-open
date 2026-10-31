# 竞赛提交材料与符合性说明

维护日期：2026-10-31（本轮按 2026 FPGA 赛道 AMD 指南 3.3 修订）。本文把仓库内材料对应到**赛事指南 3.3 自主选题赛道的要求**，并如实列出**目前无法证明满足**的条目。它不替代赛事官方文件、报名后台信息与主办方解释。

> **重要声明（必须保留）**
> 1. **本文依据**：本轮提供的《全国大学生嵌入式芯片与系统设计竞赛 2026 选题指南（AMD）》**3.3 自主选题赛道**（PDF 第 22–25 页，PDF 内页码 19–22）。官方 PDF 与本地提取文本均不随仓库发布，本仓库只保留整理后的映射与引用。**官方解释权归主办方**；赛程、提交节点与报名细则以官网公布及报名后台为准（指南 3.3.6，官网 <http://www.fpgachina.cn/>）。
> 2. **本文不作为“已满足某赛道全部要求”的声明**。逐条映射、缺口与需人工确认项见 [GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md)；若官方要求与本文不同，以官方为准并在提交前更新本页。
> 3. 所有实测数字只引用仓库内文档的记述；原始收据与原始 IQ 不在仓库内，不能在本仓库中复算。本轮新增 [EVIDENCE-PACK.md](EVIDENCE-PACK.md) 与 [docs/evidence/](../../docs/evidence/README.md) 脱敏摘要，用于核对"原件应为哪个文件、哪个 SHA-256"；摘要为人工转录、字节与原件不同，**不能用于复算原件**。
> 4. 报名后台的仓库链接登记是**独立步骤**；本仓库公开不等于报名提交完成。

## 1. 提交材料清单（本仓库内）

| 材料 | 位置 | 对应指南条款 | 状态 |
|---|---|---|---|
| 项目入口与范围说明（含硬件/工具版本、构建步骤、能力与限制、证据/复现入口） | [README.md](../../README.md) | 3.3.1、3.3.2、3.3.3.1、3.3.3.2、3.3.5.3 | 已有（本轮更新链接） |
| 指南 3.3 逐条符合性映射（含缺口与人工确认项） | [GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md) | 3.3.1–3.3.5.5 | 已有（本轮更新：46 条映射、19 项缺口、13 项人工确认） |
| 设计报告 | [DESIGN-REPORT.md](DESIGN-REPORT.md) | 3.3.5.3 | 已有（本轮更新：增加证据包/优化/接口时序入口） |
| AI 协作案例（多代理协作、复审与失败教训） | [AI-COLLABORATION-CASE.md](AI-COLLABORATION-CASE.md) | 3.3.3.1、3.3.5.3 | 已有 |
| 大模型协作记录（提示词→回答→纠错→结果＋智能体工作流） | [AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md) | 3.3.3.1、3.3.5.3 | 已有（本轮新增） |
| 综合/实现/实测指标与优化对照 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) | 3.3.4、3.3.5.1、3.3.5.3 | 已有（内部标注"优化前后对比未取得同口径基线"） |
| 交付版本脱敏证据包索引（身份/绑定/证据清单/功耗估计值口径） | [EVIDENCE-PACK.md](EVIDENCE-PACK.md) | 3.3.4、3.3.5.1 | 已有（本轮新增；证据为脱敏摘要，原件在开发机） |
| 脱敏实现与板级证据摘要（5 份＋索引） | [docs/evidence/](../../docs/evidence/README.md)：[merged Overlay](../evidence/merged-overlay-2026-09-24.md)、[RF Overlay](../evidence/rf-overlay-2026-09-19.md)、[PYNQ Overlay](../evidence/pynq-overlay-2026-09-13.md)、[bit 绑定](../evidence/bitstream-binding-2026-10-26.md)、[板级结果](../evidence/board-results-2026-10-26.md) | 3.3.4、3.3.5.1 | 已有（本轮新增；原始报告/位流不在仓库，摘要不可复算） |
| 优化前后对照与从零复现说明 | [OPTIMIZATION-AND-REPRODUCTION.md](OPTIMIZATION-AND-REPRODUCTION.md) | 3.3.4、3.3.5.1、3.3.5.3 | 已有（本轮新增；当前交付优化前后＝`未取得`，历史受控配对不可移植，最小补测方案未执行） |
| RF 源同步 DDR 接口时序缺口、参数来源与最小方案 | [RF-INTERFACE-TIMING.md](RF-INTERFACE-TIMING.md) | 3.3.5.1 | 已有（本轮新增；板级测量 `NOT_RUN`，不得宣称接口通过） |
| 文件名例外说明与赛道咨询文字 | [FILENAME-CONSULTATION.md](FILENAME-CONSULTATION.md) | 3.3.5.4 | 已有（本轮新增；4 条问题尚未发送） |
| 实测材料索引（按类别列出可引用的文档指标与出处） | [DELIVERY-INDEX.md](DELIVERY-INDEX.md) | 3.3.4、3.3.5.1 | 已有 |
| 源码、构建与测试脚本 | `rf_overlay/`、`pynq_overlay/`、`scripts/`、`host/`、`src/`、`sdk/` | 3.3.5.1 | 已有（不含位流/HWH 与原始证据） |
| 模型与训练说明 | `ml_training/`、[ml_training/README.md](../../ml_training/README.md) | 3.3.5.1 | 已有（不含数据集/venv，需重训复现） |
| SDR / GNU Radio 测试台 | `sdr_testbench/`、[README](../../sdr_testbench/README.md) | 3.3.5.1 | 已有（RF 输出未验证） |
| 可复用技能 | `skills/` | 3.3.3.1、3.3.5.2 | 已有（三套技能 24 个文件＋入口 README，共 25 个文件） |
| 技能包入口说明（适用范围/使用方法/失效条件/已验证效果） | [skills/README.md](../../skills/README.md) | 3.3.5.2 | 已有（本轮新增） |
| 指南 `skill/` 目录入口映射 | [skill/README.md](../../skill/README.md) | 3.3.5.2、3.3.5.4 | 已有（指向 `skills/`，不复制副本） |
| 指南 `report/` 目录入口（设计报告＋协作记录） | [report/README.md](../../report/README.md) | 3.3.5.3、3.3.5.4 | 已有（指向 `docs/engineering/`，不复制副本） |
| 许可证与第三方声明 | [LICENSE](../../LICENSE)、[NOTICE](../../NOTICE)、[LICENSES/](../../LICENSES/README.md) | 3.3.3.3 | 已有；5 个第三方 RTL 许可未确认（如实披露） |
| 失败、限制与未完成项 | [README](../../README.md)「失败与限制」、[DELIVERY-INDEX](DELIVERY-INDEX.md) §5、[未决问题](../memory/06-open-questions.md) | 3.3.4、3.3.5.1、3.3.5.3 | 已有 |

## 2. 与“作品简介”要求的对应（B1–B10）

> 状态词沿用工程定义（`OFFLINE_VERIFIED` / `ARM_VERIFIED` / `BOARD_VERIFIED` / `HUMAN_VERIFIED` / `RELEASED` / `NOT_RUN` / `NOT_IMPLEMENTED` / `UNVERIFIED`），见 [brief-traceability.md](../requirements/brief-traceability.md) §1。该表成文于 2026-10-14；其后 QPSK/QAM16 等的推进以 2026-10-23/24 报告和 2026-10-26 交付复审为准。**作品简介是项目内部要求，不等于赛事要求；赛事条款级对照见 §2A。**

| 简介条目 | 责任材料 | 当前状态（据文档） | 不能证明/风险 |
|---|---|---|---|
| B1 平台：Zynq + AD9361 + 五寸触摸屏 | `rf_overlay/`、`pynq_overlay/`、[README](../../README.md) | `RELEASED`（有限域，b82 候选安装收据）；PL/PS/界面在有限工况下可用 | 未做全项作品级统一发布清单；历史安装收据不代表当前板态 |
| B2 TX 模式：BPSK/QPSK/QAM + RRC | `pynq_overlay/sw/rf_iq_signal.py`、[TX 模式合同](../superpowers/specs/2026-10-13-rf-tx-mode-contract.md) | 软件生成 `OFFLINE_VERIFIED`；QAM64 已移出；**实物仅 BPSK 3 MSym/s RECT 有历史有线证据** | QPSK/QAM16 的 AD9361 实物输出、RRC 实物链路 `NOT_RUN`；QAM16 有已记录误码 |
| B3 RX/同步解调 | `rf_bpsk_receiver.py`、`rf_qpsk_receiver.py`、[QPSK 报告](../reports/2026-10-23-p3-qpsk-offline.md) | BPSK `BOARD_VERIFIED`（有限快照）；QPSK/RECT 有限帧板上冒烟（QPSK 5 帧 BER 0/128） | 连续接收/多速率未实现；QAM 接收仅有限/受限证据 |
| B4 星座/频谱/EVM/BER/数据 | `rf_rx_analysis.py`、`rf_rx_constellation.py`、`rf_bpsk_receiver.py` | 星座/频谱/削顶/RMS `BOARD_VERIFIED`（限 BPSK 域）；BER 由独立 PRBS 参考计算 | **EVM `NOT_IMPLEMENTED`**；无协议帧/CRC 载荷查看；单帧零错不等于低 BER 总体能力 |
| B5 PL/PS 分工 | `rf_overlay/rtl/`、`rf_overlay/sw/board_app/`、`pynq_overlay/sw/rf_status_worker.py` | PL 有限样点收发/采集 + PS 控制/分析/轻量推理（ARM 历史 P95 在文档中记录） | PL **未**实现实时同步解调/RRC/多制式；PS 性能只测过完整计算路径 |
| B6 触摸与同帧展示 | `pynq_overlay/sw/pynq_touch.py`、`ui_view.py`、[S1 照片复核](../reports/2026-10-04-s1-human-photo-review.md) | 软件点击路径 `BOARD_VERIFIED`；真人实体屏功能项部分闭合 | 触摸时延 `NOT_MEASURED`；多制式/RRC 界面未完成 |
| B7 环境识别与可信度 | `rf_environment.py`、`rf_headroom.py`、[环境诊断判据](../superpowers/specs/2026-10-14-s2-environment-diagnosis-contract-freeze.md) | 离线/ARM 资格已验证；窗口内标签与具名理由可用 | 真实环境闭环 `NOT_RUN`；46 dB 研究 `NOT_RUN`；结论仅限采样窗口 |
| B8 决策/人工接管/回退 | `ui_model.py`（自动执行永久关闭）、`rf_teaching_profile.py`、[runbook](../rf/deployment-trial-runbook.md) | 建议只填草稿、停止/解除/回退链有历史证据（`CODED`+部分 `BOARD_VERIFIED`） | **档位注册表/策略 `NOT_IMPLEMENTED`**；无已证明收益的第二档位；不得称“自动选档” |
| B9 三层验证 | `rf_overlay/sw/loopback_criteria.py`、`scripts/rf_capture_*`、[上板测试流程](../rf/boardtest.md) | 数字回环与有线物理回环有限工况 `BOARD_VERIFIED`；开发物理帧计数在历史收据中 | 正式 S2 108 物理帧 + 12 离线负控 `NOT_RUN`；未按全模式/全干扰类别闭环 |
| B10 发布与可复现 | `merged_release.py`、`install.sh`、[试部署 runbook](../rf/deployment-trial-runbook.md) | 有限域 `RELEASED`（候选安装/试部署/回滚有记录） | 统一发布清单 `NOT_RUN`；旧摘要不能由本仓库验证 |

## 2A. 与指南 3.3 主要条款的对应（摘要）

> 详细逐条映射（46 条）、目录对照与评分权重对应见 [GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md)。

| 指南条款 | 要求（摘要） | 主要对应材料 | 状态 | 详见 |
|---|---|---|---|---|
| 3.3.1 / 3.3.1.1 | 完整 FPGA 作品；按学历分组；器件范围 | [README](../../README.md)、[DESIGN-REPORT](DESIGN-REPORT.md) | 器件已有；**组别资格需人工确认** | §2.1 |
| 3.3.2 | 选题目由、软硬件协同、PYNQ 推荐、通用 PYNQ Skill 加分 | `pynq_overlay/`、`skills/` | 部分具备 | §2.2 |
| 3.3.3.1 | 工具版本（2026.1 推荐/2025.2）；大模型协作记录；技能包 | [README](../../README.md)、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md)、`skills/` | 已有＋部分具备 | §2.3 |
| 3.3.3.2 | 板卡与器件限制、声明具体型号、板级验证 | [README](../../README.md)、[boardtest](../rf/boardtest.md) | 部分具备 | §2.4 |
| 3.3.3.3 | GitHub/Gitee 开源、允许再分发、MIT/Apache-2.0 推荐 | [LICENSE](../../LICENSE)、[NOTICE](../../NOTICE)、[LICENSES/](../../LICENSES/README.md) | 部分具备＋**需人工确认** | §2.5 |
| 3.3.4 | 可测性能与基线对比、资源合理、从零复现、判断依据、技能包 | [DELIVERY-INDEX](DELIVERY-INDEX.md)、[DESIGN-REPORT](DESIGN-REPORT.md)、[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md)、[EVIDENCE-PACK.md](EVIDENCE-PACK.md)、[OPTIMIZATION-AND-REPRODUCTION.md](OPTIMIZATION-AND-REPRODUCTION.md)、`build_manifest.json` | 部分具备（当前交付优化前后"未取得"） | §2.6 |
| 3.3.5.1 | 工程包：源码、仿真/验证、综合实现报告、上板与实测比对、构建脚本 | `rf_overlay/`、`pynq_overlay/`、`scripts/`、`src/tb/`、[EVIDENCE-PACK.md](EVIDENCE-PACK.md)、[docs/evidence/](../../docs/evidence/README.md)、[RF-INTERFACE-TIMING.md](RF-INTERFACE-TIMING.md) | 部分具备（证据摘要已入仓库；接口时序未测） | §2.7 |
| 3.3.5.2 | 技能包：`skill/` 目录＋README（适用范围/方法/失效条件/已验证效果） | `skills/`、[skills/README.md](../../skills/README.md) | 部分具备（缺跨项目验证） | §2.8 |
| 3.3.5.3 | 设计报告七要素 | [DESIGN-REPORT](DESIGN-REPORT.md)、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md)、[OPTIMIZATION-AND-REPRODUCTION.md](OPTIMIZATION-AND-REPRODUCTION.md) | 部分具备（当前交付优化对比待补） | §2.9 |
| 3.3.5.4 | 推荐目录（非强制）与文件命名规则 | [README](../../README.md)「赛事指南对照」、[FILENAME-CONSULTATION.md](FILENAME-CONSULTATION.md) | 部分具备；**命名例外待赛道答复** | §2.10、§3、§4 |
| 3.3.5.5 | 评测指标与权重（30/20/20/15/15） | 见 [GUIDE-CONFORMANCE](GUIDE-CONFORMANCE.md) §5 | 部分具备 | §2.11、§5 |

## 3. 实测材料索引（引用规则）

- 入口：[DELIVERY-INDEX.md](DELIVERY-INDEX.md) §4（已验证能力）与 §5（失败/限制）。
- **证据包与脱敏摘要**：[EVIDENCE-PACK.md](EVIDENCE-PACK.md)（交付身份、`payload_equal` 绑定、功耗估计值口径、证据清单）与 [docs/evidence/](../../docs/evidence/README.md)（三份实现摘要＋板级结果摘要）。摘要是人工转录、**不可复算**；引用时须同时给原件 SHA-256 与"文档记载"限定语。
- **优化对照**：[OPTIMIZATION-AND-REPRODUCTION.md](OPTIMIZATION-AND-REPRODUCTION.md) §1.3（当前交付版本 `未取得` 同版本同条件基线）、§1.4（历史候选 `b3cca2ac…` 受控配对**仅作方法示例、不可移植**）、§1.6（最小补测方案，本轮未执行）。
- **RF 接口时序**：[RF-INTERFACE-TIMING.md](RF-INTERFACE-TIMING.md)：`rf.xdc` 缺 input/output delay；板级 skew/TX 生成时钟/runtime IDELAY 未测；**不得宣称接口通过**。
- 关键报告：[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[试部署回滚](../reports/2026-10-17-trial-rollback-board-closeout.md)、[QPSK 离线/板上](../reports/2026-10-23-p3-qpsk-offline.md)、[离线基线](../reports/2026-10-13-final-prj-baseline.md)、[SDR 测试报告](../../sdr_testbench/TEST-REPORT.md)。
- 引用时须同时给出：数值原文、出处路径、证据层级（离线/ARM/板端/真人）。**不得**把“文档记述”写成“可复算”，**不得**把有限帧零错写成整体误码率。

## 4. 当前对外表述（与人工验收指南一致）

> “平台已实现基于Zynq与AD9361的有限快照收发、BPSK/QPSK及QAM16接收分析、频谱/星座/参考误码展示与轻量模型结果显示。QAM16存在已记录误码，QAM64移出本次交付；RRC真实链路、EVM和自动档位选择未完成全项验收。外部SDR独立发射接收链正在复验。”

出处：[人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §四。原简介保留为目标描述，不将当前缩减演示版标成原简介全项通过。

## 5. 无法证明或未确认的事项（必须如实列出）

1. **官方解释权与报名信息**：本文以本轮提供的指南 3.3（PDF 第 22–25 页）为依据，但**官方解释权归主办方**；赛道、器材、组别资格与提交节点以**报名后台与官网**公布为准。报名后台的仓库链接/材料登记流程**未执行**。
2. **官方未强制目录结构**：指南 3.3.5.4 明确推荐目录“非强制”，采用其他组织方式须在 README 给出目录对照说明；本仓库未按推荐目录搬迁（见 [GUIDE-CONFORMANCE](GUIDE-CONFORMANCE.md) §3）。**文件命名**的字面要求与实际惯例名称（`README.md` 即指南自身示例）存在冲突；已准备 [FILENAME-CONSULTATION.md](FILENAME-CONSULTATION.md)（4 条问题，**尚未发送**），例外是否允许**未确认**。
3. **指南文本来源**：本轮依据为赛方提供的指南 3.3 节（PDF 第 22–25 页）；官方 PDF 与本地提取文本均不随仓库发布，排版/页码可能与官方 PDF 有细微差异；引用前应以官方 PDF 原文复核。
4. **完整物理验收未完成**：S2 正式 108 物理帧 + 12 离线负控、真实环境闭环、46 dB HEADROOM 研究均 `NOT_RUN`。
5. **指标限制**：QAM16 非零误码；外部 SDR BPSK 历史统计 23/30 帧同步，整体未通过；QAM64 已移出交付。
6. **未实现功能**：EVM、PL 实时多制式处理、档位注册表/自动选档、统一接收结果契约外的多制式实时链路。
7. **真人验收**：仅部分功能/照片闭合，触摸 P95 未测；现场演示需用户确认，AI 不代签。
8. **第三方许可**：5 个第三方参考工程 RTL 的再分发许可未确认（见 [LICENSES/UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)）；本仓库保留其来源披露，不代表已获授权。
9. **可安装性**：本仓库不含位流/HWH、原始 IQ、数据集、模型产物与发布归档，不能直接部署或直接复现历史指标。
10. **可复现性**：ml_training 需自建环境并重训；部分历史测试依赖未随包的证据/二进制。
11. **匿名性**：GitHub 账号 `aaaaawwaa` 本身可能与个人身份关联（账号资料、域名注册等），仓库内匿名处理不能消除账号层面的关联；如赛道要求双盲/匿名评审，需另行确认规则并调整提交方式。
12. **报名登记**：报名后台的仓库链接/材料登记流程未执行。
13. **技能包通用性**：通用 PYNQ Skill 是否满足“不与本队题目绑定、换题目换板卡仍可用”的加分口径**未确认**（尚未做跨项目验证）。
14. **评分为评委评定**：指南权重（30/20/20/15/15）为公开指标，实际评分含评委尺度；本文不预估分数、不声称任何一项已满分。
15. **本轮并行材料已生成**：`SYNTHESIS-METRICS.md`、`AI-COLLABORATION-RECORD.md`、`skills/README.md`、`EVIDENCE-PACK.md`、`docs/evidence/`（索引＋5 份摘要）、`OPTIMIZATION-AND-REPRODUCTION.md`、`RF-INTERFACE-TIMING.md`、`FILENAME-CONSULTATION.md` 均已存在并按 §1 链接引用；但各自带证据层级（部分指标来自本地原件/文档记述），引用时仍须按 [DELIVERY-INDEX](DELIVERY-INDEX.md) §9 的规则标注，不得因"文件存在"就当作可复算证据。
16. **命名例外答复未取得**：公开仓库当前 1404 文件/171 目录中有 **89 文件＋6 目录含大写**（中文/空格/其他特殊字符 0）；已准备 [FILENAME-CONSULTATION.md](FILENAME-CONSULTATION.md)（4 条问题，**尚未发送**）；在答复前不改名、不搬迁，以免破坏构建引用（→ GUIDE-CONFORMANCE §4、§6 G14、§7 H13）。
17. **RF 接口板级时序未测量**：`rf.xdc` 缺 `set_input_delay`/`set_output_delay`；全局 `no_input_delay` 10=9 HIGH+1 MEDIUM、`no_output_delay` 48=39 HIGH+9 MEDIUM；RF 子集 7 RX 输入＋8 TX 输出；`tx_clk_out_p` 需生成时钟；board skew/TX 生成时钟/runtime IDELAY tap 均未测；**内部 WNS +0.213/WHS +0.018 不覆盖接口，不得宣称接口通过**（见 [RF-INTERFACE-TIMING.md](RF-INTERFACE-TIMING.md)）。
18. **优化补测未执行**：当前交付版本没有同版本/同条件/同口径的优化前后数据（`未取得`）；历史候选 `b3cca2ac…` 的受控配对（五阶段 median 378.89→292.08 ms、P95 444.52→311.74 ms、modrec4 median 154.58→64.81 ms）**仅作方法示例、不可移植**；最小补测方案见 [OPTIMIZATION-AND-REPRODUCTION.md](OPTIMIZATION-AND-REPRODUCTION.md) §1.6。
19. **证据包为脱敏摘要，功耗为估计值**：[EVIDENCE-PACK.md](EVIDENCE-PACK.md) 与 [docs/evidence/](../../docs/evidence/README.md) 是人工转录摘要，字节与原件不同、不可复算；`2.418 W` 等功耗来自 Vivado `report_power` 估算，**不是板端实测**，也不证明散热/供电余量。

## 6. 匿名与隐私处理（本仓库）

- 文本、文件名与文档元数据：导出时替换个人姓名、手机号、个人邮箱、本机用户名/绝对路径、远端账户、设备序列号/VISA 资源串、私网地址、现场网络配置、凭据写法与授权别名；不包含图片（因此不存在图片 OCR 脱敏问题）、原始日志与原始收据。
- Git 提交身份：使用 GitHub noreply 邮箱（`192326095+aaaaawwaa@users.noreply.github.com`），不含个人姓名/私人邮箱；仓库为单次初始提交，不导入旧历史。
- 仓库描述与 README 不包含个人联系方式、学校/单位、住址或设备现场配置。
- **仍然存在**：GitHub 账号名/头像/账号资料由平台公开；若评审要求完全匿名，请按赛道规则使用独立匿名账号或由主办方指定渠道提交。另见 §5 第 11 条。

## 7. 提交前检查清单

- [ ] 按指南 3.3 逐条核对 [GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md) §2/§6，关闭或如实标注缺口。
- [ ] 复核本轮材料（`SYNTHESIS-METRICS.md`、`AI-COLLABORATION-RECORD.md`、`skills/README.md`、`EVIDENCE-PACK.md`、`docs/evidence/`（索引＋5 份摘要）、`OPTIMIZATION-AND-REPRODUCTION.md`、`RF-INTERFACE-TIMING.md`、`FILENAME-CONSULTATION.md`）在最终导出包中仍存在且链接有效，并按其内部证据层级引用。
- [ ] 确认"当前交付版本优化前后对比"仍标注 `未取得`；历史受控配对只作方法示例，不得移植；最小补测方案未执行不得写成已完成。
- [ ] 确认 RF 接口时序表述为"未测/未约束"，**不出现"接口时序通过"**；内部 WNS/WHS 不当作接口证据。
- [ ] 确认功耗数字标注为 Vivado `report_power` **估计值**，不是实测。
- [ ] 确认 README 的“当前交付范围/失败与限制”与演示现场一致。
- [ ] 确认现场演示只用已声明配置（BPSK/QPSK 有限快照 + 受限 QAM16），不演示未实现能力。
- [ ] 确认第三方 RTL 的许可状态与公开范围符合赛事规则；如不允许，从公开副本移除并说明 PL 不可完整重建。
- [ ] 确认文件命名策略：等待 [FILENAME-CONSULTATION.md](FILENAME-CONSULTATION.md) 的赛道答复；答复前**不改名、不搬迁**，答复后按 GUIDE-CONFORMANCE §6 G14 执行并同步 `SHARE-MANIFEST` 与互链。
- [ ] 确认提交身份/仓库匿名要求（如需要）。
- [ ] 完成报名后台的链接与材料登记（独立于 GitHub 可见性）。

## 8. 维护规则

- 新增或移动材料时同步更新 §1、§2A、§3 与 [GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md) 的映射与统计口径。
- 引用指标必须同时给出：数值原文、出处路径、证据层级；不把 `NOT_RUN`/`UNKNOWN`/`NOT_ESTABLISHED` 改写成通过。
- 证据类材料（EVIDENCE-PACK / docs/evidence / OPTIMIZATION / RF-INTERFACE-TIMING）只引用其中带"原件 SHA-256 + 证据层级"的条目；摘要不得当作原件。
- 官方指南更新时先更新 §0 依据说明，再逐条复核对齐。
