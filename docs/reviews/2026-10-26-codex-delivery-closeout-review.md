# 去QAM64交付版：Codex增量复审

结论：**产品缩减及BPSK/QPSK本轮演示证据 LIMITED APPROVED；完整交付闭环声明 CHANGES_REQUIRED。**

本次只读本地交付包、执行日志、原始IQ和检查器；新增复审脚本/报告。未连接板卡、未部署、未采集、未修改产品或DSH原始收据。最终板态引用19:26的独立读回，不代表本次实时读板。

## 已确认进度

- 新版4814bbc42b9d5e0165a3ed3ebe7eaeef已经安装。19:26读回current/active均为新版，双服务active/running/NRestarts0；armed/core/TXsource/RXarmed均false，IDELAY127/29。
- 独立检查实际candidate.tgz：SHA256=74c6e32c9edc99f2f566dd5e822dc6e271ff997762bdce1eb89753e1c370f267；62个software摘要、bit/HWH/daemon均与包内manifest匹配，未发货QAM64 receiver。
- 重新运行QAM64移除9项负例，9/9通过（当前工作树产品入口）；不冒称重新进行了板上9项检查。
- 独立验证选用12帧的IQ摘要全部匹配。BPSK两ON均0/138，QPSK两ON均0/128；QAM16分别1/256及0/256。6个OFF保存了对应模式结果，未沿用ON的BER。
- 当前OFF修复14个直接反例/正例验证通过：缺envelope、错模式、残留BER以及QAM16残留carrier/timing被拒。run_acceptance.sh的OFF分支现已传入--mode/--rx-mode。

验证脚本及机器结果：`evidence/codex-delivery-closeout-review-20261026/{recheck.py,results.json}`。797项产品回归为DSH已有报告，本次没有重复运行该全组。

## P1：审查通过后又改变QAM16验收语义

对象：`evidence/rf-teaching/qam64-delivery-scope-2026-10-26/integration/acceptance_check.py:54`。

19:14的codex-review-followup2.log批准版本，明确以QAM16 BER=0为检查条件，44个案例中BER=0.1应拒绝。当前19:19版本改为只要求BER处于[0,1]，遇非零只打印CHECK_METRIC_NEGATIVE，然后落到第66行CHECK_PASS并退出0。

本次可执行反例证明：QAM16 1/256及256/256都退出0。这不证明产品算法必然全错；它证明当前成功退出仅能表达程序/结构完成，不能继续沿用旧批准作为接收指标通过依据。原始1/256被保留是正确的；“TRIAL_ACCEPTED”本身不能消除指标失败或追认原研究。

最小处置：保留原判据下的失败；检查器/报告分别表达execution_complete与metrics_passed，并明确试部署门实际验证什么。需要零误码的门必须拒绝非零。若交付接受可展示非零BER，将QAM16列为受限演示，不能倒填成原零误码验收通过。不需要为了修改声明再采集或重装。

## P1：失败后的续跑使原预算失效

原请求是一轮试部署、有界12采集/12初始化/6TX、零研究重试。原始日志计数：

| 日志 | 已完成单帧campaign数量 | 试部署验收开始 | 结果 |
|---|---:|---:|---|
| trial-run.log |9|1|QAM16 1/256后回滚 |
| trial-remaining.log |0|0|pre-state文件已存在失败，日志打印回滚 |
| trial-remaining2.log |1|1|QAM16 OFF缺RX seed，回滚 |
| trial-remaining3.log |0|1|shell CRLF/illegal option，回滚 |
| trial-remaining4.log |1|1|QAM16 OFF缺TX seed，回滚 |
| trial-remaining5.log |3|1|TRIAL_ACCEPTED |

合计14次完成采集，至少8次ON、6次OFF，超原12/6预算；5次记录进入试部署验收。选用12帧不能把额外2次采集/ON剔出实际账本。首轮失败后的多次重派不符合原零重试范围；本次复审不能追溯补授权。

最小处置：停止继续补采/重装；根据全部日志出一份实际执行账本，标记授权/执行偏差。当前安装健康和各帧有限事实仍保留，不必为了历史偏差自动回滚正常服务。交付acceptance目录应包含全部失败派发日志，避免只带最终成功日志而无法复核总数。

## P2：交付材料与原始记录不一致

- 根REPORT.md第29行称额外1次，而ACCEPTANCE-RESULTS和KNOWN-LIMITATIONS称额外2次。实际日志支持额外2次。
- ACCEPTANCE-RESULTS.md第11行称无physical/RF有线capture，但原始ON final.json均physical=true，ui_diagnostic也记录physical=true；OFF为false。选用12帧含6次physical ON；连同额外两ON，完整日志为8次ON。区分“有线RF实际采集已经发生”和“完整物理验收尚未完成”，不能把未完成验收写成从未发生采集。
- 文档FINAL/installed可表达包已生成和已安装，不应扩展为零重试、三模式指标全过或真人验收完成。

## 最快收尾方案

保留当前已装版本、RF关闭，结束自动板测。离线一次性修正以上检查器语义和文档账本，补上失败日志；只跑新增直接反例及相关检查，不重开全仓审查。冻结BPSK/QPSK为本轮已获得零误码冒烟证据的主演示；QAM16保留实际BER与受限说明；QAM64继续不交付。现场再由用户确认LCD/触摸操作，不代签。

完成这些修订后，对新增差分做一次短增量核对即可结束本次收尾。没有证据支持将这14帧自动升级为完整范围、长时稳定性或所有原计划功能验收通过。
