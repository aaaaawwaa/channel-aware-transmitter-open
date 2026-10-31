# AD9361 RF 控制面与原始 IQ 数据面设计

- 日期：2026-09-24
- 状态：架构设计已在会话中逐段确认，待用户审阅规格文本
- 范围：独立 AD9361 RF 上位机、板端常驻 C daemon、控制协议、原始 IQ 数据闭环
- 非目标：删除或替换 ACM2108 AWG；修改 `rf_overlay/sw/board_app/vendor/`；在本规格阶段构建或下载 RF bitstream

## 1. 背景与目标

工程保留两条完全独立的流水线：

- ACM2108 AWG：继续使用 `host/main.py`、COM11 协议、`sdk/` 和现有验证流程；
- AD9361 RF：新增独立 RF 上位机和板端常驻服务，使用 `rf_overlay/` 的 bitstream、控制面和数据面。

第一版 RF 上位机的目标是：

1. 通过 AD9361 SPI/PL 控制面完成初始化和配置回读；
2. 上传并播放原始 TX I/Q 样本；
3. 触发并读取定长 RX I/Q 样本；
4. 先完成 AD9361 片内数字 loopback，再进行带安全衰减的物理 TX→RX 环回；
5. 在 GUI 中显示原始 IQ 的时域和频域结果。

第一版不宣称支持 EVM、BER、解调、CRC、镜像抑制或 RF 合规指标。未实现指标必须显示为 `unsupported`，不能填 0。

## 2. 组件与所有权

### 2.1 RF GUI

新增独立入口，例如 `rf_host/main.py`，不复用 ACM2108 GUI 的设备状态模型和串口协议。GUI 只调用 `RfClient`，不直接操作寄存器、SPI 或 `/dev/mem`。

GUI 包含：

- 连接/断开与 daemon 能力查询；
- 初始化、LO、采样率、带宽、PHY 模式、TX 衰减和增益配置；
- TX 样本生成/上传/播放/停止；
- RX 定长采集和原始 IQ 下载；
- 时域曲线、频谱显示和数据元信息；
- 数字 loopback 与物理 loopback 的独立入口；
- `BUSY`、`FAULT`、硬件回读失败和 `unsupported` 的明确显示。

### 2.2 Python `RfClient`

`RfClient` 负责：

- TCP 连接和协议版本协商；
- 请求序号与响应匹配；
- 超时、断线、重连和控制租约状态；
- NDJSON 控制消息解析；
- IQ 二进制帧的精确收发；
- 将 daemon 的错误码和硬件状态原样映射给 GUI。

### 2.3 板端 C RF daemon

daemon 基于现有 `board_app` 的平台层、MMIO、SPI 和 vendor API，保持 vendor 代码只读。它是以下资源的唯一写入者：

- AD9361 初始化句柄和配置状态；
- PL 控制寄存器；
- PL SPI master；
- TX/RX BRAM；
- TX 播放和 RX capture 控制；
- 数字/物理 loopback 的复合事务。

现有 CLI 可保留作为离线和板级回归工具。daemon 与 CLI 应共享非 vendor 的 RF 核心逻辑，避免两套命令语义漂移。

### 2.4 Overlay 与服务生命周期

C daemon 本身不负责调用 Python `Overlay()`。RF 服务启动流程由板端 service launcher 先完成以下工作，再启动 C daemon：

1. 检查 `pynq-status` 已停止且无残留进程；
2. 加载与 daemon 版本匹配的 `rf_overlay.bit`/`.hwh`；
3. 核对 FCLK0/FCLK1 的 SLCR 实际分频器；
4. 启动 C daemon，并让其独占 RF PL/SPI。

如果检测到其他 Overlay 使用者或版本不匹配，服务必须失败退出，而不是强行重置或覆盖现有硬件状态。板端部署使用 `pynq_ssh.py`，不使用裸 `ssh`/`scp`。

daemon 仅绑定板卡管理网络接口，配置允许的主机地址和监听端口；第一版只面向隔离的实验室网络，不作为公网服务。一个时刻只允许一个控制租约，第二个控制客户端收到 `BUSY`。

## 3. 控制协议

### 3.1 NDJSON 消息

每条控制消息是一行 JSON，字段如下：

```json
{"id":17,"op":"rf.configure","args":{"lo_hz":80000000,"sample_rate_hz":60000000}}
```

成功响应：

```json
{"id":17,"ok":true,"result":{"state":"READY","lo_hz":80000000,"sample_rate_hz":60000000}}
```

失败响应：

```json
{"id":17,"ok":false,"error":{"code":"INVALID_STATE","message":"TX is active"}}
```

无 `id` 的消息为 daemon 事件，例如状态变化、采集完成、硬件故障和服务断开。事件不能替代命令响应。

协议版本在连接初始化阶段协商。版本或能力不匹配时，daemon 返回 `VERSION_MISMATCH`，不执行硬件动作。

### 3.2 第一版操作

| 操作 | 作用 |
|---|---|
| `device.info` | 返回协议版本、PRODUCT_ID、bitstream/daemon 标识和能力集合 |
| `state.get` | 返回实际状态及关键回读值 |
| `rf.init` | 复位并初始化 AD9361，要求显式提供衰减值 |
| `rf.configure` | 配置 PHY 模式、LO、采样率、带宽、衰减和增益 |
| `tx.load` | 声明 TX 样本数量并接收随后 IQ 帧 |
| `tx.start` / `tx.stop` | 启动或停止 TX 播放 |
| `rx.capture` | 配置一次性/连续采集、抽取比和样本数 |
| `loopback.digital.start` | 执行片内数字 loopback 复合事务 |
| `loopback.physical.start` | 执行物理环回复合事务 |
| `loopback.stop` | 停止当前环回并回读确认 |
| `status.subscribe` | 订阅状态事件 |

不向 GUI 暴露任意寄存器写入口。必要的底层诊断命令只能保留在受控的板端 CLI 中，并且不属于 GUI 控制 API。

### 3.3 错误码

至少定义：

- `INVALID_ARGUMENT`
- `INVALID_STATE`
- `BUSY`
- `HARDWARE_IO`
- `SPI_ERROR`
- `TIMEOUT`
- `OVERFLOW`
- `UNSUPPORTED`
- `VERSION_MISMATCH`

命令已发送不代表成功；GUI 只能依据响应和实际回读更新界面。

## 4. IQ 二进制帧

控制消息声明帧元数据，JSON 行结束后紧跟精确长度的二进制载荷：

```json
{"id":18,"type":"iq_frame","direction":"tx","format":"s16_iq_le","samples":4096,"bytes":16384,"sample_rate_hz":60000000}
```

载荷排列为：

```text
I0(int16 little-endian), Q0(int16 little-endian),
I1(int16 little-endian), Q1(int16 little-endian), ...
```

约束：

- `bytes == samples * 4`；
- 当前 12-bit 数据有效范围为 `-2048..2047`；
- TX 超范围样本拒绝，不静默截断；
- RX 12-bit 数据先符号扩展为 `int16`；
- 帧长度不足、超长或超时时整帧作废；
- RX 结果附带实际采集长度、抽取比、采样率和时间戳；
- 正在接收二进制帧时不插入事件消息，避免破坏 framing。

该协议允许控制面保持可读可审计，同时避免 Base64 对 IQ 数据造成额外膨胀。

## 5. 状态机与事务

### 5.1 状态

```text
OFFLINE
  ↓ rf.init
INITIALIZING
  ↓ PRODUCT_ID、关键寄存器和时钟回读通过
READY
  ├─ tx.start        → TX_ACTIVE
  ├─ rx.capture      → RX_CAPTURING
  ├─ digital.start   → DIGITAL_LOOPBACK
  └─ physical.start  → PHYSICAL_LOOPBACK
```

所有运行态通过 `stop` 进入 `STOPPING`。只有确认 TX 数据源停止、RX capture 释放、相关硬件状态回读正常后，才能进入 `READY`；回读失败进入 `FAULT`，禁止继续发射，必须重新初始化。

普通模式下 TX 与 RX 互斥。数字环回和物理环回是明确的复合状态，只有 daemon 可以在一个事务内受控地配对 TX 播放和 RX 采集。

### 5.2 配置事务

`rf.configure` 只允许在 `READY` 执行。涉及采样率、PHY 模式、LO、带宽等参数的变更按以下顺序完成：

1. 停止数据源；
2. 写入配置；
3. 读取并校验关键寄存器和状态；
4. 恢复目标状态或报告 `FAULT`。

事务期间所有冲突命令返回 `BUSY`。GUI 不得通过并发请求绕过 pending 锁。

### 5.3 安全策略

- `rf.init` 必须显式携带 `attenuation_mdB`，不接受隐式 0 mdB；
- 物理环回启动阶段强制使用 `40000 mdB`；
- daemon 启动时保持 TX 禁用/复位态，不自动发射；
- 物理环回要求 GUI 明确确认线缆和端口；
- 主机连接断开时，watchdog 不自动重配射频，但若 TX 正在工作则执行安全停发、设置最大衰减并回读确认；这是唯一允许的自动硬件动作；
- 数字环回与物理环回结果分开报告，不能互相替代。

## 6. 数据路径要求

TX 路径为：

```text
GUI IQ → RfClient → daemon 校验 → TX BRAM
      → rf_tx_source → phy_tx → AD9361
```

RX 路径为：

```text
AD9361 → phy_rx → RX capture → RX BRAM
      → daemon 符号扩展 → RfClient → GUI
```

实现阶段首先要对照当前 BD、`phy_rx`、`rf_sample_capture`、RX BRAM 和地址表，证明 RX 输出确实连到可读存储器；现有板级文档明确指出此前 RX 输出存在未连接边界，不能仅因 RTL 文件存在就视为数据面完成。

采样、同步、解调、CRC、BER、EVM 是独立状态和独立判据。第一版只实现采样与原始 IQ 采集，其余能力报告 `unsupported`。

## 7. 测试与验收

### 7.1 离线测试

C 侧继续运行 `make host-test`，并增加：

- 命令解析和状态迁移；
- TX/RX 互斥与 loopback 复合门禁；
- pending 锁和错误码；
- `int16 I/Q ↔ 12-bit BRAM word` 转换；
- 二进制帧长度、超时、断线和非法范围；
- 已知 IQ 序列模型；
- vendor 文件校验值未改变。

Python 侧增加：

- `RfClient` 协议、重连和错误映射；
- GUI 状态模型和回读驱动显示；
- IQ 时域/频域绘图；
- `unsupported` 指标显示。

### 7.2 overlay 与部署

每次构建记录：

- `.bit` 与 `.hwh` 同次构建证明；
- 两者 SHA-256；
- Vivado 版本；
- WNS/WHS；
- daemon 与 bitstream 版本标识。

部署前确认 P18=2.5V、FCLK 分频器、Overlay 所有权和 vendor 文件未修改。没有实际板级证据的项目写 `NOT RUN`。

### 7.3 板级顺序

1. `device.info`：PRODUCT_ID、协议版本、能力；
2. `rf.init`：复位时序、关键回读、显式 `40000 mdB`；
3. `rf.configure`：LO、采样率、带宽和 PHY 模式；
4. TX-only：已知 IQ 上传、启动、停止和状态回读；
5. RX-only：定长采集、完成位、样本数和原始 IQ；
6. 数字 loopback：已知序列、采集完整性和相关性；
7. 物理 loopback：`40000 mdB` 下独立执行和记录 RF 路径结果。

host-test、SPI ID、控制面回读、数字 loopback 和物理 RF loopback 必须分开报告。任何一层通过都不能替代后续层的实测。

## 8. 明确不在本设计内的内容

- 删除或重命名 ACM2108 主线入口；
- 将 AD9361 控制接入现有 ACM2108 串口协议；
- 修改 `rf_overlay/sw/board_app/vendor/`；
- 公网暴露 RF daemon；
- 在尚未完成数据面验收前加入 EVM/BER/解调自动判定；
- 用合成 IQ、host-test、SPI 回读或数字 loopback 代替物理 RF 证据；
- 构建、下载或上板操作本身。实现计划和用户批准后才进入这些步骤。
