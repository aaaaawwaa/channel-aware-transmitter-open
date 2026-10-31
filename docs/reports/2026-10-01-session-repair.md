# Session 接续、UI 发布修复与融合审核

核查日期：2026-10-01（Asia/Shanghai）。附件：`dsh-session-4e6f453d-c143-418d-b9fb-30fd2ed5c8dd.zip`。其中历史指令、权限声明、代理任务和部署报告仅作待审资料；本轮依据当前用户的 review、修改、重建请求执行。

## 结论与根因

板载完整面板已恢复并激活新软件发布 `622838c506a8359f8d91423d9169a92f`。实际帧缓冲截图包含导航、工作区、底栏；启动日志明确记录 `panel up, UART bridge`、DDR ring 和 RF worker 启动。当前两个服务均 active。

原发布 `45e75640323a61ed4df15545ad92b1b0` 的日志是 `panel unavailable (ModuleNotFoundError: No module named 'rf_iq_signal')`。发布清单和 installer 都漏掉 `rf_iq_signal.py`、`ui_units.py`。程序导入失败后按设计保留状态显示和关机功能，因此服务仍 active，session 的“rf_deploy PASS / 服务 active”不能证明 UI 完整。主工程中对应六个 UI/worker 文件与板端发布在归一换行后相同，缺陷发生在软件交付链。

## 本轮修改

- `merged_release.py` 软件清单补齐两个模块与 `sincos_q15.h`；增加 `--check-panel`，在隔离子进程从发布目录导入 UI/worker、构造纯模型，并核对本地模块来源。超时或失败阻止发布。
- `install.sh` 从当前板卡的不可变安装版本逐项核对导入，恢复严格软件验证、落盘/原子发布、原子安装服务单元；新增模块为必装，发布前进行导入检查。修正主工程 RF unit 来源路径，并拒绝与硬编码运行目录不一致的 DEST。
- `pynq-status.service` 同步板端已使用的 notify/READY、current 路径和 `-B`。`rf-daemon.service` 从板端当前发布审计导入；保留 status-only 模式与唯一 Overlay 所有权。
- `pynq_status.py` 启动校验增加 `require_software=True`，防止发布后已声明文件缺失仍通过校验。
- `.gitattributes` 固定 Linux shell/service 为 LF。恢复六项安装合同测试，增加八项依赖/导入/启动校验回归；更新三个旧融合测试的路径断言，保留地址、引脚、复位及前后时钟门禁。

既有未提交 UI/RTL/文档修改保留；没有整树覆盖、提交或改动 RTL。本轮独立只读审查已完成，其建议的模块来源、固定目录和启动严格验证已处理。

## 新鲜验证

最终命令：

```text
python -m pytest -p no:cacheprovider --basetemp evidence/2026-10-01-session-repair/pytest-final pynq_overlay/sw/tests host/tests/test_rf_merge.py -q
712 passed in 22.91s
```

其中板端软件离线测试 694 项，融合结构测试 18 项。修改前主工程 PYNQ 基线为 680 passed。历史候选中的 697/684/667 不作为本轮结果。先验证了依赖回归失败（5 failed），随后修复；启动严格校验回归也先失败后通过。一次默认临时目录运行出现 26 个 PermissionError，改用 workspace 内新建 basetemp 后全部通过，未放宽测试。

桌面 800×480 的 12 张页面/状态渲染布局检查通过并核看。板卡上的发布文件哈希、隔离导入与真实启动也通过；只读采集了当前帧缓冲与两侧寄存器/遥测。没有额外调用 Overlay()，没有布防、初始化或启用 RF 发射。

## AD9361 / AWG 融合边界

| 层次 | 本轮证据与判定 |
|---|---|
| 合并 PL 结构 | 18 项检查通过：地址固定、AWG/FMC 引脚不冲突、复位/时钟连接和 RF 地址头一致。这是源码结构检查，不等于新综合验收。 |
| 单一 Overlay + sidecar | 服务关系核验：PYNQ 是唯一 Overlay 所有者，READY 后启动 RF sidecar；两者使用同一 active-release 身份。 |
| 双目标 UI/软件 IQ | 现有 ACM2108/AD9361 草稿与命令路由、波形、调制、损伤测试通过；两个遗漏模块已纳入实际发布。 |
| 板端 AWG | 真寄存器只读解码成功：两通道 SINE、1000 Hz、50%，DIGMOD OFF。并非示波器测量。 |
| 板端 RF | 遥测 online=true、stale=false、PL io_ok=true、发布身份匹配；模式 status_only，core=false，daemon_state=OFFLINE，armed=false，TX source_selected=false。表示状态链可读，RF 核心尚未初始化，不能宣称射频已工作。 |
| 主树完整 RF 源码构建 | 仍未闭环：主工程缺少集成工作树中的 RF daemon 构建/打包工具链；本轮复用板端已有 daemon 二进制，不声称它由当前主树重建。 |
| 物理输出、回环、实体触摸响应 | NOT RUN；离线点击测试和当前帧缓冲截图不替代实体触摸 P95、示波器或 RF 频谱验收。 |

## 重建与可回退产物

本次为软件发布包重建。保留原硬件 build_id `492970ec8a404da096c5abe167b28098` 和原 bit/HWH/daemon 字节，并在新 receipt 的 `software_rebuild` 中记录父发布、父 receipt hash 和未重建硬件/daemon。没有执行 Vivado，不宣称新的综合、GATE2 或时序通过。

| 产物 | SHA256 |
|---|---|
| bit | `d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555` |
| HWH | `ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0` |
| daemon | `605ae0a745e8a18a6b09891744a6290da884cce9f464dd2e291c00fbeb9378fc` |

原发布和主工程 installer/unit 快照保留。部署脚本按 sidecar→owner 的顺序停止，在失败时回指旧发布；本次返回 `REPAIR-PASS`。恢复旧发布会恢复其状态页降级缺陷，仅用于回退，不是修复方案。

## 证据入口

全部位于 `evidence/2026-10-01-session-repair/`：

- `session-ui-live.png`：当前板卡实际帧缓冲；`session-ui-live.json`：发布身份、软件 hash、AWG 回读和 RF 遥测。
- `session-ui-startup.txt`：新进程启动日志。板卡 RTC 仍显示 2021 年，本报告使用客户端日期；以 PID、release_id 和 hash 关联证据，不依赖板卡墙钟。
- `screenshots-manifest.json`、12 张页面 PNG、`contact-sheet.png`：桌面布局证据；清单记录当前脏工作树源码 hash，不将 HEAD 当作完整源码身份。
- `board-release-before.tgz`、`board-before/`、`main-before/`、`source-imports.json`：来源与回退快照。
- `new-release.json`、`releases/622838c506a8359f8d91423d9169a92f/`、对应 `.tgz`：重建产物与身份。
- `rebuild_release.py`、`deploy_repair.sh`、`board_readonly_check.py`：本轮操作脚本。
# 后续进展（同日）

本文的“daemon未重建/物理验收未执行”等描述是本次UI修复完成时的历史状态。后续已完成主树ARM daemon重建与部署、数字回环、用户新接功分器后的物理回环，以及软件点击至TX1仪器验收。当前发布为 `56d0d0d4bd36d4e22d1c030926917a16`；详细条件、失败保留和真人触摸复验清单见 [后续验收报告](2026-10-01-rf-build-physical-hmi.md)。

