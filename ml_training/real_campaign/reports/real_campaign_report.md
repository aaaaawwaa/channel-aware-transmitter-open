# 真实四类数据验收工具报告（真实数据现状）
生成：2026-10-21T12:52:58Z
> 本报告由 `ml_training/real_campaign/run.py` 一键导入生成；原始 IQ 只读，不复制、不修改。合成夹具单独标记并单独评估，不计入真实指标。
## 1. 导入与可用数据
- 真实帧：71（invalid 0）；合成夹具帧：11。
- 真实会话：8（合成夹具会话：7）；clean 42 / quality 4 / stress 3。
- 评估去重：保留每个 IQ digest 的首个记录；因重复副本排除 22 帧。

### 真实会话明细
| source | session | split | frames | unique_clean | quality | stress | release | boot | status |
|---|---|---|---:|---:|---:|---:|---|---|---|
| campaign3-2026-10-18 | train-01 | TRAIN | 11 | 10 | 1 | 0 | 1a30f4b7b9aebe27965fcdd6dcf7634e | f4015100-cd95-4fc0-a384-1b33205fb9fe | EVALUATED |
| campaign4-2026-10-18 | train-01 | TRAIN | 11 | 10 | 1 | 0 | 1c0894bfe7751be2e1afa4e8bac564b8 | NOT_DECLARED | EVALUATED |
| campaign4-2026-10-18 | train-02 | TRAIN | 11 | 10 | 1 | 0 | 1c0894bfe7751be2e1afa4e8bac564b8 | NOT_DECLARED | EVALUATED |
| campaign4-2026-10-18 | val-01 | VAL | 1 | 1 | 0 | 0 | 1c0894bfe7751be2e1afa4e8bac564b8 | NOT_DECLARED | EVALUATED |
| campaign5-2026-10-18 | train-01 | TRAIN | 11 | 0 | 0 | 0 | 1c0894bfe7751be2e1afa4e8bac564b8 | NOT_DECLARED | DUPLICATE_ONLY |
| campaign5-2026-10-18 | train-02 | TRAIN | 11 | 0 | 0 | 0 | 1c0894bfe7751be2e1afa4e8bac564b8 | NOT_DECLARED | DUPLICATE_ONLY |
| campaign5-2026-10-18 | val-01 | VAL | 11 | 10 | 1 | 0 | 1c0894bfe7751be2e1afa4e8bac564b8 | NOT_DECLARED | EVALUATED |
| teaching-2026-10-21 | s2-46db-80mhz-narrowband-2026-10-21 | UNASSIGNED | 4 | 1 | 0 | 3 | 3f84f6a189e833b6986f4c819ff89be0 | NOT_DECLARED | EVALUATED |

重复采集会话（索引保留，指标按 digest 去重）：campaign4-2026-10-18/train-01, campaign4-2026-10-18/train-02, campaign5-2026-10-18/train-01, campaign5-2026-10-18/train-02。

## 2. 真值来源与身份检查
- 调制真值只来自显式信号配置或独立实验/场景记录；`model_result`/环境标签仅作非真值诊断记录。
- 被忽略的非真值标签中与真值不同的帧：26（例如旧六类模型把真实 BPSK 帧标为 QPSK，但没有被当作真值）。
- 身份：release 按帧记录；boot_id 仅在独立声明存在时记录，不补造；缺失明确标为 `NOT_DECLARED`。

## 3. 干净信号分类（仅真实可用类别）
- 可用真实类：BPSK；缺失真实类：**QPSK, QAM16, QAM64**。
- 四类总体准确率：**NOT_RUN**（missing real class data: QPSK, QAM16, QAM64）。
- BPSK：42/42 正确，准确率 **1.000**，拒识 0，错分 0。
- QPSK：**NOT_RUN**（无真实数据）。
- QAM16：**NOT_RUN**（无真实数据）。
- QAM64：**NOT_RUN**（无真实数据）。
- 已知类误拒：0/42（0.000）。
- DUPLICATE_ONLY 会话是重复下载副本，索引保留但不重复进入指标。

### 每会话结果
| source | session | clean帧 | 正确 | 拒识 | 四类总体 |
|---|---|---:|---:|---:|---|
| campaign3-2026-10-18 | train-01 | 10 | 10 | 0 | NOT_RUN |
| campaign4-2026-10-18 | train-01 | 10 | 10 | 0 | NOT_RUN |
| campaign4-2026-10-18 | train-02 | 10 | 10 | 0 | NOT_RUN |
| campaign4-2026-10-18 | val-01 | 1 | 1 | 0 | NOT_RUN |
| campaign5-2026-10-18 | val-01 | 10 | 10 | 0 | NOT_RUN |
| teaching-2026-10-21 | s2-46db-80mhz-narrowband-2026-10-21 | 1 | 1 | 0 | NOT_RUN |

## 4. 质量拒识与干扰压力（分开报告）
- 质量拒识：4/4 拒识，拒识率 **1.000**，误接受 0；原因 {'noise_like': 4}
- 干扰压力：3 帧（窄带），接受 3 / 拒识 0；**仅压力测试，不计入干净四类准确率**。

## 5. 重复 IQ 与 TRAIN/VAL/TEST 泄漏
- 重复 IQ 组：23（真实 22）；跨 split 泄漏组：1（真实 0）。
- 真实跨 split 泄漏：**PASS**；指标已按 IQ digest 去重，重复副本不重复计数。
- 重复组明细见 `index/duplicates.json`；原始文件保留不变。

## 6. 输入域与跳过
- 导入域检查：71/71 真实帧域 OK。
- 评估跳过：0。

## 7. 合成夹具（不是真实采集）
- `synthetic=true`；来源为冻结四类生成器 `sim-smoke`，映射参考检查 22/22 passed，夹具校验 11/11 PASS。
- 合成夹具重复 IQ 组：1；跨 split 泄漏组：1（用于直接测试检测逻辑）。
- 合成夹具评估单独输出：clean 4 帧，四类总体 PASS；不进入真实指标。

## 8. 下一批实测数据最小字段清单
详见 `docs/next_batch_fields.md`；核心字段：
```text
schema / split
session_id / session_index / seed
release_id / software_contract / active_release
boot_id (or explicitly NOT_DECLARED)
operator / captured_utc
route (board loopback or external SDR -> board RX)
lo_hz / sample_rate_hz / rx_bandwidth_hz / tx_bandwidth_hz
attenuation_mdB / gain settings / phy_mode_1r1t
format=s16_iq_le / samples=4096 / decimation=1
symbol_rate_hz=3000000 / shaping=RECT
modulation class / mode / mapping / PRBS7 seed / differential flags
tx source / tx amplitude / Pluto attenuation (for external SDR)
truth_source (signal config or independent experiment record) + record id
frame_id / capture_id / iq_sha256 / frame_count
scenario (normal/off/narrowband/...) / physical flag
spectral inversion or other RX path convention notes
clipping/rail flags / peak/RMS diagnostics
```

## 9. 结论与限制
- 已有真实 BPSK 数据可演示导入、去重、冻结模型评估和逐会话结果；本次真实 clean BPSK 42/42 正确，已知类误拒 0/42。
- **QPSK/QAM16/QAM64 真实类 NOT_RUN**，因此不计算/不宣称四类总体准确率；需下一批实测数据补齐。
- 本任务未采集、未发射、未连设备、未改产品；采集路线与参数草案见 `docs/collection_prep.md`，正式验收门限不在本工具设定。
