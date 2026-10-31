# 产品侧最小接入差分说明（未应用）

> 本文件描述**实际需要改哪些文件/字段**，供主项目会话后续在有授权时实施。本任务**没有**
> 修改任何产品文件、没有部署、没有连接板卡；这里的代码片段是提案，不是已应用 patch。

## 0. 结论先行

- 冻结四类模型保持独立产物；**不塞进**现有六类模型合同（`rf_cnn_runtime.CLASSES` /
  `WEIGHT_SHAPES` 不动），不生成六类概率。
- 产品侧最小接入 = ①新增一个 vendored 适配模块 + 4 类模型文件；②在
  `rf_status_worker.py` 的帧结果组装处新增一个**附加字段**；③不覆盖
  `model_result` / `demod_result` / `environment_result`。
- 无 UI/协议破坏性改动；新增字段是否展示/转发由后续受控扩展决定。本轮不实施。

## 1. 需要新增的文件（release 软件 map 内）

| 新文件 | 内容 | 来源 |
|---|---|---|
| `pynq_overlay/sw/rf_modrec4_adapter.py` | 适配器 + 合同映射（去掉对 `ml_training` 的路径依赖，vendored） | `ml_training/integration_candidate/modrec4_adapter.py` |
| `pynq_overlay/sw/rf_modrec4_runtime.py`（或包内 runtime 目录） | 纯 NumPy 冻结 runtime | `ml_training/arm_probe/runtime/*.py`（vendored；保留相对 import 或合并为单文件） |
| `model/modrec4/cnn_model.json` | 冻结 4 类 CNN | `ml_training/arm_probe/model/cnn_model.json`（`25a65fb1…`） |
| `model/modrec4/baseline_model.json` | 冻结基线 | `ml_training/arm_probe/model/baseline_model.json`（`caa82589…`） |
| `model/modrec4/rule_config.json` | 冻结规则/阈值 | `ml_training/arm_probe/model/rule_config.json`（`9aceebbd…`） |

发布打包：在 `pynq_overlay/sw/merged_release.py` 的 `SOFTWARE_SOURCES`/`MODEL` 映射中添加
新文件/新 artifact key（例如 `modrec4_model`，包含三个 JSON 与各自 sha256）。**不要**替换
现有 `MODEL_KEY` / 六类模型 artifact。若暂不扩合同，可把该 artifact 标记为
`status: experimental` 且仅用于展示/研究，不作为生产建议。

## 2. 产品侧最小 hook（`pynq_overlay/sw/rf_status_worker.py`）

### 2.1 加载（与现有六类模型加载并列，不改动原加载）

现有加载在 `_ensure_model()`（约 L585–L621）：通过 `merged_release.verify_release` 取
`manifest[MODEL_KEY]`，`importlib.import_module("rf_cnn_runtime")` 后 `load_model(...)`。
新增一个独立的 `_ensure_modrec4()`（或同一函数内追加）：

```python
# 新增：独立加载冻结四类适配器；失败只置 None，不影响六类/环境/解调路径
self._modrec4 = None
try:
    import rf_modrec4_adapter                      # release map 内新模块
    artifact = manifest.get("modrec4_model")       # 新 artifact key，非 MODEL_KEY
    if artifact:
        artifacts = manifest["artifacts"] if "artifacts" in manifest else manifest
        self._modrec4 = rf_modrec4_adapter.ModRec4Adapter.from_release(
            directory, artifact)                   # 见 §3 vendored 入口约定
except Exception as exc:                           # 可选能力，失败不阻塞 RX
    self._modrec4 = None
    self._modrec4_reason = str(exc) or type(exc).__name__
```

### 2.2 每帧推理（`_rx_handoff` 组装处，约 L948–L1032）

现有顺序：`analysis = summarize(frame)` → `model_result = self._predict_frame(frame)` →
`demod_result = ...` → `environment_result = ...` → `_rx_handoff = {...}`。

在 `environment_result` 之后、组装 `_rx_handoff` 之前新增：

```python
modrec4_result = None
if (failure is None and not cancelled and self._modrec4 is not None
        and frame is not None):
    try:
        import numpy as np
        iq = np.frombuffer(frame.payload, dtype="<i2").reshape(frame.samples, 2)
        # 输入域上下文必须来自采集/配置元数据，不能从 IQ/模型预测反推
        requested = request.get("demod_config") or {}
        context = {"format": "s16_iq_le", "samples": frame.samples,
                   "sample_rate_hz": frame.sample_rate_hz,
                   "decimation": frame.decimation,
                   "symbol_rate_hz": requested.get("symbol_rate_hz"),
                   "shaping": requested.get("shaping")}
        modrec4_result = self._modrec4.evaluate_iq(
            iq, context=context,
            environment=environment_result,   # 原样透传，不读取
            demod=demod_result,               # 原样透传，不读取
            six_class=model_result,           # 原样透传，不读取
            frame_id=frame.capture_id)
        # 若 context 缺失/不支持，返回 status="UNAVAILABLE" +
        # input_domain.reason；产品层不要根据六类/环境结果“猜”输入条件，
        # 也不要为缺失字段补默认值来强行推理。
    except Exception as exc:
        modrec4_result = {"schema": "ml-modrec-integration-candidate-v1",
                          "status": "UNAVAILABLE",
                          "input_domain": {"status": "UNAVAILABLE",
                                           "reason": "ADAPTER_FAILED"},
                          "modulation": {"final_label": "UNKNOWN",
                                         "final_label_source": "unavailable",
                                         "accepted": False,
                                         "reason": "ADAPTER_FAILED: %s"
                                                   % (str(exc) or type(exc).__name__),
                                         "cnn_raw_scores4": None,
                                         "cnn_logits4": None,
                                         "arbitration_source": "none"},
                          "contract": {"six_class_probabilities": None,
                                       "extension_required": True}}
```

组装处**只新增一个 key**，不动原字段：

```python
self._rx_handoff = {"frame": frame.to_dict(), "analysis": analysis,
                    "model_result": model_result, "demod_result": demod_result,
                    "raw_frame": frame if self._teaching_info['available'] else None,
                    "constellation": constellation,
                    "environment_result": environment_result,
                    "modrec4_result": modrec4_result}   # ← 仅新增
```

### 2.3 状态/payload 转发（可选、非破坏性）

如果需要在 status/payload 中看到该结果，只在 `pynq_status.py`/`rf_status_client.py` 的
handoff→payload 组装处**新增**同名 key（例如 `"modrec4_result"`），不改变
`model_result`/`environment_result`/`demod_result` 字段。现有 UI/客户端不认识该 key 时应
原样忽略；本轮不改协议版本，后续若要展示/记录再做受控合同扩展。

## 3. vendored 适配器入口约定（产品实现时）

`rf_modrec4_adapter.py` 由 `modrec4_adapter.py` 派生，需改两点：

1. 删除 `ml_training/arm_probe` 的 `sys.path` 依赖，将 `runtime/*.py` 作为同目录模块
   （或单文件）导入；
2. 增加 `from_release(directory, artifact)`：用 artifact 中三个 JSON 路径 + 各自 sha256
   构造 `Round2Predictor`，并在构造时校验哈希（与 prototype 相同）。

`ModRec4Adapter` 的对外约定：

- `evaluate_iq(iq, context=..., environment=None, demod=None, six_class=None)` → 三块结果；
  `context` 必填（缺失返回 `UNAVAILABLE/missing_input_context`，不运行模型）；
- `evaluate_bytes(raw_bytes, context=...)` 用于产品 `frame.payload`（16,384 B 明确 `s16_iq_le`）；
- `to_product_fields(result)` → `{"modrec4": {...}}`（可选的扁平 payload 字段），包含
  `status`、`final_label`、`final_label_source`、`cnn_raw_top1`、`cnn_raw_scores4`、
  `arbitration_source`、`input_domain_status/reason`，且 `six_class_probabilities=None`；
- 不返回/不伪造六类概率；`contract.six_class_probabilities = None`；
- `passthrough` 深拷贝原对象，不读取、不修改环境/解调/六类结果；
- 输入域仅支持 `s16_iq_le` + 4096 复样本 + 60 MSPS/decim=1 + 3 MSym/s/RECT；其余返回明确不可用
  原因，**不根据模型预测反推输入条件**。

## 4. 明确不改的东西

- `pynq_overlay/sw/rf_cnn_runtime.py`：`CLASSES`、`WEIGHT_SHAPES`、六类模型加载与门限**不改**；
- `pynq_overlay/sw/rf_environment.py`：环境规则/标签**不改**；
- `pynq_overlay/sw/rf_bpsk_pipeline.py` / `rf_bpsk_receiver.py`：解调结果**不改**；
- 不改 release 身份/现有 `MODEL_KEY`；不部署、不重装、不加载 Overlay。

## 5. 后续最小验证顺序（不在本任务执行）

1. 主机：本 prototype 43/43 一致性已 PASS；产品侧 vendored 版做同样 43 帧回放，要求标签/原因/
   仲裁一致、logit ≤1e-3、score/频谱 ≤1e-6；
2. ARM：沿用已执行的纯 NumPy 探针证据（43/43、P95 63.24 ms、峰值 22.6 MiB）只作**runtime 参考**，
   产品 C/协议路径需另立验收；
3. 产品集成验收（新候选、安装、读回、回滚）由主项目会话按现有发布门禁执行，本任务不涉及。

## 6. 与现有六类合同的映射（再次强调）

| 四类输出 | 六类合同侧处理 |
|---|---|
| BPSK/QPSK/QAM16/QAM64 | 同名已知类子集；产品可将 `modrec4_result.modulation` 作为**附加**信息 |
| UNKNOWN（拒识） | 不映射为 TONE/OTHER；保留拒识原因 |
| — | TONE/OTHER 概率**不由本适配器生成**；环境/六类结果保持原样 |

因此四类模型不能直接加载进现行六类 `rf_cnn_runtime`（head `[6,16]`、类表校验不同）；本差分
采用“第二模型 + 附加字段”的独立集成方式，避免伪造六类概率。
