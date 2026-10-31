# 海报与 PPT 文案（英文主文案 + 中文对照）

维护日期：2026-10-31。本文给出可直接排版的**英文海报主文案**、中文对照、图注、关键指标、PPT 分页短句与讲稿要点。数值全部照抄仓库已有材料（[EVIDENCE-PACK.md](EVIDENCE-PACK.md)、[SYNTHESIS-METRICS.md](SYNTHESIS-METRICS.md)、[PERFORMANCE-EVIDENCE.md](PERFORMANCE-EVIDENCE.md)、[OPTIMIZATION-AND-REPRODUCTION.md](OPTIMIZATION-AND-REPRODUCTION.md)），带限定语；**禁止虚构/换算数据**。

> **口径**：当前交付版本 = 2026-10-26 交付复审口径：BPSK/QPSK 有限演示 + 受限 QAM16；**QAM64 已移出交付**。历史候选、辅助 AWG、QAM64 指标不得当作当前交付结果。系统框图：`docs/figures/rf-system-architecture-poster-4x3.png`（1448×1086，4:3，已确认无个人信息）。

---

## 1. Poster copy (English)

### Title

**Channel-Aware Transmitter: A Portable RF Transceiver and Receive-Analysis Platform on Zynq-7020 + AD9361**

### Subtitle

Single-board SDR demonstration with PL I/Q capture, PS analysis, and same-capture spectrum/constellation/BER — BPSK/QPSK with limited QAM16 validation

### Problem

Portable RF teaching, prototyping and field diagnostics need both a transmitter and a receiver, yet bench instruments and PC + USRP setups are bulky, costly, or tied to a bench. This project asks whether a single Zynq-7020 board with an AD9361 transceiver and a 5-inch touchscreen can serve as a self-contained RF transceiver and receive-analysis platform. The hard part is not only signal processing: the system must split real-time sample capture (PL) from analysis and lightweight inference (PS), keep exactly one owner of the PL and the transceiver, and make every reported result traceable to a specific build and evidence record.

### Approach

We build one merged overlay containing the RF sample path, the LCD/touch controller and the ARM-side control/analysis software; an external PlutoSDR driven by GNU Radio supplies an independent RF stimulus over coax. The PL captures finite 4096-point I/Q snapshots into BRAM and hands them to the PS over AXI; the PS performs control, synchronization, demodulation, spectrum/constellation analysis and a small six-class CNN, with QAM64 removed from the delivered selection. BER is computed against independent PRBS references, and results are shown on the board's touchscreen in the same capture context. Releases are gated by version manifests and evidence records rather than ad-hoc logs.

### Implementation / System

The delivered overlay is packaged as one bit/HWH plus a software manifest (delivery release `4814bbc4…`); its programmable payload is byte-identical to the 2026-09-24 build (`76e242fe…`) described by the implementation reports. On an `xc7z020clg484-2` it uses 28745 LUTs (54.03%), 22620 registers (21.26%), 18.5 Block RAM tiles (13.21%) and 104 DSP48E1 slices (47.27%); internal timing closes with WNS +0.213 ns / WHS +0.018 ns and clocks of 50 / 200 / 239.981 MHz. Estimated on-chip power is 2.418 W from Vivado `report_power` — an estimate, not a board measurement.

### Results

In a bounded 12-frame board-side smoke on the installed delivery release, both BPSK ON frames showed 0/138 bit errors and both QPSK ON frames showed 0/128, while QAM16 showed 1/256 on one frame and 0/256 on the other; the non-zero frame is retained as a metric negative and is not reported as clean. Five QPSK development frames replayed with explicit QI ordering at 0/128 errors, and QAM64-removal negative checks passed 9/9. One bounded external PlutoSDR single-frame capture (2026-10-20) also exists: two consistency decodes of the same 4096-sample snapshot each gave 0/138; this is not statistical BER and not two independent receptions. Historical/independent board records — not the delivered bit, and on the board's own TX/self-loop path rather than external-SDR validation — include TX sidebands at 78.112/81.888 MHz against 78.125/81.875 MHz targets (±13 kHz, 12/12 checks, no clipping) and an independent RF-overlay L2 stage-1 result of 8 PASS / 0 FAIL / 2 RECORD.

### Reproducibility

Source, build scripts and test entry points are in the open repository, e.g. `python scripts/build_bitstream.py`, `vivado -mode batch -source pynq_overlay/build.tcl`, `vivado -mode batch -source rf_overlay/build.tcl` and `make -C rf_overlay/sw/board_app`. Key results are transcribed into sanitized evidence summaries that state the original report SHA-256 and provenance; raw bitstreams/HWH/raw IQ are intentionally not in the repository. Byte-identical rebuild of the delivered bit is not guaranteed: it was built from a workspace with uncommitted changes, and five third-party RTL files have unresolved licensing. The repository is a sanitized source copy, not an installable release.

### Limitations

Scope is deliberately narrow: QAM64 is not delivered, QAM16 is not fully clean, and the external-SDR BPSK historical statistics reached only 23/30 synchronised frames, so that path did not pass overall. The external single-frame demo is a direct connection with internal TX attenuation only (no external attenuator); level figures are calculation bounds, not measured dBm, and do not constitute a damage guarantee. EVM, real-link RRC reception and channel-aware automatic mode selection are not implemented; formal physical capture (S2, 108 frames + 12 offline negatives) and human acceptance are NOT_RUN. Power is a tool estimate, not measured; the RF source-synchronous DDR interface is not timing-constrained or measured, so we do not claim interface timing closure. Third-party RTL licensing is unresolved and the repository contains neither bitstream/HWH nor raw IQ.

---

## 2. 海报中文对照

**标题**：信道感知发射机：基于 Zynq-7020 + AD9361 的便携式射频收发与接收分析平台

**副标题**：单板 SDR 演示：PL 样点采集、PS 分析、同帧频谱/星座/BER 展示——BPSK/QPSK 有限演示 + 受限 QAM16 验证

**问题**：便携式射频教学、原型验证与现场诊断同时需要发射与接收能力，但台式仪器和"PC+USRP"方案体积大、成本高或依赖固定工作台。本项目验证：单块带 AD9361 与五寸触摸屏的 Zynq-7020 板能否构成自包含的射频收发与接收分析平台。难点不只在信号处理：系统必须把实时样点采集（PL）与分析/轻量推理（PS）分工，保证 PL 与收发器只有一个拥有者，并使每个报告结果都能追溯到具体构建与证据记录。

**方法**：用一个合并 Overlay 同时承载 RF 样点通路、LCD/触摸控制器与 ARM 控制/分析软件；外部 PlutoSDR + GNU Radio 经同轴提供独立射频激励。PL 把有限 4096 点 I/Q 快照写入 BRAM，经 AXI 交给 PS；PS 执行控制、同步、解调、频谱/星座分析与小型六类 CNN，交付选择中已移除 QAM64。BER 由独立 PRBS 参考计算，结果在同一采集上下文显示在板载触摸屏。发布由版本清单与证据记录把关，不依赖临时日志。

**实现/系统**：交付 Overlay 以一份 bit/HWH + 软件清单打包（交付 release `4814bbc4…`）；其可编程载荷与实现报告所描述的 2026-09-24 构建（`76e242fe…`）逐字节相同。在 `xc7z020clg484-2` 上占用 28745 LUT（54.03%）、22620 寄存器（21.26%）、18.5 个 Block RAM Tile（13.21%）、104 个 DSP48E1（47.27%）；内部时序收敛：WNS +0.213 ns / WHS +0.018 ns，时钟 50/200/239.981 MHz。片上功耗估计 2.418 W，来自 Vivado `report_power`，是估计值而非板上实测。

**结果**：在已安装交付 release 上有界 12 帧**板端**冒烟中，BPSK 两个 ON 帧均为 0/138 误码、QPSK 两个 ON 帧均为 0/128 误码，QAM16 一帧 1/256、另一帧 0/256；非零帧作为指标负例保留，不写成干净通过。QPSK 5 帧开发帧以显式 QI 次序回放为 0/128；QAM64 移除负例 9/9 通过。另有一次外部 PlutoSDR 单帧（2026-10-20，同一 4096 点快照的原样/共轭两路一致性解码各 0/138；非统计 BER、非两次独立接收）。历史/独立板级记录（非交付 bit，且为**板卡自环/TX 路径，非外部 SDR 验证**）包括：TX 边带 78.112/81.888 MHz（目标 78.125/81.875，误差 ±13 kHz，12/12 检验，无削波）、独立 RF Overlay 的 L2 Stage 1 为 8 PASS / 0 FAIL / 2 RECORD。

**可复现**：源码、构建脚本与测试入口在公开仓库中，例如 `python scripts/build_bitstream.py`、`vivado -mode batch -source pynq_overlay/build.tcl`、`vivado -mode batch -source rf_overlay/build.tcl`、`make -C rf_overlay/sw/board_app`。关键结果被转录为带原件 SHA-256 与来源的脱敏证据摘要；位流/HWH/原始 IQ 有意不入库。交付 bit 不能保证逐字节重建：它由带未提交修改的工作区构建，且 5 个第三方 RTL 许可未决。本仓库是脱敏源码副本，不是可安装发布包。

**限制**：范围有意收窄：QAM64 不交付，QAM16 并非全干净，外部 SDR 的 BPSK 历史统计仅 23/30 帧同步，整体未通过。外部单帧演示为**仅内部 TX 衰减的直连（无外置衰减器）**；电平是计算界、不是实测 dBm，也不构成损伤保证。EVM、真实链路 RRC 接收、信道感知自动选档未实现；正式物理采集（S2 108 帧 + 12 离线负控）与真人验收 `NOT_RUN`。功耗是工具估计而非实测；RF 源同步 DDR 接口未加时序约束、未实测，因此不宣称接口时序通过。第三方 RTL 许可未决，仓库不含位流/HWH 与原始 IQ。

---

## 3. 图注（Figure caption）

**英文**：*Figure 1. System data path. A PC running GNU Radio drives a PlutoSDR transmitter over USB; the RF output reaches the board's AD9361 RX1 input over RF coax. On the Zynq-7020 board, the PL captures I/Q samples into a BRAM buffer and moves them to the PS/ARM over AXI; the PS performs control, synchronization/demodulation, feature computation and model inference, and renders spectrum, constellation and BER on the touchscreen. Offline model training (dashed) happens off-board. Delivered validation is limited to BPSK/QPSK and a constrained QAM16; QAM64 is not part of the delivered scope.*

**中文**：图 1. 系统数据通路。PC 上的 GNU Radio 经 USB 驱动 PlutoSDR 发射；射频输出经同轴进入板载 AD9361 的 RX1。Zynq-7020 板上，PL 把 I/Q 样点采集进 BRAM 缓冲并经 AXI 交给 PS/ARM；PS 执行控制、同步/解调、特征计算与模型推理，并在触摸屏上显示频谱、星座与 BER。离线模型训练（虚线）在板外完成。交付验证仅限 BPSK/QPSK 与受限 QAM16；QAM64 不在交付范围内。

**图文件**：[`docs/figures/rf-system-architecture-poster-4x3.png`](../figures/rf-system-architecture-poster-4x3.png)（1448×1086，4:3，无个人信息；可直接用于海报/PPT 第 3 页）。

---

## 4. 关键指标卡（照抄，可上海报/PPT）

| 项 | 数值 | 条件/版本 | 限定语（必须保留） |
|---|---|---|---|
| FPGA 资源（合并 Overlay） | LUT **28745（54.03%）**；FF **22620（21.26%）**；BRAM **18.5（13.21%）**；DSP **104（47.27%）** | 2026-09-24 构建；`report_utilization` Fully Placed；`xc7z020clg484-2` | 交付 bit 的**可编程载荷**与该构建相同（文件级 SHA 不同） |
| 内部时序 | **WNS +0.213 ns / WHS +0.018 ns**；TNS/THS 0；失败端点 0/0 | 同次构建 `report_timing_summary` Routed | **仅覆盖已约束的内部路径**；不等于外部 RF DDR 接口通过 |
| 时钟 | **50 / 200 / 239.981 MHz**（`clk_fpga_0` / `clk_fpga_1` / `data_clk`）；`sys_clk` 50 MHz、`pclk_raw` 33.333 MHz | Clock Summary | 原始值照抄；不换算 |
| 片上功耗 | **2.418 W**（Dynamic 2.247 W + Static 0.171 W；Tj 52.9 ℃） | `report_power` commercial/typical/Production | **Vivado 工具估计值，不是板上实测**；无功耗预算 |
| 交付 12 帧冒烟 | BPSK **0/138、0/138**；QPSK **0/128、0/128**；QAM16 **1/256** 与 **0/256** | release `4814bbc4…`；2026-10-26；2 ON + 2 OFF/模式 | **有限帧冒烟**；QAM16 1/256 是指标负例，不写成全零误码 |
| QPSK 5 帧回放 | 5 帧 **QI 显式、sync=true、BER 0/128** | 2026-10-23 开发帧回放（不重采） | 开发回放，非独立 TEST 集 |
| 双 TX 示波器 | **78.112 / 81.888 MHz**（目标 78.125/81.875，误差 **±13 kHz**）；12/12 检验；削波 0 | 2026-09-30 **历史板级记录**；LO 80 MHz / 60 MSPS / 2R2T | 非当前交付版本；仪器读数未去嵌频响 |
| L2 Stage 1（独立 RF Overlay） | **8 PASS / 0 FAIL / 0 N/A / 2 RECORD-ONLY**；C1 78.128/81.872 MHz；C5 19.85 dB | 2026-09-18；`rf_overlay.bit` sha `4bc8ba11…`；1R1T | 独立 RF 目标是**非交付 bit**；历史板级记录 |

> 功耗、QAM16 非零误码、历史/独立记录三类限定语**必须**随数字出现；不得只截取数字。

---

## 5. 创新点与应用价值（诚实版）

- **单板软硬件协同的便携收发 + 接收分析**：同一 Zynq-7020 板上完成 PL 样点采集与 PS 控制/分析/轻量推理，配五寸触摸屏，无需 PC 参与接收侧。
- **PL 样点采集与 PS 分析的分工**：PL 只做有限快照采集与 BRAM 缓冲，PS 负责同步/解调/特征/推理——把实时性要求与灵活算法分开，适合教学与快速原型。
- **触摸屏同帧展示**：频谱、星座与 BER 在同一采集上下文呈现，便于现场讲解与演示。
- **可复现的脱敏证据链 + 可复用 PYNQ 技能**：交付带版本清单与载荷绑定链，证据以摘要 + 原件 SHA-256 方式公开；配套 PYNQ/RF 开发技能可复用于类似 Zynq + AD9361 项目。
- **诚实边界**：以上为工程集成与证据方法层面的贡献；接收演示仅覆盖 BPSK/QPSK 与受限 QAM16，**不是**多制式通用接收机；技能包的**通用性尚未在其他板卡/题目上验证**。

---

## 6. 真实限制（必须保留，不得在 PPT/海报中删除）

1. **QAM64 已移出交付**；历史 QAM64 报告与指标不得当作当前交付结果。
2. **QAM16 非零误码**：12 帧选用集中 1/256；受限演示，不能倒填成零误码验收。
3. **外部 SDR BPSK 历史统计 23/30 帧同步，整体未通过**；不能只报同步成功帧的零误码。
4. **EVM、RRC 真实链路、信道感知自动选档未实现**（`NOT_IMPLEMENTED`/`NOT_RUN`）。
5. **完整物理采集（S2 108 帧 + 12 离线负控）与真人验收 `NOT_RUN`**；触摸时延 `NOT_MEASURED`。
6. **RF 接口时序未测**：RF 源同步 DDR 接口无 `set_input_delay`/`set_output_delay`，板级 skew/内部相位/runtime tap 为 `TBD`；**不得宣称“接口时序通过”**（内部 WNS 为正不等于接口通过）。
7. **功耗 2.418 W 是 Vivado 估计值**，非实测；无功耗预算对照。
8. **第三方 RTL 许可未确认**（5 个文件），公开副本不能完整重建 PL。
9. **仓库不含位流/HWH/原始 IQ/收据**，不是可安装发布包；历史 SHA-256 不能用本副本验证。
10. 历史 ARM P95/RSS（278.835 / 258.6409 / 272.785 ms）属于**各自历史候选**，当前交付版本**未重测**，不可移植。

---

## 7. PPT 分页（英文短句，每页 3–5 点）+ 讲稿要点（中文）

> 建议 11 页（可压成 9 页：合并第 7/8 页、删除第 11 页；不建议扩展超过 12 页）。

**Slide 1 — Title**
- Channel-Aware Transmitter: portable RF transceiver + receive-analysis on Zynq-7020 + AD9361
- Single board, PL I/Q capture, PS analysis, touchscreen display
- Scope: BPSK/QPSK demo + limited QAM16; QAM64 removed
- All numbers traceable to evidence summaries (no fabricated data)

讲稿要点：先给一句话定位——"一块板做收发+接收分析"；再说明演示范围有限，避免听众误解为通用接收机。

**Slide 2 — Problem & motivation**
- Portable RF teaching/prototyping needs TX and RX, but bench setups are bulky/costly
- Single Zynq board + AD9361 + touchscreen as a self-contained platform
- Real difficulty: PL/PS split, single-owner control, evidence traceability
- Goal: reproducible demo, not a universal SDR

讲稿要点：强调"便携 + 自包含"；把难点落在分工与证据，而不是参数堆砌。

**Slide 3 — System architecture**（配 图 1）
- PC + GNU Radio → PlutoSDR TX → RF coax → Zynq-7020 + AD9361 RX1
- PL: I/Q capture → BRAM buffer → AXI → PS
- PS: control, sync/demod, features, inference → touchscreen spectrum/constellation/BER
- Offline model training is off-board (dashed)

讲稿要点：按图走一遍链路；说明 PlutoSDR 只是激励源，接收侧全在板内。

**Slide 4 — Implementation split**
- One merged overlay: RF path + LCD/touch + ARM software
- Single-owner daemon owns PL and AD9361; release gates and rollback
- Finite 4096-point snapshots; PRBS references for BER
- Six-class CNN runs on PS; QAM64 excluded from delivered selection

讲稿要点：解释"单控制者"原则；强调有限快照不是连续接收。

**Slide 5 — Evidence & version binding**
- Delivery release `4814bbc4…` with bit `d5bfe5d9…` and HWH `ed8d011a…`
- Programmable payload byte-identical to 2026-09-24 build `76e242fe…` (`payload_equal`)
- Same build produced routed DCP `63f679bc…` and the implementation reports
- Sanitized evidence pack with original SHA-256, raw artifacts kept off-repo

讲稿要点：这是本项目最有特色的一页——证明"指标不是拼出来的"，靠载荷绑定把报告与交付 bit 连起来。

**Slide 6 — FPGA implementation results**
- LUT 28745 (54.03%) · FF 22620 (21.26%) · BRAM 18.5 (13.21%) · DSP 104 (47.27%)
- Internal timing: WNS +0.213 ns / WHS +0.018 ns, 0 failing endpoints
- Clocks: 50 / 200 / 239.981 MHz
- Power: 2.418 W **estimate** (`report_power`, not measured)

讲稿要点：先报资源与时序，再主动说明功耗是估计值；不要宣称外部接口时序。

**Slide 7 — Limited receive results**
- 12-frame smoke on delivery release: BPSK 0/138 ×2, QPSK 0/128 ×2
- QAM16: 1/256 and 0/256 — non-zero frame retained, not a clean pass
- QPSK 5 development frames: explicit QI, sync=true, BER 0/128
- QAM64-removal negative checks: 9/9

讲稿要点：强调"有限帧、单帧零错不等于总体 BER"；主动承认 QAM16 的 1/256。

**Slide 8 — RF output & independent bring-up**
- TX sidebands 78.112/81.888 MHz vs 78.125/81.875 MHz (error ±13 kHz)
- 12/12 checks, no clipping — 2026-09-30 historical board record
- Independent RF overlay L2 Stage 1: 8 PASS / 0 FAIL / 2 RECORD
- These records are historical/independent — not the delivered bit

讲稿要点：说明这些是历史/独立记录，用来展示验证方法，而不是当前交付 bit 的验收。

**Slide 9 — What we do NOT claim**
- No QAM64 delivery; QAM16 not fully clean
- External-SDR BPSK 23/30 synchronised — overall not passed
- EVM / RRC real link / auto mode selection not implemented
- Formal physical capture + human acceptance NOT_RUN
- RF interface timing not measured; power is an estimate

讲稿要点：这一页必须有；评审更看重边界诚实。逐条说明后停在这里，不超范围解释。

**Slide 10 — Reproducibility**
- Build: `scripts/build_bitstream.py`, `pynq_overlay/build.tcl`, `rf_overlay/build.tcl`
- Test: `pytest` suites + `make -C rf_overlay/sw/board_app host-test`
- Evidence: sanitized summaries with original SHA-256; bit/HWH/raw IQ not in repo
- Limits: delivered bit not guaranteed byte-identical (uncommitted workspace build)
- Third-party RTL licensing unresolved

讲稿要点：讲清"能复现什么、不能复现什么"；不要把命令存在当成已验证。

**Slide 11 — Summary**
- One board: PL capture + PS analysis + touchscreen — working limited demo
- Evidence chain binds the delivered payload to the reported implementation
- Claims limited to BPSK/QPSK + limited QAM16; QAM64 removed
- Next: formal physical capture, RF interface timing, human acceptance

讲稿要点：用一句话总结诚实结论；展望下一步实测项，不承诺未完成能力。

---

## 8. 排版与合规检查清单

- [ ] 所有数字与 [PERFORMANCE-EVIDENCE.md](PERFORMANCE-EVIDENCE.md)/[EVIDENCE-PACK.md](EVIDENCE-PACK.md) 一致；未重算百分比、未换算。
- [ ] 功耗处有"估计值/Vivado report_power"字样；未写成实测。
- [ ] 未宣称 RF 接口时序通过；未把内部 WNS 为正说成接口裕量。
- [ ] QAM64/辅助 AWG/历史候选未被当作当前交付结果。
- [ ] 12 帧为"有限冒烟"、QPSK 5 帧为"开发回放"、双 TX 与 L2 为"历史/独立记录"——限定语齐全。
- [ ] 无个人身份信息与联系方式、用户名、盘符绝对路径、设备串号、私网 IP、凭据。
- [ ] 图注与 `rf-system-architecture-poster-4x3.png` 实图一致（PC+GNU Radio → PlutoSDR → coax → AD9361 RX1 → PL/PS → 触摸屏；离线训练虚线）。
- [ ] 海报建议单页（4:3；A0 竖版或 16:9 屏可缩放），PPT 11 页（可压 9 页）。
