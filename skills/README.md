# 技能包使用说明（Skills）

本目录是本项目的可复用技能包入口，对应赛事指南 **3.3.5.2 技能包** 与 **3.3.5.3 设计报告**中的"技能包提炼过程"要求：每一套技能都写明**适用范围、使用方法、失效条件、来源与已验证效果**，目标是让一个不熟悉本项目的队伍可以直接拿去用在自己的题目上。

三套技能均为**方法、流程与踩坑清单**，不含凭据、不授予硬件操作权限、不包含原始 IQ/收据/位流等不可分享文件。协作记录（提示词、模型回答、自我纠错轨迹、智能体工作流）另见 [大模型协作记录](../docs/engineering/AI-COLLABORATION-RECORD.md)，案例摘编见 [AI 协作案例](../docs/engineering/AI-COLLABORATION-CASE.md)。

- 本目录文件数：**25**（三套技能 24 个文件 + 本入口 README）。
- 技能内容只代表其成文时点的方法与边界；历史指标按文档记载引用，不在本仓库内可复算（见 [交付材料索引](../docs/engineering/DELIVERY-INDEX.md) §9）。

## 1. 一分钟选择技能

| 你要做的事 | 用哪套 | 入口 |
|---|---|---|
| 维护/接手本项目的 RF 构建链、板载应用、接收算法、外部 SDR 联调、候选发布与证据交付 | `final-prj-rf-development` | [SKILL.md](final-prj-rf-development/SKILL.md) |
| 在 PYNQ 板卡上开发/排查 Linux 应用：Overlay 加载与校验、AXI/MMIO/BRAM、systemd 服务、部署与回退、触摸界面与数据有效性 | `pynq-linux-development` | [SKILL.md](pynq-linux-development/SKILL.md) |
| 用 GNU Radio/gr-iio 控制 ADALM-Pluto 做基础收发测试、USB IIO 排障、示波器联动与证据分级 | `pluto-gnuradio-testbench` | [SKILL.md](pluto-gnuradio-testbench/SKILL.md) |

三套技能**互相独立**，不要求顺序安装。与具体题目强绑定的是第一套；第二套是面向 PYNQ 框架的通用技能；第三套面向外接 PlutoSDR 测试台。

## 2. 技能包按指南可以是什么（本目录的对应）

指南 3.3.5.2 允许技能包是提示词工作流、案例模板、校验脚本、踩坑清单中的任意一种或多种。本目录的对应关系：

| 指南类型 | 本目录中的体现 |
|---|---|
| 提示词工作流 | 三套 SKILL.md 的"先读 → 按问题选读 → 执行 → 判定"路由结构；可复用提示词骨架与任务卡模板见 [协作记录](../docs/engineering/AI-COLLABORATION-RECORD.md) §1.5、§6 |
| 案例模板 | `pluto-gnuradio-testbench/assets/testbench/` 是可移植测试台快照（源码、配置、GRC、离线测试），提供 `scripts/prepare_testbench.py` 一键复制到新工程 |
| 校验脚本 | `pluto-gnuradio-testbench` 的离线测试与结果索引脚本；`final-prj-rf-development/references/release-and-evidence.md` 中的候选/证据核验流程；`pynq-linux-development/references/linux-and-deployment.md` 中的只读基线命令 |
| 踩坑清单 | 三套技能的 references/ 与 SKILL.md "关键约束"节；来源见 [工程陷阱 16 条](../docs/memory/04-engineering-pitfalls.md) |

## 3. 技能一：`final-prj-rf-development`（本项目 RF 平台开发）

**文件**：`SKILL.md` + `agents/openai.yaml` + 3 个 references（`project-map.md`、`receiver-and-sdr.md`、`release-and-evidence.md`），共 5 个。

### 3.1 适用场景

维护本项目（AC820 / Zynq-7020 + AD9361 便携收发与接收分析平台）的 RF 主线：三条流水线的边界与构建入口、PYNQ 板载触摸应用与 daemon 的控制/显示分工、接收算法与外部 Pluto 发射联调、候选发布与证据交付。也适用于"接手一个结构相似的多流水线 FPGA/SDR 工程"时参考其**证据纪律**（但须替换为对方工程的实际路径与合同）。

### 3.2 使用方法

1. 从 [SKILL.md](final-prj-rf-development/SKILL.md) 的"最少入口"开始：先读根 `AGENTS.md`/`CLAUDE.md` 的历史状态说明，再按任务读维护文档。
2. 按"工作路由"选 references：
   - 目录、构建、服务拥有权、测试范围 → [工程地图与入口](final-prj-rf-development/references/project-map.md)
   - 接收模式、外部 Pluto 源、人工验收与历史结论边界 → [接收与 SDR 联调](final-prj-rf-development/references/receiver-and-sdr.md)
   - 候选、执行预算、试部署、收据、分享脱敏 → [交付与真实证据](final-prj-rf-development/references/release-and-evidence.md)
3. 典型流程：**核对最新原始收据与实际代码 → 选一个有辨识力的最小问题 → 相关离线/假硬件测试 → 一次适用增量审查（R0/R1/R2，见 [开发审查规则](../docs/requirements/development-review-policy.md)）→ 候选/试部署/回退 → 收据与索引**。
4. 硬件动作前确认授权与现场条件；技能本身不提供安装、重配置或发射授权。

### 3.3 失效条件与不适用

- 工程路径、流水线职责或合同已改变时：技能中的入口名与判据可能过期，**以当前仓库收据与实际代码为准**。
- 不适用于其他项目：本技能明确绑定本项目；跨项目只能参考其纪律与方法，不能照抄路径、判据和发布流程。
- 不能把 AWG（辅助信号发生器）结果或历史回环结果当作 RF 产品当前验收；两者是不同流水线，产物不混算。
- 阶段决定（如 QAM64 移出交付、外部 SDR 优先）不是硬件永久规则；用户改变范围后以新证据为准。
- 技能不覆盖：真实板卡权限、现场接线确认、凭据与网络配置（由私有配置和当前操作者提供）。

### 3.4 来源（从哪些实践/失败提炼）

| 技能中的约束 | 来源实践/失败 | 仓库出处 |
|---|---|---|
| 不把"测试通过"当"物理通过"；未执行项写 `NOT_RUN` | 多轮阶段验收中混淆离线与物理证据 | [门禁模型](../docs/memory/02-gate-model.md) §1–§2 |
| 失败/坏结果保留、不重试、不补采凑数 | 采集超预算、重派凑帧、失败后换名再跑等事故被复审抓出 | [开发纪律](../docs/memory/03-development-discipline.md) §5；[交付复审](../docs/reviews/2026-10-26-codex-delivery-closeout-review.md) |
| 用启动账本与全量统计解释"实际执行了多少次" | 实际 4 会话/13 采集与计划 3/12 不一致被复审要求如实记账 | [交付复审](../docs/reviews/2026-10-26-codex-delivery-closeout-review.md)；[开发纪律](../docs/memory/03-development-discipline.md) §6 |
| 未知终态先只读读回、不盲目重装 | 安装 240 s 超时后板卡自行重启、原结论只能记 `UNKNOWN` | [会话复盘与板卡恢复](../docs/reports/2026-10-12-session-review-and-board-recovery.md) |
| 假硬件测试的盲区（谓词只在真板暴露） | `lease_held` 谓词写反，真实研究永远必拒；全部假硬件 peer 都报 false | [HEADROOM 安装门与预检](../docs/reports/2026-10-11-headroom-install-gate-and-preflight.md) |
| 候选/证据绑定到"实际部署输入" | 过时 daemon 被夹带进生产包、首次安装失败并自动回滚 | [产线续接审核](../docs/reports/2026-10-03-production-board-continuation-audit.md) |
| 外部 SDR 统计"23/30 同步、整体未通过"，不能只报成功帧 | 弱信号/未同步帧被剔除后称"统计通过"的风险 | [README](../README.md) §失败与限制、[交付材料索引](../docs/engineering/DELIVERY-INDEX.md) §5 |

### 3.5 已验证效果

- 技能中的每条硬约束都能追溯到本仓库的公开报告或工程记忆（见 3.4 表），不是凭空总结。
- 2026-10-30 工程整理时，本技能与 `pynq-linux-development` 一起按 `skill-creator` 格式通过校验，并与个人技能安装目录逐文件 SHA-256 一致（[工程整理记录](../docs/engineering/2026-10-30-ORGANIZATION-REPORT.md) §完成内容）。
- **尚未验证**：没有做过"使用本技能 vs 不使用"的对照实验；没有陌生队伍试用记录；作为项目专用技能，跨项目复用性**不适用**（也不据此主张加分）。

## 4. 技能二：`pynq-linux-development`（通用 PYNQ 板载 Linux 开发）

**文件**：`SKILL.md` + `agents/openai.yaml` + 3 个 references（`overlay-and-axi.md`、`linux-and-deployment.md`、`ui-and-data.md`），共 5 个。

### 4.1 适用场景

在 PYNQ（Zynq / MPSoC 板载 Linux）上开发或排查**软硬件结合**的应用：Overlay/bit/HWH 的加载与地址契约、AXI MMIO 与 BRAM、systemd 服务与 sidecar、远程部署与回退、触摸界面与数据有效性、证据保存与脱敏。面向 PS/PL 软件集成，不绑定具体题目；具体板卡引脚、射频参数与合同以目标工程资料为准。

### 4.2 使用方法

1. 从 [SKILL.md](pynq-linux-development/SKILL.md) 开始，先确定本次改动位于 **PL / PS 驱动与控制 / 数据分析 / UI / 部署** 哪一层——不要把一层通过当成整条硬件链通过。
2. 建立运行基线：确认板型、镜像/PYNQ 版本、Python 解释器、bit/HWH、地址表、服务名；动手前读发布、服务、`boot_id`、存储健康与硬件忙闲（只读基线命令见 [Linux 服务与部署](pynq-linux-development/references/linux-and-deployment.md) §只读基线）。
3. 按问题选读：
   - 加载失败、MMIO 恒零、BRAM 错位、PS/PL 时序 → [Overlay 与 AXI 数据路径](pynq-linux-development/references/overlay-and-axi.md)
   - 服务启动、远程操作、试部署、SSH 失联或回退 → [Linux 服务与部署](pynq-linux-development/references/linux-and-deployment.md)
   - 屏幕结果、线程边界、采集身份与性能 → [界面与数据有效性](pynq-linux-development/references/ui-and-data.md)
4. 典型流程：**只读基线 → 定位层 → 最小复现 → 离线/假硬件测试 → 目标机（ARM/真板）验证 → 一次适用增量审查 → 一次试部署 → 板端验收 → 独立只读读回 → 留下回退版本与收据**。
5. 每步记录"实际改动层、运行版本、执行过的检查、板端动作与终态、尚未验证的部分"。

**典型命令示例（模式，不是本机固定值）**

只读基线（先确认"现在是什么"，再动手；服务名与路径按目标工程替换）：

```sh
uname -a
cat /proc/sys/kernel/random/boot_id
cat /proc/uptime
readlink -f <release-root>/current
cat <active-release-marker>
systemctl show <overlay-owner.service> <sidecar.service> \
  -p ActiveState -p SubState -p MainPID -p NRestarts
df -h / /boot
dmesg
```

加载/验证与部署前的定位顺序（命令来自目标工程现有入口；不要自造并行入口）：

```sh
# 1) 先看拥有者与 sidecar 是否健康；服务 active ≠ 功能健康
systemctl status <overlay-owner.service> <sidecar.service> --no-pager
journalctl -u <overlay-owner.service> -b --no-pager | tail -50

# 2) Overlay 加载/校验由拥有者负责；调试脚本不得在服务持有时再次 Overlay()
#    需要隔离实验时，先按工程流程交接拥有权，再在独立进程做一次受控加载与只读寄存器检查。

# 3) 发布/部署前后用工程既有只读读回入口（示例为通用形式）
python3 -B <工程既有只读状态入口> --expect <release-id>

# 4) 不可变发布上避免字节码污染；服务环境需显式路径
#    systemd: WorkingDirectory/PYTHONPATH/解释器按镜像核对，不用交互 shell 的默认值
python3 -B <入口>.py
```

**部署/回退的固定闭环**（细节以目标工程 runbook 为准）：候选绑定 → 相关测试 → 一次适用增量审查 → 新鲜预检（发布/服务/`boot_id`/存储/RF 安全态）→ 一次试部署 → 既定验收 → 独立只读读回 → 失败按预先声明的回退路径执行并复验父版本；超时/失联先只读取证，不重装。

### 4.3 失效条件与不适用

**框架与版本**

- **不适用于非 PYNQ 的硬件加速栈**：Alveo/XRT 主机卡、纯裸机 PS、Vitis AI/DPU 的部署模型与 PYNQ Overlay/服务模型不同，须核对官方框架文档再套用。
- **不适用于任意 Linux 发行版**：systemd 单元布局、PYNQ 源码树安装方式、XRT 路径随镜像而变；技能中的命令是模式，不是固定路径。
- **PYNQ/镜像版本变化时基线命令可能失效**：PYNQ 2.x 与 3.x、不同镜像的 `PYTHONPATH`/解释器/设备节点/服务单元布局不同；先按目标镜像重跑只读基线，不要照抄版本相关路径。
- **板卡身份与引脚不通用**：引脚约束、FMC/接口电平、时钟与复位、DMA/CMA 容量都依板卡与 Overlay 而定；PS 时钟（如 FCLK 分频）被错误"重设"会把正确设计改错，必须按目标工程资料重核。

**Overlay 与地址**

- **目标工程不是"单一拥有者"模型时**：若每次脚本各自加载 Overlay，本技能的核心约束需要先改造成对应架构，不能直接照搬；否则多个加载者会互相覆盖、卡死或丢外设。
- **bit/HWH/软件地址常量不同次构建时**："同名"不代表同次构建；覆盖合并发布路径会丢失其他外设，必须成套验证 HWH 地址/范围与软件常量。
- **把"寄存器回读正常"当全部通过时**：配置回读只证明配置接口，不证明时钟到达、逐线延迟实际值或样本完整性；MMIO 字节偏移/位宽/有符号解释/端序错误会表现为"读恒零"或数据错位。
- **在没有隔离实验环境时**：不要在有副作用寄存器上"试写看看"；先在已知只读状态、普通寄存器或隔离 BRAM 区逐层定位。

**权限与执行环境**

- **权限不足/受限沙箱**：`/dev/mem`、framebuffer、USB/VISA 等设备权限不足会表现为"设备不存在/空列表"；先区分权限问题与真实未接设备，不删除身份校验。
- **systemd 环境与交互 shell 不同**：工作目录、解释器、环境变量（尤其 `PYTHONPATH`/XRT 路径）可能不同；服务 `active` 不代表功能健康，检查 READY/发布标记/实际帧缓冲与 `NRestarts`。
- **在不可变发布目录里运行 Python**：不带 `-B`/`PYTHONDONTWRITEBYTECODE=1` 会把 `__pycache__` 写进只读候选，使实际发布树偏离清单；成员校验会报非常规成员。
- **共享单例资源**：板卡、`current` 发布、pytest 全局临时目录、git index 一次只允许一个写者；并发时最坏不是文件冲突，而是两份"看起来都对"的证据互相覆盖。

**界面与数据有效性**

- **显示标签替代实测时**：界面上的"草稿/已应用参数/加载成功/实际发射/历史接收结果"必须区分；显示标签不能替代设备回读或测量。
- **旧结果未失效时**：新配置、停止、断线、发布变化或清除快照后，旧 BER/旧星座/旧图必须失效；历史结果可保留为历史，但不得显示为新帧成功。
- **短 capture_id/会话隔离不足时**：独立会话可能重用短 capture_id，归档去重必须带会话路径/摘要，不能只按该字符串。
- **单次小探针性能当产品路径时**：PS 性能要测整条处理路径的分位数与 RSS，并记录输入长度/采样率/算法/权重版本；离线测试可验按钮/失效/线程边界，真人屏幕验收另记。

**部署与回退**

- **SSH 超时/终态未知时**：远端持久作业可能在断线后继续；正确动作是另开只读会话读回 `current`/`active-release`/服务/`boot_id`/RF 安全态，而不是再次安装。
- **断电/存储故障/强杀之后**：不能保证事务完成或自动回退；保留 `UNKNOWN`，先取证，不重派。
- **技能本身**：不提供安装/重配置/发射授权；不包含凭据；历史读回不等于当前状态，现场接线与设备身份以当前操作者确认为准。

### 4.4 通用性说明（本技能与具体题目解耦的部分）

本技能是本项目三套技能中**最接近指南"通用 PYNQ Skill"** 的一套：它写的是 Overlay/AXI、Linux 服务与部署回退的方法，而不是本项目的射频实验参数。具体来说：

- **Overlay 与地址契约层**：同一套 PL 只能有一个重配置拥有者；bit/HWH/软件地址常量必须成套验证（同名不等于同次构建）；MMIO 偏移是字节偏移；BRAM 的 `Enable_32bit_Address` 配错会静默综合成空网表；`enable`/`arm`/`commit-toggle`/`wrap` 是不同语义。这些与板卡型号、题目无关，凡"PL 逻辑 + PS 通过 AXI 控制"的 PYNQ 应用都适用。
- **Linux 服务层**：Overlay 拥有者先校验发布再报告 READY，sidecar 依赖其后启动；systemd 的工作目录/解释器/环境不同于交互 shell（源码树安装的 PYNQ 镜像常需显式 `PYTHONPATH`）；对不可变发布用 `python3 -B` 防止 `__pycache__` 污染；服务 `active` 不代表功能健康；反复 `NRestarts` 是失败线索。这些是 PYNQ 板载应用的通病，可直接迁移。
- **部署与回退层**：试部署闭环（候选绑定 → 相关测试 → 一次审查 → 新鲜预检 → 一次安装 → 既定验收 → 独立读回）；远程持久作业可能在 SSH 断线后继续，超时只能先只读读回，不能盲目重装；断电/MMC 故障不保证事务完成。凡"板载 Linux + 远端安装 + 需要回退"的项目都可复用。
- **界面与数据有效性层**：UI 只请求动作并消费实际结果；草稿/已应用参数/设备回读/历史快照分开；新配置、停止、断线、发布变化时使当前分析失效。凡有触摸屏/本地显示的 PYNQ 设备都适用。
- **证据与安全层**：原始 IQ/日志/收据保留原件，脱敏在分享副本进行；只记凭据来源或路径，不输出值。可迁移到任何需要"可审计证据"的工程。

**可在其他项目/板卡复用的候选场景**（按方法层面判断，**均未实测**）：其他 Zynq-7000 PYNQ 2.x 板卡上的 Overlay 加载与自检流程；MPSoC/Kria 类 PYNQ 板卡的 systemd sidecar 与试部署回退骨架；任何"PL 寄存器 + PS Python 应用 + 触摸界面"的竞赛作品。

**尚未验证**（不宣称加分已获实证）：

1. **跨项目验证：尚未验证。** 本仓库没有第二个使用该技能的独立项目记录。
2. **跨板卡验证：尚未验证。** 未在除本项目 AC820/Zynq-7020 + PYNQ 2.7 镜像以外的板卡上跑过；MPSoC/Kria/Alveo 的 PYNQ 版本、XRT、DMA/CMA API 差异未验证。
3. **跨框架版本：尚未验证。** PYNQ 3.x、不同镜像的 systemd/源码树布局可能改变基线命令与 `PYTHONPATH` 处理。
4. **陌生队伍复用演练：尚未执行。** 因此指南所说"换题目、换一块板卡仍然可用"在本仓库只能作为**方法层面**的主张，不虚构加分案例。

### 4.5 来源（从哪些实践/失败提炼）

| 技能中的约束 | 来源实践/失败 | 仓库出处 |
|---|---|---|
| 同一套 PL 只能有一个重配置拥有者 | 守护进程持着 PL 时再次调用 `Overlay()`，历史上两次卡死 | [工程陷阱](../docs/memory/04-engineering-pitfalls.md) §16 |
| 区分独立测试 Overlay 与产品合并 Overlay；bit/HWH 与软件地址契约成套验证 | 把独立 bit 覆盖到合并服务路径会丢失其他外设；BD 内嵌 BRAM 的地址选项配错会静默综合成空网表（实际踩过） | [Overlay 与 AXI 参考](pynq-linux-development/references/overlay-and-axi.md) §构建与加载；[工程陷阱](../docs/memory/04-engineering-pitfalls.md) §16 |
| `__pycache__` 污染不可变发布 | 板上 release 启动后冒出 `__pycache__`，成员校验报 `nonregular candidate member` | [工程陷阱](../docs/memory/04-engineering-pitfalls.md) §4 |
| 产出目录先归档、不覆盖 | `mkdir(exist_ok=False)` 第二次运行抛错、旧收据被误当"已完成" | [工程陷阱](../docs/memory/04-engineering-pitfalls.md) §5 |
| 服务 active ≠ 功能健康 | 发布清单漏装两个 UI 模块，服务仍 active 但面板不可用 | [会话修复报告](../docs/reports/2026-10-01-session-repair.md) |
| 部署未知先读回、不重装 | 安装脚本 CRLF 导致首次安装失败并自动回滚；240 s 超时后板卡重启只能记 `UNKNOWN` | [产线续接审核](../docs/reports/2026-10-03-production-board-continuation-audit.md)；[会话复盘与板卡恢复](../docs/reports/2026-10-12-session-review-and-board-recovery.md) |
| `boot_id` 跨会话变化 = 重启过 | 把历史读回当当前状态 | [工程陷阱](../docs/memory/04-engineering-pitfalls.md) §7 |
| token 只记路径、不读值 | 授权工件中出现 token 值/摘要的风险 | [工程陷阱](../docs/memory/04-engineering-pitfalls.md) §8 |

### 4.6 已验证效果

只写有证据的结论；没有证据的写"未取得/尚未验证"。

| 验证项 | 结论 | 证据 |
|---|---|---|
| 条目来源可追溯 | 每条硬约束可追到本项目事故或报告（见 4.5 表） | 仓库文档（[工程陷阱](../docs/memory/04-engineering-pitfalls.md)、各报告） |
| 技能格式 | 与 `final-prj-rf-development` 一起按 `skill-creator` 格式通过校验，并与个人技能安装目录逐文件 SHA-256 一致 | [工程整理记录](../docs/engineering/2026-10-30-ORGANIZATION-REPORT.md) |
| 项目内流程落地（部署/回退） | 试部署中实现"注入失败 → 自动回滚父版本 → 独立只读读回服务/RF 安全态/`boot_id`/存储正常" | [试部署回滚收尾](../docs/reports/2026-10-17-trial-rollback-board-closeout.md) |
| 项目内流程落地（Overlay/服务/读回） | 单一 Overlay 拥有者、服务 `active` 但面板不可用、部署未知先只读读回等约束来自真实事故并被后续流程引用 | [工程陷阱](../docs/memory/04-engineering-pitfalls.md) §4/§5/§7/§8/§16；[会话修复报告](../docs/reports/2026-10-01-session-repair.md)；[产线续接审核](../docs/reports/2026-10-03-production-board-continuation-audit.md)；[会话复盘与板卡恢复](../docs/reports/2026-10-12-session-review-and-board-recovery.md) |
| 版本覆盖 | 本项目只在 **PYNQ 2.7 / Zynq-7020** 镜像上实际使用；该范围内的方法与命令由上述事故/报告支撑 | [README](../README.md)「硬件与工具版本」 |
| 使用技能 vs 不使用的效果对照 | **未取得**（没有受控对照实验） | — |
| 跨项目验证 | **尚未验证**：本仓库没有第二个使用该技能的独立项目记录 | — |
| 跨板卡验证 | **尚未验证**：未在 MPSoC/Kria/Alveo 或非 AC820 板卡上跑过；PYNQ 版本/XRT/DMA API 差异未验证 | 见 4.4 末尾清单 |
| 跨框架版本 | **尚未验证**：PYNQ 3.x、不同镜像的 systemd/源码树布局会改变基线命令与 `PYTHONPATH` 处理 | — |
| 陌生队伍复用演练 | **尚未执行**：指南"换题目、换板卡仍可用"的加分主张在本仓库只到方法层面，不虚构加分案例 | — |

> 结论：本技能在**本项目 + PYNQ 2.7/Zynq-7020**范围内有事故与流程证据；**通用性/加分主张尚未经跨项目、跨板卡或陌生队伍验证**。

## 5. 技能三：`pluto-gnuradio-testbench`（PlutoSDR / GNU Radio 测试台）

**文件**：`SKILL.md` + `agents/openai.yaml` + 2 个 references（`local-setup.md`、`development-notes.md`）+ 可移植快照 `assets/testbench/`（9 个文件，含 `SNAPSHOT.json`）+ `scripts/prepare_testbench.py`，共 14 个。

### 5.1 适用场景

用 ADALM-Pluto（AD9363）配合 GNU Radio/gr-iio 做**基础接收、限时单音发射与有线回环**，用 PyVISA 控制 Keysight 示波器并保存证据；也适用于 USB IIO/Radioconda 环境排障，以及把该测试台复用到其他 SDR 测试场景。其他型号 SDR 须先核对驱动与 API，不能直接套用本技能参数。

### 5.2 使用方法

1. 从 [SKILL.md](pluto-gnuradio-testbench/SKILL.md) 开始，明确本次请求是**接收、发射还是仅诊断**；加载技能不代表获得额外发射授权。
2. 设备与解释器：先用 `probe` 发现设备并按串号读回身份；**COM11 是 FPGA 板卡，不是 SDR 或示波器**；GNU Radio 用 Radioconda 解释器，PyVISA 用系统 Python，两者不能混用（详见 [本机与来源](pluto-gnuradio-testbench/references/local-setup.md)、[开发经验](pluto-gnuradio-testbench/references/development-notes.md)）。
3. 典型命令（在包含 `sdr_testbench/` 的工作区根目录，默认不发射）：

   ```powershell
   .\sdr_testbench\run.ps1 probe
   .\sdr_testbench\run.ps1 offline
   .\sdr_testbench\run.ps1 rx
   ```

4. 把测试台复用到新工程：

   ```powershell
   python skills/pluto-gnuradio-testbench/scripts/prepare_testbench.py --destination <工作区>/sdr_testbench
   ```

   该命令只复制模板，不覆盖已有文件、不访问硬件；随后按自己环境替换解释器路径、设备身份与频段参数。
5. 结果判定：`result.json` 状态词只说明对应层级（如 `RX_STREAM_PASS` 只证明有非零 IQ；`TX_STREAM_COMPLETED_RF_UNVERIFIED` 不证明 RF 输出），完整对照表见 SKILL.md "结果判定"。
6. 收尾：停止/释放 GNU Radio 图后，用 IIO 写最大衰减和 TX LO `powerdown=1` 并读回；收尾失败报 `CLEANUP_FAILED`，不再发射。

### 5.3 失效条件与不适用

- **其他 SDR 型号**：RTL-SDR、USRP、HackRF、其他 AD936x 板卡的驱动与 API 不同；本技能的 URI/衰减/增益语义不可直接套用。
- **GNU Radio/gr-iio 版本变化**：本技能记录的是 GNU Radio 3.10.12.0 的 `fmcomms2_*_fc32` API（不存在 `iio.pluto_sink`）；升级后须重新核对块模板。
- **其他示波器**：`scope_test.py` 的 VISA 资源与 `*IDN?` 身份检查是本机专用；换示波器须先适配 SCPI，不能直接删除校验。
- **频段/带宽不匹配**：标准 Pluto 覆盖 325–3800 MHz，本机 70/100 MHz 示波器无法验收 434 MHz 载波；`+99E+36` 是无效测量，不能当通过或故障结论。
- **USB 独占**：Python libiio Context 与 GNU Radio 不能同时持有同一 USB 设备；强杀/驱动挂死不能保证 `finally` 关断，必要时由操作者断开 USB/电源。
- **无外置衰减器的直连**：属于受控评估路线，有额定输入、衰减读回、启动时序等前置；不能把数字幅度换算成校准 dBm。
- 技能不提供发射授权；不包含凭据；历史接线/设备身份不代表当前现场。

### 5.4 来源（从哪些失败/观察提炼）

| 技能中的约束 | 来源实践/失败 | 仓库出处 |
|---|---|---|
| 不存在 `iio.pluto_sink`，用 `fmcomms2_*_fc32` | 按常见示例写 API 直接失败 | [实测报告](../sdr_testbench/TEST-REPORT.md) §"本次遇到的问题及处理" 第 3 条；[开发经验](pluto-gnuradio-testbench/references/development-notes.md) §本版本 API |
| 分阶段持有 USB：发现→释放 Python Context→GNU Radio→释放→重新读回 | Python libiio 与 GNU Radio 同时持有 USB 导致 `Unable to create context`（可能带误导性 `Unable to find host`） | [开发经验](pluto-gnuradio-testbench/references/development-notes.md) §USB 生命周期；[GNU Radio 开发说明](../sdr_testbench/GNU-RADIO-DEVELOPMENT.md) |
| RX 源没有 `set_bandwidth`，须在原生 PHY 层写带宽再释放上下文 | 按 Sink 的习惯给 Source 调 `set_bandwidth` 会失败 | [实测报告](../sdr_testbench/TEST-REPORT.md) 第 5 条；[开发经验](pluto-gnuradio-testbench/references/development-notes.md) §本版本 API |
| 两套解释器不能混用 | 为 GNU Radio 改 PATH 后模糊调用 `python` 导致 PyVISA 导入失败 | [实测报告](../sdr_testbench/TEST-REPORT.md) 第 6 条 |
| 按串号发现、不硬编码 USB URI | USB 重新枚举后 URI 变化 | [实测报告](../sdr_testbench/TEST-REPORT.md) 第 7 条 |
| 证据分级、不把超带宽示波器平线当结论 | 434 MHz 载波超出 70/100 MHz 示波器带宽，频率读数无效 | [实测报告](../sdr_testbench/TEST-REPORT.md) §示波器观测；[开发经验](pluto-gnuradio-testbench/references/development-notes.md) §证据层次与退出 |
| 收尾读回 TX LO `powerdown=1` 与最大衰减 | 收尾失败/强杀不保证关断 | [SKILL.md](pluto-gnuradio-testbench/SKILL.md) §执行流程 第 5–6 条；[开发经验](pluto-gnuradio-testbench/references/development-notes.md) |
| 外部 SDR 统计"23/30 同步、整体未通过"不剔除失败帧 | 弱信号/未同步帧被剔除后称统计通过的风险 | [README](../README.md) §失败与限制、[交付材料索引](../docs/engineering/DELIVERY-INDEX.md) §5 |

### 5.5 已验证效果

据本仓库文档记载，并已于 2026-10-31 做只读复核：

- **设备与软件链**：Pluto 设备识别、GNU Radio 3.10.12.0 加载 gr-iio、离线单音（262144 点、峰值约 100 kHz、RMS 约 0.25）、GRC 编译与执行、真实 RX 样本流、2 秒 TX 数据流完成、TX 停止读回（`powerdown=1`、`hardwaregain=-89.75 dB`）、示波器 SCPI/截图控制均通过（[实测报告](../sdr_testbench/TEST-REPORT.md)）。**RF 输出频率/幅度未验证**（示波器带宽不足）。
- **模板可用性**：`assets/testbench/SNAPSHOT.json` 列出的 8 个文件 SHA-256 本次复核**全部相符**；离线单元测试 **5/5 通过**（不访问硬件）；`prepare_testbench.py` 复制模板后得到 9 个文件，第二次运行因目标已存在而拒绝（退出码 2），不会覆盖已有工程。
- **明确未验证**：有线回环未实机验证（SKILL.md 标注"截至 2026-10-19 未做实机回环验证"）；RF 实际输出未验证；跨项目/换 SDR/换示波器的复用**尚未验证**；陌生队伍试用**尚未执行**。

## 6. `skills/` 与指南 `skill/` 的目录关系

指南 3.3.5.2/3.3.5.4 规定技能包置于仓库 `skill/` 目录并含 `README.md`；本仓库的维护目录名为 `skills/`（复数），两者内容一一对应：

| 指南期望路径 | 本仓库路径 | 说明 |
|---|---|---|
| `skill/README.md` | `skills/README.md` | 即本文；提交时复制为 `skill/README.md` |
| `skill/<name>/SKILL.md` | `skills/<name>/SKILL.md` | 三套技能目录整体映射，文件与相对链接结构不变 |
| `skill/<name>/references/…` | `skills/<name>/references/…` | 同上 |
| `skill/<name>/assets/…` | `skills/<name>/assets/…` | 同上 |
| `skill/<name>/scripts/…` | `skills/<name>/scripts/…` | 同上 |
| `skill/<name>/agents/openai.yaml` | `skills/<name>/agents/openai.yaml` | 可选的智能体适配元数据，不影响技能内容 |

**入口映射说明**：`skills/` 即本仓库的技能包入口，本 README 就是指南要求的 `README.md`。仓库**不额外维护一份 `skill/` 副本**，避免同一套技能出现两份会漂移的维护副本；提交或打包到指南结构时，把 `skills/` 整体复制/重命名为 `skill/` 即可（顶层 README 保留同一份内容）。指南 3.3.5.4 允许"采用其他组织方式"，但要求给出目录对照说明——本表即为该对照说明。

> 提交前请确认：`skill/`（或 `skills/`）下所有目录名与文件名保持纯英文小写/数字/下划线或连字符；中文只出现在正文内容中。本目录当前命名符合该规则。

## 7. 技能从哪里提炼来（失败/观察 → 条目）

技能不是"写一份通用建议"，而是把本项目真实的失败与观察固化成可执行约束。提炼路径统一为：

**一次真实事故或观察 → 当时如何定位 → 根因 → 处置 → 防止再犯的检查 → 写入技能条目 → 用后续事故/复审验证条目是否有效 → 写明边界**。

代表性映射（详见各技能 §来源表与 [工程陷阱 16 条](../docs/memory/04-engineering-pitfalls.md)）：

| 失败/观察 | 提炼到哪套技能 | 条目形态 |
|---|---|---|
| 测试"通过的原因"错误（更早的守卫满足了断言） | 三套技能共有的证据纪律；`final-prj-rf-development` | 断言必须匹配它名字所指的特定消息；加守卫后重跑旧变异 |
| 退出码/管道/包装器失真（`tail` 取错退出码、未取远端真实退出码） | `pynq-linux-development`（退出 0 ≠ 功能/证据满足）；三套技能共有的证据纪律 | 显式分离 stdout/stderr/退出码；"退出码 0"与"证据满足"是两个判据 |
| 读取失败被当成"没有错误"（fail-open） | `final-prj-rf-development`（部署/研究门禁）、`pynq-linux-development`（部署读回） | 未知必须具名终态；不可读 ≠ 无错误 |
| 假硬件夹具与真实帧不同形，测试越全绿越掩盖问题 | `final-prj-rf-development` | 夹具先与真实帧逐键比对；谓词 fail-closed |
| 板卡无 RTC、`boot_id` 跨会话变化、token 只记路径 | `pynq-linux-development` | 主机 UTC 锚 + `boot_id`/uptime；凭据只记路径 |
| `__pycache__` 污染只读发布、产出目录覆盖旧收据 | `pynq-linux-development` | `python3 -B`；先归档再新建 |
| Overlay 单一拥有者、BRAM 32 位地址、MMIO 字节偏移 | `pynq-linux-development` | 分层定位与"同名不等于同次构建" |
| Pluto USB 独占、`fmcomms2_*` API、示波器带宽不足 | `pluto-gnuradio-testbench` | 分阶段持有 USB；按串号发现；证据分级 |
| 超预算重派、失败帧被剔除后称"通过" | `final-prj-rf-development`、`pluto-gnuradio-testbench` | 单次尝试不重试；全量统计后再说明选用帧 |

## 8. 如何验证技能有效（已做 / 未做）

**本项目已做**：

1. **格式校验**：`pynq-linux-development` 与 `final-prj-rf-development` 按 `skill-creator` 工具格式通过校验（[工程整理记录](../docs/engineering/2026-10-30-ORGANIZATION-REPORT.md)）。
2. **逐条出处核对**：技能中每条硬约束都能在仓库文档中找到事故/报告出处（本 README §3.4/§4.5/§5.4）。
3. **资产一致性**：`pluto-gnuradio-testbench` 的 `assets/testbench/SNAPSHOT.json` 逐文件 SHA-256 本次复核全部相符。
4. **离线可执行性**：`pluto-gnuradio-testbench` 的离线单元测试 5/5 通过；`prepare_testbench.py` 的复制与拒绝覆盖行为经本次实测。
5. **项目内引用**：三套技能由本项目的多智能体开发流程维护与引用（例如 2026-10-19 的执行计划明确要求先读 Pluto 技能，见 [并行执行计划](../docs/superpowers/plans/2026-10-19-dsh-parallel-execution.md)）；维护副本与安装副本逐文件摘要一致（见 [工程整理记录](../docs/engineering/2026-10-30-ORGANIZATION-REPORT.md)）。

**尚未做（如实列出）**：

- 陌生队伍/第三方按本 README 从零复用的演练；
- 跨项目、跨板卡的实测复现（尤其 `pynq-linux-development` 的加分主张）；
- "使用技能 vs 不使用技能"的效率/错误率对照实验；
- 技能条目在目标工程之外的失效条件穷举（当前失效条件来自本项目已知边界）。

## 9. 使用边界

1. **授权**：技能只提供方法与边界，**不授予**板卡连接、安装、Overlay 重配置、布防或发射权限。硬件动作以当前项目/操作者的明确授权与现场确认为准。
2. **隐私**：技能不保存凭据、账户与口令；设备地址、token 等从私有配置读取，不写入收据。例外是 `pluto-gnuradio-testbench/references/local-setup.md` 与 `assets/testbench/config/pluto.json`：它们是**单机历史环境识别结果**（含设备资源串、串口号、主机绝对路径等），作为开发记录保留，**不构成通用建议**；复用前必须按自己环境重写，公开分享前按 [分享、隐私与可恢复清理](../docs/engineering/SHARING-AND-CLEANUP.md) 的规则脱敏。
3. **证据层级**：离线测试通过、ARM 运行、板端实采、真人验收是不同层级；技能中的历史指标只按"文档记载"引用，不把 `NOT_RUN`/`UNKNOWN` 改写成通过。
4. **许可证**：本仓库为混合许可（见根 [README](../README.md) §许可证、[LICENSES/](../LICENSES/)）；技能文档与方法说明属自有内容，随仓库以 MIT 分发。`pluto-gnuradio-testbench/assets/testbench/` 中的 `radio_test.py` 依赖 GNU Radio，按 GPL-3.0 分发，同目录的测试脚本依赖该模块；把测试台脚本并入自己工程前，先确认整体许可证兼容性。
5. **框架差异**：技能面向 PYNQ（Zynq/MPSoC 板载 Linux）与 GNU Radio 3.10 生态；换框架、换版本先做最小验证再套用。

## 10. 文件清单与维护规则

| 技能 | 文件数 | 主要内容 |
|---|---|---|
| `final-prj-rf-development/` | 5 | SKILL.md、agents/openai.yaml、3 个 references（工程地图、接收与 SDR、交付与证据） |
| `pynq-linux-development/` | 5 | SKILL.md、agents/openai.yaml、3 个 references（Overlay/AXI、Linux 服务与部署、界面与数据） |
| `pluto-gnuradio-testbench/` | 14 | SKILL.md、agents/openai.yaml、2 个 references、9 个可移植测试台文件、1 个模板准备脚本 |
| 本 README | 1 | 技能包入口、目录映射与验证说明 |
| **合计** | **25** | 三套技能 24 个文件 + 入口 README |

维护规则：

1. **快照更新只同步工具、默认配置与离线测试**：更新 `pluto-gnuradio-testbench/assets/testbench/` 时同步维护源工程 `sdr_testbench/`，更新 `SNAPSHOT.json` 的 SHA-256，并重跑离线测试；不打包 `results/`、原始 IQ 或历史收据。
2. **技能条目变更要有出处**：新增/修改约束时写清它来自哪次事故/观察、如何验证、边界是什么；不要从"感觉"添加规则。
3. **不复制会漂移的副本**：仓库只维护 `skills/` 一份；提交时按 §6 映射到 `skill/`。
4. **不含隐私与凭据**：提交前检查技能目录中是否含个人路径、设备串号、凭据或私网地址；环境特定值用占位符（如 `<USER>`、`<DEVICE_SERIAL>`、`<SET_LOCALLY>`）。

## 11. 尚未验证清单（评审请按此理解）

- 三套技能的**跨项目/跨板卡复用**均**尚未验证**（`pynq-linux-development` 的加分主张只停留在方法层面）。
- `pluto-gnuradio-testbench` 的**有线回环**与**RF 输出幅度/频率**未验证。
- 没有陌生队伍从零复用的记录；没有技能使用前后的对照数据。
- 技能中引用的历史指标来自本仓库文档记载，原始收据与原始 IQ 不在本仓库，不能复算。
- 本 README 的复核（快照摘要、离线测试、模板复制）只覆盖**软件模板可用性**，不覆盖任何硬件功能。
