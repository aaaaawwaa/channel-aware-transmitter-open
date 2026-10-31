# HEADROOM runner 独立增量复审（2026-10-13）

## 0. 签核结论

| 修订 | 摘要 | 结论 |
|---|---|---|
| 交接时的修订 | runner `ba72f10e…`、测试 `be7daa57…`、`receipt.json` `39bc7782…` | **CHANGES_REQUIRED** |
| 复审者最小修补后的修订 | runner `2ee0afe8…`、测试 `a49adf17…`、`receipt.json` `00318b5d…` | **APPROVED（仅限离线 runner 门）** |

1. 对**交接时的修订**给出 `CHANGES_REQUIRED`：本轮独立发现并复现 1 项**阻塞级**新缺陷
   **N1**（事后存储读取的“完整但无内核文本”形态），两种形态都可复现，见 §5。
2. 按交接报告第 3 条的要求做了**最小修补**（一条类型判据 + 2 项红绿测试，见 §5.4），并按项目惯例**重跑收据**。
   对**修补后的修订**给出 `APPROVED`，范围严格限定为“离线 host-side runner 及其证据”，
   依赖项与遗留风险见 §7/§8。
3. **独立性声明（最重要的一句）**：本轮的修补由**复审者本人**编写。这是本轮独立性最弱的一环，
   与 F1–F10 那轮的“实现者修补、他者复验”方向相反。因此**面向硬件前必须由第三方对
   `2ee0afe8…` / `a49adf17…` 再做一次新鲜独立复验**；本报告不构成对真实安装、真实发射
   或真实 46 dB 研究的任何批准。
4. 上一轮的既有结论（F1/F2 反例、账本、G1/G3/G4 的 `board_read`、34 项变异）**全部独立复现**，
   未发现回退；**F02/F11 两项逃逸仍然成立**，是冻结套件的真实覆盖缺口，其“`RUN and escaped`”
   措辞正确，**不得**改写为 “NOT RUN”。

## 1. 范围与方法

- 只读 + 离线：真实 runner 的公开入口 `run_research` 由封存的内存假链（`sim-chain.py`）驱动；
  没有 SSH、没有 socket、没有上传/安装/Overlay/布防/发射/采集/systemd。全部临时文件在 `%TEMP%`。
- 复审者**自写 41 项探针**（不依赖上一轮审查者脚本的结论），并另外**原样运行**上一轮的两个反例脚本，
  两种来源互相独立。
- 变异结论来自**复审者自己重跑整套 34 项变异台架**，以及**自写的全冻结套件逃逸复核**（含干净对照）。
- 全程未改动 `review/**`（上一轮历史证据），新增证据一律落在
  `evidence/rf-teaching/s2/headroom-runner-2026-10-12/review-dsh-2026-10-13/`。

## 2. 产物绑定核对

### 2.1 交接报告“三个摘要”与 `receipt.json` 绑定（复审开始时，磁盘实测）

| 绑定项 | 交接/收据记录 | 磁盘实测 | 结果 |
|---|---|---|---|
| `scripts/rf_headroom_research_run.py` | `ba72f10e1c96966c…` | 同 | MATCH |
| `scripts/tests/test_rf_headroom_research_run.py` | `be7daa57c871ccda…` | 同 | MATCH |
| `…/mutation-harness.py` | `5b1b38c083e9bb28…` | 同 | MATCH |
| `…/evidence-run.py` | `342ad178fcd5ee4b…` | 同 | MATCH |
| `…/sim-chain.py` | `e754eb71e748e493…` | 同 | MATCH |
| `…/mutations.json` | `773f6ad9c3565fa6…` | 同 | MATCH |

### 2.2 修补后重跑收据的绑定（复审者自写 `verify-binding.py`，`BINDING_VERDICT=ALL_MATCH`）

- 收据内 **15/15** 条摘要与磁盘逐字节相等（runner、测试、evidence-run、四个冻结套件日志、
  绿/红日志、变异台架/表格/日志、sim-chain、seal 日志）。
- 冻结面独立重算：**46/46** 软件摘要（`software_digests` == 候选 manifest == `source-freeze.json`）、
  **22/22** `files_sha256`、**58/58** 原生输入；`seal-stage.py --verify` **exit 0**。
- **runner 与其测试都不在 46 软件映射、也不在 22 绑定文件内**，所以本轮修补**不破坏任何冻结面**。
- 过程记录（如实）：上一份收据的 8 个日志摘要中，有 2 个（`mutation-harness.log`、
  `seal-stage-verify.log`）在重跑后**逐字节不变**，另外 6 个随重跑更新；旧收据已存档为
  `review-dsh-2026-10-13/receipt-pre-patch.json`（`39bc7782…`）。

## 3. 独立复现上一轮的三类结论

复审者自写探针（修订前 `39/41`、修补后 `41/41`）逐条复现，**无一项回退**：

| 类别 | 复现结果 |
|---|---|
| **F1 事后存储读取失败** | 反例脚本 + 自写探针一致：`RUN_UNKNOWN_STORAGE_UNREADABLE`、exit 3、`faults=null`/`faults_known=false`、`reboot_determination=UNKNOWN`、`run-gate.json` 已写。不完整（`complete=false`）形态同判 |
| **F2 账本不可用** | 父目录缺失 → `FileNotFoundError`、路径是目录 → `PermissionError`，二者都是**具名拒绝**（exit 2）+ 门文档 + `consumption_status=UNKNOWN` + 零板卡探针调用 + 未建会话目录；可用账本对照仍 `RUN_COMPLETED`/exit 0 |
| **账本“写入后同步失败”** | 复审者自写注入：记录已落盘后再抛 `OSError` → 不抛出、具名拒绝、理由含 “consumption outcome is unknown” 且**不含** “nothing was consumed”、账本首行确含该 nonce、零板卡读取；门文档同样带 `consumption_status=UNKNOWN` |
| **G1/G3/G4 拒绝路径 `board_read`**（交接第 3 条） | 12 项矩阵全部符合语义：**读取确实开始**→`true`（G1 探针抛错/不完整/无文本/有故障；G3 探针抛错；G4 已声明读板的工厂抛错/返回空/自证失败）；**依赖未接线**→`false`（G1/G3 探针为 `None`、G4 工厂为 `None`）；G0 拒绝全 `false`；CLI `run` 在无传输时 G1 仍 `false` |
| **`functools.partial` 解包** | `partial(production)`、嵌套 partial 均判 `True`；普通 lambda / partial(lambda) 判 `False`；端到端：partial 包裹的“已声明读板但抛错”工厂在 G4 记 `board_read=true`；partial 包裹的生产工厂整链 `RUN_COMPLETED` 且 G4 `board_read=true`、`attestation_source` 含 `daemon poll` |
| **未被误改的路径** | 干净成功路径仍 `RUN_COMPLETED`/exit 0/六门齐全/`readback=PASS`；缺依赖路径（`default_dependencies()`）仍 `RUN_NOT_STARTED_PREFLIGHT` exit 2、理由含 `offline-only`、门文档已写、**全部 `board_read=false`**、不建会话目录 |

### 3.1 交接报告点名的 F1 过度修正探针（本轮补跑）

交接报告把 `review/increment-f1-overcorrection.py` 列为“F1/F2 原反例”，本轮原样运行（该脚本只写
`%TEMP%`、只打印，不改工作树）。四个问题全部符合预期：干净控制 `RUN_COMPLETED`/exit 0/`reboot=SAME`、
`faults=[]` 且 `faults_known=true`；真实重启 `RUN_UNKNOWN_REBOOT`/exit 3/`CHANGED`（没有被遮蔽）；
事后“不可读”与“不完整但带 boot id”两种形态均为 `UNKNOWN`（未完成的读取不被当成有效观测）；
事前不可读/事前故障仍分别是 `RUN_NOT_STARTED_PREFLIGHT` 与 `RUN_ABORTED_STORAGE_FAULT`（补丁未泄漏进拒绝路径）。
同一脚本指向**被拒修订副本**时输出与修补后**逐字节相同**（`Compare-Object` → `IDENTICAL`），
即本次修补没有改动这些路径。

## 4. 34 项变异与 F02/F11 逃逸

1. **复审者自己重跑整套台架**（`--output review-dsh-2026-10-13/mutation-rerun`）：`total=34
   caught=32 escaped=[F02,F11] unexpected=0`，exit 0 —— 与收据逐字段一致（修补前后两次都如此）。
2. **逃逸是不是“选错 node id”造成的？不是。** 复审者另写 `escape-check.py`：先用**干净对照**
   （不带插件、不带 `PYTHONPATH`）跑四个冻结套件 → `155 passed`（rc 0）；再把每一项变异
   材料化后**带插件**跑同一四个套件 → 两项均 `155 passed`（rc 0），且
   `loaded_is_the_copy=true`、变异体摘要与 `mutations.json` 记录**逐字节相等**
   （F02 `a5dd20b3…`、F11 `df690945…`）。结论：**二者仍是“整份冻结套件都抓不到”的真实覆盖缺口**，
   可以继续作为本审核门的**已声明限制**。它们既不改 `RUN`、也不改 `caught`，只是记录逃逸。
3. **上一轮证据的一处缺陷（如实记录，不改变结论）**：
   `review/frozen-mutant-full-suite.json` 里两项的 `pristine_returncode` 都是 **1**、
   `pristine_summary` 为空 —— 因为 `review/verify-declared-escapes.py` 对**干净对照**也传了
   `-p mutant_plugin` 却没给 `PYTHONPATH`，对照run 直接 `ImportError` 崩掉，
   所以该 JSON 里的 `full_suite_catches_it=false` **在对照侧是无效证据**（变异侧有效）。
   同目录的 `logs/frozen-mutant/PRISTINE-CONTROL.log`（`155 passed`）与**复审者自己的对照**
   补上了这一环。本报告因此**不引用**该 JSON 的对照字段。
4. 措辞：`mutations.json` 与 `receipt.json` 对 F11 记的是“RUN and escaped …… 两侧都真的跑过”，
   与记录自身的 `baseline/mutant/mutant_was_the_loaded_copy` 字段一致 —— **没有被写成 “NOT RUN”**，
   复审者确认这一措辞正确，并在本报告中沿用 “escaped”。

## 5. 新发现 N1（阻塞）与最小修补

### 5.1 位置与设计依据

- 位置：`scripts/rf_headroom_research_run.py` `postflight()` 的事后读取分支
  （修订前 :1349-1353）。它在 `complete is True` 时直接
  `storage_faults(snapshot.get('kernel_storage') or '')`，**既不要求文本存在，也不要求它是字符串**。
- 设计依据：设计文档 §2.4 G1 行（第 99 行）“内核存储输出**完整**读取 …… **读取失败 ≠ 无错误**”；
  §6“存储故障中止”行（第 286 行）禁止条款“**禁止“读取失败当作无错误”**”；§T17（第 318 行）。
  **事前**那一半（`gate_storage_preflight` :1137-1143）已经对同一形态判
  “`the kernel storage preflight returned no kernel text`”并拒绝；**事后**那一半没有。

### 5.2 可复现反例（修订前 `ba72f10e…`，由复审者自写探针驱动真实入口）

- **N1-a 静默“无错误”**：探针第 1 次返回正常快照、第 2 次返回
  `dict(boot_id='boot-0001', complete=True)`（**没有** `kernel_storage`）→
  `storage.postrun = {"boot_id":"boot-0001","faults":[],"faults_known":true,"preflight_status":"CLEAN"}`、
  终态 **`RUN_COMPLETED`、exit 0**。即：一次**根本没有读到内核文本**的读取被当成“板卡存储无错误”，
  并让整轮研究以“完成”收尾 —— 与 F1 同一类过度声明，只是触发形态不同。
- **N1-b 无证据的崩溃**：第 2 次返回 `complete=True` 且 `kernel_storage=b'bytes not text'` →
  `storage_faults` 内 `TypeError: a bytes-like object is required, not 'str'` **穿出
  `run_research`**，**完全没有 `run-gate.json`**（只剩记录器自己的 27 个产物）。
  这正是 F2 被判定阻塞的同一类“破坏既定结局契约、事后无证据”的缺陷。
- **对照**：把同样两个形态放在**事前**读取上，两者都是具名拒绝
  （`RUN_NOT_STARTED_PREFLIGHT` + 理由 “returned no kernel text”）。
  即：**同一条设计规则的两半互相矛盾**，这正是缺陷本身。

### 5.3 严重度

**阻塞（对本审核门而言）**。理由：(a) 与已被判阻塞的 F1 同类，落在交付物的“headline”输出上；
(b) 与已被判阻塞的 F2 同类，会让整轮运行**没有 `run-gate.json`**；(c) 触发条件只在**事后**那一次
读取上（事前守卫会拦），与 F1 的“第二次读取换一种失败方式”完全同构；
(d) 当前阶段的 `run` 子命令没有接线任何板卡传输，所以**今天不可达**，但它恰好在**下一个门**
（接上真实 `storage_probe`）时变为可达。因此必须在接线真实硬件之前修掉。

### 5.4 最小修补（复审者编写，已落盘）

`postflight()` 在 `complete is True` 分支增加与事前读取**同一条**判据：

```python
text = snapshot.get('kernel_storage')
if type(text) is not str:
    self.post_storage = dict(boot_id=snapshot.get('boot_id'), faults=None,
                             faults_known=False, preflight_status='UNREADABLE',
                             error_type='MalformedStorageSnapshot',
                             error='the post-run storage read returned complete without kernel text')
else:
    faults = storage_faults(text)
    ...
```

- **改动面**：只有“`complete=True` 但文本不是字符串”这一形态改变行为（旧行为是崩或静默 CLEAN，
  新行为是 `UNREADABLE` → `RUN_UNKNOWN_STORAGE_UNREADABLE`/exit 3 → 故障列表未知；若畸形快照仍带与事前相同的 boot id，重启判定为 `SAME`，只有 boot id 缺失才是 `UNKNOWN`（此项措辞由 2026-10-13 第三方复验作非阻塞更正：`SAME` 只表示两次读取的 boot id 一致，不代表存储健康））；
  任何字符串文本（含空串）的路径**逐行未变**。
- 补丁前后 `postflight` 的语义与 F1 修复完全一致：不可读 ≠ 无错误。

### 5.5 红绿

- **红（先做）**：两项新测试指向**修订前的 runner 副本**（`RF_HEADROOM_RUNNER_PATH`）→
  `2 failed, 4 passed, 133 deselected`，exit 1；失败正是这两条，且失败原因就是上面的
  `RUN_COMPLETED` 与 `TypeError`（日志 `logs/red-new-tests-vs-pre-patch-runner.log`）。
- **绿（后做）**：`139 passed`（原 137 + 2），exit 0（`logs/green-patched-new-suite.log`）。
- 两个探针脚本版本对照同样成立：`independent-probes.py` 对修订前 `39/41`（B3、B4 失败）、
  对修补后 **`41/41`**。
- 四个冻结套件复跑 `155 passed`，**未改任何冻结文件**。

## 6. 重跑收据（修补后）

`python evidence/.../headroom-runner-2026-10-12/evidence-run.py` → exit 0、`passed=true`：

| 项 | 结果 |
|---|---|
| 新套件（绿） | **139 passed** |
| 缺席实现（红） | **108 failed / 31 passed** |
| 四个冻结套件 | **79 + 34 + 26 + 16 = 155 passed** |
| 变异台架 | **34 total / 32 caught / [F02,F11] escaped / unexpected 0** |
| 冻结检查 | 46 软件 / 22 绑定文件 / 58 原生输入全部相等；`seal-stage.py --verify` exit 0 |

## 7. 板卡接触事实

- `board_contact=false`、`board_transport_wired=false`、`new_rf_frames=0`、
  `real_initializations=0`、`installed=false`、`physical_headroom=NOT_RUN`。
- 静态扫描**修补后**的 runner：`socket|paramiko|subprocess|shutil|urllib|requests|ftplib|telnetlib|
  192.0.2.10|systemd-run|create_connection` **零匹配**。
- 本轮没有 SSH、没有上传、没有安装、没有 Overlay、没有布防/发射/采集、没有 systemd 派发；
  没有连接 `127.0.0.1:5000`。所有假件都在同一进程内（`SimClient`/`StorageProbe`/`identity`/
  `readback`）。
- 记录在案（不可达但已知）：`release_worker_factory(client=None)` 会构造冻结
  `RfStatusClient`（默认端口 5000）；本阶段 G0（`token_reader`）先拒，任何测试与探针都没有走这条路；
  复审者的 E8 显式传入内存 client。
- 板卡侧事实沿用交接报告：b67、`current == active-release`、双服务 active/running/NRestarts0、
  未布防、TX/RX 关闭、本启动无新 MMC/SDHCI/ADMA/I-O/ext4 错误；**本轮未联系板卡**，未复核这些读数。

## 8. 遗留风险与非阻塞观察（均已复现/读码确认，不阻塞本门）

1. **O1 账本处理范围窄**：只把 `OSError/UnicodeError/ValueError` 转成具名拒绝；其它异常
   （如 `RuntimeError`）仍会穿出且不写门文档。设计上这是“不把程序错误伪装成拒绝”，但意味着
   “never raises”是有界的。判定：非阻塞（当前所有现实故障都是 OSError 族）。
2. **O2 时钟/依赖构造在 try 之外**：`dependencies.now()` 或工厂构造前段的编程错误同样会穿出且无门文档
   （上一轮 E 边界用例复现）。当前 `default_dependencies().now()` 是 `utc_now`，无现实触发路径。
3. **O3 G4 的 `board_read` 是“工厂声明”**：可靠但依赖正确标注。`release_worker_factory` 已声明；
   **下一个门新接真实传输时必须给新工厂加同一属性**，否则生产形态会被记成 `board_read=false`。
   该义务已在测试与探针中固定（E1–E8）。
4. **O4 `single_connection_no_reconnect` 仍是本地启发式**（上一轮 F9），不可过度解读为
   “单 socket 已证明”；强制仍在冻结客户端 `_research_owned`。
5. **O5 冻结侧覆盖缺口与死代码**（F02 轨道/终态等价无独立用例、F11 重连守卫无测试、
   `rf_headroom._confirm_disarmed` 无调用者）：**只报告，未改冻结文件**。
6. **O6 模拟路径的零帧声明**：`simulate` 完成且 5 次尝试被接受时，`new_physical_frames=0` 但
   `zero_frame_claim.supported=false`（四事实之一不成立）。这是**保守且正确**的，但读者不得把
   `new_physical_frames=0` 当成“已证明零帧”。复审者的 A6 一开始就误读了它，特此记录。
7. **O7 摘要随本轮更新**：交接报告的摘要表（`ba72f10e`/`be7daa57`）已被本次修补后的
   `2ee0afe8`/`a49adf17` 取代；交接报告保持历史原文，**以 `receipt.json` 为权威**。

## 9. NOT_RUN（不得被本报告任何结论替代）

真实板卡联系（SSH/上传/安装/Overlay/布防/发射/采集/systemd）、runner 在硬件上的执行
（**从未**）、新候选 `b82b141e…` 的真实安装与独立只读读回、真实 46 dB 研究、
真实存储故障中止、真实断线/重启、真实只读安全读回、正式 S2（108 物理 + 12 离线）、真人验收、
S3、S4、MMC/SDHCI 根因（`NOT_ESTABLISHED`）、完整文件系统检查/修复（`NOT_RUN`）。
物理帧 **0**、真实初始化 **0**、开发物理累计仍 **67**。

## 10. 下一审核门

1. **新鲜第三方复验修补后摘要**（`2ee0afe8…` / `a49adf17…`）—— 因为补丁是本轮复审者写的。
   建议的最小复验：重跑 `evidence-run.py` 与 `review-dsh-2026-10-13/independent-probes.py`，
   并对 `postflight()` 的“完整但无文本”分支再做一次回退守卫（撤销该 if 分支 → 两条新测试必须失败）。
2. 项目下一门（需要用户授权，均**不在本轮**）：
   存储健康只读确认 → 用已封存 `install-bundle/` **单次安全安装** `b82b141e…` + 独立只读读回 →
   给 runner 接真实传输（实现 `storage_probe/identity_probe/readback_probe/token_reader`；
   **新工厂必须声明 `performs_board_read`**）→ 人工接线确认 + 一次性 token →
   一次会话最多五尝试的有限物理研究。
3. 接线真实探针前的前置说明：runner 现在会对畸形快照 fail-closed，但**探针本身仍必须返回
   `{boot_id, kernel_storage:str, complete:bool}`**；本补丁只是不让畸形数据变成“无错误”或崩溃。

## 11. 本轮改动/新增与“未改动”声明

改了 **2 个文件**（均在 `scripts/`，均在冻结面之外）：

| 文件 | 改动 | 修补后摘要 |
|---|---|---|
| `scripts/rf_headroom_research_run.py` | `postflight()` 增加“完整但非字符串文本 → UNREADABLE”判据与 UNREADABLE 分支（见 §5.4），另改 docstring 一行 | `2ee0afe8d1c0d438a38f8a689de554e4e9d1df9ca23dd5a07cc507de3291b217` |
| `scripts/tests/test_rf_headroom_research_run.py` | 新增 `SecondReadMalformed` 与 2 项红绿测试 | `a49adf177421787ffcfa334818781ffc43c8d88f8ad1a4765c50f64804e048a4` |

按项目惯例重跑收据，故 `evidence/rf-teaching/s2/headroom-runner-2026-10-12/` 下的
`receipt.json`（`00318b5df48869c585a0d1a613970f77440340bf3cce951da933123b4a5a309f`）、
`mutations.json`（`e6d941e5323eb25d0036d572e53adf77af00659020d02ddc3264e0a4028e0e30`）、
`mutation-logs/**`、`logs/**` 被重新生成；旧收据与旧变异表已存档为
`review-dsh-2026-10-13/receipt-pre-patch.json`（`39bc7782…`）与
`…/mutations-pre-patch.json`（`773f6ad9…`）。`review/**` **一个字节都没有改动**。

复审者在最近 4 小时内的全仓改动扫描（`scripts/host/src/sdk`、`docs/reports`、
证据目录顶层）只列出上述文件与两份存档/新证据目录，**没有第三方文件被本条线改动**。

### 复审者证据索引（`review-dsh-2026-10-13/`）

| 文件 | 摘要 / 结果 |
|---|---|
| `independent-probes.py` | `6d4823c7cb915228…`（41 项探针） |
| `logs/probes-vs-pre-patch-runner.log` | `71db7ddb81b7d027…` → `39/41`，exit 1（B3/B4 失败） |
| `logs/probes-vs-patched-runner.log` | `ec4b0984813432bb…` → `41/41`，exit 0 |
| `logs/red-new-tests-vs-pre-patch-runner.log` | `f9c20719fd020a3e…` → `2 failed, 4 passed, 133 deselected` |
| `logs/green-patched-new-suite.log` | `6571722f9bed512e…` → `139 passed` |
| `logs/frozen-four-suites.log` | `17c7b617e1c3b69e…` → `155 passed` |
| `escape-check.py` + `escape-check/results.json` | `aeb8fe1c5a724663…` / `657cb24b698f36b3…` → 对照 155、两变异各 155 |
| `verify-binding.py` | `cec70561eec2a275…` → `ALL_MATCH` |
| `runner-pre-patch.ba72f10e.py` / `runner-test-pre-patch.be7daa57.py` | `ba72f10e…` / `be7daa57…`（被拒修订原样存档） |
| `mutation-rerun/mutations.json` | `82229d06e3c095d3…`（复审者自己跑的 34 项，修订前） |
| `logs/f1-overcorrection-vs-patched.log` | 交接报告点名的 F1 过度修正探针，六种情形全部符合 |
| `logs/f1-overcorrection-vs-pre-patch.log` | 同一脚本指向被拒修订副本 → 与上一行 `IDENTICAL`（补丁无副作用） |

## 12. 执行命令与退出码

```powershell
# 0) 环境
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONIOENCODING='utf-8'   # Python 3.8.6
Set-Location '<LOCAL_PATH>'

# 1) 上一轮两个原始反例（原样运行）
python -B evidence/rf-teaching/s2/headroom-runner-2026-10-12/review/counterexample-postrun-storage.py   # exit 0
python -B evidence/rf-teaching/s2/headroom-runner-2026-10-12/review/increment-counterexample-f2.py     # exit 0

# 2) 复审者自写 41 项探针（两份修订对照）
$env:DSH_RUNNER_PATH = '<...>/runner-pre-patch.ba72f10e.py'
python -B evidence/rf-teaching/s2/headroom-runner-2026-10-12/review-dsh-2026-10-13/independent-probes.py  # exit 1 (39/41)
Remove-Item Env:\DSH_RUNNER_PATH
python -B evidence/rf-teaching/s2/headroom-runner-2026-10-12/review-dsh-2026-10-13/independent-probes.py  # exit 0 (41/41)

# 3) 34 项变异（复审者自己一套） + 逃逸全冻结套件复核
python -B evidence/rf-teaching/s2/headroom-runner-2026-10-12/mutation-harness.py --output evidence/rf-teaching/s2/headroom-runner-2026-10-12/review-dsh-2026-10-13/mutation-rerun  # exit 0, 34/32/2
python -B evidence/rf-teaching/s2/headroom-runner-2026-10-12/review-dsh-2026-10-13/escape-check.py    # exit 0

# 4) 红/绿（红指向被拒修订副本）
$env:RF_HEADROOM_RUNNER_PATH = '<...>/runner-pre-patch.ba72f10e.py'
python -B -m pytest -q -p no:cacheprovider --basetemp $env:TEMP\dsh-red -rf -k post_run scripts/tests/test_rf_headroom_research_run.py   # exit 1, 2 failed
Remove-Item Env:\RF_HEADROOM_RUNNER_PATH
python -B -m pytest -q -p no:cacheprovider --basetemp $env:TEMP\dsh-green -rf scripts/tests/test_rf_headroom_research_run.py              # exit 0, 139 passed
python -B -m pytest -q -p no:cacheprovider --basetemp $env:TEMP\dsh-frozen -rf pynq_overlay/sw/tests/test_rf_headroom.py pynq_overlay/sw/tests/test_rf_headroom_corrections.py pynq_overlay/sw/tests/test_rf_headroom_pipeline.py pynq_overlay/sw/tests/test_rf_headroom_native_lease.py  # exit 0, 155 passed

# 5) 重跑收据 + 独立绑定/冻结核验
python -B evidence/rf-teaching/s2/headroom-runner-2026-10-12/evidence-run.py     # exit 0, passed=True
python -B evidence/rf-teaching/s2/headroom-runner-2026-10-12/review-dsh-2026-10-13/verify-binding.py  # exit 0, ALL_MATCH
python -B evidence/rf-teaching/s2/executable-candidate-2026-10-12/seal-stage.py --verify              # exit 0
```
