# 主树 RF 构建、TX1 与回环验收（2026-10-01）

主树 ARM daemon 已构建并部署，数字回环、功分器物理回环和界面点击到实际 TX1 输出均获得通过证据。**通过条件是 tap=29、40 dB 内部发射衰减、基带峰值码 150**；不能把这一结论扩大到原 tap=7/15、峰值码 1500、其他频率或全部调制方式。真人触摸与触摸时延尚需手动验收。

当前发布：`56d0d0d4bd36d4e22d1c030926917a16`。`pynq-status` 是唯一 Overlay 所有者，`rf-daemon` 为受其约束的 sidecar。最终只读回读确认两个服务运行、RF OFFLINE/core=false/armed=false、TX source=false、RX arm=false；AWG 两通道仍回读 SINE/1000 Hz/50% duty。

## 构建与修复

- 补入主树 `rf_core/rf_daemon/rf_daemon_main/rf_protocol/rf_state/rf_iq/rf_pl_status_reader`、对应测试和 Makefile 目标。保留主树完整 `rf_main.c` CLI；来源的七行 CLI 调用不存在的函数，不能照搬。
- 修复来源构建辅助脚本把 daemon 复制到自身的问题，以及 Windows 上远端 POSIX 路径、连接超时和输入包遗漏问题。最终构建前 **45/45 个输入文件哈希匹配**；保存完整输入 SHA256 和 ARM ELF SHA256。
- 补齐真正的 vendor 完整性校验：10 个 vendor 文件原始字节匹配 SHA256SUMS。未改 vendor。
- 将布尔 IDELAY enable 映射为七路 `0x7F`，而非只启用 lane 0 的 `1`；增加真实寄存器模型回读回归。
- 实测数字采集超时暴露原 `FAULT` 清理缺口：常规 setter 在 FAULT 拒绝操作，停止/解除后 source 可仍为 true。增加拥有核心的 `rf_core_safe_shutdown()`，在释放映射前清 source、RX arm 并复位器件。新增失败回归先红后绿；最终 tap 扫描中每次失败后都验证 source=false、RX arm=false。
- 物理 `capture` 原先未与 `start` 一样核验接线和衰减。现在同样要求 `cable_confirmed=true`、`attenuation_mdB=40000`，并实际设置衰减后再选择 source；缺参数和未确认接线的无写入回归已通过。
- 主树 `scripts/merged_release.py` 仅转发唯一实现 `pynq_overlay/sw/merged_release.py`。打包复制并校验完整 UI 软件闭包，安装前仍执行 `--require-software --check-panel`。
- 修正 AWG 构建脚本默认 Vivado 为本机 2025.2 路径。本轮**未重新综合 PL**，复用已校验 bit/HWH 的 build_id `492970ec8a404da096c5abe167b28098`。收据明确区分 daemon 重建和硬件未重建。

最终 ARM 原生 `make -j1 all host-test host-test-core host-test-iq host-test-protocol vendor-integrity` 返回 BUILD_RC=0。独立测试汇总包括 state 64、daemon 230、startup 72、platform 39、core 50、IQ 40、protocol 58 项通过；selftest 同时包含有意注入错误的负例，日志中的嵌套 RESULT FAIL 是这些负例的预期输出。编译仍有测试夹具的未使用函数、缺省初始化和截断提示，均保存在完整日志中。

Python 最终回归：**717 passed in 28.90s**。数字和模拟回环分析另有拒绝纯噪声、错误频率、削顶及不完整采集的回归。

## 实板证据与边界

用户先确认 TX1 仅接测量仪，随后确认 TX1 经功分器同时接测量仪和 RX1。器件“插入损耗优于 6.2 dB”不是固定衰减校准值，未拿它推算绝对 RF 功率。

| 项目 | 实际条件 | 结果 |
|---|---|---|
| 初始 TX1 测量 | LO 80 MHz、Fs 60 MSPS、1024 点/32 周期、峰值码1500、尚未接 RX | 三次 ON 与前后 OFF 通过；78.112/81.888 MHz，无仪器削顶 |
| 数字回环初试 | tap 7 / 15 | 失败；tap7 最优误差160 LSB，tap15超时；保留失败与清理记录 |
| 延迟扫描 | tap 0..31，确定性非对称 IQ 和关闭负对照，内部40 dB | 单次有效 tap 为2、6、8、29、30；这是候选点，未宣称整段可靠窗口 |
| 数字回环完整复验 | tap29、倍率1/2/4/8/16各两次 | 10次精确匹配，零 LSB 残差；关闭负对照通过。映射为 I/Q 互换 `+q,+i`；高抽取倍率的多个精确时移不强行报告唯一时移 |
| 功分器模拟回环第一次 | tap29、内部40 dB、峰值码1500 | 频谱成立，但5891个 I/Q 分量落在 ADC 端点，判失败 |
| 功分器模拟回环复验 | tap29、内部40 dB、峰值码150 | 三帧 ±1.875 MHz，OFF 前后对照；最小局部突出49.5 dB、最小ON/OFF提升41.0 dB、零削顶，通过 |
| 最终发布界面→TX1 | 同上，DSO-X1102A CH1、AC耦合、5 mV/div | 三次 ON 的78.112/81.888 MHz；频差13 kHz，最小局部突出31.1 dB、ON/OFF提升23.7 dB，零削顶，通过 |

TX1 仪器门限事先固定为频差≤50 kHz、局部突出≥14 dB、ON比前后OFF较大者≥10 dB、三帧、零削顶，采集前后均确认 TX_ACTIVE/source_selected。模拟回环门限固定为频差≤30 kHz、局部突出≥20 dB、ON/OFF≥10 dB、三帧、零削顶。没有降低门限；降低基带峰值码是消除过载的新工作条件，原过载组仍保留 FAIL。

小量程 DC 耦合的仪器复测曾出现全记录落在 ADC 下限，判失败并保留。调整仪器为 AC 耦合和5 mV/div后重采通过。数据没有用于绝对功率标定、IQ失衡标定、EVM、BER或天线传播结论。

交互验证使用**已安装发布的 UiView 真正命中/按下/抬起、数字键盘、确认按钮→模型→生产回调→worker→client→真实 daemon**，没有直接改模型参数代替按键，也没有调用第二个 Overlay。绘图使用离屏 framebuffer；它证明控制链和命中逻辑，不证明真人 GT911 手指触摸时延。真实板屏最终 framebuffer 另行只读保存，完整导航和底栏正常。

当前 RX 页面真实采集按钮仍禁用；模拟回环采集通过 daemon 验收工具完成，未冒充从 RX 页点击采集。需要后续接通 RX 面板的数据和分析呈现接口；本轮只恢复并验收已授权的构建、发射和回环链。

## 人工验证清单

在工程根目录进行；GUI 唯一入口是 `python host/main.py`，板屏运行当前 PYNQ 面板。先保留功分器接法，确保发射处于停止/解除。不要在服务持有 PL 时运行另一个 `Overlay()` 或旧的直接 `board_app` 硬件脚本。

| 操作 | 预期与记录 |
|---|---|
| 手指依次点总览、发射、接收、数据、智能、工具、系统 | 页面内容切换，导航和底栏保留；记录漏点、误命中、卡住和异常重启 |
| 发射页选择 AD9361→RF参数；打开字段键盘，取消一次，再确认一次 | 取消保留原草稿；确认只修改草稿，尚不发射；单位转换后核对下表的实际值 |
| 布防提示中点取消 | core仍false、armed仍false，不出现谱线；再点布防并确认才获得控制 |
| 初始化、配置、RF发射页加载 | 各动作应明确成功/失败；缺必填字段应显示原因；加载完成仍不发射 |
| 点发射后取消确认，再点发射并确认 | 前一次不发射；后一次显示正在发射、TX_ACTIVE，仪器出现78.125/81.875 MHz附近谱线 |
| 发射时点底栏“停止 AD9361”，随后点解除 | 停止不再确认；谱线回到 OFF；解除后core=false/armed=false，不能自动重启 |
| 已停止时 ACM2108/AD9361 来回切换 | 草稿按设备保存；切换本身不调用RF发射，也不重写AWG草稿 |
| 30秒静默超时复验 | 在40 dB/峰值150条件发射，不再执行控制按钮；只读状态刷新不延长控制租期。约30秒应停机，随后至少再等5秒，不应自动重启。该项本轮软件时钟测试已覆盖，实体计时请记录视频 |
| 真人触摸响应 | 连续至少30次不同位置点击，记录手指抬起到画面响应；不把网络API耗时当作GT911触摸时延。保留视频和遗漏次数，供后续P95统计 |

人工复验参数：LO=`80000000 Hz`（80 MHz），采样率=`60000000 Hz`，RX/TX带宽=`30000000 Hz`，内部衰减=`40000 mdB`（40 dB），PHY 1R1T标志=`0`（2R2T），IDELAY enable=`1`，tap=`29`，正弦每帧周期=`32`，峰值码=`150`。若输入界面显示 MHz/dB，输入对应单位并核对实际值；不要把40000当成dB。原峰值码1500在当前回环中过载，不能沿用其通过结论。

RX 页尚无法完成真人点采集，可在当前已确认接线下使用本轮可复验工具（始终内部40 dB，结束自动解除并检查TX/RX清零）：

```powershell
python scripts/rf_loopback_acceptance.py --expected-release 56d0d0d4bd36d4e22d1c030926917a16 --tap 29 --amplitude 150 --cable-confirmed
```

默认在新建 `evidence/manual-loopback-时间/` 保存原始 IQ、远端日志和 analysis.json。通过须为`passed=true`且远端退出码0；TIMEOUT、削顶、不完整采集或ON/OFF失败均保留 FAIL。工具已包含发射关闭的前后两帧负对照；`--cable-confirmed`只用于实际已确认的回环接线。遇到无法停止或 source仍true，先停止 sidecar与owner，再由唯一owner重启恢复，保留日志后排查。

## 复现与证据索引

主树构建命令：

```powershell
python scripts/rf_build_daemon.py --out rf_overlay/out/rf_daemon
```

复用已核验 PL 的 daemon/UI 打包命令（parent必须为本机可读的完整已验证发布）：

```powershell
python scripts/rf_repackage.py --parent <verified-release-directory> --daemon rf_overlay/out/rf_daemon --output <new-output-directory>
```

证据位于 `evidence/2026-10-01-rf-build-hmi/`：

- `rf_daemon_safe.build.json/.log`：最终输入清单、ARM构建与测试；`package-safe/`：最终完整发布及收据。
- `deploy-safe.log`：安装/完整panel验证；`final-safe-board.json/.log`：最后实板身份、AWG回读和RF停止状态。
- `calibration.json`、`digital-full-tap29.json`：延迟扫描与精确数字回环。
- `physical-tap29-amp150/raw-iq.json`、`analysis.json`、`physical-loopback.png`：通过的模拟回环。
- `cabled-hmi-tx-ac/acquisition.json`、`analysis.json`、各帧npz/PNG、`hmi-start.png`、`tx1-spectrum.png`：最终软件点击和仪器证据。
- `digital-loopback-tap7.json`、`digital-loopback-tap15.json`、`physical-tap29-retry/`、`cabled-hmi-tx/`：初试失败、过载和仪器DC量程失败记录。
- `fault-cleanup-red/green.log`、`physical-gate-red/green.log`、`pytest-delivery.log`：问题复现、修复回归和717项最终测试。

板端系统时钟仍为2021年，主机/仪器为2026年，日志时间不混为一条绝对时间线；采集使用单调时钟、递增请求ID、实际发布身份和原始文件对应关系核对。
