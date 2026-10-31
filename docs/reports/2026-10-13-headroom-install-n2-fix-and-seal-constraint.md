# N2 修补完成、但重新封存被“审查绑定”挡住（2026-10-13）

## 0. 结论

**选项 A 的“修补”部分已完成并红绿验证；但“重新封存”这一步无法只靠改一行来完成。**

按 A 执行时，`seal-stage.py` 在 `qualify-runtime.check_chain_reviews` 处以
`ValueError: stale Python candidate/harness review: critical-review` 失败。原因不是修补本身，
而是**封存自己的授权规则**：

```python
REVIEW_TOOLS = ('scripts/rf_headroom_deploy.py', 'scripts/rf_headroom_job.py', ...)
...
or binding.get('tools_sha256') != {name: sha(ROOT/name) for name in REVIEW_TOOLS}
```

`critical-review.json` 把 `scripts/rf_headroom_deploy.py`（N2 所在文件）的摘要当作
**“独立审查仍然有效”的前提**。改了它 = 审查过期 = **“No approval means no board contact”**。
这正是封存门的设计意图，也说明 A 的完整代价是**一次新的独立关键审查**，而不是一次摘要更新。

**当前状态：树已逐字节还原到封存修订，未派发。**
`scripts/rf_headroom_deploy.py` `d6d0678f…`、`test_rf_headroom_activation.py` `afba2e3d…`、
`source-freeze.json` `c1938bff…`、`stage-checkpoint.json` `cd22f70e…`，
`seal-stage.py --verify` **exit 0**，封存套件 17 passed。板卡只读状态未变，安装未派发。

## 1. 已完成、可复用的部分

| 项 | 结果 |
|---|---|
| N2 根因 | `readback_state()` 的守卫缺一个右括号 → 板端 `SyntaxError` → python3 退出非零（真 CPython 实测 **1**；**2** 为用法错误码，见 F1） |
| 板端实证 | 本轮预检上传检查器时**板卡真的返回了该 SyntaxError**（`logs/preflight-guard.log`、`preflight-run1-partial.json`） |
| 冻结套件为何漏掉 | 假板卡对任何含 `hashlib` 的命令直接返回 0（比真板宽容） |
| 修复（工具） | 补右括号 + 2 行注释（`n2-fix/rf_headroom_deploy.fixed.py`，**逐字节可复现**，sha `2f3094b5…`） |
| 修复（测试） | 忠实假板 `board_guard_result()`（解析并执行守卫、非程序→非零退出（模拟器记为 2；真 CPython 实测 1，见 F1）、断言失败→1）+ 新用例 `test_the_read_back_guard_is_a_runnable_program_and_discriminates`（含“把已发布的坏守卫判为 2”的自证断言）；修复后测试文件 sha `f9754a82…` |
| 红绿（回退守卫） | 把修补回退后：**7 failed / 11 passed**，失败含新用例与全部读回相关用例（`logs/red-activation-reverted-guard.log`）；修复后 **18 passed**（`logs/green-activation-fixed-guard.log`） |
| 回归 | 修复状态下 `scripts/tests` 全量 **489 passed / 1 skipped**（`logs/full-scripts-tests.log`） |
| 逐字节还原 | `revert-n2.py` 用封存摘要做硬门；三个文件均回到封存字节并复验 |

（修复期间的中间状态已全部还原；`pre-fix/` 保留了封存副本与预修补工具的重建件。）

## 2. 选项 A 的完整清单（重新评估后的真实代价）

1. **修复**：工具一行 + 测试两处 —— 已完成，产物在 `n2-fix/`。
2. **新的独立关键审查**（当前阻塞点）：重新出具
   `python/critical-review.md`（须以 `APPROVED` 开头）与 `python/critical-review.json`，
   其必填绑定包括
   `candidate_release_id`、`source_sha256`（46 项软件）、`harness_sha256`、`qualifier_sha256`、
   `runtime_runner_sha256`、`tools_sha256`（**新的 REVIEW_TOOLS 摘要**）、`report_sha256`、
   `native_lease_regression_passed=true`。
   **必须由未编写该修补的 agent 出具**（封存的原话是“非实现者”）。
3. `source-freeze.json` 两处摘要（已演练过一次，逐字节操作，46 软件/58 原生不动）。
4. 重新生成 `stage-checkpoint.json`；并可选择是否一并重新生成 `development-checkpoint.json`：
   - **轻路径**：只重生成 stage-checkpoint → 已封存 bundle **逐字节不变**（它绑定的是
     development-checkpoint 的 sha 与其高层字段，不看逐文件摘要）→ 需要明确披露
     “development-checkpoint 是预修补的历史记录”。
   - **全路径**：两个 checkpoint 都重生成 → 必须重新 `prepare` install bundle（新的
     `candidate.tgz` 与收据）→ 才可 `verify_bundle`。
5. `seal-stage.py --verify` 通过。
6. 重跑预检 → 单次派发 → 独立读回。

## 3. 备选 B（不改封存）

维持封存修订派发，明示接受安装器 receipt 为 `UNKNOWN_READBACK_INCOMPLETE`/exit 1
（**已知工具假阴性**，成因已证），验收结论由复审者自己的正确实现读回 + 已审查
`verified_state()` 给出；需要一次书面的“读回不全即停止”例外说明。

- 优点：不动授权链、bundle 不变、一条命令即可推进。
- 代价：安装记录里留一个假 `UNKNOWN`，且每个后续门都要重复解释这次例外。

## 4. 建议

**A′**：因为选 A 的意图就是“不接受坏掉的验收工具”，而 A′ 只多出“一次独立审查 + 一次封存”。
我可以：
1. 另起一个**未参与该修补**的独立 agent 承担关键审查（复验变更、重跑红绿与回退守卫、
   对未变更部分以封存既有证据复核，而不是重做原生 ARM 桥）；
2. 随后按轻路径或全路径完成封存、预检，并在你批准后单次派发 + 独立读回。

若项目更看重“不放宽任何门”，则 A′；若更看重尽快完成安装并接受一次有据可查的例外，则 B。
**在你选定之前不会派发，也不会改动封存文件。**

## 5. 当前事实

- 板卡：b67、双服务 active/running/NRestarts0、root rw、内核存储 15,097 B 完整读取且故障列表 `[]`、
  RF `safe=True` 且未布防/未选源/未武装（`preflight-receipt.json`）。
- 本轮新增物理帧 0、真实初始化 0；未上传 bundle、未派发、未写 `/home/xilinx`。
- 板端仅新增两个 `/tmp/rf-headroom-preflight-<uuid>/` 只读检查器目录（保留未删）。
- 已存在的独立第三方复验（`headroom-runner-third-party-2026-10-13/\`）不受影响：
  它复验的是 **runner**，与本次 `rf_headroom_deploy.py` 的 N2 无关。
