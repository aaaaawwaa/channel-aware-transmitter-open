# HEADROOM-1 合同准备 Implementation Plan

> **For agentic workers:** REQUIRED: Use $subagent-driven-development or $executing-plans. Steps use checkbox syntax for tracking.

**Goal:** 为接收余量调查生成完整离线声明，自动拒绝当前精确40dB候选上的46dB实测，交付下一研究合同的可审查输入。

**Architecture:** 独立准备器与纯离线完整性校验，不创建client/worker或网络factory；源、候选、baseline和固定研究设计绑定。旧生产模块、候选、G2驱动和历史证据保持原字节。

**Tech Stack:** Python3.8、pytest、numpy及现有release/场景生成器；WSL gcc只编译纯状态门禁。

Spec: `docs/superpowers/specs/2026-10-10-s2-headroom-research-design.md`。
共享目录有大量既有变更；本轮不提交/重置，使用新增文件保存独立版本和证据。

## Chunk 1: 离线准备器（worker拥有）

Files: 新增 `scripts/rf_headroom_prepare.py`、`scripts/tests/test_rf_headroom_prepare.py`。

- [ ] 先读TDD skill，写失败测试并保存red日志至 `evidence/rf-teaching/s2/headroom-preparation-2026-10-10/tests/`。
- [ ] 函数 `prepare(candidate, output, baseline)` 校验精确adfac release/1.0.3/1.5、`R.verify_release(require_software=True)`、`R.software_digests`、完整native build receipt的source_sha256与本地board_app输入（所有51个）；native binary SHA与provenance一致，baseline文件与候选语义相同。
- [ ] 新目录创建前失败；候选、baseline、build receipt及native/source输入拒绝符号链接/非regular。输出不能复用。
- [ ] 所有研究参数hardcode固定，无变参CLI：46dB/80M/60M/30M/2R2T/tap29/D1/4096/3M RECT/seed1/0wait/1init/5attempt，固定OFF→normal→narrowband→pulse→OFF。
- [ ] 用当前 `generate_scenario(ScenarioConfig(kind=...))` 生成三个TX文件及完整声明。写入固定study-declaration.json、compatibility.json、准备收据；输出 `prepared:true` 与 `execution_ready:false` 分离。永远 BLOCKED_BY_EXACT_40DB_CONTRACT，零RF/零初始化，formal/human NOT_RUN，s2_stage_passed false。
- [ ] `verify_preparation(output, candidate, baseline)` 从候选/源码重新生成固定预期，逐字节校验场景payload、完整声明、全部字段和SHA；拒绝删除、改写、伪造ready、额外/缺失场景、改参数和只保留成功子集。收据不自引用。CLI只有prepare和verify，不提供run；使用新目录原子写或 `.partial`+失败不冒充成功。
- [ ] 有意义反例：native修改（包括新增缺失manifest成员）、source/candidate不同、binary/provenance不同、baseline与候选不同、链接、输出复用、改声明/RF预算、删/改payload、伪造ready、`--run`拒绝、网络helper spy从未调用。不要仅镜像实现断言。
- [ ] 测试真实候选可以离线准备/verify；fake候选隔离R boundary的单测不能替代这项。保存green日志。运行命令 `python -B -m pytest scripts/tests/test_rf_headroom_prepare.py -q -p no:cacheprovider --basetemp evidence/rf-teaching/s2/headroom-preparation-2026-10-10/pytest`。

## Chunk 2: 独立证明、审查及当前状态（root拥有）

Files: 新增 `evidence/rf-teaching/s2/headroom-preparation-2026-10-10/` 下证明脚本/日志/receipt；新增 `docs/reports/2026-10-10-headroom-contract-preparation.md`。当前CLAUDE/roadmap变更前保存快照与原摘要，不能修改历史G2 checkpoint。

- [ ] spec-document-reviewer审查设计并修正。
- [ ] 根代理保存/校验旧G2 checkpoint所有绑定，mutable文档以新快照保存原字节，确认G1冻结native/软件无漂移。
- [ ] 编译现有 `rf_state.c` 与新增无硬件harness（只链接状态C文件），断言40dB批、46dB拒、cable缺失拒、拒绝清旧批准。保存gcc命令、输入SHA、可执行SHA、输出与exit。此项离线x86，不冒充ARM/实板。
- [ ] 使用旧G2 normal保存IQ及metadata重建frame/config/envelope，对当前baseline和真实46dB分析输入执行 `analyze_environment`；要求INVALID/RF_DOMAIN_MISMATCH，记录原IQ摘要、反事实范围、零新增物理帧。不重写原metadata/记录。
- [ ] 实现后新agent先规范合规审查，再代码质量审查，开放issue修复重审；root不能用自审替代。
- [ ] fresh tests与真实候选prepare/verify成功，篡改反例必须拒绝；保存输出完整摘要。
- [ ] 在已授权只读范围使用现有 `rf_teaching_install_state.py` + paramiko独立检查current/服务/未布防/TX/RX关闭；不初始化、不安装、不Overlay、不RF。检查脚本名称不能是inspect.py。
- [ ] 更新阶段报告和当前索引，G1 PASS / G2 STOPPED_CLIPPED / HEADROOM准备完成但RF合同阻断 / development累计67 / formal/human未测 / S3未实现。列出下一合同实现与候选资格，不承诺剩余工期。

## Chunk 3: 下一任务边界（此计划不实施）

- [ ] 依据spec实现独立HEADROOM研究动作和原子安全事务，旧40dB回归不变；daemon执行预算/顺序/失败停止，实际vendor双路衰减读回绑定同帧。
- [ ] 新候选G0/G1审查及ARM假硬件/整链回归合格后，才能安装并执行固定五次研究。禁止把本轮离线prepared当成执行许可/资格或承袭旧G1。

## 进度

2026-10-10：控制调查完成。现生产合同明确阻断46dB，转为合同准备交付；无新增RF。
