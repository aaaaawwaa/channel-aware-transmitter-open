# 衰减重置核对与发射前/结束回读

问题：启动、初始化、GR 建图、调频的过程中，会不会把 TX 衰减重置为 0 dB（或任何非请求值）？

## 结论

- **板卡：正常路径不会。** init 在 vendor 初始化前就把请求值写进 init_param，初始化后又按“attenuation -> LO”的顺序重写并回读；configure 按“LO -> attenuation -> FIR”的顺序写并回读；tx.load/tx.start 只写 PL，不写 vendor 增益。任何回读不符都会让操作失败，不会静默继续。
- **Pluto：GR 建图与调频不写 hardwaregain。** 维护脚本在开 TX LO 之前先把硬件衰减压到 89.75 dB；GR 的构造函数不写增益，set_frequency 只写 LO，set_samplerate/set_filter_params('Auto') 只写采样率/FIR；set_attenuation(0, 60) 是启动前最后一条增益写。因此“启动时衰减是 60 dB”由顺序保证；但 GR 持有 USB 期间**不能**从第二个上下文读回，这一点在下面单独说明。
- **异常路径没有覆盖**：强杀、驱动挂死、断电、USB 掉线都不能保证 finally 收尾；必须靠现场断电/断线恢复，不能声称“所有异常都会自动关断”。

## 板卡：逐步依据

### rf.init

1. rf_core.c:851-857：参数校验；lo_hz/sample_rate/phy_mode 合法，attenuation 必须 present 且 ≤1000000。
2. rf_core.c:872-891：九针复位序列；此时 TX 尚未编程。
3. rf_core.c:897-899：init_param.rx/tx_synthesizer_frequency = 433.9 MHz；init_param.tx_attenuation_mdB = 46000。vendor/ad9361_api.c:221 在 ad9361_init 内把这个字段写入 phy->pdata->tx_atten，说明它不是被忽略的模板字段。
4. rf_core.c:905-914：释放 RESETB；调用 ad9361_init，vendor 初始化使用上面这两个字段（不是模板默认 0 dB）。
5. rf_core.c:956-968：vendor init 后按顺序：TX FIR config -> RX FIR config -> TX FIR enable ->（RX FIR enable 非致命）-> sampling freq -> **attenuation** -> **LO**。
6. rf_core.c:970-988：回读 product_id/phy_mode/rx_lo/tx_lo/sample_rate/attenuation/带宽/FIR 配置；attenuation 必须等于 46000，LO 必须等于 433.9 MHz，否则 HARDWARE_IO。
7. 历史运行中 init 在 46 dB 下成功，说明这条路径没有把 46 dB 重置为别的值；若被重置，statically 就会在 :981 失败。

### rf.configure（调频/重复配置）

1. rf_core.c:1034-1060：幂等检查；如果部件已经等于请求值，直接返回从部件读回的 actual，不写任何寄存器（仍然是真实回读）。
2. rf_core.c:1099-1106：需要写时按顺序写 RX/TX 带宽 -> RX/TX 采样率 -> RX/TX LO -> **TX1/TX2 attenuation** -> FIR config/enable。
3. rf_core.c:1110-1127：逐项回读；attenuation 必须等于请求；FIR 配置必须相等。
4. 结论：即使 LO/采样率/FIR 写入过程会触碰 TX 路径，attenuation 也是在它们之后写、之后读；不存在“调频完成后衰减还是旧值”的正常路径。

### tx.load / tx.start / tx.stop

- rf_core.c:1161-1176：tx.load 只写 TX BRAM 并逐字回读。
- rf_core.c:1178-1204：prepare_tx 只写 PL 的 SMP_LEN/SRC_CFG。
- rf_core.c:1207-1236：start_tx 只写 PL 的 SRC_SEL 并确认位置在动。
- rf_core.c:1238-1272：stop_tx 只清 SRC_SEL 并确认位置不动。
- 结论：这三个操作不调用任何 vendor 衰减 setter。

### 断线规则与显式收尾

- rf_daemon.c:749-816：apply_disconnect_rule 只在“TX 原本 live（tx_was_live）”时调用 rf_core_set_tx_attenuation_safe(89750)。如果 TX 已经 stop，断线不会自动把衰减抬到 89750。
- 因此结束流程必须**显式**：tx.stop -> 重新连接/arm -> rf.configure(89750) -> 检查 attenuation==89750 且 hardware_verified=true -> 检查 PL tx.source_selected=false、rx.armed=false -> control.disarm。
- rf_core.c:803-841：rf_core_set_tx_attenuation_safe 写 TX1/TX2 并分别 get 回读，两路都必须等于请求值，否则 fault；这是 89750 回读的底层依据。

## Pluto：GR 建图与调频

本机证据：

- sdr_testbench/GNU-RADIO-DEVELOPMENT.md:47：Sink 用 set_attenuation(0, 正数 dB)，底层 PHY 的 hardwaregain 读回为负数。
- <LOCAL_PATH>:56-64：公开接口是 set_bandwidth/set_frequency/set_samplerate/set_attenuation/set_filter_params；没有“启动时重置到 0 dB”的入口。
- sdr_testbench/radio_test.py:77-90：configure 顺序 = set_frequency -> set_samplerate -> set_bandwidth -> set_attenuation(60) -> set_filter_params('Auto')。
- sdr_testbench/radio_test.py:176-179：建图前先用 libiio 写 hardwaregain=-89.75，再写 TX LO powerdown=0；即 TX LO 是在最大衰减下打开的。
- sdr_testbench/radio_test.py:204-220：停止后重新打开 IIO，写 hardwaregain=-89.75 与 powerdown=1，并读回 powerdown。

上游源代码（GNU Radio maint-3.10 的 gr-iio，本机安装的是编译后的 3.10.12 绑定）：

- https://raw.githubusercontent.com/gnuradio/gnuradio/maint-3.10/gr-iio/lib/fmcomms2_sink_impl.cc
  - 构造函数：只建立 device_sink 与 underflow 线程，不写 hardwaregain。
  - set_frequency：只写 out_altvoltage1_TX_LO_frequency。
  - set_samplerate：只处理 sampling_frequency/decimation/interpolation，然后 update_dependent_params。
  - set_bandwidth：只写 out_voltage_rf_bandwidth。
  - set_attenuation：写 out_voltageN_hardwaregain = -attenuation（唯一写增益的 setter）。
  - set_filter_params('Auto')：update_dependent_params 调 ad9361_set_bb_rate。
- https://sources.debian.org/data/main/liba/libad9361-iio/0.3-2/ad9361_baseband_auto_rate.c
  - ad9361_set_bb_rate 只写 sampling_frequency、filter_fir_config、filter_fir_en；不写 hardwaregain。

结论：在 radio_test.py 的顺序下，set_attenuation(0, 60) 之后没有任何已知的 hardwaregain 写入；set_filter_params('Auto') 虽然排在 attenuation 之后，但它只动采样率/FIR。启动时目标 60 dB 在流开始前已经生效。

## 发射前/结束回读方案（本工具的硬门）

### 板卡（A 与 B 共用）

- **发射前**：rf.configure 返回 JSON 的 attenuation_mdB 必须是 46000，且 hardware_verified=true、lo_hz=433900000、sample_rate_hz=60000000、rx/tx_bandwidth=30000000、phy_mode=0、product_id & 0xF8 == 0x08；任一不合格即拒绝发射。若部件已是这些值，configure 走幂等路径但仍然返回从部件读回的 actual。
- **A 结束后**：tx.stop -> 新连接/control.arm -> rf.configure(attenuation=89750) -> 读回 attenuation_mdB==89750 且 hardware_verified=true -> PL 状态 tx.source_selected=false、rx.armed=false、io_ok=true -> control.disarm。
- **B**：不启动板卡 TX，因此没有板卡发射后衰减动作；但每次 rx.capture 的前后客户端仍检查 tx_source_selected=false，结束时 disarm。

### Pluto

- **发射前**：在建图前用 libiio 设置 TX LO powerdown=1（关闭）、hardwaregain=-60，读回硬件值；之后才允许 GR 建图。
  注意：radio_test.py 的现存流程是先把衰减压到 -89.75、再 powerdown=0、再由 GR set_attenuation(-60)。本工具的阶段 0 先沿用该已验证顺序；BPSK 6 MSPS 新路径必须先单独审查并明确它如何满足“目标衰减先回读、后开 LO”。
- **结束**：等待进程退出，重新打开 IIO，写 hardwaregain=-89.75 与 powerdown=1，读回二者；不是只看进程 exit code。
- **已知空窗**：GR 独占 USB 流期间无法从第二个上下文读 hardwaregain。该空窗由上述源代码顺序证据覆盖：构造函数不写增益、调频不写增益、set_attenuation(60) 是启动前最后一条增益写。若需要“流内回读”，需要新的硬件/驱动方案（例如单上下文全双工工具），不在本轮范围。

## 残余风险

1. 进程被强杀/断 USB 时，finally 可能不执行；此时衰减值未知。现场处理：断开 Pluto USB/电源与板卡 RF 线，确认后再恢复。
2. 板卡 FAULT 或异常断线路径可能让 TX source 在短暂时间内保持；停止/断线规则与状态机有覆盖，但本轮没有实机证明；异常后必须以 PL 状态读回为准。
3. 没有外置衰减器时，任何“衰减未生效”的软件错误都会直接暴露在接收端；本方案依赖回读门与顺序，不依赖幅度估算。
4. 不能把“60 dB 生效”外推为“所有频率/所有工作点都安全”；433.9 MHz 的模拟前端仍须实测并遵守停止条件。
