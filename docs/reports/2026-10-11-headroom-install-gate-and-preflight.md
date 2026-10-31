# HEADROOM-1 候选安装完成，真实余量研究被实时能力谓词阻塞

2026-10-11/12（接续 Claude/Codex 中断后的 HEADROOM-1 阶段）。结论先行：

1. **Claude 的开发阶段确实完整**（已逐项核对，不是"未完成"）。
2. 安装路径里有**四处从未被执行过的缺陷**，其中前三处使安装根本无法完成；已修复、测试并独立复核。
3. **候选 `b67a8aaa` 已安装并独立核验为安全**：`cnn-bpsk-headroom-v1`、daemon 1.0.4、协议 minor 6、未布防、TX/RX 关闭、双服务 active/NRestarts0。安装期间客户端 240 秒期限先到，板卡随后自行重启；状态由只读读回定案，**未重试安装**。
4. **真实 46dB 研究在当前已安装候选上不可能执行**——这是本轮最重要的发现，且只有上真板才能看见：实时能力谓词要求 `lease_held=false`，而读它的客户端**正是持有该租约的人**。
5. 新增真实物理帧 **0**、真实初始化 **0**，开发物理累计仍 **67**。

## 一、起点核验（只读）

| 核对项 | 实际结果 |
|---|---|
| `headroom-contract-2026-10-11/stage-checkpoint.json` | `passed:true`、`installed:false`、`physical_headroom:NOT_RUN`、`new_rf_frames:0` |
| 候选 `candidate-budget/releases/b67a8aaa…` | 存在，46 软件成员 + `rf_daemon` + bit/hwh/model |
| ARM 资格 `arm-runtime-budget/receipt.json` | P95 **270.793ms**、RSS 31524KiB、`chain_parity_mismatches []`、`new_rf_frames 0` |
| 失败资格 `arm-runtime-final/receipt.json` | P95 314.963ms 保留 |
| 历史 1.0.3 准备器 | 仍精确绑定 1.0.3/1.5，`execution_ready:false`、`BLOCKED_BY_EXACT_40DB_CONTRACT` |

## 二、安装路径的四个缺陷（全部此前未被执行过）

`installation: NOT_RUN` 意味着整条安装路径从未跑过，于是四个静态缺陷一直没暴露：

| # | 缺陷 | 后果 |
|---|---|---|
| D1 | `CHAIN_CASES` 用的是实现前的名字（`complete/off-sync/railed/…`），而冻结收据用 harness 的八个真名（`full-five/cancel/off-false-positive/rails/missing-capability/wrong-id/no-iq/partial-iq`） | 门禁必然拒绝 |
| D2 | 要求 host/ARM 产物**逐字节相同**；实测 96 份中仅 3 份不同，且是同一数值最后一位浮点序列化差异 | 门禁必然拒绝 |
| D3 | 部署器把暂存目录建成 `/tmp/rf-headroom-install-*`，而已审查安装器的守卫只接受 `/tmp/rf-teaching-install-*` | **exit 2，板卡未被触碰** |
| D4 | 修好 D1/D2 后的替代检查只看"键集合形状"，独立复核用一个伪造收据（96 个摘要全部改写、88 份产物换成 8 个假键）即可通过 | 门禁被削弱（性质强度，不是误判本候选） |

**D1/D2 的独立证实**（复核人自证，我复算一致）：两份清单键集相同，仅 3 份 `replay.json` 不同；421 处差异**全是 float 对 float**，最大 |Δ| = 4.440892e-15，无键/结构/类型差异；资格器自己的判据 `compare(host, arm) == []`，而仓库容差是 1e-6——比观测差九个数量级。即：摘要不同不是"资格器漏掉了分歧"。

**修复**（`scripts/rf_headroom_deploy.py`，并新增 `scripts/tests/test_rf_headroom_deploy.py`）：

- `CHAIN_CASES` 改为 harness 真名；
- 新增 `chain_evidence_ok(receipt, arm_result, host_result)`：两份清单键集一致、**必须等于引脚文件 `chain-arm.json`/`chain-host.json` 自己的 `evidence_sha256` 映射**、两次运行都须 `passed/simulation` 且 `hardware_touched=false`、`new_rf_frames=0`、八个案例各有 `passed:true` 记录；
- `REMOTE_PREFIX = '/tmp/rf-teaching-install-'` + `remote_stage()`，并以**安装器自己的守卫**为真源做绑定测试。

**验证**：门禁测试 15 项；`scripts/tests` 全量 **288 passed**；**4 个有效变异全部被抓**——还原原始两个条件（12 failed）、还原为只看形状的版本（6 failed，正是复核的 A/B/C 变体与覆盖条款）、还原被拒前缀（1 failed）。

## 三、安装（两次尝试，一次真正到达板卡）

**尝试 1**：被 D3 拒绝，`exit 2`，发生在 `changed=1` 之前。随后**只读**读回确认板卡**未被改变**：adfac、safe、双服务 active、NRestarts0（`state-after-refused-install/`）。

**尝试 2**：真正执行。板端子安装器完成了发布落地（日志 `Installed …/releases/b67a8aaa…; services remain stopped.`），但我的客户端 240 秒期限先到 → 工具据其策略记为 `UNKNOWN`，**未自动重试**。

后果与恢复：

- 板卡随后**重启**：journal 显示一次**干净**的 `systemd-poweroff` 后重新启动；检查时 uptime 203 秒；随后 75 秒间隔实测 uptime 增长 75 秒 → **稳定，无重启循环**；无欠压/降频记录；`/tmp` 暂存目录已消失（父安装器的 post-state 未能取回，其自身收据缺失，已按原样保留为 `PARTIAL_UNKNOWN_PRESERVED`）。
- **重启原因 NOT_ESTABLISHED**：已审查工具链里没有任何一处会关闭板卡电源，我不做推断。请现场确认是否有人/外部因素断电。
- 恢复期间 SSH 能完成认证但开不出会话（Jupyter 80 端口一直 200 正常），约 30 分钟后恢复——这是登入会话/系统启动期拥塞，不是主机死亡。
- **定案靠只读读回**（与安装器 post-state 同一把审查过的 `verified_state` 判据）：`installed=true`、`VERIFIED_SAFE_NEW_RELEASE`、`software_contract=cnn-bpsk-headroom-v1`、`daemon_version=1.0.4`、`safe=true`、未布防、TX 源未选、RX 未武装、双服务 active/running。

## 四、阻塞真实研究的实时能力缺陷（本轮核心发现）

安装后做了**只读**实时能力预检：用**已安装发布自己的** `rf_status_client` 轮询 `device.info`，再用**已安装发布自己的** `capability_available` 判定。

守护进程的宣告**几乎全部正确**：`study=HEADROOM-1`、`protocol_minor=6`、`daemon_version=1.0.4`、`hello 1/6`、`initialization_budget=1`、`attempt_budget=5`、五个 `headroom.*` 操作齐全、`state=OFFLINE`、`used/active/armed/pending=false`、`tx_active=false`、`rx_busy=false`、`io_ok=true`、`available=true`。

**唯一不满足的字段是 `lease_held`，实测为 `true`**：

- `rf_daemon.c:2487 rf_daemon_serve()` 在**整条客户端连接期间**持有租约（:2494 acquire），连接结束才释放（:2530），`session.lease_held` 与之同置（:241-259）；
- `rf_status_client.headroom_capability()` 把它映射进能力对象（`lease_held=state.get('lease_held')`）；
- `rf_headroom.capability_available()`（:151-152）要求 `lease_held is False`；
- `HeadroomController.run()`（:540-542）**在 `client.headroom_lease()` 之内**才读能力并判定。

于是：唯一能读到能力的客户端必然正持有租约 → 判定恒为假 → 研究**每次都被拒**（`HeadroomStopped: live research capability or idle preflight refused`，`headroom_run` 回报 `stopped_at='CAPABILITY'`，不消耗尝试）。面板侧同一谓词，`ui_model.set_headroom_status` 也会把 `available` 判为假，按钮根本不会给出。

这是**纯硬件路径缺陷**：全部假硬件用例（含八个停止类与完整五次链）注入的 peer 都报 `lease_held=false`，因此**任何测试都看不到它**——这正是"开发与假硬件资格完成、真实研究 NOT_RUN"之间的那道缝。

**本轮不修**：谓词与原生合同都是已审查的安全面，改动需要新发布、新资格、新安装（改 Python 合同会改 release id）。已写入封存收据的 `not_fixed_here`。

## 五、封存证据

`evidence/rf-teaching/s2/headroom-research-2026-10-11/`

| 文件 | 内容 |
|---|---|
| `stage-checkpoint.json` | 阶段收据，63 份证据逐文件摘要；`passed:true` |
| `install-verification.json` | 两次尝试、重启观测、只读定案、实时能力结论 |
| `preflight/receipt.json` | 安装前新鲜只读预检（adfac、safe） |
| `install-refused-prefix-guard/` | 尝试 1 的拒绝证据（exit 2，零改动） |
| `install/install-receipt.json` | 工具原样保留的 `UnKNOWN`（未改写） |
| `install/boot-provenance.log`、`uptime-1/2.log`、`boot-history.log` | 重启与稳定性证据 |
| `install-readback/`、`final-safety/` | 安装后与收尾的两次独立只读定案 |
| `capability-readback/`、`live-capability-analysis.log` | 实时能力逐字段判定与唯一失败项 |
| `gate-fix/` | 绿日志、4 个变异日志与变异插件 |
| `state-check.py`、`seal-stage.py`、`analyse-live-capability.py` 等 | 本轮脚本（只读或离线） |

`docs/reports/2026-10-11-headroom-install-gate-and-preflight.md`（本文件）为阶段报告。

## 六、下一阶段（需要用户决定）

1. **现场确认板卡**：是否有人断电/重启过；当前应仍为 b67a8aaa、safe。
2. **修实时能力谓词**：最小方案是判定中不再把"读取者自己持有的传输租约"当作空闲条件——`state=OFFLINE`、`pending/pending_op`、`used/active/armed/tx_active/rx_busy` 已足以表达空闲；或原生侧另报"研究租约"以区别于单客户端传输租约。任一方案都**改变已审查安全面**，须走新候选 + 新资格 + 新安装。
3. **真实研究 runner 仍不存在**：`scripts/` 下只有准备器与部署器；`arm-runtime-budget/runner.py` 明确声明无 socket/PL/UI/RF。真实研究需要一个走生产链、上传隔离 `/tmp`、回传原始证据的 driver（属于尚未交付的 remote execution）。
4. **真实研究的人为前提**：`HeadroomController.run` 要求 `cable_confirmed is True` 与非空控制 token（无内置值、缺失即 fail-closed）。TX1→RX1 接线确认与 token 必须来自人。
5. 之后仍是：独立 TRAIN/VAL + 18 帧复验 → S2 正式 108 物理 + 12 离线/真人 → S3 → S4。

## 七、状态表

| 项目 | 状态 |
|---|---|
| 安装门禁缺陷 D1–D4 | **已修复**（15 测试、288 全量、4 变异、独立复核后加固） |
| 板卡只读预检 | PASS |
| 安装 | **完成并核验**（b67a8aaa / `cnn-bpsk-headroom-v1` / 1.0.4） |
| 安装期板卡重启 | **观测到，原因未定**（干净 poweroff→boot；稳定） |
| 实时 HEADROOM-1 能力宣告 | **是**（minor 6、五项操作、预算 1/5、idle） |
| 实时能力判定 | **false**（唯一失败项 `lease_held`） |
| 真实 HEADROOM 研究 | **BLOCKED**（需新候选；且无 runner、无人工接线/token 确认） |
| 新增真实物理帧 / 初始化 | **0 / 0** |
| 开发物理累计 | 67（未变） |
| S2 正式 / 真人 / S3 / S4 | NOT_RUN / NOT_RUN / NOT_IMPLEMENTED / NOT_RUN |
