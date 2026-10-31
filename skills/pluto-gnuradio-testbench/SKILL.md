---
name: pluto-gnuradio-testbench
description: "通过 GNU Radio/gr-iio 控制 ADALM-Pluto，执行基础接收、限时单音发射与有线回环，使用 PyVISA 控制 Keysight 示波器并保存证据。适用于 PlutoSDR 测试、Radioconda/USB IIO 排障及本项目外接 SDR 复用；其他型号 SDR 需另行核对驱动，不将本技能参数直接套用。"
---

# PlutoSDR / GNU Radio 基础测试

复用已实测的 Windows PlutoSDR/GNU Radio 流程；先明确设备、接线与测量能力，再运行当前任务需要的测试，保存原始结果及停止读回。

## 入口与资源

- 本项目权威实现：项目根目录的 `sdr_testbench/`。原工程为 `<LOCAL_PATH>`；以当前工作区实际位置优先。
- 本机设备、解释器及既有结果：[references/local-setup.md](references/local-setup.md)。识别设备或复现实测时读。
- USB 独占、gr-iio API、测量判据：[references/development-notes.md](references/development-notes.md)。开发/排障/解释测试结果时读。
- `assets/testbench/` 是可移植的软件快照，包含源代码、配置、GRC 和离线测试，不包含历史 IQ 或成功收据。优先使用项目中的维护版本；源目录缺失时用 `scripts/prepare_testbench.py --destination <工作区>/sdr_testbench` 复制模板。该命令不覆盖已有文件、不连接硬件。

## 执行流程

1. 核对当前任务是否请求发射、接收或仅诊断。加载技能不代表可以额外发射、修改固件或操作 FPGA。沿用本会话已明确的授权与接线；现场信息不明时仅询问缺失的接线信息，同时继续离线准备。
2. 发现设备并按串号读回身份。**COM11 是 FPGA 板卡，不能当成 SDR 或示波器；本流程不打开串口。** Pluto 使用 USB IIO，示波器用明确 VISA 资源并检查 `*IDN?`。USB URI 可变化，不硬编码总线编号。设备变化时更新工作副本配置，不能直接驱动未知设备。
3. 使用明确解释器：GNU Radio 在 Radioconda，PyVISA 在系统 Python。先 `probe` 与离线信号检查，再执行所需硬件步骤。沙箱中 USB 列表为空时区分访问权限与真实未接设备，不删除身份校验。
4. TX 前核对实际端口、频率范围、测量带宽及端接。标准 Pluto 覆盖 325–3800 MHz；本机 DSO-X1102A 为 70/100 MHz，当前组合不能可靠验收 RF 载波。100 kHz 基带经 433.9 MHz LO 后是 434 MHz，不是示波器可直接测的 100 kHz 输出。不要为迁就带宽自动修改 Pluto 固件。
5. 本软件默认 2 秒、幅度 0.25、TX 衰减 40 dB，使用非 cyclic GNU Radio Sink。运行后停止/释放图，再打开 IIO 写最大衰减和 TX LO `powerdown=1` 并读回。USB 上下文不能同时由 Python libiio 与 GNU Radio 持有；具体次序见开发参考。
6. 读 `result.json`、保留原始 IQ/示波器输出并准确报告证据层次。若收尾失败或子进程挂起，停止进一步发射，报告 `CLEANUP_FAILED` / `cleanup_required`；强杀、驱动挂死不能保证 finally 关断，必要时由操作者断开 Pluto USB/电源恢复。

## 常用命令

以下从**包含 `sdr_testbench/` 的工作区根目录**执行，默认不会发射：

```powershell
.\sdr_testbench\run.ps1 probe
.\sdr_testbench\run.ps1 offline
.\sdr_testbench\run.ps1 rx
& '<LOCAL_PATH>' -X utf8 -B -m unittest discover -s sdr_testbench -p test_radio_test.py -v
```

当前请求已包含 TX、实际确认接线为 TX→Keysight CH1 时：

```powershell
& '<LOCAL_PATH>' -X utf8 -B sdr_testbench\scope_test.py --transmit --path-confirmed '<填写本次真实接线>'
```

只查看示波器：同命令去掉 `--transmit` 和 `--path-confirmed`。`scope_test.py` 的资源号和设备身份检查是本机专用；更换示波器须先适配 SCPI，不能直接移除校验。

外置衰减器是优选路线，不应把没有该器件当成一律不能测试。Pluto TX→外置30–40dB/50Ω衰减器→Pluto RX时，有明确任务授权和实际接线后可使用 `run.ps1 loopback --path-confirmed '<本次真实接线>'`。回环模式已实现，**截至2026-10-19未做实机回环验证**。

仅有同轴线时，先阅读开发参考的“内部衰减直连”节，核对真实额定输入、TX硬件衰减生效及读回、启动/复位与GR建图时序，再准备受控直连流程。不能直接把现有loopback命令当作已验证的直连入口；先准备、确认RF关闭，再给操作者换线端口。外置衰减路线与内部衰减路线都不从数字幅度推算已校准dBm。

## 结果判定

| 状态或证据 | 可以说明 | 仍不能说明 |
|---|---|---|
| `DEVICE_IDENTIFIED` | 控制接口与身份可读 | RF 收发正常 |
| `OFFLINE_PASS` | 软件信号生成/分析可用 | 硬件链路正常 |
| `RX_STREAM_PASS` | 获得有效非零 IQ | 灵敏度、频响、目标信号存在 |
| `TX_STREAM_COMPLETED_RF_UNVERIFIED` | TX 数据流正常结束 | RF 实际输出/功率正确 |
| `TONE_OBSERVED_NOT_CALIBRATED` | RX 观察到符合初筛的峰值 | 校准、完整验收、独立频率准确度 |
| `powerdown=1` 读回 | 本次结束时 TX LO 关闭 | 所有异常均能自动停止 |

回环进一步使用 TX 关/开、改变基带频率后峰值跟随作为对照，排除背景与串扰。不要拿一次峰值或超带宽示波器平线作通过/故障结论。`rms_iq` 是归一化 IQ，不能称为 ADC 计数、伏特或 dBm。示波器 `+99E+36` 是无效测量；三次串行 `during` 查询不保证都落在短 TX 窗内。

报告给出实际执行项目、结果目录、停止状态和仍缺少的验证；不把历史接线、2026-10-19 结果或旧审查当作新一次测试已执行。此技能不引入重复审查；修改硬件控制时遵守当前项目的一次适用增量审查规则。
