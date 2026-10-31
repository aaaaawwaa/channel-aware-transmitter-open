# HEADROOM runner 第三方复验与下一阶段执行报告（2026-10-13）

## 审核结论

**离线 runner 审核门通过；下一门为单次安全安装与独立读回。**
DeepSeek Harness 的复审报告先对交接修订 `ba72f10e…` 给出 `CHANGES_REQUIRED`，找到事后
存储快照 `complete=true`、但 `kernel_storage` 缺失或不是字符串的阻塞缺陷 N1；其修补后的
runner `2ee0afe8…` 得到离线 `APPROVED`，但补丁由复审者本人写成，报告要求第三方复验。
本轮作为第三方检查了原版与现版的精确差异、重新运行红绿反例和完整证据链，未发现新的阻塞项。
**该结论只覆盖 host-side runner 的离线实现及已封存候选，不覆盖真实板卡传输、安装或 RF 研究。**

| 核验 | 本轮结果 |
|---|---|
| N1 回退守卫 | 原版两项 `2 failed`、exit 1（缺文本误报 `RUN_COMPLETED`；bytes 抛 `TypeError`）；现版 `2 passed`、exit 0 |
| 独立探针 | `41/41 PASS`、exit 0 |
| 完整证据重跑 | runner `139 passed`；缺席实现红态 `108 failed / 31 passed`；冻结套件 `79+34+26+16=155 passed` |
| 变异 | `34 total / 32 caught / F02、F11 escaped / unexpected 0`，两项逃逸是已声明的真实覆盖缺口 |
| 绑定与封存 | `BINDING_VERDICT=ALL_MATCH`；46 项软件、22 项文件、58 项原生输入相等；`seal-stage.py --verify` exit 0 |
| 收据 | `passed=true`，`board_contact=false`，`installed=false`，`physical_headroom=NOT_RUN` |

当前文件 SHA-256：runner
`2ee0afe8d1c0d438a38f8a689de554e4e9d1df9ca23dd5a07cc507de3291b217`；
测试 `a49adf177421787ffcfa334818781ffc43c8d88f8ad1a4765c50f64804e048a4`；
本轮重跑后的 `receipt.json`
`d0b96a9a464e52665d00ab79dbe5732dce26f06a40ffa42fcfc10dd1206dcecc`。
DeepSeek Harness 报告中的 `00318b5d…` 是其自身重跑时的收据；本轮再次生成日志后收据字节变动，
runner 与测试摘要保持相同，当前收据的内部绑定仍全部匹配。

本轮复验日志：`evidence/rf-teaching/s2/headroom-runner-third-party-2026-10-13/`；
其 `review-receipt.json` SHA-256 为
`cd206adb4c1621f830748be2b8c5053503726fa9c36eddc5eaa477df1103ad03`，
绑定红绿、探针、摘要、封存与板卡只读摘要文件。
原复审及反例：`docs/reports/2026-10-13-headroom-runner-independent-review.md` 与
`evidence/rf-teaching/s2/headroom-runner-2026-10-12/review-dsh-2026-10-13/`。

**一项非阻塞文字更正：** DeepSeek Harness 报告 §5.4 说 N1 修补后 boot-id 比较为
`UNKNOWN`，但当畸形快照仍给出与事前相同的 `boot_id` 时，代码和新增测试实际报告 `SAME`。
这是合理的两个独立判断：重启可判 `SAME`，存储故障列表仍未知，终态仍为
`RUN_UNKNOWN_STORAGE_UNREADABLE`、exit 3。若 boot id 本身缺失，重启判断才为 `UNKNOWN`。
下一阶段不得把 `SAME` 误读成存储健康。

## 板卡即时只读状态

用项目指定的 Paramiko 帮助脚本只读连接 AC820，并用 `rf_headroom_deploy.SNAPSHOT` 的
`validate_snapshot` 判据检查了完整快照；另用发布内 `RfStatusClient.poll()` 只读轮询 RF。
本地证据 `board-readonly.json` SHA-256
`59a131cc5d042e708dc291f8a48359de1cd2cb9a026e16e7f0684bc3903a6deb`。

- boot id `bf3c9453-fe31-4977-882d-24997f77c4be`；发布 current、active、RF daemon
  身份均为 `b67a8aaab0c5904822580c0647b96fd0`。
- `pynq-status`、`rf-daemon` 均 active/running，`NRestarts=0`；根分区和 `/boot` 均 `rw`。
  留存的完整内核存储文本摘要对应 15,045 字节，故障匹配列表 `[]`。
- RF online、非 stale、PL `io_ok=true`；`armed=false`，TX source 未选、RX 未布防，
  HEADROOM `used/active/armed/tx_active/rx_busy` 全为 `false`。读连接的
  `lease_held=true` 是正常租约状态，不代表 RF 已布防。

此快照是安装前的一次观察，**安装派发前仍须重新运行完整预检**。`/boot` FAT 的历史未正常
卸载状态和 MMC/SDHCI 根因仍未做完整文件系统检查/修复，不能把本次无新故障称作长期健康证明。

## 下一阶段执行任务：单次安全安装与独立读回

**目标：** 把已封存的 `b82b141e355b4cb1833969383a409c79` 安装到当前 b67 板卡，
以独立只读读回确认发布身份、服务、安全状态和安装后存储状态；此阶段不布防、不发射、不采集。

1. **派发前核对。** 再运行 `seal-stage.py --verify`、
   `rf_headroom_deploy.verify_bundle(install-bundle/)`。当前离线核对表明 bundle 目标为 b82，
   父版本为 b67，四个 payload 为 `candidate.tgz`、`install.sh`、`install_state.py`、`job.py`。
   然后重新取完整板卡快照与只读 RF 状态：current/active 必须仍是 b67，双服务正常、
   root `rw`、内核存储读取得到完整文本且故障列表为空、RF 安全。任一条件不满足即停止，
   保存原始读数，不上传或派发。
2. **仅派发一次。** 使用已封存 bundle 和一个全新、此前不存在的本地输出目录执行
   `python -B scripts/rf_headroom_deploy.py install --bundle <install-bundle> --output <new-evidence-dir>`。
   该入口会独立记录 `attempt.jsonl`、上传摘要守卫、单个持久 systemd 任务、观察和读回。
   超时或断线后不得重提、不得复用 output/attempt；只观察**同一次**持久任务并做只读读回。
3. **独立验收。** 不能只凭派发 ACK 或进程退出码宣布安装成功。核对
   `install-receipt.json`、`remote-job.json`、前后 snapshot、发布 manifest、current 与
   active-release、双服务 unit/状态、RF safe 和安装后内核存储故障列表。只有新发布 b82
   的只读安全状态 `VERIFIED_SAFE_NEW_RELEASE` 才可进入下一门；若执行历史因重启/断线为
   `UNKNOWN`，原样保留历史状态，不把当前发布读回倒写成“该次执行成功”。旧 b67 安装
   `UNKNOWN` 记录也继续保留。
4. **失败结局。** 预检拒绝、存储故障、观察超时、读回不全、服务异常或身份不符，均停止
   后续研究，保留日志与未知状态。不得自动重装、手工绕过守卫、重启来消除不确定性，
   也不得把回滚推断成已证明。

**下一阶段交付物：** 一次安装尝试目录（含 `attempt.jsonl`、`install-receipt.json`、
作业记录、上传摘要校验、前后完整快照及独立读回）、一份说明每项门禁结果与实际板卡状态的
执行报告。若未派发，也应交付预检拒绝报告，明确 `dispatched=false`。

## 安装之后的独立门

真实 runner 的 `default_dependencies()` 仍无板卡传输，CLI 在 G0 具名拒绝；因此即使
安装验收通过，也不能直接执行 46 dB 研究。应另做传输接线与离线故障注入：
`storage_probe`、`identity_probe`、`readback_probe`、`token_reader`、
`worker_factory` 的唯一控制链，新工厂明确声明 `performs_board_read`，并复核断线、重启、
畸形快照、一次性 nonce、单次初始化/最多五次尝试。F02/F11 的测试覆盖缺口需要在物理
研究门前补强或给出独立可审查的限制说明。随后才按人工接线记录和一次性 token 进入
有限物理研究；正式 S2/S3/S4 仍在其后。

截至本报告：新候选真实安装、真实 HEADROOM 研究、正式 S2/S3/S4 均 `NOT_RUN`；
本轮新增物理帧 0、真实研究初始化 0。
