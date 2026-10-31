# RF 状态 / 证据对象契约（P0 Task 1.3）

日期：2026-10-14。本文是**文档级模式表**，不新增或修改任何代码；它固定新 RF 产品的状态对象与证据对象语义、责任模块和失效规则，作为 P1–P6 的共同接口依据。

## 1. 责任模块（现状实测）

| 模块 | 实测责任 | 不负责 |
|---|---|---|
| `pynq_overlay/sw/rf_status_client.py`（`RfStatusClient`、`RfStatusSnapshot`） | 受限 socket 读取者：状态轮询 + 二进制 RX 路径；只读操作白名单 `READ_ONLY_OPS=('protocol.hello','protocol.close','device.info','state.get','status.subscribe','pl.status.get')`；连接代次与配置修订的计数器；帧身份校验 | 不做后台线程调度、不做分析、不落盘证据 |
| `pynq_overlay/sw/rf_status_worker.py`（`RfStatusWorker`） | 后台三件事：遥测轮询（每次一个不可变 `RfStatusSnapshot`）、控制仲裁（一个活动作业 + 一个幂等 STOP）、有限 `rx.capture`/`loopback.physical.capture` 及其窗口 FFT 分析；通过 `take_rx_result` 交出**已冻结**结果；`submit_capture`/`cancel_capture` 是面板唯一入口 | 不自己声明新鲜度窗口（从 `rf_rx_analysis` 导入 30 s / 2 s）、不重连、不把取消写成"已停止" |
| `pynq_overlay/sw/rf_experiment_record.py`（`write_record`/`read_record`） | 有界本地证据：`MAX_METADATA_BYTES=131072`、`MAX_IQ_BYTES=16384`；身份绑定校验；凭据字段拒绝（`password`/`token`/`credential`）；**最终收据是提交标记，半成品永不可读**；`historical` 必须显式传入 | 不碰硬件、不联网、不重建会话、不续期实时数据 |
| `pynq_overlay/sw/rf_rx_types.py`（`RxIqFrame`/`RxAnalysis`/`RxModelResult`/`RxReadback`） | 不可变帧与结果类型：`payload` 必须是 `bytes`；`samples<=4096`（`RX_MAX_SAMPLES`）；`ratio∈0..4`（`RX_MAX_RATIO`）；`op∈{rx.capture, loopback.physical.capture}` | 不是证据收据；`to_dict()` 只输出元数据，不含 payload |

## 2. 状态 / 证据字段模式表

| 契约字段 | 语义 | 当前实现位置（实测） | 失效 / 拒绝规则 | 差距 |
|---|---|---|---|---|
| `release_id` | 板端当前发布身份（46 项 software map 绑定的候选摘要） | `RfStatusSnapshot.release_id`；帧身份校验（`rf_status_client` 帧重建处要求与快照相等） | 与帧的 `release_id` 不等 → 整帧拒绝 | 只在 BPSK/`cnn-bpsk-*` 合同下验证过；多制式发布清单未统一（P6） |
| `capture_id` | 本地生成的采集标识 `rx-<token>-g<generation>-c<revision>-<n>` | `rf_status_client` 帧重建处，全部检查通过后生成 | **跨新连接会重新编号**：不得单独用作全局唯一键；TRAIN/VAL 数据集去重使用 `(capture_id, release_id, connection_generation, config_revision, completed_at_monotonic)` 完整五元组，与原始 IQ SHA-256 独立去重 | 2026-10-18 冻结跨会话数据集去重键；不声明跨重启的通用 UUID，数据集仍保留各会话来源与 boot_id 证据 |
| 连接代次 `connection_generation` | 每次成功建连自增（`rf_status_client` 计数器） | 帧字段 `RxIqFrame.connection_generation`；研究代次 `_research_generation` 取自连接代次 | 帧代次 ≠ 当前代次 → 拒绝（断线/重连后旧结果立即失效） | 无 |
| 配置修订 `config_revision` | 每次成功下发配置自增 | 帧字段 `RxIqFrame.config_revision` | 帧修订 ≠ 当前修订 → 拒绝（配置变化后旧结果立即失效） | 非 RF 参数（例如 UI 本地显示项）是否计入未定义 |
| 采样域 | 输入/输出采样率与抽取 | `RxIqFrame.sample_rate_hz`、`ratio`、`decimation=1<<ratio`、`output_fs_hz=sample_rate_hz//decimation`；教学域 `TeachingCapability(input_fs_hz=60_000_000, decim=1, rx_len=4096)` | 分析模块只接受声明范围内的采样域，域外写 `INVALID` 具名理由 | 目前只有 60 MSPS/D1 的 BPSK 域被验收 |
| 算法版本 | 分析/接收算法标识 | `rf_bpsk_receiver.VERSION='bpsk-rect-snapshot-v2'`；`rf_environment.VERSION='rf-window-environment-v3'`；`EnvironmentResult.algorithm_version` | 结果必须带版本；跨版本结果不可比 | QPSK/QAM/RRC/EVM 算法版本尚未存在 |
| 模型版本 | 轻量推理权重身份 | `rf_cnn_runtime.validate_model` + `load_model(path, expected_sha256)`；`MAX_MODEL_BYTES=128*1024`；`CLASSES=('TONE','BPSK','QPSK','QAM16','QAM64','OTHER')` | 权重摘要不符 → 拒绝加载 | 模型更新与"环境版本"绑定规则未冻结（P4） |
| 原始 IQ 摘要 | 证据完整性 | `rf_experiment_record._sha` 对 payload 求 SHA-256 并写入记录；`RxIqFrame.payload` 唯一副本、不可变 | payload 长度必须 `samples*4` 字节 | `RxIqFrame.to_dict()` 不含摘要，遥测路径无法只看摘要（P3 视需要补） |
| 当前 / 历史标识 | 该记录是当前帧还是历史回放 | `rf_experiment_record.write_record(..., historical=bool)`，必须显式传 `bool`，调用方在提交前捕获该标志 | 类型不是 `bool` → 拒绝；历史记录不得续期/重建 | 界面"当前 vs 历史"的切换规则由 `ui_model` 持有，需在 P3 与本文对齐 |
| 关闭状态 | 采集结束后的核心释放是否被确认 | worker 结果字段 `close_confirmed`（`None` 表示未判定）；无法确认时写 `close_confirmed=False` + 理由 `关闭未确认`（`CLOSE_UNCONFIRMED`） | **不得**把未确认写成"已停止"；取消是协作式，取消请求在 I/O 锁释放后由关闭-读回优先处理 | 无 |
| 新鲜度 | 结果是否还能当"当前"用 | `rf_rx_analysis.IQ_SNAPSHOT_FRESHNESS_S=30.0`（IQ 快照）、`TELEMETRY_FRESHNESS_S=2.0`（硬件遥测）；`snapshot_is_fresh`/`telemetry_is_fresh` | 超时/失联/配置变化/停止 → 立即失效，界面不得继续显示旧数值 | 跨会话聚合与置信口径未定义（P3 Task 4.3 先冻结） |
| 控制令牌 | 谁有权下发控制 | `rf_status_worker.read_control_token(path)`：**没有内置令牌**，由操作员提供；缺令牌时拒绝 | 无令牌 → 拒绝控制路径 | 一次性授权/ token 的"单次物理研究"约束属 P1 门（Task 2.1） |

## 3. 数据流（一次采集的完整契约链）

```
面板触摸/键盘
  → RfStatusWorker.submit_capture(args)          # 唯一采集入口，检查通过后才建请求
  → RfStatusClient（socket，3 s 采集期限；帧身份：release_id/generation/revision/op/samples/ratio）
  → RxIqFrame（不可变 bytes payload + 前后 RxReadback）
  → rf_rx_analysis.summarize / rf_bpsk_receiver.receive_bpsk / rf_rx_constellation.estimate_constellation
  → rf_environment.analyze_environment（带 IDENTITY_FIELDS 与 baseline sha）
  → RfStatusWorker.take_rx_result（已冻结结果）→ ui_model/ui_view 只读展示
  → rf_experiment_record.write_record（显式 historical 标志；最终收据为提交标记）
```

## 4. 失效规则（必须同时满足才算"当前结果"）

1. 释放后无人布防、无 TX 源、RX 未武装、双服务 active / `NRestarts=0`（板端安全状态）。
2. 帧的 `release_id`、`connection_generation`、`config_revision` 与当前会话一致。
3. IQ 快照在 30 s 内、硬件遥测在 2 s 内（超时即失效）。
4. 关闭被确认（`close_confirmed=True`）；否则只显示"关闭未确认"。
5. 环境/接收结果必须带算法版本与基线摘要；缺基线写 `UNKNOWN/BASELINE_MISSING`，不得沿用旧标签。

## 5. 待冻结项（不得凭想象补全）

| 待冻结 | 归属 |
|---|---|
| `capture_id` 的跨会话唯一性与聚合键 | P3 Task 4.3 |
| 跨独立会话的聚合规则、最小参考符号/比特数、失败帧计入方式、BER 置信区间 | P3 Task 4.3 |
| RMS EVM 的理想点/等化/归一化口径与最小有效符号数 | P3 Task 4.3 |
| 多制式结果身份（模式/采样域/参考可用性）与拒绝原因集合 | P3 Task 4.1（`rf_modem_result.py`） |
| 环境可信度校准口径（未校准前只显示分数与拒绝原因） | P4 |
| 档位注册表字段（RF 参数、发布摘要、实测安全域、接收资格、成对干扰收益/代价） | P5 |
