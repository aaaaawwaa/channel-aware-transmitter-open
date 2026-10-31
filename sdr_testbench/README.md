# 外接 PlutoSDR / GNU Radio 基础测试台

本目录独立于 FPGA 的 AWG/PYNQ/RF 发布流水线。**不使用任何 COM 口，不控制 FPGA；COM11 是 FPGA 板卡，禁止当作 SDR 或示波器。**

## 本次结果（2026-10-19）

已经真实执行设备识别、GNU Radio 离线单音、USB 接收采样、2 秒发射及示波器查询/截图。Pluto 控制及 IQ 数据流可用，发射后 TX LO 关闭读回成立。**尚不能确认射频输出是否正常**：当前示波器带宽不足，未完成有线 RF 回环。详见 [TEST-REPORT.md](TEST-REPORT.md)。

| 设备 | 本机识别结果 | 控制方式 |
|---|---|---|
| SDR | Analog Devices PlutoSDR Rev.B，AD9363A，序列号 `<DEVICE_SERIAL>` | libiio USB + GNU Radio gr-iio |
| 示波器 | Keysight DSO-X 1102A，`<DEVICE_SERIAL>` | Keysight VISA / USBTMC / SCPI |
| FPGA | COM11（本次不访问） | 不属于本测试台 |

用户确认接线为 **Pluto TX → 示波器 RX1（按 CH1）**；无外置衰减器，选择保留该接线完成能做的测试。Pluto 的 COM16 是串口控制台，本流程也不打开。

## 文件导航

### 可复用技能

已封装为 `pluto-gnuradio-testbench`，项目维护副本位于 [`../skills/pluto-gnuradio-testbench/SKILL.md`](../skills/pluto-gnuradio-testbench/SKILL.md)，个人安装位于 `<LOCAL_PATH>

```text
使用 $pluto-gnuradio-testbench 检查当前 SDR 环境，并按实际接线完成基础测试，保存测试记录和停止状态。
```

技能包括独立模板、USB/API 排障与设备配置参考；优先复用本目录的维护版本。安装/封装技能本身不重新发射，也不把历史结果当作新测试通过。

| 文件 | 用途 |
|---|---|
| `radio_test.py` | 手写、可限时运行的 GNU Radio Python 流图；probe/offline/rx/tx/loopback |
| `config/pluto.json` | 设备串号、频率、采样率、增益、持续时间与样本数 |
| `run.ps1` | 为 Radioconda 配置当前进程 DLL/PATH，调用 radio_test.py |
| `scope_test.py` | 系统 Python + PyVISA 查询真实示波器，协调 TX、记录观测与截图 |
| `tone_offline.grc` | 可在 GNU Radio Companion 打开的有限样本离线流图，不发射 |
| `test_radio_test.py` | 配置边界、频谱频率/幅度、无效 IQ 等 5 项离线测试 |
| `GNU-RADIO-DEVELOPMENT.md` | 从流图、驱动 API 到异常停止的开发经验 |
| `REVIEW.md` | 本次硬件控制代码的一次独立增量审查 |
| `BOARD-INTEGRATION-PLAN.md` | DSH当前进度核对及板卡↔Pluto两方向联调设计 |
| `DSH-NEXT-PROMPT.txt` | 可复制给DSH的后续工作指令 |
| `results/` | 每次运行独立目录：原始 result.json、IQ、示波器截图与日志 |

## 本机环境与依赖

- Windows；Radioconda：`<LOCAL_PATH>`，GNU Radio **3.10.12.0**、Python 3.12、gr-iio、libiio 0.26、NumPy 已安装。
- 系统 Python：`<LOCAL_PATH>`，已安装 PyVISA/NumPy；VISA 后端为 `<LOCAL_PATH>`。
- Pluto 固件自报 `v0.39-dirty`。本次没有修改固件、扩频配置或网络配置，没有安装新软件。
- 两套 Python 分工不同。Radioconda 不含 PyVISA；系统 Python 不含 GNU Radio。不能因为 PATH 改变而直接调用模糊的 `python`。
- 设备访问需在有 USB/VISA 权限的终端运行。受限代理沙箱的 USB 枚举曾返回空，提升设备访问权限后可发现；空列表不等于未接设备。

## 可复现命令

在工程根目录运行。先 probe/offline，再 RX；这些命令不会建立 TX 数据流。

```powershell
.\sdr_testbench\run.ps1 probe
.\sdr_testbench\run.ps1 offline
.\sdr_testbench\run.ps1 rx
& '<LOCAL_PATH>' -X utf8 -B -m unittest discover -s sdr_testbench -p test_radio_test.py -v
```

在**实际接线仍为 Pluto TX → 示波器 CH1**时，复现实测 2 秒发射与示波器观测：

```powershell
& '<LOCAL_PATH>' -X utf8 -B sdr_testbench\scope_test.py --transmit --path-confirmed 'Pluto TX -> Keysight CH1; actual wiring checked now'
```

单独查询示波器、保存截图（不发射）：

```powershell
& '<LOCAL_PATH>' -X utf8 -B sdr_testbench\scope_test.py
```

所有运行保留到新的时间戳目录，旧失败记录也保留。`probe` 正常返回 `DEVICE_IDENTIFIED`；`RX_STREAM_PASS` 仅证明非零 IQ 数据流，`TX_STREAM_COMPLETED_RF_UNVERIFIED` 仅证明发射流完成；不要将它们报告为射频全功能通过。

## 默认信号及停止行为

- LO **433.9 MHz**，复数基带单音 **+100 kHz**，预期 RF 为 **434.0 MHz**，不是 100 kHz 模拟输出。
- Fs **2.4 MSPS**，RF 带宽 **1.5 MHz**，幅度 **0.25**，TX 衰减 **40 dB**，RX 手动增益 **10 dB**。
- RX 保存 **262,144 个 complex64** 样本，TX 非 cyclic，正常发射窗口 **2 秒**；配置最多允许 5 秒。
- 结束时停止/释放流图，再重新取得 USB 上下文，设 TX 最大衰减 **89.75 dB**、TX LO `powerdown=1` 并读回。控制参数不恢复到原来可能发射的状态；设备留在停止发射状态。
- 本机读到实际 Fs `2399998`，与请求值相差 2 Hz，属于记录的读回值；分析当前按请求 Fs，误差约 0.83 ppm。
- 正常/可处理异常有 finally 收尾；硬件卡死、进程强杀和断电无法保证该机制执行。scope 包装器检测子进程挂起会记录 `cleanup_required`。此时现场断开 Pluto USB/供电，确认后再测试，不能直接认定已关闭。
- 40 dB 是发射硬件衰减，0.25 是数字幅度，二者均不等于外置衰减器，也不能推导未经校准的绝对 dBm。

## 为什么现有示波器不能完成 RF 验收

[Keysight 官方规格](https://www.keysight.com/zz/en/product/DSOX1102A/oscilloscope-70-100-mhz-2-analog-channels.html)给出 DSOX1102A 额定带宽 70 MHz、选件升级 100 MHz；[ADI 官方说明](https://wiki.analog.com/university/tools/pluto)给出标准 Pluto 工作范围 325–3800 MHz。两者无保证性能的重合频段。

示波器查询的 `+99E+36` 表示无法得到有效测量，不能当成超高频结果。截图中的平线/约 10 mVpp 噪声不证明 SDR 坏了，也不证明它发射正常。本次没有为了匹配示波器而修改 Pluto 扩频固件或尝试未保证的 80 MHz 发射。

本型号示波器输入为 1 MΩ；需要 50Ω 测量时应外接匹配终端，不能假设 SCPI 能切换出内置 50Ω。即使补上端接，也不能解决带宽不足。

## 后续确认实际 RF 的最短路径

**现场条件更新（2026-10-19）**：用户只有连接线，无外置衰减器。可评估内部TX硬件衰减受控的直连，先补齐启动/复位与GR建图时序检查、发射前真实衰减读回及停止读回，再由操作者换线；详情见 [BOARD-INTEGRATION-PLAN.md](BOARD-INTEGRATION-PLAN.md) 的最新修订。外置衰减器不是所有测试的硬前置，但现有loopback命令尚未完成直连实机验证，当前不要直接照抄运行。下面保留外置衰减器的可选路线。

有器材时可准备 **30–40 dB、适用于434 MHz的50Ω衰减器及同轴线**，接Pluto TX → 衰减器 → Pluto RX；首次仍维持低数字幅度、40 dB TX衰减和固定RX增益。受控直连另按上面的内部衰减方案核对启动流程；两条路线正式连接前均核对RX输入允许值与器件额定功率。

```powershell
# 仅在已完成上述实际接线后运行；本次没有执行。
.\sdr_testbench\run.ps1 loopback --path-confirmed 'Pluto TX -> 30-40 dB external attenuator -> Pluto RX; checked now'
```

`loopback` 已准备但**未做实机验证**，其自动条件为峰值偏移接近 +100 kHz（±2 kHz）及峰值/频谱中位数 >20 dB，仅记“观察到单音”。完整 RF 验收还需发射关/开、改变单音频率后峰值跟随的对照，以排除串扰/背景；再考虑幅度线性、调制与独立参考设备。Pluto 共享参考时钟的自回环不能证明绝对频率准确度。

若继续使用示波器，需要带宽足够的接收/下变频器或 RF 检波器后测包络，或换带宽 ≥500 MHz 的测量设备。当前无需重建 FPGA，也无需触发 DSH 已在运行的板端实验。
