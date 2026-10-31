# AD9361 硬件兼容表（AC820 + FMC）

任务 0 交付物之一。**结论先行**：AC820 的 FMC 插座与 AD9361 模块在**电气层面有真实冲突风险**，
不能仅凭"都是 Zynq-7020 / 都有 FMC"就判定可直接连接。下表每行都标了证据出处；查不到的写
UNKNOWN，不猜。

本文件不构成硬件兼容性认证。它是一份**待补齐证据的清单 + 已查明的硬约束**。

---

## 0. 模块型号确认后的结论（2026-09-16 二次改判：硬阻塞已解除）

**模块 = AD-FMCOMMS3-EBZ。判定：可以直插 —— 前提是 P18 跳到 2.5V 档（实测当前即 2.5V）。**

> **⚠ 本节在 2026-09-16 被整体改判一次。** 9-15 版结论是「Bank 34 固定 3.3V、数据面所在的
> 那一半拉不到 2.5V、直插不工作」，依据是**原理图 `pdftotext` 文本**里读出「P18 只接 VCCIO35，
> VCCIO34 经 FB20 硬接 VCC3P3」。**该依据是错的**，两处独立证据都推翻它：
> 1. **原理图矢量追踪**（PyMuPDF 按坐标取线段、按 junction dot 判连通，不经过文本层）：
>    `VCCIO34` / `VCCIO35` / FMC 的 `VADJ_3`（G39）是**同一个节点**。
> 2. **板上实测 TP8 = 2.5V**（2026-09-16）—— TP8 就挂在这个网络上，即 **Bank 34 与 Bank 35
>    的 VCCO 实测就是 2.5V**。
>
> 另外：当年以为给 Bank 34 供 3.3V 的 **FB20 是 DNI（不贴装）**，而 FB16/17/18/19 是贴装的
> —— 3.3V 只是一条**没焊的备选通路**，真正的供电是那路可调 LDO。用户手册「专为 FMC 接口
> 所在的 Bank 设计了一路供电……可改变该 IO Bank 的供电电压」这句话是**准确**的；9-15 版
> 把它读成「文档没说清」才是误读。

### 0.1 证据链（三方独立对齐，互相印证）

| 来源 | 给出什么 | 权威性 |
|------|---------|--------|
| ADI 官方 `fmcomms2/zc706/system_constr.xdc` | 每个 AD9361 信号 → **FMC 引脚号 + FMC LA 名**（XDC 注释里两者都有） | ADI 发布，权威 |
| 厂商 `FMC接口.xlsx` | **FMC 引脚号 → FPGA 引脚号** | 厂商发布，权威 |
| Vivado 2025.2 `get_package_pins` | **FPGA 引脚 → Bank** | 工具权威 |

三者**完全自洽**（例：`rx_clk_in_p` → `G6` / `LA00_CC_P` → xlsx `M19` → Bank 34；
`spi_csn` → `D26` / `LA26_P` → xlsx `B16` → Bank 35）。

> **⚠ 方法教训**：先前一版曾用 `pdftotext` 从 AC820 原理图连接器页解析 LA 名→bank 引脚，
> 结果**整列错位一行**（例：把 LA00_CC_P 读成 B34_L13_P，实际是 M20）。现改用
> **ADI XDC 注释给的 FMC 引脚号直接查 xlsx** —— 完全不经过原理图文本解析，无错位风险。
> 下表以此为准（§附二）。

**另一个关键旁证**：ADI 的 **ZedBoard（也是 Zynq-7020 + LPC）** 用的是**同一套 LA 分配**
（53 个端口，LA 名逐一相同 —— 说明 FMCOMMS3 模块侧的 LA 引脚定义与载体无关）。ZedBoard 的
`rx_clk_in_p` 同样是 **`M19`（Bank 34）**、且同样约束成 `LVDS_25`。而 ZedBoard 能跑起来，
是因为它的 **FMC VADJ 跳线同时供 Bank 34/35** —— 即 ZedBoard 会把 Bank 34 拉到 2.5V。

> **AC820 与它同构**（2026-09-16 更正）：ZedBoard 靠 VADJ 同时覆盖 Bank 34/35；
> **AC820 的 `VCCIO34` 与 `VCCIO35` 本来就是同一个网络**（§0.2），P18 一路供两半 ——
> 实测 2.5V，两者条件一致，故 ZedBoard 能跑的组合 AC820 也成立。

### 0.2 改判后的结论（2026-09-16）

**结论：`VCCIO34` ≡ `VCCIO35` ≡ FMC `VADJ_3`，由 P18 可选 LDO（U35 SPX3819）驱动。**

- 原理图矢量追踪给出的是一条带 junction dot 的实链：
  `VCCIO34 标签 → 竖线 → 节点 → 横线 → 长竖线 → 节点 → FMC G39(VADJ_3) 竖线 → VCCIO35 标签`。
  FMC 的 `VADJ_3` 竖线**穿过地平线时不打点**（不相连），在下面才与 VCCIO34/35 交汇。
- **FB20 是 DNI**（原理图位号表器件值写死 `DNI/120oHM`，而 FB16/17/18/19 都是实装的
  `120oHM`）—— 那条「Bank 34 被 3.3V 硬接」的路**根本没焊**。
- 板上实测（2026-09-16）：**TP8 = 2.5V**。TP8 就挂在这条网络（VCCIO35 侧测试点，见 §0.5），
  因此 **Bank 34 与 Bank 35 的 VCCO 都是 2.5V**。

**这为什么正好可用**：

| 需求 | 电平 | Bank 类型 | 2.5V 下是否成立 |
|------|------|----------|----------------|
| AD9361 LVDS DDR 数据/时钟（LA00–LA16，Bank 34） | `LVDS_25` | HR（`BT_HIGH_RANGE`） | **成立** —— HR bank 的 `LVDS_25` 正是要求 VCCO = 2.5V（UG471） |
| AD9361 控制面（SPI/RESETB/GPIO，LA19–LA28，Bank 35） | `LVCMOS25` | HR | **成立** |
| AD9361 `VDD_INTERFACE` | 数据手册只有 1.2/1.8/2.5 V 三档 | — | **正好 2.5 V 档**，远离绝对最大值 2.8 V |

> **原「直插不工作」的判定作废。** 不需要改板（原路线 A），也不需要电平转换中介板（原路线 B）。
> 唯一要做的是一根跳线：**P18 放 2.5V 档**。

**⚠ 必须避免的档位**：手册说 P18 有 3.3V/1.5V/1.8V/2.5V 四档。**3.3V 档会重新触发
AD9361 的绝对最大值超限问题**（`VDD_INTERFACE` 最高 2.5V、abs max 2.8V，有损伤风险），
而 1.5V/1.8V 档会让 `LVDS_25` 不成立。**只有 2.5V 档可用。**

### 0.3 原「三条出路」已全部不需要（2026-09-16）

9-15 版列的三条路线 A/B/C 建立在「Bank 34 拿不到 2.5V」这一**错误前提**上。前提既已推翻，
**三条路线全部作废**（A 无需改板、B 无需中介板、C 本就排除）。改为一条操作：

> **P18 跳线放 2.5V 档；若实物已在 2.5V 档（实测 TP8 = 2.5V 即为此情形），什么都不用改。**

**U11'（Bank 34 板载外设在 2.5V 下的行为）—— 2026-09-16 已基本闭合**（见 §0.6）：

- **LED0–7：安全**。用户手册 图26 + 原理图 CB 第 9 页都是 `FPGA 引脚 → 1K → LED → GND`
  （**共阴、灌电流**）。2.5V 下 IO 上不会出现 3.3V，无绝对最大值风险。
- **FPGA_KEY0/1：安全**。上拉 4.7K（R199/R200）接 **`VCCIO35`**（2.5V 轨），不是 `VCC3P3`。
- **TP_RST：无 3.3V 注入**。`TP_RST` 是 **Bank 34 的 R15**（见 §1.3），但只接到 LCD FPC 的
  第 35 脚，**原理图里没有任何上拉** —— 由 FPGA 直接驱动。GT911 供 3.3V、VIH ≈ 2.31V，
  2.5V 驱动余量 0.19V，**仍需上板实测触摸**（这是唯一的残余项）。
- **TP_INT / TP_SCL / TP_SDA 不在 FPGA 上**（重要）：它们是 **PS I2C0** 的引出。原理图 CB
  第 14 页 U19 是 **PCA9306DCUR I2C 电平转换器**，A 侧 = `PS_I2C0_*`，B 侧 = `TP_*`；
  **SCL/SDA 的上拉（R99/R100 1K）在 A 侧（1.8V 域）**。故这几根线根本不进 Bank 34，
  与 FMC/2.5V 无关。（先前「I2C 上拉可能往 Bank 34 灌 3.3V」的担忧不成立。）
- **TFT 全部在 Bank 33**，不受影响（已查：V22 AB19 … V14 共 21 个引脚全在 Bank 33）。

### 0.4 与软件路线的关系

上述**曾是**电气层阻塞，与 §3 的 `axi-dmac` 软件问题**相互独立**。**电气层已于 2026-09-16
解除（§0.2）**；软件面（内核 `CONFIG_AXI_DMAC is not set`）仍是任务 1 的独立待办。

### 0.5 实测记录（2026-09-16）

| 项 | 方法 | 结果 |
|----|------|------|
| `VCCIO34`/`VCCIO35` 网络电压 | 万用表直流档，红笔 → **TP8**（板上 `VCCIO35` 侧测试点，CB 第 12 页 TP8 坐标 (276,196)），黑笔 → GND | **2.5 V** |
| FB20 装否 | 原理图位号表器件值 | **DNI / 120oHM**（不贴装）；FB16–FB19 为实装 `120oHM` |
| `VCCIO34` ↔ `VCCIO35` ↔ `VADJ_3` 连通性 | PyMuPDF 矢量线段 + junction dot 追踪（CB 第 13 页） | **同一节点** |

**判读**：Bank 34 与 Bank 35 的 VCCO = 2.5V → `LVDS_25` 成立、`LVCMOS25` 成立、AD9361
`VDD_INTERFACE` = 2.5V 档（abs max 2.8V，不超限）。**原 B3' 硬阻塞解除。**

**可选复核**：若想直接证 Bank 34 那一侧，可在 **FB20 的 `VCCIO34` 侧焊盘**对地再量一次
（FB20 空焊，该焊盘即 `VCCIO34` 网络的可触点在），应与 TP8 同值 2.5V。

### 0.6 第三方独立复核（2026-09-16，用户提供的例程）

用户提供的例程 `ps_ctrl_AD9361_FM_test_final_copy`（Vivado 2020.2，同 part `xc7z020clg484-2`）
是一份**在 AC820 上真跑过 AD9361** 的工程。它的 `toppin.xdc` 与我们的定表**逐脚吻合**，
构成对 §0.2 结论的**第三方独立验证** —— 而且这条证据**不依赖我们对原理图的任何解析**：

| 例程约束的端口 | 例程给的 IOSTANDARD | 例程引脚 | Vivado 查 Bank | 我们的定表 |
|---------------|--------------------|---------|---------------|-----------|
| `rx_clk_in_p/n` | **`LVDS_25 DIFF_TERM TRUE`** | M19 / M20 | **34** | 附二 · LA00_CC |
| `rx_data_in_p/n[0..5]` | **`LVDS_25 DIFF_TERM TRUE`** | J21/J22 … K16/L16 | **34** | 附二 · LA02–LA07 |
| `tx_clk_out_p/n` | **`LVDS_25`** | P17 / P18 | **34** | 附二 · LA08 |
| `tx_data_out_p/n[0..5]` | **`LVDS_25`** | J18/K18 … J20/K21 | **34** | 附二 · LA10–LA15 |
| `spi_csn/clk/mosi/miso` | **`LVCMOS25`** | B16 / B17 / D22 / C22 | **35** | 附二 · LA26/LA27 |
| `ctrl_in[0..3]`, `en_agc`, `sync_in`, `resetb` | **`LVCMOS25`** | A18…F16 | **35** | 附二 · LA19/LA24/LA25/LA28 |

**判读**：例程作者把 **Bank 34 的信号全部约束成 `LVDS_25`**（含 `DIFF_TERM TRUE`），
把 **Bank 35 的约束成 `LVCMOS25`**。`LVDS_25` 在 HR bank 里**要求 VCCO = 2.5V**（UG471），
所以这份**能在 AC820 上跑起来**的工程本身就是「Bank 34 的 VCCO 实际是 2.5V」的行为证据。
这与 §0.2 的矢量追踪、§0.5 的 TP8 实测**三方一致**。

> **注意**：XDC 的 `IOSTANDARD` 是**声明要求**，不设置 VCCO —— Vivado 只是**反推** VCCO 做
> DRC。所以「例程路由通过 DRC」本身**不是**板级证据；这里成立的是**例程真的跑通了**这一
> 行为事实。单看 XDC 不能下结论。

**附带的软件层信息**（详见 §2.4 与 `docs/rf/example_project_notes.md`）：该例程是
**PS SPI + 裸机** 架构（`config.h` 定义 `AXI_ADC_NOT_PRESENT`，即**不走 ADI AXI-DMA**），
数据面走自定义 PL 逻辑 + AXI GPIO/BRAM 与 PS 交互 —— 这证明 **B4（`axi-dmac`）不是唯一路线**。

---

## 1. 已查明的硬事实（证据充分）

### 1.1 AC820 FMC 插座的引脚分布

证据：`<LOCAL_PATH>引脚信息\FMC接口.xlsx`（厂商原始引脚表）+ Vivado 2025.2
`get_package_pins` 查 xc7z020clg484-2 的 BANK 属性（脚本见本文末）。

**74 个 FMC 信号引脚，横跨两个 IO Bank：**

| Bank | FMC 信号数 | VCCO 来源 | 证据 |
|------|-----------|-----------|------|
| **Bank 34** | 38 | **`VCCIO34` ≡ `VCCIO35` ≡ FMC `VADJ_3`，同一路**（P18 可选 LDO）；实测 **2.5V** | PyMuPDF 矢量追踪（带 junction dot）+ FB20 为 DNI + TP8 实测（§0.2/§0.5） |
| **Bank 35** | 36 | 同上（与 Bank 34 是同一网络） | P18 = A2005WV-2X4P 选 U35 SPX3819 反馈电阻：R212 48.7K→2.5V / R211 21.5K→1.8V / R214 10K→1.5V，`VOUT=1.235×(1+R1/R2)` |

**结论（2026-09-16 更正）**：FMC 的 LA 引脚横跨 Bank 34/35，**但这两个 bank 的 VCCO 是同一路、
都可调**（原 9-15 版「Bank 34 那一半被板载 LED 固定成 3.3V」是错的）。整条 FMC 数字接口
（Bank 34 数据面 + Bank 35 控制面）**统一由 P18 选择 2.5V**，与 AD9361 的接口电平要求（§2）相容。

### 1.2 P18 跳线同时控制 Bank 34 与 Bank 35（2026-09-16 更正）

**原 9-15 版标题「P18 跳线只控制 Bank 35」是错的。** 原判据是原理图 `pdftotext` 文本
（`/tmp/cb2_txt.txt:1301` 附近）只看到 P18 段旁边标着 `VCCIO35`，就推断 `VCCIO34` 不在该
网络里 —— 这正是本文件反复警告的**文本层错位**问题（已第四次踩）。

**矢量级证据**（PyMuPDF，`AC820-CB第二版/AC820-CB.pdf` 第 13 页，`set_rotation(0)` 后取
`get_drawings()` 的线段，按端点与 junction dot 追踪）：`VCCIO34` 与 `VCCIO35` 两个网络标签
**落在同一条实链上**，而该链同时接到 FMC 连接器的 **`VADJ_3`（G39）**。P18 通过
R212/R211/R214 改 U35 SPX3819 的反馈分压比 → 输出网络 = `VCCIO34` = `VCCIO35` = `VADJ_3`。

**板端实测（2026-09-16）**：该网络上的测试点 **TP8 = 2.5V**，两个 bank 都拿到 2.5V。

### 1.3 FMC 与现有设计无直接引脚冲突

**2026-09-15 强化**：不再只比 `led_btn.xdc`，而是拿厂商**全板资源表**（128 行）与
厂商 **FMC 表**（74 个 FPGA 引脚）**逐引脚交叉比对** —— **重合数为 0**。
即板上所有外设（TFT、PL 以太网 RGMII、Camera、HDMI/HDMI-I2C、DIP 开关、LED、按键、
TP_RST、DVI、通用扩展口）**没有一个**落在 FMC 占用的引脚上。

| 现有信号 | 引脚 | Bank | 是否与 FMC 冲突 |
|---------|------|------|----------------|
| LED0-7 | P20 P21 R20 R21 R19 T19 T16 T17 | 34 | **否**（FMC 在 Bank 34 用的是 M19–M22/N17–N22/P16–P22 等，不重叠）。**2.5V 下安全**：共阴灌电流，IO 上无 3.3V（§0.6） |
| TP_RST | R15 | 34 | **否**（只进 LCD FPC 第 35 脚，**无上拉**，由 FPGA 直驱；§0.6） |
| FPGA_KEY0/1 | H18 H19 | 35 | **否**（上拉 4.7K 接 `VCCIO35` = 2.5V 轨，非 VCC3P3） |
| TP_INT / TP_SCL / TP_SDA | **不在 FPGA 上** | — | **否**：是 **PS I2C0** 经 PCA9306DCUR（U19，CB p14）电平转换后引到 LCD FPC（§0.6） |
| DIP 开关 SW0-7 | G21 G20 F19 G19 G17 H17 E20 E19 | 35 | **否** |
| HDMI_I2C_SCL/SDA | G22 H22 | 35 | **否** |
| Camera_D0-7/PCLK/HREF/VSYNC/SDA/SCLK/XCLK | U17 V17 Y20 Y21 V18 V19 W20 W21 Y18 T21 U21 T22 U20 U22 | 33 | **否** |
| **PL 以太网 RGMII（15 线）** | W17 Y15 AA14 Y14 W15 U16 W16 U15 W18 Y16 AA18 V20 AB20 AA19 Y19 | **33** | **否**（见 §1.4） |
| TFT 显示 / 触摸复位（21 线） | V22 AB19 AB14 U14 U19 AB21 AA17 AB17 AA16 AB16 Y13 AA13 W22 AA22 AB22 AA21 AB15 W13 V13 V15 V14 | **全部 33**（Vivado 实测，21 个引脚） | **否**（且**不在 Bank 34**，见 §0.3） |
| DVI/HDMI-2 TX | AB7 AB6 AB5 AB4 Y4 AA4 AB2 AB1 | 13 | **否** |
| 通用扩展口 P10 / P7 | PS MIO；U12 U11 U10 U9 Y11 … | 501 / 13 | **否** |
| PL 50MHz 时钟 | Y9 | 13 | **否**（不同 Bank） |
| PL I2C | 见下 §1.3.1 | 35（推断） | **否** —— 板载 2 线总线；TFT 触摸走 PS 端 MIO50/51，不受影响 |

#### 1.3.1 关于 PL_I2C（唯一一个表外项，低风险）

原理图里有 `PL_I2C_SCL/SDA`（2 根线，接板载 I2C 切换开关，见 `cb2_txt.txt:1541`），
但**它不在厂商 `AC820+SOC+FPGA开发板管脚资源定义表.xlsx` 的 128 行里**，所以
**它的 FPGA 引脚无法从厂商表判定**。原理图给的是 `B35_L24_P/N` —— 实测该标签是
**SOM 板对板连接器的位置标号**（`B35_L18_P` 载 FMC-LA19，而 Vivado 查 `L18` 是
**Bank 34** 的球，两者不符），**不是 FPGA 引脚**，故不可用（§1.4 同款坑）。

**为何仍判"否"（推断，非证明）**：AD9361 只用 LA00–LA16 与 LA19–LA28；FMC 的
Bank 34/35 里未被 AD9361 占用的是 **LA17/LA18/LA29–LA33** 一带。板载 I2C 若要占用
FMC 引脚，只可能落在这些上 —— **不与 AD9361 的信号集冲突**。列**低风险待核项**，
不影响直插可行性判断（要么查原理图确认，要么上板实测）。

结论：**引脚号层面完全无冲突**（全板 128 项资源 vs FMC 74 引脚，重合 0）；电气层面也**已无阻塞**
—— `VCCIO34/35` 是同一路可调供电，实测 2.5V（§0.2/§0.5）。

### 1.4 板载外设与 FMC 引脚**无**重合（2026-09-15 更正，原判据已推翻）

**原判据是错的。** 本节初版（同日早些时候）称"`PL_ENET1_*` 与 FMC-LA17/18/23/25/26/27
同名网络，同一根线接两个器件" —— 那是从原理图 `pdftotext` 输出（`cb2_txt.txt:88-104`）
**整列错位**读出来的，与 §附二 警告过的是**同一个 bug**（该页把 SOM 板对板连接器表与
FMC 表并排排版，文本层把不同连接的列粘到同一行）。

**更正后的证据（不经过原理图文本，全部来自厂商权威表 + Vivado）**：

| 检查 | 数据源 | 结果 |
|------|--------|------|
| `PL_ENET1` 的 15 个信号落在哪 | 厂商 `AC820+SOC+FPGA开发板管脚资源定义表.xlsx` | `RX_CLK W17 / RX_DV Y15 / RX_DATA0 AA14 / RX_DATA1 Y14 / RX_DATA2 W15 / RX_DATA3 U16 / GTX_CLK W16 / TX_EN U15 / TX_DATA0 W18 / TX_DATA1 Y16 / TX_DATA2 AA18 / TX_DATA3 V20 / MDIO AB20 / MDC AA19 / RESET Y19` |
| 这些引脚的 Bank | Vivado `get_package_pins` | **15 个全在 Bank 33**（与 TFT 同 bank） |
| `PL_ENET1` 引脚是否出现在 FMC 表 | 厂商 `FMC接口.xlsx`（74 引脚） | **一个都没有** |
| **全板外设**是否与 FMC 重合 | 两张厂商表逐引脚交叉比对（128 行资源表 vs 74 个 FMC 引脚） | **无一重合** |

**含义（结论）**：FMC 只占 Bank 34/35；板载外设（TFT、PL 以太网 RGMII PHY、Camera、
HDMI I2C、LED、按键、TP_RST）**没有一个与 FMC 共用引脚**。所以——

> **不存在"同一根线接两个器件"的问题，不需要找 0Ω/跳线断开手段。** 原 U6 作废。

**顺带更正一条用户认知**：板上有**两路**以太网 —— **ETH0 = PS 侧**（`PS_ENET0_*` →
RTL8211F-CG，走 MIO）；**ETH1 = PL 侧**（`PL_ENET1_*`，RGMII 全套 + 独立 PHY）。
"以太网接 PS 端"只对 ETH0 成立，但这**不影响结论**：PL 侧那路在 **Bank 33**，
FMC 根本不碰这个 bank。

---

## 2. AD9361 的电平与接口要求（芯片级，来自数据手册 Rev.D）

证据：`docs/reference/ad-fmcomms3-ebz-*.pdf`（实为 **AD9361 数据手册 Rev.D**，36 页）
+ ADI HDL `axi_ad9361` / `fmcomms2` 官方约束。**注意：这是芯片级要求，
具体模块（AD-FMCOMMS3 等）可能有电平转换，必须以模块手册为准（见 U13）。**

| 项 | 要求 | 与 AC820 的匹配性 |
|----|------|------------------|
| 数字接口模式 | LVDS 或 CMOS（并行） | 两种都受下面同一条 VCCO 约束 |
| `VDD_INTERFACE` 档位 | **1.2 V / 1.8 V / 2.5 V**（手册只有这三档，**无 3.3 V**） | **Bank 34/35 实测 2.5V → 正好 2.5V 档，匹配**（§0.5） |
| LVDS 差分电平 | 需 VCCO 1.8 V 或 2.5 V（FMC 侧惯例 2.5 V） | 同上 |
| CMOS 电平 | 随 `VDD_INTERFACE`，最高 **2.5 V** | **不是 3.3 V**（此前本表误写"3.3V 可选"，已更正） |
| 逻辑 I/O 绝对最大值 | `−0.3 V .. VDD_INTERFACE + 0.3 V`（2.5V 档 → **2.8 V**） | **当前 2.5V 配置不超限**；**若 P18 拨到 3.3V 则超限，有损伤风险**（§0.2） |
| 接口时钟 | DATA_CLK（AD9361 输出，最高 61.44 MHz） | 需专用时钟引脚 + 差分对（LA00_CC 已核为真差分对） |
| 帧同步 | RX_FRAME / TX_FRAME | 需普通 IO |
| SPI 配置 | 4 线 SPI（SCLK/SDI/SDO/CSB），最高 ~10 MHz | 见 §2.1 |
| 复位/使能 | RESETB、ENABLE、TXNRX | 需普通 IO |
| 参考时钟 | FMCOMMS3 板载 40 MHz TCXO | 模块自带，无需 AC820 提供 |

### 2.1 SPI 控制面在板端的可用性（已实测）

板上实测（2026-09-15，`pynq_ssh.py run`）：

```
Linux pynq 5.4.0-xilinx-v2020.2 #1 SMP PREEMPT ... armv7l
CONFIG_SPI=y / CONFIG_SPI_MASTER=y / CONFIG_SPI_XILINX=y / CONFIG_SPI_SPIDEV=y
但：/dev/spidev* 不存在（设备树里没有 SPI 节点）
    /dev/i2c*   不存在（设备树里 I2C 节点 disabled）
```

**含义**：SPI **控制器驱动编进去了**，但**没有设备树节点**，所以用户空间看不到 SPI。
要接 AD9361 必须先补设备树（SPI 节点 + AD9361 子节点）。

### 2.2 ADI 内核驱动在板端的存在性（已实测）

```
CONFIG_IIO=y / CONFIG_IIO_BUFFER=y / CONFIG_IIO_KFIFO_BUF=y
CONFIG_AXI_DMAC is not set          ← ADI 的 DMA 驱动缺失
CONFIG_CF_AXI_ADC / CONFIG_AD9361   ← 完全不存在
```

**含义**：ADI 的 `ad9361.c` 与 `cf_axi_adc` **不在**当前内核里。板端有内核构建树
（`/lib/modules/5.4.0-xilinx-v2020.2/build` 含 `Module.symvers` + gcc + make），
**理论上可以 out-of-tree 编模块**，且 IIO/SPI/dmaengine 符号都已导出（实测 85 个
`iio_*` 符号、`spi_sync`/`spi_register_controller`、`dmaenginem_async_device_register`）。

但 `CONFIG_AXI_DMAC is not set` 意味着 ADI 的 DMA 引擎**不是内核模块**，out-of-tree 编
`cf_axi_dds`/`axi-dmac` 需要它作为依赖 —— 这是**选 ADI 路线时**的软件阻塞点。
**但它不是唯一路线**：用户例程用 PS SPI + 自定义 PL 数据面跑通（§2.4），不需要它。

### 2.3 CMA 内存现状（已实测）

```
CmaTotal: 131072 kB (128 MB)
CmaFree:    3584 kB (3.5 MB)     ← 几乎耗尽
总内存:     1001 MB
```

**含义**：DDR 大文件环形播放（P5）已经吃掉了几乎全部 CMA。RF 的 DMA 缓冲
（I16+Q16 @ 4MSPS ≈ 16MB/s 单向）需要**额外**的 CMA 或改用其他分配路径。
这是必须重新规划的容量约束，不是"顺手加个缓冲"的事。

### 2.4 用户提供的例程给出的软件层证据（2026-09-16）

例程 `docs/reference/ps_ctrl_AD9361_FM_test_final_copy.zip`（Vivado 2020.2）在 AC820 上
**真跑通了 AD9361**，且**刻意绕开了 ADI 的 AXI-DMA 框架**：

| 项 | 例程的做法 | 对我们的意义 |
|----|-----------|-------------|
| 平台宏 | `config.h` 定义 **`AXI_ADC_NOT_PRESENT`**，`ADC_DMA_EXAMPLE`/`DAC_DMA_EXAMPLE` **全部注释掉** | **不用 `axi_adc`/`axi_dmac`** —— 证明 B4 的 DMA 框架**不是唯一路线** |
| SPI | **PS SPI0**（`XPAR_PS7_SPI_0_DEVICE_ID`），`XSpiPs`，`XSPIPS_CLK_PRESCALE_256`，`clk_pha=1, clk_pol=0` | 控制面走 PS SPI 即可，与 Linux 下 `spi-xilinx` 驱动同一控制器 |
| 复位 | `gpio_resetb = EMIO_RESET`（**PS EMIO**，编号 `54+2`），另用 `54+0..8` 一组 EMIO 做 TXNRX/ENABLE/SYNC/EN_AGC/CTL0-3 | AD9361 的 RESETB/ENABLE/TXNRX 可由 **PS EMIO** 驱动，不占 PL 引脚 |
| 参考时钟 | `reference_clk_rate = 40000000UL`（**40 MHz**） | 与我们板上 FMC 侧参考时钟方案一致（§附二） |
| 初始 LO | `rx/tx_synthesizer_frequency_hz = 2400000000`，后由 `ad9361_set_*_lo_freq` 改到 80 MHz | `ad9361_init` 只是起手值，运行时可改 |
| 采样率 | `ad9361_set_tx/rx_sampling_freq(phy, 60000000)` = 60 MSPS；带宽 30 MHz | **60 MSPS 在该硬件上被设过** —— 数据面速率上限的实测参考 |
| FIR | `ad9361_set_tx/rx_fir_config` + `set_*_fir_en_dis(1)`，**RX 用 `rx_fir_config_search`** | RX FIR 在宽带搜索模式下启用，非默认 bypass |
| AGC | RX1 设为 **Slow Attack AGC**（`RF_GAIN_SLOWATTACK_AGC`），增益 30 dB；TX 衰减 `10000 mdB` | 与 `backend_contract.md` §2 要求的「记录 AGC 开关」对齐 |
| PL 交互 | 自定义 `AXI_CTRL`（I_DELAY_EN/TAP、DDS_PHASE_INC、PHY_MODE）+ `axi_gpio_0` 双通道（载频/锁定回读） | **数据面在 PL 自定义逻辑里**，PS 只做控制 —— 与我们「PL 调制链 + PS 控制」的路线同构 |

**结论**：这条路线（PS SPI 控 AD9361 + 自定义 PL 数据面 + PS 经 AXI 寄存器交互）
**不依赖 `axi-dmac`**，因此 B4 不构成硬阻塞，只是**路线选择**问题。
详细笔记见 `docs/rf/example_project_notes.md`。

---

## 3. UNKNOWN 清单（必须由用户/厂商资料补齐）

按迁移方案的"缺失写 UNKNOWN"要求，以下全部未查证：

| # | 未知项 | 为什么关键 | 从哪里拿 |
|---|--------|-----------|---------|
| ~~U1~~ | ~~模块准确型号~~ | **已确认：AD-FMCOMMS3-EBZ**（2026-09-15） | 用户提供 |
| U2 | **FMCOMMS3 模块原理图 / 引脚表** | 用户**暂无**；但 §0.1 已用 ADI 官方 XDC + 厂商 xlsx + Vivado 三方对齐**替代**，LA 映射已可定表 | 模块厂商资料包（可选） |
| U3 | **模块是否带电平转换** | **不再是门禁**（2026-09-16）：Bank 34/35 实测 2.5V，与 FMCOMMS3 官方要求的 VADJ=2.5V 一致，无需依赖模块侧电平转换 | 模块原理图（若买到；不阻塞任务 1） |
| U4 | **模块参考时钟来源** | FMCOMMS3 **自带晶振**（板载 40MHz TCXO 给 AD9361 做参考） | 模块手册 |
| U5 | **模块供电需求** | FMCOMMS3 从 FMC 取 12V/3.3V，需核 AC820 的 FMC 供电能力 | 模块手册 + AC820 原理图 |
| ~~U6~~ | ~~PL_ENET1 与 FMC LA 的复用是否有断开手段~~ | **已闭（2026-09-15 更正）**：原判据（pdftotext 读原理图）整列错位读错；厂商两张权威表 + Vivado 交叉比对证明**板载外设与 FMC 74 个引脚无一重合**，无需断开手段。见 §1.4 | 已闭 |
| U7 | ~~LPC 还是 HPC~~ | **已查：160pin LPC**（`ASP-134603-01`），且 LA00–LA33 排列与标准 LPC 定义逐引脚吻合 | 已闭 |
| U8 | **厂商是否有 AC820 + AD9361 的现成参考设计** | 若有，工程量天差地别 | 论坛 tid=29733/30096 / 厂商支持 |
| U9 | ~~FMC 差分对可用性~~ | **已查：LA00_CC / LA01_CC 是 `IS_DIFFERENTIAL=1, IS_CLK_CAPABLE=1` 的真差分对**（Vivado） | 已闭 |
| ~~U10~~ | ~~P18 跳线能否同时影响 Bank 34~~ | **已闭（2026-09-16）**：**能**。矢量追踪 + FB20 为 DNI + 实测 TP8=2.5V 三方一致，VCCIO34/35 是同一网络 | 已闭（实测） |
| ~~U11'~~ | ~~Bank 34 上的 LED0–7 / 按键 / TP_RST 在 2.5V 下是否正常~~ | **已闭（2026-09-16）**：LED 共阴灌电流、FPGA_KEY 上拉接 VCCIO35、TP_RST 无上拉、TP_I2C 走 PS I2C0 不在 FPGA 上。**残余**：TP_RST→GT911 余量 0.19V，需上板实测触摸 | 已查原理图（§0.6）；触摸待上板实测 |
| ~~U12~~ | ~~AD9361 数字接口供电上限是否真为 2.5V~~ | **已闭：是 2.5V，无 3.3V 档**（见下） | AD9361 数据手册（已到手） |
| **U13** | **FMCOMMS3 板载是否有 FMC 侧电平转换** | 若模块自带 level shifter 把 2.5V 与载体隔离，则 3.3V 载体可能可行 | 模块原理图（用户暂无 → 保持 UNKNOWN，**不做 3.3V 上电实验**） |

### U12 关闭依据（AD9361 数据手册 Rev.D，`docs/reference/ad-fmcomms3-ebz-*.pdf`）

该文件实为 **AD9361 芯片数据手册**（36 页），不是板级原理图。其中：

- 数字接口供电 `VDD_INTERFACE` 的电流表只有 **1.2 V / 1.8 V / 2.5 V** 三档（Tables 2/3/4），
  数字时序规格也只按 1.8 V 与 2.5 V 给出（`DIGITAL DATA TIMING (CMOS) VDD_INTERFACE = 1.8 V / 2.5 V`）。
  **没有 3.3 V 档。**
- 绝对最大值表：`VDD_INTERFACE to VSSx: −0.3 V to VDD_INTERFACE + 0.3 V` —— 逻辑 I/O 的
  应力上限是 **VDD_INTERFACE + 0.3 V**（2.5 V 配置下即 **2.8 V**）。

**含义**：3.3 V 数字电平不只是"不能用"，而是**超出绝对最大值**。这排除了"用 3.3V 的 LVDS 变体
（HR 组的 `LVDS`）绕过"这条路 —— **芯片本身接不了 3.3V**。也正是因此，**P18 绝不可拨到 3.3V 档**
（§0.2）；而 2.5V 档下这条限制不触发。

---

## 4. 结论（2026-09-16 改判）

| 判定 | 内容 |
|------|------|
| **已证实** | AC820 有 160pin LPC FMC（`ASP-134603-01`）；FMC 引脚横跨 Bank 34(38) / Bank 35(36)，**两 bank 的 VCCO 是同一网络**（`VCCIO34` ≡ `VCCIO35` ≡ FMC `VADJ_3`，由 P18 可选）；**全板 128 项外设引脚与 FMC 的 74 个引脚无一重合**（§1.4）；TFT 与 PL 以太网同在 Bank 33；SPI 控制器驱动在但无设备树节点；ADI 驱动不在内核；CMA 只剩 3.5MB；**Bank 34/35 均为 HR 组**；**实测 TP8 = 2.5V**（§0.5） |
| **判定：可以直插** | AD9361 的 LVDS 数据/时钟整组（LA00–LA16）落在 **Bank 34**、控制面（LA19–LA28）在 **Bank 35**，**两半 VCCO 都 = 2.5V** → `LVDS_25` 与 `LVCMOS25` 同时成立，AD9361 `VDD_INTERFACE` 也在 2.5V 档（abs max 2.8V，不超限）。**P18 保持 2.5V 档即可**（§0.2） |
| **⚠ 禁止** | **把 P18 拨到 3.3V 档**（超 AD9361 `VDD_INTERFACE` 绝对最大值，损伤风险）；1.5V/1.8V 档会让 `LVDS_25` 不成立。**不做 3.3V 上电实验**（U13 未明） |
| **U11' 已闭（2026-09-16）** | LED0–7 是**共阴灌电流**（手册图26 + CB p9，无 3.3V 通路）；FPGA_KEY 上拉接 `VCCIO35`（2.5V）；TP_RST 无上拉、只进 LCD FPC 第 35 脚；**TP_INT/SCL/SDA 是 PS I2C0 经 PCA9306 转换，不在 FPGA 上**（§0.6）。**残余**：TP_RST→GT911 余量 0.19V，需上板实测触摸 |
| **软件面：路线选择，非硬阻塞** | `CONFIG_AXI_DMAC is not set` 只影响**选 ADI AXI-DMA 路线**；用户例程证明 PS SPI + 自定义 PL 数据面可行（§2.4） |
| **已排除的风险** | ~~不能假设"FMC Bank VCCIO 统一可调"~~ → **本条已推翻**，它**确实统一可调、两半同路**；不能假设"Zynq-7020 就能接 AD9361"（CMA/DMA 约束仍在）；**不能假设 FMC 与板载外设有复用冲突**（已查：128 项外设 vs 74 个 FMC 引脚，零重合）；**不要用原理图 `pdftotext` 输出做任何定表或连通判定**（已**四次**踩坑：LA→bank 错位、U6 误报、FB20「硬接 3.3V」误报）；**`IOSTANDARD` 不设置 VCCO**，别把"DRC 通过"当板级证据（§0.6） |

**给用户的结论**：电气层已经没有阻塞。**P18 保持在 2.5V 档（实测 TP8 = 2.5V 即为此状态）
就可以直插 AD-FMCOMMS3-EBZ**，不需要改板、不需要中介板。**U11' 也已闭合**（只剩上板实测
触摸这一项）。剩下的是：B5（换镜像前备份 SD 卡）、B6（CMA 只剩 3.5MB）、
以及"走 ADI DMA 路线还是 PS-SPI + 自定义 PL 数据面"这个**路线选择**。
**在 U13 未明前仍不做 3.3V 上电实验。**

---

## 附：复现本表所用命令

```bash
# FMC 引脚 → FPGA 引脚（厂商 xlsx）
python -c "import zipfile,xml.etree.ElementTree as ET; ..."  # 见 docs/rf/extract_fmc_pins.py

# 全板资源表（128 行）→ 逐引脚与 FMC 表交叉比对（§1.3/§1.4 的"零重合"结论）
#   源: <LOCAL_PATH>引脚信息/AC820+SOC+FPGA开发板管脚资源定义表.xlsx
#   读法: 2 列的续行表示同一"功能名称"；只取第 3 列/第 2 列里的 FPGA 引脚
python - <<'PY'
import zipfile, xml.etree.ElementTree as ET
ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
def rows(p):
    z=zipfile.ZipFile(p); ss=[]
    try:
        r=ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in r.iter(ns+'si'): ss.append(''.join(t.text or '' for t in si.iter(ns+'t')))
    except KeyError: pass
    out=[]
    for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).iter(ns+'row'):
        c=[]
        for x in row.iter(ns+'c'):
            v=x.find(ns+'v')
            if v is not None: c.append(ss[int(v.text)] if x.get('t')=='s' else v.text)
        if c: out.append([s.strip() for s in c])
    return out
fmc=rows('<LOCAL_PATH>引脚信息/FMC接口.xlsx')
fpga={p for c in fmc if len(c)==4 and c[0]!='FMC引脚号' for p in (c[1], c[3])}
res=rows('<LOCAL_PATH>引脚信息/AC820+SOC+FPGA开发板管脚资源定义表.xlsx')
hit=[(c[0],c[1],c[-1]) for c in res[1:] if c[-1] in fpga]
print('重合数:', len(hit), hit)   # -> 0 []
PY

# FPGA 引脚 → Bank（Vivado 2025.2）
cat > /tmp/bankq.tcl <<'EOF'
create_project -in_memory -part xc7z020clg484-2
link_design -part xc7z020clg484-2
foreach p {P16 R16 ...} { puts "PIN $p BANK [get_property BANK [get_package_pins -quiet $p]]" }
EOF
"<LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat" -mode batch -nojournal -nolog -source /tmp/bankq.tcl

# 原理图文本（P18 / VCCIO / PL_I2C / LED / TP_RST）
pdftotext -layout "<LOCAL_PATH>硬件图纸&封装/AC820-CB第二版/AC820-CB.pdf" /tmp/cb2.txt
grep -n "VCCIO34\|VCCIO35\|VADJ_3\|FB20\|PL_I2C" /tmp/cb2.txt
#   ⚠ 仅用于"确认 P18 不覆盖 Bank 34"这类单点事实；**不要**用它把
#     FMC-LAxx ↔ 引脚 / 网表 ↔ 器件 配对（并排表格的文本层会整列错位）。

# 用户例程的引脚约束（§0.6 / example_project_notes.md）—— 第三方独立验证
unzip -p "docs/reference/ps_ctrl_AD9361_FM_test_final_copy.zip" \
  "*/ZYNQ_AD9361.srcs/constrs_1/new/toppin.xdc"
#   把其中的引脚列表喂给上面那段 bankq.tcl，即可复现「Bank 34 全 LVDS_25」的结论。

# 板端内核审计
MSYS_NO_PATHCONV=1 python "<LOCAL_PATH>" run \
  "uname -a; ls /sys/bus/iio/devices/; ls /dev/spidev*; zcat /proc/config.gz | grep -E 'SPI|IIO|AD9361|AXI_DMAC'; grep Cma /proc/meminfo"
```

---

## 附二：AD9361 全信号 → AC820 Bank 定表（权威版）

来源：ADI `fmcomms2/zc706/system_constr.xdc`（注释同时给 **FMC 引脚号 + LA 名**）
+ 厂商 `FMC接口.xlsx`（FMC 引脚号→FPGA 引脚号） + Vivado `get_package_pins`（FPGA 引脚→Bank）
+ 厂商 `AC820+SOC+FPGA开发板管脚资源定义表.xlsx`（全板资源 vs FMC 的重合性检查，§1.3/§1.4）。
**不经过原理图文本解析**，无列错位风险。

| AD9361 信号 | FMC 引脚 | LA | 要求电平 | AC820 引脚 | Bank |
|------------|---------|----|---------|-----------|------|
| `rx_clk_in_p/n` | G6 / G7 | LA00_CC | LVDS_25 | M19 / M20 | **34** |
| `rx_frame_in_p/n` | D8 / D9 | LA01_CC | LVDS_25 | N19 / N20 | **34** |
| `rx_data_in_p/n[0]` | H7 / H8 | LA02 | LVDS_25 | J21 / J22 | **34** |
| `rx_data_in_p/n[1]` | G9 / G10 | LA03 | LVDS_25 | N17 / N18 | **34** |
| `rx_data_in_p/n[2]` | H10 / H11 | LA04 | LVDS_25 | J15 / K15 | **34** |
| `rx_data_in_p/n[3]` | D11 / D12 | LA05 | LVDS_25 | L17 / M17 | **34** |
| `rx_data_in_p/n[4]` | C10 / C11 | LA06 | LVDS_25 | P16 / R16 | **34** |
| `rx_data_in_p/n[5]` | H13 / H14 | LA07 | LVDS_25 | K16 / L16 | **34** |
| `tx_clk_out_p/n` | G12 / G13 | LA08 | LVDS_25 | P17 / P18 | **34** |
| `tx_frame_out_p/n` | D14 / D15 | LA09 | LVDS_25 | N22 / P22 | **34** |
| `tx_data_out_p/n[3]` | C14 / C15 | LA10 | LVDS_25 | M21 / M22 | **34** |
| `tx_data_out_p/n[0]` | H16 / H17 | LA11 | LVDS_25 | J18 / K18 | **34** |
| `tx_data_out_p/n[1]` | G15 / G16 | LA12 | LVDS_25 | K19 / K20 | **34** |
| `tx_data_out_p/n[2]` | D17 / D18 | LA13 | LVDS_25 | R18 / T18 | **34** |
| `tx_data_out_p/n[4]` | C18 / C19 | LA14 | LVDS_25 | L21 / L22 | **34** |
| `tx_data_out_p/n[5]` | H19 / H20 | LA15 | LVDS_25 | J20 / K21 | **34** |
| `enable` / `txnrx` | G18 / G19 | LA16 | LVCMOS25 | J16 / J17 | **34** |
| `gpio_en_agc` / `gpio_sync` | H22 / H23 | LA19 | LVCMOS25 | B21 / B22 | 35 |
| `gpio_status[0]/[1]` | G21 / G22 | LA20 | LVCMOS25 | F21 / F22 | 35 |
| `gpio_status[2]/[3]` | H25 / H26 | LA21 | LVCMOS25 | D16 / D17 | 35 |
| `gpio_status[4]/[5]` | G24 / G25 | LA22 | LVCMOS25 | A21 / A22 | 35 |
| `gpio_status[6]/[7]` | D23 / D24 | LA23 | LVCMOS25 | C17 / C18 | 35 |
| `gpio_ctl[0]/[1]` | H28 / H29 | LA24 | LVCMOS25 | A18 / A19 | 35 |
| `gpio_ctl[2]/[3]` | G27 / G28 | LA25 | LVCMOS25 | A16 / A17 | 35 |
| `spi_csn` / `spi_clk` | D26 / D27 | LA26 | LVCMOS25 | B16 / B17 | 35 |
| `spi_mosi` / `spi_miso` | C26 / C27 | LA27 | LVCMOS25 | D22 / C22 | 35 |
| `gpio_resetb` | H31 | LA28 | LVCMOS25 | F16 | 35 |

**读法**：`LA00–LA16`（整条高速数据/时钟 + `enable`/`txnrx`）**全在 Bank 34**；`LA19–LA28`
（SPI / 复位 / GPIO）在 Bank 35。**两半的 VCCO 是同一路，实测 2.5V（2026-09-16，§0.5）** ——
故 `LVDS_25`（数据面）与 `LVCMOS25`（控制面）**同时成立**，可以直接照此生成引脚约束。

**旁证**：ADI 的 ZedBoard（同为 Zynq-7020 + LPC）用**完全相同的 LA 分配**（53 端口逐一相同），
其 `rx_clk_in_p` 同样是 `M19`/Bank 34，同样约束 `LVDS_25`。ZedBoard 的 VADJ 跳线**同时供
Bank 34/35**，所以它能跑 —— **AC820 与它同构**：`VCCIO34` 与 `VCCIO35` 本来就是同一个网络
（§0.2），P18 一路供两半，实测 2.5V。故 **ZedBoard 能跑的组合，AC820 也成立**。

**第三方验证（2026-09-16）**：用户例程 `ps_ctrl_AD9361_FM_test_final_copy` 的 `toppin.xdc`
**逐脚吻合本表**，且把 Bank 34 全部约束为 `LVDS_25`、Bank 35 全部约束为 `LVCMOS25` ——
该例程**在 AC820 上真跑通过**，是对 2.5V 结论的行为证据（§0.6 / `example_project_notes.md`）。

### 复现命令

```bash
# ① 信号 → FMC 引脚号 + LA 名（ADI 官方，权威；ZedBoard 版用于旁证）
curl -sL https://raw.githubusercontent.com/analogdevicesinc/hdl/master/projects/fmcomms2/zc706/system_constr.xdc
curl -sL https://raw.githubusercontent.com/analogdevicesinc/hdl/master/projects/fmcomms2/zed/system_constr.xdc

# ② FMC 引脚号 → FPGA 引脚号（厂商 xlsx）
python docs/rf/extract_fmc_pins.py --xlsx "<LOCAL_PATH>引脚信息/FMC接口.xlsx"

# ③ FPGA 引脚 → Bank + bank 类型（Vivado）
#    get_property BANK [get_package_pins M19]        → 34
#    get_property BANK_TYPE [get_iobanks 34]         → BT_HIGH_RANGE

# ④ VCCIO34 供电来源（AC820 原理图；仅用于确认 P18 不覆盖 Bank 34）
pdftotext -layout "<LOCAL_PATH>硬件图纸&封装/AC820-CB第二版/AC820-CB.pdf" /tmp/cb2.txt
grep -n "VCCIO34\|VCCIO35\|VADJ_3" /tmp/cb2.txt
```

> **不要用 `pdftotext` 解析出的 LA→bank 引脚做结论**：本文件第一版曾如此，结果整列错位一行。
> 定表一律以 ①②③ 为准。