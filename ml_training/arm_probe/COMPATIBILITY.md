# 板端 Python / NumPy 兼容性核对（冻结回放包）

## 1. 目标环境（文档记录，执行前现场复核）

| 项 | 值 | 来源 |
|---|---|---|
| 板端 Python | CPython **3.8.2** | `docs/reports/2026-10-03-rx-task8-10-followup-audit-acceptance.md`（ARMv7l、Python3.8.2、NumPy1.17.4）；`docs/superpowers/plans/2026-10-03-bpsk-receiver-implementation.md` |
| 板端 NumPy | **1.17.4** | 同上；`docs/reports/2026-10-03-task8-9-review-task10-continuation.md` 实测 |
| 架构 | ARMv7l | 同上 |
| 安装/运行 | 板端系统 Python，无 torch/scipy 依赖 | 包内 runtime 只 import `numpy` + 标准库 |

> 注意：板端实际运行的是 C 加速产品 runtime；本回放包是**纯 Python/NumPy 探针**，只用于独立
> 回放资格，不代表产品运行时或端到端指标。执行前用
> `python3 -V` 与 `python3 -c "import numpy; print(numpy.__version__)"` 复核，若版本不同则在结果 JSON 中记录。

## 2. 包内静态检查（构建时执行，产物在 `host/compatibility_check.json`）

- `run_replay.py`、`runtime/*.py`、`arm/run_arm_probe.py` 全部用
  `ast.parse(..., feature_version=(3, 8))` 检查 **Python 3.8 语法**：PASS；
- 禁止依赖扫描：**无** `torch` / `scipy` / `sklearn` / `pandas` / `ml_training` 导入（纯 NumPy + 标准库）；
- NumPy API 使用均早于 1.17：`np.ndarray` 基本运算、`np.dot`、`np.pad`、
  `np.lib.stride_tricks.as_strided`、`np.maximum`、`np.mean`、`np.fft.fft/fftshift/fftfreq`、
  `np.abs`、`np.sort`、`np.median`、`np.exp`、`np.argmax`、`np.bincount`、`np.digitize`、
  `np.angle`、`np.asarray`、`np.float32/float64`（无 `sliding_window_view`、无 `np.typing`、
  无 Python 3.9+ 语法必需特性）。
- `math.prod`（`numpy_runtime` 的模型校验）在 Python 3.8 已提供；`from __future__ import annotations`
  在 3.8 可用。

## 3. 主机干净目录回放（已完成，构建时执行）

- 将整个包复制到系统临时目录（不引用项目树、清除 `PYTHONPATH`、`python -I`）后运行
  `run_replay.py --check`：**PASS**，JSON 见 `host/host_clean_run.json`，日志 `host/host_clean_run.log`。
- 包内 runtime 与项目冻结 runtime 在 43 帧上交叉校验：**0 mismatch**、`max_abs_logit_delta = 0.0`
  （同一主机 NumPy），见 `host/project_cross_check.json`。

## 4. 数值验收口径（ARM 上预写死）

| 项 | 要求 |
|---|---|
| 帧输入 | 16,384 B、`s16_iq_le`、(4096,2)、signed-12；逐帧 SHA-256 必须等于 `input_manifest.json` |
| 标签 / 拒识原因 / 仲裁标志 | 必须与 `expected/host_expected.json` **完全一致** |
| CNN logit | max abs delta ≤ **1e-3** |
| softmax score | max abs delta ≤ **1e-6**（默认；如 ARM 差异导致超限，记录而不改期望值） |
| 频谱特征 | max abs delta ≤ **1e-6** |
| 运行时 | `run_replay.py --check` exit 0；`arm_results.json` 中 `expected_label_checks.mismatch_count == 0` |

**已知风险**：ARM NumPy 1.17 与主机 NumPy 2.5 的 BLAS/FFT 实现可能带来末位差异；若标签在阈值边缘
翻转，则按“与期望不一致”失败处理并保留原始 JSON，不通过改阈值/期望值追认通过。干净分类与
干扰压力、门拒识分开报告，环境标签不作为调制真值。

## 5. 本任务状态

- 主机干净目录回放：**完成/PASS**（见 §3）；
- ARM 实际运行、板端 NumPy 差异、ARM 时延/RSS：**NOT_RUN**（本轮不连接板卡；命令与清理见
  `arm/ARM-COMMANDS.md`，由主项目会话作为唯一板卡操作者执行）。
