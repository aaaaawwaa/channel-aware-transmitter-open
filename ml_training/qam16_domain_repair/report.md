# QAM16 真实域失配：离线模型修复试验

状态：**两个候选完成，训练总耗时 609.4 s（约 10.2 min，预算 30 min 内）**。
只新增 `ml_training/qam16_domain_repair/`；未改冻结模型/规则/产品文件/原始证据，未连设备。
14 帧真实 IQ 只作开发诊断，**不进入 TRAIN，也不是独立验收集**。

## 0 结论

- **候选 A（仅共轭/频谱反转增广）有效，值得继续正式训练；但当前不建议直接冻结。**
  - 真实开发诊断 QAM16：**0/3 → 3/3**；三帧分数从 QAM64 0.911/0.898/0.815
    变为 QAM16 0.954/0.950/0.959。
  - 独立 seed 合成 test：macro-F1 **0.8669 → 0.8988**，accuracy 0.8492 → 0.8862；
    QAM16 recall **0.765 → 0.857**，QAM64 **0.787 → 0.867**，QPSK 0.915 → 0.942。
  - **退步：BPSK recall 0.930 → 0.880（-5.0 pp）**；真实 BPSK 3/3 未退，质量门未退。
    因此 A 是一个强候选，但冻结前必须处理 BPSK 回归并做独立正式验证。
- **候选 B（A + 唯一一项有证据的 IQ gain imbalance）拒绝。**
  - 合成 test 总体略升（macro 0.8910），但 QAM16 recall 0.755（低于 baseline），
    真实开发 QAM16 仅 **1/3**；不优于 A，不再继续堆增广。
- **下一步可证伪假设**：A 的 BPSK 回归来自对 BPSK 也等量施加了共轭副本；下一次正式训练应做
  **BPSK 保持/按类加权的共轭增广**（不是再叠加新损伤）。若正式运行仍不能恢复 BPSK，
  则 A 不应冻结。此假设不在本轮继续开新候选。

## 1 入口与预算

```bat
:: 候选 A（仅共轭增广，train-only）
ml_training\.venv\Scripts\python.exe -B ml_training\qam16_domain_repair\train_candidate.py ^
  --candidate A --budget-seconds 900

:: 候选 B（A + 唯一一项 IQ gain imbalance；仅在 A 结果需要时执行）
ml_training\.venv\Scripts\python.exe -B ml_training\qam16_domain_repair\train_candidate.py ^
  --candidate B --budget-seconds 900

:: 独立合成 test / gate_test + 14 帧开发诊断
ml_training\.venv\Scripts\python.exe -B ml_training\qam16_domain_repair\evaluate_candidate.py --candidate baseline
ml_training\.venv\Scripts\python.exe -B ml_training\qam16_domain_repair\evaluate_candidate.py --candidate A
ml_training\.venv\Scripts\python.exe -B ml_training\qam16_domain_repair\evaluate_candidate.py --candidate B
ml_training\.venv\Scripts\python.exe -B ml_training\qam16_domain_repair\summarize.py
```

- 总训练预算 1800 s；实际 **A 283.3 s**（首训 279.26 s + checkpoint 续跑/导出 4.01 s）、
  **B 326.1 s**，合计 **609.4 s**；未触发限时。
- 每个 epoch 保存 `checkpoint_last.pt`；最佳 epoch 保存 `checkpoint_best.pt`。
  续跑命令已写入候选 `metadata.json` 的 `resume_command`；本轮没有从头重训。
- A 的 `metadata.json` 中 `train_seconds` 是最后一次续跑/导出的 4.0 s；**实际总训练时间**
  以本报告/`results/comparison.json` 的 observed 值为准（首训日志 279.26 s + 4.01 s）。

## 2 候选配置与哈希

| 项 | 候选 A | 候选 B |
|---|---|---|
| 增广 | 仅 train 的 conjugate/频谱反转；4800→9600 | A + IQ gain imbalance（I gain U[0.80,1.00] on conjugate copy）；4800→14400 |
| 架构 | `tiny-iq-cnn-v1`，1628 params | 同 |
| 预处理 | `dc-complex-rms-v1`（原样） | 同 |
| 决策阈值 | 冻结 `max_softmax=0.45, margin=0.0, min_complex_rms=57.05962767972466` | 同 |
| 阈值重校准 | 否 | 否 |
| seed / best epoch | 20261020 / 65 | 20261020 / 50 |
| best val macro-F1 | 0.845389 | 0.884264 |
| model.json SHA | `b840f9dbaebc5db855ef1535d091e1f1583e12368ca62742f517a8d38123a28e` | `e0f557a180ed6aa30e36bc0022a72ac698312b6d75d50feaf9d1184ec272bd2f` |
| model.pt SHA | `2521f445f2a2ce8e681713a2d3653f43378bda9c9cd6428ce07f1409ae6bad0f` | `f047cc097d377527db11550f79fe8990d4bd794390d7d12b333dafcbb045f527` |
| rules.json SHA | `10a0bef4d237818eaa39dcd4e3048466de60017dbf0545c02bcbf405da2cdf64` | `77149bff59b3b30927f4577eb38f30c1ecae492e8dd9ff7f3e2ba4e834438822` |
| checkpoint_best SHA | `32a1605d04fee85eeeb9b79d0507f51c92244d288bed3253374ef4fcd588ac74` | `05dc0a92eb3e07716b2ab2cfe803927d44c72d8494b2a230abef763764512bf8` |
| checkpoint_last SHA | `f6295ca8759018592c127319b4fa4c74de18cc5b4a0b38c6ec61b1870e7b52b4` | `69e745d31017d9237f79166e678e3e62e6a8a3f5ef9885aefaefa26c6d5f3cf2` |

候选规则只把 `frozen_round1.cnn` 换成候选 model.json；baseline、gate、arbitration、spectral
规则完全沿用冻结配置。未改冻结文件。

## 3 独立 seed 合成验证

数据：`sim-v1/test`，seed families `test-a/test-b/test-c`，训练从未使用；`gate_test` 使用
独立 gate seed families。`test` 正常集和其 **诊断性共轭变换**（不是新数据）均报告。

### 3.1 四类 recall（test 正常）

| 类 | baseline | A | ΔA | B | ΔB |
|---|---:|---:|---:|---:|---:|
| BPSK | 0.9300 | 0.8800 | **-0.0500** | 0.9350 | +0.0050 |
| QPSK | 0.9150 | 0.9417 | +0.0267 | 0.9383 | +0.0233 |
| QAM16 | 0.7650 | 0.8567 | **+0.0917** | 0.7550 | -0.0100 |
| QAM64 | 0.7867 | 0.8667 | +0.0800 | 0.9067 | +0.1200 |
| macro-F1 | 0.8669 | **0.8988** | +0.0319 | 0.8910 | +0.0241 |
| accuracy | 0.8492 | **0.8862** | +0.0371 | 0.8838 | +0.0346 |
| accept rate | 0.9588 | 0.9729 | +0.0142 | 0.9838 | +0.0250 |
| accepted accuracy | 0.8857 | **0.9109** | +0.0252 | 0.8983 | +0.0126 |

### 3.2 诊断性共轭 test（Q→−Q；非独立验收）

| 类 | baseline | A | B |
|---|---:|---:|---:|
| BPSK | 0.9217 | 0.8933 | 0.9417 |
| QPSK | 0.9183 | 0.9400 | 0.9367 |
| QAM16 | 0.7800 | 0.8300 | 0.7567 |
| QAM64 | 0.7633 | 0.8533 | 0.9200 |
| macro-F1 | 0.8635 | 0.8908 | 0.8966 |

### 3.3 质量拒识（`gate_test`，含 clipped）

| kind | baseline | A | B |
|---|---:|---:|---:|
| OFF | 1.000 | 1.000 | 1.000 |
| NOISE | 1.000 | 1.000 | 1.000 |
| TONE | 1.000 | 1.000 | 1.000 |
| CLIP | 1.000 | 1.000 | 1.000 |
| all | 1.000 | 1.000 | 1.000 |

结论：**质量拒识没有退步**；A/B 的阈值未变。A 的主要退步是 BPSK 四类分类 recall。

## 4 真实 14 帧开发诊断（非独立验收）

真值来源：runner 的 TX mode/seed/amplitude + `capture_id + IQ SHA`；未使用模型预测、RX
expected_mode 或旧六类标签。14 帧仅开发诊断，不加入 TRAIN。

### 4.1 QAM16 三帧

| 帧 | baseline | A | B | A QAM16/QAM64 score |
|---|---|---|---|---:|
| s02-qam16-1 | QAM64 | **QAM16** | QAM64 | 0.954 / 0.046 |
| s02-qam16-2 | QAM64 | **QAM16** | QAM64 | 0.950 / 0.050 |
| s02-qam16-3 | QAM64 | **QAM16** | **QAM16** | 0.959 / 0.041 |

- **A：3/3 修复**；B：1/3，且另外两帧 QAM16 分数仅 0.352/0.455，仍 QAM64。
- A 对真实 QAM16 的三帧均为 accepted=true。

### 4.2 其他类别与 OFF

| 帧组 | baseline | A | B |
|---|---|---|---|
| BPSK 3 帧 | 3/3 | 3/3 | 3/3 |
| QPSK 3 帧 | 3/3 | 3/3 | 3/3 |
| QAM64 3 帧 | 3/3 | 3/3 | 3/3 |
| OFF 2 帧 | 2/2 UNKNOWN | 2/2 UNKNOWN | 2/2 UNKNOWN |

A 在真实 14 帧上除修复 QAM16 外没有其他类别/OFF 退步；但这 14 帧是开发诊断，不能作为
独立验收。

## 5 逐项退步记录

- **A**：BPSK 合成 recall **-5.0 pp**；BPSK 合成 accept rate 0.9717→0.9683（-0.34 pp）；
  真实 BPSK 未退；质量门未退。其他三类均升。
- **B**：BPSK 合成恢复（+0.5 pp），QPSK/QAM64 升；但 QAM16 合成 **-1.0 pp**，真实 QAM16
  仅 1/3；**不能接受**。
- A/B 均未改阈值、预处理、baseline、spectral/arbitration；gate_test 无退步。
- 本轮没有同 seed 的无增广控制重训；因此 A 的 BPSK 回归是观测事实，**不能单独归因于共轭
  增广**。正式训练应加入同 seed/无增广对照或按类保持方案。

## 6 是否值得继续正式训练/冻结

**A：值得继续正式训练，但不值得现在冻结。**
- 支持：真实目标 QAM16 0/3→3/3；独立合成 macro/accuracy/QAM16/QAM64 均升；其他真实类和
  OFF/质量门未退。
- 阻止冻结：BPSK 合成 recall -5 pp；且 14 帧仅开发诊断，没有新的独立真实验收集；A 的
  val macro（0.845）低于 frozen（0.855），提示仍需正式训练/验证流程。

**B：拒绝。** 唯一一项 IQ gain imbalance 增广没有修复目标，真实 QAM16 反而从 A 的 3/3
降到 1/3；不继续堆增广。

**正式训练前的可证伪假设**：A 的 BPSK 回归来自“对 BPSK 也等量复制共轭样本”；
用按类加权/仅 QAM 的共轭增广（仍只改训练数据）应恢复 BPSK 并保留 QAM16 收益。
若正式运行不能恢复 BPSK，A 不应冻结。

## 7 产物

- 训练/评估脚本：`train_candidate.py`、`evaluate_candidate.py`、`summarize.py`
- 候选目录：`results/candidate_A/`、`results/candidate_B/`
  （`model.json`、`model.pt`、`rules.json`、`metadata.json`、`curves.json`、checkpoints）
- 评估：`results/baseline_frozen_eval.json`、`results/candidate_A_eval.json`、
  `results/candidate_B_eval.json`
- 汇总：`results/comparison.json`（SHA `7e3982883739ac88a24f48c4a100e7c550d0c46e262fbb9725ff627b8aa12420`）
- 原始证据、冻结模型/规则/产品文件均未修改；没有新真实帧，没有设备访问。
