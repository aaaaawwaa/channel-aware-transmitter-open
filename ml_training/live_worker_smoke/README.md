# live worker 合同加载无 RF 回归（`live_worker_smoke/`）

> 无设备、无控制/TX/采集、无安装、无 RF、无模型/阈值改动。使用**实际发布目录**内
> 的 `rf_status_worker.py` 及其真实 `_ensure_*` 加载方法；客户端替换为禁止外部交互的测试
> 替身，但**不替换加载方法、不伪造 available、不跳过发布校验**。

## 1. 目的

主项目真实单帧尝试在 `ad37bad4` 上失败于 worker 合同白名单（demod 返回“解调未接入”）。
本目录提供一个最小的、可复用的离线回归：

- 对真实发布运行 `merged_release.verify_release(..., require_software=True)`；
- 从发布目录按实际 worker 模块加载 `RfStatusWorker`；
- 用禁止硬件交互的 client，调用 worker 的真实：
  `_ensure_demod()`、`_ensure_model()`、`_ensure_environment()`、`_ensure_teaching()`、
  `_ensure_modrec4()`；
- 读取各自 `*_status()`，报告 `available`、`release_id`、`reason`；
- 每次检查一个发布；不同发布由 `run.py` 启动独立子进程，避免模块缓存串版本。

## 2. 运行入口

```bat
rem 基线 ad37bad4（默认）
ml_training\.venv\Scripts\python.exe ml_training\live_worker_smoke\run.py

rem 主项目提供新候选后，在独立进程中验证修复
ml_training\.venv\Scripts\python.exe ml_training\live_worker_smoke\run.py --release <new release dir>

rem 一次跑多个发布（每个发布一个独立子进程）
ml_training\.venv\Scripts\python.exe ml_training\live_worker_smoke\run.py --release <A> --release <B>
```

输出：

- `results/<release_id>/worker_smoke.json` / `.md`：单发布合同状态；
- `results/batch.json`：批次、子进程返回码、每个发布的 unavailable 列表。

## 3. 基线结果（ad37bad4）

release `ad37bad4f6b0a6e3ba707a95be7fc196`，contract `cnn-bpsk-modrec4-v1`，
`verify_release(require_software=True)` **通过**；worker 从该发布真实文件加载。

| 合同 | available | reason |
|---|---|---|
| BPSK demod | **False** | 解调未接入 |
| 六类模型 | **False** | 无模型 |
| 环境 | **False** | 环境诊断未接入 |
| 教学记录 | **False** | 教学模块未接入 |
| modrec4 | **True** | 四类扩展已加载 |

- 硬件测试 client 调用次数：**0**（没有 arm/control/TX/capture）。
- 子进程：独立进程；发布校验未被跳过；无 pycache 写入发布。
- 只读代码对照：ad37bad4 的 worker 在 demod/六类/环境/教学分支的 contract 白名单未覆盖
  `cnn-bpsk-modrec4-v1`；modrec4 分支正常。这是本次离线复现的直接原因，不是模型/阈值问题。
- 这与 task-58 真实运行在 `ad37bad4` 上得到的 `BPSK engine not available:
  {'available': False, 'reason': '解调未接入', ...}` 一致，并补齐了六类/环境/教学三个合同
  的离线证据。

结果：[results/ad37bad4f6b0a6e3ba707a95be7fc196/worker_smoke.md](results/ad37bad4f6b0a6e3ba707a95be7fc196/worker_smoke.md)。

## 4. 新候选验证口径

主项目提供新候选后，直接运行：

```bat
ml_training\.venv\Scripts\python.exe ml_training\live_worker_smoke\run.py --release <new release dir>
```

- 期望五个合同全部 `available=true`（`status=ALL_AVAILABLE`）；若仍有个别 `False`，结果会
  逐合同给出真实 `reason`，不调整候选/不重试。
- 若新发布中的 worker 仍使用旧加载方式，或合同白名单未覆盖
  `cnn-bpsk-modrec4-v1`，本回归应复现对应 unavailable，而不是被客户端/后处理掩盖。
- 不同发布分开输出，不复用旧候选的模块或结果。

## 5. 边界

- 只新增本目录；未修改产品、候选、模型、阈值、冻结数据或旧证据。
- 不连接板卡/Pluto/daemon，不启动 worker 主循环，不安装，不执行 ARM/性能/RF。
- 这是合同加载回归，不是硬件、真实 IQ、ARM 或产品资格验收；不新增审查代理。
