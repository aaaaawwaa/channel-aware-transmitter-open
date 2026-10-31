# 信号源信道损伤（静态多径 + AWGN）设计

日期：2026-09-10
状态：结构已确认（保真度 A / 按 DAC 通道实数 FIR / 幅度噪声 / N=4），待用户审阅 spec
前置：上位机 621 项测试通过；`scripts/scope_verify_user.py` 已提供 USER/DDR
模拟 I/Q 参考链（`align_known_sequence`），本设计在其上扩展信道比对。
相关文档：`2026-09-08-ethernet-ddr-upload-design.md`、
`2026-09-09-gem0-udp-ddr-pp-ring-design.md`。

## 目标

让信号源在最终 DAC 输出上叠加**信道损伤**，用于模拟真实环境：

```
y[n] = Σ_{k=0..N-1} g_k · x[n − d_k]  +  σ · w[n]
       └──── 静态多径(实数 FIR) ────┘     └ AWGN ┘
```

- 每 DAC 通道一份、独立可配：`g_k` 抽头增益、`d_k` 整数样本延迟、`σ` 噪声幅度。
- N = 4 抽头（配置表预留到 8）。
- 默认 **bypass**：enable=0 时逐位透传，既有已验收行为不变。

## 非目标

- **不做时变/多普勒衰落**（无 Jakes、无 Rayleigh 包络生成、无移动台）。
- **不做复数 I/Q 信道**：数字调制下 I/Q 各自独立实数失真，不是真复多径。
- **不做分数延迟**：`d_k` 为整数样本（20 ns 分辨率）。
- **不做自动归一化**：`Σ|g_k| > 1` 由饱和钳位兜底（文档写明，不自动缩放）。
- 不做信道估计/均衡/补偿；不改既有 DDS/RRC/调制路径的算法。
- 不改 `GET CHx` 既有 wire 格式；信道状态用独立 `GET CHx CHAN` 回读。

## 现状

- 两路最终样本在 `src/rtl/awg_top.v:513` 汇合：
  `dac_in0 = rrc_active ? rrc_i8 : rect_active ? dig_i : a1;`（CH2 同理），
  8-bit 补码，50 MHz，随后进 `dac_driver`（`^0x80` 转偏移二进制）。
- 标准路径 `a1/a2` 已过 `ampl_scale`（截断模型）；数字调制路径为 RRC/RECT I/Q。
- 控制面：PS 经 AXI GPIO 字 A–H + PLAY 字写入 `reg_bridge`（2-FF 同步，准静态）。
- 用户数据用 AXI BRAM（`axi_bram_ctrl_0`/`blk_mem_gen_0`）双 bank，`user_bit_source`
  经 B 口流式读——本设计复用该「PS 写 / PL 读」模式存放抽头表。
- 标准路径 WNS 约 0.9 ns（RTL 经验：串行 DSP48 级联会崩时序，必须平衡加法树）。

## 第 1 节：定点格式与数值模型

信号域：8-bit 补码，`x ∈ [−128, 127]`（DAC 偏移二进制在 `dac_driver` 转换）。

| 量 | 位宽/格式 | 范围 | 说明 |
|---|---|---|---|
| 抽头增益 `g_k` | 9-bit 有符号 Q8 | −256…255 = −1.0…+0.9961 | 127→−0.07 dB 附近即接近单位增益 |
| 抽头延迟 `d_k` | 9-bit 无符号 | 0…511 样本 = 0…10.24 µs @50 MHz | 整数样本 |
| 噪声电平 `level` | 7-bit 无符号 | 0…100 (%FS) | `σ = level/100 × 127` LSB |
| 多径累加 `acc` | 20-bit 有符号 | ±131072 | `Σ g_k·x[n−d_k]`（4×256×128=131072，需 19 bit，取 20 留余量），右移 8 后饱和到 ±127 |

- 抽头增益用 Q8（而非 Q7）以便表达接近单位增益；但 9-bit 有符号域上限为 +255，
  **「单位增益」取 255**（`round(255·x/256) == x` 对全部 8-bit 样本成立，故仍逐位透传），
  +256 不可表示。代价是每抽头比 Q7 多 1 bit。
- 多径输出 `y_fade = sat(round_signed(acc, 8), ±127)`（符号对称四舍五入 + 钳位，
  参照 `awg_top.v` 的 `rrc_to_dac`）。
- 信道级流水：写延迟线 → 抽头乘加 → 平衡加法树（3 级）→ 右移饱和，全程寄存，
  禁止把 4 个 DSP48 串成 `PCOUT→PCIN` 链。

## 第 2 节：数据通路与模块

```
dac_in ─► chan_impair ─► dac_driver
          ├ fade_fir   N=4 抽头多径(4×DSP48 + 平衡树)
          ├ noise_gen  LFSR + 4 路均匀求和(近似高斯) × σ
          └ saturate   y = sat(y_fade + noise, ±127)
```

- 顺序：**fade → noise → saturate**（发端 → 多径信道 → 收端 AWGN）。
- `chan_impair.v`：顶层，每通道一份实例，含 enable 透传旁路。
- `fade_fir.v`：多径 FIR。
- `noise_gen.v`：确定性白噪声源（每通道独立播种）。
- 两通道完全独立（enable/抽头/噪声电平各自一套）。

### 2.1 延迟线实现

每抽头一个 **512×8 单口 RAM**（分布式 RAM），写地址广播 `x[n]`，读地址
`(n − d_k) mod 512`：

- 写：每拍把 `x[n]` 写入所有 N 个 RAM 的同一地址（广播）。
- 读：第 k 个 RAM 提供 `x[n − d_k]`，N 个读口天然并行（50 MHz 每拍 N 读）。
- `d_k = 0` 的抽头**直接取当前输入 `x[n]`，不经 RAM**（写读同址会读到旧值）；
  只有 `d_k ≥ 1` 的抽头从 RAM 读 `n − d_k`（该地址在更早的拍已写入）。
- 资源：N=4 → 4 RAM/通道 × 2 通道 = 8 个 512×8 RAM（可用 LUTRAM 或小型
  `blk_mem_gen`）；抽头表预留 N=8 时 16 个。
- 备选（不采用 v1）：等间距抽头线（间距 D 可配）——更省，但延迟只能落在 D 的
  整数倍，不满足「任意 d_k」。选 per-tap RAM 以支持真任意整数延迟。

## 第 3 节：噪声模型

- 每通道一个 32-bit **xorshift32**（种子为固定常数，两通道取不同常数；v1 不暴露
  种子配置），每拍推进一次。
- 取 4 个独立 8-bit 均匀数 `u_j ∈ [0,255]`（不同状态字的不同字节），求和
  `S = Σ u_j ∈ [0,1020]`；由中心极限定理近似高斯：均值 510，标准差
  `σ_raw = sqrt(4·(256²−1)/12) ≈ 147.9`。
- `n = (S − 510) × σ_target / σ_raw`，`σ_target = level/100 × 127`（LSB）。
  用乘常数倒数实现（与 `ampl_scale` 的 `/100` 同法）。
- 全带白噪声（噪声带宽 = 样点率 50 MHz 的一半）。`level=100` 时 `±3.4σ` 会超过
  ±127，噪声自身可饱和——这是预期行为，文档写明。
- `level=0` 或 disable → 噪声恒 0。

## 第 4 节：控制面（寄存器 / 存储）

新增 BD 部件（**结构改动 → 必须 `build_bitstream.py --force`**）：

- `axi_gpio_12`（dual 32-bit 全输出）→ 控制字，每通道独立一个 32-bit 字
  （`awg_top` 端口 `ch1_chan_word`/`ch2_chan_word`）：
  - bit0 = `chan_en`，bits[7:1] = `noise_level`(0…100，7 bit 足够)，bit8 =
    `tap_commit_epoch`，其余保留。
  - 两通道同布局；`reg_bridge` 各经 2-FF 同步后拆包为
    `chX_chan_en`/`chX_noise[6:0]`/`chX_tap_commit`（不引入打包的 `gpio17_in`）。
- `axi_bram_ctrl_1` + `blk_mem_gen_1`（True Dual Port，32-bit × 16 字）→ 抽头表：
  - 每 tap 一个 32-bit 字：`gain[8:0] | delay[17:9] | valid(bit31)`。
  - 索引：CH1 tap0..3 = 字 0..3，CH2 tap0..3 = 字 4..7；字 8..15 预留（N=8）。
  - PS 写 A 口；`awg_top` 读 B 口。
- `reg_bridge.v` 新增：`chan_en[2]`、`noise_level[2]`、`tap_commit_epoch[2]`（2-FF
  同步后使用）；`awg_top` 在 `tap_commit_epoch` 边沿按序读 4 个字并锁存到抽头寄存器，
  与 `user_bit_source` 的 commit_toggle 模式一致。
- 参数准静态；运行中可改（下一次 commit 生效）。

## 第 5 节：串口协议

大小写不敏感，沿用现有风格：

```
SET CH1 CHAN ON | OFF                  # 使能/旁路信道损伤(默认 OFF)
SET CH1 NOISE <0..100>                 # 噪声电平 %FS
SET CH1 TAP <0..3> <gain> <delay>      # gain=-256..255(Q8), delay=0..511
SET CH1 TAP CLEAR                      # 清空抽头(全零 -> FIR 静音), 非直通; 直通用 CHAN OFF
SET CH1 CHAN COMMIT                    # 原子锁存 enable+noise+taps, 单次下发
GET CH1 CHAN ->
  CHAN <ON|OFF> NOISE <n> TAP0 <g> <d> TAP1 <g> <d> TAP2 <g> <d> TAP3 <g> <d>
```

- 仅 CH1/CH2；非法值 `ERR BAD VALUE` / 越界 `ERR ... OUT OF RANGE`。
- tap 写入先落暂存，`COMMIT` 才生效（避免半配置状态进入 FIR）。
- `GET CH1`（既有）格式不变。

## 第 6 节：上位机 GUI

信道损伤对最终 DAC 输出生效，与「标准波形 / 数字调制」两个 tab 都正交，故新增
**第三个 tab「信道损伤」**（`QTabBar` 第三项），内含 CH1/CH2 两组控件：

- `启用` 复选框；`噪声` 0…100 滑条；4 行 `(增益, 延迟)` 输入；
- `应用` 按钮 → 逐条下发 `SET CHx TAP/NOISE/CHAN` 后发 `SET CHx CHAN COMMIT`；
- 状态回读走 `GET CHx CHAN`（连接后初始化，与 DDR PP 状态同样的接入点）。

复用 `host/siggen/protocol.py` 构造命令、`controller.py` 发命令与解析回读、
`ui/widgets.py` 面板模式。

## 第 7 节：构建与部署

- BD 结构变化：`python scripts/build_bitstream.py --force`（重跑 `create_bd.tcl`
  → 新 bit + HDF）；从新 HDF 新建 BSP/应用（`sdk/ws2025*`）。
- 新增 `GET/SET` 命令后，`sdk/src/main.c` 解析与回读需同步；`test_bridge.py`
  增加命令断言。
- 新增 RTL 计入 manifest 哈希（`src/rtl/*.v`）与重建配方。

## 第 8 节：验证

主机参考（无板卡/示波器可单测）：

- `host/siggen/channel.py`：`multipath_fir(x, gains, delays)`（整数算术镜像 RTL）
  与 `awgn(x, sigma_lsb, seed)`。
- 单测：单位抽头 = 透传；两点回声的 |H(f)| 与解析解一致；噪声 σ 统计量匹配。

板上（接示波器后）：

- RTL TB `chan_impair_tb`：enable=0 逐位透传；tap0=255/d=0 且其余 0 = 透传；
  两点回声波形等于参考；噪声=0 透传；饱和边界。
- `scripts/scope_verify.py` 扩展信道用例：
  - 多径：设已知 2 抽头（如 g=[1.0, 0.5], d=[0, 20]）于 100 kHz 正弦，
    抓波形做 FFT，比对 |H(f)|（主频与镜像比）与主机参考。
  - AWGN：CW 单音设不同 level，从频谱读 SNR/噪底与 `σ_target` 比对。
  - 与 `scope_verify_user.py` 的 USER/DDR 路径结合：多径下测 |H(f)|，噪声下测 SNR
    （**不求 BER=0**）。

## 验收标准

1. `chan_en=0` 时 DAC 输出与改动前逐位一致（RTL TB + 板上波形）。
2. `chan_impair_tb` 全项通过；`host/tests` 全绿（新增信道模型与协议单测）。
3. 板上：设置 2 抽头多径后，示波器测得的 |H(f)| 与主机参考在容差内一致；
   AWGN 的噪底随 `level` 单调、量级与 `σ` 匹配。
4. `GET CHx CHAN` 回读与下发一致；运行中 `COMMIT` 生效且无毛刺（enable 边沿可接受）。

## 风险

- **时序**：标准路径 WNS ~0.9 ns。FIR 必须全流水 + 平衡加法树；必要时对抽头
  乘加再加一级寄存器（吞吐不变，延迟 +1 拍，示波器比对按对齐修正）。
- **资源**：N=4 双通道 = 8 DSP48 + 8 个 512×8 RAM；预留 N=8 时 16 DSP + 16 RAM。
- **BD/固件面**：新 GPIO/BRAM 需重建 HDF 并新建 workspace；`main.c` 需同步解析。
- **噪声模型偏差**：4 路均匀求和非严格高斯（尾部偏短）；作为「环境噪声」演示可接受，
  文档注明非 AWGN 精确实现。

## 已确认决策

- 保真度：**A 静态多径 + AWGN**（无时变、无多普勒）。
- 通路：**按 DAC 通道实数 FIR**（标准 AWG 正确；数字调制为 I/Q 独立实数失真）。
- 噪声语义：**幅度 %FS**（`σ = level/100 × 127` LSB），非 SNR(dB)。
- 抽头数：**N=4**（配置表预留到 8）。
