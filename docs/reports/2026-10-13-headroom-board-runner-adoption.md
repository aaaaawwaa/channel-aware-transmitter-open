# 板端 runner 方向复核：采纳与执行登记（2026-10-13）

**已采纳** `docs/reports/2026-10-13-headroom-board-runner-direction-review.md` 的结论与顺序。
本轮仅登记采纳与起点，**未做任何代码改动、未接触板卡、未派发**。

## 采纳的边界

- 架构：**板端形态**（runner 在 AC820 上运行，使用已安装 b82 发布内的 worker/client；主机只经既有
  Paramiko/SSH 通道投递、观察、取回）。不增加端口转发、板端代理或私有 RF 协议。
- 该决定只确定实现架构：真实接线记录、一次性授权与 token、离线故障注入、独立审查、只读上板预检
  仍是物理研究前的门。**不得因安装 b82 或离线绿灯就开始 46 dB 研究。**

## 已接受的发现（按报告原文）

| 编号 | 判定 | 我承认的事实 |
|---|---|---|
| **B1** | 阻塞 | 发布模块按**模块名**导入 `rf_rx_analysis`/`rf_status_client`，而 runner 未在板端建立受控搜索路径；`independent_readback()`/`verified_state` 直接依赖 `scripts.rf_teaching_install_state`，**真板无 `scripts` 包、发布内也无同名文件**。现有 146 项测试把仓库 `pynq_overlay/sw` 放进 `sys.path` 并注入 inspector，因此只证明接口形状，**未验证板端启动路径**。 |
| **B2** | 阻塞 | `:1500` 的 `from scripts.rf_teaching_install_state import verified_state` 在 `try` 之外，`postflight()` 之后的异常未收束：研究已执行时缺失 checker 可让进程直接抛 `ModuleNotFoundError`，无法稳定产出 `RUN_UNKNOWN_READBACK_INCOMPLETE` 与 `run-gate.json`。 |
| **B3** | 阻塞 | 板端启动与防重放持久边界未固定：`--ledger` 的文档仍称主机侧；板端需持久、受控、非发布目录的账本，断线/重启后仍拒绝同一 nonce；尚无受审的板端运行包、投递/摘要守卫、持久作业与只读观察收据。 |
| **B4** | 审查门 | 旧第三方复验绑定 `2ee0afe8…`，新板端增量 `588e84cd…` **不继承**该批准；新 runner、启动包、导入/权限路径与失联结局需要**自己的独立 delta 审查**。封存候选 b82 与已完成安装无需重做 ARM G1。 |
| **N1** | 清理 | 同一文件有两个 `default_dependencies()` 定义（前一个空实现被覆盖）、顶部叙述仍写 offline-only/无 subprocess、CLI 帮助仍写 needs injected board access —— 随本次 delta 一并校正，不放宽任何门。 |

## 执行顺序（报告 §5，逐步执行，不跳步）

1. **固定封装契约**（离线）：运行包文件清单、板端绝对路径、root 权限、导入顺序、持久账本/证据路径、
   任务派发与观察格式；同步修订设计文档的"主机侧账本"与 runner 旧说明。
2. **修 B1/B2**：发布模块的传递性导入只解析到已核验 b82 实路径；把受审 checker 放进带摘要的 runner
   包并让 inspector/`verified_state` 同源；导入、权限、checker 缺失都归入**可审计的拒绝/未知结局**，
   且不在 `run_research()` 外提前抛出无收据异常。
3. **板端形态隔离测试**：在**没有仓库 `pynq_overlay/sw`、没有隐式 `scripts` 包**的干净子进程中按板端
   目录布局跑启动/导入测试；故障注入覆盖缺文件、错摘要、无权限、daemon 不可达、畸形/不完整 dmesg、
   读回中断、boot ID 变化、nonce 重放、SSH 失联、单次派发；断言失败时无布防/采集且结局准确，
   研究后导入失败也必须有未知收据。
4. **独立审查新 delta**（由未参与实现者）：复算 runner/checker/启动件/测试/包摘要，检查无第二协议
   客户端、无 `Overlay()`、无研究连接重连、无安装发布改动；复跑关键红绿与故障注入。
5. **只读真板预检**：包摘要、root 启动与导入、b82 身份、服务、完整内核存储文本、RF safe；停在物理
   动作前，保存原始收据；任一项失败即停止，不消耗一次性授权。
6. **最后才申请有限物理会话**（人工接线 + 一次性授权 + token）：单次派发、最多五尝试、独立读回；
   断线或不完整读回保留未知与原始证据，不重派、不自动重试。

## 执行进展（滚动）

- **N1 已修**：重复的离线 `default_dependencies()` 已删除（现在**只有 1 个定义**）。
- **B2 已修（待补专用回归）**：`verified_state` 的导入已移入 `try`，缺失/不可导入的 checker 现在归入
  **具名 UNVERIFIED 读回**，不再在 `run_research()` 之外抛出无收据异常；"缺 checker 仍写出
  `RUN_UNKNOWN_READBACK_INCOMPLETE` + `run-gate.json`" 的**专用用例尚未添加**。
- 改动后 runner 套件仍 **146 passed**（离线）。
- **未开始**：B1（运行包 + 受控发布搜索路径 + 带摘要的 checker）、B3（板端持久账本/派发契约）、
  §5.3 干净子进程故障注入、§5.4 独立 delta 审查、§5.5 只读真板预检、§5.6 物理会话。

## 当前状态

"板端依赖初版 + 离线绿灯；真实运行包与真板只读预检未完成"。本阶段声明"可进入物理研究门"的判据
以报告 §6 为准，尚未满足。
