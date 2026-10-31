# REVIEW — QAM16 layout2 no-RF trial ACCEPTED

日期：2026-10-25。范围：一次 layout2 无 RF 安装、包内 120s fixture 验收、独立读回。未重开候选/算法/ARM/包审查。

## 实际目标

- candidate `cd76818afc3bee7c610678574e892b26`；parent/fallback `bedf98c77283002c632dd7f345abaff9`。
- trial `rf-qam16-cd76818a-layout2`；unit `rf-qam16-trial-cd76818a-layout2.service`。
- candidate.tgz `440ba377a84be4ac2ee6e6cd85980106271cff4a26863235cb84718bc8273c17`；trial-stage.tgz `746e156f092f44d5f2d053e0471fa2eb427541898965869d6bee66a6f5e2299d`；stage-sha `441c2f48…`。

## 门禁/异常透明记录

- 新鲜 pre：parent bedf、boot `268b7a15…`、双服务 active/NRestarts0、RF idle、无残留作业。
- 7/7 preflight 全 true（Lead 曾误写 8/8，已确认 8/8 为笔误；未伪造第8项）。
- 第一次 `sudo systemctl start` 在 SSH 非交互下被 sudo 在 systemctl 调用前拒绝；journal 无条目、unit 仍 inactive、current 仍 parent，**不是一次实际启动**。
- 随后唯一一次实际 `sudo -S systemctl start`；实际 systemctl start 调用 = **1**。

## 终态

- journal：`INSTALL_ACCEPTED` → `TRIAL_ACCEPTANCE_PASSED` → `TRIAL_ACCEPTED release=cd76818afc3bee7c610678574e892b26` → Finished。
- trial acceptance：**passed=true，19/19 checks，elapsed 9.965s，budget_ok**。
  - QAM16/RECT：0/256、valid127、align252、compare_start63、EVM unavailable；
  - mismatch/domain/worker capability/worker same-frame/UI same-frame/config clear/stop 均 true；
  - compat BPSK 0/138、QPSK 0/128、RRC 0/112；modrec4 smoke true。
- current/active-release = **cd76818afc3bee7c610678574e892b26**。
- 双服务 active/running，NRestarts=0；boot 未变；无 MMC I/O 错误。
- layout2 unit loaded/active/exited、Result=success、ExecMainStatus=0、disabled。
- 独立 RF readback：safe=true，release candidate，armed=false，daemon_state=OFFLINE，core=false，pl.io_ok=true，tx.source=false，rx.armed=false；**零 RF**。
- 回退：无 `trial-rollback-state`，未回退；旧 `rf-qam16-cd76818a` unit/stage 保留，未重启/未清理。
- 实际安装 **1**；实际 systemctl start **1**；RF 0；回退 0；清理 0；未 commit。

证据：`evidence/rf-teaching/qam16-rect-product-2026-10-24/bundle-layout2/execution-2026-10-25/`（`ACCEPTED-REPORT.md`、`execution-state.json`、`trial-state.json`、`trial-acceptance.json`、`post-readback.json`、`journal-live.log`、`evidence-sha256.json`）。

## 边界

- 本结论是**无 RF fixture 试部署验收**，不是真实 QAM 接收、物理 BER、实体屏或正式发布。
- 原 cd76818a 失败 trial 的安装入口失败保留；其回退终态仍 UNKNOWN，不被本次成功覆盖。
- 下一次四帧 QAM 真实接收需要新的物理授权/接线确认，本轮未执行。
