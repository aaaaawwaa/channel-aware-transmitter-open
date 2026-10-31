# AD9361 迁移：版本矩阵与工具链审计

任务 0 交付物之二。记录**当前工程基线**（可复现）与**目标 RF 链的版本选择问题**。

原则：不把"忽略版本检查"当成迁移成功。ADI 的 HDL 分支与 Vivado 版本强绑定，本工程是
2025.2，两者是否能对齐是必须显式回答的问题，不是可以绕过的细节。

---

## 1. 当前工程基线（已实测，2026-09-15）

### 1.1 源码

| 项 | 值 | 证据 |
|----|----|------|
| HEAD | `730d07b` | `git rev-parse HEAD` |
| 分支 | `master` | `git status` |
| 关键提交 | `ecaedf1`（数据页）`30f9569`（真实 DDR PP 状态）`730d07b`（PLAY/USERBUF 真值） | `git log --oneline` |
| PYNQ 侧离线测试 | 467 项通过 | `pynq_overlay/sw/tests/` |
| 上位机测试 | 706 项（历史基线，不与 467 相加） | `host/tests/` |

### 1.2 两条独立构建链（**不能混用产物与 PASS**）

| 目标 | 构建入口 | 产物 | 既有验收 |
|------|---------|------|---------|
| PL + 裸机 PS | `scripts/build_bitstream.py` | `awg/awg.runs/impl_1/host_bd_wrapper.bit` | `test_bridge.py` / `scope_verify.py` |
| PYNQ 合并目标 | `pynq_overlay/build.tcl` | `pynq_overlay/out_merged/awg_merged.bit` + `.hwh` | `ddr_e2e.py` / `p7_e2e.py` |
| **新 RF 目标** | 待定（见 §3） | 待定 | 新的 RF 专项测试 |

### 1.3 板端运行时（已实测）

```
uname -a   : Linux pynq 5.4.0-xilinx-v2020.2 #1 SMP PREEMPT Wed Sep 25 01:43:53 UTC 2024 armv7l
PYNQ       : 2.7.0
REVISION   : Release 2024_09_25 59515a9 / Board 2024_09_25 59515a9 (Xilinx/PYNQ.git)
内存       : 1001 MB 总 / 540 MB 空闲
CMA        : CmaTotal 131072 kB / CmaFree 3584 kB  ← 几乎耗尽
cmdline    : root=/dev/mmcblk0p2 rw ... uio_pdrv_genirq.of_id="generic-uio" clk_ignore_unused
```

| 项 | 值 | 对 RF 的含义 |
|----|----|-------------|
| 内核版本 | **5.4.0-xilinx-v2020.2** | 比 ADI 主线旧很多；ADI 驱动要按此版本适配 |
| 内核构建树 | `/lib/modules/5.4.0-xilinx-v2020.2/build`（含 `Module.symvers`） | **可编 out-of-tree 模块** |
| 编译器 | `/usr/bin/gcc` + `/usr/bin/make` | 齐全 |
| IIO | `CONFIG_IIO=y`，仅 `iio:device0` = `xadc` | 有框架，无 AD9361 设备 |
| SPI | `CONFIG_SPI_XILINX=y` / `SPI_SPIDEV=y`，**但 `/dev/spidev*` 不存在** | 驱动在，**设备树缺节点** |
| I2C | `/dev/i2c*` 不存在（设备树 disabled） | 见记忆 `vendor-pynq-image-has-kernel-build-tree.md` |
| AXI DMA | **`CONFIG_AXI_DMAC is not set`** | **选 ADI AXI-DMA 路线时的阻塞**（2026-09-16 降级：用户例程用 PS SPI + 自定义 PL 数据面，不碰它 —— `compatibility.md` §2.4） |
| CMA | 128 MB 总，剩 3.5 MB | RF 缓冲需重新规划 |
| 可用内核符号 | 85 个 `iio_*`、`spi_sync`/`spi_register_controller`、`dmaenginem_async_device_register` | 导出齐，out-of-tree 编得动 |

---

## 2. 已知版本冲突

| 冲突 | 现状 | 影响 | 处理 |
|------|------|------|------|
| **Vivado 版本** | 本工程 **2025.2**；ADI `hdl_2026_r1` 对应 **2025.1** | ADI 的 `.bd`/IP 可能无法直接在本工程打开 | 见 §3 决策 |
| **内核版本** | 板端 **5.4.0-xilinx-v2020.2**；ADI 驱动主线基于更新内核 | `ad9361.c` 的 IIO API 可能与 5.4 有差异 | 需按 5.4 适配或取同期 ADI 内核 |
| **PYNQ 镜像** | 厂商 2.7（2024_09_25） | 换 ADI 镜像会丢 TFT/触摸/字体/标定 | **优先保留现有镜像 + 补驱动**，见 §3 |

---

## 3. 三条候选路线（任务 0 的 D 项：选工程量较小的）

### 路线 A：保留现有 PYNQ 镜像，补 out-of-tree 驱动 + 设备树

- **做法**：在 5.4.0-xilinx-v2020.2 上编 `ad9361.ko` + `cf_axi_adc.ko`（或等价），
  加 SPI 设备树节点，用现有 PL 构建链加 `axi_ad9361` IP。
- **优点**：TFT/触摸/面板/字体/标定/数据页**全部保留**；不换镜像。
- **缺点**：`CONFIG_AXI_DMAC is not set` 是障碍 —— ADI 的数据面依赖它。
  可能需要：(a) 重新编内核（工程量大，且厂商内核源码不一定全）；或
  (b) 用 PYNQ 自己的 DMA 路径绕过 ADI DMA（但那要自己写适配层）；
  或 (c) **照用户例程的思路完全不碰 ADI DMA**（见下，2026-09-16）。
- **阻塞**：~~`axi-dmac` 是否必须重编内核，需查证~~ → **已由用户例程实质回答，见下**。

### 路线 B：换用 ADI 的 RF 参考镜像（如 fmcomms 系列）

- **做法**：用 ADI 发布的 SD 镜像（含完整 IIO + AD9361 驱动 + libiio）。
- **优点**：软件栈现成，`iio_info`/`pyadi-iio` 开箱可用。
- **缺点**：**丢掉整块面板**（TFT/GT911/字体/标定/七页 UI/数据页全部要重做）；
  且 ADI 镜像**不支持 AC820 的板级外设**（LED/按键/TFT 控制器）——
  需要把整个 AC820 overlay 移植到 ADI 内核上，工程量**远大于**路线 A。
- **判定**：**不推荐**。与本工程"板子自洽"的核心决策（面板自持）直接冲突。

### 路线 C：no-OS 裸机（独立启动方案）

- **做法**：不跑 Linux，裸机 PS 直接驱动 AD9361（ADI 提供 no-OS 驱动）。
- **优点**：时序可控，无内核/设备树麻烦。
- **缺点**：**与现有 PYNQ 面板完全隔离** —— 变成两套独立固件，用户要切启动镜像。
  且 ADI no-OS 的 AD9361 驱动与 Linux 驱动**不能并发**写同一芯片。
- **判定**：作为**独立备选/驱动行为参考**，不作为主线。

### 推荐：路线 A（补驱动），但先回答一个硬问题

> **`axi-dmac` 是否必须重编内核？**

- 若**是** → 路线 A 的成本大幅上升，需要评估厂商内核源码完整度；
  可能要退到路线 C 做 RF 独立验证，再谈合并。
- 若**否**（存在其他 DMA 路径）→ 路线 A 可行，优先。

**2026-09-16 更新：这个问题已被用户例程实质性回答。**
`ps_ctrl_AD9361_FM_test_final_copy`（Vivado 2020.2，同 part）在 AC820 上**真跑通了 AD9361**，
其 `config.h` 定义 **`AXI_ADC_NOT_PRESENT`**，**完全不使用 ADI 的 `axi_adc`/`axi_dmac`** ——
数据面走自定义 PL 逻辑 + AXI GPIO/BRAM。故：

- **`axi-dmac` 不是必经之路** —— 存在"PS SPI 控 AD9361 + 自定义 PL 数据面"这条
  **不需要重编内核**的路线（详见 `compatibility.md` §2.4 与 `example_project_notes.md`）。
- 但它**不是**直接可用的 Linux 方案：例程是**裸机**（Vitis/OCM），我们要的是
  Linux/PYNQ。所以问题变成**路线选择**：
  - **A1**：走 ADI Linux 驱动（需 `axi-dmac` → 可能重编内核，或自己写 DMA 适配层）；
  - **A2**：照例程思路，Linux 下用 **spidev + 自定义 PL 数据面**（驱动/设备树负担小，
    但要自己写 PL 与用户空间的接口）。
- **两者的共同前置都还在**：SPI 设备树节点（§1）、CMA 容量（§1.3）。

---

## 4. 与原后端的关系（不混淆产物）

| 项 | 原 ACM2108 链 | 新 RF 链 |
|----|--------------|---------|
| 构建 | `scripts/build_bitstream.py` | 待定（§3） |
| 位流 | `host_bd_wrapper.bit` | 独立的 RF 位流 |
| 验收 | `test_bridge.py` 195 项 / `scope_verify.py` 61 项 | 新的 RF 专项测试 |
| 采样率 | 50MHz 时钟域 | AD9361 的 61.44MSPS 上限 |
| 量化 | 8-bit + 偏移二进制 + 极性补偿 | 12-bit（若 PL 侧能提供） |
| 状态来源 | `_user_status()` / `axi_gpio_8` bit2 | RF 自己的驱动/IP 诊断 |

**严禁**：把 ADI 参考 DMA 环回通过描述为本工程 PL 链通过；
把 461 项离线基线当作 RF 验收；把 host 的 706 项与 467 项相加。

---

## 5. 待补齐（给用户）

| # | 需要的信息 |
|---|-----------|
| V1 | AD9361 模块的**准确型号 + PCB 版本** |
| V2 | 模块的**原理图/引脚表** |
| V3 | 模块是否带**电平转换**（决定 Bank 34 的 3.3V 能否用） |
| V4 | 厂商是否有 **AC820 + AD9361 现成参考设计** |
| V5 | 是否有**可恢复的原启动介质**（换镜像前必须备份） |

在 V1/V2 到手前，`compatibility.md` 的引脚约束部分只能停在 UNKNOWN，
`backend_contract.md`（软件边界）可以独立完成 —— 这是任务 0 的实际可交付范围。