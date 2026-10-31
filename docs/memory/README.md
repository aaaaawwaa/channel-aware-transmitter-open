# 工程记忆（docs/memory/）— 索引与用法

> **本目录不是证据，也不是规格。** 它只做三件事：**指路**（权威来源在哪）、**整合**（把散落在
> 报告/收据/NOTES 里的经验收成条目）、**划界**（哪些是已验证事实、哪些是经验、哪些仍是
> `NOT_RUN`/`UNKNOWN`）。任何结论要真，必须回到它指向的原始文件与收据。
>
> 建立日期：2026-10-17。建立时工作树 HEAD `cc10b02`（分支 `codex/description-aligned-platform-plan`）。
> 本目录**只由**不参与实现的一侧撰写；它**不**改产品代码、测试、证据、被封存件。

## 1. 读哪一份、什么时候读

| 你是谁 / 要做什么 | 先读 | 再读 |
|---|---|---|
| 刚接手本工程（任何角色） | `README.md`（本篇）→ `01-project-map.md` | `CLAUDE.md` 顶部"当前"段；`docs/reports/` 最近 2–3 篇 |
| 要动代码、写测试 | `03-development-discipline.md` | `04-engineering-pitfalls.md` |
| 要连板、装发布、派发物理会话 | `02-gate-model.md` | `04-engineering-pitfalls.md`（尤其 RTC/boot_id/token/nonce 四条） |
| 要判断"这件事到底证明到哪一步" | `02-gate-model.md` + `05-evidence-index.md` | `06-open-questions.md` |
| 要写新报告/新收据 | `05-evidence-index.md`（避免重造轮子） | `docs/requirements/acceptance-checklists-p1-p6.md` |
| 要给别人派活（Lead/子代理/队友） | `01-project-map.md` §3–§4 | `03-development-discipline.md` §8 |

**一句话规矩：动手前先读 03 与 04。**

## 2. 权威来源指针（本目录只索引，不复制原文）

| 主题 | 权威来源 | 一行摘要 |
|---|---|---|
| 工程当前状态、历史决策链（**最重要**） | [CLAUDE.md](../../CLAUDE.md) 顶部若干"当前/最新"段 | 逐轮记录，最新的在最上；旧段落保留为历史，不得删改 |
| 入口索引与最容易踩的坑 | [AGENTS.md](../../AGENTS.md) | 声明自己"是索引不是正文"，正文在 CLAUDE.md |
| 需求可追踪 + 状态阶梯定义 | [docs/requirements/brief-traceability.md](../requirements/brief-traceability.md) | B1–B10 逐条追到责任模块/测试/原始收据/当前状态 |
| 六类门验收清单 P1–P6 | [docs/requirements/acceptance-checklists-p1-p6.md](../requirements/acceptance-checklists-p1-p6.md) | 离线→ARM→数字回环→有线物理→真人→发布 |
| 状态对象契约（release/capture/连接代次…） | [docs/requirements/rf-status-object-contract.md](../requirements/rf-status-object-contract.md) | 责任与失效规则 |
| 物理派发门序列 G0–G8 | [docs/superpowers/plans/2026-10-12-real-headroom-research-runner-design.md](../superpowers/plans/2026-10-12-real-headroom-research-runner-design.md) §2.3–§3 | 禁止清单 + 顺序即门禁 |
| 板端运行包/账本/单次派发设计 | [docs/superpowers/specs/2026-10-13-headroom-board-run-package-design.md](../superpowers/specs/2026-10-13-headroom-board-run-package-design.md) | 六类结局、RTC 无效、负控集合 |
| S2 环境诊断判据冻结（N1–N12） | [docs/superpowers/specs/2026-10-14-s2-environment-diagnosis-contract-freeze.md](../superpowers/specs/2026-10-14-s2-environment-diagnosis-contract-freeze.md) | 冻结判据，不执行采集 |
| 实施计划与分批（Chunk 1–3） | [docs/superpowers/plans/2026-10-13-final-prj-implementation-plan.md](../superpowers/plans/2026-10-13-final-prj-implementation-plan.md) | P0/P1/P2… 每阶段六类门 |
| 单次物理会话执行包（授权/接线/token） | [docs/superpowers/plans/2026-10-14-headroom-physical-session-execution-package.md](../superpowers/plans/2026-10-14-headroom-physical-session-execution-package.md) | 授权工件字段、token 不入档、接线新鲜度 |
| 板端 C 原生验收判据 | [docs/rf/boardtest.md](../rf/boardtest.md) | **判据先于测量**写死 |
| 历史报告全文 | [docs/reports/](../reports/) | 按日期命名；本目录只给最相关的那几篇 |
| 原始收据与日志 | [evidence/](../../evidence/) | 结论的唯一可核查来源 |

> **两处已知不一致（以 CLAUDE.md 为准）**：`AGENTS.md` 只列"两条流水线"，而 CLAUDE.md 列**三条**
> （AWG / PYNQ / RF）；`AGENTS.md` 的产物路径（`pynq_overlay/out/ledbtn.bit`）比 CLAUDE.md 旧。
> 见 `01-project-map.md` §1 注。

### 2.1 已经写过的经验（本目录**只引用**，不重复造轮子）

| 已被写过的经验 | 已覆盖什么 | 本目录的处置 |
|---|---|---|
| [CLAUDE.md](../../CLAUDE.md) 顶部各"当前/最新"段 | 每轮状态、决策、失败保留、下一顺序 | 只在 [05](05-evidence-index.md) 指向；不复制数字 |
| [AGENTS.md](../../AGENTS.md) "最容易踩的几条" | 改 RTL 不重跑 build、`blk_mem_gen` 32 位地址、`Overlay()` 冲突、`pynq_ssh.py` | [04](04-engineering-pitfalls.md) §16 只列一行 |
| [docs/reports/2026-10-07-g0-handoff.md](../reports/2026-10-07-g0-handoff.md) | CRLF 锚点失效、加守卫后重跑旧变异、`pytest.raises(match=)`、变异须断言已生效、`test_vendor_integrity` 是脚本、分解释器 | [04](04-engineering-pitfalls.md) §1/§10/§11/§16 给路径 + 四段式提炼 |
| [docs/reports/2026-10-13-final-prj-baseline.md](../reports/2026-10-13-final-prj-baseline.md) | `--basetemp` 必须在仓库内、清理前校验前缀 | [04](04-engineering-pitfalls.md) §3 |
| [docs/requirements/acceptance-checklists-p1-p6.md](../requirements/acceptance-checklists-p1-p6.md) | 六类门定义、`N/A` 规则、历史收据不等价 | [02](02-gate-model.md) §1 |
| [docs/requirements/brief-traceability.md](../requirements/brief-traceability.md) | 状态阶梯定义、B1–B10 逐条状态 | [02](02-gate-model.md) §2 + [05](05-evidence-index.md) 提示 |
| [runner 设计](../superpowers/plans/2026-10-12-real-headroom-research-runner-design.md) §2.3–§3 | 禁止清单、G0–G8、接线/token 纪律 | [02](02-gate-model.md) §3、[03](03-development-discipline.md) §6/§8 |
| [board-run-package-design](../superpowers/specs/2026-10-13-headroom-board-run-package-design.md) | 六类结局、RTC 无效、12 例冻结判据 | [02](02-gate-model.md) §4、[03](03-development-discipline.md) §4 |
| [2026-10-14 执行包](../superpowers/plans/2026-10-14-headroom-physical-session-execution-package.md) | 授权工件字段、token 不入档、接线新鲜度 | [03](03-development-discipline.md) §6 |
| 各阶段 `NOTES.md`（evidence 内） | 逐轮做法与失败留痕 | [05](05-evidence-index.md) 逐条一行摘要 |

**判断标准**：写下一篇记忆前先 grep 仓库；只要权威文件已经写过，这里最多给"路径 + 一句话"。
## 3. 本目录各文件

| 文件 | 内容 |
|---|---|
| [01-project-map.md](01-project-map.md) | 三条流水线、构建/验收入口、四条并行角色、写范围纪律 |
| [02-gate-model.md](02-gate-model.md) | 六类门 + 状态阶梯 + G0–G8 与封存 G1–G4 + HEADROOM 候选链实例 |
| [03-development-discipline.md](03-development-discipline.md) | TDD、独立复审、变异战役、冻结判据、单次尝试、账本/nonce、provenance、停止条件 |
| [04-engineering-pitfalls.md](04-engineering-pitfalls.md) | 12+ 条真实事故提炼（现象→根因→处置→防再犯检查） |
| [05-evidence-index.md](05-evidence-index.md) | 权威收据/报告索引 + 状态（APPROVED/PARTIAL/NOT_RUN） |
| [06-open-questions.md](06-open-questions.md) | 未证实项与待决事项，每条写"需要什么才能关闭" |

## 4. 写入与维护规则（经验）

1. **只写指针 + 一行摘要**。凡是 CLAUDE.md / docs/reports / docs/superpowers / docs/requirements /
   evidence 已经写过的内容，这里**只给路径和一句话**，不复制正文。
2. **事实与经验分开写**。事实条目必须带路径；经验条目显式标注"（经验）"。
3. **`NOT_RUN` / `UNKNOWN` / `PARTIAL` 一律保留**，不得因为"应该会通过"而升级为通过；
   状态词与 `docs/requirements/brief-traceability.md` §1 一致。
4. **不新造数字**。需要数字时从原始收据复算或引用；不确定就不写。
5. 追加比修订安全：修订时保留原条目并注明日期与原因（历史被改写等于造假）。
6. 本目录**不**记录 token 值、nonce 值、私钥、口令，**只记路径**。

- 7-authorization-conventions.md — 授权/接线确认填写规则（授权人固定 <AUTHORIZER>、时间取本机当前时间）。
