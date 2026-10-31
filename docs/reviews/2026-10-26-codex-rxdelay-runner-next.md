# RX_DATA_DELAY runner 下一次执行前的最小核对

本对话核对DSH 2026-10-26 15:23报告及当前 scripts/rf_qam64_rxdelay_runner.py，未连接板卡、未修改产品、未采集。

## 当前进展

036a4ec9ef4cea7fa108abbb02b00300已TRIAL_ACCEPTED，无RF验收17项通过；6384为回退目标。实际初始化后接口读回0x006=4、0x007=0x70、0x010=0xC8、0x011=0、0x012=0x10。A帧完成；点1列表类型被客户端本地拒绝，因此5和3均未试写，B/C/D不存在。原值4的恢复读回在关闭core前完成，最终FPGA127/29且RF关闭。

## 当前runner仍需相关假链证明

- _select当前已返回整数，但只有SELECT_INT_TESTS_PASS纯函数检查，尚无完整run入口的A/B/C/D假链证明。应覆盖0..15全部起点、边界减少点、非法原值拒绝，以及真实候选客户端的整数校验。
- rx_delay_set在调用成功返回后才递增attempted；异常请求可能已写芯片而不被计数。应在调用边界前记录attempted，返回后completed，异常分开记录本地拒绝/协议拒绝/状态未知。失败路径恢复也必须共用预算守卫。
- _capture检查次数但没有会话deadline检查，也没有调用已导入的_idle_check。应覆盖每次capture/set前的时限与安静状态，不能只在两个point之间检查。
- BUDGET写了recovery_sessions=1，但当前run中没有创建恢复会话的实现。若本次真的在写点后capture FAULT，普通restore会被拒绝；必须有明确的恢复-only路径，而不是预算声明。恢复前确认安全，最多一次init/configure，禁止load/capture；验证原五个寄存器，必要时仅恢复原低4位。
- summary目前只有基于关闭后PL127/29的restored_tap，没有独立chip_restored字段。芯片恢复必须由关闭core前的有效rf.interface_status与原始五寄存器精确比较得出；PL29不能证明芯片0x006已恢复。失败和UNKNOWN要分开表达。
- 最终执行完成必须同时满足研究步骤、芯片恢复、FPGA配置恢复及RF关闭；close/post-readback异常不能吞掉并误报成功。

这只要求一次相关假链与Lead增量检查，不要求重审产品候选、重建ARM或全仓回归。当前candidate已安装，下一次安装预算为0。

## 数据解释修正

A帧出现0xFFC0/0xFFC2等XOR。应同时报告原始16-bit wire差异、12-bit有效数据差异，以及符号扩展一致性。bits12..15可能随bit11符号改变联动，不能直接说新增四个独立LVDS故障；但bits6..11的半字差异必须保留，不能继续只看lane1。上述仅为分类规则，不是根因结论，也不改原始IQ。

## 新一次研究建议

主研究1次，预留恢复1次；init/configure各最多2，load1，rx_data_delay.set最多5（包含恢复），idelay.configure最多1，digital capture和source各最多4，physical0，研究重试0，板端活动总时限180秒。重新实读v，内部值顺序A(v)→B(v+1)→C(v恢复)→D(v-1)→v恢复；边界只测一个邻点。不以误码扩点。全部fake链通过后上传同一套runner及依赖并核对摘要，执行新attempt。
