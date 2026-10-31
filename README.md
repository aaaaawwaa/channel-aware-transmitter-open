# Channel-Aware Transmitter / Zynq-7020 + AD9361 便携收发与接收分析平台

本仓库是本项目最新源代码与工程文档的**脱敏发布副本**。它包含 RF 主项目、PYNQ 板载应用、辅助 AWG 开发链，以及模型训练、SDR/GNU Radio 测试台、技能库和工程说明。

**它不是可安装发布包，也不是原始验收证据。** 原始 IQ、实验收据、位流/HWH、训练数据集、模型产物与照片不在本仓库（原因见[证据与隐私边界](#证据与隐私边界)）。

> 项目最初的设想是"先测量信道，再选择发射波形"的信道感知发射机。**这个自动选档能力目前并未实现**：档位注册表与策略模块不存在，界面自动执行永久关闭，只允许把建议填入草稿。当前可演示的是有限条件下的接收与识别链路（BPSK/QPSK 为主，QAM16 受限，QAM64 已移出交付）。

## 项目目标与当前交付范围

- **目标平台**：Zynq-7020 SoC + AD9361 收发器 + 五寸触摸屏的便携式射频收发/接收分析平台，板载 Linux 运行 PYNQ 应用，PL 承担实时样点侧处理，PS 承担控制、分析与轻量推理。
- **当前交付范围（2026-10-26 交付复审口径）**：BPSK/QPSK 有限接收演示 + 有限条件下 QAM16；QAM64 已移除；外部 SDR 发射、板卡纯接收是现场演示方向。
- **尚未实现/未验收**：信道感知自动选档、统一接收结果契约之外的多制式实时处理、EVM、完整物理采集（S2 正式 108 帧 + 12 离线负控）、真人现场验收、统一发布清单。完整清单见[交付材料索引](docs/engineering/DELIVERY-INDEX.md)与[作品简介追踪表](docs/requirements/brief-traceability.md)。
- **三条独立流水线，产物不混用**：

| 流水线 | 目录 | 说明 |
|---|---|---|
| RF 主项目（AD9361） | `rf_overlay/` | PL 收发 RTL、板端 C 守护进程、主机侧测试与判据工具。 |
| PYNQ 板载应用 | `pynq_overlay/` | 板载 Linux 五寸屏界面、状态拥有者、发布与安装、接收分析。 |
| 辅助 AWG | `src/`、`sdk/src/`、`host/` | 独立的信号发生器开发链，不参与 RF 产品验收。 |

## 硬件与工具版本

| 项 | 版本/型号 | 说明 |
|---|---|---|
| 开发板 | Zynq-7020（`xc7z020clg484-2`），AC820 系列 | RF 工程以其 FMC 接口连接 AD9361 模块。 |
| RF 收发器 | AD9361（FMCOMMS3 形态模块） | 1R1T / 2R2T 由 `phy_mode` 配置；当前交付使用 1R1T。 |
| 显示与交互 | 五寸触摸屏 800×480，GT911 触摸控制器 | PYNQ 应用负责界面；板载 UART/framebuffer 可从日志核对。 |
| PL/PS 工具链 | Vivado / Vitis **2025.2** | 三个 Overlay 各自的 `build.tcl` 与 `scripts/build_bitstream.py`。 |
| 板载 Linux | PYNQ 2.7 镜像，Linux `5.4.0-xilinx-v2020.2` | 板端原生 `gcc 9.3.0`；Python 3.8 + NumPy 1.17.4。 |
| 主机环境 | Python 3.8+ | 历史基线使用 3.8.6；部分测试需要独立虚拟环境（见各 README）。 |
| 外部 SDR 测试台 | PlutoSDR Rev.B（AD9363A），固件 `v0.39-dirty` | Radioconda：GNU Radio 3.10.12.0、Python 3.12、gr-iio、libiio 0.26。 |
| 示波器（SDR 测试台） | Keysight DSO-X 1102A | 系统 Python + PyVISA/USBTMC；带宽不足，不能完成 RF 回环验收。 |

版本矩阵与工具链冲突记录见 [docs/rf/version_matrix.md](docs/rf/version_matrix.md)；AD9361 兼容性与引脚/电平证据见 [docs/rf/compatibility.md](docs/rf/compatibility.md)。

## 目录对应关系

```text
rf_overlay/                 RF 主项目
  rtl/ bd/ constraints/ tb/ PL 源码、Block Design、约束与仿真
  sw/board_app/             板端 C 守护进程、状态机、原生测试与假硬件夹具
  sw/ref/                   AD9361 参考实现（第三方，见 NOTICE）
pynq_overlay/               板载 PYNQ Linux 应用
  sw/                       界面、状态拥有者、客户端、发布/安装、接收分析
  sw/tests/                 板载 Python 测试
src/ sdk/src/ host/         辅助 AWG（PL RTL / 裸机 PS / PC 上位机）
scripts/                    构建、打包、部署、采集、验收与恢复入口
ml_training/                离线调制识别：数据生成、训练、评估、NumPy runtime
sdr_testbench/              PlutoSDR / GNU Radio / 示波器测试台
skills/                     可复用技能（本项目 RF、PYNQ、Pluto 测试）
skill/                      指南 `skill/` 入口（映射到 skills/，见 skill/README.md）
report/                     指南 `report/` 入口（映射到 docs/engineering/ 报告与协作记录）
docs/                       需求、设计/规格、报告、审查、工程记忆
docs/evidence/              脱敏证据摘要（原始报告/位流/收据不在仓库）
tools/                      维护、导出与辅助脚本
ci/                         位流完整性与自托管 runner 检查
.github/                    CI 工作流（AWG 位流构建）
```

完整职责与整理原则见 [工程结构](docs/engineering/PROJECT-STRUCTURE.md)。

## 构建与运行

先在仓库根目录准备好对应的 Vivado/Vitis、Python 与板卡环境；**本仓库不含位流/HWH/虚拟环境/数据集**，所以以下命令需要按各自文档重建依赖。

```powershell
# 1) 辅助 AWG 位流（与 RF 产品无关）
python scripts/build_bitstream.py
python host/main.py                     # AWG PC 上位机（唯一入口）

# 2) 独立 PYNQ Overlay
vivado -mode batch -source pynq_overlay/build.tcl

# 3) 独立 RF Overlay
vivado -mode batch -source rf_overlay/build.tcl
```

板端部署与安装：

```bash
# 板端 C 原生构建 / 主机侧测试（主机需 Git bash 的 sh.exe 在 PATH）
make -C rf_overlay/sw/board_app
make -C rf_overlay/sw/board_app host-test

# 发布安装与状态读回（需要板卡；先读 runbook）
bash pynq_overlay/sw/install.sh
python scripts/rf_teaching_install_state.py

# 试部署 + 回滚（单次入口，终态见 runbook）
sh scripts/rf_teaching_trial_install.sh
```

测试（强烈建议带仓库内 `--basetemp`，避免系统临时目录权限问题）：

```powershell
python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-overlay pynq_overlay/sw/tests
python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-scripts scripts/tests
python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-host host/tests
python -m unittest discover -s tools -p test_project_maintenance.py -v
```

模型训练与 SDR 测试台：

```powershell
# 训练全流程（需先自建 ml_training 的虚拟环境；datasets/artifacts 不在仓库内）
ml_training\.venv\Scripts\python.exe ml_training\run.py all

# 外接 PlutoSDR / GNU Radio 测试台（先 probe/offline，再 RX；不建立 TX 数据流）
.\sdr_testbench\run.ps1 probe
.\sdr_testbench\run.ps1 offline
.\sdr_testbench\run.ps1 rx
```

部署、采集与审查规则见 [开发审查规则](docs/requirements/development-review-policy.md)、[试部署 runbook](docs/rf/deployment-trial-runbook.md)、[上板测试流程](docs/rf/boardtest.md)。

从零复现说明（工具链版本、依赖、构建入口、预期产物、缺失数据/模型/位流与再生成路径、验证步骤与限制）见 [OPTIMIZATION-AND-REPRODUCTION](docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md)；综合/实现证据的原始报告位置与脱敏摘要见 [EVIDENCE-PACK](docs/engineering/EVIDENCE-PACK.md)。

## 已验证能力（据文档记载）

> 原始收据、原始 IQ 与运行日志不在本仓库，下列指标只能按"文档记载"引用，不能在本仓库内复算。每条都带原文的限定语。

- **有限接收冒烟（12 帧选用集）**：BPSK 两 ON 均 `0/138`，QPSK 两 ON 均 `0/128`；QAM16 分别 `1/256` 与 `0/256`；QAM64 移除相关的 9 项负例 `9/9` 通过。见[交付复审](docs/reviews/2026-10-26-codex-delivery-closeout-review.md)。
- **发布安装**：`4814bbc4…` 已安装，只读读回 `current/active` 一致、双服务 `active`、RF 安全态成立（当时读回，不代表当前板态）。同一复审记录 `4814bbc4` 之前的候选 `b82b141e…` 安装收据为 `INSTALLED_VERIFIED`、`accepted=true`、`new_rf_frames=0`、`real_initializations=0`。见[作品简介追踪表](docs/requirements/brief-traceability.md)。
- **试部署 + 自动回滚**：修复候选通过非发射验收后注入退出 42，自动恢复父版本；独立读回服务、RF 安全态、`boot_id` 与存储正常。见[回滚收尾报告](docs/reports/2026-10-17-trial-rollback-board-closeout.md)。
- **ARM 完整计算历史记录**：不同候选的 100 次完整计算 P95 为 `258.6409 ms` / `272.785 ms` / `278.835 ms`（具体 RSS、判等条件与日期见原文）；必须带候选 ID 引用，不能移植到新候选。见[证据索引](docs/memory/05-evidence-index.md)、[作品简介追踪表](docs/requirements/brief-traceability.md)。
- **离线基线**：迁移受控证据后 `pynq_overlay/sw/tests` 为 `2056 passed / 5 skipped`、`scripts/tests` 为 `496 passed / 1 skipped`（退出码 0）。见[2026-10-14 报告](docs/reports/2026-10-14-deepseek-board-and-p0-followup.md)。
- **主机 C 原生套件**：core `98/0`、protocol `58/0`、sleep `22/0`、HEADROOM `148/0`、vendor integrity `10 files passed` 等。见[离线基线报告](docs/reports/2026-10-13-final-prj-baseline.md)。
- **QPSK/RECT 有限板上验证**：5 帧 I/Q 次序显式、`sync=true`、BER `0/128`；离线测试多组通过。见[2026-10-23 QPSK 离线报告](docs/reports/2026-10-23-p3-qpsk-offline.md)。
- **外部 SDR 测试台**：设备识别、离线单音、RX 样本流、TX 停止读回成立；**RF 输出未验证**（示波器带宽不足，有线 RF 回环未完成）。见 [SDR 测试报告](sdr_testbench/TEST-REPORT.md)。
- **辅助 AWG 历史基线**：`test_bridge.py COM11` 230 PASS、`scope_verify.py COM11` 61/61 PASS（与 RF 产品无关，产物不混用）。见 [golden/BASELINE.md](docs/golden/BASELINE.md)。
- **人工/照片**：功能项与实体屏照片部分闭合；触摸时延 `NOT_MEASURED`。见 [S1 真人照片复核](docs/reports/2026-10-04-s1-human-photo-review.md)。

## 失败与限制（必须保留）

- **QAM16 非零误码**：12 帧选用集中为 `1/256`，属于受限演示，不能倒填成零误码验收。
- **完整交付闭环**：独立复审结论为 `CHANGES_REQUIRED`——验收语义漂移、试部署重派超预算、交付文档与原始日志不一致（见[交付复审](docs/reviews/2026-10-26-codex-delivery-closeout-review.md)）。
- **外部 SDR BPSK 历史统计**：23/30 帧同步，整体未通过；不能只报同步成功帧的零误码。
- **QAM64**：已移出本轮交付，仅保留历史报告与负结果。
- **S2 正式 108 物理帧 + 12 离线负控**：`NOT_RUN`。
- **46 dB HEADROOM 真实研究**：`NOT_RUN`（曾因 `lease_held` 谓词被阻塞；真实帧执行需要单次授权与接线确认）。
- **EVM**：`NOT_IMPLEMENTED`，代码与界面均显式标注未接入。
- **PL 实时处理**：PL 未实现实时同步解调 / RRC / 多制式处理，不得把 PS 侧离线计算表述为 PL 实时能力。
- **档位选择 / 信道感知**：档位注册表与策略 `NOT_IMPLEMENTED`；自动执行永久关闭，建议只填入草稿。
- **触摸时延**：`NOT_MEASURED`（用户已取消专门计时，不作为阻塞项）。
- **真人现场验收**：未完成；由用户现场确认，AI 不代签。
- **板卡存储健康**：MMC 根因 `NOT_ESTABLISHED`，完整 FS 检查 `NOT_RUN`。
- **第三方 RTL 出处未决**：`rf_overlay/rtl/` 中 5 个第三方参考工程文件没有可证明的许可证，公开再分发前必须取得作者许可或替换实现（见 [LICENSES/UNKNOWN-PROVENANCE.md](LICENSES/UNKNOWN-PROVENANCE.md)）。
- **RF 源同步 DDR 接口时序未覆盖**：`rf_overlay/constraints/rf.xdc` 只有时钟/引脚/电平约束，缺 `set_input_delay`/`set_output_delay`；内部 WNS/WHS 为正不等于外部 AD9361 接口时序通过。现状、端口清单、参数来源与分阶段最小方案见 [RF-INTERFACE-TIMING](docs/engineering/RF-INTERFACE-TIMING.md)（板级测量 `NOT_RUN`）。
- **功耗是工具估计**：`2.418 W` 等数值来自 Vivado `report_power` 估算，**没有**板级功耗/电流实测，不得写成“实测功耗”。
- **优化前后对照未取得**：没有同版本、同测试条件、同指标口径的前后基线；最小补测方案见 [OPTIMIZATION-AND-REPRODUCTION](docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md)。
- **本仓库不是可安装包**：缺位流/HWH、发布归档、证据与数据集，不能直接部署；旧报告里的 SHA-256 摘要不能用本仓库文件验证。

## 材料索引

完整阅读地图见 **[docs/engineering/DELIVERY-INDEX.md](docs/engineering/DELIVERY-INDEX.md)**，其中包括：

- 设计说明与工程规则（需求、合同/规格、审查规则、部署 runbook）；
- 构建 / 测试 / 运行入口；
- 已验证能力与失败限制（逐条出处）；
- 模型与训练说明、SDR/GNU Radio 测试台、技能库；
- 证据边界（原始材料在哪里、为什么不在仓库）；
- [AI 协作案例](docs/engineering/AI-COLLABORATION-CASE.md)：7 个有出处的多代理协作事件与可复用规则。

面向评审的材料：

- [设计报告](docs/engineering/DESIGN-REPORT.md)：架构、关键设计决定、接收/发射/发布链路与已知限制；配套[系统结构图](docs/engineering/system-architecture.md)。
- [综合与实现指标](docs/engineering/SYNTHESIS-METRICS.md)：资源、时钟/时序、功耗、上板与 ARM 性能、优化前后对照（当前为“未取得同口径基线”）与缺口清单。
- [证据包索引](docs/engineering/EVIDENCE-PACK.md)：当前交付版本的脱敏证据摘要、原件位置与 SHA-256、payload_equal 绑定、功耗估计值口径；配套 [docs/evidence/](docs/evidence/README.md)。
- [优化对照与从零复现](docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md)：同口径优化前后核查（当前未取得）、最小补测方案、工具链/依赖/构建/产物/缺失项的复现说明。
- [RF 接口时序缺口](docs/engineering/RF-INTERFACE-TIMING.md)：缺失的 input/output delay 端口、AD9361 参数来源、计算方法、板级测量与离线实现的最小方案；**不是接口时序通过声明**。
- [文件名例外咨询文字](docs/engineering/FILENAME-CONSULTATION.md)：命名规则现状、惯例例外疑点与可直接发送给赛道的咨询文字（**尚未发送**）。
- [技能包入口](skills/README.md)（指南 `skill/` 映射见 [skill/README.md](skill/README.md)）：3 套技能的适用范围、使用方法、失效条件、来源与已验证效果。
- [大模型协作记录](docs/engineering/AI-COLLABORATION-RECORD.md)：3 个真实案例的“提示词 → 回答 → 纠错 → 结果”与智能体工作流；另有 [AI 协作案例](docs/engineering/AI-COLLABORATION-CASE.md) 的方法与教训。
- [指南 3.3 符合性映射](docs/engineering/GUIDE-CONFORMANCE.md)：逐条要求 → 材料 → 状态 → 缺口；[竞赛提交材料与符合性说明](docs/engineering/COMPETITION-MATERIALS.md)：材料清单、无法证明的条目与匿名/隐私处理。
- 注意：官方指南由赛方提供，本仓库不包含指南原文；本仓库材料按指南 3.3 整理，但不能作为“已满足官方全部要求”的证明；报名后台登记需单独完成。

## 赛事指南对照（2026 FPGA 赛道 AMD，3.3 自主选题）

依据：全国大学生嵌入式芯片与系统设计竞赛 2026 FPGA 赛道选题指南（AMD）**3.3 自主选题赛道**（PDF 第 22–25 页，PDF 内页码 19–22）。初级组/高级组的资格与器件范围以报名信息为准；本平台使用 Zynq-7020（`xc7z020clg484-2`），两组均允许。指南推荐目录结构**非强制**；本仓库不搬迁既有目录，按下表给出对照（逐条状态与缺口见 [GUIDE-CONFORMANCE](docs/engineering/GUIDE-CONFORMANCE.md)）。

| 指南推荐 | 本仓库实际位置 | 说明 |
|---|---|---|
| `src/` 设计源码 | `src/`（AWG RTL/裸机）、`rf_overlay/rtl/`、`rf_overlay/sw/board_app/`、`pynq_overlay/sw/`、`host/`、`sdk/src/` | 三条流水线独立、产物不混用；未搬迁 |
| `sim/` 仿真/验证 | `src/tb/`、`rf_overlay/tb/`、`pynq_overlay/tb/`；`pynq_overlay/sw/tests/`、`scripts/tests/`、`host/tests/` | RTL 仿真与 Python/C 测试分层 |
| `build/` 构建与综合/实现报告 | `scripts/build_bitstream.py`、`rf_overlay/build.tcl`、`pynq_overlay/build.tcl`、`scripts/package_merged_overlay.py`、`build_manifest.json`、`.github/workflows/awg-bitstream.yml` | 综合/实现指标整理在 [SYNTHESIS-METRICS](docs/engineering/SYNTHESIS-METRICS.md) |
| `board/` 上板工程、脚本与实测输出 | `scripts/rf_teaching_install.sh`、`scripts/rf_teaching_trial_install.sh`、`pynq_overlay/sw/install.sh`、`docs/rf/boardtest.md`、`docs/rf/deployment-trial-runbook.md` | 原始实测输出/收据不在仓库 |
| `data/` 测试数据与参考结果 | `ml_training/fourclass_test_vectors/` 等小型参考向量、`docs/golden/` | 原始 IQ/数据集/模型产物不随仓库 |
| `skill/` 技能包 | [`skill/README.md`](skill/README.md) → [`skills/`](skills/README.md) | 技能内容维护在 `skills/`，入口映射 |
| `report/` 设计报告与协作记录 | [`report/README.md`](report/README.md) → `docs/engineering/DESIGN-REPORT.md`、`SYNTHESIS-METRICS.md`、`AI-COLLABORATION-RECORD.md` | 报告正文维护在 `docs/engineering/` |

指南 3.3 要求的提交物入口：工程包（源码/仿真/综合实现/上板/构建脚本）见上表、[EVIDENCE-PACK](docs/engineering/EVIDENCE-PACK.md) 与 [README 构建与运行](#构建与运行)；从零复现见 [OPTIMIZATION-AND-REPRODUCTION](docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md)；技能包见 [skills/README.md](skills/README.md)；设计报告见 [DESIGN-REPORT](docs/engineering/DESIGN-REPORT.md) 与 [report/README.md](report/README.md)；大模型协作记录见 [AI-COLLABORATION-RECORD](docs/engineering/AI-COLLABORATION-RECORD.md)；RF 接口时序边界见 [RF-INTERFACE-TIMING](docs/engineering/RF-INTERFACE-TIMING.md)。

**文件命名规则说明**：指南 3.3.5.4 要求目录/文件名为纯英文小写、数字、下划线或连字符，但指南自身示例使用 `README.md`、`skill/README.md`；本仓库保留 `README.md`、`LICENSE`、`NOTICE`、`SKILL.md`、`Makefile`、`SHA256SUMS` 等惯例名称，未批量改名以免破坏构建引用与既有限制；不符合项清单与例外疑点见 [GUIDE-CONFORMANCE §4](docs/engineering/GUIDE-CONFORMANCE.md) 与 [FILENAME-CONSULTATION](docs/engineering/FILENAME-CONSULTATION.md)（含可直接发送给赛道的咨询文字，**尚未发送**），需向赛道确认例外。中文仅出现在正文内容中。

## 许可证

本仓库是**混合许可**，不同目录适用不同条款；完整清单与全文见 [NOTICE](NOTICE) 与 [LICENSES/](LICENSES/)：

- **自有代码与文档：MIT**，见 [LICENSE](LICENSE)（Copyright (c) 2026 aaaaawwaa）。
- **Analog Devices AD9361 no-OS 驱动**（`rf_overlay/sw/board_app/vendor/` 10 个文件）与 **ADI 参考应用**（`rf_overlay/sw/ref/main.c`、`platform.c`、`platform.h`）：保留 ADI 自定义 BSD 风格许可 `LicenseRef-ADI-AD9361`（含专利与 field-of-use 条款，使用须运行于或直连 ADI 器件），全文见 [LICENSES/Analog-Devices-AD9361-BSD-style.txt](LICENSES/Analog-Devices-AD9361-BSD-style.txt)，不随本项目重新授权。
- **依赖 GNU Radio/PyQt5 的测试台脚本**（`sdr_testbench/radio_test.py`、`sdr_testbench/board_integration/stage0.py`、`sdr_testbench/gui_demo/bpsk_tx_gui.py`、`sdr_testbench/gui_demo/verify_offline.py`、`skills/pluto-gnuradio-testbench/assets/testbench/radio_test.py`）：按 **GPL-3.0** 分发（因为它们 import GNU Radio GPL-3.0-or-later 与 PyQt5 GPL-3.0-only），全文见 [LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt)。GNU Radio Companion 生成的流图代码与 GRC 缓存**不在**本仓库内（可由 `.grc` 重新生成）。
- **出处未知的第三方参考工程 RTL**（`rf_overlay/rtl/ad9361_phy.v`、`phy_rx.v`、`phy_tx.v`、`axi_ctrl_v1_0.v`、`axi_ctrl_v1_0_S00_AXI.v`）与 1 个 Vitis 生成桩：无版权/许可声明，**不授予任何许可、不属于 MIT**；这是公开再分发前的未决阻塞项，详见 [LICENSES/UNKNOWN-PROVENANCE.md](LICENSES/UNKNOWN-PROVENANCE.md)。
- **不随仓库分发的外部依赖**（Vivado/Vitis、PYNQ、GNU Radio、NumPy、PyTorch、PySide6、PyQt5、PyVISA 等）各自遵循其许可证，清单见 [NOTICE](NOTICE) 的“External dependencies”段。

## 证据与隐私边界

- **不在本仓库**：原始 IQ、`evidence/` 收据与运行日志、位流/HWH/ELF、训练数据集与模型产物、照片/截图、发布归档、参考大文件、`.local/` 私有备份、Git 历史、`dist/` 导出包。
- **脱敏方式**：导出工具 [tools/project_maintenance.py](tools/project_maintenance.py) 只导出文本白名单，并在导出时替换个人路径、私网地址、识别出的真实设备串号、VISA 串号与常见明文口令写法；图片/二进制/原始收据不进入导出。清单与脱敏计数见 [SHARE-MANIFEST.json](SHARE-MANIFEST.json)，边界说明见 [分享与清理](docs/engineering/SHARING-AND-CLEANUP.md)。
- **因此**：本仓库内的指标是"文档记述"级引用；脱敏会改变字节，旧摘要不能用本副本验证；需要复核具体结论时请回到开发机原始收据与对应源码摘要。

## 工程整理与历史

- 本仓库是清理历史后的发布仓库：只包含一个发布提交，不含旧开发仓库的提交历史；旧私有开发仓库中的历史材料不作为本仓库的验收依据。
- 当前目录整理与分享边界见 [2026-10-30 整理记录](docs/engineering/2026-10-30-ORGANIZATION-REPORT.md)。
- 工程记忆（只索引、不复制）见 [docs/memory/README.md](docs/memory/README.md)；历史按日期理解的"当前/最新"叙述见 [CLAUDE.md](CLAUDE.md)。
- 旧 AWG 说明完整归档在 [docs/engineering/awg-legacy-guide.md](docs/engineering/awg-legacy-guide.md)。

维护约定：新增实验使用独立输出目录；原始数据、判据、结果与失败原因同批保存；缓存与可再生文件不进入候选；不对历史收据做原地改写。
