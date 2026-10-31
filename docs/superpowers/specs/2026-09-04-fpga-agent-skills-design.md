# FPGA Agent Skills 插件设计规格

- **日期**：2026-09-04
- **状态**：设计已获用户确认，等待规格审阅
- **插件名**：`fpga-agent-skills`
- **首版范围**：`plugin.json` + 九个 `SKILL.md`，不包含 commands、agents、hooks

## 1. 目标

为 `Vivado_prj` 下的 FPGA 工程提供一套可复用、可发现、可分层触发的开发技能，覆盖 RTL 设计、Verilog 审查、testbench、快速仿真、Vivado 构建、时序分析、波形调试、RF/DSP 和文档记录。

首个适配对象是 AC820 / Zynq-7020 / Vivado 2018.3 信号发生器，但上层版本必须把通用 FPGA 方法与 AC820/AWG 适配规则分开表达，使其他工程能够复用流程而不盲套当前工程路径、协议或硬件结论。

## 2. 非目标

首版不做：

- 自动化 slash commands；
- 专用 subagents；
- hooks、自动下载、自动提交或自动推送；
- 改造现有 `ac820-board` 技能；
- 把阶段四尚未完成的 DDR/DMA 板级门禁写成已验收；
- 将生成目录、旧 bitstream、旧 HDF/BSP/ELF 作为源码或验证依据。

## 3. 双路径部署

两处保留内容完全相同、版本一致的插件副本：

```text
<LOCAL_PATH>
<LOCAL_PATH>
```

上层目录是可复用主版本，当前工程目录是项目内可独立发现的同步副本。首版不使用 Windows 目录链接，避免权限、Git 和插件发现行为差异。后续变更先更新上层主版本，再同步到 `signal_generator` 副本，并保持 `plugin.json` 版本一致。

两份插件均采用已存在插件的布局约定：根目录 `plugin.json`，技能目录为 `skills/<skill-name>/SKILL.md`。

## 4. 目录结构

```text
fpga-agent-skills/
├── plugin.json
└── skills/
    ├── rtl-design/SKILL.md
    ├── verilog-review/SKILL.md
    ├── testbench/SKILL.md
    ├── iverilog-sim/SKILL.md
    ├── vivado-build/SKILL.md
    ├── timing-analysis/SKILL.md
    ├── waveform-debug/SKILL.md
    ├── rf-dsp/SKILL.md
    └── documentation/SKILL.md
```

每个技能的 frontmatter 只包含合法的 `name` 和以 `Use when...` 开头的触发描述。技能正文统一包含：Overview、When to Use、项目适配、Quick Reference、执行流程、常见错误、验证出口和明确的“不负责”边界。

## 5. 通用核心与项目适配

每个技能分为两个逻辑部分：

1. **通用核心**：适用于常见 Verilog/SystemVerilog、AXI/AXIS、CDC、仿真、Vivado 构建和测量工作。
2. **项目适配**：只有在检测到对应工程文件或工具入口时才启用。例如当前 AWG 的 `src/rtl`、`src/tb`、`host/tests`、`scripts/build_bitstream.py`、`scripts/check_bitstream.py`、`sdk/src/main.c`、AC820、Zynq-7020、Vivado 2018.3、IQ/RRC、AXI DMA 和 DSOX1102A。

技能必须先读取当前工程 `CLAUDE.md`、`README.md`、源码布局和构建入口。找不到适配文件时，使用通用流程并报告缺失信息，不把当前 AWG 的路径硬套到其他工程。

## 6. 技能职责

| 技能 | 触发条件 | 主要交付 | 边界 |
|---|---|---|---|
| `rtl-design` | 新增或修改 RTL、AXI、AXIS、CDC、状态机 | 可综合 RTL、接口契约、位宽/复位/时钟说明 | 不替代仿真、时序和板测 |
| `verilog-review` | RTL 审查、latch/X/Z/握手/CDC 怀疑 | 只读问题清单、严重度、证据和修复建议 | 默认不编辑代码 |
| `testbench` | 新功能、bug、边界行为验证 | 失败复现、GOLD、断言、场景矩阵 | 不用静态文本搜索替代仿真 |
| `iverilog-sim` | 快速 RTL 回归 | 实际仿真后端、编译/运行结果和限制 | Icarus 通过不等于 Vivado 通过 |
| `vivado-build` | BD、RTL、约束、HDF、bitstream、BSP/ELF | fresh 产物、manifest、哈希和构建日志 | 不绕过项目 fresh 构建脚本 |
| `timing-analysis` | WNS/TNS/WHS/THS、CDC、DRC、关键路径 | 报告证据、路径根因和最小修复建议 | 不用 false path 掩盖真实路径 |
| `waveform-debug` | 下载后频率、幅度、相位、BER、波形异常 | 测量配置、采样算法、原始结果和理论对照 | 不把理论值或单次 Vpp 写成验收 |
| `rf-dsp` | DDS、AM/FM/PM、IQ/QAM、RRC、采样和位序 | Python/RTL GOLD、公式、量化和误差预算 | 不修改协议或替代硬件构建 |
| `documentation` | 里程碑、故障复盘、验收、技能维护 | 可追溯记录、停止门、根因和未验证项 | 不凭推测写 PASS |

## 7. 技能协作顺序

```text
rf-dsp（算法/GOLD）
   ↓
rtl-design → verilog-review → testbench → iverilog-sim
   ↓                                  ↓
vivado-build → timing-analysis → waveform-debug
                                      ↓
                               documentation
```

技能之间通过显式技能名交叉引用，不使用 `@` 强制加载。某一停止门失败时，流程停在对应层级；不能以 Host 测试通过代替 RTL 仿真，也不能以 bitstream 构建成功代替板级验证。

## 8. 安全执行契约

1. 写入前读取当前 `git status` 和目标文件，保留用户现有未提交修改。
2. 源码只从 `src/rtl`、`src/tb`、`host`、`scripts`、`sdk/src` 等真实入口读取；`awg/`、`.Xil/`、`xsim.dir/`、`sdk/ws*/` 仅作生成产物。
3. 行为变更先建立失败测试，再实施最小修改；测试失败时保留证据并回到根因调查。
4. 构建产物必须标明来源、时间、hash 和工具版本；不按 mtime 直接信任 bitstream。
5. 下载、示波器控制、提交、推送和破坏性清理不由技能默认执行，除非用户明确授权。
6. 输出必须区分 `PASS`、`FAIL`、`BLOCKED` 和 `NOT RUN`，并列出下一停止门。

## 9. 当前 AWG 的关键适配规则

- 旧 GPIO 位域、PLAY_STATUS、PRBS7、LEGACY、BRAM USER 和 `SET DIGMOD SOURCE USER` 是兼容基线。
- Vivado 2018.3 必须重跑 BD、显式检查/恢复 OOC、执行 module_ref PRE 注入，并通过 manifest 防止旧 bitstream。
- OCM 调试 ELF 的代码、数据、heap、stack 和 BSP 运行时不能误链 DDR；fresh HDF/BSP/PS init 必须一致。
- RRC 可配置系数优先使用平衡加法树，避免 DSP48 级联造成长路径；FCLK0 与外部 `sys_clk` 的 CDC 需有正确异步约束和同步器。
- 阶段四采用 DDR → AXI DMA MM2S simple/direct register → async AXIS FIFO → USER stream source → IQ/RRC。
- DMA IOC 只代表 DMA 已完成向 AXIS 写入，不能提前关闭 PL stream；必须分别观察 FIFO drain、source done/underflow、IQ/RRC frame completion 和 RRC drain。
- DDR preset、training、alias、DMA/FIFO pattern、长 payload、≥3 MB/s 吞吐和新 bitstream 上阶段三示波器回归，在实际执行前均不得写成已通过。

## 10. 验证矩阵

### 技能文件验证

- 9 个技能目录和 `SKILL.md` 存在；frontmatter 名称合法、description 可触发且职责不重复。
- 每个技能至少有一个应用场景，能定位正确项目文件和验证出口。
- 组合场景不会跳过“设计 → 审查 → 测试 → 仿真 → 构建 → 时序 → 板测 → 文档”中的停止门。

### 当前工程验证

```text
RTL GOLD → Host pytest/compileall → fresh Vivado/HDF/BSP/ELF
→ check_bitstream → 下载（需授权）→ DDR/DMA gate → 阶段三回归
→ 长 payload/示波器 → documentation
```

Icarus 可用于快速、可移植 RTL；Vivado XSIM 用于 Vivado 语义、BD 或 vendor primitive 相关场景。工具不可用时，报告阻塞和可执行的离线替代，不声称目标后端通过。

## 11. 版本与同步验收

首版验收标准：

1. 两个路径存在同名、同版本、同内容的插件；
2. 九个技能可独立发现，触发描述无明显重叠；
3. 当前 AWG 场景能定位项目脚本、RTL/Host/SDK 入口和阶段停止门；
4. 其他 `Vivado_prj` 工程在缺少 AWG 文件时不会误执行 AWG 专用命令；
5. 文档输出不会把 DMA IOC、fresh 构建或静态 gate 脚本误报为 DDR 播放完成；
6. 所有未验证的板级项目均保留 `NOT RUN` 或 `BLOCKED` 状态。

## 12. 实施顺序

1. 创建上层主版本的 `plugin.json` 和九个技能文件。
2. 为每个技能补充通用核心与 AC820/AWG 适配段。
3. 完成 frontmatter、触发重叠和引用检查。
4. 对单技能应用场景和组合工作流做压力验证。
5. 将完全相同的内容同步到 `signal_generator/fpga-agent-skills`。
6. 由 `documentation` 技能记录版本、双路径文件清单和验证结果。

在用户审阅并批准本规格前，不执行上述实施步骤。
