# 存储结构审计与部署恢复机制（接续 10-12 复盘）

本报告接续 `docs/reports/2026-10-12-session-review-and-board-recovery.md`，按其"下一步执行建议"的优先级推进：**先存储（稳定性确认＋离线 FAT 检查），再部署恢复机制，真实研究仍排第三**。全程对板卡**只读**：无上传、无安装、无重挂载、无修复写入；未安装/未重装任何发布，未加载 Overlay，未布防、未发射、未采集。新增真实物理帧 0，开发物理累计仍 **67**。

## 结论先行

1. **运行稳定（观测窗口内）**：板卡自 10-12 恢复后同一 `boot_id` 连续运行 **9,434 秒（约 2 小时 37 分）**，本次启动 dmesg **零** MMC/SDHCI/ADMA/I-O/ext4 错误；根 ext4 超级块 `state=0x1`（clean）、`error_count=0`；`/` 与 `/boot` 均 rw。**这不证明 SD 卡没有硬件问题**，只说明本窗口内未再复现。
2. **启动分区是 FAT16，不是 FAT32**（此前未被记录）：130 MiB、4 KiB 簇、2 份 FAT 各 136 扇区、512 个根目录项、标签 `PYNQ`、由 `mkfs.fat` 创建。据此才算出正确的 FAT 布局——第一版探测按 FAT32 取偏移，读到第二份 FAT 时越界失败（失败日志保留）。
3. **两次独立只读抓取逐字节相同**（各 136,314,880 字节，SHA256 `0b809abc…`），说明抓取窗口内没有并发写入，镜像可作为审计输入。
4. **离线 FAT 结构审计判定 consistent**：0 error、0 warning；两份 FAT 完全相同；33,241 簇中 1,670 个已分配簇**全部可达**、0 丢失簇；目录项、LFN 序列与校验和全部正确。
5. **7 个启动文件与电脑上的备份逐字节一致**（SHA256 全等）→ 备份是卡上内容的忠实副本，且卡上启动文件当前完好。备份恢复路径因此可用且已核验。
6. **"未正常卸载"的准确解释（本轮新增、有内核源码依据）**：`FAT-fs (mmcblk0p1): Volume was not properly unmounted` **不是**存储控制器故障，而是 Linux 在 FAT16 引导扇区偏移 `0x25` 的挂载状态位（`fat16.state`）为脏时给出的提示。该位在 rw 挂载时被置位，**只在干净卸载时清除**，且一旦为脏就**粘滞**（`fat_set_state` 直接提前返回），必须由文件系统检查清除。实测该字节 = `0x01`。这与操作者在 MMC 故障后**硬断电**（未干净卸载）完全一致；**不能**据此宣称 FAT 损坏，也**不能**说已经修复。
7. **板卡有 e2fsprogs 但没有 dosfstools**：`dumpe2fs/tune2fs/e2fsck/debugfs` 存在，`fsck.fat` 系列仍不存在。因此"离线 FAT 检查"由本轮新建的主机侧结构审计完成；**真正的 fsck.fat 仍需维护环境（读卡器）或经批准在板上安装 dosfstools**，两者本轮都未执行。
8. **部署恢复机制已落地**（第二优先级）：追加式尝试日志（attempt ID／阶段／退出码／boot_id／服务／内核存储错误）、**执行与观察分离**（超时后新开只读会话读回，绝不重装）、安装前发现内核存储故障即**不写入板卡**。新增 10 项测试，`scripts/tests` 全量 **320 passed**（改动前 288，无回归）。
9. **仍未闭合**：物理离线 FAT 检查（需断电取卡或经批准装 dosfstools）、MMC 故障根因（`NOT_ESTABLISHED`）、真实 46 dB 研究（仍被 `lease_held` 谓词阻塞，需新候选）、真实 runner、S2 正式/真人/S3/S4。

## 一、本轮只读接触与证据

| 项 | 内容 |
|---|---|
| 板卡接触 | 仅只读 SSH（`sudo` 只读命令）＋ 只读串口 COM11；**无文件上传、无安装、无挂载/卸载/重挂载、无修复写入** |
| 串口观察 | COM11 被动监听 **600 秒**，向控制台写入 **0 字节**，收到 **0 字节**，无任何存储行 |
| 抓取 | `/dev/mmcblk0p1` 两次 `dd` 只读流式抓取，各 136,314,880 字节，两次 SHA256 相同 |
| 审计 | 主机侧离线完成，输入为归档的 `.img.gz`（重放报告与原报告逐字段相同） |
| 未做 | 未安装、未重装、未加载 Overlay、未布防/TX/RX、未采集、未修改发布或分区 |

## 二、存储健康读回（只读）

| 项目 | 实测 |
|---|---|
| `boot_id` | `d331f4a9-8fd0-4c58-b19b-bd34e9c1a9e7`（与 11:57 恢复读回同一次启动） |
| uptime | 首次探测 7,395.58 s → 末次 9,434.17 s（观测跨约 34 分钟） |
| 本次启动存储错误 | **无**（MMC 中断超时／SDHCI／ADMA／I-O／ext4-fs 全部为空） |
| ext4 根文件系统 | `state=0x1`（EXT4_VALID_FS，clean）、错误标志 0、`error_count=0`、`mnt_count=35` |
| 挂载 | `/` rw；`/boot` rw（`errors=remount-ro`） |
| 介质 | SD 卡（`type=SD`、`serial=0x00000131`、`date=11/2025`），30,535,680 块 = 14.56 GiB |
| 发布读回（只读） | `current` 与 `active-release` 均 `b67a8aaab0c5904822580c0647b96fd0`；`pynq-status`/`rf-daemon` 双 active/running、`NRestarts=0`；无内核存储故障（uptime 9,757 s，同一次启动） |
| 启动分区 | `/dev/mmcblk0p1`，起始扇区 8，266,240 扇区 = 130 MiB |
| FAT 类型 | **FAT16**：512 B/扇区、8 扇区/簇（4 KiB）、保留 8 扇区、2 份 FAT×136 扇区、512 根目录项 |
| FAT 表头 | 两份副本均为 `FAT[0]=0xFFF8`、`FAT[1]=0xFFFF` |
| 引导扇区状态位 | 偏移 `0x25` = `0x01`（见第四节） |
| 可用工具 | 板上有 `dumpe2fs/tune2fs/e2fsck/debugfs`；**无** `fsck.fat/fsck.vfat/dosfsck/busybox` |

## 三、启动分区 FAT16 完整结构审计

新增工具 `scripts/fat_structure_audit.py`（配套 `scripts/tests/test_fat_structure_audit.py`，**22 项测试**）。它是**只读结构审计**，**不是** dosfsck/fsck.fat，也**不做修复**；报告中一律如此表述。

检查内容：BPB 与布局自洽（含由簇数推导的 FAT 位宽与类型串一致性）、两份 FAT 逐字节相等、每条簇链的范围/环/长度与记录大小一致、交叉链接、丢失簇、目录项属性与 LFN 序列/校验和、退化目录项、目录环、可选的目标文件逐字节校验。

**测试覆盖的注入缺陷全部被抓**：FAT 副本不一致、交叉链接、链长于/短于文件大小、丢失簇、簇链自环、FAT 项越界、引导扇区签名错误、类型串不符、有大小却无首簇、LFN 校验和不符、退化目录项、目录环（自引用目录曾使遍历**死循环**，现已加访问集合与预算并报错）、截断镜像、FAT32 布局路径；另有两项证明审计**从不写镜像**。

**真实镜像判定（`fat-audit-report.json`）**：

| 项目 | 结果 |
|---|---|
| 判定 | **consistent**（0 error、0 warning） |
| 两份 FAT | 逐字节相同（`identical: true`） |
| 簇分配 | 33,241 簇：1,670 已分配且**全部可达**，31,571 空闲，**0 丢失簇** |
| 对象 | 11 个：卷标 `PYNQ`、8 个文件、1 个目录及其 2 个 Windows 元数据文件（`IndexerVolumeGuid`、`WPSettings.dat`） |
| 内容校验 | 期望 7 个启动文件，**7/7 逐字节相符**，缺失 0、不符 0（`status: verified`） |
| 可重放 | 由归档 `boot-image-pass1.img.gz` 解压重跑，报告与原报告逐字段相同 |

**边界（不得越界表述）**：只审了 FAT16 启动分区；根 ext4 **未**做 e2fsck（只读了超级块）；**没有任何修复**；因此**不能**说"SD 卡已彻底修复/长期稳定"。

## 四、`Volume was not properly unmounted` 的准确解释

依据 Linux v5.4 源码（板端内核 5.4.0-xilinx-v2020.2 同源）：

- `include/uapi/linux/msdos_fs.h`：`struct fat_boot_sector` 的 FAT16 分支里 `__u8 state; /* undocumented, but used for mount state */`，位于偏移 **0x25**；`#define FAT_STATE_DIRTY 0x01`。
- `fs/fat/inode.c`：`fat_fill_super()` 读 `sbi->dirty = bpb.fat16_state & FAT_STATE_DIRTY`；`fat_set_state()` 在 rw 挂载时置位、干净卸载时清除，但**若已为脏则直接 `return`**（只在挂载时打印该警告）→ 该位**粘滞**，只能由文件系统检查清除。

实测该字节 = `0x01`，与"操作者在 MMC 故障后断电重启（未干净卸载）"一致。**结论**：该提示是"请运行文件系统检查"的持久标记，不是控制器故障，也不构成 FAT 损坏的证据；而本轮的离线结构审计恰好给出"结构一致"的独立结论。**它不会自行消失**，需要一次真正的 FAT 检查（维护环境或经批准的 dosfstools）来清除。

## 五、备份保真与恢复路径

- 7 个启动文件（`BOOT.BIN`、`boot.py`、`boot.scr`、`image.ub`、`REVISION`、`pynq-status.service.new`、`pynq_status.py.new`）在卡上的内容与 `boot-backup-final-receipt.json` 记录的 SHA256 **全部相等**。
- 因此备份可用于恢复，且当前卡上启动文件未被破坏。
- 未执行任何写入/恢复动作（无需恢复）。

## 六、部署恢复机制（第二优先级）

`scripts/rf_headroom_deploy.py` 已改造（`scripts/tests/test_rf_headroom_activation.py`，**9 项测试**，含用**真实 bundle**＋假板卡的端到端路径）：

| 机制 | 行为 |
|---|---|
| 持久尝试日志 | `attempt.jsonl` 每阶段追加并 `fsync`；含 attempt ID、阶段、退出码、boot_id、uptime、服务状态、内核存储错误、payload 摘要 |
| 撕裂尾行 | 追加前自动截断不完整末行（否则后续阶段会被解析器隐藏），并有测试 |
| 执行/观察分离 | 安装**只派发一次**；客户端超时或连接中断后**新开一个只读会话**读回，绝不重装 |
| 六类结局 | `INSTALLED_VERIFIED` / `SAFE_ROLLBACK_VERIFIED` / `UNEXPECTED_STATE` / `UNKNOWN_READBACK_INCOMPLETE` / `ABORTED_STORAGE_FAULT` / `ABORTED_PREFLIGHT_UNKNOWN`，一律 `retry_allowed=false` |
| 安装前存储门禁（fail-closed） | 预检发现内核存储故障，或预检快照本身不可用 → **不上传、不派发**，直接 `ABORTED_STORAGE_FAULT` / `ABORTED_PREFLIGHT_UNKNOWN`，板卡零改动 |
| 读回内容 | 用**已安装发布自己的** `rf_state_check.py`（先校验上传摘要）＋ `verified_state` 判据，与 10-12 手工定案同一把尺子 |

`scripts/tests` 全量：**320 passed**（改动前 288 → 新增 22 FAT 审计＋10 激活路径，无回归）。本轮**未**在真板上执行该路径（无需安装）。

## 七、下一步（按优先级，含需要决定的事项）

1. **物理离线 FAT 检查（仍需现场动作）**：断电 → 取卡 → 读卡器接入电脑 → 只读检查（`chkdsk X:` 不带 `/f`，或本工具 `python scripts/fat_structure_audit.py \\.\X: --bytes <分区字节数>`）。**不要在挂载的根分区上做修复**；修复前先确认启动文件备份（已具备）。
2. **可选（需授权）**：若板上可达软件源，安装 `dosfstools` 后在**卸载的** `/boot` 上跑 `fsck.fat -n` 取得真正的 fsck 结论。本轮**未**执行，因为安装软件包会写根分区。
3. **复现 MMC 故障时的动作**：立即停止一切写卡/安装，保留串口与存储状态；本轮 2 小时 37 分未复现，根因仍 `NOT_ESTABLISHED`。
4. **真实研究（第三优先级，未变）**：46 dB 研究仍被 `lease_held` 谓词阻塞（需新候选＋新资格＋新安装）；真实 runner 仍不存在；人工接线确认与控制 token 必须来自人。
5. S2 正式 108 物理＋12 离线/真人、S3、S4：**NOT_RUN**。

## 八、状态表

| 项目 | 状态 |
|---|---|
| 板卡运行（本窗口） | 稳定，9,434 s，同一次启动，无存储错误 |
| 根 ext4 超级块 | clean，error_count 0 |
| 启动分区 FAT16 结构 | **consistent**（0 error/0 warning，0 丢失簇，两份 FAT 相同） |
| 启动文件备份保真 | **7/7 逐字节相符** |
| `Volume was not properly unmounted` | 已解释为**粘滞挂载状态位**（0x25=0x01），非控制器故障；需 FAT 检查清除 |
| 真正的 fsck.fat | **NOT_RUN**（板卡与主机均无 dosfstools；物理维护环境未做） |
| 根 ext4 e2fsck | **NOT_RUN**（只读超级块快照） |
| MMC 故障根因 | **NOT_ESTABLISHED** |
| 部署恢复机制 | 已实现＋10 测试；`scripts/tests` 320 passed |
| 安装 / 重装 | **未执行**（板卡仍为 b67a8aaa，未被本轮改动） |
| 新增真实物理帧 / 初始化 | 0 / 0（开发物理累计 67） |
| 真实 HEADROOM 研究 | 仍 **BLOCKED**（`lease_held` 谓词需新候选） |
| S2 正式 / 真人 / S3 / S4 | NOT_RUN / NOT_RUN / NOT_IMPLEMENTED / NOT_RUN |

证据入口：`evidence/rf-teaching/s2/storage-maintenance-2026-10-12/stage-checkpoint.json`。
