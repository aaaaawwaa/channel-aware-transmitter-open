# HEADROOM 单次安全安装执行报告（2026-10-13）

## 0. 结论

**单次安全安装成功，并经独立读回确认为 `VERIFIED_SAFE_NEW_RELEASE`。**

- 板卡发布由 `b67a8aaab0c5904822580c0647b96fd0` 变为
  **`b82b141e355b4cb1833969383a409c79`**；`current` 与 `active-release` 均为新发布。
- 安装**只派发一次**（`attempt_id 03b1e74823634dff914466994878ec6c`），无重试、无重装、
  无手工绕过；`retry_allowed=false`。
- 未布防、未发射、未采集；HEADROOM 仍 `OFFLINE`、`used/active=false`、预算 1/5 未消耗；
  新物理帧 **0**、真实研究初始化 **0**。
- 安装前后 `boot_id` 均为 `d85fa469-aeb7-4ef9-860b-15389314ef30`（**未重启**）；
  内核存储故障列表安装前后均为 `[]`。

前提：本阶段先修复了使安装器无法完成验收的阻塞缺陷 **N2**，并由**未参与该修补的独立 agent**
出具关键审查、重新封存后才派发（§3）。

## 1. 派发前门禁（逐项）

| 门禁 | 结果 |
|---|---|
| `seal-stage.py --verify` | **exit 0**（重新封存后；详见 §3） |
| `verify_bundle(install-bundle)` | 通过：`b82b141e…` ← 父 `b67…`、契约 `cnn-bpsk-headroom-v1`、四 payload |
| 完整快照（`SNAPSHOT`+`validate_snapshot`） | `current==active==b67…`、双服务 active/running/NRestarts0、root `rw`、内核存储 15,097 B 完整读取、故障 `[]` |
| 只读 RF 状态 | `verified=True`、`safe=True`、`io_ok`、`armed=False`、TX 未选、RX 未布防 |
| 验收工具可用性 | N2 修复后守卫可执行（`readback_can_conclude=True`） |

收据：`preflight-receipt.json`（`3e6145ab13285320…`）；首次预检在被拒守卫处中断的原始收据
`preflight-refusal-before-fix.json` 保留。

## 2. 单次派发与执行结果

```text
python -B scripts/rf_headroom_deploy.py install \
  --bundle evidence/rf-teaching/s2/executable-candidate-2026-10-12/install-bundle \
  --output evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1      # exit 0
```

| 事实 | 值 |
|---|---|
| `outcome` / `board_state` | `INSTALLED_VERIFIED` / `VERIFIED_SAFE_NEW_RELEASE` |
| `installed` / `retry_allowed` | True / False |
| 派发 ACK 退出码 | 0（`dispatch_exit_code=0`，`install_timeout=false`） |
| 持久任务（`remote-job.json`） | `phase=FINISHED`、`execution_result=RETURNED`、`exit_code=0`、`retry_allowed=false` |
| 任务与观测 boot id | `d85fa469…` == `d85fa469…`（一致） |
| 任务绑定 | release/parent/payload 与收据逐字段相等 |
| 阶段 | 13 条 `attempt.jsonl`，`attempt_started` → `attempt_finished` |
| 存储故障 | 安装前 `[]` → 安装后 `[]` |
| 服务（安装后） | `pynq-status` PID 5908、`rf-daemon` PID 5921，均 active/running、`NRestarts=0` |

板端暂存目录 `/tmp/rf-teaching-install-d56cc3cbddd949f5ac09df3521ed9434`（未清理，仅记录）。

## 3. 前置：N2 修补 + 独立审查 + 重新封存

1. **N2**：`readback_state()` 的摘要守卫缺一个右括号，板端执行即 `SyntaxError`，守卫判非零 →
   读回抛错 → 安装器**永远无法**得出 `VERIFIED_SAFE_NEW_RELEASE`（成功安装也会被记为
   `UNKNOWN_READBACK_INCOMPLETE`）。冻结套件漏掉它的原因是假板卡对任何含 `hashlib` 的
   命令一律返回 0（比真板宽容）。
2. **修补**：工具 3 行（补右括号 + 2 行注释，`d6d0678f…` → `2f3094b5…`）；
   测试 4 处改动 `+131/−2`（忠实假板 `board_guard_result()` + 一项回归用例，
   `afba2e3d…` → `f9754a82…`）。
3. **红绿**（把修补回退后）：`7 failed / 11 passed`；修复后 `18 passed`；
   `scripts/tests` 全量 `489 passed / 1 skipped`；native lease `16 passed`；
   四套件 `155 passed`。
4. **独立关键审查**（由**未参与修补**的另一 agent 完成）：判 **APPROVED**，
   自算全部绑定摘要（46 软件、harness、qualifier、runtime runner、6 项 REVIEW_TOOLS）、
   自重建封存字节并证明测试改动恰为 `+131/−2`、自跑红绿、
   并通过 15/15 字段与**真实** `qualify-runtime.check_chain_reviews` 调用
   （返回 `{"critical-review": {report_sha256 8badf9c9…, binding_sha256 2bb8394f…}}`）。
5. **重新封存**：`source-freeze.json` 两处摘要（逐字节更新，46 软件/58 原生不动）→
   `4ad384e5945740af…`；重生成 `stage-checkpoint.json` `cd22f70e…` →
   **`72e1fcd72ad645e7…`**；`seal-stage.py --verify` **exit 0**。
6. **轻路径披露**：`development-checkpoint.json`（`b7be8579…`）与 `install-bundle/`
   **逐字节未变**（bundle 仍通过 `verify_bundle`），因此它是**预修补的历史记录**，
   现行修订以 `stage-checkpoint.json` + `source-freeze.json` 为准。
   旧审查件已逐字节归档至 `pre-fix/python-critical-review.{md,json}`（`88d74459…`/`786a2549…`），
   旧 `stage-checkpoint.json` 归档为 `pre-fix/stage-checkpoint.pre-fix.json`。

## 4. 独立验收（不只凭派发 ACK）

| 检查 | 结果 |
|---|---|
| 安装器自身读回（`readback-state-new.json`） | `verified=true`、`safe=true`、`release_id=b82…`、exit 0 |
| **复审者独立读回**（`accept.py`，另一独立连接 + 自己的守卫实现） | **8/8 通过**，verdict **`VERIFIED_SAFE_NEW_RELEASE`** |
| 逐项 | `snapshot_valid`、`current_is_expected`、`active_matches_current`、`services_running`、`no_storage_faults`、`release_verified`、`rf_safe`、`guard_is_a_program` 全为 true |
| 板端只读状态 | 发布 `b82…`、`safe=true`、online/非 stale、`io_ok`、`armed=false`、TX 未选、RX 未布防 |
| HEADROOM | `state=OFFLINE`、`used/active/armed/tx_active/rx_busy=false`、`lease_held=true`（读连接自有租约）、预算 1/5、daemon 1.0.4/minor6 |
| 前后快照 | `b67…` → `b82…`；服务重启后仍 `NRestarts=0`；存储故障 `[]` → `[]` |

## 5. 交付物

**一次安装尝试目录** `evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/`：

- 安装器产物（**10/10 与 `install-receipt.json` 的 `evidence_sha256` 逐字节相符**）：
  `attempt.jsonl`、`install-receipt.json`、`remote-job.json`、`dispatch.log`、
  `payload-guard.log`、`job-observation.log`、`snapshot-pre.log`、`snapshot-post.log`、
  `readback-guard-new.log`、`readback-state-new.{json,log}`
- 复审者独立验收：`acceptance-receipt-b82b141e.json`、`acceptance-state-b82b141e.json`、
  `acceptance-snapshot.log`、`acceptance-guard.log`、`acceptance-state.log`、
  `independent-remote-{job.json,phases.jsonl,install.log}`

关键摘要：`install-receipt.json` `9dfb0adeef055157…`；`remote-job.json` `b3441d62f6d5373a…`；
`attempt.jsonl` `dc2ee6fab16d7086…`；`readback-state-new.json` `e15f5e4b87d7a263…`；
`acceptance-receipt-b82b141e.json` `c0c32c7c3346ad7d…`；
`acceptance-state-b82b141e.json` `64d88c55eaa0d827…`。

## 6. 过程记录（如实）

1. 首次预检在**被拒的守卫**处中断（当时的守卫是坏的）：原始收据保留为
   `preflight-refusal-before-fix.json`，并促成 N2 定位。
2. 复审者第一次运行独立验收脚本时，文件名与安装器产物 `remote-job.json` **重名并将其覆盖**；
   已按 `job-observation.log` 中的 `HEADROOM_JOB` 记录**逐字节还原**
   （digest `b3441d62…` 与收据一致），脚本改名 `independent-remote-*` 后重跑，
   10/10 绑定件复验 MATCH。
3. **F1（非阻塞，独立审查发现）**：修补的文字/断言写“非程序使 python3 退出 2”，
   实测 CPython 3.8.6 为 **1**（2 是用法错误码）。行为与绑定均不受影响（守卫只判真假）。
   **本报告的措辞已更正**；测试内的 `== 2` 断言因“不得再动已审查字节”留待下一个 delta 修正。
4. `/tmp` 下本阶段新增的目录（安装暂存、读回、预检、验收）**保留未删**，仅作记录。

## 7. 未做 / NOT_RUN

布防、发射、采集、真实 46 dB 研究、runner 接真实板卡传输、正式 S2（108 物理 + 12 离线）、
真人验收、S3、S4；MMC/SDHCI 根因 `NOT_ESTABLISHED`；完整文件系统检查/修复 `NOT_RUN`。
旧 b67 安装 `UNKNOWN` 记录继续保留。

## 8. 下一审核门

1. **给 runner 接真实传输**：实现 `storage_probe/identity_probe/readback_probe/token_reader`，
   复用唯一控制链；**新工厂必须声明 `performs_board_read`**；并做离线故障注入
   （断线、重启、畸形快照、一次性 nonce、单次初始化/最多五次尝试）。
2. **F02/F11 冻结套件覆盖缺口**：补强或给出独立可审查的限制说明（本阶段未改冻结文件）。
3. 满足**人工接线记录 + 一次性 token** 后，才能进入一次会话最多五尝试的有限物理研究；
   正式 S2/S3/S4 仍在其后。
