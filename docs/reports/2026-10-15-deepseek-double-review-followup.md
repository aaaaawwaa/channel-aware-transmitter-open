# DeepSeek Harness 两轮复审接续核验

核验对象：DeepSeek Harness `final_prj` 主会话最近的两轮复审，以及当前提交 `d372197`。所附会话和复审文档只作为待核查证据；本轮仅对板卡做只读查询，未安装、未布防、未发射或采集。

## 已复现的结论

1. 第一轮 `REVIEW.md` 的两处阻塞（M6 变异未被测试抓住、板端正式入口没有传 N1–N12 计数）在后续修订中已有测试和接线；第二轮 `DELTA-REVIEW.md` 也独立复现 M6 两种变异均被抓住。
2. 第二轮指出的主机启动器缺少 `--offline-rejection`、freeze 声明缺少 `tool_hashes`，已由 `d372197` 加入。但该提交对主机启动器只做文本级接线测试，没有驱动真实 `run()` 的上传、远程命令、取回和主收据路径。
3. 本轮动态负控证实：旧启动器在板端 `result.json` 的 `offline_rejection` 与上传收据不同（或缺失）而 `formal_rf_passed=true` 时，仍能将主收据写成 `formal_physical=PASS`。新增逐字段相等门禁，失配记 `result_error`，`passed=false`；匹配路径保持可通过。测试先观察到所述失败，再验证修复。
4. `tool_hashes` 一致仍不足以开放正式路径。交付 freeze 声明 `expected_release=b82b141e…`；该候选的真实 manifest 合同为 `cnn-bpsk-headroom-v1`，而 `rf_environment_board_run.run()` 和 `rf_environment_freeze.check_candidate()` 要求 `cnn-bpsk-environment-v1`。对该候选真实运行强门得到 `ValueError`，且主机启动器在联网前即拒绝。现有其他 environment-v1 候选也无法通过本份 v3/40 dB 声明。没有可合法改写为“已具备正式采集资格”的候选。

## 板卡只读状态（2026-10-15 12:23 +08:00）

- 通过项目指定的 paramiko 通道读取：`current` 与 `active-release` 同为 `b82b141e355b4cb1833969383a409c79`；`pynq-status`、`rf-daemon` 均 `active/running`，本次启动 `NRestarts=0`；读取时 uptime 约 8162 秒。
- 已安装发布内的 `RfStatusClient.poll()` 返回 `online=true`、`stale=false`、`error=null`、`mode=status_only`、`daemon_state=OFFLINE`、`armed=false`、`pending=false`、`pl_status.io_ok=true`、`tx.source_selected=false`、`rx.armed=false`。`lease_held=true` 是状态查询所在连接期间的快照，不能推断该连接关闭后的租约状态。
- 此读回证明查询当时处于安全静止状态，不证明 b82 满足环境诊断正式候选合同；没有启动 Overlay、改服务、上传文件或进行物理帧采集。

## 本轮改动与边界

- `scripts/rf_environment_board_run.py`：主机主收据写入前，比对板端返回的四项离线负控计数与已上传的实测收据；缺失或不等即拒绝。
- `scripts/tests/test_rf_environment_formal_wiring.py`：新增动态启动器测试（不连板）覆盖不符、缺失、相符；新增真实 b82 候选在连接前拒绝的回归用例。
- freeze 声明更新 `FORMAL_TOOLS` 摘要；配套 receipt 明确区分 `validate_freeze` 弱门与 `check_candidate` 强门。合同和验收清单改为“离线部分满足，正式候选未合格”。历史离线负控收据没有重写。
- `rf_environment_verify_acceptance.py` 的同名 `offline_rejection='PASS'/'NOT_RUN'` 来自另一项只读重放检查，仍待单独改名；它不作为本轮 N1–N12 四计数的通过依据。

## 下一门

先建立与 v3/40 dB 冻结域、六项软件摘要完全一致的 `cnn-bpsk-environment-v1` 候选，并完成资格、发布与独立审查；随后用生产 `check_candidate` 对候选和新 freeze 声明做强门复验。物理 108 帧、真人验收均保持 `NOT_RUN`，不得以 b82 HEADROOM 安装或离线负控通过代替。

## 验证

- 新测试先在旧启动器上复现 `assert True is False`（失配计数仍被接纳）；修复后相符、不符、缺失三路均按预期。
- 受影响的四组测试：`59 passed`，exit 0。
- 完整 `scripts/tests`：`551 passed, 1 skipped, 1 warning`，exit 0；warning 是 `rf_build_daemon.py:179` 的既有转义序列提示。
- `py_compile` 对改动的启动器及测试文件 exit 0。板卡仅执行上述只读查询，没有 ARM 计算试验。
