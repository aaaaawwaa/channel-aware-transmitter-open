# QAM64 下一次有界板测：本轮检查

本次仅离线检查，未连接板卡、未运行RF或修改产品文件。

## 本轮实际推进

读取 `evidence/rf-teaching/qam64-halfword-final-2026-10-26/REPORT.md`：严格评分、DCP关键逻辑核对、完整2R2T数字链仿真均已完成；未复现原始跨样本高低半字混合，未修改产品RTL、未构建候选。036a保持安装的说法沿用已有报告，本次未重新读板。

独立运行 `evidence/rf-teaching/qam64-halfword-scoreboard-2026-10-26/tests/test_strict_scoreboard.py`，5项均通过。修正后的评分器拒绝旧反例，RX稳态相位12–15通过；12个无稳态valid相位独立记录。完整链的5个CLEAN、4个NO_VALID、1个同epoch半字互换仅证明所建数字模型，不能证明实板误码已修复。

## 准备脚本不能原样执行的原因

对象：`evidence/rf-teaching/qam64-halfword-next-board-2026-10-26/phase_probe.py`。

1. frame-only tap25 已在 `evidence/rf-teaching/qam64-frame-idelay-2026-10-26/REPORT.md` 实测导致TIMEOUT、armed=true/pos=0，并且FAULT后会话内恢复被拒。新脚本原样再做该点，没有新的辨识信息。
2. `_idelay(...,0x40,25)` 只加载frame，随后 `_idelay(...,0x3F,25)` 不会恢复frame。B状态是data29/frame25，C实际是data25/frame25，不是声明的data25/frame29。这会污染归因。
3. 异常路径仅尝试会话内idelay恢复，然后cleanup；若恢复抛错，cleanup也会被跳过。没有接入已有reserved recovery-only路径，也没有独立最终读回证实恢复。
4. 目前只做py_compile，没有完整假链测试；counts初始化不是预算执行，deadline也未覆盖每次set。最终CLI退出码只看failures，不严格绑定研究完成、恢复及安全读回。

## 建议最小修订

复用既有成熟runner的预算、计数、恢复和独立读回结构，不另建控制框架。固定一个未重复的邻近点30，进行一次对照，不扫描：

| 帧 | 6条data实际加载值 | frame实际加载值 | 操作 |
|---|---:|---:|---|
| A |29|29|normal setup后基线 |
| B |30|29|仅mask0x3F加载30 |
| C |29|29|mask0x7F加载29后恢复对照 |
| D |29|30|仅mask0x40加载30 |
| 收尾 |29|29|mask0x7F加载29，然后关闭及独立读回 |

mask是IDELAY加载使能，不能把全局tap寄存器当作7条线的CNTVALUEOUT。以上是按操作顺序得到的预期加载状态，应和状态/操作收据分别记录；不能声称已独立测到每条线的物理延迟。

最大1主会话/1次正常初始化，另保留最多1次仅恢复初始化；最多4个digital capture/4次source activation、1次TX load、正常4次显式IDELAY set；异常恢复另计，所有尝试含失败按真实预算执行。恢复会话不load、不capture，不算研究重试。正常路径不再初始化或重新configure。

完整假链至少直接证明：每个捕获点的7线加载状态；A/B/C/D正常链；D超时后不重采；恢复拒绝后使用唯一reserved recovery；恢复或安全读回失败不能报成功。不能只检查最后一次公共tap值。

这套测量只有在用户给出相应板测授权、接线确认仍有效且只读预检通过后执行。研究错误率不达标属于真实研究负结果，不自动要求回滚已安装的控制软件。发生FAULT/timeout停止研究，按已批准收尾恢复，不临时扩大扫描。

## 判读与节奏

A/C是同会话恢复对照，B改变data组，D改变frame。分别比较高6位旧样本匹配、bit1/bit7、NO_VALID和整体错误；差异只支持与改动相关，单帧不能唯一定位或证明物理裕量。基线本身不稳定则降低归因强度。

不重复综合、安装036a、全仓测试或历史独立审查。Lead对修正runner做一次增量检查即可；将来出现新产品候选时再做一次Codex关键审查。本次检查未放行原始phase_probe.py。
