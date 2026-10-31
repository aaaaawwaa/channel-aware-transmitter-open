# AC820 / AD9361 工程入口索引

> 2026-10-30整理入口：先读`README.md`与`docs/engineering/PROJECT-STRUCTURE.md`。以下保留辅助AWG/PYNQ历史导航；RF主项目使用`skills/final-prj-rf-development/SKILL.md`。源码定位优先codebase-memory-mcp；工具缺席时再用已知入口和文本搜索。

> **本文件是入口索引, 不是正文。** 详细内容、实测数字与踩坑经验都在 **`CLAUDE.md`**
> (同一目录, 持续维护)。这里只保留"去哪儿找"和几条最容易踩的坑 ——
> 早先这里维护过一份完整副本, 结果它与 `CLAUDE.md` 分叉、数字全部过期(还写着
> Vivado 2018.3 / 150 条断言 / 54 项闭环)。**不要再往本文件堆正文。**

## 两条流水线(互相独立)

| | FPGA 侧(PL + 裸机 PS) | 板载 Linux 侧(PYNQ) |
|---|---|---|
| 目录 | `src/rtl/`、`src/tb/`、`scripts/`、`sdk/src/main.c`、`host/` | `pynq_overlay/`(BD/RTL/tb/constraints/sw) |
| 构建 | `python scripts/build_bitstream.py` | `vivado -mode batch -source pynq_overlay/build.tcl` |
| 产物 | `awg/awg.runs/impl_1/host_bd_wrapper.bit` | `pynq_overlay/out/ledbtn.bit` + `.hwh` |
| 上板 | `bash scripts/program_board.sh`(JTAG) | scp 到 `/home/xilinx/overlays/awg_merged/`, 守护进程 `Overlay()` |
| 验收 | `scripts/test_bridge.py COM11` / `scope_verify.py COM11` | `pynq_overlay/sw/tests/ddr_e2e.py` / `p7_e2e.py`(先停守护进程) |
| 技能 | `ac820-board` | `pynq-linux-development` |

工具链是 **Vivado/Vitis 2025.2**(禁用旧 `cli-anything-vivado` —— 它只配到 2018.3)。

## 最容易踩的几条(全文见 `CLAUDE.md` 同名章节)

- **改了 `src/rtl/*.v` 后直接 synth/下载会拿到旧 bitstream** —— `awg_top` 是 BD 内模块,
  必须走 `scripts/build_bitstream.py`(它会重跑 `create_bd.tcl` 并在线综合 `awg_top`)。
- **BD 内嵌 `blk_mem_gen` 的 `Enable_32bit_Address` 必须为 true** —— 否则被**静默**综合成
  没有任何存储器件的空网表(AXI 照常握手, 读回恒 0), 极易误判成软件问题。
- **板载侧: 守护进程持着 PL 时绝不能再调 `Overlay()`** —— 会把板子卡死(已两次)。
  板端解释器与环境依当前镜像配置；权限从本地私有配置或连接通道传入，不在命令中内嵌口令。
- **连板子用 `pynq-linux-development` 相关的现有Paramiko连接封装（先定位当前可用脚本）, 别用 `ssh`/`scp`** ——
  非交互时 Windows 会弹图形化凭据框把命令卡死。

## 辅助AWG的PC GUI入口

工程根目录下 `python host/main.py`(或 `host/.venv/Scripts/python.exe host/main.py`)。
不要从 `.claude/worktrees/` 的副本或 `sdk/ws*/` 生成目录启动。

RF产品界面在板载PYNQ应用中运行，入口见根README和工程结构文档。

## 其他

- 板子资料/引脚/启动模式: 技能 `ac820-board`
- PYNQ(板载 Linux / overlay / 触摸 / 守护进程): 技能 `pynq-linux-development`
- 上位机开发经验: 技能 `fpga-host-app`

## 工程记忆（docs/memory/）

- **当前审查入口**：[个人开发审查与部署规则](docs/requirements/development-review-policy.md)（2026-10-17 用户要求精简）。采用按影响分级、相关测试、R2 一次增量审查；旧文档中的重复全审/固定变异数量/逐步确认要求按此调整。
- **开发部署闭环**：[一次审查、试部署与回滚](docs/rf/deployment-trial-runbook.md)。同次审查覆盖候选、板端验收与回退计划；获准后连续执行，通过保留、失败回滚读回，不重开多轮离线审查。

- 索引与读法: `docs/memory/README.md`（谁在什么时候读哪一份 + 权威来源指针）。
- `01-project-map.md` — 三条流水线（AWG/PYNQ/RF）的构建与验收入口、四条并行角色与写范围纪律。
- `02-gate-model.md` — 六类门（离线→ARM→数字回环→有线物理→真人→发布）、状态阶梯
  `CODED→OFFLINE_VERIFIED→ARM_VERIFIED→BOARD_VERIFIED→HUMAN_VERIFIED→RELEASED`、
  物理派发 G0–G8、封存链 G1–G4、HEADROOM 候选链实例。
- `03-development-discipline.md` — 先红后绿、非实现者独立复审、变异战役、冻结判据文件
  （12 例冻结判据 / 46 项软件 / 58 项原生源）、单次尝试不重试、账本与 nonce 一次性、
  provenance 旧字节归档、停止条件。
- `04-engineering-pitfalls.md` — 真实事故提炼：CRLF 锚点 0 命中、`git add -f`、`--basetemp`、
  `__pycache__` / `nonregular candidate member`、`mkdir(exist_ok=False)` 先归档、板钟无 RTC 与 `boot_id`、
  token 只记路径不读值、帧内四键层级与夹具同形、加守卫后重跑旧变异、巨型临时树不入库。
- `05-evidence-index.md` — 权威收据/报告索引（P0、S2 环境、46 dB HEADROOM G1/G2/G3/G3b/G4、时钟锚、封存链）。
- `06-open-questions.md` — 未证实项与待决事项，每条写“需要什么才能关闭”。
- **动手前先读 03 与 04。** 本目录只做指针与整合；判据、数字与状态以原始收据/规格/报告为准。


## 可复用技能与分享

- 通用PYNQ：`skills/pynq-linux-development/SKILL.md`
- 本项目：`skills/final-prj-rf-development/SKILL.md`
- 分享/归档：`docs/engineering/SHARING-AND-CLEANUP.md`；不修改原始证据摘要链。
