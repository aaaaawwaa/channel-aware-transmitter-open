# 轻量调制识别离线训练 —— 首轮报告（BPSK/QPSK/16QAM/64QAM）

> 全部数字来自本机实际运行产物（2026-10-20，Asia/Shanghai）。未运行项一律 `NOT_RUN`。
> 所有时延均为 **主机 CPU** 数字，不是 ARM/板卡数字；仿真准确率不是板卡/实测准确率。

## 1. 结论摘要

| 项目 | 首轮结果 |
|---|---|
| 数据 | sim-v1：9,200 帧（train 4,800 / val 1,200 / test 2,400 / quality-gate 2×400），109.56 MiB |
| 独立参考检查 | **PASS 22/22**，9,200/9,200 帧逐字节重建；跨/内 split 重复 IQ = 0/0 |
| 特征基线 | 8 维可解释特征 + 多项 Logistic，36 参数；仿真 test accuracy **0.6900**，macro-F1 **0.6909** |
| 小型 CNN | 1,628 参数 / 1,278,016 MACs/帧；仿真 test accuracy **0.8467**，macro-F1 **0.8465** |
| 训练 | 80 epochs（val-only 选点，best epoch 76，val macro-F1 0.8545）；训练 140.0 s（主机 CPU 4 线程） |
| 模型大小 | `model.json` 48,971 B（47.8 KiB）；`model.pt` 9,905 B |
| 主机推理 | NumPy runtime 中位 **1.045 ms**/P95 **1.710 ms**；torch 中位 0.459 ms/P95 0.837 ms（batch=1，主机 CPU，非 ARM） |
| 质量门 | OFF 100%、CLIP 100%、NOISE 23%、TONE **0%** 拒识；已知类误拒 3.38% |
| 真实 BPSK 迁移 | 30 个唯一物理 normal 帧：CNN **20/30 = 0.667**（train-01 10/10，train-02 **0/10 全判 QPSK**，独立 val-01 10/10）；特征基线 **30/30**；OFF 3/3 拒识 |
| 真实多类/板卡 | `NOT_RUN`：真实数据只有 BPSK 3MSym/s RECT 一种配置；无 ARM/板卡执行 |

一句话结论：**仿真域内 4 类模型达到 macro-F1 0.846，流程（生成→独立参考检查→训练→独立测试→离线推理）可复现；
但迁移到真实 BPSK 存在会话级系统失败（一个物理会话全部被判为 QPSK），根因未证实，
与未建模失真（现有环境规则标为 PULSE/SPARSE_AMPLITUDE_OUTLIERS）相关；特征基线在该真实数据上反而全对。
本阶段不据此宣称真实可用。**

## 2. 范围与边界

- 目标：60 MSPS、3 MSym/s、RECT、4096 复数点/帧的 BPSK/QPSK/QAM16/QAM64 离线识别。
- 非目标：正常/窄带/脉冲/复合环境诊断（两套标签不合并）；板卡部署；硬件操作；多速率/RRC；
  真实四类准确率；大范围超参数搜索。
- 未修改任何产品代码、冻结模型、环境规则、阈值；未连接板卡、零 RF、零新物理帧；
  真实 IQ 只读、未进入训练/验证。
- 仿真中明确未建模：IQ 不平衡、DC 偏置、接收滤波/RRC 失真、多径、相位噪声、AGC 瞬变、
  硬件频响、射频链路非线性。**这些项不得当作已校准。**

## 3. 环境与可复现

| 项目 | 值 |
|---|---|
| venv | `ml_training/.venv`（独立，未改其他对话环境） |
| Python / NumPy / torch / pytest | 3.12.10 / 2.5.4 / 2.14.1+cpu / 9.1.1 |
| 主机 | AMD64 Family 25 Model 68 Stepping 1, AuthenticAMD；16 逻辑核；Windows-11-10.0.26200 |
| 训练线程 | 4（`torch.set_num_threads(4)`） |
| Azure 16GB VM | `NOT_RUN`（无连接资料，本轮未使用；本机 CPU 完成） |
| 板卡 / ARM | `NOT_RUN` |

一条命令（工程根目录）：

```bat
ml_training\.venv\Scripts\python.exe ml_training\run.py all
```

实际执行序列：数据已存在则跳过生成 → 训练基线 + CNN → `evaluate_sim` + `real_transfer` →
`infer` 示例；摘要写 `ml_training/artifacts/pipeline-summary.json`。本次该命令退出码 0，
推理示例 `test-BPSK-snr-6-test-a-0000` 判为 BPSK（score 0.543）。

## 4. 数据生成（sim-v1）与独立参考检查

- 配置唯一源：`ml_training/config/first_round.json`，`config_sha256=c34ba443…`（重冻结后）。
- 数据集 `ml_training/datasets/sim-v1/`：`manifest_sha256=9f70a0c8…`，20 个 npz + manifest + generation.log，
  **114,886,349 B = 109.56 MiB**；生成耗时 **24.04 s**（wall 24.64 s）。
- 每帧 4096 复数采样，`.s16` little-endian int16 `(I,Q)`，signed 12-bit；60 MSPS、3 MSym/s、sps=20、
  RECT、207 符号；**每帧独立 i.i.d. 随机比特**（非 PRBS 周期相移/切片）；映射/位序与冻结产品
  `rf_iq_signal.map_bits_to_symbol` 一致（非 Gray 轴表）。
- 幅度归一化：接收仿真域**复数包络峰值** `max|I+jQ| = amplitude_codes`（在初相位/CFO 旋转之前；
  与产品 TX 的分量峰值语义有意不同，产品未改，理由见 `docs/CONTRACTS.md` §4）；
  c 系数解析值 BPSK 1.0 / QPSK 1.0 / QAM16 5/9 / QAM64 3/7（参考检查实测 1.0/1.0/0.55533/0.42881）。
- 损伤（固定顺序）：窗口起点 U[0,19] 样本 → 初相位 U[0,2π) → CFO U[-20,20] kHz → 复 AWGN
  （训练/验证 SNR U[-6,18] dB；test 12 点网格 [-6,-3,0,2,4,6,8,10,12,15,18,20] dB，每 cell 50 帧）→
  `np.rint` ties-to-even → 越界饱和 `clipped=True`。幅度含 5.5σ 安全上限。
- 划分：seed family 按 split 互斥；无增强副本、无周期相移跨组。计数：

| split | 帧数 | clipped |
|---|---|---|
| train | 4,800（4 类 × 4 会话 × 300） | 0 |
| val | 1,200（4 类 × 2 × 150） | 0 |
| test | 2,400（4 类 × 12 SNR × 50） | 0 |
| gate_cal | 400（OFF/NOISE/TONE/CLIP 各 100） | 112（CLIP 100 + NOISE 12） |
| gate_test | 400（同上） | 112（CLIP 100 + NOISE 12） |

- **独立参考检查**（`ml_training/data/reference_check.py`，实现不 import 生成器内部）：
  **PASS 22/22**，9,200/9,200 帧由记录种子/参数逐字节精确重建；`iq_sha256` 9,200/9,200；
  跨 split 重复 0、split 内重复 0；SNR 残差 8,400/8,400 在容差内（方差比 0.934–1.060，
  sigma 公式最大差 0.0）；86/86 位模式与产品映射一致；gate CLIP 200/200 饱和+旗标；
  旋转后分量峰值 ≤ 幅度上限（+1 code）。耗时 11.64 s。
- 生成器/参考检查 pytest：**36 passed**（含负例：篡改帧/换标签/跨 split 复制/摘要不符/削顶旗标错误
  必须失败）。
- 追溯：配置重冻结（仅训练 epoch 与门限网格变化）后 **20 个 npz 逐字节不变**，无混配置数据；
  `clipped` 是“溢出”旗标，rail 码可能不伴随溢出（9,200 帧中 1 例，位于 gate_test NOISE；
  preprocess 的 rail 检测仍会拒识，main splits 实测 0 例）。

## 5. 模型

### 5.1 可解释特征基线

- 8 维相位不变量（`modrec-features-v1`）：幅度峰度、幅度六阶矩、`|C20|`、`|C40|`、`|C42|`、
  低幅度占比、幅度直方图熵、符号间隔相位差熵；多项 Logistic 回归（36 参数）。
- L2 仅在 val 上选择：选中 `l2=1e-4`；val accuracy **0.6917**；训练 1.46 s。
- 仿真 test：accuracy **0.6900**，macro-F1 **0.6909**；主要错误是 QAM16↔QAM64 混淆
  （见 §6）。权重 `artifacts/baseline/model.json`（2,187 B，sha256 `caa82589…`）。

### 5.2 小型 CNN（4 类）

- 结构：冻结族 `tiny-iq-cnn-v1`（2→8→16→16 的 stride-2 Conv1d + ReLU + global average pool +
  linear head），head 改为 4 类；输入 `[2,4096]` float32，预处理 `dc-complex-rms-v1`（与板端
  `rf_cnn_runtime.preprocess` 逐元素一致，有测试）。
- **参数量 1,628；MACs 1,278,016/帧；`model.json` 48,971 B；`model.pt` 9,905 B。**
- 训练：seed 20261020；AdamW lr=1e-3、wd=1e-4、batch=64、4 线程；max epochs 80、patience 8；
  **实际跑满 80 epochs，best epoch 76（仅按 val macro-F1）**，val macro-F1 **0.8545**，
  val accuracy **0.855**；训练耗时 **140.0 s**（含基线/门限/导出的总流水线 167.2 s）。
- 训练预算决策（只用 val，不用 test）：初版预算 40 epochs 的 val macro-F1=0.700 且 best epoch=39
  顶到上限；同一 seed/数据/结构下 80 epochs 得到 0.8545（64 epoch 后进入平台），据此把首轮预算定为
  80 epochs。40ep 试验产物保存在 `artifacts/trials/formal-40ep/`，**不参与最终评估**。
- 导出与一致性：torch↔纯 NumPy 前向 200 帧 max |Δlogit| **5.72e-06**，argmax 一致率 **1.0**。
- 主机推理（batch=1，warmup 20，repeats 200）：NumPy runtime 中位 **1.045 ms**、均值 1.111 ms、
  P95 **1.710 ms**；torch 中位 0.459 ms、P95 0.837 ms。**主机 CPU，非 ARM/板卡。**
- 检查点/评估纪律：test 未参与任何训练或调参（metadata `test_split_used_for_tuning=false`），
  test 只在 `evaluate_sim.py` 中评估一次；真实数据未进入训练（`real_data_used=false`）。
- 最终产物 sha256：`model.json=25a65fb1…`、`model.pt=13829c19…`、`curves.json=4055e59f…`。

### 5.3 与现有板端推理的兼容差距（本阶段不修产品代码）

板端 runtime 的模型合同是固定 **6 类**（TONE/BPSK/QPSK/QAM16/QAM64/OTHER）与固定
`WEIGHT_SHAPES`（head `[6,16]`）并校验 `classes` 完全相等。本模型网络族、输入 `[2,4096]`、
`dc-complex-rms-v1` 预处理、权重 JSON 风格相同，但 4 类 head 与类表不同，**不能**被现行板端
runtime 直接加载；上板需要一次受控合同扩展（见 §11），本任务未改产品代码。

## 6. 独立仿真测试（test split，clipped=False 全部 2,400 帧）

协议：4 类 argmax 分类（质量门拒识不改变 argmax 4 类预测，gate 指标单列）；test 只在模型与门限
冻结后评估一次。数据 manifest sha256 `9f70a0c8…`。

### 6.1 混淆矩阵（CNN）

| true \ predicted | BPSK | QPSK | QAM16 | QAM64 | 行合计 |
|---|---|---|---|---|---|
| BPSK | **546** | 46 | 0 | 8 | 600 |
| QPSK | 45 | **541** | 2 | 12 | 600 |
| QAM16 | 6 | 14 | **464** | 116 | 600 |
| QAM64 | 2 | 5 | 112 | **481** | 600 |

### 6.2 每类指标

| class | support | CNN P | CNN R | CNN F1 | baseline P | baseline R | baseline F1 |
|---|---|---|---|---|---|---|---|
| BPSK | 600 | 0.9115 | 0.9100 | 0.9108 | 0.9270 | 0.9100 | 0.9184 |
| QPSK | 600 | 0.8927 | 0.9017 | 0.8972 | 0.7876 | 0.7850 | 0.7863 |
| QAM16 | 600 | 0.8028 | 0.7733 | 0.7878 | 0.5649 | 0.4933 | 0.5267 |
| QAM64 | 600 | 0.7796 | 0.8017 | 0.7905 | 0.4978 | 0.5717 | 0.5322 |

| summary | CNN | baseline | delta |
|---|---|---|---|
| accuracy | **0.8467** | 0.6900 | +0.1567 |
| macro-F1 | **0.8465** | 0.6909 | +0.1556 |
| 已知帧被门限拒识（计数） | 81 / 2400（3.38%） | 0（基线无门限） | — |
| 拒识计为错误的 accuracy（信息性） | 0.8358 | 0.6900 | +0.1458 |

主要错误：**QAM16↔QAM64 互相混淆**（116+112=228 帧，占全部 368 个错误的 62%），
BPSK↔QPSK 之间 45+46=91 帧（低 SNR 为主）；符合预期的星座阶数判别难点。
基线在 QAM16/64 上明显更弱（F1≈0.53），CNN 的收益主要来自 QAM 阶数。

### 6.3 逐 SNR（CNN，n=200/点）

| SNR dB | accuracy | macro-F1 | recall BPSK | QPSK | QAM16 | QAM64 |
|---|---|---|---|---|---|---|
| -6 | 0.4300 | 0.3967 | 0.52 | 0.44 | 0.08 | 0.68 |
| -3 | 0.6400 | 0.6379 | 0.72 | 0.74 | 0.48 | 0.62 |
| 0 | 0.7950 | 0.7946 | 0.92 | 0.92 | 0.72 | 0.62 |
| 2 | 0.7500 | 0.7464 | 0.96 | 0.82 | 0.74 | 0.48 |
| 4 | 0.8750 | 0.8742 | 0.98 | 0.90 | 0.90 | 0.72 |
| 6 | 0.9400 | 0.9399 | 0.98 | 1.00 | 0.94 | 0.84 |
| 8 | 0.9450 | 0.9450 | 0.94 | 1.00 | 0.90 | 0.94 |
| 10 | 0.9500 | 0.9500 | 1.00 | 1.00 | 0.92 | 0.88 |
| 12 | 0.9650 | 0.9650 | 0.98 | 1.00 | 0.96 | 0.92 |
| 15 | 0.9750 | 0.9750 | 0.96 | 1.00 | 0.94 | 1.00 |
| 18 | 0.9600 | 0.9600 | 0.98 | 1.00 | 0.90 | 0.96 |
| 20 | 0.9350 | 0.9346 | 0.98 | 1.00 | 0.80 | 0.96 |

观察：≥6 dB 后 accuracy 0.94–0.975；低 SNR（-6/-3 dB）主要靠 BPSK/QPSK 撑住，
QAM16 recall 在 -6 dB 仅 0.08（噪声下无法判阶数）；20 dB 点 QAM16 recall 回落到 0.80
（该 SNR 的 200 帧单次点估计，未据此回调模型/门限）。基线同口径的逐 SNR 见
`ml_training/evaluation/reports/sim_snr.md`（其 QAM16 在多数 SNR 上明显弱于 CNN）。

## 7. 质量门 / 拒识（gate_test，与 4 类严格分开）

门限（只用 val known + gate_cal 选择，gate_test 不参与）：`max_softmax=0.45`、`margin=0.0`、
`min_complex_rms=57.0596`（OFF gate_cal 95 分位 × 3 dB）。选择目标：
`(known_val_accuracy + mean_rejection_nonclip + clip_rejection)/3`，选中点：
known_val_accuracy 0.84、OFF 1.0、NOISE 0.21、TONE 0.0、CLIP 1.0。

| gate_test kind | n | clipped 旗标 | 拒识数 | 拒识率 | 被接受的错误标签 | 拒识原因 |
|---|---|---|---|---|---|---|
| OFF | 100 | 0 | 100 | **1.0000** | - | below_energy_floor:100 |
| NOISE | 100 | 12 | 23 | **0.2300** | QAM64:77 | clipping:13, confidence:10 |
| TONE | 100 | 0 | 0 | **0.0000** | BPSK:7, QAM16:37, QAM64:52, QPSK:4 | - |
| CLIP | 100 | 100 | 100 | **1.0000** | - | clipping:100 |

已知类误拒（test known frames）：BPSK 11/600、QPSK 22/600、QAM16 30/600、QAM64 18/600，
合计 **81/2400 = 3.38%**（全部为 confidence；能量地板未误拒任何已知 test 帧）。

结论与限制：节能门（OFF）与削顶门（CLIP）可靠；能量/置信度联合门对纯噪声只有 23% 拒识、
对单音（TONE）**完全无效**（单音被高置信判成 QAM64/QAM16）——单音/未知输入需要额外的
谱特征或 OOD 检测器，本阶段未实现（`NOT_RUN`）。gate_test 与 4 类矩阵严格隔离，互不混入。

## 8. 真实数据迁移检查（只读）

### 8.1 数据清单与真值

- 规范来源（只读）：`evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/{train-01,train-02,val-01}`
  （物理 loopback，60 MSPS；TX 配置 **BPSK / 3 MSym/s / RECT**，来自 `demod_result.submitted.config`；
  `trainval.truth=normal/off`；demod BER=0）。campaign4 为重复副本，按 `iq_sha256` 去重。
- 统计：canonical 计数 33 帧（30 normal + 3 off），重复映射 22 帧（每个只计一次），
  1 个非规范 campaign4 val-01 帧被排除；clipped 0。原始 evidence 未修改（测试内哈希核验）。
- 真值来源仅 TX 配置 + truth；**未**使用现有 demod/model/environment 输出当标签。
  现有 environment 规则标签只在失败分析里作为“相关性证据”引用。

### 8.2 结果（仅 BPSK；不能据此计算四类真实准确率）

| 会话 | 帧数 | CNN 判 BPSK | 识别率 | 特征基线 | 说明 |
|---|---|---|---|---|---|
| train-01 | 10 | 10 | 1.000 | 10/10 | 全部 BPSK，score 0.71–0.92 |
| train-02 | 10 | **0** | **0.000** | 10/10 | **全部被判 QPSK**（score 0.53–0.88） |
| val-01（未参与门限选择的独立真实会话） | 10 | 10 | 1.000 | 10/10 | score 0.69–0.90 |
| 合计 | 30 | **20** | **0.667** | **30/30=1.000** | 仅 BPSK 3M RECT 一种配置 |

OFF 帧：3/3 由 CNN 门限拒识（原因 `confidence`；真实 OFF 的 rms≈94 高于仿真能量地板 57.06，
因此不是能量门拒识，而是置信度门拒识）。真实 normal 帧无一被门限拒识（0/30；
train-02 的 10 个错判被高置信接受，**门限没有拦截**）。特征基线无门限，3 个 OFF 全部被其
argmax 接受（仅描述性，不参与 gate 指标）。

### 8.3 失败案例与研究（train-02 全部错判）

- 同一会话 10 帧全部判 QPSK，scores 0.528–0.876、margins 0.056–0.754（高置信错误）；
  其余两会话全对。失败是**会话级系统性的**，不是随机帧错误。
- 三会话物理差异（只读 metadata + 评估工具独立会话统计）：train-02 的 complex_rms 会话均值≈318.7
  （train-01≈566.0、val-01≈614.4，低约 5 dB），载波相位≈**63.20°**（另两会话 ≈-4.95°/1.75°），
  由载波相干度估计的会话 SNR≈**10.52 dB**（另两会话 ≈15.90/16.52 dB）。
- 现有环境规则（不是我们的标签）对 train-02 的 10 帧给出 **9×PULSE/SPARSE_AMPLITUDE_OUTLIERS
  + 1×UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT**；train-01 为 7 CLEAR+3 UNKNOWN，val-01 为
  9 CLEAR+1 UNKNOWN。即失败会话恰好是现有规则判为“稀疏幅度离群/脉冲”的失真会话。
- 受控诊断（主机 NumPy，非 ARM；不修改模型）：
  - 把正确分类的 train-01 帧绕原点旋转 +60° 后 CNN 判 QPSK（+68°/+90° 又判回 BPSK）；
  - 仅把幅度缩放到 train-02 的 rms 322 不改变结果（仍 BPSK）；
  - 用 train-01 + AWGN 匹配到 10.4 dB/63° **无法**复现 QPSK；
  - 因此：除相位外存在未建模失真；小 CNN 在约 55–65° 存在相位相关的判决边界弱点，
    而真实采集的 10 帧共享同一载波相位，使单帧弱点放大为整会话失败。
- **根因：NOT_ESTABLISHED**（未建模的接收/TX 失真，与现有环境规则的 PULSE 标注相关；
  不能断言具体硬件机理）。特征基线（累积量）在该失真下仍 30/30 正确，说明“仿真 CNN 迁移 +
  真实失真”存在域差；本阶段未用任何真实数据训练/微调。

### 8.4 真实数据结论与限制

- 只能报告真实 BPSK 识别表现与失败案例；**不能**计算四类真实总体准确率。
- 真实数据只有 BPSK/3MSym/s/RECT 一种配置；受干扰物理场景本轮未纳入（`NOT_RUN`）。
- 质量门未拒识上述错判（被当作 QPSK 高置信接受）：未知/失真输入的拒识能力不足，列入后续工作。
- `val-01` 作为独立真实会话（未参与任何阈值选择）10/10，说明失败不是“所有真实数据都失败”，
  而是与具体采集条件/失真相关。

## 9. 适用范围与结论

- 适用：60 MSPS / 3 MSym/s / RECT / 4096 点、以 AWGN + 幅度 + 初相位 + CFO + 窗口起点为损伤的
  仿真域 4 类识别；该域内 CNN macro-F1 0.846，≥6 dB 后 0.94–0.975。
- 不适用：真实多类准确率、真实硬件失真域、RRC/其它符号率、低 SNR 下 QAM 阶数细判、
  未知/单音输入的自动拒识（门限对 TONE 无效）、板卡性能。
- 迁移判断：真实 BPSK 在 2/3 物理会话上 CNN 可用（20/30），但存在会话级系统失败且门限不拒识 →
  **首轮不支持“仿真训练 CNN 可直接用于真实 BPSK”的结论**；特征基线在现有真实 BPSK 上 30/30，
  但因仿真域 QAM 能力弱（macro-F1 0.69）不能替代 4 类 CNN。后续需要真实失真建模/域适配
  （另立 TRAIN/VAL，绝不能用测试集）以及更强拒识。

## 10. 尚需的真实数据

1. 真实 QPSK/16QAM/64QAM 独立会话（每种至少 2–3 个会话，覆盖目标 SNR/幅度/CFO/失真范围）。
2. 真实 OFF/削顶/未知/干扰（窄带、脉冲、复合）独立会话，用于质量门与 OOD 拒识标定。
3. 硬件损伤标定数据（IQ 不平衡、DC、接收滤波/频响、非线性、AGC 瞬态），才能把 §2 的
   unmodeled 项转为 modeled；本阶段不得编造这些参数。
4. 独立真实测试集（按会话/采集批次与 TRAIN/VAL 隔离，测试集不参与任何调参/选门限）。

## 11. 最小板端验证方案（未执行，`NOT_RUN`）

1. **合同扩展（离线 review）**：在板端 runtime 增加 4 类模型变体（或 6 类合同的已知类子集映射），
   保持 `dc-complex-rms-v1`、`[2,4096]`、权重 JSON 与门限语义不变；不改变现有 6 类发布的默认路径，
   合同 diff 先独立审查。
2. **产物一致性**：板端加载的 `model.json` sha256 与本报告一致（`25a65fb1…`）；同输入的主机/ARM
   logit 一致性（建议 max |Δlogit| ≤ 1e-4 或沿用项目量化判据）与 argmax 一致率 100%。
3. **资源与时延**：ARM 100–200 次完整推理 P50/P95 与 RSS；若沿用现有 ≤300 ms P95 预算需先确认；
   记录首次加载时间。主机 NumPy 1.045/1.710 ms 只是参考，**不是 ARM 数字**。
4. **真实回放**：用未参与训练的真实会话（如 campaign5 `val-01`）逐帧回放 BPSK，报告识别/拒识；
   OFF 必须被能量门拒识；削顶帧必须被 clipping 门拒识；同时复测 `train-02` 类失真会话作为负例。
5. **回归**：现有 6 类路径与其它发布功能回归；失败即回滚，不混合两套标签。

## 12. NOT_RUN / 未取得

`ARM/板卡 latency & parity`、`板卡部署`、`真实四类准确率`、`Azure VM 训练`、
`RRC 与其它符号率`、`硬件损伤建模（IQ 不平衡/DC/滤波/多径/相位噪声/AGC/频响/非线性）`、
`真实 TRAIN/VAL 域适配`、`大规模超参数搜索`、`单音/未知输入的可靠拒识`、
`真实干扰物理场景`、`真实数据的时间/会话级置信区间（单次点估计）`。

## 13. 产物索引与复现

- 配置：`ml_training/config/first_round.json`（`c34ba443…`）
- 数据：`ml_training/datasets/sim-v1/`（manifest `9f70a0c8…`；20 npz；generation.log）
- 独立参考检查：`ml_training/data/reports/reference-check.json` / `.md`（22/22 PASS）
- 数据生成报告：`ml_training/data/reports/generation-report.md`（帧数/字节/clipped/pytest/耗时）
- 模型：`ml_training/artifacts/cnn/{model.json,model.pt,metadata.json,curves.json,thresholds.json}`
- 特征基线：`ml_training/artifacts/baseline/{model.json,metadata.json}`
- 仿真评估：`ml_training/evaluation/reports/{sim_metrics.json,sim_confusion.md,sim_snr.md,gate.md}`
- 真实迁移：`ml_training/evaluation/reports/{real_inventory.json,real_bpsk.md,real_failures.json}`
- 流水线摘要与推理示例：`ml_training/artifacts/pipeline-summary.json`、`infer-sample.s16`
- 40ep val-only 决策备份：`ml_training/artifacts/trials/formal-40ep/`

复现：

```bat
ml_training\.venv\Scripts\python.exe -m pytest ml_training\tests ml_training\data\tests ml_training\evaluation\tests -q
ml_training\.venv\Scripts\python.exe ml_training\data\reference_check.py --dataset ml_training\datasets\sim-v1
ml_training\.venv\Scripts\python.exe ml_training\run.py all
```

本报告对应的最终回归：合并 pytest **99 passed, 0 failed, 0 skipped**（Lead 22 + 数据生成 36 +
评估 41；28.08 s，本机）。
