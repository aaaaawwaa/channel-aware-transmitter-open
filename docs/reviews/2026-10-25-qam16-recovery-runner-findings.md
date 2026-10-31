# QAM16：部署状态恢复与实测 runner 增量问题

日期：2026-10-25。当前 Codex 对话只读核对 DSH 第 95 轮完成记录和本地证据，并运行两项离线复现；未连接板卡、未安装、未发射或采集。

## 已知部署状态

`trial-execution-2026-10-25/execution-state.json` 记录：安装前 parent 为 `bedf98c77283002c632dd7f345abaff9`，boot_id 为 `1e08d422-dcfe-4c62-ae7d-9101fd9a0d67`；上传及 preflight 成立，`rf-qam16-trial-cd76818a.service` 已发出一次 start。之后 SSH banner 失效，未取得终态、验收或 current/active 读回，因此安装和回退都是 UNKNOWN。ping/TCP22 可达不证明 Linux 正常，也不证明死机。先检查同一作业，不再 start、不清理、不并行回滚。

本次两项 runner 问题不在冻结候选或已批准的无 RF trial bundle 内；不因此撤销 `2026-10-24-codex-qam16-trial-ready.md` 的有限试部署审查，不重建产品来修实验脚本。

## F1：QAM runner 再次将普通 BER 负结果当成结构停止

文件：`scripts/rf_qam16_real_validation.py`。

`_structural_problem()` 调用 `qam16_evidence_problem()`，后者要求严格 `(0,256,0.0)`。因此一个同步成功且计数合法的 `1/256` 返回 `qam16_zero_of_256_mismatch`，整批停止；同一记录 `_metric_problem()` 同时返回 `metric_negative_nonzero_ber`。这与 runner 文档的“非零 BER 不停止后续帧”不一致。

离线构造合法字段记录直接调用上述函数，已复现。证据：`evidence/codex-qam16-runner-review-20261025/ber-stop-check.json`。该记录是明确标记的离线构造输入，不是板端原始帧。

最小修正：结构合法性与指标达标分开；合法非零 BER、合法失同步继续且记负结果，合法计数仍核对分母/范围/BER 一致性。执行完整不等于指标通过。沿用已修复 RRC runner 的相关语义，补真实接口测试，不再只靠恒为零的 fake 场景。

## F2：TX39 / RX40 不是本接收机的错误参考负控

计划中的 `qam16-rect-wrong-ref` 发 PRBS7 seed39、参考 seed40，并要求拒绝同步。冻结候选 receiver 搜索所有 127 个循环参考偏移；不同非零 PRBS7 seed 是周期序列的相移，因此换 seed 不能保证得到不匹配序列。

独立离线复现：对实际 trial fixture `qam16-clean-qi.iq`，通过冻结候选 `cd76818afc3bee7c610678574e892b26` 的 `freeze_qam16_demod_config()` / `analyze_qam16()`，分别用 reference_seed39 和 40；两者均 `reference_sync=true`、reason 为空、`0/256`、BER0。证据：`evidence/codex-qam16-runner-review-20261025/seed-shift-check.json`。

不要改接收算法强制拒绝本可正确对齐的 seed40，也不要把正常同步误报成算法故障。错误数据/参考拒绝继续使用真实不匹配的既有离线 fixture。首次物理冒烟可简化为四帧：OFF-first → QAM16/RECT39 A → QAM16/RECT39 B → OFF-last；4 sessions / 4 init / 4 loads / 2 ON TX / 4 captures / 0 retry。此处只是下一计划建议，不授权执行物理采集。

## 下一工作包

并行：一人短时只读恢复同一安装事务状态；另一人离线修复 runner 和真实产品接口反例。SSH 仍不可用时先收集现场已确认 Linux console 的信息；未确认串口用途时不猜端口、不关闭其他串口会话。现场恢复后先下载持久 stage/journal/state 并核对 boot_id、current/active、服务和存储，再决定后续动作。恢复前不启动 RF，不重装，不自动重启或手动回滚。

两个 runner 缺陷完成一次主代理增量核对及相关测试即可；不为此重开候选、ARM 或安装包全审。
