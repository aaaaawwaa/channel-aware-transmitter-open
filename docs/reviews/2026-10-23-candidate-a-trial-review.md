# DSH 候选 A：工程交付与下一步部署审查

日期：2026-10-23。本次为新候选/安装大阶段的一次 Codex 增量审查，按 `docs/requirements/development-review-policy.md` 执行。

## 结论

**APPROVED_FOR_LIMITED_NO_RF_TRIAL：候选已具备一次开发版试部署的条件；实际安装须在用户授权范围内执行。**

当前阶段是“候选工程化与 ARM 离线验证完成 → 待安装和安装后独立读回”。不是已经安装成功，也不是四类信号的全工况交付验收通过。

- 候选：`da97347ab29d0b31513460e40bcce99f`。
- 父版本/回退目标：`4776ba0bf6cabac383934f0437358595`。
- 最近板端证据来自 task-82，current/active 仍是父版本；本次审查未连接板卡，不能把该记录表述为此刻实时状态。
- DSH 主会话最终交付时间为 2026-10-23 10:00:29；模型会话最终交付时间为 09:23:31。

## 本次独立核对

1. 工程证据 `evidence/rf-teaching/s2-46db-candidate-a-engineering-2026-10-23/SHA256SUMS.json` 的 160 项、ARM 证据 `evidence/rf-teaching/s2-46db-candidate-a-arm-2026-10-23/SHA256SUMS.json` 的 32 项，逐项读取实际文件计算 SHA256，全部相符。
2. 直接比较 candidate.tgz 与 rollback-parent.tgz：成员集合相同，只有四个文件字节变化：模型、规则、manifest、build receipt。未改变 bit/HWH、daemon、软件模块、六类模型与基线模型。
3. 安装器、事务试部署 wrapper、安装状态检查器与工程当前对应脚本逐字节相同，复用既有回退实现，不另起一轮整套安装器审查。
4. 主机回放报告为旧 14 + 新 10 帧，24/24 label、accepted 与锁定 A 参考一致。这是计算一致性，不能把“24/24 一致”解释成独立测试集准确率 100%。
5. ARM 原始交付记录：24/24 label、accepted 与主机一致；scores 最大差 8.161e-7；logits 最大差 5.722e-6；p50 62.3 ms、p95 66.8 ms、最大 RSS 27400 KiB。四类错误输入均 UNAVAILABLE。上述数值为 task-82 板端实测，本次未重新运行板端测试。
6. 本次实际运行包内 `trial/acceptance`，指定候选 release、从回退归档提取的父 release、包内现有 `nf-00-normal` IQ。退出码 0，12 项检查全部 true，`passed=true`；分析、六类、BPSK、环境、四类路径均执行。没有发射、采集、安装或加载 Overlay。

本次主机验收结果：`evidence/rf-teaching/codex-candidate-a-trial-review-2026-10-23/acceptance-host.json` 和 `acceptance-host.log`。此结果不是安装后板端验收收据。

## 部署操作说明的两处修正

这两项属于操作衔接，纳入同一次试部署即可，无需新候选或重新训练。

### 1. 先注册 unit，再启动

包内只有 `rf-modrec4-trial-da97347a.service.in`；解压不会自动注册该服务。新鲜预检通过且已有安装授权后，须把该文件安装为 `/etc/systemd/system/rf-modrec4-trial-da97347a.service`（root:root、0644），执行 `systemctl daemon-reload`，核对 `systemctl cat` 的 ExecStart 与受审模板相符，再启动该 unit 一次。

不要使用通用 alias 启动，以免命中旧 unit；不要因 SSH 断线另起第二个试部署作业。不要删除已有候选目录/旧试部署状态来绕过“只运行一次”的检查。

### 2. 使用真实存在的读回判定

`scripts/rf_environment_readonly_check.py` 实际返回的新版本安全成功值为 **`VERIFIED_SAFE_EXPECTED`**，不是计划中写的 `VERIFIED_SAFE_NEW_RELEASE`。

新版本保留条件：`TRIAL_ACCEPTED` + current/active=候选 + 双服务正常 + 无新增存储错误 + 独立读回 `passed=true`、`verdict=VERIFIED_SAFE_EXPECTED`，且 boot_id 与本次安装前一致、TX source false、RX armed false。

回退确认使用现有 `--rollback-state` 与 `--rollback-candidate`，判定 **`VERIFIED_SAFE_ROLLBACK`**。该工具此分支可能仍是 `passed=false`，表示没有得到预期的新版本；应按 rollback verdict、父版本身份及安全态确认回退，不能要求它同时 `passed=true`。

## 开始前与失败处理

- 安装前重新只读核对 current/active=父版本、boot_id、服务、存储、RF 空闲态。task-82 的历史 post 不能代替此检查。
- task-82 的 pre RF 读回未取得，不能补写为通过；其离线 ARM 回放不需要因该历史缺项重做。此次部署取得新鲜 pre 即可。
- 只允许一次候选安装，现有 wrapper 的 120 秒验收基于已保存 IQ，零新 RF 发射/采集。授权应同时涵盖失败时恢复父版本及独立读回。
- 无 RF 安装不需要为了沿用旧射频实验而重新确认接线；后续真 RF 实验需要自己的有效接线确认和预算，旧 task-80 授权不能复用。
- 验收失败由现有事务恢复父版本。未知、断线、重启或存储错误时先观察原 unit、current、状态文件与日志，不重复安装，不扩大射频操作。
- 获取终态并回传证据之后再清理临时目录，不能先删回退状态文件。
- 不手动在服务持有 PL 时调用 Overlay；按既有安装器的停服务、切换、恢复服务流程执行。

## 算法边界

A 修复了此次小样本的 QAM16/OFF 问题，但合成 BPSK test recall 从 93% 降到 88%，仍有已知退化。task-80 BPSK 只有 1 帧，不能关闭该问题。试部署用于验证工程安装与计算链路，可保留开发候选；不能据此宣称广泛信号条件识别达标。

六帧回放包 `ml_training/candidate_a_replay_package/` 已包含五个合成样本和一个真实 OFF 样本，包括保留错误的负例；它用于后续计算一致性定位，不要求候选把这些已知错误全部预测正确。

## 下一步分配

1. **主对话 / 唯一板卡操作者**：在用户给定授权后执行一次无 RF 试部署与终态独立读回；通过保留、失败回退。直接复用本审查，不再增加同范围审查代理。
2. **模型对话 / 离线**：冻结 A 及六帧包，暂停新训练与候选搜索。若主任务需要，可并行整理下一次真实验证的最小矩阵和停止条件，重点覆盖 BPSK 退化与 QAM16/OFF；不得接触板卡或把此文档工作设成当前安装的新前置门。
3. **部署完成之后**：依据实际安装终态决定真实接收验证。此阶段不启动 SDR 基础测试，也不提前重复旧 24 帧 ARM 战役。

本审查不代表已经向 DSH 派发命令或替用户签发物理授权。
