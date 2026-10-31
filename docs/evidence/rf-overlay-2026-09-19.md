# 独立 RF Overlay 综合/实现摘要（2026-09-19）

> **性质**：公开**脱敏摘要**，不是原件。原件在开发机 `<LOCAL_EVIDENCE>/rf_overlay/vivado/rf_overlay/rf_overlay.runs/impl_1/`，含开发机绝对路径与主机名，未随仓库；本文件字节与原件不同，不能用本副本复算。
> **对象**：独立 RF Overlay（`rf_bd_wrapper`，AD9361 板级 bring-up 目标，构建入口 `rf_overlay/build.tcl`）。
> **它不是交付 bit**：交付产品使用的是合并产品 Overlay（见 [merged-overlay-2026-09-24.md](merged-overlay-2026-09-24.md)）。本目标的报告只用于说明独立 RF 链在 FPGA 侧的实现结果。

## 1. 报告头（原件照抄）

| 项 | 值 |
|---|---|
| 工具 | `Vivado v.2025.2 (win64) Build 6299465 Fri Nov 14 19:35:11 GMT 2025` |
| 器件 | `xc7z020clg484-2`；时序报告写 `7z020-clg484` |
| Speed File | `-2  PRODUCTION 1.12 2019-11-22` |
| 设计 | `rf_bd_wrapper`；`Design State`：`Fully Placed` / `Routed` / `Fully Routed` |
| 报告日期 | 2026-09-19（资源 14:25:09；DRC 14:27:05；时序 14:27:16；功耗 14:27:22） |

**原件 SHA-256**（`<LOCAL_EVIDENCE>/rf_overlay/vivado/rf_overlay/rf_overlay.runs/impl_1/`，2026-10-31 只读复核）：

| 原件 | SHA-256 |
|---|---|
| `rf_bd_wrapper_utilization_placed.rpt` | `52be159c0605e348bdf8b76e1d348b3c4305d0a7598debca9deb4576e7a81a4e` |
| `rf_bd_wrapper_timing_summary_routed.rpt` | `59a25984a86015c61f80bc09c7f50b36a16363e4121fefde920ab8c7da891cbf` |
| `rf_bd_wrapper_power_routed.rpt` | `caf326ea601929a714a6c7551e9f716cdd6c9ead1835821772197cd2f5b1bf4a` |
| `rf_bd_wrapper_route_status.rpt` | `4155b8d0c5b0b3b46addec2fddb8ff15270f297ecc7a3eb3918f7f0f800898a0` |
| `rf_bd_wrapper_drc_routed.rpt` | `22aad39b898cd431592d457f84ab781de2d3d16f3cef6e4cf51eeb2a4d377b22` |
| 构建产物 `../../out/rf_overlay.bit` | `b6a73e4de60169ba5d5e998d521ccc15f5e7da5922e9bc80dc1d0004744435d1` |
| 构建产物 `../../out/rf_overlay.hwh` | `6150d4ccd28c1716f1d550c7458d7e19587de3443c99ab8ba1765e8679c6dc9c` |

## 2. 资源占用（Fully Placed）

| 资源 | Used | Available | Util% |
|---|---:|---:|---:|
| Slice LUTs | 3294 | 53200 | 6.19 |
| · LUT as Logic | 3092 | 53200 | 5.81 |
| · LUT as Memory | 202 | 17400 | 1.16 |
| Slice Registers | 4369 | 106400 | 4.11 |
| Block RAM Tile（全部 RAMB36） | 8 | 140 | 5.71 |
| DSPs | 0 | 220 | 0.00 |
| Bonded IOB | 45 | 200 | 22.50 |
| BUFGCTRL / MMCME2_ADV | 3 / 0 | 32 / 4 | 9.38 / 0.00 |
| IDELAYE2 / ILOGIC / OLOGIC | 7 / 7 / 8 | 200 / 200 / 200 | 3.50 / 3.50 / 4.00 |

## 3. 时序（Routed）

| 范围 | WNS (ns) | TNS (ns) | 失败端点 | 端点总数 | WHS (ns) | THS (ns) | 失败端点 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全局 | **0.193** | 0.000 | 0 | 10740 | **0.043** | 0.000 | 0 |
| `clk_fpga_0`（50 MHz） | 9.773 | 0.000 | 0 | 9565 | 0.043 | 0.000 | 0 |
| `data_clk`（239.981 MHz） | 0.193 | 0.000 | 0 | 783 | 0.066 | 0.000 | 0 |

- 全局 `WPWS 0.264 / TPWS 0.000`（0/4633）；`data_clk` WPWS 1.229（449）。
- 跨时钟域：`clk_fpga_0 → clk_fpga_1` WNS 1.290 / WHS 0.215（42 端点，0 失败）。
- 结论行：`All user specified timing constraints are met.`

## 4. 时钟

`clk_fpga_0` 50.000 MHz（20.000 ns）、`clk_fpga_1` 200.000 MHz（5.000 ns）、`data_clk` 239.981 MHz（4.167 ns）。

## 5. 功耗（`report_power`）—— **工具估计，不是板级实测**

| 项 | 值（Vivado 估计） |
|---|---|
| Total On-Chip Power | **2.071 W** |
| Dynamic / Device Static | 1.915 W / 0.155 W |
| Effective TJA | 11.5 ℃/W |
| Max Ambient / Junction Temperature | 61.1 ℃ / 48.9 ℃ |
| Design Power Budget | `Unspecified` |

> 与合并产品摘要同理：这是工具估算，本项目没有独立 RF Overlay 的板级功耗实测。

## 6. 实现状态

| 项 | 结果 |
|---|---|
| 路由 | 逻辑网 12238；可布通 6914，全布通 6914；**路由错误 0** |
| DRC routed | **0 Errors**；告警 `RBOR-1 4`、`REQP-1839 20`、`RTSTAT-10 1` |

## 7. 同一目标的板级记录（不同构建，单列，不调和）

2026-09-18 的板级 L2 Stage 1 使用 **2026-09-17 17:58:44 构建**的 `rf_overlay.bit`（SHA-256 `4bc8ba11…`，本机构建与板载逐字节相同），记录：**WNS = 12.359 ns / WHS = 0.056 ns**，FCLK0/FCLK1 = 50.000 / 200.000 MHz；AD9361 init 返回 0；8 PASS / 0 FAIL / 2 RECORD。出处：仓库 `docs/rf/l2_stage1_record.md`。

**已知口径冲突（如实记录，不调和）**：仓库 `docs/rf/boardtest.md` 的记录模板写同一日期构建为 **WNS = 12.710 ns / WHS = 0.066 ns**。两处数值不同，且 `boardtest.md` 是"记录模板/待重填"性质；本仓库不判断哪个是最终值，两个都保留。上述任何数字都**不是**本次 2026-09-19 实现报告（0.193/0.043），两者构建/位流不同源，不能配成优化前后对照。

## 8. 本摘要**不**证明什么

- 不是交付 bit 的指标（交付 bit 属合并产品 Overlay）。
- 不证明外部 RF 接口时序（接口未加 I/O delay，同上）。
- 不证明板级功耗；§5 是工具估计。
- 不证明功能验收：功能项见板级 L2 记录与板端有限冒烟（各自限定在对应日期/构建）。
