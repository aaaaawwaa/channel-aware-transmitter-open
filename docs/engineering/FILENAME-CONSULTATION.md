# 文件名规则咨询说明（指南 3.3.5.4）

编制日期：2026-10-31。

**复核状态**：已按本轮指南复核（2026-10-31；指南来源：Gitee 仓库 `Vickyiii/fpgachina26-amd` 的 `tracks/open-topic/GUIDE.md`）。3.3.5.4 的命名要求（纯英文小写/数字/下划线/连字符）、“推荐目录非强制、需在 README 给出对照说明”以及指南自身示例使用 `README.md`（3.3.5.2 还要求 `skill/README.md`）均与上轮 PDF 一致；本文 §3 的 4 条问题、§4 的咨询文字与 §附录 A 仍然适用，无需改写结论。

适用范围：《全国大学生嵌入式芯片与系统设计竞赛 2026 选题指南（AMD）》**3.3 自主选题赛道**，作品「AC820 / Zynq-7020 + AD9361 便携收发与接收分析平台」。

公开仓库：<https://github.com/aaaaawwaa/channel-aware-transmitter-open>（main 分支，最新提交见仓库首页）。

## 0. 本文用途与边界

- **用途**：整理指南 3.3.5.4 文件命名规则在公开仓库副本中的现状、惯例例外与待确认项；§4 的咨询文字可直接复制发送给赛道主办方，本文件也可作为提交材料的说明页。
- **边界**：本文**未发送任何咨询**，不代表官方意见；只记录仓库事实与需要确认的问题。本文不含个人联系信息，仓库链接为公开地址。
- **本轮不改名**：只做说明与咨询准备，不批量改名、不搬迁源码、不改构建引用（原因见 §2）。
- **本轮复核对齐**：已按本轮指南逐条核对 3.3.5.4 与 3.3.5.2，现有 4 条问题（§3）与长版文字（§4.2/§4.3）无需改写；本轮只新增“短版咨询文字”（§4.1）与数字口径说明（§1.3）。
- 相关入口：[README.md](../../README.md)（§目录对应关系、§赛事指南对照、§文件命名规则说明）、[GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md) §4、[COMPETITION-MATERIALS.md](COMPETITION-MATERIALS.md)。

## 1. 状态摘要（公开仓库副本）

统计对象：最终导出树（2026-10-31，1410 文件 / 172 目录），与公开仓库 main 推送内容一致。

| 项 | 结果 |
|---|---|
| 文件 / 目录数 | **1410** 个文件 / **172** 个目录 |
| 路径字符 | 全部为英文 ASCII；**中文 0、空格 0、其他特殊字符 0**（路径分段内仅 `A-Za-z0-9._-`） |
| 含大写字母 | **94** 个文件 + **6** 个目录 |
| 中文出现位置 | 仅出现在文件正文内容中（指南允许） |
| 前导点文件（Git 工具惯例） | `.gitattributes`、`.gitignore`、`.github/workflows/awg-bitstream.yml`，不计入"含大写"统计 |

### 1.1 含大写字母的文件分类（94 个）

| 类别 | 数量 | 构成 | 说明 |
|---|---:|---|---|
| A. 惯例/示例名 | **33** | `README.md` ×22（根、docs、docs/evidence、docs/figures、LICENSES、ml_training、sdr_testbench、report、skill、skills 等）、`LICENSE`、`NOTICE`、`SKILL.md` ×3、`Makefile`、`SHA256SUMS`、`AGENTS.md`、`CLAUDE.md`、`SHARE-MANIFEST.json`、`SHARE-README.md` | 开源/构建/工具惯例，或**指南自身的推荐结构示例**（3.3.5.4 示例使用 `README.md`，3.3.5.2 要求 `skill/README.md`） |
| B. 非惯例大写 | **61** | `ml_training/` 25、`sdr_testbench/` 12、`docs/` 19（工程文档 18 + `docs/golden/BASELINE.md`）、`LICENSES/` 3、`rf_overlay/` 1、`skills/` 1；按扩展名：`.md` 45、`.json` 11、`.txt` 4、`.v` 1 | 无惯例依据（作者自定大写风格），其中部分是测试向量/数据索引/工程文档 |
| C. 混合大小写测试向量 | **4** | `ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/{BPSK,QAM16,QAM64,QPSK}.symbols.json` | 已包含在 B 内；词干同时含大写制式标签与小写段 |

合计：33 + 61 = **94** 个文件；类别 C 是 B 的子集，不重复计数。

### 1.2 含大写字母的目录（6 个）

| 类别 | 数量 | 路径 |
|---|---:|---|
| 许可证目录（开源惯例） | 1 | `LICENSES/` |
| 数据/回放目录（目录名含大写制式标签） | 5 | `ml_training/candidate_a_replay_package/frames/01_syn_val_BPSK_to_QPSK/`、`.../02_syn_test_BPSK_to_QAM64/`、`.../03_syn_val_BPSK_confidence/`、`.../04_syn_test_high_snr_BPSK/`、`.../05_syn_test_correct_QPSK/` |

### 1.3 数字口径说明

- **本轮数字口径**：已按本轮最终导出树更新（**1410 文件/172 目录；94 文件 + 6 目录含大写**）。本轮新增 4 个工程文档（`FINAL-DESIGN-REPORT.md`、`POSTER-AND-PPT.md`、`PERFORMANCE-EVIDENCE.md`、`DEMO-AND-SUBMISSION.md`）、`docs/figures/README.md` 与 1 张白名单结构图。
- `GUIDE-CONFORMANCE.md` §4 与本文使用同一最终导出树口径：**1410 文件/172 目录，94 文件 + 6 目录含大写**。**以本文与最终仓库为准**。
- 本说明文件 `FILENAME-CONSULTATION.md` 自身含大写，已计入上述 94 个；如官方确认必须全小写，本文件同样适用统一改名规则。
- 逐路径扫描的原始记录与复核命令见**本地核查报告（不随仓库）**；本说明只引用汇总数字。
- 完整路径清单见 §附录 A。

## 2. 硬引用与改名风险（为什么本轮不批量改名）

改名不是纯文件操作：以下名称被代码、工具、构建脚本或许可声明直接引用，单独改名会破坏构建或校验。

| 名称 | 引用面（仓库内） | 直接引用示例 | 改名影响 |
|---|---|---|---|
| `rf_overlay/sw/board_app/vendor/SHA256SUMS` | 26 个文件、38 处提及 | [test_vendor_integrity.py](../../rf_overlay/sw/board_app/test/test_vendor_integrity.py)（读取清单并逐文件校验 SHA-256）、`host_probe.sh`、`scripts/rf_headroom_prepare.py` 及其测试、[tools/project_maintenance.py](../../tools/project_maintenance.py)（允许清单，保护第三方逐字节副本）、`.gitattributes` 注释 | 需同步改代码/测试/工具并重算；它同时是 ADI 第三方完整性清单的原始文件名。**本轮不改** |
| `rf_overlay/rtl/axi_ctrl_v1_0_S00_AXI.v` | 14 个文件、26 处提及 | [rf_overlay/build.tcl](../../rf_overlay/build.tcl) 的源码清单显式按文件名加入编译；模块名/接口名另出现在 testbench、C 侧地址宏与文档中 | 至少需同步 `build.tcl` 与文档；模块名可保留但可读性下降。**本轮不改** |
| `LICENSES/` 与 `LICENSES/*`（3 个文本） | 13 个文件、57 处引用 | [NOTICE](../../NOTICE)、[README.md](../../README.md)、[LICENSES/README.md](../../LICENSES/README.md)、[LICENSES/UNKNOWN-PROVENANCE.md](../../LICENSES/UNKNOWN-PROVENANCE.md)、工程文档与 `tools/` | 会破坏许可声明链（本项目混合许可的引用都指向这些路径）。**本轮不改** |
| `README.md`（22 个） | 多个文件、多处提及（随文档更新变化） | 文档互链；GitHub/Gitee 仓库首页约定；**指南 3.3.5.4 示例本身使用 `README.md`**；3.3.5.2 要求 `skill/README.md` | 需同步大量互链并放弃平台惯例。**保留，待官方确认例外** |
| `SKILL.md`（3 个） | 15 个文件、80 处提及 | 技能发现工具约定文件名；`skills/README.md` 与 [skill/README.md](../../skill/README.md) 已提供入口 | 改名会破坏技能发现约定。**保留** |
| `Makefile` | 29 个文件、70 处提及 | GNU make 约定（也识别小写 `makefile`）；文档普遍按 `Makefile` 指路 | 改名风险较低但收益小。**建议保留** |
| 测试向量/数据 JSON（如 `truth/*.symbols.json`、`real_campaign/fixtures/synthetic/valid/*.json`） | 被同目录 `manifest.json`、`index/*.json`、报告 JSON 与 `SHARE-MANIFEST.json` 引用 | 数据索引文件 | 改名需同步多个 JSON 索引。**本轮不改** |
| `SHARE-MANIFEST.json` | 导出工具生成，记录 **1408** 个文件条目的路径与摘要 | 导出/脱敏流程 | 任何改名都必须重新导出并重算。**统一由导出流程处理** |

补充：

- 改名前需考虑大小写不敏感文件系统（Windows/macOS）：同名仅大小写变化必须用两步 `git mv`（临时名→目标名），并在 Linux CI 验证。
- `.gitattributes`/`.gitignore`/`.github/` 属 Git 工具惯例（前导点），不在"含大写"统计内。
- 构建约定：`make -C`、`pio` 等工具不依赖 `Makefile` 的大小写；但 `README.md`、`LICENSE` 涉及 GitHub 平台识别，`SKILL.md` 涉及技能加载约定。

## 3. 需向赛道确认的问题（4 条）

> 以下问题对应 §4 咨询文字中的编号，每条都给出背景与需要官方给结论的点。

**Q1：惯例/示例名是否可以保留？**
背景：指南 3.3.5.4 推荐目录示例本身使用 `README.md`，3.3.5.2 又要求 `skill/README.md`；同时 `LICENSE`、`NOTICE`、`SKILL.md`、`Makefile`、`SHA256SUMS` 是开源/构建工具惯例名称。**需要确认**：这些名称是否允许保留大写，还是必须全部改为纯小写（例如 `readme.md`、`license`、`makefile`）。

**Q2：小写规则只约束新增/提交副本，还是现有文件也必须改？**
背景：仓库现有 94 文件 + 6 目录含大写，其中大量是历史文档、数据索引与许可证文本。**需要确认**：规则是否只针对本次新增/提交的文件，还是要求仓库内所有文件一律小写。

**Q3：若必须全小写，能否只在提交/发布副本改名？**
背景：原开发仓库的路径被历史报告、收据与构建引用；直接改名会牵动既有证据链。**需要确认**：是否允许只在提交/发布副本中改名并同步清单（原开发仓库保留原路径）；还是必须在原仓库一并重命名。

**Q4：推荐目录非强制，README 已有对照，是否还需实体重组？**
背景：指南 3.3.5.4 说明推荐目录"非强制"，采用其他组织方式须在 README 给出目录对照说明；仓库已在 README 给出逐项对照，未搬迁源码，并以 `skill/`、`report/` 作为映射入口（内容仍在 `skills/`、`docs/engineering/`）。**需要确认**：是否仍要求把内容实体迁入推荐目录（`src/sim/build/board/data/skill/report`），还是"README 对照 + 入口映射"即可。

## 4. 可直接发送的咨询文字

> 以下文字可直接复制发送给赛道主办方/组委会；未含个人联系方式，请按官方公布渠道发送。**本文件中的咨询文字均尚未发送。**
> 建议先用短版（§4.1）初次询问；长版（§4.2/§4.3）保留完整 4 条问题与背景，可在需要时一并发送。

### 4.1 短版（推荐先发；尚未发送）

> **状态：尚未发送。** 中文 5 句／英文 4 句，可直接复制；长版见 §4.2、§4.3。

#### 4.1.1 中文（短版）

```text
您好！我们是参加 2026 年全国大学生嵌入式芯片与系统设计竞赛 FPGA 赛道（AMD）自主选题赛道的队伍，作品仓库为 https://github.com/aaaaawwaa/channel-aware-transmitter-open 。

指南 3.3.5.4 要求目录/文件名为纯英文小写、数字、下划线或连字符，但该节推荐目录示例本身使用 README.md，3.3.5.2 也要求 skill/README.md；我们想确认 README.md、LICENSE、NOTICE、SKILL.md、Makefile、SHA256SUMS 这类开源/构建惯例名称是否可以保留。

如果必须全部小写，是否允许只在提交/发布副本中改名并同步清单（原开发仓库保留原路径），还是一定要在原仓库一并重命名？

另外，3.3.5.4 说明推荐目录「非强制」、采用其他组织方式须在 README 给出目录对照说明；我们已在 README 提供逐项对照（未搬迁源码），请问是否还必须按推荐目录做实体重组？

感谢！
```

#### 4.1.2 English（short version）

```text
Hello! We are a team in the FPGA track (AMD) of the 2026 National College Student Embedded Chip and System Design Competition; our repository is https://github.com/aaaaawwaa/channel-aware-transmitter-open .

Section 3.3.5.4 requires pure-English lowercase/digit/underscore/hyphen file and directory names, yet its own recommended-structure example uses README.md and Section 3.3.5.2 requires skill/README.md — may conventional names such as README.md, LICENSE, NOTICE, SKILL.md, Makefile and SHA256SUMS remain as they are?

If all names must be lowercase, may we rename only in the submission/release copy and synchronize the manifest (keeping original paths in the development repository), or must the original repository be renamed as well?

Also, Section 3.3.5.4 says the recommended structure is not mandatory and that a directory mapping must be given in README.md; we have provided an item-by-item mapping (without moving source code) — is a physical reorganization into the recommended directories still required? Thank you!
```

### 4.2 中文版（长版，4 问）

```text
您好！我们是参加 2026 年全国大学生嵌入式芯片与系统设计竞赛 FPGA 赛道（AMD）自主选题赛道的队伍。我们的作品仓库已按指南整理并公开：
https://github.com/aaaaawwaa/channel-aware-transmitter-open

我们就《选题指南（AMD）》3.3.5.4「仓库内所有目录名与文件名须使用纯英文（小写字母、数字、下划线或连字符），不得出现中文、空格或特殊字符」的理解，想确认 4 个问题，以免整理方向出错：

Q1. 指南 3.3.5.4 的推荐目录示例本身使用 README.md，3.3.5.2 也要求 skill/README.md。请问 README.md、LICENSE、NOTICE、SKILL.md、Makefile、SHA256SUMS 这类开源/构建惯例名称是否可以保留？
Q2. 该小写规则是只约束本次新提交或提交副本，还是现有惯例文件也必须全部改为小写？
Q3. 若必须全部小写，是否允许只在提交/发布副本中改名并同步清单（原开发仓库保留原路径），还是必须在原仓库一并重命名？
Q4. 3.3.5.4 注明推荐目录「非强制」、采用其他组织方式须在 README.md 给出目录对照说明。我们已在 README 给出逐项目录对照（未搬迁源码），请问是否还必须按推荐目录（src/sim/build/board/data/skill/report）做实体重组？

当前仓库所有路径均为英文，经核查无中文、空格或其他特殊字符；含大写字母的文件 94 个、目录 6 个，其中多为上述惯例名称。为避免误改破坏构建与既有引用，我们暂未批量改名，待您答复后再统一处理。感谢！
```

### 4.3 English version（长版，4 问）

```text
Hello! We are a team in the FPGA track (AMD) of the 2026 National College Student Embedded Chip and System Design Competition. Our project repository has been organized and published according to the guide:
https://github.com/aaaaawwaa/channel-aware-transmitter-open

We would like to confirm our understanding of Section 3.3.5.4 of the topic guide, which states: "All directory and file names in the repository must use pure English (lowercase letters, digits, underscores or hyphens); Chinese characters, spaces or other special characters are not allowed." We have four questions to make sure we organize the submission correctly:

Q1. The recommended directory example in Section 3.3.5.4 itself uses README.md, and Section 3.3.5.2 requires skill/README.md. Are conventional names such as README.md, LICENSE, NOTICE, SKILL.md, Makefile and SHA256SUMS allowed to remain uppercase?
Q2. Does the lowercase rule apply only to newly submitted files / the submission copy, or must all existing conventional files also be renamed to lowercase?
Q3. If all files must be lowercase, may we rename only in the submission/release copy and synchronize the manifest (keeping original paths in the development repository), or is a rename in the original repository required?
Q4. Section 3.3.5.4 says the recommended structure is not mandatory and that teams using another organization must provide a directory mapping in README.md. We have provided an item-by-item mapping in README (without moving source code). Is a physical reorganization into the recommended directories (src/sim/build/board/data/skill/report) still required?

All paths in the current repository are in English; we checked that there are no Chinese characters, no spaces and no other special characters. However, 94 files and 6 directories contain uppercase letters, most of them being the conventional names mentioned above. To avoid breaking builds and existing references by accidental renaming, we have not renamed in bulk and will unify the handling after your reply. Thank you!
```

## 5. 内部影响说明（若确认必须全小写）

### 5.1 两种可行方案

| 项 | 方案 A：保守（仅发布/提交副本改名） | 方案 B：激进（原仓库一并改名） |
|---|---|---|
| 改名范围 | 非惯例大写 **61 文件 + 6 目录 = 67 路径**；保留 33 个惯例/示例名 | 含大写 **94 文件 + 6 目录 = 100 路径**（含 22 个 `README.md`、LICENSE/NOTICE、`SKILL.md`×3、Makefile、SHA256SUMS、AGENTS/CLAUDE、SHARE-*） |
| 需同步 | `SHARE-MANIFEST.json` 重新生成（1408 条目）、文档互链、测试向量 JSON 索引、`LICENSES/*` 与 `axi_ctrl_v1_0_S00_AXI.v` 等硬引用、CI | 在 A 的基础上再加：GitHub 平台约定（README 首页、LICENSE 识别）、GNU make 名称、技能发现约定、ADI 完整性清单与 `.gitattributes` |
| 预计工作量 | 约 **1–2 人日**（机械改名 + 全量链接检查 + 冒烟测试） | 约 **3–5 人日**，回归面大 |
| 主要风险 | 漏改引用、Windows/macOS 大小写不敏感需两步 `git mv`、历史文档仍指向旧名 | 上述风险放大；`README.md` 等与指南示例冲突，且 GitHub/技能工具的行为可能改变 |
| 建议 | 若官方允许"提交副本改名"→ 优先采用 | 仅在官方明确要求"原仓库也须全小写"时采用；执行前冻结发布、备份并做链接/测试全回归 |

> 工作量为工程经验估算，不含等待官方答复的时间。

### 5.2 不改名也能做的合规动作（本轮已完成/可继续）

- README 已增加"目录对应关系 + 赛事指南对照 + 文件命名规则说明"；
- [GUIDE-CONFORMANCE.md](GUIDE-CONFORMANCE.md) §4 已同步为最终导出树口径（1410/94）；
- 本说明公开并列明待确认问题；
- 如官方要求改名，可先生成"旧路径 → 新路径"映射清单，再按方案 A/B 执行。

### 5.3 其他注意事项

- 仓库不含位流/HWH、原始 IQ、数据集与模型产物；改名不改变"不能直接复算历史指标"的证据边界。
- 第三方许可声明、`LICENSES/` 路径保持稳定，避免与 `NOTICE` 的引用链不一致；在未解决 5 个第三方 RTL 许可前，不建议改动许可相关路径。
- 改名后应重新运行自托管 CI 与文档链接检查，并重新导出、重算 `SHARE-MANIFEST.json`。

## 6. 结论

1. **现状**：公开仓库全部路径为英文；中文/空格/其他特殊字符 **0**；含大写 **94 文件 + 6 目录**，其中惯例/示例名 33 个、非惯例 61 个、混合大小写测试向量 4 个（含在 61 内）。
2. **本轮不做批量改名、不搬迁源码、不改构建引用**；保留惯例名称，等待赛道对 §3 的 Q1–Q4 给出答复。
3. **答复后的动作分支**：
   - 若允许惯例例外 → 在 README 与 `GUIDE-CONFORMANCE §4` 固定"保留清单"，本次咨询归档；
   - 若只要求提交副本小写 → 按方案 A 在发布副本执行，保留原仓库路径并同步 `SHARE-MANIFEST`；
   - 若要求原仓库全小写 → 按方案 B 执行，先冻结与备份，再全量链接/测试回归。
4. 本说明**不构成"已符合官方全部命名要求"的声明**；官方解释权归主办方。

## 附录 A：含大写字母的路径清单（94 文件 + 6 目录）

> 依据最终导出树（2026-10-31，1410 文件 / 172 目录），与公开仓库 main 推送内容一致。

### A.1 惯例/示例名（33）

```text
AGENTS.md
CLAUDE.md
LICENSE
LICENSES/README.md
NOTICE
README.md
SHARE-MANIFEST.json
SHARE-README.md
docs/memory/README.md
docs/evidence/README.md
docs/figures/README.md
ml_training/README.md
ml_training/arm_probe/README.md
ml_training/evaluation/tests/fixtures/README.md
ml_training/fourclass_seed_validation_eval/fixtures/README.md
ml_training/fourclass_test_vectors/README.md
ml_training/fourclass_test_vectors/hardware_amp60/README.md
ml_training/integration_candidate/README.md
ml_training/live_worker_smoke/README.md
ml_training/performance_candidate/README.md
ml_training/real_campaign/README.md
ml_training/round2/README.md
report/README.md
rf_overlay/sw/board_app/Makefile
rf_overlay/sw/board_app/vendor/SHA256SUMS
sdr_testbench/README.md
sdr_testbench/board_integration/README.md
sdr_testbench/gui_demo/README.md
skill/README.md
skills/README.md
skills/final-prj-rf-development/SKILL.md
skills/pluto-gnuradio-testbench/SKILL.md
skills/pynq-linux-development/SKILL.md
```

### A.2 非惯例大写（61）

```text
LICENSES/Analog-Devices-AD9361-BSD-style.txt
LICENSES/GPL-3.0.txt
LICENSES/UNKNOWN-PROVENANCE.md
docs/engineering/2026-10-30-ORGANIZATION-REPORT.md
docs/engineering/AI-COLLABORATION-CASE.md
docs/engineering/AI-COLLABORATION-RECORD.md
docs/engineering/COMPETITION-MATERIALS.md
docs/engineering/DELIVERY-INDEX.md
docs/engineering/DEMO-AND-SUBMISSION.md
docs/engineering/DESIGN-REPORT.md
docs/engineering/EVIDENCE-PACK.md
docs/engineering/FILENAME-CONSULTATION.md
docs/engineering/FINAL-DESIGN-REPORT.md
docs/engineering/GUIDE-CONFORMANCE.md
docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md
docs/engineering/PERFORMANCE-EVIDENCE.md
docs/engineering/POSTER-AND-PPT.md
docs/engineering/PROJECT-STRUCTURE.md
docs/engineering/RF-INTERFACE-TIMING.md
docs/engineering/SHARING-AND-CLEANUP.md
docs/engineering/SYNTHESIS-METRICS.md
docs/golden/BASELINE.md
ml_training/arm_probe/COMPATIBILITY.md
ml_training/arm_probe/MANIFEST.json
ml_training/arm_probe/arm/ARM-COMMANDS.md
ml_training/candidate_a_replay_package/HANDOFF.md
ml_training/data/NOTES.md
ml_training/docs/CONTRACTS.md
ml_training/fourclass_seed_validation_eval/HANDOFF.md
ml_training/fourclass_seed_validation_eval/fixtures/fixture_run/README.json
ml_training/fourclass_test_vectors/GNU_RADIO_INTERFACE.md
ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/BPSK.symbols.json
ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/QAM16.symbols.json
ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/QAM64.symbols.json
ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/QPSK.symbols.json
ml_training/fourclass_test_vectors/reports/LEAD-CHECK.md
ml_training/integration_candidate/DELIVERY.md
ml_training/integration_candidate/PRODUCT_INTEGRATION_DIFF.md
ml_training/live_worker_smoke/reports/LEAD-CHECK.md
ml_training/performance_candidate/DELIVERY.md
ml_training/performance_candidate/MINIMAL_DIFF.md
ml_training/performance_candidate/reports/LEAD-CHECK.md
ml_training/real_campaign/fixtures/synthetic/valid/BPSK.json
ml_training/real_campaign/fixtures/synthetic/valid/QAM16.json
ml_training/real_campaign/fixtures/synthetic/valid/QAM64.json
ml_training/real_campaign/fixtures/synthetic/valid/QPSK.json
ml_training/real_campaign/reports/LEAD-CHECK.md
rf_overlay/rtl/axi_ctrl_v1_0_S00_AXI.v
sdr_testbench/BOARD-INTEGRATION-PLAN.md
sdr_testbench/DSH-NEXT-PROMPT.txt
sdr_testbench/DSH-STAGE0-FIX-PROMPT.txt
sdr_testbench/GNU-RADIO-DEVELOPMENT.md
sdr_testbench/REVIEW.md
sdr_testbench/TEST-REPORT.md
sdr_testbench/board_integration/ATTENUATION.md
sdr_testbench/board_integration/BPSK-PARAMETERS.md
sdr_testbench/board_integration/CAPABILITY.md
sdr_testbench/board_integration/LEVEL-BUDGET.md
sdr_testbench/board_integration/WIRING.md
sdr_testbench/gui_demo/VERIFICATION.md
skills/pluto-gnuradio-testbench/assets/testbench/SNAPSHOT.json
```

### A.3 含大写字母的目录（6）

```text
LICENSES
ml_training/candidate_a_replay_package/frames/01_syn_val_BPSK_to_QPSK
ml_training/candidate_a_replay_package/frames/02_syn_test_BPSK_to_QAM64
ml_training/candidate_a_replay_package/frames/03_syn_val_BPSK_confidence
ml_training/candidate_a_replay_package/frames/04_syn_test_high_snr_BPSK
ml_training/candidate_a_replay_package/frames/05_syn_test_correct_QPSK
```

### A.4 混合大小写测试向量（4，已含在 A.2 内）

```text
ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/BPSK.symbols.json
ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/QAM16.symbols.json
ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/QAM64.symbols.json
ml_training/fourclass_test_vectors/hardware_amp60/outputs/truth/QPSK.symbols.json
```
