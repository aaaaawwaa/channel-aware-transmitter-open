# 2026-10-07接续状态

时序观测69290425已安装，4新帧无rail；BPSK三帧同步、末帧窄带失步，无点/BER，同IQ重放保留拒绝。最终安全解除，累计64开发帧。S2正式冻结/108＋12/真人仍NOT_RUN，S3未开始。下一项为有限稳定等待对照设计，当前无等待参数变更。最新总结及固定流程：`docs/reports/2026-10-07-s2-capture-timing-progress.md`。以下计划正文及历史状态保留。



# S2固定窗口环境诊断 Implementation Plan

2026-10-06当前：S1已关闭，专门触摸计时由用户取消；环境v3已上板，开发pilot最近两轮真实CLIPPED，正式冻结/108实板/12离线/真人未开始。新增observer版本663b9f72已安全安装并保留7be30345回退，初始化后3次真实只读观测完成（mode2/index76/digital0），0新IQ帧；解除ACK与最终安全状态通过。1707软件回归通过4跳过、ARM100 P95294.90ms。阶段总结及流程见 docs/reports/2026-10-06-s2-observation-stage-summary.md。下一项是接收启动瞬态/稳定增益调查，下方旧数字保留为历史。

> **For agentic workers:** 使用subagent-driven-development或executing-plans；步骤以复选框记录，独立规范与代码审阅可委派。保留已有dirty tree，在当前功能分支与主树继续，所有候选复制隔离，禁止重置或把未提交的S1丢在新HEAD工作树。各worker严格文件所有权，不提交混合历史改动。

**Goal:** 在已通过S1教学链路中增加同帧、可解释、可回放的固定窗口环境诊断及受控实验场景。

**Architecture:** 不可变IQ＋冻结RX/RF配置→纯规则诊断；TX场景真值独立保存，worker后台计算，UI只显示/改草稿，唯一daemon控制。新发布合同独立于S1，旧链路兼容。

**Tech Stack:** Python3.8/numpy（兼容板端1.17）、pytest、现有800×480 UI、Paramiko、既有有限采集。规格：2026-10-04-s2-window-environment-design.md及已批准总体scope design。

**2026-10-05 当前：** 软件开发、独立复核、ARM P95 287.87ms及安全安装已完成；当前开发pilot，正式与真人NOT_RUN。见docs/reports/2026-10-05-s2-window-environment-progress.md。

## Chunk 0：授权与基线

- [x] 保存用户不需专门触摸计时、允许开始S2的原文；S1关闭为功能＋体验通过，精确时延仍NOT_MEASURED且非阻塞。
- [x] 保存核心源码起始SHA，冻结BPSK/bit/daemon/CNN不改，保留S1回退。当前主树功能分支包含尚未提交S1，不从HEAD新建缺失依赖副本。
- [x] 运行S1教学/记录/合同基线测试：`python -m pytest pynq_overlay/sw/tests/test_rf_teaching_pipeline.py pynq_overlay/sw/tests/test_rf_experiment_record.py pynq_overlay/sw/tests/test_rf_teaching_release.py --basetemp=evidence/rf-teaching/s2/baseline/pytest-temp-v2 -q`。系统临时目录权限错误需改用项目内新的basetemp；不把环境错误当软件失败。
- [x] 规范/计划独立审阅，无阻塞意见后实施。

## Chunk 1：纯场景与纯诊断

Files：Create `pynq_overlay/sw/rf_experiment_scenarios.py`、`rf_environment.py`、各自`tests/test_rf_*.py`；Create校准/分组工具 `scripts/rf_environment_dataset.py`。

- [x] 红例：相同声明产生相同字节；x=A*s+j成对j不变、不整体缩放；固定实际周期/频率；超码值及非法类型拒绝。场景输入不进入诊断函数。
- [x] 红例：无基线UNKNOWN，正常PRBS及其周期偏移不假报窄带；窄带/稀疏脉冲保留数值与位置；复合/失步不报CLEAR；低能量/削顶/域外/错配拒绝。
- [x] 逐项RED后实现不可变结果/基线及纯函数，运行对应单测GREEN，保存红绿日志。
- [x] 用旧S1真实IQ建立分组TRAIN/VAL开发基线和严重度范围；生成冻结JSON、来源IQ SHA及分组收据。开发/校准输入不计新物理帧。
- [x] 独立规范合规审阅，再代码质量审阅；有问题修复后复审。

## Chunk 2：worker/UI/记录与独立合同

Files：Modify `rf_status_worker.py`、`ui_model.py`、`ui_view.py`、`pynq_status.py`、`rf_experiment_record.py`、`rf_teaching_profile.py`（仅必要入口）、`merged_release.py`、`install.sh`；Create `tests/test_rf_environment_pipeline.py`、`test_rf_environment_release.py`、`test_rf_environment_view.py`。

- [x] 先写红例：完整生产handoff诊断必须同帧、发布/代次/配置/提交修订/BPSK版本/基线一致；取消/修改/过期/断连/STOP清诊断，基础结果不被异常破坏。
- [x] 实现后台load/compute和environment_status，实际PanelScreen落地；绘制不做算法/文件/硬件IO。
- [x] 加环境页，标签与特征/依据、68.27µs窗口及拒绝原因；场景选择仅草稿，独立load→确认回环；已有CNN页保留。
- [x] 记录可保存诊断有效/拒绝/历史，声明和预测分别保存；新记录回放重算，篡改/身份错配必须失败。原S1记录仍读取。
- [x] 新`cnn-bpsk-environment-v1`合同覆盖模块/基线和资格；worker/model/panel/installer全部接受新合同，旧合同回退测试保持GREEN。
- [x] 运行新单测与全部板载软件回归，候选导入/渲染验证；冻结算法SHA不变。独立合规→质量审阅。

## Chunk 3：验收器、候选、正式实板

Files：Create `scripts/rf_environment_verify.py`、`rf_environment_acceptance.py`、有限board session/runner及部署入口（复用已验证S1模板，但不绕过其合同/父发布检查）。

- [ ] 验收器红例拒绝同源跨TRAIN/VAL/TEST、篡改、把旧数据或离线派生冒充新采集、遗漏失败帧、错配发布/基线/配置、只报成功子集。
- [x] 生成不可变候选，复制旧bit/HWH/daemon/CNN，保存manifest与源/基线hash；S1保持回退。
- [ ] 先开发pilot冻结范围，明确非正式TEST；安全新帧证据完整后再冻结90+18声明，正式TEST不参与调阈值。
- [ ] 正式90已知＋18安全有线负控＋12离线拒绝，主机/ARM同输入重放与混淆矩阵，已知类拒绝计错。每已知类≥27/30，正常误报≤3/30、负控误判CLEAR=0；异常与关闭失败保留并停止。
- [x] ARM100次完整后台链路P95≤300ms，逐次数据/RSS/CNN启用状态保留；不用替代实体体验。
- [ ] 安装/候选验证与最终停止解除读回，无第二Overlay；新实体屏测试给清晰步骤。机器通过不冒充真人通过。

## Chunk 4：阶段收据与方向

- [ ] 保存源码、数据、参数与声明hash、分组/校准/TEST、候选/安装和失败迭代；报告分别列离线实现/新实板/真人状态。
- [ ] 更新roadmap与流程图到S2当前位置，S2通过后进入S3建议/拒绝；本阶段不开发QPSK、RRC接收、EVM、空口和自动发射。

2026-10-06 observer checkpoint: 663b9f72 installed with 7be30345 rollback; 3 real raw observations, zero new RF IQ frames; final disarm/safety passed. Current report: docs/reports/2026-10-06-s2-observation-stage-summary.md.
