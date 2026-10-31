# final_prj RF 平台实施计划

> **For agentic workers:** Use $subagent-driven-development when the user assigns independent implementation tasks; otherwise use $executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking. Each chunk is independently reviewable and must meet its gate before dependent board work.

**Goal:** 在 `final_prj` 中实现用户 2026-10-13 简介所述的 Zynq + AD9361 便携式通信教学、接收分析与受约束智能决策平台。

**Architecture:** 以既有 AD9361 RF 流水线为产品主路径，保留单一板端 RF 控制者。PL 承担经实测定义的样点收发/实时处理，PS 完成配置、有限或扩展采样分析、轻量推理和无硬件 IO 的决策策略；五寸触摸屏只展示当前同帧结果并提供人工接管。AWG/ACM2108 作为独立辅助能力，不合并位流或验收统计。

**Tech Stack:** Vivado/Vitis 2025.2、Zynq-7020、AD9361/FMCOMMS3、Verilog、板载 Linux/PYNQ、C daemon、Python/numpy/PySide6/PIL、pytest、Git 与版本化发布/证据收据。

---

## 0. 执行边界与依赖

- 工作目录固定为 `<LOCAL_PATH>`，分支 `codex/description-aligned-platform-plan`。原工程 `signal_generator` 只作历史证据源；原 DeepSeek Harness 的板端 runner 工作应暂停在其当前审核门。暂停决策和已知进度见 [重心切换报告](../../reports/2026-10-13-final-prj-focus-decision.md)。
- 原任务目前只有“应暂停”的决策，没有任务级暂停确认。可先做新目录独立的只读 P0 盘点与离线测试；源端证据迁移、Harness 工作目录改指和任何板卡运行，都要先通过 Task 1.0 的切换门。
- 首个目标是 **P0 可重建基线**，随后推进 **P1 有限 BPSK 与环境诊断闭环**。P2 TX 合同设计可在 P1 离线期间准备；P3 接收机必须按 P2 真实 TX 模式逐一进入。P4 环境可信度依赖 P1/P3 的稳定数据，P5 档位决策依赖 P4 和独立档位收益证据，P6 为作品级验收。
- 每个阶段都区分 `CODED`、`OFFLINE_VERIFIED`、`ARM_VERIFIED`、`BOARD_VERIFIED`、`HUMAN_VERIFIED`、`RELEASED`。测试数量不能替代物理或真人结果。失败与未知状态保留，不能重写为通过。
- 任何新协议、PL 重新装载、RF 发射、真实采集或安装，都必须先有对应的规格、离线反例、发布身份、板卡只读预检及明确的执行门。板端由已有 daemon 独占 PL，不从测试脚本额外调用 `Overlay()`。
- 子项目首次实施前各自补一份精确规格和短任务计划；本文件固定接口责任、依赖、验收和第一批执行步骤，不凭空冻结尚未测量的 QAM/EVM 物理阈值。

## Chunk 1 — P0：副本、需求和可重建基线

### Task 1.0：确认原任务停写

**Files:** 更新 `docs/reports/2026-10-13-final-prj-focus-decision.md` 与 `FORK_SNAPSHOT.md`；不修改原工程源码。

- [ ] 在 DeepSeek Harness 的任务界面暂停原 `signal_generator` 任务，记录任务标识、最后审核门和暂停状态；不能以桌面进程是否运行推断任务状态。
- [ ] 暂停后只读记录源端 Git 状态、HEAD、runner SHA-256 与最近收据时间，确认源端不再发生写入；发现未提交改动时列清单，不覆盖或静默同步。
- [ ] 将这些事实写入切换收据。当前缺少支持的任务级控制接口，因此在得到界面确认前，该门保持 `NOT_CONFIRMED`。

**Gate P0-0:** 原任务已暂停并停写，切换点有可核查的源码与收据摘要；否则只允许新目录的独立只读盘点、离线测试和规划。

### Task 1.1：冻结切换点与证据索引

**Files:** 更新 `FORK_SNAPSHOT.md`；创建 `docs/requirements/brief-traceability.md`、`docs/reports/2026-10-13-final-prj-baseline.md`。源端只读参考 `<LOCAL_PATH>`。

- [ ] 逐句编号用户简介：平台/硬件、各 TX 模式、RX/同步、星座/频谱/EVM/BER/数据、PL/PS、触摸、环境、决策、三层验证。每条列出源码入口、现有测试、历史原始收据路径和当前状态；没有原始证据的写 `UNVERIFIED`。
- [ ] 记录当前 Git 提交、RF 核心文件 SHA-256、b82 安装收据路径、旧 runner 最后完整收据绑定的摘要，以及副本 runner 摘要。对任何不同摘要写明不得继承旧审查。
- [ ] 核对原工程 `evidence/` 的必要收据与原始 IQ 可读性；只复制 P1 必需的封存件及校验清单到新工程的受控证据目录，避免整体复制巨量临时测试树。复制前后比对 SHA-256。
- [ ] 冻结一个 `SOURCE_BASELINE` 收据，把所用源码/数据/工具版本和 `NOT_RUN` 项独立列出。提交文档与小型清单；原始 IQ 可以留在非 Git 证据目录，但须有可验证路径与摘要。

**Gate P0-A:** 每条简介要求可追到责任模块；历史事实有可检查的原始证据或显式 `UNVERIFIED`；`final_prj` 不引用不存在的本地证据为已验收。

### Task 1.2：离线测试与构建基线

**Files:** `pynq_overlay/sw/tests/`、`scripts/tests/`、`rf_overlay/sw/board_app/Makefile`；输出 `docs/reports/2026-10-13-final-prj-baseline.md` 与独立测试日志。

- [ ] 在可写且全新的 `--basetemp` 下重跑已确定的五组核心测试。已观察到 `289 passed, 5 skipped`；系统默认临时目录会引起权限错误，不能将其记作产品失败。示例：`python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-p0 -q pynq_overlay/sw/tests/test_rf_iq_signal.py pynq_overlay/sw/tests/test_rf_bpsk_receiver.py pynq_overlay/sw/tests/test_rf_rx_analysis.py pynq_overlay/sw/tests/test_rf_rx_constellation.py pynq_overlay/sw/tests/test_rf_cnn_runtime.py`。
- [ ] 再运行 `python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-p0-full -q pynq_overlay/sw/tests scripts/tests`；按依赖缺失、证据缺失、真实代码失败三类记录，不直接修改测试期待值以求全绿。
- [ ] 检查开发机 `make`/C 编译器可用性，再执行 `make -C rf_overlay/sw/board_app host-test` 与 `host-test-iq`；板上 ARM 原生构建保持独立门禁。本步骤若工具不存在，记录准确版本与 `NOT_RUN`。
- [ ] 核对 `rf_overlay/build.tcl`、`pynq_overlay/build.tcl`、发布 manifest 和软件 map 与当前源码是否相符。需要 Vivado/板卡的检查先记录构建输入，不在 P0 自动下载位流。
- [ ] 将测试、跳过、失败、工具版本、源码摘要和清理后的 `git status` 写成基线报告；临时目录只在确认解析路径位于 `final_prj` 后使用同一 PowerShell 会话删除。

**Gate P0-B:** 关键软件链路可在新目录复现，未知依赖有清单；原工程测试结果不直接冒充新目录结果。

### Task 1.3：产品入口与模块责任

**Files:** `README.md`、`CLAUDE.md`、`docs/requirements/brief-traceability.md`；保留 `AGENTS.md` 为入口索引。

- [ ] 在 README 增加“作品 RF 主路径”和“辅助 AWG 路径”的入口、构建物、发布物及当前能力状态。保留 `host/main.py` 为现有 AWG GUI 入口，不误称其为 RF 产品唯一入口。
- [ ] 固定新 RF 产品的状态/证据对象契约：`release_id`、`capture_id`、连接代次、配置修订、采样域、算法/模型版本、原始 IQ 摘要、当前/历史标识、关闭状态。写文档级模式表，确认现有 `RfStatusWorker`、`RfStatusClient`、`rf_experiment_record.py` 的责任。
- [ ] 为 P1–P6 建立各自可更新的验收清单，列出离线、ARM、数字回环、有线物理、真人和发布六类门；提交 P0 文档。此前不调整射频算法或阈值。

**Gate P0 完成:** `final_prj` 可重建基线、目标逐项可追踪、原任务切换记录和证据边界明确。此后才把 DeepSeek Harness 的工作目录改指向新副本。

## Chunk 2 — P1：迁入并关闭有限 BPSK/环境诊断链

### Task 2.1：HEADROOM runner 的板端运行包

**Files:** `scripts/rf_headroom_research_run.py`、`scripts/rf_teaching_install_state.py`、`scripts/tests/test_rf_headroom_research_run.py`；新增受审的板端启动/打包文件，位置由独立封装规格固定。

- [ ] 从 P0 摘要出发复核 B1/B2/B3/B4：受控发布导入路径、checker 缺失后的未知收据、板端持久 nonce 账本和单次持久作业。旧离线批准只覆盖旧摘要。
- [ ] 先写板端目录布局、root 权限、上传摘要、启动参数、单次派发与只读观察的规格；固定运行包来源为 `final_prj` 的已提交源码，不能隐式依赖原仓库 `scripts` 包。
- [ ] 增加“无仓库路径”的干净子进程测试及缺文件/错摘要/无权限/断线/重启/nonce 重放/研究后 checker 失效等负控；使红态可见后补实现。
- [ ] 运行 runner 全套、冻结原生/软件门禁和受影响的变异测试；生成绑定最终源码与包摘要的新收据，并由未参与实现者复审。
- [ ] 仅在上述通过后做真板**只读**导入、服务、发布、存储及 RF safe 预检。人工接线、一次性 token 和单次物理研究为另一个显式执行门；未完成则状态保持 `NOT_RUN`。

**Gate P1-A:** 新目录能产出可审查的板端运行包和独立读回路径；任何失败有具名拒绝/未知收据，零重复派发。

### Task 2.2：S2 正式环境诊断闭环

**Files:** `pynq_overlay/sw/rf_environment.py`、`rf_environment_baseline.json`、`rf_status_worker.py`、`ui_model.py`、`ui_view.py`、`rf_experiment_record.py`；`scripts/rf_environment_{dataset,acceptance,verify}.py` 与相关 tests。

- [ ] 用有限 46 dB 研究判定能否得到稳定、低削顶的 BPSK 实验域；若失败只做单变量定位，不以旧 40 dB 基线替代。
- [ ] 冻结 TRAIN/VAL/TEST 的真实会话分组、正常/窄带/脉冲/负控定义和阈值。按既有合同完成开发复验，再进行正式 108 物理帧和 12 离线负控；逐类门槛与失败计数采用冻结合同。
- [ ] 在相同 IQ 上复跑主机/ARM 分析，核对实验记录、实体屏当前/历史状态、失效/停止与最终安全读回。所有失败帧进入收据。
- [ ] 保持已有 BPSK3M RECT、星座、BER 与触摸操作的回归；把环境状态严格描述为“采样窗口内”。

**Gate P1 完成:** BPSK 教学链和环境诊断在新工程版本下有完整证据；S2 若失败则禁止以“可识别干扰”作为已完成作品功能。

## Chunk 3 — P2：多制式 RF 发射和 PL/PS 性能边界

### Task 3.1：TX 模式合同与独立参考

**Files:** `pynq_overlay/sw/rf_iq_signal.py`、`rf_status_worker.py`、`rf_overlay/sw/board_app/rf_iq.c`、`rf_overlay/rtl/phy_tx.v`；新规格 `docs/superpowers/specs/2026-10-13-rf-tx-mode-contract.md`，测试 `pynq_overlay/sw/tests/test_rf_iq_signal.py`、`rf_overlay/sw/board_app/test/test_rf_iq.c`。

- [ ] 逐模式冻结 BPSK/QPSK/QAM16/QAM64 的 Gray 映射、符号率、PRBS/用户数据位序、RECT/RRC 系数、群延迟、量化、占用带宽和拒绝条件；参考向量须独立于被测生成器。
- [ ] 写/补测试覆盖各星座所有码字、随机序列、RRC 脉冲与极端幅度；再补最小实现或修正。
- [ ] 依次执行离线 golden、AD9361 片内数字回环（经 RF PL 样点路径采集）、有线 TX1→RX1 实采；每模式记录实际配置/频谱/削顶/关闭读回，不以一个 BPSK pilot 覆盖其他模式。片内数字回环只证明对应收发路径，PL 算法须有独立仿真与时序证据。
- [ ] 测量 PS 生成、拷贝、PL 传输的吞吐/CPU/RSS/延迟，再决定是否需要把成形或其他实时样点处理迁入 RF PL。若迁移，单列 RTL 设计、仿真、网表、时序和物理门。

**Gate P2:** 模式矩阵逐行有发布身份与实测状态；简介中的“PL 实时样点处理”能对应具体模块和性能证据。

## Chunk 4 — P3：接收机、EVM 与可查看数据

> **2026-10-23 状态（有证据）：** 统一结果契约、QPSK/RECT 接收机与产品接入为
> **CODED / OFFLINE_VERIFIED**；最终候选 `6d081602…`（contract `cnn-bpsk-modrec4-qpsk-v1`，
> parent `da97347a…`）冻结产品入口已 **ARM_VERIFIED**（12/12 同 IQ，含 5 真实 QI；上一候选
> `be308d11` 的 Codex 阻塞已修复）。最终 Codex 审查通过后，已完成**一次无 RF 开发版试部署**：
> **TRIAL_ACCEPTED**，current/active=6d081602，boot `ac7e8221…`，独立读回
> `VERIFIED_SAFE_EXPECTED`；随后完成一次 **6 帧真实 QPSK 解调冒烟**（3/3 QPSK sync、BER 0/128，
> BPSK 兼容 0/138，OFF 2/2 无同步/BER，post `VERIFIED_SAFE_EXPECTED`）。仍不是 BOARD_VERIFIED：
> 未做实体屏、扩展工作点/物理 BER 资格；不宣称低 BER/总体准确率。
> 详见 `docs/reports/2026-10-23-p3-qpsk-offline.md`、
> `evidence/rf-teaching/s2-46db-p3-qpsk-candidate-2026-10-23/CANDIDATE-REPORT-FINAL.md` 与 ARM 证据目录。
> EVM、RRC、QAM16/64、真实 RF/物理 BER、UI 实屏仍未执行；Task 4.1/4.2 不能据此刻成完成。

### Task 4.1：统一接收结果契约

**Files:** 现有 `pynq_overlay/sw/rf_rx_types.py`、`rf_bpsk_receiver.py`、`rf_rx_constellation.py`；新建 `rf_modem_result.py` 与 `tests/test_rf_modem_result.py`。

- [ ] 以当前不可变 IQ 帧为唯一输入，定义公共结果身份、模式/采样域、载波/定时/参考同步、拒绝原因、符号/比特有界预览、BER 分子分母和 EVM 可用性。
- [ ] 保持既有 BPSK 算法与结果兼容；先写错帧 ID、错发布、过期、削顶和无参考负控，确认任何旧结果不能留在新当前帧上。

### Task 4.2：QPSK → RRC → QAM16/64 的递进接收

**Files:** 新建 `pynq_overlay/sw/rf_qpsk_receiver.py`、`rf_rrc_receiver.py`、`rf_qam_receiver.py` 及同名 tests；接入 `rf_status_worker.py` 和当前 UI model/view。

- [ ] QPSK 先固定一个与 TX 合同一致的 RECT 工况：频偏、定时、四相歧义、独立参考对齐和 BER。解析矢量、不同随机种子/相位/频偏、错误参考和真实有线会话逐层验收。
- [ ] RRC 接收单独冻结匹配滤波系数、群延迟、采样边界和过渡符号排除；先在 BPSK/QPSK 的同一真实波形上验证，再把“RRC 已接收”标为支持。
- [ ] QAM16 后 QAM64：幅相归一化、均衡、星座判决与置信拒绝分开验证；不得用 CNN 类别或发送配置推出同步成功。
- [ ] 每项对照独立的解调参考，记录主机/ARM 同 IQ 判等、受支持域、处理时间和资源；物理 BER 门限必须在 TEST 采集前写定。

> **2026-10-24 RRC 离线状态（有证据）：** BPSK/QPSK RRC 接收机与独立参考已完成，
> **CODED / OFFLINE_VERIFIED**；首域 60 MSPS/3 MSym/s/sps20/α0.35/4096，BPSK/QPSK，
> 161 taps、transition=4、valid symbols 4..122（119）、BER 区 67..122（BPSK 56/QPSK 112 bits）。
> A 单测 64 + B 独立 22 = 86 passed；RECT 兼容 100 passed。
> **2026-10-24 优化产品候选追加：** 候选 `bedf98c7…`（parent `6d081602…`，contract
> `cnn-bpsk-modrec4-qpsk-rrc-v1`，60）已 ARM_VERIFIED 21/21；v3 receiver 将 legal 16.3–17.4 s
> 降到 cold 1.5599 s/hot 0.5377 s，stop cold1+hot3 均 ≤5.75 s；旧 90ac6b 慢候选归档不安装。
> Codex 审查的 F1 下载后改写/F2 根目录旧证据已修复（final-arm-v2 原始 sha 一致、旧证据归档）。
> **2026-10-24 无 RF 试部署 TRIAL_ACCEPTED**：current/active=`bedf98c7…`，boot `ac7e8221…` 不变；
> modrec4+RRC 验收 passed，独立 post 读回 `VERIFIED_SAFE_EXPECTED`，无 rollback。未做真实 RRC/
> 物理 BER/UI 交互。详见
> `evidence/rf-teaching/p3-rrc-offline-2026-10-24/RRC-OFFLINE-FINAL.md` 与
> `evidence/rf-teaching/s2-46db-p3-rrc-candidate-2026-10-24/CANDIDATE-REPORT-FINAL.md`。

### Task 4.3：EVM、BER 和数据页面

**Files:** 新建 `pynq_overlay/sw/rf_rx_metrics.py` 与 tests；修改 `rf_experiment_record.py`、`ui_model.py`、`ui_view.py`。

- [ ] 在规格中固定 RMS EVM 的理想点/等化/归一化口径、测量符号区间和参考可用条件；用已知注入误差向量验证数学值，不能靠拟合把误差消掉。
- [ ] BER 分母只来自独立参考比特；无参考、失步或域外时返回明确不可用。接收数据页分开展示原始 IQ、判决符号与比特，不把它们写成协议载荷。
- [ ] 在物理 TEST 前冻结跨独立会话的聚合规则、最小参考符号/比特数、失败帧计入方式和 BER 置信区间/验收口径。4096 点、60 MSPS、3 Msym/s 的单帧约有 205 个符号，不能凭单帧零错宣称低 BER；EVM 也须规定最小有效符号数和跨会话统计。
- [ ] 实体屏核对同帧星座、频谱、EVM、BER、数据预览和历史记录；配置变化、停止、失联、超时立即使当前结果失效。

**Gate P3:** 每个制式和滤波模式单独通过离线、ARM、真实有线及 UI 门；EVM/BER 定义可重放、失败不显示旧数值。

## Chunk 5 — P4：环境识别与可信度

**Files:** `pynq_overlay/sw/rf_environment.py`、`rf_cnn_runtime.py`、`rf_status_worker.py`；新建 `rf_environment_confidence.py` 与 tests，更新 `scripts/rf_environment_{dataset,acceptance}.py`。

- [ ] 将调制识别 CNN 与环境诊断分开：前者识别信号类型，后者识别当前采样窗口内的正常、窄带、脉冲、复合/域外/未知。
- [ ] 定义时间/频率特征、低能量/削顶/失步拒绝、短窗口漏检边界；对 4096 点快照的不可观测事件写明 `UNKNOWN`，不称持续监测。
- [ ] 按真实会话和物理配置划分 TRAIN/VAL/TEST，派生窗口不得跨组。模型分数经可靠性校准后才用“可信度”；未校准时只显示分数和拒绝原因。
- [ ] 冻结混淆矩阵、逐类召回、未知拒绝、ARM P95/RSS 和实体屏效果门；每次模型更新绑定权重摘要与环境版本。

**Gate P4:** 环境标签和可信度在预声明域内有独立真实数据支持，域外结果可靠拒绝。

## Chunk 6 — P5：已验证档位选择、人工接管和异常回退

**Files:** 新建 `pynq_overlay/sw/rf_profile_registry.py`、`rf_profile_policy.py` 及 tests；修改 `ui_model.py`、`ui_view.py`、`rf_status_worker.py`、`rf_experiment_record.py`。

- [ ] 先建立只读档位注册表：每档固定 RF 参数、发布摘要、实测安全域、正常接收资格和成对干扰收益/代价。没有合格第二档时策略只可输出“保持/观察/停止/无法判断”。
- [ ] 策略纯函数只读当前诊断、可信度、接收质量、档位资格、设备安全状态和人工模式，输出建议档及完整理由；不直接调用硬件。先写过期、错版、削顶、无收益、模型失效、断线等拒绝测试。
- [ ] P5a 中间门：由操作员确认后填入草稿并经已有唯一控制链重新校验/加载/读回；取消与手动接管总是优先，历史决策不可再执行。
- [ ] P5b 最终作品门：仅在独立审查的档位白名单、前后安全读回、看门狗、可证明的人工抢占和物理收益门通过后，开放受约束自动选择。AUTO 默认关闭；任何未知结局安全停止并保存证据。P5a 的建议通过不自动升级为 P5b 资格。若 P5b 未通过，简介中的智能选档保持 `NOT_VERIFIED`，作品整体不能按该简介宣称完成。
- [ ] 用交错顺序的成对物理实验验证各档收益；不筛掉失步/失败帧。最终作品说明准确区分“智能推荐”与“受约束自动执行”的已验收级别。

**Gate P5:** P5a 人工确认链与 P5b 受约束自动选择分别验收；选档只发生在已验证域，人工接管和异常回退有实体屏、daemon 与独立读回证据。

## Chunk 7 — P6：统一发布与作品验收

**Files:** `README.md`、`CLAUDE.md`、`docs/rf/boardtest.md`、`scripts/release_manifest.py`、`scripts/rf_teaching_verify.py`、`pynq_overlay/sw/ui_model.py`、`ui_view.py`；新建 `docs/reports/final-platform-acceptance.md` 和复现清单。

- [ ] 固定 RF 软件/PL/daemon/模型/策略的完整发布清单和回退版本；在单次运行中核对 `current`、`active`、bit/HWH 与 UI 能力表一致。
- [ ] 按“离线重放 → AD9361 片内数字回环 → AD9361 有线模拟回环 → 多会话真实采集 → 实体屏”逐层执行；覆盖各调制、环境四类、P5b 自动选档、人工接管、异常拒绝和最终关闭。PL 内部处理另按仿真、网表、时序及板上观测验收。
- [ ] 保留原始 IQ、请求/实际参数、失败帧、版本和独立重放收据；给每一句作品简介附上能够直接复核的验收依据。没有通过的项继续写“拟支持”。
- [ ] 最终交付接线图、操作步骤、复现脚本、性能/资源/限制报告和独立复审结论。

**Gate P6:** 简介逐句可证，板端安全状态可独立读回，工程能由固定版本和原始数据复现。

## 首批执行包与检查顺序

1. **现在先完成 Task 1.0，同时可独立执行 Chunk 1 的只读盘点、离线测试和规划。** 现有五组离线基线为 289 passed、5 skipped；停写确认后再迁移证据、固定切换摘要。任何缺失证据先登记，不立即补物理采集。
2. Chunk 1 通过后，从 `final_prj` 的源码摘要开始 Chunk 2；旧 DeepSeek Harness 在原目录的 B1–B4 记录作为需求与反例，不再在原目录继续改码。
3. P2 的 TX 模式规格可与 Chunk 2 的离线封装审查并行写作；任何板卡运行仍按单板单任务安排。
4. 每个 Chunk 都在新分支作小提交，附当次测试命令、原始退出码和证据摘要；进入下一 Chunk 前做独立代码/证据复审。
