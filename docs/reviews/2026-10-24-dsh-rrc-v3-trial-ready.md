# RRC v3：部署前审查后证据修复核对

日期：2026-10-24。范围仅为既有 Codex 审查 `fb49f098…` 之后的 F1/F2 证据修复与实际部署包对应关系，不重复审查未改变的产品实现。

## 结论

**APPROVED_FOR_LIMITED_NO_RF_TRIAL**：此前审查所列 F1/F2 的修复已核对，最终候选具备进入用户授权的一次无 RF 开发版试部署的条件。本次没有安装或连接板卡。

- 候选：`bedf98c77283002c632dd7f345abaff9`。
- 父版本/回退：`6d081602b072f63c85ebcff83aaa63e8`。
- contract：`cnn-bpsk-modrec4-qpsk-rrc-v1`。
- 旧慢版本 `90ac6b12…` 不作为本次安装目标。

## 已核对

证据根：`evidence/rf-teaching/s2-46db-p3-rrc-candidate-2026-10-24/`。

1. 根 SHA 清单 475 项及 `final-arm-v2` 子清单 30 项全部相符（两份清单包含重叠文件，不相加称唯一文件数）。
2. F1：本地四份原始输出 arm-result/rss/stop-cold/stop-hot 的实际 SHA 与 `download-sha.json` 的 board/local 摘要一致。最终 arm-result 为 `c8f0650bcb177d66a78c75c3bc5447ff7391e39c9cb055ac5c778db111a21cc3`。旧被修改的结果不作为最终原始证据；本次未改任何 raw 输出。
3. F2：根目录 arm-result/rss/host-expected/compare/performance 与 `final-arm-v2` 对应文件逐字节相同；根 source-sha 明确绑定 bedf 候选、v3 receiver 与 final-arm-v2。旧慢候选根证据已归档。
4. stage 清单 86 个成员：bundle loose 文件和 trial-stage.tgz 内成员分别计算 SHA，全部相符。
5. 实际安装包中的 70 个 release 文件与 `candidate/release/` 逐字节相同；manifest 为 `573845cc26280b04a7f17bc4ead532073ddf00ba74fbd9f8bd331e4b47e040e2`、release id 为 bedf、software 成员为 60，与最终 ARM 身份一致。

## 两种 candidate.tgz 必须区分

| 路径（相对证据根） | SHA256 | 用途 |
|---|---|---|
| `candidate/candidate.tgz` | `3455aebcd10d0bbed2f1bf54ef40a8558b2d3a8e839b8d7cb403117614dd8e48` | ARM 身份记录引用的归档 |
| `candidate/bundle/candidate.tgz` | `f17b53d8b0f8e16fbeb04f6714f941d4fcc3e043b703eb41bab78502ce4f95ac` | 事务安装器实际消费的归档 |
| `candidate/bundle/trial-stage.tgz` | `d7f06aa8451ffc5993a291276d4dfdc498cbab80e12dbb0ecfd365c5c8e78b96` | 本次应上传解包的完整 stage |
| `candidate/bundle/rollback-parent.tgz` | `6c98f49f5c8a6e96d9d3b8e48f6f5c785398e823add1e16503d8e47523839e32` | 父版本回退归档 |

归档摘要不同不代表 release 不同；已核对实际 bundle release 与 ARM release 一致。unit ExecStart、TRIAL-STAGE.archive_sha256、bundle receipt 均使用安装归档 `f17b53d8…`。不得用 ARM 归档替换 stage 内 candidate.tgz，也不得将安装器期望摘要改成 `3455aebc…`。

## 复用的最终实测

`final-arm-v2`：ARM 产品入口 21/21，实际输入 SHA 一致，v1/v3 功能差分 21/21 保持，RECT-as-RRC 仍 11/112。冷首帧 1.5599 s、热合法输入中位 0.5377 s、最大合法输入 1.5599 s；RSS 峰值 89016 KiB。冷 stop 0.4652 s，热 stop 0.4428/0.4127/0.4398 s，线程结束、无 stale、close_confirmed=true。上述是已有原始板端记录，本次未重跑 ARM。

缓存最坏内存占用仍为非阻塞后续项；此次短时数据不证明长时间多工作点的最坏 RSS。不为此重开全量资格或安装离线慢版本。

## 下一步执行范围

用户可一次授权安装 bedf、现有 120 s 无 RF 验收、失败时恢复 6d081602 及独立读回。取得新鲜父版本 pre 后，核对全部 stage 成员，注册 `rf-modrec4-trial-bedf98c7.service`（不用 alias），启动一次。成功需 TRIAL_ACCEPTED、modrec4/RRC 验收通过、新版本 current/active、独立 post 的 VERIFIED_SAFE_EXPECTED、boot 未变及 RF 空闲；失败按已有事务回退并独立确认 VERIFIED_SAFE_ROLLBACK。断线/未知只观察原作业，不重复安装。

先下载并校验原始状态、验收及日志再清理。无 RF 试部署无需接线确认，不包含新采集、真实 RRC/物理 BER 资格或实体屏真人验收。本文件不代替用户实际安装授权。
