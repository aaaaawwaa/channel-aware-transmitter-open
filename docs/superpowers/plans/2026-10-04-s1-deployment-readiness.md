# S1候选部署准备 Implementation Plan

**Goal:** 沿用S1 Task 5/6的候选安装与回退要求，完成本地可审阅安装包、前后状态检查和失败留档。没有S0真人确认时只执行prepare，不切换板端发布。

**Architecture:** 独立主机工具从已验证不可变候选构建安装tar及源路径镜像；board wrapper沿用既有installer，校验旧993ffe21/新c3bf915c与safe状态后才停服务，失败恢复current和原服务文件。保持默认status_only，不布防、不发射、不引入第二Overlay。

**Files:** Create scripts/rf_teaching_deploy.py、scripts/rf_teaching_install.sh、scripts/rf_teaching_install_state.py、pynq_overlay/sw/tests/test_rf_teaching_deploy.py；证据 evidence/rf-teaching/s1/deployment-2026-10-04/。旧M4安装脚本保留。

- [x] RED：错合同/错发布、缺失/篡改/重复tar成员、缺真人结果或关闭未确认均拒绝。
- [x] GREEN：prepare从候选bytes构建完整包并独立核对tar/hash；不从后续dirty源码拼入。
- [x] 安装driver联网前校验包与S0收据；上板wrapper初始safe/旧发布准确、已有新目录拒绝，前后校验并记录回退。连接/超时/取证失败保存未知状态，不盲重装。
- [x] 定向测试及独立审阅；本地prepare生成hash收据。S0确认后才执行install。
- [x] S1安装后实际版本/服务/状态核对；新33帧及ARM计算均通过，实体屏门禁仍由用户执行中。

新20项、相关67项回归PASS；统一板/主机完整状态谓词，回滚误资格与foreign active marker负控RED→GREEN；独立复审Approved。bundle-v3实际安装成功，30/30同步、0/4140、3OFF、33帧重放通过，完整计算100次P95=187.18ms。4条真实已采IQ历史记录ARM写读/主机重放通过，不增算物理帧。报告docs/reports/2026-10-04-s1-board-validation.md。

本补充延续已批准范围：不开始S2，不改变冻结算法或候选发布ID。当前分支codex/s1-teaching-observation，原有dirty树保留。
