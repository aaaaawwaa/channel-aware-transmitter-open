# Lead 一次增量核对（live_worker_smoke）

日期：2026-10-22；范围：`ml_training/live_worker_smoke/` 实际 worker 合同加载的无 RF 回归。
仅新增本目录；未修改产品/候选/模型/阈值/冻结数据/旧证据；未连接设备、未启动 worker 主循环、
未执行 ARM/性能/RF；未新增审查代理。

| 检查 | 结果 |
|---|---|
| 入口 | `python ml_training/live_worker_smoke/run.py` → `COMPLETED` |
| 发布校验 | ad37bad4 的 `verify_release(require_software=True)` 通过；worker 从发布目录实际加载 |
| 独立进程 | 每个 release 由 `run.py` 启动独立子进程；基线与后续候选不共享模块缓存 |
| 测试客户端 | 禁止硬件交互的 client；本次调用次数 **0**，无 arm/control/TX/capture |
| ad37bad4 基线 | demod / 六类模型 / 环境 / 教学记录 **不可用**（分别 `解调未接入` / `无模型` / `环境诊断未接入` / `教学模块未接入`）；modrec4 **可用** |
| 与真实失败对应 | 与 task-58 的 `解调未接入` 失败一致；补出六类/环境/教学三条离线证据 |
| 新候选 | 未提供；入口已支持 `--release <new>`，修复后预期 `ALL_AVAILABLE`，不通过则逐条报告原因 |
| 写范围/设备 | 只写本目录；无设备连接；无候选/证据修改；无模型/阈值/RF 动作 |

结果：[results/ad37bad4f6b0a6e3ba707a95be7fc196/worker_smoke.md](results/ad37bad4f6b0a6e3ba707a95be7fc196/worker_smoke.md)、
[results/batch.json](results/batch.json)。

等待主项目新候选后，用同一入口在独立进程重跑并报告修复结果。
