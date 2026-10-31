# 未来 GNU Radio / Pluto 发射链接口说明（不执行设备操作）

> 本文只说明如何把 `data/synthetic_6msps/*.iq` 接入**未来**的 Pluto/GNU Radio 发射链。
> 本任务没有 GNU Radio、Pluto、板卡、网络或 RF 操作；也不承诺该链路已实测。
> `6 MSPS` 是发射表示；`60 MSPS` 是冻结模型接收域，**不能**把 60 MSPS 文件送 Pluto。

## 1. 文件接口

| 项 | 值 |
|---|---|
| 文件 | `data/synthetic_6msps/<BPSK/QPSK/QAM16/QAM64>.iq` |
| 类型 | interleaved signed int16 little-endian, `I,Q` |
| 采样率 | **6,000,000 complex samples/s** |
| 样本数 | 254 complex samples（127 symbols × 2 sps，RECT，无差分） |
| 字节数 | 1,016 |
| 幅度 | component peak = 1800（12-bit 码值）；约 0.879 满量程 |
| 循环 | 文件为一个 127-symbol PRBS 周期；发射侧可循环播放形成连续信号 |
| 真值 | 文件名/manifest 中的 `truth_class`；不是模型预测 |

不要对文件再做 RRC、重采样或差分编码；它已经是 3 MSym/s、RECT、sps=2 的基带码值。

## 2. 推荐接法（GNU Radio 流图接口）

```
File Source (type=short, repeat=true, path=.../BPSK.iq)
    -> Interleaved Short to Complex
    -> Multiply Const (1/2048)          # 12-bit code -> normalized [-1,1)
    -> PlutoSink (sample_rate=6e6, ...)
```

要点：

- `File Source` 按 **short** 读取；`Interleaved Short to Complex` 将相邻 I/Q 组成 complex。
- 归一化使用 `1/2048`（12-bit 码约定），不要用 `1/1800` 再放大；文件峰值 1800 只是幅度余量，
  不是满量程，避免额外增益改变现有时序/功率预算。
- `PlutoSink` 的 sample rate 必须是 **6 MSPS**；中心频率、RF 带宽、衰减沿用主项目当前
  Pluto 发射配置（不由本目录设定或修改）。
- 不要接 60 MSPS 重采样/插值来“匹配板卡”；板卡接收侧是 60 MSPS 采样，发射侧仍是 6 MSPS；
  由链路/接收窗口决定，不是文件重采样。
- 循环播放时 254 samples 周期为 42.33 µs；若接收窗为 4096@60 MSPS（68.27 µs），捕获窗会
  覆盖约 1.61 个发射周期。这正是 `60MSPS` 离线窗口的构造方式（10× hold，重复 2 周期取
  4096），不要另生成符号。

若现有流图只接受 `complex64` 文件，可离线转换（仅格式转换，不操作设备）：

```python
import numpy as np
src = np.fromfile("data/synthetic_6msps/BPSK.iq", dtype="<i2").reshape(-1, 2)
x = (src.astype(np.float32) / 2048.0)
x = (x[:, 0] + 1j * x[:, 1]).astype(np.complex64)
x.tofile("BPSK.complex64")
```

## 3. 不要做的混用

- 不把 `data/synthetic_60msps/*.iq` 送 Pluto（4096 samples 在 6 MSPS 下是 682.7 µs，且
  不是发射端文件定义）。
- 不把 6 MSPS 文件直接当冻结模型输入（模型域是 60 MSPS/4096/decim1）；模型离线检查只使用
  `data/synthetic_60msps/`。
- 不修改既有 GNU Radio/Pluto 发射配置、产品 profile 或 RF 预算来“适配”本文件；接口接入由
  主项目未来授权执行。

## 4. 验证边界

本接口说明没有在 GNU Radio/Pluto 上运行过，不构成发射/链路验收。未来首次使用时至少检查：
文件按 short 读取、样本计数 254、6 MSPS 播放、循环边界、输出衰减和接收端 60 MSPS 捕获窗口；
任何设备动作需主项目另行授权。
