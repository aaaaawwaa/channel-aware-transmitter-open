# HEADROOM 单次物理派发 runbook（2026-10-17）

日期：2026-10-17。**本文件是计划/操作包，不是授权**：它不连板、不安装、不派发、不生成 nonce。
它规定的动作只由人工签字工件 + 操作者现场执行承载。详细可执行版本（逐条命令）在
`evidence/rf-teaching/s2-dispatch-prep-2026-10-17/runbook.md`。

适用前提：**封存完成之后**（`<stage>/stage-checkpoint.json` 与 `<stage>/install-bundle/` 就绪，
且复审/资格收据与封存件自洽）。 【截至 2026-10-17：该前提已就绪——`<stage>/stage-checkpoint.json`（sha256 `b4bca398…`）与 `<stage>/install-bundle/`（candidate.tgz `5399bfb4…`）均已存在】

- 阶段目录：`evidence/rf-teaching/s2/executable-candidate-2026-10-16/`
- 资格发布（runner 唯一接受的 release）：`aba80e92aec4ed3d4e83196733a800ae`
- 运行包 runner 版本：`1.0.2`

## 0. 目标

在**零重试**的前提下，用**一次**授权完成一次物理研究会话：安装新候选 → 新接线确认 →
新一次性授权 → 新只读预检 → 单次派发（1 次初始化、≤5 次尝试：off-before/normal/narrowband/pulse/off-after）
→ 独立只读读回。任何身份漂移、UNKNOWN 或门禁失败都立即停止，**绝不重试补采**。

## 1. 前置条件

| 前置 | 判据 | 状态 |
|---|---|---|
| 封存收据 | `<stage>/stage-checkpoint.json` 的 `passed is True`、`release_id=aba80e92…`、`installed is False`、`physical_headroom='NOT_RUN'` | ⚠️ **尚不存在**（2026-10-17 探针：`install-bundle`/`stage-checkpoint.json` 均缺）；【**已更新（截至 2026-10-17）**：封存产物现已存在——`stage-checkpoint.json` `b4bca398…`、`install-bundle` candidate.tgz `5399bfb4…`、`development-checkpoint.json` `4f53f342…`；`verify_bundle` 与 dev checkpoint 的 `--verify` 已复跑通过。⚠️ 封存表还绑定少量 `docs/` 文件（如 `docs/superpowers/plans/2026-10-04-teaching-environment-roadmap.md`）：这些文件在此之后被修改会使 stage checkpoint 的 `--verify` artifacts 表失配（封存目录本体不受影响），需按既定流程重新封存】 |
| 安装包 | `<stage>/install-bundle/bundle-receipt.json` 通过 `rf_headroom_deploy.verify_bundle` | ⚠️ 同上；【**已更新（截至 2026-10-17）**：`install-bundle` 现已产出并通过 `verify_bundle`（99 成员；candidate.tgz `5399bfb4…`；`checkpoint_sha256=4f53f342…`；parent `b82b141e…`；`installed=false`）】 |
| 独立复审 | `<stage>/python/critical-review.json` 的 `verdict=APPROVED`、`candidate_release_id=aba80e92…` | ✅ 已存在（G1） |
| ARM 资格 | `<stage>/arm-runtime/receipt.json` `passed`、`p95_ms≤300`、`rss_kib≤65536`、`parity_mismatches=[]`、`new_rf_frames=0` | ✅ 已存在（G2，P95 272.79 ms / RSS 31728 KiB 量级） |
| 板卡现状（最近一次只读读回） | 已安装 `b82b141e…`、双服务 active/running、NRestarts=0、RF safe、零物理帧 | ✅ 2026-10-17 只读（`<stage>/final-safety/receipt.json`）；【**已过期（2026-10-17T12:26，范围外事实）**：现安装为 `aba80e92…`（`evidence/rf-teaching/s2-headroom-install-attempt-2026-10-17/install-live/install-receipt.json`，`INSTALLED_VERIFIED`、`retry_allowed=false`）；**S1 已执行、不得重装**】 |
| 运行包 | 重出后 `verify_run_bundle` 通过、`source_commit==HEAD`、`runner_version=1.0.2` | ⚠️ 现存包 `source_commit` 已过期，**必须重出** |
| 接线确认 | 逐字语句 + 12 h 窗口内 | ⚠️ 待人工（旧件已过期，不可复用） |
| 一次性授权 | 13 字段 + 新 nonce + 2 h 窗口 | ⚠️ 待人工生成（旧 nonce 已消耗） |
| 时钟锚路径 | 主机锚（现状）或另行授权校时 | **由用户决策**，见 `…/board-clock-decision.md` |

## 2. 步骤、门禁与停止条件

| 步 | 动作 | 门禁（全部成立才继续） | 失败即停 | 证据落点 |
|---|---|---|---|---|
| S0.1 | 工作树/提交核对 | 产品摘要与封存件一致；无未预期改动 | 摘要不符 → 停 | `<EV>/logs/s0-tree.txt` |
| S0.2 | 重出运行包 | `verify_run_bundle` 通过；`source_commit==git rev-parse HEAD`；成员恰好 3 项 | 失败 → 删目录重出 | `<EV>/run-bundle/` |
| S0.3 | 安装包校验 | `verify_bundle` 通过；`release_id=aba80e92…`；`installed False`；parent=板上 current | 不符 → 停 | `<EV>/logs/s0-install-bundle.json` |
| S0.4 | 人工工件 | 接线/授权/attempt_id 三件齐备且唯一 | 任一不合 → 回 S0 | `<EV>/{wiring-confirmation,authorization,attempt-id}.json` |
| S1 | **安装新候选（单次）** | exit 0；`outcome=INSTALLED_VERIFIED`；`board_state=VERIFIED_SAFE_NEW_RELEASE`；`retry_allowed False` | UNKNOWN/超时 → **不重装**，只读读回定案 | `<EV>/install-live/` |
| S2 | **新接线确认（人工）** | 语句逐字；`age ∈ [−60 s, 12 h]` | 超窗 → 重新确认（不得复用旧件） | `<EV>/wiring-confirmation.json` |
| S3 | **新一次性授权** | `load_authorization` 通过（`VALIDATION_RESULT: PASS`）；13 字段；新 nonce；2 h 窗口 | 拒绝 → 修工件后同命令重校（未接触板卡） | `<EV>/authorization-validation.log` |
| S4 | **新只读预检** | current=active-release=`<RELEASE>`；manifest release_id/合同/软件项 46；双服务 active/running/NRestarts=0；`/` rw；dmesg 无存储故障子串；RF safe；账本中**不含本次 attempt_id**（文件不存在则一定不含）；token 元数据 600/root | 任一失败 → 停（**不消耗授权**） | `<EV>/preflight.log` |
| S5 | **单次派发** | 锚 `age≤120 s` 且同 boot_id；命令只执行一次；stdout 的 `HEADROOM_RUN {…}` 与退出码入档 | 超时/断线/非 0 → 记 UNKNOWN，进 S6，**不重跑** | `<EV>/anchor.json`、`<EV>/dispatch.log` |
| S6 | **独立只读读回** | 另一条会话；release/服务/存储/RF safe 与 S4 一致；账本出现本次 nonce 消耗；`dispatch/<ATTEMPT_ID>.json` 存在；`run-gate.json` 与 dispatch.log 一致 | UNKNOWN → 只读定案；不得重派 | `<EV>/readback.log`、`<EV>/download.log`、`<EV>/board-download/` |

关键常量（写死，无 CLI 开关）：接线 12 h / 未来 60 s；锚龄 120 s / 未来 60 s；授权尝试数 5；
门禁顺序 G0→G1→G2→G3→G4→G5-G8。

退出码（`scripts/rf_headroom_research_run.py:221-223,1582-1594`；完整表见
`evidence/rf-teaching/s2-dispatch-prep-2026-10-17/exit-codes.md`）：**0** = `RUN_COMPLETED`（唯一）；
**2** = 8 个固定标签（`RUN_NOT_STARTED_PREFLIGHT`、`RUN_REFUSED_CLOCK_ANCHOR`、
`RUN_REFUSED_CAPABILITY`、`RUN_REFUSED_BUNDLE`、`RUN_REFUSED_IMPORT`、
`RUN_REFUSED_AUTHORIZATION`、`RUN_ABORTED_STORAGE_FAULT`、`RUN_ABORTED_PREFLIGHT_UNKNOWN`）；
**3** = 其余 7 个固定标签 + 全部 `RUN_STOPPED_*`。**注意：2 不等于"零尝试"**——运行后存储故障
同样是 `RUN_ABORTED_STORAGE_FAULT` → 2（`:1533-1534`）。

## 3. 硬停止条件（不可协商）

1. 身份漂移（release/合同/冻结模块摘要任一不符）；
2. 任何 UNKNOWN（`RUN_UNKNOWN_*`、安装 UNKNOWN、结果不可判定）；
3. 任何门禁失败（含存储故障、服务异常、RF 非 safe、**账本已含本次 attempt_id**、token 缺失）；
   注意"ledger 非空"本身**不是**停止条件——账本是追加式的，历史行是常态（`:636-640`）；
4. 时钟锚不成立（缺失/boot_id 不同/age>120 s/未来 >60 s）；
5. 接线超窗或不在位；
6. 超时/断线/无响应。

停止后唯一允许的动作：**只读读回 + 证据归档 + 报告**。

## 4. 终态 → 动作

| 终态 | 退出码 | 允许的动作 |
|---|---|---|
| `RUN_COMPLETED` | 0 | 进入独立 TRAIN/VAL 复验；**不得**当作 S2 正式 108+12 验收 |
| `RUN_REFUSED_BUNDLE` / `RUN_REFUSED_IMPORT` | 2 | 停止；修正后走**新授权 + 新 attempt_id** |
| `RUN_REFUSED_AUTHORIZATION` / `RUN_NOT_STARTED_PREFLIGHT` | 2 | 停止；nonce 三态：未消耗→可同 nonce 重校；已消耗→新 nonce；**未知（`consumption_status='UNKNOWN'`）→ 永不复用** |
| `RUN_REFUSED_CLOCK_ANCHOR` | 2 | 停止；重取锚并**换新 attempt_id** |
| `RUN_REFUSED_CAPABILITY` | 2 | 停止；记录能力宣告原文；不得脚本旁路 |
| `RUN_ABORTED_STORAGE_FAULT` / `RUN_ABORTED_PREFLIGHT_UNKNOWN` | 2 | 停止；先做存储健康与离线维护；**不得据退出码 2 断言"零尝试"**（运行后存储故障也是此标签） |
| `RUN_UNKNOWN_DISCONNECT` / `RUN_UNKNOWN_REBOOT` / `RUN_UNKNOWN_STORAGE_UNREADABLE` / `RUN_UNKNOWN_READBACK_INCOMPLETE` | 3 | 保留原始证据；只读读回定案；**不得重派** |
| `RUN_INTERRUPTED_MID_ATTEMPT` | 3 | 保留原证据；按未知处理，不重派 |
| `RUN_EVIDENCE_WRITE_FAILED` | 3 | 停止；先修证据落点；**不得**用退出码推断研究结论 |
| `RUN_STOPPED_<原因>` / `RUN_STOPPED_CANCELLED` | 3 | 保留原 IQ/拒绝原文；按既有 abort + closing readback 收尾 |

**注意（三处精确化，均有源码行号）**：

1. `/var/lib/rf-headroom-research/dispatch/<attempt_id>.json` 在 G0 **之前**以 `O_EXCL` 落盘
   （`:608-629`、`:2638`、`:2688-2690`），路径是 `Path(--ledger-dir).parent / 'dispatch'`
   ——**不是** `ledger/dispatch/`；包复核失败/版本不符的拒绝也会留下标记（`:2672-2687`），
   这类拒绝**确实**烧掉 attempt_id。
2. 但**不是"任何拒绝都烧"**：无 `bundle-receipt.json`（`:2637-2646`）、attempt_id 非法
   （`:2647-2651`）、板端收到 `--ledger`（`:2662-2668`）发生在写标记之前，不留标记；
   判定只能查文件是否存在。
3. **nonce 只在账本写入时消耗**（G0 内最后一步：`:1824` → `:1860`），且追加/fsync 失败时
   消耗结果记 `UNKNOWN`（`:1863-1871`）——此时永不复用该 nonce，也不能用"G0 拒绝"推断它没被消耗。
   完整规则见 `evidence/rf-teaching/s2-dispatch-prep-2026-10-17/{ledger-rules,dispatch-marker,exit-codes}.md`。

## 5. 明令禁止

1. 自造旁路：`--force` / `--retry` / `--attempts` / `--attenuation-mdb` /
   `--ignore-wiring-age` / `--skip-preflight` / `--anchor-age` / `--anchor-tolerance` /
   `--allow-board-clock`（都不存在）；
2. 复用授权工件或 nonce、把 `single_use` 改成假、手改时间戳；
3. 断线/重启/超时后自动续跑或"补一次"；
4. 跳过 S6 独立只读读回，或用同一条连接自证；
5. 把本次会话当 S2 正式验收或真人验收；
6. 读/写/记录 token 的值与摘要（`token_ref` 只写路径 `/etc/rf-control.token`）。

## 6. 证据清单（缺一即未完成）

- 运行包 receipt + 板端逐成员 `sha256sum`；安装 receipt + `attempt.jsonl` + 读回件；
- `wiring-confirmation.json`（4 字段）+ 其 SHA-256（授权里作 `artifact_digest`）；
- `authorization.json`（13 字段）+ `attempt-id.json` + 离线验真日志（**token 值不入档**）；
- `anchor.json`（host_utc / boot_id / uptime / age_check）+ `dispatch.log`（原始 stdout 逐字 + 退出码）；
- S4/S6 两次只读读回原文（含完整 dmesg）；
- 板端 `/var/lib/rf-headroom-research/ledger/authorization.jsonl`、
  `/var/lib/rf-headroom-research/dispatch/<attempt_id>.json`（与 `ledger/` 同级）、
  `.../evidence/<session_id>/`（`run-gate.json` + 每帧原始 IQ）、`.../observation/`；
- 新收据（绑定上述全部摘要）+ **未参与实现者**的复核结论。

## 7. 本 runbook 未验证的部分

1. 所有命令**本次均未执行**；封存产物尚不存在；`aba80e92…` **未安装、未派发**（安装已于 2026-10-17T12:26 完成，见 §1 表注；**派发仍未发生**）； 【**部分过期（截至 2026-10-17）**：封存三件产物现已存在（见 §1 表）；`aba80e92…` 仍未安装、未派发；安装/派发类命令本次仍未执行】
2. `runner_version=1.0.2` + `aba80e92…` 的组合**零真实派发**（唯一带锚的实测会话是
   2026-10-15 的 runner 1.0.1 + `b82b141e…`）；
3. 现存运行包 receipt 的 `source_commit` 已过期，必须重出；
4. 安装包 `prepare` 在 `aba80e92…` 上的顺序约束（development-checkpoint → prepare → 【**部分过期（截至 2026-10-17）**：development-checkpoint → prepare → seal 三步**已执行**（`install-bundle` 的 `checkpoint_sha256` = `4f53f342…`，`verify_bundle` 通过）；仅 **install** 一步未执行】
   seal → install）**未执行过**；
5. 五帧是否全部通过新谓词**未知**；首帧之外的四个真实帧从未被新谓词消费；
6. 主机 UTC 的可信来源与单调性**未证明**（残余风险，见
   `docs/reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md` §5）；
7. 板钟路径（继续用主机锚 vs 另行授权校时）**由用户决策**，见
   `evidence/rf-teaching/s2-dispatch-prep-2026-10-17/board-clock-decision.md`。

## 8. 关联文件

- 可执行 runbook：`evidence/rf-teaching/s2-dispatch-prep-2026-10-17/runbook.md`
- 字段/时钟/接线/attempt_id：同目录 `authorization-template.json`、`clock-anchor.md`、
  `wiring-freshness.md`、`attempt-id.md`、`board-clock-decision.md`、`README.md`
- 上一版执行包（b82 时代，含 2026-10-15 实测教训）：
  `docs/superpowers/plans/2026-10-14-headroom-physical-session-execution-package.md`
- runner 设计：`docs/superpowers/plans/2026-10-12-real-headroom-research-runner-design.md`
- 板端运行包规范：`docs/superpowers/specs/2026-10-13-headroom-board-run-package-design.md`
