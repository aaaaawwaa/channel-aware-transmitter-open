# Claude最新G0进度简审（2026-10-07）

**结论：Task 0.1已完成，Task 0.2部分完成；G0整体尚未关闭。** 继续离线闭合，再进入ARM资格与8帧对照。

## 对话及完成情况

读取本工程Claude会话`69bceb71-4259-41a3-942e-cf03031e5b73`，最后实质工作总结为北京时间17:11。文件19:46修改不代表有新的19:46验收结果。最新收据为`evidence/rf-teaching/s2/settle-closure-20261007-v1/stage-checkpoint.json`。

已完成分解释器基线；新增`rf_sleep.c/h`，真实nanosleep适配器进入主机与生产构建，睡眠返回失败可被交易处理；失败等待的TX释放有连接存活期测试，FAULT＋EOF进入保留FAULT状态的清源/清ARM路径。作者完成多个有效变异复验，并纠正了两次“测试虽绿但未钉住原因”的问题，这是有效进展。

本次核验最终11项C源码SHA全部匹配；在新HOST_OBJDIR重新编译测试，**daemon399/0、core98/0、sleep22/0、startup92/0、state64/0，make退出0**。作者Python日志为board1872/4skip、tools121、host非ML902、ML54；本次未重复全量Python回归，六个相关Python文件与G0前基线SHA相同。

native ARM构建、安装、真实RF、8帧对照、S2正式/真人、S3均未开始。最后确认的板端版本仍来自上一轮保存证据，本次未连板。

## 简要代码review

1. **P1：终止信号仍未闭合。** `rf_daemon_main.c:620`仍无条件重试EINTR，活跃serve不观察终止旗；读取超时30秒与服务退出预算5秒的差异仍在。EOF收尾的修复不能代替SIGTERM收尾证明。
2. **P2：新增默认路径重复清ARM。** `rf_daemon.c:1747`在成功capture后仍调用`rf_core_set_rx_arm(false)`；`rf_core_finish_rx`已清ARM确认，setter又多2次MMIO读和1次写。不符合正常默认路径不新增硬件操作的计划。建议跟踪“RX仍需清理”而非仅“曾启动”，成功finish后清标记；补默认/零等待硬件操作计数的回归测试。
3. **覆盖待补，非已证实错误。** F2的`==FAULT`条件与`!=READY`并非全域等价，continuous RX是可区分路径；但当前条件本身没有被证明错误。应核验continuous RX寄存器与状态保持，或说明不可达前置条件，避免为杀死mutation而强造测试。F1-RX和零档trace也待补；零档测试应真正取回device.info，再验证字段存在与值。
4. **上次的Python/集成问题仍在。** `rf_capture_timing_probe.py:215`仍未固定两档/最多8尝试；`rf_capture_timing_run.py:22`仍固定1.0.2/minor4四帧；`rf_status_client.py:197`、`:237`的正等待回读minor门禁及零档必填字段未加固。Task 0.3–0.6未开始，之前12帧及回读反例仍适用。

## 下一步安排

- **先收尾0.2：** 修复重复清ARM；补SIGTERM/活跃连接的安全退出、RX半边、实际零档trace；明确F2是覆盖证据任务，不能据此声称当前代码错误。
- **再做0.3–0.5：** 固定`{0,1000}`与2会话/8尝试，区分档位集合与执行顺序；补版本/严格类型/完整原IQ记录证据；接通新版独立driver的上传、下载及最终inspect。
- **最后0.6：** 独立规范和质量审阅、完整回归、源/日志绑定一致，再做ARM资格→最多8帧对照。稳定后回到18帧开发复验→正式90＋18＋12→真人→S3。

不要把“G0固化闭环”理解为全部G0任务完成。`CLAUDE.md`顶层和final-regression仍写core95/0，最终源码对应final-c-suite及本次复核均98/0；应统一阶段数字，保留旧记录的历史身份。

详细执行安排沿用`docs/superpowers/plans/2026-10-07-s2-settle-validation-next.md`，本次仅增加默认路径重复清ARM的修复条目。审查证据在`evidence/rf-teaching/s2/claude-review-g0-2026-10-07/`。本轮未修改产品源码或访问硬件。