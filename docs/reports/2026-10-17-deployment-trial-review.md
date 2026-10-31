# 单次审查、试部署与回滚：独立增量审查

日期：2026-10-17。结论：**APPROVED**，适用于下面注明的离线代码与操作计划；不代表已完成板端演练。

审查者未参与实现。本轮为同一差分的一次独立增量审查，不新增规格、质量、封存等重复审查轮次。受审工作从 `c649624` 后的工作树读取；检查期间 DSH 并行提交使 HEAD 前进到 `f0c2b172290fb89cd92f5fe8d6c2ab4af688cb26`。本结论按下列实际文件字节绑定，不将并行提交视为本轮实现。

## 范围与依据

受审：`scripts/rf_teaching_trial_install.sh`、对应定向测试、`docs/rf/deployment-trial-runbook.md`、当前审查规则第 6 节，以及 AGENTS / memory03 的入口衔接。另核对真实 `rf_teaching_install.sh` / `rf_teaching_install_state.py` 的调用契约与状态判定；未扩审未改产品功能。

| 文件 | SHA-256 |
|---|---|
| `scripts/rf_teaching_trial_install.sh` | `27c0675688cffdddbbb36707389fe7e88522f027a514c2926f0d75c68de36800` |
| `scripts/tests/test_rf_teaching_trial_install.py` | `e81be0ff19bfd8466de3b2dc98d549af7228c29294b6fb5e9496462c12de6dda` |
| `docs/rf/deployment-trial-runbook.md` | `6d321e2d7ff52df9dcc6b97473d7ff89c2785661e3ec815f94f95053fbb5ff81` |
| `docs/requirements/development-review-policy.md` | `fd8812092a22c00ea6252eb5d61af5763bf6a4591f088afcf9767878a59ceefb` |
| 既有 `scripts/rf_teaching_install.sh` | `ddac9d38ee34153f48edbd968e2ee36ede5e0fd0a793f48160a8ddea53430b29` |
| 既有 `scripts/rf_teaching_install_state.py` | `e40e7bd01450e873288f72602bc7ab66f714fda35db8aa03a356789742816e3f` |

## 技术结论

未发现阻塞本次可选入口进入受控板端试部署的代码问题。

1. **延长真实安装事务成立。** 基础安装器在函数内 dot-source，接收原有四个位置参数，安装后旧发布路径、旧 unit 备份和 `rollback()` 仍在同一 shell 可用；函数返回不吞掉基础安装失败。基础安装阶段失败仍由原 EXIT trap 收尾，验收不会继续。
2. **成功与失败终态分开。** 基础 `INSTALL_ACCEPTED` 后仅运行一次受审验收命令；命令失败、超时或最终状态检查失败由 `set -e` 进入新的 EXIT 处理。成功必须有最终新版本检查与 `TRIAL_ACCEPTED`，不能把基础激活日志当整个试部署成功。
3. **回滚命令结果不是回滚成功证据。** 处理器先清除 EXIT / INT / TERM trap，子 shell 调原 `rollback()`，避免其 `exit` 直接结束父处理器；随后新 Python 进程重新检查父版本。真实检查器验证发布清单、active-release、服务 active/running/PID，以及未布防、TX/RX 安全读回；不能验证则输出 `TRIAL_RECOVERY_REQUIRED`。试部署失败即使回滚成功也保留原非零退出码。
4. **超时边界准确。** 安装前检查 `timeout --kill-after` 是否支持；新增验收命令限制为 1–300 秒，到期 TERM 后 2 秒升级结束。INT / TERM 对 wrapper 走非零退出和既定回退路径。真实 systemd/cgroup 信号行为没有在本机模拟中证明，断电、重启、强杀及存储异常仍按 runbook 的 UNKNOWN / 待恢复处理。
5. **必要 ARM 冒烟可合并。** policy 第 2 节允许在声明回退计划的试部署内完成受影响 ARM 冒烟，并明确旧工具强制要求的安装前收据仍须真实满足；不产生绕过现有资格校验的入口。
6. **流程入口一致。** runbook 明确旧主机 install 和新 trial 二选一，避免重复安装；同一次批准包括验收条件和回退目标，实际 RF 研究次数/负结果是否要求回退须预先声明，不从试部署自动扩大物理研究授权。

## 独立验证

实际执行：

```text
python -B -m pytest -p no:cacheprovider --basetemp .pytest-work-trial-review -q scripts/tests/test_rf_teaching_trial_install.py
11 passed in 8.05s
```

Git Bash `bash -n scripts/rf_teaching_trial_install.sh` 退出 0；对已跟踪的 AGENTS、memory03、policy 执行 `git diff --check` 无差分错误，仅有仓库既有换行归一化提示。

11 项测试执行真实 wrapper，沙箱替代板卡安装与状态读取，覆盖保留新版本、验收失败、验收超时、末次安全态失败、回退未成功、基础安装失败、预算/命令/timeout 前置拒绝。结合真实依赖的控制流检查，足以支持本次代码结论；这些测试不宣称模拟了 systemd 或真实 RF/PL。

## 明确保留的部署边界

- 1–300 秒只约束新增验收命令。基础安装、既有回退、最终检查均没有新增整体期限；不能据此承诺整项作业在 302 秒内结束。
- 最终检查和回退后检查当前没有外层 `timeout`。既有客户端网络轮询带单调时限，两个 `systemctl show` 分别带 10 秒超时；磁盘/文件系统 I/O 仍可能阻塞。结合 runbook 已将真实存储异常列为待恢复、要求观察同一作业且禁止并行回滚，本轮没有证据将其认定为新增阻塞问题；不把它描述成全作业有界或所有故障保证回滚。
- 尚未执行真实 systemd 作业、服务重启、PL 重载、失联演练或真板失败回退。首次板端使用应按既定 runbook 先做不发射冒烟，并在同一工作包安排可控失败回退及独立读回。结果进入板端验证证据，不再另开同范围离线审查。
- 该批准覆盖机制及所述操作计划，不自动批准任意候选或任意 `acceptance.py`。实际派发包仍要填写其身份、验收条件和已知父版本；其已有批准可引用，新增行为按增量处理。

本审查未连接板卡或网络，未修改实现、旧安装器、DSH 候选链或他人工作树文件。
