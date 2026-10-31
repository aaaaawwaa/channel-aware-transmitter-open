# M4 BPSK板屏集成 Implementation Plan

> **For agentic workers:** REQUIRED: 使用executing-plans按下列批次实施；有适用技能明确授权的审阅任务可用子代理。保持现有dirty tree，不创建丢失本地上下文的worktree，不混合提交既有改动。每批保存相关文件基线和证据。

**Goal:** 将已经验收的BPSK3MHz RECT有限接收器接入同一worker和生产板屏，完成当前结果生命周期、软件发布和真人功能验收。

**Architecture:** UiApp冻结显式RX配置和独立解调代次，经既有submit_capture交给后台；新增纯计算pipeline封装算法结果，同frame/analysis/CNN原子交接。UiApp验证身份、修订号和有效期，UiView只读显示；新软件合同包含全部依赖并保留旧合同。

**Tech Stack:** Python3.8/numpy1.17.4、项目800×480绘制与GT911、pytest、现有C daemon host tests、不可变merged release、规定Paramiko helper。设计：`../specs/2026-10-03-bpsk-ui-integration-design.md`。

**当前状态（最新复核）：** A/B/C已经实现并安装为993ffe21199a311eabec143a50e7e8a2，源码与发布匹配；D的正式有限复验与真人功能验收分别记录。下列原始任务清单保留规划历史，不用旧未勾选状态推断当前进度。最新执行状态以`.superpowers/sdd/2026-10-03-bpsk-ui-integration/progress.md`和`docs/reports/2026-10-03-bpsk-ui-progress-audit.md`为准。用户已明确真人项尚未完成。

## Chunk A：接收配置、后台与身份

### Task 1：保存基线，建立配置/结果封套

Files：Create `pynq_overlay/sw/rf_bpsk_pipeline.py`、`pynq_overlay/sw/tests/test_rf_bpsk_pipeline.py`；Modify `pynq_overlay/sw/ui_model.py`；证据 `evidence/rx-bpsk/m4/`。

- [ ] 核对M3收据和当前相关源码hash；保存worker/model/view/status/merged_release/install基线，仅核对可靠发布身份，暂不控制硬件。
- [ ] 写pipeline红例：从显式RX配置创建BpskConfig，参考关闭对应None，bool种子/0/128拒绝；冻结封套不可变且保留result、配置、解调修订号和算法身份。
- [ ] Run `python -m pytest pynq_overlay/sw/tests/test_rf_bpsk_pipeline.py -q --basetemp=evidence/rx-bpsk/m4/tmp-pipeline-red`，确认因新模块/行为缺席FAIL并保存输出。
- [ ] 实现纯pipeline与UI model RX setter；`rx_demod_revision`独立于硬件修订号。所有设置及CNN草稿建议路径经setter，改参考后旧解调不能保留。
- [ ] 加不支持模式/成形/速率但允许基础IQ的例子，默认RX为BPSK3MHz RECT且参考关闭，无控制调用。
- [ ] 重跑同一测试GREEN，运行M3算法测试；保存hash与本批收据，审阅配置与封套边界。

接口示例（新API名称在实现时保持一致）：

```python
frozen = freeze_demod_config(app.rx_draft, app.rx_demod_revision)
# snapshot是冻结本地对象，绝不是传给daemon的wire参数。
assert frozen.config.reference_prbs7_seed is None  # 默认参考关闭
assert frozen.revision == app.rx_demod_revision
```

### Task 2：接入后台计算与原子交接

Files：Modify `pynq_overlay/sw/rf_status_worker.py`、`pynq_overlay/sw/pynq_status.py`；Create `pynq_overlay/sw/tests/test_rf_bpsk_worker.py`；Regression `test_rf_rx_pipeline.py`、`test_rf_smart_pipeline.py`、`test_rf_control_background.py`。

- [ ] 写红例：UI提交立即返回，捕获wire没有解调字段；同frame产生analysis/CNN/BPSK；旧submit_capture不带新参数仍可用。
- [ ] 写可控慢算法的取消/STOP、计算异常、连接/硬件配置改变和新UI解调代次用例；用事件同步，不用脆弱的固定sleep猜线程进度。
- [ ] Run `python -m pytest pynq_overlay/sw/tests/test_rf_bpsk_worker.py -q --basetemp=evidence/rx-bpsk/m4/tmp-worker-red`，保存有效红例。
- [ ] 扩展submit_capture可选关键字、冻结请求，后台调用pipeline；交接payload新增`demod_result`，旧键保持；取消/连接校验继续在锁内发布前执行。
- [ ] 单独BPSK异常仅返回本次解调无BER状态，不吞掉完整成功IQ/分析/CNN；完整采集失败按原关闭路径处理。
- [ ] `_rx_action`只冻结/提交，`_PanelScreen`继续领取结果；绘制回调不调用receive_bpsk、不等待socket。
- [ ] Run `python -m pytest pynq_overlay/sw/tests/test_rf_bpsk_worker.py pynq_overlay/sw/tests/test_rf_rx_pipeline.py pynq_overlay/sw/tests/test_rf_smart_pipeline.py pynq_overlay/sw/tests/test_rf_control_background.py -q --basetemp=evidence/rx-bpsk/m4/tmp-worker-green`；审阅取消与代次，保存A批收据。

## Chunk B：生命周期与板屏

### Task 3：身份校验、能力与统一失效

Files：Modify `pynq_overlay/sw/ui_model.py`；Create `pynq_overlay/sw/tests/test_rf_bpsk_ui_model.py`；Regression `test_rf_smart_pipeline.py`、`test_rf_rx_pipeline.py`。

- [ ] 写红例：BpskResult错capture/release/client配置/连接/完成时刻、封套错RX配置或解调代次；所有例子无当前BER，基础分析仍按其自己的规则落地。
- [ ] 写边界测试：完成时刻+30秒仍按既有规则、+31秒失效；新捕获/失败/无参考/域外/STOP/清除/断连/设备切换均不沿用旧BER。
- [ ] Run `python -m pytest pynq_overlay/sw/tests/test_rf_bpsk_ui_model.py -q --basetemp=evidence/rx-bpsk/m4/tmp-model-red`，确认目标行为失败。
- [ ] 实现严格封套身份检查、解调摘要和统一失效；只比较同一修订号空间，不要求UI编号与client编号相等。
- [ ] 能力来自实际已验证软件合同/引擎；将“实现可用”“当前支持域”“本帧同步”“本帧BER可用”分开。未参考不是0错误，未同步不是0BER。
- [ ] 重跑GREEN及相关生命周期测试；保存本批完整拒绝矩阵。

示例断言：

```python
app.set_rx_demod_setting('reference_enabled', False)
assert app.rx_demod_result is None
# 后续同域新捕获仍可同步，但ber/bit_errors/compared_bits都必须为空。
```

### Task 4：800×480解调页与真实触摸事件路径

Files：Modify `pynq_overlay/sw/ui_view.py`、按需 `pynq_overlay/sw/panel_preview.py`；Create `pynq_overlay/sw/tests/test_rf_bpsk_ui_view.py`；Regression `test_rf_rx_view.py`、`test_rf_touch_view.py`、`test_touch_latency.py`。

- [ ] 写RED：参考开关/种子、模式/符号率/成形设置通过实际render→ui.step→hit→press/release；隐形/禁用按钮不触发；无参考/失败/过期不画旧BER。
- [ ] Run `python -m pytest pynq_overlay/sw/tests/test_rf_bpsk_ui_view.py -q --basetemp=evidence/rx-bpsk/m4/tmp-view-red`。
- [ ] 左卡片显示显式设置，右卡片显示短capture身份、载波/定时/参考、errors/bits和BER；保留概览捕获入口提示；CRC/帧/EVM保持未接入。
- [ ] 对无参考/UNSUPPORTED/OFF/CLIPPED/当前成功/过期/关闭未确认分别生成800×480预览；检查CJK/ASCII字形、字号、溢出和按钮命中，长原因截断可读。
- [ ] GREEN及上述原UI回归通过；保存PNG和显示口径收据，审阅B批。

## Chunk C：可安装的软件发布

### Task 5：新合同、依赖、模型兼容和安装回退

Files：Modify `pynq_overlay/sw/merged_release.py`、`pynq_overlay/sw/install.sh`、`pynq_overlay/sw/rf_status_worker.py`；按测试需要 Modify `scripts/rf_repackage.py`、`scripts/package_merged_overlay.py`；Test `pynq_overlay/sw/tests/test_rf_model_release.py`、`pynq_overlay/sw/tests/test_panel_release.py`、`pynq_overlay/sw/tests/test_merged_install_contract.py`；Create `pynq_overlay/sw/tests/test_rf_bpsk_release.py`；Modify C兼容测试 `rf_overlay/sw/board_app/test/test_rf_daemon_main.c`，按需 `rf_overlay/sw/board_app/test/test_rf_daemon.c`。

- [ ] 写RED：新`cnn-bpsk-v1`完整集合、缺算法/缺pipeline/篡改hash/额外未声明模块拒绝；模型production资格和worker加载接受新CNN合同。
- [ ] 写旧合同隔离导入和回退RED：legacy/rx/cnn不要求新模块，新UI在缺引擎时基础RX可用；旧e7f1fc69仍可验证。
- [ ] Run `python -m pytest pynq_overlay/sw/tests/test_rf_bpsk_release.py -q --basetemp=evidence/rx-bpsk/m4/tmp-release-red`。
- [ ] 实现canonical合同与全套新源码身份，将worker/model/installer中cnn-v1单值分支按明确支持集合扩展；不改旧合同的文件集合。
- [ ] 加新manifest样例到现有C daemon兼容测试，Run `make -C rf_overlay/sw/board_app host-test-daemon`（含startup/manifest测试）。在已有可用GCC/make环境执行；本机工具链缺失时经规定helper使用板端独立临时源码目录，不在已安装release中编译、也不装新daemon。若原daemon兼容则复用，不改协议/RTL；出现真实不兼容先记证据和影响范围再重建。
- [ ] Run `python -m pytest pynq_overlay/sw/tests/test_rf_bpsk_release.py pynq_overlay/sw/tests/test_rf_model_release.py pynq_overlay/sw/tests/test_panel_release.py pynq_overlay/sw/tests/test_merged_install_contract.py -q --basetemp=evidence/rx-bpsk/m4/tmp-release-green`。
- [ ] 准备候选不可变包，生产CNN权重/评估与原bit/HWH保持hash；完整隔离import/安装预检、LF检查、旧发布回退收据齐全后才进入上板。
- [ ] 独立审阅C批；保存候选release_id、所有源码/产物hash及完整测试实际命令，不把本地候选写成已部署。

## Chunk D：板端与真人验收

### Task 6：资源验证与真实UI链路有限实采

Files：Create `scripts/rf_bpsk_ui_acceptance.py`、`pynq_overlay/sw/tests/test_rf_bpsk_acceptance.py`；Evidence `evidence/rx-bpsk/m4/`；Modify实际进度报告与ledger。

- [ ] 先为验收脚本写离线拒接管、发布失配、关闭回执丢失、超时/削顶停止及失败样本保留测试，RED→GREEN；有限3会话×(10ON+1OFF)，不无限重试。
- [ ] 冻结新pilot声明及预期release；用规定Paramiko helper部署已审候选，按既有installer仅由唯一daemon持有PL，先只读核对发布和引擎/模型状态。不得第二次Overlay。
- [ ] 使用已保存IQ跑100次热基础分析+CNN+BPSK，记录P50/P95/max/RSS及版本，P95目标≤300ms；保存同输入算法与主机对照。
- [ ] 原生产UiView真实事件路径→后台→client→daemon执行新有限pilot，RX显式配置/参考独立设置；30ON≥27成功且成功帧聚合BER≤1%，3OFF无BER，保留全部失败和metadata。
- [ ] 停止/取消/参考关闭/域外/过期的UI负控，生成实际候选版本页面截图；软件事件与实体GT911结果分开。
- [ ] 每会话结束核验disarm回执/source=false/RX ARM=false；超时、削顶、身份改变或关闭未确认停止后续采集。每帧raw/hash/封套/显示值一致；服务状态和current身份进入收据。
- [ ] 任一发布启动门禁失败走已有回退程序并确认安全，保留失败；没有候选可靠起点不得继续发射。

### Task 7：真人功能确认、时延口径与阶段交付

Files：Create `docs/reports/<actual-date>-bpsk-ui-human-checklist.md`、`docs/reports/<actual-date>-bpsk-ui-acceptance.md`；Update `CLAUDE.md`、总体路线阶段状态、相应ledger。

- [ ] 从已安装实际版本生成真人清单，步骤来自设计§8并核对实际按钮名称/页面；明确仅加载、不先持续start。
- [ ] 用户现场三次新帧、显式参考关闭、域外设置、修改/过期/取消、停止/解除及OFF负控逐项确认，记录真人来源，不要求重复上传已确认材料。
- [ ] 真人时延若量化，保存至少20次触摸释放→首次新结果显示的测量方法和时长；没有量化就明确NOT MEASURED，不拿纯算法/MemoryLcd时延填入。
- [ ] 最终完整相关回归/独立审阅；核对已安装文件hash和上板实际结果，列出所有未完成项。
- [ ] 分别写M4“功能与资源”“真人功能”“真人端到端性能”状态；仅已通过项记PASS，未量化时延保持待测，不宣称M4性能全部完成。
- [ ] 发布阶段总结/收据并更新位置图：M4功能完成后才开始M5a；真人时延未完成项继续显式跟踪。M5a进入前先冻结新的速率域/判据，不拿M3或M4单工况代替扩展验收。

## 执行范围控制

本计划不包含RRC/QPSK/QAM/天线/LNA/CRC/连续接收实现，也不隐式重新训练CNN。主树已有修改保持；本轮计划不启动板卡操作、不新增运行服务、不自行commit。每批完成都有可复核证据，关键门禁不通过时停止扩展并先定位原因。
