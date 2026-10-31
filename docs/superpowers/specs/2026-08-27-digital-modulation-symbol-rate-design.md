# 数字调制符号流与可变符号率设计

日期: 2026-08-27  
状态: 已实现并完成 RTL、Python、协议、上位机与板级验收  
前置条件: 全局 PRBS7 I/Q 发射链路、`SET/GET DIGMOD`、5 × 127 Python/RTL GOLD、6 档符号率仿真、协议回归、上位机 208 项测试和示波器 28/28 闭环均已通过。

## 1. 目标与范围

本阶段将固定符号保持计数器替换为 32 位相位累加器，并将 PRBS7、星座映射和符号定时拆成独立边界。交付内容如下：

- 固化全部星座映射、电平与 MSB-first bit 顺序；
- 将 PRBS7 从 `iq_digital_mod` 中拆分为可替换的符号源；
- 定义 PRBS7 source 到 mapper 的 6-bit 内部符号流；
- 新增独立 32 位 AXI GPIO 配置字，承载 PS 计算的符号相位步进；
- 新增 `SET DIGMOD SYMRATE <10..1000000>` 和扩展后的 `GET DIGMOD`；
- 覆盖 6 个符号率的 RTL 仿真、既有 5 × 127 GOLD 回归和 10 k/100 k/1 MSym/s 板级验证；
- 更新上位机回读状态、README 和测试说明。

本阶段只支持内部 `PRBS7` 数据源。不会实现 BRAM/AXI-Stream 上传、成帧、CRC/FEC、RRC 成形、载波上变频、EVM 或 BER。

## 2. 可行性与约束

PL 时钟和 DAC 更新率均为 50 MHz。1 MSym/s 时每个符号有 50 个 DAC 更新点，符号调度和 DAC 输出不存在速率瓶颈。

现有 GPIO11 的分配如下：

```text
gpio11[0]     = CH2 PHASELINK
[8:1]         = CH1 PULSE DUTY
[16:9]        = CH2 PULSE DUTY
[19:17]       = DIGMOD MODE
[31:20]       = 仅剩 12 个保留位
```

12 位无法同时保留任意整数 Hz 的符号率和 32 位相位步进。不得跨多个现有保留位拼接该配置，因为两个 AXI GPIO 的写入不是原子操作。方案新增一组专用的 32 位 AXI GPIO，不改变 GPIO11 的既有语义。

相位累加器在非整除速率下会产生最多一个 50 MHz 时钟周期的边界间隔抖动；长期平均符号率由 32 位步进决定。10 k、100 k、1 MSym/s 都处于可测量、可验证范围。

## 3. 固定星座与 bit 顺序

`symbol_bits[5]` 为符号的第一个 PRBS bit（`b0`），随后依次为 `symbol_bits[4]` 到 `symbol_bits[0]`（`b1` 到 `b5`）。每个 bit 值为 0 映射正电平、1 映射负电平的规则仅限 BPSK/QPSK 轴的二元判决；QAM 轴使用下表的固定编码。

| 模式 | 消耗 bit | I 映射 | Q 映射 |
|---|---:|---|---|
| BPSK | 1 | `b0`: 0→+127，1→−127 | 0 |
| QPSK | 2 | `b0`: 0→+127，1→−127 | `b1`: 0→+127，1→−127 |
| QAM4 | 2 | 与 QPSK 相同 | 与 QPSK 相同 |
| QAM16 | 4 | `b0b1`: 00→+126，01→+42，10→−126，11→−42 | `b2b3` 使用同一表 |
| QAM64 | 6 | `b0b1b2`: 000→+126，001→+90，010→+18，011→+54，100→−126，101→−90，110→−18，111→−54 | `b3b4b5` 使用同一表 |

QAM4 保留为协议兼容模式，但其 4 点映射与 QPSK 完全相同。Python `host/siggen/digital_mod.py` 与 RTL 以此表为唯一黄金契约。

## 4. RTL 架构与内部接口

```text
sym_phase_step
    → symbol scheduler / symbol_tick
    → prbs7_source.symbol_bits[5:0]
    → iq_constellation_mapper
    → i_symbol / q_symbol
    → awg_top I/Q mux → DAC0 / DAC1
```

### 4.1 `prbs7_source`

新模块保存 7-bit PRBS 状态，初始种子固定为 `7'h7f`，多项式保持现有 `x^7 + x^6 + 1` 行为。接口：

```text
输入:  clk, rst_n, reset_seed, advance, advance_bits[2:0]
输出:  symbol_bits[5:0]
```

- `symbol_bits` 为当前状态开始的 6 bit look-ahead，满足 mapper 在一个符号边界最多消费 6 bit 的需要；
- `advance=1` 时，状态前进 `advance_bits` 次；合法值为 1、2、4、6；
- 禁用、复位和 `MODE OFF` 都将状态恢复为种子；
- mapper 使用边界前的 `symbol_bits`，PRBS 在同一边界后的时钟状态更新，确保首符号和既有 5 × 127 序列不变。

### 4.2 `iq_constellation_mapper`

新组合模块输入 `mode` 与 `symbol_bits`，输出 `i_symbol`、`q_symbol` 和 `consume_bits`。它不包含时钟、PRBS 状态或符号率逻辑。非法模式输出零 I/Q 且 `consume_bits=0`。

### 4.3 `iq_digital_mod`

`iq_digital_mod` 重构为发射调度器，接口新增 `sym_phase_step[31:0]`，继续输出 `i_sample`、`q_sample`、`symbol_valid`。

- 启用后的第一个时钟产生首个符号和单周期 `symbol_valid`；
- 其后每个 PL 时钟执行 `{carry, phase_acc} = phase_acc + sym_phase_step`；
- `carry=1` 时产生下一个符号，向 PRBS source 发出 `advance`，并拉高单周期 `symbol_valid`；
- 禁用时复位调度器、PRBS 和 I/Q 输出；
- 模式在下一符号边界生效，现有模式切换语义保持不变。

符号率仅允许在数字调制停止时配置。运行中收到 `SET DIGMOD SYMRATE` 返回 `ERR DIGMOD RUNNING`，避免跨时钟域多 bit 配置更新在运行中形成一次异常边界。

## 5. 新 AXI GPIO 配置字

在 `scripts/create_bd.tcl` 新增 `axi_gpio_6`，配置为单通道、32-bit、PS 写入、PL 读取，不连接外部物理 IO。

```text
PS axi_gpio_6 channel 1
  → awg_top.gpio12_in
  → reg_bridge 两级同步
  → digmod_sym_phase_step[31:0]
  → iq_digital_mod.sym_phase_step
```

PS 保存原始 `symrate_hz`，默认 `10000`，并以 64 位整数计算：

```c
sym_phase_step = ((u64)symrate_hz << 32) / 50000000u;
```

PS 写入顺序为：先写新的 phase step 配置字，再写包含 MODE 的 GPIO11 字。这样由 `MODE OFF` 转入运行状态时，PL 一定先见到稳定的符号率配置。

新增 IP 改变 BD 派生状态和 SDK `xparameters.h`。实现后必须通过 `python scripts/build_bitstream.py` 重建 BD、OOC、主综合、实现和 bitstream，并重建 SDK ELF。

## 6. PS、上位机与协议

协议新增：

```text
SET DIGMOD SYMRATE <10..1000000>  → OK
```

- 非数字、零、负数、低于 10 或高于 1,000,000 返回范围错误；
- 数字调制运行时设置符号率返回 `ERR DIGMOD RUNNING`，不改变已确认配置；
- `SET DIGMOD MODE OFF` 后可设置符号率；下一次启用使用新速率；
- `SET DIGMOD MODE ...` 继续是启动/停止的唯一控制命令。

`GET DIGMOD` 统一替换为：

```text
DIGMOD MODE <OFF|BPSK|QPSK|QAM4|QAM16|QAM64> SOURCE PRBS7 SYMRATE <Hz> STATE <RUNNING|STOPPED>
```

其中 `STATE` 由 `MODE != OFF` 推导。上位机 `DigitalModState` 增加 `source`、`symrate_hz`、`running`；协议解析、控制器回读和数字面板状态文字同步更新。GUI 数字调制面板提供 `10..1000000 Sym/s` 符号率输入和设置按钮，模式下拉框选择后立即下发，选择 `OFF` 立即关闭数字调制；符号率仅在 `OFF` 状态允许编辑和设置，设置 ACK 后通过 `GET DIGMOD` 更新已确认值；数据源当前固定为 `PRBS7`，以只读状态显示，不增加新的数据源控件或用户数据上传功能。

## 7. 验证与验收

### 7.1 RTL 和 Python

1. `prbs7_source_tb`：验证 PRBS 种子、127 非零状态、1/2/4/6 bit 消耗、look-ahead 和复位重启；
2. `iq_constellation_mapper_tb`：枚举每种模式的输入编码、固定 I/Q 电平、consume bit 数和非法模式；
3. 改造 `iq_digital_mod_tb`：保留模式切换、符号有效脉冲、重启和非法模式测试；5 × 127 条 `GOLD` 输出仍须与 Python `generate_symbols()` 精确匹配；
4. 6 个速率：10 k、25 k、100 k、250 k、500 k、1 MSym/s。验证边界间隔仅为理想周期的 floor/ceil 个时钟、平均速率符合 32-bit 步进、无早到 `symbol_valid`；
5. 生产 10 k 验收不再依赖固定计数器，而以 `sym_phase_step` 的等效生产配置完成；
6. 更新 `reg_bridge_tb`、`awg_top_tb`，验证新配置字同步、DIGMOD mux、OFF 恢复和原 GPIO11 低 20 位无回归。

### 7.2 协议和上位机

- 覆盖 `SYMRATE` 合法范围、非法输入、运行中拒绝、GET 完整字段、ACK/ERR/超时状态不变；
- 现有模式命令、标准 AWG 命令和数字模式 OFF 恢复持续回归；
- 上位机离屏测试验证回读的 source/rate/state 文字和符号率控件，只在确认状态更新；DIGMOD ACK 后自动发送 `GET DIGMOD`，本地状态不再乐观覆盖，完整上位机回归为 208 项通过。

### 7.3 板级

- 保留 10 kSym/s 的 BPSK/QPSK/QAM16/QAM64 双通道电平聚类；实测 4 种模式均通过 I/Q 电平、最小间距和 inlier 判定。
- 以 QPSK 验证 10 k、100 k、1 MSym/s 的联合 I/Q 跳变间隔；三档均测得目标符号周期并通过容差。
- `scope_verify.py` 不再使用固定 `60 us` 保持阈值，而是基于 `1 / SYMRATE` 和采集点间隔构造速率相关的容差。
- 符号周期提取先丢弃短于典型稳定标签运行一半的边沿过渡，再取稳定运行低分位；该规则保留 BPSK 连续相同比特造成的 2T 运行中的最短基础周期。
- 1 MSym/s 采集中的模拟边沿会产生少量短标签运行，数字 I/Q inlier 阈值按速率设为 0.95；10 k/100 kSym/s 仍为 0.99。该阈值仅补偿采集过渡，不改变星座、最小间距或符号周期判定。
- 串口启动先等待桥欢迎语并执行一次 `SET DIGMOD MODE OFF` 握手；读行器保留半行和同一批数据中的后续行，避免启动欢迎语或部分 `OK` 造成命令响应错位。
- 验收结果: `scripts/test_bridge.py COM11` 为 `ALL PASS`；`scripts/scope_verify.py COM11` 为 `28/28 PASS`，含 DIGMOD OFF 后 1 kHz 标准正弦恢复。

## 8. 文件范围

预计修改：

```text
scripts/create_bd.tcl
src/rtl/prbs7_source.v
src/rtl/iq_constellation_mapper.v
src/rtl/iq_digital_mod.v
src/rtl/reg_bridge.v
src/rtl/awg_top.v
src/tb/prbs7_source_tb.v
src/tb/iq_constellation_mapper_tb.v
src/tb/iq_digital_mod_tb.v
src/tb/reg_bridge_tb.v
src/tb/awg_top_tb.v
sdk/src/main.c
host/siggen/protocol.py
host/siggen/controller.py
host/siggen/ui/widgets.py
host/tests/test_digital_mod.py
host/tests/test_protocol.py
host/tests/test_controller.py
host/tests/test_widgets.py
scripts/test_bridge.py
scripts/scope_verify.py
README.md
```

`host/siggen/digital_mod.py` 保留为星座映射和 GOLD 参考；若抽取共享常量，只能在保持既有 API 和 5 × 127 输出不变的前提下进行。

## 9. 风险与处理

| 风险 | 处理 |
|---|---|
| 新 AXI GPIO 未进入已有 BD | 只使用 `build_bitstream.py`，由脚本重新执行 `create_bd.tcl`；下载前运行 `check_bitstream.py` |
| SDK 使用旧的 `xparameters.h` 或 ELF | 从最新 HDF 全新生成 `sdk/ws_symrate`，确认 `axi_gpio_6` device ID 6 和 `GPIO12` 产物，再强制重建 SDK ELF |
| 运行中跨时钟域更新速率 | 协议只允许 STOPPED 状态设置 `SYMRATE`；PS 先写新配置字再启用模式 |
| 星座或 bit 顺序漂移 | mapper 真值表单测 + Python 5 × 127 精确 GOLD 作为不可移除回归 |
| 相位累加器被误判为固定周期计数器 | 速率测试以 floor/ceil 相邻间隔和长期平均为准 |
| 高速示波器测试混叠 | 用目标速率动态设置时基、采集长度和联合 I/Q 跳变判据；过滤边沿过渡并按速率保留 inlier 阈值 |
| 数字模式影响标准 AWG | `awg_top` mux 只在合法数字模式启用；OFF 和保留编码继续直通标准链路 |
| SDK 启动欢迎语或部分串口响应造成测试错位 | 板级脚本等待桥启动并完成命令握手；读行器按换行拆分且保留未完成/后续数据 |
