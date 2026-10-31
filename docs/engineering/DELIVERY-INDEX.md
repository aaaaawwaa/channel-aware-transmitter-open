# 交付材料索引

本文是公开仓库的阅读地图：先读什么、哪些结论有文档出处、哪些是失败与限制、原始证据在哪里（以及为什么不在仓库里）。索引只引用仓库内已有文件；**不新增指标、不换算数字、不把 `NOT_RUN` 写成通过**。

维护规则：新增或移动材料时同步更新本页；每条给出仓库相对路径。指标类引用必须带"文档记载"的限定语与出处，因为原始收据和原始 IQ 不在本仓库。

## 1. 建议阅读顺序

| 顺序 | 材料 | 说明 |
|---|---|---|
| 1 | [README](../../README.md) | 项目目标、硬件与工具版本、目录、构建入口、已验证能力与限制。 |
| 2 | [SHARE-README.md](../../SHARE-README.md) | 本仓库的定位声明：脱敏文本副本，不是可安装发布包，不是原始验收证据。 |
| 3 | [工程结构](PROJECT-STRUCTURE.md) | 三条流水线（RF / PYNQ / 辅助 AWG）的目录职责与构建入口。 |
| 4 | [AI 协作案例](AI-COLLABORATION-CASE.md) | 本项目的多代理协作、复审、门禁与失败教训（有出处）。 |
| 5 | [设计报告](DESIGN-REPORT.md) | 面向评审的架构、关键设计决定、接收/发射/发布链路与已知限制。 |
| 6 | [综合与实现指标](SYNTHESIS-METRICS.md) | 资源/时序/时钟/功耗、上板与 ARM 性能、优化前后对照与缺口（指标均带证据层级）。 |
| 7 | [竞赛提交材料与符合性说明](COMPETITION-MATERIALS.md) | 材料清单、与指南 3.3/作品简介的对应、无法证明的条目、匿名与隐私处理。 |
| 8 | [指南 3.3 符合性映射](GUIDE-CONFORMANCE.md) | 指南逐条要求 → 材料 → 状态 → 缺口；含目录对照与评分权重对应。 |
| 9 | [证据包索引](EVIDENCE-PACK.md) | 当前交付版本的脱敏证据摘要、原件位置与 SHA-256、payload_equal 绑定、功耗估计值口径。 |
| 10 | [最终设计报告](FINAL-DESIGN-REPORT.md) | 面向提交评审的简洁版；含系统框图、软硬件分工、关键指标、板上结果、复现与真实限制。 |
| 11 | [海报/PPT 文案](POSTER-AND-PPT.md) / [性能证据摘要](PERFORMANCE-EVIDENCE.md) | 英文海报/PPT 文案与一页性能证据（含功耗估计值限定与历史优化案例边界）。 |
| 12 | [演示与提交清单](DEMO-AND-SUBMISSION.md) | PlutoSDR TX → 板卡 RX1 的拍摄步骤、现场核对表、证据分级与最终提交清单。 |
| 13 | 按目标进入下方分节 | 设计、构建、能力、限制、模型、SDR、技能、证据边界。 |

## 2. 设计说明与工程规则

| 材料 | 说明 |
|---|---|
| [工程结构](PROJECT-STRUCTURE.md) | 目录职责表、构建路线、为什么不做整体搬迁。 |
| [系统结构图](system-architecture.md) | 外部 SDR 激励与板卡接收的功能框图（PL/PS 分工、外部信号源与显示边界）。 |
| [分享、隐私与可恢复清理](SHARING-AND-CLEANUP.md) | 脱敏导出工具的边界、白名单、恢复命令；解释为何脱敏副本不能验证旧摘要。 |
| [2026-10-30 工程整理记录](2026-10-30-ORGANIZATION-REPORT.md) | 本轮整理的实绩与保留边界。 |
| [原 AWG 说明归档](awg-legacy-guide.md) | 辅助 AWG 流水线的历史操作说明（与 RF 产品无关，产物不混用）。 |
| [开发审查规则](../requirements/development-review-policy.md) | R0/R1/R2 分级、单次增量审查、试部署闭环。 |
| [作品简介追踪表](../requirements/brief-traceability.md) | B1–B10 逐句追踪（成文 2026-10-14；QPSK/QAM 接收状态已有后续报告推进，引用前看原文日期）。 |
| [验收清单 P1–P6](../requirements/acceptance-checklists-p1-p6.md) | 六类门（离线→ARM→数字回环→有线物理→真人→发布）与当前状态。 |
| [RF 状态对象契约](../requirements/rf-status-object-contract.md) | release_id / capture_id / 连接代次 / 采样域 / 模型版本的失效规则。 |
| [工程记忆索引](../memory/README.md) | 权威来源指针；记忆本身不是证据。 |
| [工程地图](../memory/01-project-map.md) / [门禁模型](../memory/02-gate-model.md) / [开发纪律](../memory/03-development-discipline.md) / [踩坑 16 条](../memory/04-engineering-pitfalls.md) | 协作方式、状态阶梯、纪律与真实事故。 |
| [证据索引](../memory/05-evidence-index.md) / [未决问题](../memory/06-open-questions.md) / [授权约定](../memory/07-authorization-conventions.md) | 收据索引、12 条未决项、授权登记规则。 |
| [RF 后端契约](../rf/backend_contract.md) / [兼容性](../rf/compatibility.md) / [版本矩阵](../rf/version_matrix.md) | AD9361 迁移的契约、硬件兼容证据与工具链决策。 |
| [上板测试流程](../rf/boardtest.md) | 判据先于测量、缺项不开工的 L1/L2/L3 流程。 |
| [试部署与回滚 runbook](../rf/deployment-trial-runbook.md) | 单次审查、试部署、回滚、回滚后回滚的终态与退出码。 |
| [人工验收指南（2026-10-26）](../rf/2026-10-26-human-acceptance-guide.md) | H01–H11 现场验收项与对外表述建议。 |
| [冻结合同与规格](../superpowers/specs/) | 如 [TX 模式合同](../superpowers/specs/2026-10-13-rf-tx-mode-contract.md)、[环境诊断判据冻结](../superpowers/specs/2026-10-14-s2-environment-diagnosis-contract-freeze.md) 等。 |
| [实施计划与路线](../superpowers/plans/) | 如 [final_prj 实施计划](../superpowers/plans/2026-10-13-final-prj-implementation-plan.md)、[RF 接收路线](../superpowers/plans/2026-10-03-rf-receiver-roadmap.md)。 |
| [设计报告](DESIGN-REPORT.md) | 面向评审的架构、关键设计决定、接收/发射/发布链路与已知限制。 |
| [综合与实现指标](SYNTHESIS-METRICS.md) | 合并/RF/PYNQ 三个构建目标的资源、时序、时钟、功耗与实测；优化前后对照为“未取得同口径基线”。 |
| [指南 3.3 符合性映射](GUIDE-CONFORMANCE.md) | 指南逐条映射、推荐目录对照、文件命名规则与缺口/人工确认清单。 |
| [技能包入口](../../skills/README.md) | 三套技能的适用范围、使用方法、失效条件、来源与已验证效果；指南 `skill/` 入口见 [skill/README.md](../../skill/README.md)。 |
| [大模型协作记录](AI-COLLABORATION-RECORD.md) | 3 个真实案例的提示词→回答→纠错→结果、智能体工作流与技能提炼过程。 |
| [report/ 入口](../../report/README.md) | 指南 `report/` 目录映射（设计报告＋协作记录），正文仍在 `docs/engineering/`。 |
| [证据包索引](EVIDENCE-PACK.md) / [docs/evidence/](../evidence/README.md) | 交付版本证据身份（bit/DCP/payload_equal）与三份实现摘要；原件不在仓库，摘要不可复算。 |
| [优化对照与从零复现](OPTIMIZATION-AND-REPRODUCTION.md) | 同口径优化前后基线核查与最小补测方案；工具链/依赖/构建/产物/缺失项复现说明。 |
| [RF 接口时序缺口](RF-INTERFACE-TIMING.md) | AD9361 源同步 DDR 的未约束端口、参数来源、计算方法与板级/离线最小方案；不宣称接口通过。 |
| [文件名例外咨询文字](FILENAME-CONSULTATION.md) | 命名规则现状、惯例例外与可直接发送给赛道的咨询文字（尚未发送）。 |
| [最终设计报告](FINAL-DESIGN-REPORT.md) / [系统框图](../figures/rf-system-architecture-poster-4x3.png) | 面向提交评审的简洁版；系统框图已作为白名单图片收入公开仓库。 |
| [海报/PPT 文案](POSTER-AND-PPT.md) | AMD 海报英文短文、PPT 分页短句与讲稿要点（不虚构数据）。 |
| [性能证据摘要](PERFORMANCE-EVIDENCE.md) | 交付位流绑定的资源/时钟/时序/功耗估计与板上有限结果一页摘要。 |
| [演示与提交清单](DEMO-AND-SUBMISSION.md) | 现场演示拍摄步骤、核对表、证据分级与最终提交清单（报名后台由用户完成）。 |

## 3. 构建、测试与运行入口

| 目标 | 入口（命令见 README 与对应文档） |
|---|---|
| 辅助 AWG 位流 | `python scripts/build_bitstream.py`（入口 [scripts/build_bitstream.py](../../scripts/build_bitstream.py)） |
| 独立 PYNQ Overlay | `vivado -mode batch -source pynq_overlay/build.tcl`（[build.tcl](../../pynq_overlay/build.tcl)） |
| 独立 RF Overlay | `vivado -mode batch -source rf_overlay/build.tcl`（[build.tcl](../../rf_overlay/build.tcl)） |
| 合并产品打包 | [scripts/package_merged_overlay.py](../../scripts/package_merged_overlay.py)、[pynq_overlay/sw/merged_release.py](../../pynq_overlay/sw/merged_release.py) |
| 构建清单 / CI | [build_manifest.json](../../build_manifest.json)（2026-09-24 构建记录）、[.github/workflows/awg-bitstream.yml](../../.github/workflows/awg-bitstream.yml)（Vivado 2025.2 自托管 runner） |
| 板端 C 原生构建与主机测试 | [rf_overlay/sw/board_app/Makefile](../../rf_overlay/sw/board_app/Makefile)（`make` / `make host-test`） |
| 板载界面与发布安装 | [pynq_overlay/sw/install.sh](../../pynq_overlay/sw/install.sh)、[pynq_overlay/sw/ui_view.py](../../pynq_overlay/sw/ui_view.py) |
| 主机 C 测试与 Python 测试 | `rf_overlay/sw/board_app/test/`、`pynq_overlay/sw/tests/`、`scripts/tests/`、`host/tests/` |
| 维护与脱敏导出 | [tools/project_maintenance.py](../../tools/project_maintenance.py)（`plan` / `archive` / `export` / `restore`）；[从零复现说明](OPTIMIZATION-AND-REPRODUCTION.md) |
| 模型训练全流程 | [ml_training/run.py](../../ml_training/run.py)、[ml_training/README.md](../../ml_training/README.md) |
| 外接 SDR 测试台 | [sdr_testbench/README.md](../../sdr_testbench/README.md)、[run.ps1](../../sdr_testbench/run.ps1) |
| SDR + 板卡联调准备 | [sdr_testbench/board_integration/README.md](../../sdr_testbench/board_integration/README.md) |

## 4. 已验证能力（按文档记载，非仓库内可复算）

> 说明：下列结论均来自本仓库文档的记述；原始收据、原始 IQ 与运行日志在开发机 `evidence/`，未随仓库。每行都保留原文的限定语。

| 结论（据文档记载） | 出处 | 层级/边界 |
|---|---|---|
| 去 QAM64 交付版独立复审结论："产品缩减及 BPSK/QPSK 本轮演示证据 LIMITED APPROVED；完整交付闭环声明 CHANGES_REQUIRED" | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) | 文档级复审结论 |
| 12 帧选用集：BPSK 两 ON 均 0/138，QPSK 两 ON 均 0/128；QAM16 分别 1/256 与 0/256；QAM64 移除 9 项负例 9/9 通过 | [同上](../reviews/2026-10-26-codex-delivery-closeout-review.md) | 有限帧冒烟 + 离线反例，不是整体验收 |
| 发布安装：`4814bbc4…` 已安装，只读读回 current/active 一致、双服务 active、RF 安全态成立 | [同上](../reviews/2026-10-26-codex-delivery-closeout-review.md) | 安装面；引用的是当时读回 |
| 有限发布 `b82b141e…` 安装：`INSTALLED_VERIFIED`、`accepted=true`、`new_rf_frames=0`、`real_initializations=0`；父版本保留回退 | [作品简介追踪表](../requirements/brief-traceability.md) | `RELEASED`（有限域） |
| 试部署 + 自动回滚：注入退出 42 后自动恢复父版本，独立读回服务/RF 安全态/`boot_id`/存储正常 | [回滚收尾报告](../reports/2026-10-17-trial-rollback-board-closeout.md)、[runbook](../rf/deployment-trial-runbook.md) | `BOARD_VERIFIED`，仅限该试部署/回滚范围 |
| ARM 完整计算历史记录：100 次 P95 258.6409 ms / 272.785 ms / 278.835 ms（不同候选；RSS、判等条件见原文） | [证据索引](../memory/05-evidence-index.md)、[作品简介追踪表](../requirements/brief-traceability.md) | `ARM_VERIFIED`；按候选与日期引用，不得移植到新候选 |
| 离线基线：迁移受控证据后 `pynq_overlay/sw/tests` 2056 passed / 5 skipped，`scripts/tests` 496 passed / 1 skipped（退出码 0） | [2026-10-14 报告](../reports/2026-10-14-deepseek-board-and-p0-followup.md) | 离线；依赖被忽略二进制齐备 |
| 主机 C 原生套件：core 98/0、protocol 58/0、sleep 22/0、HEADROOM 148/0、vendor integrity 10 files passed 等 | [离线基线报告](../reports/2026-10-13-final-prj-baseline.md) | 主机 MinGW，不是 ARM/板端 |
| QPSK/RECT 离线推进：实际上板 5 帧 QI 显式、sync=true、BER 0/128；离线测试多组通过 | [2026-10-23 QPSK 离线报告](../reports/2026-10-23-p3-qpsk-offline.md) | 有限帧；单帧零错不等于低 BER 总体能力 |
| 外部 PlutoSDR 测试台：设备识别、离线单音、RX 样本流、TX 停止读回成立；**RF 输出未验证**（示波器带宽不足） | [SDR 测试报告](../../sdr_testbench/TEST-REPORT.md)、[README](../../sdr_testbench/README.md) | 有线 RF 回环未完成 |
| 辅助 AWG 黄金基线：`test_bridge.py COM11` 230 PASS、`scope_verify.py COM11` 61/61 PASS | [golden/BASELINE.md](../golden/BASELINE.md) | 历史 AWG 基线，与 RF 产品无关 |
| 人工/照片：功能项与实体屏照片部分闭合，触摸时延 NOT_MEASURED | [S1 照片复核](../reports/2026-10-04-s1-human-photo-review.md) | 不把照片当当前状态；触摸 P95 未测 |

## 5. 失败、限制与未完成项（必须保留）

| 项 | 状态/说明 | 出处 |
|---|---|---|
| QAM16 非零误码 | 12 帧选用集中 1/256；属受限演示，不能倒填成零误码验收 | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| 完整交付闭环 | 复审为 `CHANGES_REQUIRED`：验收语义漂移、试部署重派超预算、文档与日志不一致 | [同上](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| 外部 SDR BPSK 历史统计 | 23/30 帧同步，整体未通过；不能只报同步成功帧零误码 | [README](../../README.md) |
| QAM64 | 已移出本轮交付；仅保留历史报告与负结果 | [README](../../README.md) |
| S2 正式 108 物理帧 + 12 离线负控 | `NOT_RUN` | [验收清单](../requirements/acceptance-checklists-p1-p6.md)、[未决问题](../memory/06-open-questions.md) |
| 46 dB HEADROOM 真实研究 | `NOT_RUN`（曾被 `lease_held` 谓词阻塞；真实帧需单次授权） | [安装门与预检](../reports/2026-10-11-headroom-install-gate-and-preflight.md)、[未决问题](../memory/06-open-questions.md) |
| EVM | `NOT_IMPLEMENTED`（代码与界面显式标注未接入） | [作品简介追踪表](../requirements/brief-traceability.md) |
| 多制式实时处理 | PL 未做实时同步解调/RRC/多制式处理；PS 侧只测过完整计算路径 | [作品简介追踪表](../requirements/brief-traceability.md) |
| 档位选择/信道感知决策 | 档位注册表与策略 `NOT_IMPLEMENTED`；自动执行永久关闭，建议只进草稿 | [作品简介追踪表](../requirements/brief-traceability.md)、[ui_model.py](../../pynq_overlay/sw/ui_model.py) |
| 触摸时延 | `NOT_MEASURED`（用户已取消专门计时，不作为阻塞项） | [作品简介追踪表](../requirements/brief-traceability.md) |
| 真人现场验收 | 未完成；由用户现场确认，AI 不代签 | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) |
| 存储健康/MMC 根因 | `NOT_ESTABLISHED`；完整 FS 检查 `NOT_RUN` | [存储审计报告](../reports/2026-10-12-storage-audit-and-deploy-recovery.md) |
| RF 源同步 DDR 接口时序未覆盖 | `rf.xdc` 缺 input/output delay；内部 WNS/WHS 只覆盖已约束路径；板级未测，不得宣称接口通过 | [RF-INTERFACE-TIMING](RF-INTERFACE-TIMING.md) |
| 功耗为工具估计 | `2.418 W` 等来自 Vivado `report_power` 估算；无板级功耗/电流实测 | [EVIDENCE-PACK](EVIDENCE-PACK.md)、[merged-overlay 摘要](../evidence/merged-overlay-2026-09-24.md) |
| 优化前后对比 | `未取得` 同版本同条件基线；最小补测方案见文档，本轮未执行 | [OPTIMIZATION-AND-REPRODUCTION](OPTIMIZATION-AND-REPRODUCTION.md) |

## 6. 模型与训练说明

| 材料 | 说明 |
|---|---|
| [ml_training/README.md](../../ml_training/README.md) | 总说明：边界（只做离线调制识别）、一条命令全流程、数据/模型/评估契约、两轮结果与未运行项。 |
| [ml_training/docs/CONTRACTS.md](../../ml_training/docs/CONTRACTS.md) | 跨组冻结契约：标签、IQ 格式、预处理、划分、评估口径。 |
| [ml_training/run.py](../../ml_training/run.py) | `generate/train/evaluate/infer/all` 全流程 CLI。 |
| [ml_training/model/cnn.py](../../ml_training/model/cnn.py)、[numpy_runtime.py](../../ml_training/model/numpy_runtime.py) | 小型 IQ-CNN 与纯 NumPy 推理运行时。 |
| [ml_training/reports/first-round-report.md](../../ml_training/reports/first-round-report.md)、[round2/reports/round2-report.md](../../ml_training/round2/reports/round2-report.md) | 两轮报告（含失败案例与未运行项）。 |
| [ml_training/arm_probe/README.md](../../ml_training/arm_probe/README.md) | 独立 ARM 回放包（纯 NumPy，非产品端到端资格）。 |
| [pynq_overlay/sw/rf_cnn_runtime.py](../../pynq_overlay/sw/rf_cnn_runtime.py) | 板端六类 CNN 推理 runtime（与训练侧预处理一致）。 |

**注意**：`ml_training/datasets/`、`ml_training/artifacts/`、`ml_training/.venv/` 未随仓库；准确率与回放需按 `run.py all` 自建环境重训复现，不能"下载即推理"。

## 7. SDR / GNU Radio 测试台

| 材料 | 说明 |
|---|---|
| [sdr_testbench/README.md](../../sdr_testbench/README.md) | 入口、设备识别、可复现命令、默认信号与停止行为。 |
| [TEST-REPORT.md](../../sdr_testbench/TEST-REPORT.md) / [verification.txt](../../sdr_testbench/verification.txt) | 2026-10-19 实测报告与原始验证输出：离线单音、RX 样本流、TX 停止读回；**RF 输出未验证**。 |
| [radio_test.py](../../sdr_testbench/radio_test.py) / [scope_test.py](../../sdr_testbench/scope_test.py) | 手写 GNU Radio 流图与示波器联动脚本。 |
| [GNU-RADIO-DEVELOPMENT.md](../../sdr_testbench/GNU-RADIO-DEVELOPMENT.md) | 从流图、驱动 API 到异常停止的开发经验。 |
| [gui_demo/README.md](../../sdr_testbench/gui_demo/README.md) | BPSK GUI 演示（默认本地基带预览；实际发射需接线确认与限时窗口）。 |
| [board_integration/README.md](../../sdr_testbench/board_integration/README.md) | 板卡↔Pluto 联调准备：离线预检、接线、电平预算、停止条件。 |

运行环境不同：GNU Radio 流图需要 Radioconda（Python 3.12 + gr-iio），示波器脚本用系统 Python + PyVISA；两套解释器不能混用（详见 README）。

## 8. 技能库（skills/）

完整入口、每项技能的适用范围/使用方法/失效条件/来源/已验证效果见 [skills/README.md](../../skills/README.md)（指南 `skill/` 入口映射见 [skill/README.md](../../skill/README.md)）。

| 技能 | 用途 |
|---|---|
| [final-prj-rf-development](../../skills/final-prj-rf-development/SKILL.md) | 本项目 RF 构建、接收、外部 SDR、证据与交付边界。 |
| [pynq-linux-development](../../skills/pynq-linux-development/SKILL.md) | Overlay/AXI、systemd、触屏界面、部署与回退；面向 PYNQ 框架的通用技能（跨项目验证尚未做）。 |
| [pluto-gnuradio-testbench](../../skills/pluto-gnuradio-testbench/SKILL.md) | 外接 PlutoSDR/GNU Radio 基础测试台与可移植模板。 |

技能只记录方法与边界，不保存个人凭据，不新增硬件操作授权（见 [README](../../README.md)）。

## 9. 证据边界：原始材料在哪里

- **不在本仓库**：原始 IQ、`evidence/**` 收据与 `stage-checkpoint.json`、`results.json`、运行日志、位流/HWH/ELF、训练数据集与模型产物、图片/照片/截图、发布归档、参考大文件、`.local/` 私有备份、Git 历史与 `dist/` 导出包。
- **为什么**：体积与可复现性之外，更重要的原因是隐私与完整性：图片未做 OCR 脱敏、收据是原始字节、旧摘要只能对应旧原件。见 [SHARE-README.md](../../SHARE-README.md)、[SHARING-AND-CLEANUP.md](SHARING-AND-CLEANUP.md)、[SHARE-MANIFEST.json](../../SHARE-MANIFEST.json)。
- **因此**：本仓库内所有指标只能作为"文档记述"引用；旧报告里的 SHA-256 摘要不能用本仓库文件验证；本仓库不是可安装发布包，不能直接部署到板卡。
- **原始证据在哪**：开发机 `evidence/` 目录（未公开）。需要复核具体结论时，应回到原始收据与对应源码摘要；本索引不替代它们。
- **公开脱敏摘要**：`docs/evidence/` 下是按原件人工转录的脱敏摘要（含原件 SHA-256 与差异声明），可在不接触开发机的情况下核对证据身份；[EVIDENCE-PACK](EVIDENCE-PACK.md) 是索引。摘要字节 ≠ 原件，不能用于复算原件哈希。

## 10. AI 协作案例

见 [AI-COLLABORATION-CASE.md](AI-COLLABORATION-CASE.md)：7 个有出处的事件（测试通过但原因错误、退出码失真、读取失败被当成无错误、假硬件盲区、结论时效性、交付复审发现的语义漂移、测试有效性工程），以及可复用的硬规则和"本篇不证明什么"。

另见 [AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md)：按指南 3.3.5.3 组织的 3 个真实案例（提示词/任务 → 模型回答 → 错误或反馈 → 纠正 → 结果与证据），以及智能体工作流与可复用提示词骨架。

## 11. 许可证与引用

本仓库是混合许可，按目录/文件分区（完整清单见 [NOTICE](../../NOTICE) 与 [LICENSES/README.md](../../LICENSES/README.md)）：

- **自有代码与文档：MIT**，见 [LICENSE](../../LICENSE)。
- **Analog Devices AD9361 驱动与参考应用**：`LicenseRef-ADI-AD9361`（ADI 自定义 BSD 风格，含专利与 field-of-use 条款），全文见 [LICENSES/Analog-Devices-AD9361-BSD-style.txt](../../LICENSES/Analog-Devices-AD9361-BSD-style.txt)。
- **依赖 GNU Radio/PyQt5 的测试台脚本（5 个）**：GPL-3.0，全文见 [LICENSES/GPL-3.0.txt](../../LICENSES/GPL-3.0.txt)。GNU Radio Companion 生成物不随仓库。
- **出处未知的第三方参考工程 RTL（5 个）与 1 个 Vitis 生成桩**：无许可声明，不授予任何许可、不属于 MIT，是公开再分发前的未决阻塞项，见 [LICENSES/UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)。
- 引用本仓库材料时，请保留出处路径与"文档记载"的证据层级说明；不要把有限冒烟写成整体验收通过。

## 12. 维护本索引

- 新增/移动文档后，用 `Test-Path`/等价方式检查链接目标存在。
- 引用指标时同时写：数值原文、出处路径、证据层级（文档/离线/ARM/板端/真人）。
- 不把 `NOT_RUN`、`UNKNOWN`、`NOT_ESTABLISHED`、`NOT_MEASURED` 改写成"通过"。
