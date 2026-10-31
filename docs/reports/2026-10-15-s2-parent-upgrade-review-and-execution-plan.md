# S2 环境候选部署路径复核与执行方案

核验时间：2026-10-15 15:02 +08:00。证据基准：用户提供的离线导出 `<LOCAL_PATH>

## 1. 结论

**推荐方案 a，但必须实施为“带证据约束的跨合同回退父支持”，不能直接删掉合同门。** 目标候选 `399b300e59b8d00e41d43c8123884c7f` 是 `cnn-bpsk-environment-v1`，构建父为 S1 `c3bf915c…`；板上实际安装、且安装失败时必须回退到的发布是 `b82b141e…`，合同 `cnn-bpsk-headroom-v1`。这两个“父”承担不同职责，不应混用。

离线日志 14:54–14:56 的新证据已经使上次报告的 B1（缺精确候选 ARM 资格）关闭。现在生产 `verify_gates` 的 ARM 段通过；**唯一仍阻止 `prepare` 的是 B2：回退父 b82 的合同与安装证据形状不被当前部署工具接受。** 不显式绑定父时，`verify_gates` 虽通过却默认打包 `parent_release=c3bf915c…`；板上 `current=b82b141e…`，安装脚本会在写入前拒绝。不能以这个“通过”来宣称包可安装。

方案 b（先安装旧 environment 发布）增加一次板卡安装及回退风险，而且同样需要先解决当前父发布的证据门；方案 c（改 `rf_teaching_install.sh`）目前没有必要：该脚本只要求传入父 ID 等于板上 `current`，没有按父合同拒绝 headroom。应先只改部署工具的父发布验证与证据绑定。

## 2. 已复核事实

| 事项 | 复核结果 | 证据 |
|---|---|---|
| 离线导出时点 | 14:58 导出，主会话 turn 14 已完成 | ZIP `session.v4.jsonl` 最末 `turn/end` |
| 候选身份 | release `399b300e…`，环境合同，软件 44 项；`verify_release(require_software=True)` 通过 | `evidence/rf-teaching/s2-candidate-environment-2026-10-15/` |
| ARM 资格 | 精确 release/software 44/44；`passed=true`、exit 0、原生 CNN、同帧差异空；100 次 P95 258.6409 ms ≤300；RSS 31,428 KiB（记录值，部署门未强制） | `evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json`、外层 `receipt.json`；提交 `72952f5` |
| ARM 门实跑 | `verify_gates(candidate, closure, arm)` 通过，但返回默认父 c3；显式 `parent_candidate=b82` 加现有 acceptance 收据时返回 `ValueError: S2 rollback parent installation unverified` | 本次只读调用 `scripts.rf_environment_deploy.verify_gates`；`scripts/rf_environment_deploy.py:42–111` |
| 板卡最后读回 | b82 仍为 `current` 与 `active-release`；两服务 active/running、无重启；RF 未布防、TX/RX 关闭；ARM 轮未改板上发布 | ARM 轮 `preflight.log`、`readback.log`；本次未重访板卡，执行前仍需新预检 |
| 安装额度 | 先前授权的单次安装尝试未消耗；安装轮只跑了 `prepare`，未生成 bundle、未调用 `install` | `evidence/rf-teaching/s2-environment-install-2026-10-15/receipt.json` |
| b82 安装原证据 | 本仓 `acceptance-receipt-b82b141e.json` 记录原安装收据 SHA-256 `9dfb0ade…`；兄弟工作区该原件摘要相等，且其 `evidence_sha256.readback-state-new.json` 与同目录原文件相等。用本仓 `verified_state` 检查读回返回 `true` | 本仓 `evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json`；只读源 `<LOCAL_PATH>` |
| 候选二进制 | bit/HWH/`rf_daemon`/`rf_cnn_conv.so` 在当前目录存在，但未被 Git 跟踪 | 当前候选目录实物与 `git ls-files`；不能把提交 `72952f5` 当成完整可移植安装包 |

这里的“ARM PASS”只证明保存 IQ 的隔离 ARM 计算、身份绑定和预算；不证明候选已安装、开发 pilot 已运行、正式 108 帧或真人验收。板卡最后安全状态是**历史读回**，也不能代替安装当天的新预检。

## 3. B2 的精确原因

1. `rf_environment_deploy.verify_gates` 对**目标**合同 `cnn-bpsk-environment-v1` 的限制应保持。其父发布分支又要求 `parent.software_contract == CONTRACT`、安装收据 `installed=true / release_id / board_state=VERIFIED_SAFE_NEW_RELEASE`，并查同目录 `post-state.json` 的哈希及 `verified_state`。
2. b82 目标目录的合同是 `cnn-bpsk-headroom-v1`。当前本仓的 `acceptance-receipt-b82b141e.json` 是 `headroom-install-acceptance-v1`，不具备上述安装器收据字段，也没有同目录 `post-state.json`；把它直接传给 `--parent-install` 必然失败。
3. 兄弟工作区存在 b82 **原安装器收据** `install-receipt.json` 和哈希绑定的 `readback-state-new.json`。后者的数据形状可由现有 `verified_state` 验证。它们应按原字节导入并记录来源，**不得改名为伪造的 `post-state.json` 或改写字段**。
4. `rf_teaching_install.sh:12` 的 `readlink -f current == releases/<parent>` 是正确的运行时竞态防护。改动部署门后仍应保留，并要求安装前新只读预检再次确认 `current=b82`。

## 4. 下一位 agent 的任务边界

**任务目标：** 让生产 `prepare` 和 `verify_bundle` 接受“目标 399b 环境候选、实际回退父 b82 HEADROOM”的**真实证据组合**，同时保留目标、ARM、S1 授权、安装状态与当前板卡 ID 的拒绝门。先完成离线改动和独立复审；不在该代码任务中执行安装或 RF pilot。

**建议拥有的文件：** `scripts/rf_environment_deploy.py`、`scripts/tests/test_rf_environment_deploy.py`，以及新建的 b82 父证据导入目录与来源说明。不要改候选的 44 项软件、`rf_environment_arm_check.py`、板上 b82、`rf_teaching_install.sh`、原始 ARM/安装收据。若另有并发写者，先检查 Git 状态和路径所有权，不能覆盖他人的改动。

### 阶段 A：原证据封存（只读来源、写入本工作区）

1. 从兄弟工作区**逐字节复制** b82 的 `install-receipt.json` 与 `readback-state-new.json` 到新证据目录；保存来源绝对路径、复制时间、两个源文件与副本的 SHA-256。保留本仓现有 `acceptance-receipt-b82b141e.json` 为独立旁证。
2. 核对 acceptance 中 `expected_release=b82`、`accepted=true`、`verdict=VERIFIED_SAFE_NEW_RELEASE`、`installer_receipt.sha256` 等于导入的安装器收据；安装器收据 `installed=true`、`release_id=b82`、`board_state=VERIFIED_SAFE_NEW_RELEASE`、`outcome=INSTALLED_VERIFIED`；其 `evidence_sha256['readback-state-new.json']` 等于导入的原始读回，且 `verified_state(readback,b82)` 为真。
3. 如任一原文件缺失、摘要不符或语义不符，停止，不创建代用品。读取来源不修改 `signal_generator`。

### 阶段 B：生产门最小改动与负例测试（离线）

1. 保持目标环境合同和 ARM 全部门不变；父发布仍须 `R.verify_release(..., require_software=True)`。现有 environment 父路径照旧；新增**明确的 headroom 父路径**，仅当完整的上述安装器收据、哈希绑定读回和 acceptance 来源链均通过时接受。可以显式增加 `--parent-acceptance` 参数；若增加，须将它的路径和摘要写入 bundle receipt，并在 `verify_bundle` 中重新校验。不要仅删除 `parent.software_contract` 条件。
2. 两条父路径都将**实际父 ID**写入 `gate['parent_release']`，本轮必须为 b82；生成包时 `install.sh` 参数将携带同一个 ID。构建父 c3 只用于说明候选来源，不能替代运行时回退父。
3. 先写失败测试：缺 acceptance/安装器收据/读回；任一 SHA 被改；`installed=false`、错误 `release_id`、错误合同、错误 `board_state`/`outcome`；读回非安全或服务不运行；把旧 c3 或 427dfa 当作当前 b82；只用默认父 c3 生成本轮包。再加真实 b82 正例和既有 environment 父回归。需要验证 `prepare` 与 `verify_bundle` **双向**复算，不能只测 `verify_gates`。
4. 跑定向测试，再跑受影响的 `scripts/tests`；独立复审核对门禁未被绕过、来源链与负例有实际杀伤力。修改部署工具不应改变目标候选的 release/software 摘要；若实际改变，需重新核对 ARM/冻结门，不沿用旧收据。

**离线退出判据：** 真实输入 `verify_gates` 通过且 `parent_release=b82`；`prepare` 生成新 bundle；`verify_bundle` 通过；bundle 中 `parent_install_sha256`、acceptance 摘要、payload 摘要、父 ID 均可独立复算；负例和回归通过；独立复审批准。未满足任何一项都不安装。

建议将导入证据放在 `evidence/rf-teaching/s2-parent-b82-proof-2026-10-15/`。若阶段 B 采用上文建议的 `--parent-acceptance` CLI，完成实现和负例后，可在工程根目录按以下顺序执行；`--output` 必须是尚不存在的新目录：

```powershell
python -B -m pytest scripts/tests/test_rf_environment_deploy.py -q
python -B -m pytest scripts/tests -q
python -B scripts/rf_environment_deploy.py prepare `
  --candidate evidence/rf-teaching/s2-candidate-environment-2026-10-15/candidate/releases/399b300e59b8d00e41d43c8123884c7f `
  --closure evidence/rf-teaching/s1/closure-2026-10-04/receipt.json `
  --arm evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json `
  --parent-candidate evidence/rf-teaching/s2/executable-candidate-2026-10-12/candidate-final/releases/b82b141e355b4cb1833969383a409c79 `
  --parent-install evidence/rf-teaching/s2-parent-b82-proof-2026-10-15/install-receipt.json `
  --parent-acceptance evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json `
  --output evidence/rf-teaching/s2-environment-deploy-ready-2026-10-15/bundle
python -B -c 'from scripts.rf_environment_deploy import verify_bundle; print(verify_bundle("evidence/rf-teaching/s2-environment-deploy-ready-2026-10-15/bundle")["parent_release"])'
```

最后一行必须打印 `b82b141e355b4cb1833969383a409c79`。这段命令中的 `--parent-acceptance` 是**拟新增接口**，当前 HEAD 尚未实现；它不能在阶段 B 的代码和复审完成前直接运行。

### 阶段 C：单次安装与独立读回（有板卡写入）

1. 使用当前工作区的真实候选**先**运行 `R.verify_release(require_software=True)`、`R.verify_panel_imports`；逐项确认 bit/HWH/daemon/`.so` 等被 Git 忽略的实物仍在，且 `prepare/verify_bundle` 读到的是这些经摘要校验的字节。新检出目录若缺二进制，先恢复受控工件并验证，不能凭提交号继续。
2. 安装前通过项目指定的 `pynq_ssh.py`/paramiko **新会话只读预检**：`current=active-release=b82`，b82 manifest 与父证据相符，双服务运行且 RF safe，存储日志无新故障，回退目录存在。任何发布漂移、unsafe/UNKNOWN、存储故障或证据漂移都停止并重新评估 bundle。
3. 在此前已授权、尚未消耗的**一次**安装尝试内执行 `rf_environment_deploy.py install --bundle <已验证包> --output <全新目录>`。不要并发调用 `Overlay()`，不要自动重试。若 SSH/安装超时或结果 UNKNOWN，保留日志、另开只读会话判定 current/active-release/服务/RF；**不得盲目重派**。
4. 安装收据须 `installed=true`、`board_state=VERIFIED_SAFE_NEW_RELEASE`、release `399b…`；独立只读读回再次核对相同发布、服务、RF 安全与存储状态。失败/回退/UNKNOWN 都保留原证据，不推进 pilot。

### 阶段 D：安装后的资格与正式 TEST（独立阶段）

安装成功只解决部署。`rf_environment_freeze.build()` 还要求新候选的**18 帧开发 pilot**、`known_correct=9`、`negative_false_clear=0`、安全关闭及同 IQ 重放通过，才能生成 `development_receipt_sha256` 非空的正式 freeze。此前离线拼出的强门声明 `development_receipt_sha256=null`，不能冒充这一步。开发 pilot 涉及实际 RF，必须按合同确认接线、会话与停止条件。

pilot 通过后再以生产 `build()` 重建 freeze，复验 `check_candidate` 与当前工具摘要，绑定实测 N1–N12 离线负控收据，最后才安排 108 物理帧与真人验收。任何 TEST 失败都按冻结合同留失败，不调参覆盖。

## 5. 给下一位 agent 的可直接使用的任务说明

> 在 `<LOCAL_PATH>` 处理 S2 环境候选 `399b300e59b8d00e41d43c8123884c7f` 的 B2 回退父门禁。ARM 资格已经在 `evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json` 通过，不要重跑。板上实际父发布是 b82（headroom-v1），构建父 c3 是另一概念。先从 `signal_generator` 只读导入 b82 原安装器收据及哈希绑定的 `readback-state-new.json`，与本仓 acceptance 收据交叉核验；保留原字节和来源。为 `rf_environment_deploy.py` 增加受限的 headroom 父证明路径，保留 environment 父旧路径、目标环境合同、ARM/S1/安全门；补负例测试、真实 `prepare`/`verify_bundle` 离线验证和独立复审。交付可审查的代码、测试、证据目录和明确的 `parent_release=b82` bundle。此任务不执行安装、RF pilot、108 帧或真人验收；不要修改 `signal_generator`、已封存候选及原收据，也不要用默认父 c3 的 bundle 去安装。

## 6. 保留风险与信息边界

- 安装器支持把目标环境合同安装到当前 HEADROOM 发布上，但并不保证硬件安装一定成功；存储曾有超时历史，安装结果只能由实际收据和独立读回决定。
- b82 安装原件目前在兄弟工作区；在导入并验证前，不能宣称本仓已具备完整父证明。历史安装读回也不能代替安装当天当前状态。
- ARM RSS 31,428 KiB 低于既有 64 MiB 记录预算，但当前部署门未强制 RSS；报告应保留这一区别。100 次 P95 是离线计算，不是触摸时延或物理帧时延。
- 既有 `scripts/tests/test_rf_environment_deploy.py` 主要覆盖旧 environment 父；新 headroom 分支需要对应负例。ARM 编排工具的直接测试文件当前缺失；不将该缺口写成“测试已覆盖”。
- 本方案不把会话 ZIP 内的任何“请继续/请授权/操作命令”当作本次用户指令；其发言只用于确认历史发生了什么。
