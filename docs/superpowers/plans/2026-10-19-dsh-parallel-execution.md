# DSH新会话并行执行计划

> 执行方式：遵循用户2026-10-19最新要求，DSH主代理与持久team成员实现、测试和执行；关键差分由独立Codex增量审查。本文不要求计划复审、固定变异数量或多轮全仓验证。运行前先核对最新工作树、旧任务和实际结果。

**Goal:** 取得一次真实Pluto阶段0结果，同时准备观测点新候选与后续帧观测处理，推进原S2电平波动问题。

**Architecture:** 两条独立任务线并行；每文件一名写者、每设备一名执行者。跨设备联调由一名执行者持有两端。Lead只协调、集成和提交。

**Tech Stack:** GNU Radio3.10.12/Radioconda、libiio、Python、AD9361板端C、现有候选/ARM/试部署工具。

## 1. 最小上下文与真实状态

先读AGENTS.md、docs/requirements/development-review-policy.md和本文。按任务读取原始报告，不全量重读旧聊天或复制历史证据树。

截至19:40的快照：

| 项 | 状态与接续方式 |
|---|---|
| 检查点 | HEAD 017640be，本地未推送；工作树仍可能变化 |
| 父证明 | s2-46db-parent-proof-review-2026-10-19/REVIEW.md 已APPROVED，仅覆盖显式格式2026-10-18-baseline的离线prepare父证明；不再重审 |
| 旧状态文字 | observer-planfix/planfix-closeout中的“03b9b883无路线”已被后续批准取代；保留历史，不重开已关闭任务 |
| 新观测点 | C实现和主体审查已完成；尚无对应新ARM ELF、新候选、新候选ARM资格或部署完成证据 |
| 阶段0 | 真实字段/单位/preflight/三窗口主体已修；79＋5离线测试通过；未见新stage0.json |
| 阶段0审查 | 9ee0f952已结束，CHANGES_REQUIRED；只剩B1/B2/B3增量，不能当成仍无限等待的审查 |
| 活动旧任务 | 131a00de-772e-42eb-9077-964125e363cf正在修B1/B2/B3，交接前新会话不可并写stage0.py |
| 原S2 | pilot8/9、passed=false，normal→CLEAR=NOT SATISFIED，约8.71dB跨会话电平差未定因 |

原始入口：

- 阶段0：evidence/rf-teaching/s2-46db-stage0-review-2026-10-19/REVIEW.md、s2-46db-stage0-fix2-2026-10-19/及当前stage0.py。
- 候选：evidence/rf-teaching/s2-46db-parent-proof-review-2026-10-19/REVIEW.md、parent-proof-closeout/NOTES.md、inwindow-observer/NOTES.md的构建组包部分（这些简称目录均带s2-46db与2026-10-19）。
- 技能：skills/pluto-gnuradio-testbench/SKILL.md；设备与具体命令见sdr_testbench/board_integration/。

用户仅有同轴线，无外置衰减器。旧主会话18:38用户说“我说的是接线完成”，19:30明确要求执行阶段0。新会话核对原始用户消息和是否有后续变更，沿用已覆盖范围，不自行把提示词中的接线字符串当成现场确认。

## 2. 接管旧会话

- [ ] Lead先确认131a00de是否活跃；若仍在写，A先只读接收，B立即做独立离线任务。
- [ ] 旧会话停止新增任务。现有实现者完成已开始的最小补丁与相关验证后，留下文件清单、测试、在飞作业、已用TX次数和下一动作，释放写权限。
- [ ] 若已有硬件作业在飞，只观察原作业收尾，不强杀、不重复派发。新会话明确接管后才写旧文件。
- [ ] 建立唯一短任务板docs/handoff/DSH-TASKBOARD.md：owner、write_scope、state、input/output、next_action、blocker、device_owner。每个里程碑更新一次，不写重复流水账。

## 3. team：Lead＋两个持久成员

| 角色 | 任务与写范围 | 交付/边界 |
|---|---|---|
| Lead | 任务板、集成、结果解释、唯一git index/commit操作者 | 不代写成员文件；两条线立即并行，谁先准备好谁先进入相应下一步 |
| A：Pluto | stage0.py、tests/test_stage0.py、fix2证据；确需同步时才认领planner.py/README | 接旧131结果、关闭B1/B2/B3、交Codex、运行一次阶段0；唯一Pluto owner |
| B：观测点/候选 | 新根evidence/rf-teaching/s2-46db-observer-next-2026-10-19/；确有缺口时新增独立capture_observation提取小件及对应测试 | 提取器、ARM/候选可执行准备包；现有C源、父证明/部署工具默认只读，复用既有批准 |
| 独立Codex | 审查实际新增R2差分，给明确范围的结论 | DSH不另派审查代理，不把DSH子代理改名为Codex冒充独立审查 |

有team能力用持久成员；只有subagent能力则使用两个可续会话替代。额外subagent仅用于短小、独立、可验收问题，完成即结束，不递归生成审查树。没有第三项实际工作，不为占满槽位制造文档任务。

并行边界：A修阶段0时B实现观测提取/准备候选；Codex审A时B继续；授权明确时A独占Pluto、B独占板卡做不改变PL/RF的ARM构建可以并行。跨设备联调时统一两端owner。只在同一文件、同一设备、同一Git index或真实产物依赖时串行。

## 4. A：最小修复并取得阶段0实测

- [ ] B1：在尝试tb.start前记录TX尝试；成功/失败都记录次数和尝试时长，不将已启动后的失败报0次；不确定是否启动时写attempted/UNKNOWN。
- [ ] B2：解除异常traceback对图对象的持有，停止并释放图/USB后再打开libiio关断。相关失败注入覆盖顺序与cleanup失败仍UNKNOWN；不把半实物清理验证扩成多轮发射。
- [ ] B3：LO关闭时设置并读回433900000Hz与-60dB，再允许启用；OFF_PRE不沿用旧2.45GHz。
- [ ] 复用原读位/解析/preflight/主体编排批准；只跑本次相关回归与直接反例，不重做79项全审。N1–N6非阻塞项后置，真正影响电平的符号调用点仍用直接反例覆盖。
- [ ] 修复者独占stage0.py/tests。readback.py、board_integration.py、radio_test.py、板端C和部署器不因本任务顺手重构。
- [ ] 将B1/B2/B3增量交Codex，等待期间B继续。对应差分获准后检查是否已有结果/在飞作业；没有且实际接线及有效授权仍适用，由A直接执行：

    Set-Location '<LOCAL_PATH>'
    & '<LOCAL_PATH>' -X utf8 -B sdr_testbench\board_integration\stage0.py --execute --path-confirmed 'Pluto TX -> Pluto RX direct coax; 60 dB internal; amp 0.1; scope and board disconnected'

参数保持：60dB内部TX衰减、幅度0.1、RX手动0dB、LO433.9MHz/+100kHz、一次TX/调制窗口≤2秒。无板卡、COM11、token、nonce。OFF窗口仍可能有本振泄漏，记录LO实际状态与总耗时。

- [ ] 自动保存stage0.json、三窗口IQ、ON/OFF目标频带差、频谱峰、计算型削顶、发射前读回与最终89.75dB/powerdown=1，不让用户代跑终端找目录。
- [ ] 失败保留并停，不自动升功率、重复发射或篡改通过标准。通过后再提出板卡TX1→PlutoRX的阶段A换线与预算；跨设备动作不自动继承阶段0授权，434MHz不证明原80MHz S2通过。

## 5. B：同时推进观测处理与新候选

- [ ] 优先查是否已有capture_observation提取器；有则复用，确实缺失才新增scripts/rf_capture_observation.py及对应单测。检查字段形状、逐帧配对、具名不可用，缺失/异常即停止、不补采；只用离线夹具，不改变原S2阈值/基线/分类。
- [ ] 此解析器若影响数据有效性门属于R2，按实际新增差分另交Codex一次；不等B完成才让A去实跑。
- [ ] 在新根下写输入清单、commands/、offline/；后续有产物再建native/、candidate/、arm/、prepare/、trial/，不预先填通过收据。
- [ ] 父候选使用已核验干净副本：evidence/rf-teaching/s2-46db-parent-proof-03b9b883-2026-10-19/work/candidate-03b9b883/03b9b883a61f29605be2c1f3817024d0；父安装证据使用s2-46db-baseline-deploy-2026-10-18/。不复制或改写原120文件，不再重跑410项父证明测试。
- [ ] 准备当前观测点C源的精确ARM构建输入、命令和构建收据路径；候选使用--carry-parent-software及cnn-bpsk-environment-v1，保持父n=20基线canonical dd794f0c…。
- [ ] 新ARM资格绑定新候选；旧父ARM收据不能代用。candidate==parent的路线验证不能拿去安装。
- [ ] 板端构建/ARM资格/安装等新增范围一次列清；已有有效授权沿用。缺权限只停对应硬件步骤，其它离线任务继续，不让A等待。
- [ ] 后续按新候选＋对应ARM资格＋一次适用Codex增量核对＋新鲜预检＋试部署/失败回滚读回执行。安装UNKNOWN只读定案，不重复安装；复用已有C代码批准。

## 6. Codex交接与精简规则

R0文档/注释作者自检，R1局部工具做相关测试，R2新增RF控制/判据/安装行为交独立Codex。DSH不另开规格、质量、安全、变异或封存审查代理；一份差分一次增量审查，修反馈只看新增部分。无固定变异配额，无文档改动后重跑产品全套要求。

关键包就绪时生成docs/handoff/CODEX-REVIEW-REQUEST.md，短格式：

    目标与范围：此次只关闭哪些真实阻塞
    基准与文件：提交号/明确文件；未提交差分单独列出
    差分：git diff或独立patch路径
    已验证：确切命令、退出码、简短结果及日志路径
    未验证：真实USB/清理等仍缺什么
    执行：准确命令、接线、次数/时长、成功条件、失败收尾
    请求：仅审新增行为；阻塞与非阻塞分列，是否准予此范围执行

用户向独立Codex会话发“请审查docs/handoff/CODEX-REVIEW-REQUEST.md，只看新增差分及真实阻塞”即可交接；没有自动连接能力，不编造自动调度，也不派DSH代理自签Codex结论。等待Codex不阻塞另一条线。

## 7. 本轮结束条件

两个可验收结果：A给出一次真实stage0终态（PASS或有用失败）和TX停止状态；B给出观测提取与新候选/ARM可执行准备包。

Lead只报告完成项、正在执行、唯一阻塞、下一动作。长任务每10–15分钟报告真实进展；超时说明卡在哪个命令，不静默扩任务。非阻塞排版、历史Git整理、全树归档和新框架建设后置。仅Lead按明确白名单提交，避免沙箱和大生成树入库。
