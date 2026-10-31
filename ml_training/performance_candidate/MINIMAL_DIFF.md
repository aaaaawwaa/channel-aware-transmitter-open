# 最小差分说明（一个优化方案）

> 本任务只新增 `ml_training/performance_candidate/`；候选 `099174be`、产品文件、冻结模型、
> integration_candidate、arm_probe、evidence 均未修改。

## 差分范围

只改变 `rf_modrec4_adapter.ModRec4Adapter.evaluate_iq` 的内部计算复用；不改变：

- 权重、阈值、规则：仍用候选 `modrec4_*.json`（三件套 SHA-256 与候选一致）；
- 输入域：仍用候选 `rf_modrec4_input_domain` 校验（格式/长度/采样域/符号域）；
- 最终标签、接受/拒识、拒识原因、仲裁来源、帧 identity、contract、passthrough、provenance；
- `pure_class_hint` 仍保留并等价（不是产品最终标签，未删除）。

## 优化前的重复计算

候选 `evaluate_iq` 依次调用：

1. `_before_decision`：`preprocess_iq` + CNN forward + softmax + round-1 gate；
2. `decide`：再次 `preprocess_iq` + CNN forward + softmax + spectral FFt +（条件）baseline；
3. `classify`：再次 `preprocess_iq` + CNN forward + softmax + baseline + arbitration；
4. 末尾再次 `preprocess_iq` + `spectral_features`（仅用于输出 `modulation.spectral`）。

主机动态计数（每帧，43 帧一致）：`preprocess_iq=4`、`cnn.forward=3`、`baseline.scores=2`、
`spectral_features=2`。`classify` 的唯一输出消费者是 `pure_class_hint`。

## 优化后的单次共享计算

```
x, gate = preprocess_iq(values)                 # once
if gate.ok:
    logits = cnn.forward(x)                     # once
    probs = softmax(logits)                     # once
    before = gate decision from probs           # same as _before_decision
    baseline_probs = baseline(x)                # once (required for classify/pure hint)
    chosen, applied, detail = arbitrate(probs)  # once
    spectral = spectral_features(x)             # once
    after = decide-equivalent using probs/spectral (threshold order unchanged)
else:
    before/after = exact UNKNOWN rejection
pure_class_hint = classify-equivalent chosen class  # same value as before
```

阈值顺序、`apply_gate`、`top_scores`、spectral 规则、baseline 特征与 `_arbitrate` 均调用
候选原始实现；没有重新实现数学逻辑。

## 等价性与主机耗时

- 70 个 case（4 候选帧 + 43 arm_probe clean/rejection/stress + 4 合成合法 + 2 合成错误 +
  17 输入域/阈值边界）**0 mismatch**；所有数值字段最大差 **0.0**。
- full-path 4/4 帧 0 mismatch（六类、环境、解调、modrec4、身份全部一致）。
- 主机（仅线索）：modrec4 阶段 median **13.38 → 6.27 ms（−53.1%）**；
  full-path total median **38.38 → 30.44 ms（−20.7%）**；adapter median
  **9.46 → 4.68 ms（−50.5%）**。主机 run-to-run 有波动，以 `results/summary.json` 为准。
- **未在 ARM 验证，不宣称 ARM 已加速**；下一轮命令见 `reports/performance_report.md` §6。

## 文件

| 文件 | 角色 |
|---|---|
| `optimized_adapter.py` | 优化原型（覆盖 evaluate_iq；继承候选 adapter 的 contract/unavailable/passthrough 逻辑） |
| `optimized_fullpath.py` | 同阶段/同输入/同计时边界的全路径包装（只替换 modrec4 adapter） |
| `reference/` | 候选 vendored 14 文件只读快照（11 py + 3 json），逐 SHA 相同 |
| `measure.py` / `measure_adapter.py` / `measure_groups.py` | 主机前后计时 |
| `eval_cases.py` / `compare_cases.py` / `compare_fullpath.py` | 行为与数值一致性 |
| `profile_breakdown.py` | 只读调用次数/热点证据 |
| `run_all.py` | 一键复现全部主机测量与报告 |

产品下一轮集成时：把候选 `rf_modrec4_adapter.py` 的 `evaluate_iq` 替换为本文件同名逻辑，
候选 runtime/权重/阈值/contract 不变；`reference/` 仅用于本原型隔离，不应进入产品发布。
