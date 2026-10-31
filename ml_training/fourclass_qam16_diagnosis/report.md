# QAM16 → QAM64 误判定位报告（14 帧真实接收）

状态：**离线诊断完成；未训练、未改模型/阈值/RTL/产品代码、未连接设备、未生成新真实帧。**
范围：仅新增 `ml_training/fourclass_qam16_diagnosis/` 分析脚本与结果；原始证据只读。
数据：`evidence/rf-teaching/s2-46db-fourclass-real-rx-2026-10-22/run/`，14 帧真实 IQ。

## 0 结论摘要

1. **原始冻结结果已可靠复现**：14/14 主机与板端分类/接受/原因一致；12 ON 中
   BPSK 3/3、QPSK 3/3、QAM64 3/3、QAM16 **0/3（全部判 QAM64，accepted）**；OFF 2/2 正确拒识。
2. **TX 真值/载荷/帧身份不是问题**：独立 truth 的 14 条 ID 均由 runner 记录
   `TX mode/seed/amplitude + capture_id + IQ SHA` 建立；QAM16 payload SHA 与 amp60 manifest
   相等；QAM16 真实帧对**理想 QAM16 载荷**的相关性远高于 QAM64（见 §3）。
3. **首要原因：接收 IQ 相对训练约定是共轭方向，且四类训练明确未建模该类 I/Q 通道失配。**
   QAM16 真实帧与 `conj(ideal QAM16)` 的归一化复相关为 **0.918–0.924**，与 normal 仅
   **0.469–0.475**；QAM64 同现象（conj QAM64 0.890–0.895）。训练 `generate.py` 的损伤顺序是
   `window offset -> phase -> CFO -> AWGN`，`CONTRACTS.md` 明确把 IQ 不平衡/DC/接收滤波/
   相位噪声列为 **unmodeled**，没有共轭/频谱反转训练样本。
4. **次要原因：QAM16 类的决策裕量小，真实通道残差把它推向 QAM64。** QAM16 的原始
   QAM16 分数仅 **0.089/0.102/0.185**，QAM64 分数 **0.911/0.898/0.815**；conjugate 诊断探针
   把 QAM16 分数提高到 **0.608/0.566/0.372**，修复 **2/3**，第 3 帧仍为 QAM64。QAM64 对
   同一变换仍稳定为 QAM64，说明不是简单阈值问题。
5. **最小修复方向：下一次候选模型必须加入共轭/频谱反转 + IQ 不平衡/接收滤波等真实通道增广，
   再冻结重验；单纯产品端共轭归一化只解决 2/3，不能作为验收修复。**
   **不**提高 TX 幅度、**不**改阈值、**不**用旧六类模型替换四类模型。

## 1 复现入口与产物

```bat
ml_training\.venv\Scripts\python.exe -B ml_training\fourclass_qam16_diagnosis\run_all.py
```

该入口：

1. 从 `run/fourclass.json` 的 TX mode/seed/amplitude + 帧 metadata 身份 + 实际 IQ SHA 生成
   独立真值侧文件 `results/tx_truth_side.json`；`capture_id` 跨会话重复，因此 key 始终包含
   **IQ SHA**；
2. 调用修复后的 `ml_training/real_campaign/task68.py` 冻结入口（`Round2Predictor`），
   输出 `results/task68_eval/task68_result.json/.md`；
3. 生成 `results/diagnosis.json`（逐帧统计/特征/相关性/诊断变换）与摘要。

`task68_eval` 摘要：`COMPLETED`，14 records / 14 evaluated / 0 invalid / 0 unevaluable；
clean 12（9 correct / 3 wrong），quality 2（2 rejected / 0 false accept）。

可直接重放入口：

```bat
ml_training\.venv\Scripts\python.exe ml_training\real_campaign\task68.py ^
  --frames-root evidence\rf-teaching\s2-46db-fourclass-real-rx-2026-10-22\run ^
  --truth-file ml_training\fourclass_qam16_diagnosis\results\tx_truth_side.json ^
  --out-dir ml_training\fourclass_qam16_diagnosis\results\task68_eval ^
  --source-id task68-real-rx
```

关键 SHA：

- `tx_truth_side.json`：`5a931ea3462620dda4a0bf9a463fcfc45620c31d08c78f2fe3d775ed496b5bb8`
- `task68_result.json`：`6a8f2edb0d9c1d0d0df9ea3f0b87cc4a846cebcd2d0f465b1c7860489566b903`
- `diagnosis.json`：`784896540c4e3414039b7b2f1704659523d75544615d9eeb53a12b8db7375ce9`
- runner `fourclass.json` 与 amp60 manifest 的 source SHA 记录在
  `results/truth_provenance.json`。

## 2 原始冻结结果（权威）

| 帧 | truth | board/host | accepted | QAM16 score | QAM64 score | 旧六类（仅对照） |
|---|---|---|---|---|---|---|
| s02-qam16-1 | QAM16 | **QAM64** | true | 0.0891 | 0.9109 | QAM16 |
| s02-qam16-2 | QAM16 | **QAM64** | true | 0.1018 | 0.8982 | QAM16 |
| s02-qam16-3 | QAM16 | **QAM64** | true | 0.1849 | 0.8151 | QAM16 |
| s03-qam64-1 | QAM64 | QAM64 | true | 0.0825 | 0.9175 | QAM64 |
| s03-qam64-2 | QAM64 | QAM64 | true | 0.0380 | 0.9620 | QAM64 |
| s03-qam64-3 | QAM64 | QAM64 | true | 0.0200 | 0.9800 | QAM64 |

- host/board 在 14/14 的 class、accepted、reason 上完全一致；不是 host 重放伪影。
- 旧六类 QAM16 3/3 判对，但**只作为对照**；它是另一模型/另一训练合同，不能作为四类 truth，
  也不能直接替换四类模型。
- OFF 两帧 UNKNOWN/accepted=false/noise_like；false_accept=0。

## 3 关键证据：接收共轭方向与训练域不匹配

对 14 帧运行 `Round2Predictor` 的冻结原始结果后，对 QAM16/QAM64 用 amp60 的
**真实 TX payload period** 做已知载荷相关性搜索（不是新帧，不是训练）：

| 真实帧组 | 对 ideal 同类 normal corr | 对 ideal 同类 **conj** corr | 对另一 QAM 类 conj corr |
|---|---:|---:|---:|
| QAM16 三帧 | 0.473 / 0.475 / 0.469 | **0.924 / 0.924 / 0.918** | 0.218 / 0.211 / 0.207（QAM64） |
| QAM64 三帧 | 0.483 / 0.485 / 0.488 | **0.895 / 0.895 / 0.890** | 0.212 / 0.213 / 0.229（QAM16） |

同期最佳 lag：QAM16 272–288 samples，QAM64 276–300 samples；分段相位跨度
QAM16 ≈ −0.002…+0.013 rad，QAM64 ≈ −0.006…+0.030 rad，诊断 CFO 估计约 ±0.1 kHz 量级。
也就是说：**帧身份/载荷正确，但接收波形是理想 TX 的共轭方向（频谱反转）**；不是 CFO 大偏差，
也不是采样率/符号率错误。

训练侧证据：

- `ml_training/data/generate.py`：损伤顺序 `window offset -> initial phase -> CFO -> AWGN`；
  `_clean_rotated` 只做相位/CFO 旋转，没有共轭/频谱反转。
- `ml_training/docs/CONTRACTS.md`：IQ 不平衡、DC 偏置、接收滤波器/RRC 失真、相位噪声等
  明确列为 **unmodeled**。
- 现有参考向量研究的 inversion 组是 BPSK 研究向量，**不是**四类模型训练数据。
- 真实板 RX 的频谱反转/共轭现象在既有板端记录中已有观测（方向 B 文档）。

## 4 为什么 QAM16 特别脆弱，QAM64 不受影响

相同真实通道下，QAM64 仍 3/3 正确，说明不是“所有高阶调制都不能用”，而是 QAM16 的边界更窄：

| 统计（原始冻结预处理后） | QAM16 真实帧 | ideal QAM16 | QAM64 真实帧 | ideal QAM64 |
|---|---:|---:|---:|---:|
| complex RMS after DC | 286.1–290.0 | 62.8（ref） | 248.2–250.0 | 55.3（ref） |
| max abs code | 671/706/833 | 60 | 797/836/724 | 60 |
| amp_entropy | 0.976 / 0.976 / 0.977 | 0.766 | 0.982 / 0.981 / 0.982 | 0.946 |
| amp_low_fraction | 0.189 / 0.189 / 0.192 | 0.258 | 0.199 / 0.190 / 0.199 | 0.185 |
| c40_abs | 0.586 / 0.561 / 0.385 | 0.669 | 0.238 / 0.251 / 0.270 | 0.550 |
| amp_kurtosis | 1.543 / 1.547 / 1.626 | 1.331 | 1.746 / 1.745 / 1.722 | 1.364 |

- QAM16 真实帧的幅度分布被真实通道“抹平”，`amp_entropy`、`amp_low_fraction`、`c40_abs`
  向 QAM64 分布靠拢；QAM64 本身幅度分布已经密集，因此通道失配不改变其 top-1。
- 8 参数 baseline 在 QAM16 上同样是 QAM64（baseline score QAM64 约 0.79–0.87），
  说明不是单一 CNN 的偶发问题，而是输入统计/决策边界问题。
- 该表是诊断解释，不是新 truth；幅度/RMS 不能作为真值，也不能据此调阈值。

## 5 已检查但排除/降级的因素

| 检查项 | 观察 | 判断 |
|---|---|---|
| TX truth / 帧身份 | 14 条 truth 由 runner mode/seed/amplitude + capture+IQ SHA 建立；payload SHA 等 amp60 | 不是误判原因 |
| 采样/格式/域 | 14/14 `input_domain VALID`；60 MSPS、decim=1、3 MSym/s、RECT、4096 样本 | 不是 domain 错误 |
| DC / RMS 归一化 | DC 非零但 `dc-complex-rms-v1` 逐分量去 DC；QAM16 RMS 286–290 > QAM64 248–250；gate_ok | 不是能量/DC 拒绝；也不是低 RMS |
| 削顶 | max abs 671/706/833 < 2047，无 clipping | 不是削顶 |
| 频谱 | QAM16 band_frac_3m 0.849–0.853，QAM64 0.815–0.820；谱熵 0.694–0.698 vs 0.709–0.713 | 无 gross 频谱异常；不是主因 |
| 时序/频偏 | best lag 272–300；相位跨度 ~±0.03 rad；CFO 诊断 ~±0.1 kHz | 无 gross CFO；不是主因 |
| RX expected_mode / demod | 全部 BPSK；demod `CARRIER_NOT_SYNCED/CFO_OUT_OF_RANGE` | 按设计是 BPSK 解调诊断，不是 truth；已由修复后的 task68 正确忽略 |
| board vs host | 14/14 一致 | 不是 host 重放伪影 |
| 旧六类 | QAM16 3/3 对 | 仅对照，不是 truth，不替换四类模型 |

## 6 诊断变换（不是新真实帧，不能算验收）

在三个 QAM16 真实帧上做的小型离线变换：

| 变换（诊断） | QAM16 三帧结果 | QAM16 score |
|---|---|---|
| raw | QAM64 / QAM64 / QAM64 | 0.089 / 0.102 / 0.185 |
| conjugate（Q 取反） | **QAM16 / QAM16 / QAM64** | 0.608 / 0.566 / 0.372 |
| swap（I/Q 交换） | QAM16 / QAM16 / QAM64 | 0.531 / 0.544 / 0.323 |
| Qneg | QAM16 / QAM16 / QAM64 | 0.608 / 0.566 / 0.372 |
| reverse（时间反转） | QAM16 / QAM16 / QAM64 | 0.693 / 0.605 / 0.439 |

- `conj` 对全部 12 ON 的诊断结果是 11/12（BPSK/QPSK/QAM64 保持正确；QAM16 2/3），
  但这是**诊断探针**，不是新采集，也不是独立验收，不能写成准确率。
- 第 3 帧在 conjugate 后仍 QAM64（QAM16 0.372 vs QAM64 0.628）；说明**单纯共轭归一化不够**。
- 全局相位旋转/理想相位对齐不能修复第 3 帧；不把“调相位”当作修复建议。
- 这些变换没有回写原始 IQ，没有改变 task68 原始结果；原始 frozen 结果始终以 §2 为准。

## 7 原因排序

### P1（高置信）：接收共轭/频谱反转 + 训练未覆盖共轭/IQ 通道

支持：QAM16/QAM64 均与理想载荷的共轭版本高度相关（0.89–0.92），normal 仅 0.47–0.49；
训练损伤没有共轭；QAM16 在 conjugate 诊断下 2/3 恢复；QAM64 对变换仍稳定。
反对/限制：理想 QAM16 的 conjugate 版本仍被判 QAM16；conjugate 单独不能修复第 3 帧。

### P2（高置信）：QAM16 类决策裕量小，真实通道残差使幅度统计接近 QAM64

支持：QAM16 原始分数低（0.089–0.185），QAM64 0.815–0.911；QAM16 真实幅度统计向
QAM64 移动；baseline 也判 QAM64；QAM64 仍 3/3。
反对/限制：残差/EVM 并非 QAM16 更大（QAM16 residual_ratio 0.387–0.400，QAM64 0.466–0.485）；
因此不是“更多噪声”单独导致，而是 QAM16 的判别边界对通道形状更敏感。

### 排除项

truth/身份/载荷错配、采样率/符号率/格式错误、能量门限/削顶、gross CFO/时序、板端重放差异
均不支持。

## 8 最小修复建议

1. **下一次模型候选（推荐主修复）**：在四类训练中增加
   **conjugate / spectral inversion** 以及 **IQ gain/phase imbalance、接收滤波/ISI、相位噪声**
   增广；保持阈值/能量门限不变；重新冻结后在独立真实帧上验证 QAM16。
2. **产品端临时探针（不是验收修复）**：如果确认板端 RX 固定为共轭方向，可在推理前显式做
   IQ conjugate 归一化并保留原始 IQ；但本数据上只能修复 2/3 QAM16，第 3 帧仍失败，因此
   **不能单独作为发布修复**。
3. **不要做**：提高 TX 物理幅度、降低能量门限、改四类阈值、用旧六类模型替换四类模型、
   把本 14 帧调参后的结果当独立验收。

## 9 真正需要补采的最小实验

在已有 14 帧只作开发诊断的前提下，下一次独立验证至少需要：

- **3 个新 QAM16 + 3 个新 QAM64** 真实帧，跨 **≥2 个会话/≥2 个 seed**，保持 amp60、同一
  RX 路径；truth 仍由 runner 记录的 mode/seed/amplitude + capture+IQ SHA 生成。
- 保留 **1 个 BPSK + 1 个 QPSK** 对照帧，确认修复不回归。
- 原始 IQ/metadata/truth 全保存；对新候选做 host 重放 + 真实板端只回放（不重训/不调阈值），
  独立比较 raw 与 conjugate-augmented 候选；不能把本 14 帧或变换副本当独立验收。
- 可选单变量控制：在授权允许时，仅**降低** QAM16 TX 幅度使 RX RMS 接近 QAM64，检查
  幅度相关压缩；不提高物理幅度，不改变现有阈值。

## 10 边界

- 本报告是**原因定位与修复建议**，不是新模型验收、不是统计 BER、不是四类总体准确率验收。
- 原始 frozen 结果与 14/14 board/host 一致性保持不变；所有变换结果标记为 diagnostic，
  不冒充新真实帧。
- 不重训、不扩大模拟数据、不新建完整 QAM 解调平台、不新增审查代理；真实帧不自动加入 TRAIN。
