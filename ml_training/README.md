# ml_training — 轻量调制识别离线训练（首轮）

本目录是**独立的新目录**，只做离线数字仿真数据 + 训练 + 评估 + 离线推理，用于在
当前板卡接收条件（60 MSPS、3 MSym/s、RECT、4096 点/帧、LO 80 MHz）下识别
**BPSK / QPSK / 16QAM / 64QAM** 四类调制。

边界（务必先读）：

- 这是调制识别，**不是**正常/窄带/脉冲/复合环境诊断；两套标签不合并。
- 不修改任何现有产品代码、冻结模型、环境规则、阈值；不改板端推理合同（本阶段只交付离线模型与差距说明）。
- 不包含板卡部署或硬件操作；本轮零 RF、零新物理帧、未连接板卡。
- 已有真实 IQ 只读用于迁移评估，**不参与训练**；仿真准确率不称为板卡/实测准确率。
- 首轮只覆盖 60 MSPS / 3 MSym/s / RECT；RRC、2/5 MSym/s、IQ 不平衡、DC 偏置、接收滤波、多径等均未建模或未覆盖。

## 一条命令（全流程）

在工程根目录 `<LOCAL_PATH>` 用本任务独立环境执行：

```bat
ml_training\.venv\Scripts\python.exe ml_training\run.py all
```

它依次执行：生成数据集（已存在则跳过）→ 训练特征基线 + TinyIQCNN → 独立仿真测试评估
+ 真实 BPSK 只读迁移评估 → 离线推理示例，并写 `ml_training/artifacts/pipeline-summary.json`。
单独步骤：

```bat
ml_training\.venv\Scripts\python.exe ml_training\run.py generate --variant smoke
ml_training\.venv\Scripts\python.exe ml_training\run.py train --variant smoke
ml_training\.venv\Scripts\python.exe ml_training\run.py evaluate
ml_training\.venv\Scripts\python.exe ml_training\run.py infer --iq <frame.s16>
```

（Linux/其它 shell 用 `ml_training/.venv/bin/python ml_training/run.py all`。正式数据生成也可单独
`python ml_training/data/generate.py --config ml_training/config/first_round.json --variant formal --out ml_training/datasets/sim-v1`。）

## 环境

- 独立 venv：`ml_training/.venv`（Python 3.12.10 + numpy 2.5.4 + torch 2.14.1+cpu + pytest 9.1.1）。
  不修改其他对话正在使用的 Python 环境。
- 只在本机 CPU 训练/推理；**没有**使用 Azure 虚拟机（无连接资料，`NOT_RUN`），
  也没有任何 ARM/板卡数字。
- 所有随机源（Python/NumPy/torch/DataLoader）都有显式种子，写入模型 metadata。

## 目录与 owner

| 路径 | owner | 内容 |
|---|---|---|
| `docs/CONTRACTS.md` | Lead | 冻结的跨组接口契约（数据/模型/评估字段与语义） |
| `config/first_round.json` | Lead | 唯一配置源（波形、损伤、划分、训练、门限网格） |
| `common/` | Lead | `dc-complex-rms-v1` 预处理、质量门、IQ IO、manifest/npz 加载 |
| `data/generate.py` | data-gen | 仿真数据生成器 |
| `data/reference_check.py` | data-gen | 独立参考实现：逐帧从种子/参数精确重建 + 映射/SNR/去重/摘要检查 |
| `datasets/sim-v1/`（生成产物） | data-gen | `manifest.json` + `frames/*.npz` + `gate/*.npz` |
| `model/` | Lead | 特征提取、特征基线、TinyIQCNN、纯 NumPy 离线推理 runtime |
| `training/train.py` | Lead | 特征基线 + CNN 训练、val-only 选点/门限、导出与 host 计时 |
| `evaluation/evaluate_sim.py` | eval-tools | 独立仿真测试：4×4 混淆矩阵、逐 SNR、gate_test 拒识 |
| `evaluation/real_transfer.py` | eval-tools | 真实 IQ 只读清单与 BPSK 迁移评估、失败案例 |
| `artifacts/cnn/` | Lead | `model.json`（离线权重）、`model.pt`、`metadata.json`、`curves.json`、`thresholds.json` |
| `artifacts/baseline/` | Lead | 特征基线权重与 metadata |
| `evaluation/reports/` | eval-tools | `sim_metrics.json`、`sim_confusion.md`、`sim_snr.md`、`gate.md`、`real_bpsk.md` 等 |
| `reports/first-round-report.md` | Lead | 首轮完整报告（数字、混淆矩阵、失败案例、NOT_RUN、板端最小验证方案） |

## 数据契约（摘要，完整见 `docs/CONTRACTS.md`）

- 每帧：4096 个复数采样，`.s16` int16 little-endian `(I,Q)`，signed 12-bit `[-2048, 2047]`，
  与板端真实捕获格式一致（16384 字节/帧）。
- 波形：60 MSPS、3 MSym/s、`sps=20`、RECT（矩形保持）、207 符号。
- **每帧独立 i.i.d. 随机比特**（`default_rng(symbol_seed).integers(0,2)`），
  **不是**同一段 PRBS 的周期相移/切片；映射/位序与冻结产品契约
  `pynq_overlay/sw/rf_iq_signal.py:map_bits_to_symbol` 一致（非 Gray；QAM16 轴表 `(3,1,-3,-1)`，
  QAM64 轴表 `(7,5,1,3,-7,-5,-1,-3)`）。
- 幅度归一化（接收仿真域）：帧内**复数包络峰值** `max|I+jQ| = amplitude_codes`，归一化在
  接收相位/CFO 旋转之前完成；这样旋转不会把分量顶到 `√2·A` 而造成与接收条件无关的人为削顶。
  注意产品 TX `generate_digital_frame` 用的是分量峰值 `max(max|I|,max|Q|)`，本仿真为接收域
  安全而有意不同，产品实现未修改（报告如实记录该差异）。
- 接收损伤（按契约顺序）：窗口起点 `U[0,19]` 样本 → 初相位 `U[0,2π)` → CFO `U[-20,20] kHz`
  → 复 AWGN（逐帧 SNR，`U[-6,18] dB` 训练/验证，测试用 12 点固定网格）→ `np.rint` ties-to-even
  量化；越界饱和存储并标记 `clipped=True`（质量门单列，不静默削顶）。
- 划分按**种子族隔离**：train/val/test 的 seed family 互斥，帧独立生成，无增强副本；
  同一帧 IQ 不允许跨 split 重复（生成器与 Lead 契约测试都检查）。
- 质量门总体（**不是**训练类、不并入 4 类混淆矩阵）：`OFF`（背景）、`NOISE`、`TONE`、`CLIP`；
  `gate_cal` 只用于门限选择，`gate_test` 只用于最终拒识指标。
- 未建模项明确声明：IQ 不平衡、DC 偏置、接收滤波/RRC、多径、相位噪声、AGC 瞬变、
  硬件频响、射频链路非线性。

## 模型与预处理

- 预处理 `dc-complex-rms-v1`：逐分量去 DC → 复数 RMS 归一化 → `[2, 4096]` float32；
  与板端 `pynq_overlay/sw/rf_cnn_runtime.py:preprocess` 逐元素一致（有测试对比）。
- 特征基线（可解释，`modrec-features-v1`）：幅度峰度/六阶矩、`|C20|/|C40|/|C42|`、
  低幅度占比、幅度直方图熵、符号间隔相位差熵，8 维 + 多项 Logistic 回归（L2 只用 val 选）。
- 小型 CNN：沿用冻结的 `tiny-iq-cnn-v1` 结构（`2→8→16→16` stride-2 conv1d + ReLU +
  global average pool + linear head），只把 head 改为 **4 类**：
  **1,628 参数、1,278,016 MACs/帧**、输入 `[2,4096]` float32。
- 导出：`artifacts/cnn/model.json`（权重 JSON，约 50 KB）+ `model.pt`；纯 NumPy 离线 runtime
  `ml_training/model/numpy_runtime.py` 与 torch 前向对齐（max |Δlogit| 记录在 metadata，
  实测 `1.49e-08`，argmax 一致）；同一 runtime 语义（gate/energy floor/softmax/margin）与板端一致。
- 标签顺序冻结：`["BPSK","QPSK","QAM16","QAM64"]`，`UNKNOWN` 只作拒识输出。
- 最佳检查点只按 **val macro-F1** 选择；独立 test 只在 `evaluate_sim.py` 中读一次。

### 与现有板端推理的兼容差距（本阶段不修产品代码）

现有板端 runtime 的模型合同是固定 **6 类**（TONE/BPSK/QPSK/QAM16/QAM64/OTHER）+
固定 `WEIGHT_SHAPES`（head `[6,16]`）。本模型的网络族、输入 `[2,4096]`、`dc-complex-rms-v1`
预处理、权重 JSON 形状风格都相同，但 4 类 head 与类表不同，**不能**被现行板端 runtime
直接加载；上板需要一次受控的合同扩展（新增 4 类变体或把该模型并入 6 类合同的已知类子集），
不在本任务范围内。详见报告"最小板端验证方案"。

## 复现与测试

```bat
ml_training\.venv\Scripts\python.exe -m pytest ml_training\tests ml_training\data\tests ml_training\evaluation\tests -q
ml_training\.venv\Scripts\python.exe ml_training\data\reference_check.py --dataset ml_training\datasets\sim-v1
```

本机最终回归：**99 passed, 0 failed, 0 skipped**（Lead 22 + 数据生成 36 + 评估 41）。

## 结果

首轮实际数字（完整表格、混淆矩阵、失败案例、NOT_RUN 与板端最小验证方案）见
**[reports/first-round-report.md](reports/first-round-report.md)**。要点：

| 项目 | 结果 |
|---|---|
| 数据集 sim-v1 | 9,200 帧 / 109.56 MiB；train 4,800 / val 1,200 / test 2,400 / gate 各 400；主数据 clipped 0 |
| 独立参考检查 | PASS 22/22，9,200/9,200 帧逐字节重建，跨/内 split 重复 IQ 0/0 |
| 特征基线 | 36 参数；仿真 test accuracy 0.6900 / macro-F1 0.6909 |
| 小型 CNN | **1,628 参数 / 1,278,016 MACs**；`model.json` 47.8 KiB；仿真 test accuracy **0.8467** / macro-F1 **0.8465** |
| 训练 | 80 epochs（val-only，best epoch 76，val macro-F1 0.8545）；主机 CPU 训练 140 s |
| 主机推理（非 ARM） | NumPy runtime 中位 1.045 ms / P95 1.710 ms（batch=1） |
| 质量门（gate_test） | OFF 100% / CLIP 100% / NOISE 23% / TONE 0% 拒识；已知类误拒 3.38% |
| 真实 BPSK 迁移（只读） | 30 个唯一物理 normal 帧：CNN 20/30（train-01 10/10、train-02 **0/10 全判 QPSK**、独立 val-01 10/10）；特征基线 30/30；根因 `NOT_ESTABLISHED` |
| 未运行 | ARM/板卡、真实四类准确率、Azure VM、RRC/其它速率、硬件损伤建模、真实域适配 —— 全部 `NOT_RUN` |

注意：仿真准确率 **不是** 板卡/实测准确率；主机 CPU 耗时 **不是** ARM 性能；真实数据只有
BPSK 3MSym/s RECT 一种配置，不能据此计算四类真实总体准确率。

## 第二轮（round2）离线改进

第二轮只写 `ml_training/round2/`、不重训、不加模型参数，针对首轮两个问题做小规模对照：
A. 真实 `train-02` 10 帧 BPSK 全部高置信误判 QPSK；B. 单音拒识 0%、噪声拒识 23%。

一条命令（生成新种子族数据 → dev 标定 → 新 test 最终评估）：

```bat
ml_training\.venv\Scripts\python.exe ml_training\round2\run.py all
```

| 项目 | 改进前 | 改进后 |
|---|---|---|
| 真实 `train-02`（dev replay） | 0/10 | **10/10** |
| 独立真实会话 campaign3（未参与开发） | 0/10 | **10/10** |
| 独立真实 OFF 帧 | 被判 QAM64 接受 | **拒识（noise_like）** |
| 新仿真 test（新种子族，未参与调参） | acc 0.8471 / macro-F1 0.8469 | **0.8654 / 0.8652** |
| gate_test TONE / NOISE 拒识 | 0% / 20% | **100% / 100%** |
| 已知类误拒（新 test） | 84（3.50%） | 98（4.08%，增量 14 帧全在 -6 dB） |
| 参数 / 主机推理 | 1,628 / 1.39 ms 中位 | CNN 不变 + 36 参数基线复用（0 新学习参数）/ 3.39 ms |

改进内容：冻结 CNN 仍是四类主分类器；当 top-1 为 BPSK/QPSK 时由冻结的 36 参数可解释基线
仲裁（阈值仅用 round-2 val 标定）；在冻结首轮门之上增加两条可解释频谱拒识规则
（`top5_frac` 单音、`band_frac_3m` 噪声，阈值仅用 round-2 gate_cal + val 标定）。
细节、被否决候选（相位 TTA/离群点等）、逐 SNR、误拒 trade-off 与下一步真实数据需求见
**[round2/reports/round2-report.md](round2/reports/round2-report.md)**、运行说明
**[round2/README.md](round2/README.md)**；首轮产物 SHA-256 由 round2 测试固定并校验未变。

### 独立 ARM 回放包（仅软件包，未连板）

`ml_training/arm_probe/` 是冻结第二轮方案的自包含最小回放包：CNN/基线/规则冻结拷贝 +
43 帧输入清单（真实 BPSK、既往失败 BPSK、合成四类、OFF/单音/噪声/削顶、窄带压力）+
主机期望输出 + 纯 NumPy 运行入口 + 有界 ARM 计时/RSS 探针与清理命令。主机干净目录回放
`run_replay.py --check` 已 **PASS**（包内 runtime 与项目冻结 runtime 43 帧 0 mismatch），
板端 Python 3.8 / NumPy 1.17 静态兼容检查 PASS。板卡操作者已执行 ARM 回放：
**43 帧 0 mismatch、P95 63.24 ms、峰值 22.6 MiB**（证据
`evidence/rf-teaching/s2-46db-arm-replay-2026-10-21/result/`；纯 Python/NumPy 探针，
非产品端到端资格）。入口见 **[arm_probe/README.md](arm_probe/README.md)**。

### 四类集成适配原型（仅 `ml_training`，未改产品运行时）

`ml_training/integration_candidate/` 提供可运行的 `ModRec4Adapter`：保留冻结 CNN/基线/规则，
把四类输出与现有六类合同（`TONE/BPSK/QPSK/QAM16/QAM64/OTHER`，head `[6,16]`）显式分开，
**不伪造六类概率**；调制类别/接受-拒识/原因独立输出，environment/demod/six-class 原样透传，
不覆盖环境与解调结果。原型与冻结回放包同输入 **43/43 一致（0 mismatch）**；ARM 已执行事实
同步自结果证据（只读，不重复回放）。实际代码、测试、依赖与产品侧最小接入差分见
**[integration_candidate/README.md](integration_candidate/README.md)**。

### 真实四类数据验收工具（离线，未采集/未连设备）

`ml_training/real_campaign/` 提供一条命令的真实数据导入/去重/冻结评估工具：只读已有真实
IQ，按完整会话分组并检查重复 IQ/跨 split 泄漏；调制真值只取显式信号配置或独立实验记录，
模型/环境标签不得反填；冻结第二轮方案分别输出干净分类、质量拒识与干扰压力，缺失真实
QPSK/QAM16/QAM64 显式 `NOT_RUN`，不伪造四类总体准确率。当前真实 BPSK 可演示；合成夹具明确
标记且单独评估。入口、报告、下一批字段与两条采集路线核对见
**[real_campaign/README.md](real_campaign/README.md)**。
