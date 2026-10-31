# AI 协作案例：一个 FPGA/SDR 工程里的"证据优先"实践

本文记录本项目在人机协作中的真实方法与失误，供读者判断"AI 参与工程"能做到什么、又容易错在哪里。文中所有事件都可以在本仓库的文档中找到出处；**原始实验收据、原始 IQ、照片与构建产物不在本仓库**（见 [SHARE-README.md](../../SHARE-README.md) 和 [证据边界](../engineering/SHARING-AND-CLEANUP.md)），因此本文的数字与结论一律按"据某文档记载"引用，不表示读者可以在仓库内复算。

## 0. 背景与范围

- 项目是 Zynq-7020 + AD9361 的便携收发平台，同时保留一条独立的辅助 AWG 开发链；三条流水线产物不混用（见 [工程结构](PROJECT-STRUCTURE.md)）。
- 参与者包括人类用户、多个 AI 编码代理，以及只审不写的独立复审角色。AI 会写代码、跑离线测试、写报告，也会犯错；人类负责授权、现场确认和最终验收。
- AI 协作在本项目里不是"让模型一次写对"，而是**用流程把不确定性压小**：先红后绿、判据先于测量、单次尝试不重试、失败原样保留、独立复审不自签。
- 本文只讲方法，不宣称项目整体验收通过。完整交付状态与限制见 [README](../../README.md) 与 [材料索引](DELIVERY-INDEX.md)。

## 1. 协作模型：四个角色，写范围互斥

| 角色 | 职责 | 边界 |
|---|---|---|
| Lead（主代理） | 分派、整合、最终验收与最终回复 | 不并发写别人正在写的文件；最终自己跑一遍验收命令 |
| 一次性子代理 | 自包含的只读/离线任务（盘点、复算、写报告） | 不并发写同一文件；结论必须回带路径与摘要 |
| 持久队友 | 有独立写范围的长任务 | 任务卡登记 `write_scopes`，写范围互斥 |
| 独立复审者（非实现者） | 只审不写，给 `APPROVED` / `CHANGES_REQUIRED` | 实现者不得自签；不得改写历史收据 |

配套纪律：共享单例资源串行（板卡、`current` 发布、pytest 全局临时目录、git index）；看到别人的未跟踪目录不动、不删、不采纳；写完由 Lead 看 `git status` 与最终 diff。出处：[工程地图](../memory/01-project-map.md) §3、[开发纪律](../memory/03-development-discipline.md) §写范围。

**为什么这件事重要**：AI 代理彼此没有锁，只有纪律。写范围一旦重叠，最坏的情况不是冲突，而是两份"看起来都对"的证据互相覆盖。

## 2. 门禁模型：从离线到发布分为六级

项目把"说过的话"和"证据"分开，用状态词和六类门约束结论（参见 [门禁模型](../memory/02-gate-model.md) 与 [验收清单](../requirements/acceptance-checklists-p1-p6.md)）：

- 离线（OFFLINE）→ ARM 原生（ARM）→ 数字回环（DL）→ 有线物理回环（PHY）→ 真人（HUM）→ 发布（REL）。
- 未执行的层保持 `NOT_RUN`；没有原始收据的结论写 `UNVERIFIED`；不把"测试数量"当成"物理通过"。
- 物理派发另有 G0–G8 的授权、账本、nonce 与停止条件；默认单次尝试，失败不重跑凑数。

这套模型的价值在下面的案例里反复出现：它让 AI 的产出被"门"检验，而不是被措辞检验。

## 3. 案例一：测试通过了，但通过的原因是错的

**发生**：围绕"失败交易后清理发射源"这个行为，实现里写了一条测试并通过。独立规范复审指出：该测试观测的时机是 `serve()` 返回之后，那时断线清理规则已经执行过——它把功劳记在了一条从未运行的路径上。

**证据**：[S2 稳定等待接续计划](../superpowers/plans/2026-10-07-s2-settle-validation-next.md)（"新增教训"节）、[G0 交接](../reports/2026-10-07-g0-handoff.md)（"坑"小节）。

**问题**：断言只要求"抛错/失败"，没有匹配它名字所指的那条特定消息；一个更早的守卫就能满足它。

**纠正**：新增连接存活期的测试钩子 `timing_before_read`，在 EOF 之前、连接仍存活时检查；用 `test_a_failed_wait_stops_the_source_inside_the_transaction` 固定交易内语义；再用"把旧门禁加回去"的变异证明新测试确实会失败。

**教训**：**通过的原因比通过本身更重要。** 每个 `pytest.raises` 都要 `match=` 到它名字所指的特定消息；独立复审要用"删掉检查看是否还绿"这一招。

**可核查性**：读者可在本仓库读到上述两份文档与 `rf_overlay/sw/board_app/test/` 的测试源码，但不能在仓库内复现当时的运行日志（原始日志未随包）。

## 4. 案例二：退出码在管道和包装器里失真

**发生**：两次同类事故。其一，`make host-test` 的退出码经 `tail` 管道后取到的是 `tail` 的状态，失败被读成成功；其二，远程命令包装器没有调用 `recv_exit_status()`，退出码判据恒为 0，固件里用退出码编码的真实失败永远看不见。

**证据**：[工程踩坑](../memory/04-engineering-pitfalls.md) §14、[G1 复审](../reports/2026-10-09-claude-g1-review.md)（退出码小节）、[AD9361 TX bring-up 计划](../superpowers/plans/2026-09-17-ad9361-tx-bringup.md)。

**问题**：AI 很擅长"看到 0 就认为成功"，而 shell 管道、包装器、日志重定向都会悄悄换掉被观测的对象。

**纠正**：解析外部命令输出时显式分离 stdout 与退出码；包装器必须返回真实退出码；把"退出码 0"与"证据满足"当成两个独立判据。

**教训**：**先确认你在读的对象，再相信读数。** 一条命令的"成功"只是一个字段，不是结论。

## 5. 案例三：读取失败被当成"没有错误"

**发生**：研究 runner 在事后读取板端存储失败或读到不完整数据时，仍然产出空故障列表并判 `RUN_COMPLETED`/exit 0，还用缺失的 `boot_id` 静默解除"是否重启"的判据。独立复审用反例抓出，改为具名终态 `RUN_UNKNOWN_STORAGE_UNREADABLE`，并规定"不可读的 boot id 永不等于 same"。

**证据**：[runner 实现报告](../reports/2026-10-12-headroom-runner-implementation.md)、[独立复审](../reports/2026-10-13-headroom-runner-independent-review.md)、[Harness 进度核对](../reports/2026-10-12-deepseek-harness-progress-check.md)。

**问题**：fail-open。异常路径没有具名终态，于是"没读到"被折叠进"没问题"。

**纠正**：所有"我不知道"必须落成具名状态；不可读不等于空；不可判定不等于通过。

**教训**：**对 AI 产出的结论，"未知"要显式存在。** 宁可让流程失败得难看，也不要让它通过得可疑。

## 6. 案例四：假硬件会骗人——谓词在真板上才暴露

**发生**：两个只在真板上暴露的谓词缺陷。其一，`cap_cfg` 把整字判零（bit2 是 commit epoch），真实帧因此被误读为 CAP_ARM，研究被 `RUN_STOPPED_EVIDENCE:1` 拒绝；其二，`lease_held` 谓词写反，读能力的客户端正是租约持有者，导致真实 46 dB 研究永远必拒——而全部假硬件用例注入的 peer 都报 false，测试完全看不见。

**证据**：[HEADROOM 安装门与预检报告](../reports/2026-10-11-headroom-install-gate-and-preflight.md)、[门禁模型](../memory/02-gate-model.md) §5、[未决问题](../memory/06-open-questions.md)。

**问题**：测试夹具与真实帧不同形，测试越"全绿"越掩盖问题。

**纠正**：谓词按真实帧形状重写并加具名拒绝；夹具先与一份真实帧逐键比对；把"当前唯一阻塞"写进工程记忆。

**教训**：**测试必须守它声称守的东西，夹具必须与真机同形。** 假硬件能验证逻辑，不能验证"逻辑是否对准真实世界"。

## 7. 案例五：结论的时效性——同一件事，三个时间点三种状态

**发生**：真人回环录像一度未见成功，先被归因为"确认入口缺失"；后续用运行状态（`TX_ACTIVE` 而非 `READY`）解释了阻断；再经用户照片与文字分别确认单次链路通过。每一步都保留原文，不覆盖旧结论。

**证据**：[真人回环诊断](../reports/2026-10-03-human-loopback-diagnosis.md)、[S1 真人照片复核](../reports/2026-10-04-s1-human-photo-review.md)。

**问题**：AI 容易把"最新一次推断"写成"事实"，然后在下一轮引用自己。

**纠正**：历史结论保留、加日期与限定语；照片与文字分开记账、不互相顶替；现场验收由用户确认，不代签。

**教训**：**结论要带时间戳和证据等级。** "目前未观察到"不等于"不存在"；"已确认"必须写清是谁在哪一层确认。

## 8. 案例六：交付复审发现的三类漂移

**发生**：一次独立交付复审发现：①审查通过之后，QAM16 验收语义从"BER=0"被改成"BER∈[0,1]"仍判 `CHECK_PASS`；②首轮失败后多次重派，实际采集超过原预算；③交付文档与原始日志在"额外几次采集"和"是否发生过 physical capture"上不一致。

**证据**：[去 QAM64 交付版 Codex 增量复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)。

**问题**：不是代码错，而是"成功退出码表达了什么"被悄悄改写；预算与账本在过程里被突破却没人汇总；文档只带最终成功日志。

**纠正**：区分 `execution_complete` 与 `metrics_passed`；保留全部失败派发日志；交付材料按实际执行账本修正；承认 QAM16 为非零 BER 的受限演示。

**教训**：**复审不只审代码，还要审"成功"这个词的含义。** 一个绿灯如果语义漂移了，比红灯更危险。

## 9. 案例七：测试有效性工程——变异、守卫与锚点

**发生**：多个"看起来验证过了"的假象：加新守卫后没有重跑旧变异，整段删掉档位检查后 49 条测试仍然全绿；变异替换模式没匹配上、源码根本没改，却因为编译 exit 0 被当成"变异通过"；Windows CRLF 让 `\n` 锚点的正则静默不匹配；`sdhci.*err` 缺词界把 `Pierre` 命中的驱动版权行当成存储故障；把 `stderr` 当 `stdout` 解析导致一批假失败。

**证据**：[工程踩坑](../memory/04-engineering-pitfalls.md) §10–§14、[G0 交接](../reports/2026-10-07-g0-handoff.md)。

**纠正**：变异前先断言改动落地；新增/移动守卫后重跑全部旧变异；正则加词界；外部命令显式分离 stdout 与退出码；两次结论比较前确认用的是同一分类器版本。

**教训**：**测试本身也要被测试。** 变异验证必须能证明"它抓住了"，而不是"我没看见失败"。

## 10. 人工门：不代签

- P0-0 的任务暂停由用户确认（`CONFIRMED_BY_USER`），报告明确标注"非工具级证明"，并与两次只读盘点交叉支持。
- 真人照片与用户文字分别记录，不互相顶替；现场演示的最终确认由用户完成，AI 不代签。
- 授权/接线确认按固定字段与时间窗口登记，不构成新的硬件操作授权。

出处：[证据索引](../memory/05-evidence-index.md)、[授权约定](../memory/07-authorization-conventions.md)、[S1 真人照片复核](../reports/2026-10-04-s1-human-photo-review.md)。

## 11. 可复用的硬规则（每条一句话）

1. 判据先于测量：先写清楚什么算通过，再动手。
2. 先红后绿：先让测试因正确原因失败，再修到通过。
3. 通过的原因比通过本身重要：断言要匹配它名字所指的消息。
4. 未知要显式：不可读 ≠ 无错误，`NOT_RUN` ≠ 失败 ≠ 通过。
5. 单次尝试不重试：失败保留原帧，不靠重跑凑数。
6. 夹具与真机同形：模拟真机的夹具先与真实帧逐键比对。
7. 写范围互斥：并发代理先登记写范围，共享单例串行。
8. 实现者不自签：关键结论交给独立复审，复审只审不写。
9. 结论带时间戳与证据等级：不覆盖历史，不引用自己当证据。
10. 交付的人确认：现场与真人结论由用户确认，AI 不代签。

## 12. 本篇不证明什么

- 不证明项目整体验收通过：完整物理采集、正式环境诊断、统一发布清单、真人现场验收仍有未完成项（见 [README](../../README.md) 的"失败与限制"）。
- 不证明本文引用的指标可在仓库内复算：原始收据、原始 IQ、照片与构建产物不在本仓库，指标最高证据层级是"公开文档的记述"。
- 不证明 AI 可以替代硬件实验：相反，本项目的多个关键缺陷只在真板上暴露（案例四）。
- 不证明流程能消除错误：它只能让错误更早、更可见地被发现，并在发现后留下可核查的记录。

## 附录：出处清单

| 主题 | 文件 |
|---|---|
| 协作角色与写范围 | [docs/memory/01-project-map.md](../memory/01-project-map.md) |
| 门禁与状态阶梯 | [docs/memory/02-gate-model.md](../memory/02-gate-model.md) |
| 开发纪律 | [docs/memory/03-development-discipline.md](../memory/03-development-discipline.md) |
| 工程踩坑 16 条 | [docs/memory/04-engineering-pitfalls.md](../memory/04-engineering-pitfalls.md) |
| 权威收据索引 | [docs/memory/05-evidence-index.md](../memory/05-evidence-index.md) |
| 未决问题 | [docs/memory/06-open-questions.md](../memory/06-open-questions.md) |
| 授权约定 | [docs/memory/07-authorization-conventions.md](../memory/07-authorization-conventions.md) |
| 审查规则 | [docs/requirements/development-review-policy.md](../requirements/development-review-policy.md) |
| G0 交接 | [docs/reports/2026-10-07-g0-handoff.md](../reports/2026-10-07-g0-handoff.md) |
| G1 复审 | [docs/reports/2026-10-09-claude-g1-review.md](../reports/2026-10-09-claude-g1-review.md) |
| HEADROOM 安装门 | [docs/reports/2026-10-11-headroom-install-gate-and-preflight.md](../reports/2026-10-11-headroom-install-gate-and-preflight.md) |
| 交付复审 | [docs/reviews/2026-10-26-codex-delivery-closeout-review.md](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| 真人照片复核 | [docs/reports/2026-10-04-s1-human-photo-review.md](../reports/2026-10-04-s1-human-photo-review.md) |
