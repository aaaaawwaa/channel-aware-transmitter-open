# S2有限回环时序 Implementation Plan

> **For agentic workers:** 使用subagent-driven-development，按文件所有权分工和规范→质量审阅。已有dirty tree留在当前功能分支，候选复制隔离，不提交/重置混合历史修改。

**Goal:** 取得与真实IQ绑定的物理回环调用时序上下界，推进削顶定位。

**Architecture:** 不改core或控制顺序，只在daemon显式请求处记录clock bracket；device.info单独只读回传已成功帧的trace。客户端验证版本/当前帧身份，finite runner复用既有worker/UiView与安装/证据模板。

**Tech Stack:** C daemon/MinGW fake PL/native ARM GCC；Python3.8现有client/worker；Paramiko指定helper；原bit/HWH/CNN/基线。

## Chunk 1：设计与C时序交易

- [x] 追踪现有physical capture，确认立即TX→prepareRX→ARM，无稳定等待；核对已安装静止观测与失败IQ。
- [x] 独立审核spec及本计划。
- [x] worker独占rf_overlay/sw/board_app/rf_daemon.c、rf_version.h、test/test_rf_daemon.c、test/test_rf_daemon_main.c；先新增有序测试clock与真实transport红例，证据置evidence/rf-teaching/s2/capture-timing-2026-10-06/。
- [x] 最小实现spec：opt-in capture bool、8clock brackets、仅send成功trace、frame时间单值、control/断连/解除清旧，device.info只读可选，版本1.0.2/minor4。旧默认零额外clock/gain读、状态/关闭/原payload保持。
- [x] make独立HOST_OBJDIR host-test host-test-core host-test-iq host-test-protocol；指定Python test_vendor_integrity.py。RED日志先确认断言失败，GREEN需exit0和完整关闭回归。独立规范→质量审核。

## Chunk 2：客户端及finite evidence

- [x] worker独占pynq_overlay/sw/rf_status_client.py及新tests/test_rf_capture_timing.py，C完成后执行；严格旧daemon发送前版本门禁，physical bool opt-in，当前frame及trace身份/时间校验。默认UI不启用，gain读取独立。先红测试后实现，保留旧协议/捕获测试。
- [x] root新scripts/rf_capture_timing_probe.py/run.py及tests，复用EnvironmentSession和已验证gain probe驱动source/receipt/inspect门禁。两会话x(normal,narrowband)最多4帧，保存全部原始记录和独立诊断，任何削顶/证据/收尾失败停且无替换。
- [x] 全pynq软件及新host tests；独立规范→质量审核。预设支持域、停止条件和原始失败保留必须先固定再实采。

## Chunk 3：native候选与有限调查

- [x] 原生隔离构建all+host fake测试，不安装、不TX/Overlay。root仅改scripts/rf_environment_candidate.py及test_rf_gain_candidate.py接受明确配对1.0.1/minor3或1.0.2/minor4，不弱化45源/ELF/build门禁。
- [x] 不可变新候选，旧bit/HWH/CNN/基线不变；精确ARM4输入＋100次P95<=300ms及原始逐次数据门禁，新增只读功能不冒充正式RF验收。
- [x] 安全安装，父663b9f72安装收据绑定回退；最初未布防、TX/RX关闭、双服务正常。
- [x] 一次有限2x2调查，不自动重试；实际每帧API bracket/原IQ/handoff/trace/idle gain完整，异常保留并独立最终inspect；所有owned收尾ACK。
- [x] 阶段总结真实结果、间隔上下界与rail/peak，给出假设支持/不足及下一固定门槛。S2正式freeze/108+12TEST/真人和S3仍NOT_RUN，不凭这4帧宣布根因或通过。

2026-10-07 checkpoint: candidate692904251e024a1254aecee679c6322a safely installed; four physical timing frames recorded and replayed, no clipping; API TX-to-RX gap5.106..12.078us, not sample edges. All owned sessions ACK and independent final safety passed. Root cause remains unproven; formal TEST/human/S3 NOT_RUN. Next bounded single-variable settling experiment is design only, not implemented. Report docs/reports/2026-10-07-s2-capture-timing-progress.md.
