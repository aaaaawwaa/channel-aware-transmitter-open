# HEADROOM 单次物理会话执行包（人工接线 + 一次性授权）

日期：2026-10-14。**本文件是操作说明，不是授权**：它不连板、不改代码、不产生任何动作；真正的动作只由下面的人工签字工件 + 操作者现场执行承载。适用前提是**离线全绿 + 独立 delta 审查 APPROVED + 只读真板预检通过**，三者均已在 2026-10-14 达成（见 §1 收据）。

## 1. 前置条件与现状

| 前置 | 现状 | 证据 |
|---|---|---|
| 运行包冻结（源码提交 + 包摘要） | 已满足 | `evidence/rf-teaching/p1-run-bundle-2026-10-14/stage-checkpoint.json`（`--verify` 通过），commit `00768b5` |
| 离线：冻结红态判据 12 passed、`scripts/tests` 508 passed | 已满足 | `evidence/rf-teaching/p1-run-bundle-green-2026-10-14/` |
| 变异 5/5 被抓 + 独立审查 | **APPROVED** | `evidence/rf-teaching/p1-run-bundle-review-2026-10-14/REVIEW.md` |
| 只读真板预检（上传摘要、B0/B1、具名拒绝、零 `/tmp` 外写入） | 已满足 | `evidence/rf-teaching/p1-run-bundle-board-preflight-2026-10-14/preflight-receipt.json` |
| 候选 `b82b141e355b4cb1833969383a409c79` 已安装且安全 | 已确认 | 同上：`current`/`active-release` 一致、双服务 active、`NRestarts=0`、RF 未布防 |
| **人工接线 TX1→RX1（本次会话专用确认）** | **待人工** | §3.1 |
| **一次性授权工件 + 操作者 token** | **待人工** | §3.2 / §3.3 |
| 存储健康 | 预检无 MMC/SDHCI/ADMA/I-O/ext4 故障；仅 FAT 脏挂载状态位（已知项） | 预检日志 |
| 板端 RTC | **无效**（2021-12-03）：不得用于排序事件，以主机 UTC + `boot_id`/uptime 为准 | 预检日志 |

## 2. 三条硬约束（任何一条被违反即停止并保留证据）

1. **单次**：一次授权 = 一次会话 = 最多 5 次尝试、1 次初始化；断线、重启、超时都**不重试、不重放、不续跑**。
2. **人工在位**：接线由人确认（`TX1->RX1 physical path present`），确认超 12 小时即失效（无 `--ignore-wiring-age`）。
3. **未知就是未知**：任何 `RUN_UNKNOWN_*` 一律保留原始证据、停止操作，由独立只读读回定案，不得解释为成功或自动回滚。

## 3. 人工前置工件

### 3.1 接线确认记录 `wiring-confirmation.json`（人先写）

| 字段 | 类型/取值 | 校验 |
|---|---|---|
| `confirmed_by` | 非空字符串，≤128 字符 | 必须写明确认人 |
| `confirmed_at` | ISO8601 **带时区**（如 `2026-10-15T02:30:00+08:00` 或 `…Z`） | 距运行时刻 ≤ 12 h；不得早于未来 60 s 以上 |
| `statement` | **必须逐字** `TX1->RX1 physical path present` | 固定审查语句，无替代 |
| `method` | 非空字符串，≤512 字符 | 描述如何确认（目视同轴/衰减器/端口标注等） |
| `artifact_digest` | 64 位小写 hex | **这份接线确认记录文件本身**的 SHA-256（先写文件、再取摘要写入授权工件） |

### 3.2 一次性授权工件 `authorization.json`（人签字生成）

`AUTHORIZATION_FIELDS`（**13 项**，**不得多、不得少**，未知字段会被拒绝；2026-10-15 实测更正：旧文误写 10 项，生产校验器 `rf_headroom_research_run.load_authorization` 实际要求恰好 13 个字段名）：

| 字段 | 取值 | 说明 |
|---|---|---|
| `authorization_id` | 32 位小写 hex 或标准 UUID | 本次授权的唯一编号 |
| `nonce` | 16..128 位小写 hex | 一次性；板端账本出现即拒 |
| `session_id` | 32 位小写 hex | 会话号（可与 `--session-id` 一致） |
| `expected_release` | `b82b141e355b4cb1833969383a409c79` | 必须是已安装且已资格的发布 |
| `software_contract` | `cnn-bpsk-headroom-v1` | 固定 |
| `daemon_version` | `1.0.4` | 固定 |
| `protocol_minor` | 整数 `6` | 固定 |
| `issued_at` / `expires_at` | ISO8601 带时区 | `expires_at > issued_at`；运行时须落在窗口内；未生效即拒 |
| `single_use` | 必须 `true` | 布尔 `true`，不是字符串 |
| `wiring` | §3.1 的记录（内嵌同一份字段） | 缺字段/多字段/语句不符/超期即拒 |
| `token_ref` | 非空字符串，≤4096 | 指向 §3.3 的 token 路径；**不得包含 token 值本身** |
| `authorized_attempts` | 必须整数 `5` | 其它值（含 0/6）即拒 |

**主机制备与离线校验**（不连板，只校验工件本身）：

```bash
# 1) 生成三件套（示例：hex32 编号 / 96 位 nonce / 32 位会话号）
python -B -c "import secrets,json;print(secrets.token_hex(16), secrets.token_hex(12), secrets.token_hex(16))"
# 2) 写 wiring-confirmation.json -> 取 sha256 填入 authorization.json 的 wiring.artifact_digest
# 3) 主机侧离线校验（不连板、不消耗 nonce）：
python -B -c "import datetime,sys; sys.path.insert(0,'.'); from scripts.rf_headroom_research_run import load_authorization; print(load_authorization('authorization.json', datetime.datetime.now(datetime.timezone.utc)))"
```

校验通过才允许进入 §4；失败即修正工件后重校（此时尚未接触板卡）。

### 3.3 操作者 token 文件

- 由操作者在板端session 现场放置（例如 `/root/rf-headroom-operator.token`，`chmod 600 root:root`）；
- 只经 `read_control_token` → `headroom_arm` 一条路进入系统；**token 值与摘要都不得写入任何证据文件**；
- `token_ref` 只写路径。

## 4. 执行步骤（不可跳步）

### S0 主机制备（无板卡）
1. 按 §3 生成并离线校验收权工件；记录三件套 SHA-256。
2. 用最终提交重新打包（`build_run_bundle`），主机侧 `verify_run_bundle` 通过，记录 `bundle-receipt.json` 摘要。

### S1 板端只读预检（写 `/tmp`，不消耗授权）
3. 上传运行包到 `/tmp/rf-headroom-research-<attempt_id>/`（前缀**不得**复用 `/tmp/rf-teaching-install-*`），板端复核逐成员 SHA-256；
4. 读回：`current`/`active-release`、双服务与 `NRestarts`、完整 dmesg（存储筛选）、`/` 与 `/boot` 挂载、RF safe（未布防/未选 TX/未武装 RX）、`boot_id`、新物理帧计数基线。**任一项失败即停止，不消耗授权。**

### S2 单次派发（板端 root，唯一一次）
5. 执行（把 `<…>` 换成本次的实际值）：

```bash
cd /tmp/rf-headroom-research-<attempt_id>/bundle && \
  sudo python3 -B scripts/rf_headroom_research_run.py run \
  --authorization /tmp/rf-headroom-research-<attempt_id>/authorization.json \
  --token-path /root/rf-headroom-operator.token \
  --wiring-confirmed 'TX1->RX1 physical path present' \
  --attempt-id <attempt_id> --runner-version 1.0.0 \
  --ledger-dir /var/lib/rf-headroom-research/ledger \
  --observation-dir /var/lib/rf-headroom-research/observation \
  --evidence-root /var/lib/rf-headroom-research/evidence
```

6. 记录 stdout 的 `HEADROOM_RUN {…}` 一行与退出码；**不要**因为超时/断线重跑。

### S3 独立只读读回（另一条只读会话）
7. 读回 release/服务/存储/RF safe/证据目录清单/新物理帧计数；与 S1 基线对比；把 `run-gate.json`、账本、`dispatch` 标记与下载的证据对齐。

### S4 封存
8. 下载 `/var/lib/rf-headroom-research/` 下本次会话目录 → 写入新收据（绑定 runner/包/授权/接线/预检/读回摘要）→ 由**未参与实现者**复核。

## 5. 终态 → 动作

| 终态 | 含义 | 允许的动作 |
|---|---|---|
| `RUN_REFUSED_BUNDLE` / `RUN_REFUSED_IMPORT` | 包或发布身份不符 | **停止**；修正后走**新**授权（本次未消耗 nonce 时不需换） |
| `RUN_REFUSED_AUTHORIZATION` / `RUN_NOT_STARTED_PREFLIGHT` | 授权/前置门拒绝 | **停止**；修正工件（未消耗即同一 nonce 可重校；已消耗必须新 nonce） |
| `RUN_REFUSED_CAPABILITY` | 实时能力为假 | 停止；记录能力宣告原文；不得脚本旁路 |
| `RUN_ABORTED_STORAGE_FAULT` / `RUN_ABORTED_PREFLIGHT_UNKNOWN` | 存储故障或预检输出不可用 | 停止；先做存储健康与离线维护 |
| `RUN_UNKNOWN_READBACK_INCOMPLETE` / `RUN_UNKNOWN_REBOOT` | 研究已执行但读回不完整 / 板卡重启 | 保留原始证据；只读读回定案；**不得重派** |
| `RUN_STOPPED_CANCELLED` | 操作者取消 | 保留证据；按既有 abort + closing readback 收尾 |
| 正常完成（5 次尝试、1 次初始化、终态 OFF） | 成功 | 进入独立 TRAIN/VAL 复验（不得直接进正式 S2） |

## 6. 明令禁止

1. 不得使用 `--force` / `--retry` / `--attempts` / `--attenuation-mdb` / `--ignore-wiring-age` / `--skip-preflight`（它们不存在，出现即是自造旁路）；
2. 不得手工编辑或复用授权工件、不得复用 nonce、不得把 `single_use` 改成假；
3. 不得在断线/重启后自动续跑或"补一次"；
4. 不得跳过 S3 独立只读读回，不得用同一连接自证；
5. 不得把本次会话的结论当作 S2 正式 108+12 的验收。

## 7. 需要收集的证据（缺一即未完成）

- `authorization.json`、`wiring-confirmation.json`、三件套 SHA-256（**token 值不入档**）；
- `bundle-receipt.json` + 板端逐成员 `sha256sum` 输出；
- `HEADROOM_RUN {…}` 原始 stdout 与退出码；
- `/var/lib/rf-headroom-research/ledger/authorization.jsonl`、`dispatch/<attempt_id>.json`、`evidence/<session_id>/`（含 `run-gate.json` 与每帧原始 IQ）；
- S1/S3 两次只读读回原文（含完整 dmesg）；
- 新收据（绑定上述全部摘要）与独立复核结论。

## 8. 时间提示

单次会话本身预计 **≤10 分钟**（不含接线与撤离）；最耗时的是证据下载与独立复核。**不需要**重新构建位流或 ARM 产物：运行包是纯 Python，发布侧已有资格收据。

## 9. 本文件不做什么

不生成真实 nonce、不放置 token、不连板、不安装、不布防、不发射、不采集；工件模板见 `evidence/rf-teaching/headroom-authorization-template-2026-10-14/`（占位符，直接使用会被 runner 拒绝）。
## 10. 2026-10-15 单次派发实测发现（板卡时钟阻塞 G0） 【**已过期（截至 2026-10-17 的实际状态）**：该阻塞已解除并已实测——主机 UTC + `boot_id`/`uptime` 时钟锚已实现并经独立复审（提交 `5d2906a`）；2026-10-15 第二次派发 G0 已通过（锚龄 1.650 s、`board_clock_used=false`）。本节作为历史记录仍真实，但“仍被阻塞/需要二选一”的现状描述已过时。再次派发仍需**新授权 + 新 nonce + 新 attempt_id + 新接线确认**（旧 nonce 已消耗、旧窗口已过期）】

唯一一次 46 dB 派发被运行器具名拒绝在 **G0**：终态 `RUN_NOT_STARTED_PREFLIGHT`、原因 `the authorization is not valid yet`、exit 2；**零尝试/零初始化/零物理帧/nonce 未消耗**、未重试。根因：运行器板端入口用**板卡自身时钟**判定授权窗口，而板卡 RTC 无效（2021-12-03）→ 任何诚实的 2026 时间戳授权必被拒。这与合同 §8「板端 RTC 不得用于排序事件、以主机 UTC + `boot_id`/`uptime` 为准」**不一致**，属实现缺口。 【**已过期**：截至 2026-10-17——该实现缺口已闭合（时钟锚已实现并经独立复审），2026-10-15 第二次派发 G0 通过、`board_clock_used=false`。本段作为 2026-10-15 当时的根因记录保留】

另两处实测更正：① `AUTHORIZATION_FIELDS` 实为 **13** 项（见 §3.2）；② 接线确认记录**不能包含自身的 sha256**——文件内放 4 个人字段，第 5 个受审字段 `artifact_digest`（=该文件 sha256）记在授权工件里。

解除阻塞需要二选一（都需新的受审变更或单独授权，**不得**把授权写成 2021 时间戳）：(a) 让板端入口用可信时钟锚（主机经会话注入 UTC 并绑定 `boot_id`/`uptime`）；(b) 作为单独授权的维护动作校正板卡时钟（板卡无 rtc0）。再次派发还需要**新的 attempt_id**。 【**已过期（截至 2026-10-17 的实际状态）**：该“二选一”已由 (a) 完成并已实测通过（时钟锚已实现、经独立复审；第二次派发 G0 通过、`board_clock_used=false`），不需要 (b) 的校时授权，也绝不把授权写成 2021 时间戳。再次派发仍需**新授权 + 新 nonce + 新 `attempt_id` + 新接线确认**（旧 nonce 已消耗）】
