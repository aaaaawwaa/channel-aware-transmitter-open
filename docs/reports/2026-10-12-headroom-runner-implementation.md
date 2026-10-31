# HEADROOM 真实研究 runner：实现与离线模拟测试

**文档性质：实现报告（implementation report）。本轮零板卡联系、零安装、零 RF、零采集。**

| 项目 | 状态 |
|---|---|
| runner 实现 | `scripts/rf_headroom_research_run.py`（新增，不在 46 项冻结软件映射内） |
| 离线模拟测试 | `scripts/tests/test_rf_headroom_research_run.py`（新增，121 项，全绿） |
| 冻结链复用 | worker/controller/recorder/replay/OFF guard 均为发布内真实对象（按路径加载） |
| 变异测试 | 34 项，**32 项被抓**，2 项为**已声明的预期逃逸**（原因见 §6） |
| 冻结套件复跑 | 4 套件 155 项全绿（未复制其断言，见 §7） |
| 真实安装 / 板卡联系 / RF / 采集 | **NOT_RUN**（本轮 0 次） |
| runner 是否在真板上运行过 | **从未**（本阶段 runner 无任何板卡传输实现） |
| 新物理帧 / 真实初始化 | **0 / 0** |
| S2 正式 108+12 / 真人 / S3 / S4 | NOT_RUN |

本轮**没有**修改 `CLAUDE.md`、路线图或任何既有报告/计划/收据：上一阶段的封存校验
（`seal-stage.py --verify`，1330 个绑定摘要）因此仍然可复验。**就 runner 状态而言，本报告
取代 `CLAUDE.md` 与路线图中的旧描述**（那里写的仍是"真实 runner 不存在"）。

---

## 1. 交付物

| 路径 | 说明 |
|---|---|
| `scripts/rf_headroom_research_run.py` | runner：门禁顺序、一次性授权与消费账本、接线新鲜度、唯一执行入口、runner 层终态标签、`run-gate.json`、独立只读安全读回 |
| `scripts/tests/test_rf_headroom_research_run.py` | 121 项离线模拟测试（含变异测试用的 `RF_HEADROOM_RUNNER_PATH` / `RF_HEADROOM_RELEASE_DIR` 接缝） |
| `evidence/rf-teaching/s2/headroom-runner-2026-10-12/sim-chain.py` | 假硬件链（进程内，零 socket）：脚本化 peer + 两个只读探针 + 独立读回；CLI `simulate` 的 `--fake-chain` 模块 |
| `evidence/rf-teaching/s2/headroom-runner-2026-10-12/mutation-harness.py` | 变异台架（复制到 `%TEMP%`、断言变异已生效、先跑基线再跑变异体、记录原始日志） |
| `evidence/rf-teaching/s2/headroom-runner-2026-10-12/evidence-run.py` | 一条命令重跑全部证据并生成收据 |
| `evidence/rf-teaching/s2/headroom-runner-2026-10-12/receipt.json` | 阶段收据 |
| `evidence/rf-teaching/s2/headroom-runner-2026-10-12/mutations.json` | 机器可读变异表 |
| `evidence/rf-teaching/s2/headroom-runner-2026-10-12/logs/**`、`mutation-logs/**` | 全部原始日志（含红/绿、冻结套件、变异基线/变异体、封存校验） |

## 2. 门禁顺序（按实现）

`run-gate.json` 逐门记录 `gate / passed / board_read / …`，顺序即下表；任一门失败即
fail-closed 返回，**在第一个物理动作之前板卡零写入**。

| # | 门 | 判据（全部来自生产对象，runner 不自造） | 板卡读取 | 失败终态 |
|---|---|---|---|---|
| G0 | `G0-AUTHORIZATION` | 授权工件字段集/取值/有效期；接线 12 h 新鲜度（常量，无开关）；命令行 `--wiring-confirmed` 必须等于工件 statement；token 路径一致且 `read_control_token` 可读（值即弃）；证据根与教学记录根分离；nonce 未消费→追加账本（fsync） | 否 | `RUN_NOT_STARTED_PREFLIGHT` |
| G1 | `G1-STORAGE-PREFLIGHT` | `rf_headroom_deploy.storage_faults`（**复用已审查谓词**）对完整内核文本判定 | 是 | 检出故障→`RUN_ABORTED_STORAGE_FAULT`；读取失败/不完整→`RUN_NOT_STARTED_PREFLIGHT` |
| G2 | `G2-RELEASE-ATTESTATION` | `merged_release.verify_release(dir, require_software=True)`；契约 `cnn-bpsk-headroom-v1`；`rf_headroom.py`/`rf_headroom_replay.py` 摘要 == 冻结值 | 否 | `RUN_NOT_STARTED_PREFLIGHT` |
| G3 | `G3-RELEASE-IDENTITY` | 已安装 release == 授权 `expected_release`；实时 `device.info` 身份（release/daemon 1.0.4/major 1/minor 6）逐字段相等 | 是 | `RUN_NOT_STARTED_PREFLIGHT` |
| G4 | `G4-WORKER-ATTESTATION` | `worker.headroom_status()['available'] is True` 且 release_id 相符 | 否 | `RUN_REFUSED_CAPABILITY`（另一 release→`RUN_NOT_STARTED_PREFLIGHT`） |
| G5–G8 | `G5-G8-SINGLE-CHAIN` | **唯一一次同步调用** `RfStatusWorker.headroom_run(session_id, cable_confirmed=True, evidence_root=…, token_path=…, cancelled=…, require_hardware=…)`；租约→能力→布防→`headroom.begin` 由 `HeadroomController` 在这一次调用内完成 | 是 | 见 §3 |

G8 的语义由生产链保证：`headroom.begin` 是第一个物理动作（其 ACK 必须 `initializations==1`、
`attempt_budget==5`，且 `actual_rf` 通过 `_fixed_rf_error` 双路 46000 mdB 判定）。runner
**没有**新增 daemon 命令、没有解析协议、没有自建客户端/控制器。

## 3. runner 层终态标签（全部 `retry_allowed=false`）

| 标签 | 触发（实现） |
|---|---|
| `RUN_NOT_STARTED_PREFLIGHT` | G0/G1(读失败)/G2/G3/G4(release 不符) 拒绝；进程内第二次研究被拒 |
| `RUN_ABORTED_STORAGE_FAULT` | G1 检出内核存储故障；或事后（post-run）读回检出故障 |
| `RUN_REFUSED_CAPABILITY` | G4 `available is not True`；或收据 `stopped_at ∈ {UNAVAILABLE, CAPABILITY}`（零尝试） |
| `RUN_COMPLETED` | 收据 `completed is True` 且独立读回 PASS |
| `RUN_STOPPED_<stopped_at>` | 控制器停止点原文：`CLIPPED`、`EVIDENCE:N`、`OFF_EVIDENCE`、`headroom.begin`、`headroom.load:N`、`headroom.capture:N`、`headroom.accept:N`、`CLOSE_UNCONFIRMED`、`CANCELLED`、`CRASHED` |
| `RUN_INTERRUPTED_MID_ATTEMPT` | 已提交记录中某次尝试有 `intent.json` 而无 `decision.json` |
| `RUN_UNKNOWN_DISCONNECT` | 冻结客户端自己的断线字符串（`research connection was lost; no reconnect` / `RF daemon disconnected` / `truncated research IQ payload` / `research requires its original live I/O lease`）；**超时/期限不算断线** |
| `RUN_UNKNOWN_REBOOT` | 事前/事后 `boot_id` 变化 |
| `RUN_UNKNOWN_READBACK_INCOMPLETE` | 研究已开始但独立读回未取得 verified 状态（`UNAVAILABLE`/`UNVERIFIED`/异常） |
| `RUN_EVIDENCE_WRITE_FAILED` | 链上报 `evidence_write_error`；或 `run-gate.json` 写入失败 |

优先级（`terminal_label`，有单元测试逐项固定）：证据写失败 > 事后存储故障 > 独立读回未定案 >
boot_id 变化 > 尝试中断 > 断线 > 完成 > 能力拒绝 > 控制器停止点 > 门禁标签。

退出码：`RUN_COMPLETED`→0；零尝试类（preflight/capability/storage-abort）→2；其余→3。

## 4. 证据模型

* 沿用 `HeadroomRecorder` 的既有落盘次序（`intent.json` 先于发送；`header.json` + `frame.iq`
  先于任何分析；`receipt.json` 为提交标记），**未改动其 schema**（测试用发布内的
  `read_study` 回读验证）。
* runner 另写 `run-gate.json`：门禁逐门记录、授权/接线字段、账本序号、`runner_sha256`、
  发布身份与连接代次、逐次尝试索引、观察分类、`zero_frame_claim`、独立读回原文、`not_run`。
* 逐次尝试由 runner **从已提交文件重读**：`frame.iq` 由 runner 重新 sha256，`accepted` 取自
  `decision.json`，`hardware_verified` 取自 `header.json` 的 `actual_rf_after`。
* `observation_class`：`NEW_PHYSICAL_FRAME` 的唯一判据是原生
  `hardware_verified is True` **且** `accepted is True`；重放产物记 `REPLAYED_SAME_IQ`；
  其余帧记 `SIMULATED_FAKE_HARDWARE`。声明的模拟运行强制 `new_physical_frames=0`。
* `zero_frame_claim`：四项互相独立的证据（无 accepted 决策 / 无已证物理帧 / 板端 `used|active`
  均为 false / 收据为拒绝类），缺一即 `supported=false` 并点名缺哪项；研究从未开始时以
  `basis=NO_BOARD_CONTACT` 表述（不是假装板卡说了什么）。
* 证据根必须是专用研究根：等于/包含教学实验根、或名为 `records` 时直接拒绝。
* 研究开始前的拒绝**不创建** recorder 的会话目录，`run-gate.json` 落到
  `<evidence-root>/run-gate/<attempt-id>.json`。

## 5. 覆盖矩阵（设计 T1–T22）

| # | 覆盖方式 | 证据 |
|---|---|---|
| T1 真实租约语义正例 | 新测试（端到端）+ 冻结复跑 | `test_the_gate_order_is_the_reviewed_one_and_every_gate_ran`；`test_every_stop_path…[full-five-real-lease-semantics]` |
| T2 历史缺陷反例（`lease_held=false`） | 新测试（端到端，零尝试） | `test_the_historical_lease_defect_is_refused_with_zero_attempts` |
| T3 旧语义谓词变异 | 变异 F01（被抓）+ 冻结复跑 | `mutations.json` F01 |
| T4 第二客户端 BUSY（原生桥） | 冻结复跑（**不复制**） | `test_rf_headroom_native_lease.py` 16 项 |
| T5 解除后租约保留 / 断开后释放 | 冻结复跑（**不复制**） | 同上 |
| T6 五项操作链正例（假硬件） | 冻结复跑 + 新测试（真实 client × 封存 in-memory peer） | `test_the_real_client_runs_the_whole_study_over_one_in_memory_connection`；CLI `simulate --case full-five` |
| T7 能力字段逐个反例 | 冻结复跑（**不复制**） | `test_preflight_rejects_each_actual_ineligible_boundary` 等 |
| T8 `begin` ACK 反例 | 冻结复跑 + 新测试（物理运行要求 begin 的硬件证明） | `test_a_physical_run_demands_native_hardware_attestation` |
| T9 帧身份反例 | 冻结复跑（**不复制**） | `test_corrupted_same_frame_metadata_is_refused` 19 例等 |
| T10 重放身份 | **新测试（补缺口）** + 变异 F10（被抓） | `test_a_replayed_capture_identity_is_refused_by_the_frozen_chain` |
| T11 第六次 / 乱序 | 冻结复跑 + 变异 F06（被抓） | `test_a_study_can_never_reach_a_sixth_attempt` |
| T12 OFF 负控 | 冻结复跑 + 新测试（`off-violation` → `RUN_STOPPED_OFF_EVIDENCE`）+ 变异 F08/F09 | `test_every_stop_path…[off-violation-…]` |
| T13 人工门缺失 | 新测试（token 缺失/空/路径不符；无放宽开关） | `test_an_unusable_token_refuses_with_zero_attempts_and_no_directory`、`test_the_cli_rejects_every_bypass_switch` |
| T14 授权工件门 | 新测试（18 组参数化 + 过期 + 重放 + 账本撕裂） | `test_each_malformed_or_mismatched_authorization_field_refuses` 等 |
| T15 候选门（未安装已资格候选） | 新测试 + 变异 R08（被抓） | `test_the_installed_release_must_be_the_qualified_candidate` |
| T16 冻结摘要门 | 新测试 + 变异 R07/R22（被抓） | `test_a_tampered_frozen_research_module_is_refused_by_the_attestation` |
| T17 存储故障门 | 新测试（6 个已审查关键字 + 不完整 + 抛错，两种终态可区分）+ 变异 R05/R06（被抓） | `test_the_storage_predicate_is_the_reviewed_deploy_predicate` 等 |
| T18 证据写失败 | 新测试（链上报错；不可写证据根） | `test_an_evidence_write_failure_from_the_chain_is_reported_as_such`、`test_an_unwritable_evidence_root_is_reported_as_an_evidence_write_failure` |
| T19 隔离 `/tmp` | 新测试（以安装器自身守卫为真源做 fnmatch）+ 变异 R12（被抓） | `test_the_runner_staging_prefix_is_not_the_installer_prefix` |
| T20 进程单次性 / 重启不续跑 | 新测试 + 变异 R20/R10（被抓） | `test_a_second_study_in_one_process_refuses_before_any_board_read`、`test_a_replayed_nonce_is_refused_by_the_consumption_ledger` |
| T21 独立读回 | 新测试（调用顺序、独立探针、结论不来自研究连接）+ 变异 R15（被抓） | `test_the_readback_is_a_separate_read_only_probe_after_the_study` 等 |
| T22 「无新物理帧」四项证据 | 新测试（四项逐一破坏 + 叙述一致性 + `NO_BOARD_CONTACT`） | `test_the_zero_frame_claim_needs_all_four_independent_facts` 等 |

**未被本轮覆盖**：真板上的新语义行为、真实断线/重启、真实存储故障中止、真实独立读回、
新候选 ARM G1、正式 S2/真人/S3/S4（§8 NOT_RUN）。

## 6. 变异结果

机器可读表：`mutations.json`（含每项 `baseline`/`mutant` 日志与 `mutant_was_the_loaded_copy`）。

* 总数 **34**，被抓 **32**，逃逸 **2**（均为已声明预期逃逸，`unexpected_escapes=[]`）。
* runner 侧变异（R01–R23）：授权字段、有效期、接线新鲜度、预检读失败、存储故障中止、
  冻结摘要、候选门、实时身份、账本去重/持久化、安装器前缀、能力拒绝映射、物理帧判据、
  独立读回、`require_hardware`、token 泄漏、`retry_allowed`、绕过控制器直接调用、
  进程单次性、证据根、冻结摘要常量、按名 import 研究模块。
* 冻结生产代码变异（F01–F11）：旧能力谓词、轨道计数、`settle_wait_us`、硬件证明、
  第六次、载荷摘要、OFF 停止、OFF 重放判定、重放身份去重。
* **逃逸 1（F02，预期）**：删掉 `(terminal=='CLIPPED') is (rails>0)` 等价检查后，
  `test_a_header_that_disagrees_with_its_own_bytes_is_refused` 的三个参数化用例**都先被
  轨道计数检查拒绝**，因此该等价检查在冻结套件中**没有独立用例**。这是冻结套件的覆盖缺口，
  如实记录，不用放宽断言的方式掩盖。
* **逃逸 2（F11，预期）**：断线后重连守卫（`rf_status_client._connection` 的
  `research connection was lost; no reconnect`）在冻结套件中**没有测试**（原生租约套件只断言
  `_research_owned`）；且控制器从不重试，所以移除该守卫在当前路径上不可观测。同样作为
  覆盖缺口记录。
* **设计 §7.2 第 11 项（"用 `control.disarm` 的 ok 当解除证据"）无法照原样变异**：
  `rf_headroom._confirm_disarmed` 是**死代码**（模块内定义、全仓无调用者；解除证据实际来自
  `client.headroom_closing_readback()` 与 `_closing_confirmed`）。该变异被改为载荷摘要检查
  （F07），死代码本身作为发现上报。
* 变异**从不触碰工作树**：所有变异体都在 `%TEMP%`；runner 变异经
  `RF_HEADROOM_RUNNER_PATH`，冻结模块经 pytest 插件按模块名重定向（插件记录实际加载路径），
  发布内文件变异经 `RF_HEADROOM_RELEASE_DIR`（并断言副本哈希与原文件不同）。

## 7. 与设计文档的偏离（逐条说明理由）

1. **G5–G8 不是四个独立 runner 门**。设计 §2.4 把租约/能力/布防/`headroom.begin` 列为
   G5–G8，但同一份设计禁止 runner 调用 `client.headroom_*`、禁止自建第二个控制器/客户端。
   实现把这一段记为一个**委派门** `G5-G8-SINGLE-CHAIN`：唯一一次 `worker.headroom_run(...)`
   之内由 `HeadroomController` 完成，runner 只读回执。源码审计测试断言
   `.headroom_run(` 恰好出现一次、且不存在任何 `.headroom_begin/load/capture/accept/abort/lease(`。
2. **`--wiring-confirmed-by` 未实现**（设计 §10 草图有）。`confirmed_by` 属于签名工件；
   再让命令行重复一遍，等于允许非签名者宣称接线。命令行改为**必须**回填
   `--wiring-confirmed "<statement>"` 且必须与工件逐字相等（草图没有这条更强的双重录入）。
3. **`session_id` 由授权工件拥有**：工件先于运行产生，不可能包含"未来的随机会话号"，因此
   runner 直接以工件的 `session_id`（校验 32 位小写 hex）作为本次研究会话号，而不是另生成
   一个再比对；`run-gate.json` 记录 `session_id`，runner 内不存在 `new_session_id()` 调用。
4. **`run` 子命令在本阶段没有板卡传输**：`default_dependencies()` 是空包，任何板卡读取都会
   以具名 `DependencyUnavailable` 拒绝（`RUN_NOT_STARTED_PREFLIGHT`，退出码 2，理由含
   `offline-only`）。这是本阶段"零板卡联系"约束下的诚实形态，也保证 runner **无法**意外连板。
   生产形态的 worker 工厂（`release_worker_factory`）已实现并测试：按路径从发布加载
   `rf_status_worker` 类，用 worker 自己的只读轮询完成 G4 所需 attestation，并在研究取连接
   之前停掉轮询线程。
5. **账本消费时机**：设计说"在板卡接触之前"消费 nonce；实现放在 G0 末尾、G1 板卡读取之前。
   后果：G1 因存储故障中止也会烧掉该授权（需要新授权重来）。这是 fail-closed 的选择，如实
   记录。
6. **`single_connection_no_reconnect` 的判据是"代次增量 ∈ {0,1}"**，不是"前后相等"：租约本身
   会建立本次研究的**第一条**连接（集成测试中 0→1）。强制仍由冻结客户端的
   `_research_owned`/`_connection` 完成，runner 不实现第二份谓词。
7. **`require_hardware` 由 `simulation` 派生**：只有声明了
   `simulation=True 且 hardware_touched=False` 的假硬件包才能放宽（`simulate_dependencies`
   强制校验），实际取值写入门禁文档；物理运行一律要求原生硬件证明，且模拟运行强制
   `new_physical_frames=0`。
8. **测试措辞偏离**：按本轮任务要求，T1/T2/T3/T7–T12 **不复制**，改为复跑四个冻结套件
   （155 项全绿）。唯一新增的"补缺口"测试是 T10 的重放身份去重——冻结套件里没有任何断言
   覆盖它，而 runner 的 `RUN_STOPPED_EVIDENCE:N` 标签依赖它。
9. **假链复用方式**：封存的 `headroom-fake-chain.py` 是独立 campaign 脚本，不实现 runner 的
   `dependencies(...)` 协议，因此它被**当作 peer 复用**（新 `sim-chain.py` 只读加载它的
   `Peer`/`FakeSocket`，并补上真实 `RfStatusClient` 轮询所需的 `status.subscribe` 与
   `mode/core` 字段）；`--fake-chain` 模块是本轮新增的 `sim-chain.py`。
10. **私有只读字段**：runner 读取 `client._connection_generation`、`client._research_owner`、
    `worker._stop/_thread/_headroom_module`（只读，不改源码、不改 release id）。设计 §5.8
    明确允许读取 `_connection_generation`；封存的假链也使用同类私有字段
    （`worker._headroom_root`、`worker._headroom_thread`）。

## 8. NOT_RUN / 未验证（不得被本轮结果替代）

* 真实板卡联系（SSH/上传/安装/Overlay/布防/发射/采集/systemd）——本轮 0 次；
  runner 的 `run` 子命令**没有**任何传输实现，从未在硬件上运行过。
* 真实 46 dB 物理研究：0 新帧、0 真实初始化。
* 新候选的 ARM G1（本 runner 的）、真实安装、真实独立只读读回。
* 真实存储故障下的中止、真实断线/重启/重启后未知状态（只有模拟）。
* 正式 S2 108 物理 + 12 离线、真人验收、S3、S4。
* MMC/SDHCI 根因 `NOT_ESTABLISHED`；完整文件系统检查/修复 `NOT_RUN`。
* 冻结套件中**已被识别但未闭合**的两处覆盖缺口（§6 逃逸 1/2）与
  `rf_headroom._confirm_disarmed` 死代码——本轮只报告，不改冻结文件。
* 变异测试的 `needle` 命中是**辅助**证据；判定"被抓"的依据是
  `baseline 通过 + 变异体失败 + 变异体确为被加载的那份副本`（或 R12 的导入期守卫拒绝）。

## 9. 复现命令

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONIOENCODING='utf-8'
& '<LOCAL_PATH>' -B -m pytest `
  scripts/tests/test_rf_headroom_research_run.py -q -p no:cacheprovider `
  --basetemp $env:TEMP\hr-runner
& '<LOCAL_PATH>' -B `
  evidence/rf-teaching/s2/headroom-runner-2026-10-12/evidence-run.py     # 全部证据 + receipt.json
& '<LOCAL_PATH>' -B `
  evidence/rf-teaching/s2/executable-candidate-2026-10-12/seal-stage.py --verify
```

`evidence-run.py` 依次执行：新套件（绿）→ 缺席实现副本（红）→ 四个冻结套件 →
变异台架 → 冻结摘要与封存校验 → `receipt.json`。全部临时文件在 `%TEMP%`。

## 10. 本轮实际执行的命令与结果（逐字）

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONIOENCODING='utf-8'

# 1) 新增 runner 测试（绿）
& '<LOCAL_PATH>' -B -m pytest `
  scripts/tests/test_rf_headroom_research_run.py -q -p no:cacheprovider `
  --basetemp $env:TEMP\hr-runner-final
#   -> 133 passed in 36.15s        EXIT=0   （独立审查前为 121；修复 F1/F2 后新增 12 项）

# 2) 四个冻结套件（复跑，不复制断言）
& '<LOCAL_PATH>' -B -m pytest `
  pynq_overlay/sw/tests/test_rf_headroom.py -q -p no:cacheprovider `
  --basetemp $env:TEMP\frozen-final-test_rf_headroom
#   -> 79 passed in 1.59s          EXIT=0
#   test_rf_headroom_corrections.py -> 34 passed   EXIT=0
#   test_rf_headroom_pipeline.py    -> 26 passed   EXIT=0
#   test_rf_headroom_native_lease.py-> 16 passed   EXIT=0

# 3) 变异台架
& '<LOCAL_PATH>' -B `
  evidence/rf-teaching/s2/headroom-runner-2026-10-12/mutation-harness.py
#   -> MUTATIONS total=34 caught=32 escaped=['F02-terminal-rail-equivalence-removed',
#      'F11-research-reconnect-allowed'] unexpected=[]      EXIT=0

# 4) 封存完整性
& '<LOCAL_PATH>' -B `
  evidence/rf-teaching/s2/executable-candidate-2026-10-12/seal-stage.py --verify
#   -> verified: new candidate/G1/owned native lease/durable fake job; installation and RF NOT_RUN
#      EXIT=0

# 5) 全部证据 + 收据（`--skip-mutations` 复用第 3 步已经生成的变异表）
& '<LOCAL_PATH>' -B `
  evidence/rf-teaching/s2/headroom-runner-2026-10-12/evidence-run.py --skip-mutations
#   -> 133 passed / red: 102 failed, 31 passed / 四个冻结套件 155 passed
#   -> MUTATIONS total=34 caught=32 escaped=[F02, F11] unexpected=[]
#   -> RECEIPT passed=True         EXIT=0
```

## 11. 关于本证据目录的并发外部改动（如实记录，含更正）

本轮实现期间，`evidence/rf-teaching/s2/headroom-runner-2026-10-12/` 里出现了**本实现者之外的
agent 活动**（Lead 与独立审查方共用同一工作区），可观察到三件事：

1. `evidence-run.py` 的 `freeze_checks()` 被 **Lead** 改写过一次：本实现者的第一版按
   `merged_release.software_sources()` **逐路径**比对，并在一次运行中把 5 个名字报成不匹配
   ——恰是相对路径不是 `pynq_overlay/sw/<name>` 的那 5 个（`scripts/merged_release.py` 是会在
   `sys.path` 上插入发布树的兼容层，故该推导对导入顺序敏感）。Lead 改为使用规范函数
   `merged_release.software_digests(REPO, contract)` 与
   `verify_release(candidate)['software']` 双重判等，路径映射只用于报告。独立审查在 7 种
   敌意 `sys.path` 顺序下复测该函数，结论全部一致，确认该判据**规范且与导入顺序无关**，
   并确认原先的假阴性**没有**掩盖任何冻结破损。
2. 19:55 前后 `receipt.json` / `mutations.json` 被另一次运行重写过（内容与本节所述一致，
   哈希自洽）；**本节末尾**的收据是修复 F1–F5 后重新生成的最终版本。
3. **更正（独立审查 F10）**：`logs/lead-verify/` 是 **Lead** 在排查上述假阴性时创建的取证
   目录，**并未被删除**：其中的 `seal-stage-verify.log`（19:50:41）仍在磁盘上，且**不被**
   `receipt.json` 绑定。它不属于本实现者的交付物，原文称其“随后被删除”有误，此处更正。

除 `evidence-run.py` 的上述一段外，其余交付物（runner、测试、sim-chain、变异台架）的写入
时间与本实现者一致，未发现被外部改写；46 项软件摘要、22 个绑定文件、58 项原生输入与封存
校验共同证明**生产文件未被改动**。

## 12. 独立审查响应（REJECTED → 逐项修复）

独立审查（`review/critical-review.md`，verdict **REJECTED**）给出 2 项阻塞 + 8 项非阻塞发现。
本节的修复**已全部落地**，并新增/调整测试覆盖。审查者的 `review/**` 目录保持原样未改，作为
增量复验的原始证据。

| 发现 | 处置 | 现在由谁证明 |
|---|---|---|
| **F1（阻塞）** 事后存储读取失败被当成“无错误” → `RUN_COMPLETED`/exit 0，且 `boot_id=None` 静默解除重启判据 | `postflight` 的不可读/不完整分支不再产出 `faults=[]`；新增终态 `RUN_UNKNOWN_STORAGE_UNREADABLE`（`POST_STORAGE_UNKNOWN_STATUSES`），并规定“不可读的 boot id 永不等于 same” | `test_an_unreadable_post_run_storage_read_is_never_a_completion`、`test_an_incomplete_post_run_storage_read_is_never_a_completion`、`test_a_clean_post_run_storage_read_still_completes`、`test_a_post_run_storage_fault_is_an_abort_and_not_an_unknown_state` |
| **F2（阻塞）** 账本路径不可用直接抛 `OSError`，无 `run-gate.json` | 账本消费与授权读取全部包成**具名拒绝**（`RUN_NOT_STARTED_PREFLIGHT`，非零退出，仍写 `run-gate.json`），未消费、未读板 | `test_a_ledger_whose_parent_directory_is_missing_is_a_reported_refusal`、`test_a_ledger_path_that_is_a_directory_is_a_reported_refusal` |
| F3 G4 记为 `board_read=False`，但生产工厂的轮询确实读 daemon | G4 记录对生产形态改为 `board_read=True`；2026-10-13 补修 `functools.partial` 包装工厂时属性不传递的遗漏 | `test_the_gate_record_follows_what_the_factory_declares`：包装后的生产工厂仍执行真实 worker 轮询，G4 必须记录 `board_read=True`；修复前该断言实测失败，修复后通过 |
| F4 `mutations.json` 谎称 F11 “NOT RUN” | 改为“RUN and escaped …”，并明确“该字段记录逃逸、从不表示跳过；两者都已实际运行” | `mutations.json` 中旧文本出现次数 = **0** |
| F5 CRLF 目标的 `original_sha256` 是 LF 归一化摘要 | 变异台架改为读**原始字节**；针/替换按目标自身的行尾转换并记录 `line_ending`；新增 `input_sha256` 表达链式第二处编辑 | F11 现记录 `line_ending=crlf`、`original_sha256=bcb494ac…`（与 `source-freeze.json` 及磁盘文件一致） |
| F6 `PRE_ATTEMPT_REFUSALS` 少了 `STOPPED` | 改为 `('UNAVAILABLE','CAPABILITY','STOPPED')`（对齐设计 §4.4(b)/§5.7） | 既有门序/拒绝测试 + 源码审计 |
| F7 授权文件不可读属同类未捕获 | 与 F2 同法包成具名拒绝（`except (OSError, UnicodeError, ValueError)`） | 授权门参数化拒绝测试组 |
| F8 四处 `pytest.raises` 未带 `match=` | 全部补上指向具体消息的 `match=` | 测试源码（`attempt_id`、`simulation=True`、`dependencies` 等） |
| F9 `single_connection_no_reconnect` 是本地启发式而非单 socket 证明 | 属观察项：**强制仍由冻结客户端的 `_research_owned` 完成**，runner 不加第二份谓词；措辞已按此精确化 | 本报告 §7 与源码审计测试 |
| F10 §11.3 关于 `lead-verify/` 的陈述有误 | 本节上方 §11 第 3 条已更正 | 文件系统事实 |

**修复过程中的两点如实披露：**

1. F2 的修复把账本消费抽成 `_consume_authorization()`，但 `self._gate(GATE_AUTHORIZATION, ...)`
   留在了 `return` 之后——G0 门记录在成功路径上不再产生，被本套件当场抓住
   （`test_the_gate_order_is_the_reviewed_one_and_every_gate_ran` 报门序缺少
   `G0-AUTHORIZATION`）。Lead 修正为赋值 `self.ledger_entry` 并把 `now` 传入该助手（该段原本
   是死代码，一旦可达即 `NameError`）。**该缺陷由测试发现，不是被掩盖。**
2. 修复任务由一名实现者执行到一半被中断（runner/测试/sim-chain 已改，变异台架、证据与报告
   未改，测试处于 1 失败的中间态）；**由 Lead 接手完成** F4/F5/F8/F10 与证据重新生成。因此
   本节所述修复由两个 agent 先后完成，交付物以本报告的新摘要为准，并等待独立增量复验。

**修复后的计数（本轮实际运行）**：新测试 **133 passed**（审查前 121）；缺席实现红态
**102 failed / 31 passed**；四个冻结套件 **79+34+26+16 = 155 passed**；变异 **34 total /
32 caught / 2 已声明逃逸 / unexpected 0**；`receipt.json` **passed=True**；
`seal-stage.py --verify` 仍为 `verified: new candidate/G1/owned native lease/durable fake job;
installation and RF NOT_RUN`，exit 0。

**本报告绑定摘要（2026-10-13 补修后）**：runner `1e86dc8ebf661dbc47007625dfdb6c4cad271bf34b62280f54472daaa6ad7b19`；
测试 `8f5b1c22eda57b605d59668585f471e8be13a97b7aca823dae5e2cbef1d1b93e`；
变异台架 `5b1b38c083e9bb285461e01df7f8bd5bb4fd83a7273304a2777f6ac342a105c7`。
**仍未做**：真实板卡联系、安装、Overlay、布防、发射、采集、真实 systemd、真实 46dB 研究、
真实只读读回、S2 正式/S3/S4 —— 全部 NOT_RUN；物理帧 0、真实初始化 0；
MMC/SDHCI 根因 NOT_ESTABLISHED、完整 FS 修复 NOT_RUN。

**2026-10-13 后续接续：** 本节的 133 项计数和摘要是 F3 `partial` 修补时的历史快照，
随后又补齐账本同步失败与 G1/G3/G4 拒绝记录的缺口。最新 137 项证据、摘要、审核状态
和交给 DeepSeek Harness 的独立复审任务见
`docs/reports/2026-10-13-headroom-runner-audit-handoff.md`；独立签核仍待完成。


