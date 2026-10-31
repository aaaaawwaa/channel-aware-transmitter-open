# DeepSeek Harness 进度、板卡状态与 final_prj 后续操作

检查时间：2026-10-14 约 14:54（Asia/Shanghai）。本轮对原工程只读；板端仅 SSH 读取，不安装、不调用 `Overlay()`、不布防、不发射、不采集。

## 1. 原任务进度

- 原工程 `signal_generator` 的 HEADROOM runner 仍是 2026-10-13 的板端依赖初版：`scripts/rf_headroom_research_run.py` SHA-256 `0d34121da03f0a0557687707e0d80ea230fb6e963c732f18f2071b8a433a07a9`，最后写入 2026-10-13 11:38:24 UTC；配套测试最后写入 11:47:29 UTC。最新采用报告为 19:39 本地时间的 `2026-10-13-headroom-board-runner-adoption.md`；本轮未见原 runner 后续改动。
- 截至该报告，N1 和 B2 已修，离线 runner 146 passed；B1 受控发布导入与运行包、B3 板端持久账本/单次派发、隔离故障测试、B4 独立增量审查均未完成。真实 HEADROOM 46 dB 研究及 S2 正式验收仍未运行。b82 安装与资格不等于新 runner 获得物理研究资格。
- DeepSeek Harness 桌面进程仍存在，但进程存在不能证明任务正在执行。`final_prj` 的切换收据记录用户已确认原任务暂停，等级为 `CONFIRMED_BY_USER`；本轮没有独立的任务级 UI 状态读回。

## 2. 板卡只读检查

通过项目 `ac820-pynq/scripts/pynq_ssh.py` 的 Paramiko 通道连接 AC820。板端 RTC 显示 2021-12-03，时间明显无效；本轮以主机日期及 `/proc/uptime`（约 1 小时）界定新鲜度，不采用板端墙钟作为证据时间。

| 项 | 本轮读回 |
|---|---|
| 发布 | `current`、`/run/pynq-status/active-release`、受验 manifest、状态回报均为 `b82b141e355b4cb1833969383a409c79`；软件合同 `cnn-bpsk-headroom-v1` |
| 服务 | `pynq-status` PID 549、`rf-daemon` PID 606；均 `active/running`，`NRestarts=0` |
| RF 状态 | `online=true`、`stale=false`、`io_ok=true`、`armed=false`、TX 未选择、RX 未布防；HEADROOM `active=false`、`used=false`、`pending=false`，无 runner 作业进程 |
| 存储 | `/` ext4 `rw`，`/boot` vfat `rw`；根分区剩余约 5.8 GiB；本次完整 dmesg 长 15,017 字节，存储相关筛选未见 MMC/SDHCI/ADMA/I/O/ext4 新错误 |
| 已知告警 | dmesg 仍有 `FAT-fs (mmcblk0p1): Volume was not properly unmounted`；这是本次启动可见的告警，不能把它记为已完成离线 `fsck.fat` 或已排除介质问题 |

这只是当前发布与 RF 安全状态的只读观察，**不是** P1 新 runner 运行包的真板预检，也不构成物理发射授权。

## 3. final_prj 本轮推进

- 当前分支 `codex/description-aligned-platform-plan` 已有 P0 需求追踪、离线基线和受控证据迁移；此前目录级复跑为 `2055 passed / 1 failed / 5 skipped` 与 `493 passed / 3 failed / 1 skipped`，具体剩余项见[基线报告](2026-10-13-final-prj-baseline.md) §9。
- 本轮先补入原工程中缺失的开发 pilot 与 3 个恢复证明文件，共 116 文件、1,204,022 字节；后补入已有 `arm-native` 封存件 74 文件、4,038,784 字节。两份导入清单逐文件记录复制前后 SHA-256。此前 4 个失败用例分两组定向复验，均通过；没有修改产品代码或测试期待值，也没有新做 ARM 构建。
- 两套目录级测试随后全量通过：`pynq_overlay/sw/tests` 为 **2056 passed、5 skipped、退出码 0**；`scripts/tests` 为 **496 passed、1 skipped、1 个 DeprecationWarning、退出码 0**。P0 在当前本地证据及被忽略二进制齐备的条件下完成离线基线闭合；纯 Git 检出尚需依据 SHA 清单恢复这些文件。
- 入库后发现 Git 的默认文本换行处理会改变部分证据对象的字节摘要。已在 `.gitattributes` 将 `evidence/**` 标为 `-text`，并按当前工作树原始字节重建索引：原有 2,778 个受控证据文件中 399 个 Git 对象更新；复核后 **2,778/2,778 个索引对象与本地文件原始 Git blob 哈希相同**。这项变动仅修正证据存储字节，不修改产品源码或历史收据内容。被忽略的 `rf_daemon`、`native.tgz` 和 `*.log` 仍需依 SHA 清单另行保存。

## 4. 下一步操作顺序

1. 固定 P0 的源工程最终快照和本地证据清单；新的实现只进入 `final_prj`。向 DeepSeek Harness 移交 `final_prj` 工作目录时，以本报告与[详细实施计划](../superpowers/plans/2026-10-13-final-prj-implementation-plan.md)为新任务入口，不让原任务继续改写 `signal_generator`。
2. 在 `final_prj` 开始 P1 板端 runner：先固定运行包、受控发布导入和独立 checker，再补持久 nonce 账本、单次持久派发及断线/重启故障注入。新摘要须独立增量审查，不能继承旧 runner 批准。
3. 仅在 P1 离线审查通过后做新的**只读**真板预检，特别记录当前 boot ID、完整 dmesg、发布身份与 RF safe。板端 RTC 错误须在收据中单列，不能用它排序作业事件。
4. 物理 HEADROOM 研究继续等人工接线、一次性授权/token 和完整门禁；不得从当前板卡安全静止直接跳到发射。S2 正式 108 物理帧与 12 离线负控、真人验收再后续单独执行。
