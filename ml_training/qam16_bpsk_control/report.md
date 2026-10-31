# 候选 A 的 BPSK 退步：C0/C1 受控训练对照

状态：**C0/C1 完成；无合格候选（NO_QUALIFIED_CANDIDATE）**。
只新增 `ml_training/qam16_bpsk_control/`；未覆盖原模型、规则、A/B 结果或原始证据；未连设备。
14 帧真实 IQ 只作选择后的开发诊断，不参与选择，不加入 TRAIN，也不是独立验收。

## 0 结论

- **预写选择规则判定 C0/C1 均不合格。**
  - 两者 VAL 的 QAM16 都有改善，但 **BPSK 保持** 和 **其他类别安全** 不通过；
  - 因此 **没有推荐候选，不冻结、不部署**；A 保留为实验基线，B 继续淘汰。
- **“BPSK 退步由共轭增广所致”不被本轮支持。**
  - C0 完全没有增广，只用 `[raw, raw]`，VAL BPSK 仍从 0.950 降到 0.9367（-1.33 pp）；
  - C1 的 BPSK/QPSK 拷贝是 raw，只有 QAM16/QAM64 做共轭，VAL BPSK 仍降到 0.9167；
  - C0 说明 BPSK 退步可在**没有任何共轭增广**时发生；C1 也没有比 C0 更好地保持 BPSK。
  - 更具体地说，C0/C1 的 QPSK 也大幅退步（-8.33 / -6.00 pp），指向**重复训练集/seed/
    优化轨迹的方差或数据重复效应**，而不是单纯 BPSK 共轭增广。
- C0 在真实验证诊断中甚至 3/3 修复 QAM16，而 C1 为 0/3；进一步说明不能把 A 的真实三帧
  改善单独归因于共轭增广。真实 14 帧仍是开发诊断，不用于选择。

## 1 运行与预算

预写规则：[selection_rule.json](selection_rule.json)（训练前写入，SHA
`abbbe5a8155edc09279b993829fc31c5fddc1b217f8cf399cd8e8e9bf2ca56ca`）。

```bat
:: C0: [raw, raw]，无增广
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\qam16_bpsk_control\train_control.py --candidate C0 --budget-seconds 900

:: C1: [raw, copy]，copy 只对 QAM16/QAM64 共轭；BPSK/QPSK copy=raw
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\qam16_bpsk_control\train_control.py --candidate C1 --budget-seconds 900

:: 同一 VAL + 最终 Round2 流程，应用预写规则
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\qam16_bpsk_control\evaluate_selection.py

:: 选择之后才运行 14 帧开发诊断
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\qam16_bpsk_control\diagnose_dev.py
```

- C0：**250.68 s**；C1：**342.25 s**；合计 **592.93 s / 1800 s**，未触发限时。
- 每个 epoch 保存 checkpoint；`checkpoint_best.pt`/`checkpoint_last.pt` 和 `resume_command`
  均已保存，没有从头重训。

## 2 数据构造与公平性

| 项 | C0 | C1 |
|---|---|---|
| 训练数据 | `[raw, raw]` | `[raw, copy]` |
| copy 规则 | raw | BPSK/QPSK=raw；QAM16/QAM64=conjugate |
| 总帧数 | 4800→9600 | 4800→9600 |
| 每类原始 | 1200/类 | 1200/类 |
| 每类训练 | 2400/类 | 2400/类 |
| seed / 网络 / optimizer | 20261020 / tiny-iq-cnn-v1 / AdamW 同参数 | 同 |
| 预处理 / 阈值 / baseline 仲裁 / 频谱规则 | 冻结原样 | 冻结原样 |
| 真实数据用于训练 | 否 | 否 |

候选规则只替换 `frozen_round1.cnn`；baseline、`max_softmax=0.45`、`margin=0.0`、
`min_complex_rms=57.05962767972466`、仲裁和谱门均未改。

## 3 同一 VAL + 最终 Round2 决策对比

| 模型 | BPSK | QPSK | QAM16 | QAM64 | macro-F1 | accepted acc |
|---|---:|---:|---:|---:|---:|---:|
| frozen original | **0.9500** | **0.9367** | 0.7467 | 0.7933 | 0.8690 | 0.8832 |
| A（实验基线，全部类共轭） | 0.8733 | 0.9800 | 0.8500 | 0.8567 | 0.8947 | 0.8997 |
| C0（无增广） | 0.9367 | **0.8533** | 0.7933 | 0.8900 | 0.8724 | 0.8771 |
| C1（仅 QAM 共轭） | 0.9167 | 0.8767 | 0.7933 | 0.8967 | 0.8742 | 0.8782 |

- C0/C1 QAM16 均 **+4.67 pp**，QAM64 均明显上升；但 C0/C1 的 BPSK 和 QPSK 都低于 baseline。
- C1 的 BPSK 比 C0 更低（0.9167 vs 0.9367），没有达到“保持 BPSK”的目标。
- A 的 BPSK 最低（0.8733），但其 QPSK/QAM16 更高；A 不是本轮候选，仅作基线对照。
- 质量门 `gate_test`：frozen/A/C0/C1 的 OFF、NOISE、TONE、CLIP rejection 均为 **1.000**，
  无退步；`decision_thresholds` 未重校准。

## 4 预写选择规则的执行结果

规则要求（训练前写入）：

1. **BPSK 保持**：VAL BPSK recall ≥ baseline − 0.01；
2. **QAM16 改善**：VAL QAM16 recall ≥ baseline + 0.02；
3. **其他类别安全**：VAL QPSK/QAM64 recall 各 ≥ baseline − 0.02；
4. **质量门不退步**：gate_test OFF/NOISE/TONE/CLIP 各在 baseline 的 0.01 内；
5. 合格者按 VAL macro-F1 选择，QAM16 次之；无合格者则 `NO_QUALIFIED_CANDIDATE`。

实际检查：

| 候选 | gate_quality | BPSK 保持 | QAM16 改善 | 其他类别安全 | 结果 |
|---|---|---|---|---|---|
| C0 | ✓ | ✗（0.9367 < 0.94） | ✓ | ✗（QPSK 0.8533） | 不合格 |
| C1 | ✓ | ✗（0.9167 < 0.94） | ✓ | ✗（QPSK 0.8767） | 不合格 |

**选择结果：`selected = null`，`NO_QUALIFIED_CANDIDATE`。**  
没有依据三帧真实结果挑选 checkpoint；14 帧在 selection 完成后才被读取。

## 5 已知比较集（sim-v1/test，不用于选择）

| 模型 | BPSK | QPSK | QAM16 | QAM64 | macro-F1 |
|---|---:|---:|---:|---:|---:|
| frozen original | 0.9300 | 0.9150 | 0.7650 | 0.7867 | 0.8669 |
| A | 0.8800 | 0.9417 | 0.8567 | 0.8667 | 0.8988 |
| C0 | 0.9217 | 0.8533 | 0.8317 | 0.9067 | 0.8881 |
| C1 | 0.9133 | 0.8850 | 0.8183 | 0.8883 | 0.8857 |

该 test 只作已知比较；不参与选择，也不能作为独立验收声明。

## 6 14 帧开发诊断（选择之后，非验收）

| 预测器 | ON 正确 | QAM16 | OFF 拒识 |
|---|---:|---:|---:|
| frozen original | 9/12 | 0/3（全 QAM64） | 2/2 |
| A | 12/12 | 3/3 | 2/2 |
| C0 | 10/12 | 3/3 | 2/2 |
| C1 | 9/12 | **0/3（全 QAM64）** | 2/2 |

C0（无增广）也能修复三帧真实 QAM16，而 C1（仅 QAM 共轭）不能；这进一步说明真实三帧的
改善 **不能单独归因于共轭增广**，且 14 帧只能作开发诊断，不能作为独立验收。C1 不因三帧
结果改变选择；选择仍为无合格候选。

## 7 BPSK 退步归因判断

- **支持“共轭增广所致”的证据不足。**
  - C0 无任何增广，VAL BPSK 仍下降 1.33 pp；QPSK 下降 8.33 pp。
  - C1 的 BPSK/QPSK 副本本来就是 raw，BPSK 仍下降 3.33 pp；没有恢复 BPSK。
  - C0/C1 都采用 9600 帧（raw 重复一倍），与 baseline 的 4800 帧不同；因此数据重复、
    seed/优化轨迹与增广的效应混在一起，本轮不能把 BPSK 退步单独归因于共轭增广。
- **C1 相对 C0** 的 BPSK 额外下降约 2 pp，提示 QAM-only 共轭可能仍有一点影响，但远不足以
  解释 A 的全部 BPSK 退步；且 QPSK 的大幅退步说明训练数据重复/优化方差更突出。
- 结论：A 的 BPSK 退步不能作为“共轭增广”已证实的因果结论；下一项应优先验证
  **多 seed/重复训练集方差**，而不是继续增加增广。

## 8 推荐与下一步

**推荐候选：无。** C0/C1 都不满足预写规则；A 保持实验基线；B 淘汰；不冻结、不部署。

下一步最小验证（本轮不执行，也不开 D/E）：

1. **多 seed 对照**：C0/C1 各至少 3 个 seed，相同总帧数和每类帧数，记录 BPSK/QPSK/QAM16
   分布，先证明退步是否超出 seed/训练方差；
2. **同规模无重复控制**：4800 帧 raw-only（不重复）与 9600 帧重复控制，分离“数据重复”
   与“增广”的效应；
3. **独立真实验收集**：新的真实帧（非本 14 帧、非变换副本），覆盖 BPSK/QPSK/QAM16/QAM64/OFF，
   真值仍由 runner TX mode/seed/amplitude + capture/IQ SHA 生成；14 帧只保留开发诊断；
4. 任何正式候选仍使用原预处理、原阈值、原 baseline 仲裁/频谱规则；不连接设备、不安装。

## 9 哈希与产物

| 项 | SHA-256 |
|---|---|
| C0 model.json | `0847607d5edb6f3fde42fd5106622a54f0ed5294c5b6da88f62bc1252148cb04` |
| C0 rules.json | `fe59e166d4b22346ea1182efa8d79b5d7a74fc73be23f9fceb149a8ceacc8d53` |
| C1 model.json | `1be11069e9a49ed989090ddbf304d3eefc721739412966a735edf7a4208b80bf` |
| C1 rules.json | `9f94900ff0482ded05edbf92822da26637f080a11bf151c2f7a8bcee404cc2e1` |
| selection.json | `a5c761b4e6d208ffd62a54ad83be132f2457026ce73a9bc8b34c43bf026cf401` |
| dev_diagnosis.json | `6dd5510fe4cde9bb843a7fb57fb718207a864ffe3c8a295b06383db12651b697` |
| comparison.json | `8d9567d648be89b6e3c200ce5c281fb28fb25040ee4a3eb97d644e2c0d6a2649` |

产物：`results/C0/`、`results/C1/`、`results/selection.json`、
`results/dev_diagnosis.json`、`results/comparison.json`；脚本 `train_control.py`、
`evaluate_selection.py`、`diagnose_dev.py`、`summarize.py`。
