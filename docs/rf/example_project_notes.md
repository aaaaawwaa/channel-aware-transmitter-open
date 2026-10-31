# 用户例程 `ps_ctrl_AD9361_FM_test_final_copy` 笔记

任务 0/1 的参考资料笔记。**这份是别人写的、在 AC820 上跑通过的工程** —— 它给我们
两条独立价值：(a) 引脚/电平的**第三方验证**（见 `compatibility.md` §0.6）；
(b) 一条**绕开 ADI AXI-DMA 框架**的可行软件路线（见 §3）。

来源：`docs/reference/ps_ctrl_AD9361_FM_test_final_copy.zip`（791 MB，Vivado 2020.2，
part `xc7z020clg484-2` —— **与 AC820 同 part**）。日期戳集中在 2025-11 ~ 2026-02。

---

## 1. 工程结构（只列源码，忽略 cache/runs）

```
ZYNQ_AD9361.srcs/constrs_1/new/toppin.xdc          ← 引脚约束（关键证据）
ZYNQ_AD9361.srcs/sources_1/bd/design_1/design_1.bd ← 只有 PS7 + axi_gpio + ILA + 自定义 IP
ZYNQ_AD9361.sdk/ad9361/src/
    main.c          47898 B   ← 应用主流程
    platform.c      13066 B   ← SPI/GPIO 平台层
    platform.h       6796 B
    platform_config.h  124 B  ← 只有 STDOUT_IS_PS7_UART / UART_DEVICE_ID 0
    config.h         3358 B   ← 平台宏（AXI_ADC_NOT_PRESENT）
    ad9361.c       226134 B   ← ADI 官方驱动（未改）
    ad9361.h       134352 B
    ad9361_api.c    81507 B
    ad9361_api.h    29148 B
    ad9361_conv.c   19608 B
    common.h / util.c / util.h / lscript.ld / README.txt
```

另有约 19 份 `ps7_init.{c,h}` / `ps7_init_gpl.{c,h}` 副本散落在
`ad9361_top_hw_platform_N/` 下 —— 是 Vitis 平台导出的产物，**不是源码**。

---

## 2. 引脚约束（`toppin.xdc`，79 行）

### 2.1 Bank 34（数据面，全部 `LVDS_25`）

| 端口 | 引脚 | IOSTANDARD | FMC 名 |
|------|------|-----------|--------|
| `rx_clk_in_p` / `_n` | M19 / M20 | `LVDS_25 DIFF_TERM TRUE` | LA00_CC_P/N |
| `rx_frame_in_p` / `_n` | N19 / N20 | `LVDS_25 DIFF_TERM TRUE` | LA01_CC_P/N |
| `rx_data_in_p/n[0]` | J21 / J22 | `LVDS_25 DIFF_TERM TRUE` | LA02_P/N |
| `rx_data_in_p/n[1]` | N17 / N18 | `LVDS_25 DIFF_TERM TRUE` | LA03_P/N |
| `rx_data_in_p/n[2]` | J15 / K15 | `LVDS_25 DIFF_TERM TRUE` | LA04_P/N |
| `rx_data_in_p/n[3]` | L17 / M17 | `LVDS_25 DIFF_TERM TRUE` | LA05_P/N |
| `rx_data_in_p/n[4]` | P16 / R16 | `LVDS_25 DIFF_TERM TRUE` | LA06_P/N |
| `rx_data_in_p/n[5]` | K16 / L16 | `LVDS_25 DIFF_TERM TRUE` | LA07_P/N |
| `tx_clk_out_p` / `_n` | P17 / P18 | `LVDS_25` | LA08_P/N |
| `tx_frame_out_p` / `_n` | N22 / P22 | `LVDS_25` | LA09_P/N |
| `tx_data_out_p/n[0]` | J18 / K18 | `LVDS_25` | LA11_P/N |
| `tx_data_out_p/n[1]` | K19 / K20 | `LVDS_25` | LA12_P/N |
| `tx_data_out_p/n[2]` | R18 / T18 | `LVDS_25` | LA13_P/N |
| `tx_data_out_p/n[3]` | M21 / M22 | `LVDS_25` | LA10_P/N |
| `tx_data_out_p/n[4]` | L21 / L22 | `LVDS_25` | LA14_P/N |
| `tx_data_out_p/n[5]` | J20 / K21 | `LVDS_25` | LA15_P/N |

**全部经 Vivado `get_package_pins` 确认 = Bank 34。**

⚠ **RX 用 `DIFF_TERM TRUE`，TX 不用** —— 与 ADI 官方参考一致（TX 是输出，不需要端接）。

⚠ 这里用到的 `J16/J17` 是 `enable`/`txnrx`，Vivado 查出来也是 **Bank 34**（见 §2.3 注）。

### 2.2 Bank 35（控制面，全部 `LVCMOS25`）

| 端口 | 引脚 | FMC 名 | 备注 |
|------|------|--------|------|
| `ctrl_in[0..3]` | A18/A19/A16/A17 | LA24_P/N, LA25_P/N | |
| `en_agc` / `sync_in` | B21 / B22 | LA19_P/N | |
| `resetb` | F16 | LA28_P | |
| `enable` / `txnrx` | J16 / J17 | LA16_P/N | **Vivado 查出是 Bank 34**（见 §2.3） |
| `spi_csn` | B16 | LA26_P | **`PULLTYPE PULLUP`** |
| `spi_clk` | B17 | LA26_N | |
| `spi_mosi` | D22 | LA27_P | |
| `spi_miso` | C22 | LA27_N | |

### 2.3 需要留意的两处

1. **`enable`/`txnrx` 落在 Bank 34 而不是 35**：例程给它们 `LVCMOS25`（Bank 34 是 HR 组，
   `LVCMOS25` 在 2.5V VCCO 下合法），所以不矛盾。但**我们生成约束时要么也这么放，要么
   把它们挪到 Bank 35 的引脚上** —— 附二按 FMC LA16 的物理位置（J16/J17）保留即可。
2. **时钟约束是 `create_clock -period 40.000`**（25 MHz），注释里写
   `#4.069改成40.000，对应采样率变长` —— 说明作者把 DATA_CLK 当 25 MHz 处理
   （AD9361 LVDS DDR 下 60 MSPS 的 DATA_CLK 应是 30 MHz 单沿 / 60 MHz DDR，
   这里的 25 MHz 约束**可能是保守值或与实际不符**）。**不要照抄这个周期**，
   应按实际 DATA_CLK 频率约束。

### 2.4 非 FMC 的引脚

- `uart_tx` → **W6**（`LVCMOS25`）—— PS UART1 的 MIO 引出，与 FMC 无关。
- `IO_data[0..13]` + `clk_driver` → **AB11…U5** —— 这是**板上通用扩展口**的一组，
  与 AD9361 无关（例程用它做别的实验）。`clk_driver` = U5。

---

## 3. 软件架构（关键：不走 ADI AXI-DMA）

### 3.1 平台宏（`config.h`）

```c
#define AD9361_DEVICE  1
#define XILINX_PLATFORM
//#define ADC_DMA_EXAMPLE        ← 关
//#define ADC_DMA_IRQ_EXAMPLE    ← 关
//#define DAC_DMA_EXAMPLE        ← 关
#define AXI_ADC_NOT_PRESENT       ← 开：没有 ADI AXI-ADC/DMA IP
```

**含义**：驱动里所有 `axiadc_*` / `adc_core` / `dac_core` 的调用**全被注释掉**
（`platform.c` 末尾整段 `axiadc_init/post_setup/read/write/set_pnsel/idelay_set` 都是注释）。
AD9361 只通过 **SPI 配置**，**数据面不经过 ADI 的 AXI 框架**。

### 3.2 `main.c` 主流程

```
Xil_ICacheEnable / Xil_DCacheEnable
gpio_init(XPAR_PS7_GPIO_0_DEVICE_ID)          ← PS GPIO
gpio_initial()                                 ← 配 9 个 EMIO（54+0..54+8）
spi_init(XPAR_PS7_SPI_0_DEVICE_ID, pha=1, pol=0)
XGpio_Initialize(&CarrierGpio, XPAR_AXI_GPIO_0_DEVICE_ID)   ← PL 侧 AXI GPIO
   两通道都设成输入（载频回读 / 锁定状态）
ad9361_init(&ad9361_phy, &default_init_param)
ad9361_set_tx_fir_config / set_rx_fir_config(rx_fir_config_search)
ad9361_set_tx/rx_fir_en_dis(1)
ad9361_set_tx/rx_sampling_freq(60000000)
ad9361_set_tx/rx_lo_freq(80000000)
ad9361_set_tx/rx_rf_bandwidth(30000000)
ad9361_set_rx_gain_control_mode(0, RF_GAIN_SLOWATTACK_AGC)
ad9361_set_rx_rf_gain(0/1, 30)
ad9361_set_tx_attenuation(0/1, 10000)          ← 单位 mdB
AXI_REG_WRITE(I_DELAY_EN, 0x7f) / (I_DELAY_TAP, 15) / (DDS_PHASE_INC, 128) / (PHY_MODE, 1)
```

### 3.3 GPIO / EMIO 分配

```c
#define EMIO_TXNRX   54+0      #define EMIO_EN_AGC  54+4
#define EMIO_ENABLE  54+1      #define EMIO_CTL0    54+5
#define EMIO_RESET   54+2      #define EMIO_CTL1    54+6
#define EMIO_SYNC    54+3      #define EMIO_CTL2    54+7
                               #define EMIO_CTL3    54+8
```

`default_init_param.gpio_resetb = EMIO_RESET`（**54+2**）；
`gpio_sync = gpio_cal_sw1 = gpio_cal_sw2 = -1`（不用）。
初始化后 `EMIO_TXNRX=1`（RX 模式）、`EMIO_ENABLE=0`、`EMIO_RESET=1`（复位释放）、
`EMIO_SYNC=0`、`EMIO_EN_AGC=0`（用软件 AGC）、`CTL0-3=0`。

### 3.4 SPI 细节（`platform.c`）

```c
spi_options = XSPIPS_MASTER_OPTION | XSPIPS_CLK_PHASE_1_OPTION | XSPIPS_FORCE_SSELECT_OPTION;
XSpiPs_SetClkPrescaler(&spi_instance, XSPIPS_CLK_PRESCALE_256);
```
`clk_pha=1`（CPHA=1），`clk_pol=0`（CPOL=0）→ **SPI mode 1**。
`spi_read` 走 `XSpiPs_PolledTransfer`，**片选由 `XSpiPs_SetSlaveSelect` 按 `spi->id_no` 选**
（0 → SS0，非 0 → SS1）。

### 3.5 关键参数汇总

| 参数 | 值 | 位置 |
|------|----|------|
| `reference_clk_rate` | **40 MHz** | `main.c` `default_init_param` |
| 初始 LO | 2400 MHz（后改 80 MHz） | 同上 / `set_*_lo_freq` |
| 采样率 | **60 MSPS** | `set_*_sampling_freq` |
| 带宽 | 30 MHz | `set_*_rf_bandwidth` |
| RX 增益 | 30 dB，**Slow Attack AGC** | `set_rx_gain_control_mode` / `set_rx_rf_gain` |
| TX 衰减 | **10000 mdB = 10 dB** | `set_tx_attenuation` |
| 双工 | **FDD**（`frequency_division_duplex_mode_enable = 1`） | `default_init_param` |
| 通道模式 | 1R1T（`two_rx_two_tx_mode_enable = 0`） | 同上 |
| rx/tx_path_clk | `{1280M, 320M/160M, 160M, 80M, 40M, 40M}` | 同上（API 会重算） |
| DCXO | `{8, 5920}` | 同上 |

### 3.5 两段式工作流（宽带搜索 → 窄带解调）

例程的运行时序是**先宽带扫、再窄带解调**，切换参数如下（`main.c`）：

| 阶段 | 采样率 | RF 带宽 | RX FIR | LO |
|------|--------|---------|--------|-----|
| **宽带搜索** | 60 MSPS | 30 MHz | `rx_fir_config_search`，**使能** | 80 MHz 起，扫 |
| **窄带解调** | **4.8 MSPS** | **200 kHz** | `rx_fir_config_demod`，**关→换→开** | 设到检测到的载频 |

窄带切换的四步（`main.c` 670-687）值得照抄：

```c
ad9361_set_rx_lo_freq(phy, detected_freq);          // A. LO 对准检测到的载频
ad9361_set_rx_sampling_freq(phy, 4800000);          // B. 4.8 MSPS
ad9361_set_rx_fir_en_dis(phy, 0);                   // C. 换 FIR 必须先 disable
ad9361_set_rx_fir_config(phy, rx_fir_config_demod);
ad9361_set_rx_fir_en_dis(phy, 1);
ad9361_set_rx_rf_bandwidth(phy, 200000);            // D. 200 kHz
```

**注释里的关键提示**（作者原话）：4.8 MSPS 时「API 会自动把抽取率设为 FIR rx_dec=2，
所以实际 ADC 采样率是 9.6 MHz」—— 即 `set_rx_sampling_freq` 的语义是**接口输出速率**，
ADC 侧速率由抽取链决定。**我们设 4 MSPS 时同理要确认抽取链的实际分频**。

### 3.6 其它配置结构（`main.c` 336-410）

- `rx_fir_config`（注释掉）/**`rx_fir_config_search`**/**`rx_fir_config_demod`** —— 三套 RX FIR
- `tx_fir_config` —— 一套 TX FIR
- 另有 `rx_path_clks[6]` / `tx_path_clks[6]` 运行时用 `ad9361_get_trx_path_clks` 回读打印
  （BBPLL/ADC/R2/R1/CLKRF/RX_SAMPL 六个频点）—— **这是一个很好的诊断手段**，可直接搬。

---

## 4. 对迁移方案的直接影响

1. **B4（`axi-dmac` 是否必须重编内核）不再是硬阻塞** —— 这条路线根本不碰 `axi-dmac`。
   我们仍可选 ADI 路线（需重编内核）或这条 PS-SPI + 自定义 PL 数据面路线。
2. **引脚约束可以直接对照 `toppin.xdc` 生成** —— 它已按 Bank 34 `LVDS_25` /
   Bank 35 `LVCMOS25` 分好，与 `compatibility.md` 附二逐一吻合。
3. **控制面走 PS SPI 是可行的**（Linux 下对应 `spi-xilinx` 驱动 + 设备树补节点，
   见 `compatibility.md` §2.1）。
4. **参考时钟 40 MHz** 是一个已验证的取值。
5. **`enable`/`txnrx` 可走 PS EMIO**，不占 PL 引脚 —— 对我们紧张的资源是好事。
6. **FIR 切换必须先 `en_dis(0)` 再换再 `en_dis(1)`**（§3.5）—— 我们做采样率切换时会用到。
7. **`ad9361_get_trx_path_clks` 回读六个频点**是很实用的诊断手段，可直接搬（§3.6）。

### 不能直接照搬的部分

- 例程是**裸机**（Vitis，跑 OCM），我们是 **Linux/PYNQ** —— 平台层要重写。
- 例程的**数据面**是它自己的实验逻辑（FFT 载频检测等），**不是**我们要的调制链。
- `create_clock -period 40.000` 的 25 MHz 约束**存疑**，别照抄。
- 例程没做设备树、没走 libiio，**不能用它替代 ADI 的 Linux 驱动路线**。