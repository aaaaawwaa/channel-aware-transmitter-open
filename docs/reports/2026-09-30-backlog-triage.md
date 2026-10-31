# 积压分诊清单 (2026-09-30)

范围: 集成工作树 76 项 + 主工程 136 项未提交内容, 跨多个 session。
本文**不改动任何文件**, 只做分类与建议, 由操作员逐项拍板。
统计来源: `triage_scan.py` 对两边 `git status --porcelain` 的分类(见 `triage-scan.log`)。

## 0. 最重要的一条: 板上的 bit / daemon 无法从任何提交复现

- 集成工作树 HEAD = `07a1b55`(一个 docs-only 提交)。
- 板卡现跑的 bit `d5bfe5d9…` 是 2026-09-29 14:35 用**工作区**构建的, 而工作区里这些是
  **已修改、未提交**: `src/constraints/awg.xdc`、`scripts/create_bd.tcl`、
  `scripts/bootstrap_project.tcl`、`scripts/ps7_props.tcl`、`scripts/build_bitstream.py`、
  `tools/impl1_clock_groups.tcl`、`tools/rebuild_bitstream.tcl`。
  `create_bd.tcl` 定义 BD, `awg.xdc` 是约束 —— 两者都直接决定综合结果。
- 板上 daemon `605ae0a7…` 来自 `rf_overlay/sw/board_app/` 下 **27 个从未入库**的文件
  (`rf_daemon.c`、`rf_state.c`、`rf_protocol.c`、`rf_iq.c`、`rf_pl_status_reader.c`、
  `rf_daemon_main.c`、`rf_version.h` 与 6 个 C 测试)。

⇒ **没有任何提交能重建板卡上正在运行的东西**。这是本清单唯一的**高危项**: 它目前只存在于
这一台机器的硬盘上。Tier A 就是为它准备的。

## 1. 分类统计

| 类别 | 工作树 | 主工程 |
|---|---|---|
| RTL / 约束 / BD 脚本(改硬件行为) | 13 | 46 |
| 板载 C 固件 `rf_overlay/sw/board_app/` | 27 | 2 |
| 板载 Python / 面板 | 14 | 14 |
| 上位机 `host/` | 6 | 5 |
| 构建产物 / 生成物 | 2 | 1 |
| agent 脚手架 / 技能 | 0 | 3 |
| 文档 | 9 | 10 |
| 证据 / 探针 | 4 | 27 |
| 未分类(根目录串口转储等) | 1 | 28 |
| **合计** | **76** | **136** |

## 2. Tier A — 必须入库(否则板卡不可复现)

- **A1 `rf_overlay/sw/board_app/` 的 C 固件与测试(27 项)**: 9 个 ` M` + 18 个 `??`,
  含 daemon 全套与 `test/` 下 6 个 C 测试。
- **A2 硬件构建输入(13 项 ` M`)**: `src/constraints/awg.xdc`、`scripts/{create_bd,
  bootstrap_project, ps7_props}.tcl`、`scripts/build_bitstream.py`、
  `scripts/release_manifest.py`、`tools/{impl1_clock_groups,rebuild_bitstream}.tcl`。
- **A3 打包/发布脚本(5 项 `??` in `scripts/`)**: `merged_release.py`、
  `package_merged_overlay.py`、`rf_build_daemon.py`、`rf_release_manifest.py`、
  `rf_verify_release.py`。它们是 release 身份与 `--require-software` 的唯一真源。

建议: 分 3 个提交入库; 提交信息写明"板卡现跑的 bit / daemon 由这些文件构建"。
**注意**: A2 一旦入库, `release_id` 与 bit 复现路径才会成立。
## 3. Tier B — 复核后入库(板载 / 上位机软件与文档)

- 工作树: `pynq_overlay/sw` 与 `rf_overlay/sw` 其余 14 项、`host/` 6 项、`docs/` 9 项、探针 4 项。
- 主工程: `docs/` 10 项。
建议: 逐目录看一遍 diff 再提交; 面板与上位机多数已有测试覆盖。

## 4. Tier C — 建议不要入库(调试草稿)

- 主工程 `tools/_*.tcl` 约 30 个: `_peek.tcl`、`_wedge_state.tcl`、`_recover_via_dap.tcl`、
  `_rst_variants2.tcl` … 都是启动卡死时代的一次性 JTAG 侦察脚本(下划线前缀本身就是临时标记)。
- 主工程根目录 27 个 `serial_boot_*.txt` / `boot_capture*.txt` / `pspl_*.txt`: 调试转储, 不该
  躺在仓库根目录。
建议: 移到 `evidence/` 或删除, 并加 `.gitignore` 规则; **但先 grep 引用** —— 交付记录里点名
引用了其中若干文件名。

## 5. Tier D — 加 `.gitignore`, 不要提交

`merged_build_receipt.json`(--repackage 生成物)、`__pycache__`、`.pytest_cache`、`xsim.dir`、
`.Xil`、`*.jou` / `*.log` / `*.wdb` / `*.pb`、构建输出 `out/` 与 `out_merged/`。

## 6. Tier E — 保留, 且不要删(下一步要用)

- 主工程 `tools/serial_*.py`: `serial_ask.py`、`serial_sniff.py`、`serial_boot_watch.py`、
  `serial_drive.py`、`serial_stop_uboot.py`、`serial_boot_extra.py` —— 这是 §7 第 1 项
  `reboot` 定位需要的**持续串口捕获**工具。
- 主工程 `tools/` 里的 JTAG 恢复脚本(如 `_jtag_program_linux.tcl`、`_rst_recover*.tcl`、
  `_unwedge_reprogram.tcl`): 虽是一次性草稿, 但上次板卡失联就是靠这类脚本救回来的 ——
  建议**归档到 `evidence/`** 而不是删。

## 7. 主工程的源码改动要单独判断

主工程 `master` 上还有 10 项源码改动(`src/constraints/awg.xdc`、`scripts/*`、`tools/*.tcl`、
`rf_overlay/sw/board_app/{platform_rf.h,rf_main.c}`、`scripts/program_board.sh`)。
集成分支才是这些改动的权威副本, 主工程上的很可能是同一批工作的**旧副本**。
建议: 先 diff 两边; 确认是旧副本就丢弃, 不要在主工程 `master` 上再提交一份源码。

## 8. 建议顺序

1. Tier A 三项提交(最高优先, 防止硬件不可复现)。
2. Tier D 加 `.gitignore`。
3. Tier C 归位 / 清理(先 grep 引用)。
4. Tier B 复核后提交。
5. 主工程源码改动 diff 判定。

## 9. 本轮做了什么 / 没做什么

- 做了: 上述统计与分类(可复算: `triage_scan.py` 与 `triage-scan.log`); 本 session 切片的
  提交(交付记录 §44); 把 Tier A 的风险显式写出来。
- 没做: **没有**按 Tier A/B 提交任何东西(需操作员拍板); **没有**删除或移动任何文件;
  **没有**改 `.gitignore`。本清单是**建议**, 不是已执行的变更。