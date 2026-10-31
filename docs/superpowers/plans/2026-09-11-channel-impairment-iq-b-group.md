# 信道损伤扩展 B 组（复数 I/Q 损伤）实施计划

> **For agentic workers:** 逐任务实现，每任务先写失败测试再写实现（TDD）。用
> `- [ ]` 复选框跟踪进度。

**Goal:** 在数字调制支路上新增**唯一的复数级** `iq_impair`：静态 2×2 矩阵（相位偏移 +
I/Q 增益失配 + 正交相位误差，由主机合成为一个矩阵）+ 频偏旋转器（`sincos_rom`）。

**Spec:** `docs/superpowers/specs/2026-09-10-channel-impairment-extension-design.md`
第 1/2/3/4/5/7 节（B 组部分）。前置：A 组（tone/impulse）已实现并上板验收。

**Tech Stack:** Verilog-2001（纯 RTL，无新 IP）、Vivado 2025.2 XSIM、Zynq-7020 裸机 C
（`sdk/src/main.c`）、Python 3（上位机 + 测试）、DSOX1102A 示波器闭环。

---

## Global Constraints

- **不新增 BD IP、不改 BD 结构**：`iq_impair` / `sincos_rom` 都是普通 RTL 模块，
  `chan_cfg` 的排空表只扩步数。BRAM 字 8/9/10 已存在（声明深度 16），**不需要动
  `create_bd.tcl`**，因此正常 `build_bitstream.py` 即可，也**不需要**重新生成 BSP。
- **时序**：当前基线 **WNS 0.192 ns**（A 组重建实测，见 CLAUDE.md）。新增强制：
  - 每级乘法后**先注册乘积**，加法走**显式平衡树**，**严禁 DSP48 `PCOUT→PCIN` 级联**
    —— 照抄 `fade_fir.v:53-55`（`acc_r <= (prod_r[0]+prod_r[1]) + (prod_r[2]+prod_r[3]);`）
    与 `rrc_filter.v:237-240` 的分组写法。
  - I 与 Q 路径**结构必须对称、延时必须相同**（否则引入人为 I/Q 相位失配，污染被测项）。
- **`enable=0` 逐位透传**：`iq_impair` 关闭时输出必须与输入逐位一致（TB 钉死）。
  这是既有全部示波器用例不回归的前提。
- **饱和边界 ±127**（镜像 `rrc_to_dac`），不是 8-bit 的 −128。
- **仿真工具**：`SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"`；流程
  `"$SIM/xvlog.bat" --sv <files>` → `"$SIM/xelab.bat" <tb> -s <sim>` →
  `"$SIM/xsim.bat" <sim> -runall`。
- **上位机测试**：`host/.venv/Scripts/python -m pytest host/tests/`。
- **提交信息**：Conventional Commits 英文主题行 + 说明"为什么"的正文 +
  `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`。

## 判据范式（本组最重要的验证约定）

既有 A 组用例的核心判据是 `BER == 0`，而 B 组的**目的恰恰是打破链路**。故 B 组用例
一律改为「**实测统计量 ≈ 主机模型**」，且**不拿数字域理论值做绝对比对**（凡过 DAC 零阶
保持 + 模拟重建的量都有模拟域衰减 —— 与噪声 σ 实测只有数字域 0.65 倍同一机制）。

| 项 | 判据 |
|---|---|
| I/Q 相位偏移 | `align_known_sequence` 重建星座，旋转角 == 设定值 |
| I/Q 增益/相位失配 | 星座二阶统计：`E[I·Q] ≠ 0`、协方差特征值比，与模型比 |
| I/Q 频率偏移 | 连续符号上的星座旋转速率 == 设定 Δf |

**不用"单音镜像"测 IRR**：数字调制通路送的是符号比特，连续相同比特给出的是直流而非
单音，造不出干净的复单音；从星座协方差直接读失衡量更稳且复用现成 QAM 捕获。

---

## 需要先确认的设计决策（执行前定稿）

1. **IQIMP 参数的锁存时机**。规格 §4 的命令表列了 `SET IQIMP ON|OFF` 与四个数值参数，
   没有 COMMIT；但验收标准 4 要求"数值参数在 COMMIT 后生效"。**本计划取**：
   新增 `SET IQIMP COMMIT`，与 `SET CHx CHAN COMMIT` 对称；`ON|OFF` 只是使能位、
   走 GPIO 立即生效；四个数值参数写 BRAM、等 `IQIMP COMMIT` 才锁存。
   （替代方案：`SET IQIMP <param>` 自行脉冲 commit 立即生效 —— 但那就与验收标准 4 冲突。
   若用户要后者，改 Task 7 的固件与 Task 8 的断言即可，RTL 侧不受影响。）
2. **两通道 commit 都会刷新 iq_impair 参数**。`chan_cfg` 是按通道排空的
   （CH1→字 0-3,12,13；CH2→字 4-7,14,15），而 `iq_impair` 是**通道对级、单实例**，
   字 8/9/10 不属于任何单通道。本计划取：**任一 `CHAN/IQIMP COMMIT` 都排空字 8/9/10**
   （幂等，无害）。这样不引入"只有 CH1 commit 才能改 I/Q 参数"这种隐式耦合。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `tools/gen_sincos.py` | **新建**。生成 `sincos_rom.v`（1024 点 × 16 位 sin/cos 双输出），含自检 |
| `src/rtl/sincos_rom.v` | **新建**（生成物）。`ram_style="block"`，同步读 |
| `src/tb/sincos_rom_tb.v` | **新建**。过零 / 峰值 / `sin²+cos²` 恒定 |
| `src/rtl/iq_impair.v` | **新建**。静态 2×2 → 旋转 2×2，旁路逐位透传 |
| `src/tb/iq_impair_tb.v` | **新建**。对齐 host golden + 旁路逐位 + 饱和 |
| `host/siggen/channel.py` | **修改**。`sincos_table()`、`iq_impair_matrix()`、`apply_iq_impair()` |
| `host/tests/test_channel.py` | **修改**。模型单测（先写失败测试） |
| `src/rtl/chan_cfg.v` | **修改**。排空步数 6→9，新增字 8/9/10 输出 |
| `src/tb/chan_cfg_tb.v` | **修改**。新增字 8/9/10 锁存用例 |
| `src/rtl/reg_bridge.v` | **修改**。CH1 控制字 bit11 → `iq_impair_en` |
| `src/tb/reg_bridge_tb.v` | **修改**。bit11 解码用例 |
| `src/rtl/awg_top.v` | **修改**。插入 `iq_impair` 并改最终选择表达式 |
| `src/tb/awg_top_tb.v` / `awg_top_stream_tb.v` | **修改**。端到端穿透 + 端口补齐 |
| `sdk/src/main.c` | **修改**。`SET IQIMP …` / `GET IQIMP`、影子数组、BRAM 写入 |
| `host/siggen/protocol.py` | **修改**。`iqimp_*_cmd()` 构造器 + `parse_iqimp_status()` |
| `host/tests/test_protocol.py` | **修改**。构造器与解析单测 |
| `scripts/test_bridge.py` | **修改**。新增 IQIMP 命令断言 |
| `scripts/scope_verify.py` | **修改**。新增三项 I/Q 星座用例 |
| `README.md` / `CLAUDE.md` | **修改**。协议说明、计数、经验 |

---

### Task 1: `tools/gen_sincos.py` + `src/rtl/sincos_rom.v` + TB

> **为什么生成器排在最前**：`host/siggen/channel.py::sincos_table()` 要与 RTL 的 ROM
> **逐项相等**，而"逐项相等"的测试必须解析真实文件（A 组的教训：重算定义式的测试是自证的，
> 分叉了也照样通过）。文件由本任务产出，故本任务必须先做。

**Files:** Create `tools/gen_sincos.py`, `src/rtl/sincos_rom.v`, `src/tb/sincos_rom_tb.v`

**Interfaces:** `sincos_rom (input wire clk, input wire [9:0] addr,
output reg signed [15:0] cos_out, output reg signed [15:0] sin_out)` — **同步读**，
因为 1024×16×2 = 32 Kbit 必须落块 RAM（`ram_style = "block"`）；用 LUTRAM 会吃约 16K LUT。

- [ ] **Step 1: 写生成器**，照抄 `tools/gen_sine.py` 的结构（`N`/`AMPL`/`OUT` 常量、
  每行 4 项、末尾自检 + `OK:` 打印）。表值 = `round(32767·cos(2π i/1024))` 与
  `round(32767·sin(2π i/1024))`，取 16 位有符号。自检项：`cos[0]==32767`、`sin[0]==0`、
  `cos[256]==0`、`sin[256]==32767`、`cos[512]==-32767`、`sin[768]==-32767`、
  以及 `sin²+cos²` 与 `32767²` 的相对误差 < 1e-3。
- [ ] **Step 2: 生成**（工程根执行 `python tools/gen_sincos.py`，确认打印 `OK:`）
- [ ] **Step 3: 写 TB**：复位后用递增 addr 读一遍，与文件里的表逐项比对；
  断言读是**同步**的（addr 变化后下一拍才出现在 `cos_out`）。复位用 negedge 释放
  （照抄 `tone_gen_tb` 的 `do_reset`，避免 posedge 采样竞争）。
- [ ] **Step 4: 跑 TB**：
  `"$SIM/xvlog.bat" --sv src/rtl/sincos_rom.v src/tb/sincos_rom_tb.v` → xelab → xsim
  Expected: 含 `PASS sincos_rom`
- [ ] **Step 5: 生成器的哈希归属 —— 实测结论是"不登记"**（与规格 §6 的说法相反，见下）。
  规格 §6 写"`tools/gen_sincos.py` 需加入 `release_manifest.py` 的归一化输入哈希集合
  （与 `tools/gen_sine.py` 同等对待）"，但**实测 `tools/gen_sine.py` 根本不在那个集合里**：
  `scripts/release_manifest.py::normalized_input_hash()` 只收 `src/rtl/*.v`、`src/rtl/*.vh`、
  `src/constraints/*.xdc` 和 6 个 tcl 文件。生成物 `sincos_rom.v` 落在 `src/rtl/*.v` 的通配
  里，**已经被覆盖** —— 而它才是真正进综合的那个文件。把**生成器**加进去反而引入假陈旧信号：
  生成器改了但 `.v` 没重新生成时，综合输入没变、bitstream 并不陈旧，哈希却会翻。
  故本步**不做任何改动**，只在 Task 10 把这条勘误写进文档与 CLAUDE.md。
- [ ] **Step 6: 提交**（`feat(rtl): block-RAM sincos ROM and its generator`）

---

### Task 2: 主机信道模型（I/Q 静态矩阵 + 旋转）

**Files:** Modify `host/siggen/channel.py`; Test `host/tests/test_channel.py`

**Interfaces:**
- Consumes: 既有 `round_shift_signed(value, shift)`（符号对称四舍五入）、`_clamp`、`DAC_MIN/DAC_MAX`
- Produces:
  - `SINCOS_BITS = 10`、`SINCOS_N = 1024`、`SINCOS_AMP = 32767`
  - `sincos_table()` → `(cos_list, sin_list)`，各 1024 项 **signed 16-bit Q1.15**，
    与 `src/rtl/sincos_rom.v` 同值
  - `IQ_COEF_MIN, IQ_COEF_MAX = -32768, 32767`、`IQ_GAIN_UNITY = 16384`
  - `iq_impair_matrix(phase_off_deg100, gain_q14, phase_err_deg100)` → `(a, b, c, d)`
    各 Q2.14 原始码（主机侧合成 `R(phase_off) × diag(α,β) × IQ相位矩阵`）
  - `apply_iq_impair(i_samples, q_samples, a, b, c, d, fword=0)` → `(i_out, q_out)`

- [ ] **Step 1: 写失败测试**（追加到 `host/tests/test_channel.py`）
  - `test_sincos_table_matches_rtl_file`：解析 `src/rtl/sincos_rom.v`，**逐项**比对
    （**不要重算定义式 —— 那是自证**，A 组踩过）。Task 1 已产出该文件，此处才可写。
  - `test_iq_impair_matrix_identity`：`phase_off=0, gain=16384, phase_err=0` →
    单位阵（`a==16384, d==16384, b==0, c==0`）。
  - `test_iq_impair_matrix_pure_phase`：`phase_off=9000`(90°) → 旋转 90° 的矩阵，
    且 `a==d`、`b==-c`（正交结构）。
  - `test_iq_impair_matrix_gain_imbalance`：`gain!=16384` 时行列式偏离单位阵。
  - `test_apply_iq_impair_identity_is_bit_exact`：单位阵 + `fword=0` → 输出与输入逐位相等
    （**这是旁路之外的"数值不变"保证，单独钉一条**）。
  - `test_apply_iq_impair_rotation_matches_hand_calc`：`fword = 2^32/4`（每拍 +90°）→
    与手算 `(−q, i)` 逐项对比（含 `round_shift_signed(·,15)`）。
  - `test_apply_iq_impair_saturates`：大矩阵系数 → 输出不超过 ±127。
  - `test_apply_iq_impair_frequency_offset_accumulates`：`fword` 非零时相位每拍累加，
    地址取 `(acc>>22)&0x3FF`（1024 点 → 分辨率 2π/1024）。
- [ ] **Step 2: 跑测试确认失败**：
  `host/.venv/Scripts/python -m pytest host/tests/test_channel.py -v`（ImportError）
- [ ] **Step 3: 实现模型**（RTL 的整数镜像；逐级注册，四舍五入与 `round_shift_signed` 同法）：

  ```
  stage1: i1 = sat127( round_shift_signed(a*i0 + b*q0, 14) )
          q1 = sat127( round_shift_signed(c*i0 + d*q0, 14) )
  stage2: 相量 acc += fword 每拍; addr = (acc >> 22) & 0x3FF
          io = sat127( round_shift_signed(i1*cos[addr] - q1*sin[addr], 15) )
          qo = sat127( round_shift_signed(i1*sin[addr] + q1*cos[addr], 15) )
  ```
  **索引约定**：第 n 拍的 cos/sin 来自第 n−1 拍结束时的 acc（RTL 里相位累加与 ROM 读
  各占一拍）。这与 A 组 `tone_samples` 的 `addr[n] = fword·(n−1)` 是同一类坑 ——
  **必须显式注释并单测**，否则模型与 RTL 会静默错开一拍。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**（`feat(host): integer model for the complex I/Q impairment stage`）

---

### Task 3: `src/rtl/iq_impair.v` + TB

**Files:** Create `src/rtl/iq_impair.v`, `src/tb/iq_impair_tb.v`

**Interfaces:**
```verilog
module iq_impair (
    input  wire clk, rst_n, enable,
    input  wire signed [7:0] i_in, q_in,
    input  wire [31:0] coef_ab,   // A = [15:0], B = [31:16]  (Q2.14 有符号)
    input  wire [31:0] coef_cd,   // C = [15:0], D = [31:16]
    input  wire [31:0] fword,     // 频偏相位增量(32 位全宽)
    output reg  signed [7:0] i_out, q_out
);
```
- **流水 5 级**（TB 钉死）：① 寄存输入 + 相位累加 ② 寄存 ROM 输出 + 寄存 4 个乘积
  ③ 静态矩阵求和 + 舍入饱和 → `i1/q1` ④ 旋转 4 个乘积 + 求和 ⑤ 舍入饱和 → 输出。
- **旁路**：把 `i_in/q_in` 同步流水同样 5 级，输出端 `enable` 选择；
  `enable=0` 时逐位透传（1 拍都不可少，否则支路对齐会变）。
- `coef_*` 与 `fword` 是**静态 BRAM 参数**，不参与流水（与 `impulse_gen` 的 `level` 同理）。

- [ ] **Step 1: 写失败 TB**（golden 由 Task 1 的模型算出，**先算好再写进 TB**）
  用例：① 单位阵 + `fword=0` → 逐位透传（与旁路结果逐项相等）；② 纯相位旋转 90°；
  ③ 增益失配矩阵；④ 频偏 `fword` 非零，逐拍比对模型；⑤ `enable=0` 逐位透传；
  ⑥ 饱和（大系数）。
  ⚠ **golden 一律用 `host/.venv/Scripts/python` 从 `apply_iq_impair()` 打印后再粘进 TB**，
  不手算。索引偏移按 Task 1 的约定（第 n 拍的 ROM 数据对应第 n−1 拍的 acc）。
- [ ] **Step 2: 跑 TB 确认失败**（`module 'iq_impair' not found`）
- [ ] **Step 3: 实现 `iq_impair.v`**：乘法用 DSP48、**乘积先注册**、加法走显式平衡树
  （每级只有 2 项相加，平衡树的要求体现为"两级之间必须隔寄存器"，不要写成一口气乘加）；
  I/Q 两支路逐级对称。
- [ ] **Step 4: 跑 TB 确认通过**
- [ ] **Step 5: 误码/统计自检**：用模型生成一段随机 I/Q，跑过 TB，断言与 host 模型**逐值相等**
  （不是"近似"）。
- [ ] **Step 6: 提交**（`feat(rtl): complex I/Q impairment stage`）

---

### Task 4: `chan_cfg.v` 排空字 8/9/10 + TB

**Files:** Modify `src/rtl/chan_cfg.v`; Test `src/tb/chan_cfg_tb.v`

**Interfaces:**
- Produces: `output reg signed [15:0] iq_a, iq_b, iq_c, iq_d;`,
  `output reg [31:0] iq_fword;`
- 字映射：**字 8 = `A[15:0] | B[31:16]`；字 9 = `C[15:0] | D[31:16]`；字 10 = `fword[31:0]`**
  （与既有 `chan_imp` 的"低位字段在前"约定一致）。

- [ ] **Step 1: 写失败 TB 用例**：`mem[8]/mem[9]/mem[10]` 赋值 + 触发一次 commit +
  断言 `uut.iq_a/iq_b/iq_c/iq_d` 与 `uut.iq_fword`；含**负系数保号**（如 `A=-16384` ⇒ `0xC000`）。
  同时**回归断言既有 6 步仍对**（字 0-3 与 12/13/14/15 不变）。
- [ ] **Step 2: 跑 TB 确认失败**
- [ ] **Step 3: 扩实现**：`STEPS 6→9`、`reg [2:0] step` → `reg [3:0] step`、
  `word_sel` 加 `steps 6,7,8 → 5'd8,5'd9,5'd10`（**通道无关**）、S_CAP 加三分支、
  复位块清 5 个新输出。⚠ 既有 `S_IDLE` 的起始地址 `pend1 ? 5'd0 : 5'd4` **保持不动**
  （它读的是非阻塞赋值前的旧 `cur_ch`，改了会让 CH2 去排空 CH1 的字 —— A 组踩过）。
  ⚠ `tap_b_addr` 是 `[4:0]`，字 10 放得下；排空拍数 6→9 步 = 1 + 9×2 = 19 拍，
  既有 TB 的 `wait_cycles(20)` 会**不够**，必须一并加长。
- [ ] **Step 4: 跑 TB 确认通过**（含既有项）
- [ ] **Step 5: 提交**（`feat(rtl): drain the I/Q impairment words in chan_cfg`）

---

### Task 5: `reg_bridge.v` 使能位 + `awg_top.v` 接线 + TB

**Files:** Modify `src/rtl/reg_bridge.v`, `src/rtl/awg_top.v`;
Test `src/tb/reg_bridge_tb.v`, `src/tb/awg_top_tb.v`, `src/tb/awg_top_stream_tb.v`

**Interfaces:**
- `reg_bridge` 新输出 `iq_impair_en`，取 **CH1 控制字 bit11**（bits 11-15 实测空闲；
  固件 `write_chan_ctrl()` 把控制字截到低 16 位，故 11-15 可达）。
- `awg_top` 插入点：**改最终选择表达式**（当前 `awg_top.v:537-538`）：
  ```verilog
  wire signed [7:0] dm_i = rrc_active ? rrc_i8 : dig_i;   // 数字调制支路 I
  wire signed [7:0] dm_q = rrc_active ? rrc_q8 : dig_q;   // 数字调制支路 Q
  wire signed [7:0] iq_i, iq_q;
  iq_impair u_iq_impair (.clk(sys_clk), .rst_n(rst_n), .enable(iq_impair_en),
      .i_in(dm_i), .q_in(dm_q), .coef_ab(ch1_iq_ab), .coef_cd(ch1_iq_cd),
      .fword(iq_fword), .i_out(iq_i), .q_out(iq_q));
  wire [7:0] dac_sel0 = (rrc_active || rect_active) ? iq_i : a1;
  wire [7:0] dac_sel1 = (rrc_active || rect_active) ? iq_q : a2;
  ```
  ⚠ **只作用于数字调制支路**：`a1/a2`（标准 AWG）路径完全不变 —— 这是"关闭时逐位一致"
  验收标准 1 的关键。

- [ ] **Step 1: 写失败 TB 用例**：`reg_bridge_tb` 加 bit11 解码（置位/清零/不影响既有位）；
  `awg_top_tb` 加端到端穿透（层次引用 `uut.u_iq_impair.*` + `uut.iq_impair_en`）。
  `awg_top_stream_tb` 补端口连接（**新增端口不连会留未驱动线**，本项目把 xelab 告警当缺陷）。
- [ ] **Step 2: 跑 TB 确认失败**（xelab 报层次引用找不到）
- [ ] **Step 3~4: 改实现并跑通**
- [ ] **Step 5: 跑全部受影响 TB**：`reg_bridge_tb / awg_top_tb / awg_top_stream_tb /
  chan_impair_tb / chan_cfg_tb`，每个都要 `PASS`
- [ ] **Step 6: 提交**（`feat(rtl): wire the I/Q impairment stage into awg_top`）

---

### Task 6: 固件命令与 `GET IQIMP`

**Files:** Modify `sdk/src/main.c`

**Interfaces:**
- 命令（**新顶层家族**，不是每通道）：
  ```
  SET IQIMP ON | OFF
  SET IQIMP PHASE <deg_x100>        # 0.01° 单位
  SET IQIMP FREQOFF <hz>            # 协议层 ±1_000_000(硬件 ±25MHz)
  SET IQIMP GAIN <q14>              # 16384 = 1.0
  SET IQIMP PHASEERR <deg_x100>
  SET IQIMP COMMIT
  GET IQIMP -> IQIMP ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0
  ```
  越界返回 `ERR IQIMP OUT OF RANGE`。
- 影子数组（供 `GET` 回读，**与 BRAM 是否已 commit 无关**，沿用 `chan_tap[][]` 的做法）：
  `iq_phase_deg100, iq_freqoff_hz, iq_gain_q14, iq_phaseerr_deg100`；
  矩阵由 `iq_impair_matrix()` 的**固件侧等价整数式**合成后写入字 8/9，`fword` 写字符 10。
- ⚠ **增益 Q2.14 不能用 `parse_long` 的 1 MHz 上限**（A 组踩过：`parse_long` 上限写死
  1000000，把 (1MHz, 25MHz] 全判越界）。频率用 `parse_u32_limit(tok, &v, 1000000u)`。
- ⚠ `fword = round(Δf/fs × 2³²)` 必须用 `unsigned long long` 防 32 位溢出。

- [ ] **Step 1~3: 实现**（影子数组 + 写函数 + 命令分支 + `GET IQIMP` 扩展），
  等效于 A 组 Task 7 的结构。
- [ ] **Step 4: 构建固件**（工作区 `sdk/ws2025_gem_fresh`）：
  ```bash
  python vivado2025.py app build awg_bridge --workspace sdk/ws2025_gem_fresh
  ```
  **必须自己断言产物新鲜**（`app build` 打印 `ok` 只查 ELF 存在、不看时间戳）：
  `main.c.obj` 的 mtime 必须晚于工作区 `src/main.c`。工作区源码副本若落后于
  `sdk/src/main.c`，先同步（工作区是生成物，同步安全）。
- [ ] **Step 5: 提交**（`feat(fw): complex I/Q impairment commands`）

---

### Task 7: 上位机协议构造器与解析器

**Files:** Modify `host/siggen/protocol.py`; Test `host/tests/test_protocol.py`

**Interfaces:**
- `IQIMP_PHASE_MIN/MAX = -18000, 18000`（0.01°，±180°）
- `IQIMP_FREQOFF_MIN/MAX = -1_000_000, 1_000_000`
- `IQIMP_GAIN_MIN/MAX = -32768, 32767`（Q2.14 原始码）
- `IQIMP_PHASEERR_MIN/MAX = -18000, 18000`
- `iqimp_set_cmd(param, value)` / 或逐个 `iqimp_phase_cmd(v)` 等
- `parse_iqimp_status(line)` → `{"enabled": bool, "phase": int, "freqoff": int,
  "gain": int, "phaseerr": int}`

- [ ] **Step 1~4: TDD**（失败测试 → 实现 → 通过）
- [ ] **Step 5: 跑全量上位机测试**：`host/.venv/Scripts/python -m pytest host/tests/`
- [ ] **Step 6: 提交**（`feat(host): IQIMP command builders and status parser`）

---

### Task 8: `scripts/test_bridge.py` 命令断言

**Files:** Modify `scripts/test_bridge.py`

- [ ] **Step 1: 加断言**（块内自建状态，**不要依赖上电默认值** —— A 组的教训：
  `cmd()` 是精确相等，而 IQIMP 影子跨运行持续存在）：
  每条数值命令的来回、每个越界边界、`ON|OFF` 回读、以及
  **`FREQOFF` 越过旧 `parse_long` 上限 1000000 的回归护栏**（例如 1000001 必须 `OK`）。
  ⚠ 区间边界处**不要**断言 `PHASE ±18000` 之外的行为不确定项。
- [ ] **Step 2: 上板跑**（需最新 bit + ELF）：
  ```bash
  bash scripts/program_board.sh
  python scripts/test_bridge.py COM11
  ```
  判据：末行 `RESULT: ALL PASS`、`FAIL == 0`；**并连跑两次**（不重启桥）证明可重复。
  报出实际 `grep -c '^PASS '` 的真实数字，不要照抄推算值。
- [ ] **Step 3: 提交**（`test(bridge): IQIMP command assertions`）

---

### Task 9: `scripts/scope_verify.py` 三项 I/Q 星座用例

**Files:** Modify `scripts/scope_verify.py`

- [ ] **Step 1: 加三个用例**，插在 DIGMOD 用例区（**先关 CHAN**，避免信道损伤级顶替通路）：
  | 用例 | 判据 |
  |---|---|
  | I/Q 相位偏移 | 星座旋转角 == `PHASE` 设定值（容差按 QAM16 密集程度定） |
  | I/Q 增益/相位失配 | `E[I·Q] ≠ 0`、协方差特征值比与模型比 |
  | I/Q 频率偏移 | 连续符号上的星座旋转速率 == `Δf`（容差 1 bin） |
  ⚠ **幅度归一化必须用 `constellation_scale` 而不是全局峰值**（本轮刚修的缺陷，
  见 CLAUDE.md 2026-09-11 那条）—— 复用 `_align_rrc_capture` 与 `constellation_scale` 即可，
  不要另写一份归一化。⚠ 每个用例 `finally` 里 **`SET IQIMP OFF` 并 `SET DIGMOD MODE OFF`**，
  否则会污染其后用例与下一次运行的校准段。
- [ ] **Step 2: 上板跑**，确认计数从 58 增加，且**既有 58 项不回归**
- [ ] **Step 3: 提交**（`test(scope): I/Q constellation cases`）

---

### Task 10: 文档与上板验收

**Files:** Modify `README.md`, `CLAUDE.md`

- [ ] **Step 1: 协议说明**：README 串口协议节加 `SET IQIMP …` / `GET IQIMP`，
  说明静态三项在主机侧合成一个矩阵、`sincos_rom` 1024 点相位量化 2π/1024≈0.35°、
  `ON|OFF` 走 GPIO 立即生效而数值走 BRAM 需 `IQIMP COMMIT`。
- [ ] **Step 2: 计数与经验**：CLAUDE.md 测试速查更新（**TB 项数一律实测，不要照抄**）；
  信道损伤经验节追加 B 组条目（重点记：判据范式从 `BER==0` 改为"实测≈模型"、
  5 拍流水与 I/Q 对称性的理由、BRAM 字预算已用尽、`parse_long` 上限坑）。
- [ ] **Step 3: 完整构建下载**：
  ```bash
  VIVADO_BIN="<LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat" python scripts/build_bitstream.py
  python scripts/check_bitstream.py
  bash scripts/program_board.sh
  python vivado2025.py app build awg_bridge --workspace sdk/ws2025_gem_fresh
  python vivado2025.py deploy jtag --app awg_bridge --workspace sdk/ws2025_gem_fresh \
      --bit awg/awg.runs/impl_1/host_bd_wrapper.bit
  ```
- [ ] **Step 4: 验收（对应规格验收标准 1/3/4/5/6）**
  - `python scripts/test_bridge.py COM11`（连跑两次）
  - `python scripts/scope_verify.py COM11`（既有 58 项 + 新增，全部 PASS）
  - `python scripts/scope_verify_user.py COM11`（4/4 不回归）
  - `host/.venv/Scripts/python -m pytest host/tests/`（全绿）
  - 时序：`awg/awg.runs/impl_1/host_bd_wrapper_timing_summary_routed.rpt` 的 **WNS 必须
    回报实测值**（当前基线 0.192 ns），且 **0 setup / 0 hold 失败**、无 DSP48 级联长路径。
- [ ] **Step 5: 提交**（`docs: I/Q impairment protocol, counts and measurement notes`）

---

## 自审记录

**规格覆盖**：§1（B 组模型）→ Task 1/3；§2.1（`iq_impair`）→ Task 3；
§2.2（`sincos_rom` + 生成器进 `release_manifest.py`）→ Task 2（**生成器入 manifest 的
归一化哈希集合要一并做，与 `tools/gen_sine.py` 同等对待**）；§3 字映射 8-10 与使能位
bit11 → Task 4/5/6；§4 协议 → Task 6/7/8；§5 上位机只做协议层（**不改 controller/GUI**）→ Task 7；
§7 验证 → Task 9/10；§6 构建 → Task 10。

**待确认项**：本文件开头"需要先确认的设计决策"两条（IQIMP 的 COMMIT 语义、
两通道 commit 都刷新字 8-10）。执行前需用户定稿；两条都不影响 RTL 结构，
只影响固件与断言。

**已知会踩的坑（全部来自 A 组的实测教训）**：
1. `parse_long` 的 1000000 上限 —— 频率类参数必须用 `parse_u32_limit`。
2. `app build` 打印 `ok` 不代表编译了 —— 必须断言 obj 比 src 新。
3. `deploy jtag` 必须显式给 `--bit`。
4. `scope_verify.py` 必须用系统 `python`（需要 pyvisa），`host/.venv` 那个没装。
5. 幅度归一化用 `constellation_scale`，**不要**全局峰值。
6. 既有 `S_IDLE` 的起始地址不要改成 `cur_word`（非阻塞赋值前的 `cur_ch`）。
7. `chan_cfg` 排空拍数变化后，既有 TB 的 `wait_cycles` 必须一并加长。
