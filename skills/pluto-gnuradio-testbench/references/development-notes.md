# GNU Radio / Pluto 已验证经验

## USB 生命周期

同一 USB 接口由 Python `iio.Context` 持有时，GNU Radio 再开会报 `Unable to create context`，可能附带误导性的 `Unable to find host`。最小实验确认先销毁 Python Context 后 GNU Radio Source 可创建。

采用：串号发现与身份快照→释放 Python Context 及 device/channel 引用→GNU Radio 持有→停止并销毁图/块→重新打开 Python Context 收尾。异常 traceback 可保留局部 USB 对象，所以 `run_graph()` 内捕获异常、转文本返回，离开局部作用域后由上层报告错误并重新获取 USB；仅调用 `gc.collect()` 不能释放仍被引用的对象。

不将 `usb:4.9.5` 等临时 URI 固化。按串号发现；`hw_serial` 再核对。设备重新枚举后重新发现。GNU Radio 同一流程内的 native Source/Sink 管理由驱动负责；不要额外持有 Python Context 试图边运行边读它。

## 本版本 API

GRC模板：`<LOCAL_PATH>`。

```python
from gnuradio import iio
sink = iio.fmcomms2_sink_fc32(uri, [True, True], 32768, False)
source = iio.fmcomms2_source_fc32(uri, [True, True], 32768)
```

没有 `iio.pluto_sink`。`[True, True]` 是一条复数通道的I/Q扫描使能。Sink支持 `set_bandwidth`、`set_frequency`、`set_samplerate`、`set_attenuation(0, positive_db)`。Source 的增益用 `set_gain_mode(0, 'manual')` / `set_gain(0, gain)`；没有 `set_bandwidth`，先通过原生 PHY 的 RX `rf_bandwidth` 写入，再释放上下文。自动滤波配置为 `set_filter_params('Auto', '', 0, 0)`。

底层 TX `hardwaregain=-89.75` 等价最大衰减；`altvoltage1` output的 `powerdown=1` 关闭 TX LO。每次收尾读回，记录实际采样率、带宽与增益；请求值不能替代读回。

## Python / GRC

Radioconda 与系统 Python 包不同。用绝对解释器路径，以免改 PATH 后 PyVISA 导入失败。硬件流不要额外 Throttle；有限离线 Head→File Sink 无需按真实时间节流。`tone_offline.grc` 自然生成262144点后结束，输出原始无头 complex64 文件；`radio_test.py` 用 `.npy` 保存带格式头数组。

GRC编译可用：

```powershell
$env:PATH='<LOCAL_PATH>;<LOCAL_PATH>;'+$env:PATH
& <LOCAL_PATH> -B <LOCAL_PATH> -o sdr_testbench\generated sdr_testbench\tone_offline.grc
```

自动生成代码不要手改加入收尾，否则下次生成会覆盖；限时硬件控制维护在手写 `radio_test.py`。

## 证据层次与退出

- 每次输出独立时间戳目录，不覆盖旧IQ或失败收据；可用 `index_results.py` 重建结果索引。
- `RX_STREAM_PASS` 连背景噪声也可通过，只证明数据流。
- `loopback` 的 ±2kHz峰值位置/20dB峰值中位数比是初筛，不是绝对校准/完整验收；至少补 TX关/开、改变频率的对照。
- 单机Pluto自回环共享时钟，不能证明独立绝对频率。
- `rms_iq` 是complex float归一化幅度。硬件衰减dB和数字幅度不等于绝对dBm，不能由未经校准的软件值推出实测功率。
- 示波器频率 `+99E+36` 是无效值；70/100MHz示波器看不到434MHz不能归因SDR坏。`during` 串行查询和发射后截图不算同步RF证据。
- 软件时限是正常/可处理异常路径的运行预算，不是硬件级关断保证。驱动/USB卡死时停止再次发射，说明需要现场断开供电；不得把超时强杀等同于停止读回成功。

## 内部衰减直连（2026-10-19补充）

无外置衰减器时可评估内部TX硬件衰减控制的同轴直连，不预设必须采购器材。[ADI CN0534 Pluto自回环示例](https://wiki.analog.com/resources/eval/user-guides/circuits-from-the-lab/cn0534)采用降低发射电平后的直接连接。[ADI给出的Pluto输入上限](https://wiki.analog.com/resources/eval/user-guides/circuits-from-the-lab/cn0551)为+2.5dBm。+8dBm参考TX功率减46dB约为-38dBm仅是量级估计，不是全频段严格上界、现场功率测量或启动瞬态证明。

建议Pluto从60dB TX衰减、幅度≤0.25、固定低RX增益和2秒开始；板卡在既有域沿用46dB，不能改冻结门。优先Pluto自回环，再做跨设备方向。首先在未接接收输入时核对设备/启动路径、设置实际衰减并读回；核对GR构造、滤波、调频不会恢复高功率，之后再连接并短时输出，最终关断和读回。任何重启、复位或重新初始化都重新确认，不依赖上一次参数仍生效。低RX增益不能保护RF输入免受过功率。

项目当前`rf_core_init()`在vendor初始化前会用请求衰减覆盖结构默认值，不能只看到模板的0dB就断言启动必有满功率。另一方面，当前Pluto脚本未完成独立直连路径验证，没有专门的“启用RF前实际衰减读回”记录；补齐时序/测试后再执行，不能把本参考当作已有APPROVED或实测通过。无需增加无关防护或重复全审。

## 更新快照

维护源为项目 `sdr_testbench/`。更新技能时只同步工具、默认配置和离线测试，不打包 `results/`、生成缓存或原始IQ。保存 `assets/testbench/SNAPSHOT.json` 的源文件SHA256，重跑技能校验、模板准备及离线测试；实际新RF实验按任务需要执行，不能为了“验证技能安装”自动再发射。
