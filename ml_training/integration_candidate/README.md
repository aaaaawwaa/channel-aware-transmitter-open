# ml_training/integration_candidate — 冻结四类模型的集成适配原型

> 只写 `ml_training`，**未修改产品运行时、未部署、未连接设备**。本目录是离线可运行原型 +
> 测试 + 产品侧最小接入差分说明；不是板卡资格或产品端到端验收。冻结交接见
> **[DELIVERY.md](DELIVERY.md)**。

## 1. 四类输出与现有六类合同的关系（明确、不伪造）

| 项 | 现有板端合同 | 本原型 |
|---|---|---|
| 类表 | `TONE, BPSK, QPSK, QAM16, QAM64, OTHER` | `BPSK, QPSK, QAM16, QAM64` + 拒识 `UNKNOWN` |
| head | `[6,16]`（`pynq_overlay/sw/rf_cnn_runtime.py`） | `[4,16]`（冻结 round-2 方案） |
| 概率输出 | 6 类 softmax | **4 类 softmax/logit；不生成 TONE/OTHER 概率，也不合成 6 类分布** |
| 环境标签 | 独立环境规则 | 不使用；`environment` 结果原样透传 |
| 解调结果 | 独立 BPSK 解调 | 不读取/不解释，`demod` 原样透传 |
| 已知类映射 | — | `BPSK→BPSK, QPSK→QPSK, QAM16→QAM16, QAM64→QAM64`；`TONE/OTHER` 留在环境/六类侧 |
| 合同扩展 | — | `extension_required: true`（需受控合同扩展，本轮未改产品） |

输出三块相互独立：

```json
{
  "status": "OK",
  "input_domain": {"status": "VALID", "reason": "", "observed": {...},
                   "required": {"format": "s16_iq_le", "samples": 4096, "...": "..."}},
  "modulation": {"final_label": "BPSK",
                 "final_label_source": "baseline_arbitration",
                 "accepted": true, "reason": "",
                 "cnn_raw_top1": "QPSK",
                 "cnn_raw_scores4": [...],
                 "cnn_logits4": [...],
                 "arbitration_source": "baseline_feature_model",
                 "arbitration_applied": true,
                 "scores4": [...], "spectral": {...}},
  "contract": {"board_contract": "cnn-bpsk-environment-v1",
               "board_head_probabilities": 6,
               "modulation_head_probabilities": 4,
               "six_class_probabilities": null,
               "known_class_subset_mapping": {...}, "extension_required": true},
  "passthrough": {"environment": {...原样...}, "demod": {...原样...},
                  "six_class": {...原样...}, "note": "not read / not modified"}
}
```

- **最终标签** `modulation.final_label` 只可能是四类之一或 `UNKNOWN`，**不会是 TONE/OTHER**；
- **仲裁来源** `modulation.final_label_source` ∈ `cnn_top1` / `baseline_arbitration` /
  `gate_rejection` / `unavailable`；`modulation.arbitration_source` ∈ `none` /
  `baseline_feature_model`；
- **原始 CNN 分数** `cnn_raw_scores4` / `cnn_raw_top1` 是 round-2 仲裁与拒识标签之前的
  round-1 CNN softmax，与最终标签明确分开；
- `environment`/`demod`/`six_class` 只 deep-copy 透传，不参与调制决策、不被覆盖。

## 2. 输入域校验（先校验；缺失/不支持不跑模型）

上下文必须由调用方从**采集/配置元数据**提供；**不能**从 IQ 或模型预测反推。

| 项 | 当前支持值 |
|---|---|
| 格式 | `s16_iq_le`（little-endian signed int16 交织 I/Q） |
| 帧 | 4096 复样本 = 8192 int16 = 16,384 B |
| 采样域 | `sample_rate_hz = 60,000,000`，`decimation = 1`（输入复采样率 60 MSPS） |
| 符号域 | `symbol_rate_hz = 3,000,000`，`shaping = "RECT"` |

不可用原因码（`input_domain.reason` / `modulation.reason`）：

- `missing_input_context`：缺少上下文或缺字段（`missing_fields` 列出）；
- `unsupported_format`、`unsupported_length`、`unsupported_sampling_domain`、
  `unsupported_symbol_domain`：与上表不符；
- `invalid_iq_format`（形状/dtype/大端/越界）、`invalid_iq_length`（字节长度不是 16,384）。

缺失或不支持时返回 `status="UNAVAILABLE"`、`final_label="UNKNOWN"`，**不运行模型**，
`cnn_raw_scores4`/`cnn_logits4`/仲裁全部为空，原因明确；合法 43 帧在声明的包上下文下仍与
冻结回放管线一致（见 §4）。

直接反例（14 个）见 `negative_cases.py` 与 `results/negative_cases.json`，覆盖：缺失/部分缺失
上下文、错误 format、错误长度、错误采样率、decimation=2、错误符号率、RRC、IQ 形状/dtype/范围/
大端、raw bytes 长度/类型错误；**14/14 均返回预期不可用原因，且模型未被调用**；合法上下文
阳性对照为 `OK/BPSK`。

## 3. 文件与运行

| 文件 | 作用 |
|---|---|
| `input_domain.py` | 输入域常量/校验/raw 帧解码（独立于模型） |
| `modrec4_adapter.py` | `ModRec4Adapter`：先校验后推理 + 合同映射 + 透传 + 明确字段 |
| `run_adapter.py` | CLI：单帧/全部帧/负例/一致性检查 |
| `negative_cases.py` | 14 个直接反例及结果生成 |
| `consistency_check.py` | 与冻结回放包逐帧一致性、透传校验、ARM 证据同步 |
| `tests/test_integration_adapter.py` | 相关测试 |
| `requirements.txt` | 仅 `numpy>=1.17.4`；目标 Python 3.8.2 |
| `PRODUCT_INTEGRATION_DIFF.md` | 产品侧最小接入差分（未应用） |
| `evidence/arm_replay_evidence.json` | ARM 已执行事实同步（只读引用） |
| `results/*` | 一致性、负例、示例输出与 Lead 一次核对记录 |
| `DELIVERY.md` | 必要文件列表、摘要、入口、测试结果、冻结交接 |

运行（从项目根目录，使用现有 venv 或板端 `python3`）：

```bat
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\consistency_check.py
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\negative_cases.py
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\run_adapter.py --frame ml_training\arm_probe\frames\clean_real_bpsk\clean-real-new-normal.iq --out result.json
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\run_adapter.py --all
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\run_adapter.py --check
ml_training\.venv\Scripts\python.exe -m pytest ml_training\integration_candidate\tests -q
```

CLI 未传 `--context` 时使用声明的包上下文（60 MSPS/decim1/3MSym/s/RECT），并在输出
`input_context_source="cli-default-declared-contract"` 标明；`--no-context` 演示不可用路径。
产品调用必须传入采集/配置元数据。

## 4. 本次结果（主机，PASS）

`results/consistency.json`：

- **原型 vs 冻结回放包：43/43 帧一致，0 mismatch**；`input_domain_valid_frames=43`；
  `max_logit_delta=0.0`，`max_spectral_delta=0.0`；
- 透传：environment/demod/six-class 与输入深比较相等，输入未被修改；
- `contract.six_class_probabilities == null`，未伪造六类概率；
- **负例 14/14 PASS**；
- 板端已执行回放证据与主机期望逐帧比对：43 帧 0 mismatch。

测试：集成适配 **15 passed**；合并回归（含本目录）**143 passed**（首轮 99 + 第二轮 29 +
集成 15）。

## 5. 同步的 ARM 已执行事实（只引用，不重复回放）

来源：`evidence/rf-teaching/s2-46db-arm-replay-2026-10-21/result/`（`arm-replay-result.json`、
`ARM-REPLAY-RESULT.md`、`latency-rss.json`、`host-arm-compare.json`；哈希见
`evidence/arm_replay_evidence.json`）：

| 项 | 值 |
|---|---|
| run_id / 状态 | `arm-replay-20261021-180621-6bcea2` / PASS |
| 帧 / 标签 | 43 帧；`expected_label_checks.mismatch_count = 0` |
| 板端环境 | CPython **3.8.2** / NumPy **1.17.4** / armv7l |
| 计时（纯 Python/NumPy 探针） | 860 samples；median **56.86 ms**，**P95 63.24 ms**，max 134.53 ms |
| RSS | VmHWM **23,144 KiB = 22.60 MiB** |
| 清理/完整性 | `/tmp` 已清理；release 未变；包 MANIFEST 68/68 文件校验一致 |
| 范围 | **纯 NumPy 回放探针；不是产品 C 运行时/六类合同/端到端资格** |

## 6. 依赖与边界

- 依赖：仅 NumPy；原型复用冻结回放包 `ml_training/arm_probe/runtime`（只读）；产品接入时按
  [PRODUCT_INTEGRATION_DIFF.md](PRODUCT_INTEGRATION_DIFF.md) vendored 到 release。
- 未改产品、未部署、未连设备；产品侧 hook 仅为提案代码片段。
- 日常一次增量核对见 [results/LEAD-CHECK.md](results/LEAD-CHECK.md)；冻结交接见
  [DELIVERY.md](DELIVERY.md)。
