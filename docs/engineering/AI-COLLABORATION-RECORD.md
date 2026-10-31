# 大模型协作记录（AI Collaboration Record）

> 本文按赛事指南 **3.3.3.1 工具限制**（提交体现大模型协作过程的交互记录，含提示词、模型回答与自我纠错轨迹）与 **3.3.5.3 设计报告**（大模型协作记录：提示词、模型回答、自我纠错轨迹、智能体工作流设计；技能包的提炼过程）整理。
>
> **写作边界**：为保证隐私与可读性，本文对协作过程做**改写摘要**，不复制完整会话原文；删去个人姓名、账号、邮箱、主机绝对路径、设备串号、凭据与私网地址。所有可核查出处均为**本仓库公开文档**（相对路径 + 章节）；原始会话、原始收据与原始 IQ 不在本仓库，不能在这里复算（见 [交付材料索引](DELIVERY-INDEX.md) §9）。本文只写方法与本项目事实，不宣称项目整体验收通过。
>
> 与本文配套的案例摘编见 [AI 协作案例](AI-COLLABORATION-CASE.md)（7 个事件 + 硬规则）；技能条目与入口见 [技能包 README](../../skills/README.md)。

## 0. 背景与案例选择

项目是 Zynq-7020 + AD9361 的便携收发与接收分析平台，同时保留独立的辅助 AWG 链与外部 PlutoSDR 测试台。参与协作的是人类用户、多个 AI 编码代理（不同会话/不同模型），以及只审不写的独立复审角色。AI 会写代码、跑离线测试、写报告，也会犯错；人类负责授权、现场确认与最终验收。

本文选 **3 个**有公开出处、有明确错误或反馈、有纠正动作、结果带边界的案例。选择标准不是"成功故事"，而是**错误如何被发现、如何被纠正、纠正后还留下什么限制**：

| 编号 | 案例 | 错误类型 | 发现方式 |
|---|---|---|---|
| K1 | 测试通过了，但通过的原因是错的 | 测试有效性被更早的守卫遮蔽 | 独立规范复审用"删掉检查看是否还绿"抓出 |
| K2 | 退出码与"成功"读数失真 | 管道/包装器换掉了被观测对象 | 独立复核与真实接口核对 |
| K3 | 读取失败被当成"没有错误" | fail-open：未知被折叠成通过 | 独立复审两轮反例（F1 → N1） |

另在 **§7** 补充 3 个"优化与性能"主题的典型提示词/模型回答/纠错片段（S1–S3），用于说明性能类结论的**口径限定**与**引用 vs 重测**边界。

## 1. 协作模型与智能体工作流

### 1.1 角色、写范围与共享单例

多代理共用同一个工作目录与文件系统，**没有锁，只有纪律**。角色与边界（出处：[工程地图](../memory/01-project-map.md) §3；[开发纪律](../memory/03-development-discipline.md) §9）：

| 角色 | 职责 | 边界 |
|---|---|---|
| Lead（主代理） | 分派、整合、最终验收与最终回复 | 不并发写别人正在写的文件；最终自己跑一遍验收命令并看 `git status` 与 diff |
| 一次性子代理 | 自包含的只读/离线任务（盘点、复算、写报告） | 不并发写同一文件；结论必须回带路径与摘要 |
| 持久队友 | 有独立写范围的长任务 | 任务卡登记 `write_scopes`；写范围互斥 |
| 独立复审者（非实现者） | 只审不写，给 `APPROVED` / `CHANGES_REQUIRED` | 实现者不得自签；不得改写历史收据 |

配套纪律：**共享单例资源串行**（板卡、`current` 发布、pytest 全局临时目录、git index）；看到别人的未跟踪目录不动、不删、不采纳；写完由 Lead 看最终 diff 并复跑关键测试。出处同上 §3，另见 [开发审查规则](../requirements/development-review-policy.md) §1、§4。

**为什么写范围比"防冲突"更重要**：写范围一旦重叠，最坏的情况不是文件冲突，而是两份"看起来都对"的证据互相覆盖，之后谁都说不清哪一份对应哪次执行（[AI 协作案例](AI-COLLABORATION-CASE.md) §1）。

### 1.2 门禁模型与审查分级

"说过的话"和"证据"分开：六类门 **离线（OFF）→ ARM → 数字回环（DL）→ 有线物理（PHY）→ 真人（HUM）→ 发布（REL）**，未执行的层保持 `NOT_RUN`，没有原始收据的结论写 `UNVERIFIED`；不把"测试数量"当成"物理通过"（[门禁模型](../memory/02-gate-model.md) §1–§2、[验收清单](../requirements/acceptance-checklists-p1-p6.md)）。

审查按影响分级：R0 作者自检；R1 作者自检 + 相关测试；R2（硬件控制、判定合同、安装切换等）由**未参与实现的人**做一次增量审查，且只有关键大阶段（新候选、安装/切换/回滚、跨设备联调里程碑、发布）才交独立审查（[开发审查规则](../requirements/development-review-policy.md) §1）。日常差分不重复全审，但**实现者不得自签**。

### 1.3 一轮工作包的固定流程

1. 读最新原始收据、实际代码与当前状态（不用旧报告里的"当前"）；
2. 选一个**有辨识力的最小问题**，先写清什么算通过（判据先于测量）；
3. 声明本轮预期写入的文件/目录（任务卡 `write_scopes`），共享资源串行；
4. 先红后绿：先让测试因正确原因失败，再修到通过；
5. 对关键禁止状态写直接反例；必要时在隔离副本做少量变异，验证测试真的抓住了问题；
6. 跑受影响回归，区分"环境问题""预期负例""产品失败"；
7. 关键阶段交一次独立增量审查（只审不写，给结论与范围）；
8. 有硬件动作时：新鲜预检 → 一次执行 → 按预先声明的停止/回退条件收尾 → 独立只读读回；
9. 留下收据、失败日志与未验证项；更新索引与工程记忆；
10. Lead 最后看 diff、复跑关键检查、给最终回复；**失败与未知原样保留**。

### 1.4 工具与产物

- **任务卡**：记录 owner、写范围、状态、阻塞与下一步；持久队友按卡执行（参考 [工程地图](../memory/01-project-map.md) §3、[工程陷阱](../memory/04-engineering-pitfalls.md) §12 的"临时树不入库"等约束）。
- **收据**：每轮的关键结论绑定源码/产物摘要与执行日志；原始 IQ、日志、收据保留原件，分享副本才做脱敏（[开发纪律](../memory/03-development-discipline.md) §7）。
- **审查记录**：`APPROVED` / `CHANGES_REQUIRED` 及实际范围；历史批准不得被改写或伪造（[开发审查规则](../requirements/development-review-policy.md) §1）。
- **索引与记忆**：`docs/memory/` 是"先查这里再动手"的权威来源指针；记忆本身不是证据，冲突时以原始收据为准（[证据索引](../memory/05-evidence-index.md) 头部说明）。

### 1.5 提示词工作流（可复用骨架）

以下骨架由本项目的任务卡、审查请求与纠错复验实践改写，**不含任何个人信息与凭据**；可直接用于新的 AI 协作任务。

**（A）任务卡/任务提示词骨架**——本项目实际任务说明常用"先读与确认 → 本轮任务 → 必须修正的认识 → 继承的约束 → 交付与结束条件"五段式，并把"实现状态与验证状态分开记录"单列为记录纪律。可直接套用：

```text
【背景与入口】先读 <项目入口/最新收据/维护文档>，不要只生成泛化方案。
【本轮任务】只做 <一个有辨识力的最小问题>；范围外需求写成待评估项。
【必须修正的认识】列出被证据推翻的旧结论与正确口径。
【约束与授权】可变范围 / 不可变范围 / 禁止清单 / 硬件动作授权边界。
【交付与结束条件】改动与原因；逐项 PASS / FAIL / NOT RUN；证据位置；
                 已知风险与未验证事项；下一条可执行动作。
【记录纪律】实现状态与验证状态分开；失败保留；不重试凑数；不伪造结果。
```

**（B）审查请求骨架**——本项目独立审查请求的固定要点：给差分范围、给判据、给反例要求、给"不要审什么"、给结论格式：

```text
【范围】只审 <差分/阶段>，不重审历史与未改变部分。
【必须检查】判据是否被放宽；通过的原因是否正确；失败/未知是否有具名终态；
           预算与账本是否与实际执行一致；证据是否绑定真实字节。
【反例要求】至少提供 <直接反例/变异>，证明检查真的会失败。
【结论格式】APPROVED / CHANGES_REQUIRED + 阻塞项 + 非阻塞项 + 适用范围。
【独立性】实现者不得自签；复审者不得改写历史收据。
```

**（C）纠错与复验骨架**——把"模型回答错误"转成可验证的修复：

```text
【反馈】哪条断言/结论是错误的，为什么（指出证据与被遮蔽的路径）。
【修复】最小改动 + 它应该让哪条测试因正确原因失败。
【复验】先做变异/反例证明新检查有效，再恢复并跑相关回归；保留红绿日志。
【边界】这次修复覆盖什么、不覆盖什么；历史证据是否保留、是否需补证。
```

三种骨架的共同点是：**要求模型给出可被检验的对象**（判据、反例、收据、绑定摘要），而不是只给结论。

## 2. 案例 K1：测试通过了，但通过的原因是错的

### 2.1 提示词/任务（要点）

按 G0 离线闭合计划执行 round 2：修复独立审阅发现的 F1–F5，加固零档 trace 断言，完成"八帧硬上限 + 版本/类型/每帧证据门禁 + 宿主 driver"；**全部离线、零 RF、不安装、不连板**；承重断言必须有变异证据（任务清单见 [接续计划](../superpowers/plans/2026-10-07-s2-settle-validation-next.md) §G0 执行记录 round 2、§Task 0.3–0.6）。

### 2.2 模型回答/产出

AI 执行者按计划完成 round 2，报告：零档 trace 断言加固（先请求 `device.info`，字段存在性与数值分开）、终止旗可观察性（`sigaction` 无 `SA_RESTART` + `conn_read` 停止重试 EINTR）、八帧硬上限（批准集合相等、会话顺序固定、逐帧 requested/actual）、版本/类型/每帧证据门禁、宿主 driver 门禁；并列出回归数字（board 软件 1874 通过 / 4 跳过、tools 155、host 除 ML 902、ML 54；C 套件 daemon 417/0、core 98/0、sleep 22/0、startup 92/0、`make` 退出 0、vendor integrity 10 files passed）与变异结果（同文件 §"round 2 最终回归"）。其中一条测试：`test_wait_campaign_rejects_any_design_that_is_not_the_approved_one`。

### 2.3 错误或反馈

- 质量复审：`APPROVED`（改动过的二进制 **0 条断言被删除**，门槛未放宽）。
- 规范复审：**首轮 NOT APPROVED**，唯一阻塞项是**执行者自己的流程失误**：该测试少传 `baseline`，而 `wait_campaign` 的 baseline 守卫排在档位检查**之前**，于是"坏设计被拒绝"实际由更早的守卫满足，**档位检查零覆盖**——把档位检查整段删掉，**49 条测试仍然全绿**。执行者先前那次变异验证在当时有效，但 baseline 守卫是在那之后才加的，**旧变异没有重跑**，一条真证据悄悄失效（[接续计划](../superpowers/plans/2026-10-07-s2-settle-validation-next.md) §"round 2 独立审阅"）。

### 2.4 纠正动作

1. 测试改为传入 `baseline`，并把 `pytest.raises` 的 `match=` 钉到该测试名字所指的**特定消息**（`'approved'`）；
2. 由第三个独立代理复验：变异 → 该测试 `DID NOT RAISE`（此前全绿）；恢复 → **155 passed**；并确认**没有任何路径**能让未批准档位到达 `factory()`；
3. 复验还发现同一机制的相邻一例，判定为非真漏洞（版本检查正是该性质的编码方式），但把 `match` 钉到它依赖的消息，使依赖显式而非偶然；
4. 把教训写进交接文档与工程记忆：**加新守卫之后必须重跑旧变异；每个 `pytest.raises` 都要匹配它名字所指的特定消息**（[G0 交接](../reports/2026-10-07-g0-handoff.md) §操作要点"坑"；[工程陷阱](../memory/04-engineering-pitfalls.md) §10）。

### 2.5 结果与证据

- 修复后相关测试与回归通过，变异能正确抓住问题；出处与红绿/变异记录见 [接续计划](../superpowers/plans/2026-10-07-s2-settle-validation-next.md) §"round 2 独立审阅"、"新增教训"；冻结与收据信息见同文 round 2 段末"G0 仍未关闭"。
- 该轮**仍保留未完成项**：生产 sleep/终止/sigaction 路径位于板上专有区，未经任何 ARM 编译，主机测不到；G0 因此**未关闭**，后续由 ARM 构建阶段的真实信号/套接字测试闭合（[G0 交接](../reports/2026-10-07-g0-handoff.md) §未完成、§NOT_RUN）。
- 这条教训后来被纳入技能纪律：**通过的原因比通过本身重要**（[AI 协作案例](AI-COLLABORATION-CASE.md) §3、§11；[技能包 README](../../skills/README.md) §7）。

## 3. 案例 K2：退出码与"成功"读数失真

### 3.1 提示词/任务（要点）

独立复核另一 AI 代理的 G1 资格执行情况，并据此判断能否进入 G2。复核请求明确要求：只读核对证据与源码、复算关键数字、指出"已批准准入条件是否已有完整执行证据"（[G1 复核](../reports/2026-10-09-claude-g1-review.md) 开篇"本轮请求"）。

### 3.2 模型回答/产出

执行者此前报告 G1 接近完成，并给出"八行仅剩一行""全部步骤完成"的进度表述，附 C/Python 回归数字、Linux harness 的 74 PASS/0 FAIL、资源与同 IQ 环境判等结果。复核方独立核验后确认大部分结论属实（51 native + 17 handoff 零漂移、ARM ELF 与候选绑定一致、P95 重算 290.805352 ms、RSS 30816 KiB 等），但同时列出准入缺口（[G1 复核](../reports/2026-10-09-claude-g1-review.md) §"已独立核实的进度"）。

### 3.3 错误或反馈

1. **退出计时证据无效**：harness 的退出计时测试声明 `fd` 后未赋值就传入 `conn_read`；child 不检查读取返回值便退出 0，parent 没有 READY/阻塞同步。child 可能因错误描述符提前结束，父进程随后收割成功——**不能证明 SIGTERM 唤醒或 5 秒预算**。原 5 ms 证据标无效，历史收据不改写。
2. **退出码被管道换掉**：`sleep` 日志 22/0 是真实输出，但 `make` 经过 `tail` 管道后的返回码取到的是 `tail` 的状态，**应直接保存 make 实际退出码**（同文 §准入缺口与必要补证 第 5 条）。
3. 同类事故（早前记录）：远程命令包装器**从不调用 `recv_exit_status()`**，退出码判据恒为宿主 helper 的 0，而固件用退出码编码真实失败——该判据是**死代码**，危险形态是静默（"命令成功但没输出"）。修复用 `__BOARD_RC` 标记 + 锚定行匹配 + 无标记也报（[AD9361 发射 bring-up 计划](../superpowers/plans/2026-09-17-ad9361-tx-bringup.md) 事件表 E17）。
4. 另一类同族问题：把命令的 **stderr 当 stdout** 解析，4 个实际存在的二进制被报成 4 个失败（[工程陷阱](../memory/04-engineering-pitfalls.md) §14）。

反馈的定性很直接：**"作者报告的'八行仅剩一行'和'全部步骤完成'过于宽泛"**；两位独立审阅者结论均为 G1 仍 `PARTIAL`，不进入 G2（[G1 复核](../reports/2026-10-09-claude-g1-review.md) §"本轮独立审阅"）。

### 3.4 纠正动作

1. 保留 G1 `PARTIAL`，先补证：修退出 harness 的未初始化 fd 与同步/断言；增加**真实 socket 背压**下的 FAULT/owned-core 组合收尾；补完整行之后、派发之前的停止 guard；补两信号时点矩阵与慢读/EINTR 固定期限；**保存 `make` 真实退出码**；
2. 补"完整接收结果"的主机/ARM 判等（原判等只覆盖环境分析记录）；
3. 包装器改为返回真实退出码；解析外部命令时显式分离 stdout/stderr/退出码；把"退出码 0"与"证据满足"当成两个独立判据；
4. 所有原日志与原退出结论**保留并标无效**，不改写历史收据。

### 3.5 结果与证据

- 补证后 G1 通过：旧退出 harness 的未初始化 fd 已修，新增真实背压 FAULT/owned-core、停止派发 guard、两信号时点矩阵、慢读/EINTR 固定期限、`make` 真实退出口径、四类同 IQ 五阶段判等与六项有效变异；ARM Linux harness **123 PASS / 0 FAIL**（[G1 收尾报告](../reports/2026-10-10-claude-progress-and-closeout.md) 开篇结论、§Claude 实际完成情况、§验证证据与边界）。
- 同时保留限制：质量最终 verdict 当时仍为未完成状态（null）、G2 安装与实采仍 `NOT_RUN`；G1 PASS **不自动**开始 RF，需重新预检（同文 §验证证据与边界）。
- 可复用的判据：**先确认你在读的对象，再相信读数**；一条命令的"成功"只是一个字段，不是结论（[AI 协作案例](AI-COLLABORATION-CASE.md) §4）。

## 4. 案例 K3：读取失败被当成"没有错误"（两轮纠偏与边界）

### 4.1 提示词/任务（要点）

给 AI 编码代理下一条独立开发任务：实现 HEADROOM 真实研究 runner 与模拟测试，**复用已冻结的 worker/controller/client 唯一链**，不改 46 项软件或原生合同，**不连接板卡、不实采**；交付后先做存储健康只读预检，再做新包单次安全安装与独立只读读回（[Harness 进度核验](../reports/2026-10-12-deepseek-harness-progress-check.md) §"结论与后续"）。

### 4.2 模型回答/产出

runner 交付：门禁顺序、一次性授权与消费账本、接线新鲜度、唯一执行入口、runner 层终态标签、`run-gate.json`、独立只读安全读回，以及进程内假硬件链；报告 `133 passed`、34 项变异中 32 项被抓（2 项逃逸如实记为冻结套件覆盖缺口）、收据 `passed=True`，并逐条说明与设计文档的偏离理由（[runner 实现报告](../reports/2026-10-12-headroom-runner-implementation.md) §1、§7、§10）。

### 4.3 错误或反馈（第一轮）

独立审查结论 **REJECTED**，两项阻塞：

- **F1（阻塞）**：事后存储读取失败/不完整时仍产出空故障列表并判 `RUN_COMPLETED`/exit 0，且缺失的 `boot_id` **静默解除**了"是否重启"的判据——即"没读到"被折叠成"没问题"；
- **F2（阻塞）**：另一条崩溃路径会破坏既定结局契约、事后没有 `run-gate.json`。

（[runner 实现报告](../reports/2026-10-12-headroom-runner-implementation.md) §12"独立审查响应"）

### 4.4 纠正动作（两轮）

**第一轮（修 F1/F2）**：`postflight` 的不可读/不完整分支不再产出 `faults=[]`；新增终态 `RUN_UNKNOWN_STORAGE_UNREADABLE`（exit 3）；规定"**不可读的 boot id 永不等于 same**"；补 4 条测试（不可读/不完整不得完成、干净读取仍完成、真实存储故障是中止而非未知）；重跑收据。

**第二轮（复审发现新形态 N1）**：第二次独立复审对修补后的 runner 仍给出 `CHANGES_REQUIRED`，因为发现**同一设计规则的另一半仍有缺口**：

- **N1-a**：第 2 次快照 `complete=True` 但**没有 `kernel_storage`** → 仍 `RUN_COMPLETED`/exit 0（"根本没读到内核文本"被当成"存储无错误"）；
- **N1-b**：`complete=True` 且 `kernel_storage` 为 bytes → `TypeError` 穿出入口，**完全没有 `run-gate.json`**；
- **对照**：同样两个形态放在**事前**读取上都会被具名拒绝——同一条规则的两半互相矛盾，这就是缺陷本身。

复审者做最小修补：在 `complete is True` 分支增加与事前读取同一条类型判据（文本必须是 `str`，否则 `UNREADABLE` → `RUN_UNKNOWN_STORAGE_UNREADABLE`）；**先红后绿**：新测试指向修订前的 runner 副本 → `2 failed, 4 passed, 133 deselected`（exit 1），修补后 → **139 passed**；复审者自写探针从 `39/41` 变 `41/41`，四个冻结套件 `155 passed`、未改任何冻结文件；重跑收据 `passed=true`（[独立复审](../reports/2026-10-13-headroom-runner-independent-review.md) §5）。

### 4.5 结果与证据

- 修补后的 runner 获得 **APPROVED（仅限离线 host-side runner 门）**；范围与依赖项、遗留风险明确写入报告（[独立复审](../reports/2026-10-13-headroom-runner-independent-review.md) §0、§7、§8）。
- 保留的限制：真实板卡存储读取、真实存储故障中止、真实断线/重启、真实安装与真实 46 dB 研究全部 `NOT_RUN`；`run` 子命令尚未接线真实存储探针，因此 N1 类缺陷"今天不可达"，但**在下一个门接线时即变为可达**，必须在此之前修掉。
- **独立性声明（重要）**：第二轮修补由复审者本人编写，是本轮独立性最弱的一环；报告明确要求**接硬件前必须由第三方对修补后的修订再做一次新鲜独立复验**（[独立复审](../reports/2026-10-13-headroom-runner-independent-review.md) §0 第 3 条）。

### 4.6 这个案例说明了什么

1. **修复一个 fail-open 形态，不等于修好这类缺陷**：F1 修了"不可读/不完整"，N1 又暴露"完整但没有文本"；边界是**逐形态**确定的，不是一句"已修好"。
2. **"未知"必须具名**：不可读 ≠ 空；不可判定 ≠ 通过（[门禁模型](../memory/02-gate-model.md) §1 第 1 条纪律、G1 行"读取失败 ≠ 无错误"）。
3. **复审独立性要写进结论**：复审者自修时，必须声明独立性弱环并安排第三方复验，不能把"我修了、我批了"当成闭环。

## 5. 从协作记录到技能包（提炼过程）

指南 3.3.5.3 要求说明技能包"从哪些失败或观察中总结、如何验证有效、边界如何确定"。本项目三套技能的提炼方法与结果如下（技能入口见 [技能包 README](../../skills/README.md) §7）：

### 5.1 提炼方法（五步）

1. **收集**：从工程记忆（[工程陷阱 16 条](../memory/04-engineering-pitfalls.md)）、报告、复审记录中收集真实事故与观察；
2. **可执行化**：只保留能落成"触发条件 → 判据 → 动作"的条目，删掉无法检验的口号；
3. **找出处**：每条硬约束必须能指到仓库内的事故/报告出处；找不到出处的降级为"经验建议"或删除；
4. **写边界**：同时写"什么时候失效/不适用/尚未验证"，避免把项目内经验包装成通用结论；
5. **组织与校验**：用入口 README 把技能组织成"一分钟选择 → 按问题选读 → 执行 → 判定"，做格式校验、资产摘要校验与离线测试。

### 5.2 失败/观察 → 技能条目（映射示例）

| 失败/观察（出处） | 提炼到 | 条目形态 | 边界/未验证 |
|---|---|---|---|
| 测试被更早守卫满足、旧变异未重跑（K1；[工程陷阱](../memory/04-engineering-pitfalls.md) §10） | 三套技能共有的证据纪律；`final-prj-rf-development` | 断言匹配它名字所指的消息；加守卫后重跑旧变异 | 变异只做"受影响"的少量，不做全仓战役 |
| 退出码/管道/包装器失真、stderr 当 stdout（K2；[工程陷阱](../memory/04-engineering-pitfalls.md) §14–§15） | `pynq-linux-development`（进程退出 0 ≠ 功能/证据满足）；三套技能共有的证据纪律 | 分离 stdout/stderr/退出码；"退出码 0"≠"证据满足" | 平台/包装器不同，具体命令以目标工程为准 |
| 读取失败判 `RUN_COMPLETED`（K3；[独立复审](../reports/2026-10-13-headroom-runner-independent-review.md) §5） | `final-prj-rf-development`（部署/研究门禁）、`pynq-linux-development`（部署读回） | 未知必须具名终态；不可读 ≠ 无错误 | 覆盖到"已知形态"；新形态仍需新测试 |
| 假硬件夹具与真实帧不同形（[工程陷阱](../memory/04-engineering-pitfalls.md) §9） | `final-prj-rf-development` | 夹具先与一份真实帧逐键比对；谓词 fail-closed | 假硬件只能验逻辑，不能验"是否对准真实世界" |
| 板卡无 RTC、`boot_id` 跨会话变化、token 只记路径（[工程陷阱](../memory/04-engineering-pitfalls.md) §6–§8） | `pynq-linux-development` | 主机 UTC 锚 + `boot_id`/uptime；凭据只记路径 | 板卡/镜像不同，锚方式需重核 |
| `__pycache__` 污染只读发布、产出目录覆盖旧收据（[工程陷阱](../memory/04-engineering-pitfalls.md) §4–§5） | `pynq-linux-development` | `python3 -B`；先归档再新建 | 依目标工程的发布/归档约定 |
| Overlay 单一拥有者、BRAM 32 位地址、MMIO 字节偏移（[工程陷阱](../memory/04-engineering-pitfalls.md) §16） | `pynq-linux-development` | 分层定位；同名不等于同次构建 | 引脚/时钟/DMA 依板卡与 Overlay 而定 |
| Pluto USB 独占、`fmcomms2_*` API、示波器带宽不足（[SDR 实测报告](../../sdr_testbench/TEST-REPORT.md)、[GNU Radio 开发说明](../../sdr_testbench/GNU-RADIO-DEVELOPMENT.md)） | `pluto-gnuradio-testbench` | 分阶段持有 USB；按串号发现；证据分级 | 其他 SDR 型号/GNU Radio 版本/示波器须另核 |
| 超预算重派、把有限帧/成功帧说成整体通过（[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[交付材料索引](DELIVERY-INDEX.md) §5） | `final-prj-rf-development`、`pluto-gnuradio-testbench` | 单次尝试不重试；全量统计后再说明选用帧 | 依各项目授权与实际预算 |

### 5.3 如何验证有效

- **项目内验证**：每条约束都能追溯到事故/报告（见 5.2 出处）；相关规则在后续流程中被反复引用（工程记忆、审查规则、报告）。
- **可执行性验证**：`pluto-gnuradio-testbench` 的模板快照逐文件 SHA-256 校验、离线单元测试、模板复制与拒绝覆盖均经复核；两套技能通过 `skill-creator` 格式校验（[工程整理记录](2026-10-30-ORGANIZATION-REPORT.md)）。
- **尚未验证**：跨项目/跨板卡复用、陌生队伍从零复用、"用技能 vs 不用技能"的对照。这些在 [技能包 README](../../skills/README.md) §8、§11 中如实列出，不虚构加分案例。

### 5.4 边界如何确定

1. **从事故条件定边界**：例如"部署未知先读回"来自安装超时/板卡重启的真实事故，边界就是"适用任何远端持久作业"；"不把历史收据当当前状态"来自 `boot_id` 变化事故，边界就是"每次读回都要带 `boot_id` + uptime"。
2. **从接口差异定边界**：`pynq-linux-development` 对"非 PYNQ 硬件栈""任意 Linux 发行版""其他板卡引脚"明确写出不适用；`pluto-gnuradio-testbench` 对其他 SDR/GNU Radio 版本/示波器明确写出需另核。
3. **从验证程度定边界**：没有跨项目验证就写"尚未验证"；没有真实硬件证据就写 `NOT_RUN`；不用推断补边界。

## 6. 可复用的协作经验

1. **提示词要给可检验对象**：判据、反例、收据、绑定摘要，而不是"做好这件事"。
2. **先红后绿**：红必须因为目标行为缺失，不能因为语法错/缺文件/环境问题。
3. **通过的原因比通过本身重要**：断言要匹配它名字所指的消息；删掉检查看是否还绿。
4. **未知要显式**：不可读 ≠ 无错误；`NOT_RUN` ≠ 失败 ≠ 通过。
5. **单次尝试不重试**：失败保留原帧，不靠重跑凑数，不事后放宽标准追认成功。
6. **写范围互斥**：并发代理先登记写范围；共享单例（板卡、发布、临时目录、git index）串行。
7. **实现者不自签**：关键结论交独立复审；复审者自修时要声明独立性弱环并安排第三方复验。
8. **结论带时间戳与证据等级**：不覆盖历史，不引用自己当证据（[AI 协作案例](AI-COLLABORATION-CASE.md) §7、§11）。

## 7. 典型提示词/模型回答/纠错片段（补充）

本节补充 3 个**优化与性能**主题的真实片段，与 K1–K3 同样只做改写摘要，不复制完整会话原文，不写个人信息。出处为公开文档或开发机 `<LOCAL_EVIDENCE>` 报告（后者不在本仓库，已注明）；引用时保留"引用/重测"与"逐字节/容差"等口径限定语。

### S1：受控 A/B 的提示词与"0 mismatch"口径澄清

**提示词/任务要点**（改写自 `<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-perf-arm-2026-10-21/result/PERF-ARM-RESULT.md` §1）：

> 同一候选 `b3cca2ac…`；同候选 release 副本、同 4 帧、同顺序；baseline 与 optimized 只替换 `ModRec4Adapter.evaluate_iq` 为单次共享计算；batch=1、warmup 5、repeats 20；固定顺序（不交替，热漂移风险如实记录）；两实现逐帧六类/环境/解调/modrec4/身份在**预声明容差**内一致才算通过；不安装、不改 `current`、零 RF；不一致/异常/超时即停、不重试。

**模型回答/产出要点**：ARM 五阶段合计 median `378.89 → 292.08 ms`（−22.9%）、P95 `444.52 → 311.74 ms`（−29.9%），modrec4 阶段 median `154.58 → 64.81 ms`（−58.1%），RSS 约 32 MiB 无实质变化；ARM 4/4 帧、host 70/70 case 输出 0 mismatch；结论表述为"建议纳入的证据，决定权在 Lead/A"。

**错误/反馈**：后续产品决策文件纠正了两处**口径过度**：

1. `0 mismatch` 是**语义/字段容差比较**，**不是逐字节相等**；原始严格比较存在最后一位浮点/passthrough repr 差异（最大浮点差 `2.84e-14`、six margin `1.48e-8`），均在声明容差内；
2. 这组 ARM 数字是 **`b3cca2ac` 的历史受控配对数据，不是新候选的实测**；合入产品后"本任务未重测 ARM"，不能写成新候选性能。

**纠正动作**：在 `PERFORMANCE-DECISION.md` 增加"口径限制"与"Boundary"节：引用历史 ARM 数据只作参考、不称新候选实测；合入后只做受影响验证（43/43、70/70、hook 10/10、221 tests 等），不重测性能；`b3cca2ac` 历史证据保留不改写。

**结果与证据**：优化按该证据合入产品（最终候选 `4776ba0b…`），但性能声明保持"历史受控配对"口径；[优化与复现说明](OPTIMIZATION-AND-REPRODUCTION.md) §1.4 按同一口径引用。出处：`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-perf-arm-2026-10-21/result/PERF-ARM-RESULT.md`、`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-product-2026-10-21/PERFORMANCE-DECISION.md`。

### S2：RRC 优化两轮纠偏与"内存换时延"的如实记录

**提示词/任务要点**（改写自 `<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-rrc-perf-2026-10-24/PERF-REPORT.md` §0、§3、§5）：

> 先冻结旧基线（候选 `90ac6b12…`）与热点，准备测量/停止检查工具；**新候选未冻结前，旧/新差分与 ARM 21/21 一律 NOT_RUN**；不得用旧基线数字宣称新候选性能；停止/取消超预算必须如实记录，不改预算、不隐藏。

**模型回答/产出要点**：旧基线 cold 首次 legal `17.136 s`、hot median `16.751 s`、max legal `17.373 s`、21 输入总 `269.264 s`、max RSS `22,668 KiB`（≤3 s 目标**未达**）；热点为 matched filter/norm/FFT；ARM stop 实测 `5.7516 s` 抛 `RuntimeError: RF capture worker did not stop`，**如实记录为阻塞**。

**错误/反馈**：中间版本报告记录 host `0.1154 s`、ARM hot `2.29–2.47 s`，但 **cold 为 `2.61–3.28 s`，未稳定 ≤3 s**——不能用快照里的最好值宣称达标；该版本继续被优化。

**纠正动作**：两轮优化到 v3：先缓存/复数 FFT，按 ARM 实测"FFT 主导"改为**符号级直接卷积**；以 `v1→v3 21/21 MATCH_WITH_ALLOWED_TOLERANCES` 证明等价（版本单列、RECT 11/112 保持）；最终 ARM cold `1.5599 s`、hot median `0.5377 s`、max legal `1.5599 s`、21 输入总 `11.110 s`，`legal ≤3 s` 满足；**但同时记录 max RSS `89,016 KiB`（超过 64 MiB 记录预算）**，按"以内存换时延的权衡"保留，不删不利数据。

**结果与证据**：产品候选 `bedf98c7…` 经无 RF 试部署 `TRIAL_ACCEPTED`；该结果只证明计算路径与交互性能，不证明真实 RRC 接收或当前交付版本达标。出处：`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-rrc-perf-2026-10-24/PERF-REPORT.md`、`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-rrc-candidate-2026-10-24/CANDIDATE-REPORT-FINAL.md`；仓库内记录：[2026-10-23 QPSK 离线报告](../reports/2026-10-23-p3-qpsk-offline.md) §"RRC 优化产品候选"；对照结论见 [优化与复现说明](OPTIMIZATION-AND-REPRODUCTION.md) §1.5。

### S3：首次 ARM 预算失败后的单变量优化（跨候选，不作配对）

**提示词/任务要点**（改写自 [HEADROOM 合同收尾报告](../reports/2026-10-11-headroom-contract-final.md) §"验证与资源"）：ARM 100 次完整计算首次未通过预算时，定位并修复；**不得提高预算或重复运行取优**；修复后必须证明计算等价（标量参考逐项相等、保存窗口分析严格相等），失败记录保留。

**模型回答/产出要点**：首次候选 `ebad0de…` 的 ARM P95 `314.963 ms` 未通过（≤300 ms），失败证据完整保留；定位到 BPSK 参考计算的 127 次逐相位平均后，改为一次 `127×63` 批量整数计算；重测新候选 `b67a8aa…` P95 `270.793 ms`、RSS `31524 KiB`、原生 CNN，通过。

**错误/反馈**：首次超预算即失败；报告明确"这是保存 IQ 的计算时间，不是触摸或实采时延"，且"未提高预算或重复运行取优"。

**纠正动作**：只改 BPSK 参考平均的实现方式（算法单变量）；用 127 种子标量参考逐项相等、四个保存窗口完整五类分析严格相等（仅规范发布 ID 差异）证明输出等价；失败帧与失败收据不替换。

**结果与证据**：新候选资格通过；但这是**跨候选**数据（失败候选 → 修正候选），按 [优化与复现说明](OPTIMIZATION-AND-REPRODUCTION.md) §1.1 的判定**只作背景，不作优化前后配对**。出处：[HEADROOM 合同收尾报告](../reports/2026-10-11-headroom-contract-final.md) §"验证与资源"；配对判定见 [优化与复现说明](OPTIMIZATION-AND-REPRODUCTION.md) §1.5 第 1 行。

## 8. 本文不证明什么

- 不证明"AI 能一次写对"：三个案例都是**发现错误 → 纠正 → 保留限制**的过程，不是成功叙事。
- 不证明项目整体验收通过：完整物理采集、正式环境诊断、真人现场验收等仍有未完成项（[交付材料索引](DELIVERY-INDEX.md) §5）。
- 不证明技能可跨项目/跨板卡：该主张**尚未验证**（[技能包 README](../../skills/README.md) §4.4、§11）。
- 不证明本文引用的历史指标可在本仓库复算：原始收据、原始 IQ、原始会话与日志不在本仓库（[交付材料索引](DELIVERY-INDEX.md) §9）。
- 不发布完整私人会话：本文只做改写摘要与方法归纳，未附带会话原文、截图或附件。

## 9. 出处清单

| 主题 | 文件 |
|---|---|
| 协作角色与写范围 | [docs/memory/01-project-map.md](../memory/01-project-map.md) §3 |
| 门禁模型与状态阶梯 | [docs/memory/02-gate-model.md](../memory/02-gate-model.md) §1–§3 |
| 开发纪律（红绿、单次尝试、账本、停止条件） | [docs/memory/03-development-discipline.md](../memory/03-development-discipline.md) §1、§5、§6、§8、§9 |
| 工程陷阱 16 条 | [docs/memory/04-engineering-pitfalls.md](../memory/04-engineering-pitfalls.md) §4–§10、§14–§16 |
| 审查分级与试部署规则 | [docs/requirements/development-review-policy.md](../requirements/development-review-policy.md) §1、§2、§6 |
| 验收清单（六类门） | [docs/requirements/acceptance-checklists-p1-p6.md](../requirements/acceptance-checklists-p1-p6.md) |
| 案例 K1：round 2 独立审阅与新增教训 | [docs/superpowers/plans/2026-10-07-s2-settle-validation-next.md](../superpowers/plans/2026-10-07-s2-settle-validation-next.md) §"G0 执行记录 round 2" |
| 案例 K1：坑与未完成 | [docs/reports/2026-10-07-g0-handoff.md](../reports/2026-10-07-g0-handoff.md) §操作要点、§未完成 |
| 案例 K2：G1 复核与准入缺口 | [docs/reports/2026-10-09-claude-g1-review.md](../reports/2026-10-09-claude-g1-review.md) §准入缺口、§本轮独立审阅 |
| 案例 K2：补证后 G1 通过 | [docs/reports/2026-10-10-claude-progress-and-closeout.md](../reports/2026-10-10-claude-progress-and-closeout.md) 开篇结论、§Claude 实际完成情况、§验证证据与边界 |
| 案例 K2：包装器退出码死代码 | [docs/superpowers/plans/2026-09-17-ad9361-tx-bringup.md](../superpowers/plans/2026-09-17-ad9361-tx-bringup.md) 事件表 E17 |
| 案例 K3：runner 任务与实现 | [docs/reports/2026-10-12-deepseek-harness-progress-check.md](../reports/2026-10-12-deepseek-harness-progress-check.md) §结论与后续；[docs/reports/2026-10-12-headroom-runner-implementation.md](../reports/2026-10-12-headroom-runner-implementation.md) §12 |
| 案例 K3：两轮独立复审与 N1 | [docs/reports/2026-10-13-headroom-runner-independent-review.md](../reports/2026-10-13-headroom-runner-independent-review.md) §0、§5 |
| 片段 S1：受控 A/B 与口径澄清 | `<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-perf-arm-2026-10-21/result/PERF-ARM-RESULT.md`、`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-modrec4-product-2026-10-21/PERFORMANCE-DECISION.md`（本地原件，不在仓库） |
| 片段 S2：RRC 优化与内存权衡 | `<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-rrc-perf-2026-10-24/PERF-REPORT.md`、`<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-rrc-candidate-2026-10-24/CANDIDATE-REPORT-FINAL.md`（本地原件，不在仓库）；仓库内记录：[2026-10-23 QPSK 离线报告](../reports/2026-10-23-p3-qpsk-offline.md) §"RRC 优化产品候选" |
| 片段 S3：首次 ARM 预算失败与单变量优化 | [docs/reports/2026-10-11-headroom-contract-final.md](../reports/2026-10-11-headroom-contract-final.md) §"验证与资源" |
| 优化配对判定与从零复现 | [docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md](OPTIMIZATION-AND-REPRODUCTION.md) |
| 案例摘编与硬规则 | [docs/engineering/AI-COLLABORATION-CASE.md](AI-COLLABORATION-CASE.md) |
| 技能包入口与已验证/未验证 | [skills/README.md](../../skills/README.md) |
| 交付索引与证据边界 | [docs/engineering/DELIVERY-INDEX.md](DELIVERY-INDEX.md) §5、§9 |
| 工程整理与技能格式校验 | [docs/engineering/2026-10-30-ORGANIZATION-REPORT.md](2026-10-30-ORGANIZATION-REPORT.md) |
| 分享、隐私与脱敏 | [docs/engineering/SHARING-AND-CLEANUP.md](SHARING-AND-CLEANUP.md) |

## 附录：脱敏与改写规则

本文对协作过程统一做了以下处理，原会话与原始记录保留在开发机、不随仓库发布：

- 会话类素材按**要点改写**（谁在什么范围发现了什么、如何纠正、结果与边界），不复制原文大段内容；
- 删除或替换：个人姓名、账号/用户名、邮箱、学校/单位、主机绝对路径与盘符、设备串号与 VISA 资源串、串口号、私网地址、凭据与授权别名；
- 引用会话结论时只保留可公开的方法与项目事实；原始收据、原始 IQ、照片与截图的引用一律标注"不在本仓库"；
- 数字与结论按仓库文档记载引用，并保留原文的限定语；不把 `NOT_RUN`/`UNKNOWN` 改写成通过。
