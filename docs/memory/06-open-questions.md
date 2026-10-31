# 06 未证实项与决策待办

> 规则：**每条写"需要什么才能关闭"**。未证实项不得因为"看起来只差一点"而升级；
> 关闭时必须产出可核查的路径（收据/日志/报告），并遵守 [03](03-development-discipline.md) 的纪律。
> 排序：先阻塞物理动作的，再阻塞交付的，最后是卫生问题。

## 1. 阻塞物理动作的未决项

### Q1 `cap_cfg` 证据谓词修复未完成、未复审、未重绑
- **现状**：`pl.cap_cfg=4`（bit2 = commit epoch）被 `rf_headroom._fixed_rf_error` 当成"CAP_ARM set"误拒，
  导致 2026-10-15 第二次 46 dB 派发 `RUN_STOPPED_EVIDENCE:1`。位语义已在 2026-10-16 澄清，但**修复本身未实现/未复审/未绑新候选**。
- **需要什么才能关闭**：① 离线把读回解释为**具名位**（`SRC_SEL`/`CAP_ARM` 必须 0；bit2 允许 0/1；`CAP_CONT`/`SMP_WRAP`/未定义位必须 0，bit0 高仍 fail-closed）；
  ② 先写负例（`before`/`after` 的 `cap_cfg∈{0,4}` 允许，`{1,2,3,5,6,7,8}` 拒绝；`src_cfg` 同理）；
  ③ 旧帧**反事实离线重放**只能标 `counterfactual/offline diagnostic`，不得倒填旧 `decision.json`、不得生成"被接受物理帧"、不得复用已消耗 nonce；
  ④ 非实现者独立复审；⑤ 产生**新的受控候选/发布与运行包摘要**（旧 b82 收据不得替新代码背书）。
- 来源：[docs/reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md](../reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md) §3。

### Q2 46 dB HEADROOM 研究：`NOT_RUN` / `NOT_ASSESSED`
- **现状**：第一次派发被 G0 拒绝（板钟），nonce 未消耗；第二次派发消耗授权、取得 1 帧原始 IQ 但**证据未被接受**
  （`new_physical_frames=0`，`zero_frame_claim.supported=false`）。**没有被接受的帧，也没有 `analysis.json`**。
- **需要什么才能关闭**：新时点的**新授权 + 新 `attempt_id`/nonce + 新人工接线确认**；新候选通过资格门并安装；
  新会话重跑 G0–G8；取得冻结链接受的 OFF-before / normal / narrowband / pulse / OFF-after **全部帧**；
  完整分析与安全闭合成功。旧授权/接线窗口均已过期，**不得靠旧单次授权重试**。
- 残余风险：主机 UTC 锚的可信来源与单调性未确认，须在新会话 G0 保留审查。
- 来源：两份 research NOTES + 2026-10-16 报告 §1/§4。

### Q3 封存链仍卡在一份过期的独立复审绑定（G4 前置）
- **现状**：`python/critical-review.json` 的 `tools_sha256['scripts/rf_headroom_deploy.py']` 仍是 G3 之前的
  `174f12a5…`，工作树为 `26b3e5cc…` → `qualify-runtime.py:101` 抛 `stale Python candidate/harness review`。
  另：`seal_stage_sha256` 陈旧（门禁不检查但仍失真）。
- **需要什么才能关闭**：**独立复审者**以 G3 之后的真实字节重出或 supersede 该绑定（有 `supersedes` 先例）；
  实现者**不得**改写/自签。之后 `seal-stage` 才能进入写模式产出 `install-bundle/` 与两个 checkpoint。
- 来源：[s2-headroom-g4prep-2026-10-17/NOTES.md](../../evidence/rf-teaching/s2-headroom-g4prep-2026-10-17/NOTES.md) §4。

### Q4 新候选 `aba80e92` 的封存完成度不足
- **现状**：`source-freeze` 已重冻结、ARM 证据（267 文件）已就位、组合测试 2895 passed；但
  `arm-supervisor` / `final-safety` / `install-bundle` 收据**仍绑定旧候选 b82**，新候选没有任何 ARM 侧安装类证据。
  `SEAL-STATUS.md` 整体过期（正文仍写 "NOT SEALED — awaiting an independent critical review"）。
- **需要什么才能关闭**：复审重绑（Q3）→ 重建 `arm-supervisor`/`final-safety` 收据 → 产出 `install-bundle/` 与
  `stage-checkpoint.json`/`development-checkpoint.json` → `seal-stage --verify` 通过。
- 来源：[s2-headroom-seal-2026-10-16](../../evidence/rf-teaching/s2-headroom-seal-2026-10-16/NOTES.md)、
  [s2-headroom-g4prep-2026-10-17](../../evidence/rf-teaching/s2-headroom-g4prep-2026-10-17/NOTES.md) §5.1。

### Q5 板卡存储健康与 MMC 根因
- **现状**：MMC/SDHCI I/O 故障的**根因 `NOT_ESTABLISHED`**；本次启动的 dmesg 扫描 0 存储故障（分类器已修假阳性）；
  FAT 提示 `Volume was not properly unmounted` 已澄清为"粘滞挂载状态位"、**非控制器故障**；
  完整 ext4 `e2fsck` 与物理离线 FAT 检查 `NOT_RUN`（板上无 dosfstools、需读卡器/授权）。
- **需要什么才能关闭**：完整离线介质检查（需读卡器与工具授权）或更换介质后的连续无故障观察；
  在关闭前，每次物理派发仍须走 G1 存储预检（"读取失败 ≠ 无错误"）。
- 来源：[2026-10-12-storage-audit-and-deploy-recovery.md](../reports/2026-10-12-storage-audit-and-deploy-recovery.md)、
  [s2-headroom-g2-2026-10-16/NOTES.md](../../evidence/rf-teaching/s2-headroom-g2-2026-10-16/NOTES.md) §P0。

## 2. 阻塞交付验收的未决项

### Q6 S2 正式 108 物理帧 + 12 离线负控
- **现状**：冻结声明为 `OFFLINE_VERIFIED`，`expected_frames=108`；12/12 离线负控已通过；
  **正式 108 帧与真人 `NOT_RUN`**。且强门复核确认：当前没有任何候选能合法通过该 v3/40 dB 环境声明
  （b82 合同不匹配；`check_candidate` 在联网前即拒）。
- **需要什么才能关闭**：能通过环境合同的候选被安装 → 18 帧开发 pilot **全通过**（`known_correct=9`、
  `negative_false_clear=0`、安全关闭、同 IQ 重放通过）→ 以生产 `build()` 重建 freeze → 绑定实测 N1–N12 收据 →
  再安排 108 物理帧与真人验收。**任何 TEST 失败按冻结合同留失败，不调参覆盖**。
- 来源：[s2-freeze-declaration-2026-10-14/receipt.json](../../evidence/rf-teaching/s2-freeze-declaration-2026-10-14/receipt.json)、
  [2026-10-15-deepseek-double-review-followup.md](../reports/2026-10-15-deepseek-double-review-followup.md)。

### Q7 18 帧开发 pilot 失败后的路径
- **现状**：唯一一次 pilot 在第 2 帧 `CLIPPED` 停止（`unsafe_or_incomplete_capture`）；按规则未重试、未补采；
  正式 freeze `NOT_REACHED`。削顶根因**未证明**（40 dB 域 + TX1→RX1 直连无外部衰减器）。
- **需要什么才能关闭**：先离线定位削顶成因或按合同给出新的固定域（例如另一档衰减/换域），
  再取新的单次授权与接线确认，重跑 18 帧 pilot；每一轮都要有独立只读读回。
- 来源：[s2-development-pilot-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-development-pilot-2026-10-15/NOTES.md)。

### Q8 真人验收
- **现状**：S1 真人功能项与照片已闭合，**触摸 P95 `NOT_MEASURED`**（用户已明确取消专门触摸计时，不作为阻塞项）；
  S2 及之后的真人项 `NOT_RUN`（见 acceptance-checklists-p1-p6.md 的 HUM 行）。
- **需要什么才能关闭**：按各阶段 `*-human-checklist.md` 在实体屏上执行并留证据（照片 + 用户文字确认）；
  软件点击路径不等于真人操作。
- 来源：[docs/requirements/acceptance-checklists-p1-p6.md](../requirements/acceptance-checklists-p1-p6.md)、
  [docs/reports/2026-10-04-s1-human-photo-review.md](../reports/2026-10-04-s1-human-photo-review.md)。

### Q9 S3 / S4 与统一发布清单
- **现状**：S3 人工建议/拒绝链 `NOT_IMPLEMENTED`；S4 演示交付 `NOT_RUN`；
  统一发布清单（RF 软件/PL/daemon/模型/策略一次核对）`NOT_RUN`；回退版本只记到父版本 `b67a8aaa…`。
- **需要什么才能关闭**：按 [2026-10-13-final-prj-implementation-plan.md](../superpowers/plans/2026-10-13-final-prj-implementation-plan.md)
  的 P5/P6（Chunk 5–7）推进，每阶段过六类门；统一发布清单需一次核对全部受控件摘要并写回收据。
- 来源：[docs/requirements/brief-traceability.md](../requirements/brief-traceability.md) B9/B10。

## 3. 卫生与对账项（不阻塞物理动作）

### Q10 P1 板端运行包的状态对账
- **现状**：2026-10-14 已有 RED/GREEN/独立复审与板端预检（含 12 例冻结判据）；但
  `docs/requirements/brief-traceability.md` 仍写着"B1/B3 未开始"（该表成文早于当日结论）。两处需一次对账。
- **需要什么才能关闭**：由文档同步者在 brief-traceability 里更新状态（或注明"以收据为准"），并保持与
  [05-evidence-index.md](05-evidence-index.md) §B 一致。**本目录不改那份需求文档。**

### Q11 未跟踪的巨型 pytest 临时树
- **现状**：`evidence/rf-teaching/s2-headroom-g3-2026-10-16/`（5,713 文件 / 167 MB）原样保留，
  内含测试自造的夹具而非交付物；`.gitignore` 不覆盖 `pytest-tmp*`。
- **需要什么才能关闭**：一次显式决定（保留并登记为噪声 / 在确认无引用后删除）。
  在决定前**不得**擅自删除（会抹掉该轮唯一的运行痕迹）。
- 来源：[s2-headroom-g3b-2026-10-17/NOTES.md](../../evidence/rf-teaching/s2-headroom-g3b-2026-10-17/NOTES.md) §1.3。

### Q12 本目录自身的维护
- **现状**：`docs/memory/` 建立于 2026-10-17，内容截至该日。
- **需要什么才能关闭**：每完成一个阶段（新收据/新报告）后由**非实现者**追加一行摘要；
  修订旧条目须保留原文与日期。
