# PlutoSDR <-> 板卡 433.9 MHz 联调准备（离线，零发射）

日期：2026-10-19，Asia/Shanghai。工作区：<LOCAL_PATH>。

**本轮状态：离线准备完成，零发射、零 LO 切换、零 nonce、未连板、未连 Pluto、未部署、未采集、未 git commit。**
因此本文件是**待执行 runbook**，不是现场通过记录；现场 TX 是否关闭必须由执行时的只读 preflight 确认，不能由本轮推断。

## 结论先行

| 问题 | 结论 | 依据 |
|---|---|---|
| 433.9 MHz LO 独立诊断 | **可以做（接口层）**。通用 rf.init/rf.configure 只要求 lo_hz > 0；没有 80 MHz 总门。唯一钉死 80 MHz 的是 headroom.* 研究路径，本诊断**不使用** headroom.*。 | CAPABILITY.md |
| +100/+200 kHz 单音 | **板卡可以，但不是精确标称值**。4096 点/60 MSPS 的周期帧只支持 14.6484375 kHz 栅格；取 bin 7/14 得到 102.5390625/205.078125 kHz。Pluto 侧 2.4 MSPS 可发精确 +100/+200 kHz。 | CAPABILITY.md |
| 原 S2 80 MHz | **不能用本 Pluto 通过**。标准 Pluto 范围 325–3800 MHz；80 MHz 在范围外。434 MHz 结果不能宣布原 S2 通过。 | CAPABILITY.md |
| 衰减是否会被启动/初始化/GR 建图/调频重置 | **板卡：不会**（init 先写 46 dB 再调 LO 并回读；configure 先调 LO 再写 46 dB 并回读；tx.load/tx.start 只写 PL）。**Pluto：GR 建图/调频不写 hardwaregain**；radio_test 在建图前先把衰减压到 89.75 dB，再由 set_attenuation(60) 在启动前写入。 | ATTENUATION.md |
| 发射前/结束回读 | 板卡用 rf.configure 的真实回读门（46 dB / hardware_verified=true）；结束时再 configure 89750 并读回，PL 状态要求 TX off、RX disarmed。Pluto 用建图前 LO 关闭状态下的 60 dB 回读 + 停止后 89.75 dB/powerdown=1 回读。 | ATTENUATION.md |
| BPSK 参数 | **当前模板 2.4 MSPS 不可行**（不能承载 3 MSym/s）。纸上可行路径是 Pluto 6 MSPS、sps=2、BPSK/RECT、PRBS7 seed 1、**无差分编码**；USB2 吞吐与 TX FIR 未验证，必须先过 Pluto 自回环。 | BPSK-PARAMETERS.md |
| 电平裕量 | 保守界（按满幅 +10 dBm、0 dB 线损）：A 方向最差 38.5 dB；B 方向最差 52.5 dB。实际配置幅度下更大。**这些是计算界，不是测得 dBm。** | LEVEL-BUDGET.md |
| 现场器材 | 外置衰减器**不是硬阻塞**；用 Pluto/板卡内部 TX 硬件衰减受控直连。示波器 70/100 MHz 不能验收 434 MHz。 | WIRING.md |

## 交付物

| 文件 | 用途 |
|---|---|
| board_integration.py | CLI：--help / --dry-run / --self-test / --preflight（写 Pluto 关断并读回，带 token 时判板卡空闲）/ 受门控 execute |
| readback.py | 共享严格读回解析（真实 `-89.750000 dB` 文本与裸数值；拒绝错误单位/NaN/Inf/缺值/错值）与板卡空闲门 |
| stage0.py | **阶段 0 编排入口**：OFF_PRE → TONE → OFF_POST，单次 TX、≤2 s、三窗口原始 IQ；发射前在 LO 关闭下写并读回 TX LO 频率 433.9 MHz |
| planner.py | 纯离线参数、预算、回读门、单音/BPSK 生成、能力矩阵 |
| config/diagnostic.json | 433.9 MHz 诊断域、46 dB、两窗口预算与门 |
| config/pluto_selfloop.json | Pluto TX→RX 自回环冒烟（60 dB/0.1/0 dB/2 s） |
| config/pluto_tone1.json / pluto_tone2.json | B 方向 Pluto +100/+200 kHz 发射配置 |
| tests/test_planner.py | 离线测试（dry-run、参数、回读门、预算、BPSK 解调、真实收据重放、preflight 判定） |
| tests/test_readback.py | 严格解析正/反例与 Pluto 停止门用例 |
| tests/test_stage0.py | 阶段 0 计划/分析/削顶计算/停止门/CLI 用例 |
| WIRING.md | 端口、换线顺序、两方向接线图 |
| LEVEL-BUDGET.md | 实际输入电平裕量核算与保守界 |
| BPSK-PARAMETERS.md | Fs/sps/PRBS/差分/成形统一结论 |
| CAPABILITY.md | 433.9 MHz/单音能力核对（含 file:line） |
| ATTENUATION.md | 衰减重置核对与发射前/结束回读方案 |
| ../../evidence/rf-teaching/s2-46db-pluto-integration-2026-10-19/NOTES.md | 本轮证据索引与 red/green/mutation 记录 |

## 离线命令（不访问硬件）

~~~powershell
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --help
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --self-test
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --dry-run --direction A
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --dry-run --direction B
& "<LOCAL_PATH>" -X utf8 -B -m unittest discover -s sdr_testbench\board_integration\tests -p "test_*.py" -v
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\stage0.py --dry-run
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\stage0.py --self-test
~~~

3 会话/12 窗口扩展的预算预览：

~~~powershell
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --dry-run --direction A --sessions 3 --extended
~~~

## 物理执行前的一次性前置

以下**全部**满足才允许进入物理步骤；缺一项就停，不通过修改参数绕过：

1. 现场只读确认板卡 TX 已关：daemon poll 的 io_ok=true、tx.source_selected=false、rx.armed=false，release_id 与执行记录一致。
2. Pluto 串号仍为 <DEVICE_SERIAL>；COM11 是 FPGA，不打开。
3. 实际接线与 WIRING.md 的当前阶段完全一致，并由操作者把实际描述传给 --path-confirmed。
4. 板卡 daemon 已 armed（token 由操作者在本机提供；本工具不打印 token、不生成 nonce），且 device.info 广告了 rf.init/rf.configure/tx.load/tx.start/tx.stop/rx.capture。
5. **硬件路径必须用 Radioconda Python**（libiio/GNU Radio 在其中）：<LOCAL_PATH>；离线 --help/--dry-run/--self-test 可用系统 Python。本机到板卡 daemon 的 127.0.0.1:5000 端口转发已建立（例如 ssh -N -L 5000:127.0.0.1:5000 <REMOTE_USER>@<REMOTE_HOST>）；本工具不建立 SSH、不保存凭据。
6. 已明确本次预算（见下）与停止条件。

## 物理顺序（不可跳步）

**阶段 0：Pluto TX → Pluto RX 短时自回环**（先确认参考 SDR 自己）

- 接线：Pluto TX SMA → 同一根 50 Ω 同轴线 → Pluto RX SMA；示波器与板卡都断开。
- 参数：60 dB 内部 TX 衰减、幅度 0.1、RX 手动 0 dB、LO 433.9 MHz、+100 kHz。窗口配置 2 s，TONE 图实际发射 `min(duration_s, 2 s − 0.05 s)=1.95 s`。
- 目的：先证明 Pluto 的 TX/RX 数据流和内部衰减路径；**不得**把既有 loopback 脚本当成已通过。
- 旧 `run.ps1 loopback` **不满足阶段 0**：只有一段 ON、没有 OFF 对照、没有削顶计算、也没有"LO 关闭时 -60 dB 写入并读回"，因此不能作为阶段 0 入口。
- 命令（阶段 0 专用入口；只在完成接线后运行，**必须用 Radioconda Python**）：

~~~powershell
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\stage0.py --execute --path-confirmed "Pluto TX -> Pluto RX direct coax; 60 dB internal; amp 0.1; scope and board disconnected"
~~~

- 顺序：OFF_PRE（仅接收）→ TONE（**唯一一次 TX 启动**，实测 TX 累计 ≤2 s）→ OFF_POST（仅接收）。
- 发射前：libiio 在 **LO 关闭**状态下先写入并读回 TX LO 频率 433900000 Hz，再写入 -60 dB 并读回 hardwaregain 与 powerdown，之后才打开 TX LO。这样 OFF_PRE 窗口不会继承上一次的 LO 频率（真实收据里上电后曾是 2.45 GHz）。
- 结束后：libiio 重新写 hardwaregain=-89.75 / powerdown=1，并**各两次**读回（gain 两次、powerdown 两次，两次都要等于 -89.75 与 1）。
- **预算口径（N6）**：`tx_budget_scope = "modulated TONE graph only"`；TX LO 从前置阶段一直开到结束回读，`lo_on_windows = [OFF_PRE, TONE, OFF_POST]` 三个窗口都可能在开 LO，`tx_seconds_measured` 只统计 TONE 图的发射时长，不是总 RF-on 时间。实测终点只在确认停图（`stop()`+`wait()` 返回）之后记录；无法确认停图时写 `tx_stop_confirmed=false` 且**不**宣称 `tx_budget_ok=true`。
- **RX 带宽口径（fix4）**：stage0 **不设置** RX `rf_bandwidth`——真实 GNU Radio 3.10 的 iio **source** 没有 `set_bandwidth`（只有 sink 有；2026-10-19 真实执行已因此失败一次），因此 RX 使用设备当前带宽值。计划与 `stage0.json` 以 `rx_bandwidth_policy = "not set by Stage 0: the RX keeps the device current rf_bandwidth"` 如实声明，**不得**声称已显式设为 1.5 MHz。
- 结果目录：`sdr_testbench/board_integration/results/<时间戳>-stage0/`（`stage0.json` + `OFF_PRE/iq.npy`、`TONE/iq.npy`、`OFF_POST/iq.npy` 与各自 `result.json`）。
- 削顶判据由样本**计算**（abs-max、近满量程比例、剩余余量），不用"日志里没有 CLIPPED"代替；峰偏离 >2 kHz、任一窗口触满量程、TX 预算超时都判失败。
- **ON/OFF 判据（task-6，用户 2026-10-19 21:2x 批准）**：`matched-narrowband-mainlobe-9-bin`——取 TONE 窗口峰值 bin，以其 ±4 bin（共 9 bin Hann 主瓣）为匹配窄带，比较 TONE 与两个 OFF 窗口在**相同 bin 索引**上的平均功率，要求 `min(tone/off_pre, tone/off_post) ≥ 20.0 dB`（沿用 `STAGE0_MIN_ON_OFF_DB`，未下调）。其余不变式全部保留：峰偏移 ±2 kHz、peak/median ≥20 dB、三窗口无计算型削顶、`tx_starts=1`、`tx_seconds_measured ≤2 s`、`tx_stop_confirmed`、pre LO 433.9 MHz ok、最终 89.75 dB/powerdown=1 各两次读回、cleanup/no-retry。旧 20 kHz 带宽平均 delta 仅作 `on_off_comparison.legacy_band_delta_db`/`legacy_band_passed` **信息字段**（注明 not a gate），不再作为门。
- **修订来源（task-4 离线归因）**：旧口径是 546 bin 带宽均值对 ~9 bin 窄带单音，固定 `10log10(546/9)=17.83 dB` 集成惩罚；阶段0 #2 实测旧口径 7.864 dB、匹配窄带 25.13 dB；OFF 目标带仅超干净噪声 0.43 dB（DC/LO 泄漏与窗/旁瓣 ≤0.6 dB），故属判据口径而非泄漏/链路故障；真实电平偏低（TONE −84.76 dBFS）为次要因素。**历史结果不改写**：`results/20261019-204203-459882-stage0/stage0.json` 仍为 STAGE0_FAIL；新口径的事后重算单列于 `evidence/rf-teaching/s2-46db-stage0-criterion-2026-10-19/`，标注 post-hoc。
- 失败时强制关断发射、保存失败与收尾结果、**不自动重发**；驱动挂死导致无法确认安全关断时保留 `UNKNOWN`/`cleanup_required`。USB 释放被中断或强制关断未执行，同样一律 `UNKNOWN`/`cleanup_required=true` 并写 `cleanup_error`，不会记成 `cleanup_required=false`。

**只读 + Pluto 安全关断 preflight（不做 RF）**

~~~powershell
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --preflight --token-file <本机 token 文件> --board-port 5000
~~~

该命令**写** Pluto hardwaregain=-89.75/powerdown=1 并读回（所以**不是"全只读"**）；带 `--token-file` 时它还会**实际判定**板卡空闲：online、not stale、io_ok、tx.source_selected=false、rx.armed=false，任一不满足即 `passed=false` 且退出码 1。不带 token 时输出 `scope=pluto_only`/`board_checked=false`，明确表示"没有检查板卡"。不 init、不发射、不改 LO。若板卡不是 TX off/RX disarmed，停止，不换线。

**阶段 A（优先）：板卡 TX1 → Pluto RX**

- 接线：AD-FMCOMMS3-EBZ 的 TX1 → 50 Ω 同轴线 → Pluto RX；Pluto TX 空载；示波器断开；板卡 RX1 空载。
- 顺序：OFF_PRE（Pluto RX，板卡 TX off）→ TONE1（板卡 bin 7）→ TONE2（板卡 bin 14）→ OFF_POST（板卡 89750 dB，TX off）。
- 判据：两个 ON 窗口的 Pluto 峰值差约 102.539 kHz（±2 kHz）；ON 峰值/中位数 > 20 dB；OFF 基线不出现同频强峰；任何削顶/回读失败立即停。
- 削顶证据（task-5，与阶段 0 同一套公式与常量）：每个 Pluto RX 窗口都从样本计算 `abs_max_iq`、`full_scale_fraction`（近满量程 0.99，未改）、`hard_clip_fraction`、`full_scale_headroom_db`、`clipped`（abs_max ≥ 1.0），不看日志字符串；任一窗口 `clipped=true` 立即抛错停止该次 A 方向会话（判失败、不重试、不补采），并在停止前完成该次捕获的 Pluto −89.75/powerdown=1 读回。
- 失败路径（task-7）：会话体内任何异常（CLIPPED/采集/回读/Ctrl-C）在向上抛出前自动执行**既有**安全收尾 `_final_safe_stop`（tx.stop → configure 89750 并读回门 → TX off / RX disarmed → disarm）；成功记 `cleanup_required=false` + 收尾读回，失败/不可确认记 `cleanup_required=true` + `cleanup_error` 并写 `failure-cleanup.json`（现场需人工断开板卡 TX1/Pluto RX 同轴线与 Pluto USB）；不重试、不补采，原始异常照旧向上抛出。
- 重复 arm（fix5，2026-10-20）：daemon **已 armed 时不再广告 `control.arm`**（rf_daemon.c:2355-2361），因此 `_stop_tx_and_configure`/`_final_safe_stop` 先 poll：已 armed 跳过 arm、未 armed 才 arm；避免把一次成功的 `tx.stop` 变成会话错误（2026-10-20 真实失败根因）。
- 命令（受门控执行器；本轮未运行，需先完成一次增量审查与物理授权）：

~~~powershell
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --execute --direction A --path-confirmed "board TX1 -> Pluto RX; Pluto TX open; scope disconnected; checked now" --token-file <本机 token 文件> --board-port 5000
~~~

**阶段 B（随后）：Pluto TX → 板卡 RX1（板卡 TX 关闭）**

- 接线：Pluto TX → 50 Ω 同轴线 → 板卡 RX1；板卡 TX1 空载；示波器断开。
- 顺序：OFF_PRE（板卡 RX-only）→ TONE1（Pluto +100 kHz，板卡 RX-only）→ TONE2（Pluto +200 kHz）→ OFF_POST。
- 板卡 TX 必须始终关闭：不调用 tx.load/tx.start，不调用 loopback.physical.*；每个窗口**前后**都 poll 确认 `tx_source_selected=false`（记录 `board_tx_off_before/after`），disarm 后做**有限次重试 poll**（3 次/0.2 s）；全部超时记 `final_status_unknown=true` 并以 UNKNOWN 语义判失败，**不得**把一次瞬时超时写成 “TX source not off / io_ok not true”。
- Pluto TX 子进程生命周期（fix6）：板卡采集异常、`communicate` 超时、ready-file 超时、子进程非零退出等任一情况 ⇒ 先停子进程（terminate→wait→必要时 kill），再用**独立** libiio 上下文做 safe-off 写 + 严格读回（−89.75/powerdown=1）；未确认 ⇒ UNKNOWN/`cleanup_required=true` 并停止会话（不进下一 TONE、不只 kill）。正常 TONE 收尾在子进程退出后**也独立读回一次**。
- 判据（以板卡 IQ 为准）：4 帧 OFF_PRE/TONE1/TONE2/OFF_POST；每个 ON 窗口在**同 FFT bin** 对比两个 OFF 窗口 ≥10 dB（公共 LO 偏移抵消）；两 ON 峰相差 **|Δ| = 7 bin**（102539.0625 Hz，±1 bin；同时记录 `on_peak_delta_signed_hz`/`on_peak_delta_hz` 与 `board_rx_spectral_inversion_observed`）；任一帧削顶即停；板卡 TX 始终 off；Pluto 最终 safe-off 读回成功。
- **板卡 RX 频谱反转（2026-10-20 真机观测）**：Pluto TX `+100 kHz` 在板卡 RX 上出现在 **−102.539 kHz**（bin 2041）、`+200 kHz` → **−205.078 kHz**（bin 2034）；间距幅值仍恰 7.00 bin。判据用幅值；**共轭/交换 I-Q 是可选的 IQ 方向归一化**（可显式施加，但不改变当前冻结 BPSK 实轴判决的 BER；四例原样/反转×共轭/不共轭均为 `reference_sync=true`、BER=0/138，见 BPSK 证据根）。
- BPSK 单次会话（task-14/15）：`--dry-run --direction B --mode bpsk`；1 次采集（4096@60MSPS ratio0）/1 次 Pluto TX/板卡 TX 0；Pluto TX 用 `radio_test.py tx-bpsk`（冻结 6 MSPS/sps2 RECT PRBS7 seed1，必须 `--path-confirmed`+ready-file+配置时长≤2.0 s）；同一帧做原样/共轭两路解调并分别报告。
  - **真机已执行一次（2026-10-20 12:22，单帧，限定）**：433.9 MHz 诊断域、1 次 4096@60MSPS ratio0 采集、两路 PRBS **参考对齐**（`reference_sync` 是 PRBS 参考对齐，**不是**协议帧同步）各 0/138 误码；两路是一致性检查、**不是**两次独立接收，**不得合并为 276 bit**；**不代表**连续锁定/统计 BER/分类/80 MHz S2。证据见 `evidence/rf-teaching/s2-46db-rx-bpsk-2026-10-20/`。
  - 预算：`radio_test` 记录**实测流图窗口** `stream_started_utc/stream_stopped_utc/stream_seconds`；自动门以实测 `0 < stream_seconds <= 2.0` 判定（配置时长仅记录、不作预算证据）；无法确认 stop ⇒ UNKNOWN/FAIL。
  - 措辞：Pluto “独立读回” = 先写 safe-off 属性再读回；板卡 “TX off” = 未调用 `tx.load/tx.start` 且各快照显示 TX source off（**不是**连续监测）。
- BPSK 统计验收（task-17/18）：`--dry-run --direction B --mode bpsk-stat [--stat-tx-per-session 1|2]`；3 独立会话，seed 只允许 **{1,37,73}**（默认 1），每会话 ON 共 10 帧 + OFF 1 帧，共 **33 records / 板卡 TX 0**。`--stat-tx-per-session 1`（默认）：每会话 1 段 TX/10 ON，累计实测 ≤6.0 s；`2`：每会话 2 段 TX（5 + 5 ON），每段实测 ≤2.0 s、每段后独立 safe-off 写后读回、第 2 段前 poll TX off，累计 ≤12.0 s。每帧不再做 before/after TX poll（安全证据用 capture 收据的 pre/post `io_ok`、`post_tx_source_selected=false`、`post_rx_armed=false`、`clipped_samples=0`），只保留 TX 前 1 次 + 会话后 1 次 poll；每帧记录 poll/capture/demod/save 耗时分解。
  - 流窗保护：`radio_test` 在 TX 流阶段结束、`tb.stop` 之前写 `<ready>.stopped`（UTC/单调时间/stream_seconds）；每次 ON 采集前后检查，已停且未满本段帧数 ⇒ **`BPSK_STAT_STREAM_ENDED_EARLY` 立即停**（不再采噪声帧、不进下一会话、不重试），已采帧保留诊断但不计聚合。
  - 聚合只用 **normal** 一路（共轭路仅一致性检查、不得合并 276 bit）：`on_aligned>=27`、聚合 BER≤0.01、OFF 两路均无假 BER、3 会话 closed+观察到 disarm ACK（响应 `{"armed": false}` + final armed=false）、B1/B2/TX 收据身份（含 seed）不变。真机需用户新授权 + Lead GO。
  - 控制面瞬态有界重试（task-19）：会话启动 `_poll_status_with_retry()` 为 5×0.2 s；段前/会话后 `_board_tx_off_snapshot()` 为 3×0.2 s。只对瞬时 timeout/online=false 重试；仍只接受 `online=true`/非 stale/`io_ok=true`/TX off/RX disarmed 等原谓词；在线但不安全或 stale=true **立即硬 FAIL**；持久超时 FAIL。attempts/errors 写入报告（`preflight_poll`、`poll_attempts`/`poll_errors`）。
  - disarm 后 arm 能力等待（task-20）：`_arm_if_needed()` 只有 poll 明确显示 `armed=true`（直接跳过）或 `control_ops` 含 `control.arm` 才调用 `control_arm`；disarm 后 capability 重新广告延迟或 “did not advertise” 拒绝做 5×0.2 s 有界重试；持久不出现/始终被拒 ⇒ 具名硬 FAIL；不绕过 arm、不放松 stale/unsafe 谓词。
  - post-arm 确认与单次 poll 审计（task-21）：`_board_preflight()` 的 post-arm 确认 poll 走 `_poll_armed_with_retry()`（5×0.2 s；只接受 online/非 stale/安全且 `armed=true`；stale/unsafe 立即 FAIL；持续超时或 armed=false ⇒ FAIL）；收尾/诊断纯读 poll 走 `_poll_status_any_with_retry()`（3×0.2 s，仅重试传输瞬态，不施加安全谓词）；`tx.start` 后的 TX-on 严格确认保持单次（控制写后立即判定，不自动重发）；session 报告记录 `preflight_poll`/`arm_wait`/`post_arm_poll`。会话间 disarm 后加 0.3 s settle（仅延迟，不改判据）。
  - Codex B1/B2/B3（task-22）：**B1** TX 流窗口 `stream_seconds` 现在覆盖 `tb.start()` 至 `tb.stop()+tb.wait()` 成功返回；另记 `stream_end_requested`（早）与 `stream_stopped_confirmed` 分离，`<ready>.stream_end_requested` 用于提前停采、`<ready>.stopped` 只在停图确认后写；`stream_stop_confirmed!=true` ⇒ 预算 UNKNOWN/FAIL，不得宣称 ≤2.0 s。**B2** `_arm_if_needed()` arm 后每次 poll 都完整校验 online/非 stale/安全（io_ok、TX off、RX disarmed），不安全/stale 快照立即硬 FAIL 并保留 `unsafe_snapshot` 证据；只对传输不可用/能力未广告做有界等待。**B3** 每次 ON/OFF 采集返回后立即检查 capture 收据（pre/post io_ok、TX off、RX disarmed、clipped=0），失败立即具名停止，不再进行下一次采集或后续 TX；最终 evaluate 保留同等兜底。
- 离线探索性分类（task-17）：`scripts/rf_bpsk_b_classify.py --input <dir> --model <rf_model.json> --model-sha256 <sha> --output <dir>`；ON/OFF 分开报告，输出 `domain_declared=false`/`exploratory=true`/`acceptance_claimed=false`；模型缺失/sha 不符即具名失败；**不得**把 `reference_sync`/BER 当分类标签，也**不得**引用 80 MHz TEST 指标当 433.9 MHz 方向B 指标。**解调成功 ≠ 分类通过**。
- 预算强制（写死）：1 会话 / 1 次 board init / 1 次 configure / 4 个板卡采集 / ≤2 次 Pluto TX / 累计 Pluto TX ≤4.0 s / 板卡 TX 0；第二次 TX 前若已超预算则不再发，累计超 4.0 s 即失败停。
- 命令（受门控执行器；本轮未运行）：

~~~powershell
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --execute --direction B --path-confirmed "Pluto TX -> board RX1; board TX1 open; board TX software off; checked now" --token-file <本机 token 文件> --board-port 5000
~~~

**阶段 BPSK（更后，且必须先过阶段 A/B 单音）**

- 不进入本轮冒烟预算。先按 BPSK-PARAMETERS.md 完成 Pluto 6 MSPS 自回环与离线解调，再申请新的方向/预算。
- 不得用 2.4 MSPS 模板发送 3 MSym/s；不得用 RRC 或差分编码冒充当前冻结的 BPSK/RECT 合同。

## 预算（一次列清，不隐藏追加）

### 一会话四窗口冒烟

| 计数 | A：板卡 TX→Pluto RX | B：Pluto TX→板卡 RX |
|---|---:|---:|
| 窗口数 | 4 | 4 |
| 板卡初始化 rf.init | 1 | 1 |
| 板卡 configure | 3（init 后、tone1 stop 后、结束 89750） | 1 |
| 板卡 tx.load | 2 | 0 |
| 板卡 TX start | 2 | 0 |
| 板卡帧 rx.capture | 0 | 4 |
| Pluto RX 窗口 | 4 | 0 |
| Pluto TX start | 0 | 2 |
| TX 总次数 | 2 | 2 |
| 板卡 TX 累计开窗 | 4 s | 0 |
| Pluto TX 累计开窗 | 0 | 4 s |
| Pluto RX 累计采集 | 8 s | 0 |

### 扩展 3 会话/12 窗口

- **第一会话就是上面的四窗口冒烟，计入总预算**；扩展只追加 2 会话/8 窗口。
- A：板卡 init 3、configure 9、tx.load 6、TX start 6、Pluto RX 12 窗口/24 s。
- B：板卡 init 3、configure 3、板卡帧 12、Pluto TX start 6/12 s。
- 上限：3 会话、12 窗口、3 次板卡初始化、6 次 TX start。超过即拒绝（planner.budget）。
- 12 窗口不等于 12 个板端物理帧；A 的板端帧为 0，B 为 12。

## 停止条件（任一命中即停，不再重试）

- 任何回读不匹配：attenuation_mdB、lo_hz、sample_rate_hz、hardware_verified、product_id、TX/RX PL 状态。
- 板卡 tx.start 后 tx.source_selected 不为 true；tx.stop 后仍为 true；RX armed 未释放。
- Pluto 建图前/停止后 hardwaregain 不是 -89.75 或 powerdown 不是 1。
- CLIPPED / INVALID_IQ / 全零 / 峰值不随频率变化 / OFF 出现同频强峰。
- 预算计数超限；任何 CLEANUP_FAILED；USB 错误；板卡 FAULT；端口转发断开。
- 现场接线与 --path-confirmed 不一致，或有人移动线缆。

停止后：先 control.disarm / tx.stop；Pluto 重新打开 IIO 写 89.75 dB 与 powerdown=1 并读回；若收尾失败，直接断开 Pluto USB/电源并断开板卡 RF 线，报告 CLEANUP_FAILED，不得继续发射。

## 阻塞与所需新授权

- **现场接线未确认**：已确认的只有 Pluto TX→示波器 CH1；板卡↔Pluto 尚未确认。换线必须由操作者按 WIRING.md 执行并回报实际端口。
- **物理执行授权**：本轮没有；--execute 需要单独授权、token、端口转发和实际接线描述。工具本身不能替代授权。
- **BPSK 6 MSPS**：Pluto USB2 吞吐未验证；需要新的 Pluto TX 路径测试与一次增量审查。若 6 MSPS 不可用，BPSK 需要改板卡接收机支持更低符号率（新合同、测试、审查、部署），本轮**不做**。
- **80 MHz 原 S2**：本 Pluto 不覆盖；需要覆盖 80 MHz 的 SDR 或下变频器，不能改 Pluto 固件绕过。
- 不修改冻结合同/基线/阈值/nonce；不把 434 MHz 结果写成原 S2 通过。

## 采集记录完整性（capture_record.py）

`capture_record.py` 是**硬件无关**的严格记录器，已最小接入选定 runner 的会话构造/结束：

```powershell
python sdr_testbench\board_integration\board_integration.py --execute `
  --direction B --mode tone --path-confirmed ... --token-file ... `
  --record --record-context path\to\record-context.json
```

- `--record` **默认关闭**；开启才用 `RecordingClient` 包裹 `RfStatusClient`，`_run_a/_run_b/_run_b_bpsk/_run_b_bpsk_stat` 成功会话末尾写 `out_dir/capture-record.json`。
- `--record-context` 必填：`session`、`boot_id`、`gain_index`、`rx_attenuation_mdb`、`release_id`、`config_revision`、`waveform`（truth/amplitude/physical_max_code/seed/period_samples/quantization/chip_tx_attenuation_mdb）；如报告无 configure readback，可另给 `tx_attenuation_mdb`/`hardware_verified`。缺项在硬件动作前拒绝（`--record` 无 context → exit 2）。
- `--dry-run` / `--self-test` 不受影响；不改变命令、预算、安全门或 `passed` 语义。

底层 API（供未来其他 runner）：

```python
from capture_record import CaptureRecorder, RecordingClient
rec = CaptureRecorder()
client = RecordingClient(client, rec)       # 透明代理，记录 control_op/capture
...
rec.set_waveform(truth=..., parameters=..., expected_config=..., actual_config=..., consistent=...)
rec.set_actual_config(..., tx_attenuation_mdb=..., rx_attenuation_mdb=...,
                      gain_index=..., chip_tx_attenuation_mdb=..., session=..., boot_id=...)
rec.write(out_dir / 'capture-record.json')  # 缺任一项抛 RecordIncomplete
```

记录强制包含：`rf.init`/`rf.configure`/`tx.load` 的 request/response/error/duration/connection_generation/config_revision；capture 身份（capture_id/request_id/daemon_timestamp_ns/release/gen/rev/samples/rate/ratio/physical/IQ sha）；waveform truth/parameters（amplitude、physical_max_code、seed、period_samples、quantization）/expected/actual/consistent；actual_config（RF 固定配置、TX/RX 衰减、gain index、chip attenuation readback、采样 60 MSPS/4096/ratio0、release/config_revision、session/boot）。缺项即 `RecordIncomplete`，不是告警。

离线测试：`tests/test_capture_record.py`（9/9 green；permissive-validator mutation 9 failures red）、`tests/test_board_record_wiring.py`（4/4 fake-client 接线）。不改变现有硬件执行路径/预算/阈值。

## 80 MHz 9 ON + 2 OFF 阶梯实验包（离线，当前阻塞）

`ladder80_package.py` 是 80 MHz 实际入口 `scripts/rf_environment_board_session.py:EnvironmentSession` 的离线计划/记录包：

```powershell
& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\ladder80_package.py --dry-run
```

- **当前执行阻塞**：46000 mdB 合规；48000/50000 超过 `FIXED_RF=46000`（Δ+2000/+4000 mdB），physical capture 客户端要求精确 46000，环境/基线返回 `RF_DOMAIN_MISMATCH`；dry-run 明确 `execution_allowed=false`，不得绕过。
- 入口接线：`wrap_80mhz_session()` 在 `EnvironmentSession` 构造后把 `session.worker.client` 换成 `RecordingClient`；`run_recorded_session()` 在会话结束 `finalize_records()` 写 `capture-record.json`、`declared-context.json`、`commands.json`、`readbacks.json`（三类分开）。不新增命令/poll/重试/预算。
- 顺序/预算：seed 20261021，3 档×3 重复=9 ON + 2 OFF（首尾）；`ladder-sequence.json` 只读使用；名义 source-on 614.4 µs，span 上限 1.0 s。
- OFF 缺失：仅 `physical=false + identity_ok + 唯一 OBSERVATION_MEMBER_MISSING` 记 expected_absent；OFF 不参与 H1/H2。
- 单点 gain 只记 `single_in_window_read`，`agc_trajectory=NOT_MEASURED`。

详见 `evidence/rf-teaching/s2-46db-ladder-package-2026-10-21/EXPERIMENT-PACKAGE.md`。

## 证据

本轮证据：../../evidence/rf-teaching/s2-46db-pluto-integration-2026-10-19/NOTES.md
测试日志：同目录 logs/（01-red、02-green、03-mutations、04-cli、05-sha256）。