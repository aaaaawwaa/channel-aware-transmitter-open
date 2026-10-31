# ARM 独立回放探针 — 命令与清理（由主项目会话执行，本任务不连板）

> 本文件是**软件包内的准备文档**。本轮任务只准备软件包，未连接板卡、未部署、未改产品六类合同。
> **主项目会话是唯一板卡操作者**；下面命令供其在获得授权后按需执行。

## 0. 包边界

- 包目录：`ml_training/arm_probe/`（自包含：`runtime/`、`model/`、`frames/`、`input_manifest.json`、`expected/`、`run_replay.py`、`arm/run_arm_probe.py`）。
- 目标环境（文档记录）：板端 **CPython 3.8.2 / NumPy 1.17.4 / ARMv7l**（来源：`docs/reports/2026-10-03-rx-task8-10-followup-audit-acceptance.md` 等历史验收记录；执行前用 `python3 -V`、`python3 -c "import numpy; print(numpy.__version__)"` 现场复核）。
- 本探针是**纯 Python/NumPy**回放与计时，不是板端 C 加速产品运行时，也不改六类合同。

## 1. 有界命令（占位符 `<BOARD>`、`<USER>`、`<NONCE>` 由执行者填写）

```bash
# 1) 复制到板端 /tmp（只写 /tmp，不碰发布目录）
scp -r ml_training/arm_probe <USER>@<BOARD>:/tmp/rf-ml-arm-probe-<NONCE>

# 2) 有界正确性回放：超时 120 s，--check 必须 exit 0
ssh <USER>@<BOARD> 'cd /tmp/rf-ml-arm-probe-<NONCE> && \
  timeout 120 python3 run_replay.py --check --out /tmp/rf-ml-arm-probe-<NONCE>/results.json'

# 3) 有界计时/RSS：最多 20 重复 × 43 帧，墙钟预算 120 s，提前超预算会标记 budget_exceeded
ssh <USER>@<BOARD> 'cd /tmp/rf-ml-arm-probe-<NONCE> && \
  timeout 300 python3 arm/run_arm_probe.py --repeats 20 --warmup 5 \
    --budget-seconds 120 --out /tmp/rf-ml-arm-probe-<NONCE>/arm_results.json'

# 4) 取回结果（只读 JSON；不写板端发布目录）
scp <USER>@<BOARD>:/tmp/rf-ml-arm-probe-<NONCE>/{results.json,arm_results.json} ./arm_probe/<NONCE>/

# 5) 清理：删除临时目录（含 __pycache__、results）
ssh <USER>@<BOARD> 'rm -rf /tmp/rf-ml-arm-probe-<NONCE>'
```

可选更小边界：`--max-frames 20 --repeats 10 --budget-seconds 60`（仍覆盖各 group 的子集，须在结果 JSON 的 `config` 中如实记录）。

## 2. 通过判据（预先写死，不按结果回调标准）

1. `run_replay.py --check` exit code = **0**，且输出 `CHECK PASSED`；
2. 标签 / 拒识原因 / 仲裁标志与 `expected/host_expected.json` **完全一致**；
3. logit 差 ≤ **1e-3**、score 差 ≤ **1e-6**、频谱特征差 ≤ **1e-6**（包内默认容差；超限即失败并保留原始 JSON）；
4. `arm_results.json` 存在且 `expected_label_checks.mismatch_count == 0`；
5. 计时/RSS 只如实记录（探针的 ARM 数字，不外推为产品运行时或板卡端到端指标）。

## 3. 不做/禁止

- 不安装/替换发布、不加载 Overlay、不布防/发射/采集、不改产品六类合同或运行时；
- 不修改 `ml_training/arm_probe` 内的冻结 bin；不回写成功结果；
- 失败时只保留 `/tmp` 原始 JSON 并报告，不重试凑通过；
- 清理必须删除 `/tmp/rf-ml-arm-probe-<NONCE>`，不在板端遗留包或缓存。

## 4. 结果回收

回收 `results.json` 与 `arm_results.json` 后，交回本会话（Lead）做一次增量核对即可；不新增多轮审查。
