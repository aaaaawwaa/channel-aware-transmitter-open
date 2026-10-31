# 合并产品 Overlay 综合/实现摘要（2026-09-24）

> **性质**：公开**脱敏摘要**，不是原件。原件在开发机 `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/`，含开发机绝对路径与主机名，未随仓库；本文件字节与原件不同，不能用本副本复算或验证原件哈希。
> **对象**：合并产品 Overlay（`host_bd_wrapper`：PS7 + LCD + AXI DMA/BRAM + AD9361 样点通路 + `awg_top_0`）。该次构建的 bit 与**当前交付 bit 的可编程载荷逐字节相同**（绑定链见 [bitstream-binding-2026-10-26.md](bitstream-binding-2026-10-26.md)）。
> **边界**：本摘要只覆盖 **PL 内部**资源/时序/功耗估计/实现状态；**内部时序通过不等于外部 RF DDR 接口通过**（见 §7）。

## 1. 报告头（原件照抄）

| 项 | 值 |
|---|---|
| 工具 | `Vivado v.2025.2 (win64) Build 6299465 Fri Nov 14 19:35:11 GMT 2025` |
| 器件 | `xc7z020clg484-2`；时序/时钟利用报告写 `7z020-clg484` |
| Speed File | `-2  PRODUCTION 1.12 2019-11-22` |
| 设计 | `host_bd_wrapper`；`Design State`：`Fully Placed`（资源）/ `Routed`（时序、功耗、时钟利用）/ `Fully Routed`（DRC） |
| 报告日期 | 2026-09-24（资源 10:03:31；DRC 10:08:52；时序 10:09:43；功耗 10:10:12；时钟利用 10:10:16；bit 10:11:52） |

**原件 SHA-256**（`Get-FileHash -Algorithm SHA256`，2026-10-31 只读复核）：

| 原件（`<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/`） | SHA-256 |
|---|---|
| `host_bd_wrapper_utilization_placed.rpt` | `c42c1d087e522fc7f851f901588cf29e9988ced15ed3ec5285ea86e7866cb89d` |
| `host_bd_wrapper_timing_summary_routed.rpt` | `56f80e2f69b754901aff0a2f69e8fddfc3585d19d09c19d7d2ee002b727cf360` |
| `host_bd_wrapper_power_routed.rpt` | `ce9e611b61c21c21caaa7a202ce281a8b10612f758d0e23b084e13112b104fdc` |
| `host_bd_wrapper_clock_utilization_routed.rpt` | `ba38ab1bccd796c187ca91282082ec5e4650cc1954e5875e50951ecaeb39b196` |
| `host_bd_wrapper_route_status.rpt` | `56175c970378d05119e2780775f9e921f499c7772def5a2e261812bfe0b3471a` |
| `host_bd_wrapper_drc_routed.rpt` | `d73bcbc397ada6f15eafdb2d701275b19fe548e569b956a2759fb2e9c956efe9` |
| `host_bd_wrapper.bit` | `76e242fe0438e1bec5596ef93be256f0532a53968c991b103bd710c89f08e1bb` |
| `host_bd_wrapper_routed.dcp` | `63f679bcce815ba6c883f2e47b286dfc35a549ae9d040eba028548bd2be7c305` |

## 2. 资源占用（`report_utilization`，Fully Placed）

| 资源 | Used | Available | Util% |
|---|---:|---:|---:|
| Slice LUTs | 28745 | 53200 | 54.03 |
| · LUT as Logic | 26187 | 53200 | 49.22 |
| · LUT as Memory | 2558 | 17400 | 14.70 |
| Slice Registers | 22620 | 106400 | 21.26 |
| · Register as Flip Flop | 22606 | 106400 | 21.25 |
| · Register as Latch | 14 | 106400 | 0.01 |
| Slice（Slice 站点） | 9906 | 13300 | 74.48 |
| Block RAM Tile | 18.5 | 140 | 13.21 |
| · RAMB36/FIFO | 17 | 140 | 12.14 |
| · RAMB18 | 3 | 280 | 1.07 |
| DSPs（DSP48E1） | 104 | 220 | 47.27 |
| Bonded IOB / Bonded IOPADs | 96 / 130 | 200 / 130 | 48.00 / 100.00 |
| BUFGCTRL / MMCME2_ADV | 6 / 1 | 32 / 4 | 18.75 / 25.00 |
| IDELAYE2 / ILOGIC / OLOGIC | 7 / 7 / 8 | 200 / 200 / 200 | 3.50 / 3.50 / 4.00 |

关键原语：`FDRE 17725`、`FDCE 4149`、`FDSE 647`、`FDPE 85`、`LDCE 14`；`CARRY4 2459`；`RAMB36E1 17`、`RAMB18E1 3`；`DSP48E1 104`；`IBUFDS 8`、`OBUFDS 8`、`IDDR 7`、`ODDR 8`、`IDELAYE2 7`、`PS7 1`、`MMCME2_ADV 1`。

## 3. 时序（`report_timing_summary`，Routed）

| 范围 | WNS (ns) | TNS (ns) | 失败端点 | 端点总数 | WHS (ns) | THS (ns) | 失败端点 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全局 | **0.213** | 0.000 | 0 | 67128 | **0.018** | 0.000 | 0 |
| `clk_fpga_0`（50 MHz） | 6.699 | 0.000 | 0 | 49449 | 0.018 | 0.000 | 0 |
| `data_clk`（239.981 MHz） | 0.277 | 0.000 | 0 | 783 | 0.066 | 0.000 | 0 |
| `sys_clk`（50 MHz） | 0.213 | 0.000 | 0 | 12728 | 0.092 | 0.000 | 0 |

- 脉冲宽度：全局 `WPWS 0.264 ns` / `TPWS 0.000`，失败端点 0（25516）；`data_clk` WPWS 1.229（449 端点）。
- 报告结论行：`All user specified timing constraints are met.`
- 报告 Methodology 段（**是告警不是错误**）：`TIMING-18 Missing input or output delay 37`、`TIMING-6/7 各 1`、`TIMING-9/10 各 1`、`TIMING-20 14`、`TIMING-24 3`、`DPIR-1 2916`、`LUTAR-1 28`、`LATCH-1 1`。其中 `TIMING-18` 与 §7 的接口未约束缺口一致。

## 4. 时钟（Clock Summary + `report_clock_utilization`）

| 时钟 | 波形 (ns) | 周期 (ns) | 频率 (MHz) |
|---|---|---|---:|
| `clk_fpga_0` | 0.000 / 10.000 | 20.000 | 50.000 |
| `clk_fpga_1` | 0.000 / 2.500 | 5.000 | 200.000 |
| `data_clk` | 0.000 / 2.083 | 4.167 | 239.981 |
| `sys_clk` | 0.000 / 10.000 | 20.000 | 50.000 |
| 派生：`clkfb_raw`、`daclk0`、`daclk1` | — | 20.000 | 50.000 |
| 派生：`pclk_raw` | 0.000 / 15.000 | 30.000 | 33.333 |

时钟资源与全局时钟（`report_clock_utilization`）：

| 项 | 值 |
|---|---|
| BUFGCTRL / BUFH / MMCM / PLL | 6/32；0/72；1/4；0/4 |
| `g0 clk_fpga_0` | 20.000 ns，时钟负载 18404，覆盖 6 个时钟域区域 |
| `g1 sys_clk` | 20.000 ns，时钟负载 4803，覆盖 5 个区域 |
| `g2 data_clk` | 4.167 ns，时钟负载 448，覆盖 2 个区域 |
| `g3 pclk_raw` | 30.000 ns，时钟负载 164，覆盖 4 个区域 |
| `g4 clk_fpga_1` | 5.000 ns，时钟负载 8，覆盖 1 个区域 |
| `g5 clkfb_raw` | 20.000 ns，时钟负载 1，覆盖 1 个区域 |

## 5. 功耗（`report_power`）—— **工具估计，不是板上实测**

| 项 | 值（Vivado 估计） |
|---|---|
| Total On-Chip Power | **2.418 W** |
| Dynamic / Device Static | 2.247 W / 0.171 W |
| Effective TJA | 11.5 ℃/W |
| Max Ambient / Junction Temperature | 57.1 ℃ / 52.9 ℃ |
| Design Power Budget | `Unspecified` |

> **限定**：以上是 Vivado 在 commercial/typical/Production 条件下对设计活动的**估算**；本项目**没有**板级功耗/电流实测，也没有与供电预算对照。不得写成"实测功耗 2.418 W"。

## 6. 实现状态：路由 / DRC / bitgen

| 项 | 结果 |
|---|---|
| 路由（`route_status`） | 逻辑网 78836；可布通 51186，全布通 51186；**路由错误 0** |
| DRC（`drc_routed`） | **0 Errors**；告警 `DPIP-1 206`、`DPOP-1 76`、`DPOP-2 96`、`PDRC-153 14`、`RBOR-1 6`、`REQP-1839/1840 各 20`、`RTSTAT-10 1`；Advisory `REQP-181 1` |
| 实现日志（`runme.log`） | `DRC finished with 0 Errors, 42 Warnings`；`write_bitstream completed successfully`；`Bitgen Completed Successfully`；结尾 `190 Infos, 3 Warnings, 0 Critical Warnings and 0 Errors` |

## 7. 本摘要**不**证明什么

1. **不证明外部 RF DDR 接口时序**：`rf_overlay/constraints/rf.xdc` 只有时钟/管脚/电平约束，**没有 `set_input_delay`/`set_output_delay`**；对同源 routed DCP 的只读 `check_timing` 记录 9 个输入端口无 input delay、48 个输出端口无 output delay（RF 串行子集 7 输入 / 8 输出），lane1 输入/输出路径 `slack=inf`、`Path Group=(none)`。内部 WNS 为正不能证明 RX 采样或 TX 输出满足 AD9361 要求。
2. **不证明 RTL 功能正确**：时序/资源/DRC 通过不等于数据面协议正确；功能正确性由仿真、主机 C 测试与板端有限冒烟分别证明（见 [board-results-2026-10-26.md](board-results-2026-10-26.md)）。
3. **不证明板级功耗/散热**：§5 是工具估计。
4. **不证明 bit 文件字节与交付 bit 相同**：文件级 SHA 不同，只有**可编程载荷**相同（见绑定链摘要）。

## 8. 已知口径差异（如实记录，不调和）

| 量 | 说法 A | 说法 B | 处理 |
|---|---|---|---|
| `data_clk` 域 WHS | 本报告 Intra-Clock Table：**0.066 ns** | QAM64 线只读复核的 `CHAIN-TIMING.json`：**0.349 ns**（`data_clk_WHS_ns`） | 两个值都保留并注明出处；本摘要 §3 采用路由报告表值 0.066，未核查差异来源 |
| RF 板级 WNS（2026-09-17 构建） | `docs/rf/l2_stage1_record.md`：**12.359 ns** / WHS 0.056 ns | `docs/rf/boardtest.md` 记录模板：**12.710 ns** / WHS 0.066 ns（同写"2026-09-17 本构建"） | 属独立 RF Overlay 的板级记录，见 [rf-overlay-2026-09-19.md](rf-overlay-2026-09-19.md) §7；不调和 |
