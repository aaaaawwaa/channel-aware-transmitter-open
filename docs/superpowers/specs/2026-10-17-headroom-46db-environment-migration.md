# 46 dB 环境域迁移：独立 TRAIN/VAL 最小矩阵、基线重签与版本迁移步骤

**状态**：离线分析 + 最小实施计划（`PLAN`），2026-10-17。**不是合同、不是批准件、不是验收记录**；本文件不实施任何步骤、不冻结任何阈值、不提出新的产品变更实施。
**范围**：本文件与 `evidence/rf-teaching/s2-headroom-46db-migration-2026-10-17/` 为唯一新增物；另在 `docs/superpowers/specs/2026-10-17-headroom-margin-definition.md` 就地更正三处（原文保留、加"更正"标注）。
**边界**：零 RF、零板卡接触、零授权/nonce 消耗、零产品代码/测试/封存件改动、零 `git commit`；**不新增 6 dB 硬门、不扩展 P1dB/外部仪器/多档扫描**。
**来源**：逐条 `path:line` 与文件 sha256 见 `evidence/rf-teaching/s2-headroom-46db-migration-2026-10-17/refs.json`；核对记录见同目录 `NOTES.md`。

## 0. 一句话结论

1. 当前已安装发布 `e66197ed79131b73aa4f47804487c053`（合同 `cnn-bpsk-headroom-v1`）只提供**固定 46 dB 的研究通道**：每会话 1 次初始化 + `authorized_attempts` **恰为 5** 的尝试预算、`single_use` 授权 + nonce 账本，固定顺序里只有 **1 个 normal 帧**（`off-before→normal→narrowband→pulse→off-after`）。它**不能**承载环境合同的 TRAIN/VAL，也**不能**做多档扫描（衰减在 Python 与原生两侧都是固定常量）。
2. 46 dB 的"独立 TRAIN/VAL + 新基线"必然是一次**域版本迁移**：域常量、`environment_version`、基线文件、候选、ARM 资格、安装整体重签。旧 40 dB 基线（canonical sha `64a00c6a…`）**不得**被用来宣布 46 dB 通过（合同冻结 §2.3 已明文禁止）。
3. 最小物理矩阵 = TRAIN/VAL **3 会话 / 33 帧** + 冻结前开发 pilot **3 会话 / 18 帧** = **6 会话 / 51 帧**；环境路径**不消耗 nonce**，也不做档位扫描。
4. 最短路径 = **2 次候选构建 / 2 次 ARM 资格 / 2 次安装**（bootstrap 基线 → 拟合后基线）。两个**真实前置阻塞**见 §4.0：域常量移动的连带面（B-2）、e66197ed 的"回滚父"证明缺失（B-1）。
5. 现有 5 帧 46 dB 研究帧只作 **POST_HOC 开发诊断**，**不作独立 TEST**，也不进 TRAIN/VAL（理由见 §2.7）。

## 1. 核对过的硬约束

| # | 约束（可复算） | 来源 |
|---|---|---|
| K1 | 46 dB 在两侧都是固定常量：Python `RESEARCH_ATTENUATION_MDB=46000`、进入 `FIXED_RF` 与逐帧读回判定；原生 init/config 字面量与读回判定同样 46000 | `pynq_overlay/sw/rf_headroom.py:36,51-54,405-407`；`rf_overlay/sw/board_app/rf_daemon.c:2002,2020-2021` |
| K2 | HEADROOM 会话 = 1 初始化 + ≤5 尝试；授权里 `authorized_attempts` 必须恰为 5，`single_use`，nonce 记账；接线确认 12 h 硬窗口 | `pynq_overlay/sw/rf_headroom.py:32-34`；`scripts/rf_headroom_research_run.py:894-896,114` |
| K3 | 环境合同的固定域是 **40 dB**：teaching profile 40000、`rf-window-environment-v3`、acceptance FIXED_RF 40000、client 物理回环硬要求 40000、UI 采集参数 40000、board session 参数计划 40000 | `pynq_overlay/sw/rf_teaching_profile.py:17-19`；`rf_environment.py:19,21`；`scripts/rf_environment_acceptance.py:14-15,49`；`rf_status_client.py:839-848`；`ui_model.py:76,2144-2148`；`scripts/rf_bpsk_ui_board_session.py:210-213` |
| K4 | **46 dB 基线在当前代码里无法构造**：`EnvironmentBaseline` 要求 `rf_config == FIXED_RF`，否则 `baseline RF domain mismatch` | `rf_environment.py:89` |
| K5 | 基线 canonical sha = 8 字段 body 的规范化 JSON sha256（不含 `sha256` 本身）：`schema_version/ environment_version/ algorithm_version/ calibration_status/ rf_config/ spectrum_envelope(4096)/ thresholds(13 键)/ sources`；`sources` 只允许 `split='train'` | `rf_environment.py:119-127,152-160` |
| K6 | 40 dB 旧基线 provenance：20 条 TRAIN 源来自 2 个会话（s1-s00/s1-s01 各 10 条 normal）；VAL 会话 s02 由标定工具建组、不在基线里、须进 freeze 排除清单 | `rf_environment_baseline.json:4129-4251`；合同冻结 §1.5；`scripts/rf_environment_dataset.py:90-97` |
| K7 | 环境路径用 `/etc/rf-control.token` + 每次运行的 `--cable-confirmed`，没有 HEADROOM 那种 attempt/nonce 账本；会话帧数由 campaign 形状决定 | `scripts/rf_bpsk_ui_board_session.py:229-238`；`scripts/rf_environment_board_run.py:114-118`；`scripts/rf_environment_pilot.py:9-41` |
| K8 | 冻结前必须有一次**同一候选**上成功的 18 帧开发 pilot + 同 IQ 重放（known_correct=9、negative_false_clear=0） | `scripts/rf_environment_freeze.py:52-64` |
| K9 | 部署门：环境合同候选 + S1 closure + **逐候选 ARM 收据** + **回滚父证明**（HEADROOM 父路线只认 `INSTALLED_VERIFIED` 安装收据 + `headroom-install-acceptance-v1` + 同级 `readback-state-new.json`） | `scripts/rf_environment_deploy.py:84-180,38-81` |
| K10 | 接线确认可引用"未改变且仍在有效窗口内"的原确认（无需为换候选重新插拔）；一次性账本/窗口/尝试预算规则不变 | `docs/requirements/development-review-policy.md:47` |
| K11 | 冻结的校准排除清单目前只扫 `evidence/rf-teaching/s1` 与 `s2` 两个根 | `scripts/rf_environment_freeze.py:69-71` |
| K12 | 40 dB + 直连下 narrowband 帧真实 `CLIPPED`（2026-10-15 开发 pilot 第 2 帧即停）；削顶根因 `NOT_ESTABLISHED` | `evidence/rf-teaching/s2-development-pilot-2026-10-15/NOTES.md` §0/§3/§6 |

## 2. 46 dB 独立 TRAIN/VAL 最小矩阵

### 2.1 域定义（"46 dB 域"要写死的东西）

| 项 | 值 | 备注 |
|---|---|---|
| 采样域 | LO 80 MHz、Fs 60 MSPS、RX/TX BW 30 MHz、`phy_mode_1r1t=0`、`idelay_en=1`、`idelay_tap=29` | 与 40 dB 域相同 |
| **衰减** | `attenuation_mdB=46000`（TX1/TX2 读回同为 46000） | **唯一被移动的变量**，且是常量（K1） |
| 波形/接收 | BPSK 3 MSym/s、RECT、decim 1、4096 点、`reference_enabled=True` | 与合同一致 |
| 参考种子 | 会话轮换 `(1,37,73)`，`actual_scene_prbs7_seed=1` | 与合同一致 |
| 场景 | normal(`amplitude=60`)、narrowband(tone 40 @6 MHz)、pulse(start 600/width 80)、compound(amp 60+tone 15)、off、domain(2 MSym/s) | 声明域不变；`physical_max_code=100` |
| 窗口范围 | 4096 样点 / 60 MSPS = **68.27 µs**；不称连续监测 | 与合同一致 |

### 2.2 类别与每类帧数（最小矩阵）

沿用环境合同的"已知 / 未知 / 负例"结构，但**为 46 dB 域重新定义角色**：只有 normal 进 TRAIN 拟合；narrowband/pulse/compound/domain 是判读与负控，**不进基线**。

| 组 | 会话数 | 每会话类别 × 帧数 | 每会话帧 | 组帧数 | 角色 |
|---|---|---|---|---|---|
| **TRAIN** | **2**（独立会话；种子 1、37） | `normal ×10` + `off ×1` | 11 | **22** | 仅 normal 进 `fit_baseline`（包络/阈值来源）；off 为同会话能量地板负控 |
| **VAL** | **1**（独立会话；种子 73） | `normal ×10` + `off ×1` | 11 | **11** | 留出会话；只用于"阈值不越界"检查，不参与拟合 |
| 小计（TRAIN/VAL） | **3** | — | — | **33** | 基线 + 独立 VAL 判据 |
| **冻结前开发 pilot** | **3**（种子 1/37/73） | `normal/narrowband/pulse/compound/off/domain 各 1` | 6 | **18** | K8 的硬前置（同一候选）；覆盖全部六类标签与误报 |
| **合计** | **6** | — | — | **51** | — |

判据（逐类）：

| 类别 | TRAIN/VAL 中的判据 | pilot 中的判据（同一候选） |
|---|---|---|
| normal（已知） | TRAIN：10/10 帧可靠 BPSK（`reference_sync=true`、`ber=0`、`rms_codes>=16`）、无 `CLIPPED`/`INVALID`、rail=0；VAL：10/10 标 `CLEAR`（至少不得出现 `NARROWBAND/PULSE` 误判） | `CLEAR`（3 帧计入 known_correct=9） |
| narrowband（未知） | 不在 TRAIN/VAL 采集内（避免把"未知类"混进标定会话） | `NARROWBAND`（3 帧） |
| pulse（未知） | 同上 | `PULSE`（3 帧） |
| compound（负例） | 不在 TRAIN/VAL（由 off 承担同会话地板负控） | `UNKNOWN/INVALID`，**不得** `CLEAR` |
| off（负例） | 1/会话；**不得** `CLEAR` | 同上 |
| domain（负例，2 MSym/s） | 不在 TRAIN/VAL | `INVALID` 或非 `CLEAR` |

**严格更小变体（备选，需所有者拍板）**：TRAIN 只 1 个会话（10 normal + 1 off）+ VAL 1 个会话 ⇒ 2 会话 / 22 帧。代价：包络只来自单会话、失去会话多样性证据；历史唯一有先例的标定结构是 2 会话 × 10（K6）。**推荐按 2 会话执行**。

### 2.3 会话数与每会话初始化/尝试预算

**为什么矩阵必须定义在环境路径上**：HEADROOM 路径的预算是"1 初始化 + 恰 5 次尝试 / single_use 授权 / 衰减固定"，且固定顺序里**只有 1 个 normal 帧**。因此用 HEADROOM 拿 20 个 normal = **20 个会话 / 20 份 single_use 授权**，且拿到的帧在 v3 域是 `RF_DOMAIN_MISMATCH`（K1/K2/K3）。这不是"省一点人工"的问题，而是矩阵**不可能**在 HEADROOM 域内成立。

| 项 | HEADROOM 路径（现状，仅 46 dB 研究） | 46 dB 环境路径（迁移后，本矩阵采用） |
|---|---|---|
| 授权 | 每次派发 **新授权工件 + 新 nonce + 新 attempt_id**；`single_use=true` | 控制用 `/etc/rf-control.token`；**无 nonce**；每次运行 `--cable-confirmed` |
| 尝试预算 | `authorized_attempts` **必须恰为 5**；1 初始化/会话 | 无 attempt 预算；帧数由 campaign 形状决定（每会话最多 30 帧） |
| 衰减档位 | 固定 46000，**一次会话一档** | 固定 46000（迁移后），**不做档位扫描** |
| 接线确认 | runner 内 12 h 硬窗口 | 未改变且在有效窗口内可**引用原确认**（K10）；不要求为换候选插拔 |
| 每会话帧数 | 5（其中 normal 1） | TRAIN/VAL 11；pilot 6 |
| 会话初始化 | 1 次 `headroom.begin` | 1 次 `arm→init→configure→load` + RX 解调设置（1 次/会话） |

### 2.4 采集与判据（会话级）

| 阶段 | 动作 | 判据 |
|---|---|---|
| 会话前 | 只读预检 | `current=active-release` 等于目标候选；双服务 active/running；RF safe（未布防/TX 源未选/RX 未武装）；存储 0 故障；`/` 与 `/boot` rw |
| 会话中 | 每帧保存原始 IQ(16384 B) + metadata + environment sidecar + record；逐帧记录 requested/actual RF、标签、具名原因、rail/clip | 逐帧：`egress_safe=true`、IQ 摘要与行内一致、identity 五元组非空且不重复、环境标签在合法集合内 |
| 会话后 | 解除布防 + 关闭确认 + 独立只读读回 | `closed=true` 且 `disarm_ack=true`；读回 safe 且 release 未变 |
| 标定 | TRAIN 20 normal → `fit_baseline`；生成标定收据（逐帧、逐拒绝） | schema 通过 `from_dict`；`sha256` 重算相等；VAL 结果原样记录 |
| freeze 前置 | 同一候选 18 帧 pilot + 同 IQ 重放 | `passed=true`、`known_correct=9`、`negative_false_clear=0`、`board_state=VERIFIED_SAFE` |

### 2.5 停止条件（任一命中即停止整条 campaign，不重试、不补采、不调参）

1. 任一帧 `CLIPPED` / `INVALID_IQ` / `egress_safe!=true` / 下载缺件（env 工具的既有停止集合）；
2. 任一帧 identity 或 IQ 摘要重复；
3. 会话无法闭合（`close_unconfirmed` / `disarm_ack=false`）；
4. TRAIN 的 normal 帧出现任何不可靠 BPSK（`fit_baseline` 会拒绝）；
5. VAL 出现 `CLIPPED/INVALID` 或 normal 被判 `NARROWBAND/PULSE`（记录后停止，不得改阈值让它过）；
6. 任何"为了让数据好看"的阈值/基线/域参数改动 —— 改动 = 新版本 + 新基线 + 重跑全门；旧收据不继承；
7. release 身份漂移（`current` 变、服务重启不可解释、存储故障出现）→ 停止并只做只读读回。

### 2.6 证据落点

| 物 | 落点（建议） | 说明 |
|---|---|---|
| TRAIN 会话证据 | `evidence/rf-teaching/s2/46db-train-<date>/s0X/` | 放在 `s2` 根下，可被 K11 的排除扫描自动覆盖 |
| VAL 会话证据 | `evidence/rf-teaching/s2/46db-val-<date>/` | 同上 |
| pilot 证据 | `evidence/rf-teaching/s2/46db-pilot-<date>/` | 同上 |
| 标定收据 | 同 TRAIN 目录 | 逐帧标签/拒绝/输入摘要 + 拟合参数 |
| 新基线（产品件） | `pynq_overlay/sw/rf_environment_baseline.json` | 46 dB、新 sha；封存副本进证据目录 |
| 迁移记录 | 本文件 + `evidence/rf-teaching/s2-headroom-46db-migration-2026-10-17/` | 计划与来源，不是执行收据 |

若团队坚持把证据放在 `evidence/rf-teaching/s2-46db-…`（不在 `s2` 根下），则**必须**同步扩展 `scripts/rf_environment_freeze.py:69-71` 的扫描根，否则新帧不会进入 `calibration_iq_sha256` 排除清单（K11）；这是"二选一"的必做项，不是可选项。

### 2.7 现有研究帧的地位：只作开发诊断，不作独立 TEST

现有 5 帧（attempt4）**不能**充当独立 TEST，也**不能**充当 TRAIN/VAL：

1. 域不一致：在 v3 域下回放判 `INVALID`/`RF_DOMAIN_MISMATCH`（rf_config 46000 ≠ 40000）；
2. 证据形态不同：没有环境合同的逐帧 sidecar（`environment_file`、`display_environment`、`scenario_declaration`、record、handoff identity），`validate_frame` 的字段集对它们不成立；
3. 抽样不足：每场景 n=1、单会话、顺序固定（K2/K6），无法给出帧间/会话间方差；
4. 身份不可替代：正式 TEST 必须来自**独立会话**且与 TRAIN/VAL 的会话/源/IQ 摘要都不重叠；同一批 research 帧重复计数等于自证；
5. 对齐审查已明确：归档帧只能作**明确标注的事后诊断**，不转作独立 TEST。

允许且有用的用途：在 46 dB 域代码就绪后，把这 5 帧（尤其 attempt-2 normal）作为**POST_HOC 开发诊断**复算特征/标签，并可作为 M2 的 bootstrap 基线来源（标注 provisional，见 §4.1）。

## 3. 环境基线（46 dB）

### 3.1 46 dB 基线必须包含什么

| 字段 | 46 dB 版本的要求 | 约束来源 |
|---|---|---|
| `schema_version` | 仍为 1（除非 JSON schema 本身变更） | `rf_environment.py:20,82` |
| `environment_version` | **新字面量**（如 `rf-window-environment-v4`），必须等于 `rf_environment.VERSION`、freeze 声明与 acceptance 校验的同一字面量 | K3/K5 |
| `algorithm_version` | `bpsk-rect-snapshot-v2`（本次不计划改接收器；若改则同步） | `rf_environment.py:15,81` |
| `calibration_status` | 拟合时 `development`（当前工作流不使用 `frozen`） | `rf_environment.py:74,107` |
| `rf_config` | 8 对固定值，`attenuation_mdB=46000`，必须**等于** `FIXED_RF` | K4 |
| `spectrum_envelope` | 4096 个有限值 ∈[0,1]；由 TRAIN normal 帧的归一化谱逐 bin 取 max（含 ±8 bin 滚动 max） | `rf_environment.py:84-88,247-248` |
| `thresholds` | 恰 13 键、正有限、满足上界（`tone_concentration<=1` 等）；由 `fit_baseline` 统计生成 | `rf_environment.py:91-106,249-255` |
| `sources` | 非空；每项 `{iq_sha256(64hex), session_id, source_id, split='train'}`；**只允许 train**；IQ 摘要必须等于实际帧字节 | `rf_environment.py:109-114,152-160,235-236` |
| `sha256` | **canonical sha**：对上述 8 字段 body 做 `json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)` 后 SHA-256；`sha256` 字段本身不参与 | K5 |

**canonical sha 的可复算写法**（与代码等价）：先构造 body（键序固定、数值原样、`rf_config` 为 dict、`spectrum_envelope` 为 4096 元素 list、`thresholds` 为 dict、`sources` 为 list of dict），再取规范化 JSON 字节的 sha256；写回文件时附 `sha256`，读回用 `EnvironmentBaseline.from_dict` 校验（`:132-149`）。

### 3.2 与 40 dB 旧基线的关系（不得套用）

| 维度 | 40 dB 旧基线（现行） | 46 dB 新基线 | 后果 |
|---|---|---|---|
| `environment_version` | `rf-window-environment-v3` | 新字面量 | 旧收据/旧 freeze **不继承** |
| `rf_config.attenuation_mdB` | 40000 | 46000 | 类不可比较：包络/阈值必须重拟合 |
| `spectrum_envelope` | S1 两个会话的 max 包络 | 46 dB TRAIN 两个会话的 max 包络 | 数值全不同 |
| `thresholds` | 现行 13 值 | 由 46 dB TRAIN 重新生成 | 不得手调"让结果好看" |
| `sources` | s1-s00/s1-s01 ×10 | 46 dB TRAIN 会话 ×10 | 不得把 40 dB IQ 声称为 46 dB 源 |
| `sha256` | `64a00c6a2975252ff2c0fa055318b06912084b07dc5db759b784e95b131501a3`（文件 sha `721baa76…`） | 新增 | **禁止**用旧 sha 宣布 46 dB 通过 |

规则（与合同冻结 §2.3/§6 一致）：旧 40 dB 基线只对 40 dB 产物有效；46 dB 结论必须有自己的基线、自己的阈值、自己的收据；失败不得改写成通过；旧 40 dB pilot/帧不得充当 46 dB 标定源。

### 3.3 需要重签的冻结件与字段

| # | 文件 / 字段 | 动作 | 判据 |
|---|---|---|---|
| 1 | `pynq_overlay/sw/rf_teaching_profile.py:19` `attenuation_mdB` | 40000 → 46000（域常量唯一真源；`FIXED_RF` 由它派生） | 它是 `rf_environment.FIXED_RF` 的唯一来源；改后 K4 的构造失败消失 |
| 2 | `pynq_overlay/sw/rf_environment.py:19` `VERSION` | 新域版本字面量 | 与 baseline/freeze/acceptance/UI 门同步 |
| 3 | `pynq_overlay/sw/rf_environment_baseline.json` | 新文件（46 dB 拟合结果） | §3.1 全字段 + sha 自校验 |
| 4 | `pynq_overlay/sw/rf_status_client.py:839-848` | 物理回环放行值 40000 → 46000（含 docstring） | 拒绝语义不放宽：非 46000 仍必须拒 |
| 5 | `pynq_overlay/sw/ui_model.py:76,2144-2148` + `:1695` | 采集参数 46000；环境版本显示门接受新版本 | 面板不因版本字面量而"环境不可用" |
| 6 | `scripts/rf_environment_acceptance.py:14-15,49` | FIXED_RF 46000；版本字面量 | 与产品侧严格相等，否则 freeze 拒绝 |
| 7 | `scripts/rf_environment_dataset.py:58-59` + 标定函数 | 期望域 46000；把只服务 S1 的 `calibrate_s1` 推广/copy 到 46 dB 会话形状（**不改** split 校验语义） | 30 帧硬编码 `:96-97` 不得复用到 46 dB 会话 |
| 8 | `scripts/rf_bpsk_ui_board_session.py:210-213` | 会话参数计划 46000 | 与 client 放行值一致 |
| 9 | freeze 声明（`s2-formal-freeze-v1`） | 全量重出：`expected_release`、`software`(6 项)、`environment_version`、`baseline_sha256`、`fixed_rf`、`tool_hashes`(`FORMAL_TOOLS` 12 文件)、`calibration_iq_sha256`（含旧 40 dB IQ + 新 46 dB VAL/pilot 帧）、`scene_declarations`、`rx_config`、`reference_seeds`、`expected_frames=108` | `check_candidate` 全等（`scripts/rf_environment_freeze.py:27-41`） |
| 10 | N1–N12 离线负控收据 | 域变更后**重跑重签**（纯离线、0 会话） | 正式入口只接受 `(12,12,0,0)` |
| 11 | 测试面 | HEADROOM 的"teaching 40000 ≠ research 46000"断言与一批期待 40000 的用例需同步改写（见 §4.0 B-2） | 两套 Python 套件全绿，且**不得**放宽既有拒绝语义 |
| 12 | S1 期回环工具（`rf_bpsk_board_pilot.py`、`rf_bpsk_run_pilot.py`、`rf_bpsk_ui_board_run.py`、`rf_capture_timing_probe.py` 的 40000） | 更新或明确标为"旧 40 dB 域专用、不再服务本域" | 它们不在 46 dB 环境路径上；不得把它们的通过当作 46 dB 证据 |

## 4. 版本迁移最小步骤

### 4.0 两个真实前置阻塞 + 两个决策点

**B-1 回滚父证明缺失（板/工具侧阻塞）**。部署门要求"回滚父 = 板卡**现在**运行的发布"有完整证明：HEADROOM 父路线只接受 `outcome=INSTALLED_VERIFIED` 的安装收据 + `headroom-install-acceptance-v1` 验收收据 + 同级 `readback-state-new.json`（K9）。e66197ed 的最近一次完整安装是**可控失败演练**（注入 exit 42 后回滚，`evidence/rf-teaching/codex-trial-closeout-2026-10-17/trial.json`），随后由**启用编排**切成 current（`docs/reports/2026-10-17-candidate2-activation.md`），两者都不产生上述三件。因此在新候选 `prepare` 前必须二选一：

- (a) 扩展父路线，使"启用编排证据链"（activation 报告 + post-state 读回 + 本机独立读回）成为**受审**的第三条父证明（工具改动 + 新测试 + 独立复审）；
- (b) 先把板卡切回**证明链完整**的发布（例如 `399b300e…`：其 `evidence/rf-teaching/s2-environment-live-install-2026-10-15/install-live/` 有 `installed=true`/`board_state=VERIFIED_SAFE_NEW_RELEASE` 与 `post-state.json`，可走环境父路线），再以它为父安装新候选。代价：这次切换本身是一次板卡写入，需按启用编排的证据口径（预检 + 单次派发 + 独立只读读回 + 收据）记录，且会把板上能力从 46 dB 研究通道暂时降回 40 dB 环境通道。

不许的做法：绕过 `verify_gates`、手工补收据、把 activation 产物改名冒充 `INSTALLED_VERIFIED`。

**B-2 域常量移动的连带面（产品/合同侧阻塞）**。把 teaching/environment 衰减常量移到 46000，会与 HEADROOM 合同"teaching=40000 与 research=46000 必须不同"的既有断言（`scripts/tests/test_rf_headroom.py:1597-1607,1753`）以及一批期待 40000 的客户端/视图用例冲突。必须显式决策：

- (a) **推荐**：一次性把域常量移到 46 dB，并把 HEADROOM 研究合同重新表述为"与研究域同档"（合同文字 + 测试 + 一次增量复审）；研究通道继续可用，只是不再靠"档位不同"与教学区分；
- (b) 保留 teaching=40000，仅给环境域引入第二套声明常量与采集放行集合：改动面更大（UI/worker/client 需要域选择），且更容易出现"两个真源"漂移。

任一选项都必须保持"非放行档位仍被拒"的语义（`rf_status_client` 的拒绝测试不得删除）。

**决策点 D-1**：证据根（K11）——把新帧放 `evidence/rf-teaching/s2/` 下（零工具改动），或扩展 freeze 的扫描根（改动 + 测试）。二者必选其一。

**决策点 D-2**：TRAIN 会话数 2（推荐，K6 有先例）还是 1（22 帧的最小变体）。

### 4.1 最小步骤（M0–M12；每步都带前置与判据）

| 步 | 阶段 | 动作 | 前置 | 判据 | 复用/新建 |
|---|---|---|---|---|---|
| **M0** | 离线 | 并入窄带失步诊断结论（对齐审查 §4.1；只读 IQ + 源声明 + 接收器中间量），给出假设与单变量，或明确 `NOT_ESTABLISHED` | 既有 narrowband IQ 与诊断工具 | 结论写进迁移记录；不挑成功子集；不引入新门 | 复用既有分析法 |
| **M1** | 离线（产品） | 域常量 + 版本迁移：§3.3 第 1/2/4/5/6/7/8 项 + 测试同步（含 B-2 决策） | M0 结论（决定是否需要额外声明） | `pynq_overlay/sw/tests` 与 `scripts/tests` 全绿；无阈值/场景偷调；46 dB 基线可构造 | 改产品；新建测试表述 |
| **M2** | 离线 | **bootstrap 46 dB 基线**：用已归档的唯一真实 46 dB normal 帧（attempt-2，`d435eea7…`，回放 0/138 同步）拟合 `calibration_status='development'`、**provisional、n=1** 的基线，仅用于"让 46 dB 候选能加载环境引擎" | M1；attempt4 证据（只读） | schema 通过 `from_dict`、sha 重算相等、sources 指向真实帧；文档与文件都标注 provisional；实现时须用回放路径重建 RxIqFrame/Envelope 并保证 identity 字段与读回一致（可行性待实现时验证） | 复用 `fit_baseline`；新建 bootstrap 记录 |
| **M3** | 离线 | 构建**候选 B**：`rf_environment_candidate.py --parent c3bf915c… --daemon <1.0.4/minor6 构建件> --contract cnn-bpsk-environment-v1` | M2（repo 里已有 provisional 基线）；native 构建收据（`bbfd0409…`） | `verify_release(require_software=True)` + `verify_panel_imports`；candidate-receipt `physical_qualification=NOT_RUN` | 复用 bit/HWH/model/native；新建候选 |
| **M4** | 板（无 RF）+ 离线 | 候选 B 的 ARM 资格（隔离 /tmp，无 RF）→ deploy `prepare` → 安装 B → 独立读回 | **B-1 已解决**；B-2/M1 已落地；S1 closure 收据（复用） | ARM 收据绑定 B 的 release/software/baseline 且 `p95≤300`、parity 空、native CNN；bundle `verify_bundle`；install `installed=true`/`VERIFIED_SAFE_NEW_RELEASE`；读回 `current=active-release=B` | 新建 ARM/安装；复用工具链与 S1 closure |
| **M5** | 物理 | **46 dB TRAIN**：2 会话 ×（10 normal + 1 off），按 §2.2/§2.4/§2.5 | M4（板上是 B）；接线确认（未变可引用，K10） | 20/20 normal 满足可靠 BPSK；off 不 CLEAR；无触发停止条件；每会话独立预检/读回 | 新建物理帧 |
| **M6** | 物理 | **46 dB VAL**：1 会话 ×（10 normal + 1 off） | M5 完成且无停止 | 与 M5 相同口径 | 新建物理帧 |
| **M7** | 离线 | 用 TRAIN 20 normal 拟合**最终 46 dB 基线**（`development`）；VAL 10 normal 复算并生成标定收据 | M5/M6 证据齐备 | §3.1 全字段；sha 自校验；VAL 原样记录（不达标即阻止后续，不得换帧） | 复用 `fit_baseline`/`validate_splits`；新建标定收据 |
| **M8** | 离线 | 构建**候选 C**（同一代码 + 新基线文件 ⇒ 新 release id） | M7 通过 | 同 M3 | 新建候选 |
| **M9** | 板（无 RF） | 候选 C 的 ARM 资格（新收据） | M8 | 同 M4 的 ARM 判据；`baseline_sha256` 等于 C 的文件 | 新建 ARM 收据 |
| **M10** | 物理 | **开发 pilot 18 帧**（3 会话 × 6 类）+ 同 IQ 重放 | M9；板上是 C | `passed=true`、`known_correct=9`、`negative_false_clear=0`、`board_state=VERIFIED_SAFE`；失败帧保留、不重试 | 复用 pilot 运行器；新建物理帧 |
| **M11** | 离线 | 重跑 N1–N12；出 freeze 声明；deploy `prepare`（父 = 当时 current，即 B） | M10；D-1 已决 | `check_candidate` 全等；`offline_rejection=(12,12,0,0)`；bundle `verify_bundle` | 新建 freeze/负控收据；复用工具链 |
| **M12** | 板（无 RF） | 安装 C + 独立读回；此后才谈正式 108+12 的授权/预检/单次派发 | M11 | 同 M4 的安装判据 | 新建安装；正式 108 本文件只给预算（§5） |

### 4.2 复用 / 新建 / 不得复用（一句话版）

- **可复用**：PL `bit`/`hwh`（`d5bfe5d9…`/`ed8d011a…`）、native `rf_daemon` 1.0.4/minor6（`bbfd0409…`）、CNN 模型与 `rf_cnn_conv.so`（`828425fe…`）、S1 closure 收据（`evidence/rf-teaching/s1/closure-2026-10-04/receipt.json`，`schema=s1-closure-v1`、`release_id=c3bf915c…`）、ARM/deploy/freeze/install 工具链、`fit_baseline`/`validate_splits`/回放工具。
- **必须新建**：域常量与版本、46 dB 基线（bootstrap + 最终）、候选 B 与 C、2 份 ARM 收据、2 个安装包、N1–N12 收据、freeze 声明、46 dB 标定入口、父证明路线（B-1）。
- **不得复用**：40 dB 基线/阈值/收据；`399b300e…` 的 ARM 收据（release/software 不等）；旧 40 dB pilot/帧作 46 dB 标定；旧授权/nonce/attempt_id；旧 freeze 声明（`tool_hashes`/`software`/`baseline_sha256` 必变）。

### 4.3 本文件不做什么

不实施 M0–M12；不新增 6 dB 硬门、不做 P1dB/外部仪器、不做多档扫描、不改 AGC/增益观测面；不生成/消耗任何授权或 nonce；不连板、不安装、不采集。

## 5. 下一次物理验证预算（46 dB 域）

> 前提：§4 的 M0–M4 已完成（域迁移落地、候选 B 在板上、父证明已解决）。本预算是**第一次物理动作**（M5 的 TRAIN 会话）及其余 TRAIN/VAL/pilot 的总量，不含正式 108 帧。

### 5.1 假设

- **H1（TRAIN 域合格）**：在 46 dB、TX1→RX1 直连、BPSK 3 MSym/s RECT 下，normal 帧 **10/10** 可靠（`reference_sync=true`、`ber=0`、`rms_codes>=16`、无 rail/`CLIPPED`），且一个 11 帧会话能完整跑完不触发 §2.5。若 H1 不成立，46 dB 域**不合格**并停止迁移（保留失败帧与拒绝原因，不调参重试）。
- **H2（VAL 不越界）**：用 TRAIN 拟合的阈值，VAL 会话 10 帧 normal 不出现 `CLIPPED/INVALID` 或 `NARROWBAND/PULSE` 误判；off 帧不得 `CLEAR`。
- **H3（负例可判否）**：pilot 中 compound/off/domain 三类 9 帧**零** `CLEAR`（与合同的 `false_clear==0` 同口径）。

### 5.2 变量（单变量纪律）

| 类型 | 内容 |
|---|---|
| **自变量** | **会话**（独立会话 = 独立 capture identity）；**不是**衰减（固定 46000）、不是场景参数、不是阈值、不是 AGC 参数 |
| 控制量 | 场景声明、种子轮换 (1,37,73)、`actual_scene_prbs7_seed=1`、4096 点、参考使能、settle 等待沿用现行实现 |
| 因变量 | 逐帧标签 + 具名原因 + rail/clip + 可靠 BPSK 条件；会话级 `stop_cause`；VAL 的帧间一致性 |

### 5.3 样本数

| 组 | 会话 | 帧 | 其中 normal | 说明 |
|---|---|---|---|---|
| TRAIN | 2 | 22 | 20 | 进拟合 |
| VAL | 1 | 11 | 10 | 留出 |
| pilot | 3 | 18 | 3 | 六类各 3 |
| 合计 | **6** | **51** | 33 | — |

### 5.4 时间 / 授权预算

| 项 | 实测锚点（可复算） | 本次预算（**估计已标注**） |
|---|---|---|
| 单帧物理窗口 | 4096/60 MSPS = **68.27 µs** | 不变 |
| HEADROOM 5 帧会话总耗时 | **13.330 s**（attempt4 派发） | 不适用于环境路径 |
| 环境 pilot 实测 | **18.5 s**（会话 0 + 2 帧后因 `CLIPPED` 停止） | — |
| 环境运行超时门 | 180 s（18 帧）/ 600 s（108 帧） | TRAIN/VAL 3 会话 ≤180–300 s；pilot 3 会话 ≤180 s |
| 环境会话单次 | — | 11 帧会话 **≈30–60 s**（外推估计：2 帧/18.5 s 的会话开销 + 每帧往返） |
| ARM 资格（隔离 /tmp，无 RF） | **35.6 s**（399b，2026-10-15） | ≈40 s × 2 次 |
| 安装 + 读回 | **41.2 s**（399b，2026-10-15） | ≈45 s × 2 次；另每次安装前后各 1 次只读预检/读回 |
| 授权 | 环境路径**无 nonce**；HEADROOM 路径 1 份 single_use/会话 | **0 份 single_use 授权**；每次运行 1 次 `--cable-confirmed`（未变且在窗口内可引用，K10） |
| 板卡接触次数 | — | 2×ARM + 2×安装 + 6×物理会话 + ≥4×只读读回 + 每会话预检（**估计**） |
| RF 实际发射时间 | — | 51 × 68.27 µs ≈ **3.5 ms**（其余为会话开销与往返） |
| 墙钟（物理段） | — | **≈0.5 工作班**（含接线确认/预检/读回/证据下载；估计） |
| 墙钟（离线段：M0–M3、M7–M8、M11） | — | **≈1–2 工作日**（域迁移 + 测试 + 两次候选 + 标定 + freeze + 负控；估计） |

### 5.5 窄带诊断结论并入接口（给 M0/M5 的接口）

M0 的结论以一个显式字段并入本预算，取值与对应动作：

| `narrowband_cause` | 并入动作 | 允许/禁止 |
|---|---|---|
| `WINDOW_SETTLE`（窗口/稳定等待相关） | 矩阵不变，但 pilot 与 TRAIN 会话各**预声明** 1 个"同参数重复 narrowband 帧"以量化重复性 | 不改判据；不新增门 |
| `AGC_STATE`（窗口内增益状态相关） | 域定义登记 `window_gain_state=NOT_OBSERVED`，结论按帧陈述 | 不实现窗口内增益观测（属产品变更，见 P1，本文件不实施） |
| `FORMING_LOAD`（成形/加载相关） | 会话前增加 1 条"加载读回相等"断言（既有字段，不加门） | 不改场景声明 |
| `NOT_ESTABLISHED` | 矩阵与判据不变，narrowband 的 `CARRIER_NOT_SYNCED` 原样报告 | 不得用"无 rail"替代"可用" |

无论哪种取值：**不引入 6 dB 硬门、不做档位扫描、不上外部仪器**。

### 5.6 预算内不做的事

不改衰减（不做 D2 阶梯）、不改 AGC/MGC、不改帧格式、不扩授权模型、不做 P1dB/外部仪器、不把 D1 码域余量当验收门；不重跑已消耗 nonce 的研究会话。

## 6. 未做 / 未证明（NOT_RUN）

- 本文件是**计划**：M0–M12 全部 `NOT_RUN`；无任何新物理帧、无板卡接触、无授权/nonce 消耗、无安装/切换发布、无产品代码/测试/封存件改动、无 `git commit`。
- 未证明：46 dB 域在 normal/narrowband/pulse/compound/off/domain 上的标签正确性；窄带失步原因；任何 dB 余量；TRAIN 会话的真实时长/失败率（§5.4 为估计）；bootstrap 基线（n=1）的可用性（它只是"让引擎能加载"的开发件）。
- 明确排除：6 dB 硬门、P1dB/外部仪器、多档扫描、P1–P5 产品变更的实施、正式 108+12 的执行、真人验收、S3/S4。

## 7. 来源

逐条 `path:line` 与文件 sha256 见 `evidence/rf-teaching/s2-headroom-46db-migration-2026-10-17/refs.json`（34 个文件、32 条 claim）；核对记录与未证明项见同目录 `NOTES.md`。

## 更正（2026-10-17 19:0x）：执行顺序重排

原文 M10 要求「板上是 C 并完成 C 的 18 帧 pilot」，但安装 C 被放在 M12，且 M11 依赖 M10 ⇒ **原表不可执行**。代码依据：`scripts/rf_environment_deploy.py:180` 的 `prepare` 只校验候选、S1 closure、对应 ARM 资格与回退父证明，**不要求 freeze 或 pilot**；要求同候选成功 pilot 的是 `scripts/rf_environment_freeze.py:52-64`。

**修正后的顺序**：

```text
候选 B（开发临时基线）→ ARM / prepare / 安装 / 读回
→ 独立 TRAIN / VAL → 最终基线 → 候选 C
→ ARM / prepare / 安装 C / 读回
→ 在 C 上做 18 帧 pilot + 同 IQ 重放
→ 离线负控与正式 freeze → 再申请正式 TEST
```

**预算口径**：原文「6 会话 / 51 帧、2 候选 / 2 ARM / 2 安装」是**拟议预算，不是已完成结果**，在顺序修正与实现验证完成前不得称为已证实的最短路径；51 帧**尚未采集**。

## 实现进展（2026-10-17 20:1x，子代理离线作业）

本节**只追加**，不改动 §0–§7 与上方“更正”小节的任何历史结论；它只记录实现走到哪一步、实测到什么、还差什么。证据：`evidence/rf-teaching/s2-46db-candidate-b-2026-10-17/`（`NOTES.md` / `work/` / `logs/` / `candidate/` / `receipt.json`）。HEAD `46f302c`；本轮零板卡、零 RF、零授权、零 git commit、零产品树写入。

### A. 已落地（在库代码）与本轮离线复验

| 项 | 状态 | 依据 |
|---|---|---|
| 域真源（M1） | 已落地 | `rf_teaching_profile.ATTENUATION_MDB=46000`、`rf_environment.VERSION=rf-window-environment-v4`、`FIXED_RF` 由 teaching 派生 |
| 父证明路线（B-1） | **可消费** | 生产函数 `verify_installed_parent_via_activation_evidence(e66197ed, evidence/rf-teaching/s2-headroom-activate-2026-10-17)` 离线 **PASS**，绑定 10 个证据文件；未生成/未改名任何收据 |
| S1 closure 子句 | 全真 | `verify_gates:94-102` 逐式复算（schema/`release_id`/四项 true/verbatim/两收据 sha） |
| ARM 输入 | 存在 | `evidence/rf-teaching/s1/board-pilot-2026-10-04-v1/s02-on-00.iq`（16384 B / `d8fde529…`） |

### B. M3：候选 B 已建成（本轮新产出）

| 项 | 值 |
|---|---|
| release id | **`ad05576aff6b9bf2b6b495347a6269ec`**（合同 `cnn-bpsk-environment-v1`，**44 项**软件摘要——环境合同的项数，不是 HEADROOM 的 46） |
| 复用（零重建） | bit `d5bfe5d9…`、HWH `ed8d011a…`、daemon 1.0.4/minor6 `bbfd0409…`（**与板上已装件逐字节相同**；`verify_observer_build` 的 58 个源摘要与当前 `rf_overlay/sw/board_app` 全等）、模型 `f76f2fc0…` |
| 门 | `verify_release(require_software=True)` PASS、`verify_panel_imports` PASS、合同读回一致；`candidate-receipt.json` 记 `installed=false`/`physical_qualification=NOT_RUN` |
| 规模 | 51 个扁平文件 / 6.72 MB（与 399b300e 同形） |
| 差集 vs 已装 `e66197ed` | 变更 **4**（`rf_environment.py`、`rf_status_client.py`、`rf_teaching_profile.py`、`ui_model.py` = 迁移改动的共享模块）、仅已装件 **2**（`rf_headroom.py`、`rf_headroom_replay.py`）、相同 **40** |

### C. 阻塞（本轮证明）：候选 B 携带的基线仍是历史 40 dB/v3 ⇒ M4 当前不可执行

四个离线探针（`work/blocker-proof.json`）：**A** 候选自己的加载器拒绝随包基线（`unsupported frozen baseline version`）；**B** 生产 ARM 入口的主机前置检查对候选 B **exit 1**，只有 `environment.available=false`（model/demod/teaching 均 true）；**C** 同一探针对迁移前的 399b300e **exit 0**（正对照，`baseline_sha256=64a00c6a…`）；**D** provisional n=1 的 **body** 文件经同一加载器 **LOADED**（`6b697e0e… / v4 / 46000`）。

原因在库里是**有意**的：`pynq_overlay/sw/tests/test_rf_environment_local_event.py:74-83` 断言“随包基线必须被当前域拒绝”，`test_rf_environment_release.py:160-173` 注明“产品基线要由真实帧拟合，46 dB 迁移尚未产生”。后果：`rf_environment_arm_check.py:38-39` 必停在 statuses；`rf_environment_deploy.verify_gates:129-149` 的 `baseline_sha256` 子句不可能满足 ⇒ `prepare` 必拒。**§4.1 M3 的判据（verify_release + verify_panel_imports）已满足，但 M4 的前置不成立。**

最小补救（需所有者授权 + 一次增量复审，本轮 **NOT_RUN**）：① 把 `provisional-baseline.body.json`（`e1ed6654…`）复制到 `pynq_overlay/sw/rf_environment_baseline.json`（wrapper 会被 `from_dict` 拒：字段集必须恰为 8+1 键）；② 重建候选（新 identity；按生产 `release_identity` 推导应为 `e4dae554182e8c9f5f963c1d5040913a`，**该值不是已构建件、不得写入任何收据**）；③ 同步改上述两处测试期望与 `scripts/rf_environment_offline_negatives.py:62` 的输入语义。**在①完成前不要为 M4 申请板时**（ARM 必在 statuses 处失败，白花一次臂预算）。

### D. M4–M6 的命令与预算（本轮**未执行**；逐字命令见证据 `NOTES.md` §5–§7）

| 步 | 入口（要点） | 预算（锚点） | 判据 | 失败处置 |
|---|---|---|---|---|
| M4a ARM | `scripts/rf_environment_run_arm_check.py --candidate <新候选> --pilot evidence/rf-teaching/s1/board-pilot-2026-10-04-v1 --output …/arm-run` | **≈40 s**（实测 35.6 s，2026-10-15；工具内 120 s 超时）；上传 51 文件 6.72 MB | `passed`、`exit_code=0`、`parity_mismatches=[]`、`native_cnn`、`iterations=100`、`0<p95<=300`、`baseline_sha256` = 候选基线 | 不重试、不改门限；`/tmp` 清理 + 只读读回；**不得**改 runner（`runner_sha256` 绑死） |
| M4b prepare | `scripts/rf_environment_deploy.py prepare … --parent-candidate <e66197ed 目录> --parent-activation evidence/rf-teaching/s2-headroom-activate-2026-10-17` | **≈1–3 s**（纯主机） | `bundle-receipt.json` + `verify_bundle` 重算 gate 全等（`verify_bundle` 无 CLI，用一行 `python -c` 等价入口） | 拒即停；不改父证明、不补收据 |
| M4c 试部署 | 板端 `systemd-run` 调 `trial-install.sh <stage> <new> <parent> <tgz sha> 120 python3 -B acceptance.py …`（暂存目录必须在 `/tmp/rf-teaching-install-*`） | **≈150 s**（2026-10-17 实测同口径作业；验收上限 120 s ≤ wrapper 的 1–300 s） | `TRIAL_ACCEPTED` + `acceptance-report.ok=true`（四项：发布身份/合同/模块摘要/域常量 46000/归档帧的解调与环境**分键记录**）+ 安全态读回 | **只有事务内回退**（wrapper 的 `finish_trial` → `TRIAL_ROLLED_BACK`）；post-accept 回退无已复审命令（`rf_teaching_install.sh:12,14`），需新复审 |
| M5/M6 TRAIN/VAL | `plan` → 6 次会话 → `fit --set …` → `load-check`；证据必须在 `evidence/rf-teaching/s2/46db-{train,val,pilot}-<date>/` | 单会话 **≈30–60 s**，3 会话 ≤180–300 s；RF 33 帧 ≈ **2.3 ms**；**0 份 single_use 授权** | TRAIN 20/20 reliable、VAL 10/10 `CLEAR`、off 永不 `CLEAR`、`verdict.passed=true` 才出基线 | §2.5 七条停止条件，任一命中即停，不重试/不补采/不调参 |

**TRAIN/VAL 的两个工具缺口（本轮定位，未实现）**：① 板端**不存在 11 帧会话形状**（`rf_environment_pilot.py` 固定 3×6、`rf_environment_board_session.py` 只给 18/108）；② **不存在**把会话下载物转成 `set.json` 的适配器（M2 记录已自认）。两者都是新代码，需各自的测试与复审，**不能**用夹具结果顶替物理证据。

### E. 未做 / 未证明（NOT_RUN）

ARM、prepare（真实 bundle）、verify_bundle、安装、试部署、板端验收、TRAIN/VAL、pilot、正式 108+12、真人全部 **NOT_RUN**；**51 帧仍未采集（0 帧）**；未生成任何 ARM/安装收据；未证明 46 dB 域六类场景的标签正确性、窄带失步的物理归因、任何 dB 余量、provisional n=1 基线的可用性（它只解决“能加载”）、新父证明入口在真实 `prepare` 上的端到端、post-accept 回退路径、N1–N12 与 freeze 重签（M11）。

### F. 候选命名更正：规范里的候选 B（`ad05576a…`）≠ M2 引导候选（`e4dae554…`）

本节**只修正命名与绑定口径**，不改动 §0–§7 与 A–E 的任何结论（沿用上面“本节只追加”的规则）；依据 `evidence/rf-teaching/s2-46db-review-fixes-delta-2026-10-17/` 的 delta 复核 F8-3 与本次 `F8-CLOSURE.md`。

| 名称 | release id | 携带的环境基线 | 位置 |
|---|---|---|---|
| **候选 B**（§B/M3 的候选 B；也叫“旧候选 B”） | **`ad05576aff6b9bf2b6b495347a6269ec`** | 历史 **40 dB/v3** 基线字节 `721baa76…` | `evidence/rf-teaching/s2-46db-candidate-b-2026-10-17/candidate/releases/ad05576af…/` |
| **M2 引导候选**（M2 用生产入口**重建**的候选；**不是**本规范的“候选 B”） | **`e4dae554182e8c9f5f963c1d5040913a`** | **provisional 46 dB/v4** 基线，文件字节 `ecee5ca5…`、canonical `6b697e0e…` | `evidence/rf-teaching/s2-46db-m2-bootstrap-2026-10-17/candidate/releases/e4dae554…/` |

两点与上面 §C 直接相关的更正：

1. §C 末段“最小补救 ②”当时写“按生产 `release_identity` 推导应为 `e4dae554…`，**该值不是已构建件、不得写入任何收据**”。该推导值随后**已被 M2 实际构建**（实测 release id 与推导值相同），并写入 M2 自己的收据（`work/m2-receipt.json`、`candidate/candidate-receipt.json`）——所以“不是已构建件”只对**写那句话的时刻**成立，现在按上表读。M2 **未**改本规范 §B/§C 的历史文字。
2. 任何“绑定 `pynq_overlay/sw/rf_environment_baseline.json`（CRLF，`e1ed6654…`）的 manifest”都指 **M2 引导候选 `e4dae554…` 的 `merged_release_manifest.json`**（`software.rf_environment_baseline.json`）与 `work/m2-receipt.json`，**不是**候选 B `ad05576a…` 的 manifest（后者绑定的是 `721baa76…`）。

F8 的证据口径（提交范围 `git diff --check HEAD~1 HEAD` = **rc=2 / 40,716 处 / 72 文件**及其二分类例外清单）见 `evidence/rf-teaching/s2-46db-review-fixes-delta-2026-10-17/F8-CLOSURE.md`。

## 工具缺口闭合（2026-10-17 22:3x，子代理离线作业）

本节**只追加**，不改动 §0–§7、上方“更正”与“实现进展”A–F 的任何历史结论；只记录两个工具缺口如何被填上、离线实测到什么、还差什么。证据：`evidence/rf-teaching/s2-46db-tools-closure-2026-10-17/`（`NOTES.md` / `work/` / `logs/`）。本轮零板卡、零 RF、零授权/nonce、零 `git commit`、零产品树/封存件改动。**51 帧仍未采集（0 帧）**。

### G.1 新增入口与职责

| 文件 | 职责 |
|---|---|
| `scripts/rf_environment_trainval_session.py` | 一次会话 = **10 normal + 1 off** 的 11 帧会话入口：`plan` / `fixture` / `check` / `collect`（真实路线，必拒为 NOT_RUN） |
| `scripts/rf_environment_trainval_adapter.py` | 板端下载物（3 个会话目录 + 逐帧文件 + 收据）→ 标定入口消费的 `set.json`（`adapt`） |
| `scripts/rf_environment_calibration.py`（增量） | `fit --require-physical`：拒绝 provenance 为离线夹具的 set；正式打包路线不得消费合成 TRAIN/VAL |

两者只做离线文件处理：**不 import 板卡客户端、不建 socket、不部署、不初始化 RF、不发射、不采集、不消耗授权/nonce**。

### G.2 11 帧会话形状与判据

会话 id / 种子 / split 由矩阵唯一决定：`train-01`(seed 1)、`train-02`(seed 37)、`val-01`(seed 73)；每会话帧序固定为 `normal-01 … normal-10`、`off-01`（共 11 帧，10 normal + 1 off）。帧 id / source id 均带会话前缀，跨会话不可能重名。

会话目录（即适配器与未来板端 runner 的下载单元）：

```text
<session>/session-manifest.json      # schema rf-environment-session-manifest-v1
<session>/normal-01.iq …             # 16384 B signed16 LE I/Q
<session>/normal-01.json …           # 板形元数据：五项 identity + demod_result.submitted
<session>/normal-01.environment.json # rf_config + scenario_declaration + environment
<session>/off-01.iq / .json / .environment.json
<session>/receipt.json               # schema rf-environment-session-receipt-v1
```

manifest 记录：11 个有序 slot（position/tag/frame_id/source_id/scenario/seed/reference_prbs7_seed/artifact 名/iq_sha256/capture_id）、`session` 块、`domain`（8 对）、`environment_version`、`release_id`、`fixture/collected/physical/not_for_formal_acceptance`。receipt 绑定 manifest sha、逐 artifact sha（33 项）与逐帧 binding，并声明 `session_closed/disarm_ack`。

`check` 的具名拒绝（全部有稳定 code）：缺件 `SESSION_FRAME_MISSING`、多件 `SESSION_FRAME_EXTRA`、顺序错 `SESSION_FRAME_ORDER`、slot 数错 `SESSION_FRAME_COUNT`、场景配比错 `SESSION_SCENARIO_MIX`、种子错 `SESSION_SEED_MISMATCH`、split 非 train/val `SESSION_SPLIT_NOT_ALLOWED`、字节摘要不符 `SESSION_FRAME_IQ_SHA_MISMATCH`、身份重复 `SESSION_FRAME_IDENTITY_DUPLICATE`、IQ 重复 `SESSION_FRAME_IQ_DUPLICATE`、元数据/侧车身份不一致 `SESSION_FRAME_IDENTITY_MISMATCH`、帧自带 session 块与本目录冲突 `SESSION_FRAME_SESSION_MISMATCH`（VAL 帧进 TRAIN 即由此拒绝）、release 不符 `SESSION_RELEASE_MISMATCH`、域不符 `SESSION_FRAME_DOMAIN_MISMATCH`、版本不符 `SESSION_FRAME_ENVIRONMENT_VERSION_MISMATCH`、夹具自称已采集 `SESSION_FLAG_CONFLICT`、收据不绑定 `SESSION_RECEIPT_MISMATCH`。

**真实采集路线**：`collect` 在写任何文件之前即拒绝为 `PHYSICAL_COLLECTION_NOT_AUTHORIZED`（detail 含 NOT_RUN）；本文件**不实现**板端 runner，也不伪造“已采集”。`fixture` 生成的会话固定 `fixture=true / collected=false / physical=false / not_for_formal_acceptance=true`，帧元数据与侧车同样标记，且 off 帧必须不是 CLEAR（否则 `FIXTURE_NOT_QUALIFIED`）。

### G.3 适配器字段绑定

`adapt --download <root> --output <set> [--download-receipt <file>] [--physical]`。`<root>` 必须恰含 3 个会话目录（train-01、train-02、val-01）；子目录顺序无关，输出按 TRAIN(1,37)+VAL(73) 规范化。

| 输出 | 来源 / 绑定 |
|---|---|
| `set.json.schema` | `rf-environment-calibration-set-v1`（与既有 `fit` 同一 schema） |
| `set.json.plan` | 复用 `rf_environment_calibration._plan_summary()`（矩阵唯一真源，不复制常量） |
| `set.json.domain` | 生产 `rf_teaching_profile` 派生（当前 **46000**）；每帧侧车 `rf` 必须逐对相等，否则 `DOMAIN_MISMATCH` / `SESSION_FRAME_DOMAIN_MISMATCH` |
| `set.json.environment_version` | `rf_environment.VERSION`（当前 **rf-window-environment-v4**）；会话声明与侧车 `environment.environment_version` 必须相等，否则 `ENVIRONMENT_VERSION_MISMATCH` |
| `sessions[].split / seed / session_id` | 会话 manifest，逐帧 `split` 显式继承；跨 split 的 capture id 或 IQ 摘要 → `CROSS_SPLIT_LEAKAGE` |
| `sessions[].frames[]` | `frame_id/source_id/scenario/seed/split/iq/metadata/iq_sha256` + `download.{path,sha256,metadata_path,metadata_sha256,environment_path,environment_sha256,capture_id}`（原始相对路径 + 摘要，逐字节可回溯） |
| `frames/<id>.json` | `rf-environment-calibration-frame-v1`：`identity`（五项）、`submitted`（解调提交块）、`rf`、`truth_declaration`、`download`；`fit` 的生产 replay 路径直接消费 |
| `adapter-receipt.json` | 每个会话的绝对 `download_path`、manifest/receipt sha、逐帧 `source_path` + `iq_sha256`、set sha、计数、`not_run`、限制声明 |
| 宿主下载收据 | `evidence_sha256` 的键必须是 `<session>/<artifact>`；缺键 `DOWNLOAD_RECEIPT_FRAME_UNBOUND`、摘要不符 `DOWNLOAD_RECEIPT_SHA_MISMATCH`、夹具/物理标记不符 `DOWNLOAD_RECEIPT_FLAG_MISMATCH` |

适配器另拒绝：会话数错 `DOWNLOAD_SESSION_COUNT`、会话 id 重复 `DUPLICATE_SESSION_ID`、TRAIN/VAL 组合或种子错 `SESSION_SPLIT_PLAN`、输出已存在 `OUTPUT_EXISTS`。

### G.4 夹具与真实采集的区分

1. 夹具会话只能产出**显式标记**的夹具 set：`provenance.kind=offline_synthetic_fixture`、`fixture={fixture:true, collected:false, physical_frames:0, not_for_formal_acceptance:true}`、顶层 `not_for_formal_acceptance=true`、`not_run` 明列 physical TRAIN/VAL NOT_RUN。
2. 对夹具传 `--physical` 必拒 `PHYSICAL_PROVENANCE_CONFLICT`（夹具不得改标签为真实采集）。
3. 只有“全部会话 `fixture=false, collected=true, physical=true`、会话已闭合且解除布防、三个会话同一非空 release id、且提供宿主下载收据”时，`--physical` 才产出 `new_physical_train_val`；否则 `PHYSICAL_PROVENANCE_UNVERIFIED` / `PHYSICAL_SESSION_NOT_CLOSED` / `RELEASE_ID_MISMATCH` / `DOWNLOAD_RECEIPT_MISSING`。
4. 正式路线必须用 `fit --require-physical`：夹具 set 在此被 `SET_NOT_PHYSICAL` 拒绝；不改变 `fit` 的默认行为，也不改任何阈值/接收器。
5. **限制（明示）**：provenance 绑定到 manifest+receipt+宿主收据链；离线读取无法识别“故意整链伪造”，本轮不声称有签名/防伪。物理分支只做了纯判定函数的单元测试，**没有**用任何合成物产出 physical set。

### G.5 端到端离线贯通（实测，夹具驱动）

`fixture` 3 会话 → `adapt` → `validate_set`（`fit` 内）→ `fit` → `load-check`：

| 项 | 实测值 |
|---|---|
| set | `work/set/set.json` sha256 `cf4358bb…8b30`；33 帧（30 normal + 3 off）；domain 46000 / v4；provenance `offline_synthetic_fixture` |
| 带宿主收据 | `work/set-with-receipt/`（set sha `7dd164d6…f833`）；收据 `work/download-receipt.json` sha `17d3f49a…91c3`、99 条 evidence；适配接受 |
| fit | `verdict.passed=true`；VAL 10/10 CLEAR；off 假 CLEAR 0；TRAIN normal 20；`formal_physical=NOT_RUN`；基线 canonical `24325f4e…3c2c`、文件 `7ff1ceb2…fa7a` |
| load-check | `loaded=true`、`body_equal=true`、20 sources、v4、`calibration_status=development` |
| 正式门 | `fit --require-physical` → `SET_NOT_PHYSICAL` |
| 负例 | **27/27 具名抓住、0 静默**：缺件/多件/顺序/数量/种子/split/字节摘要/身份重复/收据篡改/夹具自称采集/collect NOT_RUN；适配器缺会话/重复会话/VAL 帧进 TRAIN/同一 IQ 跨 split/身份错配/摘要不符/域 40000/版本 v3/收据摘要/收据缺帧/夹具冒充物理/物理未绑定/无收据/输出已存在 |

逐条见 `work/negatives.json`；测试：新增 **51**（会话 27 + 适配器 24），相关集合 **167 passed**，全量 `scripts/tests` **920 passed / 1 skipped**（既有 869 + 新增 51）；日志在 `logs/`。

### G.6 下一步 TRAIN/VAL 的准确命令与数据落点（仍 NOT_RUN）

```text
# 0) 规划（可离线执行，不产帧）
python scripts/rf_environment_trainval_session.py plan --split train --session 1 --output <dir>/train-01.plan.json

# 1) 物理采集：NOT_RUN —— 本入口的 collect 必拒 PHYSICAL_COLLECTION_NOT_AUTHORIZED。
#    需要新的、经审查的板端 runner（1 次 arm→init→configure→load + 10 normal + 1 off
#    + close/disarm/readback），下载成上面 G.2 的会话目录。本规范不实施、不伪造。

# 2) 适配（三个会话目录放进同一个下载根，复制而非符号链接）
python scripts/rf_environment_trainval_adapter.py adapt \
  --download evidence/rf-teaching/s2/46db-train-<date>/download \
  --download-receipt evidence/rf-teaching/s2/46db-train-<date>/download/download-receipt.json \
  --physical --output evidence/rf-teaching/s2/46db-train-<date>/calibration-set

# 3) 分组校验 + 拟合 + 读回（正式路线必须带 --require-physical）
python scripts/rf_environment_calibration.py fit \
  --set evidence/rf-teaching/s2/46db-train-<date>/calibration-set \
  --require-physical --output evidence/rf-teaching/s2/46db-train-<date>/fit
python scripts/rf_environment_calibration.py load-check \
  --baseline evidence/rf-teaching/s2/46db-train-<date>/fit/baseline.json --require-formal
```

数据落点（spec §2.6）：TRAIN 会话 `evidence/rf-teaching/s2/46db-train-<date>/s01`、`s02`，VAL 会话 `…/46db-val-<date>/s01`，pilot 另立 `…/46db-pilot-<date>/`。**pilot 18 帧是 3 会话 × 6 类的一次独立 campaign（C 候选上），不属于本 TRAIN/VAL，也不是同一次执行**；`33 + 18 = 51` 是拟议预算，不是已采集结果。若证据根放在 `s2` 之外，必须按 D-1 扩展 freeze 的扫描根（本轮未做）。

### G.7 未做 / 未证明

- **0 个新物理帧**；无板卡接触、无 ARM/部署、无 RF/授权/nonce；51 帧（33 TRAIN/VAL + 18 独立 pilot）**未采集**；
- 物理 provenance 端到端（真实下载 → `--physical` set）**NOT_RUN**；只用纯判定函数覆盖了该分支；
- 两个新入口**尚未纳入** `scripts/rf_environment_freeze.py` 的 `FORMAL_TOOLS`/`tool_hashes`；M11 重签、N1–N12 重跑、freeze 声明 **NOT_RUN**，旧 freeze 收据不继承；
- 不声称任何 dB 余量、标签正确性、阈值有效性；夹具拟合出的 development 基线不是正式基线；
- 未 `git commit`。

### G.8 更正（2026-10-18 09:2x，重派发实例）：提交块的 RX 草稿 revision 不是常量 0

## 板端采集路径（2026-10-18，子代理离线作业）

本节**只追加**，不改动 §0–§7、上方"更正""实现进展"与"工具缺口闭合"任何历史结论；只记录 46 dB TRAIN/VAL 的**板端会话采集路径**如何被实现、离线贯通到什么、真实执行还差什么。证据：`evidence/rf-teaching/s2-46db-collect-impl-2026-10-18/`（`NOTES.md` / `work/` / `logs/`）。本轮零板卡、零 RF、零授权/nonce、零 `git commit`、零产品树/封存件改动。**33 帧 TRAIN/VAL 与 18 帧 pilot 仍未采集（0 个新物理帧）。**

### H.1 入口与职责

| 文件 | 职责 |
|---|---|
| `scripts/rf_environment_trainval_board_session.py`（新增） | **板上**11 帧会话驱动：1 次 `arm→init→configure→load` + `normal-01..10`（物理 TX1→RX1 回环）+ `off-01`（同窗口无源能量地板）+ close/disarm 读回；逐帧门禁与具名停止；11 帧全过且可闭合才写 `session-manifest.json` + `receipt.json`（即适配器的下载单元）；`--preflight` 为只读预检（release/合同/域/基线/帧序；不建客户端、不碰 RF） |
| `scripts/rf_environment_trainval_collect.py`（新增） | 主机编排：候选 release 校验 + 域前置（**连接之前**）→ 上传评测过的驱动源码到 `<remote>/scripts/` → 板上只读预检 → `sudo` 运行一次会话（复用 `/etc/rf-control.token`，**不消耗 nonce/attempt**）→ 原样下载会话目录 → 写宿主 `download-receipt.json`（`evidence_sha256`，键 `<session>/<artifact>`）与 `collect-receipt.json`；**不重试、不补采** |
| `scripts/rf_environment_trainval_session.py`（改动，仅 `collect` 分支 + 文档） | 授权门：无 `--authorize-board-collection` 时仍在**任何导入/连接/写盘之前**拒绝 `PHYSICAL_COLLECTION_NOT_AUTHORIZED`；带标志后转交主机编排，缺 `--candidate` / `--cable-confirmed` 分别具名拒绝 |
| `scripts/tests/trainval_stub_board.py`（新增，**测试桩，非入口**） | 桩板：合成帧 + 桩板 link（跑**真实** `run_session`/`build_session_documents`）+ 负例变异助手；产物 `origin` 明写 STUB、release id `stubboard...`，只用于离线贯通 |
| `scripts/rf_environment_trainval_adapter.py` / `rf_environment_calibration.py` | **未改**：本路径产出的下载物直接落进它们的既有契约 |

### H.2 授权门语义（fail-closed，三段式）

1. **无标志** → 退出码 3、`PHYSICAL_COLLECTION_NOT_AUTHORIZED`，不 import 采集模块、不建连接、不写文件；
2. **有标志但缺输入** → `SESSION_COLLECT_CANDIDATE_REQUIRED`（缺 `--candidate`）/ `SESSION_COLLECT_CABLE_CONFIRMATION_REQUIRED`（缺 `--cable-confirmed`），**均在连接之前**；
3. **三件齐备** → 主机编排；编排内部再次要求 `authorized=True`（`BOARD_COLLECTION_NOT_AUTHORIZED`）并通过候选域前置。

**该路径需要板时授权与接线确认**：授权 = 对"1 初始化 + 11 帧 + 关闭读回"的板时许可（环境合同 token + 每次运行 `--cable-confirmed`；**不用** HEADROOM 的 single_use 授权/nonce 账本）；接线确认 = TX1→RX1 直连的人工声明，且每帧物理采集仍走生产两击回环确认。

### H.3 前置（真板执行前必须全部成立）

| # | 前置 | 本轮状态 |
|---|---|---|
| P1 | 已安装 release **就是**候选：`verify_release(require_software=True)` 通过、合同 `cnn-bpsk-environment-v1` | 主机 + 板上各一次预检（代码就绪，未在板上跑过） |
| P2 | 候选自带基线与其 teaching profile **同域**：`attenuation_mdB=46000`、`environment_version=rf-window-environment-v4` | **当前不成立**：候选 B（`ad05576a…`）携带历史 40 dB/v3 基线 → 具名拒绝 `CANDIDATE_BASELINE_DOMAIN_MISMATCH`；必须先做 M4 候选构建（本轮未做） |
| P3 | 只读板检：`current=active-release`、双服务 active、RF safe（未布防 / TX 源未选 / RX 未武装）、存储 0 故障、`/` 与 `/boot` rw | **NOT_RUN**（本轮零板卡） |
| P4 | 板时授权 + 接线确认（未改变且在有效窗口内可引用原确认，K10） | 待板时任务 |
| P5 | 下载根为空、三个会话最终进入同一适配根 | 见 H.6 |

### H.4 命令（3 会话 / 33 帧；**本轮均未执行**）

```text
python scripts/rf_environment_trainval_session.py collect --authorize-board-collection \
  --candidate <candidate-release-dir> --cable-confirmed \
  --split train --session 1 --output evidence/rf-teaching/s2/46db-train-<date>/download
python scripts/rf_environment_trainval_session.py collect --authorize-board-collection \
  --candidate <candidate-release-dir> --cable-confirmed \
  --split train --session 2 --output evidence/rf-teaching/s2/46db-train-<date>/download
python scripts/rf_environment_trainval_session.py collect --authorize-board-collection \
  --candidate <candidate-release-dir> --cable-confirmed \
  --split val --session 1 --output evidence/rf-teaching/s2/46db-val-<date>/download

python scripts/rf_environment_trainval_adapter.py adapt \
  --download <root-with-all-three-sessions> --download-receipt <root>/download-receipt.json \
  --physical --output evidence/rf-teaching/s2/46db-train-<date>/calibration-set
python scripts/rf_environment_calibration.py fit \
  --set evidence/rf-teaching/s2/46db-train-<date>/calibration-set --require-physical \
  --output evidence/rf-teaching/s2/46db-train-<date>/fit
python scripts/rf_environment_calibration.py load-check \
  --baseline evidence/rf-teaching/s2/46db-train-<date>/fit/baseline.json --require-formal
```

**预算**：3 会话 / **33 帧**（TRAIN 2×11 + VAL 1×11）；每会话 1 次初始化 + 10 次物理回环 + 1 次无源 + 1 次 close/disarm；**零** nonce/attempt 消耗。18 帧开发 pilot 为**另一次独立 campaign**，不在这三条命令内。

### H.5 判据

* **逐帧落盘**：原始 IQ(16384 B) + metadata + environment sidecar + record，并逐帧登记 `requested_rf`（下发的 8 个射频参数，衰减 46000 的唯一真源是 release 的 `rf_teaching_profile`）、`actual_rf`（PL pre/post 读回、daemon 状态身份、只读 RX1 增益观测；**衰减寄存器在本路径不读回**，写 `null` + 具名 `NOT_READ_BACK_ON_ENVIRONMENT_PATH`）、`label`/`reason`、`rail`/`clipping_count`；
* **绑定**：IQ 摘要 == 文件字节 == 元数据 == 侧车 == 会话收据 ==（提供时）宿主下载收据；identity 五元组非空且逐帧唯一、IQ 摘要逐帧唯一；域 46000 / v4 三方一致；
* **标签**：TRAIN normal 可靠零误码 BPSK（`reference_sync=true`、`ber=0`、`compared_bits>=32`）且非 INVALID；VAL normal = CLEAR；off ≠ CLEAR；
* **会话**：11 帧（10 normal + 1 off）、`session_closed=true` 且 `disarm_ack=true`。

### H.6 停止条件（= §2.5 七条，逐字保持）

1. 任一帧 `CLIPPED` / `INVALID_IQ` / `egress_safe!=true` / 下载缺件；
2. 任一帧 identity 或 IQ 摘要重复；
3. 会话无法闭合（`close_unconfirmed` / `disarm_ack=false`）；
4. TRAIN 的 normal 帧出现任何不可靠 BPSK；
5. VAL 出现 `CLIPPED/INVALID` 或 normal 被判 `NARROWBAND/PULSE`（记录后停止，不得改阈值让它过）；
6. 任何"为了让数据好看"的阈值/基线/域参数改动（改动 = 新版本 + 新基线 + 重跑全门）；
7. release 身份漂移（`current` 变、服务重启不可解释、存储故障）→ 停止并只做只读读回。

### H.7 失败处置

**不重试、不补采、不调参**：任一命中即停止整条 campaign，后续会话不再开始；已落盘原始 IQ/元数据/侧车/记录**全部保留**；未过 11 帧门的会话**不写** manifest/receipt（不构成可下载会话）；下载根一旦含失败会话即视为已消耗，重新采集必须使用**新的下载根**（`SESSION_ALREADY_COLLECTED`）。

### H.8 离线贯通与负例（本轮实测，桩板驱动）

`work/run_offline_e2e.py`（桩板、零板卡）：标准候选 → 3 会话采集（`passed=true`、各 11 帧、闭合并解除布防、宿主收据累计 68/136/**204** 条摘要）→ `adapt --physical`（`new_physical_train_val`、33 帧、set sha `6465e88c…`）→ `fit --require-physical`（`passed=true`、TRAIN normal 20、VAL 10/10 CLEAR、`negative_false_clear=[]`、基线 canonical `24325f4e…`）→ `load-check --require-formal`（`loaded=true`、`body_equal=true`、20 sources）。

**18/18 负例具名拒绝、0 静默**：无授权标志 / 有标志无候选 / 无接线确认 / 候选 40 dB-v3 基线 / 裁剪帧 / 停止会话不成会话 / 下载掉件 / 缺 `--physical` / 无宿主收据 / 删文件 / 改字节 / 身份不一致 / 跨 split 同 IQ 摘要 / 收据摘要被改 / 未解除布防 / 未闭合会话进物理集 / 夹具被标夹具 / 夹具集走正式 fit。逐条见 `work/offline-e2e/negatives.json`。

**测试**：新增 39 项（板端会话 19 + 主机编排 17 + 会话入口门 3）；受影响集合 7 文件 **184 passed**；全量 `scripts/tests` **964 passed / 1 skipped**（改动前基线 921 passed / 1 skipped）。

桩板产物（`work/offline-e2e/**`）全部是合成码与声明为物理的**测试双**，**不是**物理证据、**不得**作为 46 dB 基线或 TRAIN/VAL 证据。

### H.9 证据落点

本轮：`evidence/rf-teaching/s2-46db-collect-impl-2026-10-18/{NOTES.md,work/,logs/}`。真板执行落点按 §2.6：TRAIN `evidence/rf-teaching/s2/46db-train-<date>/{download/{train-01,train-02},calibration-set,fit}`、VAL `…/s2/46db-val-<date>/download/val-01`（三会话复制进同一适配根）。若证据根仍在 `s2` 之外，必须同步扩展 `scripts/rf_environment_freeze.py:69-71` 扫描根（K11/§2.6 二选一）——**本轮未做**（无物理帧，无影响）。

### H.10 未做 / 未证明（NOT_RUN）

真实板端采集执行；板端真实导入路径（板端 `python3` 导入 `scripts.rf_environment_trainval_session` 与 release 模块）未在板上验证；M4 候选未构建（候选 B 现状被拒）；物理 provenance 的真实端到端（真实下载 → `--physical` set）；正式基线/冻结/M11 重签；新入口**尚未**纳入 `rf_environment_freeze.FORMAL_TOOLS/tool_hashes`。不声称任何 dB 余量、标签正确性或阈值有效性；未 `git commit`。

**本节只追加，只更正一条判据，不改动 §0–§7 与上方任何历史结论。**

11 帧会话此前（G.2/实现）要求每帧 `metadata.demod_result.submitted.revision == 0`。该判据在**真板上必然不成立**，会把整条采集路径卡在第 1 帧：

| 事实 | 依据 |
|---|---|
| `submitted.revision` 是板上 `UiApp.rx_demod_revision`；草稿默认 `reference_enabled=False`、revision=0 | `pynq_overlay/sw/ui_model.py:1122-1127` |
| 会话 `setup()` 置 `reference_enabled=True` 并按会话轮换 `reference_seed`(37/73) | `scripts/rf_bpsk_ui_board_session.py:242-248` |
| `set_rx_demod_setting` 对每个变化键 +1，全库无重置点 | `pynq_overlay/sw/ui_model.py:1963-1981` |
| 生产正式路径一直按**非负整数**校验同一字段 | `scripts/rf_environment_acceptance.py:166` |
| 离线 stub 写死 0，因此离线全绿看不到该阻塞 | `scripts/tests/trainval_stub_board.py:172` |

**更正后的判据**：`type(submitted['revision']) is int and submitted['revision'] >= 0`（板端 `evaluate_frame` 与下载侧 `check_session` 同判）；夹具的 0 仍通过，负数/非整数仍**具名拒绝**（`FRAME_DEMOD_CONFIG_MISMATCH` / `SESSION_FRAME_DEMOD_MISMATCH`）。其余门（五元组身份、字节摘要、域 8 对、`environment_version`、场景真值、闭合/解除布防）**未放宽**。回归：`scripts/tests/test_rf_environment_trainval_board_revision.py`（4 项，含真板 revision=7 通过）。

**证据**：`evidence/rf-teaching/s2-46db-collect-impl-2026-10-18/NOTES-redispatch.md` 与同目录 `work/redispatch-verify/`、`logs/pytest-redispatch-*`。
## I. C 侧迁移（route A，2026-10-18）：回环放行常量并入域单一真源

**本节只追加，不改动 §0–§7、A–H 与上方任何历史结论；它记录的是把"teaching 40000 ≠ research 46000"这句话本身废除的那次改动。** 证据：`evidence/rf-teaching/s2/46db-c-side-migration-2026-10-18/{NOTES.md,work/,logs/,candidate/}`。

### I.1 根因（一句话）

46 dB 域迁移只搬了 Python：`rf_teaching_profile.ATTENUATION_MDB=46000` 决定 UI/worker/client 发出的请求字段，而 daemon 编译进去的回环放行值仍是 `rf_overlay/sw/board_app/rf_state.h` 的 `RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB=40000u`（精确相等门，`rf_daemon.c:1671`，早于 `rf_state_begin`、`tx_count` 门与任何硬件写）⇒ 每个物理帧必然 `INVALID_ARGUMENT`，环境 10 个 physical 帧确定性全灭。

### I.2 本轮改动

| 文件 | 改动 |
|---|---|
| `rf_overlay/sw/board_app/rf_state.h` | 常量 40000u → **46000**（去 `u` 后缀，使报文宏展开为 `46000`）；注释写明域单一真源与跨语言钉子 |
| `rf_overlay/sw/board_app/rf_state.c` | 两条具名拒绝报文改由 `RF_STATE_STRINGIFY(RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB)` 生成 ⇒ 报文与判据不可能互相矛盾；**精确相等 + 具名拒绝语义未变**，其它值（含 40000、46001）仍拒 |
| `rf_overlay/sw/board_app/test/test_rf_state.c`、`test/test_rf_headroom.c` | 断言改为"批准 46000、40000 与相邻值仍**具名**拒绝"；`test_rf_headroom.c` 的 `test_teaching_gate_is_still_exact_40000` 改名 `test_physical_gate_is_exactly_the_domain_value`，**拒绝能力断言保留**（未删） |
| `rf_overlay/sw/board_app/test/test_rf_daemon.c` | 11 处回环请求改由 `LOOPBACK_ATTENUATION_ARG` 宏生成，不再手写数字 |
| `scripts/tests/loopback_stub_daemon.py` | 桩 daemon 默认规则改为**读** `rf_state.h`（`c_gate_attenuation_mdb`），不再是副本 |
| `scripts/tests/test_rf_state_loopback_attenuation_pin.py`（新） | 跨语言钉住：C 常量 == `rf_teaching_profile.ATTENUATION_MDB`；报文必须由该宏生成且不得硬编码数字；板端探针字面量 == 该值；桩 gate 运行时批准域值、具名拒绝其余值 |
| `scripts/tests/test_rf_environment_trainval_loopback_attenuation.py` | 改为"桩按 C 头文件批准"；**保留** 40000 桩作具名拒绝负控；e4dae554 旧发布仍作 entry gate 的 `MISMATCH` 负控，并新增"携带 46000 的候选 preflight ok=True"正控 |

### I.3 本节取代的表述（历史文字保留，不再作为判据）

- **K3** 的"client 物理回环硬要求 40000、UI 采集参数 40000"：两侧早已/现在都由域单一真源决定，"两侧不同档"不再成立。
- **§4.0 B-2** 的前提"teaching=40000 与 research=46000 **必须不同**"作废：决策 (a) 已在 Python 与 C 两侧落地为**一个域、一个值**；"非放行档位仍被拒"的语义按 I.2 保留。
- `docs/superpowers/specs/2026-10-10-s2-headroom-research-design.md` 第 8 行"`rf_state_check_physical_loopback()`严格要求40000mdB"已就地标注过期。

### I.4 连带后果（本轮实测）

- **新候选被"原生件漂移门"正确阻断**：`verify_observer_build` 要求 `rf_daemon.build.json` 的 58 项源摘要 == 当前 `board_app` 源；本轮改动的正是其中 5 项（`rf_state.c`/`rf_state.h`/3 个测试文件），旧 ARM 件（`bbfd0409…`）因此不再可用，`scripts/rf_environment_candidate.py` 以 `native artifact source changed or incomplete` 拒绝（exit 1、未建目录）⇒ **必须先在板上重编 ARM daemon（compile-only）才能构建新候选**。同 receipt + 同源还原后 `verify_observer_build` PASS（`work/pre-edit-binding-probe.json`），故 `scripts/tests/test_rf_headroom_candidate.py` 的 2 项失败**只是这一门**。
- **旧发布 e4dae554 在迁移后必然不匹配**（域 46000 vs 二进制 40000），entry gate 具名拒绝 `DAEMON_LOOPBACK_ATTENUATION_MISMATCH`——这正是"必须新候选"的机器证明。

### I.5 数字（本轮实测）

C（主机 MinGW gcc 14.2.0）：`host-test` 820 PASS / 2 FAIL（2 条为 selftest 自带注入负例）、`host-test-daemon` **768 PASS / 0 FAIL**（改动前 764/0，+4 为本轮新增断言）、core 98/0、iq 40/0、protocol 58/0、sleep 22/0、vendor integrity 10 files。
Python：`scripts/tests` **1010 passed / 2 failed（原生件漂移门）/ 1 skipped**；`pynq_overlay/sw/tests` **2186 passed / 7 skipped**（C 改动不影响该套件）。
变异矩阵（隔离副本、工作树零改动、逐字节还原）：M0 控制绿；**M1 常量回退、M2 删相等门、M3 报文与判据不一致、M4 Python 域回退全部被抓**（M2 由 C 套件抓、其余由钉子测试与/或 C 套件抓）。

### I.6 未做 / 未证明（NOT_RUN）

板端 ARM 编译、新候选构建、ARM 资格、安装、板卡接触、任何 RF、任何新物理帧；"ARM 件在板上批准 46000"只能由新 ARM 构建 + 资格证明（本轮**不声称**）；`scripts/rf_headroom_prepare.py` 的"native state…require exact 40000 mdB"叙述仍为旧文本（属已审查 HEADROOM 合同面，本轮**未改**，需随新合同一并增量复审）；`scripts/rf_environment_dataset.py:57-65` 的 S1 40 dB 历史门**有意保留**（按设计拒绝）。

## J. 采集模式与 OFF 场景声明（2026-10-18，离线修复；取代此前 runner 的"VAL 必须先 CLEAR"实现）

### J.0 一句话

`off` 必须经**真实场景加载路径**生效（场景支持范围 = 场景模块自己的 `KINDS`，每连续场景组加载一次），
且标定采集必须有一种**显式声明的模式**：它只额外接受**一对**由拟合前 n=1 开发基线产生的合格暂定 UNKNOWN，
其余停止条件、拟合来源与拟合后 VAL/OFF 判据**一个字都不放宽**。

### J.1 两个已确认的直接失败原因（2026-10-18 campaign3 train-01，原始数据保留）

1. **OFF 声明未转换**：runner 只在会话开始加载一次 normal（修复前 `scripts/rf_environment_trainval_board_session.py:674`），
   off-01 沿用 normal 声明，被逐帧门 `FRAME_SCENARIO_MISMATCH` 拒绝。根因不止一处：场景入口
   （`ui_model.set_environment_scene` / `ui_view` 的按钮表 / `EnvironmentSession.scene`）只支持 4 个场景，
   而产品场景模块 `rf_experiment_scenarios.KINDS` 已含 `off`；只加载一次是因为 runner 假定场景在整个会话内不变。
2. **标定顺序矛盾**：旧 n=1 开发基线（file `e1ed6654…` / canonical `6b697e0e…`）把 10 个**完全合格**的 normal 帧
   （identity 五元组、域、138 比较位、`reference_sync=true`、`ber=0`、无削顶）判为
   `UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT`（实测 rms 237.8–243.7、crest 1.96–2.50，阈值 `pulse_crest=1.537`），
   而 VAL 逐帧门要求 `CLEAR` ⇒ 采集在**第一次拟合之前**就被拦住，而 VAL 是否 CLEAR 本应由**拟合后**的基线判定。

### J.2 OFF 场景（规范）

- **支持范围单一声明**：`ui_model.ENVIRONMENT_SCENES` 必须**逐元素等于** `rf_experiment_scenarios.KINDS`（顺序也相同）；
  `ui_view` 的按钮由它构建并把该序列暴露为 `view.environment_scene_kinds`；runner 的 `EnvironmentSession.scene`
  由视图读回该序列（**不得**再嵌一份字面量列表）。漂移由 `pynq_overlay/sw/tests/test_rf_environment_ui.py` 的相等测试守住。
- **按槽位加载**：场景在**每个连续场景组**开始时加载一次 —— `normal-01` 前加载 `normal`，`off-01` 前加载 `off`
  （矩阵下即 2 次加载/会话）。加载走真实产品两下确认路径（按钮 → `set_environment_scene` → worker `tx.load`），
  并保留"加载结果声明 == 所加载场景"的确认。
- **声明绑定不放松**：逐帧门 `FRAME_SCENARIO_MISMATCH` **保留**：帧载 `scenario_declaration.truth` 必须等于槽位场景；
  **禁止**事后改写 sidecar 声明或删除该门来让 off 通过。
- **OFF 采集语义**：off 槽位是**非物理** RX 采集（`physical=false`）。OFF 场景本身是零波形（`tx.load` 不辐射、
  **不选中 TX 源**），逐帧门仍要求 post readback `tx_source_selected=false`（且 `io_ok=true`、`rx_armed=false`）。

### J.3 显式采集模式（规范）

会话**声明**一种采集模式，并写入 session manifest、receipt、每帧信封、适配器 set/sidecar 与拟合收据：

| 模式 | 语义 | VAL normal 逐帧门 |
|---|---|---|
| `standard_acceptance`（**默认**，fail-closed） | 正式验收规则 | **必须 `CLEAR`** |
| `calibration_collection` | 显式标定采集模式 | `CLEAR`，**或** J.3.1 的"合格暂定 UNKNOWN"（原标签原样保留） |

**J.3.1 合格暂定 UNKNOWN（唯一允许的一对）**：`label == 'UNKNOWN'` 且 `reason == 'AMPLITUDE_OUTSIDE_RULE_SUPPORT'`，
**且**在它之前 `evaluate_frame` 的**全部质量门已通过**：产物存在/大小/IQ 摘要、identity 五元组、会话绑定、release、
`rf_config` 域、环境版本、声明 truth、demod 身份与提交块（BPSK/3 MSym/s/RECT/参考种子/decim/rx_len/input_fs）、
readback 安全（`io_ok` / `rx_armed=false` / `tx_source_selected=false`）、非 `INVALID_IQ`、无 rail/`CLIPPED`，
以及 normal 的 `reference_sync=true`、`ber=0`、`compared_bits>=32`。
**原标签保留**：manifest/receipt/sidecar/适配器/拟合收据一律记录 `collected_label` / `collected_reason` 与
`provisional_unknown` 标志，**不得**改写成 `CLEAR`；下载侧（host collect）还要求会话声明的模式 == 本次派发的模式
（否则 `SESSION_COLLECTION_MODE_MISMATCH`）。

**J.3.2 仍然停止的**（两种模式一致）：`NARROWBAND`、`PULSE`、`INVALID`、**其它** `UNKNOWN` 原因（如 `BPSK_NOT_RELIABLE`、
`WEAK_SPECTRAL_ANOMALY`、`TONE_WITHOUT_RELIABLE_RESIDUAL`）、或任何上述质量门失败；
OFF 帧 `CLEAR` 在任何模式下都是 `OFF_FALSE_CLEAR`。

### J.4 与 §2.5 的关系

§2.5 停止条件 5 的原文是"VAL 出现 `CLIPPED`/`INVALID` 或 normal 被判 `NARROWBAND`/`PULSE`（记录后停止）"。
此前 runner 把它实现为"VAL 必须 `CLEAR`"（更严）。`standard_acceptance` 保留该更严实现（正式验收口径不变）；
`calibration_collection` 实现的是 §2.5.5 的**原集合**，并**再收紧一层**：除 `NARROWBAND`/`PULSE`/`INVALID` 外，
其余 UNKNOWN 原因也一律停止，只放行 J.3.1 的那一对。**任何情况下不放宽**。

### J.5 不变更的最终判据（单列，避免误读）

- 拟合输入**只有 20 个 TRAIN normal**；VAL **不参与拟合或调参**（sources 全部 `split=train`）；
- **拟合后 VAL 必须 10/10 CLEAR**；`negative_false_clear=[]`（OFF 永不 CLEAR）；
- 不手工调阈值、不放宽正式验收标准；模式只作**声明**进入证据，不进入任何判据函数；
- 试部署、正式 TEST、真人验收均**不**因本模式改变。

### J.6 实现与验证落点

- 共享常量与判据：`scripts/rf_environment_calibration.py`（`STANDARD_COLLECTION`/`CALIBRATION_COLLECTION`、
  `PROVISIONAL_UNKNOWN`、`declared_collection_mode`、`check_collected_label`）；session schema 别名并在
  manifest/receipt/槽位校验；board runner 逐帧门与场景切换；host collect 透传与下载侧强制；adapter 与 fit 记录并自洽校验。
- 新行为测试：`scripts/tests/test_rf_environment_trainval_calibration_collection.py`（场景切换、旧声明被拒、
  合格 UNKNOWN 收集、标准模式拒绝、其它标签仍停、OFF 负控、三会话链 → 适配 → 拟合 → load-check、混模式与手改拒绝）。
- 端到端数字与变异矩阵：`evidence/rf-teaching/s2-46db-d6-calibration-fix-2026-10-18/`。

### J.7 未做 / 未证明（NOT_RUN）

真板 OFF 场景加载、任何新物理帧、新候选构建/资格/安装、ARM、真人验收、正式 TEST、18 帧 pilot：
本轮**全部 NOT_RUN**（离线修复 + 假链；板卡动作由后续单次执行者完成）。


## K. 2026-10-18 操作者批准的最小 VAL 追加

campaign4 两个 TRAIN 会话完整通过（22 帧），VAL 首帧因旧 n=1 基线 PULSE/SPARSE_AMPLITUDE_OUTLIERS 停止。只用其中20个 TRAIN normal 拟合的离线诊断，将该 VAL 首帧判为 CLEAR，两个 TRAIN OFF 均非 CLEAR；这不构成完整 VAL 通过证据。

操作者已在 Codex 本会话明确批准 NEXT-VAL-PLAN.md：仅 calibration_collection 模式增加允许 PULSE/SPARSE_AMPLITUDE_OUTLIERS 原标签留存，且须先通过现有身份、域、场景声明、零误码参考同步、无削顶及安全读回质量门。标准模式不变，其他 VAL 标签继续拒绝，OFF CLEAR 继续拒绝；provisional_unknown 仍只标记原 UNKNOWN 对，PULSE 不伪装成 UNKNOWN。原始分析结果、manifest/receipt/set 的标签均保持原样。

最终判据不变：20 TRAIN normal 拟合，VAL 不参与拟合或调参；拟合后的 VAL 必须10/10 CLEAR，OFF false-CLEAR=0，否则不产出通过基线。

预算：原 campaign4 保持失败历史。新根逐字节复用原两个完整 TRAIN（记录原件路径与复制摘要），只追加 VAL seed73，最多一次初始化、10 normal+1 off、失败即停止，不自动补采。此为22帧复用加11帧新采，不声称重新采集33帧。原失败VAL首帧不混入新集合。18帧pilot、正式TEST和真人验收仍未授权。

## L. 2026-10-19：AMPLITUDE_OUTSIDE_RULE_SUPPORT 的真实语义与 normal → CLEAR 的电平支撑（探索结果，**不是**验收前置条件）（只追加）

**本节只追加**，不改动 §0–§7 与 A–K 的任何历史结论（包括 18 帧 pilot 的 `known_correct=8 < 9`、`passed=false`、唯一不符帧 `s00-normal-00` 原样保留）。
**本节不改变任何判据**：不新增/不放宽/不改写任何阈值、规则分支、基线、验收口径或停止条件；不改变已记录的 pilot 判定（仍为未通过）；不触板、不采集、不消耗授权/nonce。

### L.1 真实语义：名字里有 AMPLITUDE，判据是 crest/plateau 超出 TRAIN 拟合包络

`AMPLITUDE_OUTSIDE_RULE_SUPPORT` 由两条分支产生，两条都是**包络（crest / plateau）**判据，**不是** RMS 区间检查：

| 分支 | 位置 | 条件（逐字） | 语义 |
|---|---|---|---|
| plateau 分支 | `pynq_overlay/sw/rf_environment.py:378` | `plateau_outside`（`:359` 定义：`plateau_share > pulse_max_fraction` 或出现宽度 >256 的稠密平台） | 平台/占空比超出支撑 |
| **crest 分支（本帧命中）** | `pynq_overlay/sw/rf_environment.py:381-382` | `share > t[pulse_max_fraction]` **或** `f[crest_ratio] > t[pulse_crest]` | 峰值/中位（crest）超出 TRAIN 拟合包络 |

阈值构成（拟合函数 `pynq_overlay/sw/rf_environment.py:252`）：`pulse_crest = max(1.20, max(TRAIN crest) + 0.08)` —— 即**20 帧 TRAIN 的极值包络 + 0.08**，不是稳健上界。本冻结基线下 `max(TRAIN crest) = 1.9010145218306414`（train-02/normal-09），故 `pulse_crest = 1.9810145218306414`。

**规则里没有 RMS 上限**：`minimum_rms_codes = 16`（`pynq_overlay/sw/rf_environment.py:253`）只是**下限**，在 `:290` 与 `:294` 以 `rms_codes < minimum_rms_codes → INVALID/LOW_ENERGY` 使用。因此 RMS 高于 16 codes 的正常帧仍可能因 crest 超包络被拒。
**已复现的是这一直接触发条件（`crest_ratio > pulse_crest`）；该规则是否满足 normal→CLEAR 需求、标定覆盖是否充分，以及会话间电平差的物理原因，仍需判断。**

### L.2 电平支撑注解（探索结果，**不是**判据、不是验收前置条件）

**探索性注解（只描述已归档的合成模型结果，不是验收前置条件，也不得据此排除帧）**：在“对高电平帧线性缩放 + 叠加实测噪声底”这一特定合成模型下，本冻结基线与约 90 codes 噪声底时，crest 恰好越过 `pulse_crest` 约在窗内 complex RMS **≈303 codes（≈ -19.6 dBFS）**。该值**不是已批准的验收前置条件**：冻结判据中不存在任何 RMS 上限或电平门；它**不得**用来把失败的 normal 帧（本批为 `s00-normal-00`，仍为 `UNKNOWN/AMPLITUDE_OUTSIDE_RULE_SUPPORT`、仍计 MISMATCH）排除、重分类为“可接受”，或改写 18 帧结论。
下文的合成拒收概率（32.25%，400 次合成）同样只是该指定缩放+加噪模型下的结果，**不是板卡实际总体 normal 拒收率**。

该注解的来源与量级（全部来自已归档证据，无新物理帧）：

| 项 | 值 | 来源 |
|---|---|---|
| 本批噪声底（off 帧，`rx.capture`、`physical=false`、TX 关） | 89.02 / 90.73 / 91.34 codes（≈ -30 dBFS，会话间基本不变） | 18 帧 pilot 的 off 帧 |
| 信号电平（同波形、amplitude=60、46 dB） | 269.8 / 630.8 / 458.2 codes，会话间相差 **7.4 dB** | 同上 |
| 支撑边界（缩放实验 + 8 个真实噪声实现） | complex RMS ≈ **303 codes ≈ -19.60 dBFS** 时 crest 恰等于 1.9810145218306414 | `evidence/rf-teaching/s2-46db-first-frame-amplitude-2026-10-19/work/amplitude-support-experiment.json` |
| 该电平下拒收概率（400 次合成，f=0.40、噪声底 89.02 codes） | `P(crest > 阈值) = 0.3225` | `.../work/campaign-law.json` |
| 观测帧 | `s00-normal-00`：`rms_codes = 268.91733449436634`、`crest = 2.0911514508063367 > 1.9810145218306414`、`label=UNKNOWN`、`reason=AMPLITUDE_OUTSIDE_RULE_SUPPORT`（超出 +0.1101） | 同上 + 18 帧 pilot 侧车 |

- 覆盖关系：本帧 crest 超过**全部 20 个 TRAIN 帧**；会话间 7.4 dB 的电平差是**实测**，其物理原因仍是 **NOT_ESTABLISHED**（pilot 无增益读数）。
- 该注解只说明**既有实现**被触发到了什么量：它不修改判据、不说明该判据满足 `normal → CLEAR` 需求、也不说明标定覆盖充分，更不把未达标的结果改写成通过；`known_correct=8`、`passed=false`、18 帧完整与门（`known_correct=9`、`negative_false_clear=0`）均原样成立。
- 边界之外的遗留判断：窗口内平稳（16×256 分块 RMS 极差 1.0897、前/后半 0.9922）**不能彻底排除窗口前或会话间的增益/链路因素**；pilot 无每帧增益遥测 ⇒ 无法排除。

### L.3 off 帧的 scenario_declaration 是 stale-by-design

- `off` 帧侧车的 `scenario_declaration` 是 **stale-by-design**：pilot 循环对 `off` 不加载场景（`scripts/rf_environment_pilot.py:31-35`），声明沿用上一次加载的场景（本批为 `compound`），**不是**本次发射真值。
- `off` 帧走 `rx.capture`、`physical=false`、TX 关；真值以 `result.json` 行的 `truth=off` 与 `op=rx.capture` 为准。把 off 侧车声明读成“发过 compound”是误读。
- 该说明同样**不改变任何判据**：off 帧仍必须非 CLEAR（本批 3/3 非 CLEAR）。

### L.4 逐帧报表新增字段（呈现层，不改变判定逻辑）

`scripts/rf_environment_frame_report.py`（新增，纯读取）把既有产物中**已经存在**的量呈现在同一张逐帧表里：

| 新增列 | 来源（既有产物字段） |
|---|---|
| `rms_codes`（规则特征） | `environment.features.rms_codes` |
| complex RMS（codes） / complex dBFS | 板端元数据 `analysis.complex_rms` / `analysis.complex_dbfs` |
| 同批噪声底（codes / dBFS） | 同一会话 `off` 帧的 `environment.features.rms_codes` / `analysis.complex_dbfs` |
| crest / crest 阈值 / crest 超出 | `environment.features.crest_ratio` / `environment.rule_evidence.pulse_crest_threshold` / 两者之差 |
| 电平低于支撑 | `rms_codes` 与本节 L.2 注解常数 303.1510618377535 的比较（**注解，不是判据、不是验收前置条件**，也不用于排除任何帧） |

报表只复制 label/reason（逐字）、不重算标签、不读 IQ 字节、不派生缺失值（缺失写 `NOT_AVAILABLE`）；若侧车与该帧 `result.json` 行不一致，只如实标注，不做取舍。

### L.5 证据来源与本节边界

- 本节证据：`evidence/rf-teaching/s2-46db-first-frame-amplitude-2026-10-19/`（离线定位：逐帧复算、缩放实验、合成分布；零板卡）与 `evidence/rf-teaching/s2-46db-baseline-pilot-2026-10-18/`（18 帧 pilot 原始产物）。
- 一致性由测试 `scripts/tests/test_rf_environment_frame_report.py` 守住（文档常量 ↔ 产品源码行 ↔ 冻结基线 ↔ pilot 产物）。
- 本节**不做**：改阈值（含把 `pulse_crest` 改成电平归一形式）、新基线、新候选、新资格、新安装、重采；这些属于建议 B/C，需独立授权与增量复审。
- 本节**不新增任何事后排除条件**：不排除/不重分类 `s00-normal-00`，不建立在特定缩放+加噪模型下得到的电平门，也不把 32.25% 当作板卡实际总体拒收率。
- 复核用的机器可读常量（由上述测试逐条核对；改这些常量，或把 L.2 的注解行改写成判据/验收前置条件，都会使测试变红）：

<!-- amplitude-support-doc-constants:begin -->
```text
reason_crest_branch=pynq_overlay/sw/rf_environment.py:381-382
reason_plateau_branch=pynq_overlay/sw/rf_environment.py:378
reason_token=AMPLITUDE_OUTSIDE_RULE_SUPPORT
threshold_owner=pulse_crest
crest_threshold_rule=pulse_crest=max(1.20,max(TRAIN crest)+0.08)
crest_threshold_line=pynq_overlay/sw/rf_environment.py:252
minimum_rms_codes=16
minimum_rms_codes_line=pynq_overlay/sw/rf_environment.py:253
rms_lower_bound_compare=pynq_overlay/sw/rf_environment.py:294
frozen_baseline_pulse_crest=1.9810145218306414
frozen_baseline_minimum_rms_codes=16.0
support_boundary_codes=303.1510618377535
support_boundary_dbfs=-19.60371712744841
noise_floor_codes_approx=89.0168420883521
synthetic_unknown_probability=0.3225
synthetic_trials=400
pilot_frame=s00-normal-00
pilot_frame_label=UNKNOWN
pilot_frame_reason=AMPLITUDE_OUTSIDE_RULE_SUPPORT
pilot_frame_rms_codes=268.91733449436634
pilot_frame_crest=2.0911514508063367
pilot_frame_complex_dbfs=-20.616126092740245
pilot_known_correct=8
pilot_required_known_correct=9
off_frame_declaration=stale-by-design
off_frame_op=rx.capture
changes_no_criterion=true
amplitude_boundary_is_exploration_only=true
amplitude_boundary_is_acceptance_precondition=false
amplitude_boundary_may_exclude_frames=false
synthetic_probability_is_board_rejection_rate=false
```
<!-- amplitude-support-doc-constants:end -->

