# round2 — 第二轮离线改进（真实 BPSK 误判 + 单音/噪声拒识）

本轮仍**只写 `ml_training/`**：不连设备、不部署、不改产品运行时；首轮的模型、数据、报告和失败
结果全部保留且只读（`ml_training/round2/tests/test_round2.py` 用 SHA-256 固定并校验首轮关键产物）。

## 结论（详见 [reports/round2-report.md](reports/round2-report.md)）

| 项目 | 首轮/改进前 | 本轮改进后 |
|---|---|---|
| 真实 BPSK `train-02`（dev replay） | 0/10（全部高置信判 QPSK） | **10/10** |
| 独立真实会话 campaign3（未参与开发） | 0/10 | **10/10** |
| 独立真实 OFF 帧（同一会话） | 被判 QAM64 接受 | **拒识**（noise_like） |
| 独立仿真 test（新种子族，未参与调参） | acc 0.8471 / macro-F1 0.8469 | **0.8654 / 0.8652** |
| gate_test TONE 拒识 | 0% | **100%** |
| gate_test NOISE 拒识 | 20% | **100%** |
| 已知类误拒（新 test 2400 帧） | 84（3.50%） | 98（4.08%）；增量 14 帧全在 -6 dB |
| 模型参数 | 1,628（CNN） | CNN 不变；复用 36 参数基线；总 1,664，**0 个新学习参数** |
| 主机推理（batch=1，非 ARM） | 1.22–1.39 ms 中位 / 1.79–2.18 ms P95 | 3.39–3.60 ms / 4.79–6.28 ms（两次完整运行） |

## 一条命令（从工程根目录）

```bat
ml_training\.venv\Scripts\python.exe ml_training\round2\run.py all
```

依次：生成 `sim-r2`（已存在则跳过；生成器包装器会自动备份/还原首轮 `generation-report.md`）
→ 在 `val` + `gate_cal`（dev）上标定规则 → 在**未参与调参**的 `test` + `gate_test` 上做一次
最终评估，并写出 `evaluation/reports/`。

其它命令：

```bat
ml_training\.venv\Scripts\python.exe ml_training\round2\run.py generate [--force]
ml_training\.venv\Scripts\python.exe ml_training\round2\run.py calibrate
ml_training\.venv\Scripts\python.exe ml_training\round2\run.py evaluate
ml_training\.venv\Scripts\python.exe -m pytest ml_training\round2\tests -q
```

## 改进内容（两项，均不训练、不加参数）

1. **BPSK/QPSK 特征仲裁（针对 A：train-02 全判 QPSK）**
   `round2/predictor.py`：冻结首轮 CNN 仍是四类主分类器；当 CNN top-1 是 BPSK/QPSK 时，
   用冻结的 36 参数可解释基线（8 维相位不变量 + Logistic）在 BPSK 与 QPSK 之间仲裁，
   条件是基线 `|p_BPSK - p_QPSK| > 0.30`。阈值只在 **round-2 `val`** 上按四类准确率选取。
2. **可解释频谱拒识（针对 B：TONE 0%、NOISE 23%）**
   `round2/features.py` + 规则：`top5_frac > 0.5273` → `tone_like`；
   `band_frac_3m < 0.2516` → `noise_like`；叠加在冻结首轮门（clipping / 能量地板 / 置信度）之上。
   阈值只在 **round-2 `gate_cal` + `val`** 上标定，并显式约束**增量**已知类误拒 ≤ 1%。

## 数据使用纪律

- `sim-r2`：train/val/test/gate_cal/gate_test 全为新种子族（与首轮不重叠），独立参考检查 22/22 PASS。
  `val`/`gate_cal` 用于标定；`test`/`gate_test` 在规则冻结后才用于最终评估（同一冻结规则的
  确定性重跑只用于补充诊断/复现，未回改阈值）。
- 真实数据只读：三个 `campaign5` 会话是 **dev replay**（参与过首轮分析与本轮设计）；
  `campaign3 train-01`（release `1a30f4b7…`）在规则冻结后才被找到并评估，IQ 哈希与三个 dev
  会话不重叠，作为**独立真实会话**单独报告。仍只有 BPSK 单一配置，**不计算四类真实准确率**，
  也不是 ARM/板卡结果。
- 首轮四类模型与板端六类合同的兼容差距：**记录、未修改**（见首轮报告 §5.3 与 §11）。

## 产物

| 路径 | 内容 |
|---|---|
| `artifacts/rule_config.json` | 冻结的仲裁阈值、频谱门限、冻结首轮产物哈希、标定统计 |
| `analysis/failure_frames.json/.md` | 失败帧输入/特征/基线/CNN 输出对照（改进依据） |
| `analysis/tta_experiment.json/.md` | 被否决的候选（相位对称 TTA，dev 上不修复真实失败） |
| `analysis/calibration.json` | 规则标定完整过程与 trade-off |
| `analysis/reference-check-r2.json/.md` | round-2 数据集独立参考检查（22/22 PASS） |
| `analysis/independent_real_scan.json` | 全 evidence 物理会话扫描（仅一个独立 BPSK 会话） |
| `evaluation/reports/` | 新 test/gate_test 前后对照、逐 SNR、拒识/误拒、真实 dev replay、独立会话 |
| `reports/round2-report.md` | 本轮完整报告（依据/对照/各会话/拒识与误拒/大小耗时/下一步真实数据） |

## 附加：首轮 40ep trial 被预算截断后的接续检查（2026-10-21）

首轮 `artifacts/trials/formal-40ep/` 的 40 epoch 只是初始预算上限（best epoch 39、val macro-F1
0.7005，仍在变好），不是崩溃；本轮按"重新检查 + 接续未完成训练"的要求，从该 checkpoint 温启动
续训 40 epoch（绝对 41–80，同一 config/seed/AdamW；trial 未保存优化器/loader 状态，故为 warm
start 非逐位续训），**只做 val 诊断、不重读首轮 test、不用真实数据**。结果：best epoch 76，
val macro-F1 **0.8538** / accuracy **0.8542**，与首轮正式从零 80ep 模型（0.8545 / 0.8550，
best epoch 同为 76）几乎一致；续训产物在 `artifacts/training-resume/formal-40ep-continued/`，
报告见 **[reports/trial-40ep-resume.md](reports/trial-40ep-resume.md)**。

```bat
ml_training\.venv\Scripts\python.exe ml_training\round2\scripts\resume_40ep_trial.py
```

## 附加：2026-10-21 新真实帧的冻结迁移测试

数据：`evidence/rf-teaching/s2-46db-80mhz-narrowband-2026-10-21/raw/`（1 normal + 3 narrowband）。
冻结第二轮方案**不重训/不重标定/不连设备/不改产品运行时**直接推断，先保存原始结果再写报告：

- 输入/摘要：4 帧均 `s16_iq_le` 16,384 B / (4096,2) / 60 MSPS/decim=1；SHA256SUMS **60/60 匹配**；
  配置真值 TX = **BPSK/3 MSym/s/RECT**（normal 同帧 demod BER 0/138；narrowband 3 帧 demond 失锁，
  仅作干扰压力帧）；环境 `UNKNOWN`/`NARROWBAND` **不是**调制真值。
- 开发用途：与 45 个开发真实帧哈希 **0 重叠**、采集前 `ml_training` 引用 **0**、冻结产物 mtime
  早于采集时间 → 本次作为未见测试使用一次；若日后用于设计改进则转为 dev replay。
- 正常帧（n=1）：**BEFORE QPSK 接受 → AFTER BPSK 接受**（baseline 仲裁，`p_B−p_Q≈1.0`），
  频谱门未触发；单帧，不给准确率。
- 干扰帧（3 帧，单列不计干净四类准确率）：BEFORE 3/3 QPSK 接受 → AFTER 3/3 BPSK 接受
  （底层调制是 BPSK，但窄带干扰未被本冻结门拒识；环境判定属另一套标签）。
- 产物：`evaluation/reports/real_transfer_2026-10-21_raw.json`（先落盘，21,730 B）、
  `evaluation/reports/real_transfer_2026-10-21.md`、`reports/arm-compat-checklist.md`；
  脚本 `scripts/evaluate_real_transfer_2026_10_21.py`，测试 `tests/test_real_transfer_2026_10_21.py`。

```bat
ml_training\.venv\Scripts\python.exe ml_training\round2\scripts\evaluate_real_transfer_2026_10_21.py
```

## 限制 / NOT_RUN

ARM/板卡时延与 parity、板卡部署、真实四类准确率、更多独立真实会话、真实 QPSK/16QAM/64QAM、
真实非 BPSK 的 OFF/单音/干扰标定、硬件损伤建模、RRC/其它符号率、大规模超参搜索 —— 全部 `NOT_RUN`。
