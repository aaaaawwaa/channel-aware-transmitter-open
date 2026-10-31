# 任意波形发生器上位机控制(AC820 + ACM2108)设计文档

- 日期:2026-08-12
- 板卡:芯路恒 AC820(xc7z020clg484-2,Zynq-7020)
- 模块:小梅哥 ACM2108(双通道 8bit DAC MS9708,插 GPIO0)
- 工具:Vivado 2018.3 + SDK + `cli-anything-vivado` CLI + Python 3(PySide6)
- 通信:CH9102F USB-UART(PS MIO48/49,115200,8N1)→ PS 裸机桥 → AXI GPIO → PL
- 架构演进:纯 PL 按键控制 → **PS+PL:上位机(PC)全权控制双通道波形/频率/幅度**
- 前置:本设计建立于 `2026-08-11-awg-acm2108-design.md`(DDS/偏移二进制/上电复位等 PL 经验全部沿用)

## 1. 目标

用 PC 上位机框架替代板载按键/拨码,通过串口控制信号发生器:

- 双通道(DAC0/DAC1)**独立**设置波形/频率/幅度
- 4 波形(正弦/方波/三角/锯齿),频率 10Hz~1MHz,幅度 0~100%
- 上位机可回读板卡当前状态;界面含理论波形预览
- 板载按键(S1/S2)与拨码(SW0~1)不再参与控制
- 上电不连上位机也有默认输出(PS 桥自动下发默认配置)

## 2. 硬件上下文(通信通道论证)

| 资源 | 结论 |
|------|------|
| CH9102F USB-UART | 接 **PS_MIO48/49**,唯一板载 USB 串口,Type-C 一线供电+下载+串口 |
| PL 侧串口 | 官方 PL UART 例程走 GPIO0 的 U12/U11,已被 ACM2108 占满,不可用 |
| PL 以太网(ENET1) | 纯 PL 可行但需 RTL 实现 UDP/ARP,对控制命令过重 |
| GPIO0(40pin) | 36 路 IO 全部被 ACM2108(DAC0/1 9pin + ADC0/1 9pin)占用 |

**结论**:唯一现实通道 = PS UART(MIO48/49),PS 侧运行裸机桥程序,经 AXI GPIO 把参数写入 PL。

## 3. 总体架构

```
PC (Python + PySide6)                        AC820 板
┌─────────────────────┐  USB Type-C   ┌────────────────────────────────────────┐
│ 上位机框架           │──────────────▶│ CH9102F ─▶ PS UART (MIO48/49, 115200) │
│ 三栏仪表式界面       │               │   PS 裸机桥: UART轮询→行解析→命令处理   │
└─────────────────────┘               │   (SDK 小程序, ~200 行 C)              │
                                      │        │ AXI GPIO (2 × 32bit, 只写)    │
                                      │        ▼                               │
                                      │ PL: reg_bridge(2-FF 同步/拆包)          │
                                      │      ├─▶ dds_core CH1 ─▶ 幅度缩放 ─┐    │
                                      │      └─▶ dds_core CH2 ─▶ 幅度缩放 ─┼─▶ dac_driver ─▶ ACM2108 DAC0/DAC1
                                      └────────────────────────────────────────┘
```

- 全系统时钟:PL 50MHz(Y9);PS FCLK0 = 50MHz 供 AXI 互联与 AXI GPIO 时钟
- 数据流向:上位机 SET → 串口帧 → PS 解析写 AXI GPIO → PL 同步拆包 → 双 DDS → 幅度缩放 → DAC
- 回读:`GET` 由 PS 桥内存状态直接应答,不读 PL

## 4. PS 侧设计

### 4.1 Block Design 组成

| 组件 | 配置 |
|------|------|
| PS7 | UART1(MIO48/49,115200,8N1);FCLK0=50MHz;M_AXI_GP0 使能 |
| AXI GPIO | 2 通道 × 32bit,全部输出;C_GPIO_0/1_WIDTH=32,C_ALL_OUTPUTS=1 |
| AXI Interconnect | 自动,1 master 1 slave |

### 4.2 寄存器打包(AXI GPIO 通道)

| 位 | 字段 | 说明 |
|----|------|------|
| [19:0] | freq_hz | 频率 10~1,000,000(20bit 足够) |
| [27:20] | ampl_pct | 幅度 0~100% |
| [29:28] | wave | 0=SINE 1=SQUARE 2=TRIANGLE 3=SAW |
| [31:30] | — | 置 0 |

- CH1 打包进 GPIO 通道 0,CH2 进通道 1;**一路参数一次 32bit 写入,原子生效**,无中间态
- 写入仅由 PS 完成(全输出),PL 只读

### 4.3 裸机桥程序(Standalone BSP)

```
main(): 初始化 UART1(轮询) + AXI GPIO;写默认配置;进入主循环(轮询收帧+超时)
UART 轮询:逐字节读入缓冲 → 按 \r\n 组帧 → 解析执行
命令处理:解析 → 校验 → 写 XGpio → 回 ACK;未知/非法 → ERR
状态表:struct ch_state { u32 wave; u32 freq_hz; u32 ampl_pct; } ch[2];(由写路径维护)
```

- **上电默认**:main 里直接写 GPIO 通道(不经串口)= 正弦 1kHz 幅度 50%(双通道相同)
- 帧超时:500ms 无 `\r\n` 丢弃并回 `ERR TIMEOUT`
- UART 溢出/帧错误:清标志继续,不影响后续

### 4.4 波特率与线缆

115200,8N1,无校验。Type-C 单线(供电 + 下载 + 串口)。

## 5. 通信协议(ASCII 文本行)

命令以 `\r\n` 结尾,大小写不敏感,响应全大写。

| 命令 | 说明 | 响应 |
|------|------|------|
| `SET CH1 WAVE SINE` | 波形 SINE/SQUARE/TRIANGLE/SAW | `OK` / `ERR <msg>` |
| `SET CH1 FREQ 100000` | 频率 10~1000000 | `OK` / `ERR FREQ OUT OF RANGE` |
| `SET CH1 AMPL 80` | 幅度 0~100 | `OK` / `ERR AMPL OUT OF RANGE` |
| `GET CH1` | 查询 | `CH1 WAVE SINE FREQ 100000 AMPL 80` |
| `GET CH2` | 查询 | `CH2 WAVE ...` |

- 通道名:`CH1` / `CH2`;未知通道 → `ERR BAD CHANNEL`
- 未知命令 → `ERR UNKNOWN CMD`;字段名错误 → `ERR BAD FIELD`
- 非法参数一律拒绝并 ERR,**不执行、不写寄存器**
- 响应始终一行,`\r\n` 结尾

## 6. PL 侧改造

### 6.1 模块清单

| 文件 | 变更 | 职责 |
|------|------|------|
| `awg_top.v` | 改 | 移除 key_ctrl/S1/S2/SW0~1;新增 AXI GPIO 输入口;例化 reg_bridge + 双 dds_core;LED 显示 CH1 参数 |
| `reg_bridge.v` | 新建 | 2-FF 同步 gpio0/1 → 拆包 → ch1/ch2 {freq, ampl, wave};防御:freq<10 钳到 10 |
| `dds_core.v` | 保持单通道模块 | 原样复用,顶层例化 2 份(每通道独立 fword 累加器) |
| `ampl_scale.v` | 新建(独立模块) | 幅度缩放:signed sample × ampl ÷ 100(常数除法近似,误差 <1 LSB) |
| `dac_driver.v` | 改 | 双通道各自 sample→偏移二进制(^0x80)+ DACLK |
| `sine_rom.v` | 不变 | 256×8 |
| `key_ctrl.v` | 删除 | 按键控制废弃 |

### 6.2 数据流与细节

- **同步**:AXI GPIO 输出在 FCLK0 域;PL 侧每个 bit 过 2-FF 同步器(参数为静态值,仅随命令变化,无 CDC 风险)
- **幅度缩放**:0%→0(偏移二进制中点 0x80);100%→满幅。公式 `out = sample * ampl / 100`(sample 为 ±127 补码)
- **LED**:LED0~1 = CH1 波形编号,LED2~7 = CH1 频率粗刻度(freq_khz[9:4])——沿用原约定,仅数据源改为寄存器
- **上电复位**:沿用 10.5ms 计数器;复位期间 AXI GPIO 读数为 0 → reg_bridge 输出默认(正弦 1kHz 50%),与 PS 上电默认一致,双保险
- **偏移二进制**:dac_driver 内 `sample ^ 8'h80` 转换保留(ACM2108 关键坑,见前 spec)

## 7. 上位机框架(Python + PySide6)

### 7.1 目录结构

```
host/
├── main.py                  # 入口
├── requirements.txt         # PySide6, pyqtgraph, pyserial
├── siggen/
│   ├── protocol.py          # 命令构造 + 响应解析(纯函数,可单测)
│   ├── transport.py         # pyserial 封装:后台读线程、断线检测、超时、命令队列
│   ├── controller.py        # 状态模型(QObject):通道参数、连接状态、信号槽
│   ├── preview.py           # pyqtgraph 理论波形预览(跟随参数实时刷新)
│   └── ui/
│       ├── main_window.py   # 三栏布局
│       └── widgets.py       # 连接栏 / 通道面板 / 状态面板组件
└── README.md
```

### 7.2 三栏界面(方案 C,已确认)

| 栏 | 内容 |
|----|------|
| 左 | 串口端口下拉、波特率(默认 115200)、连接/断开、状态灯、通道选择(CH1/CH2) |
| 中 | 波形 4 按钮(选中高亮)、频率大数字、步进按钮(±10Hz、×10/÷10)、直接输入框、幅度滑块(0~100%)+数值 |
| 右 | 状态回读(CH1/CH2 当前参数)、波形预览(pyqtgraph,上方)、日志(下方,ERR 红色) |

### 7.3 交互逻辑

- 控件操作 → controller 校验(频率 10~1MHz、幅度 0~100、波形枚举)→ 发 `SET` → 收 `OK` → 更新界面
- 连接成功后自动发 `GET CH1`/`GET CH2` 同步板卡状态
- 波形预览:按当前参数画 2~3 个周期的理论波形(正弦/方波/三角/锯齿,幅度按 % 归一)
- 日志记录每次下发/回显;`ERR` 红色;断线提示可重连

## 8. 错误处理

| 层 | 处理 |
|----|------|
| 上位机 | 串口断开→状态灯灰+日志提示,不崩溃可重连;命令 1s 无响应→重发 1 次→仍失败提示"板卡无响应" |
| PS 桥 | 非法参数拒绝不执行;未知命令 ERR;UART 溢出/帧错清标志继续;帧超时 ERR TIMEOUT |
| PL | freq<10 钳位防御;参数恒来自寄存器,无外部错误面 |

## 9. 测试

| 项 | 方法 |
|----|------|
| protocol.py | pytest:命令生成、响应解析、ERR 解析、越界值、大小写 |
| PS 桥 | SDK 编译上板,Python(pyserial)脚本自动发全命令集比对回显(含非法参数/未知命令) |
| reg_bridge | TB:拆包正确性、双通道独立、freq 钳位、2-FF 同步行为 |
| dds_core ×2 + ampl_scale | TB:双通道独立 fword、幅度 0/50/100% 边界、输出与理论逐点比对 |
| 全链路 | GUI 手动 + 示波器:DAC0/DAC1 波形/频率/幅度与界面一致;GET 回读一致 |

## 10. 交付物与流程

```
<LOCAL_PATH>
├── docs/superpowers/specs/2026-08-12-awg-host-control-design.md   (本文件)
├── host/                      Python 上位机框架
├── src/rtl/                   awg_top.v reg_bridge.v dds_core.v ampl_scale.v dac_driver.v sine_rom.v
├── src/constraints/           awg.xdc(移除 SW,新增 GPIO0 输出不变)
├── src/tb/                    reg_bridge_tb.v dds_dual_tb.v awg_top_tb.v(改造)
├── scripts/                   create_bd.tcl(block design 生成)、build_sdk 脚本
└── README.md(更新)
```

构建流程:
1. `cli-anything-vivado` + TCL 脚本创建 BD(PS7 + AXI GPIO + 互联)并接线
2. 综合/实现/比特流(现有 CLI 流程)
3. `xsdk -batch` 编译裸机桥 app(导出 hdf 后)
4. 下载:JTAG 下比特流 + SDK 运行 app(XSCT/程序下载)
5. 上位机:Python 虚拟环境装 PySide6/pyqtgraph/pyserial,`python main.py`

## 11. 风险与待办

- **AXI GPIO 通道宽度**:Vivado 2018.3 的 AXI GPIO v2.0 需确认 2×32bit 全输出可行(不行则改用 4×16bit 或两个 IP)
- **cli-anything-vivado 对 TCL/BD 的支持**:需验证能否执行自定义 tcl 创建 BD;不行则 GUI 手动建一次并导出 bd.tcl 固化到 scripts/
- **xsdk -batch 命令行**:Windows 下命令行建 app/编译/下载的脚本化需验证;失败则文档化 GUI 步骤
- **FCLK0 与 Y9 相位无关**:2-FF 同步依赖"参数为准静态值"假设,成立(仅命令触发变化)
- **上电默认值**:PS 桥 main 写默认 + PL 复位默认双保险,数值暂定 1kHz/50%/正弦,可调
- **幅度缩放精度**:×ampl÷100 用常数除法(乘 2^20/100 取整后右移),误差 <1 LSB,8bit DAC 无感知
