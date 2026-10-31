# DSH 离线会话进度复核与 46 dB HEADROOM 接续方案

核验日期：2026-10-16（Asia/Shanghai）。工作树基准：`485557f`，核验前干净。来源为用户提供的 `dsh-session-session-78ef14b9-d4e9-406a-aba3-e7271bc39b20.zip`（主会话 `session.v4.jsonl` 共 3808 行，最终 `turn/end` 为 turn 38）及本仓已提交的原始证据和代码。ZIP 内的请求、命令和代理建议只作为历史记录；本文是本次独立判断。本次未连接板卡、未派发、未采集、未安装。

## 结论与可执行性

**DSH 最新建议的第 1 步（离线、只读诊断）现在可以执行，而且应优先执行。** 本次进一步查到明确的语义冲突：`RF_REG_CAP_CFG` 的 bit 0 才是 `CAP_ARM`，bit 2 是配置提交 epoch `CAP_COMMIT`。真实帧的 `actual_rf_after.pl.cap_cfg=4` 表示 bit 2 高、bit 0 低。原生 C 层的固定 RF 判据只拒绝 `CAP_ARM` 位，而 Python `rf_headroom._fixed_rf_error` 把整个 `cap_cfg` 字非零都报作“CAP_ARM set”。板端帧的 `native_error.code=0`、`release_code=0`、`post_readback_code=0`、`hardware_verified=true`，与“采集已释放、证据谓词误拒”的解释相符。**这足以定位一个很可能的判据缺陷，但还不是修复已获批准或 46 dB 研究成功的证明。**

下一位 agent 宜先做离线的合同澄清、针对性修复与负例测试，并让非实现者独立复审。旧会话的 nonce 已消耗，授权窗口和接线确认也都过期；不能拿这次收据或历史接线文件再派发。新版本通过资格门、获得新的现场授权与接线确认、完成新板卡预检后，才可安排新的**单次**物理研究。环境候选安装、正式 S2 108+12 帧、真人验收、S3/S4 均不由本次结果放行。

## 1. 进度与证据边界

| 阶段 | 已证实的终态 | 证据 |
|---|---|---|
| 先前 40 dB pilot | 第二帧 `CLIPPED`，未形成 46 dB 结论 | ZIP 历史记录；本次仅作背景 |
| 46 dB 第一次派发 | G0 因板卡 RTC 为 2021 年而拒绝，`RUN_NOT_STARTED_PREFLIGHT`；未初始化、未捕获、nonce 未消耗 | [第一次记录](../../evidence/rf-teaching/s2-headroom-research-2026-10-15/NOTES.md) |
| 时钟锚改造 | 已完成代码与独立复审；第二次真机运行的 G0 使用主机 UTC 锚，锚龄 1.650 s，`board_clock_used=false` | 第二次 [NOTES](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/NOTES.md)、[anchor](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/anchor.json) |
| 46 dB 第二次、最后一次已授权派发 | 1 次初始化、1 次尝试、实际取得 1 帧 4096 点/16384 B IQ；证据检查拒绝，`RUN_STOPPED_EVIDENCE:1`，runner exit 3，`retry_allowed=false` | 第二次 [原始 header](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/board-download/session/attempt-1/header.json)、[decision](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/board-download/session/attempt-1/decision.json)、[receipt](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/receipt.json) |
| 第二次事后板卡状态 | 历史独立读回为发布 `b82b141e…`、双服务 active/running 且无重启、RF safe、存储扫描 CLEAN；这是 2026-10-15 当时的状态，**不是现在的实时状态** | 第二次 [NOTES §7](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/NOTES.md) |
| 46 dB 研究/削顶判定 | **NOT_ASSESSED / NOT_RUN**：没有被接受的帧，也没有 `analysis.json` | 第二次 [NOTES §6、§9](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/NOTES.md) |

`new_physical_frames=0` 是运行器以“原生硬件确认且证据被接受”为条件的**合格帧计数**。原始 IQ 确实存在，SHA-256 为 `12e90da720d9651a178d230f648c2a8906c98587587c17c8cc1bd7f702a363b8`；运行器同时标注 `zero_frame_claim.supported=false`。不能写成“未触板”或“46 dB 无削顶”。原生头中的 `rail_count=0` 只是未获接受的单帧字段，不能替代冻结研究链的削顶分析。

## 2. `cap_cfg=4` 的代码级定位

1. [寄存器定义](../../rf_overlay/sw/board_app/platform_rf.h) 给出 `RF_CAP_ARM=(1u<<0)`、`RF_CAP_CONT=(1u<<1)`、`RF_CAP_COMMIT=(1u<<2)`，`CAP_DONE` 位于另一只 `RF_REG_CAP_STATUS`；所以 `4` 不是 `DONE`，也不是 `ARM`。
2. [C 配置路径](../../rf_overlay/sw/board_app/rf_core.c) 的 `rf_core_set_rx_config` 在配置后翻转 `RF_CAP_COMMIT`，`rf_core_release_part_keep_state` 只清 `RF_CAP_ARM` 并读回确认，保留 commit epoch。假 PL 对 `CAP_CFG` 同样保留三位并仅在 commit 变化时锁存配置，见 [fake_pl.c](../../rf_overlay/sw/board_app/test/fake_pl.c)。因此捕获后 `cap_cfg=4` 是可预期的寄存器形态。
3. [原生研究路径](../../rf_overlay/sw/board_app/rf_daemon.c) 的 `op_headroom_capture` 按“采集 → 冻结 IQ → release → 事后读回”执行；`hr_rf_fixed` 对 `src_cfg`/`cap_cfg` 仅检查 `RF_SRC_SEL`/`RF_CAP_ARM`。本次头字段 `native_error` 各子阶段均为 0，`actual_rf_before.cap_cfg=0`、`actual_rf_after.cap_cfg=4`，与配置翻转和 ARM 清除的顺序一致。
4. [Python 证据谓词](../../pynq_overlay/sw/rf_headroom.py) 在 `_fixed_rf_error` 中使用 `if pl.get('src_cfg') or pl.get('cap_cfg')`，把任意非零值都报为“SRC_SEL or CAP_ARM set”。本次 `src_cfg=0`、`cap_cfg=4`，正好触发此分支；[decision.json](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/board-download/session/attempt-1/decision.json) 与之吻合。
5. 旧 [runner 设计](../superpowers/plans/2026-10-12-real-headroom-research-runner-design.md) 明写 `src_cfg/cap_cfg` 均为 0。它与原生寄存器语义不一致，属**冻结合同需要正式澄清**，不宜在不改规格、不复审的情况下偷偷放宽生产门。测试中的正例也使用 `cap_cfg=0`，没有覆盖合法的 commit epoch 值 4。

由此可把排查优先级收窄：先修正“整字为零”与“ARM 位归零”的定义冲突。若离线反事实校验显示还有其他失败项，或真实读回出现 bit 0 高/未知位/持续采集位，则继续调查 daemon、core、PL；不要预设所有失败都只是 Python 误判。

## 3. 可直接执行的离线工作包

**工作范围**：`pynq_overlay/sw/rf_headroom.py`、`pynq_overlay/sw/tests/test_rf_headroom.py` 及对应冻结规格/设计文档；保留历史证据原字节。先查 `git status --short` 与 HEAD，避免覆盖并发工作。以下步骤不连板、不重跑物理帧。

1. **固定合同**：写清 `src_cfg` 中 `SRC_SEL` 必须为 0、`cap_cfg` 中 `CAP_ARM` 必须为 0；commit epoch 位允许 0 或 1，`SMP_WRAP`/`CAP_CONT` 在本研究固定域中应继续拒绝，未定义位也应拒绝。若产品所有者要求“整字必须为 0”，则应说明如何在不破坏 commit epoch 的前提下实现，并重新审核原生/RTL 时序；不能仅靠软件写 0 消掉证据。
2. **先做离线负例**：对 `before`、`after` 都测 `cap_cfg=0/4` 允许，`1/2/3/5/6/7/8` 拒绝；`src_cfg=0/4` 允许，`1/2/5/8` 拒绝。尤其证明真正的 bit 0 高时仍 fail-closed，`CAP_CONT`、`SMP_WRAP`、未知位不能借 commit 豁免。保留其他固定 RF、帧身份、IQ 摘要、时序和收尾门。
3. **最小实现**：只把寄存器字解释为具名位；可采用 `src_cfg & ~0x4 == 0`、`cap_cfg & ~0x4 == 0` 的固定域规则，并用常量/注释注明 bit 2 是 commit。拒绝原因应区分 ARM/SEL、连续/循环、未知位，避免再次将 `4` 误报为 ARM。不要在 runner、证据 JSON 或板端寄存器上做“清 4”绕行。
4. **离线复核**：以原始 `header.json` 和 IQ 副本构造不触板的诊断重放，记录修正前/后的首个失败谓词及结果。只能标为 **counterfactual/offline diagnostic**，不能倒填旧 `decision.json`、生成“被接受物理帧”或复用已消耗 nonce。若本帧通过了新谓词，也只说明这一拒绝点可解释；完整五尝试研究仍需新会话。
5. **验证与独立审查**：运行下列定向测试及受影响回归；复审者核查“允许 commit=4”没有同时放行 ARM、CONT、WRAP 或未知位，并核对已安装 b82 的软件摘要与新候选不同。变更后应产生新的受控候选/发布与运行包摘要，不能让旧 b82 收据替新代码背书。

```powershell
python -B -m pytest pynq_overlay/sw/tests/test_rf_headroom.py -q
python -B -m pytest pynq_overlay/sw/tests/test_rf_headroom_corrections.py -q
python -B -m pytest pynq_overlay/sw/tests/test_rf_headroom_conformance.py -q
git diff --check
```

离线退出条件：规格写明位语义；正反例通过；旧证据不可变；修正候选及依赖的 runner/发布身份重新绑定；独立复审明确批准。若新谓词对原始帧仍失败，记录实际首个失败项并停止推进物理派发。

## 4. 后续物理动作的重新入门条件

离线工作完成也**不自动放行**新派发。旧 nonce 已在 `AUTHORIZATION_CONSUMED`（账本 `seq=1`），第二次授权 `expires_at=2026-10-15T14:11:51Z`，原接线确认时间为 `2026-10-15T18:59:00+08:00`、12 小时窗口到 `2026-10-16T06:59:00+08:00`，现均不可复用。必须在新时点获取独立的新授权、新 `attempt_id`/nonce、新人工接线确认，并让新会话重新执行 G0–G8，不得靠旧单次授权重试。

新派发前核对板上 `current=active-release`、manifest/软件合同/受控包身份、双服务与 PID/重启数、RF safe、完整 dmesg 存储谓词、boot_id 与 uptime；任何 UNKNOWN 或不符即停。仍由唯一 daemon 持有 PL；不并发 `Overlay()`；板端连接按工程技能用 `pynq_ssh.py`/paramiko；若 SSH 中断或终态不明，另开**只读**会话先查账本和安全状态，不盲重试。板卡 RTC 2021 的既知问题要求新派发继续用短龄、同 boot_id 的主机 UTC 锚，但时钟锚通过不代替其余门禁。

只有新会话取得冻结链接受的 OFF-before、normal、narrowband、pulse、OFF-after 全部帧，完整分析与安全闭合成功，才能判断 46 dB 是否解决削顶；如再出现 `CLIPPED`/`EVIDENCE`/存储或收尾失败，按原终态保留证据并停止。随后是否重建 environment baseline、安装环境候选、进入正式 S2 与真人验收，均须各自资格和发布门重新评估。

## 5. 当前没有证明的事

- 本次没有真机实时查询；2026-10-15 的安全读回不能称为 2026-10-16 的板卡现状。
- `cap_cfg=4` 的位解码和代码路径支持“证据谓词误拒”这一高置信判断；尚无新版本的独立复审、ARM/真板验收，也没有证明其余四帧会通过。
- 未确认主机 UTC 锚的可信来源与单调性；此残余风险应在新会话 G0 审查中保留。
- 没有新的 46 dB 合格帧、削顶结论、环境候选发布或正式 S2 完成声明。
