# 冻结识别方案等价性能优化研究（`performance_candidate/`）

> 候选 `099174be0cb38a139ea95926394fcf0e`（`cnn-bpsk-modrec4-v1`）只读使用；**未安装、
> 未连接板卡、未重跑 ARM、未修改候选/产品/冻结模型/证据**。本目录是实现、测量和一致性
> 验证原型；产品下一轮接入由主项目会话决定。

## 结论

候选 full-path 的 modrec4 阶段在 adapter 内部重复计算：每帧 4 次预处理、3 次 CNN 前向、
2 次 baseline、2 次 spectral FFT。优化原型基于**同一个候选参考快照**（14 个文件逐 SHA
相同），把共享中间量计算一次并等价推导 `before`/`after`/`pure_class_hint`/`spectral`；
不删必要分析、不缩短输入、不改阈值、不增加拒识。

- 一致性：**70 个 case 0 mismatch**（候选帧、43 个 arm_probe 帧、合成夹具、17 个输入域/
  阈值边界反例），所有数值字段最大差 **0.0**；full-path **4/4 帧 0 mismatch**。
- 主机（仅线索，非 ARM）：modrec4 阶段 median **13.38 → 6.27 ms（−53.1%）**；
  full-path total median **38.38 → 30.44 ms（−20.7%）**；adapter median
  **9.46 → 4.68 ms（−50.5%）**；clean/rejection/stress 三组 adapter 分别约
  −61.3% / −57.0% / −62.6%。主机 run-to-run 有波动，以 `results/summary.json` 与报告为准。
- **未执行 ARM 验证，不宣称 ARM 已加速**。下一轮具体命令见
  [reports/performance_report.md](reports/performance_report.md) §6。
- 原 **63.24 ms** 是 43 帧独立模型探针，不是 full-path；与候选 ARM full-path
  **374.27 ms median / 424.65 ms P95 / 31.68 MiB** 不能直接比较（后者来自既有证据，本任务
  未重跑）。

## 一键复现（主机）

```bat
ml_training\.venv\Scripts\python.exe ml_training\performance_candidate\run_all.py
```

会重新运行：只读耗时分解 → 4 帧 full-path 前后计时 → 43 帧 adapter 分组计时 → 70 case
行为一致性 → full-path 结果一致性，并生成：

- `results/summary.json`、`results/profile_before.json`、`results/host_*`、
  `results/groups_*`、`results/parity.json`、`results/fullpath_parity.json`；
- `reports/performance_report.md`（含下一轮 ARM 命令）。

查看单步：

```bat
ml_training\.venv\Scripts\python.exe ml_training\performance_candidate\profile_breakdown.py
ml_training\.venv\Scripts\python.exe ml_training\performance_candidate\measure.py --mode baseline --candidate <release> --frames-dir <frames> --tags nf-00-normal nf-01-narrowband nf-02-narrowband nf-03-narrowband --out results/before.json
ml_training\.venv\Scripts\python.exe ml_training\performance_candidate\measure.py --mode optimized --candidate <release> --frames-dir <frames> --tags nf-00-normal nf-01-narrowband nf-02-narrowband nf-03-narrowband --out results/after.json
```

默认候选路径：`evidence/rf-teaching/s2-46db-modrec4-product-2026-10-21/candidate/releases/v3/out/releases/099174be0cb38a139ea95926394fcf0e`；
默认帧：`evidence/rf-teaching/s2-46db-80mhz-narrowband-2026-10-21/raw`。

## 下一轮 ARM 对照入口（本会话只 dry-run）

```bat
rem 本会话已执行：只校验本地输入、生成 plan，不连接板卡
python ml_training\performance_candidate\arm_compare.py --dry-run
python ml_training\performance_candidate\arm_compare.py --execute --candidate <current candidate release> --frames-dir <same 4 IQ> --arm-dir ml_training\arm_probe --fixtures-dir ml_training\real_campaign\fixtures\synthetic --run-id <fresh-id>
```

- `arm_compare.py` 复用项目认可的 `pynq_ssh.py` Paramiko helper，不使用 Windows ssh/scp；
  `--execute` 才连接，本会话未执行。
- plan：[results/arm_dryrun/plan.json](results/arm_dryrun/plan.json)，明确本地上传文件
  SHA/大小、远端路径、上传/命令超时、结果回收清单和 `/tmp/rf-perf-<run_id>` 清理。
- 对照使用同一候选、同一批 4 帧 IQ、相同计时边界（warmup 5/repeats 20）；输出 baseline/
  optimized 的 median/P95/RSS、加载耗时、逐帧输出一致性。研究基线是 `099174be`；当前候选
  变化时不得把旧结果当新候选资格。

## 文件

| 文件 | 作用 |
|---|---|
| `profile_breakdown.py` | 只读动态调用计数 + cProfile + 阶段耗时，证明重复工作 |
| `optimized_adapter.py` | 单次共享计算原型（覆盖 `evaluate_iq`，继承候选 metadata/unavailable/passthrough） |
| `optimized_fullpath.py` | 与候选同阶段/同输入/同计时边界的 full-path 包装，只替换 modrec4 adapter |
| `reference/` | 候选 11 个 vendored Python + 3 个模型 JSON 的只读快照（逐 SHA 相同） |
| `measure.py` | full-path 前后计时（warmup 5 / repeats 20，同 `out.total_seconds`） |
| `measure_adapter.py` | adapter 单独计时（同输入、同 warmup/repeats） |
| `measure_groups.py` | 43 个 arm_probe 帧按 clean/rejection/stress 分组计时 |
| `eval_cases.py` | 70 case 行为输出（baseline/optimized 分进程） |
| `compare_cases.py` | 标签/原因/仲裁/输入域/passthrough + 数值容差一致性 |
| `compare_fullpath.py` | 4 帧 full-path 六类/环境/解调/modrec4/身份一致性 |
| `run_all.py` | 一键跑全部主机测量并生成报告 |
| `arm_compare.py` | 下一轮 ARM 最小对照入口；默认 `--dry-run`，`--execute` 才经 `pynq_ssh.py` 连接 |
| `results/arm_dryrun/plan.json` | 离线 dry-run 的文件清单/哈希/远端路径/超时/回收/清理计划 |
| `MINIMAL_DIFF.md` | 最小差分与安全论证 |
| `DELIVERY.md` | 给主项目的最小交付索引（含旧候选警告） |
| `results/` / `reports/` | 生成物 |

## 验证口径

- 相同输入：候选 4 帧（normal + narrowband stress）；43 个 arm_probe 帧（clean/rejection/
  stress）；4 个合成合法四类、2 个合成错误输入、17 个输入域/阈值边界反例。
- 相同计时范围：full-path 五阶段合计，warmup 5/repeats 20；adapter 单独同 warmup/repeats。
- 数值容差沿用既有 full-path 口径：logit ≤1e-3，score/spectral ≤1e-6；本轮实际最大差
  均为 0.0；标签/原因/仲裁/passthrough/contract/provenance 要求 exact。
- 不通过删除分析、缩短输入、放宽阈值或增加拒识提速：原型保留 `pure_class_hint`、全部
  gate/spectral/baseline/arbitration 逻辑，只是共享同一份中间量。

## 边界

- 本任务不连接板卡、不执行任何 RF、不安装候选、不重复 ARM 测试。
- 主机加速只能作为方向线索；ARM 对照入口为 `arm_compare.py`，本会话只运行了 `--dry-run`，
  计划见 `results/arm_dryrun/plan.json`；实际 `--execute` 需主项目授权并使用当前候选。
- 优化研究基线为候选 `099174be`；当前主项目候选已变化时，不得把本目录旧结果当新候选资格。
- 候选、`integration_candidate`、`arm_probe`、冻结模型/规则、产品文件、evidence 均未修改；
  `reference/` 是研究夹具，不进入产品发布。
