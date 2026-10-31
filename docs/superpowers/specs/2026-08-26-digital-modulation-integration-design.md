# FPGA 数字调制端到端集成设计

日期: 2026-08-26  
状态: 已批准设计，待书面规格审阅  
前置条件: 独立 `iq_digital_mod` Python/RTL 模型、Vivado xsim、5 × 127 符号 GOLD 比对和生产 `SYM_TICKS=5000` 自检均已通过。

## 1. 目标与范围

将已验证的内部 PRBS7 BPSK/QPSK/QAM4/QAM16/QAM64 发射器接入 AC820 双 DAC，形成可通过 PS ASCII 协议和现有 PySide6 上位机控制的全局 I/Q 数字调制功能。

本阶段交付:

- `awg_top` 中的 I/Q DAC 旁路 mux；
- 复用现有 AXI GPIO11 高位的全局数字模式控制；
- PS `SET/GET DIGMOD` ASCII 命令与状态回读；
- GUI 方案 A：标准波形/数字调制页签、共享调参面板、理论时域/星座/对比视图；
- RTL、Python、协议、GUI、bitstream 和双通道示波器时域验证。

本阶段不交付:

- 可调符号率、可调数字幅度或平均功率归一化；
- 外部数据输入、实时符号上传、RRC 成形、解调、BER；
- 示波器采集导入、自动实测星座/EVM 或把理论曲线伪装为实测；
- 8QAM、32QAM 或任意阶数 QAM。

## 2. 全局模式与 GPIO11 打包

数字调制是共享 DAC0/DAC1 的全局资源，不属于 CH1 或 CH2。复用 GPIO11 的未分配高位，避免新增 AXI GPIO、BD 端口、地址映射和互连主口。

```text
gpio11[0]     = 现有 CH2 PHASELINK
[8:1]         = 现有 CH1 PULSE DUTY
[16:9]        = 现有 CH2 PULSE DUTY
[19:17]       = digmod_sel
[31:20]       = 保留，写零
```

`digmod_sel` 编码:

| 值 | 名称 | `iq_digital_mod.mode` | 行为 |
|---:|---|---:|---|
| 0 | OFF | — | 标准 AWG 链路 |
| 1 | BPSK | 0 | DAC0=I，DAC1=Q |
| 2 | QPSK | 1 | DAC0=I，DAC1=Q |
| 3 | QAM4 | 2 | DAC0=I，DAC1=Q |
| 4 | QAM16 | 3 | DAC0=I，DAC1=Q |
| 5 | QAM64 | 4 | DAC0=I，DAC1=Q |
| 6、7 | 保留 | — | 安全按 OFF 处理 |

`reg_bridge` 保持 GPIO11 的两级同步，并新增 `digmod_sel[2:0]` 输出。复位时同步寄存器为零，因此默认为 OFF。

## 3. RTL 数据路径

```text
PS GPIO11[19:17]
    → reg_bridge 两级同步 / digmod_sel
    → awg_top 解码
    → iq_digital_mod(enable, mode)
    → I/Q mux
    → dac_driver.sample0 / sample1
    → DAC0 / DAC1
```

`awg_top` 实例化生产参数 `SYM_TICKS=5000` 的 `iq_digital_mod`。

- `digmod_sel=1..5` 时，`enable=1`，模式为 `digmod_sel-1`；模块输出的 signed I/Q 直接接到 `dac_driver` 的 `sample0/sample1` 输入。
- `digmod_sel=0、6、7` 时，`enable=0`，mux 保持既有 `a1/a2 → dac_driver` 数据路径。
- 数字模式固定满量程，明确旁路 DDS、谐波、AM、幅度缩放、相位联动和 PULSE/NOISE 选择；这些标准 AWG 配置在后台保留，但不影响已启用 I/Q。
- 关闭数字模式后恢复已有 CH1/CH2 标准输出，不重置其频率、幅度、波形、谐波或调制状态。
- `iq_digital_mod` 在 enable 下降后恢复固定 PRBS seed；下次启用从同一首符号开始。模式变化仅在符号边界生效。

GPIO11 的新位与现有 PHASELINK/DUTY 在同一同步字中更新。PS 桥固件每次都会完整重写该字；模式切换是全局原子语义，不依赖 CH1/CH2 两个控制字同时到达。

## 4. PS ASCII 协议与状态

PS 新增独立 `digmod_mode` 状态，取值 `OFF/BPSK/QPSK/QAM4/QAM16/QAM64`。`pack_word_f()` 在保留现有低 17 位后写入 `(digmod_mode << 17)`；模式值严格限制为 0..5。

协议:

```text
SET DIGMOD MODE OFF|BPSK|QPSK|QAM4|QAM16|QAM64  → OK
GET DIGMOD                                         → DIGMOD MODE <value>
```

规则:

- `GET CH1`、`GET CH2` 的格式和字段不变；`GET DIGMOD` 是全局独立回读。
- 未知模式、缺少模式、额外 token、非法顶层或字段返回既有风格 `ERR BAD VALUE`，且不改变当前数字模式。
- 标准 `SET CHx ...` 不隐式关闭数字模式；`SET DIGMOD MODE OFF` 是唯一恢复标准 DAC 输出的协议动作。
- 固件默认值为 OFF，启动后 `write_all()` 写入零模式编码。

## 5. 上位机状态、GUI 与预览

### 5.1 协议和控制器

上位机协议层增加 `DIGMODES`、`digmod_cmd()` 和 `parse_digmod_status()`。控制器增加独立 `DigitalModState`，不扩展或复用 `ChannelState`。发送模式或符号率命令后必须由 `GET DIGMOD` 回读确认状态；串口超时或 `ERR` 时控件回到已确认状态并在现有日志区记录原因。

### 5.2 方案 A 布局

```text
顶栏: 连接栏 + 标准波形 / 数字调制页签
左侧: 当前页调参区
右上: 单一预览区
右下: 日志区
```

- 标准波形页复用现有 CH1/CH2 面板，不改变原有操作、颜色、字号或布局语义。
- 数字调制页使用一个共享 `DigitalModPanel`：模式下拉框、固定数据源“内部固定 PRBS7”、可配置符号率、DAC0=I/DAC1=Q 说明；选择模式立即下发，选择 `OFF` 立即恢复标准 AWG 输出。
- 数字调制页不显示两组 CH 参数，也不提供本阶段未实现的幅度或其他数据源控件；符号率仅在 `OFF` 状态可编辑和设置。
- 新页面复用现有 Qt 系统浅色外观、默认字号和控件样式；仅沿用现有 green/gray/orange/blue 状态提示。不得引入新深色主题或全局字体缩放。

### 5.3 预览语义

右上单一预览区添加视图切换:

| 视图 | 内容 |
|---|---|
| 时域 | Python 黄金模型的 I/Q 符号保持曲线，100 µs/符号 |
| 星座 | 同一模型生成的理论离散 I/Q 点集 |
| 对比 | 显示“仅理论仿真，未接入采集数据”；不绘制虚构实测曲线 |

无数字调制启用时，预览继续显示已有标准波形。数字调制页只在模式被确认后更新理论预览。

## 6. 文件范围

预计修改:

```text
src/rtl/awg_top.v
src/rtl/reg_bridge.v
src/tb/awg_top_tb.v
src/tb/reg_bridge_tb.v
sdk/src/main.c
host/siggen/protocol.py
host/siggen/controller.py
host/siggen/preview.py
host/siggen/ui/main_window.py
host/siggen/ui/widgets.py
host/tests/test_protocol.py
host/tests/test_controller.py
host/tests/test_preview.py
host/tests/test_widgets.py
scripts/test_bridge.py
scripts/scope_verify.py
README.md
```

继续复用且不改变其独立契约:

```text
src/rtl/iq_digital_mod.v
src/tb/iq_digital_mod_tb.v
host/siggen/digital_mod.py
host/tests/test_digital_mod.py
```

`create_bd.tcl` 不因本设计修改：GPIO11 已存在且有足够空闲位。

## 7. 验证顺序与验收

1. Python/上位机单元测试：DIGMOD 命令、状态解析、回读、错误与超时；理论 I/Q 时域/星座；页签和“仅理论”提示。
2. RTL testbench：GPIO11 `[19:17]` 解码，OFF/保留值回退，I/Q mux，原 PHASELINK/DUTY 位保持；标准链路在 OFF 时无回归。
3. Vivado xsim：保留 `iq_digital_mod` 的 PRBS、GOLD 和临时 5000-tick 验证；使用 `xvlog → xelab → xsim`。
4. 完整 bitstream：只使用 `python scripts/build_bitstream.py`，再运行 `python scripts/check_bitstream.py`。
5. 固件：重新构建 SDK ELF 并用绝对路径加载到 PS，避免运行旧桥程序。
6. 协议回归：扩展 `scripts/test_bridge.py`，覆盖全部合法/非法 `SET/GET DIGMOD` 和标准 AWG 兼容性。
7. 双通道时域板级验证：示波器同时接 DAC0/DAC1，验证 BPSK/QPSK/QAM16/QAM64 的 I/Q 离散电平、10 kSym/s（100 µs 每符号）、保持长度、模式切换及 OFF 后标准波形恢复。为获得充分点覆盖，QAM64 捕获至少 512 个符号。结果标为时域测量，不标为实测星座。

可行性门槛:

```text
[ ] DIGMOD OFF 时现有 AWG/调制/相位联动回归通过
[ ] BPSK/QPSK/QAM4/QAM16/QAM64 可经 SET/GET 完整切换与回读
[ ] gpio11 低 17 位语义不变
[ ] 保留编码 6/7 安全回退为标准 AWG
[ ] DAC0=I、DAC1=Q，固定满量程且无削顶
[ ] Python/RTL GOLD 5 × 127 完全一致
[ ] 生产 5000 tick xsim 通过
[ ] GUI 仅在回读确认后显示模式，且不伪造实测
[ ] 双通道示波器时域验证通过
```

## 8. 风险与处理

| 风险 | 处理 |
|---|---|
| GPIO11 旧字段被覆盖 | `pack_word_f()` 单元测试完整位图，RTL TB 覆盖低 17 位不变 |
| 数字模式污染标准 AWG | mux 仅由全局 `digmod_sel` 控制；OFF 和保留编码直通 `a1/a2` |
| 旧 PS ELF 仍运行 | SDK 重建后通过绝对 ELF 路径重新加载，再跑协议回归 |
| 旧 bitstream 被下载 | 只用 `build_bitstream.py` 和 `check_bitstream.py` |
| GUI 显示硬件实测幻觉 | 对比视图无采集源时固定标注仅理论 |
| GUI 新页风格漂移 | 复用当前系统浅色控件、字体与现有提示色 |
| DAC 模拟极性影响判读 | 板级脚本按现有 DAC/示波器校准链路验证相对 I/Q 电平，不把理论符号正负直接等同为探头物理正负 |
