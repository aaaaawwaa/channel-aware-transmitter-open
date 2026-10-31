# 候选 A 的 BPSK 回退定位与工程交接

状态：**定位完成；A 可支持“有限范围开发版试部署”，但不支持生产冻结/广泛发布。**
只读原模型与 A；未改 A 字节、未训练、未采集、未部署、未改阈值/输入域/坏例。task80 只作独立诊断，
不能据此宣称总体准确率 100%。

## 0 结论

- **A 相对原模型新增 BPSK 错误**：
  - VAL：**26 / 300 BPSK**（8.7%）；原模型正确、A 错误；A 同时修复原模型 3 个 BPSK 错误。
  - 已知比较 test：**36 / 600 BPSK**（6.0%）；A 同时修复 6 个。
  - 错误主体是 **CNN 把 BPSK 判成 QPSK**（VAL 23/26，test 26/36），少量 QAM64、UNKNOWN；
    **不是预处理/能量/频谱/阈值拒绝**。
- **路径分解**：新增错误中 **25/26（VAL）、30/36（test）止于 `cnn_top1`**；其余为
  `confidence` 拒识；**没有** `preprocess`、`energy_floor`、`spectral`、`margin` 或
  `arbitration` 路径错误。原模型在这些帧上多数直接 CNN top1 BPSK。
- **回退主要发生在其他条件**：低 SNR（−6…0 dB）、低幅度（150–300 codes）、高 |CFO|
  （>10 kHz）；SNR ≥6 dB 时 VAL 0/150、test 1/350，幅度 ≥600 codes 时 VAL 1/99、
  test 1/204。**当前 task80 BPSK**（amp60、CFO −19 Hz、carrier_sync=true、
  coherence≈0.963）A 正确，且是由仲裁从 CNN QPSK 拉回 BPSK（baseline margin≈1.0）。
- **明确建议**：**支持有限范围开发版试部署**——仅限当前 46 dB/amp60/高相干 BPSK 工作点，
  保留原模型回退/监测，不做生产冻结；**BPSK 低 SNR/低幅度/高 CFO 边界仍需具体修复**
  后才能扩大/正式发布。
- task80 A 为 **ON 8/8、OFF 2/2**，本批最佳；但这是 10 帧独立诊断，不是总体准确率。

## 1 证据与入口

```bat
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\candidate_a_bpsk_regression\analyze.py
```

输出：[bpsk_regression.json](results/bpsk_regression.json)（SHA
`6799ce8fdfa8fef12279aef684a3d8acb3c21dc52b848849fe94d7f70d2f6d44`）。
VAL 来自 `sim-v1/val`；已知比较来自 `sim-v1/test`（不作为独立验收）；task80 来自
`eval-task80/seed_validation_result.json`。所有帧保留，没有删除坏例或缩小输入域。

## 2 A 相对原模型新增 BPSK 错误

| 数据 | BPSK 帧 | 新增 A 错误 | A 修复原错误 | A BPSK recall | 原模型 BPSK recall |
|---|---:|---:|---:|---:|---:|
| VAL | 300 | **26（8.7%）** | 3 | 0.8733 | 0.9500 |
| 已知 test | 600 | **36（6.0%）** | 6 | 0.8800 | 0.9300 |

新增错误最终类别：

| 数据 | QPSK | QAM64 | QAM16 | UNKNOWN |
|---|---:|---:|---:|---:|
| VAL | **23** | 2 | 0 | 1 |
| test | **26** | 3 | 1 | 6 |

## 3 路径分解：CNN / 仲裁 / 门控

### 3.1 新增错误的停止阶段

| 阶段 | VAL | test |
|---|---:|---:|
| `preprocess` | 0 | 0 |
| `energy_floor` | 0 | 0 |
| `spectral` | 0 | 0 |
| `confidence` | 1 | 6 |
| `margin` | 0 | 0 |
| `arbitration` | 0 | 0 |
| `cnn_top1`（接受错误类） | **25** | **30** |

结论：**不是门控/阈值/预处理问题**；主要是 A 的 CNN 头在 BPSK/QPSK 边界上偏向 QPSK。
仅 1/6 个错误被 confidence 拒识，其余以错误类 accepted。

### 3.2 仲裁路径

- 新增错误中 A 的 `arbitration_applied = 0`。
- A 的 baseline top1 为 BPSK 的帧：VAL 11/26、test 12/36；但仲裁没有救回：
  - 这些帧的 baseline BPSK−QPSK margin 多在阈值 0.3 以下；
  - 少数 margin >0.3 的帧，CNN top1 是 QAM64/QAM16（仲裁只处理 CNN top1 为 BPSK/QPSK）。
- 对照当前 task80 BPSK：
  - A CNN top1 = QPSK（0.798），baseline = BPSK（≈1.0），margin ≈ **0.999999**，
    `arbitration_applied=true`，最终 BPSK；原模型 CNN 直接 BPSK。
  - 说明当前高相干 BPSK 由仲裁兜底；低 SNR/低幅度时 baseline margin 变小，兜底失效。

### 3.3 A 的 BPSK 路径整体

| 路径 | VAL A | VAL original | test A | test original |
|---|---:|---:|---:|---:|
| `cnn_top1` | 249 | 283 | 507 | 551 |
| `arbitration` | 48 | 11 | 74 | 32 |
| `confidence` | 3 | 6 | 12 | 10 |
| `spectral` | 0 | 0 | 7 | 7 |

A 更依赖仲裁；当前 task80 BPSK 正是仲裁救回。

## 4 SNR / CFO / 幅度分层

`sim-v1` 有逐帧 `snr_db`、`cfo_hz`、`amplitude_codes`、`window_offset`、`phase_rad`；
task80 没有 SNR ground truth（缺失，不补造）。新增 A BPSK 错误率：

### SNR

| SNR 区间 | VAL 错误/总 | VAL 率 | test 错误/总 | test 率 |
|---|---:|---:|---:|---:|
| <−6 dB | 0/0 | — | 0/0 | — |
| −6…0 dB | **23/67** | **34.3%** | **27/100** | **27.0%** |
| 0…6 dB | 3/83 | 3.6% | 8/150 | 5.3% |
| 6…12 dB | 0/80 | 0% | 0/150 | 0% |
| ≥12 dB | 0/70 | 0% | 1/200 | 0.5% |

### |CFO|

| |CFO| | VAL 错误/总 | VAL 率 | test 错误/总 | test 率 |
|---|---:|---:|---:|---:|
| <2 kHz | 1/29 | 3.4% | 3/63 | 4.8% |
| 2–10 kHz | 7/123 | 5.7% | 10/241 | 4.1% |
| >10 kHz | **18/148** | **12.2%** | **23/296** | **7.8%** |

### 幅度 codes

| amplitude | VAL 错误/总 | VAL 率 | test 错误/总 | test 率 |
|---|---:|---:|---:|---:|
| <150 | 0/0 | — | 0/0 | — |
| 150–300 | **13/67** | **19.4%** | **14/123** | **11.4%** |
| 300–600 | 12/134 | 9.0% | 21/273 | 7.7% |
| ≥600 | 1/99 | 1.0% | 1/204 | 0.5% |

**解释**：新增错误明显集中在低 SNR、低幅度、高 |CFO|；SNR ≥6 dB 几乎为零，幅度 ≥600
很低。**但 sim-v1 幅度范围是 150–1200 codes，当前 amp60 不在其中**，不能把当前 amp60
外推为“已被覆盖”；task80 BPSK 只有 1 帧且无 SNR ground truth。

## 5 当前工作条件 vs 其他条件

当前 task80 BPSK 帧（独立诊断）：

- amp60、BPSK seed2、CFO −19.1 Hz、`carrier_sync=true`、coherence≈0.963；
- 原模型 BPSK；A 最终 BPSK（CNN QPSK，baseline BPSK margin≈1.0，仲裁救回）；
- **没有 A 新增 BPSK 错误**。
- task80 A 总：ON 8/8、OFF 2/2；原模型/C0/C1 有 QAM16 或 OFF 误接受。该 10 帧不能宣称
  总体准确率 100%。

判断：

- 回退**没有在当前 task80 BPSK 工作点显现**，但主要集中低 SNR/低幅度/高 CFO；
- 当前工作条件仅 1 个 BPSK 帧，且 sim-v1 缺少 amp60 覆盖、task80 无 SNR ground truth，
  不能据此宣称当前条件已被充分覆盖；
- 因此：**有限范围开发版试部署可考虑**（当前 46 dB/amp60/高相干 BPSK 工作点），
  **生产冻结/广泛发布仍需具体 BPSK 修复**。

## 6 建议

### 支持有限范围开发版试部署（有证据）

- A 在 task80 当前条件 ON 8/8、OFF 2/2；BPSK 由仲裁兜底；
- A 修复 QAM16（task80 3/3），而原模型/C1 QAM16 2/3 错、C0 OFF-last 误接受；
- 保留原模型回退/监测，不改变输入域，不删除坏例，不降低/放宽阈值。

### 仍需具体修复（不可直接生产冻结）

- 低 SNR（−6…0 dB）、低幅度（150–300 codes）、高 |CFO|（>10 kHz）的 BPSK 仍有
  26/300（VAL）、36/600（test）新增错误；
- 修复方向：改善 A 的 **CNN BPSK/QPSK 边界**（低 SNR/低幅度/高 CFO 增广或重训），
  或设计 BPSK/QPSK 专用、经验证的仲裁策略；**不要**仅降低仲裁阈值或门限；
- 当前 task80 只有 1 个 BPSK 帧；扩大试部署前需更多当前条件 BPSK 帧验证，且新批次仍作
  独立诊断，不能与训练集混合。

## 7 工程交接：A 模型/规则/预处理/哈希/限制

| 项 | 值 |
|---|---|
| 状态 | `experimental`；**不是批准发布/未部署** |
| 模型目录 | `ml_training/qam16_domain_repair/results/candidate_A/` |
| model.json | `.../candidate_A/model.json`；SHA `b840f9dbaebc5db855ef1535d091e1f1583e12368ca62742f517a8d38123a28e` |
| rules.json | `.../candidate_A/rules.json`；SHA `10a0bef4d237818eaa39dcd4e3048466de60017dbf0545c02bcbf405da2cdf64` |
| metadata.json | `.../candidate_A/metadata.json`；SHA `da1c7ec8ad504f7c632146090bd1604420ff91aa086f62a17d1edd107b36b8d1` |
| model.pt | SHA `2521f445f2a2ce8e681713a2d3653f43378bda9c9cd6428ce07f1409ae6bad0f` |
| checkpoint_best | SHA `32a1605d04fee85eeeb9b79d0507f51c92244d288bed3253374ef4fcd588ac74` |
| 网络 | `tiny-iq-cnn-v1`，`modrec4`，1628 params，1,278,016 MACs |
| 输入 | `[2,4096]`；预处理 `dc-complex-rms-v1`（原样） |
| 类别顺序 | `BPSK, QPSK, QAM16, QAM64` |
| 决策阈值 | `max_softmax=0.45, margin=0.0, min_complex_rms=57.05962767972466`（原样） |
| baseline | `ml_training/artifacts/baseline/model.json`，SHA `caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014` |
| 仲裁 | BPSK/QPSK baseline 特征守卫，threshold 0.3（原样） |
| 频谱规则 | 原冻结规则，未重校准 |
| 训练增广 | train-only 共轭/频谱反转；4800→9600 帧；best epoch 65；val macro 0.8454 |
| 加载方式 | `Round2Predictor(rules_path=".../candidate_A/rules.json")` |

### 已知限制

- BPSK 回归集中在低 SNR/低幅度/高 CFO；当前 task80 仅 1 个 BPSK 帧，不能证明当前条件全面安全。
- sim-v1 幅度范围 150–1200 codes，不包含当前 amp60；task80 没有 SNR ground truth。
- task80 为独立诊断 10 帧；A 8/8 ON + 2/2 OFF 不代表总体准确率 100%，也不是发布验收。
- A 依赖 baseline 仲裁救 BPSK；低 SNR 时 baseline margin 变弱，CNN QPSK 错误直接接受。
- 不修改 A 字节；不训练/采集/部署；任何扩大试用需主线独立候选审查。

## 8 证据文件

- [analyze.py](analyze.py)
- [bpsk_regression.json](results/bpsk_regression.json)
- task80 eval：`evidence/rf-teaching/s2-46db-fourclass-seed-validation-2026-10-22/eval-task80/seed_validation_result.json`
  （SHA `864c22aed797b7b629a3652a1b3142a3b8eb71e635f21ed0155aa42e1510ac3b`）
- A 模型/规则/metadata/checkpoint 路径见上表。
