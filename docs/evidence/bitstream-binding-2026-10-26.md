# 交付 bit 身份与载荷绑定摘要（2026-10-26）

> **性质**：公开**脱敏摘要**，不是原件。原件在开发机 `<LOCAL_EVIDENCE>/`（发布清单、载荷绑定复核记录、安装读回日志），含开发机路径与板端账户名，未随仓库；本文件字节与原件不同。
> **作用**：说明**当前交付 Overlay 位流是谁、和哪次综合/实现构建同源**，以及"报告里的资源/时序表凭什么能描述交付 bit"。
> **不作用**：不背书 QAM64 历史结论，不证明板端功能验收，不替代原始收据。

## 1. 交付 release 身份（照抄发布清单）

| 项 | 值 |
|---|---|
| 交付复审口径 | 2026-10-26（BPSK/QPSK 有限演示 + 受限 QAM16；QAM64 已移出交付） |
| release id | `4814bbc42b9d5e0165a3ed3ebe7eaeef` |
| 位流 | `awg_merged.bit`，SHA-256 **`d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555`** |
| HWH | `awg_merged.hwh`，SHA-256 **`ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0`** |
| 板端 daemon | `rf_daemon`，SHA-256 `e51e97ae8d61d31880963cf9d9a9d8a15172872c892cf17865f5d0a9703c9a91`（版本 `1.0.4`，协议 `1`） |
| 软件合同 | `cnn-bpsk-modrec4-qpsk-rrc-qam16-v1`（62 个 software 摘要；QAM64 接收/管线不随包） |
| 候选包 | `candidate.tgz`，SHA-256 `74c6e32c9edc99f2f566dd5e822dc6e271ff997762bdce1eb89753e1c370f267` |
| 回退包 | `036a4ec9ef4cea7fa108abbb02b00300` |
| build_id | `cnn-bpsk-teaching-v1-20261004-s1` |
| 地址契约（节选） | `axi_ctrl_0 0x43000000`、`pl_spi_0 0x43010000`、`axi_bram_ctrl_tx 0x43030000`、`axi_bram_ctrl_rx 0x43040000`、`axi_dma_0 0x40400000`、`ps7_0 0x00000000` |
| 原件 | `<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/candidate/merged_release_manifest.json`，SHA-256 `7aba832d2ae706a90f252afaaa08029d8b37a34b2880552d8c365f0d9ed854b9` |

## 2. 绑定链：交付 bit → 同次构建 bit → routed DCP

| 层 | 对象 | SHA-256 | 说明 |
|---|---|---|---|
| ① 交付位流 | `awg_merged.bit` | `d5bfe5d9…`（全值见 §1） | 当前交付 release 内位流（**不在仓库**） |
| ② 同次构建位流 | `host_bd_wrapper.bit`（2026-09-24 10:11:52） | `76e242fe0438e1bec5596ef93be256f0532a53968c991b103bd710c89f08e1bb` | 本地原构建产物；`build_manifest.json` 记录同值 |
| ③ 可编程载荷 | 两 bit 自 sync 起的内容 | `0ce74071ee4f4ee625539a19ef3eef124f89aeeddb09c5ac68e157451f4ffb5e` | `payload_equal: true`；两侧 sync 偏移均为 **175** |
| ④ routed DCP | `host_bd_wrapper_routed.dcp` | `63f679bcce815ba6c883f2e47b286dfc35a549ae9d040eba028548bd2be7c305` | 第②层 bitstream 的同一实现运行的 checkpoint（报告即针对它生成） |

**结论（可复核）**：交付位流与本地 2026-09-24 构建位流的**文件级 SHA 不同**（bit 文件头/元数据差异），但**可编程载荷逐字节相同**；因此 [merged-overlay-2026-09-24.md](merged-overlay-2026-09-24.md) 摘录的资源/时序/功耗估计/DRC/路由报告**描述的就是交付 bit 的 PL 内容**。

**绑定记录原件**：`<LOCAL_EVIDENCE>/codex-frame-idelay-check-20261025/verification.json`，SHA-256 `3ada5d36107d25b0efd9eef987075a497b20cc3968980ca5cdc742242aa82ef9`。该记录同时写明：`tool: Vivado 2025.2`；`board_access: false`、`synthesis: false`、`implementation: false`、`programming: false`、`product_changes: false`（即：只读比对，未重新综合/实现、未开板）。同一记录里的 `current_release` 为 `6384a07e2d7d135bfb021b9ac75f337f`——那是 QAM64 时代的安装候选；交付 `4814bbc4` 使用**同一 bit/hwh**，故载荷绑定对交付版同样成立。本记录**不**背书 QAM64 相关结论。

## 3. 安装与只读读回（2026-10-26，摘录）

| 项 | 结果 |
|---|---|
| 安装终态 | `TRIAL_ACCEPTED`；`current` 与 `active` 读回均为 `4814bbc4…` |
| 服务 | `pynq-status`、`rf-daemon` 均 `active/running`，`NRestarts=0` |
| RF 安全态（读回 JSON） | `armed=false`、`core=false`、`tx=false`、`rx_armed=false`、`io_ok=true`；`idelay_en.raw=127`、`idelay_tap.raw=29`；`release_id=4814bbc4…` |
| 回退 | 有 `036a4ec9…` 回退包；本摘要不涉及回滚执行 |
| 原件 | `<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/acceptance/final-readback.log`，SHA-256 `9822da253076f060476195207e9565699e5092f3a70a765b1c7b1e08b03a93de` |

> **限定**：这是 2026-10-26 19:26 那一次只读读回的快照，**不代表当前板态**，也不构成现场验收。

## 4. 交付冒烟结论（与本节身份绑定，但属另一证据层）

交付 release 上的 12 帧有限冒烟：BPSK 两 ON 均 0/138、QPSK 两 ON 均 0/128、QAM16 分别 1/256 与 0/256，各模式 OFF 帧均无残留参考/ BER；QAM64 移除负例 9/9。摘要与逐项出处见 [board-results-2026-10-26.md](board-results-2026-10-26.md)；本文件不复述细节。
**保留的负面事实**：QAM16 ON1 的 1/256 是**指标负例**，不写成"全零误码"；接收验收集为 12 帧，但失败尝试中另有 2 次 QAM16 ON2 采集，物理计数为 14（见验收文档与交付复审）。

## 5. 证据清单（本文件引用原件）

| 原件（`<LOCAL_EVIDENCE>/rf-teaching/qam64-delivery-release-2026-10-26/`） | SHA-256 |
|---|---|
| `candidate/merged_release_manifest.json` | `7aba832d2ae706a90f252afaaa08029d8b37a34b2880552d8c365f0d9ed854b9` |
| `candidate/candidate.tgz` | `74c6e32c9edc99f2f566dd5e822dc6e271ff997762bdce1eb89753e1c370f267` |
| `REPORT.md` | `d0db54ac1cdf83087b295d69050ca689ca2de0138db8429e5c23f8bf06aa72c2` |
| `SHA256SUMS.txt`（覆盖本目录 107 个文件） | `a83a3c0a235279c4401d8a8ae963910f3a5b759eab7e794fb9f3af618a53211c` |
| `acceptance/aggregate.json` | `8e63a511a15da4792706b92da9e255cac780e8504b677adc085ed7717cce7dcb` |
| `acceptance/final-readback.log` | `9822da253076f060476195207e9565699e5092f3a70a765b1c7b1e08b03a93de` |
| `docs/ACCEPTANCE-RESULTS.md` | `cb2c0c9933585cb2efcd5d998a8b8962ee027635aea914c2720d1f8d06286872` |
| `<LOCAL_EVIDENCE>/codex-frame-idelay-check-20261025/verification.json` | `3ada5d36107d25b0efd9eef987075a497b20cc3968980ca5cdc742242aa82ef9` |
| `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper.bit` | `76e242fe0438e1bec5596ef93be256f0532a53968c991b103bd710c89f08e1bb` |
| `<LOCAL_EVIDENCE>/awg/awg.runs/impl_1/host_bd_wrapper_routed.dcp` | `63f679bcce815ba6c883f2e47b286dfc35a549ae9d040eba028548bd2be7c305` |

## 6. 本摘要**不**证明什么

- 不证明交付 bit 文件可由本仓库重建：本仓库没有 bit/HWH/DCP，且该 bit 是 2026-09-29 工作区构建（当时 `create_bd.tcl`/`awg.xdc` 等为未提交修改，见 `docs/reports/2026-09-30-backlog-triage.md` §0）。
- 不证明板端行为、不证明 QAM64 研究结论、不证明物理接口裕量。
- 不允许把 `payload_equal` 解释成"文件完全相同"：文件级 SHA 不同（见 §2）。
