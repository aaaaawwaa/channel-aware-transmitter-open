# 指南 3.3 符合性映射：材料 → 指南条款 → 状态 → 缺口

维护日期：2026-10-31。本文把**全国大学生嵌入式芯片与系统设计竞赛 2026 FPGA 赛道选题指南（AMD）3.3 自主选题赛道**逐条映射到本仓库现有材料，并如实标出缺口与需人工确认项。它是内部材料整理工具，**不是**"已满足官方全部要求"的声明。

## 0. 依据、范围与状态词

- 依据：本轮提供的指南 3.3 节文本（PDF 第 22–25 页，PDF 内页码 19–22）。官方 PDF 与本地提取文本均不随仓库发布，本仓库只保留整理后的映射与引用。该节覆盖 3.3.1 赛道简介/组别、3.3.2 设计任务、3.3.3 设计与优化要求、3.3.4 优化目标、3.3.5 提交内容（工程包/技能包/设计报告/推荐目录/评测权重）、3.3.6 赛程、3.3.7 参考资源。
- 边界：**官方解释权、报名后台、赛道器材与组别信息以主办方与报名信息为准**（3.3.6 明确赛程与提交节点以官网 <http://www.fpgachina.cn/> 公布为准）。本文所有"已有/部分具备"只表示仓库内存在可指向的材料，不表示评委已认可或已通过验收。
- 状态词（全文统一）：
  - **已有**：仓库内已有可指向材料，且能覆盖该条的可核查要求（不代表赛事认可）。
  - **部分具备**：有相关材料或局部证据，但缺口明确，见 §6。
  - **未完成**：缺少交付物或该项未执行（含 `NOT_RUN`）。
  - **需人工确认**：取决于队伍资格、报名信息、官方解释或权利人授权，仓库不能自证。
- 统计口径：§2 主映射表共 **46 条**（3.3.1–3.3.5.5）；§3 目录对照 8 行；§5 权重对应 5 行。主映射表状态分布（截至本文维护时点，已计入本轮并行任务 A/B 生成的材料：`SYNTHESIS-METRICS.md`、`AI-COLLABORATION-RECORD.md`、`skills/README.md`，并计入 README 目录对照补齐后的 G13）：**已有 18 条 / 部分具备 24 条 / 未完成 1 条 / 需人工确认 3 条**；缺口（部分具备＋未完成）**25 条**，归并为 §6 的 **18 项**整改工作（其中 G06–G09、G13 已关闭）与 §7 的 **12 项**人工确认。

## 1. 一页速览

| 指南块 | 主要判断 | 状态 |
|---|---|---|
| 3.3.1 组别/器件 | 器件（Zynq-7000）在两组范围内已声明；组别资格与报名审核待人工确认 | 已有＋需人工确认 |
| 3.3.2 设计任务/PYNQ | 应用方向与软硬件协同成立；使用 PYNQ 框架（Overlay + PS Python），但不是 Jupyter Notebook 组织；通用 PYNQ Skill 待认定 | 已有＋部分具备 |
| 3.3.3.1 工具与协作 | Vivado/Vitis 2025.2（指南允许）；大模型协作记录已有（案例＋改写摘要＋智能体工作流） | 已有＋部分具备 |
| 3.3.3.2 板卡与器件 | 具体型号已声明；板级验证为有限工况，完整物理验收未完成 | 部分具备 |
| 3.3.3.3 开源 | MIT 为主体的混合许可已整理；实际开源发布与 5 个第三方 RTL 许可待人工处理 | 部分具备＋需人工确认 |
| 3.3.4 优化目标 | 已有指标汇总文档（SYNTHESIS-METRICS）；"优化前后对比表"仍缺同口径基线（原文已如实记录"未取得"）；从零复现受限于仓库不含位流/数据/模型 | 部分具备 |
| 3.3.5.1 工程包 | 源码、仿真台、构建脚本齐备；综合/实现指标已由 SYNTHESIS-METRICS 汇总，原始报告与实测原始输出不在仓库 | 部分具备 |
| 3.3.5.2 技能包 | 3 套技能＋入口 README 已有；跨项目/跨板卡验证未做 | 已有＋部分具备 |
| 3.3.5.3 设计报告 | 架构与关键决定已有，协作记录已有；背景/创新、优化前后对比、复现说明待补 | 部分具备 |
| 3.3.5.4 目录与命名 | 推荐目录非强制；README 已补目录对照；不搬迁源码；导出树含中文/空格/特殊字符路径 **0**，大小写例外待确认 | 已有＋需人工确认 |
| 3.3.5.5 评分权重 | 五项均有对应材料，但没有一项能自称"完整"；现场与真实数据缺口影响 20/20 两项 | 部分具备 |

## 2. 逐条映射（3.3 全文）

### 2.1 赛道简介、组别与器件（3.3.1）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 1 | 3.3.1 | 面向完整 FPGA 作品；器件、实现方式与应用领域不限；须可复现、可演示 | [README](../../README.md)「项目目标与当前交付范围」、[DESIGN-REPORT](DESIGN-REPORT.md) §1–§2、[DELIVERY-INDEX](DELIVERY-INDEX.md) | 部分具备 | 作品完整度以"有限接收演示"为口径；完整物理/发布闭环 `NOT_RUN`（G17） |
| 2 | 3.3.1.1 | 按学历分初级组/高级组，两组独立排名；报名后台审核学生证与学历 | 仓库不含报名/学籍信息；需按实际队伍与报名后台确认 | 需人工确认 | H01；若队伍含大四或硕士按高级组 |
| 3 | 3.3.1.1 | 器件范围：初级组可用 7 Series/Zynq-7000/UltraScale/UltraScale+；高级组任意 AMD 器件 | [README](../../README.md)「硬件与工具版本」：Zynq-7020 `xc7z020clg484-2`；[DESIGN-REPORT](DESIGN-REPORT.md) §1 | 已有 | Zynq-7000 在两组均允许，器件类型不构成限制 |

### 2.2 设计任务与 PYNQ（3.3.2）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 4 | 3.3.2 | 应用方向不限，鼓励跨学科与传统信号处理/通信/控制/图像/嵌入式选题 | [README](../../README.md)、[DESIGN-REPORT](DESIGN-REPORT.md) §1：便携射频收发与接收分析（通信/信号处理） | 已有 | 选题方向在允许范围 |
| 5 | 3.3.2 | 鼓励软硬件协同，发挥器件整体性能 | [DESIGN-REPORT](DESIGN-REPORT.md) §2（PL 样点/PS 分析分工）、[PROJECT-STRUCTURE](PROJECT-STRUCTURE.md) | 已有 | 三条流水线产物不混用已写明 |
| 6 | 3.3.2 | 实现方式不限：RTL、HLS、AI Engine、PS 软件及组合 | `rf_overlay/rtl/`、`pynq_overlay/sw/`、`src/rtl/`、`host/`、[DESIGN-REPORT](DESIGN-REPORT.md) §4 | 已有 | 以 RTL + 板端 Python/C 为主 |
| 7 | 3.3.2 | 初级组推荐 PYNQ：PS 侧 Python 组织算法与数据流，PL 侧封装 Overlay | `pynq_overlay/`（PYNQ 2.7 镜像、Overlay 构建与加载）、[pynq_overlay/build.tcl](../../pynq_overlay/build.tcl)、[rf_overlay/build.tcl](../../rf_overlay/build.tcl) | 部分具备 | 使用 PYNQ 框架（`from pynq import Overlay/MMIO`），但**未使用 Jupyter Notebook** 组织流程（推荐项，非强制，G18） |
| 8 | 3.3.2 | 提炼通用性较高的 PYNQ Skill 可加分（换题目/换板卡仍可用） | [skills/pynq-linux-development/SKILL.md](../../skills/pynq-linux-development/SKILL.md)、[skills/README.md](../../skills/README.md) | 部分具备 | 技能内容面向 PYNQ 且不限本题目，但**跨项目/跨板验证未做**（G10、H07） |
| 9 | 3.3.2 | 考察重点：功能正确、经实测验证、性能与资源真实完整、他人可复现；创新与应用价值权重最高 | [README](../../README.md)「已验证能力」「失败与限制」、[DELIVERY-INDEX](DELIVERY-INDEX.md) §4–§5 | 部分具备 | 实测层级与边界已如实标注；真实数据完整性缺口见 G01/G02/G17 |

### 2.3 设计与优化要求（3.3.3）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 10 | 3.3.3.1 | 工具版本：Vivado/Vitis 2026.1（推荐）或 2025.2；其他版本须说明并保证脚本可复现 | [README](../../README.md)「硬件与工具版本」（2025.2）、[version_matrix](../rf/version_matrix.md) §2、[awg-bitstream.yml](../../.github/workflows/awg-bitstream.yml)（Vivado 2025.2） | 已有 | 2025.2 为指南允许版本；逐产物版本记录不完整（G16） |
| 11 | 3.3.3.1 | 允许公开大模型/本地模型/智能体；须提交体现协作过程的交互记录（提示词、回答、自我纠错轨迹） | [AI-COLLABORATION-CASE.md](AI-COLLABORATION-CASE.md)（7 个有出处事件＋硬规则）、[AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md)（K1–K3 案例：提示词→回答→错误→纠正→结果＋智能体工作流） | 已有 | 记录采用**改写摘要**、不复制完整私人会话（隐私边界）；原始会话不在仓库（H10） |
| 12 | 3.3.3.1 | 协作沉淀的可复用经验须提炼为技能包随作品提交（见 3.3.5.2） | `skills/` 三套技能 24 个文件（SKILL.md＋references/assets）＋入口 README（共 25 个文件）、[skills/README.md](../../skills/README.md)（入口与已验证效果）、[skill/README.md](../../skill/README.md)（指南 `skill/` 入口映射） | 已有 | 技能包入口已具备；"已验证效果"仅为工程内使用记录，跨项目验证见 G10 |

### 2.4 板卡与器件限制（3.3.3.2）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 13 | 3.3.3.2 | 两组均须在提交物中声明具体型号 | [README](../../README.md)「硬件与工具版本」：`xc7z020clg484-2`、AD9361 FMCOMMS3、五寸 800×480＋GT911；[DESIGN-REPORT](DESIGN-REPORT.md) §1 | 已有 | 精确到器件型号与非 PL 外设 |
| 14 | 3.3.3.2 | 应选择自身可实际获取并完成板级验证的器件 | [DELIVERY-INDEX](DELIVERY-INDEX.md) §4（板级有限验证）、[l2_stage1_record](../rf/l2_stage1_record.md)、[boardtest](../rf/boardtest.md) | 部分具备 | 有限工况板级验证有记录；完整物理采集 `NOT_RUN`（G17） |
| 15 | 3.3.3.2 | 作品须在板上跑通并提供实测输出 | [README](../../README.md)「已验证能力」、[DELIVERY-INDEX](DELIVERY-INDEX.md) §4、[2026-10-26 交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) | 部分具备 | 实测输出为"文档记述"级（原始 IQ/收据不在仓库）；复审结论含 `CHANGES_REQUIRED`（G15、G17） |

### 2.5 开源要求（3.3.3.3）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 16 | 3.3.3.3 | 强烈要求在 GitHub 或 Gitee 开源，采用允许再分发的协议，推荐 MIT 或 Apache-2.0 | [LICENSE](../../LICENSE)（自有代码 MIT）、[NOTICE](../../NOTICE)、[LICENSES/README.md](../../LICENSES/README.md)、[SHARING-AND-CLEANUP](SHARING-AND-CLEANUP.md) | 部分具备 | 混合许可（MIT＋ADI BSD 风格＋GPL-3.0 测试台脚本＋未知来源 RTL）已如实披露；实际开源仓库/可见性/报名登记未完成（G12、H05、H08） |
| 17 | 3.3.3.3 | 开源须为"允许再分发"：第三方与未知来源材料不得被默认 MIT | [LICENSES/UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)、[NOTICE](../../NOTICE)、[LICENSES/README.md](../../LICENSES/README.md)、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md) §1 | 部分具备 | 混合许可边界已披露；5 个 RTL 许可未确认（H05/H06） |

### 2.6 优化目标（3.3.4）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 18 | 3.3.4 | 在目标器件上取得可测量性能表现，并给出与基线的对比 | [README](../../README.md)「已验证能力」（ARM 100 次完整计算 P95 等）、[DELIVERY-INDEX](DELIVERY-INDEX.md) §4、[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md)（指标简表＋证据路径）、`build_manifest.json` | 部分具备 | 有单点评测与历史候选指标；[SYNTHESIS-METRICS §5](SYNTHESIS-METRICS.md) 已如实记录"优化前后对比未取得同口径基线"（G01） |
| 19 | 3.3.4 | 合理使用片上资源，不以显著资源代价换有限性能收益 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md)（资源/时序/时钟/功耗表）、[build_manifest.json](../../build_manifest.json)（`timing` 字段）、[l2_stage1_record](../rf/l2_stage1_record.md) | 部分具备 | 资源数据已汇总（交付 bit 资源 LUT 54.03%/DSP 47.27% 等）；原始利用率/时序报告在本地 `evidence/`，不在仓库；"合理"判断缺基线（G02） |
| 20 | 3.3.4 | 保证工程可由他人从零复现 | [README](../../README.md)「构建与运行」、[PROJECT-STRUCTURE](PROJECT-STRUCTURE.md)、[DELIVERY-INDEX](DELIVERY-INDEX.md) §3、CI 工作流 | 部分具备 | 仓库不含位流/HWH/数据集/模型产物；`ml_training` 需自建环境重训（G15、G05） |
| 21 | 3.3.4 | 把设计过程中的判断依据写进报告，而不只是结果 | [DESIGN-REPORT](DESIGN-REPORT.md) §3「关键设计决定」、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md) §11「可复用硬规则」、[reviews/](../reviews/) | 已有 | 判据先于测量、单控制者、建议而非自动等均已成文 |
| 22 | 3.3.4 | 将与大模型协作的经验提炼为他人可复用的技能包 | `skills/` 三套、[skills/README.md](../../skills/README.md)（提炼来源与已验证效果）、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md) §1 协作模型、[AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md) §6 | 已有 | 技能包与"提炼过程"已有面向评审的入口说明；跨项目复用验证见 G10 |

### 2.7 提交内容 · 工程包（3.3.5.1）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 23 | 3.3.5.1 | 完整工程文件与源码（RTL、HLS、PS 软件等按实际方式提交） | `rf_overlay/`（RTL/bd/constraints/sw）、`pynq_overlay/`（sw/rtl/bd/tb/constraints）、`src/`（AWG RTL+tb）、`sdk/src/main.c`、`host/`、`ml_training/`、`sdr_testbench/` | 已有 | 三条流水线源码齐备；第三方 RTL 来源/许可另见 G11 |
| 24 | 3.3.5.1 | 仿真或验证结果，说明功能正确性如何确认 | [src/tb/awg_top_tb.v](../../src/tb/awg_top_tb.v)（37 个 tb 文件）、[rf_overlay/tb/rf_sample_capture_tb.v](../../rf_overlay/tb/rf_sample_capture_tb.v)、[pynq_overlay/tb/lcd_fb_tb.v](../../pynq_overlay/tb/lcd_fb_tb.v)、Python/C 测试套件（README「测试」） | 部分具备 | 测试台与套件存在，但**缺统一仿真运行脚本与结果记录**（G03）；仿真原始日志不在仓库 |
| 25 | 3.3.5.1 | 综合与实现报告，含资源占用、时钟频率与关键性能指标 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md)（按合并/RF/PYNQ 三个目标分列资源、时序、时钟、功耗与实测）、[build_manifest.json](../../build_manifest.json)、[l2_stage1_record](../rf/l2_stage1_record.md)、[boardtest](../rf/boardtest.md) | 部分具备 | 已有汇总报告（含本地原件路径）；原始 `.rpt`/DCP 未入仓库，外部 RF 接口时序覆盖缺口已如实记录（G02） |
| 26 | 3.3.5.1 | 上板工程、运行脚本、实测输出与参考结果的比对数据 | [rf_overlay/bd/create_bd.tcl](../../rf_overlay/bd/create_bd.tcl)、[rf.xdc](../../rf_overlay/constraints/rf.xdc)、[build.tcl](../../rf_overlay/build.tcl)、[program_board.sh](../../scripts/program_board.sh)、[boardtest](../rf/boardtest.md)、[golden/BASELINE.md](../golden/BASELINE.md)、[DELIVERY-INDEX](DELIVERY-INDEX.md) §4 | 部分具备 | 上板工程与判据齐备；实测原始输出/比对数据为文档记述，原始收据不在仓库（G15） |
| 27 | 3.3.5.1 | 可复现的构建脚本 | [build_bitstream.py](../../scripts/build_bitstream.py)、[rf_overlay/build.tcl](../../rf_overlay/build.tcl)、[pynq_overlay/build.tcl](../../pynq_overlay/build.tcl)、[rf_overlay/sw/board_app/Makefile](../../rf_overlay/sw/board_app/Makefile)、[package_merged_overlay.py](../../scripts/package_merged_overlay.py)、[release_manifest.py](../../scripts/release_manifest.py)、[ml_training/run.py](../../ml_training/run.py)、[.github/workflows/awg-bitstream.yml](../../.github/workflows/awg-bitstream.yml)、[ci/verify_bitstream_ci.py](../../ci/verify_bitstream_ci.py) | 已有 | 构建脚本与 CI 存在；需 Vivado/Vitis 2025.2 与外部依赖（G15、G16） |

### 2.8 提交内容 · 技能包（3.3.5.2）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 28 | 3.3.5.2 | 技能包置于 `skill/` 目录，含 `README.md` 说明每一项的适用范围、使用方法与失效条件 | `skills/` 三套（`final-prj-rf-development`、`pynq-linux-development`、`pluto-gnuradio-testbench`，共 24 个技能文件＋入口 README = 25 个文件）、[skills/README.md](../../skills/README.md)（逐套写明适用场景/使用方法/失效条件/已验证效果）；指南 `skill/` 入口：[skill/README.md](../../skill/README.md) | 已有 | 目录名为 `skills/` 而非 `skill/`（推荐目录非强制，§3 说明）；已提供 `skill/README.md` 入口映射 |
| 29 | 3.3.5.2 | 可为提示词工作流、案例模板、校验脚本、踩坑清单中的任意一种或多种 | [skills/README.md](../../skills/README.md) §2（四类形态与目录对应）、[pynq-linux-development/SKILL.md](../../skills/pynq-linux-development/SKILL.md)（工作流＋踩坑）、[pluto-gnuradio-testbench](../../skills/pluto-gnuradio-testbench/SKILL.md)（模板资产＋校验脚本） | 已有 | 四类形态已在入口 README 中逐项映射；"报告模板"仍以 references 形式存在 |
| 30 | 3.3.5.2 | 评价标准是可复用性；每一项须写明适用场景、使用方法、已验证效果，并说明从哪些失败/观察总结得出 | [skills/README.md](../../skills/README.md)（逐套"来源/已验证效果/尚未验证"）、三套 SKILL.md、[AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md) | 部分具备 | 材料已写明"尚未验证"的边界；**跨题目/跨板卡复用与陌生队伍试用未做**（G10） |
| 31 | 3.3.5.2 | 通用 PYNQ Skill 单独加分（换题目、换板卡仍可用） | [pynq-linux-development/SKILL.md](../../skills/pynq-linux-development/SKILL.md)（面向 PYNQ，不绑定本队题目） | 部分具备 | 通用性只在文风与内容上成立，未经别的题目/板卡验证（H07） |

### 2.9 提交内容 · 设计报告（3.3.5.3）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 32 | 3.3.5.3 | 选题背景与创新点：为什么选这个题目、解决什么实际问题、前人做过什么 | [DESIGN-REPORT](DESIGN-REPORT.md) §1、[README](../../README.md)；[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md) 背景 | 部分具备 | 有目标与边界，但缺"前人工作/同类方案对比"与按当前缩减范围重述的创新点（G04、H11） |
| 33 | 3.3.5.3 | 设计原理与功能框图 | [DESIGN-REPORT](DESIGN-REPORT.md) §2「总体架构」（文字框图＋三条流水线表）、§4 接收链路、§5 发射链路 | 已有 | 已有原理说明与框图 |
| 34 | 3.3.5.3 | 软硬件划分依据与接口设计 | [DESIGN-REPORT](DESIGN-REPORT.md) §2–§3、[backend_contract](../rf/backend_contract.md)、[rf-status-object-contract](../requirements/rf-status-object-contract.md) | 部分具备 | 单控制者/有限快照等决定已写明；软硬件划分的量化依据（吞吐/资源/时延预算）未成章（G04） |
| 35 | 3.3.5.3 | 优化过程，含优化前后的性能与资源对比表 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §5（结论：**未取得同口径优化前后基线**）；历史报告与 [DELIVERY-INDEX](DELIVERY-INDEX.md) §4 | 未完成 | [SYNTHESIS-METRICS §5](SYNTHESIS-METRICS.md) 与 [§6 缺口清单](SYNTHESIS-METRICS.md) 已如实记录"现有材料不足以填写对比表"；历史 QAM64/旧候选数据不得冒充当前版本（G01、G14） |
| 36 | 3.3.5.3 | 大模型协作记录：提示词、模型回答、自我纠错轨迹、智能体工作流设计（如有） | [AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md)（K1–K3：提示词→回答→错误→纠正→结果＋§1 智能体工作流）、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md)（7 案例＋协作模型） | 已有 | 记录为改写摘要，不复制完整私人会话（隐私边界）；原始会话不在仓库（H10） |
| 37 | 3.3.5.3 | 技能包提炼过程：从哪些失败/观察总结、如何验证有效、边界如何确定 | [skills/README.md](../../skills/README.md)（§3.4/§3.5 等逐套"来源实践/失败→仓库出处→已验证效果/尚未验证"）、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md) §11、[AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md) §6 | 已有 | 提炼来源、验证方式与失效边界已在入口 README 成章；跨项目验证见 G10 |
| 38 | 3.3.5.3 | 复现说明：一份可供他人从零开始完整执行的操作步骤 | [README](../../README.md)「构建与运行」「测试」、[DELIVERY-INDEX](DELIVERY-INDEX.md) §3、[PROJECT-STRUCTURE](PROJECT-STRUCTURE.md)、[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §7（构建与回查说明） | 部分具备 | 缺"从零安装工具链/准备依赖/重训模型/重建位流"的完整步骤与预期产物清单（G05、G15） |

### 2.10 提交内容 · 推荐目录与命名（3.3.5.4）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 39 | 3.3.5.4 | 推荐目录 `src/ sim/ build/ board/ data/ skill/ report/`；**非强制**，采用其他组织方式须在 README 给出目录对照说明 | [README](../../README.md)「赛事指南对照」与「目录对应关系」、[PROJECT-STRUCTURE](PROJECT-STRUCTURE.md)「为什么不全量搬迁」、[skill/README.md](../../skill/README.md)、[report/README.md](../../report/README.md)；本文件 §3 逐项对照 | 已有 | 现有目录保持不搬迁；README 已补逐项对照（G13 已处理）。**不搬迁源码**：构建/测试/发布清单引用现有路径 |
| 40 | 3.3.5.4 | 仓库内所有目录名与文件名须纯英文（小写、数字、下划线或连字符），不得出现中文/空格/特殊字符；中文只在正文 | 全部路径均为英文；[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) 等本轮新增文档为大写连字符名；导出树核查见本行说明 | 部分具备 | 最终发布导出树（1394 文件）中 **84 个文件名＋6 个目录名含大写字母**，含中文/空格/其他特殊字符的路径 **0**；**指南自身示例使用 `README.md`**，大小写例外需确认（G14、H04） |

### 2.11 评测指标与权重（3.3.5.5）

| # | 指南条款 | 要求（摘要） | 现有 / 计划材料 | 状态 | 说明与缺口 |
|---|---|---|---|---|---|
| 41 | 3.3.5.5 | 创新性与应用价值 30 分（最高权重） | [README](../../README.md) 项目目标、[DESIGN-REPORT](DESIGN-REPORT.md) §1、[DELIVERY-INDEX](DELIVERY-INDEX.md) §4；注意"信道感知自动选档"愿景**未实现** | 部分具备 | 需按当前缩减范围重述创新点，不得把未实现的自动选档当卖点（G04、H11） |
| 42 | 3.3.5.5 | 性能与资源优化效果 20 分 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md)（资源/时序/性能汇总）；[README](../../README.md)「已验证能力」、`build_manifest.json`、[DELIVERY-INDEX](DELIVERY-INDEX.md) §4 | 部分具备 | 汇总数据已有；优化前后对比与同口径基线未取得，原始报告不在仓库（G01、G02） |
| 43 | 3.3.5.5 | 功能正确性与设计完整性 20 分（以提交物核查与实测数据为主要依据） | [DELIVERY-INDEX](DELIVERY-INDEX.md) §4–§5、[acceptance-checklists-p1-p6](../requirements/acceptance-checklists-p1-p6.md)、[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) | 部分具备 | S2 正式 108 帧＋12 离线负控 `NOT_RUN`；复审 `CHANGES_REQUIRED`；QAM16 有误码（G15、G17） |
| 44 | 3.3.5.5 | 大模型协作与技能包 15 分 | [AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md)、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md)、`skills/`、[skills/README.md](../../skills/README.md) | 部分具备 | 材料已齐；剩跨项目验证（G10）与现场配套 |
| 45 | 3.3.5.5 | 文档质量与可复现性 15 分 | [README](../../README.md)、[DELIVERY-INDEX](DELIVERY-INDEX.md)、[DESIGN-REPORT](DESIGN-REPORT.md)、[COMPETITION-MATERIALS](COMPETITION-MATERIALS.md)、本文件 | 部分具备 | 文档体系完整；可复现受限于仓库不含产物/数据（G05、G15） |
| 46 | 3.3.5.5 | 两组同一套评测项与权重，独立排名；各项含评委评定成分 | 报名与评审口径在主办方 | 需人工确认 | H02、H03；现场演示尺度需用户确认（H11） |

## 3. 推荐目录对照（3.3.5.4，非强制）

> 指南原文："上表为推荐结构，非强制。采用其他组织方式的队伍须在 README.md 中给出目录对照说明。"本仓库**不搬迁现有源码**（[PROJECT-STRUCTURE](PROJECT-STRUCTURE.md)「为什么不全量搬迁」：构建脚本、测试夹具、发布清单与报告都引用现有路径）。下表就是可放进 README 的对照说明草案。

| 推荐目录 | 现有对应位置 | 内容 | 状态 |
|---|---|---|---|
| `README.md` | [README.md](../../README.md) | 项目简介、硬件/工具版本、目录、构建运行、能力与限制 | 已有（有目录说明，未逐项对照本表） |
| `src/` | `rf_overlay/rtl/`、`rf_overlay/sw/board_app/`、`pynq_overlay/sw/`、`pynq_overlay/rtl/`、`src/rtl/`、`sdk/src/`、`host/`、`ml_training/` | RF 主项目 PL/PS 源码、PYNQ 应用、辅助 AWG、上位机、模型训练 | 已有（分散在三条流水线） |
| `sim/` | `src/tb/`、`rf_overlay/tb/`、`pynq_overlay/tb/`、`pynq_overlay/sw/tests/`、`scripts/tests/`、`host/tests/`、`rf_overlay/sw/board_app/test/` | RTL 测试台与离线/主机测试 | 部分具备（缺统一仿真入口，G03） |
| `build/` | [build_bitstream.py](../../scripts/build_bitstream.py)、[rf_overlay/build.tcl](../../rf_overlay/build.tcl)、[pynq_overlay/build.tcl](../../pynq_overlay/build.tcl)、[Makefile](../../rf_overlay/sw/board_app/Makefile)、[package_merged_overlay.py](../../scripts/package_merged_overlay.py)、[.github/workflows](../../.github/workflows/awg-bitstream.yml) | 构建脚本、打包、CI | 已有 |
| `board/` | [rf_overlay/bd/create_bd.tcl](../../rf_overlay/bd/create_bd.tcl)、[rf.xdc](../../rf_overlay/constraints/rf.xdc)、[program_board.sh](../../scripts/program_board.sh)、[boardtest](../rf/boardtest.md)、[l2_stage1_record](../rf/l2_stage1_record.md) | 上板工程、约束、运行脚本、实测记录 | 部分具备（原始实测输出不在仓库） |
| `data/` | [golden/BASELINE.md](../golden/BASELINE.md)、`ml_training/fourclass_test_vectors/`、`ml_training/data/`、`sdr_testbench/config/`、`sdr_testbench/results/` | 黄金参考、测试向量、配置与采集结果 | 部分具备（大数据不在仓库或为受限副本） |
| `skill/` | `skills/`（`final-prj-rf-development`、`pynq-linux-development`、`pluto-gnuradio-testbench`＋入口 [README](../../skills/README.md)）；指南入口：[skill/README.md](../../skill/README.md) | 三套可复用技能＋入口说明 | 已有（跨项目验证见 G10） |
| `report/` | [report/README.md](../../report/README.md)（入口）→ [DESIGN-REPORT](DESIGN-REPORT.md)、[SYNTHESIS-METRICS](SYNTHESIS-METRICS.md)、[AI-COLLABORATION-RECORD](AI-COLLABORATION-RECORD.md)、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md)、`docs/reports/`、`docs/reviews/` | 设计报告、综合指标、协作记录、实验/审查报告 | 部分具备（报告要素缺口见 §2.9） |

## 4. 文件命名规则（3.3.5.4 第 1 段）

- 规则：目录名与文件名使用纯英文（小写字母、数字、下划线、连字符）；不得出现中文、空格或特殊字符；中文只在正文。
- 现状（详见本地核查报告，不随仓库；最终导出后重扫的数字见本轮最终简报）：
  - 工作区 git 跟踪范围（997 文件/78 目录，排除 `evidence/` 等）：不符合 34 个文件＋3 个目录——大写 31 文件＋3 目录，中文图片名 3 个，空格 0，其他特殊字符 0。
  - 待发布暂存导出树（1383 文件/166 目录）：不符合 **76 个文件＋6 个目录**，全部是大写/混合大小写；中文/空格/其他特殊字符 **0**。
  - 工作区磁盘另有未跟踪开发原件（`docs/reference/` 的 PDF/ZIP 与 3 张中文图片、`ml_training/` 数据/结果、`pynq_overlay/NA/` 等），不在发布候选内。
- 判断：**部分具备**。全部路径已是英文；大小写不符合字面规则；中文/空格只出现在不随发布的开发原件中。
- 例外疑点：指南自身示例使用 `README.md`；3.3.5.2 又要求 `skill/README.md`；`LICENSE`/`NOTICE`（开源惯例）、`Makefile`（构建工具约定名称）、`SHA256SUMS`（第三方完整性清单约定）、`SKILL.md`（技能工具约定）均难改成全小写。**是否允许这些"示例/惯例例外"须向赛道确认**（H04）。`LICENSES/` 目录大写亦属常见开源惯例。
- 处置：本轮**只报告、不批量改名**；改名会牵动文档互链、`SHARE-MANIFEST`、CI 与构建引用，须由 Lead 统一决策后在发布副本执行（G14）。

## 5. 评分权重与对应材料（3.3.5.5）

| 评测项 | 权重 | 首选对应材料 | 辅助材料 | 状态 / 最大风险 |
|---|---:|---|---|---|
| 创新性与应用价值 | 30 | [README](../../README.md) 项目目标、[DESIGN-REPORT](DESIGN-REPORT.md) §1 | [DELIVERY-INDEX](DELIVERY-INDEX.md) §4、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md) 背景 | 部分具备：自动选档愿景未实现，须按 BPSK/QPSK/受限 QAM16 重述创新点 |
| 性能与资源优化效果 | 20 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md)（资源/时序/性能汇总） | [README](../../README.md)「已验证能力」、`build_manifest.json`、[l2_stage1_record](../rf/l2_stage1_record.md) | 部分具备：汇总数据已有；优化前后对比与同口径基线未取得；原始报告不在仓库 |
| 功能正确性与设计完整性 | 20 | [DELIVERY-INDEX](DELIVERY-INDEX.md) §4–§5、[acceptance-checklists-p1-p6](../requirements/acceptance-checklists-p1-p6.md) | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) | 部分具备：完整物理闭环/真人 `NOT_RUN`，复审 `CHANGES_REQUIRED` |
| 大模型协作与技能包 | 15 | [AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md)、[AI-COLLABORATION-CASE](AI-COLLABORATION-CASE.md)、`skills/`、[skills/README.md](../../skills/README.md) | [DELIVERY-INDEX](DELIVERY-INDEX.md) §10 | 部分具备：材料已齐；跨项目验证与陌生队伍试用未做 |
| 文档质量与可复现性 | 15 | [README](../../README.md)、[DELIVERY-INDEX](DELIVERY-INDEX.md)、[DESIGN-REPORT](DESIGN-REPORT.md)、[COMPETITION-MATERIALS](COMPETITION-MATERIALS.md) | 本文件、[PROJECT-STRUCTURE](PROJECT-STRUCTURE.md) | 部分具备：仓库不含产物/数据，复现需自建环境 |

> 说明：指南写明"两组采用同一套评测项与权重，但分组独立排名、独立评奖，评分时结合各自组别的考察侧重把握尺度。各项均含评委评定成分；功能正确性与可复现性两项以提交物核查与实测数据为主要依据。"因此上表只做材料对应，不预估分数。

## 6. 缺口清单（归并 18 项）

> 与 §2 的 26 条"部分具备/未完成"对应；编号供后续任务引用。**本轮不修复代码/布局**，只登记；G06–G09 已随本轮并行任务 B 关闭（保留编号备查）。

| 编号 | 缺口 | 对应映射行 | 建议动作 | 责任 |
|---|---|---|---|---|
| G01 | 缺"优化前后性能与资源对比表"（当前交付版） | #35、#41、#42 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §5 已如实记录"未取得同口径基线"；赛前需固定两版可比设计并补测 | 任务 A（已整理）＋赛前补测 |
| G02 | 综合/实现原始报告（利用率、时序、Fmax）未入仓库 | #18、#25、#42 | [SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) 已汇总数值与本地原件路径；是否把脱敏摘要/截图放进 `build/` 待 Lead 决定并标证据层级 | 任务 A（已整理）＋Lead |
| G03 | RTL/模块仿真缺统一运行入口与结果记录 | #24 | 增加仿真运行说明/脚本（或报告写明手工 xsim 步骤） | Lead |
| G04 | 设计报告缺"选题背景/前人工作/创新点"与软硬件划分量化依据专章 | #32、#34、#41 | 在 DESIGN-REPORT 增补章节（Lead 统一整合） | Lead |
| G05 | 设计报告缺"从零复现"完整步骤与预期产物清单 | #20、#38、#45 | 在 DESIGN-REPORT/README 增补复现章节 | Lead |
| G06 | 大模型协作缺"提示词→回答→纠错"逐步记录与智能体工作流章节 | #11、#36 | **已关闭**：`AI-COLLABORATION-RECORD.md` 已生成（K1–K3＋§1 工作流） | 任务 B |
| G07 | 技能包"提炼过程"未独立成章 | #22、#37 | **已关闭**：`skills/README.md` 逐套写明来源失败/观察、验证方式与边界 | 任务 B |
| G08 | 技能包缺入口 `skills/README.md`（适用范围/方法/失效条件/已验证效果） | #28、#30、#37 | **已关闭**：本轮任务 B 已产出 | 任务 B |
| G09 | 技能形态不齐（提示词工作流/报告模板/校验脚本未单列） | #29、#44 | **已关闭**：`skills/README.md` §2 已逐类映射四种形态 | 任务 B |
| G10 | 通用 PYNQ Skill 未做跨项目/跨板验证 | #8、#30、#31 | 如实写"尚未跨项目验证"，或补一次最小验证 | 需人工确认（H07） |
| G11 | 5 个第三方参考工程 RTL 的许可未确认 | #17 | 取得许可/替换实现/移出并声明 PL 不可完整重建，三选一 | 需人工确认（H06） |
| G12 | 开源发布动作未完成（仓库地址/可见性/报名登记） | #16 | 由所有者完成建仓、许可确认与报名后台登记 | 需人工确认（H08） |
| G13 | 未按推荐目录组织，README 无逐项对照 | #39 | **已在本轮补齐**：README 增「赛事指南对照」+ `skill/`、`report/` 入口；不搬迁源码 | 已处理（2026-10-31） |
| G14 | 文件名大小写不符合字面规则（最终发布导出树 84 文件＋6 目录含大写；中文/空格/特殊字符 0） | #40、#42 | 发布副本统一小写化或向赛道确认例外；同步 `SHARE-MANIFEST` 与互链；改名须避免破坏构建引用 | 需人工确认（H04） |
| G15 | 工程包不含位流/HWH/数据集/模型产物/原始证据 | #15、#20、#26、#38、#45 | 在 README/报告保持"文档记述"声明；必要时提供受限数据说明 | Lead |
| G16 | 工具版本证据链不完整（构建清单 vivado 字段为空/记录分散） | #10、#27 | **部分缓解**：[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md) §1.4 记录 `Vivado v.2025.2 (win64) Build 6299465`（本地报告表头，三处一致）；`build_manifest.json` 的 `toolchain.vivado` 仍为空 | Lead |
| G17 | 完整物理验收/统一发布清单/真人现场 `NOT_RUN`；复审 `CHANGES_REQUIRED` | #1、#9、#14、#32、#43 | 按验收清单推进；现场由用户确认，AI 不代签 | 需人工/现场 |
| G18 | PYNQ 使用为 Python 脚本，未用 Jupyter Notebook（推荐项，非强制） | #7 | 保留说明即可；如要贴合推荐形式可另加 notebook 入口 | Lead |

## 7. 需人工确认清单（12 项）

| 编号 | 待确认事项 | 依据/位置 |
|---|---|---|
| H01 | 组别归属（初级组/高级组）与学籍审核 | 3.3.1.1；报名后台 |
| H02 | 赛道、器材与组别信息以报名信息与官方公告为准 | 3.3.1/3.3.6 |
| H03 | 官方解释权、赛程与提交节点 | 3.3.6；<http://www.fpgachina.cn/> |
| H04 | `README.md`/`LICENSE`/`NOTICE`/`Makefile`/`SKILL.md` 等大小写"示例/惯例例外"是否允许 | 3.3.5.4；§4 |
| H05 | 混合许可（MIT＋ADI BSD＋GPL-3.0＋未知来源 RTL）是否满足"允许再分发" | 3.3.3.3；[NOTICE](../../NOTICE)、[LICENSES/README.md](../../LICENSES/README.md) |
| H06 | 5 个第三方 RTL 的最终处理方式（取得许可/替换/移出并声明不可复现） | [UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)、[NOTICE](../../NOTICE) |
| H07 | 通用 PYNQ Skill 的"不与题目绑定"认定与加分口径 | 3.3.2/3.3.5.2 |
| H08 | 报名后台仓库链接/材料登记与仓库可见性 | 报名流程；[COMPETITION-MATERIALS](COMPETITION-MATERIALS.md) §5 |
| H09 | 是否要求匿名/双盲评审 | [COMPETITION-MATERIALS](COMPETITION-MATERIALS.md) §6 |
| H10 | 是否接受"脱敏发布副本＋原始证据留本地"的提交形态 | [SHARING-AND-CLEANUP](SHARING-AND-CLEANUP.md) |
| H11 | 现场演示范围与对外表述（QAM16 受限、QAM64 移出、外部 SDR 方向） | [README](../../README.md)「失败与限制」、[人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) |
| H12 | 设计报告/材料版式（语言、PDF、篇幅、是否需中文正文） | 3.3.5.3/3.3.5.4 未规定 |

## 8. 必须保留的真实限制

以下限制不得在整合、改名或对外表述中被删改（逐条有出处）：

1. **QAM64 已移出本轮交付**；仅保留历史报告与负结果。见 [README](../../README.md)「失败与限制」、[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)。
2. **QAM16 非零误码**：12 帧选用集中 `1/256`，属受限演示，不得写成零误码验收。
3. **外部 SDR BPSK 历史统计 23/30 帧同步，整体未通过**；不能只报同步成功帧的零误码。
4. **EVM `NOT_IMPLEMENTED`**：代码与界面均显式标注未接入。
5. **5 个第三方 RTL 许可未确认**：不在 MIT 范围内，公开再分发前须处理（[UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)）。
6. **完整物理验收未完成**：S2 正式 108 物理帧＋12 离线负控、真实环境闭环、46 dB HEADROOM 研究均 `NOT_RUN`。
7. **真人现场验收未完成**；触摸时延 `NOT_MEASURED`，AI 不代签。
8. **统一发布清单与完整交付闭环未完成**；独立复审结论为 `CHANGES_REQUIRED`（验收语义漂移、试部署重派超预算、文档与日志不一致）。
9. **本仓库不是可安装发布包**：不含位流/HWH、原始 IQ、数据集、模型产物与发布归档，历史指标只能"文档记述"引用。
10. **档位注册表/信道感知自动选档 `NOT_IMPLEMENTED`**：自动执行永久关闭，建议只填草稿。
11. **PL 未实现实时同步解调/RRC/多制式处理**；PS 侧只验证过有限快照分析与轻量推理。
12. **板卡存储健康**：MMC 根因 `NOT_ESTABLISHED`，完整 FS 检查 `NOT_RUN`。

## 9. 本文件不证明什么

- 不证明作品"符合官方全部条款"：官方解释权归主办方，报名后台与现场审核另有流程。
- 不把"仓库里有材料"当成"该条已通过"：§2 的"已有"只表示可指向材料存在。
- 不新增指标、不换算数字、不把 `NOT_RUN`/`UNKNOWN`/`NOT_ESTABLISHED` 改写为通过。
- 不替代 [DESIGN-REPORT](DESIGN-REPORT.md)、[DELIVERY-INDEX](DELIVERY-INDEX.md) 与原始收据；引用本文时请连带引用原始出处与证据层级。

## 10. 维护

- 指南条款或提交物变化时更新 §2 与 §6；缺口关闭后在 §6 标"已关闭"并保留原编号。
- 新增或移动材料后同步更新链接；链接必须为仓库相对路径且目标存在。
- 状态只能由新证据升级；不得因为没有反证就把"部分具备"改为"已有"。

## 11. 相关材料

- [竞赛提交材料与符合性说明](COMPETITION-MATERIALS.md)
- [交付材料索引](DELIVERY-INDEX.md)
- [设计报告](DESIGN-REPORT.md)
- [综合与实现指标整理](SYNTHESIS-METRICS.md)
- [AI 协作案例](AI-COLLABORATION-CASE.md)
- [大模型协作记录](AI-COLLABORATION-RECORD.md)
- [技能包入口](../../skills/README.md)
- [工程结构](PROJECT-STRUCTURE.md)
- [分享与清理](SHARING-AND-CLEANUP.md)
- [作品简介追踪表](../requirements/brief-traceability.md)
- [验收清单 P1–P6](../requirements/acceptance-checklists-p1-p6.md)
- [交付复审（2026-10-26）](../reviews/2026-10-26-codex-delivery-closeout-review.md)
