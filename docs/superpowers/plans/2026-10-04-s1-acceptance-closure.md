# S1 Task 6 验收工具闭合

沿用已批准S1规格，不改变接收算法、控制协议或S2进入条件。此补充只完成主机验收工具；S0真人未确认时不安装/布防。

- [x] RED：重复/错种子/错版本/关闭失败/虚假汇总/重放失败/缺失或篡改100次耗时均拒绝。
- [x] GREEN：独立汇总固定三组30ON+3OFF及关闭回执，重算BER和P95，核对候选模型/算法/脚本/IQ哈希。
- [x] 教学实板driver在联网前校验本地候选；取回后自动重放并生成机器门禁收据。旧M4调用兼容。
- [x] 定向回归、独立审阅、续开发收据与阶段图。机器通过不替代真人及20次触摸测量。

只读预检2026-10-04已确认993ffe21发布有效、status_only、未布防、TX选择否、RX ARM否，服务正常且NRestarts=0。它只证明该时刻状态；每次安装/实测仍须重新核对。

新增31项验收测试、相关六组共91项通过；独立复审APPROVED。已补齐实际meta.display字段、三个上传脚本独立源码哈希及传输失败收据；真实生产数学重放使用测试生成的33帧，不计作实板证据。

Files：Create scripts/rf_teaching_acceptance.py、pynq_overlay/sw/tests/test_rf_teaching_acceptance.py；Modify scripts/rf_bpsk_ui_board_run.py。执行记录 evidence/rf-teaching/s1/continuation-2026-10-04/，报告 docs/reports/2026-10-04-s1-acceptance-closure.md。
