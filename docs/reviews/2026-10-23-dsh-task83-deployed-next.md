# DSH task-83 部署结果核对与下一步分配

## 结论与进度

2026-10-23 主会话最新完成时间为 17:13:25。task-83 **安装终态可接受，候选 A 已保留**；模型会话仍停在固定六帧回放包交付，没有新增训练结果。

开发位置：候选工程化 → 主机/ARM 离线回放 → **开发版部署完成** → 待已安装版本的真实接收验证。完整工作频段、信噪比及偏差条件的识别资格仍未完成，不据此估算一个缺少验收清单依据的总体百分比。

本次只核对离线交付证据，未连接板卡，也未派发 DSH 消息。

## 独立核对内容

证据目录：`evidence/rf-teaching/s2-46db-candidate-a-trial-deploy-2026-10-23/`。

- `SHA256SUMS.json` 的 50 项实际文件摘要全部相符。
- 原始 `logs/trial-terminal-full.log` 和 `logs/trial-poll.log` 均含 `TRIAL_ACCEPTED release=da97347ab29d0b31513460e40bcce99f`；unit Result=success、ExecMainStatus=0。
- 两份 `result/*.reconstructed.json` 与从 `logs/trial-poll.log` 直接用 JSON 解码器解析出的两个完整对象分别全等。原临时 JSON 未成功下载的缺口仍保留；不把重建副本冒充原文件。现有日志加独立状态读回足以支持本次安装终态，不为补原件重复部署。
- pre 主机时间 17:04:20+08；post-final 主机时间 17:10:11+08。
- pre 为父版本 `4776ba0bf6cabac383934f0437358595`；post current、active、release_verified 均指向 `da97347ab29d0b31513460e40bcce99f`。
- 同一 boot_id：`48c422a2-cdc3-47f3-8a8b-3c0864325e5f`。journal 的 Dec 07 是板钟日期，不能作为实际主机执行日期；使用主机时间、boot_id 与 uptime 关联。
- `readback/post-final/post-readonly-check.json`：passed=true、VERIFIED_SAFE_EXPECTED、failures=[]。原始 snapshot/state 日志也确认双服务 active/running、NRestarts=0，TX source=false、RX armed=false，未见存储错误。
- 清理前 post 的 B10 临时目录失败与清理后 post-final 成功一致；无需为该临时残留重启或回滚。
- 历史日志中存在大量 Ethernet link down/up，最后所见相关记录早于安装；此次部署完成且读回成立，不据此阻塞后续。若下一轮连接再失稳，先观察原作业、取证和只读确认，不能把断线当成未执行而重复初始化。

已知算法限制：A 的合成 BPSK test recall 为 88%，原模型 93%；本次验收使用旧 IQ、没有新 RF，未关闭该退化，也未证明安装后的实时 worker 分类已经通过。

## 下一步：两条并行任务

### 主线：当前已安装 A 的最小真实接收冒烟

目标是验证新安装身份下的“真实采集 → 实际产品分析路径 → 保存结果 → 收尾读回”。不再重复安装或整轮 ARM 离线资格测试。

建议一次 10 帧上限：OFF-first 1，BPSK 3，QPSK 1，QAM16 2，QAM64 2，OFF-last 1。其余工作点复用已验证配置：LO 80 MHz、内部衰减 46 dB、TX 数字 amplitude=60、fs 60 MSPS、3 Msym/s、RECT、4096 complex samples。

采用现有 task-80 流程，在新目录准备与当前候选绑定的 plan/truth/runner；旧脚本可能绑定旧发布或旧摘要，不能原样重派，也不能删除身份断言绕过。只做当前身份、帧矩阵及必要参数的最小迁移与相关离线自检，不重开整套审查。

预算按既有逐会话方式：总计最多 10 次 rf.init、10 次 load、8 次 ON/TX、10 次 capture，每帧仅一次，无自动重试；若现有接口预算更紧则先在离线准备时报告差异，不临场突破。新 nonce/attempt_id 由工具生成，旧已消耗授权不用。

**实际 RF 前须取得用户的新授权和带真实时间的当前接线确认**。只接受确认过的板卡 TX1→板卡 RX1；不能从历史留言推断现在仍连接，也不将无外接衰减器的 SDR TX→板卡 RX 作为替代。接线确认等待期间可以完成离线准备。

本批定位为开发版冒烟：报告 BPSK 3/3、QPSK 1/1、QAM16 2/2、QAM64 2/2 及 OFF false-accept 0/2 是否达到；未达到照实给出负结果，不宣称总体准确率，不自动扩大采集或调参。普通误分类不自动回退；软件功能异常或安全停止条件按既有 runner 停止并收尾，安装回滚不从 RF 授权自动推导。

必须保存 raw IQ、原始元数据、实际 TX 下发 payload SHA/参数、当前候选身份、产品分析输出及独立结束读回。不得以 PC 离线分类替代板端产品输出；离线回放只作并列定位。不将此批追加到 TRAIN。采集停止后先下载并校验必要原件，再清理临时文件。

### 模型线：一次离线 BPSK 定向修复实验

与主线无设备和写目录冲突。冻结已安装 A，不覆盖其模型、规则及六帧包；新候选放独立目录。

复用已有 BPSK 退化诊断，在 TRAIN 上进行一次有界修复实验，使用 VAL 选择 checkpoint；不依靠已知 test 或 task-80/本次新采集调参，不随意降低拒识/仲裁阈值。先以现有 TRAIN 的 BPSK/QPSK 边界训练策略为切入点，最多训练一个候选，不并行展开多套大规模搜索。

交付应包含 train-only 变更、训练配置/seed、VAL 各类 recall/拒识率/OFF 误接受和与 original/A 的并列差异。既有 test 可作已知回归比较，但不得称新独立验收集。负结果也交付，不循环重训。模型线不连接板卡、不部署、不阻塞主线冒烟；仅在新候选确有价值且准备工程接入时做对应候选阶段的增量审查。

## 审查节奏

本次关闭 task-83 安装结果核对。常规矩阵迁移与离线实验由 DSH Lead 一次增量核对；不要再添加规格审查、封存审查或审查的复审。下次交 Codex 的重点是新采集结果的实质问题或新的安装候选。
