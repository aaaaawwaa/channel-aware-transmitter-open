# S2有上限单变量稳定等待对照（设计）

## 授权与目的

承接用户已批准总体方向，以及本轮明确决定：**按计划推进；设计与实现全部离线进行，不连板、不安装、不采集**。

上一轮（`2026-10-06` 时序调查，安装 `692904251e024a1254aecee679c6322a`）已取得四帧与真实IQ绑定的调用时序上下界：TX开启至RX武装的API间隔 **5.106～12.078 微秒**，四帧均未出现rail。源码请求的slow-attack AGC更新间隔为 **1000 微秒**，名义采样窗口 `4096/60MHz ≈ 68.27 微秒`。两者相差约两个数量级，构成"短窗口可能发生在增益充分稳定之前"的**时间尺度线索**。

**该线索尚未被证明为削顶根因。** 三次idle增益读回（RX1 mode2 / index76 / digital0）全部发生在采集窗口之外，不能当作采集中增益轨迹；四帧也没有复现削顶。本轮不宣称根因、不关闭S2正式验收。

三条路线中，本轮只走**单变量有上限稳定等待**：不修改增益、AGC配置、场景幅度或环境判据；不同时改动第二个变量。若等待无效，下一步转查实际接收增益/前端余量，届时另立实验。

本设计**不改变发射能力**：等待只存在于显式请求的有限物理回环交易内，默认UI、默认捕获、普通采集一律不经过它。

## 最小实现

### 请求参数

`loopback.physical.capture` 新增**可选**非负整数参数 `settle_wait_us`：

- 缺省或显式 `0`：**与当前实现逐位一致** —— 零次睡眠调用、零次额外时钟读取、控制顺序不变。
- 取值 `1..5000`：在TX源开启调用**返回之后**、`rf_core_prepare_rx` **之前**插入一次等待。
- 非整数、负数、超过 `5000`：在**任何硬件写入之前**以 `INVALID_ARGUMENT` 拒绝。
- 出现在其它op（含普通`rx.capture`）：拒绝，与现有`observe_timing`的op门禁（`rf_daemon.c` dispatch）同形。
- **`settle_wait_us > 0` 必须同时携带 `observe_timing: true`**：等待只有在能被时序读回证明时才有意义；否则拒绝。这条把"实际等待均需时序读回"变成结构性约束而不是文档约定。

### 注入的睡眠

`rf_daemon_config_t` 新增注入函数指针 `sleep_us`，与既有 `now_ns` 同形（无默认值、由调用方提供）：

- `NULL` 表示"本进程不能等待"；此时任何 `settle_wait_us > 0` 的请求**在硬件前拒绝**（fail-closed，与 `verify_control_token` / `control_hw` 的NULL语义一致）。
- 主机测试注入一个只累计请求值、不真正睡眠的替身，于是"确实请求了1000微秒"与"没有请求"都是**断言出来的**，而不是从源码读出来的。

等待是PS侧睡眠；AGC在AD9361内部独立运行，因此PS睡眠让硬件时间真实流逝。等待期间TX源保持开启，RX看得到回环信号——这正是等待要起作用的前提。

### 时序读数

`last_capture_timing` 对象**增量**扩展，不改变既有8个bracket字段的名字、数量与顺序：

| 字段 | 条件 | 含义 |
|---|---|---|
| `settle_wait_requested_us` | trace可用时**恒有** | 本次请求的等待值；未请求为 `0` |
| `settle_begin_ns` | 仅 `requested_us > 0` | 等待调用前时钟 |
| `settle_end_ns` | 仅 `requested_us > 0` | 等待调用返回后时钟 |

顺序约束：`tx_on_end_ns ≤ settle_begin_ns ≤ settle_end_ns ≤ rx_arm_begin_ns`，且两者都为正uint64。违反即视为**无效trace**，不发布（沿用现有"失败不发布成功trace"语义）。

daemon **不**断言"实际等待 ≥ 请求值"：粗粒度时钟下那是脆弱的，且测量的意义就在于让实验回报真实值而非让生产代码自证。实际值由实验记录与分析侧比较。

### 协议版本

`RF_DAEMON_VERSION` 由 `1.0.2` 升到 `1.0.3`，`RF_PROTOCOL_MINOR` 由 `4` 升到 `5`。纯增量：major不变、manifest记录的仍是major，故不使任何已发布manifest失效。旧客户端不发送该字段，行为与今天一致。

**daemon版本必须同步升版**，不是可选装饰：`scripts/rf_environment_candidate.py` 用一张硬编码配对表 `{'1.0.1':3,'1.0.2':4}` 校验"观察者daemon版本 ↔ 协议minor"，且 `rf_daemon_main.c` 启动时会拿manifest里的 `daemon_version` 与本二进制比对。不升版则新候选会被发布工具直接拒绝（`1.0.2/minor5` 不在表内）。配对表加入 `'1.0.3':5`。

### 客户端

- `_capture_args`：仅 `physical=True` 时允许 `settle_wait_us`；校验 `type is int` 且 `0 ≤ v ≤ 5000`；`v > 0` 时要求 `observe_timing is True`。
- `_capture_unlocked`：`settle_wait_us > 0` 时，要求**当前保留连接hello**确认的 `protocol_minor >= 5`，否则**发送前**拒绝——旧daemon不得先发射再报告不支持。
- `_capture_timing_observation`：**基础trace的版本门禁保持 `protocol_minor >= 4` 不变**——时序trace本身是minor-4特性，提到5会破坏对minor-4 daemon的兼容，也会让已归档的旧4帧证据无法离线复验。仅当本次请求带等待（`> 0`）时额外要求 `protocol_minor >= 5`。随后校验 `settle_wait_requested_us` 为整数且与**本次请求值**一致；`> 0` 时要求 `settle_begin_ns`/`settle_end_ns` 为正uint64、有序、夹在 `tx_on_end_ns` 与 `rx_arm_begin_ns` 之间；`== 0` 时不得出现 bracket。
- 默认poll、默认捕获、`observe_rx_gain`、`observe_capture_timing` 不发送该字段，不增加任何硬件读。

## 有限证据实验

**本阶段只交付设计、实现与离线验证，不采集。** 上板部分在此固定门槛，待用户另行批准后执行。

对照设计：最多**两个**新初始化会话（参考seed1/37）× {normal, narrowband} × 两个等待档 {0, 1000 微秒} = **最多8帧**。等待上限 `5000` 微秒写在代码里。RF/场景/接收配置与上一轮逐项相同：TX1→RX1、attenuation 40000 mdB、LO80MHz/Fs60MHz/BW30MHz/2R2T/IDELAY1/tap29、BPSK 3M RECT、D1、4096点、场景PRBS7 seed1。

**唯一被改变的变量是等待值。** 两个档位都必须 `observe_timing: true`，否则两档不可比。

每帧保留原IQ/header/实际handoff/环境显示/实验记录/独立timing sidecar，并回报：请求与实际等待、间隔上下界、RMS/peak/rail、载波相干度、同步或拒绝原因。削顶、缺trace、身份错配、重复IQ、采集或收尾失败即停止，保留证据，**无自动重试**；不以成功帧覆盖失败帧。

实验完成只表示证据完整与安全收尾，**不表示消除削顶、不支持根因、不表示S2正式通过**。S2正式freeze/108实板/12离线TEST/真人验收与S3全部保持NOT_RUN。

## 验证（全部离线）

**C RED→GREEN**（主机MinGW，独立`HOST_OBJDIR`，`host-test-daemon` / `host-test-core`）：缺省与`0`不产生睡眠与额外时钟读、交易与既有帧逐位不变；`1..5000`仅一次睡眠且请求值被断言；非整数/负数/越界在硬件前拒绝；`>0`缺`observe_timing`拒绝；非physical op拒绝；`sleep_us`为NULL时拒绝；settle bracket有序且夹在`tx_on_end`/`rx_arm_begin`之间；乱序或不正时不发布trace；发送失败/转换失败/活动拒绝不发布trace；旧默认路径、关闭路径与看门狗保持。

**Python RED→GREEN**：`settle_wait_us`仅physical允许、类型/范围/上限校验；`>0`要求`observe_timing`；minor<5发送前拒绝；trace新字段校验与请求值一致性；默认poll/捕获不发该字段；既有客户端回归不退化。

**实验工具 RED→GREEN**：`wait_campaign`产生8帧、顺序为2会话×(normal,narrowband)×(0,1000)；等待档0的帧不带settle bracket；请求值与trace不符即拒绝；削顶即停并保留；会话所有权、解除ACK与最终inspect门禁沿用既有实现。

**完整板载软件回归**与**独立规范→质量审阅**通过后才认定实现完成。native ARM构建、安装与实采属于**上板阶段，本阶段NOT_RUN**。

## 非目标

不修改bit/HWH/core/vendor/CNN/BPSK/环境基线/诊断阈值；不新增MMIO或Overlay；不改MGC或增益档位；不为等待调整任何超时或看门狗；不把等待设为默认；不提交或重置现有dirty tree。
