# 试部署与可控失败回退：真板阶段完成

核验时间：2026-10-17 15:45–15:48（Asia/Shanghai）。工作区 `<LOCAL_PATH>`；接续起点 HEAD `9d8e226`。

**结论：本阶段已完成。** 修复候选 `e66197ed79131b73aa4f47804487c053` 已真实安装并通过不发射验收；验收后主动注入预定退出码 42，现有回滚机制恢复 `aba80e92aec4ed3d4e83196733a800ae`，新独立会话确认旧发布、服务、RF 安全态和存储读回正常。此次没有真实 RF 发射、采集或研究派发。最终停在演练回滚后的旧版本，未继续第二次正常安装。

原始收据：[receipt.json](../../evidence/rf-teaching/codex-trial-closeout-2026-10-17/receipt.json)；原始证据目录：[codex-trial-closeout-2026-10-17](../../evidence/rf-teaching/codex-trial-closeout-2026-10-17/)。

## 1. 接续时真正的阻塞与处理

DSH 的上一份提交停在离线 prepare：缺新候选 dev/G1 收据，最小 ARM 回执不满足旧打包器。后续未提交产物已补齐完整 ARM 资格和安装包，本轮直接复用并复算，未重新做诊断审查或重复全套测试。

| 输入 | 本轮核对 |
|---|---|
| 修复独立审查 | `s2-headroom-src-cfg6-diagnosis-2026-10-17/review/REVIEW.md` 已 APPROVED；candidate2 的绑定迁移报告已 APPROVED |
| 新候选 ARM 资格 | `s2-headroom-candidate2-qual-2026-10-17/arm-runtime/receipt.json`：passed、发布 e66197ed、P95 271.1826 ms、RSS 31732 KiB、native CNN、两种 parity 均为空、相关退出码均为 0 |
| 安装包 | `rf_headroom_deploy.verify_bundle` 通过；99 成员，父发布 aba80e92；candidate.tgz SHA-256 `28b8b8f14fa0c9f61f3afc78d76c3fc770105f0aefc80f9ee7271edf7d5e05d7` |
| 实际修复模块 | 包中两份 `rf_headroom.py` 均为 `fa8f92c5b85f6077172f0556dec42b2abe21dd2e731da88ae550846c67c62355` |

**如实保留封存索引问题**：复算旧 candidate2 development checkpoint 时，`logs/seal-development.log` 和 `work/seal_development.py` 与其记录摘要不同；stage checkpoint 中后者不同。因此本轮不宣称这两份旧全表封存验证成功，也不修改旧表以掩盖差异。差异位于阶段输出日志和封存辅助脚本；实际部署输入的工具校验、候选内容、ARM 收据与独立审查绑定成立。按现行开发政策保留此限制，未把文档/封存辅助件变化升级为已验证候选不能试部署。

首次在沙箱内连接返回 Windows 10013（套接字权限限制），未接触板卡。改用许可范围内的网络执行后新鲜预检通过；没有把本机权限错误当作板卡故障或重派一次安装。

## 2. 实际执行：一次派发、一次安装

trial id：`codex-14992ffdd4534c9187255ffc911347a9`。

unit：`rf-trial-codex-14992ffdd4534c9187255ffc911347a9.service`。

板端持久暂存：`/var/lib/rf-deployment-trials/codex-14992ffdd4534c9187255ffc911347a9/stage/`；安装器兼容链接：`/tmp/rf-teaching-install-trial-codex-14992ffdd4534c9187255ffc911347a9`。

1. 读取当前发布和安全态，确认没有活跃 trial/install 作业，新候选发布目录尚不存在。
2. 复用已有基础包、已审查 trial 入口和验收脚本，逐件上传并在持久目录复算 8 个输入文件摘要。
3. 派发前再次核对父版本和同一 boot_id；先写一次性本地派发意图，再只提交一次 systemd 作业。显式设置 PYNQ/XRT 环境，使用 flock 防止本入口并行写板。
4. 安装新候选，真实 ARM 执行验收脚本。成功后 `drill_acceptance.py` 才输出 `DRILL_INJECTED_AFTER_PASS exit=42` 并返回 42。
5. trial 的 EXIT 处理调用已有回滚函数，恢复旧发布指针与旧 systemd unit；两次板端状态检查确认旧版本安全。
6. 另开独立只读 SSH 会话，读取当前发布/服务/RF/存储/boot_id，并下载验收和回退状态文件；逐件比对远端与下载摘要后生成最终收据。

没有重试、重派或第二次安装。未删除或覆盖 DSH 的资格、试部署准备产物和原始失败记录。

## 3. 板端验收结果

运行平台：armv7l，Python 3.8.2，Linux `5.4.0-xilinx-v2020.2`。

| 检查 | 真实结果 |
|---|---|
| 安装发布/manifest/46 项软件身份 | PASS，e66197ed |
| 导入来源与修复模块摘要 | PASS，实际从新发布导入 |
| 历史真实失败帧，同一 IQ 重放 | PASS；after=(src_cfg 6, cap_cfg 0) 被修复谓词接受，旧规则仍拒绝同一字 |
| 合法残值与禁止控制位反例 | PASS，src_cfg {0,2,4,6}、cap_cfg {0,4} 与指定禁止位均按声明判定 |
| 可控失败注入 | 验收 PASS 后才执行，退出码恰为 42 |
| 回滚 | 日志包含 `TRIAL_ROLLED_BACK parent=aba80e92...`；`trial-rollback-state.json` 证明旧发布安全 |

systemd 的 `Result=exit-code / ExecMainStatus=42 / ActiveState=failed` 是**预期注入**，不能单独视为部署意外故障；也不能单凭这个退出码认为演练通过。通过依据是注入前四项验收全真、完整回滚日志、旧版本状态文件与独立最终读回同时成立。

## 4. 最终独立读回

| 项 | 最终值 |
|---|---|
| current / active-release | aba80e92aec4ed3d4e83196733a800ae，彼此一致 |
| pynq-status | active/running，PID 21663，NRestarts 0 |
| rf-daemon | active/running，PID 21676，NRestarts 0 |
| armed / TX source / RX armed | false / false / false |
| PL io_ok / safe | true / true |
| boot_id | `8280be4d-f5bb-4390-8d96-337b55275cae`，派发前后相同 |
| 完整存储预检/收尾扫描 | 无受审分类器识别的存储故障，根与启动分区仍 rw |
| 新 RF 帧 / 研究派发 | 0 / 0 |

## 5. 本阶段完成边界与接续入口

已完成“单次审查 → 真板试部署 → 新候选非发射验收 → 可控失败 → 自动回滚 → 独立读回”。机制在这个具体范围已 BOARD_VERIFIED，无需再做同范围离线审查。

未完成、也未声称完成：真实 46 dB 研究通过、S2 正式 108+12、真人、S3/S4，以及断电/存储故障回退保证。

下一次要保留修复版本时，应使用明确的已有发布重新激活流程或新的可安装发布身份。此次演练已在 `releases/e66197ed...` 留下候选目录，原安装器不允许覆盖同名目录，不能直接再跑同一条新装命令。本轮按演练约定停在旧版本安全态，不擅自删除发布目录或追加 RF 研究。
