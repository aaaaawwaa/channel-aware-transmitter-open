# HEADROOM 板端运行包封装契约（P1 / Task 2.1 第 1 步）

**文档性质：规格（spec）。本文件不实现、不连板、不安装、不发射、不采集。** 它固定板端运行包的**目录布局、权限、导入路径、上传摘要、启动参数、单次派发与只读观察**契约，作为后续"红态测试 → 实现 → 独立 delta 审查 → 只读真板预检"的判据。

依据：`docs/superpowers/plans/2026-10-13-final-prj-implementation-plan.md` Task 2.1；`docs/reports/2026-10-13-headroom-board-runner-direction-review.md`（B1–B4）；`docs/reports/2026-10-13-headroom-board-runner-adoption.md` §5 第 1 步；`docs/superpowers/plans/2026-10-12-real-headroom-research-runner-design.md`（runner 的控制链、门、预算与证据模型）。**本规格不改变、不放宽其中任何已审查安全面。**

## 0. 现状事实（做本规格的理由）

| 项 | 状态 |
|---|---|
| runner `scripts/rf_headroom_research_run.py` | 板端依赖初版，SHA-256 `0d34121da03f0a0557687707e0d80ea230fb6e963c732f18f2071b8a433a07a9`；离线套件 146 passed |
| B1 受控发布导入路径 | **未开始**：发布模块按模块名导入，runner 未在板端建立受控搜索路径；`scripts.rf_teaching_install_state` 在真板上不存在（无 `scripts` 包） |
| B2 checker 缺失后的未知收据 | 已修（导入移入 `try`），**专用回归用例未加** |
| B3 板端持久 nonce 账本与单次持久作业 | **未开始**：`--ledger` 文档仍写主机侧；缺受审运行包、投递/摘要守卫、持久作业与只读观察收据 |
| B4 独立 delta 审查 | **未开始**：新板端增量不继承旧摘要（`2ee0afe8…`）批准 |
| N1 重复 `default_dependencies()` 等清理 | 已修 |
| 已安装发布 | `b82b141e355b4cb1833969383a409c79`（`cnn-bpsk-headroom-v1`，daemon 1.0.4 / protocol minor 6） |

## 1. 运行包来源与边界

1. **来源固定为本副本已提交源码**：运行包由 `final_prj` 的提交内容构建，构建时记录每个成员的 SHA-256 与来源提交号；**不得隐式依赖原仓库 `scripts` 包**，也不得在板端从仓库树导入。
2. **不进发布 software map**：runner 是发布软件映射之外的**新文件**。把它放进 `software_digests` 会改变 release id，从而需要重新资格与重新安装——本规格明确禁止。
3. **运行包成员（初版清单，实现阶段与测试一起固定）**：
   - `scripts/__init__.py`（使板端可按 `scripts.<name>` 导入）。**注意：仓库当前没有该文件**（`scripts/` 是命名空间包），打包路径必须**生成**该成员并计入摘要，不得假定其已存在
   - `scripts/rf_headroom_research_run.py`（runner 本体）
   - `scripts/rf_teaching_install_state.py`（受审 checker；`inspect_state`/`verified_state`/`safe_state`）
   - （仅测试用）`scripts/tests/test_rf_headroom_research_run.py` 与干净子进程用例
   受控发布侧模块（`rf_headroom.py`、`rf_headroom_replay.py`、`rf_status_worker.py`、`rf_status_client.py`、`merged_release.py`）**一律从已安装发布的实路径导入**，不打包进运行包。
4. **包摘要**：打包时生成 `bundle-receipt.json`（成员路径、字节数、SHA-256、来源提交、构建者）；上传前主机复核，上板后板端复核。摘要不符 = 具名拒绝，零硬件动作。

## 2. 板端目录布局与权限

| 用途 | 绝对路径 | 权限 | 生命周期 |
|---|---|---|---|
| 上传暂存（一次性） | `/tmp/rf-headroom-research-<attempt_id>/` | `root:root`，目录 `0700`，文件 `0600` | 必须**独立前缀**，禁止复用安装器保留前缀 `/tmp/rf-teaching-install-*`；结束后保留作为证据，不自动清理 |
| 持久状态根（跨重启） | `/var/lib/rf-headroom-research/` | `root:root` `0700` | 由运行包首次创建；**不属于发布目录**，不写 `/home/xilinx/overlays/**` |
| nonce/授权账本 | `/var/lib/rf-headroom-research/ledger/authorization.jsonl` | `0600` | 追加式，每行 `fsync`；撕裂尾行先截断（沿用 `rf_headroom_deploy.attempt.jsonl` 做法） |
| 派发标记 | `/var/lib/rf-headroom-research/dispatch/<attempt_id>.json` | `0600` | `O_EXCL` 创建；已存在即拒绝重复派发 |
| 研究证据 | `/var/lib/rf-headroom-research/evidence/<session_id>/` | `0700` | 由 `HeadroomRecorder` 写；`receipt.json` 为提交标记 |
| 只读观察收据 | `/var/lib/rf-headroom-research/observation/<attempt_id>.json` | `0600` | 由独立只读会话写出 |

约束：
- 任何写入都不得触及已安装发布目录、`current`/`active-release` 链接、systemd unit；
- 运行包启动前要求状态根与暂存目录**均由 root 创建且不由其他用户可写**；发现非预期属主/权限即拒绝；
- `<attempt_id>` 为 32 位小写 hex，由主机生成并写入授权工件；`<session_id>` 与 `HeadroomController` 的 32 位 hex 同形。

## 3. 受控导入路径（B1）

板端启动按以下**固定顺序**解析模块，任一步失败都归入具名结局（§7），不得回退到"碰巧能导入"的路径：

1. `sys.path` 仅包含：运行包根（使 `scripts.<name>` 可导入）、**已核验发布的实路径**（`/home/xilinx/overlays/awg_merged/releases/<release_id>`；**软件文件平铺在该目录下**，不是 `<release_id>/software`——以 `install.sh` 的实际布局与该目录下 `merged_release.verify_release(dir)` 的 `<dir>/<name>` 校验为准）；
2. 发布身份先于导入核验：`merged_release.verify_release(installed_dir, require_software=True)` 通过、`software_contract == 'cnn-bpsk-headroom-v1'`、`rf_headroom.py` 与 `rf_headroom_replay.py` 摘要等于冻结值，之后才允许导入这两个模块；
3. checker 与 runner 同包来源：`scripts.rf_teaching_install_state.verified_state` 只从运行包内导入（摘要与 `bundle-receipt.json` 绑定）；缺文件/摘要不符/不可导入 → `RUN_UNKNOWN_READBACK_INCOMPLETE`（研究已执行时）或 `RUN_REFUSED_IMPORT`（研究未执行时）；
4. **禁止**：`importlib` 按名搜索兜底、从仓库路径导入、把发布模块复制进运行包、`sys.path.append` 任意目录。

## 4. 上传摘要守卫

- 主机在打包时计算每个成员 SHA-256 并写入 `bundle-receipt.json`；上传后板端在**执行任何硬件动作之前**逐一复核：成员缺失、多余、字节数或摘要不符 → 拒绝并保留观察收据；
- 摘要复核必须发生在 `control.arm` 之前；复核实现不得写任何发布/系统路径；
- 单次复核结果写入 `dispatch/<attempt_id>.json`（成员数、失败项、结论），作为"零动作拒绝"的证据。

## 5. 启动参数（固定，无实验变参）

| 参数 | 语义 | 约束 |
|---|---|---|
| `--session-id` | 32 位小写 hex，绑定本次会话 | 必须与授权工件一致 |
| `--authorization` | 一次性授权工件路径（含 nonce/wiring/expected_release/token_ref） | 缺失、过期、重放即拒 |
| `--ledger-dir` | **板端**持久账本目录（默认 `/var/lib/rf-headroom-research/ledger`） | 文档与实现都必须是板端；不得再写"主机侧账本" |
| `--evidence-root` | 研究会话证据根（默认 `…/evidence`） | 不得指到教学 `rf_experiment_record` 记录目录 |
| `--observation-dir` | 只读观察收据目录 | 与证据根分离 |
| `--token-path` | 操作者 token 文件 | 只经 `read_control_token` 读取；**值不落盘、不进收据** |
| `--runner-version` | 与 `bundle-receipt.json` 绑定 | 不符即拒 |

**禁止**任何放宽窗口/跳过门的开关（例如 `--ignore-wiring-age`、`--force`、`--retry`、`--skip-preflight`）。

本表列的是**新增/固定**的参数项；既有安全参数（如 `--wiring-confirmed`）**保留**，不得借"固定参数"之名删除或放宽其语义。

## 6. 单次派发与防重放（B3）

1. **账本先于板卡接触**：授权工件的 `nonce` 与 `authorization_id` 在**任何硬件动作之前**追加进板端账本并 `fsync`；已出现过的 nonce 直接拒绝，且不消耗任何真实尝试；
2. **单次派发**：`<状态根>/dispatch/<attempt_id>.json` 以 `O_EXCL` 创建（状态根由 `--ledger-dir` 的父目录派生，**不新增** `--dispatch-dir`）；重复派发（同 attempt_id 或同 nonce 不同 attempt_id）拒绝；
3. **重启不等于新预算**：板卡重启后账本仍在（`/var/lib` 持久）；runner **不得**自动续跑、不得因 `initialization_seen` 归零而重新初始化；重启后的 UNKNOWN 只能由**新的**人工授权 + 新 nonce + 新接线确认开启新会话；
4. **不重连、不重试**：断线后不重连、不重放、不补一次；`RfStatusClient` 在 `_research_generation` 非空时已拒绝重连——运行包不得绕过。

## 7. 结局枚举（板端包新增的具名终态）

| 结局 | 触发 | 板卡写入 |
|---|---|---|
| `RUN_REFUSED_BUNDLE` | 成员缺失/摘要不符/权限不符 | 零 |
| `RUN_REFUSED_IMPORT` | 发布身份核验失败或模块不可导入（研究未执行） | 零 |
| `RUN_REFUSED_AUTHORIZATION` | nonce 重放 / 过期 / 身份不符 / 单次派发冲突 | 零 |
| `RUN_REFUSED_CAPABILITY` | `capability_available` 为假（沿用现有语义） | 零 |
| `RUN_UNKNOWN_READBACK_INCOMPLETE` | 研究已执行但 checker 不可用/读回中断 | 已执行部分保留 |
| `RUN_UNKNOWN_REBOOT` | boot_id 变化 | 保留原始证据 |
| `RUN_ABORTED_STORAGE_FAULT` / `RUN_ABORTED_PREFLIGHT_UNKNOWN` | 内核存储故障，或预检输出**不可用** | 零（沿用部署器判据：读取失败 ≠ 无错误） |
| `RUN_STOPPED_CANCELLED` | 操作者取消后走既有 abort + closing readback | 按既有收尾 |

物理动作前的完整门序列（G0–G8）沿用 runner 设计文档 §2.4，本规格不改其判据。

## 8. 只读观察与独立读回

1. 观察在**另一条只读会话**中进行（超时/断线后绝不重装、不重派）；
2. 读回内容：`inspect_state`/`verified_state`/`safe_state`、`current` 与 `active-release`、systemd unit 与 `NRestarts`、完整 dmesg 文本、`/` 与 `/boot` 挂载与剩余空间、RF safe（未布防 / TX 未选 / RX 未武装）；
3. **板端 RTC 无效**（2026-10-14 只读观察显示板端墙钟为 2021-12-03）：观察收据不得用板端墙钟排序事件；必须记录主机时间、`boot_id` 与 `/proc/uptime`；
4. 任一项失败即停止，**不消耗**一次性授权。

## 9. 测试计划（红态先行，先写用例后补实现）

1. **干净子进程**：在没有仓库 `pynq_overlay/sw`、没有隐式 `scripts` 包、只有板端目录布局的临时根下启动运行包；断言：能按 §3 顺序导入、能从发布实路径导入、缺任一项得具名结局；
2. **负控集合**：缺文件、错摘要、无权限、daemon 不可达、畸形/不完整 dmesg、读回中断、boot_id 变化、nonce 重放、SSH 失联、重复派发；每条断言"无布防、无采集、结局准确、原始证据保留"；
3. **研究后失败**：研究已执行但 checker 缺失/不可导入 → 必须仍写出 `RUN_UNKNOWN_READBACK_INCOMPLETE` + `run-gate.json`（B2 的专用回归）；
4. **变异**：至少覆盖"删摘要复核"、"账本改为内存/每次重建"、"派发标记非 O_EXCL"、"import 兜底搜索"、"把板端账本写回主机侧"；每个变异必须被抓；
5. **不得**修改测试期待值或放宽判据求全绿；`pytest.raises` 必须 match 到该用例名字所指的特定消息（历史教训）。

## 10. 门的顺序与不继承声明

```
规格（本文件） → 红态用例 → 实现 → 离线全套 + 变异 → 独立 delta 审查（非实现者）
  → 只读真板预检（bundle 摘要/发布身份/服务/存储/RF safe）
  → 人工接线 + 一次性授权/token → 单次派发的研究会话（最多 5 尝试、1 次初始化）
  → 独立只读读回 → TRAIN/VAL 复验 → 正式 S2 108+12 / 真人
```

- **B4 不继承**：新 runner/运行包/checker 的摘要与旧批准（`2ee0afe8…`）无关，必须自己的 delta 审查；b82 的 ARM G1 与安装收据**不**覆盖新 runner。
- 本文档任何数值/路径改动 = 新版本 + 新包摘要 + 重跑 §9 全部门禁。

## 11. 本规格明确不做（NOT_RUN）

- 不写实现、不改 `scripts/rf_headroom_research_run.py`、不动 `pynq_overlay/**` 与发布；
- 不连板、不上传、不安装、不布防、不发射、不采集；
- 不新增 daemon 命令、不改协议、不改原生门禁（改这些 = 新候选 + 新资格 + 新安装）；
- 本轮不改动任何测试期待值。

## 12. 接口固定与修订记录（2026-10-14）

### 12.1 红态测试固定的接口（实现必须匹配；§1.3 授权"实现阶段与测试一起固定"）

| 项 | 固定内容 |
|---|---|
| 打包/复核路径 | `scripts/rf_headroom_run_bundle.py`：`BUNDLE_MEMBERS`、`build_run_bundle(source_root, destination, *, source_commit, builder=None, runner_version=None)`、`verify_run_bundle(directory)`、`BundleRefused` |
| `bundle-receipt.json` | 固定字段 `runner_version` / `source_commit` / `builder` / `members`（成员路径 → `{sha256, bytes}`）；多余字段容忍 |
| 单次派发 | `rf_headroom_research_run.create_dispatch_marker(dispatch_dir, attempt_id, document)`；`O_EXCL` 语义，目标已存在抛 `FileExistsError` |
| 板端账本 | `rf_headroom_research_run.BoardAuthorizationLedger(ledger_dir)`：`.path == <ledger_dir>/authorization.jsonl`、`.refusal(nonce)`、`.consume(**fields)` |
| 具名拒绝文案 | 必须可 `match`：`is missing from the bundle`、`is not a declared bundle member`、`does not match the receipt` |

红态证据：`scripts/tests/test_rf_headroom_run_bundle.py`（12 用例）与 `evidence/rf-teaching/p1-run-bundle-red-2026-10-14/`（`red-run.log`、`RED-NOTES.md`、`red-full-suite.log`）。目录级结果为 `12 failed, 496 passed, 1 skipped, 1 warning`（exit 1）——**红态是有意为之**：实现落地前 `scripts/tests` 不绿，不得据此判断产品回归。

### 12.2 本轮修订（相对 2026-10-13 初版）

| 修订 | 原因 | 安全面影响 |
|---|---|---|
| §3.1 发布实路径由 `…/releases/<id>/software` 更正为 `…/releases/<id>`（软件文件平铺） | 与实际 `install.sh` 布局及 `verify_release` 的 `<dir>/<name>` 校验一致 | 无（判据不放宽） |
| §1.3 明确 `scripts/__init__.py` 由打包路径**生成** | 仓库是命名空间包，没有该文件 | 无 |
| §5 增加"既有安全参数保留"说明 | 防止把"固定参数"误读为删除 `--wiring-confirmed` | 无（不得放宽既有语义） |
| §6.2 明确 dispatch 目录由 `--ledger-dir` 父目录派生 | 初版未命名该目录 | 无（不新增参数） |

### 12.3 实现前已知的 B1 缺口（实现阶段必须处理）

- runner 现有 `from scripts import merged_release`、以及 `_reviewed_tools()` 按名导入 **host-only** 的 `scripts.rf_headroom_deploy` → 必须改为（a）从已核验发布实路径导入 `merged_release`，（b）把 host-only 工具移出板端路径或改为运行包内成员；
- 现有 `ConsumptionLedger` 自述为 **host-side**，不得直接当板端账本使用（§2/§6 要求板端持久路径与重放拒绝）。

### 12.4 未覆盖（不伪装）

- Windows 上不可观测 §2 的 `0700/0600` 权限位与属主；
- §8 只读观察会话与 §7"研究已执行"的 UNKNOWN 分支需真板；
- §9.4 的五条变异、`--runner-version` 不符负例、§6 自动续跑行为本轮均未执行。

## 13. 裁定与独立审查结果（2026-10-14）

### 13.1 对实现偏差的裁定（Lead）

| 偏差（来自 `GREEN-NOTES.md` §四） | 裁定 | 理由 |
|---|---|---|
| A：`scripts/rf_teaching_install_state.py` 纯新增 9 个常量（`+17/-0`） | **接受** | 不在 46 项冻结发布软件映射内；§7 用例要求运行包两个成员各自以同名常量定义一次；其消费者 `pynq_overlay/sw/tests/test_rf_teaching_deploy.py` 20 passed 未变 |
| B：门顺序固定为 **B0 成员复核 → B1 发布身份/受控导入 → 既有 G0–G8** | **接受** | 与 §3/§4"复核先于任何硬件动作"一致；两个冻结用例分别要求 `RUN_REFUSED_BUNDLE` 先于 `RUN_REFUSED_IMPORT`、后者先于授权判定 |
| C：B1 只读发布自身 manifest 判身份、**不导入**发布模块；G2 仍执行完整 `verify_release(..., require_software=True)` | **接受** | 更保守：§3.2"先核验后导入"被拆成两步；G2 判据一处未删未放宽 |
| D：`RUN_ABORTED_PREFLIGHT_UNKNOWN` 无运行时触发点 | **接受并记为别名** | 既有冻结用例把该情形钉为 `RUN_NOT_STARTED_PREFLIGHT`（同 exit 2 / 零写入 / `retry_allowed=False`）。**本规格 §7 该行按别名读**：同一情形不得出现两种终态；要独立触发点须新增冻结用例 |
| E：`--ledger` 保留为离线缝；板端入口显式传它即 `RUN_REFUSED_AUTHORIZATION` | **接受** | 满足 §5"板端账本必须是目录语义"，同时不删除既有冻结 CLI 行为 |

### 13.2 独立 delta 审查（非实现者）

**结论：APPROVED，无阻塞项。** 报告 `evidence/rf-teaching/p1-run-bundle-review-2026-10-14/REVIEW.md`（SHA-256 `500f48bec8eaa324b85a8e65c32bf60756711beb8fe6d35a71621809f6c1665b`），原始日志在同目录 `logs/`。

- 复现：冻结文件 **12 passed**（exit 0）；`scripts/tests` **508 passed, 1 skipped**（exit 0）；冻结判据文件 SHA-256 在审查开始/运行时刻/结束三次一致（`7b9ab07d…`）。
- **变异 5/5 全部被抓**：M1 删摘要与字节复核、M2 去 `O_EXCL`、M3 去账本 `fsync`、M4 加 `sys.path`/`importlib` 兜底、M5 把板端账本默认改到仓库内；附加 M3b（去重放拒绝）也被抓。每次变异后逐字节还原成功（三个文件 SHA 前后相同）。
- 禁止模式扫描全过：无第二协议客户端、无 `socket.create_connection`、无 `Overlay()`、无 daemon/协议改动、0 处拒绝降级为 warning、无新增重连/重试、无仓库 `scripts/__init__.py`。
- **P3 非阻塞观察**（留档，不构成本增量缺陷）：F1 `release_verifier_for` 在发布模块加载失败时回落 `from scripts import merged_release`（板端该路径为失败关闭，G2 转 `RUN_NOT_STARTED_PREFLIGHT`，零硬件动作；建议后续改为显式离线注入）；F2 §3.4 静态检测偏窄（`sys.path.insert`/`__import__`/from-import 不报，动态断言仍覆盖真实仓库导入）；F3 即 §13.1-D。
- **注意**：既有 146 例 `test_rf_headroom_research_run.py` 对 M1/M2/M3/M3b/M5 全部通过——这四项安全属性目前**只由新增的 12 例冻结用例守护**；后续修改该文件不得放松这 12 例。
- 未验证（不伪装）：Windows 不可观测 §2 权限位/属主；§8 只读观察会话与"研究已执行"分支需真板（`NOT_RUN`）；§6.3 会话层自动续跑；运行包从未上板/上传/安装。

### 13.3 下一道门

1. 生成绑定 runner / checker / bundle / 冻结用例摘要的收据（对应 P1-OFF-04）；
2. **只读真板预检**：上传运行包到板端 `/tmp/rf-headroom-research-*`、以 root 导入、核 b82 身份、服务、完整内核存储文本与 RF safe——**这是板卡写动作**（写 `/tmp`），与后续物理会话一样需要单独执行门与用户授权；
3. 物理研究仍需人工接线确认 + 一次性授权/token。