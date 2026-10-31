# 竞赛提交材料与符合性说明

维护日期：2026-10-31。本文用于把仓库内已有材料对应到**项目内记录的“作品简介”要求**，并如实列出**目前无法证明满足**的条目。它不替代赛事官方文件。

> **重要声明（必须保留）**
> 1. 当前工作环境与仓库中**没有找到赛事官方的赛道提交要求原文或评分表**；因此本文只能按项目内记录的《作品简介》（[brief-traceability.md](../requirements/brief-traceability.md)，B1–B10）与已有的交付复审、人工验收指南进行对齐。
> 2. **本文不作为“已满足某赛道全部要求”的声明**。若官方要求与本文不同，应在提交前按官方文件重新核对并更新本页；无法核实的条目保持“未确认”。
> 3. 所有实测数字只引用仓库内文档的记述；原始收据与原始 IQ 不在仓库内，不能在本仓库中复算。
> 4. 报名后台的仓库链接登记是**独立步骤**；本仓库公开不等于报名提交完成。

## 1. 提交材料清单（本仓库内）

| 材料 | 位置 | 状态 |
|---|---|---|
| 项目入口与范围说明（含硬件/工具版本、构建步骤、能力与限制） | [README.md](../../README.md) | 已有 |
| 设计报告 | [DESIGN-REPORT.md](DESIGN-REPORT.md) | 已有（本轮整理） |
| AI 协作案例（多代理协作、复审与失败教训） | [AI-COLLABORATION-CASE.md](AI-COLLABORATION-CASE.md) | 已有 |
| 实测材料索引（按类别列出可引用的文档指标与出处） | [DELIVERY-INDEX.md](DELIVERY-INDEX.md) | 已有 |
| 源码、构建与测试脚本 | `rf_overlay/`、`pynq_overlay/`、`scripts/`、`host/`、`src/`、`sdk/` | 已有（不含位流/HWH 与原始证据） |
| 模型与训练说明 | `ml_training/`、[ml_training/README.md](../../ml_training/README.md) | 已有（不含数据集/venv，需重训复现） |
| SDR / GNU Radio 测试台 | `sdr_testbench/`、[README](../../sdr_testbench/README.md) | 已有（RF 输出未验证） |
| 可复用技能 | `skills/` | 已有（3 套 24 个文件） |
| 许可证与第三方声明 | [LICENSE](../../LICENSE)、[NOTICE](../../NOTICE)、[LICENSES/](../../LICENSES/) | 已有；5 个第三方 RTL 许可未确认（如实披露） |
| 失败、限制与未完成项 | [README](../../README.md)「失败与限制」、[DELIVERY-INDEX](DELIVERY-INDEX.md) §5、[未决问题](../memory/06-open-questions.md) | 已有 |

## 2. 与“作品简介”要求的对应（B1–B10）

> 状态词沿用工程定义（`OFFLINE_VERIFIED` / `ARM_VERIFIED` / `BOARD_VERIFIED` / `HUMAN_VERIFIED` / `RELEASED` / `NOT_RUN` / `NOT_IMPLEMENTED` / `UNVERIFIED`），见 [brief-traceability.md](../requirements/brief-traceability.md) §1。该表成文于 2026-10-14；其后 QPSK/QAM16 等的推进以 2026-10-23/24 报告和 2026-10-26 交付复审为准。

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
| B9 三层验证 | `loopback_criteria.py`、`scripts/rf_capture_*`、[上板测试流程](../rf/boardtest.md) | 数字回环与有线物理回环有限工况 `BOARD_VERIFIED`；开发物理帧计数在历史收据中 | 正式 S2 108 物理帧 + 12 离线负控 `NOT_RUN`；未按全模式/全干扰类别闭环 |
| B10 发布与可复现 | `merged_release.py`、`install.sh`、[试部署 runbook](../rf/deployment-trial-runbook.md) | 有限域 `RELEASED`（候选安装/试部署/回滚有记录） | 统一发布清单 `NOT_RUN`；旧摘要不能由本仓库验证 |

## 3. 实测材料索引（引用规则）

- 入口：[DELIVERY-INDEX.md](DELIVERY-INDEX.md) §4（已验证能力）与 §5（失败/限制）。
- 关键报告：[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[试部署回滚](../reports/2026-10-17-trial-rollback-board-closeout.md)、[QPSK 离线/板上](../reports/2026-10-23-p3-qpsk-offline.md)、[离线基线](../reports/2026-10-13-final-prj-baseline.md)、[SDR 测试报告](../../sdr_testbench/TEST-REPORT.md)。
- 引用时须同时给出：数值原文、出处路径、证据层级（离线/ARM/板端/真人）。**不得**把“文档记述”写成“可复算”，**不得**把有限帧零错写成整体误码率。

## 4. 当前对外表述（与人工验收指南一致）

> “平台已实现基于Zynq与AD9361的有限快照收发、BPSK/QPSK及QAM16接收分析、频谱/星座/参考误码展示与轻量模型结果显示。QAM16存在已记录误码，QAM64移出本次交付；RRC真实链路、EVM和自动档位选择未完成全项验收。外部SDR独立发射接收链正在复验。”

出处：[人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §四。原简介保留为目标描述，不将当前缩减演示版标成原简介全项通过。

## 5. 无法证明或未确认的事项（必须如实列出）

1. **官方赛道要求未知**：仓库内没有官方提交要求/评分表原文，无法证明“符合官方全部条目”。需要所有者按实际赛道文件逐条核对。
2. **完整物理验收未完成**：S2 正式 108 物理帧 + 12 离线负控、真实环境闭环、46 dB HEADROOM 研究均 `NOT_RUN`。
3. **指标限制**：QAM16 非零误码；外部 SDR BPSK 历史统计 23/30 帧同步，整体未通过；QAM64 已移出交付。
4. **未实现功能**：EVM、PL 实时多制式处理、档位注册表/自动选档、统一接收结果契约外的多制式实时链路。
5. **真人验收**：仅部分功能/照片闭合，触摸 P95 未测；现场演示需用户确认，AI 不代签。
6. **第三方许可**：5 个第三方参考工程 RTL 的再分发许可未确认（见 [LICENSES/UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)）；本仓库保留其来源披露，不代表已获授权。
7. **可安装性**：本仓库不含位流/HWH、原始 IQ、数据集、模型产物与发布归档，不能直接部署或直接复现历史指标。
8. **可复现性**：ml_training 需自建环境并重训；部分历史测试依赖未随包的证据/二进制。
9. **匿名性**：GitHub 账号 `aaaaawwaa` 本身可能与个人身份关联（账号资料、域名注册等），仓库内匿名处理不能消除账号层面的关联；如赛道要求双盲/匿名评审，需另行确认规则并调整提交方式。
10. **报名登记**：报名后台的仓库链接/材料登记流程未执行。

## 6. 匿名与隐私处理（本仓库）

- 文本、文件名与文档元数据：导出时替换个人姓名、手机号、个人邮箱、本机用户名/绝对路径、远端账户、设备序列号/VISA 资源串、私网地址、现场网络配置、凭据写法与授权别名；不包含图片（因此不存在图片 OCR 脱敏问题）、原始日志与原始收据。
- Git 提交身份：使用 GitHub noreply 邮箱（`192326095+aaaaawwaa@users.noreply.github.com`），不含个人姓名/私人邮箱；仓库为单次初始提交，不导入旧历史。
- 仓库描述与 README 不包含个人联系方式、学校/单位、住址或设备现场配置。
- **仍然存在**：GitHub 账号名/头像/账号资料由平台公开；若评审要求完全匿名，请按赛道规则使用独立匿名账号或由主办方指定渠道提交。

## 7. 提交前检查清单

- [ ] 按官方赛道文件逐条核对本文 §2/§5，更新“未确认”项。
- [ ] 确认 README 的“当前交付范围/失败与限制”与演示现场一致。
- [ ] 确认现场演示只用已声明配置（BPSK/QPSK 有限快照 + 受限 QAM16），不演示未实现能力。
- [ ] 确认第三方 RTL 的许可状态与公开范围符合赛事规则；如不允许，从公开副本移除并说明 PL 不可完整重建。
- [ ] 确认提交身份/仓库匿名要求（如需要）。
- [ ] 完成报名后台的链接与材料登记（独立于 GitHub 可见性）。
