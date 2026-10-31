> 历史AWG说明归档（2026-10-30）。以下保持原README正文供查阅；其中“当前”、能力范围与GUI唯一入口等历史说法不代表RF主项目的最新验收，先读根README。

# AC820 任意波形发生器(上位机控制版)

PC 上位机(Python + PySide6)通过 USB 串口控制 AC820 + ACM2108 双通道信号发生器:
波形(正弦/方波/三角/锯齿)、频率(10Hz~1MHz)、幅度(0~100%)、基波+4 路谐波
(次数 2~8)独立可调,支持状态回读与波形预览。板上验证含示波器(DSOX1102A)
USB-TMC 自动闭环回归。

## 作品 RF 主路径与辅助 AWG 路径（2026-10-13 起）

本副本 `final_prj` 是**作品 RF 平台**（Zynq + AD9361）的开发落点；工程内同时保留 AWG（ACM2108 任意波形发生器）辅助能力。两条路径的**位流、发布与验收统计互不混用**。

### RF 主路径（作品主线）

- PL：`rf_overlay/`（AD9361 样点收发与有限采集）。构建 `vivado -mode batch -source rf_overlay/build.tcl`，产物在 `rf_overlay/out/`（派生状态，不入 Git）。
- 板端 C 守护进程：`rf_overlay/sw/board_app/`（`rf_daemon.c` / `rf_core.c` / `rf_main.c`）。板上原生 `make`；开发机 `make host-test`、`make host-test-iq`。
- 板载 Linux + 五寸触摸屏：`pynq_overlay/sw/`（`rf_status_worker.py`、`rf_status_client.py`、`ui_model.py`、`ui_view.py`、`pynq_touch.py`）。发布打包走 `pynq_overlay/sw/merged_release.py` 与 `install.sh`。
- 当前能力状态：**限 BPSK 3 MSym/s、RECT、60 MSPS、4096 点快照**的生成 / 有线回环 / 接收 / 星座 / BER / 环境诊断（采样窗口内）。**EVM、QPSK/QAM 接收、RRC 接收、受约束档位决策尚未实现**。逐句追踪见 [docs/requirements/brief-traceability.md](../../docs/requirements/brief-traceability.md)。
- 状态与证据对象契约：[docs/requirements/rf-status-object-contract.md](../../docs/requirements/rf-status-object-contract.md)；P1–P6 六类门验收清单：[docs/requirements/acceptance-checklists-p1-p6.md](../../docs/requirements/acceptance-checklists-p1-p6.md)。

### 辅助 AWG 路径（下面“架构 / 唯一启动入口 / 功能”描述的是这条）

- `host/main.py` + `host/siggen/`（PySide6 上位机）→ `sdk/src/main.c`（PS 裸机桥）→ `src/rtl/`（PL）→ ACM2108 DAC0/DAC1。
- `host/main.py` 是 **AWG 上位机的唯一入口，不是 RF 产品的唯一入口**；RF 主路径的入口是板载 `pynq_overlay/sw/`（触摸屏 + 守护进程）与 `rf_overlay/`。
- AWG 与 RF 两条路径的测试、发布与证据统计分别记录，不得互相顶替（例如 AWG PL 的数字调制/RRC 成果不能算作 RF PL 的实时处理）。

## 架构

```
host/main.py（唯一入口）
  → host/siggen/（PySide6 GUI + protocol/controller）
  → CH9102F 串口（PS MIO48/49, 115200）
  → sdk/src/main.c（PS 裸机桥；sdk/ws*/ 为生成工作区）
  → AXI GPIO / reg_bridge
      ├→ 标准 AWG：双通道 DDS + harm_synth → 幅度缩放
      └→ 数字调制：PRBS7/USER → BPSK/QPSK/QAM4/QAM16/QAM64 I/Q 映射
  → 输出选择 → 信道损伤（4 抽头多径 + 近似高斯噪声）→ ACM2108 DAC0/DAC1（MS9708, 偏移二进制）
```

## 唯一启动入口

请把工程根目录作为工作目录，使用下面的命令启动上位机；不要从其他 worktree 或旧工程副本启动：

```bash
cd <LOCAL_PATH>
python host/main.py
# 若使用虚拟环境
host/.venv/Scripts/python.exe host/main.py
```

`host/main.py` 是唯一的 GUI 入口；`host/siggen/` 只存放被入口导入的上位机代码，不要直接运行其中的模块。

## 功能

- 波形:正弦/方波/三角/锯齿/脉冲/噪声;频率 10Hz~1MHz(支持 100k/250kHz/0.5M 后缀)
- 脉冲波形占空比 0~100%(默认 50%,GUI 高电平区间对应板上物理高电平);50% 与方波一致
  (ACM2108 模拟链路反相,RTL 在该区间使用内部 `8'h81` 以保持 GUI 与示波器一致)
- 幅度 0~100%;实时 Vpp 显示(校准常数默认 9.50V = 100% 方波 10kHz 实测 Vpp)
- 校准常数默认 9.50V = 100% 方波 10kHz 实测 Vpp(2026-08-14 示波器实测, 方波/正弦一致;
  50%/20% 线性度 ±1%; 三角/锯齿满摆幅 -1~2%; 100kHz 高频衰减约 3%, 频率相关不补偿)
- 示波器测量刻度影响(对比时注意): 垂直刻度应使信号占屏 40~70%——
  占屏过大(如 1V/div 测 6V+ 信号)压缩偏低 0.1~0.3V, 占屏过小(如 2V/div 测
  <3V 信号)量化抬升偏高 0.1~0.3V; 测量本身波动 ±0.05V, 建议多次取平均
- 显示值采用硬件截断模型(与 ampl_scale 整数截断一致, 误差 <1 LSB ≈ 0.075V)
- 校准数据采集: python tools/cal_measure.py COM11
- 谐波:每通道基波(BASE)+ 4 路谐波(次数 2~8, 幅度 0~100%);Σ>100% 自动归一化
- 内调制:每通道 AM/FM/PM/扫频(调制源正弦/方波/三角/锯齿 0.1Hz~50kHz;AM 深度 0~100%、
  FM 频偏 0~500kHz、PM 0~180°、扫频跨度=FMDEV),作用于整个输出(含谐波)
- 双通道相位联动(仅 CH2):CH2 频率跟随 CH1,相位差 0~360.00° 可调;联动时 CH1 的 FM/SWEEP
  路径由 CH2 继承,CH2 自身 AM/PM 被抑制,但波形/幅度/基波/谐波仍独立,静态相位偏移仍生效
- 联动预览以 CH1 为主并叠加 CH2 曲线;相位路径约有一个 50MHz 时钟周期的采样延迟,
  实际相位关系为 phase_ch2[n] = phase_ch1[n-1] + offset,误差约 360 × f_hz / 50e6 度,
  因此不应宣传为任意频率下严格零延迟
- 基础字波形编码为 3 bit(0=SINE,1=SQUARE,2=TRIANGLE,3=SAW,4=PULSE,5=NOISE),占用 GPIO0/1 的 bit[30:28];
  NOISE 使用确定性 8 位最大长度 LFSR,FREQ 控制相位地址驱动的刷新带宽(约 min(256×FREQ,50MHz));
  GPIO11 bit0 为 CH2 联动, bit[8:1]/[16:9] 分别为 CH1/CH2 PULSE DUTY, bit[22:21] 为 RRC 滚降档位
  (00→0.35, 01→0.2, 10→0.5, 11→0.35 保留)
- 数字调制(全局 I/Q 输出):BPSK/QPSK/QAM4/QAM16/QAM64;内部 PRBS7 数据源默认 seed=127，可在 MODE OFF 时设置 1..127 并显式 RESET;
  符号率 10 Sym/s~1 MSym/s 可调,由独立 32 位相位步进驱动;支持 RECT 符号保持和全部数字模式的 RRC 成形
  (4 SPS、rolloff 0.2/0.35/0.5 三档可配置、8 symbols span、33 taps);GUI 选择调制模式后立即下发,选择 OFF
  立即关闭数字调制;符号率、FILTER、ROLLOFF 与 SEED 仅在 OFF 状态可编辑并设置,运行中控件锁定;数据源可选内部 PRBS7 或用户上传 USER(见下条);
  DAC0=I、DAC1=Q。QAM4 与 QPSK
  为相同的四点映射;数字调制开启时旁路标准 AWG 的波形/幅度/谐波/内调制/相位联动路径,
  关闭后恢复标准输出
- 用户数据流:数据源选 USER 后,数字调制器的符号比特来自上传数据——小文件(≤512 B)经 UART
  上传(数字调制页「用户比特流」);大文件(≤1 MiB = `DDR_PP_BANK_BYTES`)经 UDP 上传到 DDR PP
  bank(数字调制页「大文件数据流 (DDR ≤1 MiB)」)。面板维护一个**文件队列**(添加/移除/清空),
  可「手动上传所选」到指定 bank,或勾选**环形模式**在播放中自动把队列下一块喂给**非活动 bank**
  (1 个文件=无缝循环,多个=按序轮流),实现连续播放;状态显示两 bank 的 VALID/字节数/活动 bank/
  STATE/ERROR 与已喂块数。**手动上传需 MODE OFF;播放需先选数字调制模式**(上传的字节被当作符号
  比特,经星座映射/RRC 输出),环形喂数在播放中进行,不受 MODE OFF 限制;两条路径的**元数据**
  (CRC/长度/bank 切换)已验证,模拟 I/Q 输出闭环 `scripts/scope_verify_user.py` 也已**上板 4/4 PASS**
  (QPSK/QAM16 × UART/DDR; 判据 BER=0)
- `GET DIGMOD` 新状态格式为 `DIGMOD MODE <mode> SOURCE <PRBS7|USER> FILTER <RECT|RRC> ROLLOFF <0.2|0.35|0.5> SEED <1..127> SYMRATE <Hz> STATE <RUNNING|STOPPED>`；
  旧版缺少 `FILTER`/`ROLLOFF`/`SEED` 的 9/11/13-token 状态按 RECT/0.35/127 兼容解析。RRC 支持全部数字模式，
  运行中修改 FILTER、ROLLOFF、SEED 或 SYMRATE 返回 `ERR DIGMOD RUNNING`；ROLLOFF 仅 FILTER=RRC 时有意义
- 信道损伤（CH1/CH2 独立，数字调制页「信道损伤」标签页）：静态**多径** + 近似**高斯噪声** +
  **窄带单音** + **周期冲激**（四者相加后过 `sat(±127)`），接在**输出选择
  之后**，因此标准 AWG 与数字调制两条路径都经过它。多径为 4 抽头 FIR
  `y[n] = sat(round(Σ_k g_k·x[n−d_k] / 256), ±127)`：抽头增益为 Q8 有符号 9 bit（`−256..255`，单位增益
  的 256 无法表示，取 255 ≈ 0.996）、延迟 0..511 采样点（50 MHz 下 0~10.22 µs）。噪声 σ = 噪声等级/100 × 127 LSB，
  由确定性 xorshift32 驱动 4 路 8 bit 均匀数求和（CLT 近似）。抽头写入片上抽头表 BRAM 后**需 `SET CHx CHAN COMMIT`
  才生效**（`TAP CLEAR` 只清零抽头表，仍需 COMMIT；要透传请用 `CHAN OFF`）。未使能时逐位透传（bit-exact），
  使能时端到端 3 拍流水（自由运行流，固定相移无害）
- 状态回读(GET)返回归一化值;波形预览(含谐波叠加)与示波器实测一致

## 串口协议(115200 8N1, CR/LF, 大小写不敏感)

```
SET CH1 WAVE SINE | SET CH2 FREQ 100000 | SET CH1 AMPL 80 | GET CH1
SET CH1 WAVE PULSE | SET CH1 DUTY 25 | GET CH1
SET CH1 WAVE NOISE | SET CH1 FREQ 100000 | SET CH1 AMPL 80 | GET CH1
SET CH1 BASE 100 | SET CH1 HARM1 ORDER 3 | SET CH1 HARM1 AMPL 30 | GET CH1
SET CH1 MODE FM | SET CH1 MODWAVE SQUARE | SET CH1 MODFREQ 100 | SET CH1 AMDEPTH 80 | SET CH1 FMDEV 10000 | SET CH1 PMDEV 64
SET CH2 PHASE <0..36000> | SET CH2 PHASELINK ON|OFF
SET DIGMOD MODE QPSK | SET DIGMOD FILTER RRC | SET DIGMOD ROLLOFF 0.2 | SET DIGMOD SEED 5 | SET DIGMOD SYMRATE 100000 | GET DIGMOD
SET DIGMOD RESET
GET DIGMOD -> DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.2 SEED 5 SYMRATE 100000 STATE RUNNING
SET DIGMOD FILTER RECT -> ERR DIGMOD RUNNING
SET DIGMOD ROLLOFF 0.5 -> ERR DIGMOD RUNNING
SET DIGMOD SYMRATE 250000 -> ERR DIGMOD RUNNING
SET DIGMOD SEED 63 -> ERR DIGMOD RUNNING
SET DIGMOD RESET -> ERR DIGMOD RUNNING
SET DIGMOD MODE OFF | SET DIGMOD FILTER RECT | SET DIGMOD SYMRATE 250000
SET CH1 CHAN ON | SET CH1 NOISE 30 | SET CH1 TAP 0 255 0 | SET CH1 TAP 1 64 12 | SET CH1 CHAN COMMIT
SET CH1 CHAN TONE <freq_hz 0..25000000> <ampl 0..100>
SET CH1 CHAN IMPULSE <period 1..65535> <width 1..255> <ampl -100..100>
SET CH2 TAP CLEAR | SET CH2 CHAN OFF | GET CH1 CHAN
GET CH1 CHAN -> CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 30 TAP0 255 0 ...
SET IQIMP ON | SET IQIMP PHASE 4500 | SET IQIMP GAIN 20000 | SET IQIMP PHASEERR 1200 | SET IQIMP FREQOFF 20000 | SET IQIMP COMMIT
GET IQIMP -> IQIMP ON PHASE 4500 FREQOFF 20000 GAIN 20000 PHASEERR 1200
SET IQIMP OFF
调制参数: MODFREQ 为 0.1Hz 单位(1..500000); FMDEV 0..500000Hz; PMDEV 0..511(=1/256 周期)
相位参数: PHASE 为 0.01° 单位(0..36000,36000 为一整周); PHASE/PHASELINK 仅允许 CH2;
          GET 状态包含 DUTY、PHASE 与 PHASELINK。DUTY 仅影响 PULSE 波形,范围 0..100;
          DIGMOD 支持 OFF/BPSK/QPSK/QAM4/QAM16/QAM64，FILTER 支持 RECT/RRC（RRC 支持全部数字模式），
          ROLLOFF 为 0.2|0.35|0.5（默认 0.35，仅 FILTER=RRC 有效，仅 MODE OFF 可改），
          SYMRATE 为 10..1000000 Sym/s，
          SOURCE 为 PRBS7|USER（`SET DIGMOD SOURCE USER` 需已上传用户数据，否则 `ERR USERBUF NOT READY`），STATE 由 MODE 推导。错误响应: ERR <原因>。完整命令集与用例: scripts/test_bridge.py
信道损伤参数: `SET CH1|CH2 TAP <k> <gain> <delay>`，k 为抽头号 0..3、gain 为 Q8 有符号 −256..255、
          delay 为 0..511 采样点；`NOISE` 0..100；`TONE <freq_hz> <ampl>` 为单音干扰
          (freq 0..25000000 Hz、ampl 0..100)；`IMPULSE <period> <width> <ampl>` 为周期冲激干扰
          (period 1..65535 采样点、width 1..255 采样点、ampl −100..100)。单音/冲激与 AWGN **同位置**
          (`fade_fir` 之后、不过信道 FIR)，语义是"接收端叠加的外部信号"。`CHAN ON|OFF` 与 TONE/IMPULSE 的
          **开关位走 GPIO 立即生效**，而抽头表与 TONE/IMPULSE 的**数值走 BRAM、需 `CHAN COMMIT` 才锁存**；
          上述 CHx CHAN/NOISE/TAP/TONE/IMPULSE 命令**无 MODE 限制**，越界返回
          `ERR NOISE OUT OF RANGE` / `ERR TAP OUT OF RANGE` / `ERR TONE OUT OF RANGE` / `ERR IMPULSE OUT OF RANGE`；
          `GET CHx CHAN` 回读 24 token 状态行(旧 17 token 状态行仍可解析，TONE 视为关闭)
复数 I/Q 损伤参数: `SET IQIMP ON|OFF` 使能该级(通道对级，故不挂在 CHx 下)；
          `PHASE` 0.01° 单位(−18000..18000)、`PHASEERR` 0.01°(−18000..18000)、
          `GAIN` Q2.14 原始码(−32768..32767，16384=1.0)、`FREQOFF` Hz(−1000000..1000000；
          硬件可到 ±25MHz，协议按实际用途收窄到 ±1MHz)。三个静态损伤(相位偏移、I/Q 增益失配、
          正交相位误差)在固件内合成为**一个 2×2 矩阵**，与 `host/siggen/channel.py::iq_impair_matrix()`
          逐位一致；`ON|OFF` 开关走 GPIO 立即生效，四个数值写 BRAM 字 8/9/10，**需
          `SET IQIMP COMMIT` 才锁存**(复用 CHAN COMMIT 的 epoch 位，故任一通道的 `CHAN COMMIT`
          同样会锁存它们)；`GET IQIMP` 回读 ON/OFF + 四个参数。该级位于数字调制支路
          (`rrc_active` 之后、DAC 选择之前)，**标准 AWG 路径不经此级**；越界或未知子命令返回
          `ERR IQIMP OUT OF RANGE`
```

## 构建与下载(防旧 bitstream!)

```bash
python scripts/build_bitstream.py   # 检测源变化 -> 完整重建(OOC+主综合+实现+bit)，并导出 HDF/manifest
python scripts/check_bitstream.py   # 下载前校验源哈希与 bit 内容 SHA-256
bash scripts/program_board.sh       # 校验 + JTAG 下载
```

**本工程是 Vivado/Vitis 2025.2 工程，不再使用旧 `cli-anything-vivado`**(其只配到
2018.3，2018.3 无法打开本 `awg.xpr`)。2025.2 工具链调用见工程 CLAUDE.md「工具链」节
与技能 `using-vivado2025-cli`：专用入口为工程根 `vivado2025.cmd`(先 `config check`/`show`)，
下方脚本用 `VIVADO_BIN` 注入真实 Vivado 路径。

首次从干净 checkout 构建时，`build_bitstream.py` 会先由 tracked 的
`scripts/bootstrap_project.tcl` 创建 `xc7z020clg484-2` Vivado 工程；可通过
`VIVADO_BIN` 指定 Vivado 可执行文件路径。manifest 同时记录 Git SHA、工具版本、
bit/HDF 内容哈希和 timing 摘要。

`awg/`、`.Xil/`、`xsim.dir/` 和 `sdk/ws*/` 是生成产物，不是源码入口；SDK 源码入口始终是
`sdk/src/main.c`。发布 manifest 中若记录 HDF/ELF，将同时校验其内容 SHA-256；SDK
工作区仍必须从本次 HDF fresh 生成，不能用旧 workspace 的 ELF 代替。

**⚠️ 必须用 build_bitstream.py 构建**: awg_top 是 BD 内模块, 直接
`vivado2025.cmd synth`/`bitstream generate`(或任何裸综合)不重建 BD、不读回
awg_top RTL —— 改 RTL 后直接综合/下载会拿到旧版逻辑(曾 3 次下载旧 bitstream)。
build_bitstream.py 内部
(rebuild_bitstream.tcl): 先重跑 create_bd.tcl(BD 是派生状态)→ 显式重建 OOC run
(快速失败检查)→ synth_1 注入 PRE 在线综合 awg_top, 并维护 build_manifest.json
(哈希覆盖 src/rtl + create_bd.tcl + ps7_props.tcl + 重建配方, 供下载前校验)。
PS 侧: `vivado2025.cmd app create/build`(sdk/src/main.c)。SDK 工作区是生成副本；从本次 HDF 新建应用后，先执行
`python scripts/patch_vitis_linker.py sdk/ws2025_gem_fresh/awg_bridge`，将 `.text` 放入 OCM、将
`.data/.bss/.heap/.stack` 保留在 DDR，再使用 SDK 自带 ARM GCC 强制重链。板级验证必须由最新
`awg/host_bd.hdf` 全新生成工作区，其 BSP 必须包含 `axi_gpio_6` (GPIO device ID 6)。修改
`sdk/src/main.c` 后需同步到该工作区；旧工作区若沿用旧 HDF 可能仍缺少 GPIO6，不能据此
确认 ELF 已更新。板级验证前必须先执行 `bash scripts/program_board.sh`;若 SDK 工作区
未重新链接/构建或 PS 桥未运行,需先重新链接/构建
(`vivado2025.cmd app build awg_bridge --workspace sdk/ws2025_gem_fresh`)。**⚠ 固件工作区是
`sdk/ws2025_gem_fresh`,不是旧 `sdk/ws2025`** —— 后者的 BSP 不含 lwip,编不过当前固件;
且 `app build` 打印 `ok` 只查 ELF 存在、不看时间戳,构建后必须自己断言产物新鲜。
`deploy jtag` **必须显式给 `--bit`**,否则报 `error: Bitstream path is not recorded`:
`vivado2025.cmd deploy jtag --app awg_bridge --workspace sdk/ws2025_gem_fresh --bit awg/awg.runs/impl_1/host_bd_wrapper.bit`。

GEM0/MDIO 的 PS7 属性发生变化时，必须执行 `python scripts/build_bitstream.py --force` 生成新的 HDF，并从该 HDF 新建 BSP/应用；以太网直连 GEM0，不会向 RTL 或 DMA 数据路径加入 Ethernet IP。

**⚠️ 板上 bitstream 可能落后于源码一个 BD 修订**: `check_bitstream.py` 只比对 manifest 记录的哈希,
发现不了“manifest 哈希对、但磁盘/板上 bit 实际来自更早 `create_bd.tcl`”的情况。实例:磁盘 bit
(2026-09-08, 源 `9818d9f`)缺 `7dbce14`(2026-09-09) 为 `proc_sys_reset` 补的
`rst_clock_locked`/`rst_aux_inactive` 常量——其注释即 "proc_sys_reset holds AXI peripherals in
reset when dcm_locked is left open"——结果 AXI BRAM 写入不生效:USER 数据源在示波器上输出塌成直流、
且与上传内容无关,而 PRBS7 路径完全正常,极易误判成软件问题。鉴别方法:解压
`awg/awg.runs/impl_1/host_bd_wrapper_routed.dcp`(zip),grep 该 BD 修订独有的 cell 名(如
`rst_clock_locked`);缺失即说明板上逻辑来自更早的 BD。**教训:改过 BD/PS7/RTL 后先 `--force` 重建再上板调试。**

UDP DDR PP 板级门禁必须按 M0→M1→M2→M3→M4→M5 顺序执行，主机网卡使用 `192.0.2.10/24`，板卡使用 `192.0.2.10/24`、UDP `40100`：

```bash
# 仅验证 ARP、UART 网络状态和 UDP ECHO；失败时不得发送 DDR 事务
python scripts/udp_ddr_pp_gate.py m0 --host 192.0.2.10 --port 40100 --serial COM11
# 64 KiB 完整性、乱序/重复/缺包 STATUS、坏 CRC/越界/ABORT/超时
python scripts/udp_ddr_pp_gate.py m1 --host 192.0.2.10 --port 40100 --serial COM11 --bank 0
# 1 MiB 上传速率与设备 CRC；有效载荷速率须达到 3 MiB/s
python scripts/udp_ddr_pp_gate.py m2 --host 192.0.2.10 --port 40100 --bank 1
# 双 bank 各 1 MiB 经 UDP 上传/校验后,用既有 PP 控制平面播放并观察 bank0→bank1→IDLE
python scripts/udp_ddr_pp_gate.py m3 --host 192.0.2.10 --port 40100 --serial COM11
# 播放中经 UDP 重装非活动 bank（环形，切换 K≥3 次），断言 SWITCH ORDER 1,0,1、切换后 LENGTH/CRC、UNDERFLOW 0
python scripts/udp_ddr_pp_gate.py m4 --host 192.0.2.10 --port 40100 --serial COM11
# 同上但由上位机 GUI 的 RingFeeder + 面板状态解析器驱动喂数（回归保护，见下）
python scripts/udp_ddr_pp_gate.py m5 --host 192.0.2.10 --port 40100 --serial COM11
```

每个门禁只有在全部断言通过后才打印 `M0 PASS`、`M1 PASS`、`M2 PASS`、`M3 PASS`、`M4 PASS` 或 `M5 PASS`；M1 的串口快照只比较非目标 bank 的 VALID/LENGTH/CRC，M2 不计 UDP 头部且不平均重试时间。任何门禁失败都应停在当前阶段，先诊断链路、会话、缓存或 DMA 状态，再继续后续阶段。

M3 先在 UDP 上把两个 bank 各写满 1 MiB（校验 COMMIT 回包 CRC32 与 `GET DDR PP` 的 LENGTH/CRC16 元数据），再**只经 UART 控制平面**下发 `SET DIGMOD FILTER/ROLLOFF/SYMRATE/MODE` 与 `SET DDR PP START`，轮询 `GET DDR PP` 直到观察到 `ACTIVE_BANK 0 → 1` 后回到 `STATE IDLE`、`ERROR 00`；播放中若出现非零 ERROR 或超时，M3 只发一次 `SET DDR PP STOP` 并失败。M3 **不允许**把 payload 经 UART 分块回传，也**不新增** UDP 播放启动消息。`GET DDR PP` 的 wire 格式不变。

M4 验证播放期间的环形重装：**播放中仅非活动 bank 可写**——主机在 `ACTIVE_BANK=X` 时经 UDP 向 `Y=1-X` 上传并 `COMMIT`（`COMMIT` 置 `fresh[Y]`），固件在 X 排空（DRAIN）后当且仅当 `fresh[Y]` 为真才切到 Y，可重复切换形成环形播放。M4 断言 `SWITCH ORDER 1,0,1`（切换序列交替，即 `ACTIVE_BANK 0→1→0→1`）、每次切换后刚上传 bank 的 `LENGTH/CRC16` 等于该轮内容、收尾 `STATE IDLE`/`ERROR 00`；收尾后经 `SET DMA BENCH` 回读断言 `PP0_UNDERFLOW 0 PP1_UNDERFLOW 0` 并打印 `M4 NEXT_SUBMIT_TICKS <n>`（≈3.15 ms 切换间隙由 AXIS FIFO 吸收的板上实测证据）。切换时若下一块未就绪（非活动 bank 已有 UDP `BEGIN` 但未 `COMMIT`，即 staging 进行中），固件报 `ERROR 26`（ring underrun）并停止播放；随后 `SET DDR PP STOP` 复位回到 `STATE IDLE`/`ERROR 00`，主机可再 `ABORT` 在途会话。UART 仍只做控制/恢复（`START/STOP/CLEAR/CHUNK/COMMIT` 只在停播态 staging，播放期间不能经 UART 写入 bank）；本阶段**不新增 UDP 消息**、`GET DDR PP` wire 格式与字段名不变。

M5 是 M4 的上位机侧回归保护：不再用门禁自己的循环决定喂哪一块，而是用**真实 GUI 的** `host/siggen/ring_feeder.py::RingFeeder` 决策、并用面板同款 `protocol.parse_ddr_pp_status` 解析 `GET DDR PP`，因此能锁住 GUI 的“活动/下一 bank、非活动 bank 才可写”判断不随固件行为漂移。断言与 M4 相同（切换交替、切换后刚提交 bank 的 `LENGTH/CRC16` 匹配、`PP0/PP1_UNDERFLOW 0`）。纯逻辑单测在 `host/tests/test_udp_ddr_pp_gate.py` 用 `FakeUart`/`FakeUdpClient` 覆盖（无需板卡）。

> 链式执行门禁时注意：`SET DDR PP CLEAR`（timeline 门禁在每个 profile 前都会发）会把两 bank 的 `stage_owner` 置回 UART，此后 UDP `BEGIN` 会被拒为 `BUSY`。因此在 timeline 门禁之后直接跑 M4 等 UDP 上传门禁前，需先经 UART `CHUNK/COMMIT` 重新占有两个 bank（或重新部署）再发 UDP 上传。

> 已修复（2026-09-09）：此前疑似 lwIP `pbuf_realloc` 自环挂死，实为本工程固件缺陷——`SET DMA BENCH` 的 1 MiB 缓冲原落在 `DDR_BASEADDR+DDR_RESERVED_BYTES+0x00100000`（0x300000），与链接器放进 `.bss` 的 lwIP `rx_pbufs_storage` 指针表重叠；bench 覆盖该表后，下一次 GEM RX 中断用野指针调用 `pbuf_realloc` 挂死。修复：`DMA_BUFFER_ADDR` 移到 +0x02000000，DDR 诊断基址 `DDR_DIAG_BASE` 移到 +0x03000000（均远高于 `__bss_end`），并在 `host/tests/test_stage4_m3_sdk.py` 增加偏移不变量测试。`ddr_pp_timeline_gate.py --profile all` 双 profile（各含 `SET DMA BENCH`）现已 PASS。

## 测试与验证

```bash
# RTL 仿真(mod_synth_tb 31 项, am_stage_tb 7 项, dds_core_tb 17 段(4 基础 + 5 PULSE + NOISE + 7 PM), harm_synth_tb 15 项,
# reg_bridge_tb 79 项, awg_top_tb 114 项, ampl_scale_tb 12 项, prbs7_source_tb 30 项,
#   信道损伤 fade_fir_tb 13 项 / noise_gen_tb 17 项 / chan_impair_tb 13 项 / chan_cfg_tb 32 项,
#   干扰源 tone_gen_tb 48 项 / impulse_gen_tb 8 项,
#   iq/rrc/mapper/fade_fir 走 Python GOLD — xvlog/xelab/xsim)
# 计数口径: 逐项 TB 的项数 = xsim -runall | grep -c '^PASS ', 含结尾聚合摘要 PASS <tb名>;
#   tone_gen_tb / impulse_gen_tb 只打结论行(失败才打 FAIL), 项数须从源码数比较点(48 / 8), 直接 grep 会数成 1
# 协议回归(脚本含 195 条命令断言, 需板卡在线)
python scripts/test_bridge.py COM11
# 示波器闭环(61 项: 校准 + 8 波形用例 + PULSE/NOISE + 谐波 FFT + 5 调制 + 5 相位联动 + 6 数字调制 +
#   4 信道损伤(两点多径 |H(f)| 梳状零点 / AWGN 噪底线性 / 窄带单音谱线 / 周期冲激计数) +
#   3 复数 I/Q 损伤(静态相位旋转角 / 增益-正交失配统计量 / 频偏旋转速率) +
#   RRC BER(QPSK 3 速率、QAM16/QAM64 100kSym/s、BPSK/QAM4 100kSym/s、QAM16/QAM64×α0.2/α0.5 共 13 组) + 各 OFF→RECT 恢复 + 基准恢复, 需板卡+DSOX1102A USB-TMC)
python scripts/scope_verify.py COM11
# USER/DDR 数据路模拟 I/Q 闭环(QPSK/QAM16 @100kSym/s, 上传非周期 payload 对齐求 BER; 上板 4/4 PASS)
# 判据 BER=0: 对齐同时搜符号相位与小数采样相位, 多电平星座按星座轴平均幅度归一化(勿用全局峰值)
python scripts/scope_verify_user.py COM11
# 同上但仅跑参考链自检(合成捕获, 无需板卡/示波器)
python scripts/scope_verify_user.py --selftest
# 上位机单元测试(706 项)
host/.venv/Scripts/python -m pytest host/tests/
```

## 工程结构

- `host/main.py` — 唯一的 PySide6 GUI 启动入口
- `host/siggen/` — GUI、控制器、协议和预览实现；不要直接运行其中模块
- `host/tests/` — 上位机离屏和协议单元测试
- `src/rtl/` — PL 源（`awg_top`/`reg_bridge`/DDS/调制/数字调制/信道损伤模块）
- `src/tb/` — RTL 测试台
- `scripts/` — BD 生成、协议/示波器测试、构建/校验/下载
- `sdk/src/main.c` — PS 裸机桥源码唯一入口
- `sdk/ws*/` — SDK 生成工作区，不是源码；由最新 HDF/BSP 重新生成
- `tools/` — 完整重建、网表追踪、谐波变体模拟调试工具
- `awg/`、`.Xil/`、`xsim.dir/` — Vivado/XSIM 生成产物
- `CLAUDE.md` — 工程协作和工具链说明

## 调试记录(重要)

- ACM2108 DAC 期望偏移二进制(0..255,128=中点);补码直出导致正弦/三角 V 形错位、
  方波归零。修复: dac_driver 内 sample ^ 8'h80。
- PS 无 DDR: 程序跑 OCM(资料包附录1 无DDR 例程方案);lscript 默认链 DDR 会失败。
- BD 顶层用 wrapper(awg_top 作为 BD 内模块),避免未约束顶层端口 UCIO-1。
- 谐波相位(2026-08-13): 独立累加器启动相位随机 -> 改为 ph_k=acc0×ord_k 锁相派生;
  锁相版实现后偶次谐波反相 180°(仿真不可见) -> 谐波地址按 ord 奇偶条件 MSB 翻转
  (仅偶次 ^0x80, 全量翻转会破坏奇次)。实测 ord 2..7 全部同相, 复合叠加与 GUI 一致。
- 示波器要点: pyvisa 必须默认后端(visa32.dll); 先 import pyvisa 再 import pyqtgraph;
  自动刻度收敛慢需手动固定时基/刻度(scope_verify 已处理)。闭环还会固定 CHAN1/2 的探头倍率为 1x、偏置为 0;
  否则示波器残留的 CH2 10x 配置会使波形饱和,相位提取失效。PULSE RTL 已将 GUI 高电平区间映射为
  板上物理高电平,不要用全局 DAC 极性转换修复该单一波形。
- 示波器数字调制验收: 联合 I/Q 聚类标签估计符号周期；`symbol_period_from_iq_labels()` 先过滤短于典型稳定运行一半的边沿过渡，再取稳定运行低分位，避免 BPSK 连续相同比特被误判为 2T。1 MSym/s 的模拟边沿使少量标签点落在过渡区，数字 I/Q inlier 阈值按速率设为 0.95；10 k/100 kSym/s 仍为 0.99。该阈值只补偿采集边沿，不放宽星座或符号率判定。
- 板级脚本启动时先等待桥欢迎语并完成一次 `SET DIGMOD MODE OFF` 握手；串口读取保留半行和同一块数据中的后续完整行，避免 SDK 启动期间欢迎语、部分 `OK` 和旧响应错位。
- 板级调试方法论: 网表仿真 → 引脚核对 → CSV 分析 → 参考设计(见 ac820-board 技能)。

## 当前进度：PYNQ status + AD9361 集成（2026-09-27）

- 方向已定：单一合并 Overlay + C daemon sidecar；先接入状态读取，再分阶段开放控制。
- 本阶段 Codex 管理的外部工作树：`<LOCAL_PATH>
- RF 实测进度：Task 10 Steps 1–5 已记录 PASS；Task 11 物理 RX 正负控和 CHAN1 Vpp 校准已运行，但正式 RX 数值门限结论仍为 **NOT RUN**。板卡上次记录为 `pynq-status active`、`rf-daemon inactive`，RX1 支路断开。
- 接手顺序、两个来源工作树的 HEAD/脏状态和边界见 [CLAUDE.md](../../CLAUDE.md)「PYNQ status + AD9361 集成接手记录」；Task 11 测量细节见 RF 来源工作树 `docs/rf/boardtest.md` §13。
