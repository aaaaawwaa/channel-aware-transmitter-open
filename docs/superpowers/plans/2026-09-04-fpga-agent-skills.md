# FPGA Agent Skills Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `<LOCAL_PATH>` 和 `<LOCAL_PATH>` 各部署一份同版本、可复用且项目感知的 `fpga-agent-skills` 插件，覆盖当前 FPGA 工程从 RTL 到板测文档的完整开发链路。

**Architecture:** 上层 `<LOCAL_PATH>` 是主版本，工程目录下的 `fpga-agent-skills` 是完全相同的同步副本。插件只包含根目录 `plugin.json` 和九个职责单一的 `skills/<name>/SKILL.md`；通用 FPGA 规则与检测到 AC820/Zynq/Vivado 2018.3/AWG 文件后的适配规则写在同一技能内，不增加 commands、agents 或 hooks。

**Tech Stack:** Markdown Agent Skills、JSON、Verilog/SystemVerilog、Icarus Verilog、Vivado 2018.3/XVlog/XElab/XSim、Python 3/pytest、Xilinx SDK/XSCT、DSOX1102A/pyvisa。

---

## 文件结构与不变边界

### 创建文件

- `<LOCAL_PATH>` — 上层主插件元数据
- `<LOCAL_PATH>`
- `<LOCAL_PATH>`
- `<LOCAL_PATH>`
- `<LOCAL_PATH>`
- `<LOCAL_PATH>`
- `<LOCAL_PATH>`
- `<LOCAL_PATH>`
- `<LOCAL_PATH>`
- `<LOCAL_PATH>`
- `<LOCAL_PATH>` 下与上层完全相同的上述文件

### 不修改文件

- `src/rtl/**`、`src/tb/**`、`host/**`、`scripts/**`、`sdk/src/**`
- 现有 `<LOCAL_PATH>
- 现有 `CLAUDE.md`、`AGENTS.md`、`README.md`
- `awg/`、`.Xil/`、`xsim.dir/`、`sdk/ws*/` 等生成目录

---

### Task 1: 创建插件元数据和目录骨架

**Files:**
- Create: `<LOCAL_PATH>`
- Create: `<LOCAL_PATH>` and nine skill directories

- [ ] **Step 1: 验证创建位置**

在创建目录前运行：

```bash
ls -la "<LOCAL_PATH>"
ls -la "<LOCAL_PATH>"
```

Expected: 两个父目录都存在；不要在 `sdk/ws*`、`.claude/worktrees` 或用户级 `.claude/skills` 下创建插件主版本。

- [ ] **Step 2: 创建插件目录**

```bash
mkdir -p "<LOCAL_PATH>" \
  "<LOCAL_PATH>" \
  "<LOCAL_PATH>" \
  "<LOCAL_PATH>" \
  "<LOCAL_PATH>" \
  "<LOCAL_PATH>" \
  "<LOCAL_PATH>" \
  "<LOCAL_PATH>" \
  "<LOCAL_PATH>"
```

Expected: 只有上层主版本目录和九个空技能目录新增。

- [ ] **Step 3: 写入固定 manifest**

`plugin.json` 使用以下完整内容：

```json
{
  "name": "fpga-agent-skills",
  "version": "0.1.0",
  "description": "Project-aware FPGA development skills for RTL, simulation, Vivado builds, timing, RF/DSP, and board validation."
}
```

- [ ] **Step 4: 验证 JSON**

```bash
python -m json.tool "<LOCAL_PATH>"
```

Expected: 原样格式化 JSON，退出码 0；`name` 为 `fpga-agent-skills`，版本为 `0.1.0`。

---

### Task 2: 编写 `rtl-design` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景（不读取该技能）**

让验证代理处理以下场景，但明确不读取任何 `fpga-agent-skills` 文件：

> “修改 `src/rtl/user_stream_source.v`，使短 TLAST 能报告 underflow；当前工作树有未提交改动。请直接给出修改方案并运行最可能的构建命令。”

记录其是否跳过 git 状态、先改 RTL、忽略失败测试、把 DMA IOC/stream 生命周期混为一谈或使用生成目录。

- [ ] **Step 2: 写入技能正文**

`SKILL.md` 必须使用以下 frontmatter，并包含 Overview、When to Use、Core Rules、AC820 Adapter、Implementation Checklist、Common Mistakes、Validation Boundary：

```yaml
---
name: rtl-design
description: Use when adding or changing synthesizable Verilog/SystemVerilog RTL, AXI/AXIS interfaces, CDC logic, reset behavior, or FPGA datapaths in this project family.
---
```

正文必须明确：先读当前工程指南和目标文件；先写失败测试；保持旧 GPIO/PLAY/BRAM/PRBS/LEGACY 接口；端口位宽精确匹配；跨 FCLK0/`sys_clk` 用明确 CDC；AXIS 回压保持 `valid/data/keep/last`；DMA IOC 不等于 PL 完成；生成目录不是源码；不默认下载或提交。

AC820 适配段必须列出 `src/rtl/awg_top.v`、`iq_digital_mod.v`、`rrc_digital_mod.v`、`user_stream_source.v`，以及 1/2/4/6-bit consumption、MSB-first、RRC 32-sample drain 和阶段四独立 DDR 命令约束。

- [ ] **Step 3: GREEN 场景验证**

再次执行同一场景并要求代理读取技能。Expected：先核对未提交修改，先建立短 TLAST 失败场景，只修改必要 RTL；提出 standalone/IQ/RRC/顶层测试顺序；不声称 DMA/板级完成。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: rtl-design', 'description: Use when', 'When to Use', 'AXIS', 'DMA IOC', 'RRC', 'Validation Boundary'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 3: 编写 `verilog-review` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景**

不读取技能，审查一个包含以下问题的 RTL 片段：组合块漏 default、时序块混用阻塞赋值、窄 wire 连接宽端口、AXIS backpressure 时 data 改变、跨时钟直接采样多位 status。记录是否只看语法而遗漏综合/CDC/握手风险。

- [ ] **Step 2: 写入技能正文**

Frontmatter：

```yaml
---
name: verilog-review
description: Use when reviewing Verilog/SystemVerilog for synthesis semantics, CDC, reset, AXI/AXIS handshake, timing hazards, X/Z propagation, or compatibility regressions.
---
```

正文必须提供按严重度排序的审查表：功能正确性、综合语义、时钟/复位、AXIS 协议、位宽/X/Z、资源/时序、旧接口兼容。必须要求引用文件和行号、给出证据、区分事实和假设；默认只读，不擅自修改。

项目适配必须检查 `reg_bridge.v` 位域、2-FF 准静态配置、`user_stream_source` ready/hold、RRC drain、FCLK0 与 `sys_clk` 异步关系、DMA IOC 与 FIFO drain 分离。

- [ ] **Step 3: GREEN 场景验证**

Expected：代理至少报告五类问题，指出窄端口补 Z、直接跨域多位采样和回压数据变化的具体风险；不以“编译通过”给出 approve，不直接编辑文件。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: verilog-review', 'CDC', 'AXIS', 'X/Z', '行号', '只读'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 4: 编写 `testbench` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景**

不读取技能，要求代理为 stream source 增加测试。场景必须包含 zero length、跨 32-bit word、非字节对齐 bit length、合法/非法 TKEEP、短 TLAST、无 TLAST、回压保持、单周期 `frame_done_pulse`。记录是否只写 happy path 或用静态文本断言替代仿真。

- [ ] **Step 2: 写入技能正文**

Frontmatter：

```yaml
---
name: testbench
description: Use when creating or extending Verilog/SystemVerilog testbenches, GOLD vectors, assertions, coverage matrices, or regression scenarios for this FPGA project family.
---
```

正文必须强制 RED→GREEN→REFACTOR；要求测试先失败、失败输出可复现、场景覆盖正常/边界/错误/reset/backpressure；说明 standalone→IQ→RRC→`awg_top` 的层级顺序；不能用 Host 测试代替 RTL 仿真。

当前适配必须包含 `src/tb/user_stream_source_tb.v`、`iq_digital_mod_stream_tb.v`、`rrc_digital_mod_stream_tb.v`、`awg_top_stream_tb.v` 的四层 GOLD 目标，以及 BPSK/QPSK/QAM16/QAM64、1/2/4/6-bit、RRC 固定 drain。

- [ ] **Step 3: GREEN 场景验证**

Expected：代理先列出失败测试和预期失败，再实现；不把 TLAST 当作 RRC 完成，不漏 underflow、ready hold 或 source select 场景。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: testbench', 'RED', 'GREEN', 'TKEEP', 'TLAST', 'backpressure', 'RRC'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 5: 编写 `iverilog-sim` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景**

不读取技能，要求代理在当前工程运行 RTL 回归。记录其是否假定 `iverilog` 一定存在、把编译通过当作通过、忽略 `-g2012`/include/文件顺序，或用 Icarus 结果替代 XSIM/板级结论。

- [ ] **Step 2: 写入技能正文**

Frontmatter：

```yaml
---
name: iverilog-sim
description: Use when running fast RTL simulation or diagnosing Verilog compile/runtime failures, while distinguishing Icarus limitations from Vivado XSIM behavior.
---
```

正文必须规定：先检测 `iverilog -V`；可移植 RTL 用 `iverilog -g2012`；按依赖顺序列出源文件和 include；记录 compile/elaboration/run 三阶段输出；Icarus 不支持或结果不可信时使用 Vivado 2018.3 `xvlog`/`xelab`/`xsim`；BD/vendor primitive 场景必须标明不能由 Icarus 证明。

必须给出当前工程四个 stream GOLD 的 XSIM 后端选择和以下离线命令模板：

```bash
iverilog -g2012 -o sim.out <rtl-files> <tb-file>
vvp sim.out
```

- [ ] **Step 3: GREEN 场景验证**

Expected：代理报告实际后端、工具版本、编译/仿真结果和限制；`xsim` 不在 PATH 时给出绝对路径或离线阻塞，不伪造 PASS。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: iverilog-sim', 'iverilog -g2012', 'xvlog', 'xelab', 'xsim', '限制'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 6: 编写 `vivado-build` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景**

不读取技能，要求代理在修改 `src/rtl/awg_top.v` 后直接下载现有 bitstream。记录其是否绕过 `scripts/build_bitstream.py`、信任 mtime、忽略 BD/OOC/HDF/ELF 来源或把旧产物当作新版本。

- [ ] **Step 2: 写入技能正文**

Frontmatter：

```yaml
---
name: vivado-build
description: Use when building or validating Vivado block designs, OOC runs, bitstreams, HDF/BSP/ELF artifacts, or manifest provenance for this FPGA project family.
---
```

正文必须给出当前 Vivado 2018.3 fresh 流程：重跑 `scripts/create_bd.tcl`，显式检查/恢复 OOC，执行 `tools/synth1_read_awgtop.tcl` PRE 注入，综合/实现/bitstream，导出 HDF，fresh BSP/OCM ELF，运行 `scripts/check_bitstream.py`。必须覆盖 RTL/BD/PS7/Tcl/XDC hash、防旧 bitstream、绝对工具路径、Git Bash `/d/...` ARM GCC PATH。

必须明确：`awg/`、`.Xil/`、`sdk/ws*/` 不是源码；下载、提交和推送需要明确授权；当前阶段四 DMA IOC 不能结束 PL 播放。

- [ ] **Step 3: GREEN 场景验证**

Expected：代理先检查 git 状态和 manifest，再建议 fresh 构建；不会直接下载；能区分 bitstream/HDF/BSP/ELF 构建证据与板级 DDR/DMA 播放证据。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: vivado-build', 'build_bitstream.py', 'check_bitstream.py', 'OOC', 'manifest', 'HDF', 'OCM', 'DMA IOC'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 7: 编写 `timing-analysis` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景**

不读取技能，给代理一份包含 setup/hold、CDC、DRC、DSP48 级联和异步 FIFO 跨时钟问题的报告，要求“快速让构建通过”。记录其是否随意加 false path、只看 WNS、忽略 TNS/hold/CDC/DRC 或改回不满足需求的常量 RRC。

- [ ] **Step 2: 写入技能正文**

Frontmatter：

```yaml
---
name: timing-analysis
description: Use when analyzing Vivado timing, CDC, DRC, critical paths, DSP cascades, asynchronous clocks, or setup/hold failures in this FPGA project family.
---
```

正文必须要求同时读取 WNS/TNS/WHS/THS、failing endpoints、关键路径层级、CDC/DRC；先定位真实数据流和时钟关系，再最小修复；禁止用 false path 掩盖活动路径。项目适配必须写明 RRC 平衡加法树、DSP48 PCOUT→PCIN、FCLK0/`sys_clk` 异步组、异步 FIFO 和复位释放。

- [ ] **Step 3: GREEN 场景验证**

Expected：代理先分类 setup/hold/CDC/DRC，指出根因和证据；只有确认信号确实异步才建议异步时钟组；不会把 Warning/Advisory 伪装成无 Error 的完整验收，也不会仅报 WNS。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: timing-analysis', 'WNS', 'TNS', 'WHS', 'THS', 'CDC', 'DRC', 'false path', 'DSP48'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 8: 编写 `waveform-debug` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景**

不读取技能，要求代理用一次示波器 Vpp 测量判断 AM、FM、RRC 和噪声都正确。记录其是否忽略仪器遗留状态、采样窗相位、探头/偏移、FFT 边带、BER、复位清谐波或 USB-TMC 风险。

- [ ] **Step 2: 写入技能正文**

Frontmatter：

```yaml
---
name: waveform-debug
description: Use when debugging board-level DAC waveforms, frequency/amplitude/phase, BER, modulation sidebands, RRC output, or DSOX1102A measurements.
---
```

正文必须要求：先确认 fresh bitstream/SDK 版本；固定 CHAN1/2 `PROB=1`、`OFFS=0`、垂直刻度和时基；先清谐波并复位；使用相位不敏感统计量；AM 用 FFT 边带而非只看 Vpp；FM/PM 用 Bessel 边带比；SWEEP 用四分之一调制周期分块和间隔中值；RRC/BER 记录模式和 rolloff；USB-TMC/pyvisa 失败时报告阻塞，不伪造测量。

必须说明下载和示波器控制不默认执行，阶段四 DDR/DMA gate、stream done、RRC drain 和 1 MiB QAM64 结果分别记录。

- [ ] **Step 3: GREEN 场景验证**

Expected：代理先列出仪器状态和测量条件，再选择能区分故障的指标；不会用一次 Vpp 或理论值宣布板级通过。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: waveform-debug', 'pyvisa', 'PROB=1', 'OFFS=0', 'FFT', 'Bessel', 'BER', 'RRC'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 9: 编写 `rf-dsp` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景**

不读取技能，要求代理为 QAM64/RRC/AM/FM/PM 修改 RTL，并直接按理论公式判断正确。记录其是否忽略有限位宽、截断向零、MSB-first、符号相位、采样率、谐波锁相、RRC drain 和 Python/RTL GOLD。

- [ ] **Step 2: 写入技能正文**

Frontmatter：

```yaml
---
name: rf-dsp
description: Use when designing or verifying DDS, AM/FM/PM, IQ/QAM, PRBS, symbol-rate, RRC filtering, quantization, sampling, or Python/RTL DSP reference models.
---
```

正文必须规定先写 Python/reference，再定义量化、位序和误差，再对 RTL GOLD；当前适配必须覆盖：48-bit 调制累加器、FM/SWEEP 共用 `fword_inst`、PM 查表偏移、AM 位于 `ampl_scale` 前、BPSK/QPSK/QAM4/QAM16/QAM64、1/2/4/6-bit consumption、RRC 4 SPS/8-symbol span/33 taps/32-sample drain、MSB-first 和阶段四 AXIS payload。

必须保留 AC820 经验：偶次谐波按奇偶翻转、MODFREQ 为 0.1 Hz 单位、调制累加器自由运行时使用相位不敏感统计量；不把理论模型代替 XSIM、Vivado 或板级证据。

- [ ] **Step 3: GREEN 场景验证**

Expected：代理先给出可执行的 Python/RTL 对照和边界向量，再讨论 RTL 修改；能区分算法错误、量化误差、位序错误和板级链路问题。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: rf-dsp', 'QAM64', 'MSB-first', 'RRC', 'quant', 'Python', 'fword_inst'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 10: 编写 `documentation` 技能

**Files:**
- Create: `<LOCAL_PATH>`

- [ ] **Step 1: RED 压力场景**

不读取技能，要求代理根据“fresh bitstream、Host 401 passed、DMA IOC、尚未执行 DDR gate”写一段项目完成总结。记录其是否把 IOC 写成播放完成、把静态脚本写成板级 PASS、遗漏未验证项或没有记录产物 hash。

- [ ] **Step 2: 写入技能正文**

Frontmatter：

```yaml
---
name: documentation
description: Use when recording FPGA design decisions, verification evidence, failure analyses, release notes, milestone status, or reusable project knowledge.
---
```

正文必须提供固定记录模板：目标、范围、修改文件、命令、工具版本、实际输出、产物/hash、PASS/FAIL/BLOCKED/NOT RUN、根因、回归范围、未验证项、下一停止门。必须要求引用文件和行号、区分 offline/build/board 证据；阶段四模板必须把 DMA IOC、FIFO drain、source done、IQ/RRC completion、RRC drain 分开。

必须规定：不写 narrative-only 复盘，不把猜测写成事实，不覆盖现有用户文档，不把生成目录当源码；故障记录使用“症状→证据→根因→最小修复→回归”。

- [ ] **Step 3: GREEN 场景验证**

Expected：代理只报告构建/Host/IOC 已证实的层级，明确 DDR/DMA 板测为 `NOT RUN` 或 `BLOCKED`，并列出下一停止门；不会声称阶段四完成。

- [ ] **Step 4: 结构检查**

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'<LOCAL_PATH>').read_text(encoding='utf-8')
for text in ('name: documentation', 'PASS', 'FAIL', 'BLOCKED', 'NOT RUN', 'hash', 'DMA IOC', 'FIFO drain'):
    assert text in p, text
PY
```

Expected: 退出码 0。

---

### Task 11: 主版本技能集合验证

**Files:**
- Validate: `<LOCAL_PATH>`
- Validate: `<LOCAL_PATH>*/SKILL.md`

- [ ] **Step 1: 验证目录清单**

```bash
python - <<'PY'
from pathlib import Path
root = Path(r'<LOCAL_PATH>')
expected = {'rtl-design','verilog-review','testbench','iverilog-sim','vivado-build','timing-analysis','waveform-debug','rf-dsp','documentation'}
actual = {p.name for p in (root/'skills').iterdir() if p.is_dir()}
assert actual == expected, (actual, expected)
assert all((root/'skills'/name/'SKILL.md').is_file() for name in expected)
PY
```

Expected: 9 个目录准确匹配，没有额外总控技能。

- [ ] **Step 2: 验证 frontmatter 和触发描述**

```bash
python - <<'PY'
from pathlib import Path
import re
root = Path(r'<LOCAL_PATH>')
for path in sorted(root.glob('*/SKILL.md')):
    text = path.read_text(encoding='utf-8')
    assert text.startswith('---\n') and '\n---\n' in text
    block = text.split('\n---\n', 1)[0]
    name = re.search(r'^name: ([a-z0-9-]+)$', block, re.M).group(1)
    desc = re.search(r'^description: (.+)$', block, re.M).group(1)
    assert name == path.parent.name
    assert desc.startswith('Use when ')
    assert 'TODO' not in text and 'TBD' not in text
PY
```

Expected: 所有 frontmatter 名称与目录一致，description 可发现，没有占位符。

- [ ] **Step 3: 组合压力验证**

用一个新代理读取九个技能，给出完整任务：

> “修改 `user_stream_source` 增加短包处理，比较 BRAM/AXIS IQ/RRC 输出，运行快速仿真，准备 Vivado 构建，检查 timing，最后给出板测记录；当前 git 有未提交修改，DMA 仅报告 IOC。”

Expected：代理按 `rf-dsp → rtl-design → verilog-review → testbench → iverilog-sim → vivado-build → timing-analysis → waveform-debug → documentation` 组织工作；不跳过失败测试和 fresh build；不把 IOC 写成播放完成；不默认下载/提交。

---

### Task 12: 同步到当前工程副本

**Files:**
- Create: `<LOCAL_PATH>`
- Create: `<LOCAL_PATH>**\SKILL.md`

- [ ] **Step 1: 验证目标目录状态**

```bash
ls -la "<LOCAL_PATH>"
ls -la "<LOCAL_PATH>" 2>/dev/null || true
```

Expected: 若目标不存在则创建；若已存在，先逐项比较，不覆盖任何非本插件文件。

- [ ] **Step 2: 复制主版本内容**

```bash
mkdir -p "<LOCAL_PATH>"
cp -a "<LOCAL_PATH>" "<LOCAL_PATH>"
```

Expected：仅新增/更新插件目录，工程现有 `src/`、`host/`、`scripts/`、`sdk/` 和未提交修改不变。

- [ ] **Step 3: 验证双路径完全一致**

```bash
python - <<'PY'
from pathlib import Path
import filecmp
left = Path(r'<LOCAL_PATH>')
right = Path(r'<LOCAL_PATH>')
result = filecmp.dircmp(left, right)
assert not result.left_only and not result.right_only and not result.diff_files, (
    result.left_only, result.right_only, result.diff_files
)
PY
```

Expected：两份插件无缺失、无额外、无内容差异。

---

### Task 13: 最终验证和交付记录

**Files:**
- Validate: `<LOCAL_PATH>**`
- Validate: `<LOCAL_PATH>**`
- Validate: `<LOCAL_PATH>`

- [ ] **Step 1: 检查工程 git 状态**

```bash
git status --short
```

Expected：只出现实施前已有的工程修改和新增插件/规格文件；不得出现 RTL、Host、SDK 被意外改写的差异。不要使用 `git add -A`。

- [ ] **Step 2: 检查 Markdown 空白和 JSON**

```bash
python -m json.tool "<LOCAL_PATH>" >/dev/null
python -m json.tool "<LOCAL_PATH>" >/dev/null
git diff --check
```

Expected：JSON 和 diff 空白检查退出码均为 0。

- [ ] **Step 3: 记录交付状态**

在最终交付回复中固定报告：两个插件路径、版本 `0.1.0`、9 个技能清单、双路径一致性结果、技能压力验证结果、未执行的 Vivado/板级/示波器项目。阶段四 DMA IOC/PL drain 生命周期缺陷仍标为 `BLOCKED`，不得写成完成。

- [ ] **Step 4: 停止，不自动提交**

根据当前项目规则不创建 git commit、不推送远端。若用户之后明确要求提交，再单独检查 status/diff/log 并按提交协议操作。

---

## 完成标准

- 两个路径存在同名同版本、内容完全一致的插件。
- 九个技能均有合法 frontmatter、清晰触发条件、通用核心、当前 AWG 适配和验证出口。
- RED/GREEN 压力场景验证了未提交修改保护、fresh 构建、Icarus/XSIM 区分、DMA IOC/PL drain 边界和板级证据边界。
- 没有新增 commands、agents、hooks，也没有修改 FPGA 工程源码。
- 所有未执行的 DDR training、DMA/FIFO 板测、长 payload、吞吐和示波器回归均保持 `NOT RUN` 或 `BLOCKED`。
