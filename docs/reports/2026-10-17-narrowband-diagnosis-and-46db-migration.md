# 窄带失步离线定位 与 46 dB 环境域迁移准备（开发报告）

日期：2026-10-17（Asia/Shanghai）。性质：**纯离线**——未连板、未初始化 RF、未发射、未采集、未派发、未消耗任何授权或 nonce、未改产品代码/测试/封存件。

## 0. 一句话结论

1. **narrowband CARRIER_NOT_SYNCED 根因已证实**：合同声明的带内干扰把冻结接收机的载波相干度压到 **0.6998502**，比冻结门限 **0.70** 低 **1.4978e-4（-0.0214%）** ⇒ **是预期结果，不是软件缺陷**，不引入修复。
2. **另有一项未证实的机制发现**（端到端基带频谱反转），给出**单变量实验方案**，待新授权后验证。
3. **码域余量保留为诊断指标**（D1），**不新增 6 dB 硬门**；已更正三处表述。
4. **46 dB 独立 TRAIN/VAL 最小矩阵 = 6 会话 / 51 帧**；迁移最短路径 **M0-M12 = 2 候选 / 2 ARM / 2 安装**；存在 **两处需用户拍板的前置阻塞**（B-1 父证明路线、B-2 域策略）。

## 1. 保留的既有事实（不重开已完成流程）

- attempt4：RUN_COMPLETED、exit 0、**1 次初始化**、**5 帧全部 accepted**、**0 重试**、native rail_count 全 0；
- 回放：normal/pulse 同步 0/138；narrowband = CARRIER_NOT_SYNCED；两个 OFF 为 TX 关底噪参考；
- 最坏码域距名义满量程 **4.25 dB**（pulse 1256 code）；
- S2 正式验收**未完成**：40 dB 旧基线仍在，46 dB 新基线/独立验证尚未建立。

## 2. 窄带失步根因（已证实）

### 2.1 机制

narrowband 场景**按合同额外叠加满长度带内干扰**（6 MHz 复音，40 码 vs BPSK 60 码；rf_experiment_scenarios.py:107,117-120；规格 2026-10-10-…-design.md:24）。归档采集中该干扰实测 **390.25 码**（BPSK **599.32 码**），把冻结判据「载波相干度 |mean(z^2)|/mean(|z|^2)」压到 **0.6998502231371331**，低于冻结门限 **0.70**，于是 pynq_overlay/sw/rf_bpsk_receiver.py:138 返回 CARRIER_NOT_SYNCED。

干扰功率几乎全进分母（mean|t|^2=152296.44，占 28.5%），平方后在 |mean(t^2)| 只剩 37.27（自身功率的 2.4e-4）⇒ 分子仍是 BPSK 项（374227.16）。

### 2.2 两向单变量反事实（冻结接收机与门限不动，只改归档字节）

| 反事实 | 结果 |
|---|---|
| 减去测得干扰 | **reference_sync=true、0/138、相干度 0.947734** |
| 把同一干扰加到 normal 帧 | **CARRIER_NOT_SYNCED、0.694796** |
| 仅重量化对照 | 与原始帧**逐位相同**（0.6998502） |
| OFF 帧同样处理 | 仍不同步 |

幅度扫描在 scale **0.98（0.7029，同步）↔ 0.99（0.6985，拒绝）**翻转，与解析边界一致；同幅度置于 **+6 MHz** 也拒绝（0.696787）⇒ 门限对干扰频率符号不敏感。

### 2.3 被反驳的其它假设

- **源侧无波形/非法参数**：源摘要 4693355b…、hardware_verified、AWAIT_ACCEPT；扣掉干扰即 0/138；
- **CFO 超限**：触发的是相干度门；实测 CFO **61.0 Hz** 远小于 100 kHz；
- **功率/SNR 不足**：narrowband 是五帧最强（峰值 1260.67 码），无 CLIPPED/NO_ENERGY；
- **重放配置错配**：五帧 submitted 配置逐字节相同且等于载荷声明（rf_headroom_replay.py:71-82），replay.json summary 逐字段复现 5/5 exact。

### 2.4 判定：不是软件缺陷

门限按代码字面工作；声明参数理想值 A^2/(A^2+B^2)=0.6923 在加噪声前就已越界；规格**预期失步并禁止挑正常子集**；拒绝是 fail-closed、不产 BER；与 40→46 dB 无关（度量尺度不变）。**未放宽任何判据、未改任何阈值，因此不给最小修复。**

> 若项目希望该帧可同步，那是**新合同**里的场景参数/度量定义问题（门限对应临界干扰 39.279 码，声明 40 码超出 1.8%）⇒ 必须新候选 + 新资格 + 新安装，且 **0.70 门限本身不得移动**。

## 3. 次要发现（未证实）：端到端基带频谱反转 + 单变量实验方案

**观察**：已审查载荷的干扰声明在 **+6 MHz**（TX 载荷差实测 +6000366.2 Hz / 39.92 码），而采集中出现在 **-6 MHz**（差值谱线 389.80 码，+6 MHz 仅 16.83）；narrowband 采集对**共轭**载荷拟合 corr 0.9721 / 残差 5.49%，对原样载荷 0.6896 / 52.44%；normal/pulse 两种拟合相同。候选机制：TX/RX 数字通路 I/Q 交换。**机制未证实**，与第 2 节判定无关。

**优先验证假设 P1（单变量方案，需新授权 + 接线 + 派发；本轮未做）**

| 项 | 内容 |
|---|---|
| 变量 | 已知复基带分量的符号（复用现有 narrowband 载荷即可） |
| 固定量 | LO / Fs / 带宽 / 衰减 / 长度 / seed |
| 样本 | 1 帧读符号；3 帧看波动 |
| 判据 | -6 MHz 拟合比 +6 MHz 高 **>20 dB**（归档为 **27.3 dB**） |
| 预算 | 1 初始化 / 1 尝试 / 1 物理帧 |
| 失败处置 | 不可分辨则保持**未证实**；**不得**改接收机或门限凑结果 |

## 4. 码域余量（D1）口径

- **保留为诊断指标**：M=20log10(2048/max|peak|)，参考面=ADC 数字码（AGC 之后、数字增益关）；已归档五帧可算（最坏 4.25 dB，pulse）；
- **不新增 6 dB 硬门**，不作为推进 46 dB 域或恢复 S2 主线的前置条件；
- 已更正的三处表述（原文保留 + 标注）：① 多份授权**不能**实现逐档扫描（衰减在 rf_headroom.py:36,405-407 与 rf_daemon.c:2002,2020-2021 两侧固定，N 份授权 = N 次同一 46 dB 会话；换档须改控制与判定路径）；② 删除「每档重新人工接线确认」硬约束（按 development-review-policy.md:47 可在有效窗口内引用原确认；派发仍需新授权工件 + 新 nonce + 新 attempt_id）；③ D1 仅为诊断。

## 5. 46 dB 环境域迁移（最小步骤）

### 5.1 TRAIN/VAL 最小矩阵

| 段 | 会话 | 帧 | 说明 |
|---|---|---|---|
| TRAIN | 2 | 22 | normal x10 + off x1 每会话；**仅 normal 进 fit** |
| VAL | 1 | 11 | 同上；越界即失败 |
| 冻结前 pilot | 3 | 18 | 6 类 x3；freeze 硬前置（known_correct=9、negative_false_clear=0） |
| **合计** | **6** | **51**（normal 33） | narrowband/pulse/compound/domain 只作判读与负控 |

域固定：attenuation_mdB=46000、LO 80 MHz / Fs 60 MSPS / BW 30 MHz / 2R2T / idelay29、BPSK 3 MSym/s RECT / 4096 点、种子轮换 (1,37,73)。停止条件沿用（CLIPPED / INVALID_IQ / egress 不安全 / 缺件 / 重复 identity / 会话不闭合 / 不可靠 BPSK / VAL 越界 / 身份漂移），**不重试、不补采、不调参、失败帧保留**。

**现有 attempt4 五帧只作 POST_HOC 开发诊断**：不作独立 TEST、不作 TRAIN/VAL（域判 INVALID/RF_DOMAIN_MISMATCH、无环境合同逐帧 sidecar、n=1、身份不可替代）。

### 5.2 46 dB 基线口径

- canonical sha = **8 字段 body** 规范化 JSON 的 sha256（schema_version / environment_version / algorithm_version / calibration_status / rf_config / spectrum_envelope(4096) / thresholds(13 键) / sources），**不含 sha256 字段本身**；sources 只允许 split=train；
- 与 40 dB 旧基线在 version / rf_config / envelope / thresholds / sources **全变**；**旧 64a00c6a… 不得用于宣布 46 dB 通过**；
- EnvironmentBaseline 要求 rf_config==FIXED_RF ⇒ **不改常量时 46 dB 基线无法构造**。

需重签面 12 项：rf_teaching_profile.py:19、rf_environment.py:19、新 baseline 文件、rf_status_client.py:839-848、ui_model.py:76,2144-2148,1695、rf_environment_acceptance.py:14-15,49、rf_environment_dataset.py:58-59 与标定入口、rf_bpsk_ui_board_session.py:210-213、freeze 声明全量重出、N1-N12 重跑、测试面（teaching 不等于 research 断言）、S1 期 40000 回环工具。

### 5.3 最短迁移路径 M0-M12

M0 并入本窄带结论 → M1 域常量 + 版本 → M2 bootstrap 46 dB 基线（用 attempt-2 normal，provisional n=1）→ M3 候选 B（父 c3bf915c，复用 bit/HWH/model/native bbfd0409）→ M4 ARM + prepare + 安装 B → M5 TRAIN(2 会话) → M6 VAL(1 会话) → M7 拟合最终基线 + VAL 复算 → M8 候选 C → M9 ARM(C) → M10 pilot 18 帧 + 同 IQ 重放 → M11 N1-N12 + freeze + prepare → M12 安装 C + 读回。

**最短 = 2 候选 / 2 ARM / 2 安装**。

### 5.4 两处真实前置阻塞（需用户决定）

- **B-1 父证明路线**：e66197ed… **没有 INSTALLED_VERIFIED 安装收据**（它是「启用编排」切换来的，只有可控失败演练与启用收据）⇒ prepare 的父证明必失败。选项 (a) 扩展父路线以接受**启用编排证据链**（工具 + 测试 + 复审）；或 (b) **先切回证据链完整的 399b300e 再安装**（切换是板卡写入、须留证，且会把板卡能力暂时从 46 dB 研究通道降回 40 dB 环境通道）。
- **B-2 域策略**：常量移到 46000 会撞 test_rf_headroom.py:1597-1607,1753 的 **teaching 不等于 research** 断言与一批 40000 用例 ⇒ 需决定「**一次性改域并重述 HEADROOM 合同**」还是「**保留 teaching=40000、另设第二域**」（后者改动面更大）。

## 6. 下一次物理验证：假设 / 变量 / 预算

前提：M0-M4 完成。

| 项 | 内容 |
|---|---|
| 假设 | **H1** TRAIN normal 10/10 可靠且 11 帧会话跑完；**H2** VAL 不越界且 off 不 CLEAR；**H3** pilot 负例 9 帧零 CLEAR |
| 自变量 | **仅会话**（非衰减、非场景、非阈值） |
| 样本 | **6 会话 / 51 帧**（normal 33） |
| 时间 | 单帧 68.27 微秒；锚点：HEADROOM 5 帧 13.330 s、环境 pilot 2 帧 18.5 s、ARM 35.6 s、安装 41.2 s；估计 11 帧会话 30-60 s、物理段约 0.5 工作班、离线段约 1-2 工作日；RF 实际发射约 3.5 ms |
| 授权 | **0 份 single_use**（环境路径用 token + --cable-confirmed；接线未变可引用原确认） |

另：若批准 P1（频谱反转验证）：**1 初始化 / 1 尝试 / 1 物理帧**，判据见第 3 节。

## 7. 需要用户决定的事项（仅列真正需要拍板的）

1. **B-1**：父证明路线选 (a) 扩展父路线接受启用编排证据链，还是 (b) 先切回 399b300e 再安装？
2. **B-2**：域策略选「一次性改域并重述 HEADROOM 合同」还是「保留 teaching=40000、另设第二域」？
3. **P1**：是否批准频谱反转单变量验证（1 初始化 / 1 尝试 / 1 帧，需新授权 + 接线）？
4. **是否启动 M1**（域常量与版本迁移，产品代码变更 ⇒ 需新候选 / 新 ARM / 新安装）。

## 8. 未做 / 未证明（如实保留）

- M0-M12 全部 NOT_RUN；未连板、未初始化 RF、未发射、未采集、未派发、未消耗授权/nonce、未安装/切换发布、未改产品代码/测试/封存件；
- 46 dB 各场景标签正确性、任何 dB 余量结论（NOT_ASSESSED）、TRAIN 真实时长（第 6 节为估计）、P1 机制、历史上其它采集是否同样频谱反转——均**未证明**；
- 明确排除：6 dB 硬门、P1dB/外部仪器、多档扫描、P1-P5 实施、S2 正式 108+12、真人验收、S3/S4。

## 9. 证据索引

- 窄带诊断：evidence/rf-teaching/s2-headroom-narrowband-diagnosis-2026-10-17/{NOTES.md,diagnosis.json,evidence-index.json,work/,logs/}
- 46 dB 迁移：docs/superpowers/specs/2026-10-17-headroom-46db-environment-migration.md、evidence/rf-teaching/s2-headroom-46db-migration-2026-10-17/{NOTES.md,refs.json}
- 余量定义与更正：docs/superpowers/specs/2026-10-17-headroom-margin-definition.md、evidence/rf-teaching/s2-headroom-margin-offline-2026-10-17/
- 第四次研究：evidence/rf-teaching/s2-headroom-research-attempt4-2026-10-17/
- 启用与编排复审：evidence/rf-teaching/s2-headroom-activate-2026-10-17/{NOTES.md,review/,review-delta/}
