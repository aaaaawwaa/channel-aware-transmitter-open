# DSH 离线会话变化与 TRAIN/VAL 修复建议

检查日期：2026-10-18，Asia/Shanghai。本报告依据离线 ZIP、当前 Git 差分、执行收据及一次本机无板复现；本次没有连接板卡、部署或派发 RF。工作树有 DSH 正在编辑的文件，不能将未提交补丁视作已审查或已上板。

## 进度变化

最新 ZIP：`<LOCAL_PATH>

| 阶段 | 本次确认 |
|---|---|
| e4dae554182e8c9f5f963c1d5040913a 的 ARM 资格 | 已通过，100 次、P95 262.5828 ms、RSS 31,532 KiB、parity 无差异 |
| 非发射试部署 | 已通过，真实 `TRIAL_ACCEPTED`，独立读回为 e4dae554 |
| 用户软关机后恢复 | 已有重启后读回记录；用户在会话中确认曾主动按 S0 软关机 |
| 回退演练 | 预检遇日志误报，尚未派发；不能记作真板回退成功或失败 |
| 首次 TRAIN/VAL | train-01 已派发并初始化一次，首帧失败，0 有效帧；train-02 / val-01 未派发 |
| 标定 / C 候选 / 18 帧 pilot / 正式 TEST | 尚未完成；33 帧与后续 18 帧分开计数 |

证据入口：

- `evidence/rf-teaching/s2-46db-arm-check-2026-10-17/NOTES.md`
- `evidence/rf-teaching/s2-46db-trial-exec-2026-10-17/receipt.json`
- `evidence/rf-teaching/s2-46db-postreboot-2026-10-18/`
- `evidence/rf-teaching/s2/46db-train-2026-10-18/NOTES.md`
- `evidence/rf-teaching/s2/46db-train-2026-10-18/logs/board-session-result-train-01.json`
- `evidence/rf-teaching/s2-46db-rollback-drill-2026-10-18/NOTES.md`

采集失败后日志中的板态：current/active-release 均为 e4dae554，双服务运行、NRestarts=0、RF 关闭、boot_id 未变。该事实来自 10 月 18 日上午的历史读回，不代表本报告时刻新测的板态。

## 当前问题与具体修复

### D1：预检失败后继续派发

`scripts/rf_environment_trainval_collect.py` 原来以非特权身份运行板端预检，而已安装 release 为 root 可读目录。导入失败后没有结果 JSON；下载异常分支没有执行放行断言，仍进入采集。

工作树已有 `sudo=True` 与 `BOARD_PREFLIGHT_NOT_EVALUATED` 修复。应保留这些修改并补齐结果缺失、空文件、坏 JSON、非零退出、身份/域不匹配的拒绝测试；每例都断言真实会话命令未发出。无需修改发布目录权限。

### D2：输出目录没有创建

首次真实错误为 `<remote>/out/normal-01.json` 不存在。板端 `main()` 原来只检查目录为空，`LiveUiSession._save()` 直接写文件；桩会话自行 mkdir 隐藏了真实入口的缺口。

工作树已在 staging 和板端入口补建目录。最终应在构造会话及任何 RF 操作前确认新输出目录存在、为空且可写；失败给出明确原因。入口集成测试必须覆盖真实的“构造器不创建目录”行为。

### D3：没有帧时，诊断再次被覆盖（本次新增复现）

失败路径落在 `.json` 而非 `.iq`，结合 `LiveUiSession._save()` 的条件分支可知，当时 `client.frame is None`。这不能证明其真实原因是 BUSY、超时、协议拒绝或硬件故障；原始信息未被完整保留。

本次使用真实 `TrainvalSession` 继承链，跳过硬件构造器，注入纯内存 UI/worker/client，模拟 UI 的 BUSY 拒绝。结果：

1. 无输出目录：`FileNotFoundError`。
2. 创建目录后：`FRAME_RELEASE_MISMATCH: frame normal-01 belongs to release None`。
3. 已保存 JSON 的 `capture_status` 为 worker 的 idle，缺少 `ui_diagnostic`，没有保存模拟 UI 的 BUSY 原因。

这是一个可复现的诊断缺陷；模拟 BUSY **不是**真板首帧失败原因的判定。复现脚本：`evidence/rf-teaching/codex-trainval-diagnostic-2026-10-18/reproduce_masked_refusal.py`。

修复位置与顺序：

- `scripts/rf_bpsk_ui_board_session.py`：在调用动态 `_save()` 前保存第二次点击后的 UI 状态/拒绝码、worker 状态、frame/payload 是否存在、读回和原异常。现在 `result['ui_diagnostic']` 仅存在于局部返回值中，异常时丢失，应落到失败诊断产物。
- `scripts/rf_environment_trainval_board_session.py::TrainvalSession._save`：无帧或失败状态时先保留诊断并返回/抛出原采集原因，不进入成功帧的 release/sidecar/envelope 绑定校验。只有完整成功帧才执行这些有效性检查。
- `run_session()`：保留具名采集错误；清理失败放入独立字段，不覆盖最初错误。原始失败文件不应被后续保存重写。
- 回归至少覆盖本地拒绝、worker 失败/无帧，以及成功完整帧。使用真实保存/继承路径，避免只让 StubSession 返回成功。
- 不凭猜测增加 sleep、重试、降低门限或改变 46 dB 域。若本机不能确定真板无帧原因，下一次授权会话的 normal-01 作为有诊断的第一帧；失败立即收尾，保留真实原因。

### D4：回退脚本误报存储故障

旧脚本的 `(storage regex) AND (err|fail|...)` 把 `Pierre Ossman` 中的 `err` 计为错误，并将既有 FAT 未正常卸载警告计为本次故障。演练尚未进入实际切换。

复用项目已有 `KERNEL_FAULT_KEYS` 的精确故障词，大小写归一；对已知启动警告单独记录。保留真实 I/O/ADMA/EXT4 错误、只读挂载和读日志失败的阻断。不能把所有 FAT 告警一概视为无害，也不能把读取失败当作零错误。当前 `s2-46db-rollback-fix-2026-10-18` 正在构建 v2，尚不能用局部日志声称整套通过。

## 测试与流程判断

修复工作中的旧组合日志显示 2 failed / 101 passed，原因是两条新测试把真实候选与固定 stub release 混用。检查时源码已改为 `candidate['release_id']`；单文件日志分别为 22 passed、23 passed。旧失败属于已在修正的测试输入问题，不能通过删除身份校验解决，也不能把旧失败日志称作通过。

本次本机组合回归：`test_rf_environment_trainval_board_session.py`、`test_rf_environment_trainval_collect.py`、`test_rf_environment_trainval_board_revision.py`，49 passed，41.51 秒，退出码 0。该结果仅覆盖当时运行的代码与测试；上述 D3 诊断缺陷仍可单独复现。无需为脚本修复重新跑未变化的全部软件测试或原生性能战役。

另外发现正在使用的 `evidence/rf-teaching/s2/46db-collect-defect-fix-2026-10-18/work/mutate.py` 明确把变异写回共享仓库 `scripts/`，再从 pinned 副本覆盖恢复；检查期间这两个源码文件的差分也发生变化。这与当前项目规则“变异使用隔离副本”不一致，可能让并发测试、打包或采集读取变异版本。应先由该执行者结束并核对恢复状态，将后续变异移入临时副本，不能直接覆盖其他 agent 的新修改。最终审查和派发需对应稳定的修复字节；本次 49 项测试不能代替恢复后的最终相关验证。

审查按 `docs/requirements/development-review-policy.md`：上述新增关键行为合并一次增量审查，修复后只核对受影响项。不要求重新审 F1–F8、CRLF、整个仓库、旧 ARM 或既有部署。M11 是后续正式冻结阶段，不应使当前工具调试停在重复签发材料上。

仅修改上传到 staging 的采集/回退工具时，记录其实际摘要即可，不重装 e4dae554，不重跑未变候选的 ARM。若确实改变发布成员，则为实际新内容生成新候选并做相应验证。

## 下一次派发范围

给 DSH 的新指令可一次覆盖：离线修复、相关验证、一次增量审查、非发射路径演练、已批准的回退并返回 e4dae554，以及一个新的 TRAIN/VAL campaign。

- 板端只有一个执行者；回退/返回与采集串行执行。
- 新 campaign 最多 3 次会话初始化，train-01 / train-02 / val-01 各一次；每会话 10 normal + 1 off，总计最多 33 次计划采集。首帧诊断计入预算，不另加烟测帧；任一失败立即终止后续会话，不自动补采。
- 失败旧 campaign 保留。0 有效帧不等于没有初始化或没有物理派发，不重置其历史预算。
- 新目录/标识由工具生成；环境采集不套用 HEADROOM nonce。真实接线确认未变且仍在有效窗口内可引用；自动填当前时间不能代替真人确认。
- 完成 33 帧后运行 adapter → fit → load-check，保留 TRAIN/VAL 分离与原始 IQ。最终基线合格后才进入 C 候选、必要部署和独立 18 帧 pilot。
- 新指令的授权以用户实际发给 DSH 的内容为准，本报告本身不派发板端操作。
