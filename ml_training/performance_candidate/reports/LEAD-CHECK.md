# Lead 一次增量核对（performance_candidate）

日期：2026-10-21；范围：`ml_training/performance_candidate/` 冻结识别方案等价性能优化研究。
仅此一次相关增量核对；未新增审查代理，未连接板卡，未重跑 ARM，未重训/扩网/改阈值。

| 检查 | 结果 |
|---|---|
| 一键命令 | `python ml_training/performance_candidate/run_all.py` → **PASS** |
| 候选只读 | 候选 `099174be`、产品源码、evidence、冻结模型/规则均未修改；reference 14 文件逐 SHA 与候选相同 |
| 耗时定位 | 每帧 preprocess **4**、CNN forward **3**、baseline **2**、spectral FFT **2**；classify 仅进入 pure_class_hint；文件校验/模型加载单独在 wall（1333.70ms），不混入 374.27ms 阶段 |
| 优化 | 单次共享 preprocess/CNN/softmax/spectral/baseline，推导同一 before/after/pure hint；未删必要分析、未缩短输入、未放宽阈值、未增加拒识 |
| 行为一致性 | **70/70 case 0 mismatch**（4 候选帧 + 43 arm_probe + 合成夹具 + 17 输入域/能量/频谱/削顶边界）；所有数值最大差 **0.0** |
| full-path 一致性 | **4/4 帧 0 mismatch**；六类/环境/解调/modrec4/身份一致 |
| 主机耗时（仅线索） | full-path total 38.38→30.44ms（−20.7%）；modrec4 13.38→6.27ms（−53.1%）；adapter 9.46→4.68ms（−50.5%）；clean/rejection/stress 分别 −61.3%/−57.0%/−62.6%（单次主机运行，run-to-run 有波动） |
| ARM | **未测、未宣称 ARM 加速**；下一轮命令见 `reports/performance_report.md` §6；原 63.24ms 是独立探针，不能与 424.65ms full-path 直接比较 |
| 测试 | 本轮按用户要求不重复整套测试：定向 `performance_candidate` **7 passed** + real_campaign 文档检查 **1 passed**；上一轮合并回归为 160 passed，本轮未重跑 |
| real_campaign 文档 | 已修正 `collection_prep.md` 与本次结论：29/33 对齐、0/4002 仅获认可为**已有 IQ 的离线 BER 数值复核**，不是完整统计 BER 验收 PASS（Codex `VERDICT-STAT-BER-MILESTONE` CHANGES_REQUIRED）；修正时序后的包 #5 **`on_aligned=23<27` FAIL**（bit_errors=0/compared=3174；B1/B2/B3 与预算/安全闭环通过，TX 间歇待定位）。引用 `s2-46db-lead-review-2026-10-19/CODEX-VERDICT-stat-ber-FINAL.md`、`RESULT-stat-package5-FAIL-and-offline-analysis.md`、`s2-46db-rx-acceptance-plan-2026-10-20/NOTES-task23-package5-FAIL-release.md`；不重跑/不补写 |
| 交付基线 | 优化研究明确基于候选 **099174be**；主项目当前候选已变化时，不得把本研究的旧候选结果直接标为新候选 ARM 资格，下一轮必须用当前候选重新绑定/对照 |
| ARM 入口 | `arm_compare.py --dry-run` 已离线生成 `results/arm_dryrun/plan.json`；本会话未连接板卡、未执行 ARM；执行需主项目授权后使用**当前候选**跑同一批 IQ 的 baseline/optimized 对照 |

边界：主机加速只作方向线索；最终以同命令的 ARM 结果为准。本任务不采集、不发射、不部署、
不安装候选。
