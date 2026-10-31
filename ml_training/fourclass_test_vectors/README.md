# 四类信号 SYNTHETIC 测试向量（`fourclass_test_vectors/`）

> **SYNTHETIC / 非真实采集**：本目录只生成离线测试输入，不计入真实采集数量、真实准确率或
> 产品资格。未连接板卡/Pluto，未发射、采集、安装或部署。未来 ARM 对照由主项目现有 B 代理
> 负责，本目录不执行。

## 1. 复用了什么

| 已有实现 | 复用方式 |
|---|---|
| `pynq_overlay/sw/rf_iq_signal.py` | 6 MSPS 文件直接调用 `generate_digital_frame`：PRBS7、冻结星座映射、RECT、`s16_iq_le` 打包；未复制/重建一套框架 |
| 冻结模型预处理/输入域 | 评估入口使用 `ml_training.real_campaign` → `Round2Predictor`（冻结 round-1 CNN + baseline + spectral gate；CNNs/rules/baseline SHA 校验） |
| `real_campaign/domain.py` | 60 MSPS 输入域检查；6 MSPS 明确拒绝，不静默重采样混用 |
| 已有 `ml_training/data/generate.py` | 仅作为 60 MSPS/4096 窗和幅度语义的只读参考；本任务不修改、不重新训练 |

没有新增模型、阈值、训练或通用 SDR 框架。

## 2. 规格

- 3 MSym/s、RECT、无差分编码；PRBS7 seed 1..127，周期 127 符号。
- 幅度：**component peak**，`max(max|I|,max|Q|)=1800` codes；无 `-2048/2047` 削顶码。
- 采样/格式：

| 表示 | 采样率 | sps | 每文件样本 | 字节 | 用途 |
|---|---:|---:|---:|---:|---|
| 6 MSPS | 6,000,000 Hz | 2 | 254 complex (127 symbols) | 1,016 | 未来 Pluto/GNU Radio 发射接口 |
| 60 MSPS | 60,000,000 Hz | 20 | **4096 complex** | 16,384 | 当前冻结模型离线检查（`s16_iq_le`, 4096 窗口） |

- 60 MSPS 不是另一套符号：它是 **6 MSPS 量化周期逐样本×10 保持**，重复 2 个周期后取前
  4096 样本；两者分开记录 SHA/样本数/采样率，**禁止混用**。
- 格式：little-endian signed int16 interleaved `I,Q`（4 字节/复样本），`s16_iq_le`。
- 具体 seed、映射表、幅度归一化、IQ 顺序、SHA-256 全部在
  [`data/manifest.json`](data/manifest.json) 与 [`config/vectors.json`](config/vectors.json)。

四类 seed：BPSK `1`、QPSK `37`、QAM16 `73`、QAM64 `127`。

## 3. 运行（CPU 离线）

```bat
ml_training\.venv\Scripts\python.exe ml_training\fourclass_test_vectors\run_all.py
```

一条命令完成：

1. 生成 6 MSPS + 60 MSPS 文件与 manifest；
2. 独立 PRBS/星座检查（不导入分类器，不把预测当 truth）；
3. 冻结 `Round2Predictor` 评估 60 MSPS；6 MSPS 只做输入域拒绝检查；
4. 生成 SVG 星座预览与 CSV。

分步命令：

```bat
python ml_training\fourclass_test_vectors\generator.py
python ml_training\fourclass_test_vectors\independent_check.py
python ml_training\fourclass_test_vectors\evaluate_vectors.py
python ml_training\fourclass_test_vectors\preview_svg.py
```

## 4. 已生成结果

- 独立检查：**PASS**；4/4 类的 6 MSPS 与 60 MSPS 文件均与独立 PRBS7/轴表重建的期望字节
  完全一致（`results/independent_check.json`）。检查器不导入生成器/分类器/模型。
- 冻结评估（仅 60 MSPS，4 个 SYNTHETIC 帧）：**4/4 accepted，无拒识、无误判**；
  四类预测与 truth 一致（`results/evaluation.json`、[evaluation.md](results/evaluation.md)）。
  **这不是真实准确率，也不是产品资格。**
- 6 MSPS：输入域检查返回 `unsupported_sampling_domain`，文件长度检查返回
  `invalid_iq_length`；**未喂模型**（`six_msps_all_fed_to_model=false`），不会把 6 MSPS
  静默当 60 MSPS 使用。
- 星座预览：[constellation_preview.svg](data/constellation_preview.svg)；
  点表：[constellation_points.csv](data/constellation_points.csv)。

若未来模型/阈值变化导致误判或拒识，评估脚本会逐帧如实记录，不调模型、阈值或向量。

## 5. 新候选 v8 只读对接检查（SYNTHETIC）

2026-10-22 对候选 `4adee8ecbdac03e13338b10be7278909` 做过一次只读对接检查：

```bat
ml_training\.venv\Scripts\python.exe ml_training\fourclass_test_vectors\check_candidate_adapter.py
```

- 候选身份：manifest release_id 与期望 ID 一致；adapter SHA-256
  `1ed094bb…`；manifest SHA-256 `c3281179…`。
- 60 MSPS 四类：候选 adapter 全部类别正确、4/4 accepted、无拒识；与已有冻结评估逐项比较
  **0 class/acceptance/reason/input-domain mismatch**，四类 score 最大差 **0.0**。
- 6 MSPS：候选 adapter 返回 `UNAVAILABLE / unsupported_sampling_domain`，未进入模型。
- 模型 artifact（CNN/baseline/rules）与冻结评估参考逐 SHA 一致。
- 结果只读、SYNTHETIC，不改变候选/向量/模型/阈值；不是 ARM、真实 RF 或产品资格。
- 详细结果：[results/candidate_v8_4adee8ec/candidate_check.md](results/candidate_v8_4adee8ec/candidate_check.md)、
  [candidate_check.json](results/candidate_v8_4adee8ec/candidate_check.json)。

## 6. 文件

| 文件 | 作用 |
|---|---|
| `config/vectors.json` | 配置示例：seed、模式、率、sps、幅度、映射表与边界说明 |
| `generator.py` | 复用产品 PRBS/映射/打包生成两种表示；不训练、不预测 |
| `independent_check.py` | 独立 PRBS7/星座/字节校验；不使用分类器 |
| `evaluate_vectors.py` | 接入已有冻结 `Round2Predictor`；报告预测/拒识/输入域 |
| `preview_svg.py` | 无绘图库依赖的星座 SVG/CSV |
| `run_all.py` | 一键 CPU 离线复现 |
| `data/` | 4 类 `.iq`、manifest、SVG/CSV（全部 SYNTHETIC） |
| `results/` | 独立检查、冻结评估、summary |
| `GNU_RADIO_INTERFACE.md` | 未来 Pluto/GNU Radio 接口说明（仅文档，不操作设备） |
| `tests/` | 本目录定向测试；不重跑历史全套 |

## 6. 边界

- 6 MSPS 是 Pluto/GNU Radio 发射表示；60 MSPS 是冻结模型接收域；两者不可互换。
- 文件为 12-bit 码值 scaled to component peak 1800；GNU Radio 端如何归一化/加载见
  [GNU_RADIO_INTERFACE.md](GNU_RADIO_INTERFACE.md)。
- 不改变既有模型、阈值、产品代码、冻结数据或原始证据；仅新增本目录。
