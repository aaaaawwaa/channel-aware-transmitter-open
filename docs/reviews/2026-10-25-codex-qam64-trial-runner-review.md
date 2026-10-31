# QAM64 试部署包与四帧 runner 增量审查

日期：2026-10-25。审查者：当前 Codex 对话，未参与本轮 stage/acceptance/runner 实现。

结论：**APPROVED_FOR_BOUNDED_TRIAL**。以下冻结 stage 可在用户授权下执行一次无 RF 试部署、包内有界验收、通过保留或失败按既有事务回退。四帧 QAM64 runner 的本次离线代码审查通过；实际 RF 执行仍以候选成功安装、有效真实接线确认及本批操作授权为前提。本报告不是已经安装、真实接收通过或授权工件。

未发现阻止上述有限试验的 P0/P1。本次未连接板卡、未上传、未安装、未启停服务、未初始化 RF、未发射或采集；板卡当前状态仍需执行前新鲜只读核实。

## 受审字节与范围

- 候选：`4ea1c0a68f03773fe1a77d1876ab1548`；父/回退：`cd76818afc3bee7c610678574e892b26`。
- 复用候选审查：`docs/reviews/2026-10-25-codex-qam64-candidate-review.md`，不重审未改算法。
- stage：`evidence/rf-teaching/qam64-trial-layout-2026-10-25/trial-stage.tgz`。
- stage 包 SHA-256：`7263172bd5632976d9015dce7fc19a323a0002d3281e79d7d5dde3b0fa7d5588`。
- stage 索引实际 SHA-256：`ebd075be0b7d50c692e2a52efb247c47e2614942bf34cf69e1cfcce303d183e5`。
- 内含候选 SHA-256：`5dd1b2eeb15befbffaf0e181fbf53ad19b374245ddb2457e3270501ba44ddb91`。
- 内含回退包 SHA-256：`440ba377a84be4ac2ee6e6cd85980106271cff4a26863235cb84718bc8273c17`。
- runner：`scripts/rf_qam64_real_validation.py`，SHA-256 `9873a7bf6dc327f6dc0c0dd16af3744edf6f5ac71a0da7296ee85d2cf8e0353e`。
- session：`scripts/rf_bpsk_ui_board_session.py`，SHA-256 `7299123bb0485a6eb1ba0410da8bd5881bf052cdba61c89a74ac971db40bbd6f`。
- unit：`rf-qam64-trial-4ea1c0a.service`；durable stage：`/var/lib/rf-deployment-trials/rf-qam64-4ea1c0a/stage`；入口链接：`/tmp/rf-teaching-install-rf-qam64-4ea1c0a`。

## 核查和新鲜验证

1. 读取新增验收、预检、unit、runner 和 session 的 QAM64 分支。stage 的 `install.sh`、`install_state.py`、`trial-install.sh` 与成功使用过的 QAM16 layout2 逐字节相同；安装前完整布局校验、安装与验收同事务回退路径继续保留。unit 验收不带 `--host-offline`，通过当前 release 的实际产品模块运行，120 秒只约束验收命令。
2. 完整 stage 包 17 个常规文件，无重名、绝对路径或路径穿越，归档成员与散文件逐字节一致；候选/父包摘要均匹配。除下述索引自身条目外，其余成员摘要全部匹配。
3. 本次重跑主机预检 **9/9 PASS**；对冻结候选运行实际 acceptance，**22/22 PASS、1.453 秒**。host 模式明确跳过板端 RF/service/boot，不能将该项 skipped 当成板端通过。board 模式会执行真实只读状态检查，外部执行器仍须比较安装前后 boot、服务及独立安全读回。
4. 本次重跑 QAM64/QAM16/QPSK/RRC session 与 QAM64 receiver/pipeline 相关测试：**218 passed in 7.53 s**。
5. 本次重跑既有试部署 wrapper 测试：**11 passed in 9.65 s**，覆盖已有成功、验收失败和超时等事务行为。DSH 自带 `run_offline_tests.py` 的模拟 installer/state checker 不能单独证明真实回退；本结论同时依据未改的既有事务实现和相关回归。
6. runner 固定四帧及预算 4 sessions / 4 init / 4 loads / 2 ON TX / 4 captures / 0 retry；10 秒 capture 参数实际传入，180 秒批次计划。产品能力、显式 QAM64 提交、384 位结果与 OFF 空计数路径均有测试。合法非零 BER/合法失同步与结构性错误分开，不为获得零误码重采。计划总时限不等于所有底层同步调用均可即时抢占，执行器须观察实际作业状态，超时后不重派。

本次验证文件：`evidence/codex-qam64-trial-review-20261025/verification.json`、`host-preflight.json`、`host-acceptance.json`、`runner-tests.log`、`trial-wrapper-tests.log`。

## 非阻塞发现：索引自引用旧摘要

`trial/stage-sha256.json` 的 `files` 中错误包含了 `stage-sha256.json` 自身，列出的 `7fbefe67...` 是旧摘要，而该文件实际摘要是 `ebd075be...`。一个普通摘要清单不能通过包含自身最终摘要来构造稳定的自校验。

本次无需重打包：外部绑定的完整 stage 包摘要及索引实际摘要已经核实，且包内其余 16 个文件摘要匹配。当前包的执行前校验方式是：先核对完整包 SHA 和索引 SHA，再核对清单中的所有非索引成员；**只对该已说明的自引用条目不作递归比较**，不能泛化为忽略其他摘要不一致。保留冻结包与原始记录。

后续维护打包器时将索引自身排除，并使用新输出目录；当前 `build_bundle.py` 的默认输出仍指向旧 `qam64-rect-product-2026-10-25/bundle`，含覆盖输出行为，本次不直接重跑它来重建或混用本受审包。

## 一次执行与终态要求

执行前确认 current/active 为父版本、服务和存储正常、RF safe、boot 稳定、无残留 trial；检查 stage/unit 未被使用。使用完整名称注册 unit，不 enable、不建立 alias。

成功须同时有 `TRIAL_ACCEPTED`、本次真实 `trial-acceptance.json` passed、`trial-state.json`、新候选 current/active 和独立服务/RF 安全读回；boot 变化不能冒充同次成功。验收失败沿既有事务回父版本，下载 rollback JSON 后从主机独立读回；缺少终态或失联则观察同一 unit，不并行回滚、重新安装或自动重启。

无 RF 试部署通过后，用户可在同一明确工作包中另授权本批四帧物理接收；仍有效且确认未改的接线记录可以按现行规则复用，不要求为了候选变化重新插拔。若接线真实性或有效期无法确认，只停在 RF 之前。RF 非零 BER 属研究负结果，本身不触发软件回退或自动补采。

不增加全量复审、固定变异配额、手工 nonce 流程或重复 ARM 固定输入测试。执行器只需真实满足现有接口。本批准覆盖所列字节和有限流程；实际产品代码或验收行为变化时仅补受影响差分。
