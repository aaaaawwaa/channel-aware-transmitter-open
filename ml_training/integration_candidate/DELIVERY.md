# 交付摘要与文件列表（冻结交接）

> 状态：**冻结后交主项目会话接入**。本目录只写 `ml_training`；未改产品运行时、未部署、
> 未连接设备。**不另建重复封存体系**：冻结模型/规则的哈希以既有
> `ml_training/arm_probe/MANIFEST.json` 为准，本文件只做交付索引与结果摘要。

## 1. 必要文件列表

| 文件（相对 `ml_training/integration_candidate/`） | 作用 | 必须交付 |
|---|---|---|
| `input_domain.py` | 输入域常量/校验/raw 帧解码，独立于模型 | 是 |
| `modrec4_adapter.py` | `ModRec4Adapter`（先校验后推理、合同映射、透传、明确字段） | 是 |
| `run_adapter.py` | CLI：单帧/全部帧/负例/一致性检查 | 是 |
| `negative_cases.py` | 14 个格式/长度/采样域/符号域直接反例 | 是 |
| `consistency_check.py` | 与冻结回放包一致性、透传、ARM 证据同步 | 是 |
| `tests/test_integration_adapter.py` | 测试（合同/域校验/负例/一致性/透传/CLI） | 是 |
| `requirements.txt` | 依赖（仅 NumPy；目标 Python 3.8.2） | 是 |
| `PRODUCT_INTEGRATION_DIFF.md` | 产品侧最小接入差分（未应用） | 是 |
| `README.md` | 使用说明与结果 | 是 |
| `evidence/arm_replay_evidence.json` | 已执行 ARM 回放事实同步 | 是 |
| `results/consistency.json` | 43/43 一致性 + 透传 + ARM 证据结果 | 是 |
| `results/negative_cases.json` | 14/14 输入域反例结果 | 是 |
| `results/example_output.json` | 带 environment/demod 透传的示例输出 | 是 |
| `results/LEAD-CHECK.md` | Lead 一次增量核对记录 | 是 |
| `DELIVERY.md` | 本文件 | 是 |

**只读依赖（不复制、不改动）**：

- `ml_training/arm_probe/{model,runtime,expected,input_manifest.json,MANIFEST.json}`：
  冻结 CNN/基线/规则、纯 NumPy runtime、43 帧输入清单与主机期望、包清单；
- `evidence/rf-teaching/s2-46db-arm-replay-2026-10-21/result/`：ARM 已执行事实（只读引用）。

## 2. 运行入口

```bat
rem 一致性 + 负例 + ARM 证据同步（生成 results/*.json）
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\consistency_check.py

rem 仅负例
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\negative_cases.py

rem 单帧 / 全部 43 帧 / 一致性 / 无上下文反例
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\run_adapter.py --frame <frames/...iq> --context <input_context.json> --out result.json
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\run_adapter.py --all
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\run_adapter.py --check
ml_training\.venv\Scripts\python.exe ml_training\integration_candidate\run_adapter.py --frame <frames/...iq> --no-context

rem 测试
ml_training\.venv\Scripts\python.exe -m pytest ml_training\integration_candidate\tests -q
```

## 3. 结果摘要

| 检查 | 结果 |
|---|---|
| 原型 vs 冻结回放包（43 帧同输入） | **43/43 一致，0 mismatch**；`input_domain_valid_frames=43`；`max_logit_delta=0.0`，`max_spectral_delta=0.0` |
| 输入域直接反例 | **14/14 PASS**；模型未被调用；合法上下文阳性对照 OK/BPSK |
| 透传 | environment / demod / six-class 未修改、未读取；PASS |
| 四类 vs 六类 | `six_class_probabilities=null`；无 TONE/OTHER 输出；映射仅四类已知类 |
| 最终标签 vs 原始 CNN vs 仲裁 | `final_label` / `final_label_source` / `cnn_raw_top1` / `cnn_raw_scores4` / `arbitration_source` 明确分开 |
| ARM 已执行事实（只读引用） | 43 帧 0 mismatch；P95 **63.24 ms**；峰值 **22.6 MiB**；Python 3.8.2/NumPy 1.17.4/armv7l |
| 测试 | 集成 **15 passed**；合并回归 **143 passed**（99 + 29 + 15） |
| 产品/设备 | 零产品修改、零部署、零设备连接 |

## 4. 冻结与接入

- 本文件列表中的 integration_candidate 文件在交付后冻结；冻结模型/规则/回放包哈希沿用
  `ml_training/arm_probe/MANIFEST.json`，不新建第二套封存清单。
- 主项目会话接入时按 `PRODUCT_INTEGRATION_DIFF.md`：新增 vendored 适配模块 + 4 类模型文件，
  在 `rf_status_worker.py` 的帧结果组装处只新增 `modrec4_result` 字段；不改六类合同、环境、
  解调与现有运行时。
- 产品侧 hook 代码片段仅为提案，本轮未应用；接入验收由主项目会话按其发布门禁另行执行。
