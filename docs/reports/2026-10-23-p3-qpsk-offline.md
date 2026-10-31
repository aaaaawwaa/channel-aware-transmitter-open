# 2026-10-23 P3 离线进度：统一接收结果契约 + QPSK/RECT

状态：**CODED / OFFLINE_VERIFIED**（仅主机离线；无板卡/SDR、无初始化/发射/采集/安装、无 commit）。
证据入口：`evidence/rf-teaching/p3-qpsk-offline-2026-10-23/INTEGRATION-REPORT.md`、`integration.json`、`pytest.log`、`replay/`、`SHA256SUMS.json`。

## 本轮有证据的推进

- 新增 `pynq_overlay/sw/rf_modem_result.py`：统一结果身份、mode/采样域、三种同步、具名不可用原因、有界
  symbol/bit preview、BER 分子/分母；EVM 明确 `False/None`，不填 0；`from_bpsk_result()` 保持旧 BPSK 结果兼容。
- 新增 `pynq_overlay/sw/rf_qpsk_receiver.py`：固定 QPSK/RECT 4096、60 MSPS、3 MSym/s、ratio0；显式
  `iq_order='IQ'|'QI'`；参考模式 127 符号/2540 samples，前 63 符号（126 bits）对齐，最终 BER 固定为后
  64 符号/128 bits；无参考/失步/低能量/削顶/域外均给具名原因，不伪造 BER。
- 独立参考 `pynq_overlay/sw/tests/qpsk_reference.py` 不 import 被测接收机；真实回放入口
  `scripts/p3_qpsk_replay.py`。

## 测试（本机复跑）

| 套件 | 结果 |
|---|---|
| 契约 | 7 passed |
| QPSK 接收机 | 77 passed |
| 独立参考/fixtures | 43 passed |
| 旧 BPSK 接收机 | 178 passed |
| 旧 BPSK 星座 | 14 passed |

真实 5 帧（既有开发帧，不重新采集）：task85 `05-qpsk-39`、task71 `s01-qpsk-1/2/3`、task80
`03-qpsk-38`，均为 **QI 显式次序、sync=true、BER 0/128**；独立参考一致。唯一字段差异为 task85 的
500 Hz 参考 CFO 与接收机 0 Hz 粗估，不影响同步/BER。

## 明确未执行 / 不声明

ARM、板端 worker/UI 接入、物理 BER 资格、EVM、RRC、QAM16/64 接收机、自动选档、新的 RF 采集均未执行；
5 帧仅开发回放，不冒充独立 TEST 集；单帧零错不等于低 BER 总体能力。

## 下一步最小方案（不自动部署）

1. ARM 离线同 IQ 回放 5 帧（临时目录，不 Overlay/不 rf.init/不采集/不发射），记录结果与 p50/p95/RSS。
2. 产品接入只在新的独立审查候选阶段：真实 capture 边界显式传 `iq_order='QI'` 与参考 seed，输出
   `ModemResult.to_dict()`；不改变旧 BPSK/六类/四类路径。
3. 物理 BER 需另立接线确认、一次性授权和预声明 TEST；本轮不申请 RF。

## 2026-10-23 追加：产品接入 / 候选 / ARM（有证据）

状态：**CODED / OFFLINE_VERIFIED / ARM_VERIFIED**；未安装、未切 current、无 RF、未 commit。

- 产品接入：`rf_qpsk_pipeline.py` 新增；`rf_status_worker.py`/`ui_model.py`/`ui_view.py` 增加 QPSK 配置入口、
  `ModemResult` 显示/失效处理；真实 capture 边界显式 `iq_order='QI'`；BPSK/六类/四类/环境路径保持。
- 相关产品/合同回归 **Lead 311 passed**（A 修复后相关集合 106 passed）；QPSK receiver 77、契约 7、
  独立参考 43、旧 BPSK receiver 178、旧 constellation 14。
- 最终候选：**`6d081602b072f63c85ebcff83aaa63e8`**，contract **`cnn-bpsk-modrec4-qpsk-v1`**，
  parent/rollback **`da97347ab29d0b31513460e40bcce99f`**；旧 modrec4 合同 55 成员保持不变。
  仅新增/变更 QPSK 产品模块、`install.sh`、`merged_release.py` software map；bit/HWH/daemon/六类/四类模型未变。
  candidate.tgz `b74c85c7…`、rollback-parent.tgz `e7bec531…`、trial-stage.tgz `c08ff72f…`。
- 上一候选 `be308d11` 的 Codex 审查指出：install.sh 漏拷 QPSK 模块、既有 contract 改动导致父 A 无法 canonical
  验证。已修复为 additive contract + install copy；旧候选归档到 `superseded-be308d11/`。
- ARM 同 IQ：最终冻结候选产品入口 **12/12**（5 真实 QI + 7 负例）通过，5 真实 sync/BER 0/128；
  Python 3.8.2/numpy 1.17.4，12 输入总 7.686 s，max RSS 22,820 KiB；current 仍 A、无安装/无 RF。
- 试部署：bundle 内加入 QPSK 离线验收；安装前 live pre 读回、unit 注册→preflight→dispatch→120s
  验收→独立 post 读回/回退方案见
  `evidence/rf-teaching/s2-46db-p3-qpsk-candidate-2026-10-23/CANDIDATE-REPORT-FINAL.md`。
- 最终 Codex 审查（run `dd83ef42…`）：**可进入用户授权的一次安装试部署**；F1/F2/P0/P1/P2 已闭合，
  未发现安装/读回阻塞。剩余 P3 为非阻塞：预检建议手册化核对 qpsk 帧/stage 摘要、ARM 报告负例计数文案、
  测试日志入证据；均不影响已审查的安装候选。未安装。
- 未声明：BOARD_VERIFIED、物理 BER、UI 实屏、真实 RF；5 帧开发数据不构成低 BER/总体准确率资格。

## 2026-10-24 无 RF 试部署（有证据）

状态：**TRIAL_ACCEPTED**（无 RF 开发版试部署）；不是 BOARD_VERIFIED，未做实时 QPSK 采集/实体屏/物理 BER。

- 最终候选 6d081602… 已安装并保留；current/active=候选，contract cnn-bpsk-modrec4-qpsk-v1，父/回退 A da97347a…。
- boot_id c7e8221-6cef-458d-8c82-3a7723fd3236（pre/post 相同）；双服务 active/running NRestarts=0；无新增存储错误。
- 父 A pre readback VERIFIED_SAFE_EXPECTED；trial-stage 84/84 成员 SHA 匹配；qpsk-39/nf-00-normal 帧 SHA 正确；unit root:root 0644、单次启动。
- 120s acceptance：modrec4.passed=true、qpsk.passed=true（sync、valid_symbols=127、BER 0/128、EVM unavailable）；独立 post readback VERIFIED_SAFE_EXPECTED、failures=[]、TX/RX 空闲；未回滚、未重装。
- 首次 post 仅 B10 临时 tar 残留导致失败，删除后 post-final2 全 PASS；证据先下载校验后清理。
- 证据：`evidence/rf-teaching/s2-46db-p3-qpsk-trial-deploy-2026-10-24/`。

## 2026-10-24 QPSK 真实解调冒烟（有证据）

状态：**6 帧真实解调冒烟完成**；板端仍为最终候选 `6d081602…`。不宣称总体准确率/低 BER 资格。

- 接线确认 2026-10-24T09:18:27.942+08；实际 6 sessions/6 init/6 load/4 ON TX/6 captures，0 重试/0 补采；
  工作点 80MHz/46dB/amp60/60MSPS/3Msym/RECT/4096。
- QPSK seed39/40/41 实际 worker 均 carrier/timing/reference sync=true、reason=""、valid_symbols=127、
  BER 0/128、QI；同 IQ 冻结候选 product 侧车 3/3 MATCH。BPSK seed6 单列 sync=true、valid_symbols=201、
  BER 0/138。
- OFF-first/OFF-last 无 QPSK sync/BER，pre/post TX source=false；分类 UNKNOWN/rejected，OFF 误接受 0。
- 独立 post 读回 `VERIFIED_SAFE_EXPECTED`、passed=true、failures=[]；current/active=6d081602，
  boot ac7e8221、双服务 NRestarts=0、TX/RX 空闲。
- 证据：`evidence/rf-teaching/s2-46db-qpsk-real-rx-2026-10-24/`（REAL-RUN-REPORT、summary、runner/run、
  download、readback/post、SHA256SUMS）。真实 worker 为权威，回放不覆盖失败。

## 2026-10-24 RRC 离线接收（有证据）

状态：**CODED / OFFLINE_VERIFIED**（仅 BPSK/QPSK 合成/解析；未 ARM/板卡/真实 IQ/worker/UI/安装）。

- 新 `rf_rrc_receiver.py` 与 `scripts/rf_rrc_offline.py`；首域 60 MSPS/3 MSym/s/sps20/α0.35/4096，
  BPSK/QPSK；实际 TX 161 taps、transition=4、valid symbols 4..122（119）、
  BER 区 67..122（BPSK 56/QPSK 112 bits）；显式 IQ/QI；EVM unavailable。
- A 单测 **64 passed** + B 独立参考 **22 passed** = Lead 合并 **86 passed**；RECT 兼容 **100 passed**；
  独立 fixtures 21 个；clean 4/4 BER0；负例保留（含 RECT 波形按 RRC 配置的 11/112 errors，不算 clean）。
- 未执行：ARM/主机同 IQ 判等、真实 RRC 采集、worker/UI、物理 BER、QAM/EVM/自动选档。
- 证据：`evidence/rf-teaching/p3-rrc-offline-2026-10-24/RRC-OFFLINE-FINAL.md`。

## 2026-10-24 RRC 优化产品候选（有证据）

状态：**CODED / OFFLINE_VERIFIED / ARM_VERIFIED（功能+交互性能）**；**未安装、无 RF、未 commit**。

- 最终优化候选 **`bedf98c77283002c632dd7f345abaff9`**，parent/rollback **`6d081602…`**，
  contract `cnn-bpsk-modrec4-qpsk-rrc-v1`（60；旧 55/58 不变）；candidate.tgz `3455aebc…`，
  trial-stage `d7f06aa8…`、stage-sha `ef0573b6…`；旧慢候选 90ac6b 归档 `superseded-90ac6b/`。
- RRC receiver 两轮优化到 v3 `beeaca12…`：缓存/复数 FFT 后按 ARM 实测 FFT 主导改为符号级直接卷积；
  host/ARM 21/21 等价，版本单列，RECT_as_RRC 11/112 保持；产品回归 **289 passed**。
- 最终 ARM：cold **1.5599 s**、hot 中位 **0.5377 s**、max legal **1.5599 s**、总 11.110 s、RSS 89,016 KiB；
  21/21 host/ARM 一致，实际 IQ bytes SHA 21/21；stop cold 1/1 + hot 3/3 均 ≤5.75 s、无 stale。
- Codex 增量审查指出下载后 ARM 文件被改写、根目录混放旧候选；已重跑 final-arm-v2 原始下载
  （板端/本地 sha 逐字节一致）并把旧证据移入 `superseded-90ac6b/root-old/`。
- **2026-10-24 无 RF 试部署：TRIAL_ACCEPTED**；current/active=`bedf98c7…`，boot `ac7e8221…` 不变，
  双服务 active/NRestarts=0；trial-acceptance modrec4+RRC 均 passed（RRC sync、119、0/112、EVM unavailable）；
  独立 post 读回 `VERIFIED_SAFE_EXPECTED`（清理前后各一次），TX/RX idle，无 rollback。
- 证据：`evidence/rf-teaching/s2-46db-p3-rrc-candidate-2026-10-24/CANDIDATE-REPORT-FINAL.md`、
  `evidence/rf-teaching/s2-46db-p3-rrc-trial-deploy-2026-10-24/TRIAL-DEPLOY-REPORT.md`。
