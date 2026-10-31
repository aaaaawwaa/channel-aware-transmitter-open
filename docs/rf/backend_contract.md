# AD9361 迁移：后端契约与所有权边界

任务 0 交付物之三。**这份不依赖硬件** —— 它只描述"要加一个 RF 后端，接口长什么样、
谁拥有什么、什么状态从哪里来"。可以在 AD9361 模块到手之前完成并被评审。

设计原则（全部来自迁移方案与既有工程的教训）：

1. **不重构已工作的东西**。`AwgCtl` / `RingThread` / `DdrPp` / 七页 UI 已经验收，
   RF 是**新增一个后端**，不是改写现有后端。
2. **写者唯一**。每个硬件资源只有一个写者。既有工程为此吃过两次亏（`DdrPp` 的
   DMA+GPIO 双写、`Overlay()` 双持有者卡死板子）。
3. **状态必须有真实来源**。禁止硬编码桩（`GET DDR PP` 曾是桩，环形在播时是假话）。
4. **不许把参考链的 PASS 当自研链的 PASS**。

---

## 1. 已存在的抽象（可直接复用，不要重造）

| 已有 | 位置 | 复用于 RF 的方式 |
|------|------|-----------------|
| `UiApp.caps` 字典 | `ui_model.py:321` | **已定义但未被使用** —— 就是给 RF 预留的钩子。当前 `{"ad9361": False, "receiver": False, "model": False, "user_upload": False}` |
| `PanelSession` 后台 job | `pynq_status.py:556` | RF 的 `capture` / `read_status` 走同一套 `SYNC` / `LIST_FILES` 哨兵模式 |
| `backend` 注入 | `awg_ctl.py:531`（`RecordingBackend`）/ `:555`（`PynqBackend`） | 同一模式：RF 有 `FakeRfBackend`（离线）/ `IioRfBackend`（板端） |
| `RingThread` 写者唯一 | `ring_thread.py:43` | **照抄这个模式**给 RF 缓冲：一个 `RfThread` 拥有全部 IIO 缓冲写 |
| `draft` / `observed` 事务 | `ui_model.py:301` | RF 的 `configure` 走同一套 `begin → CommandPlan → finish/fail` |
| `_status_gpio` 只读读法 | `awg_ctl.py`（`GET PLAY`/`GET USERBUF`） | RF 状态同样从**真实驱动/IP**读，读不到就标 UNKNOWN |

---

## 2. 后端接口（最小契约）

新增 `rf_backend.py`，定义两个实现：`FakeRfBackend`（离线，**明确不是板级证据**）与
`IioRfBackend`（板端，走 pyadi-iio / libiio）。

```python
class RfBackend(object):
    """RF 后端的抽象。**不继承 AwgCtl** —— 两者是并列的后端, 不是替换关系。"""

    def capabilities(self):
        """返回 dict。UI 据此启用/禁用控件, 而不是靠猜。

        必须包含:
          backend_id       : "fake" / "iio"        —— 快照里也要带, 用于区分证据来源
          channels         : 实际可用通道数(1 或 2)
          sample_rates     : 驱动**接受**的采样率列表(不是理论上限)
          tx_available     : bool
          rx_available     : bool
          rx_capture       : bool
          loopback         : bool
        """

    def configure(self, cfg):
        """提交 RF 配置(LO 频率/采样率/带宽/TX 衰减/RX 增益)。

        返回 (ok, detail)。**配置回读不是射频输出测量** —— 这里只保证寄存器写成功。
        频率用 Hz, 增益用明确 dB 语义, **不要复用 AWG 的 "AMPL 百分比 = Vpp" 标签**。
        """

    def start_tx(self): ...
    def stop_tx(self):
        """⚠ 停止 TX DMA **不保证射频口绝对无输出**。驱动支持的关闭行为需独立验证,
        并在 capabilities 里如实报告。"""

    def start_rx(self): ...
    def stop_rx(self): ...
    def capture(self, n_samples): ...
    def read_status(self):
        """返回诊断快照。**每个字段都要有来源标注**; 驱动/IP 观测不到的写 UNKNOWN,
        绝不复制原后端的 GPIO 地址(那是 ACM2108 的, 与 RF 无关)。"""
```

### 2.1 快照结构（与 `RingThread._publish` 同构）

```python
{
  "present":     1,              # 0 = 后端缺席(界面显示"RF 不可用", 而不是全 0 假状态)
  "backend_id":  "iio",
  "session_id":  <int>,          # 切后端时递增; 旧会话的迟到结果被丢弃
  "revision":    <int>,          # 配置修订号
  "timestamp":   <monotonic>,
  "stale":       0,              # 1 = 这份快照已过期, UI 不该用它做 finish 依据
  "tx":          {"active":..., "underflow":..., "state":...},
  "rx":          {"active":..., "overflow":...,  "state":...},
  "error":       {"valid":..., "code":..., "text":...},
}
```

**`present` 字段是关键**（照 `ring_thread._blank_snapshot` 的先例）：环形线程缺席时
快照是 `{}`，界面显示"DDR 不可用"而不是一份全 0 的假状态。RF 同样。

---

## 3. 所有权表（照抄既有工程，RF 只加不抢）

| 对象 | 职责（**不变**） | RF 扩展边界 |
|------|-----------------|------------|
| `AwgCtl` + `ctl.lock` | 既有命令语义、GPIO7 ch1、USER 来源控制 | **RF 不碰**。RF 由独立适配器写自己的硬件 |
| `RingThread` | **唯一**写 `DdrPp`；周期发布快照 | **不重用**。RF 有自己的 `RfThread` 拥有 RF 缓冲 |
| `feeder_decision` | 按活动/可写状态决定下一个文件 | **仅在语义相容时复用**决策逻辑（队列选择思想） |
| `UiApp` | 只被 UI 线程写 | RF 字段（`app.rf = {}`）同样只由 UI 线程经 `pump()` 落地 |
| 串口桥 | 格式化**同一份**运行快照 | 不直接抢硬件；**不输出假 IDLE** |
| **`RfThread`（新）** | **唯一**持有 IIO 缓冲与 RF 配置权限 | Linux 驱动管 DMA/SPI 时，PYNQ **不得**再裸 MMIO 同时控制同一外设 |

### 3.1 原后端的顺序必须保留（不许机械搬到 RF）

```
MODE OFF → 装填 → MODE <选定调制> → PP 启动 → 非活动 bank 补充 → 停止并核对状态
```

**"队列非空"只表示有候选文件，不表示已装填或满足启动条件。**
RF 后端用自己的 `prepare / configure / start / stop` 约束，
**不机械发送旧的 MODE/PP 命令**。

### 3.2 状态分离（P5 踩过的坑，RF 必须同样遵守）

| 概念 | 存放 | 铁律 |
|------|------|------|
| 选定调制 | `draft["dig"]["mode"]` | **`actual_mode=OFF` 不得覆盖它** |
| 实际 MODE | `app.actual_mode` | 来自设备回读，不写回 draft |
| dirty | 派生 | **不能**因"选定模式 ≠ 实际 OFF"就标 dirty（否则一开机就挂"有待应用"） |
| SOURCE | 独立字段 | 停止后核对，后续普通调制**不能沿用残留的 USER 锁存** |

---

## 4. 数据契约（任务 2 的核心，先在这里定死）

### 4.1 两种输入类型**必须显式区分**

| 类型 | 语义 | 路径 |
|------|------|------|
| `PAYLOAD_BITS` | 用户文件字节 → 位分组 → 映射 → RRC → I/Q | 现有 `/home/xilinx/waveforms/*.bin` 的**既有位序语义不变** |
| `IQ_SAMPLES` | 已生成的复数采样 → 格式适配 → RF 播放 | 新增，**必须带元数据** |

**严禁**：
- 把用户字节直接喂 RF 采样 DMA（必须先调制）；
- 把原始 I/Q 当成载荷**再次调制**；
- 按扩展名自动猜 IQ 格式。

### 4.2 IQ 文件元数据（最少字段）

```json
{
  "type": "IQ_SAMPLES",
  "sample_rate": 4000000,
  "channels": 1,
  "dtype": "int16",
  "endianness": "little",
  "valid_bits": 12,
  "scale": 2048,
  "n_samples": 262144,
  "layout": "interleaved_iq"
}
```

类型不支持时**返回明确错误**，不静默降级。

### 4.3 容量与带宽（**必须重新估算**）

迁移方案给的算例（**不是板上实测**）：

| 项 | 值 |
|----|----|
| 格式 | I16 + Q16（每复数样本 4 字节） |
| 速率 | 4 MSPS |
| 单向带宽 | 16 MB/s（十进制） |
| 1 MiB 容纳 | 262144 样本 ≈ 65.5 ms |

**约束**：原载荷 bank（1 MiB / 512 B）的容量与吞吐验收**不能覆盖**同体积的 IQ 播放。
板端实测 `CmaFree` 只剩 **3.5 MB**（`version_matrix.md` §1.3），RF 缓冲必须重新规划，
不能沿用 `DdrPp` 的固定物理地址（Linux 下缓冲经驱动分配）。

---

## 5. 验收契约（照 P5/P7 的既有判据，RF 加自己的）

### 5.1 必须延续的原后端验收（回归，不是重做）

1. 真实装配路径：`attach_screen → render/ui.step → 点击数据行入队`，**不直接调回调**。
2. `DMA 源地址解出的 bank == 账面 active_bank`；串口 `GET DDR PP` **独立观测**交替。
   **不能只读面板快照就判串口通过**（P5 曾走这条近路，等于没判）。
3. 欠载从 `_user_status()` / `axi_gpio_8` bit2 读。**禁止**"回包没有 UNDERFLOW 字符串"
   式空断言（P5 抓到过这条恒真断言）。
4. 停止后 `IDLE` / `ERROR 00`，并检查 SOURCE 恢复。
5. 环播中串口 `GET CH1` 仍响应。

### 5.2 RF 新增验收

| # | 判据 | 说明 |
|---|------|------|
| R1 | 从**真实驱动/IP** 获取诊断 | 观测不到的字段标 UNKNOWN，不复制原 GPIO 地址 |
| R2 | 采回序列**连续性** | buffer 状态正确 + 无欠载位**不充分**证明无重复/丢样/边界瞬态 |
| R3 | 周期单文件通过 ≠ 无缝多文件切换 | 两件事分开报告 |
| R4 | RX 每次重新捕获**独立对齐** | 本项目教训：自由运行 + 异步触发下捕获相位不可复现 |
| R5 | BER 报告总比较比特数与错误数 | 零错误只表示**该样本内**零错误 |
| R6 | 测量阈值**测试前**填写 | 不能看到结果后放宽 |
| R7 | 同板环回只验证**相对**频率 | 无外部绝对频率参考时不能认证 LO 绝对精度 |

### 5.3 报告纪律

- 配置回读 ≠ 射频输出测量；
- 已完成 / 播放中 / 已准备 / 已写入 是**四个不同状态**；
- 噪声等级**不标** SNR；AWG 的 Vpp **不标** RF dBm；
- 板载 461 项是历史基线，**不与** host 的 706 项相加；
- 每个 PASS 关联命令、日志、版本与产物。

---

## 6. 分阶段落地（与迁移方案的任务 1-5 对齐）

| 阶段 | 内容 | 前置门禁 |
|------|------|---------|
| **0（本文件）** | 基线固化 + 审计 + 后端边界 | **无** —— 已完成 |
| 1 | 独立 RF 参考设计、驱动、I/Q 采集 | **U1/U2/U3**（模块型号 + 原理图 + 电平） |
| 2 | Python I/Q 播放 + 本地文件类型契约 | 任务 1 通过 |
| 3 | 既有 PL 调制链 + RF 速率/位宽适配 | 任务 2 通过 |
| 4 | 数据页 / 七页 UI / RF 服务集成 | 任务 3 通过（或先接任务 2 的采样播放） |
| 5 | 接收测量与发布收口 | 任务 4 通过 |

**任务 0 可交付范围**：本文件 + `compatibility.md` + `version_matrix.md`。
**任务 1 的第一个动作**：查证 `axi-dmac` 是否必须重编内核（`version_matrix.md` §3）。

---

## 7. 与原工程的明确边界（防混淆）

| 不做什么 | 为什么 |
|---------|-------|
| 不改 `AwgCtl` / `RingThread` / `DdrPp` 的既有行为 | 它们已验收；RF 是新增后端 |
| 不重写七页 UI / 数据页 / 文件队列 / 环形引擎 | P5/P7 已完成并验收，**复用** |
| 不把 `DdrPp` 的双 bank 协议套到 RF | RF 用 ADI 的缓冲/DMA 框架，语义不同 |
| 不用裸机固定 DDR 物理地址 | Linux 下由驱动分配 |
| 不做两次整片 `Overlay()` 来"叠加" | 会卡死板子（本工程 2026-09-12、09-14 各一次） |
| 不把参考链环回 PASS 描述为自研 PL 链 PASS | 两条链的产物与判据互不覆盖 |