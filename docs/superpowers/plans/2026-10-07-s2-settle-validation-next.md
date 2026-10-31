# S2稳定等待验证接续 Implementation Plan（2026-10-07，待执行）

> **For agentic workers:** 使用executing-plans逐门槛执行；存在明确技能授权时独立规范及质量审阅可委派。保持现有dirty tree，使用隔离HOST_OBJDIR和证据目录；不重置、不自动提交、不将旧失败帧替换。当前用户任务是复盘与规划，本计划不是已执行记录。

**Goal:** 将已有等待接口补齐为可审阅、可安全收尾的有限对照交易，再验证它是否改善跨初始化稳定性。

**Architecture:** 延续唯一daemon/core拥有者、不可变IQ与后台分析，不加Overlay/MMIO。默认路径保持零额外睡眠；正等待显式请求并绑定同帧trace。原四帧driver保留，新等待实验采用独立入口或明确模式，版本、档位、数量和证据规则分别冻结。

**Tech Stack:** Vivado/Vitis2025.2既有PL；MinGW C假硬件测试；Python3.8板载软件；既有host/.venv和.venv-rf-ml；规定Paramiko helper；ARM原生构建。无新增依赖安装要求。

依据：`docs/superpowers/specs/2026-10-07-s2-settle-wait-design.md`及`docs/reports/2026-10-07-claude-progress-retrospective.md`。当前1.0.3/minor5仅源码，最后已验证板端1.0.2/minor4。G0优先；G1/G2属于后续执行范围，本次不连接设备。

## Chunk 0：G0离线闭合（下次优先交付）

### Task 0.1：保存可比基线和运行环境

**Files:** Create `evidence/rf-teaching/s2/settle-closure-<run>/baseline.json`、各测试日志；只读既有规格、源与原始记录。

- [x] 在改动前记录11个源文件SHA、C构建参数、系统/host/ML三个解释器路径及依赖可用性；保留本次210与54通过以及两个门禁反例。
- [x] 按目标解释器分开运行：board软件使用系统Python；GUI/普通host使用host/.venv；三个ML文件使用.venv-rf-ml。完整host结果按互不重叠文件集合计数，不把缺torch当算法失败，不静默skip。
- [x] 每次pytest使用全新basetemp；C使用全新HOST_OBJDIR。失败记录绑定退出码和原始日志，不统计字符串PASS/FAIL替代外层目标结果。

### Task 0.2：闭合等待失败和交易异常收尾

**Files:** Modify `rf_overlay/sw/board_app/rf_daemon.h`、`rf_daemon.c`、`rf_daemon_main.c`、`test/test_rf_daemon.c`、`test/test_rf_daemon_main.c`；必要时修改`rf_core.c`既有safe_shutdown调用接线，保持vendor/PL不变。

- [x] 先写RED：sleep非EINTR失败；EINTR后续睡；等待期间终止；capture timeout后EOF；失败发送；钟读0/倒序。验证owned交易最终尝试清SRC_SEL/CAP_ARM并确认结果；无法读回时记不确认，不伪报safe。
- [x] 睡眠回调返回状态，调用方不把失败当成功等待。将生产睡眠封装为可在离线测试中编译/注入syscall的单元；不能以PLATFORM_HOST排除生产函数后宣称已验证。
- [ ] 让终止状态可被活跃serve/transport观察，并进入同一owned安全收尾入口；保持初始未获所有权时不操作他人core。不得仅拉长5秒service退出预算或放宽watchdog来掩盖问题。
- [x] 失败关停使用既有FAULT可用的safe_shutdown路径，不依赖会在FAULT中拒绝的普通setter；正常默认路径不新增硬件操作。
- [ ] 修复2026-10-07简审发现的默认路径重复清ARM：成功finish已释放RX时不再调用setter，仅对真实未释放的资源收尾；验证缺省/0捕获硬件读写不增加，异常路径仍清TX/RX。F2条件变异存活先判断可达状态及语义，不据此直接判代码错误。
- [ ] 修正零档trace测试：先明确请求device.info，字段存在性与数值分开检查。补请求1/5000、非整数/负数/5001、NULL、时钟逆序等边界；做关键断言变异，证明测试能抓住失败。

> **G0 执行记录（2026-10-07，离线）**
> - **0.1 完成。** 基线落盘 `settle-closure-20261007-v1/baseline.txt`。分解释器基线：board **1872/4skip**、tools **121**、host 除ML **902**、ML文件(`.venv-rf-ml`) **54** —— 四套件全绿，独立证实"host 6F/36E 纯为缺 torch"。
> - **0.2 部分完成。** 新增 `rf_sleep.{h,c}`：`rf_sleep_us` 逻辑与真实 nanosleep 适配器 `rf_sleep_default` **同编进主机与板上构建**（实测 MinGW 提供 nanosleep，故板上跑的适配器就是主机测的那一个）。`sleep_us` 改为返回状态，等待未完成以 `RF_ERR_TIMEOUT` **拒绝整笔交易**。新目标 `host-test-sleep` 14/0。
> - **0.2 的 FAULT 收尾缺口已闭合。** RED 复现：`FAIL ending the connection brings the faulted part to rest`。既有手段均不可用——按"观察到活动"回退会破坏 5 条连续捕获测试；以 `RF_STATE_FAULT` 判别直接调 `safe_shutdown` 会抹掉 FAULT 证据并破坏 2 条测试。按用户决定新增 core 级 `rf_core_release_part_keep_state`（清 SRC_SEL/CAP_ARM 且**不动 state**），disconnect 规则仅在 `RF_STATE_FAULT` 且仍有活动时调用。`test_rf_core` 90→**94/0**，`test_rf_daemon` 387→**396/0**，C 套件零 FAIL。
> - **0.2 剩余**：终止旗在活跃 serve 期间的可观察性（第3条）未做；零档 trace 断言加固（第5条）未做。
>
> **独立审阅结论（两个互不重叠的代理）**
> - **质量审阅：APPROVED。** 所有声明数字独立复现；PASS 描述集合差分显示改动过的二进制**0 条断言被删除**（daemon 387→399、core 90→95 均纯增）；`test_platform_rf.c` 纯增行；`fake_pl.c` 的 CNT 接受规则被**加强**。未发现任何门槛/判据被放宽。
> - **规范审阅：首轮 NOT APPROVED**，发现 6 项，已按下述处置。**其中 F1 推翻了我此前的一个结论。**
>   - **F1（阻塞，已修）**：失败交易**没有**清 TX 源——原实现把清除放在 `if (rc == RF_ERR_OK)` 内。我上一轮那条 `a capture that failed after TX came on releases SRC_SEL` **通过的原因是错的**：它观测的是 `serve()` 返回后的状态，而那时断线规则已经清过了；其注释把功劳记到从未运行的路径上。现按"是否真的开启过源"与"core 是否 FAULT"决定，并补 `timing_before_read` 断点测试（在 EOF 前、连接仍存活时采样寄存器）。**变异验证**：把旧门禁加回 → `FAIL the source is ALREADY off inside the transaction`（398/1）。
>   - **F5（阻塞，已修）**：真实适配器的 EINTR 分支在 MinGW 上不可达，把它改成"中断即报告完成"能骗过全部 14 条测试。已把该决策抽成纯函数 `rf_sleep_attempt_outcome`。**变异验证**：`*ns = 0` → 3 条 FAIL。
>   - **F3（已修）**：新 core API 补 0xDEADBEEF 哨兵守卫 + 不可读寄存器测试。**F4（已修）**：尝试预算 off-by-one。
>   - **F2（仍开放，如实记录）**：`apply_disconnect_rule` 的 `== RF_STATE_FAULT` 判别**无法被现有测试证伪**——改成 `!= RF_STATE_READY` 仍通过全部 399 条。用读失败夹具构造反例**未能区分**（读失败会先让 `do_stop` 把 core 打成 FAULT，反而触发该分支）。这是真实覆盖缺口。
> - 收据：`evidence/rf-teaching/s2/settle-closure-20261007-v1/stage-checkpoint.json`；最终回归 `final-regression.log`。

### G0 执行记录 round 2（2026-10-08，离线）

- [x] **0.2 第5条** 零档 trace 断言加固：脚本现在先请求 `device.info`，并把**字段存在性**与数值分开断言。变异：零档省略字段 → FAIL。
- [x] **0.2 第3条** 终止旗可观察性：新增注入 `cfg.should_stop`；`run_loop` 在每次阻塞读前检查；`rf_daemon_main.c` 用 `sigaction`（**无 SA_RESTART**）安装 SIGTERM/SIGINT，`conn_read` 在挂起停止时不再重试 EINTR。受控终止因此走**普通确认收尾路径**。变异：移除检查 → FAIL。⚠ `conn_read`/sigaction 在板上专有区，**主机测不到**（socket/信号无主机等价物），只能靠 ARM 构建阶段闭合。
- [x] **0.2 RX 半边** 改为仅失败路径。**过程中发现并修掉作者引入的缺陷**：原条件在**成功路径**上也会运行，给正常捕获多加一次硬件写，违反"正常默认路径不新增硬件操作"。F2 与 RX 半边当前**不可观测**（任何让器件仍活动的失败都会先 fault core），已如实记录含构造失败的证据。
- [x] **0.3 八帧硬上限**：档位校验改为**批准集合相等**（旧范围检查会放行 `(0,1000,2000)` 并产出 12 帧）；`APPROVED_FRAMES` 不随 `len(levels)` 变；`SESSION_ORDER=((0,1000),(1000,0))`；逐帧记录 requested 与 actual，实际不足即判不合格并停止且**保留原 IQ**；无 baseline 拒绝启动。
- [x] **0.4 版本/类型/每帧证据**：修掉审阅者点名的第二个反例（minor-5 零档**省略**字段会被接受）；严格整数类型（`False != 0` 与 `0.0 != 0` 均为假）；孤立 `settle_end_ns` 与缺单个 bracket 拒绝；显示环境与保存记录纳入验收（经既有 `read_record` 重读字节）；**每帧同 IQ 数学复验**。11 条逐项损坏用例全部必须失败。
- [x] **0.5 宿主 driver**：新建 `scripts/rf_capture_settle_run.py`。门禁 1.0.3/**minor5 配对**；baseline 必需且有界；上传哈希守卫（源码 SHA **与** baseline SHA）先于任何 sudo；inspect 脚本先验 SHA 再以 root 执行；`passed` 要求 8 帧在批准版式上、`comparison_valid`、每帧复验通过、两会话 seeds [1,37]、板态来自独立读回＋解除 ACK。旧 1.0.2 四帧 driver 原样保留。
- [x] **0.6 全量回归 + 独立审阅**：见下。

**round 2 最终回归**：board软件 1874/4skip、tools 155、host除ML 902、ML文件 54；C `test_rf_daemon` 417/0、`test_rf_core` 98/0、`test_rf_sleep` 22/0、startup 92/0、`make exit=0`、vendor integrity 10 files passed。

**round 2 独立审阅**：质量 **APPROVED**（PASS描述集合差分 **0 条断言被删**）。规范**首轮 NOT APPROVED**，唯一阻塞项是**作者自己的流程失误**：`test_wait_campaign_rejects_any_design_that_is_not_the_approved_one` 少传 `baseline`，而 `wait_campaign` 的 baseline 守卫排在档位检查**之前**，于是**那些坏设计是被 baseline 守卫拒绝的**，档位检查零覆盖——**把档位检查整段删掉，49 条测试仍全绿**。作者先前那次变异验证**当时有效**，但 baseline 守卫是在那**之后**才加的，而变异**没有重跑**，一条真证据悄悄失效（与 round-1 的 F1 同类）。已修（传 baseline + `match='approved'`），第三个独立代理复验：变异 → 该测试 `DID NOT RAISE`，此前全绿；恢复 → 155 passed；且确认无路径能让未批准档位到达 `factory()`。
复验还发现**同一机制的相邻一例**（`test_a_minor_4_frame_is_not_accepted_as_a_waiting_frame` 实际由 daemon 版本检查满足，非 settle 块），判定**非真漏洞**（版本检查正是该性质的编码方式、settle 块对该输入按设计不可达），但已把 match 钉到它依赖的那条消息，使依赖显式而非偶然。

**新增教训（已写入 `docs/reports/2026-10-07-g0-handoff.md`）**：加新守卫之后**必须重跑旧变异**；且任何 `pytest.raises` 都要 match 到它名字所指的**特定消息**，否则一个更早的守卫就能满足它。

**G0 仍未关闭**：生产 sleep / 终止 / sigaction 路径**未经任何 ARM 编译**（板上专有区）；计划要求的"超长等待的交易期限与偏差报告"未做。收据 `evidence/rf-teaching/s2/settle-closure-20261007-v1/stage-checkpoint-round2.json`，源码清单 `g0-round2-sha256.txt`（20 文件，另冻结于 `frozen-sources-round2/`）。

### Task 0.3：固定八帧实验和可解释对照

**Files:** Modify `scripts/rf_capture_timing_probe.py`、`scripts/tests/test_rf_capture_timing_probe.py`；必要时更新原规格实验段，写明新顺序而不覆盖旧声明。

- [ ] RED：第三档、缺零档、替换1000档、重复档、非法类型均在factory/硬件会话创建前拒绝；固定支持集合`{0,1000}`、最多2会话/8尝试，不因len(levels)变更批准数量。
- [ ] 将“允许档位集合”与“执行顺序”分开。建议会话0按0→1000，会话1按1000→0，以降低初始化后先后顺序混杂；实际PRBS7 seed固定1，参考seed1/37按声明记录。保留顺序局限，八帧不足以证明AGC因果。
- [ ] 每帧记录requested和actual等待，不修饰原值。证据完整与对照有效分字段报告：实际等待短于请求值时原IQ仍保留，该档不合格且停止调查；允许调度造成超时长等待，但预先设定交易期限并报告偏差。
- [ ] wait=0无新增settle brackets；正值为10次clock，零档8次clock。记录仪器开销，保持RF、幅度、gain/AGC、BPSK/环境判据不变。
- [ ] 削顶/rail、重复联合身份或IQ、缺证据、等待/采集失败、关闭未确认立即停止，不重试、不补齐为8个成功帧；保留初始错误和cleanup/close错误。

### Task 0.4：补版本、类型及全部每帧证据门禁

**Files:** Modify `pynq_overlay/sw/rf_status_client.py`、`pynq_overlay/sw/tests/test_rf_capture_timing.py`、`scripts/rf_capture_timing_probe.py`及其测试。

- [ ] RED本次已复现的两反例：正等待info minor4携带完整字段；minor5零档缺requested字段。基础minor4无等待仍兼容，正等待hello及回读均minor≥5；minor5 trace必须有严格整数requested并与实发值一致。
- [ ] 独立落盘验证对row/sidecar/trace均使用严格整数类型，拒绝false/0.0；legacy拒绝孤立begin或end。检查正uint64、有序时间、frame联合身份和IQ摘要。
- [ ] 将实际handoff、显示结果、实验record/metadata/frame.iq、载波相干度及拒绝原因纳入最终验收；逐字段损坏或删文件必须失败。复用既有record数学验证函数，不能只凭probe部分检查passed。
- [ ] 同IQ分别复验环境、BPSK、星座和保存记录；失步应无伪点/BER，复验通过表示重现拒绝，不表示解调成功。

### Task 0.5：接通宿主上传、下载与独立inspect

**Files:** Prefer Create `scripts/rf_capture_settle_run.py`及`scripts/tests/test_rf_capture_settle_run.py`；复用现有`rf_capture_timing_run.py`低层工具，保留旧四帧入口和fixtures。Modify candidate工具只在确实需要时。

- [ ] RED：新1.0.3/minor5候选可进入新实验前置资格；错误major/minor/daemon/软件SHA/ELF来源拒绝且不联网。原1.0.2四帧driver行为不变。
- [ ] 新driver声明固定2会话、8尝试、两个档及顺序；传入对应板端模式，下载全部IQ/header/显示/record/trace与失败日志；独立inspect脚本另验SHA后才执行。
- [ ] 上传SHA失败禁止sudo采集；传输、运行、下载、close任一失败仍保留收据并独立检查安全；不得把缺原IQ当成功或自动重跑。
- [ ] fake-network测试覆盖8帧计数、标签、源摘要、下载中断、主错误＋cleanup错误共存及inspect不能执行的情况。最终safe必须来自独立PL/daemon读回与解除ACK，不用close_error为null代替安全证明。

### Task 0.6：完整离线验证与独立审阅

Run（示例basetemp须换成该run下未存在的目录）：

```powershell
& '<LOCAL_PATH>' -m pytest pynq_overlay/sw/tests -q -p no:cacheprovider --basetemp evidence/rf-teaching/s2/settle-closure-20261007-v1/pytest-sw
& '<LOCAL_PATH>' -m pytest scripts/tests -q -p no:cacheprovider --basetemp evidence/rf-teaching/s2/settle-closure-20261007-v1/pytest-tools
& host/.venv/Scripts/python.exe -m pytest host/tests -q --ignore=host/tests/test_rf_ml_evaluation.py --ignore=host/tests/test_rf_ml_export.py --ignore=host/tests/test_rf_ml_training.py -p no:cacheprovider --basetemp evidence/rf-teaching/s2/settle-closure-20261007-v1/pytest-host
& .venv-rf-ml/Scripts/python.exe -m pytest host/tests/test_rf_ml_evaluation.py host/tests/test_rf_ml_export.py host/tests/test_rf_ml_training.py -q -p no:cacheprovider --basetemp evidence/rf-teaching/s2/settle-closure-20261007-v1/pytest-ml
```

C在board_app内调用MinGW make，明确指定隔离HOST_OBJDIR并运行`host-test host-test-core host-test-iq host-test-protocol host-test-daemon`；vendor完整性单独用真实Python执行，避免Windows python3别名。

- [ ] 关键RED→GREEN、默认路径字节兼容、归档minor4四帧和旧driver回归通过；完整离线回归报告实际结果及环境，已知预期负例与真正目标失败分开。
- [ ] 独立规范与质量均批准，审阅检查上限/退出/版本/原IQ来源，不只重复作者测试。仍有P1或生产睡眠未编译时G0不关闭。
- [ ] stage-checkpoint绑定全部源码SHA、原始日志/退出码、反例及审阅结论；统一CLAUDE.md/报告/计划状态，明确ARM/安装/实采/正式/真人NOT_RUN。

**G0退出条件：** 新八帧driver离线走通、故障关闭有证据、回读门禁和数量上限成立、生产睡眠单元可编译、独立审阅批准。仅有测试数量增加不够。

## Chunk 1：G1精确ARM资格（G0完成后）

**Files:** 复用`scripts/rf_build_daemon.py`、`scripts/rf_environment_candidate.py`、`scripts/rf_environment_arm_check.py` and `scripts/rf_environment_run_arm_check.py`既有构建/打包/离线验证工具；保存新candidate和独立ARM收据。

- [ ] 若已有本地交叉工具链可离线构建则先用它；否则板端原生构建属于后续设备工作。不要把“原生编译”误记为纯主机离线。
- [ ] 构建含生产sleep/终止路径及全部原生假硬件测试；绑定全部输入与ELF摘要，manifest版本1.0.3与minor5 provenance一致；不加载Overlay或touch active硬件。
- [ ] 精确候选主机/ARM同输入一致、原生CNN可用，完整后台100次P95≤300ms，RSS满足既有预算。不是触摸体验计时，用户的体验计时豁免保持。
- [ ] G1通过后生成不可变候选，安装前状态和父发布由独立检查读取；不得用本次保存的旧安全状态代替未来实时检查。

## Chunk 2：G2安全安装与最多八帧对照（G1后）

- [ ] 使用规定Paramiko helper，保存可恢复旧发布；只激活已资格验证的准确候选。校验服务/发布/PL关闭状态，不第二次Overlay。
- [ ] 在TX1→RX1及40dB/LO80MHz/Fs60MHz/BW30MHz/2R2T/tap29固定域按冻结声明一次执行。零档/等待档均instrumented；最多8尝试、2初始化会话。
- [ ] 完整下载和同IQ重放；分别报告正常稳定性、窄带异常识别、载波相干度、同步/无BER拒绝、raw RMS/peak/rail、实际等待和gap。不可只用“无削顶”判断成功。
- [ ] 任一拒绝或失败保留；两会话各解除ACK，独立最终unarmed/TX/RX关闭、发布与服务验证。未经确认的安全状态不得标passed。
- [ ] 分析只给“有限对照支持/不支持/证据不足”，不宣称AGC因果或根因已修复。八帧不能替代完整18帧开发pilot或正式TEST。

## Chunk 3：G3/G4回到既定交付主线

- [ ] 若等待有效，定义明确支持域及是否仍仅诊断可选；不能直接改为生产默认。必要时重新TRAIN/VAL及S1/BPSK回归，再执行完整18帧开发复验和同IQ重放。
- [ ] 若无改善、仍削顶或失步，停止该路线实采，保留所有失败；另设实际增益/前端余量/数据路径单变量调查，不同时改gain、幅度和阈值。
- [ ] 开发域稳定后冻结代码/基线/场景/工具/来源，才开始90已知＋18实板负控＋12离线拒绝TEST；正式开始后不调门槛、不替换失败帧。
- [ ] 实体屏环境功能验收后关闭S2；最后才进入S3人工确认建议。BPSK必交/QPSK条件扩展、人工接管方向不变，本轮不新增QAM/RRC接收、EVM或自动发射。

**本次选择：** 推荐G0→G1→G2顺序。直接上板会把已知driver、数量和收尾缺口带入硬件；改AGC/MGC会改变第二个变量并引入重新校准，本轮暂不采用。本文件是下一阶段安排，未执行上述修复或硬件动作。
## 2026-10-08简审接续（当前优先项，未执行）

G0离线主体已有round2冻结，但此次发现新的闭环缺口，先关闭以下条目再进入G1/G2。不要仅凭已有[x]或离线测试数量跳过。

- [x] 补板端同IQ重放脚本全部传递依赖和正确package布局，将其摘要加入上传守卫；在清洁隔离目录验证真实导入及新八帧passed成功路径，保持不依赖板端残留文件。
- [x] 旧四帧driver给新版验证器传本地下载records_root；归档真实四帧完整复验，保留旧minor4兼容。
- [x] baseline在连接前完成schema、非空、冻结SHA及与候选关系检查；空/错误/不同baseline不得到connect。
- [ ] 修复should_stop检查和recv阻塞之间的信号窗口，补真实Linux信号/套接字测试；源读分析不记为动态通过，ARM编译不代替运行验证。
- [x] 补超长等待交易期限、偏差报告、失效停止与确认关闭，原IQ保留。

最新审查：`docs/reports/2026-10-08-claude-round2-review.md`；当前C417/98/22及241项相关Python复核通过，但本次代码审阅CHANGES_REQUIRED，ARM/硬件/正式仍NOT_RUN。

### G0 执行记录 round 3（2026-10-08，离线，闭合 round-2 审查全部条目）

**P1 板端重放依赖 — 已修并变异验证。** `rf_capture_settle_run.py` 现上传 9 个脚本（补 `rf_environment_verify`/`rf_teaching_verify`/`rf_bpsk_replay`），统一放到 `remote/scripts/` 使 `scripts.<name>` 与 probe 的 `parents[1]` 一致，declaration 记录 `upload_subdir`，守卫覆盖全部上传源 SHA。新测试在**干净 mock 板端目录**（上传子目录 + 代表已安装 release 的目录，repo 不在 `sys.path`）中真实导入 probe 与整条重放链。变异：删掉三个重放模块 → `FAIL No module named 'scripts.rf_environment_verify'`。

**P1 终止检查窗口 — 已修并变异验证（宿主可测的一半）。** 新增 **self-pipe + `poll()`**：handler 写一个字节（`write` 在 async-signal-safe 列表内），`conn_read` 同时 poll 套接字与管道，因此信号落在"检查后、recv 前"或分片之间都能立即唤醒；`run_loop` 在**读完一行之后、dispatch 之前**再查一次 `should_stop`（完整请求不得在停止请求后被派发）。新测试从**读回调内部**置旗，观测到"请求确已被读走（`stop_read_calls>=1`）却未被派发（无 `"id":1`）"。变异：删掉后置检查 → `FAIL ...does not dispatch it`。
⚠ **诚实标注**：self-pipe/`poll`/`sigaction` 一半位于 `#ifndef PLATFORM_HOST` 的**板上专有区**，主机测试**到不了**，本轮**未做任何离线验证**，只能由 G1 的 ARM 构建（及真实 Linux 信号/套接字测试）闭合。该项在计划中仍保留为未完成。

**P2 旧 driver 兼容 — 已修并变异验证。** 旧四帧 driver 现传 `records_root=output/'records'`；新测试用**下载后形态**的镜像（record 在 `records/<id>/`，而 receipt 仍指向板端绝对路径）驱动完整四帧成功路径，因此只有"传对下载根"才能通过。变异：拿掉 `records_root` → FAIL。

**P2 baseline 前置 — 已修并变异验证。** 新增 `load_frozen_baseline()`：以 replay 所用的同一 `EnvironmentBaseline.from_dict` 解析，并要求与候选 `rf_environment_baseline.json` **规范 SHA 相等**，全部发生在 `connect()` 之前。新测试覆盖空白 / 非法 JSON / 截断 / 合法但不同 / 候选缺文件五种，均**不得建立会话**。变异：退回"仅查文件"→ 5 条 FAIL。

**G0 每帧期限与偏差 — 已实现并变异验证。** `wait_campaign` 逐帧记录 `settle_actual_ns`、`settle_deviation_ns`（原始有符号）与 `settle_deadline_ns`；超过 `SETTLE_WAIT_DEADLINE_NS`(=5000us) 判不合格并以 `WAIT_OVER_DEADLINE` 停止，**原 IQ 与原始偏差保留**；期限内超长等待仍合格并原样报告。期限写入 driver declaration 与 `stop_conditions`。变异：删期限检查 → FAIL；删偏差记录 → 2 FAIL。

**两条真实成功路径（审查要求）**：旧 minor-4 四帧（`test_the_archived_four_frame_shape_is_a_real_success`）与新的 minor-5 八帧（`test_the_approved_eight_frame_grid_is_a_real_success`）在下载形态镜像上端到端走通，逐帧经共享验证器复验（含 baseline 与 records 根）。上游为九个测试文件补了共享夹具：`saved_case` 增加 `iq_seed`（解耦样本与连接代数）与 `actual_wait_us`（解耦实测等待与请求等待），另建 `downloaded_case` / `frozen_baseline_document` / `safe_state_document` / `StateSftp`。

**round 3 完整离线回归**：board软件 **1874/4skip**、tools **166**、host除ML **902**、ML文件 **54**；C `platform_rf` 64/0、`test_rf_daemon` **424/0**、`test_rf_core` 98/0、`test_rf_sleep` 22/0、`test_rf_iq` 40/0、`test_rf_protocol` 58/0、`test_rf_state` 92/0、`test_rf_daemon_main` 39/0，`test_selftest` 的两条 FAIL 为**预期负例**（`SELFTEST HARNESS OK`），`make exit=0`；vendor integrity 10 files passed（真实 Python）。

收据 `evidence/rf-teaching/s2/settle-closure-20261008-v1/stage-checkpoint-round3.json`，源码清单 `g0-round3-sha256.txt`（11 文件，另冻结于 `frozen-sources-round3/`）。

**G0 仍未关闭**：生产 sleep / 终止 / sigaction 路径**未经任何 ARM 编译**；`poll`/self-pipe 只在源码层可达分析之外**无动态证据**。**NOT_RUN**：native ARM 构建、安装、任何 RF 采集、8 帧对照、S2 正式 freeze/108/12、真人、S3；削顶根因仍未证明，本轮不作任何相关声明。
