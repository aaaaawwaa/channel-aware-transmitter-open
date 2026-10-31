# RF RX 与小型CNN Implementation Plan

> 最新真人收尾（2026-10-03）：用户对建议仅改草稿、超过31秒过期且建议禁用、停止并解除布防明确确认通过；结合实体屏TONE照片，单次真人生产回环交互闭环完成。收据human-loopback-fix-2026-10-03/human-final-confirmation.json。未访问板卡或改发布；真人P95、完整OFF/三ON/OFF及静默watchdog定时仍未单独确认，天线/LNA/示波器未运行。下方待确认记录为历史状态。

> 最新真人照片复测（2026-10-03）：实体屏TONE/source=loopback.physical.capture/生产模型仅填草稿/AUTO关闭，单次真人确认回环→实采→识别→实体屏显示通过。第1、2项用户确认正常；完整真人OFF/三ON/OFF、实际填草稿、过期、停止/P95仍待确认。证据`evidence/rx-smart/human-loopback-fix-2026-10-03/human-success.jpg`及human-success-receipt.json。本轮未操作板卡、未改发布；下方待复测描述为历史状态。

> 最新真人反馈（2026-10-03）：用户确认第1、2项正常，无需视频；真人P95未量化。录像回环尝试发生在TX_ACTIVE，按READY门禁不可用，后自动解除OFFLINE；原e7f1fc69完整准备UI回调→READY/已加载且TX关闭→确认/取消→OFF/三ON/OFF补验通过，75项回归通过、安全读回通过，无生产改动。清单补明确起始状态及停止后重加载；真人回环待复测。诊断`docs/reports/2026-10-03-human-loopback-diagnosis.md`。下方为历史记录。

> 最新生产实板阶段（2026-10-03）：当前用户已确认TX1→RX1直连。发布e7f1fc6962179eaeb57be15dcad07790的真实OFF/三ON/OFF生产CNN链路及30秒静默watchdog通过；ON全部TONE、OFF全部UNKNOWN、削顶0；30.164秒自动停机，超过5秒无重启，最终safe=true。真人触摸/屏幕端到端P95及现场表现仍待操作，具体清单见`docs/reports/2026-10-03-production-human-test-checklist.md`；验收报告见`docs/reports/2026-10-03-production-live-acceptance.md`。下方为历史接续记录。

> 历史上板审核（2026-10-03）：rot200生产模型/runtime的ARM数值及资源门槛通过，发布e7f1fc6962179eaeb57be15dcad07790已安装。原生产包CRLF安装脚本/旧daemon问题已修正，最终safe=true；新物理回环等待本轮接线确认，真人触摸/P95/watchdog及天线/LNA仍NOT RUN。详见`docs/reports/2026-10-03-production-board-continuation-audit.md`。

> 历史接续（2026-10-03，额度剩余8%）：Task9候选逐层数值验证通过，但ARM P95超200ms，未部署、不能记整体COMPLETE；Task8正式准确率FAIL，质量上限/方向问题已定位，新D4训练在报告门限取消、未完成。板端仍46fe…且safe=true。最终判定见 `docs/reports/2026-10-03-rx-task8-10-followup-audit-acceptance.md` 与ledger顶部，下一段为此前阶段记录。

> 执行状态更新（2026-10-03）：Task1–7 已有实现/采集证据；Task8 审核修复完成但准确率未验收，Task9 资源检查通过但完整逐层数值门槛未通过。Task10 实验链路/界面已实现、审核、回归；嵌套清单解析故障修复后重新编译ARM，激活发布 `46fe2c080deb9d0c42eb0a612a86d091`。码100直连OFF/三ON/OFF真实IQ推理及离线重放通过，仍有模型误判，生产建议/AUTO关闭；真人触摸/P95/watchdog及示波器 NOT RUN。具体状态由 `.superpowers/sdd/2026-10-01-rx-smart-implementation/progress.md` 维护，最新证据见 `docs/reports/2026-10-03-task8-9-review-task10-continuation.md`。下方“待执行/确认尚未收到”是编写时的历史上下文；不根据未同步复选框推断实施状态。

> **For agentic workers:** REQUIRED: Use $subagent-driven-development (if subagents available) or $executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有真实RX快照接入板载接收/智能页面，完成小型CNN训练及独立有线回环评估，再单独评估天线和可选LNA。

**Architecture:** 唯一pynq-status持有Overlay，RF worker/client调用同发布daemon；IQ采集、分析与CNN推理在后台执行。主机PyTorch训练，板端numpy运行1662参数CNN；UI只接收冻结结果，模型建议只进入草稿。

**Tech Stack:** Python3.8/PYNQ/PIL/numpy、主机隔离Python3.12/PyTorch CPU、C99 RF daemon、既有Vivado2025.2产物、Paramiko板端工具。

---

日期：2026-10-01。状态：待执行文档；下列未勾选项均未实施。依据：`docs/superpowers/specs/2026-10-01-rx-smart-design.md`。本轮用户命令为编写design/plan/进度，不改生产代码、训练环境或板端服务。已有CNN主体规格独立审查Approved，实施确认尚未收到；天线部分为本轮文档补充。

## 工程约束和起点

- 主树：`<LOCAL_PATH>`；保留全部既有修改，开始实施先记录目标文件diff/哈希，不整树复制或reset。
- 最近验证发布`56d0d0d4bd36d4e22d1c030926917a16`。同一bit/HWH可复用，不把本计划的软件构建称Vivado综合。
- codebase-memory图工具目前不可调用，定点读取已确认路径；后续工具恢复后仍优先图工具。
- 连接使用`<LOCAL_PATH>
- 构建/打包/服务部署串行；硬件测试也串行。独立纯分析/模型模块可按文件所有权分工，不能由多个任务同时操作板卡。
- 如执行阶段需要提交，仅提交审阅过的本任务增量；不得把当前大量既有未提交改动一并提交。

## 文件职责

| 文件 | 责任 |
|---|---|
| `rf_overlay/sw/board_app/rf_core.c/.h`、`rf_daemon.c` | 有限采集完成清ARM、确认读回，失败进入FAULT |
| `pynq_overlay/sw/rf_status_client.py` | 二进制IQ、持久RX会话、配置/连接代次绑定 |
| `pynq_overlay/sw/rf_status_worker.py` | 采集/控制仲裁、STOP、后台结果发布 |
| 新`pynq_overlay/sw/rf_rx_types.py` | 不可变IQ帧/分析/模型结果，避免把数组放进遥测 |
| 新`pynq_overlay/sw/rf_rx_analysis.py` | 纯IQ质量、时域/频谱和dBFS |
| 新`pynq_overlay/sw/rf_cnn_runtime.py` | JSON模型校验、预处理与numpy推理 |
| `ui_model.py`、`ui_view.py`、`pynq_status.py` | 现有页面与后台链路接入，生命周期/能力门禁 |
| 新`scripts/rf_ml/{dataset,collect,synthetic,train,evaluate,export,benchmark}.py` | 分组数据、真实采集、合成、训练、评估、导出、板端基准 |
| 新`scripts/rf_ml/__init__.py` | 模块CLI入口支持 |
| `merged_release.py`、`install.sh`、`scripts/package_merged_overlay.py`、`scripts/rf_repackage.py` | 版本化software map和声明式可选模型artifact |
| 新`host/tests/test_rf_ml_*.py`及`pynq_overlay/sw/tests/test_rf_rx_*.py` | 有意义的协议、数学、泄漏和生命周期负控 |

## Chunk 1: 真实RX采集与分析

### Task 1: 保存基线并补daemon有限采集收尾

**Modify:** `rf_overlay/sw/board_app/rf_core.c/.h`、`rf_daemon.c`；**Test:** `rf_overlay/sw/board_app/test/test_rf_core.c`、`test_rf_daemon.c`、`test/fake_pl.c/.h`。

- [ ] 保存目标源码hash、已有diff、最近verified release及原测试收据到新`evidence/rx-smart/baseline/`，不覆盖旧失败证据。
- [ ] 通过真实daemon请求路径写普通、数字、物理单次capture回归：成功读出后CAP_ARM=0；物理SRC_SEL=0；清理读回失败不发送成功IQ帧。现有DONE后ARM保持高应使新测试失败。
- [ ] 用MinGW运行`<LOCAL_PATH> -C rf_overlay/sw/board_app HOSTCC=<LOCAL_PATH> host-test-daemon host-test-core`，记录红例及退出码；若当前shell PATH缺make内部工具，局部给本次进程加入MinGW bin，不改系统PATH。
- [ ] 增加核心有限采集收尾接口：先冻结BRAM样点，清ARM并验证真实寄存器。保持连续采集语义，正常清DONE不使冻结数据无效；FAULT用既有安全关机，不在FAULT调用会拒绝写入的常规setter。
- [ ] 上述命令应全PASS，旧40dB/接线无写入负控、断连和超时清理仍通过。建立仅本任务文件的review checkpoint。

### Task 2: 二进制IQ客户端与RX会话

**Modify:** `pynq_overlay/sw/rf_status_client.py`；**Create:** `pynq_overlay/sw/rf_rx_types.py`；**Test:** `pynq_overlay/sw/tests/test_rf_rx_client.py`和现有`test_rf_status_client.py`/`test_rf_tx_transport.py`。

- [ ] 写分块header/payload、多个回复粘连、错误ID/format/bytes/ratio、超4096、非法signed12、截断和3秒deadline负控；旧JSON-only client应失败。
- [ ] 运行`python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_rf_rx_client.py -q`保存红例。
- [ ] 提供`capture(args, physical=False)`，普通只发`rx.capture`；physical必须显式接线/40000mdB且已加载。区分IQ回复和JSON error，保留多余buffer，只有完整校验帧才能发布不可变bytes。
- [ ] 显式绑定本地连接代次、配置revision、release、采集前后读回和本地单调完成时刻；不要要求当前wire不存在的字段。ratio选择码转换用`decim=1 << ratio`、`output_fs_hz=input_fs_hz//decim`。IQ是RX1的I/Q两分量，不是两个RF接收通道。
- [ ] RX持久连接不复用`tx_session_ready`含义，正常一次收尾保留已配置控制会话；意外断连丢弃当前结果/加载证明，禁止自动arm或retry发射。
- [ ] 运行上述新测试及`test_rf_status_client.py test_rf_tx_transport.py`全部通过，记录有效帧与关闭负控；审阅局部diff。

### Task 3: 纯分析和数据有效期

**Create:** `pynq_overlay/sw/rf_rx_analysis.py`；**Test:** `pynq_overlay/sw/tests/test_rf_rx_analysis.py`。

- [ ] 写独立解析单音、双音、已知幅度、零能量、NaN输入、削顶端点、抽取率、无效长度的数学测试，预期新增模块不存在而失败。
- [ ] 实现`summarize(frame)`只输出有界显示数据：样点预览、I/Q RMS、分量dBFS、复数RMS、窗归一幅度谱、主峰及99%占用带宽估计，分别标注DC处理口径。
- [ ] 使用口径`20*log10(sqrt(mean(component**2))/2048)`；零能量返回无能量状态。FFT除以窗系数和；不将谱峰突出度冒充SNR或由码值声称SMA Vpp。
- [ ] 削顶定义I或Q达到-2048/2047；不将输出未削顶等同于RF前端未压缩。完成30秒IQ新鲜度与独立2秒遥测过期负控。
- [ ] 运行`python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_rf_rx_analysis.py -q`，预期全PASS；核对已有真实IQ分析与原验收结论一致但不替换其固定判据。

### Task 4: 后台仲裁与接收UI接入

**Modify:** `rf_status_worker.py`、`pynq_status.py`、`ui_model.py`、`ui_view.py`；**Test:** 新`pynq_overlay/sw/tests/test_rf_rx_pipeline.py`、`test_rf_rx_view.py`，及既有RF worker/模型/触摸测试。

- [ ] 写真实UiView命中/按下/抬起→生产回调→worker→client fake transport测试；状态未READY、能力缺失、忙、旧会话、取消和改配置均不得采集/发射。
- [ ] 运行新测试记录红例；实现后台提交采集/分析和不可变结果交接，main loop只落地快照与绘图，不执行socket/FFT。
- [ ] `capture`只接收、`clear`只清本地快照、模型“填草稿”只编辑草稿；回环是显式确认的独立动作，不把普通捕获按钮变成发射按钮。
- [ ] STOP标排队/取消中，3秒采集deadline后优先关闭与读回；取消、FAULT/意外断连结果不落地。关闭未确认不显示已停。正常收尾保留当前冻结样点，历史视图与当前状态分开。
- [ ] 动态解锁capture能力，保留未接入的同步/CRC/BER/EVM；先显示频谱/IQ/质量而不显示假帧载荷。保持ACM2108原命令和双外设独立草稿。
- [ ] 运行`python -m pytest -p no:cacheprovider pynq_overlay/sw/tests -q`，预期全部通过；生成并逐张检查概览/采样/智能的有效、过载、过期、取消截图；实际真人触摸另验。

### Task 5: 无模型RX候选发布与实板验收

**Modify:** `pynq_overlay/sw/merged_release.py`、`install.sh`、`scripts/package_merged_overlay.py`、`scripts/rf_repackage.py`；**Test:** 现有release/install测试及新`test_rf_model_release.py`。

- [ ] 本阶段新analysis/types纳入RX-only完整software契约，CNN runtime在Task9实现并通过测试后才在Task10的CNN软件版本成为必需模块；Task5不依赖尚未实现的runtime。为旧已验证parent采用明确版本化清单，不能因全局SOFTWARE_SOURCES增加而使旧parent无条件不可验证；旧/RX-only/CNN map各自完整必验，不放宽为“有多少验多少”。
- [ ] 模型作为声明式可选artifact；无模型release可提供RX但model=false。声明存在则文件hash/schema/shape和评估清单严格验证，并让模型/评估身份参与release_id；失配拒装。
- [ ] 写旧parent兼容、新软件漏包、权重改字节、声明有模型但文件缺失、无模型导入闭包负控，红绿验证后运行全release/install测试。
- [ ] 在授权执行阶段运行`python scripts/rf_build_daemon.py --out evidence/rx-smart/build/rf_daemon`；要求输入hash逐项匹配、BUILD_RC=0、全部C/vendor测试通过，保存收据。
- [ ] 运行`python scripts/rf_repackage.py --parent evidence/2026-10-01-rf-build-hmi/package-safe/releases/56d0d0d4bd36d4e22d1c030926917a16 --daemon evidence/rx-smart/build/rf_daemon --output evidence/rx-smart/package-rx`；要求完整software与隔离panel导入通过、bit/HWH hash保持。
- [ ] 以既有不可变安装流程经Paramiko部署，核验新release与两个服务、实际framebuffer；不手工覆盖current目录或启动另一Overlay。保留明确rollback发布。
- [ ] 普通捕获验证不发射，明确回环才加40dB/接线门控；复验现有命令时`--expected-release`取刚验证的真实release，不使用旧常量。保存UI→worker→真实IQ、source/ARM均清零及OFF负对照；若失败留证据回滚，不放宽门限。

## Chunk 2: 数据、CNN训练与板端推理

### Task 6: 建立CPU训练环境和数据清单

**Create:** `scripts/rf_ml/__init__.py`、`dataset.py`、`requirements-training.in`；**Test:** `host/tests/test_rf_ml_dataset.py`。

- [ ] 在工程内`.venv-rf-ml`使用已确认的Python3.12创建隔离环境，执行下方命令安装PyTorch CPU/numpy/pytest并记录实际版本。下载不可用即记录训练阻塞，禁止编造权重；不改host或板端环境。后续训练测试均显式使用该venv Python，不能落回系统3.8。

```powershell
& '<LOCAL_PATH>' -m venv .venv-rf-ml
& '.venv-rf-ml/Scripts/python.exe' -m pip install numpy pytest
& '.venv-rf-ml/Scripts/python.exe' -m pip install torch --index-url https://download.pytorch.org/whl/cpu
& '.venv-rf-ml/Scripts/python.exe' -m pip freeze | Set-Content -Encoding ASCII scripts/rf_ml/requirements-training.lock.txt
```
- [ ] 写数据清单分组/泄漏测试，覆盖同会话不同帧/增强跨split、PRBS种子跨train/test、重复IQ哈希和缺失配置，预期新增模块不存在而失败。
- [ ] 定义每帧schema：capture_id、session_id、split、origin、label、IQ路径/hash、release、Fs/ratio/LO/BW、TX参考、AGC工况、完整/削顶/排除原因。本地单调时间与主机时间分开存；不存token。
- [ ] split在采集前冻结。每已知类train/val/test各≥3独立会话×10有效帧；OTHER的噪声/Chirp/方波/混合信号各在train/val/test各≥3独立会话×10有效帧，OFF单独在各split各≥3会话×10背景帧作质量阈值/负控，不进入归一化六类训练。同会话所有窗口/增强同split。验证阈值仅用val，独立test禁止训练/选择最佳epoch。
- [ ] 运行`& '.venv-rf-ml/Scripts/python.exe' -m pytest host/tests/test_rf_ml_dataset.py -q`全PASS，生成可检查空manifest和schema；之后数据脚本统一`& '.venv-rf-ml/Scripts/python.exe' -m scripts.rf_ml.<module>`，以下简称`python -m scripts.rf_ml.<module>`。

### Task 7: 合成与真实有线数据采集

**Create:** `scripts/rf_ml/synthetic.py`、`collect.py`；**Test:** `host/tests/test_rf_ml_collect.py`、`test_rf_ml_synthetic.py`；**Reuse:** `rf_iq_signal.py`、`rf_tx_waveform.py`及已验证capture client。

- [ ] 为五已知类和OTHER写标签无泄漏/12位界限/完整帧/清理失败拒交付测试；源生成由现有星座契约决定，信道变换不从RX结果反推标签。
- [ ] 合成生成4096点接收窗口，变化相位、时间起点、I/Q交换、轻微频偏、噪声、幅度和信道；数字域2/3/5MSym/s、RECT/RRC0.35，种子族分组。OTHER包括噪声/Chirp/方波/混合信号；纯OFF不经RMS归一化伪装信号。
- [ ] `synthetic --output evidence/rx-smart/data-synthetic`产出manifest+IQ；脚本拒覆盖已有目录，记录种子/参数，不把有限同帧增强当独立会话。
- [ ] `collect --expected-release <verified-id> --split train|val|test --cable-confirmed --output <new-dir>`按manifest配置顺序执行arm→init→configure→load→capture→disarm；host脚本使用已规定的Paramiko途径，不开第二Overlay。
- [ ] 固定LO80MHz/Fs60MSPS/BW30MHz/tap29/40dB，从峰值150开始；先每类pilot并保留OFF对照，清理和削顶均通过再扩充。过载留FAIL，降幅作为新工况重采；每split分组不重用PRBS种子。
- [ ] 数字覆盖表冻结为`{BPSK,QPSK,QAM16,QAM64} × {2M,3M,5M Sym/s} × {RECT,RRC0.35} × {train,val,test}`，每格≥3独立会话×10有效4096点帧。同一split内可共享种子集合，但跨split集合不交叉；各split覆盖同工况而不共享会话。TONE按每split≥3×10，OTHER/OFF按Task6。缺工况格报错且禁止生产宣称整个支持域；缩窄域需要同步规格/模型元数据，不能在报告里隐去缺项。
- [ ] 每会话出口验证source=false/RX ARM=false/control disarmed；TIMEOUT或无法关闭立即停止剩余硬件采集，保留日志恢复唯一服务；不得把失败帧计入有效样本数。
- [ ] 用新测试和manifest校验器确认每类有效数量/独立会话与重复哈希、排除数据；这里只判采集和数据质量，不宣称解调或识别成功。

### Task 8: 固定CNN、训练和可解释评估

**Create:** `scripts/rf_ml/train.py`、`evaluate.py`；**Test:** `host/tests/test_rf_ml_training.py`。

- [ ] 写固定shape/参数量、训练输入不包含TX标签元数据、按group split、只在val选epoch的测试；红例后实现以下固定网络（类序TONE/BPSK/QPSK/QAM16/QAM64/OTHER）：

```python
class TinyIQCNN(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.body = torch.nn.Sequential(
            torch.nn.Conv1d(2, 8, 7, stride=2, padding=3), torch.nn.ReLU(),
            torch.nn.Conv1d(8, 16, 5, stride=2, padding=2), torch.nn.ReLU(),
            torch.nn.Conv1d(16, 16, 3, stride=2, padding=1), torch.nn.ReLU())
        self.head = torch.nn.Linear(16, 6)
    def forward(self, x):
        return self.head(self.body(x).mean(dim=-1))
```

- [ ] 输入float32 `[batch,2,4096]`，每分量去DC、共用复数RMS；纯零/削顶/非4096先拒绝。输出1662参数/~1.278M MAC核对通过，增强只作用于train。
- [ ] `train --manifest <synthetic-manifest> --output evidence/rx-smart/models/synthetic-candidate`固定CPU/随机种子，保存loss/val曲线、版本、数据hash、最佳val checkpoint；仅生成synthetic候选标签。默认AdamW/lr=0.001/weight_decay=0.0001/batch32/max_epochs50、交叉熵按train类别频次加权，val选择最佳macro-F1 epoch。收据固定seed=20261001，同时记录Python random/numpy/torch/DataLoader shuffle与增强RNG seed，DataLoader num_workers=0；任何调参仅用val并记录，不承诺跨版本逐位一致。
- [ ] `train --manifest <real-manifest> --initialize <synthetic-checkpoint> --output evidence/rx-smart/models/cabled-candidate`用真实train微调，val选择epoch及max-softmax/margin阈值；退化能量阈值也在train/val确定，不用test调参。
- [ ] `evaluate --model <cabled-candidate> --manifest <held-out-manifest> --split test --output evidence/rx-smart/eval/cabled`运行独立测试，包含被拒绝的已知样本，输出混淆矩阵/macro-F1/逐类召回和未知、OFF分别拒识率。OTHER输出或max-softmax/margin阈值拒识统一映射UNKNOWN；未知/OFF判UNKNOWN算拒识成功，已知判UNKNOWN算漏检。单独报告合成/有线，绝不声称空口结果。
- [ ] 上线门限：已知macro-F1≥0.90、各类召回≥0.80、未知及OFF分别拒识≥0.90；未达标将模型标experimental且model能力false，保留训练交付而不伪造PASS。评估目标不保证能达到。

### Task 9: numpy运行时、导出和板端资源

**Create:** `pynq_overlay/sw/rf_cnn_runtime.py`、`scripts/rf_ml/export.py`、`benchmark.py`；**Test:** `pynq_overlay/sw/tests/test_rf_cnn_runtime.py`、`host/tests/test_rf_ml_export.py`。

- [ ] 写形状/文件大小/非法非有限权重/schema/hash错配拒绝、拒执行任意代码的测试；使用JSON而非接收任意pickle。
- [ ] 导出模型权重、类别/预处理版本、支持域、阈值、训练清单hash、独立评估清单hash；卷积为cross-correlation，不翻kernel，stride/padding与PyTorch逐项一致。
- [ ] 纯numpy实现三个卷积/ReLU/GAP/linear及稳定softmax，预处理同训练；Torch与numpy各层和最终logit固定对照最大绝对差≤1e-4。
- [ ] 以Paramiko把runtime/模型及冻结输入送独立验证目录，在板上调用纯推理，不创建Overlay；100帧benchmark保存热P95、RSS、numpy版本及模型hash。
- [ ] 要求包≤128KiB、增量RSS≤32MiB、热P95≤200ms。失败保留实验模型；网络调整须重验数值/准确率，不悄悄换大网络。无模型/错误模型时基础RX仍可运行。

### Task 10: 智能页面与完整候选交付

**Modify:** `rf_status_worker.py`、`ui_model.py`、`ui_view.py`、`pynq_status.py`及模型artifact发布链；**Test:** 新`pynq_overlay/sw/tests/test_rf_smart_pipeline.py`。

- [ ] 写新鲜实测/过期/实验模型/误hash/无信号/削顶/UNKNOWN负控，以及“填草稿”零硬件写入和AUTO永久关闭测试。
- [ ] 有效当前IQ可发布预测/分数/来源/模型版本；未独立验收的模型明确实验状态。不要将softmax分数称校准概率，也不把类别预测当载波/定时同步成功。
- [ ] 推荐动作仅携带允许的本地草稿字段。配置变化/意外会话丢失立即作废，30秒过期后只保留历史，不恢复发射。
- [ ] 运行完整既有与新增Python/C检查，人工检查800×480智能有效/UNKNOWN/过期/实验态；模型达到标准才在新release声明production可用。
- [ ] 软件发布按Task5门禁重新打包和核验；部署后走UiView事件→真实采集→真实numpy推理并对比离线结果。停止/解除读回，保存实际release、源码/模型/data/eval hash。
- [ ] 真人触摸按报告的30次点击/P95和30秒静默watchdog清单操作；没有现场证据仍标NOT RUN。

## Chunk 3: 天线与可选LNA（独立于有线CNN交付）

### Task 11: 明确天线工况并建立无LNA基线

**Create on execution:** `docs/reports/rx-antenna-acceptance.md`、`evidence/rx-antenna/manifest.json`；不新增默认硬件动作。

- [ ] 收集实际天线型号/频段/阻抗/接口、馈线损耗、目标频段/距离和已知信号源；只凭“甜甜圈/直杆”不选放大器，不将2.4GHz天线当80MHz合格。
- [ ] 先做天线→短50Ω馈线→RX1的接收基线，在同频带用50Ω终端记录背景。当前只实现接收，不从文档操作自动启动空口TX。
- [ ] 固定位置/极化/带宽和来波，记录谱峰/背景、削顶、增益/AGC可用读回、CNN实验/已验收状态；没有已知参考不报实测BER/SNR。
- [ ] 记录是否满足目标质量；无LNA已满足则不加。弱信号或馈线损耗占优且预计改善系统NF，才进入Task12；强干扰/过载先处理滤波和电平。

### Task 12: LNA旁路对照与空口模型评估

- [ ] 根据实际频段检查候选LNA噪声系数、增益、压缩/线性、供电/旁路和最大来波预算；包括峰值/峰均比、滤波和馈线损耗。不可把AD9361的AGC当引脚过压保护。
- [ ] 同一天线/频段/环境做旁路与开启对照，外部增益/滤波顺序明确记录，比较接收质量而非只看ADC幅度变大；过载保留失败。
- [ ] 通过50Ω实际校准再给端口Vpp/dBm/增益推荐；未校准保持相对量。首轮功分器物理回环不加LNA。
- [ ] 按场景/会话/距离建立独立空口测试集，仅测试固定有线模型；需微调时另分空口train/val/test，禁止用空口test选阈值。
- [ ] 交付“无LNA已满足/有LNA改善/有LNA过载或无改善”的实证结论及模型域说明，不把有线成绩代替空口成绩。

## 交付与执行顺序

Chunk1可先交付可用接收界面、无模型release；Chunk2随后交付实际训练与独立评估及资源报告；Chunk3需要天线工况后再执行，缺资料不阻塞前两项。第二阶段同步/完整解调/帧协议/CRC/已知比特BER另做独立规格，首版仍显示未接入。

每个Task红/绿结果、命令与退出码保存在新的证据目录；不可只在此文档勾选而没有证据。回归计数以执行时输出为准，717是当前已有历史基线，不预填为新代码通过数。
