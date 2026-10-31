# 后续真实四类采集路线核对与参数草案（不执行采集）

> 本文件只做接口核对与参数草案，**不连接设备、不发射、不采集、不部署、不改产品合同**。
> 正式采集/验收需要主项目会话另行授权；本文件不设定正式验收门限。

## 1. 当前约束（只读核对结论）

- 2026-10-20 起，**本任务/当前授权范围**采用板卡接收主线：Pluto TX → 板卡 RX1，
  **本任务不启用板卡 TX**（`evidence/rf-teaching/s2-46db-rx-directionB-2026-10-20/NOTES.md`）。
  这是当前授权范围，**不是项目永久禁止板卡 TX**；历史上板卡 TX 回环/教学采集曾执行，
  后续若需启用，必须由主项目另行授权/合同步骤，不能把本任务范围外推为永久禁令。
- 板卡 BPSK 接收合同固定：60 MSPS / decim=1 / 4096 点 / 3 MSym/s / RECT / PRBS7 seed 1..127 /
  无差分编码；接收机只接受 BPSK 3M RECT（`pynq_overlay/sw/rf_bpsk_receiver.py:96-99`）。
- 板卡 TX 数字调制生成器本身支持 BPSK/QPSK/QAM16/QAM64 与 RECT/RRC
  （`pynq_overlay/sw/rf_iq_signal.py:15-18`），但生产教学/回环合同当前固定 BPSK 3M RECT、
  46 dB（`pynq_overlay/sw/rf_teaching_profile.py:14-28`）。
- 板卡 RX 侧 `rx.capture` 接口允许（`sdr_testbench/board_integration/CAPABILITY.md`：
  Pluto TX → 板卡 RX1 接口允许；当前授权范围要求板卡 TX 不启动）；`tx.start`/
  `loopback.physical.*` 不属于本任务/当前 RX-only 授权范围，但不等于项目永久禁止。
- 当前 Pluto 只能覆盖 325–3800 MHz，**不能覆盖冻结 S2 的 80 MHz**；外部 SDR 路线若用现有
  Pluto，只能在新 LO（例如 433.9 MHz）下采集，不能冒充原 80 MHz S2 结果。
- 方向 B 已有真实执行记录，但 **Pluto→板卡 BPSK 尚未取得完整统计 BER 验收 PASS**：
  - 单音捕获（2026-10-20，同 bin 对比 51.86/44.30 dB、无削顶，观察到板卡 RX 相对 Pluto TX
    的**频谱反转/共轭**）。
  - 包 #4（2026-10-20 14:44）实测 3 会话、6 段 Pluto TX、33 次 4096@60MSPS 采集；
    **29/33 对齐、0/4002 bit、OFF 假 BER 0** 仅被独立 Codex 审查认可为**已有 IQ 的离线 BER
    数值复核**（APPROVED 仅限数值）；完整统计里程碑为 **CHANGES_REQUIRED**，预算/停图闭环
    未证明，不能记为完整统计 BER 验收 PASS。依据：
    `evidence/rf-teaching/s2-46db-lead-review-2026-10-19/CODEX-VERDICT-stat-ber-FINAL.md`。
  - 后续修正时序（B1/B2/B3）后的包 #5 唯一一次执行 **FAIL**：`on_aligned=23 < 27`，
    `bit_errors=0/compared=3174`、OFF 假 BER 0；B1/B2/B3 修复与预算/安全闭环通过，失败定位为
    Pluto TX 在 7 个快照内短时无信号（未做设备端 TX 连续性观测）。依据：
    `evidence/rf-teaching/s2-46db-lead-review-2026-10-19/RESULT-stat-package5-FAIL-and-offline-analysis.md`、
    `evidence/rf-teaching/s2-46db-rx-acceptance-plan-2026-10-20/NOTES-task23-package5-FAIL-release.md`。
    **不重跑、不补写历史包**。
  - 限定：433.9 MHz 诊断域；不是分类验收、不是 80 MHz S2、不是其他调制/长期漂移；不能写成
    “全新独立泛化验证”。
- 因此 Pluto→板卡 **BPSK 已有真实执行与离线 BER 数值复核记录**，但按上述限定，完整统计 BER
  验收仍为 FAIL/未闭合；QPSK/QAM16/QAM64 外部波形与分类真值仍需下一批实测，历史回放
  42/42 只作回放评估，不是全新独立泛化验证。

## 2. 两条路线对比与选择

| 项 | A：板卡 TX1 → 板卡 RX1 回环 | B：外部 SDR（Pluto）TX → 板卡 RX1 |
|---|---|---|
| 是否启用板卡 TX | **是**（QPSK/QAM16/QAM64 需改 TX 数字模式） | **否**（板卡 TX 保持关闭） |
| 与当前授权/合同 | **本任务不选**：需启用板卡 TX、突破生产教学/回环 BPSK 合同；需新授权/合同步骤，**不是永久禁止** | **兼容**（只调用既有 `rx.capture`；不启动 TX） |
| 基带域 | 可保持 80 MHz 冻结域 | 现有 Pluto 只能 433.9 MHz，属**新 RF 域**；不能冒充 80 MHz |
| 四类波形来源 | 板卡 `rf_iq_signal` 已支持四类映射 | 需新增 Pluto QPSK/QAM16/QAM64 波形工具（现有方向 B 只有 BPSK 离线 BER 数值复核，且完整统计验收仍 FAIL/未闭合；未做四类分类） |
| 采样/吞吐 | 板卡 TX/RX 内部，已验证 BPSK 46 dB 回环 | Pluto USB2 6 MSPS 有真实执行记录；但完整统计验收未闭合，四类波形仍需验证 |
| 已知额外风险 | 板卡 TX 合约限制、生产 UI 固定 BPSK、回环契约 | 频谱反转/共轭、外部时钟 CFO、USB/FIR、新 RF 域不能沿用 80 MHz 结论 |
| 结论 | 不作为本轮最少改动路线 | **选为最少改动路线**（不启用板卡 TX、不改产品；仅新增外部波形与采集编排） |

**选择 B** 的理由：当前任务/授权范围采用“板卡 RX-only、本任务不启用板卡 TX”，A 路线需要
重新启用板卡 TX 并突破生产教学/回环的 BPSK 合同；B 路线只复用已允许的 `rx.capture`，不修改
产品、不部署，新增的是**外部 SDR 四类波形工具/实验记录**。代价是：现有 Pluto 只能在新 LO
采集，QPSK/QAM 外部波形仍需补；不能把 433.9 MHz 的 BPSK BER/单音结果写成 80 MHz S2 或
四类分类通过。板卡 TX 的历史使用说明：历史板卡 TX 回环用于真实 BPSK 采集，本轮不启用，
不等于项目永久禁止。

## 3. 四类采集参数草案（待授权，不是验收门限）

### 3.1 板卡 RX（保持冻结接收域）

| 项 | 值 | 说明 |
|---|---|---|
| 采样率 | 60,000,000 S/s | 不改 |
| decimation | 1 | 不改 |
| 每帧 | 4096 复样本 / 16,384 B | 不改 |
| 格式 | `s16_iq_le` | 不改 |
| LO | 433,900,000 Hz（现有 Pluto 可用域） | 新 RF 域；不要写成 80 MHz S2；若必须 80 MHz，需另一台覆盖 80 MHz 的 SDR |
| RX 带宽 | 30 MHz | 沿用 |
| 衰减 | 46,000 mdB | 沿用当前接收域；不改 |
| 板卡 TX | **关闭** | 每次 capture 前后只读确认 `tx_source_selected=false` |

### 3.2 外部 Pluto TX（四类）

| 项 | 值 |
|---|---|
| 采样率 | 6,000,000 S/s（sps=2） |
| 符号率 | 3,000,000 Sym/s |
| 成形 | RECT（不用 RRC） |
| 映射 | 明确实现：BPSK/QPSK 独立 I/Q；QAM16/QAM64 按现有冻结映射表（QAM64 轴表非 Gray，需在真值记录注明） |
| PRBS7 | seed 1..127，无差分编码；每模式单独记录 seed |
| 数字幅度 | 初始 ≤0.1（QAM64 按 PAPR 下调；以削顶/峰均诊断决定，不设正式门限） |
| TX 衰减 | 内部 60 dB（-89.75 dB 安全关断读回）；每次 TX 后独立读回 powerdown=1 |
| 发射时长 | 建议每次 ≤2 s，按主项目预算；本工具不执行 |
| 已知约定 | 记录 `iq_convention`（方向 B 观察到板卡 RX 相对 Pluto TX 频谱反转/共轭）；不要用模型/环境反推 |

### 3.3 批量建议（用于让工具可评估，非正式门限）

- 每类 ≥2 个独立真实会话 × ≥10 帧；OFF ≥3 帧；窄带/干扰压力 ≥3 帧（单独报告，不混入干净类）。
- 每个会话必须有独立实验记录和明确信号配置；不足的类别继续 `NOT_RUN`，不补造、不用合成/旧数据冒充真实。

## 4. 合同/安全上不允许的操作（具名列出，不绕过）

以下操作在**本任务/当前授权范围**内不得执行；这不是项目永久禁令，任何范围外操作需主项目
另行授权/合同步骤：

1. 在**本任务/当前授权范围**内启用板卡 TX（`tx.load`/`tx.start`、改变 TX 数字模式、
   `loopback.physical.*`）来采四类；不是永久禁止，历史板卡 TX 回环曾用于 BPSK 采集，后续启用
   需新授权；
2. 修改产品代码、发布、安装、daemon/overlay 或 `cnn-bpsk-environment-v1` 合同；
3. 声称 QPSK/QAM16/QAM64 **接收/解调**能力；当前只有 BPSK 3M RECT 接收合同；
4. 修改 60 MSPS / decim=1 / 4096 / s16 / 3 MSym/s / RECT 中的任何一项来“适配”外源；
5. 使用 2.4 MSPS 模板冒充 3 MSym/s，或用 RRC/差分编码绕过 BPSK 合同；
6. 用 `model_result` 或环境标签反填调制真值；用旧 nonce/旧 token 绕过 `control.arm`；
7. 把 433.9 MHz 新域的单音/BPSK BER 结果写成冻结 80 MHz S2 或四类分类通过；
8. 在未获授权时发射 Pluto 或连接板卡；超出现有接线/预算；改动 Pluto 固件以覆盖 80 MHz；
9. 自行设定正式验收门限、通过/失败 verdict 或产品资格；本工具只准备数据与工具；
10. 把原始 IQ 复制进训练/发布目录或重写原始证据；只允许 `real_campaign/` 内的索引/报告。

## 5. 本任务状态

- **不执行采集**；不连接设备；不发射；不修改产品。
- 只交付导入/去重/冻结评估工具、合成夹具、本路线核对和下一批字段清单。
- 真实数据现状与评估结果见 `reports/real_campaign_report.md`。
- 历史 BPSK **42/42**（`reports/real_campaign_report.md`）是既有回放/评估结果，只用于验证
  工具流程；**不是全新独立泛化验证**，也不能当四类总体准确率或产品资格。
