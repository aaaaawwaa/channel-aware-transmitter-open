# 02 门禁模型（六类门 · 状态阶梯 · 物理派发 G0–G8 · 封存链 · HEADROOM 实例）

> **2026-10-17 流程修订**：审查量与开发版部署前置采用[个人开发审查与部署规则](../requirements/development-review-policy.md)。下文六类门用于说明证据层级与最终验收，不要求每次实验安装重新完成全部六类；G1–G4 实例表是其记录时的历史状态。

> 权威来源：六类门见 [docs/requirements/acceptance-checklists-p1-p6.md](../requirements/acceptance-checklists-p1-p6.md)；
> 状态阶梯见 [docs/requirements/brief-traceability.md](../requirements/brief-traceability.md) §1；
> 物理派发门序列见 [2026-10-12-real-headroom-research-runner-design.md](../superpowers/plans/2026-10-12-real-headroom-research-runner-design.md) §2.3–§3；
> 运行包/账本/六类结局见 [2026-10-13-headroom-board-run-package-design.md](../superpowers/specs/2026-10-13-headroom-board-run-package-design.md)。
> 本文只做整合与索引，判据本身以那些文件为准。

## 1. 六类门（固定顺序，缺一类就写 NOT_RUN，不写"通过"）

**离线（OFF）→ ARM（ARM）→ 数字回环（DL）→ 有线物理（PHY）→ 真人（HUM）→ 发布（REL）**；
某类不适用写 `N/A` 并说明原因。每个 Chunk 进下一阶段前逐项有证据或明确 `NOT_RUN`；**失败与未知保留**。

三条硬规矩（原文在 acceptance-checklists-p1-p6.md §头部）：
1. **测试数量不能替代物理或真人结果**（2867 个 pytest 通过 ≠ 板上跑通）。
2. **历史收据只证明其版本与范围**：改动影响到的行为需要补证；未受影响的证据和已有同差分审查可以引用（见 [03](03-development-discipline.md) §4）。
3. 某类不适用时写 `N/A` 并说明原因，**不写"通过"**。

## 2. 状态阶梯（逐条含义，权威定义在 brief-traceability.md §1）

`CODED` → `OFFLINE_VERIFIED` → `ARM_VERIFIED` → `BOARD_VERIFIED` → `HUMAN_VERIFIED` → `RELEASED`

| 状态 | 含义（一行） |
|---|---|
| `CODED` | 有实现，但该层无证据 |
| `OFFLINE_VERIFIED` | 离线测试可复现通过 |
| `ARM_VERIFIED` | 板上 ARM 原生构建/运行证据 |
| `BOARD_VERIFIED` | 真实板卡运行证据（按条目注明范围：数字回环 / 有线回环 / 真实采集） |
| `HUMAN_VERIFIED` | 真人在实体屏上完成并留证据 |
| `RELEASED` | 有绑定源码摘要的发布身份与回退版本，且安装核验通过 |

阶梯之外的常用词：`PARTIAL`（部分范围已证）、`NOT_RUN`、`NOT_MEASURED`、`NOT_IMPLEMENTED`、
`UNKNOWN`、`NOT_ESTABLISHED`（根因未证明）、`NOT_ASSESSED`（未评估）。**这些词不得被"应该是"替换。**

**纪律（经验，全工程最贵的一条）**：未证明项必须原样保留 `NOT_RUN`/`UNKNOWN`；
禁止用推断、旁证、旧收据、相似工况"补"证据。典型被保留的例外：板卡重启原因 `NOT_ESTABLISHED`、
MMC 根因 `NOT_ESTABLISHED`、46 dB 研究 `NOT_RUN`、削顶根因未证明。

## 3. 物理派发前的能力核验 G0–G8（顺序即门禁）

在 `headroom.begin` **之前不得发生任何硬件动作**（不 `control.arm`、不 `rf.init`、不 `rf.configure`、
不写 PL、不开 Overlay）。任一步失败 → `RUN_NOT_STARTED_*` / `RUN_REFUSED_*`，零尝试、板卡零写入。

| # | 步骤 | 判据要点 |
|---|---|---|
| G0 | 授权与人工门 | 授权工件齐备、未过期、未重放；释放身份相符 |
| G1 | 板端存储预检 | 内核存储输出**完整**读取、无 MMC/SDHCI/ADMA/I-O/ext4 故障；**读取失败 ≠ 无错误** |
| G2 | 安装发布 attestation | `verify_release(installed_dir, require_software=True)` + 合同 + 冻结模块摘要 |
| G3 | 发布身份 | `device.info.release_id == 授权.expected_release == 已安装已资格发布`；daemon/协议版本 |
| G4 | 发布侧自述 | `worker.headroom_status()` 的 `available is True` |
| G5 | 进入研究租约 | `with client.headroom_lease():` 一条保留连接 + 三重绑定 |
| G6 | 实时能力判定 | `capability_available(...)` 必须 True：预算、状态、五项操作、**`lease_held is True`** 等 |
| G7 | 布防 | `client.headroom_arm(token)` 前后各读一次 `device.info` 核对 `armed` |
| G8 | **第一个物理动作** | `headroom.begin` → 原生 `rf_core_init/configure/get_headroom_readback` + 固定域读回 |

**禁止清单（设计违规，测试必须抓住）**：禁止直接写 socket；禁止自定义协议客户端；禁止绕过 controller；
禁止新增 daemon 命令/放宽原生门禁；禁止 scripts-only 旁路；禁止修改 46 项冻结软件；禁止复用安装器前缀；
禁止重连/重放/"补一次"；禁止触碰生产记录目录。原文见 runner 设计 §2.3。

## 4. 封存链 G1–G4（**与上面的 G0–G8 不是同一套编号**，勿混用）

来自 [evidence/rf-teaching/s2-headroom-seal-2026-10-16/NOTES.md](../../evidence/rf-teaching/s2-headroom-seal-2026-10-16/NOTES.md)：

| # | 阻塞项 | 状态（2026-10-16/17） |
|---|---|---|
| G1 | 被 pin 的假链夹具帧形态与新发布 `rf_headroom.py` 不符 | 已修（四个追加证据键 re-home 进 `headroom`），需**独立复审重出绑定** |
| G2 | 缺绑定新候选的 `arm-runtime/receipt.json` | 2026-10-16 已由隔离 ARM 资格产出（见 §5） |
| G3 | `final-safety.py` / `seal-stage.py` 把"当前已安装发布"硬钉在 b67，板卡实为 b82 | 2026-10-17 G3b 已改为"原安装证据 + 执行时只读读回"；首轮复审 CHANGES_REQUIRED（M08/M11 两条 fail-open 变异未被抓）→ `review-delta/REVIEW.md` **delta 复审 APPROVED** |
| G4 | `install-bundle` 与两个 checkpoint 未生成 | **未完成**：G4prep 只做到输入就位，仍被一份**过期独立复审绑定**阻塞 |

**当前唯一阻塞（事实，2026-10-17）**：`critical-review.json` 里 `tools_sha256['scripts/rf_headroom_deploy.py']`
绑定的是 G3 之前的摘要（`174f12a5…`），工作树已是 `26b3e5cc…` → `qualify-runtime.py:101` 抛
`stale Python candidate/harness review: critical-review`。**实现者不得自签**，须由独立复审者重出或 supersede。
详见 [05-evidence-index.md](05-evidence-index.md) 与 g4prep NOTES。

## 5. HEADROOM / S2 候选链实例（事实；每个 ID 都可在 §来源复算）

| 候选 release id | 合同 / daemon | 绑定物 | 状态 |
|---|---|---|---|
| `adfac1f14c3cdb379cb92569a21a9054` | `cnn-bpsk-environment-v1`，daemon 1.0.3 / minor 5 | `evidence/rf-teaching/s2/g1-qualification-2026-10-08/stage-checkpoint.json` | 已安装（2026-10-10），后成为历史回退版本 |
| `b67a8aaab0c5904822580c0647b96fd0` | `cnn-bpsk-headroom-v1`，1.0.4 / minor 6 | `evidence/rf-teaching/s2/headroom-contract-2026-10-11/stage-checkpoint.json` | 已安装（2026-10-12，经历 UNKNOWN→板卡重启→只读读回定案）；现为**候选构建父/历史恢复父**，非当前运行发布 |
| `b82b141e355b4cb1833969383a409c79` | `cnn-bpsk-headroom-v1`，1.0.4 / minor 6；软件 **46** 项 | `evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json`、`s2-parent-b82-proof-2026-10-15/install-receipt.json` + `readback-state-new.json` | 2026-10-13 安装（`INSTALLED_VERIFIED`）；2026-10-15 从 399b **原子切换回** b82；2026-10-16/17 只读读回仍为 `current`/`active-release` |
| `399b300e59b8d00e41d43c8123884c7f` | `cnn-bpsk-environment-v1`，daemon 1.0.4；软件 **44** 项 | `evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json`（P95 258.6409 ms） | 2026-10-15 单次授权安装**成功**（`VERIFIED_SAFE_NEW_RELEASE`）；随后 18 帧开发 pilot **失败**（第 2 帧 `CLIPPED`）→ 被切回 b82 |
| `aba80e92aec4ed3d4e83196733a800ae` | `cnn-bpsk-headroom-v1`，daemon 1.0.4；软件 **46** 项 / 原生 **58** 项 | 候选 `evidence/rf-teaching/s2-headroom-candidate-2026-10-16/`；ARM 资格 `…s2-headroom-g2-2026-10-16/work/qualify-runtime/`（P95 272.785 ms、RSS 31728 KiB、parity `[]`） | **未安装、未封存**；封存输入已就位（G4prep），卡在 §4 的复审重绑 |

关键历史缺陷（都真实发生过，供判据设计参考）：
- **`lease_held` 谓词反了**：旧候选 `rf_headroom.capability_available` 要求 `lease_held == false`，而判定发生在租约内 → 真实 46 dB 研究**永远必拒**；假硬件用例注入的 peer 都报 false，所以测试看不见。新候选改为 `is True`。（[2026-10-11 报告](../reports/2026-10-11-headroom-install-gate-and-preflight.md)、runner 设计 §2.4 G6）
- **`cap_cfg` 整字判零 vs 具名位**：真板帧 `cap_cfg=4`（bit2 = commit epoch）被 Python 谓词当成"CAP_ARM set"而误拒 → 2026-10-15 第二次 46 dB 派发 `RUN_STOPPED_EVIDENCE:1`。2026-10-16 澄清为**具名位**：`SRC_SEL`/`CAP_ARM` 必须 0，bit2 允许 0/1，`SMP_WRAP`/`CAP_CONT`/未定义位仍必须 0。（[2026-10-16 报告](../reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md)）
- **G0 被板卡 RTC 拒绝**：板载墙钟停在 2021-12-03，运行器用板端时钟判授权窗口 → `RUN_NOT_STARTED_PREFLIGHT`、nonce 未消耗。修复为"主机 UTC + 短龄锚 + `boot_id`/`uptime`"（[s2-clock-anchor-2026-10-15](../../evidence/rf-teaching/s2-clock-anchor-2026-10-15/NOTES.md)）。

## 6. 记录纪律（经验）

- 每次板卡动作前后都要有**独立只读读回**；读回只能证明"查询当时"，不能外推为"现在"。
- 收据里"合格帧计数"（如 `new_rf_frames`）与"原始帧是否存在"是两件事：原始 IQ 可以存在而未被接受。
- 一次会话的结局必须是**具名**的（如 `RUN_STOPPED_EVIDENCE:1`、`RUN_NOT_STARTED_PREFLIGHT`、
  `ABORTED_STORAGE_FAULT`），不要写"失败了"。
