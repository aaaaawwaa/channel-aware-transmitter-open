# 设计报告：AC820 / Zynq-7020 + AD9361 便携收发与接收分析平台

本文是面向评审/读者的**设计说明**，只引用本仓库文档与源码，不新增实验、不换算指标。项目状态与失败限制以 [README](../../README.md)、[交付材料索引](DELIVERY-INDEX.md) 和 [作品简介追踪表](../requirements/brief-traceability.md) 为准；本文不宣称原简介全部完成。

## 1. 设计目标与边界

- **目标**：以 Zynq-7020 SoC + AD9361 收发器 + 五寸触摸屏构成便携式射频收发/接收分析平台；PL 承担实时样点侧逻辑，PS（板载 Linux）承担控制、分析与轻量推理；最终设想是“测量信道后选择发射波形”的受限智能选档。
- **当前交付范围（2026-10-26 交付复审口径）**：BPSK/QPSK 有限接收演示 + 有限 QAM16；QAM64 已移出交付；外部 SDR 发射、板卡纯接收为现场演示方向。
- **不在当前范围**：自动选档/档位注册表、统一全模式实时接收、EVM 接收口径、完整物理采集（S2 正式 108 帧 + 12 离线负控）、真人现场验收、统一发布清单。
- **证据纪律**：判据先于测量；未执行的层保持 `NOT_RUN`，没有原始收据的结论写 `UNVERIFIED`；历史报告只代表其写作当天状态。

## 2. 总体架构

功能框图（外部 SDR 激励与板卡接收，含 PL/PS 分工与边界声明）见 [system-architecture.md](system-architecture.md)。

```text
                  ┌──────────────────────────────┐
  PC 上位机 ──────│ host/（PySide6，辅助 AWG 用）│
                  └──────────────────────────────┘
  ┌──────────────── 板载 PYNQ Linux ──────────────┐
  │ pynq_overlay/sw/ 界面/状态/分析/发布           │
  │   ui_model/ui_view + 触摸  ──  后台 worker     │
  │   rf_status_worker / rf_status_client          │
  │   rf_bpsk_receiver / rf_qpsk_receiver          │
  │   rf_environment / rf_headroom / rf_cnn_runtime│
  └───────────────┬───────────────────────────────┘
                  │ TCP/JSON 控制协议（板端 C daemon）
  ┌───────────────┴───────────────────────────────┐
  │ rf_overlay/sw/board_app/   rf_daemon（唯一控制者）│
  │   rf_core 状态机/寄存器；platform_rf 平台适配    │
  │   厂商 AD9361 no-OS 驱动（vendor/，逐字节副本）  │
  └───────────────┬───────────────────────────────┘
                  │ MMIO / SPI / LVDS
  ┌───────────────┴───────────────────────────────┐
  │ rf_overlay/rtl/  ad9361_phy / phy_rx / phy_tx  │
  │   pl_spi_master / rf_sample_capture /          │
  │   rf_sample_reader / rf_decimate / rf_tx_source│
  │   自定义 axi_ctrl（寄存器/BRAM 接口）           │
  └────────────────────────────────────────────────┘
```

三条流水线相互独立、产物不混用：

| 流水线 | 目录 | 定位 |
|---|---|---|
| RF 主项目（AD9361） | `rf_overlay/` | 本平台核心：PL 收发、板端 daemon、主机侧判据与测试 |
| PYNQ 板载应用 | `pynq_overlay/` | 板载五寸屏 UI、状态拥有者、后台分析/推理、发布与安装 |
| 辅助 AWG | `src/`、`sdk/src/`、`host/` | 独立信号发生器开发链，不参与 RF 产品验收 |

## 3. 关键设计决定

1. **单控制者（daemon）原则**：板端 `rf_daemon` 是唯一持有 PL 与 AD9361 的进程；其他脚本不得在 daemon 持有时再次调用 `Overlay()`。协议版本、release 身份、状态读回由 daemon 统一管理，避免多入口互相覆盖。
2. **有限快照、非连续接收**：接收以 4096 点有限 IQ 快照为基本单位，带入采样域、配置修订、模型版本与 `capture_id`；不把快照结果表述为连续接收或空口监测。
3. **判据先于测量**：数字回环/物理回环/真实采集三层分开；每层先定义判据与拒绝条件（例如同步位、BER 分母、削顶、采样域检查），再执行测量并保留失败原帧。
4. **建议而非自动**：CNN/环境诊断结果只允许填入草稿；自动执行（`rx_auto_block`）永久关闭，人工确认后才进入下一步，避免自动决策造成不可逆发射。
5. **发布可回退**：安装/试部署有明确的发布身份、清单与回退父版本；试部署失败注入负例后自动回滚，终态读回与日志原件保留。
6. **逐字节第三方边界**：`rf_overlay/sw/board_app/vendor/` 与 `rf_overlay/sw/ref/` 保持原始字节，`SHA256SUMS` 校验；第三方版权与许可证在 `NOTICE`/`LICENSES/` 中保留，未确认来源单独披露，不重新授权。

## 4. 接收与分析链路

- **样点路径**：AD9361 LVDS 差分接口 → `phy_rx` / `rf_sample_reader` → `rf_sample_capture`（BRAM）→ PS 读回；发射侧 `rf_tx_source` / `phy_tx` 由 daemon 配置。
- **快照接收机**：`pynq_overlay/sw/rf_bpsk_receiver.py`（BPSK 3 MSym/s RECT 快照、独立 PRBS 参考与 BER）与 `rf_qpsk_receiver.py`（QPSK/RECT，显式 I/Q 次序与具名不可用原因）；统一结果契约见 `rf_modem_result.py`。
- **展示**：`rf_rx_analysis.py`（频谱/时域/RMS/削顶，模块头声明不输出 EVM/Vpp/dBm）与 `rf_rx_constellation.py`（最多 128 点星座，不声明 EVM）。EVM 未实现。
- **环境诊断**：`rf_environment.py`（`rf-window-environment-v3`，11 项特征，标签 `CLEAR/NARROWBAND/PULSE/INVALID/UNKNOWN` 与具名理由）；结论限定在采样窗口内。
- **模型**：板端 `rf_cnn_runtime.py` 运行六类轻量 CNN（`dc-complex-rms-v1` 预处理）；训练与评估在 `ml_training/`（独立离线流程），模型产物与数据集不随仓库。

## 5. 发射波形与辅助 AWG

- `pynq_overlay/sw/rf_iq_signal.py` 生成 BPSK/QPSK/QAM16/QAM64 × RECT/RRC 帧（QAM64 已移出本轮交付），包含 PRBS、量化与打包；映射/成形/量化口径的冻结说明见 [TX 模式合同](../superpowers/specs/2026-10-13-rf-tx-mode-contract.md)。
- 辅助 AWG 的 PL 数字调制/RRC 与 RF 路径是两套产物，不能互相顶替；AWG 的历史验收见 [golden/BASELINE.md](../golden/BASELINE.md)。
- 外部 SDR 测试台（`sdr_testbench/`）用 PlutoSDR + GNU Radio 做独立发射/接收与示波器观测；其 `RF 输出未验证`，详见 [TEST-REPORT.md](../../sdr_testbench/TEST-REPORT.md)。

## 6. 发布、部署与回退

- 发布身份与软件摘要：`pynq_overlay/sw/merged_release.py`、`scripts/release_manifest.py`、`scripts/rf_teaching_install_state.py`。
- 安装/试部署：`pynq_overlay/sw/install.sh`、`scripts/rf_teaching_trial_install.sh`；终态 `TRIAL_ACCEPTED` / `TRIAL_ROLLED_BACK` / `TRIAL_RECOVERY_REQUIRED`，流程见 [试部署 runbook](../rf/deployment-trial-runbook.md)。
- 已记录的有限验证：候选安装与只读读回、试部署注入失败后自动回滚；这些只覆盖对应候选与日期，不代表新源码继承旧批准。

## 7. 质量与验证策略

- **六类门**：离线 → ARM 原生 → 数字回环 → 有线物理回环 → 真人 → 发布；未执行层保持 `NOT_RUN`（见 [验收清单](../requirements/acceptance-checklists-p1-p6.md)）。
- **测试分层**：PYNQ/脚本/主机 Python 套件、板端 C 原生套件（含假硬件夹具）、RTL 仿真（XSIM）、主机/ARM 判等、离线负例与变异验证；测试数量不能替代物理/真人结果。
- **综合/实现与实测指标**：合并产品、独立 RF、独立 PYNQ 三个目标的资源/时序/时钟/功耗与上板性能汇总见 [综合与实现指标](SYNTHESIS-METRICS.md)；优化前后同口径对比目前**未取得**（原因与缺口见该文 §5–§6）。
- **失败保留**：失败帧、失败日志与未通过结论不删除、不改写；新结论以新文件记录（见 [工程记忆](../memory/README.md)）。

## 8. 已知限制（设计层面）

- PL 未实现实时同步解调/RRC/多制式处理；PS 侧只验证过有限快照分析与轻量推理。
- 自动选档/档位注册表未实现；只有“建议填草稿 + 人工确认”。
- 完整物理采集、真实环境闭环、正式发布清单与真人验收未完成。
- 第三方参考工程 RTL 的许可未确认（见 [LICENSES/UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)）；公开仓库不代表这些文件已获再分发许可。
- 本仓库不是可安装发布包：位流/HWH、原始 IQ、数据集与模型产物不在仓库内。

## 9. 参考

- [工程结构](PROJECT-STRUCTURE.md)
- [作品简介追踪表](../requirements/brief-traceability.md)
- [交付材料索引](DELIVERY-INDEX.md)
- [综合与实现指标](SYNTHESIS-METRICS.md)
- [指南 3.3 符合性映射](GUIDE-CONFORMANCE.md)
- [竞赛提交材料与符合性说明](COMPETITION-MATERIALS.md)
- [技能包入口](../../skills/README.md)（指南 `skill/` 映射见 [skill/README.md](../../skill/README.md)）
- [大模型协作记录](AI-COLLABORATION-RECORD.md)
- [AI 协作案例](AI-COLLABORATION-CASE.md)
- [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)
- [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md)
- [report/ 入口](../../report/README.md)
