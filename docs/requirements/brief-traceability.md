# 作品简介追踪表（Brief Traceability）

日期：2026-10-14（P0 / Task 1.1）。责任文件：实施计划 `docs/superpowers/plans/2026-10-13-final-prj-implementation-plan.md` Task 1.1。

本表把作品简介逐条追到**责任模块、现有测试、历史原始收据路径和当前状态**。没有可核查原始证据的条目一律写 `UNVERIFIED`；没有实板/真人结果的条目不得写通过。

## 0. 文本来源与边界

- 简介原文由用户 2026-10-13 提供。**仓库内保存的是转录而非原文**：`docs/reports/2026-10-13-project-brief-alignment-audit.md` 第 3 行给出引文，实施计划 Task 1.1 给出分句清单（平台/硬件、各 TX 模式、RX/同步、星座/频谱/EVM/BER/数据、PL/PS、触摸、环境、决策、三层验证）。
- 因此本表编号按**语义分句**建立（B1–B10），标注 `TEXT_SOURCE: TRANSCRIBED_PARTIAL`。若用户提供逐句原文，需按原文重新编号并逐句复核；在此之前不得声称"简介逐句已闭合"。
- 历史原始收据与原始 IQ 的权威来源仍是源工程；必要封存件已按 SHA-256 复制到本副本，见 `evidence/rf-teaching/imported-2026-10-14-manifest.json`、`evidence/rf-teaching/p0-followup-import-2026-10-14.json` 和 `evidence/rf-teaching/p0-arm-native-import-2026-10-14.json`。本表中 `SOURCE/...` 表示原工程路径，不自动表示副本中缺件。
- **门禁现状（2026-10-14 续）**：Gate P0-0 已由用户确认暂停，证据等级为 `CONFIRMED_BY_USER`，没有工具级任务状态证明。历史只读盘点及迁移过程见[切换报告](../reports/2026-10-13-final-prj-focus-decision.md)；本表的功能状态仍须逐项按原始证据复核。

## 1. 状态词定义（每条只用其中之一作为最高已获状态）

| 状态 | 含义 |
|---|---|
| `NOT_IMPLEMENTED` | 责任模块尚不存在 |
| `CODED` | 有实现，但无该层证据 |
| `OFFLINE_VERIFIED` | 离线测试可复现通过（本次 P0 已实跑，见基线报告） |
| `ARM_VERIFIED` | 板上 ARM 原生构建/运行证据 |
| `BOARD_VERIFIED` | 真实板卡运行证据（数字回环/有线回环/真实采集，按条目注明范围） |
| `HUMAN_VERIFIED` | 真人在实体屏上完成并留证据 |
| `RELEASED` | 有绑定源码摘要的发布身份与回退版本，且安装核验通过 |
| `UNVERIFIED` | 有报告但无原始证据可核，或用户声明未留证据 |
| `NOT_RUN` | 该层动作从未执行 |

## 2. 逐句追踪

### B1 平台/硬件：以 Zynq SoC 与 AD9361 为核心的便携平台（含五寸触摸屏）

- 源码入口：`rf_overlay/`（AD9361 PL：`rtl/ad9361_phy.v`、`rtl/pl_spi_master.v`、`rtl/phy_rx.v`、`rtl/phy_tx.v`、`rtl/rf_sample_capture.v`、`rtl/rf_sample_reader.v`、`rtl/rf_decimate.v`、`rtl/rf_tx_source.v`）；`rf_overlay/sw/board_app/`（C 守护进程 `rf_daemon.c`、`rf_core.c`、`rf_main.c`）；`pynq_overlay/sw/`（板载 Linux Python：`rf_status_worker.py`、`rf_status_client.py`、`ui_model.py`、`ui_view.py`、`pynq_touch.py`）。
- 现有测试：`rf_overlay/sw/board_app/test/test_rf_core.c`、`test_rf_daemon.c`、`test_rf_protocol.c`、`test_rf_state.c`、`test_platform_rf.c`、`test_rf_transport_policy.c`、`test_rf_sleep.c`、`test_rf_iq.c`、`test_rf_headroom*.c`（`make host-test`）；`rf_overlay/tb/*.v`（XSIM）；`pynq_overlay/sw/tests/test_rf_status_worker.py`、`test_rf_status_client.py`、`test_rf_control_pipeline.py`。
- 历史原始收据：`SOURCE/evidence/rf-teaching/s1/deployment-2026-10-04/live-receipt.json`、`SOURCE/evidence/rf-teaching/s2/executable-candidate-2026-10-12/stage-checkpoint.json`、`SOURCE/evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json`。
- 当前状态：`RELEASED`（有限域）：`cnn-bpsk-headroom-v1` 候选 `b82b141e355b4cb1833969383a409c79` 于 2026-10-13 安装，收据 `INSTALLED_VERIFIED`、`accepted=true`、`new_rf_frames=0`、`real_initializations=0`，只读核验 `current==active_release==b82…`、双服务 active、`rf_safe=true`、无内核存储故障；父版本 `b67a8aaa…` 保留回退。
- 差距：README 主入口仍是 ACM2108 AWG；三条流水线各自独立，未做统一发布清单与"RF 产品入口"（见 Task 1.3 与本表 B10）。

### B2 各 TX 模式：BPSK、QPSK、多阶 QAM 生成及 RRC 成形

- 源码入口：`pynq_overlay/sw/rf_iq_signal.py`（`MODES=('BPSK','QPSK','QAM16','QAM64')`、`FILTERS=('RECT','RRC')`、`ROLLOFFS=('0.2','0.35','0.5')`、`PRBS_SYMBOLS=127`、`rrc_taps`/`_rrc_pulse`、`quantize_iq`、`pack_iq`、`generate_digital_frame`）；板端 `rf_overlay/sw/board_app/rf_iq.c`、`rf_main.c` 与 PL `rtl/phy_tx.v`、`rtl/rf_tx_source.v` 承担样点搬运/发送。AWG 侧另有一套数字调制 PL（`src/rtl/iq_constellation_mapper.v`、`rrc_digital_mod.v` 等），**不属 RF 路径，不得互相顶替**。
- 现有测试：`pynq_overlay/sw/tests/test_rf_iq_signal.py`；AWG 侧 `host/tests/test_digital_modulation*.py`、`scripts/scope_verify_user.py`（AWG 管线，另计）。
- 历史原始收据：`SOURCE/evidence/rf-teaching/s1/deployment-2026-10-04/live-receipt.json`（BPSK 3 MSym/s + RECT 的有线实测）；`SOURCE/evidence/rf-teaching/s2/g1-qualification-2026-10-08/stage-checkpoint.json`（BPSK 域）；`SOURCE/evidence/rx-smart/live-production-2026-10-03/final-receipt.json`（AWG 生产模型实板，**不是 RF AD9361 实物**）。
- 当前状态：`OFFLINE_VERIFIED`（四种模式与两种成形均有离线生成/打包测试）。**实物发射仅 BPSK 3 MSym/s RECT 有历史有线证据**；QPSK/QAM16/QAM64 的 AD9361 实物输出 = `NOT_RUN`；RRC 实物输出 = `NOT_RUN`。
- 差距：逐模式的 Gray 映射、位序、群延迟、量化、占用带宽、拒绝条件未冻结成独立合同与参考向量（P2 / Task 3.1）。

### B3 RX/同步：接收采样与同步解调

- 源码入口：`pynq_overlay/sw/rf_rx_types.py`（`RxIqFrame` 4096 点 / `s16_iq_le` / `rx.capture`·`loopback.physical.capture`）、`rf_bpsk_receiver.py`（`VERSION='bpsk-rect-snapshot-v2'`、`SPS=20`、`ALIGNMENT_BITS=63`、`MIN_COMPARE_BITS=64`、`MAX_CFO_HZ=100000`）、`rf_rx_analysis.py`、`rf_rx_constellation.py`。
- 现有测试：`pynq_overlay/sw/tests/test_rf_bpsk_receiver.py`、`test_rf_bpsk_pipeline.py`、`test_rf_rx_pipeline.py`、`test_rf_rx_analysis.py`、`test_rf_rx_constellation.py`、`test_rf_rx_client.py`、`test_rf_rx_view.py`；`scripts/tests/test_rf_receiver_parity_run.py`（主机/ARM 判等）。
- 历史原始收据：`SOURCE/evidence/rf-teaching/s1/deployment-2026-10-04/live-receipt.json`（3 组 30 ON/30 同步、0/4140、3 OFF 无 BER）；`SOURCE/evidence/rx-bpsk/m4/live-acceptance-receipt.json`（30/30 同步、0/4140）；`SOURCE/evidence/rf-teaching/s2/g1-qualification-2026-10-08/stage-checkpoint.json`（`parity_mismatches: []`、P95 290.81 ms）。
- 当前状态：`BOARD_VERIFIED`（**限 BPSK 3 MSym/s、RECT、60 MSPS、4096 点快照、有线 TX1→RX1**）。
- 差距：QPSK/QAM16/QAM64 与 RRC 接收 `NOT_IMPLEMENTED`；仅有限快照，非连续接收；采样域外的结果一律拒绝。

### B4 星座/频谱/EVM/BER/接收数据查看

- 源码入口：`rf_rx_constellation.py`（`MAX_POINTS=128`；模块头明确"不声明 EVM"）、`rf_rx_analysis.py`（`SPECTRUM_WINDOW='hann'`、`PREVIEW_COMPLEX_SAMPLES=64`、`OCCUPIED_BANDWIDTH_FRACTION=0.99`；模块头明确"从不输出 Vpp/dBm/SNR/EVM/BER"）、`rf_bpsk_receiver.py`（`BpskResult.ber` 只由独立 PRBS 参考计算，前 63 判决仅用于对齐不计入 BER）、`rf_experiment_record.py`（`MAX_IQ_BYTES=16384` 有界 IQ、`write_record`/`read_record`）、`ui_view.py`（`快照估计 / CRC、帧、EVM未接入`）。
- 现有测试：`test_rf_rx_constellation.py`、`test_rf_rx_analysis.py`、`test_rf_experiment_record.py`、`test_rf_experiment_scenarios.py`、`test_rf_bpsk_ui_model.py`、`test_rf_bpsk_ui_view.py`。
- 历史原始收据：`SOURCE/evidence/rf-teaching/s1/human-photo-review-2026-10-04/photo-review-receipt.json`（实体屏 128 点/201 符号、0/138）、`SOURCE/evidence/rf-teaching/s1/deployment-2026-10-04/live-receipt.json`（ARM 写读与主机回放一致）。
- 当前状态：星座/频谱/削顶与 RMS `BOARD_VERIFIED`（限 BPSK 域）；BER `BOARD_VERIFIED`（独立 PRBS 参考、有限帧）；**EVM = `NOT_IMPLEMENTED`**（代码与界面均显式标注未接入）；"接收数据查看"当前只是有界 IQ 预览＋判决/比特预览，**没有协议帧/CRC 载荷**。
- 差距：P3 / Task 4.3 的 EVM 口径、BER 分母/置信区间与跨会话聚合规则未定义；单帧约 205 符号，不能凭单帧零错宣称低 BER。

### B5 PL/PS 分工：PL 实时样点处理，PS 控制/特征/轻量推理

- 源码入口：PL `rf_overlay/rtl/`（`ad9361_phy.v`、`pl_spi_master.v`、`phy_rx.v`、`phy_tx.v`、`rf_sample_capture.v`、`rf_sample_reader.v`、`rf_decimate.v`、`rf_tx_source.v`、`dds_inc.v`、`sine_rom.v`、`rf_overlay/bd`）；PS `rf_overlay/sw/board_app/rf_core.c`（状态机/寄存器）、`rf_daemon.c`（单板 RF 控制者）、`pynq_overlay/sw/rf_status_worker.py`（后台分析/推理）、`rf_cnn_runtime.py`（`CLASSES=('TONE','BPSK','QPSK','QAM16','QAM64','OTHER')`、`PREPROCESSING='dc-complex-rms-v1'`、`MAX_MODEL_BYTES=128*1024`）。
- 现有测试：`rf_overlay/tb/*_tb.v`（XSIM 仿真）；`test_rf_core.c`/`test_rf_daemon.c` 等主机侧 C 测试；`pynq_overlay/sw/tests/test_rf_cnn_runtime.py`、`test_rf_control_pipeline.py`、`test_rf_control_background.py`；`scripts/tests/test_rf_receiver_parity_run.py`。
- 历史原始收据：`SOURCE/evidence/rf-teaching/s2/executable-candidate-2026-10-12/stage-checkpoint.json`（隔离 ARM G1：同帧 4 窗完整计算与 8 场景/96 证据主机/ARM 判等空 mismatch、100 次完整计算 P95 278.835 ms ≤300 ms、RSS 31704 KiB、native CNN、exit 0）。
- 当前状态：PL 承担样点收发与有限采集（`BOARD_VERIFIED` 有限）；PS 承担配置、快照分析与轻量 CNN（`ARM_VERIFIED`）。
- 差距：**PL 未做实时同步解调/RRC/多制式处理**，不得如此表述；PS 侧延迟/吞吐/CPU/RSS 只测过"完整计算"路径，未测生成/拷贝/PL 传输分解（P2 / Task 3.1 第 4 条）。

### B6 触摸：五寸触摸屏配置与同帧展示、人工接管

- 源码入口：`pynq_overlay/sw/pynq_touch.py`（GT911，800×480）、`ui_view.py`、`ui_model.py`、`rf_touch_view.py`/`rf_touch_model.py` 相关测试、`rf_teaching_profile.py`（教学草稿一键填写，拒绝运行中/不兼容配置）。
- 现有测试：`pynq_overlay/sw/tests/test_rf_touch_model.py`、`test_rf_touch_view.py`、`test_rf_touch_parameters.py`、`test_ui_model.py`、`test_ui_view.py`、`test_rf_bpsk_ui_view.py`。
- 历史原始收据：`SOURCE/evidence/rx-bpsk/m4/verified-ui-pilot-2026-10-03/`（软件点击命中→生产后台→同帧显示）、`SOURCE/evidence/rf-teaching/s1/human-photo-review-2026-10-04/photo-review-receipt.json`（11 张实体屏照片）、`SOURCE/evidence/rx-smart/human-loopback-fix-2026-10-03/human-final-confirmation.json`。
- 当前状态：软件点击路径 `BOARD_VERIFIED`；真人实体屏 `HUMAN_VERIFIED`（**部分**：功能项与照片已闭合，触摸 P95 `NOT_MEASURED`，且用户已明确取消专门触摸计时，不作为阻塞项）。
- 差距：多制式/QAM/RRC 的界面入口与同帧结果展示（P3）、档位建议的人工接管界面（P5）尚未实现。

### B7 环境：窄带与脉冲环境识别、可信度与运行约束

- 源码入口：`pynq_overlay/sw/rf_environment.py`（`VERSION='rf-window-environment-v3'`、`EnvironmentBaseline`/`EnvironmentResult`、`IDENTITY_FIELDS`、11 项特征；标签 `CLEAR`/`NARROWBAND`/`PULSE`/`INVALID`/`UNKNOWN`＋具名理由如 `BASELINE_MISSING`、`COMPOUND_RULES`、`WEAK_SPECTRAL_ANOMALY`、`BPSK_NOT_RELIABLE`、`LOW_ENERGY`）、`pynq_overlay/sw/rf_environment_baseline.json`、`rf_headroom.py`、`rf_headroom_replay.py`、`scripts/rf_environment_{dataset,acceptance,verify}.py`、`scripts/rf_headroom*.py`。
- 现有测试：`pynq_overlay/sw/tests/test_rf_environment*.py`（共 10 个）、`test_rf_headroom*.py`（6 个）、`test_rf_waveform_quality.py`；`scripts/tests/test_rf_environment_*.py`、`test_rf_headroom_*.py`。
- 历史原始收据：`SOURCE/evidence/rf-teaching/s2/g2-execution-2026-10-10/stage-checkpoint.json`（G2 因窄带 CLIPPED 停止、对照证据不足）、`SOURCE/evidence/rf-teaching/s2/headroom-contract-2026-10-11/stage-checkpoint.json`、`SOURCE/evidence/rf-teaching/s2/headroom-research-2026-10-11/stage-checkpoint.json`（46 dB 研究被 `lease_held=true` 谓词阻塞）、`SOURCE/evidence/rf-teaching/s2/headroom-runner-2026-10-12/receipt.json`（runner 离线，`board_contact=false`）。
- 当前状态：离线/ARM 资格 `OFFLINE_VERIFIED` + `ARM_VERIFIED`；真实环境闭环 `NOT_RUN`（46 dB 研究未执行；S2 正式 108 物理帧＋12 离线负控未开始）。环境结论只能表述为"采样窗口内"。
- 差距：复合/域外事件的显式建模、短窗口漏检边界、可信度校准（当前只有分数与拒绝原因）——P4。

### B8 决策：已验证档位选择、人工接管、异常回退

- 源码入口：`pynq_overlay/sw/ui_model.py` 的 `rx_auto_block()`（注释与实现均为"自动执行永远关闭，建议只允许进入草稿"，恒返回 `UNSUPPORTED`）、`rf_teaching_profile.fill_drafts()`、CNN 建议路径（`rf_cnn_runtime.py` + worker）；**不存在档位注册表/策略模块**。
- 现有测试：`test_rf_smart_pipeline.py`、`test_rf_control_pipeline.py`、`test_rf_headroom_activation.py`（scripts/tests）、`test_rf_teaching_acceptance.py`。
- 历史原始收据：`SOURCE/evidence/rx-smart/human-loopback-fix-2026-10-03/human-final-confirmation.json`（用户确认"建议仅改草稿/31 秒过期/停止并解除布防"）、`SOURCE/evidence/rx-smart/live-production-2026-10-03/final-receipt.json`。
- 当前状态：`CODED` + 部分 `BOARD_VERIFIED`（建议仅填草稿、AUTO 永久关闭、停止/解除/回退链已有证据）。**已证明收益的第二档位不存在 → 档位决策 `NOT_IMPLEMENTED`**；P5a/P5b 未开始，简介中"智能选档"当前为 `NOT_VERIFIED`。

### B9 三层验证：数字回环、模拟（有线）回环、真实采集分阶段

- 源码入口/工具：`rf_overlay/sw/loopback_criteria.py`、`loopback_diag_tap.py`、`loopback_tap_sweep.py`、`physical_loopback_acceptance.py`、`scope_boundary.py`、`scope_tx_check.py`；`scripts/rf_capture_settle_run.py`、`scripts/rf_capture_timing_probe.py`；`pynq_overlay/sw/rf_experiment_scenarios.py`。
- 现有测试：`rf_overlay/sw/board_app/test/test_rf_daemon.c`（数字回环路径）、`test_rf_headroom*.c`；`scripts/tests/test_rf_capture_settle_run.py`、`test_rf_capture_timing_probe.py`、`test_rf_capture_timing_run.py`；`pynq_overlay/sw/tests/test_rf_capture_timing.py`、`test_rf_experiment_scenarios.py`。
- 历史原始收据：`SOURCE/evidence/rf-teaching/s2/settle-closure-20261008-v1/stage-checkpoint-round3.json`、`SOURCE/evidence/rf-teaching/s2/board-recovery-2026-10-12/recovery-checkpoint.json`、`SOURCE/evidence/rf-teaching/s2/storage-maintenance-2026-10-12/stage-checkpoint.json`。
- 当前状态：数字回环与有线 TX1→RX1 物理回环 `BOARD_VERIFIED`（有限工况）；真实多会话采集 `PARTIAL`（开发物理帧累计 67，正式 S2 108+12 `NOT_RUN`）。
- 差距：全简介口径需要**逐调制/逐接收模式/逐干扰类别/逐决策流程**的独立闭环，当前只覆盖 BPSK 域。

### B10 发布与可复现（工程层，来自 Gate P6）

- 源码入口：`pynq_overlay/sw/merged_release.py`、`pynq_overlay/sw/install.sh`、`scripts/release_manifest.py`、`scripts/rf_teaching_verify.py`、`scripts/rf_teaching_install_state.py`、`scripts/rf_headroom_deploy.py`、`pynq_overlay/sw/rf_teaching_profile.py`。
- 现有测试：`pynq_overlay/sw/tests/test_rf_teaching_release.py`、`test_rf_bpsk_release.py`、`test_rf_model_release.py`、`test_rf_environment_release.py`、`test_rf_teaching_deploy.py`、`test_rf_teaching_verify.py`；`scripts/tests/test_rf_headroom_deploy.py`、`test_rf_headroom_prepare.py`。
- 历史原始收据：`SOURCE/evidence/rf-teaching/s2/executable-candidate-2026-10-12/stage-checkpoint.json`（46 项 software map、候选 b82 绑定）、`SOURCE/evidence/rf-teaching/s2/headroom-install-2026-10-13/attempt-1/acceptance-receipt-b82b141e.json`。
- 当前状态：`RELEASED`（有限域，b82）。统一发布清单（RF 软件/PL/daemon/模型/策略一次核对）`NOT_RUN`；回退版本仅记录到父版本 `b67a8aaa…`。

## 3. 未实现 / 未验证清单（不得当作已完成）

| 项 | 状态 | 归属 |
|---|---|---|
| EVM 口径与实现 | `NOT_IMPLEMENTED` | P3 / Task 4.3 |
| QPSK / QAM16 / QAM64 接收机 | `NOT_IMPLEMENTED` | P3 / Task 4.2 |
| RRC 接收（匹配滤波/群延迟/采样边界） | `NOT_IMPLEMENTED` | P3 / Task 4.2 |
| 统一接收结果契约（`rf_modem_result.py`） | `NOT_IMPLEMENTED` | P3 / Task 4.1 |
| 逐 TX 模式独立参考向量与实物验收 | `NOT_RUN`（BPSK 除外） | P2 / Task 3.1 |
| PL 实时样点处理（同步解调/RRC） | `NOT_IMPLEMENTED` | P2 |
| 档位注册表与纯函数策略 | `NOT_IMPLEMENTED` | P5 |
| 环境可信度校准 | `NOT_IMPLEMENTED`（只有分数/拒绝原因） | P4 |
| 46 dB HEADROOM 真实研究 | `NOT_RUN`（被 `lease_held` 谓词阻塞） | P1 / Task 2.2 |
| 板端 runner 运行包（B1/B3） | `CODED`（B2/N1 已修，B1/B3 未开始） | P1 / Task 2.1 |
| S2 正式 108 物理帧 + 12 离线负控 | `NOT_RUN` | P1 / Task 2.2 |
| 触摸 P95 | `NOT_MEASURED`（用户取消专门计时，非阻塞） | — |
| 统一发布清单与作品级验收 | `NOT_RUN` | P6 |

## 4. 分句 → 实施计划映射

| 分句 | 目标 Chunk | 首个该层门的执行包 |
|---|---|---|
| B1 平台/硬件 | Chunk 1（P0）→ Chunk 2（P1） | Task 1.3 产品入口 + Task 2.1 板端运行包 |
| B2 各 TX 模式 | Chunk 3（P2） | Task 3.1（规格可先离线写作） |
| B3 RX/同步 | Chunk 4（P3） | Task 4.1 → 4.2 |
| B4 星座/频谱/EVM/BER/数据 | Chunk 4（P3） | Task 4.3 |
| B5 PL/PS | Chunk 3（P2）→ Chunk 4 | Task 3.1 第 4 条性能分解 |
| B6 触摸 | Chunk 4/Chunk 6 | Task 4.3 实体屏门、Task 5 人工接管 |
| B7 环境 | Chunk 2（P1）→ Chunk 5（P4） | Task 2.2 → Task 4/P4 置信度 |
| B8 决策 | Chunk 6（P5） | Task 5 只读注册表 → P5a → P5b |
| B9 三层验证 | 贯穿 P1–P6 | 每个模式/类别单独闭环 |
| B10 发布 | Chunk 7（P6） | Task 7 统一发布清单 |

## 5. 本表的使用规则

1. 任何"通过"结论必须指向**原始收据 + 源码摘要**；只有报告没有收据时写 `UNVERIFIED`。
2. 状态只能随新证据**升级**；失败、未知与 `NOT_RUN` 保留，不因后续文档改写而消失。
3. 历史收据绑定的是当时的源码摘要；源码改动后不得继承旧批准（见 runner 增量 `588e84cd…` 不继承旧第三方批准）。
4. 本表与 `docs/reports/2026-10-13-final-prj-baseline.md`（测试基线）、`docs/requirements/rf-status-object-contract.md`（对象契约）、`docs/requirements/acceptance-checklists-p1-p6.md`（各阶段验收清单）配套使用。

TEXT_SOURCE: TRANSCRIBED_PARTIAL（逐句原文待用户提供后复核）
