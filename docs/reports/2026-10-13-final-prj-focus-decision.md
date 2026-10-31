# 开发重心切换：原工程 DeepSeek Harness 与 final_prj

日期：2026-10-13。决策范围是开发任务与代码落点，不改变板卡当前发布或既有实验收据。

## 判断

**应暂停 DeepSeek Harness 在 `signal_generator` 中继续推进 HEADROOM runner 与后续 S2/S3/S4 的执行，并把新增实现、测试和规划集中到 `final_prj`。** 用户给出的新作品简介要求多制式射频接收、RRC/EVM 和受约束档位决策，范围明显大于原工程 2026-10-04 冻结的 BPSK 教学诊断收敛版。原 runner 的当前成果仍是新工程 P1 的输入，不应丢弃或在两个目录并行改写。

这是一项**任务暂停决定**，不是“原任务已经由工具确认暂停”的事实。当前可见的 DeepSeek Harness 桌面进程仍在运行，但没有支持的任务级暂停接口；结束整个应用进程可能丢失会话。原工程 `scripts/rf_headroom_research_run.py` 最后修改于 19:38，SHA-256 为 `0d34121da03f0a0557687707e0d80ea230fb6e963c732f18f2071b8a433a07a9`；截至本次检查未见 19:39 之后该文件的新改动。应用进程存在与任务正在执行不能画等号。

## 切换边界

1. **冻结原目录的新增工作。** `signal_generator` 保留其原始工作树、历史证据和板卡 b82 安装记录；不从 `final_prj` 回写源码，也不让两个任务同时控制板卡。
2. **所有新改动进入独立副本。** 新目录为 `<LOCAL_PATH>`，分支为 `codex/description-aligned-platform-plan`，源码副本已在提交 `32122bd` 固定，触摸照片补充于 `d410522`。后续任务以该目录为工作目录。
3. **继续使用原证据，但重新建立绑定。** 原工程 `evidence/` 未整体复制。涉及 b82、runner、S2 的每一项历史结论必须从原始收据和真实源码摘要回查；新工程的新运行包、安装或物理研究应生成自己的证据，不继承旧 runner 摘要的批准。
4. **板卡保持安全静止。** 本次重心切换不调用 `Overlay()`、不安装、不布防、不发射、不采集。任何未来真实 RF 会话仍须满足当时的只读预检、人工接线记录、一次性授权与独立安全读回。

**执行切换前置门：** 在 DeepSeek Harness 的任务界面确认原任务已暂停且不再写入 `signal_generator`，记录最后会话/审核门和源端最终 Git 状态及 runner 摘要。当前尚无这一确认，不能把“建议暂停”写成“已暂停”。`final_prj` 中独立的只读盘点、离线测试和规划可以继续；迁移源端证据、改指 Harness 工作目录或占用板卡须等该门通过。

## final_prj 的已知离线基线

以工作区内 pytest 临时目录运行 `rf_iq_signal`、`rf_bpsk_receiver`、`rf_rx_analysis`、`rf_rx_constellation`、`rf_cnn_runtime` 五组测试：**289 passed、5 skipped**。第一次尝试的 13 个 setup 错误由系统临时目录访问拒绝造成，指定工作区 `--basetemp` 后消失；不计作产品代码失败。此结果只证明这些离线模块在当前 Windows Python 环境下通过，不证明 ARM/板卡或完整工程通过。

下一步执行文件：[final_prj 详细实施计划](../superpowers/plans/2026-10-13-final-prj-implementation-plan.md)。

## 切换点记录（2026-10-14 只读盘点实测）

本节按实施计划 Task 1.0/1.1 第 2 条记录实测事实；**本次盘点对源工程只读，未写入、未同步、未覆盖任何文件**。收据：`evidence/rf-teaching/p0-switch-2026-10-13/switch-receipt.json`。

- **Gate P0-0 仍为 `NOT_CONFIRMED`。** DeepSeek Harness 没有任务级暂停接口；"建议暂停"不能写成"已暂停"。不能以桌面进程是否运行、也不能以本副本正在被使用来推断原任务状态。该门只能由任务界面的暂停确认（或其它可核查的停写证据）关闭。
- **源端 Git 状态**（`<LOCAL_PATH>`）：HEAD `f676cf69d7589107f476ef996807c95befa359da`，分支 `codex/s1-teaching-observation`，`git status --porcelain` 快照 SHA-256 `74CA8949…`，其中跟踪文件已修改 38 项、未跟踪条目 449 项。未提交改动只登记清单，不覆盖、不静默同步。
- **runner 摘要**：`scripts/rf_headroom_research_run.py` SHA-256 `0d34121da03f0a0557687707e0d80ea230fb6e963c732f18f2071b8a433a07a9`，92756 字节，最后写入 2026-10-13T11:38:24Z；配套测试 `scripts/tests/test_rf_headroom_research_run.py` SHA-256 `0367c6062f298a6b526fdb306e29d3cc1f512ab966fb86d365e678d0df770335`，最后写入 11:47:29Z。按 `headroom-runner-2026-10-12/receipt.json`（11:53:05Z）绑定的 runner/测试摘要与此一致，即该离线收据覆盖当前工作树摘要；但封存候选 b82 与安装收据绑定的是更早的候选摘要，**新板端增量 `588e84cd…` 不继承旧第三方批准**。
- **停写观察窗口**：源端被复制子树上最后一次写入为 2026-10-13T11:47:29Z，最新证据收据为 2026-10-13T11:53:05Z；截至 2026-10-14T05:56Z 的只读盘点未见新写入（静默约 18.1 小时）。这只描述观察窗口内的事实，不等于任务已按界面暂停。
- **副本一致性**：对 867 个副本源文件逐一比对 SHA-256，860 个与源工作树**逐字节相同**；2 个差异（`vivado2025.cmd`、`vivado2025.py`）**仅换行符不同**（源 LF / 副本 CRLF），逐行内容一致；5 个为副本专属规划文件。结论：本副本可作为源码基线，历史收据与原始 IQ 仍在源工程。
- **证据边界**：已核对 24 份 P1 必需封存收据在源工程可读并记录 SHA-256（`evidence/rf-teaching/p0-switch-2026-10-13/p1-evidence-manifest.json`）；整体复制 15.8 GB 级证据树不做。**按 Task 1.0 门禁，迁移封存件与原始 IQ 必须等 Gate P0-0 通过**，本阶段只准备清单。
- **本阶段允许的工作**：本副本独立只读盘点、离线测试（不修改产品源码与测试期待值）、规划与规格文档。板卡运行、协议/PL 改动、安装、发射、采集一律保持 `NOT_RUN`。

### Gate P0-0 关闭与证据迁移（2026-10-14）

- **Gate P0-0 = `CONFIRMED_BY_USER`**：在被问及原 `signal_generator` 任务状态时，用户选择"已暂停"。证据等级为**用户确认**（DeepSeek Harness 无任务级暂停接口，没有工具级证明），并与上一节两次同方法只读盘点（源端状态快照逐字节相同、runner 摘要与 mtime 不变、≥18.4 h 无写入）交叉支持。
- **证据迁移（按 Task 1.0 门：门通过后才执行）**：三轮共 **3,766 个文件 / 约 134 MB**，逐文件复制前后 SHA-256 核验相等：第一轮 562 文件/30.9 MB（24 份必需封存收据 + 6 个被失败测试引用的候选/证据目录）；第二轮 1,571 文件/61.0 MB（`executable-candidate-2026-10-12` 的封存子树等）；第三轮 1,633 文件/42.1 MB（`g1-qualification-2026-10-09`、`arm-runtime-budget`、两个安装收据目录、S1 部署与 pilot 目录）。清单：`evidence/rf-teaching/imported-2026-10-14-manifest.json`、`-round2-summary.json`、`-round2-detail.jsonl`、`-round3-detail.jsonl`。
- **按计划未复制**：15.8 GB 级整体证据树中的 `pytest-combined` 等巨量测试临时树、`*-scratch*`、`old-candidate-*`；位流/HWH/原生二进制与 `*.log` 不入 Git（保留在本机，由清单 SHA-256 核验），原始 IQ（`*.iq`，体量小）保留并纳入版本控制。
- **迁移后复跑**：见 `docs/reports/2026-10-13-final-prj-baseline.md` §9（新增小节，不改写本轮之前的原始退出码）。
