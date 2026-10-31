# 当前交付口径板端与参考比对摘要（2026-10-26）

> **性质**：公开**脱敏摘要**，不是原件。原始收据、原始 IQ、板端日志与照片在开发机 `<LOCAL_EVIDENCE>/`，未随仓库；本文件不复制原始 IQ，只转录结果与限定语。
> **范围**：以 2026-10-26 交付复审口径（BPSK/QPSK 有限演示 + 受限 QAM16；QAM64 移出交付）为主；**明确标记为历史/非当前版本**的条目不得移植到当前交付版本。
> **证据层级**：文档级引用。所有数字照抄原文并保留限定语；`NOT_RUN`/`NOT_MEASURED`/`NOT_IMPLEMENTED` 不改写。

## 1. 交付 release 内有限冒烟（当前交付口径）

| 项 | 值 |
|---|---|
| release | `4814bbc42b9d5e0165a3ed3ebe7eaeef`（2026-10-26 安装并只读读回；**历史安装记录，不代表当前板态**） |
| 方法 | 单帧 campaign（每次一 session/init/capture）；交付模式各 2 ON + 2 OFF；共 12 帧接收 |
| BPSK ON1 / ON2 | 0 / 138；0 / 138（`ref=true`） |
| QPSK ON1 / ON2 | 0 / 128；0 / 128（`ref=true`） |
| QAM16 ON1 / ON2 | **1 / 256**（BER 0.00390625，指标负例，保留）；0 / 256 |
| OFF 帧 | BPSK/QPSK/QAM16 各 2 个 OFF 均 `off_tx_ok`、无残留参考/carrier/timing/BER |
| QAM64 移除负例 | **9/9 PASS**（对安装 release 文件执行；QAM64 不在交付选择/能力面，旧配置/草稿被具名拒绝，不自动回退/发射） |
| 保留的预算偏差 | 接收验收集为 12 帧；失败尝试中另有 **2 次 QAM16 ON2** 采集，物理计数为 **14**（验收文档与交付复审均记录为负面事实） |
| 原件 | `<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/`：`acceptance/aggregate.json` SHA-256 `8e63a511a15da4792706b92da9e255cac780e8504b677adc085ed7717cce7dcb`；`REPORT.md` `d0db54ac…`（全值见 [bitstream-binding-2026-10-26.md](bitstream-binding-2026-10-26.md) §5）；逐帧原始 IQ 保留在 `acceptance/{partial-result,remaining-result}/`，**不复制** |
| 仓库出处 | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[README](../../README.md) |
| 状态 | `部分`：有限帧冒烟，不是整体 BER 资格；QAM16 非零误码必须保留 |

**参考比对**：BER 由独立 PRBS7 参考计算（BPSK 分母 138 bits、QPSK/QAM16 分母 128/256 bits 按各自接收机口径）；单帧零错不等于低 BER 总体能力。

**已知口径冲突（如实记录，不调和）**：`ACCEPTANCE-RESULTS.md` 写"no physical/RF cable-loopback capture"，而交付复审指出所选 12 帧中的 ON 帧 `final.json` 记录 `physical=true`、`ui_diagnostic` 也记录 `physical=true`；应区分"有线 RF 实际采集已经发生"与"完整物理验收尚未完成"。两处表述都保留，本文件不判断哪一个应改写。

## 2. QPSK 5 帧有限回放（历史开发帧，非当前交付候选）

| 项 | 值 |
|---|---|
| 日期/版本 | 2026-10-23 开发版回放（不重新采集） |
| 结果 | 5 帧真实开发帧（task85 `05-qpsk-39`、task71 `s01-qpsk-1/2/3`、task80 `03-qpsk-38`）均 **QI 显式次序、`sync=true`、BER 0/128**；独立参考一致 |
| 另记 | 6 帧真实解调冒烟（其中 QPSK seed39/40/41 均 `sync=true`、`valid_symbols=127`、BER 0/128、QI；BPSK seed6 0/138）；冻结候选产品入口 12/12（5 真实 QI + 7 负例）通过 |
| 原件 | `<LOCAL_EVIDENCE>/rf-teaching/s2-46db-p3-qpsk-arm-2026-10-23/`：`FINAL-ARM-REPORT.md` `59d054fe…`、`final-binding.json` `76bfc323…`、`compare-final.json` `5a57ae1c…`（全值见 EVIDENCE-PACK §3） |
| 仓库出处 | [QPSK 报告](../reports/2026-10-23-p3-qpsk-offline.md) |
| 状态 | `部分`：开发回放，不冒充独立 TEST 集；未做实时 QPSK 采集/实体屏/物理 BER |

## 3. 双 TX 示波器实测（历史板级记录，2026-09-30）

| 项 | 值 |
|---|---|
| 版本/配置 | 2026-09-30 板级记录；LO 80 MHz、Fs 60 MSPS、RX/TX 带宽 30 MHz、衰减 0 mdB、2R2T、IDELAY 使能 tap7；CH1=TX1 经 HK-50，CH2=TX2 直连 |
| 结果 | 三帧下/上边带 **78.112 / 81.888 MHz**（目标 78.125/81.875，误差 **±13 kHz**）；12 个"通道×帧×边带"检验全过；削波比例 **0**；Vpp 0.385930/0.393970 V（CH1）、0.739698 V（CH2）；前后 OFF 对照成立 |
| 参考/判据 | 测量前冻结：频率误差 ≤50 kHz、边带高于局部中位底噪 ≥14 dB、ON 比 OFF ≥10 dB、三帧重复、无削波、源选中 |
| 原件 | `<LOCAL_EVIDENCE>/2026-09-30-dual-tx-scope/`：`acquisition.json` `f1368227…`、`analysis.json` `55efb1ac…`、`dual-tx-spectrum.png` `86619e40…`（本地证据根含原项目目录） |
| 仓库出处 | [双 TX 记录](../reports/2026-09-30-dual-tx-scope-and-touch.md) |
| 状态 | `部分`：**历史板级记录**（当时板上 release 与当前交付不同）；仪器读数未去嵌示波器频响/线缆/终端误差；不证明当前交付版本的 RF 输出 |

## 4. 环境诊断标定（2026-10-18，开发标定，非交付候选）

| 项 | 值 |
|---|---|
| 版本 | release `1c0894bfe7751be2e1afa4e8bac564b8`（2026-10-18 验收；**非当前交付版本**） |
| 结果 | 正式拟合输入仅 **20 帧 TRAIN normal**；独立验证 **VAL normal 10/10 CLEAR**、**3 个 OFF 零误判 CLEAR**、normal 无 INVALID；`calibration_status: development`、`installed: false` |
| 原件 | `<LOCAL_EVIDENCE>/rf-teaching/codex-s2-takeover-2026-10-18/completion.json` `69464437…`、`artifact-sha256.json` `a15238ff…` |
| 仓库出处 | [2026-10-18 报告](../reports/2026-10-18-codex-s2-takeover-completion.md) |
| 状态 | `部分`：开发期标定，不是交付版本验收；结论限采样窗口 |

## 5. 独立 RF Overlay 板级 L2 Stage 1（2026-09-18，历史板级记录）

| 项 | 值 |
|---|---|
| 版本/条件 | 独立 `rf_overlay.bit`（SHA-256 `4bc8ba11…`，本机构建 2026-09-17 17:58:44，板载逐字节相同）；1R1T；LO 80 MHz / 60 MSPS |
| 结果 | **8 PASS / 0 FAIL / 0 NOT-APPLICABLE / 2 RECORD-ONLY**；C1：LO 80.000 MHz、边带 78.128/81.872 MHz（对称偏差 0.0 kHz）；C2：Δf 1872.0→3744.0 kHz，比值 2.0000；C5：衰减 10000→30000 差 **19.85 dB**（另一次 19.20 dB）；AD9361 init 返回 0；287 个寄存器地址 **0 读失败**；`FCLK0/FCLK1` 读回 50.000/200.000 MHz |
| 保留缺陷 | 固件报的 `f_DATA_CLK = 20.000 MHz` 是 16 位窗计数器折回，真值 **120.0 MHz**；C3 的 `fs = 3×f_DATA_CLK` 标签受该缺陷污染，实质为 `fs = 60 MSPS = DATA_CLK/2`（与驱动回读 `60000000 Hz` 独立吻合） |
| 原件/仓库 | 仓库 `docs/rf/l2_stage1_record.md`（板级记录本体）、`docs/rf/l2_audit_stage1.txt`（SHA-256 `edd9e7a0…`）、`docs/rf/l2_audit_stage1_postexempt.txt`（`159d7131…`）、`rf_overlay/sw/trace_init_r4.txt`（寄存器 trace，仓库内） |
| 状态 | `部分`：独立 RF 目标的板级记录，**不是当前交付 bit**；WNS/WHS 数值与 `boardtest.md` 模板存在冲突（见 [rf-overlay-2026-09-19.md](rf-overlay-2026-09-19.md) §7） |

## 6. ARM 完整计算 P95 / RSS（历史候选，**均非当前交付版本**）

| 候选（日期） | P95 | RSS | 判等 | 原件（`<LOCAL_EVIDENCE>/rf-teaching/`） | 状态 |
|---|---:|---:|---|---|---|
| `b82b141e…`（2026-10-12） | **278.835456298293 ms**（≤300） | **31704 KiB** | 四窗 Host/ARM `parity_mismatches=[]`；原生 CNN；exit 0 | `s2/executable-candidate-2026-10-12/arm-runtime/receipt.json` `7a28de5a…` | 非当前版本 |
| `399b300e…`（2026-10-15） | **258.64086860092357 ms** | **31428 KiB** | `parity_mismatches=[]`；release/software 44/44 相符 | `s2-arm-qualification-2026-10-15/arm-run/receipt.json` `a450d569…` | 非当前版本 |
| `aba80e92…`（2026-10-16） | **272.785027401005 ms** | **31728 KiB** | `parity_mismatches=[]`；`new_rf_frames=0` | `s2-headroom-g2-2026-10-16/work/qualify-runtime/receipt.json` `a28f5e02…` | 非当前版本 |
| 当前交付口径 | `未取得` | `未取得` | — | — | `未取得` |

> 门槛：P95 ≤ 300 ms；RSS 记录预算 64 MiB（65536 KiB），并非所有阶段强制。**不得**把以上数字移植为当前交付版本性能。

## 7. 未运行 / 未证明（必须保留）

| 项 | 状态 |
|---|---|
| 完整物理验收（S2 正式 108 帧 + 12 离线负控） | `NOT_RUN` |
| 46 dB HEADROOM 真实研究 | `NOT_RUN` |
| EVM 接收指标 | `NOT_IMPLEMENTED` |
| 物理 RF/线缆回环验收（当前交付口径） | 未完成（验收文档明确"not run"） |
| 真人 LCD/触摸现场验收 | 待现场确认（AI 不代签）；触摸时延 `NOT_MEASURED` |
| QAM64 交付 | 已移出；历史 QAM64 报告与指标不得作为当前交付结果 |
| 板级功耗/温度实测 | 未取得（Vivado `report_power` 仅为工具估计，见 merged/rf/pynq 摘要 §5） |

## 8. 使用限制

- 本文件是**脱敏摘要**，与原件字节不同；原件保留本地，不能用本副本复算或验证原件哈希。
- 每条结果只对其版本/日期/条件成立；历史行（§2–§6 中标注者）不得用于当前交付版本。
- 不复制原始 IQ；逐帧原始数据保留在 `<LOCAL_EVIDENCE>` 下，公开副本只保留摘要与哈希。
