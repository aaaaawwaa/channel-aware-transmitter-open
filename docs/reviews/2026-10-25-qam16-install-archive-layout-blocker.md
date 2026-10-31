# QAM 试部署 P1：归档布局与真实安装入口不兼容

日期：2026-10-25。当前 Codex 对话离线复核 DSH 第 96 轮原始日志；未连接板卡、未安装、未 RF。**CHANGES_REQUIRED：阻止原冻结包再次部署；只需修复受影响打包和预检链，不重做 QAM 算法全审。**

## 原始日志已经给出具体错误

来源：`evidence/rf-teaching/qam16-rect-product-2026-10-24/trial-execution-2026-10-25/recovery-2026-10-25/probe.json` 的 `.commands.unit.stdout`。完整原件 SHA256：`a19d9d52153f88d85b95613e6f7ceccb5388c9f0ec5dc0153573127e975c95d5`。

连续三条关键记录：

```text
INSTALL BEGIN: stopping existing PL owner and sidecar
sh: 0: Can't open /tmp/rf-teaching-install-rf-qam16-cd76818a/unpacked/pynq_overlay/sw/install.sh
INSTALL FAILED: restoring previous release and service units
```

DSH 的恢复报告称上一启动期“仅见解包、无终态”，漏提了原件中这两条明确失败记录。原安装入口失败已经证实；回退路径已进入，但完整回退终态尚未证明。重启后 current/active 为父 `bedf98c7…`，不能宣称此次 QAM 已安装。`rf-qam16-trial` 在新启动期 inactive、退出值为 0 也不能证明上一启动期成功。

提取件（带原件路径和 SHA）：`evidence/codex-qam16-runner-review-20261025/installer-failure-journal-extract.json`。

## 归档复核直接复现缺失

- QAM `candidate.tgz`（`181bf027…`）：72 个普通文件，全在 `release/` 下。没有 `pynq_overlay/sw/install.sh`、`pynq_overlay/sw/merged_release.py`、`sdk/src/sincos_q15.h`。
- 可工作的父安装归档（`f17b53d8…`）：130 个普通文件，含 `release/`、`pynq_overlay/`、`sdk/`、`rf_overlay/`，具备上述路径。
- `build_candidate.py` 末尾只遍历 RELEASE，全部以 `release/...` 归档，是缺失来源。
- `scripts/rf_teaching_install.sh` 在解包/验证之后停服务，再调用 `$src/pynq_overlay/sw/install.sh`。目前 preflight 只查 manifest、摘要和 fixture，没有查这条可执行入口及其相对依赖。

证据：`evidence/codex-qam16-runner-review-20261025/installer-layout-check.json`。这是当前真实归档的只读核对，不是构造的板端失败。

## 最小修复范围

1. 按既有父安装包布局补齐当前外层安装器所需源树及依赖，并从同一冻结候选字节映射生成，禁止混入工作树另一版本。仅把调用改成 `release/install.sh` 不够：该脚本也依赖 `$SRC/tests`、`../../sdk` 等相对路径。
2. 在任何 stop 服务或硬件操作之前验证归档的安装入口和实际相对依赖。旧 72 文件归档应在预检阶段被拒绝，不应先停服务再失败。
3. 增加真实布局的离线安装演练：解包实际归档、运行真实入口的安装路径，在隔离目标/既有沙箱替代 systemctl 等外部作用；检查最终文件摘要与路径。不要把整个安装器替换成恒成功 fake，或只再跑解调 acceptance。
4. 更新候选传输包、stage、unit、preflight 与收据中的新 SHA；旧原件和旧失败保留。若 release 内字节不变，release_id 可保持 cd76818a；外层归档 SHA 必然变化。以逐文件等同性连接既有 ARM/算法证据，不冒充旧 tgz 摘要。
5. 本次板卡卡死的直接硬件机制仍未定。已证明是安装入口失败后进入回退；下一步应定位回退的 stop/start/Overlay 阶段，而不能断言新 QAM 启动是死机触发点。补持久阶段标记，复用服务退出/AXI 静默排查；原硬件风险和安装包缺陷是两项不同问题。

## 本次审查纠正

前次 `2026-10-24-codex-qam16-trial-ready.md` 正确核对了 release 文件和 host/ARM 解调，但漏查了归档目录布局与真实外层安装入口的适配。其直接部署放行在新增证据后不再适用；已在原审查记录追加醒目纠正，不改写旧测试结果。修复后进行一次打包/预检/安装沙箱的增量审查即可，不重开全部历史审查，也不继续原样派发旧包。
