# REVIEW — RRC real-run control fix + 1/112 offline diagnosis

日期：2026-10-24。范围仅本轮实验控制逻辑与既有真实 IQ 诊断；复用已安装候选 `bedf98c7…`、既有 ARM/部署审查，不重开算法/候选/安装审查。零 RF、零安装、零训练、未 commit。

## 1 差分

| 文件 | SHA-256 | 变更 |
|---|---|---|
| `scripts/rf_rrc_real_validation.py` | `e95a4ecc363f29228ead330cb49225e73247e346f8bdd1f90b03ee4493495142` | 分离 execution/structural/metric；新增 `--plan {six,next-min}`；默认 six 不变 |
| `scripts/tests/test_rf_rrc_real_session.py` | `afcc2e0f066365230934b826afd0d37ab9d417dd87baca39d7f1f2873281af18` | 新增 1/112、合法失同步、结构/cleanup、next-min 测试 |

关键行为：
- `_structural_problem` 只保留身份/配置/缺产品结果/错帧/超时/cleanup/OFF残留/metric 容器完整性/分母/计数/BER 一致性。
- 新增 `_metric_problem`：非零 BER→`metric_negative_nonzero_ber`，合法失同步→`legal_desync_negative`，保留并继续；RRC valid/区间异常是 metric negative，不 structural。
- summary 明确 `execution_complete`、`structural_valid`、`metrics_passed`、`metric_failures`；`structural_complete` 仅 execution 别名，不是验收通过。
- 默认 six 帧仍 6 sessions/6 init/6 load/4 ON TX/6 capture；`next-min` 5 帧/5 init/5 load/3 ON TX/5 capture。

## 2 测试

- `scripts/tests/test_rf_rrc_real_session.py` **21 passed**；runner 相关（含旧 board/QPSK）**35 passed in 0.86 s**。
- 完整 fake 六帧链：clean 六帧 `execution_complete=true/metrics_passed=true`；1/112 六帧 `execution_complete=true/structural_valid=true/metrics_passed=false/stop_cause=None`；合法失同步继续；结构/cleanup/超时仍停。
- 日志：`evidence/rf-teaching/s2-46db-p3-rrc-real-rx-2026-10-24/runner-fix-tests.log`、`fake-six-chain.log`。

## 3 1/112 诊断结论（B，派生只读）

- 位置：TX payload symbol 101/bit 202；RX 比较 symbol 20（绝对 RX symbol 87）/比较 bit 40，I 轴。
- soft I/Q=+359.48/−1697.53；期望 −1/−1；I 判决裕量 359.48 raw（0.1240×中位峰值），Q 轴裕量 1697.53。
- CFO −1000 Hz、timing 1 sample、phase ambiguity 0、产品相位 42.888°、诊断残差 7.696°；valid 4..123、comparison 67..123、valid119、alignment126、reference offset14。
- TX payload SHA=truth `8717cb35…`；QI 正确；raw IQ min/max −431/716、max abs716，无 −2048/2047 削顶；产品重放仍 1/112，sidecar 1/112 MATCH。
- **CONFIRMED**：单轴 I 符号翻转、位置/裕量；payload/QI/区间/分母正确；无 clipping；产品重放 1/112。
- **REFUTED**：payload/seed/QI 错误、削顶导致。
- **UNKNOWN**：物理链路噪声/残差相位/CFO/timing 近边界翻转，或匹配滤波/循环索引；单帧无 EVM/噪声不能定根因。最小验证：未来同条件授权采集，观察同符号裕量/错位是否移动。
- 证据：`evidence/rf-teaching/s2-46db-p3-rrc-real-rx-2026-10-24/diagnosis-1bit-2026-10-24/`。

## 4 执行记录矛盾修正

计划 6/6/6/4/6/0；实际 **2 sessions / 2 init / 2 loads / 1 TX / 2 captures / 0 retry**，后 4 帧 NOT_RUN，无重派。旧报告“无 rf.init”与实际 metadata/control transcript 的 `rf.init`、2 loads 矛盾；诊断报告已记录更正，历史原文未改写。

## 5 下一次最小真板实验（未执行，需新的物理派发授权）

**推荐（仅补齐未执行模式，保留 1/112 负结果）**：使用 `next-min` 5 帧：

```bash
python -B scripts/rf_rrc_real_validation.py \
  --expected-release bedf98c77283002c632dd7f345abaff9 \
  --output <new-unique-dir> --cable-confirmed --plan next-min
```

帧顺序：OFF-first(QPSK/RRC39,noTX) → QPSK/RRC40 → BPSK/RRC6 → QPSK/RECT41 → OFF-last(QPSK/RRC39,noTX)。
预算：5 sessions/5 init/5 load/3 ON TX/5 capture/0 retry；80MHz/46dB/amp60/60MSPS/3Msym/4096/RRC0.35。
停止条件：身份/配置/结果损坏/异常/超时/cleanup失败/OFF残留；非零BER/合法失同步不停止，保留负结果。

**重要限制**：该 5 帧计划不含 QPSK/RRC39 ON 帧，因此**不能把已有 1/112 负结果升级为通过，也不能仅凭此声明“3 个 RRC ON 帧 BER=0”**。若仍要达成原 3-clean-RRC-ON 目标，必须另立一次 **6 帧重现性计划**（OFF-first、QPSK/RRC39 同条件重现、QPSK/RRC40、BPSK/RRC6、QPSK/RECT41、OFF-last；6/6/6/4/6/0），把 39 作为新实验重现（不是重试旧派发），并需新的接线确认/物理派发授权；不得用新结果覆盖旧 1/112，也不得为得到 clean 结果删错位/换seed/改阈值。

## 5.1 Window-2 真板执行结果（本轮新授权，已执行一次）

- 接线复用 2026-10-24T17:20:05.892+08:00（TX1→RX1，窗口内且未变）；新 run 目录
  `runner/run-window2/`；只派发一次，runner SHA `e95a4ecc…`。
- 实际预算：**6 sessions / 6 init / 6 loads / 4 ON TX / 6 captures / 0 retry**；
  `execution_complete=true`、`structural_valid=true`、`metrics_passed=false`，
  唯一 metric failure `03-qpsk-rrc-40`（2/112）。
- 逐帧：OFF-first 无同步/BER；QPSK/RRC39 **0/112**；QPSK/RRC40 **2/112**；
  BPSK/RRC6 **0/56**；QPSK/RECT41 **0/128**；OFF-last 无同步/BER。
- 旧 QPSK/RRC39 1/112 永久保留；新 39 0/112 仅为本批条件结果，不能据此宣称稳定零误码。
- 原始 21 文件板端/本地 SHA 21/21；post 读回清理前后 `VERIFIED_SAFE_EXPECTED`，current=bedf、boot 不变、TX/RX idle。
- 同 IQ 离线重放：所有 ON 帧板端/离线一致（完整 ModemResult 最大差 ≤1.59e-12）；实时失败未覆盖。
- 旧 1/112 与 QPSK40 2/112 诊断：错误按参考符号定位；QPSK40 两错误位于低裕量轴（0.0144/0.0562），
  max|IQ|=388、无削顶、TX payload SHA/truth/QI/区间正确；根因 **UNKNOWN**，仅保留低电平/SNR 假设。
- 未改参考/比较区间/分母/seed/验收；未重采/补采；无新安装/训练/commit。
- 结论：**本批执行完整、结构合法、指标未通过**（2/3 RRC ON clean，QPSK40 2/112）；
  原“3 个 RRC ON BER=0”目标未达成。下一步应做证据支持的最小诊断，不得为凑零误码重跑。

## 6 结论

- 本轮修复 **APPROVED（增量）**：1/112 不再误停，执行完整性与指标通过分离；结构/cleanup/OFF 安全停止保留；未改产品/候选。
- 1/112 为**已定位但根因 UNKNOWN** 的真实负结果；同 IQ 一致不等于排除 RF/算法。
- 无新 RF/安装；下一次真板必须重新取得接线确认和物理派发授权，不能复用已消费的本次派发。
