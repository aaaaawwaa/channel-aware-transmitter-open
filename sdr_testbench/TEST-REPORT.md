# PlutoSDR 基础实测报告

日期：2026-10-19，Asia/Shanghai。测试对象为外接 SDR 与示波器；没有打开 COM11，没有向 FPGA 部署或派发实验。

## 结论

**Pluto 设备控制、GNU Radio 离线链及 USB RX/TX 数据流程可用。射频输出是否正常仍未确认。** 已执行本次可做的测试，并按用户“无衰减器，保留示波器接线”选择停止于该边界。

| 项目 | 结果 | 证据 |
|---|---|---|
| USB 设备身份 | 通过；Pluto Rev.B / AD9363A，固定串号 | 各 probe/tx/rx 的 `result.json` |
| GNU Radio 环境 | 3.10.12.0 可实际加载 gr-iio | 各运行结果的版本字段 |
| 配置及 IQ 分析回归 | 5 项通过 | `verification.txt`、`test_radio_test.py` |
| Python 离线单音 | 通过；262144 点，峰值 100012.20703125 Hz，RMS 0.249999225 | [离线 result](results/20261019-172413-696310-offline/result.json) |
| GRC 编译与执行 | 通过；262144 个 complex64，RMS 0.249999225 | `verification.txt`、`tone_offline.grc` |
| 最终版真实 RX | RX_STREAM_PASS；262144 点，RMS 0.000416462（归一化 IQ） | [RX result](results/20261019-173443-220951-rx/result.json)，同目录 `iq.npy` |
| 2 秒 TX | TX_STREAM_COMPLETED_RF_UNVERIFIED，退出码 0 | [TX result](results/20261019-173154-376468-tx/result.json) |
| TX 停止 | `powerdown=1`，`hardwaregain=-89.750000 dB`；独立 probe 再次一致 | TX result、`final-probe-output.txt` |
| 示波器控制 | IDN/SCPI/PNG 截图成功，SCPI 队列 No error | [Scope result](results/20261019-173153-174107-scope/result.json) |
| RF 单音频率/幅度 | **未验证**；没有有效频率读数 | Scope result、截图 |
| 外置衰减器回环 | **未执行**；用户暂无衰减器 | 后续按 README 的回环方案实施 |

离线结果目录以 `results-index.json` 为准确索引；失败尝试也在其中，不能只按目录数量统计成功率。

## 实测参数与读回

发送的是复数基带 +100 kHz 单音，LO 433.9 MHz，预期载波 434.0 MHz。请求 Fs 2,400,000 S/s、带宽 1.5 MHz、幅度 0.25、TX 衰减 40 dB，运行窗口 2 秒。收尾读回 TX LO 433,900,000 Hz / 已关闭，TX 衰减 89.75 dB，实际 Fs 2,399,998 S/s。最终 RX 测试的读回频率 433,900,000 Hz、RX 带宽 1,500,000 Hz、手动增益 10 dB，TX 保持关闭。

本次没有校准绝对发射功率，不能从数字幅度/硬件衰减换算可靠 dBm。RX 采集的是未接标准源时的数据，非零 IQ 证明流可用，不能证明接收性能满足规格。

## 示波器观测

身份为 `KEYSIGHT TECHNOLOGIES,DSO-X 1102A,<DEVICE_SERIAL>,01.10.2018012838`。其标准额定带宽 70 MHz，可选升级 100 MHz，均低于本次 434 MHz。

| 查询 | 发射前 | 开始发射标记后 | 发射完成后 |
|---|---|---|---|
| 频率 | `+99E+36`，无有效结果 | 同左 | 同左 |
| Vpp | 10 mV | 10 mV | 10 mV |
| Vrms | 5.66 mV | 5.19 mV | 5.20 mV |

这些电压读数不能当成 RF 输出幅度，不适用超带宽校准；三次 during 查询也不具备严格同步保证。截图在 TX 完成后获取，显示低信号和无法测频，与记录一致。

![示波器最终截图](results/20261019-173153-174107-scope/scope.png)

## 本次遇到的问题及处理

1. 系统 PATH 未暴露 GNU Radio 命令，但 `<LOCAL_PATH>` 已装齐环境；使用该环境及明确 DLL 搜索路径即可，无需重装。
2. 首次受限进程 USB 枚举为空，记录为 ERROR；以设备访问权限重新运行成功。没有跳过身份检查或硬编码其他设备。
3. 常见示例 API `iio.pluto_sink` 不适用于本机版本；查安装块模板后使用 `fmcomms2_*_fc32`。
4. Python libiio 与 GNU Radio 同时持有 USB 导致上下文创建失败；最小实验确认释放旧 Context 后成功。现已按串号发现→释放→建图→释放→重新读回。
5. RX 源无 `set_bandwidth()`；最终版在原生 IIO 层写入 RX 带宽，实机确认读回 1.5 MHz。早期成功 RX 的带宽为旧值 18 MHz，保留记录，不用作最终配置验证。
6. 两套 Python 的包不同：为 GNU Radio 改 PATH 后模糊调用 `python` 会使 PyVISA 导入失败；改用各自绝对解释器。
7. USB URI 发生重新枚举变化，脚本按串号重发现；不把瞬时总线编号写死。
8. 真实频段/示波器带宽不匹配，保留 RF 未验证，不通过扩频、放宽判据或重复发射来制造成功。

## 保留材料和下一步

`results/` 保留原始成功与失败 JSON、接收/离线 IQ、示波器 PNG 和子进程输出；`results-index.json` 汇总目录、状态和 SHA256。`REVIEW.md` 为一次独立增量审查，实际结果以上述收据为准。

最短下一步是外接合适的 30–40 dB / 50Ω衰减器做 Pluto TX→RX 有线单音回环，并增加 TX 关闭/开启、频率改变的对照。该硬件步骤本次未完成，不影响本目录已完成的软件工具与经验交付。
