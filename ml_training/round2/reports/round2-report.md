# 第二轮离线改进报告 — 真实 BPSK 误判与单音/噪声拒识

> 本轮只写 `ml_training/`：不连设备、不部署、不改产品运行时。首轮模型/数据/报告/失败结果
> 全部保留（本轮测试以 SHA-256 固定校验首轮关键产物未被修改）。所有时延为主机 CPU 数字，
> **不是** ARM/板卡数字；仿真准确率**不是**实测/板卡准确率。

## 1. 结论摘要

| 项目 | 改进前（首轮冻结产物） | 本轮改进后 |
|---|---|---|
| 真实 BPSK `train-02`（dev replay，10 帧） | 0/10（全部高置信判 QPSK） | **10/10 BPSK** |
| 独立真实会话 campaign3 `train-01`（未参与开发，10 帧） | 0/10（全部判 QPSK） | **10/10 BPSK** |
| 独立真实会话 OFF 帧 | 被判 QAM64 接受 | **拒识**（noise_like） |
| 新仿真 test（新种子族，未参与调参，2400 帧） | acc 0.8471 / macro-F1 0.8469 | **acc 0.8654 / macro-F1 0.8652**（+1.83 pp / +1.82 pp） |
| gate_test TONE 拒识 | 0% | **100%** |
| gate_test NOISE 拒识 | 20% | **100%** |
| gate_test OFF / CLIP 拒识 | 100% / 100% | 100% / 100% |
| 已知类误拒（新 test 2400 帧） | 84（3.50%） | 98（4.08%）；增量 14 帧全部在 -6 dB，未通过"全部拒绝"取巧 |
| 模型参数 | CNN 1,628 | CNN **不变**；复用 36 参数基线；合计 1,664，**0 个新学习参数、0 次重训** |
| 主机推理（batch=1，非 ARM） | 1.39 ms 中位 / 2.18 ms P95 | 3.39 ms / 6.28 ms（约 2.4×；两次完整运行中位 1.22–1.39 → 3.39–3.60 ms） |

结论：两个优先问题都得到可复现的改善，且改善在**未参与开发/标定**的新仿真测试集与
**一个独立真实会话**上同时成立；代价是推理时延约 3 倍、已知类误拒率 +0.58 pp（集中在最低
SNR 档），并明确不把改善解释为"已解决真实泛化"——真实数据仍只有 BPSK 单一配置。

## 2. 改进依据（先比较特征基线与 CNN 的失败帧）

失败帧分析见 `ml_training/round2/analysis/failure_frames.json/.md`（只读真实 evidence；
真值只取 `trainval.truth` + `demod_result.submitted.config`，环境规则标签仅作上下文，未作标签）。

- 三会话对比（10 帧/会话）：`train-01` CNN 10/10 BPSK、baseline 10/10；`train-02` **CNN 0/10
  （全部 QPSK、score 0.53–0.88、margin 0.06–0.75，高置信接受、非门限拒识）而 baseline 10/10**；
  `val-01` CNN 10/10、baseline 10/10。
- 输入/物理差异（只读 metadata）：failure 会话 `complex_rms` 会话均值≈318.7（成功会话≈566/614），
  载波相位≈63.2°（成功会话≈-4.9°/1.8°），载波相干度估计 SNR≈10.5 dB（≈15.9/16.5 dB）；
  现有环境规则对 9/10 失败帧给出 `PULSE/SPARSE_AMPLITUDE_OUTLIERS`（仅作为失真相关性证据）。
- 特征差异（8 维可解释特征，failure vs success 均值）：`amp_kurtosis` 1.1845 vs 1.0885、
  `amp_sixth` 1.5907 vs 1.2508、`c20_abs` 0.9112 vs 0.9760、`c40_abs` 1.6285 vs 1.8618、
  `amp_entropy` 0.8274 vs 0.6515 —— 失败帧的可解释特征仍是"带失真/噪声的 BPSK"，
  基线据此正确判 BPSK，CNN 判 QPSK。
- 受控诊断（不修改模型/数据）：把失败帧**正交分量置零或替换为同功率白噪声**后 CNN 恢复 BPSK，
  说明触发 QPSK 的是**结构化的正交（quadrature）内容**；但幅度缩放、稀疏幅度离群点去除/
  限幅、匹配 SNR/相位/幅度、相位对称 TTA 都不能复现或修复该失败。根因仍
  `NOT_ESTABLISHED`（与首轮一致：未建模硬件失真是主要嫌疑）。
- 首轮已经全相位随机化，本轮的修复**不是**"再加随机相位"：见下节被否决候选。

### 2.1 候选改进的小规模对照（dev-only）

| 候选（单项） | dev 验证 | 真实 train-02 | 结论 |
|---|---|---|---|
| 相位对称 TTA（0/90/180/270°，标签保持，无参数） | round-1 val 0.8400→0.8567 | **仍 0/10** | 否决（不修复真实失败，仅小幅仿真收益） |
| 稀疏幅度离群点注入/去除、限幅、匹配 SNR/幅度/相位 | 无法复现失败；去除离群点后仍 QPSK | 无改善 | 否决（不是主因） |
| **特征基线参与 BPSK/QPSK 仲裁**（冻结 CNN + 冻结 36 参数基线，无新参数） | round-2 val 0.8375→**0.8542**（+21/−1） | **10/10** | **采用**（同时改善仿真与真实） |

采用依据：失败帧的相位不变量特征与基线判决仍指向 BPSK，而 CNN 在这一"低幅度 + 约 63° 相位 +
未建模正交失真"的会话上系统性失效；用一个已冻结、已在首轮真实数据 30/30 正确的 36 参数基线
做窄范围（只仲裁 BPSK vs QPSK）一致性检查，是证据最强、改动最小、且可解释的方案。

### 2.2 拒识改进依据（B：TONE 0%、NOISE 23%）

- 首轮 gate_test：TONE 100% 被误接受（被判成 BPSK/QPSK/QAM16/QAM64），NOISE 仅 23% 被拒；
  失败原因是"能量地板 + softmax 置信度"对**带内单音/纯噪声**没有判别力。
- 采用两个可解释频谱特征（单位复 RMS 预处理后计算，尺度不变）：
  `top5_frac`（最强 5 个 FFT bin 的能量占比）与 `band_frac_3m`（|f|≤3 MHz 主瓣能量占比）。
- **第一性依据**：单音能量集中在少数 bin（gate_cal TONE `top5_frac` 最小 0.917，已知类最大
  0.138）；RECT 调制 ~90% 能量在 ±3 MHz 主瓣内（已知类最小 `band_frac_3m` 约 0.30），
  全带 AWGN 仅约 10%（gate_cal NOISE 全部低于阈值）。两个阈值的选取只用
  round-2 `gate_cal` + `val`（dev），并显式限制**增量**已知类误拒 ≤ 1%。

## 3. 数据与纪律

- round-2 配置：`ml_training/round2/config/round2.json`（sha256 `431c4e7b…`）；waveform/impairment
  与首轮完全相同，只有 dataset_id 与 **新种子族** 不同；标注了首轮配置/数据集/模型哈希。
- round-2 数据集 `sim-r2`：4,800 帧 / 55.2 MiB（train 400 / val 1,200 / test 2,400 / gate 各 400），
  manifest sha256 `83afaf8a…`；主数据 clipped **0**（gate 数据 226，CLIP 200 + NOISE 26）；
  独立参考检查 **22/22 PASS**，4,800/4,800 帧逐字节重建，跨/内 split 重复 0/0。
- **用途隔离**：`val`/`gate_cal` 仅用于规则标定（dev）；`test`/`gate_test` 在规则冻结后才首次
  读取用于最终评估。之后为补充诊断/复现而按**同一冻结规则**做过确定性重跑，从未据 test 结果
  回改任何阈值（阈值文件 mtime 早于首次评估产物）；首轮 test 保持首轮证据不动。
- 真实只读：`campaign5` 三会话是 **dev replay**（参与过首轮分析与本轮设计）；
  `campaign3 train-01`（release `1a30f4b7…`，11 帧）在规则冻结后才被扫描到，IQ 哈希与 dev
  三会话不重叠，作为**独立真实会话**单独报告。evidence 全程只读、未修改。
- 首轮产物保持：CNN `25a65fb1…`、基线 `caa82589…`、sim-v1 manifest `9f70a0c8…`、
  首轮报告 `31b25fbb…` 均由本轮测试按 SHA-256 校验未变；生成 round-2 数据时包装器自动
  备份/还原了首轮 `generation-report.md`。

## 4. 新仿真测试集前后对照（NEW seeds；未参与调参）

协议同首轮：四类指标 = 纯分类决策（argmax / 仲裁后类别），门限拒识单独报告。
数据：`sim-r2/test`，2,400 帧，clipped=0。

### 4.1 混淆矩阵

| true \ predicted | BPSK | QPSK | QAM16 | QAM64 |
|---|---|---|---|---|
| **BEFORE** BPSK | 547 | 46 | 0 | 7 |
| BEFORE QPSK | 40 | 546 | 1 | 13 |
| BEFORE QAM16 | 3 | 16 | 473 | 108 |
| BEFORE QAM64 | 0 | 11 | 122 | 467 |
| **AFTER** BPSK | **574** | 19 | 0 | 7 |
| AFTER QPSK | 23 | **563** | 1 | 13 |
| AFTER QAM16 | 2 | 17 | 473 | 108 |
| AFTER QAM64 | 0 | 11 | 122 | 467 |

### 4.2 指标

| class | support | before P/R/F1 | after P/R/F1 |
|---|---|---|---|
| BPSK | 600 | 0.9271 / 0.9117 / 0.9193 | **0.9583 / 0.9567 / 0.9575** |
| QPSK | 600 | 0.8821 / 0.9100 / 0.8958 | **0.9230 / 0.9383 / 0.9306** |
| QAM16 | 600 | 0.7936 / 0.7883 / 0.7910 | 0.7936 / 0.7883 / 0.7910 |
| QAM64 | 600 | 0.7849 / 0.7783 / 0.7816 | 0.7849 / 0.7783 / 0.7816 |
| **合计** | 2,400 | **acc 0.8471 / macro-F1 0.8469** | **acc 0.8654 / macro-F1 0.8652** |

仲裁只在 CNN top-1 ∈ {BPSK, QPSK} 时介入：共改变 53 帧（+47 正确 / −3 使正确变错 / 3 帧错变
另一种错），净 +44 帧 = +1.83 pp；QAM16/64 决策不受影响。

### 4.3 逐 SNR（完整表见 `evaluation/reports/round2_sim_snr.md`）

| SNR dB | 200 帧 | before acc | after acc |
|---|---|---|---|
| -6 | 200 | 0.4550 | 0.4950 |
| -3 | 200 | 0.6050 | 0.6550 |
| 0 | 200 | 0.7950 | 0.8100 |
| 6 | 200 | 0.9150 | 0.9450 |
| 12 | 200 | 0.9800 | 0.9850 |
| 15 | 200 | 0.9700 | 0.9850 |
| 20 | 200 | 0.9700 | 0.9800 |

改善集中在低 SNR（BPSK/QPSK 边界）与高 SNR 的少量相位敏感错误；QAM 阶数判别不变。

## 5. 质量门：拒识与已知类误拒（NEW gate_test / NEW test）

门限（冻结于标定阶段）：冻结首轮门（clipping、能量地板 57.0596、`max_softmax=0.45`、
`margin=0`）+ `top5_frac > 0.527328` → `tone_like` + `band_frac_3m < 0.251610` → `noise_like`。
标定只用 round-2 `val` + `gate_cal`；未用 test 调参。

| gate_test kind | n | BEFORE 拒识 | AFTER 拒识 | AFTER 原因 |
|---|---|---|---|---|
| OFF | 100 | 100 (1.00) | 100 (1.00) | below_energy_floor 100 |
| NOISE | 100 | 20 (0.20) | **100 (1.00)** | noise_like 87 + clipping 13 |
| TONE | 100 | 0 (0.00) | **100 (1.00)** | tone_like 100 |
| CLIP | 100 | 100 (1.00) | 100 (1.00) | clipping 100 |

已知类误拒（新 test 2,400 帧，非"全部拒绝"取巧）：

| 指标 | BEFORE | AFTER |
|---|---|---|
| 拒识已知帧 | 84（3.50%） | **98（4.08%）** |
| 拒识原因 | confidence 84 | confidence 79 + noise_like 19* |
| 拒识计为错误的 accuracy | 0.8337 | **0.8475** |
| 接受帧数 / 接受帧正确率 | 2,316 / 未复算（只记录拒识口径） | **2,302 / 0.8836** |

\* noise_like 19 帧中 14 帧是增量（BEFORE 未拒、AFTER 新拒）；全部位于 **-6 dB** 档
（-6 dB 已知拒识 74→88），其余各 SNR 档拒识数不变。这 14 帧是"最低 SNR 下更像噪声"的
真实权衡，如实报告，未通过调低约束来在 test 上抹平。

标定时的 dev trade-off（round-2 `val` 1,200 帧）：扫描 `band_frac_3m` 阈值在
增量误拒 ≤0.5%/1%/2% 的约束下都给出同一工作点（noise 拒识 100%、增量误拒 1 帧=0.08%），
说明仿真域内单音/噪声与已知类在选定特征上分离度很大；test 上增量误拒 0.58%，是种子族
差异带来的泛化误差，按纪律不回调阈值。

## 6. 真实数据结果

### 6.1 dev replay（三个 campaign5 会话，只读）

| 会话 | normal | BEFORE BPSK | AFTER BPSK | 仲裁介入 | OFF BEFORE 拒识 | OFF AFTER 拒识 |
|---|---|---|---|---|---|---|
| train-01 | 10 | 10/10 | 10/10 | 0 | 1/1 | 1/1（noise_like） |
| train-02 | 10 | **0/10** | **10/10** | 10 | 1/1 | 1/1（noise_like） |
| val-01 | 10 | 10/10 | 10/10 | 0 | 1/1 | 1/1（noise_like） |

### 6.2 独立真实会话（campaign3 `train-01`，未参与开发）

- `evidence/rf-teaching/s2/46db-trainval-campaign3-2026-10-18/download/train-01`，
  release `1a30f4b7b9aebe27965fcdd6dcf7634e`，10 normal + 1 OFF，TX 配置 BPSK/3 MSym/s/RECT；
  11 个 IQ 哈希与三个 dev 会话均不重叠（`analysis/independent_real_scan.json`），规则冻结后
  才发现并评估。
- 会话特征：`complex_rms` 242.1–248.0（比 dev 成功会话更低），载波相位 54.9°–55.9°，
  demod BER=0；环境规则给 `UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT`（仅上下文）。
- 结果：BEFORE **0/10 BPSK（全部判 QPSK）→ AFTER 10/10 BPSK（10 帧均由仲裁修正）**；
  OFF 帧 BEFORE 被判 QAM64 接受 → AFTER **拒识（noise_like）**。
- 解释边界：这是**一个**独立真实会话、**只有 BPSK 一种配置**，不能据此计算四类真实准确率，
  也不是"真实泛化已解决"的证明；但它独立支持本轮改进方向（CNN 在低幅度/特定相位/失真会话上
  的系统性 QPSK 误判可由冻结基线仲裁修正）。

## 7. 模型大小与耗时（主机 CPU，非 ARM/板卡）

| 项目 | 值 |
|---|---|
| CNN | 不变：1,628 参数、`model.json` 48,971 B（`model.pt` 9,905 B） |
| 可解释基线（复用首轮产物） | 36 参数、2,187 B |
| 新规则配置 | 7,513 B（阈值 JSON，非权重；0 个新学习参数） |
| 合计 | 1,664 参数、58,671 B |
| 推理 before（冻结 CNN 全流程） | 中位 1.391 ms / 均值 1.506 ms / P95 2.176 ms |
| 推理 after（CNN + 频谱门 + 基线仲裁） | 中位 3.390 ms / 均值 3.752 ms / P95 6.283 ms |
| 相对代价 | 中位约 2.4–3.0×（两次完整运行：1.22→3.60 ms、1.39→3.39 ms；P95 1.79→4.79、2.18→6.28 ms） |

优化空间（未做）：只在 CNN top-1 为 BPSK/QPSK 时计算基线特征；频谱门可用两三个 radix-2
子带能量近似。任何优化都需重新标定并重回测试集纪律。板端 ARM 时延/内存 `NOT_RUN`。

## 8. 与板端六类合同的兼容差距（只记录，不改）

- 板端 runtime 的模型合同仍是固定 6 类（TONE/BPSK/QPSK/QAM16/QAM64/OTHER）与固定 head `[6,16]`；
  本轮继续使用 4 类离线 CNN，未修改任何产品代码/合同，也未部署。
- 本轮新增的是**离线推理层规则**（仲裁 + 频谱门），不改变 4 类 CNN 输入/预处理/权重格式；
  将来上板需要：① 6/4 类合同扩展或已知类子集映射；② 规则阈值/特征在 ARM 上的数值一致性；
  ③ ARM 时延与 RSS；④ 真实会话回放（含 OFF/削顶/单音/失真负例）。

## 9. 下一步最少需要的真实数据

1. **更多独立真实 BPSK 会话**（至少 2–3 个不同采集批次/相位/幅度，含低幅度≈250 与≈320 条件），
   用于确认仲裁规则不是对特定两个会话的过拟合，并量化真实误拒/误判率。
2. **真实 QPSK/16QAM/64QAM 独立会话**（每种≥2 个会话），否则真实四类准确率仍为 `NOT_RUN`。
3. **真实 OFF/削顶/单音/窄带/脉冲/未知输入**独立会话（≥2 会话/类），用于重标定频谱门与
   量化真实误接受率（当前频谱门只在仿真 gate_test + 3 个真实 OFF 帧上验证）。
4. **硬件失真标定数据**（尤其失败帧那类"低幅度 + 约 55–65° 相位 + 结构化正交内容"的会话），
   用于把未建模失真转成可建模项、判断是否值得替代仲裁方案。
5. 会话级 TRAIN/VAL/TEST 划分（按采集批次隔离），真实测试集不参与任何规则设计/标定。

## 10. NOT_RUN / 限制

`ARM/板卡 latency、parity、RSS`、`板卡部署`、`真实四类准确率`、`更多独立真实会话`、
`真实 QPSK/16QAM/64QAM`、`真实非 BPSK 门限标定/误接受率`、`硬件损伤建模`、
`RRC/其它符号率`、`大规模超参数搜索`、`真实干扰物理场景`、`置信区间（单次点估计）`。

## 11. 产物与复现

| 内容 | 路径 |
|---|---|
| 配置 | `ml_training/round2/config/round2.json`（`431c4e7b…`） |
| round-2 数据 | `ml_training/round2/datasets/sim-r2/`（manifest `83afaf8a…`） |
| 独立参考检查 | `ml_training/round2/analysis/reference-check-r2.json/.md`（22/22 PASS） |
| 失败帧对照（改进依据） | `ml_training/round2/analysis/failure_frames.json/.md` |
| 候选 TTA 否决实验 | `ml_training/round2/analysis/tta_experiment.json/.md` |
| 规则标定 | `ml_training/round2/analysis/calibration.json`；冻结规则 `artifacts/rule_config.json`（`9aceebbd…`） |
| 独立真实会话扫描 | `ml_training/round2/analysis/independent_real_scan.json` |
| 最终评估 | `ml_training/round2/evaluation/reports/round2_{sim_metrics.json,sim_confusion.md,sim_snr.md,gate.md,real.md,real.json,independent_real.md,independent_real.json}` |
| 运行说明 | `ml_training/round2/README.md` |

复现：

```bat
ml_training\.venv\Scripts\python.exe ml_training\round2\run.py all
ml_training\.venv\Scripts\python.exe -m pytest ml_training\round2\tests -q
```

最终回归：`pytest ml_training\tests ml_training\data\tests ml_training\evaluation\tests ml_training\round2\tests -q`
→ 本轮交付时 **110 passed**（首轮 99 + 当时第二轮 11）；2026-10-21 追加 40ep trial 接续检查后为
**114 passed**（首轮 99 + 第二轮 15），首轮关键产物 SHA-256 校验不变。接续检查见
`reports/trial-40ep-resume.md`。
