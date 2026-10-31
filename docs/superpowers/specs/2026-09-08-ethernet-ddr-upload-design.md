# AC820 GEM0 UDP DDR PP 上传设计

**状态：待规范审阅**  
**日期：2026-09-08**

## 目标与范围

将大尺寸 DDR PP payload 的主机传输从 UART ASCII 分块改为 PS GEM0 上的 UDP 数据面。第一阶段只支持**停止 DDR PP 播放后预装载**一个或两个 1 MiB bank；完成并验证后仍通过现有 DMA/FIFO/PL 链路播放。

UART 保留为控制、日志和恢复通道。该阶段不支持播放期间重写非活动 bank，也不将 PP 改造为无限循环流。

## 已知事实

- AC820 的 PS GEM0 接至板载 RTL8211F 和 RJ45；主机侧直连网卡已显示 1 Gbps 物理链路。
- 工程当前关闭 ENET0/1，且板上未观察到 ARP 邻居或可用网络服务。
- 两个 DDR PP bank 各为 1 MiB；当前 UART 协议每块最多 20 B，并在每个 `OK` 后发送下一块。
- DMA 仅承担 DDR 到 PL 的 MM2S 播放，不是主机到 DDR 的接收通道。

## 架构

```text
Host UDP uploader
  -> PS GEM0 / RTL8211F
  -> bare-metal lwIP raw UDP receiver
  -> inactive DDR PP staging bank
  -> full-payload CRC32 and cache flush
  -> valid bank metadata
  -> existing MM2S DMA -> AXIS FIFO -> user stream -> IQ/RRC
```

PS7 的 GEM0 与 MDIO 在 `scripts/ps7_props.tcl` 中启用；PHY reset 保持当前硬件复位拓扑，除非实物原理图证明其接至可控 PS MIO。`scripts/create_bd.tcl` 保持作为唯一的派生 BD 创建入口。无需引入 AXI Ethernet、额外 PL IP 或 PL 时钟域。

建议首个点对点地址约定为主机 `192.0.2.10/24`、板卡 `192.0.2.10/24`、UDP 端口 `40100`。板卡 IP 作为固件配置的明确常量，M0 实板验证确认无冲突后再冻结。

## UDP 事务

每个报文都有固定网络序头：`magic`、`version`、`session_id`、`type`、`bank`、`offset`、`sequence`、`payload_length`、`payload_crc32`。

事务类型：

1. `BEGIN` 声明目标 bank、总字节数、完整 payload CRC32。
2. `DATA` 写入 payload；单包 payload 限制为避免 IP 分片的大小（约 1400 B）。
3. `STATUS` 返回累计进度、会话状态和缺失范围。
4. `COMMIT` 在全部范围已接收且完整 CRC32 一致时登记 bank 为有效。
5. `ABORT` 仅清除该会话的 staging metadata。

host 采用有界滑动窗口；设备依据 sequence/bitmap 返回累计确认与缺失范围，host 只重传缺失报文。重复的有效 `DATA` 包必须幂等。无效头、会话不匹配、越界写、错误 CRC、过大数据报和超时会话都必须被拒绝或中止，不能写入有效 bank。

## DDR、DMA 与状态约束

第一阶段延续现有停播约束：仅当普通 DDR 上传、DMA 播放、PP 播放和手动播放均不活动时，允许 `BEGIN`。`COMMIT` 前目标 bank 始终无效；CRC 失败、超时或 `ABORT` 不得改变另一有效 bank。

lwIP 回调由 CPU 写 DDR，因此提交给 MM2S DMA 前必须 flush 写入范围。不得把未来 JTAG 外部写入所需的 cache invalidate 语义混入该 CPU 写入路径。

现有 `CLEAR` 和 `COMMIT` 的双 bank 语义保持；本阶段不放宽播放期间写入限制。运行时更新非活动 bank 需在后续设计中单独定义 `active`、`next`、`staging` 所有权、DMA 生命周期和 bank 切换行为。

## 构建与软件边界

- 修改 PS7 属性后必须运行完整 `scripts/build_bitstream.py --force` 流程，并使用 `scripts/check_bitstream.py` 校验。
- 从 fresh HDF 重新创建 BSP/应用；应用继续使用 OCM `.text`，并依照当前链接修补策略将数据、堆和栈置于 DDR。
- BSP 添加 GEM/lwIP 依赖。SDK 主循环必须同时服务网络轮询和现有 `ddr_pp_service()`，不能阻塞 DMA/FIFO 生命周期。
- host 新增独立 UDP uploader/worker；`host/siggen/transport.py` 的 `SerialWorker` 保持串口职责，不能承载 UDP payload。

## 验证停止门

### M0：GEM0 与网络栈

固件报告 GEM/lwIP 初始化结果、链路状态和 IP。主机必须获得 ARP 邻居并通过 UDP echo。任何失败只排查 MIO、PHY 硬件复位拓扑、MDIO、PS 初始化或 lwIP，不进入 DDR 写入。

### M1：64 KiB 完整性

以确定性 64 KiB pattern 上传至 staging bank，设备与 host 的 CRC32 一致。覆盖重复包、乱序包、丢包重传、错误 CRC、越界 offset、`ABORT` 和会话超时；已验证 bank 必须保持不变。

### M2：1 MiB 性能

上传完整 bank，记录 host 端总时间、有效 MB/s、重传数、最终 CRC 和设备错误。首期门槛为至少 3 MB/s。

### M3：双 bank 与播放回归

两个 bank 均完成完整性验证后，按现有 PP `START` 播放。验证 DMA/FIFO 无 underflow，并回归 UART 控制、BRAM USER、PRBS7 和已有 DDR/DMA gate。

只有 M0–M3 均通过，才可评估第二阶段的播放期间非活动 bank 重装。
