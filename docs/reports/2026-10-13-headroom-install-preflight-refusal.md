# HEADROOM 单次安全安装：派发前拒绝报告（2026-10-13）

## 0. 结论

**`dispatched=false`。未上传、未派发、未写板卡（除一个 `/tmp` 只读检查器）。**

按 `docs/reports/2026-10-13-headroom-next-stage-execution.md` 第 1 条执行派发前核对：
**离线门与板卡只读状态全部通过**，但核对中发现一项**阻塞级**新缺陷 **N2**——已封存安装器
（`scripts/rf_headroom_deploy.py`）**自身的独立读回守卫在板卡上是 Python 语法错误**，
因此它**永远无法得出 `VERIFIED_SAFE_NEW_RELEASE`**。在该缺陷解决前派发，只会消耗
唯一一次安装授权，并留下一个与真实成功无法区分的历史 `UNKNOWN`。
按任务书第 1 条“任一条件不满足即停止，保存原始读数，不上传或派发”与第 4 条，**本轮停止并请求决策**（§3）。

## 1. 派发前核对：全部通过

### 1.1 离线门

| 检查 | 结果 |
|---|---|
| `seal-stage.py --verify` | **exit 0**，`verified: new candidate/G1/owned native lease/durable fake job; installation and RF NOT_RUN` |
| `rf_headroom_deploy.verify_bundle(install-bundle/)` | 通过：目标 `b82b141e355b4cb1833969383a409c79`、父版本 `b67a8aaab0c5904822580c0647b96fd0`、契约 `cnn-bpsk-headroom-v1`、四个 payload `candidate.tgz/install.sh/install_state.py/job.py` |
| bundle 收据 | `bundle-receipt.json` sha256 `e654d219a1d7ee26…`；`candidate.tgz` sha256 `4b050cf49de62080…` |

### 1.2 板卡只读快照（`rf_headroom_deploy.SNAPSHOT` + `validate_snapshot`）

- `current_release == active_release == b67a8aaab0c5904822580c0647b96fd0`（含 active 路径逐字相等）
- `boot_id = d85fa469-aeb7-4ef9-860b-15389314ef30`，`uptime = 1658.8 s`
- 双服务 `pynq-status`（PID 555）/`rf-daemon`（PID 621）均 `active/running`、`NRestarts=0`
- 根分区挂载选项 `rw,relatime`（`validate_snapshot` 的读写判据通过）
- 内核存储文本**完整读取** 15,097 字节，`storage_faults() == []`（无 MMC/SDHCI/ADMA/I-O/ext4 故障键）
- 保留文本尾部为已知的 FAT16 粘滞“未正常卸载”提示与 DRM 行；本启动 watchdog/thermal 命中仅为驱动注册行

### 1.3 只读 RF 状态（发布内 `RfStatusClient.poll()`，经已审查 `rf_teaching_install_state.py`）

- `verified_state(state, b67) = True`、`safe = True`、`online = True`、`stale = False`、`pl.io_ok = True`
- `armed = False`、TX `source_selected = False`、RX `armed = False`
- HEADROOM `used/active/armed/tx_active/rx_busy` 全为 `false`；`lease_held=true` 是读连接自己的正常租约状态，不代表布防
- 检查器 sha256 守卫通过后才执行；上传件摘要 `b7f33e59a0560d67…` 与本地逐字节相等

### 1.4 一条需要记录的事实（不作因果解释）

本轮 `boot_id` 与 15:50 第三方复验报告的 `bf3c9453-fe31-4977-882d-24997f77c4be` **不同**：
板卡在两次只读检查之间重启过。当前启动（约 28 分钟）内**无存储故障**、双服务正常、
`current` 仍为 b67，因此第 1 条的条件按现状满足；**重启原因未做结论**（`NOT_ESTABLISHED`），
仅作事实记录。

## 2. 阻塞缺陷 N2：冻结安装器的独立读回守卫是语法错误

### 2.1 位置与事实

`scripts/rf_headroom_deploy.py:470-474`（`readback_state`）：

```python
guard = 'python3 -B -c ' + shlex.quote(
    "import hashlib;assert hashlib.sha256(open('%s/rf_state_check.py','rb').read()"
    ".hexdigest()=='%s'" % (remote, digest))
```

生成的板端命令（本轮实测原文）：

```text
python3 -B -c "import hashlib;assert hashlib.sha256(open('/tmp/rf-headroom-readback-…/rf_state_check.py','rb').read().hexdigest()=='aaaa…'"
```

`.read()` 之后缺少关闭 `hashlib.sha256(` 的右括号 → **`SyntaxError: unexpected EOF while parsing`**。
正确形态应为 `sha256(open(...).read()).hexdigest()==…`。

### 2.2 两条独立证据

1. **静态**：把该文件里的守卫表达式**原样提取**后 `exec`、按板端 shell 的方式 `shlex.split`、
   再对 `-c` 载荷做 `ast.parse` → `SyntaxError: unexpected EOF while parsing (offset 198)`
   （本报告工具 `preflight.py` 的 `acceptance_instrument()`，可复跑）。
2. **板端实证**：本轮预检用同一构造上传只读检查器时，**板卡实际执行该命令并返回同一个 SyntaxError**
   （`preflight-guard.log` 首次运行，`preflight-run1-partial.json` 记录当时中断点）。
   即这不是纸面推断：真板退出码非零。

### 2.3 影响（为什么必须派发前解决）

`_run()` 返回板端退出码；`python3` 对 `SyntaxError` 退出非零（CPython 3.8.6 实测为 **1**；**2** 是命令行用法错误码——独立复审者 2026-10-13 的非阻塞更正 F1）→ 守卫判非零 →
`readback_state` 抛 `ValueError('read-back state checker digest differs')` →
`install()` 的读回分支进入异常路径（:662-665）→
`outcome = UNKNOWN_READBACK_INCOMPLETE`、`board_state = UNKNOWN`、**exit 1**。

也就是说：**即使安装本身完全成功，这个已封存的安装器也无法得出
`VERIFIED_SAFE_NEW_RELEASE`**。按任务书第 4 条，读回不全即“停止后续研究、保留日志与未知状态”，
于是唯一一次安装授权会被消耗，并留下一个新的安装 `UNKNOWN`——与 b67 那次
`UNKNOWN` 同类，正是本项目花了数日才澄清的那种状态。

### 2.4 为什么冻结套件没抓到它

`scripts/tests/test_rf_headroom_activation.py` 的假板卡对**任何含 `hashlib` 的命令直接返回 0**
（:151-153 “`if 'hashlib' in command: … return 0`”），
而它真正解析的只有不含 `hashlib` 的 `rf_state_check.py` 调用（:134）。
**假件比真板宽容**：守卫命令的文本与退出码在整套测试里从未被校验过。
失败机制与 2026-10-08 G0 的教训同类（“注入的假件掩盖了真实路径”）。

### 2.5 冻结约束（为什么不能就地修）

该文件是 `source-freeze.json` 的 `files_sha256` **22 项之一**
（`seal-stage.py --verify` 会断言其摘要）。就地修补会**破坏封存**并牵连候选证据链，
因此这不是本复审者可以单方面做的改动，而是**封存层面的决策**。

## 3. 请求决策：两个选项

### 选项 A（建议）：修守卫 + 重新封存 + 重跑本预检，然后单次派发

1. 一处括号修正（`.read())`），并补一项**真正校验守卫**的回归测试：
   对匹配摘要必须 `rc==0`、对不匹配/被篡改文件必须非零；同时要求守卫载荷可 `ast.parse`，
   并让假板卡不再对任何含 `hashlib` 的命令一律返回 0（按真实板卡语义判）。
2. 重新封存（新 `files_sha256` / 新 `stage-checkpoint`），`seal-stage.py --verify` 重新通过。
3. 重跑本 §1 的完整预检（离线 + 板卡只读）后，按任务书第 2/3 步**单次派发 + 独立读回**。

### 选项 B：授权按现状派发，并由复审者独立读回

- 需**明确接受**：冻结安装器的 `install-receipt.json` 将是
  `UNKNOWN_READBACK_INCOMPLETE` / exit 1（**工具缺陷，不是真实未知状态**）。
- 验收结论由复审者自己正确实现的检查器上传 + 已审查的 `verified_state()` 给出，
  attempt 历史原样保留并在执行报告中写明该 `UNKNOWN` 的来源与证据。
- 残留风险：项目规则“读回不全即停止并保留未知”会对一次**实际成功**的安装继续挂 `UNKNOWN`，
  后续每个门都要重复解释这一次例外。

**未获决策前不会派发**（`dispatched=false`）。

## 4. 本轮实际做过的板卡动作（全部只读）

- SSH 只读连接、`SNAPSHOT`（`sudo python3` 读 `/proc`、`systemctl show`、`dmesg`）、
  RF 只读 `poll()`、以及一次 `/tmp` 只读检查器上传（sha 守卫后执行）。
- **未**上传 bundle、**未**创建 `/tmp/rf-teaching-install-*` 暂存目录、**未**派发 systemd 任务、
  **未**写 `/home/xilinx`、**未**改 `current/active`、**未**装载 Overlay、**未**布防/发射/采集。
- 板端因本轮新增的内容只有两个 `/tmp/rf-headroom-preflight-<uuid>/` 目录（只读检查器，已保留未删）。
- 新物理帧 **0**、真实研究初始化 **0**、HEADROOM 研究仍 `NOT_RUN`。

## 5. 证据与摘要

| 文件（`evidence/rf-teaching/s2/headroom-install-2026-10-13/`） | 说明 |
|---|---|
| `preflight.py` | 预检 + 派发决策脚本（含 `acceptance_instrument()` 静态判定） |
| `preflight-receipt.json` | 本轮完整收据：offline / snapshot / rf_state / acceptance_instrument / `dispatched=false` |
| `preflight-run1-partial.json` | 首次运行在被拒守卫处中断的原始收据（保留） |
| `preflight-snapshot.log` | 板端 `DEPLOY_SNAPSHOT` 原始输出（含 15,097 字节内核文本） |
| `preflight-state.json` / `preflight-state.log` | 发布内只读 `poll()` 的完整状态与命令日志 |
| `preflight-guard.log` | 首次运行守卫日志（**空**：板端 SyntaxError 只进 stderr，退出码非零） |

关键摘要：`preflight-receipt.json` → `e706fda9fee3d0b9…`（24,003 B）；
`preflight-run1-partial.json` → `8264605336b5d64c…`；
`preflight-snapshot.log` → `5fa8598b2f02a0d3…`；
`preflight.py` → `162129f5e613794b…`；
`scripts/rf_headroom_deploy.py`（含 N2）→ `d6d0678f478ed14d…`；
`scripts/rf_teaching_install_state.py` → `b7f33e59a0560d67…`。

## 6. 未做 / NOT_RUN

真实安装（**未派发**）、Overlay 装载、布防、发射、采集、真实 46 dB 研究、runner 接真实传输、
正式 S2（108 物理 + 12 离线）、真人验收、S3、S4；MMC/SDHCI 根因 `NOT_ESTABLISHED`；
完整文件系统检查/修复 `NOT_RUN`。旧 b67 安装 `UNKNOWN` 记录继续保留。
