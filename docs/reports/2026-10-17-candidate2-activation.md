# 2026-10-17 修复候选 e66197ed 重新启用（板端）

**结论**：在用户授权下，把板卡 `current` 从 `aba80e92aec4ed3d4e83196733a800ae` **原子切换**到**已存在**的
修复发布 `e66197ed79131b73aa4f47804487c053`，按安装器顺序重启两个 unit，跑一次**不发射**板端验收；
**验收四项全过 → 保留新版本，未回滚**。全程零 RF、零布防、零发射、零采集、零研究派发、零授权/nonce 消耗、
未加载 Overlay、未改产品代码/测试/合同、未 git commit、未删改任何发布目录。

证据目录：`evidence/rf-teaching/s2-headroom-activate-2026-10-17/`（`NOTES.md` 为逐项记录与文件索引）。

## 1. 背景与前提交代

目标目录是 2026-10-17 上午**可控失败回退演练**的残留：`codex-trial-closeout-2026-10-17/` 记录那次演练
安装成功、板端验收通过后注入 exit 42 并自动回滚回 `aba80e92`；基础安装器的回滚只还原 `current` 与 unit 文件，
**不删除已发布目录**，所以 `releases/e66197ed…` 仍在板上。本轮据此做**重新启用**，不重跑首次安装
（安装器的 `test ! -e "$dest/releases/$expected"` 会拒绝），也不重复可控失败演练。

## 2. 实际操作（一次派发，不重试）

1. **只读预检 22 项**（08:17:35Z）：`current=active-release=aba80e92…`；manifest `e51cdb40…`、合同
   `cnn-bpsk-headroom-v1`、46 项、`rf_headroom.py=f63c82c9…`、`rf_headroom_replay.py=c446262b…`、
   `rf_daemon=bbfd0409…`、daemon 1.0.4/minor 6；双服务 active/running NRestarts 0；`sudo dmesg` 全文 263 行
   0 存储故障；RF `armed=false/io_ok=true/source_selected=false/rx.armed=false`；`/`、`/boot` 均 rw；
   `boot_id=8280be4d-f5bb-4390-8d96-337b55275cae`；无活跃 `rf-trial-*`/安装作业；目标目录存在且为目录。
2. **目标发布一致性（只读）**：板端 `releases/e66197ed…` 与仓内封存候选**逐文件 sha256 比对 53/53 全等**，
   零缺失、零多余、零摘要差异、零符号链接、零 `__pycache__` 差异（允许的解释性差异一条也没有）；
   manifest `80fc00db…`、`rf_headroom.py=fa8f92c5…`、`rf_headroom_replay.py=c446262b…`、`rf_daemon=bbfd0409…` 相符。
3. **编排 + 本地沙箱**：新增 `work/activate.sh`（sha256 `5cb89362…`），复用已审查原语——
   `pynq_overlay/sw/install.sh:116-118` 的唯一临时链接 + `sync` + `mv -Tf` 原子发布、
   `scripts/rf_teaching_install.sh:25-60` 的 stop rf-daemon pynq-status → start pynq-status → start rf-daemon
   顺序与回滚形状、`scripts/rf_teaching_install_state.py` 只读状态检查器、受审 `acceptance.py 5588c287…`、
   `trial-install.sh` 的 `timeout --kill-after=2s` 用法。沙箱（WSL 真实 Linux，真 `mv -Tf`/`rename(2)`/`timeout 8.30`，
   假 releases + 假 systemctl）覆盖 9 个场景 **58 PASS / 0 FAIL**：正常路径、验收失败回滚、幂等重跑拒绝、
   SIGKILL 中断后状态可判定、SIGTERM 优雅停止回滚、目标缺失门禁、回滚无法验证时**不宣称成功**、
   以及**逐字回退命令**在健康新版本态的执行。
4. **上传 + 板端核验 + 单次派发**：上传 5 个成员到 `/tmp/rf-activate-8db3cdcb0903470b/`，板端 `sha256sum`
   逐项与本机相等；**一次** `systemd-run --unit=rf-activate-8db3cdcb0903470b --wait …`（08:18:28.7Z 起，
   20.83 s，exit 0，`Result=success/ExecMainStatus=0`）：写 pre-state → 停两个 unit → 原子切换 `current` →
   起 pynq-status → 起 rf-daemon → 有界等待 `active-release` → 60 s 预算跑 `acceptance.py` → 写 post-state →
   `ACTIVATION_ACCEPTED`。阶段日志见 `board-download/activation.jsonl`。
5. **独立只读读回（新 SSH 会话）23 项全过** + 取证 + 按精确路径清理本次 `/tmp` 暂存。

**板端写入面**：`current` 一个符号链接（唯一临时名 + `mv -Tf`，全程不缺失）、两个 unit 的 stop/start、
`/tmp` 暂存目录内的阶段日志与状态件。**没有**创建/改写/改权限/删除任何发布目录，**没有** unit 文件改动。

## 3. 验收结果（受审 acceptance.py，60 s，只读不发射）

| 检查 | 结果 |
|---|---|
| a 发布身份：`current` 解析 + `verify_release(require_software=True)`：`e66197ed…`／`cnn-bpsk-headroom-v1`／46 项／`rf_headroom.py=fa8f92c5…`／`rf_headroom_replay.py=c446262b…` | 通过 |
| b 导入与摘要：实际导入自 `releases/e66197ed…/rf_headroom.py`、摘要 `fa8f92c5…`、`STUDY=HEADROOM-1`、`RESEARCH_ATTENUATION_MDB=46000` | 通过 |
| c 历史真实帧（attempt-2：session `2fc2356f…`、before=(0,4)、after=(6,0)、IQ `af824f83…`）：`terminal=AWAIT_ACCEPT`、`hardware_verified=true`、`simulated=false`、`rail_count=0`；**旧规则仍拒绝同一字 `src_cfg=6`** | 通过 |
| d 位级允许集：`src_cfg` {0,2,4,6} 全接受、{1,8,0x10} 全拒绝；`cap_cfg` {0,4} 全接受、{1,2,8,0x10} 全拒绝 | 通过 |

`board-download/acceptance-report.json`：`ok=true`；journal 打印 `TRIAL_ACCEPTANCE PASS`；终态标记
`ACTIVATION_ACCEPTED`。`rollback-state.json` 不存在（未触发回滚）。

## 4. 最终板端状态（独立只读读回，23/23）

- `current` = `/run/pynq-status/active-release` = `.../releases/e66197ed79131b73aa4f47804487c053`（链接文本仍为相对路径）；
- manifest：`e66197ed…`、`cnn-bpsk-headroom-v1`、46 项，`rf_headroom.py=fa8f92c5…`、`rf_headroom_replay.py=c446262b…`、`rf_daemon=bbfd0409…`（manifest 与板端文件双侧相等）；daemon 1.0.4 / minor 6；
- 服务：`pynq-status` active/running MainPID 24215 NRestarts 0；`rf-daemon` active/running MainPID 24222 NRestarts 0；
- **TX/RX 未启动**：`armed=false`、`tx.source_selected=false`、`rx.armed=false`、`io_ok=true`、`online=true`、`stale=false`、`mode=status_only`；
- `headroom.used=false`、`active=false`（`initialization_budget=1`、`attempt_budget=5`，研究预算未消耗）；
- 存储：`sudo dmesg` 全文 270 行，6 类故障关键字 0 条；`/`（ext4）与 `/boot`（vfat）均 rw；
- `boot_id` = `8280be4d-f5bb-4390-8d96-337b55275cae`，与预检相同 → **未发生重启**；
- 回退目标 `releases/aba80e92…` 仍存在、53 成员、决定性成员摘要与封存父发布相等；两个 unit 文件与发布内副本相同。

已核对的 dmesg 新增行只有 XRT 设备开关与一次 `fpga_manager ... writing awg_merged.bin` + `Download new XCLBIN`：
**unit 重启使 pynq-status 重新下载同一 PL 镜像**（该 unit 既有启动行为），PL 身份未变（daemon
`bit_sha256=d5bfe5d9…`、`hwh_sha256=ed8d011a…` 前后一致且等于两个发布 manifest 的 `bit_sha256`）；期间未布防、未发射、未采集。

## 5. 回退步骤（逐字可复制，root；本轮未执行）

    cd /home/xilinx/overlays/awg_merged && ln -s releases/aba80e92aec4ed3d4e83196733a800ae .current.next.reversal && sync && mv -Tf .current.next.reversal current && sync && readlink current && readlink -f current && systemctl stop rf-daemon pynq-status && systemctl start pynq-status && systemctl start rf-daemon && systemctl is-active pynq-status rf-daemon && readlink -f /home/xilinx/overlays/awg_merged/current && cat /run/pynq-status/active-release

该文本已在沙箱内逐字执行验证（切换原子、`current` 不缺失、切回后 `current` 与 `active-release` 均为父发布、
无临时链接残留）；板上回退目标可用性已在 §4 只读核验。

## 6. 未做 / 未证明

1. **46 dB 研究会话未跑**：无 `headroom.begin/load/capture/accept`，无布防/发射/采集/研究派发，未消耗任何授权/nonce，物理帧新增 0。**软件验收通过 ≠ 46 dB 研究完成**，余量/削顶仍 `NOT ASSESSED`。
2. **新发布无完整 ARM 运行时资格**（无 100 次五段 p95/RSS/host-ARM parity/chain-arm 回执）；旧 `aba80e92` 的 p95/RSS 不为新代码背书。
3. 本编排是**新增、未经独立复审**的启用通道（`procedure_reviewed=false`），不是产品级能力；没有走 install-bundle/prepare/verify_bundle 的 runbook 路径。
4. 未做：可控失败演练（用户明示不做）、Overlay 显式加载、MMIO/SPI、LCD/触摸/真人验收、`e2fsck`/`fsck.fat`；MMC 根因仍 `NOT_ESTABLISHED`；其余历史发布目录未逐一核验。
5. 本次记录三处**本机侧**事故（均在证据 NOTES §10）：本地守卫路径错误（零板卡接触，未派发）；取证阶段以 xilinx 身份下载 root 0600 文件被拒（**派发已发生且成功，未重派发**，改用只读 `sudo cat` 取证）；清理脚本首次运行在 `rm -rf` 之后、写收据之前崩溃（删除已成功，修正后复核）。
