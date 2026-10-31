# 独立 PYNQ Overlay 综合/实现摘要（2026-09-13）

> **性质**：公开**脱敏摘要**，不是原件。原件在开发机 `<LOCAL_EVIDENCE>/pynq_overlay/vivado/ledbtn/ledbtn.runs/impl_1/`，含开发机绝对路径与主机名，未随仓库；本文件字节与原件不同，不能用本副本复算。
> **对象**：独立 PYNQ Overlay（`ledbtn_bd_wrapper`，五寸屏 LCD/GPIO 板级支撑目标，构建入口 `pynq_overlay/build.tcl`）。
> **它不是交付 bit**：交付产品使用的是合并产品 Overlay（见 [merged-overlay-2026-09-24.md](merged-overlay-2026-09-24.md)）。本目标报告只说明 LCD/GPIO 独立构建的 FPGA 侧实现结果。

## 1. 报告头（原件照抄）

| 项 | 值 |
|---|---|
| 工具 | `Vivado v.2025.2 (win64) Build 6299465 Fri Nov 14 19:35:11 GMT 2025` |
| 器件 | `xc7z020clg484-2`；时序报告写 `7z020-clg484` |
| Speed File | `-2  PRODUCTION 1.12 2019-11-22` |
| 设计 | `ledbtn_bd_wrapper`；`Design State`：`Fully Placed` / `Routed` / `Fully Routed` |
| 报告日期 | 2026-09-13（资源 20:00:56；DRC 20:02:20；时序 20:02:30；功耗 20:02:33） |

**原件 SHA-256**（`<LOCAL_EVIDENCE>/pynq_overlay/vivado/ledbtn/ledbtn.runs/impl_1/`，2026-10-31 只读复核）：

| 原件 | SHA-256 |
|---|---|
| `ledbtn_bd_wrapper_utilization_placed.rpt` | `d29c62214695c19ed0a0321bc17d28c47a5dfbcc84022c26fdea4c647d134861` |
| `ledbtn_bd_wrapper_timing_summary_routed.rpt` | `9433680be7cb5a234a8f7012a130d032ea3ee26e4537b4e845c6caf239b743a2` |
| `ledbtn_bd_wrapper_power_routed.rpt` | `8a63a59878d22185b983f2674c152383ec2086d5e929252b4f254f51909913b0` |
| `ledbtn_bd_wrapper_route_status.rpt` | `c7bae8c07b32d278c7aaf7da2e6af51a6542d4efba0858d35471f25ce89e71a0` |
| `ledbtn_bd_wrapper_drc_routed.rpt` | `4cf36815cc24a4012eae2ab30fa0759709bdb0192c274b7934751cb15b4dc836` |
| 构建产物 `../../out/ledbtn.bit` | `07dcfb52f2cde2c4ac8ef7a711f5a58567bc21b4ed4bd40e9108a9f49d578894` |
| 构建产物 `../../out/ledbtn.hwh` | `dfa04566cf6b4250bf26060b99de946b99adb69f26497367424faba63e23bd3f` |
| 构建日志 `../../build.log` | （日志未入库；原件保留本地，本摘要只引用其结尾统计行） |

## 2. 资源占用（Fully Placed）

| 资源 | Used | Available | Util% |
|---|---:|---:|---:|
| Slice LUTs | 3291 | 53200 | 6.19 |
| · LUT as Logic | 2103 | 53200 | 3.95 |
| · LUT as Memory | 1188 | 17400 | 6.83 |
| Slice Registers | 3156 | 106400 | 2.97 |
| Block RAM Tile | 0 | 140 | 0.00 |
| DSPs | 0 | 220 | 0.00 |
| Bonded IOB | 33 | 200 | 16.50 |
| BUFGCTRL / MMCME2_ADV | 3 / 1 | 32 / 4 | 9.38 / 25.00 |

## 3. 时序（Routed）

| 范围 | WNS (ns) | TNS (ns) | 失败端点 | 端点总数 | WHS (ns) | THS (ns) | 失败端点 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全局 | **11.549** | 0.000 | 0 | 15535 | **0.036** | 0.000 | 0 |
| `clk_fpga_0`（50 MHz） | 11.549 | 0.000 | 0 | 15263 | 0.036 | 0.000 | 0 |
| `pclk_raw`（33.333 MHz） | 19.840 | 0.000 | 0 | 232 | 0.083 | 0.000 | 0 |

- 全局 `WPWS 7.000 / TPWS 0.000`（0/4447）；`clkfb_raw` WPWS 18.408（3）、`clk50m` WPWS 7.000（1）。
- 结论行：`All user specified timing constraints are met.`

## 4. 时钟

`clk50m` 50.000 MHz（20.000 ns，含派生 `clkfb_raw` 50.000 MHz）、`pclk_raw` 33.333 MHz（30.000 ns）、`clk_fpga_0` 50.000 MHz。

## 5. 功耗（`report_power`）—— **工具估计，不是板级实测**

| 项 | 值（Vivado 估计） |
|---|---|
| Total On-Chip Power | **1.804 W** |
| Dynamic / Device Static | 1.657 W / 0.148 W |
| Effective TJA | 11.5 ℃/W |
| Max Ambient / Junction Temperature | 64.2 ℃ / 45.8 ℃ |
| Design Power Budget | `Unspecified` |

## 6. 实现状态

| 项 | 结果 |
|---|---|
| 路由 | 逻辑网 8930；可布通 5788，全布通 5788；**路由错误 0** |
| DRC routed | **0 Errors**，告警汇总为空 |
| 构建日志结尾 | `139 Infos, 0 Warnings, 0 Critical Warnings and 0 Errors encountered`；`Bitgen Completed Successfully` |

## 7. 本摘要**不**证明什么

- 不是交付 bit 的指标。
- **不证明** LCD/触摸在板上可用：资源/时序只说明 FPGA 侧实现状态；板载 UI/触摸证据另见 S1 照片复核与交付复审（触摸时延 `NOT_MEASURED`）。
- 不证明板级功耗；§5 是工具估计。
- 不证明与合并产品 Overlay 的等价性：两者是不同工程目标的产物，产物不混用。
