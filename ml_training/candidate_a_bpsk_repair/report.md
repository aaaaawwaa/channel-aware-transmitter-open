# 候选 A 的 BPSK 定向修复实验 — 负结果

状态：**NEGATIVE_NO_ELIGIBLE_CHECKPOINT**。
只训练了一个候选（R1），使用既有 TRAIN、只用 VAL 选 checkpoint；未使用 test/task80/新采集，
未改 A、阈值、仲裁、门控或预处理。没有合格 checkpoint，因此**没有导出新模型、没有部署、没有重训**。

## 0 结论

- A 的 VAL BPSK recall **0.8733**，original **0.9500**；A 的 BPSK 误接受率 **11.67%**，
  original **3.00%**。这是要修的边界。
- R1 用“A 权重初始化 + BPSK/QPSK 过采样 + 低 LR 微调”尝试修复；VAL BPSK raw recall
  可从 0.7133 升到 **0.8433**，但 **QPSK raw recall 从 0.95 掉到 0.7867**，
  QAM16 也降到 0.83；所有 epoch 都没有达到预设 guard。
- 最好 QPSK 的 epoch 也只有 **0.9267**（低于 guard 0.94）；**没有任何合格 checkpoint**。
- 按预注册规则停止：不导出 R1、不改阈值、不换 seed、不做第二次候选。
- 交付负结果；A 仍按主线“有限范围开发版试部署”使用，不因本实验升级。

## 1 训练配置与改动理由

| 项 | 值 |
|---|---|
| 实验目录 | `ml_training/candidate_a_bpsk_repair/` |
| 候选 | R1（唯一候选） |
| 初始化 | A `model.pt`（SHA `2521f445…`） |
| 架构/预处理 | `tiny-iq-cnn-v1` / `dc-complex-rms-v1`（不变） |
| 类别顺序 | `BPSK, QPSK, QAM16, QAM64` |
| TRAIN | sim-v1 train 4800 + A 既有 train-only conjugate 副本 = 9600 |
| VAL | sim-v1 val 1200，仅用于 checkpoint 选择 |
| seed | **20261023** |
| optimizer | AdamW，lr **1e-4**，weight_decay **1e-4** |
| batch / epochs / patience | 64 / max 20 / 6 |
| sampler 权重 | BPSK 2.0、QPSK 2.0、QAM16 1.0、QAM64 1.0 |
| loss | CrossEntropyLoss |
| 阈值/仲裁/门控 | 完全冻结，未改 |
| test/task80/新采集 | **未用于训练或选择** |

改动理由：诊断显示 A 新增 BPSK 错误主要是 **CNN top1 从 BPSK 偏向 QPSK**，
A 的 raw BPSK recall 只有 0.7133；因此只做**训练侧**的 BPSK/QPSK 边界加权微调，
用低 LR 保留 A 的 QAM 能力，并用 VAL guard 防止 QPSK/QAM 退化。

预注册的 VAL 选择规则：

1. guard：QPSK raw recall ≥ A−0.01（0.94）；QAM16 ≥ A−0.02（0.84）；
   QAM64 ≥ A−0.02（0.8433）；
2. 满足 guard 的 epoch 中，最大化 `BPSK recall + 0.5 × QPSK recall`；
3. 不满足 guard 的 epoch 不保存为候选。

## 2 R1 训练结果：无合格 checkpoint

A raw VAL recall：`BPSK 0.7133 / QPSK 0.9500 / QAM16 0.8600 / QAM64 0.8633`。

| epoch | BPSK | QPSK | QAM16 | QAM64 | BPSK+0.5QPSK | guard |
|---:|---:|---:|---:|---:|---:|---|
| 3 | 0.7833 | 0.8400 | 0.8533 | 0.8733 | 1.2033 | fail（QPSK） |
| **4** | **0.8433** | 0.7867 | 0.8300 | 0.9100 | **1.2367** | fail（QPSK/QAM16） |
| 5 | 0.7367 | **0.9267** | 0.8600 | 0.8600 | 1.2000 | fail（QPSK） |
| 17 | 0.7533 | 0.8900 | 0.8633 | 0.8633 | 1.1983 | fail（QPSK） |
| 20 | 0.7567 | 0.8867 | 0.8533 | 0.8700 | 1.2000 | fail（QPSK） |

- **无任何 epoch 的 QPSK 达到 0.94**（最高 0.9267）。
- BPSK 最高 0.8433，但同一 epoch QPSK 只有 0.7867、QAM16 0.83，不可接受。
- `training_result.json`：status `NEGATIVE_NO_ELIGIBLE_CHECKPOINT`，训练 58.69 s，20 epochs。
- 无 `model.json` / `rules.json` / `model.pt` 导出；只保留曲线、VAL 选择和负结果。

## 3 original / A 的 VAL 比较（R1 无合格模型）

`VAL` 1200 帧，每类 300。recall 对“正确类”；rejection 是 UNKNOWN；false_accept
是“接受但判成其他类”的比例。

| 模型 | 类别 | recall | rejection | false_accept |
|---|---|---:|---:|---:|
| original | BPSK | **0.9500** | 0.0200 | 0.0300 |
| original | QPSK | 0.9367 | 0.0267 | 0.0367 |
| original | QAM16 | 0.7467 | 0.0433 | 0.2100 |
| original | QAM64 | 0.7933 | 0.0300 | 0.1767 |
| A | BPSK | **0.8733** | 0.0100 | **0.1167** |
| A | QPSK | 0.9800 | 0.0100 | 0.0100 |
| A | QAM16 | 0.8500 | 0.0133 | 0.1367 |
| A | QAM64 | 0.8567 | 0.0100 | 0.1333 |

A 的 BPSK 相对 original：recall **−7.67 pp**，误接受 **+8.67 pp**；QPSK/QAM16/QAM64
更高，拒识更低，这是 A 的既有取舍，不是本轮修复成功。

**R1 没有合格模型，不能给出新候选的 VAL recall/rejection/false-accept**；不把
未通过 guard 的 epoch 冒充候选。

## 4 既有 test 仅作已知回归比较

`sim-v1/test` 2400 帧，**未用于训练或 checkpoint 选择**，也不是新独立验收。
仅列 BPSK：

| 模型 | BPSK recall | rejection | false_accept |
|---|---:|---:|---:|
| original | 0.9300 | 0.0283 | 0.0417 |
| A | 0.8800 | 0.0317 | 0.0883 |

完整四类在 `results/comparison_original_A.json`；该 test 只作为已知回归背景。

## 5 OFF 误接受 / 质量门

`gate_test` OFF 100 帧（既有独立 gate split，不是 test，也未用于训练）：

| 模型 | OFF false_accept |
|---|---:|
| original | 0 / 100 = 0.0000 |
| A | 0 / 100 = 0.0000 |

阈值、门控、仲裁完全未变；R1 没有模型，无法产生候选侧变化。

## 6 训练侧结论与停止条件

- 本轮的一次有界修复尝试**没有产生可交付候选**；原因是 BPSK/QPSK 边界改善与 QPSK
  保持存在直接冲突，而不是阈值问题。
- 不进行第二次训练、不更换 seed、不扫参、不降 guard、不改仲裁/门控；
  不采用“只看 BPSK”的单一指标强行放行。
- 若继续 BPSK 修复，应由主线另立任务，采用不同机制（例如低 SNR/高 CFO 的目标增广或
  结构/特征改动），并重新获得实验授权；本轮到此停止。
- A 保持现有状态：主线“有限范围开发版试部署”，本实验不改变 A，也不构成升级/验收。

## 7 冻结与边界

- A `model.json` SHA `b840f9dbaebc5db855ef1535d091e1f1583e12368ca62742f517a8d38123a28e`
  未变；A `rules.json` SHA `10a0bef4d237818eaa39dcd4e3048466de60017dbf0545c02bcbf405da2cdf64`
  未变。
- 六帧回放包 `manifest.json` SHA
  `cb07a8f3d9446f75a65c037f34287073a8a78c8357c59e6ae9618d402f4137eb` 未变。
- 未覆盖既有结果；未训练新候选第二轮；未连接板卡；未部署；未新增重复审查。
- 既有 test 仅作已知回归比较；task80/新采集未使用。

## 8 证据

- [experiment_config.json](experiment_config.json)
- [train_repair.py](train_repair.py)
- [results/candidate_R1/training_result.json](results/candidate_R1/training_result.json)
- [results/candidate_R1/val_selection.json](results/candidate_R1/val_selection.json)
- [results/candidate_R1/curves.json](results/candidate_R1/curves.json)
- [results/comparison_original_A.json](results/comparison_original_A.json)
- [evaluate_comparison.py](evaluate_comparison.py)
