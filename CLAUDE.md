> 2026-10-30工程整理入口：先读`README.md`、`docs/engineering/PROJECT-STRUCTURE.md`及两套`skills/`。本文保留历史记录；“当前/最新”只代表相应日期，不覆盖较新收据。对外分享请使用`dist/`脱敏包，不直接分享本文件的开发原件。

# CLAUDE.md

**2026-10-14 final_prj 开发重心（当前，覆盖下方历史状态）：** 本轮以独立副本 `<LOCAL_PATH>`（分支 `codex/description-aligned-platform-plan`，HEAD `b03bbd2`）为工作区，按 `docs/superpowers/plans/2026-10-13-final-prj-implementation-plan.md` 执行 **Chunk 1（P0）**。**Gate P0-0 = `CONFIRMED_BY_USER`**：DeepSeek Harness 无任务级暂停接口，2026-10-14 用户在本会话中确认原 `signal_generator` 任务已暂停（证据等级 `USER_CONFIRMATION`，非工具级证明），并与两次同方法只读盘点交叉支持（源端 `git status` 快照逐字节相同、runner 摘要与 mtime 不变、≥18.4 h 无写入）。据此按计划迁移 P1 必需封存件：两轮共 **2,133 个文件 / 约 92 MB**，逐文件 SHA-256 核验相等（`evidence/rf-teaching/imported-2026-10-14-*.json`）；巨量 pytest 临时树按计划不复制；位流/HWH/原生二进制与 `*.log` 不入 Git 但保留在本机并由清单核验。本轮**仍零 RF、零新物理帧、未接触板卡**。已完成：① 切换收据 `evidence/rf-teaching/p0-switch-2026-10-13/switch-receipt.json`——源端 HEAD `f676cf69`、已修改 38 项/未跟踪 449 项、runner `0d34121d…`（11:38:24Z）与配套测试 `0367c606…`（11:47:29Z）、最新证据收据 11:53:05Z，观察窗口内无新写入（约 18.1 h）；② 副本一致性核对：867 个文件中 860 个逐字节相同，2 个仅换行符不同（`vivado2025.cmd`/`.py`），5 个为副本专属规划文件；③ `docs/requirements/brief-traceability.md`（简介 B1–B10 逐条追踪；逐句原文未随副本保存→`TEXT_SOURCE: TRANSCRIBED_PARTIAL`）、`docs/requirements/rf-status-object-contract.md`（`release_id`/`capture_id`/连接代次/配置修订/采样域/算法与模型版本/IQ 摘要/当前与历史/关闭状态的责任与失效规则）、`docs/requirements/acceptance-checklists-p1-p6.md`（六类门：离线/ARM/数字回环/有线物理/真人/发布）；④ 离线基线 `docs/reports/2026-10-13-final-prj-baseline.md`：五组核心 **289 passed / 5 skipped**（复现历史值）、`pynq_overlay/sw/tests` **2040 passed / 5 failed / 5 skipped / 11 errors**、`scripts/tests` **208 passed / 62 failed / 1 skipped / 80 errors**，其中 **155 项为源工程证据未复制（直接）、3 项为其次生 `pytest.raises` 消息不匹配、0 项产品代码失败**；C 主机套件全部通过（daemon 450、core 98、protocol 58、state 64、transport_policy 64、sleep 22、iq 40、daemon_main 92、platform_rf 39、headroom 148＋owned 10、selftest 5 含两条自带负例、vendor 10 files），`make host-test` 在 cmd 下因 `mkdir -p` 必失败（`rf_overlay/sw/board_app/-p` 时间戳 2026-10-06，早于本轮），把 Git bash 的 `sh.exe` 放进 PATH 后同一命令 exit 0；⑤ P2 规划规格 `docs/superpowers/specs/2026-10-13-rf-tx-mode-contract.md`（冻结 PRBS/映射/成形/量化/拒绝条件；记录 QAM64 轴表**非** Gray、幅度为**帧内峰值归一化**、损伤噪声用 `default_rng` 不可跨平台参考、量化越界是**拒绝**而非削顶）。**仍未做**：Chunk 2 及之后的任何实现、ARM/板卡/Vivado、EVM 与 QPSK/QAM/RRC 接收、档位注册表与策略、统一发布清单；46 dB 研究、S2 正式 108+12、真人、S3/S4 全部 `NOT_RUN`。**下一顺序**：进入 Chunk 2（Task 2.1 板端运行包规格 → 干净子进程与负控 → 独立 delta 审查 → 只读真板预检），板卡物理动作仍单列执行门。

**2026-10-12最新：可执行候选＋隔离ARM G1＋安装包封存三个串行任务已全部执行完毕；真实安装与真实研究仍未开始。** 独立合并关键审查（非实现者的独立agent）**APPROVED**：绑定候选b82b141e355b4cb1833969383a409c79、46项software、harness/qualifier/runtime-runner/supervisor-qualifier与6项REVIEW_TOOLS摘要，报告sha256 88d74459…；`native_lease_regression_passed=true`由审查者自行重建原生桥（产物sha256逐字节相同）并对生产C施加两处变异（detach不保留租约、acquire不置session.lease_held）证明该回归不可伪造。**隔离ARM G1通过**：同帧4窗完整计算与8场景/96证据Host/ARM判等均为空mismatch，100次完整计算**P95=278.835ms**（≤300ms）、RSS 31704KiB（≤64MiB）、native CNN、exit 0；ARM假supervisor `simulation=true/actual_install=false/systemd_touched=false`，success(exit0)与installer-failure(exit7)两例均observer超时后存活、重复run/dispatch被拒、异常boot判UNKNOWN（补上主机跳过的真实Linux进程用例）；最终只读安全检查PASS：仍b67、safe、双服务unit与发布相符、root rw、本次启动0存储错误。**安装包已封存**：`install-bundle/`（candidate.tgz 99成员＋install.sh＋install_state.py＋job.py＋bundle-receipt）以b67为父版本、绑定board-recovery恢复证明，`development-checkpoint.json`＋`stage-checkpoint.json`、`--verify`通过。**仍未做**：真实安装/布防/发射/采集（本阶段板卡仅/tmp只读与离线计算，零RF、零新物理帧，物理累计67）、真实HEADROOM研究、正式S2/S3/S4；真实runner仅有设计文档`docs/superpowers/plans/2026-10-12-real-headroom-research-runner-design.md`（未编写未运行）；旧b67安装UNKNOWN保留、MMC根因NOT_ESTABLISHED、完整FS修复NOT_RUN。下一顺序：存储健康确认→单次安全安装与独立读回→实现真实runner并经人工接线/一次性token执行一次会话最多五尝试→独立TRAIN/VAL与18帧复验→正式108+12/真人→S3→S4。证据`evidence/rf-teaching/s2/executable-candidate-2026-10-12/`（`handoff-progress.json`为执行前17:26主机快照，最终以`stage-checkpoint.json`为准）。下方当前均为历史。

**2026-10-12 存储结构审计＋部署恢复机制（当前）：** 接续复盘报告按优先级推进（存储→部署机制→真实研究），全程对板卡**只读**：无上传/安装/重挂载/修复写入，未重装，发布仍b67a8aaa（只读读回current与active-release相等、双服务active/running/NRestarts0、无内核存储故障），新增物理帧0、开发物理累计67。**存储**：同一启动运行9,757s，本次启动零MMC/SDHCI/ADMA/I-O/ext4错，ext4超级块state=0x1(clean)/error_count=0，root与/boot均rw，SD 14.56GiB(serial 0x131, 11/2025)。**启动分区实为FAT16**（130MiB、4KiB簇、保留8扇区、2×136扇区FAT、512根目录项、标签PYNQ、mkfs.fat），此前按FAT32取偏移导致第二份FAT越界失败（失败日志保留）。两次只读抓取各136,314,880字节**逐字节相同**（sha256 0b809abc…），归档为gz并记录双哈希。新增主机侧离线结构审计`scripts/fat_structure_audit.py`＋22测试（15类注入缺陷全被抓，含曾致遍历死循环的自引用目录），真实镜像判定**consistent**：0 error/0 warning、两份FAT相同、33,241簇中1,670已分配全可达/0丢失簇、7个启动文件与备份**逐字节相符**(7/7)、由归档gz重放报告逐字段相同。**这不是fsck.fat**（板卡有e2fsprogs但无dosfstools；主机亦无），根ext4未e2fsck，物理离线检查需读卡器、装dosfstools需授权，均NOT_RUN。**关键澄清**：dmesg`FAT-fs (mmcblk0p1): Volume was not properly unmounted`=Linux v5.4把FAT16引导扇区偏移0x25的`fat16.state`当挂载状态位（`fat_set_state`脏时提前返回→**粘滞**），实测0x01，与硬断电一致，**非控制器故障**、非损坏证据，需FAT检查清除。**部署机制**：`rf_headroom_deploy`新增追加式`attempt.jsonl`（每阶段fsync＋撕裂尾行修复）、执行/观察分离（超时后**新开只读会话**读回，绝不重装）、六类结局、安装前内核存储故障或预检不可用→`ABORTED_STORAGE_FAULT`/`ABORTED_PREFLIGHT_UNKNOWN`零写入；10测试（真实bundle＋假板卡）＋原15门禁，`scripts/tests` **320 passed**（原288，无回归）。未变：46dB研究仍被`lease_held`谓词阻塞（需新候选+新资格+新安装）、真实runner不存在、人工接线/token必需；S2正式/真人/S3/S4仍NOT_RUN；MMC根因NOT_ESTABLISHED。报告docs/reports/2026-10-12-storage-audit-and-deploy-recovery.md；收据evidence/rf-teaching/s2/storage-maintenance-2026-10-12/stage-checkpoint.json（38证据，`--verify`通过）。下方“当前”为历史。

**2026-10-12日志复盘/板卡恢复（历史）：** 日志会话5c2d2874于10:37–11:35；门禁案例名/浮点字节等价及stage前缀错误已修，后续绑定加强15测试，本次复查15PASS。11:08真实安装b67a8aaa，240s后UNKNOWN；不能按旧报告NOT_RUN或假定回滚。COM11直接观察mmc0 interrupt timeout、SDHCI ADMA Err；用户重新上电后恢复SSH，实际14.6GiB SDHC卡。独立核验current/active-release均b67a8aaab0c5904822580c0647b96fd0，1.0.4/protocol1.6、manifest/software完整、实际systemd unit与发布相等，双active/running/NRestarts0（本次启动），未布防/PL io_ok/TX source=false/RX armed=false，根分区rw，当前启动约647秒日志无新MMC/I/O错。原安装UNKNOWN保留，新增恢复后安全启用收据，不重装。/boot FAT提示未正常卸载（其含义已由本阶段澄清为粘滞挂载状态位），7常规启动文件6.8MB已备份至电脑；镜像无FAT检查工具，fsck只读尝试127，完整FS检查NOT_RUN，不能称SD已彻底修复/长期稳定。冻结46生产模块未改，新增RF/研究初始化0，物理累计67。下一顺序存储稳定/离线FAT维护→持久部署状态与冻结关键审查→真实runner→一次会话最多五次余量研究→独立TRAIN/VAL+18复验→正式108+12/真人→S3/S4。不单纯加长超时或重新安装。报告docs/reports/2026-10-12-session-review-and-board-recovery.md；证据evidence/rf-teaching/s2/board-recovery-2026-10-12/recovery-checkpoint.json。历史G1所绑定文档已有后续变化；本次未改旧阶段收据，46项生产软件与候选一致；下方“当前”为历史。

**2026-10-11/12 候选已安装；真实余量研究被实时能力谓词阻塞（当前）：** 接续核对确认Claude开发阶段完整（收据passed/installed:false/physical NOT_RUN、ARM 270.793ms、314.963ms失败保留、历史1.0.3准备器仍BLOCKED）。安装路径有四处**从未执行过**的缺陷：D1 `CHAIN_CASES`用实现前旧名（应为full-five/cancel/off-false-positive/rails/missing-capability/wrong-id/no-iq/partial-iq）、D2 要求host/ARM产物逐字节相同（96份仅3份不同，全是最后一位浮点，421处差异最大4.44e-15，资格器判据为`chain_parity_mismatches`）、D3 暂存目录前缀`rf-headroom-`不被已审查安装器接受（exit 2、板卡零改动、只读证实）、D4 独立复核指出替代检查只看形状（伪造96摘要+8假键可通过）→ 已改为绑定`chain-arm.json`/`chain-host.json`引脚摘要并要求两次运行passed/simulation/无硬件。修复后门禁15测试、`scripts/tests` 288 passed、4个有效变异全被抓。**候选b67a8aaa已安装并核验安全**：`cnn-bpsk-headroom-v1`、daemon 1.0.4/minor6、safe、未布防、TX源未选、RX未武装、双服务active/NRestarts0。安装期客户端240s期限先到→UNKNOWN，**未重试**；板卡随后自行重启（journal干净poweroff→boot，uptime 203s且75s内+75s=稳定；原因NOT_ESTABLISHED，工具链无断电逻辑，请现场确认），/tmp暂存目录消失致安装器post-state缺失，状态由只读`verified_state`读回定案。**核心发现：真实46dB研究在当前候选上不可能执行**——守护宣告几乎全对（minor6、五项操作、预算1/5、OFFLINE、idle），唯一失败项`lease_held=true`，因为`rf_daemon_serve`在整条连接期间持租约(:2494/:2530)，而读能力的客户端正是持有者，`rf_headroom.capability_available`(:151-152)却要求它为false，`HeadroomController.run`(:540-542)又在lease内判定→每次必拒；全部假硬件用例注入的peer都报false故测试看不见。**本轮不修**（谓词/原生合同属已审查安全面，需新候选+新资格+新安装）。真实研究runner仍不存在（runner.py声明无socket/PL/UI/RF），且需人工接线确认与token。新增真实帧0/初始化0、开发物理总67。报告docs/reports/2026-10-11-headroom-install-gate-and-preflight.md；收据evidence/rf-teaching/s2/headroom-research-2026-10-11/stage-checkpoint.json（63证据）；安装定案install-verification.json。下方为历史。

**2026-10-11 HEADROOM-1开发/G1完成（历史）：** Claude最新主会话19:45:07中断，Python与资格封存未完成；Codex接续关键控制/证据修复。新候选b67a8aaab0c5904822580c0647b96fd0（1.0.4/minor6）58 native/46 software通过绑定；原生ARM仅构建真实daemon，假硬件HEADROOM148/0、owned10/0。Python整回归2313/4skip/1测试线程警告保留，后续批量对齐增量256PASS、独立193PASS；127种子及4窗完整分析严格等价。首次ARM314.963ms失败保留，未改300ms预算，批量127×63参考计算后新候选ARM100次P95 270.793ms、RSS31524KiB、原生CNN；Host/ARM完整分析及8案例/96证据流程判等PASS。用户要求减少审查：一次合并critical APPROVED＋必要数学增量，不再重复全审。新候选未安装，真实HEADROOM/初始化0、开发物理总67；安全只读确认仍adfac、未布防/TX/RX关闭/双active/NRestarts0。下一项安全安装/读回及一次会话最多五次46dB研究；之后独立TRAIN/VAL＋18复验→正式108物理＋12离线/真人→S3→S4。报告docs/reports/2026-10-11-headroom-contract-final.md；收据evidence/rf-teaching/s2/headroom-contract-2026-10-11/stage-checkpoint.json。下方“当前”为历史。

**2026-10-10 HEADROOM合同准备/Claude接续（当前）：** 最新Claude会话在20:07中断，已产prepared与28测试，但规范/质量报告及最终收据未完成。本轮接续：完整候选/source51/software/baseline/payload核验，准备器真实verify通过；补3个测试覆盖缺口，最终30 passed、三项隔离变异有效失败、规范增量和质量均APPROVED；封存脚本修复审查schema衔接并核报告原正文/摘要及测试日志/source。生产源码、候选、基线、阈值、默认等待均未改。确认原生/UI/client物理合同精确40dB；configure46再确认40会被真实重设40，故**46dB研究合同未实现、execution_ready=false，不得scripts-only旁路实测**。本轮HEADROOM包零RF/零初始化；安全只读检查当前adfac、未布防/TX/RX关闭、双active/NRestarts0；Claude续审阶段无新增板联系。G1旧候选PASS、G2 STOPPED_CLIPPED、开发累计67，S2正式108＋12/真人及S3仍待。下一任务：独立HEADROOM-1合同贯穿唯一控制链与native预算/同帧实际双TX46dB读回，旧40dB回归不变，完成新候选资格后执行最多一次会话五次研究；不能继承旧G1。报告和流程 docs/reports/2026-10-10-headroom-contract-preparation.md；收据 evidence/rf-teaching/s2/headroom-preparation-2026-10-10/stage-checkpoint.json；详细spec/plan 2026-10-10-s2-headroom-research-design.md / 2026-10-10-s2-headroom-contract-preparation.md。下方“当前”为历史。

**2026-10-10 G2接续（当前，覆盖下方NOT_RUN与旧current）：** G1 PASS保持。用户要求继续后，补执行授权只读检查：首次上传名inspect.py遮蔽stdlib导致导入失败，保留日志；新目录改rf_state_check.py、同源码重试safe=true。51 native/17 handoff与准确包校验通过，G2相关69测试通过。候选**adfac1f14c3cdb379cb92569a21a9054已安装**、daemon1.0.3/minor5由实测timing回读验证，回退692904251e024a1254aecee679c6322a。一次G2最多8尝试声明实际3尝试/1初始化会话后CLIPPED停止：normal0/1000us各无rail、0/138BER、CLEAR；1000us实测1106.709us；narrowband0us有4个I=-2048、无点/BER、INVALID。无narrowband1000us或第二会话，**等待对照证据不足/G2未通过**，不称等待有效或无效。3原IQ完整下载与共享重放全部一致，解除ACK及最终独立safe=true、unarmed/TX/RX关闭、双active/NRestarts0；无重试或补采，开发物理累计67。空闲gain index76/mode原码2不证明窗口内增益恒定。源60→100码峰值提示接收余量调查，根因未证明。报告docs/reports/2026-10-10-g2-wait-comparison.md，收据evidence/rf-teaching/s2/g2-execution-2026-10-10/stage-checkpoint.json；新HEADROOM-1准备方案docs/superpowers/plans/2026-10-10-s2-receive-headroom-investigation.md（拟只改TX衰减40→46dB、最多1会话/5尝试，当前未实施/未实采）。冻结源码、模型、基线、阈值、默认等待未改；S2正式108＋12/真人及S3仍待。原G2声明停止，不再重跑凑8帧；当前下一项为新的单变量接收余量调查准备。

**2026-10-10 Codex 接续复盘（当前，覆盖下方G1 PARTIAL）：** G1资格补证与独立C规范/质量、Python/parity复审均通过，**G1 PASS；G2安装/实采 NOT_RUN**。核验Claude10月9日51 native/17 handoff零漂移、ARM123/0、六变异、settle22/0及四类五阶段判等；原作者质量verdict仍null，本轮不改写其历史收据。修复判等工具zip截断误报，65新＋17既有＝82回归通过。经用户明确授权，AC820 `/tmp`隔离补测：同正常IQ关闭参考host/ARM五阶段一致、128点/201符号、无BER；完整socket背压＋fake PL FAULT/owned-core收尾ARM16/0，两信号至reap2/3ms。未改冻结native/生产分析模块、未安装/Overlay/新增RF；开发物理累计64不变。G2重新prepare绑定当前692904251e024a1254aecee679c6322a及真实父安装收据，候选adfac1f14c3cdb379cb92569a21a9054仍未安装。最终live PL安全读回已授权但自动审批额度耗尽，调用未执行，不声称新safe=true；G2前仍须新鲜预检。报告docs/reports/2026-10-10-claude-progress-and-closeout.md，绑定收据evidence/rf-teaching/s2/claude-progress-2026-10-10/stage-checkpoint.json。后续G2最多8尝试/2会话→18帧开发稳定→S2独立冻结108物理＋12离线/真人→S3人工建议或拒绝→S4演示交付；核心余量约5–8有效工作日（链路稳定前提），可选两档收益另立。用户取消专门触摸计时持续有效。

**2026-10-09 Codex G1复核（覆盖下方进度结论）：** G1仍PARTIAL，G2 NOT_RUN。51 native/17 handoff零漂移，准确ARM ELF与候选adfac1f14c3cdb379cb92569a21a9054验证通过；历史Linux日志74/0属实，但退出harness的fd未初始化且忽略read结果，原5ms退出证据无效。还须补真实socket背压下FAULT/owned-core关闭顺序、完整行后真实停止派发guard、两信号时点矩阵及慢读/EINTR固定期限、make真实exit和最终版本双复审。同IQ只证明环境parity；完整base/CNN/BPSK/星座/environment结果需补跨平台判等。100次完整计算独立重算P95=290.805352ms（通过300ms门槛），RSS30816KiB，资源证据可保留。先补G1再做G2最多8尝试，无需放弃已验证产物或重跑全部基准。本轮只运行本地证据校验，未连板/安装/新增RF帧；生产源码未改，S2开发累计64不变。报告docs/reports/2026-10-09-claude-g1-review.md；证据evidence/rf-teaching/s2/claude-g1-review-2026-10-09/review-receipt.json。

**2026-10-08 G1资格验证（最新，覆盖下方）：** 按 `docs/reports/2026-10-08-g1-agent-checklist.md` 执行 G1。**本轮接触板卡**（ARM 只能由板端原生 gcc 构建），但**未加载 Overlay、未安装或替换任何发布、未 MMIO/SPI、未武装 core、零 RF**；`current` 仍为 `692904251e024a1254aecee679c6322a`，双服务 active/NRestarts0。**结论 G1 = PARTIAL，不宣称 PASS，不进入 G2。** **步骤0**：51 native + 17 handoff 输入逐文件 sha256 **零漂移**。**步骤1**：隔离原生 ARM 构建（独立证据目录，未覆盖 `rf_overlay/out/rf_daemon`），`BUILD_RC=0`、vendor 10 files passed，板端 armv7l gcc9.3.0：transport **64/0**、state 64/0、daemon **445/0**、startup 92/0、platform 39/0、core **98/0**、iq 40/0、protocol 58/0、另 5/0；`test_selftest` 两条 FAIL 为**自带注入负例**（`SELFTEST HARNESS OK`，exit 0）；**同一隔离目录**另跑 `make host-test-sleep` **22/0**（清单要求）；ELF sha256 `4246903635ceee26…`，`verify_observer_build` 通过 = daemon **1.0.3/major1/minor5**，构建收据输入与冻结 51 项逐项相等。**步骤2**：新增 Linux harness `evidence/rf-teaching/s2/g1-qualification-2026-10-08/harness/test_rf_transport_linux.c`，**直接 `#include` 生产 `rf_daemon_main.c`**（改名 `main` + `--gc-sections` 丢弃）——不复制算法、不改冻结源码；板端 **74 PASS/0 FAIL**（armv7l，Linux 5.4.0-xilinx-v2020.2），源码 sha 本地/板端一致 `88df90ea…`、二进制已拉回 `harness_linux.board`（`ea6bc4eb…`）：真实 pipe/fcntl/sigaction 与无 `SA_RESTART`、7 个 syscall 逐个注入失败不泄漏 FD、cleanup 恢复 handler 且幂等、9KB 跨 partial send 逐字节相同、对端不读时在 **1000ms 期限**而非 30s 超时上放弃、**真实 SIGTERM/SIGINT** 均远早于期限抢占阻塞发送、阻塞 recv 中（两种信号）/已消费分片后/40 次全窗口采样均及时 clean-close、真实 fork 子进程信号后被 reap **5ms**（<5s 预算）、10 次中断的 settle 等待仍是 ~200ms。**质量审阅的变异抓到一个真缺口（已修）**：信号送给正在 poll 的线程时 poll 返 EINTR，循环由**旗标前置检查**返回 0，**pipe 可读分支从未执行**——即 self-pipe 的功效当时零证据；补 `signal_from_other_thread`（主线程屏蔽信号、辅助线程解除并 kill，poll 不被中断，只剩 pipe 能结束等待）。现三个变异全部被抓住：read pipe 分支→**73/1**、send 只 poll socket→**73/1**、正对照 handler 不写 pipe→**70/4**。**未覆盖**：真实背压 socket 下的 **FAULT/owned-core 收尾组合顺序**未重跑（该顺序由 `test_rf_daemon` 445 项以**注入** transport 覆盖，且该套件已在本轮板端 ARM 构建 445/0 通过）——清单八行中唯一剩余缺口。**步骤3**：用该 ELF 构建候选 `adfac1f14c3cdb379cb92569a21a9054`（父 `c3bf915c…`，`cnn-bpsk-environment-v1`，**未安装**），板端隔离运行：主机/ARM 同 IQ **`parity_mismatches: []`**、100 次完整路径 **P95 290.81ms**（≤300ms）、RSS **30816KiB**、`native_cnn:true`。收据 `evidence/rf-teaching/s2/g1-qualification-2026-10-08/stage-checkpoint.json`，报告 `docs/reports/2026-10-08-g1-qualification.md`。**NOT_RUN：** G2 安装与 8 帧物理对照、S2 正式 freeze/108+12、真人、S3；削顶根因仍未证明。

**2026-10-08 G1前transport补齐（覆盖下方历史）：** 用户授权“你尝试补齐，然后后续我让agent跑G1”。两个P1已离线闭合：pipe/两端fcntl/两次sigaction失败拒绝启动与清理；MSG_DONTWAIT发送＋socket/self-pipe poll，固定每次write_all整体1000ms期限，partial/EINTR不刷新，停止回既有收尾。新增rf_transport_policy.c/.h及测试，修改main/Makefile/daemon集成测试；core/daemon算法/协议/PL/vendor/gain/Python业务未改。新鲜完整C make exit0：policy64/state64/daemon445/main(startup)92/platform39/core98/iq40/protocol58/sleep22，均0FAIL；selftest内层2FAIL为预期负例，harness OK；vendor10；相关Python78通过。三有效变异均编译成功、测试失败。51 native/17交接工具输入冻结；规范与质量独立复审均APPROVED_FOR_G1_QUALIFICATION。**ARM/真实Linux signal/socket/G1/G2 NOT_RUN；本轮未连板/安装/实采。** 1秒发送期限、5秒服务退出与5ms采集后测量资格不同，后者不是实时TX硬截止。S2开发帧累计64不变，正式freeze/108+12/真人及S3未完成，根因未证明。本轮源码1.0.3/minor5不代表已安装，历史安装692904251e024a1254aecee679c6322a（1.0.2/minor4）。下方round3误标C目标，正确是platform39/state64/daemon_main92。报告docs/reports/2026-10-08-g1-transport-closure.md，交接docs/reports/2026-10-08-g1-agent-checklist.md，收据evidence/rf-teaching/s2/g1-transport-closure-2026-10-08/stage-checkpoint.json。

**2026-10-08 G0离线闭合round2（最新，覆盖下方）：** 按接续计划完成 **0.2收尾 / 0.3 / 0.4 / 0.5 / 0.6**，全部离线，零RF、不安装、不连板。**最终回归全绿**：board软件**1874通过/4跳过**、tools **155**、host除ML **902**、ML文件 **54**；C `test_rf_daemon` **417/0**、`test_rf_core` **98/0**、`test_rf_sleep` **22/0**、startup 92/0、`make exit=0`、vendor integrity 10 files passed。**本轮内容**：零档trace断言加固（先请求device.info、存在性与数值分开）；终止旗可观察性（新增注入 `cfg.should_stop`，板上sigaction去SA_RESTART + conn_read停止EINTR重试，使受控终止走普通确认收尾而非等30秒超时）；八帧**硬上限**（档位校验改为批准集合相等——旧范围检查会放行`(0,1000,2000)`并产出12帧；帧数不随参数变；档位顺序按会话对调；逐帧记录requested与actual，实际不足即判不合格并停止且**保留原IQ**）；版本/类型/每帧证据门禁（修掉"minor-5零档省略字段被接受"的反例；严格整数类型拒绝`False`/`0.0`；显示环境与保存记录纳入验收并经既有`read_record`重读；**每帧同IQ数学复验**，无baseline拒绝启动）；新建宿主driver `scripts/rf_capture_settle_run.py`（门禁1.0.3/**minor5配对**、baseline必需、上传哈希守卫先于任何sudo、inspect先验SHA再以root执行；旧1.0.2四帧driver原样保留）。**两个独立审阅**：质量**APPROVED**（PASS描述集合差分**0条断言被删**，唯一消失的旧描述是被加强版替换；sw/host/ml集合完全相同）；规范**首轮NOT APPROVED**，其唯一阻塞项**是我自己的流程失误**——`test_wait_campaign_rejects_any_design_that_is_not_the_approved_one`因少传baseline而**通过的原因是错的**（baseline守卫排在档位检查之前），**删掉档位检查仍49条全绿**；我加守卫后未重跑旧变异，真证据悄悄失效。已修并复验（变异→`DID NOT RAISE`，此前全绿）。**新增教训已写入交接文档**：加新守卫后必须重跑旧变异，且`pytest.raises`必须match到它名字所指的特定消息。**仍未闭合**：`rf_daemon_main.c`的sleep/终止/sigaction在`#ifndef PLATFORM_HOST`区，**未经任何ARM编译**；F2与RX半边在当前可达路径上不可观测；计划要求的"超长等待交易期限与偏差报告"未做。收据`evidence/rf-teaching/s2/settle-closure-20261007-v1/stage-checkpoint-round2.json`，源码清单`g0-round2-sha256.txt`（20文件，另冻结于`frozen-sources-round2/`）。**NOT_RUN：** native ARM构建、安装、任何RF采集、8帧对照、S2正式freeze/108/12、真人、S3；削顶根因仍未证明。

**2026-10-08 G0 round-3 离线闭合（最新，覆盖下方）：** 按 `docs/reports/2026-10-08-claude-round2-review.md`（结论 CHANGES_REQUIRED：P1×2、P2×2、G0待办）**逐条修复，全部离线**。**P1 板端重放依赖**：上传集补 `rf_environment_verify`/`rf_teaching_verify`/`rf_bpsk_replay`（原本既不在上传集、也不在 release software map 内），上传改到 `remote/scripts/` 使 `scripts.<name>` 与 probe 的 `parents[1]` 一致；新测试在**干净 mock 板端目录**真实导入整条重放链（变异：删模块 → `No module named 'scripts.rf_environment_verify'`）。**P1 终止窗口**：新增 **self-pipe + `poll()`**（handler 写一字节唤醒阻塞 recv，覆盖"检查后/recv 前/分片之间"），`run_loop` 在**读完一行后、dispatch 前**再查 `should_stop`（完整请求不得在停止后被派发）；新用例从**读回调内部**置旗，观测"已读走却未派发"（变异：删后置检查 → FAIL）。**P2 旧 driver**：旧四帧入口补 `records_root=output/'records'`，新测试用**下载后形态**镜像（receipt 仍指板端绝对路径，只有传对下载根才过；变异 → FAIL）。**P2 baseline 前置**：新 `load_frozen_baseline()` 用 replay 同款 `EnvironmentBaseline.from_dict` 解析，并要求与候选 `rf_environment_baseline.json` **规范 SHA 相等**，全部在 `connect()` 之前（空白/非法/截断/不同/候选缺文件五种均不得建会话；变异 → 5 FAIL）。**G0 每帧期限与偏差**：逐帧记录 `settle_actual_ns`/`settle_deviation_ns`（原始有符号）/`settle_deadline_ns`，超过 `SETTLE_WAIT_DEADLINE_NS`(=5000us) 判 `WAIT_OVER_DEADLINE` 并停止且**保留原 IQ**；期限内超长等待仍合格并原样报告（变异：删期限检查 → FAIL，删偏差 → 2 FAIL）。**两条真实成功路径**：旧 minor-4 四帧与新 minor-5 八帧在下载形态镜像上端到端走通。**回归**：board软件 1874/4skip、tools **166**、host除ML 902、ML 54；C daemon **424/0**、core 98/0、sleep 22/0、platform_rf 64/0、iq 40/0、protocol 58/0、state 92/0、daemon_main 39/0、`test_selftest` 两条 FAIL 为**预期负例**（`SELFTEST HARNESS OK`）、`make exit=0`、vendor integrity 10 files passed。收据 `evidence/rf-teaching/s2/settle-closure-20261008-v1/stage-checkpoint-round3.json`，清单 `g0-round3-sha256.txt`（11 文件）。**G0 仍未关闭**：self-pipe/`poll`/`sigaction` 位于 `#ifndef PLATFORM_HOST` 板上专有区，**主机测不到、本轮零离线验证**，须由 G1 ARM 构建＋真实 Linux 信号/套接字测试闭合。**NOT_RUN：** native ARM 构建、安装、任何 RF 采集、8 帧对照、S2 正式 freeze/108/12、真人、S3；削顶根因仍未证明。

**2026-10-07 G0离线闭合（覆盖下方）：** 按接续计划执行 G0。**Task 0.1 完成**（分解释器基线：board1872/4skip、tools121、host除ML902、ML文件54，四套件全绿——独立证实"host 6F/36E 纯为缺 torch"）。**Task 0.2 部分完成**：新增 `rf_sleep.{h,c}`，逻辑与**真实 nanosleep 适配器**同编进主机与板上构建（实测 MinGW 提供 nanosleep，故板上跑的就是主机测的那一个）；`sleep_us` 改为返回状态，等待未完成即以 `RF_ERR_TIMEOUT` 拒绝整笔交易。**FAULT 收尾缺口已闭合**：新增 core 级 `rf_core_release_part_keep_state`（清 SRC_SEL/CAP_ARM 且**不动 state**，保留 FAULT 诊断证据），disconnect 规则仅在 `RF_STATE_FAULT` 且普通 stop 未让器件停住时调用。**最终离线回归全绿**：C `test_rf_daemon` **399/0**、`test_rf_core` **95/0**、`test_rf_sleep` **22/0**、startup 92/0、vendor integrity 10 files passed、`make exit=0`。**两个独立审阅**：质量 **APPROVED**（PASS描述集合差分 0 条断言被删；`fake_pl.c` CNT 规则被加强；未放宽任何门槛）；规范**首轮 NOT APPROVED**，其 F1 **推翻了我此前一个结论**——我那条"失败后释放 SRC_SEL"的测试**通过的原因是错的**（观测的是 `serve()` 返回后，那时断线规则已清过），已改并补连接存活期断点测试；F1/F3/F4/F5 已修且 F1/F5 经变异验证，**F2 仍开放**（`== RF_STATE_FAULT` 判别无法被现有测试证伪）。**未完成**：0.2 的终止旗可观察性、零档 trace 断言加固；Task 0.3–0.6 未开始。收据 evidence/rf-teaching/s2/settle-closure-20261007-v1/stage-checkpoint.json。**NOT_RUN：** native ARM构建、安装、任何RF采集、8帧对照、S2正式freeze/108/12、真人、S3；削顶根因仍未证明。

**2026-10-07复盘接续：** Codex已读取Claude最新离线工作。当前1.0.3/minor5源码已有等待实现，但独立代码审阅发现八帧上限、旧driver接线、等待回读门禁及异常收尾缺口，未批准实现完成/上板。相关210项与ML专用环境54项复核通过；C daemon原日志实际387/0，两条内部FAIL为预期负例。下一项先G0离线闭合，再ARM资格与8帧对照；本轮零RF、不安装、不修改产品代码。最新复盘/固定流程docs/reports/2026-10-07-claude-progress-retrospective.md，接续计划docs/superpowers/plans/2026-10-07-s2-settle-validation-next.md。下方“离线实现完成”为作者此前记录。

**2026-10-07最新（离线，覆盖下方状态）：** 有上限单变量稳定等待对照**已设计、实现并离线验证完成，尚未上板**。等待插在物理捕获交易内部（TX源开启返回之后、`rf_core_prepare_rx` 之前），睡眠函数注入（与 `now_ns` 同形），`NULL` 时拒绝而非静默跳过；`settle_wait_us` 上限5000、缺省/0与既有行为逐位一致、`>0` 强制要求 `observe_timing:true`。协议 **1.0.3/minor5**（原计划漏了版本同步：`rf_environment_candidate.py` 的配对表与 `rf_daemon_main.c` 的 manifest 校验都要求它，不升版则新候选会被发布工具直接拒绝）。离线实测：pynq软件**1872通过/4跳过**、scripts/tests **121通过**、C主机测试**783 PASS/2 FAIL**（该2条与上一轮逐条相同，属 `test_selftest` 既有结果）、vendor integrity 10 files passed。**未完成/未证明：** `host/tests/` 6F/36E（全在 `torch` 相关 `test_rf_ml_*`，两种解释器结果一致；均为本次未改动模块，但**未做改动前基线，既有性未证明**）；`rf_daemon_main.c` 的 `sleep_us` 在 `#ifndef PLATFORM_HOST` 区内，**未经任何编译验证**；独立规范/质量审阅未执行。**NOT_RUN：** native ARM构建、安装、任何RF采集、S2正式freeze/108实板/12离线TEST、真人验收、S3。削顶根因仍未证明。报告docs/reports/2026-10-07-s2-settle-wait-offline.md，spec/plan见docs/superpowers/{specs,plans}/2026-10-07-s2-settle-wait*.md，证据evidence/rf-teaching/s2/settle-wait-2026-10-07/。

**2026-10-07（覆盖下方历史）：** 采集API时序观测已安全安装692904251e024a1254aecee679c6322a（daemon1.0.2/major1/minor4），663b9f72保留回退。1845软件测试通过/4跳过，原生ARM45源匹配，100次离线P95=281.68ms/RSS30476KiB。两会话4新实采均无rail，normal/NARROWBAND各2帧；BPSK三帧同步各0/138，最后窄带CARRIER_NOT_SYNCED无点/BER，原始拒绝保留；同IQ重放4/4一致。TX→RX武装API间隔5.106～12.078微秒，读取/收尾约2.8～2.9毫秒，不是样点边沿/AGC轨迹或窗口时长。两会话解除ACK，最终未布防/TX/RX关闭/双服务active/NRestarts0。累计64开发帧；削顶根因尚未证明。下一项为有上限的0/1000微秒单变量稳定等待对照设计，当前未添加等待或修改gain/AGC/判据。S1已关闭；S2正式freeze/108实板＋12离线TEST/真人仍NOT_RUN，S3未开始；无需专门触摸计时。总结与流程：docs/reports/2026-10-07-s2-capture-timing-progress.md。

**2026-10-06只读观测闭环（当前）：** 已安装663b9f7296cb55d4bdc4ae212a8d0774、保留7be30345回退；daemon1.0.1/协议minor3，仅新增显式device.info observe_rx_gain。core90/daemon255断言及独立规范/质量审核通过；板载软件1707通过4跳过，原生ARM45源匹配，100次离线P95=294.90ms/RSS30376KiB。实板init/config/load后3次TX/RX关闭的raw观察均RX1mode2（slow-attack）/index76→76/digital0/full-table；未布防null，0个新物理IQ帧，解除ACK及最终未布防/TX/RX关闭/双服务active/NRestarts0。未改AGC/增益/等待/环境判据，不把静止观察当采集期间轨迹或削顶根因。S2正式freeze/108+12TEST/真人仍NOT_RUN，S3未开始。阶段总结及流程docs/reports/2026-10-06-s2-observation-stage-summary.md；实板证据evidence/rf-teaching/s2/rx-observation-2026-10-06/board-probe-v1/。以下均为历史。

**2026-10-06 S2最新：** 当前安装7be30345b482ca48e6e5e3675237cd20（环境v3，复合默认A60/15+15）；1668板载软件测试通过4跳过；ARM完整100次P95=292.52ms、RSS30276KiB。开发实采v1/v2/v3/保守复合分别18/18/16/8帧，最近两轮因真实CLIPPED停止，原IQ/拒绝/关闭ACK均保留；S2正式freeze/108实板/12离线TEST/真人未开始，S3未开始。最终只读state evidence/rf-teaching/s2/post-pilot-2026-10-06-v2/state.json确认未布防、TX/RX关闭、双服务active/NRestarts0。当前需要先查接收增益/削顶稳定性；slow-attack AGC仅为线索，不能宣称根因。正式工具已补冻结基线/版本/配置/场景、原始handoff/显示/保存header+DSP/采集工具SHA及负控拒绝校验。报告及流程docs/reports/2026-10-05-s2-window-environment-progress.md。用户已取消专门触摸计时；以下“当前/最新”均为历史。

**2026-10-05 S2已安全安装（最新）：** 427dfa8ac7ad6b7b58edc6b59a873897安装成功、双服务active、TX/RX关闭且未布防，S1父发布保留回退；安装收据evidence/rf-teaching/s2/deployment-2026-10-05-v1/install-receipt.json。完整软件回归1643通过4跳过，随后新增部署/pilot测试20通过；ARM完整100次P95=287.87ms。同帧环境诊断/四场景草稿/独立确认load/实验保存已上板。开发18帧pilot工具正在复核，尚无新S2实采，正式实板/真人NOT_RUN，S3未开始。阶段总结及流程图docs/reports/2026-10-05-s2-window-environment-progress.md；以下未安装等文字为历史。

**2026-10-04 S1关闭、S2开发（最新，覆盖下方历史状态）：** 用户明确“每次捕获的时间间隔体验不卡顿，无需为体验专门计时，可以开始下一阶段”。S1功能与体验验收通过，精确触摸计时NOT_MEASURED且不再阻塞，收据evidence/rf-teaching/s1/closure-2026-10-04/receipt.json。S2固定BPSK窗口诊断/受控场景/环境页/记录/新合同已实现，独立规范与代码复核通过；20 TRAIN＋10 VAL旧实采建立开发基线，旧IQ派生不计新实板。候选427dfa8ac7ad6b7b58edc6b59a873897离线ARM完整base/CNN/BPSK/星座/诊断100次P95=287.87ms、RSS30076KiB，同输入主机/ARM一致；优化前336/371ms失败记录保留。S2正式实板与真人尚未验收，S3未开始；当前安装仍为S1（后续安装以新收据为准）。S2方案/计划docs/superpowers/{specs,plans}/2026-10-04-s2-window-environment*.md。精确触摸计时取消不改变后台计算资源预算。

**2026-10-04 S1真人功能闭合（最新）：** 用户提交11张实体屏照片，并对补充问题明确回复“1.对2.成功3，通过4.未测，但是测试预计通过”。照片正常参考帧两不同ID均128/201、0/138，图3/4同帧不重复计数；关闭参考新帧128/201无BER；2M拒绝无点/BER；停止武装清旧值。三次开启参考、保存当前/31秒/历史、RRC拒绝及恢复、取消回环、OFF和最终双路关闭由照片＋用户文字共同闭合。20次触摸计时明确未测，预计通过不记实测，S1整体仍未关闭，S2/S3未开始。11原图逐字节归档/源与归档SHA匹配；报告docs/reports/2026-10-04-s1-human-photo-review.md，收据evidence/rf-teaching/s1/human-photo-review-2026-10-04/photo-review-receipt.json。第三正常参考帧ID、本轮真人保存新文件完整性、短时采集中取消未另报告；本轮未访问板卡或改发布。下方“用户正在测试”保留为历史。

**2026-10-04 S1上板（最新）：** 用户确认S0五项剩余核心真人通过、TX1RX1在位，human closure收据已保存。c3bf915c544563324778d1c879b38e3a已安装，服务两路active/NRestarts0；新三组30ON+3OFF，30/30同步、0/4140、3OFF无BER无点、3组关闭ACK、33帧同输入重放均通过。ARM5热身＋100完整base/CNN/BPSK/星座计算P95=187.18ms，RSS22608KiB；不代表触摸时延。4条真实新IQ历史记录ARM写读/host回放通过，其中关参考同IQ有128/201无BER，不计新物理帧。自动收尾14:20:58未布防/TX否/RXARM否；之后用户正在实体屏测试，不能以此前读回推断手测当前状态。S1整体待真人与20次触摸计时，S2/S3未开始。最新报告docs/reports/2026-10-04-s1-board-validation.md，实板总收据evidence/rf-teaching/s1/deployment-2026-10-04/live-receipt.json；下文旧未安装文字为历史。

**2026-10-04 S1验收接续：** 机器验收工具闭合，31项新测试/六组91项回归通过，独立复审APPROVED；实际meta.display、三个上传脚本获批源码与传输失败收据已绑定。教学driver新增必填--candidate并自动同输入重放/重算BER和100次P95，机器通过不代替真人。只读板端预检993ffe21/status_only/未布防/TX选择否/RX ARM否，服务无重启；c3bf915c仍未安装。S0确认、新33帧、ARM和实体屏待办不变。报告docs/reports/2026-10-04-s1-acceptance-closure.md、收据evidence/rf-teaching/s1/continuation-2026-10-04/continuation-receipt.json。

**2026-10-04 S1开发推进：** 教学入口、真实同帧同步星座（128点/201符号，参考OFF无BER）与异步本地实验记录已离线实现，历史RF配置按捕获提交冻结；新cnn-bpsk-teaching-v1候选c3bf915c544563324778d1c879b38e3a未安装。旧33帧IQ离线回放0/4140，不能计作新实板；BPSK冻结SHA及bit/HWH/daemon/模型未改。S0剩余真人安全/失效/OFF待确认，新ARM资源/实体屏/触摸时延NOT_RUN。报告docs/reports/2026-10-04-s1-teaching-development.md、真人步骤2026-10-04-s1-teaching-human-checklist.md、总收据evidence/rf-teaching/s1/offline-receipt.json。新有限驱动须--teaching，计时必须执行完整同步星座路径。S1整体仍未验收，S2/QPSK未开始。


This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AC820 AWG 工程指南(Zynq-7020 + ACM2108 双 DAC 信号发生器)

## 当前 UI 发布接续（2026-10-01）

**2026-10-04 作品方向收敛（当前主线）：** 用户明确选择“教学演示＋环境诊断”，BPSK必交/QPSK条件扩展/建议人工确认。新顺序S0 M4真人收尾→S1教学入口/真实接收星座/实验记录→S2固定BPSK窗口异常诊断→S3人工草稿建议与拒绝→四场景演示；暂停旧M5a多速率/RRC优先路线。QAM/RRC接收、EVM、连续接收、自动换频发射退出本轮交付；已有高级TX/CNN/ACM工具保留，不删底层或模型标签。诊断限定有线受控IQ叠加与68.27微秒窗口，不称持续空口监测。第二峰值码候选档40需独立正常与收益验证，没资格/收益则观察或停止建议。本轮仅文档，不改代码/发布/硬件，不自动关闭M4真人待确认项。入口docs/reports/2026-10-04-work-scope-summary.md、docs/superpowers/specs/2026-10-04-teaching-environment-scope-design.md、docs/superpowers/plans/2026-10-04-teaching-environment-roadmap.md与2026-10-04-bpsk-teaching-observation.md。

**2026-10-04 M4真人照片复核（最新真人状态PARTIAL）：** 用户docs/reference/catch_photo的8张实体屏照片已逐张审核。三不同快照BPSK3M RECT参考1均载波/定时/参考同步、0/138、BER0；两张关闭参考新帧载波/定时同步、参考未提供、无计数/BER；2M RECT和3M RRC均不支持此配置且无BER（照片参考关闭）。参考12新帧同步0/138正常：非零PRBS7种子是周期偏移，旧seed1 IQ离线用seed12重放也0/138；不是照片IQ重放，不能据此证明修改瞬间清除旧值。失效/清除/切设备/31秒/取消/停止解除/OFF等无照片过程证据，待用户文字确认；真人P95未量测，M4不整体关闭。本轮未访问硬件或改发布。报告docs/reports/2026-10-04-bpsk-human-photo-review.md，逐图hash/读数收据evidence/rx-bpsk/m4/human-photo-review-2026-10-04/photo-review-receipt.json。下方真人NOT RUN是此前历史状态。

**2026-10-03 M4新正式自动验收（最新）：** 发布993ffe21199a311eabec143a50e7e8a2，三组真实UiView hit/press/release→回环确认→生产后台→同帧显示通过：30/30参考同步、0/4140，3OFF无BER；保存33份新IQ/metadata，33个独立板端时间戳与IQ SHA，主机/ARM33帧重放通过、最大差1.07e-14。短capture_id跨新连接会重新编号，联合会话与时间戳识别。100次热基础分析+CNN+BPSK纯计算P95 193.94ms、RSS27320KiB；不含触摸/采集/显示。三组解除ACK和关闭读回通过，最终未布防、TX source/RX ARM=false，两服务active/NRestarts=0。九个源码匹配安装发布；算法/模型/daemon/PL未改、未重载Overlay。全量sw1387 passed/4 skipped，验收工具最终64 passed，独立最终规格复核APPROVED。证据evidence/rx-bpsk/m4/current-progress-receipt.json、verified-ui-pilot-2026-10-03/；覆盖下方“缺正式自动证据”的旧状态。**用户明确真人功能尚未做，NOT RUN；真人P95 NOT MEASURED**。具体步骤docs/reports/2026-10-03-bpsk-ui-human-checklist.md，衰减40000 mdB。真人确认后进入M5a 2/3/5M RECT；总结docs/reports/2026-10-03-bpsk-ui-progress-audit.md。

**2026-10-03 最新M4审计（覆盖下方历史PASS口径）：** 新会话包与板端只读核验：当前993ffe21199a311eabec143a50e7e8a2，BPSK v2/生产CNN可用，两服务active/NRestarts=0、未布防、TX source/RX ARM关闭。全量sw实跑1387 passed/4 skipped。历史live15直接回调33条记录重新核算30ON同步、0/4140、3OFF无BER成立，但不是UiView命中/GT911证据，且未留逐帧IQ/metadata；旧聚合PASS遗漏BER≤1%及有效分母，已RED6→GREEN14修复并独立审阅。M4不能关闭，真人用户明确尚未做；具体步骤见docs/reports/2026-10-03-bpsk-ui-human-checklist.md，RF衰减须填40000 mdB。下一方向M5a需等待M4功能及证据闭合，不默认扩展当前支持域。原始证据不覆盖，最新审计见2026-10-03-bpsk-ui-progress-audit.md。

**2026-10-03 M4 Task6 实板通过：** 板卡安装 cnn-bpsk-v1 发布 993ffe21199a311eabec143a50e7e8a2（回滚安全安装器；首次安装遇板卡断连已记录并保留、第二/三次修复后 INSTALL_ACCEPTED，服务 active/NRestarts=0）。实板 UI 路径有限 pilot（生产 _rx_action→worker→client→daemon、显式参考种子 1/37/73、生产落地 rx_demod_summary）：**30/30 同步、0/4140 BER、3OFF 无误 BER、3/3 disarm 回执、无停止原因 → PASS**。资源：100 次热 分析+CNN+BPSK P95 170ms（≤300ms）、峰值 RSS 22.4MB。迭代修复（全部留证）：campaign 异常清理回归、逐帧 egress 谓词、worker 引擎绑定+就绪等待、面板 connected 稳态+实时轮询。收据 evidence/rx-bpsk/m4/live-acceptance-receipt.json；收尾读回 993ffe21 safe=true。**剩余：Task 7 真人清单（docs/reports/2026-10-03-bpsk-ui-human-checklist.md，需现场操作）**；真人 GT911/P95 单独计。
**2026-10-03 M4 安装尝试-板卡失联：** 安装 993ffe21（cnn-bpsk-v1）时：预检 e7f1fc69 safe → sftp 上传 → sudo install.sh 启动后主机侧通道 240s 超时，随后板卡 192.0.2.10 SSH 与 ping 全部不可达（≥8 分钟，4 次探测）。板端安装结果未知（可能完成/回滚/中断）；服务与 current 状态未知；代理已停止一切板卡操作，不做盲重装。恢复：现场检查电源/网络 → SSH 可达后核对 current（e7f1fc69 或 993ffe21）→ 恢复服务 → safe 读回 → 再定续装或直接 pilot。收据 evidence/rx-bpsk/m4/install-failure-receipt.json；pilot 声明 evidence/rx-bpsk/m4/pilot-declaration.json（8a961847…）。
**2026-10-03 M4 接续：** 以三份进度文档为最新基线（M3 完成、M4 待开始），审查 M2/M3：M2 上板审核证据链完整（e7f1fc69 安装收据、ARM 950 帧 parity 9.155e-5、热 P95 67.75ms、模型 f76f2fc0/runtime 27881154/daemon c33f9c5c 与收据一致）；M3 final-receipt 绑定 12 个源码哈希全部与当前树 MATCH。M4 续接：A/B 批已由中断会话实现（pipeline/worker/ui_model/ui_view，67+107 项全绿，本会话补写 a-b-receipt.json）；本会话完成 Chunk C——cnn-bpsk-v1 契约（merged_release.py BPSK_SOFTWARE_SOURCES + capability/gate 双契约、install.sh 双分支；新 test_rf_bpsk_release.py 9 项红→绿；panel closure 同步到最全契约；C daemon manifest 样例 91 PASS；候选发布 **993ffe21 11 99a311eabec143a50e7e8a2** 打包并 verify，未安装）及 Task 6 离线部分（scripts/rf_bpsk_ui_acceptance.py 编排器 + UiAcceptanceSession，7 项红→绿）。待办：板卡安装 993ffe21 + 实板 UI 路径有限 pilot（预声明声明冻结）+ Task 7 真人清单；e7f1fc69 仍可验证。
**2026-10-03 总路线与下一阶段规划：** 用户要求先阶段总结/总体plan/方向及流程图。当前位置M3 BPSK独立算法/ARM/有线pilot已完成，M4生产worker/UI集成未实施。入口`docs/reports/2026-10-03-rf-receiver-stage-summary.md`；总体路线`docs/superpowers/plans/2026-10-03-rf-receiver-roadmap.md`；M4设计`docs/superpowers/specs/2026-10-03-bpsk-ui-integration-design.md`与实施`docs/superpowers/plans/2026-10-03-bpsk-ui-integration.md`。主线M4板屏→M5a多速率RECT→M5b RRC→M5c抗扰→M6 QPSK→M7已知天线，QAM/CRC/连续接收另立。M4按配置/后台、生命周期/UI、版本发布、实板/真人四批推进；显式RX参考、不混UI/client修订号，新cnn-bpsk-v1合同保留旧合同。此文档轮次未改功能/部署/操作板卡，真人P95单列待测不冒充算法时延。

**2026-10-03 BPSK首轮接收完成：** 用户选择有线BPSK路线并要求continue。新增纯numpy有限快照同步/判决/PRBS BER、离线重放及有限pilot脚本，260项回归PASS。支持60MHz/D1/BPSK3MHz RECT/4096点；新3会话30/30参考同步、0/4140错误、3OFF无BER；33帧主机/ARM一致最大浮点差1.82e-12。ARM100热完整接收P95 92.88ms；纯重放资源复测P95 72.01ms/max77.85ms/RSS22052KiB。3会话均安全解除，原e7f1fc69发布及PID6200/6220保持，NRestarts0。代码未进生产software map，板屏同步/解调/BER仍未接入，需下一阶段worker/UI集成和真人验收；本轮不是CRC/空口/独立时钟验收。报告`docs/reports/2026-10-03-bpsk-receiver-acceptance.md`，总收据`evidence/rx-bpsk/2026-10-03/final-receipt.json`。下方保留既有CNN/UI历史。

**2026-10-03 真人收尾确认：** 在单次真人回环照片成功后，用户对“建议仅改草稿、超过31秒过期且建议禁用、停止并解除布防”明确回复“可以，验证通过”。这三项按用户现场确认记PASS，单次真人生产回环交互闭环完成。收据`evidence/rx-smart/human-loopback-fix-2026-10-03/human-final-confirmation.json`；未访问板卡、未改代码/发布。真人P95、完整OFF/三ON/OFF及静默watchdog定时仍未单独确认；天线/LNA/示波器未运行。下方相关待确认项为此前历史状态。

**2026-10-03 真人照片复测：** 用户按修订步骤完成单次真人回环，实体屏显示TONE、source=loopback.physical.capture、tiny-iq-cnn-v1、生产模型仅填草稿、AUTO永久关闭；确认入口→实采→生产识别→实体LCD显示单次通过。照片/收据`evidence/rx-smart/human-loopback-fix-2026-10-03/human-success.jpg`和`human-success-receipt.json`。第1、2项用户已确认正常；完整真人OFF/三ON/OFF、实际填草稿、31秒过期、停止现场表现/P95仍待确认，不由截图推断最终安全状态。本轮未操作板卡或改变发布。下方“真人回环待复测”为此前历史状态。

**2026-10-03 真人反馈核对：** 用户确认真人测试第1、2项正常（P95未量化，无需补视频）；TX–RX录像中已连续发射TX_ACTIVE，回环按钮按READY门禁禁用，随后静默解除变OFFLINE。完整准备按钮回调（布防/初始化/配置/加载）→READY/已加载且TX关闭→回环确认/取消→OFF/三ON/OFF已在原e7f1fc69发布补验通过：ON均TONE、OFF均UNKNOWN、削顶0、5帧重放分数差0，最终safe=true，两服务active/NRestarts0，75项回归通过。未改生产代码或重新发布；真人回环仍待按“只加载、直接进接收”复测。诊断`docs/reports/2026-10-03-human-loopback-diagnosis.md`，清单已补明确起始状态。下方为历史记录。

**2026-10-03 生产实板阶段：** 用户本轮确认TX1→RX1直连后，发布e7f1fc6962179eaeb57be15dcad07790完成OFF/三ON/OFF真实IQ、生产UiView回调→worker→daemon→CNN验收：ON全部TONE，OFF全部UNKNOWN/低能量，削顶0，±1.875MHz峰，最小突出度42.55dB/ON-OFF差32.53dB，5帧离线重放一致（最大分数差8.88e-16）。真实30秒静默watchdog在30.164秒自动解除并关闭source，此后超过5秒无重启；最终safe=true，服务active/NRestarts0。软件点击用MemoryLcd，不等于真人GT911或端到端P95。真人操作清单：`docs/reports/2026-10-03-production-human-test-checklist.md`；验收：`docs/reports/2026-10-03-production-live-acceptance.md`，证据`evidence/rx-smart/live-production-2026-10-03/`。真人触摸/P95及屏幕现场表现、天线/LNA/示波器仍NOT RUN。下方为历史记录。

**2026-10-03 本轮上板审核：** 原生产包726fc022…存在CRLF安装脚本及修复前daemon(c5b7…)；首装失败后自动回滚、安全读回通过。改为LF并复用有ARM构建收据、45/45源码哈希一致的schema修复版daemon(c33f9c5c…)后，发布 **e7f1fc6962179eaeb57be15dcad07790** 已安装。ARM numpy1.17.4核对950帧标签/拒识原因一致、870帧logit最大差9.155e-5通过；100帧热P95 **67.75ms**。203项安装/发布回归通过；ML119项通过，受沙箱读权限影响的单项重跑通过。两服务active/NRestarts0，panel up，模型加载production=true、AUTO关闭、ARM/source关闭、safe=true。没有新PL综合或daemon编译。当前发布新物理回环等待本轮接线确认；真人触摸/P95/watchdog、天线/LNA仍NOT RUN。报告：`docs/reports/2026-10-03-production-board-continuation-audit.md`；证据：`evidence/rx-smart/continuation-audit-2026-10-03/`。下方为历史记录。

**2026-10-03 正式验收（production）：** 连续相位增强（预声明：15° 网格 20 角、17356 children、44 个量化越界按声明规则弃置）按 D4 配方 200ep 重训得 model-rot-200（val 正式 0.964、未知拒识 val 1.0）。冻结新 TEST 官方验收 **9/9 门禁通过**：macro-F1 0.957、各类召回 TONE 1.0/BPSK 1.0/QPSK 0.947/QAM16 0.835/QAM64 0.88、未知拒识 0.992、OFF 1.0 → **status=production**（项目首个生产能力模型）。两处声明的修订：Amendment-4（export.py 支持无 TEST 接触训练清单的生产门禁，红→绿，全套 27 过）、Amendment-5（runtime 头部按 conv 同策略有序累加；RED 最差 1.22e-4 → 16/16 过；主机 870 帧 parity 9.2e-5、卷积/池化层逐位一致；新 runtime sha 27881154…）。生产发布 **726fc0225266cda9e3eaf527ec72cd6f**（cnn-v1、production 模型 f76f2fc0…、有序头 runtime）已打包并 verify，**未安装**；板卡仍 46fe2c08…。待办：板卡安装+ARM parity+实板验收（预声明安装步骤）、真人触摸/P95/watchdog、天线。收据：evidence/rx-smart/lowclip-recollect-2026-10-03/production-acceptance-receipt.json（3451a7b9…）。
**2026-10-03 低削顶重训：** D4 配方在新低削顶语料上重训：120ep TEST macro-F1 0.879；200ep（Amendment-3，同一配方/种子/初始化扩展）TEST macro-F1 0.9161（TONE 1.0/BPSK 0.97/QPSK 0.95/QAM64 0.86 达 0.80 门限；QAM16 0.715 未达；未知拒识 0.842 未达 0.90；OFF 1.0；可用帧诊断 0.940；val 正式 0.913）。VAL 阈值扫描证明纯阈值无法同时满足全部门禁（QAM16 上限 0.775、未知拒识上限约 0.867——chirp/噪声→BPSK 高置信误判不可由阈值拒绝）。候选导出 models/d4-lowclip-200-exported（a4fa3014…，experimental）。production 能力保持 false；剩余杠杆：连续相位增强实验（D4 声明中未覆盖族）、增采 QAM16/OTHER 独立会话、或先在实板试运行候选。收据：evidence/rx-smart/lowclip-recollect-2026-10-03/retrain-evaluation-receipt.json（e5849e8d…）。
**2026-10-03 低削顶重采：** 预声明实验执行完成：collect.py AMPLITUDE_LADDER 经两个声明修订为 (60,40,40,30)（TDD 红绿：34→35 项通过，两次独立复审 APPROVE）。板卡（46fe2c08…，TX1-RX1 直连）重采三 split：train 96 会话/900 有效帧、val 95/900、test 95/900，全部 30 格×3 会话×10 帧达标，幅度只用 60/40（rung30 未用到），160 削顶帧保留为证据；合并语料 286 会话/2860 帧/2700 有效，production_ready=true、coverage_missing=0（manifest d6c6a29c…）。冻结 D4 在新 TEST：macro-F1 0.593→0.807、未知拒识 0.995→0.917、OFF 1.0，仍 experimental（召回 BPSK 0.60/QPSK 0.67/QAM16 0.56）。按预声明顺序正在用 D4 配方（init f5538d3e…、120ep、seed20261001）于新 TRAIN/VAL 重训，完成后评估同一冻结新 TEST。收据与计划：evidence/rx-smart/lowclip-recollect-2026-10-03/（experiment-plan.json、collection-completion.json、amendment-*-receipt.json、independent-review*.json）。
**2026-10-03 D4 接续评估：** D4(120epoch, seed20261001)重跑完成后做独立TEST评估：物理TEST macro-F1 0.593→0.675、OTHER/未知拒识 0.775→0.995、OFF拒识1.0；但已知类召回0.49–0.56仍远低于0.80，正式门槛未达（experimental）。可用无损帧诊断F1 0.950——正式指标被测试集质量卡住：640/1310已知TEST帧削顶或削顶会话内，按规格计UNKNOWN漏检，理想上限约0.5/类，必须重采无削顶独立数据才能验收。合成诊断(仅原始帧)macro-F1 0.087、842/900低于物理能量地板：物理微调不迁移合成域，仅记录不作验收。主机parity（torch vs numpy导出，870帧）最大差6.1e-5通过；导出 models/d4-candidate-exported（3779cb98…，experimental）；新cnn-v1候选发布 141c2dfbf8e783372969debca050fe57（D4模型+C加速runtime d054c3c9）已打包并verify（软件map/panel/model artifact），未安装。板端仍46fe2c08…，本轮未动板卡。收据 evidence/rx-smart/continuation-2026-10-03/d4-eval-receipt.json。下一步唯一可走通的验收路径：预声明低削顶TRAIN/VAL/TEST独立重采→按D4配方重训→新独立TEST评估；连续相位方向覆盖另立实验。
**2026-10-03 额度门限接续审核：** 剩余额度8%时按用户要求收尾；Task9候选在原2825帧（含1005 TEST）逐层验证通过，最大3.0518e-5，但ARM100帧P95为225.17/293.04ms，超过200ms，不发布。当前板端仍46fe…，本轮只读确认safe=true；主树runtime候选076e814…尚未部署，不能将主树测试通过当板端通过。Task8正式准确率仍FAIL；TRAIN/VAL质量拒绝使理想F1上限仅0.756/0.726，方向敏感性已复现；合成初始化按旧配方复现并补齐来源，D4增强准备完成但120epoch训练在门限取消，无完成模型/后台任务。完整报告 `docs/reports/2026-10-03-rx-task8-10-followup-audit-acceptance.md`，证据 `evidence/rx-smart/continuation-2026-10-03/final-receipt.json`；真人现场验收仍NOT RUN。以下最新审核段落为本轮之前的状态记录。

**2026-10-03 最新审核/接续：** Task8/9 的溯源及发布契约缺口已修复；冻结模型 macro-F1 0.5929、OTHER 拒识 0.775，准确率未通过，历史 lineage=false。Task9 资源检查通过，但 1005 帧中 156 帧 conv3 逐层误差超 1e-4，不能无条件记 COMPLETE。Task10 实验链路和界面已实现/审核；首次 cnn-v1 上板发现 daemon 不兼容嵌套 schema，修复后经用户授权重新编译 ARM并通过构建/假硬件回归。当前激活发布 `46fe2c080deb9d0c42eb0a612a86d091`，生产 model=false、AUTO关闭。峰值码150削顶保留FAIL；码100的OFF/三ON/OFF真实IQ与板端numpy及离线重放通过，预测中仍有TONE→BPSK误判。两服务 active，实读 ARM/source清零、disarmed；真人触摸/P95/watchdog及示波器 NOT RUN。复用bit/HWH，无新PL综合。最新报告 `docs/reports/2026-10-03-task8-9-review-task10-continuation.md`；以下接续段落为历史记录。

**2026-10-02 最新接续：** RX 实施已完成 Task 1–6，当前发布 `e201d491bf2e5eecb3979c9bc12ab370`（rx-v1，无模型）。1073 项板载 Python 回归、67 项训练数据清单测试及 TX1→RX1 直连 UI→真实 IQ 的 OFF/ON/OFF 验收通过，最终 ARM/source 清零、disarmed；CPU 训练环境已就绪。下一项 Task 7 是合成/真实数据采集，CNN 尚未训练，真人触摸及示波器仍 NOT RUN。本轮复用既有 bit/HWH，无新 PL 综合。最新索引与证据见 `docs/reports/2026-10-02-rx-session-continuation.md`；下方 2026-10-01 段落为历史记录。

下一阶段文档已整理：`docs/reports/2026-10-01-project-progress.md`为当前进度索引，`docs/superpowers/specs/2026-10-01-rx-smart-design.md`与`docs/superpowers/plans/2026-10-01-rx-smart-implementation.md`为RX采集/分析、小型CNN及后续天线/LNA路线。现有正弦回环已通过；RX页面仍未接入，CNN尚未训练，天线/外置LNA未实测。本次仅编写文档，不把候选设计或未勾选计划写成已实现。

后续验收已覆盖旧段落的遗留项：主树ARM daemon构建/打包和717项Python回归完成，当前发布为 `56d0d0d4bd36d4e22d1c030926917a16`。数字回环tap29、功分器TX1→RX1物理回环及软件点击→实际TX1仪器验收通过；物理条件为LO80MHz/Fs60MSPS、内部40dB、每帧32周期、峰值码150。旧tap7/15失败、峰值码1500削顶均保留FAIL；已修复FAULT释放前未清source的缺口。真人触摸/实体时延和RX面板采集接入仍待验收/实现。详情与可运行人工复验命令见 `docs/reports/2026-10-01-rf-build-physical-hmi.md`。本轮未新综合PL。

完整面板因旧发布漏装 `rf_iq_signal.py` / `ui_units.py` 而退回状态页，本轮已修复并激活软件发布 `622838c506a8359f8d91423d9169a92f`。安装须携带完整 software map 并通过 `--require-software --check-panel`；服务 active 不代表 UI 启动成功，另核对 `panel up, UART bridge` 日志与实际帧缓冲。本轮 712 项回归通过；RF 当前 status_only、core=false，物理发射/回环/触摸延迟仍 NOT RUN。来源、重建身份、主树 RF 构建遗留与证据见 `docs/reports/2026-10-01-session-repair.md`。本轮未改 RTL，复用已在板端运行的 bit/HWH/daemon，不把软件重建称为 Vivado 综合。

## 三条流水线(互相独立, 产物不混用)

| | AWG(PL + 裸机 PS) | PYNQ 板载 Linux | RF(AD9361, 迁移中) |
|---|---|---|---|
| 目录 | `src/`、`scripts/`、`sdk/src/main.c`、`host/` | `pynq_overlay/` | `rf_overlay/` |
| 构建 | `scripts/build_bitstream.py` | `pynq_overlay/build.tcl` | `rf_overlay/build.tcl` |
| 上板/验收 | `test_bridge.py`/`scope_verify.py`(COM11) | `pynq_overlay/sw/tests/` | `docs/rf/boardtest.md`(判据先于测量) |

## 目录结构
- `host/main.py` — 唯一的 PySide6 GUI 启动入口；从工程根目录执行 `python host/main.py`
- `host/siggen/` — GUI、协议、控制器和预览实现，不直接运行其中模块
- `host/tests/` — 上位机离屏、协议和控制器单元测试
- `src/rtl/` — PL 源: awg_top / reg_bridge / dds_core / harm_synth / mod_synth /
  am_stage / ampl_scale / iq_digital_mod / prbs7_source / iq_constellation_mapper /
  rrc_digital_mod / rrc_filter / dac_driver / sine_rom /
  信道损伤 chan_impair / fade_fir / noise_gen / chan_cfg
- `src/tb/` — RTL 测试台（`awg_top_tb` 需 `-sv`）
- `scripts/` — `create_bd.tcl`（BD 生成）、协议/示波器测试、构建/校验/下载
- `tools/` — 完整重建、网表追踪、谐波变体模拟调试工具
- `sdk/src/main.c` — PS 裸机桥源码唯一入口
- `sdk/ws*/` — SDK 生成工作区，不是源码；由最新 HDF/BSP 重新生成
- `pynq_overlay/` — 板载 PYNQ(Linux) 那一半的独立工程：BD(PS7+LED/按键 GPIO+TFT 控制器)、
  `rtl/`、`tb/`、`constraints/`、`build.tcl`、`sw/`(板端守护进程+systemd+安装脚本)。
  用 `vivado -mode batch -source pynq_overlay/build.tcl` 构建，产物 `out_merged/awg_merged.bit`+`.hwh`
  拷到板子 `/home/xilinx/overlays/awg_merged/`。**与 AWG 侧完全无关，独立工具链流程**(见技能 `ac820-pynq`)
- `rf_overlay/` — 第三条独立流水线：AD9361(FMCOMMS3) RF 收发迁移，BD+RTL(`ad9361_phy`/
  `phy_rx`/`phy_tx`/`pl_spi_master`/`dds_inc` 等)+`sw/`(板上 C 程序 `board_app` + Python 探针)。
  构建 `vivado -mode batch -source rf_overlay/build.tcl`(产物 `out/rf_overlay.bit`+`.hwh`)；
  板上验收判据**先于测量写死**在 `docs/rf/boardtest.md`，`rf_overlay/sw/board_app/`
  下 `make`(板上原生 gcc) / `make host-test`(开发机自检)。**未实测项一律标 NOT RUN，不写"应该通过"**
- `awg/`、`.Xil/`、`xsim.dir/` — Vivado/XSIM 生成产物，不是源码入口

## 工具链（Vivado/Vitis 2025.2，禁用旧 cli-anything-vivado）
本工程是 **Vivado/Vitis 2025.2 工程**：`awg/awg.xpr` 由 Vivado 2025.2 创建，
SDK 工作区(`sdk/ws*`)由 Vitis 2025.2 生成。**不再使用旧 `cli-anything-vivado`**
(它只配置到 Vivado 2018.3；2018.3 打开本工程会以只读模式失败，`Common 17-39`)。
工具链调用规则见技能 `using-vivado2025-cli`：

- 专用 CLI：工程根 `vivado2025.cmd`(子命令 `app bitstream board boot config
  deploy export hw impl ip platform project report synth workspace` 等)。
  任何操作前先 `vivado2025.cmd config check` / `config show` 拿真实工具路径，
  **不要从安装根猜 bin 层级**；真实工具在 `<LOCAL_PATH>2025.2\2025.2\Vivado(或 Vitis)\bin\`。
- RTL/PS7 改动 → bit+HDF 仍走 `scripts/build_bitstream.py`；2025.2 下显式注入工具路径：
  ```bash
  VIVADO_BIN="<LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat" \
  python scripts/build_bitstream.py --force
  ```
- JTAG 下载 PL：`vivado2025.cmd hw program --bit <bit>`；JTAG 运行 PS 应用：
  `vivado2025.cmd deploy jtag --app <app> --workspace <ws>`。

## 唯一启动入口
请把工程根目录作为工作目录，使用：
```bash
python host/main.py
# 或使用虚拟环境
host/.venv/Scripts/python.exe host/main.py
```
不要从 `.claude/worktrees/` 下的其他副本或 `sdk/ws*/` 生成目录启动；`host/main.py` 是唯一 GUI 入口。

## 构建与下载(防旧 bitstream!)
```bash
python scripts/build_bitstream.py   # 检测 src/rtl 变化 -> 完整重建(OOC+主综合+实现+bit)
python scripts/check_bitstream.py   # 下载前校验(退出码 0=可下载)
bash scripts/program_board.sh       # 校验 + JTAG 下载
```

## ⛔ 上板铁律: 没有网表级门禁证据, 任何 bitstream 不得上板

`check_bitstream.py` 只比 **manifest 哈希与 RTL 源哈希** —— 它证明"这个 bit 来自这份源码",
**不证明"这个 bit 里的逻辑是完整的"**。两者都会静默说谎, 都已实测发生过:

- **2026-09-10**: `blk_mem_gen_0/1` 因 `Enable_32bit_Address=false` 被静默综合成**无存储器件的
  空网表**。manifest 哈希全对, 而 AXI 读写恒 0。
- **2026-09-23**: `awg/awg.cache/ip/` 里陈旧的 `blk_mem_gen` 条目**没有被新构建刷新**
  (`blk_mem_gen_0` 用的是 09-10 那个 37KB 坏 DCP; `blk_mem_gen_rx` 的条目完全缺失) →
  四个 BRAM 在主综合时全部退化成 **BlackBox**, DAC 输出钉在中点 0x80, 示波器闭环 55 项
  全挂。而 `awg_top` 的 XSIM 121 项全绿、BD validate 通过、协议回读全对 —— 只有最终
  主综合这一步被污染, 因此**源码侧一切检查都发现不了**。

**所以上板前必须 additionally 满足下面全部条件** —— 缺任何一条就不上板, 也不许用"先上板看看":

1. **构建必须自己跑完到 `BITSTREAM-OK`, 没有中途被人为/内存/超时打断。**
   被打断后磁盘上留下的 `.bit` 是**半成品**, 且 manifest 可能已被写成旧值 —— 实测被内存
   杀掉那次停在 `impl_1/opt.dcp`, 而 manifest 一切"正常"。
2. **`GATE2-OK` 必须出现在本次构建日志里**(`tools/check_bd_bram.tcl` 二级门禁:
   每个 `blk_mem_gen` 实例下有真实 RAMB 且无 BlackBox)。
   构建中断后想单独补验(不重跑实现), 用只读方式对现有 checkpoint 跑:
   ```bash
   # 打开 synth_1 的结果, 挂门禁的 level-2 过程(需要 ::CBB_SOURCED 才能跳过 level-1)
   <LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat -mode batch -notrace -nolog -nojournal \
     -source tools/_gate2_probe.tcl        # 该脚本 open_checkpoint + cbb_check_synth
   ```
   见到 `GATE2 OK` 才继续; 见到 `GATE-2 FAIL` 先清 cache 再重建(见第 3 条)。
   ⚠ **磁盘上的 `.dcp`/`.bit` 的时间戳与内容都不能作为通过证据** —— 实测(2026-09-23)
   一次成功的门禁复核之后, 磁盘 dcp 又被后续 `reset_run` 覆盖回坏状态。门禁通过的**唯一**
   证据是构建自身日志里那一次 `GATE2-OK` 与 `BITSTREAM-OK`。
3. **GATE-2 报 BlackBox / RAMB=0 时, 先清 IP cache 再 `--force` 重建, 不要改别的**:
   ```bash
   rm -rf awg/awg.cache/ip/2025.2/*/*/*          # 纯派生产物, 安全; 正确 dcp 在
                                                 # awg/awg.srcs/.../ip/*/, 不受影响
   VIVADO_BIN="<LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat" \
     python scripts/build_bitstream.py --force
   ```
   判据: 好的 `blk_mem_gen` DCP 约 **60KB 且 RAMB≥2**, 坏的是 **37KB 且 RAMB=0**。
4. **板上的 .bit 要与本次构建产物逐字节一致**(md5/sha256), 不能只比 manifest。合流产物
   `pynq_overlay/out_merged/awg_merged.bit` 与 AWG 侧 `awg/awg.runs/impl_1/host_bd_wrapper.bit`
   **是两个东西**, 互相不能作证; 上哪块就比哪块。

**⛔ 禁止事项**: 不得为了让用例变绿而跳过/放宽门禁; 不得在门禁未过时上板"先看看现象";
不得把 `NOT RUN` 写成 `PASS`。门禁不过而确实需要数据时, 先报告门禁失败与原因, 由人决定。

**⚠️ 致命陷阱**: awg_top 是 BD 内模块, 改了 src/rtl/*.v 后直接
`vivado2025.cmd synth`/`bitstream generate` 或直接下载都会拿到旧版逻辑(曾导致
3 次下载旧 bitstream), 必须走 build_bitstream.py(见上, 2025.2 注入 VIVADO_BIN)。
其内部 rebuild_bitstream.tcl 现在:
(a) **先重跑 create_bd.tcl**(BD 是派生状态, 结构变化如新增 AXI GPIO 只有重跑生成
脚本才生效);(b) **显式重建 OOC run**(create_bd.tcl 删除重建会剪掉 OOC run,
批处理 generate_target 不自动重建, 且 2025.2 下 OOC 尾部恢复 STATUS 可能停在
`Scripts Generated` —— 判完整以磁盘 `*.dcp`/`__synthesis_is_complete__` 为准);(c) synth_1 注入 STEPS.SYNTH_DESIGN.TCL.PRE
(tools/synth1_read_awgtop.tcl)读回包装器+RTL → 主综合**在线综合 awg_top**
(不再依赖 OOC DCP 作黑盒; OOC run 保留作快速失败检查);(d) **两级 BRAM 门禁**
(`tools/check_bd_bram.tcl`): 一级在 OOC 综合前查 BD 内嵌 `blk_mem_gen` 的 generic
自洽(秒级), 二级在 synth_1 之后、impl_1 **之前**查每个 `blk_mem_gen` 实例下有真实
RAMB 器件且无黑盒残留 —— 不绿就不进实现, 不浪费几十分钟。哈希覆盖:
src/rtl/*.v/*.vh + src/constraints/*.xdc + scripts/create_bd.tcl + scripts/ps7_props.tcl +
tools/rebuild_bitstream.tcl + tools/check_bd_bram.tcl + tools/synth1_read_awgtop.tcl。
bitstream 路径: awg/awg.runs/impl_1/host_bd_wrapper.bit

**⚠️ 板上 bitstream 可能落后源码一个 BD 修订**: `check_bitstream.py` 只比对 manifest 记录的哈希,
发现不了"manifest 哈希对、但磁盘/板上 bit 实际来自更早 `create_bd.tcl`"的情况。实例: 磁盘 bit
(2026-09-08, 源 `9818d9f`)缺 `7dbce14`(2026-09-09) 为 `proc_sys_reset` 补的
`rst_clock_locked`/`rst_aux_inactive` 常量(注释: "proc_sys_reset holds AXI peripherals in reset
when dcm_locked is left open") → AXI BRAM 写入不生效: USER 数据源输出塌成直流且与上传内容无关,
而 PRBS7 路径完全正常, 极易误判为软件问题。鉴别: 解压
`awg/awg.runs/impl_1/host_bd_wrapper_routed.dcp`(zip) 并 grep 该 BD 修订独有的 cell 名
(如 `rst_clock_locked`), 缺失即说明板上逻辑来自更早的 BD。教训: 改过 BD/PS7/RTL 后先 `--force`
重建再上板调试。

**⚠️ BD 内嵌 blk_mem_gen 的 `Enable_32bit_Address` 必须为 true**(2026-09-10 根因):
BD 会把 BRAM 接口地址端口固定成 32 位(BYTE_ADDRESS), 生成的 IP generic 恒有
`C_ADDRA_WIDTH=32`; 若 `Enable_32bit_Address=false` 则同时有 `C_ENABLE_32BIT_ADDRESS=0`,
这对矛盾让 `blk_mem_gen` **静默**综合成没有任何存储器件的空网表(0 个 RAMB, 不报黑盒、
不报错)。现象: AXI BRAM ctrl 照常握手 ACK(越过 4K 孔径才 FAULT), 但所有偏移/字节通道
读回恒为 0; AXI GPIO 正常往返, 极易误判为软件问题。鉴别: `open_checkpoint` 该 IP 的
`.dcp` 看 RAMB 数(坏的只有 ~30 个 cell, 只有 SAFETY_CKT 寄存器), 或直接跑
`tools/check_bd_bram.tcl`。**单独 `create_ip` 同参数是正常的(2 个 RAMB)** —— 触发条件
只有 **BD 内嵌**, 所以拿 IP 单独做实验会得出"参数没问题"的错误结论。
A/B 实测(最小 BD 顶层综合, 仅此参数不同): false → 0 RAMB, true → 2 RAMB。
配套: 字节寻址下 BRAM 取 `addra[31:2]` 作字地址, 故 `awg_top` 的 B 口
(`bram_b_addr` / `tap_b_addr`)必须给**字节地址**(`word << 2`), 与 A 口(AXI BRAM ctrl
输出的就是字节地址)一致。

**⚠️ IP cache 里的旧条目不会被配置改动刷新**: `synth_1` 靠
`ips.enableIPCacheLiteLoad` 从 `awg/awg.cache/ip/<ver>/<hash>/<ip>.dcp` 解析 BD IP 黑盒,
而 `blk_mem_gen` 的 cache 键**不随 `Enable_32bit_Address` 变化**。实测(2026-09-10):
修好参数后重建, mem1(此前无 cache 条目)拿到新条目 `181a6c3ae9ed1661`(60 KB, 18:45),
而 mem0 的目录 `b/b/bb16800ee0fe524a` **仍是 11:28 的旧坏 DCP**(37 KB)未被覆盖。
该轮 synth_1 的门禁二级实际通过(存储器已在网表中, 未受影响), 但这条陈旧派生产物是隐患。
判别: 坏 DCP 约 37 KB 且 `open_checkpoint` 后 RAMB=0; 好的约 60 KB 且 RAMB=2。
若出现"配置改对了却仍报存储器缺失", 先 `rm -rf` 对应 cache 目录再 `--force` 重建
(纯派生产物, 安全)。门禁二级会在合成后立刻抓到这类回归, 不会让它走到实现。

## teammate 动态分配与回收
- 有新任务时，先查看共享任务列表；优先把未阻塞任务分配给合适的空闲 teammate，避免重复启动同类代理。
- teammate 正在执行任务时不得因其暂时没有新消息而撤回；只有确认任务已完成、取消或代理确实空闲时才发送关闭请求。
- teammate 完成任务后必须先报告实际修改文件和测试结果，再将任务标记完成；确认没有后续任务时及时关闭，避免空转占用槽位。
- 关闭 teammate 只停止代理，不丢弃其已写入工作树的修改；未完成任务不得标记为完成。代理退出后，将仍需处理的任务重新分配给主代理或其他合适的 teammate。
- 新任务优先分配给现有空闲 teammate；没有合适空闲代理时才创建新代理，并明确其输入、边界、验收标准和是否允许修改代码。
- 代理报告“完成”后，主代理仍须检查实际 diff 和测试结果；不能仅凭摘要宣称交付。并行修改同一文件时按文件协调，避免覆盖未完成工作。

- 支持 BPSK/QPSK/QAM4/QAM16/QAM64；数据源当前为内部 PRBS7，可在 MODE OFF 时设置 seed 1..127，默认 127，并通过 RESET 显式重同步。
- 符号率范围 `10..1000000 Sym/s`，协议为 `SET DIGMOD SYMRATE <Hz>`；FILTER 为 `RECT|RRC`，ROLLOFF 为 `0.2|0.35|0.5`(默认 0.35)，SEED 为 `1..127`(默认 127)，均只能在 `MODE OFF` 时修改；RRC 支持 BPSK/QPSK/QAM4/QAM16/QAM64 全部数字模式；ROLLOFF 经 GPIO11 bit[22:21] 编码(00→0.35,01→0.2,10→0.5,11→0.35 保留)，SEED 经 bit[29:23] 编码。
- GUI 设置路径为 `python host/main.py` → “数字调制”页 → 关闭模式后编辑符号率/滚降/PRBS7 seed → 点击“设置”。
- 数据源可选 PRBS7(内部)或 USER(用户上传)：小文件 ≤512 B 经 UART 上传、大文件 ≤1 MiB(`DDR_PP_BANK_BYTES`)经 UDP 上传到 DDR PP bank。数字调制页「大文件数据流」维护**文件队列 + 环形模式**：播放中自动把队列下一块喂给**非活动 bank**(1 文件=无缝循环、多个=按序轮流)，也可手动上传所选到指定 bank。**手动上传需 MODE OFF，播放需先选数字调制模式**(上传字节被当作符号比特经星座映射/RRC)，环形喂数在播放中进行故不受 MODE OFF 限制。RRC 支持全部数字模式，采用 4 SPS、rolloff 0.2/0.35/0.5 三档可配置、8 symbols span、33 taps 的理论成形预览；成帧/FEC 未实现。

## 板级验证流程
1. `bash scripts/program_board.sh`(下载, 已含校验)
2. 若 SDK 工作区未重新链接/构建或桥程序未运行:先重新链接/构建 PS 工程
   (`vivado2025.cmd app build awg_bridge --workspace sdk/ws2025_gem_fresh`),再执行
   `vivado2025.cmd deploy jtag --app awg_bridge --workspace sdk/ws2025_gem_fresh --bit awg/awg.runs/impl_1/host_bd_wrapper.bit`(PS 程序跑 OCM)。
   **⚠ 固件工作区是 `sdk/ws2025_gem_fresh`,不是 `sdk/ws2025`** —— 后者的 BSP 无 lwip,编不过当前固件;
   且 `app build` 打印 `ok` 只查 ELF 存在、不看时间戳,构建后必须自己断言产物新鲜。
   `deploy jtag` **必须显式给 `--bit`**,否则报 `error: Bitstream path is not recorded`。
3. `python scripts/test_bridge.py COM11` — 协议脚本含 195 条命令断言，板上最终以实际运行结果为准
4. `python scripts/scope_verify.py COM11` — 示波器闭环 61 项, **必须用系统 `python`**(需要 pyvisa;
   `host/.venv/Scripts/python` 没装)。含 QPSK 三档、QAM16/QAM64、BPSK/QAM4、
   QAM16/QAM64×α0.2/α0.5 共 13 组 RRC BER 与 OFF 恢复；信道损伤四组: 两点等增益多径 `|H(f)|` 梳状
   零点(探针 100/250/500kHz, 零点在 `fs/(2d)`=500kHz) + AWGN 噪底随 level 线性 + 窄带单音谱线 +
   周期冲激计数

## 示波器(DSOX1102A)要点
- USB-TMC: `<VISA_RESOURCE>`
- **`scope_verify.py` 必须用系统 `python` 跑**(它 import pyvisa);`host/.venv/Scripts/python`
  没装 pyvisa(那个解释器只用于跑上位机断言 `pytest host/tests/`)
- pyvisa 必须用默认后端(visa32.dll), 勿用 '@py'(缺 libusb)
- **USB-TMC 会话会周期性掉线**: 实测约 30~90s 一次, 报 `VI_ERROR_NCIC`
  (与命令无关; 纯空闲反而稳定, 活跃才掉), 恢复约需 10s。多分钟的套件必然中途被截断,
  故 `scope_verify.py` 内置 `ReconnectingScope` 代理, 对每次 write/query/二进制读透明重连重试
  并重跑一次 `scope_setup()`(幂等)。改脚本时别绕过这层去直接 `rm.open_resource`;
  会话刚被占用时**首次**打开也会报 NCIC(甚至 `VI_ERROR_IO`), 等几秒重试即通
  (曾以此误判"示波器没接": 设备在 PnP/`list_resources` 里是正常的)
- **跑套件必须 `python -u`**(2026-09-12 血泪): 输出重定向到文件时 Python 是块缓冲,
  几十条 `PASS` 会全压在缓冲区, 一旦中途卡住就**一个字都看不到**(实测 8 分钟 0 字节,
  误判成"还在跑")。判断是否真卡死看**进程 CPU 时间**是否还在涨, 别只看日志。
- **`VI_ERROR_ALLOC (-1073807300)` 出现在 `viOpen`**: USBTMC 会话卡在驱动里的形态,
  此时设备在 PnP 仍报 `CM_PROB_NONE`、`list_resources` 照常枚举出该资源, 极易误判成
  "示波器好好的"。**杀进程不管用**(实测杀完连试 4 次/60s, 本机 7 个 VISA DLL 候选
  ——只装了 Keysight `ktvisa` 一套栈, IVI 的 System32 `visa32.dll` 是加载器——全是同一个错)。
  只能**拔插示波器 USB 线或重启示波器**; 也能用管理员 `Disable-PnpDevice`/`Enable-PnpDevice`
  对 `USB Test and Measurement Device (IVI)` 做设备级复位替代。另: 该卡死发生在
  `ReconnectingScope` 重连路径上, 而 `_drop()`→`inst.close()`(`viClose`)不受 VISA timeout 约束,
  所以掉线恰好落在 close/open 上时整套会静默挂住 —— 重连后卡住无效印即查这条。
- **判断"示波器在不在"只能用 `Get-PnpDevice -PresentOnly`**(或 `Present=True`):
  `list_resources` 会照常列出**幽灵节点**, 拔插失败时同样列出(`Present=False`,
  `Problem=CM_PROB_PHANTOM`), 只看它必误判。拔插后若 PnP 冒出
  `USB\VID_0000&PID_0002\...`(描述符都读不出来的设备)即**枚举失败**——查线材/换直连端口,
  不是 VISA 的问题。
- **别在诊断脚本里写 `:WAV:POIN`**: 它把记录长度钉成手动值, 且 `:WAV:POIN:MODE NORM`
  复位不了(实测写完后 `:WAV:POIN?` 恒回该值), 此后所有 `:WAV:DATA?` 只回该点数,
  所有基于波形捕获的用例(FFT 幅值、RRC BER、星座/时序)会**成片误判**, 而基于 `:MEAS:` 的
  用例照常通过 —— 极易误读成 RTL 回归。恢复: `:WAV:POIN 62500` 并确认捕获点数确为 62500
  (曾用 `:WAV:POIN 100` 做存活探针, 导致 37/61 的假失败)
- **先 import pyvisa 再 import pyqtgraph**, 否则 visa32.dll 加载失败(Qt DLL 冲突)
- 自动刻度收敛慢, 测量前手动固定时基/垂直刻度(scope_verify 已处理)
- :WAV:DATA? 读取用 `query_binary_values(datatype='B', container=list)`, container=bytes 会报错
- 示波器通道可能保留旧的 `PROB/OFFS` 状态;闭环脚本固定 CHAN1/2 为 `PROB=1`、`OFFS=0` 并同步垂直刻度,
  否则 CH2 波形可能饱和到 1/255,导致频率/相位提取失真
- PULSE 的 GUI 高电平区间已在 RTL 中映射为板上物理高电平;由于 ACM2108 模拟链路极性反相,
  该区间使用内部 `8'h81` 而非 `+127`,不要再修改全局 DAC `^8'h80` 转换
- NOISE 波形编码为 5,使用确定性 8 位最大长度 LFSR;相位高字节变化时推进一次,
  因此 FREQ 表示约 min(256×FREQ,50MHz) 的噪声刷新带宽;PM 与谐波对 NOISE 无效
- 相位提取使用基于 5%/95% 分位的动态迟滞零交叉;1kHz 相位用例需至少 4ms 采样窗,
  避免 ADC 量化毛刺和窗口边界导致有效上升沿不足

## 谐波相位经验(2026-08-13 修复)
- 独立谐波累加器 ord 0→非0 时从冻结值启动 → 相位随机 → 改为 ph_k=acc0×ord_k 组合派生
- 锁相版实现后**偶次谐波反相 180°、奇次同相**(仿真/网表均同相) → RTL 中谐波地址
  **按 ord 奇偶条件翻转**(s_k = ord_k[0] ? ph_k[31:24] : ph_k[31:24]^0x80)。
  **勿改为全量翻转**(会把正确的奇次翻转); 机制疑为谐波符号取反(-1)^(ord+1),
  SAW 方向判别已否定全局反相假说
- Δ=φ_k−k·φ1 是谐波相位基准(k×0.7° 为地址量化系统偏差, 属正常);
  示波器上升沿触发会掩盖整体反相

## 调制经验(2026-08-14)
- FM/SWEEP 共用 fword_inst 数据通路, 同一值馈入 dds_core 与 harm_synth → 谐波锁相保持
- PM 查表地址层注入(pm_off), 谐波偏移 ord×pm_off, 偶次 ^0x80 在偏移之后;
  pm_off 9 位回绕(mod 512)在 mod 256 地址域自洽
- AM 级在 ampl_scale 之前(饱和 ±127 再按 AMPL 缩放)→ 深度 100% + AMPL 100 才见全摆幅
- 调制源 48 位累加器 fword48 = mfreq_01×562950(mfreq_01 为 0.1Hz 单位)
- 示波器验证: FM/PM 边带比与 Bessel 理论吻合(J1/J0: FM β=2 → 2.58, PM 90° → 1.20),
  AM 边带判别 ratio≈0.41 防死 AM 盲区

## 双通道相位联动经验(2026-08-23)
- 协议仅允许 CH2: `SET CH2 PHASE <0..36000>` 与 `SET CH2 PHASELINK ON|OFF`;
  PHASE 单位为 0.01°,36000 表示一整周;GET 回读 PHASE/PHASELINK
- PULSE 波形编码为 4,基础字使用 bit[30:28];`SET CHx DUTY 0..100` 通过 GPIO11 bit[8:1]/[16:9]
  写入 CH1/CH2,50% 与 SQUARE 等价,调制波形仍只允许 SINE/SQUARE/TRIANGLE/SAW
- 硬件打包为 `gpio10 = phase_off[31:0]`, `gpio11[0] = link_en`;
  联动时 CH2 频率路径使用 CH1 瞬时频率,CH2 自身 AM/PM 被抑制,但 wave/ampl/base/harmonics
  与静态 phase offset 仍独立
- CH2 相位路径由 CH1 累加器组合派生并在下游时钟采样,实际为
  `phase_ch2[n] = phase_ch1[n-1] + offset`;约一个 50MHz 周期延迟,误差约 `360 × f_hz / 50e6` 度,
  不应视为任意频率下严格零延迟
- 上位机预览以 CH1 为主并叠加联动 CH2,FM/SWEEP 共享 CH1 调制路径

## 信道损伤经验(2026-09-10)
- 位置: 接在 awg_top 最终输出选择(`dac_sel0/1`)之后、`dac_driver` 之前, 因此**标准 AWG 与数字调制
  两条路径都经过**; CH1/CH2 各一个 `chan_impair`(SEED 0x00C0FFEE / 0x00DEADBEEF), 抽头表由共享的
  `chan_cfg` 从 BRAM B 口排空。
- 模型 `y[n]=sat(round(Σ_k g_k·x[n−d_k]/256), ±127)`: 增益 Q8 有符号 9 bit(`−256..255`, **单位增益
  取 255 而非 256**——9 bit 表示不了 256, 见 SDD 判决 R2); 延迟 0..511 采样点。饱和边界是 **±127**
  (镜像 `rrc_to_dac`), 不是 8-bit 的 −128。旁路(`chan_en=0`)为逐位透传, bit-exact; 使能时端到端 3 拍流水。
- `fade_fir` 每抽头延迟线为 512×8 分布式 RAM, 乘积 17-bit、累加 20-bit, 用**平衡加法树**防 DSP48 级联;
  `noise_gen` 为 xorshift32 → 4 路 8-bit 均匀数求和(CLT 近似), σ=level/100×127, 除以常量 148。
- 控制面: `axi_gpio_12` 双 32-bit 全输出给两通道控制字(bit0=en, bits[7:1]=noise, bit8=commit epoch,
  bit9=TONE 开关, bit10=IMPULSE 开关); 抽头表在 `axi_bram_ctrl_1` + `blk_mem_gen_1`(32-bit×16, CH1
  word0-3 / CH2 word4-7), 由 `chan_cfg` 同步读; TONE/IMPULSE 数值在 word 12/13(CH1)、14/15(CH2)
  (word12/14: `fword[23:0] | ampl[30:24]` 7-bit 无符号; word13/15: `period[15:0] | width[23:16] |
  ampl[31:24]` 8-bit 有符号)。`chan_cfg` 每通道排空 6 个字 {0,1,2,3,12,13} / {4,5,6,7,14,15}。
- 协议 `SET CHx CHAN ON|OFF|COMMIT` / `NOISE 0..100` / `TAP <k> <gain> <delay>` / `TAP CLEAR` /
  `TONE <freq 0..25000000> <ampl 0..100>` / `IMPULSE <period 1..65535> <width 1..255> <ampl -100..100>`,
  `GET CHx CHAN`(**24 token**; 旧 17 token 仍可解析, TONE 视为关闭)。**抽头表与 TONE/IMPULSE 数值写入
  BRAM 后要 COMMIT 才锁存**; TONE/IMPULSE 的**开关位走 GPIO 立即生效**。`TAP CLEAR` 只清抽头表仍
  需 COMMIT, 纯透传请用 `CHAN OFF`。CHAN/NOISE/TAP/TONE/IMPULSE 命令**不受 MODE 限制**。
- 抽头表 BRAM 实际深度是 BD 传播的 2048(不是脚本里的 16), 但只用前 8 个字; B 口地址由
  `chan_cfg` 给**字地址**(`tap_word_addr[4:0]`), 在 `awg_top` 内转成字节地址后出端口。
- **板级状态(2026-09-10, 已修根因)**: 真正的阻塞点是 `blk_mem_gen_0/1` 因
  `Enable_32bit_Address=false` 被静默综合成**无存储器件的空网表**(不是软件问题, 也不是
  "板上 bitstream 落后")。已修 BD + awg_top 字节寻址 + 两级门禁并 `--force` 重建, 上板实测:
  - AXI 往返已通: 0x40000000/0x42000000 写 0xDEADBEEF/0xCAFEBABE 等均**逐字读回**, 清 0 亦对
    (修前这些偏移一律读回 0)。→ 两个 BRAM 现在真实存在且可读写。
  - `scripts/test_bridge.py` **150/150 ALL PASS**(含 CHAN/NOISE/TAP 全组); 注意 `GET CHx CHAN`
    读的是固件**影子数组** `chan_tap[][]` 而非 BRAM, 所以协议通过**不能**当作 BRAM 正常的证据。
  - `scripts/scope_verify.py` **56/56 PASS**(2026-09-10 晚; 同日白天曾报 53/54, 唯一失败是 QAM64
    一组单符号误码 BER=0.009、同组另两滚降 BER=0 → 判为密集星座的测量余量问题, 逐次波动)。
    56 项 = 原 54 项 + **信道损伤两组**: 两点等增益多径 `|H(f)|`(实测 100/250/500kHz 比
    1.858/1.424/0.032, 模型 1.895/1.409/0, 零点在 `fs/(2d)`)与 AWGN 噪底随 level 线性。
    → 标准 AWG/RRC/QAM/信道通路完好。该套件**不经过** `align_known_sequence`(RRC 用例走
    `rrc_symbol_ber` + 32 个 timing offset), 故与 USER 路测试脚本的改动互不影响。
  - `scripts/scope_verify_user.py` **4/4 PASS**(QPSK/QAM16 × UART/DDR)。此前报 **0/4** 是**测试脚本
    自身的两个缺陷**, 不是 RTL 故障(误判代价: 一度计划加 ILA 抓 B 口查读延迟)。两个缺陷:
    (a) `_capture_ber` 固定 `sample_offset=0` 只搜符号相位, 从不搜**小数采样相位**——真实捕获起点
        落在符号周期内任意位置, 同一路捕获在该相位下可读出 0.004 或 0.19(PRBS7 亦如此), 与数据
        对错无关; 内部 RRC 校验正是因此搜 32 个 timing offset(见 `_align_rrc_capture`)。
    (b) `align_known_sequence` 用**全局峰值**归一化多电平星座; 数据相关的包络过冲把峰值抬高
        1.2~1.5 倍, QAM16 外圈均值被压到内外圈判决边界(84)之下而**整体错判成内圈**(BER 0.004→0.26)。
        改为按星座轴平均幅度估计尺度(`constellation_scale`, 均值对离群点不敏感)。
    另: UART 载荷由 32 B 改 128 B——32 B 在 QAM16 下只有 64 符号的循环, RRC 回绕瞬态(约 8~16 符号)
    超出 `trim_symbols=8` 护栏, 残留 BER≈0.004 会被误判为数据错误。
    已排除(均实测): UART 上传→BRAM 内容逐字节正确、小端字节序、bank1=字节偏移 512=字 128、
    控制字位域固件与 `reg_bridge` 一致; 循环周期精确、无符号时钟漂移; 两 bank 写同一数据仍"失败"
    → 与 bank 选择无关。**教训: 已知符号序列的 BER 判据必须先做 2-D 对齐(符号相位+小数采样相位),
    幅度归一化必须用鲁棒统计量, 否则会把测量缺陷当成 RTL 故障。**
- **同一类归一化缺陷在内部 RRC 判据里又出现一次(2026-09-11 定位并修复)**: `scope_verify.py` 的
  `rrc_symbol_ber` 一直用**全局峰值**归一化多电平星座(当年只修了 USER 路径, 这条漏了), 于是 QAM64
  外圈(±126)被包络过冲压到判决边界 ±108 之下、被错判成内圈 90。实测 QAM64 α=0.2 @100kSym/s 连采
  20 次: 峰值归一化 **3 次**报 BER≠0(0.0045/0.0135, 出错样点实测 −106.2/−105.6, 本该是 −126),
  换 `constellation_scale` 后 **0 次**(实测 peak/轴平均 = 1.06~1.11)。→ 此前记为"密集星座测量余量
  波动 / 逐次波动"的那 1 项失败**不是随机余量, 而是确定性的尺度偏差**(过冲量随 PRBS 图案变, 所以
  表现为时有时无 —— 这正是它能伪装成"余量"的原因)。现 `rrc_symbol_ber` 与主循环的眼图/星座量化
  统一走 `constellation_scale`(BPSK/QAM4 在 `RRC_AXIS_LEVELS` 里没有条目 → 回落峰值)。顺带修掉
  一处遮蔽: 主循环把**测量**到的 stopband/inband 比值赋给了同名变量 `rolloff`、覆盖了配置值, 使
  13 条 RRC 标签全部打印 "α=<比值>" —— 文档里的 "α≈0.0085 组" 就是这个误读, 真正失败的用例是
  **α=0.2**。修后 `scope_verify` **58/58**(那条 QAM64 用例稳定通过)。
- **信道损伤的示波器量法(2026-09-10 实测)**: 多径取**两点等增益**(TAP0/TAP1=255, 回波 d 样点) →
  `|H(f)| = 2·(255/256)·|cos(π f d/fs)|`, 在 `f=fs/(2d)` 是深零点; 探针频率**必须 ≤ `FREQ_MAX`=1MHz**
  (d=50 → 零点 500kHz; 曾取 d=20 探到 1.25MHz 被板子拒为 `ERR FREQ OUT OF RANGE`)。基准取
  `CHAN OFF`(逐位透传)逐频点对比, 比值即 `|H(f)|`, 与模拟链路频响无关。幅度不能满幅:
  两抽头等增益峰值 ~2x, AMPL 需 ≤50 否则 ±127 饱和(实测用 AMPL 40)。
- **噪声 σ 的"数字域 vs 模拟域"**(重要, 别当成 bug): `noise_gen` 的 `σ=level/100×127` 是**逐样点数字域**
  量; 示波器测到的是经 DAC 零阶保持 sinc + 模拟重建滤波后的**模拟域** σ, 实测只有数字域的
  **0.65~0.69 倍**(level 5~30 稳定, 与时间基、AMPL 均无关; 频谱平坦延伸到 ~25MHz=50MHz DAC 的
  奈奎斯特, 说明不是采样/带宽不够)。故示波器用例只判**相对**行为(level 0 静默、σ 随 level 线性),
  不拿数字域 σ 做绝对判据。
- **干扰源的量法(2026-09-11)**: 单音用 FFT 谱线位置 + 幅度单调性判据; 冲激必须用
  **宽脉冲(几十个采样点)** —— 1 个采样点的冲激会被 DAC 零阶保持 + 模拟重建滤波
  严重削平, 与噪声 σ 实测只有数字域 0.65 倍是同一机制。冲激用例的**时基不能用长时基**:
  示波器记录长度有限, 2ms/div 会把采样率压到 ~3MSa/s, 1.28us 的冲激只剩几个采样点,
  计数判据退化成噪声; 改用 200us/div(2ms 窗, ~50 个冲激)并把"每冲激示波器采样点数 ≥4"
  并入判据。两者都只判相对行为, 实测/模型比值回报在 detail 里持续观察。
  干扰源与 AWGN **同位置**(fade_fir 之后), 不过信道 FIR —— 语义是"接收端叠加的外部信号"。
- **TONE/IMPULSE 的数值参数同样要 `CHAN COMMIT` 才锁存**(与抽头一致): 开关走 GPIO
  立即生效、数值走 BRAM 等 COMMIT。故 `SET CHx CHAN TONE <f>` 单独发不会改变当前输出,
  必须再发 `SET CHx CHAN COMMIT`; `GET CHx CHAN` 回读的是**影子数组**(用户下发的值),
  与是否已 COMMIT 无关。
- **干扰源是相加的**(2026-09-11 实测): `CHAN ON` 时 `chan_impair` 输出
  `sat(fir_out + noise + tone + impulse, ±127)`, 所以**开启信道却不显式清零未测的源, 就会把它们
  一起测进去**。这不是假设: 示波器用例曾被上一轮残留的 `IMPULSE 1000 8 -100` 污染 —— 多径
  `|H(f)|` 从模型 1.895 被抬到 2.195, AWGN level 0 噪底被抬到 10.6 LSB(=127·√(8/1000))。修法是共享
  常量 `CHAN_QUIET = ("SET CH1 CHAN TONE 0 0", "SET CH1 CHAN IMPULSE 1 1 0")`, 每个信道用例开头都套用。
- **冲激极性在示波器上反相**(2026-09-11 实测): `impulse_gen` 对正 `ampl` 发正数字脉冲
  (规格 `impulse[n]=±A_imp/100×127`), 但 ACM2108 模拟链路反相, 故示波器上读作**负向尖峰** —— 实测
  `ampl=+60` → 捕获 `max=+4.5, min=−67.5`。计数判据必须按数据实际的极性数沿。(与 `PULSE` 用内部
  `8'h81` 补偿的是同一反相。)
- **本轮两个工具坑**: (a) `deploy jtag` **必须显式给 `--bit`**, 否则报
  `deploy.jtag: error: Bitstream path is not recorded`; 可用形式见「板级验证流程」。(b)
  `scope_verify.py` **必须用系统 `python`** 跑(它 import pyvisa), `host/.venv/Scripts/python` 没装 pyvisa。

## 复数 I/Q 损伤经验(2026-09-12)
- 位置: 数字调制支路(`rrc_active ? rrc_i8 : dig_i` 之后、`dac_sel0/1` 之前), **标准 AWG 的 a1/a2
  完全不经此级** —— 这是「关闭时与改动前逐位一致」的前提。参数走抽头表 BRAM 字 8/9/10(A|B、C|D、
  fword), 由 `chan_cfg` 排空; 使能借 CH1 控制字 bit11。`chan_cfg` 每通道排空 **9** 个字
  ({0,1,2,3,12,13}+{8,9,10}), 故一次 COMMIT 的排空是 19 拍。
- **⚠ 板级根因(已修): 旋转级的相位累加器 `fword=0` 时只"停止累加"、不清零** → 把上一次频偏留下的
  相位**冻住**, 变成恒定但任意的旋转, 且**随历史变化**。原来写的是 `phase <= phase + fword;`,
  改成 `phase <= (fword == 32'd0) ? 32'd0 : phase + fword;`。host 模型
  `apply_iq_impair(fword=0)` 是 `arch=0` 的恒等旋转, 两边必须一致。
  这个 bug 极难认: 上板现象是**三个 I/Q 用例里只有两个测"朝向"的失败**, 而「增益/相位失配」通过
  —— 因为它比的是 **|相关系数| 与特征值比, 对旋转不敏感**; 且失败用例的实测角恰好是设定值的
  **2 倍**(45°→89.8°)或斜率乱掉(72°/sym→11.5), 很像"静态矩阵算错", 而静态矩阵其实是对的。
- **确诊链(可复用的方法)**: (a) `GAIN 0` 应让 CH1 塌成直流、CH2 不变 → 实测成立, 证明**系数送达
  路径(固件/BRAM/排空)是好的**; (b) `FILTER RECT` 下逐符号取中值直接读星座(不打成形、**无需任何
  对齐**), ON(单位阵) 是**干净的四个点**只是整体转了个小角度 → 证明矩阵对、多出来的是旋转级相位;
  (c) 旁路时重对齐 BER=0.0000, ON(单位阵) 重对齐 BER=0.3739 —— 正是 45° 旋转把 QPSK 推到**判决
  边界**上的特征。另: 用 `xcorr` 比较两次捕获会骗人(信号只在一个 1.27ms PRBS 周期内自相似,
  异步触发下相位随机), 别用它下结论。
- 回归用例: `src/tb/iq_impair_tb.v` 用例 7 `freqoff->0 phase reset`(频偏累到 45° 再切回 0, 单位阵
  必须逐位透传)。旧 RTL 实测 `(100,0) → (71,71)` 失败、新 RTL 通过, 与板上症状一致。
- 状态: 修复后 `iq_impair_tb` PASS、`awg_top_tb` 121 项 0 失败、位流重建(WNS **0.249ns**/WHS 0.039ns,
  0 违例)、`test_bridge` 230/230 通过、已上板。`scope_verify` 的 I/Q 三用例 **2026-09-12 复测通过**
  (`scope_verify` **61/61**, 见下条)。
- **⚠ I/Q 用例的对齐方法本身曾有一个致命缺陷(2026-09-12 定位)** —— 不是 RTL 故障。旧实现是
  "先在 IQIMP OFF 时对齐一次, 记下 (timing, symbol_offset), 之后复用这组偏移做匹配滤波", 前提是
  **捕获起点落到 PRBS 序列里的位置固定**。实测**不成立**: 板上 PRBS 自由运行 + 示波器异步触发,
  同一配置连续 6 帧的符号偏移是 **110/107/95/47/100/4**, 时间偏移在 0~2.8us 之间跳。复用旧偏移
  等于把"序列起点漂移"读成"星座整体旋转", 于是报出 `45°→141.5°`、`72°/sym→5.1°/sym` ——
  这组数既不符合主机模型、也不复现旧 RTL 的症状(旧 bug 是 `45°→89.8°`、`11.5°/sym`),
  所以它**不是 RTL 回归, 也不是测量余量波动**, 而是判据缺陷。鉴别: 每帧独立重对齐后, 三个
  用例立刻落在 44.98°/72.00°; 更直接的是把符号率压到 10 Sym/s **让整个 2ms 窗内符号恒定**,
  此时 `I+jQ` 就是一条纯旋转相量, `unwrap(angle)` 斜率即 Δf —— 实测 5000→4999.7、
  20000→20000.0、-20000→-20000.0、100000→100000.3 Hz, RTL 完全正确。
- 修法(`scope_verify._align_iq_capture`, 三个 I/Q 用例统一走它): **每帧重新对齐, 判据用
  "差分相关"** `d_k = r_k·conj(r_{k-1})` 与期望序列的差分比模。差分同时消掉**恒定旋转**(静态相位)
  与**每符号等速率旋转**(频偏), 所以三种损伤下都能锁住正确偏移; 相比之下直接复相关
  `|Σ r_k conj(e_k)|` 在 72°/sym 下相干和被转平会锁错, 基于 BER 的对齐在 45° 旋转下失锁
  (QPSK 判决点正好落在边界上)。**教训: 凡"对齐一次、之后复用"的判据都要先证明捕获相位可复现;
  自由运行 PRBS + 异步触发下不可复现, 必须逐帧对齐, 且对齐判据要对被测损伤本身免疫。**

## 电压校准经验(2026-08-14)
- 单点校准即可: 实测线性度 <1%(50%/20% 档位), 各波形满摆幅一致(方波/正弦 9.50V)
- 校准常数 = 100% 方波 10kHz 实测 Vpp(GUI 默认 9.50V, 校准框可改)
- 100kHz 高频衰减 ~3%(带宽滚降, 非电压校准范围)
- 示波器 RMS 实测 ≈ 理论×0.96(测量特性, GUI 保持理论 RMS)
- 采集工具 tools/cal_measure.py; 注意复位必须清 HARM1-4, 否则 SINE 用例输出
  残留谐波, 频率/幅度全错(曾致数据误读)
- 示波器测量刻度影响(实测): 垂直刻度占屏 40~70% 最准(±0.05V); 大占屏
  (1V/div 测 ≥60% 档)压缩偏低 0.1~0.3V, 小占屏(2V/div 测小信号)量化抬升
  偏高 0.1~0.3V; 对比 GUI 电压先看占屏, 多次测量取平均
- GUI 显示用硬件截断模型: V = trunc(127×pct/100)/127×cal(与 RTL ampl_scale 一致)

## 测试速查
- RTL TB(xvlog/xelab/xsim, 见记忆/历史): mod_synth_tb 31 项, am_stage_tb 7 项,
  dds_core_tb 17 段(4 基础 + 5 PULSE + NOISE + 7 PM), harm_synth_tb 15 项, reg_bridge_tb 79 项, awg_top_tb 114 项,
  ampl_scale_tb 12 项, prbs7_source_tb 30 项; 信道损伤 fade_fir_tb 13 项 / noise_gen_tb 17 项 /
  chan_impair_tb 13 项(含 16 点 host-golden 对齐) / chan_cfg_tb 32 项; 干扰源 tone_gen_tb 48 项 /
  impulse_gen_tb 8 项(见下计数口径);
  iq_digital_mod/rrc_digital_mod/iq_constellation_mapper_tb 走 Python GOLD 对比
- **计数口径**(2026-09-11): 逐项 TB(awg_top_tb/reg_bridge_tb/chan_impair_tb/chan_cfg_tb/fade_fir_tb/
  noise_gen_tb)的"项数"= `xsim -runall | grep -c '^PASS '`, **含结尾聚合摘要 `PASS <tb名>`**(故比源码
  检查点多 1)。`tone_gen_tb` / `impulse_gen_tb` **只打结论行**(失败才打 `FAIL <name>`), 直接 grep 会把
  它们数成 1, 故项数须从源码数比较点: `tone_gen_tb` = 3 个 `run_case`×12 拍 + 6 拍 disabled + 6 拍
  silent = **48**; `impulse_gen_tb` = **8** 个 `chk()`("disabled" 那条在 `repeat (4)` 里, 4+4=8)。
- 协议脚本静态基线为 195 条命令断言 / 示波器闭环 61 项（`scripts/test_bridge.py`, `scope_verify.py`）。
  2026-09-11 上板实测(**impulse_gen 常量表重建后**: WNS 0.192ns): test_bridge **195/195 ALL PASS**
  (连跑两次、不重启桥); scope_verify 修判据归一化前 **57/58**, 修后 **58/58**(复跑两次确认)。
  此前那 1 项失败(文档旧记为 "QAM64 密集星座测量余量波动 / α≈0.0085 组")的根因是**判据自身的
  峰值归一化缺陷, 既不是 RTL 故障也不是随机余量**, 且真失败用例是 α=0.2 —— 详见「信道损伤经验」
  2026-09-11 那条。其余含信道损伤四组全过(冲激组已改为幅度扫描, 实测 spans=[26,48,71] 严格单调)。
  另: 背靠背连跑 `scope_verify.py` 偶发过一次 `VI_ERROR_RSRC_LOCKED`(示波器波形读取失败, 与信号
  无关), 隔 20s 重跑即 58/58。
- **2026-09-12 上板实测(I/Q 损伤级; 位流 = 14:27 那个)**: PL 重编程后
  test_bridge **230/230 ALL PASS**(含 IQIMP 全组); scope_verify **61/61 PASS**(= 旧 58 项 + I/Q 三用例,
  **连跑三次均 61/61**: I/Q 相位 `44.98/45.05/44.96°` vs 45°、频偏 `72.00°/sym` vs 72×3、增益失配统计量
  匹配)。跑前按惯例先核对版本: `check_bitstream.py` 通过 + `sdk/src/main.c` 与
  `sdk/ws2025_gem_fresh/awg_bridge/src/main.c` 逐字节一致 + ELF 晚于源 + `GET IQIMP` 回读正常。
- USER/DDR 数据路模拟 I/Q 闭环 `scripts/scope_verify_user.py`（QPSK/QAM16 @100kSym/s；`--selftest` 无需板卡/示波器）
  —— **2026-09-10 上板 4/4 PASS**(连跑两次稳定), **2026-09-11 复查仍 4/4 PASS**(impulse_gen 常量表
  重建后复测, 四路 BER 全 0, 无回归)。
  此前的 "0/4" 是测试脚本缺陷(缺小数采样相位搜索 +
  用全局峰值归一化多电平星座), 不是 RTL 故障; 详见「信道损伤经验」末条
- 上位机: `host/.venv/Scripts/python -m pytest host/tests/`（706 项, 2026-09-11 实测全绿）
- 协议细节: README.md 与 sdk/src/main.c

## RRC 时序经验(2026-08-30)
- RRC 系数从常量改可配置(寄存器组 c[0:32])后, Vivado 把 8 路 DSP48 串成 PCOUT→PCIN
  级联(单周期长路径 22.67ns → 32 个 setup 失败)。修复: rrc_filter.v 把 phase 选路 MAC
  从串行累加改为**显式平衡加法树**(40-bit 中间 reg pr[0:8]/qr[0:8], 先 8 路并行乘再两两分组求和),
  阻止 DSP48 级联 → 收敛(WNS 0.895ns)。改回常量不可取(需求就是运行时切换滚降)。
- GPIO 输入从 PS FCLK0(clk_fpga_0)跨到外部 sys_clk 都是 2-FF 同步器, Vivado 默认把
  首级打拍当同步时序 → 单比特 hold 违例(-0.253ns)。修复: awg.xdc 对两时钟
  `set_clock_groups -asynchronous`(正确 CDC 方法学), 而非调 hold 约束。
- 重建后 sys_clk(50MHz/20ns) 0 setup 失败、0 hold 失败; clk_fpga_0 WNS 11.347ns; 收敛。
- **2026-09-11 复测**(本轮新增 `tone_gen`/`impulse_gen` 的乘加与 `chan_impair` 四路相加):
  sys_clk **WNS 0.119ns** —— 上一轮基线只有 0.071ns, 余量本就极小, 故这是必须回报的实测值 ——
  仍 0 setup/0 hold 失败(49553 端点全过), clk_fpga_0 WNS 7.583ns; 新加法逻辑未拉长关键路径, 收敛。
- **同日再测(impulse_gen 除法→常量表之后)**: 上一条的 0.119ns 里真正的关键路径就是
  `impulse_gen` 的 `((a*127)+50)/100` —— 常量除 100 因 `a>=0 ? … : …` 符号分支无法化成
  乘倒数, 被综合成 ~72 位 restoring 除法进位链(19.909ns / 33 级逻辑, 占满 20ns 周期)。
  把 8 位 `ampl`→`level` 的映射换成 **256 项常量表**(零算术; 与原 function 逐位等价,
  已全表核对 + `impulse_gen_tb` 不改 golden 即过)后重建: sys_clk **WNS 0.192ns**(+0.073ns),
  WHS 0.019ns, 仍 **0 setup/0 hold 失败**(49526 端点), 收敛。教训: **常量除法一旦被符号
  分支挡住, 综合器不会替你做乘倒数** —— 要么消掉分支, 要么直接查表。

## PYNQ 板载显示经验(2026-09-13)
- **工程与流程**: 见上「目录结构」的 `pynq_overlay/`。板子跑厂商 PYNQ 2.7 镜像(SD 启动),
  **开机不加载任何位流**, LED 面板与 TFT 都由 `pynq-status` 守护进程在开机时用 `Overlay()` 装。
  板子 `192.0.2.10`(eth0:1) ssh `xilinx/xilinx`; 串口 COM11(PS UART, **用 DC 供电时若拔掉
  Type-C 线就没有 COM11**)。
- **P2 已完成**: `lcd_timing.v`(时序+8 条竖彩条) + `lcd_overlay_top.v`(MMCM),
  800×480@60 RGB565(`H_TOTAL 1056 / H_SYNC 128 / HFP 40 / HBP 88`, `V_TOTAL 525 / V_SYNC 2 / VFP 2 / VBP 25`,
  HS/VS **低有效**), 已上屏确认。XSIM `lcd_timing_tb` 9 项 PASS; 构建 WNS 14.426ns 0 违例。
- **P4a 已完成(2026-09-13)**: `sw/pynq_lcd_ui.py` —— 纯 Python + PIL 立即模式绘图/UI 层
  (文字/圆角矩形/圆/进度条, `Label`/`Button`/`ProgressBar`, 可插拔输入源 + 帧循环), 叠在
  `pynq_lcd.Lcd` 之上; 不引 LVGL(板上已有 PIL, 引一套 C 库不划算)。离屏 pytest **43 项**
  (帧缓冲 19 + UI 24)全绿, 板端实测整屏一帧 **7.5ms(134fps)**。跑法(用**当前**部署目录
  `awg_merged/`, 旧的 `ledbtn/` 里是修复前的版本):
  `cd /home/xilinx/overlays/awg_merged && sudo python3 pynq_lcd_ui.py --demo /tmp/x.png`
  (开发机上也能渲 PNG 看版式); 真上屏用 `demo_ui(lcd); ui.run()`。
- **⚠ 板端 Python 画图别在帧缓冲上逐行切片**(2026-09-13 实测): 帧缓冲是 `PynqBuffer` 的
  非连续视图, **逐行切片赋值每次约 100us 且与宽度无关**(69 行×220 宽 7.4ms、69 行×1 宽
  6.4ms), 圆角矩形这样画一次 23ms; 而**标量写 2us、整块 2-D 赋值 0.15ms、整屏清屏 0.75ms**。
  做法: 形状先用 numpy 在连续掩膜上算好再 `region[mask]=color` 盖一次, 字形/形状掩膜都缓存
  (这样才从 92.9ms 降到 7.5ms)。**中文要按段换字体**: 板上唯一带中文的
  `DroidSansFallbackFull.ttf` **没有 ASCII 字形**, 整行交给它英文数字变豆腐块, 须按
  ASCII/非 ASCII 分段各用各的字体并按 ascent 对齐基线(dev 机的 msyh 两种都有, 所以本地看不出来)。
- **P4b 已完成(2026-09-13): GT911 触摸跑通, 并进了守护进程**。PL 侧 `TP_RST`(PL 脚 **R15**,
  `axi_gpio_fb` 通道2 的 bit1); 板端 `sw/pynq_touch.py`(PS I2C0 裸寄存器主驱动 + GT911 +
  坐标标定 + 接 UI 的输入源)、`sw/touch_demo.py`(自检 / `--calib` 五点标定); 离屏 pytest **107 项**。
  `pynq-status` 开机自己放开 TP_RST 并用 `Touch.open()` 打开触摸, 屏幕上画**跟随手指的准星** +
  右下角坐标读数。板上实测: `GT911 @ 0x5D`, 五点标定最大残差 15 px。
  **两个真因都不是硬件**(曾误判成"板级电气故障", 已撤销):
  1. **XIICPS 的 `ADDR` 寄存器放的是 7 位地址本身(`add[6:0]`), 没有 R/W 位, 方向由 `CR[RD_WR]` 给。**
     照"8 位形式"写 `(addr7<<1)|rw` 时硬件实际寻址 `0x3A`/`0x28`(都不存在)→ 永远 NACK。
     **判据: 两个不同地址都 NACK 本身就是编码错的信号。** 依据都在本机
     `embeddedsw/XilinxProcessorIPLib/drivers/iicps_*/src/`(`*_hw.h` 注释 / `XIicPs_SetupMaster` /
     例程 `EepromAddr[]={0x54,0x55}`)。
  2. **读 PS GPIO 引脚时 `DATA_RO` 偏移取错**: 真值是 `DATA=0x040+4*bank`、
     `DATA_RO=0x060+4*bank`(见 `gpiops_*/src/xgpiops_hw.h`); 之前把 `0x044` 当成 bank0 的
     DATA_RO, 其实那是 **bank1 的输出寄存器**, 读到的是残留值 → 误判成"线被拉住"。
  - 另两条实测要点: **复位放开后器件有一段自检窗口, 期间的读会 NACK** → 探测必须**轮询**
    (`Touch.open(retry_seconds=2.0)`), 不能一次定生死; **地址由复位释放那一刻的 TP_INT 电平选**
    (拉低→0x5D, 悬空→0x14)。
  - **坐标标定别手工点四角**: `touch_demo.py --calib` 自己画靶(屏幕坐标才是真值)→
    `fit_calibration` 试遍 8 种 swap/flip 取残差最小。这块屏实测**不交换不翻转**、量程基本就是
    800×480(`raw_x_min=-12,raw_x_max=787,raw_y_min=-15,raw_y_max=476`)。换屏后重跑 `--calib`
    并更新 `pynq_touch.py` 的默认值。
  - 跑 `--calib`/`touch_demo` 前先停 `pynq-status`(overlay 只能有一个持有者); 守护进程占着
    I2C0 时**不要**并发跑 `pynq_touch.py points`(两个主会互相踩)。
  细节见技能 `ac820-pynq` 的「触摸(GT911)」一节与 `ac820-board` 的 `references/peripherals.md`。
- **P5 已完成(2026-09-15): 数据页 —— 板子自洽的大文件队列与环形播放**。此前环形引擎
  (`awg_ddr_ring.py`) 只是**实验脚本**: 要手工停守护进程、手工起脚本, 面板上 DDR 只有一个
  占位符。现在文件经 Jupyter/scp 落到板子本地 `/home/xilinx/waveforms/`(面板只读它, 所以
  `install.sh` 用 `install -d -o xilinx` 建目录 —— root 建的 755 会让用户上传被拒), 面板
  自己浏览/排队/环形播放, 不依赖主机 GUI。UX 蓝本仍是主机侧「大文件数据流」。
  - **ch1 所有权归 `AwgCtl`**: `DdrPp.start()` 原来**绕过** `AwgCtl` 直接写 gpio7 ch1, 于是
    `GET DIGMOD` 的 SOURCE 影子恒显 PRBS7、`stop()` 后 SOURCE 位保持锁存(用户接着起普通数字
    调制会被接到旧数据源上)。新增 `write_user_ctrl()`/`write_user_ctrl_for_pp()`, 写都在
    `ctl.lock` 下 —— 于是 PP 的 ch1 写与任何 `SET` 互斥, 不会交错成"半条命令"。
  - **`ring_thread.RingThread` 是唯一碰 `DdrPp` 写的线程**(面板只读它的 snapshot); 环形
    决策 `feeder_decision.py` 是主机 `RingFeeder` 的纯逻辑移植; `DdrPp.feed_file()` 本地
    文件直喂, 与 UDP 那条路共用 `latch_bank()`。
  - **⚠ MODE 互斥是本页的中心约束**: `feed_file` 停播时装填要求 `digmod_mode == 0`, 而
    `start()` 要求非 0 —— **两者不可能同时成立**。所以顺序只能是 `MODE OFF` → 装填 →
    `MODE <调制>` → 起播。面板少任何一条门禁, 用户看到的就是"点了启动, 状态直接变 ERROR"
    (`PP_START_FAILED`)。**"队列非空"不算可播** —— 它只说明能装填。
  - **⚠ 三个只有板上验收才抓得到的 bug**(离线全绿也漏): (a) 入队 chip 回调不带行号 arg,
    `_ddr_command` 在 `arg is None` 时静默跳过; (b) `set_observed` 把设备回读的 `dig.mode`
    (停播时恒 OFF)**覆盖了用户的选择** → 底栏"启动数字调制"发出 `SET DIGMOD MODE OFF` →
    板上"点了启动什么也没发生"; `dirty` 也**不能**把 `dig.mode` 的必然差异算作脏, 否则一开机
    就挂"有待应用参数"(参考设计 `ui_model.h` 明写 `mode /* selected mode; OFF is actual
    runtime state */` 且 draft 不被遥测覆盖); (c) `ddr_start_block` 缺 MODE 门禁。
  - **⚠ `GET DDR PP` 曾是硬编码桩**(2026-09-15 修): 桥不能碰 `DdrPp`(写在 RingThread 上,
    还要读 DMA 寄存器), 所以那行一直是 P0 基线值 `ACTIVE 0 ... STATE IDLE`, 环形在播时是
    **假话**。修法: RingThread 每帧把同一份快照推给 `AwgCtl`(`set_pp_status`), 桥只格式化。
    **教训**: 上一轮的板上验收"通过"了这条, 只是因为它**绕过桥直接读面板快照** —— 判据走了
    近路就等于没判。同一轮还发现 `ddr_e2e` 里 `"UNDERFLOW" not in line` 是**恒真的空断言**
    (那条回包没有 UNDERFLOW 字段), 真值在 PL 的 `axi_gpio_8` bit2, 改走 `_user_status()`。
  - **验收**(`tests/ddr_e2e.py`, 走守护进程自己的 `attach_screen` + 真实命中路径)ALL PASS:
    装填 `B0=4096`、`actual_mode=QPSK`、`state=DRAIN`、`fed=8`、两个 bank 都活动过、**PL 侧
    DMA 源地址解出的 bank == 账面 `active_bank`**(`bank0/0`、`bank1/1` —— 硬件真的切了)、
    串口 `GET DDR PP` 报 `ACTIVE 1 / STATE DRAIN` 且 `ACTIVE_BANK` 观测到交替 `['0','1']`、
    停止后 `IDLE`/`ERROR 00`、underflow 干净、`GET CH1` 全程照常回。离线 **461 项**全绿。
    提交 `ecaedf1` + `30f9569`。细节见技能 `ac820-pynq` 的「DDR PP ring feeding over UDP」。
- **P6 已完成(2026-09-14): 面板 AWG 控制页 + 唯一真源**。`sw/awg_page.py`(CH1/CH2 波形/调制/
  频率/幅度、数字调制一行、复位默认, 全部用 −/+ 步进 —— 触摸屏没键盘), `sw/awg_ctl.py` 是
  命令语义唯一真源, 面板每次操作都走 `ctl.handle()`, 于是**面板改的 = 串口改的**, 串口 `GET`
  回读必然一致(这就是验收判据)。守护进程 `attach_screen` 里把状态页与控制页合成 `_DualScreen`,
  两页**共用同一个 `lcd` 和同一个 `TouchCursor`**。
- **⚠ 控件"点不动"先查命中判定与抬起沿, 别先怀疑触摸硬件**(2026-09-14 真因): 板上报"点控制页
  不跳转、按钮全点不动", 真因是 UI 层两个缺陷, GT911 一直是好的。
  1. **整屏大小的装饰控件吃掉所有点击**: `Ui._hit` 从 Z 序顶往下找第一个 `contains`, 而准星
     (`Crosshair`)的框是整屏、又总是最后 `add`(画在最上层)→ 每次点击都命中它。修法: `Widget`
     加 `tappable` 标记, `Label`/`ProgressBar`/`Crosshair`/`StatusBoard`/`LedRow` 置 `False`。
  2. **抬手沿被吞掉**: `TouchInput.poll()` 把"没有点"和"读失败"都返回 `None`, 而 UI 只在
     `down=False` 时才合成抬起沿触发 `on_tap` → 按钮永远停在 pressed、松手什么也不发生。
     修法: 记住最后按下坐标, 抬手回 `(x, y, False)`; **读失败仍回 `None`**(掉线不该被当成
     用户点了按钮)。页面侧用 `point[2]` 决定准星显隐, 但**不能因为 `down=False` 跳过
     `ui.step()`**。
  **为什么 180 项测试全绿却漏掉**: 测试助手 `_tap()` 直接调 `on_press`/`on_release`, 绕过了
  `_hit` 和沿合成 —— 恰好是坏掉的那两条路径。凡"控件响应"的测试都必须走真实
  `render()` → `ui.step()`。诊断顺序: 先看 `Ui._hit(x,y)` 返回谁, 再看输入源抬手回了什么。
- **P7 已完成(2026-09-14): 触摸 UI + draft/observed 事务模型**(P5 加了第七页, 见上)。照
  `docs/reference/`(参考设计 `AGENT_GUIDE.md`/`index.html`/`ui_model.c`)重做面板:
  `sw/ui_model.py`(纯逻辑: draft/observed 分离、`begin→CommandPlan→finish/fail`、回读校验、
  `STALE`/`RUNNING`/`DISCONNECTED` 门禁)、`sw/ui_view.py`(顶栏 48 + 左导航 91 + 工作区 368 +
  底栏 64, 触摸数字键盘弹窗; 七页 = 状态/波形/数字调制/信道/**数据**/射频扩展/系统, 导航节距
  54→51 是 P5 加第七项时改的)、`sw/pynq_status.py` 的
  `attach_screen` 组装 `UiView`。**回读校验读的是真 PL 寄存器**(`AwgCtl.read_registers()` 解码
  GPIO 位域), 不是 `AwgCtl` 的影子状态 —— 后者写进去读出来必然一致, 是同义反复, 抓不到"写没
  生效"(本项目有 BD 修订落后致 AXI BRAM 写入不生效的前科)。`awg_page.py` 保留不删但不再接入。
  验收: 离线 339 项全绿; 板上 `p7_e2e.py` **ALL PASS**(真实命中路径 → 草稿 → 应用 → 事务线程 →
  真寄存器回读 → 串口 `GET CH1` 一致); `glyph_check.py` 163 串 EXIT=0。提交 `5a53f55`。
- **⚠ 新增触摸控件必查的四条(2026-09-14, 离线全绿之后才在板上抓到)**:
  1. **建了但没注册进 `Ui` 的控件"看得见、点不动"**: `Ui._hit` 不递归进容器, 只遍历
     `ui.widgets`。底栏两个按钮由 `Footer` 画、却没 `u.add()` → 永远命中不到(实测「应用设置」
     点了没反应)。**遍历 `ui.widgets` 的可达性测试天生看不见漏注册**(它根本不在表里) ——
     必须另有一条"沿构造路径找全部交互控件、逐个断言已注册"的测试。
  2. **弹窗的"遮罩"只置 `tappable=False` 会漏点击**: 弹窗外的点会落到下层控件上。模态需要一个
     真正 `tappable=True` 的整块控件吃掉点击(`draw` 可为空), 且随弹窗一起 `visible`; 注册顺序
     在弹窗**之前**。故意不盖住导航列(留逃生口), 但程序化切页(关机)必须显式 `keypad.close()`。
     ⚠ 这两条曾**互相掩盖**: 遮罩测试当时能过只是因为应用按钮本来就点不到。
  3. **兄弟控件重叠已踩三次**(CH 选择器 vs 波形网格、键盘 vs 确认键、底栏两按钮重叠 18px ——
     后者是因为按"右边那个只有 105 宽"推 x, 实际 130)。x 必须按邻居**真实宽度**算, 保留
     "任何页任意两控件不重叠"的测试。
  4. **中文有板上实测的字号下限 14px, `has_glyphs()` 查不出**: 板上唯一中文字体
     `DroidSansFallbackFull.ttf` 在 11px 糊、12 差、13 勉强、14 可读; 开发机 msyh 同字号清楚得多,
     故**只能上板定**(`sw/font_probe.py` 渲字号梯子看)。`MIN_CJK_SIZE` 定义在 `pynq_lcd_ui.py`
     一处, `ValueRow` 默认 13 与 `Table` 头 `size-2` 都踩过。ASCII 不受此限。
  5. **⚠ 中文笔画粘连的根因是字形掩膜"非零即实心"(2026-09-14 修复)**: PIL 渲出的是带抗锯齿的
     灰度覆盖率掩膜, 而 `_blit_mask` 老代码写的是 `sub > 0` —— **每个边缘像素都被点成整格**,
     于是每笔两侧各胖 ~1px、字内/字间的窄缝(日/月 的框、信道/损伤 的笔画间隙)被填死, 14px 的
     中文整片糊在一起。实测 14px 下**非零像素里 37% 是 <50% 覆盖率的边缘像素**, 且画出来的墨量
     **恰好等于**非零像素数。修法: 按半数覆盖率二值化(`sub >= GLYPH_COVERAGE`, 即 128)。
     判据: `>0` 时"墨量 == 非零数", 回归测试 `test_blit_mask_drops_antialiased_edge_pixels` /
     `test_cjk_strokes_are_not_fattened_by_edge_pixels`(旧代码上必失败, 报 `assert 415 < 415`)。
     ⚠ 这与字号下限是**两件事**: 只把字号调大修不好它, 字号够也不代表它不存在。只有 `_blit_mask`
     受影响(`_polyline_mask` 用 PIL `line()`, 本来就是二值的)。
  6. **脚本化的点按要连渲三帧**: 可见性在 `render()` **开头**设、输入在**末尾**收, 所以切页那一帧
     结束时屏上还是旧页控件 —— 第三帧新页才真正可点(`p7_e2e._render(n=3)`)。
  7. **⚠ 为独立显示写的屏按整屏绝对坐标排版, 嵌进框架下必被栏压住(2026-09-14 修复)**: 守护
     进程的 `StatusScreen` 标题在 y=14、板子在 x=24 —— 它独占整屏时没问题, 但面板的顶栏(y<48)、
     左导航(x<91)、底栏(y>416)正好压在那套坐标上, 板上看到的就是「栏与界面重叠」。而且**整屏版式
     比工作区更高**(394px vs 368px), 只改原点仍会被底栏切掉一截。修法: 给嵌进去的屏一个显式
     `box`(工作区)与 `relayout` 重建控件、行高收紧一档, 由面板接管时调一次; `box is None` 保持
     整屏版式, 使独立/退路路径(`_StatusOnlyView`、`--preview`)**逐字节不变**(用渲染 PNG 的 md5
     前后对比验证)。另有一个坑: `StatusBoard` 的值列是固定 `+150` 缩进, 收窄卡片后最长的值
     (「Jupyter :9090 监听中」165px)会越过右边压到 LED 排上 —— 缩进要能传参、卡片要够宽。
     回归测试两条: 每个控件框都在工作区内 + 导航列右侧空白带整条是背景色。
  8. **跑要 import `pynq` 的板端脚本**: 单元的环境不被裸 `sudo python3` 继承, 要显式给
     `echo $PYNQ_PASSWORD | sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 <script>`;
     且**先停守护进程**(两个进程同时 `Overlay()` 会卡死板子)。
- **⚠ 屏闪根因(2026-09-14 修复): 状态页上两个 `Ui` 各建了一个整屏影子缓冲**。面板把守护
  进程的 `StatusScreen` 嵌进状态页, 而 `StatusScreen` 与 `UiView` **各自**建了一个 `Ui`;
  `shadow=True` 于是变成**两个整屏 `ShadowFramebuffer`**, 状态页上两边**每帧各推一整帧**到
  同一个真帧缓冲 —— 后推的那整块把先推的整块**盖掉**, 状态屏画的东西落下即被抹掉; 而一帧里
  两次整块拷贝的中间态被面板扫到, 看着就是**整屏闪**。离线复现判据: 状态页一帧 `flush==2`,
  且"后推者的影子 == 真帧缓冲"而"先推者的 != 真帧缓冲"(两者都不成立才算修好)。
  **修法**: 两边**共用同一个** `ShadowFramebuffer`(`Ui(framebuffer=...)`), 帧末**只推一次**;
  给 `Ui.step(flush=False)` 与 `StatusScreen.render(flush=False)` 作"只画不推"那半。
  回归网: `test_the_status_page_pushes_exactly_one_frame_per_draw` 等三条(拿**真的**
  `StatusScreen`+`UiView` 装配, 替身测不出这类装配缺陷; 已验证它们在旧代码上必失败)。
- **⚠ 部署板端代码后要核对 systemd 单元指向**(2026-09-14 踩到): `pynq-status.service` 的
  `ExecStart` / `LEDBTN_OVERLAY` / `ExecStartPre` **三处各自写了一遍 overlay 目录**, 一个落后
  就会在重启时**静默退回旧版本**(实测: 代码已部署到 `awg_merged/`、进程也在跑, 但 unit 仍指
  旧的 `ledbtn/`)。另外确认跑着的进程**归属 unit**(`ls -l /proc/<pid>/cwd`、`ps -o pid,ppid`):
  手工起的残留进程 PPID=1、不属于任何 unit, 退出后没人拉它回来。当前部署目录
  `/home/xilinx/overlays/awg_merged/`, `ledbtn/` 是旧的(留作历史)。改完 unit 记得 `daemon-reload`。
- **⚠ 连板子用 `ac820-pynq` 技能的 `scripts/pynq_ssh.py`(paramiko), 别用 `ssh`/`scp`**:
  本机没装 `sshpass`/`plink`, 非交互调用时 `ssh`/`scp` 弹不出终端提示, **Windows 会弹图形化
  凭据框**并把命令卡死。`run` 走 `exec_command`(非交互), sudo 要 `echo $PYNQ_PASSWORD | sudo -S <cmd>`;
  传文件用 `put`/`get`。git-bash 下远程绝对路径要加 `MSYS_NO_PATHCONV=1` 前缀, 否则 `/home/...`
  会被改写成 Windows 路径导致 ENOENT。
- **⚠ Linux 首次 PL AXI 读挂死 = FCLK 节流门(2026-09-23 已解决)**: Linux 的 disable-unused
  会把没有内核客户端认领的四路 FPGA 输出时钟写成 `FPGAx_THR_CNT=1`(`0xF8000178/0188/0198/01A8`),
  而 `FPGAx_CLK_CTRL` 照旧读出正常值; 此时首次 PL AXI 读按 UG585 §5.6.2 挂死整个 PS。
  修复在 `pynq_status.py::configure_ps_clocks()`(Overlay 前后清四路门 + 逐路校验
  `THR_STA` bit16 RUNNING); A/B 证据(`clk_ignore_unused` 单变量)与板上验收见记忆
  `axi-hang-evidence-first-probing.md` 与技能 `ac820-pynq` 同名章节。**不要只清 FCLK0**。

## 测试命令速查(全部从工程根目录跑)

```bash
# 上位机单元测试(单文件: 去掉 -k 换成文件路径; 单用例: 再加 -k 名称)
host/.venv/Scripts/python -m pytest host/tests/
python -m pytest host/tests/test_channel.py -q          # 单文件也可用系统 python
python -m pytest host/tests -q -k channel               # 按名过滤

# 板载 PYNQ 守护进程/UI 离线测试 —— 必须用系统 python(host/.venv 没有 PIL), 当前 478 项
python -m pytest pynq_overlay/sw/tests/
python -m pytest pynq_overlay/sw/tests/test_pynq_status.py -q   # 单文件

# 协议回归(需板卡串口) / 示波器闭环(需板卡+DSOX1102A, 必须系统 python, python -u)
python scripts/test_bridge.py COM11
python -u scripts/scope_verify.py COM11                 # 示波器不在时判据记 NOT RUN, 不标 PASS

# RF 板上 C 程序: 板上(rf_overlay/sw/board_app/) make; 开发机自检 make host-test
```

## 其他
- 板子资料/引脚/启动模式: 见 ~/.claude/skills/ac820-board 技能
- 上位机开发经验: ~/.claude/skills/fpga-host-app 技能

## PYNQ status + AD9361 集成接手记录（2026-09-27）

### 目标与阶段

- 已选方向：**单一合并 Overlay + C daemon sidecar**；先接入 AD9361 状态读取，再分阶段开放控制。
- 当前工作只进入集成准备/来源核对阶段；新工作树尚未导入下列两个脏工作区的未提交内容，也未开始合并或构建。
- 下一步先在新工作树逐项盘点并快照两个来源的 tracked diff 与所需 untracked 源文件，记录 hash/来源；再统一 Overlay/HWH/服务所有权边界。完成来源核对后先实现只读状态接入并验证现有 `pynq-status`，控制操作保持关闭；后续阶段另行定义和验收。

### 工作树与来源边界

- **本阶段专用 Codex 管理工作树（位于项目目录之外）：**
  `<LOCAL_PATH>
- 初始基线为 `b43d95440245083816fa0004bc043280702a23fa`（`claude/ad9361-rf-control-plane-impl` 的提交），当前是 detached HEAD；此提交只代表基线，**不包含** RF 来源工作树里的 Task 11 文档、产物及其他未提交修改。开始代码修改前应在该工作树建立本任务分支，并完成下述来源导入审计。
- PYNQ/主工程来源：`<LOCAL_PATH>`，分支 `master`，HEAD `663cf78ef4d1b60e1dc0123e7bd9bf9645aa8a50`，有未提交修改及 untracked 文件；其中含当前 PYNQ UI/status 与合并构建相关工作。
- AD9361/RF 来源：`<LOCAL_PATH>`，分支 `claude/ad9361-rf-control-plane-impl`，HEAD `b43d95440245083816fa0004bc043280702a23fa`，同样有未提交修改和 untracked 文件；最新板测记录与原始 Task 11 数据目前仍在该来源工作树。
- 两个来源工作区都保留原状；不要把它们的全部脏改动当成一个可直接复制的补丁，也不要从旧的嵌套 `agent-*` worktree 取代码。新工作树是唯一集成工作位置；每个导入文件都要核对来源、差异和 hash。

### 当前验证状态（以来源记录为准）

- RF Task 10 Steps 1–5 已记录 PASS；Task 11 物理连接正控/断线负控和 CHAN1 Vpp 阶梯已实测。Task 11 的正式数值 RX 门限判定仍是 **NOT RUN**，不将其表述为完整定量 PASS。
- Task 11 记录的示波器拟合为 `Vpp(mV)=0.00308×amplitude_counts+1.12`、`R²=0.9981`，仅适用于记录中的测试路径。详情及边界见 `<LOCAL_PATH>` §13。
- 板卡最后记录状态：`pynq-status active`、`rf-daemon inactive`，RX1 支路断开；这是 2026-09-27 的记录值，本节没有重新访问板卡。
- 权威记录优先级、Task 10/11 细节及未完成项仍按本工程 RF 来源 `CLAUDE.md`、`docs/rf/boardtest.md` §12–13、`rf_overlay/out/task10/` 与 `rf_overlay/out/task11/` 核验；不得因新工作树从已提交基线创建，就推定这些记录/产物已经存在于新树。
