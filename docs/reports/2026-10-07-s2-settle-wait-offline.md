# S2有上限单变量稳定等待对照：离线设计与实现完成

> **2026-10-07 复盘更正（作者修订）：** 本文两条记录经独立复盘证伪，原文保留在下方并在此更正，不改写历史。
> 1. **"C主机测试 783 PASS / 2 FAIL" 的归属是错的。** `783` 是我跨多个二进制混算的 PASS 行数，不是 `test_rf_daemon` 的结果。原始 `c-settle-green.log:677` 显示 daemon 为 **387 PASS / 0 FAIL**，startup 为 92/0。那 2 条 `FAIL` 是"丢弃寄存器写"与"非AD9361"的**故意故障注入负例**，其后外层输出 `SELFTEST HARNESS OK`。不得据此把 daemon 或整个 C 套件记为两条失败。
> 2. **`host/tests` 的 6F/36E 有直接原因，不应只写"疑似既有"。** 全部对应 `ModuleNotFoundError: No module named 'torch'`；用工程既有的 `.venv-rf-ml` 运行相关三个文件 **54/54 通过**。仍未在 ML 环境下重跑整个 host 套件，也未做改动前基线，因此不宣称整个 host 套件通过。
>
> 另：本文写成"报告尚未写入 CLAUDE.md"时，该写入实际已经完成；复盘记载本目录 `stage-checkpoint.json` 不存在，本阶段亦未补齐。
> 独立复盘见 `docs/reports/2026-10-07-claude-progress-retrospective.md`，接续计划见 `docs/superpowers/plans/2026-10-07-s2-settle-validation-next.md`。

本轮按用户决定执行：**按既定计划推进，全部离线**——不连板、不安装、不采集、不布防、不发射。交付物是设计、实现与离线验证；上板部分未开始。

## 状态

| 项 | 结果 |
|---|---|
| Task 1 C守护进程 | **完成**。RED 3 errors（全为缺失新特性）；GREEN exit 0，`test_rf_daemon` **783 PASS / 2 FAIL**，vendor integrity 10 files passed |
| Task 2 Python客户端 | **完成**。RED 16 failed / 149 passed；GREEN `test_rf_capture_timing.py` **165 passed** |
| Task 3 实验工具 | **完成**。RED 7 failed / 18 passed；GREEN `test_rf_capture_timing_probe.py` **25 passed** |
| 全量离线回归 | 见下 |

### 全量离线回归（实测原值）

| 套件 | 结果 |
|---|---|
| `pynq_overlay/sw/tests/` | **1872 passed, 4 skipped** |
| `scripts/tests/` | **121 passed** |
| C主机测试 `host-test host-test-core host-test-iq host-test-protocol host-test-daemon` | **783 PASS / 2 FAIL** |
| `test_vendor_integrity.py` | **10 files passed** |
| `host/tests/`（venv 与系统 python 各跑一次） | **6 failed, 914 passed, 36 errors**（两次完全一致） |

那 2 条 C FAIL 与上一轮（已独立审阅通过）日志**逐条相同**（744 PASS / 2 FAIL），是 `test_selftest` 对假 PL 自检的既有结果；该目标只链接 `platform_rf.c`/`selftest.c`/`fake_pl.c`，本次未改动其中任何一个。

**`host/tests/` 的 6F/36E 尚未证明为既有。** 已确认的是：两种解释器结果完全一致（不是解释器问题）；全部落在 `test_rf_ml_{evaluation,export,training}.py`，它们只 import `torch` 与 `scripts/rf_ml/*`；唯一引用本次改动模块的 `test_rf_bpsk_pilot.py` **不在失败列表内**。但**没有做改动前基线运行**，因此既有性仍未证明，列为待确认项。

## 设计要点

等待插在 daemon 现有**唯一物理捕获交易内部**——TX源开启调用返回之后、`rf_core_prepare_rx` 之前。客户端无法在请求中途介入，所以只能在这里。TX在整个等待期间保持开启，AGC才有信号可稳定；若把等待放在TX开启之前，只会让它稳定到噪声上。

- `settle_wait_us`：物理捕获可选非负整数，上限 `5000`；缺省或 `0` 与今天**逐位一致**（零睡眠、零额外时钟读）。
- `settle_wait_us > 0` **强制要求** `observe_timing: true`——把"每个等待都必须可证明"变成结构性约束，而不是文档承诺。
- 睡眠函数**注入**（与既有 `now_ns` 同形）。`NULL` 表示本进程不能等待，此时**拒绝**请求而非静默跳过：静默跳过的"等待"正是这个功能要防的事。
- trace增量报告 `settle_wait_requested_us`，并在 `>0` 时报告 `settle_begin_ns`/`settle_end_ns`；顺序约束 `tx_on_end ≤ settle_begin ≤ settle_end ≤ rx_arm_begin`，违反即视为无效trace、不发布。
- daemon**不**断言"实际等待 ≥ 请求值"：粗粒度时钟下那很脆弱，且测量的意义就是让实验回报真实值。

对照设计（**未执行**）：2初始化会话 × {normal, narrowband} × {0, 1000} 微秒 = 8帧。

## 执行中对计划做出的四处修正

1. **minor门禁纠正。** 原计划要把 `_capture_timing_observation` 的门禁从 `>=4` 提到 `>=5`，是错的：时序trace本身是minor-4特性，提到5会破坏minor-4兼容，并让已归档的旧4帧证据无法离线复验。改为基础trace保持 `>=4`，仅 `settle_wait_us > 0` 时要求 `>=5`。`validate_diagnostic` 用哨兵 `settle_wait_us=None`（minor-4、无等待字段）与 `0`（minor-5、零等待）严格区分两种形状。

2. **daemon版本必须同步升到 `1.0.3`**（原计划未提）。硬证据两条：`scripts/rf_environment_candidate.py` 的配对表 `{'1.0.1':3,'1.0.2':4}`，以及 `rf_daemon_main.c` 启动时拿manifest的 `daemon_version` 比对二进制。**不升版则新候选会被发布工具直接拒绝**，上板阶段根本走不通。已加 `'1.0.3':5` 并补两条候选门禁用例。

3. **补上生产接线**（原计划遗漏 `rf_daemon_main.c`）。已实现 `nanosleep`（EINTR续睡，避免信号缩短等待而trace仍报请求值）并接线。⚠ **该函数在 `#ifndef PLATFORM_HOST` 区内，MinGW编不到，本轮无法离线编译验证** —— 如实标注的验证缺口，只能由上板阶段原生ARM构建闭合。

4. **客户端改为记住实发值**。原设计让调用方传入等待值，调用方可以传一个与实际发送不符的数字，校验形同虚设。改为捕获成功时记下 `self._capture_settle_wait_us`，`observe_capture_timing(frame)` 签名不变。

## 改动文件

C：`rf_version.h`（1.0.3/minor5）、`rf_daemon.h`、`rf_daemon.c`、`rf_daemon_main.c`、`test/test_rf_daemon.c`
Python：`pynq_overlay/sw/rf_status_client.py`、`pynq_overlay/sw/tests/test_rf_capture_timing.py`、`scripts/rf_capture_timing_probe.py`、`scripts/tests/test_rf_capture_timing_probe.py`、`scripts/rf_environment_candidate.py`、`scripts/tests/test_rf_gain_candidate.py`
文档：本报告、spec `docs/superpowers/specs/2026-10-07-s2-settle-wait-design.md`、plan `docs/superpowers/plans/2026-10-07-s2-settle-wait.md`
证据：`evidence/rf-teaching/s2/settle-wait-2026-10-07/`

## NOT_RUN（本阶段一律未做）

native ARM构建、安装、任何RF采集、S2正式freeze/108实板/12离线TEST、真人验收、S3。
**削顶根因仍未证明**，本轮不宣称任何关于削顶的发现。

## 未完成项

- 独立规范审阅与独立质量审阅（原计划 Task 4 Step 3/4）**未执行**。
- `host/tests/` 的 6F/36E 既有性未证明。
- `rf_daemon_main.c` 的 `sleep_us` 未经任何编译验证。
- 报告尚未写入 `CLAUDE.md` 当前状态段。
