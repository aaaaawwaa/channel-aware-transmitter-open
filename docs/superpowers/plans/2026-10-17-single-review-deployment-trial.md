# 单次审查、试部署与回滚

用户已要求建立“一次审查 + 部署后验证/回滚”机制。本轮实现独立试部署入口，复用现有安装器，不修改 DSH 正在生成的候选或旧封存工具。

## 设计

- 一次 R2 增量审查同时覆盖修复差分、板端验收命令、时间预算和回退版本。
- 新入口在同一个 shell 中加载已验证的 `install.sh`，保留它保存的旧版本、旧 unit 和失败回滚函数。
- 基础安装完成后运行预先声明的板端验收命令；验收失败或超时触发已有回滚。即使验收命令返回 0，仍以新的独立进程/连接核对当前发布、服务和 RF 安全态。
- 回滚后再次调用现有状态检查器，只有确证旧版本安全才报告 `TRIAL_ROLLED_BACK`；否则报告 `TRIAL_RECOVERY_REQUIRED`。
- 由板端 systemd 独立作业承载，SSH 只派发一次并观察；失联、断电或重启不能被宣称为回滚完成。
- 原始 `install.sh` 的 `INSTALL_ACCEPTED` 仅表示基础安装阶段完成，整个试部署必须等到 `TRIAL_ACCEPTED` 和新读回文件才算通过。

## 实施

- [x] 先写成功、验收失败、超时、最终安全态失败、回滚失败、基础安装失败、参数拒绝的离线 shell 测试并确认红态。
- [x] 新增 `scripts/rf_teaching_trial_install.sh`，验收预算有界，不自动发射 RF，不隐式重派。
- [x] 定向测试与 shell 语法检查：11 passed（7.58 s），`bash -n` 退出 0；文档链接和差分检查通过。
- [x] 更新审查政策、AGENTS 入口和可执行 runbook。
- [x] 对本轮新增入口做一次独立增量审查：结论 **APPROVED**，复跑 11 项测试通过；[独立报告](../../reports/2026-10-17-deployment-trial-review.md)记录了真实测试结果与真板未测范围。

## 授权与边界

用户的一次试部署授权可以包含安装、明确列出的板端验收、失败后的旧版本恢复及安全读回，不再在每个步骤重复审批。涉及 RF 时仍使用已有有效接线确认和一次性研究预算；回滚不产生新的研究预算。

本计划最初实现与离线验证阶段未调用板卡。**2026-10-17 接续真板演练已完成**：新候选非发射验收、预定退出42、自动回退与独立读回通过；该具体范围已 BOARD_VERIFIED。报告见[试部署真板收尾](../../reports/2026-10-17-trial-rollback-board-closeout.md)。

## 验证记录

- 测试夹具最初用了 Python 3.8 不支持的 `Path.write_text(newline=...)`，已改成显式 LF 字节写入；该环境错误不计入功能红态。
- 入口尚不存在时 10 个定向用例失败；新增入口后 10 项通过。随后新增 timeout 不支持所需选项的用例，先失败（已经安装后才回退），补齐安装前能力检查后全部 11 项通过。
- 检查命令：`python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-deployment-trial-green2 -q scripts/tests/test_rf_teaching_trial_install.py`。
- 旧的四件部署工具 `rf_teaching_install.sh`、`rf_teaching_install_state.py`、`rf_headroom_deploy.py`、`rf_headroom_job.py` 与当前 HEAD 相同，未修改 DSH 的既有部署输入。
- 验收预算只限制新增验收命令，不限制基础安装、回退和最终状态检查的总时长；真实存储阻塞、断电、重启与强杀仍可能要求恢复处理。独立报告已核对并保留这些边界。
