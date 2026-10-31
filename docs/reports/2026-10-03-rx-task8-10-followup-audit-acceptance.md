# Task8–10 接续审核与验收报告（2026-10-03）

本轮按用户要求继续审核、修复与实验，在 5 小时窗口工具显示 **已用92%、剩余8%** 时停止新增实验并出报告，接近用户指定的剩余9%。这是账号共享额度，不是本任务耗时或精确成本。附带文档作为审核材料；实际执行范围依据用户消息及已给出的上传、构建、安装和直连验收授权。

**结论：尚不能验收生产模型。Task8 的正式准确率仍失败；Task9 的完整逐层数值与 ARM P95 现已有通过验证的 C 加速候选（600样本 P95 65.16ms，低于200ms），但尚未安装/发布。新板端发布保持 `46fe2c080deb9d0c42eb0a612a86d091`，模型 experimental、生产建议关闭、AUTO关闭。**

此前 Task8/9 溯源及清单契约修复、ARM daemon 构建、Task10 实验集成和真实软件事件采集结果，见同目录 `2026-10-03-task8-9-review-task10-continuation.md`。本报告补充其后的进展，不覆盖历史失败记录。

## 验收状态

|项目|本轮结果|判定|
|---|---|---|
|Task8 冻结模型独立 TEST|已冻结正式 macro-F1 0.5928997364；OTHER拒识0.775；OFF拒识1.0|FAIL，门槛分别0.90/0.90/0.90；未用TEST调权重或阈值|
|Task8 数据/训练诊断|只读原 TRAIN1590、VAL1570；定位削顶质量上限和方向敏感性|诊断完成，不等于准确率验收|
|Task8 新来源记录|合成200epoch初始化复现；所有权重与旧初始化逐项相同，完整来源绑定通过|PASS，仅训练来源与复现|
|Task8 物理从零50epoch对照|完整来源绑定；VAL效果差|完成，未部署、未评估TEST|
|Task8 D4增强120epoch实验|预声明、数据验证通过；额度触发时尚未完成|INCOMPLETE，收尾取消，不宣称候选训练或验收完成|
|Task9 固定模型逐层误差|TRAIN920、VAL900、原1005 TEST，共2825帧全部≤1e-4|本地主机候选 PASS|
|Task9 实际 ARM 六TRAIN对照|旧固定模型/原参考，NumPy1.17.4；最大误差1.52587890625e-5|该有限板端样本 PASS|
|Task9 实际 ARM 100帧热推理|原首版225.17ms/预分配293.04ms FAIL；本轮C加速候选 P95 58.23ms(102样本)/65.16ms(600样本)，门槛200ms|CANDIDATE PASS，未部署|
|Task9 内存/体积|C加速候选增量峰值1,540,096B，低于32MiB；运行时SHA d054c3…+模型+评估文件|CANDIDATE PASS，未部署|
|采集器发布身份门禁|严格pin更新到实际已验证46fe…，完整34项测试通过、独立审核通过|PASS，未启动新物理采集|
|Task10|沿用此前实验链路与OFF/三ON/OFF真实IQ、离线重放证据|实验集成通过；完整现场验收未完成|
|真人触摸/P95、现场watchdog、示波器、天线/LNA|本轮没有执行|NOT RUN|

## Task8：为何还不能靠继续训练达标

冻结模型 TRAIN/VAL 正式五类F1分别0.727132213/0.577611922。只统计质量可用帧的诊断值为0.960728773/0.805557320，不能替代正式指标。削顶及同会话质量拒绝的已知帧必须计入 UNKNOWN 漏检；即使可用帧全部正确，现有 TRAIN/VAL 的理想五类F1上限也仅0.755866/0.725686，VAL各已知类召回上限均低于0.80。因此，需要先改善新采集的质量和独立会话覆盖，不能删除失败帧制造PASS。

30份可用 TRAIN TONE 原方向全部正确，但同一帧共轭或90°旋转后原模型均不再输出TONE。30份可用 VAL TONE中18份误为BPSK；错误会话I/Q相关约-0.88/-0.84、幅度比约8.4:1，与训练方向不同，同时仍保留正确±1.875MHz峰和较低DC。这支持方向/会话域差异解释，尚不能证明某个硬件链路故障。VAL OTHER中的CHIRP、SQUARE也有高分BPSK误判，提高统一分数门槛存在已知类召回代价。

合成200epoch、seed20261001从零复现，最佳epoch198、六类VAL F1为0.7096983217；新 checkpoint `f5538d3ed0555cf3b10ba85830ece385e011dc5e547209dc059176396508c74a`。其所有state tensor与旧初始化完全相同，补齐绑定元数据，未读取TEST。旧最终物理模型没有被替换，其历史lineage=false也没有被追溯改成true。

物理从零50epoch、seed20261003对照保留固定1662参数网络，来源验证通过；最佳六类VAL F1 0.5293848317，按原目标选择拒识阈值后的正式五类VAL F1仅0.0620260328。不能据此证明初始化的单独因果作用，因为epoch预算和seed也不同。该候选明确不发布。

D4实验仅覆盖TRAIN：3160份原 TRAIN/VAL（含excluded）逐字节保留，890份可用非OFF TRAIN各生成7份整数符号置换/交换与确定时间起点变体，6230个child，共9390份IQ，无重复跳过；VAL原始帧及元数据不变，没有TEST。验证valid=true，生产ready=false。训练方案在启动前冻结，使用上述新绑定且权重完全相同的合成初始化；仍用原网络、120epoch、原VAL选择目标与阈值网格。D4为离散方向覆盖，不能声称覆盖任意连续相位。额度触发时未完成，训练状态和取消收据见本轮 `phase-augmentation/status.md`、`training-invocation.json`，不宣称精度改善。

## Task9：数值问题修复了，资源门槛尚未解决

原运行时在冻结1005帧TEST中156帧conv3前ReLU误差超过1e-4，最大0.000244140625。诊断先在TRAIN/VAL进行，发现BLAS累加顺序与固定Torch32单帧参考不同。候选按通道/核顺序逐项累加，每项舍入回float32，最后加bias；float64乘积避免提前的乘积舍入。没有改网络、权重、参考后端或容差。

真实训练权重与TRAIN帧回归先RED（2失败、1通过），修复后15项全部通过。独立规格和代码质量审核通过。算法冻结后对原2825帧进行数值复验：三层Conv/ReLU误差均为0，全部层最大误差3.0517578125e-5；原1005 TEST logits最大2.288818359375e-5，无超限。1005集合中包含105份同会话质量excluded但自身未剪切的帧，仅供数值对照，没有改变Task8质量资格。

首版runtime SHA `40ab3132bab9124a4dd215e5d7de915975ad5a9eee8ed465f1b7d9627e417c69`，实际ARM六TRAIN逐层通过，但100帧热推理P95为225.1668ms。随后仅预分配product缓冲，通过multiply/add的out与copyto维持相同舍入顺序，SHA `076e814b036e5e86c37db7b76b17c7a731a8fa77ca12694de13235d92ade81bb`；重新15项测试、2825帧和六TRAIN ARM逐层对照通过，但板端P95为293.0379ms、median202.7706ms。不能把主机约12ms的P95外推到ARM，也不能挑选更快记录覆盖失败。两次测量差异未单独控制系统负载，不能断言优化导致退化；两次实际门槛均失败。

100帧测量包含完整predict与预处理，5帧warmup；ARMv7l、Python3.8.2、NumPy1.17.4。优化版增量峰值901120B，模型+评估文件35246B；该体积统计不含部署包其余软件，也不等于整包大小。候选留在工作区作后续修复依据，当前运行中的板端仍是旧运行时；不能把本地候选PASS写成已部署PASS。



## 续报：C加速候选通过ARM P95

Task9 ARM P95已由C加速候选通过：600样本P95 65.16ms，RSS增量1,540,096B。主机test_rf_cnn_runtime.py 15 passed，板端parity最大误差1.53e-5。证据目录 runtime-c-accel/。已打包 cnn-v1 候选发布 6d46e3a5f9285dcc6949f2e0ab513082 并通过 verify_release；当前未安装。另完成低削顶 TRAIN/VAL 试点采集：TRAIN 18 有效帧（14 正确）、VAL 12 有效帧（9 正确），OTHER-CHIRP 仍高置信误判 BPSK。

## 证据、回归与安全收尾

证据根目录：`evidence/rx-smart/continuation-2026-10-03/`。关键记录包括：

- `trainval/diagnosis.md`、原始逐帧诊断与理想上限；`scratch-baseline-receipt.json`；`synthetic-reproduction-receipt.json`。
- `phase-augmentation/experiment-plan.json`、`preparation-receipt.json`、`training-invocation.json`、`status.md`。
- `numerics/frozen-corpus-verification.json`、两份独立审核收据；`numerics/board/`与`numerics/board-optimized/`的原始上传清单、逐层结果、100帧bench和失败退出码。
- `collector/receipt.json`与`trainval/collector-independent-review.json`：完整34passed、独立窄测7passed；精确release pin保持不接受旧ID、任意ID、格式错或设备身份不符。
- `pytest-corrected.log`：导出、评估、运行时专项50passed。首次命令误写测试文件名，未运行任何测试；错误日志保留，不算验证成功。
- `board-safe-readback.json`：本轮末只读当前daemon确认46fe…、online/fresh、control disarmed、RX ARM=false、TX source_selected=false、safe=true。

本轮板端只上传原已授权六份冻结TRAIN IQ、旧模型及纯numpy验证脚本到独立临时目录；没有加载Overlay、发射、改服务或安装候选。没有RTL修改、新PL综合、Git提交或整树恢复。只读安全收尾时板卡仍TX1→RX1直连、无天线。

曾尝试创建接续heartbeat，自动审批因没有明确的周期自动任务授权拒绝；没有创建或绕过任何自动任务。本轮在活动会话内推进，到额度门限后停止新增实验；后续不会自动运行。

下一轮应把C加速候选纳入发布前复核/打包（已更新cnn-v1软件映射与install.sh），再进行预声明的低削顶新TRAIN/VAL采集与方向覆盖实验。候选冻结后另用独立会话TEST评估，不能将本轮已诊断的数据或旧真实推理帧当新独立验收。真人触摸、30次操作P95、现场watchdog和示波器须分别完成，天线/LNA另行进入其工况。






