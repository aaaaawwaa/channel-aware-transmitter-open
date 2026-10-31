# REVIEW — RRC rootcause（观测门阻塞）+ QAM16/RECT product candidate

日期：2026-10-24。范围：A 的 rootcause 观测 fail-closed 修复与一次物理执行；B 的 QAM16/RECT worker/UI 接入、唯一候选、无 RF ARM。复用既有 ARM/部署/算法审查；不重开全审。零安装/回滚/训练/commit。

## 1 结论

- **RRC rootcause：本轮未能取得 ON 帧数据。** 首 OFF 帧观测 sidecar 存在且身份匹配，但 `usable=false / stop=true / OBSERVATION_MEMBER_MISSING`；runner 按约定收尾，**0 ON TX**，未重跑。根因仍 **UNKNOWN**；旧 1/112、2/112 保留。
- **QAM16/RECT：离线产品接入与唯一候选已完成，Lead 增量审查通过；Codex 审查尝试因 provider 无响应未取得 verdict，不得记 APPROVED；未安装。** 129 product/regression tests（B）、Lead 105 relevant tests；ARM 同输入 0/256、62/62 candidate SHA、host/ARM 测量字段一致；ARM 无 worker/UI，取消由离线集成测试覆盖。

## 2 A：观测门与 rootcause 实际执行

修复：
- rootcause plan 要求 observation：远端 `remote/scripts/` package 布局检查、forwarder 导入、header hook 安装/flag 验证；缺布局/导入/hook 分别具名退出，失败关闭 client，**在 worker/init/TX 前停止**。
- 首 OFF 必须 sidecar 存在、capture/request/timestamp 身份对应、`usable=true`、`stop=false`、observation 非空；缺失即收尾，不进 ON；后续帧 stop 同义。
- `six`/`next-min` 保持观测可选，不改变旧行为。

实际一次派发（唯一，未重跑）：
| 项 | 值 |
|---|---|
| run_id | `rrc-rootcause-20261024T122728-cc0156` |
| 接线 | 2026-10-24T20:15:46.869+08:00 TX1→RX1 |
| 预算实际 | **1 session / 1 init / 1 load / 0 ON TX / 1 capture / 0 retry** |
| stop | `capture_observation_unusable:01-off-first` |
| OFF sidecar | 存在/身份匹配；`usable=false, stop=true, reasons=[OBSERVATION_MEMBER_MISSING]`；gain/attenuation unavailable |
| ON | **NOT_RUN** |
| post | `all_checks_passed=true`；TX/RX idle；current=bedf98c7；boot 不变 |
| 原始 SHA | 7/7 下载一致；远端清理 |

源码原因（A 核查）：OFF 走普通 `rx.capture`，daemon `op_rx_capture` 使用非 observed 的 `rf_proto_write_iq_frame`；只有 `op_loopback_physical_capture` 写 `capture_observation`。因此当前 daemon 的 OFF 路径不产生 in-window observation，与“首 OFF 观测必需”门不兼容；不是匹配滤波/算法缺陷。**无新 RRC BER、gain index、chip attenuation、TX/RX 功率或裕量数据。**

后续二选一（需新的物理授权，未执行）：
1. 把观测门改为“OFF 允许无观测；首个物理 ON 必须 usable”；OFF 仅作安全/残留检查；或
2. 新候选/安装给普通 `rx.capture` 增加 observation（超出本轮）。
在未取得新授权前不重跑。

## 3 B：QAM16/RECT 产品接入

- `rf_qam16_pipeline.py` 显式冻结 QAM16/RECT/QI/60M/3M/4096；worker/UI 能力 version+sha、提交、同帧 landing、具名失败、配置/模式切换清旧 BER、stop/cancel、EVM unavailable。
- 合同 **`cnn-bpsk-modrec4-qpsk-rrc-qam16-v1`**，**62 software**；旧 55/58/60 为严格子集；bit/HWH/daemon/model/modrec4 字节与父 `bedf98c7` 相同。
- 唯一候选 **`cd76818afc3bee7c610678574e892b26`**；`candidate.tgz` SHA **`181bf027a321da6d4ceadd664a7ea24fdaa1660918a72a908deb35e8fb2a4816`**；父/回退 `bedf98c77283002c632dd7f345abaff9`；`verify_release` + `verify_panel_imports` PASS。
- 集成测试 B：**129 passed**；Lead 复跑 QAM receiver/pipeline/product **105 passed**。
- ARM：唯一临时目录；上传 tgz 实际 SHA 核对；解包 **62/62 software SHA 匹配**；同输入 `arm_input.iq` SHA `1e12d23a…`；host/ARM **0/256、valid127、align252、start63、同步 true、CFO12000、timing7**；测量字段空 diff、bit_preview exact、symbol max abs diff 8.88e-16；仅 capture_id 本地标签不同。
- ARM 性能 median 655.36ms、min634.52/max719.81ms；host 129.52/185.82ms 分列，不冒充 ARM。ARM 无 worker/UI，未宣称 ARM cancel；取消/停止由 13 项离线集成测试覆盖。
- 临时目录清理；post readback `VERIFIED_SAFE_EXPECTED`，current/active 仍 bedf98c7，boot 不变，服务 NRestarts0，TX/RX idle。未安装/未切服务/未 Overlay/未 rf.init。

## 4 下一工作包（QAM 试部署/验收/回滚；未执行）

1. 冻结本次候选 `cd76818a…`/tgz `181bf027…`；父/回退 `bedf98c7…`；先做一次 Codex 关键阶段审查，任何改动后重签候选。
2. 构建 QAM trial bundle（从 candidate release + 安装器 + trial unit 模板 + QAM 验收 fixture），核对 62 members、实际 candidate.tgz、trial-stage SHA；**不得**把 QAM 候选热替换当前 release。
3. 新鲜父 pre 读回：current/active=bedf98c7、boot、服务/NRestarts、storage、TX/RX idle、无残留作业；异常停止。
4. 注册唯一 QAM trial unit（root:root 0644、daemon-reload、ExecStart 与模板一致、无 alias），preflight 后启动一次；验收含 QAM16/RECT clean、错误参考、域外、能力/EVM、UI站同帧/旧结果清理；120s 有界、无 RF。
5. 成功：TRIAL_ACCEPTED、current=cd76818a、双服务正常、trial-acceptance 全 passed、独立 post `VERIFIED_SAFE_EXPECTED`、boot 不变、TX/RX idle。
6. 失败：既有事务恢复父 bedf98c7；独立 `--rollback-state` + 父 release 副本读回 `VERIFIED_SAFE_ROLLBACK`；UNKNOWN/RECOVERY 只观察原 unit，不重装/重试。
7. 本轮不安装；新试部署需用户另行授权，且需新的接线/窗口（若涉及 RF，本轮无 RF）。

## 5 风险/边界

- RRC rootcause 未完成：观测门与当前 daemon OFF 路径不兼容；无 ON 数据，不能据此更新根因。
- QAM 为离线/ARM candidate；未安装、未 worker/UI 实屏、未真板 RF、未 QAM64/RRC/EVM/训练。
- 候选/产品源码已改但当前安装 release 未变；不得把 candidate 验证写成已部署。

## 6 Codex 关键阶段审查尝试

- 已按“一次关键阶段审查”调用本地 Codex（`codex review`/`codex exec`，只读提示），但 provider 连接两次进入重连/无输出，未取得 verdict；日志 `evidence/review-qam16-codex-2026-10-24.log`。
- 因此 QAM candidate **没有 Codex APPROVED**，也不得伪造；Lead 仅给出增量审查并标记“待 Codex”。
- 下一次可重试时只提交一次同一候选/材料，不重开多轮审查。

## 7 RRC rootcause 下一最小门（未执行）

当前 daemon 的普通 `rx.capture` 不写 `capture_observation`，因此“首 OFF 必须 usable”不可满足。下一轮（需**新的物理授权**，本轮已消耗一次）最小修正为：
- 改 rootcause 门：OFF 只校验安全/无残留；**首个物理 ON 必须 observation usable**；后续 ON 同样 stop 语义；
- 或经新候选/安装让普通 `rx.capture` 写 observation（不在本轮范围）。
- 不改采集次数/控制；不按 BER 选 timing/seed；下一实验仍 6/6/6/4/6/0，先只读预检，再唯一派发。
