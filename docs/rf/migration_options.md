# RF 后端迁移：路线并列评估

- **日期**：2026-09-18
- **状态**：**评估，未决策。** 本文只出矩阵与判据，**不选路线**
- **上游**：`docs/rf/migration_plan.md`、`docs/rf/version_matrix.md`、`docs/rf/compatibility.md`、
  `docs/rf/backend_contract.md`、`docs/rf/example_project_notes.md`、
  `docs/rf/usability_assessment.md`（门禁定义）
- **决策方式**：用户已选"**先并列评估再定**"。本文给出对比矩阵 + 会决定选择的那几个问题（§6），
  不给出推荐路线。

---

## 0. 先把"两条路线"这个说法校正过来

任务里给的说法是「**裸机 + 自研数据面** vs **Linux/IIO/`axi_ad9361`**」。**这个二分漏掉了当前的实际形态**，
而当前形态既不是裸机、也不是 Linux 驱动路线：

| 事实 | 出处 |
|------|------|
| `board_app` 是**板载 Linux 用户态**程序，板上原生 gcc 编 | `rf_overlay/sw/board_app/Makefile:4-6` |
| 它用 **`/dev/mem` + `mmap`** 直接裸寄存器，**没有内核驱动、没有设备树节点** | `rf_overlay/sw/board_app/platform_rf.c:36-57` |
| 控制面是**自研 PL SPI master**（PS SPI0 在本镜像里不可用） | `rf_overlay/bd/create_bd.tcl:5`、`rf_overlay/bd/ps7_props_rf.tcl:12-14` |
| 数据面在 **PL 自定义逻辑**里 | `rf_overlay/bd/create_bd.tcl:102`（`rf_tx_source_0`） |
| 例程也是这套形态（但例程是**真裸机**：Vitis / OCM） | `docs/rf/example_project_notes.md:221-222`：「例程是**裸机**（Vitis，跑 OCM），我们是 **Linux/PYNQ** —— 平台层要重写」 |

⇒ 真实的可选集合是 **5 条**，不是 2 条。当前形态（本文记为 **R0**）**在既有文档里没有名字** ——
`version_matrix.md` §4 的三条（A/B/C）里没有它。这本身就是需要先校正的地方。

---

## 1. 五条路线

| # | 路线名 | 一句话 | 与既有文档的对应 |
|---|--------|--------|-----------------|
| **R0** | **现状**：Linux 用户态 + `/dev/mem` + PL 自定义数据面 | 已跑通，但只到"能发单音"（`usability_assessment.md` §3.2） | **无对应** —— 既有文档未命名 |
| **R1** | **R0 + 补 PL 数据面** | 在同一形态里给 `rf_tx_source` 接上真实的 DMA/BRAM 数据面 | `version_matrix.md:113-114` 的 **A2**；`usability_assessment.md` §3 那条门禁 |
| **R2** | **ADI Linux 驱动栈**：`ad9361.ko` + `cf_axi_*` + `axi_ad9361` + `axi_dmac` | 走 ADI 的官方 HDL + 驱动 + IIO/pyadi-iio | `version_matrix.md:67-76` 的 **A1**（原文称 A） |
| **R3** | **换 ADI 参考镜像**（fmcomms 系列） | 软件栈现成，`iio_info`/pyadi-iio 开箱可用 | `version_matrix.md:78-85` 的 **B**（**已判"不推荐"**） |
| **R4** | **真裸机 no-OS**（例程形态） | 与 PYNQ 面板完全隔离 | `version_matrix.md:87-93` 的 **C**（**已判"不作主线"**） |

---

## 2. 对比矩阵

评分口径：**低/中/高**，相对而言，不是绝对工期估计（没有可依据的历史数据，不编）。

| 维度 | **R1**（R0+补数据面） | **R2**（ADI 驱动栈） | **R3**（换 ADI 镜像） | **R4**（裸机） |
|------|---------------------|---------------------|----------------------|---------------|
| **PL 侧工作量** | 中（加一条 DMA/BRAM 数据面 + 速率适配级，**模板在 AWG 侧已有**） | **高**（换掉整个 PL 数据面：`axi_ad9361` + `axi_dmac` + 端口适配层） | 高（同 R2，且要重新适配 AC820 板级外设） | 中（照例程，但数据面是实验逻辑不是调制链） |
| **PS 侧工作量** | 低（沿用现有 `board_app` 形态，只加 mmap 新窗口） | **高**（out-of-tree 编 `ad9361.ko` + `cf_axi_adc.ko`；补设备树 SPI 节点 + AD9361 子节点） | 中（驱动现成） | 中（平台层要重写：例程是 PS SPI+EMIO，我们是 PL SPI+PL GPIO） |
| **内核改动** | **无** | **可能有**（`CONFIG_AXI_DMAC is not set`；见 §3.2） | 换整颗内核 | **无** |
| **实时性** | **中**（`/dev/mem` 直写 + PL 硬时序；用户态调度不参与数据面） | 中（IIO 缓冲经内核；有 cyclic buffer 可用但**周期缓冲 ≠ 连续流**） | 中（同 R2） | **高**（无 OS） |
| **AWG PL 逻辑复用度** | **高**（`awg_top` 单 module reference；DMA→AXIS→FIFO 模板可照抄） | **低**（换 ADI HDL；`migration_plan.md:127`：「查 `axi_ad9361` 的**真实端口**，不假定它接受任意标准 AXIS」） | 低 | 低（例程数据面不是调制链，`example_project_notes.md:222`） |
| **DMA 框架要自造多少** | **全自造**（但 AWG 侧有可照抄的实现） | 少（用 `axi_dmac`）—— **前提是它能编进内核** | 少（镜像自带） | 全自造（例程是裸机固定物理地址，`backend_contract.md:170` 明写 Linux 下**不能沿用**） |
| **校准框架** | **全自造**（沿用现有 PL 自定义逻辑路线） | ADI 提供（`cf_axi_*` + IIO） | ADI 提供 | 用厂商 no-OS 驱动（**当前已在用**） |
| **保住七页面板 / P5 数据页** | **✅ 保住** | ✅ 保住 | **❌ 全丢**（`version_matrix.md:82`：「TFT/GT911/字体/标定/七页 UI/数据页全部要重做」） | ❌ 与面板完全隔离 |
| **判据重标定范围** | 大（50 MHz 时标 → RF 时标，见 §4.3） | 大 | 大 | 大 |
| **不通过的硬阻塞** | 无（**唯一阻塞是工作量**） | §3.2 的内核问题 | AC820 板级外设不支持（`version_matrix.md:82-85`） | 与面板不能共存（`version_matrix.md:92-93`） |
| **既有文档判定** | 未评价 | 未评价（停在"路线选择"） | **不推荐** | **不作主线**（可作驱动行为参考） |

---

## 3. 各条路线的既有记录（有出处，不转述）

### 3.1 R1 —— 自研 PL 数据面

**要自己写的**：
- PL 与用户空间的接口：`docs/rf/version_matrix.md:113-114`「**A2**：照例程思路，Linux 下用 **spidev + 自定义 PL 数据面**（驱动/设备树负担小，**但要自己写 PL 与用户空间的接口**）」
- DMA 适配器：`docs/rf/migration_plan.md:131`「7. `AwgCtl`/`RingThread` 写所有权**不变**；RF 加自己的 DMA 适配器。」
- 不能沿用固定物理地址：`docs/rf/backend_contract.md:170`「不能沿用 `DdrPp` 的固定物理地址（Linux 下缓冲经驱动分配）」；`:231`「不用裸机固定 DDR 物理地址 | Linux 下由驱动分配」
- 三个时钟要拆干净：`docs/rf/migration_plan.md:125-126`「3. **分离三个时钟**：FPGA 逻辑时钟、RRC 样点率、RF 复数采样率。 **不能靠异步 FIFO 解决长期速率不匹配。**」
- 连续性要自证：`docs/rf/migration_plan.md:132`「8. 用**带序号或可对齐的非重复载荷**检查连续性（不能仅凭 bank 切换/无欠载位判定无缝）」

**可复用的模板**（AWG 侧，已实跑）：`scripts/create_bd.tcl:211-266` ——
`axi_dma_0`(MM2S, 32-bit, 无 SG) → `smartconnect sc_dma_hp0` → `ps7_0/S_AXI_HP0`；
`M_AXIS_MM2S` → `axis_data_fifo_0`(`IS_ACLK_ASYNC=1`, 深度 4096) → `axis_dma_benchmark_sink_0`
→ `awg_top_0/user_stream_*`，带 underflow 回馈 + 校验和/水位 benchmark。

### 3.2 R2 —— ADI Linux 驱动栈

**内核阻塞点**：
- `docs/rf/compatibility.md:283-287` 实测：
  ```
  CONFIG_IIO=y / CONFIG_IIO_BUFFER=y / CONFIG_IIO_KFIFO_BUF=y
  CONFIG_AXI_DMAC is not set          ← ADI 的 DMA 驱动缺失
  CONFIG_CF_AXI_ADC / CONFIG_AD9361   ← 完全不存在
  ```
- `docs/rf/compatibility.md:289-296`：「ADI 的 `ad9361.c` 与 `cf_axi_adc` **不在**当前内核里。板端有内核构建树…**理论上可以 out-of-tree 编模块**，且 IIO/SPI/dmaengine 符号都已导出（实测 85 个 `iio_*` 符号…）。但 `CONFIG_AXI_DMAC is not set` 意味着 ADI 的 DMA 引擎**不是内核模块**，out-of-tree 编 `cf_axi_dds`/`axi-dmac` 需要它作为依赖」
- `docs/rf/version_matrix.md:72-75`：「`CONFIG_AXI_DMAC is not set` 是障碍 —— ADI 的数据面依赖它。可能需要：(a) 重新编内核（工程量大，且厂商内核源码不一定全）；或 (b) 用 PYNQ 自己的 DMA 路径绕过 ADI DMA（但那要自己写适配层）」
- 内核版本：`docs/rf/version_matrix.md:43` 板端 **5.4.0-xilinx-v2020.2**，「比 ADI 主线旧很多；ADI 驱动要按此版本适配」

**HDL 侧**：`docs/rf/migration_plan.md:68-70`「ADI 的 HDL 是 **Makefile 流程 + 版本绑 Vivado 分支**；本工程 2025.2 vs ADI `hdl_2026_r1`→2025.1，需确认能否直接打开 `.bd`；不能打开就把 IP 源文件移植过来，**记录 commit**。」

**设备树**：`docs/rf/migration_plan.md:76`「**设备树**：SPI 节点（驱动在，节点缺）+ AD9361 子节点。」
`docs/rf/compatibility.md:271-279` 实测 `/dev/spidev*`、`/dev/i2c*` **都不存在**（节点缺 / disabled），但控制器驱动**编进去了**。

**⚠ 重要更正**：`docs/rf/migration_plan.md:52-54` 那条前置已被**降级**：
「**2026-09-16 已降级**：用户例程用 PS SPI + 自定义 PL 数据面跑通，**不碰 `axi-dmac`**，故这只是**路线选择**」。`compatibility.md:377` 同。

**语义边界**：`docs/rf/backend_contract.md:105`「**不把 ADI cyclic buffer 伪装成 `DdrPp` 双 bank。**」；
`(zip)→AD9361_MIGRATION.md:122`「周期缓冲适合第一版固定波形验证…周期缓冲通过不等价于持续主机流式传输已通过。」

### 3.3 R3 —— 换 ADI 镜像（**已判不推荐**）

`docs/rf/version_matrix.md:82-85`：「**丢掉整块面板**（TFT/GT911/字体/标定/七页 UI/数据页全部要重做）；且 ADI 镜像**不支持 AC820 的板级外设**（LED/按键/TFT 控制器）—— 需要把整个 AC820 overlay 移植到 ADI 内核上，工程量**远大于**路线 A。**判定**：**不推荐**。」

附加：`docs/rf/migration_plan.md:19-21`「**原启动介质备份** | **未做** | **⚠ 待办（V5）**」；
「**⚠ 换镜像前必须做的第一件事**：备份当前 SD 卡镜像。这是**唯一不可逆的步骤**。」

### 3.4 R4 —— 真裸机 no-OS（**已判不作主线**）

`docs/rf/version_matrix.md:92-93`：「与现有 PYNQ 面板完全隔离…且 ADI no-OS 的 AD9361 驱动与 Linux 驱动**不能并发**写同一芯片。**判定**：作为**独立备选/驱动行为参考**，不作为主线。」

`docs/rf/example_project_notes.md:221-226`「不能直接照搬的部分」：「例程是**裸机**（Vitis，跑 OCM），我们是 **Linux/PYNQ** —— 平台层要重写。… 例程的**数据面**是它自己的实验逻辑（FFT 载频检测等），**不是**我们要的调制链。… 例程没做设备树、没走 libiio，**不能用它替代 ADI 的 Linux 驱动路线**。」

### 3.5 例程证明的那件事（对**每一条**路线都有用）

`config.h` 里 `AXI_ADC_NOT_PRESENT` 已定义、`ADC_DMA_EXAMPLE`/`DAC_DMA_EXAMPLE` 全部注释掉
（`rf_overlay/sw/board_app/vendor/config.h:58-66`，sha256 记录在 `vendor/SHA256SUMS`）：

- `docs/rf/compatibility.md:144-146`：「该例程是**PS SPI + 裸机** 架构（`config.h` 定义 `AXI_ADC_NOT_PRESENT`，即**不走 ADI AXI-DMA**），数据面走自定义 PL 逻辑 + AXI GPIO/BRAM 与 PS 交互 —— 这证明 **B4（`axi-dmac`）不是唯一路线**。」
- `docs/rf/version_matrix.md:104-106`：例程「在 AC820 上**真跑通了 AD9361**…**完全不使用 ADI 的 `axi_adc`/`axi_dmac`**」

⇒ **"不用 ADI HDL 也能跑通 AD9361"已经不是假设，是实证。** 这是 R1 最强的一条依据。

**但注意差异**：当前 `rf_overlay` **没有**照抄例程的 PS SPI / PS EMIO，而是自写了 PL SPI master + 9 位 PL GPIO
（`rf_overlay/sw/board_app/platform_rf.c:3-6`：「The reference platform.c cannot be reused because it drives XSpiPs and puts the nine control pins on PS EMIO GPIO, whereas here they are a 9-bit PL aperture and the SPI port is our own PL master.」）。
所以 R4 与 R1 的平台层**已经分叉**，不能互抄。

---

## 4. 与路线无关的共同约束

**这些不因选哪条路线而消失**（也是"并列评估"里最容易被忽略的一块）：

| # | 约束 | 出处 |
|---|------|------|
| C1 | **CMA 只剩 3.5 MB**（`CmaTotal 131072 kB` / `CmaFree 3584 kB`） | `docs/rf/compatibility.md:300-308`；`docs/rf/version_matrix.md:37,50` |
| C2 | **SD 卡备份未做**，是**唯一不可逆**的步骤 | `docs/rf/migration_plan.md:19-21`、`:209`（B5） |
| C3 | **不做两次整片 `Overlay()`** —— 会卡死板子 | `docs/rf/backend_contract.md:232`；本项目 2026-09-12、09-14 各一次前科 |
| C4 | **不许把参考链的 PASS 当自研链的 PASS** | `docs/rf/backend_contract.md:13`、`:232`；`docs/rf/version_matrix.md:130-131` |
| C5 | **两个写者不许并发写同一芯片**（no-OS 驱动 vs Linux 驱动） | `docs/rf/version_matrix.md:92-93`；`(zip)→AD9361_MIGRATION.md:73` |
| C6 | **停止 TX 不保证射频口无输出**，须独立验证并如实报告 | `docs/rf/backend_contract.md:60-63` |
| C7 | DMA 缓冲容量要**重新估算**，不能沿用 `DdrPp` 的 1 MiB / 512 B | `docs/rf/backend_contract.md:157-170` |
| C8 | 板载 461 项是历史基线，**不与** host 706 相加 | `docs/rf/migration_plan.md:183` |

### 4.1 可复用的抽象（**两条路线都成立**）

`docs/rf/backend_contract.md:17-27`「## 1. 已存在的抽象（可直接复用，不要重造）」：

| 已有 | 位置 | 用于 RF |
|------|------|---------|
| `UiApp.caps` 字典 | `pynq_overlay/sw/ui_model.py:321` | 当前 `{"ad9361": False, "receiver": False, "model": False, "user_upload": False}` —— **已定义但未被使用，就是给 RF 预留的钩子** |
| `PanelSession` 后台 job | `pynq_overlay/sw/pynq_status.py:556` | RF 的 `capture`/`read_status` 走同一套哨兵模式 |
| `backend` 注入 | `pynq_overlay/sw/awg_ctl.py:531`(`RecordingBackend`) / `:555`(`PynqBackend`) | RF 有 `FakeRfBackend`（离线）/ `IioRfBackend`（板端） |
| `RingThread` 写者唯一 | `pynq_overlay/sw/ring_thread.py:43` | 照抄给 RF：一个 `RfThread` 拥有全部缓冲写 |
| `draft`/`observed` 事务 | `pynq_overlay/sw/ui_model.py:301` | RF 的 `configure` 走同一套 `begin → CommandPlan → finish/fail` |
| 只读状态读法 | `awg_ctl.py`（`GET PLAY`/`GET USERBUF`） | RF 状态同样从**真实驱动/IP**读，读不到就标 UNKNOWN |

**⚠ 两条必须写明的**：
1. `backend_contract.md` 在**接口层**偏 R2（`:30-33` 把板端写成 pyadi-iio/libiio），
   但它的抽象（capabilities / snapshot / 所有权 / 事务模型）对**两条路线都成立** ——
   这一点在设计 R1 时要用到，不能因为措辞偏 R2 就以为 R1 要另起一套。
2. **这些实现文件目前都不存在**（全仓搜 `RfThread|IioRfBackend|FakeRfBackend` 零命中）。
   `backend_contract.md` 是**契约**，不是**代码**。

### 4.2 与后端无关、可直接复用的资产

`(zip)→AD9361_MIGRATION.md:37-50`（方案包里的复用表，**逐条**）：

- `:41`「Python黄金模型、Gray映射、RRC参数与验证算法 | 优先复用；逐个检查缩放、位序与滤波时标」
- `:42`「PRBS7、USER字节流、板载本地队列/环播与主机UDP链 | 复用业务逻辑；DdrPp/ADI DMA分别实现硬件适配」
- `:43`「PL映射/RRC | 单独接入新采样时钟、数据使能和背压契约」
- `:44`「8-bit量化、MS9708偏移二进制、模拟链路极性补偿 | **留在ACM2108后端；不直接搬到RF后端**」
- `:45`「50MHz下的信道损伤 | 延迟、NCO频率字、噪声标定**必须按所处采样率重算**」
- `:46`「PYNQ七页触屏、数据页与draft/observed事务 | 保留页面、键盘、命中路径和选择态，通过设备服务接RF」
- `:47`「裸机DDR内存地址与寄存器裸写 | Linux分支重新管理，**不能照搬固定物理地址**」
- `:48`「示波器回归 | 保留作原后端基线；RF接收数据添加独立验收」
- `:50`「保留按后端的能力表，不将裸机IQIMP、信道或全速率验收自动转移到PYNQ/RF版本。」

同结论在正文：`docs/rf/migration_plan.md:99,102,129`；`docs/rf/backend_contract.md:228-229`。

### 4.3 判据重标定的范围

**原则句**（仓库里唯一一处"必须重算"的成文条款）：
`docs/rf/migration_plan.md:130`「6. 逐层迁移：PRBS7 → QPSK → RRC → USER → 本地队列/DDR → 信道损伤。 **每层重新计算**延迟秒数、NCO 频率字、噪声标定（**50MHz 的静态延迟不能不变地解释为新采样时标**）。」
（同句在方案包 `(zip)→AD9361_MIGRATION.md:45`。）

**已被 50 MHz 时标写死、换 RF 后语义失效的具体量**：

| 量 | 现状（50 MHz 时标） | 出处 |
|----|-------------------|------|
| 抽头延迟 | `0..511` 样本 = **0…10.24 µs @50 MHz** | `docs/reference/AGENT_GUIDE.md:50`；`docs/superpowers/specs/2026-09-10-channel-impairment-design.md:50` |
| 噪声底 | 噪声带宽 = 样点率一半；`level=100` 时 `±3.4σ` 超 ±127 | `docs/superpowers/specs/2026-09-10-channel-impairment-design.md:100` |
| 干扰音 NCO | `f = fword/2²⁴ × fs`，分辨率 2.98 Hz | `docs/superpowers/specs/2026-09-10-channel-impairment-extension-design.md:85-87` |
| 符号率/DAC 更新率 | 均为 50 MHz；1 MSym/s 时每符号 50 个更新点 | `docs/superpowers/specs/2026-08-27-digital-modulation-symbol-rate-design.md:23,37` |
| RRC | PL 时钟 50 MHz | `docs/superpowers/specs/2026-08-28-digital-modulation-rrc-design.md:39` |
| 量化 | 8-bit + 偏移二进制 + 极性补偿 | `docs/rf/version_matrix.md:126` |
| 状态来源 | `_user_status()` / `axi_gpio_8` bit2 | `docs/rf/version_matrix.md:127` |

**⚠ 但这一整块的规模取决于 §5 那个未决参数。**

### 4.4 验收契约：哪些"延续"、哪些"新增"

- **延续（回归，不是重做）**：`docs/rf/backend_contract.md:176-184`（真实装配路径、bank 一致、
  欠载从 `_user_status()`/`axi_gpio_8` bit2 读、停止后 IDLE/ERROR 00、环播中 `GET CH1` 仍响应）
- **RF 新增 R1–R7**：`docs/rf/backend_contract.md:186-197`
  （R1 真实驱动/IP 取诊断；R2 连续性；R3 周期单文件 ≠ 无缝切换；R4 **每次重新捕获独立对齐**；
  R5 BER 报总比较比特数与错误数；R6 **测量阈值测试前填写**；R7 **无外部绝对频率参考时同板环回只验证相对频率**）
- **报告纪律**：`docs/rf/backend_contract.md:199-204`（「噪声等级**不标** SNR；AWG 的 Vpp **不标** RF dBm」等）

**⚠ 重标定不是假设 —— 已经真实发生过**：`docs/rf/l2_stage1_record.md:120`「⚠⚠ 判据 3.2 的数值错了 6 倍 —— 根因已定位（16 位窗计数器溢出）」；
`docs/rf/boardtest.md:724-745` C3 在两个模式下都不可信。

---

## 5. 一条**未决参数**，它会显著改变 R1 的工作量

**AD9361 的采样率定 60 MSPS（现状 / 规格 / 例程）还是 50 MSPS（与 AWG 链一比一）。**

| | 60 MSPS | 50 MSPS |
|---|---|---|
| DATA_CLK | 120 MHz(1R1T) / 240 MHz(2R2T) | 100 / 200 MHz |
| 与 AWG `sys_clk`(50 MHz) | 非整数比 → **需要分数比转换** | **一比一，无需转换** |
| §4.3 的重标定 | **全做** | **整块消失** |
| `rx/tx_path_clock_frequencies` | `{1280M,160M,160M,80M,40M,40M}`（已实跑） | **需重算，未验证** |
| 代价 | — | 丢 10 MHz 奈奎斯特 |

见 `docs/rf/usability_assessment.md` §3.4。**按"参数冲突先按例程为准"的常驻指令，默认取 60 MSPS**；
改 50 是一次需要你显式裁决的偏离。

---

## 6. 会决定路线选择的几个问题（这才是"并列评估"该产出的东西）

不回答这些就选路线，等于按偏好选。**每个问题的答案都能把选项砍掉一半**：

| # | 问题 | 若答"是" | 若答"否" |
|---|------|---------|---------|
| **Q1** | **一定要保住七页面板 / P5 数据页吗？** | 砍掉 R3、R4 | R3 重新进入候选 |
| **Q2** | **能接受为了 RF 重编内核吗？**（`CONFIG_AXI_DMAC`） | R2 可行 | **R2 出局**，只剩 R1（或 R2 的 (b) 绕过方案 = 自写适配层 ≈ R1） |
| **Q3** | **需要连续流式（不是周期单文件）吗？** | R2 的 cyclic buffer 不够，需另做；R1 也要自证连续性 | R2 的现成周期缓冲有优势 |
| **Q4** | **CMA 的 3.5 MB 打算怎么解决？**（C1） | 影响所有路线，且影响 R2 更多（驱动分配缓冲） | — |
| **Q5** | **RX 要不要现在就规划？** | R2/R3 的 IIO 通路直接给了 RX，权重上升 | R1 优势上升 |
| **Q6** | **AD9061 的采样率取 60 还是 50 MSPS？**（§5） | 取 50 → R1 工作量**大幅下降** | 取 60 → 各路线差距缩小 |

**我的观察（不是推荐，供你判断）**：Q2 和 Q6 的答案几乎单独决定了这张表。
Q2 = "不接受重编内核" 时，R2 只剩"用 PYNQ 自己的 DMA 绕过 ADI DMA + 自写适配层"，
那**在工程量上已经收敛到 R1**，而 R1 还多保住了 AWG 侧可照抄的 DMA 模板。
但这是**工作量**维度的观察，**没有**计入 R2 在 RX / 校准框架上的现成度 ——
那正是 Q5 在问的。

---

## 7. 明确"无记录"的部分（不推测填补）

1. **两条路线之间的正式决策结论无记录** —— 既有文档一律停在"这是**路线选择**问题"
   （`docs/rf/migration_plan.md:54`、`docs/rf/compatibility.md:383`），没有任何一处写下选哪条。
2. **R2 的"合并 BD / 地址 / 中断 / 设备树"具体方案无记录** ——
   `(zip)→AD9361_MIGRATION.md:179` 只说"须规划成完整的新BD"，仓库里没有该 BD 草案、地址图或中断分配表。
3. **ADI 镜像的具体版本号/下载地址无记录** —— `docs/rf/version_matrix.md:80` 只写"如 fmcomms 系列"。
4. **`axi_ad9361` / `axi_dmac` 的端口清单、AXI-Stream 适配层设计无记录** ——
   仓库内**没有任何** `axi_ad9361` 端口表或适配层代码。
5. **`RfBackend`/`RfThread`/`IioRfBackend`/`FakeRfBackend` 的实现不存在**（只有契约描述）。
6. **设备树片段无记录** —— 仓库内无 `.dts` / overlay 文件。
7. **RX 通路的任何实现细节无记录** —— 仅 `docs/rf/boardtest.md:614` 说排在阶段二之后。
8. **`rf_overlay/` 下没有 README、没有设计说明文件**（`ls rf_overlay/*.md rf_overlay/*.txt` 为空）。
9. **`CLAUDE.md` / `README.md` / `AGENTS.md` 完全没有 RF 迁移内容**（全仓 grep 零命中）——
   RF 的知识**只**在 `docs/rf/*` 与 `docs/superpowers/{plans,specs}/2026-09-17-*`。

---

## 8. 引用索引

- 门禁与三个判断：`docs/rf/usability_assessment.md`
- 示波器边界与探索方案：`docs/rf/scope_limits.md`
- 后端契约：`docs/rf/backend_contract.md`
- 迁移任务分解：`docs/rf/migration_plan.md`
- 路线（A/B/C）与环境差异：`docs/rf/version_matrix.md`、`docs/rf/compatibility.md`
- 例程分析（不走 ADI AXI-DMA 的实证）：`docs/rf/example_project_notes.md`
- 方案包（zip 内）：`docs/reference/ad9361_migration_pack (1).zip → AD9361_MIGRATION.md`、`AGENT_PROMPTS.md`
