# ml_training 跨组契约 v1（Lead 冻结，2026-10-20）

本文件是数据生成、模型/训练、评估三个写范围之间的**唯一接口契约**。任何一方需要改动本文件，
必须由 Lead 修改并通知另外两方；实现方不得单方面改字段名、单位、顺序或数组形状。

## 0. 写范围（advisory，互不重叠）

| owner | 可写目录 | 内容 |
|---|---|---|
| Lead | `ml_training/docs/`, `ml_training/config/`, `ml_training/common/`, `ml_training/model/`, `ml_training/training/`, `ml_training/artifacts/`, `ml_training/reports/`, `ml_training/README.md`, `ml_training/run.py`, `ml_training/tests/`, `ml_training/.venv/` | 契约、预处理、模型、训练、导出、集成、最终报告 |
| data-gen | `ml_training/data/**`, `ml_training/datasets/**` | 仿真数据生成器、独立参考检查、生成测试、生成清单与数据摘要 |
| eval-tools | `ml_training/evaluation/**` | 仿真测试评估、SNR 扫描、质量门/拒识、真实 IQ 只读清单与迁移评估、评估测试 |

禁止事项（全体）：修改 `ml_training/` 之外的任何产品代码、冻结模型、阈值、环境规则；
不得读取/移动/改写 `evidence/` 下任何文件（真实数据只读打开）；不连板卡、不发射、不动 Git。
真实数据本阶段**绝不进入训练/验证**，只做推理评估。

## 1. 运行环境

- 独立 venv：`<LOCAL_PATH>`
  （Python 3.12.10，numpy、torch CPU、pytest）。不改其他解释器环境。
- 工作目录固定为工程根 `<LOCAL_PATH>`。
- 所有命令示例用 `python` 指代上述 venv 解释器。
- 训练/评估只在本机 CPU 运行；不假设 Azure 虚拟机可用（本轮无连接资料，NOT_RUN）。

## 2. 标签

- 训练/仿真测试的 4 个已知类，顺序冻结：
  `CLASS_ORDER = ["BPSK", "QPSK", "QAM16", "QAM64"]`
- `UNKNOWN` **不是**训练类，只作为推理/拒识输出。
- 质量门总体（质量门/拒识报告用，绝不进 4 类训练，也绝不并入 4 类混淆矩阵）：
  `GATE_KINDS = ["OFF", "NOISE", "TONE", "CLIP"]`

## 3. IQ 数据格式

- 每帧 4096 个复数采样，存为 int16 小端交错的 `(I, Q)`：数组形状 `(4096, 2)`，dtype `<i2`。
- 取值限定 signed 12-bit：`-2048 .. 2047`（与板端真实采集一致）。
- 量化规则：`np.rint`（ties-to-even）后取整；量化后若越界，则该帧**饱和到
  `[-2048, 2047]` 以 int16 存储并标记 `clipped=True`**（模拟 ADC 削顶，rail 码保留），
  训练/4 类评估只使用 `clipped=False` 的帧。
- 追溯：2026-10-20 data-gen 指出 QPSK 的峰值归一化平均功率系数应为 2（两轴分量同时
  达到峰值 A，E|x|²=2A²），初稿误写 1.0；Lead 确认采用解析值（见 §4）。
- 磁盘上 `.s16`/`.iq` 帧文件 = `X.astype('<i2').tobytes()`，16384 字节。

## 4. 发射波形契约（冻结）

- 采样率 `fs = 60_000_000 Hz`；符号率 `Rs = 3_000_000`；`sps = 20`；成形 `RECT`（矩形保持）。
- 每帧使用**独立同分布均匀随机比特**（`np.random.default_rng(symbol_seed).integers(0, 2)`），
  **不得**用同一段 PRBS 做周期相移/切片来扩充样本。
- 每帧符号数：`n_symbols = ceil((4096 + max_window_offset) / sps) + 1 = 207`；
  RECT 成形 = `np.repeat(symbols, 20, axis=0)`。
- 位映射/位序与已冻结产品契约 `pynq_overlay/sw/rf_iq_signal.py:map_bits_to_symbol` 一致
  （MSB-first；I 用前若干 bit，Q 用后若干 bit）：
  - BPSK：`b0 → (axis(b0), 0)`，axis 表 `(1, -1)`（0→+1，1→-1）
  - QPSK：`(axis(b0), axis(b1))`，axis 表 `(1, -1)`
  - QAM16：`i = axis4((b0<<1)|b1)`，`q = axis4((b2<<1)|b3)`，axis4 表 `(3, 1, -3, -1)`
    → 00→3, 01→1, 10→-3, 11→-1（**非 Gray**，以冻结 RTL 为准）
  - QAM64：`i = axis8((b0<<2)|(b1<<1)|b2)`，`q = axis8((b3<<2)|(b4<<1)|b5)`，
    axis8 表 `(7, 5, 1, 3, -7, -5, -1, -3)`
- 幅度归一化（接收仿真域）：每帧把 RECT 波形按**复数包络峰值**缩放到 `amplitude_codes`：
  `max(|I + jQ|) = amplitude_codes`，且该归一化在施加接收初相位/CFO **之前**完成。
  理由：本仿真的接收相位/CFO 旋转会使双轴星座的分量峰值升至 `√2·A`；若按分量峰值归一化，
  高 SNR 会产生与接收条件无关的人为削顶（data-gen 逐 SNR 探针实测：分量峰值语义下
  QPSK 15 dB 约 13%、18 dB 约 6% 帧 clipped，复数峰值语义下所有类别/SNR 均 0%）。
  **注意**：产品 TX `generate_digital_frame` 的分量峰值归一化
  （`peak = np.max(np.abs(iq))`，即 `max(max|I|,max|Q|)`）语义未被修改，也未修改产品实现；
  本仿真为接收域安全而有意采用复数包络峰值，报告与 metadata 中如实记录该差异。
- 每个星座在复数包络峰值归一化下的平均功率系数
  `c = E|x|^2 / amplitude_codes^2`（解析值，供安全幅度与参考检查使用）：
  BPSK `1.0`，QPSK `1.0`，QAM16 `5/9`，QAM64 `3/7`。
  （推导：归一化固定 `max|I+jQ| = A`；BPSK \(|x|=A\) → \(A^2\)；QPSK 点 \((\pm1\pm j)/\sqrt2\cdot A\)
  → \(|x|=A\) → \(A^2\)；QAM16 单点最大 \(|x|=\sqrt{18}\)（轴电平 3,1 时），归一到 A 后
  功率 \(10A^2/18=5A^2/9\)；QAM64 单点最大 \(\sqrt{98}\)，功率 \(42A^2/98=3A^2/7\)。
  这些值**小于**旧的分量峰值系数（QPSK 2、QAM16 10/9、QAM64 6/7），所以同一 5.5σ 公式下更安全。）

## 5. 接收损伤模型（首轮，明确建模范围）

参数与生成顺序**冻结**（独立参考检查按此逐位复算）：

1. 由 `symbol_seed` 生成独立比特 → 映射 → RECT 成形（float64 `(4140, 2)`，4140 = 207*20）。
2. 帧内峰值归一化到 `amplitude_codes`（整数）。
3. 窗口起点：整数 `window_offset ∈ [0, 19]`（即 `wave[offset:offset+4096]`）。
4. 初相位：`exp(1j * phase_rad)`，`phase_rad ~ U[0, 2π)`。
5. 频偏：`x *= exp(1j * 2π * cfo_hz * n / fs)`，`n = 0..4095`，`cfo_hz ~ U[-20000, +20000] Hz`。
6. AWGN（复）：每分量独立正态，`sigma = sqrt(P_sig / (2 * snr_lin))`，
   `P_sig = mean(|clean_4096|^2)`（该帧旋转后实功率），`snr_lin = 10 ** (snr_db / 10)`，
   噪声由 `noise_seed` 的 `default_rng(...).standard_normal((4096, 2))` 生成后乘 `sigma`。
7. `rint` ties-to-even 量化到 int16；越界记 `clipped=True`。

- 训练/验证的 `snr_db ~ U[-6, +18]`（逐帧抽）；仿真测试按固定网格
  `[-6, -3, 0, 2, 4, 6, 8, 10, 12, 15, 18, 20]` dB。
- 幅度：先抽 `amplitude_codes ~ U[150, 1200]`（整数），再服从安全上限
  `amplitude_codes = min(drawn, floor(2046 / (1 + 5.5 * sqrt(c / (2 * snr_lin)))))`，
  使 AWGN 峰不越界（`c` 见 §4；下限 < 64 视为配置错误）。5.5σ 头寸使每帧至少一个
  分量越界的概率约 3e-4（4.5σ 时约 5%，smoke 实测约 6–14% 的帧被标 clipped，故上调）。
- **明确未建模**（报告中必须写为 unmodeled）：IQ 不平衡、DC 偏置、接收滤波器/RRC 失真、
  多径、相位噪声、AGC 瞬变、硬件频响、真实射频链路非线性。不得编造已校准参数。
- 每帧参数（`symbol_seed`, `noise_seed`, `snr_db`, `amplitude_codes`, `phase_rad`, `cfo_hz`,
  `window_offset`, `noise_sigma_codes`）必须逐个写入数据集元数据数组。

## 6. 数据划分（按源与种子族隔离）

- `train`：4 个 label 各 4 个 session，每 session 300 帧 → 4800 帧；
  seed families `train-a..train-d`。
- `val`：4 label 各 2 session × 150 帧 → 1200 帧；families `val-a, val-b`。
- `test`：每个 (label, snr_db_grid 点) 一个 session × 50 帧 → 2400 帧；
  families `test-a..test-c`。
- `gate_cal` / `gate_test`：各 4 个 kind（OFF/NOISE/TONE/CLIP）各 100 帧 → 各 400 帧；
  families `gate-cal-*` / `gate-test-*`。
- 三组 families 互不相交；帧全部独立生成（无增强副本、无周期相移跨组）；不同 split 之间
  不允许出现相同 `iq_sha256`；同一 session 内也应是独立随机符号（不得重复帧）。
- `gate_cal` 只用于质量门阈值选择；`gate_test` 只用于最终拒识指标；4 类 test 只用于最终
  仿真测试指标，选中后不得回改模型/阈值（报告须声明）。

## 7. 输出目录与 manifest 契约

`ml_training/datasets/<dataset_id>/` 下：

```
manifest.json
frames/<split>-<label>.npz        # split ∈ train/val/test；label ∈ 4 类
gate/<split>-<kind>.npz           # split ∈ gate_cal/gate_test；kind ∈ GATE_KINDS
```

每个 npz 至少有这些数组（名字/类型冻结）：

| 数组 | dtype | 形状 | 含义 |
|---|---|---|---|
| `X` | int16 | (N, 4096, 2) | IQ 帧 |
| `frame_id` | unicode | (N,) | 形如 `train-BPSK-train-a-0000` |
| `session_id` | unicode | (N,) | 形如 `train-BPSK-train-a` |
| `label` | unicode | (N,) | 4 类之一或 gate kind |
| `seed_family` | unicode | (N,) | split 内种子族 |
| `symbol_seed` | uint64 | (N,) | 符号 RNG 种子 |
| `noise_seed` | uint64 | (N,) | 噪声 RNG 种子 |
| `snr_db` | float32 | (N,) | 逐帧 SNR |
| `amplitude_codes` | int16 | (N,) | 峰值幅度 |
| `phase_rad` | float32 | (N,) | 初相位 |
| `cfo_hz` | float32 | (N,) | 频偏 |
| `window_offset` | int16 | (N,) | 窗口起点 |
| `noise_sigma_codes` | float32 | (N,) | 噪声每分量 sigma |
| `iq_sha256` | unicode | (N,) | 该帧 int16 字节 SHA-256 |
| `clipped` | bool | (N,) | 是否量化越界 |

`manifest.json` 顶层冻结字段：

```jsonc
{
  "schema": "ml-modrec-synthetic-v1",
  "dataset_id": "...",                  // 目录名
  "created_utc": "...Z",
  "config_path": "ml_training/config/first_round.json",
  "config_sha256": "...",
  "numpy_version": "...",
  "waveform": { "fs_hz": 60000000, "symbol_rate_hz": 3000000, "sps": 20,
                "shaping": "RECT", "samples": 4096, "n_symbols": 207 },
  "classes": ["BPSK", "QPSK", "QAM16", "QAM64"],
  "gate_kinds": ["OFF", "NOISE", "TONE", "CLIP"],
  "splits": {
    "train": {"seed_families": [...], "snr": {"kind": "uniform", "low": -6.0, "high": 18.0},
              "sessions": [{"session_id": "...", "label": "...", "seed_family": "...",
                            "frames": 300, "snr_db_grid": null}, ...]},
    "val":   { "...": "..." },
    "test":  {"seed_families": [...], "snr_db_grid": [...], "sessions": [...]},
    "gate_cal": { "...": "..." }, "gate_test": { "...": "..." }
  },
  "files": [{"path": "frames/train-BPSK.npz", "sha256": "...", "split": "train",
             "label": "BPSK", "count": 1200, "clipped_count": 0}, ...],
  "summary": {"total_frames": 9200, "per_split": {...},
              "clipped_frames": 0, "cross_split_duplicate_iq": 0, "intra_split_duplicate_iq": 0}
}
```

生成器必须同时产出 `ml_training/data/reports/generation-report.md`（含实际帧数、耗时、
clipped 统计、重复检查、参考检查结果、config/npz 摘要）。数据摘要以实际 sha256 为准。

## 8. 预处理契约 `dc-complex-rms-v1`（与产品一致）

由 Lead 的 `ml_training/common/preprocess.py` 实现；语义必须与
`pynq_overlay/sw/rf_cnn_runtime.py:preprocess` 等价（测试逐元素对比）：

- 输入 `(4096,2)` int16；范围/有限性校验。
- 任一分量等于 `-2048` 或 `2047` → `gate = {"ok": False, "reason": "clipping", "rms": 0.0}`。
- 逐分量去 DC 均值；`rms = sqrt(mean(sum(x^2, axis=1)))`；`rms <= 1e-6` → `reason="no_energy"`。
- 输出 float32 `(2, 4096)`（转置，通道在前），`x / rms`，保证复数 RMS = 1。
- 质量门推理顺序：`clipping/no_energy` → `rms < min_complex_rms`（`below_energy_floor`）→
  softmax 置信度/间隔门（`confidence` / `margin`）→ 否则给 4 类之一。

## 9. 模型产物契约（Lead 产出，eval-tools 消费）

`ml_training/artifacts/cnn/`：

- `model.pt`：torch checkpoint（state_dict + class_order/preprocessing/seed/config 摘要）。
- `model.json`：离线推理权重 JSON：
  ```jsonc
  { "schema_version": 1, "network": "tiny-iq-cnn-v1", "variant": "modrec4",
    "preprocessing": "dc-complex-rms-v1",
    "classes": ["BPSK","QPSK","QAM16","QAM64"], "input_shape": [2,4096],
    "supported_domain": {"input_fs_hz": 60000000, "decim": [1],
                         "symbol_rates_hz": [3000000], "shaping": ["RECT"]},
    "thresholds": {"max_softmax": 0.95, "margin": 0.05, "min_complex_rms": 40.0},
    "weights": {"conv1.weight": {"shape": [8,2,7], "values": [...]}, "conv1.bias": ...,
                "conv2.weight": ..., "conv2.bias": ..., "conv3.weight": ...,
                "conv3.bias": ..., "head.weight": {"shape": [4,16], ...},
                "head.bias": ...},
    "params": 1628, "macs": 0,
    "training": {"manifest_sha256": "...", "config_sha256": "...", "best_epoch": 0,
                 "val_macro_f1": 0.0, "seed": 0},
    "status": "experimental",
    "board_compat": "not loadable by a stock 6-class board runtime; contract extension needed" }
  ```
- `metadata.json`：版本、耗时、参数量、文件大小、SHA-256、NOT_RUN 声明。
- `curves.json`、`thresholds.json`。
- `ml_training/model/numpy_runtime.py`：`load_model(path) -> ModRecModel`；
  `model.predict_iq(iq_int16) -> {"label","reason","score","margin","scores","gate"}`；
  `model.forward(x_float32)`；纯 numpy，无 ctypes/产品依赖。
- `ml_training/artifacts/baseline/`（特征基线）：`model.json` + `metadata.json`；
  `ml_training/model/baseline.py`：`load_baseline`, `predict_iq`；`features.py`：`extract_features`。
  predict 返回同构 dict（至少 `label`、`scores`、`reason`）。

## 10. 评估契约（eval-tools 产出）

脚本（均可被 `run.py` 以子进程调用，退出码 0=成功）：

- `ml_training/evaluation/evaluate_sim.py`
  `python ml_training/evaluation/evaluate_sim.py --dataset ml_training/datasets/sim-v1 --model ml_training/artifacts/cnn --baseline ml_training/artifacts/baseline --out ml_training/evaluation/reports`
  输出：`sim_metrics.json`（4×4 混淆矩阵、per-class precision/recall/F1、macro-F1、准确率、
  gate_test 拒识率、每 SNR 点指标）、`sim_confusion.md`、`sim_snr.md`、`gate.md`。
  4 类指标只用 `clipped=False` 的 test 帧；gate 指标只用 gate_test。
- `ml_training/evaluation/real_transfer.py`
  `python ml_training/evaluation/real_transfer.py --model ... --baseline ... --out ...`
  只读真实 IQ，输出 `real_inventory.json`、`real_bpsk.md`、`real_failures.json`。
  规则见 §11。

所有结果 JSON 必须含 `"not_run": [...]` 列出未执行项；不得把主机数字写成 ARM 数字。

## 11. 真实数据只读规则（eval-tools）

- 只用已有落盘的真实物理帧；候选根目录（只读）：
  `evidence/rf-teaching/codex-s2-takeover-2026-10-18/campaign5-val-followup/download/{train-01,train-02,val-01}`
  （每会话 10 个 normal + 1 个 off，物理 BPSK 3MSym/s RECT，60MSPS，4096 点），以及同目录
  `campaign4`（与 campaign5 重复，必须按 `iq_sha256` 去重；不得重复计数）。
- 真值来源：`<frame>.json` 中 `trainval.truth`（normal/off）与
  `demod_result.submitted.config`（`mode=BPSK, symbol_rate_hz=3000000, shaping=RECT`）。
  **不得**用现有算法输出（demod/model/env label）当训练标签。
- 排除 `analysis.clipping_count > 0` 或 `output_clipped=true` 的帧（单列削顶）。
- 会话划分：`train-01/train-02/val-01` 三会话分别报告；`val-01` 为"未参与任何阈值选择/调参的
  独立真实会话"，作为最终迁移测试；gate 阈值只允许用仿真 `val` + `gate_cal` 选。
- 只能报告：已有真实 BPSK 的识别表现、误判去向、失败案例、OFF/削顶/未知拒识；
  **不得**计算四类真实总体准确率，不得把仿真准确率称为板卡/实测准确率。
- 其他目录的物理帧（如受干扰场景）如需纳入，必须逐帧有 manifest/收据支持且去重；
  无法证明物理属性的一律不纳入，并在 `not_run/limitations` 说明。

## 12. CLI 契约（Lead 集成）

`python ml_training/run.py <cmd>`：

- `generate [--variant formal|smoke]`
- `train [--variant formal|smoke]`
- `evaluate [--variant formal|smoke]`
- `infer --iq <file.s16> [--model ...]`（离线推理示例，打印 label/scores/reason）
- `all [--variant formal|smoke]`：generate → train → evaluate → infer 示例；一条命令全流程。

生成器 CLI（data-gen 提供）：
`python ml_training/data/generate.py --config ml_training/config/first_round.json --variant formal --out ml_training/datasets/sim-v1`
（`--variant smoke` 输出到 `ml_training/datasets/sim-smoke`，帧数按配置里的 `smoke` 覆盖）

训练 CLI（Lead 提供）：`python ml_training/training/train.py --config ... --dataset ... --out ml_training/artifacts [--variant]`

## 13. 质量要求

- 生成器、参考检查、预处理、numpy runtime、导出各有 pytest；全部测试可在 venv 中无网络运行。
- 先小批量（smoke）跑通全流程，再跑正式首轮；正式首轮不进行大范围超参数搜索。
- 最佳检查点只看 val；test 只评一次；若任何调参使用过 test，必须声明它不再是独立测试集。
- 所有报告数字来自实际运行产物；未跑项写 `NOT_RUN`。
