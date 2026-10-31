# AWG / AD9361 / 接收智能项目进度

> 2026-10-02 已执行接续：Task 1–6 完成，RX 无模型发布与真实 IQ 验收通过，训练环境和数据清单已就绪。当前发布 e201d491bf2e5eecb3979c9bc12ab370，下一项 Task 7。最新状态见 [2026-10-02 接续报告](2026-10-02-rx-session-continuation.md)；下文保留为实施前历史快照。

日期：2026-10-01。此报告汇总主树源码和已有验收文件；本轮只修改文档，没有新增硬件测试、生产代码、模型训练或部署。板端状态以最近实测快照为依据，不把其称为本轮实时查询。附件历史指令只作为证据，当前任务范围来自本轮用户请求。

## 项目所处位置

项目已经具备完整板载面板、两外设软件操作路由、主树RF daemon构建/发布，以及真实正弦数字/模拟回环。当前进入“把已有真实IQ采集接到RX界面，并开发接收分析和CNN”的阶段。模型规格已经审查，尚无训练成果。未用一个百分比混合软件实现、实板验收和未研发接收机三类进度。

三条流水线分别是AWG的PL/裸机PS、PYNQ板载Linux/显示/数据路径、RF AD9361迁移与合并运行。原AWG已有双DAC、波形/谐波/数字调制/RRC、用户数据和信道损伤，PYNQ已有触摸、AWG真源/桥接及DDR队列/环形播放；CLAUDE.md记载P5/P6/P7历史实板验收。本轮没有重跑这些历史全套测试，RF有限IQ映射不能替代ACM2108长流或独立双DAC能力。

## 已完成与证据

| 工作 | 状态与边界 | 证据 |
|---|---|---|
| 状态页降级修复 | 完整software map、隔离panel导入门禁，缺模块根因修复；实际framebuffer完整 | `docs/reports/2026-10-01-session-repair.md` |
| ACM2108/AWG功能与RF映射 | 统一外设选择、独立草稿、单位输入；常见波形、PRBS7/BPSK/QPSK/QAM16/QAM64、RECT/RRC、有限FIR/噪声软件路径存在 | `pynq_overlay/sw/rf_tx_waveform.py`、`rf_iq_signal.py`、统一UI/路由回归 |
| 主树RF构建/打包 | 45个构建输入校验，原生ARM daemon构建及C/vendor测试完成，完整不可变发布 | `evidence/2026-10-01-rf-build-hmi/rf_daemon_safe.build.json`及`.log` |
| 安全修复 | FAULT释放前safe_shutdown；物理capture必须接线确认+内部40dB，已有负控 | `rf_daemon.c`、`test/test_rf_daemon.c`及红绿证据 |
| Python回归 | 最近生产修改验收717 passed/28.90s，本轮文档工作未重跑，不宣称新增代码通过 | `evidence/2026-10-01-rf-build-hmi/pytest-delivery.log` |
| 数字回环 | tap29、抽取倍率1/2/4/8/16各两次、精确0LSB；IQ交叉映射+q,+i | `digital-full-tap29.json` |
| TX1仪器与软件交互链 | UiView命中/按下抬起→实际daemon→TX1示波器验收通过；属于离屏软件点击 | `cabled-hmi-tx-ac/analysis.json` |
| TX1→RX1物理回环 | 已按功分器接线验收，40dB/峰值150零削顶；最近复验3帧±1.875MHz、min ON/OFF41.53dB | `evidence/2026-10-01-rx-smart-preflight-loopback/analysis.json`与`raw-iq.json` |
| CNN设计 | 用户选小型CNN；1662参数/2×4096/主机训练+numpy部署，主体规格独立审查Approved | `docs/superpowers/specs/2026-10-01-rx-smart-design.md` |

最近验证release：`56d0d0d4bd36d4e22d1c030926917a16`。bit SHA256 `d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555`；HWH `ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0`；daemon `9ea94f5a68e6961d09bba0e7063bd4e2c5ac0e5a7af8ac2c31f772aff3154c20`。本次软件修复复用了既有PL，没有新综合。最近回环结束已确认disarmed、source=false、RX armed=false，两个服务active。

## 已知失败与未完成

| 项目 | 当前状态 | 下一动作 |
|---|---|---|
| tap7/15初试 | mismatch/TIMEOUT，保留FAIL | 当前使用已验证tap29，不能宣称整段tap可靠 |
| 峰值1500物理回环 | ADC削顶，保留FAIL | 当前40dB/峰值150工况；新调制仍逐项检查过载 |
| 单次采集完成清ARM | 目前DONE后ARM可保持高，tx.stop不保证清DONE后的ARM；disarm安全关闭已有验证 | 改daemon有限采集收尾、读回确认后发送IQ |
| RX界面捕获 | 按钮禁用/未接入真实IQ | client二进制帧、RX会话、worker/页面接入 |
| 基础RX分析 | 面板尚无真实频谱/RMS/质量链 | 实现纯分析并接入不可变快照 |
| 完整AWG→RF硬件能力矩阵 | 软件生成/路由已存在，当前物理证据以正弦为主 | 逐项波形/调制/损伤回环，不把软件PASS外推硬件 |
| CNN训练/部署 | 未训练、无模型权重；主机当前无torch | 隔离CPU环境、分组数据、训练/独立评估/板端基准 |
| 同步、解调、CRC/BER/EVM | 未实现完整接收机 | 首版保持未接入，后续独立规格与参考对齐 |
| 天线与LNA | 无空口实测；天线型号/频段/馈线/目标距离未确认 | 先无LNA基线，按噪声/过载和馈线损耗决定 |
| 绝对RX Vpp/dBm | 未做整板插口标定；0.843Vpp为芯片50Ω单端正弦绝对极限 | 50Ω参考和频点分支损耗校准 |
| 真人触摸/P95与30秒静默停机 | NOT RUN；已有软件路径/假时钟测试 | 人工点击、视频/时间与实际停机证据 |

## 下一阶段里程碑

1. **M1：可用接收界面。** 修采集收尾、接二进制IQ/后台/频谱、完整发布与实板验收，无模型也可交付。
2. **M2：真实可审查数据。** 五已知类及OTHER/OFF，独立train/val/test会话，零泄漏、过载剔除保留失败。
3. **M3：实际CNN候选。** CPU合成训练及实测微调、numpy数值对照、板端时延/内存；准确率不达门限保持实验态。
4. **M4：智能页面交付。** 新鲜实测推理、UNKNOWN/过期/质量原因、建议只填草稿，AUTO关闭。
5. **M5：天线/LNA与空口评估。** 收到真实天线工况后无LNA起步，做旁路对照和独立空口测试。

对应分任务、路径、命令和验收门禁见`docs/superpowers/plans/2026-10-01-rx-smart-implementation.md`。本轮不重开部署或硬件授权，不将文档交付误写为上述里程碑完成。

早期能力矩阵中“RF status_only/未发射/回环NOT RUN”等记录由后续构建与正弦验收覆盖，不能作为最新进度；仍有效的未完成项是RX按钮、完整调制硬件矩阵、真人触摸和接收/智能研发。

## 关于天线放大器的项目结论

现阶段有线回环不需要LNA。后续天线先用匹配频段天线和短50Ω馈线接RX1，利用AD9361内置LNA/增益；外部LNA是否有用由系统噪声、馈线损耗、目标质量与最大来波决定。强干扰或近距离强信号优先处理过滤和电平，外部放大可能加重过载。天线外形不能决定其适用频率或是否需要放大。

依据：[AD9361数据表](https://www.analog.com/media/en/technical-documentation/data-sheets/AD9361.pdf)、[UG-570 p49 External LNA](https://www.analog.com/media/en/technical-documentation/user-guides/AD9361_Reference_Manual_UG-570.pdf)、[官方输入极限说明](https://support.analog.com/en-us/knowledgebase/article/6EFN2248)。
