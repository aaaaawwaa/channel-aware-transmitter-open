# 内调制功能设计(AM/FM/PM/扫频)

日期: 2026-08-13
状态: 已批准(第 1~5 节, 用户逐节确认)
硬件基线: AC820 (xc7z020clg484-2) + ACM2108 双 DAC(MS9708, 8-bit, 125MSPS 芯片上限);
系统 50MHz Y9 时钟 → 50MSa/s。现有功能: 双通道 DDS(4 波形) + 基波+4 谐波锁相合成,
PC 上位机经 PS 裸机桥(OCM) 以 ASCII 协议控制。

## 1. 目标与范围

在现有谐波合成功能之外,为每通道增加**内调制**能力:

- **AM** 幅度调制: 载波幅度按调制信号变化(标准 AM, 深度 0~100%)
- **FM** 频率调制: 瞬时频率 = 载波 ± 频偏×m
- **PM** 相位调制: 相位按调制信号偏移(谐波同步偏移, 保持相对相位)
- **SWEEP** 扫频: FM 的特例(起点 = 当前 FREQ, 跨度 = fm_dev, 周期 = 1/mfreq)

约束(用户确认):

- 调制类型: AM/FM/PM/扫频全部支持,每通道单选(0=OFF 1=AM 2=FM 3=PM 4=SWEEP)
- 调制源: 正弦/方波/三角/锯齿,频率 0.1Hz~50kHz
- 作用域: 调制作用于**整个输出**(含谐波),与现有谐波功能正交叠加
- 每通道独立调制参数

范围外(不做): DSB-SC/SSB 调制、AM 与 FM 同时混合、调制频谱实时分析。

## 2. 总体架构

```
                    ┌─────────────────────────────────────────────┐
 PS AXI GPIO        │ awg_top (50MHz)                             │
 word0/1 (基础) ────► reg_bridge ──► fword_base                   │
 wordA/B (谐波) ────► reg_bridge ──► base/ord/amp                  │
 wordC/D (调制,新增)─► reg_bridge ──► mode/mwave/mfreq/am/fm/pm    │
                    │                                             │
                    │   ┌──────────┐   fword_inst ──► dds_core ◄──┐│
   fword_base ─────► │   │ mod_synth │   (FM/SWEEP)   ──────► harm_synth ◄─┐
                    │   │ (48-bit  │   pm_off ──────► (地址层)     ││
                    │   │ 调制源)  │   am_gain ──► AM级 ◄─────────┘│
                    │   └──────────┘                │              │
                    │                     s_am ──► ampl_scale ──► dac_driver
                    └─────────────────────────────────────────────┘
```

数据流(每通道一份, 下以 CH1 为例):

1. `reg_bridge` 拆包新增调制字 C/D(见 §5),输出 `mode/mwave/mfreq/am_depth/fm_dev/pm_dev`
2. `mod_synth`(新模块)以 `fword_base` 和调制参数产生 4 个输出:
   - `m`(有符号 8 位): 调制信号,∈[-127, +127]
   - `fword_inst`(32 位): 瞬时 fword,= fword_base(FM 关时)或 FM/SWEEP 注入值
   - `pm_off`(有符号 9 位): PM 相位偏移(地址 LSB 单位)
   - `am_gain`(有符号 17 位): AM 增益 = 12700 + m×am_depth
3. `fword_inst` 同时馈入 `dds_core` 与 `harm_synth`(锁相保持,见 §4 FM 说明)
4. `pm_off` 注入两模块的**查表地址层**(在相位累加器之后,不改频率)
5. 波形选择 mux(dds/harm)之后新增 **AM 级**: `s_am = sat(sample × am_gain / 12700)`
6. 之后仍走 `ampl_scale`(AMPL 0~100%)→ `dac_driver`(偏移二进制)

延迟对齐: mod_synth 输出的 m/fword_inst/pm_off/am_gain 必须同拍有效,
下游各注入点按各自已有流水延迟对齐(m 提前生成,AM 级/FM 注入点用延迟链校准)。

## 3. 调制源(mod_synth 内部)

- 48 位累加器 `acc_m`,增量 `fword48 = mfreq_01 × 562950`
  - `mfreq_01`: 调制频率的 0.1Hz 单位整数,范围 1~500000(0.1Hz~50kHz)
  - 推导: `fword48 = f×2^48/5e7`;f=0.1Hz 时 = 562949.95 ≈ 562950(误差 <0.001%)
- 调制信号 m(有符号 8 位, 取 `acc_m[47:40]` 高字节,复用 dds_core 的波形算法):
  - `mwave=0` SINE: `m = sine_rom(acc_m[47:40])`
  - `mwave=1` SQUARE: `m = acc_m[47] ? -127 : +127`
  - `mwave=2` TRIANGLE: `m = (acc_m[47] ? ~acc_m[46:39] : acc_m[46:39]) - 128`
  - `mwave=3` SAW: `m = acc_m[47:40] - 128`(补码,上升锯齿)

频率跨度核算: 0.1Hz → 周期 10s(2^48/562950 ≈ 5×10^8 拍);50kHz → 每周期 1000 拍,
48 位累加器无溢出问题(50kHz 时 fword48 ≈ 2.8×10^11,周期 1000 拍)。

## 4. 调制数学

### 4.1 AM

```
am_gain = 12700 + m × am_depth        # am_depth ∈ [0,100](%)
s_am    = sat8(sample × am_gain / 12700)
```

- m=0 → am_gain=12700 → 直通(×1)。depth=100% 且 m=±127 → 增益 0~2 倍(标准 AM 定义)
- am_gain 范围: depth≤100 时 ∈ [0, 25400],无负增益;17 位有符号容纳
- 输出饱和 ±127: depth=100% + AMPL=100% 时峰值削波属标准 AM 行为,GUI 提示降低 AMPL
- 实现: `sample(8s) × am_gain(17s)` → 25 位,常量除法 /12700(综合用倒数乘法,同 ampl_scale 模式)

### 4.2 FM(与 SWEEP 共用)

```
fword_inst = fword_base + (m × fword_dev) / 127      # 饱和 [0, 2^31]
fword_dev  = fm_dev × 85 + fm_dev × 9/10             # 与 awg_top 现有 fword 公式一致
```

- `fm_dev ∈ [0, 500000]`(Hz),19 位;`fword_dev` 26 位以内
- `(m × fword_dev)/127`:`m(8s) × fword_dev(26s)` → 34 位,除以 127,加到 32 位 fword
- 饱和下限 0(防负频回绕;瞬时频率=0 时输出保持直流),上限 2^31(25MHz,奈奎斯特)
- **锁相保持**: `fword_inst` 同一值馈入 dds_core 与 harm_synth;harm_synth 谐波相位
  仍由 `ph_k = acc0×ord_k` 派生(acc0 积分的正是 fword_inst)→ 谐波与基波在 FM 下依旧锁相
- **SWEEP**(mode=4): 数据通路与 FM 完全一致,语义上"起点 = 当前 FREQ、跨度 = fm_dev、
  周期 = 1/mfreq";GUI 预设 mwave=TRIANGLE(双向扫)或 SAW(单向上扫跳回)

### 4.3 PM

```
pm_off = (m × pm_dev) / 127            # 有符号 9 位,±511
基波:  addr0 = acc0[31:24] + pm_off
谐波:  addr_k = ph_k[31:24] + (ord_k × pm_off)    # 均 mod 256 回绕
       然后按现有 ord 奇偶条件翻转(偶次 ^0x80, 顺序: 先加偏移再翻转)
dds_core: addr = phase_acc[31:24] + pm_off
```

- `pm_dev ∈ [0, 511]`,单位 1/256 周期 = 1.40625°(GUI 显示 0~180° = 0~128 LSB)
- 注入点在**查表地址**(累加器之后),不改相位累加器 → 不影响频率
- 谐波偏移放大 ord 倍,与"θk ≡ ord×θ0"一致 → 谐波相对相位在 PM 下保持不变
- 地址回绕 mod 256 安全(无毛刺)
- `ord_k × pm_off` 最大 8×511=4088,13 位有符号,取低 8 位加(等价 mod 256)

### 4.4 直通(mode=OFF)

m 仍按 mfreq 运行但输出被抑制: `fword_inst=fword_base`、`pm_off=0`、`am_gain=12700`,
输出与现版本比特级一致(调制全 0 不改变数据通路行为)。

## 5. 寄存器与 BD 扩展

### 5.1 字 C / 字 D 打包(每通道 2 个新 32 位字)

```
Word C (CH1: gpio6, CH2: gpio7):
  [2:0]   mode      0=OFF 1=AM 2=FM 3=PM 4=SWEEP(5..7 保留)
  [4:3]   mwave     0=SINE 1=SQUARE 2=TRIANGLE 3=SAW
  [23:5]  mfreq     0.1Hz 单位, 1..500000(0.1Hz~50kHz)
  [31:24] am_depth  0..100(%)

Word D (CH1: gpio8, CH2: gpio9):
  [18:0]  fm_dev    Hz, 0..500000
  [27:19] pm_dev    1/256 周期单位, 0..511
  [31:28] reserved
```

哨兵: 全 0 = 合法关闭态(mode=0),与谐波字一样无哨兵问题(不需要 DEF 默认值)。

### 5.2 reg_bridge 扩展

- 新增输入 `gpio6_in..gpio9_in`(4 个,2-FF 同步,同现有模式)
- 拆包输出每通道: `mode[2:0] mwave[1:0] mfreq[18:0] am_depth[7:0] fm_dev[18:0] pm_dev[8:0]`
- 不做钳位(PS 负责校验,与谐波字一致)

### 5.3 BD 扩展(create_bd.tcl)

- 新增 2 个 AXI GPIO:`axi_gpio_3`(字 C 通道对)、`axi_gpio_4`(字 D 通道对),
  配置与现有 3 个完全相同(`C_ALL_OUTPUTS 1` + `C_ALL_OUTPUTS_2 1`)
- interconnect 加 M03_AXI/M04_AXI;连线 `gpio6_in..gpio9_in` ↔ `awg_top_0`
- PS 侧地址映射自动生成:XPAR_AXI_GPIO_3/4_DEVICE_ID
- awg_top 端口新增 4 个输入(OOC 综合会因端口变化自动重新生成)

### 5.4 awg_top 扩展

- 计算 `fword_dev = fm_dev×85 + fm_dev×9/10`(26 位)、`fword48 = mfreq×562950`(39 位)
- 实例化 mod_synth ×2
- `fword_inst` 选择: mode∈{FM,SWEEP} ? mod.fword_inst : fword_base,馈入 dds_core/harm_synth
- dds_core 新增 `pm_off` 输入(地址层);harm_synth 新增 `pm_off` 输入(§4.3)
- mux 后新增 AM 级模块(`am_gain` 输入),在 ampl_scale 之前
- LED 显示不变

## 6. PS 桥协议扩展(sdk/src/main.c)

ch_state_t 新增字段: `mode, mwave, mfreq_01, am_depth, fm_dev, pm_dev`;
`pack_word_c()/pack_word_d()`,write_all() 追加 4 行 XGpio_DiscreteWrite。
默认值: mode=OFF, mwave=SINE, mfreq_01=10(1Hz), am_depth=100, fm_dev=0, pm_dev=0。

新命令(大小写不敏感,CR/LF 结尾,响应 OK / ERR <原因>):

```
SET CH1|CH2 MODE OFF|AM|FM|PM|SWEEP
SET CH1|CH2 MODWAVE SINE|SQUARE|TRIANGLE|SAW
SET CH1|CH2 MODFREQ <1..500000>       # 0.1Hz 单位
SET CH1|CH2 AMDEPTH <0..100>          # %
SET CH1|CH2 FMDEV <0..500000>         # Hz
SET CH1|CH2 PMDEV <0..511>            # 1/256 周期 LSB
```

越界 → `ERR MODE OUT OF RANGE` / `ERR MODWAVE OUT OF RANGE` / `ERR MODFREQ OUT OF RANGE`
/ `ERR AMDEPTH OUT OF RANGE` / `ERR FMDEV OUT OF RANGE` / `ERR PMDEV OUT OF RANGE`。
GET 响应追加(放在 H4 之后,旧解析器按未知键跳过/新解析器显式读取):

```
CH1 WAVE SINE FREQ 1000 AMPL 50 BASE 100 H1 0 0 .. H4 0 0
    MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0
```

## 7. 上位机(host/)

### 7.1 protocol.py

- 新常量 MODES/MODWAVES;`mod_cmd(ch, field, value)` 构造 + 校验(与 harm_cmd 同风格)
- `parse_status()` 扩展: 识别 MODE/MODWAVE/MODFREQ/AMDEPTH/FMDEV/PMDEV 键;
  旧格式(无调制字段)兼容 → 这些键为 None
- MODFREQ 输入解析: 支持小数 Hz(如 "1.5")×10 取整为 0.1Hz 单位,与 parse_freq_input 同风格

### 7.2 controller.py

ChannelState 新增调制字段;Controller 经 `set_field(ch, field, value)` 路由调制字段
(mode/mwave/modfreq/amdepth/fmdev/pmdev → mod_cmd), OK ack 后本地直接应用该值
(调制字段不回读;仅 base/harm 等归一化字段发 GET 回读)。

### 7.3 GUI(ChannelPanel 新增"调制"分组)

- 模式下拉: OFF/AM/FM/PM/扫频(单选,互斥)
- 调制波形下拉: 正弦/方波/三角/锯齿
- 调制频率输入: 支持 k 后缀与小数(0.1Hz~50kHz)
- AM 深度 spin: 0~100%;FM 频偏 spin: 0~500kHz;PM 相位 spin: 显示 °(0~180°),发送 LSB(=°×0.7111)
- SWEEP 模式下 FM 频偏标签显示"扫频跨度",调制波形自动预设三角(可改)
- 字段随 mode 联动显隐(如 OFF 时调制参数置灰)

### 7.4 预览(preview.py make_plot_data 扩展)

- 新增 `mod` 参数(mode/mwave/mfreq/depth/dev);数值生成:
  - AM: 逐点包络乘 `1 + m(t)×depth/100`,再乘谐波合成样本,饱和 ±127
  - FM/SWEEP: 逐点积分 `θ(t+1) = θ(t) + fword_inst(t)`,谐波相位 `θk = k×θ`(锁相),
    偶次谐波按硬件行为 ^0x80 翻转 → 预览与示波器实测一致
  - PM: `θ += offset(t)`,谐波偏移 ×ord
- 调制采样率用与硬件一致的 50MHz 时基,窗口内生成与 GUI 时间轴匹配的点数

## 8. 测试计划

### 8.1 RTL 仿真(mod_synth_tb 新建, 约 10 项)

1. 调制源 4 波形形状正确(sine 对称、square ±127、tri/saw 线性)
2. fword48 频率正确: 0.1Hz → 10s 周期;50kHz → 1000 拍/周期
3. AM: m=0 → gain=12700(直通);depth=100,m=±127 → 25400/0;输出饱和 ±127
4. FM: `fword_inst = fword_base ± fword_dev` 边界;饱和下限 0
5. PM: `pm_off = m×pm_dev/127`;谐波偏移 = ord×pm_off(mod 256)
6. SWEEP == FM(同输入同输出)
7. mode=OFF 直通(输出与无调制时比特级一致)
8. 管道延迟对齐(m 与 fword_inst/gain/pm_off 同拍)

awg_top_tb 扩展: 字 C/D 拆包 + AM/FM 全链路冒烟(波形幅度/频率随调制变化)。

### 8.2 协议回归(test_bridge.py, +12 项, 需板卡在线)

- 6 个新命令设置 + GET 回读一致
- 越界错误: MODE=5 / MODFREQ=0 / AMDEPTH=101 / FMDEV=500001 / PMDEV=512
- mode 切换时其余字段保持;默认值回读
- 原有 39 项回归全过

### 8.3 上位机单元测试(host pytest)

- protocol.py: mod_cmd 边界、parse_status 新旧格式、MODFREQ 小数解析
- preview: AM 包络深度、FM 后瞬时频率变化、SWEEP 端点频率、谐波叠加下 PM

### 8.4 示波器闭环(scope_verify.py, +4 项, 需板卡+DSOX1102A)

1. AM 包络: 载波 10kHz + 调制 1kHz 正弦,固定时基测量包络峰谷比 ≈ 1+depth/100
2. FM 边带: FFT 观察 f0±fm 边带对
3. PM: 相位跳变或频谱边带(与 FM 区分: 同载波同调制下 PM 边带为 J 函数形态)
4. SWEEP: 端点频率 f0±dev 随时间往返
- 原有 9 项回归全过

### 8.5 构建与下载

- 流程不变:`build_bitstream.py`(RTL+BD 变更检测)→ `program_board.sh` → PS 桥重建
- PS 侧:`cli-anything-vivado sdk app build`(main.c 变更)

## 9. 构建流程影响(build_bitstream.py / check_bitstream.py)

- 现有 `hash_rtl()` 只哈希 `src/rtl/*.v`;**本次 BD 变更(2 个新 AXI GPIO)不会触发重建**。
  必须扩展: 哈希输入加入 `scripts/create_bd.tcl`(BD 源),两脚本同步修改。
- 重建路径已修改: rebuild_bitstream.tcl 先重跑 create_bd.tcl 重新生成 BD(结构变化才能生效),再 reset_target+generate_target+OOC+主综合+实现。构建哈希覆盖 src/rtl/*.v、create_bd.tcl、ps7_props.tcl。

## 10. 风险与边界

| 项 | 说明 | 处理 |
|----|------|------|
| FM 负频 | dev>载波时瞬时频率可为负 | PL 饱和下限 0;PS 校验 fm_dev≤500kHz |
| AM 削波 | depth=100% + AMPL=100% 峰值为 2×载波 | 饱和 ±127(标准 AM);GUI 提示 |
| 调制频率上限 | 50kHz(50MSa/s → 1000 点/周期) | 协议校验 MODFREQ≤500000 |
| PM 回绕 | 地址 mod 256 | 无毛刺,语义安全 |
| 管道延迟 | mod 输出与下游注入点需对齐 | mod_synth_tb 第 8 项覆盖 |
| OOC 陷阱 | awg_top 端口新增 4 个 | build_bitstream.py 强制全重建(哈希含 create_bd.tcl) |
| 谐波+调制 | 谐波走同一调制通路 | 数据通路设计保证(§4) |

## 11. 实现顺序(建议)

1. mod_synth 模块 + mod_synth_tb(TDD)
2. dds_core/harm_synth PM 输入 + AM 级(awg_top_tb 扩展, TDD)
3. reg_bridge 扩展 + awg_top 集成(awg_top_tb, TDD)
4. create_bd.tcl 扩展(2 个 AXI GPIO)+ build_bitstream.py/check_bitstream.py 哈希扩展
5. 构建 + PS 桥扩展(main.c)+ 上板协议测试
6. 上位机 protocol.py/controller/preview/GUI(pytest)
7. scope_verify.py 扩展 + 示波器闭环回归
8. README/CLAUDE.md 更新
