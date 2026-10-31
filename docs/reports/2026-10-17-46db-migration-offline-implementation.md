# 46 dB 环境域迁移：离线实现与验证汇总（2026-10-17）

范围：**纯离线**（未连板、未部署、未 ARM、未初始化 RF、未发射、未采集、未消耗授权/nonce）。提交 `410fd8f` → `46f302c` → `821b909` → `8c87187` → `28062a0`（工作树干净）。

## 1. 实际修改

| 面 | 内容 |
|---|---|
| 执行顺序修正 | 原方案 M10/M11/M12 自相矛盾（要求板上是 C 且完成 pilot，却把安装 C 放在 M12）→ 改为：候选B→ARM/安装/读回→TRAIN/VAL→最终基线→候选C→ARM/安装C/读回→C 上 18 帧 pilot→freeze→再申请 TEST |
| 域迁移（B-2） | `rf_teaching_profile.ATTENUATION_MDB=46000` **域推导链唯一真源**；`rf_environment.VERSION=rf-window-environment-v4`；`FIXED_RF`/客户端门/`ui_model`/board-session/`rf_headroom.TEACHING_ATTENUATION_MDB`/`rf_environment_acceptance` 全部派生；不支持的 40000/45999/46100 在 5 个入口**具名拒绝** |
| 父证明入口（B-1） | `verify_installed_parent_via_activation_evidence`（两套 deploy 可用，CLI `--parent-activation`）；核验发布身份/46 项清单（取自 `R.verify_release`）/关键摘要/启用 phase 链与唯一 `ACTIVATION_ACCEPTED`/post-state/独立读回/产物字节绑定；**不生成不改名 `INSTALLED_VERIFIED`**，旧三路线未删未放宽 |
| 标定与基线 | 新增 `scripts/rf_environment_calibration.py`（plan/fixture/fit/provisional/load-check + `--require-formal`）；provisional n=1 基线（canonical `6b697e0e…`）；M2 把产品基线由历史 40 dB/v3（`721baa76…`，被拒）换成 provisional 46 dB body，旧字节**逐字节归档** |
| 候选 B（M3/M2 重建） | **`e4dae554182e8c9f5f963c1d5040913a`**（父 `c3bf915c`、合同 `cnn-bpsk-environment-v1`、44 项、51 文件/7,046,406 B）；PL/HWH/daemon/模型**零重建复用**；`verify_release(require_software=True)`+panel+合同读回 PASS、`tree_match=true` |
| 复审修复 | F1 反空转计数 + 恢复复现性用例；F2 恢复 3 条真实回归（受守卫 skip）+ 新增 verify 层 split-leak；F3 日志 `.txt` 入库 + 568 规范清单；F4 pre-state 交叉断言；F5 摘要分族隔离用例；F6 `--require-formal`；F7 措辞限定 + AST 普查；F8 证据 LF 归一（canonical 不变） |

## 2. 验证结果

- **测试**：`pynq_overlay/sw/tests` **2186 passed / 7 skipped / 0 failed**；`scripts/tests` **869 passed / 1 skipped / 0 failed**；过滤集 **356 passed / 514 deselected**；17 文件 569 passed/3 skipped；
- **拒绝语义**：38/38 具名（0 静默）；父证明 **7 类负例全抓**（含 2 例自洽伪造）；标定 **39 条具名负例**；N1–N12 **12/12 passed / 0 cleared**（3 变异体逐个被抓）；
- **离线前置翻转**：候选自载加载器 `REFUSED(3)→LOADED(0)`；ARM 入口主机前置 `exit 1→0`（`baseline_sha256=6b697e0e…`）；配对对照（旧候选 B）仍 3/1；迁移前正对照 `399b300e` 仍 0；
- **独立审查两轮**：合并增量复审 CHANGES_REQUIRED（核心行为成立、指定 5 类变异全抓）→ F1–F8 修复 → **delta 复核确认 F1–F7**，F8 按窄口径闭合（提交范围 `git diff --check HEAD~1 HEAD` 实测 **rc=2 / 40,716 / 72 文件**，全量二分类 45 字节绑定 + 27 自由记录，**不声称 rc=0**）；
- **范围**：`git show --numstat` 无 C/RTL/PL 改动；断言差分为 + 或 0（无以删断言修绿）。

## 3. 剩余阻塞

1. **post-accept 回退命令缺失**：`install.sh:12,14` 要求 `current==parent` 且目标发布目录不存在 ⇒ 试部署若在验收**之后**失败，无已复审回退路径（需新复审补齐）；
2. **两个工具缺口**：11 帧会话形状不存在；下载物→`set.json` 适配器不存在（TRAIN/VAL 前置，需新代码 + 复审）；
3. **M11 重签未做**：freeze 声明 `tool_hashes` 仍为历史 v3/40 dB（已被具名钉住）、N1–N12 正式重签、`FORMAL_TOOLS` 纳入新入口；
4. **F8 例外（已记录）**：产品基线文件保持 CRLF（候选 manifest 绑定）、`offline_negatives.main` 写入器保持 CRLF（M2 收据被钉）；
5. **未证明**：真实 prepare→install→readback 端到端、原生 C 的 46000 判定、46 dB 标签正确性、任何 dB 余量、provisional n=1 的可用性边界。

## 4. 下一板端工作包

| 段 | 命令（要点） | 预算 | 判据 / 失败处置 |
|---|---|---|---|
| ARM | `rf_environment_run_arm_check.py --candidate <e4dae554…> --pilot evidence/rf-teaching/s1/board-pilot-2026-10-04-v1 --output …/arm-run` | ≈40 s（锚点 35.6 s，工具内 120 s 超时） | `passed/exit0/parity[]/native_cnn/iterations=100/p95≤300/baseline_sha` 相符；失败即停，不改 runner（`runner_sha256` 绑死） |
| 试部署 | `rf_environment_deploy.py prepare --closure … --arm … --parent-candidate <e66197ed 目录> --parent-activation evidence/rf-teaching/s2-headroom-activate-2026-10-17`（≈1–3 s）+ `verify_bundle` + 板端 `systemd-run … trial-install.sh <stage> <e4dae554> <e66197ed> <tgz sha> 120 python3 -B acceptance.py …` | ≈150 s | 不发射冒烟验收：发布身份/44 模块/域 46000/基线 canonical/一帧归档 IQ 的 **demod 与 environment 分键记录**；失败走事务内 `TRIAL_ROLLED_BACK`；`TRIAL_RECOVERY_REQUIRED` ⇒ 停止写入并报告 |
| TRAIN/VAL | `rf_environment_calibration.py plan → 6 会话 → fit --set → load-check` | 单会话 ≈30–60 s；33 帧 RF ≈2.3 ms；**0 份 single_use 授权** | TRAIN 2×11 + VAL 1×11 + pilot 18 = 6 会话/51 帧（**仍未采集**）；证据落 `evidence/rf-teaching/s2/46db-{train,val,pilot}-<date>/` |

**需要用户决定的只有板时**：何时跑真 ARM / 试部署 / TRAIN-VAL（其余实现选择已自行决定）。

## 5. 证据索引

- 域迁移：`evidence/rf-teaching/s2-46db-domain-migration-2026-10-17/`
- 标定与 provisional 基线：`evidence/rf-teaching/s2-46db-trainval-baseline-2026-10-17/`
- 候选 B：`evidence/rf-teaching/s2-46db-candidate-b-2026-10-17/`
- M2 引导与重建：`evidence/rf-teaching/s2-46db-m2-bootstrap-2026-10-17/`
- 合并复审：`evidence/rf-teaching/s2-46db-independent-review-2026-10-17/`
- 复审修复与 delta：`evidence/rf-teaching/s2-46db-review-fixes-2026-10-17/`、`evidence/rf-teaching/s2-46db-review-fixes-delta-2026-10-17/`
- 迁移方案（含顺序更正与实现进展）：`docs/superpowers/specs/2026-10-17-headroom-46db-environment-migration.md`
