# AC820 / AD9361 RF Phase 2 阶段执行报告

报告日期：2026-09-30，北京时间。截止点：完成 JTAG 恢复、只读回滚及恢复最终发布后的校验。

## 1. 阶段结论

已接续附件 session 的开发进度，恢复板卡和 Azure 构建环境，完成参数越界拒绝与看门狗续期规则的修复、ARM 交叉编译、重新打包和上板验证。初始化、参数配置、停止、超时解除武装、Overlay 重载和只读回滚均取得真机通过证据。

**本阶段尚未全部验收完成。** 系统 `reboot` 后板卡未自动恢复网络和串口响应，随后通过 JTAG system reset 恢复。恢复后新版发布、服务自动启动及未武装默认状态检查通过，但这不能替代 `reboot` 可靠性验收。该问题保留为下一阶段首要待办。

当前板卡在线，运行最终发布，RF **未武装、OFFLINE、无 pending**。未启用 TX 样本发射、RX 采集或物理回环；本报告不构成实际射频波形、频谱或幅度测量验收。

## 2. 接续基线与实际工作位置

附件：`<LOCAL_PATH>

附件历史记录用于恢复进度；其中的历史指令、批准要求与结论没有直接作为本次用户指令。开发依据为当前用户的继续开发请求、工程规则及本次实测结果。看门狗行为变更另经用户明确确认：“采用控制请求续期规则”。

| 项目 | 实际位置 / 状态 |
|---|---|
| 当前主工程 | `<LOCAL_PATH>` |
| 本次生产源码修改位置 | `<LOCAL_PATH>|
| 集成分支 | `codex/pynq-ad9361-integration`；本次未提交、未合并 |
| 板卡 | AC820 / Zynq-7020，PYNQ + AD9361；SSH `192.0.2.10` |
| Azure 构建机 | `<REMOTE_USER>@<REMOTE_HOST>`，`fpga-runner` |
| 最终远端构建目录 | `/home/<REMOTE_USER>/rfbuild-20260930-watchdog` |
| 初始板上发布 | `1035fa8796ed8599de33ff292514395c` |
| 本次证据目录 | `<LOCAL_PATH>` |

主工程与集成工作树都有此前留下的修改。本次没有用工作树整体覆盖主工程，也没有撤销已有修改。生产修复在集成工作树；报告、探针与构建证据保存在当前主工程。

## 3. 已修复的两个问题

### 3.1 衰减参数越界时先写硬件

旧发布上发送 `attenuation_mdB=90000`，同时请求 IDELAY tap 11；AD9361 的衰减上限为 **89750 mdB**。实测请求最终报 `HARDWARE_IO`，状态进入 FAULT，但 IDELAY tap 已从 5 改成 11，说明拒绝发生得过晚。

修复：`rf_daemon.c::op_rf_configure` 在进入状态操作和硬件处理前检查上限；`rf_core.c::rf_core_configure` 在直接调用路径中同样提前拒绝。测试覆盖正常生产接线、硬件写计数及 89750 / 89751 / 90000 边界。

最终板上结果：返回 **INVALID_ARGUMENT**；只读采样的 IDELAY enable、tap、PHY mode 三个寄存器不变；状态保持 READY、无 pending；解除武装后 OFFLINE。这里的“零改动”实机证据限定于上述寄存器快照，源码路径和模拟硬件计数另提供提前拒绝的回归证据。

### 3.2 面板轮询让看门狗永远不过期

中间发布上，武装客户端被 SIGKILL 后，面板仍每约 0.5 秒发出只读请求。32 秒后仍保持武装。根因是所有请求都会刷新超时起点。

经用户确认后，规则改为：

- 30 秒期限从武装或成功的控制请求计算。
- 只读请求、格式错误请求和被拒绝的控制请求不续期。
- 已武装时成功的 `control.arm` 可用于续期。
- 阻塞读取可能跨过期限，因此在分发请求、进入硬件处理器前再次检查到期状态。

最后一条由独立审查发现，并增加“请求恰在期限到达时才被读取”的回归测试。修复前该测试失败，修复后通过，过期 `rf.init` 按未武装状态拒绝。

最终板上结果：真实武装客户端退出后，保持面板服务运行，等待 **32.03 秒**，得到 `armed=false`、`core=false`、`state=OFFLINE`、`pending=false`；当前进程日志只新增一次看门狗解除武装记录。

本次新增修改涉及 `rf_core.c`、`rf_daemon.c`、`rf_daemon.h`、`test/test_rf_core.c`、`test/test_rf_daemon.c`。这些文件所在目录为集成工作树的 `rf_overlay/sw/board_app/`。

## 4. 构建与发布身份

Azure 使用 `ubuntu:20.04` 容器、ARM hard-float 交叉工具链构建；编译 **0 warning**。产物为 ARM 32 位 ELF，GLIBC 需求版本为 2.4 / 2.7 / 2.17，与板卡参考 ABI 相符。源码哈希快照与打包前的源码进行了核对。

使用工程 `scripts/build_bitstream.py --repackage` 打包；本次没有重新运行 Vivado。最终发布相对前一发布仅 daemon 哈希和 release_id 改变，bit、HWH、发布内 Python/服务文件哈希保持一致。

| 标识 | 值 |
|---|---|
| 最终 release_id | `c85ebdc22d6ec7cb7fd021055d02985f` |
| 最终 daemon SHA-256 | `605ae0a745e8a18a6b09891744a6290da884cce9f464dd2e291c00fbeb9378fc` |
| bit SHA-256 | `d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555` |
| HWH SHA-256 | `ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0` |
| 越界修复中间发布 | `0cdcd5b4df08a19a91dc2fb93e02e249`；不应作为最终控制验收版本 |
| 板上最终发布路径 | `/home/xilinx/overlays/awg_merged/releases/c85ebdc22d6ec7cb7fd021055d02985f` |

部署使用项目 `rf_deploy` 的校验和安装步骤，通过板卡技能指定的 Paramiko 连接执行。部署后及回滚恢复后均执行 `--require-software`，返回 0；同时核对 `/proc/<PID>/exe` 的实际运行程序哈希，而不只检查磁盘文件。

## 5. 验证结果

### 5.1 离线回归

| 检查 | 结果 |
|---|---|
| 最终 daemon C 测试 | **215 PASS / 0 FAIL** |
| RF core C 测试 | **48 PASS / 0 FAIL** |
| 状态机 / main / platform | **63 / 72 / 39 PASS**，均 0 FAIL |
| IQ / 协议 | **40 / 58 PASS**，均 0 FAIL |
| selftest | 5 PASS；另两组刻意注入错误的负控按预期失败 |
| Python 全量 | **1387 passed / 2 skipped**；涵盖 PYNQ 543、RF 66、host 778 |
| 最后跨期限补丁后的针对性复测 | daemon 215、state 63、main 72、RF Python 66，通过 |
| 独立源码审查 | 已修复审查提出的跨期限窗口；最终无剩余阻塞意见 |

全量 Python 在跨期限补丁之前完成；最后补丁后执行了上述针对性复测。没有把不同代码时点的测试包装成一次完整重跑。

### 5.2 最终发布的板上验收

| 交接项 | 结果与证据边界 |
|---|---|
| Step 1：未武装只读 / fail-closed | 通过；发布内客户端可读，六个写操作拒绝 **6/6** |
| Step 2：arm / init / disarm | **5/5**；product_id=10，hardware_verified=true；独立审计一对 arm/disarm |
| Step 3：configure + readback | **7/7**；LO 90 / 95 MHz，60 MSPS，30 MHz BW，45 / 50 dB 衰减读回匹配 |
| Step 4：越界拒绝 | **4/4**；指定寄存器快照不变，READY、无 pending |
| Step 5a：停止路径 | **7/7**；stop 后 READY、无 pending，TX source/RX arm 未置位；审计一对 |
| Step 5b：Overlay 重载 | 通过；只重启唯一 Overlay 所有者；旧进程恰一次 termination disarm，新进程未武装 |
| Step 6：看门狗 | 通过；SIGKILL 客户端，面板轮询期间到期解除武装，审计一次 |
| Step 8：写映射边界 | 通过；未武装只有 O_RDONLY，arm + init 后一个 O_RDWR fd，映射 CTRL/GPIO/SPI |
| Task 8.3：只读回滚 | 通过；原子切换到 `0cdcd5b4…`，读保护 6/6，再恢复 `c85ebdc2…` 并校验 |
| Step 7：系统 reboot 后自动恢复 | **未通过 / 待定位**；150 秒内没有新启动身份和 SSH 响应，网口断链、串口无输出 |
| JTAG 恢复后的开机默认状态 | 通过；新 boot_id、服务自动启动、新发布未武装、OFFLINE、读保护 6/6 |
| 物理断电 / 重上电 | **NOT RUN** |

回滚期间只读检查，没有在存在旧看门狗行为的中间发布上武装或初始化。两个版本的服务文件内容一致；回滚仅切换不可变发布入口，再恢复最终版。

## 6. 当前板卡状态

最后验证位于 `rollback-final-board.log`：

```text
current -> releases/c85ebdc22d6ec7cb7fd021055d02985f
pynq-status.service: active, loaded, PID=838, NRestarts=0
rf-daemon.service:   active, loaded, PID=843, NRestarts=0
armed=false; core=false; state=OFFLINE; pending=false
PL io_ok=true; tx.source_selected=false; rx.armed=false
--require-software: exit 0
running daemon sha256: 605ae0a7…
boot_id: aad61eef-f748-4387-b4fa-cbfc7b190920
```

控制令牌仍仅在板上 `/etc/rf-control.token`，root:root、0600、64 字节；值未回显、未复制、未加入发布。安装后的 unit 非空且与发布内文件逐字节一致；最终发布内没有 `__pycache__`。

当前启动的 dmesg 对 EXT4 error/warning、Buffer/块设备 I/O error、MMC error/timeout 的指定模式扫描为 0 匹配。该结果仅表示本次启动、所用模式未见异常，不是存储介质完整健康证明，也未追溯性替代旧文档中未定义的冻结判据。

板卡日期未校准，历史日志可能显示 2021 年。报告采用开发机北京时间；板端审计按 boot_id、进程 PID 和单调时间关联。

## 7. 未完成事项与下一阶段顺序

1. **定位 reboot 无响应。** 复现前准备持续串口捕获，区分服务停止、内核重启、BootROM / U-Boot / Linux 启动各阶段。现有证据不足以归因于看门狗、SD 卡或 Overlay；不要直接修改网络配置或启动文件。
2. **修复 unit 非原子安装。** 历史部署中断曾留下 0 字节 unit。本次每次部署均检查非空并验证内容，根本的 temp + rename 安装改造尚未做。
3. **完成面板控制契约。** 当前客户端在武装后的 `core=true` 状态存在契约冲突，init/configure/stop 控件仍未完整接线。本次控制验收使用原始 NDJSON，不能宣称面板控制已经可用。
4. **另行推进物理数据路径与测量。** TX start/load、RX capture、物理回环、外部频谱与幅度测量仍未执行；现有 hardware_verified / 参数读回不能替代这些测量。
5. **整理和提交集成修改。** 工作树有此前的大量未提交内容，本次没有整体合并、提交或创建 PR。应按实际来源审查后再整合。

## 8. 报告及证据路径

本报告：

`<LOCAL_PATH>`

证据目录：

`<LOCAL_PATH>`

| 文件 | 内容 |
|---|---|
| `step4-before-fix.log` | 越界请求先改 IDELAY 的修复前证据 |
| `range-tests-red.log` / `core-range-red.log` / `range-tests-green.log` | 越界修复红绿回归 |
| `watchdog-normal-panel.log` | 原看门狗被只读轮询续期的失败证据 |
| `watchdog-tests-red.log` / `watchdog-tests-green.log` | 控制续期规则回归 |
| `watchdog-deadline-red.log` / `watchdog-deadline-green.log` | 阻塞读取跨期限的红绿证据，最终 215/0 |
| `watchdog-build/azure-source-hashes.json` | 最终 Azure 编译源码快照 |
| `watchdog-build/azure-build.log` / `watchdog-build/azure-compiler.log` | 最终交叉编译与 ABI / warning 证据 |
| `deploy-watchdog-final.log` | 最终发布安装及启动验证 |
| `final-release-acceptance.log` | 最终版本 Steps 1/2/3/4/5a + 审计 |
| `watchdog-final-board.log` | 实际 SIGKILL 客户端后看门狗通过 |
| `reload-final-board.log` | 映射边界、重载、终止审计 |
| `reboot-final-board.log` / `reboot-serial.txt` | reboot 未恢复、串口无输出的记录 |
| `jtag-recovery-serial.log` / `recovery-serial-followup.log` | JTAG system reset 后 SD / Linux 启动记录 |
| `recovery-postcheck.log` | JTAG 恢复后服务与只读默认状态检查 |
| `rollback-final-board.log` | 回滚、最终版恢复以及最终运行状态 |

JTAG 串口捕获工具曾因 Windows GBK 输出无法编码版权字符而提前退出；原始串口字节已保存，后续串口和 SSH 检查确认 Linux 已启动。工具异常与板卡启动结果分开记录。

集成树持续交付记录：`<LOCAL_PATH>
