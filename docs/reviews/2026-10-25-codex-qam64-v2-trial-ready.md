# QAM64/RECT v2 候选与试部署增量审查

日期：2026-10-25。审查者：当前 Codex 对话，未参与本轮产品实现。复用既有安装事务审查，只检查 v2 算法、版本识别、具名失败与新 stage/验收的实际差分。

结论：**APPROVED_FOR_BOUNDED_TRIAL**。未发现阻止一次有限无 RF 试部署的 P0/P1。批准覆盖下列字节、120 秒包内验收和失败时既有事务回退；试部署通过后，可以在本批实际 RF 授权及有效真实接线确认下使用所列 v2 runner 做一批四帧测量。此结论不是用户操作授权，也不代表 v2 已安装或新鲜物理验收通过。

## 冻结身份

- candidate：`024fb7b130dc756f648a655560ef740f`。
- parent/fallback：`4ea1c0a68f03773fe1a77d1876ab1548`，即最近确认的已安装版本。
- 合同：`cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1`，64 software。
- candidate.tgz SHA：`3725382f98c37a3cb2e914f97d5d45575579efc1deb7092996ad0573dd06dd5e`。
- trial-stage.tgz SHA：`f320e53d73c04ea260fd3ea97f89dfdc7010ef97fea23496b14b1c5b49d65f03`。
- stage 索引 SHA：`bdc2c218c39e448341e6548e4ab534c4b07e9555ac9dbb7c2f8d6056d343405c`。
- parent 包 SHA：`5dd1b2eeb15befbffaf0e181fbf53ad19b374245ddb2457e3270501ba44ddb91`。
- trial/unit：`rf-qam64-024fb7b` / `rf-qam64-trial-024fb7b.service`。
- durable stage：`/var/lib/rf-deployment-trials/rf-qam64-024fb7b/stage`；入口链接：`/tmp/rf-teaching-install-rf-qam64-024fb7b`。
- 当前 v2 runner SHA：`0cdff4249d11132b6e87e3afe40b55515acd920064ac2471e03a0c594eea78cb`。
- session SHA：`7299123bb0485a6eb1ba0410da8bd5881bf052cdba61c89a74ac971db40bbd6f`。

材料根：`evidence/rf-teaching/qam64-rect-product-v2-2026-10-25/`。执行命令引用该目录 `NEXT-STEP.md`，不得混用旧 trial 身份。

## 关键差分核对

1. 父候选至新候选软件变化准确为三个文件：`rf_qam64_receiver.py`、`rf_modem_result.py`、`ui_model.py`；三个冻结文件与当前工作树一致。接收器版本为 `qam64-rect-snapshot-v2`，pipeline 从接收器引入版本，UI 与验收均已按 v2 校验，失败码加入 `ILL_CONDITIONED`。不把未改 pipeline 字节说成新增改动。
2. 阅读新增模型选择实现：仿射/细 CFO 拟合实际使用对齐区内前 62 个符号，以对齐 RMSE 和固定改善规则选择，不使用比较区 BER 作为分支条件；校正后比较区仍统计 64×6=384 位。数值有限性、矩阵条件及具名失败路径存在，失败无 BER；EVM 保持 unavailable。此审查不声称已定位具体硬件器件失真。
3. candidate 包 138 个成员与冻结 release、64 个源路径副本一致，包含安装入口。stage 包 17 个成员与散文件一致；索引现排除自身，16 个非索引摘要全部匹配。完整候选包、父包、stage 包及索引摘要均核对；旧自引用缺陷已消除。
4. stage 的基础 `install.sh`、`trial-install.sh`、`install_state.py` 与已审并成功使用过的 QAM64 v1 stage 逐字节相同；新预检从本包元数据取得候选/父身份，包摘要须由主机按本报告固定值核验。unit 绑定本次身份、v2 验收和当前父版本，实际板端入口不使用 `--host-offline`。

## 本次重新执行与核对

- QAM64 receiver/pipeline/alignment-v2/独立 impairments/product、QAM16 product、v2 runner：**226 passed in 18.09 s**。无再次执行未改安装事务的全套历史检查。
- 对当前 stage 重新执行主机预检：**9/9 PASS**。
- 对冻结候选实际运行包内 host acceptance：**22/22 PASS，1.594 秒**。host 模式跳过的板端状态不能当作板端已通过。
- 通过冻结候选实际产品 pipeline 重新解码四份历史 IQ：ON-A/B 均 **0/384**；前后 OFF 均未同步且计数为空。原 v1 实时 2/384、5/384 不改写；这些是已观察的历史样本，不是新鲜 VAL。
- ARM 收据：核对 5 个输入材料摘要（候选包、IQ、配置、比较脚本、host 结果），下载候选与本报告字节一致；18 个测量字段 host/ARM 相同，五次成功输出相同。耗时 min 3.013794、median 3.097749、max 3.471878 秒；仅证明固定输入计算，没有证明完整 worker/取消/实时吞吐预算。

本次机器核验：`evidence/codex-qam64-v2-review-20261025/verification.json`、`related-tests.log`、`host-preflight.json`、`host-acceptance.json`。本次未连接板卡、未上传、未安装、未启停服务、未初始化 RF、未发射或采集。

## 一次部署和新鲜验证边界

执行前必须新鲜只读确认父版本、双服务、boot、存储、RF safe、无冲突作业。包和 runner 绑定本报告实际摘要，执行中不临时改算法或文件。

一次 trial start；验收通过须有 `TRIAL_ACCEPTED`、本次真实 acceptance/state、目标 current/active、独立服务/RF 读回及 boot 一致。功能失败由既有事务回到 `4ea1c0a...`；缺少终态或失联先观察同一作业，不再安装、不并行回滚、不自动重启。

部署成功且接线/授权条件满足时，新鲜四帧预算为 4 sessions / 4 init / 4 loads / 2 ON TX / 4 captures / 0 retry，工作点沿用 80 MHz / 46 dB / amp60 / 60 MSPS / 3 Msym/s / 4096 / RECT / QI / seed39，总墙钟计划≤180秒、capture deadline 10秒。ON 目标 0/384、valid127、align378、start63，OFF 三同步 false、BER/计数空。非零 BER 保留为研究负结果，不能补采或用历史重放替代新的实时结果。

接线未改变且记录仍有效可按现行规则复用；真实性不能确认时只暂停 RF，不阻止无 RF 部署。新鲜 IQ 下载后可由离线代理并行重放，板卡仍由一个执行者控制。RRC 历史误码、QAM16 已通过工作点、EVM 未实现等事实继续保留，不扩展本次批准范围。
