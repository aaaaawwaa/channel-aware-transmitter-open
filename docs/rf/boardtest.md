# RF overlay 上板测试流程（L1 控制面 + L2/L3）

本文件是 `rf_overlay/` 的上板操作手册。**判据在测量之前写死**（`backend_contract.md`
§5.3 的报告纪律）—— 事后再定判据等于没有判据。凡未实测的项一律标 **NOT RUN**，
不写"应该通过"。

**范围**：三个阶段的步骤都在本文件里，判据与器材各自冻结（实测记录见
`docs/rf/l2_stage1_record.md`）：

* **§2.1–§2.4 —— L1 控制面**：离屏自检、装 overlay 并核对 FCLK1、控制面探活、
  最小初始化 + 逐寄存器回读。只走 SPI。
* **§2.5–§2.8 —— L2**：板上原生的 C 传输层自检、PL 配置 + 否定判据、初始化 + trace、
  PL 状态转正。
* **§2.9–§2.10 —— L3**：示波器 B/C 层判据（RF 口上的实测）与寄存器回读审计。

**仍然不在**本文件判据内的：RX 数据面、I/Q 顺序与镜像抑制、RF 性能指标 ——
见 §6（L1 那一轮的诚实边界，**保留**）与 §8（L2/L3 之后仍然不证明什么）。

> ⚠ **L1 用 Python 脚本走 mmap，L2 起换用板上编译的 C 程序 `board_app`**
> （源码 `rf_overlay/sw/board_app/`）。两条路都要求独占 PL/SPI，**不能并发跑**；
> 跑之前先停 `pynq-status`（见 §2.5 前置）。

---

## 0. 先决条件（缺一项就不要开始）

| # | 条件 | 为什么 | 怎么确认 |
|---|------|--------|---------|
| 0.1 | **P18 在 2.5V 档** | 3.3V 档超 AD9361 `VDD_INTERFACE` 绝对最大 2.8V，**会损坏模块** | 目视跳线；`compatibility.md` §0 |
| 0.2 | FMC 模块插紧、卡扣到位 | 虚接的表现与"器件没应答"完全一样 | 目视 |
| 0.3 | 板子跑 PYNQ 镜像，`/dev/mem` 可写 | 脚本走 mmap | `ls -l /dev/mem` |
| 0.4 | `rf_overlay.bit` + `.hwh` 已拷到板上 | 两个文件必须**同一次构建**产出 | 比对 `rf_overlay/out/` 时间戳 |
| 0.5 | **FCLK1 = 200 MHz**（BD 默认即是，**不要去设**） | `IDELAYCTRL` 要求 200MHz±10MHz 参考时钟 | 读 SLCR 分频器，见 §2.2 |

> ⚠ **不要用 `Clocks.fclk1_mhz = 200` 去"设置"FCLK1 —— 那会把正确的 200 MHz 改成
> 142.857 MHz。** BD 已经把 FCLK1 设成 200 MHz（`FCLK1_PERIPHERAL_DIVISOR0=5`、
> `DIVISOR1=1`，IO PLL 1000 MHz），`Overlay()` **不会**改这些分频器，所以开机即是正确的
> 200 MHz，**无需也不能再设**。
>
> 陷阱在于 PYNQ 的 `_ClocksZynq.DEFAULT_SRC_CLK_MHZ = 50.0` 是按 **ZedBoard 的 50 MHz
> PS_CLK** 写死的，而本板 PS_CLK 是 **33.333 MHz**（hwh：`IOPLL_CTRL_FBDIV=30`、
> `IO_IO_PLL_FREQMHZ=1000`）。于是 PYNQ 把 IO PLL 误算成 `50×30 = 1500 MHz`，**所有读数
> 与设置都被放大 1.5 倍**：真 50 MHz 的 FCLK0 被读成 75 MHz，真 200 MHz 的 FCLK1 被读成
> 300 MHz；而"设成 200"会让它去选 `d0·d1 = 1500/200 = 7`，实测落到 **142.857 MHz**，
> **跌破 IDELAYCTRL 的 200±10 MHz 规格**（`phy_rx`/`phy_tx` 的 IDELAY 行为随之未定义）。
>
> 判据用 **SLCR 寄存器**读，不用 PYNQ 的换算值 —— 见 §2.2。

---

## 1. 从 `.hwh` 取地址（不要猜）

两个地址都写在 overlay 的 `.hwh` 里。**猜错的后果是静默的**：读未映射寄存器返回
`0xDEADBEEF` 而不是报错，于是"器件没应答"和"地址写错"长得一模一样。

```bash
# 在板子上
python3 - <<'EOF'
import json, re
h = open('/home/xilinx/overlays/rf/rf_overlay.hwh').read()
for m in re.finditer(r'MODULE.*?FULLNAME="([^"]+)"', h, re.S):
    print(m.group(1))
EOF
```

或用 PYNQ 的 API 直接问：

```python
from pynq import Overlay
ol = Overlay('/home/xilinx/overlays/rf/rf_overlay.bit')
print(hex(ol.ip_dict['pl_spi_0']['phys_addr']))      # 期望 0x40001000
print(hex(ol.ip_dict['axi_gpio_ctrl']['phys_addr'])) # 期望 0x41200000
```

**本构建实测地址**（2026-09-17，`rf_overlay/out/rf_overlay.hwh`）：

| IP | base | high |
|----|------|------|
| `pl_spi_0` | `0x40001000` | `0x40001FFF` |
| `axi_gpio_ctrl` | `0x41200000` | `0x4120FFFF` |
| `axi_ctrl_0` | `0x40000000` | `0x40000FFF` |

地址**会随 BD 变化**。每次重建 overlay 后重新取，不要沿用本表。

---

## 2. 测试步骤

### 2.1 离屏自检（无板、无 `/dev/mem`、无需 root）

**先跑这个。** 它在开发机和板上都应通过；不通过说明脚本本身坏了，
此时上板测出的任何结论都不可信。

```bash
cd rf_overlay/sw
python3 spi_probe.py --selftest      # 期望 === selftest ALL PASS ===（19 项）
python3 ad9361_init.py --selftest    # 期望 === selftest ALL PASS ===（23 项）
```

自检覆盖的是**真实代码路径**：`FakeMmio` 按 RTL 的寄存器语义解指令字
（`{RW[15], CNT[14:12], ADDR[9:0]}`），所以 `SpiMaster.transact` 的指令编码、
TXD0/TXD1 的字节打包、RXD0/RXD1 的字节解包都对着一个**独立的 RTL 模型**校验，
而不是自己验自己。

### 2.2 装 overlay 并**核对** FCLK1（不要设置它）

```bash
sudo env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 - <<'EOF'
from pynq import Overlay
import mmap, os, struct

ol = Overlay('/home/xilinx/overlays/rf/rf_overlay.bit')
# 注意: 这里刻意不碰 Clocks.fclk1_mhz —— 见 §0.5, 设它反而会把 200 MHz 改坏。

fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 4096, offset=0xF8000000)     # SLCR
def r(o): return struct.unpack_from('<I', m, o)[0]
for name, off in (('FCLK0', 0x170), ('FCLK1', 0x180)):
    v = r(off)
    d0, d1 = (v >> 8) & 0x3F, (v >> 20) & 0x3F
    print('%s div0=%d div1=%d -> %.3f MHz' % (name, d0, d1, 1000.0 / (d0 * d1)))
EOF
```

**期望 `FCLK0 = 50.000 MHz`、`FCLK1 = 200.000 MHz`**（IO PLL 1000 MHz ÷ (5×4) 与 ÷ (5×1)）。
本构建实测（2026-09-17）：`div0=5 div1=4 -> 50.000` / `div0=5 div1=1 -> 200.000`，PASS。

若这里读到 75/300（或其它 1.5 倍的值），那是**读法**错了 —— 用了 PYNQ 的
`Clocks.fclkX_mhz`，不是分频器真的错。分频器是唯一真源。

### 2.3 L1-a：控制面探活

```bash
sudo env PYTHONPATH=/home/xilinx python3 spi_probe.py \
    --spi-base 0x40001000 --gpio-base 0x41200000
```

`spi_probe.py` 做的事：模式引脚置已知态 → **拉低 resetb** → 释放 → 轮询 `PRODUCT_ID`
最多 2 秒 → 回环 `REG_CTRL`（`0x3DF`，写 `0x01`/`0x00`，比对 bit 0）。

**顺序是有讲究的**：AD9361 在复位期间**忽略 SPI**，跳过复位释放会看到
"SPI master 是死的"这一模一样的现象。

**回环目标为什么是 `REG_CTRL` 而不是"scratch"寄存器**：本器件**没有** scratch 寄存器。
`0x3F5` 是 `REG_OBSERVE_CONFIG`（厂商头文件），驱动只往里写它刚读出的值，不是安全的
测试目标 —— 初版拿它当 scratch，结果在板上误判成"写路径坏了"。`REG_CTRL` 的 bit 0
是 `CTRL_ENABLE`，厂商直接写它（`ad9361.c:5297`），两个值都是器件会保持的合法值。

### 2.4 L1-b：最小初始化 + 逐寄存器回读

```bash
sudo env PYTHONPATH=/home/xilinx python3 ad9361_init.py \
    --spi-base 0x40001000 --gpio-base 0x41200000
```

写 14 个寄存器（顺序照厂商 `ad9361_setup` 的开头 + `ad9361_pp_port_setup`），
每个写完立即回读并比对。

---

### 2.5 L2-a：C 传输层自检（Step 0，**必须在任何厂商驱动代码之前**）

L2 起改用板上原生的 C 程序 `board_app`（板上原生 `gcc` 编；`Makefile` 另带
`make host-test`，在开发机上对着 PL 与 AD9361 的 C 模型跑同一套自检）。部署 + 编译：

```bash
# helper 的 put 是**单文件** sftp.put —— 传目录会直接报错, 所以先打包成一个文件。
tar czf board_app_src.tgz -C rf_overlay/sw board_app

# helper 路径用 $USERPROFILE 展开成 C:\... , **不要写 ~/.claude/...**
# (那个形式会被 Git-Bash 改写成带盘符的怪路径, 见 §2.10 同族的坑)。
# 远端路径用**相对**形式: SFTP 的 cwd 就是 /home/xilinx, 于是连 MSYS 的路径
# 转换一起绕开了 —— 非要用绝对远端路径就必须加 MSYS_NO_PATHCONV=1。
PYSSH="$USERPROFILE/.claude/skills/ac820-pynq/scripts/pynq_ssh.py"

python "$PYSSH" put board_app_src.tgz board_app_src.tgz
python "$PYSSH" run \
    "rm -rf /home/xilinx/board_app /home/xilinx/board_app_src && \
     tar xzf /home/xilinx/board_app_src.tgz -C /home/xilinx && \
     mv /home/xilinx/board_app /home/xilinx/board_app_src && \
     cd /home/xilinx/board_app_src && make 2>&1 | tail -20"
```

**连板子只能走 `pynq_ssh.py`（paramiko），不能用 `ssh`/`scp`** —— 本机没装
`sshpass`/`plink`，非交互调用时 Windows 会弹图形化凭据框把命令卡死。
`sudo` 要写成 `echo $PYNQ_PASSWORD | sudo -S <cmd>`。

> **`run` 的整条命令串不会被改写，单独的路径参数才会。** MSYS 只看"这个参数像不像
> 一个以 `/` 开头的路径"：`run "cd /home/... && make"` 是安全的，而
> `--board-app /home/xilinx/...` 这种**裸路径参数**会被改写成
> `<LOCAL_PATH>`（§2.10 的 287 条 `READ-FAILED` 就是这个）。

**编译产物要核对新鲜度。** 本工程有过"构建只查 ELF 存在、不看时间戳"的前科 ——
`make` 打印得像成功了不等于产物是新的，核对板上二进制的 mtime 晚于源文件。

```bash
sudo /home/xilinx/board_app_src/board_app selftest
```

期望 `=== 5 PASS, 0 FAIL ===` 与 `RESULT PASS`。判据逐字对齐 §3 的 L1-a/L1-b
（**这里不新增判据，只是把它搬到 C 程序里再证一遍**）：

| # | 判据 | 期望 |
|---|------|------|
| S2a | `enable=1` 先于 `resetb` 释放（在释放前那一刻对影子值断言） | PASS |
| S2b | `resetb` 释放后**轮询**取到 `PRODUCT_ID`（器件自检窗口内会 NACK） | PASS |
| S1 | `PRODUCT_ID` bits[7:3] == `0x08`（**掩码不是等值**，见 §3） | PASS（本板字节 `0x0A` = rev 2） |
| S3a | 写 `REG_CTRL`(`0x3DF`) `0x01` → 读回 bit0 == 1 | PASS |
| S3b | 写 `REG_CTRL` `0x00` → 读回 bit0 == 0 | PASS |

**为什么这一步必须排在最前**：往后每一次失败都有两个可能的来源 ——「厂商驱动/寄存器
序列」和「我们的 C 传输层」。Step 0 把后者单独证明掉，之后再出问题就只剩**一个**变量。
S3a/S3b 是五条里**唯一走写路径**的 —— 而"读得漂亮、写全石沉大海"正是本工程踩过的
CNT 编码坑（§5 末条），只读探针看不出来。

> **⚠ `selftest` 会触发一次真实硬件复位**（拉低再释放 `RESETB`），所以它同时是
> 「**部件自维护寄存器**」的复位基线标尺。复位后实测 `0x109 = 0x4C`、
> `0x10A = 0x40`、`0x10C = 0x4C`、`0x10D = 0x00`。§2.10 的审计要判「这些地址的读数
> 是不是驱动写的」，靠的就是这条基线 —— 而**复位本身在 trace 里看不见**
> （GPIO 复位路径不写寄存器），所以只能这样单独测。

**前置**：先停 `pynq-status`（`systemctl stop pynq-status`）；**`board_app` 与
`spi_probe.py`/`ad9361_init.py` 互斥**（都要独占 PL/SPI）。停守护进程**只判
`systemctl is-active` 不够** —— 手工起的残留进程 PPID=1、不属于任何 unit，
`is-active` 照样报 inactive，必须**同时**查 `ps`。

### 2.6 L2-b：PL 配置 + **否定判据**（Step 1）

```bash
sudo /home/xilinx/board_app_src/board_app plcfg
```

写 4 个 PL 配置寄存器并逐个回读比对。

| # | 判据 | 期望 | 本构建实测 |
|---|------|------|-----------|
| 1.1 | `phy_mode` | `1` | `1` |
| 1.2 | `idelay_en` | `0x7F` | `0x7F` |
| 1.3 | `idelay_tap` | `15` | `15` |
| 1.4 | `phase_inc` | `128` | `128` |
| 1.5 | `f_DATA_CLK` —— **否定判据**，见下 | PASS | 见下 |

**1.5 是这一步的核心，而且它是否定判据。** 此时 BBPLL 还没编程、`DATA_CLK` 不存在，
所以 `f_DATA_CLK` **读不出真实时钟量级**。若观测通道在无时钟时报「时钟在跑」，
那**它是坏的**，后面 Step 3 的读数一律不可信 —— 这就是「先证伪观测通道，再怀疑被测
对象」（§5 首条）。

**判法是 `f_DATA_CLK` 的幅值，不是粘滞位。** `clk_seen` 只回答「这条线有没有动过」，
噪声就能让它置位（这是判据修正 E19）。脚本按三档处置：

| 读数 | 脚本行为 | 含义 |
|------|---------|------|
| `< 0.266 MHz` | 干净 `RESULT PASS` | 通道诚实 |
| `[0.266, 1.000)` MHz | 打印 `NOTE: ... treat the observation channel as SUSPECT and say so in the record`（**pass/fail 结论仍是 PASS、exit code 0**） | 高于本板实测噪声上限；**必须在记录里写明 SUSPECT，不许当成干净通过** |
| `>= 1.000 MHz` | `RESULT FAIL`（exit 1） | 通道坏了，**Step 3 不可信** |

`0.266 MHz` 是本板在 BBPLL 未编程时**实测**的最高噪声（25 个窗口，跨度
`0.023–0.266 MHz`）。它是**观测到的最大值，不是界** —— 天花板钉在它之上 3.8 倍处，
因为真实时钟一出现就是 30 MHz 量级（噪声与信号之间差 113 倍）。
**不要为了让读数落进正向区间去动这个阈值。**

**本构建实测**：`0.269 MHz`（冻结点那次，`STATUS=0x0F00B02D`）、`0.162 MHz`、
`0.902 MHz`（run4，落在灰区 → 按脚本要求把观测通道记为 **SUSPECT**）。
三次判据都是 PASS，但**不确定性如实写进记录**。

### 2.7 L2-c：初始化 + trace（Step 2）

```bash
sudo /home/xilinx/board_app_src/board_app init /home/xilinx/trace_init.txt
# 可选参数（是 init 的参数，不是独立子命令）: [atten <mdB>] [lo <Hz>] [dump]
```

第三个参数是 trace 路径**仅当它不是 `atten`/`lo`/`dump` 关键字时**。注意源码里那句
注释说 `init atten 30000` 是 "no trace" —— **实际行为不是**：`trace_path` 留在 `NULL`
后 `rf_trace_open` 会回落到**默认文件名 `trace_init.txt`**（相对 cwd），
即"换个名字写 trace"而不是"不写 trace"。trace 是**逐条 SPI 事务**的记录，§2.10 的
审计要拿它逐地址比对 —— **所以每一轮都必须留下 trace，并且要知道它落在哪个文件里**。

| # | 判据 | 期望 | 本构建实测 |
|---|------|------|-----------|
| 2.1 | `ad9361_init` 返回值 | `0` | `0`（`AD936x Rev 2 successfully initialized`） |
| 2.2 | ENSM 状态回读 | `RX`/`FDD`（非 `SLEEP`/`ALERT`） | `0x03` = FDD/RX |
| 2.3 | TX LO 回读 | ≈ 80 MHz | `80000000 Hz` |
| 2.4 | TX 衰减回读 | 与写入值一致（写 0） | `0 mdB` |
| 2.5 | 采样率回读印 `MATCH` | `MATCH`（请求 60 MSPS） | RX/TX 均 `60000000` → `MATCH` |

> **2.3 的诚实边界**：LO 回读是驱动**按寄存器算出**的，不是测量值。它证明**寄存器自洽**，
> **不证明 PLL 锁定**。「PLL 锁定」的证明只能来自 §2.9 在 RF 口上的实测。

**⚠ `atten 0` 不是"什么都不改"。** 它让 `ad9361_set_tx_attenuation` 把**两个通道**都写
一遍，而 1R1T 的 init 本来把没用的 **TX2 停在最大衰减**（89750 mdB）。于是带 `atten 0`
的 trace 与裸 `init` 的 trace 在**三个地址**上写入值不同：

| 地址 | 带 `atten 0`（run2/run3） | 裸 `init`（冻结运行 / run4） |
|------|--------------------------|---------------------------|
| `0x075` | `0x00` | `0x67` |
| `0x076` | `0x00` | `0x01` |
| `0x07C` | `0x57` | `0x40` |

后果是审计的 `DECLARED-WEAK` 计数多 2。**「默认值」与「显式设成默认值」不是一回事**；
要断言两轮可比，唯一办法是**把 trace 逐地址对一遍**（本轮就是这样把事故定位出来的）。

**驱动会打两条告警，如实记录、不得忽略**：`ad9361_validate_enable_fir: Invalid ...`
（比例超出 64 taps，**"TX FIR 已启用"这件事尚未成立**）与
`Unhandled case in ad9361_tx_quad_calib line 3172`（TX 正交校准走了默认分支）。
两者是否影响性能本轮**无法判定**，列为开放项。

### 2.8 L2-d：PL 状态转正（Step 3）

```bash
sudo /home/xilinx/board_app_src/board_app status
```

| # | 判据 | 期望 | 本构建实测 |
|---|------|------|-----------|
| 3.1 | `clk_seen` | 从 0 变 1 | `1` |
| 3.2 | `f_DATA_CLK` | 具体数值（非 `NO CLOCK`） | 固件报 `20001220 Hz` —— **这个数值是错的，真值 `120.0 MHz`** |
| 3.3 | 同一观测通道**正反两向都验过** | §2.6 的否定 + 本步的肯定 | `0.162 MHz` → `20.000 MHz`，**123 倍** |

**⚠ 判据 3.2 的数值不可采信 —— 已定位的观测通道缺陷（16 位窗计数器折回）。**
`f_DATA_CLK` 的测法是：DATA_CLK 域一个 **16 位**自由计数器，在 `2^15` 个 FCLK0 周期
（`655.36 µs`）窗口两端取差再换算。窗口 ÷ `2^16` 得 **`99.997 MHz` 就是该通道的不模糊
上限**，超过就整体折回。本板实测 `win_cycles = 0x3333 = 13107`，而

```
13107 + 65536 = 78643 = 120.0 MHz × 655.36 µs   <- 恰好等于 DATA_CLK 的真值
```

**这不是判据失败，是仪器有一个可证明的缺陷**，所以：

* 3.2 只记「**判据字面成立／数值已被证伪**」，**不要写成干净 PASS**；
* 真正的证据是 **3.1**（`clk_seen` 0→1）与 **3.3**（同一观测通道在 §2.6 与本节给出
  123 倍的正反两向差异）；
* 缺陷是**阻塞级**的，但修它（`win_cycles` 加宽到 32 位，或窗口缩短到 `2^14`）
  属于 **RTL 改动，不在本测试任务范围内** —— 在此记为**已知边界**。

> **为什么差点漏掉**：原记录用"跨重启可复现"（`20001220` vs `19999694 Hz`，差 0.006%）
> 来验证这个读数。但**复现性验证的是解码，不是量** —— 一个恒定的折回余数当然次次一样。
> 仪器证伪要证的是**量程**，不是重复性。
>
> **对 §2.6 否定判据的影响（这一点是运气，不是设计）**：真时钟 120 MHz 折回成 20 MHz，
> **仍远高于 `1.000 MHz` 天花板**，故"有钟"照样 FAIL。但通道对 `>= 99.997 MHz` 的任何
> 频率都不再保真 —— 若 DATA_CLK 恰为 `n × 99.9973 MHz`，边沿数折回 **0**，会**谎报
> `NO CLOCK`**。本板 120 MHz 不落在那个点上，**换个采样率就可能失效**。

`status` 打印的 `rst_source_ok` 是 `rf_tx_source` 自己复位链的**存活**，
**不是 phy 就绪**，别当就绪位用。

### 2.9 L3：示波器 B/C 层判据（Step 4）

```bash
MSYS_NO_PATHCONV=1 python -u rf_overlay/sw/scope_tx_check.py \
    --board-app /home/xilinx/board_app_src/board_app --board <板子地址>
```

**必须用系统 `python` 并加 `-u`**：脚本 import pyvisa（`host/.venv` 那个解释器没装），
`-u` 保证输出不被块缓冲吞掉（本工程有过"跑了几分钟日志里 0 字节、被误判成还在跑"的前科）。
`--selftest` 用**合成频谱**离线判一遍全部判据，**无板无示波器** —— 与 §2.1 同理，
上板前先跑它。省略 `--board` 即"只看示波器、不改 RF"。

**`MSYS_NO_PATHCONV=1` 在这里同样不能省**：`--board-app` 是**裸的绝对路径参数**，
Git-Bash（MSYS）会把它改写成 `<LOCAL_PATH>`（详见 §2.10 ——
那里真栽过一次，287 条全 `READ-FAILED`）。规则很简单：**凡以 `/` 开头的参数都要防这一手**，
`run "…"` 那种整条命令串反而安全。

分两层：**B 层是诊断**（只看频率，无幅度门限），**C 层是主判据**。

| # | 判据 | 期望 | 本构建实测 |
|---|------|------|-----------|
| 4.1 | 谱线存在性 | LO 处一条 + 关于 LO 对称两条 | `80.000` / `78.128` / `81.872` MHz，对称偏差 **`0.0 kHz`** |
| 4.2 | `Δf` 线性于 `phase_inc` | 128→256 时比值 `2.00 ± 2%` | `1872.0` → `3744.0` kHz，比值 **`2.0000`** |
| 4.3 | `fs ↔ DATA_CLK` 被裁定并记录是哪一个 | 落在候选集合中之一 | 脚本裁定 `fs = 3 × f_DATA_CLK`，距最近候选 **`0.2%`**（限 5%） |
| 4.4 | LO 平移：所有谱线整体 +5 MHz | 全部平移 | `80.0→85.0`、`78.1→83.1`、`81.9→86.9` |
| 4.5 | 衰减 10000→30000 mdB ≈ `20 ± 3 dB` | 差值 17–23 dB | **`19.85 dB`**（另一次 `19.20 dB`），取差值已消掉前端频响 |
| 4.6 | LO 泄漏随衰减是否同步变化 | **只记录，不判** | `4.31` / `11.51 dB` —— 两次相差大，不判是对的 |

**4.3 的标签被 §2.8 的缺陷污染了，实质结论反而更强。** `fs = 3 × f_DATA_CLK` 里的那个
"3"是**折回因子的产物**。把真值代回去：

```
fs = Δf × 2^12 / phase_inc = 1872.0 kHz × 32 = 59.904 MHz ≈ 60 MSPS
DATA_CLK / 2 = 120.0 / 2 = 60 MHz      <- 与判据 2.5 的驱动回读 60000000 Hz 独立吻合
```

即 **`fs = DATA_CLK / 2`**，DDS 由 `dds_ce` 对 `data_clk` 1/2 分频后推进，
**恰好每个输出采样推进一步**。于是 4.3 从一条"落在候选集合里"的弱判据，变成**第二条
互相独立的采样率证据**。

**4.1 的量化证据**：边带比载波高约 **36 dB**（`snr` 55.3 / 56.7 dB，LO 泄漏 20.0 dB），
正是 **I-only DDS 上变频后载波被抑制、只剩 LO 泄漏**的样子 ——
这也正是 §8 里「I/Q 顺序与镜像抑制」只能记 NOT RUN 的原因。

### 2.10 L3-a：回读审计（Step 5）

```bash
MSYS_NO_PATHCONV=1 python -u rf_overlay/sw/audit_report.py \
    --trace <trace 文件> \
    --board-app /home/xilinx/board_app_src/board_app --board <板子地址> \
    --out docs/rf/l2_audit_stage1_postexempt.txt 2>&1 | tee audit_run.log
```

`tee` 不是装饰：产物的汇总块里**没有** `EVERY read failed` 那条警报（见下），
只留产物会把警报丢掉。跑完**两样都留**：产物（判决 + 汇总）+ 日志（完整 stdout）。

审计是**只读**进程：逐个地址（287 个）在**独立进程**里原始读器件，再与 trace 里
"驱动写过什么"比对。`--board-app` 的默认值就是板上那个路径，显式给一遍没坏处。

**⚠ `MSYS_NO_PATHCONV=1` 不能省 —— 照抄计划 Step 8 的示例命令就会栽在这里。**
`--board-app` 的值以 `/` 开头，Git-Bash（MSYS）把它当 Windows 路径改写成
`<LOCAL_PATH>`；该路径在板上不存在，于是 `audit-begin -> rc=1`，
随后**每一条** `audit` 都 `rc=1`，287 条全 `READ-FAILED`。**这个失败是响的、自证的**：
每条 `READ-FAILED` 的 `raw output` 里就印着被改写后的路径；脚本末尾另有一条
`EVERY read failed (N/N) -- this run proves NOTHING` 的汇总行，就是为这种情形准备的。

> **那条喊话只进 stdout，不进 `--out` 文件。** `--out` 写的是"逐行判决 + 汇总"
> （`audit_report.py:563-564`），而 `EVERY read failed` 是 `print` 出来的 ——
> **只看产物文件会丢掉整个警报**。所以跑的时候 stdout 要落盘/留屏，并且
> **读失败必须单独看**（`read failed` 那一行），不能被 287 条判决盖过去。

判决分五类，另有两种非判决：

| 判决 | 含义 |
|------|------|
| `CONFIRMED` | 不在豁免表里，回读与写入（**按 mask**）一致 |
| `DECLARED-VOLATILE` | 已声明为易变：器件自己会改它，不回读比对 |
| `DECLARED-INERT` | 非可写配置寄存器（如读数据端口），不参与比对 |
| `DECLARED-WEAK` | 写入值**在 mask 内全为 0** —— "回读为 0"证明不了写入生效（**写 0 盲区**，与 §3 的 b3 同源） |
| `UNEXPLAINED` | 不符且无豁免 —— **这是审计最有价值的输出**，逐条查清之前不要从任何 PASS 判据下结论 |
| `read failed` | 读失败（观测通道问题，不是器件问题） |
| `not read (offline)` | 该地址不在 trace 里，无从比对 |

**豁免要显式、要写理由，而且判据里"对器件完全不公平"的那几条必须单独说明。**
典型是**部件自维护寄存器**：`0x109`/`0x10A` 的增益索引由**使能中的** RX1 通道自己维护
（驱动只是读-改-写、低 5 位原样送回），拿一个只读进程去"回读比对"它们，
**对器件是不公平的判据** —— 所以豁免，且理由必须是可复核的引用（厂商源码行号）
或板级实测，**不能是"它没对上"**。`0x10A` 那条只 mask 掉低 5 位、驱动的 bit7:5
**仍然被比对** —— **能少豁免就少豁免，只有这个方向的改动才有资格进豁免表。**
对照：RX2 未使能的孪生寄存器 `0x10C`/`0x10D` **明确不豁免**，它们要是动了就是**真发现**，
审计必须照报 `UNEXPLAINED`。

**本构建最新实测**（`docs/rf/l2_audit_stage1_postexempt.txt`，sha256 `159d7131418622c7...`）：

```
confirmed          : 206
declared volatile  : 3
declared inert     : 3
declared weak      : 75
UNEXPLAINED        : 0
read failed        : 0
not read (offline) : 0
```

`206 + 3 + 3 + 75 = 287`，**0 条 `UNEXPLAINED`、0 条读失败** —— 观测通道在 287 个地址上
全程可用。

**⚠ 产物纪律：产物必须与生成它的脚本版本一致 —— 改过脚本就必须重跑。**
`reason` 字符串本身就是脚本的输出，所以改完理由/豁免表不重跑，产物与它自称的生成脚本
就对不上（本轮 run3、run4 都是因为改了脚本才重跑的）。
`docs/rf/l2_audit_stage1.txt` 是**冻结的**测量（当初的 6 条 `UNEXPLAINED` **不回改**），
而豁免是**在看到结果之后**才加的 —— 用新表覆盖它，就把"豁免晚于测量"这个可核查的事实
抹掉了。**两份都留**，新的写进 `..._postexempt.txt`。

> **一条常备自查**：豁免条目如果**永不触发**，它删掉的检查是 **0**、而它宣称的豁免是
> **1** —— **两头都错，外表却与"真的豁免了一条"一模一样**。这类条目只能靠"它到底命中
> 没有"来抓：把豁免表的地址集合与 trace 实际写过的地址集合**求交**。本轮真抓到过一条
> 永不触发的（`0x037`）。

---

## 3. 判据（**测量前已定死**）

### L1-a 通过条件

| # | 判据 | 期望 | 判 FAIL 的含义 |
|---|------|------|---------------|
| a1 | `PRODUCT_ID` bits 7:3 | `== 0x08`（字节 0x08..0x0F；本板实测 **0x0A** = rev 2） | 器件没应答，或 SPI 基地址错 |
| a2 | 复位/使能时序 | `enable=1` 先于 `resetb` 释放 | — |
| a3 | `REG_CTRL`(`0x3DF`) 回环 bit 0 | 写 `0x01` 读回 bit0=1，写 `0x00` 读回 bit0=0 | 读得通但写不进 → 不一定是 SPI 坏，见 §5 |

> **`PRODUCT_ID` 判的是掩码不是等值。** 厂商驱动写的是
> `(ret & 0xF8) != 0x08`（`ad9361_api.c`），`0x08` 是**原位掩码常量**不是移位后的字段值。
> 拿固定字节做等值比较会把除一个修订号以外的所有器件都误判为"不是 AD9361"。

### L1-b 通过条件

| # | 判据 | 期望 |
|---|------|------|
| b1 | 器件先应答 | `PRODUCT_ID` bits 7:3 == 0x08，**否则不写任何寄存器** |
| b2 | 14 步全部回读一致（按各自 mask） | `verified == total` |
| b3 | 不可验证的步骤要显式报出 | `weak` 列出 `parallel port conf 2` |

**b3 是刻意保留的诚实标记。** 该寄存器写 `0x00`、mask 也是 `0x00` 覆盖位，
所以"回读为 0"这一条**无法证明写入生效** —— 一个完全忽略写、复位默认值又是 0
的器件同样会"通过"。脚本把它单列成 `weak`，而不是混在 14 项里冒充已验证。

---

## 4. 记录（照抄进验收报告）

```
日期：2026-09-17
板子：AC820 Zynq-7020，SN ____
模块：AD-FMCOMMS3-EBZ
P18 档位：2.5V（实测 ____）
overlay：rf_overlay.bit 时间戳 ____  sha256 ____
构建时序：WNS = 12.710 ns  WHS = 0.066 ns（2026-09-17 本构建；重建后须重填）
FCLK0 / FCLK1 = 50.000 / 200.000 MHz（读 SLCR 分频器，**不是** PYNQ 的换算值）

L1-a  spi_probe.py        ：**PASS**   product_id = 0x0A  rev = 2
L1-b  ad9361_init.py      ：**PASS**   verified = 14 / 14   weak = ['parallel port conf 2']
      失败寄存器：无
```

**首轮实测记录（留档，说明上面这两条 PASS 不是一次就到的）**：初版 `spi_probe.py`
在板上报 `RESULT FAIL`、`reason = 'write/read-back of 0x3F5 failed: wrote 0x5A got 0x00'`，
而 `PRODUCT_ID` 读得**完全正常**（0x0A/rev2）。真因是**指令字 CNT 字段编码错**（见 §5
最后一条），不是写路径坏、也不是器件问题。修 `spi_probe.py` 后 L1-a/L1-b 双双 PASS。

---

## 5. 失败时的诊断顺序

**先证伪观测通道，再怀疑被测对象。** 本工程有过多起"判据缺陷被误读成 RTL 故障"的前科。

| 现象 | 先查什么 | 常见真因 |
|------|---------|---------|
| `PRODUCT_ID` 恒 `0xDEADBEEF` | SPI 基地址 | 未映射寄存器的默认值，**不是器件返回的** |
| 器件完全不应答 | P18 档位、FMC 是否插紧、复位是否真的释放了 | 跳过 resetb 释放 → 与 SPI 死掉同形 |
| **读得通、写全不生效** | **指令字的 CNT 字段编码**（见下） | 见下 —— 这是**最像硬件故障的软件 bug** |
| 读得通、写不进 | 是否 `PRODUCT_ID` 就通过而直接开始写 | 复位后自检窗口内的读会 NACK，脚本用轮询覆盖 |
| 单个寄存器回读不符 | 该寄存器的 mask 是否选对 | 只读/保留位不在 mask 内属正常；**在 mask 内就是真失败** |
| `parallel port conf 2` 报 weak | 这是**预期**的 | 该寄存器写 0，判据天生弱 |

### ⚠ 最隐蔽的一条：`AD_CNT` 是"字节数 − 1"，不是字节数

厂商头文件（`ad9361.h`）：

```c
#define AD_CNT(x)   ((((x) - 1) & 0x7) << 12)   /* 注意 -1 */
```

**CNT 字段存的是"字节数减一"**。所以**单字节**传输编码为 **CNT=0**，bits[14:12] 全零。
若按"字节数"直译（单字节写 → CNT=1），器件会认为后面跟**两个**数据字节，而实际只来
一个 → 传输不完整 → **器件丢弃整条写命令**。

**为什么极难发现**：**单字节读在两种规则下都编码成 0**。于是现象是
「`PRODUCT_ID` 读得漂漂亮亮、寄存器扫描全返回合理的复位默认值，但**每一次写都石沉
大海**」—— 看起来完全是 PL 的 MOSI 路径坏了。本工程 2026-09-17 就踩了这个坑：初版
`spi_probe.py` 的 `write_reg` 写的是 `AD_WRITE | (1 << 12)`（CNT=1），板上表现为
`wrote 0x5A got 0x00`。

**为什么两级自检都没抓到**：`FakeMmio` 建模的是 **PL master**，而 PL master **根本不
解释 CNT** —— 它只负责时钟出 `2 + N_TX + N_RX` 个字节。CNT 在模型里不影响任何东西，
所以"自检全绿"和"CNT 编码正确"是两件独立的事。`pl_spi_master_tb.v` 同理（RTL 是透明
字节移位器，CNT 对它是不透明的）。

**修法**：`spi_probe.py` 里把编码收敛到一个 `ad_cnt(num)` 函数（照抄厂商表达式），
并新增 `Ad9361Slave` 模型 —— 它按**器件**的规则解码（一次写必须恰好携带 `CNT+1` 个
数据字节，取最后一个），于是编码错误在**离屏自检**里就被拒掉，不必等上板。
自检里有一条反向用例："单字节写若编码成 CNT=1，器件必须拒绝"。

**不要**为了让测试变绿去放宽 mask —— 那会把真失败一起放过去。
`ad9361_init.py --selftest` 里有一条专门测"mask 内的粘滞位必须仍被判失败"，
就是为了防这个。

---

## 6. 本文件**不**证明什么

写清楚边界，避免把控制面 PASS 说成整个 RF 链 PASS（`backend_contract.md` §7）。

| 不证明 | 为什么 |
|--------|--------|
| **LVDS 数据面可用** | 本流程只走 SPI。数据面**被配置了**（`pp_conf`/`lvds_bias`/`invert` 已写并回读），但**一条 I/Q 都没传过**。计划 §4 明写"SPI 通 ≠ I/Q 可靠" |
| **RF 链可用** | 未编程 BBPLL/RFPLL 分频、未设采样率、**未调 LO**、未做任何校准（基带/ADC/TIA 滤波、DC offset、TX quad 全未跑） |
| **能收能发** | 未做 PN 序列检查、未做环回、未采过一个样点 |
| **时序收敛之外的事** | WNS/WHS 只说明静态时序满足，不说明数据面协议正确 |

**下一阶段（数据面）的门禁**是计划任务 1 的其余通过条件：PN/时序检查、RX 定长采集、
TX 数字单音、受控衰减环回。在本文件全绿之前不必开始那些。

---

## 7. 回退到原 ACM2108 版本

RF overlay 与原 AWG 是**两条独立工具链**，互不覆盖（`version_matrix.md` §4）：
原版本走 `scripts/program_board.sh`（`awg/awg.runs/impl_1/host_bd_wrapper.bit`），
RF 走 `rf_overlay/build.tcl`（`rf_overlay/out/rf_overlay.bit`）。两者产物目录、
PS 程序、部署目录都不同。回退 = 重新下原 bitstream + 原 `awg_bridge`，无需改 RF 侧任何文件。

---

## 8. L2/L3 之后仍然不证明什么

§6 那张表是 **L1 那一轮**的诚实边界（当时只走了 SPI，一条 I/Q 都没传过），
在此**原样保留** —— 它记录的是当时真实的认知状态，删掉或改写它等于伪造历史。
L2/L3 之后这份边界变短了，但没有变成空的：

| 不证明 | 为什么 |
|--------|--------|
| **RX 数据面可用** | `ad9361_phy` 的 RX 侧**确实在跑**（`rx_clk_in`/`rx_data_in`/`rx_frame_in` 从 FMC 引脚接进 `phy_rx`），但它的输出 `adc_data_d1/q1/d2/q2` 与 `adc_d1q1_valid`/`adc_d2q2_valid` 在 BD 里**一个都没连**；整个 overlay **没有 BRAM、没有 DMA、也没有任何 AXI 通路**能看见哪怕一个 RX 采样点 —— 从软件侧看 RX 数据面**不存在**。本轮明确只做 TX |
| **I/Q 顺序与镜像抑制** | **NOT RUN**。当前 DDS 是 **I-only**：`rf_tx_source.v` 的 `dac_data_q1 = mode ? ext_dac_q1 : 12'sd0`，DDS 模式下 Q 恒 0；而 external 模式下 `ext_dac_q1` 也接到 `xlconstant` 的 **0** —— 即**两种模式下 Q 都是 0**。所以输出是**载波抑制的双边带**，没有"哪一侧是镜像"可看；要判 I/Q 顺序还缺一条**接收通路**（见上一行） |
| **我们的寄存器语义理解正确** | 用的是**厂商驱动**。一次 PASS 证明的是**我们的传输层**（PL SPI master + 模式引脚 + LVDS TX 数据面 + PL DDS），**不证明**我们对 AD9361 寄存器语义的理解 —— 驱动写错寄存器我们照样会测通过。§2.10 的审计把这一点**部分**收窄（逐地址回读 + 豁免必须引厂商源码），但它同样以"驱动写的就是对的"为前提 |
| **TX2 在 1R1T 下任何结论** | 1R1T 下 TX2 本就关闭，CH2 上"看不到"与"通道坏了"长得一模一样，**沉默不构成证据** |
| **RF 性能指标** | 不测 EVM、不测镜像抑制的 dB 数、无校准后指标。§2.7 里驱动报的 `TX FIR 未启用` 与 `tx_quad_calib Unhandled case` 的后果**仍未量化** |
| **射频合规 / 功率** | 有线连接，无天线，不涉辐射 |
| **长期 / 温度稳定性** | 单次测量 |
| **`f_DATA_CLK` 这个量本身可信** | 见 §2.8：16 位窗计数器折回，真值 120 MHz 被报成 20 MHz。**判据 3.2 的字面成立，数值已被证伪** |
| **TX 数据通路（`phy_tx`）的时序余量** | 本轮证明的是"器件能发出这个音调"，不是"PL→AD9361 的接口在 60 MSPS 下无亚稳态"；那需要眼图 / ILA，未做 |

**下一步是阶段二（2R2T）**：它把 TX2 打开，让「CH2 上有没有信号」从**无判据**变成
**有判据**。注意 `dds_ce` 的分频比必须跟随 `phy_mode`，且**必须与 2R2T 启用放在同一次
重建里**完成 —— 否则中间那次重建的 TX1 音会**频率翻倍**，症状极像器件故障。
RX 数据面（BRAM/DMA）与镜像抑制的 dB 数都排在它之后。
**阶段二已于 2026-09-18 执行，见 §9 —— 上面那段"下一步"到此为止。**

---

## 9. 阶段二（2R2T）—— 把 TX2 从"无判据"变成"有判据"

### 9.1 为什么必须换模式

1R1T 下 TX2 是关闭的，CH2 上"看不到"与"通道坏了"长得一模一样
⇒ **沉默不构成证据**（§8）。要让「CH2 有没有信号」成为判据，AD9361 必须进 2R2T。

### 9.2 ⚠ 唯一的真陷阱：`dds_ce` 必须跟着 `phy_mode`

`phy_tx` 在 1R1T 下每 **2** 个 DATA_CLK 收一个 I/Q 样点，2R2T 下每 **4** 个。
`rf_tx_source.v` 的 DDS 频闪必须按**样点**走，所以分频上限要跟随 `phy_mode`
（`strobe_max = phy_mode ? 8'd1 : 8'd3`，组合逻辑，改动本身只有一行）。
**若不跟随，TX1 的音会频率翻倍** —— 现象是"C 层判据突然差 2 倍"，
与"器件在 2R2T 下行为不同"无法区分，是本阶段最贵的误判方向。
所以这个改动**必须和 2R2T 启用放在同一次重建里**，且验收时
**先测 5.3（TX1 没变）、再测 5.1/5.2（CH2）**。

### 9.3 ⚠ 第二陷阱：`plcfg` 会把 PL 悄悄弄回 1R1T

`plcfg` 的第一条语句就是 `rf_reg_write(RF_REG_PHY_MODE, 1)`，**在它判断任何事之前**
（`rf_main.c:585`）。它随后读 STATUS，看到已编程的器件当然有 DATA_CLK，
于是报"否定判据失败 / 观测通道坏了"—— **指的是错的东西**。
而此时 PL 已被改回 1R1T、器件却还在 2R2T，接着整轮测量跑的是 1R1T，
数字与一次健康的阶段一**完全无法区分**（绿，且什么都没测到）。
本轮第一次尝试正栽在这上面，破案线索是**从板上独立读回的 `phy_mode = 1`**。

⇒ 阶段二**不跑 `plcfg`**。PL 寄存器由谁负责：`idelay_en`/`idelay_tap` 仍由 `plcfg`
在**器件复位期间**写（那时 DATA_CLK 停着，写它是安全的）；`phy_mode` 由 `init 2r2t` 自己写。
`board_app` 因此新增了一条**只读**的 `plstate`，用来"问"模式而不是"设"模式。

### 9.4 写 `phy_mode` 的时序纪律（硬约束，不是风格问题）

`axi_ctrl_0/phy_mode` 跨到 DATA_CLK 域**没有同步器**，靠的是写序纪律
（`rf_overlay/tools/impl1_clock_groups.tcl:12-14`）：
**必须在 DATA_CLK 停着的时候写，然后再让时钟出现。**

`init 2r2t` 正好是这个形状：拉低 RESETB → 放开 → 等器件启动（**此时 BBPLL 未编程，
DATA_CLK 尚未出现**）→ 写 `phase_inc = 0` 与 `phy_mode = 0` → 之后才 `ad9361_init`
把时钟带起来。`create_bd.tcl` 与 `rf_tx_source.v` 里原先写的
"只要 TX 数据通路安静时写就行"是**弱化**的表述（`phase_inc = 0` 并不等于 DATA_CLK 停了，
未同步的跨域写照样有亚稳态风险），已按 `impl1_clock_groups.tcl` 的口径统一。

`board_app 2r2t` 那条独立子命令**不满足**上述纪律（它可以在器件已在运行、DATA_CLK
正在跳的时候被调用），因此**只作诊断用**，不作为阶段二流程的一步。

### 9.5 流程（实测走通的顺序）

```bash
BOARD=/home/xilinx/board_app_src/board_app
# 0) 重装 overlay 并核对 FCLK0=50.000 / FCLK1=200.000 MHz（FCLK1 绝不去设）。
#    装完 PL 处于复位态：idelay_en=0、phy_mode=0（这是 2R2T，是 slv_reg3 的复位值）、
#    GPIO 全 0 ⇒ RESETB=0 ⇒ 器件被按在复位里，DATA_CLK 停着。
sudo $BOARD plstate     # 期望 idelay_en=0x00 phase_inc=0 phy_mode=0
sudo $BOARD status      # 期望 f_DATA_CLK 只有噪声量级（实测 0.031 MHz）
# 1) 只在器件复位期间写 idelay —— plcfg 的否定判据此时成立
sudo $BOARD plcfg       # 期望 RC=0 且 "RESULT PASS (negative criterion)"
# 2) 一次 init 同时把 PL 与器件搬到 2R2T（写 phy_mode 时 DATA_CLK 停着）
sudo $BOARD init trace_2r2t.txt 2r2t atten 0 lo 80000000
# 3) 独立回读确认模式真的变了（不要只信 init 自己打印的）
sudo $BOARD plstate     # 期望 phy_mode = 0 (2R2T)
# 4) init 2r2t 故意把 phase_inc 留在 0（切换时数据通路无跳变），恢复它
sudo $BOARD phase_inc 128
# 5) 先 CH1（判据 5.3），再 CH2（判据 5.1/5.2）
MSYS_NO_PATHCONV=1 python -u rf_overlay/sw/scope_tx_check.py \
  --board 192.0.2.10 --phy-mode 2r2t --channel CHAN1 --board-app $BOARD
MSYS_NO_PATHCONV=1 python -u rf_overlay/sw/scope_tx_check.py \
  --board 192.0.2.10 --phy-mode 2r2t --channel CHAN2 --board-app $BOARD
```

> **Git Bash 必须带 `MSYS_NO_PATHCONV=1`**：否则 `--board-app /home/...` 会被改写成
> `<LOCAL_PATH>`，板上报 `sudo: C:/Program: command not found`。
> 本轮第一次跑就是这样；`scope_tx_check.py` 的 `plstate` 守卫**拒绝测量**而没有
> 带着未知模式往下跑 —— 这正是那条守卫存在的意义（旧版守卫用的是宽松正则，
> 会被 `board_app` 的 usage 文本满足，从而**带病放行**）。

> **`--phy-mode 2r2t` 是必须的**：它的作用不是"声明"，而是"不跑 plcfg + 自己用
> `init 2r2t` 建立模式 + 从 PL 回读证明"。默认的 `1r1t` 路径仍会跑 `plcfg`，
> 那是阶段一的形状，不能用在 2R2T 板上。

### 9.6 判据与实际结果（2026-09-18）

| # | 判据 | 期望 | 实测 | 结论 |
|---|------|------|------|------|
| 5.1 | CH2 上 LO 处能分辨出一条线 | 位置在 80 MHz | **80.000 MHz，snr 41.3 dB**（C1(CHAN2)：高于噪底 41.6 dB） | **PASS** |
| 5.2 | LO 80 → 85，该线随之平移 | 平移量 = 5 MHz | **4.992 MHz**（C4(CHAN2) 80.0 → 85.0；B2 用 CH1 测亦为 4.992） | **PASS** |
| 5.3 | **CH1 的 `Δf` 与阶段一一致**（不是 2 倍） | 与阶段一 4.2 同值 = 1872.0 kHz | **1872.0 kHz**（旁瓣 78.128 / 81.872 MHz，phase_inc=128；双音比 1872.0 → 3744.0 比值 2.0000） | **PASS** |
| 5.4 | `f_DATA_CLK` 在 2R2T 下变化 | 记录，不预设方向 | 固件报 **40.001 MHz**；扣 16 位折回后真值 **240.0 MHz**（阶段一真值 120.0 MHz） | **记录：翻倍** |

**5.3 为什么是决定性的**：2R2T 下 DATA_CLK 从 120 MHz 变成 240 MHz，而 `dds_ce` 分频
从 2 变成 4 —— 两者**恰好抵消**，音调必须**不变**。任何一边没跟上，音调都会变成 2 倍
（df → 3.744 MHz，正是 C2 在 `phase_inc=256` 下测到的那个值）。实测 1872.0 kHz 与阶段一
**同值**，这一条**同时**证明：
(a) BD 里 `phy_mode` 那根线**确实接到了 `rf_tx_source_0`**；
(b) DDS 频闪**确实跟随了模式**。
（R7 门禁 `tools/check_status_reg.tcl` 另从布线 DCP 侧独立证明过同一根线有 2 个负载：
`ad9361_phy_0` 与 `rf_tx_source_0`。）

**运行中写 `phy_mode` 已成立**：从 `plstate` 读出 1 → 0 的切换发生在**同一个已加载的
bitstream** 上，期间没有重建、没有重装（bit 的 sha256 在跑前跑后一致）。

`f_DATA_CLK` 真值换算：`f = (win_cycles + k·65536) × 50e6 / 32768`。
阶段一 `win_cycles = 13107` → +1×65536 = 78643 → **120.0 MHz**；
阶段二 `win_cycles = 26215` → +2×65536 = 157287 → **240.0 MHz**。
（240 MHz 与"4 拍/样点 × 60 MSPS"自洽，也与 5.3 的 `df` 自洽。）

### 9.7 ⚠ 判据 C3 的比形式**有缺陷**（本轮发现）—— **2026-09-20 已重写，本条作废**

> **更新（2026-09-20）**：本节下面的诊断**成立**，结论**已不适用** —— C3 的"比形式"
> 已经删掉，不再是"NOT RUN for purpose"。见本节末尾。

C3 解的是 `ratio = fs / f_DATA_CLK`：把实测 `df` 往候选
`(phase_inc/2^12) × f_dclk × r`（`r ∈ {0.5, 1, 2, 3}`）上凑。

- **2R2T**：`df = 1872 kHz`、实读 `f_dclk = 39.999 MHz` ⇒ 隐含 `r = 1.50`，
  恰好落在 1 与 2 中间 ⇒ **FAIL**（"separation 0.5%"）。而真值 `r = 60/240 = 0.25`
  **根本不在候选集里** ⇒ 即使 `f_DATA_CLK` 读对了，C3 在 2R2T 下**照样会 FAIL**。
- **1R1T（阶段一那条 PASS）是假识别**：折回值 20.0 MHz 把候选整体缩小 6 倍
  （{312.5, 625, 1250, 1875} kHz），实测 1872 kHz 落进 1875 ⇒ 判
  "`fs = 3 × f_DATA_CLK`"。而真值是 `fs = 0.5 × f_DATA_CLK`。**它 PASS 了，
  却命名了一个物理上错误的关系** —— 这正是 E11 那一类缺陷（给不存在的关系起名字）。
  若 `f_DATA_CLK` 读对（120 MHz），候选变成 {1.875, 3.75, 7.5, 11.25} MHz，
  1872 kHz 落进 1.875 ⇒ 才判得出正确的 `r = 0.5`。

**诊断结论**：C3 在 2R2T 下 FAIL 是**判据自身的缺陷**（候选集缺 0.25，且它拿的是折回后的
`f_DATA_CLK` 当标尺），**不是硬件故障**。独立证据：`df` 与阶段一同值，
且 `df × 2^12 / phase_inc = 59.9 MHz` = 判据 2.5 回读的采样率 60 MHz。
**没有**动阈值去让它变绿。

#### 9.7.1 重写（2026-09-20）：C3 **不再**拿 `f_DATA_CLK` 当标尺

上面那个"独立证据"（`df × 2^12 / phase_inc` 就是采样率）**本身就是更好的判据**，
所以 C3 改成直接做**它**，**彻底不碰 `fs / f_DATA_CLK`**：

```
fs_from_df = df × 2^12 / phase_inc     vs     init 回读的采样率
```

判据变成**与模式无关**（1R1T/2R2T 同一个式子），候选集与分离度常量
（`FS_RATIO_CANDIDATES` / `FS_RATIO_SEPARATION_MIN`）**删除**。
新门限 `FS_FIT_MAX = 0.05`（相对偏差），**不是试出来的**，是从规格算术**预先登记**的：
捕获 62500 点 @1 GSa/s ⇒ 16 kHz 频点，峰值取的是**频点中心** ⇒ `df` 自带 ±16 kHz 量化，
而 `fs = df × 2^12 / phase_inc` 把它**乘 32** ⇒ 60 MHz 上是 **512 kHz = 0.85%**。
门限必须宽于它（否则好板子因量化误 FAIL），又远窄于会让另一档采样率蒙混的 20%。
5% 是量化底的 5.9 倍、那个距离的 1/4。C2 另有一条独立的 ±2% 门。

**上板实测**（2026-09-20，两轮一致）：`df = 1872.0 kHz → fs = 59.904 MHz`，
`init` 回读 **`60.000 MHz`**，偏差 **0.16%** ⇒ **PASS**。
原来的 `FS_RATIO_DOCUMENTED = 2.0` 保留为 **C3x**（RECORD，不判对错）：实测
`fs / f_DATA_CLK = 0.25`，打印出来与文档值 2.0 **DISAGREES** —— 这正是本轮诊断的
**板上直接证据**（0.25 就是 2R2T 的真比值，而它不在任何候选里）。

⇒ 本节原来的 **"NOT RUN for purpose" 作废**：C3 不再按构造在 2R2T 下失败。
**没有**为了变绿而放松任何门限（改的是**判据形式**，且新门限由算术预先登记）。


### 9.8 阶段二**不**证明什么

| 不证明 | 为什么 |
|--------|--------|
| **TX2 的数据通路是好的** | CH2 上看到的**只有 LO 泄漏**（`dac_data_d2/q2` 在 `mode=0` 下恒零，§8）。5.1/5.2 证明的是"2R2T 把 TX2 的模拟与时钟通路打开了"，**不是**"TX2 能把 I/Q 发出去" |
| **`f_DATA_CLK` 这个量可信** | 仍然不可信（§2.8）：真值 240 MHz 被报成 40 MHz。本轮只是**再次**证实了这条，并把折回系数从 1 变成 2 |
| **1R1T 未回归** | 本轮没有在 1R1T 下重跑阶段一全套。5.3 只覆盖 `Δf` 这一条（`df`、`f_dclk`）；`atten` 差值、LO 平移、星座类判据都没有在 1R1T 下复测 |
| **两路同时发数据** | 只有 TX1 有 DDS，TX2 的数据源仍恒零。真正的 2R2T 数据面（两路各自送样点）**未做** |
| **`tx_quad_calib` 的告警已被处理** | `init 2r2t` 仍报 `Unhandled case in ad9361_tx_quad_calib line 3172 clkrf 60000000 clktf 120000000`（2R2T 下 clkrf/clktf 与 1R1T 不同，告警值也随之变），以及 `TX FIR` 未启用那条。两者后果**仍未量化** |
