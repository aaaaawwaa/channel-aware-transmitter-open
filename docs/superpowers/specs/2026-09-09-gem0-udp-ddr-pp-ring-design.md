# GEM0 UDP DDR PP 第二阶段：播放期间环形重装设计

日期：2026-09-09
状态：设计已确认（分节评审通过），待用户审阅 spec
前置：M0–M3 全部板上通过并提交（7dbce14 / 92fd7b0 / 83f84c3 / 7398235），
`SET DMA BENCH` 撞 `.bss` 的挂死缺陷已修复（9ab3066）。
相关文档：`2026-09-08-ethernet-ddr-upload-design.md`（第一阶段，本设计扩展其
第 55/82 行推迟的“播放期间非活动 bank 重装”）。

## 目标

在播放进行中，允许主机通过现有 UDP 数据面把新内容写入**非活动** bank，
使两个 bank 无限交替播放（环形重装）：播放 A 时灌 B，播放 B 时灌 A，
只要下一次切换前目标 bank 已 `COMMIT`，播放就继续。

## 非目标

- 不改 AXI DMA 模式 / BD / PL：不消除约 3.15 ms 的 bank 切换间隙（由 AXIS
  FIFO 吸收，实测 `UNDERFLOW 0`）。
- 不做细粒度流式（环形缓冲分块喂数）。
- 不新增 UDP 播放启动消息；`GET DDR PP` wire 格式与字段名不变。
- 不允许播放期间经 UART 写入 bank；UART 仍是控制/恢复平面。
- 不改变有限播放语义：两个已提交 bank 各播一次后仍回到 `IDLE`。

## 现状（第一阶段）

- `ddr_pp_udp_begin` 只要 `ddr_pp_active` 或 `digmod_mode != 0` 就拒绝，
  因此播放期间无法上传。
- `ddr_pp_start_transfer(bank)` 置 `active_bank=bank`，`next_bank = 另一 bank
  （若 valid）`；`digmod_mode != 0` 是播放前提。
- `ddr_pp_service` 在 `DRAIN` + `user_stream_done` 时用一次性标志
  `ddr_pp_reload_used` 只自动切换一次，之后 `IDLE`。
- 切换路径 `XAxiDma_Reset` + 重臂约 3.15 ms；AXIS FIFO 约 16 KB（≈30 ms）
  吸收该间隙，实测 `UNDERFLOW 0`。
- 主循环单线程：`udp_ddr_pp_poll()`（`xemacif_input` 内跑 UDP 回调）与
  `ddr_pp_service()` 顺序执行，无重入。

## 第 1 节：所有权模型

每个 bank 的角色：

| 角色 | 含义 |
|------|------|
| `active` | 正被 DMA 读出的 bank（同时最多 1 个）——只读 |
| `staging` | 被写入方占有的 bank（UDP 会话或 UART CHUNK 序列）——绝不能是 active |
| `valid` | 已 COMMIT、可播放（现有标志） |
| `fresh`（新增） | 自上次播放以来被 COMMIT 过 |

`fresh` 生命周期：

- 初始化：`fresh=0`；`SET DDR PP CLEAR`：`fresh[bank] = valid[bank]`（重新武装仍有效的
  bank，使其可被再次 `START` 播放——保持 timeline/重复播放的既有契约：CLEAR 只重置播放
  记账，不使已提交内容失效）。
- 成功 `COMMIT`（UDP 或 UART）：`fresh[bank]=1`。
- `ddr_pp_start_transfer(bank)` 开始播放该 bank：`fresh[bank]=0`。
- `BEGIN`（UDP）开始新会话：`fresh[bank]=0`（连同现有 valid 清零）。
- `STOP`：不动 `fresh`。

固件强制的不变量：

1. active bank 永远不可写。
2. 正在 staging 的 bank 不可被 START 播放：UDP 会话进行中时 `valid=0`，
   `start_transfer` 要求 `valid[bank]`，故被拒。
3. UDP 会话与 UART staging 互斥（沿用现有 owner 规则）。
4. 只有 2 个 bank：一个 active 时，另一个是唯一合法写入目标、也是唯一切换候选。

由此：A 播放时主机用 `BEGIN/DATA/COMMIT` 灌 B；B 在 COMMIT 时变 fresh；
A 排空时固件**当且仅当 B 是 fresh 才切到 B**。

相对现状的改动：

- `ddr_pp_udp_begin`：若正在播放，放宽为仅允许 `bank != active_bank`（仍排除
  UART owner）；未播放时保持现有停播规则。详见第 3 节。
- 用 per-bank `ddr_pp_bank_fresh[2]` 取代一次性 `ddr_pp_reload_used`。

## 第 2 节：DMA 与切换时序

`ddr_pp_service` 的排空→切换序列（同一主循环内，无重入）：

1. `RUNNING` + IOC + DMA 不忙 → `DRAIN`（现有逻辑不变）。
2. `DRAIN` + `user_stream_done` → 取非活动 bank `other = active_bank ^ 1`，
   三态判定：
   - `fresh[other]` → `XAxiDma_Reset` + `ddr_pp_start_transfer(other)`
     （沿用现有重装路径，去掉一次性限制，**可重复**）；
   - 否则若 `ddr_pp_stage_owner[other] == DDR_PP_STAGE_UDP`（会话已开始但未
     COMMIT）→ **ring underrun**：`ddr_pp_stop_transfer()` +
     `STATE ERROR ERROR 26`；
   - 否则（非 fresh、无进行中会话）→ `IDLE`（自然结束）。
3. `start_transfer(other)` 清除 `fresh[other]`、置 `active_bank=other`。

判定说明：`fresh` 与“UDP staging 进行中”互斥（`BEGIN` 清 fresh，`COMMIT` 置
fresh 并清 owner）。UART 在播放期间不能 staging（`CHUNK/COMMIT` 要求
`!ddr_pp_active`），故 drain 只需检查 UDP owner；UART 默认 owner（CLEAR 后
`owner=UART`、`stage_bytes=0`）不构成“进行中”。

间隙：切换约 3.15 ms，由 AXIS FIFO（约 16 KB ≈ 30 ms）吸收，实测
`UNDERFLOW 0`。本阶段不改 DMA/PL；M4 门禁连续切换并断言 `UNDERFLOW 0`（间隙由
FIFO 吸收），并打印 `DMA BENCH` 时间线的 `NEXT_SUBMIT_TICKS` 供观察。

有限播放仍成立：M3 的 A→B 播完后，A 既非 fresh 也非 staging → `IDLE`，
timeline 的 A→B→IDLE 语义不变。

状态上报：`GET DDR PP` 的 `NEXT_BANK` 改为按当前状态计算——`active_bank !=
NONE` 且 `fresh[other]` 时显示 `other`，否则 `NONE`（现在是在 START 时一次性
算出、可能过期）。wire 格式与字段名不变。

## 第 3 节：协议与状态字段

UDP 数据面：不新增消息，复用 `ECHO/BEGIN/DATA/STATUS/COMMIT/ABORT`；
REPLY 格式不变。

`ddr_pp_udp_begin` 精确放宽条件：

- 通用校验不变（`bank<=1`、`ddr_pp_layout_valid()`、`total_bytes` 非零、4 字节
  对齐、`<= DDR_PP_BANK_BYTES`）。
- 若 `ddr_pp_active`：允许当且仅当 `bank != ddr_pp_active_bank`，且两 bank 均
  非 UART owner；`digmod_mode != 0` 不再作为拒绝理由。
- 若未播放：保持现有停播规则（`!ddr_active && !ddr_uploading &&
  digmod_mode == 0 && !play_blocks_config()` 且两 bank 非 UART owner）。
- 目标 bank 已 active 或 `owner != NONE` → `BUSY`（不变）。
- `BEGIN` 清 `fresh[bank]=0`；`COMMIT` 置 `fresh[bank]=1`（UART COMMIT 同样置位）。

`GET DDR PP`：字段名与 wire 不变；`NEXT_BANK` 采用第 2 节新语义。

UART 控制面：命令集与停播态 staging 规则不变（`CHUNK/COMMIT` 仍只在停播态）；
`CLEAR` 仅按第 1 节重定义 `fresh` 语义（`fresh[bank]=valid[bank]`），其余记账不变。
UART 继续作为控制/恢复平面；不新增命令。

错误码：新增 `0x26` = ring underrun（切换时下一块未就绪）；其余沿用
（0x20 参数/layout、0x21 DMA 复位失败、0x22 submit 失败、0x23 DMA 错误、
0x24 超时、0x25 stream underflow）。

上位机：新增“环形喂数”编排——始终比活动 bank 提前一整块（灌 B → 等切到 B →
灌 A → …），底层仍调用现有 `UdpDdrPpClient.begin/data/commit`；
`host/siggen/transport.py` 的 `SerialWorker` 职责不变。

## 第 4 节：错误与恢复

- **ring underrun（0x26）**：drain 时下一块正在 staging 但未 COMMIT →
  `stop_transfer` + `STATE ERROR ERROR 26`。**不自动 ABORT 该 staging 会话**，
  保留给主机决定：补齐后 `COMMIT` 再 `START`，或 `ABORT` 结束。会话自身 5 s
  不活动超时仍生效。
- **主机 ABORT 非活动 bank**：清 owner/valid/fresh，活动 bank 继续播；下次
  drain 因 other 非 fresh 且非 staging → `IDLE`。即“用 ABORT 优雅收尾”。
- **STOP 播放中**：沿用现状（立即停、清 active/next、复位 DMA），不动 fresh；
  主机随后可 ABORT 在途会话。
- **主机中途断流**：5 s 会话超时 → 自动 `ddr_pp_udp_abort(bank)`。时序决定收尾
  形态：drain 前超时 → `IDLE`；drain 时仍在 staging → `0x26`。两者都停止播放，
  只有后者报明确 underrun。
- **真·欠载（FIFO 被抽干）**：沿用现有 `XAXIDMA_ERR_ALL_MASK`(0x23) 与
  `user_underflow`(0x25) → STOP + ERROR，与 0x26（切换前就绪检查）互补。
- **收尾不变量**：任何 ERROR/STOP 后 `active_bank=NONE`、`next=NONE`、DMA 复位、
  state=`IDLE`（复位失败则 `ERROR 0x21`）。恢复路径：`STOP` → 清错 → 主机
  `BEGIN/COMMIT` → `START`；UART 始终是恢复平面。

## 第 5 节：测试与门禁

离线单测（host，先 RED 后 GREEN）：

- 固件源码不变量测试（沿用 `test_stage4/5_*_sdk.py` 风格）：`begin` 放宽的精确
  条件、per-bank `fresh`、错误码 `0x26`、`NEXT_BANK` 新语义、
  `ddr_pp_reload_used` 已移除、drain 三态判定。
- 新门禁 runner 的离线测试（FakeUdpClient / FakeUart）：环形编排“始终提前一整
  块”、ABORT/超时收尾、`0x26` 路径。
- 上位机环形喂数编排器单测：顺序、提前量、异常处理。

板上新门禁 M4（`scripts/udp_ddr_pp_gate.py m4`）：

1. 经 UDP 预装两 bank（均 COMMIT、`fresh=1`；从无 UART owner 的干净状态开始，
   不先 `CLEAR`）→ `SET DDR PP START`。
2. 循环 K≥3 次：播放 bank X 时，向非活动 bank Y 上传**新**确定性内容（CRC 与
   旧值不同）并 `COMMIT`；轮询 `GET DDR PP` 直到 `ACTIVE_BANK` 切到 Y。
3. 断言：切换序列 `0→1→0→1…`、`ERROR 00`、`UNDERFLOW 0`、每次切换后 Y 的
   `LENGTH/CRC` 等于新上传内容；打印 `NEXT_SUBMIT_TICKS` 供观察。
4. 收尾：不再回灌 → 观察 `IDLE`；或用 `ABORT` 在途会话优雅结束。
5. 负例：对非活动 bank `BEGIN` 但不 `COMMIT`，等活动 bank 排空 → 断言
   `ERROR 26` + `STATE ERROR`；随后 `STOP` 恢复。

回归：M0–M3、`test_bridge.py`(150)、`scope_verify.py`(54)、
`ddr_pp_timeline_gate.py`（单 profile + `--profile all`）、`ddr_gate.py`、host
全量单测全绿；timeline 的 A→B→IDLE 有限语义不得改变。

## 验收

- 离线：上述 host 单测与门禁 runner 测试全绿。
- 板上：M4 PASS（环形切换 K≥3、每次内容正确、无 underflow、无 ERROR）；M0–M3
  与全部回归 PASS。

## 待定/后续（不在本阶段）

- 消除 bank 切换间隙（需 SG 描述符链或 PL 双缓冲），本阶段不做。
- 播放期间 UART 重装（当前明确不做，UART 仅控制/恢复）。
- 三 bank（active/next/staging）以允许更早预装，本阶段 2 bank 已足够。
