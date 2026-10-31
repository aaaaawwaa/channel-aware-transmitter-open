# 首轮 40-epoch trial 的接续检查与续训（2026-10-21）

> 范围：只读检查 + 离线续训；不连设备、不部署、不改产品运行时；未重读首轮独立测试集；
> 真实数据未参与；首轮/第二轮既有产物保持只读（关键文件 SHA-256 由测试固定校验）。

## 1. 检查结论（先说结果）

- 当前 `ml_training/` 里的**唯一"被预算截断"的训练**是首轮的 **40-epoch val-only trial**：
  `artifacts/trials/formal-40ep/`。它**不是崩溃/中断**：`epochs_run=40`、`best_epoch=39`、
  `val macro-F1=0.7005`，正好停在初始 40 epoch 预算上限且仍在变好；随后首轮预算改为 80 epoch，
  另行从零训练出正式交付模型（val macro-F1 0.8545，best epoch 76）。
- 该 trial 的检查点只存权重（`model_state_dict`，无优化器/loader 状态）；其配置/数据绑定
  （config `f1a677ce…`、manifest `ce66f476…`）早于首轮最终重冻结（当前 `c34ba443…` / `9f70a0c8…`）。
  首轮报告 §4 已记录重冻结后 20 个数据 npz **逐字节不变**，因此可安全用当前冻结数据续训，
  但只能做**温启动**（warm start），不是逐位一致的断点续训；本报告与产物元数据都明确记录该限制。
- 另外只读检查了旧工作区 `signal_generator` 里 2026-10-03 的 **D4 120-epoch 增强实验**（文档中
  唯一标记为 INCOMPLETE 的训练）：原 run 被取消、无 checkpoint；但后续 **rerun 已完整跑满
  120/120**（`curves.json` 120 条，best epoch 113，val macro-F1 0.9197，`model.pt` 10,445 B），
  `status.md` 未更新仍写 "No completion claim yet"。**它没有剩余 epoch 可接续**；若要收尾，
  下一步是补正式评估/验收声明，而不是继续训练。本次未修改旧工作区任何文件。

## 2. 续训方法（单变量：只补足 epoch 预算）

- 初始点：`artifacts/trials/formal-40ep/cnn/model.pt`（best epoch 39 权重；
  sha256 `04d1cfae3d457544701693e9f8db6b4e73c87836bc5236e4bcebf81ee38df60c`）。
- 配置/数据：`ml_training/config/first_round.json`（formal）+ `datasets/sim-v1` 的 train/val；
  AdamW lr 1e-3、wd 1e-4、batch 64、threads 4、seed 20261020、patience 8；只按 val macro-F1
  选 checkpoint；gate 门限仍只用 val known + gate_cal 重选（冻结流程）。
- 续训 40 epoch（绝对 epoch 41–80）。**无 early stop**；best epoch 76（与正式 80ep 模型的
  best epoch 相同）。
- 输入：权重（trial）；**未续**：优化器状态与 dataloader 状态（原 trial 未保存）——元数据
  `optimiser_state_resumed=false`、`loader_state_resumed=false`，并注明是 warm start。
- 产出目录（新目录，不改首轮）：
  `ml_training/round2/artifacts/training-resume/formal-40ep-continued/cnn/`
  （`model.json`、`model.pt`、`curves.json` 80 条、`metadata.json`、`thresholds.json`）。

## 3. 结果（同一 round-1 val 1,200 帧）

| 训练 | best epoch | val accuracy | val macro-F1 |
|---|---|---|---|
| 40ep trial（原） | 39 | 0.7133 | 0.7005 |
| **40ep trial + 续训 40ep（本次）** | **76** | **0.8542** | **0.8538** |
| 首轮正式 80ep（从零，交付模型） | 76 | 0.8550 | 0.8545 |

续训曲线（val macro-F1，节选）：epoch 41 0.6866 → 44 0.7322 → 48 0.7576 → 51 0.7896 →
57 0.8225 → 64 0.8365 → 71 0.8454 → **76 0.8538** → 80 0.8262。

结论：40ep trial 只是**预算不足**（欠训练），并非数据/优化器/流程损坏；续训后其 val 指标
与正式从零 80ep 模型几乎相同（macro-F1 差 −0.0007，accuracy 差 −0.0008），best epoch 也同为
第 76 轮。该结果支持首轮"40 → 80 epoch"预算决策，同时**不替代**正式交付模型。

## 4. 纪律与限制

- **未评估首轮独立 test split**：本续训是 val-only 诊断，避免重复使用首轮一次性 test 证据。
- 真实数据未参与训练/选择；无 ARM/板卡数字（全部为主机 CPU）。
- 首轮产物（`artifacts/cnn/*`、`artifacts/trials/formal-40ep/*`、`datasets/sim-v1/*`、
  `reports/first-round-report.md`）与第二轮产物均未修改；关键 SHA-256 校验见
  `ml_training/round2/tests/test_round2.py` 与 `test_trial_resume.py`。
- `model.json` 的 thresholds 是按首轮冻结流程在 val known + gate_cal 上重选的
  （`max_softmax=0.45, margin=0, min_complex_rms=57.0596`）；gate 指标不是本续训目标，未重新出报告。
- 该续训模型状态为 val-only 诊断，**未**导出为交付/上板候选；正式交付仍是首轮 80ep 模型。

## 5. 产物与复现

| 内容 | 路径 |
|---|---|
| 续训模型 | `ml_training/round2/artifacts/training-resume/formal-40ep-continued/cnn/{model.json,model.pt}` |
| 续训曲线（80 条） | `.../formal-40ep-continued/cnn/curves.json` |
| 元数据（绑定/对比/限制） | `.../formal-40ep-continued/cnn/metadata.json` |
| 验收测试 | `ml_training/round2/tests/test_trial_resume.py` |

```bat
ml_training\.venv\Scripts\python.exe ml_training\round2\scripts\resume_40ep_trial.py
ml_training\.venv\Scripts\python.exe -m pytest ml_training\round2\tests -q
```

本次续训关键哈希：`model.json` `ba8cd51559d607102cedc2330aa970997baa18b0d49e381c6c2519393bfe6698`
（49,051 B）；`model.pt` `a5f81ef0a77e87f2d1fb8a389d113639afdbbe61b1a0ae5ec3cdb2e1728994fe`
（10,417 B）；torch↔NumPy 50 帧 max|Δlogit|=5.72e-06、argmax 一致率 1.0。
