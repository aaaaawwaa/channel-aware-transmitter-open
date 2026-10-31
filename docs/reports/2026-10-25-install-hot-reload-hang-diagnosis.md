# 安装后整板失去响应、断电后新版本可运行：初步定位

日期：2026-10-25。当前 Codex 对话离线检查。未连接板卡、未重启、未安装、未执行 MMIO 或 RF 操作。

> **后续原始日志复核补充（同日）：** 本次 QAM 事务已明确发现安装包缺少 `unpacked/pynq_overlay/sw/install.sh`，在停止服务后调用安装入口失败，并打印进入回退。重启后实际 current/active 为父 bedf。不能将本次说成“QAM 已切换、在启动新版本时卡死”；具体卡死点需优先查回退的服务重启/PL 运行恢复。下文硬件收尾分析仍是有源码依据的排查方向，尚非已证直接根因。具体 blocker 及前次审查纠正见 `../reviews/2026-10-25-qam16-install-archive-layout-blocker.md`。

## 已确认的现象

用户补充：新安装后 SSH 失联时，屏幕及所用串口也不能响应；通过断电重启恢复，发现新版本已经部署。这将问题定位优先级从 SSH 通信故障提升为安装期间的板端系统/硬件运行卡死。串口在正常运行时由 AWG bridge 占用，不能把没有 Linux shell 单独当成卡死证明；本次结合屏幕无响应、SSH banner 不返回和需断电恢复综合判断。

最近一次 QAM 事务已有一次 start，安装前 boot 为 `1e08d422-dcfe-4c62-ae7d-9101fd9a0d67`，之后没有安装终态/验收/独立读回。重启后文件存在或 current 指向新版本说明发布阶段可能已完成，不证明原 trial 的完整验收或回滚事务成功。恢复后应分别报告“实际安装版本、重启后验收结果、原事务终态”。

历史 RRC v3 的 2026-10-24 部署报告记录过同 boot 的正常切换，可作为成功对照。现有证据支持反复发生的热切换缺陷，尚不能证明任何版本/任何硬件状态下都必然卡死。

## 从实际源码核对的事实

1. `scripts/rf_teaching_install.sh` 停止 `rf-daemon` 和 `pynq-status`，激活发布后再启动两者。候选安装器先完成不可变 release、current 和 unit 的发布及 sync，随后才由外层重新启动服务。因此卡在后半程仍可能留下完整新版本，断电后新服务会按该 current 冷启动。
2. `pynq-status.service` 没有 ExecStop，`pynq_status.py` 没有 SIGTERM/SIGINT 的受控退出路径；长按关机中的 `screen.stop()` 不会因为普通 `systemctl stop` 自动运行。
3. `_PanelScreen.stop()` 目前只停止 bridge/RF worker，不关闭 `self.lcd`，也不提供完整的硬件静默确认；`Lcd.close()` 确实存在，但没有接入服务正常停止路径。
4. `load_overlay()` 每次服务启动都会 `configure_ps_clocks()` → `Overlay(bit)` → `configure_ps_clocks()`。停止 CPU 进程不等价于已停止 PL 的自主 DDR/AXI 事务；软件停止与硬件静默需要分别证明。
5. 此次 QAM 候选与父 bedf 的 bit、HWH、pynq_status.py、pynq_lcd.py、两份服务 unit 及 RF daemon 字节相同。软件功能更新仍重走时钟配置和位流下载；这是需要避免或安全封装的实际热重载路径，不是新 QAM 位流差异证据。
6. LCD RTL 的 `fb_en` 经同步驱动 `fb_axi_reader.run`。该 reader 在 `rst || !run` 时直接重置控制状态，`m_axi_rready` 恒为 1；没有对软件公开的“已禁止新请求且所有在途事务已排空”的确认接口。不能仅凭 `Lcd.disable()` 的写入返回就声明已经完成硬件排空；也不能把这里的源代码风险直接宣布为本次整板卡死根因。

## 优先假设及当前边界

最高优先检查服务退出时的 PL/DMA/帧缓冲硬件收尾，以及随后时钟/Overlay 重载时仍有在途 AXI 事务的情况。项目已有 Overlay 争用和 FCLK/AXI 卡死的历史经验，症状相似，但当前 unit 的 Requires/After/BindsTo 和基础 stop 顺序本身存在，不能凭历史就认定这次是双 owner。

需同时保留 MMC/SDHCI/I/O 故障、OOM 或驱动重配置故障等可能性；安装前 ext4 正常不能排除安装中出错。SSH banner 失败不支持优先更换认证、延长等待或反复重连；TUN 可能代理 ping/TCP，不能把这两项当作内核存活的充分证据。

## 最小下一步

1. 断电恢复后只读收集 current/active、boot_id、unit/journal、持久 stage 状态、内核存储/OOM 信息、RF 空闲和服务归属。保留原事务 UNKNOWN，不再次安装；若新版本已运行，先做无 RF 的重启后验收，另写新报告，不补造旧 TRIAL_ACCEPTED。
2. 在下一次受控切换前补齐持久阶段标记及及时输出：停止 RF 前后、停止 owner 前后、current 已切换、启动 owner、时钟配置前后、Overlay 前后、READY、验收开始/结束。每条携带 boot_id/单调时间，关键阶段落盘并同步；主机增量读取输出，不能只等 SSH EOF 才保存全部 stdout。
3. 离线先复现和修正“SIGTERM 未进入资源收尾”的软件缺口，停止新请求、停止并等待相关工作线程、明确 LCD/ring/RF 的所有权与退出顺序。不会仅加 sleep 或 Lcd.close 就宣布根因修复；需要核对在途 AXI 的真实排空机制。
4. 若确需 RTL 的 quiesce/idle 握手，独立列为硬件改动，补 AXI 反压/在途突发/停止后不发新请求的测试，不混入 QAM 功能候选。若选择软件更新重启激活路线，也需作为明确的新部署流程验证；Linux warm reboot 与用户已观察到的断电恢复不可直接等同。
5. 短期不要将“安装后强制断电”当作默认部署流程，不让 watchdog 自动掩盖卡死。恢复后验收与原热切换结果分开，QAM 算法/ARM 证据继续按其既有范围有效。

## 对已有审查的补充

`docs/reviews/2026-10-24-codex-qam16-trial-ready.md` 已核对包字节、离线功能和复用的事务入口；它不证明热重载在所有运行状态下都稳定。此次用户现场补充和失败事务是新增运行证据。后续不应继续原样重复热安装：先恢复本次实际状态，再针对切换路径做一次有日志的增量修复与验证，不重做未受影响的调制算法全审。
