# REVIEW — QAM16 trial state recovery（blocked）+ QAM16 real-RX runner prep

日期：2026-10-25。范围：B 的 QAM16 trial 状态恢复/唯一一次试部署；A 的 QAM16 真实 RX runner 离线入口。复用 `docs/reviews/2026-10-24-codex-qam16-trial-ready.md` 的 APPROVED_FOR_LIMITED_NO_RF_TRIAL；不重开 Codex，不重建候选。

## 1 实际阶段与证据

Phase1（只读，2026-10-25T09:09:28+08:00）：
- current/active-release = **bedf98c77283002c632dd7f345abaff9**
- boot_id = **1e08d422-dcfe-4c62-ae7d-9101fd9a0d67**
- `pynq-status`/`rf-daemon` active/running/NRestarts=0
- trial unit **not-found/inactive/dead**，journal 0
- persistent stage **absent**；无本地 trial 收据/状态
- RF idle；pre readback `VERIFIED_SAFE_EXPECTED`；storage 无 MMC I/O error
→ 证据证明 **从未派发**，进入授权 C。

Phase2（setup，无产品安装）：
- upload trial-stage SHA `0a847d19…`，展开 `/var/lib/rf-deployment-trials/rf-qam16-cd76818a/stage`
- stage **16/16** SHA match；preflight **6/6 passed**
- install unit 全名 `rf-qam16-trial-cd76818a.service`，disabled/inactive，no enable/alias
- **唯一一次** `sudo systemctl start ...` 已发出，`start_attempts=1`

失联：
- start/observe 无 terminal marker；ping 192.0.2.10 可达、TCP22 True，但 Paramiko **SSH protocol banner** 失败，未到认证、未执行命令
- **product install = UNKNOWN**，rollback = UNKNOWN；无 post readback
- 未清理 stage/unit；无第二次 start、无并行 rollback、零 RF/采集/Overlay

证据：
`evidence/rf-teaching/qam16-rect-product-2026-10-24/trial-execution-2026-10-25/`
（`phase1-state.json`、`execution-state.json`、`BLOCKED-UNKNOWN-REPORT.md`、`short-probe.json`、logs/source-sha256）。

## 2 本轮安装/验收/回退计数

| 项 | 实际 |
|---|---|
| trial dispatch/start | **1** |
| confirmed new install | **0**（outcome UNKNOWN） |
| confirmed rollback | 0 |
| retries/second unit | 0 |
| acceptance readback | NOT OBTAINED |
| independent readback | NOT OBTAINED |
| RF init/capture | 0 |

**不得**把 start 发出当成安装成功或失败；当前 current/active 未知。

## 3 A：QAM16 real-RX runner（离线）

- `rx_mode` 新增 QAM16；强制 tx_mode QAM16、RECT、rolloff0.35、显式 seed/QI；产品能力 `qam16_available`；提取实际 Qam16Envelope：0/256、valid127、align252、compare63、EVM unavailable；失败 reason。
- 固定 5 帧：OFF-first(QAM16/RECT,noTX) → QAM16/RECT seed39 A → seed39 B → 错误参考 TX39/RX40 → OFF-last(noTX)。
- 工作点 80MHz/46dB/amp60/60MSPS/3Msym/4096/RECT/0.35/QI；5 sessions/5 init/5 load/3 ON TX/5 capture/0 retry；总墙钟 180s。
- 命令：`python -B scripts/rf_qam16_real_validation.py --expected-release cd76818afc3bee7c610678574e892b26 --output <new-empty-dir> --cable-confirmed`
- fake 8 passed；QAM+RRC runner 合并 49 passed（Lead 修复测试 sys.path 后复跑）。
- 未连接板卡/未安装/零 RF；冻结部署包/候选未改。

## 4 下一步恢复判断（不自动执行）

现场 console/电源/网络检查；恢复后只读按顺序：
1. unit 是否 running/exited + journal；
2. current/active-release、boot_id、服务、storage、RF idle；
3. 若 candidate 已安装：下载原始 TRIAL_ACCEPTED/state/acceptance，核对 SHA，独立 post readback；
4. 若 parent 且 unit 有 TRIAL_ROLLED_BACK：下载 rollback-state/日志，独立 `--rollback-state` 本地 JSON + parent release 读回；
5. 仍 UNKNOWN/无 marker：保留 stage/unit，不重装/重 start/并行回滚；
6. 只有确认原事务完成后才清理 stage/unit。

## 5 边界

- 当前板卡失联；不得声称部署成功/回滚成功/真实 QAM 接收。
- QAM 候选未确认安装；真实 QAM RX 工作包需 candidate 已部署 + 新物理授权 + 当前 TX1→RX1 接线确认。
- Codex APPROVED_FOR_LIMITED_NO_RF_TRIAL 有效，但不替代这次 UNKNOWN 终态处理。
