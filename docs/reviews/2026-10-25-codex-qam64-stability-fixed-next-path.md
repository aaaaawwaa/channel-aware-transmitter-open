# 稳定性 runner 修复复核与下一项路径对照

日期：2026-10-25。本次仅本机读代码和测试，未连接板卡、未初始化、发射、采集或安装。

## 本次确认

- 修复 runner SHA：`39f5293c52997510b9d33286cf4dc6fade6f1a0e1cceab242bb63cee3da97b6e`。
- 对真实修复 runner 的旧测试和新增 guardrail 测试，本次重新执行：**33 passed in 0.63 s**。日志：`evidence/codex-qam64-stability-followup-20261025/runner-tests.log`。
- 主要前述问题已有对应检查：cleanup 返回值、ON 嵌套观测、误码计数、合法失同步、接口预检和逐帧文件。无需重新审查未改候选，不为复核再采旧实验。
- ON gain=76、attenuation=[46000,46000] 和实际等待已经追加更正；一次读回不是连续轨迹，不能据此排除整个模拟链。旧报告/原始材料继续保留。
- 第一次失败后又启动完整 run2，累计超出原包预算；不是因 BER 重采，不等于没有第二次尝试。后续工作包发生物理副作用后中止，授权即结束，不能换目录重新启动。

## 顺手修正的计数语义

当前 runner 将 `session.initial()` 前后计作 initialization；而 `LiveUiSession.initial()` 只是一次状态读取，真实 `rf.init` 在 `setup()` 的 arm/init/configure/load 控制序列中。正常成功时数字碰巧一致，但拒绝/异常时不代表实际初始化。

下一 runner 在实际 `rf.init`、`tx.load`、capture 调用边界记录 started/completed/unknown；只读 initial 单列 precheck。只记录操作名称和结果，不记录 arm 参数中的 token。此项可在同一小工作包完成，不另开一轮候选审查或权限框架。

## 数字回环的实际入口及含义

- `rf_overlay/sw/board_app/rf_daemon.c:op_loopback_digital_capture` 已实现 `loopback.digital.capture`，经过相同的 core/状态机，操作 AD9361 数据口 loop、配置现有 TX buffer、采样并返回二进制 `iq_frame`，随后关闭 source 和 loop。
- 当前冻结 `RfStatusClient.capture(args, physical=False)` 只映射为 `rx.capture` 或 `loopback.physical.capture`，没有可直接传入 digital 的公开 typed 参数。
- 已有底层 `_capture_unlocked(op, wire, physical)` 与 IQ header/payload 验证可被诊断脚本内的薄适配层复用；需保留原 I/O 锁、live advertised-op 检查、loaded session、身份、二进制长度、超时、异常收尾。数字帧 `op` 必须保留真实操作，不伪装成物理或普通 OFF。
- 数字路径需要额外确认 source 已关闭；原 `_confirm_capture(..., physical=False)` 不要求 `post.tx_source_selected=false`，不能把这一默认分支当作数字收尾已证实。
- 不使用 `board_app`/旧 loopback criteria 的硬件执行模式绕开当前 daemon，也不加载 Overlay、停止服务或修改已安装候选。旧 criteria 可以作为离线路径一致性算法参考。

**不能按数字/物理 RMS 的高低定位模拟增益。** 两者比例与路径不同；数据口 loop 的通过主要支持其覆盖的数字端口/PL/打包片段，不证明外部 RF 或 ADC/DAC 整条模拟路径正常。数字也异常时，先查其覆盖的数字片段、映射与缓冲；不能直接断言 ADC 故障。物理随时间变化本身也不等于热漂移。

## 建议新工作包（尚无实现或执行）

保持已安装 `024fb7b130dc756f648a655560ef740f` 和 RF 工作点，制作小型 host-only 路径对照 runner，Lead 单一控制板卡，独立代理负责 TX-buffer 参考与离线分析。先完成薄适配层的假协议/完整二进制帧、身份、计数和收尾测试。

在新的明确操作授权、有效接线、只读预检及接口检查满足后，一次 session、一次 init/load，固定顺序：

`OFF-first → physical A(wait=0) → digital A → digital B → physical B(wait=0) → OFF-last`。

预算：**1 session / 1 rf.init / 1 tx.load / 6 captures / 最多4次TX source启用（2 physical + 2 digital）/ 0 retry**；总墙钟计划≤180秒、capture deadline10秒。数字路径同样开启 TX buffer source，不能漏算为零 TX 消耗或承诺没有模拟输出。

数字路径主验收：以本次实际 tx.load 的不可变 payload 为参考，独立核对原始整数码、符号扩展、I/Q 映射及循环移位，报告精确匹配/残差与歧义。该路径检查不是独立比较区产品 BER；产品 QAM64 解调继续使用其既定对齐/比较区，不能借完整真值调整接收器。数字帧无物理窗口 gain/attenuation 允许具名缺失，不伪造。

每帧记录真实 acquisition path、身份、IQ/SHA、原始结果与必要观测。物理 A/B 固定工作点 80MHz/46dB/amp60/60MSPS/3Msym/s/4096/RECT/QI/seed39；数字的映射按实际路径独立确认，不强套物理 QI。数字关闭后再采 physical B，以检查路径恢复；非零物理 BER 保留，不为追零重试。

若已有协议/冻结客户端无法通过该 host-only 适配，具名 `PATH_UNOBSERVABLE` 并停止本包，提交具体缺口，不临时重装或改 daemon/RTL。前置代码和假链由 Lead 一次增量自检，实际新 runner 未在本报告中被预先批准；物理操作范围应由用户复制的提示词明确给出。
