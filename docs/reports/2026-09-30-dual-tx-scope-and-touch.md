# 双 TX 示波器验收与触摸屏发射对接阶段报告

日期：2026-09-30；工程：AC820 / Zynq-7020 / AD9361。

## 本阶段结论

双 TX **频率与启停控制验收通过**。在控制连接持续有效期间，两个物理输出均出现预期调制边带，三帧重复采集通过预先冻结的频率、谱线突出度与 ON/OFF 对照判据。此次不作绝对 RF 功率校准、1Vpp 保证或双端口幅度一致性验收。

HK-50 的用户提供标识为 `through-typeload HK-50 50omu-1W`，据此判断为 **50Ω、1W 直通终端负载**。它给原生高阻示波器输入提供同轴终端负载，有实际用途；不是频率转换器。归档中“造成20–30dB衰减”的结论缺少同一通道/端口、同一有效发射状态的 A/B 对照，本轮数据亦不支持该幅度损失。准确型号参数、80MHz匹配精度及插入损耗仍未获得厂家资料或器件校准。

触摸屏首阶段对接已实现并部署：从加载起保持连接，复用该连接轮询，显示真实 TX 状态，提供加载/确认发射/停止。沿已部署产品的模拟触摸命中→回调→模型→worker→客户端→实际 AD9361 路径，第二轮双通道示波器验收亦通过。实体屏幕人手点击尚未执行，不能把程序模拟触摸写成人手触摸验收。

## 接续依据与测试前现场状态

- 读取用户指定的 `<LOCAL_PATH>
- 实际集成源码：`<LOCAL_PATH>
- 本轮接线按用户描述：TX1→HK-50→CH1；TX2→CH2。接线未由图像或人工通断测量独立确认；仅 CH1 有负载，因此 CH1/CH2 幅度不可直接用于端口一致性判定。
- 示波器 USB ID：`<VISA_RESOURCE>`；实读 `KEYSIGHT TECHNOLOGIES,DSO-X 1102A,<DEVICE_SERIAL>,01.10.2018012838`，两输入均读回 `ONEM`。
- 板端 release：`d56794a32b98a8511a980e9bc90f6298`。唯一 overlay owner `pynq-status` PID12268；RF daemon PID12288；两服务 active、NRestarts=0。本轮没有重配 Overlay()，没有更换 bit/HWH 或重启服务，也不涉及 Azure 构建。
- 板载时间历史上不可靠；报告以本机日期、单调时钟与 PID 为准，截图仪器时钟仅作辅助。

## 发射与采集配置

LO=80,000,000Hz；采样率60,000,000SPS；RX/TX带宽30MHz；衰减0mdB；2R2T；IDELAY使能、tap7。波形为1024点、I=round(1500*sin(2π*32*n/1024))、Q=0，s16_iq_le，4096字节，循环。基带1.875MHz，期望RF边带78.125/81.875MHz。RTL 的 BRAM源在2R2T模式下把相同 I/Q 送到两个 TX；不依赖旧 DDS 模式的“仅TX1”假设。

示波器两通道均显示、探头系数1、DC耦合、偏移0、200mV/div、带宽限制关闭；主时基3.125µs/div；EDGE触发、AUTO扫掠。`:DIGitize CHAN1,CHAN2` 完成后停止采集，读取同一帧的两通道；每通道62,500点，X increment=0.5ns，FFT bin=32kHz。根据各自 preamble 转为伏特，不比较依赖记录长度的原始 FFT 整数值。设置及各帧后的 SCPI 错误队列均为 No error。

本轮修正了归档脚本中几个问题：正确使用 `:TRIG:SWE AUTO`，不把 AUTO 当作 `:TRIG:MODE` 类型；使用 `:WAV:POIN:MODE NORM`，而不是未定义的 `:WAV:MODE NORM`；对查询读取回复，不把 `:SYST:ERR?` 仅作为 write 留在输出队列；两通道始终打开，并在新采集完成后取数。[Keysight 1000 X-Series 编程手册，触发、波形与 DIGitize 章节](https://www.keysight.com.cn/cn/zh/assets/9018-07554/programming-guides/9018-07554.pdf)。

## 验收结果

事前判据：目标频率误差≤50kHz，谱线高于70–90MHz局部中位底噪≥14dB（排除目标边带与LO邻域），目标±50kHz窗口内 ON 比前/后 OFF 中较大的谱幅高≥10dB；三帧重复；无 ADC 贴边削波；采集前后均确认 TX_ACTIVE 和 source_selected=true。

| 项目 | CH1 / TX1，带 HK-50 | CH2 / TX2，直接接入 |
|---|---:|---:|
| 三帧下边带 | 78.112MHz | 78.112MHz |
| 三帧上边带 | 81.888MHz | 81.888MHz |
| 对目标频率误差 | ±13kHz | ±13kHz |
| 三帧 Vpp | 0.385930 / 0.393970 / 0.393970V | 0.739698 / 0.739698 / 0.739698V |
| 停机前负对照 Vpp | 0.032161V | 0.024121V |
| 停机后负对照 Vpp | 0.032161V | 0.040201V |
| 目标边带检验 | 6/6通过 | 6/6通过 |
| 削波比例 | 0 | 0 |

共12个“通道×帧×边带”检验通过。前后 OFF 对照使“只是拾取环境信号或读取旧波形”的解释不成立。Vpp 为本次仪器读数，未去嵌示波器频响、同轴线及终端的误差。

![双通道归一化频谱](../../evidence/2026-09-30-dual-tx-scope/dual-tx-spectrum.png)

## 确认的软件根因

首次诊断使用每次操作均断开的短连接：tx.start 成功返回 TX_ACTIVE，但紧随其后的 state.get 已变成 READY、source_selected=false。这与 `rf_daemon.c` 的 `apply_disconnect_rule()` 及 `rf_daemon_main.c` 同步单连接服务一致：活跃控制连接断开后，服务停止正在运行的 TX。

随后改用仅用于验收的、全程保持同一个控制 socket 的测试桥：start后、每帧前以及第三帧后，状态均为 TX_ACTIVE，source_selected=true，读指针持续变化；由此取得稳定双输出。正常 stop 返回 READY，disarm 返回 OFFLINE，source_selected=false；两服务 PID 与重启计数未变化。

这证明短连接生命周期是可复现的停机原因；不能将归档中所有异常都归于这一项，旧测试也可能受看门狗期限、SCPI错误或不同采集时刻影响。保存首次失败日志以免只保留成功样本。

## HK-50 的作用及测量限制

DSO-X1102A 仅有1MΩ输入，官方手册限定阻抗指令合法值为 ONEMeg。外接50Ω直通负载使同轴末端的阻性负载接近50Ω；约50Ω源在低频理想条件下，加载电压相对开路约减半（约6.02dB）。80MHz 时高阻输入电容、线缆反射和终端寄生均会影响读数，因此不能把6.02dB作为本器件的实测插入损耗。

本轮 CH1/CH2 电压比约0.52–0.53，与终端造成加载的方向一致，但端口、线缆和负载不同，不能作为该器件的隔离 A/B 测量。若需要量化 HK-50 效果，应在同一 TX1、同一线缆与 CH1 上、同一有效发射状态下分别带/不带负载采集，并记录 OFF 对照；最好使用两个匹配终端进行双端口幅度比较。1W 是负载功耗额定值，不是它的带宽证明。[Keysight 官方手册的通道阻抗说明](https://www.keysight.com.cn/cn/zh/assets/9018-07554/programming-guides/9018-07554.pdf)。

## 触摸屏对接设计与待办

设计文件：`docs/superpowers/specs/2026-09-30-rf-touch-transmit-design.md`。首阶段提供显式验收正弦参数、加载与确认发射动作；从加载起保持连接，面板轮询与后续启动复用同一连接。STOP/解除/到期/退出释放连接并使加载准备状态失效。真实发射状态依据新鲜 daemon 与 PL 遥测，不依据按钮按下或请求返回成功。

保留仅成功控制请求续期30秒、只读轮询不续期；不增加自动重新布防或自动重启 TX。现有 AWG/数字调制仍属 ACM2108 路径，后续映射 AD9361 数据是独立工作，当前不得宣称已有完整任意波形/调制 RF 输出。

上述规格已获独立评审通过与用户明确批准；实现、回归、软件发布、产品调用链板端与示波器验证均完成。仍待人手在实体屏上复核点击、触控坐标和观看实际显示，及进一步的任意波形/调制数据 RF 映射。

## 新版实现、发布和板端复验

客户端在 tx.load 时保留连接，加载后轮询与启动不重连；STOP/解除、到期/错误、worker退出均释放连接。新鲜快照携带本地 `tx_session_ready` 证据，断线即清除准备状态，即使随后马上重连并读到 READY，也不能跳过重新加载。模型锁定执行中/发射中的编辑，草稿修改会取消启动确认及已加载状态，只有配置已应用且波形已加载才能确认发射。

波形峰值码、周期数均无默认值；1024点、I正弦、Q=0、循环播放的约束在页面明确展示。“正在发射”须由新鲜 TX_ACTIVE、可读 PL 和 source_selected=true 共同证明。发射页的现有数字调制底栏按钮隐藏，避免把 ACM2108 控制混入 AD9361 发射操作。

验证记录：Python 三套回归1457 passed / 2 skipped；最后界面和部署模块调整对应131 passed。native C在隔离的 Linux host object 目录重编译通过：64/0、225/0、72/0、39/0、5/0，SELFTEST HARNESS OK，exit0；其预期故障负控按 harness 判据通过。首次 native 编译复用了 Windows 对象而发生 __main 链接错误，使用独立 object 目录后消除，未修改 C 源码或放宽断言。代码规格与质量审查 Approved；源码 diff --check 通过。

源码提交：**d204f05**（此前规格提交423f850）。当前软件 release：**67865a3e41b50c048b62d52b85011901**，按项目既有流程重新打包并验证、短暂停止两服务、安装不可变新目录、原子切换 current 与单元、启动唯一 owner。发布目录的软件摘要新增 rf_tx_waveform.py，部署计划与 installer 均携带该模块，板上 --require-software 完整校验 exit0。旧 d56794a32b98a8511a980e9bc90f6298 保留。没有重新运行 Vivado；bit/HWH/daemon 与测试前相同：

- bit SHA256：d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555
- HWH SHA256：ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0
- daemon SHA256：96300f3c32b37a1e861b1f06b93a2c73e98265c136bd6ed022e8d8fbdc9f18fc

产品调用链示波器复验：加载后执行三次轮询再确认启动，持续保持 TX_ACTIVE/source_selected=true，三帧每路78.112/81.888MHz，12项边带判据再次通过；CH1约0.386–0.394Vpp，CH2约0.788Vpp。停止/解除后两路回到噪声，源选择关闭；不同初始化轮次模拟幅度略变，不据此作终端损耗校准。

新版 watchdog 复验：后台每0.5秒轮询，额外只读状态检查，持续40秒，自动进入 OFFLINE，`transmitting=false`、`tx_loaded=false`、`tx_session_ready=false`。随后沿模拟触摸事件显式布防成功，无 TX 自动恢复，再解除；证明过期后已释放持有连接，不会阻塞下一次布防。

最终状态：pynq-status PID17681、rf-daemon PID17708，active、NRestarts=0；RF OFFLINE、source_selected=false，io_ok=true。最终查询中的第一次 release 校验因普通用户无法读取不可变目录而 Permission denied，随后以服务所用 root 权限校验成功；未改变目录权限。

### 实体屏操作入口

发射→AD9361→RF参数；先显式填写并布防、初始化、配置，再切 RF发射，填写每帧周期与峰值码，加载后点击发射并确认。当前验收用值如下，页面不会自动写入这些值：

| 参数 | 验收值 |
|---|---:|
| 本振 / 采样率 | 80000000 / 60000000Hz |
| 衰减 / PHY模式 | 0mdB / 0（2R2T） |
| RX / TX带宽 | 30000000 / 30000000Hz |
| 延迟开关 / 档位 | 1 / 7 |
| 每帧周期 / 峰值码 | 32 / 1500 |

STOP 无需确认；30秒无成功控制请求自动停机，状态读取不续期。当前程序已停止发射并解除控制，不能把现场噪声当作仍在发射的异常。

## 证据与复现路径

报告所在工程根目录：`<LOCAL_PATH>`。以下路径相对此根目录：

- session 公共消息摘录：`evidence/2026-09-30-dual-tx-scope/session-public-tail.txt`
- 仪器只读探测：`scope_probe.py`；采集脚本：`scope_acceptance.py`；分析：`analyze.py`
- 实际采集记录：`acquisition.log`、`acquisition.json`；每帧双通道 `.npz`，含原始 byte、伏特与 preamble
- 12项验收明细：`analysis.json`；双通道频谱：`dual-tx-spectrum.png`
- 仪器实际截图：`off-before.png`、`on-1.png`、`on-2.png`、`on-3.png`、`off-after.png`
- 短连接失败证据：`disconnect-attempt/acquisition.log` 与采集记录
- 现场服务与停机确认：`board-state-before.log`、`board-state-after.log`
- 看门狗专项验证：`watchdog_probe.py`、`watchdog.log`、`watchdog.json`；只读每2秒轮询，持续连接，约30.2秒首次观察到 OFFLINE 且 source_selected=false，32.2/34.2秒仍停机，验证通过。保留控制请求续期规则。
- 产品触摸调用链与仪器复验：`product-path/acquisition.log`、`product-path/acquisition.json`、`product-path/analysis.json`、该目录下所有 `.npz/.png`；12项通过。
- 产品 watchdog/再次布防：`product-watchdog.log`、`product-watchdog.json`；板端字体离屏图：`board-preview/product-*.png`（非实体 LCD 摄影）。
- 发布与最终身份：`repackage-final.log`、`deploy-final-dry-run.log`、`deploy.log`、`deployed-final-state.log`、`deployed-release-verify.log`。
- 本地测试：`full-regression.log`、`c-regression-isolated.log`，首次失败日志保留供解释对象文件混用。

脚本在项目现有环境下使用系统 Python + 默认 VISA 后端与已配置的 Paramiko 连接助手。运行仪器脚本会实际启停发射，应在用户授权的现有同轴测量接线上使用。报告中的脚本路径默认位于上述 evidence 目录。
