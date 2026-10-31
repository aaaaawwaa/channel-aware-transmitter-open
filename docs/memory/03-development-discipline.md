# 03 开发纪律（怎么干活才算数）

> **当前规则（2026-10-17 用户修订）**：按[个人开发审查与部署规则](../requirements/development-review-policy.md)分级执行。R0 作者自检，R1 相关测试，R2 一次独立增量审查；以下历史案例用于解释经验，不再引申出全链重复审查或逐命令授权。

> 这些规矩不是本目录发明的，是从 2026-08 起的多轮真实事故与独立复审中固化的。每条给出**权威来源**，
> 本目录只做整合。（经验）标记的是跨任务通用的做法，（事实）标记的是某次已发生的记录。

## 1. 先红后绿（红 → 绿，证据都要留）（经验 + 事实）

- 新增/修改行为前，**先写会失败的测试**并留下红态日志与**红的原因**；实现后留绿态日志。
  工程里成对的目录即此纪律的产物：`evidence/rf-teaching/p1-run-bundle-red-2026-10-14/RED-NOTES.md` 与
  `p1-run-bundle-green-2026-10-14/GREEN-NOTES.md`；`s2-headroom-g3b-2026-10-17/logs/red-targeted.log`（24 failed / 1 passed）
  与 `green-targeted.log`（25 passed）。
- **红必须是因为目标行为缺失**，不能因为语法错、缺文件、环境问题（否则等于没红）。
- 测试数量不是证据：`2867 passed` 只说明离线面通过；物理/真人结果必须另有物理/真人证据
  （见 [02](02-gate-model.md) §1）。

## 2. 按影响分级审查（当前规则）

- 文档、引用、格式等 R0 改动由作者自检；不改变硬件控制或证据判定的局部 R1 改动跑相关测试。
- R2（硬件控制、判定合同、部署切换等）由未参与本次实现的一人做**一次增量审查**，规格/实现/质量可合并。
- 同一差分已有独立 APPROVED，且落地字节或经说明的行尾等价内容对应受审补丁时，复用该结论；只审新增行为。
- 独立审查保留 `APPROVED` / `CHANGES_REQUIRED` 及其范围，作者不得自签或改写旧批准。新增候选材料记录对既有报告的引用和实际摘要，不伪造旧绑定。
- 搬目录、生成包、补文档不重复做全套审查；现有工具确需重新绑定时，按真实输入完成一次迁移。
- 部署改动优先采用[单次审查与试部署闭环](../rf/deployment-trial-runbook.md)：同一审查覆盖候选、板端验收和回退计划，板端验收与安全读回不另开一轮代码审查。
- 权威例子：[evidence/rf-teaching/s2-headroom-g3b-2026-10-17/review/REVIEW.md](../../evidence/rf-teaching/s2-headroom-g3b-2026-10-17/review/REVIEW.md)、
  [p1-run-bundle-review-2026-10-14/REVIEW.md](../../evidence/rf-teaching/p1-run-bundle-review-2026-10-14/REVIEW.md)、
  [2026-10-13-headroom-runner-independent-review.md](../reports/2026-10-13-headroom-runner-independent-review.md)。

## 3. 相关回归与必要反例（当前规则）

**目的**：证明测试真的在守它声称守的东西。

1. 对问题写可复现用例，运行受影响回归；对新增关键禁止状态写直接反例。不要求固定数量变异或未触及模块的全量战役。
2. 普通测试不能证明关键检查有效时，在隔离副本做少量相关变异；有效的禁止状态变异若未被抓，先修复测试/实现。
   先例（事实）：`s2-offline-negatives-review-2026-10-15/REVIEW.md` 判
   `CHANGES_REQUIRED`，理由是 M6 变异（把真实结果静默替换为 `NOT_RUN`）**整个 `scripts/tests` 仍 537 passed**；
   处置记录见同目录 `RESPONSE.md`。
3. **变异必须先断言"已生效"再编译/运行**：首次做 F3 变异时模式没匹配 → 编译 exit 0、无 FAIL，读数看起来像
   "变异通过了"，其实是没改到（[2026-10-07-g0-handoff.md](../reports/2026-10-07-g0-handoff.md)）。
4. **锚点唯一性**：替换前断言 `anchor_occurrences == 1`；优先使用隔离副本，避免与其他 agent 的工作竞争。
5. 保留足以复现变异的脚本、差分和结果；源码已有 Git 记录时，不要求每轮再做整树旧字节归档。
6. 新增/移动守卫可能遮蔽旧检查时，重跑**受影响的**旧反例或变异（见 [04](04-engineering-pitfalls.md) 第 10 条），不扩展为全仓战役。

## 4. 冻结判据与按范围补证（当前规则 + 历史清单）

- **冻结判据文件**：某些测试文件一旦定稿即视为契约，改动必须显式声明并重跑变异。已知两份：
  - `scripts/tests/test_rf_headroom_run_bundle.py` —— **12 例冻结判据**；
    原文："后续修改该文件不得放松这 12 例"（[2026-10-13-headroom-board-run-package-design.md](../superpowers/specs/2026-10-13-headroom-board-run-package-design.md) §7）。
    事实：后续轮次（如 clock-anchor）只**新增**用例，12 例逐字节未改，且红态下仍全绿。
  - `docs/superpowers/specs/2026-10-14-s2-environment-diagnosis-contract-freeze.md` §2.1 的 **N1–N12 表** —— S2 环境诊断冻结判据。
- **冻结源清单**：`source-freeze.json` 逐文件 `files_sha256`（阶段 22 → 23 → 24 条随范围扩大）；
  原生冻结 **58 项**（`_native_sources`）、软件冻结 **46 项**（`cnn-bpsk-headroom-v1`）/ **44 项**（`cnn-bpsk-environment-v1`）。
  三处必须相等：工作树 == `source-freeze` == `arm-native/rf_daemon.build.json.source_sha256`。
- **证据按实际版本和范围使用**：源码/工具/夹具改动补齐受影响的验证；未改模块和已覆盖同差分的独立审查可以引用。
  文档或行尾改变不使整条历史证据链失效。实际部署模块改变需要新候选身份和必要 ARM 检查/安装；不把旧实测冒充新候选实测。
- 以上冻结清单保留历史合同含义；现行可执行绑定须按真实字节满足。新增阶段默认只绑定必要部署输入，避免把状态文档变化变成安装阻塞。
- 重冻结只允许更新**陈旧值**，不得动身份字段；每轮记录"前 → 后"摘要与原因（G4prep 做法）。

## 5. 单次尝试 / 不重试 / 不补采（经验，硬性）

- 物理会话按**实际授权与预算**执行：一次会话最多 N 次尝试（如 5），nonce 消耗后，**超时/UNKNOWN/失败不自动重派、不补采、不临时调参**，
  原始帧与拒绝理由完整保留。
- 只读预检和离线校验失败可修正后重跑，保留每次失败日志；不要把它们与真实安装/采集重试混为一谈。
- 安装发生副作用后结果不明，不盲目重装；只读定位后按授权范围使用已有回退机制。历史先例：b82 覆盖 399b 时安装器因目标目录
  已存在而拒绝 → 零板卡写入、不重试，改用独立授权的原子切换路径。
- 用户已授权整个试部署与回退工作包时，已知的板端验收失败可直接按既定目标回滚，不重复询问；失联/重启等 UNKNOWN 仍先观察原作业，不能与尚在运行的安装竞争。
- 超时/失联后的正确动作是**另开只读会话**读回 `current`/`active-release`/服务/`boot_id`/RF 安全态，而不是重发。
- 反面先例（事实）：早期某次安装 240 s 客户端超时 → `UNKNOWN`，**未重试**；随后板卡自行重启，状态由只读读回定案。

## 6. 账本与 nonce 一次性（经验 + 事实）

- 板端账本 `authorization.jsonl` **追加式**：每行 `fsync`、撕裂尾行先截断再追加、重放同 nonce 具名拒绝且不追加第二条。
  （判据与红/绿记录：`p1-run-bundle-red-2026-10-14`、`p1-run-bundle-green-2026-10-14`）
- `nonce` 一次性、全小写 hex、不可复用；授权有 `issued_at` / `expires_at`；接线确认有新鲜度窗口（设计意图 ≤12 h）。
  模板（不含真实值）：`evidence/rf-teaching/headroom-authorization-template-2026-10-14/`。
- 授权**消耗后不可复用**：nonce/attempt_id 等由工具自动生成，人工只确认真实接线和操作范围。
  未改变且仍在有效窗口内的接线确认可以引用；新派发仍使用新 nonce/attempt_id 和真实有效的授权。历史先例 `AUTHORIZATION_CONSUMED`（账本 `seq=1`）；第二次派发的授权
  `expires_at=2026-10-15T14:11:51Z`、接线窗口到 `2026-10-16T06:59:00+08:00` —— **新派发必须取新授权、新 `attempt_id`/nonce、
  有效人工接线确认**，并执行现有 runner 门禁。
- **token 只记路径，不读值、不入档**：`token_ref` 非空字符串且**不得包含 token 值本身**；token 值与其摘要
  都不得写进任何证据文件；唯一通路是 `read_control_token(path)` → `client.headroom_arm(token)`。
  （原文：[2026-10-14 执行包](../superpowers/plans/2026-10-14-headroom-physical-session-execution-package.md) §3.3、§7；
  [runner 设计](../superpowers/plans/2026-10-12-real-headroom-research-runner-design.md) §token 处理纪律）

## 7. 保留原始证据，减少重复归档（当前规则）

- 源码和文档通过 Git 差分/提交保留历史，不要求每次编辑前复制整份旧字节。真实 IQ、原始日志、旧收据和既有封存件保留原件，不覆盖历史结果。
- 归档也要能被引用：`s2-headroom-g4prep-2026-10-17/archived/old-bytes/…` 保留被重新冻结前的字节，
  `archive-record.json` 逐文件记 source/copy 摘要；`s2-headroom-g1-2026-10-16/archived/` 用后缀（`.stale-binding`、
  `.before-g1`、`.f5336594`）明确"这是旧版本"，不改写历史正文。
- 导入外部/兄弟工作区的原件时**逐字节复制 + 双端摘要核验 + 来源绝对路径**，不改名成伪造的字段形状
  （反例被明令禁止："不得改名为伪造的 `post-state.json` 或改写字段"）。
- **历史记录被改写等于造假**：描述旧状态的日志、旧收据、旧封存件一律不改（即使它们已经过期）。

## 8. 停止条件（任一条出现即停，不推进）（经验，硬性）

1. **身份漂移**：`current` / `active-release` / manifest / 发布目录 / daemon 版本与授权工件或证据不符。
2. **`UNKNOWN`**：任何终态不明（SSH 失联、安装超时、状态读不出）→ 先只读判定，不重试。
3. **门禁失败**：G0–G8 任一步失败、存储故障、RF 非 safe、`boot_id` 变化（= 板卡重启，不可假设未重启）。
4. **新鲜度失效**：授权过期、接线确认超窗、时钟锚龄过大、`boot_id` 与锚不符。
5. **未证明项**：需要 `NOT_RUN` 的地方不得用推断补；只有本次操作所需的前置缺失才阻塞它，不以无关的最终验收未完成阻塞实验部署。
6. 违反 runner 设计 §2.3 禁止清单任意一条 → 视为设计违规。

## 9. 写范围与并发纪律

见 [01-project-map.md](01-project-map.md) §3。要点：派活前声明预期写入集合、共享单例（板卡/git/临时目录）串行、
别人正在写或留下的未跟踪产物不动、Lead 最终自己看 `git status --porcelain` 并确认本轮相关检查结果；文档变更不跑产品全套测试。
