# 05 权威收据 / 报告索引

> 用途：**先查这里再动手**，避免重造轮子；也避免把 `NOT_RUN` 写成已完成。
> 状态词：`APPROVED` / `PARTIAL` / `NOT_RUN` / `FAILED` / `BLOCKED` / `SUPERSEDED` / `NOT_ESTABLISHED`。
> 一行摘要只写"这份东西证明了什么"，数字只在有据可查时给出；要用于决策请回原文件复算。
> 本索引**不替代** [docs/requirements/brief-traceability.md](../requirements/brief-traceability.md)（B1–B10 逐条状态）
> 与 [CLAUDE.md](../../CLAUDE.md) 顶部（最新叙事）。两者冲突时以原始收据为准。

## 当前新增：试部署与回退闭环（2026-10-17）

| 路径 | 一句话 | 状态 |
|---|---|---|
| [codex-trial-closeout 收据](../../evidence/rf-teaching/codex-trial-closeout-2026-10-17/receipt.json) / [真板收尾报告](../reports/2026-10-17-trial-rollback-board-closeout.md) | 修复候选 e66197ed 真板非发射验收通过后注入退出42，自动回滚 aba80e92；独立读回安全，最终仍旧版本；不代表46dB研究完成 | `BOARD_VERIFIED`（限定试部署与可控失败回退） |

## A. 工程基线 / P0（切换、副本、离线基线）

| 路径 | 一句话 | 状态 |
|---|---|---|
| [evidence/rf-teaching/p0-switch-2026-10-13/switch-receipt.json](../../evidence/rf-teaching/p0-switch-2026-10-13/switch-receipt.json) | 切换点记录：源端 HEAD `f676cf69`、38 改/449 未跟踪、runner 摘要、≥18.4 h 无写入；`gate_p0_0=CONFIRMED_BY_USER` | `PARTIAL`（用户确认级，非工具级证明） |
| [evidence/rf-teaching/p0-baseline-2026-10-13/](../../evidence/rf-teaching/p0-baseline-2026-10-13/) + [docs/reports/2026-10-13-final-prj-baseline.md](../reports/2026-10-13-final-prj-baseline.md) | 离线基线：五组核心 289 passed / 5 skipped 复现；`pynq_overlay/sw/tests` 2040/5F/5S/11E、`scripts/tests` 208/62F/1S/80E（155 项为源证据未复制、3 项次生 `pytest.raises` 消息、**0 项产品代码失败**） | 核心 `OFFLINE_VERIFIED`；其余 `PARTIAL` |
| [evidence/rf-teaching/imported-2026-10-14-manifest.json](../../evidence/rf-teaching/imported-2026-10-14-manifest.json)（+ `-round2-detail.jsonl`、`-p0-followup`） | P1 封存件迁移清单：逐文件 SHA-256；重力临时树按计划不复制；位流/HWH/原生二进制与 `*.log` 不入 Git 但本机保留 | 事实（清单） |
| [FORK_SNAPSHOT.md](../../FORK_SNAPSHOT.md) | 副本一致性：867 文件中 860 逐字节相同、2 仅换行符不同、5 副本专属；证据字节不做换行转换 | 事实 |
| [docs/reports/2026-10-13-final-prj-focus-decision.md](../reports/2026-10-13-final-prj-focus-decision.md) | 重心切换决策与边界（新工作集中 `final_prj`；源工程冻结为只读输入） | 决策记录 |

## B. P1 运行包（红/绿/复审/板端预检）

| 路径 | 一句话 | 状态 |
|---|---|---|
| [p1-run-bundle-red-2026-10-14/RED-NOTES.md](../../evidence/rf-teaching/p1-run-bundle-red-2026-10-14/RED-NOTES.md) | 先红：10 项期望 red 的原因逐条列出（账本/重放拒绝/runner 版本…） | 红态已留证 |
| [p1-run-bundle-green-2026-10-14/GREEN-NOTES.md](../../evidence/rf-teaching/p1-run-bundle-green-2026-10-14/GREEN-NOTES.md) | 绿：`BoardAuthorizationLedger`（fsync、撕裂尾行、重放具名拒绝）等逐条对应 | 绿态已留证 |
| [p1-run-bundle-review-2026-10-14/REVIEW.md](../../evidence/rf-teaching/p1-run-bundle-review-2026-10-14/REVIEW.md) | 独立复审：冻结判据未改、5/5（+1）变异被抓、还原逐字节成功、禁止模式扫描通过 | `APPROVED` |
| [p1-run-bundle-board-preflight-2026-10-14/](../../evidence/rf-teaching/p1-run-bundle-board-preflight-2026-10-14/) | 板端预检（只读） | 事实 |
| [docs/superpowers/specs/2026-10-13-headroom-board-run-package-design.md](../superpowers/specs/2026-10-13-headroom-board-run-package-design.md) | 运行包规格：六类结局、RTC 无效、负控集合、**12 例冻结判据**（`scripts/tests/test_rf_headroom_run_bundle.py`） | 规格（冻结） |

## C. S2 环境诊断（399b 线）

| 路径 | 一句话 | 状态 |
|---|---|---|
| [s2-freeze-declaration-2026-10-14/receipt.json](../../evidence/rf-teaching/s2-freeze-declaration-2026-10-14/receipt.json) | 冻结声明收据：40 dB 固定域、`expected_frames=108`、12/12 离线负控、`not_run=[formal 108 physical frames, human acceptance]` | `OFFLINE_VERIFIED`；正式 108/真人 `NOT_RUN` |
| [s2-offline-negatives-2026-10-14/receipt.json](../../evidence/rf-teaching/s2-offline-negatives-2026-10-14/receipt.json)（+ `-2026-10-15`） | N1–N12 离线负控 12/12 通过 | `OFFLINE_VERIFIED` |
| [s2-offline-negatives-review-2026-10-15/REVIEW.md](../../evidence/rf-teaching/s2-offline-negatives-review-2026-10-15/REVIEW.md) + `RESPONSE.md` | 独立复审：产物诚实可复现，但**门禁接线两条 P1 缺口**（M6 变异未被抓） | `CHANGES_REQUIRED` → 已响应（delta 复审 `APPROVED`） |
| [s2-arm-qualification-2026-10-15/arm-run/receipt.json](../../evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json) | 399b 候选隔离 ARM 资格：release/software 44/44 相符、原生 CNN、同帧差异空、100 次 P95 **258.6409 ms**、RSS 31,428 KiB | `ARM_VERIFIED` |
| [s2-parent-b82-proof-2026-10-15/](../../evidence/rf-teaching/s2-parent-b82-proof-2026-10-15/) | 跨合同回退父证明：b82 原安装器收据 + 哈希绑定读回导入；复审 11/11 变异被抓 | `APPROVED` |
| [s2-environment-install-2026-10-15/receipt.json](../../evidence/rf-teaching/s2-environment-install-2026-10-15/receipt.json) | 只跑 `prepare`，**未安装** | `PARTIAL` |
| [s2-environment-live-install-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-environment-live-install-2026-10-15/NOTES.md) | **单次授权安装成功**：399b 上板，`installed=true`、`board_state=VERIFIED_SAFE_NEW_RELEASE`，二次独立只读读回一致 | `BOARD_VERIFIED`（安装面） |
| [s2-development-pilot-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-development-pilot-2026-10-15/NOTES.md) | 唯一一次 18 帧开发 pilot：**exit 1 / passed=false**，第 2 帧 `INVALID`/`CLIPPED`；按规则**未重试、未补采、未调参**；正式 freeze `NOT_REACHED` | `FAILED`（保留原帧） |
| [docs/reports/2026-10-15-s2-parent-upgrade-review-and-execution-plan.md](../reports/2026-10-15-s2-parent-upgrade-review-and-execution-plan.md) | 部署路径复核：推荐"带证据约束的跨合同回退父支持"（不得直接删合同门）；B2 门禁精确原因 | 报告 |
| [docs/reports/2026-10-15-deepseek-double-review-followup.md](../reports/2026-10-15-deepseek-double-review-followup.md) | 强门复核：`tool_hashes` 一致仍不够；b82 合同与环境声明不匹配 → **没有可合法声明"已具备正式采集资格"的候选** | 报告（阻塞记录） |

## D. 46 dB HEADROOM（b82 / aba80e92 线）

| 路径 | 一句话 | 状态 |
|---|---|---|
| [s2-clock-anchor-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-clock-anchor-2026-10-15/NOTES.md) | 板端可信时钟锚修复（合同 §8）：主机 UTC + `boot_id`/`uptime` 锚，替代无效板钟；含独立复审 | `APPROVED`（离线） |
| [s2-headroom-research-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-headroom-research-2026-10-15/NOTES.md) | 第一次派发：G0 因板钟 2021 被拒 `RUN_NOT_STARTED_PREFLIGHT`；**nonce 未消耗**、零帧、零初始化 | `NOT_RUN` |
| [s2-headroom-research-attempt2-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/NOTES.md) | 第二次（最后一次授权）派发：1 初始化 / 1 尝试 / 实得 1 帧 4096 点原始 IQ；证据检查拒绝 `RUN_STOPPED_EVIDENCE:1`、`retry_allowed=false`；`new_physical_frames=0` | `FAILED`（原始 IQ 保留） |
| [s2-headroom-research-attempt2-2026-10-15/anchor.json](../../evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/anchor.json) | 第二次 G0 时钟锚：主机 UTC 锚、锚龄 1.650 s、`board_clock_used=false` | 事实 |
| [s2-headroom-restore-options-2026-10-15/OPTIONS.md](../../evidence/rf-teaching/s2-headroom-restore-options-2026-10-15/OPTIONS.md) | 恢复选项分析（选 A：原子切换回已存在的 b82 发布） | 决策记录 |
| [s2-headroom-switch-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-headroom-switch-2026-10-15/NOTES.md) | 板卡写入记录：`current` 从 399b **原子切换到 b82**（`mv -Tf` 原子替换、未触发恢复、`boot_id` 未变），重启双服务后两次只读读回；只读判定 `capability_available()==true`（正是 b67 上被 `lease_held` 卡住的那道门）；`procedure_reviewed=false` | `BOARD_VERIFIED`（切换面） |
| [s2-headroom-live-install-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-headroom-live-install-2026-10-15/NOTES.md) | 另一路径：已审查安装器**拒绝**（目标目录已存在），零板卡写入、不重试 | `BLOCKED`（该路径） |
| [s2-headroom-capcfg-clarification-2026-10-16/](../../evidence/rf-teaching/s2-headroom-capcfg-clarification-2026-10-16/) + [docs/reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md](../reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md) | `cap_cfg` 位语义正式澄清（bit0=ARM、bit2=commit epoch）：修正证据谓词的整字判零误拒；给出离线工作包与重新入门条件 | 澄清（离线）+ 报告 |
| [s2-headroom-framing-defect2-2026-10-16/](../../evidence/rf-teaching/s2-headroom-framing-defect2-2026-10-16/) | 帧内四键层级缺陷 #2 的探针与论证（`snprintf` 覆盖 `]}}`） | 事实 |
| [s2-headroom-candidate-2026-10-16/candidate/releases/aba80e92…](../../evidence/rf-teaching/s2-headroom-candidate-2026-10-16/) | 新候选：`cnn-bpsk-headroom-v1`、daemon 1.0.4、软件 46 项、原生 58 项 | 候选（**未安装**） |
| [s2-headroom-g1-2026-10-16/NOTES.md](../../evidence/rf-teaching/s2-headroom-g1-2026-10-16/NOTES.md) | G1：假链夹具按真板帧形态 re-home；主机侧研究链 `full-five completed=true/attempts=5`、8 用例/96 证据件、`chain_parity_mismatch=[]`、零板端接触；旧 `harness_sha256` 绑定变 stale 待重出 | 已完成（待新独立复审） |
| [s2-headroom-g2-2026-10-16/NOTES.md](../../evidence/rf-teaching/s2-headroom-g2-2026-10-16/NOTES.md) | G2：`aba80e92` 隔离 ARM 资格 **PASS**（`passed=true`、46 项、P95 **272.785 ms** ≤300、RSS **31728 KiB**、parity/chain parity `[]`、`new_rf_frames=0`）+ P0/P2 板卡只读预检与读回（b82 未变、双服务、0 存储故障、`/tmp` 清理 `NONE_LEFT`） | `ARM_VERIFIED` + 只读读回 PASS |
| [s2-headroom-seal-2026-10-16/NOTES.md](../../evidence/rf-teaching/s2-headroom-seal-2026-10-16/NOTES.md) | 封存链：H1/H2/B0/B2a 通过；**B1 被 stale 复审绑定阻断** → 无新 ARM 回执、install-bundle 与两个 checkpoint 未生成 | `PARTIAL` / `BLOCKED` |
| [s2-headroom-g3b-2026-10-17/NOTES.md](../../evidence/rf-teaching/s2-headroom-g3b-2026-10-17/NOTES.md) + `review/REVIEW.md` + `review-delta/REVIEW.md` | G3 第 1 步："当前已安装发布"改为"原安装证据 + 执行时只读读回"（b82 核准链 3 个 sha256 + 2 次 `verified_state`）；6 类负例 25 passed；首轮复审 `CHANGES_REQUIRED`（M08/M11 未被抓）→ **delta 复审 `APPROVED`** | `APPROVED`（delta） |
| [s2-headroom-safetyrerun-2026-10-17/NOTES.md](../../evidence/rf-teaching/s2-headroom-safetyrerun-2026-10-17/NOTES.md) | 用已复审 `final-safety.py` 只读重跑：rc=0、`passed=true`、已安装=b82（来源 `original-installation-evidence`）、P0↔P3 45 项 0 漂移 | `APPROVED`（只读） |
| [s2-headroom-g4prep-2026-10-17/NOTES.md](../../evidence/rf-teaching/s2-headroom-g4prep-2026-10-17/NOTES.md) | G4 离线准备：ARM 证据 267 文件逐字节就位、source-freeze 重冻结、组合测试 **2901 用例 / 2895 passed / 6 skipped / 0 failed**；`seal-stage.current()` 仍停在 `qualify-runtime.py:101` 的 stale 复审绑定 | `PARTIAL` / `BLOCKED` |
| [s2/executable-candidate-2026-10-12/](../../evidence/rf-teaching/s2/executable-candidate-2026-10-12/) | **b82 的已封存档**：`install-bundle/`（candidate.tgz 99 成员 + install.sh + install_state.py + job.py + bundle-receipt）、`development-checkpoint.json`、`stage-checkpoint.json`、`--verify` 通过 | 已封存（历史） |
| [s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json](../../evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json) | b82 安装验收收据（`accepted=true`、`VERIFIED_SAFE_NEW_RELEASE`、8 项 checks） | 事实（核准链根） |
| [s2/headroom-contract-2026-10-11/](../../evidence/rf-teaching/s2/headroom-contract-2026-10-11/) | headroom 合同封存：原生 58 项 + 4 份 native 复核（spec/quality 均 `APPROVED`）；`stage-checkpoint.json` `passed=true` | 已封存（历史） |
| [s2/headroom-runner-2026-10-12/](../../evidence/rf-teaching/s2/headroom-runner-2026-10-12/) | runner 初版封存与变异表（`receipt.json`、`mutations.json`） | 已封存（历史） |

## E. 早期阶段（S0/S1/CNN/UI/存储）—— 只给指针，不逐条索引

| 路径 | 一句话 | 状态 |
|---|---|---|
| [docs/reports/](../reports/)（76 篇，按日期） | S0–S1 教学链、CNN 生产化、UI 实板、BPSK 接收、S2 观察/等待/G2 等的完整叙事 | 历史报告 |
| [evidence/rf-teaching/s1/](../../evidence/rf-teaching/s1/) | S1 关闭收据（`closure-2026-10-04/receipt.json`）、上板 live 收据、真人照片复核 | S1 功能已闭合；触摸 P95 `NOT_MEASURED`（用户已取消专门计时） |
| [evidence/rf-teaching/s2/board-recovery-2026-10-12/](../../evidence/rf-teaching/s2/board-recovery-2026-10-12/) | 存储故障后的恢复读回收据（b67 在位、双服务、RF safe） | 事实；MMC 根因 `NOT_ESTABLISHED` |
| [evidence/rf-teaching/s2/storage-maintenance-2026-10-12/stage-checkpoint.json](../../evidence/rf-teaching/s2/storage-maintenance-2026-10-12/stage-checkpoint.json) | 存储结构审计（主机侧离线 FAT 审计 + 22 测试）与部署恢复机制 | 事实；物理离线检查 `NOT_RUN` |
| [evidence/rf-teaching/s2/settle-closure-20261007-v1/](../../evidence/rf-teaching/s2/settle-closure-20261007-v1/)（+ `-20261008-v1`） | G0 等待对照的离线闭合（round2/round3 收据、冻结源码清单） | 历史 |
| [evidence/rf-teaching/s2/g1-qualification-2026-10-08/](../../evidence/rf-teaching/s2/g1-qualification-2026-10-08/)（+ `-2026-10-09`） | `adfac1f1` 候选的 G1 资格（含真实 Linux harness 74 PASS/0 FAIL） | 历史（该候选随后被替换） |
| [evidence/rf-teaching/s2/g2-execution-2026-10-10/stage-checkpoint.json](../../evidence/rf-teaching/s2/g2-execution-2026-10-10/stage-checkpoint.json) | G2 等待对照执行（3 尝试/1 会话后 `CLIPPED` 停止，未通过、不重跑） | `FAILED`（保留） |

## F. 最新整体复盘（读它再动手）

| 路径 | 一句话 |
|---|---|
| [docs/reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md](../reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md) | 离线复核 + 46 dB 接续方案：`cap_cfg=4` 的代码级定位、可直接执行的离线工作包、重新入门条件、当前没有证明的事 |
| [docs/reports/2026-10-15-s2-parent-upgrade-review-and-execution-plan.md](../reports/2026-10-15-s2-parent-upgrade-review-and-execution-plan.md) | S2 环境候选部署路径复核与 A–D 阶段执行方案 |
| [docs/reports/](../reports/) 中以 `2026-10-17-headroom-g1-g4-status.md` 为代表的状态报告 | 同期 G1–G4 状态与过时表述标注（由文档同步角色维护；本索引只指向、不复制、不修改） |

> 提醒：`docs/reports/` 由当轮的执行者/文档同步者维护；本索引只做索引进 `docs/memory/`，**不**修改报告。
