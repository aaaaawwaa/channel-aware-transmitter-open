# 信号源信道损伤扩展（窄带/冲激干扰 + 复数 I/Q 损伤）设计

日期：2026-09-10
状态：结构已确认（I/Q 级位置 / 交付深度 / A 组形态 / 复数级结构 / 参数承载），待用户审阅 spec
前置：`2026-09-10-channel-impairment-design.md` 已实现并上板验收
（静态多径 `fade_fir` + AWGN `noise_gen`，示波器闭环 56/56，上位机 685 项）。
相关文档：`2026-09-10-channel-impairment-design.md`、
`2026-09-08-ethernet-ddr-upload-design.md`。

## 目标

在既有两个信道损伤（静态多径、AWGN）之上再加**两类**损伤，共五项：

**A 组 — 实数域，每通道独立**

```
tone[n]    = A_tone/100 × 127 × sin(2π f_tone n / fs)          窄带单音干扰
impulse[n] = ±A_imp/100 × 127   (在 period 内的前 width 个采样点，其余为 0)   周期冲激干扰
```

**B 组 — 复数域，I/Q 对级**

```
[I1]   [A B][I0]                静态 2×2：相位偏移、I/Q 增益失配、I/Q 相位失配
[Q1] = [C D][Q0]

[Io]   [cosθ −sinθ][I1]
[Qo] = [sinθ  cosθ][Q1]         θ += fword，每拍累加：频率偏移
```

设计目标是把 A 组与 B 组都做成**运行时可按数值配置**，并由示波器闭环给出可量测的
证据，而不只是"看起来像"。

## 非目标

- **不做时变/多普勒衰落**（无 Jakes、无 Rayleigh/Rician 包络生成、无多普勒谱）。
  这是后续独立立项的内容，本设计只为其留出结构余量。
- **不做分数延迟**（沿用既有整数样本延迟）。
- **不做带限噪声干扰**：A 组窄带干扰只做单音 CW，不做带限噪声（带限噪声的难度来源
  与 Rayleigh 引擎相同 —— 50 MHz 采样率下的极窄带滤波器）。
- **不做脉冲随机到达**：冲激为固定周期，不做泊松到达。
- **不做 GUI**：本轮交付到 RTL + 固件 + 协议 + 示波器用例；面板控件单独一轮。
- **不改 A 组/标准 AWG 路径既有行为**：本轮所有新增项在关闭时逐位透传。
- 不做信道估计/均衡/补偿。

## 现状

- 两路最终样本在 `src/rtl/awg_top.v:533` 汇合：
  `dac_sel0 = rrc_active ? rrc_i8 : rect_active ? dig_i : a1;`（CH2 同理），
  8-bit 补码，50 MHz。
- 其后是两个独立的 `chan_impair`（`awg_top.v:547` / `:552`，SEED 0x00C0FFEE /
  0x00DEADBEEF），结构为 `fade_fir → +noise_gen → ±127 饱和`，端到端 3 拍，
  `chan_en=0` 时逐位透传（`awg_top.v:531-532` 已写明"自由运行流，恒定相移无害"）。
- **`chan_impair` 的端口是单通道实数**（`sample_in[7:0]`），两个实例互不可见。
- 控制面：`axi_gpio_12` 双 32-bit 全输出（`create_bd.tcl:81-82`，`C_GPIO_WIDTH {32}` +
  `C_GPIO2_WIDTH {32}` + `C_ALL_OUTPUTS`）给 `ch1_chan_word`/`ch2_chan_word`，
  由 `reg_bridge.v:244` 起解码（bit0=en、bit[7:1]=noise、bit8=commit epoch）。
  每通道仅用 9 位。**注意可达性**：RTL/BD 层面 bits 9-31 空闲，但固件
  `write_chan_ctrl()`（`main.c:646-648`）用 `& 0xffffu` 把控制字**截断到低 16 位**，
  因此**实际可用的只有 bits 9-15（每通道 7 位）**。本设计所需使能位落在此范围内，
  故不改该掩码。
- 抽头表在 `axi_bram_ctrl_1` + `blk_mem_gen_1`（脚本声明 32-bit × 16 字），
  由 `chan_cfg.v` 经 B 口同步读排空。字 0-3 = CH1、4-7 = CH2，
  `chan_cfg.v:4` 明确标注"**字 8..15 预留**"；每字只用 `gain[8:0] | delay[17:9]`。
- 时序余量极小：最近一次实现 **WNS 0.071 ns**（全部约束满足）。资源：LUT 47.5%、
  FF 18.2%、BRAM Tile 9.5/140、**DSP48 96/220**。
- 既有 RTL 经验：`fade_fir` 与 `rrc_filter` 都因 DSP48 `PCOUT→PCIN` 串行级联导致
  时序崩塌，最终改为显式平衡加法树。**新算术必须照此模式。**

## 第 1 节：定点格式与数值模型

信号域：8-bit 补码，`x ∈ [−128, 127]`。

### A 组（实数，每通道）

```
y[n] = sat( fir_out[n] + noise[n] + tone[n] + impulse[n], ±127 )
```

干扰与噪声在**同一加法节点**相加 —— 即都在 `fade_fir` **之后**。理由：干扰是接收端
叠加的外部信号，不应再过信道 FIR；且与既有噪声的位置一致，实现最省。

- `tone_gen`：24 位相位累加器，`acc += fword`，地址取 `acc[23:16]` → 复用既有
  `sine_rom`（256 点 8-bit）。频率 `f = fword/2²⁴ × fs`，**分辨率 2.98 Hz**，
  上限约 50 MHz（实际受 DAC 奈奎斯特限制为 25 MHz）。相位量化 2π/256 ≈ 1.41°，
  与既有 `dds_core` 取 `phase_acc[31:24]` 的质量一致，不额外劣化。
- `tone` 幅度 `A_tone ∈ 0..100`（与噪声等级同一"百分比满幅"约定）。
- `impulse_gen`：周期计数器 `0..period−1`，每个周期内前 `width` 个采样点输出幅度，
  其余为 0。`period ∈ 1..65535` 采样点，`width ∈ 1..255` 采样点，
  `A_imp ∈ −100..100`（有符号，可正向或负向）。

### B 组（复数，I/Q 对级）

```
[I1]   [A B][I0]        A,B,C,D ∈ Q2.14 有符号 16 位
[Q1] = [C D][Q0]        范围 ±1.99994，分辨率 6.1e-5
```

**为什么是 Q2.14 而不是 Q1.15**：I/Q 增益失配要求 `α = 1+ε > 1`，而 Q1.15 上限是
0.99997，表示不了大于 1 的增益。Q2.14 的 6.1e-5 分辨率对应镜像抑制本底约 −84 dB，
而信号本身只有 8 位（量化地板约 −50 dB），**系数精度不是瓶颈**，不需要用 18 位系数
去换（那会破坏"两个系数占一个 32 位字"的打包）。

```
[Io]   [cosθ −sinθ][I1]        θ += fword32，每拍累加
[Qo] = [sinθ  cosθ][Q1]        θ[31:22] → sincos_rom（1024 点 × 16 位）
```

- 频偏 `fword32 = round(Δf/fs × 2³²)`，32 位全宽（Δf 最大 ±25 MHz 需 `2³¹`）。
- `sincos_rom` 相位量化 2π/1024 ≈ 0.35°，量化杂散约 −49 dB。

**静态三项由主机合成为一个矩阵**：主机分别构造 `R(phase_off)`、`diag(α, β)`、
IQ 相位矩阵，相乘后写 A/B/C/D 两个字。**硬件不需要知道用户设的是哪一项** —— 这是
"一个矩阵乘覆盖三项"这个结构的直接收益，也是本轮最重要的简化。

## 第 2 节：数据通路与模块

```
                         ┌─────────────────────────────────────────────┐
标准 AWG: dds→harm→mod→ampl_scale ─────────────────────────► a1/a2    │
                         │                                             │
数字调制: prbs7/user → iq_digital_mod / rrc_digital_mod ──► dm_i/dm_q │
                         │  dm = rrc_active ? rrc : dig（二选一 mux）   │
                  ┌──────▼──────────────────┐                          │
                  │ 新: iq_impair           │  ← 唯一的复数级            │
                  │  ① 静态 2×2 (A B C D)   │                          │
                  │  ② 旋转 2×2 (θ 累加+LUT) │                          │
                  └──────┬──────────────────┘                          │
                         │ imp_i/imp_q                                 │
           最终选择 ◄────┴─────────────────────────────────────────────┘
                         │  dm_active ? imp : a1/a2
                ┌────────▼────────┐
                │ chan_impair ×2  │
                │  fade_fir  ─┐   │
                │  noise_gen ─┤   │
                │  tone_gen  ─┼─► Σ ─► ±127 饱和
                │  impulse_gen┘   │
                └────────┬────────┘
                    dac_driver
```

### 2.1 `iq_impair.v`（新增，单实例）

端口：`clk, rst_n, enable, i_in[7:0], q_in[7:0], coef_ab[31:0], coef_cd[31:0],
fword[31:0], i_out[7:0], q_out[7:0]`。

- **两级级联**：静态 2×2 → 旋转 2×2。每级 = 4 乘 + 2 加。
- 乘法用 DSP48，**加法走显式平衡树并注册中间量**，禁止 `PCOUT→PCIN` 级联。
  照抄 `fade_fir` / `rrc_filter` 已验证的结构。
- `enable=0` 时**逐位透传**（必须与 `chan_en=0` 同样的保证，TB 钉死）。
- 延时约 4~6 拍。**只作用于数字调制支路**，因此两条支路延时不同；由于数字调制与
  标准 AWG 互斥且都是自由运行流，恒定相移无害（沿用 `chan_impair` 的既有论证）。
- I 与 Q 对称处理，**两者延时相同**（否则会引入人为 I/Q 相位失配）。

### 2.2 `sincos_rom.v`（新增，生成物）

1024 点 × 16 位 sin/cos 双输出，`ram_style = "block"`（32 Kbit ≈ 1 RAMB36；
用 LUTRAM 会吃掉约 16K LUT，不可接受）。由 `tools/gen_sincos.py` 生成，
自检：过零、峰值、`sin²+cos²` 恒定。仿照 `tools/gen_sine.py` → `sine_rom.v` 的模式。

### 2.3 `tone_gen.v`（新增，每通道一份）

相位累加器（24 位）+ 复用 `sine_rom` + 幅度乘。输出与 `fade_out` / `noise_raw`
在加法节点同拍有效（合计 3 拍，见 §2.6）。

### 2.4 `impulse_gen.v`（新增，每通道一份）

周期计数器 + 宽度计数器 + 幅度乘。同样对齐到 3 拍。

### 2.5 `chan_impair.v`（修改）

新增端口 `tone_fword[23:0], tone_ampl[7:0], tone_en, impulse_period[15:0],
impulse_width[7:0], impulse_ampl[7:0], impulse_en`；加法节点扩为
`sum = fade_out + noise_raw + tone + impulse`。**端到端延时保持 3 拍不变**
（既有 `chan_impair_tb` 的 `ov[n+3]` 断言因此不受影响）。

### 2.6 `chan_cfg.v`（修改）

`step` 从 `0..3` 扩到遍历所需字组，新增输出：`coef_ab`、`coef_cd`、`fword`、
CH1/CH2 的 tone/impulse 参数。字 8-11（I/Q）**在任意一次 COMMIT 时一并锁存** ——
它们是静态系数，不存在需要原子生效的状态机时序，torn-update 风险与现有 4 个抽头字
同级（主机写齐再 commit 即可）。这样不新增专门的 IQ commit 命令，也不占 epoch 位。

### 2.7 `awg_top.v`（修改）

最终选择改为：

```verilog
wire [7:0] dm_i = rrc_active ? rrc_i8 : dig_i;
wire [7:0] dm_q = rrc_active ? rrc_q8 : dig_q;
wire       dm_active = rrc_active || rect_active;
wire [7:0] imp_i, imp_q;
iq_impair u_iq_impair (...);
wire [7:0] dac_sel0 = dm_active ? imp_i : a1;
wire [7:0] dac_sel1 = dm_active ? imp_q : a2;
```

与原有 `rrc_active ? rrc_i8 : rect_active ? dig_i : a1` 等价，但把数字调制的 I/Q 对
单独引出。**标准 AWG 路径的逐位透传保证不变**，既有 56 项示波器用例不受影响。

### 2.8 `reg_bridge.v`（修改）

在 `r17`/`r18` 的现有解码处扩位（§3）。

## 第 3 节：字映射与控制面

沿用抽头表 BRAM 的**声明深度 16 字**。不依赖"实际综合成 2048"这个 BD 传播副产物 ——
那虽已记录在案，但属意外行为，不宜作为设计依据。现有 0-7 已用，8-15 恰好八个字：

| 字 | 内容 |
|---|---|
| 0-3 | CH1 抽头（现有，`gain[8:0] \| delay[17:9]`） |
| 4-7 | CH2 抽头（现有） |
| 8 | `iq_impair` 静态矩阵 `A[15:0] \| B[15:0]`（Q2.14） |
| 9 | `iq_impair` 静态矩阵 `C[15:0] \| D[15:0]`（Q2.14） |
| 10 | `iq_impair` 频偏相位增量 `fword[31:0]` |
| 11 | 预留（唯一剩余字） |
| 12 | CH1 tone `fword[23:0] \| ampl[30:24]` |
| 13 | CH1 impulse `period[15:0] \| width[23:16] \| ampl[31:24]` |
| 14 | CH2 tone（同 12 布局） |
| 15 | CH2 impulse（同 13 布局） |

> **勘误（字 12/14 的 tone `ampl` 是 7 位 `[30:24]`，不是 8 位 `[31:24]`）**：本文档
> 初版把 tone 幅度写成 `ampl[31:24]`，但固件/RTL 一致地使用 **bit30:bit24**：
> `src/rtl/chan_cfg.v:134/137` 取 `tap_b_rdata[30:24]`，`sdk/src/main.c:2546` 写
> `(a & 0x7Fu) << 24`，`GET` 也从 `[30:24]` 读回（`main.c:1954`）。
> 7 位足够：tone 幅度是无符号 `0..100 ≤ 127`，bit31 恒为 0。上位机不自己打包 BRAM 字
> （只发 ASCII 命令、解析 `GET` 文本行），其 `chan_tone_cmd()`
> （`host/siggen/protocol.py:796`）把 ampl 限死在 `0..100`，故下发的命令永不可能用到
> bit31 —— 侧别一致以 `0..100` 的协议范围保证，而非另做掩码。此处以固件/RTL 为准修正，
> 未执行的第 B 轮计划读同一字映射，须按 7 位解析。字 13/15 的 impulse `ampl` 仍是有符号
> 8 位 `[31:24]`（范围 −100..100，需要 bit31 表负号），不受此勘误影响。

`ampl` 为 tone 时无符号 0..100；为 impulse 时有符号 −100..100（tone 的极性可由相位
表达，故无符号足够；impulse 需要双向）。

**使能走 GPIO，数值走 BRAM**：

| 位 | 含义 |
|---|---|
| CH1 字 bit9 | `tone_en` (CH1) |
| CH1 字 bit10 | `impulse_en` (CH1) |
| CH1 字 bit11 | `iq_impair_en` |
| CH2 字 bit9 | `tone_en` (CH2) |
| CH2 字 bit10 | `impulse_en` (CH2) |

以上全部落在**固件可达的 bits 9-15** 内（见「现状」的控制面可达性说明）。
`iq_impair_en` 借用 CH1 控制字的空闲位，因为该级是通道对级、且只在数字调制模式下
作用于 DAC0/DAC1 这一对。

约定：**开关走 GPIO 立即生效；数值参数走 BRAM，COMMIT 后生效** —— 与既有
`chan_en`（GPIO）对比抽头（BRAM + COMMIT）的分工一致。

**本轮不需要动 BD**：字表落在声明深度内，使能用 GPIO 空闲位。因此不需要
`create_bd.tcl` 改动、不需要 `--force` 重建、不需要重新生成 BSP —— 只有
`build_bitstream.py` 的正常增量重建。

## 第 4 节：串口协议

A 组是每通道的，进现有家族；I/Q 四项不是每通道的，**新开顶层家族** `IQIMP`：

```
SET CH1 CHAN TONE <freq_hz> <ampl>
SET CH1 CHAN IMPULSE <period> <width> <ampl>
SET IQIMP ON | OFF
SET IQIMP PHASE <deg_x100>
SET IQIMP FREQOFF <hz>
SET IQIMP GAIN <q14>
SET IQIMP PHASEERR <deg_x100>
GET CH1 CHAN -> CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 30 TAP0 255 0 ...
GET IQIMP    -> IQIMP ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0
```

- `PHASE`/`PHASEERR` 用 0.01° 单位（与既有 `SET CH2 PHASE` 的约定一致）。
- `FREQOFF` 单位 Hz，**协议层限制 ±1 MHz**（硬件能力到 ±25 MHz，协议刻意收窄到
  实际会用到的范围，超出返回越界错误）。
- `GAIN` 为 Q2.14 原始码（16384 = 1.0），便于无损回读。
- 越界返回具名错误，沿用既有风格：`ERR TONE OUT OF RANGE`、
  `ERR IMPULSE OUT OF RANGE`、`ERR IQIMP OUT OF RANGE`。
- `GET` 回读固件影子数组（与既有 `GET CHx CHAN` 读 `chan_tap[][]` 而非 BRAM 的
  行为一致）。`GET CHx CHAN` 的 token 数 **从 17 增加到 24**（新增
  `TONE <freq> <ampl>` 3 个与 `IMPULSE <period> <width> <ampl>` 4 个）；
  **既有 17-token 状态行按缺省值兼容**（TONE/IMPULSE 视为关闭、使能位为 0），
  避免破坏已归档的解析测试。
- 这些命令**不受 MODE 限制**（与现有 `SET CHx CHAN/NOISE/TAP` 一致）。

## 第 5 节：上位机

本轮只做协议层：`host/siggen/protocol.py` 增加命令构造与状态解析，
`host/tests/` 增加纯逻辑单测。**不改 `controller.py` 与 GUI 面板**（下一轮）。

矩阵合成（`R(phase_off) × diag(α,β) × IQ 相位矩阵` → A/B/C/D）放在主机侧，
作为 `host/siggen/channel.py` 的纯函数实现并单测。

## 第 6 节：构建与部署

- 不改 BD/PS7 → 正常 `python scripts/build_bitstream.py`（无需 `--force`），
  但仍需 `check_bitstream.py` 校验后 `program_board.sh` 下载。
- 新增 RTL 计入 manifest 哈希（`src/rtl/*.v`）与重建配方。
- 固件改动后需重新构建并部署 `awg_bridge`（`sdk/src/main.c`），
  工作区沿用现有 `sdk/ws2025`。
- 新增 ROM 生成器 `tools/gen_sincos.py` 需加入 `release_manifest.py` 的
  归一化输入哈希集合（与 `tools/gen_sine.py` 同等对待）。

## 第 7 节：验证

**判据范式变更（本节最重要）**：既有 56 项的核心判据是 `BER == 0`，而这批新特性的
目的**恰恰是打破链路**。因此新用例一律改为"**实测统计量 ≈ 模型**"，并遵守既有教训 ——
**不拿数字域理论值做绝对比对**：凡经过 DAC 零阶保持 + 模拟重建滤波的量都有模拟域衰减，
要么做成相对判据，要么把实测/模型比值回报在 detail 里持续观察（沿用噪声 σ 的做法，
那次实测只有数字域的 0.65 倍）。

| 项 | 判据 |
|---|---|
| 窄带单音 | 关闭时输出为静默基线；开启后 FFT 谱线位置 == 设定频率（容差 ≈ 1 个 bin）；幅度随 `ampl` 线性 |
| 周期冲激 | 长捕获超阈尖峰计数 ≈ 窗长 / `period`；幅度随 `ampl` 线性 |
| I/Q 相位偏移 | 用既有 `align_known_sequence` 重建星座，旋转角 == 设定值 |
| I/Q 增益/相位失配 | 星座二阶统计：`E[I·Q] ≠ 0`、协方差矩阵特征值比，与模型比 |
| I/Q 频率偏移 | 连续符号上的星座旋转速率 == 设定 Δf |

**为什么不用"单音镜像"测 IRR**：数字调制通路送的是符号比特，造不出干净的复单音
（连续相同比特给出直流而非单音）。从星座协方差直接读失衡量更稳，且复用现成的 QAM 捕获。

**一个必须写明的物理限制**：1 个采样点的冲激在 50 MHz 下会被 DAC 零阶保持 +
模拟重建滤波**严重削平** —— 与让噪声 σ 掉到 0.65 倍的是同一机制。冲激用例必须使用
**几十个采样点的宽度**，并把实测/模型比值回报出来。这条若不写明，一定会被当成 RTL
缺陷反复排查。

**单测与 TB**：

- 主机模型扩展 `host/siggen/channel.py`：`iq_impair_matrix()`、`apply_iq_impair()`、
  `tone_samples()`、`impulse_samples()`；**TDD，先写失败测试**。
- RTL TB：`iq_impair_tb`（含旁路逐位、矩阵对齐 host golden）、`tone_gen_tb`、
  `impulse_gen_tb`；`chan_impair_tb` 扩项（新增干扰项后 3 拍延时不变）。
- 示波器用例加入 `scripts/scope_verify.py`，计数从 56 增加。

## 验收标准

1. `chan_en=0` 且 `iq_impair_en=0` 时 DAC 输出与改动前**逐位一致**（RTL TB + 上板）。
2. 新增 TB 全项通过；`host/tests` 全绿（含新增协议与信道模型单测）。
3. 板上：单音干扰的谱线位置与幅度、周期冲激的计数与幅度、I/Q 三项的星座旋转角与
   二阶统计量，均与主机模型在容差内一致；关闭时全部回到既有基线。
4. `GET CHx CHAN` / `GET IQIMP` 回读与下发一致；数值参数在 COMMIT 后生效。
5. 既有示波器 56 项与 `scope_verify_user.py` 4/4 **不回归**。
6. 实现后时序仍收敛（WNS > 0），且**无 DSP48 级联**引入的长路径。

## 风险

- **时序**：WNS 仅 0.071 ns。新增约 12 个 DSP48 与一个 RAMB36。**必须**用平衡加法树 +
  逐级注册，禁止 `PCOUT→PCIN` 级联（`fade_fir`/`rrc_filter` 的既有教训）。这是本轮
  最大的工程风险。
- **冲激的模拟域削平**：见 §7。宽冲激与比值回报是对策，不是缺陷。
- **相位量化**：tone 用 256 点 ROM（1.41°）、旋转用 1024 点（0.35°）。作为损伤模拟器
  可接受并文档化；若日后需要更干净的干扰源，再单独提升。
- **`iq_impair` 延时只加在数字调制支路**：两条支路延时不同。因互斥且自由运行，无害；
  但若日后引入需要两条支路严格对齐的功能（如快速模式切换的相位连续性），需重新评估。
- **BRAM 字预算基本用尽**：8-15 八个字中 7 个已分配，仅字 11 预留。后续再加参数只能
  扩声明深度，而那次就要动 BD 并重建（`create_bd.tcl` 的 `Write_Depth_A`），
  连带必须重新生成 BSP。设计新参数时应把这一点纳入成本。

## 交付顺序

本设计**分两轮**实施，两轮互不依赖：

- **第一轮（A 组）**：`tone_gen` + `impulse_gen` + `chan_impair` 扩展 + 字 12-15 +
  `reg_bridge` 使能位 + 协议 + 用例 + TB。
  **不碰 I/Q 结构、不动 `awg_top` 的最终选择** → 风险最低，能最快看到真实效果。
- **第二轮（B 组）**：`iq_impair` + `sincos_rom` + `awg_top` 最终选择改造 + 字 8-10 +
  协议 `IQIMP` 家族 + 用例 + TB。

两轮都**不需要动 BD**，各自只需 `build_bitstream.py` 的正常增量重建。
GUI 在两轮之后单独一轮。

**每轮都必须独立满足验收标准 1 与 5**（关闭时逐位透传、既有用例不回归）——
即第一轮结束后既有 56 项必须仍然全绿，不能把回归留到第二轮一起处理。

## 已确认决策

- **I/Q 级位置**：数字调制输出后的**独立复数级**（先 mux 出 `dm_i/dm_q`，过复数级，
  再进最终选择）。理由：语义正确（只有数字调制下 CH1/CH2 才是真 I/Q）、一个级同时覆盖
  RECT 与 RRC、不动两个调制器内部、标准 AWG 路径零影响。
- **交付深度**：先 RTL + 固件 + 协议 + 示波器用例，**GUI 随后单独一轮**。
- **A 组形态**：窄带 = **单音 CW**；脉冲 = **周期冲激**（固定周期，非泊松到达）。
- **复数级结构**：**两级级联 2×2 实矩阵**（静态级 + 旋转级），8 个 DSP48。
  不选"单级 + 实时合成矩阵"（省 4 个 DSP48 但需硬件三角函数），不选极坐标/CORDIC
  （迭代会破坏每拍一样点的流）。
- **参数承载**：数值参数走抽头表 BRAM 的**预留字 8-15**；使能走 `axi_gpio_12` 的空闲位。
  本轮不动 BD。
- **静态三项由主机合成矩阵**，硬件只做矩阵乘。

## 与既有设计的偏离（需显式记录）

`2026-09-10-channel-impairment-design.md` 的「非目标」原文包含：

> **不做复数 I/Q 信道**：数字调制下 I/Q 各自独立实数失真，不是真复多径。

本设计**推翻这一条** —— 新增的 `iq_impair` 是一个真正的复数 I/Q 级。当时的理由是
"标准 AWG 正确"，本设计通过**把复数级放在最终选择之前、只作用于数字调制支路**，
在不牺牲标准 AWG 逐位透传的前提下实现了真复数 I/Q 损伤。既有规格的其余决策
（实数 FIR 多径、幅度噪声语义、N=4 抽头）继续有效。
