# 2026-10-21 新真实帧的冻结第二轮迁移测试

> 数据：`evidence/rf-teaching/s2-46db-80mhz-narrowband-2026-10-21/raw/`（只读；1 个 normal + 3 个
> narrowband/干扰帧）。本测试只做推断：**不重训、不重新标定、不连接设备、不改产品运行时**。
> 冻结方案 = 首轮 4 类 CNN + 第二轮冻结仲裁/频谱门；冻结产物哈希见 §1。原始测试结果在任何
> 报告/改进之前已保存为 `real_transfer_2026-10-21_raw.json`（21,730 B，
> sha256 `d7c6c491f12e7c317c60c43248dbe4d842f45edccee2237e50a1f33f7d74f350`）。

## 1. 冻结方案与数据核对

**冻结产物（本轮只读使用）**

| 产物 | sha256 |
|---|---|
| 4 类 CNN `ml_training/artifacts/cnn/model.json` | `25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc` |
| 可解释基线 `ml_training/artifacts/baseline/model.json` | `caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014` |
| 冻结规则 `ml_training/round2/artifacts/rule_config.json` | `9aceebbdb6b9b9a54ad0dd09ebd61935ae8c8fd276ba18d8b9db69e7949619e0` |

规则内容（未改）：BPSK/QPSK 仲裁阈值 `0.30`；`top5_frac > 0.527328` → `tone_like`；
`band_frac_3m < 0.251610` → `noise_like`；首轮门 `max_softmax=0.45`、`margin=0.0`、
`min_complex_rms=57.05962767972466`。

**输入格式（全部通过）**

| frame | kind | bytes | dtype/shape | min/max code | IQ sha256（前 16） | metadata/summary 一致 |
|---|---|---|---|---|---|---|
| nf-00-normal | normal | 16,384 | i16 LE / (4096,2) | −437 / 442 | `b40cc8d74e003716…` | 是 / 是 |
| nf-01-narrowband | narrowband | 16,384 | i16 LE / (4096,2) | −589 / 831 | `078504a6abb14ea8…` | 是 / 是 |
| nf-02-narrowband | narrowband | 16,384 | i16 LE / (4096,2) | −590 / 815 | `75286f67149a0d84…` | 是 / 是 |
| nf-03-narrowband | narrowband | 16,384 | i16 LE / (4096,2) | −589 / 772 | `e90b1cab07c0ae3a…` | 是 / 是 |

- 4 帧均为 4096 samples @ 60 MSPS、decim=1、`s16_iq_le`；无全零、无 rail；signed-12 范围内。
- 证据目录 `SHA256SUMS` **60/60 条全部匹配**；4 个 IQ 的哈希与各帧 metadata 的 `iq_sha256`、
  `raw/SUMMARY.json` 一致。
- 采集身份：`capture_status.state=done`、`physical=true`、`op=loopback.physical.capture`、
  `egress_safe=true`；release `3f84f6a189e833b6986f4c819ff89be0`，4 帧同会话、4 个不同 IQ SHA。

**配置真值（不拿环境标签当调制真值）**

| frame | TX 配置真值（`demod_result.submitted.config`） | 解调/场景旁证 | 环境标签（仅上下文） |
|---|---|---|---|
| nf-00-normal | BPSK / 3,000,000 / RECT | `carrier/timing/reference_sync=true`，BER **0/138**；`declared.truth=normal` | `UNKNOWN / AMPLITUDE_OUTSIDE_RULE_SUPPORT` |
| nf-01-narrowband | BPSK / 3,000,000 / RECT | `CARRIER_NOT_SYNCED`；`declared.truth=narrowband`（BPSK + 6 MHz tone，幅度 40） | `NARROWBAND / SPECTRAL_EXCESS` |
| nf-02-narrowband | 同上 | 同上 | 同上 |
| nf-03-narrowband | 同上 | 同上 | 同上 |

- **normal 的调制真值 = BPSK**：由 TX 提交配置 + 同帧解调 BPSK 同步/BER=0 双重支持；
  环境标签 `UNKNOWN`（幅度超出环境规则支持）**不是**调制识别真值，未用于本测试的标签或选型。
- narrowband 3 帧的底层 TX 也是 BPSK，但叠加了 6 MHz tone（`declared.tone_amplitude=40`），
  demod 失锁；它们**单列为干扰压力测试**，不计入干净四类准确率。

**这些数据是否曾用于开发？→ 否（本次作为未见测试使用一次）**

- 与 round-1/round-2 开发用的 45 个真实 IQ 哈希逐一比对：**重叠 0**；
- `ml_training/` 中在本证据采集时间（2026-10-21 15:48）之前引用该目录或这 4 个 IQ 哈希的文件：
  **0 个**；冻结产物 mtime（CNN 2026-10-20 21:04、规则 2026-10-20 22:53）**早于**帧采集时间；
- 同 evidence 目录下的 `analysis/` 是另一条板卡/环境规则工作线的环境分析（tone concentration 等），
  与本冻结 4 类调制方案无关；raw metadata 里的 `model_result` 是板端 6 类生产模型
  （`f76f2fc0…`）的推理记录，**仅作上下文**，不是本次冻结 4 类管线的输出，也不作为本测试的参考真值。

## 2. 正常帧（干净四类）结果

| 指标 | BEFORE：冻结首轮 CNN + 首轮门 | AFTER：冻结第二轮 |
|---|---|---|
| 预测类别 | **QPSK**（错误） | **BPSK**（正确） |
| 接受/拒识 | 接受 | 接受 |
| 拒识原因 | 无 | 无 |
| CNN 原始 score / margin | 0.9545 / 0.9107 | 0.9545 / 0.9107（CNN 原分数） |
| 仲裁 | 未启用 | **启用**：CNN QPSK → 基线 BPSK，`p_BPSK − p_QPSK ≈ 1.000` |
| 频谱门 | 未启用 | 未触发：`top5_frac=0.0595 < 0.5273`；`band_frac_3m=0.8346 > 0.2516` |
| 输入统计 | — | `complex_rms=275.11`，载波相位 48.23°，无削顶 |

结论：**这一帧上冻结第二轮修复生效**（QPSK→BPSK 接受，无拒识）。样本 n=1，只能作为单帧迁移
证据，**不能**据此给出真实准确率、泛化率或四类性能。

## 3. 干扰帧压力测试（3 帧，单列，不计入干净四类准确率）

| frame | 场景真值（旁证） | BEFORE | AFTER | 仲裁 | 频谱门 |
|---|---|---|---|---|---|
| nf-01-narrowband | BPSK + tone；demod 失锁 | QPSK 接受（0.9388） | **BPSK 接受** | 启用，`p_B−p_Q=0.9973` | 未触发（top5=0.2985，band=0.5590） |
| nf-02-narrowband | 同上 | QPSK 接受（0.9356） | **BPSK 接受** | 启用，`p_B−p_Q=0.9995` | 未触发（top5=0.3002，band=0.5622） |
| nf-03-narrowband | 同上 | QPSK 接受（0.8592） | **BPSK 接受** | 启用，`p_B−p_Q=0.9996` | 未触发（top5=0.2946，band=0.5676） |

- 底层调制仍是 BPSK，因此输出 BPSK 从"调制类别"看不矛盾；但这是被窄带干扰污染的帧，
  **不能计入干净四类准确率**（本报告不给其中任何准确率数字）。
- 冻结频谱门是为**纯 TONE / 纯 NOISE**设计的（阈值 0.5273 / 0.2516），不会把"BPSK+窄带干扰"
  判为 unknown；干扰/环境判定属于环境诊断层（本目录环境规则给 `NARROWBAND/SPECTRAL_EXCESS`），
  与 4 类调制识别是两套标签。若产品策略要求在窄带干扰时拒识，应另立环境/质量门改进，
  不能把这三帧计入干净类指标。3/3 结果仅作压力观察，不声称统计意义。

## 4. 失败处理与原始结果保留

- 正常帧未失败（QPSK→BPSK，接受）；原始推断结果（含 before/after 分数、仲裁、频谱、拒绝原因）
  已在写本报告之前落盘到 `real_transfer_2026-10-21_raw.json`，未对结果做任何阈值/后处理修改。
- 干扰帧"未拒识"记为 **stress observation**，不是干净分类失败；本次**未**据此调阈值、重标定、
  重训或改规则。
- 若后续用这 4 帧设计改进：它们立即转为 **dev replay 资料**，不再称"未见测试"，并需另设新测试集。

## 5. 局限

- 只有 **1 个 normal 帧**：不能给出真实四类准确率/置信区间，也不能证明真实泛化已解决；
- 只有 BPSK / 3 MSym/s / RECT、单一 release、单会话、TX1→RX1 物理回环；
- 全部为主机 CPU 离线推断，**不是 ARM/板卡**结果；
- 窄带干扰为预声明注入（6 MHz tone，幅度 40）的物理回环，不代表所有干扰形态；
- 环境标签（UNKNOWN/NARROWBAND）不是调制真值，本次未使用。

## 6. 下一步最少真实数据 / 使用纪律

1. 更多独立 normal 会话（≥2–3 个批次，覆盖低幅度 ~275 与不同载波相位），以及独立 OFF 帧；
2. 真实 QPSK/16QAM/64QAM 会话（目前真实只有 BPSK，四类真实准确率仍 `NOT_RUN`）；
3. 若要研究窄带/单音干扰下的拒识策略：需独立干扰会话与显式的环境/质量门设计，不能混入干净四类指标；
4. 任何用本 4 帧做的改进都转为 dev replay，并重新切分未参与设计的新测试集；
5. ARM/板卡最小验证准备见 `ml_training/round2/reports/arm-compat-checklist.md`（本任务 `NOT_RUN`）。

## 7. 文件与复现

| 内容 | 路径 |
|---|---|
| 冻结推断脚本 | `ml_training/round2/scripts/evaluate_real_transfer_2026_10_21.py` |
| 原始冻结结果（先落盘） | `ml_training/round2/evaluation/reports/real_transfer_2026-10-21_raw.json` |
| 本报告 | `ml_training/round2/evaluation/reports/real_transfer_2026-10-21.md` |
| ARM/兼容准备清单 | `ml_training/round2/reports/arm-compat-checklist.md` |
| 验收测试 | `ml_training/round2/tests/test_real_transfer_2026_10_21.py` |

```bat
ml_training\.venv\Scripts\python.exe ml_training\round2\scripts\evaluate_real_transfer_2026_10_21.py
ml_training\.venv\Scripts\python.exe -m pytest ml_training\round2\tests -q
```
