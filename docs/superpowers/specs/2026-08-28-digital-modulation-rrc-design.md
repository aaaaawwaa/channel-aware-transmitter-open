# 数字调制 RRC 成形设计规格

日期: 2026-08-28  
状态: 已冻结设计，待分阶段实施  
前置条件: 现有 PRBS7、QPSK、可变符号率、RECT I/Q 数字调制链路已通过 RTL、Python、协议、上位机和板级回归。

## 1. 目标与范围

本阶段在现有全局数字调制链路中增加 QPSK 根升余弦（Root Raised Cosine，RRC）发射成形，同时保留 bit-exact 的 RECT 输出。RRC 只服务于内部固定 PRBS7 QPSK，不实现外部数据输入、成帧、FEC、RRC 接收硬件、EVM 或 BER 硬件测量。

交付内容：

- 固定 4 samples/symbol、滚降系数 0.35、总跨度 8 symbols、33 taps 的 RRC 系数。
- Python 定点黄金模型、系数文件和 RTL 逐采样参考向量。
- 独立 4 相 polyphase RRC 发射器，输出内部 signed 16-bit I/Q。
- 由 GPIO11 bit20 选择 RECT/RRC，GPIO11 低 20 位语义保持不变。
- `SET/GET DIGMOD FILTER` 协议、上位机回读确认和 GUI 选择。
- 10 kSym/s、100 kSym/s、1 MSym/s 的 RTL、综合、频谱和板级验证。

不在范围内：

- 改变现有 RECT 的 PRBS、星座、符号边界、幅度或 DAC 数据。
- 支持 QAM4/QAM16/QAM64 的 RRC 成形。
- 运行中的 FILTER 或 SYMRATE 修改。
- 外部采样数据、AXI-Stream、BRAM、载波上变频、解调、BER 计算。

## 2. 固定参数

| 参数 | 固定值 | 定义 |
|---|---:|---|
| 调制 | QPSK | 使用现有 QPSK 映射，I/Q 为 ±127 |
| 数据源 | PRBS7 | 种子 `7'h7f`，多项式 `x^7+x^6+1` |
| SPS | 4 | 每个输入符号产生 4 个 RRC 输出采样 |
| 滚降系数 α | 0.35 | 无运行时配置 |
| 总跨度 | 8 symbols | 从 `-4T` 到 `+4T` |
| 抽头数 | 33 | `span × SPS + 1 = 8 × 4 + 1` |
| 系数格式 | signed Q1.15 | 16-bit two's-complement，实数值为 `coeff_q / 32767` |
| 内部 I/Q | signed 16-bit | 现有 ±127 符号先左移 8 位，得到 ±32512 |
| PL 时钟 | 50 MHz | 与现有 AWG 相同 |
| 输出采样率 | `4 × SYMRATE` | 40 kS/s 至 4 MS/s |
| RRC 输出 | signed 16-bit | 每个采样节拍更新一次，其余 PL 时钟保持 |
| DAC 输入 | signed 8-bit | 明确舍入、右移和 `[-127,127]` 饱和 |
| 默认 FILTER | RECT | 复位、启动和旧固件兼容均保持 RECT |

`span` 明确表示滤波器总时间跨度，不表示单边跨度。因此 tap 下标为 `k=0..32`，对应相对时间 `t/T=(k-16)/4`，单个滤波器群延迟为 16 个输出采样，即 4 个符号。

发射 RRC 与离线匹配 RRC 串联后的总群延迟为 32 个输出采样，即 8 个符号。板级采集只把该延迟用于时间对齐，不把理论延迟误报为硬件实时延迟。

## 3. 连续时间公式与奇异点

令 `x=t/T`，标准 RRC 脉冲为：

```text
h(x) = [sin(πx(1-α)) + 4αx cos(πx(1+α))]
       / [πx(1-(4αx)^2)]
```

特殊点必须使用解析值，不能依赖浮点除零：

```text
h(0) = 1 + α(4/π - 1)

h(±1/(4α)) = α/√2 ×
  [(1+2/π) sin(π/(4α)) +
   (1-2/π) cos(π/(4α))]
```

采样点使用 `x=(k-16)/4`，`k=0..32`。Python 和 RTL 只使用同一份量化系数，不在 RTL 中重新计算三角函数。

## 4. 系数归一化和量化

采用离散能量归一化，而不是峰值归一化：

```text
h_energy = sqrt(sum(h[k]^2 for k=0..32))
h_norm[k] = h[k] / h_energy
```

量化规则为 ties-away-from-zero：

```text
q[k] = sign(h_norm[k]) × floor(abs(h_norm[k]) × 32767 + 0.5)
```

然后将结果限制在 signed 16-bit 范围 `[-32768,32767]`。系数文件必须满足：

- `q[k] == q[32-k]`，对称性不允许因生成器浮点微小差异破坏。
- `q[16] == 17953`，中心抽头固定。
- 量化后系数平方和固定为 `1_073_666_113`，最大绝对系数固定为 `17953`。
- 量化后的系数序列为：
  `33, 218, 157, -156, -417, -242, 420, 1071, 936, -362, -2215, -3091, -1388, 3390, 9959, 15683, 17953, 15683, 9959, 3390, -1388, -3091, -2215, -362, 936, 1071, 420, -242, -417, -156, 157, 218, 33`。
- RTL 只读取 `src/rtl/rrc_coeffs.vh` 中的 33 个常量。

离散能量归一化使匹配滤波的符号中心幅度具有确定的整数契约；本阶段不增加额外发射增益。RRC 与 RECT 的 DAC 峰值可以不同，比较频谱时使用相对 dB 和归一化幅度，不把两者强行视为同一峰值。

## 5. 定点运算契约

### 5.1 输入

现有 mapper 的 QPSK signed 8-bit 电平 `±127` 进入 RRC 路径前左移 8 位：

```text
x16 = sign_extend(x8) × 256
±127 → ±32512
```

RECT 路径继续直接使用原有 signed 8-bit 电平，不能复用 RRC 的缩放逻辑。

### 5.2 MAC

每个输出采样的整数累加为：

```text
acc = Σ(x16[j] × q[k])
```

乘积为 signed 32-bit。最多 9 个 polyphase 乘积相加，RTL 累加器使用 signed 40-bit，避免综合工具根据局部表达式宽度截断。Python 使用任意精度整数模拟同一累加过程。

### 5.3 右移和舍入

系数为 Q1.15，MAC 结果右移 15 位。右移前使用最近整数、ties-away-from-zero：

```text
round_shift(acc, 15) =
    ( acc + 2^14) >>> 15, acc >= 0
    -((-acc + 2^14) >>> 15), acc < 0
```

结果随后饱和到 signed 16-bit：

```text
[-32768, 32767]
```

不能使用 Verilog 算术右移直接代替负数舍入，也不能让累加器或输出发生 two's-complement 回绕。

### 5.4 DAC 缩放

RRC signed 16-bit 输出转换为现有 DAC signed 8-bit 输入时使用同一套 ties-away-from-zero 舍入：

```text
dac_round = round_shift(rrc_i16, 8)
dac_i8 = saturate(dac_round, -127, 127)
```

QPSK 的 `±32512` 经该规则得到 `±127`。DAC 转换后仍由既有 `dac_driver` 执行 `sample ^ 8'h80` offset-binary；不得修改全局 DAC 极性或寄存器时序。

## 6. 4 相采样调度

现有符号率 phase step 为：

```text
sym_phase_step = floor(SYMRATE × 2^32 / 50_000_000)
```

RRC 输出采样使用：

```text
rrc_sample_phase_step = sym_phase_step << 2
```

当前最大符号率 1 MSym/s 下该值仍小于 `2^32`。RRC 维护独立 32 位采样相位累加器：

```text
{carry, phase_acc} = phase_acc + rrc_sample_phase_step
```

每次 `carry=1` 产生一个 RRC 输出采样。连续四次 sample tick 使用 polyphase `phase=0,1,2,3`，随后回到 phase 0 并消费一个新的 QPSK 符号。首个符号在 enable 后首个时钟装入并产生 phase 0 输出，之后按照 sample phase accumulator 产生后续采样。

相邻 sample tick 的间隔只允许为理想采样周期的 floor/ceil 个 50 MHz 时钟；长期平均采样率等于 `4×SYMRATE` 的 32 位 phase-step 结果。符号边界由每四个 sample tick 形成，不再依赖 RECT 的稀疏 `symbol_valid` 作为采样时钟。

禁用、复位和重新启用时：

- phase accumulator 清零。
- polyphase phase 清零。
- PRBS7 恢复固定种子。
- 9 个符号历史清零。
- RRC 输出清零。
- 下次启用从同一首 QPSK 符号开始。

## 7. 4 相多相滤波器

令输出采样下标为 `m=4n+p`，其中 `p=0..3`。只保留输入符号非零的位置，滤波计算为：

```text
Y[4n+p] = Σ_j h[p+4j] × S[n-j]
```

各相 tap 数为：

- phase 0：`h[0],h[4],...,h[32]`，9 taps。
- phase 1：`h[1],h[5],...,h[29]`，8 taps。
- phase 2：`h[2],h[6],...,h[30]`，8 taps。
- phase 3：`h[3],h[7],...,h[31]`，8 taps。

符号历史定义为 `S[0]=当前符号`、`S[1]=前一个符号`，依次向后保存 9 个值。phase 0 载入新符号时，当前输入替代 `S[0]` 参与本次计算，旧 `S[0]` 移入 `S[1]`。这样单符号冲激的中心系数位于第 16 个输出采样，群延迟固定为 16。

推荐 RTL 实现为每个 sample tick 计算一相的最多 9 个 MAC，并在非 sample tick 时保持输出。实现可以使用并行乘加或可证明满足时序预算的时分 MAC；不得为了省资源而改变逐采样输出顺序。

## 8. RECT/RRC 系统接入

数字模式选择继续使用 GPIO11 `[19:17]`：

```text
gpio11[0]     = CH2 PHASELINK
gpio11[8:1]   = CH1 PULSE DUTY
gpio11[16:9]  = CH2 PULSE DUTY
gpio11[19:17] = DIGMOD MODE
gpio11[20]    = DIGMOD FILTER, 0=RECT, 1=RRC
gpio11[31:21] = 保留，写零
```

`reg_bridge` 继续对 GPIO11 做两级同步，增加 `digmod_filter` 输出。GPIO12 的 32 位符号率 phase step 语义不变。

`awg_top` 保持现有 RECT `iq_digital_mod` 实例和 mux，并增加独立 RRC 实例：

```text
合法 DIGMOD + FILTER=RECT → 现有 dig_i/dig_q
MODE=QPSK + FILTER=RRC   → rrc_i16/rrc_q16 经 DAC 缩放
OFF 或保留 MODE 6/7      → 标准 AWG a1/a2
```

硬件保护规则：只有合法数字模式、FILTER=RRC 且 mode=QPSK 时才启用 RRC；任何非法组合均回退到 RECT 或标准 AWG，不允许输出未初始化数据。协议层会阻止非法组合，因此该规则主要用于 GPIO 直接写入或复位竞态保护。

现有 `iq_digital_mod` 不做 RRC 改造，确保 RECT 的 5×127 GOLD、symbol_valid、符号保持和已有板级聚类完全不变。

## 9. FILTER 协议

新增命令：

```text
SET DIGMOD FILTER RECT|RRC  → OK
```

完整状态格式为：

```text
DIGMOD MODE <OFF|BPSK|QPSK|QAM4|QAM16|QAM64> SOURCE PRBS7 FILTER <RECT|RRC> SYMRATE <Hz> STATE <RUNNING|STOPPED>
```

协议语义：

- 默认 `FILTER RECT`。
- FILTER 只能在 MODE OFF 时修改。
- 运行中修改 FILTER 返回 `ERR DIGMOD RUNNING`，已确认 FILTER 不变。
- `FILTER RRC` 只允许随后启用 QPSK。
- MODE 为 OFF 时可以先设置 FILTER RRC，再设置 MODE QPSK。
- FILTER 为 RRC 时尝试启用 BPSK、QAM4、QAM16 或 QAM64，返回 `ERR FILTER REQUIRES QPSK`，MODE 和 FILTER 都不改变。
- `SET DIGMOD MODE OFF` 仍是恢复标准 AWG 的唯一动作，但不会自动把 FILTER 状态改回 RECT。
- 未知 FILTER、缺少参数、额外 token、非法顶层返回 `ERR BAD VALUE`。
- 旧的 9-token GET 状态在上位机解析层兼容，缺少 FILTER 时按 RECT 处理；新固件始终返回完整 11-token 状态。

PS 写 GPIO11 时完整重写 MODE、FILTER、PHASELINK 和两个 DUTY 字段；bit20 与 MODE 同一同步字更新，保证一次配置语义。

## 10. 控制器和 GUI 语义

`DigitalModState` 增加：

```python
filter: str = "RECT"
```

发送 FILTER 或 MODE 后，控制器必须执行 ACK → `GET DIGMOD` → 状态更新。ACK 本身不乐观改变 GUI；ERR 或 timeout 保留最后已确认状态，并写入现有日志区。

数字调制面板增加 RECT/RRC 选择和已确认状态显示：

- FILTER 选择只有 RECT、RRC。
- FILTER 控件在运行中禁用，在 OFF 状态可配置。
- 面板显示 RRC 仅支持 QPSK；选择 RRC 后先保持 OFF，再选择 QPSK 启用。
- MODE 下拉框始终保持可用，运行中可以选择 OFF。
- 符号率控件沿用现有规则，只在 OFF 状态可编辑。
- 默认和旧状态缺少 FILTER 时显示 RECT。

理论预览需要接收当前已确认的 mode、source、symrate 和 filter：

- RECT 继续使用现有 `np.repeat()` 保持曲线和旧默认参数。
- RRC 使用同一 Python 黄金模型，按 4 SPS 生成理论 I/Q 波形。
- 预览必须显示 RRC 群延迟和“仅理论仿真，未接入采集数据”说明。
- 不得把理论曲线标为示波器实测数据。

## 11. Python 黄金模型和 BER 定义

黄金模型至少提供以下稳定接口：

```python
def rrc_coefficients(alpha=0.35, sps=4, span=8) -> np.ndarray:
    """返回 33 个 signed Q1.15 系数。"""


def rrc_filter_symbols(i_symbols, q_symbols, coeffs=None) -> tuple[np.ndarray, np.ndarray]:
    """返回 N×4+32 个 signed int16 发射采样。"""


def matched_filter_symbols(samples, coeffs=None, delay_samples=32) -> tuple[np.ndarray, np.ndarray]:
    """对 sample-rate I/Q 做匹配滤波并返回符号中心抽样。"""
```

`rrc_filter_symbols()` 接受等长的一维 I/Q 符号数组，先插入三个零采样，再执行定点 FIR，返回 `N×4+32` 个包含完整发射尾部的 signed int16 采样。`matched_filter_symbols()` 的 `samples` 参数是 `(i_samples, q_samples)`，对这两个 sample-rate 数组再次执行同一量化 FIR，并在 `delay_samples + 4×n` 位置返回 N 个符号；默认 `delay_samples=32`。调用方去掉首尾各 8 个符号后执行符号判决。

BER=0 的定义为：

- 使用至少 127 个连续 PRBS7 QPSK 符号。
- 发送端和匹配端均使用同一份量化 33-tap 系数。
- 匹配滤波抽样起点为总延迟 32 samples。
- 去除首尾各 8 symbols 的滤波瞬态后，对剩余符号按 I/Q 符号判决。
- I/Q 判决均不得出现符号错误。
- DAC 8-bit 量化回路另测，但不能替代内部 int16 定点 BER 测试。

## 12. 验收门槛

### Python

- 33 个系数、奇异点、对称性、量化值和离散能量通过测试。
- QPSK 4 SPS 插值顺序、首符号、phase 0..3 顺序与 RTL 向量完全一致。
- 负数舍入、16-bit 饱和和 DAC 8-bit 缩放边界通过测试。
- 127 符号匹配滤波去瞬态后 BER=0。
- 现有 RECT `generate_symbols()` 和 `generate_samples()` 输出 bit-exact 不变。

### RTL

- 冲激响应 33 个 tap 与 Python 参考逐点一致。
- 固定 QPSK 序列和连续 PRBS7 逐采样一致。
- 每个符号恰好 4 个采样；10 k、100 k、1 M 的采样边界满足 phase-step floor/ceil 规则。
- 复位、disable、重新 enable 清除历史并从 seed 重启。
- RRC 输出不发生累加器回绕；饱和点与 Python 一致。
- RECT passthrough 和现有 5×127 GOLD 完全不变。
- GPIO11 bit20、MODE、OFF 回退和 DAC offset-binary 通过集成 TB。

### 协议、GUI 和板级

- FILTER 合法/非法命令、GET 完整状态、运行中拒绝和错误状态保持全部通过。
- RRC 仅能启用 QPSK；非 QPSK 组合安全拒绝。
- GUI 只在回读确认后更新 FILTER、模式、符号率和运行状态。
- 10 k、100 k、1 M Sym/s 的 QPSK RRC 采样率通过。
- RECT/RRC 频谱显示滚降趋势，RRC 理论频谱不被标为实测。
- 示波器采集的 RRC I/Q 在总延迟对齐后，经 Python 匹配滤波恢复 QPSK 星座且 BER=0。
- OFF 后现有 1 kHz 标准正弦恢复测试继续通过。
- 原有全部 RTL、协议、上位机和 RECT 板级回归继续通过。

## 13. 风险与限制

| 风险 | 处理 |
|---|---|
| 定点规则漂移 | 系数、舍入、移位、饱和和参考向量由 Python 单一黄金模型生成；RTL 不计算浮点系数 |
| 1 MSym/s 时序不足 | 4 MS/s sample tick；优先使用 4 相、每相最多 9 tap 的结构，综合后检查 DSP、LUT、Fmax |
| RRC 影响 RECT | 保留现有 `iq_digital_mod`，在 `awg_top` 最后一级独立选择；默认 RECT |
| FILTER 多 bit 更新不原子 | 使用 GPIO11 bit20，与 MODE 和旧字段同一完整写字；只允许 OFF 状态配置 |
| 非 QPSK 误启用 RRC | PS 协议拒绝，`awg_top` 硬件保护条件只对 QPSK 激活 |
| RRC 幅度低于 RECT | 不增加隐式增益；在 Python、GUI 和板级报告中分别记录实际幅度，频谱使用归一化比较 |
| 群延迟导致误判 | 明确单 RRC 16 samples、匹配总延迟 32 samples；板级脚本按已确认延迟对齐 |
| 示波器边沿和 DAC 量化 | RRC 不使用 RECT 电平聚类作为主判据，采用中心采样、眼图、频谱和离线匹配滤波 |
| 旧固件状态缺少 FILTER | 上位机兼容 9-token GET，缺少字段默认 RECT；新固件回归必须检查完整 11-token 状态 |

## 14. 文件边界

新增：

```text
host/siggen/rrc.py
host/tests/test_rrc.py
tools/generate_rrc_vectors.py
src/rtl/rrc_coeffs.vh
src/rtl/rrc_filter.v
src/rtl/rrc_digital_mod.v
src/tb/rrc_filter_tb.v
src/tb/rrc_digital_mod_tb.v
src/tb/rrc_expected.mem
host/tests/test_scope_verify_rrc.py
```

修改：

```text
src/rtl/reg_bridge.v
src/rtl/awg_top.v
src/tb/reg_bridge_tb.v
src/tb/awg_top_tb.v
sdk/src/main.c
host/siggen/protocol.py
host/siggen/controller.py
host/siggen/preview.py
host/siggen/ui/widgets.py
host/siggen/ui/main_window.py
host/tests/test_protocol.py
host/tests/test_controller.py
host/tests/test_preview.py
host/tests/test_widgets.py
scripts/test_bridge.py
scripts/scope_verify.py
scripts/build_bitstream.py
scripts/check_bitstream.py
README.md
CLAUDE.md
AGENTS.md
```

`scripts/create_bd.tcl` 不需要修改：FILTER 使用现有 GPIO11 bit20，符号率继续使用已经存在的 GPIO12。若综合发现新增 RRC 资源需要 IP 或时钟改动，必须先单独更新设计规格和 BD 依赖，不能在实施中隐式改变 GPIO 语义。
