# AC820 / AD9361 接手阶段报告: 核实、积压分桶提交、面板状态文案、最小 TX/RX 闭环离线实现

报告时间: 2026-09-30 (开发机时间; 板卡时钟仍为 2021-11-27, 板端身份以 release、PID、boot_id 与单调时间对应)。
上一阶段报告: `docs/reports/2026-09-30-rf-touch-parameters.md` (release `ff96b755…`)。

## 1. 结论

按顺序完成了四件事, 并且**没有写板**:

1. **只读核实**了工作树、板上 release、服务、软件哈希与武装状态 —— 与上一阶段报告**无差异**。
2. 按积压分诊清单, 把**集成工作树的 76 项积压**按来源与功能拆成 **6 个提交**入库, 并给出
   "哪些产物能从提交重建、哪些还没验证"的实测结论。
3. 修掉面板把"采集能力"当成"RF 控制可用性"的状态文案, 把 daemon 连接 / 武装 / 控制结果 /
   RX 采集能力拆成四个各自取源的显示位。
4. 把最小 TX/RX 数据闭环从"协议里根本没有路径"推进到**离线可执行、有红绿与变异证据**的实现;
   实机跑需要先做一次 ARM 重建 + 重打包 + 部署, 并需要操作员对"是否开放数据面 op"与
   "物理环回的线缆/仪器"作出决定。

## 2. 修改与提交清单 (集成分支 `codex/pynq-ad9361-integration`)

| commit | 主题 | 规模 |
|---|---|---|
| `7d73d8c` | chore(gitignore): 忽略 `--repackage` 生成物 `merged_build_receipt.json` | 1 文件 |
| `b18cd46` | feat(build): 产生板上 bit/hwh 的 BD/约束/构建输入 + BD 契约测试 + `build_manifest.json` | 15 文件, 1373+/54- |
| `ab15331` | feat(rf): `board_app` 侧车固件 (31 源) + 两个 unit + 契约/allowlist 测试 | 33 文件, 10705+/1634- |
| `a076d51` | feat(release): 合并发布打包/attestation 工具 + 板卡 e2e 工具 + 测试 | 12 文件, 4703+ |
| `2982363` | feat(panel): RF 触摸参数录入与后台控制 (发布内 5 文件 + 测试) | 13 文件, 2229+/130- |
| `2260dc1` | docs: 交付记录 §47、phase-2 计划、验收探针 | 15 文件, 4029+/1- |
| `d011a42` | feat(rf): 最小 TX/RX 数据闭环 (离线): `loopback.digital.capture` 单一事务 | 8 文件, 325+/37- |
| `4356419` | feat(panel): daemon 连接/武装/控制结果/RX 能力四态分列 | 2 文件, 167+/56- |

单独更新: 集成树 `docs/rf/merged-release-record.md` §48、
`docs/rf/phase2-board-handover.md` §0 接手更新、进度账本
`.superpowers/sdd/2026-09-27-pynq-ad9361-integration/progress.md` (gitignore 内)。

**没有**把两个工程的积压整体提交, **没有**删除或移动任何历史证据, **没有**把任何密钥/令牌入库
(只出现 `/etc/rf-control.token` 的路径引用; 令牌本身从未读取)。**未 push、未合并 master、未建 PR**。

## 3. 可重建性 (实测, 不说没测的)

- **daemon**: `rf_overlay/out/daemon_src_hashes.txt` 的 **31 个源哈希 31/31** 与提交后的工作树
  一致; `rf_overlay/out/rf_daemon` = `605ae0a745e8a18a6b09891744a6290da884cce9f464dd2e291c00fbeb9378fc`
  = 板上运行件。ARM 重建走 `scripts/rf_build_daemon.py`(板上原生 gcc); **本轮未重跑**。
- **bit/hwh**: 输入脚本/约束已入库, 字节级重建需 Vivado 2025.2 与工程构建流程; **本轮未重跑**。
- **软件**: 发布内 5 个软件文件、`install.sh`、两个 unit 与工作树**逐字节相同**;
  两份 `merged_release.py` 与 live receipt 的软件表一致 (`4377eba5…`)。
- 提交后工作树回归: PYNQ **579 passed/1 skipped** + RF **66** + host **779 passed/1 skipped**
  = **1424 passed / 2 skipped**, 与上一阶段报告一致。

## 4. 离线测试与板卡实测结果

### 4.1 离线

| 套件 | 结果 |
|---|---|
| PYNQ Python (`pynq_overlay/sw/tests`) | 提交积压后 579/1; 面板文案改后 **583 passed / 1 skipped** (+4) |
| RF Python (`rf_overlay/sw/tests`) | 66; TX/RX 离线实现后 **67 passed** (+1) |
| host Python (`host/tests`) | **779 passed / 1 skipped** |
| C (`rf_overlay/sw/board_app`, MinGW gcc 14.2.0) | `test_rf_iq` 40 / `test_rf_protocol` 58 / `test_rf_daemon` **224 (+8)** / `test_rf_state` **64 (+1)** / `test_rf_core` 48 / `test_platform_rf` 39, **全 0 FAIL** |

- 面板文案: **红先** (HEAD ui_view + 新测试 → 3 failed / 1 passed), 绿 583/1。
- TX/RX 离线判据: 预置 RX BRAM 已知字 → 跑完整 NDJSON 事务 (`tx.load` + `loopback.digital.capture`)
  → 要求返回 payload **逐字节**等于预置字、数据口环位被开关各一次并清、part 回 READY;
  另有"无 tx.load 被拒"(用**全新 daemon**, 因为 tx_count 是 daemon 状态)与"ratio 越界被拒"的反控。
- 变异: 去掉清环 → **222 pass / 2 fail**; 去掉 `enabled[]` 里的 op 名 → Python 契约测试红;
  两者逐字节恢复后全绿。**红绿与变异证据均保留**在 `evidence/2026-09-30-takeover/`。

### 4.2 板卡 (只读)

- release `ff96b755880df100c932d753398d3c00`; bit `d5bfe5d9…`、hwh `ed8d011a…`、
  daemon `605ae0a7…`、`install.sh` `e97fba77…` —— 与上一阶段报告一致。
- 两服务 `loaded / active / running`, `NRestarts=0`, MainPID 19761 / 19768;
  `/etc/systemd/system` 下 unit 与发布内副本哈希相同 (`3c24bd06…` / `43573db1…`)。
- 发布内客户端实测: `online=True, stale=False, error=None, mode=status_only, core=False,
  armed=False, daemon_state=OFFLINE, pending=False`, `io_ok=true`, TX `source_selected=false
  length_words=0`, RX `armed=false done=false`; `boot_id=aad61eef…` 未变。
- **板上字体检查 (纯渲染, 不碰 overlay, 不需要写板)**: 用发布内模块 + 新的 `ui_view.py` 跑
  `glyph_check.py` → `GLYPH_EXIT=0`; 201 串去重字**全部有字形**, 中文字号下限 14。
- 受约束: 全程用 `~/.claude/skills/ac820-pynq/scripts/pynq_ssh.py` (paramiko), 未用 Windows
  非交互 ssh/scp; **没有调用 `Overlay()`**; 令牌只 `ls` 过属性, 未读取; 板上临时文件已清。
- **没有**在板上重新武装、没有改变任何 RF 参数、没有发过任何一个样本。

## 5. 当前运行 release 及最终板卡状态

```text
release_id            = ff96b755880df100c932d753398d3c00
current               = /home/xilinx/overlays/awg_merged/releases/ff96b755880df100c932d753398d3c00
awg_merged.bit        = d5bfe5d9…   awg_merged.hwh = ed8d011a…   rf_daemon = 605ae0a7…
install.sh            = e97fba77…   (原子安装器)
pynq-status           = loaded/active/running  MainPID 19761  NRestarts 0
rf-daemon             = loaded/active/running  MainPID 19768  NRestarts 0
live status           = online=true stale=false error=null armed=false core=false OFFLINE
PL                    = io_ok=true, tx source_selected=false length_words=0, rx armed=false done=false
boot_id               = aad61eef-f748-4387-b4fa-cbfc7b190920  (未变)
token                 = /etc/rf-control.token 存在, -rw------- root 64B (未读取)
```

**板卡未被本轮改动**: release、服务、武装状态与接手时完全一致; 本轮只做了只读探针、只读状态轮询、
板上只读字形渲染 (临时文件已清)。

## 6. 未完成事项与具体原因

1. **TX/RX 数据闭环的实机运行** —— 离线实现完成并有测试, 但要把 `d011a42` 送上板需要
   (a) 用 `scripts/rf_build_daemon.py` 在板上原生 gcc 重建 ARM daemon、
   (b) `--repackage` 生成新 release、(c) 停服务部署。这是**改运行 daemon 的动作**, 建议先经你确认。
   数字环回本身**不需要外部接线**; 物理环回需要接线信息。
2. **物理环回 / 外部测量** —— 记录 (`docs/rf/channel_ai_assessment.md:212`、
   `loopback_criteria.py`) 表明本板物理环回需要 **TX1→RX1 电缆 + 40 dB 衰减器**
   (`RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB=40000`, 且要求 `cable_confirmed=true`),
   **没有**记录说明它现在是否接好。`loopback.physical.start` 因此**保持关闭**。
   另外它的旧实现 arm 了 RX 却不排空 BRAM, 取不到数据, 也需要一并修。
3. **旧 `board_app` CLI 不可重建** —— `rf_main.c` 调用 `rf_core_cli_dispatch()`, 该符号在本树
   **没有任何定义**, 所以 `make all` 链接不出来, 只有 `make rf_daemon` 与 host 测试能链接。
   `loopback_criteria.py` / `rf_task10.py` Step 5 依赖的 CLI 路线因此暂时不可用 (已记入交接文档)。
4. **面板像素级板上布局验收** —— 板上字体/字形已过 (`glyph_check.py` exit 0), 但像素级布局只在
   开发机字体 (msyh) 下验过, **未**在板上 DroidSansFallbackFull 下做像素级复核。
5. **软件重打包 / 部署 (面板文案 + TX/RX 离线实现)** —— 未做; 需要一次 `--repackage` + 部署。
6. **reboot / 物理断电定位** —— 仍 NOT RUN (上一阶段已明确需要持续串口捕获与已核实恢复路径,
   本阶段未重复尝试)。
7. **bit/hwh 字节级重建** —— 输入已入库, 但未重跑 Vivado。

## 7. 需要你决定 / 提供的信息

1. **是否把数据面 op 部署上板**: 即是否接受 `control_op_enabled` 新增 `tx.load` 与
   `loopback.digital.capture` 的 release 上板。离线已证明它只做"开环→抓一次→关环", 仍然拒绝
   `tx.start` / `rx.capture` / `loopback.digital.start` / `loopback.physical.start`。
2. 板上是否存在**可用的旧 `board_app` CLI** 及其来源 (本树无法重建)。
3. 物理环回的 **TX1→RX1 电缆 + 40 dB 衰减器**是否已接好 (需要你确认后才开 `loopback.physical.start`)。
4. **仪器型号 / 接法**与**预先冻结的数值门限** (示波器在本工程只作频率计、不作幅度计;
   任何 L4 结论都必须先有门限, 且不得并入 L3 PASS)。
5. 是否确认 AD9361 当前为 **1R1T / RX1 / TX1** (数字环回未调用厂商的通道交叉修正,
   在 RX1/TX1 下应为 no-op, 但应作为跑前显式检查)。

## 8. 报告、证据与操作入口

- 本报告绝对路径:
  `<LOCAL_PATH>`
- 证据目录 (集成工作树):
  `<LOCAL_PATH>
  关键文件: `task1-verification.md`、`board-identity.log`、`board-status.log`、`board-final-state.log`、
  `board-final-status.log`、`board-glyph-check.log`、`tests-{rf,pynq,host}.log`、
  `c-host-tests-*.log`、`mutation-task4.log`、`txrx-investigation.md`、`ui-copy/{REPORT.md,red_new_tests.txt,
  green_full_suite_final.txt,preview_geometry.txt,preview/}`、`commit-msgs/`。
- 交付记录: 集成树 `docs/rf/merged-release-record.md` §48;
  交接文档: 集成树 `docs/rf/phase2-board-handover.md` §0;
  进度账本: 集成树 `.superpowers/sdd/2026-09-27-pynq-ad9361-integration/progress.md`。
- 板载操作: 进入"接收 → RF参数"填参数后武装; 面板顶栏现在显示 `链路 在线/过期/离线`,
  接收页另列 `RF 武装` / `采集能力` / `控制` 三行, 不再用"RF 未接入"表示控制不可用。