# GNU Radio BPSK 外部信号源：流图、配置GUI与拍摄

本目录为2026-10-26新增。默认只做本地基带预览；只有在控制窗口中填写真实接线、确认板卡TX关闭并点击发射时，才调用现有Pluto限时发射脚本。本轮仅离线验证，没有连接Pluto、发射、板端采集、安装或修改固件。

## 1. 先打开可拍摄的参数与曲线窗口

在PowerShell执行：

```powershell
Set-Location '<LOCAL_PATH>'
.\sdr_testbench\gui_demo\run.ps1
```

或只检查配置，不打开GUI：

```powershell
.\sdr_testbench\gui_demo\run.ps1 --dry-run
```

打开GUI不会探测USB、打开IIO上下文或启动TX。窗口展示参数、时域I/Q、相对基带频谱、BPSK发送星座和运行日志。LO、参考种子、窗口时长、设备序号可编辑；“应用到本地预览”只更新预览并保存last_preview_profile.json。实际发射时，所选参数另外固化进runs/<时间>/profile.json。

本轮为板卡匹配的BPSK/RECT方案，采样率、符号率、幅度和衰减固定，不扩展QPSK/QAM。拍摄时可讲解这些固定参数，切勿为展示任意拖动而改变真实测试合同。

## 2. 打开GNU Radio Companion流图

```powershell
.\sdr_testbench\gui_demo\open_grc.ps1
```

也可以在GRC中打开本目录bpsk_preview.grc。

可拍摄的处理链为：

```
PRBS7 BPSK symbols → Repeat (RECT, sps=2) → Multiply Const (量化幅度)
                                              ↓
                              Throttle → Time / Frequency / Constellation
                                              ↓
                              PlutoSDR Sink [DISABLED，仅参数说明]
```

Pluto Sink在流图中明确禁用，因此GRC运行只预览；可双击该块拍摄LO、采样率、60dB衰减、非cyclic及带宽配置。**不要将禁用块截图称作已经发射，也不要直接启用它绕过限时控制。**实际RF由第3节控制窗口调用既有真实GNU Radio/iio Sink实现。

按F5生成、F6运行本地QtGUI预览；如果GRC弹出运行提示，按其提示执行。GRC参数改动只作用于该流图，不会自动改控制窗口的JSON配置；实际TX以控制窗口及当次profile.json为准。移动工程目录后用Radioconda运行make_grc.py重新生成流图中的模块路径。

## 3. 实际发射入口（本轮未运行）

此入口准备给后续已确认的外部接收测试使用，不能直接沿用目前“板卡TX1→板卡RX1”的旧接线确认。

1. 确认板卡TX和Pluto TX均关闭，依照项目现有内部硬件衰减直连流程核验设备和电平条件，再改接 **Pluto TX→板卡RX1**。板卡TX1不接此链路，示波器不接此RF链。
2. 板卡用纯RX配置，不能点“回环捕获”。旧80MHz教学模板不适用；两端LO要相同。
3. 在GUI填写实际接线说明并勾选板卡TX关闭。准备好板卡捕获后，点击“启动一次限时Pluto发射”，核对弹窗参数并确认。
4. GUI启动独立tx_worker.py，复用sdr_testbench/radio_test.py的tx-bpsk流图和结束关断。不会操纵板卡、打开COM11或自动重发。
5. “请求停止并关闭TX”采用协作取消，等待stop/wait和LO关闭读回；关闭窗口时同样等待收尾，不强杀子进程。设备调用阻塞时取消不能保证立即完成；若显示关闭未证实，不继续发射。
6. 即使窗口显示“发射流已结束”，RF正确性仍须由板卡的实际IQ/同步/BER证明。不同设备时钟的频偏、USB流间歇、RF滤波影响均不是本次GUI离线验证能够排除的。

| 参数 | 默认 |
|---|---:|
| Pluto LO |433.9MHz |
| Pluto Fs |6MSPS |
| 符号率/成形 |3MSym/s，RECT，sps=2 |
| PRBS7 |seed1，可选37/73，无差分编码 |
| 数字幅度 |请求0.1；既有发生器量化后205/2047≈0.100147 |
| Pluto内部TX衰减 |60dB |
| TX RF带宽 |1.5MHz，沿用历史配置；不是新带宽质量验收 |
| 流窗口 |最多2s；既有发射器预留0.15s调度余量，收据记录实际流时长 |
| 板卡RX |60MSPS、4096点、D1、匹配PRBS种子 |

2s限制指调制数据流窗口，不代表USB初始化耗时或全部LO上电时间；以既有stream与tx_lo_on_wall收据分别说明。不要为拍摄连续开流或更改固件扩频。

## 4. 推荐拍摄顺序

1. GRC全图：展示符号、sps、幅度、分支，明确这是发送端软件链。
2. GRC的Pluto Sink参数窗口：拍LO433.9MHz、6MSPS、TX衰减60dB及非cyclic。
3. 独立控制窗口：拍参数设置及本地I/Q、频谱和两点星座；保留“本地预览”标签。
4. 后续真实接线准备好后，再拍一次限时发射与板卡屏幕的真实接收结果；不能以第3步图形代替实测。
5. 拍结束状态：Pluto TX关闭回执、板卡RX结束。每次拍摄若涉及RF，均计入当次约定的发射/采集预算。

## 5. 文件与复现

| 文件 | 用途 |
|---|---|
|bpsk_preview.grc / make_grc.py|GRC可视流图及生成器 |
|bpsk_tx_gui.py / run.ps1|默认软件预览、参数面板、限时TX按钮 |
|profile_contract.py / pluto_bpsk.json|与既有发送器共用的样本/参数合同 |
|tx_worker.py|既有发射脚本的独立子进程和协作停止 |
|test_profile.py|PRBS样本一致性、参数拒绝、接线门与结果状态检查 |
|preview.png|本次纯软件运行的窗口截图，不是RF证据 |

```powershell
& '<LOCAL_PATH>' -X utf8 -B sdr_testbench\gui_demo\test_profile.py
# 与现有run.ps1相同的DLL环境下编译GRC：
$env:PATH = '<LOCAL_PATH>;<LOCAL_PATH>;' + $env:PATH
$env:XDG_CACHE_HOME = '<LOCAL_PATH>'
& '<LOCAL_PATH>' -X utf8 -B sdr_testbench\gui_demo\make_grc.py
& '<LOCAL_PATH>' -X utf8 -B '<LOCAL_PATH>' -o sdr_testbench\gui_demo\generated sdr_testbench\gui_demo\bpsk_preview.grc
```

启动器设置当前进程DLL搜索路径和项目内FFT缓存，不更改系统安装。Windows中文用户名下默认FFTW缓存可能引起UTF-8异常；项目内缓存已在本机预览验证。offscreen自动截图若看不到字体，设置QT_QPA_FONTDIR=<LOCAL_PATH>；正常桌面启动无需设置offscreen。
