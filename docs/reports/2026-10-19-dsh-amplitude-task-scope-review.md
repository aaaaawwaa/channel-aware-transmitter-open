# DSH 当前幅度文档任务范围核验

核验时间：2026-10-19 09:14（Asia/Shanghai）。读取本地实时会话及文件，无板卡操作，无修改 DSH 正在编辑的文件，无消息发送。

## 当前进度

- 部署完成，板端 `03b9b883a61f29605be2c1f3817024d0` 已携带 n=20 development 基线。
- 18 帧 pilot 完整、同 IQ 重放通过，但 known_correct=8/9，passed=false，正式 TEST 不具备前置条件。
- 08:40 派发的只读板卡检查及离线诊断均已于约09:03完成。板卡重启后发布/基线/51成员一致、服务正常、RF关闭，无新精确存储错误；存在日志重放和FAT非正常卸载警告，不等于存储根因已修复。
- 当前活动子任务 `9872834f-2419-4740-81aa-8e53f86e92a4`，09:03派发“Document amplitude semantics and report fields”，截至09:13仍在实现 `scripts/rf_environment_frame_report.py` 与其测试，尚未提交。

## 判断：应保留已有诊断成果，纠正当前后续范围

### 已证实的直接触发原因

冻结候选下失败normal帧 `crest_ratio=2.09115145` 高于 `pulse_crest=1.98101452`，且未触发前面的窄带/脉冲分支，最终 UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT。相同IQ可重现此结果。

复现证明实现结果一致，不能据此证明判据设计满足normal→CLEAR的需求，也不能排除算法覆盖或标定分布问题。现有资料缺少本pilot实际增益遥测，物理电平差机制仍未确认。

### 当前偏离

1. **事后增加验收前提。** 子任务被要求把约303 codes/-19.6dBFS写为normal→CLEAR的“前置条件”；新报表实现已含“需要窗内complex RMS不低于约303 codes”，新测试还钉住必须包含这一表述。该值来自特定信号缩放与加噪模型，原冻结判据没有这一门。可作为探索结果呈现，不应作为已认可验收条件或排除失败帧的理由。
2. **推断强于证据。** 主会话使用“已定罪=预期行为，非缺陷”“首帧/建立期不成立”等绝对措辞；窗口内平稳不足以排除窗口前或会话间增益/链路因素。400次合成的32.3%是指定模型下的结果，不能当作真实板卡总体normal拒收率。
3. **过度审查。** 任务书对文档/报表强制“≥2变异+复审”，违反当前development-review-policy不要求固定变异数、R0作者自检、R1相关测试的规则。报表功能若确有复用价值可保留为R1，跑必要字段、来源和判定不变检查即可；不为措辞做新一轮独立审查。
4. **物理机制混淆。** 诊断建议又要求环境pilot使用一次性nonce，与本项目环境路径明确“无nonce”的合同不符。新采集需明确预算及真实有效接线确认，但不应额外引入HEADROOM授权工件。

## 下一步

保留原pilot失败及诊断原件，停止将经验边界升级成验收门。文档只解释规则、呈现原始量及模型限制；无条件保留known_correct=9/18帧/负控零误判的门。off sidecar沿用上一场景应说明其字段来源及与本次RX条件的区别，不把“stale-by-design”自述作为不再核对实际TX关闭/场景声明的依据。

对现有证据做一次简短收口，区分已证实规则触发、未确定的覆盖/电平原因，并提出最小修复或最小遥测试验。不要重算已有400次仿真，不重做整套审查，不自动补采、部署或启动正式TEST。新的物理试验只在明确预算内执行。

## 依据

- DSH主会话09:03:25的派发任务书及活动子会话9872834f。
- `scripts/rf_environment_frame_report.py`（09:14读取，报告渲染中的303 codes文字）。
- `scripts/tests/test_rf_environment_frame_report.py`（09:14读取，303前提及措辞断言）。
- `pynq_overlay/sw/rf_environment.py` 最终crest判定分支。
- `scripts/rf_environment_pilot.py` 的correct==9、len(rows)==18与negative==0门。
- `docs/requirements/development-review-policy.md`。
- `docs/superpowers/specs/2026-10-17-headroom-46db-environment-migration.md` 环境授权/帧矩阵。
- `evidence/rf-teaching/s2-46db-first-frame-amplitude-2026-10-19/NOTES.md`。
