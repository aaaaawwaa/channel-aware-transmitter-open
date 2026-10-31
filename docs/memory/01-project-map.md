# 01 工程地图

> 目的：让人在 10 分钟内知道"东西在哪、怎么构建、怎么验收、谁在写"。
> 已写过的大表（三条流水线）**只索引不复制**：见 [CLAUDE.md](../../CLAUDE.md) "三条流水线"表
> 与 [AGENTS.md](../../AGENTS.md) "两条流水线"表。本文只补它们没写的东西。

## 1. 三条流水线（事实）

| | AWG（PL + 裸机 PS） | PYNQ 板载 Linux | RF（AD9361 / FMCOMMS3） |
|---|---|---|---|
| 目录 | `src/rtl/`、`src/tb/`、`scripts/`、`sdk/src/main.c`、`host/` | `pynq_overlay/`（bd/rtl/tb/constraints/sw/tools） | `rf_overlay/`（bd/rtl/rtl_ref/tb/constraints/sw/tools） |
| 构建 | `python scripts/build_bitstream.py` | `vivado -mode batch -source pynq_overlay/build.tcl` | `vivado -mode batch -source rf_overlay/build.tcl` |
| 板上验收 | `scripts/test_bridge.py COM11`、`scope_verify.py COM11` | `pynq_overlay/sw/tests/`（`ddr_e2e.py`、`p7_e2e.py`；先停守护进程） | `docs/rf/boardtest.md`（判据先于测量） |
| 独立工具链 | Vivado/Vitis 2025.2（禁用只配 2018.3 的旧插件） | 同左 + 板端原生 gcc | 同左 + 板端原生 gcc |

**注（事实）**：`AGENTS.md` 的表只列两条（AWG / PYNQ），`CLAUDE.md` 列三条（含 RF）。
**以 CLAUDE.md 为准**；`AGENTS.md` 自己声明"是索引不是正文、不要再堆正文"，历史上曾因复制正文而分叉过期。

## 2. 目录角色（本文新增的整合；其余见 §1 指针）

- `scripts/`（顶层 70+ 个 `.py`，按前缀成族）：
  - `rf_environment_*`（S2 环境诊断：candidate / freeze / acceptance / deploy / offline / pilot / board_run …）
  - `rf_headroom_*`（HEADROOM-1 研究：prepare / run_bundle / job / research_run / deploy）
  - `rf_bpsk_*`、`rf_teaching_*`、`rf_capture_*`、`rf_receiver_parity*`、`rf_gain_observation_*`
  - AWG/PL 侧：`build_bitstream.py`、`create_bd.tcl`、`check_bitstream.py`、`scope_verify*.py`、`test_bridge.py`、
    `ddr_*_gate.py`、`udp_ddr_pp_gate.py`、`stage5_fault_gate.py`、`fat_structure_audit.py`（主机侧离线 FAT 结构审计）、
    `package_merged_overlay.py`、`merged_release.py`
  - `scripts/tests/`：28 个测试文件；**冻结判据文件**见 [03-development-discipline.md](03-development-discipline.md) §4
- `pynq_overlay/sw/`：板载 Python（`awg_ctl.py`、`ui_model.py`、`ui_view.py`、`rf_headroom.py`、`rf_status_client.py`、
  `rf_status_worker.py`、`rf_experiment_record.py` 等）+ `install.sh` / `pynq-status.service`；测试在 `pynq_overlay/sw/tests/`
- `rf_overlay/sw/board_app/`：板端 C 原生（`rf_daemon.c`、`rf_daemon_main.c`、`rf_core.c`、`platform_rf.[ch]`）
  + `test/`（含 `fake_pl.c` 假硬件）；`make`（板端原生 gcc）/ `make host-test`（开发机）
- `host/`：上位机（PySide6 GUI，唯一入口 `python host/main.py`）+ `host/tests/`
- `evidence/`：原始收据/日志/帧；`docs/`：报告、规格、计划、需求；`docs/memory/`：本目录

## 3. 四条并行角色（经验，来自 2026-10-17 的 Agent Teams 实践）

同一个工作目录、同一份文件系统，**没有锁**，只有纪律：

| 角色 | 职责 | 边界 |
|---|---|---|
| **Lead** | 分派、整合、最终验收与最终回复 | 不亲自写别人正在写的文件；最终必须自己跑一遍验收命令 |
| **子代理（subagent，无历史）** | 一次性的、自包含的只读/离线任务（盘点、复算、写报告） | 不并发写同一文件；结论必须回带路径与摘要 |
| **队友（teammate，持久）** | 有独立写范围的长任务（如本目录、docs/reports、runbook） | **写范围互斥**：任务卡上登记 `write_scopes` |
| **独立复审者（非实现者）** | 只审不写；给 `APPROVED` / `CHANGES_REQUIRED` | **实现者不得自签**；不得改写历史收据 |

**写范围纪律（经验，硬性）**：
1. 派活前先声明**预期写入的文件/目录**，重叠即视为冲突（advisory，不是锁）。
2. 只允许写自己范围内的文件；要改别人的文件先发消息，不"顺手改"。
3. **共享单例资源串行**：板卡、`current` 发布、pytest 全局临时目录、git index。
4. 并发前先跑 `git status --porcelain`；看到别人的未跟踪目录**不动、不删、不采纳**（先例：G3b 对
   上一轮 167 MB 未跟踪 pytest 临时树"保留不删"）。
5. 写完后 Lead 必须看 `git status --porcelain` 与最终 diff，再自行复跑关键测试。

## 4. 板卡是唯一资源（事实 + 经验）

- 单板单任务：**同一时刻只允许一个会话做板卡写入**；读也要按"只读会话 + 只读读回"记账。
- 板端脚本一律在 `sudo` 下用 `-B` / `PYTHONDONTWRITEBYTECODE=1` 运行（原因见 [04](04-engineering-pitfalls.md) 第 4 条）。
- 连板用 `ac820-pynq` 技能的 `scripts/pynq_ssh.py`（paramiko），**不要**用系统 `ssh` / `scp`。
- 守护进程持着 PL 时**绝不能再调 `Overlay()`**。
- 不并发 `Overlay()`；SSH 超时/终态不明时**另开只读会话**查账本与安全状态，**不盲重试**。

## 5. 当前重心与工作区（事实，截至 2026-10-17）

- 工作区：`<LOCAL_PATH>`（分支 `codex/description-aligned-platform-plan`）。
- 重心切换决策与边界：[docs/reports/2026-10-13-final-prj-focus-decision.md](../reports/2026-10-13-final-prj-focus-decision.md)
  —— 新实现/测试/规划集中在 `final_prj`；源工程 `signal_generator` 冻结为新工作的只读输入。
- 实施分批（Chunk 1 P0 → Chunk 2 P1 → Chunk 3 P2）：[2026-10-13-final-prj-implementation-plan.md](../superpowers/plans/2026-10-13-final-prj-implementation-plan.md)。
- 三条流水线的**真实状态**不要从这里推断 —— 见 [05-evidence-index.md](05-evidence-index.md) 与
  [docs/requirements/brief-traceability.md](../requirements/brief-traceability.md)。
