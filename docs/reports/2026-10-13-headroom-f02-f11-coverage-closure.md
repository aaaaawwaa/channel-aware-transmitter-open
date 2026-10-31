# F02/F11 冻结套件覆盖缺口闭合（2026-10-13）

## 0. 结论

**两个缺口已闭合，且没有改动任何冻结文件。**

| 项 | 结果 |
|---|---|
| 变异台架 | **34 total / 34 caught / escaped [] / unexpected 0**（此前 32/34，逃逸 F02、F11） |
| runner 套件（绿） | **141 passed**（原 139 + 2 条新覆盖） |
| 缺席实现（红） | 108 failed / 33 passed |
| 四个冻结套件 | **79 + 34 + 26 + 16 = 155 passed**（未改动、未重写） |
| `receipt.json` | `passed=true`；46 软件 / 22 绑定文件 / 58 原生输入全等；`seal-stage.py --verify` **exit 0** |

**做法**：不去改冻结套件（改冻结文件＝重新审查＋重新封存），而是把覆盖写进**非冻结**的
`scripts/tests/test_rf_headroom_research_run.py`，再把变异台架里 F02/F11 的节点指过去。
执行文档允许"补强**或**给出独立可审查的限制说明"——本阶段选"补强"，代价为零。

## 1. F02：轨道/终态等价（`rf_headroom.py:442`）

- **缺口**：冻结用例 `test_a_header_that_disagrees_with_its_own_bytes_is_refused` 只断言
  `'rail' in str(...)` —— 而这是**两条**可能消息的公共子串；它的三个参数化用例**同时**破坏了
  声明的轨道数，所以计数检查（:437）先拒，等价分支从来不是拒绝理由。
- **新用例** `test_the_rail_terminal_equivalence_is_the_reason_a_frame_is_refused`：
  让**声明的轨道数与实测一致**（因此计数检查通过），只有等价分支能拒；断言**它自己的消息**
  `'the terminal state does not match the rails'`，两个方向都覆盖
  （有轨道却报 `AWAIT_ACCEPT`、无轨道却报 `CLIPPED`）。
- **变异**：`F02 baseline=0 mutant=1 caught=True loaded=True needle=True`。

## 2. F11：重连守卫（`rf_status_client.py:467`）

- **缺口**：没有任何冻结用例触及它；而且**冻结控制器从不重试**，所以该守卫在控制器路径上
  **不可达**——这正是上一轮把它记为 escape 的理由。这一事实**保留**，不粉饰。
- **新用例** `test_a_lost_research_connection_is_never_reconnected`：
  白盒地构造"丢失的研究连接留下的**确切状态**"（`headroom_lease()` 在 :508 记录的
  `_research_generation` ＋ socket 已消失），随后一次**普通读取**必须
  fail-soft 地报出 `'no reconnect'`，并且**绝不新建第二个连接**
  （`socket.create_connection` 的地址列表仍只有 1 条）。
- **诚实说明**：这是对**纵深防御**的覆盖（"即使有人重试也不会静默重连"），
  不是"研究路径会走到这里"的断言。
- **变异**：`F11 baseline=0 mutant=1 caught=True loaded=True `。

## 3. 重跑收据

`python evidence/rf-teaching/s2/headroom-runner-2026-10-12/evidence-run.py` → exit 0、`passed=true`：
绿 141、红 108 failed／33 passed、冻结 155、**变异 34/34**、冻结检查全通过、`seal --verify` exit 0。

## 4. 摘要

| 文件 | 摘要（前 16 位） |
|---|---|
| `scripts/rf_headroom_research_run.py` | `2ee0afe8d1c0d438…`（**本阶段未改动**，第三方复验仍有效） |
| `scripts/tests/test_rf_headroom_research_run.py` | `9492455deea05b9d…` |
| `…/mutation-harness.py` | `057a10719605b0c9…` |
| `…/receipt.json` | `f2b1ade8f3b4f4e5…` |
| `…/mutations.json` | `9486e28247251514…` |

## 5. 仍未做

- **真实传输接线**（下一门的主体）：`storage_probe`、`identity_probe`、`readback_probe`、
  `token_reader`、`worker_factory` 的唯一控制链，新工厂明确声明 `performs_board_read`，
  外加离线故障注入（断线、重启、畸形快照、一次性 nonce、单次初始化/最多五次尝试）。
  该实现必须先固定 **runner 的宿主形态**（设计文档第 460 行把这一项列为"待实现阶段固定"），
  并受第 82 行约束：runner 及其辅助模块**不得**自行 `socket.create_connection` 到
  `127.0.0.1:5000`。
- 人工接线记录 + 一次性 token、真实 46 dB 研究、正式 S2/S3/S4 —— 都在其后。
