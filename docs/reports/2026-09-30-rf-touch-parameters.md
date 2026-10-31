# AC820 / AD9361 RF 触摸参数阶段报告

报告时间：2026-09-30，北京时间。最终审计完成于约 12:59。板卡时钟仍为2021年，本报告使用开发机时间，板端身份以 release、PID、boot_id 和单调时间对应。

## 1. 接续范围与结论

已读取 `<LOCAL_PATH>

**本阶段完成并上板：RF 参数录入、完整参数转发、后台控制和发布内验收。** 实机使用新发布自带的 UI/model/worker/client，完成武装 → 初始化并排队停止 → 配置 → 停止 → 解除武装。最终恢复未武装 `OFFLINE`，两服务正常运行。

这次验证的是 RF 控制链和参数返回。没有启动 TX/RX 数据路径，没有外部频谱、幅度、误码或物理回环测量；也没有重做历史失败的 OS reboot / 物理断电验收。

## 2. 实际工程与发布身份

| 项目 | 当前值 |
|---|---|
| 实际源码树 | `<LOCAL_PATH>|
| 分支 / 基线 HEAD | `codex/pynq-ad9361-integration` / `466091e` |
| 接续时板上 release | `3e36d6ac65d7306fae62fa223f796e52` |
| 最终板上 release | **`ff96b755880df100c932d753398d3c00`** |
| 最终板上路径 | `/home/xilinx/overlays/awg_merged/releases/ff96b755880df100c932d753398d3c00` |
| build_id | `492970ec8a404da096c5abe167b28098` |
| bit SHA-256 | `d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555` |
| HWH SHA-256 | `ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0` |
| daemon SHA-256 | `605ae0a745e8a18a6b09891744a6290da884cce9f464dd2e291c00fbeb9378fc` |

相对接续版本，只改变软件映射中的 `ui_model.py`、`ui_view.py`、`pynq_status.py`、`rf_status_client.py`、`rf_status_worker.py` 五项。bit、HWH、daemon、地址契约和原子安装器保持相同哈希。本轮未运行 Vivado、未进行 ARM 交叉编译，也未连接 Azure VM；现有已验证硬件产物足够支持本次软件更新。

源码改动尚未提交；没有 push、merge 或 PR。发布身份包含本轮实际软件哈希，**基线提交本身不等于本轮完整源码**。历史未提交的构建输入问题仍在积压分诊清单中，未在本轮批量处理。

## 3. 界面与参数行为

“接收”页新增“RF参数”子页。八个字段均从空草稿开始，可通过既有数字键盘编辑：本振、采样率、衰减、PHY模式、RX/TX带宽、IDELAY开关和档位。取消、空输入和越界输入不替换原值；清空仅操作本地草稿。接受参数编辑或清空会撤销尚未确认的动作。

初始化要求左栏四项；配置要求全部八项。PHY模式必须显式填写（0=2R2T，1=1R1T），配置还要求显式 IDELAY，避免省略参数导致隐含选择2R2T或清零延迟。频率/速率字段的传输表示范围是 `1..2147483647`；这不是 AD9361 的硬件可用范围保证。衰减 `0..89750 mdB`、开关整数0/1、tap `0..31`。

初始化/配置仍需二次确认，确认期间保留停止按钮。硬件操作执行时显示等待状态，参数行暂时禁用，避免全屏键盘遮住停止入口。控制线程最多接受一个正在执行的动作和一个排队停止；重复停止不重复排队，其余并发动作返回 BUSY。停止须等待 daemon 当前同步操作返回后执行，不能中断 native 初始化。

界面提交排队不算操作成功，只有实际控制响应完成后才推进状态。worker 一次性交付快照、完成结果和运行动作，轮询到发布的过程也串行，避免旧快照覆盖新状态。过期遥测仍按过期显示，没有伪造新鲜状态。

## 4. 超时缺陷与修复

接续版本的发布内客户端默认只有0.75秒控制期限。实测初始化在 **0.7517秒超时**，但随后板上已 `READY`：此前离线控制原语并不能证明真实初始化可用。

本轮将 init/configure 的控制期限独立设为20秒，轮询与短控制仍为0.75秒；网络事务串行并放到后台线程，显示线程只提交参数副本、领取结果，不执行 socket 往返。新增测试验证慢控制、等待轮询仍有自己的期限、停止排队和界面响应。

## 5. 验证结果

| 验证 | 实测结果 |
|---|---|
| PYNQ Python 回归 | **579 passed / 1 skipped** |
| RF + host Python 回归 | **845 passed / 1 skipped**（RF66 + host779） |
| 本轮 Python 合计 | **1424 passed / 2 skipped** |
| 新增行为用例 | 22项：触摸/参数14、后台控制3、完整调用链与竞态5 |
| 修复前证据 | 录入/后台接口先红；原子帧竞态及停止遮挡/轮询发布先红 |
| 独立只读复审 | 三项具体问题已修复；复审未发现剩余阻断问题 |
| 本地及板上画面 | 未填、键盘、已填、越界、确认、执行中、配置完成、解除武装；已目视检查 |
| `git diff --check`（本轮相关源码/既有测试） | exit 0，只有 Git 配置访问与换行提示 |
| C / RTL 测试 | 本轮未重跑，C / RTL / daemon二进制未改 |

离线测试与板上验收使用真实 `_hit`、按下/抬起和生产回调。板上帧缓冲为内存数组，**没有第二个 `Overlay()`，没有向物理触摸控制器注入输入**。本轮不声称人工触屏或真实 LCD 输出已验收；下载的画面使用板上实际字体渲染。

板上自动验收选用既有已验证参数：LO80MHz、采样60MHz、衰减40000mdB、PHY0、RX/TX带宽30MHz、IDELAY1/tap7。所有八项先经触摸路径录入，录入期间无控制请求；之后生产回调发送完整计划。

| 实机步骤 | 结果 |
|---|---|
| 武装 | 完成，面板 `armed` |
| 初始化确认触摸 | **0.103秒返回**，操作继续在后台运行 |
| `rf.init` | **0.892秒**；返回LO/采样率/带宽/衰减/PHY一致，`hardware_verified=true`，product_id10 |
| 初始化时停止排队 | 完成；返回READY，面板 `stopped` |
| `rf.configure` | **0.324秒**；完整八项准确传出，返回主要RF参数一致；面板 `configured` |
| 再次停止及解除武装 | 完成，面板 `read_only`，daemon `OFFLINE` |
| 全部210帧的最慢单帧 | **0.236秒**（本次内存绘制测量，不是物理LCD帧率保证） |
| 最终脚本 | `TOUCH_UI_PASS` / `BOARD_EXIT=0` |

daemon 当前响应没有返回 IDELAY 字段，故本报告只确认延迟参数准确发送且 configure 成功，**没有把它写成 tap 寄存器读回或物理时序验收**。1R1T和其他参数组合本轮未进行硬件验证。

## 6. 部署与最终运行状态

经既有 `rf_deploy.plan_merged` 校验后停止两服务、上传到非活动 staging、使用原子安装器发布，再由唯一 PYNQ 所有者装载。部署脚本的启动瞬时快照曾显示 `active/failed`，后续独立检查证实 sidecar已启动；报告以最终审计为准，不以该瞬时输出宣称两服务就绪。

最终核验完整软件哈希（`require_software=True`）、current与active路径一致、运行中 daemon哈希一致、两 unit非空且与发布内哈希一致：

```text
pynq-status: MainPID=19761, loaded, active/running, NRestarts=0
rf-daemon:   MainPID=19768, loaded, active/running, NRestarts=0
online=true, stale=false, error=null
armed=false, core=false, daemon_state=OFFLINE
PL io_ok=true, TX source=false/length0, RX armed=false
/dev/mem: r--s，offset 43000000
boot_id=aad61eef-f748-4387-b4fa-cbfc7b190920
FINAL_AUDIT_PASS / BOARD_EXIT=0
```

验收工具三次试运行均保留日志：首次轮询启动过晚造成状态过期；第二次在确认回调后尚未刷新下一帧就检查编辑禁用，初始化实际已成功；修正脚本时序后完整通过。最终审计工具也保留了把路径标记误当JSON的失败日志，改为按源码定义读取路径后通过。这些工具失败没有被当作产品验收成功。

板上工具导入AWG模块时仍出现缺少 `sdk/src/sincos_q15.h`、使用近似表的既有提示；本次没有生成或发送AWG数据，不将RF控制验收扩展为AWG系数一致性结论。现有顶部“RF未接入/OBSERVE ONLY”文案仍按接收采集能力显示，不能据此判断RF控制失败；本轮控制结果以底部状态和daemon响应为证。

## 7. 后续工作

1. 按来源整理、审查和提交历史构建输入及本轮软件，形成可从提交重建的版本；不整体搬运两个工程的积压修改。
2. 配合人工触屏/LCD检查，并区分RF控制可用性与接收采集能力文案。
3. 准备持续串口捕获，再定位历史 OS reboot 无响应；本轮没有重现或归因。
4. 另立计划推进TX/RX数据路径、物理回环与外部测量。`hardware_verified`不能替代这些测量。

## 8. 报告、证据与操作入口

报告：`<LOCAL_PATH>`

证据：`<LOCAL_PATH>`

关键文件：`control-timing-baseline.log`、`tests-expanded-red.log`、`atomic-frame-red.log`、`ordered-poll-red.log`、`pynq-regression.log`、`rf-host-regression.log`、`release-delta.log`、`deploy.log`、`board-touch-pipeline.log`、`final-board-audit.log`、`board-preview/*.png`。本轮修改前源码备份在 `before-touch-ui/`。

板载操作：进入“接收 → RF参数”，填写所需字段后武装，初始化/配置再次确认。草稿不跨进程保存；模式切换要先解除武装、重新武装并初始化。当前板卡已恢复未武装，用户按操作目的重新武装。

集成树交付记录新增§47，进度账本和 `docs/rf/phase2-board-handover.md` 同步更新。旧阶段报告保留历史数据，本报告是本轮参数录入进度的最新补充。
