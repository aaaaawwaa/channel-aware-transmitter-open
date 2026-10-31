# S2 环境诊断合同冻结（P1 / Task 2.2 第 2 条）

日期：2026-10-14。**本文件冻结判据，不执行采集**：不连板、不发射、不落新物理帧。它把 `${baseline}` 与 `${acceptance}` 里已经写死的判据逐条列出，并补齐计划要求的"TRAIN/VAL/TEST 真实会话分组、四类定义与阈值、12 离线负控"。

本文件把 `pynq_overlay/sw/rf_environment_baseline.json`（基线）与 `scripts/rf_environment_acceptance.py`（正式验收）里已经写死的判据逐条列出，并补齐计划 Task 2.2 要求的"TRAIN/VAL/TEST 真实会话分组、四类定义与阈值、12 离线负控"。

出处缩写：`env` = `pynq_overlay/sw/rf_environment.py`；`base` = `pynq_overlay/sw/rf_environment_baseline.json`；`scen` = `pynq_overlay/sw/rf_experiment_scenarios.py`；`acc` = `scripts/rf_environment_acceptance.py`；`ds` = `scripts/rf_environment_dataset.py`；`ver` = `scripts/rf_environment_verify.py`。

## 1. 已经写死的判据（代码即判据，本文件不改写）

### 1.1 域与版本

| 项 | 冻结值 | 出处 |
|---|---|---|
| 环境合同版本 | `rf-window-environment-v3` | `env:19` |
| 算法版本 | `bpsk-rect-snapshot-v2` | `env`（`BPSK_VERSION`） |
| 基线摘要 | `64a00c6a2975252ff2c0fa055318b06912084b07dc5db759b784e95b131501a3` | `base.sha256` |
| 基线标定状态 | `development`；`sources` **20 条且全部 `split='train'`**，来自两个源会话 `s1-s00`、`s1-s01`（实测统计） | `base.sources` |
| 基线 RF 配置 | LO 80 MHz、Fs 60 MHz、RX/TX BW 30 MHz、**attenuation_mdB 40000**、1R1T=0、idelay_en=1、tap=29 | `base.rf_config` |
| 固定接收域 | BPSK / 3 MSym/s / RECT / decim 1 / 4096 点 / 60 MSPS / `reference_enabled=True` | `acc:16-17` |
| 特征集 | 11 项（window_us、rms_codes、crest_ratio、q999_ratio、tone_concentration、tone_excess、tone_frequency_hz、pulse_sample_fraction、pulse_longest_samples、pulse_start_sample、pulse_end_sample_exclusive、sign_balance） | `env:24-27` |

### 1.2 阈值（基线冻结值，逐字）

| 阈值 | 值 | 用途 |
|---|---|---|
| `minimum_rms_codes` | 16.0 | 低于即 `INVALID/LOW_ENERGY` |
| `tone_concentration` | 0.15 | 窄带触发之一 |
| `tone_excess` | 0.18 | 窄带触发之二 |
| `tone_weak_excess` | 0.025 | 弱谱异常 → `UNKNOWN/WEAK_SPECTRAL_ANOMALY` |
| `pulse_outlier` | 1.514364175465688 | 脉冲样本判据 |
| `pulse_crest` | 1.5921870687410116 | 脉冲峰值判据 |
| `pulse_max_fraction` | 0.1 | 脉冲占比上限 |
| `pulse_min_samples` | 3.0 | 最少样本 |
| `pulse_after_tone_min_samples` | 8.0 | 去单音后最少样本 |
| `pulse_after_tone_min_density` | 0.3 | 去单音后最小密度 |
| `pulse_plateau_outlier` | 1.3869868475054137 | 平台判据 |
| `pulse_plateau_min_samples` | 16.0 | 平台最少样本 |
| `pulse_plateau_min_density` | 0.35 | 平台最小密度 |

### 1.3 受控场景（四类 + off + domain）

- `KINDS = ('normal','narrowband','pulse','compound','off')`；公式 `x = A*s + j`（只缩放期望的归一化 BPSK 波形，干扰用固定 DAC 码、不随 A 缩放）；`PERIOD_SAMPLES=2540`；`physical_max_code=100`；峰值约束 `sum(amplitude, tone_amplitude, pulse_amplitude) <= 100`（`acc:71-73`）。
- 默认码：`amplitude=60`；`tone_amplitude` 默认 40（`compound` 默认 15）；`pulse_start=600`、`pulse_width=80`；`prbs7_seed=1`（`scen:42-52, 92-93`）。
- **truth 只用于排程，永不进入诊断**（`acc:3`、`scen:2`）。

### 1.4 标签与拒绝原因

- 标签集合：`CLEAR` / `NARROWBAND` / `PULSE` / `UNKNOWN` / `INVALID`（`env:280-381`、`acc:106`）。
- 具名原因（部分）：`LOW_ENERGY`、`BASELINE_MISSING`、`BASELINE_INVALID`、`COMPOUND_RULES`、`WEAK_SPECTRAL_ANOMALY`、`AMPLITUDE_OUTSIDE_RULE_SUPPORT`、`BPSK_NOT_RELIABLE`、`TONE_WITHOUT_RELIABLE_RESIDUAL`、`SPECTRAL_EXCESS`、`SPARSE_AMPLITUDE_OUTLIERS`、`NO_COVERED_ANOMALY_IN_WINDOW`。
- **`UNKNOWN` ≠ 通过**；`CLIPPED` / `INVALID_IQ` 直接判该帧不合格（`acc:102`）。

### 1.5 TRAIN/VAL/TEST 分组规则（真实会话，禁止泄漏）

`ds.validate_splits`：**同一源、其后代、相同 IQ 摘要与同一会话必须落在同一 split**；跨 split 出现分派冲突即报 `source/session/IQ leakage across splits`（`ds:13-44`）。

- S1 标定（历史、离线）：两个 S1 会话作 TRAIN、第三个（`s02`）作 VAL（`ds:47-105`）。**注意：VAL 组由标定工具建立，不在 `rf_environment_baseline.json` 的 `sources` 里**（该文件只登记 20 条 TRAIN 源）；标定用 IQ 摘要须进入正式 freeze 的 `calibration_iq_sha256` 排除清单（`acc:74-76, 98`）。
- **派生窗口不得跨组**：派生自同一原始会话的任何窗口都继承该会话的 split。
- 正式 TEST 必须来自**独立会话**，且与 TRAIN/VAL 的会话、源、IQ 摘要都不重叠。

### 1.6 正式物理验收门（写死在 `acc.campaign`）

| 项 | 判据 | 出处 |
|---|---|---|
| 总帧数 | **108**（`expected_frames` 必须恰为 108） | `acc:46, 244` |
| 会话数 | **6**，全部 `closed=True` 且 `disarm_ack=True` | `acc:244-246` |
| 已知帧 | 每类（normal/narrowband/pulse）各 **30** 帧，正确判别 **≥27** | `acc:246` |
| 正常误报 | `normal_false_alarm <= 3` | `acc:247` |
| 负控帧 | **18**（compound/off/domain × 2 × 3 会话），`false_clear == 0` | `acc:208-212, 247` |
| 身份唯一 | 每帧 identity 与 IQ 摘要不得重复 | `acc:219-221` |
| 帧完整性 | IQ 恰 16384 字节、SHA 与行内一致、`egress_safe=True`、前后读回存在 | `acc:96-119` |
| 参考种子 | 会话轮换 `(1, 37, 73)`；`actual_scene_prbs7_seed=1` | `acc:47, 207` |
| 早期停止 | 任一 `cause` 非空即停止，剩余帧不补采；失败帧**保留在收据里** | `acc:224-253` |
| 未完成项 | `human_acceptance` 固定为 `NOT_RUN`；`offline_rejection` **默认** `NOT_RUN`，但正式 108 帧入口必须携带实测 N1–N12 收据（见 §2.1），否则拒绝启动 | `acc:253`、`scripts/rf_environment_board_session.py` |

**帧数与分类**：`6` 会话 ×（前 3 会话：3 类 × 10 帧 = 30；后 3 会话：compound/off/domain × 2 帧 = 6）= `90` 已知 + `18` 负控 = **108**。

## 2. 本文件新冻结的内容（计划 Task 2.2 第 2 条）

### 2.1 12 项离线负控（本次定义）

对**同一份已封存 IQ**做数学重放，逐项必须被拒绝且给出具名原因；全部离线、零新物理帧（`ver:20-39` 已声明 replay 语义）：

| # | 注入项 | 期望结果 |
|---|---|---|
| N1 | 非冻结发布 `release_id` | 拒绝（身份不符） |
| N2 | 过期/陈旧快照（超出 30 s 新鲜度） | 拒绝（`STALE`） |
| N3 | 连接代次或配置修订与当前不符 | 拒绝（identity mismatch） |
| N4 | 削顶帧（`CLIPPED`） | 拒绝，不得判 `CLEAR` |
| N5 | 域外采样参数（非 60 MSPS/decim1/4096） | `INVALID` |
| N6 | 无参考 / 参考不可用 | 拒绝 BER，标签不得升级为 `CLEAR` |
| N7 | IQ 摘要命中 `calibration_iq_sha256` | 拒绝（标定集排除） |
| N8 | 重复 identity 或重复 IQ 摘要 | 拒绝（duplicate） |
| N9 | 非 BPSK/低能量帧 | `INVALID/LOW_ENERGY` |
| N10 | 基线摘要不符（错误 `baseline_sha256`） | 拒绝 |
| N11 | 复合（窄带+脉冲同时）帧 | `UNKNOWN/COMPOUND_RULES`，**不得**择一标签 |
| N12 | 部分帧（截断 payload） | 拒绝（长度/身份） |

要求：12/12 必须被拒绝或判 `UNKNOWN/INVALID`，且**没有任何一项被判 `CLEAR`**；逐项保存具名原因与输入摘要；该组结果写入正式收据的 `offline_rejection`——正式 108 帧入口（`scripts/rf_environment_board_session.py --offline-rejection`）**必须**携带实测收据，缺失即拒绝；`campaign` 的默认值仍是 `NOT_RUN`，只服务非正式/离线调用。

**实现记录（2026-10-15）**：N1–N12 已实现为 `scripts/rf_environment_offline_negatives.py`（**34 项测试** + 收据 `evidence/rf-teaching/s2-offline-negatives-2026-10-14/`）：**12/12 通过、0 项 CLEAR**，并有未注入对照（记录门 ACCEPTED + `CLEAR/NO_COVERED_ANOMALY_IN_WINDOW`）证明结论非空洞。口径澄清与开放项：

- **N6 按裁决层满足**（记录门 + BER 拒绝）；**标签层**在无参考时仍可能给出 `CLEAR` → 记为 P4 开放项（最小修法涉及 `_domain_error`/`_reliable_bpsk`，任何改动 = 新算法/环境版本 + 新基线 + 重新资格）。
- **N2 的拒绝来自 `rf_rx_analysis` 的 30 s 窗口与 `ui_model` 快照映射**；`rf_environment_acceptance.validate_frame` 本身**没有独立陈旧判据** → 记为开放项。
- PRBS7 种子 `1/37/73` 互为同一 m 序列的循环移位，**不能**用作"参考不可用"注入（实测换错种子仍 `reference_sync=True`、`ber=0.0`）；N6 使用 `reference_prbs7_seed=None`。
- 冻结 compound 声明域内没有参数点能同时触发窄带与脉冲，`COMPOUND_RULES` 只能由更强复合帧触发；N11 使用**声明的构造帧**（已在收据 `data_sources.constructed` 标明不是真实射频数据）。
- **正式收据的 `offline_rejection`**：`scripts/rf_environment_acceptance.campaign` 现接受实测收据（默认仍为 `NOT_RUN`；非 `(12,12,0,0)`、缺字段或畸形即**拒绝**）。该门禁文件改动**待独立复审**后才能被正式 108 帧会话依赖。

### 2.2 final_prj 的冻结声明（freeze）字段

沿用 `acc.validate_freeze` 的必填集（少一项即拒绝构造硬件客户端）：`expected_release`、`environment_version`、`baseline_sha256`、`algorithm_version`、`fixed_rf`、`software`（`rf_environment.py`、`rf_environment_baseline.json`、`rf_experiment_scenarios.py`、`rf_status_worker.py`、`ui_model.py`、`ui_view.py` 六个 SHA-256）、`scene_declarations`（normal/narrowband/pulse/compound）、`rx_config`、`reference_seeds=[1,37,73]`、`actual_scene_prbs7_seed=1`、`split='test'`、`expected_frames=108`，另加 `calibration_iq_sha256` 排除清单。

**在 final_prj 里重新生成这份声明时，必须重新计算六个软件 SHA 并写入自己的一份；不得沿用源工程的摘要**（源码摘要已因 P1 增量而变化；`rf_environment.py` 等未被 P1 改动，但仍须逐项复算）。

**除 `validate_freeze` 的弱门之外，正式路径还要求 `tool_hashes`**：`rf_environment_freeze.check_candidate` 会拒绝没有 `tool_hashes`（或与 `rf_environment_freeze.tool_hashes()` 不等）的声明——它是 108 帧主机侧启动器在上传前调用的第一道门（2026-10-15 由 delta 复审发现并补齐；缺少它时弱门会错误地"通过"）。因此本声明的必填集**再加**：`tool_hashes`（`FORMAL_TOOLS` 全部文件的 SHA-256，必须等于本仓当前值，因为启动器上传的正是本仓文件）。

**2026-10-15 强门复核：仍未具备正式采集资格。** `tool_hashes` 匹配只能证明工具绑定；交付声明的 `expected_release=b82b141e…` 对应候选的合同为 `cnn-bpsk-headroom-v1`，而 `check_candidate` 要求 `cnn-bpsk-environment-v1`，且其软件摘要与声明不等。真实调用 `check_candidate(freeze,b82)` 返回 `ValueError('freeze must bind the exact verified candidate/code/baseline/scenes')`；主机启动器更早在合同检查处拒绝。必须先产出并资格验证与 v3/40 dB 冻结域完全一致的环境诊断候选，再重建声明和独立审查；不得改写 b82 的合同或借用其已安装身份。

### 2.3 46 dB 研究与本合同的关系（决策规则）

1. 本合同当前的固定域是 **40 dB**（`fixed_rf.attenuation_mdB=40000`）。
2. P1 的 46 dB HEADROOM 研究**只**用于判定"能否得到稳定、低削顶的 BPSK 实验域"；若通过，必须**新建** 46 dB 的基线（在 46 dB TRAIN 上 `fit_baseline`）、提升 `environment_version`、重建候选并重新资格与安装——**不得**把 46 dB 的帧拿去套 40 dB 的阈值与基线。
3. 若 46 dB 研究失败：只做**单变量定位**（TX 衰减/AGC/成形各一次），保留原始 IQ 与拒绝原因；**禁止**用旧 40 dB 基线替代 46 dB 结论，也禁止把失败改写成通过。
4. 46 dB 研究本身受 P1 的执行门约束（人工接线 + 一次性授权/token，单次会话最多 5 尝试）。

### 2.4 失败计数与保留

- 每帧必须记录：请求与实际 RF 参数、`requested` vs `actual`、削顶计数、具名拒绝原因、原始 IQ 摘要；
- 失败帧、拒绝帧、`UNKNOWN` 帧一律计入收据且**不得筛除**；聚合时按 `truth` 与标签分列，禁止只报"可用帧"；
- 会话级 `cause` 一旦出现即停止后续会话，收据写 `stop_cause`，不补采。

## 3. 执行顺序（每步独立门）

```
本合同冻结 → 开发复验（现有 TRAIN/VAL + 同 IQ 重放）
  → 12 项离线负控（§2.1）→ 一次性授权 + 人工接线下的 108 物理帧（6 会话）
  → 同 IQ 主机/ARM 判等 + 实验记录/实体屏当前与历史状态 → 失效/停止/最终安全读回 → 独立复审
```

## 4. 禁止事项

1. 不得修改阈值、基线、`FIXED_RF`/`FIXED_RX` 或标签集合来"让结果好看"；改动 = 新版本 + 新基线 + 重新资格。
2. 不得把派生窗口放进与源会话不同的 split；不得用 TEST 会话做标定。
3. 不得把 `UNKNOWN` 当通过、不得把 `CLIPPED` 帧当有效帧、不得删除失败帧。
4. 不得用单帧零错宣称低 BER/低误报；环境结论只能表述为"**采样窗口内**"。
5. 不得把本合同的 108+18 与 S2 之外的目标（QPSK/QAM/RRC、EVM）混算。

## 5. 未实现 / 未验证（NOT_RUN）

| 项 | 现状 |
|---|---|
| 46 dB 研究 | `NOT_RUN`（P1 执行门未开启） |
| 12 项离线负控 | **已实现并 12/12 通过**（`scripts/rf_environment_offline_negatives.py` + 34 项测试 + 收据 `evidence/rf-teaching/s2-offline-negatives-2026-10-14/`）；门禁接线经独立复审后已修两处 P1（campaign 级用例 + 正式入口 `--offline-rejection`），**该 delta 待复审** |
| 108 物理帧正式验收 | `NOT_RUN`（历史仅有开发帧；开发物理累计 67，不计正式） |
| 真人验收 | `NOT_RUN` |
| 同 IQ 主机/ARM 判等的**新**摘要 | 历史有（旧候选）；final_prj 需重绑 |
| 派生窗口自动校验 | 现有 `validate_splits` 覆盖源/会话/IQ 摘要；"派生窗口"尚无独立断言 |

## 6. 变更控制

本文件任何数值、标签、分组规则或门限的改动 = 新版本号 + 新基线摘要 + 重跑 §3 全部门；**旧收据不继承**。
