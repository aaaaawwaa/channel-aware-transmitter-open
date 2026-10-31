# HEADROOM runner 审核门接续报告（2026-10-13）

## 结论与边界

候选 `b82b141e355b4cb1833969383a409c79` 的隔离 ARM G1 已在先前封存阶段通过；
本轮完成的是新 host-side runner 的离线缺陷修补与证据重跑。**runner 的独立增量审查尚未签核**：
审查者验证了 F1/F2 原反例并发现后述两个新问题，但最终复核因其运行额度中断。因此当前审核门
是 `LOCAL_VERIFIED / INDEPENDENT_SIGNOFF_PENDING`，不能称为独立 `APPROVED`。

板卡此前只读检查在线，发布仍为 b67，`current` 与 `active-release` 一致；两项服务 active、
running、NRestarts=0；TX/RX 未布防，当前启动内未见新的 MMC/SDHCI/ADMA/I/O/ext4 错误。
板载时钟显示 2021-12-02，不能用它给证据排序。runner 证据本轮 `board_contact=false`、
`board_transport_wired=false`。真实安装、Overlay、布防、发射、采集和 46 dB 研究均未执行。

## 本轮补齐

1. `functools.partial(release_worker_factory, ...)` 不继承函数属性，原 G4 记录会误报
   `board_read=false`。runner 现在逐层解包 `partial` 判断工厂声明；补了真实 worker 轮询
   的端到端回归。
2. 账本追加并同步失败时，nonce 可能已经写入，但原拒绝文案声称“nothing was consumed”。
   现在结果标为 `consumption_status=UNKNOWN`，明确 nonce 不可重用且未读板；回归模拟
   追加之后抛出 `OSError`。
3. G1/G3/G4 的失败读取路径原记录 `board_read=false`。现在调用 G1/G3 探针前先记
   读取尝试；G4 依工厂声明在调用前标记。`_refuse` 与 `refusal_report` 均使用该状态。
   定向测试先实测 11 个失败、1 个通过，修补后 12 个通过；未触及板卡。

## 新鲜验证与绑定

在 `<LOCAL_PATH>` 运行：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
& '<LOCAL_PATH>' evidence/rf-teaching/s2/headroom-runner-2026-10-12/evidence-run.py
& '<LOCAL_PATH>' evidence/rf-teaching/s2/executable-candidate-2026-10-12/seal-stage.py --verify
```

`evidence-run.py` exit 0：新套件 **137 passed**；缺席实现红态 **106 failed / 31 passed**；
四个冻结套件 **79+34+26+16=155 passed**；变异 **34 total / 32 caught /
F02、F11 两项已知 escaped / unexpected 0**；`receipt.json` `passed=true`。
`seal-stage.py --verify` exit 0，明确 installation/RF `NOT_RUN`。

| 绑定文件 | SHA-256 |
|---|---|
| `scripts/rf_headroom_research_run.py` | `ba72f10e1c96966cdb37d3d7169721d0707a29008ee990559e3e019761d3c335` |
| `scripts/tests/test_rf_headroom_research_run.py` | `be7daa57c871ccdaf6425c9a98132c22e8d2d8d10507372b92e6c416db8cde65` |
| `evidence/.../headroom-runner-2026-10-12/mutation-harness.py` | `5b1b38c083e9bb285461e01df7f8bd5bb4fd83a7273304a2777f6ac342a105c7` |

收据：`evidence/rf-teaching/s2/headroom-runner-2026-10-12/receipt.json`；
原独立审查：同目录 `review/critical-review.md`；F1/F2 原反例：
`review/increment-f1-overcorrection.py` 与 `review/increment-counterexample-f2.py`。
旧 `review/increment-f3-partial-probe.py` 访问不存在的 `SimClient.connections`，
不能将其运行错误当成 runner 通过或失败；F3 以新套件的端到端断言复核。
46 项发布软件、22 项绑定文件、58 项原生输入与候选一致，未更动冻结生产软件。

## 分配给 DeepSeek Harness 的下一项任务

**任务：对上述 runner 摘要做独立增量复审，并形成一个明确的审核门结论。**

1. 先核对三个摘要及 `receipt.json` 的产物绑定，再独立运行 F1/F2 原反例、账本
   `fsync` 后失败回归、G1/G3/G4 拒绝路径的 `board_read` 回归，检查正常成功路径与
   缺依赖路径没有被误改。
2. 审视 34 个变异结果，特别说明 F02/F11 已知逃逸是否仍可作为本审核门的声明限制；
   不得把 “escaped” 改称 “NOT RUN”。
3. 若找到新的阻塞问题，先给可复现反例和严重度，再做最小修补、红绿测试并重跑收据；
   新摘要必须重新写入复审报告。若无阻塞问题，输出 `APPROVED` 并列明范围、依据与
   遗留风险；若证据不足，输出 `CHANGES_REQUIRED`，不可默认为通过。
4. 交付 `docs/reports/2026-10-13-headroom-runner-independent-review.md`，包含签核结论、
   执行命令/退出码、反例结果、摘要、板卡接触事实和下一审核门。保持原 `review/**` 历史
   证据不变。

此任务仅限离线与只读检查。真实安装或 RF 研究需另按项目门序与人工接线、一次性授权执行；
不能把先前隔离 ARM G1 当作实际安装/采集。

## 分配尝试状态

2026-10-13 11:22（Asia/Shanghai）尝试通过 `dsh headless` 提交上述完整任务；
DSH 返回 `QUOTA: Insufficient Balance`，exit 1，request id
`92d2cd2f-3f49-43a6-9e69-157943574f98`。因此**任务未执行、没有独立签核**。
该交接报告可在 DSH 额度恢复后直接作为下一次输入；不要把本次配额失败当成审查结论。
