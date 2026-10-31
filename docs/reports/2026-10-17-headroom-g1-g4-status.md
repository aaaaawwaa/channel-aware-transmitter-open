# HEADROOM G1–G4 链路状态报告（2026-10-17，可交接）

- 报告日期：2026-10-17（Asia/Shanghai）
- 观测时点：2026-10-17 11:20–11:33（Asia/Shanghai；以下所有"现行"均以此区间实测为准；G4 独立复审在该区间内仍在执行，见 §6.5）
- 工作区：`<LOCAL_PATH>`，分支 `codex/description-aligned-platform-plan`，观测起点 HEAD = **`cc10b02`**（观测期间并行任务又提交 `872d832` dispatch-prep 与 `0463c18` project-memory，均与本报告 §2–§7 的事实无关；G4 复审目录直至观测结束仍为未跟踪）
- 报告性质：**只读盘点 + 文档同步**。本轮未连接板卡（无 SSH/sftp/上传/安装/切换发布/布防/发射/采集/Overlay/MMIO/SPI），未消耗任何授权或 nonce，未修改产品代码/测试/证据/被封存件，未 `git commit`。本文件是 `docs/reports/` 下**唯一新增**；`docs/superpowers/` 下的计划/规格**一个字节未改**（只在本报告 §8 标注）。
- 与既有事实的口径：本报告只回答"现在能证明什么"；历史阶段（10-12/10-13/10-15）的收据与报告仍按原样有效，不作改写。

## 0. 结论分级（一句话）

| 级别 | 内容 | 关键证据 |
|---|---|---|
| **已实现且经独立复审 APPROVED** | **G1** 夹具帧形态 re-home（`6573f259…`；绑定 `critical-review.json` sha256 `99d7d3ef…`）；**G3** final-safety/seal-stage 的"当前已安装发布"期望解析（b82 由原安装证据链 + 执行时只读读回核准；b67 保留候选构建父/历史恢复父含义）；**G3b** 两条 fail-open 变异钉桩测试（delta 复审 APPROVED） | §2、§4、§5 |
| **已实现但仅部分验证** | **G2** 隔离 ARM 资格 PASS（p95 **272.785 ms** / RSS **31728 KiB** / parity 空 / 零物理帧，绑定 `aba80e92…`）；**G3** 板端只读安全检查重跑 PASS（rc=0、45 项读回 0 漂移） | §3、§4.3 |
| **进行中 / 未完成** | **G4 封存准备**：ARM 证据就位、源冻结重冻结、combined-tests 重生成**已完成**；独立复审（重出绑定）、`install-bundle`、`development-checkpoint`、`stage-checkpoint` **未完成** | §6 |
| **NOT_RUN / 未证实** | 安装新候选、切换发布、布防/发射/采集、46 dB 研究本体（无被接受帧、无削减判定）、S2 正式 108+12、真人验收、S3/S4；板卡无 RTC（板钟偏差 ≈ **4.87 年**）；`boot_id` 在 10-15→10-17 之间变化（板卡重启过，原因未建立）；MMC 根因 `NOT_ESTABLISHED` | §7 |

**一句话**：G1–G3b 的安全/证据链已在离线与独立复审层面闭合；G4 尚差"新独立复审重出绑定 + 封存三件产物"，因此**新候选 `aba80e92…` 目前仍不得称"已安装"或"已封存"**，板卡仍运行 b82。

---

## 1. 观测基准与工作树状态

| 项 | 实测 |
|---|---|
| HEAD | 观测起点 `cc10b02`（G4 证据入库提交，2026-10-17T11:20:25+08:00）；观测期间并行提交：`872d832`（dispatch-prep runbook）、`0463c18`（project-memory），均不改变本报告引用的 G1–G4 事实与文件摘要 |
| `git status --porcelain`（观测区间） | 本报告写入前有 `?? evidence/rf-teaching/s2-dispatch-prep-2026-10-17/`、`?? evidence/rf-teaching/s2-headroom-g4prep-2026-10-17/review/`；写入期间又出现 `?? docs/memory/`、`?? docs/superpowers/plans/2026-10-17-dispatch-runbook.md`、`?? evidence/rf-teaching/s2-headroom-g4prep-2026-10-17/work/review-pytest-tmp/` 等（前两者随后被并行任务提交）——**全部来自并行 agent**，与本报告无关；G4 复审目录在观测区间内仍在写入 |
| 板卡当前发布（历史只读读回） | `b82b141e355b4cb1833969383a409c79`（`current` = `active-release`；最近一次实际执行读回 = 2026-10-17T02:33:52Z–02:33:59Z，见 §4.3） |
| 新候选 | `aba80e92aec4ed3d4e83196733a800ae`（`cnn-bpsk-headroom-v1`，46 项 software；父 b67）——**未安装** |
| 本轮板卡接触 | **零**（本报告作者全程未连板；G2/G3 的板卡数据取自 2026-10-16/10-17 已保存原始证据） |

---

## 2. G1 — 被 pin 的假链夹具帧形态 re-home（已复审 APPROVED）

### 2.1 问题与修复

真板帧实测外层**恰 10 键**、`headroom` 内层 **19 键**，四个追加证据键（`native_error/closure/timing_scope/timing_order`）都在 `headroom` 内（daemon 写入点 `rf_overlay/sw/board_app/rf_daemon.c:2130-2142`，第二次 `snprintf` 起点 `d->frame+n-2`）。被 pin 的夹具 `headroom-fake-chain.py` 却把这四键放在**外层**（14 键 / headroom 15 键），导致 `qualify-runtime.py` 的主机侧研究链在 attempt 1 就被具名拒绝（`the frame carries native_error outside headroom`，即缺陷 #2 的镜像）。

- 修复：只搬层级（**+10/−6 行**），未改任何判据/断言/取值/IQ 载荷；夹具 `f5336594…` → **`6573f25991b89b8b77fceaa46e9abce3f33247b863e678f6908901dfe246f6bb`**（14942 B，保持 CRLF）。
- 证据：`evidence/rf-teaching/s2-headroom-g1-2026-10-16/NOTES.md`（§1–§6）；差分证明 `logs/02-shape-diff.json`（8 用例、33 条帧回复、`differences=[]`、verdict PASS）；红/绿 `logs/01-red-chain-frame-shape.log`（19 failed / 1 passed）→ `logs/03-green-chain-frame-shape.log`（**21 passed**）。

### 2.2 主机侧研究链已走通（零板端接触）

`logs/05-host-segment.json`：`passed=true`、`board_contact=false`、守卫计数 `{load_helper:0, socket:0}`；8 个用例全部 passed、`full-five: completed=true / attempts=5`（off-before→normal→narrowband→pulse→off-after）、96 份证据件、`chain_parity_mismatch=[]`；停止点固定在 `helper = load_helper(); client = helper.connect()` **之前**。`logs/04-A2-old-sha.log` 证明 pin 是双向的（旧 sha 也不能放行新夹具）。

### 2.3 独立关键复审（重出绑定）APPROVED

- `evidence/rf-teaching/s2-headroom-g1-2026-10-16/review/REVIEW.md`：`verdict = APPROVED`（就 delta）；复审者独立复算 40 帧重建字节恒等 40/40、六处 reader 变异全被抓、真实 gcc 原生租约回归（16 passed，两处生产 C 变异各 2 failed）。
- 重出后的绑定：`evidence/rf-teaching/s2/executable-candidate-2026-10-16/python/critical-review.json` 文件 sha256 = **`99d7d3eff22cb7471f82fb99197195077b28af17c1c92424f744d188e23abf9b`**（verdict APPROVED、candidate `aba80e92…`、`harness_sha256 = 6573f259…`、46 项 `source_sha256`、6 项 `tools_sha256`）；其 `report_sha256` = `critical-review.md` 文件 sha256 `cab69f66…`。
- `source-freeze.json` 按复审授权 22 → 23 项（纳入新守卫测试 `scripts/tests/test_rf_headroom_chain_frame_shape.py`）。
- 全量回归（复审时）：`scripts/tests` + `pynq_overlay/sw/tests` = **2867 passed / 6 skipped / 0 failed**（收集 2873，464.28 s）。

### 2.4 提交与遗留

- 提交：`8935c6d`（fix）+ `f451a6a`（docs 复审 APPROVED 入库）。
- 遗留（当时声明、后续已变化）：G1 阶段尚未跑 ARM 侧链与 `check_chain(host,arm)`——该缺口已由 **G2** 补上（§3）；G1 阶段 `combined-tests.xml` 为 2873 用例，已被 G4prep 重生成至 **2901**（§6）。

---

## 3. G2 — 隔离 ARM 资格（已实现，仅部分验证）

### 3.1 结论 PASS（绑定新候选 `aba80e92…`）

单次执行、未重试（2026-10-16T12:45:48.73Z → 12:46:49.80Z，退出码 0）。只做板端 `/tmp` 隔离计算：**不安装、不切换发布、不布防/发射/采集、不派发研究、不消耗 nonce**；板卡零新物理帧（`new_rf_frames=0`、`initialization_count=0`）。

证据：`evidence/rf-teaching/s2-headroom-g2-2026-10-16/NOTES.md`、`work/qualify-runtime/receipt.json`、`logs/p1-qualify.log`（236 行）、`p0-preflight.json`、`p2-readback.json`、`p2-verdict.json`、`p2-cleanup.json`；提交 `6fb9d37`。

| 字段 | 值（绑定 `aba80e92aec4ed3d4e83196733a800ae`） |
|---|---|
| `passed` | **true** |
| `software` | **46** 项（= 候选 manifest） |
| `p95_ms` | **272.7850274010052**（100 个 timings 主机侧重算，≤300） |
| `rss_kib` | **31728**（≤65536） |
| `native_cnn` | **true** |
| `parity_mismatches` / `chain_parity_mismatches` | **[]** / **[]** |
| `new_rf_frames` / `initialization_count` | **0** / **0** |
| `board_contact` | true（但仅 /tmp 隔离计算） |
| 两侧研究链 | host 与 ARM 各 8 用例全 passed、各 96 证据件；`simulation=true`、`hardware_touched=false` |

板端 P0/P2：`current = active-release = b82b141e…`、双服务 active/running/NRestarts 0、RF safe（未布防/未选 TX 源/未武装 RX）、`/` 与 `/boot` rw、dmesg **0 存储故障**；`/tmp` 隔离目录按精确路径删除后 `NONE_LEFT`，新会话读回 45 项一致。同阶段收尾（F1–F4）：夹具漂移用例补摘要断言、`native-reuse.json` 重生成（摘要不变）、`combined-tests.xml` 重生成（当时 2873 收集 / 2867 passed）、SEAL-STATUS 与 REQUEST 计数更正。

### 3.2 为什么仍只是"部分验证"

- 该 PASS 是**隔离 /tmp 计算资格**，不是板端安装、不是发布行为、不是真实 46 dB 研究；候选 `aba80e92…` 至今未安装（§7）。
- 同阶段明确声明：`arm-supervisor`、`final-safety`、`install-bundle` 对新候选**仍不存在**（现存者仍绑定 b82）；该缺口后来由 G3/G4 部分推进（§4、§6）。
- G2 报告的 `combined-tests.xml` 数字（2873/2867）为历史值；**现行**为 **2901 / 2895 passed / 6 skipped / 0 failed**（G4prep 重生成，§6.2）。

---

## 4. G3 — final-safety / seal-stage 的"当前已安装发布"期望解析（已实现且独立复审 APPROVED）

### 4.1 被修的缺陷（事实）

修复前 `final-safety.py:16` 硬编码 `EXPECTED='b67a8aaa…'`，`seal-stage.py:68/:95` 也把"当前已安装发布"硬约束为 b67；而板卡自 2026-10-13 起安装的是 **b82b141e…**。活证据：`evidence/rf-teaching/s2/executable-candidate-2026-10-16/final-safety/`（修复前那份 `passed=false`、`expected_release=b67a8aaa…`、`error="safe state checker failed"` 的收据）已归档到 `evidence/rf-teaching/s2-headroom-safetyrerun-2026-10-17/archived/final-safety-20261017T023344Z/`（6 文件 / 40580 B）。

### 4.2 修复与复审

- 实现：`scripts/rf_headroom_deploy.py` **+173/−1**（唯一删除行是改为 `from scripts.rf_teaching_install_state import safe_state, verified_state`；`verify_parent/verify_gates/verify_bundle/install` 一字未改）；`final-safety.py` **+71/−18**；`seal-stage.py` **+16/−5**；新增 `scripts/tests/test_rf_headroom_installed.py`（25 用例）。提交 `41e252b`（+ chore `5c22155`）。
- 核准链（离线可复算，全部真实文件）：b82 原始安装验收收据 `s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json`（`accepted=true`、`verdict=VERIFIED_SAFE_NEW_RELEASE`、`new_rf_frames=0`）→ `installer_receipt.sha256 = 9dfb0ade…` → `s2-parent-b82-proof-2026-10-15/install-receipt.json`（实测摘要相等、`outcome=INSTALLED_VERIFIED`）→ 其绑定的 `readback-state-new.json`（sha `e15f5e4b…`、`verified_state(state,b82)==true`）。`approved` 恰好 `(b82, b67)` 两个发布，没有第三条放行路径。
- **b67 历史含义保留**：`RECOVERED_PARENT` = b67 值未变，仍是候选构建父（`seal-stage.py:29` 断言）与历史恢复父（`verify_parent` 恢复证明路径、final-safety 第二次读回、`rollback_parent`）；`ROLLBACK_PARENT` 仍为 adfac。
- 期望标记：离线真实文件（b82 证据链、2026-10-13 安装期读回、2026-10-16 历史快照、2026-10-12 b67 恢复态、2026-10-15 环境候选 399b 负例）；**执行时读回**（`state.json`/`state-parent.json`、`boot_id`、`storage_faults`、`root_read_write`）由真板执行时提供。
- 复审：`evidence/rf-teaching/s2-headroom-g3b-2026-10-17/review/REVIEW.md` 首轮结论 **CHANGES_REQUIRED**——产品逻辑本身正确，唯一阻塞是 11 条自设变异中 **M08**（approve 侧删 `verified_state`）与 **M11**（`len(names)!=1`→`<1`）两处**未被测试钉住**（已构造可复现 fail-open 反例）；`M06` 经证明为拒绝等价、不构成缺陷。最小修法 = **纯新增测试**，不改产品逻辑。

### 4.3 G3b — 两条 fail-open 变异钉桩（delta 复审 APPROVED）

- 新增测试：`scripts/tests/test_rf_headroom_installed.py` **+60 行**（提交 `9203d63`），含 **2 条专钉 M08 / M11 的 fail-open 变异** + 1 条钉 M06 顺序语义（共 3 条新用例）；产品文件一字未改（deploy `26b3e5cc…`、final-safety `6d01bca1…`、seal-stage `4ca4202a…`，与 G3 复审基线逐字节相同）。docs 提交 `965e2a8`。
- delta 复审：`evidence/rf-teaching/s2-headroom-g3b-2026-10-17/review-delta/REVIEW.md` 结论 **APPROVED**，0 阻塞。复审者在**仓库外副本**亲手施加 M08/M11：均复现 fail-open（ACCEPTED），且**恰好**是两条新测试 FAILED（DID NOT RAISE），其余 27 条全过；另 M08b 被既有测试抓住、M06-ORDER 被第三条新测试抓住。
- 回归：定向 **28 passed**；全量 `scripts/tests` **747 collected / 746 passed / 1 skipped / 0 failed / 0 error**；junit 逐文件对比与上一轮仅差 `test_rf_headroom_installed` 25→28，无删除、无既有文件计数变化。
- 未采纳的 3 条非阻塞建议（需改产品代码，超出"只许改测试"授权）：把 `boot_id` 写进状态文档、改名 `test_..._historical_document...`、给验收收据加锚。

### 4.4 板端只读安全检查重跑（PASS，仅部分验证）

用经独立复审的 `final-safety.py`（sha `6d01bca1…`）在真板**只读**重跑（2026-10-17T02:33:52.622Z→02:33:59.377Z，rc=0，约 6.8 s）：

- 收据 `evidence/rf-teaching/s2/executable-candidate-2026-10-16/final-safety/receipt.json`：`passed=true`、`installed_release = b82b141e…`、`installed_release_source = original-installation-evidence`、`approved_releases = [b82, b67]`、`safe=true`、`service_units_match=true`、`current_boot_storage_errors=[]`、`root_read_write=true`、`boot_id == readback_boot_id = 75e4522b-59ad-47a6-8c7a-a5a8a57f964f`。
- P0/P3 两次独立预检逐字段比对 **45 项、0 漂移**；`/tmp` 仅一处 checker/state 写入，已按精确路径删除并复查 `NONE_LEFT`；`/var/lib` 151 项中本启动以来无板钟写入（研究账本/派发标记均停在旧启动）→ **未消耗授权/nonce**。
- 证据：`evidence/rf-teaching/s2-headroom-safetyrerun-2026-10-17/NOTES.md`、`p0-precheck.json`、`p3-precheck.json`、`work/p3-compare.json`、`archived/`；提交 `b6ac15b`。
- **仅部分验证的原因**：只执行了 `final-safety.py` 的只读路径；`seal-stage.py` 的写模式（`--development` 生成 development-checkpoint、封存 `stage-checkpoint`）与 `--verify` **从未运行**；`install-bundle` 对新候选不存在。故"执行时读回"这一新语义已在真板验证一次，但**完整封存流程未验证**。

---

## 5. G3 与 G4 之间的状态补充（板卡发布史，避免误读）

- 2026-10-13：b82 原样安装并核验（`s2/headroom-install-2026-10-13/attempt-1/`）。
- 2026-10-15：环境候选 399b 安装曾把 `current` 切到 `399b300e…`；同日经用户授权、用已审查原语**原子切回 b82**（`evidence/rf-teaching/s2-headroom-switch-2026-10-15/NOTES.md`，`procedure_reviewed=false` 如实披露，即"把原语串起来的编排"未独立复审）。
- 2026-10-16（G2 P0/P2）与 2026-10-17（G3 重跑 P0/P3）的独立只读读回均为 `b82b141e…`。
- 因此："b82 是当前已安装发布"这一结论在 **G3 的实现语义**上来自"原安装证据 + 执行时读回"两条独立依据，而不是某个常量；b67 仍保留其历史含义。

---

## 6. G4 — 封存准备（进行中，分项状态）

证据目录：`evidence/rf-teaching/s2-headroom-g4prep-2026-10-17/`；提交 `cc10b02`（提交信息即"绑定待独立复审重出"）。

| # | 封存输入/动作 | 状态 | 现行证据 |
|---|---|---|---|
| G4.1 | ARM 证据按来源就位（`s2/executable-candidate-2026-10-16/arm-runtime/`） | **已完成** | `logs/02-arm-runtime-copy.log`：267 文件 / 8,408,627 B、逐文件 **0 mismatch**；receipt `release_id=aba80e92…`、`software=46`、`new_rf_frames=0`、p95 272.785 ms、RSS 31728 KiB、`native_cnn=true`、parity/chain_parity 空；13 项 evidence sha、两侧各 96 件 chain artifacts、63 项 upload、53 项 release 成员全部核验相等 |
| G4.2 | `source-freeze.json` 重冻结 | **已完成** | `logs/03-refreeze.log`：23 → **24** 条，`35dae4be…` → **`fdbf6430c21d47b94877805c3ab8cd67748ea869d50b3eaf3448ea5b6f8729a5`**；更新 4 条陈旧摘要（final-safety.py / seal-stage.py / rf_headroom_deploy.py / chain_frame_shape 测试）+ 新增 `test_rf_headroom_installed.py`；software(46)/native(58)/original_unknown 三类身份字段与摘要未变。旧文件归档于 `archived/source-freeze.json.pre-g4prep.json` |
| G4.3 | `combined-tests.xml` 重生成 | **已完成** | `logs/04-combined-tests.log`：**2901 收集 / 2895 passed / 6 skipped / 0 failed / 0 error**，exit 0，437.71 s；sha `31d71b2b…` → **`1980fbca890497e3f997a5063fb96b047c645caec626f385605fdb2543014bfe`**；旧 XML 归档于 `archived/combined-tests.pre-g4prep.xml` |
| G4.4 | 封存输入一致性核验 | **已完成** | `logs/05-native-reuse-check.log`（58 项原生 == 树 == build；`rf_daemon` sha `bbfd0409…` 三处相等；native spec/quality review 均 APPROVED 且 `source_equals_tree=true`，报告/文摘相等）、`logs/08-input-blocks.log`（A–G 七块全 ok；final-safety receipt passed、arm-supervisor `simulation=true/actual_install=false`） |
| G4.5 | **独立复审（重出绑定）** | **进行中** | `logs/06-progress-probe.log` 与 `logs/07-review-binding-diagnosis.log` 定位唯一活门：`qualify-runtime.check_chain_reviews` 报 `stale Python candidate/harness review: critical-review`，原因 = 现行 `critical-review.json`（G1 重出版 `99d7d3ef…`）里 `tools_sha256["scripts/rf_headroom_deploy.py"] = 174f12a5…`（G3 改动后实际 `26b3e5cc…`）、`seal_stage_sha256 = 6ade63b8…`（实际 `4ca4202a…`）；其余绑定字段（report/harness/qualifier/runtime_runner/source/native_lease）全部相符。观测区间内 `evidence/.../s2-headroom-g4prep-2026-10-17/review/` 正在产出审计日志（`00-gate-field-diff.log`、`01-g3-*-diff.log`、`10-arm-runtime-vs-g2-source.log`、`11-arm-runtime-receipt-audit.log`、`20-source-freeze-diff.log`、`21-guard-test-delta.log`、`30-combined-tests-audit.log`、`31-my-full-suite.log`、`40-native-58-inputs.log`、`41-native-lease*.log`、`43-chain-probes.log` 等）；截至观测时点 11:33 **尚未出现 `REVIEW.md`/重出后的 `critical-review.json`**，复审仍在运行——本报告不能替它宣布 APPROVED |
| G4.6 | `install-bundle/` | **未完成** | `logs/06-progress-probe.log`：`verify_bundle` → `FileNotFoundError … install-bundle/bundle-receipt.json`；`seal-stage.py:98` 写模式先 `D.verify_bundle(BASE/'install-bundle')` |
| G4.7 | `development-checkpoint.json` | **未完成** | 文件不存在（`seal-stage.py:90` 的 `--development` 目标）；`stage-checkpoint` 的 artifacts 集合会引用它（`:125`） |
| G4.8 | `stage-checkpoint.json` | **未完成** | 文件不存在；`seal-stage.py current()` 当前仍因 G4.5 失败（`logs/06-progress-probe.log`），且写模式在 `current()` 之后还要 `verify_bundle` |
| G4.9 | `_native_reuse.json` 等既有输入复核 | **已完成（无需改写）** | `logs/05-native-reuse-check.log`：`reuse_unchanged=true`（内容逐字节不变，只是时间戳陈旧的问题已被判明为不影响断言） |

**G4 结论：封存未完成。** `seal-stage.py --verify` 未以任何模式运行；`stage-checkpoint.json`/`development-checkpoint.json` 未创建；`install-bundle` 未生成。**`aba80e92…` 不得称"已安装"或"已封存"。**

---

## 7. NOT_RUN / 未证实 / 不得声称（必须保留）

### 7.1 物理动作与发布

- **安装新候选 `aba80e92…`：NOT_RUN**。**切换发布：NOT_RUN**（`current=active-release=b82`）；**布防/发射/采集：NOT_RUN**（G2 仅板端 /tmp 隔离计算；G3 仅只读状态检查；G1 全程零板端接触）。
- **`aba80e92…` 在封存完成前不得称"已安装/已封存"**；b82 仍是板上唯一已安装发布。
- 不重跑、不补采、不复用旧授权/nonce：2026-10-15 第二次派发已消耗 nonce（`ledger_seq=1`，`phase=AUTHORIZATION_CONSUMED`），授权窗口与接线确认均已过期。

### 7.2 46 dB 研究本体（无被接受帧、无削减判定）

- 第一次派发（2026-10-15）：G0 被板卡 RTC 判窗拒绝，`RUN_NOT_STARTED_PREFLIGHT`、零初始化/零帧、nonce 未消耗。
- 第二次（也是最后一次授权）派发（2026-10-15T12:11–12:14Z）：1 次初始化 / 1 次尝试 / 实取 1 帧原始 IQ（16384 B，sha256 `12e90da7…`），但证据谓词拒绝（`actual_rf_after still has SRC_SEL or CAP_ARM set`，源于 `cap_cfg=4` 被整字误读），终态 `RUN_STOPPED_EVIDENCE:1`、exit 3；**无被接受的帧、无 `analysis.json`、无削顶/rail 判定**；`zero_frame_claim.supported=false`（不能写"未触板"或"46 dB 无削顶"）；nonce 已消耗，未重试。
- `cap_cfg` 整字误读随后被**离线合同澄清**（具名位语义：`SRC_SEL/CAP_ARM` 必须 0、commit epoch bit2 允许 0/1、`SMP_WRAP/CAP_CONT` 与 bit3+ 仍拒），并实现为新候选 `aba80e92…`：`docs/superpowers/specs/2026-10-10-s2-headroom-research-design.md:52-80`、`docs/superpowers/plans/2026-10-12-real-headroom-research-runner-design.md:110`；提交 `8316fc0` + `205d0d9`。这是**合同澄清 + 新候选资格**，不构成 46 dB 研究结论。

### 7.3 板卡时间与健康

- **板卡无 RTC/NTP**：`date -u` 读数 `2021-12-03T23:00:29Z`，与主机 UTC `2026-10-17T02:37:33Z` 相差 **153,632,223 s（≈4.87 年）**；板钟时间戳不可作墙钟证据，封存/派发的"时间窗"必须用主机 UTC + `boot_id`/`uptime` 锚（`evidence/rf-teaching/s2-headroom-safetyrerun-2026-10-17/NOTES.md` §4）。
- **`boot_id` 在 10-15→10-17 之间变化**（板卡重启过，原因未建立）：
  - 2026-10-15：`f9f6b032-f782-4767-821b-bae13d1057f0`（attempt1/attempt2/switch/restore 均同值，期间未重启）
  - 2026-10-16：`d39c6371-9905-4255-aa99-cfa6de6e619a`（G2 P0/P2，uptime 14074.91 s）
  - 2026-10-17：`75e4522b-59ad-47a6-8c7a-a5a8a57f964f`（G3 重跑，uptime 7080.05 s）
  - 至少发生两次重启；**重启原因 `NOT_ESTABLISHED`**。历史 dmesg 中 FAT 粘滞脏位与 journald "uncleanly shut down" 与既往非正常断电同源，但**不构成 MMC/控制器故障证据**。
- **MMC/存储根因：`NOT_ESTABLISHED`**；完整文件系统检查/修复 **`NOT_RUN`**。本轮（G2 P0/P2、G3 P0/P3）dmesg 存储扫描均为 **0 故障**，仅证明"本次启动"健康，不是长期稳定性结论。

### 7.4 研究/验收/交付

- **S2 正式 108+12：NOT_RUN**；**真人验收：NOT_RUN**；**S3/S4：NOT_RUN/NOT_IMPLEMENTED**。
- 环境候选（399b）安装/回退属 2026-10-15 独立事件：当前板上不是 399b；**不得**据此称环境合同已获正式采集资格。
- 46 dB 研究结论、削顶判定、环境候选资格、正式 S2 完成、真人通过——**一项都不成立**。

---

## 8. 既有计划/规格文档中已过时或被推翻的表述（只标注，本轮未改）

> 说明：以下为**只读核对结果**。本轮**没有**修改这些文件；修正与否、以及如何修正，交由文档所有者/后续任务决定。

| # | 文件:行 | 原文要点 | 现行事实（附证据） |
|---|---|---|---|
| 8.1 | `docs/superpowers/plans/2026-10-12-real-headroom-research-runner-design.md:9` | "真实 46dB 研究 **不可能执行**（当前已安装候选的语义缺陷）" | 已不可能 → 已可执行：b82（修复 `lease_held` 语义）已于 2026-10-13 安装（`evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json`），且研究已于 2026-10-15 真实派发两次（第二次进入 1 初始化/1 尝试/1 帧，见 `evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/NOTES.md`）；仍无**被接受的帧** |
| 8.2 | 同上 `:10` | "真实 runner **不存在**（`scripts/rf_headroom_research_run.py` 实测不存在）" | 现行存在：`scripts/rf_headroom_research_run.py`（runner 1.0.0/1.0.1），且已真实执行过两次派发（同上证据） |
| 8.3 | 同上 `:11` | "新候选 `b82b141e…` **未安装**（安装状态 NOT_RUN）" | b82 自 **2026-10-13** 已安装并核验；2026-10-15 曾短暂切到 399b，同日授权切回 b82；10-16/10-17 只读读回均 `current=active-release=b82`（§5、§4.4） |
| 8.4 | 同上 `:31` | "**旧候选（已安装的 b67a8aaa…）**" | 现行已安装发布是 **b82**（同上）；b67 仅保留候选构建父/历史恢复父含义 |
| 8.5 | 同上 `:34` | "b82 … **尚未安装**，且其 ARM G1、独立关键审查、安装包封存均为 NOT_RUN" | b82 的 ARM G1、独立关键审查（APPROVED，report `88d74459…`）、`install-bundle` 封存均在 2026-10-12 完成、2026-10-13 安装（`docs/reports/2026-10-13-headroom-install-execution.md`）；当前新候选 `aba80e92…` 的 ARM 资格（G2）与独立复审（G1）也已完成，但**封存未完成**（§6） |
| 8.6 | 同上 `:36` | "**装上的是旧语义发布，跑的是新语义设计**；真实路径保持 NOT_RUN" | 现行板上是 b82（新语义），其只读能力判定 `capability_available=true` 已实测（`evidence/rf-teaching/s2-headroom-switch-2026-10-15/NOTES.md`）；真实研究已执行到证据检查（仍无被接受帧），不再是"完全无法执行" |
| 8.7 | 同上 `:363`、`:442` | 假硬件夹具复用 `executable-candidate-2026-10-12/python/headroom-fake-chain.py`（sha `f5336594…`） | 10-12 那份副本**仍逐字节为 `f5336594…`**（历史件，未改，其描述对 10-12 文件仍成立）；但**当前阶段被 pin 的夹具**是 10-16 阶段 re-home 后的 **`6573f259…`**（§2）。若按本节直接引用 10-12 路径作为"当前被 pin 夹具"，已被取代 |
| 8.8 | 同上 `:383` | "新语义在真板上的实际行为：新候选未安装 → **TRULY NOT_RUN**" | b82 已安装，只读读回证明 `capability_available=true`；且真实研究已运行到证据检查（终态 `RUN_STOPPED_EVIDENCE:1`）。"租约语义在真机为真"已被只读与真实派发间接验证；**其余 4 帧行为仍未验证** |
| 8.9 | 同上 `:386` | "新候选 ARM G1：NOT_RUN；禁止继承旧 b67 的 270.793ms" | b82 的 ARM G1 于 2026-10-12 通过（P95 278.835 ms / RSS 31704 KiB，见 `docs/reports/2026-10-12-headroom-executable-candidate.md`）；当前候选 `aba80e92…` 的隔离 ARM 资格也已 PASS（G2，P95 272.785 ms / RSS 31728 KiB，§3）。"禁止继承 b67 结论"的纪律仍有效 |
| 8.10 | 同上 `:388` | "最终只读安全读回在真板上执行：NOT_RUN（旧 b67 的读回不能当作新候选的读回）" | 已执行：b82 安装后 2026-10-13 独立只读读回，以及 2026-10-17 `final-safety.py` 只读重跑 PASS（§4.4）；b67 历史读回确实不能顶替，但"未执行"已不成立 |
| 8.11 | 同上 `:401` | "无 ARM G1 / 无封存：新候选的 ARM 资格、独立关键审查、安装包封存均 NOT_RUN" | 对 b82 全部已完成（10-12/10-13）；对 `aba80e92…`：ARM 资格（G2）与独立关键复审（G1 重出）已通过，**封存仍在进行（§6）**，故"NOT_RUN"已不准确 |
| 8.12 | 同上 `:414-415` | 流程图节点 "新候选独立关键审查 → ARM G1 → 打包封存（当前 NOT_RUN…）"、"单次安全安装 + 独立只读安全读回（**b82b141e… 当前未安装**）" | b82 的审查→ARM→封存→安装→独立读回整条链均已完成（10-12/10-13）；图注"当前未安装"已被推翻 |
| 8.13 | 同上 `:471` | "新候选 `b82b141e…` 修复租约语义、**未安装**（安装状态 NOT_RUN）" | 同 8.3：b82 已安装并核验；该表其余"已核实"项仍成立 |
| 8.14 | 同上 `:472` | "46 项软件 / 58 项原生 / **22 工具冻结摘要**" | 该 22 条属于 **10-12 阶段** `source-freeze.json`（历史值，仍是那 22 项）。**当前阶段**（10-16）冻结条目经 G1 22→23、G4prep 23→24，现为 **24 条**、文件 sha `fdbf6430…`（§6.2）。不要把 22 当作现行冻结规模 |
| 8.15 | 同上 `:482` | "真实 46dB 研究、真实 runner 执行、新候选 ARM G1 … NOT_RUN" | 真实 runner 已存在并执行过（两次派发）；新候选 ARM 资格（G2）已 PASS；真实 46 dB 研究**仍未成立**（无被接受帧），S2 正式/真人/S3/S4 仍 NOT_RUN，MMC 根因仍 `NOT_ESTABLISHED`。此条需拆分而非整条作废 |
| 8.16 | 同上 `:495` | "`evidence/rf-teaching/s2/executable-candidate-2026-10-12/source-freeze.json` — runner 的候选/摘要门以此为真源" | **现行**封存的真源是 `evidence/rf-teaching/s2/executable-candidate-2026-10-16/source-freeze.json`（24 条、sha `fdbf6430…`、候选 `aba80e92…`）；10-12 那份是历史阶段真源，不能作为当前门禁依据 |
| 8.17 | `docs/superpowers/plans/2026-10-04-teaching-environment-roadmap.md:5` | "**2026-10-12 当前**：部署超时后板卡恢复，**新 b67a8aaa 已实际安全启用**" | 现行已安装发布是 **b82**（2026-10-13 起；10-15 短暂切 399b 后同日授权切回）；板卡当前也**不是** b67（2026-10-17 只读读回，§4.4） |
| 8.18 | 同上 `:3` | "**2026-10-12 最新**：…最终只读安全检查 PASS（**仍 b67**、safe、unit 相符、root rw、0 存储错误）；…整阶段仍未结束" | 同上：该"仍 b67"是 2026-10-12 当时的事实；现行板上为 b82，"最新"标记之后已发生 10-13 安装、10-15 环境候选/回切、10-16/10-17 G1–G4 各阶段。文档顶部**没有** 10-13 之后的条目 |
| 8.19 | `docs/superpowers/plans/2026-10-15-s2-development-pilot-execution-package.md:3` | "**适用前提**：候选 `399b300e…` 已安装并通过独立读回（阶段 C）" | 该前提**已失效**：399b 仅在 2026-10-15 短暂作为 `current`，同日经授权切回 b82（`evidence/rf-teaching/s2-headroom-switch-2026-10-15/NOTES.md`）；10-16/10-17 只读读回均为 b82。此执行包不能按原前提直接执行 |
| 8.20 | 同上 `:24`、`:33` | 预检要求 `current=active-release=399b300e…`；命令以 399b 候选目录为参数 | 同上：现行 `current=active-release=b82`。按原表执行会在预检第 1 项即失败（且不允许绕过） |
| 8.21 | `docs/superpowers/plans/2026-10-14-headroom-physical-session-execution-package.md:143-149` | §10 "2026-10-15 单次派发实测发现（板卡时钟阻塞 G0）"；"解除阻塞需要二选一" | 该阻塞**已解除并已实测**：主机 UTC + `boot_id`/`uptime` 时钟锚已实现并经独立复审（提交 `5d2906a`），2026-10-15 第二次派发 **G0 通过**、锚龄 1.650 s、`board_clock_used=false`（`evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/NOTES.md` §0/§1）。§10 作为历史记录仍真实，但"仍被阻塞/需要二选一"的现状描述已过时；再次派发仍需**新授权 + 新 attempt_id + 新接线确认**（旧 nonce 已消耗） |
| 8.22 | `docs/superpowers/specs/2026-10-10-s2-headroom-research-design.md:7` | "**当前已安装候选** `adfac1f14…`，daemon 1.0.3、协议 1.5" | 现行已安装发布是 **b82**（daemon 1.0.4 / protocol minor 6 / `cnn-bpsk-headroom-v1`；2026-10-17 只读读回）。该行是 2026-10-10 的历史快照，"当前"措辞已被推翻 |
| 8.23 | 同上 `:32` | "## 后续独立研究合同（**规范，尚未实现**）" | 该合同**已实现并已执行**：b67/b82 候选具备 HEADROOM-1 研究动作；2026-10-15 两次真实派发（第二次 1 初始化/1 尝试）与独立的`cap_cfg`具名位澄清（`:52-80`，2026-10-16 正式澄清）均已落地。"尚未实现"已过时；但"研究本体未成立"仍成立（§7.2） |

### 8.24 封存阶段内部输入文件（不是 docs/，属 G4 更新范围；本轮未改）

`evidence/rf-teaching/s2/executable-candidate-2026-10-16/SEAL-STATUS.md` 仍在描述 2026-10-16 引脚迁移时的"未封存"状态，其中下列表述已被本轮事实推翻，**修正它属于 G4 动作，不是本报告动作**：

| 行 | 原文要点 | 现行事实 |
|---|---|---|
| :18 | `source-freeze.json` sha256 `da7a8c5b…` | 已重冻结为 `fdbf6430c21d47b94877805c3ab8cd67748ea869d50b3eaf3448ea5b6f8729a5`（24 条），旧值已归档 |
| :26-27 | "the **23** `files_sha256` entries" | 现行 **24** 条（§6.2） |
| :34 | `critical-review.json`/`.md` **absent** | 已由 G1 独立复审重出：`critical-review.json` sha256 `99d7d3ef…`、`critical-review.md` `cab69f66…`（APPROVED） |
| :43-46 | "`arm-runtime/`、`arm-supervisor/`、`final-safety/`、`install-bundle/` 均不存在（现存 arm-runtime 绑定 b82）" | `arm-runtime/`（267 文件，receipt 绑定 `aba80e92…`）、`arm-supervisor/`（passed）、`final-safety/`（passed）**均已存在**；仅 `install-bundle/` 仍缺（§6.6） |
| :48-52 | `headroom-contract-2026-10-11/native/{spec,quality}-review.{json,md}` 在工作区**缺失** | 已存在（2026-10-16 19:33 导入）：`spec-review`/`quality-review` 均 APPROVED，G4prep 复核 `source_equals_tree=true`、report/md 摘要相等（`logs/05-native-reuse-check.log`） |
| :56-58 | `test_the_qualified_release_pin_is_the_sealed_candidate_review` **expected to fail** | 不再失败：现行 `scripts/tests` **746 passed / 1 skipped / 0 failed**；合并套件 **2895 passed / 6 skipped / 0 failed**（§4.3、§6.3） |

---

## 9. 关联历史报告的状态更新（供交接时避免重复劳动）

- `docs/reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md` 的"第 1 步离线工作包"（`cap_cfg` 具名位澄清 + 缺陷 #2 合同变更 + 新候选）**已执行完毕**：提交 `8316fc0`（具名位）、`205d0d9`（缺陷 #2 合同变更包：谓词从 headroom 内读 4 键、夹具同形、一致性测试钉落点）、`a480bc8`（候选 `aba80e92…`）、`f26f49f`（引脚迁移）；其"明确标注 counterfactual/offline、不倒填旧 decision.json"的纪律被后续执行遵守（旧 `decision.json` 未改）。该报告的 §5"当前没有证明的事"中关于"无新版独立复审、无 46 dB 结论"的部分仍成立，但"离线工作包待做"已过时。
- `docs/reports/2026-10-15-s2-parent-upgrade-review-and-execution-plan.md` 的 b82 父子升级相关结论（b82 原安装证据链、readback 摘要）已被 G3 的核准链直接复用（§4.2），且 G3 已把"合同门不能直接删、但要把 b82/b67 核准内建到解析器"落实为代码 + 25 条测试。
- 2026-10-12 时期的"仍 b67 / b82 未安装"类收据与报告**保持原样**（历史真实性），不要据此判断现状。

---

## 10. 交接建议（G4 之后、物理动作之前）

1. **G4 顺序（必须先于任何安装）**：新独立复审重出 `critical-review.{json,md}` 绑定（覆盖 G3/G3b delta；新 `tools_sha256["scripts/rf_headroom_deploy.py"]=26b3e5cc…`、`seal_stage_sha256=4ca4202a…`）→ 更新 `SEAL-STATUS.md` 与（若需要）`critical-review.REQUEST.json` → 生成 `install-bundle/` → `seal-stage.py --development`（写 `development-checkpoint.json`）→ `seal-stage.py`（写 `stage-checkpoint.json`）→ `seal-stage.py --verify`。全部离线/主机侧，不连板。
2. 封存完成**之后**才有资格谈：安装新候选（单次、带独立读回）→ 切换/验证 → 新接线确认 + 新一次性授权（新 nonce、新 `attempt_id`、2h 窗口、主机时钟锚）→ 新预检 → 单次物理派发（最多 5 尝试/1 初始化）。任何一步失败按既有纪律保留证据、不重试。
3. 46 dB 研究本体、S2 正式 108+12、真人验收、S3/S4 均保持 NOT_RUN，**不因 G1–G3b 或 G4 封存而自动放行**。
4. 与封存并行、互不阻塞的下一阶段离线工件（授权工件再生成流程、runbook、时钟锚决策备忘）由并行任务 `dispatch-prep` 负责（本报告不重复其内容）。

---

## 11. 证据索引（相对仓库根）

| 阶段 | 路径 |
|---|---|
| G1 | `evidence/rf-teaching/s2-headroom-g1-2026-10-16/`（`NOTES.md`、`review/REVIEW.md`、`logs/02-shape-diff.json`、`logs/05-host-segment.json`、`logs/06-combined-tests.*`、`archived/`）；绑定 `evidence/rf-teaching/s2/executable-candidate-2026-10-16/python/critical-review.{json,md}` |
| G2 | `evidence/rf-teaching/s2-headroom-g2-2026-10-16/`（`NOTES.md`、`work/qualify-runtime/receipt.json`、`logs/p1-qualify.log`、`p0-preflight.json`、`p2-readback.json`、`p2-verdict.json`、`logs/f2b-*`） |
| G3（离线实现+复审） | `evidence/rf-teaching/s2-headroom-g3b-2026-10-17/`（`NOTES.md`、`review/REVIEW.md`、`review-delta/REVIEW.md`、`logs/full-scripts-tests.log`、`logs/junit-comparison.log`、`work/scripts-tests*.xml`） |
| G3（板端只读重跑） | `evidence/rf-teaching/s2-headroom-safetyrerun-2026-10-17/`（`NOTES.md`、`p0-precheck.json`、`p3-precheck.json`、`work/p3-compare.json`、`archived/final-safety-20261017T023344Z/`）；收据 `evidence/rf-teaching/s2/executable-candidate-2026-10-16/final-safety/receipt.json` |
| G4 | `evidence/rf-teaching/s2-headroom-g4prep-2026-10-17/`（`logs/01..08*.log`、`work/*-record.json`、`archived/`、进行中的 `review/`）；`evidence/rf-teaching/s2/executable-candidate-2026-10-16/arm-runtime/`、`source-freeze.json`、`combined-tests.xml` |
| 46 dB 研究（状态上下文） | `evidence/rf-teaching/s2-headroom-research-2026-10-15/`（第一次派发）、`evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/`（第二次派发、唯一原始帧与终态） |
| 板卡发布史 | `evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/`（b82 安装）、`evidence/rf-teaching/s2-headroom-switch-2026-10-15/`（399b→b82 回切）、`evidence/rf-teaching/s2-headroom-restore-options-2026-10-15/` |

---

## 12. 本轮边界（逐条）

- **未连板**：未 SSH/sftp、未上传、未安装、未切换 `current`/`active-release`、未布防、未发射、未采集、未加载 Overlay、未 MMIO/SPI、未派发研究、未消耗授权或 nonce。
- **未改**：产品代码、测试、`evidence/` 下任何文件、被封存件、`docs/superpowers/` 下任何计划/规格、`CLAUDE.md`、`AGENTS.md`。
- **新增**：仅本文件 `docs/reports/2026-10-17-headroom-g1-g4-status.md`。
- **未 commit**：本报告写入后仍为未跟踪文件（`?? docs/reports/2026-10-17-headroom-g1-g4-status.md`）；`git status` 中的其它未跟踪项（`docs/memory/`、`docs/superpowers/plans/2026-10-17-dispatch-runbook.md`、`evidence/rf-teaching/s2-dispatch-prep-2026-10-17/`、G4 复审目录等）均来自并行 agent。
- **未自签任何审查结论**：G1/G3/G3b 的 APPROVED 均来自独立复审者文件；本报告只转述并给路径。
