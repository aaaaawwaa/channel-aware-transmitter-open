# REVIEW — QAM16 trial bundle + RRC observation gate

日期：2026-10-24。范围：A 的 QAM16 trial stage/bundle/acceptance；B 的 RRC rootcause 观测门修正。复用既有 QAM 产品/ARM/RRC runner 审查，不重开全仓库/ARM/回滚战役。零安装/切服务/板卡/RF/commit。

## 1 结论

- **QAM16 trial bundle：PREPARED_EXECUTABLE，待 Codex 关键阶段审查，尚未部署。**
  - 实际候选/父/60+2 合同自洽；62 software 与候选 tgz 内 62 文件逐字节一致；stage 16 files 与 tgz 一致；本地实际产品入口验收 18/18（含 19 个布尔检查）通过，bundle tests 6/6；无占位路径。
  - 未安装，current 仍 `bedf98c7…`。
- **RRC observation gate：已修正为 OFF 可缺观测、physical ON 必须可用观测；离线 fake 通过。** 本轮未再物理运行，旧 1/112、2/112 保留；下一 rootcause 仍需新物理授权。
- **Codex：无有效 verdict。** 此前 review/exec 两次 provider 重连/无输出；本文件只作为待审查入口，不伪造 APPROVED。

## 2 QAM trial bundle

绝对路径：
`<LOCAL_PATH>`

| 项 | 实际值 |
|---|---|
| candidate | `cd76818afc3bee7c610678574e892b26` |
| parent/rollback | `bedf98c77283002c632dd7f345abaff9` |
| contract | `cnn-bpsk-modrec4-qpsk-rrc-qam16-v1` |
| software members | 62（tgz 共 72 tar files；已分开核对） |
| candidate.tgz | 1,695,506 B，`181bf027a321da6d4ceadd664a7ea24fdaa1660918a72a908deb35e8fb2a4816` |
| parent archive | `f17b53d8b0f8e16fbeb04f6714f941d4fcc3e043b703eb41bab78502ce4f95ac` |
| stage | `bundle/trial/`，16 files，`345461624b16212d0b8c011b389c2059b3cee331b23e8c9e13f781286cdd199c` |
| trial-stage.tgz | 3,751,266 B，`0a847d19b2c247ff09f9777c0c8d30ebd7e459c0c2f60779fba7d4271dd114dd` |
| unit | `rf-qam16-trial-cd76818a.service`；按全名启动，未 enable/未使用 alias |
| budget | 120 s |

本机实际验收：
- `python -B bundle/trial/trial/acceptance.py --release .../candidate/release ...` → `TRIAL_ACCEPTANCE_PASSED`，`passed=true`，QAM16/RECT **0/256**、valid127、align252、compare_start63、EVM unavailable；mismatch `REFERENCE_NOT_FOUND`；域外 `INPUT_DOMAIN`；worker same-frame 0/256；UI same-frame 0/256；配置切换清旧 BER；BPSK/QPSK/RRC 兼容；modrec4 smoke OK。
- `pytest bundle/tests/test_qam16_trial_bundle.py` → **6 passed**；QAM product/pipeline/receiver 105 passed。

## 3 RRC observation gate

- 事实：普通 OFF `rx.capture` 无 `capture_observation`；physical ON/loopback physical capture 才有。
- B 修正：运行前仍强制依赖/远端 package 布局/hook；OFF 缺失观测 → `OFF_PATH_NOT_PROVIDED`，继续、不伪造；OFF 损坏/身份不符/读取失败 → 停止；每个 physical ON 必须 sidecar `usable=True/stop=False/身份匹配/payload`，缺失/损坏/读取失败/stop → 停止+cleanup；不改发射/初始化/采集次数/工作点；six/next-min 兼容。
- 测试：`test_rf_rrc_real_session.py` **41 passed**；相关 observation 套件 **138 passed**。
- 本轮未再物理执行；A 上次 rootcause 实际预算 **1/1/1/0/1/0**，首 OFF `capture_observation_unusable` 停止，旧结果未变。

## 4 待 Codex 审查入口

见 `docs/reviews/2026-10-24-qam16-trial-codex-entry.md`：包含绝对路径/摘要、相对父版本差分、ARM 原始/对比、验收/成功/回退命令、测试、未决问题。Codex verdict **UNKNOWN/待审查**，不是 APPROVED。

## 5 风险/未决

- QAM 候选尚未安装；worker/UI 仅在主机/测试和 ARM 同输入验证，未真板 UI/RF。
- QAM trial acceptance 用 fixture；未做真实 QAM 采集。
- RRC rootcause 仍无 ON 数据，根因 UNKNOWN；下一轮需新物理授权。
- QAM 候选/包未变动；若 Codex 要求改动，必须重签候选和 stage。
