# 现场演示拍摄手册与最终提交清单

维护日期：2026-10-31。本文是**只读整理**：只写拍摄步骤与核对表，**不改变接线、不启动发射或采集、不操作板卡、不修改源码/算法/判据**。任何现场动作都必须由操作者在明确授权、现场确认与安全回读之后执行；本文不构成硬件操作授权，也不替代 [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) 与 [试部署 runbook](../rf/deployment-trial-runbook.md)。

> **证据与隐私边界**
> 1. 原始收据、原始 IQ、位流/HWH、原始日志与视频都**不在公开仓库**（见 [交付材料索引](DELIVERY-INDEX.md) §9、[证据包](EVIDENCE-PACK.md)）。公开副本只放脱敏摘要与文档。
> 2. 本文不写个人姓名、联系方式、账号、设备串号、私网地址、主机绝对路径或凭据；拍摄与提交前按 [分享与清理](SHARING-AND-CLEANUP.md) 检查画面与文件名。
> 3. 状态词沿用工程定义：`已验证` / `有限验证` / `未完成` / `NOT_RUN` / `NOT_IMPLEMENTED` / `NOT_MEASURED`；不把有限单帧写成统计验收。

## 1. 演示边界（先说清楚再开机）

**本次现场只演示**：Zynq-7020 + AD9361 板卡的**纯接收**有限快照链路——外部 PlutoSDR 发射 BPSK 基带信号，板卡 RX1 接收并在五寸触摸屏展示**当前帧**的频谱、星座、参考 BER 与有限环境/模型结果。

**本次现场不演示、也不得口头宣称**：

- 板卡自身 TX 发射能力（本次只用外部源；历史发射证据另列，不当场重复自环）；
- 统计误码率/连续锁定（外部 SDR 历史统计整体**未通过**，见 §4 行 N1）；
- QAM64（已移出交付）、EVM（未实现）、真实 RRC 接收（未验收）、自动选档/信道感知（未实现）；
- 完整物理采集 S2（108+12）、46 dB HEADROOM 研究、真人现场验收（均 `NOT_RUN`）；
- RF 源同步 DDR 接口时序“已通过”（**未测**，见 [RF-INTERFACE-TIMING.md](RF-INTERFACE-TIMING.md) §1）；
- ARM 当前版本性能数字（当前交付口径未重测，历史数字不可移植）。

**对外一句话口径**（来自 [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §四，可按现场实际缩减）：

> “平台已实现基于 Zynq 与 AD9361 的有限快照收发、BPSK/QPSK 及 QAM16 接收分析、频谱/星座/参考误码展示与轻量模型结果显示。QAM16 存在已记录误码，QAM64 移出本次交付；RRC 真实链路、EVM 和自动档位选择未完成全项验收。”

## 2. 短演示流程（PlutoSDR TX → 板卡 RX1）

### 2.0 演示路径与前提

| 项 | 本次取值 | 依据/说明 |
|---|---|---|
| 方向 | **Pluto TX → 板卡 RX1**（板卡 TX 全程关闭） | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §二.3；[WIRING](../../sdr_testbench/board_integration/WIRING.md) 阶段 B |
| 波形 | BPSK、3 MSym/s、**RECT**、PRBS7 **seed 1**、**无差分编码**；有限单帧快照 | [BPSK-PARAMETERS](../../sdr_testbench/board_integration/BPSK-PARAMETERS.md) 结论；板卡硬参数同文 |
| 频点 | 共同频点 **433.9 MHz**（两端 LO 相同） | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §二.2；[CAPABILITY](../../sdr_testbench/board_integration/CAPABILITY.md) |
| 板卡接收 | 60 MSPS、4096 点、D1（ratio 0）、BPSK/RECT、参考 seed 1 | [BPSK-PARAMETERS](../../sdr_testbench/board_integration/BPSK-PARAMETERS.md)、[优化与复现](OPTIMIZATION-AND-REPRODUCTION.md) §2.2 |
| 演示性质 | **有限单帧**（不是统计 BER/不是 S2 正式验收） | §4 行 L1/L2 |
| 器材 | PlutoSDR Rev.B（AD9363A）+ 50 Ω 同轴线 + 板卡/五寸屏 + PC；**示波器不接此 RF 链路** | [WIRING](../../sdr_testbench/board_integration/WIRING.md) |

> 前置：允许在**不发射**时先做界面检查、软件预览与 `probe/offline`；真实发射必须在 §3 核对表全部通过、并获得现场授权后执行。**先 probe/offline，再 TX**。

### 2.1 GNU Radio 外部信号源配置要点（软件侧）

**推荐入口（为拍摄准备的本地预览与限时发射）**：`sdr_testbench/gui_demo/` 的 BPSK 外部源 GUI/GRC（见 [gui_demo README](../../sdr_testbench/gui_demo/README.md)）。默认只做本地基带预览，**只有**在控制窗口填写真实接线、勾选“板卡 TX 关闭”并确认后，才调用现有 Pluto 限时发射脚本；GRC 流图中的 Pluto Sink **保持 DISABLED**，不得为拍摄启用禁用块绕过限时控制。

| 配置项 | 值 | 说明 |
|---|---|---|
| 设备 | PlutoSDR（按串号重新发现；公开副本配置中的串号已脱敏，现场替换为本机值） | [SDR 测试台 README](../../sdr_testbench/README.md)；不硬编码 USB URI |
| LO/中心频率 | **433.9 MHz** | 与板卡同频；不把 80 MHz 教学模板套到 433.9 MHz |
| 采样率 | **6 MSPS** | 3 MSym/s × sps 2 |
| 符号率/成形/sps | **3 MSym/s / RECT / sps=2** | 不能用 RRC；板卡只接受 RECT |
| 数字幅度 | **0.1**（请求值；发生器量化后记录实际值） | 不靠调大数字幅度提亮画面 |
| Pluto 内部 TX 衰减 | **60 dB**（发射前回读；收尾压到最大衰减并读回） | [ATTENUATION](../../sdr_testbench/board_integration/ATTENUATION.md) |
| TX RF 带宽 | 1.5 MHz（沿用历史配置，**不是**新带宽质量验收） | gui_demo README §3 参数表 |
| PRBS7 | **seed 1**（统计验收才允许 1/37/73；本次单帧只用 1） | 两端 seed 必须一致，否则参考 BER 无意义 |
| 差分编码 | **无** | 接收机直接用参考相关性定极性 |
| 发射时长/窗口 | 配置窗口最多 **2 s**；收据记录**实测流时长**（既有实现预留调度余量） | 2 s 限制指调制数据流窗口，不含 USB/LO 建立；不为拍摄连续开流 |

> 已知空窗：GR 独占 USB 流期间**无法**从第二个上下文回读 `hardwaregain`；衰减生效由启动顺序保证（构造函数不写增益、调频不写增益、`set_attenuation` 是启动前最后一条增益写），现场按既有脚本执行并保留**收尾**读回（[ATTENUATION](../../sdr_testbench/board_integration/ATTENUATION.md) §已知空窗）。

**先做非发射检查（不产生 RF）**：

```powershell
# 1) 发现设备、读回身份（不发射）
.\sdr_testbench\run.ps1 probe
# 2) 纯软件离线链（不发射）
.\sdr_testbench\run.ps1 offline
# 3) 配好参数但不打开硬件（GUI 本地预览/干跑）
.\sdr_testbench\gui_demo\run.ps1 --dry-run
```

**软件链拍摄要点**：GRC 流图展示 `PRBS7 BPSK → Repeat(RECT,sps=2) → 幅度 → Throttle → 时间/频谱/星座`，并明确 **Pluto Sink 为 DISABLED（仅参数说明）**；参数窗口拍 LO 433.9 MHz、6 MSPS、衰减 60 dB、非 cyclic、带宽 1.5 MHz。**不要把禁用块截图讲成“正在发射”**。

**安全开关顺序（软件侧）**：

1. 先确认板卡 TX 关闭、Pluto TX 关闭（只读回读见 §2.2/§3）。
2. 先设置衰减/LO 顺序：在开 TX LO 之前完成衰减写入与回读；GR 建图与调频不写 `hardwaregain`（[ATTENUATION](../../sdr_testbench/board_integration/ATTENUATION.md)）。
3. 发射仅按一次有限窗口；停止后**重新打开 IIO**，写最大衰减与 `powerdown=1` 并读回（不是只看进程退出码）。
4. 收尾不确认（进程挂起/USB 异常）→ 报 `CLEANUP_FAILED`，停止继续发射，必要时由操作者断开 Pluto USB/电源与 RF 线。

### 2.2 外部发射条件（必须在真实接线确认后才可执行）

1. 板卡与 Pluto 两端 TX 均已关，且已做**只读安全回读**（见 §3-D）。
2. 接线为 **Pluto TX SMA → 50 Ω 同轴线 → 板卡 RX1 SMA**；板卡 TX1 空载、Pluto RX 空载、示波器断开（[WIRING](../../sdr_testbench/board_integration/WIRING.md) 阶段 B）。
3. 一次只允许**一个**发射源；不得同时把板卡 TX1 与 Pluto TX 接到同一接收端。
4. 内部衰减路线已按既有流程设置并可回读；无外置衰减器**不是**本次硬阻塞，但所有电平结论只能是**计算界**，不是实测 dBm（[LEVEL-BUDGET](../../sdr_testbench/board_integration/LEVEL-BUDGET.md)）。
5. 已确认 433.9 MHz 在**赛事/当地允许的频段与功率范围**内；如不允许，改用允许频段并重新核验两端 LO/滤波/判据，**不为拍摄扩频或改固件**。
6. 本次授权已写明：发射次数/总时长、窗口、停止条件、失败不重试；拍摄每段 RF 均计入当次预算（[开发纪律](../memory/03-development-discipline.md) §5）。

### 2.3 板卡 RX1 接收步骤（触摸屏侧）

> 页面/按钮名称以现场版本为准；本文件不重新定义交互，只按 [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §二的顺序组织。

1. **界面确认**：开机后进入“总览”，确认后端在线、屏幕正常；再依次切到“接收”页——应有“概览、采样、解调、智能、星座、环境”子页。
2. **确认设备与接收模式**：“接收→解调”选择 **BPSK、3 MSym/s、RECT**，开启 **PRBS7 参考**，种子与 Pluto 端一致（单帧用 **1**）。
3. **采样设置**：“接收→采样”选择 **D1（不抽取）、4096 点**；板卡输入采样率 **60 MSPS**（只读核对，不在此改判据）。
4. **外部接收配置**：使用经联调核验的 **433.9 MHz 外部源配置**并核对回读；**不要**使用“填入教学配置”（它会填 80 MHz 且默认关闭 PRBS 参考）；只查看草稿不应用。
5. **安全读回**：起始与结束均为 `TX source=false`、`RX armed=false`、`io_ok=true`、非 stale；若现场纯接收流程要求布防，只允许布防**接收**、全程不得启用板卡 TX，采集后回到 `armed=false`。**不点击“回环捕获/确认回环”**（会启动板卡自身发射）。
6. **捕获当前帧**：等 Pluto 限时发射窗口开启、操作者口令确认后，点 **“捕获快照”**（或现场已有的纯 RX 入口），只采**一帧**；若纯 RX 入口不可用，记录具体状态并**停止该步骤**，不用回环按钮替代。
7. **同帧展示**：依次看 **概览 → 解调 → 星座 → 采样/数据**，确认各页对应**同一实际 IQ/本次配置**：解调页记录载波同步/定时同步/参考同步、错误数/比较数/BER；星座页为接收同步符号（工具页的理想星座不作实测）；概览/频谱为当前 IQ 分析（**非校准 dBm**）。
8. **（可选，仅限现场已确认）** 智能/环境页：只展示**同一帧**的模型输出与理由，标注“模型得分未校准、不作为准确率/可信度”；若现场不确定，不拍。
9. **失效检查**：清除本地快照或修改配置后，旧星座/BER 应失效；不能拿旧历史图冒充新采集。

### 2.4 停止与安全回退（固定顺序）

1. **停止发射**：GUI“请求停止并关闭 TX”（协作取消，等 stop/wait 与 LO 关闭读回）；或脚本入口按其既有收尾逻辑结束。若显示关闭未证实，**不继续发射**。
2. **Pluto 安全关断读回**：重新打开 IIO，写 `hardwaregain=-89.75 dB` 与 TX LO `powerdown=1`，**各两次读回**一致；记录 `cleanup_required`。
3. **板卡侧**：不启动板卡 TX；结束时按既有流程 **停止 → 解除/ disarm**，只读读回 `armed=false`、`TX source=false`、`RX armed=false`、`io_ok=true`，并记录 `boot_id`/服务状态。
4. **异常处置**：出现削顶、FAULT、回读不符、USB/端口转发异常、发射预算超限或收尾失败 → **立即停止、不重试、不补采**；收尾无法确认时由操作者断开 Pluto USB/电源与 RF 线，报告 `CLEANUP_FAILED`/`UNKNOWN`。
5. **保存证据**：按现有目录约定保存到**本地**——SDR 侧 `sdr_testbench/results/<run-id>/`（`result.json`/`iq.npy`/截图）、联调侧 `sdr_testbench/board_integration/results/<run-id>/`（`session.json`、板端/Pluto 收据、`failure-cleanup.json`）；屏幕照片/视频由现场指定本地目录；**不放入公开仓库**。记录版本/配置/seed/实测流时长/回读原文/IQ 摘要；公开仓库只引用脱敏摘要（[docs/evidence/](../evidence/README.md)）。

### 2.5 失败停止条件（任一条出现即停，不修补演示）

| # | 条件 | 动作 |
|---|---|---|
| F1 | 任一端 TX 状态不明或回读不符 | 停止，不接线、不发射；先只读取证 |
| F2 | 板卡不是预期 release/服务异常/`NRestarts>0`/存储新错误 | 停止演示，重新基线或改用离线材料 |
| F3 | 削顶/FAULT/USB 错误/端口转发断开/发射预算超限 | 立即按 §2.4 收尾并记录 |
| F4 | 捕获后无参考同步或 BER 非零 | 如实记录实际值，**不换帧、不重试**；仍可作为“有限验证”素材或停止该段 |
| F5 | 收尾读回无法确认 | 报 `CLEANUP_FAILED`/`UNKNOWN`，人工断开 RF/USB，不再发射 |
| F6 | 现场有人质疑未验证能力 | 按 §4 分级表如实回答，不口头补证 |

## 3. 现场核对表（全部通过后才可进入真实发射；任一项不通过就停）

> 编号 C01–C30；每组最后一项为“整组结论”。逐项填写“通过/不通过/未测”与证据（屏幕照片、只读读回、日志文件名）。

### A. 电源与散热（C01–C05）

| ID | 核对项 | 通过标准/确认方式 | 不通过动作 |
|---|---|---|---|
| C01 | 板卡额定电源 | 按板卡资料使用额定适配器/供电；插头锁定、无松动 | 不供电，换合规电源 |
| C02 | **P18 在 2.5 V 档** | 目视跳线确认；3.3 V 档会损坏 AD9361（[boardtest](../rf/boardtest.md) §0.1） | 严禁上电，先改跳线 |
| C03 | 散热与通风 | 散热片/风扇无遮挡、无异味/异响；空载数分钟无异常重启 | 停止，排查供电/散热 |
| C04 | 板卡与 PC 接地/走线 | 不被绊倒、线缆不受力；USB/网线留有活动余量 | 整理后再开始 |
| C05 | Pluto 供电与温度 | USB 供电稳定、外壳无明显过热；不插拔正在开流的设备 | 停止，重新枚举后再试 |

### B. FMC 与 RF 接线/衰减（C06–C13）

| ID | 核对项 | 通过标准/确认方式 | 不通过动作 |
|---|---|---|---|
| C06 | FMC 模块插紧 | FMCOMMS3 卡扣到位；虚接表现与“器件不应答”相同（[boardtest](../rf/boardtest.md) §0.2） | 断电后重插 |
| C07 | 本次接线 | 同轴线：**Pluto TX → 板卡 RX1**；板卡 TX1/Pluto RX 空载；示波器断开（[WIRING](../../sdr_testbench/board_integration/WIRING.md) 阶段 B） | 按 §2.2 重新确认 |
| C08 | 单一发射源 | 同一接收端只接一个 TX；不得同时连接板卡 TX1 与 Pluto TX | 立即断开多源 |
| C09 | 线缆与接头 | 50 Ω 同轴线尽量短、接头拧紧、不过度弯折 | 更换/整理后再开始 |
| C10 | 板卡内部 RX 设置 | 沿用既有 RX 增益设置，不暗改；回读与配置一致 | 记录差异，按既有流程处理 |
| C11 | Pluto 内部衰减 | 60 dB 请求值已写入并回读（发射前）；收尾压到 89.75 dB + `powerdown=1` 各两次读回（[ATTENUATION](../../sdr_testbench/board_integration/ATTENUATION.md)） | 不发射 |
| C12 | 电平预算 | 按 [LEVEL-BUDGET](../../sdr_testbench/board_integration/LEVEL-BUDGET.md) 记录“计算界”，不写实测 dBm；无外置衰减器不阻塞，可选衰减器须记录实际值 | 预算不成立则不发射 |
| C13 | 整组结论 | A+B 全部通过；否则不进入发射 | 停止 |

### C. 频段合法性与参数一致性（C14–C18）

| ID | 核对项 | 通过标准/确认方式 | 不通过动作 |
|---|---|---|---|
| C14 | 频段合法性 | 433.9 MHz 在赛事/当地允许范围内，功率满足要求；有疑问先问主办方/现场负责人 | 不发射，改用允许频段并重核 |
| C15 | 器件频段能力 | Pluto 标准范围 325–3800 MHz；不通过改固件/扩频绕过（[CAPABILITY](../../sdr_testbench/board_integration/CAPABILITY.md)） | 停止 |
| C16 | 两端参数一致 | Pluto：6 MSPS、3 MSym/s、RECT、sps2、PRBS7 seed1、无差分、幅度 0.1、衰减 60 dB；板卡：60 MSPS、4096、D1、BPSK/RECT、seed1 | 逐项改正后重核 |
| C17 | 80 MHz 教学模板隔离 | 确认不使用“填入教学配置”的 80 MHz 默认（[人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §二.2） | 不开始 |
| C18 | 发射预算与时长 | 已写明次数/总时长/窗口/停止条件；单段实测流时长 ≤2 s；不重试不补采 | 未写明则不发射 |

### D. 网络、服务与只读安全态（C19–C25）

| ID | 核对项 | 通过标准/确认方式 | 不通过动作 |
|---|---|---|---|
| C19 | PC 与板卡网络 | 板卡可 SSH/可执行只读检查；到板端控制服务的本机端口转发可用且稳定 | 停止，先恢复网络 |
| C20 | 预期发布版本 | 只读读回 `current`/`active-release` 与现场记录的预期 release 一致（历史记录 `4814bbc4…`；**以演示时读回为准**） | 版本不符先停止并重新基线 |
| C21 | 服务状态 | `pynq-status.service`、`rf-daemon.service` 均 active/running，`NRestarts=0`；面板 READY [[runbook](../rf/deployment-trial-runbook.md)] | 停止，排查服务 |
| C22 | 只读安全态 | `armed=false`、`TX source=false`、`RX armed=false`、`io_ok=true`、非 stale；只读读回命令见 [安装状态入口](../../scripts/rf_teaching_install_state.py) | 不接线、不发射 |
| C23 | 重启/存储证据 | 记录 `boot_id`、uptime；dmesg 无新 MMC/SDHCI/ADMA/I-O/ext4 错误 | 停止，按存储流程处理 |
| C24 | 串口与凭据 | **不打开 FPGA 板卡串口（历史配置中的 COM 口）**；token/口令只记路径不显示值；不用明文口令拼命令（[工程陷阱](../memory/04-engineering-pitfalls.md) §8） | 停止 |
| C25 | 换线前门槛 | 两端 TX 已关且回读通过后才允许拆/接线（[WIRING](../../sdr_testbench/board_integration/WIRING.md) 阶段 B） | 不换线 |

### E. 界面、触摸与证据（C26–C30）

| ID | 核对项 | 通过标准/确认方式 | 不通过动作 |
|---|---|---|---|
| C26 | 七页显示与触摸 | 总览、发射、接收、数据、智能、工具、系统可切换；触摸有响应、无死区 | 记录异常；不连续连点 |
| C27 | 接收子页 | 接收页含概览/采样/解调/智能/星座/环境；页面元素与本次版本一致 | 记录异常，不演示未确认页 |
| C28 | 纯 RX 入口 | 存在可用的“捕获快照/纯接收”入口；**不点回环捕获** | 若不可用，记录状态并停止该步骤 |
| C29 | 证据保存 | 本地证据目录可写；命名含时间戳+模式+运行号；保存 result/IQ/照片/视频/回读原文 | 先解决再开始 |
| C30 | 画面脱敏与整组结论 | 画面/文件名无姓名、账号、串号、私网地址、token、密码；E 组全部通过 | 重拍/裁剪后再提交 |

> 若现场需要“只做不发射的界面与软件演示”（如设备未就绪），可以执行 §2.1 的 `probe/offline/dry-run` 与 C01–C05、C26–C29 中不涉及发射的项；但**不得**把该段讲成已完成外部接收演示。

## 4. 证据状态分级表（逐行给出处）

> 读法：`已验证（有限域）`=有可核对文档/收据支撑、但只在声明范围成立；`有限验证`=仅单帧/有限帧或部分条件，可作为演示素材但不得外推；`未完成/不可演示`=尚未实现、未测或明确移出，不得在演示/视频/旁白中出现“已完成”表述。

### 4.1 已验证（有限域）

| ID | 能力/结果 | 状态与范围 | 出处 |
|---|---|---|---|
| V1 | 发布安装 + 只读读回 | `4814bbc4…` 于 2026-10-26 安装，current/active 一致、双服务 active、RF 安全态成立；**历史记录，不代表当前板态** | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[EVIDENCE-PACK](EVIDENCE-PACK.md) §1 |
| V2 | 试部署 + 注入失败自动回滚 | 修复候选安装并通过非发射验收后注入退出 42，恢复父版本；独立读回服务/RF 安全态/`boot_id`/存储正常 | [试部署回滚收尾](../reports/2026-10-17-trial-rollback-board-closeout.md) |
| V3 | 离线 Python 回归基线（迁移受控证据后） | `pynq_overlay/sw/tests` 2056 passed/5 skipped；`scripts/tests` 496 passed/1 skipped（退出码 0） | [2026-10-14 报告](../reports/2026-10-14-deepseek-board-and-p0-followup.md) |
| V4 | 主机 C 原生套件 | core 98/0、protocol 58/0、sleep 22/0、HEADROOM 148/0、vendor integrity 10 files passed 等（版本相关） | [DELIVERY-INDEX](DELIVERY-INDEX.md) §4、[离线基线](../reports/2026-10-13-final-prj-baseline.md) |
| V5 | SDR 测试台基础能力 | 设备识别、离线单音、RX 样本流、TX 停止读回成立；**RF 输出未验证**（示波器带宽不足、有线回环未完成） | [SDR 实测报告](../../sdr_testbench/TEST-REPORT.md) |
| V6 | 屏幕显示与基础触摸 | 用户现场确认五寸屏正常显示；S1 照片复核部分闭合；**触摸 P95 `NOT_MEASURED`** | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) 开头、[S1 照片复核](../reports/2026-10-04-s1-human-photo-review.md) |
| V7 | 源码回归测试（Linux CI，公开快照） | **956 passed / 2 skipped / 0 failed**（Ubuntu 24.04、Python 3.11、`QT_QPA_PLATFORM=offscreen`）；skip 为 HWH 构建产物缺失与 SDK fresh workspace 不存在；Vivado/release 作业 skipped，**不代表构建通过** | [GitHub Actions run 41189065903](https://github.com/aaaaawwaa/channel-aware-transmitter-open/actions/runs/41189065903)、[README](../../README.md#构建与运行) |
| V8 | Linux XSim 最小 RTL 验证（Azure） | Ubuntu 22.04.5 + Vivado/XSim 2025.2；commit `7729ba7` 的 `am_stage_tb` 6 项 PASS，`xvlog/xelab/xsim` 退出码均 0，工具耗时约 9.1 s；**仅一个最小自检测试，不代表完整 `src/tb/` 套件或综合/位流** | [README](../../README.md#构建与运行)「Linux XSim 快速验证」 |

### 4.2 有限验证（可作演示素材，不得外推）

| ID | 能力/结果 | 状态与范围 | 出处 |
|---|---|---|---|
| L1 | 12 帧选用集接收冒烟 | BPSK 两 ON 均 `0/138`；QPSK 两 ON 均 `0/128`；QAM16 分别 `1/256`、`0/256`；QAM64 移除 9 项负例 `9/9`；**有限帧冒烟，不是整体验收** | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md)、[DELIVERY-INDEX](DELIVERY-INDEX.md) §4 |
| L2 | 外部 SDR 单帧 BPSK（Pluto TX→板卡 RX1） | 2026-10-20 12:22 真机执行**一次**：1 次 4096@60MSPS 采集；原样/共轭两路 `reference_sync` 且各 `0/138`；两路是一致性检查、**不是**两次独立接收；不代表连续锁定/统计 BER | [board_integration README](../../sdr_testbench/board_integration/README.md) BPSK 单次会话 |
| L3 | 频谱/星座/参考 BER 展示（BPSK 域） | 展示当前实际 IQ 的分析结果；频谱非校准 dBm；工具页理想/合成星座不充当实测 | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) H05/H06 |
| L4 | 同帧模型/环境结果显示（可选） | 只展示**同一帧**的标签/得分/原因与版本；模型得分未校准，**不是准确率/可信度**；不得据场景按钮倒推识别正确 | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) H08/H09 |
| L5 | 板卡自身 TX 能力（历史证据） | 历史发射/回环证据保留，但**本次现场不重复自环、不以外部源代替板卡 TX 验收** | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) H02、[boardtest](../rf/boardtest.md) |

### 4.3 未完成 / 不可演示

| ID | 能力/结果 | 状态 | 出处 |
|---|---|---|---|
| N1 | 外部 SDR BPSK 统计验收 | 历史统计 **23/30 帧同步、整体未通过**；不能剔除弱/未同步帧后称通过 | [README](../../README.md)「失败与限制」、[DELIVERY-INDEX](DELIVERY-INDEX.md) §5 |
| N2 | QAM16 零误码/整体验收 | 12 帧选用集 `1/256`；受限演示，不能倒填成零误码 | [交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| N3 | QAM64 | 已移出本轮交付，仅保留历史报告与负结果 | [README](../../README.md)、[交付复审](../reviews/2026-10-26-codex-delivery-closeout-review.md) |
| N4 | 真实 RRC 接收链路 | RRC 选项/离线软件 ≠ 真实 RRC 收发验收；本次主演示 RECT | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) H04 |
| N5 | EVM | `NOT_IMPLEMENTED`（代码与界面显式标注未接入） | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) H06 |
| N6 | 自动选档/信道感知决策 | 档位注册表与策略 `NOT_IMPLEMENTED`；自动执行永久关闭，“建议填入草稿”只改本地草稿 | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) H10、[作品简介追踪](../requirements/brief-traceability.md) |
| N7 | 完整物理采集（S2 正式 108 帧 + 12 离线负控） | `NOT_RUN` | [README](../../README.md)、[验收清单](../requirements/acceptance-checklists-p1-p6.md) |
| N8 | 46 dB HEADROOM 真实研究 | `NOT_RUN`（曾被能力谓词阻塞；真实帧需单次授权） | [README](../../README.md)、[安装门与预检](../reports/2026-10-11-headroom-install-gate-and-preflight.md) |
| N9 | 真人现场验收 | 未完成；由用户现场确认，AI 不代签 | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md)、[验收清单](../requirements/acceptance-checklists-p1-p6.md) |
| N10 | RF 源同步 DDR 接口时序 | 约束缺陷已确认、**板级接口时序未测**；内部 WNS/WHS 不覆盖外部接口，不得宣称通过 | [RF-INTERFACE-TIMING.md](RF-INTERFACE-TIMING.md) §1.1/§1.2 |
| N11 | 位流/HWH/原始 IQ/原始收据 | 不在公开仓库；不能在本副本内复算历史指标 | [EVIDENCE-PACK](EVIDENCE-PACK.md) §0、[DELIVERY-INDEX](DELIVERY-INDEX.md) §9 |
| N12 | 触摸时延（P95） | `NOT_MEASURED`（用户已取消专门计时，不作为阻塞项） | [作品简介追踪](../requirements/brief-traceability.md) |
| N13 | QPSK/QAM16 外部源 | 本次外部源先验 BPSK；QPSK/QAM16 外部链**未验证**，不顺带宣称支持 | [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §二.3 |
| N14 | 功耗/散热实测 | 仅有 Vivado `report_power` **估计值**（非板端实测） | [EVIDENCE-PACK](EVIDENCE-PACK.md) §2 第 4 行 |
| N15 | 当前交付版本 ARM 性能 | 当前交付口径 P95/RSS **未重测**；历史候选数字不可移植 | [SYNTHESIS-METRICS](SYNTHESIS-METRICS.md) §4.1、[优化与复现](OPTIMIZATION-AND-REPRODUCTION.md) §1.3 |

## 5. 最终提交清单

> 原则：提交材料与演示口径一致；所有“未验证/未完成”保持原样；公开仓库可见 ≠ 报名提交完成（见 [COMPETITION-MATERIALS](COMPETITION-MATERIALS.md) 开头声明与 §7 检查项）。

| # | 提交物 | 位置/入口 | 状态 | 负责人/说明 |
|---|---|---|---|---|
| S1 | 公开仓库（源码+文档） | 仓库根 [README](../../README.md)、[COMPETITION-MATERIALS](COMPETITION-MATERIALS.md)；提交前确认 HEAD/分支与材料一致 | **已有** | Lead；报名后台登记是独立步骤（S9） |
| S2 | 设计报告 | [FINAL-DESIGN-REPORT.md](FINAL-DESIGN-REPORT.md)（提交版）、[DESIGN-REPORT.md](DESIGN-REPORT.md)（扩展版）；指南 `report/` 入口 [report/README.md](../../report/README.md) | **已有** | Lead；引用证据层级与失败限制 |
| S3 | 海报 / PPT（英文、中文） | 文案已有：[POSTER-AND-PPT.md](POSTER-AND-PPT.md)（英文海报/PPT 文案 + 中文讲稿）；**实际海报/PPT 排版文件待制作** | **文案已有 / 排版未完成** | Lead/材料负责人；内容口径按本文 §4，禁止出现 §4.3 行 |
| S4 | 技能包入口 | [skills/README.md](../../skills/README.md)（25 文件：24 技能文件+入口 README）；指南 `skill/` 映射 [skill/README.md](../../skill/README.md) | **已有** | 已写明适用范围/方法/失效条件/已验证效果；跨项目验证标注“尚未验证” |
| S5 | AI 协作记录 | [AI-COLLABORATION-RECORD.md](AI-COLLABORATION-RECORD.md)（K1–K3 + S1–S3 + 智能体工作流）；案例 [AI-COLLABORATION-CASE.md](AI-COLLABORATION-CASE.md) | **已有** | 改写摘要，不发布完整私人会话 |
| S6 | 综合/实现/证据/优化/接口时序材料 | [SYNTHESIS-METRICS](SYNTHESIS-METRICS.md)、[EVIDENCE-PACK](EVIDENCE-PACK.md)、[docs/evidence](../evidence/README.md)、[OPTIMIZATION-AND-REPRODUCTION](OPTIMIZATION-AND-REPRODUCTION.md)、[RF-INTERFACE-TIMING](RF-INTERFACE-TIMING.md) | **已有（有限）** | 原件不在仓库；当前版本优化前后 `未取得`、接口时序未测，均如实标注 |
| S7 | 演示视频 | **待拍摄**，**不在仓库**；拍摄脚本见 §6；成片可按主办方要求提供网盘/本地 | **未完成** | 用户/现场操作者；须经 §3 核对与本文件 §6 旁白检查；画面/文件名脱敏 |
| S8 | 许可证与第三方声明 | [LICENSE](../../LICENSE)、[NOTICE](../../NOTICE)、[LICENSES/](../../LICENSES/README.md) | **已有（有限）** | 5 个第三方参考 RTL 许可未确认，公开范围以此为前提 |
| S9 | 报名后台人工登记 | 大赛官网/报名后台（指南 3.3.6） | **未完成（用户完成）** | 用户/队长：队伍信息、仓库链接、材料上传、匿名/双盲规则确认；AI 不代填 |
| S10 | 文件名/目录结构说明 | [FILENAME-CONSULTATION.md](FILENAME-CONSULTATION.md)、[GUIDE-CONFORMANCE](GUIDE-CONFORMANCE.md) §4 | **待答复** | 咨询文字尚未发送；答复前**不改名、不搬迁** |
| S11 | 现场演示材料（脚本/核对表/PPT 备份/离线备选） | 本文档 §2–§3、§6；离线演示用 SDR 测试台离线命令与截屏 | **待准备** | 用户/操作者；离线替代不得讲成实时接收 |
| S12 | 提交前最终检查 | 见下方核对项 | **待执行** | Lead 最终确认 |

**提交前最终检查（S12）**：

- [ ] README 的交付范围、失败与限制、演示口径与现场一致（[README](../../README.md)）。
- [ ] 公开仓库 HEAD 与材料索引一致；`skills/`、`skill/`、`report/` 入口链接有效。
- [ ] 源码回归可复现：`QT_QPA_PLATFORM=offscreen python -m pytest host/tests -q` 通过（当前公开快照 956 passed / 2 skipped / 0 failed）；两项 skip 原因与 Vivado/release `skipped` 状态已说明，未把 `skipped` 写成构建通过。
- [ ] 设计报告/海报/PPT/视频不出现 §4.3 的“未完成/不可演示”表述被写成已完成。
- [ ] RF 接口时序表述为“未测/未约束”，**不出现“接口时序通过”**；内部 WNS/WHS 不当作接口证据。
- [ ] 功耗数字标注为 Vivado 估计值；性能数字带候选/日期/证据层级。
- [ ] 未把有限单帧 BER 写成统计 BER；未把 QAM16 受限演示写成零误码；QAM64 表述为移出交付。
- [ ] 许可证/第三方范围与公开内容一致；5 个第三方 RTL 的未决状态如实披露。
- [ ] 画面、文档、文件名、元数据无姓名/联系方式/账号/串号/私网地址/凭据。
- [ ] 报名后台登记已完成（用户操作）；GitHub 公开不等于报名提交。

## 6. 拍摄脚本（≤10 步）与旁白要点

**总时长**：约 60–75 秒（可剪）；**现场不重复发射**，每段 RF 动作均计入授权预算。**拍摄顺序依据** [gui_demo README](../../sdr_testbench/gui_demo/README.md) §4 与 [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) §二。

| 步 | 画面（拍什么） | 参考时长 | 旁白要点（中文） | Voice-over (English, short) |
|---:|---|---:|---|---|
| 1 | 板卡整机、五寸屏亮起、进入“总览” | 4 s | 基于 Zynq-7020 与 AD9361 的便携接收分析平台 | Zynq-7020 + AD9361 portable receiver |
| 2 | 手指依次切换七个页面（总览/发射/接收/数据/智能/工具/系统） | 8 s | 本地触摸界面，实时显示运行状态 | On-device touch UI for status and control |
| 3 | GRC 软件链全图（明确 Pluto Sink 为 DISABLED） | 8 s | 发送端软件链：PRBS7 BPSK、RECT、2 倍上采样；Sink 仅参数展示 | Transmit-side flowgraph; sink shown for parameters only |
| 4 | 参数面板：433.9 MHz、6 MSPS、3 MSym/s、60 dB、非 cyclic | 6 s | 外部源参数：同频点、BPSK/RECT、内部 60 dB 衰减 | External source: same LO, BPSK/RECT, 60 dB internal attenuation |
| 5 | 板卡只读状态页/回读：TX 关闭、RX 未武装、服务正常 | 6 s | 发射前先核对：两端 TX 关闭、只读安全态成立 | Pre-check: both TX off, read-only safe state |
| 6 | 接线特写：Pluto TX → 同轴线 → 板卡 RX1；板卡 TX1 空载 | 5 s | 只接外部源；板卡自身发射不启用 | External source only; board TX stays off |
| 7 | 板卡“接收→解调/采样”设置：BPSK、3 MSym/s、RECT、seed 1、4096 点、D1 | 7 s | 接收端选 BPSK/RECT，参考种子与外部源一致 | Receive: BPSK/RECT, matching PRBS seed |
| 8 | 一次限时发射 + 板卡点“捕获快照”，出现当前帧 | 6 s | 一次有限发射，采集一帧当前快照 | One bounded transmit window; one snapshot |
| 9 | 概览/解调/星座页：同步指示、错误/比特、BER、真实接收星座 | 10 s | 本帧参考同步；BER 为本帧参考误码（有限单帧，非统计） | This frame is reference-synced; BER shown for this frame only |
| 10 | 收尾读回：Pluto 衰减/关断、板卡解除武装、屏幕安全态 | 6 s | 发射已关闭、已解除武装，证据已保存 | TX off, disarmed, evidence saved |

**旁白硬约束（拍摄前逐条核对）**

- 必须说“**有限单帧/该配置下**”，不得说“统计 BER/长时间稳定/全项通过”。
- 不得出现：`QAM64`、`EVM`、`真实 RRC 接收`、`自动选档/信道感知`、`S2 108 帧`、`46 dB`、`接口时序通过`、`准确率/可信度`、`实时连续接收`。
- 模型/环境页如入镜，只能说“该帧模型输出/理由，得分未校准”，不得说“识别正确率”。
- 不拍屏幕上的串号、token、私网地址、账号、路径；如无法避免，后期打码或重拍。
- 旁白与字幕用中性事实句；遇到现场异常按 §2.4/§2.5 停止并重拍，不剪辑成“成功”。

**拍摄后检查**：

- [ ] 视频未出现 §4.3 的未完成能力表述；
- [ ] 每段 RF 动作都有对应的停止/回读画面；
- [ ] 证据文件已在本地归档（不入公开仓库）；
- [ ] 视频文件名/元数据已脱敏，交由用户决定提交渠道。

## 7. 待 Lead/用户确认（本文不解决的事项）

1. **本次现场是否具备板卡、Pluto、同轴与授权**；若不具备，演示改为“软件预览 + 已有证据讲解”，不得讲成实时接收（§2.5 F6）。
2. **海报/PPT 文字已就绪**：[POSTER-AND-PPT.md](POSTER-AND-PPT.md) 提供英文海报/PPT 文案与中文讲稿；实际海报/PPT 排版与文件制作由用户/现场完成，提交渠道由用户决定。
3. **演示视频拍摄与提交渠道**由用户决定；本文只提供脚本与检查表，不发布视频。
4. **报名后台登记**、匿名/双盲规则、仓库链接登记由用户在官网/报名后台完成；AI 不代填。
5. 若需在演示中展示“同帧模型/环境结果”，必须先按 [人工验收指南](../rf/2026-10-26-human-acceptance-guide.md) H08/H09 现场核对并接受“未校准/不可当准确率”的表述；否则建议不拍该页。

## 附录：引用与回查

- 演示/验收顺序：[人工验收指南](../rf/2026-10-26-human-acceptance-guide.md)；硬件前置与判据：[boardtest](../rf/boardtest.md)；部署/回退：[试部署 runbook](../rf/deployment-trial-runbook.md)。
- 外部源参数与安全：[BPSK-PARAMETERS](../../sdr_testbench/board_integration/BPSK-PARAMETERS.md)、[WIRING](../../sdr_testbench/board_integration/WIRING.md)、[ATTENUATION](../../sdr_testbench/board_integration/ATTENUATION.md)、[LEVEL-BUDGET](../../sdr_testbench/board_integration/LEVEL-BUDGET.md)、[CAPABILITY](../../sdr_testbench/board_integration/CAPABILITY.md)、[gui_demo README](../../sdr_testbench/gui_demo/README.md)、[SDR 测试台 README](../../sdr_testbench/README.md)。
- 证据与限制：[EVIDENCE-PACK](EVIDENCE-PACK.md)、[SYNTHESIS-METRICS](SYNTHESIS-METRICS.md)、[RF-INTERFACE-TIMING](RF-INTERFACE-TIMING.md)、[DELIVERY-INDEX](DELIVERY-INDEX.md) §4–§5、[README](../../README.md)「已验证能力」「失败与限制」。
- 提交与合规：[COMPETITION-MATERIALS](COMPETITION-MATERIALS.md)、[GUIDE-CONFORMANCE](GUIDE-CONFORMANCE.md)、[FILENAME-CONSULTATION](FILENAME-CONSULTATION.md)、[LICENSES](../../LICENSES/README.md)、[分享与清理](SHARING-AND-CLEANUP.md)。
- 状态词与证据层级：[作品简介追踪](../requirements/brief-traceability.md) §1、[验收清单](../requirements/acceptance-checklists-p1-p6.md)、[开发纪律](../memory/03-development-discipline.md) §5/§8。

> 本文只写“怎么核对、怎么拍、怎么提交”，**不授权也不执行**任何接线、发射、采集或板卡操作。现场动作以操作者授权与只读回读为准；出现异常按 §2.5 停止并保留原始证据。
