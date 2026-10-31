# REVIEW — QAM16 trial recovery + QAM17 real-RX runner fix

日期：2026-10-25。A 只读恢复 QAM trial 原事务；B 离线修 `rf_qam16_real_validation.py`。复用 `2026-10-24-codex-qam16-trial-ready.md` 的 APPROVED_FOR_LIMITED_NO_RF_TRIAL；不重开候选/ARM/bundle/Codex。零安装/start/回滚/清理/RF/commit。

## 1 恢复到的实际状态（A，只读）

- SSH：一次短超时 Paramiko 连接成功（0.578s）；随后一次有界只读补读，无 start/安装/回滚。
- **boot_id = `268b7a15-e037-4a59-83e8-ab87ceba771e`**；安装前 boot = `1e08d422-dcfe-4c62-ae7d-9101fd9a0d67` → **BOOT_CHANGED_CROSS_REBOOT**，当前 uptime ~2 min。
- **current / active-release = `bedf98c77283002c632dd7f345abaff9`（parent）**。
- `pynq-status` / `rf-daemon` active/running、NRestarts=0。
- `rf-qam16-trial-cd76818a.service`：**inactive/dead**，ExecMainStatus=0；当前 boot **No entries**；上一 boot 日志无终态 marker。
- storage：root ext4 rw、5.6G free，无新 MMC/SDHCI/ADMA/I-O/ext4 错误；FAT dirty 为旧提示。
- **RF state = NOT_READ/UNKNOWN**：补读时通道超时，未声明 RF 空闲。
- 持久 stage 存在（root 700）：含 `pre-state.json`、before snapshots、candidate/parent tgz、install/state/wrapper/preflight、unit 模板、TRIAL-STAGE/stage-sha、trial acceptance/host-acceptance；**缺失** `post-state.json`、`trial-state.json`、`trial-rollback-state.json`、`trial-acceptance.json`；当前 boot 无 unit 日志。
- 结论：**installation = UNKNOWN/INCOMPLETE_NOT_PROVEN；rollback = UNKNOWN；acceptance = UNKNOWN/NOT_PROVEN；terminal = NONE FOUND**。current=parent 不等于同事务回滚证据；不把跨重启后双服务正常当原验收通过。
- 未清理 stage/unit；未启动/安装/回滚/服务切换/RF。证据：`.../trial-execution-2026-10-25/recovery-2026-10-25/RECOVERY-REPORT.md`。

## 2 本轮安装计数

- 本轮新增安装/start/回滚/清理：**0**。
- 原 trial：此前唯一 start_attempts=1，终态 UNKNOWN；不能重试。
- 新四帧 QAM 实测未执行。

## 3 QAM real-RX runner 修复

- F1：`_structural_problem` 不再要求 0/256；合法非零 BER（0..256）与合法失同步保留为 metric negative，继续后续帧；身份/配置/损坏/超时/cleanup/OFF 残留停止；有效 BER 校验分母256、整数范围、`ber=errors/compared`；`execution_complete ≠ metrics_passed`。
- F2：删除 TX39/RX40 “必须失同步”物理帧；不强制拒绝 seed40；真正 mismatch 使用既有 `qam16-mismatch-qi.iq`（实际 `REFERENCE_NOT_FOUND`、无 BER）。
- 4 帧计划：OFF-first(QAM16/RECT39,noTX) → QAM16/RECT39 A → QAM16/RECT39 B → OFF-last(QAM16/RECT39,noTX)；预算 **4/4/4/2/4/0**；工作点不变；逐帧 metrics 预写。
- 命令：`python -B scripts/rf_qam16_real_validation.py --expected-release cd76818afc3bee7c610678574e892b26 --output <new-empty-dir> --cable-confirmed`
- 测试：QAM runner **19 passed**；QAM/RRC/QPSK related **70 passed**；未连接板卡/未执行；冻结候选/bundle 未改。
- SHA：runner `0ce4694641a2a419cfa2321471c34f4eb4af6b29643e497e1f151f7d17e83604`；tests `cf7644fb80fbf8810f6b189b51202fefd37557fdc56bd81c0e571d5813b4b10d`。

## 4 下一步判断（不自动执行）

当前不能声明安装/回退/验收，也不能直接跑 QAM 实测。需要一次**新的只读补读授权**（平台/要求短连接）：
1. 读取 stage 内 `pre-state.json` 内容、上一 boot 完整 journal、candidate release 目录存在性、RF 状态；
2. 确认是否有可证明的原事务终态；若仍无，保留 UNKNOWN；
3. 不 start、不安装、不回滚、不清理；确认状态后再决定是否需要全新一次 trial（需新授权）。
若现场 console 可用，也可先由现场只读确认 current/unit/journal；不要猜 COM/关闭其他串口/自动断电。

## 5 边界

- 不把重启后的 parent/服务正常当原 trial 验收通过；不宣称安装/回退成功。
- Codex limited trial 审查仍有效，但原 trial 终态 UNKNOWN 必须先解决。
- QAM 真实接收需 candidate 实际安装/current 且新物理授权/接线确认；本轮不运行。
