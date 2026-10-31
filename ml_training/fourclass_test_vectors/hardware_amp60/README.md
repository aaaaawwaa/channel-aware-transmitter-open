# 四类幅度 60 实际 TX 载荷（`hardware_amp60/`）

> **离线生成的 TX 载荷候选**，不是真实 RF 采集，不加 TRAIN、不重训、不调模型/阈值/幅度。
> 仅新增本目录；未修改旧的 amplitude-1800 向量、模型、产品代码或主项目 runner。

## 1. 参数与复用

| 项 | 值 |
|---|---|
| 模式/seed | BPSK 1、QPSK 37、QAM16 73、QAM64 127 |
| 采样率 | 60,000,000 S/s |
| 符号率 | 3,000,000 Sym/s |
| 成形 | RECT |
| 滚降记录 | **0.35**（与最终 runner/UI 配置一致；RECT 波形不依赖 rolloff，但配置明确记录） |
| 幅度 | component peak **60** codes：`max(max|I|,max|Q|)=60` |
| 差分编码 | 否 |
| 生成器 | 复用 `pynq_overlay/sw/rf_iq_signal.py` 的 PRBS7、冻结星座映射、RECT、`s16_iq_le` 打包 |

rolloff 0.35 来源：`scripts/rf_bpsk_ui_board_session.py:310`；幅度 60 来源：
`pynq_overlay/sw/rf_teaching_profile.py:53`（`tx_amplitude=60`）。

## 2. 两种样本视图

| 视图 | 每类样本 | 字节 | 用途 |
|---|---:|---:|---|
| **实际 TX 载荷 / 周期** | **2540** complex | 10,160 | 127 PRBS symbols × 20 sps；未来板端 TX 载荷 |
| **软件参考窗口** | **4096** complex | 16,384 | 冻结模型 60 MSPS/4096 输入 |

构造关系：

```
tx_period      = 127 symbols × 20 samples/symbol = 2540 samples
reference_4096 = tile(tx_period, 2)[:4096]
               = 1 full period + first 1556 samples of the next period
               = 1.612598... periods, no resampling
```

实际 TX 载荷与 4096 参考窗口是同一 60 MSPS 波形的前缀/周期关系，不是两个不同信号。
4096 窗口时长 68.2667 µs，2540 周期 42.3333 µs。

## 3. 文件与 SHA

- 实际 TX 载荷：`outputs/tx_payload/<mode>.iq`（2540 samples，`s16_iq_le`，I/Q 交错，10,160 B）。
- 4096 参考窗口：`outputs/reference_4096/<mode>.iq`（4096 samples，16,384 B）。
- 独立符号真值：`outputs/truth/<mode>.symbols.json`（bits、原始星座点 I/Q、缩放码值、seed、映射）。
- 完整清单：`outputs/manifest.json`（每类 seed、样本数、格式、SHA-256、滚降、构造关系、
  `old_amp1800` 对照、runner 配置证据）。
- 独立验证：`outputs/verification.json`（不导入分类器；4/4 TX/参考窗口逐字节与独立 PRBS/轴表一致）。
- 冻结模型结果：`outputs/frozen_eval.json` / `.md`。

幅度 60 与既有 amplitude-1800 向量**不声称、也不是**字节相同；manifest 记录了旧 60 MSPS 文件
SHA 和 `same_sha256=false`。真实 RF 接收 IQ 也不要求与理想载荷字节相等。

## 4. 冻结模型离线检查（如实记录）

使用 `ml_training.real_campaign` 的冻结 round-2 入口 `Round2Predictor`，输入 4096 参考窗口：

| truth | 结果 | accepted | reason | rms |
|---|---|---|---:|---:|
| BPSK | BPSK | true | — | 59.917 |
| QPSK | QPSK | true | — | 84.789 |
| QAM16 | QAM16 | true | — | 62.825 |
| QAM64 | **UNKNOWN** | **false** | **below_energy_floor** | 55.264 |

- 3 accepted / 3 correct；QAM64 被能量门限拒识，如实保留，**没有为通过而调模型、阈值或幅度**。
- CNN 内部 class hint 仍为 QAM64，但最终结果是 UNKNOWN；不可把 hint 当作 accepted 或真值。
- 这不是真实 RF 准确率、四类总体准确率或统计 BER 验收。

## 5. 一键入口

```bat
ml_training\.venv\Scripts\python.exe ml_training\fourclass_test_vectors\hardware_amp60\run_all.py
```

分别运行：

```bat
python ml_training\fourclass_test_vectors\hardware_amp60\generate.py
python ml_training\fourclass_test_vectors\hardware_amp60\verify.py
python ml_training\fourclass_test_vectors\hardware_amp60\evaluate_frozen.py
```

主项目直接使用 `generate.py --out-dir <dir>` 生成并读取 `outputs/manifest.json` 即可；
配置示例见 [`config/hardware_amp60.json`](config/hardware_amp60.json)。

## 6. 边界

- 只做离线载荷/符号/冻结模型检查；不连接板卡/Pluto、不发射/采集/安装、不训练、不重跑历史全套测试、不新增审查代理。
- 结果不能升级为真实 RF 准确率、统计 BER 通过或产品资格。
