# 04 工程陷阱（真实事故提炼）

> 每条格式：**现象 → 根因 → 处置 → 防止再犯的检查**（+ 来源路径 + 事实/经验标记）。
> 凡是 CLAUDE.md / AGENTS.md / docs/reports 已写过的，本文件只给路径与一行摘要，不复制原文。

## 1. CRLF / `autocrlf` 让锚点 0 命中，改动静默不发生（事实）
- **现象**：就地编辑/变异脚本"以为改了"，编译 exit 0、无测试失败，读数看起来像"变异通过了"；或板端 Linux
  回归在换行符上失败。
- **根因**：源码/脚本是 CRLF，用 `perl -0pi -e 's/...\n/.../'` 之类的 `\n` 锚点**静默不匹配**；
  `install.sh` 含 CRLF 时 `/bin/sh` 会直接失败（即使哈希看起来一样）。
- **处置**：改用 Python `io.open(..., newline='')` 或显式 `\r\n`；`.gitattributes` 固定
  `*.sh`/`*.service` 为 `eol=lf`，并对 `evidence/**` 关闭换行归一化（`-text`，证据哈希指原始字节）。
  历史上曾把含 140 个 CRLF 的 `install.sh` 改为 LF 后重打包。
- **检查**：改动后**先 grep 确认命中**再运行；变异脚本先断言 `anchor_occurrences == 1`；
  写回用 `newline=''` 保留未触碰字节。
- 来源：[docs/reports/2026-10-07-g0-handoff.md](../reports/2026-10-07-g0-handoff.md)（坑小节）、
  [2026-10-03-production-board-continuation-audit.md](../reports/2026-10-03-production-board-continuation-audit.md) §1、[.gitattributes](../../.gitattributes)。

## 2. `.gitignore` 命中证据文件 → 必须 `git add -f`（事实）
- **现象**：`git status --porcelain` 看不到 junit/日志/位流的变化，"本地可封存、克隆后不可封存"。
- **根因**：根 `.gitignore` 忽略 `*.log`；`evidence/.gitignore` 忽略 `*.bit *.hwh *.iq *.tgz *.gz *.so *.elf *.npy *.xml 
  rf_daemon *.exe *.board`。大二进制与日志按计划留在本机、由清单摘要核验。
- **处置**：需要入库的证据字节用 `git add -f`（既有先例）；`combined-tests.xml`、`stage-checkpoint.json` 同类，
  所以**不能**用 `git status` 判断它们没变 —— 要复算摘要。
- **检查**：`git status --porcelain` 为空 ≠ 无变化；对关键证据先 `sha256` 再下结论。
- 来源：[.gitignore](../../.gitignore)、[evidence/.gitignore](../../evidence/.gitignore)、
  [s2-headroom-g4prep-2026-10-17/NOTES.md](../../evidence/rf-teaching/s2-headroom-g4prep-2026-10-17/NOTES.md) §5.2/§5.3。

## 3. pytest `--basetemp` 必须在仓库内（事实）
- **现象**：默认系统临时目录在本机引发权限错误，看起来像测试失败。
- **根因**：本机环境对系统 temp 的权限受限（历史已知问题）。
- **处置**：一律 `python -B -m pytest -p no:cacheprovider --basetemp <final_prj 内独立目录> -q <范围>`；
  临时目录删除前用 `Resolve-Path` 校验前缀确实在仓库内。
- **检查**：任何 pytest 命令都要显式带 `--basetemp`；不在仓内的临时目录不得作为证据。
- 来源：[docs/reports/2026-10-13-final-prj-baseline.md](../reports/2026-10-13-final-prj-baseline.md) §方法、
  同目录收据 `evidence/rf-teaching/p0-baseline-2026-10-13/lead/raw-summary.json` 的 `pytest_policy`。

## 4. `python -B` / `__pycache__` 污染"只读/封存"目录（事实）
- **现象**：成员校验报 **`nonregular candidate member`**，候选/封存目录里多出非预期成员。
- **根因**：不带 `-B` 时 CPython 会把 `__pycache__` 写进"不可变"的发布目录（板上实测：某 release 启动 7 分钟后
  冒出一个 `__pycache__`），只读权限位挡不住它；打包/校验器只接受常规文件成员。
- **处置**：板端任意 Python 调用带 `-B` 或 `PYTHONDONTWRITEBYTECODE=1`；打包器显式跳过 `__pycache__` 成员，
  校验器对非常规成员**具名拒绝**。
- **检查**：封存/候选目录出现新文件先查解释器字节码；测试断言"产品源码路径不得可导入"而非前缀猜测。
- 来源：`scripts/rf_environment_deploy.py`（`nonregular candidate member`）、`scripts/rf_headroom_deploy.py`、
  `scripts/rf_headroom_research_run.py`（跳过 `__pycache__`）、`pynq_overlay/sw/pynq-status.service` 注释、
  `pynq_overlay/sw/tests/test_merged_install_contract.py`。

## 5. `mkdir(exist_ok=False)` 要求先归档旧目录（事实）
- **现象**：脚本第一次跑出收据后，第二次直接抛 `FileExistsError`，或被误当成"已存在就是已完成"。
- **根因**：产出目录用 `mkdir(..., exist_ok=False)` 表达"本次必须全新"，而目录里有上一轮（甚至上一代发布）的收据。
- **处置**：执行前按既有做法**归档旧目录**（`archived/<name>-<UTC时间戳>/`）并记录，**不得覆盖**；
  被归档的不是删除，是可追溯的旁路。
- **检查**：所有写目录前先 `Test-Path`；产出目录必须"尚不存在"，否则先归档。
- 来源：`pynq_overlay/sw/rf_headroom.py:906`、`rf_experiment_record.py:90`、`scripts/rf_environment_deploy.py`、
  `scripts/rf_teaching_deploy.py`；先例 [s2-headroom-g3b NOTES §6](../../evidence/rf-teaching/s2-headroom-g3b-2026-10-17/NOTES.md)、
  [s2-headroom-safetyrerun-2026-10-17/archived/](../../evidence/rf-teaching/s2-headroom-safetyrerun-2026-10-17/)。

## 6. 板卡无 RTC（板载墙钟 ≈ 2021）→ 必须用主机 UTC 锚（事实）
- **现象**：46 dB 第一次派发在 G0 被拒 `RUN_NOT_STARTED_PREFLIGHT / the authorization is not valid yet`，
  nonce 未消耗、零帧 —— 看起来像授权工件写错。
- **根因**：板载 RTC 未设，`date -u` 显示 2021-12-03；运行器用**板端自身时钟**判授权生效窗口与接线新鲜度，
  于是"授权还没生效"。
- **处置**：改用**主机 UTC + 短龄锚**：记录主机 UTC 与 `boot_id`/`/proc/uptime` 锚，`board_clock_used=false`；
  容差写死，锚龄超限即拒。**禁止**把授权或收据的时间写成 2021（那不是证据时间）。
- **检查**：任何板端时间戳先问"这是板钟还是主机钟"；证据文件里出现 2021/2022 一律视为板钟不可用；
  主机锚的可信来源与单调性属**残余风险**，需在新会话 G0 保留审查。
- 来源：[s2-headroom-research-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-headroom-research-2026-10-15/NOTES.md)、
  [s2-clock-anchor-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-clock-anchor-2026-10-15/NOTES.md)、
  [board-run-package-design §RTC](../superpowers/specs/2026-10-13-headroom-board-run-package-design.md)、
  [2026-10-16 报告](../reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md) §1。

## 7. `boot_id` 跨会话变化 = 板卡重启过（事实）
- **现象**：拿历史读回当"当前状态"，或把上一次启动的状态说成现在。
- **根因**：`boot_id` 变了就说明不是一个启动期；跨启动的文件（哪怕自身 `verified_state` 为真）不能当本次读回。
- **处置**：读回前后各读一次 `/proc/sys/kernel/random/boot_id`，两者必须相等才承认"读回发生在本次启动内"；
  `boot_id` 变化是具名结局 `RUN_UNKNOWN_REBOOT`，保留原始证据、不重试。
- **检查**：任何"当前状态"论断必须带 `boot_id` + `uptime`；跨 `boot_id` 的证据只能作历史。
- 来源：[board-run-package-design §六类结局](../superpowers/specs/2026-10-13-headroom-board-run-package-design.md)、
  [s2-headroom-g3b NOTES §3 ⑥b/⑥d](../../evidence/rf-teaching/s2-headroom-g3b-2026-10-17/NOTES.md)、
  `scripts/rf_headroom_deploy.py`（`final-safety` 的新鲜性读回）。

## 8. token 只记路径，不读值、不入档（事实 + 经验）
- **现象**：授权工件里出现"token 值"或"token 摘要"。
- **根因**：便利性诱惑 —— 把 token 抄进 JSON 最省事。
- **处置**：`token_ref` 是**路径引用**（如 `/etc/rf-control.token`），不得包含值；token 值与其摘要都不得写进
  任何证据文件；唯一通路 `read_control_token(path)` → `client.headroom_arm(token)`。
- **检查**：收据 diff 里 grep 不到 token 值/摘要；模板文件明确"不含真实 nonce、不含 token 值"。
- 来源：[2026-10-14 执行包 §3.3/§7](../superpowers/plans/2026-10-14-headroom-physical-session-execution-package.md)、
  [runner 设计 §token 处理纪律](../superpowers/plans/2026-10-12-real-headroom-research-runner-design.md)、
  [headroom-authorization-template README](../../evidence/rf-teaching/headroom-authorization-template-2026-10-14/README.md)。

## 9. 帧内 4 键层级 + 夹具必须与真实帧同形，否则出现 fail-open 盲区（事实）
- **现象**：假硬件研究链在 attempt 1 就 `EVIDENCE:1` 停止，理由 `the frame carries native_error outside headroom`。
- **根因**：daemon 用第二次 `snprintf` 从 `d->frame+n-2`（正好落在模板的两个闭合花括号 `]}}` 上）写入四个
  追加证据键，所以它们必须在 **`headroom` 内层**（真实帧：外层恰 10 键、`headroom` 19 键）。夹具把四键写在
  **外层**（14 键 / 15 键）——"夹具与真实帧不同形"。若新谓词只宽容地"两处都读"，就会留下 fail-open 盲区：
  夹具能过、真实帧的畸形变体能过。
- **处置**：谓词从 `headroom` **内层**读并**具名拒绝外层同名键**；夹具按真板形态 re-home；新增
  `test_rf_headroom_chain_frame_shape.py`（21 例）钉住形状与冻结摘要。
- **检查**：任何"模拟真机"的夹具都必须先与**一份真实帧逐键比对**（键集/键数/嵌套位置），并把该形状写进测试；
  谓词对未知/错位形态一律 fail-closed 并给具名理由。
- 来源：[s2-headroom-g1-2026-10-16/NOTES.md](../../evidence/rf-teaching/s2-headroom-g1-2026-10-16/NOTES.md) §1、
  `rf_overlay/sw/board_app/rf_daemon.c`（写入点）、`pynq_overlay/sw/rf_headroom.py`（读取点）、
  [2026-10-16 报告](../reports/2026-10-16-dsh-offline-session-and-headroom-next-step.md) §2。

## 10. 加新守卫后必须重跑旧变异；`pytest.raises(match=...)` 必须匹配它名字所指的那条消息（事实）
- **现象**：把某段检查整段删掉，49 条测试**仍然全绿** —— 变异验证悄悄失效，真证据报废。
- **根因**：`wait_campaign` 的"必须给 baseline"守卫是在拿到八帧上限的变异验证**之后**才加的，而它排在档位检查**之前**：
  `pytest.raises` 只要求"抛异常"，更早的守卫先抛，于是它满足了本该由档位检查满足的断言。
- **处置**：修复并把教训写进交接文档。
- **检查**：（a）任何新增/移动守卫之后，**重跑全部旧变异**；（b）每个 `pytest.raises` 用 `match=` 匹配
  **它名字所指的那条特定消息**；（c）复审用"删掉检查看是否还绿"这一招。
- 来源：[docs/reports/2026-10-07-g0-handoff.md](../reports/2026-10-07-g0-handoff.md)（坑小节第一条）、
  [2026-10-07-s2-settle-validation-next.md](../superpowers/plans/2026-10-07-s2-settle-validation-next.md) §新增教训。

## 11. 变异必须先断言"已生效"再编译（事实）
- **现象**：变异后编译 exit 0、无 FAIL、读数正常，看起来像"变异通过了"。
- **根因**：替换模式没匹配上，源码根本没改（见第 1 条）。
- **处置/检查**：编译或跑测试**之前** grep 确认改动落地；锚点唯一性断言；还原后复算 SHA。
- 来源：[2026-10-07-g0-handoff.md](../reports/2026-10-07-g0-handoff.md)。

## 12. 大型 pytest 临时树不得入库（事实）
- **现象**：仓库里出现 5,713 个文件 / 167 MB 的未跟踪 `pytest-tmp-baseline/`。
- **根因**：`--basetemp` 指向证据目录内的临时树，而 `.gitignore` 不覆盖 `pytest-tmp*` / `.pytest-work-*`。
- **处置**：该轮产物**原样保留、未删**（删除属破坏性且会抹掉唯一运行痕迹），并在 NOTES 里如实标注为噪声；
  规则是"临时树不入库"。
- **检查**：`--basetemp` 用 `.pytest-work-*` 这类**可安全删除**的目录名（基线报告：确认路径在仓库内后可安全删除）；
  交付/封存目录内不得使用临时树；`git status` 里出现巨型未跟踪目录时先判断来源再决定。
- 来源：[s2-headroom-g3b-2026-10-17/NOTES.md §1.3](../../evidence/rf-teaching/s2-headroom-g3b-2026-10-17/NOTES.md)、
  [2026-10-13-final-prj-baseline.md](../reports/2026-10-13-final-prj-baseline.md) §讨论。

## 13. 正则缺词界 → dmesg 假阳性（事实）
- **现象**：存储故障分类器报出 1 条故障，人工一看是驱动注册信息。
- **根因**：模式 `sdhci.*err` 命中了 `sdhci: Copyright(c) Pierre Ossman` 里的 "Pi**err**e"。
- **处置**：改为词界锚定 `sdhci.*\bErr`，用**同一模块**重扫已保存的完整日志 → 0 故障，两次扫描可比。
- **检查**：故障分类器必须词界锚定 + 全量关键词扫描**不隐藏任何行** + 假阳性如实记录并修正；
  两次结论比较前确认用的是同一分类器版本。
- 来源：[s2-headroom-g2-2026-10-16/NOTES.md](../../evidence/rf-teaching/s2-headroom-g2-2026-10-16/NOTES.md) §P0 dmesg 扫描。

## 14. 把命令的 stderr 当成 stdout 解析 → 一批假失败（事实）
- **现象**：候选检查脚本报 4 个失败，实际 4 个二进制都在且摘要相等。
- **根因**：脚本把 `git ls-files` 的 **stderr** 当作"被跟踪路径列表"来读；`--error-unmatch` 失败时的报错被当成路径。
- **处置**：先修脚本、再重跑（30/30 PASS）；保留的是**修正后**的日志。
- **检查**：解析外部命令输出时显式取 stdout 与退出码，两者分别判定；`subprocess` 用 `capture_output` 后
  断言 `returncode`；发现"失败"先复现一次再看是不是解析问题。
- 来源：[s2-environment-live-install-2026-10-15/NOTES.md](../../evidence/rf-teaching/s2-environment-live-install-2026-10-15/NOTES.md) §C1。

## 15. `make host-test` 在 cmd 下必失败（历史，已解决）（事实）
- **现象**：`make host-test` 在 cmd 下退出非 0，错误看似来自 `mkdir -p`（产物名 `rf_overlay/sw/board_app/-p`）。
- **根因**：cmd 下 `make` 用的是 Windows 自带/其它 `sh`，不支持 POSIX 语义。
- **处置**：把 Git bash 的 `sh.exe` 放进 `PATH`，同一命令 exit 0。
- **检查**：主机侧 C 测试统一在 Git bash 的 `sh.exe` 可见的 PATH 下跑；把"环境问题"与"产品失败"分开记。
- 来源：[CLAUDE.md](../../CLAUDE.md) 2026-10-14 段（最终回归行）。

## 16. 已经写在别处的经典坑（只给指针，不重复）（事实）
- 改了 `src/rtl/*.v` 后直接 synth/下载会拿到旧 bitstream → 必须走 `scripts/build_bitstream.py`：
  [AGENTS.md](../../AGENTS.md)。
- BD 内嵌 `blk_mem_gen` 的 `Enable_32bit_Address` 必须为 true，否则**静默**综合成空网表：同上。
- 守护进程持着 PL 时绝不能再调 `Overlay()`（已两次卡死）：同上。
- 连板用 `pynq_ssh.py`（paramiko），别用 `ssh`/`scp`（Windows 会弹图形化凭据框卡死命令）：同上。
- 分解释器跑 Python 套件（`torch` 只在 `.venv-rf-ml`；缺 torch 不是测试失败）：
  [2026-10-07-g0-handoff.md](../reports/2026-10-07-g0-handoff.md) 操作要点。
- `test_vendor_integrity.py` 是**脚本**不是 pytest 模块（用 pytest 跑得 `no tests ran`）：同上。

> 新增陷阱时请补在 1–15 的编号序列里（保持"现象→根因→处置→检查"四段式），并在末尾附来源路径。
