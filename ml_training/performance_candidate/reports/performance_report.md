# 冻结识别方案等价性能优化研究（主机）
> 候选 `099174be` 只读；未安装、未连接板卡、未重跑 ARM、未修改产品/模型/证据。主机加速只作线索，不宣称 ARM 已加速。
## 1. 结论
- 定位：候选 full-path 的 modrec4 阶段在 adapter 内部重复做 4 次预处理、3 次 CNN 前向、2 次 baseline、2 次 spectral FFT；`classify` 的输出只用于 `pure_class_hint`。
- 优化：单次共享 preprocess/CNN/softmax/spectral/baseline，一次推导 `before`/`after`/`pure_class_hint`；不删必要分析、不缩短输入、不改阈值、不增加拒识。
- 等价性：70 个 case **0 mismatch**，所有数值字段最大差 **0.0**；full-path 4/4 帧 0 mismatch。
- 主机：full-path 总时延 median **38.38 → 30.44 ms（−20.7%）**；modrec4 阶段 median **13.38 → 6.27 ms（−53.1%）**；adapter median **9.45 → 4.68 ms（−50.5%）**。

## 2. 耗时定位证据
候选 ARM full-path（既有证据，本任务未重跑）：median **374.27 ms**、P95 **424.65 ms**、峰值 RSS **31.68 MiB**。

| ARM 阶段 | median ms |
|---|---:|
| analysis | 28.250 |
| six_class | 63.073 |
| bpsk | 19.435 |
| environment | 113.673 |
| modrec4 | 149.926 |

- 既有 wall invocation（含 release 校验/模型加载）median **1333.70 ms**，明显高于五阶段合计；冷启动/文件校验单独报告，不混入稳态阶段。
- 重复模型加载/文件校验主要发生在 harness 每次调用（wall 含 release verify + model load），不在五阶段稳态计时内；本任务只优化稳态 adapter，不改变加载/身份语义。
- 原 **63.24 ms** 是 43 帧独立模型探针，不是 full-path，不能与新候选 424.65 ms 直接比较。

### 重复工作量（主机动态计数，逐帧一致）
| 调用 | 优化前每帧次数 |
|---|---:|
| preprocess_iq | 4 |
| cnn.forward | 3 |
| baseline.scores | 2 |
| spectral_features | 2 |
| predictor.decide | 1 |
| predictor.classify | 1 |
| apply_gate | 1 |
| softmax | 2 |

- 静态调用点：`{'preprocess_iq_occurrences': 2, 'spectral_features_occurrences': 1, 'decide_occurrences': 1, 'classify_occurrences': 1, 'before_decision_occurrences': 2, 'contract_map_occurrences': 3, 'passthrough_occurrences': 2}`；`classify` 结果只进入 `pure_class_hint`，不是产品最终标签/仲裁来源。

## 3. 优化原型与最小差分
- 原型：`optimized_adapter.py`（单次共享计算，覆盖 evaluate_iq）+ `optimized_fullpath.py`（同阶段/同输入/同计时边界的全路径包装）。
- 参考快照 `reference/` 与候选 11 个 vendored 文件、3 个模型 JSON 逐文件 SHA-256 一致（integrity=True）；未修改权重/阈值/输入域。
- 差分只涉及 `ModRec4Adapter.evaluate_iq` 内部计算复用；输出字段、passthrough、contract、provenance 保持。详见 `MINIMAL_DIFF.md`。

## 4. 公平验证
- 相同输入：候选 4 帧（normal + 3 narrowband stress）；43 个 arm_probe 帧（clean / rejection / stress）；合成夹具与输入域/阈值边界反例。
- 相同计时范围：full-path `out.total_seconds` 五阶段合计 + 相同的 warmup 5 / repeats 20；adapter 单独 measure 使用相同 warmup/repeats。
- 结果：70 case 0 mismatch；max numeric delta 0.0；full-path 4/4 0 mismatch。

## 5. 主机前后耗时（仅线索）
| 指标 | 优化前 | 优化后 | 变化 |
|---|---:|---:|---:|
| full-path total median | 38.38 ms | 30.44 ms | −20.7% |
| full-path total P95 | 41.71 ms | 33.56 ms | −19.5% |
| modrec4 stage median | 13.38 ms | 6.27 ms | −53.1% |
| adapter median | 9.45 ms | 4.68 ms | −50.5% |

| 样本类 | 优化前 adapter median | 优化后 | 变化 |
|---|---:|---:|---:|
| clean | 11.55 ms | 4.47 ms | −61.3% |
| rejection | 9.50 ms | 4.08 ms | −57.0% |
| stress | 11.70 ms | 4.38 ms | −62.5% |

## 6. 下一轮 ARM 对照：最小入口（本会话只 dry-run）
- 优化研究基线固定为候选 **099174be**；主项目当前候选可能已变化，本报告不把旧候选结果当新候选资格。下一轮必须用主项目**当前候选**重新绑定并对照。
- 本会话已完成的离线入口：`python ml_training/performance_candidate/arm_compare.py --dry-run`，生成 `results/arm_dryrun/plan.json`；**未连接板卡、未执行 ARM**。
- 实际执行（需主项目授权后用当前候选）：
```bash
python ml_training/performance_candidate/arm_compare.py --execute --candidate <current candidate release> --frames-dir <same 4 IQ> --arm-dir ml_training/arm_probe --fixtures-dir ml_training/real_campaign/fixtures/synthetic --run-id <fresh-id>
```
- 入口只复用项目已认可的 `pynq_ssh.py` Paramiko 连接能力，不使用 Windows ssh/scp。plan 明确列出：所有本地上传文件（含 SHA-256/大小）、远端路径、上传/命令超时、结果回收清单、`/tmp/rf-perf-<run_id>` 清理命令。
- 同一候选、同一批 4 帧 IQ、相同计时边界（full-path `out.total_seconds`，warmup 5/repeats 20）；分别回收 baseline/optimized 的 median/P95/RSS、加载耗时（wall−阶段合计与 adapter 构造时间）及逐帧输出一致性。
- 运行目录仅为板端 `/tmp/rf-perf-<run_id>`；不安装候选、不执行 RF、不改 current/overlay/服务，结束后 `rm -rf` 临时目录。

## 7. 限制
- 未执行 ARM 测量或 ARM 资格；本任务不连接板卡。
- 主机 timing 受系统负载影响，作为方向性线索；最终以相同命令的 ARM 结果为准。
- 候选/参考/冻结模型/证据均只读；本任务只写 `ml_training/performance_candidate/`。
