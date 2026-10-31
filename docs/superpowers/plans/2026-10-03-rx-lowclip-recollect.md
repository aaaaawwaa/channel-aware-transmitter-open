# 低削顶全矩阵重采（预声明实验，2026-10-03）

状态：**DECLARED_PRE_LAUNCH**（2026-10-03 由续接会话冻结）。根因用户选择“规划并启动预声明低削顶重采”。

## 动机与依据

正式验收被测试集质量卡死：D4 候选在 1310 个已知 TEST 帧上 640 帧削顶（535 削顶 + 105 削顶会话内），按规格计 UNKNOWN 漏检；即使完美模型每类召回上限 ≈ 0.5。现有 TEST 各幅度削顶率：

| 幅度码 | 帧数 | 削顶率 |
|---|---|---|
| 150 | 430 | 62.8% |
| 120 | 460 | 41.3% |
| 100 | 290 | 41.4% |
| 80 | 270 | 22.2% |
| **60** | **90** | **0.0%** |

D4 在可用（无削顶）帧上诊断 F1 达 0.9497（TONE 1.0/BPSK 0.78/QPSK 1.0/QAM16 0.90/QAM64 0.89）。因此把采集幅度压到 60（备降 40）即可得到全可用独立会话。

## 冻结参数

- 发布：`46fe2c080deb9d0c42eb0a612a86d091`（collect.py VERIFIED_RELEASE_ID 不变）
- 接线：TX1-RX1 直连（无示波器）→ `--cable-confirmed` 必须
- 射频：LO 80MHz / Fs 60MSPS / BW 30MHz / tap29 / 内部 40dB / 1R1T
- 采集：4096 点 / ratio 0 / `--analyze`
- 格：全部 30 格（TONE + 4 调制 × 3 速率 × 2 成形 + OTHER×4 + OFF）
- 每格 3 会话 × 每会话 10 帧 / 每 split；共 900 有效帧/split、2700 总
- 幅度阶梯：**(60, 40)** —— 60 实测 0% 削顶，40 为记录过的备降档
- 种子池：沿用 split 专属池（train/val/test 家族不相交；新 TEST 与 D4 训练家族不相交，lineage 门禁可过）
- 无增强（--augment-per-session 关闭）；仅 physical origin
- egress 每会话验证：control disarmed / RX ARM=false / TX source=false

## 预声明的源码修订（TDD）

`scripts/rf_ml/collect.py` 的 `AMPLITUDE_LADDER` (150,120,100,80,60,40) → (60,40)。
- RED：`host/tests/test_rf_ml_collect.py` 更新（txpeak150→txpeak60；削顶重采 150/120→60/40；failures [60]；amplitudes_used [60,40]），旧常量下 1 failed ✓
- GREEN：常量+注释更新；聚焦 3 passed ✓；全量 34 项等待
- 编辑前基线哈希：collect.py `5df45919…`、tests `e645398a…`（与 final-receipt 绑定一致）
- 独立复审检查点在 GREEN 后记录

## 执行顺序与停止条件

train → val → test（严格串行，唯一 daemon，绝不并行）；每会话后 checkpoint（支持 --resume）。
停止：fatal 会话即停本 split；重复 IQ 哈希即失败；板卡不可达/发布不符/daemon 离线不发会话；中断恢复必须确认 safe。

## 采集后序列（不属本声明执行范围）

1. 每 split production 校验（production=True, coverage_origin=physical）
2. 用冻结 D4 checkpoint 评估新独立 TEST（阈值冻结）
3. 未达门禁则按 D4 配方以新 TRAIN/VAL 重训（另立预声明训练步骤），再评估同一冻结新 TEST
4. 全部门禁通过才可 production 声明；否则保留 experimental

## 非目标

不动天线/LNA、不部署/安装任何发布、不接示波器、不用合成数据、不用 TEST 调参、不加增强、不改阈值与网络。

机器可读：`evidence/rx-smart/lowclip-recollect-2026-10-03/experiment-plan.json`（sha `7764d6d6…`）。
