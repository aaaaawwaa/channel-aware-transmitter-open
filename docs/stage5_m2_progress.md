# Stage5 M2 progress

日期：2026-09-07

## 当前结果

| 项目 | 状态 | 证据 |
|---|---|---|
| Vivado BD/module-ref 修复 | PASS | `scripts/create_bd.tcl` 将 sink 观测连接移到 `create_bd_cell` 之后；fresh build 已通过 |
| Fresh bitstream/HDF | PASS | `awg/awg.runs/impl_1/host_bd_wrapper.bit`、`awg/host_bd.hdf` |
| Fresh SDK ELF | PASS | `sdk/ws_stage5_m2_fresh/awg_bridge_stage5_m2/Debug/awg_bridge_stage5_m2.elf` |
| M2 DMA benchmark gate | PASS | `evidence/stage5_m2_dma_benchmark_uart.txt` |
| 400 KiB 低速双 bank 矩阵 | PASS | `evidence/stage5_m2_ddr_pp_low_uart.txt`；BPSK/RECT、QPSK/RRC，800 kSym/s |
| 1 MiB 高速双 bank 矩阵 | PASS | `evidence/stage5_m2_ddr_pp_full_uart.txt`；QAM16/RECT、QAM64/RRC，1 MSym/s |
| 1 MiB BPSK 低速边界 | LIMITED | `evidence/stage5_m2_ddr_pp_timeout_uart.txt`；100 kSym/s 触发既有 `ERROR 24` |
| PP reload 关键路径（PS XTime 回读） | PASS | `evidence/stage5_m2_pp_timeline_uart.txt`；NEXT_SUBMIT 623723/623684 ticks ≈ 1.871 ms，跨两 profile 复现；软件级，非硬件 cycle gap |
| PP sink CRC/TLAST/TKEEP、underflow、FIFO raw 水位与 hardware gap | PASS_WITH_LIMITS | `evidence/stage5_m2_pp_data_plane_low_uart.txt`、`evidence/stage5_m2_pp_data_plane_full_uart.txt`；FIFO 为 XPM FWFT raw read-count，观测范围 0..4097 |
| zero-gap seamless | NOT CLAIMED | 当前仍为 simple/direct-register bounded reload |
| DDR PCB physical signoff | BLOCKED | 缺 BOM、逐根 routing、training、margin 和板卡身份资料 |

## Fresh 构建

- Vivado 2018.3，器件 `xc7z020clg484-2`。
- BD 重建、OOC 综合、主综合、实现、时序和 bitstream 均成功。
- HDF 导出成功。
- bitstream SHA256：`31a74c382ee673e7779e29e905dfaba2e03dbed76484729473cac807b7c3992f`。
- HDF SHA256：`2bb2988e928c4bace425d34b56f0483ab5935b85cc036609b6cb14b394261795`。
- fresh ELF SHA256：`7b00f2d8527866899504719688960707d5b37ad6ca4e8391653874589e5df233`。

## COM11 benchmark transcript

```text
M2 DMA benchmark gate PASS
DMA BENCH PASS BYTES 1048576 CRC 262A423D STATUS E0100000 TICKS 1748678 RATE_MBPS_X1000 199879 FIRST_VALID_CYCLE 69552 FIFO_MIN 0 FIFO_MAX 3 DDR_PP_TIMELINE IOC_TICKS 0 DRAIN_TICKS 0 NEXT_SUBMIT_TICKS 0 FIRST_BEAT_TICKS 0 GAP_TICKS 0 FIFO_MIN 0 FIFO_MAX 0
FIRST_VALID_CYCLE 69552 FIFO_MIN 0 FIFO_MAX 3
```

解释：`STATUS E0100000` 表示 ACTIVE、DONE、TLAST 置位，ERROR 清零，计数为 `0x100000` 字节。sink 时钟域首个有效 beat 周期为 `69552`；FIFO 读计数水位为 `0..3`（深度 4096）；速率为 `199.879 MB/s`。`DDR_PP_TIMELINE` 在独立 `SET DMA BENCH` 命令中为零是预期行为，不将其解释为 PP reload 时间线。

## COM11 PP 长 payload 矩阵证据（2026-09-06）

### 400 KiB 低速组

- 两个 bank 均为 `409600` bytes / `3276800` bits，commit CRC16 为 bank0 `9DBF`、bank1 `6423`。
- BPSK/RECT、QPSK/RRC 均以 `800000 Sym/s` 完成。
- 两个 profile 都观测到 `ACTIVE_BANK 0` → `DRAIN` → `ACTIVE_BANK 1` → `DRAIN` → `IDLE`，全程 `ERROR 00`。
- transcript：`evidence/stage5_m2_ddr_pp_low_uart.txt`。

### 1 MiB 高速组

- 两个 bank 均为 `1048576` bytes / `8388608` bits，commit CRC16 为 bank0 `DDF0`、bank1 `CC1E`。
- QAM16/RECT、QAM64/RRC 均以 `1000000 Sym/s` 完成。
- 两个 profile 都观测到 `ACTIVE_BANK 0` → `DRAIN` → `ACTIVE_BANK 1` → `DRAIN` → `IDLE`，全程 `ERROR 00`。
- 由于冻结的 20-byte ASCII CHUNK 和单次 host 运行时限，上传阶段拆为 bank0、bank1 两次命令；第二阶段使用 `--reuse-upload`，没有改变 PP wire format。
- transcript：`evidence/stage5_m2_ddr_pp_full_uart.txt`。

### Timeout 边界

- 原始 1 MiB BPSK/RECT、`100000 Sym/s` 试验在双 bank 上传和 metadata 校验成功后进入既有 `ERROR 24`；证据为 `evidence/stage5_m2_ddr_pp_timeout_uart.txt`。
- 未修改生产 `DMA_TIMEOUT_TICKS` 或错误码。为在现有 simple-DMA timeout 预算内获得低速长 payload 证据，低速组采用 400 KiB / 800 kSym/s。

### 软件级 PP reload 时间线回读（2026-09-06）

在不改 RTL/BD/固件的前提下，用现有 `SET DMA BENCH` 响应中已经打印的
`DDR_PP_TIMELINE` 字段，回读真实 PP 双 bank 播放期间固件记录的时间线：
`scripts/ddr_pp_timeline_gate.py COM11`，复用已 commit 的 1 MiB 双 bank
（CRC metadata DDF0/CC1E）。两个 1 MSym/s profile（QAM16/RECT、QAM64/RRC）均
再次观测到 `ACTIVE_BANK 0 -> 1 -> IDLE`、`ERROR 00`，且回读值稳定：

| 字段 | QAM16/RECT | QAM64/RRC | 含义 |
|---|---|---|---:|
| IOC_TICKS | 687772122 | 458515251 | 末 bank DMA start→IOC（按符号率门控的 1 MiB 消费） |
| DRAIN_TICKS | 11274600 | 8167865 | IOC→user_stream_done（轮询观测） |
| NEXT_SUBMIT_TICKS | 623723 | 623684 | **bank0 drain 观测→bank1 SimpleTransfer submit**（固件 bounded reload 关键路径） |
| FIRST_BEAT_TICKS | 0 | 0 | PP 路径下 user_valid（旧 BRAM commit 门控）未置位，恒 0 |
| GAP_TICKS | =NEXT_SUBMIT | =NEXT_SUBMIT | 未观测到首 beat，未更新，不等于 drain→首有效 gap |

时间基准：PS XTime，`COUNTS_PER_SECOND = 333333343`（冻结 M2 fresh BSP，
CPU 666666687 Hz /2）。`NEXT_SUBMIT ≈ 1.871 ms`，两个 profile 偏差 <0.01%。
transcript：`evidence/stage5_m2_pp_timeline_uart.txt`。

边界：这是软件级回读，不是 PL 硬件 cycle-accurate gap；`SET DMA BENCH` 会先跑
一段独立 benchmark 再输出保留的 PP timeline，该 benchmark 也复写了 DDR bank0
内容，但 timeline 在 PS OCM 中不受影响。回放时 bank payload 字节无法板级回读
复核，本证据仅限时序与 bank 顺序。

### PP normal-mode 数据面观测证据（2026-09-07）

使用 `scripts/ddr_pp_data_plane_gate.py COM11`，在不修改 `GET DDR PP`、不增加公开测试命令的前提下，通过现有 GPIO 页回读两个 PP bank 的数据面结果。两个 gate 均在每个 profile 中验证 PP sink CRC32、TLAST、TKEEP、有效字节数、source underflow、FIFO raw min/max 和 bank0 TLAST 到 bank1 首个有效 beat 的 `sys_clk` gap。

### 400 KiB 低速组

- BPSK/RECT、QPSK/RRC，`800000 Sym/s`。
- 两个 profile 的 PP sink CRC32 均为 bank0 `D633BBDE`、bank1 `9C25A04B`。
- 两个 bank 均报告 TLAST=1、TKEEP=`F`、underflow=0、bytes=`409600`。
- FIFO raw read-count 均为 `0..4097`；gap cycles 分别为 `42009`、`293164`。
- 每个 profile 均观察到 `ACTIVE_BANK 0 -> 1 -> IDLE`，metadata CRC16 为 `9DBF/6423`，`ERROR 00`。
- transcript：`evidence/stage5_m2_pp_data_plane_low_uart.txt`。

### 1 MiB 高速组

- QAM16/RECT、QAM64/RRC，`1000000 Sym/s`。
- 两个 profile 的 PP sink CRC32 均为 bank0 `426B7A5C`、bank1 `4C416790`。
- 两个 bank 均报告 TLAST=1、TKEEP=`F`、underflow=0、bytes=`1048576`。
- FIFO raw read-count 均为 `0..4097`；gap cycles 分别为 `95869`、`95214`。
- 每个 profile 均观察到 `ACTIVE_BANK 0 -> 1 -> IDLE`，metadata CRC16 为 `DDF0/CC1E`，`ERROR 00`。
- `SET DMA BENCH` 保持既有语义并会写入共享的 DMA/PP bank0 缓冲区；因此后续 profile 在播放前只重传 bank0，避免 benchmark pattern 污染 PP payload，同时不改变 PP wire format。
- transcript：`evidence/stage5_m2_pp_data_plane_full_uart.txt`。

### FIFO raw count 语义

`axis_data_fifo` 配置深度为 4096，但 Vivado 2018.3 XPM FIFO 的 FWFT read-count 计算包含 output staging 的 `extra_words_fwft`，因此 GPIO raw count 的合法观测上限冻结为 4097。该字段表示 IP 的 raw read-count，不等同于严格物理 occupancy；gate 接受 `0..4097`，仍拒绝 min 大于 max 或超过 4097 的结果。

### 观测边界

- `PP_GAP_CYCLES` 是 sink 在 `sys_clk` 域测得的 bank0 TLAST 到 bank1 首个有效 handshake 的硬件周期数；它不是 PS XTime 的 `NEXT_SUBMIT_TICKS`，也不等同于 DAC 模拟波形的连续性。
- gate 的 `PP STATE` 时间戳来自 UART 主机轮询，能证明状态顺序和 bounded reload 完成，不能替代上述 sink 硬件 gap。
- PP underflow 当前采集的是 `awg_top` source sticky underflow 状态；transcript 中两 bank 均为 0，但没有独立的累计 underflow 次数，因此“underflow count”仍不宣称。
- 当前实现仍是 simple/direct-register bounded reload；正 gap 明确表明不能称为 zero-gap seamless。
- `evidence/stage5_m2_dma_benchmark_uart.txt` 中的独立 benchmark CRC、首 beat 周期和 FIFO 水位继续只代表 benchmark，不与 PP 长 payload 结果混用。
## 结论边界

本次板测证明了 fresh 硬件上 1 MiB MM2S benchmark 观测链路，以及 400 KiB 低速和 1 MiB 高速 PP 双 bank bounded reload 的指定 profile 矩阵、metadata CRC、bank 顺序和独立数据面 CRC32/TLAST/TKEEP/FIFO/gap 观测。M2 仍为 **PASS_WITH_LIMITS**：FIFO 水位是带 FWFT staging 语义的 raw read-count，underflow 是 sticky zero observation 而非累计计数，当前 simple/direct-register 方案存在可测正 gap，不能宣称 zero-gap seamless；DDR PCB physical signoff 仍为 BLOCKED。
