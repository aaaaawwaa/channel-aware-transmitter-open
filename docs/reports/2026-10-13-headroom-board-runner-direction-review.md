# HEADROOM 板端 runner 方向复核与下一阶段执行报告（2026-10-13）

## 1. 决定与边界

**采纳 DeepSeek Harness 提出的板端形态，但当前实现尚不能上板启动研究。** runner 在 AC820 上运行、使用已安装 b82 发布内的 `RfStatusWorker` / `RfStatusClient`，符合既有生产控制链；主机只通过项目已有的 Paramiko/SSH 通道投递、观察和取回证据。此阶段不增加 SSH 端口转发、板端代理或私有 RF 协议，也不修改已封存发布与 daemon。

这个决定仅确定实现架构。真实接线记录、一次性授权和 token、离线故障注入、独立审查及只读上板预检仍是物理研究前的门。不得因为安装 b82 或离线测试通过就开始 46 dB 研究。

## 2. 当前事实与本次核验

| 项 | 已核实结果 |
|---|---|
| 板卡发布 | `current == active == b82b141e355b4cb1833969383a409c79`；`pynq-status` 与 `rf-daemon` 均 active/running、`NRestarts=0`。本次仅做只读检查。 |
| runner 增量 | `scripts/rf_headroom_research_run.py` SHA-256 `588e84cd831e1c0a92c88845d26268f2dc6acf5afa0ef6ac94bbddd95686fa3e`；已从离线 runner `2ee0afe8…` 变为板端依赖初版。 |
| 离线收据 | `headroom-runner-2026-10-12/receipt.json` 记录 `passed=true`、`board_contact=false`、`physical_headroom=NOT_RUN`；runner 测试 146 passed，缺席实现红态 109 failed / 37 passed，变异 34/34 检出，冻结套件 155 passed。 |
| 已封存候选 | `seal-stage.py --verify` exit 0；该检查仅证明候选与原生资格链有效，**不等于新 runner 的板端封装或真实传输已获审查**。 |
| 真板 Python 环境 | 按工程规定的 `PYTHONPATH=/home/xilinx XILINX_XRT=/usr`，即使用 root，`importlib.util.find_spec('rf_rx_analysis')` 和 `find_spec('scripts')` 均返回 `None`。 |
| 发布目录权限 | `xilinx` 不能列出 `/home/xilinx/overlays/awg_merged/current`，root 可以；发布内有 `rf_status_worker.py` 和 `rf_rx_analysis.py`，没有 `rf_teaching_install_state.py`。 |
| 内核读取 | `xilinx` 执行 `dmesg --color=never` 返回 0；这只验证当前命令可运行，不代替每次研究前后的完整存储快照。 |

上一阶段的 b82 安装与独立验收仍以 `docs/reports/2026-10-13-headroom-install-execution.md` 为准；本报告没有安装、装载 Overlay、布防、发射或采集。

## 3. 审查发现

### B1（阻塞）：发布依赖和独立读回工具没有可执行的板端封装

`release_client()` 与 `release_token_reader()` 通过路径加载发布内模块，但这些模块还会按模块名导入 `rf_rx_analysis`、`rf_status_client` 等依赖。当前 runner 没有在板端受控地建立发布模块搜索路径；真板默认环境找不到这些依赖。`independent_readback()` 及最终 `verified_state` 又直接从 `scripts.rf_teaching_install_state` 导入，真板上既没有该 `scripts` 包，也没有发布内同名文件。位置：`scripts/rf_headroom_research_run.py:672-739,1500`。

现有离线测试在文件开头主动把仓库 `pynq_overlay/sw` 放入 `sys.path`，并向 `default_dependencies()` 注入 inspector。因此 146 项通过证明接口形状和离线逻辑，但没有验证真板的启动路径。按现有布局，研究不应被派发；即便绕开前面的导入障碍，研究后的独立读回也无法按默认路径完成。

### B2（阻塞）：最终读回导入异常未被收束为门禁结局

`scripts/rf_headroom_research_run.py:1500` 的 `from scripts.rf_teaching_install_state import verified_state` 位于该处 `try` 之外；`execute()` 在 `postflight()` 之后没有包住这类异常。若研究已经执行，缺失的 checker 可能使进程直接抛出 `ModuleNotFoundError`，不能稳定写出 `RUN_UNKNOWN_READBACK_INCOMPLETE` 与 `run-gate.json`。必须先修复，再考虑任何物理会话。

### B3（阻塞）：板端启动与防重放的持久边界尚未固定

当前 CLI 要求 `--ledger`，但帮助与 `_consume_authorization()` 文档仍称账本位于 host；设计文档的真实命令同样写“主机侧追加式消费账本”。在板端形态中，账本必须落在板端持久、受控、非发布目录，断线和重启后仍能拒绝同一 nonce。runner 代码尚无受审的板端运行包、投递/摘要守卫、持久作业与只读观察收据。通过交互 SSH 启动研究会使断线后的执行历史难以判定，且绝不能靠再次派发消除未知。

### B4（审查门）：旧 runner 独立复验不覆盖新摘要

先前第三方复验绑定 `2ee0afe8…`；板端增量现为 `588e84cd…`。新离线收据可以作为开发证据，但不能继承旧摘要的独立批准。封存候选 b82 和已完成安装无需因 host/板端 runner 改动而重做 ARM G1；**新 runner、启动包、导入/权限路径及失联结局需要自己的独立 delta 审查**。若改动冻结发布文件，才须重走候选资格、发布和安装门。

### N1（非阻塞但需清理）：旧叙述与新实现并存

同一文件目前有两个 `default_dependencies()` 定义，前一个离线空实现被后一个板端实现覆盖；文件顶部仍写“offline-only、无 subprocess”，CLI 帮助仍写“needs injected board access”。这些文字已不符合运行行为，影响后续审查。应随本次 delta 一并校正，不应借此放宽任何门。

## 4. 目标板端形态

1. **固定运行包与发布身份。** runner、独立只读 checker 和必要的启动件放在发布目录外、root 管控的版本化目录；记录每个文件 SHA-256。运行前把 `current` 解析为一次固定的 b82 实路径，按已审查 manifest/冻结摘要核验；生产模块始终从该实路径加载。为传递性导入建立只指向该已核验目录的受控搜索路径，不依赖仓库测试注入或偶然的系统模块。
2. **权限与环境。** 使用板端经审查的 root 运行入口和 `python3 -B`，保留工程所需 `PYTHONPATH=/home/xilinx`、`XILINX_XRT=/usr`，显式加入已核验发布路径与 runner 包路径。不得调用 `Overlay()`；PL 由现有 `pynq-status` 服务持有。token 文件只读，值不进入命令行、日志或证据。
3. **唯一控制链。** `G3` 身份探针和 `G4` worker 预轮询使用已有发布客户端；研究只通过同一个 worker 的 `headroom_run()` 进入 controller，保留其一条研究连接。最终验收用另外一次只读会话/进程和受审 checker；任何读回失败维持未知结局，不以研究收据自证安全。
4. **持久一次性执行。** 主机经 Paramiko/SSH 上传并校验包摘要，派发一个持久板端任务后只观察该任务；SSH 断线不得杀死任务、不得重派。板端 nonce 账本使用持久路径及追加/fsync 语义；证据位于独立根目录，既不写发布目录，也不混入教学记录目录。任务收据绑定 release、runner 包摘要、authorization ID、nonce、attempt ID、boot ID、阶段与最终状态。

## 5. 给 DeepSeek Harness 的执行顺序

1. **先固定封装契约。** 写明运行包文件清单、板端绝对路径、root 权限、导入顺序、持久账本/证据路径、任务派发与观察格式。同步修订设计文档中的“主机侧账本”与 runner 旧说明。仅做离线工作，不接触 RF。
2. **修复 B1/B2。** 让发布模块的传递性导入只解析到已核验 b82；将受审的 `rf_teaching_install_state` 放入带摘要的 runner 包，并让 inspector 与 `verified_state` 使用同一受审来源。把导入、权限及 checker 缺失归入可审计的拒绝/未知状态；避免在 `run_research()` 外提前抛出无收据异常。
3. **做板端形态的隔离测试。** 在没有仓库 `pynq_overlay/sw`、没有隐式 `scripts` 包的干净子进程中，按板端目录布局运行启动/导入测试；故障注入覆盖缺文件、错摘要、无权限、daemon 不可达、畸形或不完整 dmesg、读回中断、boot ID 变化、nonce 重放、SSH 失联与单次派发。测试须断言失败时无布防/采集且有准确结局；研究后导入失败也必须产生未知收据。
4. **独立审查新 delta。** 审查者复算 runner、checker、启动件、测试及包摘要，检查没有第二协议客户端、没有 `Overlay()`、没有研究连接重连、没有安装发布改动；复跑关键红绿与故障注入。明确旧 `2ee0afe8…` 复验的继承边界。
5. **再做只读真板预检。** 使用项目指定 Paramiko 帮助脚本，先验证包摘要、root 启动与导入、b82 身份、服务、完整内核存储文本和 RF safe；测试应停在物理动作前，并保存原始收据。任一项失败即停止，不消耗一次性物理授权。
6. **最后才申请有限物理会话。** 人工接线记录、一次性授权与 token 齐备后，单次派发、最多五尝试、独立读回；断线或不完整读回保留未知与原始证据，不重派、不自动重试。正式 S2/S3/S4 另按其审核门执行。

## 6. 完成判据

本阶段“板端 runner 已可进入物理研究门”只能在以下事实同时成立时声明：

- 板端运行包的受审摘要与实际上传件相等，root 入口能从固定 b82 实路径加载生产模块和独立 checker；在无仓库测试路径的环境中通过。
- 所有 G0–G4 失败均返回具名拒绝结局；证据介质不可写时明确返回 `RUN_EVIDENCE_WRITE_FAILED`。研究一旦开始，任何读回/存储/断线失败均有未知或停止结局；导入异常不得在可写条件下使 `run-gate.json` 消失。
- 一次性板端账本在进程重启和 SSH 失联后仍拒绝同一 nonce；持久任务的重复观察不等于重复派发。
- 离线完整套件、针对板端形态的故障注入、冻结套件和变异测试通过；新摘要完成独立审查。
- 真板只读预检确认发布、权限、模块导入、服务、存储与 RF 安全状态。**此项仍不产生物理帧。**

当前状态为“板端依赖初版 + 离线绿灯；真实运行包与真板只读预检未完成”。建议采纳板端方向，并按 B1–B4 补齐后再进入物理门。
