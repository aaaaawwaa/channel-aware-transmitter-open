# Lead 一次增量核对（real_campaign）

日期：2026-10-21；范围：`ml_training/real_campaign/` 真实四类数据验收工具。仅此一次核对，
未新增审查代理、未重训、未扩展网络、未重跑 ARM、未采集/发射/连接设备。

## 核对结论

| 项 | 结果 |
|---|---|
| 一键命令 | `python ml_training/real_campaign/run.py` → **PASS**；生成 index/report |
| 导入不可变性 | 原始 IQ/元数据只读；只写 `ml_training/real_campaign/`；未改产品/证据原件 |
| 真实数据 | 71 帧；真实会话 8；clean 42 / quality 4 / stress 3；域检查 71/71 OK |
| 去重 | 指标按 IQ SHA-256 去重，排除 22 帧重复副本；真实重复组 22；**真实跨 split 泄漏 0** |
| 干净分类 | BPSK 42/42 正确，已知类误拒 0/42；**QPSK/QAM16/QAM64 真实类 NOT_RUN**，四类总体准确率 NOT_RUN |
| 质量拒识 | OFF 4/4 拒识，误接受 0，原因 `noise_like` |
| 干扰压力 | 窄带 3/3 接受；仅压力结果，不进入干净准确率 |
| 真值政策 | 只用显式信号配置/独立实验记录；模型/环境标签仅诊断，不作为真值；测试含直接拒绝夹具 |
| 合成夹具 | 4 合法四类 + 4 错误输入 + 3 重复/泄漏；`synthetic=true`；映射参考检查 22/22 PASS；夹具校验 11/11 PASS |
| 冻结哈希 | CNN `25a65fb1…`、基线 `caa82589…`、round-2 规则 `9aceebbd…`、arm_probe MANIFEST `007e40bd…` 未变 |
| 测试 | real_campaign **10 passed**；合并回归 **153 passed**（原有 143 + 新增 10） |
| 产品/设备 | 零产品修改、零部署、零设备连接、零新采集；route 与参数草案仅文档 |
| 文档 | README、collection_prep.md、next_batch_fields.md、报告与索引 |

## 边界

- 本工具不定义正式验收门限；干净 BPSK 通过只说明现有 BPSK 数据可演示，不构成四类产品资格。
- 真实 QPSK/QAM16/QAM64 缺失，报告和 JSON 均显式 `NOT_RUN`；合成夹具不冒充真实采集。
- 采集路线选择与合同禁止项见 `docs/collection_prep.md`；未执行任何路线。
- 后续变更应重新跑本核对，不做多轮审查。
