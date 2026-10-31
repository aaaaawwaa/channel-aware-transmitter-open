# final_prj 离线测试与构建基线（P0 / Task 1.2）

日期：2026-10-14（UTC）。范围：`<LOCAL_PATH>` 副本内的**离线**测试与主机构建；**未连接板卡、未运行 Vivado、未做 ARM 原生构建、零 RF**。

原始日志：`evidence/rf-teaching/p0-baseline-2026-10-13/`（子代理首轮 `logs/`，本轮补充 `lead/`）。每条命令的日志头部含命令行、UTC 起止时间与退出码，正文为未截断的原始输出。

**提交边界**：仓库 `.gitignore` 排除 `*.log`，原始日志**不入 Git**，按路径/大小/SHA-256 登记在 `evidence/rf-teaching/p0-evidence-manifest.json`（40 个文件、785,543 字节）；摘要 JSON、说明 MD 与抓取脚本入 Git。

## 1. 环境

| 项 | 版本 / 路径 |
|---|---|
| OS | Windows（PowerShell 7）；`pwsh` 内可用 Git bash 的 `sh.exe`：`<LOCAL_PATH>` |
| Python | 3.8.6（`<LOCAL_PATH>`） |
| pytest | 8.3.5 |
| make | GNU Make 4.4.1（`<LOCAL_PATH>`） |
| C 编译器 | gcc 14.2.0（x86_64-posix-seh，MinGW-w64） |
| Vivado / Vitis | 未调用（`NOT_RUN`） |

所有 pytest 一律使用 `python -B -m pytest -p no:cacheprovider --basetemp <工作区内目录>`：系统默认临时目录在本机引发权限错误（历史已知问题），不计作产品失败。

## 2. 结果总览

| # | 命令 | 退出码 | 结果 | 日志 |
|---|---|---|---|---|
| A | 五组核心测试（iq_signal / bpsk_receiver / rx_analysis / rx_constellation / cnn_runtime） | 0 | **289 passed, 5 skipped**（3.18 s） | `logs/A-five-groups.log` |
| B | 单条全量：`pynq_overlay/sw/tests scripts/tests` | 2 | **收集中断**（1 个 collection error），未执行任何用例 | `logs/B-full-suites.log` |
| B1 | `pynq_overlay/sw/tests` 单独 | 1 | **2040 passed, 5 failed, 5 skipped, 11 errors**（181.70 s） | `lead/B1-pynq-sw-tests.log` |
| B2 | `scripts/tests`（排除 B3 的文件） | 1 | **208 passed, 62 failed, 1 skipped, 80 errors**（83.18 s） | `lead/B2-scripts-tests.log` |
| B3 | B 中报错文件的收集复现（`--collect-only`） | 1 | **no tests collected, 1 error** | `lead/B3-collection-error.log` |
| C1 | 工具链版本 | 0 | see §1 | `lead/C1-tools.log` |
| C2 | `make -C rf_overlay/sw/board_app host-test`（cmd 环境） | **1** | `mkdir -p .hostobj` → `A subdirectory or file -p already exists.` | `lead/C2-host-test.log` |
| C2b | 同一命令，PATH 前置 `<LOCAL_PATH>`（让 GNU Make 找到 `sh.exe`） | 0 | 全部套件通过，见 §4 | `lead/C2b-host-test-posix-shell.log` |
| C3 | `host-test-iq` | 0 | `test_rf_iq` 40 PASS / 0 FAIL | `lead/C3-host-test-iq.log` |
| C4 | `host-test-core` | 0 | `test_rf_core` 98 / 0 | `lead/C4-host-test-core.log` |
| C5 | `host-test-protocol` | 0 | `test_rf_protocol` 58 / 0 | `lead/C5-host-test-protocol.log` |
| C6 | `host-test-sleep` | 0 | `test_rf_sleep` 22 / 0 | `lead/C6-host-test-sleep.log` |
| C7 | `host-test-headroom` | 0 | `HEADROOM 148 passed 0 failed`；`HEADROOM_OWNED 10 passed 0 failed` | `lead/C7-host-test-headroom.log` |
| C8 | `vendor-integrity` | 0 | `vendor integrity: 10 files passed` | `lead/C8-vendor-integrity.log` |

**A 的结果与历史参考值 289 passed / 5 skipped 一致**（见 `docs/reports/2026-10-13-final-prj-focus-decision.md`）；这是新目录下重跑得到的同一数值，不是引用旧结果。

## 3. 失败与错误的三分类（本副本内**没有产品代码失败**）

### 3.1 证据缺失（直接）——155 项

B1 的 16 项（5 failed + 11 errors）与 B2 的 142 项（62 failed + 80 errors）中的 139 项（余下 3 项见 §3.2）根因相同：源工程 `evidence/` 未随副本复制（`FORK_SNAPSHOT.md` 已声明），测试引用的封存路径在副本内不存在。典型缺失路径：

- `evidence/rf-teaching/s1/candidate/releases/c3bf915c544563324778d1c879b38e3a/merged_release_manifest.json`
- `evidence/rf-teaching/s1/board-pilot-2026-10-04-v1/declaration.json`
- `evidence/rf-teaching/s2/headroom-contract-2026-10-11/candidate-budget/releases/b67a8aaab0c5904822580c0647b96fd0/merged_release_manifest.json`
- `evidence/rf-teaching/s2/g1-qualification-2026-10-08/candidate/releases/adfac1f14c3cdb379cb92569a21a9054`
- `evidence/2026-10-01-rf-build-hmi/package-safe/releases/56d0d0d4bd36d4e22d1c030926917a16/merged_release_manifest.json`

异常类型分布（B2 的 174 条 `E ` 行）：`FileNotFoundError` 115、`ValueError: missing or invalid merged manifest` 23、`ValueError: required input missing` 14；**无 `TypeError`/`KeyError`**。

按文件统计（B2）：`test_rf_headroom_prepare.py` 27 failed、`test_rf_environment_deploy.py` 14、`test_rf_headroom_deploy.py` 14、`test_rf_headroom_candidate.py` 5、`test_rf_headroom_parent.py` 2；errors 集中在 `test_rf_receiver_parity_run.py` 65 与 `test_rf_headroom_activation.py` 15。

### 3.2 证据缺失（次生：`pytest.raises` 期望消息不匹配）——3 项

3 项断言失败的原因是**更早的证据检查先拒绝**，而不是被测逻辑错误：

| 用例 | 期望 | 实际（根因） |
|---|---|---|
| `test_new_headroom_candidate_requires_new_native_contract` | `ValueError` 匹配 `1.0.4` | 先命中 `missing or invalid merged manifest`（父候选清单缺失） |
| `test_current_headroom_parent_cannot_be_used_for_environment_contract` | 匹配 `parent` | 同上（`b67a8aa…` 候选目录缺失） |
| `test_historical_preparer_still_refuses_the_new_working_tree` | 匹配 `source changed\|frozen native` | 先命中 `required input missing: …adfac1f14…` |

### 3.3 依赖缺失——0 项

本机 Python 3.8.6 环境未出现 `ModuleNotFoundError`/`ImportError`；`torch` 相关 ML 测试不在本次两个目录内（`host/tests/` 未运行）。

### 3.4 真实代码失败——0 项

**没有任何一项失败/错误来自被测产品逻辑**。等价地：本副本当前无法对 `scripts/tests`、`pynq_overlay/sw/tests` 给出"全绿"结论，原因是缺少源工程证据树，而不是实现缺陷。

## 4. C 主机侧构建与测试（`rf_overlay/sw/board_app`）

| 套件 | 结果 |
|---|---|
| `test_rf_transport_policy` | 64 PASS / 0 FAIL |
| `test_rf_state` | 64 PASS / 0 FAIL |
| `test_rf_daemon` | 450 PASS / 0 FAIL |
| `test_rf_daemon_main` | 92 PASS / 0 FAIL |
| `test_platform_rf` | 39 PASS / 0 FAIL |
| `test_selftest` | 5 PASS / 0 FAIL（另有两条**自带注入负例** `4 PASS 1 FAIL` 与 `1 PASS 1 FAIL`，最后输出 `SELFTEST HARNESS OK`，属预期） |
| `test_rf_iq` | 40 PASS / 0 FAIL |
| `test_rf_core` | 98 PASS / 0 FAIL |
| `test_rf_protocol` | 58 PASS / 0 FAIL |
| `test_rf_sleep` | 22 PASS / 0 FAIL |
| `test_rf_headroom` / `test_rf_headroom_owned` | 148 / 10 PASS，0 FAIL |
| vendor 完整性 | 10 files passed |

**`make host-test` 在 cmd 下必然失败**：Makefile 第 185/46 行的 `mkdir -p $(OBJDIR)` 由 cmd 内建 `mkdir` 执行，`-p` 被当成目录名；`rf_overlay/sw/board_app/-p` 目录的最后写入时间为 **2026-10-06T02:43:26Z**，说明这是先于本次工作的既有环境问题，不是本轮引入。把 Git bash 的 `sh.exe` 置于 PATH 后，GNU Make 用 POSIX shell 执行配方，同一命令退出码 0。**该工作方式不改动任何产品源码或 Makefile**。

## 5. 构建输入与发布一致性（静态核对）

| 文件 | SHA-256 |
|---|---|
| `rf_overlay/build.tcl` | `3f9afa048f91054096f25c93ace30eae692da8ded8e8915de8f5eeff882b9fdc` |
| `pynq_overlay/build.tcl` | `01ddd6f17a5a614eaf910f99e6b275710034ff5a8c016f1cd74fe8a537a42dec` |
| `scripts/release_manifest.py` | `d0d97f2a59cf35fc5af914c55a64adf142d3faa03b36375fffb4ea4aab80035b` |
| `pynq_overlay/sw/merged_release.py`（software map 与合同） | `35be90bc5fcfbbb7182cea0a08a97dd3bcaaa91508724f489867cb3c2edf2380` |
| `pynq_overlay/sw/install.sh` | `3fda35af82de1dfbce7f0614ddaf05abb25d3bc69da65e96b57952ac44228372` |
| `scripts/rf_teaching_verify.py` | `1027fefcfe9a18e1bf0064715a8ca79f7fd34eae85e6bf3ee626014734ad682b` |
| `build_manifest.json`（AWG 侧） | `d5d53103ead0dc2d41ca1ad1f868653e546752d24ee73c42c86e92f72c4c5877` |

- `pynq_overlay/sw/merged_release.py` 的 software map 为**分层叠加**：`SOFTWARE_SOURCES` 之上依次叠加 `LEGACY_`、`CNN_`、`BPSK_`、`TEACHING_`、`ENVIRONMENT_` 合同（第 41–110 行），因此**旧候选仍可验证**。
- 上述文件的存在与摘要已记录；但 `pynq_overlay/sw/merged_release.py` 与当前工作树源码的**逐文件 digest 相等性**属于发布/资格流程，本次未执行（需候选与冻结清单，`NOT_RUN`）。
- Vivado 构建、位流、HWH、XSIM 仿真与 ARM 原生构建一律 `NOT_RUN`；位流/PL 相关结论不得由本文件推断。

## 6. 结论与后续

1. **Gate P0-B（关键软件链路可在新目录复现）**：Python 侧五组核心测试可复现（A）；C 主机侧全部套件在同一副本内可构建并通过（§4）；两套目录级全量测试**因缺源工程证据**而不能全绿，已按三类记录并给出精确缺失路径。
2. **Gate P0-0 仍未通过**（原任务停写未确认），因此**不迁移证据**。迁移后需重跑 B/B1/B2 并把结果写回本报告（新增小节，不改写本次原始退出码）。
3. 不得通过修改测试期待值、放宽判据或跳过用例来"求全绿"；正确做法是补齐受控证据目录，或按 P1 规格把证据注入路径做成显式契约。
4. 临时目录 `.pytest-work-p0`、`.pytest-work-lead-pynq`、`.pytest-work-lead-scripts`、`.pytest-work-lead-collect` 位于副本根目录（已确认解析路径在 `final_prj` 之内），原始日志已保存在 `evidence/` 下，可安全删除。

## 7. 未验证（`NOT_RUN`）清单

- Vivado/Vitis 构建、位流/HWH 生成、XSIM 仿真；
- 板上 ARM 原生构建（`make` on ARM）与板端运行；
- `host/tests/`、`host/main.py` GUI 路径（AWG 辅助路径）的本次回归；
- 任何真实板卡、RF、采集、安装动作；
- 证据迁移后的复跑（依赖 Gate P0-0）。

## 8. P0 门禁自检（对照实施计划 Chunk 1）

| 门 | 判据 | 本次状态 | 依据 |
|---|---|---|---|
| Gate P0-0 | 原任务已暂停并停写，切换点有可核查的源码与收据摘要 | **NOT_CONFIRMED** | 无任务级暂停接口，界面确认缺失；已记录源端只读快照与约 18.4 h 停写观察（`switch-receipt.json`） |
| Gate P0-A | 每条简介要求可追到责任模块；历史事实有原始证据或显式 `UNVERIFIED`；不引用不存在的本地证据为已验收 | 满足（在不迁移证据的前提下） | `docs/requirements/brief-traceability.md`；历史收据统一以 `SOURCE/` 前缀标注并登记于 `p1-evidence-manifest.json`（24/24 可读） |
| Gate P0-B | 关键软件链路可在新目录复现，未知依赖有清单 | 满足（缺证据已分类） | 本报告 §2–§4；五组 289 passed / 5 skipped 与 C 全套房件在同一副本内复现 |
| Gate P0 完成 | `final_prj` 可重建基线、目标逐项可追踪、原任务切换记录与证据边界明确 | **未完成（被 P0-0 阻塞）** | — |

**结论：** Chunk 1 的文档与基线产物已齐（Task 1.1/1.2/1.3），但 **Chunk 1 整体不得宣布完成**：Gate P0-0 未通过，"此后才把 Harness 工作目录改指向新副本"的条件未满足。因此本轮**没有开始 Chunk 2 的实现**；唯一产出的后续文档是规划性质、不触碰任何产品代码/协议/PL 的 P2 TX 合同规格（`docs/superpowers/specs/2026-10-13-rf-tx-mode-contract.md`）。

**（该节写于 Gate P0-0 关闭之前；P0-0 随后由用户确认关闭，见 §9。）**

## 9. 证据迁移后的复跑（Gate P0-0 关闭后）

本节不改写 §2–§8 的任何原始退出码；它是同一副本在**补齐受控证据之后**的独立测量。Gate P0-0 于 2026-10-14 由用户确认关闭（`CONFIRMED_BY_USER`，非工具级证明），随后按 `p1-evidence-manifest.json` 做三轮迁移，**逐文件复制前后 SHA-256 核验相等**：562 文件/30.9 MB、1,571 文件/61.0 MB、1,633 文件/42.1 MB（清单见 `evidence/rf-teaching/imported-2026-10-14-*.json`）。巨量 `pytest-*` 临时树按计划不复制。

| 轮次 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| D1 / D2（第一轮后） | `pynq_overlay/sw/tests` / `scripts/tests` | 1 / 1 | 2050 passed, 6 failed, 5 skipped, 0 errors / 254 passed, 162 failed, 1 skipped, 80 errors |
| E1 / E2（第二轮后） | 同上 | 1 / 1 | 2050 passed, 6 failed, 5 skipped / 400 passed, 16 failed, 1 skipped, 80 errors |
| F1 / F2（第三轮后） | 同上 | 1 / 1 | **2055 passed, 1 failed, 5 skipped** / **493 passed, 3 failed, 1 skipped, 0 errors** |

日志（含命令、UTC 起止与退出码）：`evidence/rf-teaching/p0-baseline-2026-10-13/after-import/{D,E,F}{1,2}-*.log`。

### 9.1 剩余 4 项失败（全部为证据/工具链，**0 项 `AssertionError`**）

| 数量 | 用例 | 原因 | 归属 |
|---|---|---|---|
| 1 | `test_real_development_tone_residuals_do_not_become_pulses` | 缺 `evidence/rf-teaching/s2/board-pilot-2026-10-05-v2/result.json` | 证据件缺失（可补） |
| 2 | `test_rf_headroom_candidate.py`（2 项） | `ValueError: bounded native artifact and build evidence required` | 需要 ARM 原生件与构建证据 → **ARM 门未执行** |
| 1 | `test_current_recovered_parent_is_qualified_without_rewriting_unknown` | `ValueError: verified rollback parent recovery hash differs: final-safe-state.json` | 导入的回退父版本证据与测试期望哈希不同 → 证据绑定问题，不是产品逻辑 |

### 9.2 结论

1. **Gate P0-A / P0-B 已满足**：简介逐条可追踪（§8 表）；两套目录级测试在补齐受控证据后达到 2055/2056 与 493/496，且**从未出现产品代码失败**（分类始终是证据缺失/工具链缺失）。
2. **Gate P0 仍未宣布完成**：剩余 4 项分别指向一个缺失的 pilot 证据件与一个未执行的 ARM 门；按计划"不得通过修改测试期待值、放宽判据或跳过用例求全绿"。补齐方式：补 `board-pilot-2026-10-05-v2/result.json`，或用受控 ARM 构建产出 native artifact/build evidence；两者都属于下一步工作，不是本轮产物。
3. 迁移后的证据目录已纳入版本控制（位流/HWH/原生二进制与 `*.log` 由 `evidence/.gitignore` 排除但保留在本机，按清单 SHA-256 核验）。

## 10. 后续缺件补齐与定向复验（2026-10-14）

§9 的 4 项失败是当时的原始结果，保留不改。本次按 `evidence/rf-teaching/p0-followup-import-2026-10-14.json` 从源工程补入 116 个文件、1,204,022 字节，包括完整 `board-pilot-2026-10-05-v2` 目录及 `board-recovery-2026-10-12` 中的 3 个恢复证明文件。逐文件复制前后 SHA-256 相等；恢复证明文件还与 `recovery-checkpoint.json` 的封存摘要相等。

只重跑先前因这两组缺件失败的两个用例：`test_real_development_tone_residuals_do_not_become_pulses`、`test_current_recovered_parent_is_qualified_without_rewriting_unknown`，结果 **2 passed in 1.30s，退出码 0**。本次没有重跑目录级全量测试，因此 §9 的总数仍是历史快照，不能直接改写为新的全量通过数。

随后发现 `test_rf_headroom_candidate.py` 所需的 `arm-native` 可执行文件和构建收据已在原工程封存目录中，仅未复制到副本。按 `evidence/rf-teaching/p0-arm-native-import-2026-10-14.json` 补入 **74 文件、4,038,784 字节**，逐文件复制前后 SHA-256 相等；这不是本轮新 ARM 构建。原先的另外两个用例定向复验 **2 passed、1 个 Python DeprecationWarning，退出码 0**。

最后分别全量重跑：

| 命令范围 | 退出码 | 新结果 |
|---|---:|---|
| `pynq_overlay/sw/tests` | 0 | **2056 passed, 5 skipped in 171.06s** |
| `scripts/tests` | 0 | **496 passed, 1 skipped, 1 warning in 145.79s** |

本次测试使用 `python -B -m pytest -p no:cacheprovider --basetemp <final_prj 内独立目录> -q <范围>`，两个临时目录经路径检查后清理。警告来自 `scripts/rf_build_daemon.py:179` 的 Python 无效转义 `\;`，未造成测试失败。P0 在**当前本地副本及 SHA 清单所列外部/忽略二进制齐备**的条件下完成离线基线闭合；Git 独立检出仍须按清单恢复未入库二进制/日志，不能把该条件写成纯 Git 克隆即可全量复现。板卡只读观察与 P1 顺序见[本轮状态报告](2026-10-14-deepseek-board-and-p0-followup.md)。

## 11. 独立复核（2026-10-14，Lead 重跑）

本节是对 §10 结论的**独立重跑**（不是引用同一结果）：同一副本、两套范围各自使用独立的 `--basetemp`，日志 `evidence/rf-teaching/p0-baseline-2026-10-13/after-import/G1-lead-verify-pynq.log` 与 `G2-lead-verify-scripts.log`（头部含命令行、UTC 起止与退出码）。

| 范围 | 退出码 | 复核结果 |
|---|---:|---|
| `pynq_overlay/sw/tests` | 0 | **2056 passed, 5 skipped（179.71 s）** |
| `scripts/tests` | 0 | **496 passed, 1 skipped, 1 warning（155.91 s）** |

- 唯一警告仍是 `scripts/rf_build_daemon.py:179` 的无效转义（**未修**：属产品源码清洁度问题，本轮不改产品代码）。
- `.gitattributes` 新增的 `evidence/** -text` 已核对：新提交**未改动任何产品源码**（`src`/`rf_overlay`/`pynq_overlay`/`scripts`/`host`/`tools`/`sdk`/`ci` 在 `ab5210d..HEAD` 内无差异）。
- 边界保持不变：**纯 Git 克隆仍需按 SHA 清单恢复被忽略的二进制与日志**，不能据此声称"克隆即可全量复现"。

## 12. P1 红态用例（有意为之，2026-10-14）

按 Task 2.1 的 TDD 顺序，板端运行包的判据先落成测试：`scripts/tests/test_rf_headroom_run_bundle.py`（12 用例，判据 = `docs/superpowers/specs/2026-10-13-headroom-board-run-package-design.md` §3–§7），**本轮按设计为红**。

| 命令范围 | 退出码 | 结果 |
|---|---:|---|
| 仅该文件 | 1 | 12 failed, 0 passed, 0 skipped（1.48 s） |
| `scripts/tests`（含该文件） | 1 | **12 failed, 496 passed, 1 skipped, 1 warning（144.68 s）** |

- **红态是有意为之**：实现落地前 `scripts/tests` 不再全绿；§10/§11 的 496 passed 是该文件加入**之前**的口径，两者不矛盾，也不得据此判断产品回归。
- 逐用例的期望/实际红态原因、今天已绿的子断言与未覆盖项：`evidence/rf-teaching/p1-run-bundle-red-2026-10-14/RED-NOTES.md`；原始日志 `red-run.log`、`red-full-suite.log`（含命令行、UTC 起止与退出码）。
- 本轮**未修改**任何产品源码、既有测试或测试期待值；由此产生的规格修订（发布实路径更正、接口固定、B1 缺口）记录在规格 §12。
