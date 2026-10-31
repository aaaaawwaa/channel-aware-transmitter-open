# S1 BPSK教学观察与实验记录 Implementation Plan

**最新门禁修订：** 用户确认操作体验不卡顿，无需专门计时并授权下一阶段。S1已关闭，精确触摸时延仍NOT_MEASURED、非阻塞；闭合收据 `evidence/rf-teaching/s1/closure-2026-10-04/receipt.json`。下方旧待计时状态保留历史。

> **For agentic workers:** REQUIRED: 使用executing-plans执行。独立审阅按适用技能委派，步骤使用复选框。现有dirty tree原位保留，提交只包含明确归属文件，不reset或混合提交。

**Goal:** 在已验收BPSK3M RECT链路上增加教学入口、真实接收星座和可回放实验记录，不扩展算法支持域。

**Architecture:** 纯星座函数读取同帧IQ与已冻结BpskEnvelope，复用算法既有同步估计；worker同帧交接，UiApp统一身份与过期校验，UiView只画真实点。导出从不可变当前帧生成，后台写本地文件，复用既有release与控制流程。

**Tech Stack:** Python3.8/numpy1.17.4、pytest、现有800×480 UI/GT911、不可变RxIqFrame和版本化发布。规格：`docs/superpowers/specs/2026-10-04-teaching-environment-scope-design.md`，总体路线：`docs/superpowers/plans/2026-10-04-teaching-environment-roadmap.md`。

状态：S0五项核心真人已确认；S1 c3bf915c已安装，新33帧物理/数学重放/100次ARM资源门禁均通过。11张实体屏照片及用户文字确认闭合真人功能；20次触摸计时用户明确未测，S1整体仍未关闭。最新报告docs/reports/2026-10-04-s1-board-validation.md；下文旧离线收据保留为历史。

## Chunk 1：纯数据与能力入口

### Task 1：冻结基线与教学能力

Files：Create `pynq_overlay/sw/rf_teaching_profile.py`、`pynq_overlay/sw/tests/test_rf_teaching_profile.py`；Modify `pynq_overlay/sw/ui_model.py`和`ui_view.py`；证据`evidence/rf-teaching/s1/`。

- [x] 保存993ffe21 manifest、相关源码hash及M4/真人收据，不执行Overlay或强制接管已布防板卡。
- [x] 定义只读教学能力：AD9361/BPSK/3M/RECT/Fs60M/D1/4096；RF默认LO80M/BW30M/衰减40000mdB/PHY0/idelay1 tap29，TX峰值60，显式RX参考默认关闭。教学能力描述与应用草稿动作分开。
- [x] 写红例：能力读取无控制调用；“填入教学配置”只走现有setters改独立TX/RX草稿、清除旧结果/加载凭据，不布防、初始化、加载或发射。
- [x] Run `python -m pytest pynq_overlay/sw/tests/test_rf_teaching_profile.py -q --basetemp=evidence/rf-teaching/s1/red-profile`；确认新入口缺席的有意义FAIL并留日志。
- [x] 最小实现教学入口和高级工具入口。高级QAM/RRC发射保留，接收页标未支持；旧ACM2108控制不改，模型六分类标签不裁剪。
- [x] 重跑GREEN；Run `python -m pytest pynq_overlay/sw/tests/test_rf_touch_model.py pynq_overlay/sw/tests/test_rf_touch_view.py pynq_overlay/sw/tests/test_rf_bpsk_ui_model.py -q --basetemp=evidence/rf-teaching/s1/green-profile-regression`。不得为新入口删旧STOP/回环确认回归。

### Task 2：从已冻结同步估计提取真实星座

Files：Create `pynq_overlay/sw/rf_rx_constellation.py`、`pynq_overlay/sw/tests/test_rf_rx_constellation.py`。不修改冻结`rf_bpsk_receiver.py`。

- [x] 写红例：同一真实IQ与同BpskEnvelope才能产生点；capture/release/generation/配置/完成时间错配拒绝；不支持/载波失步/定时失步返回无点；无参考但载波定时同步仍可显示点且不生成BER。
- [x] Run `python -m pytest pynq_overlay/sw/tests/test_rf_rx_constellation.py -q --basetemp=evidence/rf-teaching/s1/red-constellation`；保存FAIL。
- [x] 创建冻结结果`RxConstellation`，包含完整身份、提交修订、算法版本、状态/原因、归一化规则、总符号数及最多128对浮点点。返回tuple，拒绝非有限参数；禁止填理想±1点代替测量。
- [x] 复用当前BPSK估计，数值流程固定如下（核心计算片段，外围先做上述类型/身份/域验证）：

```python
# frame: RxIqFrame; envelope: BpskEnvelope; r = envelope.result
iq = np.frombuffer(frame.payload, dtype='<i2').reshape(-1, 2)
z = iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)
z -= np.mean(z)
n = np.arange(len(z), dtype=float)
z *= np.exp(-2j*np.pi*r.cfo_hz*n/frame.sample_rate_hz)
z *= np.exp(-1j*r.phase_mod_pi)
start, count, sps = r.timing_offset_samples, r.raw_symbols, 20
symbols = z[start:start+count*sps].reshape(count, sps).mean(axis=1)[1:-1]
assert len(symbols) == r.total_symbols
scale = float(np.sqrt(np.mean(np.abs(symbols)**2)))
if not np.isfinite(scale) or scale <= 0:
    # Return a bound INVALID result with no points; never divide by zero.
    raise ValueError('invalid symbol RMS')
points = tuple((float(v.real/scale), float(v.imag/scale)) for v in symbols[:128])
```

外围将上述异常转成当前帧明确失败结果，不能沿用旧点。归一化仅服务教学绘图，不是EVM/射频功率校准，不用显示点数量当BER分母。

- [x] 用独立合成积分参考与已保存M4 IQ验证真实点；注入噪声时有散布，I/Q变换、相位和定时场景不产生固定理想图。保持不同极性均可作为无参考BPSK星座，不强制推断发送比特。
- [x] Run `python -m pytest pynq_overlay/sw/tests/test_rf_rx_constellation.py pynq_overlay/sw/tests/test_rf_bpsk_receiver.py pynq_overlay/sw/tests/test_rf_bpsk_pipeline.py -q --basetemp=evidence/rf-teaching/s1/green-constellation`；确认原BER全部不变，并核对冻结算法SHA。

### Task 3：有界本地实验记录

Files：Create `pynq_overlay/sw/rf_experiment_record.py`、`pynq_overlay/sw/tests/test_rf_experiment_record.py`。

- [x] 写红例：IQ SHA与原frame.payload一致；metadata保留同帧身份/RF配置/显式RX参考/算法及模型版本/分析/星座/BER；禁止同名覆盖、路径逃逸与非有限JSON；磁盘失败返回明确错误且不影响停止。
- [x] Run `python -m pytest pynq_overlay/sw/tests/test_rf_experiment_record.py -q --basetemp=evidence/rf-teaching/s1/red-record`。
- [x] 最小实现显式本地保存：每条记录≤4096复数样点，metadata有长度上限；唯一会话目录和record ID，IQ与JSON先写暂存再提交，失败不冒充完成。无token/密码；无网络上传；当前过期记录允许另存历史并明确标注，禁止恢复成实时状态。
- [x] 写线程/阻塞负控：绘制线程只提交保存请求，后台接收冻结payload并写盘；停止不等待正在写盘才触发。记录尚无控制结果则写未执行/未知，不能推断已停止或已发射。
- [x] 重跑GREEN，读取保存记录后使用同一算法/配置回放，核对分子分母与IQ SHA；混改IQ/metadata应失败。

## Chunk 2：同帧交接与实体屏

### Task 4：worker/model/view集成

Files：Modify `pynq_overlay/sw/rf_status_worker.py`、`ui_model.py`、`ui_view.py`、`pynq_status.py`；Create `pynq_overlay/sw/tests/test_rf_teaching_pipeline.py`。

- [x] 写红例：后台输出frame/analysis/model/demod/constellation同一身份；计算慢、取消、STOP、异常、旧修订和断连时不得落旧点/旧BER；导出保存当前被冻结帧而非后来替换的帧。
- [x] Run `python -m pytest pynq_overlay/sw/tests/test_rf_teaching_pipeline.py -q --basetemp=evidence/rf-teaching/s1/red-pipeline`。
- [x] 在既有后台分析之后添加有界纯星座调用和保存请求；不增加daemon wire字段。新视图沿用接收页，新增星座切换按钮；保持概览回环确认/取消及底栏STOP位置可达。
- [x] UiApp对星座采用现有同帧/代次/30秒规则；新增保存状态仅表示保存结果，不续期、不自动捕获。失步显示原因和空点，历史显示历史标签。
- [x] 理想发射星座明确标“理想发射示意”；真实接收图标“归一化同步符号/当前快照”，同时保留同步和BER分子分母。不显示EVM/CRC已完成或“持续锁定”。
- [x] 重跑GREEN；Run `python -m pytest pynq_overlay/sw/tests/test_rf_teaching_pipeline.py pynq_overlay/sw/tests/test_rf_bpsk_worker.py pynq_overlay/sw/tests/test_rf_bpsk_ui_view.py pynq_overlay/sw/tests/test_rf_rx_view.py pynq_overlay/sw/tests/test_rf_control_background.py -q --basetemp=evidence/rf-teaching/s1/green-pipeline-regression`。800×480预览目视检查按钮可读且STOP不被遮挡。

## Chunk 3：候选发布与独立验收

### Task 5：完整软件合同与回退

Files：Modify `pynq_overlay/sw/merged_release.py`、`install.sh`、`rf_status_worker.py`；Create `pynq_overlay/sw/tests/test_rf_teaching_release.py`；Modify `rf_overlay/sw/board_app/test/test_rf_daemon_main.c`新manifest样例；Modify `scripts/rf_repackage.py`仅在其入口确需新合同参数时。

- [x] 写红例：新教学合同必须包含profile/constellation/record三模块及完整既有依赖；缺文件/额外未声明文件/错误hash拒绝；旧cnn-bpsk-v1/旧e7校验继续通过。
- [x] Run `python -m pytest pynq_overlay/sw/tests/test_rf_teaching_release.py -q --basetemp=evidence/rf-teaching/s1/red-release`。
- [x] 新增`cnn-bpsk-teaching-v1`合同，不篡改旧已安装manifest。同步扩展worker的CNN/BPSK合同加载白名单、merged_release的模型能力/canonical资格、installer分支与完整软件源列表；新合同必须实际加载生产CNN与BPSK引擎。新模型不是本阶段需求，沿用生产模型与原bit/HWH/daemon。
- [x] 在隔离候选目录验证新合同panel导入、模型和解调available；旧cnn/cnn-bpsk目录缺新增模块仍可隔离加载原能力；额外模块未声明仍拒绝，不能靠主工作目录sys.path漏入新依赖。
- [x] C启动测试加入新manifest样例。Run `make -C rf_overlay/sw/board_app host-test-daemon`；预期所有既有与新manifest断言通过，不为新增Python模块默认改C生产控制逻辑。
- [x] Run `python -m pytest pynq_overlay/sw/tests/test_rf_teaching_release.py pynq_overlay/sw/tests/test_panel_release.py pynq_overlay/sw/tests/test_rf_bpsk_release.py pynq_overlay/sw/tests/test_rf_cnn_runtime.py -q --basetemp=evidence/rf-teaching/s1/green-release`，保存候选hash和回退证据。提交仅本阶段明确改动；大量既有dirty文件保持。

### Task 6：新实板/回放/真人门禁

Files：Create `scripts/rf_teaching_verify.py`；Extend `scripts/rf_bpsk_ui_board_session.py`/`rf_bpsk_ui_board_run.py`时必须保留旧M4无新模块兼容；证据`evidence/rf-teaching/s1/`。

- [x] 离线审阅新模块、发布合同与完整链路回归；S0关闭条件确认后安装。执行前确认未布防和当前发布；守护进程持PL时未创建第二Overlay。
- [x] 新有限声明冻结：同M4 RF参数，种子1/37/73，三组各10ON+1OFF。保存原始IQ/metadata/实际UiView按钮事件/星座/显示/关闭回执。
- [x] 门禁：30/30同步、0/4140、3OFF无BER/无同步星座，三次关闭safe及解除ACK；停止条件未触发。
- [x] 33帧主机/ARM原BPSK逐字段一致；真实星座容差1e-6，33个不同IQ SHA。新结果与源码/发布hash绑定。
- [x] 100次完整后台热计算（热身5）P95=187.18ms，逐次时长、RSS22608KiB和CNN/教学启用均记录；≤300ms。
- [x] 真人三正常参考帧、星座/计数、参考关闭新帧有真实星座无BER、域外拒绝及恢复、保存当前/31秒/历史、取消回环/停止/解除/OFF检查：11张照片＋用户直接文字确认。此前ARM4条记录文件读取/回放通过；本轮真人保存对应新文件未另核对，短时采集中取消未单独报告。
- [x] 用户确认操作体验不卡顿，明确不需专门计时；精确时延未测且非阻塞，不用预计通过或纯计算数值冒充测量。
- [x] 保存S1闭合收据与阶段报告、更新roadmap；按用户最新修订的门禁进入S2独立设计/计划，不同时开发QPSK或S3策略。

2026-10-04最新：实板机器总收据/阶段报告已保存（live-receipt.json、2026-10-04-s1-board-validation.md），4条新采IQ历史记录ARM写读/主机重放通过。11张实体照片＋用户文字确认闭合真人功能，独立触摸时延未测，最后阶段条目仍待计时后闭合。本轮真人保存对应新文件未另取回核对。旧Task 6离线记录描述当时状态，不代表现在未安装。

Task 6离线子项已完成：新工具RED→GREEN、33条旧M4 IQ历史记录再读、候选隔离检查、整体软件/C回归与独立Approved。上面的复合实板条目保持未勾选。下次实板命令使用scripts/rf_bpsk_ui_board_run.py --teaching；基准输入必须实际同步且有201符号/128点，短路路径拒绝，不删失败迭代。阶段总收据与报告已保存，不等于S1验收完成。

2026-10-04接续完成Task 6机器验收闭合：新增rf_teaching_acceptance.py，driver教学模式须提供--candidate，取回后自动同输入数学重放并汇总BER/关闭/100次P95及发布/源码/IQ绑定；传输异常留失败收据。91项定向回归＋独立复审APPROVED。只读预检板端仍993ffe21/status_only，候选未安装。详情docs/reports/2026-10-04-s1-acceptance-closure.md；S0真人未确认、新实板/ARM/实体屏门禁仍保持未勾选。
