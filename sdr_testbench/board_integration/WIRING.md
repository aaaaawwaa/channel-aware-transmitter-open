# 接线图与换线顺序（PlutoSDR <-> 板卡，433.9 MHz）

## 当前已确认与未确认

- **已确认**：Pluto TX -> Keysight DSO-X 1102A CH1（2026-10-19 现场）。
- **未确认**：板卡 <-> Pluto 的任何接线。
- **没有**外置衰减器；本方案用两端内部 TX 硬件衰减受控直连，不把采购衰减器当硬阻塞。
- **示波器不能验收 434 MHz**：DSOX1102A 额定 70/100 MHz，输入 1 MΩ；不要把它接回 434 MHz 链路冒充测量。
- 板卡为 AC820 + AD-FMCOMMS3-EBZ（AD9361）。RF 端口丝印：Pluto 为 TX / RX；FMCOMMS3 为 TX1 / TX2 / RX1 / RX2。本方案只用 TX1 与 RX1。

## 端口表

| 设备 | 端口 | 本方案用途 | 注意事项 |
|---|---|---|---|
| PlutoSDR Rev.B | TX（SMA） | 阶段 0 自回环；阶段 B 发射 | 只用内部 60 dB 硬件衰减；数字幅度 0.1 |
| PlutoSDR Rev.B | RX（SMA） | 阶段 0 自回环；阶段 A 接收 | 手动 0 dB；禁止 AGC |
| AD-FMCOMMS3-EBZ | TX1（SMA） | 阶段 A 发射 | 板卡 46 dB 内部衰减；数字码 60 |
| AD-FMCOMMS3-EBZ | RX1（SMA） | 阶段 B 接收 | 板卡既有 RX 增益设置，不暗改 |
| AD-FMCOMMS3-EBZ | TX2 / RX2 | 不使用 | 保持空载 |
| Keysight DSO-X 1102A | CH1 | **所有 RF 阶段都断开** | 带宽不足，不能验证 434 MHz |

## 换线顺序（严格按阶段）

### 阶段 0：Pluto TX -> Pluto RX（先证明参考 SDR）

1. 先确认板卡 TX 已关（只读 preflight），示波器 CH1 探头/线从 Pluto TX 取下。
2. 用一根 50 Ω 同轴线：**Pluto TX SMA -> Pluto RX SMA**。
3. 板卡与示波器都不接在这根线上。
4. 运行阶段 0 命令；若失败/无峰值/CLIPPED/读回失败，停止，不进入阶段 A。
5. 结束后先完成 Pluto 89.75 dB / powerdown=1 读回，再拆线。

~~~mermaid
flowchart LR
  PTX[Pluto TX SMA] -->|50 ohm coax| PRX[Pluto RX SMA]
~~~

### 阶段 A（优先）：板卡 TX1 -> Pluto RX

1. 确认阶段 0 已通过，且 Pluto TX 已关、板卡 TX 已关。
2. 把阶段 0 的同轴线从 Pluto RX 取下；**同一根线**一端接 **AD-FMCOMMS3-EBZ TX1**，另一端接 **Pluto RX**。
3. Pluto TX 空载；示波器断开；板卡 RX1 空载。
4. 运行阶段 A 命令；顺序 OFF_PRE -> TONE1（bin 7）-> TONE2（bin 14）-> OFF_POST。
5. 任何回读/削顶/峰值不跟随即停；结束后完成板卡 89750 dB + TX off 读回和 Pluto 89.75 dB/powerdown=1 读回，再拆线。

~~~mermaid
flowchart LR
  BTX[AD-FMCOMMS3 TX1] -->|50 ohm coax| PRX[Pluto RX SMA]
  PTX[Pluto TX SMA] -. open .-> X1[not connected]
  BRX[AD-FMCOMMS3 RX1] -. open .-> X2[not connected]
  SCOPE[DSOX1102A CH1] -. disconnected .-> X3[not connected]
~~~

### 阶段 B（随后）：Pluto TX -> 板卡 RX1（板卡 TX 关闭）

1. 确认阶段 A 已通过，且两端 TX 已关。
2. 把同轴线从板卡 TX1 取下；一端接 **Pluto TX**，另一端接 **AD-FMCOMMS3-EBZ RX1**。
3. 板卡 TX1 空载；示波器断开。**板卡 TX 软件必须保持关闭**：本阶段不调用 tx.load/tx.start，不调用 loopback.physical.*。
4. 运行阶段 B 命令；顺序 OFF_PRE -> TONE1（Pluto +100 kHz）-> TONE2（Pluto +200 kHz）-> OFF_POST。
5. 每次板卡 capture 前后检查 tx_source_selected=false；若为 true 立即停。
6. 结束后完成 Pluto 89.75 dB/powerdown=1 读回与板卡 disarm 读回，再拆线。

~~~mermaid
flowchart LR
  PTX[Pluto TX SMA] -->|50 ohm coax| BRX[AD-FMCOMMS3 RX1]
  BTX[AD-FMCOMMS3 TX1] -. open .-> X1[not connected]
  PRX[Pluto RX SMA] -. open .-> X2[not connected]
  SCOPE[DSOX1102A CH1] -. disconnected .-> X3[not connected]
~~~

## 禁止的接线

- **禁止**把 AD-FMCOMMS3 的 TX1 直接接到同一模块的 RX1：那是项目冻结的 physical loopback 路径，不属于本诊断；本诊断 A 方向的接收机是 Pluto。
- **禁止**把 Pluto TX 接到示波器并试图用 70/100 MHz 前端验收 434 MHz。
- **禁止**在板卡 TX 可能开启时换线；换线前必须先只读确认 TX off，或先完成安全关断读回。
- **禁止**同时把板卡 TX1 与 Pluto TX 接到同一个接收端；一次只允许一个发射源。
- 不使用 COM11；不打开任何串口。

## 接头与线缆

- 全部 50 Ω SMA；同轴线尽量短，避免过度弯折；先接设备端再上电/开流。
- 直连没有外置衰减器，安全完全依赖两端内部 TX 衰减与启动顺序；见 LEVEL-BUDGET.md 与 ATTENUATION.md。
- 若现场后来获得 30–40 dB/50 Ω 外置衰减器，应作为**可选**路线单独记录实际衰减值，不能把现有内部衰减预算直接套用。
