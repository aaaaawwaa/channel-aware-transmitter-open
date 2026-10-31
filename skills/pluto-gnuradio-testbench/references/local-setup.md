# 本机环境与来源（2026-10-19）

这些是历史识别结果；新任务先读取实时身份和实际接线，不默认设备仍连接。

| 项 | 值 |
|---|---|
| 项目 | `<LOCAL_PATH>` |
| 维护中的测试目录 | `<项目根目录>/sdr_testbench` |
| SDR | Analog Devices PlutoSDR Rev.B，Z7010-AD9363A |
| SDR 串号 | `<DEVICE_SERIAL>` |
| Pluto 固件 | 自报 `v0.39-dirty`，本次未修改 |
| USB | VID 0456 / PID B673，IIO 接口；USB URI 在实测中变动 |
| SDR 串口控制台 | 当时 COM16，测试不用它 |
| FPGA | **COM11，禁止作为 SDR/示波器操作** |
| 示波器 | Keysight DSO-X1102A，序列号 <DEVICE_SERIAL> |
| VISA 资源 | `<VISA_RESOURCE>` |
| GNU Radio 解释器 | `<LOCAL_PATH>`，Python 3.12，GNU Radio 3.10.12.0 |
| DLL 搜索目录 | `<LOCAL_PATH>`，需放入当前子进程 PATH |
| 示波器解释器 | `<LOCAL_PATH>`，安装 PyVISA |
| VISA 后端 | `<LOCAL_PATH>` |

可读发现使用 libiio `scan_contexts()` 和 PyVISA `list_resources()`；只对匹配身份的设备发查询。必要的 Windows PnP 枚举曾因沙箱权限不足失败，使用获得设备权限的工具执行后可读。

历史接线：用户确认 TX→示波器 RX1（按 CH1），无外置衰减器，并选择保留该接线完成可做的测试。此确认只属于原任务，新任务接线不能仅从此文推断。

实测默认：LO433.9 MHz、基带 +100 kHz、Fs2.4 MSPS、RF BW1.5 MHz、幅度0.25、TX衰减40 dB、RX手动10 dB、2秒、262144点。实际 Fs 读回2399998。软件默认限制可查 `assets/testbench/config/pluto.json` 和 `radio_test.py`；修改边界须说明实际实验需要。

原始证据留在项目 `sdr_testbench/results/` 和 `TEST-REPORT.md`：离线通过、最终 RX 非零 IQ 通过、2秒 TX 流完成、TX LO关断读回通过、示波器查询/截图通过。**RF 未验证，回环未执行**。原始失败记录和 IQ 不复制到技能中，避免把历史收据当成新运行。

本机示波器与标准 Pluto 保证频段不重合。DSOX1102A 是70 MHz（可选100 MHz），输入1 MΩ，需要50Ω测量时用外接匹配终端。标准Pluto是325–3800 MHz。不要由该设备推断项目80 MHz实验可直接沿用；应另核对参考硬件/变频方案。

详细已维护经验见项目 `sdr_testbench/GNU-RADIO-DEVELOPMENT.md`；代码和此快照有差分时，先查看差分及项目当前规则，以维护版本为准。技能快照不是候选/发布身份，也不继承项目硬件部署批准。
