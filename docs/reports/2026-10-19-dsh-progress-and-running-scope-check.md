# DSH 进度与当前执行范围核验

检查时间：2026-10-19 08:48（Asia/Shanghai）。只读核对本机 DSH 实时会话、工程产物和 DSH 刚取回的板端记录；本检查没有另连板卡，没有修改 DSH 产品源码、终止任务或发送消息。

## 结论

当前运行方向符合要求：一条只读板卡状态检查，一条使用已有 IQ 的离线首帧诊断，没有看到追加安装、初始化或物理采集。当前是 **S2 开发 pilot 失败后的离线定位阶段**，尚未满足冻结/正式 TEST 条件。

主会话最新操作者请求为 08:40:02 的“我昨天因为结束工作将板卡软关机，检查板卡状态，同步进行派发”。主会话 08:40:51 派发两项：

- `0535526c-225a-4dea-8a84-b83d4fd19ce9`：Read-only board status check，板卡唯一执行者，已生成本次读回。
- `7f4bcb18-b1bd-4971-be8b-d71efeab468f`：Diagnose first-frame amplitude，零板卡，正在重算特征与候选字节下的产品重放。

## 实际推进

| 阶段 | 实际状态 |
|---|---|
| 33 帧 TRAIN/VAL、TRAIN-only 拟合 | 已完成；VAL 10/10、3 OFF 零误判 |
| 新 n=20 基线候选 | `03b9b883a61f29605be2c1f3817024d0` |
| 新候选部署 | 昨晚约 22:10:52 TRIAL_ACCEPTED；新版本保留 |
| 18 帧开发 pilot | 1 次派发、3 次初始化、18/18 帧；known_correct=8，要求9，passed=false |
| 负控与收尾 | negative_false_clear=0、board_state=VERIFIED_SAFE |
| 同 IQ 重放 | 通过且与板端逐帧标签一致；没有把重放通过改写成 pilot 通过 |
| 当前阻碍 | `s00-normal-00` 为 UNKNOWN / AMPLITUDE_OUTSIDE_RULE_SUPPORT；RMS268.917，对照其它两个 normal 首帧630.695/458.152 |
| 正式 TEST / 真人验收 | 未运行 |

物理低幅度成因（AGC、建立期、链路或其它）仍未证实。诊断任务名“first-frame amplitude”只是调查方向，不能仅凭 RMS 较低定因。最新产品重放再次得到相同 UNKNOWN 理由，bpsk_reliable=true；应继续结合触发的归一化幅度/crest 规则与时序证据定位，不预设结论。

## 最新板端记录

来源 `evidence/rf-teaching/s2-46db-board-status-2026-10-19/logs/state.json`；命令窗口约 08:46:25–08:47:07。

- 新 boot_id `0885f219-e673-4afa-b213-7982e676466f`，与昨日不同，符合用户关机后的重新启动事实。
- current/active/verified 均为 `03b9b883…`，51 个成员与本机候选逐文件一致。
- 新基线 file=`351c52c5…`、canonical=`dd794f0c…`，板端独立重算一致，20 个 TRAIN 来源、development。
- 两服务 active/running、NRestarts=0；RF armed/core/RX armed/TX source selected 均 false，PL I/O正常。
- 无新的精确 MMC/SDHCI/存储故障，根与 /boot 均 rw；FAT 非正常卸载警告仍在，不能宣称存储根因已修复。
- 锁空闲，无在飞任务及 RF 临时目录；原部署暂存摘要未变化，D6 回退父仍在。

**新探针存在汇总缺陷**：`B16_boot_journal` 仅存日志，没有 `ok` 字段，结尾却统一按 `item.get('ok') is not True` 判失败，故得到唯一失败 B16 和 passed=false。其它15项都有 ok=true。这个总结果不能被解释成板卡硬件失败，也不应粗暴改成所有 journal error 都通过。

可直接使用已取回字节离线修正汇总：将启动日志单列为 observation，保留原始日志及结果；如要设门，必须声明明确的相关故障判据。无需为这个格式问题再次连接、重装或重启板卡。

## 是否符合执行要求

### 当前符合

1. 两条任务互不争用板卡：只读检查与纯离线诊断并行。
2. 没有自动补采、修改基线阈值、重新拟合或把8/9改成通过。
3. 没有启动无关全仓审查/构建。已有真实失败帧重放及特征重算与当前问题相关。
4. 本次状态检查无RF动作；即使旧接线确认到期，也不影响离线诊断和只读观察。新的RF派发必须另有明确预算。

### 需要纠正或防止重复

1. 修正上述 B16 汇总误报，用已有输出完成即可。
2. 首帧问题要先给“已证明/未证明”的最小结论；不能因8/9未通过重新定义 known 集合、放宽9/9门或只重采失败帧。
3. 昨晚部署记录承认验收重钉当时只有实现者自审（独立审查调用受限），后来 22:39 独立复审 APPROVED 补齐了同字节审查。应如实保留先部署后补审的过程偏差，不宣称部署前已获独立批准。现在不要再审第三遍。
4. 昨晚部署后的只读读回与pilot重叠，未发生安装写事务与RF控制重叠；两链已追加边界说明。今后应等部署独立读回完成再交接给唯一RF执行者，不重做历史数据来追求“独占”措辞。

当前可以继续这两项任务并汇总，无需停住DSH。完成诊断后再决定最小修复与新一轮pilot工作包；目前没有依据新增部署或额外采集。

## 证据入口

- `evidence/rf-teaching/s2-46db-baseline-deploy-2026-10-18/NOTES.md`
- `evidence/rf-teaching/s2-46db-baseline-pilot-2026-10-18/report/verdict.json`
- `evidence/rf-teaching/s2-46db-baseline-pilot-2026-10-18/NOTES.md`
- `evidence/rf-teaching/s2-46db-baseline-repin-review-2026-10-18/REVIEW.md`
- `evidence/rf-teaching/s2-46db-board-status-2026-10-19/logs/state.json`
- `evidence/rf-teaching/s2-46db-board-status-2026-10-19/work/board_status_probe.py`
- `evidence/rf-teaching/s2-46db-first-frame-amplitude-2026-10-19/logs/product-chain-s00-normal-00.json`
- 本次DSH本地压缩会话读取脚本：`evidence/rf-teaching/codex-progress-check-2026-10-19/read_dsh.py`（仅输出用户/助手普通消息与工具描述，不输出模型推理流）。
