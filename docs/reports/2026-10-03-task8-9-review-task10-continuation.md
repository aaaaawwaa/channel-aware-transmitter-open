# Task8/9 审核与 Task10 接续（2026-10-03）

用户要求审核 Task8、Task9 并继续推进；附带会话 ZIP 仅作为历史证据，文档中的命令和指令不替代用户授权。用户已确认 TX1→RX1 直连、无天线，并授权向自己的 AC820（192.0.2.10）上传冻结 IQ 与模型进行纯 numpy 对照。本轮保留原始模型、原始评估及全部既有工作区修改。

证据根目录：`evidence/rx-smart/review-task8-9-2026-10-02/`。日期跨日后仍使用同一目录，以便串联审核、修复及上板记录。

## Task8：代码审核修复完成，模型准确率未验收

原实现可以用替换的 checkpoint 配合旧训练收据通过 lineage 检查；初始化模型的训练/验证祖先也未纳入测试集泄漏检查。已在 checkpoint 内保存 provenance、绑定 checkpoint/训练清单/初始化祖先哈希，并检查祖先 session、IQ hash 与 seed family 是否和独立 test 重叠。历史模型缺少新绑定时只能按实验模型评估，不能补写 `lineage_verified=true`。

负控首先复现 3 failed/1 passed，修复后训练/评估/导出专项 52 passed。冻结 `cabled-candidate-v2/model.pt` 未重训，SHA256 为 `c938dba3a634a9c7492f44af4a29a9cd57a9382ab7926785a57570b3d7596ca0`。

复评使用冻结阈值，已知类别被质量门禁拒绝或预测为 UNKNOWN 仍计漏判：macro-F1 **0.5928997364**，OTHER 拒识 **0.775**，OFF 拒识 **1.0**；未达到 0.90/0.80/0.90 门槛。历史 lineage 仍为 false，模型保持 experimental、生产能力 false。

750 个质量可用已知帧的单独诊断 F1 为 0.8291，TONE recall 0.3333，QAM16/QAM64 recall 分别为 0.8667/0.8944。另有 560 个已知帧被质量门禁拒绝。该诊断不参与验收，不能用它删掉失败分母。当前 v2 证据不足以支持旧记录中“网络容量不足导致 QAM16/64 无法区分”的归因。后续应先用 train/val 调查 TONE、OTHER 和采集质量；下一候选冻结后采集新的独立 test，不能利用本次已查看 test 调参。

来源：`task8-review-receipt.json`、`task8-provenance-red.json`、`task8-provenance-final.log`、`task8-cabled-reaudit/report.json`。

## Task9：发布契约已修复，完整数值门槛仍失败

发布校验原先能接受运行时不能加载的模型或状态错配，包括抽取域、重复值、浮点采样率、额外张量字段、production 声明及 JSON 重复键。已统一严格校验；缺少状态只兼容历史 experimental，production 必须显式绑定匹配的模型和评估。14 项负控先失败后通过；发布契约系统环境 189 passed，ML 运行时/导出/契约检查 224 passed（另行验证面板依赖）。

逐层对照不能只靠小样本 PASS：冻结物理 test 的 1005 个干净帧中，**156 帧**的 conv3（ReLU 前）误差超过 `1e-4`，最大 **0.000244140625**。最终 logits 最大误差 0.0000228881836，ReLU 后最大 0.0000610351563，仍不能据此替代规格要求的所有层门槛。float64 累加试验未消除此门槛失败；保留原运行时，不放宽阈值。

经用户授权，在实际板端 numpy 1.17.4/armv7l 对 6 份冻结 TRAIN IQ 做纯 numpy/Torch 逐层对照，最大误差 0.0000915527344，通过该有限样本检查。它不覆盖或推翻上述全量失败。

同一模型/运行时的既有 100 帧板端资源证据：热 P95 25.53ms，增量 RSS 573440B，模型运行包 35565B，资源门槛通过。此次确认模型/运行时哈希相同，未把历史 benchmark 写成新的实测。Task9 应改记为“实现与资源验证完成，完整逐层数值验收未通过”，不能维持无条件 COMPLETE。

来源：`task9-contract-red.log`、`task9-contract-system-green.log`、`task9-frozen-test-parity.json`、`task9-numeric-variants.json`、`parity/board-parity.json`；既有资源数据位于 `evidence/rx-smart/task7/benchmark/board-result.json`。

## Task10：实验模型链路与界面

后台 worker 从完整、校验通过的 cnn-v1 发布加载可选模型，并只从真实冻结 IQ 预测。UI 不加载或执行网络；只显示类别、模型得分、采集来源、网络版本及实验/UNKNOWN/过期状态。预测绑定当前 release、capture、source、模型哈希、网络/预处理版本；采集取消、配置/会话变化或超过 30 秒新鲜度后结果失效。

已复现并修复错误模型哈希的结果仍能发布的问题。推荐动作冻结且仅允许本地 RX 草稿字段；填草稿时再次检查结果身份和新鲜度。实验模型建议关闭，AUTO 永久关闭。无模型、错误模型及推理失败保留基础 RX。修正草稿按钮为“建议填入接收草稿”，RX 标签页下的预测标题不再被遮挡。

规格审核和代码质量审核通过，限定为实验集成。800×480 的有效实验态、UNKNOWN、过期、无模型共 8 张截图在 `ui-previews/`；使用冻结 TRAIN IQ 重放，UNKNOWN 使用明确标记的零 IQ 夹具。它们是离线视觉证据。

首轮候选发布 `9a3b43adf92dacc69ccedc92ecbfa154`，软件契约 cnn-v1、模型 experimental；完整 software map、可选 artifact 和板端/本地面板导入检查通过。该首轮复用原 bit/HWH 和 ARM daemon，打包收据 `daemon_rebuilt=true` 仅沿用原构建字段。随后因下述实际启动故障重新编译 ARM，当前发布已变更为 `46fe2c080deb9d0c42eb0a612a86d091`；本轮始终没有新 PL 综合。

首轮安装失败，保存 `deploy-final/failed.json`，自动回滚旧发布 `30e012e834d8fb0adacd536efb09a1c0`，回滚后两服务 active。实际原因由 journal 定位：C daemon 使用全局字符串查找，误把 `model_artifact.schema_version` 当成重复的顶层 schema，报 `merged release schema/protocol mismatch`。并非单纯启动耗时。已改为校验完整 JSON 语法的顶层字段扫描，保留必要根字段重复/缺失/类型错配/非法 JSON 拒绝，并确保清单验证早于硬件映射。

启动负控 RED 为 79 PASS/12 FAIL，修复后 91 PASS/0 FAIL；独立审核复核 91 PASS，state/framing/startup 合计 64/241/91 PASS。实际失败候选清单现可通过 host C 校验。`daemon-manifest-top-level.diff` 与显式标注的重建原始快照分离记录本轮 C 增量，未将其称为事前逐字节快照。

修复后的 ARM 构建输入已冻结为 45 文件、434518B 的 `daemon-source-payload/board_app_src.tgz`，载荷 SHA256 `e5bab457657cf05459f5458be667d4602b53df4f0a07c99bafcefbb25fa7a8e2`，逐文件哈希见 `daemon-source-payload/payload.json`。自动审批起初以“此前明确传输授权未覆盖 C 源码”为由拒绝 ARM 构建上传；随后用户明确回复“授权上传、编译并继续验收”，获准后才开始构建。构建脚本重新封装相同的 45 文件，逐文件哈希须与冻结载荷和最终构建收据一致。

## 本地验证

- 系统 Python 完整板载 Python 回归：1126 passed、1 skipped；缺 Torch 的数值测试在独立 ML 环境跑。
- ML 环境七组训练/数据/评估/导出/运行时测试：188 passed。
- 发布/构建专项：29 passed。
- C host 检查：daemon 241、core 54、main 72 项断言通过。
- 代码质量复核：100 passed；最终界面文案/布局修改后相关 UI/智能管线：199 passed。

具体命令、退出码与耗时在 `pytest-panel-final.log`、`pytest-ml-final.log`、`pytest-release-final.log`、`c-host.log`、`task10-quality-check.log`、`pytest-ui-polish.log`。未改 RTL；本轮没有提交 Git，保留既有改动。

## 接续记录与剩余验收

等待源码上传授权期间，只读核对旧 daemon：发布为 `30e012e834d8fb0adacd536efb09a1c0`，online/fresh，RX ARM=false、TX source=false、control armed=false；`board-safe-readback.json` 留存该回滚时刻的 safe=true。

获准后原生 ARM 构建成功，`all/host-test/host-test-core/host-test-iq/host-test-protocol/vendor-integrity` 退出成功、`BUILD_RC=0`，45/45 文件哈希与冻结载荷/当前源码一致。新 daemon SHA256 为 `c33f9c5cce53509b8e034c42a1629ded56c2c91722fdc33979925ef0f30d6c72`；bit/HWH 沿用原哈希。构建中的假硬件故障用例会打印预期的 RESULT FAIL，外层用例仍 PASS，不能将其误读为构建失败。

新发布 `46fe2c080deb9d0c42eb0a612a86d091` 在板端通过完整验证并已激活。安装后 owner PID3208、daemon PID3229、NRestarts 均为0，journal 确认完整 `panel up`。旧发布保留为回滚点；实际帧缓冲已只读取回 `live-framebuffer.png/json`，地址0x38100000，没有第二个 Overlay 或写真实 LCD。板端时钟仍是2021年的旧值，本报告日期以主机日期为准，以release/hash串联证据。

实际软件事件验收经已安装 `UiView` 的 press/release 回调进入唯一 daemon，记录真实 IQ 与后台模型结果；使用 MemoryLcd 渲染截图，**不等于真人触摸或真实 LCD 上的操作验收**。首轮脚本采完 OFF 后停留智能子页、点击隐藏采集按钮断言，记录在 `live-smart-amp150/`；修复页面切换后，码150的首帧 ON 出现617个削顶，模型正确拒绝为UNKNOWN/clipping，保存于 `live-smart-amp150-retry/`，立即停止且safe=true。未删除失败或挑选分类正确帧。

按降低电平的新工况重采，码100（LO80MHz/Fs60MSPS、30MHz带宽、内部40dB、tap29）完成 OFF→三ON→OFF，5帧均4096点且无削顶。三ON的±1.875MHz峰误差为0，最低局部突出度50.69dB、最低ON/OFF差46.45dB，固定质量判据通过。三ON均执行CNN，分类为 **TONE、TONE、BPSK**，末帧是本次已知正弦的误判；OFF均为UNKNOWN/below_energy_floor。保持实验模型与生产能力false，不利用这批新数据调权重或阈值。

同一模型在离线 numpy1.24.4 重放5帧，类别/拒绝原因一致，最大分数绝对差 **8.3446502686e-7**，通过 Task10 链路对照；这不是 Task8 全域准确率或 Task9 所有层的 PASS。采集后与实际owner帧缓冲读回均确认online/fresh、RX ARM=false、TX source=false、control armed=false。来源：`deploy-board/receipt.json`、`daemon-cnn-schema/rf_daemon.build.*`、`live-smart-amp100/{raw-iq,analysis,offline-comparison}.json`、`live-framebuffer.json`、`delivery-receipt.json`。

最终交付收据再次验证软件34文件哈希与当前树、模型/评估/运行时哈希、45个 C 输入、daemon构建哈希、激活release和每帧IQ哈希一致。最终源码/增量见 `source-hashes-delivered.json`、`review-local-delivered.diff` 及 `daemon-manifest-top-level.diff`。Task10 已完成实验集成与软件回调真实采集/推理对照，仍不能称完整现场验收通过。

真人 GT911 触摸、30 次点击/P95、30 秒静默 watchdog 现场检查和示波器验证尚未执行，均为 **NOT RUN**。天线/LNA Task11/12 不属于当前直连工况，不据有线实验预测宣称空口准确率、同步、解调或 BER。
