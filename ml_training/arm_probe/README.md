# ml_training/arm_probe — 冻结第二轮方案的独立 ARM 回放包

> **本任务只准备软件包**：不连接板卡、不部署、不改产品六类合同/运行时；板卡操作由主项目会话
> 作为唯一操作者执行。包为纯 Python/NumPy 探测，不是板端 C 加速产品运行时。

## 目标

为冻结的第二轮方案提供一个**自包含、可独立运行**的最小回放包，用于后续“独立 ARM 回放资格”：

- 冻结 CNN：`model/cnn_model.json`（`25a65fb1…`）
- 冻结基线：`model/baseline_model.json`（`caa82589…`）
- 冻结规则/阈值：`model/rule_config.json`（`9aceebbd…`）
- 输入清单：`input_manifest.json`（43 帧，逐帧 SHA-256）
- 主机期望输出：`expected/host_expected.json` + `expected/host_summary.md`
- 运行入口：`run_replay.py`（主机/板端同一命令）
- 包内纯 NumPy 运行时：`runtime/`（冻结推理路径的包内拷贝，无 torch/scipy/product 依赖）
- 有界 ARM 计时/RSS 探针：`arm/run_arm_probe.py`，命令与清理：`arm/ARM-COMMANDS.md`
- 构建清单：`MANIFEST.json`（所有文件 SHA-256、帧数/分组、host 校验状态）

## 帧覆盖（43 帧，干净分类与干扰压力分开）

| 分组 | 帧数 | 说明 | 计入干净准确率？ |
|---|---|---|---|
| `clean_real_bpsk` | 10 | 真实物理 BPSK normal（含 2026-10-21 新 normal） | 是（真值 BPSK） |
| `clean_failure_bpsk` | 10 | 首轮失败会话 train-02 的 10 个 BPSK 帧（round-1 CNN 曾全判 QPSK） | 是（真值 BPSK） |
| `clean_synthetic_4class` | 8 | round-2 新种子 test，每类 2 帧 @ SNR 20 dB | 是（真值为对应类） |
| `gate_off` | 3 | 真实 OFF 物理帧 | 否（门拒识） |
| `gate_tone` / `gate_noise` / `gate_clip` | 3+3+3 | round-2 gate_test 纯单音/纯噪声/削顶 | 否（门拒识） |
| `stress_narrowband` | 3 | 2026-10-21 真实 BPSK+6 MHz tone 干扰帧 | **否**（单列压力） |

主机冻结管线期望（由包内 runtime 生成，构建时与项目 runtime 交叉校验）：

- 干净合计：BEFORE 14/28 → **AFTER 28/28**（真实 BPSK 6/10→10/10；失败回放 0/10→10/10；合成四类 8/8→8/8）；
- 门：OFF 3/3 `noise_like`、TONE 3/3 `tone_like`、NOISE 3/3 `noise_like`、CLIP 3/3 `clipping`；
- 干扰压力：3/3 输出 BPSK 接受（底层调制为 BPSK，但**不计入**干净四类准确率）。

## 主机使用

```bat
rem 从项目根目录；包内 runtime 不需要项目其它模块
ml_training\.venv\Scripts\python.exe ml_training\arm_probe\run_replay.py --check --out ml_training\arm_probe\host\host_results.json
ml_training\.venv\Scripts\python.exe ml_training\arm_probe\arm\run_arm_probe.py --repeats 5 --budget-seconds 30 --out ml_training\arm_probe\host\host_timing.json
```

`--check` 会与 `expected/host_expected.json` 比较：标签/拒识原因/仲裁标志必须完全一致，
logit ≤ 1e-3、score ≤ 1e-6、频谱特征 ≤ 1e-6。

## 板端使用

只在获得板卡授权后、由**主项目会话**执行；本轮不执行。命令、边界与清理见
**[arm/ARM-COMMANDS.md](arm/ARM-COMMANDS.md)**。目标板端环境为 CPython 3.8.2 / NumPy 1.17.4 /
ARMv7l（见 [COMPATIBILITY.md](COMPATIBILITY.md)）。

## 构建/刷新

```bat
ml_training\.venv\Scripts\python.exe ml_training\arm_probe\build_package.py
```

构建脚本会：校验冻结哈希 → 复制模型/帧 → 生成输入清单 → 用包内 runtime 生成主机期望 →
与项目冻结 runtime 交叉校验 → Python 3.8 语法/纯 NumPy 静态检查 → 复制到临时干净目录执行
`run_replay.py --check`（并记录日志）→ 写 `MANIFEST.json`。构建只写 `ml_training/arm_probe/`
与一个临时目录（随后删除）。

## 纪律

- 不重训、不重新标定；模型/基线/规则为冻结拷贝；
- 干净分类、门拒识、干扰压力三组分开报告；环境标签不作为调制真值；
- 不在板端安装/替换发布、不加载 Overlay、不布防/发射/采集、不改产品六类合同；
- 失败保留原始 JSON，不重试凑通过；结果交回 Lead 一次增量核对。
