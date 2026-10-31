# 433.9 MHz 与 +100/+200 kHz 单音：能力核对（离线，读代码）

核对对象：当前工作树的 rf_overlay/sw/board_app 与 sdr_testbench/pluto 源码/配置。**未读取板卡实时 current**；安装版本是否与工作树一致，必须由执行时的 device.info/状态 preflight 证明。本文件只回答“接口是否允许”，不回答“433.9 MHz 的模拟性能是否合格”。

## 结论矩阵

| 能力 | 能否 | 依据 | 说明 |
|---|---|---|---|
| 433.9 MHz LO | **能** | rf_daemon.c:1142-1148；rf_core.c:855-857；rf_core.c:897-898；rf_core.c:975-979 | 通用 rf.init/rf.configure 只要求 lo_hz>0；init 后回读必须等于请求值。没有 80 MHz 总门。 |
| 板卡 60 MSPS | **能** | rf_core.c:900-901；rf_core.c:982-983 | 带宽固定 30 MHz；采样率回读必须等于请求。当前 PL/FIR 按 60 MSPS 设计。 |
| 板卡 2.4 MSPS | **不能（本诊断）** | rf_core.c:900-901；rf_core.c:982-983；test 路径 | 初始化把带宽固定 30 MHz、FIR 配置按 60 MSPS；不要在本诊断改采样率。 |
| 板卡 TX 任意 1..4096 IQ | **能** | rf_daemon.c:1291-1319；rf_protocol.c:396-427；platform_rf.h:77 | tx.load 只校验样本数/字节数/格式/方向/12-bit 范围，不校验频谱。 |
| 板卡 +100 kHz 周期单音 | **能，但不是 100.000 kHz** | 60e6/4096 栅格 | 4096 点/60 MSPS 的 bin 间隔 14648.4375 Hz；bin 7 = 102.5390625 kHz。 |
| 板卡 +200 kHz 周期单音 | **能，但不是 200.000 kHz** | 同上 | bin 14 = 205.078125 kHz。 |
| 板卡精确 100.000/200.000 kHz 周期单音 | **不能（4096 点 wrap）** | 4096/60e6 | 若 wrap=true，帧必须整数周期；精确 100 kHz 不是整数 bin。wrap=false 可发单帧 68.27 µs，但频谱栅格仍有限。 |
| headroom.* 在 433.9 MHz | **不能** | rf_headroom.c:14；rf_daemon.c:2082 | headroom.begin 把 80 MHz/60 MSPS/30 MHz/46 dB 写死；capability 也要求 rx_lo=tx_lo=80 MHz。 |
| 原 S2 80 MHz 用本 Pluto | **不能** | sdr_testbench/README.md；技能 SKILL.md | 标准 Pluto 325–3800 MHz；80 MHz 在范围外。不能改固件绕过。 |
| 板卡 TX1 -> Pluto RX（A） | **接口允许** | rf_daemon.c:2275-2281；rf_state.c:89-99 | tx.start 已启用；TX/RX 互斥，READY 才能开始。 |
| Pluto TX -> 板卡 RX1（B） | **接口允许** | rf_daemon.c:2275-2281；rf_state.c:89-99 | rx.capture 已启用；板卡 TX 必须不启动。 |
| 物理授权/nonce | **不涉及 nonce** | rf_daemon.c:2301-2341 | daemon 用 control.arm token，不是 nonce；本工具不消耗旧实验 nonce，也不生成新 nonce。 |

## 关键代码路径

### 1) rf.init 不限制 80 MHz

- rf_daemon.c:1142-1148：读取 lo_hz，只拒绝 0，不拒绝 433.9 MHz。
- rf_daemon.c:1152-1157：attenuation_mdB 必须显式给出，避免默认 0 dB。
- rf_core.c:855-857：只拒绝 lo_hz=0/sample_rate=0。
- rf_core.c:897-899：把请求 LO 和 46 dB 写进 init_param，再调用 vendor init。
- rf_core.c:964-968：vendor init 后重新写 attenuation（先）和 LO（后）。
- rf_core.c:975-988：回读 rx_lo/tx_lo/sample_rate/attenuation/带宽/FIR 配置，任何不等即 HARDWARE_IO。
- 结论：如果 433.9 MHz 不在器件/模拟前端能力内，失败会以 init 回读失败出现；不是被 80 MHz 常量挡住。

### 2) rf.configure 不限制 80 MHz

- rf_daemon.c:1204-1222：lo_hz/sample_rate/带宽/attenuation 都显式解析；attenuation 上限 89750。
- rf_core.c:1103-1106：先写 RX/TX LO，再写 TX1/TX2 attenuation。
- rf_core.c:1142：回读 attenuation 必须等于请求。
- 结论：调频顺序不会把 46 dB 重置为 0；若任何一步不等，configure 失败。

### 3) 80 MHz 只被 headroom 钉死

- rf_headroom.c:14：headroom.begin 的 args 必须是 lo_hz=80000000、sample_rate=60000000、30 MHz、attenuation_mdb=46000 等。
- rf_daemon.c:2082：headroom capability 要求 rx_lo_hz==80000000 && tx_lo_hz==80000000。
- rf_daemon.c:2103-2104：headroom 内部默认就是 80 MHz/60 MSPS/46 dB。
- 结论：433.9 MHz 诊断必须走普通 rf.init/rf.configure/tx.load/tx.start/rx.capture，**不得**调用 headroom.*。

### 4) 单音生成与 4096 点限制

- platform_rf.h:77：RF_BRAM_WORDS=4096。
- rf_daemon.c:1291-1319：tx.load 接受 1..max_samples，转换后写 TX BRAM；不检查频率内容。
- rf_protocol.c:396-427：声明只校验 samples/bytes/format/direction，长度由样本数推导。
- 60e6/4096 = 14648.4375 Hz/bin；bin 7 = 102539.0625 Hz，bin 14 = 205078.125 Hz。
- 结论：所谓“+100/+200 kHz 单音”在本诊断中应写成“标称 +100/+200 kHz，实际周期 bin 7/14”。峰值跟随判据用两者差值 102539.0625 Hz，不能用 100000 Hz 的绝对值去卡。

### 5) 状态机与物理门

- rf_state.c:89-99：除 init/stop 外，所有操作要求 READY；TX_START 与 RX_CAPTURE 互斥。
- rf_state.c:126-131：physical loopback 要求 cable_confirmed 且 attenuation 精确 46000。
- rf_daemon.c:2275-2281：tx.start、rx.capture 在启用列表中；loopback.physical.start 不在。
- 结论：A 方向用 tx.load/tx.start，不调用 physical loopback；B 方向用 rx.capture，板卡 TX 不启动。

## 需要哪个“新物理范围/授权”

1. **433.9 MHz 独立诊断域**：接口允许，但它是**新域**，不是原 S2 80 MHz 合同。需要现场物理授权（板卡 TX、Pluto TX/RX、实际接线）与执行预算；不需要新 FPGA bitstream，不需要改冻结 80 MHz 常量。
2. **板卡 46 dB**：沿用冻结门；本诊断不修改。
3. **BPSK 3 MSym/s**：板卡接收机合同固定在 60 MSPS/3 MSym/s/RECT；Pluto 需要 6 MSPS 的独立 TX 路径（当前模板 2.4 MSPS 不允许）。这需要新的 Pluto 工具/测试/一次增量审查，不是改一个配置就能声称可用。
4. **80 MHz 原 S2**：本 Pluto 不具备；需要覆盖 80 MHz 的 SDR 或下变频器。不得改 Pluto 固件、不得把 434 MHz 结果写成原 S2 通过。

## 不能从本核对推出的结论

- 不能推出 433.9 MHz 的模拟增益/频响/噪声/杂散合格；这些必须实测。
- 不能推出安装版本与工作树一致；执行前必须读 device.info/current。
- 不能推出 BPSK 6 MSPS 可用；USB2/FIR 尚未验证。
- 不能推出原 S2 80 MHz 的任何 PASS/FAIL。
- 不能推出“衰减不会在任何异常路径重置”；只覆盖了正常启动/初始化/GR 建图/调频路径，异常/强杀/断电仍可能留下未知状态，必须靠停止读回和现场断电恢复。
