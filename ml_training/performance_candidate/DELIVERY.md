# 交付：冻结识别方案等价性能优化（主项目）

> 本文件是提交给主项目的最小交付索引；不包含产品改动。
> **优化研究基线候选：`099174be0cb38a139ea95926394fcf0e`（`cnn-bpsk-modrec4-v1`）。**
> 主项目当前候选已变化；本交付**不是**新候选资格，禁止把 `099174be` 的旧结果直接标成
> 新候选的 ARM/性能资格。下一轮必须用当前候选重新绑定并运行同一对照。

## 1. 最小 adapter 修改说明

只改 `ModRec4Adapter.evaluate_iq` 的内部计算复用，不改：

- 权重/阈值/规则（仍用候选 `modrec4_*.json`）；
- 输入域校验与格式/长度/采样域/符号域；
- 最终标签、接受/拒识、拒识原因、仲裁来源、帧 identity、contract、passthrough、provenance；
- `pure_class_hint` 仍保留并等价。

原路径每帧重复 4 次 `preprocess_iq`、3 次 CNN forward、2 次 baseline、2 次 spectral FFT；
优化为单次共享 preprocess/CNN/softmax/spectral/baseline，已与候选原实现逐字段 0 mismatch。

详见 `MINIMAL_DIFF.md`。实际代码：`optimized_adapter.py`（+ `optimized_fullpath.py` 仅用于
同边界全路径对照）。

## 2. 必要文件

| 文件 | 用途 | 是否进入产品发布 |
|---|---|---|
| `optimized_adapter.py` | 最小 adapter 修改实现（研究原型） | 仅作为修改参考；需主项目按发布流程重写/审阅后进入候选，不能直接覆盖 |
| `MINIMAL_DIFF.md` | 差分与安全论证 | 否 |
| `results/parity.json` | 70 case 行为一致性（0 mismatch） | 否（证据） |
| `results/fullpath_parity.json` | 4/4 full-path 输出一致性 | 否（证据） |
| `results/summary.json` | 主机计时/一致性机器摘要 | 否（证据） |
| `reports/performance_report.md` | 完整主机报告与 ARM 入口 | 否 |
| `reports/LEAD-CHECK.md` | 一次增量核对 | 否 |
| `arm_compare.py` | 下一轮 ARM 最小对照入口（dry-run/execute） | 否（研究工具） |
| `results/arm_dryrun/plan.json` | 离线 dry-run 的具体文件/路径/超时/回收/清理计划 | 否（计划证据） |
| `measure*.py`, `eval_cases.py`, `compare*.py` | 主机/ARM 复现测量 | 否 |
| `reference/` | 候选 14 文件逐 SHA 只读快照 | **否；研究夹具，不进入产品发布** |

## 3. 实际运行命令

主机复现（不连接板卡）：

```bat
ml_training\.venv\Scripts\python.exe ml_training\performance_candidate\run_all.py
```

下一轮 ARM（本会话只 dry-run，未连接板卡；需主项目授权后用当前候选）：

```bat
python ml_training\performance_candidate\arm_compare.py --dry-run
python ml_training\performance_candidate\arm_compare.py --execute --candidate <current candidate release> --frames-dir <same 4 IQ> --arm-dir ml_training\arm_probe --fixtures-dir ml_training\real_campaign\fixtures\synthetic --run-id <fresh-id>
```

`--execute` 复用项目认可的 `pynq_ssh.py` Paramiko helper（非 Windows ssh/scp）；只写板端
`/tmp/rf-perf-<run_id>`，跑完 `rm -rf` 清理，不安装候选、不执行 RF、不改 current/服务。命令行
传送文件、命令超时、结果回收和清理均在 plan 中明确。

## 4. 已完成的等价性证据（主机）

- 行为一致性：**70/70 case 0 mismatch**；所有数值字段最大差 **0.0**。
  覆盖 4 个候选帧、43 个 arm_probe 帧（clean/rejection/stress）、4 个合成合法四类、2 个合成
  错误输入、17 个输入域/能量/频谱/削顶边界。
- full-path：**4/4 帧 0 mismatch**（六类、环境、解调、modrec4、身份）。
- 主机耗时（仅线索）：modrec4 median **13.38 → 6.27 ms（−53.1%）**；full-path median
  **38.38 → 30.44 ms（−20.7%）**；adapter median **9.46 → 4.68 ms（−50.5%）**。
- **未执行 ARM；无 ARM 加速声明。** ARM 对照必须在当前候选上分别测 baseline/optimized 的
  median/P95/RSS、加载耗时和输出一致性后再判断。

## 5. 边界与警告

- 候选/产品/冻结模型/证据均未修改；`reference/` 不进入产品发布。
- 当前主项目候选已变化：`arm_compare.py` 会把传入候选与 `099174be` 对比并在 plan 中标记
  `candidate_changed`；结果只对传入候选负责。
- BPSK 历史 29/4002 仅为已有 IQ 的离线 BER 数值复核，不是完整统计 BER 验收 PASS；修正时序后
  包 #5 `on_aligned=23<27` FAIL（详见 `real_campaign/docs/collection_prep.md`）。
