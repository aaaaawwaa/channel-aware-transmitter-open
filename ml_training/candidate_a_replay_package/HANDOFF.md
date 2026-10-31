# 候选 A 固定回放包 — 工程交接

状态：**6 帧固定回放包已生成并主机自检 PASS**。
只使用已有数据（5 合成 + 1 真实），未训练、未生成新模拟数据、未改 A 字节/阈值、未连接设备。
本包用于主机/ARM 计算一致性检查，**不是性能验收**；错误输出原样保留，不要求把已知负例全部改判正确。

## 1 包内容与复现

```bat
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\candidate_a_replay_package\build_package.py
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\candidate_a_replay_package\verify_package.py
```

产物：

```text
ml_training/candidate_a_replay_package/
  manifest.json                  # 6 帧清单、模型锁、修正说明
  frames/<id>/frame.iq           # 原 IQ 字节副本
  frames/<id>/frame.json         # 源、条件、真值、IQ SHA、original/A 输出与路径
  verification.json              # 主机重放 PASS/FAIL
  build_package.py
  verify_package.py
  HANDOFF.md
```

`verify_package.py` 当前结果：**PASS，6/6 帧，0 error**；合成帧回查 sim-v1 源 index，
真实帧回查 task80 原始 `.iq` 字节，均一致。

## 2 六帧覆盖

| # | 类别 | 来源 | 合成/真实 | 真值 | original 输出 | A 输出 |
|---|---|---|---|---|---|---|
| 01 | A 新增 BPSK→QPSK | sim-v1 `val` index 24 | 合成 | BPSK | BPSK | **QPSK**（错误保留） |
| 02 | A 新增 BPSK→QAM64 | sim-v1 `test` index 439 | 合成 | BPSK | BPSK | **QAM64**（错误保留） |
| 03 | A confidence 拒识 | sim-v1 `val` index 158 | 合成 | BPSK | BPSK | **UNKNOWN/confidence**（错误保留） |
| 04 | 高 SNR 正确 BPSK | sim-v1 `test` index 500 | 合成 | BPSK | BPSK | BPSK |
| 05 | 正确 QPSK | sim-v1 `test` index 1100 | 合成 | QPSK | QPSK | QPSK |
| 06 | task80 OFF-last | task80 `raw/10-off-last/s00-off-00.iq` | **真实** | OFF | **QAM64（误接受，保留）** | UNKNOWN（正确拒识） |

逐帧条件：

| # | SNR dB | CFO Hz | amplitude codes | 备注 |
|---|---:|---:|---:|---|
| 01 | −2.61 | 12862 | 327 | A cnn_top1 QPSK，baseline margin 0.280，未仲裁 |
| 02 | 12.0 | −2199 | 994 | A cnn_top1 QAM64；original 由仲裁回 BPSK |
| 03 | −3.61 | −13134 | 296 | A score 0.313 < 0.45，confidence |
| 04 | 18.0 | −5790 | 417 | 高 SNR 正确 BPSK |
| 05 | 18.0 | −12731 | 436 | 高 SNR 正确 QPSK |
| 06 | 缺失 | 12435（demod 估计） | TX amp60 | 真实 OFF；coherence≈0.0376，environment INVALID |

真实帧来源、metadata 路径、capture/request ID 均在 `frame.json`；合成帧记录
`dataset=sim-v1, split, index, session_id, seed_family`。合成/真实在 `manifest.json`
和每帧 `frame.json` 中均有 `synthetic`/`real_capture` 布尔字段明确区分。

## 3 每帧保存的字段

- 源：`source.dataset/split/index` 或 `source.real_path/metadata_path`；
- 条件：合成 `snr_db/cfo_hz/amplitude_codes/window_offset/phase_rad`；真实 demod CFO、
  carrier sync/coherence、environment、TX amp；缺失 `snr_db` 明确为 `null` + `missing_fields`；
- 真实类别 `truth_class`（不从模型输出反填）；
- `iq_sha256`、`iq_bytes`、`frame.iq`；
- `original` 与 `A` 的实际 `label/reason/accepted/scores`；
- 决策路径 `path.stage`（preprocess/energy_floor/spectral/confidence/margin/arbitration/cnn_top1）、
  `cnn_label`、`baseline_label`、`arbitration_margin_bpsk_minus_qpsk`、`arbitration_applied`、
  `final`。

`06` 的 original 误接受和 `01/02/03` 的 A 错误均原样写入，不做改判。

## 4 交接修正（重要）

- **TX amp60 不是接收 IQ 幅度**。task80 BPSK 帧的实际接收 ADC 峰值为 **898**，
  去 DC 后复 RMS 为 **504.51**（`504.514147311898`）；TX amp60 只是发射幅度码。
- **高 SNR 分层统计是“A 相对原模型的新增错误率”**，不是 A 的总体错误率。
  A 的总体错误率/召回不能由这些分层数字代替；task80 10 帧也只是独立诊断，不是总体准确率。
- task80 OFF-last 的 original 误接受、A 拒识均保留，不能据此宣称 A 总体 100% 正确。

## 5 A 模型/规则/预处理/哈希

| 项 | 值 |
|---|---|
| 状态 | `experimental`；未部署、未批准 |
| 模型目录 | `ml_training/qam16_domain_repair/results/candidate_A/` |
| model.json | SHA `b840f9dbaebc5db855ef1535d091e1f1583e12368ca62742f517a8d38123a28e` |
| rules.json | SHA `10a0bef4d237818eaa39dcd4e3048466de60017dbf0545c02bcbf405da2cdf64` |
| metadata.json | SHA `da1c7ec8ad504f7c632146090bd1604420ff91aa086f62a17d1edd107b36b8d1` |
| model.pt | SHA `2521f445f2a2ce8e681713a2d3653f43378bda9c9cd6428ce07f1409ae6bad0f` |
| checkpoint_best.pt | SHA `32a1605d04fee85eeeb9b79d0507f51c92244d288bed3253374ef4fcd588ac74` |
| 网络 | `tiny-iq-cnn-v1` / `modrec4`；1628 params；1,278,016 MACs |
| 预处理 | `dc-complex-rms-v1` |
| 类别顺序 | `BPSK, QPSK, QAM16, QAM64` |
| 阈值 | `max_softmax=0.45, margin=0.0, min_complex_rms=57.05962767972466` |
| baseline | `ml_training/artifacts/baseline/model.json`，SHA `caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014` |
| 仲裁 | BPSK/QPSK baseline 守卫，threshold 0.3 |
| 训练增广 | train-only 共轭/频谱反转；4800→9600 帧；best epoch 65；val macro 0.8454 |
| 加载 | `Round2Predictor(rules_path=".../candidate_A/rules.json")` |

已知限制：

- BPSK 新增错误集中在低 SNR、低幅度、高 |CFO|；当前 task80 BPSK 由仲裁兜底；
- sim-v1 幅度 150–1200 codes，不包含当前 amp60；task80 无 SNR ground truth；
- A 依赖 baseline 仲裁救 BPSK，低 SNR margin 变弱时 CNN QPSK 错误会直接接受；
- task80 10 帧独立诊断，不能宣称总体准确率 100%；
- 不修改 A 字节；扩大试用需主线独立候选审查。

## 6 主机/ARM 使用方式

1. 将本包 `frames/<id>/frame.iq` 原样送入 ARM 上的同一 `dc-complex-rms-v1` 预处理、
   原模型和 A `Round2Predictor`；
2. 对比 `frame.json` 中 `original`/`A` 的 label、reason、accepted、scores 和 path；
3. 期望：本包错误保持错误（`01/02/03` A 错，`06` original 误接受），不要求 ARM 改判；
4. 只做一致性检查，不作性能验收，不据此安装/发布。

## 7 边界

- 仅新增本交接子目录；未覆盖既有分析结果；
- 不训练、不生成新模拟数据、不改 A 权重/阈值、不连接设备；
- 合成帧来自既有 sim-v1 VAL/已知 test；真实帧来自既有 task80；
- 错误输出原样保留，不删除坏例、不缩小输入域；
- 本包不作为发布批准，task80 也不是独立性能验收。
