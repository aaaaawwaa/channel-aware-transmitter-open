# HEADROOM-1：接收余量研究准备与控制合同设计

依据：用户在接收余量调查方案后回复“继续推进”。目标仍是BPSK教学、可解释环境诊断和人工确认。本设计细化已同意的单变量调查，发现当前候选不能合法采46dB后，先交付可执行的离线准备器与新合同规范。准备器没有网络、安装、布防或RF执行功能。

## 已核实的限制

当前已安装候选 `adfac1f14c3cdb379cb92569a21a9054`，daemon 1.0.3、协议1.5。 【**已过期**：截至 2026-10-17 的实际状态——板上已安装发布是 `b82b141e355b4cb1833969383a409c79`（daemon 1.0.4 / protocol minor 6 / `cnn-bpsk-headroom-v1`，2026-10-17 只读读回）。`adfac1f14…` + daemon 1.0.3 / 协议 1.5 是 2026-10-10 的历史快照，“当前”措辞已被推翻】
`rf_state_check_physical_loopback()`严格要求40000mdB；`op_loopback_physical_capture()`随后按请求真实设置衰减。因此先配置46000再确认40000会把硬件改回40000，不能作为46dB测试。环境load、UI物理确认和client也各有精确40000门禁。 【**已过期**：2026-10-18 route A——C 侧回环常量并入域单一真源（`rf_state.h RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB=46000`，报文由该宏生成），`rf_state_check_physical_loopback()` 现在严格要求的**就是 46000**，40000 变成被具名拒绝的值；本节描述的"native 只能 40000"阻塞随之改变。改动、跨语言钉子、数字与未做项见 `docs/superpowers/specs/2026-10-17-headroom-46db-environment-migration.md` §I 与 `evidence/rf-teaching/s2/46db-c-side-migration-2026-10-18/NOTES.md`。本节其余历史结论不改】

配置返回的worker外层`args`和UiApp applied草稿不是硬件证据。完整配置证据来自外层`result`内的native返回值，包含vendor getter读回和`hardware_verified`；物理事务还必须证明两路TX衰减相等且为46000。

## 本轮交付范围

新增 `scripts/rf_headroom_prepare.py` 与测试。它只接受当前精确候选、当前源码和候选冻结的baseline，验证release、daemon/native来源及所有生产软件摘要后，写入全新目录。缺失、修改、符号链接、候选/源码/基线错配均拒绝；拒绝不得连接板卡或产生准备成功记录。输出已存在时拒绝，不覆盖历史。

输出包含研究声明、normal/narrowband/pulse三个现有生成器的原TX载荷、每个载荷完整场景声明和SHA，以及兼容性报告。声明所有参数固定，不提供衰减/源幅度/等待/次数等可变选项。OFF是普通RX捕获且TX源关闭，不能把零波形物理发射称为OFF。输出明确 `execution_ready:false`、`rf_execution:BLOCKED_BY_EXACT_40DB_CONTRACT`、新增物理帧0、初始化0、正式S2未测。

不会根据手写manifest的“新能力”字段接受未来版本。未来研究候选必须由另外的资格链确认；当前准备器只为1.0.3/1.5精确冻结候选生成计划，永远不执行RF。不存在`--run`或隐式连接factory。

准备声明含：固定候选/daemon/build receipt、生产软件与native全部输入SHA、工具源码SHA、完整baseline文件与语义SHA、研究RF参数、RX参数、固定序列、source载荷/声明、停止条件及研究结论边界。收据对所有输出文件给出摘要，收据本身不作自引用摘要。准备成功只表示离线材料生成和完整性校验成功，与RF就绪和研究通过分别表示。

## HEADROOM-1固定实验（本轮不执行）

只改变TX40000→46000mdB。LO80MHz、Fs60MHz、RX/TX BW30MHz、2R2T、IDELAY启用tap29、BPSK3M RECT/D1/4096；PRBS7源及参考seed1。normal源60码；narrowband额外tone40码；pulse额外pulse40码。现有生成器、量化和无全局缩放保持。

一次初始化、最多五次尝试，固定 `off-before → normal → narrowband → pulse → off-after`。normal/narrowband/pulse各一次物理回环，两个OFF为普通RX。0us显式观测API时钟，不能加入settling wait。先预检未布防/TX源关闭/RX关闭，失败立即停，不重试、不替换、不补齐。

每次尝试前持久化attempt，失败原IQ、metadata、handoff、native配置读回、timing和错误均保留。触边、配置错配、缺原IQ、身份错配、超时、OFF同步/点/BER、关闭异常立即停止；finally解除，保存ACK及独立最终安全读回。没有关闭证据不能宣布研究完成。

normal同步/BER用于研究观察；干扰帧失步必须保留，不能挑正常子集作S2通过。46dB是新环境域：当前分析必须按真实RF配置返回 `INVALID/RF_DOMAIN_MISMATCH`；旧40dB baseline仅供复现拒绝。不能改成CLEAR或继承旧资格。保存的40dB三帧为历史参考，不构成严格配对；不把RX下降6dB定为成功阈值。

## 后续独立研究合同（规范，尚未实现）  【**已过期（截至 2026-10-17 的实际状态）**：该合同**已实现并已执行**——b67/b82 候选已具备 HEADROOM-1 研究动作；2026-10-15 两次真实派发（第二次进入 1 初始化/1 尝试，终态 `RUN_STOPPED_EVIDENCE:1`）；`cap_cfg` 整字误读亦已于 2026-10-16 具名位澄清（见后文“固定 RF 读回的寄存器位语义”）。“尚未实现”已不成立；但**46 dB 研究本体仍未成立**（无被接受帧、无削顶判定）】

引入独立命名的HEADROOM-1研究动作，贯穿UiApp研究plan→controller→worker→client→同一daemon拥有的core。旧教学load和physical动作保持40000；不能改变全局常量、伪造旧确认或串接tx.start/rx.capture绕开原子事务。独立动作必须只接受研究标识、46000、固定参数、0wait、同一持有者及一次初始化/五次尝试预算，包含OFF普通采集；失败消耗尝试并停止。次数和顺序在daemon研究会话中执行，不能仅由主机限制。

研究动作单独标识其请求/返回能力和采集身份；版本采用新daemon版本/协议minor，未公布精确编号前不能伪造现有版本的能力。客户端先检查真实daemon能力，缺失时不得初始化。场景load接受完整固定声明和载荷SHA，与normal/narrowband/pulse实际源绑定；不能放宽生产场景门禁到任意衰减。

捕获前native验证请求与真实RF配置，设置并读回两路TX46000后才允许源/RX武装。IQ header或同帧native metadata要绑定研究ID、动作类型、衰减和实际RF读回，防止“先46后40”的控制历史混淆。同帧预算/身份/读回交叉校验，再用原IQ完整重放BPSK、星座和环境拒绝。

该候选需独立C状态/协议/daemon假硬件回归、旧40dB合同回归、client/UI合同回归、ARM完整链一致性与资源检查，源/二进制/发布绑定，全部审查后才能安装并执行五次研究。若不满足则停在资格阶段；G1旧候选PASS不能替代新资格。

## 验证与推进顺序

1. 准备器TDD：真实候选成功、候选/软件/native/基线错配、参数改变、输出复用、缺载荷或改载荷、缺证据、伪造ready/成功子集均拒绝。所有路径网络factory调用0。
2. 编译现有纯C状态门禁做离线证明：40000+cable可批，46000拒绝，拒绝清旧批准；它不是ARM或实板验收。
3. 同一保存IQ仅将分析输入RF设46000做离线反事实，证明环境域外拒绝。必须标注没有采46dB物理帧，不能将此重放当新域验证。
4. 规范审查、代码质量审查，保存报告/摘要；当前阶段更新为研究合同准备完成、实测被现合同阻断。
5. 下一任务实现新研究合同与候选资格→有限HEADROOM-1→若新域值得继续，独立TRAIN/VAL及BPSK回归→18帧开发冻结→108物理+12离线正式验收与实体屏→S3人工建议→S4收尾。

本轮继续使用当前工作目录，新增文件与冻结候选分离；不提交或覆盖共享目录的无关变更。历史G2已停止，不能补采。

## 固定 RF 读回的寄存器位语义（2026-10-16 正式澄清）

本节是对上文"同帧预算/身份/读回交叉校验"以及
`docs/superpowers/plans/2026-10-12-real-headroom-research-runner-design.md` §2.4 中
"`pl.src_cfg/cap_cfg` 均为 0"这一**整字为零**表述的正式澄清。澄清不放松任何安全属性：
它把冻结原生寄存器本就存在的具名位语义写下来，并纠正一个与冻结原生行为相矛盾的读法。

### 寄存器事实（来源 `rf_overlay/sw/board_app/platform_rf.h:50-64`）

| 寄存器 | 位 | 名称 | 语义 |
|---|---|---|---|
| `RF_REG_SRC_CFG` (0x14, reg5) | bit0 | `RF_SRC_SEL` | TX 源选择（真正"开源"的控制位） |
| | bit1 | `RF_SMP_WRAP` | 样点缓冲循环/重复发射 |
| | bit2 | `RF_SMP_COMMIT` | 配置提交 epoch（边沿翻转，非控制位） |
| `RF_REG_CAP_CFG` (0x24, reg9) | bit0 | `RF_CAP_ARM` | 采集武装（真正"开采集"的控制位） |
| | bit1 | `RF_CAP_CONT` | 连续采集 |
| | bit2 | `RF_CAP_COMMIT` | 配置提交 epoch（边沿翻转，非控制位） |

`CAP_DONE` **不在** `RF_REG_CAP_CFG` 内，而在只读的 `RF_REG_CAP_STATUS`（reg13，bit13，
`platform_rf.h:56,66-67`）；因此 `cap_cfg=4` 既不是 DONE，也不是 ARM。

### 固定域的位判据（取代"整字为零"）

- `src_cfg` 的 `SRC_SEL`(bit0) 必须为 0；
- `cap_cfg` 的 `CAP_ARM`(bit0) 必须为 0；
- `src_cfg` 的 `SMP_COMMIT`(bit2) 与 `cap_cfg` 的 `CAP_COMMIT`(bit2) **允许 0 或 1**；
- `src_cfg` 的 `SMP_WRAP`(bit1) **允许 0 或 1**（2026-10-17 修正，依据见下节末）；
- `cap_cfg` 的 `CAP_CONT`(cap bit1)，以及两者 **bit3 及以上的未定义位**，仍必须为 0。

即 `src_cfg & ~0x6 == 0` 且 `cap_cfg & ~0x4 == 0`。

> 两个 bit1 号相同而语义相反，不能合并成一条规则：`src_cfg` bit1 是**本研究会自己请求的
> TX 样点缓冲模式**（`rf_daemon.c:2079` 传 `wrap=true`），`cap_cfg` bit1 是**会让采集永不停止的
> 连续模式**（`rf_daemon.c:2083` 传 `continuous=false`）。前者是配置残值，后者是"这一帧不是
> 固定交易"的证明，所以前者放宽、后者保持 fail-closed。

### 为什么这是澄清而不是放宽（原生行为证据）

1. 提交是**边沿**语义：`commit_toggle`（`rf_core.c:452-470`）实现 `(base & ~bit) | (~reg_value & bit)`，
   与 RTL 的 `commit_edge = commit_s[1] ^ commit_s[0]`（`rf_tx_source.v:144`、`rf_sample_capture.v:77`）一致；
   `axi_ctrl_v1_0_S00_AXI.v:598` 明说"电平会让第一次之后的每次提交变成空操作"。
   `rf_core_set_smpl_config`（`rf_core.c:1782`）与 `rf_core_configure_capture`（`:1834`）在写配置后翻转该位，
   所以**每次成功配置都会把 commit 位留成 1**，这是 RTL 要求的形态，不是活动状态。
2. 释放只清控制位：`rf_core_release_part_keep_state`（`rf_core.c:1537-1589`，注释 `:1507-1508`
   "leave the commit bits at whatever level they hold"）只清 `SRC_SEL`/`CAP_ARM` 并读回确认这两位；
   `rf_core_stop` 同规则。因此"采集→冻结 IQ→释放"之后的合法真实读回是：`cap_cfg=4`（每次
   `prepare_rx` 翻转 epoch），`src_cfg=4` 当本次加载用 `wrap=false`、**`src_cfg=6` 当本次加载用
   `wrap=true`**——即 HEADROOM 三个发射尝试的情形（2026-10-17 修正：本节原文写"正是 `src_cfg=0`（或 4）"，
   只覆盖了 `wrap=false`，与研究会自己请求的 `wrap=true` 相矛盾）。
3. 原生固定域判据本身就是具名位：`hr_rf_fixed`（`rf_daemon.c:1992-2006`）为
   `!(pl.src_cfg & RF_SRC_SEL) && !(pl.cap_cfg & RF_CAP_ARM)`；只读观测路径
   `gain_observation_path_idle`（`rf_core.c:1945-1957`）用同一规则。
4. 因此 Python 证据谓词原先"任意非零即拒"（`pynq_overlay/sw/rf_headroom.py` 的
   `if pl.get('src_cfg') or pl.get('cap_cfg')`）把 `cap_cfg=4` 报成 "SRC_SEL or CAP_ARM set" 是**谓词缺陷**，
   与已审查原生合同不一致；2026-10-15 第二次 46 dB 派发的 `RUN_STOPPED_EVIDENCE:1` 即此处
   （`evidence/rf-teaching/s2-headroom-research-attempt2-2026-10-15/board-download/session/attempt-1/decision.json`）。

### 必须保持 fail-closed 的部分

- `SRC_SEL`(bit0) 高 → 拒绝（TX 源选择）；
- `CAP_ARM`(bit0) 高 → 拒绝（采集武装；`CAP_DONE` 置位也不使其安全）；
- `CAP_CONT`(cap bit1) 高 → 拒绝（连续采集，不是有限交易）；
- bit3 及以上任何位 → 拒绝（未定义位）；
- 唯一允许的非零形态是：两个 commit 位可各为 0 或 1，`src_cfg` 的 `SMP_WRAP` 可另为 0 或 1，
  且**不得**与任何控制位或未定义位同时出现（即 `src_cfg & ~0x6` 与 `cap_cfg & ~0x4` 必须为 0）。

本澄清**不**授权在 runner、证据 JSON 或板端寄存器上做"清 4"绕行，**不**改变
daemon/core/RTL/软件映射，也**不**代替新候选资格、独立复审、安装、新授权/接线确认与板端预检。

### 2026-10-17 同族澄清：`src_cfg` bit1（`SMP_WRAP`）在 wrap 捕获后允许为 1

第三次 46 dB 派发（`evidence/rf-teaching/s2-headroom-research-attempt3-2026-10-17/`）在第 2 次
捕获之后被冻结谓词以逐字 `actual_rf_after.pl.src_cfg has SMP_WRAP (bit1) set` 拒绝，终态
`RUN_STOPPED_EVIDENCE:2`/exit 3。板端该帧的读回是

| 位置 | `src_cfg` | `cap_cfg` |
|---|---|---|
| `actual_rf_before`（attempt-1 之后、本次 load 之前） | 0 | 4 |
| `actual_rf_after`（本次捕获 release 之后） | **6** | 0 |

这不是缺陷 #1 的重复（那次是 `cap_cfg=4` 被当成武装），而是同一类"残值位被判为活动状态"
的第二个实例。`6 = SMP_WRAP(bit1) | SMP_COMMIT(bit2)`，成因在代码里是闭合的：

1. `rf_daemon.c:2079` 对本研究每一个发射尝试调用 `rf_core_configure_tx_path(core, 2540, true, NULL)`
   ——`wrap=true` 是**研究合同自己**要求的，`evidence/.../attempt-2/intent.json` 的
   `sent_args.load.wrap = true` 是同一事实的线上形态；
2. `rf_core.c:1780-1782` 无条件写该位并翻转 commit，因此写完即 `src_cfg=6`；
   `start_tx` 置 bit0 成 7（`rf_core.c:1223`）；
3. `rf_daemon.c:2090` 调 `rf_core_release_part_keep_state`，它只清 bit0（`rf_core.c:1562`），
   于是回到 6；`rf_daemon.c:2092` 的 after 读回发生在这之后。
   离线复现（真实 `rf_core.c` + 主机 PL 模型，无板卡）给出全部四个归档读回：
   `0 → 6（wrap=true）/ 4（wrap=false）→ 7/5 → 释放后 6/4`，见
   `evidence/rf-teaching/s2-headroom-src-cfg6-diagnosis-2026-10-17/work/probe_src_cfg_lifecycle.c`。

**为何允许这一位不放宽安全边界**（四点，全部可离线核对）：

1. 控制位才决定活路径：RTL 只在 `src_br`（同步后的 bit0）为 1 时使能样点读取器，寄存器 bit1
   经 `wrap_lat` 只影响读取器的循环方式（`rf_tx_source.v:130-176`）；bit0=0 时它不选择、
   不武装、不运行任何东西。
2. 原生固定域判据本来就不要它：`hr_rf_fixed`（`rf_daemon.c:1992-2006`）只测两个 bit0。
   本帧在原生侧 `native_error.stage=NONE`、`post_readback_code=0`、`terminal=AWAIT_ACCEPT`，
   即**安全门已放行、只有 Python 证据谓词拒绝**。Python 谓词不得比它所镜像的安全门更严。
3. 它不能被"泄漏"到下一帧：`rf_core_configure_tx_path`（`rf_core.c:1768-1795`）与
   `rf_core_prepare_tx`（`:1178-1205`）对 `wrap` 的两个分支都**显式写**该位，任何下一次
   发射配置都会把它覆盖成本次请求的值；不发射的帧（attempt-1/5）根本不写它。
4. 它也不能被用来把 `src_cfg` 的其它位夹带过去：严格掩码 `src_cfg & ~0x6 == 0` 仍拒绝
   bit0 与所有 bit3+，逐字 256 值穷举真值表见
   `pynq_overlay/sw/tests/test_rf_headroom.py::test_the_verdict_is_exactly_the_register_map_for_every_word`。

**仍然不知道、需要真机再确认的一点**：`off-before`（attempt-1）读到 0 是因为本次会话开始前
PL 的 `slv_reg5` 为 0（新上电/重新加载位流）；若同一 bitstream 未经重载就开新会话，
`off-before` 也应读到 6（上一会话最后一次 wrap 加载的残值）。因此本澄清**不得**把 bit1
与本帧场景绑定（"发射帧必须为 1、关闭帧必须为 0"是错的：`off-after` 会继承 attempt-4 的 1）。
允许"0 或 1"是唯一对两种历史都成立的规则。

本澄清同样**不**改变 daemon/core/RTL/软件映射，**不**授权任何新派发，**不**代替新候选资格、
独立复审、安装、新授权/接线确认与板端预检；并且因为 `rf_headroom.py` 属 46 项软件，
修正它就必须走新候选与新安装（见 `FROZEN_RESEARCH_MODULES` 引脚与 G2-RELEASE-ATTESTATION）。

## 研究帧的 JSON 结构与追加键的层级（2026-10-16 缺陷 #2 正式澄清）

本节补充上文"同帧预算/身份/读回交叉校验"，并**取代**任何"四个证据键位于帧对象外层"的读法。
依据：`docs/reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md` **§3 第 5 条 + 退出条件段**
（该报告没有编号 §3.5；历史证据 NOTES 中的"§3.5"属误引，本文件不沿用该写法）。

### 帧由两次写组成（来源 `rf_overlay/sw/board_app/rf_daemon.c:2130-2142`）

1. **模板**（`:2130`）：一次 `snprintf` 写出
   `{"type":"research_iq_frame",...,"headroom":{...,"timing_ns":[8 个括号]}}`，字面量以 `]}}` 结尾：
   第一个 `}` 关 `headroom`，第二个 `}` 关整帧；返回长度记为 `n`。
2. **追加**（`:2136-2141`）：第二次 `snprintf` 的起始偏移是 **`n-2`**，正好压在模板末尾那两个 `}` 上，
   写入
   `,"native_error":…,"closure":…,"timing_scope":"API_MONOTONIC_NOT_SAMPLE_EDGES","timing_order":[…8 个名字…]}}`，
   并把长度修正为 `n-2+extra`。
   因此四个追加键**写在 `headroom` 内**，其后的 `}}` 先关 `headroom`、再关整帧。
   追加失败或越界（`extra<0` 或 ≥ 剩余容量）时整帧作废，daemon 关闭研究并返回失败。
3. IQ 载荷接在**换行之后**（`d->frame[n]='\n'; send_all(c,d->frame,n+1+16384)`），不在 JSON 内。

### 谓词从哪一层读取（决定）

`pynq_overlay/sw/rf_headroom.py` 的 `check_research_frame` **只从 `headroom`** 读
`timing_scope`/`timing_order`/`native_error`/`closure`。理由：

1. daemon 是这四键的唯一生产者，且一直是 `n-2` 形态：2026-10-15 的真实第二帧
   `header.json` 外层 10 键中没有这四键，`headroom` 内 19 键中包含全部四键。
2. **兼容两层读取会再造一个"daemon 永远不会发出"的形状**——这正是缺陷 #2 得以存活的原因：
   宿主机夹具把这四键放在外层，一致性测试只看顶层键集合，于是把追加点从 `n-2` 改成 `n-1` 后
   整套 2116 条测试仍然全绿（上一轮复审 M10）。
3. 两层并存会让同一帧的"已审查值"取决于读者挑哪一层，属可被利用的歧义。

因此谓词对**外层**出现这四键**具名拒绝**（`the frame carries <key> outside headroom`），而不是
"优先读内层"：错层即拒绝（fail-closed，无任何放宽）。

### 判据未变的部分

- `timing_scope` 必须精确等于 `API_MONOTONIC_NOT_SAMPLE_EDGES`；
- `timing_order` 必须是 **JSON 数组**且恰为 8 个已审查名字与顺序（非数组类型同样具名拒绝）；
- `native_error.stage` 必须在既定集合内，六个 code 必须是 `[-5,0]` 的整数；
- `closure` 的布尔标志与终态一致性（`AWAIT_ACCEPT` 自洽、终态关闭字段）不变；
- 其余冻结门（帧身份、IQ 摘要、点计数、时序单调、寄存器具名位）全部不变。

### 边界

本澄清**不**改 daemon/core/RTL/寄存器，**不**建候选/不安装/不切换发布，**不**授权任何新派发；
`scripts/rf_headroom_research_run.py` 的冻结引脚与已安装发布的收据**未**随本改动重绑，
旧收据不为新代码背书。
