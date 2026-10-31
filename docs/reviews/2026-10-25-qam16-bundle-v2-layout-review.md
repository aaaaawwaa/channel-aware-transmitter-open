# REVIEW — QAM16 安装包布局修复（bundle-v2）

日期：2026-10-25。范围仅打包/预检/真实安装入口与 A 的只读证据；复用 QAM 产品/ARM/Demod acceptance 既有结论，不重开算法全审。旧 `bundle/` 保留，旧 Codex 直接部署放行已被纠正；本轮零板卡/RF/安装/回滚/清理/commit。

## 1 原故障（已更正）

从原始 journal 提取的真实失败链：
```text
INSTALL BEGIN: stopping existing PL owner and sidecar
sh: 0: Can't open /tmp/rf-teaching-install-rf-qam16-cd76818a/unpacked/pynq_overlay/sw/install.sh
INSTALL FAILED: restoring previous release and service units
```
- **安装入口失败：CONFIRMED**；不是“只有tar日志/是否报错未知”。
- 旧 outer `candidate.tgz` `181bf027…` 只有 72 个文件且全在 `release/`，缺 `pynq_overlay/sw/install.sh` 和相对依赖；父包 `f17b53d8…` 有 130 files，含 `release/`、`pynq_overlay/`、`sdk/`、`rf_overlay/`。
- 当前 boot=`268b7a15…`（预试验 `1e08d422…`，跨重启）；current/active=**bedf98c7（父）**；candidate release dir 不存在；unit inactive/dead，当前 boot 无日志；RF 只读 online/unarmed/TX off；storage 无 I/O/OOM。
- **回退终态 = UNKNOWN/NOT_PROVEN**（进入 `INSTALL FAILED: restoring...`，但无 rollback-state/trial-rollback-state；current=parent 非充分证据）；**验收 = NOT RUN/UNKNOWN**；**整板卡死机制 = UNKNOWN**。
- `pre-state.json` 原始 SHA `5a945936…`；完整 journal `bff428d3…`；证据目录 `.../recovery-v2-2026-10-25/`。

## 2 bundle-v2 修复（B，离线）

**绝对路径**：`<LOCAL_PATH>`

| 项 | 值 |
|---|---|
| outer `candidate.tgz` | **`2437b44acd4d285ca5d9c451c0baeceea998011478fff7abd4675962f23412e9`**，2,047,691 B |
| outer layout | **138 files** = release/72 + pynq_overlay/ + sdk/ + rf_overlay/66 |
| release id | `cd76818afc3bee7c610678574e892b26`（release 字节不变） |
| stage `trial-stage.tgz` | **`9a6fb48040b86a564f1374f61782a5bd29ab1d9dfb950eea55f56fc6797ea55b`**，4,105,290 B |
| parent archive | `f17b53d8b0f8e16fbeb04f6714f941d4fcc3e043b703eb41bab78502ce4f95ac` |
| old outer | `181bf027…` 保留在 `bundle/trial/candidate.tgz`，未改 |

- 真实安装入口 `pynq_overlay/sw/install.sh` 与全部相对依赖存在；QAM 两个模块置于 `pynq_overlay/sw/`，candidate-owned 文件来自冻结 release，external 只来自冻结 parent archive（不混工作树）。
- 新 preflight **8/8 passed**（`install_entry/archive_layout/candidate_identity/parent_identity/new_release_absent/archive_digest/unique_identity/bounded_acceptance`）；旧72包 **passed=false**，`install_entry=false/archive_layout=false/archive_digest=false`，preflight 无 systemctl，未停服务/硬件。
- 隔离 WSL 沙箱（fake systemctl，无 stop）解包实际 outer，执行真实 `pynq_overlay/sw/install.sh`：输出 `Installed .../releases/cd76818a…; services remain stopped.`；current 指向候选；`verify_release --require-software --check-panel` 通过；安装 release 72 files，含 QAM 模块、sincos、service、install.sh；fake-systemctl 只有 is-active×2 + daemon-reload，无 stop。**非整安装器 mock，非仅 acceptance。**
- stage wrapper：preflight 先于 inner；`trial-phase.log`/tee/set -x 持久记录；`host_tail_phase_log.py` 可增量拉取（未执行）。
- 测试：`test_bundle_v2.py` **5 passed**；MEASURED host/WSL only，不冒充板卡。

## 3 结论与限制

- **旧包禁止重派**；旧 `bundle/` 失败保留。
- **bundle-v2 离线打包/预检/真实入口路径通过 Lead 增量核对**；未安装、未切服务；当前仍 `bedf98c7`。
- 原 trial 终态仍未证明：安装入口失败已证实，回退终态/重启机制 UNKNOWN；不得把 current=parent 当回退成功。
- 新包需要一次新的、用户授权的有界试部署；不得复用旧包/旧派发。
- 仍需关注：真实板卡上的持久阶段日志是否按预期增量保存；重启/卡死机制未定位，不因包修复而关闭。

## 4 下一步

1. 由 Codex 对本修复差分做一次增量核对（入口：`docs/reviews/2026-10-25-qam16-bundle-v2-codex-entry.md`）；旧包/旧授权不再引用。
2. 核对通过后，重新做父 bedf 的新鲜只读 pre；上传 **stage `9a6fb480…`**，安装 unit 全名，preflight 8/8，唯一一次 start。
3. 失败/失联：只观察原 unit，不重派/不并行回滚；先下载原始 `trial-phase.log`/journal/state；回退用既有事务，独立读回。
4. 本轮不执行上述物理步骤。
